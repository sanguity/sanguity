"""Blood glucose from the phone apps (#181): watch/receiver.py reads meter and continuous glucose monitor (CGM)
readings from Life Dashboard Companion (Health Connect) and Health Auto Export (Apple Health), and server/glucose.py
summarises a CGM's days. All data here is fictional, written for these tests.

  python tests/test_glucose.py
"""
import hashlib, importlib.util, json, math, os, shutil, sqlite3, sys, tempfile, time, unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="sanguity-glucose-")
os.environ["SANGUITY_KEY_FILE"] = os.path.join(TMP, "keys", "data_key")

from server import crypto, glucose, units   # noqa: E402

crypto.make_key_file(os.environ["SANGUITY_KEY_FILE"])
crypto.keyring.load()
spec = importlib.util.spec_from_file_location("watch_receiver_glucose", os.path.join(ROOT, "watch", "receiver.py"))
rx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rx)

TZ = "America/New_York"   # -0400 in June
OFF = timezone(timedelta(hours=-4))
CGM_APP, CGM_SRC, METER_SRC = "com.example.fictionalcgm", "Fictional CGM", "Fictional Meter"


# ---- the same fictional readings, as each app sends them
def readings():
    """[(local time, mg/dL, "cgm" | "meter")]: a CGM worn 2024-06-09 and 06-10 (every five minutes; on the 10th a gap
    from 02:00 to 08:00, so 18 h of readings), and finger-stick meter readings on the 8th and the 9th."""
    out = []
    for day in (9, 10):
        t = datetime(2024, 6, day, 0, 0, tzinfo=OFF)
        for i in range(288):
            at = t + timedelta(minutes=5 * i)
            if day == 10 and 2 <= at.hour < 8:
                continue
            out.append((at, round(125 + 75 * math.sin(i / 23) + (day - 9) * 10), "cgm"))
    for (day, h, m), v in (((8, 7, 10), 98), ((8, 18, 40), 142), ((9, 7, 5), 105)):
        out.append((datetime(2024, 6, day, h, m, tzinfo=OFF), v, "meter"))
    return out


def hc_payload(rows=None, **extra):
    """Life Dashboard Companion's upload: the CGM's readings in mmol/L, the meter's in mg/dL, each with its specimen."""
    rows = readings() if rows is None else rows
    out = []
    for at, v, kind in rows:
        r = {"uuid": f"fictional-{kind}-{int(at.timestamp())}", "time": at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
             "source": CGM_APP if kind == "cgm" else "com.example.fictionalmeter",
             "specimen_source": 1 if kind == "cgm" else 2}
        if kind == "cgm":
            r["millimoles_per_liter"] = v / glucose.MGDL_PER_MMOL
        else:
            r["milligrams_per_deciliter"] = v
        out.append(r)
    return {"source": "health_connect", "timezone": TZ, "sequence": 1, "timestamp": "2024-06-11T09:00:00Z", "blood_glucose": out, **extra}


def hae_payload(rows=None, units_="mg/dL"):
    """Health Auto Export's upload: one blood_glucose metric, each reading with its source's name and no specimen."""
    rows = readings() if rows is None else rows
    f = 1 if units_ == "mg/dL" else 1 / glucose.MGDL_PER_MMOL
    return {"data": {"metrics": [{"name": "blood_glucose", "units": units_, "data": [
        {"date": at.strftime("%Y-%m-%d %H:%M:%S %z"), "qty": v * f, "source": CGM_SRC if kind == "cgm" else METER_SRC}
        for at, v, kind in rows]}]}}


def gpts(summary, key):
    return dict(next((m["pts"] for m in summary["metrics"] if m["key"] == key), []))


def gkeys(summary):
    return [m["key"] for m in summary["metrics"] if m["key"].startswith("glucose_")]


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rx-", dir=TMP)
        rx.DATA = self.dir
        self.store = rx.Store()

    def ingest(self, p, store=None):
        b = json.dumps(p).encode()
        return (store or self.store).ingest(json.loads(b), hashlib.sha256(b).hexdigest())


