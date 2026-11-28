"""Notifications (#1130; notify.py has what's sent, when, and why it carries no health details). The routes:

  GET  /api/settings/install/notify        admin: General → Notifications, the access token only as where it's from
  PUT  /api/settings/install/notify/token  admin, after the fresh check: {token}, a new one ("" removes it); logged by
                                           name only (notify.token). The server's address is the install setting
                                           notify.url, changed as every install setting is (PUT /api/settings/install/
                                           notify.url: the fresh check, logged with its old and new values)
  POST /api/settings/install/notify/test   admin: Test, a test notification sent now (logged: notify.test)
  GET  /api/me/notify                      whether this server sends any, to which host, and this person's choices for
                                           each profile they can see
  PUT  /api/me/notify                      {profile, kind, on}: one turned on or off (in the profile's activity: notify)
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import activity, config, db as dbm, notify
from .main import _client_ip, admin, fresh, get_db, need, user

router = APIRouter()


def _install(db):
    u = notify.url()
    return {"url": u, "host": next(iter(notify.hosts()), None), "token": notify.token_source(db), "token_env": notify.TOKEN_ENV,
            "click": config.public_address(), "texts": [notify.message(k, "Ben") for k in notify.KINDS],
            "people": db.execute("SELECT COUNT(DISTINCT user_id) FROM notify_optins").fetchone()[0]}


@router.get("/api/settings/install/notify")
def install_get(s=Depends(admin), db=Depends(get_db)):
    return _install(db)


class Token(BaseModel):
    token: str


@router.put("/api/settings/install/notify/token")
def install_token(body: Token, request: Request, s=Depends(admin), db=Depends(get_db)):
    if notify.token_source(db) == "environment":
        raise HTTPException(409, f"The access token is set by the server's environment ({notify.TOKEN_ENV}), so it can't be "
                                 "changed here. Change it there, or take it out to set it here.")
    fresh(request, s, "change the notification server's access token")
    had = notify.token_source(db)
    try:
        with dbm.transaction(db):
            notify.set_token(db, body.token)
            if body.token.strip() or had:
                activity.log(db, "notify.token", s, None, {"change": "set" if body.token.strip() else "remove"}, _client_ip(request))
    except notify.SendError as e:
        raise HTTPException(400, str(e)) from None
    return _install(db)


@router.post("/api/settings/install/notify/test")
def install_test(request: Request, s=Depends(admin), db=Depends(get_db)):
    if not notify.configured():
        raise HTTPException(409, "Set the notification server first.")
    try:
        return notify.test(db, s, _client_ip(request))
    except notify.SendError as e:
        raise HTTPException(502, str(e)) from None


@router.get("/api/me/notify")
def mine(s=Depends(user), db=Depends(get_db)):
    return {"set_up": notify.configured(), "host": next(iter(notify.hosts()), None), "profiles": notify.choices(db, s["id"])}


class Choice(BaseModel):
    profile: int
    kind: str
    on: bool


@router.put("/api/me/notify")
def choose(body: Choice, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, body.profile, "viewer")
    if body.kind not in notify.KINDS:
        raise HTTPException(400, "The kinds are new results and dose reminders.")
    if body.on and not notify.configured():
        raise HTTPException(409, "This server sends no notifications: an admin sets its notification server first.")
    with dbm.transaction(db):
        if notify.choose(db, s["id"], body.profile, body.kind, body.on):
            activity.log(db, "notify", s, body.profile, {"kind": body.kind, "on": body.on}, _client_ip(request))
    return mine(s, db)
