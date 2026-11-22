"""Backups of everything the app keeps in DATA_DIR, and of the watch receiver's folder, and restoring them.

A backup is one archive, sanguity-backup-YYYYMMDD-HHMMSS.tar.gz:
  sanguity.db      a consistent copy made with SQLite's online backup API, so the app can keep running: accounts,
                   records, settings
  profiles/...     each profile's documents and uploads
  build/...        the setup build (what linked profiles are loaded from)
  watch/...        the watch receiver's data (#227), from its folder (config.WATCH_STORE, mounted read-only): each
                   profile's database, watch.db or profiles/<slot>/watch.db, and every upload it archived, raw/...
  manifest.json    format, time, database schema version, the SHA-256 of every file above, and the number of rows
                   in each of the database's tables (from #200; older backups have no counts); and for the receiver's
                   data, each database's rows, when the receiver last saved it, and the uploads archived since (#227)

Each file is read once and hashed from those same bytes, so the manifest always matches what was archived. Not in
it (back these up yourself, see the README): the record exports themselves, auth/ and your .env and *.json
configuration.

The receiver's watch.db is already a consistent snapshot: the receiver keeps its database in memory and writes it
whole, sealed with the data key, replacing the file at once (os.replace), so the file is copied as it is, still
sealed, with nothing to lock. Uploads that came after its last save are in raw/, and the receiver folds them in when
it starts. Its public/ summaries are made again from the database, and keys/ again from the app's, so neither is
copied. The app never writes the receiver's folder, apart from keys/ (watch_keys.py), which restore() also uses.

check() is the Admin page's "Check a backup" (#200): it restores a backup into a temporary folder beside it, never
into DATA_DIR, compares what came out with the manifest, and deletes the copy. backup_jobs.py runs it, and create(),
in the background.

Restoring needs the app stopped (it holds a lock on DATA_DIR/app.lock while running). The data it replaces is
moved to DATA_DIR/before-restore-<time>/, never deleted, and an older backup's database is upgraded on the next start.
The receiver's data goes to the receiver's keys/restore/ folder, the one of its folders the app may write; the
receiver moves it in when it next starts, and what it had to before-restore-<time>/ in its own folder.
"""
import contextlib, datetime, glob, hashlib, io, json, logging, os, re, shutil, sqlite3, tarfile, tempfile

from . import config, crypto, db as dbm

log = logging.getLogger("sanguity.backups")

FORMAT = 1
NAME = re.compile(r"^sanguity-backup-\d{8}-\d{6}\.tar\.gz$")
CONTENTS = ("profiles", "build")   # folders under DATA_DIR, besides the database
LOCK_FILE = os.path.join(config.DATA_DIR, "app.lock")
SNAPSHOT, CHECKING = ".sanguity-snapshot-", ".sanguity-check-"   # working folders in the backups folder, gone when done
WATCH = "watch"          # the receiver's data in the archive (#227)
WATCH_STAGE = "restore"   # in the receiver's keys folder: a restore's watch data, moved in when the receiver starts
STAMP = "%Y%m%d%H%M%S%f"   # the receiver's raw/ names and its "saved" mark (watch/receiver.py)
SNAPSHOT_TRIES = 3   # a backup's database snapshot taken again while files it names are removed as they're read (#592)
# the receiver's tables a check names in words, and the rows each counts. Every table is compared; cycle records (#183)
# aren't named: the Admin page is for admins, and only a profile's owners and editors may know they're kept
WATCH_FRIENDLY = {"readings": ("samples", "buckets", "hae_points", "hae_vitals", "glucose"), "sleep": ("sleep", "hae_sleep"),
                  "workouts": ("exercise",), "ecgs": ("ecg",)}


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


# ---------------------------------------------------------------- the watch receiver's folder (#227)

def _watch_slots(store=None):
    """{slot: its folder} of every profile the receiver keeps data for: "" (its top folder) and profiles/<slot>/."""
    store = store if store is not None else config.WATCH_STORE
    if not store or not os.path.isdir(store):
        return {}
    out = {"": store}
    top = os.path.join(store, "profiles")
    for n in sorted(os.listdir(top) if os.path.isdir(top) else (), key=lambda n: int(n) if n.isdigit() else -1):
        if n.isdigit() and os.path.isdir(os.path.join(top, n)):
            out[n] = os.path.join(top, n)
    raw = lambda d: os.path.join(d, "raw")
    return {s: d for s, d in out.items()
            if os.path.isfile(os.path.join(d, "watch.db")) or (os.path.isdir(raw(d)) and os.listdir(raw(d)))}


