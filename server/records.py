"""A profile's records as rows: imports, events, series and results (schema version 10, see db.py).

The page reads one object per profile (GET /api/profiles/{pid}/data), in the shape build.py has always written
to data.json, and each part of it has its place:

  events     [{id, t, k, title, sub, end, abn, hidden...}]   one row each in events (body)
  details    {event id: {...}}                               events.detail; the page's data has only some (page(),
                                                             #217): the rest come when a record is opened (details())
  costs      [{id: event id, d, billed, paid, owe...}]       events.cost, the claim's own row
  labs       [{name, unit, panel, loinc, n, txt, pts}]       series, with each point of pts a row in results
  the rest   built, source, upcoming, problems, outOfPocket, milestones, clinicWeights...   imports.meta
             (milestones there are no longer what the page shows: they are a setting since #39, milestones.py)
  edits      [{id, action, found, ids...}]                   the edits table: corrections made in the app, applied
                                                             after the rows (edits.py), never part of an import
  same       [{id, same, found, sides...}]                   the same_results table: results from two sources the
  possible   [{ref, a, b}]                                   person said are the same (shown once) or not, applied
                                                             last; and the possible matches still undecided
                                                             (crossmatch.py, #96). Only when there are any
  imports    {import id: {label, origin, format, at, synced, name?}}  the imports rows: where each record came from
                                                             (#100); name: what the person calls it (#38).
                                                             Each event and lab series names its import as "imp"; a
                                                             series' results are all of its import. Sent once here,
                                                             so no row repeats its importer's label
  oop        {payments, claims}                              out-of-pocket costs worked out (oop.py, #55): where
                                                             each payment came from, HSA/FSA, the claim it paid,
                                                             and what is counted; only with payments or marks
  journal    [{id, kind, t, title, severity, note, med, by...}]  the journal table: notes, symptoms and side
                                                             effects people wrote (journal.py), never part of an
                                                             import; left out while there are none
  medications [{name, key, rxnorm, spans, changes, fills...}]  each medication's listings as spans, its dose changes
                                                             and its pharmacy fills (medications.py, #50), worked out
                                                             from the events above; left out while there are none
  reminders  [{id, med, name, times, days, from, until, tz, on, by...}]  the med_reminders table: medication reminders
                                                             for the calendar feed (med_reminders.py, #162), never part
                                                             of an import; left out while there are none
  doses      [{id, med, name, t, status, note, slot, by...}]  the med_doses table: the medication log, doses people
                                                             logged as taken, taken late or skipped (med_log.py, #235),
                                                             never part of an import; left out while there are none

store() takes such an object apart into the rows of one import; assemble() puts a profile's imports back
together. An event's id is its row's, so the ids an import arrives with are mapped on the way in, the ones a lab
day lists (details[...].ids) included. With several imports, lists in their meta are joined and single values
come from the newest.

Everything a row holds is encrypted (crypto.enc); only ids and the links between rows are plain. The one exception
is key_hash (schema version 13) on events and series: a keyed hash of the source key the importer gave (the key
itself stays inside the encrypted body), so an import preview finds records it has seen before without decrypting
every row. '' means the record has no key; NULL, not worked out yet (backfill_key_hashes, at start-up).
"""
import collections, hashlib, hmac, json, re, threading
from contextlib import contextmanager

from . import crossmatch, crypto, edits, encounters, journal, medications, oop, units
from .importers import common

FORMAT = "sanguity-data"            # imports.format of a data.json (build.py, the demo, an export zip)
ROWS = ("events", "details", "labs", "costs")   # the parts kept as rows; the rest of the object is the meta
SERVED = ("imports", "edits", "same", "possible", "journal", "medications", "oop", "reminders", "doses",
          "corrections")   # parts assemble() adds: never stored from an import (an export's data.json has them, and
                           # its corrections, #589: those go back into their own tables, export.restore)


def _enc(obj):
    return crypto.enc(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))


def _dec(text):
    return None if text is None else json.loads(crypto.dec(text))


def _bump(db, pid):
    db.execute("UPDATE profiles SET records_rev=records_rev+1 WHERE id=?", (pid,))


def store(db, pid, data, origin, fmt=FORMAT, now=None, by=None):
    """Add data (the data.json shape, already checked to have an events list) to profile pid as one new import.
    by: the account that typed it in (imports.created_by, #325). Call inside a transaction. Returns the import's id."""
    from . import db as dbm
    assert db.in_transaction, "records.store must run inside a transaction"
    details = {str(k): v for k, v in (data.get("details") or {}).items()}
    events = [e for e in data.get("events") or [] if isinstance(e, dict)]
    meta = {k: v for k, v in data.items() if k not in ROWS and k not in SERVED}

    # the new ids, decided up front (the transaction holds the write lock), so a lab day can list them: after the
    # highest there has been (AUTOINCREMENT's count, schema version 39), so an undone import's never come again (#588)
    base = db.execute("""SELECT MAX(IFNULL((SELECT MAX(id) FROM events), 0),
                                IFNULL((SELECT seq FROM sqlite_sequence WHERE name='events'), 0))""").fetchone()[0]
    ids = {}
    for n, e in enumerate(events, start=base + 1):
        ids.setdefault(str(e.get("id")), n)   # a repeated id: its details and cost go to the first
    costs, loose = {}, []
    for seq, c in enumerate(data.get("costs") or []):
        key = str(c.get("id")) if isinstance(c, dict) else None
        if key in ids and key not in costs:
            costs[key] = (seq, c)
        else:   # a cost with no event of its own still shows on the Costs tab
            loose.append([seq, {**c, "id": None} if isinstance(c, dict) else c])
    if loose:
        meta["costs"] = loose

    iid = db.execute("INSERT INTO imports(profile_id, at, origin, format, meta, created_by) VALUES(?, ?, ?, ?, ?, ?)",
                     (pid, now or dbm.now(), origin, fmt, _enc(meta), by)).lastrowid
    rows, secret = [], _key_secret(db)
    for n, e in enumerate(events, start=base + 1):
        key = str(e.get("id"))
        first = ids[key] == n
        d = details.get(key) if first else None
        if isinstance(d, dict) and isinstance(d.get("ids"), list):
            d = {**d, "ids": [ids[str(i)] for i in d["ids"] if str(i) in ids]}
        seq, c = costs.get(key, (None, None)) if first else (None, None)
        rows.append((n, pid, iid, _enc({k: v for k, v in e.items() if k not in ("id", "imp")}),
                     None if d is None else _enc(d), None if c is None else _enc({**c, "id": n}), seq, _hash(secret, e.get("key"))))
    db.executemany("INSERT INTO events(id, profile_id, import_id, body, detail, cost, cost_seq, key_hash) VALUES(?, ?, ?, ?, ?, ?, ?, ?)", rows)

    for s in data.get("labs") or []:
        if not isinstance(s, dict):
            continue
        sid = db.execute("INSERT INTO series(profile_id, import_id, body, key_hash) VALUES(?, ?, ?, ?)",
                         (pid, iid, _enc({k: v for k, v in s.items() if k not in ("pts", "imp", "same")}), _hash(secret, s.get("key")))).lastrowid
        db.executemany("INSERT INTO results(profile_id, import_id, series_id, body) VALUES(?, ?, ?, ?)",
                       [(pid, iid, sid, _enc(p)) for p in s.get("pts") or []])
    _bump(db, pid)
    return iid


