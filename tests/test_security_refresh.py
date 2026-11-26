#!/usr/bin/env python3
"""The security review's refresh (#203, docs/security-review.md): checks that keep its findings true.

- Every API route is listed here with what it needs: no session, a session (any_user, settled, user), an admin, and
  for a profile's routes the role need() checks. A new route, or a changed check, fails until this table (and the
  review's "Who can reach what") is updated.
- Who can reach what, for real: an owner, an editor, a viewer, an account the profile isn't shared with, an admin
  who isn't a member, and someone holding only a share link or a calendar feed's address. Every answer is no-store.
- Every text column in the database is either encrypted at rest (atrest.FIELDS) or listed here as plain on purpose,
  with why; so are the settings kept in plain text.
- A failed import, pull or request is logged with where it happened, never the exception's message, which can quote
  a file's name or a record (main.QuietTracebacks).
- nginx keeps no access log, whose request lines would name uploaded files, searches and sign-in codes.

Fictional data only.

  python3 tests/test_security_refresh.py
"""
import ast, inspect, io, json, logging, os, re, shutil, sys, tempfile, textwrap, time, unittest
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-security-test-")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "no-build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  SANGUITY_BACKUPS=os.path.join(TMP, "backups"), SANGUITY_WATCH_KEYS=os.path.join(TMP, "watch-keys", "keys.json"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"))
for d in ("records", "keys", "backups", "watch-keys"):
    os.makedirs(os.path.join(TMP, d))
sys.path.insert(0, ROOT)

from fastapi.routing import APIRoute   # noqa: E402
from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, atrest, crypto, db as dbm, jobs, main, profiles as prof, records, settings, uploads   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"olive": "harbour lantern pebble crow", "eddie": "seventeen quiet orange kites", "vera": "maple tunnel brisk saddle",
      "stan": "willow kettle amber stone", "ada": "a stranger to every record"}
D = lambda n: (date.today() + timedelta(days=n)).isoformat()
DATA = {"built": "2026-11-01T00:00:00+00:00", "source": "fictional",
        "events": [{"id": 1, "t": D(-30) + "T09:00:00", "k": "visit", "title": "Annual physical", "sub": "Dr. Fictional Example"}],
        "labs": [{"name": "Hemoglobin", "unit": "g/dL", "panel": "CBC", "n": 1, "txt": [], "pts": [[D(-30) + "T08:00:00", 13.1, 12, 16, ""]]}],
        "upcoming": [{"t": D(12) + "T10:30:00", "title": "Follow-up visit", "who": "Dr. Fictional Example", "where": "Riverside Clinic"}]}

