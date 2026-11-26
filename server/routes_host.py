"""Settings → Install → General: Host (#1000). The public addresses (PUBLIC_ORIGINS, settings.py's host.publicOrigins)
are editable, guarded (#998): before a change, what it does (POST .../effects: the address the app gives out, the
connected sources' return address and how many source apps must register the new one with their vendor, the phone
upload address and how many profiles' phones need it); the change typed to confirm; a list this page's own address
wouldn't pass refused, since the cross-site check would then refuse everything it sends, putting it back included;
then the fresh check, as every install setting has (routes_settings.py). Everything reads the value at each use, so it
applies at once. Read-only beside it (GET /api/settings/host): the names passkeys were made at, with their counts,
what this request carried, and the settings only the server's own setup sets (trusted proxies, the internal port and
bind address). Admins only.

General → Phone receiver (#1003): the upload address (WATCH_UPLOAD_URL, phone.uploadUrl), its change said first (how
many profiles' phones need the new one), and read-only (GET /api/settings/phone) the receiver's own settings where the
app can see them."""
import os

from fastapi import APIRouter, Depends, HTTPException, Request

from . import config, connections, settings, watch_keys
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


def _confirm_origins(db, before, value, request, s):
    """(#998) Every change to the public addresses (a reset too) is confirmed, routes_settings._confirmed's one rule,
    with "change addresses" typed; a list this page's own address wouldn't pass is refused outright."""
    e = origins_effects(db, request, value)
    if not e["accepted"]:
        raise HTTPException(409, f"Not saved: this page is open at {e['origin']}, and with that list the cross-site check "
                                 f"would refuse everything it sends, changing the list back included. Add {e['origin']} to it.")
    said = [f"The address the app gives out, {e['given_out'][0] or 'none'}, becomes {e['given_out'][1] or 'none'}."]
    if e["apps"]:
        said.append(f"{e['apps']} connected-source app{'' if e['apps'] == 1 else 's'} must have the new return address, "
                    f"{e['return_address'][1] or 'none'}, registered with its vendor.")
    if e["phones"]:
        said.append(f"The phones of {e['phones']} profile{'' if e['phones'] == 1 else 's'} with an upload secret need the "
                    f"new upload address, {', '.join(e['upload'][1])}.")
    return {"count": e["apps"] + e["phones"], "type": CONFIRM, "title": "Change the public addresses?",
            "action": "Change addresses", "message": " ".join(said), "effects": e}


def upload_effects(db, request, new):
    """What changing the upload address to new does: the addresses phones are given, before and after, and how many
    profiles' phones need the new one."""
    host = _request_host(request)
    up = [watch_keys.addresses(host), watch_keys.addresses(host, url=new)]
    return {"upload": up, "phones": watch_keys.with_secret(db) if up[0] != up[1] else 0}


settings.get_def("host.publicOrigins").confirm = _confirm_origins
EFFECTS = {"host.publicOrigins": origins_effects, "phone.uploadUrl": upload_effects}


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


@router.get("/api/settings/phone")
def phone(request: Request, s=Depends(admin), db=Depends(get_db)):
    """General → Phone receiver's read-only rows: the addresses phones are given now, how many profiles' phones upload
    with a secret, and the receiver's own settings where this process sees them (the all-in-one image passes them to
    both; with compose only the receiver has them: None)."""
    return {"upload": watch_keys.addresses(_request_host(request)), "phones": watch_keys.with_secret(db),
            "tz": os.environ.get("TZ") or None, "slot_grace_days": os.environ.get("SLOT_GRACE_DAYS") or None}
