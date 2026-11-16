"""Scheduled sync of connected sources (#91): pulls what's new from each connection (connections.py) and keeps the
profile's records up to date in place, as imports that can be undone.

A thread ("sanguity-sync", beside jobs.py's worker) wakes every TICK seconds, or at once when woken (a sign-in, Sync
now), and pulls each connection that is due, one at a time. A pull's network reads happen outside any transaction;
what it saves goes in one transaction at the end, so it never holds SQLite's single write lock while waiting on a
vendor, and a failure saves nothing.

A pull reads, with an access token from connections.Tokens (renewed as needed; a rotated refresh token is saved at
once): the Patient, then a search per type (connections.TYPES) that the granted scopes cover, for the patient, page
by page, with _lastUpdated=gt<the last pull's Bundle.meta.lastUpdated> once there was one, and the Binary
attachments that new or changed DocumentReferences and DiagnosticReports point at (up to SYNC_MAX_ATTACHMENT each).
One pull reads at most SYNC_MAX_RESOURCES; the rest come next time. A type refused anyway (403) is reported as such,
not skipped quietly: the connection's report says, type by type, what was pulled. An insurer's connection (#92)
reads only Patient, ExplanationOfBenefit and Coverage, by its scopes.

What a pull read is kept as an upload (pull.json, a Bundle, and Binary/<id>), sealed like any upload, so its files
are the import's original files. The connection also keeps every resource it has read so far, sealed in
profiles/<id>/connections/<connection id>, newest version of each: a lab day groups results across pulls, so it
is rebuilt from all of them.

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
again; anything else is shown as it is and retried with the same backoff.

Each successful pull, new records or not, marks every import of the connection as up to date then
(imports.synced, #100): the page shows it as "last synced" beside each record's source. The first pull's import gets
it when its preview is saved.
"""
import contextlib, io, json, logging, os, shutil, tempfile, threading, urllib.parse

from . import activity, config, connections as conns, crypto, db as dbm, importers as registry, jobs, labmap, outside
from . import preview, profiles as prof, records, smart, uploads

log = logging.getLogger("sanguity.sync")

TICK = 30                    # seconds between looks for connections that are due
PAGE = 100                   # _count asked for per page
BACKOFF_FIRST = 300          # a failed pull is tried again after this, doubling up to SYNC_HOURS
REF_TYPES = {"DiagnosticReport": "result"}   # a report groups its results: changed results touch the report too
PATIENT_PARAM = {"Coverage": "beneficiary"}   # the search parameter naming the patient, where it isn't `patient`

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
        got = _fetch(reader, crypto.dec(c["patient"]), None if first else json.loads(c["cursor"]), c["scopes"])
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
    """The Binary attachments of a DocumentReference or DiagnosticReport on this server: [(relative path, url)]."""
    atts = [(c or {}).get("attachment") for c in r.get("content") or []] if r["resourceType"] == "DocumentReference" \
        else list(r.get("presentedForm") or [])
    out = []
    for a in atts:
        url = (a or {}).get("url") if isinstance(a, dict) else None
        if not isinstance(url, str) or a.get("data"):
            continue
        rel = url[len(base) + 1:] if url.startswith(base + "/") else url
        parts = rel.split("/")
        if len(parts) == 2 and parts[0] == "Binary" and parts[1] and "?" not in rel and "#" not in rel:
            out.append((rel, base + "/" + rel))
    return out


