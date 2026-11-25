"""Sanguity API. Every route that touches health data checks the caller's role on that profile.

Roles on a profile:  owner (view, import/edit, share, delete) · editor (view, import/edit) · viewer (view).
Admin is an account flag for managing accounts only; it gives no access to anyone's records.
"""
import json, logging, os, secrets, string, sys, threading, traceback, urllib.parse
from contextlib import asynccontextmanager
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import accounts, activity, alerts, atrest, backup, backup_jobs, calendar, config, crypto, db as dbm, edits, export, fhir_export, hrzones, jobs, milestones, passkeys, profiles as prof, records, security as sec, settings, sync, twofactor, units, uploads, watch_keys, watch_store

log = logging.getLogger("sanguity")
# security events (sign-ins, refused second steps and access): one JSON line each on the app's output, so
# `docker compose logs app` shows them. Never a password, code or token; a username only when it names an account
# (a name typed at sign-in that matches none might be a password typed into the wrong field).
audit = logging.getLogger("sanguity.security")
if not audit.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(message)s"))
    audit.addHandler(_h)
    audit.setLevel(logging.INFO)
    audit.propagate = False


def quiet_traceback(exc):
    """A traceback with where each exception happened (file, line, function, the line of code) and its type, but not
    its message, nor its cause's or context's: an importer's message quotes a file's name, and an unexpected
    KeyError or ValueError can quote a record's text (#203)."""
    chain, seen = [], set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        chain.append(exc)
        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    parts = []
    for e in reversed(chain):   # the first one raised first, as Python prints them
        frames = "".join(traceback.format_list(traceback.extract_tb(e.__traceback__)))
        parts.append(f"Traceback (most recent call last):\n{frames}{type(e).__module__}.{type(e).__qualname__}"
                     " (its message isn't logged: it may quote records)")
    return "\n\nWhile handling that, another exception:\n\n".join(parts)


class QuietTracebacks(logging.Filter):
    """Logs a record's exception with quiet_traceback instead of the full one (ASVS 7.1.2): on the "sanguity" loggers
    (failed imports, pulls and backups) and on uvicorn's error log (an error in a request)."""
    def filter(self, record):
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text, record.exc_info = quiet_traceback(record.exc_info[1]), None
        return True


if not log.handlers:   # in place of Python's last-resort handler: the same stderr and plain format, quieter
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(message)s"))
    _h.addFilter(QuietTracebacks())
    log.addHandler(_h)
logging.getLogger("uvicorn.error").addFilter(QuietTracebacks())
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
    atrest.keep_private()   # readable only by the app's own user, what it makes and what's there (umask 077, #935)
    with backup.app_running_lock():   # held while the app runs, so a restore can tell and refuse
        dbm.init()
        db = dbm.connect()
        try:
            atrest.check_key(db)   # stops here, saying why, if the key is missing or wrong
            atrest.load_pepper(db)
            if (gone := prof.sweep(db)):   # what a profile's deletion stopped part-way left (#932)
                print(f"Removed {gone} folder(s) of deleted profiles that a stop part-way left.", flush=True)
            sealed = atrest.seal_everything(db)
            records.backfill_key_hashes(db)   # source keys' hashes for rows from before schema version 13
            from . import labmap   # lab-name matches reviews made before schema version 41: their profile's own (#927)
            labmap.scope_old(db)
            if (given := sync.backfill_keys(db)):   # a lab event's rows' own records, from before #885 (schema version 40)
                print(f"Gave {given} lab event(s) a connected source saved before the rows' own records were kept "
                      "theirs, from what the source has sent.", flush=True)
            if sealed["files"] or sealed["fields"]:
                print(f"Encrypted {sealed['files']} files and {sealed['fields']} database fields that weren't yet.", flush=True)
            if (moved := prof.migrate_files(db)):
                print(f"Moved the records of {moved} profile(s) into the database.", flush=True)
            if (moved := settings.migrate_files(db, prof.profile_dir)):
                print(f"Moved the settings.json of {moved} profile(s) into the database; settings are changed on the "
                      "Settings page now, and each file was deleted.", flush=True)
            if (kept := watch_store.migrate(db)):
                print(f"Kept the imported watch data of {kept} profile(s) as an import that can't be undone.", flush=True)
            pid = prof.bootstrap(db)
            from . import cycle_tracking   # cycle tracking turned off, its deletion done: its setting goes (#935)
            cycle_tracking.forget_done(db)
            watch_keys.publish(db)   # the watch receiver's secrets, from the database (#46)
            if pid:
                print(f"Loaded the existing records into profile {pid} (\"My records\").", flush=True)
            if (made := edits.migrate_corrections(db)):
                print(f"Moved corrections.json's event corrections into {made} edit(s) on the first profile.", flush=True)
            elif dbm.meta_get(db, edits.MIGRATED) and edits.leftover_corrections():
                print("corrections.json still lists not_hospital_stays: those were moved into the app's edits once "
                      "and aren't read any more. Make event corrections in the app's event panel.", flush=True)
            if (moved := milestones.migrate(db)):
                print(f"Moved the milestones of {moved} profile(s) into their settings; milestones are changed on the "
                      "Settings page now, and corrections.json is no longer needed.", flush=True)
            prof.sync_build(db)
            if (gone := uploads.cleanup(db)):
                print(f"Removed {gone} upload(s) left unfinished for over a day.", flush=True)
            if (gone := export.strip_all(db)):   # what an uploaded export kept that its profile doesn't import (#918)
                print(f"Took {gone} file(s) out of uploaded exports: an activity log, or cycle records while cycle tracking "
                      "is off.", flush=True)
            if (gone := cycle_tracking.strip_old(db)):   # Apple Health exports an older version kept as they came (#555)
                print(f"Took {gone} record(s) out of uploaded Apple Health exports: sexual activity, and cycle records and "
                      "basal body temperature where cycle tracking is off.", flush=True)
            if (stopped := jobs.recover(db)):
                print(f"Marked {stopped} import(s) the last stop interrupted as failed; nothing of them was saved.", flush=True)
            from . import journal_photos   # photos sent for a journal entry never saved, a day on (#591)
            journal_photos.sweep_all(db)
            backup_jobs.recover(db)   # backups and checks a stop interrupted (#200)
            with dbm.transaction(db):   # share links and feeds an older version left to come back with their person (#590)
                accounts.end_lost_access(db)
            _setup_code(db)
        finally:
            db.close()
        jobs.start()
        sync.start()   # connected sources' scheduled pulls (#91)
        backup_jobs.start()   # Back up now, scheduled backups and checks (#200)
        try:
            yield
        finally:
            backup_jobs.stop()
            sync.stop()
            jobs.stop()


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


