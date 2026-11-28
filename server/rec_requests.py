"""Records requests (#1133): a log of the requests a person makes for their own health records, each counted down to
the deadline of the rule they picked, its letter drafted on the page (requests.js), and, once the records arrive,
linked to their import.

A request says who it went to, when it was sent and how (portal, email, fax, post, in person), the records wanted
(all, or a date range and kinds) and in what format, the person's name for the letter, its status (waiting,
extended, received, closed) and whether its extension was used, and the rule picked for it (the owner's decision;
the app assumes no country, and the page says "the rule you picked", never "the law says"):

  hipaa   US HIPAA: 30 days from the request, plus one 30-day extension
  gdpr    UK/EU GDPR: one month, extendable by two months more (calendar months, calendar.add_months: Jan 31's
          month ends Feb 28)
  other   the person's own number of days, with an optional extension of their own

due() works the response date out from the sent day, which is the person's (today in their time zone when not given).
The calendar feed lists each request waiting or extended on its due day, all day, as "Sanguity records request" unless
the person chose to show that one's words (as follow-ups are, #1138). Facts only, never a verdict: the page says
"Sent Mar 3 · response due by Apr 2 (US HIPAA, 30 days)", "Extended to May 2", "Past the date you set".

One row each in records_requests (schema version 57, db.py): the body encrypted; import_id the import it's linked to (set to NULL when
that import is undone); who added or changed it and when, plain. The records list them as "requests"; Export
everything carries them (without the import link: the import ids are this install's) and loading an export puts them
back.
"""
import json, re
from datetime import date, timedelta

from . import calendar, crypto, db as dbm

RULES = {"hipaa": ("US HIPAA", 30, 30), "gdpr": ("UK/EU GDPR", 1, 2), "other": ("Your own rule", None, None)}
HOW = ("portal", "email", "fax", "post", "in person")
STATUS = ("waiting", "extended", "received", "closed")
FORMATS = ("fhir", "ccda", "pdf", "any", "paper")
OPEN = ("waiting", "extended")
TO_MAX, NAME_MAX, KINDS_MAX, NOTE_MAX, DAYS_MAX = 200, 200, 300, 2000, 365
GENERIC = "Sanguity records request"
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class RequestError(ValueError):
    """Something about a request that can't be saved, said for the person who made it."""


def _enc(obj):
    return crypto.enc(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))


def _day(v, what):
    if v in (None, ""):
        return None
    if not isinstance(v, str) or not _DAY.match(v):
        raise RequestError(f"Give the {what} as YYYY-MM-DD.")
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise RequestError(f"The {what} doesn't exist.") from None


def _text(v, limit, what):
    if v is None:
        return ""
    if not isinstance(v, str):
        raise RequestError(f"The {what} must be text.")
    v = v.strip() if what == "note" else " ".join(v.split())
    if len(v) > limit:
        raise RequestError(f"The {what} is too long (at most {limit} characters).")
    return v


def _days(v, what, zero=False):
    if v in (None, ""):
        return 0 if zero else None
    if not isinstance(v, int) or isinstance(v, bool) or not (0 if zero else 1) <= v <= DAYS_MAX:
        raise RequestError(f"Give the {what} as a whole number of days, {0 if zero else 1} to {DAYS_MAX}.")
    return v


def check(f, today):
    """The body to keep from the fields given, or RequestError. today: the person's today (a date), the sent day when
    none is given."""
    body = {"to": _text(f.get("to"), TO_MAX, "name of who it went to")}
    if not body["to"]:
        raise RequestError("Say who the request went to.")
    rule = f.get("rule") or "hipaa"
    if rule not in RULES:
        raise RequestError("Pick the rule: US HIPAA, UK/EU GDPR or your own.")
    status = f.get("status") or "waiting"
    if status not in STATUS:
        raise RequestError("The status is one of waiting, extended, received or closed.")
    how = f.get("how") or ""
    if how and how not in HOW:
        raise RequestError("How it was sent is one of: " + ", ".join(HOW) + ".")
    fmt = f.get("format") or "any"
    if fmt not in FORMATS:
        raise RequestError("The format is one of FHIR, C-CDA, PDF, any electronic copy, or paper.")
    sent = _day(f.get("sent"), "day it was sent") or today
    body.update(rule=rule, status=status, how=how, format=fmt, sent=sent.isoformat(),
                extended=bool(f.get("extended")) or status == "extended", show=bool(f.get("show")),
                name=_text(f.get("name"), NAME_MAX, "name"), kinds=_text(f.get("kinds"), KINDS_MAX, "kinds of records"),
                note=_text(f.get("note"), NOTE_MAX, "note"))
    if rule == "other":
        body["days"] = _days(f.get("days"), "number of days")
        if body["days"] is None:
            raise RequestError("Give the number of days of your own rule.")
        body["ext_days"] = _days(f.get("ext_days"), "extension", zero=True)
        if body["extended"] and not body["ext_days"]:
            raise RequestError("Give the extension's number of days, or leave the extension unused.")
    a, b = _day(f.get("from"), "first day of the records wanted"), _day(f.get("until"), "last day of the records wanted")
    if a and b and b < a:
        raise RequestError("The records' last day is before their first.")
    if a:
        body["from"] = a.isoformat()
    if b:
        body["until"] = b.isoformat()
    if (got := _day(f.get("received"), "day they arrived")):
        body["received"] = got.isoformat()
    return body


def due(b):
    """(the first response date, the date with its extension or None where the rule has none) of a body, as dates."""
    sent = date.fromisoformat(b["sent"])
    if b["rule"] == "hipaa":
        first = sent + timedelta(days=30)
        return first, first + timedelta(days=30)
    if b["rule"] == "gdpr":   # one month, and two more: counted from the request, so a 31st is each month's last day
        return calendar.add_months(sent, 1), calendar.add_months(sent, 3)
    first = sent + timedelta(days=b["days"])
    return first, first + timedelta(days=b["ext_days"]) if b.get("ext_days") else None


