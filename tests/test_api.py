#!/usr/bin/env python3
"""API tests: setup, sign-in, sessions, roles and sharing, and that records and documents only reach the accounts
a profile is shared with. Uses the fictional demo data in a temporary folder.

  pip install -r requirements-dev.txt
  python3 tests/test_api.py
"""
import json, os, shutil, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-test-")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), PASSWORD_MIN_LENGTH="15",
                  SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"), PUBLIC_ORIGINS="https://health.example.com/")
os.makedirs(os.path.join(TMP, "records"))
subprocess.run([sys.executable, "-I", os.path.join(ROOT, "demo", "generate.py"), os.path.join(TMP, "build"),
                "--end", "2026-11-15", "--watch", os.path.join(TMP, "watch")], check=True, stdout=subprocess.DEVNULL)
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import db as dbm, main, security as sec   # noqa: E402

H = {"X-Sanguity": "1"}
PW = {"ben": "correct horse battery staple", "alex": "purple monkey dishwasher", "sam": "seven wild otters dancing"}
CODE = "TEST-CODE-1234"


def client():
    return TestClient(main.app, base_url="http://testserver")


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = client().__enter__()   # runs startup: database, demo build into profile 1, setup code
        db = dbm.connect()
        dbm.meta_set(db, "setup_code", sec.token_hash(CODE))
        db.close()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        shutil.rmtree(TMP, ignore_errors=True)

    def setUp(self):
        sec.throttle.clear(*list(sec.throttle._fails))

    def login(self, name, pw=None):
        c = client()
        r = c.post("/api/login", json={"username": name, "password": pw or PW[name]}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return c

    def make_user(self, admin_c, name):
        """Admin creates an account; its owner signs in with the one-time password and sets their own."""
        r = admin_c.post("/api/users", json={"username": name}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        c = self.login(name, r.json()["temporary_password"])
        self.assertEqual(c.get("/api/me").json()["user"]["must_change_pw"], True)
        self.assertEqual(c.get("/api/profiles/1/data").status_code, 403)   # nothing until the password is changed
        r = c.post("/api/me/password", json={"current": r.json()["temporary_password"], "new": PW[name]}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return c

    def test_a_flow(self):   # runs first: it does the first-run setup the other tests rely on
        anon = client()
        self.assertTrue(anon.get("/api/setup").json()["needed"])
        self.assertEqual(anon.get("/api/me").status_code, 401)
        self.assertEqual(anon.get("/api/profiles/1/data").status_code, 401)

        # ---- first-run setup: wrong code, weak password, then the real thing
        bad = {"code": "NOPE-NOPE-NOPE", "username": "ben", "password": PW["ben"], "profile_name": "Ben"}
        self.assertEqual(anon.post("/api/setup", json=bad, headers=H).status_code, 403)
        weak = {**bad, "code": CODE, "password": "short"}
        self.assertEqual(anon.post("/api/setup", json=weak, headers=H).status_code, 400)
        ben = client()
        r = ben.post("/api/setup", json={**bad, "code": CODE.lower()}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(anon.post("/api/setup", json={**bad, "code": CODE}, headers=H).status_code, 409)
        self.assertFalse(anon.get("/api/setup").json()["needed"])

        me = ben.get("/api/me").json()
        self.assertTrue(me["user"]["is_admin"])
        self.assertEqual([(p["id"], p["name"], p["role"]) for p in me["profiles"]], [(1, "Ben", "owner")])

        # ---- the existing records came across, with documents behind the API
        data = ben.get("/api/profiles/1/data").json()
        self.assertGreater(len(data["events"]), 100)
        urls = [d["url"] for d in data["details"].values() if isinstance(d, dict) and d.get("type") == "file"]
        self.assertTrue(urls and all(u.startswith("api/profiles/1/documents/") for u in urls), urls[:3])
        doc = ben.get("/" + urls[0])
        self.assertEqual(doc.status_code, 200)
        self.assertEqual(doc.headers["content-type"], "application/pdf")
        self.assertIn("script-src 'none'", doc.headers["content-security-policy"])
        self.assertIn("default-src 'none'", doc.headers["content-security-policy"])   # nothing fetched from elsewhere
        self.assertEqual(ben.get("/api/profiles/1/watch").status_code, 200)
        self.assertIn("visitWorkouts", ben.get("/api/profiles/1/settings").json())

        # ---- two more accounts; a second profile; sharing
        alex = self.make_user(ben, "alex")
        sam = self.make_user(ben, "sam")
        self.assertEqual(alex.get("/api/me").json()["profiles"], [])
        self.assertEqual(alex.get("/api/profiles/1/data").status_code, 404)   # not shared: reported as not found
        self.assertEqual(alex.get("/" + urls[0]).status_code, 404)
        self.assertEqual(alex.get("/api/users").status_code, 403)              # not an admin

        r = alex.post("/api/profiles", json={"name": "Alex"}, headers=H)
        alex_pid = r.json()["id"]
        self.assertEqual(ben.get(f"/api/profiles/{alex_pid}/data").status_code, 404)   # admin sees no one else's records
        self.assertEqual(alex.get(f"/api/profiles/{alex_pid}/data").status_code, 204)  # exists, but no records yet

        # Ben shares his profile with Alex as viewer: Alex sees it, Sam still doesn't
        self.assertEqual(ben.put("/api/profiles/1/members", json={"username": "alex", "role": "viewer"}, headers=H).status_code, 200)
        self.assertEqual(sorted(p["id"] for p in alex.get("/api/me").json()["profiles"]), sorted([1, alex_pid]))
        self.assertEqual(alex.get("/api/profiles/1/data").status_code, 200)
        self.assertEqual(alex.get("/" + urls[0]).status_code, 200)
        self.assertEqual(sam.get("/api/me").json()["profiles"], [])
        self.assertEqual(sam.get("/api/profiles/1/data").status_code, 404)
        self.assertEqual(sam.get("/" + urls[0]).status_code, 404)

        # a viewer can't share, rename or list members; a document id from another profile doesn't work
        self.assertEqual(alex.put("/api/profiles/1/members", json={"username": "sam", "role": "viewer"}, headers=H).status_code, 403)
        self.assertEqual(alex.patch("/api/profiles/1", json={"name": "x"}, headers=H).status_code, 403)
        self.assertEqual(alex.get("/api/profiles/1/members").status_code, 403)
        doc_id = urls[0].rsplit("/", 1)[1]
        self.assertEqual(alex.get(f"/api/profiles/{alex_pid}/documents/{doc_id}").status_code, 404)

        # the last owner can't leave or be demoted
        self.assertEqual(ben.delete("/api/profiles/1/members/1", headers=H).status_code, 409)
        self.assertEqual(ben.put("/api/profiles/1/members", json={"username": "ben", "role": "viewer"}, headers=H).status_code, 409)
        # promote Alex to owner; then Alex can manage it and Ben may step down
        ben.put("/api/profiles/1/members", json={"username": "alex", "role": "owner"}, headers=H)
        self.assertEqual(len(alex.get("/api/profiles/1/members").json()), 2)
        self.assertEqual(ben.put("/api/profiles/1/members", json={"username": "ben", "role": "editor"}, headers=H).status_code, 200)
        self.assertEqual(ben.get("/api/profiles/1/members").status_code, 403)
        self.assertEqual(ben.get("/api/profiles/1/data").status_code, 200)
        alex.put("/api/profiles/1/members", json={"username": "ben", "role": "owner"}, headers=H)
        # a viewer may leave on their own
        alex.put("/api/profiles/1/members", json={"username": "sam", "role": "viewer"}, headers=H)
        self.assertEqual(sam.delete("/api/profiles/1/members/3", headers=H).status_code, 200)
        self.assertEqual(sam.get("/api/profiles/1/data").status_code, 404)

        # ---- sessions: list, revoke one, revoke others
        ben2 = self.login("ben")
        sess = ben.get("/api/sessions").json()
        self.assertEqual(len(sess), 2)
        other = next(s for s in sess if not s["current"])
        self.assertEqual(ben.delete(f"/api/sessions/{other['id']}", headers=H).status_code, 200)
        self.assertEqual(ben2.get("/api/me").status_code, 401)
        self.assertEqual(alex.delete(f"/api/sessions/{sess[0]['id']}", headers=H).status_code, 404)   # not Alex's
        ben3 = self.login("ben")
        self.assertEqual(ben.post("/api/sessions/revoke-others", headers=H).json()["revoked"], 1)
        self.assertEqual(ben3.get("/api/me").status_code, 401)

        # ---- admin: disabling signs the account out at once; reset gives a one-time password
        sam_id = next(u["id"] for u in ben.get("/api/users").json() if u["username"] == "sam")
        ben.patch(f"/api/users/{sam_id}", json={"disabled": True}, headers=H)
        self.assertEqual(sam.get("/api/me").status_code, 401)
        self.assertEqual(client().post("/api/login", json={"username": "sam", "password": PW["sam"]}, headers=H).status_code, 401)
        r = ben.patch(f"/api/users/{sam_id}", json={"disabled": False, "reset_password": True}, headers=H).json()
        self.assertEqual(self.login("sam", r["temporary_password"]).get("/api/me").json()["user"]["must_change_pw"], True)
        self.assertEqual(ben.patch("/api/users/1", json={"is_admin": False}, headers=H).status_code, 400)   # not on yourself

        # ---- password change signs out other devices and needs the current password
        alex2 = self.login("alex")
        self.assertEqual(alex.post("/api/me/password", json={"current": "wrong wrong wrong wrong", "new": "another long passphrase"}, headers=H).status_code, 403)
        r = alex.post("/api/me/password", json={"current": PW["alex"], "new": "another long passphrase"}, headers=H)
        self.assertEqual(r.json()["signed_out_elsewhere"], 1)
        self.assertEqual(alex2.get("/api/me").status_code, 401)
        self.assertEqual(alex.get("/api/me").status_code, 200)

        # ---- logout ends the session on the server
        self.assertEqual(ben.post("/api/logout", headers=H).status_code, 200)
        self.assertEqual(ben.get("/api/me").status_code, 401)

    def test_b_settings_follow_the_build(self):   # after test_a_flow: profile 1 is Ben's and linked to the build
        ben = self.login("ben")
        built = os.path.join(TMP, "build", "settings.json")
        self.assertIn("visitWorkouts", ben.get("/api/profiles/1/settings").json())
        with open(built, "w", encoding="utf-8") as f:
            f.write('{"sources": {"EXT": "Outside lab"}}')
        later = os.path.getmtime(built) + 5
        os.utime(built, (later, later))   # only settings.json changed, not data.json
        self.assertEqual(ben.get("/api/profiles/1/settings").json(), {"sources": {"EXT": "Outside lab"}})
        data_file = os.path.join(TMP, "build", "data.json")   # a data.json restored with an older date is noticed too
        with open(data_file, encoding="utf-8") as f:
            d = json.load(f)
        d["source"] = "Restored copy"
        with open(data_file, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.utime(data_file, (later - 100000, later - 100000))
        self.assertEqual(ben.get("/api/profiles/1/data").json()["source"], "Restored copy")
        os.remove(built)                  # removing it goes back to the defaults
        self.assertEqual(ben.get("/api/profiles/1/settings").json(), {})

    def test_c_bad_build_keeps_app_up(self):   # after test_b: profile 1 is Ben's, linked to the build
        from server import profiles as prof
        built = os.path.join(TMP, "build", "data.json")
        with open(built, "rb") as f:
            good = f.read()
        ben = self.login("ben")
        db = dbm.connect()
        fresh = prof.create_profile(db, "Fresh", owner_id=1, linked_build=True)   # linked, no copy yet
        try:
            with open(built, "w", encoding="utf-8") as f:
                f.write("[1, 2, 3]")   # valid JSON, wrong shape
            later = os.path.getmtime(built) + 10
            os.utime(built, (later, later))
            with client() as restarted:   # startup with a bad build still comes up and serves sign-in
                self.assertEqual(restarted.get("/api/setup").status_code, 200)
            self.assertEqual(ben.get("/api/profiles/1/data").status_code, 200)   # keeps its last good copy
            r = ben.get(f"/api/profiles/{fresh}/data")
            self.assertEqual(r.status_code, 500)
            self.assertIn("isn't a Sanguity data file", r.json()["detail"])
            from server import manage
            with self.assertRaises(SystemExit) as e:   # `manage sync` says so and fails, instead of "up to date"
                manage.run(["sync"])
            self.assertIn("couldn't load the setup build", str(e.exception.code))
        finally:
            with open(built, "wb") as f:
                f.write(good)
            later = os.path.getmtime(built) + 20
            os.utime(built, (later, later))
            db.execute("DELETE FROM profiles WHERE id=?", (fresh,))
            db.close()
        self.assertEqual(ben.get("/api/profiles/1/data").status_code, 200)   # a fixed rebuild is picked up again

    def test_csrf(self):
        c = client()
        self.assertEqual(c.post("/api/login", json={"username": "x", "password": "y"}).status_code, 403)   # no header
        r = c.post("/api/login", json={"username": "x", "password": "y"}, headers={**H, "Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)
        r = c.post("/api/login", json={"username": "x", "password": "y"}, headers={**H, "Origin": "http://testserver"})
        self.assertEqual(r.status_code, 401)
        # behind a reverse proxy that rewrites Host: its X-Forwarded-Host, or an address in PUBLIC_ORIGINS
        r = c.post("/api/login", json={"username": "x", "password": "y"},
                   headers={**H, "Origin": "https://nas.home:8443", "X-Forwarded-Host": "nas.home:8443"})
        self.assertEqual(r.status_code, 401)
        r = c.post("/api/login", json={"username": "x", "password": "y"}, headers={**H, "Origin": "https://health.example.com"})
        self.assertEqual(r.status_code, 401)
        r = c.post("/api/login", json={"username": "x", "password": "y"},   # a proxy that spells out the default port
                   headers={**H, "Origin": "https://nas.home", "X-Forwarded-Host": "nas.home:443"})
        self.assertEqual(r.status_code, 401)
        r = c.post("/api/login", json={"username": "x", "password": "y"},
                   headers={**H, "Origin": "https://evil.example", "X-Forwarded-Host": "nas.home:8443"})
        self.assertEqual(r.status_code, 403)

    def test_throttle(self):
        c = client()
        codes = [c.post("/api/login", json={"username": "nobody", "password": "wrong password here"}, headers=H).status_code for _ in range(7)]
        self.assertEqual(codes[:5], [401] * 5)
        self.assertEqual(codes[-1], 429)

    def test_known_device_isnt_locked_out(self):
        db = dbm.connect()
        if not db.execute("SELECT 1 FROM users WHERE username='kim'").fetchone():
            db.execute("INSERT INTO users(username, pw_hash, created) VALUES('kim', ?, 0)", (sec.hash_password(PW["sam"]),))
        kim_id = db.execute("SELECT id FROM users WHERE username='kim'").fetchone()[0]
        db.close()
        kim = client()   # Kim's own browser signs in once and is remembered
        self.assertEqual(kim.post("/api/login", json={"username": "kim", "password": PW["sam"]}, headers=H).status_code, 200)
        self.assertTrue(any(c.name == "sg_device" for c in kim.cookies.jar))
        stranger = client()   # same address (as behind a reverse proxy), no device cookie
        codes = [stranger.post("/api/login", json={"username": "kim", "password": "not kim's password"}, headers=H).status_code for _ in range(8)]
        self.assertEqual(codes[-1], 429)
        self.assertEqual(client().post("/api/login", json={"username": "kim", "password": PW["sam"]}, headers=H).status_code, 429)
        self.assertEqual(kim.post("/api/login", json={"username": "kim", "password": PW["sam"]}, headers=H).status_code, 200)
        # a device cookie only counts for its own account, and an admin reset forgets every device
        db = dbm.connect()
        token = next(c.value for c in kim.cookies.jar if c.name == "sg_device")
        self.assertIsNone(main._known_device(type("R", (), {"cookies": {"sg_device": token}})(), db, {"id": kim_id + 999}))
        db.execute("DELETE FROM devices WHERE user_id=?", (kim_id,))
        db.close()
        self.assertEqual(kim.post("/api/login", json={"username": "kim", "password": PW["sam"]}, headers=H).status_code, 429)

    def test_short_idle_timeout_keeps_active_sessions(self):
        from server import config
        db = dbm.connect()
        if not db.execute("SELECT 1 FROM users WHERE username='idle'").fetchone():
            db.execute("INSERT INTO users(username, pw_hash, created) VALUES('idle', ?, 0)", (sec.hash_password(PW["ben"]),))
        c = self.login("idle", PW["ben"])
        old = config.SESSION_IDLE_MINUTES
        config.SESSION_IDLE_MINUTES = 1
        try:
            db.execute("UPDATE sessions SET last_seen=? WHERE user_id=(SELECT id FROM users WHERE username='idle')", (dbm.now() - 50,))
            self.assertEqual(c.get("/api/me").status_code, 200)   # used 50 s ago: still signed in, and that use counts...
            db.execute("UPDATE sessions SET last_seen=last_seen-20 WHERE user_id=(SELECT id FROM users WHERE username='idle')")
            self.assertEqual(c.get("/api/me").status_code, 200)   # ...so 20 s later it isn't judged idle from the older time
            db.execute("UPDATE sessions SET last_seen=? WHERE user_id=(SELECT id FROM users WHERE username='idle')", (dbm.now() - 61,))
            self.assertEqual(c.get("/api/me").status_code, 401)   # unused for over a minute: signed out
        finally:
            config.SESSION_IDLE_MINUTES = old
            db.close()

    def test_disabling_the_only_owner_asks_first(self):
        from server import accounts, profiles as prof
        admin_c = self.login("ben")
        db = dbm.connect()
        lee, _ = accounts.create(db, "lee", password="lee's long passphrase")
        moe, _ = accounts.create(db, "moe", password="moe's long passphrase")
        pid = prof.create_profile(db, "Lee's mum", owner_id=lee)
        r = admin_c.patch(f"/api/users/{lee}", json={"disabled": True}, headers=H)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"]["sole_owner_of"], ["Lee's mum"])
        self.assertEqual(db.execute("SELECT disabled FROM users WHERE id=?", (lee,)).fetchone()[0], 0)   # nothing changed
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'owner')", (moe, pid))
        self.assertEqual(admin_c.patch(f"/api/users/{lee}", json={"disabled": True}, headers=H).status_code, 200)   # Moe co-owns
        r = admin_c.patch(f"/api/users/{moe}", json={"disabled": True}, headers=H)   # Lee is disabled, so Moe is now the only active owner
        self.assertEqual(r.status_code, 409)
        self.assertEqual(admin_c.patch(f"/api/users/{moe}", json={"disabled": True, "force": True}, headers=H).status_code, 200)
        db.close()

    def test_shared_browser_keeps_everyone_known(self):
        from server import accounts
        db = dbm.connect()
        for name in ("pat", "sky"):
            if not db.execute("SELECT 1 FROM users WHERE username=?", (name,)).fetchone():
                accounts.create(db, name, password=f"{name}'s long passphrase here")
        db.close()
        tablet = client()   # one browser, two people signing in on it in turn
        for name in ("pat", "sky"):
            self.assertEqual(tablet.post("/api/login", json={"username": name, "password": f"{name}'s long passphrase here"}, headers=H).status_code, 200)
        stranger = client()
        for _ in range(8):
            stranger.post("/api/login", json={"username": "pat", "password": "a wrong guess, again"}, headers=H)
        self.assertEqual(client().post("/api/login", json={"username": "pat", "password": "pat's long passphrase here"}, headers=H).status_code, 429)
        r = tablet.post("/api/login", json={"username": "pat", "password": "pat's long passphrase here"}, headers=H)
        self.assertEqual(r.status_code, 200)   # Sky signing in after Pat didn't make the tablet a stranger to Pat

    def test_password_hashing_runs_a_few_at_a_time(self):
        import threading, time
        from server import config
        running, peak, lock = [0], [0], threading.Lock()
        real = sec._ph

        class Slow:   # stands in for the hasher and records how many checks overlap
            def verify(self, *a):
                with lock:
                    running[0] += 1
                    peak[0] = max(peak[0], running[0])
                time.sleep(0.05)
                with lock:
                    running[0] -= 1
                return real.verify(*a)
        sec._ph = Slow()
        try:
            threads = [threading.Thread(target=sec.verify_password, args=(None, "x")) for _ in range(12)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            sec._ph = real
        self.assertGreater(peak[0], 0)
        self.assertLessEqual(peak[0], config.HASH_PARALLEL)
        # when every slot stays taken, a sign-in waits briefly and then hears "busy" instead of tying up a worker
        old_wait, held = config.HASH_WAIT, 0
        config.HASH_WAIT = 0.2
        try:
            while sec._slots.acquire(blocking=False):
                held += 1
            r = client().post("/api/login", json={"username": "ben", "password": PW["ben"]}, headers=H)
            self.assertEqual(r.status_code, 503)
            self.assertIn("busy", r.json()["detail"])
        finally:
            for _ in range(held):
                sec._slots.release()
            config.HASH_WAIT = old_wait

    def test_owner_rule_counts_only_active_owners(self):
        from server import accounts, profiles as prof
        admin_c = self.login("ben")
        db = dbm.connect()
        ids = {}
        for name in ("ana", "bo", "cy"):
            ids[name] = db.execute("SELECT id FROM users WHERE username=?", (name,)).fetchone()
            ids[name] = ids[name][0] if ids[name] else accounts.create(db, name, password=f"{name} long passphrase ok")[0]
        pid = prof.create_profile(db, "Shared", owner_id=ids["ana"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'owner')", (ids["bo"], pid))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (ids["cy"], pid))
        admin_c.patch(f"/api/users/{ids['ana']}", json={"disabled": True}, headers=H)   # Bo still owns it, so no question
        bo = self.login("bo", "bo long passphrase ok")
        self.assertEqual(bo.delete(f"/api/profiles/{pid}/members/{ids['bo']}", headers=H).status_code, 409)   # Ana is disabled
        self.assertEqual(bo.put(f"/api/profiles/{pid}/members", json={"username": "bo", "role": "viewer"}, headers=H).status_code, 409)
        self.assertEqual(bo.put(f"/api/profiles/{pid}/members", json={"username": "cy", "role": "owner"}, headers=H).status_code, 200)
        self.assertEqual(bo.delete(f"/api/profiles/{pid}/members/{ids['bo']}", headers=H).status_code, 200)   # Cy owns it now
        # a profile already left with no active owner can still be changed (repaired), not frozen
        db.execute("UPDATE users SET disabled=1 WHERE id=?", (ids["cy"],))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (ids["bo"], pid))
        self.assertEqual(bo.delete(f"/api/profiles/{pid}/members/{ids['bo']}", headers=H).status_code, 200)
        db.execute("UPDATE users SET disabled=0 WHERE id IN (?, ?)", (ids["ana"], ids["cy"]))
        db.close()

    def test_html_record_loads_its_own_pictures_and_styles(self):
        from server import profiles as prof
        src = os.path.join(TMP, "html-export")
        os.makedirs(os.path.join(src, "docs", "visit_files"), exist_ok=True)
        files = {"docs/visit summary.htm": b"<link rel=stylesheet href=style.css><img src='visit_files/logo.png'>",
                 "docs/style.css": b"body{}", "docs/visit_files/logo.png": b"\x89PNG", "docs/notes.json": b"{}",
                 "docs/report.pdf": b"%PDF-1.4", "secret.png": b"\x89PNG"}
        for rel, body in files.items():
            with open(os.path.join(src, *rel.split("/")), "wb") as f:
                f.write(body)
        with open(os.path.join(src, "docs", "no-extension"), "wb") as f:
            f.write(b"<link rel=stylesheet href=style.css>")
        with open(os.path.join(src, "data.json"), "w", encoding="utf-8") as f:
            json.dump({"events": [], "details": {"1": {"type": "file", "url": "docs/visit summary.htm", "ext": "htm"},
                                                 "2": {"type": "file", "url": "docs/report.pdf", "ext": "pdf"},
                                                 "3": {"type": "file", "url": "docs/no-extension", "ext": "html"}}}, f)
        db = dbm.connect()
        pid = prof.create_profile(db, "HTML", owner_id=1)
        prof.import_dataset(db, pid, src)
        db.close()
        ben = self.login("ben")
        data = ben.get(f"/api/profiles/{pid}/data").json()
        html_url, pdf_url = data["details"]["1"]["url"], data["details"]["2"]["url"]
        self.assertTrue(html_url.endswith("/visit%20summary.htm"), html_url)
        self.assertEqual(ben.get("/" + html_url).headers["content-type"], "text/html; charset=utf-8")
        base = "/" + html_url.rsplit("/", 1)[0]
        self.assertEqual(ben.get(base + "/style.css").headers["content-type"], "text/css; charset=utf-8")
        self.assertEqual(ben.get(base + "/visit_files/logo.png").status_code, 200)
        self.assertEqual(ben.get(base + "/notes.json").status_code, 404)          # pictures and stylesheets only
        self.assertEqual(ben.get(base + "/../secret.png").status_code, 404)        # nothing outside its folder
        self.assertEqual(ben.get(base + "/..%2Fsecret.png").status_code, 404)
        self.assertEqual(ben.get("/" + pdf_url + "/style.css").status_code, 404)   # only HTML records have parts
        bare = data["details"]["3"]["url"]   # described as HTML in data.json though its name has no extension
        self.assertTrue(bare.endswith("/no-extension"), bare)
        self.assertEqual(ben.get("/" + bare.rsplit("/", 1)[0] + "/style.css").status_code, 200)
        self.assertEqual(self.login("alex", "another long passphrase").get(base + "/style.css").status_code, 404)   # not shared

    def test_throttle_flood_keeps_blocking_keys(self):
        t = sec.Throttle()
        t.MAX_KEYS = 100
        for _ in range(8):
            t.fail(*sec.login_keys("ben", "10.9.9.9"))
        self.assertGreater(t.wait(*sec.login_keys("ben", "10.9.9.9")), 0)
        for i in range(300):   # a flood of one-off keys from other addresses
            t.fail(f"pair:u{i}|10.8.{i // 250}.{i % 250}")
        self.assertLessEqual(len(t._fails), 100)
        self.assertGreater(t.wait(*sec.login_keys("ben", "10.9.9.9")), 0)   # still held back

    def test_rehash_keeps_the_password_that_was_checked(self):
        from server import accounts
        long_pw = ("correct horse battery staple " * 10)[:256]
        db = dbm.connect()
        if not db.execute("SELECT 1 FROM users WHERE username='longpw'").fetchone():
            accounts.create(db, "longpw", password=long_pw)
        db.close()
        real = sec.needs_rehash
        sec.needs_rehash = lambda stored: True
        try:   # a password manager adds a trailing newline: the first 256 characters are checked and rehashed
            self.assertEqual(client().post("/api/login", json={"username": "longpw", "password": long_pw + "\n"}, headers=H).status_code, 200)
        finally:
            sec.needs_rehash = real
        self.assertEqual(client().post("/api/login", json={"username": "longpw", "password": long_pw}, headers=H).status_code, 200)

    def test_manage_reports_and_keeps_disabled(self):
        from server import accounts, manage
        db = dbm.connect()
        if not db.execute("SELECT 1 FROM users WHERE username='gone'").fetchone():
            accounts.create(db, "gone", password="gone away long passphrase")
        db.execute("UPDATE users SET disabled=1 WHERE username='gone'")
        manage.run(["reset-password", "gone"])
        self.assertEqual(db.execute("SELECT disabled FROM users WHERE username='gone'").fetchone()[0], 1)
        manage.run(["reset-password", "gone", "--enable"])
        self.assertEqual(db.execute("SELECT disabled FROM users WHERE username='gone'").fetchone()[0], 0)
        db.close()

    def test_duplicate_username_race(self):
        from server import accounts

        class Racing:   # the "is it free?" check sees nothing, as if another request inserted just after it
            def __init__(self, db): self.db = db
            def execute(self, sql, args=()):
                if sql.startswith("SELECT 1 FROM users"):
                    return type("C", (), {"fetchone": lambda self: None})()
                return self.db.execute(sql, args)
        db = dbm.connect()
        accounts.create(db, "twin")
        with self.assertRaises(accounts.AccountError) as e:
            accounts.create(Racing(db), "Twin")
        self.assertEqual(e.exception.status, 409)
        db.close()

    def test_throttle_keys(self):
        t = sec.Throttle()
        # a stranger's guesses from their own address don't lock the owner out on theirs
        for _ in range(10):
            t.fail(*sec.login_keys("ben", "10.0.0.9"))
        self.assertGreater(t.wait(*sec.login_keys("ben", "10.0.0.9")), 0)
        self.assertEqual(t.wait(*sec.login_keys("ben", "10.0.0.2")), 0)
        # many addresses guessing at one account are slowed once the account's own count is used up
        for i in range(25):
            t.fail(*sec.login_keys("ben", f"10.1.0.{i}"))
        self.assertGreater(t.wait(*sec.login_keys("ben", "10.0.0.2")), 0)
        # one address guessing at many accounts is slowed after 20 tries
        for i in range(20):
            t.fail(*sec.login_keys(f"user{i}", "10.2.0.1"))
        self.assertGreater(t.wait(*sec.login_keys("someone-else", "10.2.0.1")), 0)
        # a flood of made-up usernames can't grow memory without bound
        t2 = sec.Throttle()
        t2.MAX_KEYS = 100
        for i in range(500):
            t2.fail(*sec.login_keys(f"u{i}", f"10.3.{i // 250}.{i % 250}"))
        self.assertLessEqual(len(t2._fails), 100)

    def test_cookie_flags(self):
        r = client().post("/api/login", json={"username": "nobody", "password": "x"}, headers=H)
        self.assertNotIn("set-cookie", r.headers)
        db = dbm.connect()
        if not db.execute("SELECT 1 FROM users WHERE username='cookie'").fetchone():
            db.execute("INSERT INTO users(username, pw_hash, created) VALUES('cookie', ?, 0)", (sec.hash_password(PW["ben"]),))
        db.close()
        r = client().post("/api/login", json={"username": "cookie", "password": PW["ben"]}, headers=H)
        ck = r.headers["set-cookie"].lower()
        for flag in ("httponly", "samesite=strict", "path=/"):
            self.assertIn(flag, ck)
        self.assertNotIn("secure", ck)   # plain http here; https sets it
        r = TestClient(main.app, base_url="https://testserver").post("/api/login", json={"username": "cookie", "password": PW["ben"]}, headers=H)
        self.assertIn("secure", r.headers["set-cookie"].lower())

    def test_document_paths(self):
        from server import profiles as prof
        base = os.path.join(TMP, "build")
        self.assertIsNone(prof.resolve_document("../sanguity.db", base))
        self.assertIsNone(prof.resolve_document("records/../../etc/passwd", base))
        self.assertIsNone(prof.resolve_document("https://example.com/x.pdf", base))
        self.assertTrue(prof.resolve_document("records/export-1/doc.pdf", base).startswith(os.path.realpath(os.path.join(TMP, "records"))))
        self.assertTrue(prof.resolve_document("files/demo-report.pdf#page=2", base).endswith("demo-report.pdf"))
        # a sources.json "files" prefix from an install before accounts: found in the old www/files*/
        old = os.path.join(TMP, "www", "files-mychart", "letter.pdf")
        os.makedirs(os.path.dirname(old), exist_ok=True)
        open(old, "wb").close()
        self.assertEqual(prof.resolve_document("files-mychart/letter.pdf", base), os.path.realpath(old))
        self.assertIsNone(prof.resolve_document("files-mychart/../../sanguity.db", base))
        # "files" only as a real folder name: files/../ can't reach the rest of the old www/
        open(os.path.join(TMP, "www", "data.json"), "w").close()
        self.assertEqual(prof.resolve_document("files-mychart/../data.json", base), os.path.realpath(os.path.join(base, "data.json")))
        self.assertIsNone(prof.resolve_document("files-mychart/../../www/data.json", base))

    def test_manage_import_failure_keeps_link(self):
        from server import manage
        db = dbm.connect()
        pid = db.execute("INSERT INTO profiles(name, created, linked_build) VALUES('linked', 0, 1)").lastrowid
        with self.assertRaises(FileNotFoundError):
            manage.run(["import", str(pid), os.path.join(TMP, "no-such-dir")])
        self.assertEqual(db.execute("SELECT linked_build FROM profiles WHERE id=?", (pid,)).fetchone()[0], 1)
        db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
