"""SMART Health Links and Cards pasted on the import page (#94; the format is shl.py's).

  POST /api/profiles/{pid}/shl/inspect  {"link"}                          what the link says (host, label, flags,
                                                                         expiry): nothing is fetched
  POST /api/profiles/{pid}/shl/import   {"link", "passcode"?, "confirm": true}   fetch it (the one outside request,
                                         made through net.py and recorded), decrypt, store the files as an upload and queue
                                         its import: {"upload", "job", "host", "cards", "notes"}

The import then goes as any upload's does (jobs.py): the page polls the job to its preview, then saves or cancels.
Owners and editors only; changes need the X-Sanguity header like every API write. The key and the passcode are
never logged or stored: only the host goes into the activity log.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import activity, jobs, outside, shl, uploads
from .main import _client_ip, get_db, need, user

router = APIRouter()


class Pasted(BaseModel):
    link: str = Field(max_length=20000)


class Fetch(Pasted):
    passcode: Optional[str] = Field(None, max_length=200)
    confirm: bool = False


def _refused(e):
    # 401 would send the page to the sign-in screen: a wrong passcode is 403
    status = 403 if e.status == 401 else e.status
    return HTTPException(status, str(e))


@router.post("/api/profiles/{pid}/shl/inspect")
def inspect_link(pid: int, body: Pasted, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    try:
        return shl.describe(shl.parse(body.link))
    except shl.Refused as e:
        raise _refused(e) from None


@router.post("/api/profiles/{pid}/shl/import", status_code=202)
def import_link(pid: int, body: Fetch, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    try:
        link = shl.parse(body.link)
    except shl.Refused as e:
        raise _refused(e) from None
    if not body.confirm:   # the page shows what the link says first; fetching is the person's choice
        raise HTTPException(400, "Nothing was fetched: confirm first.")
    ip = _client_ip(request)

    permit = outside.for_link(db, pid, shl.describe(link)["host"] if link.kind == "link" else None, s, ip)
    try:
        items = shl.card_items(link) if link.kind == "card" else shl.fetch(link, body.passcode, permit)
        got = shl.prepare(items, permit)
    except shl.Refused as e:
        raise _refused(e) from None
    host = shl.describe(link)["host"]
    up = uploads.create(db, pid, s["id"])
    try:
        for rel, data in got["files"].items():
            uploads.add_file(db, pid, up, rel, [data])
        done = uploads.finish(db, pid, up, unzip=False)
    except uploads.UploadError as e:
        uploads.discard(db, pid, up)
        raise HTTPException(e.status, str(e)) from None
    activity.log(db, "upload", s, pid, {"upload": up, **done, "source": "smart-health-link", "host": host}, ip)
    jid = jobs.submit(db, pid, up, s["id"], "fhir", ip)
    return {"upload": up, "job": jid, "host": host, "cards": got["cards"], "notes": got["notes"]}
