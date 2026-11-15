#!/usr/bin/env python3
"""API tests: setup, sign-in, sessions, roles and sharing, and that records and documents only reach the accounts
a profile is shared with. Uses the fictional demo data in a temporary folder.

  pip install -r requirements-dev.txt
  python3 tests/test_api.py
"""
import atexit, json, os, shutil, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-test-")
atexit.register(shutil.rmtree, TMP, True)   # after every test class (Backups uses what Api left)
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), PASSWORD_MIN_LENGTH="15",
                  SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"), PUBLIC_ORIGINS="https://health.example.com/",
                  SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"))   # outside the data folder, as in Docker
os.makedirs(os.path.join(TMP, "records"))
subprocess.run([sys.executable, "-I", os.path.join(ROOT, "demo", "generate.py"), os.path.join(TMP, "build"),
                "--end", "2026-11-15", "--watch", os.path.join(TMP, "watch")], check=True, stdout=subprocess.DEVNULL)
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import db as dbm, main, security as sec   # noqa: E402

H = {"X-Sanguity": "1"}


class SoftKey:
    """A software passkey: real WebAuthn messages (attestation "none", ES256), as a phone or security key sends them."""
    ORIGIN = "https://health.example.com"   # in PUBLIC_ORIGINS above; passkeys need HTTPS

    def __init__(self, origin=ORIGIN):
        import secrets
        from cryptography.hazmat.primitives.asymmetric import ec
        self.key, self.cid, self.count, self.origin = ec.generate_private_key(ec.SECP256R1()), secrets.token_bytes(32), 0, origin
        self.handle = None

    @staticmethod
    def b64(b):
        import base64
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    @staticmethod
    def unb64(s):
        import base64
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

    def _client(self, kind, challenge, origin=None):
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin or self.origin, "crossOrigin": False}).encode()

    def _auth_data(self, rp_id, flags, extra=b""):
        import hashlib, struct
        return hashlib.sha256(rp_id.encode()).digest() + bytes([flags]) + struct.pack(">I", self.count) + extra

    def create(self, options):
        import cbor2, struct
        nums = self.key.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")})
        self.handle = options["user"]["id"]
        auth = self._auth_data(options["rp"]["id"], 0x45, bytes(16) + struct.pack(">H", len(self.cid)) + self.cid + cose)
        att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth})
        return {"id": self.b64(self.cid), "rawId": self.b64(self.cid), "type": "public-key", "clientExtensionResults": {},
                "response": {"clientDataJSON": self.b64(self._client("webauthn.create", options["challenge"])),
                             "attestationObject": self.b64(att), "transports": ["internal"]}}

    def get(self, options, verified=True, origin=None, rp_id=None):
        import hashlib
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        self.count += 1
        auth = self._auth_data(rp_id or options["rpId"], 0x05 if verified else 0x01)
        client_data = self._client("webauthn.get", options["challenge"], origin)
        sig = self.key.sign(auth + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": self.b64(self.cid), "rawId": self.b64(self.cid), "type": "public-key", "clientExtensionResults": {},
                "response": {"clientDataJSON": self.b64(client_data), "authenticatorData": self.b64(auth),
                             "signature": self.b64(sig), "userHandle": self.handle}}


class other_key:
    """Point the app at a different key file for a while (missing, or holding a key that didn't write the data)."""
    def __init__(self, make=True):
        self.path, self.make = os.path.join(TMP, "keys", "other_key"), make

    def __enter__(self):
        from server import config, crypto
        if os.path.exists(self.path):
            os.remove(self.path)
        if self.make:
            crypto.make_key_file(self.path)
        self.saved = (config.KEY_FILE, crypto.keyring.path)
        config.KEY_FILE = crypto.keyring.path = self.path
        crypto.keyring.reset()

    def __exit__(self, *exc):
        from server import config, crypto
        config.KEY_FILE, crypto.keyring.path = self.saved
        crypto.keyring.reset()
        if os.path.exists(self.path):
            os.remove(self.path)
PW = {"ben": "kettle orbit marsh velvet", "alex": "purple monkey dishwasher", "sam": "seven wild otters dancing"}
CODE = "TEST-CODE-1234"