def _fetch(reader, patient, cursor, scopes=None):
    """Read what's new: {"resources", "binaries" {rel: bytes}, "cursor", "report", "warnings"}. Types the granted
    scopes don't cover aren't asked for (an insurer connection reads only Patient, claims and coverage)."""
    left, resources, report, warnings = config.SYNC_MAX_RESOURCES, [], {}, []
    new_cursor = dict(cursor or {})
    try:
        p = reader.get("Patient/" + urllib.parse.quote(patient, safe="")).json()
        if isinstance(p, dict) and p.get("resourceType") == "Patient":
            resources.append(p)
        report["Patient"] = {"n": 1, "status": "ok"}
    except smart.Forbidden:
        report["Patient"] = {"n": 0, "status": "not allowed"}
    except smart.Refused as e:
        report["Patient"] = {"n": 0, "status": "failed", "why": str(e)}
    for t in conns.TYPES[1:]:
        if not conns.granted(scopes, t):
            continue
        if left <= len(resources):
            report[t] = {"n": 0, "status": "next time"}
            continue
        params = {PATIENT_PARAM.get(t, "patient"): patient, "_count": PAGE}
        if cursor and cursor.get(t):
            params["_lastUpdated"] = cursor[t]
        try:
            got, stamp, cut = reader.search(t, params, left - len(resources))
        except smart.Forbidden:
            report[t] = {"n": 0, "status": "not allowed"}
            continue
        except smart.Refused as e:   # this type only; the vendor being down or access ending stops the pull
            report[t] = {"n": 0, "status": "failed", "why": str(e)}
            continue
        resources += got
        newest = _newest(got)
        if cut:   # the rest next time: from the newest read (ge, since others may share its time)
            if newest:
                new_cursor[t] = "ge" + newest
            report[t] = {"n": len(got), "status": "more next time"}
        else:
            if stamp:
                new_cursor[t] = "gt" + stamp
            elif newest:
                new_cursor[t] = "ge" + newest
            report[t] = {"n": len(got), "status": "ok"}
    binaries, too_big, missing = {}, 0, 0
    for r in resources:
        if r.get("resourceType") not in conns.BINARY_FOR:
            continue
        for rel, url in _attachment_urls(r, reader.base):
            if rel in binaries:
                continue
            try:
                binaries[rel] = reader.get(url, accept="*/*", max_bytes=config.SYNC_MAX_ATTACHMENT).body
            except smart.TooBig:
                too_big += 1
            except smart.Refused:
                missing += 1
    if too_big:
        warnings.append(f"Left out {too_big} attachment{'s' if too_big != 1 else ''} bigger than "
                        f"{config.SYNC_MAX_ATTACHMENT // 2**20} MB (SYNC_MAX_ATTACHMENT_MB).")
    if missing:
        warnings.append(f"{missing} attachment{'s' if missing != 1 else ''} couldn't be read from the vendor.")
    return {"resources": resources, "binaries": binaries, "cursor": new_cursor, "report": report, "warnings": warnings}


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
    _save_store(pid, cid, _merge_store(c, got))
    if len(got["resources"]) <= 1:   # the patient, nothing else: no import to preview; later pulls bring what comes
        db.execute("""UPDATE connections SET cursor=?, pending_cursor=NULL, last_sync=?, next_sync=?, failures=0,
                      error_kind=NULL, last_error=NULL, report=? WHERE id=? AND state='active'""",
                   (json.dumps(got["cursor"]), dbm.now(), dbm.now() + config.SYNC_HOURS * 3600,
                    conns._jenc(_report(got)), cid))
        return {"ok": True, "first": True, "job": None}
    up = _upload(db, c, got)
    if db.execute("SELECT state FROM connections WHERE id=?", (cid,)).fetchone()["state"] != "active":
        uploads.discard(db, pid, up)
        return {"ok": False, "error": "This source was disconnected during the pull.", "kind": "other"}
    # committed before the worker can see it (submit queues the job at once)
    jid = jobs.submit(db, pid, up, (who or {}).get("id") or c["created_by"], "fhir", ip)
    db.execute("""UPDATE connections SET first_job=?, pending_cursor=?, last_sync=?, next_sync=NULL, failures=0,
                  error_kind=NULL, last_error=NULL, report=? WHERE id=?""",
               (jid, json.dumps(got["cursor"]), dbm.now(), conns._jenc(_report(got)), cid))
    return {"ok": True, "first": True, "job": jid}


def first_saved(db, jid, iid):
    """jobs._save, in its transaction: the first pull of a connection was saved. Its cursor becomes the
    connection's, and its import belongs to the connection."""
    c = db.execute("SELECT id FROM connections WHERE first_job=? AND cursor IS NULL", (jid,)).fetchone()
    if not c:
        return
    if iid is not None:
        db.execute("UPDATE imports SET connection_id=?, synced=? WHERE id=?", (c["id"], dbm.now(), iid))
    db.execute("""UPDATE connections SET cursor=IFNULL(pending_cursor, '{}'), pending_cursor=NULL,
                  next_sync=CASE WHEN state='active' THEN ? ELSE NULL END WHERE id=?""",
               (dbm.now() + config.SYNC_HOURS * 3600, c["id"]))


# ---------------------------------------------------------------- a later pull: new records added, changed ones updated in place

def _parse(files):
    try:
        return registry.get("fhir").parse(files)["data"]
    except ValueError:   # nothing in it Sanguity shows (only claims or patients, say)
        return None


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
        _save_store(pid, cid, store)
        with dbm.transaction(db):
            _done(db, c, got, report)
        return {"ok": True, "import": None, "counts": None, "changed": 0}
    full = full or delta
    touched = _touched(delta, got["resources"], store)
    full_by_key = {e["key"]: e for e in full["events"] if isinstance(e, dict) and isinstance(e.get("key"), str)}
    delta_by_key = {e["key"]: e for e in delta["events"] if isinstance(e, dict) and isinstance(e.get("key"), str)}

    # labs and the profile-wide lists come from what this pull read; lab names matched as remembered
    lab_plan = labmap.plan(db, pid, "fhir", delta)
    delta = labmap.apply(delta, lab_plan["auto"])
    dups = preview.find(db, pid, "fhir", delta)
    keyless_new = [e for e in delta["events"] if isinstance(e, dict) and not isinstance(e.get("key"), str)
                   and str(e.get("id")) not in dups["events"]]
    dups["events"] = {str(e.get("id")) for e in delta["events"] if isinstance(e, dict)}   # events: decided below
    rest = preview.trim(delta, dups)

    hashes = records.key_hashes(db, touched)
    have = _rows_by_hash(db, pid, hashes.values())
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
        _save_store(pid, cid, store)
        with dbm.transaction(db):
            _done(db, c, got, report)
        return {"ok": True, "import": None, "counts": None, "changed": 0}

    summary = jobs.summarise(data)
    _save_store(pid, cid, store)   # before the commit: if that fails, the next pull reads these again (harmless)
    with dbm.transaction(db):
        if db.execute("SELECT state FROM connections WHERE id=?", (cid,)).fetchone()["state"] != "active":
            raise jobs.Failed("This source was disconnected during the pull.")
        keepdocs, n_docs = prof.index_documents(db, pid, data["details"], stored)
        iid = records.store(db, pid, data, "sync", fmt="fhir")
        prof.link_documents(db, iid, keepdocs)
        db.execute("UPDATE imports SET connection_id=? WHERE id=?", (cid, iid))
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
                  report=? WHERE id=?""",
               (json.dumps(got["cursor"]), dbm.now(), dbm.now() + config.SYNC_HOURS * 3600, conns._jenc(report), c["id"]))


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
    version, and what it says was there before becomes what this one found. Call before the import's rows go."""
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
