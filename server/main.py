"""Sanguity API. Every route that touches health data checks the caller's role on that profile.

Roles on a profile:  owner (view, import/edit, share, delete) · editor (view, import/edit) · viewer (view).
Admin is an account flag for managing accounts only; it gives no access to anyone's records.
"""
import logging, os, secrets, string, threading, urllib.parse
from contextlib import asynccontextmanager
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import accounts, activity, atrest, backup, config, crypto, db as dbm, export, passkeys, profiles as prof, security as sec, twofactor

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
    with backup.app_running_lock():   # held while the app runs, so a restore can tell and refuse
        dbm.init()
        db = dbm.connect()
        try:
            atrest.check_key(db)   # stops here, saying why, if the key is missing or wrong
            sealed = atrest.seal_everything(db)
            if sealed["files"] or sealed["fields"]:
                print(f"Encrypted {sealed['files']} files and {sealed['fields']} database fields that weren't yet.", flush=True)
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


@app.exception_handler(passkeys.PasskeyError)
async def passkey_error(request: Request, exc: passkeys.PasskeyError):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


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


def settled(s=Depends(any_user)):
    """Signed in, with no pending password change (two-factor setup may still be due)."""
    if s["must_change_pw"]:
        raise HTTPException(403, "password_change_required")
    return s


def user(s=Depends(settled), db=Depends(get_db)):
    """Signed in, with nothing left to set up: no pending password change, and two-factor sign-in on if the server
    requires it."""
    if accounts.two_factor_required(db) and not accounts.has_second_factor(db, s):
        raise HTTPException(403, "two_factor_required")
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
        first = {"id": uid, "username": body.username.strip()}
        activity.log(db, "setup", first, ip=_client_ip(request))
        label = (body.profile_name or "").strip()[:80]
        if label and owned:
            db.execute("UPDATE profiles SET name=? WHERE id=?", (label, owned[0]))
        elif not owned:
            pid = prof.create_profile(db, label or body.username.strip(), owner_id=uid)
            activity.log(db, "create", first, pid, ip=_client_ip(request))
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
    keys_n = passkeys.count(db, u["id"])
    if u["totp_secret"] or keys_n:   # the password was right; now the code or a passkey
        return {"two_factor": True, "ticket": _tickets.issue(u["id"]),
                "methods": {"code": bool(u["totp_secret"]), "passkey": keys_n > 0}}
    _remember_device(request, response, db, u["id"])
    _start_session(request, response, db, u["id"])
    return {"must_change_pw": bool(u["must_change_pw"])}


class _Tickets:
    """Between a correct password and a correct code: a short-lived, single-account ticket. In memory (a restart
    means entering the password again); five wrong codes and it's gone, besides the sign-in throttle."""
    TTL, TRIES, MAX = 300, 5, 10000

    def __init__(self):
        self._t, self._lock = {}, threading.Lock()

    def issue(self, uid):
        token, now = sec.new_token(), dbm.now()
        with self._lock:
            if len(self._t) >= self.MAX:
                self._t = {k: v for k, v in self._t.items() if v[1] > now}
            self._t[sec.token_hash(token)] = [uid, now + self.TTL, 0]
        return token

    def get(self, token):
        with self._lock:
            t = self._t.get(sec.token_hash(token or ""))
            return t[0] if t and t[1] > dbm.now() else None

    def failed(self, token):
        with self._lock:
            t = self._t.get(sec.token_hash(token))
            if t:
                t[2] += 1
                if t[2] >= self.TRIES:
                    del self._t[sec.token_hash(token)]

    def used(self, token):
        with self._lock:
            self._t.pop(sec.token_hash(token), None)


_tickets = _Tickets()


class LoginCode(BaseModel):
    ticket: str
    code: str