# ---------------------------------------------------------------- source keys, found without decrypting (#22, #89)

def _key_secret(db):
    """The HMAC key for key_hash: derived from the pepper (atrest.load_pepper), which never changes, so the hashes
    stay valid across a rotation of the data key."""
    from . import atrest, security as sec
    if sec._pepper is None:
        atrest.load_pepper(db)
    return hashlib.sha256(b"sanguity source keys\x00" + sec._pepper).digest()


def _hash(secret, key):
    if not isinstance(key, str) or not key.strip():
        return ""
    return hmac.new(secret, key.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def key_hashes(db, keys):
    """{source key: its key_hash} for the keys given (others skipped)."""
    secret = _key_secret(db)
    return {k: _hash(secret, k) for k in keys if isinstance(k, str) and k.strip()}


def backfill_key_hashes(db):
    """Work out key_hash for rows written before schema version 13. Safe to repeat. Returns how many rows."""
    from . import db as dbm
    secret, n = _key_secret(db), 0
    for table in ("events", "series"):
        rows = db.execute(f"SELECT id, body FROM {table} WHERE key_hash IS NULL").fetchall()
        if rows:
            with dbm.transaction(db):
                db.executemany(f"UPDATE {table} SET key_hash=? WHERE id=?",
                               [(_hash(secret, (_dec(r["body"]) or {}).get("key")), r["id"]) for r in rows])
            n += len(rows)
    return n


STAMPED_KEYS = "fhir_stamped_keys"   # meta, set by schema step 44: rekey_stamped hasn't run yet
_STAMPED = re.compile(r"fhir:([^|]*#[^|]*)\|([A-Za-z]+/.+)$", re.S)   # fhir:<a meta.source stamped per write>|<type>/<id>...
_NAMED = re.compile(r"fhir:(?!lab(?:item|day):)([^|#?:]+:[^|#?]+)\|[A-Za-z]+/[^|]+$")   # fhir:<a server>|<type>/<id>: an upload mixing servers


def rekey_stamped(db):
    """Once, at start-up after schema step 44 (#1032): records saved before #1022 from a server that stamps meta.source
    per write (HAPI FHIR's "#<request id>", "<base>#<request id>") were keyed by the stamp, "fhir:#<request>|<type>/<id>"
    (their "ver" lost), so each was added again as a copy when it changed at its source. Each is keyed as the FHIR
    importer keys it now: by its stable source (the part before "#", else the server of the connection whose pull saved
    it), plainly ("fhir:<type>/<id>") where that's its import's one server, "fhir:<source>|<type>/<id>" where its import
    mixes servers; its "ver" says that source where it says none. Events (their key_hash, and a lab event's rows'
    "keys"), the profile-wide lists' entries, and the corrections and Costs marks that find them by key, what a lab
    event's correction holds among them. -> how many records were keyed again

    Record by record (#1044): one it can't read or key (an event, a list's entry, an import's lists, a correction or
    Costs mark) is left as it is, named in the app's log and the install log, and the rest are keyed; the mark is
    cleared only once none was left, so each start-up tries those again, and the app starts whatever they hold."""
    from . import activity, db as dbm, preview
    if not dbm.meta_get(db, STAMPED_KEYS):
        return 0
    secret, n, left = _key_secret(db), 0, []
    with dbm.transaction(db):
        moved = collections.defaultdict(dict)   # profile id -> {old key: new key}
        for imp in db.execute("""SELECT i.id, i.profile_id, i.meta, c.fhir_base FROM imports i
                                 LEFT JOIN connections c ON c.id = i.connection_id ORDER BY i.id""").fetchall():
            evs = []
            for r in db.execute("SELECT id, body, detail FROM events WHERE import_id=?", (imp["id"],)).fetchall():
                try:
                    b, d = _dec(r["body"]) or {}, _dec(r["detail"])
                    if not isinstance(b, dict):
                        raise ValueError("not a record")
                    evs.append((r["id"], b, d))
                except Exception:
                    left.append(f"event {r['id']}")
            try:
                meta = _dec(imp["meta"]) or {}
                lists = preview._meta_lists(meta)
                keys = [b.get("key") for _, b, _ in evs] + [k for _, _, d in evs if isinstance(d, dict) for k in d.get("keys") or []] + \
                    [x.get("key") for items in lists.values() for x in items if isinstance(x, dict)]
                keys = [k for k in keys if isinstance(k, str)]
            except Exception:
                left.append(f"import {imp['id']}")
                continue

            def source(k):
                m = _STAMPED.match(k)
                return (common.stable_source(m.group(1)) or imp["fhir_base"] or "").strip() if m else None
            try:
                if not any(source(k) is not None for k in keys):
                    continue
                servers = {preview._server(source(k)) for k in keys if _STAMPED.match(k)} | \
                    {preview._server(m.group(1)) for k in keys if (m := _NAMED.match(k))}
            except Exception:
                left.append(f"import {imp['id']}")
                continue
            mixed, got = len(servers - {""}) > 1, {}   # got: this record's keys moved, kept once it's saved

            def new(k):
                if not (isinstance(k, str) and (m := _STAMPED.match(k))):
                    return k
                s = source(k)
                to = f"fhir:{s}|{m.group(2)}" if mixed and s else f"fhir:{m.group(2)}"
                got.setdefault(k, to)
                return to

            def keep():
                for k, to in got.items():
                    moved[imp["profile_id"]].setdefault(k, to)
                got.clear()

            def said(x, k):   # its "ver" says its source, where it says none
                s = source(k) if isinstance(k, str) else None
                v = x.get("ver") if isinstance(x.get("ver"), dict) else {}
                if s and not common.stable_source(v.get("server")):
                    x["ver"] = {**v, "server": s}
            for eid, b, d in evs:
                got.clear()
                try:
                    first = next((k for k in [b.get("key"), *(d.get("keys") or [] if isinstance(d, dict) else [])]
                                  if isinstance(k, str) and _STAMPED.match(k)), None)
                    if first is None:
                        continue
                    if b.get("k") != "lab":
                        said(b, first)
                    b["key"] = new(b.get("key"))
                    if isinstance(d, dict) and isinstance(d.get("keys"), list):
                        d["keys"] = [new(k) for k in d["keys"]]
                    db.execute("UPDATE events SET body=?, detail=?, key_hash=? WHERE id=?",
                               (_enc(b), None if d is None else _enc(d), _hash(secret, b.get("key")), eid))
                except Exception:
                    left.append(f"event {eid}")
                    continue
                keep()
                n += 1
            changed = False
            for items in lists.values():
                for x in items:
                    if isinstance(x, dict) and isinstance(x.get("key"), str) and _STAMPED.match(x["key"]):
                        got.clear()
                        try:
                            y = dict(x)
                            said(y, y["key"])
                            y["key"] = new(y["key"])
                        except Exception:
                            left.append(f"an entry of import {imp['id']}'s lists")
                            continue
                        x.update(y)
                        keep()
                        changed, n = True, n + 1
            if changed:
                db.execute("UPDATE imports SET meta=? WHERE id=?", (_enc(meta), imp["id"]))
        for pid, to in moved.items():
            for r in db.execute("SELECT id, target_key, other_key, value FROM edits WHERE profile_id=?", (pid,)).fetchall():
                try:
                    tk, ok = crypto.dec(r["target_key"]), crypto.dec(r["other_key"]) if r["other_key"] else None
                    v = _dec(r["value"]) if r["value"] is not None else None
                    held = {f: [to.get(h, h) for h in v[f]] for f in ("holds", "other_holds")
                            if isinstance(v, dict) and isinstance(v.get(f), list) and any(h in to for h in v[f])}
                    if tk in to or ok in to or held:
                        tk, ok = to.get(tk, tk), to.get(ok, ok) if ok else None
                        db.execute("UPDATE edits SET target=?, target_key=?, other=?, other_key=?, value=? WHERE id=?",
                                   (edits.target_hash(tk), crypto.enc(tk), None if ok is None else edits.target_hash(ok),
                                    None if ok is None else crypto.enc(ok), _enc({**v, **held}) if held else r["value"], r["id"]))
                except Exception:
                    left.append(f"correction {r['id']}")
            for r in db.execute("SELECT id, target_key FROM cost_marks WHERE profile_id=?", (pid,)).fetchall():
                try:
                    if (tk := crypto.dec(r["target_key"])) in to:
                        db.execute("UPDATE OR IGNORE cost_marks SET target=?, target_key=? WHERE id=?",
                                   (oop.target_hash(to[tk]), crypto.enc(to[tk]), r["id"]))
                except Exception:
                    left.append(f"Costs mark {r['id']}")
            _bump(db, pid)
        if left:   # tried again at the next start-up
            print(f"Couldn't key {len(left)} record(s) saved before #1022 as they are now, so they were left as they were "
                  f"(tried again at the next start-up): {', '.join(left)}.", flush=True)
            activity.log(db, "records.rekey", detail={"left": left}, actor=activity.START_UP)
        else:
            dbm.meta_set(db, STAMPED_KEYS, None)
    return n


COPIES = "fhir_stamped_copies"   # meta, set by schema step 45: merge_copies hasn't run yet
LAB_KINDS = ("lab", "labitem", "labday")   # a lab event's rows are each their own result's (sync.py, #899): not merged


def _copy_key(k):
    """Whether copies of one record of a connected source can share key k: a FHIR record's own (preview._record_key,
    not a day's or a content hash), keyed as it is now (not one rekey_stamped left stamped)."""
    from . import preview
    return isinstance(k, str) and k.startswith("fhir:") and preview._record_key(k) and not _STAMPED.match(k)


def merge_copies(db):
    """Once, at start-up after schema step 45, when rekey_stamped's work is done (#1068): records of one connection
    sharing one key, the copies #1022's bug made (each changed record added again at a pull, under a key of its own
    until rekey_stamped keyed them alike), are one record each, as updates in place would have left it: the earliest
    pull's, saying what the latest says, each later pull's version kept as what that pull changed (sync_changes), so
    undoing a pull puts back what the one before it brought and never takes away a record an earlier pull brought too.
    The later copies are removed (what a pull since changed of the latest goes with the record kept). Events (not lab
    events, whose rows are each their own result's), the profile-wide lists' entries, and a Costs mark the keying
    couldn't move onto the kept one's because it had one (the later of the two kept). Corrections find the kept one by
    its key already. Uploads' records aren't touched. Said in the app's log and the install log, with those counts.
    -> {"events", "entries", "marks"}: how many removed or moved; None when it's not to run now"""
    from . import activity, db as dbm, preview
    if not dbm.meta_get(db, COPIES) or dbm.meta_get(db, STAMPED_KEYS):
        return None
    out, pids = {"events": 0, "entries": 0, "marks": 0}, set()
    with dbm.transaction(db):
        imps = db.execute("SELECT id, profile_id, connection_id, meta FROM imports WHERE connection_id IS NOT NULL "
                          "ORDER BY id").fetchall()
        groups = collections.defaultdict(list)   # (profile, connection, key hash) -> [(import id, event id)]
        for imp in imps:
            for r in db.execute("SELECT id, key_hash, body FROM events WHERE import_id=?", (imp["id"],)).fetchall():
                try:
                    b = _dec(r["body"])
                except Exception:
                    continue
                if isinstance(b, dict) and b.get("k") not in LAB_KINDS and _copy_key(b.get("key")) and r["key_hash"]:
                    groups[(imp["profile_id"], imp["connection_id"], r["key_hash"])].append((imp["id"], r["id"]))
        for (pid, _, _), rows in groups.items():
            if len(rows) < 2:
                continue
            rows, was = sorted(rows), {}
            for _, eid in rows:
                r = db.execute("SELECT body, detail, cost FROM events WHERE id=?", (eid,)).fetchone()
                was[eid] = (r, {"body": _dec(r["body"]), "detail": _dec(r["detail"]), "cost": _dec(r["cost"])})
            keep, last = rows[0][1], rows[-1][1]
            since = db.execute("SELECT import_id, previous FROM sync_changes WHERE event_id=? ORDER BY id", (last,)).fetchall()
            for (iid, _), (_, before) in zip(rows[1:], rows):   # each later pull: what it found, the copy before it
                db.execute("INSERT INTO sync_changes(import_id, event_id, previous) VALUES(?, ?, ?)", (iid, keep, _enc(was[before][1])))
            db.execute("UPDATE events SET body=?, detail=?, cost=? WHERE id=?",
                       (was[last][0]["body"], was[last][0]["detail"], was[last][0]["cost"], keep))
            for _, eid in rows[1:]:
                db.execute("DELETE FROM events WHERE id=?", (eid,))
                out["events"] += 1
            for r in since:   # what pulls since changed of the latest copy, after (undone in turn, the latest first)
                db.execute("INSERT INTO sync_changes(import_id, event_id, previous) VALUES(?, ?, ?)", (r["import_id"], keep, r["previous"]))
            pids.add(pid)
        metas, copies = {}, collections.defaultdict(list)   # import id -> its meta; (profile, connection, path, key) -> [import id]
        for imp in imps:
            try:
                meta = _dec(imp["meta"]) or {}
                lists = preview._meta_lists(meta)
            except Exception:
                continue
            metas[imp["id"]] = meta
            for path, items in lists.items():
                for k in dict.fromkeys(x["key"] for x in items if isinstance(x, dict) and _copy_key(x.get("key"))):
                    copies[(imp["profile_id"], imp["connection_id"], path, k)].append(imp["id"])

        def entry(iid, path, k):
            return next(x for x in preview._meta_lists(metas[iid])[path] if isinstance(x, dict) and x.get("key") == k)
        changed = set()
        for (pid, _, path, k), iids in copies.items():
            if len(iids) < 2:
                continue
            keep, last = iids[0], iids[-1]
            since = [r for r in db.execute("SELECT id, import_id, previous FROM sync_changes WHERE meta_import=? ORDER BY id", (last,)).fetchall()
                     if (p := _dec(r["previous"])).get("path") == path and p.get("key") == k]
            for iid, before in zip(iids[1:], iids):
                db.execute("INSERT INTO sync_changes(import_id, meta_import, previous) VALUES(?, ?, ?)",
                           (iid, keep, _enc({"path": path, "key": k, "entry": entry(before, path, k)})))
            items = preview._meta_lists(metas[keep])[path]
            at = next(i for i, x in enumerate(items) if isinstance(x, dict) and x.get("key") == k)
            items[at] = entry(last, path, k)
            for iid in iids[1:]:
                items = preview._meta_lists(metas[iid])[path]
                gone = [x for x in items if isinstance(x, dict) and x.get("key") == k]
                items[:] = [x for x in items if not (isinstance(x, dict) and x.get("key") == k)]
                out["entries"] += len(gone)
            for r in since:   # what pulls since changed of the latest copy's entry, now of the one kept
                db.execute("DELETE FROM sync_changes WHERE id=?", (r["id"],))
                db.execute("INSERT INTO sync_changes(import_id, meta_import, previous) VALUES(?, ?, ?)", (r["import_id"], keep, r["previous"]))
            changed.update(iids)
            pids.add(pid)
        for iid in sorted(changed):
            db.execute("UPDATE imports SET meta=? WHERE id=?", (_enc(metas[iid]), iid))
        for r in db.execute("SELECT id, profile_id, target_key, value, created_by, at FROM cost_marks").fetchall():
            try:   # a mark rekey_stamped's UPDATE OR IGNORE left on a copy's old key, as the kept one had one
                m = _STAMPED.match(crypto.dec(r["target_key"]))
                src = m and common.stable_source(m.group(1))
                there = m and next((t for k in (f"fhir:{m.group(2)}", *([f"fhir:{src}|{m.group(2)}"] if src else []))
                                    if (t := db.execute("SELECT id, at FROM cost_marks WHERE profile_id=? AND target=?",
                                                        (r["profile_id"], oop.target_hash(k))).fetchone())), None)
            except Exception:
                continue
            if not there:
                continue
            if r["at"] > there["at"]:
                db.execute("UPDATE cost_marks SET value=?, created_by=?, at=? WHERE id=?",
                           (r["value"], r["created_by"], r["at"], there["id"]))
            db.execute("DELETE FROM cost_marks WHERE id=?", (r["id"],))
            out["marks"] += 1
            pids.add(r["profile_id"])
        for pid in pids:
            _bump(db, pid)
        dbm.meta_set(db, COPIES, None)
        if any(out.values()):
            print(f"Made the copies a connected source's changed records were saved as before #1022 one record each, the "
                  f"latest kept: {out['events']} record(s) and {out['entries']} list entries removed, {out['marks']} Costs "
                  "mark(s) moved.", flush=True)
            activity.log(db, "records.copies", detail=out, actor=activity.START_UP)
    return out


def known_event_keys(db, pid, hashes):
    """Which of these key hashes the profile's events already have."""
    return _known(db, "events", pid, hashes)


def known_series(db, pid, hashes):
    """{key hash: [series id]} for the profile's series with these key hashes."""
    out = {}
    for h in _chunks(sorted(set(hashes))):
        q = f"SELECT id, key_hash FROM series WHERE profile_id=? AND key_hash IN ({','.join('?' * len(h))})"
        for r in db.execute(q, (pid, *h)):
            out.setdefault(r["key_hash"], []).append(r["id"])
    return out


def _known(db, table, pid, hashes):
    found = set()
    for h in _chunks(sorted(set(hashes))):
        q = f"SELECT DISTINCT key_hash FROM {table} WHERE profile_id=? AND key_hash IN ({','.join('?' * len(h))})"
        found.update(r[0] for r in db.execute(q, (pid, *h)))
    return found


def _chunks(items, n=500):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def series_points(db, pid, series_ids):
    """The results (pts) of these series, and their text results (txt), as stored."""
    pts, txt = [], []
    for ids in _chunks(sorted(set(series_ids))):
        marks = ",".join("?" * len(ids))
        pts += [_dec(r["body"]) for r in db.execute(f"SELECT body FROM results WHERE profile_id=? AND series_id IN ({marks})", (pid, *ids))]
        for r in db.execute(f"SELECT body FROM series WHERE profile_id=? AND id IN ({marks})", (pid, *ids)):
            txt += (_dec(r["body"]) or {}).get("txt") or []
    return pts, txt


def series_results(db, pid, series_ids):
    """The results of these series with where each is: [(result id, series id, import id, the series' body, point)],
    for a result its source sends again to be updated in place (preview.py, #869)."""
    out = []
    for ids in _chunks(sorted(set(series_ids))):
        marks = ",".join("?" * len(ids))
        bodies = {r["id"]: _dec(r["body"]) or {} for r in db.execute(f"SELECT id, body FROM series WHERE profile_id=? AND id IN ({marks})", (pid, *ids))}
        out += [(r["id"], r["series_id"], r["import_id"], bodies.get(r["series_id"]) or {}, _dec(r["body"]))
                for r in db.execute(f"SELECT id, series_id, import_id, body FROM results WHERE profile_id=? AND series_id IN ({marks})", (pid, *ids))]
    return out


def events_with_keys(db, pid, keys):
    """The profile's events with these source keys, as the page's records have them (with "imp" and their details
    as "detail"), for the rows of a result its source sends again (preview.py, #869)."""
    hashes = key_hashes(db, keys)
    out = []
    for h in _chunks(sorted(set(hashes.values()))):
        q = f"SELECT id, import_id, body, detail FROM events WHERE profile_id=? AND key_hash IN ({','.join('?' * len(h))}) ORDER BY id"
        out += [{**(_dec(r["body"]) or {}), "id": r["id"], "imp": r["import_id"], "detail": _dec(r["detail"])} for r in db.execute(q, (pid, *h))]
    return out


def labitems_of(db, pid, import_ids):
    """The labitems these imports brought, as events_with_keys gives them: where a result of theirs has its row, for an
    Observation found by its id under another labitem key than its upload's (preview.py, #879)."""
    out = []
    for ch in _chunks(sorted(set(import_ids))):
        q = f"SELECT id, import_id, body, detail FROM events WHERE profile_id=? AND import_id IN ({','.join('?' * len(ch))}) ORDER BY id"
        for r in db.execute(q, (pid, *ch)):
            b = _dec(r["body"]) or {}
            if b.get("k") == "labitem":
                out.append({**b, "id": r["id"], "imp": r["import_id"], "detail": _dec(r["detail"])})
    return out


def keyless(db, pid, fmt):
    """Events and series without a source key that imports of format fmt brought: ([event body], [series body
    with its pts]). For the duplicate check's fallback (same importer, date, kind and content); importers give keys
    nowadays, so these are few."""
    events = [_dec(r["body"]) for r in db.execute("""SELECT e.body FROM events e JOIN imports i ON i.id = e.import_id
                                                     WHERE e.profile_id=? AND i.format=? AND e.key_hash=''""", (pid, fmt))]
    series = []
    for r in db.execute("""SELECT s.id, s.body FROM series s JOIN imports i ON i.id = s.import_id
                           WHERE s.profile_id=? AND i.format=? AND s.key_hash=''""", (pid, fmt)):
        pts = [_dec(p["body"]) for p in db.execute("SELECT body FROM results WHERE series_id=?", (r["id"],))]
        series.append({**(_dec(r["body"]) or {}), "pts": pts})
    return events, series


def metas(db, pid):
    """The profile-wide part (imports.meta) of each of its imports, merged as assemble() does."""
    out = {}
    for r in db.execute("SELECT meta FROM imports WHERE profile_id=? ORDER BY id", (pid,)):
        meta = _dec(r["meta"]) or {}
        meta.pop("costs", None)
        _merge(out, meta)
    return out


def imports(db, pid, origin=None):
    """The ids of a profile's imports, oldest first; only those of one origin when given."""
    q = "SELECT id FROM imports WHERE profile_id=?" + (" AND origin=?" if origin else "") + " ORDER BY id"
    return [r[0] for r in db.execute(q, (pid, origin) if origin else (pid,))]


def drop(db, pid, import_ids):
    """Remove imports with everything they brought in: events, series, results and documents (by cascade)."""
    if import_ids:
        db.executemany("DELETE FROM imports WHERE id=? AND profile_id=?", [(i, pid) for i in import_ids])
        _bump(db, pid)


VER = "ver"   # (#955) which version of its record an event or a list's entry is (its server, its lastUpdated, the date
             # of the upload it came in): kept for the import's preview, never served (an export keeps it: raw)


def _unversioned(meta):
    """An import's meta without its lists' entries' VER (the problem list's, upcoming visits'...), one level down as
    preview._meta_lists reads them."""
    def strip(v):
        return [{k: y for k, y in x.items() if k != VER} if isinstance(x, dict) else x for x in v] if isinstance(v, list) else v
    return {k: ({k2: strip(v2) for k2, v2 in v.items()} if isinstance(v, dict) else strip(v)) for k, v in meta.items()}


def _merge(into, meta):
    for k, v in meta.items():
        have = into.get(k)
        if isinstance(have, list) and isinstance(v, list):
            into[k] = have + v
        elif isinstance(have, dict) and isinstance(v, dict):
            into[k] = _merge(dict(have), v)
        else:
            into[k] = v
    return into


@contextmanager
def _snapshot(db):
    """Read everything from one state of the database, so an import committed meanwhile isn't half seen."""
    if db.in_transaction:
        yield
        return
    db.execute("BEGIN")
    try:
        yield
    finally:
        db.execute("COMMIT")


def assemble(db, pid, raw=False):
    """The profile's records in the shape the page reads, or None when nothing was ever imported into it. raw: as they
    were imported, for Export everything (#589): not tidied, no correction or same-result link applied (the export
    carries those apart, to be applied again once loaded), and nothing worked out from them (medications,
    out-of-pocket, units); the journal, doses and reminders listed as ever."""
    with _snapshot(db):
        return _assemble(db, pid, raw)


def source_label(origin, fmt):
    """What the page calls an import's source: the importer's label, or how the records got there."""
    from . import history
    return history.label(fmt, origin)


def _source(r):
    name = crypto.dec(r["name"]) if r["name"] else None
    return {"label": source_label(r["origin"], r["format"]), "origin": r["origin"], "format": r["format"],
            "at": r["at"], "synced": r["synced"], **({"name": name} if name else {})}


NAME_MAX = 80


def rename_import(db, pid, iid, name):
    """Give import iid of profile pid its own name (#38), or take it away (name empty or None: the importer's label
    again). Call inside a transaction. Returns (the import's label, the name kept or None); None when there's no
    such import. Raises ValueError for a name that's too long."""
    r = db.execute("SELECT origin, format FROM imports WHERE id=? AND profile_id=?", (iid, pid)).fetchone()
    if r is None:
        return None
    name = " ".join((name or "").split()) or None
    if name and len(name) > NAME_MAX:
        raise ValueError(f"A source's name can be at most {NAME_MAX} characters.")
    label = source_label(r["origin"], r["format"])
    if name == label:
        name = None
    db.execute("UPDATE imports SET name=? WHERE id=?", (crypto.enc(name) if name else None, iid))
    _bump(db, pid)
    return label, name


def _panels(labs):
    """(#994) Each lab series whose import gave it no panel is put in one: the panel another import gave a series of the
    same name, else the one lab-aliases.json's "panels" names for it (importers.common.lab_panel), so a lab typed in or
    from a spreadsheet is grouped as the pages group it (labGroups) with the rest of its panel, not under Other. The
    records as stored keep what each import gave."""
    given = {}
    for s in labs:
        if s.get("panel") and s.get("name") not in given:
            given[s.get("name")] = s["panel"]
    for s in labs:
        if not s.get("panel") and not s.get("watch"):
            s["panel"] = given.get(s.get("name")) or common.lab_panel(s.get("name"))


def _assemble(db, pid, raw=False):
    imps = db.execute("SELECT id, at, origin, format, synced, meta, name FROM imports WHERE profile_id=? ORDER BY id", (pid,)).fetchall()
    if not imps:
        return None
    out, costs, pay_imps = {}, [], []   # costs: (import, place in its list, row)
    for r in imps:
        meta = _dec(r["meta"]) or {}
        costs += [(r["id"], seq, c) for seq, c in meta.pop("costs", [])]
        for k in SERVED:
            meta.pop(k, None)
        pays = (meta.get("outOfPocket") or {}).get("payments") if isinstance(meta.get("outOfPocket"), dict) else None
        pay_imps += [r["id"]] * (len(pays) if isinstance(pays, list) else 0)   # which import each payment came from (#55)
        _merge(out, meta if raw else _unversioned(meta))
    out["imports"] = {str(r["id"]): _source(r) for r in imps}
    events, details = [], {}
    for r in db.execute("SELECT id, import_id, body, detail, cost, cost_seq FROM events WHERE profile_id=? ORDER BY id", (pid,)):
        events.append({"id": r["id"], **{k: v for k, v in _dec(r["body"]).items() if raw or k != VER}, "imp": r["import_id"]})
        if r["detail"] is not None:
            details[str(r["id"])] = _dec(r["detail"])
        if r["cost"] is not None:
            costs.append((r["import_id"], r["cost_seq"], _dec(r["cost"])))
    costs.sort(key=lambda c: c[:2])   # as each import listed them
    costs = [c for _, _, c in costs]
    if len(imps) > 1:   # one import keeps its own order; several are interleaved by time
        events.sort(key=lambda e: str(e.get("t") or ""))
        costs.sort(key=lambda c: str(c.get("d") or "") if isinstance(c, dict) else "")
    pts = {}
    for r in db.execute("SELECT series_id, body FROM results WHERE profile_id=? ORDER BY id", (pid,)):
        pts.setdefault(r["series_id"], []).append(_dec(r["body"]))
    labs = [{**_dec(r["body"]), "imp": r["import_id"], "pts": pts.get(r["id"], [])}
            for r in db.execute("SELECT id, import_id, body FROM series WHERE profile_id=? ORDER BY id", (pid,))]
    if raw:   # #589: the rows as stored, for an export to load again exactly
        out = {**out, "events": events, "details": details, "labs": labs, "costs": costs}
    else:
        _panels(labs)   # (#994) a series whose report gave no panel: its name's from another report, else lab-aliases.json's
        encounters.tidy(events, details)   # chart activity, plain visit titles, reminders (#236); corrections have the last word
        out = edits.apply(db, pid, {**out, "events": events, "details": details, "labs": labs, "costs": costs})
        hidden = out.pop(edits.UNLISTED, None)   # (#950) the lab events hidden by hand
        for e in out.get("events") or []:
            e.pop("orig", None)            # what tidy() changed, kept only for the corrections to find their events
        out = crossmatch.apply(db, pid, out)
        crossmatch.copies(out, crossmatch.not_same(db, pid))   # one result twice, not linked: each copy with the one
                                                               # result's range and flag (#825), never two said not the same (#833)
        crossmatch.unlisted(out, hidden)   # their results, on no lab day (#950)
        _row_series(out)   # each result row's series, for the drawer to read its flag from (#782)
        out = oop.annotate(db, pid, out, pay_imps)   # out-of-pocket: sources, HSA/FSA, matched to claims, counted once (#55)
    if (entries := journal.listing(db, pid)):   # only when there are some, like edits
        out["journal"] = entries
    if not raw and (meds := medications.history(out)):   # #50: each medication's spans and fills, only when there are some
        out["medications"] = meds
    from . import med_reminders
    if (rem := med_reminders.listing(db, pid)):   # #162: reminders for the calendar feed, only when there are some
        out["reminders"] = rem
    from . import med_log
    if (doses := med_log.listing(db, pid)):   # #235: the medication log, only when there's something in it
        out["doses"] = doses
    return out if raw else units.annotate(out)   # which series can be shown in other units, exactly (#41)


def _row_series(data):
    """Each result row the drawer lists named with the lab series its result is in (#782): a result's details get
    "series", one series name (or None) for each of its rows, by the match the same results use (crossmatch._row_of:
    the same import and day, the same value, the row's name or its canonical one, else the one row in the series'
    unit). The page reads a row's flag from that result, as Labs shows it, whatever the report called the test: a
    report's "Glucose" is the series "Glucose, Blood". A row two series' results both match is no series'."""
    drawer = crossmatch._rows(data)
    if not drawer:
        return
    found = {}   # (event id, row index) -> series name, or None where two series claim it
    for s in data.get("labs") or []:
        if not isinstance(s, dict) or s.get("watch"):
            continue
        for p in s.get("pts") or []:
            if isinstance(p, list) and p and (at := crossmatch._row_of(drawer, s, p)):
                found[at] = s.get("name") if found.get(at, s.get("name")) == s.get("name") else None
    details, by = data.get("details") or {}, collections.defaultdict(dict)
    for (eid, r), name in found.items():
        by[eid][r] = name
    for eid, names in by.items():
        d = details.get(str(eid))
        if isinstance(d, dict) and isinstance(d.get("rows"), list):
            details[str(eid)] = {**d, "series": [names.get(r) for r in range(len(d["rows"]))]}
    # (#972) a lab event others were merged into: each of those named with the series its rows' results are in, so its
    # lab day's badge counts them (util.js labDraws, crossmatch._draws), not their own import's other lab day of that day
    for e in data.get("events") or []:
        if isinstance(e, dict) and e.get(edits.FOLDED) and e.get("k") == "labitem":
            at, names = edits.rows_from(e, details.get(str(e["id"]))), collections.defaultdict(set)
            for r, (imp, t) in enumerate(at):
                if by[e["id"]].get(r):
                    names[(imp, t)].add(by[e["id"]][r])
            e[edits.FOLDED] = [[imp, t, sorted(names[(imp, t)])] for imp, t, *_ in e[edits.FOLDED]]


# the page asks for a profile's records on every load; putting them together decrypts every row, so the result
# is kept for a few profiles until their records change (profiles.records_rev): whole, as the server's own readers
# parse it, as the records page reads it (page(): without most of each record's details, #217), and each record's
# details on their own (details())
_cache, _cache_lock, CACHED_PROFILES = {}, threading.Lock(), 4

# ---- a record's details when it's opened (#217)
# The records page reads a record's details (a result's rows, a claim's lines, a message, a letter) when it's opened,
# GET .../details?ids=, not with every record: with ten years of records they were 40% of its data, mostly for records
# nobody opens. What the page reads of details without opening anything stays in its data: a lab day's list of
# results (how many, search, the drawer's list) whole, and of an immunization what the Overview lists. One cut short
# says so ("partial": true), so the page loads the rest when it's opened. The server's own readers (the visit summary,
# share links, the exports, medications) read served() or assemble(), with every record's details.
LISTED = {"labday": None, "immunization": ("dose", "where", "performer")}   # type: the fields kept (None: all)
DETAILS_MAX = 500   # ids in one request: a lab day lists a few dozen at most


def _listed(d):
    """What the page's data keeps of one record's details, or None."""
    if not isinstance(d, dict) or d.get("type") not in LISTED:
        return None
    keep = LISTED[d["type"]]
    return d if keep is None else {"type": d["type"], **{k: d[k] for k in keep if k in d}, "partial": True}


def _dumps(obj):
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _entry(db, pid):
    """What's kept for the profile: {key, rev, body, page, details: {event id: its details as JSON}}, or None when
    there's no such profile; body and page are None when nothing was ever imported into it."""
    with _snapshot(db):
        r = db.execute("SELECT records_rev, created FROM profiles WHERE id=?", (pid,)).fetchone()
        if not r:
            return None
        key = (r["records_rev"], r["created"])   # created: a new profile may be given a deleted one's id
        with _cache_lock:
            hit = _cache.get(pid)
            if hit and hit["key"] == key:
                _cache[pid] = _cache.pop(pid)   # most recently used last
                return hit
        data = _assemble(db, pid)
    entry = {"key": key, "rev": _rev(key), "body": None, "page": None, "details": {}, "docs": {}}
    if data is not None:
        entry["body"] = _dumps(data)
        details = data.get("details") or {}
        entry["page"] = _dumps({**data, "details": {k: x for k, v in details.items() if (x := _listed(v)) is not None}})
        entry["details"] = {str(k): _dumps(v) for k, v in details.items() if v is not None}
        entry["docs"] = _document_titles(data)
    with _cache_lock:
        _cache.pop(pid, None)
        _cache[pid] = entry
        while len(_cache) > CACHED_PROFILES:
            _cache.pop(next(iter(_cache)))
    return entry


_DOC_URL = re.compile(r"/documents/([^/#?]+)")


def _document_titles(data):
    """{document id: the title of the record that links to it, as the page shows it (its edits applied)}"""
    titles = {str(e.get("id")): e.get("title") for e in data.get("events") or [] if isinstance(e, dict)}
    out = {}
    for k, d in (data.get("details") or {}).items():
        if isinstance(d, dict) and d.get("type") == "file" and isinstance(d.get("url"), str) and isinstance(titles.get(str(k)), str):
            if (m := _DOC_URL.search(d["url"])) and titles[str(k)].strip():
                out.setdefault(m.group(1), titles[str(k)].strip())
    return out


def document_title(db, pid, doc):
    """The title of the record a document of profile pid belongs to, as the page shows it, or None (#342): what the
    activity log says was opened, rather than the stored file's name"""
    e = _entry(db, pid)
    return e["docs"].get(doc) if e else None


def served(db, pid):
    """assemble() as JSON bytes, every record's details with it (what the server's own readers parse), or None."""
    e = _entry(db, pid)
    return e and e["body"]


def _rev(key):
    return f"{key[0]}.{key[1]}"


def stamp(response, db, pid):
    """Say on a change's reply what the records' revision is now, as page() and details() say it (X-Records-Rev): a
    change to a list the page puts in place (the journal, the doses, the reminders, a Costs mark) moves it, and the
    offline copy the page keeps takes the change with it, so it keeps the details opened after it too (#405)."""
    r = db.execute("SELECT records_rev, created FROM profiles WHERE id=?", (pid,)).fetchone()
    response.headers["X-Records-Rev"] = _rev((r["records_rev"], r["created"])) if r else ""


def page(db, pid):
    """(the records page's data as the API sends it: assemble() with only the details in LISTED, the records'
    revision, which changes whenever they do), or None when the profile has no records (#217)."""
    e = _entry(db, pid)
    return None if not e or e["page"] is None else (e["page"], e["rev"])


def journal_page(db, pid, watch=False):
    """(the records page's data for a profile with journal entries but nothing imported: no records but the journal,
    the records' revision), or None when it has no entries either (#472: the records page shows the Timeline's
    Journal view for it, and the command palette finds its entries, rather than "no records yet"). watch: the
    profile has a watch summary (#477: a phone connected first, nothing imported), so its data is this too, with no
    entries, and the page shows Body and activity."""
    e = _entry(db, pid)
    entries = e and journal.listing(db, pid)
    if not e or not (entries or watch):
        return None
    return _dumps(units.annotate({"imports": {}, "events": [], "details": {}, "labs": [], "costs": [], "journal": entries})), e["rev"]


def page_reply(db, pid, reply):
    """(reply, a change's answer, with the records page's data as "records" (null when there are none) as JSON bytes,
    the records' revision). For a change any record may show, an edit or a link between results: the page puts these
    records in place of its own, rather than loading them again (#271)."""
    got = page(db, pid)
    head = _dumps(reply)[:-1] + (b"," if reply else b"")
    return head + b'"records":' + (got[0] if got else b"null") + b"}", got[1] if got else ""


def details(db, pid, ids):
    """({"details": {event id: its details}} as JSON bytes, for those of the ids that have details; the records'
    revision), or None when the profile has no records (#217)."""
    e = _entry(db, pid)
    if not e or e["page"] is None:
        return None
    have = e["details"]
    parts = [b'"' + k.encode() + b'":' + have[k] for k in dict.fromkeys(str(i) for i in ids) if k in have]
    return b'{"details":{' + b",".join(parts) + b"}}", e["rev"]


def forget(pid=None):
    """Drop what served() kept for a profile (one being deleted), or for all."""
    with _cache_lock:
        if pid is None:
            _cache.clear()
        else:
            _cache.pop(pid, None)
