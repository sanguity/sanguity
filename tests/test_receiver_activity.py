"""Respiratory rate and cardio fitness from the phone apps (#226): watch/receiver.py reads VO2 max from Life
Dashboard Companion (Health Connect's Vo2Max) and Health Auto Export (HealthKit's VO2Max), the Apple Health importer
from an uploaded export.xml, and respiratory rate from all three; the same readings give the same days and values
whichever way they came. Heart rate zones on workouts (#197, server/hrzones.py): the zones from a maximum (220
minus the age on the workout's day, or one measured), what each phone app sends during a workout (Health Auto
Export's minute-by-minute readings or only an average and highest; Health Connect's heart rate samples in the
session's time; an export's average and highest), time in each zone, and the API: the measured maximum in About this
person (owners and editors), the zones the Health tab gets. All data here is fictional, written for these tests.

  python tests/test_receiver_activity.py
"""
import hashlib, importlib.util, json, os, shutil, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="sanguity-activity-")
os.environ.update(SANGUITY_DATA=os.path.join(TMP, "app"), SANGUITY_RECORDS=os.path.join(TMP, "records"),
                  SANGUITY_BUILD=os.path.join(TMP, "build"), SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"),
                  SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"), PASSWORD_MIN_LENGTH="15",
                  SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"), SANGUITY_CORRECTIONS=os.path.join(TMP, "config", "corrections.json"))
for d in ("app", "records", "keys", "build", "config"):
    os.makedirs(os.path.join(TMP, d), exist_ok=True)

from server import crypto, hrzones   # noqa: E402
from server.importers import apple_health as ah   # noqa: E402

crypto.make_key_file(os.environ["SANGUITY_KEY_FILE"])
crypto.keyring.load()
spec = importlib.util.spec_from_file_location("watch_receiver", os.path.join(ROOT, "watch", "receiver.py"))
rx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rx)

W = "Fictional Watch"
OFF, TZ = "-0500", "America/New_York"   # March 2024, before the clocks change: -0500 all along
# (local time, breaths a minute) measured during sleep; one late in the evening, the next day in UTC
RESP = [("2024-03-08 02:10:00", 14.5), ("2024-03-08 04:40:00", 15.5), ("2024-03-09 03:00:00", 13.0),
        ("2024-03-09 23:30:00", 16.0)]
# (local time, mL/kg/min): the watch's estimates after outdoor walks and runs; one late in the evening
VO2 = [("2024-03-08 18:20:00", 41.2), ("2024-03-09 07:45:00", 41.9), ("2024-03-09 19:05:00", 42.3)]
WANT_RESP = {"2024-03-08": 15.0, "2024-03-09": 14.5}
WANT_VO2 = {"2024-03-08": 41.2, "2024-03-09": 42.1}


def utc(local):
    from datetime import datetime, timedelta, timezone
    return (datetime.fromisoformat(local) + timedelta(hours=5)).replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def export_xml():
    rec = lambda typ, unit, t, v: (f'<Record type="HKQuantityTypeIdentifier{typ}" sourceName="{W}" unit="{unit}" '
                                   f'startDate="{t} {OFF}" endDate="{t} {OFF}" value="{v}"/>')
    rows = [rec("RespiratoryRate", "count/min", t, v) for t, v in RESP] + [rec("VO2Max", "mL/min·kg", t, v) for t, v in VO2]
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<HealthData locale="en_US">\n'
            f' <ExportDate value="2024-03-12 10:00:00 {OFF}"/>\n ' + "\n ".join(rows) + "\n</HealthData>\n")


def hae_payload(vo2_units="ml/(kg·min)"):
    return {"data": {"metrics": [
        {"name": "respiratory_rate", "units": "count/min", "data": [{"date": f"{t} {OFF}", "qty": v, "source": W} for t, v in RESP]},
        {"name": "vo2_max", "units": vo2_units, "data": [{"date": f"{t} {OFF}", "qty": v, "source": W} for t, v in VO2]}]}}


def ldc_payload(seq=1):
    return {"source": "health_connect", "timezone": TZ, "sequence": seq, "timestamp": "2024-03-12T15:00:00Z",
            "respiratory_rate": [{"uuid": f"resp-{t}", "time": utc(t), "rate": v, "source": W} for t, v in RESP],
            "vo2_max": [{"uuid": f"vo2-{t}", "time": utc(t), "vo2_milliliters_per_minute_kilogram": v, "source": W,
                         "measurement_method": 1} for t, v in VO2]}


