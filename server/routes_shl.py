"""SMART Health Links and Cards pasted on the import page (#94; the format is shl.py's).

  POST /api/profiles/{pid}/shl/inspect  {"link"}                          what the link says (host, label, flags,
                                                                         expiry): nothing is fetched
  POST /api/profiles/{pid}/shl/import   {"link", "passcode"?, "confirm": true, "hosts"?: [...]}   fetch it (made
                                         through net.py and recorded), decrypt, store the files as an upload and queue
                                         its import: {"upload", "job", "host", "cards", "notes"}

What's contacted is what the person confirmed (#919): the link's host, or a pasted Health Card's issuer, as the page
names it; and "hosts", the others the link itself names that the page named and they confirmed. A file's location on
another host, or a Health Card's issuer (for its keys), not among them is never contacted: the answer is 409,
{"detail": {"message", "hosts", "files", "issuers"}}, naming them, and the page asks, then sends the link again with
them in "hosts".

The import then goes as any upload's does (jobs.py): the page polls the job to its preview, then saves or cancels.
Owners and editors only; changes need the X-Sanguity header like every API write. The key and the passcode are
never logged or stored: only the host goes into the activity log.
"""
from typing import List, Optional

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
    hosts: List[str] = Field(default_factory=list, max_length=40)   # others the page named and they confirmed (#919)


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

    # the link's host (a pasted card's issuer), as the page named it, and what else it named that was confirmed
    permit = outside.for_link(db, pid, shl.describe(link)["host"], s, ip, also=[h[:300] for h in body.hosts])
    try:
        items = shl.card_items(link) if link.kind == "card" else shl.fetch(link, body.passcode, permit)
        got = shl.prepare(items, permit)
    except shl.Ask as e:   # hosts the link names that weren't confirmed: none of them contacted (#919)
        raise HTTPException(409, {"message": str(e), "hosts": e.hosts, "files": e.files, "issuers": e.issuers}) from None
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