def _watch_files(store=None):
    """[(name in the archive, path)] of what a backup takes from the receiver's folder: each slot's watch.db, then its
    archived uploads (raw/YYYY/MM/DD/*.json.gz), oldest first. Nothing else (public/, keys/, before-restore-*/)."""
    store = store if store is not None else config.WATCH_STORE
    out = []
    for slot, d in _watch_slots(store).items():
        if os.path.isfile(os.path.join(d, "watch.db")):
            out.append((os.path.join(d, "watch.db"),))
        for root, _, names in sorted(os.walk(os.path.join(d, "raw"))):   # by folder: oldest day first
            out +=[(os.path.join(root, n),) for n in sorted(names) if n.endswith(".json.gz")]
    return [(WATCH + "/" + os.path.relpath(p, store).replace(os.sep, "/"), p) for (p,) in out]


def _watch_slot_of(name):
    """The slot a watch/ name in the archive belongs to: "" for watch/watch.db and watch/raw/..., else the profile's."""
    parts = name.split("/")
    return parts[2] if len(parts) > 3 and parts[1] == "profiles" else ""


def _raw_stamp(name):
    """An archived upload's time, as the receiver's STAMP, from its name (.../raw/YYYY/MM/DD/HHMMSSffffff-....json.gz)."""
    parts = name.replace(os.sep, "/").split("/")
    return "".join(parts[-4:-1]) + parts[-1][:12]


def _open_watch_db(data):
    """A receiver's sealed watch.db, opened in memory (the receiver keeps it the same way)."""
    con = sqlite3.connect(":memory:")
    try:
        con.deserialize(crypto.unseal(data))
    except BaseException:
        con.close()
        raise
    return con


