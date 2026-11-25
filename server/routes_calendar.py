"""Calendar feeds (#51; calendar.py): making, rotating and revoking a person's feed of a profile, and the feed itself.

Anyone who can view a profile may make a feed of it for themself: it shows only what they can already see in the
app (appointments and expected lab draws), and they choose how much it says. Each person manages their own; the
profile's owners also see everyone's feeds and can revoke any. Making, rotating, changing and revoking are logged
in the profile's activity as "calendar"; fetches aren't (a calendar app asks every few hours), only the last one's
time.

The feed, GET /api/profiles/{pid}/calendar.ics?token=..., is the one profile route with no session: a calendar app
can't sign in, so the token is the credential. It sets no cookie, answers a wrong token or profile with the same
bare 404, and is throttled (calendar.throttle) per address for wrong tokens and per feed for every fetch.
"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from . import activity, calendar, config, db as dbm, security as sec
from .main import _client_ip, _request_host, get_db, need, user

router = APIRouter()


def _base_url(request):
    """Where the app is opened, for the link back in each event: the first PUBLIC_ORIGINS entry if set, else the
    address this request reached (uvicorn applies X-Forwarded-Proto from nginx)."""
    if (origins := config.setting("PUBLIC_ORIGINS")):
        return sorted(origins)[0]
    host = _request_host(request)
    return f"{request.url.scheme}://{host}" if host else str(request.base_url)


def _path(pid, token):
    return f"api/profiles/{pid}/calendar.ics?token={token}"


def _log(db, s, pid, change, request, **detail):
    activity.log(db, "calendar", s, pid, {"change": change, **detail}, _client_ip(request))


@router.get("/api/profiles/{pid}/calendar")
def state(pid: int, s=Depends(user), db=Depends(get_db)):
    """This person's feed of the profile (never its address: that was shown once), and, for owners, everyone's."""
    role = need(db, s, pid, "viewer")
    out = {"role": role, "mine": calendar.mine(db, pid, s["id"])}
    if role == "owner":
        out["everyone"] = calendar.everyone(db, pid)
    return out


class FeedIn(BaseModel):
    details: Literal["minimal", "full"] = "minimal"
    labs: bool = True
    tz: Optional[str] = None
    meds: bool = True   # medication reminders (#162)


class FeedPatch(BaseModel):
    details: Optional[Literal["minimal", "full"]] = None
    labs: Optional[bool] = None
    tz: Optional[str] = None
    meds: Optional[bool] = None   # medication reminders (#162)


@router.post("/api/profiles/{pid}/calendar")
def make(pid: int, body: FeedIn, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "viewer")
    with dbm.transaction(db):
        if calendar.mine(db, pid, s["id"]):
            raise HTTPException(409, "You already have a calendar feed of this profile. Rotate it for a new address.")
        token = calendar.create(db, pid, s["id"], body.details, body.labs, body.tz, body.meds)
        _log(db, s, pid, "create", request, details=body.details)
    return {"path": _path(pid, token), "feed": calendar.mine(db, pid, s["id"])}


@router.post("/api/profiles/{pid}/calendar/rotate")
def rotate(pid: int, request: Request, s=Depends(user), db=Depends(get_db)):
    """A new address; the old one stops working at once (a calendar subscribed to it then shows nothing new)."""
    need(db, s, pid, "viewer")
    with dbm.transaction(db):
        token = calendar.rotate(db, pid, s["id"])
        if token is None:
            raise HTTPException(404, "You have no calendar feed of this profile.")
        _log(db, s, pid, "rotate", request)
    return {"path": _path(pid, token), "feed": calendar.mine(db, pid, s["id"])}


@router.patch("/api/profiles/{pid}/calendar")
def change(pid: int, body: FeedPatch, request: Request, s=Depends(user), db=Depends(get_db)):
    """How much the feed says, and whether it lists expected labs and medication reminders; the address stays the same."""
    need(db, s, pid, "viewer")
    with dbm.transaction(db):
        before = calendar.mine(db, pid, s["id"])
        if not before or not calendar.change(db, pid, s["id"], body.details, body.labs, body.tz, body.meds):
            raise HTTPException(404, "You have no calendar feed of this profile.")
        after = calendar.mine(db, pid, s["id"])
        if (before["details"], before["labs"], before["meds"]) != (after["details"], after["labs"], after["meds"]):
            _log(db, s, pid, "change", request, details=after["details"], labs=after["labs"], meds=after["meds"])
    return {"feed": after}


@router.delete("/api/profiles/{pid}/calendar")
def revoke_mine(pid: int, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "viewer")
    with dbm.transaction(db):
        if not calendar.revoke(db, pid, uid=s["id"]):
            raise HTTPException(404, "You have no calendar feed of this profile.")
        _log(db, s, pid, "revoke", request)
    return {"ok": True}


@router.delete("/api/profiles/{pid}/calendar/{feed_id}")
def revoke_any(pid: int, feed_id: int, request: Request, s=Depends(user), db=Depends(get_db)):
    """An owner revokes anyone's feed of the profile (a person may revoke their own this way too)."""
    role = need(db, s, pid, "viewer")
    with dbm.transaction(db):
        f = db.execute("SELECT user_id FROM calendar_feeds WHERE id=? AND profile_id=?", (feed_id, pid)).fetchone()
        if not f or (role != "owner" and f["user_id"] != s["id"]):
            raise HTTPException(404, "There's no such calendar feed.")
        who = calendar.revoke(db, pid, feed_id=feed_id)
        _log(db, s, pid, "revoke", request, **({} if who["user_id"] == s["id"] else {"username": who["username"]}))
    return {"ok": True}


# ---------------------------------------------------------------- the feed

def _bare(status, retry=None):
    """The same answer for any refusal, so it tells nothing about which profiles or tokens exist."""
    headers = {"Cache-Control": "private, no-store"}
    if retry:
        headers["Retry-After"] = str(retry)
    return Response("Not found.\n" if status == 404 else "Too many requests. Try again later.\n", status_code=status,
                    media_type="text/plain; charset=utf-8", headers=headers)


@router.api_route("/api/profiles/{pid}/calendar.ics", methods=["GET", "HEAD"])
def feed(pid: int, request: Request, token: str = "", db=Depends(get_db)):
    ip = _client_ip(request)
    bad = "bad:" + ip
    # a working token is served even while this address is waiting for wrong ones (#203; as for share links)
    f = calendar.find(db, pid, token)
    if f is None:
        wait = calendar.throttle.wait(bad)
        if wait:
            return _bare(429, wait)
        calendar.throttle.fail(bad)
        return _bare(404)
    key = "feed:" + f["token_hash"]
    wait = calendar.throttle.wait(key)
    if wait:
        return _bare(429, wait)
    calendar.throttle.fail(key)   # every fetch counts toward the feed's own allowance
    calendar.fetched(db, f)
    body = calendar.build(db, f, _base_url(request)).encode("utf-8")
    return Response(b"" if request.method == "HEAD" else body, media_type="text/calendar; charset=utf-8",
                    headers={"Cache-Control": "private, max-age=0", "Content-Disposition": 'inline; filename="sanguity.ics"',
                             **({"Content-Length": str(len(body))} if request.method == "HEAD" else {})})
