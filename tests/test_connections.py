#!/usr/bin/env python3
"""Connected sources (#32 sign-in, #91 connections and sync), against the fake SMART on FHIR server
(tests/fakefhir). Fictional patients only; nothing real is contacted.

  python3 tests/test_connections.py
"""
import http.client, json, os, sqlite3, sys, tempfile, time, unittest, urllib.parse
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-connect-test-")
ORIGIN = "http://sanguity.test:8730"
RETURN = ORIGIN + "/connect/return"
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "no-build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"), PUBLIC_ORIGINS=ORIGIN, CONNECT_ALLOW_HTTP="1")
os.makedirs(os.path.join(TMP, "records"))
os.makedirs(os.path.join(TMP, "keys"))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, connections as conns, crypto, db as dbm, main, profiles as prof, smart, sync   # noqa: E402
from tests.fakefhir import FakeFHIR   # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites", "lee": "maple tunnel brisk saddle"}
NEW_OBS = {"resourceType": "Observation", "status": "final",
           "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "laboratory"}]}],
           "code": {"coding": [{"system": "http://loinc.org", "code": "2345-7", "display": "Glucose"}], "text": "Glucose"},
           "effectiveDateTime": "2026-11-10T08:00:00Z", "valueQuantity": {"value": 97, "unit": "mg/dL"}}


REAL_START = sync.start


def client():
    return TestClient(main.app, base_url="http://testserver")


