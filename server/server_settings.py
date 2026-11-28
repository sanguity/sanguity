"""The server's settings that need a restart (#1121): changed on Settings → Install → General (Needs a restart), kept in
data/server-settings.json (mode 0600, the app's own user, written whole and atomically), and read once, as the server
starts: the upload limits, connected sources' and Health Links' limits, the password checks at once (HASH_PARALLEL),
the trusted proxies (uvicorn's forwarded_allow_ips), the phone receiver's time zone and unclaimed data's days, and
how long what a restore replaced is kept. Ports and the bind address stay Docker's. The environment still wins where
it sets one (not empty): then that row is shown as set there, and a change to it is refused.

A bad value must never leave the server unable to start, so a change is never put in force at once:

  saved        it's written as "pending" (General says Restart needed); what runs is unchanged
  next start   start() tries the pending values, first writing "trying" into the file
  healthy      once the server has been up and serving for PROMOTE_SECONDS (watch(), from python -m server),
               promote() makes them the "good" ones and takes "trying" away
  not healthy  the server stops before that (a value it can't start with, a crash), or isn't up within
               GIVE_UP_SECONDS (then watch() stops it): the next start finds "trying" still there, falls back to the
               good values, drops the pending ones and records why ("fell_back"), which General shows

Restart now (routes_settings.py) stops the app after answering; Docker's restart policy (unless-stopped, in every
template: deploy/check.py checks) starts it again. The phone receiver runs in a container of its own without the app's
data folder, so its three (TZ, SLOT_GRACE_DAYS, RESTORE_KEEP_DAYS) are copied into receiver-settings.json beside the
receiver's secrets (config.WATCH_KEYS' folder) as the app is restarted, and on a fall back; the receiver reads it
as it starts and restarts itself when it changes (watch/receiver.py). python -m server.manage config show and
config reset (manage.py) show these and put them back to their defaults from the command line.

Only the standard library and config: security.py reads HASH_PARALLEL as it's imported, so start() runs before it
(server/__main__.py), and nothing here may import it first."""
import ipaddress, json, os, re, signal, tempfile, threading, time

from . import config

FILE_NAME = "server-settings.json"
RECEIVER_FILE = "receiver-settings.json"   # beside the receiver's secrets (config.WATCH_KEYS), for watch/receiver.py
PROMOTE_SECONDS = 30   # answering /api/healthz this long with new values makes them the good ones
GIVE_UP_SECONDS = 180   # not healthy by then: stopped, and the next start falls back
_lock = threading.RLock()


def _say(line):
    print(line, flush=True)


class Refused(ValueError):
    """A value the setting doesn't take, said in plain language."""


class Def:
    def __init__(self, name, group, label, default, check, read, unit, help, kind, receiver, app):
        self.name, self.group, self.label, self.default, self.check = name, group, label, default, check
        self.read, self.unit, self.help, self.kind, self.receiver, self.app = read, unit, help, kind, receiver, app

    def public(self):
        lo, hi = getattr(self.check, "lo", None), getattr(self.check, "hi", None)
        return {"name": self.name, "group": self.group, "label": self.label, "unit": self.unit, "help": self.help,
                "kind": self.kind, "default": self.default, **({"min": lo, "max": hi} if lo is not None else {})}


DEFS = {}   # name (the variable's) -> Def, in the page's order


def define(name, group, label, check, unit="", help="", kind="number", default=None, read=None, receiver=False, app=True):
    """A setting that needs a restart. Its default is config.VARS' for a variable of the app's, else default; read: the
    environment's text -> its value (ValueError: unreadable, so as if unset), for the ones that aren't config.VARS'."""
    if name in DEFS:
        raise ValueError(f"{name} is defined twice")
    if name in config.VARS:
        default = config.VARS[name].default
    if check(default) != default:
        raise ValueError(f"{name}: its default doesn't pass its own check")
    DEFS[name] = Def(name, group, label, default, check, read, unit, help, kind, receiver, app)


def whole(lo, hi, what):
    def check(v):
        if isinstance(v, str) and re.fullmatch(r"\s*\d+\s*", v):
            v = int(v)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v) or not lo <= v <= hi:
            raise Refused(f"{what} must be a whole number from {lo:,} to {hi:,}.")
        return int(v)
    check.lo, check.hi = lo, hi
    return check


