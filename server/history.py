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
  - the lab-name matches its review made (labmap.forget, #500), unless another import has been saved with them since
    or an admin has changed them: the next import of those names asks again. The history entry counts them
    ("lab_matches"), for the question asked first
It never touches anything else stored apart from the import's rows: settings, other imports, corrections (#33).

By origin:
  upload    undone as above: the profile is as it was before the import.
  manual    the same: records typed in on the import page (routes_manual.py), their files and watch data.
  sync      a connected source's scheduled pull (sync.py): what it added goes, and the records it updated in place
            get back what they were (sync_changes); the connection keeps pulling what's new from then on. Two
            more come from a connection (imports.note says which, #99, #101): taking out the records of a kind it
            brought (undone, they come back) and an attachment fetched on request (undone, the file goes and the
            attachment is listed as left out again).
  dataset   undone, but it replaced the profile's records when it was loaded, and those can't be brought back.
  build     refused: the profile is kept in step with the setup build, so the next rebuild would bring it back.
"""
import collections, json, os

from . import activity, crypto, db as dbm, importers as registry, labmap, records, uploads, watch_store

FILES_SHOWN = 20   # file names listed per import; the rest are counted
NOTES = {
    "dataset": "This import replaced the profile's records when it was loaded (server.manage import). Undoing it "
               "removes the records it brought; what it replaced can't be brought back.",
    "build": "These records come from the setup build, which this profile is kept in step with: undone, they would "
             "come back at the next rebuild. To leave records out, their export has to be taken out of the build, which "
             "an admin does on the server.",
}
BY = {"build": activity.SETUP_BUILD, "dataset": activity.COMMAND_LINE, "sync": activity.SCHEDULED_SYNC}


class Refused(Exception):
    """Why an undo can't be done, for the person, with an HTTP status."""
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


# imports named for how they got there rather than the format they're in; the page's Source lines use the same (#100)
BY_ORIGIN = {"build": "Setup build", "manual": "Typed in"}


def label(fmt, origin=None):
    if origin in BY_ORIGIN:
        return BY_ORIGIN[origin]
    if fmt == records.FORMAT:
        return "Sanguity data file"
    mod = registry.get(fmt)
    return mod.LABEL if mod else fmt


def _counts(db, iid, typed=False):
    """An import's rows, counted, for one with no job to say what it brought. typed: records typed in (#513), counted
    as what was typed, by kind as a job counts them (kinds), and not the lab draws made for typed results or their
    results' items (counted as results): one Ferritin result is "1 lab result in 1 test", not "2 events"."""
    out = {t: db.execute(f"SELECT COUNT(*) FROM {t} WHERE import_id=?", (iid,)).fetchone()[0]
           for t in ("events", "series", "results", "documents")}
    if typed:
        kinds = collections.Counter(json.loads(crypto.dec(r[0])).get("k") for r in db.execute("SELECT body FROM events WHERE import_id=?", (iid,)))
        kinds = {str(k): n for k, n in sorted(kinds.items(), key=lambda x: str(x[0])) if k not in ("lab", "labitem")}
        out.update(events=sum(kinds.values()), kinds=kinds)
        # one saved before the save's counts were kept (#517): its problems, upcoming appointments and payments, which
        # are in the profile's lists (imports.meta), not rows
        r = db.execute("SELECT meta FROM imports WHERE id=?", (iid,)).fetchone()
        meta = records._dec(r["meta"]) if r and r["meta"] else {}
        pays = (meta.get("outOfPocket") or {}).get("payments") if isinstance(meta.get("outOfPocket"), dict) else None
        probs = [p for p in meta.get("problems") or [] if isinstance(p, dict)]
        lists = {"allergies": sum(1 for p in probs if p.get("type") == "Allergy"), "problems": sum(1 for p in probs if p.get("type") != "Allergy"),
                 "upcoming": len(meta.get("upcoming") or []), "payments": len(pays or [])}
        out.update({k: n for k, n in lists.items() if n})
    return out


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
    if job and (job["username"] or origin != "sync"):
        by = job["username"] or "a removed account"
    elif origin == "manual":   # typed in: no job; the import names its account (imports.created_by, #325). None for one
        # typed in before that was kept, or once the account is removed: no name, never "command line"
        a = db.execute("SELECT u.username FROM imports i JOIN users u ON u.id = i.created_by WHERE i.id=?", (iid,)).fetchone()
        by = a["username"] if a else None
    else:
        by = BY.get(origin, activity.COMMAND_LINE)
    names = _names(db, pid, job["upload_id"]) if job else []
    note = None
    if origin in ("sync", "manual"):   # typed in: the save's own counts, of what was typed (routes_manual.py, #517)
        r = db.execute("SELECT note FROM imports WHERE id=?", (iid,)).fetchone()
        note = json.loads(crypto.dec(r["note"])) if r and r["note"] else None
    if note:
        summary = {**summary, "counts": note.get("counts")}
    if not summary.get("counts") and watch_store.has_import(pid, iid):   # typed in (no job): its watch data counted too (#224)
        summary = {**summary, "counts": {**_counts(db, iid, origin == "manual"), **watch_store.import_counts(pid, iid)}}
    own =db.execute("SELECT name FROM imports WHERE id=?", (iid,)).fetchone()   # its own name (#38), if given
    return {"id": iid, "name": crypto.dec(own["name"]) if own and own["name"] else None, "at": imp["at"], "origin": origin, "format": imp["format"],
            "label": (note or {}).get("label") or label(imp["format"], origin), "removal": bool(note and note.get("removed")),
            "by": by, "job": job["id"] if job else None, "upload": job["upload_id"] if job else None,
            "files": names[:FILES_SHOWN], "file_count": len(names),
            "counts": summary.get("counts") or _counts(db, iid, origin == "manual"), "range": summary.get("range"),
            "watch": watch_store.has_import(pid, iid), "lab_matches": labmap.forgettable(db, iid),   # (#500)
            "connection": _source(db, imp["connection_id"]), "changed": summary.get("changed"),
            "state": "active", "can_undo": origin != "build", "note": NOTES.get(origin)}


def _source(db, cid):
    """The connected source (#91) an import came from, by name, or None."""
    r = db.execute("SELECT source FROM connections WHERE id=?", (cid,)).fetchone() if cid else None
    return r["source"] if r else None


def entries(db, pid):
    """The profile's imports, newest first: those it has, then (in the same order of time) those undone."""
    live = [_entry(db, pid, r) for r in db.execute("SELECT id, at, origin, format, connection_id FROM imports WHERE profile_id=?", (pid,))]
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


def undo(db, pid, iid, who, ip=None, at=None):
    """Undo import iid of profile pid (who: the signed-in account's row). at: when the import asked about was saved
    (its history entry's "at"), so an id that came again before schema version 37 (#512) never undoes another import
    than the one asked. Returns the history entry, now undone. Raises Refused when there's no such import, it isn't
    the one asked, or it can't be undone."""
    imp = db.execute("SELECT id, at, origin, format, connection_id FROM imports WHERE id=? AND profile_id=?", (iid, pid)).fetchone()
    if not imp or (at is not None and imp["at"] != at):
        gone = db.execute("SELECT 1 FROM import_undos WHERE profile_id=? AND import_id=?", (pid, iid)).fetchone()
        raise Refused("This import was undone already." if gone else "No such import. It may have been undone already.", 404)
    if imp["origin"] == "build":
        raise Refused("This import is the setup build's and can't be undone here: " + NOTES["build"])
    entry = _entry(db, pid, imp)
    finish_watch, discard = None, []
    try:
        with dbm.transaction(db):
            # asked again under the write lock (#542): undone, or its profile deleted, since it was read above
            if not db.execute("SELECT 1 FROM imports WHERE id=? AND profile_id=? AND at=?", (iid, pid, imp["at"])).fetchone():
                raise Refused("No such import. It may have been undone already.", 404)
            ups = [r[0] for r in db.execute("SELECT DISTINCT upload_id FROM jobs WHERE import_id=?", (iid,))]
            ups += [u for u in _document_uploads(db, pid, iid) if u not in ups]   # hand entry's attached files
            if imp["origin"] == "sync":   # a connected source's sync: what it changed in place goes back first
                from . import sync
                sync.unapply(db, iid)
            forgot = labmap.forget(db, iid)   # before its row goes (labmaps.import_id is set null with it)
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
                                                "label": entry["label"], "counts": entry["counts"], "uploads": discard,
                                                **({"lab_matches": forgot} if forgot else {})}, ip=ip)
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
