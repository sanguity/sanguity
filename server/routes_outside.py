"""Outside connections (#59, #97; outside.py): every host this install may contact, and when it last did.

  GET /api/outside   what the Admin page's "Outside connections" section shows: admins the whole install,
                     owners and editors their profiles' entries (viewers see an empty list)
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
