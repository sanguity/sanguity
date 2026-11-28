"""Imports that run in the background, so a large export doesn't hold up the page that uploaded it.

An import of an upload (uploads.py) is a row in jobs (schema versions 12 and 13), which the page polls:

  queued -> running (detecting, parsing) -> preview -> confirm: queued -> running (saving) -> done | failed
                                                    -> discard: discarded (the upload too, unless another job uses it)
  running, holding nothing to import (#542): discarded likewise, at once, with a message saying why
  saving, all of it in the profile by now (#547): discarded likewise, saying so (a connected source's first pull: done)

An upload is kept while a job uses it: queued, being read, waiting in review, or saved as an import (until that's
undone, history.py). A job that ends otherwise, failed, interrupted, cancelled or saving nothing, removes it with
its own end unless another job uses it (#547); uploads.cleanup removes any left over at the next start.

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
their uploads removed, except one that was saving a confirmed preview: it goes back to "preview".
"""
import collections, contextlib, json, logging, os, posixpath, queue, re, threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import activity, config, crossmatch, crypto, db as dbm, export, importers as registry, labmap, preview, profiles as prof, records, uploads, watch_store

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
    preview goes back to its preview (still there), to be confirmed again; the others are marked failed, and their
    uploads removed unless another job uses them (#547). Preview files no waiting job owns are removed. Returns how
    many failed."""
    for r in db.execute("SELECT id, profile_id FROM jobs WHERE state IN ('queued', 'running') AND stage='saving'").fetchall():
        if os.path.isfile(preview_path(r["profile_id"], r["id"])):
            db.execute("UPDATE jobs SET state='preview', stage=NULL WHERE id=?", (r["id"],))
    stopped = db.execute("SELECT id, profile_id, upload_id FROM jobs WHERE state IN ('queued', 'running')").fetchall()
    n = db.execute("UPDATE jobs SET state='failed', stage=NULL, message=?, finished=? WHERE state IN ('queued', 'running')",
                   (crypto.enc(INTERRUPTED), dbm.now())).rowcount
    # a job over without an import keeps only its counts (#591): those failed now, and any from before that kept more
    for r in db.execute("SELECT id, summary FROM jobs WHERE state IN ('failed', 'discarded') AND summary IS NOT NULL").fetchall():
        kept = counts_only(r["summary"])
        if kept != r["summary"]:
            db.execute("UPDATE jobs SET summary=? WHERE id=?", (kept, r["id"]))
    for r in stopped:
        _drop_upload(db, r["profile_id"], r["upload_id"], r["id"])
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
    """Queue an import of an upload; returns the job's id. Inside a transaction() it's queued once that commits (a
    connection's first pull, #592: the job and the connection's first_job in one), so the worker never looks for it
    before it's there."""
    jid = db.execute("INSERT INTO jobs(profile_id, upload_id, created_by, state, importer, created) VALUES(?, ?, ?, 'queued', ?, ?)",
                     (pid, upload_id, uid, importer, dbm.now())).lastrowid
    _ips[jid] = ip
    if _q is not None and not dbm.after_commit(db, lambda: _q.put(jid)):
        _q.put(jid)   # in a transaction begun another way: as before (the worker waits for its write lock)
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
        # only while it's still queued: cancelled since (a Disconnect during a connection's first pull, #592), it stays so
        if not db.execute("UPDATE jobs SET state='running', stage=?, started=IFNULL(started, ?) WHERE id=? AND state='queued'",
                          ("saving" if saving else "detecting", dbm.now(), jid)).rowcount:
            return
        try:
            _save(db, j) if saving else _import(db, j)
        except Exception as e:
            stage = (db.execute("SELECT stage FROM jobs WHERE id=?", (jid,)).fetchone() or {"stage": None})["stage"]
            # a job cancelled while it ran (#592: its source disconnected) stays cancelled, saying why; no failure to log
            if db.execute("UPDATE jobs SET state='failed', message=?, finished=?, summary=? WHERE id=? AND state='running'",
                          (crypto.enc(_explain(e, stage)), dbm.now(), counts_only(_summary_of(db, jid)), jid)).rowcount:
                log.exception("import job %s (profile %s, upload %s) failed while %s", jid, j["profile_id"], j["upload_id"], stage)
            _remove(preview_path(j["profile_id"], jid))
            _drop_upload(db, j["profile_id"], j["upload_id"], jid)   # nothing can start it again (#547)
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
        mod, data, warnings, mark = _read(db, jid, j["importer"], files, pid)
        names = sorted(files)

    for d in data["details"].values():   # stored under a random name, so the type comes from the path in the upload
        if isinstance(d, dict) and d.get("type") == "file" and isinstance(d.get("url"), str) and not d.get("ext"):
            d["ext"] = posixpath.splitext(d["url"].split("#", 1)[0])[1].lstrip(".").lower()
    watch = data.pop("watch", None)   # daily wearable data: saved with watch_store, not as records
    labs = labmap.plan(db, pid, mod.NAME, data)   # lab names to match; remembered ones go before duplicates are found
    path = preview_path(pid, jid)
    # cycle tracking asked again (#539), under the lock turning it off takes (cycle_tracking.drop_imported): turned off
    # or its records deleted while this was read, they're left out. An upload left holding nothing (only those, or only
    # what Sanguity doesn't read) is cancelled at once, its upload removed (#542): nothing would show it to cancel it
    with preview_lock(jid):
        from . import cycle_tracking
        dropped = mark and cycle_tracking.mark(db, pid) != mark and isinstance(watch, dict) and watch.pop("cycle", None) is not None
        if _holds_nothing({"data": data, "watch": watch}) and not _first_pull(db, jid):
            who = cycle_tracking.changed_by(db, pid) if dropped else _account(db, j["created_by"])
            _cancel(db, pid, jid, who, state="running", message=NOTHING)
            return
        report = _report(db, pid, mod.NAME, data, watch, labs)
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


NOTHING = "Nothing in this upload could be imported, so it was removed."   # a job cancelled holding nothing (#542)
NOTHING_NEW = "Nothing new to add, so the upload was removed."   # a save of what's all here by now (#547)
ALL_HERE = "Nothing new to add: it's all in the profile already."   # ...whose upload another job uses


def _first_pull(db, jid):
    """Whether the job is a connected source's first pull, whose save starts its syncing (sync.first_saved): never
    cancelled for holding nothing."""
    return db.execute("SELECT 1 FROM connections WHERE first_job=?", (jid,)).fetchone() is not None


def _account(db, uid):
    return db.execute("SELECT id, username FROM users WHERE id=?", (uid,)).fetchone() if uid else None


def _holds_nothing(held):
    """Whether a preview's file (or its parts) holds no records at all, new or already here (#539: an upload of cycle
    records only, once they're left out), as the page's sgNothing says of its summary."""
    return not preview.adds_anything(held["data"]) and not any(_watch_counts(held.get("watch") or {}).values()) \
        and not export.brings(held["data"])   # an export's journal, corrections... (#589)


def _report(db, pid, name, data, watch, labs):
    """What the preview shows: what's new, the duplicates of what the profile has now, and possible matches from other
    sources (#96), with the profile's records stamp (_stamp) it was found against."""
    stamp = _stamp(db, pid)
    named = labmap.apply(data, labs["auto"])
    dups = preview.find(db, pid, name, named)
    report = preview.report(named, dups, summarise)
    report["possible"] = crossmatch.public(crossmatch.for_preview(db, pid, named, dups))   # from other sources (#96)
    # a lab name is asked about only while its series brings something new (#497: not one whose results are all here)
    listed = [e for e in labs["listed"] if not _all_here(named, dups, int(e["ref"][2:]))]
    report["labs"], report["lab_targets"] = listed, labs["targets"] if listed else []
    # (#1103) a first import into a profile with no lab series yet: nothing of its own to match to, so the page shows
    # the names closed, kept as they are unless someone opens them
    report["labs_first"] = bool(listed) and not db.execute("SELECT 1 FROM series WHERE profile_id=? LIMIT 1", (pid,)).fetchone()
    if watch:   # what the profile has already counts as already here (#497), not as new
        fresh, here, _ = _watch_split(db, pid, watch)
        report["counts"].update(fresh)
        report["range"] = _with_days(report["range"], watch)   # the dates the upload covers, its watch data's too (#500)
        if here:
            report["duplicates"]["watch"] = here
        report["new"] = report["new"] or any(fresh.values())
    if (back := export.brings(data)):   # an export's corrections, journal, medication log and reminders (#589)
        report["restores"] = back
        report["new"] = True
    report["stamp"] = stamp
    return report


def _all_here(data, dups, i):
    """Whether every result of data's lab series i is a duplicate (preview.find's dups)."""
    s = (data.get("labs") or [])[i] if i < len(data.get("labs") or []) else None
    if not isinstance(s, dict):
        return False
    pts, txt = s.get("pts") or [], s.get("txt") or []
    here = len(dups["points"].get(i, ())) + len((dups.get("updates") or {}).get(i, ()))   # (updated in place, #869)
    return i in dups["series"] or bool(pts or txt) and here >= len(pts) and len(dups["txt"].get(i, ())) >= len(txt)


def _watch_split(db, pid, watch):
    """An import's watch data counted as what's new to the profile and what it has already (watch_store.unseen):
    (the new counts, as _watch_counts gives them, {count key: how many already here}, the new data itself)."""
    new = watch_store.unseen(watch_store.load(db, pid), watch)
    whole, fresh = _watch_counts(watch), _watch_counts(new)
    return fresh, {k: n - fresh.get(k, 0) for k, n in whole.items() if n > fresh.get(k, 0)}, new


def _with_days(rng, watch):
    """A range ({"from", "to"} or None, summarise's) widened to the days of watch data (#500): its daily points',
    workouts' and home readings' (an Apple Health export's later days may be all it brings that's new), a time with
    an offset on its day in the data's own time zone ("tz": a zone's name or "UTC-0700"), as its daily points are."""
    w = watch or {}
    tz, off = timezone.utc, re.fullmatch(r"UTC([+-])(\d\d)(\d\d)", str(w.get("tz") or ""))
    try:
        tz = (timezone((-1 if off[1] == "-" else 1) * timedelta(hours=int(off[2]), minutes=int(off[3]))) if off
              else ZoneInfo(str(w.get("tz") or "UTC")))
    except (ZoneInfoNotFoundError, ValueError, OSError):
        pass

    def day(s):
        try:
            t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        except ValueError:
            return str(s)[:10]
        return (t.astimezone(tz) if t.tzinfo else t).date().isoformat()
    days = {str(p[0])[:10] for m in w.get("metrics") or [] if isinstance(m, dict) for p in m.get("pts") or [] if isinstance(p, list) and p}
    days |= {day(x.get("start")) for x in w.get("exercise") or [] if isinstance(x, dict)}
    days |= {day(r[0]) for k in watch_store.READINGS for r in w.get(k) or [] if isinstance(r, list) and r}
    days = {d for d in days if re.match(r"^\d{4}-\d{2}-\d{2}$", d)} | ({rng["from"], rng["to"]} if rng else set())
    return {"from": min(days), "to": max(days)} if days else None


def _stamp(db, pid):
    """How the profile's records stand, cheaply: each table's count and latest row. Anything saved or undone since a
    preview was read changes it, and the preview is read again (freshen). With the remembered lab-name matches: how
    many were never checked (#575) and how many times they've changed (labmap.rev, #583), so any choice remembered,
    kept, changed or forgotten plans its lab names again too, as does the upgrade to schema version 38 (a stamp
    without it), whose matches to check (#569) a preview planned before it applied."""
    parts = [f"{n}.{m or 0}" for n, m in (db.execute(f"SELECT COUNT(*), MAX(id) FROM {t} WHERE profile_id=?", (pid,)).fetchone()
                                          for t in ("imports", "events", "series"))]
    unchecked = db.execute("SELECT COUNT(*) FROM labmaps WHERE checked IS NULL").fetchone()[0]
    return ";".join(parts + [f"labmaps.{unchecked}.{labmap.rev(db)}"])


_FRESHENING = [threading.Lock() for _ in range(16)]   # freshen, one at a time per job (#521), by its id


def freshen(db, pid, jid):
    """A preview read again against the profile as it is now (#490), when its records have changed since it was read:
    the same file saved meanwhile shows as already here, not as new. Only what the page shows changes: what's saved is
    found again on confirm anyway (_save), but for the lab names to match, planned again and kept for confirm (#497).
    A preview that can't be read again keeps what it said. One at a time per job (#521): the open review's own fetch
    and the Overview's list can ask together, and the second then finds the first's summary already fresh."""
    with preview_lock(jid):
        _freshen(db, pid, jid)


def freshen_held(db, pid, jid):
    """freshen, with preview_lock held already (cycle_tracking.drop_imported, #534)."""
    _freshen(db, pid, jid)


def preview_lock(jid):
    """Held while a waiting preview is read and written again: by freshen, and by turning cycle tracking off
    (cycle_tracking.drop_imported, #526), so neither writes back what the other took out."""
    return _FRESHENING[int(jid) % len(_FRESHENING)]


def rewrite_preview(db, jid, path, held):
    """A waiting preview's file written again (with preview_lock held); not for a job saved or cancelled before or
    while it's written, whose file mustn't come back (#521). Returns whether it's still waiting, written."""
    if _left(db, jid):
        return False
    crypto.write_file(path, json.dumps(held, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    if _left(db, jid):
        _remove(path)
        return False
    return True


def _left(db, jid):
    """The job no longer needs its preview file: saved, failed or cancelled (or gone); a confirmed one still does."""
    r = db.execute("SELECT state FROM jobs WHERE id=?", (jid,)).fetchone()
    return not r or r["state"] not in ("preview", "queued", "running")


def _freshen(db, pid, jid):
    r = db.execute("SELECT summary FROM jobs WHERE id=? AND profile_id=? AND state='preview'", (jid, pid)).fetchone()
    if not r or not r["summary"]:
        return
    old = json.loads(crypto.dec(r["summary"]))
    if old.get("stamp") == _stamp(db, pid):
        return
    path = preview_path(pid, jid)
    try:
        held = json.loads(crypto.read_file(path))
        # the lab names to match planned again too (#497), what was asked still asked (a choice may be typed in it)
        labs = labmap.plan(db, pid, held["importer"], held["data"], keep={e["ref"] for e in (held.get("labs") or {}).get("listed") or []})
        report = _report(db, pid, held["importer"], held["data"], held.get("watch"), labs)
        if labs != held.get("labs") and not rewrite_preview(db, jid, path, {**held, "labs": labs}):   # what confirm (_save) reads the choices against
            return
    except Exception:   # gone (saved or cancelled meanwhile), or an importer since removed
        log.warning("the preview of job %s couldn't be read again", jid, exc_info=True)
        return
    db.execute("UPDATE jobs SET summary=? WHERE id=? AND state='preview'", (crypto.enc(json.dumps(report)), jid))


# the daily summaries of readings counted on their own (watch_store.measure_counts): home blood pressure and body
# temperature (apple_health.VITALS_META, the receiver's too) and glucose, not days of activity data (#534)
_READ_DAILY = ("bp_", "body_temp", "glucose_")


def _watch_counts(watch):
    days = {str(p[0])[:10] for m in watch.get("metrics") or [] if isinstance(m, dict) and not str(m.get("key") or "").startswith(_READ_DAILY)
            for p in m.get("pts") or [] if isinstance(p, list) and p}
    return {"watch_days": len(days), "workouts": len(watch.get("exercise") or []), **watch_store.ecg.counts(watch),   # ECGs (#182)
            **watch_store.measure_counts(watch),   # home readings, glucose, heights (#223); cycle records: the preview's only,
            **({"cycle": len(watch["cycle"])} if watch.get("cycle") else {})}   # read only while tracking is on (#223)


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
    held["labs"] = labmap.recheck(db, pid, held.get("labs"))   # one now to check, forgotten, changed or another's isn't applied (#575, #583, #927)
    data, watch, names = held["data"], held.get("watch"), set(held.get("paths") or ())
    keep = json.loads(j["keep"]) if j["keep"] else []
    sent = json.loads(crypto.dec(j["labs"])) if j["labs"] else {}
    raw, data = data, labmap.apply(data, labmap.choices(held.get("labs"), sent))   # series names as matched
    from . import cycle_tracking
    with export.photo_files(pid) as written, dbm.transaction(db):
        # what the profile has already, found under the write lock (#592): the same records saved by another save at the
        # same time (a sync, another review of the same file, records typed in) are found here, and not saved twice
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
        summary["skipped"] = {k: skipped[k] for k in ("events", "kinds", "results", "other")}
        if same:
            summary["linked"] = len(same)
        who_id = j["confirmed_by"] or j["created_by"]
        who = db.execute("SELECT id, username FROM users WHERE id=?", (who_id,)).fetchone() if who_id else None

        def photo(rel):   # a journal photo an export names (#589): its sealed copy in the upload
            p = stored(rel)
            return crypto.read_file(p) if p else None

        # cycle records only while tracking is on, asked under the write lock (#542: turned off while this waited to be
        # saved, which drop_imported leaves alone); left holding nothing, it's cancelled like an empty preview, its
        # upload removed, not saved as an import of nothing
        cancelled = None
        if isinstance(watch, dict) and watch.get("cycle") and not cycle_tracking.is_on(db, pid):
            watch = {k: v for k, v in watch.items() if k != "cycle"}
            if _holds_nothing({"data": data, "watch": watch}) and not _first_pull(db, jid):
                cancelled = _cancelled(db, pid, jid, cycle_tracking.changed_by(db, pid), None, "running", NOTHING)
        if not cancelled:
            # watch data: what the profile has already is counted as skipped (#497); all of it there, it isn't saved again
            fresh, here, new_watch = _watch_split(db, pid, watch) if watch else ({}, {}, None)
            if here:
                summary["skipped"]["watch"] = here
            summary["range"] = _with_days(summary["range"], new_watch)   # what it brought, the wearable's new days too (#500)
            watch = watch if any(fresh.values()) else None
            # an export's corrections, journal, medication log and reminders (#589), what the profile hasn't already
            back = export.restore(db, pid, data, photo, written) if export.brings(data) else None
            restored = {k: v for k, v in (back or {}).items() if v and k not in ("here", "photos_missing")}
            if restored:
                summary["restored"] = restored
            # all of it here by now (#547: the same file in two reviews, one added): no import, and nothing to undo or
            # remove the upload by, so it's cancelled with it, saying so; a first pull is saved, its upload removed below
            # results here their source sends again, updated in place (#869), and records of any kind (#920)
            amended = preview.updates(dups) + preview.changes(dups)
            if not (preview.adds_anything(new) or watch or restored or amended) and not _first_pull(db, jid):
                cancelled = _cancelled(db, pid, jid, who, _ips.get(jid), "running", NOTHING_NEW)
                if cancelled and cancelled[1]:   # another job uses the upload: kept
                    db.execute("UPDATE jobs SET message=? WHERE id=?", (crypto.enc(ALL_HERE), jid))
        if not cancelled:
            iid = None
            # all duplicates: nothing to add, not even an empty import; but watch data and results updated in place
            # (#869) need an import to be undone by
            if preview.adds_anything(new) or watch or amended:
                keepdocs, n = prof.index_documents(db, pid, new["details"], stored)
                summary["counts"]["documents"] = n
                iid = records.store(db, pid, new, "upload", fmt=name)
                prof.link_documents(db, iid, keepdocs)
                if back is not None:   # (#1097) an export's labs charted under another test, among the records just saved
                    labmap.from_export(db, pid, data)
                # (#920) a medication stopped, a problem resolved, a claim processed: first, so a record's results
                # updated after it add to what it was (one "before" an event, as update() keeps them)
                if preview.changes(dups):
                    summary["counts"]["changed"] = preview.change(db, pid, iid, data, dups, stored)
                if preview.updates(dups):
                    summary["counts"]["updated"] = preview.update(db, pid, iid, data, dups)
            if watch:   # last, so a failure before it leaves no file behind
                kept = watch_store.save_import(db, pid, watch, import_id=iid)   # all of it, as it came: undone with its import
                summary["counts"].update(watch_days=fresh["watch_days"], workouts=fresh["workouts"],   # what's new of it (#497)
                                         **{k: fresh[k] for k in ("ecgs", "rhythm", "bp_readings", "temp_readings", "glucose_readings",
                                                                  "cgm_days", "heights") if fresh.get(k)})   # ECGs (#182), #223
                # apart from the counts, which Import history and the activity log show (#223, #534); only if they were
                # kept (#539: not once tracking is turned off)
                if fresh.get("cycle") and kept.get("cycle"):
                    summary["cycle"] = fresh["cycle"]
            for x in same if iid else ():
                crossmatch.decide(db, pid, who["id"] if who else None, x["other_key"], x["key"], True,
                                  [x["other"], {k: x[k] for k in ("name", "t", "value", "unit")}])
            remembered = labmap.remember(db, pid, name, held.get("labs"), sent, raw, who, import_id=iid)   # undone with it (#500)
            labmap.used(db, labmap.applied(held.get("labs"), sent))   # ...unless another import used them since
            matched = sum(1 for t in labmap.choices(held.get("labs"), sent).values() if t)
            summary["lab_names"] = {"matched": matched, "remembered": remembered}
            activity.log(db, "import", who, pid, {"importer": name, "counts": summary["counts"], "job": jid,
                                                  "skipped": summary["skipped"], "kept": len(keep),
                                                  **({"same": len(same)} if same else {}),
                                                  **({"restored": restored} if restored else {}),
                                                  **({"labs": summary["lab_names"]} if matched or remembered else {})},
                         ip=_ips.get(jid), actor=None if who else "removed account")
            db.execute("UPDATE jobs SET state='done', stage=NULL, import_id=?, summary=?, finished=? WHERE id=?",
                       (iid, crypto.enc(json.dumps(summary)), dbm.now(), jid))
            from . import sync   # the first pull of a connected source (#91): its import and cursor go to the connection
            sync.first_saved(db, jid, iid)
    if cancelled:
        _cancel_files(db, pid, jid, cancelled)
        return
    _remove(path)
    if iid is None:   # a first pull that kept nothing (#547)
        _drop_upload(db, pid, upload_id, jid)


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
    once it's saved (crossmatch.py). Refused unless the job is waiting in "preview", and (400) when a series named
    in labs isn't one its review offered (#927: never any text sent)."""
    keep = [str(r)[:40] for r in keep or []][:20000]
    labs = {str(k)[:20]: labmap.clean_target(v) for k, v in list((labs or {}).items())[:5000]}
    held = db.execute("SELECT summary FROM jobs WHERE id=? AND profile_id=? AND state='preview'", (jid, pid)).fetchone() if labs else None
    if held and held["summary"]:   # (not waiting in preview: refused below)
        shown = json.loads(crypto.dec(held["summary"]))
        try:
            labs = labmap.check_sent(shown.get("labs"), shown.get("lab_targets"), labs)
        except labmap.NotOffered as e:
            raise Refused(str(e), 400) from None
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
    if not _cancel(db, pid, jid, who, ip):
        _refuse_not_preview(db, pid, jid)


def _cancel(db, pid, jid, who, ip=None, state="preview", message=None):
    """discard's work, for a job in that state; who may be None (an account since removed). Also for one left with
    nothing (#539: once cycle tracking is turned off, by cycle_tracking.drop_imported; #542: holding nothing once
    read, by _import, as "running", saying why in message). Returns whether it was."""
    with dbm.transaction(db):
        r = _cancelled(db, pid, jid, who, ip, state, message)
    if r:
        _cancel_files(db, pid, jid, r)
    return bool(r)


def _cancelled(db, pid, jid, who, ip, state, message=None):
    """_cancel's rows, in the caller's transaction: (the job's upload, whether another job uses it), or None."""
    r = db.execute("SELECT * FROM jobs WHERE id=? AND profile_id=?", (jid, pid)).fetchone()
    if not r or r["state"] != state:
        return None
    db.execute("UPDATE jobs SET state='discarded', stage=NULL, finished=?, confirmed_by=?, message=?, summary=? WHERE id=?",
               (dbm.now(), who["id"] if who else None, crypto.enc(message) if message else None, counts_only(r["summary"]), jid))
    activity.log(db, "discard", who, pid, {"importer": r["importer"], "job": jid, "upload": r["upload_id"]}, ip,
                 actor=None if who else "removed account")
    return r["upload_id"], _used_elsewhere(db, r["upload_id"], jid)


FIRST_PULL_CANCELLED = "Its source was disconnected before this first pull was saved, so nothing from it was kept."


def cancel_first_pull(db, pid, jid, who, ip=None):
    """Disconnect's (connections.disconnect, in its transaction, #592): a connection's first pull cancelled wherever it
    is, queued, being read, waiting in review or being saved; the worker leaves a job cancelled meanwhile as it is (run,
    _import), and a save finds the connection disconnected (sync.first_saved). Returns (job id, _cancelled's) for
    _cancel_files once committed, or None (saved, failed or cancelled already)."""
    r = db.execute("SELECT state FROM jobs WHERE id=? AND profile_id=?", (jid, pid)).fetchone()
    if not r or r["state"] not in ("queued", "running", "preview"):
        return None
    got = _cancelled(db, pid, jid, who, ip, r["state"], FIRST_PULL_CANCELLED)
    return (jid, got) if got else None


def counts_only(sealed):
    """A job's sealed summary once it's over without an import (cancelled, or failed): how many records it held, and
    nothing else (#591). What the review showed (sample records, lab names, possible matches, the dates it covered)
    was for whoever chose whether to save it, and goes with the choice. None when there's nothing to keep, or it
    can't be read."""
    if not sealed:
        return None
    try:
        was = json.loads(crypto.dec(sealed))
    except Exception:   # unreadable: kept as nothing, never as it was
        return None
    counts = was.get("counts") if isinstance(was, dict) else None
    if not isinstance(counts, dict):
        return None
    return sealed if was == {"counts": counts} else crypto.enc(json.dumps({"counts": counts}))   # already only that: as it is


def _summary_of(db, jid):
    r = db.execute("SELECT summary FROM jobs WHERE id=?", (jid,)).fetchone()
    return r["summary"] if r else None


def _used_elsewhere(db, upload_id, jid):
    """Whether a job other than jid uses the upload: one queued, being read or waiting in review, or one saved as an
    import (#547: not one that failed, was cancelled or kept nothing)."""
    return db.execute("""SELECT 1 FROM jobs WHERE upload_id=? AND id != ? AND (state IN ('queued', 'running', 'preview')
                         OR state='done' AND import_id IS NOT NULL) LIMIT 1""", (upload_id, jid)).fetchone() is not None


def _drop_upload(db, pid, upload_id, jid):
    """A job over without an import (failed, interrupted, or a first pull that kept nothing, #547): its upload is removed
    unless another job uses it. One still receiving is left to uploads.cleanup."""
    r = db.execute("SELECT state FROM uploads WHERE id=? AND profile_id=?", (upload_id, pid)).fetchone()
    if r and r["state"] == "ready" and not _used_elsewhere(db, upload_id, jid):
        with contextlib.suppress(uploads.UploadError):
            uploads.discard(db, pid, upload_id)


def _cancel_files(db, pid, jid, cancelled):
    """Once _cancelled has committed: the job's preview file, and its upload unless another job uses it."""
    upload_id, others = cancelled
    _remove(preview_path(pid, jid))
    if not others:
        with contextlib.suppress(uploads.UploadError):
            uploads.discard(db, pid, upload_id)


def _refuse_not_preview(db, pid, jid):
    r = db.execute("SELECT state FROM jobs WHERE id=? AND profile_id=?", (jid, pid)).fetchone()
    if not r:
        raise Refused("No such import.", 404)
    raise Refused({"queued": "This import is being saved already.", "running": "This import is still being read.",
                   "done": "This import has been saved already.", "discarded": "This import was cancelled.",
                   "failed": "This import failed; start it again."}.get(r["state"], "This import can't be changed now."))


def _read(db, jid, name, files, pid=None):
    """Pick the importer (or take the one asked for), parse and check: (module, data, warnings, mark). An importer
    that can read menstrual cycle records (READS_CYCLE: Apple Health's, #223) is asked to only for a profile with cycle
    tracking on; otherwise it never reads them. mark: how tracking stood when it was asked (cycle_tracking.mark), or
    None when it wasn't (#539: asked again before the preview is written)."""
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
    from . import cycle_tracking
    mark = cycle_tracking.mark(db, pid) if getattr(mod, "READS_CYCLE", False) and pid else None
    mark = mark if mark and mark[0] else None
    out = mod.parse(files, cycle=True) if mark else mod.parse(files)
    problems = registry.check(out, files)
    if problems:
        raise Failed(f"The {mod.LABEL} importer returned records Sanguity can't load: " + "; ".join(problems[:5]) + ".")
    warnings = [str(w) for w in out.get("warnings") or []]
    if tied:
        warnings.insert(0, f"These files also look like {', '.join(m.LABEL for m in tied)}; read them as {mod.LABEL}.")
    return mod, out["data"], warnings, mark


def summarise(data):
    """What an import brought: events by kind (visible ones; lab results are counted as results), lab series and
    results, and the first and last day it covers."""
    kinds = collections.Counter(str(e.get("k")) for e in data["events"] if isinstance(e, dict) and not e.get("hidden"))
    labs = [s for s in data["labs"] if isinstance(s, dict)]
    days = [str(e.get("t"))[:10] for e in data["events"] if isinstance(e, dict) and e.get("t")]
    days += [str(p[0])[:10] for s in labs for p in s.get("pts") or [] if isinstance(p, list) and p and p[0]]
    oop = data.get("outOfPocket") if isinstance(data.get("outOfPocket"), dict) else {}
    pays = [p for p in oop.get("payments") or [] if isinstance(p, dict)]
    days += [str(p.get("d"))[:10] for p in pays if p.get("d")]   # (#1163) a payments spreadsheet's days too
    days = [d for d in days if re.match(r"^\d{4}-\d{2}-\d{2}$", d)]
    counts = {"events": sum(kinds.values()), "kinds": dict(sorted(kinds.items())), "series": len(labs),
              # a text result ("Negative") is a result too (#1103: "5 lab results in 6 tests" where one held only text)
              "results": sum(len(s.get("pts") or []) + len(s.get("txt") or []) for s in labs)}
    # #179: the profile-wide lists' allergies, other problems and upcoming visits, for the preview to list
    probs = [p for p in data.get("problems") or [] if isinstance(p, dict)]
    lists = {"allergies": sum(1 for p in probs if p.get("type") == "Allergy"),
             "problems": sum(1 for p in probs if p.get("type") != "Allergy"),
             "upcoming": sum(1 for u in data.get("upcoming") or [] if isinstance(u, dict)),
             "payments": len(pays)}   # (#1163) a payments spreadsheet's, for Costs
    counts.update({k: n for k, n in lists.items() if n})
    return {"counts": counts, "range": {"from": min(days), "to": max(days)} if days else None}


# ---------------------------------------------------------------- reading jobs

def describe(r, stamp=None, uid=None):
    """A job for the page; with the profile's records stamp, a preview says whether it was read before they changed
    ("stale"). The cycle records a saved one brought ("cycle") only to uid, who saved it (#534): never to others
    on the profile (#223)."""
    mod = registry.get(r["importer"]) if r["importer"] else None
    summary = json.loads(crypto.dec(r["summary"])) if r["summary"] else {}
    if r["state"] in ("failed", "discarded"):   # only its counts (#591), whatever an older row kept
        summary = {"counts": summary.get("counts")}
    out = {"id": r["id"], "profile_id": r["profile_id"], "upload_id": r["upload_id"], "state": r["state"], "stage": r["stage"],
            "importer": r["importer"], "label": mod.LABEL if mod else r["importer"],
            "counts": summary.get("counts"), "samples": summary.get("samples"), "range": summary.get("range"), "total": summary.get("total"),
            "duplicates": summary.get("duplicates"), "new": summary.get("new"), "skipped": summary.get("skipped"),
            "labs": summary.get("labs"), "lab_targets": summary.get("lab_targets"), "lab_names": summary.get("lab_names"),
            "labs_first": bool(summary.get("labs_first")),   # (#1103)
            "possible": summary.get("possible") or [], "linked": summary.get("linked"),
            "warnings": json.loads(crypto.dec(r["warnings"])) if r["warnings"] else [],
            "message": crypto.dec(r["message"]) if r["message"] else None, "import_id": r["import_id"],
            "created": r["created"], "started": r["started"], "finished": r["finished"]}
    if stamp is not None and r["state"] == "preview":
        out["stale"] = summary.get("stamp") != stamp
    if summary.get("cycle") and uid is not None and uid == (r["confirmed_by"] or r["created_by"]):
        out["cycle"] = summary["cycle"]
    for k in ("restores", "restored"):   # an export's corrections, journal, medication log and reminders: to come, put back (#589)
        if summary.get(k):
            out[k] = summary[k]
    return out


def get(db, pid, jid, uid=None):
    """A job; a preview read again first if the profile's records have changed since (freshen). uid: who asks."""
    freshen(db, pid, jid)
    r = db.execute("SELECT * FROM jobs WHERE id=? AND profile_id=?", (jid, pid)).fetchone()
    out = describe(r, uid=uid) if r else None
    if out and out.get("cycle"):   # the cycle records a saved one brought, while they're still kept (#542: not once
        from . import cycle_tracking   # tracking is turned off or they're deleted, which an Undo's question would name)
        if not (r["import_id"] and cycle_tracking.is_on(db, pid) and os.path.isfile(cycle_tracking.imported_path(pid, r["import_id"]))):
            del out["cycle"]
    return out


STATES = ("queued", "running", "preview", "done", "failed", "discarded")
WAITING = 100   # previews listed at most: each is an import someone left to save or cancel (#283)


FRESH = 5   # previews read again when listed (freshen): the latest, the ones "Waiting for you" shows first (#490)


def recent(db, pid, limit=RECENT, state=None):
    """The profile's jobs, newest first: the RECENT latest, or with state, every one in that state (up to WAITING): the
    Sources overview's "Waiting for you" asks for the previews, however long ago they were left open; the FRESH latest
    are read again if the profile has changed since (freshen), the rest marked "stale" until they're opened."""
    if state == "preview":
        ids = [r[0] for r in db.execute("SELECT id FROM jobs WHERE profile_id=? AND state='preview' ORDER BY id DESC LIMIT ?",
                                        (pid, FRESH))]
        for jid in ids:
            freshen(db, pid, jid)
        stamp = _stamp(db, pid)
        return [describe(r, stamp) for r in db.execute("SELECT * FROM jobs WHERE profile_id=? AND state='preview' ORDER BY id DESC LIMIT ?",
                                                       (pid, WAITING))]
    if state:
        return [describe(r) for r in db.execute("SELECT * FROM jobs WHERE profile_id=? AND state=? ORDER BY id DESC LIMIT ?",
                                                (pid, state, WAITING))]
    return [describe(r) for r in db.execute("SELECT * FROM jobs WHERE profile_id=? ORDER BY id DESC LIMIT ?", (pid, limit))]


def for_upload(db, pid, upload_id):
    """The upload's jobs, newest first: (id, state)."""
    return [tuple(r) for r in db.execute("SELECT id, state FROM jobs WHERE profile_id=? AND upload_id=? ORDER BY id DESC",
                                         (pid, upload_id))]
