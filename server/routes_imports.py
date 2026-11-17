"""A profile's import history and undoing an import (history.py). Anyone the profile is shared with sees the history;
owners and editors may undo, and give an import its own name (#38). The CSRF check in main.guard covers the POST
and PUT like every other change."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import activity, db as dbm, history, records
from .main import _client_ip, get_db, need, user

router = APIRouter()


@router.get("/api/profiles/{pid}/imports")
def list_imports(pid: int, s=Depends(user), db=Depends(get_db)):
    """{"can_undo": whether the caller may undo, "imports": newest first, undone ones included}."""
    role = need(db, s, pid, "viewer")
    return {"can_undo": role in ("owner", "editor"), "imports": history.entries(db, pid)}


@router.post("/api/profiles/{pid}/imports/{iid}/undo")
def undo_import(pid: int, iid: int, request: Request, s=Depends(user), db=Depends(get_db)):
    """Remove everything the import brought. Returns its history entry, now undone."""
    need(db, s, pid, "editor")
    try:
        return history.undo(db, pid, iid, s, _client_ip(request))
    except history.Refused as e:
        raise HTTPException(e.status, str(e)) from None


class Name(BaseModel):
    name: Optional[str] = None


@router.put("/api/profiles/{pid}/imports/{iid}/name")
def name_import(pid: int, iid: int, body: Name, request: Request, s=Depends(user), db=Depends(get_db)):
    """Name the import (shown in place of its importer's label wherever a record says where it came from), or, with
    no name, go back to the label. Returns {"id", "label", "name"}."""
    need(db, s, pid, "editor")
    try:
        with dbm.transaction(db):
            done = records.rename_import(db, pid, iid, body.name)
            if done is None:
                raise HTTPException(404, "There's no such import in this profile.")
            label, name = done
            activity.log(db, "source", s, pid, {"import": iid, "label": label, "named": name is not None}, _client_ip(request))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return {"id": iid, "label": label, "name": name}
