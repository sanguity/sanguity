"""Outside connections (#59, #97; outside.py): every host this install may contact, and when it last did.

  GET /api/outside   what the Admin page's "Outside connections" section shows: admins the whole install,
                     owners and editors their profiles' entries (viewers see an empty list)
  POST /api/outside/test {host}   (#1167) a listed host's reachability: its name, a connection, TLS (through the
                     outbound proxy where one is set; nothing sent beyond the handshake). Admins
  GET /api/outside/log?kind=&host=&since=&until=   (#1167) recent contacts, filtered, as /api/outside's scope
  POST /api/outside/clear {confirm}   (#1167) the history of contacts cleared: asked first with how many, the fresh
                     check, and logged in Install activity (outside.cleared). Admins
  The switches per kind, "No outside connections at all" and the blocked hosts are install settings (PUT
  /api/settings/install/{key}: admins, the fresh check, logged); outside.refusal() reads them and net.py enforces them.
  PUT /api/outside/proxy-password {value}   (#1159) the outbound proxy's password, write-only: kept for the proxy
                     the next start uses, sent to no other; "" takes it away. Admins, the fresh check, logged (never it)
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import activity, config, db as dbm, outside, server_settings
from .main import _client_ip, admin, fresh, get_db, user

router = APIRouter()


@router.get("/api/outside")
def outside_connections(s=Depends(user), db=Depends(get_db)):
    return outside.view(db, s)


class Host(BaseModel):
    host: str


@router.post("/api/outside/test")
def test_host(body: Host, request: Request, s=Depends(admin), db=Depends(get_db)):
    from . import net
    host = net.norm(body.host)[:255]
    if host not in outside.view(db, s)["may_contact"] and host not in outside.blocked():
        raise HTTPException(404, f"{host} isn't one of the hosts listed here.")
    try:
        return {"host": host, "steps": outside.probe(db, s, host, _client_ip(request))}
    except net.Refused as e:
        raise HTTPException(409, str(e))


@router.get("/api/outside/log")
def contacts_log(kind: str = "", host: str = "", since: int = 0, until: int = 0, s=Depends(user), db=Depends(get_db)):
    if kind and kind not in outside.KINDS and kind != "test":
        raise HTTPException(400, "There's no such kind of outside connection.")
    return {"contacts": outside.history(db, s, kind or None, host[:255], since or None, until or None)}


class Confirm(BaseModel):
    confirm: Any = None


@router.post("/api/outside/clear")
def clear_log(body: Confirm, request: Request, s=Depends(admin), db=Depends(get_db)):
    n = db.execute("SELECT COUNT(*) FROM activity WHERE action=?", (outside.ACTION,)).fetchone()[0]
    rows = db.execute("SELECT COUNT(*) FROM outside_contacts").fetchone()[0]
    if not n and not rows:
        return {"cleared": 0}
    if str(body.confirm or "").strip() != str(n):
        goes = {"count": n, "type": str(n), "title": f"Clear {n:,} contact{'' if n == 1 else 's'}?", "action": "Clear history",
                "message": f"The history of outside contacts goes: {n:,} recorded contact{'' if n == 1 else 's'}, and when each host "
                           "was last contacted. It can't be brought back; the clearing itself is kept in Install activity."}
        raise HTTPException(409, {"code": "confirm", "message": f"{goes['message']} Type {n} to confirm.", "confirm": goes})
    fresh(request, s, "clear the history of outside contacts")
    with dbm.transaction(db):
        gone = outside.clear(db)
        activity.log(db, "outside.cleared", s, None, {"contacts": gone}, _client_ip(request))
    return {"cleared": gone}


class Secret(BaseModel):
    value: Any = ""


@router.put("/api/outside/proxy-password")
def proxy_password(body: Secret, request: Request, s=Depends(admin), db=Depends(get_db)):
    if config.managed(outside.PROXY_PASSWORD_ENV) is not None:
        raise HTTPException(409, f"The proxy's password is managed by the environment ({config.managed_by(outside.PROXY_PASSWORD_ENV)}): change it there.")
    address = server_settings.next_values().get("OUTBOUND_PROXY", "")
    value = body.value if isinstance(body.value, str) else ""
    if value and not address:
        raise HTTPException(409, "Set the proxy first: its password is kept for it alone.")
    fresh(request, s, "change a server setting")
    try:
        with dbm.transaction(db):
            outside.set_proxy_password(db, value, address)
            activity.log(db, "settings.install", s, None, {"setting": outside.PROXY_PASSWORD_ENV,
                         "label": "Proxy password (Outbound proxy)", "to": "set" if value else "none"}, _client_ip(request))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"source": outside.proxy_password_source(db, address)}
