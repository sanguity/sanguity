"""Uploads: the raw files of an import, as the browser sent them (issue #19). Importing them is #20's job.

An upload belongs to a profile. The browser creates one, sends its files one request each (PUT, the body is the
file), then finishes it; a zip it sent is unpacked here, on the server, at that point. From then on the upload is
"ready", and its files stay as the import's raw files: they are in "Export everything", in backups, and go with
the profile when it is deleted. Once an import job has read it, it goes when no job holds it any more (#547): with
the job that ends without an import (jobs.py), or else at the next start (cleanup). One sent from the page that no job
ever read and no record holds (a file attached to a record typed in that was never saved, an upload whose import was
never started) goes at the first start a day on, as one still receiving does (#933).

On disk, profiles/<id>/uploads/<upload id>/ holds each file sealed as an encrypted stream (crypto.StreamWriter)
under a random name. Which name holds which path is in the database, encrypted: while receiving, one row per file
in upload_files; once ready, the upload's manifest. So the folder says nothing about whose records these are.

  create(db, pid, user_id) -> upload_id
  add_file(db, pid, upload_id, relpath, stream) -> {"path", "bytes"}   stream: a file-like object or bytes pieces
  finish(db, pid, upload_id) -> {"files", "bytes"}                     state "ready"
  manifest(db, pid, upload_id) -> [relpath]
  with open_plain(db, pid, upload_id) as files: {relpath: absolute path}   decrypted copies in a private temporary
                                                                           folder, removed when the block ends
  discard(db, pid, upload_id)

Limits (config.UPLOAD_*): bytes per file, bytes and files per upload (counted after unpacking), and how much a
file unpacked from a zip may be bigger than it was compressed.
"""
import contextlib, errno, json, os, re, secrets, shutil, sqlite3, stat, tempfile, threading, unicodedata, zipfile, zlib

from . import config, crypto, db as dbm, profiles as prof

STALE = 86400   # an upload still receiving after this long was abandoned: removed at the next start
UNPACKED = "__MACOSX/"   # resource forks macOS adds to zips it makes; not records
RATIO_FROM = 1 << 20     # the ratio limit applies once an unpacked file is bigger than this


class UploadError(Exception):
    """Refused, with a message for the person and an HTTP status."""
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _size(n):
    for unit, b in (("GB", 2**30), ("MB", 2**20), ("KB", 2**10)):
        if n >= b:
            v = n / b
            return f"{v:.0f} {unit}" if v >= 10 or v == int(v) else f"{v:.1f} {unit}"
    return f"{n} bytes"


def limits():
    return {"file_bytes": config.UPLOAD_MAX_FILE, "total_bytes": config.UPLOAD_MAX_TOTAL,
            "files": config.UPLOAD_MAX_FILES}


def folder(pid, upload_id):
    return os.path.join(prof.profile_dir(pid), "uploads", str(int(upload_id)))


# ---------------------------------------------------------------- paths

_DRIVE = re.compile(r"^[A-Za-z]:")


def clean_path(path):
    """A relative path inside the upload, normalised ("a//./b" is "a/b", Unicode NFC), or UploadError. Refused:
    absolute paths, "..", drive letters, NUL and other control characters, backslashes."""
    if not isinstance(path, str) or not path.strip():
        raise UploadError("A file needs a name.")
    if "\\" in path or any(ord(c) < 32 or ord(c) == 127 for c in path):
        raise UploadError(f"Not allowed in a file name: a backslash or a control character ({path[:200]!r}).")
    path = unicodedata.normalize("NFC", path)
    if path.startswith("/") or _DRIVE.match(path):
        raise UploadError(f"Only relative paths can be uploaded ({path[:200]!r}).")
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if not parts:
        raise UploadError("A file needs a name.")
    if ".." in parts:
        raise UploadError(f"A path can't go up a folder with .. ({path[:200]!r}).")
    if len(path) > 1024 or len(parts) > 32 or any(len(p.encode("utf-8")) > 255 for p in parts):
        raise UploadError(f"That path is too long ({path[:200]!r}).")
    return "/".join(parts)


def _key(upload_id, rel):
    return crypto.keyed_name(f"{int(upload_id)}/{rel.casefold()}")


def _conflict(paths):
    """The first path that clashes with another: the same path in other letter cases, or a file where another
    path needs a folder ("a" and "a/b"). None if there's none."""
    names, dirs = {}, {}
    for p in paths:
        cf = p.casefold()
        if cf in names or cf in dirs:
            return p
        names[cf] = p
        parts = cf.split("/")
        for i in range(1, len(parts)):
            d = "/".join(parts[:i])
            if d in names:
                return p
            dirs[d] = p
    return None