def pts(summary, key):
    return dict(next((m["pts"] for m in summary["metrics"] if m["key"] == key), []))


def meta(summary, key):
    return next(((m["name"], m["unit"], m["group"]) for m in summary["metrics"] if m["key"] == key), None)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rx-", dir=TMP)
        rx.DATA = self.dir
        self.store = rx.Store()

    def ingest(self, p, store=None):
        b = json.dumps(p).encode()
        return (store or self.store).ingest(json.loads(b), hashlib.sha256(b).hexdigest())

    def export(self):
        path = os.path.join(self.dir, "export.xml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(export_xml())
        return ah.parse({"apple_health_export/export.xml": path})["data"]["watch"]


class RespiratoryRateAndVo2Max(Base):
    def test_the_same_days_from_both_apps_and_an_export(self):
        self.assertTrue(self.ingest(hae_payload()).endswith("records"))
        iphone = self.store.summary()
        android = rx.Store("2")
        self.assertTrue(self.ingest(ldc_payload(), android).endswith("records"))
        android = android.summary()
        export = self.export()
        for got in (iphone, android, export):
            self.assertEqual(pts(got, "resp"), WANT_RESP)
            self.assertEqual(pts(got, "vo2max"), WANT_VO2)
            self.assertEqual(meta(got, "vo2max"), ("Cardio fitness (VO2 max)", "mL/kg/min", "Heart"))
            self.assertEqual(meta(got, "resp"), ("Respiratory rate", "breaths/min", "Oxygen & breathing"))
        keys = lambda s: [m["key"] for m in s["metrics"]]
        self.assertEqual(keys(iphone), keys(export))   # in the same order, as the Health tab lists them

    def test_sent_again_counts_once(self):
        self.ingest(ldc_payload(1))
        self.ingest(ldc_payload(2))   # a later upload with the same readings (same uuids)
        self.ingest(hae_payload())
        self.ingest({"data": {"metrics": hae_payload()["data"]["metrics"][1:]}})   # Health Auto Export's "Today" again
        s = self.store.summary()
        self.assertEqual(pts(s, "vo2max"), WANT_VO2)   # Health Connect's days, Health Auto Export's the same ones
        self.assertEqual(pts(s, "resp"), WANT_RESP)

    def test_other_spellings_of_the_unit(self):
        for units in ("mL/min·kg", "ml/kg/min", "mL/(kg·min)"):
            with self.subTest(units=units):
                store = rx.Store(str(len(units)))
                self.ingest(hae_payload(units), store)
                self.assertEqual(pts(store.summary(), "vo2max"), WANT_VO2)
        store = rx.Store("99")
        self.ingest(hae_payload("L/min"), store)   # an absolute rate, not per kilogram: not the same measure
        self.assertEqual(pts(store.summary(), "vo2max"), {})

    def test_a_deleted_estimate_goes(self):
        self.ingest(ldc_payload())
        self.ingest({"source": "health_connect", "timezone": TZ, "sequence": 2,
                     "deleted_records": [{"type": "vo2_max", "uuid": "vo2-2024-03-08 18:20:00"}]})
        self.assertEqual(pts(self.store.summary(), "vo2max"), {"2024-03-09": 42.1})

    def test_reprocess_from_raw_gives_the_same(self):
        for p in (hae_payload(), ldc_payload()):
            b = json.dumps(p).encode()
            rx.archive(b)
            self.store.ingest(json.loads(b), hashlib.sha256(b).hexdigest())
        fresh = rx.Store()
        self.assertEqual(fresh.catch_up(), 2)
        self.assertEqual(fresh.summary()["metrics"], self.store.summary()["metrics"])


# ---- heart rate zones on workouts (#197)
def minutes(start, *runs):
    """Health Auto Export's heartRateData: a row a minute from start ("2024-03-09 07:00:00"), runs of (minutes, bpm)."""
    from datetime import datetime, timedelta
    t, out = datetime.fromisoformat(start), []
    for n, bpm in runs:
        for _ in range(n):
            out.append({"date": f"{t:%Y-%m-%d %H:%M:%S} {OFF}", "Min": bpm - 4, "Avg": bpm, "Max": bpm + 3, "units": "count/min", "source": W})
            t += timedelta(minutes=1)
    return out


def run_workout(**extra):
    return {"id": "FICTIONAL-RUN", "name": "Outdoor Run", "start": f"2024-03-09 07:00:00 {OFF}", "end": f"2024-03-09 07:30:00 {OFF}",
            "duration": 1800, **extra}


def workouts(summary):
    return [x for x in summary["exercise"]]


class Zones(unittest.TestCase):
    def test_the_zones_of_a_maximum(self):
        self.assertEqual(hrzones.bounds(185), [93, 111, 130, 148, 167])   # 92.5 rounds up, as on the page
        self.assertEqual(hrzones.bounds(190), [95, 114, 133, 152, 171])
        hist = [[92, 10], [93, 20], [110, 30], [111, 40], [166, 50], [167, 60], [200, 70]]
        self.assertEqual(hrzones.in_zones(hist, 185), [10, 50, 40, 0, 50, 130])   # below zone 1, then zones 1 to 5

    def test_the_maximum_follows_the_age_on_the_day_or_a_measured_one(self):
        self.assertEqual(hrzones.max_for("1990-09-10", 0, "2026-09-09"), (185, "age", 35))   # 220 - 35
        self.assertEqual(hrzones.max_for("1990-09-10", 0, "2026-09-10"), (184, "age", 36))   # the birthday
        self.assertEqual(hrzones.max_for("1990-09-10", 190, "2026-09-09"), (190, "measured", 35))
        self.assertEqual(hrzones.max_for("", 190, "2026-09-09"), (190, "measured", None))
        self.assertEqual(hrzones.max_for("", 0, "2026-09-09"), (None, None, None))
        self.assertEqual(hrzones.max_for("2027-01-01", 0, "2026-09-09"), (None, None, None))   # before the birth date

    def test_time_at_each_bpm(self):
        hist, avg, mx = hrzones.histogram([(0, 100), (60, 100.4), (120, 140), (999, 150)], 0, 180)
        self.assertEqual((hist, round(avg, 2), mx), ({100: 120, 140: 60}, 113.47, 140))   # the one after the end left out
        hist, _, _ = hrzones.histogram([(0, 100), (600, 120)], 0, 660)
        self.assertEqual(hist, {100: hrzones.GAP, 120: 60}, "a gap in the readings isn't time at the last one")
        self.assertEqual(hrzones.histogram([], 0, 60), ({}, None, None))

    def test_a_measured_maximum_is_checked(self):
        for v, want in ((0, 0), (None, 0), ("", 0), (185, 185), (185.0, 185)):
            self.assertEqual(hrzones.check_max(v), want)
        for bad in (99, 231, 185.5, True, "185", -1):
            with self.assertRaises(hrzones.ZoneError, msg=bad):
                hrzones.check_max(bad)

    def test_annotate(self):
        s = {"tz": "America/New_York", "exercise": [
            {"start": "2026-09-10T03:00:00+00:00", "name": "Run", "hr": {"avg": 140, "max": 170, "hist": [[120, 600], [150, 600]]}},
            {"start": "2026-09-11T12:00:00+00:00", "name": "Ride", "hr": {"avg": 130, "max": 160}},
            {"start": "2026-09-12T12:00:00+00:00", "name": "Walk"}]}
        got = hrzones.annotate(json.loads(json.dumps(s)), "1990-09-10", 0)
        run, ride, walk = got["exercise"]
        # the run started the evening before the birthday, in the phone's time zone: 35, so 185
        self.assertEqual(run["hr"], {"avg": 140, "max": 170, "zmax": 185, "basis": "age", "age": 35, "zones": [0, 0, 600, 0, 600, 0]})
        self.assertEqual(ride["hr"], {"avg": 130, "max": 160, "zmax": 184, "basis": "age", "age": 36}, "no readings: no time in zones")
        self.assertNotIn("hr", walk)
        self.assertEqual(got["hrZones"], {"basis": "age", "maxHr": None, "workouts": True})
        got = hrzones.annotate(json.loads(json.dumps(s)), "", 0)
        self.assertEqual(got["exercise"][0]["hr"], {"avg": 140, "max": 170}, "no birth date or maximum: no zones, and no readings sent")
        self.assertEqual(got["hrZones"]["basis"], None)
        got = hrzones.annotate(json.loads(json.dumps(s)), "", 190)
        self.assertEqual((got["exercise"][0]["hr"]["zmax"], got["exercise"][0]["hr"]["zones"], got["hrZones"]["basis"]),
                         (190, [0, 0, 600, 600, 0, 0], "measured"))


class WorkoutHeartRate(Base):
    def test_health_auto_export_readings(self):
        rows = minutes("2024-03-09 07:00:00", (5, 100), (10, 120), (10, 140), (5, 160))
        self.ingest({"data": {"workouts": [run_workout(heartRateData=rows, heartRate={"min": {"qty": 92, "units": "bpm"},
                                                                                     "avg": {"qty": 131.5, "units": "bpm"}, "max": {"qty": 166, "units": "bpm"}})]}})
        hr = workouts(self.store.summary())[0]["hr"]
        self.assertEqual((hr["avg"], hr["max"]), (131.5, 166), "the app's own average and maximum")
        self.assertEqual(hr["hist"], [[100, 300], [120, 600], [140, 600], [160, 300]], "a minute at each reading")
        self.assertEqual(hrzones.in_zones(hr["hist"], 185), [0, 300, 600, 600, 300, 0])

    def test_only_an_average_and_maximum(self):
        self.ingest({"data": {"workouts": [run_workout(avgHeartRate={"qty": 128, "units": "bpm"}, maxHeartRate={"qty": 158, "units": "bpm"}),
                                           dict(run_workout(), start=f"2024-03-10 07:00:00 {OFF}", end=f"2024-03-10 07:30:00 {OFF}")]}})
        with_hr, without = workouts(self.store.summary())
        self.assertEqual(with_hr["hr"], {"avg": 128, "max": 158}, "an older version's fields: no readings, so no time in zones")
        self.assertNotIn("hr", without)

    def test_health_connect_samples_during_the_session(self):
        start = 1710000000   # 2024-03-09 16:00 UTC
        iso = lambda t: __import__("datetime").datetime.fromtimestamp(t, __import__("datetime").timezone.utc).isoformat()
        hr = [{"uuid": f"hr{i}", "time": iso(start + 5 * i), "bpm": 105 if i < 180 else 125, "source": W} for i in range(540)]
        hr.append({"uuid": "before", "time": iso(start - 600), "bpm": 60, "source": W})   # before the walk: not its
        self.ingest({"source": "health_connect", "timezone": TZ, "sequence": 1, "heart_rate": hr,
                     "exercise": [{"uuid": "walk-1", "start_time": iso(start), "end_time": iso(start + 2700), "type": 79,
                                   "name": "walking", "title": "Walking", "duration_seconds": 2700, "source": W}]})
        x = workouts(self.store.summary())[0]
        self.assertEqual(x["hr"]["hist"], [[105, 900], [125, 1800]])
        self.assertEqual((x["hr"]["max"], round(x["hr"]["avg"], 1)), (125, 118.3))
        # heart rate only in hourly buckets: nothing to put in zones
        store = rx.Store("5")
        self.ingest({"source": "health_connect", "timezone": TZ, "sequence": 1,
                     "heart_rate": [{"bucket_start": iso(start), "bucket_end": iso(start + 3600), "avg": 110, "min": 90, "max": 140, "sample_count": 400}],
                     "exercise": [{"uuid": "walk-1", "start_time": iso(start), "end_time": iso(start + 2700), "name": "walking"}]}, store)
        self.assertNotIn("hr", workouts(store.summary())[0])

    def test_an_exports_workout_statistics(self):
        xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<HealthData locale="en_US">\n <ExportDate value="2024-03-12 10:00:00 {OFF}"/>\n'
               f' <Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="30" durationUnit="min" sourceName="{W}" '
               f'startDate="2024-03-09 07:00:00 {OFF}" endDate="2024-03-09 07:30:00 {OFF}">\n'
               f'  <WorkoutStatistics type="HKQuantityTypeIdentifierHeartRate" startDate="2024-03-09 07:00:00 {OFF}" endDate="2024-03-09 07:30:00 {OFF}" '
               f'average="131.46" minimum="92" maximum="166" unit="count/min"/>\n'
               f'  <WorkoutStatistics type="HKQuantityTypeIdentifierActiveEnergyBurned" sum="300" unit="kcal"/>\n </Workout>\n'
               f' <Workout workoutActivityType="HKWorkoutActivityTypeWalking" duration="20" durationUnit="min" sourceName="{W}" '
               f'startDate="2024-03-10 07:00:00 {OFF}" endDate="2024-03-10 07:20:00 {OFF}"/>\n</HealthData>\n')
        path = os.path.join(self.dir, "export.xml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(xml)
        run, walk = ah.parse({"apple_health_export/export.xml": path})["data"]["watch"]["exercise"]
        self.assertEqual(run["hr"], {"avg": 131.5, "max": 166.0})
        self.assertNotIn("hr", walk)

    def test_a_database_from_before_reads_its_workouts_again(self):
        b = json.dumps({"data": {"workouts": [run_workout(heartRateData=minutes("2024-03-09 07:00:00", (30, 130)))]}}).encode()
        rx.archive(b)
        self.store.ingest(json.loads(b), hashlib.sha256(b).hexdigest())
        self.store.db.execute("ALTER TABLE exercise DROP COLUMN hr")   # as a store from before #197
        self.store.db.execute("DELETE FROM meta WHERE k='workout_hr'")
        self.store.save()
        again = rx.Store()
        again.catch_up()
        self.assertEqual(workouts(again.summary())[0]["hr"]["hist"], [[130, 1800]])


class Api(unittest.TestCase):
    """The measured maximum in About this person, and the zones the Health tab gets (GET .../watch)."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from server import accounts, db as dbm, main, profiles as prof, watch_store
        import logging
        logging.getLogger("sanguity.security").setLevel(logging.ERROR)
        dbm.init()
        cls.ctx = TestClient(main.app, base_url="http://testserver").__enter__()
        db = dbm.connect()
        pw = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites"}
        uid = {n: accounts.create(db, n, password=p)[0] for n, p in pw.items()}
        cls.pid = prof.create_profile(db, "Robin (fictional)", owner_id=uid["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (uid["kim"], cls.pid))
        with dbm.transaction(db):
            watch_store.save_import(db, cls.pid, {"tz": "America/New_York", "metrics": [], "exercise": [
                {"start": "2026-09-08T11:00:00+00:00", "end": "2026-09-08T11:30:00+00:00", "name": "Running", "kind": "running", "minutes": 30,
                 "hr": {"avg": 131.5, "max": 166, "hist": [[100, 300], [120, 600], [140, 600], [160, 300]]}},
                {"start": "2026-11-10T23:00:00+00:00", "end": "2026-11-10T23:30:00+00:00", "name": "Running", "kind": "running", "minutes": 30,
                 "hr": {"avg": 160, "max": 175, "hist": [[150, 600], [165, 900], [175, 300]]}}]})
        db.close()
        cls.c = {}
        for n, p in pw.items():
            c = TestClient(main.app, base_url="http://testserver")
            assert c.post("/api/login", json={"username": n, "password": p}, headers={"X-Sanguity": "1"}).status_code == 200
            cls.c[n] = c

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

    def watch(self):
        return self.c["kim"].get(f"/api/profiles/{self.pid}/watch").json()

    def put(self, who, **body):
        return self.c[who].put(f"/api/profiles/{self.pid}/person", json=body, headers={"X-Sanguity": "1"})

    def test_zones_follow_the_birth_date_then_a_measured_maximum(self):
        w = self.watch()
        self.assertEqual((w["hrZones"]["basis"], w["exercise"][0]["hr"]), (None, {"avg": 131.5, "max": 166}), "no birth date: no zones")
        self.assertEqual(self.put("robin", birthDate="1990-09-10").status_code, 200)
        first, second = (x["hr"] for x in self.watch()["exercise"])
        self.assertEqual((first["zmax"], first["age"], first["zones"]), (185, 35, [0, 300, 600, 600, 300, 0]))
        self.assertEqual((second["zmax"], second["age"], second["zones"]), (184, 36, [0, 0, 0, 0, 1500, 300]), "a birthday between them")
        self.assertNotIn("hist", second, "the readings stay on the server")
        r = self.put("robin", maxHr=190)
        self.assertEqual((r.status_code, r.json()["maxHr"]), (200, 190))
        w = self.watch()
        self.assertEqual((w["hrZones"], w["exercise"][1]["hr"]["zones"]), ({"basis": "measured", "maxHr": 190, "workouts": True}, [0, 0, 0, 600, 900, 300]))
        r = self.put("robin", maxHr=300)
        self.assertEqual(r.status_code, 400)
        self.assertIn("from 100 to 230", r.json()["detail"])
        self.assertEqual(self.put("kim", maxHr=180).status_code, 403, "a viewer can't change it")
        self.assertEqual(self.put("robin", maxHr=0).json()["maxHr"], 0, "0 clears it")
        self.assertEqual(self.watch()["hrZones"]["basis"], "age")


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(TMP, True)
