"""Per-profile data on disk: profiles/<id>/data.json and settings.json, plus the documents they link to.

Until the import tool (Phase 2) stores records in the database, a profile's records are one built data.json, as
before. What changes is how it is reached: only through the API, after a role check. Each document link in the
data is replaced by a random document id; the id maps to a file under the records folder or the build folder,
and nothing else on disk can be reached.
"""
import contextlib, json, os, secrets, shutil, threading
from urllib.parse import quote

from . import activity, config, crypto, db as dbm

_lock = threading.Lock()
_failed = {}   # profile id -> (build mtime, why): a build that couldn't be loaded, so it isn't retried on every request


def profile_dir(pid):
    return os.path.join(config.DATA_DIR, "profiles", str(int(pid)))


def data_path(pid):
    return os.path.join(profile_dir(pid), "data.json")


def settings_path(pid):
    return os.path.join(profile_dir(pid), "settings.json")


def _inside(path, root):
    root = os.path.realpath(root)
    path = os.path.realpath(path)
    return path == root or path.startswith(root + os.sep)


def resolve_document(url, base_dir):
    """The file a document link in data.json points at, or None if it points anywhere it shouldn't.

    build.py writes "records/<export folder>/..." for files inside the record exports; the demo writes paths
    relative to its own output folder. A sources.json "files" prefix (files-.../) used to point into www/, so a
    file not found next to the data is looked for there. Absolute paths, URLs and anything that climbs out with
    .. are refused.
    """
    if not isinstance(url, str) or not url or "://" in url or url.startswith("//") or "\0" in url:
        return None
    rel = url.split("#", 1)[0].split("?", 1)[0].lstrip("/")
    if rel.startswith("records/"):
        root, rel = config.RECORDS_DIR, rel[len("records/"):]
    else:
        root = base_dir
    if not rel or os.path.isabs(rel):
        return None
    path = os.path.join(root, *rel.split("/"))
    if not _inside(path, root):
        return None
    parts = os.path.normpath(rel).replace(os.sep, "/").split("/")
    if root == base_dir and len(parts) > 1 and parts[0].startswith("files") and not os.path.exists(path):
        old = os.path.join(config.LEGACY_WWW, *parts)   # only inside a files*/ folder of the old www/
        if os.path.isfile(old) and _inside(old, os.path.join(config.LEGACY_WWW, parts[0])):
            return os.path.realpath(old)
    return os.path.realpath(path)


HTML_DOCS = ("html", "htm")
# what an HTML record may load from its own folder: pictures and stylesheets, nothing that runs
PART_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
              "webp": "image/webp", "bmp": "image/bmp", "css": "text/css; charset=utf-8"}


def document_url(pid, doc, path, ext):
    """Where the app fetches a document. An HTML record's URL ends in its own file name, so the relative links
    inside it (images, a stylesheet saved beside it) resolve next to it; see main.document_part. ext is the type
    stored for the document, the same one document_part checks."""
    url = f"api/profiles/{pid}/documents/{doc}"
    if ext in HTML_DOCS:
        url += "/" + quote(os.path.basename(path))
    return url


def _write_json(path, obj):
    crypto.write_file(path, json.dumps(obj, separators=(",", ":")).encode("utf-8"))


def read_json(path):
    """A JSON file Sanguity wrote (encrypted) or was given (plaintext)."""
    return json.loads(crypto.read_file(path))


def import_dataset(db, pid, src_dir, version=None, mirror_settings=False):
    """Load src_dir/data.json (and settings.json, when there is one) into profile pid. With mirror_settings, a
    missing settings.json also removes the profile's (the setup build is the only source of a linked profile's).

    Document ids stay the same across re-imports for the same file, so links people saved keep working.
    Returns the number of documents indexed.
    """
    data = read_json(os.path.join(src_dir, "data.json"))
    if not isinstance(data, dict) or not isinstance(data.get("events"), list) or not isinstance(data.get("details", {}), dict):
        raise ValueError("it isn't a Sanguity data file (expected an object with an \"events\" list)")
    os.makedirs(profile_dir(pid), exist_ok=True)
    known = {crypto.dec(r["path"]): (r["id"], r["ext"]) for r in db.execute("SELECT id, path, ext FROM documents WHERE profile_id=?", (pid,))}
    keep, n = set(), 0
    with dbm.transaction(db):
        for d in (data.get("details") or {}).values():
            if not isinstance(d, dict) or d.get("type") != "file":
                continue
            path = resolve_document(d.get("url"), src_dir)
            if not path:
                d["url"] = None
                continue
            ext = (d.get("ext") or os.path.splitext(path)[1].lstrip(".")).lower()[:10]
            doc, had = known.get(path, (None, None))
            if not doc:
                doc = secrets.token_urlsafe(16)
                db.execute("INSERT INTO documents(id, profile_id, path, ext) VALUES(?, ?, ?, ?)", (doc, pid, crypto.enc(path), ext))
            elif had != ext:   # same file, newly described as another type
                db.execute("UPDATE documents SET ext=? WHERE id=?", (ext, doc))
            known[path] = (doc, ext)
            keep.add(doc)
            frag = d["url"].split("#", 1)[1] if "#" in d["url"] else ""
            d["url"] = document_url(pid, doc, path, ext) + ("#" + frag if frag else "")
            n += 1
        for path, (doc, _) in known.items():
            if doc not in keep:
                db.execute("DELETE FROM documents WHERE id=?", (doc,))
        _write_json(data_path(pid), data)
        src_settings = os.path.join(src_dir, "settings.json")
        if os.path.isfile(src_settings):
            crypto.write_file(settings_path(pid), crypto.read_file(src_settings))
        elif mirror_settings and os.path.isfile(settings_path(pid)):
            os.remove(settings_path(pid))
        db.execute("UPDATE profiles SET build_mtime=? WHERE id=?", (version, pid))
    return n


