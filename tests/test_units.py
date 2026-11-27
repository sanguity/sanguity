#!/usr/bin/env python3
"""Unit preferences (#41, server/units.py): only exact conversions (a change of prefix, a molar mass for the tests
in units.TESTS, lb and kg by definition, °F and °C, cm and inches for heights, #194), never one for a test without a fixed factor; crossmatch's
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
        # #194: heights, cm and inches by definition; "ft and in" draws a series in inches
        self.assertEqual(units.conversions(None, "in"), {"cm": ["cm", 2.54, 0]})
        self.assertEqual(units.conversions(None, "[in_i]"), {"cm": ["cm", 2.54, 0]})
        self.assertAlmostEqual(units.conversions(None, "cm")["ftin"][1] * 2.54, 1, places=12)
        self.assertEqual(units.conversions(None, "cm")["ftin"][0], "in")
        self.assertEqual(units.conversions(None, "m"), {}, "metres (a watch's distance) aren't a height")


def page_js(expr):
    """expr evaluated in node with ranges.js and units.js loaded as a page loads them (one scope), as JSON."""
    import json, subprocess
    js = ("const vm = require('vm'), fs = require('fs'); const ctx = vm.createContext({CFG: {units: {}}});"
          "for (const f of process.argv.slice(1, -1)) vm.runInContext(fs.readFileSync(f, 'utf8'), ctx, {filename: f});"
          "console.log(JSON.stringify(vm.runInContext(process.argv.at(-1), ctx)));")
    files = [os.path.join(ROOT, "www", "js", n) for n in ("ranges.js", "units.js")]
    r = subprocess.run(["node", "-e", js, *files, expr], capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)


@unittest.skipUnless(__import__("shutil").which("node") or os.environ.get("CI"), "needs node")   # in CI, a missing node fails
class PageFigures(unittest.TestCase):
    def test_a_converted_value_keeps_its_figures(self):
        """#898: units.js ucFmt wrote a converted value to as many significant figures as it was reported with (at least
        3), but to 6 decimal places at most, so a very small one lost them: Creatinine "1.2E-3 µmol/L" in mg/dL was
        "0.000014", about 3% off (1.36e-5). Now every value keeps its figures, however small, here against Decimal's
        rounding to the same figures; a large one is written whole, as before."""
        cr = units.conversions("Creatinine", "µmol/L")["conventional"]
        self.assertEqual(cr[0], "mg/dL")
        cases = [("1.2E-3", cr[1]), ("0.5", cr[1]), ("88.4", cr[1]), ("1.25E-6", 1), ("3.0E-9", 1e-3), ("7.123E-4", 0.1),
                 ("4,5", cr[1]), ("120", 1000), ("6.5E5", 1)]
        got = page_js("CASES.map(([t, a]) => ucText({a, b: 0}, t))".replace("CASES", __import__("json").dumps(cases)))
        for (t, a), g in zip(cases, got):
            with self.subTest(value=t, factor=a):
                x = Decimal(t.replace(",", ".")) * Decimal(repr(a))
                sig = max(3, len(Decimal(t.replace(",", ".")).as_tuple().digits))
                want = x.quantize(Decimal(1).scaleb(x.adjusted() - sig + 1)) if x.adjusted() - sig + 1 < 0 else x.quantize(Decimal(1))
                self.assertEqual(Decimal(g), want, f"{t} × {a}")
        self.assertEqual(got[0], "0.0000136", "1.2E-3 µmol/L of creatinine in mg/dL")
        self.assertEqual(page_js("[ucFmt(0, 3), ucFmt(NaN, 3), ucFmt(-1.5e-8, 2), ucFmt(1e-30, 3)]"), ["0", "", "-0.0000000150", "0." + "0" * 29 + "100"])

    def test_a_unit_said_alike_on_the_page_and_the_server(self):
        """#1103: a unit as people write it, one rule in two places: util.js unitSaid (the pages) and units.said (what
        the server writes out: the visit summary's PDF, a share link's measures), each giving the same for every unit
        here; how it's written only, U/L (enzyme units) and the units people already write kept."""
        import json, re, subprocess
        src = open(os.path.join(ROOT, "www", "js", "util.js"), encoding="utf-8").read()
        rule = re.search(r"const UNIT_SUP = .*?\nfunction unitSaid\(u\) \{.*?\n\}\n", src, re.S).group()
        r = subprocess.run(["node", "-e", rule + "console.log(JSON.stringify(JSON.parse(process.argv[1]).map(unitSaid)))", json.dumps(UNIT_CASES)],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), [units.said(u) for u in UNIT_CASES])


# units as reports code them, and as people write them (#1103: util.js unitSaid, units.said)
UNIT_SAID = {"10*3/uL": "10³/µL", "x10E3/uL": "10³/µL", "10^9/L": "10⁹/L", "x10E12/L": "10¹²/L", "10*6/uL": "10⁶/µL",
             "mm[Hg]": "mmHg", "[in_i]": "in", "Cel": "°C", "[degF]": "°F", "{beats}/min": "beats/min", "kg/m2": "kg/m²",
             "umol/L": "µmol/L", "uIU/mL": "µIU/mL", "ug/dL": "µg/dL", "mL/min/{1.73_m2}": "mL/min/1.73 m²",
             "mL/min/1.73m2": "mL/min/1.73m²", "10*3/mm3": "10³/mm³", "/[HPF]": "/HPF", "K/uL": "K/µL",
             "U/L": "U/L", "mg/dL": "mg/dL", "mEq/L": "mEq/L", "%": "%", "fL": "fL", "bpm": "bpm", "cm": "cm", "in": "in",
             "mm/h": "mm/h", "IU/L": "IU/L", "10³/µL": "10³/µL", "": ""}
