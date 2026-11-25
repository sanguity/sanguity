"""Scheduled sync of connected sources (#91): pulls what's new from each connection (connections.py) and keeps the
profile's records up to date in place, as imports that can be undone.

A thread ("sanguity-sync", beside jobs.py's worker) wakes every TICK seconds, or at once when woken (a sign-in, Sync
now), and pulls each connection that is due, one at a time. A pull's network reads happen outside any transaction;
what it saves goes in one transaction at the end, so it never holds SQLite's single write lock while waiting on a
vendor, and a failure saves nothing.

A pull reads, with an access token from connections.Tokens (renewed as needed; a rotated refresh token is saved at
once): the Patient, then a search per type for the patient, page by page, with _lastUpdated=gt<the last pull's
Bundle.meta.lastUpdated> once there was one, and the Binary attachments that new or changed DocumentReferences and
DiagnosticReports point at. One pull reads at most SYNC_MAX_RESOURCES; the rest come next time. A type the access
granted doesn't cover (403) is reported as such, not skipped quietly: the connection's report says, type by type,
what was pulled. Types the granted scopes don't cover aren't asked for at all: an insurer's connection (#92)
reads only Patient, ExplanationOfBenefit and Coverage (Coverage searched by beneficiary).

What it searches is the connection's choice (#99, plan()): the kinds of records (connections.KINDS) and how far
back. Labs and vitals are Observations by category; DiagnosticReports come for labs or documents; a start date is
sent as date=ge<it> where the type has that search, and checked on each resource too (a server may ignore it;
undated resources are kept). Each search has its own cursor, named by its parameters, so a kind turned on later
(or an earlier start date) is pulled from the beginning, and what the profile has already is matched by source key
as always. Turning a kind off stops it being pulled; remove_kind() takes out what it brought (only once it's off,
#922), as an import in the history that undoing puts back, less what's here again since (the kind turned on again). Kinds that share a place (#228) are told apart by the resource a record's source key
names: a pharmacy fill (`rx`) is a claim's or a MedicationDispense's, an upcoming visit a planned Encounter's (visits)
or an Appointment's, and the problem list's allergies (type "Allergy") are the allergies kind's.

Attachments (#101) follow the connection's rules (connections.attach_rules, over the install's sync.* settings):
fetched automatically or only when asked, the kinds allowed (PDF, images, text, HTML), a size limit per attachment
(never above SYNC_MAX_ATTACHMENT_MB) and a total per connection. One left out is listed (sync_skipped) with why,
and fetch_anyway() brings it in later, as its own import, linked to its record.

What a pull read is kept as an upload (pull.json, a Bundle, and Binary/<id>), sealed like any upload, so its files
are the import's original files. The connection also keeps every resource it has read so far, sealed in
profiles/<id>/connections/<connection id>, newest version of each: a lab day groups results across pulls, so it
is rebuilt from all of them. That store is written only in a transaction that finds the connection still active,
as its records are (#591): a Disconnect is saved before it, and then nothing is written, or waits for it, and then
removes the store (connections.disconnect), so nothing a pull read outlives a Disconnect.

  The first pull reads everything and goes through the import job flow (jobs.py) like an upload: preview, the
  duplicate check, lab-name matching, then save. Its cursor is kept aside (pending_cursor) and becomes the
  connection's when the preview is saved (jobs._save); cancelled, the next Sync now pulls everything again.

  Later pulls are saved without a preview (no one is there to look), as an import of origin "sync":
    - records whose source key the profile doesn't have are added, as an upload's would be; lab results the
      profile has already are skipped, and remembered lab-name matches applied (labmap.py)
    - records whose source key it has are updated in place (the row keeps its id, import and corrections), when
      what the vendor sends now differs (a claim's Costs row with it); sync_changes keeps what each was, so undoing
      the sync puts it back. A key scoped to a day (a lab day's, the labitem of a day's results on their own,
      preview.day_key) names no one source's record (#885): an upload's or another source's labitem of that day is
      never replaced by the pull's, whose rows it lost. Each of its rows is its own record (its Observation, the
      details' "keys") and is found by it wherever it is (#899: an upload of the portal's own export, a connection made
      before this one), updated there; a row new to the profile goes in a labitem of the pull's own beside its result,
      and one whose result is a duplicate is left out with it (#897). A report's labitem is placed the same way, row by
      row (#881): every row sits beside its result, in a labitem of its result's own import (one saved apart before is
      moved beside it, _apart). The day's lab day is joined, never a second added beside it: the connection's own,
      else its source's (#912: one listing the portal's records, an upload's of its export), else the earliest,
      keeping every labitem it lists, each lab event listed by one lab day (_draw_day, _link_labdays, #910; the first
      pull's lab day joins it too, _fold_first). Lab events saved before #885 take their rows' records from the
      connection's store once (backfill_keys, schema version 40), and those of the days a pull reads as it goes
      (_give_keys, #909: an upload of the portal's own export, the portal connected again)
    - only records that come from what this pull read count: one undone from the history doesn't come back
      unless the vendor changes it
  Each is recorded in the import history (#24) with a done job, and can be undone there.

Failures: the vendor unavailable (429, 5xx, no answer after smart.Client's retries) backs off, 5 minutes doubling
up to SYNC_HOURS; the vendor ending access makes the connection "needs reconnect", and the page asks to sign in
again; anything else is shown as it is and retried with the same backoff. Each tick also notes, once, in the
profile's activity log a connection that has stopped syncing (connections.note_warnings, #102); the profile's main
page shows it too.

Each successful pull, new records or not, marks every import of the connection as up to date then
(imports.synced, #100): the page shows it as "last synced" beside each record's source. The first pull's import gets
it when its preview is saved.
"""
import collections, contextlib, hashlib, io, json, logging, os, re, shutil, tempfile, threading, urllib.parse

from . import activity, config, connections as conns, crossmatch, crypto, db as dbm, importers as registry, jobs, labmap
from . import outside, preview, profiles as prof, records, smart, uploads
from .importers import common

log = logging.getLogger("sanguity.sync")

TICK = 30                    # seconds between looks for connections that are due
PAGE = 100                   # _count asked for per page
BACKOFF_FIRST = 300          # a failed pull is tried again after this, doubling up to SYNC_HOURS
REF_TYPES = {"DiagnosticReport": "result"}   # a report groups its results: changed results touch the report too
PATIENT_PARAM = {"Coverage": "beneficiary"}   # the search parameter naming the patient, where it isn't `patient`
DATE_PARAM = {"Observation": "date", "Encounter": "date", "DiagnosticReport": "date", "DocumentReference": "date",
              "Immunization": "date", "Appointment": "date", "ImagingStudy": "started"}   # the last three: #228

_thread, _stop, _wake = None, threading.Event(), threading.Event()


def wake():
    _wake.set()


def start():
    global _thread
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="sanguity-sync", daemon=True)
    _thread.start()


def stop(timeout=5):
    global _thread
    _stop.set()
    _wake.set()
    if _thread is not None:
        _thread.join(timeout)
    _thread = None


def _loop():
    while not _stop.is_set():
        try:
            run_due()
        except Exception:
            log.exception("sync")
        _wake.wait(TICK)
        _wake.clear()


def run_due(now=None):
    """Pull every connection that is due, oldest due first. Returns how many were pulled."""
    db = dbm.connect()
    try:
        due = [r[0] for r in db.execute("""SELECT id FROM connections WHERE state='active' AND next_sync IS NOT NULL
                                           AND next_sync <= ? ORDER BY next_sync, id""", (now or dbm.now(),))]
        for cid in due:
            if _stop.is_set():
                break
            try:
                pull(db, cid)
            except Exception:
                log.exception("sync of connection %s", cid)
        try:
            conns.note_warnings(db)
        except Exception:
            log.exception("noting connections that stopped syncing")
        return len(due)
    finally:
        db.close()


# ---------------------------------------------------------------- the resources a connection has read

def store_path(pid, cid):
    return os.path.join(prof.profile_dir(pid), "connections", str(int(cid)))


def _load_store(pid, cid):
    try:
        return json.loads(crypto.read_file(store_path(pid, cid)))
    except FileNotFoundError:
        return {"resources": {}}


