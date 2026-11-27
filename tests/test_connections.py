#!/usr/bin/env python3
"""Connected sources (#32 sign-in, #91 connections and sync, #99 choosing what to pull, #101 attachments, #102
warnings when a connection stops syncing), against the fake SMART on FHIR server
(tests/fakefhir). Fictional patients only; nothing real is contacted.

  python3 tests/test_connections.py
"""
import base64, http.client, io, json, os, sqlite3, sys, tempfile, time, unittest, urllib.parse
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-connect-test-")
ORIGIN = "http://sanguity.test:8730"
RETURN = ORIGIN + "/connect/return"
os.environ["CONNECTED_SOURCES"] = "1"   # connected sources on, as before #1096 made them a switch, off by default
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "no-build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"), PUBLIC_ORIGINS=ORIGIN, CONNECT_ALLOW_HTTP="1")
os.makedirs(os.path.join(TMP, "records"))
os.makedirs(os.path.join(TMP, "keys"))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, connections as conns, crypto, db as dbm, main, net, profiles as prof, records, smart, sync   # noqa: E402
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


INTERP = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"


def lab_obs(oid, code, name, v, unit, flag=None, rng=None, t="2025-03-04T09:45:00-06:00"):
    """A fictional lab Observation on the fake's draw day (2025-03-04, the CBC's and LDL's), High where flag says."""
    r = {"resourceType": "Observation", "id": oid, "status": "final", "category": [{"coding": [{"code": "laboratory"}]}],
         "code": {"coding": [{"system": "http://loinc.org", "code": code}], "text": name}, "effectiveDateTime": t,
         "valueQuantity": {"value": v, "unit": unit}}
    if flag:
        r["interpretation"] = [{"coding": [{"system": INTERP, "code": flag}]}]
    if rng:
        r["referenceRange"] = [{"low": {"value": rng[0]}, "high": {"value": rng[1]}, "text": f"{rng[0]}-{rng[1]}"}]
    return r