def _audit(event, request=None, user=None, warn=True, **fields):
    """Log a security event. user: an account row (or {"id", "username"}), or None."""
    line = {"at": dbm.now(), "event": event}
    if request is not None:
        line["ip"] = _client_ip(request)
    if user is not None:
        line["user"], line["uid"] = user["username"], user["id"]
    line.update(fields)
    audit.log(logging.WARNING if warn else logging.INFO, "security %s", json.dumps(line, separators=(",", ":")))
    if event == "login.fail":   # also kept, so admins see bursts (alerts.py)
        alerts.record(line.get("ip"), line.get("uid"), fields.get("step"))
    elif event == "reauth.fail" and fields.get("step") != "passkey":   # a signed-in session's wrong password or code (#931)
        alerts.record(line.get("ip"), line.get("uid"), alerts.CHECK + (fields.get("step") or "password"))


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
            _audit("csrf.refused", request, path=request.url.path, origin=(origin or "")[:200])
            return JSONResponse({"detail": "Request refused (cross-site check)."}, status_code=403)
    resp = await call_next(request)
    if request.url.path.startswith("/api/"):
        # a calendar feed (routes_calendar.py) may be kept by the calendar app itself, never by a shared cache
        if not resp.headers.get("Cache-Control", "").startswith("private"):
            resp.headers["Cache-Control"] = "no-store"
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        # an API answer opened directly is downloaded, never rendered (documents say inline themselves)
        resp.headers.setdefault("Content-Disposition", 'attachment; filename="api.json"')
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


def _lifetime(db, u):
    """How long, in seconds, a session of account u lasts from sign-in: SESSION_DAYS with a second factor, and
    SESSION_HOURS_WITHOUT_2FA (if shorter) without one."""
    days, hours = config.SESSION_DAYS * 86400, config.SESSION_HOURS_WITHOUT_2FA * 3600
    if hours and hours < days and not accounts.has_second_factor(db, u):
        return hours
    return days


def _session(request, db):
    token = request.cookies.get(config.COOKIE_HOST) or request.cookies.get(config.COOKIE)
    if not token or len(token) > 200:
        return None
    s = db.execute("""SELECT s.id AS sid, s.created AS s_created, s.last_seen, s.expires, s.verified_at, u.* FROM sessions s
                      JOIN users u ON u.id = s.user_id WHERE s.token_hash=? AND u.disabled=0""",
                   (sec.token_hash(token),)).fetchone()
    now = dbm.now()
    if not s:
        return None
    idle = config.SESSION_IDLE_MINUTES * 60
    # judged from sign-in as well as from the stored end, so a session from before the second factor went (or from
    # before this limit existed) ends on the shorter limit too
    if (s["expires"] <= now or now - s["s_created"] >= _lifetime(db, s)
            or (idle and now - s["last_seen"] > idle)):
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


def admin(request: Request, s=Depends(user), db=Depends(get_db)):
    """An admin with a second factor of their own, an authenticator app or a passkey (ASVS 4.3.1), unless they are
    the only account on the server. The command line (server.manage) needs none: it stays the way back in."""
    if not s["is_admin"]:
        _audit("access.denied", request, s, need="admin", path=request.url.path)
        raise HTTPException(403, "Only an admin can do that.")
    if accounts.admin_needs_second_factor(db, s):
        _audit("access.denied", request, s, need="admin second factor", path=request.url.path)
        raise HTTPException(403, "admin_two_factor_required")
    return s


def fresh(request, s, action):
    """Before the most powerful admin actions (ASVS 4.3.3): this session's person proved it was them within
    REAUTH_MINUTES, by signing in or with POST /api/me/reauth (password or code) or /api/me/reauth/passkey."""
    if (s["verified_at"] or 0) <= dbm.now() - config.REAUTH_MINUTES * 60:
        _audit("reauth.needed", request, s, warn=False, action=action)
        raise HTTPException(403, "reauth_required")


def role_of(db, uid, pid):
    r = db.execute("SELECT role FROM memberships WHERE user_id=? AND profile_id=?", (uid, pid)).fetchone()
    return r["role"] if r else None


def need(db, u, pid, at_least):
    """The caller's role on pid, or an error. A profile not shared with the caller is reported as not found,
    so its existence isn't revealed."""
    r = role_of(db, u["id"], pid)
    if not r or RANK[r] < RANK[at_least]:
        _audit("access.denied", None, u, profile=pid, need=at_least, role=r)
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


def _start_session(request, response, db, uid, how=None):
    """A new session for uid. how: the way they signed in, for the security log (None: a fresh session after a
    change, not a sign-in)."""
    token, now = sec.new_token(), dbm.now()
    life = _lifetime(db, db.execute("SELECT id, totp_secret FROM users WHERE id=?", (uid,)).fetchone())
    db.execute("DELETE FROM sessions WHERE expires<=?", (now,))
    # every caller has just checked the person (password, code or passkey), so the session starts verified
    db.execute("INSERT INTO sessions(token_hash, user_id, created, last_seen, expires, ip, agent, verified_at) VALUES(?,?,?,?,?,?,?,?)",
               (sec.token_hash(token), uid, now, now, now + life,
                _client_ip(request), (request.headers.get("user-agent") or "")[:300], now))
    secure = _secure(request)
    response.set_cookie(config.COOKIE_HOST if secure else config.COOKIE, token, max_age=life,
                        path="/", httponly=True, samesite="strict", secure=secure)
    if secure and request.cookies.get(config.COOKIE):   # one from before the prefix, or from plain HTTP
        response.delete_cookie(config.COOKIE, path="/", httponly=True, samesite="strict")
    if how:
        u = db.execute("SELECT id, username FROM users WHERE id=?", (uid,)).fetchone()
        _audit("login.ok", request, u, warn=False, method=how)


# ---------------------------------------------------------------- setup and sign-in

class Setup(BaseModel):
    code: str
    username: str
    password: str
    profile_name: Optional[str] = None


@app.get("/api/setup")
def setup_state(db=Depends(get_db)):
    """Whether first-run setup is due, how long a new password must be (setup counts toward it as it's typed, #290),
    and how long a sign-in lasts (for the note on the sign-in page). records: while setup is due, whether there are
    records here that the first account will own (claim_unowned; #498: setup says so only then, not on an empty install)."""
    needed = db.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None
    return {"needed": needed, "password_min": config.PASSWORD_MIN,
            "records": needed and db.execute("SELECT 1 FROM profiles WHERE id NOT IN (SELECT profile_id FROM memberships) LIMIT 1").fetchone() is not None,
            "session": {"days": config.SESSION_DAYS, "hours_without_2fa": config.SESSION_HOURS_WITHOUT_2FA,
                        "idle_minutes": config.SESSION_IDLE_MINUTES}}


class SetupRefused(Exception):
    """an account setup can't create: its message, and the field it's about ("username" or "password", #501)"""


