"""Share links (#47; share_links.py): owners make and revoke them, and the holder of one opens it.

  GET    /api/profiles/{pid}/links         the profile's links (never their tokens), the sections to choose from and
                                           the profile's lab names (owners)
  POST   /api/profiles/{pid}/links         {label, sections, labs, from, to, hours}: a new link; its address is in
                                           the answer, the only time the token exists outside the link (owners)
  DELETE /api/profiles/{pid}/links/{id}    revoke it, at once (owners: any link of the profile, whoever made it)
  POST   /api/share/view                   {"token"}: what the link shows. No session: the token is the credential
                                           {"token", "photo": id}: one of the journal's photos the link shows (#161,
                                           only with "Photos in the journal" ticked), the picture itself
                                           {"token", "section": id}: one of the link's long sections in full, for its
                                           Show all (#498: the view sends their latest rows)

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

from . import activity, config, db as dbm, profiles as prof, share_links as links
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
            "sections": links.offered(db, pid, s["id"])}   # a measure only when there's some (#225)


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
    # (#1009) base: the public address, which the page makes the link from in place of its own, when one is set, so a
    # link made at the server's home-network address or a dev name opens for the clinician it's sent to
    return {"path": "share.html#" + token, **config.given_base(), "link": links.get(db, pid, lid)}


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


async def _ask(request: Request):
    """(the token, a photo's id or None, a section's id or None) from a small JSON body, or ("", None, None) for
    anything else (read here, so a bad body is a bare 404 too)."""
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > 1024:
            return "", None, None
    try:
        body = json.loads(raw)
        t, photo, section = body.get("token"), body.get("photo"), body.get("section")
    except (ValueError, AttributeError):
        return "", None, None
    photo = photo if isinstance(photo, int) and not isinstance(photo, bool) else None
    return (t if isinstance(t, str) else ""), photo, (section if isinstance(section, str) else None)


@router.post("/api/share/view")
def view(request: Request, ask=Depends(_ask), db=Depends(get_db)):
    token, photo, section = ask
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
    key = ("photo:" if photo is not None else "link:") + link["token_hash"]   # #161: a photo has its own allowance
    wait = links.throttle.wait(key)
    if wait:
        return _bare(429, wait)
    links.throttle.fail(key)   # every view counts toward the link's own allowance
    if photo is not None:   # one of the page's photos: part of the view just logged, so not logged again
        got = links.photo(db, link, photo)
        if got is None:
            return _bare()
        return Response(got[0], media_type=got[1], headers={"Cache-Control": "no-store",
                                                            "Content-Disposition": f'inline; filename="photo-{photo}"'})
    if section is not None and not links.shows(link, section):   # not one of this link's long sections
        return _bare()
    prof.sync_build(db, link["profile_id"])
    out = links.build(db, link, section=section)
    with dbm.transaction(db):
        links.viewed(db, link, ip, section)   # a Show all too (#498): what it sends is logged like the view
    return JSONResponse(out)
