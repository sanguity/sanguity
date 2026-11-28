"""The server's settings that need a restart (#1121): changed on Settings → Install → General (Needs a restart), kept in
data/server-settings.json (mode 0600, the app's own user, written whole and atomically), and read once, as the server
starts: the upload limits, connected sources' and Health Links' limits, the password checks at once (HASH_PARALLEL),
the trusted proxies (uvicorn's forwarded_allow_ips), the phone receiver's time zone and unclaimed data's days, and
how long what a restore replaced is kept. Ports and the bind address stay Docker's. The file is their one source of
truth (#1158): on the first start of this version each the environment set was copied into it once (take_environment,
said in Install activity and once on General), and from then on one the environment sets is ignored, with a warning in
the log naming it when it's changed since.

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
        choices = getattr(self.check, "choices", None)   # (#1159) a choice's {value: label}
        return {"name": self.name, "group": self.group, "label": self.label, "unit": self.unit, "help": self.help,
                "kind": self.kind, "default": self.default, **({"min": lo, "max": hi} if lo is not None else {}),
                **({"choices": choices} if choices else {})}


DEFS = {}   # name (the variable's) -> Def, in the page's order


def define(name, group, label, check, unit="", help="", kind="number", default=None, read=None, receiver=False, app=True):
    """A setting that needs a restart. Its default is config.VARS' for a variable of the app's, else default; read: the
    environment's text -> its value (ValueError: unreadable, so not taken in, #1158), for the ones that aren't config.VARS'."""
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
define("UPLOAD_MAX_ZIP_RATIO", "Uploads", "A file unpacked from a zip, at most", whole(20, 2000, "A file unpacked from a zip"),
       "times its packed size", "A zip bomb is thousands of times its packed size; real exports are far below 200.")
define("JOURNAL_PHOTO_MAX_MB", "Uploads", "A journal photo", whole(1, 25, "A journal photo"), "MB",
       "nginx takes one photo sent of up to 25 MB.")
define("SYNC_HOURS", "Connected sources", "Time between a connection's pulls", whole(1, 168, "The time between pulls"), "hours")
define("SYNC_MAX_RESOURCES", "Connected sources", "Records one pull fetches", whole(100, 100000, "Records one pull fetches"),
       "records", "The rest come next time.")
define("SYNC_MAX_ATTACHMENT_MB", "Connected sources", "One attachment, at most", whole(1, 1024, "One attachment"), "MB",
       "The Sync defaults' ceiling: a bigger attachment is left out, with a warning.")
define("SHL_MAX_MB", "Health Links", "What one link brings", whole(1, 256, "What one link brings"), "MB")
define("SHL_MAX_FILES", "Health Links", "Files in one link", whole(1, 500, "Files in one link"), "files")
# (#1158) how long the server waits for another server, together where an admin looks for them
define("CONNECT_TIMEOUT", "Timeouts", "A connected source's vendor", whole(5, 120, "The wait for a vendor"), "seconds",
       "Each request of a sync, a connection or Test connection gives up after this long.")
define("SHL_TIMEOUT", "Timeouts", "A Health Link's server", whole(5, 120, "The wait for each request"), "seconds",
       "Each request of a pasted Health Link (its manifest, each file) gives up after this long.")
define("OIDC_TIMEOUT", "Timeouts", "The sign-in provider", whole(5, 60, "The wait for the sign-in provider"), "seconds",
       "Its discovery document, its keys and each sign-in's token: past this, the sign-in says the provider didn't answer.")
define("NOTIFY_TIMEOUT", "Timeouts", "The notification server", whole(3, 60, "The wait for the notification server"), "seconds",
       "Each notification and Send a test; one not answered in time is tried again later.")
define("DIRECTORY_TIMEOUT", "Timeouts", "The provider directory's lists", whole(10, 300, "The wait for a directory list"), "seconds",
       "How long a list's host may go silent while Update now or the weekly update downloads it.")
