#!/usr/bin/env python3
"""Settings → Install → General: Host (#1000). The public addresses (PUBLIC_ORIGINS) saved in the app apply at once,
without a restart, to each thing that reads them: the cross-site check, connected sources' return address, the phone
upload address, calendar feed links and Find your provider's return addresses. A change is guarded: what it does said
first (the source apps to register again, the phones with a secret), typed confirmation, a list this page's own
address wouldn't pass refused, a reset refused, then the fresh check. And General: Phone receiver (#1003): the upload
address (WATCH_UPLOAD_URL) saved in the app applies at once on Sources → Phone and watch, its change counting the
profiles whose phones need it. Fictional data only.

  python3 tests/test_general_host.py
"""
import contextlib, io, os, shutil, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-general-host-test-")
for name in ("PUBLIC_ORIGINS", "WATCH_UPLOAD_URL", "SANGUITY_SAVED_SETTINGS"):
    os.environ.pop(name, None)
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch-rx", "public", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  SANGUITY_WATCH_KEYS=os.path.join(TMP, "watch-rx", "keys", "receiver-secrets.json"), RECEIVER_PORT="8731",
                  SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"))
for d in ("records", "keys", "build", os.path.join("watch-rx", "keys"), os.path.join("watch-rx", "public")):
    os.makedirs(os.path.join(TMP, d), exist_ok=True)
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, config, db as dbm, main, profiles as prof   # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = "an admin of the fictional install"
KEY = "host.publicOrigins"
OLD, NEW = "https://health.example.test", "https://records.example.test"
# a reverse proxy that changes the Host header and sends no X-Forwarded-Host: only the list lets its pages in
BEHIND = {"Host": "app:8000"}