@app.post("/api/login/code")
def login_code(body: LoginCode, request: Request, response: Response, db=Depends(get_db)):
    """The second step: a code from the authenticator app, or a recovery code."""
    uid = _tickets.get(body.ticket[:200])
    u = db.execute("SELECT * FROM users WHERE id=? AND disabled=0", (uid,)).fetchone() if uid else None
    if not u or not u["totp_secret"]:
        raise HTTPException(401, "That took too long. Enter your password again.")
    keys = ("pair:2fa|" + str(uid) + "|" + _client_ip(request), "user:2fa|" + str(uid))
    if (w := sec.throttle.wait(*keys)):
        raise HTTPException(429, f"Too many wrong codes. Try again in {w} seconds.", headers={"Retry-After": str(w)})
    out = {"must_change_pw": bool(u["must_change_pw"])}
    if twofactor.looks_like_code(body.code):
        step = twofactor.verify(crypto.dec(u["totp_secret"]), body.code, u["totp_last_step"])
        if step is None:
            sec.throttle.fail(*keys)
            _tickets.failed(body.ticket)
            raise HTTPException(401, "That code isn't right. Check your authenticator app's time, or use a recovery code.")
        db.execute("UPDATE users SET totp_last_step=? WHERE id=?", (step, uid))
    else:
        hit = db.execute("UPDATE recovery_codes SET used_at=? WHERE user_id=? AND code_hash=? AND used_at IS NULL",
                         (dbm.now(), uid, twofactor.recovery_hash(body.code))).rowcount
        if not hit:
            sec.throttle.fail(*keys)
            _tickets.failed(body.ticket)
            raise HTTPException(401, "That recovery code isn't right, or it was used already.")
        accounts.sign_out_everywhere(db, uid)   # a recovery code may mean a lost phone: end every other sign-in
        out["recovery_codes_left"] = left = db.execute(
            "SELECT COUNT(*) FROM recovery_codes WHERE user_id=? AND used_at IS NULL", (uid,)).fetchone()[0]
        activity.log(db, "2fa.recovery", u, detail={"left": left}, ip=_client_ip(request))
    _tickets.used(body.ticket)
    sec.throttle.clear(keys[0])
    _remember_device(request, response, db, uid)
    _start_session(request, response, db, uid)
    return out


class PasskeyStart(BaseModel):
    ticket: Optional[str] = None   # after a correct password; without one, a passkey signs in on its own


class PasskeyFinish(BaseModel):
    state: str
    credential: dict
    ticket: Optional[str] = None


@app.post("/api/login/passkey/options")
def login_passkey_options(body: PasskeyStart, request: Request, db=Depends(get_db)):
    uid = None
    if body.ticket:
        uid = _tickets.get(body.ticket[:200])
        if not uid:
            raise HTTPException(401, "That took too long. Enter your password again.")
    state, options = passkeys.authentication_options(db, request.headers.get("origin"), uid)
    return {"state": state, "options": options}


@app.post("/api/login/passkey")
def login_passkey(body: PasskeyFinish, request: Request, response: Response, db=Depends(get_db)):
    """Sign in with a passkey: the step after the password (with the ticket), or on its own."""
    keys = ("ip:passkey|" + _client_ip(request),)
    if (w := sec.throttle.wait(*keys)):
        raise HTTPException(429, f"Too many failed sign-ins. Try again in {w} seconds.", headers={"Retry-After": str(w)})
    want = None
    if body.ticket:
        want = _tickets.get(body.ticket[:200])
        if not want:
            raise HTTPException(401, "That took too long. Enter your password again.")
    try:
        uid, name = passkeys.authenticate(db, body.state[:200], body.credential)
    except passkeys.PasskeyError:
        sec.throttle.fail(*keys)
        if body.ticket:
            _tickets.failed(body.ticket[:200])
        raise
    if want is not None and uid != want:   # the state was issued for this ticket's account, so this can't happen
        raise HTTPException(401, passkeys.STALE)
    u = db.execute("SELECT * FROM users WHERE id=? AND disabled=0", (uid,)).fetchone()
    if not u:
        sec.throttle.fail(*keys)
        raise HTTPException(401, "That account can't sign in.")
    if body.ticket:
        _tickets.used(body.ticket[:200])
    _remember_device(request, response, db, uid)
    _start_session(request, response, db, uid)
    return {"must_change_pw": bool(u["must_change_pw"]), "username": u["username"]}


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
    required = accounts.two_factor_required(db)
    second = accounts.has_second_factor(db, s)
    needs_2fa = required and not second
    return {"user": {"id": s["id"], "username": s["username"], "is_admin": bool(s["is_admin"]),
                     "must_change_pw": bool(s["must_change_pw"]), "two_factor": second,
                     "needs_two_factor": needs_2fa},
            "two_factor_required": required,
            "profiles": _profiles_for(db, s["id"]) if not (s["must_change_pw"] or needs_2fa) else [],
            "password_min": _password_min(db, s)}