# ---------------------------------------------------------------- every API route, and what it needs
# (method, path): (gate, role). gate: "none" (no session: the token in the request is the credential, or there is
# none), "any_user" (signed in, perhaps with a password change pending), "settled", "user" (nothing left to set
# up), "admin" (an admin with a second factor, main.admin). role: the least role main.need() checks on the profile.
NONE, ANY, SETTLED, USER, ADMIN = "none", "any_user", "settled", "user", "admin"
ROUTES = {
    ("GET", "/api/setup"): (NONE, None), ("POST", "/api/setup"): (NONE, None),
    ("POST", "/api/login"): (NONE, None), ("POST", "/api/login/code"): (NONE, None),
    ("POST", "/api/login/passkey/options"): (NONE, None), ("POST", "/api/login/passkey"): (NONE, None),
    ("POST", "/api/logout"): (NONE, None), ("GET", "/api/healthz"): (NONE, None),
    ("POST", "/api/share/view"): (NONE, None),                         # the share link's token, in the body
    ("GET", "/api/profiles/{pid}/calendar.ics"): (NONE, None),        # the feed's token, in the address
    ("GET", "/api/me"): (ANY, None), ("POST", "/api/me/password"): (ANY, None),
    ("GET", "/api/sessions"): (ANY, None), ("DELETE", "/api/sessions/{sid}"): (ANY, None),
    ("POST", "/api/sessions/revoke-others"): (ANY, None),
    ("GET", "/api/me/2fa"): (SETTLED, None), ("POST", "/api/me/2fa/start"): (SETTLED, None),
    ("POST", "/api/me/2fa/confirm"): (SETTLED, None), ("POST", "/api/me/2fa/recovery-codes"): (SETTLED, None),
    ("POST", "/api/me/2fa/off"): (SETTLED, None), ("GET", "/api/me/passkeys"): (SETTLED, None),
    ("POST", "/api/me/passkeys/options"): (SETTLED, None), ("POST", "/api/me/passkeys"): (SETTLED, None),
    ("PATCH", "/api/me/passkeys/{pk}"): (SETTLED, None), ("DELETE", "/api/me/passkeys/{pk}"): (SETTLED, None),
    ("POST", "/api/me/reauth"): (USER, None), ("POST", "/api/me/reauth/passkey/options"): (USER, None),
    ("POST", "/api/me/reauth/passkey"): (USER, None),
    # anyone signed in: nothing of anyone's records
    ("POST", "/api/profiles"): (USER, None), ("GET", "/api/uploads/limits"): (USER, None), ("GET", "/api/lab-names"): (USER, None),
    ("GET", "/api/lab-test-pages"): (USER, None),                     # "What is this test?" pages: the app's own table (#163)
    ("GET", "/api/settings/user"): (USER, None), ("PUT", "/api/settings/user/{key}"): (USER, None),
    ("GET", "/api/settings/user/sections"): (USER, None), ("POST", "/api/settings/user/reset"): (USER, None),
    ("GET", "/api/directory"): (USER, None), ("GET", "/api/directory/entry"): (USER, None),
    ("GET", "/api/outside"): (USER, None),                             # each person's own profiles' entries
    ("POST", "/api/connect/return"): (USER, "editor"),                 # on the profile the sign-in was started for
    # a profile's: viewers
    ("GET", "/api/profiles/{pid}/data"): (USER, "viewer"), ("GET", "/api/profiles/{pid}/watch"): (USER, "viewer"),
    ("GET", "/api/profiles/{pid}/details"): (USER, "viewer"),   # records' details, as the page opens them (#217)
    ("GET", "/api/profiles/{pid}/watch/ecg/{name}"): (USER, "viewer"),   # an ECG recording's samples (#182)
    ("GET", "/api/profiles/{pid}/documents/{doc}"): (USER, "viewer"),
    ("GET", "/api/profiles/{pid}/documents/{doc}/{part:path}"): (USER, "viewer"),
    ("GET", "/api/profiles/{pid}/members"): (USER, "viewer"), ("GET", "/api/profiles/{pid}/imports"): (USER, "viewer"),
    ("GET", "/api/profiles/{pid}/settings"): (USER, "viewer"), ("GET", "/api/profiles/{pid}/settings/sections"): (USER, "viewer"),
    ("PUT", "/api/profiles/{pid}/settings/{key}"): (USER, "viewer"),   # then the setting's own scope (settings.may_change)
    ("DELETE", "/api/profiles/{pid}/settings/{key}"): (USER, "viewer"),
    ("POST", "/api/profiles/{pid}/settings/reset"): (USER, "viewer"),
    ("PUT", "/api/profiles/{pid}/settings/{key}/name"): (USER, "viewer"),
    ("GET", "/api/profiles/{pid}/condition-presets"): (USER, "editor"),   # Start from a condition (#186): fills in settings
    ("GET", "/api/profiles/{pid}/connections"): (USER, "viewer"), ("GET", "/api/profiles/{pid}/connections/warnings"): (USER, "viewer"),
    ("GET", "/api/profiles/{pid}/calendar"): (USER, "viewer"), ("POST", "/api/profiles/{pid}/calendar"): (USER, "viewer"),
    ("POST", "/api/profiles/{pid}/calendar/rotate"): (USER, "viewer"), ("PATCH", "/api/profiles/{pid}/calendar"): (USER, "viewer"),
    ("DELETE", "/api/profiles/{pid}/calendar"): (USER, "viewer"),
    ("DELETE", "/api/profiles/{pid}/calendar/{feed_id}"): (USER, "viewer"),   # anyone's: owners only (in the route)
    ("GET", "/api/profiles/{pid}/journal"): (USER, "viewer"),
    ("GET", "/api/profiles/{pid}/journal/photos/{photo_id}"): (USER, "viewer"),   # a journal entry's photo (#161)

    ("GET", "/api/profiles/{pid}/medications/reminders"): (USER, "viewer"),   # medication reminders (#162)
    ("GET", "/api/profiles/{pid}/medications/doses"): (USER, "viewer"),       # the medication log (#235)
    ("GET", "/api/profiles/{pid}/person"): (USER, "viewer"),           # about this person (#194)
    ("GET", "/api/profiles/{pid}/summary"): (USER, "viewer"), ("PUT", "/api/profiles/{pid}/summary/questions"): (USER, "viewer"),
    ("POST", "/api/profiles/{pid}/summary/preview"): (USER, "viewer"), ("POST", "/api/profiles/{pid}/summary/pdf"): (USER, "viewer"),
    ("DELETE", "/api/profiles/{pid}/members/{uid}"): (USER, "owner"),  # anyone may remove themself (leave)
    # editors
    ("GET", "/api/profiles/{pid}/export"): (USER, "editor"),
    ("POST", "/api/profiles/{pid}/uploads"): (USER, "editor"), ("PUT", "/api/profiles/{pid}/uploads/{upload_id}/files"): (USER, "editor"),
    ("POST", "/api/profiles/{pid}/uploads/{upload_id}/finish"): (USER, "editor"),
    ("GET", "/api/profiles/{pid}/uploads/{upload_id}"): (USER, "editor"),
    ("DELETE", "/api/profiles/{pid}/uploads/{upload_id}"): (USER, "editor"),
    ("POST", "/api/profiles/{pid}/uploads/{upload_id}/import"): (USER, "editor"),
    ("GET", "/api/profiles/{pid}/jobs"): (USER, "editor"), ("GET", "/api/profiles/{pid}/jobs/{jid}"): (USER, "editor"),
    ("POST", "/api/profiles/{pid}/jobs/{jid}/confirm"): (USER, "editor"), ("POST", "/api/profiles/{pid}/jobs/{jid}/discard"): (USER, "editor"),
    ("POST", "/api/profiles/{pid}/imports/{iid}/undo"): (USER, "editor"), ("PUT", "/api/profiles/{pid}/imports/{iid}/name"): (USER, "editor"),
    ("GET", "/api/profiles/{pid}/edits"): (USER, "editor"), ("POST", "/api/profiles/{pid}/edits"): (USER, "editor"),
    ("DELETE", "/api/profiles/{pid}/edits/{edit_id}"): (USER, "editor"), ("GET", "/api/profiles/{pid}/edits/similar"): (USER, "editor"),
    ("POST", "/api/profiles/{pid}/same"): (USER, "editor"), ("DELETE", "/api/profiles/{pid}/same/{rid}"): (USER, "editor"),
    ("POST", "/api/profiles/{pid}/manual"): (USER, "editor"), ("PUT", "/api/profiles/{pid}/costs/mark"): (USER, "editor"),
    ("POST", "/api/profiles/{pid}/journal"): (USER, "editor"), ("PUT", "/api/profiles/{pid}/journal/{jid}"): (USER, "editor"),
    ("DELETE", "/api/profiles/{pid}/journal/{jid}"): (USER, "editor"),
    ("POST", "/api/profiles/{pid}/journal/group"): (USER, "editor"), ("PUT", "/api/profiles/{pid}/journal/photos"): (USER, "editor"),   # #161

    ("POST", "/api/profiles/{pid}/medications/reminders"): (USER, "editor"),   # medication reminders (#162)
    ("PUT", "/api/profiles/{pid}/medications/reminders/{rid}"): (USER, "editor"),
    ("PATCH", "/api/profiles/{pid}/medications/reminders/{rid}"): (USER, "editor"),
    ("DELETE", "/api/profiles/{pid}/medications/reminders/{rid}"): (USER, "editor"),
    ("POST", "/api/profiles/{pid}/medications/doses"): (USER, "editor"),      # the medication log (#235)
    ("PUT", "/api/profiles/{pid}/medications/doses/{did}"): (USER, "editor"),
    ("DELETE", "/api/profiles/{pid}/medications/doses/{did}"): (USER, "editor"),
    ("PUT", "/api/profiles/{pid}/person"): (USER, "editor"), ("POST", "/api/profiles/{pid}/person/heights"): (USER, "editor"),
    ("DELETE", "/api/profiles/{pid}/person/heights/{day}"): (USER, "editor"),
    ("GET", "/api/profiles/{pid}/watch/receiver"): (USER, "editor"),
    ("GET", "/api/profiles/{pid}/cycle"): (USER, "editor"),           # cycle tracking, opt-in (#183): not viewers
    ("POST", "/api/profiles/{pid}/watch/receiver/rotate"): (USER, "editor"),
    ("POST", "/api/profiles/{pid}/shl/inspect"): (USER, "editor"), ("POST", "/api/profiles/{pid}/shl/import"): (USER, "editor"),
    ("GET", "/api/profiles/{pid}/connect/start"): (USER, "editor"), ("POST", "/api/profiles/{pid}/connect/start"): (USER, "editor"),
    ("POST", "/api/profiles/{pid}/connections/{cid}/sync"): (USER, "editor"),
    ("POST", "/api/profiles/{pid}/connections/{cid}/reconnect"): (USER, "editor"),
    ("PATCH", "/api/profiles/{pid}/connections/{cid}"): (USER, "editor"),
    ("POST", "/api/profiles/{pid}/connections/{cid}/remove"): (USER, "editor"),
    ("POST", "/api/profiles/{pid}/connections/{cid}/attachments/{sid}/fetch"): (USER, "editor"),
    ("POST", "/api/profiles/{pid}/connections/{cid}/disconnect"): (USER, "editor"),
    # owners
    ("DELETE", "/api/profiles/{pid}"): (USER, "owner"), ("PATCH", "/api/profiles/{pid}"): (USER, "owner"),
    ("PUT", "/api/profiles/{pid}/members"): (USER, "owner"), ("GET", "/api/profiles/{pid}/activity"): (USER, "owner"),
    ("GET", "/api/profiles/{pid}/links"): (USER, "owner"), ("POST", "/api/profiles/{pid}/links"): (USER, "owner"),
    ("DELETE", "/api/profiles/{pid}/links/{lid}"): (USER, "owner"),
    ("PUT", "/api/profiles/{pid}/cycle"): (USER, "owner"), ("POST", "/api/profiles/{pid}/cycle/delete"): (USER, "owner"),   # (#183)
    # admins: accounts and the install, never a profile's records
    ("GET", "/api/users"): (ADMIN, None), ("POST", "/api/users"): (ADMIN, None), ("PATCH", "/api/users/{uid}"): (ADMIN, None),
    ("DELETE", "/api/users/{uid}"): (ADMIN, None), ("GET", "/api/activity"): (ADMIN, None),
    ("GET", "/api/ownerless"): (ADMIN, None), ("POST", "/api/ownerless/{pid}/owner"): (ADMIN, None),
    ("GET", "/api/security/notice"): (ADMIN, None), ("POST", "/api/security/notice/dismiss"): (ADMIN, None),
    ("GET", "/api/settings"): (ADMIN, None), ("PATCH", "/api/settings"): (ADMIN, None),
    ("GET", "/api/settings/install"): (ADMIN, None), ("PUT", "/api/settings/install/{key}"): (ADMIN, None),
    ("POST", "/api/settings/install/reset"): (ADMIN, None),
    ("GET", "/api/settings/install/backups-logging"): (ADMIN, None),   # General → Backups and logging (#1002)
    ("GET", "/api/labmaps"): (ADMIN, None), ("PATCH", "/api/labmaps/{mid}"): (ADMIN, None), ("DELETE", "/api/labmaps/{mid}"): (ADMIN, None),
    ("GET", "/api/connect/sources"): (ADMIN, None), ("POST", "/api/connect/sources"): (ADMIN, None),
    ("PATCH", "/api/connect/sources/{cid}"): (ADMIN, None), ("DELETE", "/api/connect/sources/{cid}"): (ADMIN, None),
    ("POST", "/api/connect/sources/{cid}/test"): (ADMIN, None),
    ("GET", "/api/backups"): (ADMIN, None), ("POST", "/api/backups"): (ADMIN, None), ("POST", "/api/backups/check"): (ADMIN, None),
    ("PUT", "/api/backups/schedule"): (ADMIN, None), ("DELETE", "/api/backups/replaced/{name}"): (ADMIN, None),   # (#595)
}
RANK = {"viewer": 1, "editor": 2, "owner": 3}


