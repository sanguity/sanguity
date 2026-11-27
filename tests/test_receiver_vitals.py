"""Home readings from the phone apps (#180, #184): watch/receiver.py keeps each blood pressure reading that Life
Dashboard Companion (Health Connect) or Health Auto Export (Apple Health) sends, with its pulse when one comes with it,
and each body temperature (in °C, whatever unit it came in), and a daily summary of them; the same readings from
either app give the same readings and days (blood oxygen's daily figures too), and a reading sent again counts once.
All data here is fictional, written for these tests.

  python tests/test_receiver_vitals.py
"""
import hashlib, importlib.util, json, os, shutil, sqlite3, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="sanguity-vitals-")
os.environ["SANGUITY_KEY_FILE"] = os.path.join(TMP, "keys", "data_key")

from server import crypto   # noqa: E402

crypto.make_key_file(os.environ["SANGUITY_KEY_FILE"])
crypto.keyring.load()
spec = importlib.util.spec_from_file_location("watch_receiver", os.path.join(ROOT, "watch", "receiver.py"))
rx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rx)

CUFF = "Fictional Cuff"
TZ, OFF = "America/Chicago", "-0600"   # the phone's zone; Health Auto Export writes each reading's own offset
# (local time, systolic, diastolic, pulse or None): three days, one reading late in the evening (the next day in UTC)
BP = [("2024-03-09 07:05:00", 128, 82, 71), ("2024-03-09 21:40:00", 121, 78, None), ("2024-03-09 22:30:00", 135, 88, 76),
      ("2024-03-10 07:00:00", 124, 80, 68), ("2024-03-11 06:55:00", 119, 77, 66)]


def utc(local):
    from datetime import datetime, timedelta, timezone
    t = datetime.fromisoformat(local) + timedelta(hours=6)   # -0600 to UTC
    return t.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def ldc(rows=BP, seq=1, **extra):
    """Life Dashboard's upload of these readings."""
    return {"source": "health_connect", "timezone": TZ, "sequence": seq, "timestamp": "2024-03-11T13:00:00Z",
            "blood_pressure": [{"uuid": f"bp-{t}", "time": utc(t), "systolic": s, "diastolic": d, "source": CUFF,
                                **({"pulse": p} if p is not None else {})} for t, s, d, p in rows], **extra}


def hae(rows=BP):
    """Health Auto Export's upload of the same readings."""
    return {"data": {"metrics": [{"name": "blood_pressure", "units": "mmHg", "data": [
        {"date": f"{t} {OFF}", "systolic": s, "diastolic": d, "source": CUFF, **({"pulse": p} if p is not None else {})}
        for t, s, d, p in rows]}]}}


def pts(summary, key):
    return dict(next((m["pts"] for m in summary["metrics"] if m["key"] == key), []))


def readings(summary):
    """The readings without the app that sent them."""
    return [r[:5] for r in summary.get("bp_readings") or []]


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rx-", dir=TMP)
        rx.DATA = self.dir
        self.store = rx.Store()

    def ingest(self, p, store=None):
        b = json.dumps(p).encode()
        return (store or self.store).ingest(json.loads(b), hashlib.sha256(b).hexdigest())