@app.exception_handler(SetupRefused)
async def setup_refused(request: Request, exc: SetupRefused):
    e = exc.args[0]
    return JSONResponse({"detail": str(e), "field": e.field}, status_code=e.status)


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
        _audit("setup.fail", request)
        raise HTTPException(403, "That setup code is wrong. The current one is in the app's log (docker compose logs app).")
    with dbm.transaction(db):
        if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            raise HTTPException(409, "Sanguity is already set up. Sign in instead.")
        try:
            uid, _ = accounts.create(db, body.username, is_admin=True, password=body.password)
        except accounts.AccountError as e:   # naming the field it's about, which setup marks (#501)
            raise SetupRefused(e)
        owned = accounts.claim_unowned(db, uid)   # the records already on this server
        first = {"id": uid, "username": body.username.strip()}
        activity.log(db, "setup", first, ip=_client_ip(request))
        label = (body.profile_name or "").strip()[:80]
        if owned and (label or prof.DEFAULT_NAME == db.execute("SELECT name FROM profiles WHERE id=?", (owned[0],)).fetchone()["name"]):
            # the records already here are named as asked; left empty, after the username, as setup says (#492), unless
            # they had a name of their own (only the setup build's are "My records")
            db.execute("UPDATE profiles SET name=? WHERE id=?", (label or body.username.strip(), owned[0]))
        elif not owned:
            pid = prof.create_profile(db, label or body.username.strip(), owner_id=uid)
            activity.log(db, "create", first, pid, ip=_client_ip(request))
    sec.throttle.clear(*keys)
    _start_session(request, response, db, uid, "setup")
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
        _audit("login.throttled", request, u, step="password")
        raise HTTPException(429, f"Too many failed sign-ins. Try again in {w} seconds.", headers={"Retry-After": str(w)})
    pw = body.password[:config.PASSWORD_MAX]   # no stored password is longer; the same string is checked and rehashed
    ok = sec.verify_password(u["pw_hash"] if u else None, pw)
    if not ok or u["disabled"]:
        sec.throttle.fail(*keys)
        _audit("login.fail", request, u, step="password", reason="disabled" if ok else "password" if u else "no such account")
        raise HTTPException(401, "Wrong username or password.")
    if u["must_change_pw"] and accounts.temp_password_expired(u):
        _audit("login.fail", request, u, step="password", reason="one-time password expired")
        raise HTTPException(401, "This one-time password has expired. Ask an admin for a new one.")
    sec.throttle.clear(keys[0])   # this person on this device got in; the address and account counts age out
    if sec.needs_rehash(u["pw_hash"]):
        db.execute("UPDATE users SET pw_hash=? WHERE id=?", (sec.hash_password(pw), u["id"]))
    keys_n = passkeys.count(db, u["id"])
    if u["totp_secret"] or keys_n:   # the password was right; now the code or a passkey
        return {"two_factor": True, "ticket": _tickets.issue(u["id"]),
                "methods": {"code": bool(u["totp_secret"]), "passkey": keys_n > 0,
                            **({"codes_stopped": True} if accounts.codes_stopped(u) else {}),   # (#928: a recovery code still works)
                            "sites": passkeys.sites(db, u["id"])}}   # where they were made, for a page at 127.0.0.1 (#535)
    _remember_device(request, response, db, u["id"])
    _start_session(request, response, db, u["id"], "password")
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
    code = twofactor.looks_like_code(body.code)
    if code and accounts.codes_stopped(u):   # (#928: before the throttle, whose wait wouldn't help)
        _audit("login.fail", request, u, step="code", reason="codes stopped")
        raise HTTPException(401, accounts.CODES_STOPPED)
    if (w := sec.throttle.wait(*keys)):
        _audit("login.throttled", request, u, step="code")
        raise HTTPException(429, f"Too many wrong codes. Try again in {w} seconds.", headers={"Retry-After": str(w)})
    out = {"must_change_pw": bool(u["must_change_pw"])}
    if code:
        n = accounts.code_try(db, uid)   # counted before it's checked (#928)
        if n is None:
            raise HTTPException(401, accounts.CODES_STOPPED)
        secret = crypto.dec(u["totp_secret"])
        step = twofactor.verify(secret, body.code, u["totp_last_step"])
        if step is None:
            sec.throttle.fail(*keys)
            _tickets.failed(body.ticket)
            _audit("login.fail", request, u, step="code",
                   reason="code already used" if twofactor.verify(secret, body.code) is not None else "wrong code")
            if n >= twofactor.STOP_AFTER:
                _codes_stopped_now(request, db, u)
                raise HTTPException(401, "That code isn't right either. " + accounts.CODES_STOPPED)
            raise HTTPException(401, "That code isn't right. Check your authenticator app's time, or use a recovery code.")
        accounts.code_right(db, uid, step)
    else:
        rows = db.execute("SELECT code_hash FROM recovery_codes WHERE user_id=? AND used_at IS NULL", (uid,)).fetchall()
        match = next((r[0] for r in rows if twofactor.recovery_matches(r[0], body.code)), None)
        hit = match is not None and db.execute("UPDATE recovery_codes SET used_at=? WHERE user_id=? AND code_hash=? AND used_at IS NULL",
                                               (dbm.now(), uid, match)).rowcount
        if not hit:
            sec.throttle.fail(*keys)
            _tickets.failed(body.ticket)
            _audit("login.fail", request, u, step="recovery code")
            raise HTTPException(401, "That recovery code isn't right, or it was used already.")
        accounts.code_right(db, uid)   # the authenticator's codes work again (#928)
        accounts.sign_out_everywhere(db, uid)   # a recovery code may mean a lost phone: end every other sign-in
        out["recovery_codes_left"] = left = db.execute(
            "SELECT COUNT(*) FROM recovery_codes WHERE user_id=? AND used_at IS NULL", (uid,)).fetchone()[0]
        activity.log(db, "2fa.recovery", u, detail={"left": left}, ip=_client_ip(request))
    _tickets.used(body.ticket)
    sec.throttle.clear(keys[0])
    _remember_device(request, response, db, uid)
    _start_session(request, response, db, uid, "password + " + ("code" if code else "recovery code"))
    return out


def _codes_stopped_now(request, db, u):
    """The wrong code that made twofactor.STOP_AFTER (#928): in the security log and the install's activity log, where
    admins see it, as their notice does (alerts.codes_stopped) and the person's sign-in and Account page."""
    _audit("2fa.stopped", request, u, wrong=twofactor.STOP_AFTER)
    activity.log(db, "2fa.stopped", u, detail={"wrong": twofactor.STOP_AFTER}, ip=_client_ip(request))


