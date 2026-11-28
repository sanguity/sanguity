"""Follow-ups from a visit (#1138; followups.py): add, change (its words, date, record, done or not) and remove a
profile's follow-ups. Viewers read them in the records ("followups"); owners and editors write. Each change's reply
carries the records whole (records.page_reply), as an own medication's does, and goes in the profile's activity log
as "followup" (what was done, never what it says).

  POST   /api/profiles/{pid}/followups          {text, due | in_n + in_unit (+ from), about, show, done} -> {"id"}
  PUT    /api/profiles/{pid}/followups/{fid}    the same, all of it
  DELETE /api/profiles/{pid}/followups/{fid}    -> {"removed": what it was, for an Undo to add again}
"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import activity, db as dbm, followups, records, settings
from .main import _client_ip, get_db, need, user

router = APIRouter()


class FollowUp(BaseModel):
    text: str
    due: Optional[str] = None                  # YYYY-MM-DD
    in_n: Optional[int] = None                 # or a number of days, weeks or months from the visit
    in_unit: Optional[Literal["days", "weeks", "months"]] = None
    from_: Optional[str] = Field(None, alias="from")   # the day that counts from (the visit's); else its record's, else today
    said: Optional[str] = None                 # "in 3 months" with from: its wording kept with due (an Undo)
    about: Optional[dict] = None               # {id, title, t}: the record it's about
    show: bool = False                         # its words in the calendar feed
    done: bool = False


def _reply(db, pid, reply, status=200):
    body, rev = records.page_reply(db, pid, reply)
    return Response(body, status_code=status, media_type="application/json", headers={"X-Records-Rev": rev})


def _checked(db, s, body, was=None):
    try:
        return followups.check(body.model_dump(by_alias=True), settings.local_now(db, s["id"]).date(), was)
    except followups.FollowUpError as x:
        raise HTTPException(400, str(x))


@router.post("/api/profiles/{pid}/followups", status_code=201)
def add(pid: int, body: FollowUp, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    f = _checked(db, s, body)
    with dbm.transaction(db):
        fid = followups.add(db, pid, s, f)
        activity.log(db, "followup", s, pid, followups.log_detail("add"), _client_ip(request))
    return _reply(db, pid, {"id": fid}, 201)


@router.put("/api/profiles/{pid}/followups/{fid}")
def change(pid: int, fid: int, body: FollowUp, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    with dbm.transaction(db):
        was = followups.get(db, pid, fid)
        if was is None:
            raise HTTPException(404, "No such follow-up.")
        f = _checked(db, s, body, was)
        followups.change(db, pid, s, fid, f)
        what = "done" if f["done"] and not was.get("done") else "undone" if was.get("done") and not f["done"] else "change"
        activity.log(db, "followup", s, pid, followups.log_detail(what), _client_ip(request))
    return _reply(db, pid, {"id": fid})


@router.delete("/api/profiles/{pid}/followups/{fid}")
def remove(pid: int, fid: int, request: Request, s=Depends(user), db=Depends(get_db)):
    """The reply's "removed" is what it was, in the fields POST takes, for an Undo to add it again."""
    need(db, s, pid, "editor")
    with dbm.transaction(db):
        was = followups.remove(db, pid, fid)
        if was is None:
            raise HTTPException(404, "No such follow-up.")
        activity.log(db, "followup", s, pid, followups.log_detail("delete"), _client_ip(request))
    return _reply(db, pid, {"removed": was})