def q(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


class Connections(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.no_thread = mock.patch.object(sync, "start", lambda: None)   # tests pull when they choose; one test runs it
        cls.no_thread.start()
        cls.ctx = client().__enter__()
        db = dbm.connect()
        uids = {n: accounts.create(db, n, is_admin=(n == "robin"), password=pw)[0] for n, pw in PW.items()}
        cls.uids = uids
        db.close()
        cls.robin, cls.kim, cls.lee = (cls.login(n) for n in ("robin", "kim", "lee"))
        db = dbm.connect()
        db.execute("UPDATE users SET totp_secret=? WHERE id=?", (crypto.enc("JBSWY3DPEHPK3PXP"), uids["robin"]))   # admin's 2nd factor
        db.close()
        cls.sleeps = []
        cls.no_sleep = mock.patch.object(smart, "sleep", cls.sleeps.append)
        cls.no_sleep.start()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        cls.no_thread.stop()
        cls.no_sleep.stop()

    @staticmethod
    def login(name):
        c = client()
        r = c.post("/api/login", json={"username": name, "password": PW[name]}, headers=H)
        assert r.status_code == 200, r.text
        return c

    def setUp(self):
        self.fake = FakeFHIR(patients=["robin-example"]).start()
        self.addCleanup(self.fake.stop)
        db = dbm.connect()
        self.pid = prof.create_profile(db, "Robin (fictional)", owner_id=self.uids["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (self.uids["kim"], self.pid))
        db.close()
        self.sleeps.clear()

    # ---------------------------------------------------------------- helpers

    def source(self, auth="public", secret=None, scopes=None, register=True):
        cid = f"app-{auth}-{self.fake.port}"
        if register:
            self.fake.register_client(cid, [RETURN], secret=secret)
        body = {"name": f"Fake {auth} {self.fake.port}", "vendor": "epic", "issuer": self.fake.fhir_base, "client_id": cid,
                "auth": auth, **({"secret": secret} if secret else {}), **({"scopes": scopes} if scopes else {})}
        r = self.robin.post("/api/connect/sources", json=body, headers=H)
        self.assertEqual(r.status_code, 201, r.text)
        sid = r.json()["id"]
        t = self.robin.post(f"/api/connect/sources/{sid}/test", headers=H)
        self.assertEqual(t.status_code, 200, t.text)
        self.assertTrue(t.json()["ok"], t.json())
        return sid

    def start(self, sid, c=None, pid=None):
        r = (c or self.robin).post(f"/api/profiles/{pid or self.pid}/connect/start", json={"source": sid}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["url"]

    def back(self, back, c=None):
        return (c or self.robin).post("/api/connect/return", headers=H,
                                      json={"code": back.code, "state": back.state, "error": back.error,
                                            "error_description": back.error_description})

    def connect(self, sid, patient="pat-1", tamper=None):
        url = self.start(sid)
        back = self.fake.authorize(tamper(url) if tamper else url, patient=patient)
        r = self.back(back)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["connection"]

    def row(self, cid):
        db = dbm.connect()
        try:
            return dict(db.execute("SELECT * FROM connections WHERE id=?", (cid,)).fetchone())
        finally:
            db.close()

    def pull(self, cid, who=None):
        db = dbm.connect()
        try:
            return sync.pull(db, cid, who)
        finally:
            db.close()

    def first_pull(self, cid):
        """Pull, then save the first pull's preview, as the person would."""
        res = self.pull(cid)
        self.assertTrue(res["ok"], res)
        jid = res["job"]
        end = time.time() + 30
        while time.time() < end:
            j = self.robin.get(f"/api/profiles/{self.pid}/jobs/{jid}").json()
            if j["state"] == "preview":
                self.assertEqual(self.robin.post(f"/api/profiles/{self.pid}/jobs/{jid}/confirm", headers=H).status_code, 202)
            elif j["state"] in ("done", "failed"):
                self.assertEqual(j["state"], "done", j)
                return j
            time.sleep(0.05)
        self.fail("first pull didn't finish")

    def counts(self):
        db = dbm.connect()
        try:
            return {t: db.execute(f"SELECT COUNT(*) FROM {t} WHERE profile_id=?", (self.pid,)).fetchone()[0]
                    for t in ("imports", "events", "series", "results")}
        finally:
            db.close()

    def data(self):
        return self.robin.get(f"/api/profiles/{self.pid}/data").json()

    # ---------------------------------------------------------------- sign-in (#32)

    def test_sign_in_happy_path(self):
        sid = self.source()
        url = self.start(sid)
        p = q(url)
        self.assertEqual((p["response_type"], p["code_challenge_method"], p["aud"], p["redirect_uri"]),
                         ("code", "S256", self.fake.fhir_base, RETURN))
        self.assertGreaterEqual(len(p["state"]), 43)   # 256 random bits
        back = self.fake.authorize(url, patient="pat-1")
        r = self.back(back)
        self.assertEqual(r.status_code, 200, r.text)
        c = self.row(r.json()["connection"])
        self.assertEqual((c["state"], c["credential"], c["profile_id"]), ("active", "refresh", self.pid))
        self.assertEqual(crypto.dec(c["patient"]), "pat-1")
        listed = self.robin.get(f"/api/profiles/{self.pid}/connections").json()
        self.assertEqual([x["phase"] for x in listed["connections"]], ["first pull"])
        self.assertTrue(listed["can_connect"])
        db = dbm.connect()
        self.assertIsNone(db.execute("SELECT 1 FROM connect_pending WHERE profile_id=?", (self.pid,)).fetchone())
        db.close()

    def test_get_start_redirects_to_the_vendor(self):
        sid = self.source()
        r = self.robin.get(f"/api/profiles/{self.pid}/connect/start?source={sid}", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith(self.fake.authorize_url + "?"))

    def test_pkce_mismatch_is_refused(self):
        sid = self.source()
        other = smart.pkce()[1]
        tamper = lambda url: url.replace("code_challenge=" + q(url)["code_challenge"], "code_challenge=" + other)
        url = self.start(sid)
        r = self.back(self.fake.authorize(tamper(url)))
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("didn't accept", r.json()["detail"])
        self.assertEqual(self.robin.get(f"/api/profiles/{self.pid}/connections").json()["connections"], [])

    def test_forged_and_replayed_state_are_refused(self):
        sid = self.source()
        url = self.start(sid)
        back = self.fake.authorize(url)
        forged = type(back)(back.status, back.location)
        forged.code, forged.state = back.code, "forged-" + back.state[7:]
        self.assertEqual(self.back(forged).status_code, 400)
        self.assertEqual(self.back(back).status_code, 200)   # the real one still works
        again = self.back(back)                              # replayed
        self.assertEqual(again.status_code, 400)
        self.assertIn("used already", again.json()["detail"])
        # started in one session, returned in another (a link someone else sent): refused
        url = self.start(sid)
        back = self.fake.authorize(url)
        r = self.back(back, c=self.lee)
        self.assertEqual(r.status_code, 400)
        self.assertIn("another browser session", r.json()["detail"])

    def test_wrong_aud_and_wrong_issuer_are_refused(self):
        sid = self.source()
        tamper = lambda url: url.replace(urllib.parse.quote(self.fake.fhir_base, safe=""), urllib.parse.quote("http://elsewhere.test/fhir", safe=""))
        url = self.start(sid)
        r = self.back(self.fake.authorize(tamper(url)))
        self.assertEqual(r.status_code, 400)
        self.assertIn("aud", r.json()["detail"])
        self.assertEqual(self.robin.get(f"/api/profiles/{self.pid}/connections").json()["connections"], [])
        # a token saying it's for another server
        url = self.start(sid)
        back = self.fake.authorize(url)
        real = smart.exchange_code
        with mock.patch.object(smart, "exchange_code", lambda *a: {**real(*a), "aud": "http://elsewhere.test/fhir"}):
            r = self.back(back)
        self.assertEqual(r.status_code, 400)
        self.assertIn("another server", r.json()["detail"])
        # discovery naming another issuer
        self.fake.smart_config = {"issuer": "http://elsewhere.test/fhir"}
        r = self.robin.post(f"/api/profiles/{self.pid}/connect/start", json={"source": sid}, headers=H)
        self.assertEqual(r.status_code, 502)
        self.assertIn("issuer", r.json()["detail"])

    def test_declined_at_the_vendor(self):
        sid = self.source()
        r = self.back(self.fake.authorize(self.start(sid), approve=False))
        self.assertEqual(r.status_code, 400)
        self.assertIn("didn't allow", r.json()["detail"])

    def test_confidential_client(self):
        sid = self.source("confidential", secret="fictional-secret-1")
        cid = self.connect(sid)
        self.assertEqual(self.row(cid)["credential"], "refresh")
        token_calls = [r for r in self.fake.requests if r.path == "/oauth2/token"]
        self.assertTrue(token_calls and all(r.authorization for r in token_calls))   # Basic auth with the secret
        # a wrong secret: the vendor refuses, nothing connected
        r = self.robin.patch(f"/api/connect/sources/{sid}", json={"secret": "wrong"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        bad = self.back(self.fake.authorize(self.start(sid)))
        self.assertEqual(bad.status_code, 400)
        self.assertIn("client ID or secret", bad.json()["detail"])

    def test_epic_dynamic_registration(self):
        self.fake.dynamic_registration = True
        sid = self.source("dynamic")
        cid = self.connect(sid)
        c = self.row(cid)
        self.assertEqual(c["credential"], "jwt")
        cred = json.loads(crypto.dec(c["secret"]))
        self.assertTrue(cred["dyn_client"].startswith("dyn-") and "PRIVATE KEY" in cred["key"])
        self.assertTrue(any(r.path == "/oauth2/register" for r in self.fake.requests))
        self.first_pull(cid)
        self.fake.expire_access_tokens()   # a new access token comes from a JWT assertion signed with the install's key
        self.fake.add(NEW_OBS, patient="pat-1")
        res = self.pull(cid)
        self.assertTrue(res["ok"], res)
        self.assertGreaterEqual(res["counts"]["results"], 1)

    def test_viewers_cant_connect(self):
        sid = self.source()
        cid = self.connect(sid)
        r = self.kim.post(f"/api/profiles/{self.pid}/connect/start", json={"source": sid}, headers=H)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.kim.get(f"/api/profiles/{self.pid}/connect/start?source={sid}", follow_redirects=False).status_code, 403)
        listed = self.kim.get(f"/api/profiles/{self.pid}/connections")
        self.assertEqual(listed.status_code, 200)
        self.assertFalse(listed.json()["can_connect"])
        for path in ("sync", "reconnect", "disconnect"):
            self.assertEqual(self.kim.post(f"/api/profiles/{self.pid}/connections/{cid}/{path}", headers=H).status_code, 403)
        self.assertEqual(self.lee.get(f"/api/profiles/{self.pid}/connections").status_code, 404)   # not shared
        self.assertEqual(self.kim.get("/api/connect/sources").status_code, 403)                  # not an admin
        # a viewer can't finish a sign-in someone started either (their session doesn't match)
        back = self.fake.authorize(self.start(sid))
        self.assertEqual(self.back(back, c=self.kim).status_code, 400)

    def test_unknown_source_and_directory_issuer(self):
        r = self.robin.post(f"/api/profiles/{self.pid}/connect/start", json={"issuer": "https://not-set-up.example/fhir"}, headers=H)
        self.assertEqual(r.status_code, 409)
        self.assertIn("isn't set up", r.json()["detail"])
        self.source()
        r = self.robin.post(f"/api/profiles/{self.pid}/connect/start", json={"issuer": self.fake.fhir_base + "/"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)   # #93's "Connect" sends a directory entry's FHIR base

    # ---------------------------------------------------------------- pulls (#91)

    def test_first_pull_through_preview_and_save(self):
        cid = self.connect(self.source())
        before = self.counts()
        j = self.first_pull(cid)
        self.assertEqual(j["importer"], "fhir")
        after = self.counts()
        self.assertGreater(after["events"], before["events"])
        self.assertGreater(after["results"], 0)
        c = self.row(cid)
        self.assertIsNotNone(c["cursor"])
        self.assertIsNotNone(c["next_sync"])
        hist = self.robin.get(f"/api/profiles/{self.pid}/imports").json()["imports"]
        self.assertEqual(hist[0]["connection"], c["source"])
        listed = self.robin.get(f"/api/profiles/{self.pid}/connections").json()["connections"][0]
        self.assertEqual(listed["phase"], "syncing")
        self.assertEqual(listed["report"]["types"]["Observation"]["status"], "ok")
        docs = [e for e in self.data()["events"] if e["k"] == "document"]
        self.assertTrue(docs, "the DocumentReference's Binary came in as a document")

    def test_incremental_pull_adds_only_new_and_changed(self):
        cid = self.connect(self.source())
        self.first_pull(cid)
        before = self.counts()
        self.fake.clear_requests()
        res = self.pull(cid)   # nothing new
        self.assertTrue(res["ok"], res)
        self.assertIsNone(res["import"])
        self.assertEqual(self.counts(), before)
        searches = [r for r in self.fake.requests if r.path == "/fhir/Observation"]
        self.assertTrue(searches and all(r.query.get("_lastUpdated", "").startswith("gt") for r in searches), searches)

        enc = next(r for r in self.fake.resources("Encounter", patient="pat-1") if r.get("status") != "planned")
        self.fake.update("Encounter", enc["id"], {"type": [{"text": "Follow-up visit (renamed at the vendor)"}]})
        title_before = {e["id"]: e for e in self.data()["events"]}
        self.fake.add(NEW_OBS, patient="pat-1")
        res = self.pull(cid)
        self.assertTrue(res["ok"], res)
        self.assertIsNotNone(res["import"])
        self.assertEqual(res["counts"]["results"], 1)    # just the new glucose
        self.assertGreaterEqual(res["changed"], 1)       # the encounter, in place
        after = self.counts()
        self.assertEqual(after["results"], before["results"] + 1)
        self.assertEqual(after["imports"], before["imports"] + 1)
        changed = [e for e in self.data()["events"] if e["id"] in title_before and e != title_before[e["id"]]]
        self.assertTrue(changed, "an existing event changed in place, keeping its id")

        # the sync is in the history, and undoing it puts things back
        hist = self.robin.get(f"/api/profiles/{self.pid}/imports").json()["imports"]
        self.assertEqual((hist[0]["origin"], hist[0]["by"]), ("sync", "scheduled sync"))
        r = self.robin.post(f"/api/profiles/{self.pid}/imports/{res['import']}/undo", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.counts(), before)
        self.assertEqual({e["id"]: e for e in self.data()["events"]}, title_before)
        # undone records don't come back by themselves at the next pull
        self.assertIsNone(self.pull(cid)["import"])

    def test_paging(self):
        self.fake.page_size = self.fake.max_count = 2
        cid = self.connect(self.source())
        self.first_pull(cid)
        offsets = [r for r in self.fake.requests if r.path.startswith("/fhir/") and "_offset" in r.query]
        self.assertTrue(offsets, "followed next links")
        report = self.row(cid)
        n = json.loads(crypto.dec(report["report"]))["types"]["Observation"]["n"]
        self.assertEqual(n, len(self.fake.resources("Observation", patient="pat-1")))

    def test_429_backoff_and_vendor_unavailable(self):
        cid = self.connect(self.source())
        self.fake.fail(429, retry_after=3, times=1, path="/fhir/Observation")
        self.first_pull(cid)
        self.assertIn(3, self.sleeps)   # waited as Retry-After said, then went on
        self.assertTrue(any(r.status == 429 for r in self.fake.requests))
        # down for longer than the retries: "vendor unavailable", tried later, still connected
        self.fake.fail(503, times=None, path="/fhir/")
        res = self.pull(cid)
        self.assertEqual(res["kind"], "unavailable")
        c = self.row(cid)
        self.assertEqual((c["state"], c["error_kind"], c["failures"]), ("active", "unavailable", 1))
        self.assertGreater(c["next_sync"], dbm.now() + 200)
        self.assertLessEqual(len(self.sleeps), 1 + smart.TRIES)   # a cap on the retries
        self.fake.clear_faults()
        self.assertTrue(self.pull(cid)["ok"])
        self.assertEqual(self.row(cid)["failures"], 0)

    def test_refresh_rotation(self):
        self.fake.refresh = "rotate"
        cid = self.connect(self.source())
        self.first_pull(cid)
        old = json.loads(crypto.dec(self.row(cid)["secret"]))["refresh"]
        self.fake.expire_access_tokens()
        self.assertTrue(self.pull(cid)["ok"])
        new = json.loads(crypto.dec(self.row(cid)["secret"]))["refresh"]
        self.assertNotEqual(old, new)
        self.assertTrue(self.fake.token_info(old).rotated)
        self.fake.expire_access_tokens()
        self.assertTrue(self.pull(cid)["ok"])   # and the rotated one works in turn

    def test_access_ended_needs_reconnect(self):
        cid = self.connect(self.source())
        self.first_pull(cid)
        self.fake.revoke_all("pat-1")   # the patient ended access in their portal
        self.fake.expire_access_tokens()
        res = self.pull(cid)
        self.assertEqual(res["kind"], "access")
        c = self.row(cid)
        self.assertEqual((c["state"], c["error_kind"], c["next_sync"]), ("needs reconnect", "access", None))
        listed = self.robin.get(f"/api/profiles/{self.pid}/connections").json()["connections"][0]
        self.assertEqual(listed["phase"], "needs reconnect")
        self.assertEqual(self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/sync", headers=H).status_code, 409)
        # reconnecting signs in again into the same connection, and pulls go on from where they were
        r = self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/reconnect", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        back = self.back(self.fake.authorize(r.json()["url"], patient="pat-1"))
        self.assertEqual(back.json(), {"profile": self.pid, "connection": cid, "reconnected": True})
        self.assertEqual(self.row(cid)["state"], "active")
        self.assertTrue(self.pull(cid)["ok"])

    def test_expired_refresh_token_needs_reconnect(self):
        self.fake.refresh_ttl = 60
        cid = self.connect(self.source())
        self.first_pull(cid)
        self.fake.advance(120)
        self.fake.expire_access_tokens()
        self.assertEqual(self.pull(cid)["kind"], "access")
        self.assertEqual(self.row(cid)["state"], "needs reconnect")

    def test_no_refresh_token_needs_reconnect_when_access_runs_out(self):
        self.fake.offline_for_public = False
        cid = self.connect(self.source())
        self.assertEqual(self.row(cid)["credential"], "access")
        self.first_pull(cid)
        db = dbm.connect()
        db.execute("UPDATE connections SET expires=0 WHERE id=?", (cid,))
        db.close()
        self.assertEqual(self.pull(cid)["kind"], "access")

    def test_disconnect_revokes_and_keeps_or_removes_records(self):
        sid = self.source()
        cid = self.connect(sid)
        self.first_pull(cid)
        kept = self.counts()
        refresh = json.loads(crypto.dec(self.row(cid)["secret"]))["refresh"]
        r = self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/disconnect", json={"remove_records": False}, headers=H)
        self.assertEqual(r.json(), {"revoked": True, "removed": 0})
        self.assertTrue(any(x.path == "/oauth2/revoke" for x in self.fake.requests))
        self.assertTrue(self.fake.token_info(refresh).grant.revoked)
        c = self.row(cid)
        self.assertEqual((c["state"], c["secret"], c["patient"]), ("disconnected", None, None))
        self.assertFalse(os.path.exists(sync.store_path(self.pid, cid)))
        self.assertEqual(self.counts(), kept)   # records kept
        # another connection, disconnected with its records removed
        cid2 = self.connect(sid, patient="pat-1")
        self.assertNotEqual(cid2, cid)
        self.fake.add(NEW_OBS, patient="pat-1")
        self.first_pull(cid2)   # the first connection's records are duplicates; the new result comes in
        self.assertEqual(self.counts()["results"], kept["results"] + 1)
        r = self.robin.post(f"/api/profiles/{self.pid}/connections/{cid2}/disconnect", json={"remove_records": True}, headers=H)
        self.assertEqual(r.json()["removed"], 1)
        self.assertEqual(self.counts(), kept)

    def test_tokens_encrypted_at_rest(self):
        sid = self.source("confidential", secret="fictional-secret-2")
        url = self.start(sid)
        raw = sqlite3.connect(os.path.join(TMP, "sanguity.db"))
        verifier = raw.execute("SELECT verifier FROM connect_pending").fetchone()[0]
        self.assertTrue(verifier.startswith(crypto.FIELD))
        self.assertEqual(self.back(self.fake.authorize(url)).status_code, 200)
        cid = raw.execute("SELECT MAX(id) FROM connections").fetchone()[0]
        self.first_pull(cid)
        secret, patient = raw.execute("SELECT secret, patient FROM connections WHERE id=?", (cid,)).fetchone()
        cred = json.loads(crypto.dec(secret))
        for value in (secret, patient):
            self.assertTrue(value.startswith(crypto.FIELD))
        dump = "\n".join(raw.iterdump())
        for plain in (cred["access"], cred["refresh"], "fictional-secret-2"):
            self.assertNotIn(plain, dump)
        raw.close()
        self.assertTrue(crypto.file_is_encrypted(sync.store_path(self.pid, cid)))

    def test_nothing_contacts_another_host(self):
        hosts = set()
        real = http.client.HTTPConnection.__init__

        def spy(conn, host, port=None, *a, **k):
            hosts.add(f"{host}:{port}")
            real(conn, host, port, *a, **k)
        with mock.patch.object(http.client.HTTPConnection, "__init__", spy):
            cid = self.connect(self.source())
            self.first_pull(cid)
            self.fake.add(NEW_OBS, patient="pat-1")
            self.pull(cid)
            self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/disconnect", headers=H)
        own = f"127.0.0.1:{self.fake.port}"
        self.assertEqual(hosts, {own})
        self.assertEqual(self.fake.hosts(), {own})
        # each host contacted is recorded for the profile (#97)
        log = self.robin.get(f"/api/profiles/{self.pid}/activity?action=outside").json()
        rows = log if isinstance(log, list) else log.get("entries", log.get("items", []))
        self.assertTrue(rows and {e["detail"]["host"] for e in rows} == {own}, log)
        # a "next" link to another host is refused rather than followed
        http_ = smart.Client({own})
        with self.assertRaises(smart.Refused):
            http_.request("GET", "http://127.0.0.2:9/fhir/Observation")

    def test_endpoints_moved_since_tested_are_refused(self):
        sid = self.source()
        self.fake.smart_config = {"token_endpoint": "http://127.0.0.2:9/oauth2/token"}
        r = self.robin.post(f"/api/profiles/{self.pid}/connect/start", json={"source": sid}, headers=H)
        self.assertEqual(r.status_code, 409)
        self.assertIn("test it again", r.json()["detail"])

    def test_scheduled_sync_runs_by_itself(self):
        cid = self.connect(self.source())
        self.first_pull(cid)
        self.fake.add(NEW_OBS, patient="pat-1")
        db = dbm.connect()
        db.execute("UPDATE connections SET next_sync=? WHERE id=?", (dbm.now() - 1, cid))
        db.close()
        with mock.patch.object(sync, "TICK", 0.2):
            REAL_START()
            try:
                end = time.time() + 20
                while time.time() < end and self.row(cid)["next_sync"] <= dbm.now():
                    time.sleep(0.1)
            finally:
                sync.stop()
        self.assertGreater(self.row(cid)["next_sync"], dbm.now() + 3600)
        self.assertEqual(self.counts()["imports"], 2)

    # ---------------------------------------------------------------- the setup screen

    def test_setup_screen_and_local_counts(self):
        sid = self.source()
        self.connect(sid)
        s = self.robin.get("/api/connect/sources").json()
        self.assertEqual(s["return_address"], RETURN)
        self.assertTrue(any("plain HTTP" in w for w in s["warnings"]))
        mine = next(x for x in s["sources"] if x["id"] == sid)
        self.assertEqual(mine["connections"]["active"], 1)
        self.assertNotIn("secret", json.dumps(mine).replace("has_secret", ""))
        self.assertIn("epic", s["guides"])
        self.assertTrue(conns.address_warnings("https://192.168.1.20/connect/return"))
        self.assertEqual(conns.address_warnings("https://health.example.com/connect/return"), [])
        # a source a profile is connected to can't be removed
        self.assertEqual(self.robin.delete(f"/api/connect/sources/{sid}", headers=H).status_code, 409)


if __name__ == "__main__":
    unittest.main(verbosity=2)