PROXIES_MOST = 20


def proxies(v):
    """Whose X-Forwarded-For and -Proto the app believes: "*" (anyone: only where nothing but the proxy can reach the
    app, as in every template), or addresses and networks (10.0.0.0/8), comma-separated. Said the way uvicorn reads it."""
    items = v if isinstance(v, list) else str(v).split(",") if isinstance(v, str) else None
    if items is None or len(items) > PROXIES_MOST:
        raise Refused(f"Trusted proxies are up to {PROXIES_MOST} addresses or networks, comma-separated, or *.")
    out = []
    for item in (str(i).strip() for i in items):
        if not item:
            continue
        if item == "*":
            out.append(item)
            continue
        try:
            n = ipaddress.ip_address(item) if "/" not in item else ipaddress.ip_network(item)
        except ValueError:
            try:   # a network with host bits set (10.0.0.5/8): said, never widened to its network in silence
                net = ipaddress.ip_network(item, strict=False)
            except ValueError:
                raise Refused(f"“{item[:60]}” isn't an address (like 172.18.0.2) or a network (like 172.16.0.0/12).") from None
            raise Refused(f"“{item}” isn't a network's start: it's in the network {net}. Give {net} to trust all of that "
                          f"network, or {item.split('/')[0]} for that one address.") from None
        n = "*" if getattr(n, "prefixlen", None) == 0 else str(n)   # 0.0.0.0/0 and ::/0 trust everyone, as * does
        if n == "*":
            out.append(n)
            continue
        if n not in out:
            out.append(n)
    if not out:
        raise Refused("Give at least one trusted proxy: 127.0.0.1 is the server's own machine.")
    if "*" in out and len(out) > 1:
        raise Refused("* (or 0.0.0.0/0, ::/0) already trusts every address: give it alone.")
    if "*" not in out:   # networks that together are every address (0.0.0.0/1 and 128.0.0.0/1): * by another name
        nets = [ipaddress.ip_network(n) for n in out]
        for version, name in ((4, "IPv4"), (6, "IPv6")):
            if any(c.prefixlen == 0 for c in ipaddress.collapse_addresses(n for n in nets if n.version == version)):
                raise Refused(f"Together these networks trust every {name} address: give * instead, which asks you to "
                              "confirm that any client may then claim any address.")
    return ", ".join(out)


def _env_proxies(text):
    """The environment's FORWARDED_ALLOW_IPS: always its own text, as uvicorn reads it (never proxies()' form of it);
    one proxies() wouldn't take raises, so lookup logs it, and keeps the text all the same."""
    proxies(text)
    return text


def proxies_cover(value, address):
    """Whether the trusted proxies value (as proxies() gives it) trusts address (a request's peer)."""
    if value == "*":
        return True
    try:
        a = ipaddress.ip_address(address)
    except ValueError:
        return False
    for item in value.split(", "):
        try:
            if a in ipaddress.ip_network(item, strict=False):
                return True
        except ValueError:
            continue
    return False


def _zones():
    try:
        import zoneinfo
        return zoneinfo.available_timezones()   # empty where there's no time zone database
    except Exception:
        return set()


def time_zone(v):
    """An IANA time zone name, checked against the time zone database where this machine has one (the receiver checks
    it again as it reads it, and keeps UTC for one it doesn't know)."""
    if not isinstance(v, str) or len(v) > 64 or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_+\-]*(/[A-Za-z0-9_+\-]+){0,2}", v.strip()):
        raise Refused("A time zone is a name like America/Chicago or Europe/Paris.")
    v = v.strip()
    known = _zones()
    if known and v not in known and v != "UTC":
        raise Refused(f"There's no time zone called {v}.")
    return v


def _days(text):
    v = float(text)
    if not 0 <= v < 1e6:
        raise ValueError(text)
    return int(v) if v.is_integer() else v


define("UPLOAD_MAX_FILE_MB", "Uploads", "One file", whole(1, 4096, "One file"), "MB",
       "nginx takes one file sent of up to 4 GB.")
define("UPLOAD_MAX_TOTAL_MB", "Uploads", "One upload, unpacked", whole(1, 65536, "One upload"), "MB")
define("UPLOAD_MAX_FILES", "Uploads", "Files in one upload", whole(1, 100000, "Files in one upload"), "files")
define("JOURNAL_PHOTO_MAX_MB", "Uploads", "A journal photo", whole(1, 25, "A journal photo"), "MB",
       "nginx takes one photo sent of up to 25 MB.")