def rule_words(b):
    """"US HIPAA, 30 days", "UK/EU GDPR, one month", "Your own rule, 45 days" """
    if b["rule"] == "gdpr":
        return "UK/EU GDPR, one month"
    return f"{RULES[b['rule']][0]}, {30 if b['rule'] == 'hipaa' else b['days']} days"


def _shown(r, b, imports):
    first, ext = due(b)
    out = {"id": r["id"], **b, "due_first": first.isoformat(), "due_ext": ext.isoformat() if ext else None,
           "due": (ext if b["extended"] and ext else first).isoformat(), "rule_words": rule_words(b),
           "by": r["created_name"], "at": r["created"], "changed": r["changed"], "changed_by": r["changed_name"]}
    if r["import_id"] is not None and r["import_id"] in imports:
        out["import"] = imports[r["import_id"]]
    return out


def _rows(db, pid):
    return [(r, json.loads(crypto.dec(r["body"]))) for r in db.execute(
        """SELECT id, body, import_id, created_name, created, changed_name, changed FROM records_requests
           WHERE profile_id=? ORDER BY id""", (pid,)).fetchall()]


def _imports(db, pid):
    """{import id: {id, at, records}} of the profile's imports a request links to: records, the events it brought"""
    return {r["id"]: {"id": r["id"], "at": r["at"], "records": r["n"]} for r in db.execute(
        """SELECT i.id, i.at, (SELECT COUNT(*) FROM events e WHERE e.import_id = i.id) AS n FROM imports i
           WHERE i.profile_id=? AND i.id IN (SELECT import_id FROM records_requests WHERE profile_id=?)""", (pid, pid))}


def listing(db, pid, raw=False):
    """The profile's requests, oldest first, each with its due dates worked out and its import ({id, at, records}) when
    linked (the records' "requests"). raw: as kept, for an export (no import link, nothing worked out)."""
    rows = _rows(db, pid)
    if raw:
        return [{"id": r["id"], **b, "by": r["created_name"], "at": r["created"], "changed": r["changed"],
                 "changed_by": r["changed_name"]} for r, b in rows]
    imports = _imports(db, pid) if rows else {}
    return [_shown(r, b, imports) for r, b in rows]


def get(db, pid, rid):
    r = db.execute("SELECT body FROM records_requests WHERE id=? AND profile_id=?", (rid, pid)).fetchone()
    return json.loads(crypto.dec(r["body"])) if r else None


def add(db, pid, who, body):
    from . import records
    rid = db.execute("INSERT INTO records_requests(profile_id, body, created_by, created_name, created) VALUES(?, ?, ?, ?, ?)",
                     (pid, _enc(body), who["id"], who["username"], dbm.now())).lastrowid
    records._bump(db, pid)
    return rid


def change(db, pid, who, rid, body):
    from . import records
    db.execute("UPDATE records_requests SET body=?, changed_by=?, changed_name=?, changed=? WHERE id=? AND profile_id=?",
               (_enc(body), who["id"], who["username"], dbm.now(), rid, pid))
    records._bump(db, pid)


def link(db, pid, who, rid, import_id, zone=None):
    """Link request rid to import import_id of the same profile (None: unlink). Linked, a request waiting or extended is
    received, on the day of the import in the person's time zone (zone) if it has no day of its own. -> True, or False
    when there's no such import."""
    from datetime import datetime, timezone
    from . import records
    body = get(db, pid, rid)
    if import_id is not None:
        r = db.execute("SELECT at FROM imports WHERE id=? AND profile_id=?", (import_id, pid)).fetchone()
        if r is None:
            return False
        if body["status"] in OPEN:
            body["status"] = "received"
        tz = calendar._zone(zone) or timezone.utc
        body.setdefault("received", datetime.fromtimestamp(r["at"], timezone.utc).astimezone(tz).date().isoformat())
    db.execute("UPDATE records_requests SET body=?, import_id=?, changed_by=?, changed_name=?, changed=? WHERE id=? AND profile_id=?",
               (_enc(body), import_id, who["id"], who["username"], dbm.now(), rid, pid))
    records._bump(db, pid)
    return True


def remove(db, pid, rid):
    """-> what it was, or None"""
    from . import records
    was = get(db, pid, rid)
    if was is not None:
        db.execute("DELETE FROM records_requests WHERE id=? AND profile_id=?", (rid, pid))
        records._bump(db, pid)
    return was


def any_(db, pid):
    return db.execute("SELECT 1 FROM records_requests WHERE profile_id=? LIMIT 1", (pid,)).fetchone() is not None


def from_export(x):
    """A request an export lists (listing(raw=True)'s), as kept here, or ValueError: checked as one typed in (its sent
    day is given)."""
    if not isinstance(x, dict):
        raise ValueError("it isn't an object")
    if not x.get("sent"):
        raise ValueError("it doesn't say when it was sent")
    return check(x, date.today())


def feed_events(db, pid):
    """The requests the calendar feed lists: waiting or extended, on their due day. [{ident, day, summary, words}]"""
    out = []
    for r, b in _rows(db, pid):
        if b["status"] not in OPEN:
            continue
        first, ext = due(b)
        day = ext if b["extended"] and ext else first
        out.append({"ident": f"request|{r['id']}", "day": day, "words": b["show"],
                    "summary": f"Records request to {b['to']}: response due" if b["show"] else GENERIC})
    return out


def log_detail(change_):
    """What the activity log keeps: what was done, never who the request went to."""
    return {"change": change_}
