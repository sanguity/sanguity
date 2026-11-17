#!/usr/bin/env python3
"""BMI over time (#195): the calculation (kg/m² from a weight in kg or lb and a height in cm, the height typed in cm or
in feet and inches), each weight with the height in effect on its day, and the WHO's adult categories read at one
decimal as BMI is shown. server/person.py has bmi() and who_bmi(); www/js/growth.js does the same on the page, and
tests/browser_check.py checks the two agree. Fictional values only.

Children's growth (#196): the bundled tables (www/js/growth-lms.json, made by tools/growth_tables.py) name their
sources with the SHA-256 docs/growth-charts.md gives, cover birth to 20 for both sexes, and give the percentiles the
WHO and the CDC publish (the WHO's expanded tables by day, the CDC's percentile columns by half month, the CDC's 2022
extended BMI above the 95th). browser_check checks the page's growthPct against the same values.

  python3 tests/test_growth.py
"""
import json, os, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
from server import person   # noqa: E402
import growth_tables as gt   # noqa: E402

M = 30.4375   # days in the WHO's and the CDC's month
# published percentiles: (measure, sex, age in days, value, the percentile it is). The WHO's from its expanded
# percentile tables (who.int, by day of age), the CDC's from its data files' percentile columns (ages at half months)
PUBLISHED = [("weight", "male", 0, 2.507, 3), ("weight", "male", 0, 4.35, 97), ("weight", "male", 365, 9.646, 50),
             ("weight", "female", 61, 4.481, 15), ("weight", "female", 700, 14.337, 97), ("weight", "female", 365, 6.779, 1),
             ("height", "male", 180, 63.463, 3), ("height", "male", 730, 87.802, 50), ("height", "female", 14, 53.484, 85),
             ("head", "female", 91, 41.855, 97), ("head", "male", 200, 42.442, 15), ("head", "female", 730, 44.555, 3),
             ("weight", "female", 120.5 * M, 26.33131782, 10), ("weight", "female", 120.5 * M, 43.85001322, 90),
             ("weight", "male", 30.5 * M, 11.12786972, 3), ("height", "male", 60.5 * M, 101.4523027, 5),
             ("height", "male", 60.5 * M, 116.7549954, 95), ("height", "female", 200.5 * M, 162.8272889, 50),
             ("bmi", "male", 180.5 * M, 23.4512, 85), ("bmi", "male", 180.5 * M, 26.8369, 95), ("bmi", "male", 180.5 * M, 31.9956, 98),
             ("bmi", "female", 96.5 * M, 25.4977, 99), ("bmi", "female", 96.5 * M, 13.5405, 5)]


class Bmi(unittest.TestCase):
    def test_kg_and_cm(self):
        self.assertAlmostEqual(person.bmi(70, "kg", 175), 70 / 1.75 ** 2, places=12)
        self.assertAlmostEqual(person.bmi(70, "kg", 175), 22.857142857, places=8)
        self.assertAlmostEqual(person.bmi(100, "kg", 200), 25.0, places=12)

    def test_lb_is_converted_exactly(self):
        # 1 lb is 0.45359237 kg by definition: 154.3236 lb is 70.00 kg to the gram
        self.assertAlmostEqual(person.bmi(154.32358352941432, "lb", 175), person.bmi(70, "kg", 175), places=10)
        self.assertAlmostEqual(person.bmi(180, "lb", 180), 180 * 0.45359237 / 1.8 ** 2, places=12)

    def test_heights_typed_in_feet_and_inches(self):
        cm = person.from_ftin(5, 9)   # 69 in, 175.26 cm
        self.assertEqual(cm, 175.26)
        self.assertAlmostEqual(person.bmi(160, "lb", cm), 23.63, places=2)   # the familiar 703 × lb / in² gives 23.6
        self.assertAlmostEqual(person.bmi(160, "lb", cm), 703.0696 * 160 / 69 ** 2, places=3)

    def test_nothing_that_isnt_one(self):
        for args in ((0, "kg", 170), (70, "kg", 0), (-5, "kg", 170), (70, "st", 170), ("70", "kg", 170), (70, "kg", None), (True, "kg", 170)):
            self.assertIsNone(person.bmi(*args), args)

    def test_who_categories_read_as_shown(self):
        cat = lambda v: person.who_bmi(v)["range"]
        self.assertEqual([cat(v) for v in (16.0, 18.44, 18.45, 18.5, 24.94, 24.95, 25.0, 29.9, 29.96, 30.0, 41.2)],
                         ["under 18.5", "under 18.5", "18.5–24.9", "18.5–24.9", "18.5–24.9", "25–29.9", "25–29.9", "25–29.9",
                          "30 and over", "30 and over", "30 and over"],
                         "18.45 shows as 18.5 and is read as 18.5; 24.95 shows as 25.0")
        self.assertEqual(person.who_bmi(22)["name"], "normal weight", "the WHO's own words, said as the WHO's")

    def test_each_weight_with_the_height_of_its_day(self):
        heights = [{"date": "2019-01-10", "cm": 178, "unit": "cm"}, {"date": "2026-03-02", "cm": 177.5, "unit": "ftin"}]
        got = [(d, round(person.bmi(w, "lb", (person.height_on(heights, d) or {}).get("cm")) or 0, 2))
               for d, w in (("2018-12-31", 170), ("2019-01-10", 170), ("2026-03-01", 170), ("2026-03-02", 170))]
        self.assertEqual(got, [("2018-12-31", 0), ("2019-01-10", 24.34), ("2026-03-01", 24.34), ("2026-03-02", 24.47)])


