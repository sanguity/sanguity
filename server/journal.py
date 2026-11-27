"""The journal (issue #49): what the person notes down themselves, beside the records. Stored in the journal table
(schema version 24, see db.py).

Four kinds of entry, each dated (a day, or a day and time):

  note         free text
  symptom      what it was (title), how bad on a 0-10 scale (severity), an optional note
  side_effect  the same, usually linked to a medication (med): one of the profile's medications, by name
  mood         (#161) a day's mood and/or energy, each on the same 0-10 scale (0 lowest, 10 best), an optional note;
               one a day, and only a day (no time)

And with #161, optionally:

  where        on a symptom or side effect: where on the body, as region ids of REGIONS (the front and back outline
               the page draws, www/js/journal-body.js, with the same ids)
  group        on a symptom or side effect: the name of the symptom group it was logged with (check_group). Groups
               are the person's own named sets of symptoms ("Migraine day": headache, nausea...), the profile setting
               journal.groups (check_groups); an entry keeps the name it was logged with when the group changes
  photos       on a note, symptom or side effect: [{"id", "alt"}], pictures sent first and kept by journal_photos.py,
               each with what it shows in the person's words (alt)

An entry is written by a person, not imported: it belongs to the profile, so undoing or replacing an import leaves
it, and it goes with the profile when the profile is deleted. Everything an entry says is encrypted in body
(crypto.enc); who wrote it and when are plain. The records the page loads (records.assemble) list the entries as
"journal", so a change bumps profiles.records_rev like any change to the records. An export carries them in its
data.json (and the photos in journal/photos/), and a backup has them with the rest of the database and the profile's
folder.

A share link (#47) leaves the journal out unless the person sharing ticks it in: these are the person's own words,
not their records; and its photos unless "Photos in the journal" is ticked too (#161).
"""
import json, re

from . import crypto, journal_photos as photos_store

KINDS = ("note", "symptom", "side_effect", "mood")
LABEL = {"note": "Note", "symptom": "Symptom", "side_effect": "Side effect", "mood": "Mood"}
RATED = ("symptom", "side_effect")
TITLE_MAX, NOTE_MAX, MED_MAX, GROUP_MAX, WHERE_MAX = 200, 5000, 300, 80, 12
GROUPS_MAX, GROUP_SYMPTOMS_MAX = 20, 12
MED_KINDS = ("med", "rx")   # the events that are medications (the page's "Medications" lane)
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?$")
# (#161) where on the body: the regions of the front and back outline (www/js/journal-body.js draws them, with these
# ids, in this order). "Right" and "left" are the person's own.
REGIONS = {
    "head": "Head", "neck": "Neck", "chest": "Chest", "upper-abdomen": "Upper belly", "lower-abdomen": "Lower belly",
    "groin": "Hips and groin",
    "back-head": "Back of the head", "back-neck": "Back of the neck", "upper-back": "Upper back", "lower-back": "Lower back",
    **{f"{s}-{k}": v.format(side=side, Side=side.title()) for s, side in (("r", "right"), ("l", "left")) for k, v in (
        ("shoulder", "{Side} shoulder"), ("upper-arm", "{Side} upper arm"), ("forearm", "{Side} forearm"), ("hand", "{Side} hand"),
        ("thigh", "Front of the {side} thigh"), ("knee", "{Side} knee"), ("shin", "{Side} shin"), ("foot", "{Side} foot"),
        ("buttock", "{Side} buttock"), ("shoulder-back", "Back of the {side} shoulder"),
        ("upper-arm-back", "Back of the {side} upper arm"), ("forearm-back", "Back of the {side} forearm and elbow"),
        ("hand-back", "Back of the {side} hand"), ("thigh-back", "Back of the {side} thigh"),
        ("knee-back", "Back of the {side} knee"), ("calf", "{Side} calf"), ("heel", "{Side} heel and ankle"))},
}


class JournalError(ValueError):
    """Something about an entry that can't be saved, said for the person who typed it."""


def _enc(obj):
    return crypto.enc(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))


def _text(v, limit, what):
    if v is None:
        return ""
    if not isinstance(v, str):
        raise JournalError(f"The {what} must be text.")
    v = v.strip()
    if len(v) > limit:
        raise JournalError(f"The {what} is too long (at most {limit} characters).")
    return v


def medications(db, pid):
    """The names of the profile's medications, as the page shows them (edits applied, those hidden by hand left out,
    #1093), sorted."""
    from . import medications as meds, records
    body = records.served(db, pid)
    if not body:
        return []
    data = json.loads(body)
    events, hid = data.get("events") or [], meds.hidden_test(data)
    return sorted({e["title"] for e in events if isinstance(e, dict) and e.get("k") in MED_KINDS
                   and isinstance(e.get("title"), str) and e["title"].strip() and not hid(e["title"])}
                  | {m["name"] for m in data.get("medications") or [] if m.get("own")}, key=str.lower)   # its own too (#1091)


