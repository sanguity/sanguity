"""Loading records into a profile, and what a profile keeps on disk: the documents its records link to (its
settings are in the database since schema version 18, settings.py).

A profile's records are rows in the database (records.py), each belonging to the import that brought it in. A
data.json (what build.py, the demo and "Export everything" write) is loaded as one import: the setup build into
the profiles linked to it, replacing the build's previous import; anything else with server.manage import,
replacing the profile's records. They are reached only through the API, after a role check. Each document link
is replaced by a random document id; the id maps to a file under the records folder, the build folder or the
profile's own folder, and nothing else on disk can be reached.

Before schema version 10 a profile's records were profiles/<id>/data.json; migrate_files loads such a file into
the tables at start-up, then deletes it (#934).
"""
import contextlib, json, os, secrets, shutil, threading
from urllib.parse import quote

from . import activity, config, crypto, db as dbm, edits, milestones, records, settings

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


def import_dataset(db, pid, src_dir, version=None, mirror_settings=False, origin="dataset", then=None, unpacked=False):
    """Load src_dir/data.json (and settings.json, when there is one) into profile pid as one import. It replaces
    the profile's records, or with origin "build" only what the setup build brought before. A settings.json
    replaces the profile's settings (an export loaded back in); with mirror_settings (the setup build) it only
    seeds them the first time, see settings.from_build. then(db, data, folder): more to load from data.json in the
    same transaction (an export's corrections, journal and medication log, export.restore, #589), folder where
    src_dir's files are now. unpacked: src_dir is an export unpacked for this import in imported/ (export.import_archive),
    which becomes the import's own folder, imported/<import id>/ (#945).

    A dataset import replaces every import, so what an earlier one was loaded from in imported/ goes once it's saved
    (left_behind). Document ids stay the same across re-imports for the same file, so links people saved keep
    working (an export loaded again: by the file's place in it). Returns the number of documents indexed.
    """
    data = read_json(os.path.join(src_dir, "data.json"))
    _check(data)
    shown = {str(e.get("id")) for e in data["events"] if isinstance(e, dict)}
    data["details"] = {k: v for k, v in (data.get("details") or {}).items() if str(k) in shown}   # what the page can open
    os.makedirs(profile_dir(pid), exist_ok=True)
    folder, gone = src_dir, []
    try:
        with dbm.transaction(db):
            old = records.imports(db, pid, "build" if origin == "build" else None)
            before = [records._dec(r["body"]) for r in db.execute(
                "SELECT body FROM events WHERE profile_id=? AND import_id IN (SELECT value FROM json_each(?))",
                (pid, json.dumps(old)))] if origin == "build" else []
            q = """SELECT id, path, ext FROM documents
                   WHERE profile_id=? AND (import_id IS NULL OR import_id IN (SELECT value FROM json_each(?)))"""
            known = {crypto.dec(r["path"]): (r["id"], r["ext"]) for r in db.execute(q, (pid, json.dumps(old)))}
            if unpacked:   # the same file of an export loaded before, at the same place in it: the same document
                known.update({os.path.realpath(os.path.join(src_dir, rel)): v for p, v in list(known.items()) if (rel := _in_imported(pid, p))})
            keep, n = index_documents(db, pid, data.get("details") or {}, lambda url: resolve_document(url, src_dir), known)
            iid = records.store(db, pid, data, origin)
            link_documents(db, iid, keep)
            if unpacked:
                folder = _own_folder(db, pid, iid, src_dir)
            records.drop(db, pid, old)   # and, by cascade, the documents the new import no longer links to
            if before:   # a rebuild: edits on build events whose title changed follow them
                edits.follow_rebuild(db, pid, before, data["events"])
            db.execute("DELETE FROM documents WHERE profile_id=? AND import_id IS NULL", (pid,))
            src_settings = os.path.join(folder, "settings.json")
            if mirror_settings:
                settings.from_build(db, pid, src_settings)
            elif os.path.isfile(src_settings):
                values = settings.from_file(settings.read_file(src_settings), where=src_settings)
                if "milestones" not in values and milestones.old(data):   # an export from before #39 kept them in data.json
                    values["milestones"] = milestones.clean(milestones.old(data), src_settings)
                settings.replace_profile(db, pid, values)
                db.execute("UPDATE profiles SET settings_seeded=1 WHERE id=?", (pid,))
            db.execute("UPDATE profiles SET build_mtime=? WHERE id=?", (version, pid))
            if then:
                then(db, data, folder)
            if origin == "dataset":
                gone = left_behind(db, pid, keep=[folder])
    except BaseException:
        if folder != src_dir:   # moved into its import's folder, which wasn't saved: back where it was unpacked
            os.replace(folder, src_dir)
        raise
    remove(gone)
    return n