class GrowthTables(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "www", "js", "growth-lms.json"), encoding="utf-8") as f:
            cls.T = json.load(f)

    def test_sources_named_with_their_checksums_and_licences(self):
        src = self.T["sources"]
        self.assertEqual({n: (v["url"], v["sha256"]) for n, v in src.items()}, {n: tuple(v) for n, v in gt.SOURCES.items()})
        with open(os.path.join(ROOT, "docs", "growth-charts.md"), encoding="utf-8") as f:
            doc = f.read()
        for n, v in src.items():
            self.assertIn(v["sha256"], doc, f"docs/growth-charts.md gives {n}'s SHA-256")
            self.assertIn("GPL-3" if "anthro" in n else "public domain", v["licence"])

    def test_birth_to_20_for_both_sexes(self):
        for key in ("weight", "length", "head"):
            for sex in ("1", "2"):
                rows = self.T["who"][key][sex]
                self.assertEqual((rows[0][0], rows[-1][0]), (0, 730), (key, sex))
                self.assertEqual([r[0] for r in rows[:92]], list(range(92)), "every day to 13 weeks")
        for key in ("weight", "stature", "bmi"):
            for sex in ("1", "2"):
                rows = self.T["cdc"][key][sex]
                self.assertEqual((rows[0][0], rows[-1][0]), (24, 240), (key, sex))
        self.assertEqual(len(self.T["cdc"]["bmi"]["1"][0]), 5, "BMI rows carry the 2022 extended charts' scale (sigma)")
        self.assertEqual((self.T["who"]["curves"], self.T["cdc"]["curves"]), ([3, 15, 50, 85, 97], [5, 10, 25, 50, 75, 90, 95]))

    def test_the_published_percentiles(self):
        for key, sex, days, x, want in PUBLISHED:
            got = gt.percentile(self.T, key, sex, days, x)
            self.assertAlmostEqual(got, want, delta=0.1, msg=(key, sex, days, x))

    def test_a_curve_is_the_published_value(self):
        # the 50th percentile curve is M; at a CDC row's age, its percentile columns exactly
        L, Mv, S = gt.lms_at(self.T["cdc"]["stature"]["1"], 60.5)[:3]
        self.assertAlmostEqual(gt.x_of(0, L, Mv, S), 109.1751441, places=4)
        self.assertAlmostEqual(gt.x_of(-1.6448536269514722, L, Mv, S), 101.4523027, places=3)
        L, Mv, S = gt.lms_at(self.T["who"]["weight"]["2"], 365)
        self.assertAlmostEqual(gt.x_of(1.8807936081512509, L, Mv, S), 11.331, places=3)

    def test_which_chart_and_what_none(self):
        self.assertIsNone(gt.percentile(self.T, "head", "female", 800, 48), "no head circumference after 2 years")
        self.assertIsNone(gt.percentile(self.T, "bmi", "female", 400, 16), "no BMI before 2")
        self.assertIsNone(gt.percentile(self.T, "weight", "", 400, 9), "no sex, no chart")
        self.assertIsNone(gt.percentile(self.T, "weight", "female", 241 * M, 60), "nothing after 20")
        self.assertIsNotNone(gt.percentile(self.T, "height", "male", 730, 88), "2 years to the day: the WHO's")
        self.assertIsNotNone(gt.percentile(self.T, "height", "male", 731, 88), "the day after: the CDC's")

    def test_the_whos_extremes(self):
        # beyond 3 SD, weight is measured in the distance between the 2 and 3 SD lines (the WHO's anthro): 3.5 SD there
        L, Mv, S = gt.lms_at(self.T["who"]["weight"]["1"], 365)
        sd3, sd2 = gt.x_of(3, L, Mv, S), gt.x_of(2, L, Mv, S)
        self.assertAlmostEqual(gt.percentile(self.T, "weight", "male", 365, sd3 + 0.5 * (sd3 - sd2)), 100 * gt.phi(3.5), places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