class BothApps(Base):
    def test_same_days_from_both_apps(self):
        self.ingest(hc_payload())
        other = rx.Store("2")
        self.ingest(hae_payload(), other)
        a, b = self.store.summary(), other.summary()
        self.assertEqual(gkeys(a), list(glucose.META))
        self.assertEqual(gkeys(a), gkeys(b))
        for k in gkeys(a):
            pa, pb = gpts(a, k), gpts(b, k)
            self.assertEqual(sorted(pa), sorted(pb), k)
            for d in pa:
                self.assertAlmostEqual(pa[d], pb[d], delta=0.051, msg=f"{k} {d}")
        # meter readings as they are, by local time; the CGM by day
        self.assertEqual(gpts(a, "glucose_meter"), {"2024-06-08T07:10": 98, "2024-06-08T18:40": 142, "2024-06-09T07:05": 105})
        self.assertEqual(gpts(a, "glucose_n"), {"2024-06-09": 288, "2024-06-10": 216})
        self.assertEqual(gpts(a, "glucose_hours"), {"2024-06-09": 24, "2024-06-10": 18})   # the sensor gap shows
        cgm9 = [v for at, v, k in readings() if k == "cgm" and at.day == 9]
        self.assertAlmostEqual(gpts(a, "glucose_avg")["2024-06-09"], sum(cgm9) / len(cgm9), delta=0.051)
        self.assertAlmostEqual(gpts(a, "glucose_min")["2024-06-09"], min(cgm9), delta=0.01)
        self.assertAlmostEqual(gpts(a, "glucose_max")["2024-06-09"], max(cgm9), delta=0.01)
        for d in ("2024-06-09", "2024-06-10"):
            self.assertAlmostEqual(gpts(a, "glucose_lt70")[d] + gpts(a, "glucose_in")[d] + gpts(a, "glucose_gt180")[d], 100, delta=0.11)
            self.assertLessEqual(gpts(a, "glucose_lt54")[d], gpts(a, "glucose_lt70")[d])
        # none of the five-minute readings reach the page
        self.assertLessEqual(max(len(m["pts"]) for m in a["metrics"]), 3)

    def test_mmol_from_health_auto_export_too(self):
        self.ingest(hae_payload(units_="mmol<180.1558800000541>/L"))
        other = rx.Store("2")
        self.ingest(hae_payload(), other)
        for k in glucose.META:
            for d, v in gpts(other.summary(), k).items():
                self.assertAlmostEqual(gpts(self.store.summary(), k)[d], v, delta=0.051, msg=k)

    def test_health_connect_wins_a_shared_day(self):
        rows = readings()
        self.ingest(hae_payload([r for r in rows if r[0].day in (8, 9)]))
        self.ingest(hc_payload([(at, v + 20, k) for at, v, k in rows if at.day == 9]))   # the 9th from both, differently
        s = self.store.summary()
        self.assertEqual(gpts(s, "glucose_meter"), {"2024-06-08T07:10": 98, "2024-06-08T18:40": 142, "2024-06-09T07:05": 125})
        cgm9 = [v + 20 for at, v, k in rows if k == "cgm" and at.day == 9]
        self.assertAlmostEqual(gpts(s, "glucose_avg")["2024-06-09"], sum(cgm9) / len(cgm9), delta=0.051)

    def test_other_watch_data_unchanged(self):
        self.ingest(hc_payload(daily_totals=[{"date": "2024-06-09", "steps": 5000}]))
        s = self.store.summary()
        self.assertEqual(gpts(s, "steps"), {"2024-06-09": 5000})
        self.assertEqual([m["key"] for m in s["metrics"]][0], "steps")   # glucose comes after the others
        self.assertEqual(rx.Store("3").summary()["metrics"], [])         # and nothing at all without readings


