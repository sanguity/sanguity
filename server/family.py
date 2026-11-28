"""Family history (#1134): relatives and the conditions they had, read from the records or typed in.

The records' come from importers as entries of `familyHistory` (importers/common.family_member: FHIR's
FamilyMemberHistory, a C-CDA's Family History section), kept in each import's meta like the problem list, so a
re-import or a later pull finds them by their source key and adds no copy. One entry is one relative as one record
says it: {"relation", "code", "name", "conditions": [{"name", "code", "system", "onset", "death", "note"}],
"deceased", "deathAge", "note", "date", "src", "key"}.

A profile's own entries, and its hiding of the records', are rows of family_entries (schema version 53; the API in
routes_family.py), each body encrypted:
  an own entry  {key: "own:" and a random word, own: true, relation, name, conditions: [{name, onset}], deceased
                (true, false, or absent: not said), deathAge, note}: the person's own, changed or removed by owners and
                editors; its key kept, so its removal's Undo puts the same one back (add_own(key=...))
  a hiding      {key: an entry of the records' source key, hidden: true}: left out of DATA.familyHistory, the summary
                and the FHIR export, its records kept, and listed in DATA.hiddenFamily to show again. The records' are
                never edited here: they say what the record says.
records._assemble adds both (assembled()): DATA.familyHistory, the records' entries not hidden and the profile's own
("own": its row's id, by, at), each with its "key"; the page groups them by relative (relative()), as the summary does.
Facts only: nothing here works out a risk or reads anything into them.
"""
import hashlib, json, re, secrets

OWN = "own:"
_OWN_KEY = re.compile(r"^own:[0-9a-f]{8,32}$")
MAX = {"relation": 100, "name": 100, "condition": 200, "onset": 40, "deathAge": 40, "note": 2000}
MAX_CONDITIONS = 30


def is_own(key):
    return isinstance(key, str) and key.startswith(OWN)


class EntryError(ValueError):
    """Something about a family history entry that can't be saved, said for the person who made it."""


def _enc(obj):
    from . import crypto
    return crypto.enc(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))


def _line(v, k, what):
    if v is None:
        return ""
    if not isinstance(v, str):
        raise EntryError(f"The {what} must be text.")
    v = v.strip() if k == "note" else " ".join(v.split())
    if len(v) > MAX[k]:
        raise EntryError(f"The {what} is too long (at most {MAX[k]} characters).")
    return v


def check(fields):
    """An own entry's fields as kept, or EntryError. fields: {relation, name, conditions: [{name, onset}], status:
    "living", "deceased" or "" (not said), deathAge, note}."""
    out = {"relation": _line(fields.get("relation"), "relation", "relative")}
    if not out["relation"]:
        raise EntryError("Say who the relative is (mother, brother…).")
    if (name := _line(fields.get("name"), "name", "name")):
        out["name"] = name
    conds = fields.get("conditions") or []
    if not isinstance(conds, list) or len(conds) > MAX_CONDITIONS:
        raise EntryError(f"Give at most {MAX_CONDITIONS} conditions.")
    out["conditions"] = []
    for c in conds:
        if not isinstance(c, dict):
            raise EntryError("A condition must have a name.")
        n, onset = _line(c.get("name"), "condition", "condition"), _line(c.get("onset"), "onset", "onset age")
        if not n and not onset:
            continue
        if not n:
            raise EntryError("Give the condition's name, or leave its onset age empty.")
        out["conditions"].append({"name": n, **({"onset": onset} if onset else {})})
    status = fields.get("status") or ""
    if status not in ("", "living", "deceased"):
        raise EntryError("Say living, deceased, or neither.")
    if status:
        out["deceased"] = status == "deceased"
    if (age := _line(fields.get("deathAge"), "deathAge", "age at death")):
        if status != "deceased":
            raise EntryError("An age at death needs Deceased.")
        out["deathAge"] = age
    if (note := _line(fields.get("note"), "note", "note")):
        out["note"] = note
    return out