define("SYNC_HOURS", "Connected sources", "Time between a connection's pulls", whole(1, 168, "The time between pulls"), "hours")
define("SYNC_MAX_RESOURCES", "Connected sources", "Records one pull fetches", whole(100, 100000, "Records one pull fetches"),
       "records", "The rest come next time.")
define("SYNC_MAX_ATTACHMENT_MB", "Connected sources", "One attachment, at most", whole(1, 1024, "One attachment"), "MB",
       "The Sync defaults' ceiling: a bigger attachment is left out, with a warning.")
define("CONNECT_TIMEOUT", "Connected sources", "Wait for a vendor's answer", whole(5, 120, "The wait for a vendor"), "seconds")
define("SHL_MAX_MB", "Health Links", "What one link brings", whole(1, 256, "What one link brings"), "MB")
define("SHL_MAX_FILES", "Health Links", "Files in one link", whole(1, 500, "Files in one link"), "files")
define("SHL_TIMEOUT", "Health Links", "Wait for each request", whole(5, 120, "The wait for each request"), "seconds")
define("HASH_PARALLEL", "Server", "Password checks at once", whole(1, 8, "Password checks at once"), "at a time",
       "Each uses 64 MiB of memory while it runs; more wait their turn.")
define("FORWARDED_ALLOW_IPS", "Server", "Trusted proxies", proxies, kind="text", default="127.0.0.1", read=_env_proxies,
       help="Whose forwarded headers (the visitor's address, HTTPS or not) the server believes: addresses or networks, "
            "comma-separated, or * where only the proxy can reach the app.")
define("TZ", "Phone receiver", "Time zone", time_zone, kind="zone", default="UTC", read=time_zone, receiver=True, app=False,
       help="The receiver's days start at midnight here, for uploads that don't say their own.")
define("SLOT_GRACE_DAYS", "Phone receiver", "Unclaimed data, kept", whole(1, 365, "Unclaimed data's days"), "days",
       "Phone data no profile or upload secret names is deleted this long after it's first found.",
       default=7, read=_days, receiver=True, app=False)
define("RESTORE_KEEP_DAYS", "Backups", "What a restore replaced, kept", whole(1, 3650, "What a restore replaced"), "days",
       "Deleted this long after the restore, here and by the receiver.", receiver=True)


# ---------------------------------------------------------------- the file

def path():
    return os.path.join(config.DATA_DIR, FILE_NAME)


def receiver_path():
    return os.path.join(os.path.dirname(config.WATCH_KEYS), RECEIVER_FILE)


def read_doc():
    """(the file's object, None) or ({}, why it couldn't be read): {} when there's none."""
    try:
        with open(path(), encoding="utf-8") as f:
            doc = json.load(f)
    except FileNotFoundError:
        return {}, None
    except (OSError, ValueError) as e:
        return {}, f"{path()} couldn't be read ({type(e).__name__}), so the defaults are in force."
    return (doc, None) if isinstance(doc, dict) else ({}, f"{path()} isn't a settings file, so the defaults are in force.")


def _write(target, obj):
    """Written whole or not at all: a file of its own (mode 0600) beside it, then renamed over it."""
    folder = os.path.dirname(target)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(target) + "-", dir=folder)   # (0600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=1, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        _same_owner(tmp, folder)
        os.replace(tmp, target)
    except BaseException:
        with _quiet():
            os.unlink(tmp)
        raise
    with _quiet():
        d = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(d)
        finally:
            os.close(d)


def _same_owner(p, folder):
    """Written by root (server.manage run as root): handed to the folder's owner, the app's user, so the app can read it."""
    if hasattr(os, "getuid") and os.getuid() == 0:
        st = os.stat(folder)
        if st.st_uid != 0:
            os.chown(p, st.st_uid, st.st_gid)


class _quiet:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return exc[0] is not None and issubclass(exc[0], OSError)


def write_doc(doc):
    _write(path(), doc)