def _iso(stamp):
    try:
        return datetime.datetime.strptime(stamp, STAMP).replace(tzinfo=datetime.timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return None


def _uploads_after(raw, since):
    """How many uploads in a raw/ folder came after since (a STAMP), looking only at the days from then on."""
    n = 0

    def names(d):
        return sorted((x for x in os.listdir(d) if x.isdigit()), reverse=True) if os.path.isdir(d) else []
    for y in names(raw):
        if y < since[:4]:
            break
        for m in names(os.path.join(raw, y)):
            if y + m < since[:6]:
                break
            for d in names(os.path.join(raw, y, m)):
                if y + m + d < since[:8]:
                    break
                n += sum(1 for f in os.listdir(os.path.join(raw, y, m, d))
                         if f.endswith(".json.gz") and y + m + d + f[:12] > since)
    return n


def _watch_files_in(d):
    """A slot folder's archived uploads (names only)."""
    return [n for _, _, ns in os.walk(os.path.join(d, "raw")) for n in ns if n.endswith(".json.gz")]


_saved_cache = {}   # watch.db path -> ((mtime, size), when it was saved): opened again only when it changes


def _saved(db, st):
    """When the receiver last saved a watch.db (its "saved" mark, a STAMP), or None if it can't be read."""
    sig = (st.st_mtime_ns, st.st_size)
    hit = _saved_cache.get(db)
    if hit and hit[0] == sig:
        return hit[1]
    try:
        with open(db, "rb") as f:
            con = _open_watch_db(f.read())
        try:
            saved = _watch_counts(con)[1]
        finally:
            con.close()
    except (OSError, sqlite3.Error, crypto.KeyError_, ValueError):
        saved = None
    _saved_cache[db] = (sig, saved)
    return saved


def _store_visible(store):
    """Whether the app sees the receiver's folder itself: mounted as a whole (compose and the templates since #227,
    the all-in-one container), or with more in it than public/ and keys/, all that compose files from before mount."""
    if not store or not os.path.isdir(store):
        return False
    return os.path.ismount(store) or any(n not in ("public", "keys") for n in os.listdir(store))


def watch_status():
    """What the next backup takes from the receiver's folder, for the Admin page: {"folder", "visible" (the app sees
    the folder itself), "found" (data in it), "profiles", "saved" (epoch: the latest save of any profile's database),
    "after" (uploads newer than their profile's last save, folded in when the receiver starts after a restore),
    "size" (the databases' bytes)}. Each database is opened once after it changes, for its "saved" mark; only the
    days of raw/ since then are listed."""
    slots = _watch_slots()
    out = {"folder": config.WATCH_STORE or None, "visible": bool(slots) or _store_visible(config.WATCH_STORE),
           "found": bool(slots), "profiles": len(slots), "saved": None, "after": 0, "size": 0}
    for d in slots.values():
        db = os.path.join(d, "watch.db")
        try:
            st = os.stat(db)
        except OSError:
            st = None
        since = _saved(db, st) if st else None
        if st:
            out["size"] += st.st_size
        if not since:   # never saved (or unreadable): every upload is still to be folded in
            out["after"] += len(_watch_files_in(d))
            continue
        at = int(datetime.datetime.strptime(since, STAMP).replace(tzinfo=datetime.timezone.utc).timestamp())
        out["saved"] = max(out["saved"] or 0, at)
        out["after"] += _uploads_after(os.path.join(d, "raw"), since)
    return out


def _watch_counts(con):
    """{table: rows} of a receiver's database, and when it was last saved (a STAMP, or None)."""
    counts = _tables(con)
    saved = con.execute("SELECT v FROM meta WHERE k='saved'").fetchone() if "meta" in counts else None
    return counts, saved[0] if saved else None


def _live_watch():
    """Each profile's rows in the receiver's live database, read-only, for the Now column of a check: {slot: {table: rows}}."""
    out = {}
    for slot, d in _watch_slots().items():
        try:
            with open(os.path.join(d, "watch.db"), "rb") as f:
                con = _open_watch_db(f.read())
            try:
                out[slot] = _tables(con)
            finally:
                con.close()
        except (OSError, sqlite3.Error, crypto.KeyError_, ValueError):
            continue
    return out


def watch_words(per_slot, uploads=None):
    """{"readings": rows, "sleep": ..., "uploads": archived uploads} summed over the profiles, from {slot: {table: rows}}."""
    out = {k: sum((c or {}).get(t, 0) for c in per_slot.values() for t in ts) for k, ts in WATCH_FRIENDLY.items()}
    if uploads is not None:
        out["uploads"] = uploads
    return out


def _data_files():
    """{name in the archive: path} of the files a backup takes from DATA_DIR besides the database: everything under
    profiles/ and build/ but a .tmp (a file being replaced right now; the finished one is there already)."""
    out = {}
    for top in CONTENTS:
        for root, dirs, names in os.walk(os.path.join(config.DATA_DIR, top)):
            for n in names:
                if not n.endswith(".tmp"):
                    path = os.path.join(root, n)
                    out[os.path.relpath(path, config.DATA_DIR).replace(os.sep, "/")] = path
    return out


def _snapshot(copy):
    """A consistent copy of the live database at copy (SQLite's online backup, so the app keeps running): returns
    (its schema version, {table: rows})."""
    src, dst = sqlite3.connect(config.DB_PATH), sqlite3.connect(copy)
    try:
        src.backup(dst)
        return dst.execute("PRAGMA user_version").fetchone()[0], _tables(dst)
    finally:
        dst.close()
        src.close()


def _in_data(path):
    """A document's file as a name in the archive (profiles/... or build/...), or None: outside DATA_DIR (the record
    exports, which a backup doesn't take), or not a path at all."""
    if not isinstance(path, str) or not path:
        return None
    for base in {os.path.abspath(config.DATA_DIR), os.path.realpath(config.DATA_DIR)}:
        rel = os.path.relpath(os.path.abspath(path), base)
        parts = rel.replace(os.sep, "/").split("/")
        if parts[0] != ".." and not os.path.isabs(rel) and parts[0] in CONTENTS:
            return "/".join(parts)
    return None


def named_files(con):
    """{name in the archive: kind} of the files a database's rows name (#592): each journal photo's, each finished
    upload's (an import's files, and the documents stored in them), each document's under DATA_DIR. A value this
    install's key doesn't open is passed over (a check says so apart). kind: "photo", "upload" or "document"."""
    have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    out = {}
    if "journal_photos" in have:
        for pid, file in con.execute("SELECT profile_id, file FROM journal_photos"):
            out[f"profiles/{int(pid)}/journal/{file}"] = "photo"
    if "uploads" in have:
        for uid, pid, manifest in con.execute("SELECT id, profile_id, manifest FROM uploads WHERE state='ready'"):
            try:
                entries = json.loads(crypto.dec(manifest)) if manifest else []
            except (crypto.KeyError_, ValueError, TypeError):
                continue
            for e in entries if isinstance(entries, list) else ():
                if isinstance(e, dict) and isinstance(e.get("f"), str):
                    out[f"profiles/{int(pid)}/uploads/{int(uid)}/{e['f']}"] = "upload"
    if "documents" in have:
        for (path,) in con.execute("SELECT path FROM documents"):
            try:
                rel = _in_data(crypto.dec(path))
            except (crypto.KeyError_, ValueError, TypeError):
                continue
            if rel:
                out.setdefault(rel, "document")
    return out


def _missing(db_path, files):
    """The names named_files finds in the database at db_path that aren't among files: {name: kind}."""
    con = sqlite3.connect(db_path)
    try:
        return {n: k for n, k in named_files(con).items() if n not in files}
    finally:
        con.close()


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
    watch = _watch_files()   # the receiver's data (#227); none without a receiver
    total = _size(config.DB_PATH) + sum(_size(p) for p in _data_files().values()) + sum(_size(p) for _, p in watch)

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

            # the database snapshot first, then the files as they are after it (#592): a row is committed after the
            # file it names is written, and a file removed after the row naming it is gone (journal photos, uploads,
            # an undone import's watch data), so each file the snapshot names is there when it's listed, unless it's
            # removed while the files are read. One that is, or a file the snapshot names that isn't there: the
            # snapshot is taken again, after that removal, and the files new since read too. The database goes in last.
            with tempfile.TemporaryDirectory(prefix=SNAPSHOT, dir=dest) as work:
                copy, archived = os.path.join(work, "sanguity.db"), set()
                for _ in range(SNAPSHOT_TRIES):
                    before = _data_files()
                    schema, counts = _snapshot(copy)   # counts: what a check of this backup expects to find (#200)
                    listed = _data_files()
                    for rel in sorted(set(listed) - archived):
                        try:
                            f = open(listed[rel], "rb")   # one open file: the hash is of exactly what goes in
                        except FileNotFoundError:
                            continue   # removed since the list was made: taken again below
                        with f:
                            add_file(rel, f)
                        archived.add(rel)
                    gone = (set(before) | set(listed)) - archived
                    missing = _missing(copy, archived)
                    if not gone and not missing:
                        break
                if missing:   # still, after every try: missing from the live data too. Check a backup says so
                    log.warning("backup %s: its database names %d file(s) that aren't in it", name, len(missing))
                with open(copy, "rb") as f:
                    add("sanguity.db", f.read())
            slots = {}   # the receiver's data (#227): each database as it is on disk, sealed, then its uploads
            for arc, path in watch:
                slot = slots.setdefault(_watch_slot_of(arc), {"saved": None, "counts": None, "uploads": 0, "after": 0})
                try:
                    f = open(path, "rb")
                except FileNotFoundError:
                    continue   # an upload rewritten or removed since the list was made
                with f:
                    if arc.endswith("/watch.db"):
                        data = f.read()   # replaced whole by the receiver, so this is one consistent snapshot
                        add(arc, data)
                        try:
                            con = _open_watch_db(data)
                            try:
                                slot["counts"], slot["saved"] = _watch_counts(con)
                            finally:
                                con.close()
                        except (sqlite3.Error, crypto.KeyError_, ValueError):
                            pass   # archived all the same; a check says it can't be read
                    else:
                        add_file(arc, f)
                        slot["uploads"] += 1
                        if not slot["saved"] or _raw_stamp(arc) > slot["saved"]:
                            slot["after"] += 1   # came after the receiver's last save: folded in when it starts
            for slot in slots.values():
                slot["saved"] = _iso(slot["saved"])
            add("manifest.json", json.dumps({"format": FORMAT, "created": now.isoformat(timespec="seconds"),
                                             "schema": schema, "files": hashes, "counts": counts,
                                             **({"watch": slots} if slots else {})}, indent=1).encode())
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
            and (name in ("sanguity.db", "manifest.json") or parts[0] in CONTENTS + (WATCH,)))


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