# ---------------------------------------------------------------- the upload's row

def _row(db, pid, upload_id):
    r = db.execute("SELECT * FROM uploads WHERE id=? AND profile_id=?", (upload_id, pid)).fetchone()
    if not r:
        raise UploadError("No such upload.", 404)
    return r


def _receiving(db, pid, upload_id):
    r = _row(db, pid, upload_id)
    if r["state"] != "receiving":
        raise UploadError("This upload is finished already." if r["state"] == "ready" else "This upload was discarded.", 409)
    return r


def _pending(db, upload_id):
    return [{"f": r["file"], "p": crypto.dec(r["path"]), "n": r["bytes"]}
            for r in db.execute("SELECT file, path, bytes FROM upload_files WHERE upload_id=? ORDER BY rowid", (upload_id,))]


def _entries(db, pid, upload_id):
    r = _row(db, pid, upload_id)
    if r["state"] == "receiving":
        return r, _pending(db, upload_id)
    return r, json.loads(crypto.dec(r["manifest"])) if r["manifest"] else []


def info(db, pid, upload_id):
    r, entries = _entries(db, pid, upload_id)
    return {"id": r["id"], "state": r["state"], "created": r["created"], "files": len(entries) if r["state"] == "receiving" else r["files"],
            "bytes": r["bytes"], "paths": [e["p"] for e in entries]}


# ---------------------------------------------------------------- receiving

def create(db, pid, user_id):
    """A new, empty upload for profile pid. user_id: who uploads (None: the command line)."""
    cur = db.execute("INSERT INTO uploads(profile_id, created, created_by, state) VALUES(?, ?, ?, 'receiving')",
                     (pid, dbm.now(), user_id))
    os.makedirs(folder(pid, cur.lastrowid), mode=0o700, exist_ok=True)
    return cur.lastrowid


def _pieces(stream):
    if hasattr(stream, "read"):
        while piece := stream.read(crypto.CHUNK):
            yield piece
    else:
        for piece in stream:
            if piece:
                yield piece


def too_big(file_limit_binds):
    return UploadError(f"A file can be at most {_size(config.UPLOAD_MAX_FILE)}." if file_limit_binds else
                       f"An upload can hold at most {_size(config.UPLOAD_MAX_TOTAL)} altogether.", 413)


def _new_file():
    """A new random name for a file in an upload's folder."""
    return secrets.token_hex(16)


def add_file(db, pid, upload_id, relpath, stream):
    """Store one file of the upload, sealed, a piece at a time. Refused (and nothing kept) past the limits, for a
    path that's already in it, or once the upload is finished."""
    rel = clean_path(relpath)
    r = _receiving(db, pid, upload_id)
    if r["files"] >= config.UPLOAD_MAX_FILES:
        raise UploadError(f"An upload can hold at most {config.UPLOAD_MAX_FILES} files.", 413)
    key, dest = _key(upload_id, rel), folder(pid, upload_id)
    if db.execute("SELECT 1 FROM upload_files WHERE upload_id=? AND name=?", (upload_id, key)).fetchone():
        raise UploadError(f"{rel} is in this upload already.", 409)
    room = config.UPLOAD_MAX_TOTAL - r["bytes"]
    limit = min(config.UPLOAD_MAX_FILE, room)
    name = _new_file()
    path = os.path.join(dest, name)
    os.makedirs(dest, mode=0o700, exist_ok=True)
    with crypto.StreamWriter(path) as w:
        for piece in _pieces(stream):
            if w.size + len(piece) > limit:
                raise too_big(config.UPLOAD_MAX_FILE <= room)
            w.write(piece)
    try:
        with dbm.transaction(db):   # counted under the write lock, so files sent at once can't pass the limits together
            r = _receiving(db, pid, upload_id)
            if r["files"] >= config.UPLOAD_MAX_FILES:
                raise UploadError(f"An upload can hold at most {config.UPLOAD_MAX_FILES} files.", 413)
            if r["bytes"] + w.size > config.UPLOAD_MAX_TOTAL:
                raise too_big(False)
            try:
                db.execute("INSERT INTO upload_files(upload_id, name, file, path, bytes) VALUES(?, ?, ?, ?, ?)",
                           (upload_id, key, name, crypto.enc(rel), w.size))
            except sqlite3.IntegrityError:
                raise UploadError(f"{rel} is in this upload already.", 409) from None
            db.execute("UPDATE uploads SET files=files+1, bytes=bytes+? WHERE id=?", (w.size, upload_id))
    except BaseException:
        _remove(path)
        raise
    return {"path": rel, "bytes": w.size}


