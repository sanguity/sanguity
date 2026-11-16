"""A profile's import history, and undoing an import (issue #24).

Every import is a row in imports (records.py): the setup build's (origin "build"), a data folder or export loaded with
server.manage import ("dataset"), or an upload read in the browser ("upload", jobs.py). The history lists them, newest
first, with when, who, the importer, the upload's file names and counts, and those undone since (import_undos, schema
version 14, keeps what the history showed for them).

Undo removes everything the import added, and only that, in one transaction:
  - its events, series, results and documents' rows (records.drop: they cascade from the import's row)
  - the upload's raw files, unless another import still uses them (another job's import of the same upload, or a
    document of another import stored in it); for records typed in by hand (origin "manual"), the uploads holding
    the files attached to its documents: the upload is marked discarded in the transaction and its folder
    removed once it commits (uploads.cleanup removes it at the next start if that's interrupted)
  - its watch data (watch_store.forget_import): its own summary set aside and the merged file rebuilt without it
It never touches anything stored apart from the import's rows: settings, other imports, corrections (#33).

By origin:
  upload    undone as above: the profile is as it was before the import.
  manual    the same: records typed in on the import page (routes_manual.py), their files and watch data.
  dataset   undone, but it replaced the profile's records when it was loaded, and those can't be brought back.
  build     refused: the profile is kept in step with the setup build, so the next rebuild would bring it back.
"""
import json, os

from . import activity, crypto, db as dbm, importers as registry, records, uploads, watch_store

FILES_SHOWN = 20   # file names listed per import; the rest are counted
NOTES = {
    "dataset": "This import replaced the profile's records when it was loaded (server.manage import). Undoing it "
               "removes the records it brought; what it replaced can't be brought back.",
    "build": "These records come from the setup build, which this profile is kept in step with: undone, they would "
             "come back at the next rebuild. To leave records out, take their export out of the build (sources.json) "
             "and rebuild.",
}
BY = {"build": activity.SETUP_BUILD, "dataset": activity.COMMAND_LINE}


class Refused(Exception):
    """Why an undo can't be done, for the person, with an HTTP status."""
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def label(fmt):
    if fmt == records.FORMAT:
        return "Sanguity data file"
    mod = registry.get(fmt)
    return mod.LABEL if mod else fmt


def _counts(db, iid):
    return {t: db.execute(f"SELECT COUNT(*) FROM {t} WHERE import_id=?", (iid,)).fetchone()[0]
            for t in ("events", "series", "results", "documents")}


def _names(db, pid, upload_id):
    try:
        names = uploads.manifest(db, pid, upload_id)
    except uploads.UploadError:
        names = []
    return sorted(names)


def _entry(db, pid, imp):
    """What the history shows for a live import (a row of imports)."""
    iid, origin = imp["id"], imp["origin"]
    job = db.execute("""SELECT j.id, j.upload_id, j.summary, j.created_by, u.username FROM jobs j
                        LEFT JOIN users u ON u.id = j.created_by WHERE j.import_id=? ORDER BY j.id LIMIT 1""", (iid,)).fetchone()
    summary = json.loads(crypto.dec(job["summary"])) if job and job["summary"] else {}
    if job:
        by = job["username"] or "a removed account"
    else:
        by = BY.get(origin, activity.COMMAND_LINE)
    names = _names(db, pid, job["upload_id"]) if job else []
    return {"id": iid, "at": imp["at"], "origin": origin, "format": imp["format"], "label": label(imp["format"]),
            "by": by, "job": job["id"] if job else None, "upload": job["upload_id"] if job else None,
            "files": names[:FILES_SHOWN], "file_count": len(names),
            "counts": summary.get("counts") or _counts(db, iid), "range": summary.get("range"),
            "watch": watch_store.has_import(pid, iid),
            "state": "active", "can_undo": origin != "build", "note": NOTES.get(origin)}


