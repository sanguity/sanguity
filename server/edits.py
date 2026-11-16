"""Corrections made in the app (issue #33): hide an event, rename it, fix its date, say a "stay" wasn't a hospital
stay, or merge two events into one. Stored in the edits table (schema version 15, see db.py) and applied to the
records each time they are put together (records._assemble calls apply()).

An edit belongs to the profile, never to an import: undoing or replacing an import, or a rebuild of the setup
build, leaves it in place. It finds its event again by the event's key, never by its id (a re-import gives every
event a new id):

  - an importer's source key ("fhir:Observation/abc", see docs/importers.md, "Source keys"), when the event has one;
  - otherwise a content key, "build:h:" + a hash of the kind, the start time to the minute and the title. The setup
    build (build.py) and data.json files from before source keys give no keys, and are re-imported as they are, so
    the same event gets the same content key every time;
  - "build:d:" + the same hash with the day instead of the minute: only the corrections.json migration makes these,
    because a corrections.json entry names a day, not a time.

Edits made in the app always use the first two. When a rebuild gives a build event a new title (a new build.py
reads its source differently), follow_rebuild() moves its edits to it, matched by kind and start time.
An edit whose event is gone (the import was undone, or the event
changed in the source) stays stored, unapplied, and is listed as not found until its event comes back or someone
removes the edit.

How each action changes the records:
  hide      the event is left out, with its row on the Costs tab and its place in any lab day's list
  rename    a new title
  date      a new start time; an end moves with it, so the event lasts as long as before
  not_stay  a "stay" becomes a "visit" with no end (so it no longer shades the charts as a hospital stay); its detail
            reads as a visit's
  merge     two events become one. The first (the target) keeps everything it has: kind, title, time, its own detail.
            The second's detail fills in only the fields the first lacks, its cost row and its place in a lab day
            move to the first, an abnormal flag on either marks the result, and the drawer lists what was merged
            in, with the import it came from (details.merged[].imp), so both sources show. Then the second is
            left out.

Edits apply in the order they were made, merges last, so a merged event's own corrections are made first. One edit
may match several events (two keyless events with the same kind, minute and title): it applies to each, except a
merge, which takes the first of each side.
"""
import hashlib, json, os, re
from datetime import datetime, timedelta

from . import config, crypto, db as dbm, security as sec

ACTIONS = ("hide", "rename", "date", "not_stay", "merge")
CONTENT, DAY = "build:h:", "build:d:"
MIGRATED = "corrections_migrated"     # meta: when corrections.json was loaded into edits
TITLE_MAX = 300
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?$")


# ---------------------------------------------------------------- keys

def _h(*fields):
    """As docs/importers.md has importers hash records with no id of their own."""
    return hashlib.sha256("\x1f".join(str(f).strip().lower() for f in fields).encode("utf-8")).hexdigest()[:16]


def content_key(kind, t, title):
    return CONTENT + _h(kind, str(t or "")[:16], title)


def day_key(kind, day, title):
    return DAY + _h(kind, str(day or "")[:10], title)


def keys_of(e):
    """The keys an event answers to, as it was imported (before any edit): its source key, or its content keys."""
    k = e.get("key")
    if isinstance(k, str) and k.strip():
        return [k]
    return [content_key(e.get("k"), e.get("t"), e.get("title")), day_key(e.get("k"), e.get("t"), e.get("title"))]


def key_of(e):
    """The key an edit made in the app stores for this event."""
    return keys_of(e)[0]


def target_hash(key):
    """A keyed hash of an event's key, so the table can be searched by it without holding the key in the clear."""
    return sec.pepper_mac(b"sanguity edit target\x00" + key.encode("utf-8")).hex()[:32]


# ---------------------------------------------------------------- applying

def _enc(obj):
    return crypto.enc(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))


def _dec(text):
    return None if text is None else json.loads(crypto.dec(text))


def _rows(db, pid):
    return db.execute("""SELECT e.id, e.target_key, e.action, e.other_key, e.value, e.at, u.username
                         FROM edits e LEFT JOIN users u ON u.id = e.created_by
                         WHERE e.profile_id=? ORDER BY e.id""", (pid,)).fetchall()


