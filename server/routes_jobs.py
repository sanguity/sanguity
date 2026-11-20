"""Importing an upload into a profile, in the background (jobs.py): start it, poll it until it shows its preview,
then confirm (save) or discard it. Owners and editors only; the CSRF check in main.guard covers the POSTs like every
other change.

  POST /api/profiles/{pid}/uploads/{id}/import   start: {"id": job id}
  GET  /api/profiles/{pid}/jobs[/{id}]           how it goes; in state "preview", what it would add (and a few of
                                                 each, "samples", #282) and the duplicates
  POST /api/profiles/{pid}/jobs/{id}/confirm     save it, {"keep": [refs of duplicates to save anyway],
                                                 "labs": {series ref: series name, or null to keep its own},
                                                 "same": [refs of possible matches that are the same result]} optional
  POST /api/profiles/{pid}/jobs/{id}/discard     throw it away, with its upload unless another job uses it
"""
from typing import Dict, List, Optional

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
        if state in ("queued", "running", "preview"):
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


class Confirm(BaseModel):
    keep: List[str] = []   # duplicates to save anyway, by the refs the preview lists them with
    labs: Dict[str, Optional[str]] = {}   # lab series the preview listed: the series to put each in (labmap.py)
    same: List[str] = []   # possible matches from other sources (crossmatch.py) confirmed as the same result


@router.post("/api/profiles/{pid}/jobs/{jid}/confirm", status_code=202)
def confirm_job(pid: int, jid: int, request: Request, body: Optional[Confirm] = None, s=Depends(user), db=Depends(get_db)):
    """Save a previewed import, skipping the duplicates except those in keep. It saves in the background: poll
    GET .../jobs/{id} until it's done."""
    need(db, s, pid, "editor")
    try:
        jobs.confirm(db, pid, jid, s["id"], body.keep if body else None, _client_ip(request), body.labs if body else None,
                     body.same if body else None)
    except jobs.Refused as e:
        raise HTTPException(e.status, str(e)) from None
    return {"id": jid}


@router.post("/api/profiles/{pid}/jobs/{jid}/discard")
def discard_job(pid: int, jid: int, request: Request, s=Depends(user), db=Depends(get_db)):
    """Cancel a previewed import: nothing is saved, and the preview and the upload are removed."""
    need(db, s, pid, "editor")
    try:
        jobs.discard(db, pid, jid, s, _client_ip(request))
    except jobs.Refused as e:
        raise HTTPException(e.status, str(e)) from None
    return {"ok": True}
