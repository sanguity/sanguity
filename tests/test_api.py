#!/usr/bin/env python3
"""API tests: setup, sign-in, sessions, roles and sharing, and that records and documents only reach the accounts
a profile is shared with. Uses the fictional demo data in a temporary folder.

  pip install -r requirements-dev.txt
  python3 tests/test_api.py
"""
import atexit, base64, json, os, shutil, subprocess, sys, tempfile, unittest

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
from tests.whole_records import whole   # noqa: E402  records with every record's details (#217)
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)   # quiet here; assertLogs turns it on where it's checked
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
O = {**H, "Origin": SoftKey.ORIGIN}   # passkeys need an HTTPS origin
KEYS = {}   # name -> SoftKey, for accounts whose sign-in finishes with a passkey (Ben: admins need a second factor)


def same_records(data):
    """Records without the event ids the database gave them, which a re-import changes: the details, costs and
    lab-day lists in event order instead. Without the import each record came from (#100), which a re-import
    changes too. Without the conversions the page may show values in (#41, units.annotate), the out-of-pocket costs worked out
    (#55, oop.annotate) and each result row's series (#782, records._row_series), added as they're served."""
    data = json.loads(json.dumps(data))
    data.pop("imports", None)
    data.pop("unitConv", None)
    data.pop("medications", None)   # #50: worked out from the events as they're served, naming their ids
    data.pop("oop", None)   # out-of-pocket costs worked out as they're served (#55, oop.py)
    for x in data["events"] + data.get("labs", []):
        x.pop("imp", None)
        x.pop("conv", None)
    order = {e["id"]: n for n, e in enumerate(data["events"])}
    pos = lambda i: order.get(i, -1)
    details = [data["details"].get(str(e["id"])) for e in data["events"]]
    for d in details:
        if d:
            d.pop("series", None)
        if d and isinstance(d.get("ids"), list):
            d["ids"] = [pos(i) for i in d["ids"]]
    return {**data, "events": [{k: v for k, v in e.items() if k != "id"} for e in data["events"]], "details": details,
            "costs": [{**c, "id": pos(c["id"])} for c in data["costs"]]}


