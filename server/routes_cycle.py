"""Menstrual cycle tracking, opt-in (#183; cycle_tracking.py has the setting, cycle.py the records and the privacy rules).

  GET  /api/profiles/{pid}/cycle          owners and editors: whether it's on, who may change it, the latest deletion
                                          and whether it's done, and, while on, the spans and marks the page shows
  PUT  /api/profiles/{pid}/cycle          owners: {"on": true | false}. Turning it off deletes everything kept
  POST /api/profiles/{pid}/cycle/delete   owners: delete everything kept, leaving it on

Viewers get 403 from all three, and the page asks only for owners and editors. Each change goes in the profile's
activity log (on, off, delete: never a record), and the receiver is told at once (watch_keys.publish). The CSRF check
in main.guard covers the PUT and POST like every other change.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import activity, cycle_tracking as ct, db as dbm, watch_keys
from .main import _client_ip, get_db, need, user

router = APIRouter()


class Turn(BaseModel):
    on: bool


@router.get("/api/profiles/{pid}/cycle")
def cycle_status(pid: int, s=Depends(user), db=Depends(get_db)):
    role = need(db, s, pid, "editor")
    return ct.status(db, pid, role)


@router.put("/api/profiles/{pid}/cycle")
def turn(pid: int, body: Turn, request: Request, s=Depends(user), db=Depends(get_db)):
    role = need(db, s, pid, "owner")
    if body.on and not watch_keys.ready():
        raise HTTPException(409, "The watch receiver can't be told yet, so it would leave cycle records out anyway: "
                                 "its secrets folder isn't set up on the server.")
    with dbm.transaction(db):
        changed = ct.turn(db, pid, body.on, s["id"])
        if changed:
            activity.log(db, "cycle", s, pid, {"change": "on" if body.on else "off"}, _client_ip(request))
    if changed and not body.on:
        ct.drop_imported(db, pid, s, _client_ip(request))   # what uploaded exports brought (#223), once it's off (#526)
    watch_keys.publish(db)
    return ct.status(db, pid, role)


@router.post("/api/profiles/{pid}/cycle/delete")
def delete_all(pid: int, request: Request, s=Depends(user), db=Depends(get_db)):
    role = need(db, s, pid, "owner")
    with dbm.transaction(db):
        ct.delete_all(db, pid, s["id"])
        activity.log(db, "cycle", s, pid, {"change": "delete"}, _client_ip(request))
    ct.drop_imported(db, pid, s, _client_ip(request))   # (#223, #526; a preview left with nothing cancelled as s, #539)
    watch_keys.publish(db)
    return ct.status(db, pid, role)
