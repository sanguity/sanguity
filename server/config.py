"""Settings from the environment. Defaults are the container paths set up by compose.yaml."""
import os


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


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

SESSION_DAYS = _int("SESSION_DAYS", 30)                 # a session ends this long after sign-in...
# ...or this many hours after it, while the account has no second factor (0 = SESSION_DAYS for everyone)
SESSION_HOURS_WITHOUT_2FA = _int("SESSION_HOURS_WITHOUT_2FA", 12)
SESSION_IDLE_MINUTES = _int("SESSION_IDLE_MINUTES", 0)  # also end it after this long unused (0 = off)
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "auto").lower()   # auto = Secure when the request came over HTTPS
COOKIE = "sg_session"
# over HTTPS the session cookie is named with the __Host- prefix, so the browser holds it to Secure, Path=/ and
# this exact host (no subdomain can set or overwrite it); plain HTTP can't carry that prefix, so it keeps COOKIE
COOKIE_HOST = "__Host-" + COOKIE
TEMP_PASSWORD_DAYS = _int("TEMP_PASSWORD_DAYS", 7)   # a one-time password from an admin stops working after this
# the fresh check before an admin resets a password, turns off someone's two-factor sign-in or makes someone an
# admin: the password, a code or a passkey within this many minutes (signing in counts)
REAUTH_MINUTES = _int("REAUTH_MINUTES", 5)
DEVICE_COOKIE, DEVICE_DAYS = "sg_device", 365   # marks a browser that has signed in to an account before
# Addresses people open Sanguity at, when a reverse proxy in front changes the Host header and doesn't send
# X-Forwarded-Host, e.g. "https://health.example.com" (comma-separated)
PUBLIC_ORIGINS = {o.strip().lower().rstrip("/") for o in os.environ.get("PUBLIC_ORIGINS", "").split(",") if o.strip()}

# NIST SP 800-63B-4: at least 15 characters while a password is the only factor; no composition rules
PASSWORD_MIN = _int("PASSWORD_MIN_LENGTH", 15)
PASSWORD_MAX = 256
PASSWORD_MIN_2FA = _int("PASSWORD_MIN_LENGTH_2FA", 8)   # with two-factor sign-in on (NIST SP 800-63B)
HASH_PARALLEL = _int("HASH_PARALLEL", 2)   # password hashes run at once (64 MiB each); more wait their turn
HASH_WAIT = 2
ACTIVITY_DAYS = _int("ACTIVITY_DAYS", 365)   # how long the activity log keeps entries   # seconds a sign-in waits for its turn before the API answers 503 "busy, try again"

# uploads from the import page (uploads.py). A file is one the browser sent or one unpacked from a zip; an upload's
# total counts what it holds after unpacking. nginx.conf caps one file sent at 4 GB too: raise both to go higher.
MB = 2**20
UPLOAD_MAX_FILE = _int("UPLOAD_MAX_FILE_MB", 4096) * MB
UPLOAD_MAX_TOTAL = _int("UPLOAD_MAX_TOTAL_MB", 16384) * MB
UPLOAD_MAX_FILES = _int("UPLOAD_MAX_FILES", 20000)
# a file unpacked from a zip may be at most this many times its compressed size (a zip bomb is thousands)
UPLOAD_MAX_ZIP_RATIO = _int("UPLOAD_MAX_ZIP_RATIO", 200)
