"""Insurance cards (#1137; insurance.py): a profile's health plans, each with a photo of its card's front and back.

  GET    /api/profiles/{pid}/insurance                    the plans (viewers), and whether the caller can change them
  POST   /api/profiles/{pid}/insurance                    {name, member, group, phones}: a new plan -> {"id", "plans"}
  PUT    /api/profiles/{pid}/insurance/{id}               the same fields, in place of its own (its photos stay)
  DELETE /api/profiles/{pid}/insurance/{id}               the plan and its photos
  PUT    /api/profiles/{pid}/insurance/{id}/{side}        side front or back: the request body is the picture (checked and
                                                          cleaned as a journal photo is); nginx lets this one be large
  GET    /api/profiles/{pid}/insurance/{id}/{side}        that photo, to anyone who can view the profile
  DELETE /api/profiles/{pid}/insurance/{id}/{side}        take it off

Owners and editors change them. Each change goes in the profile's activity log as "insurance": what was done, and for
a photo which side, never the plan's name or numbers (a member ID is never logged anywhere)."""
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import activity, db as dbm, insurance, journal_photos as photos
from .main import DOC_CSP, _client_ip, get_db, need, user

router = APIRouter()
Side = Literal["front", "back"]


class Phone(BaseModel):
    label: Optional[str] = Field(None, max_length=200)
    number: Optional[str] = Field(None, max_length=200)


class Plan(BaseModel):
    name: str = Field("", max_length=500)
    member: Optional[str] = Field(None, max_length=500)
    group: Optional[str] = Field(None, max_length=500)
    phones: List[Phone] = Field(default_factory=list, max_length=20)


def _checked(body):
    try:
        return insurance.check(body.model_dump())
    except insurance.InsuranceError as x:
        raise HTTPException(x.status, str(x))


def _log(db, s, pid, request, change, side=None):
    activity.log(db, "insurance", s, pid, {"change": change, **({"side": side} if side else {})}, _client_ip(request))


@router.get("/api/profiles/{pid}/insurance")
def plans(pid: int, s=Depends(user), db=Depends(get_db)):
    role = need(db, s, pid, "viewer")
    return {"plans": insurance.listing(db, pid), "can_change": role in ("owner", "editor")}


@router.post("/api/profiles/{pid}/insurance", status_code=201)
def add(pid: int, body: Plan, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    plan = _checked(body)
    with dbm.transaction(db):
        try:
            iid = insurance.add(db, pid, s, plan)
        except insurance.InsuranceError as x:
            raise HTTPException(x.status, str(x))
        _log(db, s, pid, request, "add")
    return {"id": iid, "plans": insurance.listing(db, pid)}


@router.put("/api/profiles/{pid}/insurance/{iid}")
def change(pid: int, iid: int, body: Plan, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    plan = _checked(body)
    with dbm.transaction(db):
        if not insurance.change(db, pid, iid, s, plan):
            raise HTTPException(404, "That plan isn't here any more.")
        _log(db, s, pid, request, "change")
    return {"id": iid, "plans": insurance.listing(db, pid)}


@router.delete("/api/profiles/{pid}/insurance/{iid}")
def remove(pid: int, iid: int, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    with dbm.transaction(db):
        if not insurance.remove(db, pid, iid):
            raise HTTPException(404, "That plan isn't here any more.")
        _log(db, s, pid, request, "delete")
    return {"plans": insurance.listing(db, pid)}


def _store(db, pid, iid, side, data, s, ip):
    with dbm.transaction(db):
        got = insurance.set_photo(db, pid, iid, side, data, s)
        activity.log(db, "insurance", s, pid, {"change": "photo", "side": side}, ip)
        return got


@router.put("/api/profiles/{pid}/insurance/{iid}/{side}", status_code=201)
async def add_photo(pid: int, iid: int, side: Side, request: Request, s=Depends(user), db=Depends(get_db)):
    """A side's photo, checked, its metadata taken out, sealed. Refused before reading past the limit."""
    from starlette.concurrency import run_in_threadpool
    await run_in_threadpool(need, db, s, pid, "editor")
    try:
        if int(request.headers.get("content-length") or 0) > photos.max_bytes():
            raise photos.too_big()
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > photos.max_bytes():
                raise photos.too_big()
        got = await run_in_threadpool(_store, db, pid, iid, side, bytes(data), s, _client_ip(request))
    except ValueError as x:   # PhotoError, InsuranceError, or a content-length that isn't a number
        raise HTTPException(getattr(x, "status", 400), str(x) if hasattr(x, "status") else "Send the photo as the request body.")
    return {**got, "plans": await run_in_threadpool(insurance.listing, db, pid)}


@router.get("/api/profiles/{pid}/insurance/{iid}/{side}")
def photo(pid: int, iid: int, side: Side, s=Depends(user), db=Depends(get_db)):
    """A side's photo, shown inline under the documents' strict policy (nothing in it can run or load)."""
    need(db, s, pid, "viewer")
    got = insurance.photo(db, pid, iid, side)
    if got is None:
        raise HTTPException(404, "That photo isn't here.")
    data, media = got
    return Response(data, media_type=media, headers={"Content-Disposition": f'inline; filename="card-{iid}-{side}"',
                                                     "Content-Security-Policy": DOC_CSP, "X-Frame-Options": "SAMEORIGIN",
                                                     "Cache-Control": "no-store"})


@router.delete("/api/profiles/{pid}/insurance/{iid}/{side}")
def drop_photo(pid: int, iid: int, side: Side, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    with dbm.transaction(db):
        if not insurance.drop_photo(db, pid, iid, side, s):
            raise HTTPException(404, "That photo isn't here.")
        _log(db, s, pid, request, "photo.delete", side)
    return {"plans": insurance.listing(db, pid)}