class Host(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with contextlib.redirect_stdout(io.StringIO()):
            cls.ctx = TestClient(main.app).__enter__()
        db = dbm.connect()
        uid = accounts.create(db, "ada", password=PW, is_admin=True)[0]
        cls.pid = prof.create_profile(db, "Robin (fictional)", owner_id=uid)
        db.execute("INSERT INTO smart_clients(name, vendor, issuer, client_id, auth, scopes, created) VALUES"
                   "('Fictional sandbox', 'other', 'https://fhir.example.test/r4', 'fictional-id', 'public', 'openid', 0)")
        db.close()
        cls.c = TestClient(main.app)
        r = cls.c.post("/api/login", json={"username": "ada", "password": PW}, headers=H)   # (signing in is the fresh check)
        assert r.status_code == 200, r.text
        r = cls.c.post(f"/api/profiles/{cls.pid}/watch/receiver/rotate", headers=H)   # a phone with a secret
        assert r.status_code == 200, r.text

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        shutil.rmtree(TMP, True)

    def put(self, value, confirm="change addresses", **headers):
        return self.c.put(f"/api/settings/install/{KEY}", json={"value": value, "confirm": confirm}, headers={**H, **headers})

    def effects(self, value, **headers):
        return self.c.post(f"/api/settings/install/{KEY}/effects", json={"value": value}, headers={**H, **headers})

    def set_to(self, value):
        r = self.put(value)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(config.setting("PUBLIC_ORIGINS"), tuple(value))

    def test_a_each_effect_applies_at_once(self):
        """Each thing that reads the public addresses follows a change made in the app, without a restart."""
        self.set_to([OLD])
        cross_site = lambda origin: self.effects([OLD], Origin=origin, **BEHIND).status_code   # noqa: E731
        returned = lambda: self.c.get("/api/connect/sources").json()["return_address"]   # noqa: E731
        upload = lambda: self.c.get(f"/api/profiles/{self.pid}/watch/receiver").json()["addresses"]   # noqa: E731
        calendar = lambda: self.c.post(f"/api/profiles/{self.pid}/calendar/rotate", headers=H).json()["base"]   # noqa: E731
        directory = lambda: [a["url"] for a in self.c.get("/api/directory").json()["return_addresses"]]   # noqa: E731
        self.assertEqual(self.c.post(f"/api/profiles/{self.pid}/calendar", json={}, headers=H).status_code, 200)
        before = (cross_site(OLD), cross_site(NEW), returned(), upload(), calendar(), directory())
        self.assertEqual(before, (200, 403, OLD + "/connect/return", ["http://health.example.test:8731/ingest"], OLD + "/",
                                  [OLD + "/connect/return", "http://testserver/connect/return"]))
        self.set_to([NEW])
        after = (cross_site(NEW), cross_site(OLD), returned(), upload(), calendar(), directory())
        self.assertEqual(after, (200, 403, NEW + "/connect/return", ["http://records.example.test:8731/ingest"], NEW + "/",
                                 [NEW + "/connect/return", "http://testserver/connect/return"]))

    def test_b_effects_said_first(self):
        """The effects route says what changes, with the counts: the source apps to register the new return address
        with their vendor, and the phones with a secret that need the new upload address; none when nothing changes."""
        self.set_to([OLD])
        e = self.effects(["HTTPS://Records.example.test:443/", "http://192.168.1.5:8730"]).json()
        self.assertEqual((e["value"], e["given_out"], e["return_address"], e["apps"], e["phones"], e["accepted"], e["confirm"]),
                         ([NEW, "http://192.168.1.5:8730"], [OLD, NEW], [OLD + "/connect/return", NEW + "/connect/return"], 1, 1,
                          True, "change addresses"))
        self.assertEqual(e["upload"], [["http://health.example.test:8731/ingest"],
                                       ["http://192.168.1.5:8731/ingest", "http://records.example.test:8731/ingest"]])
        same = self.effects([OLD, "http://192.168.1.5:8730"]).json()   # the address given out and the upload hosts' first stay
        self.assertEqual((same["apps"], same["phones"] > 0), (0, True))
        self.assertEqual(self.effects([OLD]).json()["phones"], 0)
        self.assertEqual(self.effects(["ftp://nope.example.test"]).status_code, 400)
        # the read-only rows: counts, passkey names, what this request carried, the server's own setup
        h = self.c.get("/api/settings/host?origin=" + OLD, headers={**BEHIND, "X-Forwarded-Proto": "https"}).json()
        self.assertEqual((h["apps"], h["phones"], h["passkeys"], h["request"]["host"], h["request"]["forwarded_host"],
                          h["request"]["forwarded_proto"], h["request"]["accepted_by"], h["port"], h["bind"]),
                         (1, 1, [], "app:8000", None, "https", "listed", "8000", "0.0.0.0"))
        self.assertEqual(self.c.get("/api/settings/host?origin=http://testserver").json()["request"]["accepted_by"], "host")

    def test_c_guarded(self):
        """Typed confirmation, asked as every install setting's (#998: 409 {code: confirm, confirm: {type, ...}}); a list
        this page's own address wouldn't pass refused outright, with nothing to confirm (behind a proxy that changes the
        Host, dropping the page's address would lock it out); a reset asked the same way; the environment's value
        locked."""
        self.set_to([OLD])
        for typed in (None, "change"):
            r = self.put([NEW], confirm=typed)
            self.assertEqual(r.status_code, 409, r.text)
            c = r.json()["detail"]["confirm"]
            self.assertEqual((r.json()["detail"]["code"], c["type"], c["count"], c["effects"]["given_out"]),
                             ("confirm", "change addresses", 2, [OLD, NEW]))
        r = self.put([NEW], Origin=OLD, **BEHIND)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("cross-site check", r.json()["detail"])   # (words alone: refused, nothing to confirm)
        self.assertFalse(self.effects([NEW], Origin=OLD, **BEHIND).json()["accepted"])
        self.assertEqual(config.setting("PUBLIC_ORIGINS"), (OLD,))
        self.assertEqual(self.put([OLD, NEW], Origin=OLD, **BEHIND).status_code, 200)   # with it kept: saved
        self.assertEqual(self.put([NEW], Origin="http://testserver").status_code, 200)   # a page the Host lets in: saved
        for r in (self.c.post("/api/settings/install/reset", json={"section": "host"}, headers=H),
                  self.c.delete(f"/api/profiles/{self.pid}/settings/{KEY}", headers=H)):
            self.assertEqual((r.status_code, r.json()["detail"]["confirm"]["type"]), (409, "change addresses"), r.text)
        self.assertEqual(config.setting("PUBLIC_ORIGINS"), (NEW,))
        r = self.c.request("DELETE", f"/api/profiles/{self.pid}/settings/{KEY}", json={"confirm": "change addresses"}, headers=H)
        self.assertEqual((r.status_code, config.setting("PUBLIC_ORIGINS")), (200, ()), r.text)   # a reset, confirmed
        self.set_to([NEW])
        self.set_to([])   # emptied, through the guard
        os.environ["PUBLIC_ORIGINS"] = OLD
        try:
            self.assertEqual(self.put([NEW]).status_code, 409)
        finally:
            del os.environ["PUBLIC_ORIGINS"]

    def test_d_fresh_check_after_the_guard(self):
        """The fresh check is asked for after the guard: a refused list is said before a password is asked for."""
        self.set_to([OLD])
        db = dbm.connect()
        db.execute("UPDATE sessions SET verified_at=0")
        db.close()
        self.assertEqual(self.put([NEW], confirm=None).json()["detail"]["code"], "confirm")
        self.assertEqual(self.put([NEW]).json()["detail"], "reauth_required")
        self.assertEqual(self.c.post("/api/me/reauth", json={"secret": PW}, headers=H).status_code, 200)
        self.assertEqual(self.put([NEW]).status_code, 200)

    def test_e_upload_address_applies_at_once(self):
        """#1003: the upload address saved in the app is the one Sources → Phone and watch gives, at once (with the
        answer that makes a secret too); its effects count the phones that need it; back to empty, the public address's
        host on RECEIVER_PORT again. A bad one is refused; the environment's is locked."""
        self.set_to([OLD])
        key, url = "phone.uploadUrl", "https://phones.example.test/ingest"
        upload = lambda: self.c.get(f"/api/profiles/{self.pid}/watch/receiver").json()["addresses"]   # noqa: E731
        put = lambda v: self.c.put(f"/api/settings/install/{key}", json={"value": v}, headers=H)   # noqa: E731
        self.assertEqual(upload(), ["http://health.example.test:8731/ingest"])
        e = self.c.post(f"/api/settings/install/{key}/effects", json={"value": url}, headers=H).json()
        self.assertEqual((e["value"], e["upload"], e["phones"]), (url, [["http://health.example.test:8731/ingest"], [url]], 1))
        self.assertEqual(self.c.get("/api/settings/phone").json()["phones"], 1)
        self.assertEqual(put(url).status_code, 200)
        self.assertEqual(upload(), [url])
        r = self.c.post(f"/api/profiles/{self.pid}/watch/receiver/rotate", headers=H)
        self.assertEqual(r.json()["addresses"], [url])
        self.assertEqual(self.c.get("/api/settings/phone").json()["upload"], [url])
        self.assertEqual(self.c.post(f"/api/settings/install/{key}/effects", json={"value": url}, headers=H).json()["phones"], 0)
        self.assertEqual(put("ftp://phones.example.test").status_code, 400)
        self.assertEqual(put("").status_code, 200)
        self.assertEqual(upload(), ["http://health.example.test:8731/ingest"])
        os.environ["WATCH_UPLOAD_URL"] = url
        try:
            self.assertEqual(put("https://other.example.test/ingest").status_code, 409)
            self.assertEqual(upload(), [url])
        finally:
            del os.environ["WATCH_UPLOAD_URL"]


if __name__ == "__main__":
    unittest.main(verbosity=2)
