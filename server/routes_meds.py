"""Medication schedules and their reminders (#1094, #162, med_reminders.py): list, set, change, turn Remind me on or off
and remove a profile's schedule lines (still "reminders" in the API and the records). Viewers read them (the records
the page loads list them too, as "reminders"); owners and editors set them. The calendar feed (routes_calendar.py)
lists those with Remind me on. The medication log (#235, med_log.py): list, log, correct and remove doses,
the same way ("doses" in the records). The CSRF check in main.guard covers each change like every other, and each goes
in the profile's activity log as "medication" (what was done, how many times a day or the dose's status, never the
medication or a note). Each change's reply carries what it changed (#271), as the records list it, so the records page
updates in place: "reminders" or "doses", the one added or changed; "removed", the id of one removed. Its X-Records-Rev
header is the records' revision after it (records.stamp, #405)."""
from datetime import timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from . import activity, db as dbm, med_log, med_reminders as mr, medications, records, settings
from .main import _client_ip, get_db, need, user

router = APIRouter()


class Amount(BaseModel):
    amount: Optional[float] = None   # above 0; none: no amount
    unit: Optional[str] = None       # "tablets", "ml"


class Reminder(BaseModel):
    """A schedule line (#1094): its times with their amounts, days, from, until, or As needed; on: Remind me."""
    med: str                       # the medication's grouping key (DATA.medications[].key)
    times: List[str] = []          # "HH:MM", 24-hour; one twice for two doses at that time
    amounts: Optional[List[Optional[Amount]]] = None   # one for each time, in the same order; none: no amounts
    days: List[str]                # "mo".."su"; all seven: every day
    start: str                     # YYYY-MM-DD: from
    until: Optional[str] = None    # YYYY-MM-DD, or none
    tz: Optional[str] = None       # an IANA zone; none: the person's own (Settings -> Dates and times), else the server's UTC
    asneeded: bool = False         # As needed: no times
    dose: Optional[Amount] = None  # As needed: its usual amount
    on: bool = True                # Remind me: in the calendar feed


class Switch(BaseModel):
    on: Optional[bool] = None       # Remind me
    paused: Optional[bool] = None   # paused: kept, its doses on no Today and in no count, in no calendar (#1094)


def _zone(db, s, tz):
    if tz:
        return tz
    return settings.effective(db, None, s["id"])["dates.timeZone"][0] or "UTC"


def _checked(db, pid, s, body, keep_med=None):
    try:
        amounts = None if body.amounts is None else [a.model_dump() if a else None for a in body.amounts]
        return mr.check(db, pid, body.med, body.times, body.days, body.start, body.until, _zone(db, s, body.tz), keep_med,
                        amounts, body.asneeded, body.dose.model_dump() if body.dose else None)
    except mr.ReminderError as x:
        raise HTTPException(400, str(x))


def _log(db, s, pid, change, request, r=None):
    activity.log(db, "medication", s, pid, mr.log_detail(change, r), _client_ip(request))


@router.get("/api/profiles/{pid}/medications/reminders")
def list_reminders(pid: int, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "viewer")
    return {"reminders": mr.listing(db, pid)}


