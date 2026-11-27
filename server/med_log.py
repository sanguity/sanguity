"""A medication log (#235): doses the person says were taken, taken late or skipped, beside the records. Stored in
med_doses (schema version 33, with #162's reminders).

A dose is written by a person, not imported: it belongs to the profile, so undoing or replacing an import leaves it,
and it finds its medication by the medication's grouping key (medications.med_key), as a reminder does. Everything it
says is encrypted in body: {med: the key, name: what the medication was called when it was logged, t: the wall-clock
time it happened, YYYY-MM-DDTHH:MM, in the person's own time, status: "taken" | "late" | "skipped", note, slot: the
scheduled time "HH:MM" it was logged against from today's list, when it was, amount: a number or None, unit: "tablets"
(#1095: from its scheduled time's, or typed), asneeded: true when logged As needed, never one of the schedule's}. Who logged or last changed it, and when,
are plain. The records the page loads list them as "doses" (a change bumps profiles.records_rev); an export carries
them in its data.json, and a backup has them with the rest of the database.

From the phone: Health Auto Export (Apple Health, iOS 26 and later) sends Apple Health's Medications dose events in
its "medications" list, and the watch receiver keeps those marked Taken or Skipped (receiver.py, "med_doses" in the
summary); Health Connect has no dose events (its medical records are a clinic's, not doses logged), so from Android
they are typed only. The phone's are read, not changed here.

It records what the person says happened. It never says a dose was due, or should be taken: a count is of the doses
logged ("12 of 14 logged doses taken"), or, for a medication with a schedule (#1094, med_reminders.py), of the doses
its schedule gives ("9 of 14 scheduled doses taken", #1095), plain counts. Each dose of the schedule on a day is
matched with the dose logged for it: one logged against its time (slot) takes the first of that time not yet taken
(two at 8:00 are two, logged one by one); one with no slot (the phone's, or typed in the drawer) the nearest of that
day within WINDOW minutes of it; one logged As needed (asneeded), and any left, is "also" (logged, but not one of the
schedule's).
"""
import json, re
from datetime import date, datetime, timedelta

from . import crypto

STATUSES = ("taken", "late", "skipped")
LABEL = {"taken": "Taken", "late": "Taken late", "skipped": "Skipped"}
NOTE_MAX = 1000
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
_SLOT = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
PHONE = {"taken": "taken", "skipped": "skipped"}   # Apple Health's dose statuses kept; the others aren't a dose logged
WINDOW = 120   # minutes either side of a scheduled time in which a phone's dose is that one (#1095)


class DoseError(ValueError):
    """Something about a dose that can't be saved, said for the person who logged it."""


def _enc(obj):
    return crypto.enc(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))


def medications(db, pid):
    """{key: name} of the profile's medications (medications.history, edits applied)."""
    from . import records
    body = records.served(db, pid)
    meds = (json.loads(body).get("medications") or []) if body else []
    return {m["key"]: m.get("name") or m["key"] for m in meds if isinstance(m, dict) and m.get("key")}


def check(db, pid, med, t, status, note=None, slot=None, today=None, keep_med=None, amount=None, asneeded=False):
    """The dose as stored, or DoseError. med: one of the profile's medications, by key (or keep_med, the dose's own,
    when its medication's import was undone since). t: no later than today (the person's own, given). amount:
    {amount, unit}, or None."""
    from . import med_reminders
    meds = medications(db, pid)
    if med != keep_med and med not in meds:
        raise DoseError("Pick the medication from this profile's medications.")
    if status not in STATUSES:
        raise DoseError("A dose is taken, taken late or skipped.")
    if not isinstance(t, str) or not _TIME.match(t):
        raise DoseError("Give the date and time as YYYY-MM-DDTHH:MM.")
    try:
        when = datetime.strptime(t, "%Y-%m-%dT%H:%M")
    except ValueError:
        raise DoseError("That date or time doesn't exist.") from None
    if today and when.date() > today:
        raise DoseError("That's after today: log a dose once it has happened.")
    if note is not None and not isinstance(note, str):
        raise DoseError("The note must be text.")
    note = (note or "").strip()
    if len(note) > NOTE_MAX:
        raise DoseError(f"The note is too long (at most {NOTE_MAX} characters).")
    if slot is not None and (not isinstance(slot, str) or not _SLOT.match(slot)):
        raise DoseError("The scheduled time is HH:MM.")
    try:
        a = med_reminders.amount(amount)
    except med_reminders.ReminderError as x:
        raise DoseError(str(x)) from None
    return {"med": med, "name": str(meds.get(med) or med)[:300], "t": t, "status": status, "note": note, "slot": slot or None, **a,
            **({"asneeded": True} if asneeded and not slot else {})}