def _remove(path):
    with contextlib.suppress(FileNotFoundError):
        os.remove(path)


# ---------------------------------------------------------------- finishing: zips are unpacked here

def _is_zip(path):
    for piece in crypto.iter_file(path, 4):
        return piece[:4] in (b"PK\x03\x04", b"PK\x05\x06")   # a zip, or an empty one
    return False


def _unzip(upload_id, dest, entry, prefix, files_left, bytes_left, out):
    """Unpack the zip `entry` (a pending file) into dest as new sealed files, appended to out as manifest entries.
    Symlinks, absolute or .. entries, encrypted entries, and more files or bytes than are left are refused; so is
    an entry that unpacks to more than UPLOAD_MAX_ZIP_RATIO times its compressed size, counting what is actually
    written, not the size its header claims. Zips inside are kept as files."""
    zname = entry["p"]
    work = tempfile.mkdtemp(prefix="sanguity-unzip-")   # the zip's plaintext, private (mode 700), never kept
    try:
        plain = os.path.join(work, "upload.zip")
        crypto.decrypt_to(os.path.join(dest, entry["f"]), plain)
        zsize = os.path.getsize(plain)
        with zipfile.ZipFile(plain) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir() and not i.filename.startswith(UNPACKED)]
            if len(infos) > files_left:
                raise UploadError(f"{zname} holds {len(infos)} files: more than an upload can hold "
                                  f"({config.UPLOAD_MAX_FILES} files).", 413)
            for i in infos:
                if stat.S_ISLNK(i.external_attr >> 16):
                    raise UploadError(f"{zname} holds a symbolic link ({i.filename[:200]}), which isn't allowed.")
                if i.flag_bits & 0x1:
                    raise UploadError(f"{zname} is password-protected. Upload it unprotected.")
                try:
                    rel = clean_path(prefix + clean_path(i.filename))
                except UploadError as e:
                    raise UploadError(f"{zname}: {e}") from None
                name = _new_file()
                path = os.path.join(dest, name)
                limit, denom = min(config.UPLOAD_MAX_FILE, bytes_left), max(min(i.compress_size, zsize), 1)
                with crypto.StreamWriter(path) as w, zf.open(i) as src:
                    while piece := src.read(crypto.CHUNK):
                        n = w.size + len(piece)
                        if n > limit:
                            raise too_big(config.UPLOAD_MAX_FILE <= bytes_left)
                        if n > RATIO_FROM and n > config.UPLOAD_MAX_ZIP_RATIO * denom:
                            raise UploadError(f"{zname} unpacks to far more than its size (at {i.filename[:200]}): "
                                              "it looks like a zip bomb, so it was refused.", 413)
                        w.write(piece)
                out.append({"f": name, "p": rel, "n": w.size})
                bytes_left -= w.size
    except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError, ValueError) as e:
        raise UploadError(f"{zname} can't be unpacked ({e}).") from None
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return bytes_left


def finish(db, pid, upload_id, unzip=True):
    """No more files: unpack each zip that was sent (into a folder named after it, or at the top when it is the
    only file), check the whole, and set the state to "ready". Returns {"files", "bytes"}. On a refusal nothing
    unpacked is kept and the upload stays as it was, receiving (discard it, or send something else)."""
    _receiving(db, pid, upload_id)
    pending = _pending(db, upload_id)
    if not pending:
        raise UploadError("Nothing was uploaded.")
    dest = folder(pid, upload_id)
    zips = [e for e in pending if unzip and e["p"].lower().endswith(".zip") and _is_zip(os.path.join(dest, e["f"]))]
    zipped = {e["f"] for e in zips}
    unpacked = []
    try:
        kept = [e for e in pending if e["f"] not in zipped]
        files_left = config.UPLOAD_MAX_FILES - len(kept)
        bytes_left = config.UPLOAD_MAX_TOTAL - sum(e["n"] for e in kept)
        for z in zips:
            prefix = "" if len(pending) == 1 else z["p"][:-4] + "/"
            before = len(unpacked)
            bytes_left = _unzip(upload_id, dest, z, prefix, files_left, bytes_left, unpacked)
            files_left -= len(unpacked) - before
        with dbm.transaction(db):
            _receiving(db, pid, upload_id)
            final = [e for e in _pending(db, upload_id) if e["f"] not in zipped] + unpacked   # files sent meanwhile too
            if len(final) > config.UPLOAD_MAX_FILES:
                raise UploadError(f"An upload can hold at most {config.UPLOAD_MAX_FILES} files.", 413)
            total = sum(e["n"] for e in final)
            if total > config.UPLOAD_MAX_TOTAL:
                raise too_big(False)
            if (clash := _conflict([e["p"] for e in final])):
                raise UploadError(f"{clash} clashes with another file in this upload (the same name, or a file "
                                  "named like a folder).", 409)
            db.execute("UPDATE uploads SET state='ready', files=?, bytes=?, manifest=? WHERE id=?",
                       (len(final), total, crypto.enc(json.dumps(final, separators=(",", ":"))), upload_id))
            db.execute("DELETE FROM upload_files WHERE upload_id=?", (upload_id,))
    except BaseException:
        for e in unpacked:
            _remove(os.path.join(dest, e["f"]))
        raise
    for f in zipped:   # the zips themselves: what they held is kept instead
        _remove(os.path.join(dest, f))
    return {"files": len(final), "bytes": total}