def _when(t, day_only=False):
    t = _text(t, 16, "date")
    if not _TIME.match(t):
        raise JournalError("Give the date as YYYY-MM-DD, with a time as YYYY-MM-DDTHH:MM if you like.")
    try:
        from datetime import datetime
        datetime.strptime(t, "%Y-%m-%dT%H:%M" if "T" in t else "%Y-%m-%d")
    except ValueError:
        raise JournalError("That date doesn't exist.") from None
    from datetime import date, timedelta
    if t[:10] > (date.today() + timedelta(days=366)).isoformat():   # a year typed wrong (2207): it'd be listed under no period (#479)
        raise JournalError("That date is more than a year ahead: check the year.")
    if day_only and "T" in t:
        raise JournalError("A mood entry is for a whole day: give the date without a time.")
    return t


def _scale(v, what):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 10:
        raise JournalError(f"{what} is a whole number from 0 to 10.")
    return v


def _where(v):
    """Body regions, by id, once each in REGIONS' order; [] for none."""
    if v in (None, []):
        return []
    if not isinstance(v, list) or any(not isinstance(x, str) or x not in REGIONS for x in v):
        raise JournalError("Pick where on the body from the outline or the list.")
    if len(set(v)) > WHERE_MAX:
        raise JournalError(f"Pick at most {WHERE_MAX} places on the body.")
    return [k for k in REGIONS if k in v]


def check(db, pid, e, keep_med=None, jid=None):
    """The entry as stored, or JournalError (or journal_photos.PhotoError) saying what's wrong with it. e: what was
    sent, {kind, t, title, severity, note, med, where, group, photos, mood, energy}. keep_med: the medication the
    entry is linked to already, accepted even when it's no longer in the list (the import that listed it was undone).
    jid: the entry being changed (its own photos, and its own day's mood, are fine)."""
    kind = e.get("kind")
    if kind not in KINDS:
        raise JournalError("An entry is a note, a symptom, a side effect or a mood.")
    t = _when(e.get("t"), kind == "mood")
    title, note, med = _text(e.get("title"), TITLE_MAX, "title"), _text(e.get("note"), NOTE_MAX, "note"), _text(e.get("med"), MED_MAX, "medication")
    group = " ".join(_text(e.get("group"), GROUP_MAX, "group's name").split())
    where = _where(e.get("where"))
    if kind not in RATED and (where or group):
        raise JournalError("Only a symptom or side effect says where on the body, or belongs to a group.")
    if kind == "mood":
        mood, energy = _scale(e.get("mood"), "Mood"), _scale(e.get("energy"), "Energy")
        if mood is None and energy is None:
            raise JournalError("Give a mood, an energy level, or both.")
        if title or med or e.get("severity") is not None or e.get("photos"):
            raise JournalError("A mood entry has a mood, an energy level and a note, nothing else.")
        day = [x for x in listing(db, pid) if x["kind"] == "mood" and x["t"] == t]
        # a mood changed on its own day is fine, even beside a second one two saves at once left before #925
        if any(x["id"] != jid for x in day) and not any(x["id"] == jid for x in day):
            raise JournalError("There's a mood entry for that day already: change that one instead.")
        return {"kind": kind, "t": t, "mood": mood, "energy": energy, "note": note}
    if e.get("mood") is not None or e.get("energy") is not None:
        raise JournalError("Only a mood entry has a mood or energy level.")
    photos = photos_store.check(db, pid, e.get("photos"), jid)
    extra = {"photos": photos} if photos else {}
    if kind == "note":
        if not note:
            raise JournalError("Write something in the note.")
        if e.get("severity") is not None or med:
            raise JournalError("A note has no severity or medication; make it a symptom or side effect instead.")
        return {"kind": kind, "t": t, "title": title, "note": note, **extra}
    if not title:
        raise JournalError(f"Say what the {LABEL[kind].lower()} was.")
    severity = e.get("severity")
    if isinstance(severity, bool) or not isinstance(severity, int) or not 0 <= severity <= 10:
        raise JournalError("Severity is a whole number from 0 (none) to 10 (worst).")
    if med and med != keep_med and med not in medications(db, pid):
        raise JournalError("Pick the medication from this profile's medication list.")
    if where:
        extra["where"] = where
    if group:
        extra["group"] = group
    return {"kind": kind, "t": t, "title": title, "severity": severity, "note": note, "med": med or None, **extra}


def check_group(db, pid, e):
    """A symptom group logged in one go (#161): e is {group, t, note, items: [{title, severity, where}]}. Returns the
    entries to add, each a symptom of its own with its own severity (and place), or JournalError."""
    items = e.get("items")
    if not isinstance(items, list) or not items:
        raise JournalError("Give at least one symptom of the group a severity.")
    if len(items) > GROUP_SYMPTOMS_MAX:
        raise JournalError(f"A group can have at most {GROUP_SYMPTOMS_MAX} symptoms.")
    group = e.get("group")
    if not isinstance(group, str) or not group.strip():
        raise JournalError("Say which group this is.")
    out = []
    for i in items:
        if not isinstance(i, dict):
            raise JournalError("Each symptom of the group has a name and a severity.")
        out.append(check(db, pid, {"kind": "symptom", "t": e.get("t"), "note": e.get("note"), "group": group,
                                   "title": i.get("title"), "severity": i.get("severity"), "where": i.get("where")}))
    return out