def _extract(tar, m, target):
    os.makedirs(os.path.dirname(target), exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with tar.extractfile(m) as src, os.fdopen(fd, "wb") as out:
        shutil.copyfileobj(src, out)


def _stage_watch(archive, manifest, stamp):
    """The receiver's data from a backup (#227), where the receiver takes it from when it starts: its keys folder's
    restore/ (written as restore.part/ first, renamed by restore() once the rest is restored), or, when that folder
    can't be written here, DATA_DIR/watch-restored-<time>/ for the operator to move in by hand. An earlier restore's
    that the receiver hasn't taken yet goes (this one replaces it). Returns (folder, staged) or (None, False)."""
    keys = os.path.dirname(config.WATCH_KEYS)
    stage = os.path.join(keys, WATCH_STAGE)
    for old in (stage, stage + ".part"):
        if os.path.isdir(old) and not os.path.islink(old):
            shutil.rmtree(old)
    if not any(n.startswith(WATCH + "/") for n in manifest.get("files") or {}):
        return None, False
    staged = os.path.isdir(keys) and os.access(keys, os.W_OK)
    where = stage + ".part" if staged else os.path.join(config.DATA_DIR, f"watch-restored-{stamp}")
    os.makedirs(where)
    with tarfile.open(archive, "r:gz") as tar:
        for m in tar:
            if m.name.startswith(WATCH + "/"):   # names verify() checked: inside where
                _extract(tar, m, os.path.join(where, *m.name.split("/")[1:]))
    with open(os.path.join(where, "restore.json"), "w", encoding="utf-8") as f:   # names and times only
        json.dump({"backup": os.path.basename(archive), "created": manifest.get("created"),
                   "profiles": sorted(manifest.get("watch") or {})}, f)
    return where, staged


def restore(archive):
    """Replace DATA_DIR's database, profiles and build with the archive's, and hand the receiver its data (#227).
    Returns (manifest, where the old data went); manifest["watch_restored"] is {"folder", "staged"} when the backup
    has the receiver's data (staged: in the receiver's keys/restore/, which it moves in when it next starts)."""
    if _app_is_running():
        raise BackupError("The app is running. Stop it first (docker compose stop app, or stop the app in TrueNAS), "
                          "then restore with: docker compose run --rm --no-deps app python -m server.manage restore ...")
    manifest = verify(archive)
    if manifest.get("schema", 0) > len(dbm.SCHEMA):
        raise BackupError("This backup was made by a newer version of Sanguity. Update first, then restore.")
    _check_key(archive)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    watch, staged = _stage_watch(archive, manifest, stamp)   # first: it replaces nothing, so a failure leaves all as it was
    aside = os.path.join(config.DATA_DIR, f"before-restore-{stamp}")
    os.makedirs(aside)
    for n in ("sanguity.db", "sanguity.db-wal", "sanguity.db-shm", *CONTENTS):
        if os.path.exists(os.path.join(config.DATA_DIR, n)):
            shutil.move(os.path.join(config.DATA_DIR, n), os.path.join(aside, n))
    with tarfile.open(archive, "r:gz") as tar:
        for m in tar.getmembers():
            if m.name == "manifest.json" or m.name.startswith(WATCH + "/"):
                continue
            _extract(tar, m, os.path.join(config.DATA_DIR, *m.name.split("/")))
    dbm.init()   # an older backup's schema is brought up to date
    if staged:   # complete: the receiver takes it when it next starts
        os.replace(watch, watch[:-len(".part")])
        watch = watch[:-len(".part")]
    if watch:
        manifest["watch_restored"] = {"folder": watch, "staged": staged}
    return manifest, aside


# ---------------------------------------------------------------- checking a backup by restoring a copy (#200)

MISSING_WORDS = {"photo": ("journal photo", "journal photos"), "upload": ("uploaded file", "uploaded files"),
                 "document": ("document", "documents")}   # what a check says a backup lacks (#592)


def _plural(n, one, many):
    return f"{n} {one if n == 1 else many}"


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


def _check_watch(work, manifest, out, bad):
    """A check's step for the receiver's data (#227), restored under work/watch/: each profile's database opens with
    this install's key, is intact, and has the rows the manifest says; every upload is there (verify() saw each one's
    checksum). Sets out["watch"]: {"profiles", "saved" (the latest save), "after" (uploads since, folded in when the
    receiver starts), "counts": {word: {"backup", "expected"}}, "now": {word: rows in the live data}}, or None."""
    expected = manifest.get("watch") or {}
    found = _watch_slots(os.path.join(work, WATCH))
    if not expected and not found:
        out["watch"] = None
        return
    got, uploads = {}, 0
    for slot in sorted(set(expected) | set(found), key=lambda s: int(s) if s.isdigit() else -1):
        who = "the first profile's" if slot == "" else f"profile {slot}'s"
        want = (expected.get(slot) or {}).get("counts")
        d = found.get(slot)
        uploads += len(_watch_files_in(d)) if d else 0
        db = os.path.join(d, "watch.db") if d else None
        if not db or not os.path.isfile(db):
            if want is not None:
                bad(f"The backup has no phone and watch database for {who} data, though it had one when it was made.")
            continue
        try:
            with open(db, "rb") as f:
                con = _open_watch_db(f.read())
            try:
                ok = con.execute("PRAGMA integrity_check").fetchone()[0]
                if ok != "ok":
                    bad(f"The phone and watch database for {who} data is damaged ({ok}).")
                    continue
                got[slot] = _watch_counts(con)[0]
            finally:
                con.close()
        except crypto.KeyError_ as e:
            bad(f"This install's encryption key doesn't open the phone and watch database for {who} data ({e})")
            continue
        except (sqlite3.Error, ValueError) as e:
            bad(f"The phone and watch database for {who} data can't be read ({e}).")
            continue
        for t in sorted(set(got[slot]) | set(want or {})) if want is not None else ():
            if got[slot].get(t) != want.get(t):
                table = t if t != "cycle" else "one of its tables"   # (#183: not named to admins)
                bad(f"The phone and watch database for {who} data has {got[slot].get(t, 0)} rows in {table}; it had "
                    f"{want.get(t, 0)} when the backup was made.")
    exp_words = watch_words({s: v.get("counts") for s, v in expected.items()},
                            sum(v.get("uploads") or 0 for v in expected.values())) if expected else {}
    words = watch_words(got, uploads)
    saved = [v["saved"] for v in expected.values() if v.get("saved")]
    out["watch"] = {"profiles": len(set(expected) | set(found)), "saved": max(saved) if saved else None,
                    "after": sum(v.get("after") or 0 for v in expected.values()),
                    "counts": {k: {"backup": n, "expected": exp_words.get(k)} for k, n in words.items()},
                    "now": watch_words(_live_watch(), sum(len(_watch_files_in(d)) for d in _watch_slots().values()))}


def check(archive, progress=None):
    """Restore a backup into a temporary folder beside it, check what came out, then delete the copy. It never
    touches DATA_DIR: the copy is made in the backups folder and its database is opened there; the live database
    is only read, to show its counts beside the backup's. Returns {"ok", "problems": [plain sentences], "created",
    "schema", "files", "counts": {table: {"backup": rows, "expected": rows the manifest says}}, "now": {table: rows
    now}}. progress(stage, done, total), stage "checksums", "restoring" or "database"."""
    say = progress or (lambda *a: None)
    out = {"ok": False, "problems": [], "created": None, "schema": None, "files": 0, "counts": {}, "now": {}, "watch": None}

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
                named = named_files(con)   # (#592)
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
        # 5. every file a row names is in it (#592): a journal photo, an upload's file, a document
        present = {os.path.relpath(os.path.join(root, n), work).replace(os.sep, "/") for root, _, names in os.walk(work) for n in names}
        lost = {}
        for n, kind in named.items():
            if n not in present:
                lost[kind] = lost.get(kind, 0) + 1
        if lost:
            bad("The backup's database names files that aren't in it: " +
                ", ".join(_plural(lost[k], *MISSING_WORDS[k]) for k in MISSING_WORDS if k in lost) +
                ". They were missing when the backup was made: restored, the records they belong to would be without them.")
        _check_watch(work, manifest, out, bad)   # 6. the receiver's data (#227)
        out["now"] = _live_counts()
        out["ok"] = not out["problems"]
        say("database", 1, 1)
        return out
    except (OSError, tarfile.TarError, EOFError, ValueError) as e:
        return bad(f"The backup couldn't be restored into a temporary folder: {e}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