def _code_checked(request, db, s, code, action):
    """A code from the authenticator app of signed-in account s, for the fresh check or new recovery codes (#928):
    counted, and refused once its codes have stopped. Returns its step, or None for a wrong one."""
    if accounts.codes_stopped(s):
        raise HTTPException(409 if action == "new recovery codes" else 403,
               f"Codes from your authenticator app are stopped for this account: {twofactor.STOP_AFTER} wrong codes "
               "were entered since its last right one. " + ("Use your password or a passkey instead." if action == "fresh check"
               else "Sign in once with a recovery code to use them again, or ask an admin to reset your two-factor sign-in."))
    n = accounts.code_try(db, s["id"])
    step = twofactor.verify(crypto.dec(s["totp_secret"]), code, s["totp_last_step"]) if n is not None else None
    if step is None:
        if n is not None and n >= twofactor.STOP_AFTER:
            _codes_stopped_now(request, db, s)
        return None
    accounts.code_right(db, s["id"], step)
    return step


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
        _audit("login.throttled", request, step="passkey")
        raise HTTPException(429, f"Too many failed sign-ins. Try again in {w} seconds.", headers={"Retry-After": str(w)})
    want = None
    if body.ticket:
        want = _tickets.get(body.ticket[:200])
        if not want:
            raise HTTPException(401, "That took too long. Enter your password again.")
    try:
        uid, name = passkeys.authenticate(db, body.state[:200], body.credential)
    except passkeys.PasskeyError as e:
        sec.throttle.fail(*keys)
        if body.ticket:
            _tickets.failed(body.ticket[:200])
        _audit("login.fail", request, step="passkey", reason=str(e)[:200])
        raise
    if want is not None and uid != want:   # the state was issued for this ticket's account, so this can't happen
        raise HTTPException(401, passkeys.STALE)
    u = db.execute("SELECT * FROM users WHERE id=? AND disabled=0", (uid,)).fetchone()
    if not u:
        sec.throttle.fail(*keys)
        _audit("login.fail", request, step="passkey", reason="account disabled", uid=uid)
        raise HTTPException(401, "That account can't sign in.")
    if body.ticket:
        _tickets.used(body.ticket[:200])
    _remember_device(request, response, db, uid)
    _start_session(request, response, db, uid, "password + passkey" if body.ticket else "passkey")
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
    for name in (config.COOKIE_HOST, config.COOKIE):
        if (token := request.cookies.get(name)):
            db.execute("DELETE FROM sessions WHERE token_hash=?", (sec.token_hash(token),))
    response.delete_cookie(config.COOKIE, path="/", httponly=True, samesite="strict", secure=_secure(request))
    if _secure(request):
        response.delete_cookie(config.COOKIE_HOST, path="/", httponly=True, samesite="strict", secure=True)
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
                     "needs_two_factor": needs_2fa, "admin_needs_two_factor": accounts.admin_needs_second_factor(db, s)},
            "two_factor_required": required, "reauth_minutes": config.REAUTH_MINUTES,
            "profiles": _profiles_for(db, s["id"]) if not (s["must_change_pw"] or needs_2fa) else [],
            "password_min": _password_min(db, s), "session_ends": _session_ends(db, s),
            "calendar_feeds": calendar.count_for(db, s["id"]),   # what changing the password or removing a passkey ends (#597)
            # the install's HTTPS address (PUBLIC_ORIGINS), for "install from this address" on a plain-HTTP page (#56)
            "https_address": next((o for o in sorted(config.PUBLIC_ORIGINS) if o.startswith("https://")), None),
            # backups late or failing (#200): the Admin menu item says so, for admins who may use it
            "backups_late": bool(s["is_admin"]) and not (s["must_change_pw"] or needs_2fa)
                            and not accounts.admin_needs_second_factor(db, s) and backup_jobs.late(db)}


def _session_ends(db, s):
    """When this sign-in ends at the latest (epoch seconds), if it stays in use: the offline copy of records a device
    may keep (#56, www/js/offline.js) is deleted at this time even if the device never reaches the server again."""
    ends = min(s["expires"], s["s_created"] + _lifetime(db, s))
    if config.SESSION_IDLE_MINUTES:
        ends = min(ends, dbm.now() + config.SESSION_IDLE_MINUTES * 60)
    return ends


def _password_min(db, s):
    return config.PASSWORD_MIN_2FA if accounts.has_second_factor(db, s) else config.PASSWORD_MIN


class PasswordChange(BaseModel):
    current: str
    new: str


@app.post("/api/me/password")
def change_password(body: PasswordChange, request: Request, s=Depends(any_user), db=Depends(get_db)):
    keys = sec.password_keys(s["username"], _client_ip(request))   # per account too (#931)
    if (w := sec.throttle.wait(*keys)):
        raise HTTPException(429, f"Too many wrong passwords. Try again in {w} seconds.")
    if not sec.verify_password(s["pw_hash"], body.current[:config.PASSWORD_MAX]):
        sec.throttle.fail(*keys)
        _audit("reauth.fail", request, s, action="change password")
        raise HTTPException(403, "Your current password is wrong.")
    if (p := sec.password_problem(body.new, s["username"], _password_min(db, s))):
        raise HTTPException(400, p)
    if sec.verify_password(s["pw_hash"], body.new):
        raise HTTPException(400, "Choose a password different from the current one.")
    pw = sec.hash_password(body.new)
    with dbm.transaction(db):
        db.execute("UPDATE users SET pw_hash=?, must_change_pw=0, temp_pw_expires=NULL, pw_short=? WHERE id=?",
                   (pw, int(len(body.new) < config.PASSWORD_MIN), s["id"]))
        n = db.execute("DELETE FROM sessions WHERE user_id=? AND id<>?", (s["id"], s["sid"])).rowcount
        # its calendar feeds end too (#597, the owner's decision), as signing out everywhere ends them (#590): a password
        # is changed when someone else may have had it, and so may a feed's address; the page said how many beforehand
        feeds = calendar.drop_for(db, s["id"])
        activity.log(db, "password", s, ip=_client_ip(request))
    return {"ok": True, "signed_out_elsewhere": n, "calendar_feeds": feeds}


class FreshCheck(BaseModel):
    secret: str   # the password, or a current code from the authenticator app


def _verified(db, s):
    now = dbm.now()
    db.execute("UPDATE sessions SET verified_at=? WHERE id=?", (now, s["sid"]))
    return {"ok": True, "until": now + config.REAUTH_MINUTES * 60}


@app.post("/api/me/reauth")
def reauth(body: FreshCheck, request: Request, s=Depends(user), db=Depends(get_db)):
    """The fresh check before a powerful admin action (see fresh): the password, or a code from the app."""
    if s["totp_secret"] and twofactor.looks_like_code(body.secret):   # no password is 6 digits (8 at least)
        keys = ("pair:2fa|" + str(s["id"]) + "|" + _client_ip(request), "user:2fa|" + str(s["id"]))
        if (w := sec.throttle.wait(*keys)) and not accounts.codes_stopped(s):
            raise HTTPException(429, f"Too many wrong codes. Try again in {w} seconds.", headers={"Retry-After": str(w)})
        if _code_checked(request, db, s, body.secret, "fresh check") is None:
            sec.throttle.fail(*keys)
            _audit("reauth.fail", request, s, action="fresh check", step="code")
            raise HTTPException(403, "That code isn't right, or it was used already. Wait for the next one.")
    else:
        keys = sec.password_keys(s["username"], _client_ip(request))   # per account too (#931)
        if (w := sec.throttle.wait(*keys)):
            raise HTTPException(429, f"Too many wrong passwords. Try again in {w} seconds.", headers={"Retry-After": str(w)})
        if not sec.verify_password(s["pw_hash"], body.secret[:config.PASSWORD_MAX]):
            sec.throttle.fail(*keys)
            _audit("reauth.fail", request, s, action="fresh check", step="password")
            raise HTTPException(403, "Your password is wrong." + (" Or enter a code from your authenticator app."
                                                                 if s["totp_secret"] else ""))
    return _verified(db, s)


@app.post("/api/me/reauth/passkey/options")
def reauth_passkey_options(request: Request, s=Depends(user), db=Depends(get_db)):
    state, options = passkeys.authentication_options(db, request.headers.get("origin"), s["id"], fresh=True)   # verifying the person (#930)
    return {"state": state, "options": options}


