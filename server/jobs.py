"""Imports that run in the background, so a large export doesn't hold up the page that uploaded it.

An import of an upload (uploads.py) is a row in jobs (schema version 12), which the page polls:

  queued -> running (stage: detecting -> parsing -> saving) -> done | failed

One worker thread takes jobs in turn from an in-process queue, which is enough: SQLite has a single writer anyway.
A job opens the upload's files decrypted (uploads.open_plain), picks the importer (importers.match, unless one was
asked for), parses, checks the output against the contract (importers.check), then saves it in one transaction:
the records (records.store, origin "upload", adding to the profile's records) with their document links, and the
job's own "done". Anything that goes wrong rolls all of that back, so a failed import leaves nothing behind. The job
then says why in plain language; the traceback goes only to the log.

A document link in the importer's output names a file in the upload ({"type": "file", "url": "<path in it>"}); it is
indexed to the stored, sealed copy in profiles/<id>/uploads/<upload id>/ (uploads.stored_files), never to the
decrypted temporary one. The stored copy has a random name, so the document's type comes from the path in the upload.

Jobs left queued or running when the app stopped never committed, so at start-up (recover) they are marked failed.
"""
import collections, contextlib, json, logging, os, posixpath, queue, re, threading

from . import activity, crypto, db as dbm, importers as registry, profiles as prof, records

log = logging.getLogger("sanguity.jobs")

ISSUES = "https://github.com/sanguity/sanguity/issues"
INTERRUPTED = "The import was interrupted; nothing was saved. Start it again."
UPLOAD_ID = re.compile(r"^[0-9]{1,18}$")   # uploads are numbered (uploads.create)
RECENT = 20   # jobs listed per profile


class Failed(Exception):
    """A reason to show as it is."""


def _uploads():
    from . import uploads   # imported when used: uploads.py imports profiles, as this does
    return uploads


def _refused():
    """The exception uploads.py raises for an upload that doesn't exist or isn't ready, with a message to show."""
    return getattr(_uploads(), "UploadError", LookupError)


def upload_exists(db, pid, upload_id):
    if not UPLOAD_ID.match(str(upload_id)):
        return False
    try:
        _uploads().manifest(db, pid, int(upload_id))
        return True
    except _refused():
        return False


def no_match_message():
    labels = ", ".join(m.LABEL for m in registry.importers())
    return ("Sanguity didn't recognise these files." + (f" It looked for: {labels}." if labels else "") +
            " If they're from a patient portal or app it can't read yet, open an issue at " + ISSUES +
            " saying where they came from, with an anonymised sample (made-up names, dates and values).")


# ---------------------------------------------------------------- the queue and its worker

_q, _thread, _ips = None, None, {}   # _ips: job id -> address of whoever started it, for the activity log


def recover(db):
    """At start-up: jobs a stop left queued or running never saved anything; mark them failed. Returns how many."""
    return db.execute("UPDATE jobs SET state='failed', stage=NULL, message=?, finished=? WHERE state IN ('queued', 'running')",
                      (crypto.enc(INTERRUPTED), dbm.now())).rowcount


def start():
    """Start the worker (in the app's lifespan). A worker from an earlier start finishes its job and stops."""
    global _q, _thread
    _q = queue.Queue()
    _thread = threading.Thread(target=_work, args=(_q,), name="sanguity-jobs", daemon=True)
    _thread.start()


def stop(timeout=5):
    """Ask the worker to stop after the job in hand. A job still running when the process ends is recovered as failed."""
    global _q, _thread
    if _q is not None:
        _q.put(None)
    if _thread is not None:
        _thread.join(timeout)
    _q = _thread = None


def submit(db, pid, upload_id, uid, importer=None, ip=None):
    """Queue an import of an upload; returns the job's id."""
    jid = db.execute("INSERT INTO jobs(profile_id, upload_id, created_by, state, importer, created) VALUES(?, ?, ?, 'queued', ?, ?)",
                     (pid, upload_id, uid, importer, dbm.now())).lastrowid
    _ips[jid] = ip
    if _q is not None:
        _q.put(jid)
    return jid


def _work(q):
    while (jid := q.get()) is not None:
        try:
            run(jid)
        except Exception:   # run() records its own failures; this is only for a failure to record one
            log.exception("import job %s", jid)


# ---------------------------------------------------------------- one job

def run(jid):
    db = dbm.connect()
    try:
        j = db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if not j or j["state"] != "queued":
            return
        db.execute("UPDATE jobs SET state='running', stage='detecting', started=? WHERE id=?", (dbm.now(), jid))
        try:
            _import(db, j)
        except Exception as e:
            stage = (db.execute("SELECT stage FROM jobs WHERE id=?", (jid,)).fetchone() or {"stage": None})["stage"]
            log.exception("import job %s (profile %s, upload %s) failed while %s", jid, j["profile_id"], j["upload_id"], stage)
            db.execute("UPDATE jobs SET state='failed', message=?, finished=? WHERE id=?",
                       (crypto.enc(_explain(e, stage)), dbm.now(), jid))
    finally:
        _ips.pop(jid, None)
        db.close()


def _explain(e, stage):
    if isinstance(e, (Failed, ValueError)):   # ValueError: an importer's own plain-language reason (the contract)
        msg = str(e).strip() or "These files couldn't be read."
    elif stage == "saving":
        msg = "The records were read but couldn't be saved."
    else:
        msg = "The importer stopped with an error while reading these files."
    if "nothing was saved" not in msg.lower():
        msg += " Nothing was saved."
    if not isinstance(e, (Failed, ValueError)):
        msg += " The details are in the app's log."
    return msg