define("DOWNLOAD_TIMEOUT", "Timeouts", "A text-recognition language download", whole(10, 300, "The wait for a language download"),
       "seconds", "How long tessdata's host may go silent while a language file downloads.")
define("HASH_PARALLEL", "Server", "Password checks at once", whole(1, 8, "Password checks at once"), "at a time",
       "Each uses 64 MiB of memory while it runs; more wait their turn.")
# (#1158) every template puts the app behind its own nginx, with no port of its own published (the all-in-one container
# binds it to 127.0.0.1): there only nginx can reach it, and it sets the headers, so a template's default is "*";
# a server started some other way believes only a proxy on its own machine until told otherwise
# (#1158's review: only the kinds Sanguity's own templates set, never any value at all)
TEMPLATES = ("compose", "release", "truenas", "casaos", "unraid", "allinone")


def proxy_default(kind):
    """The trusted proxies' default where SANGUITY_DEPLOYMENT is kind: * under one of Sanguity's templates, else 127.0.0.1."""
    return "*" if (kind or "").strip().lower() in TEMPLATES else "127.0.0.1"


define("FORWARDED_ALLOW_IPS", "Server", "Trusted proxies", proxies, kind="text",
       default=proxy_default(os.environ.get("SANGUITY_DEPLOYMENT")),
       read=proxies, help="Whose forwarded headers (the visitor's address, HTTPS or not) the server believes: addresses or "
                          "networks, comma-separated, or * where only the proxy can reach the app (as in Sanguity's templates).")
define("TZ", "Phone receiver", "Time zone", time_zone, kind="zone", default="UTC", read=time_zone, receiver=True, app=False,
       help="The receiver's days start at midnight here, for uploads that don't say their own.")
define("SLOT_GRACE_DAYS", "Phone receiver", "Unclaimed data, kept", whole(1, 365, "Unclaimed data's days"), "days",
       "Phone data no profile or upload secret names is deleted this long after it's first found.",
       default=7, read=_days, receiver=True, app=False)
define("RESTORE_KEEP_DAYS", "Backups", "What a restore replaced, kept", whole(1, 3650, "What a restore replaced"), "days",
       "Deleted this long after the restore, here and by the receiver.", receiver=True)


# ---------------------------------------------------------------- (#1159) the address, built-in HTTPS, the log, the proxy
# Each can cut people off (a URL base the reverse proxy doesn't forward, a certificate that doesn't load, a proxy that
# isn't there), so each is a restart setting: tried at the next start and kept once healthy (watch(): for HTTPS its own
# port serving too, for the proxy one connection to it, made by net.py), else the start after falls back. Saving one
# is confirmed first (routes_settings.py), and a certificate is checked before it's saved (certificate()).

def url_base(v):
    """"" (the address's root) or a path such as /sanguity: segments of letters, digits and . _ ~ -, no trailing /."""
    if not isinstance(v, str) or len(v) > 100:
        raise Refused("A URL base is a path such as /sanguity, at most 100 characters.")
    v = v.strip()
    if v in ("", "/"):
        return ""
    v = "/" + v.strip("/")
    if not re.fullmatch(r"(/[A-Za-z0-9._~-]+)+", v) or any(p in (".", "..") for p in v.split("/")[1:]):
        raise Refused("A URL base is a path such as /sanguity: letters, digits and . _ ~ - between the slashes.")
    if v.split("/")[1].lower() in ("api", "js", "css", "fonts", "icons", "connect"):
        raise Refused(f"A URL base can't start with /{v.split('/')[1]}: Sanguity's own addresses do.")
    return v


def data_file(what):
    """A file's path in the data folder, kept relative to it ("" for none): nothing outside it is ever read."""
    def check(v):
        if not isinstance(v, str) or len(v) > 300:
            raise Refused(f"{what} is a file's path in the data folder, at most 300 characters.")
        v = v.strip()
        if not v:
            return ""
        root = os.path.realpath(config.DATA_DIR)
        full = os.path.realpath(os.path.join(root, v))
        if os.path.commonpath([root, full]) != root or full == root:
            raise Refused(f"{what} must be a file in the data folder ({config.DATA_DIR}), such as tls/cert.pem.")
        return os.path.relpath(full, root)
    return check


