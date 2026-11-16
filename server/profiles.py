"""Loading records into a profile, and what a profile keeps on disk: the documents its records link to (its
settings are in the database since schema version 18, settings.py).

A profile's records are rows in the database (records.py), each belonging to the import that brought it in. A
data.json (what build.py, the demo and "Export everything" write) is loaded as one import: the setup build into
the profiles linked to it, replacing the build's previous import; anything else with server.manage import,
replacing the profile's records. They are reached only through the API, after a role check. Each document link
is replaced by a random document id; the id maps to a file under the records folder, the build folder or the
profile's own folder, and nothing else on disk can be reached.

Before schema version 10 a profile's records were profiles/<id>/data.json; migrate_files loads such a file into
the tables at start-up and keeps it beside them as data-before-v10.json.
"""
import contextlib, json, os, secrets, shutil, threading
from urllib.parse import quote

from . import activity, config, crypto, db as dbm, edits, records, settings

_lock = threading.Lock()
_failed = {}   # profile id -> (build mtime, why): a build that couldn't be loaded, so it isn't retried on every request


def profile_dir(pid):
    return os.path.join(config.DATA_DIR, "profiles", str(int(pid)))


def data_path(pid):
    """Where a profile's records were kept before schema version 10 (see migrate_files)."""
    return os.path.join(profile_dir(pid), "data.json")


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


def read_json(path):
    """A JSON file Sanguity wrote (encrypted) or was given (plaintext)."""
    return json.loads(crypto.read_file(path))


def _check(data):
    if not isinstance(data, dict) or not isinstance(data.get("events"), list) or not isinstance(data.get("details") or {}, dict) \
            or not isinstance(data.get("labs") or [], list) or not isinstance(data.get("costs") or [], list):
        raise ValueError("it isn't a Sanguity data file (expected an object with an \"events\" list)")


def import_dataset(db, pid, src_dir, version=None, mirror_settings=False, origin="dataset"):
    """Load src_dir/data.json (and settings.json, when there is one) into profile pid as one import. It replaces
    the profile's records, or with origin "build" only what the setup build brought before. A settings.json
    replaces the profile's settings (an export loaded back in); with mirror_settings (the setup build) it only
    seeds them the first time, see settings.from_build.

    Document ids stay the same across re-imports for the same file, so links people saved keep working.
    Returns the number of documents indexed.
    """
    data = read_json(os.path.join(src_dir, "data.json"))
    _check(data)
    shown = {str(e.get("id")) for e in data["events"] if isinstance(e, dict)}
    data["details"] = {k: v for k, v in (data.get("details") or {}).items() if str(k) in shown}   # what the page can open
    os.makedirs(profile_dir(pid), exist_ok=True)
    with dbm.transaction(db):
        old = records.imports(db, pid, "build" if origin == "build" else None)
        before = [records._dec(r["body"]) for r in db.execute(
            "SELECT body FROM events WHERE profile_id=? AND import_id IN (SELECT value FROM json_each(?))",
            (pid, json.dumps(old)))] if origin == "build" else []
        q = """SELECT id, path, ext FROM documents
               WHERE profile_id=? AND (import_id IS NULL OR import_id IN (SELECT value FROM json_each(?)))"""
        known = {crypto.dec(r["path"]): (r["id"], r["ext"]) for r in db.execute(q, (pid, json.dumps(old)))}
        keep, n = index_documents(db, pid, data.get("details") or {}, lambda url: resolve_document(url, src_dir), known)
        iid = records.store(db, pid, data, origin)
        link_documents(db, iid, keep)
        records.drop(db, pid, old)   # and, by cascade, the documents the new import no longer links to
        if before:   # a rebuild: edits on build events whose title changed follow them
            edits.follow_rebuild(db, pid, before, data["events"])
        db.execute("DELETE FROM documents WHERE profile_id=? AND import_id IS NULL", (pid,))
        src_settings = os.path.join(src_dir, "settings.json")
        if mirror_settings:
            settings.from_build(db, pid, src_settings)
        elif os.path.isfile(src_settings):
            settings.replace_profile(db, pid, settings.from_file(settings.read_file(src_settings), where=src_settings))
            db.execute("UPDATE profiles SET settings_seeded=1 WHERE id=?", (pid,))
        db.execute("UPDATE profiles SET build_mtime=? WHERE id=?", (version, pid))
    return n


