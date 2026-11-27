"""A medication's schedule (#1094), and its reminders through the calendar feed (#162): for a current medication, the
people who keep the profile (owners and editors) set its schedule as one or more lines, each with its times of day
and the amount at each (2 tablets at 08:00, 1 at 20:00, any number a day, two at one time if need be), which days, from
and until; or a line As needed, with no times (doses logged when taken, its usual amount said). Several lines make one
medication's schedule: every day at 08:00, and Mondays and Fridays at 20:00 (the owner, on #1094). Today lists each
dose a line gives that day (medlog.js), and the counts are of those (med_log.schedule_counts). Remind me on a line
(its remind column) puts each of its times in the calendar feed (#51, calendar.py) as a recurring event, with the
amounts, so the person's own calendar app does the reminding. Sanguity sends nothing itself. A line paused (its
paused column; the owner, on #1090) is kept with its times and days to resume: until then it gives no dose on Today
or in any count (med_log.scheduled), and is in no calendar.

A line belongs to the profile, never to an import (schema version 33, med_reminders; version 47 made each reminder a
line, its "active" Remind me, and one that was off a paused line): it finds its medication by the medication's grouping key (medications.med_key), so
undoing or re-importing the records keeps it, and it finds its medication again when the same one is imported later.
Its body is encrypted: {med: the key, name: what the medication was called when it was set, times: ["08:00", ...]
(in order, a time twice for two doses then), amounts: [{amount: a number or None, unit: "tablets"}, ...] (one for each
time; a reminder from before #1094 has none: no amount), days: ["mo", ...] (all seven: every day), from: YYYY-MM-DD,
until: YYYY-MM-DD or None, tz: an IANA zone, or "" for none, asneeded: true for As needed (no times), dose: its usual
amount {amount, unit} or None}.

When it ends, as the feed says it: on its own until day; or, sooner, on the day the records say the medication
stopped (medications.history's "stop"): a medication that stops in the records ends its schedule, and the page says
so where the line is listed. A line whose medication isn't in the records any more (its import undone) is left out
of the feed until it is. With Remind me off, it is in no calendar, but still the schedule.

Times are wall-clock times in the line's own zone, the time zone of the person who set it (their Settings → Dates
and times, #42, or their browser's): "08:00 every day" stays at 08:00 when the clocks change. The feed writes them
as DTSTART;TZID=<zone> with a VTIMEZONE made from this server's time zone database (vtimezone()), because RFC 5545
expands a recurrence in its DTSTART's local time (3.3.10) and requires a VTIMEZONE for every TZID used (3.6.5); UNTIL
is then in UTC (3.3.10). A zone this server doesn't know (no tz database) gives floating local times instead
(3.3.5, form #1), with a floating UNTIL, which calendar apps show as written.
"""
import json, re
from datetime import date, datetime, time as Time, timedelta, timezone

from . import crypto, medications

DAYS = ("mo", "tu", "we", "th", "fr", "sa", "su")   # Python's weekday() order; RFC 5545's BYDAY names, upper case
DAY_NAMES = {"mo": "Mon", "tu": "Tue", "we": "Wed", "th": "Thu", "fr": "Fri", "sa": "Sat", "su": "Sun"}
TIMES_MAX = 24        # doses a day on one line
UNIT_MAX = 40
AMOUNT_MAX = 100000
REMINDER_MINUTES = 15   # how long each event lasts in a calendar: a short mark, not a block of time
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ReminderError(ValueError):
    """Something about a reminder that can't be saved, said for the person who set it."""


def _enc(obj):
    return crypto.enc(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))


def _date(v, what):
    if not isinstance(v, str) or not _DAY.match(v):
        raise ReminderError(f"Give the {what} date as YYYY-MM-DD.")
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise ReminderError(f"The {what} date doesn't exist.") from None


def current(db, pid):
    """{key: medication} of the profile's medications (medications.history, edits applied), each with "current": the
    records give no end for its latest listing."""
    from . import records
    body = records.served(db, pid)
    meds = (json.loads(body).get("medications") or []) if body else []
    out = {}
    for m in meds:
        if isinstance(m, dict) and m.get("key"):
            spans = m.get("spans") or []
            out[m["key"]] = {**m, "current": bool(spans) and bool(spans[-1].get("open"))}
    return out