class Connections(unittest.TestCase):
    made = 0   # sources made so far, each named apart (source())

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
        self.fake = self.serve(patients=["robin-example"])
        db = dbm.connect()
        self.pid = prof.create_profile(db, "Robin (fictional)", owner_id=self.uids["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (self.uids["kim"], self.pid))
        db.close()
        self.sleeps.clear()

    # ---------------------------------------------------------------- helpers

    def serve(self, **kw):
        """A fake FHIR server for this test (FakeFHIR(**kw)), stopped after it. Sources from earlier tests stay in the
        database, and the OS may hand this fake a port an earlier test's had: its address would match theirs and adding
        a source would be refused as a duplicate (409, "Another source has that FHIR base address already"), at random
        (#1030: a fake started within a test, not by setUp, didn't do this). Theirs at its address are moved aside."""
        fake = FakeFHIR(**kw).start()
        self.addCleanup(fake.stop)
        db = dbm.connect()
        try:
            db.execute("UPDATE smart_clients SET issuer = issuer || '/earlier-test-' || id WHERE issuer = ?", (fake.fhir_base,))
        finally:
            db.close()
        return fake

    def source(self, auth="public", secret=None, scopes=None, register=True, vendor="epic"):
        cid = f"app-{auth}-{self.fake.port}"
        if register:
            self.fake.register_client(cid, [RETURN], secret=secret)
        # a name of its own (seen in PR #953's CI): a fake given a port an earlier test's fake had would otherwise share
        # that source's name, and the admins' notice, which counts by name, would add the earlier source's warnings
        Connections.made += 1
        body = {"name": f"Fake {auth} {self.fake.port} ({Connections.made})", "vendor": vendor, "issuer": self.fake.fhir_base, "client_id": cid,
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

    def afresh(self, name):
        """A fake FHIR server and a profile of their own, within one test (#909–#914: each case its own): earlier sources
        at its address moved aside (serve)."""
        self.fake = self.serve(patients=["robin-example"])
        db = dbm.connect()
        self.pid = prof.create_profile(db, name, owner_id=self.uids["robin"])
        db.close()

    def faults(self, day="2025-03-04"):
        """(#909–#914) What's wrong with that day's lab days, by the one check of them (crossmatch.lab_faults): none
        where each result has its row in one lab day, each lab day lists what there is, titled for it, its badge and
        drawer agreeing."""
        from server import crossmatch
        try:
            return crossmatch.lab_faults(self.data(), day)
        except AssertionError:   # (nothing left: the records answer 204)
            return []

    def draw(self, day="2025-03-04"):
        """A draw as the page has it (#899): each lab result of that day ({name: [(low, high, flag, its row's range as
        printed, its row's flag as read)]}), the day's lab days ("lab days": [(title, each of its rows)]) and what's
        wrong with them ("faults", crossmatch.lab_faults: results without rows and rows without results among them)."""
        from server import crossmatch, ranges
        data = self.data()
        drawer, out = crossmatch._rows(data), {}
        for s in data["labs"]:
            if s.get("panel") != "Vitals":
                for p in s["pts"]:
                    if p[0][:10] == day:
                        at = crossmatch._row_of(drawer, s, p)
                        row = at and data["details"][str(at[0])]["rows"][at[1]]
                        out.setdefault(s["name"], []).append((p[2], p[3], p[4], row and row[3], row and ranges.read_flag(row[4], p[1], p[2], p[3], p[5])))
        out["lab days"] = [(e["title"], [r[:5] for i in data["details"][str(e["id"])]["ids"] for r in (data["details"].get(str(i)) or {}).get("rows") or []])
                           for e in data["events"] if e["k"] == "lab" and e["t"][:10] == day]
        out["faults"] = crossmatch.lab_faults(data, day)
        return out

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

    def test_a_medication_the_vendor_renames_keeps_what_hangs_on_it(self):   # #960
        """The vendor names the patient's MedicationRequest another way (Atorvastatin, then Lipitor, then Zocor), and the
        next pull updates it in place. Its reminder and its doses hang on its key (its shown name's leading words), so
        each rename left them on a medication no longer there. Now they go with it in the pull's own save, as a person's
        rename takes them (#954); undoing the pulls in either order puts them back where each found them."""
        from server import med_reminders   # noqa: F401  (the reminders' table, listed with the records)
        cid = self.connect(self.source())
        self.first_pull(cid)
        P = f"/api/profiles/{self.pid}"
        r = self.robin.post(P + "/medications/reminders", headers=H, json={"med": "atorvastatin", "times": ["08:00"],
                            "days": ["mo", "tu", "we", "th", "fr", "sa", "su"], "start": "2026-11-01", "tz": "America/New_York"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(self.robin.post(P + "/medications/doses", headers=H, json={"med": "atorvastatin", "t": "2026-11-02T08:05", "status": "taken"}).status_code, 201)

        def state():
            d = self.robin.get(P + "/data").json()
            return (sorted(m["key"] for m in d.get("medications") or [] if m["key"] != "amoxicillin"),   # (the fake's other)
                    [x["med"] for x in d.get("reminders") or []], [x["med"] for x in d.get("doses") or []])
        self.assertEqual(state(), (["atorvastatin"], ["atorvastatin"], ["atorvastatin"]))
        pulls = []
        for name in ("Lipitor 20 mg tablet", "Zocor 20 mg tablet"):
            self.fake.update("MedicationRequest", "med-1", {"medicationCodeableConcept": {"text": name}})
            res = self.pull(cid)
            self.assertTrue(res["ok"], res)
            self.assertGreaterEqual(res["changed"], 1, res)
            pulls.append(res["import"])
            key = name.split()[0].lower()
            self.assertEqual(state(), ([key], [key], [key]), f"with {name}")
        for order in ((0, 1), (1, 0)):
            with self.subTest(undone=order):
                for i in order:
                    r = self.robin.post(P + f"/imports/{pulls[i]}/undo", headers=H)
                    self.assertEqual(r.status_code, 200, r.text)
                    if order == (0, 1) and i == 0:
                        self.assertEqual(state(), (["zocor"], ["zocor"], ["zocor"]), "the later pull's name kept, and what hangs on it")
                self.assertEqual(state(), (["atorvastatin"], ["atorvastatin"], ["atorvastatin"]), "both undone: each back on Atorvastatin")
                if order == (0, 1):   # again, to undo them the other way round
                    pulls = []
                    for name in ("Lipitor 20 mg tablet", "Zocor 20 mg tablet"):
                        self.fake.update("MedicationRequest", "med-1", {"medicationCodeableConcept": {"text": name}})
                        pulls.append(self.pull(cid)["import"])
                    self.assertEqual(state(), (["zocor"], ["zocor"], ["zocor"]))

    def test_a_pull_matches_only_its_own_sources_records(self):   # #971
        """Hospital A's FHIR export uploaded first, its MedicationRequest, Condition and Observation with the ids the
        connected portal (server B) gives its own (med-1, cond-1, obs-hgb). The first pull, reviewed as an upload, keys
        B's as its own (#967), but a later pull looked its records up by their plain ids among every import's: B's
        Atorvastatin stopped at the vendor was put in place of A's Metformin, B's Hyperlipidemia resolved in place of
        A's diabetes, each never reaching B's own. Now a pull matches only its own source's records (by their "ver",
        else the connection whose pull saved them): B's are updated, A's as they were, and a pull again finds B's by
        the keys naming its server."""
        a = "https://hospital-a.example/fhir"
        pat = {"resourceType": "Patient", "id": "pat-1", "name": [{"family": "Example", "given": ["Robin"]}], "birthDate": "1980-02-02"}
        status = lambda code: {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": code}]}   # noqa: E731
        hospital = [pat, {"resourceType": "MedicationRequest", "id": "med-1", "status": "active", "intent": "order", "subject": {"reference": "Patient/pat-1"},
                          "medicationCodeableConcept": {"text": "Metformin 500 mg tablet"}, "authoredOn": "2025-01-10", "dosageInstruction": [{"text": "1 tablet twice daily"}]},
                    {"resourceType": "Condition", "id": "cond-1", "subject": {"reference": "Patient/pat-1"}, "code": {"text": "Type 2 diabetes"},
                     "clinicalStatus": status("active"), "category": [{"coding": [{"code": "problem-list-item"}]}], "onsetDateTime": "2025-01-05"},
                    lab_obs("obs-hgb", "718-7", "Hemoglobin", 12.1, "g/dL", rng=(12.0, 16.0))]   # the same id, time and value: its own range
        self.upload_bundle(hospital, base=a)
        cid = self.connect(self.source())
        self.first_pull(cid)

        def said():
            d = self.data()
            return (sorted((m["name"], m["spans"][-1]["status"]) for m in d.get("medications") or [] if m["key"] != "amoxicillin"),
                    sorted((p["name"], p["status"]) for p in d.get("problems") or [] if p.get("name") in ("Type 2 diabetes", "Hyperlipidemia")),
                    sorted((p[2], p[3], p[4]) for s in d["labs"] if s["name"].startswith("Hemoglobin") for p in s["pts"]))
        before = said()
        self.assertEqual(before, ([("Atorvastatin 20 mg tablet", "Active"), ("Metformin 500 mg tablet", "Active")],
                                  [("Hyperlipidemia", "Active"), ("Type 2 diabetes", "Active")], [(12.0, 16.0, ""), (13.5, 17.5, "L")]), "each source's own")
        self.fake.update("MedicationRequest", "med-1", {"status": "stopped"})
        self.fake.update("Condition", "cond-1", {"clinicalStatus": {"coding": [{"code": "resolved"}]}})
        self.fake.update("Observation", "obs-hgb", {"referenceRange": [{"low": {"value": 12.0}, "high": {"value": 15.5}, "text": "12.0-15.5"}],
                                                    "interpretation": None})
        res = self.pull(cid)
        self.assertTrue(res["ok"], res)
        self.assertEqual(said(), ([("Atorvastatin 20 mg tablet", "Stopped"), ("Metformin 500 mg tablet", "Active")],
                                  [("Hyperlipidemia", "Resolved"), ("Type 2 diabetes", "Active")], [(12.0, 15.5, ""), (12.0, 16.0, "")]),
                         "the portal's records updated, the hospital's as they were")
        self.assertEqual(self.faults(), [])
        self.assertIsNone(self.pull(cid)["import"], "again: nothing new, its own found by their keys")
        r = self.robin.post(f"/api/profiles/{self.pid}/imports/{res['import']}/undo", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(said(), before, "undone: as they were")

    def test_records_a_connection_saved_saying_no_source_are_its_own(self):   # #1021
        """#970 and #971 tell sources apart by what each record says of its source (its "ver"), else the address of the
        connection that pulled it; the importer names a server by its meta.source first. Records a connection saved
        before them say none, so where the server sends a stable meta.source the next pull took its own for another
        source's: its amended Hemoglobin added as a second result with a second CBC lab event holding a copy of its row,
        its stopped medication and resolved problem added beside the old ones, which never updated again. A record a
        connection saved that names no source is that connection's source's: each is updated in place, as before."""
        for r in self.fake._res.values():
            r.setdefault("meta", {})["source"] = "urn:example:fake-ehr"
        cid = self.connect(self.source())
        self.first_pull(cid)
        db = dbm.connect()   # what it saved, as saved before #955 and #970: saying no source
        try:
            for r in db.execute("SELECT id, body FROM events WHERE profile_id=?", (self.pid,)).fetchall():
                b = records._dec(r["body"]) or {}
                if b.pop("ver", None) is not None:
                    db.execute("UPDATE events SET body=? WHERE id=?", (records._enc(b), r["id"]))
            for r in db.execute("SELECT id, meta FROM imports WHERE profile_id=?", (self.pid,)).fetchall():
                m = records._dec(r["meta"]) or {}
                for v in m.values():
                    for x in v if isinstance(v, list) else ():
                        if isinstance(x, dict):
                            x.pop("ver", None)
                db.execute("UPDATE imports SET meta=? WHERE id=?", (records._enc(m), r["id"]))
            records._bump(db, self.pid)
        finally:
            db.close()

        def said():
            d = self.data()
            return (sorted((m["name"], m["spans"][-1]["status"]) for m in d.get("medications") or [] if m["key"] != "amoxicillin"),
                    sorted((p["name"], p["status"]) for p in d.get("problems") or [] if p.get("name") == "Hyperlipidemia"),
                    sorted((s["name"], p[2], p[3], p[4]) for s in d["labs"] if s["name"] in ("Hemoglobin", "MCV") for p in s["pts"]),
                    sorted((e["title"], tuple(r[0] for r in d["details"][str(e["id"])]["rows"])) for e in d["events"] if e["k"] == "labitem"))
        n = self.counts()
        self.fake.update("Observation", "obs-hgb", {"referenceRange": [{"low": {"value": 12.0}, "high": {"value": 15.5}, "text": "12.0-15.5"}],
                                                    "interpretation": None})
        mcv = lab_obs("obs-mcv", "787-2", "MCV", 88, "fL", rng=(80, 100))
        mcv["meta"] = {"source": "urn:example:fake-ehr"}
        self.fake.add(mcv, patient="pat-1")
        self.fake.update("DiagnosticReport", "dr-cbc", {"result": self.fake._res[("DiagnosticReport", "dr-cbc")]["result"] + [{"reference": "Observation/obs-mcv"}]})
        self.fake.update("MedicationRequest", "med-1", {"status": "stopped"})
        self.fake.update("Condition", "cond-1", {"clinicalStatus": {"coding": [{"code": "resolved"}]}})
        res = self.pull(cid)
        self.assertTrue(res["ok"], res)
        got = said()
        self.assertEqual(got[:3], ([("Atorvastatin 20 mg tablet", "Stopped")], [("Hyperlipidemia", "Resolved")],
                                   [("Hemoglobin", 12.0, 15.5, ""), ("MCV", 80, 100, "")]), "each updated in place, its new result added")
        self.assertEqual([x for x in got[3] if x[0] == "CBC WITH DIFFERENTIAL"],
                         [("CBC WITH DIFFERENTIAL", ("Hemoglobin", "Platelet Count")), ("CBC WITH DIFFERENTIAL", ("MCV",))],
                         "the report's rows where they were, the new one beside its result")
        self.assertEqual({k: v - n[k] for k, v in self.counts().items()}, {"imports": 1, "events": 1, "series": 1, "results": 1})
        self.assertEqual(self.faults(), [])
        self.assertIsNone(self.pull(cid)["import"], "again: nothing new")

    def test_a_server_stamping_meta_source_per_write_is_one_source(self):   # #1022
        """HAPI FHIR stamps meta.source "#<request id>" on each write, so a record it changes comes back saying another
        source: its resources were keyed by each request and taken for another server's, every changed record (the
        stopped medication, the resolved problem, the amended Hemoglobin) added as a copy beside the old one, the
        Hemoglobin with no row. A server is named by the part before "#" (else its address): each is updated in place.
        Then records saved before, saying such a stamp of their source, are its own too."""
        def stamp(n):
            for (rt, rid), r in self.fake._res.items():
                r.setdefault("meta", {})["source"] = f"#req-{rid}-{n}"

        def said():
            d = self.data()
            return (sorted((m["name"], m["spans"][-1]["status"]) for m in d.get("medications") or [] if m["key"] != "amoxicillin"),
                    sorted((p["name"], p["status"]) for p in d.get("problems") or [] if p.get("name") == "Hyperlipidemia"),
                    sorted((s["name"], p[2], p[3], p[4]) for s in d["labs"] if s["name"] == "Hemoglobin" for p in s["pts"]))

        def change(n, status, clinical, rng):
            stamp(n)
            self.fake.update("MedicationRequest", "med-1", {"status": status})
            self.fake.update("Condition", "cond-1", {"clinicalStatus": {"coding": [{"code": clinical}]}})
            self.fake.update("Observation", "obs-hgb", {"referenceRange": [{"low": {"value": rng[0]}, "high": {"value": rng[1]},
                                                                            "text": f"{rng[0]}-{rng[1]}"}], "interpretation": None})
        stamp(1)
        cid = self.connect(self.source())
        self.first_pull(cid)
        n = self.counts()
        change(2, "stopped", "resolved", (12.0, 15.5))
        res = self.pull(cid)
        self.assertTrue(res["ok"], res)
        self.assertEqual(said(), ([("Atorvastatin 20 mg tablet", "Stopped")], [("Hyperlipidemia", "Resolved")],
                                  [("Hemoglobin", 12.0, 15.5, "")]), "each updated in place, never a copy")
        self.assertEqual({k: v - n[k] for k, v in self.counts().items()}, {"imports": 1, "events": 0, "series": 0, "results": 0})
        self.assertEqual(self.faults(), [])
        self.assertIsNone(self.pull(cid)["import"], "again: nothing new")

        db = dbm.connect()   # what it saved, as saved before: its source said by each write's stamp
        try:
            for r in db.execute("SELECT id, body FROM events WHERE profile_id=?", (self.pid,)).fetchall():
                b = records._dec(r["body"]) or {}
                if isinstance(b.get("ver"), dict) and b["ver"].get("server"):
                    b["ver"]["server"] = f"#req-{r['id']}-2"
                    db.execute("UPDATE events SET body=? WHERE id=?", (records._enc(b), r["id"]))
            for r in db.execute("SELECT id, meta FROM imports WHERE profile_id=?", (self.pid,)).fetchall():
                m = records._dec(r["meta"]) or {}
                for v in m.values():
                    for x in v if isinstance(v, list) else ():
                        if isinstance(x, dict) and isinstance(x.get("ver"), dict) and x["ver"].get("server"):
                            x["ver"]["server"] = "#req-list-2"
                db.execute("UPDATE imports SET meta=? WHERE id=?", (records._enc(m), r["id"]))
            records._bump(db, self.pid)
        finally:
            db.close()
        n = self.counts()
        change(3, "active", "active", (11.5, 15.0))
        res = self.pull(cid)
        self.assertTrue(res["ok"], res)
        self.assertEqual(said(), ([("Atorvastatin 20 mg tablet", "Active")], [("Hyperlipidemia", "Active")],
                                  [("Hemoglobin", 11.5, 15.0, "")]), "saved saying a stamp: its own, updated in place")
        self.assertEqual({k: v - n[k] for k, v in self.counts().items()}, {"imports": 1, "events": 0, "series": 0, "results": 0})
        self.assertEqual(self.faults(), [])

    def test_records_keyed_by_a_stamp_before_1022_are_keyed_again(self):   # #1032
        """Before #1022 a server stamping meta.source per write had its resources keyed by the stamp,
        "fhir:#req-<id>-<n>|<type>/<id>", with no "ver": each changed record was added again as a copy. Schema step 44
        marks them, and at start-up records.rekey_stamped keys them as the importer does now (the connection's one
        server: plainly), a correction of one finding it still: the next pull updates each in place."""
        from server import edits
        import re
        def stamp(n):
            for (rt, rid), r in self.fake._res.items():
                r.setdefault("meta", {})["source"] = f"#req-{rid}-{n}"
        stamp(1)
        cid = self.connect(self.source())
        self.first_pull(cid)
        plain = re.compile(r"fhir:([A-Za-z]+/([^|?#]+)(?:#.*)?)$", re.S)
        old = lambda k: f"fhir:#req-{m.group(2)}-1|{m.group(1)}" if isinstance(k, str) and (m := plain.match(k)) else k

        def keys():
            db = dbm.connect()
            try:
                out = {}
                for r in db.execute("SELECT id, body, detail FROM events WHERE profile_id=?", (self.pid,)):
                    d = records._dec(r["detail"])
                    out[r["id"]] = ((records._dec(r["body"]) or {}).get("key"), (d or {}).get("keys") if isinstance(d, dict) else None)
                for r in db.execute("SELECT id, meta FROM imports WHERE profile_id=?", (self.pid,)):
                    out[f"i{r['id']}"] = sorted(x["key"] for v in (records._dec(r["meta"]) or {}).values() if isinstance(v, list)
                                                for x in v if isinstance(x, dict) and isinstance(x.get("key"), str))
                return out
            finally:
                db.close()
        db = dbm.connect()   # a visit renamed, then everything as saved before #1022
        try:
            visit = next(r["id"] for r in db.execute("SELECT id, body FROM events WHERE profile_id=?", (self.pid,))
                         if str((records._dec(r["body"]) or {}).get("key")).startswith("fhir:Encounter/"))
            with dbm.transaction(db):
                edits.add(db, self.pid, self.uids["robin"], "rename", visit, title="Checkup (renamed)")
        finally:
            db.close()
        now = keys()
        self.assertTrue(any(plain.match(str(k)) for k, _ in [v for v in now.values() if isinstance(v, tuple)]))
        db = dbm.connect()
        try:
            secret = records._key_secret(db)
            for r in db.execute("SELECT id, body, detail FROM events WHERE profile_id=?", (self.pid,)).fetchall():
                b, d = records._dec(r["body"]) or {}, records._dec(r["detail"])
                b.pop("ver", None)
                b["key"] = old(b.get("key"))
                if isinstance(d, dict) and isinstance(d.get("keys"), list):
                    d["keys"] = [old(k) for k in d["keys"]]
                db.execute("UPDATE events SET body=?, detail=?, key_hash=? WHERE id=?",
                           (records._enc(b), None if d is None else records._enc(d), records._hash(secret, b.get("key")), r["id"]))
            for r in db.execute("SELECT id, meta FROM imports WHERE profile_id=?", (self.pid,)).fetchall():
                m = records._dec(r["meta"]) or {}
                for v in m.values():
                    for x in v if isinstance(v, list) else ():
                        if isinstance(x, dict) and isinstance(x.get("key"), str):
                            x.pop("ver", None)
                            x["key"] = old(x["key"])
                db.execute("UPDATE imports SET meta=? WHERE id=?", (records._enc(m), r["id"]))
            for r in db.execute("SELECT id, target_key FROM edits WHERE profile_id=?", (self.pid,)).fetchall():
                k = old(crypto.dec(r["target_key"]))
                db.execute("UPDATE edits SET target=?, target_key=? WHERE id=?", (edits.target_hash(k), crypto.enc(k), r["id"]))
            records._bump(db, self.pid)
            self.assertNotEqual(keys(), now)
            step = next(s for s in dbm.SCHEMA if records.STAMPED_KEYS in s)
            db.executescript("BEGIN;" + step + "COMMIT;")
            self.assertTrue(dbm.meta_get(db, records.STAMPED_KEYS), "the schema step marks the work")
            self.assertGreater(records.rekey_stamped(db), 0)
            self.assertIsNone(dbm.meta_get(db, records.STAMPED_KEYS), "once")
            self.assertEqual(records.rekey_stamped(db), 0)
        finally:
            db.close()
        self.assertEqual(keys(), now, "each keyed as the importer keys it now")
        self.assertIn("Checkup (renamed)", [e["title"] for e in self.data()["events"]], "its correction finds it")

        n = self.counts()
        stamp(2)
        self.fake.update("MedicationRequest", "med-1", {"status": "stopped"})
        self.fake.update("Condition", "cond-1", {"clinicalStatus": {"coding": [{"code": "resolved"}]}})
        self.fake.update("Observation", "obs-hgb", {"referenceRange": [{"low": {"value": 12.0}, "high": {"value": 15.5}, "text": "12.0-15.5"}],
                                                    "interpretation": None})
        res = self.pull(cid)
        self.assertTrue(res["ok"], res)
        d = self.data()
        self.assertEqual([m["spans"][-1]["status"] for m in d.get("medications") or [] if m["name"] == "Atorvastatin 20 mg tablet"], ["Stopped"])
        self.assertEqual([p["status"] for p in d.get("problems") or [] if p.get("name") == "Hyperlipidemia"], ["Resolved"])
        self.assertEqual([(p[2], p[3]) for s in d["labs"] if s["name"] == "Hemoglobin" for p in s["pts"]], [(12.0, 15.5)])
        self.assertEqual({k: v - n[k] for k, v in self.counts().items()}, {"imports": 1, "events": 0, "series": 0, "results": 0},
                         "each updated in place, never a copy")
        self.assertEqual(self.faults(), [])
        self.assertIn("Checkup (renamed)", [e["title"] for e in self.data()["events"]])

    def test_uploads_keyed_by_a_stamp_before_1022_by_its_stable_part(self):   # #1032
        """An upload's records keyed by "<base>#<request>" are its base's: plainly where that's the upload's one server
        (their "ver" saying it), "fhir:<base>|..." where the upload mixes servers, as the importer keys them now."""
        ev = lambda key: {"id": key, "t": "2025-03-04T09:00:00", "k": "visit", "title": "Visit", "key": key}
        db = dbm.connect()
        try:
            with dbm.transaction(db):
                one = records.store(db, self.pid, {"events": [ev("fhir:http://a.example/fhir#x1|Encounter/1"),
                                                              ev("fhir:http://a.example/fhir#y2|Encounter/2")]}, "upload")
                two = records.store(db, self.pid, {"events": [ev("fhir:http://a.example/fhir#z3|Encounter/3"),
                                                              ev("fhir:http://b.example/fhir|Encounter/3")]}, "upload")
            dbm.meta_set(db, records.STAMPED_KEYS, "1")
            self.assertEqual(records.rekey_stamped(db), 3)
            got = {(r["import_id"], (b := records._dec(r["body"]))["key"], (b.get("ver") or {}).get("server"))
                   for r in db.execute("SELECT import_id, body FROM events WHERE profile_id=?", (self.pid,))}
            hashes = {r[0] for r in db.execute("SELECT key_hash FROM events WHERE profile_id=?", (self.pid,))}
        finally:
            db.close()
        self.assertEqual(got, {(one, "fhir:Encounter/1", "http://a.example/fhir"), (one, "fhir:Encounter/2", "http://a.example/fhir"),
                               (two, "fhir:http://a.example/fhir|Encounter/3", "http://a.example/fhir"),
                               (two, "fhir:http://b.example/fhir|Encounter/3", None)})
        db = dbm.connect()
        try:
            self.assertEqual(hashes, set(records.key_hashes(db, [k for _, k, _ in got]).values()))
        finally:
            db.close()

    def test_copies_a_stamp_made_become_one_record_once_keyed_alike(self):   # #1068
        """#1022's bug doubled each changed record at a pull ("fhir:#req-<id>-<n>|..." keys of their own); #1032 keyed
        them alike but left both. At start-up after schema step 45, records of one connection sharing one key are one:
        the earliest pull's, saying what the latest says, the later copy removed (an event, a list's entry), the
        correction made on the older one finding it; undoing the latest pull puts back what the earlier one brought,
        the record staying, and the Costs mark the keying couldn't move kept as the later of the two. Nothing else of
        that source changes, nor an upload's records; logged with counts; once."""
        from server import activity, edits, oop
        cid = self.connect(self.source())
        self.first_pull(cid)   # the fixture's records too, none of them a copy: left as they are
        ev = lambda key, title: {"id": key, "t": "2025-03-04T09:00:00", "k": "visit", "title": title, "key": key}   # noqa: E731
        st = lambda t, n: f"fhir:#req-{n}|{t}"   # noqa: E731
        db = dbm.connect()
        try:
            with dbm.transaction(db):
                a = records.store(db, self.pid, {"events": [ev(st("Encounter/e1", 1), "Checkup"), ev(st("Encounter/e2", 1), "Other visit")],
                                                 "problems": [{"key": st("Condition/c1", 1), "name": "Fictional condition c1", "status": "Active"}]},
                                  "sync", fmt="fhir")
                b = records.store(db, self.pid, {"events": [ev(st("Encounter/e1", 2), "Checkup (changed)")],
                                                 "problems": [{"key": st("Condition/c1", 2), "name": "Fictional condition c1", "status": "Resolved"}]},
                                  "sync", fmt="fhir")
                db.execute("UPDATE imports SET connection_id=? WHERE id IN (?, ?)", (cid, a, b))
                up = records.store(db, self.pid, {"events": [ev(st("Encounter/e9", 1), "Upload's"), ev(st("Encounter/e9", 2), "Upload's again")]}, "upload")
                old = db.execute("SELECT id FROM events WHERE import_id=? ORDER BY id LIMIT 1", (a,)).fetchone()[0]
                edits.add(db, self.pid, self.uids["robin"], "rename", old, title="Checkup (renamed)")
                for n, hsa in ((1, True), (2, False)):
                    db.execute("INSERT INTO cost_marks(profile_id, target, target_key, value, at) VALUES(?, ?, ?, ?, ?)",
                               (self.pid, oop.target_hash(st("Encounter/e1", n)), crypto.enc(st("Encounter/e1", n)),
                                crypto.enc(json.dumps({"hsa": hsa})), n))
            dbm.meta_set(db, records.STAMPED_KEYS, "1")
            self.assertEqual(records.rekey_stamped(db), 7)
            bodies = lambda: {r["id"]: (r["import_id"], records._dec(r["body"])) for r in   # noqa: E731
                              db.execute("SELECT id, import_id, body FROM events WHERE profile_id=?", (self.pid,))}
            before = bodies()
            self.assertEqual(sorted(b_["key"] for _, b_ in before.values()).count("fhir:Encounter/e1"), 2, "keyed alike, both left")
            self.assertIsNone(records.merge_copies(db), "not before schema step 45 marks it")
            step = next(s for s in dbm.SCHEMA if records.COPIES in s)
            db.executescript("BEGIN;" + step + "COMMIT;")
            from contextlib import redirect_stdout
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(records.merge_copies(db), {"events": 1, "results": 0, "entries": 1, "marks": 1})
            self.assertIsNone(records.merge_copies(db), "once")
            after = bodies()
            newer = next(i for i, (imp, b_) in before.items() if imp == b and b_["key"] == "fhir:Encounter/e1")
            self.assertEqual(set(before) - set(after), {newer}, "the later copy removed, nothing else")
            self.assertEqual(after[old], (a, before[newer][1]), "the earliest pull's record, saying what the latest says")
            self.assertEqual({i: before[i] for i in after if i != old}, {i: v for i, v in after.items() if i != old}, "the rest as they were")
            self.assertEqual(sum(1 for i, _ in after.values() if i == up), 2, "an upload's records aren't touched")
            marks = [(crypto.dec(r["target_key"]), json.loads(crypto.dec(r["value"])), r["at"])
                     for r in db.execute("SELECT target_key, value, at FROM cost_marks WHERE profile_id=?", (self.pid,))]
            self.assertEqual(marks, [("fhir:Encounter/e1", {"hsa": False}, 2)])
        finally:
            db.close()
        d = self.data()
        self.assertEqual([e["title"] for e in d["events"] if e["title"].startswith("Checkup")], ["Checkup (renamed)"], "its correction kept")
        self.assertEqual([p["status"] for p in d.get("problems") or [] if p.get("name") == "Fictional condition c1"], ["Resolved"])
        self.assertIn("one record each, the latest kept: 1 record(s), 0 lab result(s) and 1 list entries removed, 1 Costs mark(s) moved.", out.getvalue())
        with open(main.__file__, encoding="utf-8") as f:
            self.assertIn("records.merge_copies(db)", f.read(), "at start-up")
        # undoing the latest pull puts back what the earlier one brought: the record stays (its correction with it)
        from server import history
        db = dbm.connect()
        try:
            history.undo(db, self.pid, b, db.execute("SELECT * FROM users WHERE id=?", (self.uids["robin"],)).fetchone())
            self.assertEqual(records._dec(db.execute("SELECT body FROM events WHERE id=?", (old,)).fetchone()[0])["title"], "Checkup")
        finally:
            db.close()
        d = self.data()
        self.assertEqual([e["title"] for e in d["events"] if e["title"].startswith("Checkup")], ["Checkup (renamed)"], "still there, corrected")
        self.assertEqual([p["status"] for p in d.get("problems") or [] if p.get("name") == "Fictional condition c1"], ["Active"],
                         "the list's entry as the earlier pull had it")
        db = dbm.connect()
        try:
            r = db.execute("SELECT actor, detail FROM activity WHERE action='records.copies' ORDER BY id DESC").fetchone()
            self.assertEqual((r["actor"], json.loads(crypto.dec(r["detail"]))), (activity.START_UP, {"events": 1, "results": 0, "entries": 1, "marks": 1}))
        finally:
            db.close()

    def stamped_before_1022(self):
        """The profile's records as saved before #1022 from a server stamping meta.source per write: each key
        "fhir:#req-<id>-1|<type>/<id>", no "ver"."""
        import re
        plain = re.compile(r"fhir:([A-Za-z]+/([^|?#]+)(?:#.*)?)$", re.S)
        old = lambda k: f"fhir:#req-{m.group(2)}-1|{m.group(1)}" if isinstance(k, str) and (m := plain.match(k)) else k   # noqa: E731
        db = dbm.connect()
        try:
            secret = records._key_secret(db)
            for r in db.execute("SELECT id, body, detail FROM events WHERE profile_id=?", (self.pid,)).fetchall():
                b, d = records._dec(r["body"]) or {}, records._dec(r["detail"])
                b.pop("ver", None)
                b["key"] = old(b.get("key"))
                if isinstance(d, dict) and isinstance(d.get("keys"), list):
                    d["keys"] = [old(k) for k in d["keys"]]
                db.execute("UPDATE events SET body=?, detail=?, key_hash=? WHERE id=?",
                           (records._enc(b), None if d is None else records._enc(d), records._hash(secret, b.get("key")), r["id"]))
            for r in db.execute("SELECT id, meta FROM imports WHERE profile_id=?", (self.pid,)).fetchall():
                m = records._dec(r["meta"]) or {}
                for v in m.values():
                    for x in v if isinstance(v, list) else ():
                        if isinstance(x, dict) and isinstance(x.get("key"), str):
                            x.pop("ver", None)
                            x["key"] = old(x["key"])
                db.execute("UPDATE imports SET meta=? WHERE id=?", (records._enc(m), r["id"]))
            records._bump(db, self.pid)
        finally:
            db.close()

    def test_lab_results_a_stamp_doubled_become_one_row(self):   # #1082
        """A lab result changed at its source was added again at a pull from a server stamping meta.source per write,
        keyed by the stamp (before #1022): a second result, its row in a labitem of that pull's own. Once keyed alike
        (#1032), start-up makes them one row by row (#1068's step): the earliest pull's row and result, saying what the
        latest says, in its lab day; the later copy's result and row gone. Nothing else changes."""
        from contextlib import redirect_stdout
        for (rt, rid), r in self.fake._res.items():
            r.setdefault("meta", {})["source"] = f"#req-{rid}-1"
        cid = self.connect(self.source())
        self.first_pull(cid)
        self.stamped_before_1022()
        n0, draw0 = self.counts(), self.draw()
        for (rt, rid), r in self.fake._res.items():
            r["meta"]["source"] = f"#req-{rid}-2"
        self.fake.update("Observation", "obs-hgb", {"referenceRange": [{"low": {"value": 12.0}, "high": {"value": 15.5}, "text": "12.0-15.5"}],
                                                    "interpretation": None})
        res = self.pull(cid)
        self.assertTrue(res["ok"], res)
        n1, draw1 = self.counts(), self.draw()
        self.assertEqual({k: v - n0[k] for k, v in n1.items()}, {"imports": 1, "events": 1, "series": 1, "results": 1}, "doubled")
        self.assertEqual(len(draw1["Hemoglobin"]), 2)
        db = dbm.connect()
        try:
            from server import edits
            with dbm.transaction(db):   # a correction of the result's lab event: kept
                edits.add(db, self.pid, self.uids["robin"], "rename", next(
                    r["id"] for r in db.execute("SELECT id, body, detail FROM events WHERE profile_id=? ORDER BY id", (self.pid,))
                    if (records._dec(r["body"]) or {}).get("k") == "labitem"
                    and "Hemoglobin" in [x[0] for x in (records._dec(r["detail"]) or {}).get("rows") or []]), title="CBC (renamed)")
            hidden = db.execute("SELECT COUNT(*) FROM edits WHERE profile_id=?", (self.pid,)).fetchone()[0]
            dbm.meta_set(db, records.STAMPED_KEYS, "1")
            with redirect_stdout(io.StringIO()):
                self.assertGreater(records.rekey_stamped(db), 0)
            dbm.meta_set(db, records.COPIES, "1")
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(records.merge_copies(db), {"events": 0, "results": 1, "entries": 0, "marks": 0})
            self.assertEqual(db.execute("SELECT COUNT(*) FROM edits WHERE profile_id=?", (self.pid,)).fetchone()[0], hidden)
        finally:
            db.close()
        self.assertIn("1 lab result(s) and 0 list entries removed", out.getvalue())
        n2, draw2 = self.counts(), self.draw()
        self.assertEqual({k: v - n0[k] for k, v in n2.items()}, {"imports": 1, "events": 0, "series": 0, "results": 0}, "one again")
        self.assertEqual(draw2["Hemoglobin"], [(12.0, 15.5, "", "12.0-15.5", "")], "the latest pull's")
        self.assertEqual(draw2["lab days"], [("4 lab results", [["Hemoglobin", "12.1", "g/dL", "12.0-15.5", ""]] + draw0["lab days"][0][1][1:])],
                         "one row in one lab day")
        self.assertIn("CBC (renamed)", [e["title"] for e in self.data()["events"]], "its correction kept")
        self.assertEqual({k: v for k, v in draw2.items() if k not in ("Hemoglobin", "lab days")},
                         {k: v for k, v in draw0.items() if k not in ("Hemoglobin", "lab days")}, "nothing else changes")
        # undoing the later pull puts back what the first brought
        from server import history
        db = dbm.connect()
        try:
            history.undo(db, self.pid, res["import"], db.execute("SELECT * FROM users WHERE id=?", (self.uids["robin"],)).fetchone())
        finally:
            db.close()
        draw3 = self.draw()
        self.assertEqual((self.counts(), draw3), (n0, draw0))

    def test_three_copies_a_stamp_made_become_one_and_undo_in_turn(self):   # #1068, #1082
        """A visit and a lab result each changed twice at their source, each change added again at a pull before #1022:
        three copies of each. At start-up each is one (the earliest pull's, saying what the latest says); undoing the
        latest pull, then the middle one, leaves each step as the pull before had it, and nothing is lost."""
        self.three_copies()

    def test_three_copies_undone_middle_pull_first(self):   # #1068, #1082
        """The same, the middle pull undone first: the record says what the latest says still; then the latest: as the
        first pull had it."""
        self.three_copies(middle_first=True)

    def three_copies(self, middle_first=False):
        from contextlib import redirect_stdout
        from server import history

        def stamp(n):
            for (rt, rid), r in self.fake._res.items():
                r.setdefault("meta", {})["source"] = f"#req-{rid}-{n}"
        enc = next(r for r in self.fake.resources("Encounter", patient="pat-1") if r.get("status") != "planned")

        def state():
            d, w = self.data(), self.draw()
            return (self.counts(), sorted(e["title"] for e in d["events"] if e["k"] != "lab" and e["k"] != "labitem"), w)
        stamp(1)
        cid = self.connect(self.source())
        self.first_pull(cid)
        self.stamped_before_1022()
        states, pulls = [state()], []
        for n, (low, high, visit) in enumerate(((12.0, 15.5, "Follow-up (changed once)"), (11.5, 15.0, "Follow-up (changed twice)")), 2):
            stamp(n)
            self.fake.update("Observation", "obs-hgb", {"referenceRange": [{"low": {"value": low}, "high": {"value": high}, "text": f"{low}-{high}"}],
                                                        "interpretation": None})
            self.fake.update("Encounter", enc["id"], {"type": [{"text": visit}]})
            res = self.pull(cid)
            self.assertTrue(res["ok"], res)
            pulls.append(res["import"])
            self.stamped_before_1022()   # (this pull's copies keyed by the stamp too, as before #1022)
            got = self.counts()
            self.assertEqual({k: v - states[-1][0][k] for k, v in got.items()}, {"imports": 1, "events": 2, "series": 1, "results": 1},
                             f"pull {n}: the visit and the result added again")
            states.append(state())
        db = dbm.connect()
        try:
            dbm.meta_set(db, records.STAMPED_KEYS, "1")
            dbm.meta_set(db, records.COPIES, "1")
            with redirect_stdout(io.StringIO()):
                records.rekey_stamped(db)
                self.assertEqual(records.merge_copies(db), {"events": 2, "results": 2, "entries": 0, "marks": 0})
        finally:
            db.close()
        merged = state()
        self.assertEqual({k: v - states[0][0][k] for k, v in merged[0].items()}, {"imports": 2, "events": 0, "series": 0, "results": 0}, "one each")
        self.assertEqual(merged[2]["Hemoglobin"], [(11.5, 15.0, "", "11.5-15.0", "")], "the latest pull's")
        from collections import Counter
        (orig,) = Counter(states[0][1]) - Counter(merged[1])   # the visit as the first pull had it
        titled = lambda visit: sorted([*(Counter(states[0][1]) - Counter([orig])).elements(), visit])   # noqa: E731
        self.assertEqual(merged[1], titled("Follow-up (changed twice)"), "one visit, the latest's")
        self.assertEqual(merged[2]["faults"], [])
        steps = [(pulls[0], merged[2]["Hemoglobin"], "Follow-up (changed twice)"), (pulls[1], states[0][2]["Hemoglobin"], orig)] if middle_first \
            else [(pulls[1], states[1][2]["Hemoglobin"][-1:], "Follow-up (changed once)"), (pulls[0], states[0][2]["Hemoglobin"], orig)]
        for iid, hgb, visit in steps:   # the latest pull undone, then the middle one (or the other way round)
            db = dbm.connect()
            try:
                history.undo(db, self.pid, iid, db.execute("SELECT * FROM users WHERE id=?", (self.uids["robin"],)).fetchone())
            finally:
                db.close()
            now = state()
            self.assertEqual(now[2]["Hemoglobin"], hgb, f"undoing {iid}: the result as the pull before had it")
            self.assertEqual(now[1], titled(visit), f"undoing {iid}: the visit as the pull before had it")
            self.assertEqual(now[2]["faults"], [])
            self.assertEqual(len(now[2]["lab days"]), 1)
        self.assertEqual(state(), states[0], "back to the first pull's, nothing lost")

    def test_a_stamped_record_that_cant_be_keyed_is_left_and_the_app_starts(self):   # #1044
        """One stamped record start-up can't key (its details unreadable) is left as it was and named in the app's log and
        the install log; the others are keyed, and the work stays marked, tried again at the next start-up. Nothing is
        raised, so the app starts (main.py's start-up had stopped there, every time); once none is left, it's done."""
        from contextlib import redirect_stdout
        ev = lambda key: {"id": key, "t": "2025-03-04T09:00:00", "k": "visit", "title": "Visit", "key": key}
        db = dbm.connect()
        try:
            with dbm.transaction(db):
                one = records.store(db, self.pid, {"events": [ev(f"fhir:http://a.example/fhir#x{i}|Encounter/{i}") for i in (1, 2, 3)]}, "upload")
            bad, before = db.execute("SELECT id, body FROM events WHERE import_id=? ORDER BY id LIMIT 1", (one,)).fetchone()
            db.execute("UPDATE events SET detail='not what the app encrypts' WHERE id=?", (bad,))
            dbm.meta_set(db, records.STAMPED_KEYS, "1")
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(records.rekey_stamped(db), 2)
            self.assertIn(f"left as they were (tried again at the next start-up): event {bad}.", out.getvalue())
            got = {r["id"]: (r["body"], records._dec(r["body"])["key"] if r["id"] != bad else None)
                   for r in db.execute("SELECT id, body FROM events WHERE import_id=?", (one,))}
            self.assertEqual(got.pop(bad)[0], before, "left as it was")
            self.assertEqual(sorted(k for _, k in got.values()), ["fhir:Encounter/2", "fhir:Encounter/3"])
            self.assertTrue(dbm.meta_get(db, records.STAMPED_KEYS), "still marked: tried again")
            r = db.execute("SELECT actor, detail FROM activity WHERE action='records.rekey' ORDER BY id DESC").fetchone()
            self.assertEqual((r["actor"], json.loads(crypto.dec(r["detail"]))), ("start-up", {"left": [f"event {bad}"]}))
            db.execute("DELETE FROM events WHERE id=?", (bad,))   # once nothing is left: done
            with redirect_stdout(io.StringIO()):
                self.assertEqual(records.rekey_stamped(db), 0)
            self.assertIsNone(dbm.meta_get(db, records.STAMPED_KEYS))
        finally:
            db.execute("DELETE FROM events WHERE import_id=?", (one,))
            dbm.meta_set(db, records.STAMPED_KEYS, None)
            db.close()

    def test_a_result_sent_again_is_updated_in_place(self):   # #869
        """A result the source amends comes back at the next pull: the same result, updated in place, range and flag,
        never a copy. The report's Hemoglobin 12.1 (13.5–17.5, Low) amended to 12.0–15.5 and Normal, its Platelet Count
        given a range (none, then 150–400: High), an ALT's flag changed (High with no range, then Abnormal). The pull
        updated their lab day's rows in place but added a copy of each result: two results, the row reading the first's
        Low beside the new range. Now no result is added, each is as the source sends it now, its row agreeing with it;
        undoing the pull puts back the results and the rows. And a bound (round 7 together: a CRP's ">10", a Quantity's
        comparator, a bound since #870, text before) with its range corrected ("<10", High, then "<20") the same."""
        code = lambda c: {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation", "code": c}]}
        self.fake.add({"resourceType": "Observation", "id": "obs-alt", "status": "final", "category": [{"coding": [{"code": "laboratory"}]}],
                       "code": {"coding": [{"system": "http://loinc.org", "code": "1742-6"}], "text": "ALT"},
                       "effectiveDateTime": "2025-03-04T09:45:00-06:00", "valueQuantity": {"value": 60, "unit": "U/L"},
                       "interpretation": [code("H")]}, patient="pat-1")
        self.fake.add({"resourceType": "Observation", "id": "obs-crp", "status": "final", "category": [{"coding": [{"code": "laboratory"}]}],
                       "code": {"coding": [{"system": "http://loinc.org", "code": "1988-5"}], "text": "CRP"},
                       "effectiveDateTime": "2025-03-04T09:45:00-06:00", "valueQuantity": {"value": 10, "comparator": ">", "unit": "mg/L"},
                       "referenceRange": [{"high": {"value": 10, "unit": "mg/L"}, "text": "<10"}]}, patient="pat-1")
        self.fake.update("Observation", "obs-plt", {"referenceRange": None})   # its range given at the next pull
        cid = self.connect(self.source())
        self.first_pull(cid)
        before, n = self.data(), self.counts()

        def said(data):   # each lab's results, and its rows' ranges as printed and flags as read, by the row's result
            from server import crossmatch, ranges
            drawer, out = crossmatch._rows(data), {}
            for s in data["labs"]:
                if s["name"] in ("Hemoglobin", "Platelet Count", "ALT", "CRP"):
                    for p in s["pts"]:
                        at = crossmatch._row_of(drawer, s, p)
                        row = at and data["details"][str(at[0])]["rows"][at[1]]
                        out.setdefault(s["name"], []).append([p[2], p[3], p[4], row and row[3], row and ranges.read_flag(row[4], p[1], p[2], p[3], p[5])])
            return out
        self.assertEqual(said(before), {"Hemoglobin": [[13.5, 17.5, "L", "13.5 - 17.5", "L"]], "Platelet Count": [[None, None, "", "", ""]],
                                        "ALT": [[None, None, "H", "", "H"]], "CRP": [[None, 10, "H", "<10", "H"]]})
        self.fake.update("Observation", "obs-hgb", {"referenceRange": [{"low": {"value": 12.0, "unit": "g/dL"}, "high": {"value": 15.5, "unit": "g/dL"},
                                                                        "text": "12.0-15.5"}], "interpretation": [code("N")]})
        self.fake.update("Observation", "obs-plt", {"referenceRange": [{"low": {"value": 150}, "high": {"value": 400}}]})
        self.fake.update("Observation", "obs-alt", {"interpretation": [code("A")]})
        self.fake.update("Observation", "obs-crp", {"referenceRange": [{"high": {"value": 20, "unit": "mg/L"}, "text": "<20"}]})
        res = self.pull(cid)
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["counts"]["results"], res["counts"].get("updated")), (0, 4), res)
        self.assertEqual({k: v for k, v in self.counts().items() if k != "imports"}, {k: v for k, v in n.items() if k != "imports"}, "no result added")
        self.assertEqual(said(self.data()), {"Hemoglobin": [[12.0, 15.5, "", "12.0-15.5", ""]], "Platelet Count": [[150, 400, "H", "150 - 400", "H"]],
                                             "ALT": [[None, None, "A", "", "A"]], "CRP": [[None, 20, "", "<20", ""]]}, "each as sent now, its row agreeing")
        # amended again (the report's Hemoglobin, not its Platelet Count), then the first pull undone: what the later one
        # changed again keeps its version (sync.unapply), each record its own (#911: the Platelet Count, which it didn't
        # change, back to no range, as the report's other rows are), the rest as before; then the later one: all as before
        self.fake.update("Observation", "obs-hgb", {"referenceRange": [{"low": {"value": 12.0, "unit": "g/dL"}, "high": {"value": 15.0, "unit": "g/dL"},
                                                                        "text": "12.0-15.0"}]})
        again = self.pull(cid)
        self.assertEqual((again["counts"]["results"], again["counts"].get("updated")), (0, 1), again)
        undo = lambda iid: self.robin.post(f"/api/profiles/{self.pid}/imports/{iid}/undo", headers=H)
        self.assertEqual(undo(res["import"]).status_code, 200)
        self.assertEqual(said(self.data()), {"Hemoglobin": [[12.0, 15.0, "", "12.0-15.0", ""]], "Platelet Count": [[None, None, "", "", ""]],
                                             "ALT": [[None, None, "H", "", "H"]], "CRP": [[None, 10, "H", "<10", "H"]]},
                         "the Hemoglobin as the later pull left it, the Platelet Count, the ALT and the CRP as before")
        self.assertEqual(undo(again["import"]).status_code, 200)
        self.assertEqual(said(self.data()), said(before), "both undone: as they were")
        self.assertEqual(self.counts(), n)

    def test_a_later_change_keeps_its_version_only_for_the_record_it_changed(self):   # #911
        """The portal's export uploaded, then connected; a pull changes the CBC's Hemoglobin (Low, then Normal against
        12.0–15.5), a later one its Platelet Count (High, then Normal against 150–500), each in the report's lab event
        the upload made. Undoing the first pull kept the later pull's version of the whole lab event, so the Hemoglobin
        stayed Normal, result and row, while Import history said the result it updated "gets back the range and flag it
        had". Now the later change keeps its version only for the record it changed: the Hemoglobin is Low again, the
        Platelet Count as the later pull left it, until that one is undone too; in either order, as before."""
        hgb_n = {"interpretation": [{"coding": [{"system": INTERP, "code": "N"}]}],
                 "referenceRange": [{"low": {"value": 12.0, "unit": "g/dL"}, "high": {"value": 15.5, "unit": "g/dL"}}]}
        plt_n = {"interpretation": [{"coding": [{"system": INTERP, "code": "N"}]}], "referenceRange": [{"low": {"value": 150}, "high": {"value": 500}}]}
        hgb, plt = ((13.5, 17.5, "L", "13.5 - 17.5", "L"), (12.0, 15.5, "", "12.0 - 15.5", "")), ((150, 400, "H", "150 - 400", "H"), (150, 500, "", "150 - 500", ""))
        for order in ((0, 1), (1, 0)):
            with self.subTest(order=order):
                self.afresh(f"Undone {order} (fictional)")
                self.upload_bundle([json.loads(json.dumps(r)) for r in self.fake.resources(patient="pat-1")])
                cid = self.connect(self.source())
                self.first_pull(cid)
                before, n = self.draw(), self.counts()
                self.assertEqual((before["Hemoglobin"], before["Platelet Count"]), ([hgb[0]], [plt[0]]))
                self.fake.update("Observation", "obs-hgb", hgb_n)
                pulls = [self.pull(cid)]
                self.fake.update("Observation", "obs-plt", plt_n)
                pulls.append(self.pull(cid))
                self.assertEqual([p["counts"].get("updated") for p in pulls], [1, 1], pulls)
                drawn = self.draw()
                self.assertEqual((drawn["Hemoglobin"], drawn["Platelet Count"], self.faults()), ([hgb[1]], [plt[1]], []))
                self.undo(pulls[order[0]]["import"])
                drawn = self.draw()
                self.assertEqual((drawn["Hemoglobin"], drawn["Platelet Count"], self.faults()),
                                 ([hgb[order[0] == 1]], [plt[order[0] == 0]], []), "what the undone pull changed back, the other's kept")
                self.undo(pulls[order[1]]["import"])
                self.assertEqual((self.draw(), self.counts()), (before, n), "both undone: as before")

    def test_another_source_at_one_time_is_never_the_same_record(self):   # #885, #897
        """Only the same record of the same source is updated in place: an Observation by its own id. A hospital's FHIR
        file uploaded, Potassium 5.05 (3.5–5.0, High) and Sodium on their own (no report) on two days; then a pull
        brings the connected source's Potassium at that time, another Observation in a labitem keyed by the same day:
        with no range a duplicate, with another range (3.5–5.1) a copy with its own row, and the hospital's results and
        rows stay as they were. The pull took the plan's for the hospital's sent again, its no range and no flag or its
        range replacing the hospital's (#869), and updated the hospital's labitem and lab day of that day in place with
        its own rows (sync._update_in_place, _link_labdays: by the day's key). Its own Observation sent again, amended,
        is updated in place, the hospital's as it was; undone, both pulls leave the hospital's as it was.
        One lab day a draw (#897): the pull added its own beside the hospital's ("2 lab draws"), the duplicate's row in
        it with no result of its own, read against the hospital's result. Now the copy's row, and a result new to the
        profile (the source's Glucose), join the hospital's lab day, each row beside its result in the pull's own
        labitem; the duplicate's row is left out with it."""
        from server import crossmatch, ranges, uploads
        days = ("2025-05-06T08:30:00-05:00", "2025-05-07T08:30:00-05:00")

        def obs(oid, code, name, v, unit, t, rng=None, flag=None):
            r = {"resourceType": "Observation", "id": oid, "status": "final", "category": [{"coding": [{"code": "laboratory"}]}],
                 "code": {"coding": [{"system": "http://loinc.org", "code": code}], "text": name}, "effectiveDateTime": t,
                 "valueQuantity": {"value": v, "unit": unit}}
            if rng:
                r["referenceRange"] = [{"low": {"value": rng[0]}, "high": {"value": rng[1]}, "text": f"{rng[0]}-{rng[1]}"}]
            if flag:
                r["interpretation"] = [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation", "code": flag}]}]
            return r
        k = lambda oid, t, rng=None, flag=None: obs(oid, "2823-3", "Potassium", 5.05, "mmol/L", t, rng, flag)   # noqa: E731
        hospital = [r for i, t in enumerate(days) for r in (k(f"hosp-k{i}", t, (3.5, 5.0), "H"), obs(f"hosp-na{i}", "2951-2", "Sodium", 140, "mmol/L", t, (135, 145)))]
        db = dbm.connect()
        try:
            up = uploads.create(db, self.pid, None)
            uploads.add_file(db, self.pid, up, "hospital.json", io.BytesIO(json.dumps({"resourceType": "Bundle", "type": "collection",
                                                                                      "entry": [{"resource": r} for r in hospital]}).encode()))
            uploads.finish(db, self.pid, up)
        finally:
            db.close()
        r = self.robin.post(f"/api/profiles/{self.pid}/uploads/{up}/import", json={}, headers=H)
        self.assertEqual(r.status_code, 202, r.text)
        jid, end = r.json()["id"], time.time() + 30
        while time.time() < end and (j := self.robin.get(f"/api/profiles/{self.pid}/jobs/{jid}").json())["state"] != "done":
            if j["state"] == "preview":
                self.assertEqual(self.robin.post(f"/api/profiles/{self.pid}/jobs/{jid}/confirm", headers=H).status_code, 202)
            time.sleep(0.05)
        self.assertEqual(j["state"], "done", j)
        upload_iid = j["import_id"]

        def said():   # each result on the two days: its range and flag, its row's as printed and as read; each lab day's rows
            data = self.data()
            drawer, out = crossmatch._rows(data), {}
            for s in data["labs"]:
                for p in s["pts"]:
                    if p[0][:10] in {t[:10] for t in days}:
                        at = crossmatch._row_of(drawer, s, p)
                        row = at and data["details"][str(at[0])]["rows"][at[1]]
                        out.setdefault((s["name"], p[0][:10], s["imp"] == upload_iid), []).append(
                            (p[2], p[3], p[4], row and row[3], row and ranges.read_flag(row[4], p[1], p[2], p[3], p[5])))
            labdays = sorted((e["t"][:10], e["imp"] == upload_iid, [r[:4] for i in data["details"][str(e["id"])]["ids"] for r in data["details"][str(i)]["rows"]])
                             for e in data["events"] if e["k"] == "lab" and e["t"][:10] in {t[:10] for t in days})
            return out, labdays
        hosp_k, hosp_na = [(3.5, 5.0, "H", "3.5-5.0", "H")], [(135, 145, "", "135-145", "")]
        before = said()
        self.assertEqual(before, ({(n, t[:10], True): v for t in days for n, v in (("Potassium, Blood", hosp_k), ("Sodium, Blood", hosp_na))},
                                  [(t[:10], True, [["Potassium", "5.05", "mmol/L", "3.5-5.0"], ["Sodium", "140", "mmol/L", "135-145"]]) for t in days]))
        cid = self.connect(self.source())
        self.first_pull(cid)
        n = self.counts()
        # the source's Potassium of the first day, no range: a duplicate; of the second, its own range: a copy; and its
        # Glucose of the second, new to the profile
        self.fake.add(k("plan-k0", days[0]), patient="pat-1")
        self.fake.add(k("plan-k1", days[1], (3.5, 5.1)), patient="pat-1")
        self.fake.add(obs("plan-glu1", "2345-7", "Glucose", 97, "mg/dL", days[1], (70, 99)), patient="pat-1")
        res = self.pull(cid)
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["counts"]["results"], res["counts"].get("updated")), (2, None), res)
        self.assertEqual(self.counts()["results"], n["results"] + 2)
        mine, labdays = said()
        self.assertEqual({x: v for x, v in mine.items() if x[2]}, before[0], "the hospital's results and rows as they were")
        self.assertEqual({x: v for x, v in mine.items() if not x[2]}, {("Potassium, Blood", days[1][:10], False): [(3.5, 5.1, "", "3.5-5.1", "")],
                                                                    ("Glucose, Blood", days[1][:10], False): [(70, 99, "", "70-99", "")]},
                         "the source's copy, its own range and row, and its new result")
        joined = lambda plan_k: [before[1][0], (*before[1][1][:2], before[1][1][2] + [["Glucose", "97", "mg/dL", "70-99"], ["Potassium", "5.05", "mmol/L", plan_k]])]   # noqa: E731
        self.assertEqual(labdays, joined("3.5-5.1"), "one lab day a draw: the hospital's, its rows as they were, joined by the "
                         "source's on the second day, the duplicate's row left out on the first (#897)")
        self.assertEqual([self.draw(t[:10])["lab days"][0][0] for t in days], ["2 lab results", "4 lab results"], "titled for what it holds")
        self.assertEqual([self.draw(t[:10])["faults"] for t in days], [[]] * 2, "each result with its row, each row with its result")
        # its own Observation amended: updated in place, the hospital's as it was
        self.fake.update("Observation", "plan-k1", {"referenceRange": [{"low": {"value": 3.5}, "high": {"value": 5.0}, "text": "3.5-5.0"}]})
        again = self.pull(cid)
        self.assertEqual((again["counts"]["results"], again["counts"].get("updated")), (0, 1), again)
        mine, labdays = said()
        self.assertEqual({x: v for x, v in mine.items() if x[2]}, before[0], "the hospital's as they were")
        self.assertEqual(labdays, joined("3.5-5.0"))
        self.assertEqual(mine[("Potassium, Blood", days[1][:10], False)], [(3.5, 5.0, "H", "3.5-5.0", "H")], "the source's own, updated")
        for iid in (again["import"], res["import"]):
            self.assertEqual(self.robin.post(f"/api/profiles/{self.pid}/imports/{iid}/undo", headers=H).status_code, 200)
        self.assertEqual(said(), before, "both undone: as the upload left them")
        self.assertEqual([self.draw(t[:10])["lab days"][0][0] for t in days], ["2 lab results"] * 2)

    LDL_CORRECTED = {"referenceRange": [{"text": "<200 mg/dL", "high": {"value": 200, "unit": "mg/dL"}}],
                     "interpretation": [{"coding": [{"system": INTERP, "code": "N"}]}]}

    def amended_where_it_is(self, cid, before):
        """(#899) LDL corrected at the source ("<130 mg/dL" and High, then "<200 mg/dL" and Normal) and pulled: the result
        here and its row updated where they are, whoever's import holds them, nothing added, the draw's one lab day.
        -> the pull's result"""
        n = self.counts()
        self.assertEqual(before["LDL Cholesterol"], [(None, 130, "H", "<130 mg/dL", "H")])
        self.assertEqual([t for t, _ in before["lab days"]], ["4 lab results"])
        self.fake.update("Observation", "obs-ldl", self.LDL_CORRECTED)
        res = self.pull(cid)
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["counts"]["results"], res["counts"].get("updated")), (0, 1), res)
        after = self.draw()
        self.assertEqual(after["LDL Cholesterol"], [(None, 200, "", "<200 mg/dL", "")], "one result, as sent now, its row with it")
        self.assertEqual(after["lab days"], [(t, [r if r[0] != "LDL Cholesterol" else [*r[:3], "<200 mg/dL", ""] for r in rows])
                                             for t, rows in before["lab days"]], "the draw's one lab day, its LDL row updated")
        self.assertEqual({k: v for k, v in self.counts().items() if k != "imports"}, {k: v for k, v in n.items() if k != "imports"}, "nothing added")
        self.assertEqual(after["faults"], [])
        return res

    def undo(self, *imports):
        for iid in imports:
            self.assertEqual(self.robin.post(f"/api/profiles/{self.pid}/imports/{iid}/undo", headers=H).status_code, 200)

    def test_a_pull_updates_an_uploads_rows_where_they_are_and_joins_its_lab_day(self):   # #899
        """The portal's own export uploaded, then the portal connected: its first pull finds everything already here. LDL
        corrected at the source updated the upload's result but left its row reading "<130 mg/dL", now unflagged
        (sync._update_in_place, _link_labdays, #885: only the connection's own events of a day), and added a second lab day
        for the draw. Now the result and its row are updated where they are, in the draw's one lab day; a result new that
        day (an ALT) joins that lab day, its row beside it in the pull's own labitem. Undone, each pull puts back what it
        changed, result and row."""
        self.upload_bundle([json.loads(json.dumps(r)) for r in self.fake.resources(patient="pat-1")])
        cid = self.connect(self.source())
        self.first_pull(cid)
        before, n = self.draw(), self.counts()
        self.assertEqual(before["faults"], [])
        res = self.amended_where_it_is(cid, before)
        lab_day = self.draw()["lab days"][0][1]
        self.fake.add({"resourceType": "Observation", "id": "obs-alt-899", "status": "final", "category": [{"coding": [{"code": "laboratory"}]}],
                       "code": {"coding": [{"system": "http://loinc.org", "code": "1742-6"}], "text": "ALT"}, "effectiveDateTime": "2025-03-04T09:45:00-06:00",
                       "valueQuantity": {"value": 60, "unit": "U/L"}, "interpretation": [{"coding": [{"system": INTERP, "code": "H"}]}]}, patient="pat-1")
        more = self.pull(cid)
        self.assertEqual((more["counts"]["results"], more["counts"]["kinds"]), (1, {}), more)
        joined = self.draw()
        self.assertEqual(joined["lab days"], [("5 lab results", lab_day + [["ALT", "60", "U/L", "", "H"]])], "the draw's one lab day, joined")
        self.assertEqual((joined["ALT"], joined["faults"]), ([(None, None, "H", "", "H")], []), "its row beside its result")
        self.undo(more["import"], res["import"])
        self.assertEqual(self.draw(), before, "undone: result and row as the upload left them")
        self.assertEqual(self.counts(), n)

    def test_a_lab_day_a_pull_joined_keeps_its_labitems_whatever_is_undone(self):   # #899
        """The upload's lab day joined by two pulls (an ALT, then a GGT, each its own labitem): the first pull undone
        before the second, the lab day lists the second's labitem still, the first's gone with it; the upload undone,
        its lab day goes to the pull whose labitem it lists, holding only that, so the GGT keeps its lab day."""
        upload = self.upload_bundle([json.loads(json.dumps(r)) for r in self.fake.resources(patient="pat-1")])
        cid = self.connect(self.source())
        self.first_pull(cid)
        before = self.draw()["lab days"][0][1]
        test = lambda oid, code, name, v: {"resourceType": "Observation", "id": oid, "status": "final", "category": [{"coding": [{"code": "laboratory"}]}],   # noqa: E731
                                           "code": {"coding": [{"system": "http://loinc.org", "code": code}], "text": name},
                                           "effectiveDateTime": "2025-03-04T09:45:00-06:00", "valueQuantity": {"value": v, "unit": "U/L"}}
        self.fake.add(test("obs-alt-899", "1742-6", "ALT", 60), patient="pat-1")
        alt = self.pull(cid)
        self.fake.add(test("obs-ggt-899", "2324-2", "GGT", 30), patient="pat-1")
        ggt = self.pull(cid)
        rows = {"ALT": ["ALT", "60", "U/L", "", ""], "GGT": ["GGT", "30", "U/L", "", ""]}
        self.assertEqual(self.draw()["lab days"], [("6 lab results", before + [rows["ALT"], rows["GGT"]])])
        self.undo(alt["import"])
        drawn = self.draw()
        self.assertEqual((drawn["lab days"], drawn["faults"]), ([("5 lab results", before + [rows["GGT"]])], []))
        self.undo(upload["import_id"])
        drawn = self.draw()
        self.assertEqual((drawn["lab days"], drawn["faults"]), ([("1 lab result", [rows["GGT"]])], []), "the GGT keeps its lab day")
        self.undo(ggt["import"])
        self.assertEqual({k: v for k, v in self.counts().items() if k != "imports"}, {"events": 0, "series": 0, "results": 0}, "nothing left")

    def test_every_order_of_undoing_a_joined_lab_day_leaves_it_listing_what_there_is(self):   # #908
        """The draw's lab day, the upload's (U: the portal's export uploaded first) or the connection's own (P0: its first
        pull), joined by two pulls (P1 an ALT, P2 a GGT, each its own lab event), undone in each of the six orders: after
        each undo the lab day lists only lab events there are, titled for the rows they hold, each result with its row and
        each row with its result, and there's no lab day once nothing of that day is left. Undoing U (or P0), then P2,
        handed the lab day to P1, but P2's undo then wrote it back as P1 had left it, listing U's lab events, gone: "5 lab
        results" over the one ALT row."""
        import itertools
        from server.importers import common
        test = lambda oid, code, name, v: {"resourceType": "Observation", "id": oid, "status": "final", "category": [{"coding": [{"code": "laboratory"}]}],   # noqa: E731
                                           "code": {"coding": [{"system": "http://loinc.org", "code": code}], "text": name},
                                           "effectiveDateTime": "2025-03-04T09:45:00-06:00", "valueQuantity": {"value": v, "unit": "U/L"},
                                           "interpretation": [{"coding": [{"system": INTERP, "code": "H"}]}]}
        for first in ("U", "P0"):
            for order in itertools.permutations((first, "P1", "P2")):
                with self.subTest(first=first, order=order):
                    self.afresh(f"Undone {first} {' '.join(order)} (fictional)")   # (a source and a profile of its own each time)
                    imps = {"U": self.upload_bundle([json.loads(json.dumps(r)) for r in self.fake.resources(patient="pat-1")])["import_id"]} if first == "U" else {}
                    cid = self.connect(self.source())
                    imps["P0"] = self.first_pull(cid)["import_id"]
                    self.fake.add(test("obs-alt-908", "1742-6", "ALT", 60), patient="pat-1")
                    imps["P1"] = self.pull(cid)["import"]
                    self.fake.add(test("obs-ggt-908", "2324-2", "GGT", 70), patient="pat-1")
                    imps["P2"] = self.pull(cid)["import"]
                    self.assertTrue(all(imps[k] for k in (first, "P1", "P2")), imps)
                    undone = []
                    for k in order:
                        self.undo(imps[k])
                        undone.append(k)
                        if len(undone) == 3:
                            self.assertEqual(self.counts()["events"], 0, f"after {undone}: nothing left")
                            break
                        data = self.data()
                        days = [e for e in data["events"] if e["k"] == "lab" and e["t"][:10] == "2025-03-04"]
                        have = {e["id"] for e in data["events"]}
                        listed = [i for e in days for i in data["details"][str(e["id"])]["ids"]]
                        rows = sum(len(data["details"][str(i)]["rows"]) for i in listed if i in have)
                        tests = {s["name"] for s in data["labs"] if any(p[0][:10] == "2025-03-04" for p in s["pts"]) and s.get("panel") != "Vitals"}
                        self.assertEqual((len(days), [i for i in listed if i not in have], [e["title"] for e in days], self.faults()),
                                         (1, [], [common.lab_day_title(rows)], []), f"after {undone}: one lab day, listing what there is, titled for it")
                        self.assertEqual(("ALT" in tests, "GGT" in tests, "Hemoglobin" in tests),
                                         ("P1" not in undone, "P2" not in undone, first not in undone), f"after {undone}: {tests}")

    def test_a_pull_joins_its_own_sources_lab_day_of_the_draw(self):   # #912
        """A hospital's FHIR report of the day (a metabolic panel, its own lab day) uploaded, then the portal's export,
        then the portal connected: a pull's new result (an ALT) joined the earliest lab day of its date, the hospital's,
        so the portal's draw was split across two lab days and the hospital's listed the portal's ALT. Now it joins its
        own source's lab day, the one listing the portal's records (its export's), and the hospital's is as it was;
        undone, as before."""
        bmp = [{"resourceType": "DiagnosticReport", "id": "hosp-bmp", "status": "final", "category": [{"coding": [{"code": "LAB"}]}],
                "code": {"text": "Basic metabolic panel"}, "effectiveDateTime": "2025-03-04T07:10:00-06:00",
                "result": [{"reference": "Observation/hosp-k"}, {"reference": "Observation/hosp-na"}]},
               lab_obs("hosp-k", "2823-3", "Potassium", 5.3, "mmol/L", "H", (3.5, 5.1), t="2025-03-04T07:10:00-06:00"),
               lab_obs("hosp-na", "2951-2", "Sodium", 140, "mmol/L", None, (135, 145), t="2025-03-04T07:10:00-06:00")]
        self.upload_bundle(bmp)
        hospital = self.draw()["lab days"]
        self.upload_bundle([json.loads(json.dumps(r)) for r in self.fake.resources(patient="pat-1")])
        cid = self.connect(self.source())
        self.first_pull(cid)
        before, n = self.draw(), self.counts()
        self.assertEqual(sorted(t for t, _ in before["lab days"]), ["2 lab results", "4 lab results"])
        self.fake.add(lab_obs("obs-alt-912", "1742-6", "ALT", 60, "U/L", "H", (7, 56)), patient="pat-1")
        alt = self.pull(cid)
        self.assertEqual(alt["counts"]["results"], 1, alt)
        drawn = self.draw()
        portal = [rows for t, rows in before["lab days"] if (t, rows) not in hospital][0]
        self.assertEqual(sorted(drawn["lab days"]), sorted(hospital + [("5 lab results", portal + [["ALT", "60", "U/L", "7-56", "H"]])]),
                         "the ALT in the portal's lab day, the hospital's as it was")
        self.assertEqual(self.faults(), [])
        self.undo(alt["import"])
        self.assertEqual((self.draw(), self.counts()), (before, n), "undone: as before")

    def test_each_lab_event_is_listed_by_one_lab_day_from_the_first_pull(self):   # #910
        """Part of the portal's export uploaded (the CBC report alone), then the portal connected: its first pull's new
        results (the LDL, the urine colour) made a lab day of their own beside the upload's, two for one draw, and a
        later pull then listed the upload's CBC in the connection's lab day as well: its rows in both drawers, the badge
        2 out of range against the drawer's 3. Now the first pull's results join the upload's lab day, one for the draw,
        and so do the later pulls'; each lab event is listed by one lab day, whichever is undone. Saved before (two lab
        days for the draw, as the first pull left them), a later pull lists none of the upload's in the connection's lab
        day, and one listing the upload's CBC lists it no more once the upload is undone."""
        from server import records
        hgb_n = {"interpretation": [{"coding": [{"system": INTERP, "code": "N"}]}],
                 "referenceRange": [{"low": {"value": 12.0, "unit": "g/dL"}, "high": {"value": 15.5, "unit": "g/dL"}}]}
        part = lambda: [json.loads(json.dumps(r)) for r in self.fake.resources(patient="pat-1") if r["id"] in ("pat-1", "dr-cbc", "obs-hgb", "obs-plt")]   # noqa: E731
        up = self.upload_bundle(part())["import_id"]
        cid = self.connect(self.source())
        p0 = self.first_pull(cid)
        drawn = self.draw()
        self.assertEqual(([(t, len(rows)) for t, rows in drawn["lab days"]], self.faults()), ([("4 lab results", 4)], []), "one lab day for the draw")
        self.assertNotIn("lab", p0["counts"]["kinds"], "the first pull's lab day joined the upload's: none of its own")
        self.fake.update("Observation", "obs-hgb", hgb_n)
        p1 = self.pull(cid)["import"]
        self.fake.add(lab_obs("obs-alt-910", "1742-6", "ALT", 60, "U/L", "H", (7, 56)), patient="pat-1")
        p2 = self.pull(cid)["import"]
        drawn = self.draw()
        self.assertEqual(([(t, len(rows)) for t, rows in drawn["lab days"]], self.faults()), ([("5 lab results", 5)], []))
        self.undo(up)
        self.assertEqual(([(t, len(rows)) for t, rows in self.draw()["lab days"]], self.faults()), ([("3 lab results", 3)], []), "handed over, holding the pulls'")
        self.undo(p2, p1, p0["import_id"])
        self.assertEqual({k: v for k, v in self.counts().items() if k != "imports"}, {"events": 0, "series": 0, "results": 0})
        # as saved before: the first pull's lab day its own, beside the upload's
        self.afresh("Two lab days for one draw (fictional)")
        up = self.upload_bundle(part())["import_id"]
        cid = self.connect(self.source())
        with mock.patch.object(sync, "_fold_first", lambda *a: None):
            self.first_pull(cid)
        self.assertEqual(len(self.draw()["lab days"]), 2)
        self.fake.add(lab_obs("obs-alt-910", "1742-6", "ALT", 60, "U/L", "H", (7, 56)), patient="pat-1")
        self.pull(cid)
        self.assertEqual(self.faults(), [], "each lab event listed by one of the two")
        db = dbm.connect()
        try:   # the connection's lab day listing the upload's CBC too, as a pull left it before
            labdays = {r["import_id"]: r for r in db.execute("SELECT id, import_id, detail FROM events WHERE profile_id=?", (self.pid,)).fetchall()
                       if (records._dec(r["detail"]) or {}).get("type") == "labday"}
            ours, theirs = [records._dec(labdays[i]["detail"]) for i in sorted(labdays)][::-1][0], records._dec(labdays[up]["detail"])
            mine = [i for i in labdays if i != up][0]
            db.execute("UPDATE events SET detail=? WHERE id=?", (records._enc({**ours, "ids": ours["ids"] + theirs["ids"]}), labdays[mine]["id"]))
            records._bump(db, self.pid)
        finally:
            db.close()
        self.assertTrue(any("listed by 2 lab days" in f for f in self.faults()))
        self.undo(up)
        self.assertEqual(self.faults(), [], "the upload's CBC gone from the connection's lab day with it")

    def test_connected_again_a_pull_updates_the_first_connections_rows(self):   # #899
        """Connected and pulled, disconnected keeping its records, connected again (a new connection, whose first pull
        finds them already here). LDL corrected at the source updated the result, but its row, in the first connection's
        lab event, still read "<130 mg/dL", and the pull added a second lab day for the draw. Now both where they are, in
        the one lab day; undone, as before."""
        sid = self.source()
        cid = self.connect(sid)
        self.first_pull(cid)
        r = self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/disconnect", json={"remove_records": False}, headers=H)
        self.assertEqual(r.json()["removed"], 0)
        cid2 = self.connect(sid, patient="pat-1")
        self.first_pull(cid2)
        before, n = self.draw(), self.counts()
        res = self.amended_where_it_is(cid2, before)
        self.undo(res["import"])
        self.assertEqual(self.draw(), before, "undone: result and row as they were")
        self.assertEqual(self.counts(), n)

    def test_a_result_new_on_a_day_the_connection_has_gets_its_row_beside_it(self):   # #899
        """A pull's result new on a day its connection has: its row went in the first pull's labitem of that day, updated
        whole in its own import (sync._update_in_place), so the drawer, reading a row's result from the row's own import
        (drawer.js rowPoint), found none for it. Now each new row goes in the pull's own labitem, beside its result,
        joining the day's lab day; undone, as before."""
        cid = self.connect(self.source())
        self.first_pull(cid)
        before = self.draw()
        self.fake.add({"resourceType": "Observation", "id": "obs-alt-899", "status": "final", "category": [{"coding": [{"code": "laboratory"}]}],
                       "code": {"coding": [{"system": "http://loinc.org", "code": "1742-6"}], "text": "ALT"}, "effectiveDateTime": "2025-03-04T09:45:00-06:00",
                       "valueQuantity": {"value": 60, "unit": "U/L"}}, patient="pat-1")
        alt = self.pull(cid)
        self.assertEqual(alt["counts"]["results"], 1)
        drawn = self.draw()
        self.assertEqual(drawn["lab days"], [("5 lab results", before["lab days"][0][1] + [["ALT", "60", "U/L", "", ""]])])
        self.assertEqual(drawn["faults"], [], "each result with its row, each row with its result")
        self.undo(alt["import"])
        self.assertEqual(self.draw(), before)

    def test_a_value_its_source_amends_is_updated_in_place(self):   # #877
        """LDL's value amended at the source (162, then 165, the same Observation): a pull added it as a second result,
        with a row of its own beside the old one, so the old (wrong) value showed beside the corrected one, in Labs and
        in the lab day. Now the same record from the same source is updated in place whatever changes, result and row,
        where they are (the uploaded export's, or the connection's own), nothing added; undone, the old value is back."""
        for upload in (True, False):
            with self.subTest(upload=upload):
                self.afresh(f"LDL amended, upload {upload} (fictional)")
                if upload:
                    self.upload_bundle([json.loads(json.dumps(r)) for r in self.fake.resources(patient="pat-1")])
                cid = self.connect(self.source())
                self.first_pull(cid)
                before, n = self.draw(), self.counts()
                self.fake.update("Observation", "obs-ldl", {"valueQuantity": {"value": 165, "unit": "mg/dL"}})
                ldl = self.pull(cid)
                self.assertEqual((ldl["counts"]["results"], ldl["counts"].get("updated")), (0, 1), ldl)
                drawn = self.draw()
                self.assertEqual((drawn["LDL Cholesterol"], self.faults()), ([(None, 130, "H", "<130 mg/dL", "H")], []), "one LDL, with its row")
                self.assertEqual(drawn["lab days"], [(t, [r if r[0] != "LDL Cholesterol" else [r[0], "165", *r[2:]] for r in rows])
                                                     for t, rows in before["lab days"]], "its row says 165, where it was")
                ldl_pts = [p[1] for s in self.data()["labs"] if s["name"] == "LDL Cholesterol" for p in s["pts"]]
                self.assertEqual((ldl_pts, {k: v for k, v in self.counts().items() if k != "imports"}),
                                 ([165], {k: v for k, v in n.items() if k != "imports"}), "updated, nothing added")
                self.undo(ldl["import"])
                self.assertEqual((self.draw(), [p[1] for s in self.data()["labs"] if s["name"] == "LDL Cholesterol" for p in s["pts"]]),
                                 (before, [162]), "undone: 162 again")

    def test_a_result_a_pull_adds_to_a_report_has_its_row_beside_it(self):   # #881
        """A GGT the portal adds to the CBC report it sent before: the report's labitem was updated whole where it was,
        the upload's (the portal's export uploaded first) or the first pull's, so the GGT's row sat in another import's
        labitem than its result, and the drawer, finding a row's result in the row's own import (drawer.js rowPoint,
        crossmatch._row_of), found none: a result with no row, a row with no result. Now the report's rows are placed
        each by its own record: the GGT's goes in a labitem of the pull's own of that report, beside its result, in the
        draw's lab day; the next pull changing Hemoglobin's flag and the GGT's range finds each where it is; undone, as
        before. A row saved apart from its result before (a pull's ALT whose row went in the first pull's labitem, as
        before #899) is moved beside it when a pull reads that day again, so its corrected range updates the result too."""
        hgb_n = {"interpretation": [{"coding": [{"system": INTERP, "code": "N"}]}],
                 "referenceRange": [{"low": {"value": 12.0, "unit": "g/dL"}, "high": {"value": 15.5, "unit": "g/dL"}}]}
        for upload in (True, False):
            with self.subTest(upload=upload):
                self.afresh(f"GGT added, upload {upload} (fictional)")
                if upload:
                    self.upload_bundle([json.loads(json.dumps(r)) for r in self.fake.resources(patient="pat-1")])
                cid = self.connect(self.source())
                self.first_pull(cid)
                before, n = self.draw(), self.counts()
                self.fake.add(lab_obs("obs-ggt-881", "2324-2", "GGT", 70, "U/L", "H", (8, 61)), patient="pat-1")
                cbc = self.fake.get("DiagnosticReport", "dr-cbc")
                self.fake.update("DiagnosticReport", "dr-cbc", {"result": cbc["result"] + [{"reference": "Observation/obs-ggt-881"}]})
                ggt = self.pull(cid)
                self.assertEqual(ggt["counts"]["results"], 1, ggt)
                drawn = self.draw()
                self.assertEqual((drawn["GGT"], self.faults()), ([(8, 61, "H", "8-61", "H")], []), "its row beside its result, in the draw's lab day")
                self.assertEqual([(t, len(rows)) for t, rows in drawn["lab days"]], [("5 lab results", 5)])
                self.fake.update("Observation", "obs-hgb", hgb_n)
                self.fake.update("Observation", "obs-ggt-881", {"referenceRange": [{"low": {"value": 8}, "high": {"value": 75}, "text": "8-75"}],
                                                                "interpretation": [{"coding": [{"system": INTERP, "code": "N"}]}]})
                more = self.pull(cid)
                drawn = self.draw()
                self.assertEqual((more["counts"]["results"], drawn["GGT"], drawn["Hemoglobin"], self.faults()),
                                 (0, [(8, 75, "", "8-75", "")], [(12.0, 15.5, "", "12.0 - 15.5", "")], []), "each updated where it is")
                self.undo(more["import"], ggt["import"])
                self.assertEqual((self.draw(), self.counts()), (before, n), "undone: as before")
        # a row apart from its result, as saved before #899: the ALT's in the first pull's labitem of that day
        from server import records
        self.afresh("ALT apart (fictional)")
        cid = self.connect(self.source())
        p0 = self.first_pull(cid)["import_id"]
        self.fake.add(lab_obs("obs-alt-881", "1742-6", "ALT", 60, "U/L", "H", (7, 56)), patient="pat-1")
        alt = self.pull(cid)["import"]
        db = dbm.connect()
        try:   # its row moved into the first pull's labitem of that day, its own labitem gone, as #899 found them
            mine = [r for r in db.execute("SELECT id, import_id, body, detail FROM events WHERE profile_id=?", (self.pid,)).fetchall()
                    if (records._dec(r["body"]) or {}).get("k") == "labitem" and str((records._dec(r["body"]) or {}).get("key")).startswith("fhir:labitem:")]
            first, pulled = [r for r in mine if r["import_id"] == p0][0], [r for r in mine if r["import_id"] == alt][0]
            a, b = records._dec(first["detail"]), records._dec(pulled["detail"])
            db.execute("UPDATE events SET detail=? WHERE id=?", (records._enc({**a, "rows": a["rows"] + b["rows"], "keys": a["keys"] + b["keys"]}), first["id"]))
            db.execute("DELETE FROM events WHERE id=?", (pulled["id"],))
            for r in db.execute("SELECT id, detail FROM events WHERE profile_id=?", (self.pid,)).fetchall():
                d = records._dec(r["detail"])
                if isinstance(d, dict) and d.get("type") == "labday":
                    db.execute("UPDATE events SET detail=? WHERE id=?", (records._enc({**d, "ids": [i for i in d["ids"] if i != pulled["id"]]}), r["id"]))
            records._bump(db, self.pid)
        finally:
            db.close()
        self.assertIn("ALT at 2025-03-04T09:45:00-06:00 (import %d): no row" % alt, self.faults())
        self.fake.update("Observation", "obs-alt-881", {"referenceRange": [{"low": {"value": 7}, "high": {"value": 65}, "text": "7-65"}],
                                                        "interpretation": [{"coding": [{"system": INTERP, "code": "N"}]}]})
        fixed = self.pull(cid)
        drawn = self.draw()
        self.assertEqual((fixed["counts"]["results"], drawn["ALT"], self.faults()), (0, [(7, 65, "", "7-65", "")], []),
                         "one ALT, its result and its row both corrected, side by side")

    def strip_keys(self, pid=None):
        """Each labitem as saved before #885: no "keys". -> {event id: its keys as they were}"""
        from server import records
        db = dbm.connect()
        try:
            had = {}
            for r in db.execute("SELECT id, detail FROM events WHERE profile_id=?", (pid or self.pid,)).fetchall():
                d = records._dec(r["detail"])
                if isinstance(d, dict) and "keys" in d:
                    had[r["id"]] = d.pop("keys")
                    db.execute("UPDATE events SET detail=? WHERE id=?", (records._enc(d), r["id"]))
            return had
        finally:
            db.close()

    def keys_now(self, ids):
        from server import records
        db = dbm.connect()
        try:
            return {i: (records._dec(db.execute("SELECT detail FROM events WHERE id=?", (i,)).fetchone()["detail"]) or {}).get("keys") for i in ids}
        finally:
            db.close()

    def test_lab_events_saved_before_885_get_their_rows_keys(self):   # #899, schema version 40
        """A labitem saved before #885 has no "keys": its rows' Observations were named nowhere, so LDL corrected at the
        source and pulled again was never found as the same record (#869): its result left as it was, a duplicate, beside
        its row updated. Schema step 40 marks them, and at start-up sync.backfill_keys gives a connection's theirs from
        its store, each row by the row the records it read make. An upload's has nothing left naming them (its files went
        with its job) and keeps none. Then the corrected LDL is updated in place, result and row, the one lab day; undone,
        as before. A connection made again to the same portal gives the first one's records theirs from its own store."""
        from server import records, uploads
        db = dbm.connect()
        try:   # another source's file, of another day: its labitem's keys can't be worked out again
            up = uploads.create(db, self.pid, None)
            uploads.add_file(db, self.pid, up, "hospital.json", io.BytesIO(json.dumps({"resourceType": "Bundle", "type": "collection", "entry": [{"resource": {
                "resourceType": "Observation", "id": "hosp-na", "status": "final", "category": [{"coding": [{"code": "laboratory"}]}],
                "code": {"coding": [{"system": "http://loinc.org", "code": "2951-2"}], "text": "Sodium"}, "effectiveDateTime": "2025-05-06T08:30:00-05:00",
                "valueQuantity": {"value": 140, "unit": "mmol/L"}}}]}).encode()))
            uploads.finish(db, self.pid, up)
        finally:
            db.close()
        r = self.robin.post(f"/api/profiles/{self.pid}/uploads/{up}/import", json={}, headers=H)
        jid, end = r.json()["id"], time.time() + 30
        while time.time() < end and (j := self.robin.get(f"/api/profiles/{self.pid}/jobs/{jid}").json())["state"] != "done":
            if j["state"] == "preview":
                self.robin.post(f"/api/profiles/{self.pid}/jobs/{jid}/confirm", headers=H)
            time.sleep(0.05)
        sid = self.source()
        cid = self.connect(sid)
        self.first_pull(cid)
        before = self.draw()
        had = self.strip_keys()
        self.assertEqual(len(had), 3, "the upload's labitem, and the pull's: its report's and its day's")
        db = dbm.connect()
        try:
            db.executescript("BEGIN;" + dbm.SCHEMA[39] + "COMMIT;")
            self.assertTrue(dbm.meta_get(db, sync.KEYS_TO_GIVE), "the schema step marks the work")
            self.assertEqual(sync.backfill_keys(db), 2, "the connection's two")
            self.assertIsNone(dbm.meta_get(db, sync.KEYS_TO_GIVE), "once")
            self.assertEqual(sync.backfill_keys(db), 0)
            upload = db.execute("SELECT id FROM events WHERE import_id=?", (j["import_id"],)).fetchall()
        finally:
            db.close()
        theirs = {i: k for i, k in had.items() if i not in {x["id"] for x in upload}}
        self.assertEqual(self.keys_now(theirs), theirs, "the connection's, each row's Observation as #885 keeps them")
        self.assertEqual(set(self.keys_now(set(had) - set(theirs)).values()), {None}, "the upload's: none")
        self.assertEqual(self.draw(), before)
        res = self.amended_where_it_is(cid, before)
        self.undo(res["import"])
        self.assertEqual(self.draw(), before, "undone: result and row as they were")
        # the same portal connected again, in another profile: the first connection's store went at Disconnect, and
        # the new one's gives its records their keys
        db = dbm.connect()
        other = prof.create_profile(db, "Robin again (fictional)", owner_id=self.uids["robin"])
        db.close()
        was, self.pid = self.pid, other
        try:
            first = self.connect(sid)
            self.first_pull(first)
            self.robin.post(f"/api/profiles/{self.pid}/connections/{first}/disconnect", json={"remove_records": False}, headers=H)
            self.first_pull(self.connect(sid, patient="pat-1"))
            had = self.strip_keys()
            self.assertEqual(len(had), 2)
            db = dbm.connect()
            try:
                db.executescript("BEGIN;" + dbm.SCHEMA[39] + "COMMIT;")
                self.assertEqual(sync.backfill_keys(db), 2)
            finally:
                db.close()
            self.assertEqual(self.keys_now(had), had)
        finally:
            self.pid = was

    def test_lab_events_saved_before_885_are_given_their_rows_keys_at_a_pull(self):   # #909
        """Saved before #885 (no "keys"), as sync.backfill_keys can't reach: the portal's export uploaded, then the portal
        connected (its first pull found it all here: the connection has no lab events of its own, and the upload's files
        are gone), and the portal connected, disconnected keeping its records and connected again after the upgrade (the
        first connection's store went at Disconnect). LDL corrected at the source added a second result (162 against
        <200 beside 162 against <130, High). Now a pull gives the lab events of the days it reads theirs from what the
        connection had read (its store), each row the one it makes the same, and the corrected LDL is updated in place,
        result and row, the one lab day; undone, as before. Another source's lab event of that day, under the same day's
        key, keeps none, its LDL as it was."""
        from server import records
        for case in ("uploaded", "connected again"):
            with self.subTest(case=case):
                self.afresh(f"Before #885, {case} (fictional)")
                sid = self.source()
                if case == "uploaded":
                    self.upload_bundle([json.loads(json.dumps(r)) for r in self.fake.resources(patient="pat-1")])
                else:
                    first = self.connect(sid)
                    self.first_pull(first)
                    self.robin.post(f"/api/profiles/{self.pid}/connections/{first}/disconnect", json={"remove_records": False}, headers=H)
                db = dbm.connect()
                try:   # another source's results of that day on their own, keyed by the same day, an LDL among them
                    db.execute("BEGIN IMMEDIATE")
                    other = records.store(db, self.pid, {"events": [
                        {"id": 1, "t": "2025-03-04T07:10:00", "k": "labitem", "title": "Lab results", "hidden": 1, "key": "fhir:labitem:2025-03-04", "abn": 1},
                        {"id": 2, "t": "2025-03-04T07:10:00", "k": "lab", "title": "2 lab results", "sub": "Lab results", "key": "fhir:labday:2025-03-04", "abn": 1}],
                        "details": {"1": {"type": "result", "order": "Lab results", "collected": "2025-03-04 07:10",
                                          "rows": [["LDL Cholesterol", "162", "mg/dL", "<100 mg/dL", "H"], ["Sodium", "140", "mmol/L", "135-145", ""]]},
                                    "2": {"type": "labday", "ids": [1]}},
                        "labs": [{"name": "LDL Cholesterol", "unit": "mg/dL", "key": "hospital:ldl", "pts": [["2025-03-04T07:10:00", 162, None, 100, "H", "162"]]},
                                 {"name": "Sodium", "unit": "mmol/L", "key": "hospital:na", "pts": [["2025-03-04T07:10:00", 140, 135, 145, "", "140"]]}]},
                        "upload", fmt="fhir")
                    db.execute("COMMIT")
                finally:
                    db.close()
                had = self.strip_keys()
                db = dbm.connect()
                try:
                    db.executescript("BEGIN;" + dbm.SCHEMA[39] + "COMMIT;")
                    self.assertEqual(sync.backfill_keys(db), 0, "nothing a connection's store at start-up can reach")
                finally:
                    db.close()
                cid = self.connect(sid, patient="pat-1")
                self.first_pull(cid)
                before, n = self.draw(), self.counts()
                self.assertEqual((sorted(before["LDL Cholesterol"]), len(before["lab days"])), ([(None, 100, "H", "<100 mg/dL", "H"), (None, 130, "H", "<130 mg/dL", "H")], 2))
                self.fake.update("Observation", "obs-ldl", self.LDL_CORRECTED)
                res = self.pull(cid)
                self.assertEqual((res["counts"]["results"], res["counts"].get("updated")), (0, 1), res)
                drawn = self.draw()
                self.assertEqual((sorted(drawn["LDL Cholesterol"]), self.faults()), ([(None, 100, "H", "<100 mg/dL", "H"), (None, 200, "", "<200 mg/dL", "")], []),
                                 "the portal's LDL and its row corrected, nothing added; the other's as it was")
                self.assertEqual({k: v for k, v in self.counts().items() if k != "imports"}, {k: v for k, v in n.items() if k != "imports"})
                db = dbm.connect()
                try:
                    theirs = [(records._dec(r["detail"]) or {}).get("keys") for r in db.execute("SELECT detail FROM events WHERE import_id=?", (other,))]
                finally:
                    db.close()
                self.assertEqual(theirs, [None, None], "another source's lab event of that day keeps none")
                self.assertTrue(had)
                self.undo(res["import"])
                self.assertEqual((self.draw(), self.counts()), (before, n), "undone: result and row as they were")

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
        self.fake = self.serve(fixtures=PAYER_FIXTURES)
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
        # labs work the same way, series and all, once they're off (#922: refused while they're pulled)
        r = self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/remove", json={"kind": "labs"}, headers=H)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("Turn lab results off", r.json()["detail"])
        self.assertEqual(self.patch(cid, {"kinds": [k for k in kinds if k != "labs"]}).status_code, 200)
        r = self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/remove", json={"kind": "labs"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse([s for s in self.data()["labs"] if s.get("panel") != "Vitals"])
        self.assertTrue([s for s in self.data()["labs"] if s.get("panel") == "Vitals"], "vitals stay")
        self.robin.post(f"/api/profiles/{self.pid}/imports/{r.json()['import']}/undo", headers=H)
        self.assertEqual(self.counts()["results"], before["results"])
        self.assertEqual(self.kim.post(f"/api/profiles/{self.pid}/connections/{cid}/remove", json={"kind": "labs"},
                                       headers=H).status_code, 403)   # viewers can't

    def test_put_back_leaves_out_what_was_pulled_again(self):   # #922
        """Medications, then the problem list, then lab results: each turned off and removed, turned on again, changed
        at the source (med-1 completed, cond-1 given a note, LDL corrected) and pulled, and the removal undone from the
        history. Put back added the removed records beside the ones pulled again: "fhir:MedicationRequest/med-1" twice,
        cond-1 twice, two LDLs and two lab days for one draw. Now what's here again stays as the source sends it now,
        once, and the rest comes back (cond-2, which no pull sent again; the draw's other results, in the one lab day).
        And removing a kind that's still pulled is refused."""
        from server import crossmatch
        cid = self.connect(self.source())
        self.first_pull(cid)
        allk = list(conns.KINDS)

        def remove(kind, change):
            r = self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/remove", json={"kind": kind}, headers=H)
            self.assertEqual(r.status_code, 409, "refused while it's pulled")
            self.assertEqual(self.patch(cid, {"kinds": [k for k in allk if k != kind]}).status_code, 200)
            r = self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/remove", json={"kind": kind}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(self.patch(cid, {"kinds": allk}).status_code, 200)
            change()
            self.assertTrue(self.pull(cid)["ok"])
            self.undo(r.json()["import"])
            return self.data()

        meds = lambda d: sorted(e["key"] for e in d["events"] if e["k"] == "med")   # noqa: E731
        probs = lambda d: sorted(x["key"] for x in d.get("problems") or [] if x.get("type") != "Allergy")   # noqa: E731
        labs = lambda d: sorted((s["name"], len(s["pts"]) + len(s.get("txt") or [])) for s in d["labs"] if s.get("panel") != "Vitals")   # noqa: E731
        days = lambda d: [(e["title"], e["sub"]) for e in d["events"] if e["k"] == "lab"]   # noqa: E731
        before, had = self.data(), {k: v for k, v in self.counts().items() if k != "imports"}

        d = remove("medications", lambda: self.fake.update("MedicationRequest", "med-1", {"status": "completed"}))
        self.assertEqual(meds(d), meds(before), "med-1 once")
        med = next(e for e in d["events"] if e.get("key") == "fhir:MedicationRequest/med-1")
        self.assertEqual(d["details"][str(med["id"])]["status"], "Completed", "as the source sends it now")

        d = remove("problems", lambda: self.fake.update("Condition", "cond-1", {"note": [{"text": "Changed at the source (fictional)"}]}))
        self.assertEqual(probs(d), probs(before), "cond-1 once, cond-2 back")

        d = remove("labs", lambda: self.fake.update("Observation", "obs-ldl", self.LDL_CORRECTED))
        self.assertEqual(labs(d), labs(before), "each result once")
        self.assertEqual(days(d), days(before), "one lab day for the draw, listing them all")
        ldl = next(s for s in d["labs"] if s["name"] == "LDL Cholesterol")
        self.assertEqual(ldl["pts"][0][3], 200, "the LDL as corrected")
        self.assertEqual(crossmatch.lab_faults(d), [])
        self.assertEqual({k: v for k, v in self.counts().items() if k in ("series", "results")},
                         {k: v for k, v in had.items() if k in ("series", "results")}, "as many results as before")
        self.assertEqual(self.counts()["events"], had["events"] + 1, "the LDL's row in a lab event of the pull's own")

    def joined_draw(self, first):
        """(#914) The draw's lab day, the upload's (first "U": the portal's export uploaded first) or the connection's
        own (first "P0": its first pull), joined by two pulls (P1: LDL corrected, an ALT new; P2: the Platelet Count
        given a range, a GGT new). -> (connection, {name: import})"""
        self.afresh(f"Joined draw, {first} (fictional)")
        imps = {"U": self.upload_bundle([json.loads(json.dumps(r)) for r in self.fake.resources(patient="pat-1")])["import_id"]} if first == "U" else {}
        cid = self.connect(self.source())
        imps["P0"] = self.first_pull(cid)["import_id"]
        self.fake.update("Observation", "obs-ldl", self.LDL_CORRECTED)
        self.fake.add(lab_obs("obs-alt-914", "1742-6", "ALT", 60, "U/L", "H", (7, 56)), patient="pat-1")
        imps["P1"] = self.pull(cid)["import"]
        self.fake.update("Observation", "obs-plt", {"referenceRange": [{"low": {"value": 150}, "high": {"value": 500}}],
                                                    "interpretation": [{"coding": [{"system": INTERP, "code": "N"}]}]})
        self.fake.add(lab_obs("obs-ggt-914", "2324-2", "GGT", 70, "U/L", "H", (8, 61)), patient="pat-1")
        imps["P2"] = self.pull(cid)["import"]
        self.assertTrue(all(imps[k] for k in (first, "P1", "P2")), imps)
        return cid, imps

    def test_remove_put_back_and_undo_in_any_order(self):   # #914
        """The draw's lab day (the upload's, or the connection's own) joined by two pulls; the connection's labs removed
        (R), put back (B, undoing the removal), and the three imports undone, in every order with R before B. A removal
        left the removed lab events' ids in the upload's lab day so that Put back brought them back, and an Undo of a
        pull meanwhile (#908's filter) dropped them for good; it took out the connection's own lab day with its pulls'
        "before" records (sync_changes, by cascade), so after Put back an Undo no longer saw it ("6 lab results" over 5);
        and the upload undone meanwhile took its lab day with it, Put back bringing the ALT and the GGT back with none.
        Now after each step every result has its row in one lab day, each lab day lists what there is, titled for it, its
        badge and drawer agreeing (lab_faults), and once put back the records are what the same undos alone leave."""
        import itertools
        state = lambda: ((self.draw() if self.counts()["events"] else None), {x: v for x, v in self.counts().items() if x != "imports"})   # noqa: E731
        for first in ("U", "P0"):
            undos = (first, "P1", "P2")
            seen = {}   # {imports undone: the records they leave, without a removal}
            for k in range(4):
                for done in itertools.combinations(undos, k):
                    _, imps = self.joined_draw(first)
                    self.undo(*[imps[x] for x in done])
                    seen[frozenset(done)] = state()
            for order in itertools.permutations(undos + ("R", "B")):
                if order.index("R") > order.index("B"):
                    continue
                with self.subTest(first=first, order=order):
                    cid, imps = self.joined_draw(first)
                    done = set()
                    for step in order:
                        if step == "R":
                            self.assertEqual(self.patch(cid, {"kinds": [k for k in conns.KINDS if k != "labs"]}).status_code, 200)
                            r = self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/remove", json={"kind": "labs"}, headers=H)
                            if r.status_code == 409:   # nothing of it left to remove: no removal to put back
                                imps["R"] = None
                                continue
                            self.assertEqual(r.status_code, 200, r.text)
                            imps["R"] = r.json()["import"]
                        elif step == "B":
                            if imps["R"]:
                                self.undo(imps["R"])
                        else:
                            self.undo(imps[step])
                            done.add(step)
                        self.assertEqual(self.faults(), [], f"after {order[:order.index(step) + 1]}")
                        if "R" not in order[:order.index(step) + 1] or "B" in order[:order.index(step) + 1]:
                            self.assertEqual(state(), seen[frozenset(done)],
                                             f"after {order[:order.index(step) + 1]}: as the undos alone leave it")
                    self.assertEqual({x: v for x, v in self.counts().items() if x != "imports"}, {"events": 0, "series": 0, "results": 0})

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

    def upload_bundle(self, resources, base=None):
        """Upload the resources as a FHIR bundle file, as someone would from their portal's export (or another server's,
        base), and save it."""
        from server import uploads
        body = json.dumps({"resourceType": "Bundle", "type": "collection",
                           "entry": [{"fullUrl": f"{base or self.fake.fhir_base}/{r['resourceType']}/{r['id']}", "resource": r}
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
        # the problem list removed (turned off first, #922): allergies stay, and the other way round
        on = [k for k in conns.KINDS if k not in self.NEW_KINDS]
        self.assertEqual(self.patch(cid, {"kinds": [k for k in on if k != "problems"]}).status_code, 200)
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
        self.assertEqual(self.patch(cid, {"kinds": [k for k in on if k not in ("problems", "documents")]}).status_code, 200)
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

    def test_a_connection_keeps_the_address_it_was_made_with(self):   # #926
        """An admin can't move a source's FHIR base while a profile is connected to it, as they can't remove it; a
        connection whose source was moved anyway (before #926) sends its tokens to no other server; and a sign-in
        started at the old address doesn't finish at the new one."""
        sid = self.source()
        cid = self.connect(sid)
        other = self.serve(patients=["robin-example"], smart_config={"token_endpoint": self.fake.token_url})
        r = self.robin.patch(f"/api/connect/sources/{sid}", json={"issuer": other.fhir_base}, headers=H)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("still connected", r.json()["detail"])
        src = lambda: next(x for x in self.robin.get("/api/connect/sources").json()["sources"] if x["id"] == sid)
        self.assertEqual((src()["issuer"], src()["tested"] is not None), (self.fake.fhir_base, True))
        self.assertEqual(self.robin.patch(f"/api/connect/sources/{sid}", json={"name": "Renamed 926"}, headers=H).status_code, 200)
        # moved anyway, as an admin could before: its tested discovery the other server's, which names a revocation
        # endpoint there. The connection reaches no host of the other server, and its disconnect sends it nothing
        db = dbm.connect()
        old = dict(db.execute("SELECT issuer, discovery FROM smart_clients WHERE id=?", (sid,)).fetchone())
        db.execute("UPDATE smart_clients SET issuer=?, discovery=? WHERE id=?",
                   (other.fhir_base, json.dumps(other.smart_configuration()), sid))
        from server import outside
        self.assertNotIn(smart.host_of(other.url), outside._connection_hosts(db, cid))
        db.close()
        r = self.robin.post(f"/api/profiles/{self.pid}/connections/{cid}/disconnect", json={"remove_records": False}, headers=H)
        self.assertEqual(r.json(), {"revoked": False, "removed": 0})   # the page says to end access in the portal
        self.assertEqual(other.requests, [])
        db = dbm.connect()
        db.execute("UPDATE smart_clients SET issuer=?, discovery=? WHERE id=?", (old["issuer"], old["discovery"], sid))
        db.close()
        # nobody connected now: the address can be corrected, and a sign-in started at the old one is used up
        url = self.start(sid)
        r = self.robin.patch(f"/api/connect/sources/{sid}", json={"issuer": other.fhir_base}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(self.robin.post(f"/api/connect/sources/{sid}/test", headers=H).json()["ok"])
        r = self.back(self.fake.authorize(url, patient="pat-1"))
        self.assertEqual(r.status_code, 400, r.text)
        db = dbm.connect()
        self.assertIsNone(db.execute("SELECT 1 FROM connections WHERE client_id=? AND state != 'disconnected'", (sid,)).fetchone())
        db.close()

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