def _password_min(db, s):
    return config.PASSWORD_MIN_2FA if accounts.has_second_factor(db, s) else config.PASSWORD_MIN


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
    if (p := sec.password_problem(body.new, s["username"], _password_min(db, s))):
        raise HTTPException(400, p)
    if sec.verify_password(s["pw_hash"], body.new):
        raise HTTPException(400, "Choose a password different from the current one.")
    db.execute("UPDATE users SET pw_hash=?, must_change_pw=0, pw_short=? WHERE id=?",
               (sec.hash_password(body.new), int(len(body.new) < config.PASSWORD_MIN), s["id"]))
    n = db.execute("DELETE FROM sessions WHERE user_id=? AND id<>?", (s["id"], s["sid"])).rowcount
    activity.log(db, "password", s, ip=_client_ip(request))
    return {"ok": True, "signed_out_elsewhere": n}


# ---------------------------------------------------------------- two-factor sign-in, for the signed-in account

class TwoFactorCode(BaseModel):
    code: str


class TwoFactorOff(BaseModel):
    password: str


@app.get("/api/me/2fa")
def two_factor_state(s=Depends(settled), db=Depends(get_db)):
    left = db.execute("SELECT COUNT(*) FROM recovery_codes WHERE user_id=? AND used_at IS NULL", (s["id"],)).fetchone()[0]
    return {"on": bool(s["totp_secret"]), "recovery_codes_left": left if s["totp_secret"] else 0,
            "required": accounts.two_factor_required(db), "passkeys": passkeys.count(db, s["id"])}


@app.post("/api/me/2fa/start")
def two_factor_start(s=Depends(settled), db=Depends(get_db)):
    """A new secret to scan; it takes effect only once a code from it is confirmed."""
    if s["totp_secret"]:
        raise HTTPException(409, "Two-factor sign-in is already on.")
    secret = twofactor.new_secret()
    db.execute("UPDATE users SET totp_pending=? WHERE id=?", (crypto.enc(secret), s["id"]))
    link = twofactor.uri(secret, s["username"])
    return {"secret": secret, "uri": link, "qr": twofactor.qr_data_uri(link)}


@app.post("/api/me/2fa/confirm")
def two_factor_confirm(body: TwoFactorCode, request: Request, s=Depends(settled), db=Depends(get_db)):
    if s["totp_secret"]:
        raise HTTPException(409, "Two-factor sign-in is already on.")
    keys = ("pair:2fa|" + str(s["id"]) + "|" + _client_ip(request),)
    if (w := sec.throttle.wait(*keys)):
        raise HTTPException(429, f"Too many wrong codes. Try again in {w} seconds.")
    step = twofactor.verify(crypto.dec(s["totp_pending"]), body.code)
    if step is None:
        sec.throttle.fail(*keys)
        raise HTTPException(400, "That code doesn't match. Scan the QR code again, and check the time on your phone.")
    with dbm.transaction(db):
        db.execute("UPDATE users SET totp_secret=totp_pending, totp_pending=NULL, totp_last_step=? WHERE id=?", (step, s["id"]))
        codes = accounts.new_recovery_codes(db, s["id"])
        activity.log(db, "2fa.on", s, ip=_client_ip(request))
    return {"recovery_codes": codes}


@app.post("/api/me/2fa/recovery-codes")
def two_factor_new_codes(body: TwoFactorCode, request: Request, s=Depends(settled), db=Depends(get_db)):
    """Replace the recovery codes; needs a current code from the app."""
    if not s["totp_secret"]:
        raise HTTPException(409, "Two-factor sign-in is off.")
    keys = ("pair:2fa|" + str(s["id"]) + "|" + _client_ip(request),)
    if (w := sec.throttle.wait(*keys)):
        raise HTTPException(429, f"Too many wrong codes. Try again in {w} seconds.")
    step = twofactor.verify(crypto.dec(s["totp_secret"]), body.code, s["totp_last_step"])
    if step is None:
        sec.throttle.fail(*keys)
        raise HTTPException(400, "That code isn't right.")
    with dbm.transaction(db):
        db.execute("UPDATE users SET totp_last_step=? WHERE id=?", (step, s["id"]))
        codes = accounts.new_recovery_codes(db, s["id"])
        activity.log(db, "2fa.codes", s, ip=_client_ip(request))
    return {"recovery_codes": codes}


