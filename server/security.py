"""Passwords (Argon2id), session tokens, and sign-in throttling."""
import contextlib, hashlib, heapq, hmac, secrets, threading, time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from . import config

_ph = PasswordHasher()   # Argon2id with the library's current recommended parameters (RFC 9106 low-memory profile)
# Each hash takes 64 MiB for a moment. A burst of sign-ins (all passing the throttle before any failure is counted)
# would otherwise run one per worker thread at once and could exhaust a small server's memory, so only a few run
# at once. The rest wait briefly, then give up with Busy (the API answers 503 "try again"): waiting longer would
# hold the worker threads every other request needs.
_slots = threading.BoundedSemaphore(max(1, config.HASH_PARALLEL))
_DUMMY = _ph.hash(secrets.token_urlsafe(24))   # verified against when the username doesn't exist, so timing doesn't tell


class Busy(Exception):
    """Too many password checks are running; try again in a moment."""


@contextlib.contextmanager
def _slot():
    if not _slots.acquire(timeout=config.HASH_WAIT):
        raise Busy()
    try:
        yield
    finally:
        _slots.release()


def hash_password(pw):
    with _slot():
        return _ph.hash(pw)


def verify_password(stored, pw):
    """True when pw matches. A missing account still costs one hash, so response time doesn't reveal usernames."""
    with _slot():
        try:
            return _ph.verify(stored or _DUMMY, pw) and stored is not None
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False


def needs_rehash(stored):
    try:
        return _ph.check_needs_rehash(stored)
    except InvalidHashError:
        return False


def password_problem(pw, username=""):
    """Why a new password is refused, or None. Length only, per NIST SP 800-63B: no composition rules."""
    if not isinstance(pw, str) or len(pw) < config.PASSWORD_MIN:
        return f"Use at least {config.PASSWORD_MIN} characters. A few unrelated words make a strong, memorable password."
    if len(pw) > config.PASSWORD_MAX:
        return f"Use at most {config.PASSWORD_MAX} characters."
    if username and pw.strip().lower() == username.strip().lower():
        return "The password can't be the same as the username."
    if len(set(pw)) < 4:
        return "That password is too repetitive."
    return None


def username_problem(name):
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 64:
        return "Usernames are 1 to 64 characters."
    if any(c.isspace() or c in ":/\\<>\"'" for c in name.strip()):
        return "Usernames can't contain spaces or : / \\ < > \" '."
    return None


def new_token():
    return secrets.token_urlsafe(32)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def same(a, b):
    return hmac.compare_digest(a.encode(), b.encode())


class Throttle:
    """Failed sign-ins, in memory (a restart clears them). Keys are "kind:value"; each kind gets its own free tries:

      pair:<user>|<address>  5    one person guessing at one account
      ip:<address>          20    one address guessing at many accounts (a household shares one, so more slack)
      user:<user>           30    many addresses guessing at one account; high enough that a stranger can't
                                  cheaply lock someone out, while still slowing a distributed guesser

    After a key's free failures inside WINDOW seconds, each further try waits longer (doubling, capped at
    MAX_WAIT). NIST SP 800-63B asks for at most 100 consecutive failures before an account is locked; this stays
    below. At most MAX_KEYS keys are kept, so a flood of made-up usernames can't exhaust memory.
    """
    FREE = {"pair": 5, "ip": 20, "user": 30}
    WINDOW, MAX_WAIT, MAX_KEYS, MAX_TIMES = 15 * 60, 15 * 60, 10000, 64

    def __init__(self):
        self._fails, self._lock = {}, threading.Lock()

    def _key_wait(self, key, now):
        times = [t for t in self._fails.get(key, ()) if now - t < self.WINDOW]
        if times:
            self._fails[key] = times
        else:
            self._fails.pop(key, None)
        free = self.FREE.get(key.split(":", 1)[0], 5)
        if len(times) < free:
            return 0
        wait = min(self.MAX_WAIT, 2 ** min(len(times) - free, 10) * 30)
        return max(0, int(times[-1] + wait - now))

    def wait(self, *keys):
        """Seconds the caller must wait before another try (0 = may try now)."""
        now = time.time()
        with self._lock:
            return max(self._key_wait(k, now) for k in keys)

    def fail(self, *keys):
        now = time.time()
        with self._lock:
            for k in keys:
                times = self._fails.setdefault(k, [])
                times.append(now)
                del times[:-self.MAX_TIMES]
            if len(self._fails) > self.MAX_KEYS:
                for k in [k for k, t in self._fails.items() if now - t[-1] >= self.WINDOW]:
                    del self._fails[k]
                if len(self._fails) > self.MAX_KEYS:
                    # still full: forget a tenth in one go (so this runs once per that many new keys), keys that
                    # aren't holding anyone back first, idlest first; a flood can't wipe a guesser's count early
                    drop = len(self._fails) - self.MAX_KEYS * 9 // 10
                    blocking = lambda k: len(self._fails[k]) >= self.FREE.get(k.split(":", 1)[0], 5)
                    for k in heapq.nsmallest(drop, self._fails, key=lambda k: (blocking(k), self._fails[k][-1])):
                        del self._fails[k]

    def clear(self, *keys):
        with self._lock:
            for k in keys:
                self._fails.pop(k, None)


def login_keys(username, address, device=None):
    """Throttle keys for a sign-in. A browser that has signed in to this account before (device = its cookie's
    hash) is counted on its own: other people's failures, from the same address behind a reverse proxy or
    aimed at this account from anywhere, never hold it up, and its own wrong guesses still get the 5 free tries."""
    u = username.strip().lower()
    if device:
        return ("pair:dev|" + device,)
    return ("pair:" + u + "|" + address, "ip:" + address, "user:" + u)


throttle = Throttle()
