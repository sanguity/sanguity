"""The server's settings: its folders and files, from the environment (defaults are the container paths set up by
compose.yaml), and the settings that tune it (VARS), each read through setting(NAME) where it's used."""
import os


DATA_DIR = os.environ.get("SANGUITY_DATA", "/data")              # sanguity.db and profiles/<id>/
BUILD_DIR = os.environ.get("SANGUITY_BUILD", os.path.join(DATA_DIR, "build"))   # where setup writes the built data
RECORDS_DIR = os.environ.get("SANGUITY_RECORDS", "/srv/records")  # record exports (read-only); documents link here
# www/ of an install from before accounts: documents behind a sources.json "files" prefix lived in www/files*/
LEGACY_WWW = os.environ.get("SANGUITY_LEGACY_WWW", "/srv/www")
WATCH_FILE = os.environ.get("SANGUITY_WATCH_FILE", "/srv/watch/daily.json")     # the receiver's daily summary
DB_PATH = os.path.join(DATA_DIR, "sanguity.db")
# the encryption key file: kept apart from DATA_DIR (and backed up separately); see crypto.py
KEY_FILE = os.environ.get("SANGUITY_KEY_FILE", "/run/secrets/sanguity/data_key")
# the first-run setup code is printed to the log and also written here (readable only by the app's own user),
# for when the log is hard to reach; it is removed as soon as the first account exists
SETUP_CODE_FILE = os.path.join(DATA_DIR, "setup-code.txt")
BACKUP_DIR = os.environ.get("SANGUITY_BACKUPS", os.path.join(DATA_DIR, "backups"))   # where server.manage backup writes

COOKIE = "sg_session"
# over HTTPS the session cookie is named with the __Host- prefix, so the browser holds it to Secure, Path=/ and
# this exact host (no subdomain can set or overwrite it); plain HTTP can't carry that prefix, so it keeps COOKIE
COOKIE_HOST = "__Host-" + COOKIE
DEVICE_COOKIE, DEVICE_DAYS = "sg_device", 365   # marks a browser that has signed in to an account before
PASSWORD_MAX = 256
HASH_WAIT = 2   # seconds a sign-in waits for its turn before the API answers 503 "busy, try again"
MB = 2**20
# plain-HTTP issuers (only for a test server on this machine, like tests/fakefhir): never for a real vendor, so it is
# the environment's alone, never a setting, and no compose file passes it
CONNECT_ALLOW_HTTP = os.environ.get("CONNECT_ALLOW_HTTP", "") == "1"


# ---------------------------------------------------------------- the settings that tune the server (#999)
# Each is read where it's used, through setting(NAME), so a change applies at once. Its value is the environment's
# when the variable is set (not empty: the compose files pass "${NAME:-}", which is empty while .env doesn't set it),
# else the value an admin saved in the app, else the default below. Only a variable that an install setting of
# settings.py's registry stands for (define(..., env=NAME), which sets Var.setting) can have a saved value; the rest
# are the environment's or their default. source(NAME) says which it is. With SANGUITY_SAVED_SETTINGS=off, saved values
# are left out (#1006): the way back in when one locks everyone out (python -m server.manage config reset, too).
#
# Adding one: a var() line below, then setting("NAME") where it's used, its line in both compose files' app service
# ("NAME: ${NAME:-}", deploy/check.py checks), and in .env.example and the README's configuration. To let admins change
# it in the app, define() it in settings.py with scope="install", private=False and env="NAME".

class Var:
    def __init__(self, name, default, read, keep, about):
        self.name, self.default, self.read, self.keep, self.about = name, default, read, keep, about
        self.setting = None   # the key of the registry's install setting that stands for it, if one does


VARS = {}   # name -> Var


def var(name, default, read=int, keep=None, about=""):
    """read: the variable's text -> its value (ValueError: unreadable, so left out); keep: a saved value -> the same
    kind of value (the registry has checked it)."""
    VARS[name] = Var(name, default, read, keep or (lambda v: v), about)


def _origins(items):
    out = []
    for o in items:
        o = str(o).strip().lower().rstrip("/")
        if o and o not in out:
            out.append(o)
    return tuple(out)


def _days(text):
    v = float(text)
    if not 0 <= v < 1e6:   # (NaN isn't; nor is inf, which would never be reached)
        raise ValueError(text)
    return v


var("SESSION_DAYS", 30, about="a session ends this long after sign-in...")
var("SESSION_HOURS_WITHOUT_2FA", 12, about="...or this many hours after it, while the account has no second factor "
                                           "(0 = SESSION_DAYS for everyone)")
var("SESSION_IDLE_MINUTES", 0, about="also end it after this long unused (0 = off)")
var("COOKIE_SECURE", "auto", str.lower, about="auto = Secure when the request came over HTTPS; 1 always, 0 never")
var("TEMP_PASSWORD_DAYS", 7, about="a one-time password from an admin stops working after this")
var("REAUTH_MINUTES", 5, about="the fresh check before the most powerful admin actions: the password, a code or a "
                               "passkey within this many minutes (signing in counts)")
var("PUBLIC_ORIGINS", (), lambda t: _origins(t.split(",")), _origins,
    about="the addresses people open Sanguity at, comma-separated, e.g. https://health.example.com: for a reverse proxy "
          "that changes the Host header and sends no X-Forwarded-Host, and the address the app gives out (public_address)")
var("PASSWORD_MIN_LENGTH", 15, about="NIST SP 800-63B-4: at least 15 characters while a password is the only factor; "
                                     "no composition rules")
