#!/usr/bin/env python3
"""Outside connections (#97, #59; server/net.py, server/outside.py): every request to another host goes through one
gateway, which refuses any host the install wasn't told to contact and records each one; the Admin page lists
them, with scopes and last contact; disconnecting stops all contact. Run under a socket-level guard
(tests/netguard.py), against the fake SMART on FHIR server and the local Health Link server. Fictional data only.

  python3 tests/test_outside.py
"""
import ast, http.client, json, os, re, sys, tempfile, time, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-outside-test-")
ORIGIN = "http://sanguity.test:8730"
RETURN = ORIGIN + "/connect/return"
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "no-build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"), PUBLIC_ORIGINS=ORIGIN, CONNECT_ALLOW_HTTP="1",
                  SANGUITY_SERVE_STATIC="1")
os.makedirs(os.path.join(TMP, "records"))
os.makedirs(os.path.join(TMP, "keys"))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import logging   # noqa: E402

from cryptography.hazmat.primitives.asymmetric import ec   # noqa: E402
from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, crypto, db as dbm, main, net, outside, profiles as prof, shl, smart, sync   # noqa: E402
from tests import netguard   # noqa: E402
from tests.fakefhir import FakeFHIR   # noqa: E402
import shl_server   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites", "lee": "maple tunnel brisk saddle"}
NETWORK_MODULES = {"urllib.request", "http.client", "requests", "socket", "httpx", "urllib3", "aiohttp", "asyncio"}
GATEWAY = os.path.join("server", "net.py")
# a maintainer's script, run by hand to rebuild the breached-password list; the server never imports it
NOT_THE_SERVER = {os.path.join("server", "passwords", "update.py")}


def client():
    return TestClient(main.app, base_url="http://testserver")


def setUpModule():
    netguard.install()


def tearDownModule():
    netguard.uninstall()
    netguard.check()


