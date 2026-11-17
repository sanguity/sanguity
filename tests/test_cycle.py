#!/usr/bin/env python3
"""Menstrual cycle tracking, opt-in (#183; server/cycle.py, cycle_tracking.py, routes_cycle.py, watch/receiver.py).

Off, nothing is kept: the receiver takes every cycle record out of an upload before it archives it (raw/ and its
database never see them), and sexual activity is taken out of every upload, on or off. On, Health Connect's and
Apple Health's records (fictional payloads in each app's shape) are kept and shown as spans and marks, in words that
describe. Deleting (or turning it off) removes them from the receiver's database and every archived upload, and
hides them at once. Only owners turn it on, off or delete; owners and editors see it; viewers get nothing. Never in
a share link; in an export and the visit summary only when ticked. All data here is fictional.

  python3 tests/test_cycle.py
"""
import datetime, glob, hashlib, hmac, importlib.util, io, json, os, re, sys, tempfile, threading, unittest, urllib.request, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-cycle-test-")
RX = os.path.join(TMP, "rx")   # the receiver's DATA, laid out as compose mounts it for the app
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "build"),
                  SANGUITY_WATCH_FILE=os.path.join(RX, "public", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  SANGUITY_WATCH_KEYS=os.path.join(RX, "keys", "receiver-secrets.json"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_CORRECTIONS=os.path.join(TMP, "config", "corrections.json"))
for d in ("records", "keys", "build", "config", "profiles", "rx/keys", "rx/public"):
    os.makedirs(os.path.join(TMP, d), exist_ok=True)
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, crypto, cycle, cycle_tracking, db as dbm, export, main, profiles as prof, records, settings, share_links  # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
spec = importlib.util.spec_from_file_location("watch_receiver", os.path.join(ROOT, "watch", "receiver.py"))
rx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rx)
rx.DATA, rx.KEYS_FILE, rx.SECRET_FILE = RX, os.environ["SANGUITY_WATCH_KEYS"], os.path.join(TMP, "no-legacy-secret")

H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites", "lee": "maple tunnel brisk saddle"}
TZ = "America/Chicago"
FLOWS = [("2026-03-02", 1), ("2026-03-03", 2), ("2026-03-04", 3), ("2026-03-05", 2), ("2026-03-06", 1)]
# words a cycle feature must never say: no predictions, no fertile windows, no judgements
PREDICTS = re.compile(r"predict|fertil|expected|next period|likely|window|late|irregular|normal|should", re.I)


def utc(day, hour=8):
    """A local Chicago time (UTC-6 in March) as Health Connect's instant."""
    t = datetime.datetime.fromisoformat(f"{day}T{hour:02d}:00:00") + datetime.timedelta(hours=6)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def ldc(seq=1, **extra):
    """Life Dashboard Companion's upload: a period logged as one, its flow day by day, an ovulation test, cervical
    mucus, spotting, sexual activity (never kept), basal body temperature, and steps (always kept)."""
    return {"source": "health_connect", "timezone": TZ, "sequence": seq, "timestamp": "2026-03-20T12:00:00Z",
            "menstruation_period": [{"uuid": "hc-period-1", "start_time": "2026-03-02T06:00:00Z", "end_time": "2026-03-07T06:00:00Z", "source": "Fictional Cycle App"}],
            "menstruation_flow": [{"uuid": f"hc-flow-{d}", "time": utc(d), "flow": f, "source": "Fictional Cycle App"} for d, f in FLOWS],
            "ovulation_test": [{"uuid": "hc-ovu-1", "time": utc("2026-03-15"), "result": "RESULT_POSITIVE", "source": "Fictional Cycle App"}],
            "cervical_mucus": [{"uuid": "hc-mucus-1", "time": utc("2026-03-14"), "appearance": 5, "sensation": 1, "source": "Fictional Cycle App"}],
            "intermenstrual_bleeding": [{"uuid": "hc-spot-1", "time": utc("2026-03-19", 22), "source": "Fictional Cycle App"}],   # 04:00 UTC the 20th
            "sexual_activity": [{"uuid": "hc-sex-1", "time": utc("2026-03-12"), "protection_used": 1}],
            "basal_body_temperature": [{"uuid": "hc-bbt-1", "time": utc("2026-03-12"), "celsius": 36.4}],
            "steps": [{"bucket_start": "2026-03-10T14:00:00Z", "bucket_end": "2026-03-10T15:00:00Z", "total": 900, "complete": True}],
            "daily_totals": [{"date": "2026-03-10", "steps": 900}], **extra}


