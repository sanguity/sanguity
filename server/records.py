"""A profile's records as rows: imports, events, series and results (schema version 10, see db.py).

The page reads one object per profile (GET /api/profiles/{pid}/data), in the shape build.py has always written
to data.json, and each part of it has its place:

  events     [{id, t, k, title, sub, end, abn, hidden...}]   one row each in events (body)
  details    {event id: {...}}                               events.detail
  costs      [{id: event id, d, billed, paid, owe...}]       events.cost, the claim's own row
  labs       [{name, unit, panel, loinc, n, txt, pts}]       series, with each point of pts a row in results
  the rest   built, source, upcoming, problems, outOfPocket, milestones, clinicWeights...   imports.meta

store() takes such an object apart into the rows of one import; assemble() puts a profile's imports back
together. An event's id is its row's, so the ids an import arrives with are mapped on the way in, the ones a lab
day lists (details[...].ids) included. With several imports, lists in their meta are joined and single values
come from the newest.

Everything a row holds is encrypted (crypto.enc); only ids and the links between rows are plain.
"""
import json, threading
from contextlib import contextmanager

from . import crypto

FORMAT = "sanguity-data"            # imports.format of a data.json (build.py, the demo, an export zip)
ROWS = ("events", "details", "labs", "costs")   # the parts kept as rows; the rest of the object is the meta


def _enc(obj):
    return crypto.enc(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))


def _dec(text):
    return None if text is None else json.loads(crypto.dec(text))


def _bump(db, pid):
    db.execute("UPDATE profiles SET records_rev=records_rev+1 WHERE id=?", (pid,))


def store(db, pid, data, origin, fmt=FORMAT, now=None):
    """Add data (the data.json shape, already checked to have an events list) to profile pid as one new import.
    Call inside a transaction. Returns the import's id."""
    from . import db as dbm
    assert db.in_transaction, "records.store must run inside a transaction"
    details = {str(k): v for k, v in (data.get("details") or {}).items()}
    events = [e for e in data.get("events") or [] if isinstance(e, dict)]
    meta = {k: v for k, v in data.items() if k not in ROWS}

    # the new ids, decided up front (the transaction holds the write lock), so a lab day can list them
    base = db.execute("SELECT IFNULL(MAX(id), 0) FROM events").fetchone()[0]
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

    iid = db.execute("INSERT INTO imports(profile_id, at, origin, format, meta) VALUES(?, ?, ?, ?, ?)",
                     (pid, now or dbm.now(), origin, fmt, _enc(meta))).lastrowid
    rows = []
    for n, e in enumerate(events, start=base + 1):
        key = str(e.get("id"))
        first = ids[key] == n
        d = details.get(key) if first else None
        if isinstance(d, dict) and isinstance(d.get("ids"), list):
            d = {**d, "ids": [ids[str(i)] for i in d["ids"] if str(i) in ids]}
        seq, c = costs.get(key, (None, None)) if first else (None, None)
        rows.append((n, pid, iid, _enc({k: v for k, v in e.items() if k != "id"}),
                     None if d is None else _enc(d), None if c is None else _enc({**c, "id": n}), seq))
    db.executemany("INSERT INTO events(id, profile_id, import_id, body, detail, cost, cost_seq) VALUES(?, ?, ?, ?, ?, ?, ?)", rows)

    for s in data.get("labs") or []:
        if not isinstance(s, dict):
            continue
        sid = db.execute("INSERT INTO series(profile_id, import_id, body) VALUES(?, ?, ?)",
                         (pid, iid, _enc({k: v for k, v in s.items() if k != "pts"}))).lastrowid
        db.executemany("INSERT INTO results(profile_id, import_id, series_id, body) VALUES(?, ?, ?, ?)",
                       [(pid, iid, sid, _enc(p)) for p in s.get("pts") or []])
    _bump(db, pid)
    return iid


def imports(db, pid, origin=None):
    """The ids of a profile's imports, oldest first; only those of one origin when given."""
    q = "SELECT id FROM imports WHERE profile_id=?" + (" AND origin=?" if origin else "") + " ORDER BY id"
    return [r[0] for r in db.execute(q, (pid, origin) if origin else (pid,))]


def drop(db, pid, import_ids):
    """Remove imports with everything they brought in: events, series, results and documents (by cascade)."""
    if import_ids:
        db.executemany("DELETE FROM imports WHERE id=? AND profile_id=?", [(i, pid) for i in import_ids])
        _bump(db, pid)


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


def assemble(db, pid):
    """The profile's records in the shape the page reads, or None when nothing was ever imported into it."""
    with _snapshot(db):
        return _assemble(db, pid)


def _assemble(db, pid):
    imps = db.execute("SELECT id, meta FROM imports WHERE profile_id=? ORDER BY id", (pid,)).fetchall()
    if not imps:
        return None
    out, costs = {}, []   # costs: (import, place in its list, row)
    for r in imps:
        meta = _dec(r["meta"]) or {}
        costs += [(r["id"], seq, c) for seq, c in meta.pop("costs", [])]
        _merge(out, meta)
    events, details = [], {}
    for r in db.execute("SELECT id, import_id, body, detail, cost, cost_seq FROM events WHERE profile_id=? ORDER BY id", (pid,)):
        events.append({"id": r["id"], **_dec(r["body"])})
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
    labs = [{**_dec(r["body"]), "pts": pts.get(r["id"], [])}
            for r in db.execute("SELECT id, body FROM series WHERE profile_id=? ORDER BY id", (pid,))]
    return {**out, "events": events, "details": details, "labs": labs, "costs": costs}


# the page asks for a profile's records on every load; putting them together decrypts every row, so the result
# is kept for a few profiles until their records change (profiles.records_rev)
_cache, _cache_lock, CACHED_PROFILES = {}, threading.Lock(), 4


def served(db, pid):
    """assemble() as the JSON bytes the API sends, or None."""
    with _snapshot(db):
        r = db.execute("SELECT records_rev, created FROM profiles WHERE id=?", (pid,)).fetchone()
        if not r:
            return None
        key = (r["records_rev"], r["created"])   # created: a new profile may be given a deleted one's id
        with _cache_lock:
            hit = _cache.get(pid)
            if hit and hit[0] == key:
                _cache[pid] = _cache.pop(pid)   # most recently used last
                return hit[1]
        data = _assemble(db, pid)
    body = None if data is None else json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    with _cache_lock:
        _cache.pop(pid, None)
        _cache[pid] = (key, body)
        while len(_cache) > CACHED_PROFILES:
            _cache.pop(next(iter(_cache)))
    return body


def forget(pid=None):
    """Drop what served() kept for a profile (one being deleted), or for all."""
    with _cache_lock:
        if pid is None:
            _cache.clear()
        else:
            _cache.pop(pid, None)