UNIT_CASES = list(UNIT_SAID)


class OneUnitTwoSpellings(unittest.TestCase):
    """#1102: a FHIR file's platelets in "10*3/uL" (said 10³/µL) and a CSV's in "K/µL" were two series in Latest and the
    Overview, one unit spelled two ways. Each series carries its unit's comparable form (uk), which the page joins on."""

    def test_one_form(self):
        from server.importers import common
        for spellings in (["10*3/uL", "10³/µL", "10^3/uL", "x10E3/uL", "K/µL", "K/uL", "Thou/uL", "10³/μL"],
                          ["10*6/uL", "x10E6/uL", "10⁶/µL", "M/uL", "mil/uL"], ["mm[Hg]", "mmHg"]):
            for f in (common.unit_norm, common.unit_key):
                self.assertEqual(len({f(u) for u in spellings}), 1, (f.__name__, spellings, [f(u) for u in spellings]))
        self.assertNotEqual(common.unit_key("10*3/uL"), common.unit_key("10*6/uL"))
        self.assertNotEqual(common.unit_key("mg/dL"), common.unit_key("mmol/L"))
        data = units.annotate({"labs": [{"name": "Platelet Count", "unit": "10*3/uL"}, {"name": "Platelet Count", "unit": "K/µL"},
                                        {"name": "Glucose, Blood", "unit": "mg/dL"}]})
        self.assertEqual([s["uk"] for s in data["labs"]], ["k/ul", "k/ul", "mg/dl"])


class UnitSaid(unittest.TestCase):
    def test_a_unit_as_people_write_it(self):   # #1103
        self.assertEqual({u: units.said(u) for u in UNIT_CASES}, UNIT_SAID)
        self.assertIsNone(units.said(None))

    def test_the_pdf_writes_a_power_it_has_no_glyph_for_plainly(self):   # #1103
        """The summary's PDF fonts (Windows-1252) have ¹ ² ³ but no other superscript: 10⁹/L is written 10^9/L, never
        "109/L", which reads as another number."""
        from server import pdfdoc
        self.assertEqual([pdfdoc.plain(u) for u in ("10³/µL", "10⁹/L", "10¹²/L", "10⁻³/L", "kg/m²")], ["10³/µL", "10^9/L", "10¹²/L", "10^-3/L", "kg/m²"])


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

    def test_two_draws_on_one_day_one_chart(self):   # #1102, browser_check's #471 setup
        """An outside lab's Creatinine 1.6 H typed in on the day of the clinic's draw (0.92): both series of the test are
        served with one unit form (uk), so the page charts both results in one Creatinine (a series without one, made
        on the page, joins by its unit as written: same.js joinSeries), never one hidden behind the other."""
        db = dbm.connect()
        pid = prof.create_profile(db, "Two draws (fictional)", owner_id=self.uids["robin"])
        with dbm.transaction(db):
            records.store(db, pid, DATA, "upload")
        db.close()
        row = {"date": "2026-10-02", "time": "14:30", "test": "Creatinine", "value": "1.6", "unit": "mg/dL", "range": "0.6-1.2",
               "flag": "H", "lab": "Fictional Outside Lab"}
        r = self.robin.post(f"/api/profiles/{pid}/manual", json={"rows": [row], "save": True}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        d = self.robin.get(f"/api/profiles/{pid}/data").json()
        cr = [s for s in d["labs"] if s["name"] == "Creatinine"]
        self.assertEqual(sorted(p[1] for s in cr for p in s["pts"]), [0.92, 1.6])
        self.assertEqual({s["uk"] for s in cr}, {"mg/dl"})
        self.assertEqual({s["unit"] for s in cr}, {"mg/dL"})

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
                         {"lab": "reported", "weight": "reported", "temperature": "reported", "height": "reported"})
        for bad in ("si", {"lab": "mmol"}, {"lab": "si", "volume": "l"}, {"weight": "stone"}, {"temperature": "K"}, {"height": "m"}):
            r = self.kim.put(f"/api/profiles/{self.pid}/settings/units", json={"value": bad}, headers=H)
            self.assertEqual(r.status_code, 400, bad)
        r = self.kim.put(f"/api/profiles/{self.pid}/settings/units", json={"value": {"lab": "si", "weight": "kg"}}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)   # a viewer sets their own units
        self.assertEqual(r.json()["units"], {"lab": "si", "weight": "kg", "temperature": "reported", "height": "reported"})
        self.assertEqual(self.robin.get(f"/api/profiles/{self.pid}/settings").json()["units"]["lab"], "reported")
        secs = {st["key"]: st for sec in self.kim.get(f"/api/profiles/{self.pid}/settings/sections").json()["sections"]
                for st in sec["settings"]}
        self.assertEqual((secs["units"]["scope"], secs["units"]["kind"], secs["units"]["from"]), ("user", "units", "user"))
        self.assertEqual(list(secs["units"]["choices"]), ["lab", "weight", "temperature", "height"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
