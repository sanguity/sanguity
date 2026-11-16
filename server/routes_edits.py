"""Corrections made in the app (edits.py): list, add and remove a profile's edits. Owners and editors only (viewers
see what was edited in the records the page loads, under "edits"); the CSRF check in main.guard covers the POST
and DELETE like every other change, and each change goes in the profile's activity log."""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import activity, db as dbm, edits
from .main import _client_ip, get_db, need, user

router = APIRouter()


class NewEdit(BaseModel):
    event: int                                    # the event's id in the records the page has now
    action: Literal["hide", "rename", "date", "not_stay", "merge"]
    title: Optional[str] = None                   # rename
    t: Optional[str] = None                       # date: YYYY-MM-DD or YYYY-MM-DDTHH:MM[:SS]
    other: Optional[int] = None                   # merge: the event folded into this one


@router.get("/api/profiles/{pid}/edits")
def list_edits(pid: int, s=Depends(user), db=Depends(get_db)):
    """The profile's edits, oldest first, each with whether it found its event in the current records."""
    need(db, s, pid, "editor")
    return {"edits": edits.listing(db, pid)}


@router.post("/api/profiles/{pid}/edits", status_code=201)
def add_edit(pid: int, body: NewEdit, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    try:
        with dbm.transaction(db):
            eid, e = edits.add(db, pid, s["id"], body.action, body.event, title=body.title, t=body.t, other_id=body.other)
            activity.log(db, "edit", s, pid, {"action": body.action, "title": e.get("title"),
                                              **({"to": body.title.strip()} if body.action == "rename" else {})},
                         _client_ip(request))
    except edits.EditError as x:
        raise HTTPException(400, str(x))
    return {"id": eid}


@router.delete("/api/profiles/{pid}/edits/{edit_id}")
def remove_edit(pid: int, edit_id: int, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    with dbm.transaction(db):
        gone = edits.remove(db, pid, edit_id)
        if gone is None:
            raise HTTPException(404, "No such edit.")
        activity.log(db, "edit", s, pid, {"action": gone["action"], "title": (gone["was"] or {}).get("title"), "undone": True},
                     _client_ip(request))
    return {"ok": True}
