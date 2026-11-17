#!/usr/bin/env python3
"""Unit preferences (#41, server/units.py): only exact conversions (a change of prefix, a molar mass for the tests
in units.TESTS, lb and kg by definition, °F and °C), never one for a test without a fixed factor; crossmatch's
comparison is the same table and still never uses a molar mass; the records the page loads carry each series'
conversions, the watch's weights too (a temperature change never); the "units" setting is per user and refuses
what it doesn't know. Fictional data only.

  python3 tests/test_units.py
"""
import os, sys, tempfile, unittest
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-units-test-")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "no-build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_CORRECTIONS=os.path.join(TMP, "none", "corrections.json"))
os.makedirs(os.path.join(TMP, "records"))
os.makedirs(os.path.join(TMP, "keys"))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, crossmatch as cm, db as dbm, main, profiles as prof, records, units   # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites"}


def lab(name, unit, pts, loinc=None):
    return {"name": name, "unit": unit, "panel": "Fictional panel", "n": len(pts), "pts": pts, "txt": [],
            **({"loinc": loinc} if loinc else {})}


DATA = {"built": "2026-11-01T00:00:00+00:00", "source": "fictional",
        "events": [{"id": 1, "t": "2026-10-02T08:10:00", "k": "lab", "title": "Fictional panel"}],
        "details": {"1": {"type": "result", "rows": [["Glucose", "95", "mg/dL", "70-99", ""]]}},
        "labs": [lab("Glucose", "mg/dL", [["2026-10-02T08:10:00", 95.0, 70, 99, "", "95"]], loinc="2345-7"),
                 lab("Creatinine", "mg/dL", [["2026-10-02T08:10:00", 0.92, 0.7, 1.3, "", "0.92"]]),
                 lab("Hemoglobin", "g/L", [["2026-10-02T08:10:00", 141.0, 135, 175, "", "141"]]),
                 lab("Triglycerides", "mg/dL", [["2026-10-02T08:10:00", 120.0, None, 150, "", "120"]]),
                 lab("ALT", "U/L", [["2026-10-02T08:10:00", 24.0, 7, 56, "", "24"]]),
                 lab("Temperature", "°F", [["2026-10-02T08:10:00", 98.6, None, None, "", "98.6 [degF]"]])],
        "costs": [], "clinicWeights": [["2026-10-02T08:10:00", 176.4, "Fictional clinic"]]}


class Factors(unittest.TestCase):
    def test_prefix_changes_are_exact(self):
        self.assertEqual(units.exact_factor("Hemoglobin", "g/L", "g/dL"), Decimal("0.1"))
        self.assertEqual(units.exact_factor("Creatinine", "mmol/L", "umol/L"), Decimal(1000))
        self.assertEqual(units.exact_factor("Sodium, Blood", "mEq/L", "mmol/L"), 1)   # monovalent: the same
        self.assertIsNone(units.exact_factor("Calcium", "mEq/L", "mmol/L"))           # divalent: not the same
        self.assertIsNone(units.exact_factor("Glucose, Blood", "mg/dL", "mmol/L"))    # needs a molar mass

    def test_crossmatch_uses_the_same_table_and_no_molar_mass(self):
        self.assertIs(cm.factor, units.exact_factor)
        g = {"name": "Glucose", "loinc": "2345-7"}
        self.assertFalse(cm.same_value({**g, "unit": "mg/dL"}, ["t", 90.0, None, None, "", "90"],
                                       {**g, "unit": "mmol/L"}, ["t", 5.0, None, None, "", "5.0"]))

    def test_molar_conversions_only_for_listed_tests(self):
        self.assertEqual(units.lab_factor("Glucose, Blood", "mg/dL", "mmol/L"), Decimal(10) / Decimal("180.156"))
        self.assertEqual(units.lab_factor("Glucose, Blood", "mmol/L", "mg/dL"), Decimal("18.0156"))
        self.assertEqual(units.lab_factor("Creatinine", "mg/dL", "umol/L"), Decimal(10000) / Decimal("113.12"))
        self.assertIsNone(units.lab_factor("Triglycerides", "mg/dL", "mmol/L"))   # rests on an assumed molecule
        self.assertIsNone(units.lab_factor("Total Bilirubin", "mg/dL", "umol/L"))
        self.assertIsNone(units.lab_factor("ALT", "U/L", "ukat/L"))

    def test_conversions_by_choice(self):
        c = units.conversions("Glucose, Blood", "mg/dL")
        self.assertEqual(list(c), ["si"])
        self.assertEqual(c["si"][0], "mmol/L")
        self.assertAlmostEqual(95 * c["si"][1] + c["si"][2], 5.2732, places=4)
        self.assertEqual(units.conversions("Glucose, Blood", "mmol/L")["conventional"][:1], ["mg/dL"])
        self.assertEqual(units.conversions("Creatinine", "mg/dL")["si"][0], "µmol/L")
        self.assertEqual(units.conversions("Hemoglobin", "g/L")["conventional"], ["g/dL", 0.1, 0])
        self.assertEqual(units.conversions("Triglycerides", "mg/dL"), {})
        self.assertEqual(units.conversions("ALT", "U/L"), {})
        self.assertEqual(units.conversions(None, "lb"), {"kg": ["kg", 0.45359237, 0]})
        self.assertAlmostEqual(units.conversions(None, "kg")["lb"][1] * 0.45359237, 1, places=12)
        f = units.conversions(None, "°F")["C"]
        self.assertEqual(f[0], "°C")
        self.assertAlmostEqual(98.6 * f[1] + f[2], 37.0, places=9)
        c = units.conversions(None, "Cel")["F"]
        self.assertAlmostEqual(37 * c[1] + c[2], 98.6, places=9)
        self.assertEqual(units.conversions(None, "°C", change=True), {})   # a change has no offset to apply


