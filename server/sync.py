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
as always. Turning a kind off stops it being pulled; remove_kind() takes out what it brought, as an import in the
history that undoing puts back. Kinds that share a place (#228) are told apart by the resource a record's source key
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
      the sync puts it back
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
import contextlib, hashlib, io, json, logging, os, shutil, tempfile, threading, urllib.parse

from . import activity, config, connections as conns, crypto, db as dbm, importers as registry, jobs, labmap, outside
from . import preview, profiles as prof, records, smart, uploads

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
    wait = min(config.SYNC_HOURS * 3600, BACKOFF_FIRST * 2 ** max(0, failures - 1))
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
    left, resources, report, warnings = config.SYNC_MAX_RESOURCES, [], {}, []
    searches, kinds = searches or ([(t, {}, t, None) for t in conns.TYPES[1:]], set(conns.KINDS))
    rules = rules or {"auto": True, "types": ["pdf", "image", "text", "html", "other"], "budget_mb": 1 << 30,
                      "max_mb": config.SYNC_MAX_ATTACHMENT // config.MB}
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
                       (json.dumps(got["cursor"]), dbm.now(), dbm.now() + config.SYNC_HOURS * 3600,
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
    c = db.execute("SELECT id, state FROM connections WHERE first_job=? AND cursor IS NULL", (jid,)).fetchone()
    if not c:
        return
    if c["state"] == "disconnected":
        raise Disconnected(DISCONNECTED)
    if iid is not None:
        db.execute("UPDATE imports SET connection_id=?, synced=? WHERE id=?", (c["id"], dbm.now(), iid))
    db.execute("""UPDATE connections SET cursor=IFNULL(pending_cursor, '{}'), pending_cursor=NULL,
                  next_sync=CASE WHEN state='active' THEN ? ELSE NULL END WHERE id=?""",
               (dbm.now() + config.SYNC_HOURS * 3600, c["id"]))


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
    out = {}
    for h in records._chunks(sorted({x for x in hashes if x})):
        q = f"SELECT id, key_hash FROM events WHERE profile_id=? AND key_hash IN ({','.join('?' * len(h))}) ORDER BY id"
        for r in db.execute(q, (pid, *h)):
            out.setdefault(r["key_hash"], []).append(r["id"])
    return out


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
            tmp = tempfile.mkdtemp(prefix="sanguity-sync-")
            try:
                os.chmod(tmp, 0o700)
                path = os.path.join(tmp, "connection.json")
                with open(path, "wb") as f:
                    f.write(_bundle(c["fhir_base"], store["resources"].values()))
                full = _with_ext(_parse({**{k: v for k, v in files.items() if k != "pull.json"}, "connection.json": path}))
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
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
        dups = preview.find(db, pid, "fhir", delta)
        keyless_new = [e for e in delta["events"] if isinstance(e, dict) and not isinstance(e.get("key"), str)
                       and str(e.get("id")) not in dups["events"]]
        dups["events"] = {str(e.get("id")) for e in delta["events"] if isinstance(e, dict)}   # events: decided below
        hashes = records.key_hashes(db, touched)
        have = _rows_by_hash(db, pid, hashes.values())
        # a result already here is kept as a copy only with its own row (#868): one whose row is a record the profile
        # has, updated in place below in its own import, would be a result of this pull's import with no lab day of
        # its own (util.js labDraws put it in every lab day of its date); left out, a duplicate
        keys = {str(e.get("id")): e.get("key") for e in delta["events"] if isinstance(e, dict)}   # (each touched)
        for i, ns in dups["at"].items():
            for n, (eid, _) in list(ns.items()):
                if hashes.get(keys.get(eid)) in have:
                    preview.leave_out(dups, i, n)
        rest = preview.trim(delta, dups)
        # events come from the whole set read so far (a lab day groups results across pulls), ids as it numbers them;
        # one only this pull's records have (keyless, or not in the whole set) keeps its own, marked apart
        new_events, updates, src = [], [], {}
        for k in sorted(touched):
            e, details = full_by_key.get(k), full.get("details", {})
            if e is None:
                e, details = delta_by_key.get(k), delta.get("details", {})
                e = e and {**e, "id": f"d{e.get('id')}", "_from": details.get(str(e.get("id")))}
            if e is None:
                continue
            if hashes.get(k) in have:
                updates.append(e)
            else:
                new_events.append(e)
            src[str(e["id"])] = e.pop("_from", None) if "_from" in e else details.get(str(e["id"]))
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
        if not (preview.adds_anything(data) or updates or meta_changes):
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
        changed = _update_in_place(db, pid, iid, updates, {**full.get("details", {}), **src}, stored, costs)
        changed += _apply_meta(db, iid, meta_changes)
        _link_labdays(db, pid, iid, full, set(full_by_key) & (touched | {e.get("key") for e in new_events}))
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
               (json.dumps(got["cursor"]), dbm.now(), dbm.now() + config.SYNC_HOURS * 3600, conns._jenc(report), c["id"]))
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


def _update_in_place(db, pid, iid, updates, details, stored, costs=None):
    """Change the events whose source key the profile has, where the vendor's version differs; keep what each was.
    A claim's Costs row changes with it (an adjusted claim's new amounts)."""
    n = 0
    for e in updates:
        h = records.key_hashes(db, [e["key"]])[e["key"]]
        body = {k: v for k, v in e.items() if k != "id"}
        new_d = details.get(str(e.get("id")))
        new_c = (costs or {}).get(str(e.get("id")))
        for r in db.execute("SELECT id, body, detail, cost FROM events WHERE profile_id=? AND key_hash=?", (pid, h)).fetchall():
            old_b, old_d = records._dec(r["body"]), records._dec(r["detail"])
            old_c = records._dec(r["cost"]) if r["cost"] is not None else None
            cost = {**new_c, "id": r["id"]} if new_c is not None else old_c   # a row the vendor's version lacks stays
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


def _link_labdays(db, pid, iid, full, keys):
    """Point each lab day this pull touched at its results' rows: all of that day's, across pulls and imports."""
    events = {str(e.get("id")): e for e in full["events"] if isinstance(e, dict)}
    for e in full["events"]:
        if not isinstance(e, dict) or e.get("key") not in keys:
            continue
        d = full["details"].get(str(e.get("id")))
        if not (isinstance(d, dict) and d.get("type") == "labday"):
            continue
        members = [events[str(i)]["key"] for i in d.get("ids") or [] if str(i) in events and events[str(i)].get("key")]
        mh = records.key_hashes(db, members)
        rows = _rows_by_hash(db, pid, mh.values())
        ids = [rows[mh[k]][-1] for k in members if mh.get(k) in rows]
        h = records.key_hashes(db, [e["key"]])[e["key"]]
        for r in db.execute("SELECT id, import_id, body, detail FROM events WHERE profile_id=? AND key_hash=?", (pid, h)).fetchall():
            old = records._dec(r["detail"]) or {}
            if old.get("ids") == ids:
                continue
            if r["import_id"] != iid and not db.execute("SELECT 1 FROM sync_changes WHERE import_id=? AND event_id=?",
                                                        (iid, r["id"])).fetchone():
                db.execute("INSERT INTO sync_changes(import_id, event_id, previous) VALUES(?, ?, ?)",
                           (iid, r["id"], records._enc({"body": records._dec(r["body"]), "detail": records._dec(r["detail"])})))
            db.execute("UPDATE events SET detail=? WHERE id=?", (records._enc({**old, "type": "labday", "ids": ids}), r["id"]))


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


# ---------------------------------------------------------------- undo (history.undo, in its transaction)

def unapply(db, iid):
    """Put back what sync import iid changed in place. A later sync that changed the same record again keeps its
    version, and what it says was there before becomes what this one found. Call before the import's rows go.
    An import that removed a kind's records (remove_kind) puts them back."""
    _put_back(db, iid)
    for r in db.execute("SELECT * FROM sync_changes WHERE import_id=? ORDER BY id DESC", (iid,)).fetchall():
        prev = records._dec(r["previous"])
        if r["event_id"] is not None:
            later = db.execute("SELECT id FROM sync_changes WHERE event_id=? AND id > ? ORDER BY id LIMIT 1",
                               (r["event_id"], r["id"])).fetchone()
            if later:
                db.execute("UPDATE sync_changes SET previous=? WHERE id=?", (r["previous"], later["id"]))
            else:
                db.execute("UPDATE events SET body=?, detail=? WHERE id=?",
                           (records._enc(prev["body"]), None if prev["detail"] is None else records._enc(prev["detail"]), r["event_id"]))
                if "cost" in prev:   # kept since #92; older entries didn't change the Costs row
                    db.execute("UPDATE events SET cost=? WHERE id=?",
                               (None if prev["cost"] is None else records._enc(prev["cost"]), r["event_id"]))
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


def _put_back(db, iid):
    """Undoing a removal (remove_kind): its rows go back where they were, with their ids where those are free."""
    for x in db.execute("SELECT * FROM sync_removed WHERE import_id=? ORDER BY id", (iid,)).fetchall():
        row = records._dec(x["row"])
        if x["what"] == "event":
            cols = ["profile_id", "import_id", "body", "detail", "cost", "cost_seq", "key_hash"]
            free = not db.execute("SELECT 1 FROM events WHERE id=?", (row["id"],)).fetchone()
            names = (["id"] if free else []) + cols
            db.execute(f"INSERT INTO events({', '.join(names)}) VALUES({', '.join('?' * len(names))})",
                       [row[k] for k in names])
        elif x["what"] == "series":
            free = not db.execute("SELECT 1 FROM series WHERE id=?", (row["id"],)).fetchone()
            names = (["id"] if free else []) + ["profile_id", "import_id", "body", "key_hash"]
            sid = db.execute(f"INSERT INTO series({', '.join(names)}) VALUES({', '.join('?' * len(names))})",
                             [row[k] for k in names]).lastrowid
            db.executemany("INSERT INTO results(profile_id, import_id, series_id, body) VALUES(?, ?, ?, ?)",
                           [(row["profile_id"], row["import_id"], sid, b) for b in row["results"]])
        else:
            m = db.execute("SELECT meta FROM imports WHERE id=?", (x["home_import"],)).fetchone()
            meta = records._dec(m["meta"]) or {}
            top, _, sub = row["path"].partition(".")
            holder = meta.setdefault(top, {}) if sub else meta
            have = holder.get(sub or top)
            holder[sub or top] = row["items"] + (have if isinstance(have, list) else [])
            db.execute("UPDATE imports SET meta=? WHERE id=?", (records._enc(meta), x["home_import"]))
    db.execute("DELETE FROM sync_removed WHERE import_id=?", (iid,))


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


def remove_kind(db, who, pid, cid, kind, ip=None):
    """Take out the records of one kind that this connection brought, as one entry in the import history: undoing
    it there puts them back (_put_back). Records of that kind from elsewhere (uploads, other sources) stay.
    Returns {"import", "counts"}."""
    c = conns.get(db, pid, cid)
    if kind not in conns.KINDS:
        raise conns.Problem("There's no kind of record called that.", 400)
    label = conns.KINDS[kind][0]
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
            db.execute("INSERT INTO sync_removed(import_id, home_import, what, row) VALUES(?, ?, 'event', ?)",
                       (rid, r["import_id"], records._enc(dict(r))))
            db.execute("DELETE FROM events WHERE id=?", (r["id"],))
        for r in series:
            pts = [x[0] for x in db.execute("SELECT body FROM results WHERE series_id=? ORDER BY id", (r["id"],))]
            db.execute("INSERT INTO sync_removed(import_id, home_import, what, row) VALUES(?, ?, 'series', ?)",
                       (rid, r["import_id"], records._enc({**dict(r), "results": pts})))
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
        body = reader.get(c["fhir_base"] + "/" + d["rel"], accept="*/*", max_bytes=config.SYNC_MAX_ATTACHMENT).body
    except (smart.AccessEnded, smart.TokenRejected) as e:
        conns.needs_reconnect(db, cid, str(e) + " Sign in again to keep this source up to date.")
        raise conns.Problem(str(e) + " Sign in again, then fetch it.", 409) from None
    except smart.TooBig:
        raise conns.Problem(f"It's bigger than {config.SYNC_MAX_ATTACHMENT // config.MB} MB, the most this server "
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
