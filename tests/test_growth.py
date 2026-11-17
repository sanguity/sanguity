#!/usr/bin/env python3
"""BMI over time (#195): the calculation (kg/m² from a weight in kg or lb and a height in cm, the height typed in cm or
in feet and inches), each weight with the height in effect on its day, and the WHO's adult categories read at one
decimal as BMI is shown. server/person.py has bmi() and who_bmi(); www/js/growth.js does the same on the page, and
tests/browser_check.py checks the two agree. Fictional values only.

  python3 tests/test_growth.py
"""
import os, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import person   # noqa: E402


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
