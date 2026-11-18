#!/usr/bin/env python3
"""SMART Health Links and Cards (server/shl.py, server/routes_shl.py, server/outside.py; issue #94): a pasted link is
read without fetching anything; once confirmed, its manifest (or its one file) is fetched from a local HTTPS test
server (tests/shl_server.py), decrypted, stored as an upload and imported through the normal job, to a preview and
saved. Also: passcodes right and wrong, expired links, Health Cards with valid, invalid and uncheckable signatures,
HTTP and private addresses refused, redirects elsewhere not followed, size caps, the key and passcode never kept,
and the host recorded as an outside contact. Fictional data only.

  python3 tests/test_shl.py
"""
import json, os, sys, tempfile, time, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-shl-test-")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "no-build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"))
os.makedirs(os.path.join(TMP, "records"))
os.makedirs(os.path.join(TMP, "keys"))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import logging   # noqa: E402

from cryptography.hazmat.primitives.asymmetric import ec   # noqa: E402
from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, activity, config, crypto, db as dbm, main, net, outside, profiles as prof, shl, uploads   # noqa: E402
from tests.whole_records import whole   # noqa: E402  records with every record's details (#217)
import shl_server   # noqa: E402
from tests import netguard   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites"}
PASSCODE = "fictional-1234"


def client():
    return TestClient(main.app, base_url="http://testserver")


def setUpModule():   # the whole run under the socket guard: only the test servers started here may be contacted (#97)
    netguard.install()


def tearDownModule():
    netguard.uninstall()
    netguard.check()


class Parsing(unittest.TestCase):
    """shl.py on its own: no server, no network."""
    KEY = bytes(range(32))

    def test_round_trip_and_viewer_address(self):
        text = shl.encode("https://shl.example.org/m/abc", self.KEY, "LP", "Fictional clinic", 2_000_000_000)
        self.assertTrue(text.startswith("shlink:/"))
        for pasted in (text, "https://viewer.example.org/view#" + text, "  " + text + "\n"):
            link = shl.parse(pasted)
            self.assertEqual((link.url, link.key, link.flags, link.label, link.exp),
                             ("https://shl.example.org/m/abc", self.KEY, "LP", "Fictional clinic", 2_000_000_000))
        d = shl.describe(link, now=1_000_000_000)
        self.assertEqual((d["host"], d["label"], d["passcode"], d["long_term"], d["direct"], d["expired"]),
                         ("shl.example.org", "Fictional clinic", True, True, False, False))
        self.assertNotIn(shl.b64e(self.KEY), json.dumps(d))   # what's shown never holds the key
        self.assertNotIn(shl.b64e(self.KEY), repr(link))

    def test_refused_payloads(self):
        good = {"url": "https://shl.example.org/m", "key": shl.b64e(self.KEY)}
        for bad in ({**good, "key": "short"}, {**good, "flag": "PU"}, {"key": good["key"]}, {**good, "v": 2},
                    {**good, "exp": "tomorrow"}, {**good, "flag": "p"}):
            with self.assertRaises(shl.Refused, msg=bad):
                shl.parse("shlink:/" + shl.b64e(json.dumps(bad).encode()))
        for bad in ("", "hello", "https://example.org/#nothing", "shlink:/!!!", "shlink:/" + shl.b64e(b"[1]")):
            with self.assertRaises(shl.Refused, msg=bad):
                shl.parse(bad)

    def test_jwe_round_trip_and_wrong_key(self):
        jwe = shl.encrypt(b'{"resourceType":"Bundle"}', self.KEY, zip_=True)
        self.assertEqual(shl.decrypt(jwe, self.KEY), (b'{"resourceType":"Bundle"}', shl.FHIR))
        with self.assertRaises(shl.Refused):
            shl.decrypt(jwe, bytes(32))
        head, *rest = jwe.split(".")
        with self.assertRaises(shl.Refused):   # the header is authenticated
            shl.decrypt(".".join([shl.b64e(b'{"alg":"dir","enc":"A256GCM"}')] + rest), self.KEY)

    def test_unpacking_is_capped(self):
        jwe = shl.encrypt(b"0" * (3 * config.MB), self.KEY, zip_=True)
        with self.assertRaises(shl.Refused) as e:
            shl.decrypt(jwe, self.KEY, limit=config.MB)
        self.assertEqual(e.exception.status, 413)

    def test_http_and_private_addresses_refused_before_any_connection(self):
        with mock.patch.object(shl, "ALLOW_PRIVATE", False), mock.patch("socket.create_connection") as conn:
            for url in ("http://shl.example.org/m", "https://127.0.0.1/m", "https://10.1.2.3/m", "https://[::1]/m",
                        "https://169.254.169.254/latest", "https://user:pw@shl.example.org/m", "ftp://shl.example.org/"):
                with self.assertRaises(shl.Refused, msg=url):
                    shl._request("GET", url)
            with mock.patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("192.168.1.20", 443))]):
                with self.assertRaises(shl.Refused) as e:   # a public-looking name that resolves to the home network
                    shl._request("GET", "https://records.example.org/m", permit=net.Permit("test", {"records.example.org"}))
                self.assertIn("private or local", str(e.exception))
            conn.assert_not_called()

    def test_health_card_numeric_qr(self):
        k = ec.generate_private_key(ec.SECP256R1())
        jws = shl.sign_card({"iss": "https://issuer.example.org", "vc": {"credentialSubject": {"fhirBundle": shl_server.bundle()}}}, k, "k1")
        link = shl.parse("shc:/" + "".join(f"{ord(c) - 45:02d}" for c in jws))
        self.assertEqual((link.kind, link.jws), ("card", jws))
        self.assertEqual(shl.describe(link)["host"], "issuer.example.org")
        with self.assertRaises(shl.Refused):
            shl.parse("shc:/1/2/5676")


