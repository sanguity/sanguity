"""Family history (#1134, family.py): a profile's own entries, added, changed and removed, and the records' hidden or
shown again, by owners and editors. Viewers read them in the records the page loads (DATA.familyHistory, its own
entries with "own"; DATA.hiddenFamily). Each reply carries the records whole, "records" (records.page_reply, #271), with
"key": the entry's. Logged as "family": what was done, never the relative or a condition."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from . import activity, db as dbm, family, records
from .main import _client_ip, get_db, need, user

router = APIRouter()


class Condition(BaseModel):
    name: Optional[str] = None
    onset: Optional[str] = None          # the age it began at, as typed: "52"


class FamilyFields(BaseModel):
    relation: Optional[str] = None       # "Mother", "Maternal grandfather"
    name: Optional[str] = None
    conditions: List[Condition] = []
    status: Optional[str] = None         # "living", "deceased", or none: not said
    deathAge: Optional[str] = None
    note: Optional[str] = None
    key: Optional[str] = None            # an own entry's, its removal undone (POST)


class Hidden(BaseModel):
    key: str
    hidden: bool


def _reply(db, pid, reply, status=200):
    body, rev = records.page_reply(db, pid, reply)
    return Response(body, status_code=status, media_type="application/json", headers={"X-Records-Rev": rev})


def _fields(body):
    try:
        return family.check({**body.model_dump(exclude={"key", "conditions"}), "conditions": [c.model_dump() for c in body.conditions]})
    except family.EntryError as x:
        raise HTTPException(400, str(x))


def _log(db, s, pid, change, request):
    activity.log(db, "family", s, pid, family.log_detail(change), _client_ip(request))


@router.post("/api/profiles/{pid}/family/own", status_code=201)
def add_own(pid: int, body: FamilyFields, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    f = _fields(body)
    try:
        with dbm.transaction(db):
            eid = family.add_own(db, pid, s, f, body.key)
            _log(db, s, pid, "back" if body.key else "add", request)
    except family.EntryError as x:
        raise HTTPException(400, str(x))
    return _reply(db, pid, {"id": eid, "key": family.get(db, pid, eid)["key"]}, 201)


@router.put("/api/profiles/{pid}/family/own/{eid}")
def change_own(pid: int, eid: int, body: FamilyFields, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    f = _fields(body)
    with dbm.transaction(db):
        was = family.change_own(db, pid, s, eid, f)
        if was is None:
            raise HTTPException(404, "No such family history entry of your own.")
        _log(db, s, pid, "change", request)
    return _reply(db, pid, {"id": eid, "key": was["key"]})


@router.delete("/api/profiles/{pid}/family/own/{eid}")
def remove_own(pid: int, eid: int, request: Request, s=Depends(user), db=Depends(get_db)):
    """An own entry deleted; the reply says what it was ("removed": its fields and key), for an Undo to add it back."""
    need(db, s, pid, "editor")
    with dbm.transaction(db):
        was = family.remove_own(db, pid, eid)
        if was is None:
            raise HTTPException(404, "No such family history entry of your own.")
        _log(db, s, pid, "delete", request)
    st = "" if "deceased" not in was else "deceased" if was["deceased"] else "living"
    return _reply(db, pid, {"removed": {**{k: v for k, v in was.items() if k not in ("own", "deceased")}, "status": st},
                            "key": was["key"]})


@router.put("/api/profiles/{pid}/family/hidden")
def hide(pid: int, body: Hidden, request: Request, s=Depends(user), db=Depends(get_db)):
    """An entry of the records' hidden, its records kept, or shown again."""
    need(db, s, pid, "editor")
    with dbm.transaction(db):
        if family.is_own(body.key) or body.key not in family.record_keys(db, pid):
            raise HTTPException(404, "No such family history entry in the records.")
        eid = family.set_hidden(db, pid, s, body.key, body.hidden)
        _log(db, s, pid, "hide" if body.hidden else "show", request)
    return _reply(db, pid, {"id": eid, "key": body.key})