def db_count(sql, *args):
    db = dbm.connect()
    try:
        return db.execute(sql, args).fetchone()[0]
    finally:
        db.close()


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
        if r.json().get("two_factor") and name in KEYS:   # the second step, with the account's passkey
            ticket = r.json()["ticket"]
            o = c.post("/api/login/passkey/options", json={"ticket": ticket}, headers=O).json()
            r = c.post("/api/login/passkey", json={"state": o["state"], "credential": KEYS[name].get(o["options"]), "ticket": ticket}, headers=O)
            self.assertEqual(r.status_code, 200, r.text)
        return c

    def add_passkey(self, c, pw, key, name="Phone"):
        o = c.post("/api/me/passkeys/options", json={"password": pw}, headers=O)
        self.assertEqual(o.status_code, 200, o.text)
        o = o.json()
        r = c.post("/api/me/passkeys", json={"state": o["state"], "credential": key.create(o["options"]), "name": name}, headers=O)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

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
        self.assertEqual(anon.get("/api/setup").json()["password_min"], main.config.PASSWORD_MIN)   # setup's live count (#290)
        self.assertTrue(anon.get("/api/setup").json()["records"])   # the setup build's, which the first account will own (#498)
        self.assertEqual(anon.get("/api/me").status_code, 401)
        self.assertEqual(anon.get("/api/profiles/1/data").status_code, 401)

        # ---- first-run setup: wrong code, weak password, then the real thing
        bad = {"code": "NOPE-NOPE-NOPE", "username": "ben", "password": PW["ben"], "profile_name": "Ben"}
        self.assertEqual(anon.post("/api/setup", json=bad, headers=H).status_code, 403)
        weak = {**bad, "code": CODE, "password": "short"}
        self.assertEqual(anon.post("/api/setup", json=weak, headers=H).status_code, 400)
        # each refusal names its field (#501): the username's words in a password refusal mark the password
        for body, field in ((weak, "password"), ({**weak, "password": "benbenbenbenbenben"}, "password"),
                            ({**weak, "username": "b e n"}, "username")):
            r = anon.post("/api/setup", json=body, headers=H)
            self.assertEqual((r.status_code, r.json()["field"]), (400, field), r.text)
        ben = client()
        r = ben.post("/api/setup", json={**bad, "code": CODE.lower()}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(anon.post("/api/setup", json={**bad, "code": CODE}, headers=H).status_code, 409)
        self.assertFalse(os.path.exists(os.path.join(TMP, "setup-code.txt")))   # the code's file is gone with it
        self.assertEqual([anon.get("/api/setup").json()[k] for k in ("needed", "records")], [False, False])

        me = ben.get("/api/me").json()
        self.assertTrue(me["user"]["is_admin"])
        self.assertEqual([(p["id"], p["name"], p["role"]) for p in me["profiles"]], [(1, "Ben", "owner")])

        # ---- the existing records came across, with documents behind the API
        data = whole(ben, 1)
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

        # ---- admins need a second factor of their own, except on a one-person install (issue #75)
        self.assertFalse(me["user"]["admin_needs_two_factor"])
        self.assertEqual(ben.get("/api/users").status_code, 200)   # the only account: no second factor needed
        r = ben.post("/api/users", json={"username": "alex"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["admin_needs_two_factor"])   # not alone any more
        alex_temp = r.json()["temporary_password"]
        self.assertEqual(ben.get("/api/users").json()["detail"], "admin_two_factor_required")
        self.assertTrue(ben.get("/api/me").json()["user"]["admin_needs_two_factor"])
        self.assertEqual(ben.get("/api/profiles/1/data").status_code, 200)   # only admin actions wait for it
        KEYS["ben"] = SoftKey()
        self.add_passkey(ben, PW["ben"], KEYS["ben"])
        self.assertEqual(ben.get("/api/users").status_code, 200)
        self.assertFalse(ben.get("/api/me").json()["user"]["admin_needs_two_factor"])

        # ---- two more accounts; a second profile; sharing
        alex = self.login("alex", alex_temp)
        alex.post("/api/me/password", json={"current": alex_temp, "new": PW["alex"]}, headers=H)
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
        r = ben.put("/api/profiles/1/members", json={"username": "nobody-here", "role": "viewer"}, headers=H)   # no such account
        self.assertEqual((r.status_code, r.json()["detail"]), (404, "No active account with that username. An admin can create it under Admin → Accounts."))
        self.assertEqual(ben.put("/api/profiles/1/members", json={"username": "alex", "role": "viewer"}, headers=H).status_code, 200)
        self.assertEqual(sorted(p["id"] for p in alex.get("/api/me").json()["profiles"]), sorted([1, alex_pid]))
        self.assertEqual(alex.get("/api/profiles/1/data").status_code, 200)
        self.assertEqual(alex.get("/" + urls[0]).status_code, 200)
        self.assertEqual(sam.get("/api/me").json()["profiles"], [])
        self.assertEqual(sam.get("/api/profiles/1/data").status_code, 404)
        self.assertEqual(sam.get("/" + urls[0]).status_code, 404)

        # a viewer can't share or rename, but sees who has access and as what (#168); someone without access doesn't;
        # a document id from another profile doesn't work
        self.assertEqual(alex.put("/api/profiles/1/members", json={"username": "sam", "role": "viewer"}, headers=H).status_code, 403)
        self.assertEqual(alex.patch("/api/profiles/1", json={"name": "x"}, headers=H).status_code, 403)
        self.assertEqual([(m["username"], m["role"]) for m in alex.get("/api/profiles/1/members").json()], [("alex", "viewer"), ("ben", "owner")])
        self.assertEqual(sam.get("/api/profiles/1/members").status_code, 404)
        doc_id = urls[0].rsplit("/", 1)[1]
        self.assertEqual(alex.get(f"/api/profiles/{alex_pid}/documents/{doc_id}").status_code, 404)

        # the last owner can't leave or be demoted
        self.assertEqual(ben.delete("/api/profiles/1/members/1", headers=H).status_code, 409)
        self.assertEqual(ben.put("/api/profiles/1/members", json={"username": "ben", "role": "viewer"}, headers=H).status_code, 409)
        # promote Alex to owner; then Alex can manage it and Ben may step down
        ben.put("/api/profiles/1/members", json={"username": "alex", "role": "owner"}, headers=H)
        self.assertEqual(len(alex.get("/api/profiles/1/members").json()), 2)
        self.assertEqual(ben.put("/api/profiles/1/members", json={"username": "ben", "role": "editor"}, headers=H).status_code, 200)
        self.assertEqual(ben.put("/api/profiles/1/members", json={"username": "sam", "role": "viewer"}, headers=H).status_code, 403)
        self.assertEqual(ben.get("/api/profiles/1/members").status_code, 200)   # an editor still sees who has access
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

    def test_b_settings_seeded_by_the_build_once(self):   # after test_a_flow: profile 1 is Ben's and linked to the build
        ben = self.login("ben")
        built = os.path.join(TMP, "build", "settings.json")
        before = ben.get("/api/profiles/1/settings").json()
        self.assertTrue(before["visitWorkouts"])   # from the build's settings.json, the first time it was loaded
        with open(built, "w", encoding="utf-8") as f:
            f.write('{"sources": {"EXT": "Outside lab"}}')
        later = os.path.getmtime(built) + 5
        os.utime(built, (later, later))   # only settings.json changed, not data.json
        ben.get("/api/profiles/1/data")   # the rebuild is loaded, and settings changed in the app are kept (#35)
        self.assertEqual(ben.get("/api/profiles/1/settings").json(), before)
        data_file = os.path.join(TMP, "build", "data.json")   # a data.json restored with an older date is noticed too
        from server import crypto
        d = json.loads(crypto.read_file(data_file))   # the app encrypts the build at start-up
        d["source"] = "Restored copy"
        with open(data_file, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.utime(data_file, (later - 100000, later - 100000))
        self.assertEqual(ben.get("/api/profiles/1/data").json()["source"], "Restored copy")
        os.remove(built)                  # removing it changes nothing either
        self.assertEqual(ben.get("/api/profiles/1/settings").json(), before)

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
        data = whole(ben, 1)
        ben.get("/api/profiles/1/data")
        doc = next(d["url"] for d in data["details"].values() if isinstance(d, dict) and d.get("type") == "file")
        ben.get("/" + doc)
        ben.get("/" + doc)
        mine = newer(log(ben))
        self.assertEqual([e["action"] for e in mine].count("view"), 1)
        self.assertEqual([e["action"] for e in mine].count("document"), 1)
        opened = next(e for e in mine if e["action"] == "document")
        self.assertEqual((opened["actor"], opened["detail"]), ("ben", {"title": "PET/CT end of treatment (PDF)"}))   # its record's title (#342)
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
        # #314: its owners see whose account is disabled (ola: an owner who doesn't count as one), so the page knows that
        # mia changing her own role would be refused; someone who isn't an owner isn't told
        members = mia.get(f"/api/profiles/{pid}/members").json()
        self.assertEqual([(m["username"], m["role"], m["disabled"]) for m in members], [("mia", "owner", False), ("ola", "owner", True)])
        self.assertEqual(mia.put(f"/api/profiles/{pid}/members", json={"username": "mia", "role": "editor"}, headers=H).status_code, 409)
        row = db.execute("SELECT id FROM users WHERE username='nia'").fetchone()
        nia = row[0] if row else accounts.create(db, "nia", password="nia keeps a long passphrase")[0]
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (nia, pid))
        listed = self.login("nia", "nia keeps a long passphrase").get(f"/api/profiles/{pid}/members").json()
        self.assertEqual([sorted(m) for m in listed], [["role", "user_id", "username"]] * 3)
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
        self.assertEqual(rita.request("DELETE", f"/api/users/{ids['kai']}", json={"confirm_name": "kai"}, headers=H).status_code, 403)
        self.assertEqual(ben.request("DELETE", "/api/users/1", json={"confirm_name": "ben"}, headers=H).status_code, 400)
        # its username typed exactly (#287): missing, or not quite it, removes nothing
        self.assertEqual(ben.request("DELETE", f"/api/users/{ids['rita']}", json={}, headers=H).status_code, 422)
        r = ben.request("DELETE", f"/api/users/{ids['rita']}", json={"confirm_name": "Rita"}, headers=H)
        self.assertEqual((r.status_code, r.json()["detail"]), (400, "Type the account's username exactly to confirm."))
        # a sole owner: asked first, then their profile is handed over in the same step
        r = ben.request("DELETE", f"/api/users/{ids['rita']}", json={"confirm_name": " rita "}, headers=H)
        self.assertEqual((r.status_code, r.json()["detail"]["sole_owner_of"]), (409, ["Rita's records"]))
        self.assertEqual(ben.request("DELETE", f"/api/users/{ids['rita']}", json={"confirm_name": "rita", "hand_to": "nobody-here"}, headers=H).status_code, 404)
        r = ben.request("DELETE", f"/api/users/{ids['rita']}", json={"confirm_name": "rita", "hand_to": "kai"}, headers=H)
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
        for table in ("profiles WHERE id", "memberships WHERE profile_id", "documents WHERE profile_id", "activity WHERE profile_id",
                      "imports WHERE profile_id", "events WHERE profile_id", "series WHERE profile_id", "results WHERE profile_id"):
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
        self.assertIn("settings.json", names)   # always, so loading it back in restores them exactly (#35)
        data = json.loads(z.read("data.json"))
        links = [d["url"] for d in data["details"].values() if isinstance(d, dict) and d.get("type") == "file" and d.get("url")]
        self.assertTrue(links and all(u.split("#")[0] in names for u in links), links)   # every link points into the zip
        self.assertEqual(z.read(links[0].split("#")[0]), ben.get("/" + whole(ben, 1)["details"][
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
        original, copied = whole(ben, 1), whole(ben, copy)
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

    def test_pepper(self):
        """Password and recovery-code hashes can't be checked without the pepper (which the database holds only
        encrypted with the data key); hashes from before it still work, and passwords are upgraded at sign-in."""
        import hashlib, secrets, time
        from server import accounts, atrest, crypto, twofactor
        db = dbm.connect()
        stored = dbm.meta_get(db, "pepper")
        self.assertTrue(stored.startswith(crypto.FIELD))   # not readable from the database alone
        pepper = sec._pepper
        self.assertEqual(len(pepper), 32)
        self.assertEqual(atrest.load_pepper(db), pepper)   # made once, the same at every start
        # a password hash: Argon2id of HMAC(pepper, password); neither the password nor another pepper checks it
        pw = "pepper grinder on the windowsill"
        if not db.execute("SELECT 1 FROM users WHERE username='pep'").fetchone():
            accounts.create(db, "pep", password=pw)
        h = db.execute("SELECT pw_hash FROM users WHERE username='pep'").fetchone()[0]
        self.assertTrue(h.startswith(sec.PEPPERED + "$argon2id$"))
        self.assertTrue(sec.verify_password(h, pw))
        self.assertFalse(sec.needs_rehash(h))
        self.assertFalse(sec.verify_password(h[len(sec.PEPPERED):], pw))   # the Argon2 part alone, cracked as usual
        code = twofactor.new_recovery_codes()[0]
        ch = twofactor.recovery_hash(code)
        self.assertNotEqual(ch, twofactor.recovery_hash(code))   # a unique salt per code
        self.assertTrue(twofactor.recovery_matches(ch, code.upper()))
        self.assertFalse(twofactor.recovery_matches(ch, "abcd-efgh"))
        sec.set_pepper(secrets.token_bytes(32))
        try:
            self.assertFalse(sec.verify_password(h, pw))
            self.assertFalse(twofactor.recovery_matches(ch, code))
            sec.set_pepper(None)   # nothing is hashed without it
            with self.assertRaises(RuntimeError):
                sec.hash_password(pw)
            with self.assertRaises(RuntimeError):
                twofactor.recovery_hash(code)
        finally:
            sec.set_pepper(pepper)
        # a password hashed before the pepper still signs in, and is replaced by a peppered one as it does
        db.execute("UPDATE users SET pw_hash=? WHERE username='pep'", (sec._ph.hash(pw),))
        self.assertTrue(sec.needs_rehash(db.execute("SELECT pw_hash FROM users WHERE username='pep'").fetchone()[0]))
        self.assertEqual(client().post("/api/login", json={"username": "pep", "password": "not the password at all"}, headers=H).status_code, 401)
        self.login("pep", pw)
        h = db.execute("SELECT pw_hash FROM users WHERE username='pep'").fetchone()[0]
        self.assertTrue(h.startswith(sec.PEPPERED))
        self.login("pep", pw)
        # recovery codes from before the pepper (plain SHA-256) work until new ones are made, which are peppered
        uid = db.execute("SELECT id FROM users WHERE username='pep'").fetchone()[0]
        secret = twofactor.new_secret()
        db.execute("UPDATE users SET totp_secret=? WHERE id=?", (crypto.enc(secret), uid))
        old = ["ab2c-de3f", "gh4j-km5n"]
        db.execute("DELETE FROM recovery_codes WHERE user_id=?", (uid,))
        db.executemany("INSERT INTO recovery_codes(user_id, code_hash) VALUES(?, ?)",
                       [(uid, hashlib.sha256(c.replace("-", "").encode()).hexdigest()) for c in old])

        def with_code(code):
            t = client().post("/api/login", json={"username": "pep", "password": pw}, headers=H).json()["ticket"]
            return client().post("/api/login/code", json={"ticket": t, "code": code}, headers=H)
        r = with_code(old[1])
        self.assertEqual((r.status_code, r.json().get("recovery_codes_left")), (200, 1))
        self.assertEqual(with_code(old[1]).status_code, 401)   # once only
        new = accounts.new_recovery_codes(db, uid)
        rows = [r[0] for r in db.execute("SELECT code_hash FROM recovery_codes WHERE user_id=?", (uid,))]
        self.assertTrue(len(rows) == 10 and all(r.startswith("h1:") for r in rows))
        self.assertEqual(with_code(old[0]).status_code, 401)   # replaced
        r = with_code(new[3])
        self.assertEqual((r.status_code, r.json().get("recovery_codes_left")), (200, 9))
        sec.throttle.clear(*list(sec.throttle._fails))
        # losing the pepper row never silently makes a new one that would lock everyone out
        db.execute("DELETE FROM meta WHERE k='pepper'")
        try:
            with self.assertRaises(SystemExit) as e:
                atrest.load_pepper(db)
            self.assertIn("missing", str(e.exception))
        finally:
            dbm.meta_set(db, "pepper", stored)
        self.assertEqual(atrest.load_pepper(db), pepper)
        db.execute("DELETE FROM users WHERE id=?", (uid,))
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
        started = tess.post("/api/me/2fa/start", json={"password": "tess keeps a long passphrase"}, headers=H).json()
        secret = started["secret"]
        self.assertTrue(started["uri"].startswith("otpauth://totp/Sanguity:tess?") and "qr" not in started)   # the page draws it (qr.js, #495)
        self.assertEqual(tess.post("/api/me/2fa/confirm", json={"code": "000000", "password": "tess keeps a long passphrase"}, headers=H).status_code, 400)
        first_code = code(secret)   # kept: the same code must not work again
        r = tess.post("/api/me/2fa/confirm", json={"code": first_code, "password": "tess keeps a long passphrase"}, headers=H)
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
        # require it for everyone: only an admin who has it on can (any admin action needs it, with other accounts
        # around); others must set it up before seeing records
        ben = self.login("ben")
        if not db.execute("SELECT 1 FROM users WHERE username='uma'").fetchone():
            accounts.create(db, "uma", password="uma keeps a long passphrase")
        uma = self.login("uma", "uma keeps a long passphrase")
        db.execute("UPDATE users SET is_admin=1 WHERE id=?", (tess_id,))
        self.assertEqual(tess.patch("/api/settings", json={"require_2fa": True}, headers=H).json()["detail"], "admin_two_factor_required")
        secret = tess.post("/api/me/2fa/start", json={"password": "tess keeps a long passphrase 2"}, headers=H).json()["secret"]
        tess.post("/api/me/2fa/confirm", json={"code": code(secret), "password": "tess keeps a long passphrase 2"}, headers=H)
        self.assertEqual(tess.patch("/api/settings", json={"require_2fa": True}, headers=H).status_code, 200)
        try:
            self.assertEqual(uma.get("/api/profiles/1/data").json()["detail"], "two_factor_required")
            self.assertTrue(uma.get("/api/me").json()["user"]["needs_two_factor"])
            self.assertEqual(uma.post("/api/me/2fa/start", json={"password": "uma keeps a long passphrase"}, headers=H).status_code, 200)   # setting it up is still allowed
            self.assertEqual(ben.get("/api/profiles/1/data").status_code, 200)   # Ben's passkey counts
            self.assertEqual(tess.post("/api/me/2fa/off", json={"password": "tess keeps a long passphrase 2"}, headers=H).status_code, 409)
        finally:
            tess.patch("/api/settings", json={"require_2fa": False}, headers=H)
        self.assertEqual(uma.get("/api/profiles/1/data").status_code, 404)   # past the gate: just not shared
        # an admin's list says which second factor each has, for what turning it off removes (#536)
        listed = {u["username"]: (u["two_factor"], u["authenticator_app"], u["passkeys"]) for u in ben.get("/api/users").json()}
        self.assertEqual(listed["tess"], (1, 1, 0))
        self.assertEqual(listed["ben"][::2], (1, 1))
        # an admin, or the command line, can turn someone's off (lost phone and codes): signed out everywhere
        self.assertEqual(ben.patch(f"/api/users/{tess_id}", json={"disable_2fa": True}, headers=H).status_code, 200)
        self.assertEqual(next(u for u in ben.get("/api/users").json() if u["username"] == "tess")["authenticator_app"], 0)
        self.assertEqual(tess.get("/api/me").status_code, 401)
        self.assertEqual(client().post("/api/login", json={"username": "tess", "password": "tess keeps a long passphrase 2"},
                                       headers=H).json(), {"must_change_pw": False})
        manage.run(["disable-2fa", "tess"])
        actions = {r[0] for r in db.execute("SELECT action FROM activity WHERE profile_id IS NULL")}
        self.assertTrue({"2fa.on", "2fa.off", "2fa.recovery", "2fa.reset", "settings.require_2fa"} <= actions)
        db.execute("UPDATE users SET is_admin=0 WHERE id=?", (tess_id,))
        db.close()

    def test_admin_second_factor_and_fresh_check(self):   # ASVS 4.3.1 and 4.3.3 (issue #75)
        import time
        from server import accounts, config, manage, twofactor
        code = lambda secret, ahead=0: twofactor._code(secret, int(time.time() // twofactor.PERIOD) + ahead)
        db = dbm.connect()
        for name, admin in (("ada", True), ("ivo", False)):
            if not db.execute("SELECT 1 FROM users WHERE username=?", (name,)).fetchone():
                accounts.create(db, name, is_admin=admin, password=f"{name} keeps a long passphrase")
        ada_id, ivo_id = (db.execute("SELECT id FROM users WHERE username=?", (n,)).fetchone()[0] for n in ("ada", "ivo"))
        ivo = self.login("ivo", "ivo keeps a long passphrase")   # a passkey before Ada resets the password, below
        self.add_passkey(ivo, "ivo keeps a long passphrase", ivo_key := SoftKey())
        ada = self.login("ada", "ada keeps a long passphrase")
        # an admin without a second factor can't use any admin action, and is told to set one up
        self.assertTrue(ada.get("/api/me").json()["user"]["admin_needs_two_factor"])
        with self.assertLogs("sanguity.security", "WARNING") as logged:
            for method, path, body in (("GET", "/api/users", None), ("POST", "/api/users", {"username": "nobody-new"}),
                                       ("PATCH", f"/api/users/{ivo_id}", {"disabled": True}), ("GET", "/api/activity", None),
                                       ("GET", "/api/ownerless", None), ("GET", "/api/settings", None),
                                       ("PATCH", "/api/settings", {"require_2fa": False}), ("GET", "/api/security/notice", None),
                                       ("DELETE", f"/api/users/{ivo_id}", {})):
                r = ada.request(method, path, json=body, headers=H)
                self.assertEqual((r.status_code, r.json()["detail"]), (403, "admin_two_factor_required"), path)
        self.assertIn('"need":"admin second factor"', logged.output[0])
        self.assertEqual(ada.get("/api/profiles/1/data").status_code, 404)   # the rest of the account works
        # an authenticator app is enough
        secret = ada.post("/api/me/2fa/start", json={"password": "ada keeps a long passphrase"}, headers=H).json()["secret"]
        self.assertEqual(ada.post("/api/me/2fa/confirm", json={"code": code(secret), "password": "ada keeps a long passphrase"}, headers=H).status_code, 200)
        self.assertEqual(ada.get("/api/users").status_code, 200)
        self.assertFalse(ada.get("/api/me").json()["user"]["admin_needs_two_factor"])

        # the most powerful actions need a fresh check; signing in counts, for REAUTH_MINUTES
        stale = lambda: db.execute("UPDATE sessions SET verified_at=? WHERE user_id=?", (dbm.now() - config.REAUTH_MINUTES * 60 - 1, ada_id))
        stale()
        powerful = ({"reset_password": True}, {"disable_2fa": True}, {"is_admin": True})
        for body in powerful:
            r = ada.patch(f"/api/users/{ivo_id}", json=body, headers=H)
            self.assertEqual((r.status_code, r.json()["detail"]), (403, "reauth_required"), body)
        r = ada.post("/api/users", json={"username": "new-admin", "is_admin": True}, headers=H)
        self.assertEqual((r.status_code, r.json()["detail"]), (403, "reauth_required"))
        self.assertIsNone(db.execute("SELECT 1 FROM users WHERE username='new-admin'").fetchone())
        self.assertFalse(db.execute("SELECT is_admin FROM users WHERE id=?", (ivo_id,)).fetchone()[0])   # nothing changed
        # the everyday ones don't
        self.assertEqual(ada.patch(f"/api/users/{ivo_id}", json={"disabled": True}, headers=H).status_code, 200)
        self.assertEqual(ada.patch(f"/api/users/{ivo_id}", json={"disabled": False}, headers=H).status_code, 200)
        # the password renews it
        with self.assertLogs("sanguity.security", "WARNING") as logged:
            self.assertEqual(ada.post("/api/me/reauth", json={"secret": "not my password at all"}, headers=H).status_code, 403)
        self.assertIn('"event":"reauth.fail"', logged.output[0])
        r = ada.post("/api/me/reauth", json={"secret": "ada keeps a long passphrase"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertAlmostEqual(r.json()["until"], dbm.now() + config.REAUTH_MINUTES * 60, delta=5)
        r = ada.patch(f"/api/users/{ivo_id}", json={"reset_password": True}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("temporary_password", r.json())
        # so does a code from the app, once
        stale()
        c = code(secret, 1)
        self.assertEqual(ada.post("/api/me/reauth", json={"secret": "000000"}, headers=H).status_code, 403)
        self.assertEqual(ada.post("/api/me/reauth", json={"secret": c}, headers=H).status_code, 200)
        self.assertEqual(ada.patch(f"/api/users/{ivo_id}", json={"is_admin": True}, headers=H).status_code, 200)
        self.assertEqual(ada.patch(f"/api/users/{ivo_id}", json={"is_admin": False}, headers=H).status_code, 200)   # removing: no check
        stale()
        self.assertEqual(ada.post("/api/me/reauth", json={"secret": c}, headers=H).status_code, 403)   # already used
        # and a passkey of the account's own
        key = SoftKey()
        self.add_passkey(ada, "ada keeps a long passphrase", key)
        stale()
        o = ada.post("/api/me/reauth/passkey/options", headers=O).json()
        self.assertEqual(ada.post("/api/me/reauth/passkey", json={"state": o["state"], "credential": SoftKey().get(o["options"])},
                                  headers=O).status_code, 401)   # a passkey Sanguity doesn't know
        # it must verify the person (#930): the options ask for it, and a bare tap (user verification clear) isn't taken
        o = ada.post("/api/me/reauth/passkey/options", headers=O).json()
        self.assertEqual(o["options"]["userVerification"], "required")
        r = ada.post("/api/me/reauth/passkey", json={"state": o["state"], "credential": key.get(o["options"], verified=False)}, headers=O)
        self.assertEqual(r.status_code, 401, r.text)
        self.assertIn("check it's you", r.json()["detail"])
        self.assertEqual(ada.post("/api/users", json={"username": "new-admin-930", "is_admin": True}, headers=H).json()["detail"], "reauth_required")
        # nor with a sign-in's challenge, which after the password asks for it only where the passkey can
        t = client().post("/api/login", json={"username": "ada", "password": "ada keeps a long passphrase"}, headers=H).json()["ticket"]
        o = client().post("/api/login/passkey/options", json={"ticket": t}, headers=O).json()
        self.assertEqual(o["options"]["userVerification"], "preferred")
        r = ada.post("/api/me/reauth/passkey", json={"state": o["state"], "credential": key.get(o["options"], verified=False)}, headers=O)
        self.assertEqual(r.status_code, 401, r.text)
        self.assertEqual(ada.post("/api/users", json={"username": "new-admin-930", "is_admin": True}, headers=H).json()["detail"], "reauth_required")
        o = ada.post("/api/me/reauth/passkey/options", headers=O).json()
        r = ada.post("/api/me/reauth/passkey", json={"state": o["state"], "credential": key.get(o["options"])}, headers=O)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(ada.post("/api/users", json={"username": "new-admin", "is_admin": True}, headers=H).status_code, 200)
        # someone else's passkey doesn't renew this session
        stale()
        o = ada.post("/api/me/reauth/passkey/options", headers=O).json()
        self.assertEqual(ada.post("/api/me/reauth/passkey", json={"state": o["state"], "credential": ivo_key.get(o["options"])},
                                  headers=O).status_code, 401)
        self.assertEqual(ada.patch(f"/api/users/{ivo_id}", json={"disable_2fa": True}, headers=H).json()["detail"], "reauth_required")
        # a session from before the fresh check existed asks too; a new sign-in doesn't
        db.execute("UPDATE sessions SET verified_at=NULL WHERE user_id=?", (ada_id,))
        self.assertEqual(ada.patch(f"/api/users/{ivo_id}", json={"disable_2fa": True}, headers=H).json()["detail"], "reauth_required")
        c2 = client()
        t = c2.post("/api/login", json={"username": "ada", "password": "ada keeps a long passphrase"}, headers=H).json()["ticket"]
        o = c2.post("/api/login/passkey/options", json={"ticket": t}, headers=O).json()
        self.assertEqual(c2.post("/api/login/passkey", json={"state": o["state"], "credential": key.get(o["options"]), "ticket": t},
                                 headers=O).status_code, 200)
        self.assertEqual(c2.patch(f"/api/users/{ivo_id}", json={"disable_2fa": True}, headers=H).status_code, 200)
        # the command line stays the way back in: no second factor, no fresh check
        manage.run(["disable-2fa", "ada"])
        self.assertFalse(accounts.has_second_factor(db, db.execute("SELECT * FROM users WHERE id=?", (ada_id,)).fetchone()))
        for name in ("ada", "ivo", "new-admin"):   # leave the others' tests as they were
            db.execute("UPDATE users SET is_admin=0 WHERE username=?", (name,))
        db.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE username IN ('ada', 'ivo', 'new-admin'))")
        db.close()

    def test_admin_ways_into_records_need_the_fresh_check(self):   # #590: each gives an admin, or whoever they name, a profile
        from server import accounts, config, profiles as prof
        db = dbm.connect()
        ids = {}
        for name in ("wes", "zia"):
            row = db.execute("SELECT id FROM users WHERE username=?", (name,)).fetchone()
            ids[name] = row[0] if row else accounts.create(db, name, password=f"{name} keeps a long passphrase")[0]
        pid = prof.create_profile(db, "Wes's (590)", owner_id=ids["wes"])
        ben = self.login("ben")
        ben_id = db.execute("SELECT id FROM users WHERE username='ben'").fetchone()[0]
        stale = lambda: db.execute("UPDATE sessions SET verified_at=? WHERE user_id=?", (dbm.now() - config.REAUTH_MINUTES * 60 - 1, ben_id))
        refused = lambda r: self.assertEqual((r.status_code, r.json()["detail"]), (403, "reauth_required"), r.text)
        renew = lambda: self.assertEqual(ben.post("/api/me/reauth", json={"secret": PW["ben"]}, headers=H).status_code, 200)
        # disabling a profile's only owner (force): asked first as before, then the fresh check
        stale()
        self.assertEqual(ben.patch(f"/api/users/{ids['wes']}", json={"disabled": True}, headers=H).status_code, 409)
        refused(ben.patch(f"/api/users/{ids['wes']}", json={"disabled": True, "force": True}, headers=H))
        self.assertEqual(db.execute("SELECT disabled FROM users WHERE id=?", (ids["wes"],)).fetchone()[0], 0)
        renew()
        self.assertEqual(ben.patch(f"/api/users/{ids['wes']}", json={"disabled": True, "force": True}, headers=H).status_code, 200)
        # giving the profile left without an owner a new one: oneself, or an account one may have just made
        stale()
        for who in ("ben", "zia"):
            refused(ben.post(f"/api/ownerless/{pid}/owner", json={"username": who}, headers=H))
        self.assertEqual(db.execute("SELECT COUNT(*) FROM memberships WHERE profile_id=? AND user_id<>?", (pid, ids["wes"])).fetchone()[0], 0)
        self.assertEqual(ben.get(f"/api/profiles/{pid}/data").status_code, 404)
        renew()
        self.assertEqual(ben.post(f"/api/ownerless/{pid}/owner", json={"username": "zia"}, headers=H).status_code, 200)
        # removing an account and handing its profiles over
        stale()
        refused(ben.request("DELETE", f"/api/users/{ids['zia']}", json={"confirm_name": "zia", "hand_to": "ben"}, headers=H))
        self.assertIsNotNone(db.execute("SELECT 1 FROM users WHERE id=?", (ids["zia"],)).fetchone())
        self.assertIsNone(main.role_of(db, ben_id, pid))
        renew()
        r = ben.request("DELETE", f"/api/users/{ids['zia']}", json={"confirm_name": "zia", "hand_to": "ben"}, headers=H)
        self.assertEqual((r.status_code, r.json()["handed_over"]), (200, ["Wes's (590)"]))
        # the everyday ones still don't ask: enabling, and removing an account that hands nothing over
        stale()
        self.assertEqual(ben.patch(f"/api/users/{ids['wes']}", json={"disabled": False}, headers=H).status_code, 200)
        db.execute("DELETE FROM profiles WHERE id=?", (pid,))
        r = ben.request("DELETE", f"/api/users/{ids['wes']}", json={"confirm_name": "wes", "hand_to": "ben"}, headers=H)
        self.assertEqual((r.status_code, r.json()["handed_over"]), (200, []))
        db.close()

    def test_a_phone_secret_ends_with_its_makers_access(self):   # #929
        """The profile's phone upload secret, made by an editor, is taken away when they lose access to change the
        profile: removed, made a viewer, or their account disabled; the receiver is no longer given it, so their phone
        can't upload; the profile's activity says so, and Sharing's Remove can say it beforehand (members' phone_secret).
        A secret someone else made stays; one an older version left (its maker without access) goes at start-up."""
        from unittest import mock
        from server import accounts, crypto, watch_keys
        db = dbm.connect()
        ids = {}
        for name in ("tova", "ugo"):
            row = db.execute("SELECT id FROM users WHERE username=?", (name,)).fetchone()
            ids[name] = row[0] if row else accounts.create(db, name, password=f"{name} keeps a long passphrase")[0]
        tova, ugo = (self.login(n, f"{n} keeps a long passphrase") for n in ("tova", "ugo"))
        pid = tova.post("/api/profiles", json={"name": "Tova's (929)"}, headers=H).json()["id"]
        keys = os.path.join(TMP, "watch-keys-929", "keys.json")
        os.makedirs(os.path.dirname(keys), exist_ok=True)
        with mock.patch.object(main.config, "WATCH_KEYS", keys):
            listed = lambda: {x["profile"]: x["secret"] for x in json.loads(crypto.read_file(keys))["profiles"]}
            role = lambda r: self.assertEqual(tova.put(f"/api/profiles/{pid}/members", json={"username": "ugo", "role": r}, headers=H).status_code, 200)
            made = lambda c: c.post(f"/api/profiles/{pid}/watch/receiver/rotate", headers=H).json()["secret"]
            has = lambda: tova.get(f"/api/profiles/{pid}/watch/receiver").json()["has_secret"]
            # removed: the secret ugo made goes, and the receiver no longer takes it
            role("editor")
            secret = made(ugo)
            self.assertEqual(listed().get(pid), secret)
            self.assertEqual({m["username"]: m.get("phone_secret") for m in tova.get(f"/api/profiles/{pid}/members").json()},
                             {"tova": None, "ugo": True})
            self.assertTrue(next(m for m in ugo.get(f"/api/profiles/{pid}/members").json() if m["username"] == "ugo")["phone_secret"])
            self.assertEqual(tova.delete(f"/api/profiles/{pid}/members/{ids['ugo']}", headers=H).status_code, 200)
            self.assertFalse(has())
            self.assertNotIn(pid, listed())
            log = tova.get(f"/api/profiles/{pid}/activity?action=watch.secret").json()
            rows = log if isinstance(log, list) else log.get("entries", log.get("items", []))
            self.assertEqual([e["detail"].get("ended") for e in rows][:1], [True])
            self.assertEqual(rows[0]["detail"]["maker"], "ugo")
            # one the owner made stays when the editor goes
            role("editor")
            own = made(tova)
            self.assertEqual(tova.delete(f"/api/profiles/{pid}/members/{ids['ugo']}", headers=H).status_code, 200)
            self.assertEqual((has(), listed().get(pid)), (True, own))
            # made a viewer: it goes; given editor back, it stays gone
            role("editor")
            made(ugo)
            role("viewer")
            self.assertFalse(has())
            role("editor")
            self.assertFalse(has())
            # their account disabled: it goes
            made(ugo)
            ben = self.login("ben")
            self.assertEqual(ben.patch(f"/api/users/{ids['ugo']}", json={"disabled": True}, headers=H).status_code, 200)
            self.assertFalse(has())
            self.assertNotIn(pid, listed())
            self.assertEqual(ben.patch(f"/api/users/{ids['ugo']}", json={"disabled": False}, headers=H).status_code, 200)
            # one an older version left, made by someone without access now: gone at start-up (as main.lifespan does)
            with dbm.transaction(db):
                watch_keys.rotate(db, pid, ids["ugo"])
                db.execute("DELETE FROM memberships WHERE user_id=? AND profile_id=?", (ids["ugo"], pid))
            self.assertTrue(has())
            with dbm.transaction(db):
                accounts.end_lost_access(db)
            self.assertFalse(has())
            self.assertNotIn(pid, listed())
        db.execute("DELETE FROM profiles WHERE id=?", (pid,))
        db.close()

    def test_turning_on_two_factor_needs_the_password(self):   # #590: a session left open can't lock its person out
        import time
        from server import accounts, twofactor
        code = lambda secret: twofactor._code(secret, int(time.time() // twofactor.PERIOD))
        db = dbm.connect()
        if not db.execute("SELECT 1 FROM users WHERE username='gus'").fetchone():
            accounts.create(db, "gus", password="gus keeps a long passphrase")
        gus = self.login("gus", "gus keeps a long passphrase")
        self.assertEqual(gus.post("/api/me/2fa/start", headers=H).status_code, 422)
        with self.assertLogs("sanguity.security", "WARNING") as logged:
            self.assertEqual(gus.post("/api/me/2fa/start", json={"password": "not the password at all"}, headers=H).status_code, 403)
        self.assertIn('"event":"reauth.fail"', logged.output[0])
        secret = gus.post("/api/me/2fa/start", json={"password": "gus keeps a long passphrase"}, headers=H).json()["secret"]
        # the code alone doesn't turn it on, nor the code with a wrong password: a secret left pending is no way in
        self.assertEqual(gus.post("/api/me/2fa/confirm", json={"code": code(secret)}, headers=H).status_code, 422)
        self.assertEqual(gus.post("/api/me/2fa/confirm", json={"code": code(secret), "password": "not the password at all"},
                                  headers=H).status_code, 403)
        self.assertFalse(gus.get("/api/me/2fa").json()["on"])
        r = gus.post("/api/me/2fa/confirm", json={"code": code(secret), "password": "gus keeps a long passphrase"}, headers=H)
        self.assertEqual((r.status_code, len(r.json()["recovery_codes"])), (200, 10))
        self.assertTrue(gus.get("/api/me/2fa").json()["on"])
        db.close()

    def test_wrong_codes_since_the_last_right_one_stop_code_sign_in(self):   # #928
        """Someone who knows the password guesses the authenticator code, a new ticket every five codes: each wrong
        code counts towards the account's own limit, since its last right one (a right one starts it again), and at
        100 sign-in with a code stops, a right code refused too, until a recovery code is used or an admin resets
        two-factor sign-in. The person is told at sign-in and on Account, the admins in their notice, the accounts
        list and the install's log; the fresh check and new recovery codes take no code meanwhile."""
        import time
        from server import accounts, crypto, twofactor
        PWW = "wyn keeps a long passphrase"
        step = lambda: int(time.time() // twofactor.PERIOD)
        db = dbm.connect()
        if not db.execute("SELECT 1 FROM users WHERE username='wyn'").fetchone():
            accounts.create(db, "wyn", password=PWW)
        uid = db.execute("SELECT id FROM users WHERE username='wyn'").fetchone()[0]
        secret = twofactor.new_secret()
        db.execute("UPDATE users SET totp_secret=?, totp_last_step=0 WHERE id=?", (crypto.enc(secret), uid))
        recovery = accounts.new_recovery_codes(db, uid)
        fails = lambda: db.execute("SELECT totp_fails FROM users WHERE id=?", (uid,)).fetchone()[0]
        wrong = lambda: "%06d" % ((int(twofactor._code(secret, step())) + 500000) % 1000000)   # never the code of a step near now

        def ticket():
            r = client().post("/api/login", json={"username": "wyn", "password": PWW}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            return r.json()

        def tries(n):   # n wrong codes, a new ticket every five, the sign-in throttle's window gone by each time
            out = None
            for i in range(n):
                if i % 5 == 0:
                    sec.throttle.clear(*list(sec.throttle._fails))
                    t = ticket()["ticket"]
                out = client().post("/api/login/code", json={"ticket": t, "code": wrong()}, headers=H)
            return out

        def right(c=None):
            db.execute("UPDATE users SET totp_last_step=0 WHERE id=?", (uid,))   # (each code once: the same step again)
            sec.throttle.clear(*list(sec.throttle._fails))
            return (c or client()).post("/api/login/code", json={"ticket": ticket()["ticket"], "code": twofactor._code(secret, step())}, headers=H)

        keep = client()   # a device signed in before the guessing
        self.assertEqual(right(keep).status_code, 200)
        self.assertEqual(fails(), 0)
        self.assertEqual(tries(12).status_code, 401)
        self.assertEqual(fails(), 12)
        self.assertEqual(right().status_code, 200)   # a right code: counted from nought again
        self.assertEqual(fails(), 0)
        db.execute("UPDATE users SET totp_fails=96 WHERE id=?", (uid,))   # (as after 96 more wrong ones)
        self.assertEqual(tries(3).status_code, 401)
        self.assertFalse(ticket()["methods"].get("codes_stopped"))
        with self.assertLogs("sanguity.security", "WARNING"):
            r = tries(1)   # the 100th
        self.assertEqual(r.status_code, 401)
        self.assertIn("stopped", r.json()["detail"])
        self.assertEqual(fails(), 100)
        # stopped: a right code is refused, the sign-in says so at once, the fresh check takes no code either
        r = right()
        self.assertEqual(r.status_code, 401)
        self.assertIn("recovery code", r.json()["detail"])
        self.assertEqual(fails(), 100)
        self.assertTrue(ticket()["methods"]["codes_stopped"])
        self.assertTrue(keep.get("/api/me/2fa").json()["codes_stopped"])
        db.execute("UPDATE users SET totp_last_step=0 WHERE id=?", (uid,))
        r = keep.post("/api/me/reauth", json={"secret": twofactor._code(secret, step())}, headers=H)
        self.assertEqual(r.status_code, 403)
        self.assertIn("stopped", r.json()["detail"])
        self.assertEqual(keep.post("/api/me/2fa/recovery-codes", json={"code": twofactor._code(secret, step())}, headers=H).status_code, 409)
        self.assertEqual(keep.post("/api/me/reauth", json={"secret": PWW}, headers=H).status_code, 200)   # the password still does
        # the admins: in their notice, the accounts list and the install's log
        ben = self.login("ben")
        self.assertEqual([x["username"] for x in ben.get("/api/security/notice").json()["codes_stopped"]], ["wyn"])
        self.assertTrue(next(u for u in ben.get("/api/users").json() if u["username"] == "wyn")["codes_stopped"])
        self.assertEqual(db.execute("SELECT COUNT(*) FROM activity WHERE action='2fa.stopped' AND actor_id=?", (uid,)).fetchone()[0], 1)
        # a recovery code signs in and lets codes work again
        sec.throttle.clear(*list(sec.throttle._fails))
        r = client().post("/api/login/code", json={"ticket": ticket()["ticket"], "code": recovery[0]}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(fails(), 0)
        self.assertEqual(right().status_code, 200)
        self.assertEqual(ben.get("/api/security/notice").json()["codes_stopped"], [])
        # stopped again; an admin's reset of two-factor sign-in starts it afresh
        db.execute("UPDATE users SET totp_fails=99 WHERE id=?", (uid,))
        with self.assertLogs("sanguity.security", "WARNING"):
            self.assertIn("stopped", tries(1).json()["detail"])
        self.assertEqual(ben.patch(f"/api/users/{uid}", json={"disable_2fa": True}, headers=H).status_code, 200)
        self.assertEqual(fails(), 0)
        self.assertFalse(next(u for u in ben.get("/api/users").json() if u["username"] == "wyn")["codes_stopped"])
        db.close()

    def test_removing_a_passkey_needs_the_password_and_signs_out_elsewhere(self):   # #590
        from server import accounts
        db = dbm.connect()
        if not db.execute("SELECT 1 FROM users WHERE username='hal'").fetchone():
            accounts.create(db, "hal", password="hal keeps a long passphrase")
        hal = self.login("hal", "hal keeps a long passphrase")
        other = self.login("hal", "hal keeps a long passphrase")   # another device
        pk = self.add_passkey(hal, "hal keeps a long passphrase", SoftKey())
        gone = lambda: db.execute("SELECT COUNT(*) FROM passkeys WHERE id=?", (pk,)).fetchone()[0] == 0
        self.assertEqual(hal.delete(f"/api/me/passkeys/{pk}", headers=H).status_code, 422)
        with self.assertLogs("sanguity.security", "WARNING") as logged:
            r = hal.request("DELETE", f"/api/me/passkeys/{pk}", json={"password": "not the password at all"}, headers=H)
        self.assertEqual(r.status_code, 403)
        self.assertIn('"event":"reauth.fail"', logged.output[0])
        self.assertFalse(gone())
        self.assertEqual(other.get("/api/me").status_code, 200)
        r = hal.request("DELETE", f"/api/me/passkeys/{pk}", json={"password": "hal keeps a long passphrase"}, headers=H)
        self.assertEqual((r.status_code, r.json()["left"], r.json()["signed_out_elsewhere"]), (200, 0, 1), r.text)
        self.assertTrue(gone())
        self.assertEqual(other.get("/api/me").status_code, 401)   # every other session ends, as turning off two-factor does
        self.assertEqual(hal.get("/api/me").status_code, 200)     # this one stays
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

    def test_session_lifetime_follows_second_factor(self):
        from server import config, twofactor
        import time
        db = dbm.connect()
        if not db.execute("SELECT 1 FROM users WHERE username='lasting'").fetchone():
            db.execute("INSERT INTO users(username, pw_hash, created) VALUES('lasting', ?, 0)", (sec.hash_password(PW["ben"]),))
        who = "user_id=(SELECT id FROM users WHERE username='lasting')"
        try:
            # without a second factor: 12 hours from sign-in, in the session row and the cookie
            c = client()
            r = c.post("/api/login", json={"username": "lasting", "password": PW["ben"]}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertIn("Max-Age=43200", r.headers["set-cookie"])
            r = db.execute(f"SELECT created, expires FROM sessions WHERE {who}").fetchone()
            self.assertEqual(r["expires"] - r["created"], 12 * 3600)
            db.execute(f"UPDATE sessions SET created=created-11*3600 WHERE {who}")
            self.assertEqual(c.get("/api/me").status_code, 200)   # 11 hours in: still signed in
            db.execute(f"UPDATE sessions SET created=created-3600 WHERE {who}")
            self.assertEqual(c.get("/api/me").status_code, 401)   # 12 hours in: signed out, though used just now
            # with an authenticator app: 30 days
            secret = twofactor.new_secret()
            db.execute("UPDATE users SET totp_secret=? WHERE username='lasting'", (secret,))
            c = client()
            t = c.post("/api/login", json={"username": "lasting", "password": PW["ben"]}, headers=H).json()["ticket"]
            r = c.post("/api/login/code", json={"ticket": t, "code": twofactor._code(secret, int(time.time() // twofactor.PERIOD))}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertIn("Max-Age=2592000", r.headers["set-cookie"])
            r = db.execute(f"SELECT created, expires FROM sessions WHERE {who}").fetchone()
            self.assertEqual(r["expires"] - r["created"], 30 * 86400)
            db.execute(f"UPDATE sessions SET created=created-13*3600 WHERE {who}")
            self.assertEqual(c.get("/api/me").status_code, 200)   # 13 hours in: fine with a second factor
            self.assertEqual(len(c.get("/api/sessions").json()), 1)
            # the second factor gone without a sign-out (or a session from before this limit): it ends at 12 hours too
            db.execute("UPDATE users SET totp_secret=NULL, totp_last_step=0 WHERE username='lasting'")
            self.assertEqual(c.get("/api/me").status_code, 401)
            # SESSION_HOURS_WITHOUT_2FA=0: SESSION_DAYS for everyone, as before
            old = config.SESSION_HOURS_WITHOUT_2FA
            config.SESSION_HOURS_WITHOUT_2FA = 0
            try:
                r = client().post("/api/login", json={"username": "lasting", "password": PW["ben"]}, headers=H)
                self.assertIn("Max-Age=2592000", r.headers["set-cookie"])
                self.assertEqual(client().get("/api/setup").json()["session"], {"days": 30, "hours_without_2fa": 0, "idle_minutes": 0})
            finally:
                config.SESSION_HOURS_WITHOUT_2FA = old
            self.assertEqual(client().get("/api/setup").json()["session"]["hours_without_2fa"], 12)
        finally:
            db.execute("UPDATE users SET totp_secret=NULL, totp_last_step=0 WHERE username='lasting'")
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
            json.dump({"events": [{"id": i, "t": f"2026-01-0{i}T09:00:00", "k": "document", "title": t, "sub": ""}
                                  for i, t in ((1, "Visit summary"), (2, "Report"), (3, "Bare"))],
                       "details": {"1": {"type": "file", "url": "docs/visit summary.htm", "ext": "htm"},
                                   "2": {"type": "file", "url": "docs/report.pdf", "ext": "pdf"},
                                   "3": {"type": "file", "url": "docs/no-extension", "ext": "html"},
                                   "4": {"type": "file", "url": "docs/style.css", "ext": "css"}}}, f)   # 4: no event
        db = dbm.connect()
        pid = prof.create_profile(db, "HTML", owner_id=1)
        prof.import_dataset(db, pid, src)
        db.close()
        ben = self.login("ben")
        data = whole(ben, pid)
        det = {e["title"]: data["details"][str(e["id"])] for e in data["events"]}   # ids are the database's own
        self.assertEqual(len(data["details"]), 3)   # a detail no event shows isn't kept, nor its document
        self.assertEqual(db_count("SELECT COUNT(*) FROM documents WHERE profile_id=?", pid), 3)
        html_url, pdf_url = det["Visit summary"]["url"], det["Report"]["url"]
        self.assertTrue(html_url.endswith("/visit%20summary.htm"), html_url)
        self.assertEqual(ben.get("/" + html_url).headers["content-type"], "text/html; charset=utf-8")
        # (#221) an HTML record is sandboxed even in a tab of its own, so a refresh in it can't move the tab; a PDF and
        # the record's pictures and stylesheets aren't (a sandbox would stop a PDF rendering)
        csp = lambda url: ben.get(url).headers["content-security-policy"]   # noqa: E731
        self.assertTrue(csp("/" + html_url).endswith("; sandbox allow-same-origin"), csp("/" + html_url))
        self.assertIn("script-src 'none'", csp("/" + html_url))
        self.assertIn("sandbox allow-same-origin", csp("/" + det["Bare"]["url"]), "described as HTML")
        base = "/" + html_url.rsplit("/", 1)[0]
        for url in ("/" + pdf_url, base + "/style.css", base + "/visit_files/logo.png"):
            self.assertNotIn("sandbox", csp(url), url)
            self.assertIn("script-src 'none'", csp(url), url)
        self.assertEqual(ben.get(base + "/style.css").headers["content-type"], "text/css; charset=utf-8")
        self.assertEqual(ben.get(base + "/visit_files/logo.png").status_code, 200)
        self.assertEqual(ben.get(base + "/notes.json").status_code, 404)          # pictures and stylesheets only
        self.assertEqual(ben.get(base + "/../secret.png").status_code, 404)        # nothing outside its folder
        self.assertEqual(ben.get(base + "/..%2Fsecret.png").status_code, 404)
        self.assertEqual(ben.get("/" + pdf_url + "/style.css").status_code, 404)   # only HTML records have parts
        bare = det["Bare"]["url"]   # described as HTML in data.json though its name has no extension
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
        # over HTTPS the session cookie carries the __Host- prefix (Secure, Path=/, no Domain); it signs in, and
        # signing out ends it
        s = TestClient(main.app, base_url="https://testserver")
        s.cookies.set("sg_session", "left over from plain http")
        r = s.post("/api/login", json={"username": "cookie", "password": PW["ben"]}, headers=H)
        ck = r.headers["set-cookie"].lower()
        self.assertIn("__host-sg_session=", ck)
        self.assertNotIn("domain=", ck)
        self.assertRegex(ck, r'sg_session=""; ')   # the unprefixed one is cleared
        self.assertEqual(s.get("/api/me").status_code, 200)
        self.assertEqual(s.post("/api/logout", headers=H).status_code, 200)
        self.assertEqual(s.get("/api/me").status_code, 401)

    def test_security_log(self):
        """Sign-ins, refused second steps and refused access go to the app's log, without passwords or codes."""
        import time
        from server import accounts, twofactor
        db = dbm.connect()
        if not db.execute("SELECT 1 FROM users WHERE username='logged'").fetchone():
            accounts.create(db, "logged", password="logged keeps a long passphrase")
        db.close()
        with self.assertLogs("sanguity.security", "INFO") as cm:
            client().post("/api/login", json={"username": "logged", "password": "a wrong password typed"}, headers=H)
            client().post("/api/login", json={"username": "my secret passphrase", "password": "x"}, headers=H)
            c = self.login("logged", "logged keeps a long passphrase")
            c.get("/api/profiles/1/data")   # not shared with this account
            client().post("/api/login", json={"username": "x", "password": "y"})   # no X-Sanguity header
        out = "\n".join(cm.output)
        lines = [json.loads(o.split("security ", 1)[1]) for o in cm.output]
        events = [(e["event"], e.get("user"), e.get("reason")) for e in lines]
        self.assertIn(("login.fail", "logged", "password"), events)
        self.assertIn(("login.fail", None, "no such account"), events)
        self.assertIn(("login.ok", "logged", None), events)
        self.assertIn(("access.denied", "logged", None), events)
        self.assertIn(("csrf.refused", None, None), events)
        for secret in ("a wrong password typed", "my secret passphrase", "logged keeps a long passphrase"):
            self.assertNotIn(secret, out)
        # a code used a second time is refused and logged as reuse
        db = dbm.connect()
        secret = twofactor.new_secret()
        step = int(time.time() // twofactor.PERIOD)
        db.execute("UPDATE users SET totp_secret=?, totp_last_step=? WHERE username='logged'", (secret, step + 1))
        db.close()
        t = client().post("/api/login", json={"username": "logged", "password": "logged keeps a long passphrase"}, headers=H).json()["ticket"]
        with self.assertLogs("sanguity.security", "INFO") as cm:
            r = client().post("/api/login/code", json={"ticket": t, "code": twofactor._code(secret, step)}, headers=H)
        self.assertEqual(r.status_code, 401)
        self.assertIn('"reason":"code already used"', cm.output[-1])
        db = dbm.connect()
        db.execute("UPDATE users SET totp_secret=NULL, totp_last_step=0 WHERE username='logged'")
        db.close()

    def test_one_time_password_expires(self):
        from server import accounts, config
        ben = self.login("ben")
        r = ben.post("/api/users", json={"username": "late"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["temporary_days"], config.TEMP_PASSWORD_DAYS)
        temp = r.json()["temporary_password"]
        db = dbm.connect()
        db.execute("UPDATE users SET temp_pw_expires=? WHERE username='late'", (dbm.now() - 1,))
        r = client().post("/api/login", json={"username": "late", "password": temp}, headers=H)
        self.assertEqual(r.status_code, 401)
        self.assertIn("expired", r.json()["detail"])
        # an admin's reset gives a fresh one, which works and is cleared once replaced
        uid = db.execute("SELECT id FROM users WHERE username='late'").fetchone()[0]
        r = ben.patch(f"/api/users/{uid}", json={"reset_password": True}, headers=H)
        temp = r.json()["temporary_password"]
        c = self.login("late", temp)
        self.assertEqual(c.post("/api/me/password", json={"current": temp, "new": "late but a long passphrase"}, headers=H).status_code, 200)
        self.assertIsNone(db.execute("SELECT temp_pw_expires FROM users WHERE id=?", (uid,)).fetchone()[0])
        db.execute("UPDATE users SET must_change_pw=1, temp_pw_expires=NULL WHERE id=?", (uid,))   # their own password,
        self.login("late", "late but a long passphrase")   # to be replaced (e.g. a second factor went): no expiry
        db.close()
        self.assertTrue(accounts.temp_password_expired({"temp_pw_expires": 1}))

    def test_api_answers_download_and_pages_run_no_inline_script(self):
        r = self.login("ben").get("/api/me")
        self.assertEqual(r.headers["content-disposition"], 'attachment; filename="api.json"')
        # nginx sends the same CSP as the dev server (main.PAGE_CSP), and it allows no inline script
        import re
        conf = open(os.path.join(ROOT, "nginx.conf"), encoding="utf-8").read()
        csp = re.search(r'add_header Content-Security-Policy "([^"]+)" always;', conf).group(1)
        self.assertEqual(csp, main.PAGE_CSP)
        script = next(d for d in csp.split(";") if d.strip().startswith("script-src"))
        self.assertNotIn("unsafe", script)
        for page in ("index", "login", "setup", "account", "import"):
            html = open(os.path.join(ROOT, "www", page + ".html"), encoding="utf-8").read()
            self.assertNotRegex(html, r"<script(?![^>]*\bsrc=)", page)
            self.assertNotRegex(html, r"\son[a-z]+\s*=", page)

    def test_import_refuses_an_oversized_zip(self):
        import io, zipfile
        from server import export
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("data.json", '{"events": []}' + " " * 5000)
        old = export.MAX_BYTES
        export.MAX_BYTES = 1000
        try:
            db = dbm.connect()
            with self.assertRaisesRegex(ValueError, "too big"):
                export.import_archive(db, 1, io.BytesIO(buf.getvalue()))
            db.close()
        finally:
            export.MAX_BYTES = old

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
        from server import atrest, config, crypto, manage, records as rec
        pepper = sec._pepper
        files = [p for p in glob.glob(os.path.join(TMP, "profiles", "**", "*"), recursive=True) if os.path.isfile(p)]
        for p in files:   # nothing Sanguity keeps is readable from the disk alone
            with open(p, "rb") as f:
                self.assertEqual(f.read(4), crypto.MAGIC, p)
        db = dbm.connect()
        for table, col in (("documents", "path"), ("activity", "detail"), ("imports", "meta"), ("events", "body"),
                           ("events", "detail"), ("events", "cost"), ("series", "body"), ("results", "body")):
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
        rec.forget()   # read the rows again, not what the last request put together
        self.assertEqual(same_records(ben.get("/api/profiles/1/data").json()), same_records(records))   # old data still opens with the old line
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
        rec.forget()
        self.assertEqual(same_records(ben.get("/api/profiles/1/data").json()), same_records(records))   # rebuilt: new ids
        doc = db.execute("SELECT id FROM documents WHERE profile_id=1 LIMIT 1").fetchone()[0]
        self.assertEqual(ben.get(f"/api/profiles/1/documents/{doc}").status_code, 200)
        # the pepper was re-encrypted with the new key, not replaced: after a restart, passwords still work
        stored = dbm.meta_get(db, "pepper")
        self.assertEqual(base64.b64decode(stored[len(crypto.FIELD):])[:4], new)
        self.assertEqual(atrest.load_pepper(db), pepper)
        self.login("ben")
        db.close()

    def test_records_are_rows(self):   # issue #17
        from server import crypto, profiles as prof, records as rec
        src = os.path.join(TMP, "rows-src")
        os.makedirs(os.path.join(src, "docs"), exist_ok=True)
        with open(os.path.join(src, "docs", "scan.pdf"), "wb") as f:
            f.write(b"%PDF-1.4 scan")
        data = {"built": "2026-01-02T03:04:05+00:00", "source": "rows test",
                "events": [{"id": 50, "t": "2026-01-01T08:00:00", "k": "labitem", "title": "CBC", "sub": "", "hidden": 1},
                           {"id": 51, "t": "2026-01-01T08:00:00", "k": "lab", "title": "1 lab result", "sub": "CBC"},
                           {"id": 60, "t": "2026-01-03T09:00:00", "k": "claim", "title": "Office visit", "sub": "Plan", "abn": 1},
                           {"id": 70, "t": "2026-01-04T10:00:00", "k": "document", "title": "Scan", "sub": ""}],
                "details": {"50": {"type": "result", "order": "CBC", "rows": [["Hemoglobin", "14.8", "g/dL", "13.5 - 17.5", ""]]},
                            "51": {"type": "labday", "ids": [50, 99]},   # 99: not an event, so not listed
                            "60": {"type": "claim", "billed": 120.0, "owe": 20.0},
                            "70": {"type": "file", "url": "docs/scan.pdf", "ext": "pdf"},
                            "80": {"type": "file", "url": "docs/scan.pdf", "ext": "pdf"}},   # no event: dropped
                "costs": [{"id": 60, "d": "2026-01-03", "billed": 120.0, "owe": 20.0, "title": "Office visit"},
                          {"id": 61, "d": "2026-01-05", "billed": 5.0, "owe": 5.0, "title": "No event of its own"}],
                "labs": [{"name": "Hemoglobin", "unit": "g/dL", "panel": "CBC", "n": 2, "loinc": "718-7", "txt": [],
                          "pts": [["2025-12-01T08:00:00", 15.1, 13.5, 17.5, "", "15.1"],
                                  ["2026-01-01T08:00:00", 14.8, 13.5, 17.5, "", "14.8", "EXT"]]}],
                "upcoming": [{"t": "2026-02-01", "title": "Follow-up"}], "problems": [], "milestones": [{"date": "2025-06-01", "label": "Diagnosis"}],
                "outOfPocket": {"payments": [{"d": "2026-01-06", "amount": 10.0}], "exported": "2026-01-07"}, "clinicWeights": []}
        with open(os.path.join(src, "data.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)
        db = dbm.connect()
        pid = prof.create_profile(db, "Rows", owner_id=1)
        prof.import_dataset(db, pid, src)
        self.assertFalse(os.path.exists(os.path.join(prof.profile_dir(pid), "data.json")))   # rows, not a file
        counts = {t: db_count(f"SELECT COUNT(*) FROM {t} WHERE profile_id=?", pid) for t in ("imports", "events", "series", "results", "documents")}
        self.assertEqual(counts, {"imports": 1, "events": 4, "series": 1, "results": 2, "documents": 1})
        iid = db.execute("SELECT id FROM imports WHERE profile_id=?", (pid,)).fetchone()[0]
        for t in ("events", "series", "results", "documents"):   # every row belongs to the import that brought it
            self.assertEqual(db_count(f"SELECT COUNT(*) FROM {t} WHERE profile_id=? AND import_id IS NOT ?", pid, iid), 0, t)
        ben = self.login("ben")
        got = whole(ben, pid)
        ids = [e["id"] for e in got["events"]]
        lab, claim, doc = ids[1], ids[2], ids[3]
        self.assertEqual(got["details"][str(lab)]["ids"], [ids[0]])
        self.assertEqual(got["costs"][0]["id"], claim)   # in the order they came
        self.assertEqual(got["costs"][1], {**data["costs"][1], "id": None})   # kept for the Costs tab
        self.assertNotIn("80", got["details"])
        url = got["details"][str(doc)]["url"]
        self.assertRegex(url, rf"^api/profiles/{pid}/documents/[^/]+$")
        self.assertEqual(ben.get("/" + url).content, b"%PDF-1.4 scan")
        expect = json.loads(json.dumps(data))
        del expect["details"]["80"]
        expect["details"]["51"]["ids"] = [50]
        expect["details"]["70"]["url"] = url
        expect["costs"][1]["id"] = None
        self.assertEqual(same_records(got), same_records(expect))
        # #217: the page's data has a lab day's list of results, nothing else of details; the rest come when a record
        # is opened, any of the ids asked for that have details, with the records' revision
        page = ben.get(f"/api/profiles/{pid}/data")
        self.assertEqual(page.json()["details"], {str(lab): {"type": "labday", "ids": [ids[0]]}})
        r = ben.get(f"/api/profiles/{pid}/details", params={"ids": f"{ids[0]},{claim},{claim},{doc},999999"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["details"], {k: got["details"][k] for k in (str(ids[0]), str(claim), str(doc))})
        self.assertEqual(r.headers["x-records-rev"], page.headers["x-records-rev"])
        for bad in ("", "1,x", ",".join(map(str, range(rec.DETAILS_MAX + 1)))):
            self.assertEqual(ben.get(f"/api/profiles/{pid}/details", params={"ids": bad}).status_code, 400, bad[:20])
        self.assertEqual(ben.get(f"/api/profiles/{pid + 1000}/details", params={"ids": "1"}).status_code, 404)
        # replacing the records (server.manage import) leaves nothing of the old import behind
        data["events"], data["details"], data["costs"], data["labs"] = data["events"][:1], {}, [], []
        with open(os.path.join(src, "data.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)
        prof.import_dataset(db, pid, src)
        counts = {t: db_count(f"SELECT COUNT(*) FROM {t} WHERE profile_id=?", pid) for t in ("imports", "events", "series", "results", "documents")}
        self.assertEqual(counts, {"imports": 1, "events": 1, "series": 0, "results": 0, "documents": 0})
        self.assertEqual(len(ben.get(f"/api/profiles/{pid}/data").json()["events"]), 1)   # not the cached copy
        self.assertNotEqual(ben.get(f"/api/profiles/{pid}/data").headers["x-records-rev"], page.headers["x-records-rev"])   # #217
        self.assertEqual(ben.get(f"/api/profiles/{pid}/details", params={"ids": str(claim)}).json(), {"details": {}})   # gone with them
        # several imports: lists are joined, events interleaved by time, single values from the newest
        with dbm.transaction(db):
            rec.store(db, pid, {"source": "second", "events": [{"id": 1, "t": "2025-01-01T00:00:00", "k": "visit", "title": "Earlier"}],
                                "upcoming": [{"t": "2026-03-01", "title": "Another"}], "outOfPocket": {"payments": [{"d": "2026-02-01", "amount": 1.0}]}}, "dataset")
        both = ben.get(f"/api/profiles/{pid}/data").json()
        self.assertEqual([e["title"] for e in both["events"]], ["Earlier", "CBC"])
        self.assertEqual((both["source"], len(both["upcoming"]), len(both["outOfPocket"]["payments"]), both["outOfPocket"]["exported"]),
                         ("second", 2, 2, "2026-01-07"))
        # a profile that takes a deleted one's id doesn't see the deleted one's records
        ben.request("DELETE", f"/api/profiles/{pid}", json={"confirm_name": "Rows"}, headers=H)
        again = prof.create_profile(db, "Rows again", owner_id=1)
        if again == pid:
            self.assertEqual(ben.get(f"/api/profiles/{again}/data").status_code, 204)
        ben.request("DELETE", f"/api/profiles/{again}", json={"confirm_name": "Rows again"}, headers=H)
        db.close()

    def test_build_is_served_as_built(self):   # issue #17: the demo build, through the rows, as the page always read it
        from server import crypto, profiles as prof
        ben = self.login("ben")
        db = dbm.connect()   # closed, not left to the garbage collector: an open one keeps sanguity.db locked on Windows
        prof.sync_build(db)
        db.close()
        got = whole(ben, 1)
        built = json.loads(crypto.read_file(os.path.join(TMP, "build", "data.json")))
        from server import encounters   # as the records load: chart activity, plain visit titles, reminders (#236)
        self.assertEqual(encounters.tidy(built["events"], built["details"]), 3)   # the demo's chart review, call and billed visit
        for e in built["events"]:
            e.pop("orig", None)
        for d in (got, built):
            for v in d["details"].values():
                if v.get("type") == "file":
                    v["url"] = "-"
        self.assertEqual(same_records(got), same_records(built))
        events = {e["id"]: e for e in got["events"]}
        for c in got["costs"]:
            self.assertEqual(events[c["id"]]["k"] in ("claim", "rx"), True, c)
        for v in got["details"].values():
            if v.get("type") == "labday":
                self.assertTrue(v["ids"] and all(events[i]["k"] == "labitem" for i in v["ids"]), v)

    def test_existing_data_json_moves_into_the_database(self):   # issue #17: an install from before schema version 10
        from server import crypto, profiles as prof
        db = dbm.connect()
        pid = prof.create_profile(db, "Old install", owner_id=1)
        os.makedirs(os.path.join(prof.profile_dir(pid), "imported"), exist_ok=True)
        pdf = os.path.join(prof.profile_dir(pid), "imported", "letter.pdf")
        crypto.write_file(pdf, b"%PDF-1.4 old")
        db.execute("INSERT INTO documents(id, profile_id, path, ext) VALUES('olddoc', ?, ?, 'pdf')", (pid, crypto.enc(pdf)))
        url = f"api/profiles/{pid}/documents/olddoc"   # links were rewritten when the file was written
        old = {"source": "before v10", "events": [{"id": 1, "t": "2024-05-01T00:00:00", "k": "letter", "title": "Letter"}],
               "details": {"1": {"type": "file", "url": url, "ext": "pdf"}}, "labs": [], "costs": [], "milestones": []}
        crypto.write_file(prof.data_path(pid), json.dumps(old).encode())
        broken = prof.create_profile(db, "Broken", owner_id=1)
        crypto.write_file(prof.data_path(broken), b"[1]")
        self.assertEqual(prof.migrate_files(db), 1)   # the broken one is left for someone to look at
        self.assertTrue(os.path.isfile(prof.data_path(broken)))
        self.assertFalse(os.path.exists(prof.data_path(pid)))
        self.assertFalse(os.path.exists(os.path.join(prof.profile_dir(pid), prof.MIGRATED)), "not kept: undone, its records would stay in it (#934)")
        self.assertEqual(db.execute("SELECT origin FROM imports WHERE profile_id=?", (pid,)).fetchone()[0], "dataset")
        self.assertIsNotNone(db.execute("SELECT import_id FROM documents WHERE id='olddoc'").fetchone()[0])
        ben = self.login("ben")
        got = whole(ben, pid)
        self.assertEqual(same_records(got), same_records(old))
        self.assertEqual(ben.get("/" + url).content, b"%PDF-1.4 old")
        self.assertEqual(prof.migrate_files(db), 0)   # once
        for p in (pid, broken):
            ben.request("DELETE", f"/api/profiles/{p}", json={"confirm_name": "Old install" if p == pid else "Broken"}, headers=H)
        db.close()

    def test_encryption_format_is_unchanged(self):   # written by the pycryptodome version, read by this one
        from server import crypto
        with other_key(make=False):
            with open(crypto.keyring.path, "w", encoding="ascii") as f:
                f.write("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=\n")   # bytes 0..31
            crypto.keyring.reset()
            field = "enc:1:Yw3NKRgA52TPXHy3iamfyRSp+vy/X3R9Ut//yB6JzcOV06RNHHVtMNf66qp5S5/bVDMUcQ=="
            self.assertEqual(crypto.dec(field), "Hemoglobin 14.8 g/dL")
            blob = base64.b64decode("U0dFMWMNzSkvYFQIP0/xBVZKiNAaqqwJyeQ+fD4t2h4bdAJHuWCDsh5guvTzj+5JBA==")
            self.assertEqual(crypto.unseal(blob), b'{"events":[]}')
            self.assertEqual(crypto.unseal(crypto.seal(b"round trip")), b"round trip")
            for bad in (blob[:-1] + bytes([blob[-1] ^ 1]), blob[:20]):   # a changed tag, or cut short
                with self.assertRaises(crypto.KeyError_):
                    crypto.unseal(bad)

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
        for o in ("http://127.0.0.1:8730", "http://[::1]:8730"):   # this computer's own: its name works without HTTPS (#531)
            self.assertIn("Open Sanguity at http://localhost:8730 to use one.", pia.get("/api/me/passkeys", params={"origin": o}).json()["problem"])
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
        self.assertEqual((first["two_factor"], first["methods"]), (True, {"code": False, "passkey": True, "sites": ["health.example.com"]}))
        # #535: at this computer's own address, Account points to where the passkeys were made, not to localhost
        self.assertIn("Open Sanguity at https://health.example.com to use one.",
                      pia.get("/api/me/passkeys", params={"origin": "http://127.0.0.1:8730"}).json()["problem"])
        self.assertEqual(c.get("/api/me").status_code, 401)
        self.assertEqual(c.post("/api/login/code", json={"ticket": first["ticket"], "code": "123456"}, headers=H).status_code, 401)
        # #540: at a name none was made at, the refusal says where they were, and no code, which pia hasn't got
        from server import passkeys
        with self.assertRaises(passkeys.PasskeyError) as no:
            passkeys.authentication_options(db, "http://localhost:8730", pia_id)
        self.assertEqual((no.exception.status, str(no.exception)),
                         (409, "None of your passkeys was made at this address. Open Sanguity at https://health.example.com to use one."))
        # #549: on plain HTTP, and at an IP that isn't this computer's, the refusal names where they were made too ("its
        # https:// address", "by its name"); localhost in words off this computer (it was https://localhost to a remote page)
        for o, want in (("http://sanguity.test", "Passkeys need HTTPS. Open Sanguity at https://health.example.com to use one."),
                        ("https://172.16.26.2", "Passkeys don't work at an IP address. Open Sanguity at https://health.example.com to use one.")):
            self.assertEqual(pia.get("/api/me/passkeys", params={"origin": o}).json()["problem"], want)
            with self.assertRaises(passkeys.PasskeyError) as no:
                passkeys.authentication_options(db, o, pia_id)
            self.assertEqual(str(no.exception), want)
        from urllib.parse import urlsplit as page
        self.assertEqual([passkeys.where("localhost", page(o)) for o in ("https://sanguity.test", "http://172.16.26.2:8730", "http://127.0.0.1:8730", "http://[::1]")],
                         ["localhost (on the computer it runs on)"] * 2 + ["http://localhost:8730", "http://localhost"])
        for o, want in (("http://sanguity.test", "Passkeys need HTTPS. Open Sanguity at localhost (on the computer it runs on) to use one."),
                        ("https://172.16.26.2", "Passkeys don't work at an IP address. Open Sanguity at localhost (on the computer it runs on) to use one.")):
            with self.assertRaises(passkeys.PasskeyError) as no:
                passkeys.site(o, ["localhost"])
            self.assertEqual(str(no.exception), want)
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
            r = pia.request("DELETE", f"/api/me/passkeys/{pk}", json={"password": "pia keeps a long passphrase"}, headers=H)
            self.assertEqual(r.status_code, 409)
            key2 = SoftKey()
            pk2 = add(pia, "pia", key2, "Laptop").json()["id"]
            self.assertEqual(pia.request("DELETE", f"/api/me/passkeys/{pk}", json={"password": "pia keeps a long passphrase"}, headers=H).json()["left"], 1)
            # signing in with the one left still works, and counts as two-factor
            c, r = alone(key2)
            self.assertEqual(c.get("/api/profiles/1/data").status_code, 404)   # past the two-factor gate: just not shared
        finally:
            dbm.meta_set(db, "require_2fa", None)
        self.assertEqual(quinn.request("DELETE", f"/api/me/passkeys/{pk2}", json={"password": "quinn keeps a long passphrase"}, headers=H).status_code, 404)   # not theirs
        self.assertEqual(pia.patch(f"/api/me/passkeys/{pk2}", json={"name": "  Work  laptop "}, headers=H).json()["name"], "Work laptop")
        # an admin's reset (lost phone) takes the passkeys too
        manage.run(["disable-2fa", "pia"])
        self.assertEqual(db.execute("SELECT COUNT(*) FROM passkeys WHERE user_id=?", (pia_id,)).fetchone()[0], 0)
        self.assertEqual(alone(key2)[1].status_code, 401)
        actions = {r[0] for r in db.execute("SELECT action FROM activity WHERE actor_id=?", (pia_id,))}
        self.assertTrue({"passkey.add", "passkey.remove"} <= actions)
        db.close()

    def test_watch_receiver_encrypts_at_rest(self):   # the real receiver, with the app's key
        import glob, gzip, hashlib, hmac, socket, sqlite3, time, urllib.request
        from server import config, crypto
        top = os.path.join(TMP, "watch-rx")
        os.makedirs(os.path.join(top, "raw", "2026", "11", "01"))
        os.makedirs(os.path.join(top, "public"))
        with open(os.path.join(TMP, "keys", "watch_secret"), "w") as f:
            f.write("test-watch-secret\n")
        env = {**os.environ, "DATA": top, "SECRET_FILE": os.path.join(TMP, "keys", "watch_secret"),
               "SANGUITY_KEY_FILE": config.KEY_FILE, "TZ_DEFAULT": "UTC"}
        receiver = os.path.join(ROOT, "watch", "receiver.py")

        def payload(day, kg, uuid):
            return json.dumps({"source": "health_connect", "timezone": "UTC", "timestamp": f"{day}T09:00:00Z",
                               "weight": [{"time": f"{day}T08:00:00Z", "kilograms": kg, "uuid": uuid}]}).encode()

        def weights():
            daily = json.loads(crypto.read_file(os.path.join(top, "public", "daily.json")))
            return dict(next((m["pts"] for m in daily["metrics"] if m["key"] == "weight"), []))

        lb = lambda kg: round(kg * 2.20462, 2)

        # an install from before encryption: a plaintext upload, an SQLite file in WAL mode, a plaintext summary
        with gzip.open(os.path.join(top, "raw", "2026", "11", "01", "080000000000-old.json.gz"), "wb") as f:
            f.write(payload("2026-11-01", 80.5, "w-old"))
        old = sqlite3.connect(os.path.join(TMP, "old-watch.db"))
        old.executescript("PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0; CREATE TABLE samples(type TEXT, uuid TEXT, t INTEGER, day TEXT, v REAL, "
                          "v2 REAL, src TEXT, PRIMARY KEY(type, uuid)); INSERT INTO samples VALUES('weight', 'w-db', 0, "
                          "'2026-10-31', 79.25, NULL, NULL);")   # only in the database (raw/ would have it too, really)
        with open(os.path.join(top, "public", "daily.json"), "w") as f:
            f.write('{"metrics": []}')

        def start():
            with socket.socket() as sk:
                sk.bind(("127.0.0.1", 0))
                port = sk.getsockname()[1]
            proc = subprocess.Popen([sys.executable, "-I", receiver], env={**env, "PORT": str(port)},
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(100):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1)
                    return proc, port
                except OSError:
                    time.sleep(0.1)
            proc.kill()
            self.fail("the receiver didn't start")

        def post(port, body):
            sig = "sha256=" + hmac.new(b"test-watch-secret", body, hashlib.sha256).hexdigest()
            r = urllib.request.Request(f"http://127.0.0.1:{port}/ingest", data=body, headers={"X-Signature": sig})
            self.assertEqual(urllib.request.urlopen(r, timeout=5).status, 200)

        def until(cond):
            for _ in range(200):
                if cond():
                    return
                time.sleep(0.1)
            self.fail("the receiver didn't write its summary")

        def all_sealed():
            files = [p for p in glob.glob(os.path.join(top, "**", "*"), recursive=True) if os.path.isfile(p)]
            self.assertTrue(files)
            for p in files:
                with open(p, "rb") as f:
                    self.assertEqual(f.read(8), crypto.MAGIC + crypto.current_key_id(), p)

        for ext in ("", "-wal"):   # copied while open, as after an unclean stop: the row is only in the -wal
            shutil.copyfile(os.path.join(TMP, "old-watch.db" + ext), os.path.join(top, "watch.db" + ext))
        old.close()
        proc, port = start()
        try:
            all_sealed()   # migrated as it started: the old WAL folded in, nothing left in plaintext
            self.assertFalse(os.path.exists(os.path.join(top, "watch.db-wal")))
            post(port, payload("2026-11-02", 81.234, "w-new"))
            until(lambda: "2026-11-02" in weights())
            self.assertEqual(weights(), {"2026-10-31": lb(79.25), "2026-11-02": lb(81.234)})   # the old database carried over
            all_sealed()
            # a crash before the next snapshot: the upload is in raw/, and folded in again on start
            post(port, payload("2026-11-03", 82.0, "w-late"))
        finally:
            proc.kill(); proc.wait()
        proc, port = start()
        try:
            self.assertEqual(weights(), {"2026-10-31": lb(79.25), "2026-11-02": lb(81.234), "2026-11-03": lb(82.0)})
        finally:
            proc.kill(); proc.wait()
        all_sealed()
        # --reprocess rebuilds from the sealed archive alone (and the old plaintext upload, now sealed)
        subprocess.run([sys.executable, "-I", receiver, "--reprocess"], env=env, check=True, stdout=subprocess.DEVNULL)
        self.assertEqual(weights(), {"2026-11-01": lb(80.5), "2026-11-02": lb(81.234), "2026-11-03": lb(82.0)})
        all_sealed()
        # the app shows the sealed summary
        db = dbm.connect()
        saved = db.execute("SELECT watch_file FROM profiles WHERE id=1").fetchone()[0]
        db.execute("UPDATE profiles SET watch_file=? WHERE id=1", (os.path.join(top, "public", "daily.json"),))
        db.commit()
        try:
            r = self.login("ben").get("/api/profiles/1/watch")
            self.assertEqual(r.status_code, 200)
            self.assertIn(["2026-11-03", lb(82.0)], next(m for m in r.json()["metrics"] if m["key"] == "weight")["pts"])
        finally:
            db.execute("UPDATE profiles SET watch_file=? WHERE id=1", (saved,))
            db.commit()
            db.close()

    def test_a_sessions_password_checks_are_limited_per_account(self):   # #931
        """A session left signed in guesses its person's password through the fresh check, a password change and the
        password turning on two-factor sign-in asks for, from a new address every five tries: after the account's 30
        free tries (as sign-in has), every address waits, the right password too; and the admins' notice counts them."""
        from server import accounts, alerts
        PWK = "kai keeps a long passphrase"
        db = dbm.connect()
        if not db.execute("SELECT 1 FROM users WHERE username='kai'").fetchone():
            accounts.create(db, "kai", password=PWK)
        dbm.meta_set(db, alerts.SEEN, str(dbm.now() - 1))   # (other tests fail sign-ins too)
        kai = self.login("kai", PWK)
        at = lambda n: TestClient(main.app, base_url="http://testserver", client=(f"198.51.100.{n}", 5000), cookies=kai.cookies)
        tries = (lambda g: ("/api/me/reauth", {"secret": g}), lambda g: ("/api/me/password", {"current": g, "new": g + " and more"}),
                 lambda g: ("/api/me/2fa/start", {"password": g}))
        wrong, waited = 0, None
        for n in range(1, 13):
            c = at(n)
            for _ in range(5):
                path, body = tries[wrong % 3](f"wrong guess number {wrong:04d} for kai")
                r = c.post(path, json=body, headers=H)
                if r.status_code == 429:
                    waited = r
                    break
                self.assertEqual(r.status_code, 403, r.text)
                wrong += 1
            if waited:
                break
        self.assertEqual(wrong, 30)   # the account's free tries, from any address
        self.assertEqual(at(99).post("/api/me/reauth", json={"secret": PWK}, headers=H).status_code, 429)
        n = self.login("ben").get("/api/security/notice").json()["notice"]
        self.assertGreaterEqual(n["checks"], 30)   # (a check another test just failed may be in the window too)
        self.assertEqual(next(a for a in n["accounts"] if a["username"] == "kai")["failures"], 30)
        sec.throttle.clear(*list(sec.throttle._fails))
        self.assertEqual(at(99).post("/api/me/reauth", json={"secret": PWK}, headers=H).status_code, 200)
        db.close()

    def test_failed_sign_in_notice(self):   # ASVS 8.1.4: admins see bursts of failed sign-ins
        from server import accounts, alerts
        db = dbm.connect()
        if not db.execute("SELECT 1 FROM users WHERE username='nora'").fetchone():
            accounts.create(db, "nora", password="nora keeps a long passphrase")
        nora_id = db.execute("SELECT id FROM users WHERE username='nora'").fetchone()[0]
        dbm.meta_set(db, alerts.SEEN, str(dbm.now() - 1))   # start from a clean slate (other tests fail sign-ins too)
        # failures are kept: the account when the name is real, never a typed name that isn't one
        before = db.execute("SELECT COUNT(*) FROM signin_failures").fetchone()[0]
        client().post("/api/login", json={"username": "nora", "password": "not it at all, nope"}, headers=H)
        client().post("/api/login", json={"username": "maybe-my-password", "password": "x"}, headers=H)
        rows = db.execute("SELECT user_id, step FROM signin_failures ORDER BY rowid DESC LIMIT 2").fetchall()
        self.assertEqual(db.execute("SELECT COUNT(*) FROM signin_failures").fetchone()[0], before + 2)
        self.assertEqual(sorted((r[0] or 0, r[1]) for r in rows), [(0, "password"), (nora_id, "password")])
        ben = self.login("ben")
        self.assertIsNone(ben.get("/api/security/notice").json()["notice"])   # two failures: nothing stands out
        for _ in range(9):
            alerts.record("203.0.113.9", None, "password")
        self.assertIsNone(ben.get("/api/security/notice").json()["notice"])   # 9 from one address: not yet
        alerts.record("203.0.113.9", nora_id, "password")
        n = ben.get("/api/security/notice").json()["notice"]
        self.assertEqual(n["addresses"][0], {**n["addresses"][0], "ip": "203.0.113.9", "failures": 10})
        self.assertIn("nora", [a["username"] for a in n["accounts"]])
        nora = self.login("nora", "nora keeps a long passphrase")
        self.assertEqual(nora.get("/api/security/notice").status_code, 403)   # admins only
        self.assertEqual(nora.post("/api/security/notice/dismiss", headers=H).status_code, 403)
        self.assertEqual(ben.post("/api/security/notice/dismiss", headers=H).status_code, 200)
        self.assertIsNone(ben.get("/api/security/notice").json()["notice"])
        self.assertEqual(ben.get("/api/activity").json()[0]["action"], "security.notice_dismissed")
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
            # after the backup: a profile, which restoring takes back, and an account, which it keeps (#917: records
            # only come back, today's access stays)
            db = dbm.connect()
            db.execute("INSERT INTO users(username, pw_hash, created) VALUES('after-backup', 'x', 0)")
            db.execute("INSERT INTO profiles(name, created) VALUES('after-backup', 0)")
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
        profiles = {r[0] for r in db.execute("SELECT name FROM profiles")}
        db.close()
        self.assertIn("ben", names)
        self.assertIn("after-backup", names, "today's accounts are kept (#917)")
        self.assertNotIn("after-backup", profiles, "the records are the backup's")
        self.assertEqual([n for _, n in m["access"]["gone"]], ["after-backup"])
        with client() as c:   # and the app starts on it
            self.assertEqual(c.post("/api/login", json={"username": "ben", "password": PW["ben"]}, headers=H).status_code, 200)
        self.assertEqual(good, open(path, "rb").read())   # verifying and restoring never change the archive
        self.assertEqual(len(backup.list_backups()), 1)   # the doctored copies don't count as backups


if __name__ == "__main__":
    suite = unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(Api),
                                unittest.defaultTestLoader.loadTestsFromTestCase(Backups)])   # Backups last
    sys.exit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