def _save_store(pid, cid, store):
    path = store_path(pid, cid)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    crypto.write_file(path, json.dumps(store, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


class Disconnected(jobs.Failed):
    """The connection stopped being active while the pull read it: nothing it read is kept."""


DISCONNECTED = "This source was disconnected during the pull."


def _keep_store(db, c, store):
    """Inside the pull's dbm.transaction (the write lock, so a Disconnect is saved before it or waits for it): the
    connection's store written only while the connection is still active, the check its records are saved under;
    Disconnected otherwise, with nothing written (#591)."""
    r = db.execute("SELECT state FROM connections WHERE id=?", (c["id"],)).fetchone()
    if not r or r["state"] != "active":
        raise Disconnected(DISCONNECTED)
    _save_store(c["profile_id"], c["id"], store)


def forget_store(pid, cid):
    with contextlib.suppress(FileNotFoundError):
        os.remove(store_path(pid, cid))


def _bundle(base, resources):
    return json.dumps({"resourceType": "Bundle", "type": "collection",
                       "entry": [{"fullUrl": f"{base}/{r['resourceType']}/{r.get('id')}", "resource": r} for r in resources]},
                      separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------- one pull

def _due_after_failure(failures, retry_after=None):
    wait = min(config.setting("SYNC_HOURS") * 3600, BACKOFF_FIRST * 2 ** max(0, failures - 1))
    return dbm.now() + max(wait, retry_after or 0)


def pull(db, cid, who=None, ip=None):
    """Pull connection cid now. who: the person who asked (Sync now), else the schedule. Returns what happened:
    {"ok", "first"?, "job"?, "import"?, "counts"?, "changed"?, "error"?, "kind"?}."""
    c = db.execute("SELECT * FROM connections WHERE id=?", (cid,)).fetchone()
    if not c or c["state"] != "active":
        return {"ok": False, "error": "This connection isn't active.", "kind": "other"}
    actor = who or conns.SYNC_ACTOR
    http = smart.Client(outside.for_connection(db, cid, "Connected source: sync", actor, ip))
    first = c["cursor"] is None
    try:
        tokens = conns.Tokens(db, cid, http)
        reader = smart.Reader(http, c["fhir_base"], tokens)
        got = _fetch(reader, crypto.dec(c["patient"]), None if first else json.loads(c["cursor"]),
                     plan(c), _rules(db, c), c["attach_bytes"], c["scopes"])
    except (smart.AccessEnded, smart.TokenRejected) as e:
        conns.needs_reconnect(db, cid, str(e) + " Sign in again to keep this source up to date.")
        return {"ok": False, "error": str(e), "kind": "access"}
    except smart.SmartError as e:
        return _failed(db, c, e, e.kind, e.retry_after)
    except Exception as e:   # noqa: BLE001 - recorded, and shown as a plain reason
        log.exception("pull of connection %s", cid)
        return _failed(db, c, e, "other")
    try:
        if first:
            return _first(db, c, got, who, ip)
        return _later(db, c, got, who, ip)
    except Disconnected as e:
        return {"ok": False, "error": str(e), "kind": "other"}
    except Exception as e:   # noqa: BLE001
        log.exception("saving the pull of connection %s", cid)
        return _failed(db, c, e, "other")


def _failed(db, c, e, kind, retry_after=None):
    msg = str(e) if isinstance(e, smart.SmartError) else "The pull stopped with an error; the details are in the app's log."
    if kind == "unavailable":
        msg = "The vendor is unavailable just now: " + msg + " Sanguity will try again later."
    n = c["failures"] + 1
    db.execute("""UPDATE connections SET failures=?, error_kind=?, last_error=?, next_sync=? WHERE id=? AND state='active'""",
               (n, kind, crypto.enc(msg), _due_after_failure(n, retry_after), c["id"]))
    return {"ok": False, "error": msg, "kind": kind}


def _newest(resources):
    stamps = [(r.get("meta") or {}).get("lastUpdated") for r in resources]
    stamps = [s for s in stamps if isinstance(s, str)]
    return max(stamps) if stamps else None


def _attachment_urls(r, base):
    """The Binary attachments of a DocumentReference or DiagnosticReport on this server: [(relative path, url,
    the source key of the document record the FHIR importer makes for it, the attachment)]."""
    doc = r["resourceType"] == "DocumentReference"
    atts = [(c or {}).get("attachment") for c in r.get("content") or []] if doc else list(r.get("presentedForm") or [])
    out = []
    for i, a in enumerate(atts, 1):
        url = (a or {}).get("url") if isinstance(a, dict) else None
        if not isinstance(url, str) or a.get("data"):
            continue
        rel = url[len(base) + 1:] if url.startswith(base + "/") else url
        parts = rel.split("/")
        if len(parts) == 2 and parts[0] == "Binary" and parts[1] and "?" not in rel and "#" not in rel:
            key = f"fhir:{r['resourceType']}/{r.get('id')}" + ((f"#content/{i}" if i > 1 else "") if doc else f"#presentedForm/{i}")
            out.append((rel, base + "/" + rel, key, a))
    return out


# ---------------------------------------------------------------- what a pull asks for (#99)

def _codes(cc):
    out = set()
    for c in cc if isinstance(cc, list) else [cc]:
        for x in (c or {}).get("coding") or [] if isinstance(c, dict) else []:
            if isinstance(x, dict) and isinstance(x.get("code"), str):
                out.add(x["code"].lower())
        if isinstance(c, dict) and isinstance(c.get("text"), str):
            out.add(c["text"].lower())
    return out


def _is_lab_report(r):
    return bool(_codes(r.get("category")) & {"lab", "laboratory"})


def _date_of(r):
    """The date a resource is about, for a start date: YYYY-MM-DD..., or None (kept: nothing to go by)."""
    for k in ("effectiveDateTime", "effectiveInstant", "issued", "authoredOn", "onsetDateTime", "recordedDate",
              "date", "created", "occurrenceDateTime", "whenHandedOver", "whenPrepared", "start", "started"):   # #228
        if isinstance(r.get(k), str):
            return r[k]
    for k in ("effectivePeriod", "period", "onsetPeriod", "billablePeriod"):
        if isinstance(r.get(k), dict) and isinstance(r[k].get("start"), str):
            return r[k]["start"]
    ctx = (r.get("context") or {}).get("period") if isinstance(r.get("context"), dict) else None
    return ctx.get("start") if isinstance(ctx, dict) and isinstance(ctx.get("start"), str) else None


def plan(c):
    """The searches a pull of connection row c makes: [(FHIR type, extra parameters, cursor key, keep)], keep(r)
    saying whether a resource the search returned belongs to the chosen kinds and dates, and a set of the kinds."""
    kinds = json.loads(c["kinds"]) if c["kinds"] else None
    since = c["since"]
    k = set(kinds) if kinds is not None else set(conns.KINDS)
    searches = []

    def add(t, params=None, keep=None):
        params = dict(params or {})
        if since and t in DATE_PARAM:
            params[DATE_PARAM[t]] = "ge" + since
        key = t + ("?" + urllib.parse.urlencode(sorted(params.items())) if params else "")

        def ok(r):
            if since:
                d = _date_of(r)
                if d and d[:10] < since:
                    return False
            return keep(r) if keep else True
        searches.append((t, params, key, ok))

    if kinds is None:
        for t in conns.TYPES[1:]:
            add(t)
        return searches, k
    obs = [x for x in ("labs", "vitals") if x in k]
    if len(obs) == 2:
        add("Observation")
    elif obs == ["labs"]:
        add("Observation", {"category": "laboratory"}, lambda r: "vital-signs" not in _codes(r.get("category")))
    elif obs == ["vitals"]:
        add("Observation", {"category": "vital-signs"}, lambda r: "vital-signs" in _codes(r.get("category")))
    for kind, t in (("problems", "Condition"), ("medications", "MedicationRequest"), ("visits", "Encounter"),
                    ("allergies", "AllergyIntolerance"), ("immunizations", "Immunization"),   # #228
                    ("fills", "MedicationDispense"), ("appointments", "Appointment")):
        if kind in k:
            add(t)
    if "documents" in k:
        add("DocumentReference")
        add("ImagingStudy")   # #228: an imaging event, unless a DiagnosticReport describes the study
    if "labs" in k or "documents" in k:
        add("DiagnosticReport", None, None if "documents" in k else _is_lab_report)
    if "claims" in k:
        add("ExplanationOfBenefit")   # mapped to claims on the Costs tab by the FHIR importer (#92)
        add("Coverage")               # the insurer and plan the claims are under
    order = {t: i for i, t in enumerate(conns.TYPES)}
    searches.sort(key=lambda s: order.get(s[0], 99))
    return searches, k


# ---------------------------------------------------------------- attachments (#101)

def _att_type(content_type):
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct == "application/pdf":
        return "pdf"
    if ct.startswith("image/"):
        return "image"
    if ct in ("text/html", "application/xhtml+xml"):
        return "html"
    if ct.startswith("text/") or ct in ("application/xml", "application/json", "application/rtf"):
        return "text"
    return "other"


def _rules(db, c):
    return conns.attach_rules(db, c)


def _ref(c, rel):
    return hashlib.sha256((c["fhir_base"] + "/" + rel).encode()).hexdigest()


def _title(r):
    if r["resourceType"] == "DocumentReference":
        t = r.get("description") or ((r.get("type") or {}).get("text") if isinstance(r.get("type"), dict) else None)
    else:
        t = (r.get("code") or {}).get("text") if isinstance(r.get("code"), dict) else None
    return (t or r["resourceType"])[:160]


def _attachments(reader, resources, rules, used):
    """Fetch what the rules allow: ({rel: bytes}, [what was left out], bytes fetched, could not be read)."""
    binaries, left_out, added, missing = {}, [], 0, 0
    limit, budget = rules["max_mb"] * config.MB, rules["budget_mb"] * config.MB
    for r in resources:
        if r.get("resourceType") not in conns.BINARY_FOR:
            continue
        for rel, url, key, a in _attachment_urls(r, reader.base):
            if rel in binaries:
                continue
            ct = a.get("contentType") if isinstance(a.get("contentType"), str) else None
            size = a.get("size") if isinstance(a.get("size"), int) and not isinstance(a.get("size"), bool) else None
            info = {"rel": rel, "record": key, "title": _title(r), "content_type": ct, "type": _att_type(ct), "size": size}
            why = ("ask" if not rules["auto"] else
                   "type" if ct and info["type"] not in rules["types"] else
                   "size" if size is not None and size > limit else
                   "budget" if used + added + (size or 0) > budget else None)
            if why:
                left_out.append({**info, "why": why})
                continue
            try:
                resp = reader.get(url, accept="*/*", max_bytes=limit)
            except smart.TooBig:
                left_out.append({**info, "why": "size"})
                continue
            except smart.Refused:
                missing += 1
                continue
            body = resp.body
            if not ct:   # only the answer says what it is
                ct = resp.headers.get("content-type")
                info.update(content_type=ct, type=_att_type(ct))
                if info["type"] not in rules["types"]:
                    left_out.append({**info, "size": len(body), "why": "type"})
                    continue
            if used + added + len(body) > budget:
                left_out.append({**info, "size": len(body), "why": "budget"})
                continue
            binaries[rel] = body
            added += len(body)
    return binaries, left_out, added, missing


WHY = {"ask": "only fetched when you ask", "type": "a kind of file this connection doesn't fetch",
       "size": "bigger than this connection's limit", "budget": "over this connection's total for attachments"}


def _fetch(reader, patient, cursor, searches=None, rules=None, used=0, scopes=None):
    """Read what's new: {"resources", "binaries" {rel: bytes}, "cursor", "report", "warnings", "left_out", "added"}.
    searches: plan()'s (every type, as before #99, when None); rules: the attachment rules (#101);
    scopes: the granted scopes; a type they don't cover isn't asked for (#92)."""
    left, resources, report, warnings = config.setting("SYNC_MAX_RESOURCES"), [], {}, []
    searches, kinds = searches or ([(t, {}, t, None) for t in conns.TYPES[1:]], set(conns.KINDS))
    rules = rules or {"auto": True, "types": ["pdf", "image", "text", "html", "other"], "budget_mb": 1 << 30,
                      "max_mb": config.setting("SYNC_MAX_ATTACHMENT_MB")}
    cursor = cursor or {}
    new_cursor = {}   # only the searches made now: one dropped (a kind turned off) starts over when it comes back
    try:
        p = reader.get("Patient/" + urllib.parse.quote(patient, safe="")).json()
        if isinstance(p, dict) and p.get("resourceType") == "Patient":
            resources.append(p)
        report["Patient"] = {"n": 1, "status": "ok"}
    except smart.Forbidden:
        report["Patient"] = {"n": 0, "status": "not allowed"}
    except smart.Refused as e:
        report["Patient"] = {"n": 0, "status": "failed", "why": str(e)}
    for t, extra, key, keep in searches:
        if not conns.granted(scopes, t):
            continue
        if key in cursor:
            new_cursor[key] = cursor[key]   # kept unless this pull moves it on
        if left <= len(resources):
            report[t] = {"n": 0, "status": "next time"}
            continue
        params = {PATIENT_PARAM.get(t, "patient"): patient, "_count": PAGE, **extra}
        if cursor.get(key):
            params["_lastUpdated"] = cursor[key]
        try:
            got, stamp, cut = reader.search(t, params, left - len(resources))
        except smart.Forbidden:
            report[t] = {"n": 0, "status": "not allowed"}
            continue
        except smart.Refused as e:   # this type only; the vendor being down or access ending stops the pull
            report[t] = {"n": 0, "status": "failed", "why": str(e)}
            continue
        newest = _newest(got)   # of everything read, kept or not, so the cursor moves past both
        got = [r for r in got if keep is None or keep(r)]
        resources += got
        if cut:   # the rest next time: from the newest read (ge, since others may share its time)
            if newest:
                new_cursor[key] = "ge" + newest
            report[t] = {"n": len(got), "status": "more next time"}
        else:
            if stamp:
                new_cursor[key] = "gt" + stamp
            elif newest:
                new_cursor[key] = "ge" + newest
            report[t] = {"n": len(got), "status": "ok"}
    binaries, left_out, added, missing = {}, [], 0, 0
    if "documents" in kinds:
        binaries, left_out, added, missing = _attachments(reader, resources, rules, used)
    if left_out:
        n = len(left_out)
        warnings.append(f"Left out {n} attachment{'s' if n != 1 else ''} ("
                        + ", ".join(sorted({WHY[x['why']] for x in left_out})) + "): listed under Attachments left out, "
                        "to fetch if you want them.")
    if missing:
        warnings.append(f"{missing} attachment{'s' if missing != 1 else ''} couldn't be read from the vendor.")
    return {"resources": resources, "binaries": binaries, "cursor": new_cursor, "report": report, "warnings": warnings,
            "left_out": left_out, "added": added}


def _upload(db, c, got):
    pid = c["profile_id"]
    up = uploads.create(db, pid, c["created_by"])
    try:
        uploads.add_file(db, pid, up, "pull.json", io.BytesIO(_bundle(c["fhir_base"], got["resources"])))
        for rel, body in sorted(got["binaries"].items()):
            uploads.add_file(db, pid, up, rel, io.BytesIO(body))
        uploads.finish(db, pid, up, unzip=False)
    except BaseException:
        with contextlib.suppress(uploads.UploadError):
            uploads.discard(db, pid, up)
        raise
    return up


def _merge_store(c, got):
    store = _load_store(c["profile_id"], c["id"])
    for r in got["resources"]:
        if r.get("resourceType") and r.get("id"):
            store["resources"][f"{r['resourceType']}/{r['id']}"] = r
    return store


def _report(got, extra=None):
    return {"types": got["report"], "warnings": got["warnings"], **(extra or {})}


def _first(db, c, got, who, ip):
    """Everything, through the job flow: an upload, then an import job previewed like any other."""
    cid, pid = c["id"], c["profile_id"]
    store = _merge_store(c, got)
    if len(got["resources"]) <= 1:   # the patient, nothing else: no import to preview; later pulls bring what comes
        with dbm.transaction(db):
            _keep_store(db, c, store)
            db.execute("""UPDATE connections SET cursor=?, pending_cursor=NULL, last_sync=?, next_sync=?, failures=0,
                          error_kind=NULL, last_error=NULL, report=?, warned=NULL WHERE id=? AND state='active'""",
                       (json.dumps(got["cursor"]), dbm.now(), dbm.now() + config.setting("SYNC_HOURS") * 3600,
                        conns._jenc(_report(got)), cid))
            _note_attachments(db, c, got)
        return {"ok": True, "first": True, "job": None}
    up = _upload(db, c, got)
    try:
        # the store, the job and the connection's first_job in one transaction, under the check that it's still active
        # (#592): a Disconnect is saved before it (nothing kept, no job) or after it, and then finds the job to cancel.
        # The job is queued once this commits
        with dbm.transaction(db):
            _keep_store(db, c, store)
            jid = jobs.submit(db, pid, up, (who or {}).get("id") or c["created_by"], "fhir", ip)
            db.execute("""UPDATE connections SET first_job=?, pending_cursor=?, last_sync=?, next_sync=NULL, failures=0,
                          error_kind=NULL, last_error=NULL, report=?, warned=NULL WHERE id=? AND state='active'""",
                       (jid, json.dumps(got["cursor"]), dbm.now(), conns._jenc(_report(got)), cid))
            _note_attachments(db, c, got)
    except BaseException:
        with contextlib.suppress(uploads.UploadError):
            uploads.discard(db, pid, up)
        raise
    return {"ok": True, "first": True, "job": jid}


def _note_attachments(db, c, got):
    """In the pull's transaction: list the attachments it left out (once each), take those it fetched off the
    list, and count what it fetched toward the connection's total."""
    now = dbm.now()
    for x in got.get("left_out") or []:
        db.execute("""INSERT INTO sync_skipped(connection_id, profile_id, ref_hash, detail, found) VALUES(?, ?, ?, ?, ?)
                      ON CONFLICT(connection_id, ref_hash) DO UPDATE SET detail=excluded.detail, found=excluded.found,
                      import_id=NULL""",
                   (c["id"], c["profile_id"], _ref(c, x["rel"]), conns._jenc(x), now))
    for rel in got.get("binaries") or {}:
        db.execute("DELETE FROM sync_skipped WHERE connection_id=? AND ref_hash=? AND import_id IS NULL", (c["id"], _ref(c, rel)))
    if got.get("added"):
        db.execute("UPDATE connections SET attach_bytes=attach_bytes+? WHERE id=?", (got["added"], c["id"]))


def first_saved(db, jid, iid):
    """jobs._save, in its transaction: the first pull of a connection was saved. Its cursor becomes the
    connection's, and its import belongs to the connection. Disconnected since (#592): Disconnected, so the save is
    rolled back, nothing from the pull kept (Disconnect cancels the job too: this is for one it couldn't)."""
    c = db.execute("SELECT * FROM connections WHERE first_job=? AND cursor IS NULL", (jid,)).fetchone()
    if not c:
        return
    if c["state"] == "disconnected":
        raise Disconnected(DISCONNECTED)
    if iid is not None:
        db.execute("UPDATE imports SET connection_id=?, synced=? WHERE id=?", (c["id"], dbm.now(), iid))
        _fold_first(db, c, jid, iid)
    db.execute("""UPDATE connections SET cursor=IFNULL(pending_cursor, '{}'), pending_cursor=NULL,
                  next_sync=CASE WHEN state='active' THEN ? ELSE NULL END WHERE id=?""",
               (dbm.now() + config.setting("SYNC_HOURS") * 3600, c["id"]))


def _fold_first(db, c, jid, iid):
    """(#910) The first pull's lab days (import iid, of job jid), each joining the lab day of its source's draw where the
    profile has one (_draw_day: one listing a lab event that holds a record of what the connection has read, an
    upload's of part of the portal's export), as a later pull joins it: one lab day a draw from the first pull too. Its
    own lab day held only what the upload hadn't (the LDL and the urine colour beside the upload's CBC), and a later
    pull's lab day then listed the upload's CBC as well. The lab day joined is kept as it was (sync_changes), for
    undoing iid; the job's counts lose the lab day it didn't keep."""
    pid = c["profile_id"]
    mine = [r for r in db.execute("SELECT id, key_hash, detail FROM events WHERE import_id=?", (iid,)).fetchall()
            if (d := records._dec(r["detail"])) and isinstance(d, dict) and d.get("type") == "labday"]
    store = _load_store(pid, c["id"]) if mine else {"resources": {}}
    # the records it has read, as the FHIR importer keys them (one server's: "fhir:<type>/<id>"), without reading them
    # all again (the preview did)
    src = {f"fhir:{k}" for k in store["resources"] if k.split("/")[0] in ("Observation", "DiagnosticReport")}
    if not src:
        return
    n = 0
    for r in mine:
        day = _draw_day(db, pid, set(), r["key_hash"], src, but=r["id"], earliest=False)
        if day is None:
            continue
        old = day["detail"].get("ids") or []
        ids = old + [i for i in records._dec(r["detail"]).get("ids") or [] if i not in old]
        ids = [i for i in ids if i in _there(db, ids)]
        _changing(db, iid, {"id": day["id"], "import_id": day["import_id"], "body": records._enc(day["body"]), "detail": records._enc(day["detail"])})
        db.execute("UPDATE events SET body=?, detail=? WHERE id=?",
                   (records._enc(_labday_body(db, day["body"], ids)), records._enc({**day["detail"], "ids": ids}), day["id"]))
        db.execute("DELETE FROM events WHERE id=?", (r["id"],))
        n += 1
    if n:
        j = db.execute("SELECT summary FROM jobs WHERE id=?", (jid,)).fetchone()
        s = json.loads(crypto.dec(j["summary"])) if j and j["summary"] else None
        counts = (s or {}).get("counts") or {}
        if isinstance(counts.get("kinds"), dict) and counts["kinds"].get("lab"):
            counts["kinds"]["lab"] -= n
            counts["kinds"] = {k: v for k, v in counts["kinds"].items() if v}
            counts["events"] = max(0, (counts.get("events") or 0) - n)
            db.execute("UPDATE jobs SET summary=? WHERE id=?", (crypto.enc(json.dumps(s)), jid))
        records._bump(db, pid)


# ---------------------------------------------------------------- a later pull: new records added, changed ones updated in place

def _parse(files):
    try:
        data = registry.get("fhir").parse(files)["data"]
    except ValueError:   # nothing in it Sanguity shows (only claims or patients, say)
        return None
    # details by event id as text, as everything here looks them up (and as a job's saved preview has them): the
    # importer numbers them with ints, and without this a later pull's new records lost their details, and a record
    # updated in place had its details cleared
    data["details"] = {str(k): v for k, v in (data.get("details") or {}).items()}
    return data


def _whole(c, store, files=None):
    """The records the connection's whole set read so far (its store) makes, as the importer reads them, with the pull's
    files (files, its attachments) beside it; None where it holds nothing Sanguity shows."""
    tmp = tempfile.mkdtemp(prefix="sanguity-sync-")
    try:
        os.chmod(tmp, 0o700)
        path = os.path.join(tmp, "connection.json")
        with open(path, "wb") as f:
            f.write(_bundle(c["fhir_base"], store["resources"].values()))
        return _with_ext(_parse({**{k: v for k, v in (files or {}).items() if k != "pull.json"}, "connection.json": path}))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _with_ext(data):
    for d in (data or {}).get("details", {}).values():   # as jobs._import: the type comes from the path
        if isinstance(d, dict) and d.get("type") == "file" and isinstance(d.get("url"), str) and not d.get("ext"):
            ext = os.path.splitext(d["url"].split("#", 1)[0])[1].lstrip(".").lower()
            if ext:
                d["ext"] = ext
    return data


def _touched(delta, delta_resources, store):
    """Source keys of the events what this pull read makes: its own, the reports grouping a result it read, and the
    claims an adjusted claim it read replaces."""
    keys = {e["key"] for e in delta["events"] if isinstance(e, dict) and isinstance(e.get("key"), str)}
    read = {f"Observation/{r.get('id')}" for r in delta_resources if r.get("resourceType") == "Observation"}
    for r in store["resources"].values():
        if r.get("resourceType") != "DiagnosticReport":
            continue
        refs = {"/".join(str((x or {}).get("reference") or "").split("/_history")[0].split("/")[-2:]) for x in r.get("result") or []}
        if refs & read:
            keys.add(f"fhir:DiagnosticReport/{r.get('id')}")
    # an adjusted claim read now marks the one it replaces, saved by an earlier pull, as replaced (#92)
    from .importers import _fhir_claims
    older = set().union(*(_fhir_claims.replaces(r) for r in delta_resources if r.get("resourceType") == "ExplanationOfBenefit"))
    if older:
        for r in store["resources"].values():
            if r.get("resourceType") == "ExplanationOfBenefit" and _fhir_claims.names_of(r) & older:
                keys.add(f"fhir:ExplanationOfBenefit/{r.get('id')}")
    return keys


def _rows_by_hash(db, pid, hashes):
    """{key hash: [event ids]}, any import's."""
    out = {}
    for h in records._chunks(sorted({x for x in hashes if x})):
        q = f"SELECT id, key_hash FROM events WHERE profile_id=? AND key_hash IN ({','.join('?' * len(h))}) ORDER BY id"
        for r in db.execute(q, (pid, *h)):
            out.setdefault(r["key_hash"], []).append(r["id"])
    return out


def _ours(db, pid, cid):
    """The imports connection cid saved (its first pull's and every later one's)."""
    return {r[0] for r in db.execute("SELECT id FROM imports WHERE profile_id=? AND connection_id=?", (pid, cid))}


# (#899) A labitem keyed by its day (a FHIR file's results on their own, preview.day_key) is no one source's record: its
# rows are, each its own Observation (the details' "keys", #885). A pull finds each row by that record wherever it is,
# whoever's import holds it (an upload of the portal's own export, the records of a connection made before this one
# to the same portal), and updates it there; a row new to the profile goes in a labitem of this pull's own, beside its
# result; and a row whose result is left out, a duplicate of one here, is left out with it (#897: in the pull's lab day
# it read another source's result's flag). (#881) A report's labitem the profile has is placed the same way, row by row,
# its report's own details (status, narrative) updated where it is: a result the source added to the report goes in a
# labitem of the pull's own of that report, beside its result. Updated whole, as before, the new row sat in the
# report's labitem of another import (an upload's, the first pull's), and the drawer, finding a row's result in the
# row's own import (drawer.js rowPoint, crossmatch._row_of), found none: a result with no row, a row with no result.
# Every row sits in a labitem of its result's own import, so Undo and Remove take a result and its row together.

ROW_FIELDS = ("rows", "keys", "series")   # a labitem's details that go row by row; the rest are its report's


def _day_labitem(e):
    """Whether event e is a labitem keyed by its day, whose rows are each found by its own record (#899)."""
    return isinstance(e, dict) and e.get("k") == "labitem" and preview.day_key(e.get("key"))


def _labitem(e):
    return isinstance(e, dict) and e.get("k") == "labitem" and isinstance(e.get("key"), str)


def _row_keys(d):
    """[(its own record's key, which of that record's rows it is)] for each row of a labitem's details d (an Observation
    with components makes a row of each): None for a row without one of its own (a hash of its content, or saved before
    #885 without "keys", and not given them, backfill_keys)."""
    keys = d.get("keys") if isinstance(d, dict) and isinstance(d.get("keys"), list) else []
    rows, seen, out = (d.get("rows") if isinstance(d, dict) else None) or [], {}, []
    for j in range(len(rows)):
        k = keys[j] if j < len(keys) and preview._record_key(keys[j]) else None
        out.append(k and (k, seen.setdefault(k, 0)))
        if k:
            seen[k] += 1
    return out


def _items_here(db, pid, hashes):
    """{event id: {"id", "import_id", "key_hash", "body", "detail"}}: the profile's labitems with these key hashes, any
    import's, decrypted."""
    out = {}
    for h in records._chunks(sorted({x for x in hashes if x})):
        q = f"SELECT id, import_id, key_hash, body, detail FROM events WHERE profile_id=? AND key_hash IN ({','.join('?' * len(h))}) ORDER BY id"
        for r in db.execute(q, (pid, *h)).fetchall():
            b = records._dec(r["body"]) or {}
            if b.get("k") == "labitem":
                out[r["id"]] = {"id": r["id"], "import_id": r["import_id"], "key_hash": r["key_hash"], "body": b,
                                "detail": records._dec(r["detail"])}
    return out


def _connected(db, c):
    """The imports a connection of this profile to the same server (c's fhir_base) saved: c's, and those of the same
    portal connected before (disconnected keeping its records)."""
    return {r[0] for r in db.execute("""SELECT id FROM imports WHERE connection_id IN (SELECT id FROM connections WHERE
                                        profile_id=? AND fhir_base=?)""", (c["profile_id"], c["fhir_base"]))}


def _lab_days_listing(db, pid, days):
    """The FHIR lab days of these days (key "fhir:labday:<day>", any import's): [{"id", "import_id", "key_hash", "body",
    "detail"}]."""
    hs = records.key_hashes(db, [f"fhir:labday:{d}" for d in days])
    out = []
    for h in records._chunks(sorted(set(hs.values()))):
        q = f"SELECT id, import_id, key_hash, body, detail FROM events WHERE profile_id=? AND key_hash IN ({','.join('?' * len(h))}) ORDER BY id"
        out += [{"id": r["id"], "import_id": r["import_id"], "key_hash": r["key_hash"], "body": records._dec(r["body"]) or {},
                 "detail": records._dec(r["detail"])} for r in db.execute(q, (pid, *h)).fetchall()]
    return [x for x in out if isinstance(x["detail"], dict) and x["detail"].get("type") == "labday"]


def _apart(db, pid, items, imps):
    """(#881) Rows saved apart from their results, before every row sat beside its result: a pull's new result whose
    row went in the first pull's labitem of that day (before #899), a result added to a report whose row went in the
    report's labitem of another import (before #881). Each row of these labitems (_items_here) with no result of its
    own import, whose result is a result of one of imps (the imports a connection to this portal saved, _connected)
    with no row of its own, is moved into a labitem of that import (with the same key, one it has or a new one), listed
    by the lab days that list the one it was in. A repair, not a change a pull made: no Undo puts it back where it was
    (what a pull's Undo would put back there loses it too). -> how many rows moved"""
    if not items or not imps:
        return 0
    day = lambda li: str(li["body"].get("t") or "")[:10]   # noqa: E731
    days = {day(li) for li in items.values()}
    names = {str(row[0] or "").strip().lower() for li in items.values() for row in (li["detail"] or {}).get("rows") or [] if isinstance(row, list) and row}
    names |= {(common.canonical_lab(n) or n).lower() for n in names}
    sids = {}
    ids = sorted({li["import_id"] for li in items.values()} | set(imps))
    for ch in records._chunks(ids):
        for r in db.execute(f"SELECT id, import_id, body FROM series WHERE profile_id=? AND import_id IN ({','.join('?' * len(ch))})", (pid, *ch)).fetchall():
            b = records._dec(r["body"]) or {}
            if str(b.get("name") or "").strip().lower() in names or (common.canonical_lab(str(b.get("name") or "")) or "").lower() in names:
                sids[r["id"]] = {**b, "imp": r["import_id"], "pts": [], "txt": [t for t in b.get("txt") or [] if isinstance(t, list) and t and str(t[0])[:10] in days]}
    for ch in records._chunks(sorted(sids)):
        for r in db.execute(f"SELECT series_id, body FROM results WHERE series_id IN ({','.join('?' * len(ch))}) ORDER BY id", ch).fetchall():
            p = records._dec(r["body"])
            if isinstance(p, list) and p and str(p[0])[:10] in days:
                sids[r["series_id"]]["pts"].append(p)
    labs = list(sids.values())
    rows = crossmatch._rows({"events": [{"id": i, "k": "labitem", "t": li["body"].get("t"), "imp": li["import_id"]} for i, li in items.items()],
                             "details": {str(i): li["detail"] for i, li in items.items()}})
    paired, lost = set(), []
    for s in labs:
        for p in s["pts"] + s["txt"]:
            at = crossmatch._row_of(rows, s, p)
            if at:
                paired.add(at)
            elif s["imp"] in imps:
                lost.append((s, p))
    loose = collections.defaultdict(list)   # {day: rows with no result of their own import}
    for (_, d), rs in rows.items():
        loose[d] += [x for x in rs if x[:2] not in paired]
    moves = {}
    for s, p in lost:
        cand = [x for x in loose[crossmatch._day(p[0])] if x[:2] not in moves and items[x[0]]["import_id"] != s["imp"]]
        at = crossmatch._row_of({(s["imp"], crossmatch._day(p[0])): cand}, s, p)
        if at:
            moves[at] = s["imp"]
    if not moves:
        return 0
    lab_days = _lab_days_listing(db, pid, days)
    for (eid, target) in sorted({(e, t) for (e, _), t in moves.items()}):
        li, rs = items[eid], sorted(r for (e, r), t in moves.items() if e == eid and t == target)
        d = li["detail"]
        keys = d.get("keys") if isinstance(d.get("keys"), list) else [None] * len(d["rows"])
        moved = {"rows": [d["rows"][r] for r in rs], "keys": [keys[r] if r < len(keys) else None for r in rs]}
        tgt = next((x for x in items.values() if x["import_id"] == target and x["key_hash"] == li["key_hash"] and day(x) == day(li)), None)
        if tgt:
            td = tgt["detail"]
            tk = td.get("keys") if isinstance(td.get("keys"), list) else [None] * len(td["rows"])
            td = {**td, "rows": td["rows"] + moved["rows"], "keys": tk + moved["keys"]}
            db.execute("UPDATE events SET body=?, detail=? WHERE id=?", (records._enc(_with_abn(tgt["body"], td["rows"])), records._enc(td), tgt["id"]))
            tgt["detail"] = td
        else:
            nd = {**{k: v for k, v in d.items() if k not in ROW_FIELDS}, **moved}
            new = db.execute("INSERT INTO events(profile_id, import_id, body, detail, key_hash) VALUES(?, ?, ?, ?, ?)",
                             (pid, target, records._enc(_with_abn(li["body"], moved["rows"])), records._enc(nd), li["key_hash"])).lastrowid
            items[new] = {"id": new, "import_id": target, "key_hash": li["key_hash"], "body": _with_abn(li["body"], moved["rows"]), "detail": nd}
            for ld in lab_days:
                if eid in ld["detail"].get("ids") or []:
                    at = ld["detail"]["ids"].index(eid) + 1
                    ld["detail"] = {**ld["detail"], "ids": ld["detail"]["ids"][:at] + [new] + ld["detail"]["ids"][at:]}
                    db.execute("UPDATE events SET detail=? WHERE id=?", (records._enc(ld["detail"]), ld["id"]))
        left = [j for j in range(len(d["rows"])) if j not in rs]
        gone = {k for k in moved["keys"] if k}
        if left:
            d = {**d, "rows": [d["rows"][j] for j in left], **({"keys": [keys[j] for j in left]} if isinstance(d.get("keys"), list) else {})}
            db.execute("UPDATE events SET body=?, detail=? WHERE id=?", (records._enc(_with_abn(li["body"], d["rows"])), records._enc(d), eid))
            li["detail"] = d
        else:
            db.execute("DELETE FROM events WHERE id=?", (eid,))
            del items[eid]
            for ld in lab_days:
                if eid in ld["detail"].get("ids") or []:
                    ld["detail"] = {**ld["detail"], "ids": [i for i in ld["detail"]["ids"] if i != eid]}
                    db.execute("UPDATE events SET detail=? WHERE id=?", (records._enc(ld["detail"]), ld["id"]))
        for x in db.execute("SELECT id, previous FROM sync_changes WHERE event_id=?", (eid,)).fetchall() if left else []:
            prev = records._dec(x["previous"])   # what an Undo puts back there never brings the rows moved out
            pd = prev.get("detail")
            if isinstance(pd, dict) and isinstance(pd.get("rows"), list) and isinstance(pd.get("keys"), list) and gone & set(pd["keys"]):
                keep = [j for j, k in enumerate(pd["keys"]) if k not in gone]
                pd = {**pd, "rows": [pd["rows"][j] for j in keep if j < len(pd["rows"])], "keys": [pd["keys"][j] for j in keep]}
                db.execute("UPDATE sync_changes SET previous=? WHERE id=?", (records._enc({**prev, "detail": pd}), x["id"]))
    for ld in lab_days:   # titled for what they hold
        body = _labday_body(db, ld["body"], ld["detail"].get("ids") or [])
        if not _same(body, ld["body"]):
            db.execute("UPDATE events SET body=? WHERE id=?", (records._enc(body), ld["id"]))
    records._bump(db, pid)
    return len(moves)


def _rows_here(items, ours):
    """{(own record, n): (event id, row index, row)}: the rows of these labitems (_items_here), any import's, by their
    own records (_row_keys); the connection's own (ours: its imports) first, the latest, then the earliest."""
    out = {}
    for r in sorted(items.values(), key=lambda r: (r["import_id"] not in ours, -r["id"] if r["import_id"] in ours else r["id"])):
        for j, own in enumerate(_row_keys(r["detail"])):
            if own:
                out.setdefault(own, (r["id"], j, r["detail"]["rows"][j]))
    return out


def _left_out(delta, dups):
    """The rows of this pull's labitems whose results are left out, duplicates of results here (#897): {(event id as
    text, row index)}, the row as preview._row finds a result's (crossmatch._row_of)."""
    labs, drawer, out = delta.get("labs") or [], preview._drawer(delta), set()
    for what, field in (("points", "pts"), ("txt", "txt")):
        for i, ns in (dups.get(what) or {}).items():
            got = labs[i].get(field) or [] if i < len(labs) and isinstance(labs[i], dict) else []
            out.update(at for n in ns if n < len(got) and (at := preview._row(drawer, labs[i], got[n])))
    return out


def _place(e, detail, here, sent, dropped, fresh=False, amended=()):
    """(#899, #881) Where the rows of e, a labitem as the whole set makes it (a day's, or a report's; detail its
    details), go: each found here by its own record (here: _rows_here), its value as reported the same or its result
    updated in place with the value amended (#877, amended: {(event id, row index)} here of the results find() updates),
    is updated in place there, where it differs; one this pull read (sent: {(own record, n): (event id, row index)} of
    its own labitems), new to the profile, goes in a labitem of this pull's own, unless its result is left out (dropped:
    _left_out). fresh: no labitem here has e's key, so a row with no record of its own (a hash of its content) goes in it
    too, as an upload's would. -> (that labitem or None, its details, {event id here: {row index: row}})"""
    rows, changes, new = detail.get("rows") or [], {}, []
    for j, own in enumerate(_row_keys(detail)):
        if own in here and (here[own][2][1:2] == rows[j][1:2] or here[own][:2] in amended):
            eid, r, _ = here[own]
            changes.setdefault(eid, {})[r] = rows[j]
        elif own in sent and sent[own] not in dropped or fresh and not own:
            new.append(j)
    if not new:
        return None, None, changes
    d = {**detail, "rows": [rows[j] for j in new], "keys": [(detail.get("keys") or [None] * len(rows))[j] for j in new]}
    return _with_abn(e, d["rows"]), d, changes


def _with_abn(body, rows):
    """A labitem's body with its count of flagged rows (abn, as the FHIR importer counts them) for these rows."""
    n = sum(1 for row in rows if isinstance(row, list) and len(row) > 4 and row[4])
    return {**{k: v for k, v in body.items() if k != "abn"}, **({"abn": n} if n else {})}


def _set_rows(db, pid, iid, changes, fields=None):
    """(#899) Rows found by their own records updated in place, in whichever import's labitem holds them, where they
    differ, and (#881) a report's own details (fields: {event id: {name: value}}, those not ROW_FIELDS) in each of its
    labitems: what each labitem was is kept (sync_changes) for undoing the pull. -> how many labitems changed"""
    n, fields = 0, fields or {}
    for eid in sorted(set(changes) | set(fields)):
        rows = changes.get(eid) or {}
        r = db.execute("SELECT id, import_id, body, detail FROM events WHERE id=? AND profile_id=?", (eid, pid)).fetchone()
        old = records._dec(r["detail"]) if r else None
        if not isinstance(old, dict) or not isinstance(old.get("rows"), list):
            continue
        d = {**{k: v for k, v in old.items() if k in ROW_FIELDS}, **(fields.get(eid) or {k: v for k, v in old.items() if k not in ROW_FIELDS}),
             "rows": [rows.get(j, row) for j, row in enumerate(old["rows"])]}
        if _same(d, old):
            continue
        _changing(db, iid, r)
        db.execute("UPDATE events SET body=?, detail=? WHERE id=?",
                   (records._enc(_with_abn(records._dec(r["body"]), d["rows"])), records._enc(d), eid))
        n += 1
    return n


def _differ(items, changes, fields):
    """Whether any of these labitems (_items_here) would change by _set_rows' changes and fields."""
    for eid in set(changes) | set(fields):
        old = (items.get(eid) or {}).get("detail")
        if not (isinstance(old, dict) and isinstance(old.get("rows"), list)):
            continue
        rows = changes.get(eid) or {}
        if any(j < len(old["rows"]) and not _same(row, old["rows"][j]) for j, row in rows.items()):
            return True
        if eid in fields and not _same(fields[eid], {k: v for k, v in old.items() if k not in ROW_FIELDS}):
            return True
    return False


def _changing(db, iid, r):
    """Before import iid changes event r (its id, import_id, body and detail) in place: what it was, kept once
    (sync_changes) for undoing iid, unless it's iid's own."""
    if r["import_id"] != iid and not db.execute("SELECT 1 FROM sync_changes WHERE import_id=? AND event_id=?",
                                                (iid, r["id"])).fetchone():
        db.execute("INSERT INTO sync_changes(import_id, event_id, previous) VALUES(?, ?, ?)",
                   (iid, r["id"], records._enc({"body": records._dec(r["body"]), "detail": records._dec(r["detail"])})))


def _events_with_key(db, pid, cid, key, cols):
    """The profile's events with source key key, cols of each: connection cid's own only where the key is scoped to a
    day (preview.day_key, #885), as an upload's or another source's lab day of that day isn't this one's to replace
    (#899: _link_labdays joins it)."""
    h = records.key_hashes(db, [key])[key]
    if preview.day_key(key):
        return db.execute(f"""SELECT {cols} FROM events WHERE profile_id=? AND key_hash=?
                              AND import_id IN (SELECT id FROM imports WHERE connection_id=?)""", (pid, h, cid)).fetchall()
    return db.execute(f"SELECT {cols} FROM events WHERE profile_id=? AND key_hash=?", (pid, h)).fetchall()


def _same(a, b):
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def _later(db, c, got, who, ip):
    cid, pid = c["id"], c["profile_id"]
    store = _merge_store(c, got)
    report = _report(got)
    if len(got["resources"]) <= 1:   # only the patient: nothing changed
        with dbm.transaction(db):
            _done(db, c, got, report)
        return {"ok": True, "import": None, "counts": None, "changed": 0}
    up = _upload(db, c, got)
    try:
        with uploads.open_plain(db, pid, up) as files:
            delta = _with_ext(_parse(files))
            full = _whole(c, store, files)
            names = set(files)
        result = _save(db, c, up, delta, full, got, store, names, report, who, ip)
    except BaseException:
        with contextlib.suppress(uploads.UploadError):
            uploads.discard(db, pid, up)
        raise
    if result.get("import") is None and not result.get("changed"):
        with contextlib.suppress(uploads.UploadError):
            uploads.discard(db, pid, up)
    return result


def _save(db, c, up, delta, full, got, store, names, report, who, ip):
    cid, pid = c["id"], c["profile_id"]
    if delta is None:
        with dbm.transaction(db):
            _keep_store(db, c, store)
            _done(db, c, got, report)
        return {"ok": True, "import": None, "counts": None, "changed": 0}
    full = full or delta
    touched = _touched(delta, got["resources"], store)
    full_by_key = {e["key"]: e for e in full["events"] if isinstance(e, dict) and isinstance(e.get("key"), str)}
    delta_by_key = {e["key"]: e for e in delta["events"] if isinstance(e, dict) and isinstance(e.get("key"), str)}

    # under the write lock from here (#592): which of these records the profile has already (the duplicates, the rows
    # to update in place) is asked where nothing else can save, so the same record saved meanwhile by an import, records
    # typed in or another pull is found, and not saved twice
    with dbm.transaction(db):
        # labs and the profile-wide lists come from what this pull read; lab names matched as remembered
        lab_plan = labmap.plan(db, pid, "fhir", delta)
        delta = labmap.apply(delta, lab_plan["auto"])
        # (#899, #881) the labitems this pull touched, a day's or a report's, are placed row by row; each row is found
        # by its own record in whichever labitem of those days holds it (a result added to a report the profile has in a
        # day's labitem before)
        items = {k for k in touched if _labitem(full_by_key.get(k) or delta_by_key.get(k))}
        days = {str((full_by_key.get(k) or delta_by_key.get(k)).get("t") or "")[:10] for k in items}
        hashes_on = records.key_hashes(db, items | {e["key"] for e in full["events"] if _labitem(e) and str(e.get("t") or "")[:10] in days})
        labitems = _items_here(db, pid, hashes_on.values())
        if _give_keys(db, c, labitems):   # (#909) lab events saved before #885, given their rows' records first
            labitems = _items_here(db, pid, hashes_on.values())
        if _apart(db, pid, labitems, _connected(db, c)):   # (#881) a row saved apart from its result, moved beside it first
            labitems = _items_here(db, pid, hashes_on.values())
        dups = preview.find(db, pid, "fhir", delta, whole=full)   # (#869: a result sent again found by its rows in the whole set)
        keyless_new = [e for e in delta["events"] if isinstance(e, dict) and not isinstance(e.get("key"), str)
                       and str(e.get("id")) not in dups["events"]]
        dups["events"] = {str(e.get("id")) for e in delta["events"] if isinstance(e, dict)}   # events: decided below
        hashes = records.key_hashes(db, touched)
        have = _rows_by_hash(db, pid, hashes.values())   # (any import's, #899: a lab day joined, a day's rows by their records)
        ours = _ours(db, pid, cid)
        here = _rows_here(labitems, ours)   # (#899) the rows here, by their own records
        mine = {str(e["id"]): _row_keys(delta.get("details", {}).get(str(e["id"])))
                for e in delta["events"] if isinstance(e, dict) and e.get("k") == "labitem"}
        sent = {own: (eid, j) for eid, owns in mine.items() for j, own in enumerate(owns) if own}
        # a result already here is kept as a copy only with its own row (#868): one whose row is a record the profile
        # has, updated in place below in its own import, would be a result of this pull's import with no lab day of
        # its own (util.js labDraws put it in every lab day of its date); left out, a duplicate. A row of it is where
        # its own record is here (#899), else new, in this pull's labitem beside its copy
        keys = {str(e.get("id")): e.get("key") for e in delta["events"] if isinstance(e, dict)}   # (each touched)
        for i, ns in dups["at"].items():
            for n, (eid, r) in list(ns.items()):
                k, owns = keys.get(eid), mine.get(eid) or []
                if (r < len(owns) and owns[r] in here) if k in items else hashes.get(k) in have:
                    preview.leave_out(dups, i, n)
        rest = preview.trim(delta, dups)
        dropped = _left_out(delta, dups)   # (#897) the rows of the results left out, duplicates: left out with them
        # (#877) the rows here of the results the source sent again amended, their value too: updated where they are
        amended = {(u["event"], u["row"]) for us in (dups.get("updates") or {}).values() for ul in us.values() for u in ul}
        # events come from the whole set read so far (a lab day groups results across pulls), ids as it numbers them;
        # one only this pull's records have (keyless, or not in the whole set) keeps its own, marked apart
        new_events, updates, src, rows_here, fields = [], [], {}, {}, {}
        # (#912, #910) each draw's lab day: the connection's own, else its source's (one listing its records), else
        # the earliest, else the pull's own, new
        mine_src = _source_records(full)
        labdays = {k for k in touched if (full_by_key.get(k) or {}).get("k") == "lab"
                   and ((full.get("details") or {}).get(str(full_by_key[k].get("id"))) or {}).get("type") == "labday"}
        chosen = {k: _draw_day(db, pid, ours, hashes.get(k), mine_src) for k in labdays}
        for k in sorted(touched):
            e, details = full_by_key.get(k), full.get("details", {})
            if e is None:
                e, details = delta_by_key.get(k), delta.get("details", {})
                e = e and {**e, "id": f"d{e.get('id')}", "_from": details.get(str(e.get("id")))}
            if e is None:
                continue
            i = str(e["id"])
            src[i] = e.pop("_from", None) if "_from" in e else details.get(i)
            if k in items:   # (#899, #881) each row where its own record is, else new, in a labitem of this pull's
                if preview._record_key(k):   # a report's own details, in each of its labitems here
                    fields.update({x: {f: v for f, v in (src[i] or {}).items() if f not in ROW_FIELDS}
                                   for x, li in labitems.items() if li["key_hash"] == hashes.get(k)})
                e, src[i], changes = _place(e, src[i] or {}, here, sent, dropped, hashes.get(k) not in have, amended)
                for eid, rs in changes.items():
                    rows_here.setdefault(eid, {}).update(rs)
                if e:
                    new_events.append(e)
            elif k in chosen:   # the connection's own updated, another's joined (_link_labdays), or the pull's own
                if chosen[k] is None:
                    new_events.append(e)
                elif chosen[k]["import_id"] in ours:
                    updates.append(e)
            elif hashes.get(k) in have:
                updates.append(e)
            else:
                new_events.append(e)
        for e in keyless_new:
            e = {**e, "id": f"d{e.get('id')}"}
            new_events.append(e)
            src[e["id"]] = delta.get("details", {}).get(e["id"][1:])
        ids = {str(e.get("id")) for e in new_events}
        costs = _costs_by_id(full, delta)   # a claim's Costs row goes with its event (#92)
        data = {**{k: v for k, v in rest.items() if k not in records.ROWS}, "events": [dict(e) for e in new_events],
                "details": {i: json.loads(json.dumps(src[i])) for i in ids if src.get(i) is not None}, "labs": rest["labs"],
                "costs": [{**costs[str(e["id"])], "id": e["id"]} for e in new_events if str(e["id"]) in costs]}
        for d in data["details"].values():   # a lab day's results are linked after saving, when they all have rows
            if isinstance(d, dict) and d.get("type") == "labday":
                d["ids"] = [i for i in d.get("ids") or [] if str(i) in ids]
        problems = registry.check({"data": data, "warnings": []}, names)
        if problems:
            raise jobs.Failed("The pulled records couldn't be loaded: " + "; ".join(problems[:5]) + ".")

        sealed = {rel: p for uid, rel, p in uploads.stored_files(db, pid) if uid == up and rel in names}

        def stored(url):
            p = sealed.get(url.split("#", 1)[0]) if isinstance(url, str) else None
            return os.path.realpath(p) if p and os.path.isfile(p) else None

        meta_changes = _meta_changes(db, pid, delta)
        if not (preview.adds_anything(data) or updates or meta_changes or preview.updates(dups) or _differ(labitems, rows_here, fields)):
            _keep_store(db, c, store)
            _done(db, c, got, report)
            return {"ok": True, "import": None, "counts": None, "changed": 0}

        summary = jobs.summarise(data)
        _keep_store(db, c, store)   # before the commit: if that fails, the next pull reads these again (harmless)
        keepdocs, n_docs = prof.index_documents(db, pid, data["details"], stored)
        iid = records.store(db, pid, data, "sync", fmt="fhir")
        prof.link_documents(db, iid, keepdocs)
        db.execute("UPDATE imports SET connection_id=? WHERE id=?", (cid, iid))
        labmap.used(db, lab_plan["ids"].values())   # the lab-name matches it was saved with (#500)
        changed = _update_in_place(db, pid, iid, updates, {**full.get("details", {}), **src}, stored, costs, cid)
        changed += _set_rows(db, pid, iid, rows_here, fields)   # (#899, #881) a day's rows and a report's, where each is
        changed += _apply_meta(db, iid, meta_changes)
        # results the source sent again, amended (#869): updated in place, after their rows (above: a report's labitem
        # whole, a day's rows where each is)
        if (amended := preview.update(db, pid, iid, delta, dups, rows=False)):
            summary["counts"]["updated"] = amended
        _link_labdays(db, pid, iid, full, set(full_by_key) & (touched | {e.get("key") for e in new_events}), cid, chosen)
        summary["counts"]["documents"] = n_docs
        summary["changed"] = changed
        summary["connection"] = c["source"]
        jid = db.execute("""INSERT INTO jobs(profile_id, upload_id, created_by, state, importer, warnings, summary, import_id,
                            created, started, finished) VALUES(?, ?, ?, 'done', 'fhir', ?, ?, ?, ?, ?, ?)""",
                         (pid, up, (who or {}).get("id"), crypto.enc(json.dumps(got["warnings"])), crypto.enc(json.dumps(summary)),
                          iid, dbm.now(), dbm.now(), dbm.now())).lastrowid
        report["import"], report["changed"], report["counts"] = iid, changed, summary["counts"]
        _done(db, c, got, report)
        activity.log(db, "import", who or conns.SYNC_ACTOR, pid,
                     {"importer": "fhir", "counts": summary["counts"], "job": jid, "changed": changed, "source": c["source"]}, ip=ip)
        records._bump(db, pid)
        return {"ok": True, "import": iid, "job": jid, "counts": summary["counts"], "changed": changed}


def _done(db, c, got, report):
    """A pull worked: the connection's cursor moves on, and its imports are up to date as of now (imports.synced,
    which the page shows, so the profile's records_rev moves too)."""
    now = dbm.now()
    if db.execute("UPDATE imports SET synced=? WHERE connection_id=?", (now, c["id"])).rowcount:
        records._bump(db, db.execute("SELECT profile_id FROM connections WHERE id=?", (c["id"],)).fetchone()["profile_id"])
    db.execute("""UPDATE connections SET cursor=?, last_sync=?, next_sync=?, failures=0, error_kind=NULL, last_error=NULL,
                  report=?, warned=NULL WHERE id=?""",
               (json.dumps(got["cursor"]), dbm.now(), dbm.now() + config.setting("SYNC_HOURS") * 3600, conns._jenc(report), c["id"]))
    _note_attachments(db, c, got)


def _file_url(old, new):
    """A document link keeps the document it points at: what the vendor sends names a file in the pull, not the app's."""
    if isinstance(old, dict) and isinstance(new, dict) and old.get("type") == "file" and new.get("type") == "file":
        return {**new, "url": old.get("url")}
    return new


def _comparable(d):
    return {k: v for k, v in d.items() if k not in ("url", "ids")} if isinstance(d, dict) else d


def _costs_by_id(full, delta):
    """{event id as _save numbers them: the whole set's, or "d<id>" for this pull's own: Costs row}."""
    out = {f"d{c.get('id')}": c for c in (delta or {}).get("costs") or [] if isinstance(c, dict) and c.get("id") is not None}
    out.update({str(c.get("id")): c for c in (full or {}).get("costs") or [] if isinstance(c, dict) and c.get("id") is not None})
    return out


def _update_in_place(db, pid, iid, updates, details, stored, costs=None, cid=None):
    """Change the events whose source key the profile has, where the vendor's version differs; keep what each was.
    A claim's Costs row changes with it (an adjusted claim's new amounts). One whose key is scoped to a day (a lab day:
    a day's labitem has its rows placed, _place) is this connection's (cid) only (#885: _events_with_key); another's is
    joined (_link_labdays)."""
    n = 0
    for e in updates:
        body = {k: v for k, v in e.items() if k != "id"}
        new_d = details.get(str(e.get("id")))
        new_c = (costs or {}).get(str(e.get("id")))
        for r in _events_with_key(db, pid, cid, e["key"], "id, body, detail, cost"):
            old_b, old_d = records._dec(r["body"]), records._dec(r["detail"])
            old_c = records._dec(r["cost"]) if r["cost"] is not None else None
            cost = {**new_c, "id": r["id"]} if new_c is not None else old_c   # a row the vendor's version lacks stays
            if isinstance(new_d, dict) and new_d.get("type") == "labday":   # titled for the rows it holds (_link_labdays)
                body = _labday_body(db, {k: v for k, v in e.items() if k != "id"}, (old_d or {}).get("ids") or [])
            if _same(old_b, body) and _same(_comparable(old_d), _comparable(new_d)) and _same(old_c, cost):
                continue
            d = _file_url(old_d, new_d)
            if isinstance(d, dict) and d.get("type") == "labday":
                d = {**d, "ids": (old_d or {}).get("ids", [])}   # linked again by _link_labdays
            if isinstance(d, dict) and d.get("type") == "file" and not d.get("url") and isinstance(new_d, dict) and new_d.get("url"):
                one = {"x": dict(new_d)}
                keep, _ = prof.index_documents(db, pid, one, stored)
                prof.link_documents(db, iid, keep)
                d = one["x"]
            db.execute("INSERT INTO sync_changes(import_id, event_id, previous) VALUES(?, ?, ?)",
                       (iid, r["id"], records._enc({"body": old_b, "detail": old_d, "cost": old_c})))
            db.execute("UPDATE events SET body=?, detail=?, cost=?, cost_seq=IFNULL(cost_seq, 0) WHERE id=?",
                       (records._enc(body), None if d is None else records._enc(d), None if cost is None else records._enc(cost), r["id"]))
            n += 1
    return n


def _source_records(full):
    """(#912) The records the whole set read so far (full) holds in its lab events: its reports' keys and its rows' own
    (an Observation's), each naming one record of this source."""
    out, details = set(), full.get("details") or {}
    for e in full.get("events") or []:
        if isinstance(e, dict) and e.get("k") == "labitem":
            if preview._record_key(e.get("key")):
                out.add(e["key"])
            d = details.get(str(e.get("id")))
            out |= {k for k in (d.get("keys") if isinstance(d, dict) and isinstance(d.get("keys"), list) else []) if preview._record_key(k)}
    return out


def _labday_rows(db, pid, h):
    """The profile's lab days with key hash h, any import's: [{"id", "import_id", "body", "detail"}], earliest first."""
    out = []
    for r in db.execute("SELECT id, import_id, body, detail FROM events WHERE profile_id=? AND key_hash=? ORDER BY id", (pid, h)).fetchall():
        d = records._dec(r["detail"])
        if isinstance(d, dict) and d.get("type") == "labday":
            out.append({"id": r["id"], "import_id": r["import_id"], "body": records._dec(r["body"]) or {}, "detail": d})
    return out


def _holds(db, pid, ids, mine):
    """Whether any of these lab events holds one of the source's records (mine: _source_records)."""
    for ch in records._chunks([i for i in ids if isinstance(i, int)]):
        marks = ",".join("?" * len(ch))
        for r in db.execute(f"SELECT body, detail FROM events WHERE profile_id=? AND id IN ({marks})", (pid, *ch)).fetchall():
            b, d = records._dec(r["body"]) or {}, records._dec(r["detail"])
            if b.get("key") in mine or any(k in mine for k in (d.get("keys") if isinstance(d, dict) and isinstance(d.get("keys"), list) else [])):
                return True
    return False


def _draw_day(db, pid, ours, h, mine, but=None, earliest=True):
    """(#912, #910) The lab day a pull of this source joins for the draw of the day whose lab day key hash is h: the
    connection's own (ours: its imports), the latest, where it has one; else the earliest that lists a lab event holding
    one of this source's records (mine: _source_records), an upload's of the portal's own export or a connection's to it
    made before, rather than another source's of that date (a hospital's FHIR file of the day, whose lab day it joined
    as the earliest, the portal's draw split across two); else (earliest) the earliest there is (#897: the source's copy
    of a hospital's result is that draw's); else None, and the pull's is its own, new. but: a lab day not to take. ->
    the lab day ({"id", "import_id", "body", "detail"}) or None"""
    days = [x for x in _labday_rows(db, pid, h) if x["id"] != but]
    own = [x for x in days if x["import_id"] in ours]
    if own:
        return own[-1]
    return next((x for x in days if _holds(db, pid, x["detail"].get("ids") or [], mine)), days[0] if days and earliest else None)


def _link_labdays(db, pid, iid, full, keys, cid=None, chosen=None):
    """Point each lab day this pull touched at its results' rows, across this connection's pulls and imports: one lab
    day a draw, whoever's came first (#899, #897), the one _draw_day chose (chosen: {its key: that lab day, or None for
    the pull's own, new}), never a second beside it. It keeps the lab events it lists (#885: never pointed at the pull's
    alone), and is joined by the connection's of that draw that no other lab day lists (#910: each lab event is listed
    by one lab day; the connection's own took a report's lab event another import's lab day listed, its rows in both
    drawers, the badge and the drawer apart). Its title counts the rows it holds (\"5 lab results\")."""
    events = {str(e.get("id")): e for e in full["events"] if isinstance(e, dict)}
    ours = _ours(db, pid, cid)
    for e in full["events"]:
        if not isinstance(e, dict) or e.get("key") not in keys:
            continue
        d = full["details"].get(str(e.get("id")))
        if not (isinstance(d, dict) and d.get("type") == "labday"):
            continue
        h = records.key_hashes(db, [e["key"]])[e["key"]]
        days = _labday_rows(db, pid, h)
        pick = (chosen or {}).get(e["key"])
        day = next((x for x in days if x["id"] == pick["id"]), None) if pick else next((x for x in days if x["import_id"] == iid), None)
        if day is None:
            continue
        elsewhere = {i for x in days if x["id"] != day["id"] for i in x["detail"].get("ids") or []}
        members = [events[str(i)]["key"] for i in d.get("ids") or [] if str(i) in events and events[str(i)].get("key")]
        mh = records.key_hashes(db, members)
        old = day["detail"].get("ids") or []
        ids = list(old)
        for k in members:
            for r in db.execute("SELECT id, import_id FROM events WHERE profile_id=? AND key_hash=? ORDER BY id", (pid, mh[k])).fetchall():
                if r["import_id"] in ours and r["id"] not in elsewhere and r["id"] not in ids:
                    ids.append(r["id"])
        ids = [i for i in ids if i in _there(db, ids)]
        body = _labday_body(db, day["body"], ids)
        if old == ids and _same(body, day["body"]):
            continue
        _changing(db, iid, {"id": day["id"], "import_id": day["import_id"], "body": records._enc(day["body"]), "detail": records._enc(day["detail"])})
        db.execute("UPDATE events SET body=?, detail=? WHERE id=?",
                   (records._enc(body), records._enc({**day["detail"], "type": "labday", "ids": ids}), day["id"]))


def _there(db, ids):
    """Which of these event ids there are."""
    out = set()
    for ch in records._chunks(sorted({i for i in ids if isinstance(i, int)})):
        out |= {x[0] for x in db.execute(f"SELECT id FROM events WHERE id IN ({','.join('?' * len(ch))})", ch)}
    return out


def _labday_body(db, body, ids):
    """A lab day's body said for what its labitems (ids) hold (#889, common.lab_day_counted: \"5 lab results\" and
    their tests, as the importers say one; a title of another form kept), its abn their count of flagged rows."""
    items = []
    for i in ids:
        r = db.execute("SELECT body, detail FROM events WHERE id=?", (i,)).fetchone()
        if r:
            items.append((records._dec(r["body"]) or {}, records._dec(r["detail"])))
    return common.lab_day_counted(body, items)


def _meta_changes(db, pid, delta):
    """Entries of the profile-wide lists (problems, upcoming...) with a source key some import has, that now differ:
    [(import id, path, key, new entry)]."""
    have = {}
    for r in db.execute("SELECT id, meta FROM imports WHERE profile_id=? ORDER BY id", (pid,)):
        for path, items in preview._meta_lists(records._dec(r["meta"]) or {}).items():
            for x in items:
                if isinstance(x, dict) and isinstance(x.get("key"), str):
                    have[(path, x["key"])] = (r["id"], x)
    out = []
    for path, items in preview._meta_lists(delta).items():
        for x in items:
            if isinstance(x, dict) and isinstance(x.get("key"), str) and (path, x["key"]) in have:
                iid, old = have[(path, x["key"])]
                if not _same(old, x):
                    out.append((iid, path, x["key"], x))
    return out


def _meta_list(meta, path):
    top, _, sub = path.partition(".")
    holder = meta.get(top) if sub else meta
    return holder.get(sub or top) if isinstance(holder, dict) else None


def _apply_meta(db, iid, changes):
    n = 0
    for target, path, key, entry in changes:
        r = db.execute("SELECT meta FROM imports WHERE id=?", (target,)).fetchone()
        meta = records._dec(r["meta"]) or {}
        items = _meta_list(meta, path)
        if not isinstance(items, list):
            continue
        for i, x in enumerate(items):
            if isinstance(x, dict) and x.get("key") == key:
                db.execute("INSERT INTO sync_changes(import_id, meta_import, previous) VALUES(?, ?, ?)",
                           (iid, target, records._enc({"path": path, "key": key, "entry": x})))
                items[i] = entry
                n += 1
                break
        db.execute("UPDATE imports SET meta=? WHERE id=?", (records._enc(meta), target))
    return n


# ---------------------------------------------------------------- lab events saved before #885 (#899, schema step 40)

KEYS_TO_GIVE = "labitem_keys"   # meta, set by schema step 40: backfill_keys hasn't run yet


def backfill_keys(db):
    """Once, at start-up after schema step 40 (#899): the labitems a connection's pulls saved, or changed in place,
    before #885 gave each row its own record ("keys") take them from the connection's whole set read so far (its
    store), each row the one that set makes the same (name, value, unit, range and flag, else name, value and unit).
    Nothing else holds them: a row is [name, value, unit, range, flag] and a result [time, value, low, high, flag...],
    with no Observation id, and an upload's files go once its job ends, so an upload's labitems keep none. A
    connection's store goes at Disconnect: its records take theirs from another connection's to the same server
    (fhir_base) of the same profile, the same portal connected again. -> how many labitems were given keys"""
    if not dbm.meta_get(db, KEYS_TO_GIVE):
        return 0
    n = 0
    for c in db.execute("SELECT * FROM connections ORDER BY id").fetchall():
        store = _load_store(c["profile_id"], c["id"])
        if len(store["resources"]) <= 1:
            continue
        try:
            full = _whole(c, store) or {}
        except Exception:   # one store that can't be read stops none of the rest, nor the start-up
            log.exception("connection %s: its store couldn't be read to give its lab events their keys", c["id"])
            continue
        made = {e["key"]: full["details"].get(str(e["id"])) for e in full.get("events") or []
                if isinstance(e, dict) and e.get("k") == "labitem" and isinstance(e.get("key"), str)}
        with dbm.transaction(db):
            imps = [r[0] for r in db.execute("""SELECT id FROM imports WHERE connection_id IN (SELECT id FROM connections
                                                WHERE profile_id=? AND fhir_base=?)""", (c["profile_id"], c["fhir_base"]))]
            q = ",".join("?" * len(imps))
            given = 0
            for r in db.execute(f"""SELECT id, body, detail FROM events WHERE import_id IN ({q}) OR id IN
                                    (SELECT event_id FROM sync_changes WHERE import_id IN ({q}))""", imps + imps).fetchall() if imps else []:
                b, d = records._dec(r["body"]) or {}, records._dec(r["detail"])
                f = made.get(b.get("key")) if b.get("k") == "labitem" else None
                if not (isinstance(f, dict) and isinstance(d, dict) and isinstance(d.get("rows"), list)) or "keys" in d:
                    continue
                keys = _match_keys(d["rows"], f.get("rows") or [], f.get("keys") or [])
                if any(keys):
                    db.execute("UPDATE events SET detail=? WHERE id=?", (records._enc({**d, "keys": keys}), r["id"]))
                    given += 1
            if given:
                records._bump(db, c["profile_id"])
            n += given
    dbm.meta_set(db, KEYS_TO_GIVE, None)
    return n


def _give_keys(db, c, items):
    """(#909) At a pull, as backfill_keys does at start-up: the labitems of the days it reads (items: _items_here) saved
    before #885 with no "keys" take their rows' records from the whole set connection c had read before this pull (its
    store as it was), each row the one that set's labitem of the same key makes the same (_match_keys): one a connection
    to the same portal saved (its store gone at Disconnect, so backfill_keys had none to give from: the portal connected
    again after the upgrade), and an upload of the portal's own export (its files gone with its job) where every row is
    one of that labitem's and it was collected at the same time; another source's file of that day, under the same day's
    key, never is. Then a corrected range finds the row and its result here, the same record, and updates both in place:
    it added a second result. What an Undo would put back there gets them too. -> how many labitems were given keys"""
    keyless = [li for li in items.values() if isinstance(li["detail"], dict) and isinstance(li["detail"].get("rows"), list)
               and li["detail"]["rows"] and "keys" not in li["detail"]]
    if not keyless:
        return 0
    connected = _connected(db, c)
    uploaded = {r[0] for r in db.execute("SELECT id FROM imports WHERE profile_id=? AND origin='upload' AND format='fhir' AND connection_id IS NULL",
                                         (c["profile_id"],))}
    keyless = [li for li in keyless if li["import_id"] in connected | uploaded]
    store = _load_store(c["profile_id"], c["id"])   # (as it was: this pull's is written once it's saved)
    if not keyless or len(store["resources"]) <= 1:
        return 0
    try:
        full = _whole(c, store) or {}
    except Exception:   # a store that can't be read gives none, as at start-up
        log.exception("connection %s: its store couldn't be read to give lab events their keys", c["id"])
        return 0
    made = {records.key_hashes(db, [e["key"]])[e["key"]]: full["details"].get(str(e["id"])) for e in full.get("events") or []
            if isinstance(e, dict) and e.get("k") == "labitem" and isinstance(e.get("key"), str)}
    n = 0
    for li in keyless:
        f, d = made.get(li["key_hash"]), li["detail"]
        if not isinstance(f, dict):
            continue
        keys = _match_keys(d["rows"], f.get("rows") or [], f.get("keys") or [])
        if li["import_id"] not in connected and not (all(keys) and f.get("collected") == d.get("collected")):
            continue
        if not any(keys):
            continue
        li["detail"] = {**d, "keys": keys}
        db.execute("UPDATE events SET detail=? WHERE id=?", (records._enc(li["detail"]), li["id"]))
        for x in db.execute("SELECT id, previous FROM sync_changes WHERE event_id=?", (li["id"],)).fetchall():
            prev = records._dec(x["previous"])
            pd = prev.get("detail")
            if isinstance(pd, dict) and isinstance(pd.get("rows"), list) and "keys" not in pd:
                db.execute("UPDATE sync_changes SET previous=? WHERE id=?",
                           (records._enc({**prev, "detail": {**pd, "keys": _match_keys(pd["rows"], f.get("rows") or [], f.get("keys") or [])}}), x["id"]))
        n += 1
    if n:
        records._bump(db, c["profile_id"])
    return n


def _match_keys(rows, made, keys):
    """Each of rows' own record's key: that of the row in made (the whole set's, keys its records) the same as it, else
    the one with its name, value and unit, each taken once; None where none is."""
    out, used = [None] * len(rows), set()
    for same in (lambda a, b: a == b, lambda a, b: a[:3] == b[:3]):
        for i, row in enumerate(rows):
            j = next((j for j, m in enumerate(made[:len(keys)]) if out[i] is None and j not in used
                      and isinstance(m, list) and isinstance(row, list) and same(m, row)), None)
            if j is not None:
                out[i] = keys[j]
                used.add(j)
    return out


# ---------------------------------------------------------------- undo (history.undo, in its transaction)

def unapply(db, iid):
    """Put back what import iid changed in place: a sync's, or an upload's results sent again by their source (#869).
    A later import that changed the same record again keeps its version, and what it says was there before becomes
    what this one found (its results' too, each the earliest); in a lab event each row is its own record (#911,
    _rows_back), so what the later one didn't change goes back now. Call before the import's rows go. An import that
    removed a kind's records (remove_kind) puts them back. A lab day a pull joined (#899) lists none of its labitems
    once they go, and one of iid's own that a pull joined is handed to that pull, holding its labitems: they keep a
    lab day (_hand_over). A lab day put back lists only the lab events there are (#908: what it was may list those of
    an import undone since, the upload whose lab day it was), titled for what it holds; one left holding nothing goes."""
    _put_back(db, iid)
    _hand_over(db, iid)
    changed = [r[0] for r in db.execute("SELECT DISTINCT event_id FROM sync_changes WHERE import_id=? AND event_id IS NOT NULL", (iid,))]
    _unapply(db, iid)
    _unapply_out(db, iid)
    gone = {r[0] for r in db.execute("SELECT id FROM events WHERE import_id=?", (iid,))}
    changed += [x["id"] for x in _listing(db, iid, gone) if x["id"] not in changed]   # (#910) any lab day listing them
    for ch in records._chunks(changed):   # (a later pull's version kept, or what it was put back: iid's labitems go from it)
        for r in db.execute(f"SELECT id, body, detail FROM events WHERE import_id != ? AND id IN ({','.join('?' * len(ch))})", (iid, *ch)).fetchall():
            d = records._dec(r["detail"])
            if not (isinstance(d, dict) and d.get("type") == "labday" and isinstance(d.get("ids"), list)):
                continue
            there = {x[0] for x in db.execute(f"SELECT id FROM events WHERE id IN ({','.join('?' * len(d['ids']))})", d["ids"])} if d["ids"] else set()
            ids = [i for i in d["ids"] if i in there and i not in gone]
            if ids == d["ids"]:
                continue
            if ids:
                db.execute("UPDATE events SET body=?, detail=? WHERE id=?",
                           (records._enc(_labday_body(db, records._dec(r["body"]), ids)), records._enc({**d, "ids": ids}), r["id"]))
            else:
                db.execute("DELETE FROM events WHERE id=?", (r["id"],))


def _listing(db, iid, ids):
    """(#910) The FHIR lab days of other imports than iid that list one of these events (iid's): those of their days."""
    r = db.execute("SELECT profile_id FROM imports WHERE id=?", (iid,)).fetchone()
    if not r or not ids:
        return []
    days = set()
    for ch in records._chunks(sorted(ids)):
        days |= {str((records._dec(x["body"]) or {}).get("t") or "")[:10]
                 for x in db.execute(f"SELECT body FROM events WHERE id IN ({','.join('?' * len(ch))})", ch)}
    return [x for x in _lab_days_listing(db, r["profile_id"], days) if x["import_id"] != iid and set(x["detail"].get("ids") or []) & ids]


def _hand_over(db, iid):
    """(#899) Undoing import iid: a lab day of its own that lists another import's labitems (a later pull joined it, a
    first pull's joined it, #910, or Put back made it again for the lab events it brought back, #914) goes to the
    latest import whose labitems it lists, holding only those, so they keep their lab day when iid's records go."""
    for r in db.execute("SELECT id, body, detail FROM events WHERE import_id=? AND detail IS NOT NULL", (iid,)).fetchall():
        d = records._dec(r["detail"])
        if not (isinstance(d, dict) and d.get("type") == "labday" and d.get("ids")):
            continue
        q = f"SELECT id, import_id FROM events WHERE import_id != ? AND id IN ({','.join('?' * len(d['ids']))})"
        theirs = db.execute(q, (iid, *d["ids"])).fetchall()
        if theirs:
            ids = [i for i in d["ids"] if i in {x["id"] for x in theirs}]
            db.execute("UPDATE events SET import_id=?, body=?, detail=? WHERE id=?",
                       (max(x["import_id"] for x in theirs), records._enc(_labday_body(db, records._dec(r["body"]), ids)),
                        records._enc({**d, "ids": ids}), r["id"]))


def _unapply(db, iid):
    """What import iid changed in place (sync_changes) put back, as unapply says."""
    for r in db.execute("SELECT * FROM sync_changes WHERE import_id=? ORDER BY id DESC", (iid,)).fetchall():
        prev = records._dec(r["previous"])
        if r["event_id"] is not None:
            later = db.execute("SELECT id, previous FROM sync_changes WHERE event_id=? AND id > ? ORDER BY id LIMIT 1",
                               (r["event_id"], r["id"])).fetchone()
            if later and _rows_back(db, r, prev):   # (#911) a labitem: row by row, each record's own
                continue
            if later:
                theirs = records._dec(later["previous"])
                results = {**dict(theirs.get("results") or []), **dict(prev.get("results") or [])}   # (#869) this one's first
                db.execute("UPDATE sync_changes SET previous=? WHERE id=?",
                           (records._enc({**prev, **({"results": [[k, v] for k, v in results.items()]} if results else {})}), later["id"]))
            else:
                db.execute("UPDATE events SET body=?, detail=? WHERE id=?",
                           (records._enc(prev["body"]), None if prev["detail"] is None else records._enc(prev["detail"]), r["event_id"]))
                if "cost" in prev:   # kept since #92; older entries didn't change the Costs row
                    db.execute("UPDATE events SET cost=? WHERE id=?",
                               (None if prev["cost"] is None else records._enc(prev["cost"]), r["event_id"]))
                for rid, body in prev.get("results") or []:   # results it updated in place (#869)
                    db.execute("UPDATE results SET body=? WHERE id=?", (records._enc(body), rid))
            continue
        later = None
        for x in db.execute("SELECT id, previous FROM sync_changes WHERE meta_import=? AND id > ? ORDER BY id",
                            (r["meta_import"], r["id"])).fetchall():
            p = records._dec(x["previous"])
            if p["path"] == prev["path"] and p["key"] == prev["key"]:
                later = x
                break
        if later:
            db.execute("UPDATE sync_changes SET previous=? WHERE id=?", (r["previous"], later["id"]))
            continue
        m = db.execute("SELECT meta FROM imports WHERE id=?", (r["meta_import"],)).fetchone()
        meta = records._dec(m["meta"]) or {}
        items = _meta_list(meta, prev["path"])
        for i, x in enumerate(items or []):
            if isinstance(x, dict) and x.get("key") == prev["key"]:
                items[i] = prev["entry"]
        db.execute("UPDATE imports SET meta=? WHERE id=?", (records._enc(meta), r["meta_import"]))
    db.execute("DELETE FROM sync_changes WHERE import_id=?", (iid,))


def _unapply_out(db, iid):
    """(#914) What import iid changed in place of a lab event taken out by a removal (remove_kind keeps what its pulls
    kept of it, "_changes") put back in the copy kept, as _unapply does where it's here: Put back then brings it back as
    it was before iid, its results too. A later change of it keeps its version (the event as a whole)."""
    r = db.execute("SELECT profile_id FROM imports WHERE id=?", (iid,)).fetchone()
    if not r:
        return
    held = db.execute("""SELECT x.id, x.what, x.row FROM sync_removed x JOIN imports i ON i.id = x.import_id
                         WHERE i.profile_id=? ORDER BY x.id""", (r["profile_id"],)).fetchall()
    rows = {x["id"]: records._dec(x["row"]) for x in held}
    series = {x["id"]: rows[x["id"]] for x in held if x["what"] == "series"}
    for x in held:
        row = rows[x["id"]]
        changes = row.get("_changes") if x["what"] == "event" else None
        if not changes or not any(imp == iid for imp, _ in changes):
            continue
        for k in [k for k, (imp, _) in enumerate(changes) if imp == iid][::-1]:
            prev = records._dec(changes[k][1])
            if k + 1 < len(changes):   # a later one keeps its version: what it found becomes what this one found
                changes[k + 1] = [changes[k + 1][0], changes[k][1]]
            else:
                row["body"] = records._enc(prev["body"])
                row["detail"] = None if prev.get("detail") is None else records._enc(prev["detail"])
                for rid, body in prev.get("results") or []:
                    db.execute("UPDATE results SET body=? WHERE id=?", (records._enc(body), rid))
                    for sid, s in series.items():
                        if rid in (s.get("rids") or []):
                            s["results"][s["rids"].index(rid)] = records._enc(body)
                            db.execute("UPDATE sync_removed SET row=? WHERE id=?", (records._enc(s), sid))
            del changes[k]
        db.execute("UPDATE sync_removed SET row=? WHERE id=?", (records._enc({**row, "_changes": changes}), x["id"]))


def _row_ids(d):
    """Each row of a labitem's details d as the record it is (_row_keys), else by its place: [identity]."""
    return [own or ("#", j) for j, own in enumerate(_row_keys(d))]


def _by_row(d):
    """{row identity: row} of a labitem's details d."""
    rows = (d or {}).get("rows") if isinstance(d, dict) else None
    return dict(zip(_row_ids(d), rows)) if isinstance(rows, list) else {}


def _rows_back(db, r, prev):
    """(#911) Undoing one import's change of a labitem (r: its sync_changes row, prev: what it was before) that a later
    import changed again, record by record: a row the later one didn't change gets back what it was now, its result too
    (prev's "results", #869); one it changed keeps its version, and what it says was there before (theirs, and every
    later one's that saw this one's) becomes what this one found. #869's rule applied to the labitem whole: a pull's
    Hemoglobin back from Low to Normal undone under a later pull that changed the report's Platelets kept Normal, while
    the history said it "gets back the range and flag it had". A report's own details go the same way. -> whether it was
    a labitem (else the event goes as a whole, as before)"""
    old = prev.get("detail")
    e = db.execute("SELECT id, import_id, body, detail FROM events WHERE id=?", (r["event_id"],)).fetchone()
    now = records._dec(e["detail"]) if e else None
    if not (isinstance(old, dict) and isinstance(old.get("rows"), list) and isinstance(now, dict) and isinstance(now.get("rows"), list)):
        return False
    laters = [(x["id"], records._dec(x["previous"])) for x in
              db.execute("SELECT id, previous FROM sync_changes WHERE event_id=? AND id > ? ORDER BY id", (r["event_id"], r["id"])).fetchall()]
    seen = laters[0][1].get("detail") if isinstance(laters[0][1].get("detail"), dict) else {}   # what this one left
    was, left = _by_row(old), _by_row(seen)
    mine = {k for k in left if k not in was or not _same(left[k], was[k])}   # the rows this one changed or added
    nonrow = lambda d: {k: v for k, v in (d or {}).items() if k not in ROW_FIELDS}   # noqa: E731

    def back(d):
        """d with what this one did taken out: each of its rows as it was where d's is what this one left it, one it
        added gone; its report's details too."""
        ids, rows, out = _row_ids(d), d.get("rows") or [], {"rows": [], "keys": []}
        keys = d.get("keys") if isinstance(d.get("keys"), list) else [None] * len(rows)
        for j, k in enumerate(ids):
            if k in mine and k not in was:
                continue   # one it added: gone with it
            row = was[k] if k in mine and _same(rows[j], left.get(k)) else rows[j]
            out["rows"].append(row)
            out["keys"].append(keys[j] if j < len(keys) else None)
        base = nonrow(old) if _same(nonrow(d), nonrow(seen)) else nonrow(d)
        return {**base, **{k: v for k, v in d.items() if k in ROW_FIELDS and k not in ("rows", "keys")}, "rows": out["rows"],
                **({"keys": out["keys"]} if isinstance(d.get("keys"), list) else {})}
    cur = back(now)
    if not _same(cur, now):
        db.execute("UPDATE events SET body=?, detail=? WHERE id=?",
                   (records._enc(_with_abn(records._dec(e["body"]) or {}, cur["rows"])), records._enc(cur), e["id"]))
    # results this one updated in place (#869): back now, unless a later one changed it again, whose "before" it becomes
    mine_res, theirs = dict(prev.get("results") or []), {}
    for xid, p in laters:
        for rid, body in p.get("results") or []:
            theirs.setdefault(rid, xid)
    for rid, body in mine_res.items():
        if rid not in theirs:
            db.execute("UPDATE results SET body=? WHERE id=?", (records._enc(body), rid))
    for xid, p in laters:   # what each later one says was there before: as this one found it, where it saw this one's
        d = p.get("detail")
        if not isinstance(d, dict) or not isinstance(d.get("rows"), list):
            continue
        res = [[rid, mine_res[rid] if theirs.get(rid) == xid and rid in mine_res else body] for rid, body in p.get("results") or []]
        d = back(d)
        q = {**p, "detail": d, "body": _with_abn(p.get("body") or {}, d["rows"]), **({"results": res} if res else {})}
        if not _same(q, p):
            db.execute("UPDATE sync_changes SET previous=? WHERE id=?", (records._enc(q), xid))
    return True


def _put_back(db, iid):
    """Undoing a removal (remove_kind): its rows go back where they were, with their ids where those are free (#914:
    what its pulls kept of each for Undo too, theirs that are still here), its results with theirs. A lab event goes
    back in each lab day it was taken out of (_lab_days_out's "_in"), or, that lab day undone meanwhile (the upload
    whose it was), in one made again as it was, of the latest import whose lab events it lists; each lab day it
    touches lists only lab events there are, titled for them. What's here again since (#922: the kind turned on again
    and pulled, or the same records uploaded) isn't put back beside it (_back_since); the connection's own lab day of a
    draw put back joins the one it has made since (_one_day)."""
    back, days = {}, set()
    removed = db.execute("SELECT * FROM sync_removed WHERE import_id=? ORDER BY id", (iid,)).fetchall()
    pid = db.execute("SELECT profile_id FROM imports WHERE id=?", (iid,)).fetchone()[0]
    since = _back_since(db, pid, [(x["what"], records._dec(x["row"])) for x in removed])
    for x in removed:
        row = records._dec(x["row"])
        if x["what"] == "event":
            row = since.event(row)
            if row is None:
                continue
            cols = ["profile_id", "import_id", "body", "detail", "cost", "cost_seq", "key_hash"]
            free = not db.execute("SELECT 1 FROM events WHERE id=?", (row["id"],)).fetchone()
            names = (["id"] if free else []) + cols
            eid = db.execute(f"INSERT INTO events({', '.join(names)}) VALUES({', '.join('?' * len(names))})",
                             [row[k] for k in names]).lastrowid
            for imp, prev in row.get("_changes") or []:
                if db.execute("SELECT 1 FROM imports WHERE id=?", (imp,)).fetchone():
                    db.execute("INSERT INTO sync_changes(import_id, event_id, previous) VALUES(?, ?, ?)", (imp, eid, prev))
            for snap in row.get("_in") or []:
                back.setdefault(snap["id"], (snap, []))[1].append(eid)
            d = records._dec(row["detail"]) if row.get("detail") else None
            if isinstance(d, dict) and d.get("type") == "labday":
                days.add(eid)
        elif x["what"] == "series":
            row = since.series(row)
            if row is None:
                continue
            free = not db.execute("SELECT 1 FROM series WHERE id=?", (row["id"],)).fetchone()
            names = (["id"] if free else []) + ["profile_id", "import_id", "body", "key_hash"]
            sid = db.execute(f"INSERT INTO series({', '.join(names)}) VALUES({', '.join('?' * len(names))})",
                             [row[k] for k in names]).lastrowid
            rids = row.get("rids") or [None] * len(row["results"])
            for b, rid in zip(row["results"], rids):
                if rid is not None and not db.execute("SELECT 1 FROM results WHERE id=?", (rid,)).fetchone():
                    db.execute("INSERT INTO results(id, profile_id, import_id, series_id, body) VALUES(?, ?, ?, ?, ?)",
                               (rid, row["profile_id"], row["import_id"], sid, b))
                else:
                    db.execute("INSERT INTO results(profile_id, import_id, series_id, body) VALUES(?, ?, ?, ?)",
                               (row["profile_id"], row["import_id"], sid, b))
        else:
            row = since.meta(row)
            if row is None:
                continue
            m = db.execute("SELECT meta FROM imports WHERE id=?", (x["home_import"],)).fetchone()
            meta = records._dec(m["meta"]) or {}
            top, _, sub = row["path"].partition(".")
            holder = meta.setdefault(top, {}) if sub else meta
            have = holder.get(sub or top)
            holder[sub or top] = row["items"] + (have if isinstance(have, list) else [])
            db.execute("UPDATE imports SET meta=? WHERE id=?", (records._enc(meta), x["home_import"]))
    db.execute("DELETE FROM sync_removed WHERE import_id=?", (iid,))
    listed = {}   # (#914) each lab day the lab events were taken out of: they go back in it, or in one made again
    for ld_id, (snap, eids) in back.items():
        r = db.execute("SELECT body, detail FROM events WHERE id=?", (ld_id,)).fetchone()
        was = snap["detail"].get("ids") or []
        if r:
            now = (records._dec(r["detail"]) or {}).get("ids") or []
            want = set(now) | set(eids)
            listed[ld_id] = ([i for i in was if i in want] + [i for i in now if i not in was] + [i for i in eids if i not in was and i not in now]), None
            continue
        imps = {x[0]: x[1] for ch in records._chunks(sorted(eids)) for x in db.execute(
            f"SELECT id, import_id FROM events WHERE id IN ({','.join('?' * len(ch))})", ch)}
        imp = snap["imp"] if db.execute("SELECT 1 FROM imports WHERE id=?", (snap["imp"],)).fetchone() else max(imps.values())
        db.execute("INSERT INTO events(id, profile_id, import_id, body, detail, key_hash) SELECT ?, profile_id, ?, ?, ?, ? FROM imports WHERE id=?",
                   (ld_id, imp, records._enc(snap["body"]), records._enc(snap["detail"]), snap["key_hash"], imp))
        listed[ld_id] = (was, snap["body"])
        days.add(ld_id)
    for ld_id in days - set(listed):
        r = db.execute("SELECT body, detail FROM events WHERE id=?", (ld_id,)).fetchone()
        listed[ld_id] = ((records._dec(r["detail"]) or {}).get("ids") or [], None)
    for ld_id, (ids, body) in listed.items():
        r = db.execute("SELECT body, detail FROM events WHERE id=?", (ld_id,)).fetchone()
        ids = [i for i in ids if i in _there(db, ids) and i != ld_id]
        d = records._dec(r["detail"]) or {}
        if not ids:
            db.execute("DELETE FROM events WHERE id=?", (ld_id,))
            continue
        db.execute("UPDATE events SET body=?, detail=? WHERE id=?",
                   (records._enc(_labday_body(db, body or records._dec(r["body"]) or {}, ids)), records._enc({**d, "ids": ids}), ld_id))
    _one_day(db, pid, iid, days)


class _Since:
    """(#922) A removal's rows less what's here again since it was made (_back_since)."""

    def __init__(self, events, rows, results, items):
        self.events, self.rows, self.results, self.items = events, rows, results, items

    def event(self, row):
        """The event to put back (a labitem without the rows here again, its flags counted again), or None."""
        if row["id"] in self.events:
            return None
        keep = self.rows.get(row["id"])
        if keep is None:
            return row
        d = records._dec(row["detail"])
        d = {**d, **{f: [x for j, x in enumerate(d[f]) if j in keep] for f in ROW_FIELDS if isinstance(d.get(f), list)}}
        body = _with_abn(records._dec(row["body"]) or {}, d.get("rows") or [])
        return {**row, "body": records._enc(body), "detail": records._enc(d)}

    def series(self, row):
        """The series to put back without the results here again, or None when that leaves none."""
        gone = self.results.get(row["id"])
        if not gone:
            return row
        body = records._dec(row["body"]) or {}
        txt = [p for n, p in enumerate(body.get("txt") or []) if ("txt", n) not in gone]
        keep = [n for n in range(len(row["results"])) if ("pts", n) not in gone]
        if not keep and not txt:
            return None
        rids = row.get("rids") or [None] * len(row["results"])
        return {**row, "body": records._enc({**body, **({"txt": txt} if "txt" in body else {})}),
                "results": [row["results"][n] for n in keep], "rids": [rids[n] for n in keep]}

    def meta(self, row):
        items = [x for x in row["items"] if not (isinstance(x, dict) and x.get("key") in self.items.get(row["path"], ()))]
        return {**row, "items": items} if items else None


def _back_since(db, pid, removed):
    """(#922) What of a removal (removed: [(what, row)] as sync_removed keeps them) is here again since it was made: the
    kind turned on again and pulled (from the beginning, a kind's search having its own cursor), or the same records
    uploaded. An event whose source key names its record (preview._record_key) and is here; a labitem's rows whose
    records are in a labitem here, with their results (crossmatch._row_of, as the drawer pairs them); a list's
    entries (the problem list, upcoming visits) whose keys are here. Put back leaves those out: the ones here are the
    source's newer word, and the removed ones beside them would be the same record twice. -> _Since"""
    events, rows, results, items = set(), {}, {}, {}
    out = [r for what, r in removed if what == "event"]
    for r in out:
        b = records._dec(r["body"]) or {}
        if b.get("k") not in ("lab", "labitem") and r.get("key_hash") and preview._record_key(b.get("key")) and \
                db.execute("SELECT 1 FROM events WHERE profile_id=? AND key_hash=? LIMIT 1", (pid, r["key_hash"])).fetchone():
            events.add(r["id"])
    items_out = {r["id"]: (records._dec(r["body"]) or {}, records._dec(r["detail"])) for r in out
                 if (records._dec(r["body"]) or {}).get("k") == "labitem"}
    if items_out:
        here = {k for it in _items_here(db, pid, [r["key_hash"] for r in out if r["id"] in items_out]).values()
                for k in _row_keys(it["detail"]) if k}
        dropped = set()
        for eid, (b, d) in items_out.items():
            rk = _row_keys(d)
            gone = {j for j, k in enumerate(rk) if k and k in here}
            if not gone:
                continue
            dropped |= {(eid, j) for j in gone}
            if len(gone) == len(rk):
                events.add(eid)
            else:
                rows[eid] = {j for j in range(len(rk)) if j not in gone}
        if dropped:
            imps = {r["id"]: r["import_id"] for r in out}
            drawer = crossmatch._rows({"events": [{**b, "id": eid, "imp": imps[eid]} for eid, (b, _) in items_out.items()],
                                       "details": {str(eid): d for eid, (_, d) in items_out.items()}})
            for what, r in removed:
                if what != "series":
                    continue
                sr = {**(records._dec(r["body"]) or {}), "imp": r["import_id"]}
                pts = [("pts", n, records._dec(b)) for n, b in enumerate(r["results"])] + [("txt", n, p) for n, p in enumerate(sr.get("txt") or [])]
                gone = {(f, n) for f, n, p in pts if isinstance(p, list) and p and crossmatch._row_of(drawer, sr, p) in dropped}
                if gone:
                    results[r["id"]] = gone
    paths = {r["path"] for what, r in removed if what == "meta"}
    if paths:
        for (m,) in db.execute("SELECT meta FROM imports WHERE profile_id=?", (pid,)).fetchall():
            meta = records._dec(m) or {}
            for path in paths:
                got = _meta_list(meta, path)
                items.setdefault(path, set()).update(x.get("key") for x in got or [] if isinstance(x, dict) and x.get("key"))
    return _Since(events, rows, results, items)


def _one_day(db, pid, iid, days):
    """(#922) A lab day of the connection's own put back (days: those put back whole, or made again) where the
    connection has made another for that draw since (the kind turned on again and pulled): its lab events join that
    one, as a pull's join the draw's lab day (_draw_day), and it goes; one lab day a draw."""
    ours = {r[0] for r in db.execute("SELECT id FROM imports WHERE connection_id=(SELECT connection_id FROM imports WHERE id=?)", (iid,))}
    for ld_id in sorted(days):
        r = db.execute("SELECT import_id, key_hash, detail FROM events WHERE id=?", (ld_id,)).fetchone()
        if not r or r["import_id"] not in ours:
            continue
        other = [x for x in _labday_rows(db, pid, r["key_hash"]) if x["id"] != ld_id and x["import_id"] in ours]
        if not other:
            continue
        to = other[-1]
        ids = [i for i in to["detail"].get("ids") or []]
        ids += [i for i in (records._dec(r["detail"]) or {}).get("ids") or [] if i not in ids]
        ids = [i for i in ids if i in _there(db, ids)]
        db.execute("DELETE FROM events WHERE id=?", (ld_id,))
        db.execute("UPDATE events SET body=?, detail=? WHERE id=?",
                   (records._enc(_labday_body(db, to["body"], ids)), records._enc({**to["detail"], "ids": ids}), to["id"]))


# ---------------------------------------------------------------- taking out what a kind brought (#99)

KIND_EVENTS = {"labs": {"lab", "labitem"}, "visits": {"visit", "stay"}, "medications": {"med"},
               "documents": {"document", "study", "imaging"}, "claims": {"claim"}, "immunizations": {"immunization"}}
KIND_META = {"visits": ("upcoming",), "problems": ("problems",), "vitals": ("clinicWeights",),
             "appointments": ("upcoming",), "allergies": ("problems",)}   # the last two: #228


def _from(entry, rtype):
    """Whether a record's source key names a resource of this FHIR type ("fhir:<type>/<id>", or with its server)."""
    key = str(entry.get("key") or "") if isinstance(entry, dict) else ""
    return key.startswith(f"fhir:{rtype}/") or (key.startswith("fhir:") and f"|{rtype}/" in key)


def _of_kind(kind, body):
    if body.get("k") in KIND_EVENTS.get(kind, ()):
        return True
    if body.get("k") == "rx":   # #228: a pharmacy fill is a claim's (ExplanationOfBenefit) or a MedicationDispense's
        return kind == ("claims" if _from(body, "ExplanationOfBenefit") else "fills")
    return kind == "claims" and _from(body, "ExplanationOfBenefit")


def _in_list(kind, path, entry):
    """Whether an entry of one of the profile-wide lists belongs to this kind (#228): upcoming visits are planned
    Encounters (visits) or Appointments; the problem list holds problems and allergies (type "Allergy")."""
    if path == "upcoming":
        return (kind == "appointments") == _from(entry, "Appointment")
    if path == "problems":
        return (kind == "allergies") == (isinstance(entry, dict) and entry.get("type") == "Allergy")
    return True


def _lab_days_out(db, pid, events):
    """(#914) Taking out a connection's lab events (events: their rows, its lab days and labitems), the lab days that
    list them and stay: another import's (the uploaded export's that its pulls joined, #899), or one of its own that
    lists another's lab events too (one it was handed, #899, or its first pull's own beside an upload's): each lists
    them no more, titled for what it still holds, and the lab events taken out keep it as it was, for Put back (_in).
    One left listing nothing goes with them, to come back whole. A removed one listed nowhere else had kept their ids
    while they were out, and an Undo meanwhile (#908's filter) dropped them for good. -> (the rows to take out, {lab day
    id: what it is now: its id, body and detail}, {lab event id: [each lab day it was taken out of, as it was]}, how
    many of the connection's own lab days stay)"""
    removing = {r["id"]: r for r in events}
    days = {str((records._dec(r["body"]) or {}).get("t") or "")[:10] for r in events}
    lds = _lab_days_listing(db, pid, days)
    there = _there(db, [i for x in lds for i in x["detail"].get("ids") or []])
    stays, outside, staying = {}, collections.defaultdict(list), 0
    for ld in lds:
        ids = [i for i in ld["detail"].get("ids") or [] if i in there]
        rest = [i for i in ids if i not in removing]
        snap = {"id": ld["id"], "imp": ld["import_id"], "key_hash": ld["key_hash"], "body": ld["body"], "detail": ld["detail"]}
        if ld["id"] in removing and rest:   # one of its own listing another's lab events too: it stays
            del removing[ld["id"]]
            staying += 1
        gone = [i for i in ids if i in removing]
        for i in gone:   # (one taken out whole comes back whole; its import undone meanwhile, its lab events make it again)
            outside[i].append(snap)
        if not gone or ld["id"] in removing:
            continue
        if not rest:   # another's left holding nothing: out too, to come back whole
            removing[ld["id"]] = db.execute("SELECT * FROM events WHERE id=?", (ld["id"],)).fetchone()
            continue
        stays[ld["id"]] = {"id": ld["id"], "body": _labday_body(db, ld["body"], rest), "detail": {**ld["detail"], "ids": rest}}
    return [r for r in events if r["id"] in removing] + [r for i, r in removing.items() if i not in {e["id"] for e in events}], stays, outside, staying


def remove_kind(db, who, pid, cid, kind, ip=None):
    """Take out the records of one kind that this connection brought, as one entry in the import history: undoing
    it there puts them back (_put_back). Only a kind turned off (#922): one still pulled would come back with the next
    sync, and Put back then bring it twice. Records of that kind from elsewhere (uploads, other sources) stay. Its lab
    events leave the lab days that stay (_lab_days_out, #914), and what its pulls kept of each for Undo goes and comes
    back with it, so Remove, Put back and Undo in any order leave each result its row in one lab day.
    Returns {"import", "counts"}."""
    c = conns.get(db, pid, cid)
    if kind not in conns.KINDS:
        raise conns.Problem("There's no kind of record called that.", 400)
    label = conns.KINDS[kind][0]
    if c["state"] != "disconnected" and kind in conns.chosen_kinds(c):   # (#922) the next pull would bring it back
        raise conns.Problem(f"Turn {label.lower()} off for {c['source']} first: while they're pulled, the next sync "
                            "brings back what's removed.", 409)
    with dbm.transaction(db):
        homes = [r[0] for r in db.execute("SELECT id FROM imports WHERE connection_id=? AND profile_id=? AND note IS NULL "
                                          "ORDER BY id", (cid, pid))]
        events, series, lists, kinds = [], [], [], {}
        for h in homes:
            for r in db.execute("SELECT * FROM events WHERE import_id=? ORDER BY id", (h,)).fetchall():
                b = records._dec(r["body"])
                if _of_kind(kind, b):
                    events.append(r)
                    kinds[b.get("k")] = kinds.get(b.get("k"), 0) + 1
            if kind in ("labs", "vitals"):
                for r in db.execute("SELECT * FROM series WHERE import_id=? ORDER BY id", (h,)).fetchall():
                    if ("vitals" if (records._dec(r["body"]) or {}).get("panel") == "Vitals" else "labs") == kind:
                        series.append(r)
            for path in KIND_META.get(kind, ()):
                meta = records._dec(db.execute("SELECT meta FROM imports WHERE id=?", (h,)).fetchone()["meta"]) or {}
                items = _meta_list(meta, path)
                taken = [x for x in items if _in_list(kind, path, x)] if isinstance(items, list) else []
                if taken:
                    lists.append((h, path, taken))
                    items[:] = [x for x in items if not _in_list(kind, path, x)]
                    db.execute("UPDATE imports SET meta=? WHERE id=?", (records._enc(meta), h))
        events, kept, outside, staying = _lab_days_out(db, pid, events) if kind == "labs" else (events, {}, {}, 0)
        if staying:   # (a lab day of its own listing another's lab events stays, holding those)
            kinds["lab"] -= staying
            kinds = {k: v for k, v in kinds.items() if v}
        results = sum(db.execute("SELECT COUNT(*) FROM results WHERE series_id=?", (r["id"],)).fetchone()[0] for r in series)
        counts = {"kinds": kinds, "series": len(series), "results": results,
                  **({"entries": sum(len(x[2]) for x in lists)} if lists else {})}
        if not (events or series or lists):
            raise conns.Problem(f"There's nothing to remove: {c['source']} hasn't brought any {label.lower()} that are "
                                "still here.", 409)
        rid = records.store(db, pid, {"events": []}, "sync", fmt="fhir")
        note = {"removed": kind, "label": f"{label} from {c['source']}, removed", "counts": counts}
        db.execute("UPDATE imports SET connection_id=?, note=? WHERE id=?", (cid, records._enc(note), rid))
        for r in events:
            # (#914) what its pulls kept of it for Undo (sync_changes, which go with its row), and the lab days it was
            # taken out of, for Put back to give back
            changes = [[x["import_id"], x["previous"]] for x in db.execute("SELECT import_id, previous FROM sync_changes WHERE event_id=? ORDER BY id", (r["id"],))]
            db.execute("INSERT INTO sync_removed(import_id, home_import, what, row) VALUES(?, ?, 'event', ?)",
                       (rid, r["import_id"], records._enc({**dict(r), **({"_changes": changes} if changes else {}),
                                                            **({"_in": outside[r["id"]]} if r["id"] in outside else {})})))
            db.execute("DELETE FROM events WHERE id=?", (r["id"],))
        for x in kept.values() if kind == "labs" else ():
            db.execute("UPDATE events SET body=?, detail=? WHERE id=?", (records._enc(x["body"]), records._enc(x["detail"]), x["id"]))
        for r in series:
            got = db.execute("SELECT id, body FROM results WHERE series_id=? ORDER BY id", (r["id"],)).fetchall()
            db.execute("INSERT INTO sync_removed(import_id, home_import, what, row) VALUES(?, ?, 'series', ?)",
                       (rid, r["import_id"], records._enc({**dict(r), "results": [x["body"] for x in got], "rids": [x["id"] for x in got]})))
            db.execute("DELETE FROM series WHERE id=?", (r["id"],))   # its results by cascade
        for h, path, items in lists:
            db.execute("INSERT INTO sync_removed(import_id, home_import, what, row) VALUES(?, ?, 'meta', ?)",
                       (rid, h, records._enc({"path": path, "items": items})))
        activity.log(db, "connect", who, pid, {"source": c["source"], "change": "remove", "kind": kind, "label": label,
                                               "counts": counts, "import": rid}, ip)
        records._bump(db, pid)
    return {"import": rid, "counts": counts}


# ---------------------------------------------------------------- fetching a left-out attachment (#101)

def fetch_anyway(db, who, pid, cid, sid, ip=None):
    """Fetch one attachment a pull left out, whatever the connection's rules say (SYNC_MAX_ATTACHMENT_MB still
    holds), and link it to its record, as an import of its own: undoing it from the history takes the file away and
    lists it again. Returns {"import", "size"}."""
    c = conns.get(db, pid, cid)
    if c["state"] != "active":
        raise conns.Problem("This connection needs signing in again first." if c["state"] == "needs reconnect"
                            else "This source is disconnected.", 409)
    row = db.execute("SELECT * FROM sync_skipped WHERE id=? AND connection_id=?", (sid, cid)).fetchone()
    if not row:
        raise conns.Problem("No such attachment.", 404)
    if row["import_id"] is not None:
        raise conns.Problem("This attachment was fetched already.", 409)
    d = conns._jdec(row["detail"])
    h = records.key_hashes(db, [d["record"]])[d["record"]]
    targets = db.execute("SELECT id, detail FROM events WHERE profile_id=? AND key_hash=?", (pid, h)).fetchall()
    if not targets:
        raise conns.Problem("The record this attachment belongs to isn't in the profile: save the first pull's preview "
                            "first, or it was undone or removed.", 409)
    http = smart.Client(outside.for_connection(db, cid, "Connected source: sync", who, ip))   # the one way out (#97)
    try:
        reader = smart.Reader(http, c["fhir_base"], conns.Tokens(db, cid, http))
        body = reader.get(c["fhir_base"] + "/" + d["rel"], accept="*/*", max_bytes=config.setting("SYNC_MAX_ATTACHMENT_MB") * config.MB).body
    except (smart.AccessEnded, smart.TokenRejected) as e:
        conns.needs_reconnect(db, cid, str(e) + " Sign in again to keep this source up to date.")
        raise conns.Problem(str(e) + " Sign in again, then fetch it.", 409) from None
    except smart.TooBig:
        raise conns.Problem(f"It's bigger than {config.setting('SYNC_MAX_ATTACHMENT_MB')} MB, the most this server "
                            "fetches (SYNC_MAX_ATTACHMENT_MB, set by whoever runs it).", 409) from None
    except smart.SmartError as e:
        raise conns.Problem("The vendor didn't send it: " + str(e), 502) from None
    uid = who["id"] if who is not None else None
    up = uploads.create(db, pid, uid or c["created_by"])
    try:
        uploads.add_file(db, pid, up, d["rel"], io.BytesIO(body))
        uploads.finish(db, pid, up, unzip=False)
        sealed = {rel: p for uid, rel, p in uploads.stored_files(db, pid) if uid == up}

        def stored(url):
            p = sealed.get(url) if isinstance(url, str) else None
            return os.path.realpath(p) if p and os.path.isfile(p) else None

        with dbm.transaction(db):
            iid = records.store(db, pid, {"events": []}, "sync", fmt="fhir")
            note = {"fetched": d.get("title"), "label": f"Attachment from {c['source']}, fetched", "counts": {"documents": 1}}
            db.execute("UPDATE imports SET connection_id=?, note=? WHERE id=?", (cid, records._enc(note), iid))
            linked = 0
            for e in targets:
                old = records._dec(e["detail"])
                if not (isinstance(old, dict) and old.get("type") == "file"):
                    continue
                one = {"x": {**old, "url": d["rel"]}}
                keep, n = prof.index_documents(db, pid, one, stored)
                prof.link_documents(db, iid, keep)
                if not n:
                    continue
                db.execute("INSERT INTO sync_changes(import_id, event_id, previous) VALUES(?, ?, ?)",
                           (iid, e["id"], records._enc({"body": records._dec(db.execute("SELECT body FROM events WHERE id=?",
                                                                                         (e["id"],)).fetchone()["body"]),
                                                         "detail": old})))
                db.execute("UPDATE events SET detail=? WHERE id=?", (records._enc(one["x"]), e["id"]))
                linked += 1
            if not linked:
                raise conns.Problem("The record this attachment belongs to has no place for a file.", 409)
            db.execute("""INSERT INTO jobs(profile_id, upload_id, created_by, state, importer, warnings, summary, import_id,
                          created, started, finished) VALUES(?, ?, ?, 'done', 'fhir', ?, ?, ?, ?, ?, ?)""",
                       (pid, up, uid, crypto.enc("[]"),
                        crypto.enc(json.dumps({"counts": {"documents": 1}, "connection": c["source"]})), iid,
                        dbm.now(), dbm.now(), dbm.now()))
            db.execute("UPDATE sync_skipped SET import_id=? WHERE id=?", (iid, sid))
            db.execute("UPDATE connections SET attach_bytes=attach_bytes+? WHERE id=?", (len(body), cid))
            activity.log(db, "connect", who, pid, {"source": c["source"], "change": "fetch", "title": d.get("title"),
                                                   "size": len(body), "import": iid}, ip)
            records._bump(db, pid)
    except BaseException:
        with contextlib.suppress(uploads.UploadError):
            uploads.discard(db, pid, up)
        raise
    return {"import": iid, "size": len(body)}
