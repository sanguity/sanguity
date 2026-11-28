"""Follow-ups from a visit (#1138): the person's own list of things to do, like "book the MRI" or "repeat bloodwork
in 3 months", each with an optional date. Like the medication log, it records what the person was told; Sanguity
never decides what's due, and the page words it as theirs ("You noted: repeat bloodwork by Jun 3", "date passed",
never "overdue").

A follow-up has its text; a date, given as one or as "in N days, weeks or months" from the visit it's about (else from
the day it was added, in the person's time zone), months being calendar months (calendar.add_months); the record it's
about ({id, title, t}: the event's id, and its title and day as they were, so it still reads once that record is gone
or comes back under another id from an export); whether it's done; and whether the calendar feed shows its words. The
feed lists the ones not done that have a date, each as an all-day event: "Sanguity follow-up" with nothing more
unless the person ticked Show its words for that one (the owner's decision: calendar apps keep the feed on their
servers), whatever the feed's own detail setting.

One row each in followups (schema version 56, db.py), its body encrypted; who added or changed it and when are plain, as the journal's
are. The records list them as "followups" (records._assemble); Export everything carries them, and loading an export
puts them back (export.restore).
"""
import json, re
from datetime import date

from . import calendar, crypto, db as dbm

TEXT_MAX, TITLE_MAX, N_MAX = 300, 200, 600
GENERIC = "Sanguity follow-up"
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SAID = re.compile(r"in \d{1,3} (?:day|week|month)s?")


class FollowUpError(ValueError):
    """Something about a follow-up that can't be saved, said for the person who made it."""


def _enc(obj):
    return crypto.enc(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))


def _day(v, what):
    if v in (None, ""):
        return None
    if not isinstance(v, str) or not _DAY.match(v):
        raise FollowUpError(f"Give the {what} as YYYY-MM-DD.")
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise FollowUpError(f"The {what} doesn't exist.") from None


def check(f, today, was=None):
    """The body to keep from the fields given ({text, due, in_n, in_unit, from, about, show, done}), or FollowUpError.
    today: the person's today (date), what "in N weeks" counts from without a visit. was: the body before (a change),
    whose "in N" wording is kept while its date is."""
    text = " ".join(str(f.get("text") or "").split()) if isinstance(f.get("text") or "", str) else None
    if not text:
        raise FollowUpError("Say what the follow-up is.")
    if len(text) > TEXT_MAX:
        raise FollowUpError(f"The follow-up is too long (at most {TEXT_MAX} characters).")
    body = {"text": text, "show": bool(f.get("show")), "done": bool(f.get("done"))}
    about = f.get("about")
    if about:
        if not isinstance(about, dict) or not isinstance(about.get("title") or "", str):
            raise FollowUpError("That isn't a record to link it to.")
        a = {"title": " ".join((about.get("title") or "").split())[:TITLE_MAX]}
        if isinstance(about.get("id"), int) and not isinstance(about.get("id"), bool):
            a["id"] = about["id"]
        if (t := _day(str(about.get("t") or "")[:10] or None, "record's date")):
            a["t"] = t.isoformat()
        body["about"] = a
    n = f.get("in_n")
    if n not in (None, ""):
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= N_MAX or f.get("in_unit") not in calendar.UNITS:
            raise FollowUpError(f"Say how long from the visit as 1 to {N_MAX} days, weeks or months.")
        start = _day(f.get("from"), "date it counts from") or (date.fromisoformat(body["about"]["t"]) if body.get("about", {}).get("t") else today)
        unit = f["in_unit"] if n != 1 else f["in_unit"][:-1]
        body.update(due=calendar.add(start, n, f["in_unit"]).isoformat(), said=f"in {n} {unit}", **{"from": start.isoformat()})
    elif (due := _day(f.get("due"), "date")):
        body["due"] = due.isoformat()
        # its "in N" wording, while the date is still the one it gave: the one before (a change), or given (an Undo, an export)
        said = was if was and was.get("due") == body["due"] and was.get("said") else f
        if isinstance(said.get("said"), str) and _SAID.fullmatch(said["said"]) and _day(said.get("from"), "date it counts from"):
            body.update(said=said["said"], **{"from": said["from"]})
    if body["done"]:
        body["done_at"] = (was or {}).get("done_at") if (was or {}).get("done") else dbm.now()
    return body