def clean(values):
    """{name: value} of those that pass their check; the rest are left out (their default), never an error."""
    out = {}
    for name, v in (values.items() if isinstance(values, dict) else ()):
        d = DEFS.get(name)
        if d is None:
            continue
        try:
            v = d.check(v)
        except (Refused, TypeError, ValueError):
            continue
        if v != d.default:
            out[name] = v
    return out


# ---------------------------------------------------------------- at start

_started = None   # {name: value} in force in this process; None until start() or the first lookup
_state = {"tried": False, "fell_back": None, "unreadable": None}


def start(now=None, say=_say):
    """As the server starts (server/__main__.py, before anything reads a setting): the values this process uses. The
    pending ones when there are, marked "trying" first; the good ones when the last start that tried pending ones
    didn't come up healthy (and why, recorded in the file), or when the marker can't be written."""
    global _started
    now = int(now if now is not None else time.time())
    with _lock:
        doc, err = read_doc()
        good = clean(doc.get("good"))
        _state.update(tried=False, fell_back=None, unreadable=err)
        if err:
            say(f"[settings] {err}")
        if doc.get("trying"):
            t = doc["trying"] if isinstance(doc["trying"], dict) else {}
            why = t.get("why") or (f"The server stopped before it had run healthy for {PROMOTE_SECONDS} seconds with "
                                   "them, so it started again with the last values that worked.")
            doc["fell_back"] = {"at": now, "tried_at": t.get("at"), "why": why, "values": clean(doc.get("pending")), "kept": good}
            doc.pop("pending", None)
            doc.pop("trying", None)
            try:
                write_doc(doc)
            except OSError as e:
                say(f"[settings] couldn't record the fall back in {path()}: {type(e).__name__}")
            _state["fell_back"] = doc["fell_back"]
            say(f"[settings] fell back to the last good settings: {why}")
            write_receiver(good, say)
            _started = good
        elif "pending" in doc:
            pending = clean(doc.get("pending"))
            doc["trying"] = {"at": now}
            try:
                write_doc(doc)
            except OSError as e:   # without the marker a value that fails would be tried again and again
                say(f"[settings] couldn't write {path()} ({type(e).__name__}): the new settings aren't tried; the last good ones are used")
                _started = good
            else:
                _state["tried"] = True
                say(f"[settings] trying the new settings: {', '.join(n for n in DEFS if pending.get(n) != good.get(n)) or 'none changed'}")
                _started = pending
        else:
            _started = good
        return dict(_started)


def in_force():
    """{name: value} from the file in force in this process: start()'s, else (a process that isn't the server, like
    server.manage, or the tests' app) the good ones, read once."""
    global _started
    with _lock:
        if _started is None:
            doc, err = read_doc()
            _started = clean(doc.get("good"))
        return _started


def forget():
    """Read the file again at the next lookup (the tests, and server.manage after a reset)."""
    global _started
    with _lock:
        _started = None
        _state.update(tried=False, fell_back=None, unreadable=None)


def promote(say=_say):
    """The values this process tried have run healthy: they're the good ones now. False when it tried none."""
    with _lock:
        doc, err = read_doc()
        if err or not doc.get("trying"):
            return False
        doc["good"] = dict(_started or {})
        doc.pop("trying", None)
        doc.pop("fell_back", None)
        if clean(doc.get("pending")) == doc["good"]:
            doc.pop("pending", None)
        write_doc(doc)
        _state["tried"] = False
        say("[settings] the new settings work: kept as the good ones")
        return True


def give_up(why, say=_say):
    """Not healthy with the values tried: says why in the file, for the next start's fall back."""
    with _lock:
        doc, err = read_doc()
        if not err and doc.get("trying"):
            doc["trying"] = {**(doc["trying"] if isinstance(doc["trying"], dict) else {}), "why": why}
            with _quiet():
                write_doc(doc)
        say(f"[settings] {why}")


POLL = 2   # seconds between its health checks