def api_routes():
    """Every API route of the app, routers included in it too."""
    def walk(rs):
        for r in rs:
            if hasattr(r, "original_router"):   # FastAPI keeps an included router as one entry
                yield from walk(r.original_router.routes)
            else:
                yield r
    return [r for r in walk(main.app.routes) if isinstance(r, APIRoute) and r.path.startswith("/api/")]


def gate_of(route):
    names, todo = set(), list(route.dependant.dependencies)
    while todo:
        d = todo.pop()
        names.add(getattr(d.call, "__name__", ""))
        todo.extend(d.dependencies)
    for g in (ADMIN, USER, SETTLED, ANY):
        if g in names:
            return g
    return NONE


def roles_checked(fn):
    """The roles fn's need() calls name (also need passed to run_in_threadpool), and "viewer" for main._document."""
    out = set()
    for c in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(fn)))):
        if not isinstance(c, ast.Call):
            continue
        name = getattr(c.func, "id", None) or getattr(c.func, "attr", None)
        args = [a for a in c.args if isinstance(a, ast.Constant) and a.value in RANK]
        if name == "need" or (name == "run_in_threadpool" and c.args and getattr(c.args[0], "id", "") == "need"):
            out.update(a.value for a in args)
        if name == "_document":
            out.update(roles_checked(main._document))
    return out