class Ranges(unittest.TestCase):
    """Time in ranges: each reading as whole mg/dL against 54, 70 and 180, in mg/dL and in mmol/L."""

    def day(self, values, unit="mg/dL"):
        con = sqlite3.connect(":memory:")
        con.executescript(glucose.TABLE)
        t = datetime(2024, 6, 9, 8, 0, tzinfo=OFF)
        for i, v in enumerate(values):
            at = t + timedelta(minutes=5 * i)
            con.execute("INSERT INTO glucose VALUES('hae', 'Fictional CGM', ?, ?, ?, ?, NULL)",
                        (str(int(at.timestamp())), int(at.timestamp()), at.strftime("%Y-%m-%dT%H:%M"), glucose.to_mgdl(v, unit)))
        s = {m["key"]: dict(m["pts"]) for m in glucose.summary(con)}
        return {k[8:]: s[k]["2024-06-09"] for k in s if k != "glucose_meter"}

    def test_boundaries_in_mg_dl(self):
        d = self.day([53, 53.6, 54, 69, 69.4, 69.5, 70, 180, 180.4, 180.5, 181, 250])
        self.assertEqual(d["n"], 12)
        self.assertEqual((d["lt54"], d["lt70"], d["in"], d["gt180"]), (8.3, 41.7, 33.3, 25.0))   # 1, 5, 4 and 3 of 12
        self.assertEqual((d["min"], d["max"]), (53, 250))
        self.assertEqual(d["hours"], 1)   # 08:00 to 08:55: four quarter hours

    def test_boundaries_in_mmol_l(self):
        # 2.9 is 52.2 (below 54); 3.0 is 54.05 (not below 54); 3.8 is 68.5 (below 70); 3.9 is 70.3 and 10.0 is 180.2
        # (both in 70–180, as 3.9–10.0 mmol/L says); 10.1 is 182.0 (above)
        d = self.day([2.9, 3.0, 3.8, 3.9, 10.0, 10.1] * 2, "mmol/L")
        self.assertEqual((d["lt54"], d["lt70"], d["in"], d["gt180"]), (16.7, 50.0, 33.3, 16.7))

    def test_counts_by_whole_mg_dl_for_a_range_of_ones_own(self):   # #234
        from server import targets
        d = self.day([53, 53.6, 54, 69, 69.4, 69.5, 70, 180, 180.4, 180.5, 181, 250])
        self.assertEqual(d["dist"][:4], [53, 1, 2, 0], "from the lowest whole mg/dL: 53 once, 54 twice (53.6 rounds up)")
        self.assertEqual((len(d["dist"]) - 1, sum(d["dist"][1:])), (250 - 53 + 1, 12), "a count for each mg/dL to the highest, every reading")
        self.assertEqual(targets.own_shares(d["dist"], 54, 69), (8.3, 33.4, 58.3), "counted as the standard ranges are: 69.5 is 70, above 69")
        self.assertEqual(targets.own_shares(d["dist"], 70, 180), (41.7, 33.3, 25.0), "the standard 70–180 the same way")
        d = self.day([3.9, 7.8, 7.9, 10.0] * 3, "mmol/L")
        self.assertEqual(targets.own_shares(d["dist"], *targets.cgm_bounds(targets.typed("cgm", {"low": 3.9, "high": 7.8, "unit": "mmol/L"}))),
                         (0.0, 50.0, 50.0), "a range typed in mmol/L, compared in whole mg/dL: 7.9 is above 7.8")

    def test_a_gap_in_the_readings(self):
        con = sqlite3.connect(":memory:")
        con.executescript(glucose.TABLE)
        t = datetime(2024, 6, 9, 0, 0, tzinfo=OFF)
        for i in range(0, 24 * 60, 15):   # a sensor that reads every 15 minutes, off from 09:00 to 15:30
            at = t + timedelta(minutes=i)
            if not (9 * 60 <= i < 15 * 60 + 30):
                con.execute("INSERT INTO glucose VALUES('hc', 'x', ?, ?, ?, 110, 'cgm')", (str(i), int(at.timestamp()), at.strftime("%Y-%m-%dT%H:%M")))
        s = {m["key"]: dict(m["pts"]) for m in glucose.summary(con)}
        self.assertEqual(s["glucose_hours"], {"2024-06-09": 17.5})
        self.assertEqual(s["glucose_in"], {"2024-06-09": 100})


