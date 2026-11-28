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


# ---------------------------------------------------------------- what only the container sets (#1158)
# Every variable the server, the receiver and docker/ read is either a setting the page owns (VARS below, the restart
# settings of server_settings.py), one an install may opt to manage there (MANAGED, below), or one of these: what the container itself needs to start,
# or a fact of how it's built and deployed, each with why it can't be a setting. tests/test_env_audit.py reads the
# code for every variable read and fails on one that's neither; deploy/check.py lets the templates set only these.
_MOUNT = "a folder inside the container, which the template mounts there: moving it is the template's"
_FILE = "a data file shipped in the image (the tests point it at their own)"
_BIND = ("where the server listens inside its container: nginx, the healthcheck and the all-in-one container are built "
         "around it, so a change on the page would cut the app off")
CONTAINER = {
    "PUID": "the user the container runs as, and owns its files with: set before anything starts",
    "PGID": "the group the container runs as: set before anything starts",
    "SANGUITY_DATA": "the data folder: the settings live in it, so it can't be one of them",
    "SANGUITY_KEY_FILE": "the encryption key file: needed to read the database the settings are in",
    "SANGUITY_AUTH": "the folder of the key and the receiver's secret (setup, the all-in-one container): " + _MOUNT,
    "SANGUITY_BUILD": "where setup writes the built data: " + _MOUNT,
    "SANGUITY_RECORDS": "the record exports: " + _MOUNT,
    "SANGUITY_LEGACY_WWW": "an older install's www/ (documents in www/files*/): " + _MOUNT,
    "SANGUITY_WATCH_FILE": "the receiver's daily summary: " + _MOUNT,
    "SANGUITY_WATCH_KEYS": "the receiver's secrets, written by the app: " + _MOUNT,
    "SANGUITY_WATCH_STORE": "the receiver's whole folder, read-only, for backups: " + _MOUNT,
    "SANGUITY_WATCH": "the receiver's folder, as setup and the all-in-one container see it: " + _MOUNT,
    "SANGUITY_BACKUPS": "where backups are written: " + _MOUNT,
    "SANGUITY_CONFIG": "sources.json, settings.json and corrections.json, read by setup before the app starts: " + _MOUNT,
    "SANGUITY_APP": "where setup finds the app's code: " + _MOUNT,
    "SANGUITY_DEPLOYMENT": "which template started it, set by the template itself (About, Restart now's question)",
    "SANGUITY_IMAGE_VERSION": "the image's own version, baked in as it's built (a release's .env picks the image by "
                              "its tag, SANGUITY_VERSION, which the app never reads)",
    "SANGUITY_SERVE_STATIC": "a server run without nginx (development) serves www/ itself",
    "SANGUITY_TESSERACT": "the text-recognition program, as installed in the image",
    "SANGUITY_TESSDATA": "the text-recognition program's language data, as installed in the image",
    "SANGUITY_CORRECTIONS": _FILE, "SANGUITY_LAB_TEST_PAGES": _FILE, "SANGUITY_CONDITION_PRESETS": _FILE,
    "SANGUITY_LAB_ALIASES": _FILE, "SANGUITY_DIRECTORY": _FILE,
    "SANGUITY_SAVED_SETTINGS": "the way back in: off leaves the values saved in the app out while it's set; read at "
                               "each use, never taken in, as it must work when the page can't be reached",
    "CONNECT_ALLOW_HTTP": "tests only: a plain-HTTP connected source on this machine, never a real vendor",
    "HOST": _BIND, "PORT": _BIND,
    "RECEIVER_PORT": "the receiver's published port (compose's ports:), which the app can't see, given in the phone's "
                     "upload address: the port mapping is the container's",
    "PATH": "the program search path, passed on to the text-recognition program",
    "DATA_DIR": "server/directory/build.py, a tool run by hand: the folder it writes the provider directory to",
    "DATA": "the receiver's folder inside its container: " + _MOUNT,
    "SECRET_FILE": "the receiver's first secret (from before #46): " + _MOUNT,
    "KEYS_FILE": "the receiver's secrets, written by the app: " + _MOUNT,
}
# (#1158, the owner's decision: env-managed, opt-in) settings an install may choose to keep in its environment, as a
# secret manager or Docker secrets do: while one is set (NAME, or NAME_FILE: the path of a file holding it, read at each
# use, so a rotated secret applies), it wins and the page shows its row locked, "Managed by the environment"; unset,
# the page owns it like any other. Never taken in. Each with why it's offered. Read through managed().
_SSO = "the sign-in provider, kept with the identity provider's own configuration where an install manages that as code"
MANAGED = {
    "OIDC_ISSUER": "Sign-in provider's issuer: " + _SSO,
    "OIDC_CLIENT_ID": "Sign-in provider's client ID: " + _SSO,
    "OIDC_CLIENT_SECRET": "Sign-in provider's client secret: a secret a secret manager rotates (or OIDC_CLIENT_SECRET_FILE, "
                          "a Docker secret)",
    "OIDC_SCOPES": "Sign-in provider's scopes: " + _SSO,
    "OIDC_BUTTON": "Sign-in provider's button name: " + _SSO,
    "OIDC_ACR": "Sign-in provider's acr that counts as two factors: " + _SSO,
    "OIDC_ONLY": "Sign in with OIDC only: " + _SSO + " (taken out, the page's switch again)",
    "NOTIFY_TOKEN": "Notifications' access token: a secret a secret manager rotates (or NOTIFY_TOKEN_FILE)",
    # (#1159) the outbound proxy's password joins here, as OUTBOUND_PROXY_PASSWORD (and _FILE), read with managed()
}
_managed_warned = set()