class Routes(unittest.TestCase):
    def test_every_route_is_listed_with_what_it_needs(self):
        seen = {}
        for r in api_routes():
            for m in r.methods - {"HEAD"} or {"HEAD"}:
                roles = roles_checked(r.endpoint)
                seen[(m, r.path)] = (gate_of(r), min(roles, key=RANK.get) if roles else None)
        self.assertEqual(set(seen) - set(ROUTES), set(), "new routes: add them to ROUTES and the review's access table")
        self.assertEqual(set(ROUTES) - set(seen), set(), "routes listed here that no longer exist")
        for k, want in ROUTES.items():
            self.assertEqual(seen[k], want, k)

    def test_every_profile_route_checks_the_role(self):
        for (m, path), (gate, role) in ROUTES.items():
            if "{pid}" in path and path != "/api/profiles/{pid}/calendar.ics" and not path.startswith("/api/ownerless"):
                self.assertIsNotNone(role, (m, path))

    def test_need_hides_a_profile_not_shared(self):
        src = inspect.getsource(main.need)
        self.assertIn('raise HTTPException(404, "No such profile.")', src)


# ---------------------------------------------------------------- who can reach what, for real

def client():
    return TestClient(main.app, base_url="http://testserver")


CTX = None


def setUpModule():   # the app's start-up once (database, key, pepper), for every test here
    global CTX
    CTX = client().__enter__()


