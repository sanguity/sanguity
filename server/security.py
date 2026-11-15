"""Passwords (Argon2id, peppered), session tokens, and sign-in throttling.

Passwords are stored as "pepper1:" + Argon2id of HMAC-SHA256(pepper, password). The pepper is a server secret kept
out of the database's reach (atrest.load_pepper: stored in it only encrypted with the data key), so a copied
sanguity.db can't be checked against guesses without the key file too. Hashes from before the pepper (plain
Argon2id) still verify, and are replaced at the person's next sign-in (needs_rehash).
"""
import array, base64, contextlib, gzip, hashlib, heapq, hmac, os, re, secrets, threading, time

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


PEPPERED = "pepper1:"
_pepper = None


def set_pepper(value):
    """The 32-byte server secret mixed into password and recovery-code hashes (atrest.load_pepper sets it)."""
    global _pepper
    _pepper = value


def pepper_mac(data):
    """HMAC-SHA256(pepper, data). Refuses to run without the pepper, so nothing is ever stored unpeppered."""
    if _pepper is None:
        raise RuntimeError("The pepper isn't loaded (atrest.load_pepper runs at start-up).")
    return hmac.new(_pepper, data, hashlib.sha256).digest()


def _peppered(pw):
    return base64.b64encode(pepper_mac(pw.encode("utf-8"))).decode()


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
    peppered = _peppered(pw)
    with _slot():
        return PEPPERED + _ph.hash(peppered)


def verify_password(stored, pw):
    """True when pw matches. A missing account still costs one hash, so response time doesn't reveal usernames.
    A hash from before the pepper (plain Argon2id) is checked the old way."""
    if stored and stored.startswith(PEPPERED):
        stored, pw = stored[len(PEPPERED):], _peppered(pw)
    with _slot():
        try:
            return _ph.verify(stored or _DUMMY, pw) and stored is not None
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False


def needs_rehash(stored):
    """True for a hash from before the pepper, or one made with weaker Argon2 parameters than today's."""
    if not stored.startswith(PEPPERED):
        return stored.startswith("$argon2")
    try:
        return _ph.check_needs_rehash(stored[len(PEPPERED):])
    except InvalidHashError:
        return False


class _Breached:
    """passwords/common.txt.gz (see its README): about half a million breached passwords, sorted. Kept as one block
    of bytes plus line offsets and searched by bisection, so it costs about 7 MB instead of a set's 40-odd, and is
    loaded only the first time a password is chosen."""
    PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "passwords", "common.txt.gz")

    def __init__(self):
        self._blob, self._starts, self._lock = None, None, threading.Lock()

    def _load(self):
        with self._lock:
            if self._blob is None:
                with gzip.open(self.PATH, "rb") as f:
                    blob = f.read()
                starts = array.array("I", [0])
                i = blob.find(b"\n")
                while i != -1:
                    starts.append(i + 1)
                    i = blob.find(b"\n", i + 1)
                self._blob, self._starts = blob, starts   # the last start is the end of the blob

    def __contains__(self, word):
        if self._blob is None:
            self._load()
        key, blob, starts = word.encode("utf-8"), self._blob, self._starts
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            line = blob[starts[mid]:starts[mid + 1] - 1]
            if line < key:
                lo = mid + 1
            elif line > key:
                hi = mid
            else:
                return True
        return False


BREACHED = _Breached()


def _variants(pw):
    """The forms of a password checked against the list: as typed, without spaces, and without digits or
    symbols tacked on at either end ("Correct horse battery staple 1!" is still a famous phrase)."""
    p = pw.strip().lower()
    out = {p, re.sub(r"\s+", "", p)}
    out |= {re.sub(r"^[\W\d_]+|[\W\d_]+$", "", v) for v in list(out)}
    return {v for v in out if len(v) >= 8}


def password_problem(pw, username="", minimum=None):
    """Why a new password is refused, or None. Per NIST SP 800-63B: a minimum length (shorter with two-factor
    sign-in on), no composition rules, and nothing from lists of breached passwords or built from this service's
    name or the username."""
    minimum = minimum or config.PASSWORD_MIN
    if not isinstance(pw, str) or len(pw) < minimum:
        return f"Use at least {minimum} characters. A few unrelated words make a strong, memorable password."
    if len(pw) > config.PASSWORD_MAX:
        return f"Use at most {config.PASSWORD_MAX} characters."
    if len(set(pw)) < 4:
        return "That password is too repetitive."
    letters, name = re.sub(r"[^a-z]", "", pw.lower()), re.sub(r"[^a-z]", "", (username or "").lower())
    context = "|".join(re.escape(w) for w in ("sanguity", name) if w)
    if not letters or re.fullmatch(f"(?:{context})+", letters):
        return "Don't build the password from the username or the word Sanguity."
    if any(v in BREACHED for v in _variants(pw)):
        return ("That password appears in lists of breached passwords that attackers try first. "
                "Pick something less common: a few unrelated words work well.")
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
