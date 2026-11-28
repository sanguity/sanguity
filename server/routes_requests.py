"""Records requests (#1133; rec_requests.py): add, change, link to an import and remove a profile's requests for its
records. Viewers read them (in the records, as "requests", and here); owners and editors write. Each change's reply
carries the records whole (records.page_reply), and goes in the profile's activity log as "request" (what was done,
never who it went to).

  GET    /api/profiles/{pid}/requests                  {"requests": [...]}: an import's result asks which are open
  POST   /api/profiles/{pid}/requests                  {to, how, sent, rule, days, ext_days, extended, status, ...}
  PUT    /api/profiles/{pid}/requests/{rid}            the same, all of it
  PUT    /api/profiles/{pid}/requests/{rid}/import     {"import_id": n | null}: linked to an import of the profile
  DELETE /api/profiles/{pid}/requests/{rid}            -> {"removed": what it was, for an Undo to add again}
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import activity, db as dbm, rec_requests as rq, records, settings
from .main import _client_ip, get_db, need, user

router = APIRouter()


class RecordsRequest(BaseModel):
    to: str
    how: Optional[str] = None
    sent: Optional[str] = None                 # YYYY-MM-DD; today in the person's time zone when not given
    rule: str = "hipaa"                        # hipaa | gdpr | other
    days: Optional[int] = None                 # other: its number of days
    ext_days: Optional[int] = None             # other: its extension, if it has one
    extended: bool = False
    status: str = "waiting"                    # waiting | extended | received | closed
    received: Optional[str] = None
    from_: Optional[str] = Field(None, alias="from")   # the records wanted: from, until, kinds (none: all of them)
    until: Optional[str] = None
    kinds: Optional[str] = None
    format: str = "any"
    name: Optional[str] = None                 # the person's name, for the letter
    note: Optional[str] = None
    show: bool = False                         # its words in the calendar feed


class Link(BaseModel):
    import_id: Optional[int] = None


def _reply(db, pid, reply, status=200):
    body, rev = records.page_reply(db, pid, reply)
    return Response(body, status_code=status, media_type="application/json", headers={"X-Records-Rev": rev})


def _checked(db, s, body):
    try:
        return rq.check(body.model_dump(by_alias=True), settings.local_now(db, s["id"]).date())
    except rq.RequestError as x:
        raise HTTPException(400, str(x))


@router.get("/api/profiles/{pid}/requests")
def listing(pid: int, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "viewer")
    return {"requests": rq.listing(db, pid)}


@router.post("/api/profiles/{pid}/requests", status_code=201)
def add(pid: int, body: RecordsRequest, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    b = _checked(db, s, body)
    with dbm.transaction(db):
        rid = rq.add(db, pid, s, b)
        activity.log(db, "request", s, pid, rq.log_detail("add"), _client_ip(request))
    return _reply(db, pid, {"id": rid}, 201)


@router.put("/api/profiles/{pid}/requests/{rid}")
def change(pid: int, rid: int, body: RecordsRequest, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    b = _checked(db, s, body)
    with dbm.transaction(db):
        if rq.get(db, pid, rid) is None:
            raise HTTPException(404, "No such records request.")
        rq.change(db, pid, s, rid, b)
        activity.log(db, "request", s, pid, rq.log_detail("change"), _client_ip(request))
    return _reply(db, pid, {"id": rid})


@router.put("/api/profiles/{pid}/requests/{rid}/import")
def link(pid: int, rid: int, body: Link, request: Request, s=Depends(user), db=Depends(get_db)):
    """The records a request brought: linked to one of the profile's imports (received, if it was waiting), or not."""
    need(db, s, pid, "editor")
    zone = settings.effective(db, None, s["id"])["dates.timeZone"][0] or None
    with dbm.transaction(db):
        if rq.get(db, pid, rid) is None:
            raise HTTPException(404, "No such records request.")
        if not rq.link(db, pid, s, rid, body.import_id, zone):
            raise HTTPException(404, "No such import in this profile.")
        activity.log(db, "request", s, pid, rq.log_detail("link" if body.import_id is not None else "unlink"), _client_ip(request))
    return _reply(db, pid, {"id": rid})


@router.delete("/api/profiles/{pid}/requests/{rid}")
def remove(pid: int, rid: int, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    with dbm.transaction(db):
        was = rq.remove(db, pid, rid)
        if was is None:
            raise HTTPException(404, "No such records request.")
        activity.log(db, "request", s, pid, rq.log_detail("delete"), _client_ip(request))
    return _reply(db, pid, {"removed": was})