def full_path(rel):
    return os.path.join(config.DATA_DIR, rel) if rel else ""


def one_of(choices, what):
    def check(v):
        v = v.strip().lower() if isinstance(v, str) else v
        if v not in choices:
            raise Refused(f"{what} is one of: {', '.join(choices.values())}.")
        return v
    check.choices = choices
    return check


LOG_LEVELS = {"info": "Info", "debug": "Debug", "trace": "Trace"}
_PROXY = re.compile(r"(http|socks5)://([a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?|\[[0-9a-f:.]+\]):(\d{1,5})")


def proxy_address(v):
    """"" (none), or http://host:port or socks5://host:port. Never a username or password in it: they're their own."""
    if not isinstance(v, str) or len(v) > 300:
        raise Refused("The proxy is an address such as http://proxy.lan:3128 or socks5://10.0.0.2:1080.")
    v = v.strip().lower().rstrip("/")
    if not v:
        return ""
    if "@" in v:
        raise Refused("Give the proxy's username and password in their own fields, not in its address.")
    m = _PROXY.fullmatch(v)
    if not m or not 1 <= int(m.group(3)) <= 65535:
        raise Refused("The proxy is an address such as http://proxy.lan:3128 or socks5://10.0.0.2:1080: http or "
                      "socks5, its host, and its port.")
    return v


def proxy_user(v):
    if not isinstance(v, str) or len(v) > 100 or ":" in v or any(not 0x21 <= ord(c) <= 0x7e for c in v.strip()):
        raise Refused("The proxy's username is up to 100 letters, digits and signs, with no spaces or colon.")
    return v.strip()


BYPASS_MOST = 50


def proxy_bypass(v):
    """Hosts reached without the proxy besides the local ones (always): names (a leading . or *. for every name under
    one), addresses and networks, comma-separated."""
    items = v if isinstance(v, list) else str(v).split(",") if isinstance(v, str) else None
    if items is None or len(items) > BYPASS_MOST:
        raise Refused(f"The proxy's bypass list is up to {BYPASS_MOST} names, addresses or networks, comma-separated.")
    out = []
    for item in (str(i).strip().lower() for i in items):
        if item.startswith("*."):
            item = item[1:]
        if not item:
            continue
        try:
            item = str(ipaddress.ip_network(item, strict=False)) if "/" in item else str(ipaddress.ip_address(item.strip("[]")))
        except ValueError:
            item = item.rstrip(".")
            if not re.fullmatch(r"\.?[a-z0-9][a-z0-9-]{0,62}(?:\.[a-z0-9][a-z0-9-]{0,62})*", item):
                raise Refused(f"“{item[:60]}” isn't a name (proxy.lan, .example.org), an address or a network (10.0.0.0/8).") from None
        if item not in out:
            out.append(item)
    return ", ".join(out)


define("URL_BASE", "Address", "URL base", url_base, kind="text", default="", read=url_base,
       help="Serve Sanguity under a path of your domain, such as /sanguity, behind a reverse proxy that forwards that "
            "path, as it is or with it taken off. Every page, address the app gives out and cookie uses it; the "
            "container's nginx serves under it, and sends its root there. A host name of its own is still best: "
            "pages of other apps on the same host share its origin. Changing it signs everyone out (its cookies are "
            "named for it). Empty: at the address's root.")
define("HTTPS_CERT", "Built-in HTTPS", "Certificate file", data_file("The certificate"), kind="path", default="",
       read=data_file("The certificate"),
       help="A PEM certificate (with its chain) in the data folder, such as tls/cert.pem: with its key, Sanguity also "
            "serves HTTPS itself on its own port, so passkeys and installing the app work without a reverse proxy. "
            "Checked before it's saved.")
define("HTTPS_KEY", "Built-in HTTPS", "Key file", data_file("The key"), kind="path", default="", read=data_file("The key"),
       help="The certificate's private key (PEM, without a passphrase), in the data folder, such as tls/key.pem.")