def _stage(db, jid, stage, importer=None):
    db.execute("UPDATE jobs SET stage=?, importer=IFNULL(?, importer) WHERE id=?", (stage, importer, jid))


def _import(db, j):
    jid, pid, upload_id = j["id"], j["profile_id"], j["upload_id"]
    up = _uploads()
    with contextlib.ExitStack() as stack:
        try:
            files = stack.enter_context(up.open_plain(db, pid, upload_id))
        except _refused() as e:   # not finished, or discarded since
            raise Failed(str(e)) from None
        mod, data, warnings = _read(db, jid, j["importer"], files)
        names = set(files)

    _stage(db, jid, "saving")
    sealed = {rel: path for uid, rel, path in up.stored_files(db, pid) if uid == upload_id and rel in names}
    for d in data["details"].values():   # stored under a random name, so the type comes from the path in the upload
        if isinstance(d, dict) and d.get("type") == "file" and isinstance(d.get("url"), str) and not d.get("ext"):
            d["ext"] = posixpath.splitext(d["url"].split("#", 1)[0])[1].lstrip(".").lower()

    def stored(url):
        """The sealed copy of the file a document link names, or None."""
        path = sealed.get(url.split("#", 1)[0]) if isinstance(url, str) else None
        return os.path.realpath(path) if path and os.path.isfile(path) else None

    summary = summarise(data)
    who = db.execute("SELECT id, username FROM users WHERE id=?", (j["created_by"],)).fetchone() if j["created_by"] else None
    with dbm.transaction(db):
        keep, n = prof.index_documents(db, pid, data["details"], stored)
        summary["counts"]["documents"] = n
        iid = records.store(db, pid, data, "upload", fmt=mod.NAME)
        prof.link_documents(db, iid, keep)
        activity.log(db, "import", who, pid, {"importer": mod.NAME, "counts": summary["counts"], "job": jid},
                     ip=_ips.get(jid), actor=None if who else "removed account")
        db.execute("""UPDATE jobs SET state='done', stage=NULL, import_id=?, warnings=?, summary=?, finished=? WHERE id=?""",
                   (iid, crypto.enc(json.dumps(warnings)), crypto.enc(json.dumps(summary)), dbm.now(), jid))


def _read(db, jid, name, files):
    """Pick the importer (or take the one asked for), parse and check: (module, data, warnings)."""
    if not files:
        raise Failed("The upload is empty.")
    tied = []
    if name:
        mod = registry.get(name)
        if not mod:
            raise Failed(f"There's no importer called {name!r}.")
    else:
        hit = registry.match(files)
        mod, tied = hit["importer"], hit["tied"]
        if not mod:
            raise Failed(no_match_message())
    _stage(db, jid, "parsing", mod.NAME)
    out = mod.parse(files)
    problems = registry.check(out, files)
    if problems:
        raise Failed(f"The {mod.LABEL} importer returned records Sanguity can't load: " + "; ".join(problems[:5]) + ".")
    warnings = [str(w) for w in out.get("warnings") or []]
    if tied:
        warnings.insert(0, f"These files also look like {', '.join(m.LABEL for m in tied)}; read them as {mod.LABEL}.")
    return mod, out["data"], warnings


def summarise(data):
    """What an import brought: events by kind (visible ones; lab results are counted as results), lab series and
    results, and the first and last day it covers."""
    kinds = collections.Counter(str(e.get("k")) for e in data["events"] if isinstance(e, dict) and not e.get("hidden"))
    labs = [s for s in data["labs"] if isinstance(s, dict)]
    days = [str(e.get("t"))[:10] for e in data["events"] if isinstance(e, dict) and e.get("t")]
    days += [str(p[0])[:10] for s in labs for p in s.get("pts") or [] if isinstance(p, list) and p and p[0]]
    days = [d for d in days if re.match(r"^\d{4}-\d{2}-\d{2}$", d)]
    counts = {"events": sum(kinds.values()), "kinds": dict(sorted(kinds.items())), "series": len(labs),
              "results": sum(len(s.get("pts") or []) for s in labs)}
    return {"counts": counts, "range": {"from": min(days), "to": max(days)} if days else None}


# ---------------------------------------------------------------- reading jobs

def describe(r):
    mod = registry.get(r["importer"]) if r["importer"] else None
    summary = json.loads(crypto.dec(r["summary"])) if r["summary"] else {}
    return {"id": r["id"], "upload_id": r["upload_id"], "state": r["state"], "stage": r["stage"],
            "importer": r["importer"], "label": mod.LABEL if mod else r["importer"],
            "counts": summary.get("counts"), "range": summary.get("range"),
            "warnings": json.loads(crypto.dec(r["warnings"])) if r["warnings"] else [],
            "message": crypto.dec(r["message"]) if r["message"] else None, "import_id": r["import_id"],
            "created": r["created"], "started": r["started"], "finished": r["finished"]}


def get(db, pid, jid):
    r = db.execute("SELECT * FROM jobs WHERE id=? AND profile_id=?", (jid, pid)).fetchone()
    return describe(r) if r else None


def recent(db, pid, limit=RECENT):
    return [describe(r) for r in db.execute("SELECT * FROM jobs WHERE profile_id=? ORDER BY id DESC LIMIT ?", (pid, limit))]


def for_upload(db, pid, upload_id):
    """The upload's jobs, newest first: (id, state)."""
    return [tuple(r) for r in db.execute("SELECT id, state FROM jobs WHERE profile_id=? AND upload_id=? ORDER BY id DESC",
                                         (pid, upload_id))]
