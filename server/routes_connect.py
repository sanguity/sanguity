"""Connected sources (#32, #91; connections.py, sync.py): signing in to a vendor's FHIR API for a profile, and the
connections that keeps up to date. The CSRF check in main.guard covers the POSTs like every other change.

Profiles (anyone the profile is shared with sees its connections; owners and editors connect and change them):
  GET  /api/profiles/{pid}/connections                      the sources one can connect, and the profile's connections
  GET  /api/profiles/{pid}/connect/start?source=ID           a sign-in: straight on to the vendor (303)
  POST /api/profiles/{pid}/connect/start                     the same as JSON {"url"}: {"source": id} or {"issuer":
                                                            a FHIR base} (a provider directory entry's, #93)
  POST /api/connect/return                                   {code, state, error?, error_description?} from the
                                                            /connect/return page: {"profile", "connection"}
  POST /api/profiles/{pid}/connections/{id}/sync            pull now
  POST /api/profiles/{pid}/connections/{id}/reconnect       sign in again: {"url"}
  POST /api/profiles/{pid}/connections/{id}/disconnect      {"remove_records": false}: revoke, delete the tokens
Admins (vendor settings, option 1: each install registers its own app):
  GET    /api/connect/sources                               the setup screen: return address, guides, sources, counts
  POST   /api/connect/sources                               add one
  PATCH  /api/connect/sources/{id}                          change one ("secret": "" removes it; leave it out to keep)
  DELETE /api/connect/sources/{id}                          remove one no profile is connected to
  POST   /api/connect/sources/{id}/test                     "Test this connection": read its discovery

/connect/return itself is a static page (www/connect-return.html, served by nginx): the session cookie is
SameSite=Strict, so it isn't sent on the vendor's redirect, and the page posts what came back same-origin instead.
"""
import os
import urllib.parse
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from . import connections as conns, db as dbm
from .main import PAGE_CSP, WWW, _client_ip, admin, get_db, need, user

router = APIRouter()


def _problem(e):
    return HTTPException(e.status, str(e))


# ---------------------------------------------------------------- a profile's connections

@router.get("/api/profiles/{pid}/connections")
def list_connections(pid: int, s=Depends(user), db=Depends(get_db)):
    role = need(db, s, pid, "viewer")
    return {"can_connect": role in ("owner", "editor"), "sources": conns.choices(db),
            "connections": conns.for_profile(db, pid), "admin": bool(s["is_admin"])}


class Start(BaseModel):
    source: Optional[int] = None
    issuer: Optional[str] = None
    connection: Optional[int] = None


@router.get("/api/profiles/{pid}/connect/start")
def start_get(pid: int, request: Request, source: int, s=Depends(user), db=Depends(get_db)):
    """Opened by the page (same site, so the session cookie comes along): on to the vendor's sign-in."""
    need(db, s, pid, "editor")
    try:
        url = conns.start(db, s, pid, vendor=source, ip=_client_ip(request))
    except conns.Problem as e:
        back = "../../../../connect.html?" + urllib.parse.urlencode({"profile": pid, "problem": str(e)[:300]})
        return RedirectResponse(back, status_code=303)
    return RedirectResponse(url, status_code=303)


@router.post("/api/profiles/{pid}/connect/start")
def start_post(pid: int, body: Start, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    try:
        return {"url": conns.start(db, s, pid, vendor=body.source, issuer=body.issuer, connection_id=body.connection,
                                   ip=_client_ip(request))}
    except conns.Problem as e:
        raise _problem(e) from None


class Return(BaseModel):
    code: Optional[str] = None
    state: Optional[str] = None
    error: Optional[str] = None
    error_description: Optional[str] = None


@router.post("/api/connect/return")
def connect_return(body: Return, request: Request, s=Depends(user), db=Depends(get_db)):
    try:
        return conns.finish(db, s, body.code, body.state, body.error, body.error_description, _client_ip(request),
                            role_check=lambda pid: need(db, s, pid, "editor"))
    except conns.Problem as e:
        raise _problem(e) from None


@router.post("/api/profiles/{pid}/connections/{cid}/sync", status_code=202)
def sync_now(pid: int, cid: int, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    try:
        return conns.sync_now(db, pid, cid)
    except conns.Problem as e:
        raise _problem(e) from None


@router.post("/api/profiles/{pid}/connections/{cid}/reconnect")
def reconnect(pid: int, cid: int, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    try:
        c = conns.get(db, pid, cid)
        if c["client_id"] is None:
            raise conns.Problem("This connection's source was removed; an admin can set it up again.", 409)
        return {"url": conns.start(db, s, pid, vendor=c["client_id"], connection_id=cid, ip=_client_ip(request))}
    except conns.Problem as e:
        raise _problem(e) from None


class Disconnect(BaseModel):
    remove_records: bool = False


@router.post("/api/profiles/{pid}/connections/{cid}/disconnect")
def disconnect(pid: int, cid: int, request: Request, body: Optional[Disconnect] = None, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    try:
        return conns.disconnect(db, s, pid, cid, bool(body and body.remove_records), _client_ip(request))
    except conns.Problem as e:
        raise _problem(e) from None


# ---------------------------------------------------------------- vendor settings (admins)

class Source(BaseModel):
    name: Optional[str] = None
    vendor: Optional[str] = None
    issuer: Optional[str] = None
    client_id: Optional[str] = None
    secret: Optional[str] = None
    auth: Optional[str] = None
    scopes: Optional[str] = None


@router.get("/api/connect/sources")
def sources(s=Depends(admin), db=Depends(get_db)):
    return conns.setup(db)


@router.post("/api/connect/sources", status_code=201)
def add_source(body: Source, request: Request, s=Depends(admin), db=Depends(get_db)):
    try:
        return conns.save_client(db, s, body.model_dump(), ip=_client_ip(request))
    except conns.Problem as e:
        raise _problem(e) from None


@router.patch("/api/connect/sources/{cid}")
def change_source(cid: int, body: Source, request: Request, s=Depends(admin), db=Depends(get_db)):
    try:
        return conns.save_client(db, s, body.model_dump(), cid, ip=_client_ip(request))
    except conns.Problem as e:
        raise _problem(e) from None


@router.delete("/api/connect/sources/{cid}")
def remove_source(cid: int, request: Request, s=Depends(admin), db=Depends(get_db)):
    try:
        conns.remove_client(db, s, cid, ip=_client_ip(request))
    except conns.Problem as e:
        raise _problem(e) from None
    return {"ok": True}


@router.post("/api/connect/sources/{cid}/test")
def test_source(cid: int, request: Request, s=Depends(admin), db=Depends(get_db)):
    try:
        return conns.test_client(db, s, cid, ip=_client_ip(request))
    except conns.Problem as e:
        raise _problem(e) from None


# ---------------------------------------------------------------- the pages, when the app serves www/ itself (development)

if os.environ.get("SANGUITY_SERVE_STATIC") == "1":   # in Docker, nginx serves these (nginx.conf)
    def _page(name, referrer=False):
        headers = {"Cache-Control": "no-store", "Content-Security-Policy": PAGE_CSP}
        if referrer:
            headers["Referrer-Policy"] = "no-referrer"
        return FileResponse(os.path.join(WWW, name), media_type="text/html", headers=headers)

    @router.get("/connect/return", include_in_schema=False)
    def return_page():
        return _page("connect-return.html", referrer=True)

    @router.get("/connect.html", include_in_schema=False)
    def connect_page():
        return _page("connect.html")