def hae():
    """Health Auto Export's upload: flow in words and numbers (one marked as a cycle's start), an LH-surge test,
    spotting by HealthKit's own name, sexual activity, and steps."""
    z = "-0600"
    return {"data": {"metrics": [
        {"name": "menstrual_flow", "units": "", "data": [
            {"date": f"2026-01-0{d} 00:00:00 {z}", "value": v, "source": "Fictional Phone", **({"isCycleStart": True} if d == 5 else {})}
            for d, v in ((5, "Light"), (6, "Heavy"), (7, 3), (8, "HKCategoryValueMenstrualFlowLight"), (9, "None"))]},
        {"name": "ovulation_test_result", "units": "", "data": [{"date": f"2026-01-18 07:30:00 {z}", "value": "LH surge", "source": "Fictional Phone"}]},
        {"name": "HKCategoryTypeIdentifierIntermenstrualBleeding", "data": [{"date": f"2026-01-23 09:00:00 {z}", "source": "Fictional Phone"}]},
        {"name": "Sexual Activity", "units": "", "data": [{"date": f"2026-01-12 22:00:00 {z}", "qty": 1}]},
        {"name": "step_count", "units": "count", "data": [{"date": f"2026-01-10 09:00:00 {z}", "qty": 4000, "source": "Fictional Phone"}]}]}}


def sealed_keys(doc):
    crypto.write_file(rx.KEYS_FILE, json.dumps(doc).encode())
    rx._keys.update(sig=False, doc=None)


def archived(slot):
    """Every archived upload of a slot, as text."""
    return [rx.read_raw(p).decode() for _, p in rx.raw_files(slot)]


CYCLE_WORDS = ("menstruation", "menstrual", "ovulation", "cervical", "intermenstrual", "sexual", "basal_body", "hc-flow", "LH surge")