define("HTTPS_PORT", "Built-in HTTPS", "HTTPS port", whole(1024, 65535, "The HTTPS port"), "", default=8443, read=int,
       help="Where HTTPS is served inside the app's container: publish this port from it to reach it.")
define("LOG_LEVEL", "Logging", "Log level", one_of(LOG_LEVELS, "The log level"), kind="choice", default="info",
       read=one_of(LOG_LEVELS, "The log level"),
       help="How much the app writes to its own log. Debug adds each request's method, route and status; Trace also "
            "each outside contact's host and the names of a request's query fields. Never a record's contents, a "
            "name, a value typed in, a password or a token, at any level.")
define("OUTBOUND_PROXY", "Outbound proxy", "Proxy", proxy_address, kind="text", default="", read=proxy_address,
       help="Every outside connection (connected sources, Health Links, the sign-in provider, notifications, downloads) "
            "through this HTTP or SOCKS5 proxy, such as http://proxy.lan:3128 or socks5://10.0.0.2:1080. The hosts "
            "allowed and their checks stay as they are; a blocked name still is, but an address block can't apply "
            "to a name the proxy looks up. Empty: none.")
define("OUTBOUND_PROXY_USER", "Outbound proxy", "Proxy username", proxy_user, kind="text", default="", read=proxy_user,
       help="Only if the proxy asks for one. Its password is set on its own, and never shown.")
define("OUTBOUND_PROXY_BYPASS", "Outbound proxy", "Reached without the proxy", proxy_bypass, kind="text", default="",
       read=proxy_bypass, help="Names (.example.org for every name under it), addresses and networks, comma-separated. "
                               "Local addresses (this network's, localhost, .local, .lan, .internal) always are.")
RISKY = {"Address": "URL_BASE", "Built-in HTTPS": "HTTPS_CERT", "Outbound proxy": "OUTBOUND_PROXY"}   # confirmed first


def value(name):
    """The value in force of a restart setting."""
    return lookup(name)[1]


def https_files(values=None):
    """(certificate path, key path) of built-in HTTPS in values ({name: value}; the ones in force), or None while off."""
    get = (lambda n: values.get(n, DEFS[n].default)) if values is not None else value
    cert, key = get("HTTPS_CERT"), get("HTTPS_KEY")
    return (full_path(cert), full_path(key)) if cert and key else None


WARN_DAYS = 30


def certificate(cert, key, now=None):
    """(#1159) A certificate and key checked as the server loads them: {"names", "expires", "days_left", "warn"}, or
    Refused in plain words. The key must be the certificate's own; an expired certificate isn't taken."""
    import ssl
    from cryptography import x509
    try:
        ssl.create_default_context(ssl.Purpose.CLIENT_AUTH).load_cert_chain(cert, key)
    except FileNotFoundError as e:
        raise Refused(f"There's no file at {e.filename or cert}.") from None
    except ssl.SSLError:
        raise Refused("The certificate and key don't load together: check that both are PEM files, that the key is the "
                      "certificate's own, and that it isn't protected by a passphrase.") from None
    except OSError as e:
        raise Refused(f"The certificate or key couldn't be read ({type(e).__name__}).") from None
    with open(cert, "rb") as f:
        c = x509.load_pem_x509_certificates(f.read())[0]
    try:
        alt = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        names = [str(n) for n in alt.get_values_for_type(x509.DNSName) + alt.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:
        names = [a.value for a in c.subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)]
    ends, now = int(c.not_valid_after_utc.timestamp()), int(now if now is not None else time.time())
    if ends <= now:
        raise Refused("That certificate has expired: renew it first.")
    left = (ends - now) // 86400
    return {"names": names[:20], "expires": ends, "days_left": left, "warn": left <= WARN_DAYS}


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
        if not err and take_environment(doc, now, say):
            try:
                write_doc(doc)
            except OSError as e:
                say(f"[settings] couldn't write {path()} ({type(e).__name__}): what the environment set is used this time only")
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
        if not _state["fell_back"]:   # (#1158) the receiver reads only its copy: the values this start uses
            write_receiver(_started, say)
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
    headers it believes (FORWARDED_ALLOW_IPS: the file's, else the default: "*" in a template, else this machine's own)."""
    return {"host": os.environ.get("HOST", "0.0.0.0"), "port": int(os.environ.get("PORT", "8000")),
            "forwarded_allow_ips": lookup("FORWARDED_ALLOW_IPS")[1]}


# ---------------------------------------------------------------- what's in force, and changes

def lookup(name):
    """(where it comes from, the value in force): "file" (changed on the page, applied at the last start) or "default".
    A variable of config.VARS through config.lookup, which reads the file's in turn."""
    d = DEFS[name]
    if name in config.VARS:
        return config.lookup(name)
    have = in_force()
    return ("file", have[name]) if name in have else ("default", d.default)