def imported_dir(pid, iid=None):
    """Where server.manage import unpacks an export (export.import_archive): imported/, and in it each import's own
    folder, imported/<import id>/ (#945). An older version unpacked it into imported/ itself."""
    base = os.path.join(profile_dir(pid), IMPORTED)
    return base if iid is None else os.path.join(base, str(int(iid)))


def _in_imported(pid, path):
    """Where path is in the export it was unpacked from, if it's in imported/: its path in imported/<id>/ (or in the
    folder unpacked for an import being saved, .new-…), or in imported/ itself, an older version's. Else None."""
    base = os.path.realpath(imported_dir(pid))
    if not _inside(path, base) or os.path.realpath(path) == base:
        return None
    parts = os.path.relpath(os.path.realpath(path), base).split(os.sep)
    if parts[0].isdigit() or parts[0].startswith(UNPACKING):
        parts = parts[1:]
    return os.path.join(*parts) if parts else None


def _own_folder(db, pid, iid, src):
    """(#945) Inside import iid's transaction: the export unpacked for it (src, in imported/) becomes its own folder,
    imported/<iid>/, and its documents' paths go with it (one kept from the export loaded before, by the file's place
    in it, too). Returns the folder."""
    dest = imported_dir(pid, iid)
    if os.path.lexists(dest):   # (an id is never given again, #512: nothing of another import's)
        remove([dest])
    os.replace(src, dest)
    for r in db.execute("SELECT id, path FROM documents WHERE import_id=?", (iid,)).fetchall():
        rel = _in_imported(pid, crypto.dec(r["path"]))
        if rel:
            db.execute("UPDATE documents SET path=? WHERE id=?", (crypto.enc(os.path.realpath(os.path.join(dest, rel))), r["id"]))
    return dest


def left_behind(db, pid, keep=(), older=True):
    """(#945) What's in imported/ that no import is loaded from, inside a transaction: every import's folder but those
    in keep (a dataset import being saved replaces every import: its own, or the folder it's loaded from), what an
    import being saved left half unpacked, and (older) what an older version unpacked into imported/ itself; never one
    a document of the profile is stored in. imported/ itself where all of it goes. A watch file the profile reads among
    them is let go, and the imports' own watch data merged again without it. Returns the paths to remove once it's
    committed (remove)."""
    from . import watch_store
    base = imported_dir(pid)
    if not os.path.isdir(base) or os.path.islink(base):
        return []
    held = [crypto.dec(r["path"]) for r in db.execute("SELECT path FROM documents WHERE profile_id=?", (pid,))]
    names = os.listdir(base)
    out = [p for n in names for p in [os.path.join(base, n)]
           if (older or n.isdigit() or n.startswith(UNPACKING)) and not any(_inside(k, p) for k in keep) and not any(_inside(h, p) for h in held)]
    if len(out) == len(names) and not any(_inside(k, base) for k in keep):
        out = [base]
    r = db.execute("SELECT watch_file FROM profiles WHERE id=?", (pid,)).fetchone()
    if r and r["watch_file"] and any(_inside(r["watch_file"], p) for p in out):
        db.execute("UPDATE profiles SET watch_file=NULL WHERE id=?", (pid,))
        side = watch_store.side_path(pid)   # the imports' merged beside it: merged again into the profile's own file
        watch_store.rebuild(db, pid, prune=False)
        out += [side] if os.path.isfile(side) else []
    return out


def sweep_imported(db):
    """At start-up (#945): what a stop left in each profile's imported/ that no import is loaded from (left_behind): the
    folder of an import replaced or undone, and one half unpacked. What an older version unpacked into imported/ itself
    goes where an import's own folder is there (it replaced that one), or no command-line import is left; else it may be
    the one that's left's, and stays until that one is replaced or undone. And in what's kept, what an export loaded
    before #918 kept that's never kept now (#948, export.LEFT_OUT: its activity log, its cycle records). Returns how
    many folders and files were removed."""
    from . import export
    n = 0
    for (pid,) in db.execute("SELECT id FROM profiles ORDER BY id").fetchall():
        if not os.path.isdir(imported_dir(pid)):
            continue
        live = [r[0] for r in db.execute("SELECT id FROM imports WHERE profile_id=? AND origin='dataset'", (pid,))]
        own = [imported_dir(pid, i) for i in live if os.path.isdir(imported_dir(pid, i))]
        with dbm.transaction(db):
            gone = left_behind(db, pid, keep=own, older=bool(own) or not live)
        remove(gone)
        n += len(gone)
        for d in own + ([imported_dir(pid)] if live and not own else []):
            for name in sorted(export.LEFT_OUT):
                p = os.path.join(d, *name.split("/"))
                if os.path.isfile(p) and not os.path.islink(p):
                    os.remove(p)
                    n += 1
                    with contextlib.suppress(OSError):   # its folder too, cycle/, left empty
                        if os.path.dirname(p) != d:
                            os.rmdir(os.path.dirname(p))
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