def build_version():
    """Which setup build is there: the modification time and size of its data.json and of its settings.json, so
    adding, editing, replacing (even with an older file) or removing either changes it. None when there is no
    build. Stored in profiles.build_mtime."""
    def sig(name):
        try:
            st = os.stat(os.path.join(config.BUILD_DIR, name))
        except OSError:
            return None
        return f"{st.st_mtime_ns}:{st.st_size}"
    data = sig("data.json")
    return None if data is None else f"{data}|{sig('settings.json') or '-'}"


def sync_build(db, pid=None):
    """Re-import the setup build into linked profiles whose copy is older than it (after `setup rebuild`).
    Returns {profile id: why} for those the current build couldn't be loaded into (empty when all is well)."""
    m = build_version()
    if m is None:
        return {}
    q = "SELECT id, build_mtime FROM profiles WHERE linked_build=1" + (" AND id=?" if pid is not None else "")
    rows = db.execute(q, (pid,) if pid is not None else ()).fetchall()
    stale = [r["id"] for r in rows if r["build_mtime"] != m or not os.path.isfile(data_path(r["id"]))]
    with _lock if stale else contextlib.nullcontext():   # don't queue behind another profile's import for nothing
        for p in stale:
            cur = db.execute("SELECT build_mtime FROM profiles WHERE id=?", (p,)).fetchone()
            if not cur or (cur["build_mtime"] == m and os.path.isfile(data_path(p))) or _failed.get(p, (None,))[0] == m:
                continue
            # a bad build must not take the app down: the profile keeps its last good copy (if any) and the
            # problem goes to the log; the next rebuild is tried afresh
            try:
                import_dataset(db, p, config.BUILD_DIR, m, mirror_settings=True)
                _failed.pop(p, None)
                activity.log(db, "reload", profile_id=p, actor=activity.SETUP_BUILD)
            except Exception as e:
                _failed[p] = (m, f"{type(e).__name__}: {e}")
                print(f"Couldn't load the built records into profile {p}: {_failed[p][1]}", flush=True)
    return {r["id"]: _failed[r["id"]][1] for r in rows if _failed.get(r["id"], (None,))[0] == m}


def build_error(pid):
    """Why the latest setup build couldn't be loaded into this profile, or None."""
    f = _failed.get(pid)
    return f[1] if f and f[0] == build_version() else None


def delete_profile(db, pid):
    """Remove a profile: its folder (records, settings, anything imported), document index, memberships and
    activity. The folder is moved aside first and removed only once the database change is committed, so a
    failure leaves the profile whole. Original exports and the watch receiver's data are never touched.
    Returns what was deleted: {"name", "members", "linked_build"}."""
    p = db.execute("SELECT name, linked_build FROM profiles WHERE id=?", (pid,)).fetchone()
    members = db.execute("SELECT COUNT(*) FROM memberships WHERE profile_id=?", (pid,)).fetchone()[0]
    folder, aside = profile_dir(pid), os.path.join(config.DATA_DIR, "profiles", f".deleting-{int(pid)}")
    shutil.rmtree(aside, ignore_errors=True)
    if os.path.isdir(folder):
        os.replace(folder, aside)
    try:
        with dbm.transaction(db):
            db.execute("DELETE FROM profiles WHERE id=?", (pid,))   # cascades: memberships, documents, activity
            if p["linked_build"]:   # don't bring it back from the build on the next start
                dbm.meta_set(db, "build_unlinked", "1")
    except BaseException:
        if os.path.isdir(aside):
            os.replace(aside, folder)
        raise
    shutil.rmtree(aside, ignore_errors=True)
    _failed.pop(pid, None)
    return {"name": p["name"], "members": members, "linked_build": bool(p["linked_build"])}


def link_build(db, pid):
    """Load the setup build into a profile and keep it in step with rebuilds (undoes the unlinking above)."""
    db.execute("UPDATE profiles SET linked_build=1, build_mtime=NULL, watch_file=IFNULL(watch_file, ?) WHERE id=?",
               (config.WATCH_FILE, pid))
    dbm.meta_set(db, "build_unlinked", None)
    _failed.pop(pid, None)
    return sync_build(db, pid)


def create_profile(db, name, owner_id=None, linked_build=False, watch_file=None):
    cur = db.execute("INSERT INTO profiles(name, created, linked_build, watch_file) VALUES(?, ?, ?, ?)",
                     (name, dbm.now(), int(linked_build), watch_file))
    pid = cur.lastrowid
    if owner_id is not None:
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'owner')", (owner_id, pid))
    os.makedirs(profile_dir(pid), exist_ok=True)
    return pid


def bootstrap(db):
    """First start: the existing single-person data (the setup build) becomes the first profile. Not again after
    its profile was deleted (server.manage link-build loads it into a profile on purpose)."""
    if db.execute("SELECT 1 FROM profiles LIMIT 1").fetchone() or build_version() is None or dbm.meta_get(db, "build_unlinked"):
        return None
    pid = create_profile(db, "My records", linked_build=True, watch_file=config.WATCH_FILE)
    sync_build(db, pid)
    return pid
