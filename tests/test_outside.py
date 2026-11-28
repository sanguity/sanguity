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
os.environ["CONNECTED_SOURCES"] = "1"   # connected sources on, as before #1096 made them a switch, off by default
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
from tests.fakeproxy import FakeProxy   # noqa: E402
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
        self.assertRegex(src, r"\nimport [^\n]*\bhttp\.client, [^\n]*\bsocket\b")


class Gateway(unittest.TestCase):
    """net.py on its own."""

    def test_unlisted_host_refused_before_anything_is_recorded_or_sent(self):
        seen = []
        p = net.Permit("A test's own purpose", {"fhir.example.org"}, lambda h, why: seen.append((h, why)))
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
        self.assertEqual(seen, [("fhir.example.org", "A test's own purpose")])   # once, host only
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

    def test_through_the_outbound_proxy(self):   # #1159
        """An HTTP (CONNECT) or SOCKS5 proxy with a username and password: the permit still decides, the name goes to
        the proxy (never looked up here), an address checked beforehand is the one asked for, a wrong password is said
        in plain words, and local hosts and the bypass list's go direct."""
        srv = _echo_host()
        p = net.Permit("t", {"records.example.org"})
        for kind in ("http", "socks5"):
            fp = FakeProxy(kind, srv.server_address[1], user="sg", password="pebble lantern").start()
            self.addCleanup(fp.stop)
            with mock.patch.object(net, "PROXY", lambda: net.Proxy(kind, "127.0.0.1", fp.port, "sg", "pebble lantern", [])):
                for ip in (None, "93.184.215.14"):
                    c = net.connection(p, "http://records.example.org/x", ip=ip)
                    c.request("GET", "/x")
                    r = c.getresponse()
                    self.assertEqual((r.status, r.read()), (200, b"records.example.org"), kind)
                    c.close()
                self.assertEqual(fp.asked, ["records.example.org:80", "93.184.215.14:80"], kind)
                self.assertEqual(netguard.take(), [], "the name went to the proxy: never looked up here")
                with self.assertRaises(net.Refused):
                    net.connection(p, "http://other.example.org/x")
            with mock.patch.object(net, "PROXY", lambda: net.Proxy(kind, "127.0.0.1", fp.port, "sg", "not it", [])):
                c = net.connection(p, "http://records.example.org/x")
                with self.assertRaises(net.ProxyError) as e:
                    c.request("GET", "/x")
                c.close()
                self.assertRegex(net.why(e.exception), "outbound proxy (refused its username|wants a username)", kind)
        prox = net.Proxy("http", "127.0.0.1", 9, bypass=[".example.net", "93.184.215.0/24", "files.example.com"])
        for host, ip in (("localhost", None), ("printer.lan", None), ("nas", None), ("10.0.0.4", None), ("a.example.net", None),
                         ("example.net", None), ("files.example.com", None), ("x.example.org", "192.168.1.5"), ("x.example.org", "93.184.215.14")):
            self.assertTrue(prox.bypasses(host, ip), host)
        for host in ("records.example.org", "example.net.evil.example", "www.files.example.com"):
            self.assertFalse(prox.bypasses(host), host)