def add(db, pid, who, dose):
    from . import db as dbm, records
    did = db.execute("INSERT INTO med_doses(profile_id, body, created_by, created_name, created) VALUES(?, ?, ?, ?, ?)",
                     (pid, _enc(dose), who["id"], who["username"], dbm.now())).lastrowid
    records._bump(db, pid)
    return did


def get(db, pid, did):
    r = db.execute("SELECT body FROM med_doses WHERE id=? AND profile_id=?", (did, pid)).fetchone()
    return json.loads(crypto.dec(r["body"])) if r else None


def change(db, pid, did, who, dose):
    from . import db as dbm, records
    was = get(db, pid, did)
    if was is None:
        return None
    db.execute("UPDATE med_doses SET body=?, changed_by=?, changed_name=?, changed=? WHERE id=? AND profile_id=?",
               (_enc(dose), who["id"], who["username"], dbm.now(), did, pid))
    records._bump(db, pid)
    return was


def remove(db, pid, did):
    from . import records
    was = get(db, pid, did)
    if was is not None:
        db.execute("DELETE FROM med_doses WHERE id=? AND profile_id=?", (did, pid))
        records._bump(db, pid)
    return was


def listing(db, pid, ids=None):
    """The profile's doses, oldest first: {id, med, name, t, status, note, slot, amount, unit, by, at, changed, changed_by}. ids: only
    those doses (a change's reply carries the dose it changed, #271)."""
    out = []
    only = "" if ids is None else f" AND id IN ({','.join('?' * len(ids))})"
    for r in db.execute(f"""SELECT id, body, created_name, created, changed_name, changed FROM med_doses
                           WHERE profile_id=?{only} ORDER BY id""", (pid, *(ids or ()))):
        d = json.loads(crypto.dec(r["body"]))
        out.append({"id": r["id"], "med": d.get("med"), "name": d.get("name") or "", "t": d.get("t"), "status": d.get("status"),
                    "note": d.get("note") or "", "slot": d.get("slot"), "amount": d.get("amount"), "unit": d.get("unit") or "",
                    "asneeded": bool(d.get("asneeded")), "by": r["created_name"], "at": r["created"],
                    "changed": r["changed"], "changed_by": r["changed_name"]})
    out.sort(key=lambda d: (str(d["t"] or ""), d["id"]))
    return out


def log_detail(action, dose):
    """What the activity log keeps: what was done and the dose's status, never the medication or the note."""
    return {"change": "dose." + action, "status": dose.get("status")}


# ---------------------------------------------------------------- the phone's, and the two together

def phone_doses(watch, tz=None):
    """The watch summary's dose events (receiver.py: "med_doses" = [[time with its offset, name, status, dosage, units,
    app]]), as {t: local wall time YYYY-MM-DDTHH:MM, name, status, dose, app}; tz: the summary's zone, for a time with
    no offset."""
    out = []
    for r in (watch or {}).get("med_doses") or []:
        if not isinstance(r, list) or len(r) < 3 or PHONE.get(r[2]) is None:
            continue
        try:
            x = datetime.fromisoformat(str(r[0]).replace("Z", "+00:00"))
        except ValueError:
            continue
        if x.tzinfo and tz is not None:
            x = x.astimezone(tz)
        dose = r[3] if len(r) > 3 and isinstance(r[3], (int, float)) and not isinstance(r[3], bool) else None
        unit = str(r[4]) if len(r) > 4 and r[4] else ""
        out.append({"t": x.strftime("%Y-%m-%dT%H:%M"), "name": str(r[1] or "")[:300], "status": PHONE[r[2]],
                    "dose": f"{dose:g} {unit}".strip() if dose is not None else "", "app": str(r[5]) if len(r) > 5 and r[5] else "phone"})
    return out


def week_of(day):
    """The Monday of a day's week."""
    return day - timedelta(days=day.weekday())


def counts(doses):
    """{"logged", "taken" (late ones included), "late", "skipped"} of a list of doses."""
    n = {s: sum(1 for d in doses if d.get("status") == s) for s in STATUSES}
    return {"logged": len(doses), "taken": n["taken"] + n["late"], "late": n["late"], "skipped": n["skipped"]}