def entry_key(x):
    """An entry of the records' key: its source key, else a hash of what it says (an entry from a data.json of no key)."""
    k = x.get("key")
    if isinstance(k, str) and k.strip():
        return k
    return "c:" + hashlib.sha256(json.dumps({a: b for a, b in x.items() if a not in ("ver", "imp")}, sort_keys=True,
                                            ensure_ascii=False).encode()).hexdigest()[:16]


def relative(x):
    """The relative an entry is of, to group by: its relation and name, in lower case."""
    return (" ".join(str(x.get("relation") or "").split()).lower(), " ".join(str(x.get("name") or "").split()).lower())


def _rows(db, pid):
    from . import crypto
    return [(r, json.loads(crypto.dec(r["body"])))
            for r in db.execute("""SELECT id, body, created_name, created, changed_name, changed FROM family_entries
                                   WHERE profile_id=? ORDER BY id""", (pid,)).fetchall()]


def listing(db, pid):
    """The profile's own entries and hidings, oldest first: {id, key, own, the body's fields, by, at, changed,
    changed_by} (an export's "familyEntries")."""
    return [{"id": r["id"], **b, "own": bool(b.get("own")), "by": r["created_name"], "at": r["created"],
             "changed": r["changed"], "changed_by": r["changed_name"]} for r, b in _rows(db, pid)]


def get(db, pid, eid):
    from . import crypto
    r = db.execute("SELECT body FROM family_entries WHERE id=? AND profile_id=?", (eid, pid)).fetchone()
    return json.loads(crypto.dec(r["body"])) if r else None


OWN_FIELDS = ("relation", "name", "conditions", "deceased", "deathAge", "note")


def with_entries(entries, rows):
    """(DATA.familyHistory, DATA.hiddenFamily): the records' entries (one per key, the last import's), those hidden
    left out and listed apart ({key, relation, name, id}), and the profile's own added ("own": {id, by, at, changed,
    changed_by}, "src": "Typed in") after them; the records' in their own order. rows: listing()'s."""
    by_key = {}
    for x in entries or []:
        if isinstance(x, dict) and isinstance(x.get("conditions", []), list):
            by_key[entry_key(x)] = {**x, "key": entry_key(x)}
    hide = {r["key"]: r["id"] for r in rows if not r["own"] and r.get("hidden")}
    shown, hidden = [], []
    for k, x in by_key.items():
        if k in hide:
            hidden.append({"key": k, "relation": x.get("relation") or "Relative", "id": hide[k],
                           **({"name": x["name"]} if x.get("name") else {})})
        else:
            shown.append(x)
    for r in rows:
        if r["own"]:
            shown.append({**{k: r[k] for k in OWN_FIELDS if k in r}, "key": r["key"], "src": "Typed in",
                          "own": {k: r.get(k) for k in ("id", "by", "at", "changed", "changed_by")}})
    hidden.sort(key=relative)
    return shown, hidden


def assembled(db, pid, data):
    """(DATA.familyHistory, DATA.hiddenFamily) for records._assemble."""
    return with_entries(data.get("familyHistory"), listing(db, pid))


def record_keys(db, pid):
    """The keys of the records' own entries (hidden or not)."""
    from . import records
    data = records.assemble(db, pid, raw=True) or {}
    return {entry_key(x) for x in data.get("familyHistory") or [] if isinstance(x, dict)}


def _save(db, pid, who, body, eid=None):
    from . import db as dbm, records
    if eid is None:
        eid = db.execute("INSERT INTO family_entries(profile_id, body, created_by, created_name, created) VALUES(?, ?, ?, ?, ?)",
                         (pid, _enc(body), who["id"], who["username"], dbm.now())).lastrowid
    else:
        db.execute("UPDATE family_entries SET body=?, changed_by=?, changed_name=?, changed=? WHERE id=? AND profile_id=?",
                   (_enc(body), who["id"], who["username"], dbm.now(), eid, pid))
    records._bump(db, pid)
    return eid


