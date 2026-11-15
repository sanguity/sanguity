"""Sanguity API. Every route that touches health data checks the caller's role on that profile.

Roles on a profile:  owner (view, import/edit, share, delete) · editor (view, import/edit) · viewer (view).
Admin is an account flag for managing accounts only; it gives no access to anyone's records.
"""
import logging, os, secrets, string
from contextlib import asynccontextmanager
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import accounts, config, db as dbm, profiles as prof, security as sec

log = logging.getLogger("sanguity")
RANK = {"viewer": 1, "editor": 2, "owner": 3}
WWW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "www")


# ---------------------------------------------------------------- startup

def _setup_code(db):
    """While no account exists, the first one is created in the browser with a one-time code from the logs."""
    if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        accounts.forget_setup_code(db)
        return
    alphabet = string.ascii_uppercase.translate(str.maketrans("", "", "IO")) + "23456789"
    code = "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))
    dbm.meta_set(db, "setup_code", sec.token_hash(code))
    fd = os.open(config.SETUP_CODE_FILE + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:   # readable only by the app's own user, like its database
        f.write(code + "\n")
    os.replace(config.SETUP_CODE_FILE + ".tmp", config.SETUP_CODE_FILE)
    bar = "=" * 64
    print(f"\n{bar}\n  Finish setting up Sanguity in your browser.\n  Setup code: {code}\n"
          f"  (also in {config.SETUP_CODE_FILE}; a new code is made each time the app starts until the\n"
          f"  first account exists)\n{bar}\n", flush=True)


@asynccontextmanager
async def lifespan(app):
    dbm.init()
    db = dbm.connect()
    try:
        pid = prof.bootstrap(db)
        if pid:
            print(f"Loaded the existing records into profile {pid} (\"My records\").", flush=True)
        prof.sync_build(db)
        _setup_code(db)
    finally:
        db.close()
    yield


app = FastAPI(title="Sanguity", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def _same_site(origin, request):
    """The browser's Origin names this site: the Host it reached us on, the host a reverse proxy in front says it
    served (X-Forwarded-Host, passed on by our nginx), or an address listed in PUBLIC_ORIGINS."""
    origin = origin.lower().rstrip("/")
    if origin in config.PUBLIC_ORIGINS:
        return True
    plain = lambda h: h.removesuffix(":443").removesuffix(":80")   # browsers leave default ports out of Origin
    host = plain(origin.split("://", 1)[-1])
    seen = {plain(request.headers.get("host", "").lower())}
    seen.update(plain(h.strip().lower()) for h in request.headers.get("x-forwarded-host", "").split(",") if h.strip())
    return host in seen


@app.exception_handler(sec.Busy)
async def busy(request: Request, exc: sec.Busy):
    return JSONResponse({"detail": "The server is busy checking other sign-ins. Try again in a moment."},
                        status_code=503, headers={"Retry-After": "2"})


@app.middleware("http")
async def guard(request: Request, call_next):
    """CSRF: state-changing API calls must carry X-Sanguity (a custom header, so a cross-site page can't send it
    without a CORS preflight, which this app never grants) and, when the browser says where they came from, come
    from this site. Session cookies are SameSite=Strict too."""
    if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if request.headers.get("x-sanguity") != "1" or (origin and not _same_site(origin, request)):
            return JSONResponse({"detail": "Request refused (cross-site check)."}, status_code=403)
    resp = await call_next(request)
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


# ---------------------------------------------------------------- dependencies

def get_db():
    db = dbm.connect()
    try:
        yield db
    finally:
        db.close()


def _client_ip(request):
    return request.client.host if request.client else "?"


def _session(request, db):
    token = request.cookies.get(config.COOKIE)
    if not token or len(token) > 200:
        return None
    s = db.execute("""SELECT s.id AS sid, s.last_seen, s.expires, u.* FROM sessions s JOIN users u ON u.id = s.user_id
                      WHERE s.token_hash=? AND u.disabled=0""", (sec.token_hash(token),)).fetchone()
    now = dbm.now()
    if not s:
        return None
    idle = config.SESSION_IDLE_MINUTES * 60
    if s["expires"] <= now or (idle and now - s["last_seen"] > idle):
        db.execute("DELETE FROM sessions WHERE id=?", (s["sid"],))
        return None
    # note use at most once a minute, or more often when the idle limit is short enough for that to matter
    if now - s["last_seen"] >= (min(60, idle // 4) if idle else 60):
        db.execute("UPDATE sessions SET last_seen=? WHERE id=?", (now, s["sid"]))
    return s


def any_user(request: Request, db=Depends(get_db)):
    """Signed in (a password change may still be pending)."""
    s = _session(request, db)
    if not s:
        raise HTTPException(401, "Not signed in.")
    return s


def user(s=Depends(any_user)):
    """Signed in, with no pending password change."""
    if s["must_change_pw"]:
        raise HTTPException(403, "password_change_required")
    return s


def admin(s=Depends(user)):
    if not s["is_admin"]:
        raise HTTPException(403, "Only an admin can do that.")
    return s


def role_of(db, uid, pid):
    r = db.execute("SELECT role FROM memberships WHERE user_id=? AND profile_id=?", (uid, pid)).fetchone()
    return r["role"] if r else None


def need(db, u, pid, at_least):
    """The caller's role on pid, or an error. A profile not shared with the caller is reported as not found,
    so its existence isn't revealed."""
    r = role_of(db, u["id"], pid)
    if not r:
        raise HTTPException(404, "No such profile.")
    if RANK[r] < RANK[at_least]:
        raise HTTPException(403, f"This needs the {at_least} role on this profile.")
    return r


def _secure(request):
    if config.COOKIE_SECURE in ("1", "true", "yes", "on"):
        return True
    if config.COOKIE_SECURE in ("0", "false", "no", "off"):
        return False
    return request.url.scheme == "https"


def _start_session(request, response, db, uid):
    token, now = sec.new_token(), dbm.now()
    db.execute("DELETE FROM sessions WHERE expires<=?", (now,))
    db.execute("INSERT INTO sessions(token_hash, user_id, created, last_seen, expires, ip, agent) VALUES(?,?,?,?,?,?,?)",
               (sec.token_hash(token), uid, now, now, now + config.SESSION_DAYS * 86400,
                _client_ip(request), (request.headers.get("user-agent") or "")[:300]))
    response.set_cookie(config.COOKIE, token, max_age=config.SESSION_DAYS * 86400, path="/",
                        httponly=True, samesite="strict", secure=_secure(request))


# ---------------------------------------------------------------- setup and sign-in

class Setup(BaseModel):
    code: str
    username: str
    password: str
    profile_name: Optional[str] = None


@app.get("/api/setup")
def setup_state(db=Depends(get_db)):
    return {"needed": db.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None}


@app.post("/api/setup")
def setup(body: Setup, request: Request, response: Response, db=Depends(get_db)):
    keys = ("pair:setup|" + _client_ip(request),)
    if (w := sec.throttle.wait(*keys)):
        raise HTTPException(429, f"Too many tries. Wait {w} seconds.", headers={"Retry-After": str(w)})
    want = dbm.meta_get(db, "setup_code")
    if db.execute("SELECT 1 FROM users LIMIT 1").fetchone() or not want:
        raise HTTPException(409, "Sanguity is already set up. Sign in instead.")
    if not sec.same(sec.token_hash(body.code.strip().upper()), want):
        sec.throttle.fail(*keys)
        raise HTTPException(403, "That setup code is wrong. The current one is in the app's log (docker compose logs app).")
    with dbm.transaction(db):
        if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            raise HTTPException(409, "Sanguity is already set up. Sign in instead.")
        try:
            uid, _ = accounts.create(db, body.username, is_admin=True, password=body.password)
        except accounts.AccountError as e:
            raise HTTPException(e.status, str(e))
        owned = accounts.claim_unowned(db, uid)   # the records already on this server
        label = (body.profile_name or "").strip()[:80]
        if label and owned:
            db.execute("UPDATE profiles SET name=? WHERE id=?", (label, owned[0]))
        elif not owned:
            prof.create_profile(db, label or body.username.strip(), owner_id=uid)
    sec.throttle.clear(*keys)
    _start_session(request, response, db, uid)
    return {"ok": True}


class Login(BaseModel):
    username: str
    password: str


@app.post("/api/login")
def login(body: Login, request: Request, response: Response, db=Depends(get_db)):
    name = body.username.strip()[:64]
    u = db.execute("SELECT * FROM users WHERE username=?", (name,)).fetchone()
    device = _known_device(request, db, u)
    keys = sec.login_keys(name, _client_ip(request), device)
    if (w := sec.throttle.wait(*keys)):
        raise HTTPException(429, f"Too many failed sign-ins. Try again in {w} seconds.", headers={"Retry-After": str(w)})
    pw = body.password[:config.PASSWORD_MAX]   # no stored password is longer; the same string is checked and rehashed
    ok = sec.verify_password(u["pw_hash"] if u else None, pw)
    if not ok or u["disabled"]:
        sec.throttle.fail(*keys)
        raise HTTPException(401, "Wrong username or password.")
    sec.throttle.clear(keys[0])   # this person on this device got in; the address and account counts age out
    if sec.needs_rehash(u["pw_hash"]):
        db.execute("UPDATE users SET pw_hash=? WHERE id=?", (sec.hash_password(pw), u["id"]))
    _remember_device(request, response, db, u["id"])
    _start_session(request, response, db, u["id"])
    return {"must_change_pw": bool(u["must_change_pw"])}


def _known_device(request, db, u):
    """The device-cookie hash, when this browser has signed in to account u before; else None."""
    token = request.cookies.get(config.DEVICE_COOKIE)
    if not u or not token or len(token) > 200:
        return None
    h = sec.token_hash(token)
    r = db.execute("SELECT 1 FROM devices WHERE token_hash=? AND user_id=? AND last_seen>?",
                   (h, u["id"], dbm.now() - config.DEVICE_DAYS * 86400)).fetchone()
    return h if r else None


def _remember_device(request, response, db, uid):
    """Mark this browser as one account uid signs in from. A browser already known for any account keeps its
    token and gains a row for this one too, so on a shared tablet every person who signs in stays known."""
    now = dbm.now()
    token = request.cookies.get(config.DEVICE_COOKIE)
    if not token or len(token) > 200 or not db.execute(
            "SELECT 1 FROM devices WHERE token_hash=? AND last_seen>?", (sec.token_hash(token), now - config.DEVICE_DAYS * 86400)).fetchone():
        token = sec.new_token()   # only reuse tokens this server issued
    db.execute("DELETE FROM devices WHERE last_seen<=?", (now - config.DEVICE_DAYS * 86400,))
    db.execute("""INSERT INTO devices(token_hash, user_id, created, last_seen) VALUES(?, ?, ?, ?)
                  ON CONFLICT(token_hash, user_id) DO UPDATE SET last_seen=excluded.last_seen""", (sec.token_hash(token), uid, now, now))
    # only ever sent to the sign-in endpoint; it lets nobody in, it only keeps this browser's tries apart.
    # Set again on every sign-in, so a browser in use keeps it as long as the server does.
    response.set_cookie(config.DEVICE_COOKIE, token, max_age=config.DEVICE_DAYS * 86400, path="/api/login",
                        httponly=True, samesite="strict", secure=_secure(request))


@app.post("/api/logout")
def logout(request: Request, response: Response, db=Depends(get_db)):
    token = request.cookies.get(config.COOKIE)
    if token:
        db.execute("DELETE FROM sessions WHERE token_hash=?", (sec.token_hash(token),))
    response.delete_cookie(config.COOKIE, path="/", httponly=True, samesite="strict", secure=_secure(request))
    return {"ok": True}


# ---------------------------------------------------------------- the signed-in account

def _profiles_for(db, uid):
    return [dict(r) for r in db.execute(
        """SELECT p.id, p.name, m.role, (SELECT COUNT(*) FROM memberships x WHERE x.profile_id = p.id) AS members
           FROM memberships m JOIN profiles p ON p.id = m.profile_id WHERE m.user_id=? ORDER BY p.name COLLATE NOCASE, p.id""", (uid,))]


@app.get("/api/me")
def me(s=Depends(any_user), db=Depends(get_db)):
    return {"user": {"id": s["id"], "username": s["username"], "is_admin": bool(s["is_admin"]),
                     "must_change_pw": bool(s["must_change_pw"])},
            "profiles": _profiles_for(db, s["id"]) if not s["must_change_pw"] else [],
            "password_min": config.PASSWORD_MIN}


class PasswordChange(BaseModel):
    current: str
    new: str


@app.post("/api/me/password")
def change_password(body: PasswordChange, request: Request, s=Depends(any_user), db=Depends(get_db)):
    keys = ("pair:pw|" + s["username"].lower() + "|" + _client_ip(request),)
    if (w := sec.throttle.wait(*keys)):
        raise HTTPException(429, f"Too many wrong passwords. Try again in {w} seconds.")
    if not sec.verify_password(s["pw_hash"], body.current[:config.PASSWORD_MAX]):
        sec.throttle.fail(*keys)
        raise HTTPException(403, "Your current password is wrong.")
    if (p := sec.password_problem(body.new, s["username"])):
        raise HTTPException(400, p)
    if sec.verify_password(s["pw_hash"], body.new):
        raise HTTPException(400, "Choose a password different from the current one.")
    db.execute("UPDATE users SET pw_hash=?, must_change_pw=0 WHERE id=?", (sec.hash_password(body.new), s["id"]))
    n = db.execute("DELETE FROM sessions WHERE user_id=? AND id<>?", (s["id"], s["sid"])).rowcount
    return {"ok": True, "signed_out_elsewhere": n}


@app.get("/api/sessions")
def sessions(s=Depends(any_user), db=Depends(get_db)):
    return [{**dict(r), "current": r["id"] == s["sid"]} for r in db.execute(
        "SELECT id, created, last_seen, expires, ip, agent FROM sessions WHERE user_id=? AND expires>? ORDER BY last_seen DESC",
        (s["id"], dbm.now()))]


@app.delete("/api/sessions/{sid}")
def revoke_session(sid: int, s=Depends(any_user), db=Depends(get_db)):
    if not db.execute("DELETE FROM sessions WHERE id=? AND user_id=?", (sid, s["id"])).rowcount:
        raise HTTPException(404, "No such session.")
    return {"ok": True}


@app.post("/api/sessions/revoke-others")
def revoke_others(s=Depends(any_user), db=Depends(get_db)):
    n = db.execute("DELETE FROM sessions WHERE user_id=? AND id<>?", (s["id"], s["sid"])).rowcount
    return {"ok": True, "revoked": n}


# ---------------------------------------------------------------- profiles and sharing

class ProfileIn(BaseModel):
    name: str


def _profile_name(name):
    name = (name or "").strip()
    if not 1 <= len(name) <= 80:
        raise HTTPException(400, "Profile names are 1 to 80 characters.")
    return name


@app.post("/api/profiles")
def new_profile(body: ProfileIn, s=Depends(user), db=Depends(get_db)):
    pid = prof.create_profile(db, _profile_name(body.name), owner_id=s["id"])
    return {"id": pid}


@app.patch("/api/profiles/{pid}")
def rename_profile(pid: int, body: ProfileIn, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "owner")
    db.execute("UPDATE profiles SET name=? WHERE id=?", (_profile_name(body.name), pid))
    return {"ok": True}


@app.get("/api/profiles/{pid}/members")
def members(pid: int, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "owner")
    return [dict(r) for r in db.execute(
        """SELECT u.id AS user_id, u.username, m.role FROM memberships m JOIN users u ON u.id = m.user_id
           WHERE m.profile_id=? ORDER BY u.username COLLATE NOCASE""", (pid,))]


class MemberIn(BaseModel):
    username: str
    role: Literal["owner", "editor", "viewer"]


NEEDS_OWNER = "A profile needs at least one active owner. Make someone else an owner first."


def _keep_an_owner(db, pid, before):
    """After a change inside a transaction: refuse it if the profile had an active owner and now has none. (One
    already left without any, e.g. after its only owner was disabled, can still be changed, to repair it.)"""
    if before and not dbm.active_owners(db, pid):
        raise HTTPException(409, NEEDS_OWNER)


@app.put("/api/profiles/{pid}/members")
def share(pid: int, body: MemberIn, s=Depends(user), db=Depends(get_db)):
    """Share the profile with an account, or change that account's role."""
    need(db, s, pid, "owner")
    u = db.execute("SELECT id FROM users WHERE username=? AND disabled=0", (body.username.strip(),)).fetchone()
    if not u:
        raise HTTPException(404, "No active account with that username. An admin can create it under Account.")
    with dbm.transaction(db):
        before = dbm.active_owners(db, pid)
        db.execute("""INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, ?)
                      ON CONFLICT(user_id, profile_id) DO UPDATE SET role=excluded.role""", (u["id"], pid, body.role))
        _keep_an_owner(db, pid, before)
    return {"ok": True}


@app.delete("/api/profiles/{pid}/members/{uid}")
def unshare(pid: int, uid: int, s=Depends(user), db=Depends(get_db)):
    """An owner removes someone's access; anyone may remove their own (leave the profile)."""
    if uid != s["id"]:
        need(db, s, pid, "owner")
    with dbm.transaction(db):
        if not role_of(db, uid, pid):
            raise HTTPException(404, "That account doesn't have access to this profile.")
        before = dbm.active_owners(db, pid)
        db.execute("DELETE FROM memberships WHERE user_id=? AND profile_id=?", (uid, pid))
        _keep_an_owner(db, pid, before)
    return {"ok": True}


# ---------------------------------------------------------------- a profile's records

def _json_file(path):
    """Read the whole file in one go: a re-import replaces it with os.replace, and an open file keeps the version
    it opened, whereas FileResponse stats and opens separately and could mix two versions' length and bytes."""
    with open(path, "rb") as f:
        return Response(f.read(), media_type="application/json")


@app.get("/api/profiles/{pid}/data")
def profile_data(pid: int, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "viewer")
    prof.sync_build(db, pid)
    path = prof.data_path(pid)
    if not os.path.isfile(path):
        if (why := prof.build_error(pid)):
            raise HTTPException(500, f"The built records couldn't be loaded ({why}). Details are in the app's log.")
        return Response(status_code=204)   # the profile exists but has no records yet
    return _json_file(path)


@app.get("/api/profiles/{pid}/settings")
def profile_settings(pid: int, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "viewer")
    prof.sync_build(db, pid)
    path = prof.settings_path(pid)
    return _json_file(path) if os.path.isfile(path) else JSONResponse({})


@app.get("/api/profiles/{pid}/watch")
def profile_watch(pid: int, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "viewer")
    r = db.execute("SELECT watch_file FROM profiles WHERE id=?", (pid,)).fetchone()
    if not r or not r["watch_file"] or not os.path.isfile(r["watch_file"]):
        return Response(status_code=204)
    return _json_file(r["watch_file"])


DOC_CSP = ("default-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self' data:; "
           "media-src 'self'; script-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'")
DOC_TYPES = {"pdf": "application/pdf", "html": "text/html; charset=utf-8", "htm": "text/html; charset=utf-8",
             "txt": "text/plain; charset=utf-8", "xml": "text/plain; charset=utf-8", "jpg": "image/jpeg",
             "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "tif": "image/tiff", "tiff": "image/tiff"}


# what an HTML record may load from its own folder: pictures and stylesheets, nothing that runs
PART_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
              "webp": "image/webp", "bmp": "image/bmp", "css": "text/css; charset=utf-8"}


def _serve(path, ext, types):
    media = types.get(ext) or "application/octet-stream"   # unknown types download instead of rendering
    resp = FileResponse(path, media_type=media, content_disposition_type="inline" if ext in types else "attachment",
                        filename=os.path.basename(path))
    # shown in the app's own frame; no scripts, plugins or base URL inside records, and nothing fetched from
    # anywhere else (a remote image in a portal message would tell its sender when the record was read)
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    resp.headers["Content-Security-Policy"] = DOC_CSP
    return resp


def _document(db, s, pid, doc):
    need(db, s, pid, "viewer")
    r = db.execute("SELECT path, ext FROM documents WHERE id=? AND profile_id=?", (doc[:64], pid)).fetchone()
    if not r or not os.path.isfile(r["path"]):
        raise HTTPException(404, "Document not found.")
    return r


@app.get("/api/profiles/{pid}/documents/{doc}")
def document(pid: int, doc: str, s=Depends(user), db=Depends(get_db)):
    r = _document(db, s, pid, doc)
    return _serve(r["path"], r["ext"], DOC_TYPES)


@app.get("/api/profiles/{pid}/documents/{doc}/{part:path}")
def document_part(pid: int, doc: str, part: str, s=Depends(user), db=Depends(get_db)):
    """An HTML record by its own file name, or a picture or stylesheet in its folder that it links to."""
    r = _document(db, s, pid, doc)
    if part == os.path.basename(r["path"]):
        return _serve(r["path"], r["ext"], DOC_TYPES)
    folder = os.path.dirname(r["path"])
    target = os.path.join(folder, *part.split("/"))
    ext = os.path.splitext(target)[1].lstrip(".").lower()
    if r["ext"] not in prof.HTML_DOCS or ext not in PART_TYPES or not prof._inside(target, folder) or not os.path.isfile(target):
        raise HTTPException(404, "Document not found.")
    return _serve(os.path.realpath(target), ext, PART_TYPES)


# ---------------------------------------------------------------- accounts (admin)

@app.get("/api/users")
def users(s=Depends(admin), db=Depends(get_db)):
    return [dict(r) for r in db.execute(
        """SELECT id, username, is_admin, disabled, must_change_pw, created,
                  (SELECT COUNT(*) FROM sessions x WHERE x.user_id = users.id AND x.expires > ?) AS sessions
           FROM users ORDER BY username COLLATE NOCASE""", (dbm.now(),))]


class UserIn(BaseModel):
    username: str
    is_admin: bool = False


@app.post("/api/users")
def new_user(body: UserIn, s=Depends(admin), db=Depends(get_db)):
    """Create an account with a one-time password; its owner chooses their own at first sign-in."""
    try:
        uid, pw = accounts.create(db, body.username, is_admin=body.is_admin)
    except accounts.AccountError as e:
        raise HTTPException(e.status, str(e))
    return {"id": uid, "username": body.username.strip(), "temporary_password": pw}


class UserPatch(BaseModel):
    disabled: Optional[bool] = None
    is_admin: Optional[bool] = None
    reset_password: bool = False
    force: bool = False   # disable even though the account is the only active owner of some profiles


@app.patch("/api/users/{uid}")
def edit_user(uid: int, body: UserPatch, s=Depends(admin), db=Depends(get_db)):
    if uid == s["id"] and (body.disabled is not None or body.is_admin is not None or body.reset_password):
        raise HTTPException(400, "You can't change your own admin role, disable yourself or reset your own password here.")
    if not db.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
        raise HTTPException(404, "No such account.")
    if body.disabled and not body.force:
        sole = [r["name"] for r in db.execute(
            """SELECT p.id, p.name FROM memberships m JOIN profiles p ON p.id = m.profile_id
               WHERE m.user_id=? AND m.role='owner' ORDER BY p.name COLLATE NOCASE""", (uid,)).fetchall()
                if not dbm.active_owners(db, r["id"], excluding=uid)]
        if sole:   # nobody else could then share or rename these; the client asks, then resends with force
            raise HTTPException(409, {"sole_owner_of": sole, "message":
                f"This account is the only owner of {', '.join(sole)}. While it is disabled, nobody can share or rename "
                f"{'that profile' if len(sole) == 1 else 'those profiles'} (a server admin can still add an owner with "
                f"server.manage share). Disable it anyway?"})
    out = {"ok": True}
    if body.is_admin is not None:
        db.execute("UPDATE users SET is_admin=? WHERE id=?", (int(body.is_admin), uid))
    if body.disabled is not None:
        db.execute("UPDATE users SET disabled=? WHERE id=?", (int(body.disabled), uid))
        if body.disabled:
            accounts.sign_out_everywhere(db, uid)
    if body.reset_password:
        out["temporary_password"] = pw = accounts.temp_password()
        db.execute("UPDATE users SET pw_hash=?, must_change_pw=1 WHERE id=?", (sec.hash_password(pw), uid))
        accounts.sign_out_everywhere(db, uid)   # a reset may follow a lost or stolen device
    return out


# ---------------------------------------------------------------- health check, and static pages for development

@app.get("/api/healthz")
def healthz():
    return Response(status_code=204)


if os.environ.get("SANGUITY_SERVE_STATIC") == "1":   # in Docker, nginx serves www/ and only proxies /api/ here
    from fastapi.staticfiles import StaticFiles

    for sub in ("css", "js"):
        app.mount(f"/{sub}", StaticFiles(directory=os.path.join(WWW, sub)), name=sub)

    @app.get("/{page}")
    @app.get("/", include_in_schema=False)
    def page(page: str = "index.html"):
        if page not in ("index.html", "login.html", "setup.html", "account.html"):
            raise HTTPException(404)
        return FileResponse(os.path.join(WWW, page), media_type="text/html", headers={"Cache-Control": "no-store"})