def client():
    return TestClient(main.app, base_url="http://testserver")


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = client().__enter__()   # runs startup: database, demo build into profile 1, setup code
        with open(os.path.join(TMP, "setup-code.txt"), encoding="utf-8") as f:   # also written to a private file
            assert len(f.read().strip()) == 14, "setup-code.txt doesn't hold a code"
        db = dbm.connect()
        dbm.meta_set(db, "setup_code", sec.token_hash(CODE))
        db.close()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

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
        self.assertFalse(os.path.exists(os.path.join(TMP, "setup-code.txt")))   # the code's file is gone with it
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
        from server import crypto
        d = json.loads(crypto.read_file(data_file))   # the app encrypts the build at start-up
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

    def test_activity_log(self):   # after test_a_flow: Ben owns profile 1, which he shared along the way
        from server import accounts, activity
        ben = self.login("ben")
        log = lambda c, q="": c.get("/api/profiles/1/activity" + q).json()
        start = log(ben)[0]["id"] if log(ben) else 0
        newer = lambda entries: [e for e in entries if e["id"] > start]
        # viewing: once per session for the records, once per session for each document
        data = ben.get("/api/profiles/1/data").json()
        ben.get("/api/profiles/1/data")
        doc = next(d["url"] for d in data["details"].values() if isinstance(d, dict) and d.get("type") == "file")
        ben.get("/" + doc)
        ben.get("/" + doc)
        mine = newer(log(ben))
        self.assertEqual([e["action"] for e in mine].count("view"), 1)
        self.assertEqual([e["action"] for e in mine].count("document"), 1)
        opened = next(e for e in mine if e["action"] == "document")
        self.assertEqual((opened["actor"], opened["detail"]), ("ben", {"name": "demo-report.pdf"}))
        self.login("ben").get("/api/profiles/1/data")   # a new session is a new view
        self.assertEqual([e["action"] for e in newer(log(ben))].count("view"), 2)
        # sharing and renaming are recorded with what changed
        db = dbm.connect()
        if not db.execute("SELECT 1 FROM users WHERE username='vic'").fetchone():
            accounts.create(db, "vic", password="vic has a long passphrase")
        db.close()
        ben.put("/api/profiles/1/members", json={"username": "vic", "role": "viewer"}, headers=H)
        ben.put("/api/profiles/1/members", json={"username": "vic", "role": "viewer"}, headers=H)   # no change, no entry
        ben.patch("/api/profiles/1", json={"name": "Ben (renamed)"}, headers=H)
        ben.patch("/api/profiles/1", json={"name": "Ben"}, headers=H)
        recent = newer(log(ben))
        shares = [e for e in recent if e["action"] == "share" and e["detail"]["username"] == "vic"]
        self.assertEqual([e["detail"] for e in shares], [{"username": "vic", "role": "viewer"}])
        self.assertEqual([e["detail"] for e in recent if e["action"] == "rename"],
                         [{"from": "Ben (renamed)", "to": "Ben"}, {"from": "Ben", "to": "Ben (renamed)"}])   # newest first
        # filters and paging
        self.assertTrue(all(e["action"] == "view" for e in log(ben, "?action=view")))
        self.assertTrue(all(e["actor_id"] == 1 for e in log(ben, "?actor=1")))
        page = log(ben, "?limit=2")
        self.assertEqual(len(page), 2)
        self.assertTrue(all(e["id"] < page[-1]["id"] for e in log(ben, f"?before={page[-1]['id']}")))
        # who may read it: owners of the profile; a viewer can't, and admins see account events only
        vic = self.login("vic", "vic has a long passphrase")
        self.assertEqual(vic.get("/api/profiles/1/activity").status_code, 403)
        self.assertEqual(vic.get("/api/activity").status_code, 403)
        accounts_log = ben.get("/api/activity").json()
        self.assertTrue(accounts_log and all(e["profile_id"] is None for e in accounts_log))
        self.assertTrue({"setup", "account.create", "password"} <= {e["action"] for e in accounts_log})
        self.assertFalse({"view", "document"} & {e["action"] for e in accounts_log})
        # old entries are pruned (at most once a day, when something is logged)
        db = dbm.connect()
        db.execute("INSERT INTO activity(at, actor, action) VALUES(?, 'old', 'password')", (dbm.now() - 400 * 86400,))
        dbm.meta_set(db, "activity_pruned", "0")
        activity.log(db, "password", actor="test")
        self.assertIsNone(db.execute("SELECT 1 FROM activity WHERE actor='old'").fetchone())
        db.close()

    def test_repair_ownerless(self):
        from server import accounts, profiles as prof
        db = dbm.connect()
        ids = {}
        for name in ("ola", "mia"):
            row = db.execute("SELECT id FROM users WHERE username=?", (name,)).fetchone()
            ids[name] = row[0] if row else accounts.create(db, name, password=f"{name} keeps a long passphrase")[0]
        pid = prof.create_profile(db, "Ola's", owner_id=ids["ola"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (ids["mia"], pid))
        ben = self.login("ben")
        ben.patch(f"/api/users/{ids['ola']}", json={"disabled": True, "force": True}, headers=H)   # now nobody can manage it
        listed = next(p for p in ben.get("/api/ownerless").json() if p["id"] == pid)
        self.assertEqual(set(listed), {"id", "name", "members"})   # names and members only, never records
        self.assertEqual([(m["username"], m["role"], m["disabled"]) for m in listed["members"]], [("mia", "viewer", 0), ("ola", "owner", 1)])
        self.assertNotIn(1, [p["id"] for p in ben.get("/api/ownerless").json()])   # profile 1 has an active owner
        # admins only, and only for profiles with no active owner
        mia = self.login("mia", "mia keeps a long passphrase")
        self.assertEqual(mia.get("/api/ownerless").status_code, 403)
        self.assertEqual(mia.post(f"/api/ownerless/{pid}/owner", json={"username": "mia"}, headers=H).status_code, 403)
        self.assertEqual(ben.post("/api/ownerless/1/owner", json={"username": "mia"}, headers=H).status_code, 409)
        self.assertEqual(ben.post(f"/api/ownerless/{pid}/owner", json={"username": "ola"}, headers=H).status_code, 404)   # disabled
        # repaired: the member is owner and can manage it again; it's off the list; both logs say so
        self.assertEqual(ben.post(f"/api/ownerless/{pid}/owner", json={"username": "mia"}, headers=H).status_code, 200)
        self.assertEqual(mia.get(f"/api/profiles/{pid}/members").status_code, 200)
        self.assertNotIn(pid, [p["id"] for p in ben.get("/api/ownerless").json()])
        shared = mia.get(f"/api/profiles/{pid}/activity?action=share").json()[0]
        self.assertEqual((shared["actor"], shared["detail"]), ("ben", {"username": "mia", "role": "owner", "from": "viewer", "repair": True}))
        self.assertEqual(ben.get("/api/activity").json()[0]["action"], "profile.repair")
        self.assertEqual(ben.get(f"/api/profiles/{pid}/data").status_code, 404)   # the admin still can't see its records
        db.close()

    def test_remove_account(self):
        from server import accounts, manage, profiles as prof
        db = dbm.connect()
        ids = {}
        for name in ("rita", "kai", "lou"):
            row = db.execute("SELECT id FROM users WHERE username=?", (name,)).fetchone()
            ids[name] = row[0] if row else accounts.create(db, name, password=f"{name} keeps a long passphrase")[0]
        pid = prof.create_profile(db, "Rita's", owner_id=ids["rita"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (ids["kai"], pid))
        rita = self.login("rita", "rita keeps a long passphrase")
        rita.patch(f"/api/profiles/{pid}", json={"name": "Rita's records"}, headers=H)   # something in the log
        ben = self.login("ben")
        # only admins, and not themselves
        self.assertEqual(rita.request("DELETE", f"/api/users/{ids['kai']}", json={}, headers=H).status_code, 403)
        self.assertEqual(ben.request("DELETE", "/api/users/1", json={}, headers=H).status_code, 400)
        # a sole owner: asked first, then their profile is handed over in the same step
        r = ben.request("DELETE", f"/api/users/{ids['rita']}", json={}, headers=H)
        self.assertEqual((r.status_code, r.json()["detail"]["sole_owner_of"]), (409, ["Rita's records"]))
        self.assertEqual(ben.request("DELETE", f"/api/users/{ids['rita']}", json={"hand_to": "nobody-here"}, headers=H).status_code, 404)
        r = ben.request("DELETE", f"/api/users/{ids['rita']}", json={"hand_to": "kai"}, headers=H)
        self.assertEqual((r.status_code, r.json()["handed_over"]), (200, ["Rita's records"]))
        # gone: can't sign in, signed out, not in sharing lists; the other member is unaffected and now owns it
        self.assertEqual(client().post("/api/login", json={"username": "rita", "password": "rita keeps a long passphrase"}, headers=H).status_code, 401)
        self.assertEqual(rita.get("/api/me").status_code, 401)
        kai = self.login("kai", "kai keeps a long passphrase")
        self.assertEqual([(m["username"], m["role"]) for m in kai.get(f"/api/profiles/{pid}/members").json()], [("kai", "owner")])
        self.assertNotIn("rita", [u["username"] for u in ben.get("/api/users").json()])
        # their past activity stays, marked as removed
        renamed = next(e for e in kai.get(f"/api/profiles/{pid}/activity").json() if e["action"] == "rename")
        self.assertEqual((renamed["actor"], renamed["actor_removed"]), ("rita", True))
        event = ben.get("/api/activity").json()[0]
        self.assertEqual((event["action"], event["detail"]["username"]), ("account.remove", "rita"))
        # --force leaves the profile without an owner; the command line works too
        pid2 = prof.create_profile(db, "Lou's", owner_id=ids["lou"])
        with self.assertRaises(SystemExit):
            manage.run(["remove-user", "lou"])   # asks for --hand-to or --force
        manage.run(["remove-user", "lou", "--force"])
        self.assertIsNone(db.execute("SELECT 1 FROM users WHERE username='lou'").fetchone())
        self.assertEqual(db.execute("SELECT COUNT(*) FROM memberships WHERE profile_id=?", (pid2,)).fetchone()[0], 0)
        db.close()

    def test_delete_profile(self):
        from server import accounts, manage, profiles as prof
        db = dbm.connect()
        ben = self.login("ben")
        for name in ("edna", "otto"):
            if not db.execute("SELECT 1 FROM users WHERE username=?", (name,)).fetchone():
                accounts.create(db, name, password=f"{name} has quite a long passphrase")
        # a profile with records and a document from a folder outside the data folder (like real exports)
        src = os.path.join(TMP, "delete-src")
        os.makedirs(os.path.join(src, "docs"), exist_ok=True)
        with open(os.path.join(src, "docs", "letter.pdf"), "wb") as f:
            f.write(b"%PDF-1.4 letter")
        with open(os.path.join(src, "data.json"), "w", encoding="utf-8") as f:
            json.dump({"events": [{"id": 1, "t": "2025-01-01T00:00:00", "k": "document", "title": "L"}],
                       "details": {"1": {"type": "file", "url": "docs/letter.pdf", "ext": "pdf"}}}, f)
        pid = prof.create_profile(db, "To be deleted", owner_id=1)
        prof.import_dataset(db, pid, src)
        ben.put(f"/api/profiles/{pid}/members", json={"username": "edna", "role": "editor"}, headers=H)
        ben.put(f"/api/profiles/{pid}/members", json={"username": "otto", "role": "viewer"}, headers=H)
        ben.get(f"/api/profiles/{pid}/data")   # something in its activity log
        # only an owner, and only with the exact name
        for who in ("edna", "otto"):
            c = self.login(who, f"{who} has quite a long passphrase")
            self.assertEqual(c.request("DELETE", f"/api/profiles/{pid}", json={"confirm_name": "To be deleted"}, headers=H).status_code, 403)
        self.assertEqual(ben.request("DELETE", f"/api/profiles/{pid}", json={"confirm_name": "to be deleted"}, headers=H).status_code, 400)
        r = ben.request("DELETE", f"/api/profiles/{pid}", json={"confirm_name": " To be deleted "}, headers=H)
        self.assertEqual((r.status_code, r.json()["unlinked_build"]), (200, False))
        # gone from disk and the database; the original files and other profiles are untouched
        self.assertFalse(os.path.exists(prof.profile_dir(pid)))
        for table in ("profiles WHERE id", "memberships WHERE profile_id", "documents WHERE profile_id", "activity WHERE profile_id"):
            self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table}=?", (pid,)).fetchone()[0], 0, table)
        self.assertTrue(os.path.isfile(os.path.join(src, "docs", "letter.pdf")))
        self.assertEqual(ben.get(f"/api/profiles/{pid}/data").status_code, 404)
        self.assertEqual(ben.get("/api/profiles/1/data").status_code, 200)
        event = ben.get("/api/activity").json()[0]
        self.assertEqual((event["action"], event["detail"]), ("profile.delete", {"name": "To be deleted", "members": 3}))
        # a profile showing the setup build: deleting it unlinks the build, so the next start won't recreate it;
        # link-build loads it into a profile again on purpose
        linked = prof.create_profile(db, "Build copy", owner_id=1, linked_build=True)
        prof.sync_build(db, linked)
        r = ben.request("DELETE", f"/api/profiles/{linked}", json={"confirm_name": "Build copy"}, headers=H)
        self.assertTrue(r.json()["unlinked_build"])
        self.assertEqual(dbm.meta_get(db, "build_unlinked"), "1")
        again = prof.create_profile(db, "Build again", owner_id=1)
        manage.run(["link-build", str(again)])
        self.assertIsNone(dbm.meta_get(db, "build_unlinked"))
        self.assertGreater(len(ben.get(f"/api/profiles/{again}/data").json()["events"]), 100)
        ben.request("DELETE", f"/api/profiles/{again}", json={"confirm_name": "Build again"}, headers=H)
        dbm.meta_set(db, "build_unlinked", None)
        db.close()

    def test_export_and_import(self):
        import io, zipfile
        from server import accounts, manage, profiles as prof
        ben = self.login("ben")
        r = ben.get("/api/profiles/1/export")
        self.assertEqual(r.status_code, 200, r.text[:200])
        self.assertEqual(r.headers["content-type"], "application/zip")
        self.assertRegex(r.headers["content-disposition"], r'attachment; filename="sanguity-[a-z0-9-]+-\d{8}\.zip"')
        z = zipfile.ZipFile(io.BytesIO(r.content))
        names = set(z.namelist())
        self.assertTrue({"data.json", "README.txt", "activity.json", "watch/daily.json"} <= names, names)
        self.assertEqual("settings.json" in names, bool(ben.get("/api/profiles/1/settings").json()))   # only when it has some
        data = json.loads(z.read("data.json"))
        links = [d["url"] for d in data["details"].values() if isinstance(d, dict) and d.get("type") == "file" and d.get("url")]
        self.assertTrue(links and all(u.split("#")[0] in names for u in links), links)   # every link points into the zip
        self.assertEqual(z.read(links[0].split("#")[0]), ben.get("/" + ben.get("/api/profiles/1/data").json()["details"][
            next(k for k, d in data["details"].items() if isinstance(d, dict) and d.get("url") == links[0])]["url"]).content)
        self.assertIn("health records", z.read("README.txt").decode())
        self.assertEqual(ben.get("/api/profiles/1/activity?action=export").json()[0]["actor"], "ben")
        # who may: owners and editors, not viewers
        db = dbm.connect()
        if not db.execute("SELECT 1 FROM users WHERE username='vera'").fetchone():
            accounts.create(db, "vera", password="vera reads but never exports")
        ben.put("/api/profiles/1/members", json={"username": "vera", "role": "viewer"}, headers=H)
        vera = self.login("vera", "vera reads but never exports")
        self.assertEqual(vera.get("/api/profiles/1/export").status_code, 403)
        ben.put("/api/profiles/1/members", json={"username": "vera", "role": "editor"}, headers=H)
        self.assertEqual(vera.get("/api/profiles/1/export").status_code, 200)
        # an HTML record goes with the pictures and stylesheet it uses, and nothing else from its folder
        src = os.path.join(TMP, "export-html")
        os.makedirs(os.path.join(src, "docs", "v_files"), exist_ok=True)
        for rel, body in {"docs/v.htm": b'<link href="v_files/s.css" rel=stylesheet><img src="v_files/a%20b.png">',
                          "docs/v_files/s.css": b"p{}", "docs/v_files/a b.png": b"\x89PNG", "docs/private.txt": b"no"}.items():
            with open(os.path.join(src, *rel.split("/")), "wb") as f:
                f.write(body)
        with open(os.path.join(src, "data.json"), "w", encoding="utf-8") as f:
            json.dump({"events": [{"id": 1, "t": "2025-01-01T00:00:00", "k": "document", "title": "V"}],
                       "details": {"1": {"type": "file", "url": "docs/v.htm", "ext": "htm"}}}, f)
        hp = prof.create_profile(db, "HTML export", owner_id=1)
        prof.import_dataset(db, hp, src)
        hz = zipfile.ZipFile(io.BytesIO(ben.get(f"/api/profiles/{hp}/export").content))
        docs = sorted(n for n in hz.namelist() if n.startswith("documents/"))
        self.assertEqual([n.split("/", 2)[2] for n in docs], ["v.htm", "v_files/a b.png", "v_files/s.css"])
        # round trip: load the export into a new profile with the command line; everything matches
        archive = os.path.join(TMP, "export-1.zip")
        with open(archive, "wb") as f:
            f.write(r.content)
        manage.run(["create-profile", "Copy of Ben", "ben"])
        copy = db.execute("SELECT id FROM profiles WHERE name='Copy of Ben'").fetchone()[0]
        manage.run(["import", str(copy), archive])
        original, copied = ben.get("/api/profiles/1/data").json(), ben.get(f"/api/profiles/{copy}/data").json()
        self.assertEqual(len(copied["events"]), len(original["events"]))
        self.assertEqual(ben.get(f"/api/profiles/{copy}/settings").json(), ben.get("/api/profiles/1/settings").json())
        self.assertEqual(ben.get(f"/api/profiles/{copy}/watch").status_code, 200)
        doc_copy = next(d["url"] for d in copied["details"].values() if isinstance(d, dict) and d.get("type") == "file" and d.get("url"))
        self.assertEqual(ben.get("/" + doc_copy).content, z.read(links[0].split("#")[0]))
        # a zip with anything unexpected is refused
        bad = os.path.join(TMP, "bad.zip")
        with zipfile.ZipFile(bad, "w") as bz:
            bz.writestr("data.json", "{}")
            bz.writestr("../escape.txt", "x")
        with self.assertRaises(ValueError):
            from server import export
            export.import_archive(db, copy, bad)
        db.close()

    def test_two_factor(self):
        import time
        from server import accounts, manage, twofactor
        step = lambda: int(time.time() // twofactor.PERIOD)
        code = lambda secret, ahead=0: twofactor._code(secret, step() + ahead)
        db = dbm.connect()
        if not db.execute("SELECT 1 FROM users WHERE username='tess'").fetchone():
            accounts.create(db, "tess", password="tess keeps a long passphrase")
        tess_id = db.execute("SELECT id FROM users WHERE username='tess'").fetchone()[0]
        tess = self.login("tess", "tess keeps a long passphrase")
        other = self.login("tess", "tess keeps a long passphrase")   # a second device, signed in before 2FA
        # turning it on: a secret to scan, confirmed with a first code, then 10 recovery codes shown once
        self.assertFalse(tess.get("/api/me/2fa").json()["on"])
        started = tess.post("/api/me/2fa/start", headers=H).json()
        secret = started["secret"]
        self.assertTrue(started["uri"].startswith("otpauth://totp/Sanguity:tess?") and started["qr"].startswith("data:image/svg+xml;base64,"))
        self.assertEqual(tess.post("/api/me/2fa/confirm", json={"code": "000000"}, headers=H).status_code, 400)
        first_code = code(secret)   # kept: the same code must not work again
        r = tess.post("/api/me/2fa/confirm", json={"code": first_code}, headers=H)
        recovery = r.json()["recovery_codes"]
        self.assertEqual(len(recovery), 10)
        self.assertTrue(tess.get("/api/me").json()["user"]["two_factor"])
        self.assertEqual(tess.get("/api/me").json()["password_min"], 8)
        # signing in: the password alone gives a ticket, not a session
        c = client()
        first = c.post("/api/login", json={"username": "tess", "password": "tess keeps a long passphrase"}, headers=H).json()
        self.assertTrue(first["two_factor"])
        self.assertEqual(c.get("/api/me").status_code, 401)
        self.assertEqual(c.post("/api/login/code", json={"ticket": first["ticket"], "code": first_code}, headers=H).status_code, 401)   # already used
        self.assertEqual(c.post("/api/login/code", json={"ticket": "made-up", "code": code(secret, 1)}, headers=H).status_code, 401)
        self.assertEqual(c.post("/api/login/code", json={"ticket": first["ticket"], "code": code(secret, 1)}, headers=H).status_code, 200)
        self.assertEqual(c.get("/api/me").status_code, 200)
        # five wrong codes and the ticket is gone, even before the throttle
        c2 = client()
        t = c2.post("/api/login", json={"username": "tess", "password": "tess keeps a long passphrase"}, headers=H).json()["ticket"]
        for _ in range(5):
            c2.post("/api/login/code", json={"ticket": t, "code": "123456"}, headers=H)
        self.assertIn(c2.post("/api/login/code", json={"ticket": t, "code": code(secret, 1)}, headers=H).status_code, (401, 429))
        sec.throttle.clear(*list(sec.throttle._fails))
        # a recovery code: works once, signs out every other session, says how many are left
        c3 = client()
        t = c3.post("/api/login", json={"username": "tess", "password": "tess keeps a long passphrase"}, headers=H).json()["ticket"]
        r = c3.post("/api/login/code", json={"ticket": t, "code": recovery[0].upper().replace("-", " ")}, headers=H)
        self.assertEqual((r.status_code, r.json().get("recovery_codes_left")), (200, 9))
        self.assertEqual(other.get("/api/me").status_code, 401)
        self.assertEqual(tess.get("/api/me").status_code, 401)
        t = client().post("/api/login", json={"username": "tess", "password": "tess keeps a long passphrase"}, headers=H).json()["ticket"]
        self.assertEqual(client().post("/api/login/code", json={"ticket": t, "code": recovery[0]}, headers=H).status_code, 401)
        # with 2FA on, an 8-character password is allowed; turning 2FA off then asks for a 15-character one
        tess = c3
        r = tess.post("/api/me/password", json={"current": "tess keeps a long passphrase", "new": "kq7 zv3m"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(tess.post("/api/me/2fa/off", json={"password": "wrong password!!"}, headers=H).status_code, 403)
        r = tess.post("/api/me/2fa/off", json={"password": "kq7 zv3m"}, headers=H)
        self.assertEqual((r.status_code, r.json()["must_change_pw"]), (200, True))
        self.assertTrue(tess.get("/api/me").json()["user"]["must_change_pw"])
        r = tess.post("/api/me/password", json={"current": "kq7 zv3m", "new": "kq7 zv3m again"}, headers=H)
        self.assertEqual(r.status_code, 400)   # 15 characters again without the second factor
        tess.post("/api/me/password", json={"current": "kq7 zv3m", "new": "tess keeps a long passphrase 2"}, headers=H)
        self.assertEqual(client().post("/api/login", json={"username": "tess", "password": "tess keeps a long passphrase 2"},
                                       headers=H).json(), {"must_change_pw": False})   # no second step any more
        # require it for everyone: only an admin who has it on can; others must set it up before seeing records
        ben = self.login("ben")
        self.assertEqual(ben.patch("/api/settings", json={"require_2fa": True}, headers=H).status_code, 409)
        db.execute("UPDATE users SET is_admin=1 WHERE id=?", (tess_id,))
        secret = tess.post("/api/me/2fa/start", headers=H).json()["secret"]
        tess.post("/api/me/2fa/confirm", json={"code": code(secret)}, headers=H)
        self.assertEqual(tess.patch("/api/settings", json={"require_2fa": True}, headers=H).status_code, 200)
        try:
            self.assertEqual(ben.get("/api/profiles/1/data").json()["detail"], "two_factor_required")
            self.assertTrue(ben.get("/api/me").json()["user"]["needs_two_factor"])
            self.assertEqual(ben.post("/api/me/2fa/start", headers=H).status_code, 200)   # setting it up is still allowed
            self.assertEqual(tess.post("/api/me/2fa/off", json={"password": "tess keeps a long passphrase 2"}, headers=H).status_code, 409)
        finally:
            tess.patch("/api/settings", json={"require_2fa": False}, headers=H)
        self.assertEqual(ben.get("/api/profiles/1/data").status_code, 200)
        # an admin, or the command line, can turn someone's off (lost phone and codes): signed out everywhere
        self.assertEqual(ben.patch(f"/api/users/{tess_id}", json={"disable_2fa": True}, headers=H).status_code, 200)
        self.assertEqual(tess.get("/api/me").status_code, 401)
        self.assertEqual(client().post("/api/login", json={"username": "tess", "password": "tess keeps a long passphrase 2"},
                                       headers=H).json(), {"must_change_pw": False})
        manage.run(["disable-2fa", "tess"])
        actions = {r[0] for r in db.execute("SELECT action FROM activity WHERE profile_id IS NULL")}
        self.assertTrue({"2fa.on", "2fa.off", "2fa.recovery", "2fa.reset", "settings.require_2fa"} <= actions)
        db.execute("UPDATE users SET is_admin=0 WHERE id=?", (tess_id,))
        db.close()

    def test_breached_and_context_passwords_are_refused(self):
        from server import accounts
        breached = ("correct horse battery staple", "Correct Horse Battery Staple 1!", "iloveyouiloveyou", "passwordpassword1")
        for pw in breached:
            self.assertIn("breached passwords", sec.password_problem(pw, "someone") or "", pw)
        self.assertIn("Sanguity", sec.password_problem("sanguity sanguity 2026", "x"))
        self.assertIn("username", sec.password_problem("benjamin benjamin 12", "benjamin"))
        self.assertIsNone(sec.password_problem("kettle orbit marsh velvet", "ben"))
        # refused wherever a password is chosen: a password change (setup and accounts.create use the same check)
        ben = self.login("ben")
        r = ben.post("/api/me/password", json={"current": PW["ben"], "new": "correct horse battery staple"}, headers=H)
        self.assertEqual(r.status_code, 400)
        self.assertIn("breached passwords", r.json()["detail"])
        db = dbm.connect()
        with self.assertRaises(accounts.AccountError):
            accounts.create(db, "famous", password="Correct Horse Battery Staple")
        db.close()
        self.assertEqual(self.login("ben").get("/api/me").status_code, 200)   # the old password still works

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

    def test_encryption_at_rest(self):   # after test_a_flow: profile 1 holds the demo build, Ben signs in
        import glob
        from server import atrest, config, crypto, manage
        files = glob.glob(os.path.join(TMP, "profiles", "*", "data.json"))
        self.assertTrue(files)
        for p in files:   # nothing Sanguity keeps is readable from the disk alone
            with open(p, "rb") as f:
                self.assertEqual(f.read(4), crypto.MAGIC, p)
        db = dbm.connect()
        for table, col in (("documents", "path"), ("activity", "detail")):
            vals = [r[0] for r in db.execute(f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL")]
            self.assertTrue(vals, table)
            self.assertTrue(all(v.startswith(crypto.FIELD) for v in vals), table)
        if os.name != "nt":
            self.assertEqual(os.stat(config.KEY_FILE).st_mode & 0o777, 0o600)
        ben = self.login("ben")
        records = ben.get("/api/profiles/1/data").json()
        # a missing key isn't replaced by a new one once data is encrypted; a different key is refused
        with other_key(make=False), self.assertRaises(SystemExit) as e:
            atrest.check_key(db)
        self.assertIn("encrypted", str(e.exception))
        self.assertFalse(os.path.exists(os.path.join(TMP, "keys", "other_key")))
        with other_key(), self.assertRaises(SystemExit) as e:
            atrest.check_key(db)
        self.assertIn("isn't the one", str(e.exception))
        # rotation: a new first key, re-encrypt, then the old line can go
        old = crypto.current_key_id()
        crypto.add_key(config.KEY_FILE)
        crypto.keyring.reset()
        self.assertEqual(ben.get("/api/profiles/1/data").json(), records)   # old data still opens with the old line
        from server import backup
        if backup.fcntl:   # the app is running here: it would keep writing with the old key
            with self.assertRaises(SystemExit):
                manage.run(["reencrypt"])
            atrest.seal_everything(db)   # what the app does as it starts
        else:
            manage.run(["reencrypt"])
        # that covered the setup build too, which the tests above rewrote in plaintext (in Docker, setup seals it)
        files += glob.glob(os.path.join(TMP, "build", "*.json"))
        new = crypto.current_key_id()
        self.assertNotEqual(old, new)
        for p in files:
            with open(p, "rb") as f:
                self.assertEqual(f.read(8), crypto.MAGIC + new, p)
        self.assertEqual(atrest.seal_everything(db), {"files": 0, "fields": 0})   # nothing left on the old key
        with open(config.KEY_FILE, encoding="ascii") as f:
            lines = [l for l in f.read().splitlines() if l and not l.startswith("#")]
        with open(config.KEY_FILE, "w", encoding="ascii") as f:
            f.write(lines[0] + "\n")
        crypto.keyring.reset()
        self.assertEqual(ben.get("/api/profiles/1/data").json(), records)
        doc = db.execute("SELECT id FROM documents WHERE profile_id=1 LIMIT 1").fetchone()[0]
        self.assertEqual(ben.get(f"/api/profiles/1/documents/{doc}").status_code, 200)
        db.close()

    def test_passkeys(self):
        from server import accounts, manage
        db = dbm.connect()
        for name in ("pia", "quinn"):
            if not db.execute("SELECT 1 FROM users WHERE username=?", (name,)).fetchone():
                accounts.create(db, name, password=f"{name} keeps a long passphrase")
        pia_id = db.execute("SELECT id FROM users WHERE username='pia'").fetchone()[0]
        O = {**H, "Origin": SoftKey.ORIGIN}
        pia = self.login("pia", "pia keeps a long passphrase")

        def add(c, who, key, name="Phone"):
            o = c.post("/api/me/passkeys/options", json={"password": f"{who} keeps a long passphrase"}, headers=O)
            self.assertEqual(o.status_code, 200, o.text)
            o = o.json()
            self.assertEqual((o["options"]["authenticatorSelection"]["userVerification"], o["options"]["rp"]["id"]), ("required", "health.example.com"))
            return c.post("/api/me/passkeys", json={"state": o["state"], "credential": key.create(o["options"]), "name": name}, headers=O)

        # where passkeys can't work, it says why
        self.assertIn("IP address", pia.get("/api/me/passkeys", params={"origin": "http://192.168.1.20:8730"}).json()["problem"])
        r = pia.post("/api/me/passkeys/options", json={"password": "pia keeps a long passphrase"}, headers={**H, "Origin": "http://testserver"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("HTTPS", r.json()["detail"])
        # adding one needs the password
        self.assertEqual(pia.post("/api/me/passkeys/options", json={"password": "wrong"}, headers=O).status_code, 403)
        key = SoftKey()
        r = add(pia, "pia", key)
        self.assertEqual((r.status_code, r.json()["name"]), (200, "Phone"))
        self.assertEqual(add(pia, "pia", key).status_code, 409)   # the same one twice
        listed = pia.get("/api/me/passkeys", params={"origin": SoftKey.ORIGIN}).json()
        self.assertEqual([(k["name"], k["here"], k["credential_id"]) for k in listed["passkeys"]], [("Phone", True, SoftKey.b64(key.cid))])
        self.assertEqual(listed["user_handle"], key.handle)   # what the page tells the device still counts
        self.assertTrue(pia.get("/api/me").json()["user"]["two_factor"])
        # a state is single-use, and only for the account it was made for
        o = pia.post("/api/me/passkeys/options", json={"password": "pia keeps a long passphrase"}, headers=O).json()
        quinn = self.login("quinn", "quinn keeps a long passphrase")
        self.assertEqual(quinn.post("/api/me/passkeys", json={"state": o["state"], "credential": SoftKey().create(o["options"])}, headers=O).status_code, 401)

        # after the password: the passkey is the second step; the password alone gives no session
        c = client()
        first = c.post("/api/login", json={"username": "pia", "password": "pia keeps a long passphrase"}, headers=H).json()
        self.assertEqual((first["two_factor"], first["methods"]), (True, {"code": False, "passkey": True}))
        self.assertEqual(c.get("/api/me").status_code, 401)
        self.assertEqual(c.post("/api/login/code", json={"ticket": first["ticket"], "code": "123456"}, headers=H).status_code, 401)
        o = c.post("/api/login/passkey/options", json={"ticket": first["ticket"]}, headers=O).json()
        self.assertEqual([x["id"] for x in o["options"]["allowCredentials"]], [SoftKey.b64(key.cid)])
        answer = key.get(o["options"], verified=False)   # after a password, presence is enough
        r = c.post("/api/login/passkey", json={"state": o["state"], "credential": answer, "ticket": first["ticket"]}, headers=O)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(c.get("/api/me").json()["user"]["username"], "pia")
        # the same answer again is refused (the state is used up)
        self.assertEqual(client().post("/api/login/passkey", json={"state": o["state"], "credential": answer}, headers=O).status_code, 401)

        def alone(k, **kw):
            c = client()
            o = c.post("/api/login/passkey/options", json={}, headers=O).json()
            self.assertEqual(o["options"]["userVerification"], "required")
            return c, c.post("/api/login/passkey", json={"state": o["state"], "credential": k.get(o["options"], **kw)}, headers=O)

        # on its own: needs the person verified (fingerprint, face, PIN), the right site, and a registered key
        c, r = alone(key)
        self.assertEqual((r.status_code, r.json()["username"]), (200, "pia"))
        self.assertEqual(c.get("/api/me").status_code, 200)
        self.assertEqual(alone(key, verified=False)[1].status_code, 401)
        self.assertEqual(alone(key, origin="https://evil.example")[1].status_code, 401)
        self.assertEqual(alone(key, rp_id="evil.example")[1].status_code, 401)
        r = alone(SoftKey())[1]   # a passkey the device has but Sanguity doesn't: says to delete it from the device
        self.assertEqual(r.status_code, 401)
        self.assertIn("Delete it from this device", r.json()["detail"])
        stale = SoftKey(); stale.key, stale.cid, stale.handle = key.key, key.cid, key.handle   # a cloned key: its count is behind
        self.assertEqual(alone(stale)[1].status_code, 401)
        # with someone else's ticket, pia's passkey doesn't sign in as them
        first = client().post("/api/login", json={"username": "quinn", "password": "quinn keeps a long passphrase"}, headers=H).json()
        self.assertNotIn("methods", first)   # no second step: quinn has none (signed straight in)

        # removing the last one can't lock anyone out: refused only where two-factor is required and it's the last
        pk = listed["passkeys"][0]["id"]
        dbm.meta_set(db, "require_2fa", "1")
        try:
            r = pia.delete(f"/api/me/passkeys/{pk}", headers=H)
            self.assertEqual(r.status_code, 409)
            key2 = SoftKey()
            pk2 = add(pia, "pia", key2, "Laptop").json()["id"]
            self.assertEqual(pia.delete(f"/api/me/passkeys/{pk}", headers=H).json()["left"], 1)
            # signing in with the one left still works, and counts as two-factor
            c, r = alone(key2)
            self.assertEqual(c.get("/api/profiles/1/data").status_code, 404)   # past the two-factor gate: just not shared
        finally:
            dbm.meta_set(db, "require_2fa", None)
        self.assertEqual(quinn.delete(f"/api/me/passkeys/{pk2}", headers=H).status_code, 404)   # not theirs
        self.assertEqual(pia.patch(f"/api/me/passkeys/{pk2}", json={"name": "  Work  laptop "}, headers=H).json()["name"], "Work laptop")
        # an admin's reset (lost phone) takes the passkeys too
        manage.run(["disable-2fa", "pia"])
        self.assertEqual(db.execute("SELECT COUNT(*) FROM passkeys WHERE user_id=?", (pia_id,)).fetchone()[0], 0)
        self.assertEqual(alone(key2)[1].status_code, 401)
        actions = {r[0] for r in db.execute("SELECT action FROM activity WHERE actor_id=?", (pia_id,))}
        self.assertTrue({"passkey.add", "passkey.remove"} <= actions)
        db.close()

    def test_manage_import_failure_keeps_link(self):
        from server import manage
        db = dbm.connect()
        pid = db.execute("INSERT INTO profiles(name, created, linked_build) VALUES('linked', 0, 1)").lastrowid
        with self.assertRaises(FileNotFoundError):
            manage.run(["import", str(pid), os.path.join(TMP, "no-such-dir")])
        self.assertEqual(db.execute("SELECT linked_build FROM profiles WHERE id=?", (pid,)).fetchone()[0], 1)
        db.close()


class Backups(unittest.TestCase):
    """Runs after Api (alphabetical class order doesn't apply to unittest.main's default loader across classes,
    so this class is listed explicitly below): it restores an older state, which the other tests don't expect."""

    def test_backup_verify_restore(self):
        import glob, io, tarfile
        from server import backup
        with client() as c:   # the app is running (and holds its lock) while the backup is taken
            if backup.fcntl:
                self.assertTrue(backup._app_is_running())
            path = backup.create(keep=2)
            self.assertTrue(backup.NAME.match(os.path.basename(path)))
            if os.name != "nt":
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)   # health data: the app's user only
            m = backup.verify(path)
            self.assertIn("sanguity.db", m["files"])
            self.assertTrue(any(n.startswith("profiles/") for n in m["files"]))
            if backup.fcntl:   # restoring under a running app is refused
                with self.assertRaises(backup.BackupError):
                    backup.restore(path)
            # something that happens after the backup, which restoring takes back
            db = dbm.connect()
            db.execute("INSERT INTO users(username, pw_hash, created) VALUES('after-backup', 'x', 0)")
            db.close()
        # damaged or doctored archives are refused
        with open(path, "rb") as f:
            good = f.read()
        bad = path.replace(".tar.gz", "-bad.tar.gz")
        with tarfile.open(path) as src, tarfile.open(bad, "w:gz") as dst:
            for mem in src.getmembers():
                data = src.extractfile(mem).read()
                if mem.name == "sanguity.db":
                    data = data[:-1] + b"x"
                mem.size = len(data)
                dst.addfile(mem, io.BytesIO(data))
        with self.assertRaises(backup.BackupError):
            backup.verify(bad)
        evil = path.replace(".tar.gz", "-evil.tar.gz")
        with tarfile.open(evil, "w:gz") as dst:
            for name in ("manifest.json", "../escape.txt"):
                info = tarfile.TarInfo(name); info.size = 2
                dst.addfile(info, io.BytesIO(b"{}"))
        with self.assertRaises(backup.BackupError):
            backup.verify(evil)
        # a backup another key wrote is refused before anything is replaced
        with other_key(), self.assertRaises(backup.BackupError) as e:
            backup.restore(path)
        self.assertIn("different key", str(e.exception))
        self.assertFalse(glob.glob(os.path.join(TMP, "before-restore-*")))
        # restore (app stopped): the state at backup time comes back, the replaced data is kept aside
        m, aside = backup.restore(path)
        self.assertTrue(os.path.isfile(os.path.join(aside, "sanguity.db")))
        db = dbm.connect()
        names = {r[0] for r in db.execute("SELECT username FROM users")}
        db.close()
        self.assertIn("ben", names)
        self.assertNotIn("after-backup", names)
        with client() as c:   # and the app starts on it
            self.assertEqual(c.post("/api/login", json={"username": "ben", "password": PW["ben"]}, headers=H).status_code, 200)
        self.assertEqual(good, open(path, "rb").read())   # verifying and restoring never change the archive
        self.assertEqual(len(backup.list_backups()), 1)   # the doctored copies don't count as backups


if __name__ == "__main__":
    suite = unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(Api),
                                unittest.defaultTestLoader.loadTestsFromTestCase(Backups)])   # Backups last
    sys.exit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
