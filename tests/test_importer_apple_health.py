"""Apple Health importer (server/importers/apple_health.py): a fictional export.xml becomes the daily watch summary
the Health tab reads, in the shape watch/receiver.py writes for Health Connect.

  python3 -m pytest tests/test_importer_apple_health.py
"""
import importlib.util, os, shutil, sys, tempfile, tracemalloc, unittest
from datetime import timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server.importers import apple_health as ah   # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "apple_health")
EXPORT = os.path.join(FIXTURE, "apple_health_export", "export.xml")
FILES = {"apple_health_export/export.xml": EXPORT}

HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE HealthData [
<!ELEMENT HealthData (ExportDate,Me,(Record|Correlation|Workout|ActivitySummary)*)>
<!ATTLIST HealthData locale CDATA #REQUIRED>
]>
<HealthData locale="en_US">
 <ExportDate value="2024-06-01 10:00:00 -0500"/>
"""


def pts(watch, key):
    return dict(next((m["pts"] for m in watch["metrics"] if m["key"] == key), []))


class Tmp(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sanguity-ah-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, text, name="export.xml", mode="w"):
        p = os.path.join(self.dir, name)
        with open(p, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as f:
            f.write(text)
        return {f"apple_health_export/{name}": p}


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = ah.parse(FILES)
        cls.w = cls.out["data"]["watch"]

    def test_contract(self):
        self.assertEqual(ah.NAME, "apple-health")
        self.assertTrue(ah.LABEL)
        self.assertEqual(set(self.out), {"data", "warnings"})
        self.assertEqual(set(self.out["data"]), {"events", "watch"})
        self.assertEqual(self.out["data"]["events"], [])
        self.assertEqual(self.out["warnings"], [])
        for m in self.w["metrics"]:
            self.assertEqual(set(m), {"key", "name", "unit", "group", "pts"})
        for x in self.w["exercise"]:
            self.assertEqual(set(x), {"key", "start", "end", "name", "kind", "minutes"})
        self.assertEqual(self.w["last_payload"], "2024-03-13T09:00:00+01:00")   # the export's date
        self.assertEqual(self.w["tz"], "America/Los_Angeles")   # the latest HKTimeZone in the export

    def test_steps_across_midnight_and_time_zones(self):
        self.assertEqual(pts(self.w, "steps"), {
            "2024-03-09": 1200,   # watch 1000 + half of 23:50–00:10; the phone's overlapping 900 isn't added
            "2024-03-10": 700,    # the other half + 500, after the change to daylight saving time
            "2024-03-12": 300,    # 23:30 in California (13 March in UTC)
            "2024-03-13": 50})    # 00:20 in Europe (12 March in UTC)

    def test_activity_units(self):
        self.assertEqual(pts(self.w, "distance_km"), {"2024-03-09": 1.61})   # 1 mi from the watch beats the phone's 1.2 km
        self.assertEqual(pts(self.w, "active_cal"), {"2024-03-09": 350})     # 250 kcal + 418.4 kJ

    def test_heart(self):
        d = "2024-03-09"   # the cuff's heart rate inside a blood pressure correlation isn't a top-level record
        self.assertEqual((pts(self.w, "hr_avg")[d], pts(self.w, "hr_min")[d], pts(self.w, "hr_max")[d]), (80, 60, 100))
        self.assertEqual(pts(self.w, "hr_rest"), {d: 55})
        self.assertEqual(pts(self.w, "hrv"), {d: 42.5})
        self.assertEqual(pts(self.w, "spo2_avg"), {d: 96})
        self.assertEqual(pts(self.w, "spo2_min"), {d: 95})
        self.assertEqual(pts(self.w, "resp"), {d: 14})

    def test_sleep_stages(self):
        d = "2024-03-09"   # the watch's staged night (410 min asleep) and the afternoon nap (30 min)
        self.assertEqual(pts(self.w, "sleep_h"), {d: 7.33})
        self.assertEqual(pts(self.w, "sleep_deep_min"), {d: 40})
        self.assertEqual(pts(self.w, "sleep_rem_min"), {d: 100})
        self.assertEqual(pts(self.w, "sleep_light_min"), {d: 270})
        self.assertEqual(pts(self.w, "sleep_awake_min"), {d: 10})

    def test_workouts(self):
        self.assertEqual([{k: v for k, v in x.items() if k != "key"} for x in self.w["exercise"]], [
            {"start": "2024-03-10T11:00:00+00:00", "end": "2024-03-10T11:32:00+00:00",
             "name": "Running", "kind": "running", "minutes": 32},
            {"start": "2024-03-13T01:00:00+00:00", "end": "2024-03-13T01:45:00+00:00",
             "name": "Traditional strength training", "kind": "traditional_strength_training", "minutes": 45}])

    def test_workout_keys_are_stable(self):
        keys = [x["key"] for x in self.w["exercise"]]
        self.assertEqual(keys, [x["key"] for x in ah.parse(FILES)["data"]["watch"]["exercise"]])   # parsed again
        self.assertEqual(len(set(keys)), len(keys))
        for k in keys:
            self.assertRegex(k, r"^apple-health:workout:[0-9a-f]{16}$")
        # from start, end and activity type alone: the same workout written in another offset keeps its key
        from datetime import datetime
        s, e = (datetime.fromisoformat(t) for t in ("2024-03-10T07:00:00-04:00", "2024-03-10T07:32:00-04:00"))
        self.assertEqual(ah.workout_key(s.astimezone(timezone.utc), e, "HKWorkoutActivityTypeRunning"), keys[0])
        self.assertNotEqual(ah.workout_key(s, e, "HKWorkoutActivityTypeWalking"), keys[0])

    def test_weight_units(self):
        self.assertEqual(pts(self.w, "weight"), {"2024-03-09": 176.37, "2024-03-10": 175})   # 80 kg, then 175 lb
        self.assertEqual(pts(self.w, "lean_mass"), {"2024-03-09": 136.69})                    # 62 kg
        self.assertEqual(pts(self.w, "body_fat"), {"2024-03-09": 22})                         # stored as 0.22


class MatchesReceiver(unittest.TestCase):
    """The same kinds of data, sent as a Health Connect payload to watch/receiver.py, give the same summary."""

    def test_same_summary(self):
        try:
            spec = importlib.util.spec_from_file_location("watch_receiver", os.path.join(ROOT, "watch", "receiver.py"))
            rx = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(rx)
        except ImportError as e:   # the receiver needs the app's crypto dependency
            self.skipTest(f"receiver not importable: {e}")
        tmp = tempfile.mkdtemp(prefix="sanguity-rx-")
        self.addCleanup(shutil.rmtree, tmp, True)
        # its days in New York's standard offset (the zone database may be missing, as on Windows without tzdata);
        # every sample here sits on the same day either side of the change to daylight saving time
        rx.zone = lambda name: timezone(timedelta(hours=-5))
        store = rx.Store(os.path.join(tmp, "watch.db"))
        z = "-05:00"
        stages = [("light", 50), ("deep", 40), ("light", 60), ("rem", 40), ("awake", 10), ("light", 160), ("rem", 60)]
        store.ingest({"source": "health_connect", "timezone": "America/New_York", "sequence": 1, "timestamp": "x",
                      "heart_rate": [{"uuid": f"h{v}", "time": f"2024-03-09T{h}:00:00{z}", "bpm": v}
                                     for h, v in (("08", 60), ("12", 80), ("23", 100))],
                      "resting_heart_rate": [{"uuid": "r", "time": f"2024-03-09T12:00:00{z}", "bpm": 55}],
                      "heart_rate_variability": [{"uuid": "v", "time": f"2024-03-09T03:00:00{z}", "heart_rate_variability_millis": 42.5}],
                      "oxygen_saturation": [{"uuid": f"o{v}", "time": f"2024-03-09T0{h}:00:00{z}", "percentage": v}
                                            for h, v in ((2, 97), (4, 95))],
                      "respiratory_rate": [{"uuid": "rr", "time": f"2024-03-09T02:00:00{z}", "rate": 14}],
                      "weight": [{"uuid": "w1", "time": f"2024-03-09T07:00:00{z}", "kilograms": 80},
                                 {"uuid": "w2", "time": "2024-03-10T07:00:00-04:00", "kilograms": 175 / rx.LB}],
                      "body_fat": [{"uuid": "f", "time": f"2024-03-09T07:00:00{z}", "percentage": 22}],
                      "lean_body_mass": [{"uuid": "l", "time": f"2024-03-09T07:00:00{z}", "kilograms": 62}],
                      "daily_totals": [{"date": "2024-03-09", "steps": 1200, "distance_meters": 1609.344, "active_calories": 350},
                                       {"date": "2024-03-10", "steps": 700}, {"date": "2024-03-12", "steps": 300},
                                       {"date": "2024-03-13", "steps": 50}],
                      "sleep": [{"uuid": "s1", "session_end_time": f"2024-03-09T06:00:00{z}", "duration_seconds": 25800,
                                 "stages": [{"stage": s, "duration_seconds": m * 60} for s, m in stages]},
                                {"uuid": "s2", "session_end_time": f"2024-03-09T14:30:00{z}", "duration_seconds": 1800,
                                 "stages": [{"stage": "sleeping", "duration_seconds": 1800}]}],
                      "exercise": [{"uuid": "e1", "start_time": "2024-03-10T07:00:00-04:00", "end_time": "2024-03-10T07:32:00-04:00",
                                    "name": "running", "duration_seconds": 1896},
                                   {"uuid": "e2", "start_time": "2024-03-12T18:00:00-07:00", "end_time": "2024-03-12T18:45:00-07:00",
                                    "name": "traditional_strength_training", "duration_seconds": 2700}]}, "d1")
        want = store.summary()
        got = ah.parse(FILES)["data"]["watch"]
        self.assertEqual([m["key"] for m in got["metrics"]], [m["key"] for m in want["metrics"]])
        for g, w in zip(got["metrics"], want["metrics"]):
            self.assertEqual((g["unit"], g["group"]), (w["unit"], w["group"]), g["key"])
            if g["key"] != "hrv":   # Apple's is SDNN, Health Connect's RMSSD: same key, its own name
                self.assertEqual(g["name"], w["name"])
            self.assertEqual([d for d, _ in g["pts"]], [d for d, _ in w["pts"]], g["key"])
            for (_, a), (_, b) in zip(g["pts"], w["pts"]):
                self.assertAlmostEqual(a, b, delta=0.011, msg=g["key"])
        self.assertEqual([(x["start"], x["end"], x["kind"], x["minutes"]) for x in got["exercise"]],
                         [(x["start"], x["end"], x["kind"], x["minutes"]) for x in want["exercise"]])


class Detect(Tmp):
    def test_fixture(self):
        self.assertEqual(ah.detect(FILES), 1.0)

    def test_bare_export_and_the_zip_folder(self):
        self.assertEqual(ah.detect({"export.xml": EXPORT}), 1.0)
        files = dict(FILES, **{"apple_health_export/export_cda.xml": EXPORT,
                               "apple_health_export/workout-routes/route_1.gpx": EXPORT})
        self.assertEqual(ah.detect(files), 1.0)

    def test_not_apple_health(self):
        self.assertEqual(ah.detect({}), 0.0)
        self.assertEqual(ah.detect(self.write('<?xml version="1.0"?><ClinicalDocument xmlns="urn:hl7-org:v3"/>')), 0.0)
        self.assertEqual(ah.detect(self.write('{"resourceType": "Bundle"}', "data.json")), 0.0)
        self.assertEqual(ah.detect(self.write("date,value\n", "export.csv")), 0.0)

    def test_reads_only_the_start(self):
        big = HEAD + " " * (2 * ah.HEAD_BYTES) + "</HealthData>"
        files = self.write(big)
        self.assertGreater(ah.detect(files), 0.5)   # the root is near the top however long the file

    def test_missing_file_is_zero(self):
        self.assertEqual(ah.detect({"export.xml": os.path.join(self.dir, "gone.xml")}), 0.0)


class Refused(Tmp):
    def test_entities(self):
        bomb = ('<?xml version="1.0"?>\n<!DOCTYPE HealthData [\n <!ENTITY a "aaaaaaaaaa">\n'
                ' <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">\n <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">\n]>\n'
                '<HealthData locale="en_US"><Record type="HKQuantityTypeIdentifierStepCount" sourceName="&c;" unit="count"'
                ' startDate="2024-01-01 10:00:00 +0000" endDate="2024-01-01 10:00:00 +0000" value="1"/></HealthData>')
        with self.assertRaisesRegex(ValueError, "entities"):
            ah.parse(self.write(bomb))

    def test_external_entity(self):
        xxe = ('<?xml version="1.0"?><!DOCTYPE HealthData [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
               '<HealthData locale="&x;"/>')
        with self.assertRaisesRegex(ValueError, "entities"):
            ah.parse(self.write(xxe))

    def test_entity_split_across_reads(self):
        # the declaration straddles the parser's read boundary
        pad = "<!-- " + "x" * (64 * 1024 - 60) + " -->"
        doc = '<?xml version="1.0"?>' + pad + '<!DOCTYPE HealthData [<!ENTITY a "b">]><HealthData locale="x"/>'
        for shift in range(0, 12):
            with self.subTest(shift=shift), self.assertRaisesRegex(ValueError, "entities"):
                ah.parse(self.write(doc[:21] + " " * shift + doc[21:]))

    def test_other_encodings(self):
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            ah.parse(self.write(HEAD.encode("utf-16") + b"</HealthData>", mode="wb"))
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            ah.parse(self.write(HEAD.replace("UTF-8", "UTF-7") + "</HealthData>"))

    def test_plain_language_errors(self):
        with self.assertRaisesRegex(ValueError, "No export.xml"):
            ah.parse({"notes.txt": EXPORT})
        with self.assertRaisesRegex(ValueError, "well-formed"):
            ah.parse(self.write(HEAD + ' <Record type="HKQuantityTypeIdentifierStepCount"'))   # cut short
        with self.assertRaisesRegex(ValueError, "isn't an Apple Health export"):
            ah.parse(self.write('<?xml version="1.0"?><Other/>'))


class Edges(Tmp):
    def rec(self, typ, unit, value, start, end=None, src="Test Watch"):
        return (f' <Record type="{typ}" sourceName="{src}" unit="{unit}" startDate="{start}" '
                f'endDate="{end or start}" value="{value}"/>\n')

    def test_unknown_units_and_empty_export(self):
        files = self.write(HEAD + self.rec("HKQuantityTypeIdentifierBodyMass", "slug", 5, "2024-01-01 07:00:00 +0000")
                           + "</HealthData>")
        out = ah.parse(files)
        self.assertEqual(out["data"]["watch"]["metrics"], [])
        self.assertTrue(any("'slug'" in w for w in out["warnings"]), out["warnings"])
        self.assertTrue(any("none of the data" in w for w in out["warnings"]), out["warnings"])

    def test_steps_over_several_days(self):
        files = self.write(HEAD + self.rec("HKQuantityTypeIdentifierStepCount", "count", 4800,
                                           "2024-01-01 12:00:00 +0000", "2024-01-03 12:00:00 +0000") + "</HealthData>")
        self.assertEqual(pts(ah.parse(files)["data"]["watch"], "steps"),
                         {"2024-01-01": 1200, "2024-01-02": 2400, "2024-01-03": 1200})


class FlatMemory(Tmp):
    """A large export is streamed: peak memory doesn't grow with the number of records."""

    def make(self, n, name):
        p = os.path.join(self.dir, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(HEAD)
            for i in range(n):
                day, minute = 1 + i % 28, i % 1440
                t = f"2024-02-{day:02d} {minute // 60:02d}:{minute % 60:02d}:00 -0500"
                if i % 2:
                    f.write(f' <Record type="HKQuantityTypeIdentifierHeartRate" sourceName="Test Watch" unit="count/min"'
                            f' creationDate="{t}" startDate="{t}" endDate="{t}" value="{60 + i % 40}">\n'
                            f'  <MetadataEntry key="HKMetadataKeyHeartRateMotionContext" value="0"/>\n </Record>\n')
                else:
                    f.write(f' <Record type="HKQuantityTypeIdentifierStepCount" sourceName="Test Watch" unit="count"'
                            f' creationDate="{t}" startDate="{t}" endDate="{t}" value="10"/>\n')
            f.write("</HealthData>\n")
        return {f"{name}/export.xml": p}

    def peak(self, files):
        tracemalloc.start()
        try:
            out = ah.parse(files)
            return tracemalloc.get_traced_memory()[1], out
        finally:
            tracemalloc.stop()

    def test_200k_records(self):
        small, large = self.make(20_000, "small.xml"), self.make(200_000, "large.xml")
        self.assertGreater(os.path.getsize(next(iter(large.values()))), 40_000_000)
        p_small, _ = self.peak(small)
        p_large, out = self.peak(large)
        self.assertEqual(sum(v for _, v in pts(out["data"]["watch"], "steps").items()), 100_000 * 10)
        self.assertLess(p_large, 8 * 1024 * 1024, f"peak {p_large / 1e6:.1f} MB")
        self.assertLess(p_large, p_small * 1.5 + 512 * 1024, f"small {p_small / 1e6:.1f} MB, large {p_large / 1e6:.1f} MB")


if __name__ == "__main__":
    unittest.main()