@app.post("/api/me/2fa/off")
def two_factor_off(body: TwoFactorOff, request: Request, s=Depends(settled), db=Depends(get_db)):
    """Turn it off; needs the password. Signs out everywhere else."""
    if not s["totp_secret"]:
        raise HTTPException(409, "Two-factor sign-in is already off.")
    if accounts.two_factor_required(db) and not passkeys.count(db, s["id"]):
        raise HTTPException(409, "This server requires two-factor sign-in, so it can't be turned off. "
                                 "Add a passkey first if you want to stop using the authenticator app.")
    keys = ("pair:pw|" + s["username"].lower() + "|" + _client_ip(request),)
    if (w := sec.throttle.wait(*keys)):
        raise HTTPException(429, f"Too many wrong passwords. Try again in {w} seconds.")
    if not sec.verify_password(s["pw_hash"], body.password[:config.PASSWORD_MAX]):
        sec.throttle.fail(*keys)
        raise HTTPException(403, "Your password is wrong.")
    with dbm.transaction(db):
        accounts.turn_off_2fa(db, s["id"])
        activity.log(db, "2fa.off", s, ip=_client_ip(request))
    # turn_off_2fa ended every session, this one too: start a fresh one so the page keeps working
    after = db.execute("SELECT must_change_pw FROM users WHERE id=?", (s["id"],)).fetchone()
    response = JSONResponse({"ok": True, "must_change_pw": bool(after["must_change_pw"])})
    _start_session(request, response, db, s["id"])
    return response


# ---------------------------------------------------------------- passkeys, for the signed-in account

class PasskeyAdd(BaseModel):
    password: str


class PasskeyRegister(BaseModel):
    state: str
    credential: dict
    name: Optional[str] = None


class PasskeyRename(BaseModel):
    name: str


@app.get("/api/me/passkeys")
def my_passkeys(origin: str = "", s=Depends(settled), db=Depends(get_db)):
    """origin: the page's own (location.origin), to say which passkeys work there; only shown, never trusted."""
    try:
        here = passkeys.site(origin[:300])[0]
        problem = None
    except passkeys.PasskeyError as e:
        here, problem = None, str(e)
    return {"passkeys": passkeys.listing(db, s["id"], here), "site": here, "problem": problem,
            "user_handle": passkeys.user_handle(db, s["id"]),
            "required": accounts.two_factor_required(db), "authenticator_app": bool(s["totp_secret"])}


@app.post("/api/me/passkeys/options")
def passkey_add_options(body: PasskeyAdd, request: Request, s=Depends(settled), db=Depends(get_db)):
    """Adding a passkey adds a way in, so it needs the password."""
    keys = ("pair:pw|" + s["username"].lower() + "|" + _client_ip(request),)
    if (w := sec.throttle.wait(*keys)):
        raise HTTPException(429, f"Too many wrong passwords. Try again in {w} seconds.")
    if not sec.verify_password(s["pw_hash"], body.password[:config.PASSWORD_MAX]):
        sec.throttle.fail(*keys)
        raise HTTPException(403, "Your password is wrong.")
    state, options = passkeys.registration_options(db, s, request.headers.get("origin"))
    return {"state": state, "options": options}


@app.post("/api/me/passkeys")
def passkey_add(body: PasskeyRegister, request: Request, s=Depends(settled), db=Depends(get_db)):
    pk = passkeys.register(db, s, body.state[:200], body.credential, body.name)
    name = db.execute("SELECT name FROM passkeys WHERE id=?", (pk,)).fetchone()[0]
    activity.log(db, "passkey.add", s, detail={"name": name}, ip=_client_ip(request))
    return {"id": pk, "name": name}


@app.patch("/api/me/passkeys/{pk}")
def passkey_rename(pk: int, body: PasskeyRename, s=Depends(settled), db=Depends(get_db)):
    name = " ".join(body.name.split())[:passkeys.NAME_MAX]
    if not name:
        raise HTTPException(400, "Give the passkey a name.")
    if not db.execute("UPDATE passkeys SET name=? WHERE id=? AND user_id=?", (name, pk, s["id"])).rowcount:
        raise HTTPException(404, "No such passkey.")
    return {"name": name}