# ---------------------------------------------------------------- reading

def manifest(db, pid, upload_id):
    """The relative paths of the upload's files."""
    return [e["p"] for e in _entries(db, pid, upload_id)[1]]


@contextlib.contextmanager
def open_plain(db, pid, upload_id):
    """Decrypted copies of a ready upload's files in a private temporary folder (mode 700, under
    tempfile.gettempdir(): in Docker, the container's tmpfs /tmp), as {relpath: absolute path}. The folder is
    removed when the block ends, also on an error. What is stored stays encrypted."""
    r, entries = _entries(db, pid, upload_id)
    if r["state"] != "ready":
        raise UploadError("This upload isn't finished." if r["state"] == "receiving" else "This upload was discarded.", 409)
    src = folder(pid, upload_id)
    # room for it first (#592): the TrueNAS app's /tmp is a tmpfs sized from its memory limit, and an upload that
    # doesn't fit is refused saying so, rather than failing part way with a disk-full error
    need = sum(e.get("n") or _size_of(os.path.join(src, e["f"])) for e in entries)
    free = shutil.disk_usage(tempfile.gettempdir()).free
    if need > free:
        raise UploadError(no_room(need, free), 507)
    tmp = tempfile.mkdtemp(prefix="sanguity-upload-")
    try:
        os.chmod(tmp, 0o700)
        root, files = os.path.realpath(tmp), {}
        for e in entries:
            target = os.path.join(tmp, *clean_path(e["p"]).split("/"))
            if not os.path.realpath(target).startswith(root + os.sep):
                raise UploadError(f"{e['p']} would be outside the upload's folder.")
            os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
            try:
                crypto.decrypt_to(os.path.join(src, e["f"]), target)
            except OSError as x:
                if x.errno != errno.ENOSPC:
                    raise
                raise UploadError(no_room(need, free), 507) from None   # (another import filling it at the same time)
            files[e["p"]] = target
        yield files
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _size_of(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def no_room(need, free):
    """Why an upload couldn't be read (#592): no room for its decrypted copy in the app's temporary folder."""
    return (f"There isn't room to read this upload: it needs about {_size(need)} of temporary space, and the app's "
            f"temporary folder ({tempfile.gettempdir()}) has {_size(free)} free. Nothing was saved. In the TrueNAS app, "
            "raise Sanguity's memory limit (its temporary folder is half of it); elsewhere, give the app's /tmp more room "
            "(the README's Uploading records in the app says how).")


_rewriting = threading.Lock()   # one file written again at a time (rewrite)


def rewrite(db, pid, upload_id, rel, fn):
    """One of a ready upload's files written again through fn(pieces, write) -> how many things it left out (#555: an
    Apple Health export without its cycle records): the file's plaintext a piece at a time in, sealed out; with
    nothing left out, the file is left as it was. The new copy takes the old one's name (os.replace: a reader already
    reading it reads it as it was), and the manifest its size. Returns fn's count; fn's ValueError leaves it as it was."""
    with _rewriting:
        r, entries = _entries(db, pid, upload_id)
        e = next((x for x in entries if x["p"] == rel), None) if r["state"] == "ready" else None
        if e is None:
            return 0
        path = os.path.join(folder(pid, upload_id), e["f"])
        w = crypto.StreamWriter(path)
        try:
            n = fn(crypto.iter_file(path), w.write)
        except BaseException:
            w.abort()
            raise
        if not n:
            w.abort()
            return 0
        w.close()
        with dbm.transaction(db):
            r = _row(db, pid, upload_id)
            if r["state"] == "ready" and r["manifest"]:
                now = json.loads(crypto.dec(r["manifest"]))
                for x in now:
                    if x["f"] == e["f"]:
                        x["n"] = w.size
                db.execute("UPDATE uploads SET manifest=?, bytes=? WHERE id=?",
                           (crypto.enc(json.dumps(now, separators=(",", ":"))), sum(x["n"] for x in now), upload_id))
        return n


def stored_files(db, pid):
    """Every ready upload's files as stored: [(upload_id, relpath, sealed file)], for Export everything."""
    out = []
    for r in db.execute("SELECT id, manifest FROM uploads WHERE profile_id=? AND state='ready' ORDER BY id", (pid,)):
        for e in json.loads(crypto.dec(r["manifest"])) if r["manifest"] else []:
            out.append((r["id"], e["p"], os.path.join(folder(pid, r["id"]), e["f"])))
    return out


# ---------------------------------------------------------------- discarding and tidying up

def _rmtree(path):
    """Remove a folder: renamed aside first, so a half-removed one is never mistaken for a live upload."""
    if not os.path.isdir(path):
        return
    aside = os.path.join(os.path.dirname(path), f".discarding-{os.path.basename(path)}-{secrets.token_hex(4)}")
    try:
        os.replace(path, aside)
    except OSError:
        aside = path
    shutil.rmtree(aside, ignore_errors=True)


def discard(db, pid, upload_id):
    """Remove an upload's files. The row stays, as "discarded", with its counts."""
    _row(db, pid, upload_id)
    with dbm.transaction(db):
        db.execute("UPDATE uploads SET state='discarded', manifest=NULL WHERE id=?", (upload_id,))
        db.execute("DELETE FROM upload_files WHERE upload_id=?", (upload_id,))
    _rmtree(folder(pid, upload_id))


def orphaned(db, now=None):
    """The ready uploads nothing holds any more (#547), by id: read by an import job once, and now used by none
    (queued, being read, in review, or saved as an import; jobs.py), with no document stored in them; and those sent
    from the page (by an account) that no job ever read, with no document stored in them, a day on (#933): a file
    attached to a record typed in that was never saved, or an upload whose import was never started. One put back
    from an export (made by the command line) is kept, as is a typed-in record's file once it's saved (its document)."""
    now = now or dbm.now()
    rows = db.execute("""SELECT u.id, u.profile_id FROM uploads u WHERE u.state='ready'
                         AND (EXISTS(SELECT 1 FROM jobs j WHERE j.upload_id=u.id)
                              OR u.created_by IS NOT NULL AND u.created < ?)
                         AND NOT EXISTS(SELECT 1 FROM jobs j WHERE j.upload_id=u.id AND (j.state IN ('queued', 'running', 'preview')
                                        OR j.state='done' AND j.import_id IS NOT NULL))""", (now - STALE,)).fetchall()
    docs, out = {}, []
    for r in rows:
        if r["profile_id"] not in docs:
            docs[r["profile_id"]] = [os.path.realpath(crypto.dec(d["path"]))
                                     for d in db.execute("SELECT path FROM documents WHERE profile_id=?", (r["profile_id"],)) if d["path"]]
        root = os.path.realpath(folder(r["profile_id"], r["id"]))
        if not any(p.startswith(root + os.sep) for p in docs[r["profile_id"]]):
            out.append(r["id"])
    return out


def cleanup(db, now=None):
    """At start-up: remove uploads still receiving after a day (abandoned), uploads nothing holds (orphaned: an
    import's no job holds, one no job read a day on), and any upload folder that no live upload owns (a discarded one,
    or one left by a crash). Returns how many uploads still receiving were removed."""
    now = now or dbm.now()
    stale = db.execute("SELECT id, profile_id FROM uploads WHERE state='receiving' AND created < ?", (now - STALE,)).fetchall()
    for r in stale:
        db.execute("DELETE FROM uploads WHERE id=?", (r["id"],))
        _rmtree(folder(r["profile_id"], r["id"]))
    for r in orphaned(db, now):
        db.execute("UPDATE uploads SET state='discarded', manifest=NULL WHERE id=?", (r,))
        db.execute("DELETE FROM upload_files WHERE upload_id=?", (r,))
    live = {(r["profile_id"], r["id"]) for r in db.execute("SELECT id, profile_id FROM uploads WHERE state != 'discarded'")}
    base = os.path.join(config.DATA_DIR, "profiles")
    for p in os.listdir(base) if os.path.isdir(base) else ():
        up = os.path.join(base, p, "uploads")
        if not p.isdigit() or not os.path.isdir(up):
            continue
        for u in os.listdir(up):
            if u.startswith(".discarding-"):
                shutil.rmtree(os.path.join(up, u), ignore_errors=True)
            elif not (u.isdigit() and (int(p), int(u)) in live):
                _rmtree(os.path.join(up, u))
    return len(stale)