class Units(unittest.TestCase):
    def test_exact_conversion(self):
        self.assertEqual(glucose.MGDL_PER_MMOL, 18.0156)   # glucose's molar mass, 180.156 g/mol (#41)
        self.assertAlmostEqual(glucose.to_mgdl("5.5", "mmol/L"), 99.0858)
        self.assertAlmostEqual(glucose.to_mgdl(5.5, "mmol<180.1558800000541>/L"), 99.0858)   # HealthKit's spelling
        self.assertEqual(glucose.to_mgdl(99, "mg/dL"), 99)
        self.assertEqual(glucose.to_mgdl(99, "mg/dl"), 99)
        self.assertIsNone(glucose.to_mgdl(99, "g/L"))      # not a unit it converts
        self.assertIsNone(glucose.to_mgdl("high", "mg/dL"))
        self.assertIsNone(glucose.to_mgdl(0, "mg/dL"))     # not a reading
        self.assertIsNone(glucose.to_mgdl(120, "mmol/L"))  # 2,162 mg/dL: a reading in the wrong unit

    def test_shown_in_mmol_l_by_the_lab_choice(self):
        s = {"metrics": [{"key": "glucose_avg", "unit": "mg/dL", "pts": [["2024-06-09", 99.0858]]},
                         {"key": "glucose_meter", "unit": "mg/dL", "pts": [["2024-06-09T07:05", 105]]},
                         {"key": "glucose_in", "unit": "%", "pts": [["2024-06-09", 80]]},
                         {"key": "glucose_n", "unit": "readings", "pts": [["2024-06-09", 288]]}]}
        units.annotate_watch(s)
        conv = {m["key"]: m.get("conv") for m in s["metrics"]}
        self.assertEqual(conv["glucose_avg"]["si"][0], "mmol/L")
        self.assertAlmostEqual(99.0858 * conv["glucose_avg"]["si"][1], 5.5)
        self.assertEqual(conv["glucose_avg"], conv["glucose_meter"])
        self.assertNotIn("conventional", conv["glucose_avg"])   # already in mg/dL
        self.assertIsNone(conv["glucose_in"])
        self.assertIsNone(conv["glucose_n"])


