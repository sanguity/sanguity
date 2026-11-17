"""iPhone sync through Health Auto Export (#148): watch/receiver.py takes the app's REST API uploads beside Life
Dashboard Companion's, on the same address, with the profile's secret in an "Authorization: Bearer" header, and
folds them into the same daily summary. All data here is fictional, written for these tests.

  python tests/test_receiver_hae.py
"""
import hashlib, hmac, importlib.util, json, os, shutil, socket, sys, tempfile, threading, unittest, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="sanguity-hae-")
os.environ["SANGUITY_KEY_FILE"] = os.path.join(TMP, "keys", "data_key")

from server import crypto   # noqa: E402
from server.importers import apple_health as ah   # noqa: E402

crypto.make_key_file(os.environ["SANGUITY_KEY_FILE"])
crypto.keyring.load()
spec = importlib.util.spec_from_file_location("watch_receiver", os.path.join(ROOT, "watch", "receiver.py"))
rx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rx)

Z = "-0500"
W, P = "Fictional Watch", "Fictional Phone"

# ---- the same readings twice: as an iPhone's export.xml, and as Health Auto Export sends them
XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE HealthData [
<!ELEMENT HealthData (ExportDate,Me,(Record|Correlation|Workout|ActivitySummary)*)>
]>
<HealthData locale="en_US">
 <ExportDate value="2024-03-11 10:00:00 {Z}"/>
 <Record type="HKQuantityTypeIdentifierStepCount" sourceName="{W}" unit="count" startDate="2024-03-09 08:00:00 {Z}" endDate="2024-03-09 08:59:00 {Z}" value="400"/>
 <Record type="HKQuantityTypeIdentifierStepCount" sourceName="{W}" unit="count" startDate="2024-03-09 09:00:00 {Z}" endDate="2024-03-09 09:59:00 {Z}" value="600"/>
 <Record type="HKQuantityTypeIdentifierStepCount" sourceName="{P}" unit="count" startDate="2024-03-09 08:00:00 {Z}" endDate="2024-03-09 08:59:00 {Z}" value="500"/>
 <Record type="HKQuantityTypeIdentifierStepCount" sourceName="{W}" unit="count" startDate="2024-03-10 10:00:00 {Z}" endDate="2024-03-10 10:30:00 {Z}" value="300"/>
 <Record type="HKQuantityTypeIdentifierDistanceWalkingRunning" sourceName="{W}" unit="km" startDate="2024-03-09 08:00:00 {Z}" endDate="2024-03-09 08:59:00 {Z}" value="1.2"/>
 <Record type="HKQuantityTypeIdentifierActiveEnergyBurned" sourceName="{W}" unit="kcal" startDate="2024-03-09 08:00:00 {Z}" endDate="2024-03-09 08:59:00 {Z}" value="200"/>
 <Record type="HKQuantityTypeIdentifierActiveEnergyBurned" sourceName="{W}" unit="kcal" startDate="2024-03-09 09:00:00 {Z}" endDate="2024-03-09 09:59:00 {Z}" value="100"/>
 <Record type="HKQuantityTypeIdentifierHeartRate" sourceName="{W}" unit="count/min" startDate="2024-03-09 08:00:00 {Z}" endDate="2024-03-09 08:00:00 {Z}" value="60"/>
 <Record type="HKQuantityTypeIdentifierHeartRate" sourceName="{W}" unit="count/min" startDate="2024-03-09 12:00:00 {Z}" endDate="2024-03-09 12:00:00 {Z}" value="80"/>
 <Record type="HKQuantityTypeIdentifierHeartRate" sourceName="{W}" unit="count/min" startDate="2024-03-09 23:00:00 {Z}" endDate="2024-03-09 23:00:00 {Z}" value="100"/>
 <Record type="HKQuantityTypeIdentifierRestingHeartRate" sourceName="{W}" unit="count/min" startDate="2024-03-09 12:00:00 {Z}" endDate="2024-03-09 12:00:00 {Z}" value="55"/>
 <Record type="HKQuantityTypeIdentifierHeartRateVariabilitySDNN" sourceName="{W}" unit="ms" startDate="2024-03-09 03:00:00 {Z}" endDate="2024-03-09 03:01:00 {Z}" value="42.5"/>
 <Record type="HKQuantityTypeIdentifierOxygenSaturation" sourceName="{W}" unit="%" startDate="2024-03-09 02:00:00 {Z}" endDate="2024-03-09 02:00:00 {Z}" value="0.97"/>
 <Record type="HKQuantityTypeIdentifierOxygenSaturation" sourceName="{W}" unit="%" startDate="2024-03-09 04:00:00 {Z}" endDate="2024-03-09 04:00:00 {Z}" value="0.95"/>
 <Record type="HKQuantityTypeIdentifierRespiratoryRate" sourceName="{W}" unit="count/min" startDate="2024-03-09 02:00:00 {Z}" endDate="2024-03-09 02:00:00 {Z}" value="14"/>
 <Record type="HKQuantityTypeIdentifierBodyMass" sourceName="Fictional Scale" unit="kg" startDate="2024-03-09 07:00:00 {Z}" endDate="2024-03-09 07:00:00 {Z}" value="80"/>
 <Record type="HKQuantityTypeIdentifierBodyFatPercentage" sourceName="Fictional Scale" unit="%" startDate="2024-03-09 07:00:00 {Z}" endDate="2024-03-09 07:00:00 {Z}" value="0.22"/>
 <Record type="HKQuantityTypeIdentifierLeanBodyMass" sourceName="Fictional Scale" unit="kg" startDate="2024-03-09 07:00:00 {Z}" endDate="2024-03-09 07:00:00 {Z}" value="62"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="{W}" startDate="2024-03-08 22:40:00 {Z}" endDate="2024-03-09 06:00:00 {Z}" value="HKCategoryValueSleepAnalysisInBed"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="{W}" startDate="2024-03-08 23:00:00 {Z}" endDate="2024-03-08 23:50:00 {Z}" value="HKCategoryValueSleepAnalysisAsleepCore"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="{W}" startDate="2024-03-08 23:50:00 {Z}" endDate="2024-03-09 00:30:00 {Z}" value="HKCategoryValueSleepAnalysisAsleepDeep"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="{W}" startDate="2024-03-09 00:30:00 {Z}" endDate="2024-03-09 01:30:00 {Z}" value="HKCategoryValueSleepAnalysisAsleepCore"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="{W}" startDate="2024-03-09 01:30:00 {Z}" endDate="2024-03-09 02:10:00 {Z}" value="HKCategoryValueSleepAnalysisAsleepREM"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="{W}" startDate="2024-03-09 02:10:00 {Z}" endDate="2024-03-09 02:20:00 {Z}" value="HKCategoryValueSleepAnalysisAwake"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="{W}" startDate="2024-03-09 02:20:00 {Z}" endDate="2024-03-09 05:00:00 {Z}" value="HKCategoryValueSleepAnalysisAsleepCore"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="{W}" startDate="2024-03-09 05:00:00 {Z}" endDate="2024-03-09 06:00:00 {Z}" value="HKCategoryValueSleepAnalysisAsleepREM"/>
 <Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="31.6" durationUnit="min" sourceName="{W}" startDate="2024-03-10 07:00:00 {Z}" endDate="2024-03-10 07:32:00 {Z}"/>
 <Workout workoutActivityType="HKWorkoutActivityTypeTraditionalStrengthTraining" duration="45" durationUnit="min" sourceName="{W}" startDate="2024-03-10 18:00:00 {Z}" endDate="2024-03-10 18:45:00 {Z}"/>