@app.delete("/api/me/passkeys/{pk}")
def passkey_remove(pk: int, request: Request, s=Depends(settled), db=Depends(get_db)):
    """Remove one. The password always still signs in; but where the server requires two-factor sign-in, the last
    second factor can't go (that would leave the account unable to use anything until an admin stepped in)."""
    with dbm.transaction(db):
        r = db.execute("SELECT name FROM passkeys WHERE id=? AND user_id=?", (pk, s["id"])).fetchone()
        if not r:
            raise HTTPException(404, "No such passkey.")
        if accounts.two_factor_required(db) and not s["totp_secret"] and passkeys.count(db, s["id"]) == 1:
            raise HTTPException(409, "This server requires two-factor sign-in, and this is your only second step. "
                                     "Add another passkey or turn on the authenticator app first.")
        db.execute("DELETE FROM passkeys WHERE id=?", (pk,))
        accounts.short_password_check(db, s["id"])
        activity.log(db, "passkey.remove", s, detail={"name": r["name"]}, ip=_client_ip(request))
    after = db.execute("SELECT must_change_pw FROM users WHERE id=?", (s["id"],)).fetchone()
    return {"ok": True, "left": passkeys.count(db, s["id"]), "must_change_pw": bool(after["must_change_pw"])}


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
def new_profile(body: ProfileIn, request: Request, s=Depends(user), db=Depends(get_db)):
    pid = prof.create_profile(db, _profile_name(body.name), owner_id=s["id"])
    activity.log(db, "create", s, pid, ip=_client_ip(request))
    return {"id": pid}


class ProfileDelete(BaseModel):
    confirm_name: str


@app.delete("/api/profiles/{pid}")
def delete_profile(pid: int, body: ProfileDelete, request: Request, s=Depends(user), db=Depends(get_db)):
    """Delete a profile and everything in it (owners only), confirmed by typing its name."""
    need(db, s, pid, "owner")
    name = db.execute("SELECT name FROM profiles WHERE id=?", (pid,)).fetchone()["name"]
    if body.confirm_name.strip() != name:
        raise HTTPException(400, "Type the profile's name exactly to confirm.")
    gone = prof.delete_profile(db, pid)
    activity.log(db, "profile.delete", s, detail={"name": gone["name"], "members": gone["members"]}, ip=_client_ip(request))
    return {"ok": True, "unlinked_build": gone["linked_build"]}


@app.patch("/api/profiles/{pid}")
def rename_profile(pid: int, body: ProfileIn, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "owner")
    name, old = _profile_name(body.name), db.execute("SELECT name FROM profiles WHERE id=?", (pid,)).fetchone()["name"]
    if name != old:
        db.execute("UPDATE profiles SET name=? WHERE id=?", (name, pid))
        activity.log(db, "rename", s, pid, {"from": old, "to": name}, _client_ip(request))
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
def share(pid: int, body: MemberIn, request: Request, s=Depends(user), db=Depends(get_db)):
    """Share the profile with an account, or change that account's role."""
    need(db, s, pid, "owner")
    u = db.execute("SELECT id, username FROM users WHERE username=? AND disabled=0", (body.username.strip(),)).fetchone()
    if not u:
        raise HTTPException(404, "No active account with that username. An admin can create it under Account.")
    with dbm.transaction(db):
        before, old = dbm.active_owners(db, pid), role_of(db, u["id"], pid)
        db.execute("""INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, ?)
                      ON CONFLICT(user_id, profile_id) DO UPDATE SET role=excluded.role""", (u["id"], pid, body.role))
        _keep_an_owner(db, pid, before)
        if old != body.role:
            activity.log(db, "share", s, pid, {"username": u["username"], "role": body.role, **({"from": old} if old else {})},
                         _client_ip(request))
    return {"ok": True}


@app.delete("/api/profiles/{pid}/members/{uid}")
def unshare(pid: int, uid: int, request: Request, s=Depends(user), db=Depends(get_db)):
    """An owner removes someone's access; anyone may remove their own (leave the profile)."""
    if uid != s["id"]:
        need(db, s, pid, "owner")
    with dbm.transaction(db):
        if not role_of(db, uid, pid):
            raise HTTPException(404, "That account doesn't have access to this profile.")
        before = dbm.active_owners(db, pid)
        db.execute("DELETE FROM memberships WHERE user_id=? AND profile_id=?", (uid, pid))
        _keep_an_owner(db, pid, before)
        name = db.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()["username"]
        activity.log(db, "unshare", s, pid, {"username": name}, _client_ip(request))
    return {"ok": True}