class MeterOrCgm(Base):
    def test_specimen_says(self):
        self.assertEqual([glucose.specimen(x) for x in (1, 2, 3, 6, 0, None, "interstitial_fluid", "CAPILLARY_BLOOD", "whole blood", "unknown", "4")],
                         ["cgm", "meter", "meter", "meter", None, None, "cgm", "meter", "meter", None, "meter"])
        # a finger-stick taken while the sensor runs, said to be one, stays a meter reading even among the sensor's
        rows = [r for r in readings() if r[2] == "cgm" and r[0].day == 9][:30]
        p = hc_payload(rows)
        p["blood_glucose"].append({"uuid": "fictional-fingerstick", "time": "2024-06-09T11:02:00Z", "milligrams_per_deciliter": 117,
                                   "source": CGM_APP, "specimen_source": "capillary_blood"})
        self.ingest(p)
        s = self.store.summary()
        self.assertEqual(gpts(s, "glucose_meter"), {"2024-06-09T07:02": 117})
        self.assertEqual(gpts(s, "glucose_n"), {"2024-06-09": 30})

    def test_otherwise_by_how_often_it_reads(self):
        cgm = [r for r in readings() if r[2] == "cgm"]
        # the sensor's last 9 readings on a day of their own are still a CGM day: the source reads every 5 minutes
        late = [(datetime(2024, 6, 11, 0, 5 * i, tzinfo=OFF), 120, "cgm") for i in range(9)]
        meter = [(datetime(2024, 6, d, h, 0, tzinfo=OFF), 100 + h, "meter") for d in range(1, 8) for h in (7, 12, 18)]   # 21 readings
        self.ingest(hae_payload(cgm + late + meter))
        s = self.store.summary()
        self.assertEqual(gpts(s, "glucose_n")["2024-06-11"], 9)
        self.assertEqual(len(gpts(s, "glucose_meter")), 21)
        self.assertNotIn("2024-06-01", gpts(s, "glucose_n"))

    def test_the_fullest_source_of_a_day(self):
        cgm = [r for r in readings() if r[2] == "cgm" and r[0].day == 9]
        p = hae_payload(cgm)
        p["data"]["metrics"][0]["data"] += [{"date": at.strftime("%Y-%m-%d %H:%M:%S %z"), "qty": v + 40, "source": "Another Fictional App"}
                                             for at, v, _ in cgm[:100]]   # the same sensor passed on by a second app, in part
        self.ingest(p)
        s = self.store.summary()
        self.assertEqual(gpts(s, "glucose_n"), {"2024-06-09": 288})
        self.assertAlmostEqual(gpts(s, "glucose_avg")["2024-06-09"], sum(v for _, v, _ in cgm) / 288, delta=0.051)


class Uploads(Base):
    def test_repeated_and_overlapping_uploads_count_once(self):
        self.ingest(hae_payload())
        first = self.store.summary()["metrics"]
        rows = readings()
        self.ingest(hae_payload(rows[100:300]))   # "Since last sync" or "Today" sends some again
        self.ingest(hc_payload([r for r in rows if r[0].day == 8]))
        self.ingest(hc_payload([r for r in rows if r[0].day == 8]))   # the same Health Connect readings again
        s = self.store.summary()
        self.assertEqual([m for m in s["metrics"] if m["key"] != "glucose_meter"], [m for m in first if m["key"] != "glucose_meter"])
        self.assertEqual(len(gpts(s, "glucose_meter")), 3)

    def test_deleted_in_health_connect(self):
        self.ingest(hc_payload([r for r in readings() if r[2] == "meter"]))
        gone = [r for r in readings() if r[2] == "meter"][0]
        self.ingest({"source": "health_connect", "timezone": TZ, "sequence": 2,
                     "deleted_records": [{"type": "blood_glucose", "uuid": f"fictional-meter-{int(gone[0].timestamp())}"}]})
        self.assertEqual(sorted(gpts(self.store.summary(), "glucose_meter")), ["2024-06-08T18:40", "2024-06-09T07:05"])

    def test_what_cant_be_read_is_left_out(self):
        p = hc_payload([r for r in readings() if r[2] == "meter"])
        p["blood_glucose"] += [{"bucket_start": "2024-06-09T10:00:00Z", "bucket_end": "2024-06-09T11:00:00Z", "avg": 6.1, "sample_count": 12},
                               {"uuid": "fictional-no-value", "time": "2024-06-09T12:00:00Z"}]
        self.ingest(p)
        hae = {"data": {"metrics": [{"name": "blood_glucose", "units": "mg/dL", "data": [
            {"date": "2024-06-12 08:00:00 -0400", "Avg": 120, "Min": 90, "Max": 160, "source": CGM_SRC}]},   # grouped into hours
            {"name": "blood_glucose", "units": "g/L", "data": [{"date": "2024-06-12 09:00:00 -0400", "qty": 1.1, "source": METER_SRC}]}]}}
        self.ingest(hae)
        self.assertEqual(len(gpts(self.store.summary(), "glucose_meter")), 3)

    def test_reprocess_from_raw_gives_the_same(self):
        for p in (hc_payload([r for r in readings() if r[0].day != 10]), hae_payload([r for r in readings() if r[0].day == 10])):
            b = json.dumps(p).encode()
            rx.archive(b)
            self.store.ingest(json.loads(b), hashlib.sha256(b).hexdigest())
        want = self.store.summary()["metrics"]
        fresh = rx.Store()   # nothing saved: a new database from all of raw/, as --reprocess
        self.assertEqual(fresh.catch_up(), 2)
        self.assertEqual(fresh.summary()["metrics"], want)

    def test_an_existing_database_reads_earlier_uploads_once(self):
        """A receiver from before #181 took uploads with glucose in them and kept none of it: on its first start
        after the update it reads that from raw/, once."""
        b = json.dumps(hc_payload()).encode()
        rx.archive(b)
        self.store.ingest(json.loads(b), hashlib.sha256(b).hexdigest())
        self.store.db.execute("DELETE FROM glucose")
        self.store.db.execute("DELETE FROM meta WHERE k='glucose'")   # as it was before the update
        self.store.save()
        old = rx.Store()
        self.assertTrue(old.loaded)
        self.assertEqual(gkeys(old.summary()), [])
        self.assertEqual(old.catch_up(), 1)   # read again for its glucose, though its upload was already folded in
        self.assertEqual(gpts(old.summary(), "glucose_n"), {"2024-06-09": 288, "2024-06-10": 216})
        old.save()
        again = rx.Store()
        self.assertEqual(again.catch_up(), 0)   # once only
        self.assertEqual(gpts(again.summary(), "glucose_n"), {"2024-06-09": 288, "2024-06-10": 216})

    def test_encrypted_at_rest(self):
        self.ingest(hc_payload())
        self.store.save()
        self.store.write_summary()
        for path in (self.store.path, rx.summary_file("")):
            self.assertTrue(crypto.file_is_encrypted(path), path)
        with open(self.store.path, "rb") as f:
            self.assertNotIn(CGM_APP.encode(), f.read())
        with open(rx.summary_file(""), "rb") as f:
            self.assertNotIn(b"glucose", f.read())