class Static(unittest.TestCase):
    """New code can't get around the gateway: no module in server/ but net.py imports a way to the network."""

    def test_only_the_gateway_imports_network_modules(self):
        found = []
        for folder, _, files in os.walk(os.path.join(ROOT, "server")):
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(folder, f)
                rel = os.path.relpath(path, ROOT)
                if rel in (GATEWAY, *NOT_THE_SERVER):
                    continue
                with open(path, encoding="utf-8") as fh:
                    src = fh.read()
                for node in ast.walk(ast.parse(src, rel)):
                    names = []
                    if isinstance(node, ast.Import):
                        names = [a.name for a in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                        names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
                    found += [f"{rel}:{node.lineno} imports {n}" for n in names
                              if n in NETWORK_MODULES or n.split(".")[0] in {"requests", "httpx", "urllib3", "aiohttp"}]
                # and not by name, around the import statement
                for m in re.finditer(r"(?:__import__|import_module)\(\s*['\"]([\w.]+)", src):
                    if m.group(1) in NETWORK_MODULES:
                        found.append(f"{rel} imports {m.group(1)} by name")
        self.assertEqual(found, [], "Outbound requests go through server/net.py only")

    def test_the_gateway_is_where_the_network_is(self):
        with open(os.path.join(ROOT, GATEWAY), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("import http.client, socket", src)


class Gateway(unittest.TestCase):
    """net.py on its own."""

    def test_unlisted_host_refused_before_anything_is_recorded_or_sent(self):
        seen = []
        p = net.Permit("Connected source: sync", {"fhir.example.org"}, lambda h, why: seen.append((h, why)))
        for url in ("https://other.example.org/x", "https://fhir.example.org.evil.example/x", "http://127.0.0.2:9/x",
                    "ftp://fhir.example.org/", "https:///nohost"):
            with self.assertRaises(net.Refused, msg=url):
                net.connection(p, url)
        with self.assertRaises(net.Refused):
            net.resolve(p, "https://other.example.org/")   # no lookup either: the guard saw none
        self.assertEqual(seen, [])
        self.assertEqual(netguard.take(), [])
        c = net.connection(p, "https://FHIR.example.org:443/api/Patient?x=secret-token")   # not connected yet
        c.close()
        net.connection(p, "https://fhir.example.org/again").close()
        self.assertEqual(seen, [("fhir.example.org", "Connected source: sync")])   # once, host only
        self.assertFalse(net.NOTHING.allows("anything.example.org"))

    def test_hosts_are_read_again_before_each_request(self):
        hosts = {"fhir.example.org"}
        p = net.Permit("sync", lambda: hosts)
        net.connection(p, "https://fhir.example.org/").close()
        hosts.clear()   # disconnected meanwhile
        with self.assertRaises(net.Refused):
            net.connection(p, "https://fhir.example.org/")

    def test_names_compare_the_way_they_are_listed(self):
        self.assertEqual(net.host_of("https://Records.Example.org:443/a"), "records.example.org")
        self.assertEqual(net.host_of("http://127.0.0.1:8080/a"), "127.0.0.1:8080")
        self.assertEqual(net.norm("x.example.org:443"), "x.example.org")
        self.assertTrue(net.Permit("t", {"x.example.org:443"}).allows("X.example.org"))

    def test_a_host_that_didnt_answer_is_said_in_plain_words(self):
        """#528: a failed test said "didn't answer (gaierror)", a Python exception's name"""
        import socket, ssl
        said = [net.why(e) for e in (socket.gaierror(-2, "Name or service not known"), TimeoutError(), ConnectionRefusedError(),
                                     ssl.SSLCertVerificationError(), ConnectionResetError(), http.client.RemoteDisconnected())]
        self.assertEqual(said, ["its name wasn't found (check the address)", "it took too long to answer", "it refused the connection",
                                "its HTTPS certificate isn't valid", "the connection broke off", "the connection broke off"])

    def test_the_guard_catches_code_that_goes_around_the_gateway(self):
        conn = http.client.HTTPConnection("203.0.113.9", 80, timeout=1)   # TEST-NET-3: never routed anyway
        with self.assertRaises(ConnectionRefusedError):
            conn.connect()
        conn.close()
        self.assertEqual(netguard.take(), ["203.0.113.9:80"])


class View(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.no_thread = mock.patch.object(sync, "start", lambda: None)
        cls.no_thread.start()
        cls.no_sleep = mock.patch.object(smart, "sleep", lambda s: None)
        cls.no_sleep.start()
        cls.srv = shl_server.Server().start()
        cls.srv.trust()
        cls.ctx = client().__enter__()
        db = dbm.connect()
        cls.uids = {n: accounts.create(db, n, is_admin=(n == "robin"), password=pw)[0] for n, pw in PW.items()}
        db.close()
        cls.robin, cls.kim, cls.lee = (cls.login(n) for n in ("robin", "kim", "lee"))
        db = dbm.connect()   # the admin's second factor, set once signed in (admin changes need one)
        db.execute("UPDATE users SET totp_secret=? WHERE id=?", (crypto.enc("JBSWY3DPEHPK3PXP"), cls.uids["robin"]))
        db.close()
        cls.issuer_key = ec.generate_private_key(ec.SECP256R1())
        jwks = json.dumps({"keys": [shl.jwk_of(cls.issuer_key.public_key(), "issuer-1")]}).encode()
        cls.srv.routes["/issuer/.well-known/jwks.json"] = lambda m, q, b: (200, "application/json", jwks)

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        cls.srv.stop()
        cls.no_thread.stop()
        cls.no_sleep.stop()
        shl.ALLOW_PRIVATE, shl.CA_FILE = False, None

    @staticmethod
    def login(name):
        c = client()
        r = c.post("/api/login", json={"username": name, "password": PW[name]}, headers=H)
        assert r.status_code == 200, r.text
        return c

    def setUp(self):
        self.fake = FakeFHIR(patients=["robin-example"]).start()
        self.addCleanup(self.fake.stop)
        self.own = f"127.0.0.1:{self.fake.port}"
        db = dbm.connect()
        # robin (admin) owns P1, kim views it, lee edits it; P2 is robin's alone
        self.pid = prof.create_profile(db, "Robin (fictional)", owner_id=self.uids["robin"])
        self.other = prof.create_profile(db, "Other (fictional)", owner_id=self.uids["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (self.uids["kim"], self.pid))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (self.uids["lee"], self.pid))
        db.close()

    # ---------------------------------------------------------------- helpers

    def source(self):
        cid = f"app-{self.fake.port}"
        self.fake.register_client(cid, [RETURN])
        r = self.robin.post("/api/connect/sources", json={"name": f"Fake clinic {self.fake.port}", "vendor": "epic",
                                                          "issuer": self.fake.fhir_base, "client_id": cid, "auth": "public"}, headers=H)
        self.assertEqual(r.status_code, 201, r.text)
        sid = r.json()["id"]
        self.assertTrue(self.robin.post(f"/api/connect/sources/{sid}/test", headers=H).json()["ok"])
        return sid

    def connect(self, sid):
        r = self.robin.post(f"/api/profiles/{self.pid}/connect/start", json={"source": sid}, headers=H)
        back = self.fake.authorize(r.json()["url"], patient="pat-1")
        r = self.robin.post("/api/connect/return", headers=H, json={"code": back.code, "state": back.state})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["connection"]

    def pull(self, cid):
        db = dbm.connect()
        try:
            return sync.pull(db, cid)
        finally:
            db.close()

    def view(self, c):
        r = c.get("/api/outside")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def mine(self, v, sid):
        return next((s for s in v["sources"] if s["id"] == sid), None)

    # ---------------------------------------------------------------- connected sources

    def test_connected_source_listed_with_hosts_scopes_and_last_contact(self):
        sid = self.source()
        cid = self.connect(sid)
        self.assertTrue(self.pull(cid)["ok"])
        v = self.view(self.robin)
        self.assertIn("Nothing else leaves this server", v["nothing_else"])
        s = self.mine(v, sid)
        self.assertEqual((s["vendor"], s["vendor_label"]), ("epic", "Epic (MyChart)"))   # as Set up a source names it (#528)
        self.assertEqual((s["hosts"]["issuer"], s["hosts"]["token"]), (self.own, self.own))
        self.assertIn(self.own, v["may_contact"])
        self.assertIn("patient/Observation", s["scopes_requested"])
        (c,) = s["connections"]
        self.assertEqual((c["id"], c["profile"], c["profile_name"], c["state"]), (cid, self.pid, "Robin (fictional)", "active"))
        self.assertTrue(c["scopes_granted"])
        self.assertIsNotNone(c["last_contact"])
        self.assertGreaterEqual(s["last_contact"], c["last_contact"])
        # everything the vendor saw came from a host the view lists, and the guard saw nothing else
        self.assertLessEqual(self.fake.hosts(), set(v["may_contact"]))
        # an editor sees the profile's entry; a viewer sees none
        s = self.mine(self.view(self.lee), sid)
        self.assertEqual([c["profile"] for c in s["connections"]], [self.pid])
        self.assertEqual(self.view(self.kim)["sources"], [])

    def test_admin_test_is_listed_for_admins_only(self):
        sid = self.source()   # tested, no profile involved
        self.assertIsNotNone(self.mine(self.view(self.robin), sid)["last_contact"])
        self.assertIsNone(self.mine(self.view(self.lee), sid))   # no connection of lee's profiles
        db = dbm.connect()
        try:   # the host is encrypted at rest, like an activity detail
            raw = [r["host"] for r in db.execute("SELECT host FROM outside_contacts")]
            self.assertTrue(raw and not any("127.0.0.1" in h for h in raw), raw)
        finally:
            db.close()

    def test_disconnect_stops_all_contact(self):
        sid = self.source()
        cid = self.connect(sid)
        self.assertTrue(self.pull(cid)["ok"])
        db = dbm.connect()
        try:
            midway = smart.Client(outside.for_connection(db, cid, "Connected source: sync"))   # a pull under way
            self.assertEqual(midway.hosts, {self.own})
            r = self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/disconnect", headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            before = len(self.fake.requests)
            self.assertEqual(midway.hosts, set())
            with self.assertRaises(smart.Refused):
                midway.request("GET", self.fake.fhir_base + "/Patient/pat-1")
            with self.assertRaises(net.Refused):   # the gateway itself, under the same permit
                net.connection(midway.permit, self.fake.fhir_base + "/metadata")
            self.assertEqual(outside.for_connection(db, cid, "Connected source: sync").hosts(), set())
        finally:
            db.close()
        self.assertFalse(self.pull(cid)["ok"])
        self.assertEqual(len(self.fake.requests), before)   # nothing reached the vendor after the disconnect
        # gone from "may contact" for the profile's people; listed as an earlier contact instead
        v = self.view(self.lee)
        self.assertIsNone(self.mine(v, sid))
        self.assertNotIn(self.own, v["may_contact"])
        self.assertIn(self.own, [e["host"] for e in v["earlier"]])
        self.assertEqual(self.mine(self.view(self.robin), sid)["connections"], [])   # still set up, no connection

    def test_a_host_the_source_doesnt_name_is_refused(self):
        sid = self.source()
        cid = self.connect(sid)
        db = dbm.connect()
        try:
            http_ = smart.Client(outside.for_connection(db, cid, "Connected source: sync"))
            for url in ("http://127.0.0.2:9/fhir/Observation", "https://fhir.example.org/r4/Patient"):
                with self.assertRaises(smart.Refused, msg=url):
                    http_.request("GET", url)
        finally:
            db.close()
        self.assertEqual(netguard.take(), [])

    def test_removing_a_source_stops_contact(self):
        sid = self.source()
        db = dbm.connect()
        try:
            p = outside.for_source(db, sid, "Connected source: sign-in")
            self.assertEqual(p.hosts(), {self.own})
            self.assertEqual(self.robin.delete(f"/api/connect/sources/{sid}", headers=H).status_code, 200)
            self.assertEqual(p.hosts(), set())
        finally:
            db.close()
        self.assertNotIn(self.own, self.view(self.robin)["may_contact"])

    # ---------------------------------------------------------------- Health Links and Health Cards

    def test_health_link_and_card_issuer_listed(self):
        link = self.srv.share(shl_server.bundle("2026-02-01"))
        r = self.robin.post(f"/api/profiles/{self.other}/shl/import", json={"link": link, "confirm": True}, headers=H)
        self.assertEqual(r.status_code, 202, r.text)
        payload = {"iss": self.srv.base + "/issuer", "vc": {"credentialSubject": {"fhirBundle": shl_server.bundle("2026-02-02")}}}
        card = self.srv.share({"verifiableCredential": [shl.sign_card(payload, self.issuer_key, "issuer-1")]}, cty=shl.CARD)
        r = self.robin.post(f"/api/profiles/{self.other}/shl/import", json={"link": card, "confirm": True}, headers=H)
        self.assertEqual(r.status_code, 202, r.text)
        self.assertEqual(r.json()["cards"][0]["signature"], "verified")
        host = f"localhost:{self.srv.port}"
        v = self.view(self.robin)
        links = [(e["host"], e["profile"], e["purpose"]) for e in v["health_links"]]
        self.assertIn((host, self.other, "SMART Health Link"), links)
        self.assertEqual([(e["host"], e["profile_name"]) for e in v["card_keys"]], [(host, "Other (fictional)")])
        self.assertTrue(all(e["last"] and e["count"] >= 1 for e in v["health_links"] + v["card_keys"]))
        # lee edits P1 only: P2's links aren't theirs to see
        self.assertEqual(self.view(self.lee)["health_links"], [])
        self.assertEqual(self.view(self.lee)["card_keys"], [])

    def test_unconfirmed_link_contacts_nobody(self):
        link = self.srv.share(shl_server.bundle("2026-02-03"))
        self.srv.seen.clear()
        r = self.robin.post(f"/api/profiles/{self.other}/shl/import", json={"link": link}, headers=H)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.srv.seen, [])

    # ---------------------------------------------------------------- the provider directory's lists (#992)

    def test_directory_lists_contact_their_hosts_only_when_an_admin_asks_each_contact_recorded(self):
        from server.directory import build, update
        from tests.directory_host import DirectoryHost
        host = DirectoryHost().start()
        self.addCleanup(host.stop)
        v = self.view(self.robin)
        self.assertEqual(v["directory"]["hosts"], ["open.epic.com", "raw.githubusercontent.com"])
        self.assertNotIn("open.epic.com", v["may_contact"], "only when an admin asks: not a host it may contact now")
        self.assertNotIn("directory", self.view(self.lee), "an admin's alone")
        host.use()
        update.wake()   # the app's thread, with the weekly update off: no contact
        time.sleep(0.2)
        self.assertEqual((host.hits, self.view(self.robin)["directory"]["contacts"]), ([], []), "nothing before the button")
        r = self.robin.post("/api/provider-directory/update", headers=H)
        self.assertEqual(r.status_code, 202, r.text)
        for _ in range(200):
            if not self.robin.get("/api/provider-directory").json()["running"]:
                break
            time.sleep(0.05)
        here = f"127.0.0.1:{host.port}"
        got = self.view(self.robin)["directory"]["contacts"]
        self.assertEqual([(e["host"], e["purpose"], e["profile"], e["count"]) for e in got], [(here, "Provider directory", None, 1)])
        log = [e for e in self.robin.get("/api/activity").json() if e["action"] == "outside"]
        self.assertEqual((log[0]["detail"], log[0]["actor"]), ({"host": here, "purpose": "Provider directory"}, "robin"))
        # a host the lists don't name is refused before anything is sent
        build.SOURCES["oracle-millennium"]["url"] = "http://127.0.0.2:9/elsewhere"
        db = dbm.connect()
        try:
            with self.assertRaises(net.Refused):
                net.connection(outside.for_directory(db), "http://127.0.0.1:9/x")
        finally:
            db.close()
        self.assertEqual(netguard.take(), [])

    # ---------------------------------------------------------------- the page

    def test_settings_install_has_the_section_without_inline_script(self):
        r = self.robin.get("/settings.html")   # Settings → Install since #991 (the Admin page's from #167)
        self.assertEqual(r.status_code, 200)
        html = r.text
        self.assertIn('id="outside"', html)
        self.assertIn('src="js/outside.js"', html)
        self.assertNotIn('id="outside"', self.robin.get("/admin.html").text)   # its old address only says where it went (moved.js)
        self.assertEqual(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", html), [], "no inline script")
        self.assertNotIn("unsafe-inline", r.headers.get("content-security-policy", "").split("script-src")[-1].split(";")[0])
        with open(os.path.join(ROOT, "www", "js", "outside.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertNotIn("innerHTML", js)

    def test_signed_out_gets_nothing(self):
        self.assertEqual(client().get("/api/outside").status_code, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
