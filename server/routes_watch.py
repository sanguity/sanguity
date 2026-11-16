"""A profile's watch connection on the Settings page (#46, watch_keys.py): the receiver's setup instructions, and
making the profile's secret (or a new one in place of it). Only the profile's owners and editors see or change it;
the secret is given out once, in the answer that makes it. Changes pass main.guard's CSRF check and are logged in
the profile's activity (never the secret)."""
from fastapi import APIRouter, Depends, Request

from . import activity, db as dbm, watch_keys
from .main import _client_ip, get_db, need, user

router = APIRouter()


@router.get("/api/profiles/{pid}/watch/receiver")
def receiver(pid: int, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    return watch_keys.status(db, pid, request.headers.get("host", ""))


@router.post("/api/profiles/{pid}/watch/receiver/rotate")
def rotate(pid: int, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    had = watch_keys.status(db, pid)
    with dbm.transaction(db):
        secret = watch_keys.rotate(db, pid, s["id"])
        activity.log(db, "watch.secret", s, pid, {"rotated": had["has_secret"] or had["legacy"]}, _client_ip(request))
    watch_keys.publish(db)
    return {**watch_keys.status(db, pid, request.headers.get("host", "")), "secret": secret}