@app.get("/api/profiles/{pid}/activity")
def profile_activity(pid: int, actor: Optional[int] = None, action: Optional[str] = None, before: Optional[int] = None,
                     limit: int = 100, s=Depends(user), db=Depends(get_db)):
    """The profile's activity log, newest first (owners only). actor: an account id; before: an entry id."""
    need(db, s, pid, "owner")
    return activity.for_profile(db, pid, actor, action, before, limit)


# ---------------------------------------------------------------- a profile's records

def _json_file(path):
    """Read the whole file in one go: a re-import replaces it with os.replace, and an open file keeps the version
    it opened, whereas FileResponse stats and opens separately and could mix two versions' length and bytes."""
    return Response(crypto.read_file(path), media_type="application/json")   # decrypted, if Sanguity wrote it


@app.get("/api/profiles/{pid}/data")
def profile_data(pid: int, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "viewer")
    prof.sync_build(db, pid)
    path = prof.data_path(pid)
    if not os.path.isfile(path):
        if (why := prof.build_error(pid)):
            raise HTTPException(500, f"The built records couldn't be loaded ({why}). Details are in the app's log.")
        return Response(status_code=204)   # the profile exists but has no records yet
    activity.once(db, s, pid, "view", _client_ip(request))
    return _json_file(path)