class Volume(Base):
    def test_a_year_of_cgm_readings(self):
        """288 readings a day for a year, in a day's upload each (Health Connect's ids are the longest): what the
        receiver's database holds, sealed (docs/database.md, "The watch receiver's database"), and the summary."""
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        for d in range(365):
            day = start + timedelta(days=d)
            rows = [(day + timedelta(minutes=5 * i), 100 + (i * 7 + d) % 120, "cgm") for i in range(288)]
            p = hc_payload(rows, timezone="UTC")
            for r in p["blood_glucose"]:
                r["uuid"] = hashlib.md5(r["uuid"].encode()).hexdigest()[:8] + "-1d3a-4c2b-9f0e-" + r["uuid"][-12:].zfill(12)   # uuid-sized
            self.store.ingest(p, f"digest-{d}")
        self.store.db.execute("VACUUM")
        self.store.save()
        size = os.path.getsize(self.store.path)
        t = time.perf_counter()
        s = self.store.summary()
        took = time.perf_counter() - t
        print(f"\n  a year of CGM readings: watch.db {size / 1e6:.1f} MB sealed; summary {took:.2f} s, "
              f"daily.json {len(json.dumps(s, separators=(',', ':'))) / 1e3:.0f} KB", file=sys.stderr)
        self.assertLess(size, 12_000_000)   # about 10 MB (docs/database.md)
        self.assertLess(took, 3)
        self.assertEqual(len(gpts(s, "glucose_n")), 365)
        self.assertEqual(set(gpts(s, "glucose_n").values()), {288})
        self.assertEqual(set(gpts(s, "glucose_hours").values()), {24})


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(TMP, True)