def check_groups(v):
    """The journal.groups setting (#161): [{"name", "symptoms": [titles]}], each name once (in any letter case)."""
    if not isinstance(v, list):
        raise JournalError("The symptom groups must be a list.")
    if len(v) > GROUPS_MAX:
        raise JournalError(f"At most {GROUPS_MAX} symptom groups.")
    out, names = [], set()
    for g in v:
        if not isinstance(g, dict):
            raise JournalError("Each symptom group has a name and its symptoms.")
        name = " ".join(_text(g.get("name"), GROUP_MAX, "group's name").split())
        if not name:
            raise JournalError("Give the group a name.")
        if name.casefold() in names:
            raise JournalError(f"There's a group called “{name}” already.")
        names.add(name.casefold())
        syms = g.get("symptoms")
        if not isinstance(syms, list):
            raise JournalError(f"List the symptoms of “{name}”.")
        clean = []
        for x in syms:
            x = " ".join(_text(x, TITLE_MAX, "symptom").split())
            if x and x.casefold() not in {c.casefold() for c in clean}:
                clean.append(x)
        if not clean:
            raise JournalError(f"Give “{name}” at least one symptom.")
        if len(clean) > GROUP_SYMPTOMS_MAX:
            raise JournalError(f"A group can have at most {GROUP_SYMPTOMS_MAX} symptoms.")
        out.append({"name": name, "symptoms": clean})
    return out


def add(db, pid, who, entry):
    """Store a checked entry (check()). who: the signed-in account. Call inside a transaction. Returns its id."""
    from . import db as dbm, records
    jid = db.execute("INSERT INTO journal(profile_id, body, created_by, created_name, created) VALUES(?, ?, ?, ?, ?)",
                     (pid, _enc(entry), who["id"], who["username"], dbm.now())).lastrowid
    photos_store.attach(db, pid, jid, entry.get("photos") or [])
    records._bump(db, pid)
    return jid


def get(db, pid, jid):
    r = db.execute("SELECT body FROM journal WHERE id=? AND profile_id=?", (jid, pid)).fetchone()
    return json.loads(crypto.dec(r["body"])) if r else None


def change(db, pid, jid, who, entry):
    """Replace an entry with a checked one. Returns what it was, or None when there's no such entry."""
    from . import db as dbm, records
    was = get(db, pid, jid)
    if was is None:
        return None
    db.execute("UPDATE journal SET body=?, changed_by=?, changed_name=?, changed=? WHERE id=? AND profile_id=?",
               (_enc(entry), who["id"], who["username"], dbm.now(), jid, pid))
    photos_store.attach(db, pid, jid, entry.get("photos") or [])
    records._bump(db, pid)
    return was


def remove(db, pid, jid):
    """Delete an entry, with its photos. Returns what it was, or None when there's no such entry."""
    from . import records
    was = get(db, pid, jid)
    if was is not None:
        photos_store.drop_entry(db, pid, jid)
        db.execute("DELETE FROM journal WHERE id=? AND profile_id=?", (jid, pid))
        records._bump(db, pid)
    return was


def listing(db, pid, ids=None):
    """The profile's entries, oldest first: {id, kind, t, title, severity, note, med, by, at, changed, changed_by}, and
    (#161) where, group, photos [{id, alt, type, bytes}], mood and energy when they have them. ids: only those entries
    (a change's reply carries the entries it changed, #271)."""
    out, pics = [], None
    only = "" if ids is None else f" AND id IN ({','.join('?' * len(ids))})"
    for r in db.execute(f"""SELECT id, body, created_name, created, changed_name, changed FROM journal
                           WHERE profile_id=?{only} ORDER BY id""", (pid, *(ids or ()))):
        e = json.loads(crypto.dec(r["body"]))
        row = {"id": r["id"], "kind": e.get("kind"), "t": e.get("t"), "title": e.get("title") or "",
               "severity": e.get("severity"), "note": e.get("note") or "", "med": e.get("med"),
               "by": r["created_name"], "at": r["created"], "changed": r["changed"], "changed_by": r["changed_name"]}
        if e.get("kind") == "mood":
            row.update(mood=e.get("mood"), energy=e.get("energy"))
        if e.get("where"):
            row["where"] = e["where"]
        if e.get("group"):
            row["group"] = e["group"]
        if e.get("photos"):
            pics = photos_store.info(db, pid) if pics is None else pics
            row["photos"] = [{**p, **pics[p["id"]]} for p in e["photos"] if p.get("id") in pics]
        out.append(row)
    out.sort(key=lambda e: (str(e["t"] or ""), e["id"]))
    return out


def log_detail(action, entry):
    """What the activity log keeps: the kind of entry and what was done, never what it says."""
    return {"action": action, "kind": entry.get("kind")}