def index_documents(db, pid, details, locate, known=None):
    """Give each document link in details ({"type": "file", "url": ...}) a document id, and point its url at the
    app's route for it (keeping any #fragment). locate(url) is the file the link names, or None, which drops the
    link. A file already in known ({path: (document id, ext)}) keeps its id. Call inside the import's transaction,
    then link_documents once the import exists. Returns (the document ids used, the number of links indexed)."""
    known = {} if known is None else known
    keep, n = set(), 0
    for d in details.values():
        if not isinstance(d, dict) or d.get("type") != "file":
            continue
        path = locate(d.get("url"))
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
    return keep, n


def link_documents(db, import_id, doc_ids):
    """Make the documents index_documents gave out belong to the import, so they go when it does."""
    db.executemany("UPDATE documents SET import_id=? WHERE id=?", [(import_id, d) for d in doc_ids])


def build_version():
    """Which setup build is there: the modification time and size of its data.json and of its settings.json, so
    adding, editing, replacing (even with an older file) or removing either changes it (a changed settings.json is
    then reported, see settings.from_build). None when there is no build. Stored in profiles.build_mtime."""
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
    stale = [r["id"] for r in rows if r["build_mtime"] != m or not records.imports(db, r["id"], "build")]
    with _lock if stale else contextlib.nullcontext():   # don't queue behind another profile's import for nothing
        for p in stale:
            cur = db.execute("SELECT build_mtime FROM profiles WHERE id=?", (p,)).fetchone()
            if not cur or (cur["build_mtime"] == m and records.imports(db, p, "build")) or _failed.get(p, (None,))[0] == m:
                continue
            # a bad build must not take the app down: the profile keeps its last good copy (if any) and the
            # problem goes to the log; the next rebuild is tried afresh
            try:
                import_dataset(db, p, config.BUILD_DIR, m, mirror_settings=True, origin="build")
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
    """Remove a profile: its records, settings, its folder (anything imported), document index, memberships and
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
            db.execute("DELETE FROM profiles WHERE id=?", (pid,))   # cascades: records, memberships, documents, activity
            if p["linked_build"]:   # don't bring it back from the build on the next start
                dbm.meta_set(db, "build_unlinked", "1")
    except BaseException:
        if os.path.isdir(aside):
            os.replace(aside, folder)
        raise
    shutil.rmtree(aside, ignore_errors=True)
    _failed.pop(pid, None)
    records.forget(pid)
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


MIGRATED = "data-before-v10.json"


def migrate_files(db):
    """Load each profile's data.json, where its records were kept before schema version 10, into the tables as one
    import (the setup build's, for a linked profile, so the next rebuild replaces it), and keep the file beside
    them as data-before-v10.json. Its document links already point at the profile's documents, which become the
    import's. Returns the number of profiles loaded."""
    n = 0
    for r in db.execute("SELECT id, linked_build FROM profiles ORDER BY id").fetchall():
        pid, path = r["id"], data_path(r["id"])
        if not os.path.isfile(path):
            continue
        if not records.imports(db, pid):   # else loaded already, by a start that stopped before moving the file
            try:
                data = read_json(path)
                _check(data)
            except Exception as e:   # left where it is, for someone to look at; the app still starts
                print(f"Couldn't move profile {pid}'s records into the database ({type(e).__name__}: {e}); "
                      f"{path} is left as it was.", flush=True)
                continue
            with dbm.transaction(db):
                iid = records.store(db, pid, data, "build" if r["linked_build"] else "dataset")
                db.execute("UPDATE documents SET import_id=? WHERE profile_id=? AND import_id IS NULL", (iid, pid))
            n += 1
        os.replace(path, os.path.join(profile_dir(pid), MIGRATED))
    return n