@app.post("/api/me/reauth/passkey")
def reauth_passkey(body: PasskeyFinish, request: Request, s=Depends(user), db=Depends(get_db)):
    """The fresh check with one of the account's own passkeys, which must verify the person (#930: a PIN or a
    fingerprint, as the password it stands in for would; a tap alone on a key left plugged in isn't enough)."""
    keys = ("ip:passkey|" + _client_ip(request),)
    if (w := sec.throttle.wait(*keys)):
        raise HTTPException(429, f"Too many failed tries. Try again in {w} seconds.", headers={"Retry-After": str(w)})
    try:
        uid, _ = passkeys.authenticate(db, body.state[:200], body.credential, fresh=True)
    except passkeys.PasskeyError as e:
        sec.throttle.fail(*keys)
        _audit("reauth.fail", request, s, action="fresh check", step="passkey", reason=str(e)[:200])
        raise
    if uid != s["id"]:   # the state was issued for this account's passkeys, so this can't happen
        raise HTTPException(401, passkeys.STALE)
    return _verified(db, s)


# ---------------------------------------------------------------- two-factor sign-in, for the signed-in account

class TwoFactorCode(BaseModel):
    code: str


class TwoFactorOff(BaseModel):
    password: str


class TwoFactorStart(BaseModel):
    password: str


class TwoFactorConfirm(BaseModel):
    code: str
    password: str   # again (the page keeps what was typed at the start): a secret left pending is no way in


def _password(request, s, password, action):
    """The person's password, checked before a change to how they sign in (#590): throttled as sign-in is, and a wrong
    one is in the security log as reauth.fail."""
    keys = sec.password_keys(s["username"], _client_ip(request))   # per account too (#931)
    if (w := sec.throttle.wait(*keys)):
        raise HTTPException(429, f"Too many wrong passwords. Try again in {w} seconds.")
    if not sec.verify_password(s["pw_hash"], password[:config.PASSWORD_MAX]):
        sec.throttle.fail(*keys)
        _audit("reauth.fail", request, s, action=action)
        raise HTTPException(403, "Your password is wrong.")


@app.get("/api/me/2fa")
def two_factor_state(s=Depends(settled), db=Depends(get_db)):
    left = db.execute("SELECT COUNT(*) FROM recovery_codes WHERE user_id=? AND used_at IS NULL", (s["id"],)).fetchone()[0]
    return {"on": bool(s["totp_secret"]), "recovery_codes_left": left if s["totp_secret"] else 0,
            "required": accounts.two_factor_required(db), "passkeys": passkeys.count(db, s["id"]),
            "codes_stopped": accounts.codes_stopped(s)}   # (#928)


@app.post("/api/me/2fa/start")
def two_factor_start(body: TwoFactorStart, request: Request, s=Depends(settled), db=Depends(get_db)):
    """A new secret to scan; it takes effect only once a code from it is confirmed. The page draws its QR code from the
    otpauth address (www/js/qr.js, #495). Needs the password, as confirming does (#590): a session left signed in
    could otherwise turn on an app of its own, and with it the recovery codes, and lock its person out."""
    if s["totp_secret"]:
        raise HTTPException(409, "Two-factor sign-in is already on.")
    _password(request, s, body.password, "turn on two-factor sign-in")
    secret = twofactor.new_secret()
    db.execute("UPDATE users SET totp_pending=? WHERE id=?", (crypto.enc(secret), s["id"]))
    link = twofactor.uri(secret, s["username"])
    return {"secret": secret, "uri": link}


@app.post("/api/me/2fa/confirm")
def two_factor_confirm(body: TwoFactorConfirm, request: Request, s=Depends(settled), db=Depends(get_db)):
    if s["totp_secret"]:
        raise HTTPException(409, "Two-factor sign-in is already on.")
    _password(request, s, body.password, "turn on two-factor sign-in")
    keys = ("pair:2fa|" + str(s["id"]) + "|" + _client_ip(request),)
    if (w := sec.throttle.wait(*keys)):
        raise HTTPException(429, f"Too many wrong codes. Try again in {w} seconds.")
    step = twofactor.verify(crypto.dec(s["totp_pending"]), body.code)
    if step is None:
        sec.throttle.fail(*keys)
        raise HTTPException(400, "That code doesn't match. Scan the QR code again, and check the time on your phone.")
    with dbm.transaction(db):
        db.execute("UPDATE users SET totp_secret=totp_pending, totp_pending=NULL, totp_last_step=?, totp_fails=0 WHERE id=?", (step, s["id"]))
        codes = accounts.new_recovery_codes(db, s["id"])
        activity.log(db, "2fa.on", s, ip=_client_ip(request))
    return {"recovery_codes": codes}


@app.post("/api/me/2fa/recovery-codes")
def two_factor_new_codes(body: TwoFactorCode, request: Request, s=Depends(settled), db=Depends(get_db)):
    """Replace the recovery codes; needs a current code from the app."""
    if not s["totp_secret"]:
        raise HTTPException(409, "Two-factor sign-in is off.")
    keys = ("pair:2fa|" + str(s["id"]) + "|" + _client_ip(request),)
    if (w := sec.throttle.wait(*keys)) and not accounts.codes_stopped(s):
        raise HTTPException(429, f"Too many wrong codes. Try again in {w} seconds.")
    if _code_checked(request, db, s, body.code, "new recovery codes") is None:   # counted (#928)
        sec.throttle.fail(*keys)
        raise HTTPException(400, "That code isn't right.")
    with dbm.transaction(db):
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
    _password(request, s, body.password, "turn off two-factor sign-in")
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
        here = passkeys.site(origin[:300], passkeys.sites(db, s["id"]))[0]
        problem = None
    except passkeys.PasskeyError as e:
        here, problem = None, str(e)
    return {"passkeys": passkeys.listing(db, s["id"], here), "site": here, "problem": problem,
            "user_handle": passkeys.user_handle(db, s["id"]),
            "required": accounts.two_factor_required(db), "authenticator_app": bool(s["totp_secret"])}


@app.post("/api/me/passkeys/options")
def passkey_add_options(body: PasskeyAdd, request: Request, s=Depends(settled), db=Depends(get_db)):
    """Adding a passkey adds a way in, so it needs the password."""
    _password(request, s, body.password, "add a passkey")
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


class PasskeyRemove(BaseModel):
    password: str


@app.delete("/api/me/passkeys/{pk}")
def passkey_remove(pk: int, body: PasskeyRemove, request: Request, s=Depends(settled), db=Depends(get_db)):
    """Remove one. The password always still signs in; but where the server requires two-factor sign-in, the last
    second factor can't go (that would leave the account unable to use anything until an admin stepped in). Needs the
    password and signs out everywhere else (#590), as turning off the authenticator app does: a session left signed in
    can't take away its person's way in, nor keep a device it signed in on. The account's calendar feeds end too (#597,
    the owner's decision): a passkey is removed when its device is lost, and that device's calendar app holds their
    addresses (turning off one's own authenticator app keeps them: accounts.turn_off_2fa)."""
    _password(request, s, body.password, "remove a passkey")
    with dbm.transaction(db):
        r = db.execute("SELECT name FROM passkeys WHERE id=? AND user_id=?", (pk, s["id"])).fetchone()
        if not r:
            raise HTTPException(404, "No such passkey.")
        if accounts.two_factor_required(db) and not s["totp_secret"] and passkeys.count(db, s["id"]) == 1:
            raise HTTPException(409, "This server requires two-factor sign-in, and this is your only second step. "
                                     "Add another passkey or turn on the authenticator app first.")
        db.execute("DELETE FROM passkeys WHERE id=?", (pk,))
        accounts.short_password_check(db, s["id"])
        n = db.execute("DELETE FROM sessions WHERE user_id=? AND id<>?", (s["id"], s["sid"])).rowcount
        feeds = calendar.drop_for(db, s["id"])   # (#597)
        activity.log(db, "passkey.remove", s, detail={"name": r["name"]}, ip=_client_ip(request))
    after = db.execute("SELECT must_change_pw FROM users WHERE id=?", (s["id"],)).fetchone()
    return {"ok": True, "left": passkeys.count(db, s["id"]), "must_change_pw": bool(after["must_change_pw"]), "signed_out_elsewhere": n,
            "calendar_feeds": feeds}


