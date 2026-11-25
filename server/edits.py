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
A lab event's key is its day's or its report's ("fhir:labitem:2025-03-04"), shared by every import's lab event of it
(an upload's, each pull's, another source's file of that day), and a lab day's by every lab day of its date. So an edit
of either also keeps what it holds (holds(): a lab event's rows, each by its own record, else its test; a lab day's,
those of the lab events it lists), and finds only the one holding them (#937): hiding the ALT's lab event hides it
alone, not the upload's of that day too.
An edit whose event is gone (the import was undone, or the event
changed in the source) stays stored, unapplied, and is listed as not found until its event comes back or someone
removes the edit.

How each action changes the records:
  hide      the event is left out, with its row on the Costs tab and its place in any lab day's list. A lab day hidden
            takes the lab events it lists with it, and one all of whose lab events are hidden goes too (#950); their
            results stay in Labs, charts, share links and summaries, on no lab day (crossmatch.unlisted), and the
            drawer's Hide says so
  rename    a new title
  date      a new start time; an end moves with it, so the event lasts as long as before
  not_stay  a "stay" becomes a "visit" with no end (so it no longer shades the charts as a hospital stay); its detail
            reads as a visit's
  kind      the event moves to another category (#237): only between the kinds in MOVABLE, whose details are
            free-form; its detail stays as it is
  merge     two events become one. The first (the target) keeps everything it has: kind, title, time, its own detail.
            The second's detail fills in only the fields the first lacks, as its Costs row does the first's (one
            row, counted once, #924; the second's becomes the first's where it has none), its place in a lab day
            moves to the first, an abnormal flag on either marks the result, and the drawer lists what was merged
            in, with the import it came from (details.merged[].imp), so both sources show. Two lab days merged list
            both days' lab events (#915), titled for them. A lab event merged into another gives it its rows too
            (#972), each saying the import and time of the lab event it came in (details' ROWS_FROM), where its
            result finds it (crossmatch._rows, drawer.js rowPoint); the event says those lab events (FOLDED, with
            the series their rows' results are in, records._row_series), so its lab day's badge counts their results
            (util.js labDraws); it's listed by its own lab day alone, and a lab day left listing nothing goes. Then
            the second is left out.

Edits apply in the order they were made, merges last, so a merged event's own corrections are made first. One edit
may match several events (two keyless events with the same kind, minute and title): it applies to each, except a
merge, which takes the first of each side.

Similar entries (#237): a rename or a change of category can be made to the open event and, at once, to others that
look the same (similar(): the same source, kind and title as the page shows them). Each gets its own edit, all with
one "batch" id in their value, and undoing any of them undoes the batch. A change of category can also be kept for
new imports: a rule (action "kind_rule", its key "rule:" + a hash of the source, kind and title) moves every entry
from that source with that kind and title as the page shows them, now and in later imports, except one moved on its
own. A source is an import's origin and format ("connection:3|fhir", "upload|epic-mychart"): the same portal synced
again, or the next export uploaded, is the same source.
"""
import hashlib, json, os, re, secrets
from datetime import datetime, timedelta

from . import config, crypto, db as dbm, security as sec
from .importers import common

ACTIONS = ("hide", "rename", "date", "not_stay", "merge", "kind")
UNLISTED = "_unlisted"   # (#950) apply()'s lab events hidden, for records._assemble to pop (never served)
RULE = "kind_rule"   # not made with add(): add_batch()
# the kinds an entry can move between (#237): their details are free-form, so any of them reads as any other
MOVABLE = ("visit", "contact", "procedure", "imaging", "study", "message", "appt_msg", "auto_msg", "letter", "document")
SIMILAR_MAX = 2000
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
    """The keys an event answers to, as it was imported (before any edit): its source key, or its content keys. Its
    kind and title as imported, before encounters.tidy() changed them ("orig")."""
    k = e.get("key")
    if isinstance(k, str) and k.strip():
        return [k]
    o = e.get("orig") if isinstance(e.get("orig"), dict) else {}
    kind, title = o.get("k", e.get("k")), o.get("title", e.get("title"))
    return [content_key(kind, e.get("t"), title), day_key(kind, e.get("t"), title)]


def key_of(e):
    """The key an edit made in the app stores for this event."""
    return keys_of(e)[0]


def _rows_held(d):
    if not (isinstance(d, dict) and isinstance(d.get("rows"), list)):
        return set()
    keys = d.get("keys") if isinstance(d.get("keys"), list) else []
    out = set()
    for j, r in enumerate(d["rows"]):
        k = keys[j] if j < len(keys) else None
        if isinstance(k, str) and k.strip():
            out.add(k)
        elif isinstance(r, list) and r and isinstance(r[0], str) and r[0].strip():
            out.add("test:" + " ".join(r[0].split()).casefold())
    return out


def holds(kind, detail, detail_of):
    """What an event holds, by which an edit of it finds this one of the events that share its key (#937): a lab
    event's rows, each by its own record (its details' "keys": the Observation it is) or else its test's name; a lab
    day's, those of the lab events it lists (detail_of(id): a listed event's details). Empty for any other kind."""
    if kind == "labitem":
        return _rows_held(detail)
    if kind == "lab" and isinstance(detail, dict) and isinstance(detail.get("ids"), list):
        return set().union(*(_rows_held(detail_of(i)) for i in detail["ids"] if isinstance(i, int)))
    return set()


def _held(value, field="holds"):
    """What an edit keeps of what its event held, or None (an edit of any other kind, or made before #937)."""
    h = value.get(field) if isinstance(value, dict) else None
    return ({x for x in h if isinstance(x, str)} or None) if isinstance(h, list) else None


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


def source_of(imp):
    """The source an import came from, for similar entries and rules: its origin and format."""
    return f"{imp.get('origin') or ''}|{imp.get('format') or ''}"


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
    days = set()   # the lab days whose lists change: one merged into, or listing an event hidden or merged in (#889)

    def entry(r, value, hits, found):
        v = {k: x for k, x in (value or {}).items() if k not in ("was", "by")}
        return {"id": r["id"], "action": r["action"], "found": found, "ids": [e["id"] for e in hits], "value": v,
                "was": (value or {}).get("was") or (_was(hits[0]) if hits else None),
                "by": r["username"] or (value or {}).get("by"), "at": r["at"]}   # by: an export's, by name (#589)

    def found(key, held):   # the events an edit finds: by its key and, of those sharing it, the one holding what it held (#937)
        out = [e for e in index.get(key, []) if e["id"] not in hidden and e["id"] not in folded]
        if held is None:
            return out
        return [e for e in out if held & holds(e.get("k"), details.get(str(e["id"])), lambda i: details.get(str(i)))]

    kind_set, rules = set(), []   # kind_set: events moved on their own, which a rule leaves alone
    for r in sorted(rows, key=lambda r: (r["action"] == "merge", r["id"])):
        value = _dec(r["value"]) or {}
        if r["action"] == RULE:
            rules.append((r, value))
            continue
        hits = found(crypto.dec(r["target_key"]), _held(value))
        a = r["action"]
        if a == "merge":
            others = found(crypto.dec(r["other_key"] or ""), _held(value, "other_holds"))
            if not hits or not others or hits[0] is others[0]:
                listed.append(entry(r, value, hits[:1], False))
                continue
            first, second = hits[0], others[0]
            _merge(first, second, details)
            days.add(str(first["id"]))
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
            elif a == "kind" and e.get("k") in MOVABLE and value.get("k") in MOVABLE:
                e["k"] = value["k"]
                kind_set.add(e["id"])
            elif a == "not_stay" and e.get("k") == "stay":
                e["k"] = "visit"
                e.pop("end", None)
                d = details.get(str(e["id"]))
                details[str(e["id"])] = {**(d if isinstance(d, dict) else {}), "type": "visit",
                                         "note": "Recorded as a hospital stay; marked in Sanguity as not one."}
                if value.get("title"):
                    e["title"] = value["title"]
        listed.append(entry(r, value, hits, bool(hits)))

    sources = {str(i): source_of(x) for i, x in (data.get("imports") or {}).items() if isinstance(x, dict)}
    for r, value in rules:   # after the entries' own edits, so they match what the page shows
        title = str(value.get("title") or "").strip().casefold()
        hits = [e for e in events if e["id"] not in hidden and e["id"] not in folded and e["id"] not in kind_set
                and e.get("k") == value.get("from") and str(e.get("title") or "").strip().casefold() == title
                and sources.get(str(e.get("imp"))) == value.get("source") and value.get("k") in MOVABLE]
        for e in hits:
            e["k"] = value["k"]
        listed.append({**entry(r, value, hits, bool(hits)), "was": {"k": value.get("from"), "title": value.get("title")}})

    # (#950) a lab day hidden hides the lab events it lists, and one all of whose lab events are hidden goes with them;
    # the lab events hidden are kept apart for crossmatch.unlisted, their results in Labs on no lab day
    kinds = {e["id"]: e.get("k") for e in events}
    for e in events:
        ids = (details.get(str(e["id"])) or {}).get("ids") if e.get("k") == "lab" else None
        if isinstance(ids, list) and e["id"] in hidden:
            hidden.update(i for i in ids if kinds.get(i) == "labitem")
    for e in events:
        ids = (details.get(str(e["id"])) or {}).get("ids") if e.get("k") == "lab" else None
        if isinstance(ids, list) and ids and all(i in hidden for i in ids):
            hidden.add(e["id"])
    off = [e for e in events if e["id"] in hidden and e.get("k") == "labitem"]
    if off:
        data[UNLISTED] = {"events": off, "details": {str(e["id"]): details[str(e["id"])] for e in off if str(e["id"]) in details}}
    gone = hidden | set(folded)
    if gone:
        for i in gone:
            details.pop(str(i), None)
        into = {}
        for i in folded:
            j = folded[i]
            while j in folded:
                j = folded[j]
            into[i] = j
        lists = [(k, d) for k, d in details.items() if isinstance(d, dict) and isinstance(d.get("ids"), list)]
        # (#972) a lab event merged into another is listed where that one is: each listed by one lab day, its own (or
        # the first to list one merged into it), and a lab day left listing nothing goes (as one whose are hidden, #950)
        lister = {}
        for k, d in lists:
            for i in d["ids"]:
                if i not in gone:
                    lister.setdefault(i, k)
        emptied, targets = set(), set(into.values())
        for k, d in lists:
            if any(i in gone or i in targets for i in d["ids"]):   # (one merged into: said for what it holds now)
                ids = []
                for i in d["ids"]:
                    i = into.get(i, i)
                    if i not in hidden and i not in ids and lister.setdefault(i, k) == k:
                        ids.append(i)
                if d["ids"] and not ids and d.get("type") == "labday":
                    emptied.add(int(k) if str(k).isdigit() else k)
                d["ids"] = ids
                days.add(k)
        for i in emptied:
            details.pop(str(i), None)
        gone |= emptied
        events = [e for e in events if e["id"] not in gone]
        costs = _fold_costs([c for c in costs if not (isinstance(c, dict) and c.get("id") in hidden)], folded)
    common.lab_days_counted(events, details, days)   # (#889) a lab day said for what it lists now
    if moved:
        events.sort(key=lambda e: str(e.get("t") or ""))
    data.update(events=events, details=details, costs=costs, edits=sorted(listed, key=lambda x: x["id"]))
    return data


def _fold_costs(costs, folded):
    """The Costs rows once merged events are folded in (#924): one row an event. A merged-in event's row moves to the
    event it went into (followed through a merge of that one) when that has none; when it has one, that's kept, the
    merged-in row filling in only the fields it lacks, as its detail does, so the claim's share is counted once."""
    def into(i):
        while i in folded:
            i = folded[i]
        return i
    out, own, drop = list(costs), {}, set()   # own: the place of each event's own row
    for n, c in enumerate(costs):
        if isinstance(c, dict) and c.get("id") not in folded:
            own.setdefault(c.get("id"), n)
    for n, c in enumerate(costs):
        if not (isinstance(c, dict) and c.get("id") in folded):
            continue
        to = into(c["id"])
        if to in own:
            have = out[own[to]]
            out[own[to]] = {**have, **{k: v for k, v in c.items() if k != "id" and have.get(k) in (None, "", [])}}
            drop.add(n)
        else:
            out[n], own[to] = {**c, "id": to}, n
    return [c for n, c in enumerate(out) if n not in drop]


ROWS_FROM = "rowsFrom"   # (#972) a result's details: [import, time] of the lab event each row came in, where not its own
FOLDED = "folded"        # (#972) an event: [[import, time]] of the lab events merged into it, whose rows it holds (and
#                          the series their results are in, records._row_series)


def rows_from(e, d):
    """(#972) Where each of a result's rows came in, (import, time): the lab event's own, else the one merged into it
    whose row it is (its details' ROWS_FROM). The drawer finds a row's result in that import on that day (drawer.js
    rowPoint, crossmatch._rows)."""
    rows = d.get("rows") if isinstance(d, dict) and isinstance(d.get("rows"), list) else []
    at = d.get(ROWS_FROM) if isinstance(d, dict) and isinstance(d.get(ROWS_FROM), list) else []
    return [tuple(at[r]) if r < len(at) and isinstance(at[r], list) and len(at[r]) == 2 else (e.get("imp"), e.get("t"))
            for r in range(len(rows))]


def _padded(x, n):
    x = list(x)[:n] if isinstance(x, list) else []
    return x + [None] * (n - len(x))


def _merge(first, second, details):
    counted = [x.get("abn") for x in (first, second)]
    if second.get("abn") and not first.get("abn"):
        first["abn"] = second["abn"]
    a, b = details.get(str(first["id"])), details.get(str(second["id"]))
    d = dict(a) if isinstance(a, dict) else {}
    if isinstance(b, dict):
        theirs = b.get("rows") if isinstance(b.get("rows"), list) else []
        for k, v in b.items():
            if (k not in d or d[k] in (None, "", [])) and not (theirs and k in ("rows", "keys", ROWS_FROM)):
                d[k] = v
        if isinstance(d.get("ids"), list) and isinstance(b.get("ids"), list):   # (#915) a lab day merged in: its list too
            d["ids"] = d["ids"] + [i for i in b["ids"] if i not in d["ids"]]
        if theirs:   # (#972) a lab event merged in: its rows too, each saying where it came in, so its result finds it
            mine = d.get("rows") if isinstance(d.get("rows"), list) else []
            if isinstance(d.get("keys"), list) or isinstance(b.get("keys"), list):
                d["keys"] = _padded(d.get("keys"), len(mine)) + _padded(b.get("keys"), len(theirs))
            d[ROWS_FROM] = _padded(d.get(ROWS_FROM), len(mine)) + [list(x) for x in rows_from(second, b)]
            d["rows"] = mine + theirs
            # and the event the times and imports of the lab events it holds rows of, for its lab day's badge
            # (crossmatch._draws, util.js labDraws): their results are its lab day's
            first[FOLDED] = [*(first.get(FOLDED) or []), [second.get("imp"), second.get("t")], *(second.get(FOLDED) or [])]
            if all(isinstance(n, int) and not isinstance(n, bool) for n in counted):   # its flagged rows, both's
                first["abn"] = counted[0] + counted[1]
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


def _holds_now(db, pid, event_id, e):
    """holds() of an event as stored, sorted, for an edit to keep (#937)."""
    if e.get("k") not in ("lab", "labitem"):
        return []

    def detail_of(i):
        r = db.execute("SELECT detail FROM events WHERE id=? AND profile_id=?", (i, pid)).fetchone()
        return _dec(r["detail"]) if r else None
    return sorted(holds(e.get("k"), detail_of(event_id), detail_of))


def add(db, pid, uid, action, event_id, title=None, t=None, other_id=None, k=None, batch=None):
    """Record an edit of the event with this id (as the page has it now). A second edit of the same kind to the same
    event replaces the first. `batch`: the id shared by the edits made at once to similar entries. Call inside a
    transaction. Returns (edit id, the event as imported)."""
    if action not in ACTIONS:
        raise EditError("Unknown kind of edit.")
    e = _event(db, pid, event_id)
    key, value, other = key_of(e), {"was": _was(e)}, None
    held = _holds_now(db, pid, event_id, e)
    if held:
        value["holds"] = held
    if batch:
        value["batch"] = batch
    if action == "kind":
        if k not in MOVABLE:
            raise EditError("Choose one of the categories an entry can move to.")
        if e.get("k") not in MOVABLE:
            raise EditError("This kind of entry can't move to another category.")
        value["k"] = k
    elif action == "rename":
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
        other_held = _holds_now(db, pid, other_id, o)
        if other_held:
            value["other_holds"] = other_held
    same = "SELECT id, value FROM edits WHERE profile_id=? AND target=? AND action=?" + (" AND other=?" if other else "")
    args = (pid, target_hash(key), action) + ((target_hash(other),) if other else ())
    mine = _held(value)   # (#937) another import's lab event of that day keeps its own edit

    def replaced(v):
        theirs = _held(_dec(v))
        return not mine or not theirs or bool(mine & theirs)
    gone = [r for r in db.execute(same, args).fetchall() if replaced(r[1])]
    for r in gone:   # (#954) a rename of a rename: what the first moved goes back when this one is undone
        if (_dec(r[1]) or {}).get("moved"):
            from . import medications
            value["moved"] = medications.merged(value.get("moved"), _dec(r[1])["moved"])
    db.executemany("DELETE FROM edits WHERE id=?", [(r[0],) for r in gone])
    eid = _insert(db, pid, uid, action, key, value, other)
    _bump(db, pid)
    return eid, e


def add_batch(db, pid, uid, action, event_id, also=(), title=None, k=None, rule=False):
    """A rename or a change of category made to the open event and, at once, to the similar entries chosen (`also`,
    ids from similar()), and, for a change of category with `rule`, kept for new imports from that source. One undo
    takes them all back. Call inside a transaction. Returns (edit ids, the open event as imported, the rule's id)."""
    if action not in ("rename", "kind"):
        raise EditError("Only a rename or a change of category can be made to similar entries.")
    if rule and action != "kind":
        raise EditError("Only a change of category can be kept for new imports.")
    also = [i for i in dict.fromkeys(int(i) for i in also or ()) if i != int(event_id)]
    if len(also) > SIMILAR_MAX:
        raise EditError(f"At most {SIMILAR_MAX} similar entries can be changed at once.")
    look = similar(db, pid, event_id)
    allowed = {x["id"] for x in look["items"]}
    if any(i not in allowed for i in also):
        raise EditError("Some of those entries aren't like this one any more; open it again to see them.")
    batch = secrets.token_hex(8) if (also or rule) else None
    first, e = add(db, pid, uid, action, event_id, title=title, k=k, batch=batch)
    ids = [first] + [add(db, pid, uid, action, i, title=title, k=k, batch=batch)[0] for i in also]
    rule_id = None
    if rule:
        shown = look["event"]
        rk = "rule:" + _h(look["source"], shown["k"], shown["title"])
        db.executemany("DELETE FROM edits WHERE id=?", [(r[0],) for r in db.execute(
            "SELECT id FROM edits WHERE profile_id=? AND target=? AND action=?", (pid, target_hash(rk), RULE)).fetchall()])
        rule_id = _insert(db, pid, uid, RULE, rk, {"k": k, "from": shown["k"], "title": shown["title"],
                                                   "source": look["source"], "label": look["label"], "batch": batch})
    return ids, e, rule_id


def similar(db, pid, event_id):
    """The entries that look like this one, as the page shows it: the same source, kind and title (letter case and
    spaces aside), not hidden. {"event": {k, title}, "source", "label", "items": [{id, t, title}]}."""
    from . import records
    data = records.assemble(db, pid) or {}
    events = data.get("events") or []
    e = next((x for x in events if x.get("id") == int(event_id)), None)
    if e is None:
        raise EditError("There's no such event in this profile's records.")
    imps = data.get("imports") or {}
    imp = imps.get(str(e.get("imp"))) or {}
    src, title = source_of(imp), str(e.get("title") or "").strip().casefold()
    items = [{"id": x["id"], "t": x.get("t"), "title": x.get("title")} for x in events
             if x is not e and x.get("k") == e.get("k") and str(x.get("title") or "").strip().casefold() == title
             and source_of(imps.get(str(x.get("imp"))) or {}) == src]
    return {"event": {"k": e.get("k"), "title": e.get("title")}, "source": src,
            "label": imp.get("name") or imp.get("label") or "", "items": items}


def moved(db, pid, edit_id, rec):
    """(#954) Keep with a rename what it moved of its medications' (medications.follow), for its undoing."""
    r = db.execute("SELECT value FROM edits WHERE id=? AND profile_id=?", (edit_id, pid)).fetchone()
    if r and rec:
        from . import medications
        value = _dec(r["value"]) or {}
        db.execute("UPDATE edits SET value=? WHERE id=?", (_enc({**value, "moved": medications.merged(value.get("moved"), rec)}), edit_id))


def remove(db, pid, edit_id):
    """Remove an edit (undo it), with the rest of its batch (the similar entries changed with it, and a rule made
    then). Returns what it was, {"action", "was", "n", "moved"} (n: edits removed; moved: what renames among them moved
    of their medications', #954, for medications.back), or None if there's no such edit."""
    r = db.execute("SELECT action, value FROM edits WHERE id=? AND profile_id=?", (edit_id, pid)).fetchone()
    if not r:
        return None
    value = _dec(r["value"]) or {}
    gone, recs = [edit_id], [value.get("moved")]
    if value.get("batch"):
        for x in db.execute("SELECT id, value FROM edits WHERE profile_id=? AND id<>?", (pid, edit_id)).fetchall():
            v = _dec(x["value"]) or {}
            if v.get("batch") == value["batch"]:
                gone.append(x["id"])
                recs.append(v.get("moved"))
    db.executemany("DELETE FROM edits WHERE id=?", [(i,) for i in gone])
    _bump(db, pid)
    was = value.get("was") or ({"k": value.get("from"), "title": value.get("title")} if r["action"] == RULE else None)
    out = {}
    for rec in recs:
        if rec:
            from . import medications
            out = medications.merged(out, rec)
    return {"action": r["action"], "was": was, "n": len(gone), "moved": out}


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
    no longer applies them (its milestones server/milestones.py moves into settings). Each
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
