"""Backups of everything the app keeps in DATA_DIR, and restoring them.

A backup is one archive, sanguity-backup-YYYYMMDD-HHMMSS.tar.gz:
  sanguity.db      a consistent copy made with SQLite's online backup API, so the app can keep running: accounts,
                   records, settings
  profiles/...     each profile's documents and uploads
  build/...        the setup build (what linked profiles are loaded from)
  manifest.json    format, time, database schema version, the SHA-256 of every file above, and the number of rows
                   in each of the database's tables (from #200; older backups have no counts)

Each file is read once and hashed from those same bytes, so the manifest always matches what was archived. Not in
it (back these up yourself, see the README): the record exports themselves, the watch receiver's folder, auth/
and your .env and *.json configuration.

check() is the Admin page's "Check a backup" (#200): it restores a backup into a temporary folder beside it, never
into DATA_DIR, compares what came out with the manifest, and deletes the copy. backup_jobs.py runs it, and create(),
in the background.

Restoring needs the app stopped (it holds a lock on DATA_DIR/app.lock while running). The data it replaces is
moved to DATA_DIR/before-restore-<time>/, never deleted, and an older backup's database is upgraded on the next start.
"""
import contextlib, datetime, glob, hashlib, io, json, os, re, shutil, sqlite3, tarfile, tempfile

from . import config, crypto, db as dbm

FORMAT = 1
NAME = re.compile(r"^sanguity-backup-\d{8}-\d{6}\.tar\.gz$")
CONTENTS = ("profiles", "build")   # folders under DATA_DIR, besides the database
LOCK_FILE = os.path.join(config.DATA_DIR, "app.lock")
SNAPSHOT, CHECKING = ".sanguity-snapshot-", ".sanguity-check-"   # working folders in the backups folder, gone when done


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