WHERE = "Settings → Install → General → Needs a restart"


def salvage(d, text):
    """(#1158's review) What a restart setting takes of the environment's text, never weaker than the text: (value, None)
    as given; (value, note) where it had to be adjusted: a number past its bounds is the nearest bound; the trusted
    proxies keep the entries that pass, one by one (a network with host bits set, which uvicorn trusted nobody for, or
    * among others, is left out: never widened), and with none, this machine's own (127.0.0.1, never *); (None, note)
    where nothing of it can be taken (then the default: the unreadable text never was in force)."""
    try:
        return d.check(d.read(text) if d.read else config.VARS[d.name].read(text)), None
    except (Refused, TypeError, ValueError):
        pass
    lo, hi = getattr(d.check, "lo", None), getattr(d.check, "hi", None)
    if d.kind == "number" and lo is not None:
        try:
            n = float(text.strip())
        except ValueError:
            n = None
        if n is not None and n == n:
            v = d.check(int(min(max(round(n), lo), hi)))
            return v, f"{v} is used: {text[:40]} is past what it takes ({lo:,} to {hi:,}), and {v} is the nearest it does"
    if d.name == "FORWARDED_ALLOW_IPS":
        parts = [p.strip() for p in text.split(",") if p.strip()]
        kept = []
        for p in parts:
            if p == "*" and len(parts) > 1:
                continue
            try:
                proxies(kept + [p])
            except Refused:
                continue
            kept.append(p)
        v = proxies(kept) if kept else "127.0.0.1"
        return v, f"{v} is used: what it didn't take of {text[:60]!r} is left out, never widened"
    return None, f"not taken: {text[:40]!r} isn't a value it takes, so it's {d.default}"


def take_environment(doc, now, say=_say):
    """(#1158) Once, on the first start of this version: each of these the environment sets is copied into doc (the
    good values, and the pending ones where there are: it was the one in force, and the page couldn't change it), and
    doc["taken"] records when, which were copied ("names"), which couldn't be as they were set and what was used instead
    ("adjusted", salvage) and a fingerprint of each other one's text ("seen"). From then on the environment sets none of
    them: one it sets that's changed since is said in the log, naming it, and ignored; an adjusted one is said at every
    start until it's put right. True when doc needs writing."""
    seen = {n: t for n in DEFS if (t := os.environ.get(n, "").strip())}
    taken = doc.get("taken")
    if isinstance(taken, dict):
        before = taken.get("seen") if isinstance(taken.get("seen"), dict) else {}
        adjusted = taken.get("adjusted") if isinstance(taken.get("adjusted"), dict) else {}
        for n, t in seen.items():
            if n in adjusted:
                say(f"[settings] {n} in the environment couldn't be taken in as it's set: {adjusted[n]}. Put it right on "
                    f"{WHERE} and take {n} out of the environment.")
            elif before.get(n) != config.fingerprint(t):
                say("[settings] " + config.environment_changed(n, WHERE))
        return False
    good, names, adjusted = clean(doc.get("good")), [], {}
    pending = clean(doc.get("pending")) if "pending" in doc else None
    for n, t in seen.items():
        d = DEFS[n]
        v, note = salvage(d, t)
        if note:
            adjusted[n] = note
            say(f"[settings] {n} in the environment couldn't be taken in as it's set: {note}. Put it right on {WHERE}.")
        if v is None:
            continue
        for values in (good, pending) if pending is not None else (good,):
            if v == d.default:
                values.pop(n, None)
            else:
                values[n] = v
        if v != d.default:
            names.append(n)
    doc["good"] = good
    if pending is not None:
        doc["pending"] = pending
    doc["taken"] = {"at": now, "names": names, "adjusted": adjusted,
                    "seen": {n: config.fingerprint(t) for n, t in seen.items() if n not in adjusted}}
    if names:
        say(f"[settings] taken from the environment into {path()}, once: {', '.join(names)}. From now on they're "
            f"changed on {WHERE} (or with python -m server.manage config set), not in the environment.")
    return True