def amount(v, what="amount"):
    """{amount, unit} as kept: amount a number above 0 (None: none given), unit up to UNIT_MAX characters; or
    ReminderError. v: {amount, unit}, or None."""
    v = v if isinstance(v, dict) else {}
    n, unit = v.get("amount"), v.get("unit")
    if n is not None and (isinstance(n, bool) or not isinstance(n, (int, float)) or not 0 < n <= AMOUNT_MAX or n != n):
        raise ReminderError(f"Give the {what} as a number above 0.")
    if unit is not None and not isinstance(unit, str):
        raise ReminderError("Give the unit as words: tablets, ml.")
    unit = " ".join((unit or "").split())
    if len(unit) > UNIT_MAX:
        raise ReminderError(f"The unit is too long (at most {UNIT_MAX} characters).")
    return {"amount": None if n is None else round(float(n), 4), "unit": unit}


def said(a):
    """"2 tablets", "0.5 ml", or "" with no amount."""
    if not a or a.get("amount") is None:
        return ""
    return f"{a['amount']:g} {a.get('unit') or ''}".strip()


def line(times, amounts, days, start, until, asneeded=False, dose=None):
    """A schedule line's times (in order, with their amounts), days, from, until, As needed and its usual amount, as
    kept; or ReminderError."""
    if asneeded:
        times, amounts = [], []
    elif not isinstance(times, list) or not times:
        raise ReminderError("Give at least one time of day.")
    if not all(isinstance(t, str) and _TIME.match(t) for t in times):
        raise ReminderError("Give each time as HH:MM, on a 24-hour clock.")
    if len(times) > TIMES_MAX:
        raise ReminderError(f"At most {TIMES_MAX} times a day.")
    if amounts is None:
        amounts = [None] * len(times)
    if not isinstance(amounts, list) or len(amounts) != len(times):
        raise ReminderError("Give an amount, or none, for each time.")
    pairs = sorted(zip(times, (amount(x) for x in amounts)), key=lambda p: p[0])   # stable: two at a time keep their order
    if not isinstance(days, list) or not days or not all(d in DAYS for d in days):
        raise ReminderError("Pick at least one day of the week.")
    a = _date(start, "from")
    b = _date(until, "until") if until else None
    if b and b < a:
        raise ReminderError("The until date is before the from date.")
    usual = amount(dose, "usual amount") if asneeded and dose else None
    return {"times": [t for t, _ in pairs], "amounts": [x for _, x in pairs], "days": [d for d in DAYS if d in days],
            "from": a.isoformat(), "until": b.isoformat() if b else None, "asneeded": bool(asneeded),
            "dose": usual if usual and (usual["amount"] is not None or usual["unit"]) else None}


def check(db, pid, med, times, days, start, until, tz, keep_med=None, amounts=None, asneeded=False, dose=None):
    """The schedule line as stored, or ReminderError. med: the medication's key; it must be a current medication of the
    profile, or keep_med (the line's own, when it is changed after the records said it stopped)."""
    from . import settings
    meds = current(db, pid)
    m = meds.get(med) if isinstance(med, str) else None
    if med != keep_med and not (m and m["current"]):
        raise ReminderError("A schedule is for a medication the records list as current. Pick one from this profile's medications.")
    ln = line(times, amounts, days, start, until, asneeded, dose)
    tz = tz or ""
    if tz:
        try:
            settings.time_zone(tz)
        except settings.SettingError as x:
            raise ReminderError(str(x)) from None
    name = (m or {}).get("name") or med
    return {"med": med, "name": str(name)[:300], **ln, "tz": tz}


def add(db, pid, who, r, active=True):
    from . import db as dbm, records
    rid = db.execute("""INSERT INTO med_reminders(profile_id, body, remind, created_by, created_name, created)
                        VALUES(?, ?, ?, ?, ?, ?)""", (pid, _enc(r), int(bool(active)), who["id"], who["username"], dbm.now())).lastrowid
    records._bump(db, pid)
    return rid


