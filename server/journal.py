"""The journal (issue #49): what the person notes down themselves, beside the records. Stored in the journal table
(schema version 24, see db.py).

Three kinds of entry, each dated (a day, or a day and time):

  note         free text
  symptom      what it was (title), how bad on a 0-10 scale (severity), an optional note
  side_effect  the same, usually linked to a medication (med): one of the profile's medications, by name

An entry is written by a person, not imported: it belongs to the profile, so undoing or replacing an import leaves
it, and it goes with the profile when the profile is deleted. Everything an entry says is encrypted in body
(crypto.enc); who wrote it and when are plain. The records the page loads (records.assemble) list the entries as
"journal", so a change bumps profiles.records_rev like any change to the records. An export carries them in its
data.json, and a backup has them with the rest of the database.

Share links (#47) don't exist yet. When they do, a link should leave the journal out unless the person sharing
ticks it in: these are the person's own words, not their records.
"""
import json, re

from . import crypto

KINDS = ("note", "symptom", "side_effect")
LABEL = {"note": "Note", "symptom": "Symptom", "side_effect": "Side effect"}
TITLE_MAX, NOTE_MAX, MED_MAX = 200, 5000, 300
MED_KINDS = ("med", "rx")   # the events that are medications (the page's "Medications" lane)
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?$")


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
    """The names of the profile's medications, as the page shows them (edits applied), sorted."""
    from . import records
    body = records.served(db, pid)
    if not body:
        return []
    events = json.loads(body).get("events") or []
    return sorted({e["title"] for e in events if isinstance(e, dict) and e.get("k") in MED_KINDS
                   and isinstance(e.get("title"), str) and e["title"].strip()}, key=str.lower)


def check(db, pid, kind, t, title=None, severity=None, note=None, med=None, keep_med=None):
    """The entry as stored, or JournalError saying what's wrong with it. keep_med: the medication the entry is
    linked to already, accepted even when it's no longer in the list (the import that listed it was undone)."""
    if kind not in KINDS:
        raise JournalError("An entry is a note, a symptom or a side effect.")
    t = _text(t, 16, "date")
    if not _TIME.match(t):
        raise JournalError("Give the date as YYYY-MM-DD, with a time as YYYY-MM-DDTHH:MM if you like.")
    try:
        from datetime import datetime
        datetime.strptime(t, "%Y-%m-%dT%H:%M" if "T" in t else "%Y-%m-%d")
    except ValueError:
        raise JournalError("That date doesn't exist.") from None
    title, note, med = _text(title, TITLE_MAX, "title"), _text(note, NOTE_MAX, "note"), _text(med, MED_MAX, "medication")
    if kind == "note":
        if not note:
            raise JournalError("Write something in the note.")
        if severity is not None or med:
            raise JournalError("A note has no severity or medication; make it a symptom or side effect instead.")
        return {"kind": kind, "t": t, "title": title, "note": note}
    if not title:
        raise JournalError(f"Say what the {LABEL[kind].lower()} was.")
    if isinstance(severity, bool) or not isinstance(severity, int) or not 0 <= severity <= 10:
        raise JournalError("Severity is a whole number from 0 (none) to 10 (worst).")
    if med and med != keep_med and med not in medications(db, pid):
        raise JournalError("Pick the medication from this profile's medication list.")
    return {"kind": kind, "t": t, "title": title, "severity": severity, "note": note, "med": med or None}


def add(db, pid, who, entry):
    """Store a checked entry (check()). who: the signed-in account. Call inside a transaction. Returns its id."""
    from . import db as dbm, records
    jid = db.execute("INSERT INTO journal(profile_id, body, created_by, created_name, created) VALUES(?, ?, ?, ?, ?)",
                     (pid, _enc(entry), who["id"], who["username"], dbm.now())).lastrowid
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
    records._bump(db, pid)
    return was


def remove(db, pid, jid):
    """Delete an entry. Returns what it was, or None when there's no such entry."""
    from . import records
    was = get(db, pid, jid)
    if was is not None:
        db.execute("DELETE FROM journal WHERE id=? AND profile_id=?", (jid, pid))
        records._bump(db, pid)
    return was


def listing(db, pid):
    """The profile's entries, oldest first: {id, kind, t, title, severity, note, med, by, at, changed, changed_by}."""
    out = []
    for r in db.execute("""SELECT id, body, created_name, created, changed_name, changed FROM journal
                           WHERE profile_id=? ORDER BY id""", (pid,)):
        e = json.loads(crypto.dec(r["body"]))
        out.append({"id": r["id"], "kind": e.get("kind"), "t": e.get("t"), "title": e.get("title") or "",
                    "severity": e.get("severity"), "note": e.get("note") or "", "med": e.get("med"),
                    "by": r["created_name"], "at": r["created"], "changed": r["changed"], "changed_by": r["changed_name"]})
    out.sort(key=lambda e: (str(e["t"] or ""), e["id"]))
    return out


def log_detail(action, entry):
    """What the activity log keeps: the kind of entry and what was done, never what it says."""
    return {"action": action, "kind": entry.get("kind")}
