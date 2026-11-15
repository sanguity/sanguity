"""Backups of everything the app keeps in DATA_DIR, and restoring them.

A backup is one archive, sanguity-backup-YYYYMMDD-HHMMSS.tar.gz:
  sanguity.db      a consistent copy made with SQLite's online backup API, so the app can keep running
  profiles/...     each profile's records and settings
  build/...        the setup build (what linked profiles are loaded from)
  manifest.json    format, time, database schema version, and the SHA-256 of every file above

Each file is read once and hashed from those same bytes, so the manifest always matches what was archived. Not in
it (back these up yourself, see the README): the record exports themselves, the watch receiver's folder, auth/
and your .env and *.json configuration.

Restoring needs the app stopped (it holds a lock on DATA_DIR/app.lock while running). The data it replaces is
moved to DATA_DIR/before-restore-<time>/, never deleted, and an older backup's database is upgraded on the next start.
"""
import contextlib, datetime, glob, hashlib, io, json, os, re, shutil, sqlite3, tarfile, tempfile

from . import config, crypto, db as dbm

FORMAT = 1
NAME = re.compile(r"^sanguity-backup-\d{8}-\d{6}\.tar\.gz$")
CONTENTS = ("profiles", "build")   # folders under DATA_DIR, besides the database
LOCK_FILE = os.path.join(config.DATA_DIR, "app.lock")


class BackupError(Exception):
    pass


# ---------------------------------------------------------------- the running app's lock

try:
    import fcntl
except ImportError:   # Windows (development only): no lock, so restore can't tell whether the app is running
    fcntl = None


@contextlib.contextmanager
def app_running_lock():
    """Held (shared) by the app while it runs. Several app processes can hold it; restore needs it exclusively."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    f = open(LOCK_FILE, "a")
    try:
        if fcntl:
            fcntl.flock(f, fcntl.LOCK_SH)
        yield
    finally:
        f.close()   # closing releases the lock


def _app_is_running():
    if not fcntl or not os.path.exists(LOCK_FILE):
        return False
    with open(LOCK_FILE, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False


# ---------------------------------------------------------------- making a backup

def create(dest=None, keep=None):
    """Write a backup into dest (default BACKUP_DIR) and return its path; with keep, delete all but the newest
    keep backups there."""
    dest = dest or config.BACKUP_DIR
    os.makedirs(dest, exist_ok=True)
    if not os.path.isfile(config.DB_PATH):
        raise BackupError(f"There is no database at {config.DB_PATH} yet: nothing to back up.")
    now = datetime.datetime.now()
    final = os.path.join(dest, f"sanguity-backup-{now:%Y%m%d-%H%M%S}.tar.gz")
    part = final + ".part"
    hashes = {}
    fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # health data: readable by the app's user only
    try:
        with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as tar:
            def add(name, data):
                hashes[name] = hashlib.sha256(data).hexdigest()
                info = tarfile.TarInfo(name)
                info.size, info.mtime, info.mode = len(data), int(now.timestamp()), 0o600
                tar.addfile(info, io.BytesIO(data))

            with tempfile.TemporaryDirectory(dir=dest) as work:   # the database snapshot, then into the archive
                copy = os.path.join(work, "sanguity.db")
                src, dst = sqlite3.connect(config.DB_PATH), sqlite3.connect(copy)
                try:
                    src.backup(dst)
                    schema = dst.execute("PRAGMA user_version").fetchone()[0]
                finally:
                    dst.close()
                    src.close()
                with open(copy, "rb") as f:
                    add("sanguity.db", f.read())
            for top in CONTENTS:
                base = os.path.join(config.DATA_DIR, top)
                for root, dirs, files in os.walk(base):
                    dirs.sort()
                    for n in sorted(files):
                        if n.endswith(".tmp"):
                            continue   # a file being replaced right now; the finished one is there already
                        path = os.path.join(root, n)
                        with open(path, "rb") as f:   # one read: the hash is of exactly what goes in
                            add(os.path.relpath(path, config.DATA_DIR).replace(os.sep, "/"), f.read())
            add("manifest.json", json.dumps({"format": FORMAT, "created": now.isoformat(timespec="seconds"),
                                             "schema": schema, "files": hashes}, indent=1).encode())
        os.replace(part, final)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.remove(part)
        raise
    if keep:
        for old in list_backups(dest)[:-keep]:
            os.remove(old)
    return final


def list_backups(dest=None):
    """Backups in dest, oldest first."""
    dest = dest or config.BACKUP_DIR
    return sorted(p for p in glob.glob(os.path.join(dest, "sanguity-backup-*.tar.gz")) if NAME.match(os.path.basename(p)))


# ---------------------------------------------------------------- checking and restoring

def _safe_name(name):
    parts = name.split("/")
    return (name and not name.startswith("/") and ".." not in parts and "" not in parts
            and (name in ("sanguity.db", "manifest.json") or parts[0] in CONTENTS))


def verify(archive):
    """The archive's manifest, after checking that every file is there, unchanged, and nothing else is."""
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = {m.name: m for m in tar.getmembers()}
            if "manifest.json" not in members:
                raise BackupError("This isn't a Sanguity backup: it has no manifest.json.")
            manifest = json.load(tar.extractfile(members["manifest.json"]))
            if manifest.get("format") != FORMAT:
                raise BackupError(f"Backup format {manifest.get('format')} isn't one this version reads ({FORMAT}).")
            files = manifest.get("files") or {}
            for name, m in members.items():
                if not m.isfile() or not _safe_name(name):
                    raise BackupError(f"Unexpected entry in the backup: {name}")
                if name != "manifest.json" and name not in files:
                    raise BackupError(f"{name} is in the archive but not in its manifest.")
            for name, digest in files.items():
                if name not in members:
                    raise BackupError(f"{name} is missing from the backup.")
                if hashlib.sha256(tar.extractfile(members[name]).read()).hexdigest() != digest:
                    raise BackupError(f"{name} doesn't match its checksum: the backup is damaged.")
            if "sanguity.db" not in files:
                raise BackupError("The backup has no database.")
            return manifest
    except (tarfile.TarError, OSError, EOFError, ValueError) as e:
        raise BackupError(f"Can't read {archive}: {e}") from None


