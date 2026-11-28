"""Sign in with OIDC (#1109; oidc.py has the flow and the checks). The routes:

  GET    /api/login/oidc                  anyone: {"label", "only"} of the provider set up, or {"label": null}
  POST   /api/login/oidc/start            anyone: {"url"}, the provider's authorization address; sets the browser cookie
  POST   /api/login/oidc/return           {state, code, error?} from the sign-in page the provider sent the browser back
                                          to: signed in (as a password sign-in is), the second step asked for, or, for a
                                          link started below, the identity linked
  GET    /api/me/oidc                     settled: the provider's name, and this account's linked identities
  POST   /api/me/oidc/start               settled, {password}: {"url"}, to link this account's identity (the password as
                                          adding a passkey asks it; with OIDC only, a code's, a passkey's or the provider's
                                          fresh check instead, main._password)
  POST   /api/me/reauth/oidc/start        settled: {"url"}, the fresh check at the provider (signed in again there now)
  DELETE /api/me/oidc/{iid}               settled, after the fresh check: unlink one (signs out every other sign-in)
  GET    /api/settings/install/oidc       admin: General → Sign-in provider, its client secret only as where it's from
  PUT    /api/settings/install/oidc       admin, after the fresh check: a change (each logged; the secret by name only)
  POST   /api/settings/install/oidc/test  admin: Test connection, the discovery document and the JWKS read again
  POST   /api/users/{uid}/oidc            admin, after the fresh check: link an identity to an account by its subject
  DELETE /api/users/{uid}/oidc/{iid}      admin, after the fresh check: unlink one (the account signed out everywhere)

A sign-in ends in main._start_session as a password's does: the sign-in lengths, the device cookie, the security log
(login.ok method "oidc", or "oidc + code", "oidc + passkey"). A failure is login.fail, step "oidc", counted by the
address's throttle as a failed passkey is; an identity linked to no account is refused plainly, and no account is made."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from . import accounts, activity, config, crypto, db as dbm, oidc, security as sec
from .main import (_audit, _client_ip, _password, _remember_device, _request_host, _same_site, _secure, _session,
                   _start_session, _verified, admin, fresh, get_db, must_change, second_step, settled)

router = APIRouter()


def _refused(e):
    return HTTPException(e.status, str(e))


def _page_origin(request):
    """The page's own address, where no public address is set: the Origin the browser sent (the cross-site check
    passed it), else the address this request reached."""
    origin = request.headers.get("origin")
    if origin and _same_site(origin, request):
        return origin.rstrip("/")
    return f"{request.url.scheme}://{_request_host(request)}"


def _cookie(request):
    """(name, path) of the browser cookie: over HTTPS __Host-sanguity_oidc at /, which no other name or plain-HTTP
    page can set (#1109's review: one planted for /api/login/oidc would bind a sign-in to someone else's), as the
    session cookie's (config.COOKIE_HOST); over plain HTTP, sanguity_oidc for /api/login/oidc only."""
    return ("__Host-" + oidc.BROWSER_COOKIE, "/") if _secure(request) else (oidc.BROWSER_COOKIE, oidc.COOKIE_PATH)


def _begin(db, request, response, purpose, s=None):
    try:
        url, browser = oidc.start(db, purpose, _page_origin(request), uid=s and s["id"], sid=s and s["sid"],
                                  who=s, ip=_client_ip(request))
    except oidc.OidcError as e:
        raise _refused(e) from None
    name, path = _cookie(request)
    response.set_cookie(name, browser, max_age=oidc.STATE_TTL, path=path, httponly=True, samesite="strict", secure=_secure(request))
    return {"url": url}


@router.get("/api/login/oidc")
def provider(db=Depends(get_db)):
    """For the sign-in page: the button's name, and whether it's the only way in. Nothing is contacted."""
    return oidc.brief(db) or {"label": None, "only": False}


@router.post("/api/login/oidc/start")
def login_start(request: Request, response: Response, db=Depends(get_db)):
    return _begin(db, request, response, "login")


class Return(BaseModel):
    state: Optional[str] = None
    code: Optional[str] = None
    error: Optional[str] = None


@router.post("/api/login/oidc/return")
def login_return(body: Return, request: Request, response: Response, db=Depends(get_db)):
    ip = _client_ip(request)
    keys = ("ip:oidc|" + ip,)
    if (w := sec.throttle.wait(*keys)):
        _audit("login.throttled", request, step="oidc")
        raise HTTPException(429, f"Too many failed sign-ins. Try again in {w} seconds.", headers={"Retry-After": str(w)})
    name, path = _cookie(request)
    response.delete_cookie(name, path=path, httponly=True, samesite="strict", secure=_secure(request))
    try:
        entry, claims, mfa = oidc.finish(db, (body.state or "")[:200], body.code, request.cookies.get(name), body.error, ip=ip)
    except oidc.OidcError as e:
        if e.status < 500:   # (a provider that didn't answer isn't the person's wrong try)
            sec.throttle.fail(*keys)
        _audit("login.fail", request, step="oidc", reason=str(e)[:200])
        raise _refused(e) from None
    issuer, sub, label = entry["issuer"], claims["sub"], oidc.load(db)["label"]
    if entry["purpose"] == "link":
        return _linked(db, request, entry, issuer, sub, claims)
    if entry["purpose"] == "reauth":
        return _reauthed(db, request, entry, issuer, sub, claims)
    uid = oidc.account_for(db, issuer, sub)
    u = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone() if uid else None
    if not u or u["disabled"]:
        sec.throttle.fail(*keys)
        _audit("login.fail", request, u, step="oidc", reason="disabled" if u else "identity not linked")
        raise HTTPException(401, "That account can't sign in." if u else
                            f"Your {label} identity isn't linked to an account here. Sign in another way and link it under "
                            "Settings, Sign-in and security, or ask an admin to link it to your account.")
    oidc.used(db, issuer, sub, oidc.shown_name(claims))
    if not mfa and (step := second_step(db, u, "oidc")):   # the provider didn't say two factors: the account's own, now
        return {**step, "username": u["username"]}
    _remember_device(request, response, db, u["id"])
    _start_session(request, response, db, u["id"], "oidc" + (" (the provider's two factors)" if mfa else ""), idp_mfa=mfa)
    return {"must_change_pw": must_change(db, u), "username": u["username"]}


def _here(db, request, entry, what):
    """The session that started a link or a fresh check, still signed in here."""
    s = _session(request, db)
    if not s or s["sid"] != entry["sid"] or s["id"] != entry["uid"]:
        raise HTTPException(401, f"You're signed out of Sanguity here, so {what}. Sign in, then try again.")
    return s


def _reauthed(db, request, entry, issuer, sub, claims):
    """The fresh check at the provider: signed in again there just now (oidc.finish checked auth_time), as an identity
    linked to this session's account. It stands in for the password while OIDC only is on (main._password)."""
    s = _here(db, request, entry, "it couldn't confirm it's you")
    if oidc.account_for(db, issuer, sub) != s["id"]:
        _audit("reauth.fail", request, s, action="fresh check", step="oidc", reason="another identity")
        raise HTTPException(403, "That identity at the provider isn't the one linked to your account here.")
    oidc.used(db, issuer, sub, oidc.shown_name(claims))
    return {"reauth": True, **_verified(db, s, stepped=True)}


def _linked(db, request, entry, issuer, sub, claims):
    """The link started under Sign-in and security: for the session that started it, still signed in."""
    s = _here(db, request, entry, "the identity wasn't linked")
    name = oidc.shown_name(claims)
    try:
        with dbm.transaction(db):
            iid = oidc.link(db, s["id"], issuer, sub, name)
            oidc.used(db, issuer, sub)   # (its sign-in at the provider just worked: may_go_only)
            activity.log(db, "oidc.link", s, detail={"name": name or sub}, ip=_client_ip(request))
    except oidc.OidcError as e:
        raise _refused(e) from None
    _audit("oidc.link", request, s, warn=False)
    return {"linked": iid, "name": name}


# ---------------------------------------------------------------- one's own identities

@router.get("/api/me/oidc")
def mine(s=Depends(settled), db=Depends(get_db)):
    return {"provider": oidc.brief(db), "identities": oidc.listing(db, s["id"])}


class Proof(BaseModel):
    password: str = ""


@router.post("/api/me/oidc/start")
def link_start(body: Proof, request: Request, response: Response, s=Depends(settled), db=Depends(get_db)):
    """Linking adds a way in, and its sign-ins may count as two factors: the password, as adding a passkey asks it
    (#590; #1109's review: a sign-in's fresh check alone let someone at the browser for a few minutes link their own);
    with OIDC only on, a fresh check with a code, a passkey or the provider instead (main._password)."""
    if not oidc.brief(db):
        raise HTTPException(404, "No sign-in provider is set up on this server.")
    _password(request, db, s, body.password, "link a sign-in provider")
    return _begin(db, request, response, "link", s)


@router.post("/api/me/reauth/oidc/start")
def reauth_start(request: Request, response: Response, s=Depends(settled), db=Depends(get_db)):
    """The fresh check at the provider (#1109's review): for an account with an identity linked there, signed in again
    now (prompt=login, max_age=0). Settled, not user: where two-factor sign-in is required and OIDC only is on, it's how
    an account with none confirms it's them to set one up."""
    if not oidc.brief(db):
        raise HTTPException(404, "No sign-in provider is set up on this server.")
    if not oidc.current_count(db, s["id"]):
        raise HTTPException(409, "No identity at the sign-in provider is linked to your account.")
    return _begin(db, request, response, "reauth", s)


def _last_way_in(db, uid, iid):
    """(#1109) With Sign in with OIDC only on, an account's last identity at the provider is its only way in."""
    cfg = oidc.load(db)
    if not oidc.only(db):
        return False
    r = db.execute("SELECT issuer FROM oidc_identities WHERE id=? AND user_id=?", (iid, uid)).fetchone()
    return bool(r) and r["issuer"] == oidc.issuer_key(cfg["issuer"]) and oidc.current_count(db, uid) == 1


def _unlink(db, uid, iid):
    r = db.execute("SELECT subject, name FROM oidc_identities WHERE id=? AND user_id=?", (iid, uid)).fetchone()
    if not r:
        raise HTTPException(404, "No such linked identity.")
    db.execute("DELETE FROM oidc_identities WHERE id=?", (iid,))
    return crypto.dec(r["name"]) if r["name"] else r["subject"]


@router.delete("/api/me/oidc/{iid}")
def unlink(iid: int, request: Request, s=Depends(settled), db=Depends(get_db)):
    """Unlink one of one's own: after the fresh check; every other sign-in of the account ends, as removing a passkey's
    does (a sign-in elsewhere may be the one it made)."""
    fresh(request, s, "unlink a sign-in provider")
    if _last_way_in(db, s["id"], iid):
        raise HTTPException(409, "This server signs in with its sign-in provider only, and this is your only identity "
                                 "there: unlinked, you couldn't sign in again. An admin can turn that off first.")
    with dbm.transaction(db):
        name = _unlink(db, s["id"], iid)
        n = db.execute("DELETE FROM sessions WHERE user_id=? AND id<>?", (s["id"], s["sid"])).rowcount
        activity.log(db, "oidc.unlink", s, detail={"name": name}, ip=_client_ip(request))
    return {"ok": True, "signed_out_elsewhere": n}


# ---------------------------------------------------------------- General → Sign-in provider (admins)

def _install(db, request):
    cfg = oidc.load(db)
    origin = _page_origin(request)
    return {**{k: cfg[k] for k in oidc.FIELDS}, "only": bool(cfg["only"]) and oidc.configured(cfg),
            "configured": oidc.configured(cfg), "secret": oidc.secret_source(db), "secret_env": oidc.SECRET_ENV,
            "redirect_uri": oidc.redirect_uri(origin), "from_public_address": bool(config.public_address()),
            "hosts": oidc.hosts(db), "linked": db.execute("SELECT COUNT(*) FROM oidc_identities WHERE issuer=?",
                                                          (oidc.issuer_key(cfg["issuer"]),)).fetchone()[0]}


@router.get("/api/settings/install/oidc")
def install_get(request: Request, s=Depends(admin), db=Depends(get_db)):
    """The provider as set up, the client secret only as where it comes from (never its value), the redirect URI to
    register, and whether this admin's own sign-in with it works yet (may_go_only)."""
    return {**_install(db, request), "may_go_only": oidc.works_for(db, s["id"])}


class Provider(BaseModel):
    issuer: Optional[str] = None
    client_id: Optional[str] = None
    secret: Optional[str] = None   # a new client secret; "" removes it; absent keeps it
    label: Optional[str] = None
    scopes: Optional[str] = None
    acr: Optional[str] = None
    only: Optional[bool] = None


NAMES = {"issuer": "issuer", "client_id": "client ID", "label": "button's name", "scopes": "scopes", "acr": "acr for two factors"}


@router.put("/api/settings/install/oidc")
def install_put(body: Provider, request: Request, s=Depends(admin), db=Depends(get_db)):
    before = oidc.load(db)
    try:
        after = oidc.clean(before, body.model_dump(include=set(oidc.FIELDS)))
    except oidc.OidcError as e:
        raise _refused(e) from None
    if body.secret is not None and len(body.secret) > 2000:
        raise HTTPException(400, "The client secret is at most 2000 characters.")
    secret = body.secret.strip() if body.secret is not None else None
    if secret is not None and oidc.secret_source(db) == "environment":
        raise HTTPException(409, f"The client secret is set by the server's environment ({oidc.SECRET_ENV}), so it can't be "
                                 "changed here. Change it there, or take it out to set it here.")
    changed = [k for k in oidc.FIELDS if after[k] != before[k]]
    if secret is not None and (secret or oidc.secret_source(db)):
        changed.append("secret")
    if body.only is not None and body.only != bool(before["only"]):
        changed.append("only")
    if not changed:
        return {**_install(db, request), "may_go_only": oidc.works_for(db, s["id"])}
    if before["only"] and oidc.configured(before) and set(changed) - {"label", "only"}:
        raise HTTPException(409, "Sign in with OIDC only is on: turn it off before changing the provider, so a change that "
                                 "doesn't work can't keep everyone out.")
    if body.only and "only" in changed:
        if not oidc.configured(after) or set(changed) - {"only", "label"}:
            raise HTTPException(409, "Save the provider and sign in with it first.")
        if not oidc.works_for(db, s["id"]):
            raise HTTPException(409, f"Sign in with {after['label']} yourself first: link your own identity under Settings, "
                                     "Sign-in and security (or sign in with it), so an OIDC-only server still lets you in.")
    fresh(request, s, "change the sign-in provider")
    after["only"] = bool(body.only) if body.only is not None else bool(before["only"])
    with dbm.transaction(db):
        oidc.save(db, after, secret)
        for k in changed:
            detail = {"setting": "oidc." + k, "label": "Sign-in provider: " + NAMES.get(k, {"secret": "client secret",
                      "only": "sign in with OIDC only"}.get(k, k))}
            if k in oidc.FIELDS:
                detail.update({"from": before[k], "to": after[k]})
            elif k == "only":
                detail.update({"from": bool(before["only"]), "to": after["only"]})   # (the secret: by name only)
            activity.log(db, "settings.install", s, None, detail, _client_ip(request))
    _audit("oidc.settings", request, s, warn=False, changed=changed)
    return {**_install(db, request), "may_go_only": oidc.works_for(db, s["id"])}


@router.post("/api/settings/install/oidc/test")
def install_test(request: Request, s=Depends(admin), db=Depends(get_db)):
    """Test connection: the provider's discovery document and JWKS, read again (each contact in Outside connections)."""
    try:
        return oidc.test(db, s, _client_ip(request))
    except oidc.OidcError as e:
        raise _refused(e) from None


# ---------------------------------------------------------------- an account's identities (Install → Accounts)

class Subject(BaseModel):
    subject: str


@router.post("/api/users/{uid}/oidc")
def admin_link(uid: int, body: Subject, request: Request, s=Depends(admin), db=Depends(get_db)):
    """Link an identity to an account by its subject (the provider's user ID, its ID token's sub): its person can then
    sign in with the provider. A way into the account, so after the fresh check."""
    target = db.execute("SELECT id, username FROM users WHERE id=?", (uid,)).fetchone()
    if not target:
        raise HTTPException(404, "No such account.")
    cfg = oidc.load(db)
    if not oidc.configured(cfg):
        raise HTTPException(404, "No sign-in provider is set up on this server.")
    sub = body.subject.strip()
    if not 0 < len(sub) <= 255 or any(c.isspace() for c in sub):
        raise HTTPException(400, "The subject is the provider's ID for the person, with no spaces, at most 255 characters.")
    fresh(request, s, "link a sign-in provider")
    try:
        with dbm.transaction(db):
            iid = oidc.link(db, uid, cfg["issuer"], sub, by=s["id"])   # (its person told, on Sign-in and security, oidc.listing)
            activity.log(db, "oidc.link", s, detail={"username": target["username"], "name": sub}, ip=_client_ip(request))
    except oidc.OidcError as e:
        raise _refused(e) from None
    # in the security log as the account's event too: a way into it that its person didn't make (#1109's review)
    _audit("oidc.link", request, target, by=s["username"], subject=sub[:255])
    return {"id": iid}


@router.delete("/api/users/{uid}/oidc/{iid}")
def admin_unlink(uid: int, iid: int, request: Request, s=Depends(admin), db=Depends(get_db)):
    target = db.execute("SELECT id, username FROM users WHERE id=?", (uid,)).fetchone()
    if not target:
        raise HTTPException(404, "No such account.")
    if uid == s["id"] and _last_way_in(db, uid, iid):
        raise HTTPException(409, "This server signs in with its sign-in provider only, and this is your only identity "
                                 "there. Turn Sign in with OIDC only off first.")
    fresh(request, s, "unlink a sign-in provider")
    with dbm.transaction(db):
        name = _unlink(db, uid, iid)
        if uid != s["id"]:
            accounts.sign_out_everywhere(db, uid, feeds=False)
        activity.log(db, "oidc.unlink", s, detail={"username": target["username"], "name": name}, ip=_client_ip(request))
    return {"ok": True}
