"""Medication reminders (#162, med_reminders.py): list, set, change, turn off and remove a profile's reminders. Viewers
read them (the records the page loads list them too, as "reminders"); owners and editors set them. The calendar feed
(routes_calendar.py) lists those turned on. The medication log (#235, med_log.py): list, log, correct and remove doses,
the same way ("doses" in the records). The CSRF check in main.guard covers each change like every other, and each goes
in the profile's activity log as "medication" (what was done, how many times a day or the dose's status, never the
medication or a note)."""
from datetime import timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import activity, db as dbm, med_log, med_reminders as mr, settings
from .main import _client_ip, get_db, need, user

router = APIRouter()


class Reminder(BaseModel):
    med: str                       # the medication's grouping key (DATA.medications[].key)
    times: List[str]               # "HH:MM", 24-hour
    days: List[str]                # "mo".."su"; all seven: every day
    start: str                     # YYYY-MM-DD: from
    until: Optional[str] = None    # YYYY-MM-DD, or none
    tz: Optional[str] = None       # an IANA zone; none: the person's own (Settings -> Dates and times), else the server's UTC
    on: bool = True


class Switch(BaseModel):
    on: bool


def _zone(db, s, tz):
    if tz:
        return tz
    return settings.effective(db, None, s["id"])["dates.timeZone"][0] or "UTC"


def _checked(db, pid, s, body, keep_med=None):
    try:
        return mr.check(db, pid, body.med, body.times, body.days, body.start, body.until, _zone(db, s, body.tz), keep_med)
    except mr.ReminderError as x:
        raise HTTPException(400, str(x))


def _log(db, s, pid, change, request, r=None):
    activity.log(db, "medication", s, pid, mr.log_detail(change, r), _client_ip(request))


@router.get("/api/profiles/{pid}/medications/reminders")
def list_reminders(pid: int, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "viewer")
    return {"reminders": mr.listing(db, pid)}


@router.post("/api/profiles/{pid}/medications/reminders", status_code=201)
def add_reminder(pid: int, body: Reminder, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    r = _checked(db, pid, s, body)
    with dbm.transaction(db):
        rid = mr.add(db, pid, s, r, body.on)
        _log(db, s, pid, "reminder.add", request, r)
    return {"id": rid, "reminder": mr.get(db, pid, rid)}


@router.put("/api/profiles/{pid}/medications/reminders/{rid}")
def change_reminder(pid: int, rid: int, body: Reminder, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    was = mr.get(db, pid, rid)
    if was is None:
        raise HTTPException(404, "No such reminder.")
    r = _checked(db, pid, s, body, was.get("med"))
    with dbm.transaction(db):
        if mr.change(db, pid, rid, s, r, body.on) is None:
            raise HTTPException(404, "No such reminder.")
        _log(db, s, pid, "reminder.change", request, r)
    return {"reminder": mr.get(db, pid, rid)}


@router.patch("/api/profiles/{pid}/medications/reminders/{rid}")
def switch_reminder(pid: int, rid: int, body: Switch, request: Request, s=Depends(user), db=Depends(get_db)):
    """Turn a reminder on or off; off keeps it, listed nowhere, to turn on again."""
    need(db, s, pid, "editor")
    with dbm.transaction(db):
        if mr.change(db, pid, rid, s, active=body.on) is None:
            raise HTTPException(404, "No such reminder.")
        _log(db, s, pid, "reminder.on" if body.on else "reminder.off", request)
    return {"reminder": mr.get(db, pid, rid)}


@router.delete("/api/profiles/{pid}/medications/reminders/{rid}")
def remove_reminder(pid: int, rid: int, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    with dbm.transaction(db):
        if mr.remove(db, pid, rid) is None:
            raise HTTPException(404, "No such reminder.")
        _log(db, s, pid, "reminder.delete", request)
    return {"ok": True}


# ---------------------------------------------------------------- the medication log (#235)

class Dose(BaseModel):
    med: str                       # the medication's grouping key
    t: str                         # YYYY-MM-DDTHH:MM, the person's own wall-clock time
    status: str                    # taken, late, skipped
    note: Optional[str] = None
    slot: Optional[str] = None     # the reminder time ("HH:MM") it was logged against, from today's list


def _dose(db, pid, s, body, keep_med=None):
    today = settings.local_now(db, s["id"]).date() + timedelta(days=1)   # a day's grace: the browser's zone may be ahead
    try:
        return med_log.check(db, pid, body.med, body.t, body.status, body.note, body.slot, today, keep_med)
    except med_log.DoseError as x:
        raise HTTPException(400, str(x))


@router.get("/api/profiles/{pid}/medications/doses")
def list_doses(pid: int, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "viewer")
    return {"doses": med_log.listing(db, pid)}


@router.post("/api/profiles/{pid}/medications/doses", status_code=201)
def add_dose(pid: int, body: Dose, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    d = _dose(db, pid, s, body)
    with dbm.transaction(db):
        did = med_log.add(db, pid, s, d)
        activity.log(db, "medication", s, pid, med_log.log_detail("add", d), _client_ip(request))
    return {"id": did}


@router.put("/api/profiles/{pid}/medications/doses/{did}")
def change_dose(pid: int, did: int, body: Dose, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    was = med_log.get(db, pid, did)
    if was is None:
        raise HTTPException(404, "No such dose in the log.")
    d = _dose(db, pid, s, body, was.get("med"))
    with dbm.transaction(db):
        if med_log.change(db, pid, did, s, d) is None:
            raise HTTPException(404, "No such dose in the log.")
        activity.log(db, "medication", s, pid, med_log.log_detail("change", d), _client_ip(request))
    return {"ok": True}


@router.delete("/api/profiles/{pid}/medications/doses/{did}")
def remove_dose(pid: int, did: int, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    with dbm.transaction(db):
        was = med_log.remove(db, pid, did)
        if was is None:
            raise HTTPException(404, "No such dose in the log.")
        activity.log(db, "medication", s, pid, med_log.log_detail("delete", was), _client_ip(request))
    return {"ok": True}