</HealthData>
"""

SLEEP = [("2024-03-08 22:40:00", "2024-03-09 06:00:00", "In Bed"), ("2024-03-08 23:00:00", "2024-03-08 23:50:00", "Core"),
         ("2024-03-08 23:50:00", "2024-03-09 00:30:00", "Deep"), ("2024-03-09 00:30:00", "2024-03-09 01:30:00", "Core"),
         ("2024-03-09 01:30:00", "2024-03-09 02:10:00", "REM"), ("2024-03-09 02:10:00", "2024-03-09 02:20:00", "Awake"),
         ("2024-03-09 02:20:00", "2024-03-09 05:00:00", "Core"), ("2024-03-09 05:00:00", "2024-03-09 06:00:00", "REM")]


def metric(name, units, *rows):
    return {"name": name, "units": units, "data": [dict(r) for r in rows]}


def pt(date, qty, src=W):
    return {"date": f"{date} {Z}", "qty": qty, "source": src}


def hae_payload():
    return {"data": {
        "metrics": [
            metric("step_count", "count", pt("2024-03-09 08:00:00", 400), pt("2024-03-09 09:00:00", 600),
                   pt("2024-03-09 08:00:00", 500, P), pt("2024-03-10 10:00:00", 300)),
            metric("walking_running_distance", "km", pt("2024-03-09 08:00:00", 1.2)),
            metric("active_energy", "kJ", pt("2024-03-09 08:00:00", 836.8), pt("2024-03-09 09:00:00", 418.4)),
            metric("heart_rate", "count/min", *({"date": f"2024-03-09 {h}:00:00 {Z}", "Min": v, "Avg": v, "Max": v, "source": W}
                                                for h, v in (("08", 60), ("12", 80), ("23", 100)))),
            metric("resting_heart_rate", "count/min", pt("2024-03-09 12:00:00", 55)),
            metric("heart_rate_variability", "ms", pt("2024-03-09 03:00:00", 42.5)),
            metric("blood_oxygen_saturation", "%", pt("2024-03-09 02:00:00", 97), pt("2024-03-09 04:00:00", 95)),
            metric("respiratory_rate", "count/min", pt("2024-03-09 02:00:00", 14)),
            metric("weight_body_mass", "kg", pt("2024-03-09 07:00:00", 80, "Fictional Scale")),
            metric("body_fat_percentage", "%", pt("2024-03-09 07:00:00", 22, "Fictional Scale")),
            metric("lean_body_mass", "kg", pt("2024-03-09 07:00:00", 62, "Fictional Scale")),
            metric("sleep_analysis", "hr", *({"startDate": f"{s} {Z}", "endDate": f"{e} {Z}", "value": v, "source": W,
                                              "qty": 0} for s, e, v in SLEEP)),
            metric("dietary_water", "mL", pt("2024-03-09 12:00:00", 500)),   # not on the Health tab: ignored
        ],
        "workouts": [
            {"id": "FICTIONAL-1", "name": "Running", "start": f"2024-03-10 07:00:00 {Z}", "end": f"2024-03-10 07:32:00 {Z}",
             "duration": 1896, "activeEnergyBurned": {"qty": 300, "units": "kcal"}},
            {"id": "FICTIONAL-2", "name": "Traditional Strength Training", "start": f"2024-03-10 6:00:00 PM {Z}",
             "end": f"2024-03-10 6:45:00 PM {Z}", "duration": 2700},   # on a 12-hour clock, as iOS may write it
        ]}}


def pts(summary, key):
    return dict(next((m["pts"] for m in summary["metrics"] if m["key"] == key), []))


def names(summary):
    return {m["key"]: m["name"] for m in summary["metrics"]}


def body(p):
    return json.dumps(p).encode()


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rx-", dir=TMP)
        rx.DATA = self.dir
        self.store = rx.Store()

    def ingest(self, p, digest=None):
        b = body(p)
        return self.store.ingest(json.loads(b), digest or hashlib.sha256(b).hexdigest())


class SameAsExport(Base):
    def test_same_summary_as_the_export(self):
        xml = os.path.join(self.dir, "export.xml")
        with open(xml, "w", encoding="utf-8") as f:
            f.write(XML)
        want = ah.parse({"apple_health_export/export.xml": xml})["data"]["watch"]
        self.assertTrue(self.ingest(hae_payload()).endswith("records"))
        got = self.store.summary()
        self.assertEqual([m["key"] for m in got["metrics"]], [m["key"] for m in want["metrics"]])
        for g, w in zip(got["metrics"], want["metrics"]):
            self.assertEqual((g["name"], g["unit"], g["group"]), (w["name"], w["unit"], w["group"]), g["key"])
            self.assertEqual([d for d, _ in g["pts"]], [d for d, _ in w["pts"]], g["key"])
            for (_, a), (_, b) in zip(g["pts"], w["pts"]):
                self.assertAlmostEqual(a, b, delta=0.011, msg=g["key"])
        self.assertEqual([(x["start"], x["end"], x["name"], x["kind"], x["minutes"]) for x in got["exercise"]],
                         [(x["start"], x["end"], x["name"], x["kind"], x["minutes"]) for x in want["exercise"]])
        # and the figures themselves
        self.assertEqual(pts(got, "steps"), {"2024-03-09": 1000, "2024-03-10": 300})   # the watch's, not watch + phone
        self.assertEqual(pts(got, "active_cal"), {"2024-03-09": 300})                  # from kJ
        self.assertEqual(pts(got, "spo2_avg"), {"2024-03-09": 96})
        self.assertEqual(pts(got, "body_fat"), {"2024-03-09": 22})
        self.assertEqual(pts(got, "weight"), {"2024-03-09": 176.37})                   # pounds, as the receiver's
        self.assertEqual(pts(got, "sleep_h"), {"2024-03-09": 6.83})                    # in bed isn't sleep
        self.assertEqual(names(got)["hrv"], "HRV (SDNN)")                              # Apple's measure, named so

    def test_heart_rate_min_avg_max(self):
        self.ingest({"data": {"metrics": [metric("heart_rate", "bpm",
                     {"date": f"2024-03-09 08:00:00 {Z}", "Min": 52, "Avg": 70, "Max": 120},
                     {"date": f"2024-03-09 09:00:00 {Z}", "Min": 61, "Avg": 90, "Max": 101})]}})
        s = self.store.summary()
        self.assertEqual((pts(s, "hr_avg"), pts(s, "hr_min"), pts(s, "hr_max")),
                         ({"2024-03-09": 80}, {"2024-03-09": 52}, {"2024-03-09": 120}))

    def test_aggregated_sleep_and_pounds(self):
        night = {"date": f"2024-03-09 00:00:00 {Z}", "sleepStart": f"2024-03-08 23:00:00 {Z}", "sleepEnd": f"2024-03-09 06:00:00 {Z}",
                 "inBed": 7.5, "asleep": 0, "core": 4.5, "deep": 1, "rem": 1, "awake": 0.5, "source": W}
        self.ingest({"data": {"metrics": [metric("sleep_analysis", "hr", night),
                                          metric("weight_body_mass", "lb", pt("2024-03-09 07:00:00", 175))]}})
        s = self.store.summary()
        self.assertEqual((pts(s, "sleep_h"), pts(s, "sleep_deep_min"), pts(s, "sleep_light_min"), pts(s, "sleep_awake_min")),
                         ({"2024-03-09": 6.5}, {"2024-03-09": 60}, {"2024-03-09": 270}, {"2024-03-09": 30}))
        self.assertEqual(pts(s, "weight"), {"2024-03-09": 175})
        # the same night again, longer (the morning's upload after a 4 am one): replaced, not added
        self.ingest({"data": {"metrics": [metric("sleep_analysis", "hr", dict(night, sleepEnd=f"2024-03-09 07:00:00 {Z}", core=5.5))]}})
        self.assertEqual(pts(self.store.summary(), "sleep_h"), {"2024-03-09": 7.5})


class Duplicates(Base):
    def test_repeated_upload_is_a_duplicate(self):
        self.assertTrue(self.ingest(hae_payload()).endswith("records"))
        first = self.store.summary()
        self.assertEqual(self.ingest(hae_payload()), "duplicate")
        self.assertEqual(self.store.summary()["metrics"], first["metrics"])

    def test_overlapping_uploads_count_once(self):
        self.ingest(hae_payload())
        first = self.store.summary()
        # "Since last sync" or "Today" sends hours already sent, plus new ones; and workouts again
        again = {"data": {"metrics": [metric("step_count", "count", pt("2024-03-09 09:00:00", 600), pt("2024-03-10 10:00:00", 300),
                                             pt("2024-03-10 11:00:00", 250)),
                                      metric("sleep_analysis", "hr", *({"startDate": f"{s} {Z}", "endDate": f"{e} {Z}", "value": v,
                                                                        "source": W} for s, e, v in SLEEP[3:]))],
                          "workouts": hae_payload()["data"]["workouts"]}}
        self.assertTrue(self.ingest(again).endswith("records"))
        s = self.store.summary()
        self.assertEqual(pts(s, "steps"), {"2024-03-09": 1000, "2024-03-10": 550})
        self.assertEqual(pts(s, "sleep_h"), pts(first, "sleep_h"))
        self.assertEqual(len(s["exercise"]), 2)

    def test_reprocess_from_raw_gives_the_same(self):
        b = body(hae_payload())
        rx.archive(b)
        rx.archive(b)   # the same upload twice in raw/
        self.store.ingest(json.loads(b), hashlib.sha256(b).hexdigest())
        want = self.store.summary()
        fresh = rx.Store()   # nothing saved yet: a new database, folding in all of raw/ (--reprocess does the same)
        self.assertEqual(fresh.catch_up(), 1)
        self.assertEqual(fresh.summary()["metrics"], want["metrics"])


class BesideLifeDashboard(Base):
    def test_one_summary_health_connect_wins_a_shared_day(self):
        self.ingest({"source": "health_connect", "timezone": "UTC", "sequence": 1, "timestamp": "2024-03-09T20:00:00Z",
                     "daily_totals": [{"date": "2024-03-09", "steps": 5000}],
                     "heart_rate_variability": [{"uuid": "v1", "time": "2024-03-09T08:00:00Z", "heart_rate_variability_millis": 30}],
                     "weight": [{"uuid": "w1", "time": "2024-03-08T08:00:00Z", "kilograms": 81}]})
        self.ingest(hae_payload())
        s = self.store.summary()
        self.assertEqual(pts(s, "steps"), {"2024-03-09": 5000, "2024-03-10": 300})
        self.assertEqual(pts(s, "hrv"), {"2024-03-09": 30})
        self.assertEqual(names(s)["hrv"], "HRV (RMSSD)")
        self.assertEqual(pts(s, "hrv_sdnn"), {"2024-03-09": 42.5})   # SDNN beside it, never averaged with RMSSD
        self.assertEqual(names(s)["hrv_sdnn"], "HRV (SDNN)")
        self.assertEqual(set(pts(s, "weight")), {"2024-03-08", "2024-03-09"})
        self.assertEqual(len(s["exercise"]), 2)

    def test_life_dashboard_payloads_still_skip_other_sources(self):
        self.assertEqual(self.ingest({"source": "something_else", "steps": []}), "skipped")
        self.assertFalse(rx.is_hae({"data": {"other": []}}))
        self.assertFalse(rx.is_hae({"data": "x"}))
        self.assertTrue(rx.is_hae({"data": {"workouts": []}}))


class Upload(unittest.TestCase):
    """Over HTTP: the profile's secret as a bearer header picks the profile; a wrong or missing one is refused."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="rx-http-", dir=TMP)
        rx.DATA = cls.dir
        rx.SECRET_FILE = os.path.join(cls.dir, "no-install-secret")
        rx.KEYS_FILE = os.path.join(cls.dir, "keys", "receiver-secrets.json")
        os.makedirs(os.path.dirname(rx.KEYS_FILE))
        crypto.write_file(rx.KEYS_FILE, json.dumps({"legacy": False, "profiles": [
            {"profile": 1, "slot": "", "secret": "fictional-first-profile-secret"},
            {"profile": 7, "slot": "7", "secret": "fictional-second-profile-secret"}]}).encode())
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), rx.H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}/ingest"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def post(self, data, **headers):
        try:
            return urllib.request.urlopen(urllib.request.Request(self.url, data=data, headers=headers), timeout=5).status
        except urllib.error.HTTPError as e:
            e.close()
            return e.code

    def queued(self):
        out = []
        while not rx.work.empty():
            out.append(rx.work.get_nowait())
        return out

    def test_bearer_secret_picks_the_profile(self):
        b = body(hae_payload())
        self.assertEqual(self.post(b, Authorization="Bearer fictional-second-profile-secret"), 200)
        self.assertEqual(self.post(b, Authorization="bearer  fictional-first-profile-secret"), 200)
        q = self.queued()
        self.assertEqual([slot for slot, _, _ in q], ["7", ""])
        self.assertEqual(q[0][1], b)
        store = rx.Store("7")
        self.assertTrue(store.ingest(json.loads(q[0][1]), q[0][2]).endswith("records"))
        self.assertEqual(pts(store.summary(), "steps"), {"2024-03-09": 1000, "2024-03-10": 300})

    def test_wrong_or_missing_secret_refused(self):
        b = body(hae_payload())
        before = [len(rx.raw_files(s)) for s in ("", "7")]
        self.assertEqual(self.post(b), 401)
        self.assertEqual(self.post(b, Authorization="Bearer a-guess"), 401)
        self.assertEqual(self.post(b, Authorization="Bearer "), 401)
        self.assertEqual(self.post(b, Authorization="fictional-second-profile-secret"), 401)   # without "Bearer"
        self.assertEqual(self.post(b, Authorization="Bearer fictional-second-profile-secret-and-more"), 401)
        self.assertEqual(self.queued(), [])
        self.assertEqual([len(rx.raw_files(s)) for s in ("", "7")], before)   # nothing archived either

    def test_life_dashboard_signature_still_works(self):
        b = body({"source": "health_connect", "timezone": "UTC", "daily_totals": [{"date": "2024-03-09", "steps": 10}]})
        sig = "sha256=" + hmac.new(b"fictional-second-profile-secret", b, hashlib.sha256).hexdigest()
        self.assertEqual(self.post(b, **{"X-Signature": sig}), 200)
        self.assertEqual([slot for slot, _, _ in self.queued()], ["7"])
        self.assertEqual(self.post(b, **{"X-Signature": "sha256=" + "0" * 64}), 401)

    # ---- refused before the body is read, and bad signatures slowed (#222)
    def setUp(self):
        rx.throttle = rx.BadSignatures()   # each test from a clean slate

    def head_only(self, **headers):
        """Send only a request's head, its Content-Length saying 32 MB follow, and read the answer's head: a receiver
        that read the body first would never answer (the socket times out and the test fails)."""
        with socket.create_connection(("127.0.0.1", self.srv.server_address[1]), timeout=3) as c:
            c.sendall(("POST /ingest HTTP/1.1\r\nHost: receiver\r\nContent-Type: application/json\r\n"
                       f"Content-Length: {rx.MAX_BODY}\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n").encode())
            f = c.makefile("rb")
            status = int(f.readline().split()[1])
            head = {}
            while (line := f.readline().decode().strip()):
                k, _, v = line.partition(":")
                head[k.strip().lower()] = v.strip()
            return status, head

    def test_unsigned_refused_before_reading_its_body(self):
        self.assertEqual(self.head_only()[0], 401, "neither X-Signature nor a bearer secret")
        self.assertEqual(self.head_only(Authorization="Bearer ")[0], 401)
        self.assertEqual(self.head_only(Authorization="fictional-second-profile-secret")[0], 401)   # without "Bearer"
        self.assertEqual(self.head_only(Authorization="Bearer a-guess")[0], 401, "a wrong secret is known before the body")
        self.assertEqual(self.head_only()[1].get("connection"), "close", "what's left of the body is never read")
        self.assertEqual(self.queued(), [])

    def test_bad_signatures_slow_the_address(self):
        b = body({"source": "health_connect", "timezone": "UTC", "daily_totals": [{"date": "2024-03-09", "steps": 10}]})
        good = {"X-Signature": "sha256=" + hmac.new(b"fictional-second-profile-secret", b, hashlib.sha256).hexdigest()}
        bad = {"X-Signature": "sha256=" + "0" * 64}
        for _ in range(30):   # unsigned ones cost nothing now, so they don't count
            self.assertEqual(self.post(b), 401)
        for _ in range(rx.BAD_FREE):
            self.assertEqual(self.post(b, **bad), 401)
        status, head = self.head_only(**bad)
        self.assertEqual(status, 429, "the address waits: its next signed body isn't read")
        self.assertGreaterEqual(int(head["retry-after"]), 25)
        self.assertEqual(self.post(b, **good), 429, "a signed upload from it waits too, its body unread")
        self.assertEqual(self.post(b, Authorization="Bearer fictional-first-profile-secret"), 200,
                         "a right secret is known before the body, so it isn't held up")
        self.assertEqual(self.post(b, Authorization="Bearer a-guess"), 401)   # counted: a wrong secret is a bad one too
        rx.throttle.fail("ip:127.0.0.1")
        self.assertGreater(rx.throttle.wait("ip:127.0.0.1"), int(head["retry-after"]), "each bad one more waits longer")
        with rx.throttle._lock:   # 15 minutes on: the address may send again
            rx.throttle._fails = {k: [t - 16 * 60 for t in v] for k, v in rx.throttle._fails.items()}
        self.assertEqual(self.post(b, **good), 200)
        self.assertEqual([slot for slot, _, _ in self.queued()], ["", "7"])


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(TMP, True)