def get(db, pid, rid):
    r = db.execute("SELECT body, remind, paused FROM med_reminders WHERE id=? AND profile_id=?", (rid, pid)).fetchone()
    return {**json.loads(crypto.dec(r["body"])), "on": bool(r["remind"]), "paused": bool(r["paused"])} if r else None


def change(db, pid, rid, who, r=None, active=None, paused=None):
    """Replace a line with a checked one, turn its Remind me on or off, and/or pause or resume it. Returns what it
    was, or None."""
    from . import db as dbm, records
    was = get(db, pid, rid)
    if was is None:
        return None
    body = _enc(r) if r is not None else None
    db.execute("""UPDATE med_reminders SET body=COALESCE(?, body), remind=COALESCE(?, remind), paused=COALESCE(?, paused),
                  changed_by=?, changed_name=?, changed=? WHERE id=? AND profile_id=?""",
               (body, None if active is None else int(bool(active)), None if paused is None else int(bool(paused)),
                who["id"], who["username"], dbm.now(), rid, pid))
    records._bump(db, pid)
    return was


def remove(db, pid, rid):
    from . import records
    was = get(db, pid, rid)
    if was is not None:
        db.execute("DELETE FROM med_reminders WHERE id=? AND profile_id=?", (rid, pid))
        records._bump(db, pid)
    return was


def listing(db, pid, ids=None):
    """The profile's schedule lines, oldest first: {id, med, name, times, amounts, days, from, until, tz, asneeded, dose,
    on (Remind me), paused, by, at, changed, changed_by}. ids: only those lines (a change's reply carries the one it changed,
    #271). One from before #1094 is read as it reminded: its times with no amounts."""
    out = []
    only = "" if ids is None else f" AND id IN ({','.join('?' * len(ids))})"
    for r in db.execute(f"""SELECT id, body, remind, paused, created_name, created, changed_name, changed FROM med_reminders
                           WHERE profile_id=?{only} ORDER BY id""", (pid, *(ids or ()))):
        b = json.loads(crypto.dec(r["body"]))
        times = b.get("times") or []
        amounts = b.get("amounts") if isinstance(b.get("amounts"), list) and len(b["amounts"]) == len(times) else None
        out.append({"id": r["id"], "med": b.get("med"), "name": b.get("name") or "", "times": times,
                    "amounts": amounts or [{"amount": None, "unit": ""} for _ in times],
                    "days": b.get("days") or list(DAYS), "from": b.get("from"), "until": b.get("until"), "tz": b.get("tz") or "",
                    "asneeded": bool(b.get("asneeded")), "dose": b.get("dose") or None,
                    "on": bool(r["remind"]), "paused": bool(r["paused"]), "by": r["created_name"], "at": r["created"], "changed": r["changed"],
                    "changed_by": r["changed_name"]})
    return out


def log_detail(change_, r=None):
    """What the activity log keeps: what was done and how many times a day, never the medication or the amounts."""
    return {"change": change_, **({"times": len(r.get("times") or [])} if r else {})}


def ends(r, med):
    """(the last day a reminder runs, why: "until" | "stopped") or (None, None) while it has no end. med: its
    medication from medications.history (its "stop": the day the records say it stopped)."""
    stop = (med or {}).get("stop")
    until = r.get("until")
    if stop and (not until or stop < until):
        return stop, "stopped"
    return (until, "until") if until else (None, None)


# ---------------------------------------------------------------- iCalendar (RFC 5545): recurrence and VTIMEZONE

def _zone(name):
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:   # no tz database here, or a name it doesn't have
        return None


def _offset(td):
    """A UTC offset as TZOFFSETFROM/TO write it (3.3.14): +HHMM, or +HHMMSS when it has seconds."""
    s = int(td.total_seconds())
    sign, s = ("-" if s < 0 else "+"), abs(s)
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{sign}{h:02d}{m:02d}" + (f"{sec:02d}" if sec else "")