def said(c):
    """"12 of 14 logged doses taken (1 late), 2 skipped": counts in words, of what was logged."""
    if not c["logged"]:
        return "No doses logged"
    s = f"{c['taken']} of {c['logged']} logged dose{'' if c['logged'] == 1 else 's'} taken"
    if c["late"]:
        s += f" ({c['late']} late)"
    if c["skipped"]:
        s += f", {c['skipped']} skipped"
    return s


# ---------------------------------------------------------------- against a schedule (#1095)

def _min(hm):
    return int(hm[:2]) * 60 + int(hm[3:5])


def scheduled(lines, day, stop=None):
    """The doses a medication's schedule lines (med_reminders.listing's, its own) give on a day: [(time "HH:MM",
    {amount, unit})] by time, two at one time two; a paused line gives none. stop: the day the records say it stopped
    (it ends its lines)."""
    from . import med_reminders
    k, iso = med_reminders.DAYS[day.weekday()], day.isoformat()
    out = []
    for r in lines:
        if r.get("asneeded") or r.get("paused") or k not in (r.get("days") or ()) or not r.get("from") or r["from"] > iso:
            continue
        last, _ = med_reminders.ends(r, {"stop": stop})
        if last and last < iso:
            continue
        amounts = r.get("amounts") or []
        out += [(t, amounts[i] if i < len(amounts) else {"amount": None, "unit": ""}) for i, t in enumerate(r.get("times") or [])]
    return sorted(out, key=lambda x: x[0])


def match(sched, doses):
    """Each of a day's scheduled doses with the dose logged for it: ([dose or None for each], [the others]). doses:
    that day's, of that medication, {t, status, slot, asneeded?}: one with a slot takes the first of its time not yet
    taken; one without (the phone's, or typed with no time of the schedule) the nearest within WINDOW minutes; one
    logged As needed, and the rest, are the others."""
    got, other, later = [None] * len(sched), [], []
    for d in sorted(doses, key=lambda d: str(d.get("t") or "")):
        if d.get("slot"):
            i = next((i for i, (at, _) in enumerate(sched) if at == d["slot"] and got[i] is None), None)
            if i is None:
                other.append(d)
            else:
                got[i] = d
        else:
            later.append(d)
    for d in later:
        if not d.get("asneeded") and _TIME.match(str(d.get("t") or "")):
            hm = _min(d["t"][11:16])
            near = [(abs(_min(at) - hm), i) for i, (at, _) in enumerate(sched) if got[i] is None and abs(_min(at) - hm) <= WINDOW]
            if near:
                got[min(near)[1]] = d
                continue
        other.append(d)
    return got, other


def schedule_counts(lines, doses, days, now, stop=None):
    """{"scheduled", "taken" (late ones included), "late", "skipped", "other"} of the doses a medication's schedule
    gives on the days (dates), each counted once its time has come (now: the person's wall-clock datetime) or a dose
    is logged for it; other: the doses logged on those days that aren't one of them. doses: the medication's, typed
    and the phone's ({t, status, slot, phone?})."""
    by = {}
    for d in doses:
        by.setdefault(str(d.get("t") or "")[:10], []).append(d)
    n = {"scheduled": 0, "taken": 0, "late": 0, "skipped": 0, "other": 0}
    today, hm = now.date(), now.strftime("%H:%M")
    for day in days:
        if day > today:
            continue
        sched = scheduled(lines, day, stop)
        got, other = match(sched, by.get(day.isoformat(), []))
        n["other"] += len(other)
        for (at, _), d in zip(sched, got):
            if day == today and at > hm and d is None:
                continue
            n["scheduled"] += 1
            st = d and d.get("status")
            if st in ("taken", "late"):
                n["taken"] += 1
            n["late"] += st == "late"
            n["skipped"] += st == "skipped"
    return n


def said_scheduled(c):
    """"9 of 14 scheduled doses taken (1 late), 2 skipped; 1 other dose logged": plain counts of the schedule's doses
    so far, and of those logged that aren't one."""
    s = "No scheduled doses in this period"   # (the week's line says "this week", medlog.js)
    if c["scheduled"]:
        s = f"{c['taken']} of {c['scheduled']} scheduled dose{'' if c['scheduled'] == 1 else 's'} taken"
        if c["late"]:
            s += f" ({c['late']} late)"
        if c["skipped"]:
            s += f", {c['skipped']} skipped"
    if c.get("other"):
        s += f"; {c['other']} other dose{'' if c['other'] == 1 else 's'} logged"
    return s