@router.post("/api/profiles/{pid}/medications/reminders", status_code=201)
def add_reminder(pid: int, body: Reminder, request: Request, response: Response, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    r = _checked(db, pid, s, body)
    with dbm.transaction(db):
        rid = mr.add(db, pid, s, r, body.on)
        _log(db, s, pid, "reminder.add", request, r)
    records.stamp(response, db, pid)
    return {"id": rid, "reminder": mr.get(db, pid, rid), "reminders": mr.listing(db, pid, [rid])}


@router.put("/api/profiles/{pid}/medications/reminders/{rid}")
def change_reminder(pid: int, rid: int, body: Reminder, request: Request, response: Response, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    was = mr.get(db, pid, rid)
    if was is None:
        raise HTTPException(404, "No such reminder.")
    r = _checked(db, pid, s, body, was.get("med"))
    with dbm.transaction(db):
        if mr.change(db, pid, rid, s, r, body.on) is None:
            raise HTTPException(404, "No such reminder.")
        _log(db, s, pid, "reminder.change", request, r)
    records.stamp(response, db, pid)
    return {"reminder": mr.get(db, pid, rid), "reminders": mr.listing(db, pid, [rid])}


@router.patch("/api/profiles/{pid}/medications/reminders/{rid}")
def switch_reminder(pid: int, rid: int, body: Switch, request: Request, response: Response, s=Depends(user), db=Depends(get_db)):
    """Turn a line's Remind me on or off, or pause or resume it: paused, it's kept to resume, giving no doses."""
    need(db, s, pid, "editor")
    if body.on is None and body.paused is None:
        raise HTTPException(400, "Say on or paused.")
    with dbm.transaction(db):
        if mr.change(db, pid, rid, s, active=body.on, paused=body.paused) is None:
            raise HTTPException(404, "No such reminder.")
        if body.on is not None:
            _log(db, s, pid, "reminder.on" if body.on else "reminder.off", request)
        if body.paused is not None:
            _log(db, s, pid, "reminder.pause" if body.paused else "reminder.resume", request)
    records.stamp(response, db, pid)
    return {"reminder": mr.get(db, pid, rid), "reminders": mr.listing(db, pid, [rid])}


@router.delete("/api/profiles/{pid}/medications/reminders/{rid}")
def remove_reminder(pid: int, rid: int, request: Request, response: Response, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    with dbm.transaction(db):
        if mr.remove(db, pid, rid) is None:
            raise HTTPException(404, "No such reminder.")
        _log(db, s, pid, "reminder.delete", request)
    records.stamp(response, db, pid)
    return {"ok": True, "removed": [rid]}


# ---------------------------------------------------------------- the medication log (#235)

class Dose(BaseModel):
    med: str                       # the medication's grouping key
    t: str                         # YYYY-MM-DDTHH:MM, the person's own wall-clock time
    status: str                    # taken, late, skipped
    note: Optional[str] = None
    slot: Optional[str] = None     # the scheduled time ("HH:MM") it was logged against, from today's list
    amount: Optional[float] = None   # how much (#1095): from its scheduled time's, or typed
    unit: Optional[str] = None
    asneeded: bool = False         # logged As needed: never one of the schedule's doses


def _dose(db, pid, s, body, keep_med=None):
    today = settings.local_now(db, s["id"]).date() + timedelta(days=1)   # a day's grace: the browser's zone may be ahead
    try:
        return med_log.check(db, pid, body.med, body.t, body.status, body.note, body.slot, today, keep_med,
                             {"amount": body.amount, "unit": body.unit}, body.asneeded)
    except med_log.DoseError as x:
        raise HTTPException(400, str(x))


@router.get("/api/profiles/{pid}/medications/doses")
def list_doses(pid: int, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "viewer")
    return {"doses": med_log.listing(db, pid)}


@router.post("/api/profiles/{pid}/medications/doses", status_code=201)
def add_dose(pid: int, body: Dose, request: Request, response: Response, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    d = _dose(db, pid, s, body)
    with dbm.transaction(db):
        did = med_log.add(db, pid, s, d)
        activity.log(db, "medication", s, pid, med_log.log_detail("add", d), _client_ip(request))
    records.stamp(response, db, pid)
    return {"id": did, "doses": med_log.listing(db, pid, [did])}


@router.put("/api/profiles/{pid}/medications/doses/{did}")
def change_dose(pid: int, did: int, body: Dose, request: Request, response: Response, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    was = med_log.get(db, pid, did)
    if was is None:
        raise HTTPException(404, "No such dose in the log.")
    d = _dose(db, pid, s, body, was.get("med"))
    with dbm.transaction(db):
        if med_log.change(db, pid, did, s, d) is None:
            raise HTTPException(404, "No such dose in the log.")
        activity.log(db, "medication", s, pid, med_log.log_detail("change", d), _client_ip(request))
    records.stamp(response, db, pid)
    return {"ok": True, "doses": med_log.listing(db, pid, [did])}


@router.delete("/api/profiles/{pid}/medications/doses/{did}")
def remove_dose(pid: int, did: int, request: Request, response: Response, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    with dbm.transaction(db):
        was = med_log.remove(db, pid, did)
        if was is None:
            raise HTTPException(404, "No such dose in the log.")
        activity.log(db, "medication", s, pid, med_log.log_detail("delete", was), _client_ip(request))
    records.stamp(response, db, pid)
    return {"ok": True, "removed": [did]}


# ---------------------------------------------------------------- your own medications, and your changes (#1091)
# medications.py, "your own medications". Viewers read them in the records the page loads (DATA.medications' "own",
# "change" and "listed", DATA.hiddenMeds); owners and editors add, change, remove, hide and show again. A change can
# move a medication anywhere on the page (Current to Past, out of Today), so each reply carries the records whole,
# "records", as an edit's does (records.page_reply, #271), with "key": the medication's. Logged as "medication":
# what was done, never the medication.

class MedFields(BaseModel):
    name: Optional[str] = None
    strength: Optional[str] = None
    dose: Optional[str] = None                 # the dose and instructions: "1 tablet twice a day"
    route: Optional[str] = None
    start: Optional[str] = None                # YYYY-MM-DD
    stop: Optional[str] = None                 # YYYY-MM-DD, or "" for none
    status: Optional[str] = None
    prescriber: Optional[str] = None
    note: Optional[str] = None
    key: Optional[str] = None                  # an own entry's, its removal undone (POST); the records' (PUT .../listed)


class Hidden(BaseModel):
    key: str
    hidden: bool


def _entry_reply(db, pid, reply, status=200):
    body, rev = records.page_reply(db, pid, reply)
    return Response(body, status_code=status, media_type="application/json", headers={"X-Records-Rev": rev})


def _fields(body, own):
    try:
        return medications.check(body.model_dump(exclude={"key"}), own)
    except medications.EntryError as x:
        raise HTTPException(400, str(x))


def _listed(db, pid, key):
    was = medications.record_listed(db, pid, key) if isinstance(key, str) and not medications.is_own(key) else None
    if was is None:
        raise HTTPException(404, "No such medication in the records.")
    return was


@router.get("/api/profiles/{pid}/medications/entries")
def list_entries(pid: int, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "viewer")
    return {"entries": medications.listing(db, pid)}


@router.post("/api/profiles/{pid}/medications/own", status_code=201)
def add_own(pid: int, body: MedFields, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    f = _fields(body, True)
    try:
        with dbm.transaction(db):
            eid = medications.add_own(db, pid, s, f, body.key)
            activity.log(db, "medication", s, pid, medications.log_detail("own.back" if body.key else "own.add"), _client_ip(request))
    except medications.EntryError as x:
        raise HTTPException(400, str(x))
    return _entry_reply(db, pid, {"id": eid, "key": medications.get(db, pid, eid)["key"]}, 201)


@router.put("/api/profiles/{pid}/medications/own/{eid}")
def change_own(pid: int, eid: int, body: MedFields, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    f = _fields(body, True)
    with dbm.transaction(db):
        was = medications.change_own(db, pid, s, eid, f)
        if was is None:
            raise HTTPException(404, "No such medication of your own.")
        activity.log(db, "medication", s, pid, medications.log_detail("own.change"), _client_ip(request))
    return _entry_reply(db, pid, {"id": eid, "key": was["key"]})


@router.delete("/api/profiles/{pid}/medications/own/{eid}")
def remove_own(pid: int, eid: int, request: Request, s=Depends(user), db=Depends(get_db)):
    """An own entry deleted; the reply says what it was ("removed": its fields and key), for an Undo to add it back."""
    need(db, s, pid, "editor")
    with dbm.transaction(db):
        was = medications.remove_own(db, pid, eid)
        if was is None:
            raise HTTPException(404, "No such medication of your own.")
        activity.log(db, "medication", s, pid, medications.log_detail("own.delete"), _client_ip(request))
    return _entry_reply(db, pid, {"removed": {k: v for k, v in was.items() if k != "own"}, "key": was["key"]})


@router.put("/api/profiles/{pid}/medications/listed")
def change_listed(pid: int, body: MedFields, request: Request, s=Depends(user), db=Depends(get_db)):
    """Your changes to a medication of the records (body.key): every field Edit shows, those as the records say them
    not kept. With none left it's as listed again (Put back as listed). "was": the change before, for an Undo."""
    need(db, s, pid, "editor")
    f = _fields(body, False)
    with dbm.transaction(db):
        before, eid = medications.set_change(db, pid, s, body.key, _listed(db, pid, body.key), fields=f)
        activity.log(db, "medication", s, pid, medications.log_detail("listed.change" if eid else "listed.back"), _client_ip(request))
    return _entry_reply(db, pid, {"id": eid, "key": body.key, "was": before})


@router.put("/api/profiles/{pid}/medications/hidden")
def hide_listed(pid: int, body: Hidden, request: Request, s=Depends(user), db=Depends(get_db)):
    """A medication of the records hidden, its records kept, or shown again (#1093)."""
    need(db, s, pid, "editor")
    with dbm.transaction(db):
        _, eid = medications.set_change(db, pid, s, body.key, _listed(db, pid, body.key), hidden=body.hidden)
        activity.log(db, "medication", s, pid, medications.log_detail("listed.hide" if body.hidden else "listed.show"), _client_ip(request))
    return _entry_reply(db, pid, {"id": eid, "key": body.key})
