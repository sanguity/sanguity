"""Settings → Install → General: Host (#1000). The public addresses (PUBLIC_ORIGINS, settings.py's host.publicOrigins)
are editable, guarded (#998): before a change, what it does (POST .../effects: the address the app gives out, the
connected sources' return address and how many source apps must register the new one with their vendor, the phone
upload address and how many profiles' phones need it); the change typed to confirm; a list this page's own address
wouldn't pass refused, since the cross-site check would then refuse everything it sends, putting it back included;
then the fresh check, as every install setting has (routes_settings.py). Everything reads the value at each use, so it
applies at once. Read-only beside it (GET /api/settings/host): the names passkeys were made at, with their counts,
what this request carried, and the settings only the server's own setup sets (trusted proxies, the internal port and
bind address). Admins only."""
import os

from fastapi import APIRouter, Depends, HTTPException, Request

from . import config, connections, routes_settings, settings, watch_keys
from .main import _request_host, _same_site, admin, get_db
from .routes_settings import Value

router = APIRouter()

CONFIRM = "change addresses"   # typed to confirm a change to the public addresses


def _apps(db):
    return db.execute("SELECT COUNT(*) FROM smart_clients").fetchone()[0]


def origins_effects(db, request, new):
    """What changing the public addresses to new does, said to this request's page."""
    new, host, origin = tuple(new), _request_host(request), request.headers.get("origin")
    ret = [connections.return_address(), connections.return_address(new)]
    up = [watch_keys.addresses(host), watch_keys.addresses(host, origins=new)]
    return {"given_out": [config.public_address(), config.public_address(new)],
            "return_address": ret, "apps": _apps(db) if ret[0] != ret[1] else 0,
            "upload": up, "phones": watch_keys.with_secret(db) if up[0] != up[1] else 0,
            "origin": origin, "accepted": not origin or _same_site(origin, request, new), "confirm": CONFIRM}


def _guard_origins(db, request, body, value):
    if body is None:
        raise HTTPException(409, "The public addresses are changed under Settings → Install → General → Host, where what "
                                 "the change does is said first.")
    e = origins_effects(db, request, value)
    if not e["accepted"]:
        raise HTTPException(409, f"Not saved: this page is open at {e['origin']}, and with that list the cross-site check "
                                 f"would refuse everything it sends, changing the list back included. Add {e['origin']} to it.")
    if body.confirm != CONFIRM:
        raise HTTPException(400, f"Type “{CONFIRM}” to confirm the change.")


routes_settings.GUARDS["host.publicOrigins"] = _guard_origins
EFFECTS = {"host.publicOrigins": origins_effects}


@router.post("/api/settings/install/{key}/effects")
def effects(key: str, body: Value, request: Request, s=Depends(admin), db=Depends(get_db)):
    """What a change to this install setting would do, before it's made: {value: as it would be saved, ...}."""
    if key not in EFFECTS:
        raise HTTPException(404, "That setting's effects aren't said before a change.")
    try:
        value = settings.get_def(key).clean(body.value)
    except settings.SettingError as e:
        raise HTTPException(400, str(e))
    return {"value": value, **EFFECTS[key](db, request, value)}


@router.get("/api/settings/host")
def host(request: Request, origin: str = "", s=Depends(admin), db=Depends(get_db)):
    """General → Host's read-only rows. origin: the page's own address, for which rule of the cross-site check
    accepts it ("listed" in the public addresses, the "host" the request reached, or none)."""
    h, origin = request.headers, origin.lower().rstrip("/")
    by = ("listed" if origin in config.setting("PUBLIC_ORIGINS") else "host" if _same_site(origin, request, ()) else None) if origin else None
    return {"request": {"host": h.get("host"), "forwarded_host": h.get("x-forwarded-host"), "scheme": request.url.scheme,
                        "forwarded_proto": h.get("x-forwarded-proto"), "accepted_by": by},
            "passkeys": [dict(r) for r in db.execute("SELECT rp_id AS name, COUNT(*) AS count FROM passkeys GROUP BY rp_id ORDER BY rp_id")],
            "apps": _apps(db), "phones": watch_keys.with_secret(db),
            # the process's own environment (python -m server, server/__main__.py), read here only
            "proxies": os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"), "port": os.environ.get("PORT", "8000"),
            "bind": os.environ.get("HOST", "0.0.0.0")}