class Records(unittest.TestCase):
    """server/cycle.py on its own: each app's shapes, the spans and marks, the words."""
    def setUp(self):
        import sqlite3
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(cycle.TABLE)

    def test_health_connect(self):
        from zoneinfo import ZoneInfo
        n = cycle.from_health_connect(self.con.cursor(), ldc(), ZoneInfo(TZ))
        self.assertEqual(n, 9, "a period, five flow days, a test, mucus, spotting")
        recs = cycle.records(self.con)
        self.assertEqual([(r["kind"], r["day"], r.get("value")) for r in recs if r["kind"] == "flow"],
                         [("flow", d, {1: "light", 2: "medium", 3: "heavy"}[f]) for d, f in FLOWS])
        self.assertIn({"kind": "period", "day": "2026-03-02", "end": "2026-03-06", "app": "Health Connect", "source": "Fictional Cycle App"}, recs,
                      "a period ending at midnight ends the day before")
        self.assertIn({"kind": "spotting", "day": "2026-03-19", "app": "Health Connect", "source": "Fictional Cycle App"}, recs,
                      "the day is the phone's: 10 pm in Chicago, the 20th in UTC")
        self.assertIn({"kind": "mucus", "day": "2026-03-14", "value": "egg white", "detail": "light", "app": "Health Connect",
                       "source": "Fictional Cycle App"}, recs)
        self.assertIn("positive", [r.get("value") for r in recs if r["kind"] == "ovulation"])
        cycle.from_health_connect(self.con.cursor(), {"deleted_records": [{"type": "ovulation_test", "uuid": "hc-ovu-1"}]}, ZoneInfo(TZ))
        self.assertNotIn("ovulation", [r["kind"] for r in cycle.records(self.con)], "deleted on the phone: deleted here")

    def test_health_auto_export(self):
        for m in hae()["data"]["metrics"]:
            cycle.from_hae(self.con.cursor(), m, rx.hae_when)
        recs = cycle.records(self.con)
        self.assertEqual([(r["day"], r.get("value")) for r in recs if r["kind"] == "flow"],
                         [("2026-01-05", "light"), ("2026-01-06", "heavy"), ("2026-01-07", "medium"), ("2026-01-08", "light"), ("2026-01-09", "none")],
                         "HealthKit's words, its numbers (3: medium) and its identifiers")
        self.assertEqual({(r["kind"], r.get("value")) for r in recs if r["kind"] != "flow"}, {("ovulation", "positive"), ("spotting", None)})
        self.assertNotIn("sexual", json.dumps(recs).lower())

    def test_view_describes_what_was_recorded(self):
        from zoneinfo import ZoneInfo
        cycle.from_health_connect(self.con.cursor(), ldc(), ZoneInfo(TZ))
        for m in hae()["data"]["metrics"]:
            cycle.from_hae(self.con.cursor(), m, rx.hae_when)
        # Apple Health also sent flow on a day Health Connect did, and the same spotting: Health Connect's is the day's
        both = {"name": "menstrual_flow", "data": [{"date": "2026-03-04 00:00:00 -0600", "value": "Light", "source": "Fictional Phone"},
                                                   {"date": "2026-03-10 00:00:00 -0600", "value": "Light", "source": "Fictional Phone"}]}
        cycle.from_hae(self.con.cursor(), both, rx.hae_when)
        v = cycle.view(cycle.records(self.con))
        self.assertEqual([(s["from"], s["to"], s["logged"]) for s in v["spans"]],
                         [("2026-01-05", "2026-01-08", False), ("2026-03-02", "2026-03-06", True), ("2026-03-10", "2026-03-10", False)],
                         "a logged period, runs of flow days outside it; a day of no flow isn't one")
        march = v["spans"][1]
        self.assertEqual(march["flow"], [[d, {1: "light", 2: "medium", 3: "heavy"}[f]] for d, f in FLOWS], "the 4th: Health Connect's heavy")
        self.assertEqual([(m["kind"], m["day"]) for m in v["marks"]],
                         [("ovulation", "2026-01-18"), ("spotting", "2026-01-23"), ("mucus", "2026-03-14"), ("ovulation", "2026-03-15"), ("spotting", "2026-03-19")])
        said = [cycle.words(s) for s in v["spans"]] + [(cycle.mark_words(m),) for m in v["marks"]]
        self.assertEqual(said[1], ("Period, 5 days", "Logged as a period in Health Connect · flow light, medium, heavy"))
        self.assertEqual(said[0][0], "Flow recorded on 4 days")
        self.assertIn(("Cervical mucus: egg white (sensation: light)",), said)
        for words in said:
            self.assertIsNone(PREDICTS.search(" ".join(words)), words)

    def test_cycle_start_splits_a_run(self):
        rows = [{"date": f"2026-02-0{d} 00:00:00 -0600", "value": "Light", **({"isCycleStart": 1} if d == 3 else {})} for d in (1, 2, 3)]
        cycle.from_hae(self.con.cursor(), {"name": "menstrual_flow", "data": rows}, rx.hae_when)
        self.assertEqual([(s["from"], s["to"]) for s in cycle.view(cycle.records(self.con))["spans"]],
                         [("2026-02-01", "2026-02-02"), ("2026-02-03", "2026-02-03")])

    def test_strip(self):
        p = ldc(deleted_records=[{"type": "menstruation_flow", "uuid": "x"}, {"type": "sleep", "uuid": "y"}])
        self.assertEqual(cycle.strip(p, False), 8, "every cycle list, sexual activity, basal temperature, the cycle deletion")
        self.assertEqual(sorted(p), ["daily_totals", "deleted_records", "sequence", "source", "steps", "timestamp", "timezone"])
        self.assertEqual(p["deleted_records"], [{"type": "sleep", "uuid": "y"}])
        p = ldc()
        self.assertEqual(cycle.strip(p, True), 1, "on: only sexual activity goes")
        self.assertIn("menstruation_flow", p)
        h = hae()
        self.assertEqual(cycle.strip(h, False), 4)
        self.assertEqual([m["name"] for m in h["data"]["metrics"]], ["step_count"])
        h = hae()
        cycle.strip(h, True)
        self.assertNotIn("Sexual Activity", [m["name"] for m in h["data"]["metrics"]])
        body = json.dumps({"source": "health_connect", "steps": []}).encode()
        self.assertIs(cycle.keep(body, False), body, "nothing to take out: the upload as it came, byte for byte")


