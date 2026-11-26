#!/usr/bin/env python3
"""The Host check (#1064, DNS rebinding): once public addresses are set, a request sent to a name that is none of this
server's is refused, on every route, signed in or not; an IP address (v4 or v6, any port), localhost and the
network-only names still pass, and with none set any name does, as before. General → Host refuses a list that would
refuse the name its own page is open at. Fictional data only.

  python3 tests/test_host_check.py
"""
import contextlib, io, json, os, shutil, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-host-check-test-")
for name in ("PUBLIC_ORIGINS", "SANGUITY_SAVED_SETTINGS"):
    os.environ.pop(name, None)
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  SANGUITY_WATCH_KEYS=os.path.join(TMP, "watch", "keys.json"), SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"))
for d in ("records", "keys", "build", "watch"):
    os.makedirs(os.path.join(TMP, d), exist_ok=True)
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, config, db as dbm, main   # noqa: E402
import logging   # noqa: E402

H = {"X-Sanguity": "1"}
PW = "an admin of the fictional install"
PUBLIC = "https://health.example.test"
LISTED = ("health.example.test", "Health.Example.Test:443", "health.example.test.")
IPS = ("192.168.1.20:8730", "10.0.0.5", "[::1]:8000", "[fd00::20]:8730", "[::1]")
LOCAL = ("localhost", "localhost:8730", "127.0.0.1:8000", "nas.local:8730", "nas.lan", "nas.home:8443", "nas.home.arpa", "app.internal",
         "app:8000", "testserver", "dev.localhost")
UNLISTED = ("rebind.example.net", "rebind.example.net:8730", "evil.ts.net:8443", "health.example.test.evil.example")


class Logged(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


class HostCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with contextlib.redirect_stdout(io.StringIO()):
            cls.ctx = TestClient(main.app).__enter__()
        db = dbm.connect()
        accounts.create(db, "ada", password=PW, is_admin=True)
        db.close()
        cls.c = TestClient(main.app)
        r = cls.c.post("/api/login", json={"username": "ada", "password": PW}, headers=H)
        assert r.status_code == 200, r.text
        cls.log = Logged()
        sec = logging.getLogger("sanguity.security")
        sec.addHandler(cls.log)
        sec.propagate = False

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("PUBLIC_ORIGINS", None)
        cls.ctx.__exit__(None, None, None)
        shutil.rmtree(TMP, True)

    def tearDown(self):
        os.environ.pop("PUBLIC_ORIGINS", None)

    def login(self, host, **headers):
        """The unauthenticated route a rebinding page would guess passwords at: "in" when let in (a wrong password's 401,
        or 429 once the guesses are throttled), else the status."""
        r = self.c.post("/api/login", json={"username": "ada", "password": "a wrong guess"},
                           headers={**H, "Host": host, "Origin": "http://" + host, **headers})
        return "in" if r.status_code in (401, 429) else r.status_code

    def me(self, host, **headers):
        """A signed-in route."""
        return self.c.get("/api/me", headers={"Host": host, **headers}).status_code

    def test_a_none_set_any_name_passes(self):
        for host in LISTED + IPS + LOCAL + UNLISTED:
            self.assertEqual((host, self.login(host), self.me(host)), (host, "in", 200))

    def test_b_listed_ip_and_local_pass(self):
        os.environ["PUBLIC_ORIGINS"] = PUBLIC
        for host in LISTED + IPS + LOCAL:
            self.assertEqual((host, self.login(host), self.me(host)), (host, "in", 200))

    def test_c_unlisted_name_refused(self):
        os.environ["PUBLIC_ORIGINS"] = PUBLIC + ",http://192.168.1.20:8730"
        self.log.lines.clear()
        for host in UNLISTED:
            self.assertEqual((host, self.login(host), self.me(host)), (host, 400, 400))
        r = self.c.get("/api/me", headers={"Host": "rebind.example.net", "Cookie": "x=a-secret-of-theirs"})
        self.assertEqual(r.json()["detail"], f"This address isn't one of this server's. Use {PUBLIC}.")
        line = json.loads(self.log.lines[-1].split(" ", 1)[1])
        self.assertEqual({k: line[k] for k in ("event", "host", "path")},
                         {"event": "host.refused", "host": "rebind.example.net", "path": "/api/me"})
        self.assertNotIn("a-secret-of-theirs", self.log.lines[-1])
        # a page can add X-Forwarded-Host itself: a listed one doesn't let an unlisted Host in, nor the other way round
        self.assertEqual(self.me("rebind.example.net", **{"X-Forwarded-Host": "health.example.test"}), 400)
        self.assertEqual(self.me("192.168.1.20:8730", **{"X-Forwarded-Host": "rebind.example.net"}), 400)
        self.assertEqual(self.me("app:8000", **{"X-Forwarded-Host": "health.example.test"}), 200)   # behind a proxy

    def test_d_ts_net_listed_like_any_name(self):
        os.environ["PUBLIC_ORIGINS"] = "https://nas.tail1234.ts.net:8443"
        self.assertEqual((self.me("nas.tail1234.ts.net:8443"), self.me("other.tail1234.ts.net:8443")), (200, 400))

    def test_e_host_guard_refuses_a_list_without_this_page(self):
        """General → Host: a list that leaves out the name this page is open at is refused, as the cross-site one is."""
        page = {**H, "Host": "nas.example.org:8730", "Origin": "http://nas.example.org:8730"}
        e = self.c.post("/api/settings/install/host.publicOrigins/effects", json={"value": [PUBLIC]}, headers=page).json()
        self.assertEqual((e["accepted"], e["refused_host"]), (True, "nas.example.org"))
        r = self.c.put("/api/settings/install/host.publicOrigins", json={"value": [PUBLIC], "confirm": "change addresses"},
                       headers=page)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("nas.example.org", r.json()["detail"])
        self.assertEqual(config.setting("PUBLIC_ORIGINS"), ())
        e = self.c.post("/api/settings/install/host.publicOrigins/effects", json={"value": [PUBLIC, "http://nas.example.org:8730"]},
                        headers=page).json()
        self.assertIsNone(e["refused_host"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
