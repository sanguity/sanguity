"""Corrections made in the app (edits.py): list, add and remove a profile's edits. Owners and editors only (viewers
see what was edited in the records the page loads, under "edits"); the CSRF check in main.guard covers the POST
and DELETE like every other change, and each change goes in the profile's activity log."""
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import activity, db as dbm, edits
from .main import _client_ip, get_db, need, user

router = APIRouter()


class NewEdit(BaseModel):
    event: int                                    # the event's id in the records the page has now
    action: Literal["hide", "rename", "date", "not_stay", "merge", "kind"]
    title: Optional[str] = None                   # rename
    t: Optional[str] = None                       # date: YYYY-MM-DD or YYYY-MM-DDTHH:MM[:SS]
    other: Optional[int] = None                   # merge: the event folded into this one
    k: Optional[str] = None                       # kind: the category it moves to (edits.MOVABLE)
    also: Optional[List[int]] = None              # rename, kind: similar entries changed with it (GET .../similar)
    rule: Optional[bool] = None                   # kind: keep doing it for this source's new imports


@router.get("/api/profiles/{pid}/edits")
def list_edits(pid: int, s=Depends(user), db=Depends(get_db)):
    """The profile's edits, oldest first, each with whether it found its event in the current records."""
    need(db, s, pid, "editor")
    return {"edits": edits.listing(db, pid)}


@router.get("/api/profiles/{pid}/edits/similar")
def similar_entries(pid: int, event: int, s=Depends(user), db=Depends(get_db)):
    """The entries that look like this one (the same source, kind and title as the page shows them), offered for a
    rename or a change of category made to them all at once (#237)."""
    need(db, s, pid, "editor")
    try:
        return edits.similar(db, pid, event)
    except edits.EditError as x:
        raise HTTPException(404, str(x))


@router.post("/api/profiles/{pid}/edits", status_code=201)
def add_edit(pid: int, body: NewEdit, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    try:
        with dbm.transaction(db):
            if body.action in ("rename", "kind") and (body.also or body.rule):
                ids, e, rule = edits.add_batch(db, pid, s["id"], body.action, body.event, also=body.also or (),
                                               title=body.title, k=body.k, rule=bool(body.rule))
            else:
                ids, rule = None, None
                eid, e = edits.add(db, pid, s["id"], body.action, body.event, title=body.title, t=body.t,
                                   other_id=body.other, k=body.k)
            activity.log(db, "edit", s, pid, {"action": body.action, "title": e.get("title"),
                                              **({"to": body.title.strip()} if body.action == "rename" else {}),
                                              **({"to": body.k} if body.action == "kind" else {}),
                                              **({"similar": len(ids) - 1} if ids else {}),
                                              **({"rule": True} if rule else {})},
                         _client_ip(request))
    except edits.EditError as x:
        raise HTTPException(400, str(x))
    return {"id": ids[0], "ids": ids, "rule": rule} if ids else {"id": eid}


@router.delete("/api/profiles/{pid}/edits/{edit_id}")
def remove_edit(pid: int, edit_id: int, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    with dbm.transaction(db):
        gone = edits.remove(db, pid, edit_id)
        if gone is None:
            raise HTTPException(404, "No such edit.")
        activity.log(db, "edit", s, pid, {"action": gone["action"], "title": (gone["was"] or {}).get("title"), "undone": True,
                                          **({"with": gone["n"] - 1} if gone["n"] > 1 else {})}, _client_ip(request))
    return {"ok": True, "n": gone["n"]}