def _check_key(archive):
    """Refuse a backup this install's key can't open, before anything is replaced."""
    with tarfile.open(archive, "r:gz") as tar:
        for m in tar.getmembers():
            if m.name in ("manifest.json", "sanguity.db"):
                continue
            data = tar.extractfile(m).read()
            if crypto.encrypted(data):
                try:
                    crypto.keyring.load()
                    crypto.unseal(data)
                except crypto.KeyError_ as e:
                    raise BackupError(f"This backup can't be read with this install's encryption key ({e}) It was "
                                      f"made with a different key: put that key file at {config.KEY_FILE} "
                                      "(or add its line to the key file), then restore again.") from None
                return


def restore(archive):
    """Replace DATA_DIR's database, profiles and build with the archive's. Returns (manifest, where the old data went)."""
    if _app_is_running():
        raise BackupError("The app is running. Stop it first (docker compose stop app, or stop the app in TrueNAS), "
                          "then restore with: docker compose run --rm --no-deps app python -m server.manage restore ...")
    manifest = verify(archive)
    if manifest.get("schema", 0) > len(dbm.SCHEMA):
        raise BackupError("This backup was made by a newer version of Sanguity. Update first, then restore.")
    _check_key(archive)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    aside = os.path.join(config.DATA_DIR, f"before-restore-{stamp}")
    os.makedirs(aside)
    for n in ("sanguity.db", "sanguity.db-wal", "sanguity.db-shm", *CONTENTS):
        if os.path.exists(os.path.join(config.DATA_DIR, n)):
            shutil.move(os.path.join(config.DATA_DIR, n), os.path.join(aside, n))
    with tarfile.open(archive, "r:gz") as tar:
        for m in tar.getmembers():
            if m.name == "manifest.json":
                continue
            target = os.path.join(config.DATA_DIR, *m.name.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with tar.extractfile(m) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
    dbm.init()   # an older backup's schema is brought up to date
    return manifest, aside