class BloodPressure(Base):
    def test_life_dashboard_readings_and_days(self):
        self.assertTrue(self.ingest(ldc()).endswith("records"))
        s = self.store.summary()
        self.assertEqual(readings(s), [["2024-03-09T13:05:00+00:00", 128, 82, 71, CUFF], ["2024-03-10T03:40:00+00:00", 121, 78, None, CUFF],
                                       ["2024-03-10T04:30:00+00:00", 135, 88, 76, CUFF], ["2024-03-10T13:00:00+00:00", 124, 80, 68, CUFF],
                                       ["2024-03-11T12:55:00+00:00", 119, 77, 66, CUFF]])
        self.assertEqual({r[5] for r in s["bp_readings"]}, {"Health Connect"})
        # the day is the phone's: the evening readings are the 9th, though the 10th in UTC
        self.assertEqual(pts(s, "bp_n"), {"2024-03-09": 3, "2024-03-10": 1, "2024-03-11": 1})
        self.assertEqual(pts(s, "bp_sys"), {"2024-03-09": 128, "2024-03-10": 124, "2024-03-11": 119})
        self.assertEqual(pts(s, "bp_dia"), {"2024-03-09": 82.67, "2024-03-10": 80, "2024-03-11": 77})
        self.assertEqual(pts(s, "bp_sys_max"), {"2024-03-09": 135, "2024-03-10": 124, "2024-03-11": 119})
        self.assertEqual(pts(s, "bp_dia_max"), {"2024-03-09": 88, "2024-03-10": 80, "2024-03-11": 77})
        names = {m["key"]: (m["name"], m["unit"], m["group"]) for m in s["metrics"]}
        self.assertEqual(names["bp_sys"], ("Blood pressure, systolic, daily average", "mmHg", "Blood pressure"))
        self.assertEqual(names["bp_n"], ("Blood pressure readings", "readings", "Blood pressure"))

    def test_both_apps_give_the_same_readings_and_days(self):
        self.ingest(ldc())
        other = rx.Store("9")
        self.ingest(hae(), other)
        a, b = self.store.summary(), other.summary()
        self.assertEqual(readings(a), readings(b))
        self.assertEqual({r[5] for r in b["bp_readings"]}, {"Apple Health"})
        for k in rx.VITALS_META:
            self.assertEqual(pts(a, k), pts(b, k), k)

    def test_sent_again_counts_once(self):
        self.ingest(ldc())
        want = self.store.summary()
        self.assertEqual(self.ingest(ldc(seq=2)).split()[1], "records")   # the same readings in a later upload
        self.ingest(ldc(BP[3:], seq=3))   # and some of them again
        self.ingest(hae())                 # and by the other app too (a phone moved over, both sending)
        self.ingest({"data": {"metrics": hae(BP[:2])["data"]["metrics"]}})   # overlapping uploads of Health Auto Export
        s = self.store.summary()
        self.assertEqual(readings(s), readings(want))
        self.assertEqual({r[5] for r in s["bp_readings"]}, {"Health Connect"})   # the reading Health Connect sent first
        for k in rx.VITALS_META:
            self.assertEqual(pts(s, k), pts(want, k), k)
        # a reading only one app sent is added
        self.ingest(hae([("2024-03-12 07:10:00", 131, 84, None)]))
        s = self.store.summary()
        self.assertEqual(len(s["bp_readings"]), len(BP) + 1)
        self.assertEqual(s["bp_readings"][-1][5], "Apple Health")
        self.assertEqual(pts(s, "bp_n")["2024-03-12"], 1)

    def test_a_deleted_reading_goes(self):
        self.ingest(ldc())
        self.ingest({"source": "health_connect", "timezone": TZ, "sequence": 2,
                     "deleted_records": [{"type": "blood_pressure", "uuid": f"bp-{BP[0][0]}"}]})
        s = self.store.summary()
        self.assertEqual(len(s["bp_readings"]), len(BP) - 1)
        self.assertEqual(pts(s, "bp_n")["2024-03-09"], 2)

    def test_readings_that_arent_numbers_are_left_out(self):
        self.ingest({"data": {"metrics": [{"name": "blood_pressure", "units": "mmHg", "data": [
            {"date": f"2024-03-09 07:05:00 {OFF}", "systolic": "high", "diastolic": 80},
            {"date": f"2024-03-09 07:06:00 {OFF}", "systolic": 120},
            {"date": "not a date", "systolic": 120, "diastolic": 80},
            {"date": f"2024-03-09 07:07:00 {OFF}", "systolic": 120, "diastolic": 80, "pulse": {"qty": 64, "units": "bpm"}}]}]}})
        self.assertEqual(readings(self.store.summary()), [["2024-03-09T13:07:00+00:00", 120, 80, 64, None]])

    def test_no_readings_no_summary(self):
        self.ingest({"source": "health_connect", "timezone": TZ, "daily_totals": [{"date": "2024-03-09", "steps": 10}]})
        s = self.store.summary()
        self.assertNotIn("bp_readings", s)
        self.assertFalse([m for m in s["metrics"] if m["key"].startswith("bp_")])

    def test_reprocess_from_raw_gives_the_same(self):
        for p in (ldc(), ldc(seq=2), hae()):
            b = json.dumps(p).encode()
            rx.archive(b)
            self.store.ingest(json.loads(b), hashlib.sha256(b).hexdigest())
        want = self.store.summary()
        fresh = rx.Store()   # nothing saved yet: a new database, folding in all of raw/ (--reprocess does the same)
        self.assertEqual(fresh.catch_up(), 3)
        got = fresh.summary()
        self.assertEqual((got["metrics"], got["bp_readings"]), (want["metrics"], want["bp_readings"]))


THERMO = "Fictional Thermometer"
# (local time, °F as an iPhone in the US sends it, the same in °C as Health Connect sends it)
TEMP = [("2024-03-09 07:30:00", 98.6, 37.0), ("2024-03-09 22:15:00", 100.4, 38.0), ("2024-03-10 08:00:00", 99.5, 37.5)]
# blood oxygen: (local time, %), the last one late in the evening
SPO2 = [("2024-03-09 02:00:00", 97), ("2024-03-09 04:00:00", 94), ("2024-03-09 23:30:00", 96), ("2024-03-10 03:00:00", 98)]


def ldc_temp_spo2(seq=1):
    return {"source": "health_connect", "timezone": TZ, "sequence": seq, "timestamp": "2024-03-11T13:00:00Z",
            "body_temperature": [{"uuid": f"t-{t}", "time": utc(t), "celsius": c, "source": THERMO} for t, _, c in TEMP],
            "oxygen_saturation": [{"uuid": f"o-{t}", "time": utc(t), "percentage": v, "source": "Fictional Watch"} for t, v in SPO2]}