def tearDownModule():
    CTX.__exit__(None, None, None)


class Reach(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db = dbm.connect()
        cls.uid = {n: accounts.create(db, n, password=pw, is_admin=(n == "ada"))[0] for n, pw in PW.items()}
        cls.pid = prof.create_profile(db, "Olive Example (fictional)", owner_id=cls.uid["olive"])
        for who, role in (("eddie", "editor"), ("vera", "viewer")):
            db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, ?)", (cls.uid[who], cls.pid, role))
        with dbm.transaction(db):
            records.store(db, cls.pid, DATA, "dataset")
        db.close()
        cls.c = {}
        for n in PW:
            c = client()
            r = c.post("/api/login", json={"username": n, "password": PW[n]}, headers=H)
            assert r.status_code == 200, r.text
            cls.c[n] = c
        db = dbm.connect()   # the admin's second factor, after signing in (main.admin needs one with several accounts)
        db.execute("UPDATE users SET totp_secret=? WHERE id=?", (crypto.enc("JBSWY3DPEHPK3PXP"), cls.uid["ada"]))
        db.close()

    def status(self, who, path, method="GET", **kw):
        r = (self.c[who] if who else client()).request(method, path, headers=H, **kw)
        return r

    def test_the_access_table(self):
        p = f"/api/profiles/{self.pid}"
        ok = (200, 204)
        V = {"olive": ok, "eddie": ok, "vera": ok, "stan": (404,), "ada": (404,), None: (401,)}
        E = {**V, "vera": (403,)}
        O = {**E, "eddie": (403,)}
        A = {"olive": (403,), "eddie": (403,), "vera": (403,), "stan": (403,), "ada": ok, None: (401,)}
        U = {"olive": ok, "eddie": ok, "vera": ok, "stan": ok, "ada": ok, None: (401,)}
        table = [
            (V, [p + "/data", p + "/details?ids=1", p + "/watch", p + "/members", p + "/imports", p + "/settings", p + "/settings/sections",
                p + "/connections", p + "/connections/warnings", p + "/calendar", p + "/journal", p + "/summary", p + "/person"]),
            (E, [p + "/export", p + "/jobs", p + "/edits", p + "/watch/receiver", p + "/cycle", p + "/condition-presets"]),
            (O, [p + "/activity", p + "/links"]),
            (A, ["/api/users", "/api/activity", "/api/ownerless", "/api/security/notice", "/api/settings", "/api/settings/install",
                "/api/labmaps", "/api/connect/sources", "/api/backups"]),
            (U, ["/api/me", "/api/outside", "/api/directory", "/api/lab-names", "/api/lab-test-pages", "/api/uploads/limits", "/api/settings/user"]),
        ]
        for rule, paths in table:
            for path in paths:
                for who, want in rule.items():
                    r = self.status(who, path)
                    self.assertIn(r.status_code, want, f"{who or 'no session'} {path}: {r.status_code} {r.text[:200]}")
                    if r.status_code in ok:   # never kept by a browser or proxy (ASVS 8.1.1)
                        self.assertEqual(r.headers.get("cache-control"), "no-store", path)
        # a viewer sees no other profile's outside contacts, and no admin's view of the install
        j = self.status("vera", "/api/outside").json()
        self.assertFalse(j["admin"])
        self.assertEqual(j["sources"], [])

    def test_settings_install_is_the_admins_alone(self):
        """Settings → Install (#991): the Admin page's groups, now on the Settings page every account opens, so their
        scripts are there for everyone and only the server keeps the install's to its admins. Every route they ask for is
        an admin's, but the person's own: their sign-in (the fresh check), and what any account may read (Find your
        provider's directory, the lab names, outside connections, each its own share for a non-admin). And each one
        that reads refuses an owner, an editor, a viewer and an account with no profile, for real."""
        with open(os.path.join(ROOT, "www", "settings.html"), encoding="utf-8") as f:
            scripts = re.findall(r'<script src="js/([\w-]+\.js)"></script>', f.read())
        install = ["admin.js", "admin-sources.js", "admin-backups.js", "outside.js", "activity.js",
                   "gen-backups.js"]   # General's sections (#998)
        self.assertEqual([s for s in install if s not in scripts], [], "Install's scripts on the Settings page")
        heads = set()
        for s in install:
            with open(os.path.join(ROOT, "www", "js", s), encoding="utf-8") as f:
                js = f.read()
            heads |= set(re.findall(r'\b(?:call|api)\(\s*["`]([a-z][\w/-]*)', js)) | set(re.findall(r'\bactivityPanel\(\s*"([\w/-]+)"', js))
        own = ("/api/me", "/api/outside", "/api/directory", "/api/lab-names")
        reads = []
        for h in sorted(heads):
            path = "/api/" + h.rstrip("/")
            routes = [k for k in ROUTES if k[1] == path or (h.endswith("/") and k[1].startswith(path + "/"))]
            self.assertTrue(routes, f"{h}: no such route")
            for k in routes:
                if any(k[1] == o or k[1].startswith(o + "/") for o in own):
                    continue
                self.assertEqual(ROUTES[k][0], ADMIN, f"Install asks {h} for {k}, which isn't only an admin's")
                if k[0] == "GET" and "{" not in k[1]:
                    reads.append(k[1])
        self.assertGreaterEqual(len(reads), 8, reads)
        for path in reads:
            for who in ("olive", "eddie", "vera", "stan"):
                self.assertEqual(self.status(who, path).status_code, 403, f"{who} {path}")
            self.assertIn(self.status("ada", path).status_code, (200, 204), path)

    def test_a_share_link_or_feed_opens_only_itself(self):
        r = self.status("olive", f"/api/profiles/{self.pid}/links", "POST", json={"sections": ["visits"]})
        self.assertEqual(r.status_code, 200, r.text)
        token = r.json()["path"].split("#", 1)[1]
        r = self.status("vera", f"/api/profiles/{self.pid}/calendar", "POST", json={})
        self.assertEqual(r.status_code, 200, r.text)
        feed = r.json()["path"].split("token=", 1)[1]
        holder = client()
        r = holder.post("/api/share/view", json={"token": token}, headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(r.json()) - {"profile", "expires", "from", "to", "sections", "cut"}, {"visits"})   # cut: the long sections cut (#498)
        self.assertNotIn("set-cookie", r.headers)
        for path in (f"/api/profiles/{self.pid}/data", f"/api/profiles/{self.pid}/details?ids=1", f"/api/profiles/{self.pid}/journal", "/api/me"):
            self.assertEqual(holder.get(path).status_code, 401)   # the link is no session
        self.assertEqual(holder.get(f"/api/profiles/{self.pid}/calendar.ics", params={"token": token}).status_code, 404)
        self.assertEqual(holder.post("/api/share/view", json={"token": feed}, headers=H).status_code, 404)
        r = holder.get(f"/api/profiles/{self.pid}/calendar.ics", params={"token": feed})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["cache-control"].startswith("private"))
        self.assertEqual(holder.get(f"/api/profiles/{self.pid + 1}/calendar.ics", params={"token": feed}).status_code, 404)
        self.status("vera", f"/api/profiles/{self.pid}/calendar", "DELETE")


