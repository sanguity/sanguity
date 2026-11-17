"""Imports that run in the background, so a large export doesn't hold up the page that uploaded it.

An import of an upload (uploads.py) is a row in jobs (schema versions 12 and 13), which the page polls:

  queued -> running (detecting, parsing) -> preview -> confirm: queued -> running (saving) -> done | failed
                                                    -> discard: discarded (the upload too, unless another job uses it)

The preview (#22) holds the parsed records, sealed in profiles/<id>/previews/<job id> (crypto.write_file), and says
what they would add: counts by kind, the date range, and the duplicates of what the profile has (preview.py), which
are skipped unless chosen to keep anyway, and lab names lab-aliases.json doesn't know, with a series to put each in
(labmap.py: remembered choices are applied first, before duplicates are found). Confirming looks for duplicates again (the profile may have changed since)
and runs importers.check() on what's left before saving it. The file goes once the job is done, failed or discarded.

One worker thread takes jobs in turn from an in-process queue, which is enough: SQLite has a single writer anyway.
A job opens the upload's files decrypted (uploads.open_plain), picks the importer (importers.match, unless one was
asked for), parses, checks the output against the contract (importers.check) and previews it; once confirmed, it
saves it in one transaction:
the records (records.store, origin "upload", adding to the profile's records) with their document links, and the
job's own "done". Anything that goes wrong rolls all of that back, so a failed import leaves nothing behind. The job
then says why in plain language; the traceback goes only to the log.

A document link in the importer's output names a file in the upload ({"type": "file", "url": "<path in it>"}); it is
indexed to the stored, sealed copy in profiles/<id>/uploads/<upload id>/ (uploads.stored_files), never to the
decrypted temporary one. The stored copy has a random name, so the document's type comes from the path in the upload.

Jobs left queued or running when the app stopped never committed, so at start-up (recover) they are marked failed,
except one that was saving a confirmed preview: it goes back to "preview".
"""
import collections, contextlib, json, logging, os, posixpath, queue, re, threading

from . import activity, config, crossmatch, crypto, db as dbm, importers as registry, labmap, preview, profiles as prof, records, uploads, watch_store

log = logging.getLogger("sanguity.jobs")

ISSUES = "https://github.com/sanguity/sanguity/issues/new?template=portal-export.yml"   # the "My portal's export doesn't import" form
INTERRUPTED = "The import was interrupted; nothing was saved. Start it again."
UPLOAD_ID = re.compile(r"^[0-9]{1,18}$")   # uploads are numbered (uploads.create)
RECENT = 20   # jobs listed per profile


class Failed(Exception):
    """A reason to show as it is."""


def upload_exists(db, pid, upload_id):
    if not UPLOAD_ID.match(str(upload_id)):
        return False
    try:
        uploads.manifest(db, pid, int(upload_id))
        return True
    except uploads.UploadError:
        return False


def no_match_message():
    labels = ", ".join(m.LABEL for m in registry.importers())
    return ("Sanguity didn't recognise these files." + (f" It looked for: {labels}." if labels else "") +
            " If they're from a patient portal or app it can't read yet, open an issue at " + ISSUES +
            " saying where they came from, with an anonymised sample (made-up names, dates and values).")


# ---------------------------------------------------------------- the queue and its worker

_q, _thread, _ips = None, None, {}   # _ips: job id -> address of whoever started it, for the activity log