def transitions(zone, y0, y1):
    """The zone's changes of UTC offset from the start of y0 to the end of y1: [(instant UTC, offset before, offset
    after, name after, daylight after)], found day by day, then to the second."""
    out = []
    t = datetime(y0, 1, 1, tzinfo=timezone.utc)
    end = datetime(y1 + 1, 1, 1, tzinfo=timezone.utc)
    prev = t.astimezone(zone).utcoffset()
    while t < end:
        n = t + timedelta(days=1)
        off = n.astimezone(zone).utcoffset()
        if off != prev:
            lo, hi = int(t.timestamp()), int(n.timestamp())
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if datetime.fromtimestamp(mid, timezone.utc).astimezone(zone).utcoffset() == prev:
                    lo = mid
                else:
                    hi = mid
            at = datetime.fromtimestamp(hi, timezone.utc).astimezone(zone)
            out.append((datetime.fromtimestamp(hi, timezone.utc), prev, off, at.tzname() or "", bool(at.dst())))
            prev = off
        t = n
    return out


def _rule(local):
    """The yearly rule a local onset follows as RRULE parts: its month and its weekday counted from the start of the
    month (1-4) or, when it is the month's last such weekday, from the end (-1)."""
    nxt = local + timedelta(days=7)
    nth = -1 if nxt.month != local.month else (local.day - 1) // 7 + 1
    return (local.month, nth, DAYS[local.weekday()].upper(), local.time())


def vtimezone(name, y0, y1):
    """A VTIMEZONE (as content lines) for the zone over the years y0 to y1, from this server's tz database; None for
    a zone it doesn't know. Each run of yearly changes that follow one rule (the US's second Sunday in March, the EU's
    last Sunday in October...) is one STANDARD or DAYLIGHT observance with an RRULE: the last run of each goes on with
    no end, an earlier one ends (UNTIL) at its last change, as when a country's rules changed. Changes that follow no
    yearly rule are an observance each. A zone with no changes in those years is one STANDARD observance."""
    zone = _zone(name)
    if zone is None:
        return None
    lines = ["BEGIN:VTIMEZONE", "TZID:" + name]
    trs = transitions(zone, y0 - 1, y1)   # from the year before, so an observance starts before the first event
    if not trs:
        at = datetime(y0, 1, 1, 12, tzinfo=timezone.utc).astimezone(zone)
        off = _offset(at.utcoffset())
        lines += ["BEGIN:STANDARD", "DTSTART:19700101T000000", "TZOFFSETFROM:" + off, "TZOFFSETTO:" + off]
        lines += (["TZNAME:" + at.tzname()] if at.tzname() else []) + ["END:STANDARD", "END:VTIMEZONE"]
        return lines
    runs = []   # [(kind key, rule, [onsets...])]: consecutive yearly changes with the same offsets, name and rule
    for when, before, after, tzname, dst in trs:
        local = (when + before).replace(tzinfo=None)   # the onset in the offset before it (3.6.5: DTSTART is local)
        key, rule = (dst, before, after, tzname), _rule(local)
        last = next((r for r in reversed(runs) if r[0] == key), None)
        if last and last[1] == rule and last[2][-1][1].year == local.year - 1:
            last[2].append((when, local))
        else:
            runs.append((key, rule, [(when, local)]))
    final = {}   # each kind's last run: open-ended when it reaches the last year scanned
    for i, r in enumerate(runs):
        final[r[0]] = i
    for i, ((dst, before, after, tzname), (month, nth, wd, _), onsets) in enumerate(runs):
        comp = "DAYLIGHT" if dst else "STANDARD"
        lines += ["BEGIN:" + comp, "DTSTART:" + onsets[0][1].strftime("%Y%m%dT%H%M%S"),
                  "TZOFFSETFROM:" + _offset(before), "TZOFFSETTO:" + _offset(after)]
        if len(onsets) > 1:
            rr = f"RRULE:FREQ=YEARLY;BYMONTH={month};BYDAY={nth}{wd}"
            if not (final[(dst, before, after, tzname)] == i and onsets[-1][1].year >= y1):
                rr += ";UNTIL=" + onsets[-1][0].strftime("%Y%m%dT%H%M%SZ")
            lines.append(rr)
        if tzname:
            lines.append("TZNAME:" + tzname)
        lines.append("END:" + comp)
    return lines + ["END:VTIMEZONE"]