class Served(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = TestClient(main.app, base_url="http://testserver").__enter__()
        db = dbm.connect()
        cls.uids = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        cls.pid = prof.create_profile(db, "Fictional person", owner_id=cls.uids["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (cls.uids["kim"], cls.pid))
        with dbm.transaction(db):
            records.store(db, cls.pid, DATA, "upload")
        db.close()
        cls.robin, cls.kim = cls.login("robin"), cls.login("kim")

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

    @staticmethod
    def login(name):
        c = TestClient(main.app, base_url="http://testserver")
        r = c.post("/api/login", json={"username": name, "password": PW[name]}, headers=H)
        assert r.status_code == 200, r.text
        return c

    def test_records_carry_each_series_conversions(self):
        d = self.robin.get(f"/api/profiles/{self.pid}/data").json()
        conv = {s["name"]: s.get("conv") for s in d["labs"]}
        self.assertEqual(conv["Glucose"]["si"][0], "mmol/L")
        self.assertEqual(conv["Creatinine"]["si"][0], "µmol/L")
        self.assertEqual(conv["Hemoglobin"], {"conventional": ["g/dL", 0.1, 0]})
        self.assertIsNone(conv["Triglycerides"])   # no exact conversion: left as reported
        self.assertIsNone(conv["ALT"])
        self.assertEqual(conv["Temperature"]["C"][0], "°C")
        self.assertEqual(d["unitConv"], {"lb": {"kg": ["kg", 0.45359237, 0]}})
        g = next(s for s in d["labs"] if s["name"] == "Glucose")
        self.assertEqual(g["unit"], "mg/dL")
        self.assertEqual(g["pts"][0][1], 95.0)   # the values themselves are as reported; the page converts

    def test_watch_weights_but_never_a_temperature_change(self):
        summary = {"metrics": [{"key": "weight", "name": "Weight", "unit": "lb", "group": "Body", "pts": [["2026-10-01", 176.0]]},
                               {"key": "skin_temp", "name": "Skin temperature change", "unit": "°C", "group": "Other",
                                "pts": [["2026-10-01", 0.4]]},
                               {"key": "steps", "name": "Steps", "unit": "steps", "group": "Activity", "pts": [["2026-10-01", 9000]]}],
                   "exercise": []}
        self.assertEqual(units.annotate_watch(summary)["metrics"][0]["conv"], {"kg": ["kg", 0.45359237, 0]})
        self.assertNotIn("conv", summary["metrics"][1])
        self.assertNotIn("conv", summary["metrics"][2])

    def test_the_setting_is_each_persons_own(self):
        self.assertEqual(self.robin.get(f"/api/profiles/{self.pid}/settings").json()["units"],
                         {"lab": "reported", "weight": "reported", "temperature": "reported"})
        for bad in ("si", {"lab": "mmol"}, {"lab": "si", "volume": "l"}, {"weight": "stone"}, {"temperature": "K"}):
            r = self.kim.put(f"/api/profiles/{self.pid}/settings/units", json={"value": bad}, headers=H)
            self.assertEqual(r.status_code, 400, bad)
        r = self.kim.put(f"/api/profiles/{self.pid}/settings/units", json={"value": {"lab": "si", "weight": "kg"}}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)   # a viewer sets their own units
        self.assertEqual(r.json()["units"], {"lab": "si", "weight": "kg", "temperature": "reported"})
        self.assertEqual(self.robin.get(f"/api/profiles/{self.pid}/settings").json()["units"]["lab"], "reported")
        secs = {st["key"]: st for sec in self.kim.get(f"/api/profiles/{self.pid}/settings/sections").json()["sections"]
                for st in sec["settings"]}
        self.assertEqual((secs["units"]["scope"], secs["units"]["kind"], secs["units"]["from"]), ("user", "units", "user"))
        self.assertEqual(list(secs["units"]["choices"]), ["lab", "weight", "temperature"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
