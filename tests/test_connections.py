#!/usr/bin/env python3
"""Connected sources (#32 sign-in, #91 connections and sync, #99 choosing what to pull, #101 attachments, #102
warnings when a connection stops syncing), against the fake SMART on FHIR server
(tests/fakefhir). Fictional patients only; nothing real is contacted.

  python3 tests/test_connections.py
"""
import base64, http.client, json, os, sqlite3, sys, tempfile, time, unittest, urllib.parse
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

from server import accounts, connections as conns, crypto, db as dbm, main, net, profiles as prof, smart, sync   # noqa: E402
from tests.whole_records import whole   # noqa: E402  records with every record's details (#217)
from tests import netguard   # noqa: E402
from tests.fakefhir import PAYER_FIXTURES, FakeFHIR   # noqa: E402
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


def setUpModule():   # the whole run under the socket guard: only the test servers started here may be contacted (#97)
    netguard.install()


def tearDownModule():
    netguard.uninstall()
    netguard.check()


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
        # sources from earlier tests stay in the database; when the OS hands this fake a port an earlier one had, its
        # address would match theirs and adding a source would be refused as a duplicate (409): move theirs aside
        db.execute("UPDATE smart_clients SET issuer = issuer || '/earlier-test-' || id WHERE issuer = ?", (self.fake.fhir_base,))
        self.pid = prof.create_profile(db, "Robin (fictional)", owner_id=self.uids["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (self.uids["kim"], self.pid))
        db.close()
        self.sleeps.clear()

    # ---------------------------------------------------------------- helpers

    def source(self, auth="public", secret=None, scopes=None, register=True, vendor="epic"):
        cid = f"app-{auth}-{self.fake.port}"
        if register:
            self.fake.register_client(cid, [RETURN], secret=secret)
        body = {"name": f"Fake {auth} {self.fake.port}", "vendor": vendor, "issuer": self.fake.fhir_base, "client_id": cid,
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
        return whole(self.robin, self.pid)

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

    def test_records_show_when_they_were_last_synced(self):   # imports.synced, shown beside each record's source (#100)
        cid = self.connect(self.source())
        self.first_pull(cid)
        synced = lambda: {i: v["synced"] for i, v in self.data()["imports"].items()
                          if v["origin"] in ("upload", "sync") and v["synced"] is not None}
        first = synced()
        self.assertEqual(len(first), 1, self.data()["imports"])   # the first pull's import, once saved
        db = dbm.connect()
        db.execute("UPDATE imports SET synced=synced-3600 WHERE connection_id=?", (cid,))   # an hour ago
        db.close()
        res = self.pull(cid)   # nothing new: still, the records are up to date as of now
        self.assertTrue(res["ok"], res)
        self.assertIsNone(res["import"])
        now = synced()
        self.assertEqual(set(now), set(first))
        self.assertTrue(all(now[i] >= first[i] for i in now), (first, now))   # moved on, and the page sees it
        self.fake.add(NEW_OBS, patient="pat-1")
        res = self.pull(cid)
        latest = synced()
        self.assertIn(str(res["import"]), latest)   # a sync that brought records is marked too
        self.assertEqual(len(set(latest.values())), 1, latest)

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
        details = self.data()["details"]
        self.assertTrue(all(details.get(str(e["id"])) for e in changed), "and kept its details")
        new = [e for e in self.data()["events"] if e["id"] not in title_before and e["k"] == "labitem"]
        self.assertTrue(new and all(details.get(str(e["id"])) for e in new), "a new record comes with its details")

        # the sync is in the history, and undoing it puts things back
        hist = self.robin.get(f"/api/profiles/{self.pid}/imports").json()["imports"]
        self.assertEqual((hist[0]["origin"], hist[0]["by"]), ("sync", "scheduled sync"))
        r = self.robin.post(f"/api/profiles/{self.pid}/imports/{res['import']}/undo", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.counts(), before)
        self.assertEqual({e["id"]: e for e in self.data()["events"]}, title_before)
        # undone records don't come back by themselves at the next pull
        self.assertIsNone(self.pull(cid)["import"])

    def test_a_result_sent_again_is_updated_in_place(self):   # #869
        """A result the source amends comes back at the next pull: the same result, updated in place, range and flag,
        never a copy. The report's Hemoglobin 12.1 (13.5–17.5, Low) amended to 12.0–15.5 and Normal, its Platelet Count
        given a range (none, then 150–400: High), an ALT's flag changed (High with no range, then Abnormal). The pull
        updated their lab day's rows in place but added a copy of each result: two results, the row reading the first's
        Low beside the new range. Now no result is added, each is as the source sends it now, its row agreeing with it;
        undoing the pull puts back the results and the rows."""
        code = lambda c: {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation", "code": c}]}
        self.fake.add({"resourceType": "Observation", "id": "obs-alt", "status": "final", "category": [{"coding": [{"code": "laboratory"}]}],
                       "code": {"coding": [{"system": "http://loinc.org", "code": "1742-6"}], "text": "ALT"},
                       "effectiveDateTime": "2025-03-04T09:45:00-06:00", "valueQuantity": {"value": 60, "unit": "U/L"},
                       "interpretation": [code("H")]}, patient="pat-1")
        self.fake.update("Observation", "obs-plt", {"referenceRange": None})   # its range given at the next pull
        cid = self.connect(self.source())
        self.first_pull(cid)
        before, n = self.data(), self.counts()

        def said(data):   # each lab's results, and its rows' ranges as printed and flags as read, by the row's result
            from server import crossmatch, ranges
            drawer, out = crossmatch._rows(data), {}
            for s in data["labs"]:
                if s["name"] in ("Hemoglobin", "Platelet Count", "ALT"):
                    for p in s["pts"]:
                        at = crossmatch._row_of(drawer, s, p)
                        row = at and data["details"][str(at[0])]["rows"][at[1]]
                        out.setdefault(s["name"], []).append([p[2], p[3], p[4], row and row[3], row and ranges.read_flag(row[4], p[1], p[2], p[3], p[5])])
            return out
        self.assertEqual(said(before), {"Hemoglobin": [[13.5, 17.5, "L", "13.5 - 17.5", "L"]], "Platelet Count": [[None, None, "", "", ""]],
                                        "ALT": [[None, None, "H", "", "H"]]})
        self.fake.update("Observation", "obs-hgb", {"referenceRange": [{"low": {"value": 12.0, "unit": "g/dL"}, "high": {"value": 15.5, "unit": "g/dL"},
                                                                        "text": "12.0-15.5"}], "interpretation": [code("N")]})
        self.fake.update("Observation", "obs-plt", {"referenceRange": [{"low": {"value": 150}, "high": {"value": 400}}]})
        self.fake.update("Observation", "obs-alt", {"interpretation": [code("A")]})
        res = self.pull(cid)
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["counts"]["results"], res["counts"].get("updated")), (0, 3), res)
        self.assertEqual({k: v for k, v in self.counts().items() if k != "imports"}, {k: v for k, v in n.items() if k != "imports"}, "no result added")
        self.assertEqual(said(self.data()), {"Hemoglobin": [[12.0, 15.5, "", "12.0-15.5", ""]], "Platelet Count": [[150, 400, "H", "150 - 400", "H"]],
                                             "ALT": [[None, None, "A", "", "A"]]}, "each as sent now, its row agreeing")
        # amended again (its report's Hemoglobin and Platelet Count one record), then the first pull undone: what the later
        # one changed again keeps its version (sync.unapply), the rest as before; then the later one: all as before
        self.fake.update("Observation", "obs-hgb", {"referenceRange": [{"low": {"value": 12.0, "unit": "g/dL"}, "high": {"value": 15.0, "unit": "g/dL"},
                                                                        "text": "12.0-15.0"}]})
        again = self.pull(cid)
        self.assertEqual((again["counts"]["results"], again["counts"].get("updated")), (0, 1), again)
        undo = lambda iid: self.robin.post(f"/api/profiles/{self.pid}/imports/{iid}/undo", headers=H)
        self.assertEqual(undo(res["import"]).status_code, 200)
        self.assertEqual(said(self.data()), {"Hemoglobin": [[12.0, 15.0, "", "12.0-15.0", ""]], "Platelet Count": [[150, 400, "H", "150 - 400", "H"]],
                                             "ALT": [[None, None, "H", "", "H"]]}, "the report as the later pull left it, the ALT as before")
        self.assertEqual(undo(again["import"]).status_code, 200)
        self.assertEqual(said(self.data()), said(before), "both undone: as they were")
        self.assertEqual(self.counts(), n)

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

    def test_a_disconnect_during_a_pull_leaves_nothing_it_read(self):   # #591
        """Disconnect (keeping nothing) while a pull is reading, the first or a later one, at whichever step: what it
        read isn't kept in the connection's store, nor added to the records, once the pull ends."""
        sid = self.source()
        before = self.counts()
        for first, step in ((True, "_merge_store"), (False, "_merge_store"), (False, "_upload")):
            with self.subTest(first=first, step=step):
                cid = self.connect(sid)
                if not first:
                    self.first_pull(cid)
                    self.fake.add({**NEW_OBS, "id": f"obs-591-{step}"}, patient="pat-1")
                real = getattr(sync, step)

                def then_disconnect(*a, **k):   # the network reads are done; the person clicks Disconnect now
                    r = self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/disconnect", json={"remove_records": True}, headers=H)
                    self.assertEqual(r.status_code, 200, r.text)
                    return real(*a, **k)
                with mock.patch.object(sync, step, then_disconnect):
                    res = self.pull(cid)
                self.assertFalse(res["ok"], res)
                self.assertEqual(self.row(cid)["state"], "disconnected")
                self.assertFalse(os.path.exists(sync.store_path(self.pid, cid)))
                self.assertEqual(self.counts(), before)
                db = dbm.connect()
                try:   # no review left waiting either
                    self.assertFalse(db.execute("SELECT 1 FROM jobs WHERE profile_id=? AND state IN ('queued', 'running', 'preview')",
                                                (self.pid,)).fetchone())
                finally:
                    db.close()
        # disconnected while its first pull waits for its review: the review goes with it, and what it read
        cid = self.connect(sid)
        jid = self.pull(cid)["job"]
        end = time.time() + 30
        while self.robin.get(f"/api/profiles/{self.pid}/jobs/{jid}").json()["state"] != "preview":
            self.assertLess(time.time(), end)
            time.sleep(0.05)
        self.assertTrue(os.path.exists(sync.store_path(self.pid, cid)))
        r = self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/disconnect", json={"remove_records": True}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        j = self.robin.get(f"/api/profiles/{self.pid}/jobs/{jid}").json()
        self.assertEqual((j["state"], j["samples"]), ("discarded", None))
        self.assertFalse(os.path.exists(sync.store_path(self.pid, cid)))
        self.assertEqual(self.counts(), before)

    def test_a_disconnect_at_any_point_of_a_first_pull_leaves_nothing_from_it(self):   # #592
        """Disconnect, keeping its records or not, at each point of a first pull: as its job is made, while it's queued,
        while it's read, while it waits for its review, while it's saved, and with the review confirmed afterwards. No
        review or job is left going, no records or store from it, and its upload is gone."""
        import threading
        from server import jobs, labmap
        sid = self.source()
        before = self.counts()

        def job_state(jid, settle=True):
            end = time.time() + 30
            while True:
                j = self.robin.get(f"/api/profiles/{self.pid}/jobs/{jid}").json()
                if not settle or j["state"] not in ("queued", "running") or time.time() > end:
                    return j["state"]
                time.sleep(0.05)

        def until_preview(jid):
            end = time.time() + 30
            while job_state(jid, settle=False) != "preview":
                self.assertLess(time.time(), end)
                time.sleep(0.05)

        def disconnect(cid, remove):
            r = self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/disconnect", json={"remove_records": remove}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)

        for remove in (True, False):
            for point in ("made", "queued", "reading", "review", "saving", "confirmed after"):
                with self.subTest(point=point, remove_records=remove):
                    cid = self.connect(sid)
                    later = []
                    if point == "made":   # the click lands just as the pull makes its job (#592: between it and first_job)
                        real = jobs.submit

                        def made(*a, **k):
                            t = threading.Thread(target=disconnect, args=(cid, remove))
                            t.start()
                            later.append(t)
                            time.sleep(0.3)   # it's waiting for the pull's write lock, or done without one
                            return real(*a, **k)
                        with mock.patch.object(jobs, "submit", made):
                            res = self.pull(cid)
                        later[0].join(30)
                    elif point in ("queued", "reading"):
                        name = "run" if point == "queued" else "_read"
                        real = getattr(jobs, name)

                        def then(*a, real=real, **k):
                            if not later:
                                later.append(1)
                                disconnect(cid, remove)
                            return real(*a, **k)
                        with mock.patch.object(jobs, name, then):
                            res = self.pull(cid)
                            job_state(res["job"])
                    else:
                        res = self.pull(cid)
                        until_preview(res["job"])
                        if point == "review":
                            disconnect(cid, remove)
                        elif point == "saving":   # confirmed; Disconnect as the worker starts to save it
                            real = labmap.recheck

                            def then(*a, **k):
                                if not later:
                                    later.append(1)
                                    disconnect(cid, remove)
                                return real(*a, **k)
                            with mock.patch.object(jobs.labmap, "recheck", then):
                                self.assertEqual(self.robin.post(f"/api/profiles/{self.pid}/jobs/{res['job']}/confirm",
                                                                 headers=H).status_code, 202)
                                job_state(res["job"])
                        else:
                            disconnect(cid, remove)
                            r = self.robin.post(f"/api/profiles/{self.pid}/jobs/{res['job']}/confirm", headers=H)
                            self.assertEqual(r.status_code, 409, r.text)
                    jid = res.get("job") or self.row(cid)["first_job"]
                    self.assertEqual(self.row(cid)["state"], "disconnected")
                    if jid:
                        self.assertIn(job_state(jid), ("discarded", "failed"))
                        db = dbm.connect()
                        try:
                            up = db.execute("SELECT u.state FROM jobs j JOIN uploads u ON u.id=j.upload_id WHERE j.id=?", (jid,)).fetchone()
                        finally:
                            db.close()
                        self.assertEqual(up[0], "discarded", "the pull's upload is removed")
                    self.assertEqual(self.counts(), before)
                    self.assertFalse(os.path.exists(sync.store_path(self.pid, cid)))
                    db = dbm.connect()
                    try:
                        self.assertFalse(db.execute("SELECT 1 FROM jobs WHERE profile_id=? AND state IN ('queued', 'running', 'preview')",
                                                    (self.pid,)).fetchone())
                        self.assertFalse(db.execute("SELECT 1 FROM imports WHERE connection_id=?", (cid,)).fetchone())
                    finally:
                        db.close()

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
        http_ = smart.Client(net.Permit("test", {own}))
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

    # ---------------------------------------------------------------- insurers' patient-access APIs (#92)

    def test_insurer_claims_on_costs_incrementally(self):
        """A payer's CARIN Blue Button API (the fake, standing in for Blue Button's sandbox: a confidential client)
        brings claims onto the Costs tab; a later sync adds only new claims and marks an adjusted claim's original."""
        self.fake.stop()
        self.fake = FakeFHIR(fixtures=PAYER_FIXTURES).start()
        self.addCleanup(self.fake.stop)
        sid = self.source(auth="confidential", secret="fictional-payer-secret", vendor="insurer")
        mine = next(x for x in self.robin.get("/api/connect/sources").json()["sources"] if x["id"] == sid)
        self.assertEqual(mine["scopes"], conns.default_scopes(vendor="insurer"))
        url = self.start(sid)
        asked = q(url)["scope"].split()
        self.assertLessEqual({"patient/ExplanationOfBenefit.read", "patient/Coverage.read", "patient/Patient.read"}, set(asked))
        self.assertFalse([x for x in asked if "Observation" in x or "*" in x])
        r = self.back(self.fake.authorize(url, patient="mem-1"))
        self.assertEqual(r.status_code, 200, r.text)
        cid = r.json()["connection"]
        self.fake.clear_requests()
        self.first_pull(cid)

        # only what the scopes cover was asked for; Coverage by beneficiary
        fhir_paths = {x.path for x in self.fake.requests if x.path.startswith("/fhir/") and x.path != "/fhir/metadata"
                      and ".well-known" not in x.path}
        self.assertEqual(fhir_paths, {"/fhir/Patient/mem-1", "/fhir/ExplanationOfBenefit", "/fhir/Coverage"})
        self.assertTrue(all(x.status == 200 for x in self.fake.requests if x.path.startswith("/fhir/")))
        cov = next(x for x in self.fake.requests if x.path == "/fhir/Coverage")
        self.assertEqual(cov.query.get("beneficiary"), "mem-1")
        report = json.loads(crypto.dec(self.row(cid)["report"]))
        self.assertEqual(set(report["types"]), {"Patient", "ExplanationOfBenefit", "Coverage"})
        self.assertEqual(report["types"]["ExplanationOfBenefit"], {"n": 5, "status": "ok"})

        def costs():
            return {c["title"] + "|" + c["d"]: c for c in self.data()["costs"]}
        first = costs()
        self.assertEqual(len(first), 5)
        self.assertEqual({c["ins"] for c in first.values()}, {"Lakeview Health Plan (fictional)"})
        self.assertEqual(sorted(c["status"] for c in first.values()), ["Denied", "Paid", "Paid", "Paid", "Paid"])
        self.assertEqual(first["Office visit, established patient|2026-01-06"]["owe"], 28.0)
        self.assertEqual({c["kind"] for c in first.values()}, {"medical", "pharmacy"})

        # nothing new: nothing added
        self.assertIsNone(self.pull(cid)["import"])

        # the plan adjusts a claim and a new fill comes in: the next sync adds those two, and the original is replaced
        adjusted = json.loads(json.dumps(self.fake.get("ExplanationOfBenefit", "carrier-mem-1001")))
        adjusted.pop("meta", None)
        adjusted.update({"id": "carrier-mem-1001a",
                         "identifier": [{"system": "https://lakeview.example/fictional/claim-id", "value": "LHP-1001-A1"}],
                         "total": [{"category": {"coding": [{"code": "submitted"}]}, "amount": {"value": 255.0}},
                                   {"category": {"coding": [{"code": "benefit"}]}, "amount": {"value": 146.5}},
                                   {"category": {"coding": [{"code": "memberliability"}]}, "amount": {"value": 8.0}}],
                         "related": [{"claim": {"identifier": {"value": "LHP-1001"}}, "relationship": {"coding": [{"code": "prior"}]}}]})
        self.fake.add(adjusted, patient="mem-1")
        refill = json.loads(json.dumps(self.fake.get("ExplanationOfBenefit", "pde-mem-3001")))
        refill.pop("meta", None)
        refill.update({"id": "pde-mem-3002", "identifier": [{"value": "LHP-3002"}],
                       "billablePeriod": {"start": "2026-02-08", "end": "2026-02-08"}})
        refill["item"][0]["servicedDate"] = "2026-02-08"
        self.fake.add(refill, patient="mem-1")
        before_ids = {c["id"] for c in self.data()["costs"]}
        self.fake.clear_requests()
        res = self.pull(cid)
        self.assertTrue(res["ok"], res)
        eob_q = next(x for x in self.fake.requests if x.path == "/fhir/ExplanationOfBenefit")
        self.assertTrue(eob_q.query.get("_lastUpdated", "").startswith("gt"))   # incremental
        self.assertEqual(res["counts"]["kinds"], {"claim": 1, "rx": 1})          # only the new ones
        self.assertEqual(res["changed"], 1)                                      # the original, in place
        after = costs()
        self.assertEqual(len(self.data()["costs"]), 7)
        self.assertEqual(len([c for c in self.data()["costs"] if c["id"] not in before_ids]), 2)
        replaced = [c for c in self.data()["costs"] if c["status"] == "Replaced"]
        self.assertEqual(len(replaced), 1)
        self.assertEqual((replaced[0]["paid"], replaced[0]["owe"]), (0.0, 0.0))
        self.assertIn(replaced[0]["id"], before_ids)                              # same row, updated
        self.assertEqual(round(sum(c["paid"] for c in self.data()["costs"]), 2),
                         round(sum(c["paid"] for c in first.values()) - 126.5 + 146.5 + 27.4, 2))
        self.assertIn("Atorvastatin Calcium 20mg tablet|2026-02-08", after)

        # undoing the sync puts the original's Costs row back
        r = self.robin.post(f"/api/profiles/{self.pid}/imports/{res['import']}/undo", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(costs(), first)

    def test_granted_scopes(self):
        self.assertTrue(conns.granted("launch/patient patient/ExplanationOfBenefit.read", "ExplanationOfBenefit"))
        self.assertFalse(conns.granted("launch/patient patient/ExplanationOfBenefit.read", "Observation"))
        self.assertTrue(conns.granted("patient/*.rs", "Coverage"))
        self.assertFalse(conns.granted("patient/Coverage.r", "Coverage"))        # read by id only, no search
        self.assertTrue(conns.granted("patient/Coverage.rs?category=x", "Coverage"))
        self.assertTrue(conns.granted("launch/patient offline_access", "Observation"))   # no resource scopes named
        self.assertTrue(conns.granted(None, "Observation"))

    # ---------------------------------------------------------------- choosing what a connection pulls (#99)

    def listed(self, cid, c=None):
        return next(x for x in (c or self.robin).get(f"/api/profiles/{self.pid}/connections").json()["connections"] if x["id"] == cid)

    def patch(self, cid, body, c=None):
        return (c or self.robin).patch(f"/api/profiles/{self.pid}/connections/{cid}", json=body, headers=H)

    def fhir_paths(self):
        return [r for r in self.fake.requests if r.path.startswith("/fhir/") and not r.path.startswith("/fhir/.well-known")]

    def test_choosing_kinds_asks_for_less_and_pulls_only_those(self):
        sid = self.source()
        offered = {k["id"]: k for k in self.robin.get(f"/api/profiles/{self.pid}/connections").json()["sources"][0]["kinds"]}
        self.assertTrue(all(k["allowed"] for k in offered.values()), offered)   # the default scopes cover every kind
        self.assertIn("reference ranges", offered["labs"]["brings"])
        r = self.robin.post(f"/api/profiles/{self.pid}/connect/start", headers=H,
                            json={"source": sid, "kinds": ["medications", "labs"], "since": "2025-01-01"})
        self.assertEqual(r.status_code, 200, r.text)
        scopes = q(r.json()["url"])["scope"].split()
        types = {s.split("/")[1].split(".")[0] for s in scopes if s.startswith("patient/")}
        self.assertEqual(types, {"Patient", "Observation", "DiagnosticReport", "MedicationRequest"}, scopes)
        self.assertIn("offline_access", scopes)
        back = self.back(self.fake.authorize(r.json()["url"], patient="pat-1"))
        cid = back.json()["connection"]
        c = self.listed(cid)
        self.assertEqual((c["chosen"], c["since"]), (["labs", "medications"], "2025-01-01"))
        self.assertFalse({k["id"]: k["allowed"] for k in c["kinds"]}["visits"])   # not granted, so not offered
        self.fake.clear_requests()
        self.first_pull(cid)
        searched = {(r.path, r.query.get("category")) for r in self.fhir_paths() if r.path != "/fhir/Patient/pat-1"}
        self.assertEqual(searched, {("/fhir/Observation", "laboratory"), ("/fhir/DiagnosticReport", None),
                                    ("/fhir/MedicationRequest", None)}, searched)
        self.assertTrue(all(r.query.get("date") == "ge2025-01-01" for r in self.fhir_paths()
                            if r.path in ("/fhir/Observation", "/fhir/DiagnosticReport")))
        d = self.data()
        kinds = {e["k"] for e in d["events"]}
        self.assertIn("med", kinds)
        self.assertFalse(kinds & {"visit", "stay", "document"}, kinds)
        self.assertFalse([s for s in d["labs"] if s.get("panel") == "Vitals"], "no vital signs")
        self.assertFalse(d.get("problems"), "no problems")
        self.assertTrue(self.counts()["results"])
        # a kind the access doesn't cover can't just be ticked: signing in again with it asks for its scope
        r = self.patch(cid, {"kinds": ["labs", "medications", "visits"]})
        self.assertEqual(r.status_code, 409)
        self.assertIn("Sign in again", r.json()["detail"])
        r = self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/reconnect", headers=H,
                            json={"kinds": ["labs", "medications", "visits"]})
        self.assertTrue(any(s.startswith("patient/Encounter.") for s in q(r.json()["url"])["scope"].split()))
        self.assertEqual(self.back(self.fake.authorize(r.json()["url"], patient="pat-1")).json()["reconnected"], True)
        self.assertEqual(self.listed(cid)["chosen"], ["labs", "visits", "medications"])   # in KINDS order
        # the visits come in at the next pull, from the beginning (their search is new), and the rest goes on as before
        self.fake.clear_requests()
        res = self.pull(cid)
        self.assertTrue(res["ok"], res)
        enc = [r for r in self.fhir_paths() if r.path == "/fhir/Encounter"]
        self.assertTrue(enc and "_lastUpdated" not in enc[0].query, enc)
        obs = [r for r in self.fhir_paths() if r.path == "/fhir/Observation"]
        self.assertTrue(obs and obs[0].query.get("_lastUpdated", "").startswith("gt"), obs)
        self.assertIn("visit", {e["k"] for e in self.data()["events"]})
        # a later start date: older records are left out even when the server ignores date=
        old = dict(NEW_OBS, effectiveDateTime="2024-05-01T08:00:00Z", valueQuantity={"value": 88, "unit": "mg/dL"})
        self.fake.add(old, patient="pat-1")
        self.fake.add(NEW_OBS, patient="pat-1")
        self.assertEqual(self.patch(cid, {"since": "2026-01-01"}).status_code, 200)
        before = self.counts()["results"]
        res = self.pull(cid)
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.counts()["results"], before + 1)   # the 2026 glucose, not the 2024 one
        # the change is in the activity log
        log = self.robin.get(f"/api/profiles/{self.pid}/activity?action=connect").json()
        rows = log if isinstance(log, list) else log.get("entries", log.get("items", []))
        self.assertTrue(any(e["detail"].get("change") == "options" and e["detail"].get("since") == "2026-01-01" for e in rows), rows)
        # viewers can't change it
        self.assertEqual(self.patch(cid, {"since": None}, c=self.kim).status_code, 403)
        self.assertEqual(self.patch(cid, {"kinds": []}).status_code, 400)
        self.assertEqual(self.patch(cid, {"since": "2999-01-01"}).status_code, 400)

    def test_narrowing_scopes(self):
        all_ = "launch/patient offline_access patient/*.read"
        self.assertEqual(conns.narrow_scopes(all_, ["problems"]), "launch/patient offline_access patient/Patient.read patient/Condition.read")
        # a vendor that lists only the wildcard keeps it
        self.assertEqual(conns.narrow_scopes(all_, ["problems"], {"scopes_supported": ["patient/*.read", "launch/patient"]}), all_)
        self.assertEqual(conns.narrow_scopes(all_, None), all_)
        self.assertEqual(conns.narrow_scopes("patient/Observation.rs patient/Binary.rs openid", ["documents"]), "patient/Binary.rs openid")
        self.assertTrue(conns.scope_allows("patient/Observation.rs?category=laboratory", "Observation"))
        self.assertFalse(conns.scope_allows("patient/Observation.r", "Observation"))   # read by id, but no search
        self.assertTrue(conns.kind_allowed("documents", "patient/DiagnosticReport.read"))

    def test_turning_a_kind_off_and_removing_what_it_brought(self):
        cid = self.connect(self.source())
        self.first_pull(cid)
        visits = lambda: [e for e in self.data()["events"] if e["k"] in ("visit", "stay")]
        before, had = self.counts(), len(visits())
        self.assertTrue(had)
        # turned off: future pulls don't ask for visits; what they brought stays
        kinds = [k for k in conns.KINDS if k != "visits"]
        self.assertEqual(self.patch(cid, {"kinds": kinds}).status_code, 200)
        self.fake.clear_requests()
        self.assertTrue(self.pull(cid)["ok"])
        self.assertFalse([r for r in self.fhir_paths() if r.path == "/fhir/Encounter"])
        self.assertEqual(len(visits()), had)
        # removed on request, as an entry in the import history...
        r = self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/remove", json={"kind": "visits"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(visits(), [])
        # the visit planned in the records (a planned Encounter) went too; a booked Appointment is a kind of its own (#228)
        self.assertEqual([u["key"] for u in self.data().get("upcoming") or []], ["fhir:Appointment/appt-1"])
        hist = self.robin.get(f"/api/profiles/{self.pid}/imports").json()["imports"]
        self.assertTrue(hist[0]["removal"], hist[0])
        self.assertIn("removed", hist[0]["label"])
        self.assertEqual(sum(hist[0]["counts"]["kinds"].values()), had)
        raw = sqlite3.connect(os.path.join(TMP, "sanguity.db"))
        self.assertTrue(all(v.startswith(crypto.FIELD) for (v,) in raw.execute("SELECT row FROM sync_removed")))
        raw.close()
        self.assertEqual(self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/remove", json={"kind": "visits"},
                                         headers=H).status_code, 409)   # nothing left to remove
        # ...that undoing puts back
        r = self.robin.post(f"/api/profiles/{self.pid}/imports/{hist[0]['id']}/undo", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(visits()), had)
        self.assertEqual(len(self.data().get("upcoming")), 2)
        self.assertEqual({k: v for k, v in self.counts().items() if k != "imports"}, {k: v for k, v in before.items() if k != "imports"})
        # labs work the same way, series and all
        r = self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/remove", json={"kind": "labs"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse([s for s in self.data()["labs"] if s.get("panel") != "Vitals"])
        self.assertTrue([s for s in self.data()["labs"] if s.get("panel") == "Vitals"], "vitals stay")
        self.robin.post(f"/api/profiles/{self.pid}/imports/{r.json()['import']}/undo", headers=H)
        self.assertEqual(self.counts()["results"], before["results"])
        self.assertEqual(self.kim.post(f"/api/profiles/{self.pid}/connections/{cid}/remove", json={"kind": "labs"},
                                       headers=H).status_code, 403)   # viewers can't

    # ---------------------------------------------------------------- immunizations, allergies, fills, appointments (#228)

    OLD_SCOPES = ("launch/patient offline_access patient/Patient.read patient/Observation.read patient/Condition.read "
                  "patient/MedicationRequest.read patient/Encounter.read patient/DocumentReference.read "
                  "patient/DiagnosticReport.read patient/ExplanationOfBenefit.read patient/Binary.read")   # the default before #228
    NEW_KINDS = ("appointments", "allergies", "fills", "immunizations")

    def mine(self, kinds=None):
        """What the profile has that #228's resources bring: {name: [source keys]}, each record once per key."""
        d = self.data()
        ev = lambda k, rtype: [e.get("key") for e in d["events"] if e["k"] == k and str(e.get("key")).startswith(f"fhir:{rtype}/")]
        return {"immunizations": ev("immunization", "Immunization"), "fills": ev("rx", "MedicationDispense"),
                "imaging": ev("imaging", "ImagingStudy"),
                "allergies": [x.get("key") for x in d.get("problems") or [] if x.get("type") == "Allergy"],
                "appointments": [x.get("key") for x in d.get("upcoming") or [] if str(x.get("key")).startswith("fhir:Appointment/")]}

    def upload_bundle(self, resources):
        """Upload the resources as a FHIR bundle file, as someone would from their portal's export, and save it."""
        from server import uploads
        body = json.dumps({"resourceType": "Bundle", "type": "collection",
                           "entry": [{"fullUrl": f"{self.fake.fhir_base}/{r['resourceType']}/{r['id']}", "resource": r}
                                     for r in resources]}).encode()
        db = dbm.connect()
        try:
            up = uploads.create(db, self.pid, self.uids["robin"])
            uploads.add_file(db, self.pid, up, "portal-export.json", __import__("io").BytesIO(body))
            uploads.finish(db, self.pid, up)
        finally:
            db.close()
        r = self.robin.post(f"/api/profiles/{self.pid}/uploads/{up}/import", json={}, headers=H)
        self.assertEqual(r.status_code, 202, r.text)
        jid, end = r.json()["id"], time.time() + 30
        while time.time() < end:
            j = self.robin.get(f"/api/profiles/{self.pid}/jobs/{jid}").json()
            if j["state"] == "preview":
                self.assertEqual(self.robin.post(f"/api/profiles/{self.pid}/jobs/{jid}/confirm", headers=H).status_code, 202)
            elif j["state"] in ("done", "failed"):
                self.assertEqual(j["state"], "done", j)
                return j
            time.sleep(0.05)
        self.fail("the upload didn't finish")

    def test_new_kinds_asked_for_and_pulled_as_an_upload_would(self):
        sid = self.source()
        offered = {k["id"]: k for k in self.offered(sid)}
        self.assertTrue(all(offered[k]["allowed"] for k in self.NEW_KINDS), offered)
        self.assertEqual(offered["fills"]["label"], "Pharmacy fills")
        self.assertIn("imaging studies", offered["documents"]["brings"])
        # the scopes asked for by default: each new type, v1 (.read) or v2 (.rs) as discovery says
        default = conns.default_scopes()
        for t in ("Immunization", "AllergyIntolerance", "MedicationDispense", "Appointment", "ImagingStudy"):
            self.assertIn(f"patient/{t}.read", default.split())
            self.assertIn(f"patient/{t}.rs", conns.default_scopes({"capabilities": ["permission-v2"]}).split())
            self.assertTrue(conns.scope_allows(f"patient/{t}.rs", t) and conns.scope_allows(f"patient/{t}.read", t))
        self.assertNotIn("Immunization", conns.default_scopes(vendor="insurer"))   # an insurer: claims alone, as before
        # a discovery that lists its patient scopes type by type is taken at its word; a wildcard covers them all
        listed = {"scopes_supported": ["launch/patient", "patient/Observation.read", "patient/Immunization.read"]}
        self.assertEqual(conns.default_scopes(listed), "launch/patient offline_access patient/Patient.read "
                                                       "patient/Observation.read patient/Immunization.read")
        self.assertEqual(conns.default_scopes({"scopes_supported": ["patient/*.read"]}), default)
        # choosing only the new kinds asks for only their scopes (and the patient's)
        r = self.robin.post(f"/api/profiles/{self.pid}/connect/start", headers=H, json={"source": sid, "kinds": list(self.NEW_KINDS)})
        self.assertEqual(r.status_code, 200, r.text)
        types = {s.split("/")[1].split(".")[0] for s in q(r.json()["url"])["scope"].split() if s.startswith("patient/")}
        self.assertEqual(types, {"Patient", "Appointment", "AllergyIntolerance", "MedicationDispense", "Immunization"})
        # the same records uploaded first as a portal's export file: the sync's first pull finds them already here
        pat = [x for t in ("Immunization", "AllergyIntolerance", "MedicationDispense", "Appointment", "ImagingStudy")
               for x in self.fake.resources(t, "pat-1")]
        self.assertEqual(len(pat), 5)
        self.upload_bundle(pat)
        from_file = self.mine()
        self.assertEqual(from_file, {"immunizations": ["fhir:Immunization/imm-1"], "fills": ["fhir:MedicationDispense/disp-1"],
                                     "imaging": ["fhir:ImagingStudy/img-1"], "allergies": ["fhir:AllergyIntolerance/allergy-1"],
                                     "appointments": ["fhir:Appointment/appt-1"]})
        cid = self.connect(sid)
        self.fake.clear_requests()
        self.first_pull(cid)
        searched = {r.path for r in self.fhir_paths()}
        self.assertTrue({"/fhir/Immunization", "/fhir/AllergyIntolerance", "/fhir/MedicationDispense", "/fhir/Appointment",
                         "/fhir/ImagingStudy"} <= searched, searched)
        self.assertEqual(self.mine(), from_file)   # each once: the file and the sync carry the same source keys (#96)
        d = self.data()
        allergy = next(x for x in d["problems"] if x.get("type") == "Allergy")
        self.assertEqual((allergy["name"], allergy.get("severity"), allergy.get("criticality")),
                         ("Allergy: Sulfamethoxazole (Hives)", "Moderate", "High"))
        self.assertTrue(any(x.get("type") != "Allergy" for x in d["problems"]), "the problem list's own entries came too")
        fill = next(e for e in d["events"] if e.get("key") == "fhir:MedicationDispense/disp-1")
        self.assertIn("Harbor Street Pharmacy", fill["sub"])
        # a sync on its own brings the same as the upload did: a fresh profile, the same connection's records
        db = dbm.connect()
        other = prof.create_profile(db, "Robin again (fictional)", owner_id=self.uids["robin"])
        db.close()
        was, self.pid = self.pid, other
        try:
            self.first_pull(self.connect(sid))
            self.assertEqual(self.mine(), from_file)
            # and a later pull brings a new one in, as an import of its own
            self.fake.add({"resourceType": "Immunization", "status": "completed", "occurrenceDateTime": "2026-10-20",
                           "vaccineCode": {"coding": [{"system": "http://hl7.org/fhir/sid/cvx", "code": "141"}]}}, patient="pat-1")
            res = self.pull(self.listed_ids()[0])
            self.assertTrue(res["ok"], res)
            self.assertEqual(res["counts"]["kinds"], {"immunization": 1}, res)
            self.assertEqual(len(self.mine()["immunizations"]), 2)
        finally:
            self.pid = was

    def offered(self, sid):
        return next(x for x in self.robin.get(f"/api/profiles/{self.pid}/connections").json()["sources"] if x["id"] == sid)["kinds"]

    def listed_ids(self):
        return [c["id"] for c in self.robin.get(f"/api/profiles/{self.pid}/connections").json()["connections"]]

    def test_new_kinds_turned_off_and_removed(self):
        cid = self.connect(self.source())
        self.first_pull(cid)
        had, before = self.mine(), self.counts()
        self.assertTrue(all(had.values()), had)
        planned = [u for u in self.data()["upcoming"] if u["key"] == "fhir:Encounter/enc-3"]
        self.assertTrue(planned)
        # off: their searches stop
        self.assertEqual(self.patch(cid, {"kinds": [k for k in conns.KINDS if k not in self.NEW_KINDS]}).status_code, 200)
        self.fake.clear_requests()
        self.assertTrue(self.pull(cid)["ok"])
        searched = {r.path for r in self.fhir_paths()}
        self.assertFalse(searched & {"/fhir/Immunization", "/fhir/AllergyIntolerance", "/fhir/MedicationDispense",
                                     "/fhir/Appointment"}, searched)
        self.assertIn("/fhir/ImagingStudy", searched)   # imaging studies come with documents, still on
        self.assertEqual(self.mine(), had)   # what they brought stays until removed
        # removed one kind at a time: only what that kind brought, nothing that shares its place
        problems = lambda: [x for x in self.data().get("problems") or [] if x.get("type") != "Allergy"]
        n_problems = len(problems())
        claims_rx = lambda: [e for e in self.data()["events"] if e["k"] in ("claim", "rx") and str(e.get("key")).startswith("fhir:ExplanationOfBenefit/")]
        n_claims = len(claims_rx())
        removed = []
        for kind in self.NEW_KINDS:
            r = self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/remove", json={"kind": kind}, headers=H)
            self.assertEqual(r.status_code, 200, (kind, r.text))
            removed.append(r.json()["import"])
            self.assertEqual(self.mine()[kind], [], kind)
        self.assertEqual(self.mine()["imaging"], had["imaging"])
        self.assertEqual(len(problems()), n_problems, "the problem list's own entries stay")
        self.assertEqual(len(claims_rx()), n_claims, "claims stay")
        self.assertEqual([u["key"] for u in self.data()["upcoming"]], ["fhir:Encounter/enc-3"], "the planned visit stays")
        hist = self.robin.get(f"/api/profiles/{self.pid}/imports").json()["imports"]
        self.assertEqual(hist[0]["counts"]["kinds"], {"immunization": 1}, hist[0])   # immunizations last: one event
        self.assertIn("Immunizations from", hist[0]["label"])
        # the problem list removed: allergies stay, and the other way round
        r = self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/remove", json={"kind": "problems"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(problems(), [])
        self.robin.post(f"/api/profiles/{self.pid}/imports/{r.json()['import']}/undo", headers=H)
        # undone from the history, each comes back
        for iid in reversed(removed):
            self.assertEqual(self.robin.post(f"/api/profiles/{self.pid}/imports/{iid}/undo", headers=H).status_code, 200)
        self.assertEqual(self.mine(), had)
        self.assertEqual(len(problems()), n_problems)
        self.assertEqual({k: v for k, v in self.counts().items() if k != "imports"}, {k: v for k, v in before.items() if k != "imports"})
        # documents removed takes the imaging studies too
        r = self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/remove", json={"kind": "documents"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.mine()["imaging"], [])

    def test_a_connection_made_before_asks_to_sign_in_again_only_for_a_new_kind(self):
        sid = self.source(scopes=self.OLD_SCOPES)
        mine = next(x for x in self.robin.get("/api/connect/sources").json()["sources"] if x["id"] == sid)
        self.assertEqual(mine["leaves_out"], ["Appointments", "Allergies", "Pharmacy fills", "Immunizations"])
        offered = {k["id"]: k["allowed"] for k in self.offered(sid)}
        self.assertFalse(any(offered[k] for k in self.NEW_KINDS), offered)   # the setup doesn't ask for them
        self.assertTrue(offered["documents"])
        cid = self.connect(sid)   # everything its access allows
        self.first_pull(cid)
        self.assertEqual(self.mine()["immunizations"], [])
        # the admin adds the new scopes (as the walk-through now lists them); the connection goes on as it was
        r = self.robin.patch(f"/api/connect/sources/{sid}", json={"scopes": conns.default_scopes()}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(next(x for x in self.robin.get("/api/connect/sources").json()["sources"] if x["id"] == sid)["leaves_out"], [])
        self.fake.clear_requests()
        res = self.pull(cid)
        self.assertTrue(res["ok"], res)
        self.assertFalse({r.path for r in self.fhir_paths()} & {"/fhir/Immunization", "/fhir/Appointment"})   # not granted: not asked
        c = self.listed(cid)
        self.assertEqual(c["state"], "active")   # nobody is asked to sign in again for kinds they haven't turned on
        self.assertFalse(any(k["allowed"] for k in c["kinds"] if k["id"] in self.NEW_KINDS), c["kinds"])
        self.assertEqual(self.patch(cid, {"since": "2020-01-01"}).status_code, 200)   # other changes need no sign-in
        # turning one on does: signing in again asks for its scope, and the next pull brings it
        r = self.patch(cid, {"kinds": c["chosen"] + ["immunizations"]})
        self.assertEqual(r.status_code, 409)
        self.assertIn("Sign in again", r.json()["detail"])
        r = self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/reconnect", headers=H, json={"kinds": c["chosen"] + ["immunizations"]})
        self.assertEqual(r.status_code, 200, r.text)
        scope = q(r.json()["url"])["scope"].split()
        self.assertIn("patient/Immunization.read", scope)
        self.assertNotIn("patient/Appointment.read", scope)   # only the kind turned on
        self.assertTrue(self.back(self.fake.authorize(r.json()["url"], patient="pat-1")).json()["reconnected"])
        self.assertIn("immunizations", self.listed(cid)["chosen"])
        res = self.pull(cid)
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.mine()["immunizations"], ["fhir:Immunization/imm-1"])
        self.assertEqual(self.mine()["appointments"], [])

    # ---------------------------------------------------------------- attachments (#101)

    def big_document(self, size):
        """A DocumentReference whose Binary is size bytes, its size not given in the record."""
        b = self.fake.add({"resourceType": "Binary", "contentType": "application/pdf",
                           "data": base64.b64encode(b"%PDF-1.4 " + b"x" * (size - 9)).decode()})
        self.fake._owner[("Binary", b["id"])] = "pat-1"
        return self.fake.add({"resourceType": "DocumentReference", "status": "current", "date": "2026-11-11T10:00:00Z",
                              "description": "Scanned referral letter (fictional)",
                              "content": [{"attachment": {"contentType": "application/pdf", "url": f"Binary/{b['id']}"}}]},
                             patient="pat-1")

    def set_install(self, key, value):
        r = self.robin.put(f"/api/profiles/{self.pid}/settings/{key}", json={"value": value}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.addCleanup(lambda: self.robin.delete(f"/api/profiles/{self.pid}/settings/{key}", headers=H))

    def test_attachment_rules_skip_list_and_fetch_anyway(self):
        self.set_install("sync.attachTypes", ["text"])   # install-wide: only text attachments
        shown = {s["key"]: s for sec in self.robin.get(f"/api/profiles/{self.pid}/settings/sections").json()["sections"]
                 for s in sec["settings"]}
        self.assertEqual(shown["sync.attachTypes"]["value"], ["text"])
        self.assertEqual(shown["sync.attachMaxMB"]["scope"], "install")
        r = self.robin.put(f"/api/profiles/{self.pid}/settings/sync.attachMaxMB", json={"value": 51}, headers=H)
        self.assertEqual(r.status_code, 400)   # SYNC_MAX_ATTACHMENT_MB (50) is the ceiling
        cid = self.connect(self.source())
        self.first_pull(cid)
        bins = {r.path for r in self.fake.requests if r.path.startswith("/fhir/Binary/")}
        self.assertEqual(bins, {"/fhir/Binary/bin-dr-xr-1"}, bins)   # the text one; the PDFs left out
        c = self.listed(cid)
        self.assertEqual(sorted((x["type"], x["why"]) for x in c["skipped"]), [("pdf", "type"), ("pdf", "type")])
        self.assertTrue(any("Left out 2 attachments" in w for w in c["report"]["warnings"]), c["report"])
        self.assertEqual(c["attach"]["types"], ["text"])
        docs = lambda: {e["title"]: self.data()["details"][str(e["id"])] for e in self.data()["events"] if e["k"] == "document"}
        letter = next(x for x in c["skipped"] if x["record"].startswith("fhir:DocumentReference/"))
        self.assertFalse([d for d in docs().values() if d.get("url") and "pdf" in str(d.get("ext"))], docs())
        # fetch anyway: the file comes in, linked to its record, as an import of its own
        r = self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/attachments/{letter['id']}/fetch", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["size"], 102)
        self.assertEqual(len(self.listed(cid)["skipped"]), 1)
        linked = [d for d in docs().values() if d.get("url") and d.get("ext") == "pdf"]
        self.assertEqual(len(linked), 1, docs())
        doc = self.robin.get("/" + linked[0]["url"].split("#")[0])
        self.assertEqual(doc.status_code, 200)
        self.assertTrue(doc.content.startswith(b"%PDF"))
        hist = self.robin.get(f"/api/profiles/{self.pid}/imports").json()["imports"]
        self.assertIn("fetched", hist[0]["label"])
        self.assertEqual(self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/attachments/{letter['id']}/fetch",
                                         headers=H).status_code, 409)   # once
        # undone from the history: the file goes, and it's listed again
        self.assertEqual(self.robin.post(f"/api/profiles/{self.pid}/imports/{hist[0]['id']}/undo", headers=H).status_code, 200)
        self.assertEqual(len(self.listed(cid)["skipped"]), 2)
        self.assertFalse([d for d in docs().values() if d.get("url") and "Referral" in str(d)])
        # viewers can't fetch
        self.assertEqual(self.kim.post(f"/api/profiles/{self.pid}/connections/{cid}/attachments/{letter['id']}/fetch",
                                       headers=H).status_code, 403)
        # the connection's own rules: every kind, but at most 1 MB; a bigger one is left out, and fetched anyway
        r = self.patch(cid, {"attach": {"types": ["pdf", "image", "text", "html"], "max_mb": 1}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["attach"]["max_mb"], 1)
        self.big_document(int(1.5 * 2**20))
        res = self.pull(cid)
        self.assertTrue(res["ok"], res)
        big = next(x for x in self.listed(cid)["skipped"] if x["why"] == "size")
        r = self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/attachments/{big['id']}/fetch", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["size"], int(1.5 * 2**20))
        # only when asked: nothing is fetched by itself
        self.assertEqual(self.patch(cid, {"attach": {"auto": False}}).status_code, 200)
        self.fake.update("DocumentReference", self.fake.resources("DocumentReference", patient="pat-1")[0]["id"], {})
        self.fake.clear_requests()
        self.assertTrue(self.pull(cid)["ok"])
        self.assertFalse([r for r in self.fake.requests if r.path.startswith("/fhir/Binary/")])
        self.assertTrue(any(x["why"] == "ask" for x in self.listed(cid)["skipped"]))
        # a total per connection
        self.assertEqual(self.patch(cid, {"attach": {"auto": None, "budget_mb": 1}}).status_code, 200)
        self.big_document(2**19)
        self.assertTrue(self.pull(cid)["ok"])
        self.assertTrue(any(x["why"] == "budget" for x in self.listed(cid)["skipped"]))
        # back to the install's rules
        self.assertEqual(self.patch(cid, {"attach": {"types": None, "max_mb": None, "budget_mb": None}}).json()["attach_own"], {})
        raw = sqlite3.connect(os.path.join(TMP, "sanguity.db"))
        self.assertTrue(all(v.startswith(crypto.FIELD) for (v,) in raw.execute("SELECT detail FROM sync_skipped")))
        raw.close()
        for _, rel, path in self._stored_files():
            if rel.startswith("Binary/"):
                self.assertTrue(crypto.file_is_encrypted(path), rel)

    def _stored_files(self):
        db = dbm.connect()
        try:
            from server import uploads
            return uploads.stored_files(db, self.pid)
        finally:
            db.close()

    # ---------------------------------------------------------------- warnings when a connection stops syncing (#102)

    def warnings(self, c=None):
        r = (c or self.robin).get(f"/api/profiles/{self.pid}/connections/warnings")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def admin_sync_notice(self):
        return self.robin.get("/api/security/notice").json()["sync"]

    def note(self):
        db = dbm.connect()
        try:
            return conns.note_warnings(db)
        finally:
            db.close()

    def warning_log(self):
        log = self.robin.get(f"/api/profiles/{self.pid}/activity?action=connect").json()
        rows = log if isinstance(log, list) else log.get("entries", log.get("items", []))
        return [e for e in rows if e["detail"].get("change") == "warning"]

    def test_warnings_after_failures_staleness_and_revocation(self):
        sid = self.source()
        cid = self.connect(sid)
        self.first_pull(cid)
        name = self.listed(cid)["source"]
        self.assertEqual(self.warnings()["warnings"], [])
        # the vendor down, pull after pull
        self.fake.fail(503, times=None, path="/fhir/")
        for _ in range(conns.FAILURES_WARN):
            self.assertEqual(self.pull(cid)["kind"], "unavailable")
        w = self.warnings()
        self.assertEqual([x["kind"] for x in w["warnings"]], ["failing"])
        self.assertTrue(w["can_fix"])
        self.assertIn("3 syncs failed", w["warnings"][0]["text"])
        kim = self.warnings(self.kim)   # a viewer sees it too, to show as a quieter note
        self.assertEqual((kim["can_fix"], len(kim["warnings"])), (False, 1))
        self.assertEqual(self.lee.get(f"/api/profiles/{self.pid}/connections/warnings").status_code, 404)
        mine = next(x for x in self.admin_sync_notice() if x["source"] == name)
        self.assertEqual(mine["failing"], 1)
        self.assertNotIn("Robin", json.dumps(self.admin_sync_notice()))   # counts only: no profile names
        self.assertEqual(self.kim.get("/api/security/notice").status_code, 403)
        # once in the activity log, however many ticks
        self.assertEqual(self.note(), 1)
        self.assertEqual(self.note(), 0)
        self.assertEqual(len(self.warning_log()), 1)
        self.assertEqual(self.warning_log()[0]["detail"]["why"], "failing")
        # working again: the warning goes
        self.fake.clear_faults()
        self.assertTrue(self.pull(cid)["ok"])
        self.assertEqual(self.warnings()["warnings"], [])
        self.assertFalse([x for x in self.admin_sync_notice() if x["source"] == name])
        # not synced for longer than the setting (the vendor stopped answering, then nobody noticed)
        db = dbm.connect()
        db.execute("UPDATE connections SET last_sync=? WHERE id=?", (dbm.now() - 4 * 86400, cid))
        db.close()
        w = self.warnings()["warnings"]
        self.assertEqual([x["kind"] for x in w], ["stale"])
        self.assertIn("4 days", w[0]["text"])
        self.assertEqual(self.note(), 1)
        self.assertEqual(self.warning_log()[0]["detail"]["why"], "stale")
        self.set_install("sync.staleDays", 7)
        self.assertEqual(self.warnings()["warnings"], [])   # the setting says how long is too long
        # access ended
        self.fake.revoke_all("pat-1")
        self.fake.expire_access_tokens()
        self.assertEqual(self.pull(cid)["kind"], "access")
        w = self.warnings()["warnings"]
        self.assertEqual([x["kind"] for x in w], ["access"])
        self.assertIn("Sign in again", w[0]["text"])
        self.assertEqual(next(x for x in self.admin_sync_notice() if x["source"] == name)["access"], 1)
        # disconnected: nothing to warn about
        self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/disconnect", headers=H)
        self.assertEqual(self.warnings()["warnings"], [])

    def test_first_pull_waiting_for_its_preview_isnt_stale(self):
        cid = self.connect(self.source())
        res = self.pull(cid)   # a preview, not yet saved
        self.assertTrue(res["ok"])
        db = dbm.connect()
        db.execute("UPDATE connections SET last_sync=?, created=? WHERE id=?", (dbm.now() - 9 * 86400, dbm.now() - 9 * 86400, cid))
        db.close()
        self.assertEqual(self.warnings()["warnings"], [])

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
        ins = s["guides"]["insurer"]
        self.assertIn("patient/ExplanationOfBenefit.read", ins["scopes"])
        self.assertTrue(any("bluebutton.cms.gov" in p["url"] for p in ins["portals"]))
        self.assertTrue(all("unverified" in p["note"] for p in ins["portals"] if "bluebutton" not in p["url"]))
        self.assertEqual(s["insurer_scopes"], conns.default_scopes(vendor="insurer"))
        self.assertTrue(conns.address_warnings("https://192.168.1.20/connect/return"))
        self.assertEqual(conns.address_warnings("https://health.example.com/connect/return"), [])
        # a source a profile is connected to can't be removed
        self.assertEqual(self.robin.delete(f"/api/connect/sources/{sid}", headers=H).status_code, 409)

    def test_walk_through_per_vendor(self):   # #165: numbered steps, each with what to paste, for every vendor
        g = self.robin.get("/api/connect/sources").json()["guides"]
        self.assertEqual(set(g), {"epic", "oracle", "insurer", "other"})
        for vendor, guide in g.items():
            self.assertTrue(guide["reviewer"] and guide["steps"], vendor)
            for st in guide["steps"]:
                self.assertTrue(st["title"] and st["text"], (vendor, st))
                self.assertLessEqual(set(st.get("paste", [])), {"return_address", "scopes", "types", "sandbox"})
            pasted = {k for st in guide["steps"] for k in st.get("paste", [])}
            self.assertIn("return_address", pasted, vendor)   # every vendor needs the return address registered
            if guide["sandbox"]:
                self.assertIn("sandbox", pasted, vendor)
        self.assertTrue(any(st.get("link") for st in g["epic"]["steps"]))
        self.assertTrue(any(st.get("portals") for st in g["insurer"]["steps"]))
        # #228: the scopes (and types) it says to paste are the setup's defaults, with the new types; an app registered
        # before is told to add them at the vendor
        s = self.robin.get("/api/connect/sources").json()
        for t in ("Immunization", "AllergyIntolerance", "MedicationDispense", "Appointment", "ImagingStudy"):
            self.assertIn(f"patient/{t}.read", s["default_scopes"].split())
            self.assertNotIn(t, s["insurer_scopes"])
        for vendor in ("epic", "oracle", "other"):
            self.assertTrue(any("MedicationDispense" in n and "sign in again" in n for n in g[vendor]["notes"]), vendor)

    def test_waiting_for_the_vendors_review(self):   # #165
        base = {"name": "Riverside (fictional)", "vendor": "epic", "issuer": self.fake.fhir_base}
        r = self.robin.post("/api/connect/sources", json=dict(base, client_id=""), headers=H)
        self.assertEqual(r.status_code, 400)
        self.assertIn("Waiting for the vendor's review", r.json()["detail"])
        r = self.robin.post("/api/connect/sources", json=dict(base, waiting=True), headers=H)   # no client ID yet
        self.assertEqual(r.status_code, 201, r.text)
        sid, since = r.json()["id"], r.json()["waiting"]
        self.assertTrue(since)
        # people see it as coming, with what to do meanwhile, in plain words, and can't connect it
        for c in (self.robin, self.kim):
            mine = next(s for s in c.get(f"/api/profiles/{self.pid}/connections").json()["sources"] if s["id"] == sid)
            self.assertTrue(mine["waiting"])
            self.assertEqual(mine["note"], "Riverside (fictional) isn't ready to connect yet: it's waiting for Epic's review. "
                                           "Meanwhile, download your records from its website or app and upload them under "
                                           "Sources → Upload files.")
        r = self.robin.post(f"/api/profiles/{self.pid}/connect/start", json={"source": sid}, headers=H)
        self.assertEqual(r.status_code, 409)
        self.assertIn("waiting for Epic's review", r.json()["detail"])
        # testing it works meanwhile (discovery is public); it stays waiting, and saving again keeps when it started
        self.assertTrue(self.robin.post(f"/api/connect/sources/{sid}/test", headers=H).json()["ok"])
        r = self.robin.patch(f"/api/connect/sources/{sid}", json={"name": "Riverside Health (fictional)"}, headers=H)
        self.assertEqual((r.json()["waiting"], r.json()["tested"] is not None), (since, True))
        # the review is done: not without a client ID, then with one, and people can connect it
        r = self.robin.patch(f"/api/connect/sources/{sid}", json={"waiting": False}, headers=H)
        self.assertEqual(r.status_code, 400)
        cid = f"app-reviewed-{self.fake.port}"
        self.fake.register_client(cid, [RETURN])
        r = self.robin.patch(f"/api/connect/sources/{sid}", json={"waiting": False, "client_id": cid}, headers=H)
        self.assertEqual((r.status_code, r.json()["waiting"]), (200, None))
        mine = next(s for s in self.robin.get(f"/api/profiles/{self.pid}/connections").json()["sources"] if s["id"] == sid)
        self.assertEqual((mine["waiting"], mine["note"]), (False, None))
        self.assertTrue(self.connect(sid))
        # marked again (a new review): kept apart from connecting again; the setup screen and the log say so
        self.robin.patch(f"/api/connect/sources/{sid}", json={"waiting": True}, headers=H)
        self.assertTrue(next(s for s in self.robin.get("/api/connect/sources").json()["sources"] if s["id"] == sid)["waiting"])
        self.assertEqual(self.robin.post(f"/api/profiles/{self.pid}/connect/start", json={"source": sid}, headers=H).status_code, 409)
        # an untested source that isn't waiting stays hidden from people, as before
        r = self.robin.post("/api/connect/sources", json={"name": "Untested (fictional)", "vendor": "other", "client_id": "x",
                                                         "issuer": "https://fhir.untested.example/R4"}, headers=H)
        self.assertNotIn(r.json()["id"], [s["id"] for s in self.kim.get(f"/api/profiles/{self.pid}/connections").json()["sources"]])
        self.assertEqual(self.robin.post("/api/connect/sources", json=dict(base, issuer="https://x.example/R4", client_id="y",
                                                                          waiting="maybe"), headers=H).status_code, 422)


if __name__ == "__main__":
    unittest.main(verbosity=2)