def recover(db):
    """At start-up: jobs a stop left queued or running never saved anything. One that was saving a confirmed
    preview goes back to its preview (still there), to be confirmed again; the others are marked failed. Preview
    files no waiting job owns are removed. Returns how many failed."""
    for r in db.execute("SELECT id, profile_id FROM jobs WHERE state IN ('queued', 'running') AND stage='saving'").fetchall():
        if os.path.isfile(preview_path(r["profile_id"], r["id"])):
            db.execute("UPDATE jobs SET state='preview', stage=NULL WHERE id=?", (r["id"],))
    n = db.execute("UPDATE jobs SET state='failed', stage=NULL, message=?, finished=? WHERE state IN ('queued', 'running')",
                   (crypto.enc(INTERRUPTED), dbm.now())).rowcount
    waiting = {(r[0], r[1]) for r in db.execute("SELECT profile_id, id FROM jobs WHERE state='preview'")}
    base = os.path.join(config.DATA_DIR, "profiles")
    for p in os.listdir(base) if os.path.isdir(base) else ():
        folder = os.path.join(base, p, "previews")
        if not p.isdigit() or not os.path.isdir(folder):
            continue
        for f in os.listdir(folder):
            if not (f.isdigit() and (int(p), int(f)) in waiting):
                _remove(os.path.join(folder, f))
    return n


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
        saving = j["stage"] == "saving"   # confirmed after its preview
        db.execute("UPDATE jobs SET state='running', stage=?, started=IFNULL(started, ?) WHERE id=?",
                   ("saving" if saving else "detecting", dbm.now(), jid))
        try:
            _save(db, j) if saving else _import(db, j)
        except Exception as e:
            stage = (db.execute("SELECT stage FROM jobs WHERE id=?", (jid,)).fetchone() or {"stage": None})["stage"]
            log.exception("import job %s (profile %s, upload %s) failed while %s", jid, j["profile_id"], j["upload_id"], stage)
            db.execute("UPDATE jobs SET state='failed', message=?, finished=? WHERE id=?",
                       (crypto.enc(_explain(e, stage)), dbm.now(), jid))
            _remove(preview_path(j["profile_id"], jid))
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
    """Detect, parse and check, then stop: the parsed records go into the job's sealed preview file, and the job
    waits in state "preview" for confirm() or discard()."""
    jid, pid, upload_id = j["id"], j["profile_id"], j["upload_id"]
    with contextlib.ExitStack() as stack:
        try:
            files = stack.enter_context(uploads.open_plain(db, pid, upload_id))
        except uploads.UploadError as e:   # not finished, or discarded since
            raise Failed(str(e)) from None
        mod, data, warnings = _read(db, jid, j["importer"], files)
        names = sorted(files)

    for d in data["details"].values():   # stored under a random name, so the type comes from the path in the upload
        if isinstance(d, dict) and d.get("type") == "file" and isinstance(d.get("url"), str) and not d.get("ext"):
            d["ext"] = posixpath.splitext(d["url"].split("#", 1)[0])[1].lstrip(".").lower()
    watch = data.pop("watch", None)   # daily wearable data: saved with watch_store, not as records
    labs = labmap.plan(db, pid, mod.NAME, data)   # lab names to match; remembered ones go before duplicates are found
    named = labmap.apply(data, labs["auto"])
    dups = preview.find(db, pid, mod.NAME, named)
    report = preview.report(named, dups, summarise)
    report["possible"] = crossmatch.public(crossmatch.for_preview(db, pid, named, dups))   # from other sources (#96)
    report["labs"], report["lab_targets"] = labs["listed"], labs["targets"]
    if watch:
        report["counts"].update(_watch_counts(watch))
        report["new"] = True
    path = preview_path(pid, jid)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    crypto.write_file(path, json.dumps({"importer": mod.NAME, "data": data, "watch": watch, "paths": names, "labs": labs},
                                       separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    try:
        n = db.execute("UPDATE jobs SET state='preview', stage=NULL, warnings=?, summary=? WHERE id=? AND state='running'",
                       (crypto.enc(json.dumps(warnings)), crypto.enc(json.dumps(report)), jid)).rowcount
    except BaseException:
        _remove(path)
        raise
    if not n:
        _remove(path)


def _watch_counts(watch):
    days = {str(p[0]) for m in watch.get("metrics") or [] if isinstance(m, dict)
            for p in m.get("pts") or [] if isinstance(p, list) and p}
    return {"watch_days": len(days), "workouts": len(watch.get("exercise") or []), **watch_store.ecg.counts(watch)}   # ECGs (#182)


def _save(db, j):
    """Save a confirmed preview: what it found, less what the profile has by now (unless chosen to keep anyway),
    checked again, in one transaction with the job's "done"."""
    jid, pid, upload_id = j["id"], j["profile_id"], j["upload_id"]
    path = preview_path(pid, jid)
    try:
        held = json.loads(crypto.read_file(path))
    except FileNotFoundError:
        raise Failed("The preview of this import is gone; start it again.") from None
    name = held["importer"]
    mod = registry.get(name)
    label = mod.LABEL if mod else name
    data, watch, names = held["data"], held.get("watch"), set(held.get("paths") or ())
    keep = json.loads(j["keep"]) if j["keep"] else []
    sent = json.loads(crypto.dec(j["labs"])) if j["labs"] else {}
    raw, data = data, labmap.apply(data, labmap.choices(held.get("labs"), sent))   # series names as matched
    dups = preview.keep(data, preview.find(db, pid, name, data), keep)
    new = preview.trim(data, dups)
    problems = registry.check({"data": new, "warnings": []}, names)   # a trimmed lab day must still hold together
    if problems:
        raise Failed(f"Without the duplicates, the {label} records couldn't be loaded: " + "; ".join(problems[:5]) + ".")
    skipped = preview.report(data, dups, summarise)["duplicates"]
    chosen = set(json.loads(j["same"]) if j["same"] else [])   # possible matches confirmed as the same (#96)
    same = [x for x in crossmatch.for_preview(db, pid, data, dups) if x["ref"] in chosen] if chosen else []

    sealed = {rel: p for uid, rel, p in uploads.stored_files(db, pid) if uid == upload_id and rel in names}

    def stored(url):
        """The sealed copy of the file a document link names, or None."""
        p = sealed.get(url.split("#", 1)[0]) if isinstance(url, str) else None
        return os.path.realpath(p) if p and os.path.isfile(p) else None

    summary = summarise(new)
    summary["counts"]["documents"] = 0
    summary["skipped"] = {k: skipped[k] for k in ("events", "results", "other")}
    if same:
        summary["linked"] = len(same)
    who_id = j["confirmed_by"] or j["created_by"]
    who = db.execute("SELECT id, username FROM users WHERE id=?", (who_id,)).fetchone() if who_id else None
    with dbm.transaction(db):
        iid = None
        # all duplicates: nothing to add, not even an empty import; but watch data needs an import to be undone by
        if preview.adds_anything(new) or watch:
            keepdocs, n = prof.index_documents(db, pid, new["details"], stored)
            summary["counts"]["documents"] = n
            iid = records.store(db, pid, new, "upload", fmt=name)
            prof.link_documents(db, iid, keepdocs)
        if watch:   # last, so a failure before it leaves no file behind
            saved = watch_store.save_import(db, pid, watch, import_id=iid)
            summary["counts"].update(watch_days=saved["days"], workouts=saved["workouts"],
                                     **{k: saved[k] for k in ("ecgs", "rhythm") if saved.get(k)})   # ECGs (#182)
        for x in same if iid else ():
            crossmatch.decide(db, pid, who["id"] if who else None, x["other_key"], x["key"], True,
                              [x["other"], {k: x[k] for k in ("name", "t", "value", "unit")}])
        remembered = labmap.remember(db, name, held.get("labs"), sent, raw, who)
        matched = sum(1 for t in labmap.choices(held.get("labs"), sent).values() if t)
        summary["lab_names"] = {"matched": matched, "remembered": remembered}
        activity.log(db, "import", who, pid, {"importer": name, "counts": summary["counts"], "job": jid,
                                              "skipped": summary["skipped"], "kept": len(keep),
                                              **({"same": len(same)} if same else {}),
                                              **({"labs": summary["lab_names"]} if matched or remembered else {})},
                     ip=_ips.get(jid), actor=None if who else "removed account")
        db.execute("UPDATE jobs SET state='done', stage=NULL, import_id=?, summary=?, finished=? WHERE id=?",
                   (iid, crypto.enc(json.dumps(summary)), dbm.now(), jid))
        from . import sync   # the first pull of a connected source (#91): its import and cursor go to the connection
        sync.first_saved(db, jid, iid)
    _remove(path)


# ---------------------------------------------------------------- preview: confirm or discard

def preview_path(pid, jid):
    """The job's parsed records, sealed (crypto.write_file), while it waits in "preview"."""
    return os.path.join(prof.profile_dir(pid), "previews", str(int(jid)))


def _remove(path):
    with contextlib.suppress(FileNotFoundError):
        os.remove(path)


class Refused(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def confirm(db, pid, jid, uid, keep=None, ip=None, labs=None, same=None):
    """Save a previewed import, in the background like the parse: duplicates are skipped, except those whose refs
    (preview.keep) are in keep; lab series the preview listed go under the names in labs ({ref: series name or
    None}, labmap.py); the possible matches from other sources whose refs are in same are linked as the same result
    once it's saved (crossmatch.py). Refused unless the job is waiting in "preview"."""
    keep = [str(r)[:40] for r in keep or []][:20000]
    labs = {str(k)[:20]: labmap.clean_target(v) for k, v in list((labs or {}).items())[:5000]}
    same = [str(r)[:40] for r in same or [] if str(r).startswith("m:")][:crossmatch.LISTED]
    n = db.execute("""UPDATE jobs SET state='queued', stage='saving', confirmed_by=?, keep=?, labs=?, same=? WHERE id=?
                      AND profile_id=? AND state='preview'""",
                   (uid, json.dumps(keep) if keep else None, crypto.enc(json.dumps(labs)) if labs else None,
                    json.dumps(same) if same else None, jid, pid)).rowcount
    if not n:
        _refuse_not_preview(db, pid, jid)
    _ips[jid] = ip
    if _q is not None:
        _q.put(jid)


def discard(db, pid, jid, who, ip=None):
    """Throw a previewed import away: its preview file, and its upload too unless another job uses it."""
    with dbm.transaction(db):
        r = db.execute("SELECT * FROM jobs WHERE id=? AND profile_id=?", (jid, pid)).fetchone()
        if not r or r["state"] != "preview":
            _refuse_not_preview(db, pid, jid)
        db.execute("UPDATE jobs SET state='discarded', finished=?, confirmed_by=? WHERE id=?", (dbm.now(), who["id"], jid))
        others = db.execute("""SELECT 1 FROM jobs WHERE upload_id=? AND id != ?
                               AND state IN ('queued', 'running', 'preview', 'done') LIMIT 1""", (r["upload_id"], jid)).fetchone()
        activity.log(db, "discard", who, pid, {"importer": r["importer"], "job": jid, "upload": r["upload_id"]}, ip)
    _remove(preview_path(pid, jid))
    if not others:
        with contextlib.suppress(uploads.UploadError):
            uploads.discard(db, pid, r["upload_id"])


def _refuse_not_preview(db, pid, jid):
    r = db.execute("SELECT state FROM jobs WHERE id=? AND profile_id=?", (jid, pid)).fetchone()
    if not r:
        raise Refused("No such import.", 404)
    raise Refused({"queued": "This import is being saved already.", "running": "This import is still being read.",
                   "done": "This import has been saved already.", "discarded": "This import was cancelled.",
                   "failed": "This import failed; start it again."}.get(r["state"], "This import can't be changed now."))


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
    # #179: the profile-wide lists' allergies, other problems and upcoming visits, for the preview to list
    probs = [p for p in data.get("problems") or [] if isinstance(p, dict)]
    lists = {"allergies": sum(1 for p in probs if p.get("type") == "Allergy"),
             "problems": sum(1 for p in probs if p.get("type") != "Allergy"),
             "upcoming": sum(1 for u in data.get("upcoming") or [] if isinstance(u, dict))}
    counts.update({k: n for k, n in lists.items() if n})
    return {"counts": counts, "range": {"from": min(days), "to": max(days)} if days else None}


# ---------------------------------------------------------------- reading jobs

def describe(r):
    mod = registry.get(r["importer"]) if r["importer"] else None
    summary = json.loads(crypto.dec(r["summary"])) if r["summary"] else {}
    return {"id": r["id"], "upload_id": r["upload_id"], "state": r["state"], "stage": r["stage"],
            "importer": r["importer"], "label": mod.LABEL if mod else r["importer"],
            "counts": summary.get("counts"), "range": summary.get("range"), "total": summary.get("total"),
            "duplicates": summary.get("duplicates"), "new": summary.get("new"), "skipped": summary.get("skipped"),
            "labs": summary.get("labs"), "lab_targets": summary.get("lab_targets"), "lab_names": summary.get("lab_names"),
            "possible": summary.get("possible") or [], "linked": summary.get("linked"),
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