def _parse(t):
    try:
        return datetime.fromisoformat(str(t).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _moved(e, t):
    """The event at a new start time, its end (if any) as far after it as before."""
    start, end = _parse(e.get("t")), _parse(e.get("end")) if e.get("end") else None
    e["t"] = t
    if e.get("end"):
        new = _parse(t)
        if start and end and new:
            e["end"] = (new.replace(tzinfo=None) + (end - start)).isoformat(timespec="seconds")


def _was(e):
    return {"k": e.get("k"), "t": e.get("t"), "title": e.get("title")}


def apply(db, pid, data):
    """Apply the profile's edits to its assembled records (the page's shape, see records.py) and list them under
    data["edits"]: [{id, action, found, ids (events it changed), value, was, by, at}]. Returns data, unchanged (no
    "edits" either) when the profile has none, so records nobody corrected are served exactly as imported."""
    rows = _rows(db, pid)
    listed = []
    data.pop("edits", None)   # never one an import brought along (an export's data.json)
    if not rows:
        return data
    events, details, costs = data.get("events") or [], data.get("details") or {}, data.get("costs") or []
    index = {}
    for e in events:
        for k in keys_of(e):
            index.setdefault(k, []).append(e)
    hidden, folded, moved = set(), {}, False   # folded: id of a merged-in event -> the id it went into

    def entry(r, value, hits, found):
        v = {k: x for k, x in (value or {}).items() if k != "was"}
        return {"id": r["id"], "action": r["action"], "found": found, "ids": [e["id"] for e in hits], "value": v,
                "was": (value or {}).get("was") or (_was(hits[0]) if hits else None), "by": r["username"], "at": r["at"]}

    for r in sorted(rows, key=lambda r: (r["action"] == "merge", r["id"])):
        value = _dec(r["value"]) or {}
        hits = [e for e in index.get(crypto.dec(r["target_key"]), []) if e["id"] not in hidden and e["id"] not in folded]
        a = r["action"]
        if a == "merge":
            others = [e for e in index.get(crypto.dec(r["other_key"] or ""), []) if e["id"] not in hidden and e["id"] not in folded]
            if not hits or not others or hits[0] is others[0]:
                listed.append(entry(r, value, hits[:1], False))
                continue
            first, second = hits[0], others[0]
            _merge(first, second, details)
            folded[second["id"]] = first["id"]
            listed.append({**entry(r, value, [first], True), "ids": [first["id"]], "other": _was(second)})
            continue
        for e in hits:
            if a == "hide":
                hidden.add(e["id"])
            elif a == "rename" and value.get("title"):
                e["title"] = value["title"]
            elif a == "date" and value.get("t"):
                _moved(e, value["t"])
                moved = True
            elif a == "not_stay" and e.get("k") == "stay":
                e["k"] = "visit"
                e.pop("end", None)
                d = details.get(str(e["id"]))
                details[str(e["id"])] = {**(d if isinstance(d, dict) else {}), "type": "visit",
                                         "note": "Recorded as a hospital stay; marked in Sanguity as not one."}
                if value.get("title"):
                    e["title"] = value["title"]
        listed.append(entry(r, value, hits, bool(hits)))

    gone = hidden | set(folded)
    if gone:
        events = [e for e in events if e["id"] not in gone]
        for i in gone:
            details.pop(str(i), None)
        for d in details.values():
            if isinstance(d, dict) and isinstance(d.get("ids"), list) and any(i in gone for i in d["ids"]):
                ids = []
                for i in d["ids"]:
                    i = folded.get(i, i)
                    if i not in hidden and i not in ids:
                        ids.append(i)
                d["ids"] = ids
        costs = [c for c in costs if not (isinstance(c, dict) and c.get("id") in hidden)]
        costs = [{**c, "id": folded[c["id"]]} if isinstance(c, dict) and c.get("id") in folded else c for c in costs]
    if moved:
        events.sort(key=lambda e: str(e.get("t") or ""))
    data.update(events=events, details=details, costs=costs, edits=sorted(listed, key=lambda x: x["id"]))
    return data


def _merge(first, second, details):
    if second.get("abn") and not first.get("abn"):
        first["abn"] = second["abn"]
    a, b = details.get(str(first["id"])), details.get(str(second["id"]))
    d = dict(a) if isinstance(a, dict) else {}
    if isinstance(b, dict):
        for k, v in b.items():
            if k not in d or d[k] in (None, "", []):
                d[k] = v
    d["merged"] = (d.get("merged") or []) + [{**_was(second), "imp": second.get("imp")}]   # imp: its source (#100)
    details[str(first["id"])] = d


# ---------------------------------------------------------------- adding and removing

class EditError(ValueError):
    """An edit that can't be made, said in plain language."""


def _event(db, pid, eid):
    r = db.execute("SELECT body FROM events WHERE id=? AND profile_id=?", (eid, pid)).fetchone()
    if not r:
        raise EditError("There's no such event in this profile's records.")
    return _dec(r["body"])


def _insert(db, pid, uid, action, key, value, other=None, at=None):
    return db.execute("""INSERT INTO edits(profile_id, target, target_key, action, other, other_key, value, created_by, at)
                         VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                      (pid, target_hash(key), crypto.enc(key), action,
                       None if other is None else target_hash(other), None if other is None else crypto.enc(other),
                       _enc(value), uid, at or dbm.now())).lastrowid


def _bump(db, pid):
    from . import records
    records._bump(db, pid)


def follow_rebuild(db, pid, before, after):
    """After a rebuild of the setup build: an edit whose event came back with a new title follows it. `before` and
    `after` are the build's events (bodies, as imported) before and after. An edit's content key that no event in
    `after` answers to moves to the event of the same kind at the same minute (for a "build:d:" key, the same day),
    but only when that time holds exactly one such event before and one after, so it can't land on the wrong one.
    Keyed events are left alone: their key doesn't change with their title. Call inside the import's transaction.
    Returns how many edits moved."""
    moves = {}
    for make, width in ((content_key, 16), (day_key, 10)):
        slots = ({}, {})
        for side, events in zip(slots, (before, after)):
            for e in events:
                if isinstance(e, dict) and not (isinstance(e.get("key"), str) and e["key"].strip()):
                    side.setdefault((e.get("k"), str(e.get("t") or "")[:width]), []).append(e)
        now = {make(e.get("k"), e.get("t"), e.get("title")) for es in slots[1].values() for e in es}
        for slot, old in slots[0].items():
            new = slots[1].get(slot) or []
            if len(old) == 1 and len(new) == 1:
                was, to = make(old[0].get("k"), old[0].get("t"), old[0].get("title")), make(new[0].get("k"), new[0].get("t"), new[0].get("title"))
                if was != to and was not in now:
                    moves[was] = to
    if not moves:
        return 0
    moved = 0
    for r in db.execute("SELECT id, target_key, other_key FROM edits WHERE profile_id=?", (pid,)).fetchall():
        tk, ok = crypto.dec(r["target_key"]), crypto.dec(r["other_key"]) if r["other_key"] else None
        if tk in moves or ok in moves:
            tk, ok = moves.get(tk, tk), moves.get(ok, ok) if ok else None
            db.execute("UPDATE edits SET target=?, target_key=?, other=?, other_key=? WHERE id=?",
                       (target_hash(tk), crypto.enc(tk), None if ok is None else target_hash(ok),
                        None if ok is None else crypto.enc(ok), r["id"]))
            moved += 1
    return moved


def add(db, pid, uid, action, event_id, title=None, t=None, other_id=None):
    """Record an edit of the event with this id (as the page has it now). A second edit of the same kind to the same
    event replaces the first. Call inside a transaction. Returns (edit id, the event as imported)."""
    if action not in ACTIONS:
        raise EditError("Unknown kind of edit.")
    e = _event(db, pid, event_id)
    key, value, other = key_of(e), {"was": _was(e)}, None
    if action == "rename":
        title = (title or "").strip()
        if not title or len(title) > TITLE_MAX:
            raise EditError(f"Give a title of 1 to {TITLE_MAX} characters.")
        value["title"] = title
    elif action == "date":
        t = (t or "").strip()
        if not _TIME.match(t) or not _parse(t):
            raise EditError("Give the date as YYYY-MM-DD, with a time (THH:MM) if you know it.")
        value["t"] = (t + "T00:00:00" if len(t) == 10 else t + ":00" if len(t) == 16 else t)
    elif action == "not_stay" and e.get("k") != "stay":
        raise EditError("Only a hospital stay can be marked as not one.")
    elif action == "merge":
        if other_id is None or int(other_id) == int(event_id):
            raise EditError("Choose another event to merge with this one.")
        o = _event(db, pid, other_id)
        other, value["other"] = key_of(o), _was(o)
        if other == key:
            raise EditError("These two events can't be told apart, so they can't be merged.")
    same = "SELECT id FROM edits WHERE profile_id=? AND target=? AND action=?" + (" AND other=?" if other else "")
    args = (pid, target_hash(key), action) + ((target_hash(other),) if other else ())
    db.executemany("DELETE FROM edits WHERE id=?", [(r[0],) for r in db.execute(same, args).fetchall()])
    eid = _insert(db, pid, uid, action, key, value, other)
    _bump(db, pid)
    return eid, e


def remove(db, pid, edit_id):
    """Remove an edit (undo it). Returns what it was, {"action", "was"}, or None if there's no such edit."""
    r = db.execute("SELECT action, value FROM edits WHERE id=? AND profile_id=?", (edit_id, pid)).fetchone()
    if not r:
        return None
    db.execute("DELETE FROM edits WHERE id=?", (edit_id,))
    _bump(db, pid)
    return {"action": r["action"], "was": (_dec(r["value"]) or {}).get("was")}


def listing(db, pid):
    """The profile's edits as apply() lists them, with whether each found its event in the current records."""
    from . import records
    return (records.assemble(db, pid) or apply(db, pid, {"events": [], "details": {}, "costs": []})).get("edits", [])


# ---------------------------------------------------------------- corrections.json

def corrections_file():
    """Where an install keeps corrections.json, as build.py reads it: SANGUITY_CORRECTIONS, else the copy the
    Docker setup puts beside the build (docker/setup.py), else next to build.py."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for p in (os.environ.get("SANGUITY_CORRECTIONS"), os.path.join(config.BUILD_DIR, "corrections.json"),
              os.path.join(root, "corrections.json")):
        if p and os.path.isfile(p):
            return p
    return None


def migrate_corrections(db):
    """Once: turn the event corrections in an existing corrections.json into edits on the first profile, so build.py
    no longer applies them. Milestones stay in corrections.json (editing them in the app is #39). Each
    not_hospital_stays entry {date, org, title} becomes a not_stay edit of the stay that day at that organisation;
    with a title (build.py also renamed that day's "Hospital admission" visit to it), that visit is renamed and the
    stay merged into it, so one visit remains, as before. Returns how many edits it made (None: nothing to do)."""
    if dbm.meta_get(db, MIGRATED):
        return None
    path = corrections_file()
    pid = db.execute("SELECT id FROM profiles ORDER BY id LIMIT 1").fetchone()
    if not path or not pid:
        return None   # tried again on the next start
    corr = json.loads(crypto.read_file(path).decode("utf-8"))
    from .importers.common import _stay_title
    n = 0
    with dbm.transaction(db):
        for x in corr.get("not_hospital_stays") or []:
            if not isinstance(x, dict) or not str(x.get("date") or "")[:10]:
                continue
            day, org, title = str(x["date"])[:10], x.get("org") or "", (x.get("title") or "").strip()
            why = {"why": x["why"]} if x.get("why") else {}
            stay = day_key("stay", day, _stay_title(org))
            _insert(db, pid[0], None, "not_stay", stay, {"was": {"k": "stay", "t": day, "title": _stay_title(org)}, **why})
            n += 1
            if title:
                visit = day_key("visit", day, "Hospital admission")
                was = {"k": "visit", "t": day, "title": "Hospital admission"}
                _insert(db, pid[0], None, "rename", visit, {"was": was, "title": title[:TITLE_MAX], **why})
                _insert(db, pid[0], None, "merge", visit, {"was": was, "other": {"k": "visit", "t": day, "title": _stay_title(org)}, **why}, other=stay)
                n += 2
        dbm.meta_set(db, MIGRATED, str(dbm.now()))
        if n:
            _bump(db, pid[0])
    return n


def leftover_corrections():
    """Event corrections still in corrections.json after the migration: they are no longer read."""
    path = corrections_file()
    try:
        corr = json.loads(crypto.read_file(path).decode("utf-8")) if path else {}
    except (OSError, ValueError):
        return 0
    return len(corr.get("not_hospital_stays") or [])