def add_own(db, pid, who, fields, key=None):
    """A new own entry (check()'d fields). key: the one it had, its removal undone. Inside a transaction. -> its id"""
    if key is not None:
        if not (isinstance(key, str) and _OWN_KEY.match(key)):
            raise EntryError("That isn't an entry of your own to put back.")
        if any(b.get("key") == key for _, b in _rows(db, pid)):
            raise EntryError("That entry is here already.")
    return _save(db, pid, who, {"key": key or OWN + secrets.token_hex(8), "own": True, **fields})


def change_own(db, pid, who, eid, fields):
    """An own entry's fields replaced. -> what it was, or None"""
    was = get(db, pid, eid)
    if was is None or not was.get("own"):
        return None
    _save(db, pid, who, {"key": was["key"], "own": True, **fields}, eid)
    return was


def remove_own(db, pid, eid):
    """An own entry deleted. -> what it was (its key with it), or None"""
    from . import records
    was = get(db, pid, eid)
    if was is None or not was.get("own"):
        return None
    db.execute("DELETE FROM family_entries WHERE id=? AND profile_id=?", (eid, pid))
    records._bump(db, pid)
    return was


def set_hidden(db, pid, who, key, hidden):
    """An entry of the records' hidden, or shown again. -> the hiding's id, or None once shown"""
    from . import records
    eid = next((r["id"] for r, b in _rows(db, pid) if not b.get("own") and b.get("key") == key), None)
    if hidden:
        return eid if eid is not None else _save(db, pid, who, {"key": key, "hidden": True})
    if eid is not None:
        db.execute("DELETE FROM family_entries WHERE id=? AND profile_id=?", (eid, pid))
        records._bump(db, pid)
    return None


def restore_body(x):
    """(export.py) an export's familyEntries row as family_entries keeps it, or ValueError."""
    if not isinstance(x, dict) or not isinstance(x.get("key"), str) or not x["key"].strip():
        raise ValueError("it has no key")
    if x.get("own"):
        if not _OWN_KEY.match(x["key"]):
            raise ValueError("its key isn't one of an entry of your own")
        st = "" if "deceased" not in x else "deceased" if x["deceased"] else "living"
        return {"key": x["key"], "own": True, **check({**{k: x.get(k) for k in ("relation", "name", "deathAge", "note")},
                                                         "conditions": x.get("conditions") or [], "status": st})}
    if not x.get("hidden"):
        raise ValueError("it changes nothing")
    return {"key": x["key"][:4000], "hidden": True}


def summary_rows(data):
    """The visit summary's Family history (#1134): one row per relative, the entries of each put together, as the page
    groups them: {"relative": "Mother (Ann)", "conditions": "Type 2 diabetes mellitus (onset age 52); …", "status":
    "Deceased, age 78" | "Deceased" | "Living" | "", "source"}."""
    groups = {}
    for x in data.get("familyHistory") or []:
        if isinstance(x, dict):
            groups.setdefault(relative(x), []).append(x)
    rows = []
    for xs in groups.values():
        first = xs[0]
        conds, seen = [], set()
        for x in xs:
            for c in x.get("conditions") or []:
                if not isinstance(c, dict) or not c.get("name"):
                    continue
                said = c["name"] + "".join(f" ({w})" for w in [f"onset age {c['onset']}" if c.get("onset") else "",
                                                               "contributed to death" if c.get("death") else ""] if w)
                if said.lower() not in seen:
                    seen.add(said.lower())
                    conds.append(said)
        dead = next((x["deceased"] for x in xs if isinstance(x.get("deceased"), bool)), None)
        age = next((x["deathAge"] for x in xs if x.get("deathAge")), "")
        rel = first.get("relation") or "Relative"
        rows.append({"relative": f"{rel} ({first['name']})" if first.get("name") else rel,
                     "conditions": "; ".join(conds), "status": f"Deceased, age {age}" if age else "Deceased" if dead
                     else "Living" if dead is False else "",
                     "source": ", ".join(dict.fromkeys(str(x.get("src") or "") for x in xs if x.get("src")))})
    return rows


def log_detail(change):
    """What the activity log keeps: what was done, never the relative or the condition."""
    return {"change": change}