def signed(body, secret):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class App(unittest.TestCase):
    """The app and the receiver together, as compose runs them: the app writes the receiver's secrets file, the receiver
    reads it, and the app reads the receiver's cycle.json."""
    @classmethod
    def setUpClass(cls):
        dbm.init()
        cls.ctx = TestClient(main.app, base_url="http://testserver").__enter__()
        db = dbm.connect()
        cls.uid = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        cls.pid = prof.create_profile(db, "Robin (fictional)", owner_id=cls.uid["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (cls.uid["kim"], cls.pid))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (cls.uid["lee"], cls.pid))
        with dbm.transaction(db):
            records.store(db, cls.pid, {"built": "2026-03-20", "source": "fictional", "details": {}, "costs": [], "upcoming": [], "labs": [],
                                        "events": [{"id": 1, "t": "2026-02-01T09:00:00", "k": "visit", "title": "Office visit"}]}, "upload", fmt="fhir")
        db.close()
        cls.c = {}
        for n in PW:
            c = TestClient(main.app, base_url="http://testserver")
            assert c.post("/api/login", json={"username": n, "password": PW[n]}, headers=H).status_code == 200
            cls.c[n] = c
        r = cls.c["robin"].post(f"/api/profiles/{cls.pid}/watch/receiver/rotate", headers=H)
        cls.secret = r.json()["secret"]
        cls.slot = json.loads(crypto.read_file(rx.KEYS_FILE))["profiles"][0]["slot"]
        cls.http = rx.ThreadingHTTPServer(("127.0.0.1", 0), rx.H)
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()
        cls.stores = {}

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.ctx.__exit__(None, None, None)

    def url(self, rest=""):
        return f"/api/profiles/{self.pid}/cycle{rest}"

    def post(self, payload, hae_app=False):
        """Upload to the receiver as the phone would, then fold it in as its worker does."""
        body = json.dumps(payload).encode()
        hdr = {"Authorization": f"Bearer {self.secret}"} if hae_app else {"X-Signature": signed(body, self.secret)}
        req = urllib.request.Request(f"http://127.0.0.1:{self.http.server_address[1]}/ingest", data=body, headers=hdr, method="POST")
        self.assertEqual(urllib.request.urlopen(req).status, 200)
        slot, kept, digest = rx.work.get(timeout=5)
        store = self.stores.setdefault(slot, rx.Store(slot))
        store.ingest(json.loads(kept), digest)
        store.save()
        store.write_summary()
        return store

    def test_1_off_means_nothing_is_kept(self):
        self.assertFalse(self.c["robin"].get(self.url()).json()["on"], "off by default")
        store = self.post(ldc())
        self.post(hae(), hae_app=True)
        for text in archived(self.slot):
            for w in CYCLE_WORDS:
                self.assertNotIn(w, text, "not even in the raw archive")
        self.assertIn('"steps"', archived(self.slot)[0], "the rest of the upload is kept")
        self.assertEqual(store.db.execute("SELECT COUNT(*) FROM cycle").fetchone()[0], 0)
        self.assertFalse(os.path.exists(rx.cycle_file(self.slot)))
        self.assertEqual(self.c["robin"].get(self.url()).json()["spans"], [])
        image = crypto.read_file(store.path)
        self.assertNotIn(b"hc-flow", image)
        self.assertNotIn(b"menstru", rx.read_raw(rx.raw_files(self.slot)[0][1]))

    def test_2_roles(self):
        self.assertEqual(self.c["kim"].get(self.url()).status_code, 403, "viewers don't see it")
        self.assertFalse(self.c["lee"].get(self.url()).json()["can_change"])
        self.assertTrue(self.c["robin"].get(self.url()).json()["can_change"])
        for r in (self.c["lee"].put(self.url(), json={"on": True}, headers=H), self.c["lee"].post(self.url("/delete"), headers=H),
                  self.c["kim"].put(self.url(), json={"on": True}, headers=H)):
            self.assertEqual(r.status_code, 403, "only owners turn it on or delete it")
        self.assertEqual(self.c["robin"].put(self.url(), json={"on": True}).status_code, 403, "the CSRF check, like every change")

    def test_3_on_keeps_both_apps_and_shows_them(self):
        r = self.c["robin"].put(self.url(), json={"on": True}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["on"])
        doc = json.loads(crypto.read_file(rx.KEYS_FILE))
        self.assertEqual(doc["cycle"], [self.slot], "the receiver is told")
        self.post(ldc(seq=2))
        store = self.post(hae(), hae_app=True)
        text = "\n".join(archived(self.slot))
        self.assertIn("menstruation_flow", text, "on: archived, so the receiver can rebuild")
        self.assertNotIn("sexual", text.lower(), "sexual activity: never, on or off")
        self.assertNotIn("Sexual", text)
        daily = crypto.read_file(rx.summary_file(self.slot)).decode()
        for w in ("menstru", "ovulation", "cervical", "period"):
            self.assertNotIn(w, daily.lower(), "never in daily.json, which the export and share links read")
        st = self.c["lee"].get(self.url()).json()
        self.assertEqual([(s["from"], s["to"], s["title"]) for s in st["spans"]],
                         [("2026-01-05", "2026-01-08", "Flow recorded on 4 days"), ("2026-03-02", "2026-03-06", "Period, 5 days")])
        self.assertEqual(len(st["marks"]), 5)
        self.assertEqual(st["count"], store.db.execute("SELECT COUNT(*) FROM cycle").fetchone()[0])
        self.assertIsNone(PREDICTS.search(json.dumps([[s["title"], s["text"]] for s in st["spans"]] + [m["title"] for m in st["marks"]])))
        self.assertTrue(crypto.file_is_encrypted(rx.cycle_file(self.slot)), "sealed on disk")
        self.assertNotIn(b"hc-flow", open(rx.cycle_file(self.slot), "rb").read())

    def test_4_never_in_share_links_export_or_summary_unless_ticked(self):
        r = self.c["robin"].post(f"/api/profiles/{self.pid}/links", json={"hours": 24, "sections": list(share_links.SECTION_IDS)}, headers=H)
        view = TestClient(main.app).post("/api/share/view", json={"token": r.json()["path"].split("#", 1)[1]}, headers=H).json()
        for w in ("menstru", "ovulation", "cervical", "Period, 5 days", "Flow recorded"):
            self.assertNotIn(w, json.dumps(view), "a share link never carries it")
        plain = zipfile.ZipFile(io.BytesIO(self.c["lee"].get(f"/api/profiles/{self.pid}/export").content))
        self.assertNotIn("cycle/cycle.json", plain.namelist(), "not unless ticked")
        self.assertNotIn(b"menstru", b"".join(plain.read(n) for n in plain.namelist()).lower())
        ticked = zipfile.ZipFile(io.BytesIO(self.c["lee"].get(f"/api/profiles/{self.pid}/export?cycle=1").content))
        got = json.loads(ticked.read("cycle/cycle.json"))
        self.assertEqual([s["title"] for s in got["spans"]], ["Flow recorded on 4 days", "Period, 5 days"])
        self.assertIn("cycle/cycle.json", ticked.read("README.txt").decode())
        self.assertEqual(self.c["kim"].get(f"/api/profiles/{self.pid}/export?cycle=1").status_code, 403)
        # loaded into another profile, an export leaves the cycle records out (and doesn't refuse the zip)
        zipped = self.c["lee"].get(f"/api/profiles/{self.pid}/export?cycle=1").content
        db = dbm.connect()
        try:
            other = prof.create_profile(db, "Copy (fictional)", owner_id=self.uid["robin"])
            export.import_archive(db, other, io.BytesIO(zipped))
            kept = [p for p in glob.glob(os.path.join(prof.profile_dir(other), "**", "*"), recursive=True) if "cycle" in os.path.basename(p)]
            self.assertEqual(kept, [])
            log = [json.loads(crypto.dec(r["detail"])) if r["detail"] else None for r in db.execute(
                "SELECT detail FROM activity WHERE profile_id=? AND action='export' ORDER BY id", (self.pid,))]
        finally:
            db.close()
        self.assertIn({"cycle": True}, log)
        self.assertIn(None, log)
        # the visit summary: offered to owners and editors only, and never ticked by default
        opts = self.c["kim"].get(f"/api/profiles/{self.pid}/summary").json()
        self.assertNotIn("cycle", [s["id"] for s in opts["sections"]])
        mine = {s["id"]: s for s in self.c["lee"].get(f"/api/profiles/{self.pid}/summary").json()["sections"]}
        self.assertTrue(mine["cycle"]["ask"])
        self.assertEqual(self.c["kim"].post(f"/api/profiles/{self.pid}/summary/preview", json={"sections": ["cycle"], "since": "2025-12-01"},
                                            headers=H).status_code, 403)
        r = self.c["lee"].post(f"/api/profiles/{self.pid}/summary/preview", json={"sections": ["medications", "cycle"], "since": "2025-12-01"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        rows = next(x for x in r.json()["extra"] if x["id"] == "cycle")["rows"]
        self.assertIn({"when": "2026-03-02", "title": "Period, 5 days", "text": "Logged as a period in Health Connect · flow light, medium, heavy"}, rows)
        r = self.c["lee"].post(f"/api/profiles/{self.pid}/summary/preview", json={"sections": ["medications"], "since": "2025-12-01"}, headers=H)
        self.assertNotIn("cycle", [x["id"] for x in r.json()["extra"]])
        self.assertNotIn("Period", json.dumps(r.json()))

    def test_5_delete_reaches_every_copy_it_can(self):
        store = self.stores[self.slot]
        r = self.c["robin"].post(self.url("/delete"), headers=H)
        st = r.json()
        self.assertTrue(st["on"], "deleting leaves it on")
        self.assertEqual((st["spans"], st["marks"]), ([], []), "hidden at once, before the receiver has done it")
        self.assertIsNone(st["deletion"]["done"])
        token = json.loads(crypto.read_file(rx.KEYS_FILE))["cycle_purge"][self.slot]
        rx.cycle_sweep(self.stores)   # the receiver's worker, between uploads
        self.assertEqual(store.db.execute("SELECT COUNT(*) FROM cycle").fetchone()[0], 0)
        for text in archived(self.slot):
            for w in CYCLE_WORDS:
                self.assertNotIn(w, text, "every archived upload rewritten without them")
        self.assertIn('"steps"', "".join(archived(self.slot)), "the rest of each upload stays")
        reloaded = rx.Store(self.slot)
        self.assertEqual(reloaded.db.execute("SELECT COUNT(*) FROM cycle").fetchone()[0], 0, "the saved database too")
        self.assertNotIn(b"hc-flow", crypto.read_file(reloaded.path), "vacuumed: nothing left in its free pages")
        self.assertEqual(reloaded.catch_up(), 0, "rewritten uploads aren't folded in again")
        said = json.loads(crypto.read_file(rx.cycle_file(self.slot)))
        self.assertEqual((said["purged"], said["records"]), (token, []))
        st = self.c["robin"].get(self.url()).json()
        self.assertTrue(st["deletion"]["done"])
        rx.cycle_sweep(self.stores)
        self.assertEqual(json.loads(crypto.read_file(rx.cycle_file(self.slot)))["purged_at"], said["purged_at"], "done once")
        # still on: the next upload is kept again
        self.post(ldc(seq=3))
        self.assertEqual(len(self.c["robin"].get(self.url()).json()["spans"]), 1)

    def test_6_turning_off_deletes_too(self):
        r = self.c["robin"].put(self.url(), json={"on": False}, headers=H)
        self.assertFalse(r.json()["on"])
        self.assertEqual(r.json()["spans"], [])
        doc = json.loads(crypto.read_file(rx.KEYS_FILE))
        self.assertEqual(doc["cycle"], [])
        rx.cycle_sweep(self.stores)
        self.assertEqual(self.stores[self.slot].db.execute("SELECT COUNT(*) FROM cycle").fetchone()[0], 0)
        self.assertFalse(any("menstru" in t for t in archived(self.slot)))
        self.assertTrue(self.c["robin"].get(self.url()).json()["deletion"]["done"])
        self.post(ldc(seq=4))
        self.assertFalse(any("menstru" in t for t in archived(self.slot)), "off again: left out again")
        db = dbm.connect()
        try:
            log = [json.loads(crypto.dec(r["detail"])) for r in db.execute(
                "SELECT detail FROM activity WHERE profile_id=? AND action='cycle' ORDER BY id", (self.pid,))]
            row = db.execute("SELECT value, sealed FROM settings WHERE key=? AND profile_id=?", (cycle_tracking.KEY, self.pid)).fetchone()
            self.assertNotIn("_cycle", json.dumps(settings.to_file(db, self.pid)), "not in a settings export")
        finally:
            db.close()
        self.assertEqual(log, [{"change": "on"}, {"change": "delete"}, {"change": "off"}], "logged, never a record")
        self.assertIsNone(row["value"])
        self.assertTrue(row["sealed"].startswith(crypto.FIELD), "the setting is sealed")
        self.assertNotIn("_cycle", json.dumps(self.c["robin"].get(f"/api/profiles/{self.pid}/settings").json()))


class Receiver(unittest.TestCase):
    """The receiver on its own: what it does before it knows, and uploads archived before this feature."""
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rx-", dir=TMP)
        self.keys = os.path.join(self.dir, "keys.json")
        self.old = rx.DATA, rx.KEYS_FILE
        rx.DATA, rx.KEYS_FILE = self.dir, self.keys
        rx._keys.update(sig=False, doc=None)

    def tearDown(self):
        rx.DATA, rx.KEYS_FILE = self.old
        rx._keys.update(sig=False, doc=None)

    def test_old_archives_are_scrubbed_once(self):
        body = json.dumps(ldc()).encode()
        rx.archive(body, "")   # archived whole, as the receiver did before #183
        store = rx.Store("")
        store.ingest(json.loads(body), hashlib.sha256(body).hexdigest())
        self.assertEqual(store.db.execute("SELECT COUNT(*) FROM cycle").fetchone()[0], 0, "no keys file: no profile has it on")
        self.assertIn("menstruation", archived("")[0])
        rx.cycle_sweep({"": store})
        self.assertNotIn("menstruation", archived("")[0], "an upload from before is scrubbed")
        self.assertNotIn("sexual_activity", archived("")[0])
        self.assertIn("daily_totals", archived("")[0])
        self.assertEqual(store.meta("cycle_clean"), "1", "once")

    def test_unreadable_keys_keep_nothing_and_delete_nothing(self):
        with open(self.keys, "wb") as f:
            f.write(b"not sealed")
        self.assertIsNone(rx.cycle_state())
        self.assertFalse(rx.cycle_on(""))
        sealed_keys({"profiles": [], "cycle": [""]})
        self.assertTrue(rx.cycle_on(""))
        store = rx.Store("")
        body = json.dumps(ldc()).encode()
        store.ingest(json.loads(body), hashlib.sha256(body).hexdigest())
        self.assertGreater(store.db.execute("SELECT COUNT(*) FROM cycle").fetchone()[0], 0)
        with open(self.keys, "wb") as f:
            f.write(b"broken again, mid-write")
        rx.cycle_sweep({"": store})   # what was read last stands: still on, nothing deleted
        self.assertGreater(store.db.execute("SELECT COUNT(*) FROM cycle").fetchone()[0], 0)

    def test_a_deletion_for_a_profile_with_nothing_received(self):
        sealed_keys({"profiles": [], "cycle": [], "cycle_purge": {"7": "token-7"}})
        rx.cycle_sweep({})
        self.assertEqual(json.loads(crypto.read_file(rx.cycle_file("7")))["purged"], "token-7", "said done: there was nothing")
        self.assertFalse(os.path.isdir(rx.slot_dir("7")), "and no store made for it")

    def test_reprocess_remembers_deletions_done(self):
        sealed_keys({"profiles": [], "cycle": [""], "cycle_purge": {"": "token-a"}})
        store = rx.Store("")
        store.cycle_purge("token-a")
        rx.reprocess()
        self.assertEqual(rx.Store("").meta("cycle_purged"), "token-a", "a rebuild doesn't delete again what came after")


if __name__ == "__main__":
    unittest.main(verbosity=2)