def managed(name):
    """(#1158) The value the environment manages for name (one of MANAGED), or None: NAME's text, else the contents of
    the file NAME_FILE names (a Docker secret), read at each use; stripped. A file that can't be read is None, said once
    (its path, never its contents)."""
    text = os.environ.get(name, "").strip()
    if text:
        return text
    path = os.environ.get(name + "_FILE", "").strip()
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip() or None
    except (OSError, ValueError) as e:
        if (name, path) not in _managed_warned:
            _managed_warned.add((name, path))
            print(f"{name}_FILE names {path}, which can't be read ({type(e).__name__}): it's as if unset.", flush=True)
        return None


def managed_by(name):
    """The variable that manages name now (NAME or NAME_FILE), or None."""
    if os.environ.get(name, "").strip():
        return name
    return name + "_FILE" if managed(name) is not None else None


# ---------------------------------------------------------------- the settings that tune the server (#999, #1158)
# Each is read where it's used, through setting(NAME), so a change applies at once. The settings file is the one source
# of truth, as Sonarr's and Radarr's (#1158): the value an admin saved in the app (an install setting of settings.py's
# registry that stands for it: define(..., env=NAME) sets Var.setting), or, for one of RESTART, the one changed on the
# page's Needs a restart as this process started (server_settings.py), else the default below. The environment no
# longer sets any of them: on the first start of this version each one it set was taken into the settings once
# (settings.take_environment, server_settings.take_environment), and a later change to it is ignored with a warning
# naming it. The container's own (CONTAINER) are the environment's alone. source(NAME) says where a value comes from.
# With SANGUITY_SAVED_SETTINGS=off, the values saved in the app are left out (#1006): the way back in when one locks
# everyone out (python -m server.manage config reset NAME, or config set NAME VALUE, too).
#
# Adding one: a var() line below, then setting("NAME") where it's used, and a home on the page: define() it in
# settings.py with scope="install", private=False and env="NAME" (applies at once), or in server_settings.py and
# RESTART (read as the server starts). tests/test_env_audit.py fails on a variable that has neither.

class Var:
    def __init__(self, name, default, read, keep, about):
        self.name, self.default, self.read, self.keep, self.about = name, default, read, keep, about
        self.setting = None   # the key of the registry's install setting that stands for it, if one does


VARS = {}   # name -> Var
# (#1121) the variables changed on the page's Needs a restart (server_settings.py defines them; a test keeps the two the
# same): kept in data/server-settings.json and read as the server starts, before the default
RESTART = {"UPLOAD_MAX_FILE_MB", "UPLOAD_MAX_TOTAL_MB", "UPLOAD_MAX_FILES", "UPLOAD_MAX_ZIP_RATIO", "JOURNAL_PHOTO_MAX_MB", "SYNC_HOURS",
           "SYNC_MAX_RESOURCES", "SYNC_MAX_ATTACHMENT_MB", "CONNECT_TIMEOUT", "SHL_MAX_MB", "SHL_MAX_FILES", "SHL_TIMEOUT",
           "HASH_PARALLEL", "RESTORE_KEEP_DAYS", "OIDC_TIMEOUT", "NOTIFY_TIMEOUT", "DIRECTORY_TIMEOUT", "DOWNLOAD_TIMEOUT"}


def var(name, default, read=int, keep=None, about=""):
    """read: the variable's text -> its value (ValueError: unreadable, so not taken in); keep: a saved value -> the same
    kind of value (the registry has checked it)."""
    VARS[name] = Var(name, default, read, keep or (lambda v: v), about)


def _origins(items):
    out = []
    for o in items:
        o = str(o).strip().lower().rstrip("/")
        if o and o not in out:
            out.append(o)
    return tuple(out)


def _yes(text):
    v = text.lower()
    if v not in ("1", "on", "yes", "true", "0", "off", "no", "false"):
        raise ValueError(text)
    return v in ("1", "on", "yes", "true")


def _days(text):
    v = float(text)
    if not 0 <= v < 1e6:   # (NaN isn't; nor is inf, which would never be reached)
        raise ValueError(text)
    return v


var("SESSION_DAYS", 30, about="a session ends this long after sign-in...")
var("SESSION_HOURS_WITHOUT_2FA", 12, about="...or this many hours after it, while the account has no second factor "
                                           "(0 = SESSION_DAYS for everyone)")