def entries(db, pid):
    """The profile's imports, newest first: those it has, then (in the same order of time) those undone."""
    live = [_entry(db, pid, r) for r in db.execute("SELECT id, at, origin, format FROM imports WHERE profile_id=?", (pid,))]
    gone = []
    for r in db.execute("SELECT at, by_name, entry FROM import_undos WHERE profile_id=?", (pid,)):
        e = json.loads(crypto.dec(r["entry"]))
        gone.append({**e, "state": "undone", "can_undo": False, "undone_at": r["at"], "undone_by": r["by_name"]})
    return sorted(live + gone, key=lambda e: (e["at"], e["id"], e["state"] == "active"), reverse=True)


def _still_used(db, pid, iid, upload_id):
    """Whether an import other than iid uses the upload's files: its own import of them, or a document stored there."""
    if db.execute("SELECT 1 FROM jobs WHERE upload_id=? AND import_id IS NOT NULL AND import_id != ?", (upload_id, iid)).fetchone():
        return True
    root = os.path.realpath(uploads.folder(pid, upload_id))
    for r in db.execute("SELECT path FROM documents WHERE profile_id=? AND import_id IS NOT ?", (pid, iid)):
        path = os.path.realpath(crypto.dec(r["path"]))
        if path == root or path.startswith(root + os.sep):
            return True
    return False


def _document_uploads(db, pid, iid):
    """The uploads that hold the files of an import's documents without a job of that import reading them: a
    document typed in by hand (routes_manual.py) is a file sent on its own through the upload routes."""
    paths = [os.path.realpath(crypto.dec(r["path"])) for r in db.execute("SELECT path FROM documents WHERE import_id=?", (iid,))]
    if not paths:
        return []
    out = []
    for r in db.execute("SELECT id FROM uploads WHERE profile_id=? AND state='ready'", (pid,)):
        root = os.path.realpath(uploads.folder(pid, r["id"]))
        if any(p.startswith(root + os.sep) for p in paths):
            out.append(r["id"])
    return out


def undo(db, pid, iid, who, ip=None):
    """Undo import iid of profile pid (who: the signed-in account's row). Returns the history entry, now undone.
    Raises Refused when there's no such import or it can't be undone."""
    imp = db.execute("SELECT id, at, origin, format FROM imports WHERE id=? AND profile_id=?", (iid, pid)).fetchone()
    if not imp:
        raise Refused("No such import. It may have been undone already.", 404)
    if imp["origin"] == "build":
        raise Refused("This import is the setup build's and can't be undone here: " + NOTES["build"])
    entry = _entry(db, pid, imp)
    finish_watch, discard = None, []
    try:
        with dbm.transaction(db):
            ups = [r[0] for r in db.execute("SELECT DISTINCT upload_id FROM jobs WHERE import_id=?", (iid,))]
            ups += [u for u in _document_uploads(db, pid, iid) if u not in ups]   # hand entry's attached files
            records.drop(db, pid, [iid])   # events, series, results, documents: by cascade
            for u in ups:
                if not _still_used(db, pid, iid, u):
                    db.execute("UPDATE uploads SET state='discarded', manifest=NULL WHERE id=? AND profile_id=?", (u, pid))
                    db.execute("DELETE FROM upload_files WHERE upload_id=?", (u,))
                    discard.append(u)
            kept = {k: v for k, v in entry.items() if k not in ("state", "can_undo")}
            db.execute("INSERT INTO import_undos(profile_id, import_id, at, by_id, by_name, entry) VALUES(?, ?, ?, ?, ?, ?)",
                       (pid, iid, dbm.now(), who["id"], who["username"], crypto.enc(json.dumps(kept, separators=(",", ":")))))
            activity.log(db, "undo", who, pid, {"import": iid, "origin": imp["origin"], "importer": imp["format"],
                                                "label": entry["label"], "counts": entry["counts"], "uploads": discard}, ip=ip)
            finish_watch = watch_store.forget_import(db, pid, iid)   # last: files can't roll back with the rest
    except BaseException:
        if finish_watch:
            finish_watch(False)
        raise
    if finish_watch:
        finish_watch(True)
    for u in discard:
        uploads._rmtree(uploads.folder(pid, u))
    return {**entry, "state": "undone", "can_undo": False, "undone_at": dbm.now(), "undone_by": who["username"]}
