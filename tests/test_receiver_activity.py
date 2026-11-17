"""Respiratory rate and cardio fitness from the phone apps (#226): watch/receiver.py reads VO2 max from Life
Dashboard Companion (Health Connect's Vo2Max) and Health Auto Export (HealthKit's VO2Max), the Apple Health importer
from an uploaded export.xml, and respiratory rate from all three; the same readings give the same days and values
whichever way they came. All data here is fictional, written for these tests.

  python tests/test_receiver_activity.py
"""
import hashlib, importlib.util, json, os, shutil, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="sanguity-activity-")
os.environ["SANGUITY_KEY_FILE"] = os.path.join(TMP, "keys", "data_key")

from server import crypto   # noqa: E402
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


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(TMP, True)