def take_late(say=_say):
    """(#1158) take_environment for a process the start-up path didn't start (the app run without python -m server: its
    tests, a development server), as the app starts."""
    global _started
    with _lock:
        doc, err = read_doc()
        if err or isinstance(doc.get("taken"), dict):
            return False
        if not take_environment(doc, int(time.time()), say):
            return False
        write_doc(doc)
        if not _state["tried"]:
            _started = clean(doc.get("good"))
        return True


def taken():
    """What the first start of this version took from the environment into the file: {"at", "names"} or None."""
    doc, _ = read_doc()
    t = doc.get("taken")
    return ({"at": t.get("at"), "names": [n for n in t.get("names") or [] if n in DEFS],
             "adjusted": {n: v for n, v in (t.get("adjusted") or {}).items() if n in DEFS}} if isinstance(t, dict) else None)


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
        nxt = value if pending is None else pending.get(name, d.default)
        rows.append({**d.public(), "value": value, "from": src, "can_change": True,
                     **({"next": nxt} if nxt != value else {})})
    return {"settings": rows, "restart_needed": pending is not None and pending != now,
            "trying": bool(doc.get("trying")), "fell_back": doc.get("fell_back"), "unreadable": err or _state["unreadable"],
            "promote_seconds": PROMOTE_SECONDS}


def change(values, by=None, now=None):
    """Save values ({name: value, or None for its default}) as pending, for the next start. Refused (Refused) where one
    isn't taken, or (LookupError) there's no such setting. Returns [(name, before, after)]
    of the ones that change what the next start takes."""
    with _lock:
        doc, err = read_doc()
        base = clean(doc.get("pending")) if "pending" in doc else clean(doc.get("good"))
        new = dict(base)
        for name, v in values.items():
            d = DEFS.get(name)
            if d is None:
                raise LookupError(f"There's no setting called {name} here.")
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
        doc = {"good": new, **({"pending": left} if "pending" in doc and left != new else {}),
               **({"taken": doc["taken"]} if "taken" in doc else {})}   # (#1158) never taken in again
        write_doc(doc)
        write_receiver(new)
        forget()
        return had


def write_receiver(values, say=_say):
    """The receiver's settings (TZ, SLOT_GRACE_DAYS, RESTORE_KEEP_DAYS, as set on the page) into its folder, for it to
    read as it starts; only when they differ from what's there (it restarts itself on a change). False when its folder
    isn't there (no receiver) or can't be written."""
    want = {n: v for n, v in values.items() if n in DEFS and DEFS[n].receiver}
    target = receiver_path()
    if not os.path.isdir(os.path.dirname(target)):
        return False
    try:
        with open(target, encoding="utf-8") as f:
            if json.load(f) == want:
                return True
    except FileNotFoundError:
        if not want:   # none: the receiver's defaults, as no file is (and nothing to restart it for)
            return True
    except (OSError, ValueError):
        pass
    try:
        _write(target, want)
        return True
    except OSError as e:
        say(f"[settings] couldn't write the receiver's settings to {target}: {type(e).__name__}")
        return False