def watch(healthy, stop=None):
    """While new values are being tried: promote them once the server has been healthy (healthy(): python -m server's
    uvicorn has started, the app's start-up done, and isn't stopping; asked in-process, so nothing here opens a
    connection: only server/net.py does) for PROMOTE_SECONDS in a row; stop the server (SIGTERM, the way Docker stops
    it) if it hasn't been by GIVE_UP_SECONDS, so the next start falls back. stop: what stops it (the tests')."""
    if not _state["tried"]:
        return None

    def run():
        began, first = time.monotonic(), None
        while True:
            time.sleep(POLL)
            try:
                ok = bool(healthy())
            except Exception:
                ok = False
            at = time.monotonic()
            first = (first or at) if ok else None
            if first is not None and at - first >= PROMOTE_SECONDS:
                with _quiet():
                    promote()
                return
            if at - began >= GIVE_UP_SECONDS:
                give_up(f"The server wasn't up and serving for {PROMOTE_SECONDS} seconds within "
                        f"{GIVE_UP_SECONDS} seconds of starting with them, so it was stopped and started again with "
                        "the last values that worked.")
                (stop or stop_now)()
                return
    t = threading.Thread(target=run, name="settings-watch", daemon=True)
    t.start()
    return t


def stop_now():
    os.kill(os.getpid(), signal.SIGTERM)   # uvicorn's own way out: requests finish, then the process exits


def stop_soon(seconds=1.0):
    """Restart now: the app stops a moment after its answer has gone (Docker starts it again)."""
    t = threading.Timer(seconds, stop_now)
    t.daemon = True
    t.start()


def log_start(db):
    """As the app starts (main.py's lifespan): a fall back this start made goes in the install log, by start-up."""
    fb = _state["fell_back"]
    if not fb:
        return
    from . import activity, db as dbm
    with dbm.transaction(db):
        activity.log(db, "server.restart", detail={"fell_back": fb["why"], "changes": [
            {"setting": n, "label": f"{d.label} ({d.group})", "from": fb["values"].get(n, d.default), "to": fb["kept"].get(n, d.default)}
            for n, d in DEFS.items() if fb["values"].get(n, d.default) != fb["kept"].get(n, d.default)]}, actor=activity.START_UP)
    _state["fell_back"] = None


class PeerKept:
    """The app python -m server runs, outside uvicorn's ProxyHeadersMiddleware: the address each request came through
    (its peer, before a trusted proxy's X-Forwarded-For replaces it) kept in scope["sanguity.peer"], so a change to
    the trusted proxies can refuse a list that leaves out the proxy it came through (routes_settings.py)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            c = scope.get("client")
            scope["sanguity.peer"] = c[0] if c else None
        await self.app(scope, receive, send)


def asgi_app(app, forwarded_allow_ips):
    """main.app as python -m server serves it: forwarded headers believed from forwarded_allow_ips (uvicorn's own
    middleware, as its proxy_headers does), with each request's peer kept (PeerKept)."""
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    return PeerKept(ProxyHeadersMiddleware(app, trusted_hosts=forwarded_allow_ips))


def uvicorn_options():
    """What python -m server gives uvicorn: the address it binds (Docker's: HOST and PORT) and whose forwarded
    headers it believes (FORWARDED_ALLOW_IPS: the environment's, else the file's, else this machine's own only)."""
    return {"host": os.environ.get("HOST", "0.0.0.0"), "port": int(os.environ.get("PORT", "8000")),
            "forwarded_allow_ips": lookup("FORWARDED_ALLOW_IPS")[1]}


# ---------------------------------------------------------------- what's in force, and changes

def lookup(name):
    """(where it comes from, the value in force): "environment", "file" (changed on the page, applied at the last
    start) or "default". A variable of config.VARS through config.lookup, which reads the file's in turn."""
    d = DEFS[name]
    if name in config.VARS:
        return config.lookup(name)
    text = os.environ.get(name, "").strip()
    if text:   # the environment's always wins, even one these checks wouldn't take (#1131's review): never dropped
        try:
            return "environment", d.read(text)
        except (Refused, ValueError):
            _unread(name, text)
            # a text one as it's set (uvicorn reads the proxies its own way: Cloudflare's 22 ranges, "unix:"), a number
            # at its default, as the receiver reads one it can't
            return "environment", text if d.kind != "number" else d.default
    have = in_force()
    return ("file", have[name]) if name in have else ("default", d.default)


_warned = set()


def _unread(name, text):
    """Said once per value: one the environment sets that this page's checks wouldn't take, used as it's set."""
    if (name, text) not in _warned:
        _warned.add((name, text))
        import logging
        logging.getLogger("sanguity").warning("%s is set in the environment to a value Settings → General wouldn't take; "
                                              "it's used as it's set (the environment's always wins)", name)