@app.get("/api/sessions")
def sessions(s=Depends(any_user), db=Depends(get_db)):
    now, life = dbm.now(), _lifetime(db, s)
    return [{**dict(r), "expires": min(r["expires"], r["created"] + life), "current": r["id"] == s["sid"]}
            for r in db.execute("SELECT id, created, last_seen, expires, ip, agent FROM sessions WHERE user_id=? AND "
                                "expires>? AND created>? ORDER BY last_seen DESC", (s["id"], now, now - life))]


@app.delete("/api/sessions/{sid}")
def revoke_session(sid: int, s=Depends(any_user), db=Depends(get_db)):
    if not db.execute("DELETE FROM sessions WHERE id=? AND user_id=?", (sid, s["id"])).rowcount:
        raise HTTPException(404, "No such session.")
    return {"ok": True}


@app.post("/api/sessions/revoke-others")
def revoke_others(s=Depends(any_user), db=Depends(get_db)):
    """Sign out everywhere else. The account's calendar feeds go too (#590): it may follow a lost phone, whose
    calendar app holds their addresses."""
    with dbm.transaction(db):
        n = db.execute("DELETE FROM sessions WHERE user_id=? AND id<>?", (s["id"], s["sid"])).rowcount
        feeds = calendar.drop_for(db, s["id"])
    return {"ok": True, "revoked": n, "calendar_feeds": feeds}


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
    watch_keys.publish(db)   # its watch secret stops working
    activity.log(db, "profile.delete", s, detail={"name": gone["name"], "members": gone["members"],
                                                  **({"lab_matches": gone["lab_matches"]} if gone["lab_matches"] else {})},
                 ip=_client_ip(request))
    return {"ok": True, "unlinked_build": gone["linked_build"], "lab_matches": gone["lab_matches"]}


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
    """Who has access, and as what: anyone with access may see it (the Sharing page's People, #168); only owners change it.
    Owners also see whose account an admin has disabled ("disabled": true or false): such an owner doesn't count as one
    (dbm.active_owners), so the page knows when changing your own role would be refused (#314); and who made the
    phone upload secret ("phone_secret": true, #929: it goes with their access, which Remove says), as its maker does
    on their own row."""
    role = need(db, s, pid, "viewer")
    rows = [dict(r) for r in db.execute(
        """SELECT u.id AS user_id, u.username, m.role, u.disabled FROM memberships m JOIN users u ON u.id = m.user_id
           WHERE m.profile_id=? ORDER BY u.username COLLATE NOCASE""", (pid,))]
    made = watch_keys.maker(db, pid)
    for r in rows:
        disabled = bool(r.pop("disabled"))
        if role == "owner":
            r["disabled"] = disabled
        if r["user_id"] == made and (role == "owner" or made == s["id"]):
            r["phone_secret"] = True
    return rows


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
        raise HTTPException(404, "No active account with that username. An admin can create it under Admin → Accounts.")
    with dbm.transaction(db):
        accounts.end_lost_access(db)   # what they had before losing access stays ended (#590)
        before, old = dbm.active_owners(db, pid), role_of(db, u["id"], pid)
        db.execute("""INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, ?)
                      ON CONFLICT(user_id, profile_id) DO UPDATE SET role=excluded.role""", (u["id"], pid, body.role))
        _keep_an_owner(db, pid, before)
        accounts.end_lost_access(db)   # an owner made an editor or viewer: their share links stop for good
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
        accounts.end_lost_access(db)   # their share links and calendar feed end for good (#590)
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

@app.get("/api/profiles/{pid}/data")
def profile_data(pid: int, request: Request, s=Depends(user), db=Depends(get_db)):
    """The records page's data: every record, with only some of their details (records.page, #217)."""
    need(db, s, pid, "viewer")
    prof.sync_build(db, pid)
    got = records.page(db, pid)
    if got is None:
        if (why := prof.build_error(pid)):
            raise HTTPException(500, f"The built records couldn't be loaded ({why}). Details are in the app's log.")
        # a journal and nothing imported: the journal (#472); or a watch's summary alone, as records (#477)
        if (got := records.journal_page(db, pid, watch_store.exists(db, pid))) is None:
            return Response(status_code=204)   # the profile exists but has no records yet
    activity.once(db, s, pid, "view", _client_ip(request))
    return Response(got[0], media_type="application/json", headers={"X-Records-Rev": got[1]})


@app.get("/api/profiles/{pid}/details")
def profile_details(pid: int, request: Request, ids: str = "", s=Depends(user), db=Depends(get_db)):
    """Records' details, as the page opens them (#217): ids= event ids, comma-separated (at most
    records.DETAILS_MAX). {"details": {event id: its details}} for those that have details. The same access as the
    records (viewers and up); a share link is no session, so it never reaches these. X-Records-Rev: the records'
    revision, as with .../data (the offline copy keeps details only with the records they belong to)."""
    need(db, s, pid, "viewer")
    try:
        wanted = [int(i) for i in ids.split(",") if i.strip()]
    except ValueError:
        raise HTTPException(400, "ids is a list of record ids, separated by commas.") from None
    if not wanted or len(wanted) > records.DETAILS_MAX:
        raise HTTPException(400, f"Ask for 1 to {records.DETAILS_MAX} records' details at a time.")
    prof.sync_build(db, pid)
    got = records.details(db, pid, wanted)
    if got is None:
        return Response(b'{"details":{}}', media_type="application/json")
    activity.once(db, s, pid, "view", _client_ip(request))
    return Response(got[0], media_type="application/json", headers={"X-Records-Rev": got[1]})