def _tables(con):
    """{table: number of rows} for every table of a database."""
    names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {n: con.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def new_name(dest=None, now=None):
    """The name the next backup in dest gets: its time, a second later while that name is taken."""
    dest = dest or config.BACKUP_DIR
    now = now or datetime.datetime.now()
    while os.path.exists(os.path.join(dest, f"sanguity-backup-{now:%Y%m%d-%H%M%S}.tar.gz")):
        now += datetime.timedelta(seconds=1)
    return f"sanguity-backup-{now:%Y%m%d-%H%M%S}.tar.gz"


def made_at(path):
    """When a backup was made (epoch seconds), from its name: the time it was written, in this machine's time zone."""
    m = re.search(r"(\d{8}-\d{6})", os.path.basename(path))
    return int(datetime.datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").timestamp())


def create(dest=None, keep=None, name=None, progress=None):
    """Write a backup into dest (default BACKUP_DIR) and return its path; with keep, delete all but the newest
    keep backups there. name: the file's name (new_name()); progress(done, total): bytes archived so far."""
    dest = dest or config.BACKUP_DIR
    os.makedirs(dest, exist_ok=True)
    if not os.path.isfile(config.DB_PATH):
        raise BackupError(f"There is no database at {config.DB_PATH} yet: nothing to back up.")
    name = name or new_name(dest)
    if not NAME.match(name):
        raise BackupError(f"{name} isn't a backup's name.")
    now = datetime.datetime.fromtimestamp(made_at(name))
    final = os.path.join(dest, name)
    part = final + ".part"
    hashes, counts, done = {}, {}, [0]
    files = [(root, n) for top in CONTENTS for root, dirs, names in sorted(os.walk(os.path.join(config.DATA_DIR, top)))
             for n in sorted(names) if not n.endswith(".tmp")]
    total = _size(config.DB_PATH) + sum(_size(os.path.join(r, n)) for r, n in files)

    def tick(n):   # n more bytes archived
        done[0] += n
        if progress:
            progress(done[0], max(total, done[0]))
    fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # health data: readable by the app's user only
    try:
        with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as tar:
            def add(name, data):
                hashes[name] = hashlib.sha256(data).hexdigest()
                info = tarfile.TarInfo(name)
                info.size, info.mtime, info.mode = len(data), int(now.timestamp()), 0o600
                tar.addfile(info, io.BytesIO(data))
                tick(len(data))

            def add_file(name, f):
                """A file a piece at a time (an upload can be gigabytes): hashed, then archived from the same open
                file. Sanguity replaces files whole (os.replace), never in place, so both passes read the same bytes."""
                h, size = hashlib.sha256(), 0
                while piece := f.read(1 << 20):
                    h.update(piece)
                    size += len(piece)
                f.seek(0)
                hashes[name] = h.hexdigest()
                info = tarfile.TarInfo(name)
                info.size, info.mtime, info.mode = size, int(now.timestamp()), 0o600
                tar.addfile(info, f)
                tick(size)

            with tempfile.TemporaryDirectory(prefix=SNAPSHOT, dir=dest) as work:   # the database snapshot, then into the archive
                copy = os.path.join(work, "sanguity.db")
                src, dst = sqlite3.connect(config.DB_PATH), sqlite3.connect(copy)
                try:
                    src.backup(dst)
                    schema = dst.execute("PRAGMA user_version").fetchone()[0]
                    counts = _tables(dst)   # what a check of this backup expects to find (#200)
                finally:
                    dst.close()
                    src.close()
                with open(copy, "rb") as f:
                    add("sanguity.db", f.read())
            for root, n in files:   # .tmp left out: a file being replaced right now; the finished one is there already
                path = os.path.join(root, n)
                try:
                    f = open(path, "rb")   # one open file: the hash is of exactly what goes in
                except FileNotFoundError:
                    continue   # removed since the list was made (an upload cleaned up)
                with f:
                    add_file(os.path.relpath(path, config.DATA_DIR).replace(os.sep, "/"), f)
            add("manifest.json", json.dumps({"format": FORMAT, "created": now.isoformat(timespec="seconds"),
                                             "schema": schema, "files": hashes, "counts": counts}, indent=1).encode())
        os.replace(part, final)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.remove(part)
        raise
    if keep:
        prune(keep, dest)
    return final


def prune(keep, dest=None):
    """Delete all but the newest keep backups in dest; returns the names removed. Only files named as Sanguity names
    its backups are counted or removed: anything else in the folder is left alone."""
    gone = []
    for old in list_backups(dest)[:-keep] if keep and keep > 0 else ():
        with contextlib.suppress(FileNotFoundError):
            os.remove(old)
            gone.append(os.path.basename(old))
    return gone


def list_backups(dest=None):
    """Backups in dest, oldest first."""
    dest = dest or config.BACKUP_DIR
    return sorted(p for p in glob.glob(os.path.join(dest, "sanguity-backup-*.tar.gz")) if NAME.match(os.path.basename(p)))


def clean_leftovers(dest=None):
    """At start-up: what a stop in the middle of a backup or a check left in the backups folder (a .part archive,
    a database snapshot, a check's restored copy). Only Sanguity's own names are touched. Returns how many."""
    dest = dest or config.BACKUP_DIR
    n = 0
    for p in glob.glob(os.path.join(dest, "sanguity-backup-*.tar.gz.part")):
        if NAME.match(os.path.basename(p)[:-5]):
            with contextlib.suppress(FileNotFoundError):
                os.remove(p)
                n += 1
    for prefix in (SNAPSHOT, CHECKING):
        for p in glob.glob(os.path.join(dest, prefix + "*")):
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p, ignore_errors=True)
                n += 1
    return n


# ---------------------------------------------------------------- checking and restoring

def _safe_name(name):
    parts = name.split("/")
    return (name and not name.startswith("/") and ".." not in parts and "" not in parts
            and (name in ("sanguity.db", "manifest.json") or parts[0] in CONTENTS))


class _reading:
    """The archive opened for reading, with progress(done, total) told how far through the file it is."""
    def __init__(self, archive, progress):
        self.f, self.total, self.progress = open(archive, "rb"), os.path.getsize(archive), progress

    def read(self, n=-1):
        piece = self.f.read(n)
        if self.progress:
            self.progress(self.f.tell(), self.total)
        return piece

    def __getattr__(self, name):   # seek, tell, close...: the file's own
        return getattr(self.f, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.f.close()


def verify(archive, progress=None):
    """The archive's manifest, after checking that every file is there, unchanged, and nothing else is."""
    try:
        with _reading(archive, progress) as raw, tarfile.open(fileobj=raw, mode="r:gz") as tar:
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
                h, src = hashlib.sha256(), tar.extractfile(members[name])
                while piece := src.read(1 << 20):
                    h.update(piece)
                if h.hexdigest() != digest:
                    raise BackupError(f"{name} doesn't match its checksum: the backup is damaged.")
            if "sanguity.db" not in files:
                raise BackupError("The backup has no database.")
            return manifest
    except (tarfile.TarError, OSError, EOFError, ValueError) as e:
        raise BackupError(f"Can't read {archive}: {e}") from None


def _opens(head, src):
    """Whether this install's key opens an encrypted file that starts with head (4 bytes; the rest in src): raises
    crypto.KeyError_ if not. Returns False for a file that isn't encrypted."""
    if head != crypto.STREAM and head != crypto.MAGIC:
        return False
    crypto.keyring.load()
    if head == crypto.STREAM:   # an upload's file: its first chunk is enough (and a byte of the next,
        rest = src.read(15)     # if there is one, so it isn't taken for the last)
        first = src.read(int.from_bytes(rest[11:15], "big") + 16 + 1)
        next(crypto._stream_chunks(io.BytesIO(head + rest + first)))
    else:
        crypto.unseal(head + src.read())
    return True


def _check_key(archive):
    """Refuse a backup this install's key can't open, before anything is replaced."""
    with tarfile.open(archive, "r:gz") as tar:
        for m in tar.getmembers():
            if m.name in ("manifest.json", "sanguity.db"):
                continue
            src = tar.extractfile(m)
            try:
                if _opens(src.read(4), src):
                    return
            except crypto.KeyError_ as e:
                raise BackupError(f"This backup can't be read with this install's encryption key ({e}) It was "
                                  f"made with a different key: put that key file at {config.KEY_FILE} "
                                  "(or add its line to the key file), then restore again.") from None


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


# ---------------------------------------------------------------- checking a backup by restoring a copy (#200)

FRIENDLY = {"users": "accounts", "profiles": "profiles", "events": "records", "results": "lab results",
            "documents": "documents", "imports": "imports"}   # the tables a check names in words


def _live_counts():
    """The same tables' rows in the live database, opened read-only: shown beside the backup's, for comparison
    only (it has changed since)."""
    try:
        con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    try:
        return {t: n for t, n in _tables(con).items() if t in FRIENDLY}
    except sqlite3.Error:
        return {}
    finally:
        con.close()


def check(archive, progress=None):
    """Restore a backup into a temporary folder beside it, check what came out, then delete the copy. It never
    touches DATA_DIR: the copy is made in the backups folder and its database is opened there; the live database
    is only read, to show its counts beside the backup's. Returns {"ok", "problems": [plain sentences], "created",
    "schema", "files", "counts": {table: {"backup": rows, "expected": rows the manifest says}}, "now": {table: rows
    now}}. progress(stage, done, total), stage "checksums", "restoring" or "database"."""
    say = progress or (lambda *a: None)
    out = {"ok": False, "problems": [], "created": None, "schema": None, "files": 0, "counts": {}, "now": {}}

    def bad(why):
        out["problems"].append(why)
        return out

    try:   # 1. every file there and unchanged (the archive's own checksums)
        manifest = verify(archive, lambda d, t: say("checksums", d, t))
    except BackupError as e:
        return bad(str(e))
    out.update(created=manifest.get("created"), schema=manifest.get("schema"), files=len(manifest["files"]))
    if (manifest.get("schema") or 0) > len(dbm.SCHEMA):
        return bad("This backup was made by a newer version of Sanguity: this version can't restore it.")
    work = tempfile.mkdtemp(prefix=CHECKING, dir=os.path.dirname(os.path.abspath(archive)))
    try:
        # 2. restored into the temporary folder, as restore() would into DATA_DIR, each file checked again on disk
        with _reading(archive, lambda d, t: say("restoring", d, t)) as raw, tarfile.open(fileobj=raw, mode="r:gz") as tar:
            for m in tar:
                if m.name == "manifest.json":
                    continue
                target = os.path.join(work, *m.name.split("/"))   # names verify() checked: inside work
                os.makedirs(os.path.dirname(target), exist_ok=True)
                h = hashlib.sha256()
                with tar.extractfile(m) as src, open(target, "wb") as f:
                    while piece := src.read(1 << 20):
                        h.update(piece)
                        f.write(piece)
                if h.hexdigest() != manifest["files"].get(m.name):
                    bad(f"{m.name} came out of the backup different from its checksum.")
        if out["problems"]:
            return out
        # 3. this install's key opens it: every encrypted file's key is in the key file, one file opens, and so
        # does the database's pepper (passwords are checked with it)
        say("database", 0, 1)
        _, keys = crypto.keyring.load()
        opened = False
        for root, dirs, names in os.walk(work):
            for n in sorted(names):
                path = os.path.join(root, n)
                if path == os.path.join(work, "sanguity.db"):
                    continue
                with open(path, "rb") as f:
                    head = f.read(8)
                    if head[:4] not in (crypto.MAGIC, crypto.STREAM):
                        continue
                    if head[4:8] not in keys:
                        return bad(f"{os.path.relpath(path, work)} was encrypted with a key that isn't in this install's "
                                   "key file: the backup can't be read without that key.")
                    if not opened:
                        f.seek(4)
                        try:
                            opened = _opens(head[:4], f)
                        except crypto.KeyError_ as e:
                            return bad(f"This install's encryption key doesn't open the backup's files ({e})")
        # 4. the database: intact, the version its manifest says, and the rows it had when the backup was made
        try:
            con = sqlite3.connect(os.path.join(work, "sanguity.db"))
            try:
                ok = con.execute("PRAGMA integrity_check").fetchone()[0]
                if ok != "ok":
                    return bad(f"The backup's database is damaged ({ok}).")
                if con.execute("PRAGMA user_version").fetchone()[0] != manifest.get("schema"):
                    return bad("The backup's database isn't the version its manifest says.")
                got = _tables(con)
                pepper = con.execute("SELECT v FROM meta WHERE k='pepper'").fetchone() if "meta" in got else None
            finally:
                con.close()
        except sqlite3.DatabaseError as e:
            return bad(f"The backup's database can't be read ({e}).")
        if pepper and pepper[0]:
            try:
                crypto.dec(pepper[0])
            except crypto.KeyError_ as e:
                return bad(f"This install's encryption key doesn't open the backup's database ({e})")
        expected = manifest.get("counts")
        out["counts"] = {t: {"backup": n, "expected": (expected or {}).get(t)} for t, n in got.items()}
        if expected is not None:
            for t in sorted(set(got) | set(expected)):
                if got.get(t) != expected.get(t):
                    bad(f"The backup's database has {got.get(t, 0)} rows in {FRIENDLY.get(t, t)}; it had "
                        f"{expected.get(t, 0)} when the backup was made.")
        out["now"] = _live_counts()
        out["ok"] = not out["problems"]
        say("database", 1, 1)
        return out
    except (OSError, tarfile.TarError, EOFError, ValueError) as e:
        return bad(f"The backup couldn't be restored into a temporary folder: {e}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