def from_export(x):
    """A follow-up an export lists (listing()'s), as kept here, or ValueError: checked as one typed in, its "in N"
    wording and done day kept, its record's id left out (the records loaded with it have ids of their own: it's found
    by its title and day)."""
    if not isinstance(x, dict):
        raise ValueError("it isn't an object")
    about = x.get("about") if isinstance(x.get("about"), dict) else None
    body = check({**{k: x.get(k) for k in ("text", "due", "said", "from", "show", "done")},
                  "about": about and {k: about.get(k) for k in ("title", "t")}}, date.today())
    if body["done"] and isinstance(x.get("done_at"), int) and not isinstance(x.get("done_at"), bool):
        body["done_at"] = x["done_at"]
    return body


def _rows(db, pid, ids=None):
    q = "SELECT id, body, created_name, created, changed_name, changed FROM followups WHERE profile_id=?"
    return [(r, json.loads(crypto.dec(r["body"]))) for r in db.execute(q + " ORDER BY id", (pid,)).fetchall()
            if ids is None or r["id"] in ids]


def listing(db, pid, ids=None):
    """The profile's follow-ups, oldest first: {id, text, due?, said?, from?, about?, show, done, done_at?, by, at,
    changed, changed_by} (the records' "followups", an export's too)."""
    return [{"id": r["id"], **b, "by": r["created_name"], "at": r["created"], "changed": r["changed"],
             "changed_by": r["changed_name"]} for r, b in _rows(db, pid, ids)]


def get(db, pid, fid):
    r = db.execute("SELECT body FROM followups WHERE id=? AND profile_id=?", (fid, pid)).fetchone()
    return json.loads(crypto.dec(r["body"])) if r else None


def add(db, pid, who, body, at=None):
    from . import records
    fid = db.execute("INSERT INTO followups(profile_id, body, created_by, created_name, created) VALUES(?, ?, ?, ?, ?)",
                     (pid, _enc(body), who["id"], who["username"], at or dbm.now())).lastrowid
    records._bump(db, pid)
    return fid


def change(db, pid, who, fid, body):
    from . import records
    n = db.execute("UPDATE followups SET body=?, changed_by=?, changed_name=?, changed=? WHERE id=? AND profile_id=?",
                   (_enc(body), who["id"], who["username"], dbm.now(), fid, pid)).rowcount
    records._bump(db, pid)
    return n > 0


def remove(db, pid, fid):
    """-> what it was, or None"""
    from . import records
    was = get(db, pid, fid)
    if was is not None:
        db.execute("DELETE FROM followups WHERE id=? AND profile_id=?", (fid, pid))
        records._bump(db, pid)
    return was


def any_(db, pid):
    return db.execute("SELECT 1 FROM followups WHERE profile_id=? LIMIT 1", (pid,)).fetchone() is not None


def feed_events(db, pid):
    """The follow-ups the calendar feed lists: not done, with a date. [{ident, day, summary, words}], words: the
    person's own text when they chose to show it, else None (the summary is then GENERIC)."""
    out = []
    for r, b in _rows(db, pid):
        if b.get("done") or not b.get("due"):
            continue
        out.append({"ident": f"followup|{r['id']}", "day": date.fromisoformat(b["due"]),
                    "summary": b["text"] if b.get("show") else GENERIC, "words": b["text"] if b.get("show") else None})
    return out


def log_detail(change_):
    """What the activity log keeps: what was done, never what the follow-up says."""
    return {"change": change_}