DELETING = ".deleting-"   # profiles/.deleting-<id>/: a profile's folder while its deletion is saved


def _deleting_dir(pid):
    return os.path.join(config.DATA_DIR, "profiles", f"{DELETING}{int(pid)}")


def _put_back(aside, folder):
    """A folder moved aside put back where it was (#932): renamed back if nothing is there, else moved in beside
    what was written there meanwhile, entry by entry, never onto it (a file written since is the newer one)."""
    if not os.path.lexists(folder):
        os.replace(aside, folder)
        return
    for n in os.listdir(aside):
        src, dst = os.path.join(aside, n), os.path.join(folder, n)
        if not os.path.lexists(dst):
            os.replace(src, dst)
        elif os.path.isdir(src) and not os.path.islink(src) and os.path.isdir(dst) and not os.path.islink(dst):
            _put_back(src, dst)
    shutil.rmtree(aside, ignore_errors=True)


def delete_profile(db, pid):
    """Remove a profile: its records, settings, its folder (anything imported), document index, memberships and
    activity, and the watch receiver is asked to delete what it keeps for it (watch_keys.forget, #588; the caller
    publishes the secrets file after). Original exports are never touched.
    The lab-name matches its own reviews made go with it (labmap.forget_profile, #591).
    The folder is moved aside only once the write lock is held (#932), so a save that holds it (a pull keeping what its
    source sent, an import's watch data) has written its files first and they go too; and it's removed once the database
    change is committed, with anything written to the profile's folder meanwhile by what doesn't take the lock (a file
    being uploaded). A failure puts it back, beside anything written meanwhile, never onto it; what a stop part-way
    leaves is put back or removed at the next start (sweep).
    Returns what was deleted: {"name", "members", "linked_build", "lab_matches"}."""
    from . import watch_keys
    folder, aside, moved = profile_dir(pid), _deleting_dir(pid), False
    try:
        with dbm.transaction(db):
            p = db.execute("SELECT name, linked_build FROM profiles WHERE id=?", (pid,)).fetchone()
            members = db.execute("SELECT COUNT(*) FROM memberships WHERE profile_id=?", (pid,)).fetchone()[0]
            shutil.rmtree(aside, ignore_errors=True)   # what an earlier deletion of it left: it all goes now
            if os.path.isdir(folder):
                os.replace(folder, aside)
                moved = True
            watch_keys.forget(db, pid)
            from . import labmap   # the lab-name matches its reviews made (#591), while its imports are there
            matches = labmap.forget_profile(db, pid)
            db.execute("DELETE FROM profiles WHERE id=?", (pid,))   # cascades: records, memberships, documents, activity
            if p["linked_build"]:   # don't bring it back from the build on the next start
                dbm.meta_set(db, "build_unlinked", "1")
    except BaseException:
        if moved and os.path.isdir(aside):
            _put_back(aside, folder)
        raise
    for d in (aside, folder):
        shutil.rmtree(d, ignore_errors=True)
    _failed.pop(pid, None)
    records.forget(pid)
    return {"name": p["name"], "members": members, "linked_build": bool(p["linked_build"]), "lab_matches": matches}


def sweep(db):
    """At start-up (#932): what a profile's deletion stopped part-way left in profiles/. A .deleting-<id>/ goes, or is
    put back if its profile is still there (the app stopped before the deletion was saved); a <id>/ that no profile
    owns goes (written after its profile was deleted, by something that doesn't hold the write lock). The profiles' id
    count is moved past them first, so no new profile is given one of their ids (#588). Nothing else there is touched.
    Returns how many folders were removed."""
    base = os.path.join(config.DATA_DIR, "profiles")
    try:
        names = os.listdir(base)
    except OSError:
        return 0
    live = {r[0] for r in db.execute("SELECT id FROM profiles")}
    gone = []
    for n in names:
        path = os.path.join(base, n)
        if os.path.islink(path) or not os.path.isdir(path):
            continue
        pid = n[len(DELETING):] if n.startswith(DELETING) else n
        if not pid.isdigit():
            continue
        if int(pid) in live:
            if n != pid:
                _put_back(path, profile_dir(int(pid)))
            continue
        gone.append((int(pid), path))
    if gone:
        top = max(p for p, _ in gone)
        if not db.execute("UPDATE sqlite_sequence SET seq=MAX(seq, ?) WHERE name='profiles'", (top,)).rowcount:
            db.execute("INSERT INTO sqlite_sequence(name, seq) VALUES('profiles', ?)", (top,))
    for _, path in gone:
        shutil.rmtree(path, ignore_errors=True)
    return len(gone)