# ---------------------------------------------------------------- encryption at rest: every text column

# columns kept in plain text on purpose: ids, hashes, states, names of accounts and profiles (crypto.py), install
# configuration, and the plain metadata of a connected source (accepted: docs/security-review.md, V6)
PLAIN = {
    "activity": {"actor", "action", "ip", "item"},           # item: a document's random id (activity.once)
    "backup_runs": {"kind", "origin", "state", "file", "stage", "message", "result", "created_name"},   # no health data
    "calendar_feeds": {"token_hash", "details", "tz"},
    "connect_pending": {"state_hash", "redirect_uri", "scopes", "token_url", "kinds", "since"},
    "connections": {"source", "issuer", "fhir_base", "credential", "scopes", "state", "cursor", "pending_cursor",
                    "error_kind", "kinds", "since", "attach"},   # source, issuer: the admin's vendor setting (accepted)
    "cost_marks": {"target"}, "devices": {"token_hash"}, "documents": {"id", "ext"},
    "edits": {"target", "action", "other"}, "events": {"key_hash"}, "import_undos": {"by_name"},
    "imports": {"origin", "format"}, "jobs": {"state", "stage", "importer", "keep", "same"},
    "journal": {"created_name", "changed_name"}, "journal_photos": {"file", "type"}, "med_reminders": {"created_name", "changed_name"},
    "med_doses": {"created_name", "changed_name"},   # #161: a random file name, jpeg/png/webp
    "labmaps": {"lookup", "importer", "created_name", "changed_name"},
    "memberships": {"role"}, "meta": {"k", "v"}, "outside_contacts": {"purpose"},
    "passkeys": {"credential_id", "public_key", "rp_id", "name", "transports"},
    "profiles": {"name", "build_mtime", "watch_file"}, "recovery_codes": {"code_hash"}, "same_results": {"pair"},
    "series": {"key_hash"}, "sessions": {"token_hash", "ip", "agent"}, "settings": {"key", "value"},
    "share_links": {"created_name", "token_hash", "revoked_name"}, "signin_failures": {"ip", "step"},
    "smart_clients": {"name", "vendor", "issuer", "client_id", "auth", "scopes", "discovery"},
    "sync_removed": {"what"}, "sync_skipped": {"ref_hash"}, "upload_files": {"name", "file"}, "uploads": {"state"},
    "users": {"username", "pw_hash", "webauthn_handle"},
}
# settings kept in settings.value, not sealed: how things look and the install's sync rules, never a record
PLAIN_SETTINGS = {"sync.attachAuto", "sync.attachMaxMB", "sync.attachTypes", "sync.attachBudgetMB", "sync.staleDays", "tabs",
                  "activity.days",   # (#1002) a variable's, plain as every one is (#999)
                  "theme", "guide", "dates.timeZone", "dates.format", "units"}