def state():
    """For General's card: each setting's value in force, where it comes from, and the value the next start takes
    where that differs; whether one waits for a restart; whether the values in force are still being tried; the last
    fall back, and a file that couldn't be read."""
    doc, err = read_doc()
    pending = clean(doc.get("pending")) if "pending" in doc else None
    now = in_force()
    rows = []
    for name, d in DEFS.items():
        src, value = lookup(name)
        nxt = value if src == "environment" or pending is None else pending.get(name, d.default)
        rows.append({**d.public(), "value": value, "from": src, "can_change": src != "environment",
                     **({"next": nxt} if nxt != value else {})})
    return {"settings": rows, "restart_needed": pending is not None and pending != now,
            "trying": bool(doc.get("trying")), "fell_back": doc.get("fell_back"), "unreadable": err or _state["unreadable"],
            "promote_seconds": PROMOTE_SECONDS}


def change(values, by=None, now=None):
    """Save values ({name: value, or None for its default}) as pending, for the next start. Refused (Refused) where one
    isn't taken, or (LookupError) one the environment sets or there's no such setting. Returns [(name, before, after)]
    of the ones that change what the next start takes."""
    with _lock:
        doc, err = read_doc()
        base = clean(doc.get("pending")) if "pending" in doc else clean(doc.get("good"))
        new = dict(base)
        for name, v in values.items():
            d = DEFS.get(name)
            if d is None:
                raise LookupError(f"There's no setting called {name} here.")
            if lookup(name)[0] == "environment":
                raise PermissionError(f"“{d.label}” is set by the server's environment ({name}), so it can't be changed "
                                      f"here. To change it here, take {name} out of where the server is set up and start it again.")
            v = d.default if v is None or v == "" else d.check(v)
            if v == d.default:
                new.pop(name, None)
            else:
                new[name] = v
        changed = [(n, base.get(n, DEFS[n].default), new.get(n, DEFS[n].default)) for n in DEFS if base.get(n) != new.get(n)]
        if not changed:
            return []
        if new == in_force() and not doc.get("trying"):
            doc.pop("pending", None)   # back to what runs: nothing waits for a restart
        else:
            doc["pending"] = new
        doc.pop("fell_back", None)
        doc["changed"] = {"at": int(now if now is not None else time.time()), **({"by": by} if by else {})}
        doc.setdefault("good", clean(doc.get("good")))
        write_doc(doc)
        return changed


def next_values():
    """The values the next start takes: the pending ones, else the good ones."""
    doc, _ = read_doc()
    return clean(doc.get("pending")) if "pending" in doc else clean(doc.get("good"))


def reset(names=None):
    """server.manage config reset: the file's values (the good, the pending, all of them or only names) back to their
    defaults, the next start's included, with nothing left to try. Returns {name: the value it held} of those that
    held one (the pending one, where there was)."""
    with _lock:
        doc, err = read_doc()
        good, pending = clean(doc.get("good")), clean(doc.get("pending"))
        names = set(DEFS) if names is None else set(names)
        had = {n: pending.get(n, good.get(n)) for n in DEFS if n in names and (n in good or n in pending)}
        if not had and not err and not doc.get("trying"):
            return {}
        new = {k: v for k, v in good.items() if k not in names}
        left = {k: v for k, v in pending.items() if k not in names}
        doc = {"good": new, **({"pending": left} if "pending" in doc and left != new else {})}
        write_doc(doc)
        write_receiver(new)
        forget()
        return had


def write_receiver(values, say=_say):
    """The receiver's settings (TZ, SLOT_GRACE_DAYS, RESTORE_KEEP_DAYS, as set on the page) into its folder, for it to
    read as it starts; only when they differ from what's there (it restarts itself on a change). False when its folder
    isn't there (no receiver) or can't be written."""
    want = {n: v for n, v in values.items() if DEFS[n].receiver}
    target = receiver_path()
    if not os.path.isdir(os.path.dirname(target)):
        return False
    try:
        with open(target, encoding="utf-8") as f:
            if json.load(f) == want:
                return True
    except (OSError, ValueError):
        pass
    try:
        _write(target, want)
        return True
    except OSError as e:
        say(f"[settings] couldn't write the receiver's settings to {target}: {type(e).__name__}")
        return False