def link_build(db, pid):
    """Load the setup build into a profile and keep it in step with rebuilds (undoes the unlinking above)."""
    db.execute("UPDATE profiles SET linked_build=1, build_mtime=NULL, watch_file=IFNULL(watch_file, ?) WHERE id=?",
               (config.WATCH_FILE, pid))
    dbm.meta_set(db, "build_unlinked", None)
    _failed.pop(pid, None)
    return sync_build(db, pid)


def create_profile(db, name, owner_id=None, linked_build=False, watch_file=None):
    """A new profile, its id never one a profile had (AUTOINCREMENT, schema version 39) nor one a folder or receiver
    slot left on disk is named by (#588): that profile's phone data would be the new one's."""
    from . import watch_keys
    if (top := watch_keys.leftover_ids(db)):
        if not db.execute("UPDATE sqlite_sequence SET seq=MAX(seq, ?) WHERE name='profiles'", (top,)).rowcount:
            db.execute("INSERT INTO sqlite_sequence(name, seq) VALUES('profiles', ?)", (top,))
    cur = db.execute("INSERT INTO profiles(name, created, linked_build, watch_file) VALUES(?, ?, ?, ?)",
                     (name, dbm.now(), int(linked_build), watch_file))
    pid = cur.lastrowid
    if owner_id is not None:
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'owner')", (owner_id, pid))
    os.makedirs(profile_dir(pid), exist_ok=True)
    return pid


DEFAULT_NAME = "My records"   # the setup build's profile, until first-run setup names it (main.setup, #492)


def bootstrap(db):
    """First start: the existing single-person data (the setup build) becomes the first profile. Not again after
    its profile was deleted (server.manage link-build loads it into a profile on purpose)."""
    if db.execute("SELECT 1 FROM profiles LIMIT 1").fetchone() or build_version() is None or dbm.meta_get(db, "build_unlinked"):
        return None
    pid = create_profile(db, DEFAULT_NAME, linked_build=True, watch_file=config.WATCH_FILE)
    sync_build(db, pid)
    return pid


MIGRATED = "data-before-v10.json"   # the records file as versions before #934 kept it once it was loaded
IMPORTED = "imported"   # exports server.manage import unpacked into the profile's folder (export.import_archive, #945)
UNPACKING = ".new-"     # in imported/: an export being unpacked for an import not saved yet


def forget_dataset(db, pid, iid):
    """A dataset import (server.manage import) being undone, inside its transaction, after its records and documents
    are dropped (#934): what it was loaded from, kept in the profile's folder, goes with it. That's the export
    export.import_archive unpacked into its own folder in imported/ (its data.json holds every record, beside its
    documents, settings and watch data; #945), and whatever else is there, as no other import is loaded from it (a
    dataset import replaces every import: left_behind, which leaves a folder a document of another import is stored
    in and lets go of a watch file in it); and a records file from before schema version 10 an older version kept.
    Returns the paths to remove once the undo has committed."""
    out = [p for p in (os.path.join(profile_dir(pid), MIGRATED),) if os.path.isfile(p)]
    return out + left_behind(db, pid)


def remove(paths):
    """Remove files and folders (forget_dataset's, once the undo has committed)."""
    for p in paths:
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p, ignore_errors=True)
        else:
            with contextlib.suppress(FileNotFoundError):
                os.remove(p)


def migrate_files(db):
    """Load each profile's data.json, where its records were kept before schema version 10, into the tables as one
    import (the setup build's, for a linked profile, so the next rebuild replaces it), then delete the file: every
    record is in the tables, and undone they'd stay in it (#934; versions before kept it as data-before-v10.json,
    deleted here too). Its document links already point at the profile's documents, which become the import's.
    Returns the number of profiles loaded."""
    n = 0
    for r in db.execute("SELECT id, linked_build FROM profiles ORDER BY id").fetchall():
        pid, path = r["id"], data_path(r["id"])
        with contextlib.suppress(FileNotFoundError):   # loaded by an older version, which kept it (#934)
            os.remove(os.path.join(profile_dir(pid), MIGRATED))
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
        os.remove(path)
    return n