var("PASSWORD_MIN_LENGTH_2FA", 8, about="with two-factor sign-in on (NIST SP 800-63B)")
var("HASH_PARALLEL", 2, about="password hashes run at once (64 MiB each); more wait their turn. Read once, at start "
                              "(security.py's semaphore), so never a setting")
var("ACTIVITY_DAYS", 365, about="how long the activity log keeps entries")
# uploads from the import page (uploads.py). A file is one the browser sent or one unpacked from a zip; an upload's
# total counts what it holds after unpacking. nginx.conf caps one file sent at 4 GB too: raise both to go higher.
var("UPLOAD_MAX_FILE_MB", 4096)
var("UPLOAD_MAX_TOTAL_MB", 16384)
var("UPLOAD_MAX_FILES", 20000)
var("UPLOAD_MAX_ZIP_RATIO", 200, about="a file unpacked from a zip may be at most this many times its compressed size "
                                       "(a zip bomb is thousands)")
var("JOURNAL_PHOTO_MAX_MB", 20, about="a photo attached to a journal entry (#161, journal_photos.py); nginx.conf caps "
                                      "one sent at 25 MB: raise both to go higher")
# connected sources (#32, #91: connections.py, smart.py, sync.py). Nothing is contacted until someone connects a
# source an admin has set up; then only that source's issuer, and the hosts its discovery names.
var("SYNC_HOURS", 12, about="how often each connection pulls what's new")
var("SYNC_MAX_RESOURCES", 20000, about="the most one pull fetches; the rest come next time")
var("SYNC_MAX_ATTACHMENT_MB", 50, about="a bigger attachment is left out, with a warning; the Sync defaults' ceiling")
var("CONNECT_TIMEOUT", 30, about="seconds to wait for a vendor's answer")
# SMART Health Links (shl.py): the one fetch a pasted link makes, after the person confirms. Everything it brings
# (the manifest and every file in it) counts toward SHL_MAX_MB; each request gives up after SHL_TIMEOUT seconds.
var("SHL_MAX_MB", 64)
var("SHL_MAX_FILES", 50)
var("SHL_TIMEOUT", 20)
var("RECEIVER_PORT", 8731, about="the watch receiver's published port, for the upload address (watch_keys.py)")
var("WATCH_UPLOAD_URL", "", str.strip, about="the upload address, when the receiver is behind a reverse proxy")
var("RESTORE_KEEP_DAYS", 30, _days, about="what a restore replaced is deleted this long after (backup.py; the receiver "
                                          "reads its own, watch/receiver.py)")

saved = None   # settings.py sets it: () -> {key: value} of the install settings saved in the app
SAVED_OFF = ("off", "0", "no", "false")


def saved_ignored():
    """SANGUITY_SAVED_SETTINGS=off: the values saved in the app are left out, for as long as it's set (#1006)."""
    return os.environ.get("SANGUITY_SAVED_SETTINGS", "").strip().lower() in SAVED_OFF


def lookup(name):
    """(where it comes from, the value in force) of the variable name: ("environment" | "install" | "default", value)."""
    v = VARS[name]
    text = os.environ.get(v.name, "")
    if text.strip():
        try:
            return "environment", v.read(text.strip())
        except ValueError:   # unreadable: as if it weren't set (unreadable() says so at start)
            pass
    if v.setting and saved is not None and not saved_ignored():
        have = saved()
        if v.setting in have:
            return "install", v.keep(have[v.setting])
    return "default", v.default


def setting(name):
    """The value in force of the variable name (a key of VARS): the environment's, else the one saved in the app,
    else the default."""
    return lookup(name)[1]


def source(name):
    """Where setting(name) comes from: "environment", "install" (saved in the app) or "default"."""
    return lookup(name)[0]


def public_address():
    """The address the app gives out (#1009): share links, calendar feed addresses and their QR codes, the link back in
    each calendar event, and connected sources' return address. The first https:// one of PUBLIC_ORIGINS (in order of
    their names, as the return address has always been taken), else the first; None while it lists none: then the
    address a page was opened at."""
    origins = sorted(setting("PUBLIC_ORIGINS"), key=lambda o: (not o.startswith("https://"), o))
    return origins[0] if origins else None


def given_base():
    """{"base": public_address() + "/"} for an answer that gives out an address the page makes from a path (#1009),
    or {} with none set (the page uses its own address, as before)."""
    a = public_address()
    return {"base": a + "/"} if a else {}


def unreadable():
    """[(name, text)] of the variables set to something they can't be read as (left out, as if unset)."""
    out = []
    for v in VARS.values():
        text = os.environ.get(v.name, "").strip()
        if text:
            try:
                v.read(text)
            except ValueError:
                out.append((v.name, text))
    return out


# the watch receiver, one secret per profile (#46: watch_keys.py). The app writes the profiles' secrets here, sealed,
# for the receiver (compose: WATCH_DIR/keys); each profile's summary is <dirname(WATCH_FILE)>/profiles/<id>/daily.json,
# except the first profile's, which stays WATCH_FILE. Setup instructions give the upload address: WATCH_UPLOAD_URL
# when set (a receiver behind a reverse proxy), else each PUBLIC_ORIGINS host (or the address the page was opened
# at) on RECEIVER_PORT.
WATCH_KEYS = os.environ.get("SANGUITY_WATCH_KEYS", "/srv/watch-keys/receiver-secrets.json")
# the receiver's whole folder, read-only (#227: compose WATCH_DIR at /srv/watch-store, the released image's /watch): its
# databases and archived uploads go in backups. Unset, or not there: backups have no phone and watch data.
WATCH_STORE = os.environ.get("SANGUITY_WATCH_STORE", "")
