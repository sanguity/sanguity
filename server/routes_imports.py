"""A profile's import history and undoing an import (history.py). Anyone the profile is shared with sees the history;
owners and editors may undo. The CSRF check in main.guard covers the POST like every other change."""
from fastapi import APIRouter, Depends, HTTPException, Request

from . import history
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