@app.get("/api/profiles/{pid}/export")
def profile_export(pid: int, request: Request, s=Depends(user), db=Depends(get_db)):
    """Everything in the profile as one zip, streamed (owners and editors)."""
    need(db, s, pid, "editor")
    prof.sync_build(db, pid)
    p = export.plan(db, pid)
    activity.log(db, "export", s, pid, ip=_client_ip(request))
    return StreamingResponse(export.stream(p, s["username"]), media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{export.filename(p["name"])}"'})


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


PART_TYPES = prof.PART_TYPES


def _serve(path, ext, types):
    media = types.get(ext) or "application/octet-stream"   # unknown types download instead of rendering
    disposition = "inline" if ext in types else "attachment"
    if crypto.file_is_encrypted(path):   # imported into the profile's folder: decrypted here, whole
        resp = Response(crypto.read_file(path), media_type=media,
                        headers={"Content-Disposition": f'{disposition}; filename="{urllib.parse.quote(os.path.basename(path))}"'})
    else:   # an original export: streamed as it is
        resp = FileResponse(path, media_type=media, content_disposition_type=disposition, filename=os.path.basename(path))
    # shown in the app's own frame; no scripts, plugins or base URL inside records, and nothing fetched from
    # anywhere else (a remote image in a portal message would tell its sender when the record was read)
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    resp.headers["Content-Security-Policy"] = DOC_CSP
    return resp


def _document(db, s, pid, doc):
    need(db, s, pid, "viewer")
    r = db.execute("SELECT path, ext FROM documents WHERE id=? AND profile_id=?", (doc[:64], pid)).fetchone()
    r = {"path": crypto.dec(r["path"]), "ext": r["ext"]} if r else None
    if not r or not os.path.isfile(r["path"]):
        raise HTTPException(404, "Document not found.")
    return r


def _opened(db, s, pid, doc, r, request):
    activity.once(db, s, pid, "document", _client_ip(request), {"name": os.path.basename(r["path"])}, key=doc[:64])


@app.get("/api/profiles/{pid}/documents/{doc}")
def document(pid: int, doc: str, request: Request, s=Depends(user), db=Depends(get_db)):
    r = _document(db, s, pid, doc)
    _opened(db, s, pid, doc, r, request)
    return _serve(r["path"], r["ext"], DOC_TYPES)


@app.get("/api/profiles/{pid}/documents/{doc}/{part:path}")
def document_part(pid: int, doc: str, part: str, request: Request, s=Depends(user), db=Depends(get_db)):
    """An HTML record by its own file name, or a picture or stylesheet in its folder that it links to."""
    r = _document(db, s, pid, doc)
    if part == os.path.basename(r["path"]):
        _opened(db, s, pid, doc, r, request)
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
                  totp_secret IS NOT NULL OR EXISTS(SELECT 1 FROM passkeys k WHERE k.user_id = users.id) AS two_factor,
                  (SELECT COUNT(*) FROM sessions x WHERE x.user_id = users.id AND x.expires > ?) AS sessions
           FROM users ORDER BY username COLLATE NOCASE""", (dbm.now(),))]


class UserIn(BaseModel):
    username: str
    is_admin: bool = False


@app.post("/api/users")
def new_user(body: UserIn, request: Request, s=Depends(admin), db=Depends(get_db)):
    """Create an account with a one-time password; its owner chooses their own at first sign-in."""
    try:
        uid, pw = accounts.create(db, body.username, is_admin=body.is_admin)
    except accounts.AccountError as e:
        raise HTTPException(e.status, str(e))
    activity.log(db, "account.create", s, detail={"username": body.username.strip(), **({"admin": True} if body.is_admin else {})},
                 ip=_client_ip(request))
    return {"id": uid, "username": body.username.strip(), "temporary_password": pw}


@app.get("/api/activity")
def account_activity(before: Optional[int] = None, limit: int = 100, s=Depends(admin), db=Depends(get_db)):
    """Account events, newest first (admins only; no profile's records or views)."""
    return activity.for_accounts(db, before, limit)


class UserPatch(BaseModel):
    disabled: Optional[bool] = None
    is_admin: Optional[bool] = None
    reset_password: bool = False
    force: bool = False   # disable even though the account is the only active owner of some profiles
    disable_2fa: bool = False   # for someone who lost both their phone and their recovery codes


class UserRemove(BaseModel):
    hand_to: Optional[str] = None   # username to become owner of the profiles this account alone owned
    force: bool = False             # remove anyway, leaving those profiles without an active owner


@app.delete("/api/users/{uid}")
def remove_user(uid: int, body: UserRemove, request: Request, s=Depends(admin), db=Depends(get_db)):
    """Delete an account for good (admins; not your own, not the last admin)."""
    if uid == s["id"]:
        raise HTTPException(400, "You can't remove your own account here.")
    target = db.execute("SELECT username, is_admin FROM users WHERE id=?", (uid,)).fetchone()
    if not target:
        raise HTTPException(404, "No such account.")
    if target["is_admin"] and db.execute("SELECT COUNT(*) FROM users WHERE is_admin=1 AND disabled=0 AND id<>?", (uid,)).fetchone()[0] == 0:
        raise HTTPException(409, "That's the last active admin.")
    hand_to = None
    if body.hand_to:
        h = db.execute("SELECT id FROM users WHERE username=? AND disabled=0", (body.hand_to.strip(),)).fetchone()
        if not h or h["id"] == uid:
            raise HTTPException(404, "No other active account with that username to hand the profiles to.")
        hand_to = h["id"]
    sole = [name for _, name in accounts.sole_owned(db, uid)]
    if sole and hand_to is None and not body.force:   # the client asks who should own them, then resends
        raise HTTPException(409, {"sole_owner_of": sole, "message":
            f"{target['username']} is the only owner of {', '.join(sole)}. Who should own "
            f"{'it' if len(sole) == 1 else 'them'} instead? Without an owner nobody can share or rename "
            f"{'it' if len(sole) == 1 else 'them'}."})
    gone = accounts.remove(db, uid, hand_to)
    activity.log(db, "account.remove", s, detail=gone, ip=_client_ip(request))
    return {"ok": True, **gone}


# ---------------------------------------------------------------- profiles nobody can manage (admin repair)

@app.get("/api/ownerless")
def ownerless(s=Depends(admin), db=Depends(get_db)):
    """Profiles with no active owner (every owner disabled or removed): names and members only, never records."""
    out = []
    for p in db.execute("SELECT id, name FROM profiles ORDER BY name COLLATE NOCASE").fetchall():
        if dbm.active_owners(db, p["id"]):
            continue
        members = [dict(r) for r in db.execute(
            """SELECT u.id AS user_id, u.username, m.role, u.disabled FROM memberships m JOIN users u ON u.id = m.user_id
               WHERE m.profile_id=? ORDER BY u.username COLLATE NOCASE""", (p["id"],))]
        out.append({"id": p["id"], "name": p["name"], "members": members})
    return out


class OwnerIn(BaseModel):
    username: str


@app.post("/api/ownerless/{pid}/owner")
def repair_owner(pid: int, body: OwnerIn, request: Request, s=Depends(admin), db=Depends(get_db)):
    """Give a profile that has no active owner a new one. Only for such profiles: any other is its owners' to share."""
    p = db.execute("SELECT name FROM profiles WHERE id=?", (pid,)).fetchone()
    if not p:
        raise HTTPException(404, "No such profile.")
    u = db.execute("SELECT id, username FROM users WHERE username=? AND disabled=0", (body.username.strip(),)).fetchone()
    if not u:
        raise HTTPException(404, "No active account with that username.")
    with dbm.transaction(db):
        if dbm.active_owners(db, pid):
            raise HTTPException(409, "That profile has an active owner, who can share it.")
        old = role_of(db, u["id"], pid)
        db.execute("""INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'owner')
                      ON CONFLICT(user_id, profile_id) DO UPDATE SET role='owner'""", (u["id"], pid))
    ip = _client_ip(request)   # in both logs: the profile's owners see who was given it, and by whom
    activity.log(db, "share", s, pid, {"username": u["username"], "role": "owner", **({"from": old} if old else {}), "repair": True}, ip)
    activity.log(db, "profile.repair", s, detail={"name": p["name"], "username": u["username"]}, ip=ip)
    return {"ok": True}


@app.patch("/api/users/{uid}")
def edit_user(uid: int, body: UserPatch, request: Request, s=Depends(admin), db=Depends(get_db)):
    if uid == s["id"] and (body.disabled is not None or body.is_admin is not None or body.reset_password or body.disable_2fa):
        raise HTTPException(400, "You can't change your own admin role, disable yourself or reset your own password here.")
    target = db.execute("SELECT username, is_admin, disabled FROM users WHERE id=?", (uid,)).fetchone()
    if not target:
        raise HTTPException(404, "No such account.")
    note = lambda action: activity.log(db, action, s, detail={"username": target["username"]}, ip=_client_ip(request))
    if body.disabled and not body.force:
        sole = [name for _, name in accounts.sole_owned(db, uid)]
        if sole:   # nobody else could then share or rename these; the client asks, then resends with force
            raise HTTPException(409, {"sole_owner_of": sole, "message":
                f"This account is the only owner of {', '.join(sole)}. While it is disabled, nobody can share or rename "
                f"{'that profile' if len(sole) == 1 else 'those profiles'} until an admin gives "
                f"{'it' if len(sole) == 1 else 'them'} a new owner under Accounts. Disable it anyway?"})
    out = {"ok": True}
    if body.is_admin is not None and body.is_admin != bool(target["is_admin"]):
        db.execute("UPDATE users SET is_admin=? WHERE id=?", (int(body.is_admin), uid))
        note("account.admin" if body.is_admin else "account.unadmin")
    if body.disabled is not None and body.disabled != bool(target["disabled"]):
        db.execute("UPDATE users SET disabled=? WHERE id=?", (int(body.disabled), uid))
        if body.disabled:
            accounts.sign_out_everywhere(db, uid)
        note("account.disable" if body.disabled else "account.enable")
    if body.reset_password:
        out["temporary_password"] = pw = accounts.temp_password()
        db.execute("UPDATE users SET pw_hash=?, must_change_pw=1 WHERE id=?", (sec.hash_password(pw), uid))
        accounts.sign_out_everywhere(db, uid)   # a reset may follow a lost or stolen device
        note("account.reset")
    if body.disable_2fa:
        accounts.turn_off_2fa(db, uid, passkeys_too=True)
        note("2fa.reset")
    return out


class ServerSettings(BaseModel):
    require_2fa: bool


@app.get("/api/settings")
def server_settings(s=Depends(admin), db=Depends(get_db)):
    return {"require_2fa": accounts.two_factor_required(db)}


@app.patch("/api/settings")
def change_server_settings(body: ServerSettings, request: Request, s=Depends(admin), db=Depends(get_db)):
    if body.require_2fa != accounts.two_factor_required(db):
        if body.require_2fa and not accounts.has_second_factor(db, s):
            raise HTTPException(409, "Turn on two-factor sign-in for your own account first.")
        dbm.meta_set(db, "require_2fa", "1" if body.require_2fa else None)
        activity.log(db, "settings.require_2fa", s, detail={"on": body.require_2fa}, ip=_client_ip(request))
    return {"require_2fa": body.require_2fa}


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