class AtRest(unittest.TestCase):
    def test_every_text_column_is_encrypted_or_plain_on_purpose(self):
        db = dbm.connect()
        try:
            sealed = {(t, c) for t, _, c in atrest.FIELDS}
            for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
                for r in db.execute(f"PRAGMA table_info({t})"):
                    if "INT" in (r["type"] or "").upper():
                        continue   # ids, times, counts and flags
                    col = r["name"]
                    self.assertTrue((t, col) in sealed or col in PLAIN.get(t, ()),
                                    f"{t}.{col}: encrypt it (atrest.FIELDS) or list it in PLAIN with why")
                    self.assertFalse((t, col) in sealed and col in PLAIN.get(t, ()), f"{t}.{col} is in both")
        finally:
            db.close()

    def test_plain_settings_are_the_reviewed_ones(self):
        self.assertEqual({k for k, d in settings.SETTINGS.items() if not d.private}, PLAIN_SETTINGS)


# ---------------------------------------------------------------- what the app's log says about a failure

# what an exception's message might quote (not on a line of code, which a traceback shows)
RECORD_TEXT, FILE_PROBLEM = "FICTIONAL-RECORD-TEXT Hemoglobin 13.1", "Fictional-Oncology-Visit.xml is not well-formed XML"


class QuietLogs(unittest.TestCase):
    def capture(self, logger):
        """What the "sanguity" handler (or, for uvicorn's logger, a handler like it) writes."""
        out = io.StringIO()
        h = logging.StreamHandler(out)
        h.setFormatter(logging.Formatter("%(message)s"))
        if logger.startswith("sanguity"):
            h.addFilter(main.QuietTracebacks())   # as main's own handler has
        lg = logging.getLogger(logger)
        lg.addHandler(h)
        was, lg.propagate = lg.propagate, False   # only here, not on the test's own output too
        self.addCleanup(setattr, lg, "propagate", was)
        self.addCleanup(lg.removeHandler, h)
        return out

    def test_a_failure_is_logged_without_its_message(self):
        for name in ("sanguity.jobs", "sanguity.sync", "uvicorn.error"):
            out = self.capture(name)
            try:
                try:
                    {}[RECORD_TEXT]
                except KeyError as e:
                    raise ValueError(FILE_PROBLEM) from e
            except ValueError:
                logging.getLogger(name).exception("import job %s failed", 7)
            text = out.getvalue()
            self.assertIn("import job 7 failed", text, name)
            self.assertIn("builtins.ValueError", text, name)
            self.assertIn("builtins.KeyError", text, name)
            self.assertIn("test_security_refresh.py", text, name)   # where it happened
            self.assertNotIn("FICTIONAL-RECORD-TEXT", text, name)
            self.assertNotIn("Oncology", text, name)

    def test_main_installs_the_filter(self):
        self.assertTrue(any(isinstance(f, main.QuietTracebacks) for h in main.log.handlers for f in h.filters))
        self.assertTrue(any(isinstance(f, main.QuietTracebacks) for f in logging.getLogger("uvicorn.error").filters))

    def test_a_failed_import_logs_no_file_name(self):
        out = io.StringIO()
        h = main.log.handlers[0]
        old = h.setStream(out)
        self.addCleanup(h.setStream, old)
        db = dbm.connect()
        try:
            uid = accounts.create(db, "ivy", password="lantern harbour crow pebble")[0]
            pid = prof.create_profile(db, "Ivy Example (fictional)", owner_id=uid)
            up = uploads.create(db, pid, uid)
            name = "Fictional-PRIVATE-Clinic-Summary.xml"
            uploads.add_file(db, pid, up, name, [b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><ClinicalDocument/>'])
            uploads.finish(db, pid, up)
            jid = jobs.submit(db, pid, up, uid, "ccda")   # run by the app's own import worker
            for _ in range(200):
                j = db.execute("SELECT state, message FROM jobs WHERE id=?", (jid,)).fetchone()
                if j["state"] in ("failed", "preview", "done"):
                    break
                time.sleep(0.05)
            self.assertEqual(j["state"], "failed")
            self.assertIn("PRIVATE", crypto.dec(j["message"]))   # the person is told, encrypted at rest
        finally:
            db.close()
        text = out.getvalue()
        self.assertIn(f"import job {jid}", text)
        self.assertNotIn("PRIVATE", text)


# ---------------------------------------------------------------- nginx keeps no access log

class Nginx(unittest.TestCase):
    def test_no_access_log(self):
        with open(os.path.join(ROOT, "nginx.conf"), encoding="utf-8") as f:
            conf = f.read()
        lines = [l.split("#", 1)[0].strip() for l in conf.splitlines()]
        server = lines[next(i for i, l in enumerate(lines) if l.startswith("server {")):]
        first_location = next(i for i, l in enumerate(server) if l.startswith("location"))
        self.assertIn("access_log off;", server[:first_location])
        for l in lines:   # nowhere turned back on
            if "access_log" in l:
                self.assertRegex(l, r"\baccess_log off;")
        with open(os.path.join(ROOT, "docker", "nginx-main.conf"), encoding="utf-8") as f:
            self.assertIn("access_log off;", f.read())   # the released image's main configuration


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(TMP, True)