@app.get("/api/profiles/{pid}/export")
def profile_export(pid: int, request: Request, cycle: bool = False, format: str = "sanguity", include: str = "",
                   s=Depends(user), db=Depends(get_db)):
    """Everything in the profile as one zip, streamed (owners and editors; the activity log only in an owner's, #589). ?cycle=1: with the cycle records, when
    tracking is on (#183): ticked in each time, never by default. ?format=fhir (#205): a FHIR R4 bundle instead
    (fhir_export.py), of the parts named in include= (fhir_export.PARTS' keys, comma-separated; all when empty)."""
    role = need(db, s, pid, "editor")
    if format not in ("sanguity", "fhir"):
        raise HTTPException(400, "The format is sanguity or fhir.")
    prof.sync_build(db, pid)
    if format == "fhir":   # #205
        try:
            parts = fhir_export.parts_of(include)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        p = fhir_export.plan(db, pid, parts, cycle=cycle)
        activity.log(db, "export", s, pid, {"format": "fhir", "parts": [k for k in fhir_export.PART_KEYS if k in parts],
                                            **({"cycle": True} if p["cycle"] else {})}, ip=_client_ip(request))
        now = settings.local_now(db, s["id"])
        zone = settings.effective(db, None, s["id"])["dates.timeZone"][0] or None   # times without an offset are the person's
        return StreamingResponse(fhir_export.stream(p, s["username"], now, zone), media_type="application/zip",
                                 headers={"Content-Disposition": f'attachment; filename="{fhir_export.filename(p["name"], now)}"'})
    p = export.plan(db, pid, cycle=cycle, activity=role == "owner")   # the activity log is owners' only (#589)
    activity.log(db, "export", s, pid, {"cycle": True} if p["cycle"] else None, ip=_client_ip(request))
    now = settings.local_now(db, s["id"])   # dated in the exporting person's own time zone (#42)
    return StreamingResponse(export.stream(p, s["username"], now), media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{export.filename(p["name"], now)}"'})


@app.get("/api/profiles/{pid}/watch")
def profile_watch(pid: int, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "viewer")
    summary = watch_store.load(db, pid)   # the receiver's file, with any Apple Health import merged in
    if summary is None:
        return Response(status_code=204)
    units.annotate_watch(summary)   # weights that can be shown in kg or lb (#41)
    have = settings.stored(db, "profile", pid)   # heart rate zones on workouts, from About this person (#197)
    hrzones.annotate(summary, have.get("person.birthDate") or "", have.get("person.maxHr") or 0)
    from . import targets   # #234: time in the person's own CGM range, by day; the counts by mg/dL stay here
    targets.annotate(summary, have.get("person.targets") or {})
    return Response(json.dumps(summary, separators=(",", ":")).encode(), media_type="application/json")


@app.get("/api/profiles/{pid}/watch/ecg/{name}")
def profile_ecg_wave(pid: int, name: str, s=Depends(user), db=Depends(get_db)):
    """An ECG recording's samples (#182), apart from the watch summary so the records page stays small: read when its
    drawer opens. name: the 16 hex of its key (server/ecg.py)."""
    need(db, s, pid, "viewer")
    w = watch_store.wave(db, pid, name)
    if w is None:
        raise HTTPException(404, "That recording's samples aren't here.")
    return Response(json.dumps(w, separators=(",", ":"), ensure_ascii=False).encode(), media_type="application/json")


DOC_CSP = ("default-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self' data:; "
           "media-src 'self'; script-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'")
# (#221) an HTML record is sandboxed as the drawer's frame is, in a tab of its own too (Open in new tab), so a
# <meta http-equiv="refresh"> in it can't send that tab elsewhere; not a PDF or a picture, which a sandbox would stop
HTML_SANDBOX = "; sandbox allow-same-origin"
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
    resp.headers["Content-Security-Policy"] = DOC_CSP + (HTML_SANDBOX if ext in prof.HTML_DOCS else "")
    return resp


def _document(db, s, pid, doc):
    need(db, s, pid, "viewer")
    r = db.execute("SELECT path, ext FROM documents WHERE id=? AND profile_id=?", (doc[:64], pid)).fetchone()
    r = {"path": crypto.dec(r["path"]), "ext": r["ext"]} if r else None
    if not r or not os.path.isfile(r["path"]):
        raise HTTPException(404, "Document not found.")
    return r


def _opened(db, s, pid, doc, r, request):
    """Log a document opened, once a session, by its record's title (#342): an imported file's stored name is a random
    hex word. One no record names (a stale link) keeps its file's name."""
    def detail():
        title = records.document_title(db, pid, doc[:64])
        return {"title": title} if title else {"name": os.path.basename(r["path"])}
    activity.once(db, s, pid, "document", _client_ip(request), detail, key=doc[:64])


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
                  totp_secret IS NOT NULL AS authenticator_app, (SELECT COUNT(*) FROM passkeys k WHERE k.user_id = users.id) AS passkeys,
                  (SELECT COUNT(*) FROM sessions x WHERE x.user_id = users.id AND x.expires > ?) AS sessions,
                  totp_secret IS NOT NULL AND totp_fails >= ? AS codes_stopped
           FROM users ORDER BY username COLLATE NOCASE""", (dbm.now(), twofactor.STOP_AFTER))]


class UserIn(BaseModel):
    username: str
    is_admin: bool = False


@app.post("/api/users")
def new_user(body: UserIn, request: Request, s=Depends(admin), db=Depends(get_db)):
    """Create an account with a one-time password; its owner chooses their own at first sign-in."""
    if body.is_admin:
        fresh(request, s, "make an admin")
    try:
        uid, pw = accounts.create(db, body.username, is_admin=body.is_admin)
    except accounts.AccountError as e:
        raise HTTPException(e.status, str(e))
    activity.log(db, "account.create", s, detail={"username": body.username.strip(), **({"admin": True} if body.is_admin else {})},
                 ip=_client_ip(request))
    return {"id": uid, "username": body.username.strip(), "temporary_password": pw, "temporary_days": config.TEMP_PASSWORD_DAYS,
            "admin_needs_two_factor": accounts.admin_needs_second_factor(db, s)}   # no longer the only account


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
    confirm_name: str               # the account's username, typed by the admin (Admin's danger zone, #287)
    hand_to: Optional[str] = None   # username to become owner of the profiles this account alone owned
    force: bool = False             # remove anyway, leaving those profiles without an active owner


@app.delete("/api/users/{uid}")
def remove_user(uid: int, body: UserRemove, request: Request, s=Depends(admin), db=Depends(get_db)):
    """Delete an account for good (admins; not your own, not the last admin), confirmed by typing its username."""
    if uid == s["id"]:
        raise HTTPException(400, "You can't remove your own account here.")
    target = db.execute("SELECT username, is_admin FROM users WHERE id=?", (uid,)).fetchone()
    if not target:
        raise HTTPException(404, "No such account.")
    if body.confirm_name.strip() != target["username"]:   # as deleting a profile asks its name (#287)
        raise HTTPException(400, "Type the account's username exactly to confirm.")
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
    if sole and hand_to is not None:   # a way into those profiles' records, for the admin or whoever they name (#590)
        fresh(request, s, "hand profiles over")
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
    """Give a profile that has no active owner a new one. Only for such profiles: any other is its owners' to share.
    Whoever it's given to can see its records, the admin themself or an account they may have just made, so it needs
    the fresh check (#590)."""
    p = db.execute("SELECT name FROM profiles WHERE id=?", (pid,)).fetchone()
    if not p:
        raise HTTPException(404, "No such profile.")
    u = db.execute("SELECT id, username FROM users WHERE username=? AND disabled=0", (body.username.strip(),)).fetchone()
    if not u:
        raise HTTPException(404, "No active account with that username.")
    if dbm.active_owners(db, pid):   # (said before the check: nothing to confirm)
        raise HTTPException(409, "That profile has an active owner, who can share it.")
    fresh(request, s, "give a profile an owner")
    with dbm.transaction(db):
        if dbm.active_owners(db, pid):
            raise HTTPException(409, "That profile has an active owner, who can share it.")
        accounts.end_lost_access(db)   # the old owners' share links stay ended (#590)
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
    if body.reset_password or body.disable_2fa or (body.is_admin and not target["is_admin"]):   # a way into the account
        fresh(request, s, "reset a password" if body.reset_password else "turn off two-factor sign-in"
              if body.disable_2fa else "make an admin")
    sole = [name for _, name in accounts.sole_owned(db, uid)] if body.disabled and not target["disabled"] else []
    if sole and not body.force:   # nobody else could then share or rename these; the client asks, then resends with force
        raise HTTPException(409, {"sole_owner_of": sole, "message":
            f"This account is the only owner of {', '.join(sole)}. While it is disabled, nobody can share or rename "
            f"{'that profile' if len(sole) == 1 else 'those profiles'} until an admin gives "
            f"{'it' if len(sole) == 1 else 'them'} a new owner under Accounts. Disable it anyway?"})
    if sole:   # leaves them for an admin to give a new owner: the first step of a way into their records (#590)
        fresh(request, s, "disable a profile's only owner")
    out = {"ok": True}
    if body.is_admin is not None and body.is_admin != bool(target["is_admin"]):
        db.execute("UPDATE users SET is_admin=? WHERE id=?", (int(body.is_admin), uid))
        note("account.admin" if body.is_admin else "account.unadmin")
    if body.disabled is not None and body.disabled != bool(target["disabled"]):
        with dbm.transaction(db):
            accounts.end_lost_access(db)   # before enabling: what ended when it was disabled stays ended (#590)
            db.execute("UPDATE users SET disabled=? WHERE id=?", (int(body.disabled), uid))
            if body.disabled:
                accounts.sign_out_everywhere(db, uid)   # its calendar feeds too
                accounts.end_lost_access(db)   # its share links stop for good
        note("account.disable" if body.disabled else "account.enable")
    if body.reset_password:
        out["temporary_password"] = accounts.set_temp_password(db, uid)   # signs out everywhere: it may follow a lost device
        out["temporary_days"] = config.TEMP_PASSWORD_DAYS
        note("account.reset")
    if body.disable_2fa:
        accounts.turn_off_2fa(db, uid, passkeys_too=True)
        note("2fa.reset")
    return out


@app.get("/api/security/notice")
def security_notice(s=Depends(admin), db=Depends(get_db)):
    """A burst of failed sign-ins since the last dismissal, or null (ASVS 8.1.4); accounts whose authenticator codes
    stopped (#928); and connected sources that stopped syncing, per source (#102)."""
    return {"notice": alerts.notice(db), "codes_stopped": alerts.codes_stopped(db), "sync": alerts.sync_notice(db)}


@app.post("/api/security/notice/dismiss")
def security_notice_dismiss(request: Request, s=Depends(admin), db=Depends(get_db)):
    alerts.dismiss(db)
    activity.log(db, "security.notice_dismissed", s, ip=_client_ip(request))
    return {"ok": True}


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

# the pages' Content-Security-Policy, as nginx.conf sends it (a test keeps the two the same): scripts only from
# js/, never inline, so injected markup can't run anything
PAGE_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-src 'self'; "
            "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")

@app.get("/api/healthz")
def healthz():
    return Response(status_code=204)


app.include_router(__import__("server.routes_uploads", fromlist=["router"]).router)   # uploads (#19): uses the dependencies above
app.include_router(__import__("server.routes_jobs", fromlist=["router"]).router)      # importing them (#20)
app.include_router(__import__("server.routes_imports", fromlist=["router"]).router)   # import history and undo (#24)
app.include_router(__import__("server.routes_edits", fromlist=["router"]).router)     # corrections in the app (#33)
app.include_router(__import__("server.routes_same", fromlist=["router"]).router)      # same result from two sources (#96)
app.include_router(__import__("server.routes_manual", fromlist=["router"]).router)    # results typed in (#27)
app.include_router(__import__("server.routes_labmaps", fromlist=["router"]).router)   # lab names matched on import (#23)
app.include_router(__import__("server.routes_settings", fromlist=["router"]).router)  # settings (#35)
app.include_router(__import__("server.routes_watch", fromlist=["router"]).router)     # a watch receiver secret per profile (#46)
app.include_router(__import__("server.routes_shl", fromlist=["router"]).router)       # SMART Health Links and Cards (#94)
app.include_router(__import__("server.routes_directory", fromlist=["router"]).router)  # find your provider (#93)
app.include_router(__import__("server.routes_connect", fromlist=["router"]).router)   # connected sources: sign-in and sync (#32, #91)
app.include_router(__import__("server.routes_outside", fromlist=["router"]).router)   # outside connections (#59, #97)
app.include_router(__import__("server.routes_calendar", fromlist=["router"]).router)  # calendar feeds (#51)
app.include_router(__import__("server.routes_journal", fromlist=["router"]).router)   # the journal: notes, symptoms, side effects (#49)
app.include_router(__import__("server.routes_summary", fromlist=["router"]).router)   # the visit summary PDF (#48)
app.include_router(__import__("server.routes_share_links", fromlist=["router"]).router)  # share links (#47)
app.include_router(__import__("server.routes_costs", fromlist=["router"]).router)     # out-of-pocket costs: HSA/FSA, matched to claims (#55)
app.include_router(__import__("server.routes_backups", fromlist=["router"]).router)   # backups on the Admin page (#200)
app.include_router(__import__("server.routes_person", fromlist=["router"]).router)    # about this person: birth date, sex, height (#194)
app.include_router(__import__("server.routes_cycle", fromlist=["router"]).router)     # menstrual cycle tracking, opt-in (#183)
app.include_router(__import__("server.routes_meds", fromlist=["router"]).router)      # medication reminders (#162) and log (#235)


if os.environ.get("SANGUITY_SERVE_STATIC") == "1":   # in Docker, nginx serves www/ and only proxies /api/ here
    from fastapi.staticfiles import StaticFiles

    for sub in ("css", "js", "fonts", "icons"):
        app.mount(f"/{sub}", StaticFiles(directory=os.path.join(WWW, sub)), name=sub)

    # install as a phone app (#56): as nginx.conf sends them
    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest():
        return FileResponse(os.path.join(WWW, "manifest.webmanifest"), media_type="application/manifest+json",
                            headers={"Cache-Control": "no-store", "Content-Security-Policy": PAGE_CSP})

    @app.get("/sw.js", include_in_schema=False)
    def service_worker():
        return FileResponse(os.path.join(WWW, "sw.js"), media_type="text/javascript",
                            headers={"Cache-Control": "no-store", "Content-Security-Policy": PAGE_CSP})

    @app.get("/{page}")
    @app.get("/", include_in_schema=False)
    def page(page: str = "index.html"):
        if page not in ("index.html", "login.html", "setup.html", "account.html", "import.html", "settings.html", "providers.html", "summary.html", "share.html",
                        "admin.html", "sources.html", "sharing.html", "guide.html"):
            raise HTTPException(404)
        return FileResponse(os.path.join(WWW, page), media_type="text/html",
                            headers={"Cache-Control": "no-store", "Content-Security-Policy": PAGE_CSP})