var("SESSION_IDLE_MINUTES", 0, about="also end it after this long unused (0 = off)")
def _cookie(text):
    v = text.strip().lower()
    if v in ("auto", "always", "never"):
        return v
    return "always" if _yes(v) else "never"


var("COOKIE_SECURE", "auto", _cookie, about="auto = Secure when the request came over HTTPS; always (1), never (0)")
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
                              "(security.py's semaphore): changed on the page's Needs a restart (#1121)")
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
var("CONNECTED_SOURCES", False, _yes, bool, about="connected sources at all (#1096): experimental, off by default; on, "
                                                "Sources offers portals and health plans, Find your provider and its "
                                                "directory, and connections sync (1 or 0)")
var("SYNC_HOURS", 12, about="how often each connection pulls what's new")
var("SYNC_MAX_RESOURCES", 20000, about="the most one pull fetches; the rest come next time")
var("SYNC_MAX_ATTACHMENT_MB", 50, about="a bigger attachment is left out, with a warning; the Sync defaults' ceiling")
var("CONNECT_TIMEOUT", 30, about="seconds to wait for a vendor's answer")
# SMART Health Links (shl.py): the one fetch a pasted link makes, after the person confirms. Everything it brings
# (the manifest and every file in it) counts toward SHL_MAX_MB; each request gives up after SHL_TIMEOUT seconds.
var("SHL_MAX_MB", 64)
var("SHL_MAX_FILES", 50)
var("SHL_TIMEOUT", 20)
# (#1158) how long the server waits for another server, each changed under Needs a restart's Timeouts (with
# CONNECT_TIMEOUT and SHL_TIMEOUT above)
var("OIDC_TIMEOUT", 15, about="seconds to wait for the sign-in provider (its discovery, keys and token)")
var("NOTIFY_TIMEOUT", 10, about="seconds to wait for the notification server")
var("DIRECTORY_TIMEOUT", 60, about="seconds a provider directory list's host may go silent")
var("DOWNLOAD_TIMEOUT", 60, about="seconds a text-recognition language file's download may go silent")
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
    """(where it comes from, the value in force) of the variable name: ("install" | "file" | "default", value): "file"
    for one of RESTART changed on the page, as this process started (#1121); "environment" only for one of CONTAINER
    (RECEIVER_PORT), which the container sets."""
    v = VARS[name]
    if name in CONTAINER:
        text = os.environ.get(v.name, "").strip()
        if text:
            try:
                return "environment", v.read(text)
            except ValueError:   # unreadable: as if it weren't set (unreadable() says so at start)
                pass
        return "default", v.default
    if v.setting and saved is not None and not saved_ignored():
        have = saved()
        if v.setting in have:
            return "install", v.keep(have[v.setting])
    if name in RESTART:   # (#1121) one changed on the page that needs a restart: the file's, as this process started
        from . import server_settings
        have = server_settings.in_force()
        if name in have:
            return "file", have[name]
    return "default", v.default


def setting(name):
    """The value in force of the variable name (a key of VARS): the one saved in the app, else the default."""
    return lookup(name)[1]


def source(name):
    """Where setting(name) comes from: "install" (saved in the app), "file" (#1121), "default", or "environment" for
    one of CONTAINER."""
    return lookup(name)[0]


def fingerprint(text):
    """What's kept of a variable's text once it's been taken in (#1158): enough to tell it changed, never the text (a
    secret's included)."""
    import hashlib
    return hashlib.sha256(text.strip().encode()).hexdigest()[:24]


def environment_changed(name, where):
    """The warning for a variable the environment sets again, or differently, after it was taken in (#1158)."""
    return (f"{name} is set in the environment, but the settings file is where it's kept since it was taken in once: "
            f"this change is ignored. Change it on {where}, or with python -m server.manage config set {name} VALUE; "
            f"then take {name} out of the environment (.env, or the app's settings where it's deployed).")


def public_address(origins=None):
    """The address the app gives out (#1009): share links, calendar feed addresses and their QR codes, the link back in
    each calendar event, and connected sources' return address. The first https:// one of PUBLIC_ORIGINS (in order of
    their names, as the return address has always been taken), else the first; None while it lists none: then the
    address a page was opened at. origins: another list than the one in force (General → Host's change, #1000)."""
    origins = sorted(setting("PUBLIC_ORIGINS") if origins is None else origins, key=lambda o: (not o.startswith("https://"), o))
    return origins[0] if origins else None


def given_base():
    """{"base": public_address() + "/"} for an answer that gives out an address the page makes from a path (#1009),
    or {} with none set (the page uses its own address, as before)."""
    a = public_address()
    return {"base": a + "/"} if a else {}


def unreadable():
    """[(name, text)] of the container's variables set to something they can't be read as (left out, as if unset)."""
    out = []
    for v in (v for n, v in VARS.items() if n in CONTAINER):
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
