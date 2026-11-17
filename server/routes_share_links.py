"""Share links (#47; share_links.py): owners make and revoke them, and the holder of one opens it.

  GET    /api/profiles/{pid}/links         the profile's links (never their tokens), the sections to choose from and
                                           the profile's lab names (owners)
  POST   /api/profiles/{pid}/links         {label, sections, labs, from, to, hours}: a new link; its address is in
                                           the answer, the only time the token exists outside the link (owners)
  DELETE /api/profiles/{pid}/links/{id}    revoke it, at once (owners: any link of the profile, whoever made it)
  POST   /api/share/view                   {"token"}: what the link shows. No session: the token is the credential

Making and revoking are logged in the profile's activity as "link", and so is every view, under the actor "share
link", with the viewer's address and never the token.

The view is the one API route that needs no sign-in, so it gives nothing away: a token that's wrong, malformed,
expired or revoked, or whose maker is no longer an owner, and a body that isn't JSON, all get the same bare 404. It
ignores any cookie the browser sends and sets none (nginx strips them before it, too), and it's throttled per
address for wrong tokens and per link for every view (share_links.throttle). It's a POST so the token travels in
the body, never in a URL (ASVS 8.1.3); the usual cross-site check applies, which the share page passes like any.
"""
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import activity, db as dbm, profiles as prof, share_links as links
from .main import _audit, _client_ip, get_db, need, user

router = APIRouter()


class LinkIn(BaseModel):
    label: str = Field("", max_length=links.LABEL_MAX * 2)
    sections: List[str] = Field(default_factory=list, max_length=len(links.SECTIONS))
    labs: List[str] = Field(default_factory=list, max_length=links.MAX_LABS)
    start: Optional[str] = Field(None, alias="from", max_length=10)
    end: Optional[str] = Field(None, alias="to", max_length=10)
    hours: int = 7 * 24


@router.get("/api/profiles/{pid}/links")
def state(pid: int, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "owner")
    prof.sync_build(db, pid)
    return {"links": links.listing(db, pid), "labs": links.lab_names(db, pid), "max_hours": links.MAX_HOURS,
            "sections": [{"id": i, "label": l, "what": w} for i, l, w in links.SECTIONS]}


@router.post("/api/profiles/{pid}/links")
def make(pid: int, body: LinkIn, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "owner")
    try:
        label, scope = links.clean(body.label, body.sections, body.labs, body.start, body.end, body.hours)
        with dbm.transaction(db):
            token, lid = links.create(db, pid, s, label, scope, body.hours)
            activity.log(db, "link", s, pid, {"change": "create", "link": lid, **({"label": label} if label else {}),
                                              "sections": scope["sections"], "labs": len(scope["labs"]),
                                              "from": scope["from"], "to": scope["to"], "hours": body.hours}, _client_ip(request))
    except links.LinkError as e:
        raise HTTPException(400, str(e)) from None
    return {"path": "share.html#" + token, "link": links.get(db, pid, lid)}


@router.delete("/api/profiles/{pid}/links/{lid}")
def revoke(pid: int, lid: int, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "owner")
    with dbm.transaction(db):
        label = links.revoke(db, pid, lid, s)
        if label is None:
            raise HTTPException(404, "There's no such link, or it was already revoked.")
        activity.log(db, "link", s, pid, {"change": "revoke", "link": lid, **({"label": label} if label else {})}, _client_ip(request))
    return {"ok": True, "link": links.get(db, pid, lid)}


# ---------------------------------------------------------------- the holder's view

def _bare(status=404, retry=None):
    """The same answer for any refusal, so it tells nothing about which links exist or why one doesn't work."""
    headers = {"Cache-Control": "no-store"}
    if retry:
        headers["Retry-After"] = str(retry)
    return Response("Not found.\n" if status == 404 else "Too many requests. Try again later.\n", status_code=status,
                    media_type="text/plain; charset=utf-8", headers=headers)


async def _token(request: Request):
    """The token from a small JSON body, or "" for anything else (read here, so a bad body is a bare 404 too)."""
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > 1024:
            return ""
    try:
        t = json.loads(raw).get("token")
    except (ValueError, AttributeError):
        return ""
    return t if isinstance(t, str) else ""


@router.post("/api/share/view")
def view(request: Request, token: str = Depends(_token), db=Depends(get_db)):
    ip = _client_ip(request)
    bad = "bad:" + ip
    # a working token opens its link even while this address is waiting for wrong ones (#203): behind a reverse
    # proxy everyone shares its address, so otherwise a stranger's few wrong tokens would shut out every holder
    link = links.find(db, token)
    if link is None:
        wait = links.throttle.wait(bad)
        if wait:
            return _bare(429, wait)
        links.throttle.fail(bad)
        if token:
            _audit("share.refused", request, warn=False)
        return _bare()
    key = "link:" + link["token_hash"]
    wait = links.throttle.wait(key)
    if wait:
        return _bare(429, wait)
    links.throttle.fail(key)   # every view counts toward the link's own allowance
    prof.sync_build(db, link["profile_id"])
    out = links.build(db, link)
    with dbm.transaction(db):
        links.viewed(db, link, ip)
    return JSONResponse(out)