class Links(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = shl_server.Server().start()
        cls.srv.trust()
        cls.ctx = client().__enter__()   # lifespan: database, key, the import worker
        db = dbm.connect()
        uids = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        cls.pid = prof.create_profile(db, "Robin (fictional)", owner_id=uids["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (uids["kim"], cls.pid))
        db.close()
        cls.robin, cls.kim = cls.login("robin"), cls.login("kim")
        # an issuer of Health Cards, with its public key where SMART Health Cards keep them
        cls.issuer_key, cls.other_key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
        jwks = json.dumps({"keys": [shl.jwk_of(cls.issuer_key.public_key(), "issuer-1")]}).encode()
        cls.srv.routes["/issuer/.well-known/jwks.json"] = lambda m, q, b: (200, "application/json", jwks)

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        cls.srv.stop()
        shl.ALLOW_PRIVATE, shl.CA_FILE = False, None

    @staticmethod
    def login(name):
        c = client()
        r = c.post("/api/login", json={"username": name, "password": PW[name]}, headers=H)
        assert r.status_code == 200, r.text
        return c

    def setUp(self):
        self.srv.seen.clear()

    def post(self, path, body, c=None):
        return (c or self.robin).post(f"/api/profiles/{self.pid}/shl/{path}", json=body, headers=H)

    def imported(self, link, passcode=None, status=202):
        r = self.post("import", {"link": link, "passcode": passcode, "confirm": True})
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def wait(self, jid, save=True, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            j = self.robin.get(f"/api/profiles/{self.pid}/jobs/{jid}").json()
            if j["state"] == "preview" and save:
                self.assertEqual(self.robin.post(f"/api/profiles/{self.pid}/jobs/{jid}/confirm", headers=H).status_code, 202)
                save = False
                continue
            if j["state"] in ("done", "failed") or (j["state"] == "preview" and not save):
                return j
            time.sleep(0.05)
        self.fail(f"job {jid} didn't finish: {j}")

    def card_link(self, signer, day, iss=None):
        payload = {"iss": iss or self.srv.base + "/issuer", "nbf": 1_700_000_000,
                   "vc": {"type": ["https://smarthealth.cards#health-card"],
                          "credentialSubject": {"fhirVersion": "4.0.1", "fhirBundle": shl_server.bundle(day)}}}
        doc = {"verifiableCredential": [shl.sign_card(payload, signer, "issuer-1")]}
        return self.srv.share(doc, cty=shl.CARD)

    # ---------------------------------------------------------------- reading the link: nothing is fetched

    def test_inspect_fetches_nothing_and_never_returns_the_key(self):
        link = self.srv.share(shl_server.bundle("2026-01-02"), passcode=PASSCODE, flags="L", label="Fictional clinic")
        r = self.post("inspect", {"link": link})
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertEqual((d["host"], d["label"], d["passcode"], d["long_term"]),
                         (f"localhost:{self.srv.port}", "Fictional clinic", True, True))
        self.assertNotIn(shl.b64e(shl.parse(link).key), r.text)
        r = self.post("import", {"link": link, "passcode": PASSCODE})   # not confirmed
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(self.srv.seen, [])   # neither reading the link nor an unconfirmed import contacted anyone

    def test_viewers_cant_use_it(self):
        link = self.srv.share(shl_server.bundle("2026-01-03"))
        self.assertEqual(self.post("inspect", {"link": link}, self.kim).status_code, 403)
        self.assertEqual(self.post("import", {"link": link, "confirm": True}, self.kim).status_code, 403)
        self.assertEqual(self.srv.seen, [])

    # ---------------------------------------------------------------- importing

    def test_link_without_passcode_imports_through_preview(self):
        link = self.srv.share(shl_server.bundle("2026-02-01", 12.4))
        r = self.imported(link)
        (method, path, _, body), = self.srv.seen   # exactly one request: the manifest
        self.assertEqual(method, "POST")
        ask = json.loads(body)
        self.assertEqual(ask["recipient"], shl.RECIPIENT)
        self.assertNotIn("passcode", ask)
        j = self.wait(r["job"], save=False)
        self.assertEqual((j["state"], j["importer"]), ("preview", "fhir"), j)
        self.assertTrue(j["new"])
        self.assertEqual(j["counts"]["results"], 1, j)
        j = self.wait(r["job"])
        self.assertEqual(j["state"], "done", j)
        pts = [p for s in self.robin.get(f"/api/profiles/{self.pid}/data").json()["labs"] if s["name"].startswith("Hemoglobin") for p in s["pts"]]
        self.assertTrue(any(p[0].startswith("2026-02-01") and p[1] == 12.4 for p in pts), pts)
        # the import is a normal upload: its decrypted files, sealed at rest, and undoable like any other
        db = dbm.connect()
        try:
            self.assertEqual(uploads.manifest(db, self.pid, r["upload"]), ["smart-health-link/file-1.json"])
            folder = uploads.folder(self.pid, r["upload"])
            for f in os.listdir(folder):
                with open(os.path.join(folder, f), "rb") as fh:
                    raw = fh.read()
                self.assertEqual(raw[:4], crypto.STREAM)
                self.assertNotIn(b"Hemoglobin", raw)
            # the outside contact: host and purpose, nothing else
            self.assertEqual([(o["host"], o["purpose"]) for o in outside.recent(db, self.pid)][:1],
                             [(f"localhost:{self.srv.port}", "SMART Health Link")])
        finally:
            db.close()
        hist = self.robin.get(f"/api/profiles/{self.pid}/imports").json()["imports"]
        self.assertIn(j["import_id"], [i["id"] for i in hist])

    def test_passcode_right_wrong_and_missing(self):
        link = self.srv.share(shl_server.bundle("2026-02-02"), passcode=PASSCODE)
        r = self.post("import", {"link": link, "confirm": True})   # missing: refused here, nothing sent
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(self.srv.seen, [])
        r = self.post("import", {"link": link, "passcode": "not-it", "confirm": True})
        self.assertEqual(r.status_code, 403, r.text)
        self.assertIn("passcode isn't right", r.json()["detail"])
        self.assertIn("2 attempts left", r.json()["detail"])
        r = self.imported(link, PASSCODE)
        self.assertEqual(json.loads(self.srv.seen[-1][3])["passcode"], PASSCODE)
        self.assertEqual(self.wait(r["job"])["state"], "done")
        self.assert_secrets_not_kept(link, PASSCODE)

    def test_expired_link_is_not_fetched(self):
        link = self.srv.share(shl_server.bundle("2026-02-03"), exp=int(time.time()) - 60)
        self.assertTrue(self.post("inspect", {"link": link}).json()["expired"])
        r = self.post("import", {"link": link, "confirm": True})
        self.assertEqual(r.status_code, 410, r.text)
        self.assertEqual(self.srv.seen, [])

    def test_direct_file_link(self):
        link = self.srv.share(shl_server.bundle("2026-02-04"), flags="U", viewer="https://viewer.example.org/")
        self.assertTrue(self.post("inspect", {"link": link}).json()["direct"])
        r = self.imported(link)
        (method, _, query, _), = self.srv.seen
        self.assertEqual((method, query.get("recipient")), ("GET", [shl.RECIPIENT]))
        self.assertEqual(self.wait(r["job"])["state"], "done")

    def test_file_at_a_location(self):
        key = os.urandom(32)
        jwe = shl.encrypt(json.dumps(shl_server.bundle("2026-02-05")).encode(), key)
        self.srv.routes["/files/one"] = lambda m, q, b: (200, "application/jose", jwe.encode())
        self.srv.routes["/shl/located"] = lambda m, q, b: (200, "application/json", json.dumps(
            {"files": [{"contentType": shl.FHIR, "location": self.srv.base + "/files/one"}]}).encode())
        r = self.imported(shl.encode(self.srv.base + "/shl/located", key))
        self.assertEqual([s[:2] for s in self.srv.seen], [("POST", "/shl/located"), ("GET", "/files/one")])
        self.assertEqual(self.wait(r["job"])["state"], "done")

    # ---------------------------------------------------------------- Health Cards

    def test_health_card_with_a_valid_signature(self):
        r = self.imported(self.card_link(self.issuer_key, "2026-03-01"))
        self.assertEqual([(c["signature"], c["host"]) for c in r["cards"]], [("verified", f"localhost:{self.srv.port}")])
        self.assertIn("/issuer/.well-known/jwks.json", [s[1] for s in self.srv.seen])
        j = self.wait(r["job"])
        self.assertEqual((j["state"], j["counts"]["results"]), ("done", 1), j)
        db = dbm.connect()
        try:
            self.assertIn("Health Card issuer's keys", [o["purpose"] for o in outside.recent(db, self.pid)])
        finally:
            db.close()

    def test_vaccination_health_card_imports_immunizations(self):   # #179: what Health Cards most often carry
        payload = {"iss": self.srv.base + "/issuer", "nbf": 1_700_000_000,
                   "vc": {"type": ["https://smarthealth.cards#health-card", "https://smarthealth.cards#immunization"],
                          "credentialSubject": {"fhirVersion": "4.0.1", "fhirBundle": shl_server.vaccination_bundle()}}}
        link = self.srv.share({"verifiableCredential": [shl.sign_card(payload, self.issuer_key, "issuer-1")]}, cty=shl.CARD)
        r = self.imported(link)
        self.assertEqual([c["signature"] for c in r["cards"]], ["verified"])
        j = self.wait(r["job"], save=False)
        self.assertEqual((j["state"], j["counts"]["kinds"]), ("preview", {"immunization": 2}), j)
        self.assertEqual(self.wait(r["job"])["state"], "done")
        data = whole(self.robin, self.pid)
        imms = sorted((e for e in data["events"] if e["k"] == "immunization"), key=lambda e: e["t"])
        self.assertEqual([(e["t"][:10], e["title"]) for e in imms], [("2025-01-15", "COVID-19 vaccine"), ("2025-02-12", "COVID-19 vaccine")])
        self.assertEqual(data["details"][str(imms[1]["id"])],   # the shape the C-CDA and FHIR importers give one
                         {"type": "immunization", "vaccine": "COVID-19 vaccine", "code": "207", "system": "CVX", "lot": "FICT-0002",
                          "performer": "Fictional County Health Department", "status": "Completed", "src": "Fictional County Health Department"})
        again = self.imported(self.srv.share({"verifiableCredential": [shl.sign_card(payload, self.issuer_key, "issuer-1")]}, cty=shl.CARD))
        j = self.wait(again["job"], save=False)   # the same card again: both doses found by their keys
        self.assertEqual((j["state"], j["new"], j["duplicates"]["kinds"]), ("preview", False, {"immunization": 2}), j)

    def test_health_card_with_an_invalid_signature_is_left_out(self):
        r = self.post("import", {"link": self.card_link(self.other_key, "2026-03-02"), "confirm": True})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("signature doesn't match", r.json()["detail"])

    def test_health_card_whose_signature_cant_be_checked_says_so(self):
        r = self.imported(self.card_link(self.issuer_key, "2026-03-03", iss=self.srv.base + "/nobody"))
        (c,), = [r["cards"]]
        self.assertEqual(c["signature"], "unchecked")
        self.assertIn("404", c["why"])
        self.assertEqual(self.wait(r["job"])["state"], "done")

    def test_pasted_health_card_qr_text(self):
        payload = {"iss": self.srv.base + "/issuer", "vc": {"credentialSubject": {"fhirBundle": shl_server.bundle("2026-03-04")}}}
        jws = shl.sign_card(payload, self.issuer_key, "issuer-1")
        r = self.imported("shc:/" + "".join(f"{ord(c) - 45:02d}" for c in jws))
        self.assertEqual(r["cards"][0]["signature"], "verified")
        self.assertEqual([s[1] for s in self.srv.seen], ["/issuer/.well-known/jwks.json"])
        self.assertEqual(self.wait(r["job"])["state"], "done")

    # ---------------------------------------------------------------- refused

    def test_private_and_http_targets_refused(self):
        link = self.srv.share(shl_server.bundle("2026-04-01"))
        with mock.patch.object(shl, "ALLOW_PRIVATE", False):
            for l in (link, shl.encode(f"https://127.0.0.1:{self.srv.port}/shl/x", os.urandom(32))):
                r = self.post("import", {"link": l, "confirm": True})
                self.assertEqual(r.status_code, 400, r.text)
                self.assertIn("private or local", r.json()["detail"])
        r = self.post("import", {"link": shl.encode(f"http://localhost:{self.srv.port}/shl/x", os.urandom(32)), "confirm": True})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("isn't HTTPS", r.json()["detail"])
        self.assertEqual(self.srv.seen, [])

    def test_redirect_to_another_host_not_followed(self):
        self.srv.routes["/shl/away"] = lambda m, q, b: (307, "text/plain", f"https://127.0.0.1:{self.srv.port}/shl/x".encode())
        r = self.post("import", {"link": shl.encode(self.srv.base + "/shl/away", os.urandom(32)), "confirm": True})
        self.assertEqual(r.status_code, 502, r.text)
        self.assertIn("doesn't follow", r.json()["detail"])
        self.assertEqual(len(self.srv.seen), 1)

    def test_size_and_file_caps(self):
        link = self.srv.share({"resourceType": "Bundle", "type": "collection", "entry": [], "pad": "x" * 200_000})
        with mock.patch.object(config, "SHL_MAX_BYTES", 50_000):
            r = self.post("import", {"link": link, "confirm": True})
        self.assertEqual(r.status_code, 413, r.text)
        key = os.urandom(32)
        jwe = shl.encrypt(json.dumps(shl_server.bundle()).encode(), key)
        self.srv.manifest("/shl/many", [(shl.FHIR, jwe)] * 3)
        with mock.patch.object(config, "SHL_MAX_FILES", 2):
            r = self.post("import", {"link": shl.encode(self.srv.base + "/shl/many", key), "confirm": True})
        self.assertEqual(r.status_code, 413, r.text)

    def test_wrong_key_is_refused(self):
        link = shl.parse(self.srv.share(shl_server.bundle("2026-04-02")))
        r = self.post("import", {"link": shl.encode(link.url, os.urandom(32)), "confirm": True})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("couldn't be decrypted", r.json()["detail"])

    # ---------------------------------------------------------------- nothing secret kept

    def assert_secrets_not_kept(self, link, passcode):
        key = shl.b64e(shl.parse(link).key)
        db = dbm.connect()
        try:
            for r in activity.for_profile(db, self.pid, limit=500):
                self.assertNotIn(key, json.dumps(r))
                self.assertNotIn(passcode, json.dumps(r))
            for table in ("uploads", "jobs", "activity"):
                for row in db.execute(f"SELECT * FROM {table}"):
                    text = " ".join(crypto.dec(v) if isinstance(v, str) and v.startswith(crypto.FIELD) else str(v) for v in tuple(row))
                    self.assertNotIn(key, text)
                    self.assertNotIn(passcode, text)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