def _echo_host():
    """A plain HTTP server on this machine answering each GET with the Host it was sent."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Echo(BaseHTTPRequestHandler):
        def do_GET(self):
            body = self.headers.get("Host", "").encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Echo)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


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

    def test_weekly_directory_update_contacts_nobody_with_saved_settings_off(self):   # #1043
        """The weekly update saved on, and due (never read): with SANGUITY_SAVED_SETTINGS=off it's off, as every saved
        setting is then, and the app's thread contacts nobody."""
        from server import settings
        from server.directory import update
        from tests.directory_host import DirectoryHost
        host = DirectoryHost().start()
        self.addCleanup(host.stop)
        host.use()
        db = dbm.connect()
        try:
            for k in ("directory.last", "directory.read_at"):
                dbm.meta_set(db, k, None)
            settings.put(db, "directory.weekly", True)
            self.assertLessEqual(update.due_at(db), time.time(), "on and due")
            with mock.patch.dict(os.environ, {"SANGUITY_SAVED_SETTINGS": "off"}):
                self.assertIsNone(update.due_at(db))
                update.wake()   # the app's thread looks at the weekly update
                time.sleep(0.3)
                self.assertEqual(host.hits, [], "nobody contacted")
                # (#1158) connected sources, on as saved, are off too while saved settings are left out
                self.assertEqual(self.robin.get("/api/provider-directory").status_code, 404)
                settings.put(db, "directory.weekly", False)
        finally:
            settings.put(db, "directory.weekly", False)
            settings.put(db, "experimental.connectedSources", True)
            settings.forget_saved()
            db.close()
        self.assertEqual(netguard.take(), [])

    def test_connected_sources_off_contact_nobody_and_keep_what_they_had(self):   # #1096
        """Connected sources turned off, with a connection due and the weekly directory update on and due: every
        connections, sync and directory route refused saying so, for its editor and the admin; no pull and no directory
        read; the gateway refuses the connection's hosts; the outside view lists none of it. The connection stays as
        it was, and syncs once they're on again."""
        from server import settings
        from server.directory import update
        from tests.directory_host import DirectoryHost
        sid = self.source()
        cid = self.connect(sid)
        self.assertTrue(self.pull(cid)["ok"])
        host = DirectoryHost().start()
        self.addCleanup(host.stop)
        host.use()
        db = dbm.connect()
        p = f"/api/profiles/{self.pid}"
        try:
            for k in ("directory.last", "directory.read_at"):
                dbm.meta_set(db, k, None)
            settings.put(db, "directory.weekly", True)
            db.execute("UPDATE connections SET next_sync=1 WHERE id=?", (cid,))
            before = len(self.fake.requests)
            settings.put(db, "experimental.connectedSources", False)   # (#1158: the page's switch, not the environment's)
            settings.forget_saved()
            if True:
                for c, method, path in ((self.lee, "GET", p + "/connections"), (self.lee, "GET", p + "/connections/warnings"),
                                        (self.lee, "POST", f"{p}/connections/{cid}/sync"), (self.lee, "POST", p + "/connect/start"),
                                        (self.lee, "POST", f"{p}/connections/{cid}/disconnect"), (self.lee, "POST", "/api/connect/return"),
                                        (self.robin, "GET", "/api/connect/sources"), (self.robin, "POST", f"/api/connect/sources/{sid}/test"),
                                        (self.lee, "GET", "/api/directory?q=clinic"), (self.robin, "GET", "/api/provider-directory"),
                                        (self.robin, "POST", "/api/provider-directory/update")):
                    r = c.request(method, path, headers=H, json={"source": sid, "code": "x", "state": "y"} if method == "POST" else None)
                    self.assertEqual((r.status_code, r.json()["detail"]), (404, "Connected sources are turned off on this server."), path)
                self.assertEqual(client().get(p + "/connections").status_code, 401, "signed out: the sign-in's answer first")
                self.assertEqual(sync.run_due(), 0)
                self.assertIsNone(update.due_at(db))
                update.wake()   # the app's thread looks at the weekly update
                time.sleep(0.3)
                with self.assertRaises(net.Refused):
                    net.connection(outside.for_connection(db, cid, "Connected source: sync"), self.fake.fhir_base + "/metadata")
                with self.assertRaises(net.Refused):
                    net.connection(outside.for_source(db, sid, "Connected source: test"), self.fake.fhir_base + "/metadata")
                for c in (self.robin, self.lee):
                    v = self.view(c)
                    self.assertEqual((v["sources"], v["may_contact"], v["earlier"], "directory" in v), ([], [], [], False))
            self.assertEqual((len(self.fake.requests), host.hits), (before, []), "nobody contacted")
            self.assertEqual(db.execute("SELECT state, next_sync FROM connections WHERE id=?", (cid,)).fetchone()[:], ("active", 1), "kept as it was")
            settings.put(db, "experimental.connectedSources", True)
            settings.forget_saved()
            settings.put(db, "directory.weekly", False)
            self.assertGreaterEqual(sync.run_due(), 1, "on again: it syncs")
            self.assertGreater(len(self.fake.requests), before)
            self.assertIsNotNone(self.mine(self.view(self.lee), sid))
        finally:
            settings.put(db, "directory.weekly", False)
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

    def test_general_about_contacts_nobody(self):   # #1004
        """General → About shows the version and links to the releases page, and that's all (#998, decision 5): reading
        it, and Backups and logging, contacts no host and adds no outside contact, and its script asks only the app for
        anything, the releases page a link it opens in another tab."""
        from server import general
        netguard.take()
        before = self.view(self.robin)
        got = [self.robin.get(p) for p in ("/api/settings/install/about", "/api/settings/install/backups-logging")]
        self.assertEqual([r.status_code for r in got], [200, 200], [r.text for r in got])
        self.assertEqual(got[0].json()["releases"], general.RELEASES)
        self.assertEqual((netguard.take(), self.view(self.robin)), ([], before))
        with open(os.path.join(ROOT, "www", "js", "gen-about.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertEqual(re.findall(r"\b(fetch|XMLHttpRequest|sendBeacon|WebSocket|EventSource|import)\s*\(", js), [])
        self.assertEqual(re.findall(r'\bcall\(\s*"([^"]+)"', js), ["settings/install/about"])
        self.assertIn('href: a.releases, target: "_blank", rel: "noopener noreferrer"', js)

    def test_sign_in_provider_contacts_nobody_until_set_up_then_its_hosts_alone(self):   # #1109
        """Before a sign-in provider is set up nothing is contacted for it, the sign-in page's question included; once
        it is, a sign-in's start reaches its issuer's host alone (here also its token endpoint's and JWKS's), each
        contact recorded and listed for admins, never for members."""
        from server import oidc
        from tests.fakeoidc import FakeOIDC
        netguard.take()
        before = self.view(self.robin)
        self.assertEqual(client().get("/api/login/oidc").json()["label"], None)
        self.assertEqual(client().post("/api/login/oidc/start", headers=H).status_code, 404)
        self.assertEqual((netguard.take(), self.view(self.robin)), ([], before))
        self.assertNotIn("sign_in", before)
        with FakeOIDC() as idp:
            try:
                r = self.robin.put("/api/settings/install/oidc", json={"issuer": idp.issuer, "client_id": idp.client_id}, headers=H)
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(netguard.take(), [], "saving it contacts nobody")
                self.assertEqual(client().post("/api/login/oidc/start", headers=H).status_code, 200)
                v = self.view(self.robin)
                host = f"127.0.0.1:{idp._httpd.server_address[1]}"
                self.assertEqual(v["sign_in"]["hosts"], [host])
                self.assertIn(host, v["may_contact"])
                self.assertEqual([(c["host"], c["purpose"]) for c in v["sign_in"]["contacts"]], [(host, oidc.PURPOSE)])
                self.assertNotIn("sign_in", self.view(self.kim))
            finally:
                db = dbm.connect()
                db.execute("DELETE FROM meta WHERE k='oidc'")
                db.close()
                oidc._cache.clear()

    def test_update_check_off_by_default_github_only_once_on(self):   # #1159
        """Checking for new versions contacts nobody until an admin turns it on; then GitHub's releases API alone, through
        the gateway, listed under Outside connections; a newer release is said to admins only."""
        from server import updates
        db = dbm.connect()
        self.addCleanup(db.close)
        self.assertNotIn("updates", self.view(self.robin))
        with self.assertRaises(net.Refused):
            net.connection(updates.permit(db), updates.RELEASES)
        self.assertEqual(self.robin.post("/api/settings/install/updates/check", headers=H).status_code, 409)
        r = self.robin.put("/api/settings/install/updates.check", json={"value": True}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.addCleanup(lambda: self.robin.put("/api/settings/install/updates.check", json={"value": False}, headers=H))
        self.assertEqual(self.kim.put("/api/settings/install/updates.check", json={"value": False}, headers=H).status_code, 403)
        v = self.view(self.robin)
        self.assertEqual(v["updates"]["hosts"], ["api.github.com"])
        self.assertIn("api.github.com", v["may_contact"])
        self.assertTrue(updates.permit(db).allows("api.github.com"))
        self.assertFalse(updates.permit(db).allows("github.com"))
        sent = []

        class Answer:
            status = 200

            def read(self, n):
                return json.dumps({"tag_name": "v9.9.0", "html_url": "https://github.com/sanguity/sanguity/releases/tag/v9.9.0"}).encode()

        class Conn:
            def request(self, method, path, headers):
                sent.append((method, path, sorted(headers)))

            def getresponse(self):
                return Answer()

            def close(self):
                pass

        def fake(permit, url, **kw):
            permit.contact(net.host_of(url))
            return Conn()
        with mock.patch.object(updates.net, "connection", fake), mock.patch.dict(os.environ, {"SANGUITY_IMAGE_VERSION": "0.2.0"}):
            st = self.robin.post("/api/settings/install/updates/check", headers=H).json()["updates"]
            self.assertEqual((st["latest"], st["newer"], st["current"]), ("9.9.0", True, "0.2.0"))
            self.assertEqual(self.robin.get("/api/me").json()["update"]["version"], "9.9.0")
            self.assertIsNone(self.kim.get("/api/me").json()["update"])
        self.assertEqual(sent, [("GET", "/repos/sanguity/sanguity/releases/latest", ["Accept", "User-Agent"])], "nothing about the server")
        self.assertEqual([c["host"] for c in self.view(self.robin)["updates"]["contacts"]], ["api.github.com"])

    def test_instance_name_and_defaults_for_new_people(self):   # #1159
        r = self.robin.put("/api/settings/install/instance.name", json={"value": "  Home   lab "}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.addCleanup(lambda: self.robin.put("/api/settings/install/instance.name", json={"value": ""}, headers=H))
        self.assertEqual(self.kim.get("/api/me").json()["instance_name"], "Home lab")
        self.assertEqual(self.robin.put("/api/settings/install/instance.name", json={"value": "x" * 41}, headers=H).status_code, 400)
        self.lee.put("/api/settings/user/dates.format", json={"value": "ymd"}, headers=H)   # lee chose their own
        for key, value in (("dates.format", "dmy"), ("dates.timeZone", "Europe/Paris"), ("units", {"weight": "kg", "temperature": "C"})):
            r = self.robin.put(f"/api/settings/install/defaults/{key}", json={"value": value}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            self.addCleanup(lambda k=key: self.robin.put(f"/api/settings/install/defaults/{k}", json={"value": None}, headers=H))
        self.assertEqual(self.kim.put("/api/settings/install/defaults/dates.format", json={"value": "mdy"}, headers=H).status_code, 403)
        self.assertEqual(self.robin.put("/api/settings/install/defaults/theme", json={"value": "dark"}, headers=H).status_code, 404)
        self.assertEqual(self.robin.put("/api/settings/install/defaults/dates.format", json={"value": "yyyy"}, headers=H).status_code, 400)
        db = dbm.connect()
        accounts.create(db, "nell", password=PW["kim"])
        db.close()
        nell = client()
        self.assertEqual(nell.post("/api/login", json={"username": "nell", "password": PW["kim"]}, headers=H).status_code, 200)
        mine = nell.get("/api/settings/user").json()
        self.assertEqual((mine["dates.format"], mine["dates.timeZone"], mine["units"]["weight"]), ("dmy", "Europe/Paris", "kg"))
        self.assertEqual(self.lee.get("/api/settings/user").json()["dates.format"], "ymd", "their own choice stays")
        r = nell.put("/api/settings/user/dates.format", json={"value": "mdy"}, headers=H)
        self.assertEqual(r.json()["dates.format"], "mdy", "each person can still change it")

    def setting(self, key, value):
        r = self.robin.put(f"/api/settings/install/{key}", json={"value": value}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)

    def test_each_kind_off_the_master_switch_and_a_blocked_host_refused_in_the_gateway(self):   # #1167
        """Every kind of outside connection, through net.py: with its switch off, with "No outside connections at all"
        on, and with its host blocked, refused before anything is recorded, looked up or sent, whatever its permit
        says; and contacted again once it's back on. Changes are an admin's, and logged."""
        from server import notify, ocr, oidc, updates
        kinds = {"sources": outside.SOURCE + "sync", "health_links": outside.LINK, "sign_in": oidc.PURPOSE,
                 "notifications": notify.PURPOSE, "directory": outside.DIRECTORY, "languages": ocr.PURPOSE, "updates": updates.PURPOSE}
        self.assertEqual(set(kinds), set(outside.KINDS))
        self.assertEqual({outside.kind_of(p) for p in kinds.values()}, set(outside.KINDS))
        host = "records.example.org"
        seen = []
        permit = lambda purpose: net.Permit(purpose, {host}, lambda h, why: seen.append((h, why)))   # noqa: E731
        on = {k: outside.kind_on(k) for k in outside.KINDS}
        self.addCleanup(lambda: [self.setting(outside.KINDS[k][1], v) for k, v in on.items()])
        netguard.take()
        for kind, purpose in kinds.items():
            key = outside.KINDS[kind][1]
            self.setting(key, True)
            net.connection(permit(purpose), f"https://{host}/x").close()   # on: allowed (not connected yet)
            self.setting(key, False)
            with self.assertRaisesRegex(net.Refused, f"{outside.KINDS[kind][0]} is off under Outside connections"):
                net.connection(permit(purpose), f"https://{host}/x")
            with self.assertRaises(net.Refused):
                net.resolve(permit(purpose), f"https://{host}/x")
            self.setting(key, True)
        self.assertEqual(len(seen), len(kinds), "only the allowed ones recorded")
        self.setting("outside.none", True)
        self.addCleanup(self.setting, "outside.none", False)
        for purpose in [*kinds.values(), outside.PROBE]:
            with self.assertRaisesRegex(net.Refused, "No outside connections at all"):
                net.connection(permit(purpose), f"https://{host}/x")
        self.setting("outside.none", False)
        self.setting("outside.blocked", ["Records.Example.org:443", "10.0.0.5:8443"])
        self.addCleanup(self.setting, "outside.blocked", [])
        for purpose in [*kinds.values(), outside.PROBE]:
            with self.assertRaisesRegex(net.Refused, "records.example.org: it's blocked under Outside connections"):
                net.connection(permit(purpose), f"https://{host}/x")
        self.assertEqual(len(seen), len(kinds))
        self.assertEqual(netguard.take(), [], "nothing looked up or sent")
        self.assertEqual(self.lee.put("/api/settings/install/outside.none", json={"value": True}, headers=H).status_code, 403)
        self.assertEqual(self.robin.put("/api/settings/install/outside.blocked", json={"value": ["not a host!"]}, headers=H).status_code, 400)
        v = self.view(self.robin)
        self.assertEqual(v["switches"]["blocked"], ["10.0.0.5:8443", "records.example.org"])
        self.assertEqual([k["kind"] for k in v["switches"]["kinds"]], list(outside.KINDS))
        self.assertNotIn("switches", self.view(self.lee))
        db = dbm.connect()
        logged = [r["detail"].get("setting") for r in __import__("server.activity", fromlist=["x"]).for_accounts(db, limit=50)]
        db.close()
        self.assertIn("outside.blocked", logged)
        self.assertIn("outside.none", logged)

    def test_a_feature_says_its_blocked(self):   # #1167
        """A Health Link whose host is blocked: the paste says why, and nothing is contacted."""
        self.setting("outside.healthLinks", False)
        self.addCleanup(self.setting, "outside.healthLinks", True)
        link = self.srv.share(shl_server.bundle("2026-02-04"))
        netguard.take()
        r = self.robin.post(f"/api/profiles/{self.other}/shl/import", json={"link": link, "confirm": True}, headers=H)
        self.assertIn("Health Link fetching is off under Outside connections", r.text, r.status_code)
        self.setting("outside.healthLinks", True)
        self.setting("outside.blocked", [f"localhost:{self.srv.port}"])
        self.addCleanup(self.setting, "outside.blocked", [])
        r = self.robin.post(f"/api/profiles/{self.other}/shl/import", json={"link": link, "confirm": True}, headers=H)
        self.assertIn("blocked under Outside connections", r.text, r.status_code)
        self.assertEqual(netguard.take(), [])

    def test_the_sign_in_page_without_the_provider_while_its_off(self):   # #1167
        """Switched off (or every outside connection, or its issuer blocked), the sign-in page has no button; with Sign
        in with OIDC only on, it says why and how an admin gets back in."""
        from server import oidc
        from tests.fakeoidc import FakeOIDC
        with FakeOIDC() as idp:
            try:
                r = self.robin.put("/api/settings/install/oidc", json={"issuer": idp.issuer, "client_id": idp.client_id}, headers=H)
                self.assertEqual(r.status_code, 200, r.text)
                self.assertNotIn("off", client().get("/api/login/oidc").json())
                for key, value in (("outside.signIn", False), ("outside.none", True), ("outside.blocked", [net.host_of(idp.issuer)])):
                    before = outside.settings.install_value(key)
                    self.setting(key, value)
                    p = client().get("/api/login/oidc").json()
                    self.setting(key, before)
                    self.assertIn("under Outside connections", p.get("off", ""), key)
                    self.assertIn("python -m server.manage config set outside.signIn true", p["off"])
                self.assertNotIn("off", client().get("/api/login/oidc").json(), "back on")
            finally:
                db = dbm.connect()
                db.execute("DELETE FROM meta WHERE k='oidc'")
                db.close()
                oidc._cache.clear()

    def test_test_a_host_filter_and_clear_the_log(self):   # #1167
        from server import notify
        url = self.srv.base + "/topics"
        self.setting("notify.url", url)
        self.addCleanup(self.setting, "notify.url", "")
        host = net.host_of(url)
        r = self.robin.post("/api/outside/test", json={"host": host}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        steps = r.json()["steps"]
        self.assertEqual([(x["step"], x["ok"]) for x in steps], [("name", True), ("connect", True), ("tls", False)], steps)
        self.assertIn("certificate", steps[2]["said"], "its own test certificate, not one this server trusts")
        sid = self.source()   # a plain-HTTP source on this machine (the test's fake): tested at its own scheme and port
        own = net.host_of(self.fake.fhir_base)
        db = dbm.connect()
        self.assertEqual((outside.schemes(db)[own], outside.schemes(db)[host]), ("http", "https"))
        db.close()
        steps = self.robin.post("/api/outside/test", json={"host": own}, headers=H).json()["steps"]
        self.assertEqual([(x["step"], x["ok"]) for x in steps], [("name", True), ("connect", True)], steps)
        self.assertIn(f"port {self.fake.port}", steps[1]["said"])
        self.robin.delete(f"/api/connect/sources/{sid}", headers=H)
        self.assertEqual(self.robin.post("/api/outside/test", json={"host": "elsewhere.example.org"}, headers=H).status_code, 404)
        self.assertEqual(self.lee.post("/api/outside/test", json={"host": host}, headers=H).status_code, 403)
        self.setting("outside.blocked", [host])
        r = self.robin.post("/api/outside/test", json={"host": host}, headers=H)
        self.assertEqual((r.status_code, "blocked under Outside connections" in r.text), (409, True))
        self.setting("outside.blocked", [])
        log = self.robin.get("/api/outside/log", params={"kind": "test", "host": host.split(":")[0]}).json()["contacts"]
        self.assertEqual([(c["host"], c["purpose"]) for c in log], [(host, outside.PROBE)])
        self.assertEqual(self.robin.get("/api/outside/log", params={"kind": "notifications"}).json()["contacts"], [])
        self.assertEqual(self.robin.get("/api/outside/log", params={"since": dbm.now() + 3600}).json()["contacts"], [])
        self.assertEqual(self.robin.get("/api/outside/log", params={"kind": "nonsense"}).status_code, 400)
        self.assertTrue(all(c["profile"] not in (None, self.other) for c in self.lee.get("/api/outside/log").json()["contacts"]), "their profiles' only")
        r = self.robin.post("/api/outside/clear", json={}, headers=H)
        self.assertEqual(r.status_code, 409, "asked first, with how many")
        n = r.json()["detail"]["confirm"]["count"]
        self.assertEqual(self.lee.post("/api/outside/clear", json={"confirm": n}, headers=H).status_code, 403)
        r = self.robin.post("/api/outside/clear", json={"confirm": n}, headers=H)
        self.assertEqual(r.json(), {"cleared": n})
        self.assertEqual(self.robin.get("/api/outside/log").json()["contacts"], [])
        self.assertEqual(self.view(self.robin)["health_links"], [])
        db = dbm.connect()
        entry = __import__("server.activity", fromlist=["x"]).for_accounts(db, limit=1)[0]
        db.close()
        self.assertEqual((entry["action"], entry["detail"]), ("outside.cleared", {"contacts": n}))

    def test_signed_out_gets_nothing(self):
        self.assertEqual(client().get("/api/outside").status_code, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