def first_day(start, days):
    """The first day on or after start that is one of the days: where the recurrence starts, so DTSTART is one of
    its own instances (3.8.5.3: a DTSTART not synchronized with its rule gives an undefined set)."""
    want = {DAYS.index(d) for d in days}
    for i in range(7):
        d = start + timedelta(days=i)
        if d.weekday() in want:
            return d
    return start


def recurrence(r, at, last_day, zone):
    """[DTSTART, DTEND, RRULE] content lines of one time of day of a reminder, or None when it never happens (its end
    is before its first day). at: "HH:MM"; last_day: date or None; zone: ZoneInfo, or None for floating times."""
    days = r.get("days") or list(DAYS)
    d0 = first_day(date.fromisoformat(r["from"]), days)
    if last_day and last_day < d0:
        return None
    h, m = (int(x) for x in at.split(":"))
    start = datetime.combine(d0, Time(h, m))
    end = start + timedelta(minutes=REMINDER_MINUTES)
    fmt = "%Y%m%dT%H%M%S"
    if zone is not None:
        tzid = ";TZID=" + r["tz"]
        out = ["DTSTART" + tzid + ":" + start.strftime(fmt), "DTEND" + tzid + ":" + end.strftime(fmt)]
    else:
        out = ["DTSTART:" + start.strftime(fmt), "DTEND:" + end.strftime(fmt)]
    rule = "RRULE:FREQ=DAILY" if len(days) == 7 else "RRULE:FREQ=WEEKLY;BYDAY=" + ",".join(d.upper() for d in days)
    if last_day:
        stop = datetime.combine(last_day, Time(23, 59, 59))   # every instance on the last day is in
        if zone is not None:   # with a TZID, UNTIL is UTC (3.3.10)
            rule += ";UNTIL=" + stop.replace(tzinfo=zone).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        else:   # floating DTSTART: a floating UNTIL
            rule += ";UNTIL=" + stop.strftime(fmt)
    return out + [rule]


def feed_events(db, pid, full, today):
    """What the calendar feed lists for the profile's schedule: ([{ident, summary, dose, amount, lines}], {zone names
    used: (first year, last year)}). Only lines with Remind me on, not paused, and times (not As needed), of a medication still in
    the records, that haven't ended before today: an event for each time of day, saying the amounts at it (two doses
    at one time, both). amount: "2 tablets", or "" with none set."""
    meds = current(db, pid)
    events, zones = [], {}
    for r in listing(db, pid):
        m = meds.get(r["med"])
        if not r["on"] or r["paused"] or not m or not r.get("from") or r["asneeded"] or not r["times"]:
            continue
        last, _ = ends(r, m)
        last_day = date.fromisoformat(last) if last else None
        if last_day and last_day < today:
            continue   # over: nothing to remind of
        zone = _zone(r["tz"])
        if zone is not None:
            y0 = date.fromisoformat(r["from"]).year
            y1 = max(today.year + 2, last_day.year if last_day else 0)
            a, b = zones.get(r["tz"], (y0, y1))
            zones[r["tz"]] = (min(a, y0), max(b, y1))
        spans = m.get("spans") or []
        dose = (spans[-1].get("dose") if spans else "") or ""
        at_times = {}
        for at, x in zip(r["times"], r["amounts"]):
            at_times.setdefault(at, []).append(said(x))
        for at, amounts in at_times.items():
            lines = recurrence(r, at, last_day, zone)
            if lines is None:
                continue
            amount = " and ".join(a for a in amounts if a)
            summary = ((m.get("name") or r["name"] or "Medication") + (f" · {amount}" if amount else "")) if full else "Medication"
            events.append({"ident": f"reminder|{r['id']}|{at}", "summary": summary, "dose": dose if full else "",
                           "amount": amount if full else "", "lines": lines})
    return events, zones
