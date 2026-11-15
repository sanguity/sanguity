"""Importing an upload into a profile, in the background (jobs.py): start it, then poll it. Owners and editors only;
the CSRF check in main.guard covers the POST like every other change."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import importers as registry, jobs
from .main import _client_ip, get_db, need, user

router = APIRouter()


class StartImport(BaseModel):
    importer: Optional[str] = None   # a NAME, to read the upload as that format instead of detecting it


@router.post("/api/profiles/{pid}/uploads/{upload_id}/import", status_code=202)
def start_import(pid: int, upload_id: str, request: Request, body: Optional[StartImport] = None,
                 s=Depends(user), db=Depends(get_db)):
    """Queue an import of the upload. Returns {"id": job id}; GET .../jobs/{id} says how it goes. Asking again while
    it's queued or running returns the same job; an upload already imported isn't imported twice."""
    need(db, s, pid, "editor")
    if not jobs.upload_exists(db, pid, upload_id):
        raise HTTPException(404, "No such upload.")
    upload_id = int(upload_id)
    name = (body.importer or "").strip() if body else ""
    if name and not registry.get(name):
        raise HTTPException(400, f"There's no importer called {name[:40]!r}.")
    for jid, state in jobs.for_upload(db, pid, upload_id):
        if state in ("queued", "running"):
            return {"id": jid}
        if state == "done":
            raise HTTPException(409, "This upload has been imported already.")
    return {"id": jobs.submit(db, pid, upload_id, s["id"], name or None, _client_ip(request))}


@router.get("/api/profiles/{pid}/jobs")
def list_jobs(pid: int, s=Depends(user), db=Depends(get_db)):
    """The profile's recent imports, newest first."""
    need(db, s, pid, "editor")
    return jobs.recent(db, pid)


@router.get("/api/profiles/{pid}/jobs/{jid}")
def get_job(pid: int, jid: int, s=Depends(user), db=Depends(get_db)):
    """state, stage, importer, counts by kind, the date range, warnings and the message (when it failed)."""
    need(db, s, pid, "editor")
    j = jobs.get(db, pid, jid)
    if not j:
        raise HTTPException(404, "No such import.")
    return j