def hae_temp_spo2(unit="degF"):
    return {"data": {"metrics": [
        {"name": "body_temperature", "units": unit, "data": [{"date": f"{t} {OFF}", "qty": f if unit == "degF" else c, "source": THERMO}
                                                             for t, f, c in TEMP]},
        {"name": "blood_oxygen_saturation", "units": "%", "data": [{"date": f"{t} {OFF}", "qty": v, "source": "Fictional Watch"} for t, v in SPO2]}]}}


class TemperatureAndOxygen(Base):
    """Body temperature (#184): kept reading by reading in °C and summed up by day, from both apps alike; blood
    oxygen's daily average and low the same from both apps."""

    def test_both_apps_give_the_same_readings_and_days(self):
        self.ingest(ldc_temp_spo2())
        other, third = rx.Store("9"), rx.Store("10")
        self.ingest(hae_temp_spo2(), other)
        self.ingest(hae_temp_spo2("degC"), third)
        a, b, c = self.store.summary(), other.summary(), third.summary()
        self.assertEqual([r[:3] for r in a["temp_readings"]], [["2024-03-09T13:30:00+00:00", 37, THERMO], ["2024-03-10T04:15:00+00:00", 38, THERMO],
                                                               ["2024-03-10T14:00:00+00:00", 37.5, THERMO]])
        for s in (b, c):
            self.assertEqual([r[:3] for r in s["temp_readings"]], [r[:3] for r in a["temp_readings"]])
            for k in ("body_temp", "body_temp_max", "spo2_avg", "spo2_min"):
                self.assertEqual(pts(s, k), pts(a, k), k)
        self.assertEqual([r[3] for r in a["temp_readings"]], ["Health Connect"] * 3)
        self.assertEqual([r[3] for r in b["temp_readings"]], ["Apple Health"] * 3)
        self.assertEqual(pts(a, "body_temp"), {"2024-03-09": 37.5, "2024-03-10": 37.5})   # the evening is the 9th, the phone's day
        self.assertEqual(pts(a, "body_temp_max"), {"2024-03-09": 38, "2024-03-10": 37.5})
        self.assertEqual(pts(a, "spo2_avg"), {"2024-03-09": 95.67, "2024-03-10": 98})
        self.assertEqual(pts(a, "spo2_min"), {"2024-03-09": 94, "2024-03-10": 98})
        m = {x["key"]: (x["name"], x["unit"]) for x in a["metrics"]}
        self.assertEqual(m["body_temp"], ("Body temperature, daily average", "°C"))

    def test_sent_again_counts_once(self):
        self.ingest(ldc_temp_spo2())
        want = self.store.summary()
        self.ingest(ldc_temp_spo2(seq=2))
        self.ingest(hae_temp_spo2())   # by the other app as well: 98.6 °F is the same reading as 37 °C
        s = self.store.summary()
        self.assertEqual(s["temp_readings"], want["temp_readings"])
        for k in ("body_temp", "body_temp_max"):
            self.assertEqual(pts(s, k), pts(want, k), k)

    def test_a_unit_it_cant_read_is_left_out(self):
        self.ingest(hae_temp_spo2("kelvin"))
        s = self.store.summary()
        self.assertNotIn("temp_readings", s)
        self.assertEqual(pts(s, "spo2_min"), {"2024-03-09": 94, "2024-03-10": 98})   # the rest of the upload still counts


class StoreFromBefore(Base):
    def test_a_database_from_before_is_migrated(self):
        """A watch.db written before #180: its samples have no pulse column. It loads, the readings it holds show,
        and new ones keep their pulse."""
        old = sqlite3.connect(":memory:")
        old.executescript("""
            CREATE TABLE processed(seq INTEGER, digest TEXT PRIMARY KEY, at TEXT);
            CREATE TABLE samples(type TEXT, uuid TEXT, t INTEGER, day TEXT, v REAL, v2 REAL, src TEXT, PRIMARY KEY(type, uuid));
            CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);""")
        old.execute("INSERT INTO samples VALUES('blood_pressure', 'old-1', 1709989500, '2024-03-09', 128, 82, 'Fictional Cuff')")
        old.execute("INSERT INTO meta VALUES('saved', '20240309000000000000')")
        old.commit()
        crypto.write_file(os.path.join(self.dir, "watch.db"), old.serialize())
        store = rx.Store()
        self.assertIn("v3", {r[1] for r in store.db.execute("PRAGMA table_info(samples)")})
        self.assertEqual(readings(store.summary()), [["2024-03-09T13:05:00+00:00", 128, 82, None, CUFF]])
        self.ingest(ldc(BP[3:4]), store)
        self.assertEqual(readings(store.summary())[-1], ["2024-03-10T13:00:00+00:00", 124, 80, 68, CUFF])
        store.save()
        self.assertEqual(readings(rx.Store().summary()), readings(store.summary()))   # saved and loaded again


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(TMP, True)
