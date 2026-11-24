#!/usr/bin/env python3
"""Reference ranges from settings (#44, server/ranges.py): the checks on a range (a low or a high, low not above
high, whole-year ages, no two rows for the same lab, unit and sex covering the same ages), which row fits a result
(lab name ignoring case and spaces, the same unit only, sex, the age on the result's day, a row for the sex before
one for anyone), the ranges setting and About this person's birth date and sex (#194) through the API (owners and
editors set them, viewers can't, values encrypted), and the visit summary: a result whose report gives no range is judged against the range from settings and printed
with "(settings)"; a result with its own range or flag keeps it; a result in another unit gets none. Fictional data
only.

  python3 tests/test_ranges.py
"""
import collections, datetime, io, json, os, re, shutil, sys, tempfile, time, unittest, zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-ranges-test-")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_CORRECTIONS=os.path.join(TMP, "config", "corrections.json"))
for d in ("records", "keys", "build", "config", "profiles"):
    os.makedirs(os.path.join(TMP, d), exist_ok=True)
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402
from pypdf import PdfReader   # noqa: E402

from server import accounts, crossmatch, db as dbm, main, profiles as prof, ranges, records, settings, summary   # noqa: E402
import logging   # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ranges_matrix as M   # noqa: E402  (#752's matrix, shared with tests/browser_check.py)

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites", "lee": "maple tunnel brisk saddle"}
TODAY = datetime.date.today()


def day(n):
    return (TODAY + datetime.timedelta(days=n)).isoformat() + "T09:00:00"


def row(**kw):
    return {"lab": "Ferritin", "unit": "ng/mL", "sex": "any", "ageFrom": None, "ageTo": None, "low": 20, "high": 200, **kw}


def page_once(pts, apart=None):
    """A series' results as the records page shows them (same.js joinSeries): in time order, a flagged one after an
    unflagged one at its time (#833), a result's copies (each given the one result's range and flag by
    crossmatch.copies, #825) once, the first; two the person said aren't the same apart (apart: each point's mark, the
    series' "apart", #833)."""
    seen, out = set(), []
    flag = lambda p: ranges.judge(p[1], *p[2:6], None, (list(p) + [None] * 8)[7] or "")[2]   # as judged, the page's ties
    for p, a in sorted(zip(pts, apart or [None] * len(pts)), key=lambda x: (x[0][0], bool(flag(x[0])))):
        q = list(p) + [None] * (8 - len(p))   # (its flag as the page compares it, read as stored: ranges.read_flag, #839)
        k = (q[0], q[1], str(q[5] or "").strip(), q[2], q[3], flag(p), q[7] or "", a)
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


class Rules(unittest.TestCase):
    def test_checks(self):
        self.assertEqual(ranges.check_overrides([{"lab": "  Vitamin  D ", "unit": "ng/mL", "high": 100.0}]),
                         [{"lab": "Vitamin D", "unit": "ng/mL", "sex": "any", "ageFrom": None, "ageTo": None, "low": None, "high": 100}])
        for bad, says in [([{"lab": "Ferritin", "unit": "ng/mL"}], "needs a low, a high"),
                          ([row(low=300)], "low is above the high"),
                          ([row(ageFrom=40, ageTo=20)], "age from is above"),
                          ([row(ageFrom=1.5)], "whole number"),
                          ([row(ageTo=200)], "whole number"),
                          ([row(sex="other")], "sex must be"),
                          ([row(unit="")], "unit"),
                          ([row(low="12")], "must be a number"),
                          ([row(low=True)], "must be a number"),
                          ([row(), row(lab="ferritin", unit="ng / mL", ageFrom=18)], "cover the same ages"),
                          ([row(ageTo=17), row(ageFrom=17)], "cover the same ages"),
                          ("x", "list"), ([row()] * 201, "At most")]:
            with self.assertRaises(ranges.RangeError, msg=bad if isinstance(bad, str) else bad[:2]) as e:
                ranges.check_overrides(bad)
            self.assertIn(says, str(e.exception))
        # separate bands, or the same band for different sexes, or another unit, are fine
        self.assertEqual(len(ranges.check_overrides([row(ageTo=17), row(ageFrom=18), row(sex="female"), row(sex="male"),
                                                     row(unit="ug/L")])), 5)
        # the birth date and sex are About this person's since #194 (their checks: tests/test_person.py)

    def test_row_series(self):
        """#782: each drawer row named with the series its result is in (records._row_series), by the same results' match:
        the same import and day and value, the row's name or its canonical one ("Glucose" is "Glucose, Blood"), else the
        one row in the series' unit; none for a text row, a row of another import, or one two series both claim."""
        t = day(-3)
        data = {"events": [{"id": 1, "t": t, "k": "labitem", "imp": 1}, {"id": 2, "t": t, "k": "labitem", "imp": 2}],
                "details": {"1": {"type": "result", "rows": [["Glucose", "95", "mg/dL", "70 - 99", ""], ["Chol LDL", "120", "mg/dL", "<100", ""],
                                                             ["Comment", "see note", "", "", ""], ["Trig", "88", "mg/dL", "", ""], ["TG", "88", "mg/dL", "", ""],
                                                             ["Chol", "200", "mg/dL", "", ""]]},
                            "2": {"type": "result", "rows": [["Glucose", "95", "mg/dL", "70 - 99", ""]]}},
                "labs": [{"name": "Glucose, Blood", "unit": "mg/dL", "imp": 1, "pts": [[t, 95, 70, 99, "", "95"]]},
                         {"name": "LDL Cholesterol", "unit": "mg/dL", "imp": 1, "pts": [[t, 120, None, 100, "H", "120"]]},
                         {"name": "Triglycerides", "unit": "mg/dL", "imp": 1, "pts": [[t, 88, None, None, "", "88"]]},
                         {"name": "Cholesterol, Total", "unit": "mg/dL", "imp": 1, "pts": [[t, 200, None, None, "", "200"]]},
                         {"name": "Non-HDL Cholesterol", "unit": "mg/dL", "imp": 1, "pts": [[t, 200, None, None, "", "200"]]}]}
        records._row_series(data)
        self.assertEqual(data["details"]["1"]["series"], ["Glucose, Blood", "LDL Cholesterol", None, None, None, None],
                         "a canonical name, the one row in its unit with its value, a text row, two rows alike (\"Trig\", \"TG\") and a row "
                         "two series claim (\"Chol\")")
        self.assertNotIn("series", data["details"]["2"], "another import's row is none of these series'")

    def test_age_on(self):
        self.assertEqual(ranges.age_on("1980-04-30", "2026-04-29"), 45)
        self.assertEqual(ranges.age_on("1980-04-30", "2026-04-30T08:00:00"), 46)
        self.assertIsNone(ranges.age_on("", "2026-04-30"))
        self.assertIsNone(ranges.age_on("2000-01-01", "1999-12-31"))

    def test_which_row_fits(self):
        rows = [row(low=10, high=100), row(sex="female", low=15, high=150, ageFrom=18, ageTo=49),
                row(sex="female", low=20, high=200, ageFrom=50), row(sex="male", low=30, high=300)]
        cfg = {"birthDate": "1980-06-15", "sex": "female", "overrides": rows}
        find = lambda c, lab="Ferritin", unit="ng/mL", on="2020-01-01": ranges.find(c, lab, unit, on)
        self.assertEqual(find(cfg)["high"], 150, "female, 39: the female 18-49 row, before the one for anyone")
        self.assertEqual(find(cfg, on="2030-06-15")["high"], 200, "50 on her birthday: the next band")
        self.assertEqual(find(cfg, on="1990-01-01")["high"], 100, "9: no female band fits, so the one for anyone")
        self.assertEqual(find(cfg, lab="  FERRITIN ", unit="ng / mL")["high"], 150, "case and spaces don't matter")
        self.assertIsNone(find(cfg, unit="ug/L"), "another unit: nothing, and nothing converted")
        self.assertIsNone(find(cfg, lab="Ferritin, serum"))
        self.assertEqual(find({**cfg, "sex": "male"})["high"], 300)
        self.assertEqual(find({**cfg, "sex": ""})["high"], 100, "no sex set: only the rows for anyone")
        self.assertEqual(find({**cfg, "birthDate": ""})["high"], 100, "no birth date: no row with an age band")
        self.assertIsNone(find({**cfg, "birthDate": "", "overrides": rows[1:3]}))
        self.assertIsNone(find({}))
        self.assertEqual(ranges.flag(201, 20, 200), "H")
        self.assertEqual(ranges.flag(19.9, 20, 200), "L")
        self.assertEqual(ranges.flag(20, 20, 200), "")
        self.assertEqual(ranges.flag(5, None, 4), "H")

    def test_copies(self):
        """#781: one result twice, kept as one: the kept copy's range and flag, what it lacks from the other's, as
        edits.py's merge fills an event; a flag only where it was given against the range kept; the range's edge with it
        (#780)."""
        for a, b, want in [((None, None, ""), (3.5, 5.1, "L"), (3.5, 5.1, "L")),   # the issue's: none, then the report's
                           ((8.5, 10.5, ""), (None, None, ""), (8.5, 10.5, "")),   # the kept's own, the flag worked out later
                           ((None, None, "L"), (135, 145, ""), (135, 145, "L")),   # a flag in one, the range in the other
                           ((135, 145, ""), (None, None, " L "), (135, 145, "L")),
                           ((3.5, 5.1, "H"), (3.4, 5.0, "L"), (3.5, 5.1, "H")),   # the kept's own wins
                           ((3.5, 5.1, ""), (3.4, 5.0, "L"), (3.5, 5.1, "")),   # a Low against another range isn't taken
                           ((None, 5, ""), (1, 5, "H"), (None, 5, "")),   # a range is its low and high together
                           ((None, None, None), (None, None, None), (None, None, "")),
                           # #780: the edge goes with its range ("<5": 5 left out of it), given where either copy has one
                           ((None, None, "", ""), (None, 5, "", "<"), (None, 5, "", "<")),   # none, then the report's "<5"
                           ((None, None, ""), (None, 5, "", "<"), (None, 5, "", "<")),
                           ((None, 5, "", "<"), (None, None, "", ""), (None, 5, "", "<")),   # the kept's own
                           ((None, 5, "", ""), (None, 5, "", "<"), (None, 5, "", "")),   # "≤ 5" kept: the kept's own range
                           ((None, 5, "", ""), (None, 5, "H", "<"), (None, 5, "", "")),   # a High against "<5" isn't "≤ 5"'s
                           ((None, 5, "", "<"), (None, 5, "H", "<"), (None, 5, "H", "<"))]:
            self.assertEqual(ranges.copies(a, b), want, (a, b))
        # #847: two copies are one result only where their own ranges agree (the low, the high and the edge) or one has none
        for a, b, want in [((None, None, ""), (3.5, 5.1, "L"), True), ((3.5, 5.1, "H"), (None, None, "A"), True),
                           ((3.5, 5.1, "H"), (3.5, 5.1, ""), True), ((None, 5, "H", "<"), (None, 5, "", "<"), True),
                           ((None, 5, "H", "<"), (None, 5, "", ""), False), ((60, None, "", ""), (60, None, "L", ">"), False),
                           ((13.5, 17.5, "L"), (12, 16, ""), False), ((3.5, 5.1, ""), (3.5, 5.3, ""), False), ((None, 5, ""), (1, 5, ""), False)]:
            self.assertEqual((ranges.agree(a, b), ranges.agree(b, a)), (want, want), (a, b))

    def test_copies_not_linked(self):
        """#825: a result twice and not linked (two imports, or twice from one: the same lab, unit, time, value and value
        as given) is given the one result's range and flag in each copy (crossmatch.copies, by ranges.copies: the first
        copy's own, what it lacks from the others'), the range's edge with it, in either import order; a result at
        another time, with another value as given, of another unit, or a watch's, is left as it is, and the records' own
        lists aren't changed."""
        t, u = day(-2), day(-1)
        data = {"labs": [
            {"name": "Potassium", "unit": "mmol/L", "imp": 1, "pts": [[t, 5.2, 3.5, 5.1, "", "5.2"], [u, 4.0, None, None, "", "4.0"]]},
            {"name": "Sodium", "unit": "mmol/L", "imp": 1, "pts": [[t, 150, None, None, "", "150"]]},
            {"name": "Potassium", "unit": "mmol/L", "imp": 2, "pts": [[t, 5.2, None, None, "", "5.2"], [u, 4.1, 3.5, 5.1, "", "4.1"]]},
            {"name": "Sodium", "unit": "mmol/L", "imp": 2, "pts": [[t, 150, 135, 145, "H", "150", "Portal"]]},
            {"name": "Potassium", "unit": "mg/dL", "imp": 2, "pts": [[t, 5.2, None, None, "", "5.2"]]},
            {"name": "C-Reactive Protein", "unit": "mg/L", "imp": 1, "pts": [[t, 5, None, None, "", "5"], [t, 5, None, 5, "", "5", None, "<"]]},
            {"name": "C-Reactive Protein", "unit": "mg/L", "imp": 2, "pts": [[t, 5, None, None, "", "5.0"]]},
            {"name": "Potassium", "unit": "mmol/L", "imp": 3, "watch": True, "pts": [[t, 5.2, None, None, ""]]}]}
        was = json.loads(json.dumps(data["labs"]))
        own = [s["pts"] for s in data["labs"]]
        labs = crossmatch.copies(data)["labs"]
        got = [s["pts"] for s in labs]
        self.assertEqual([s.get("rangeFrom") for s in labs], [None, {"0": 2}, {"0": 1}, None, None, {"0": 1}, None, None],
                         "#834: a copy whose report gave no range says whose copy's gave it")
        # (a copy given the one result's range has its flag as read, #839: the first's 5.2 against its 3.5–5.1 is "H"; a
        # copy left as it was keeps its own, read the same)
        self.assertEqual(got, [[[t, 5.2, 3.5, 5.1, "", "5.2"], [u, 4.0, None, None, "", "4.0"]],   # the first copy's own
                               [[t, 150, 135, 145, "H", "150"]],   # the second's range and its report's H
                               [[t, 5.2, 3.5, 5.1, "H", "5.2"], [u, 4.1, 3.5, 5.1, "", "4.1"]],   # the first's range
                               [[t, 150, 135, 145, "H", "150", "Portal"]],
                               [[t, 5.2, None, None, "", "5.2"]],   # another unit
                               [[t, 5, None, 5, "H", "5", None, "<"], [t, 5, None, 5, "", "5", None, "<"]],   # twice from one, "<5" with it
                               [[t, 5, None, None, "", "5.0"]],   # "5.0" given otherwise
                               [[t, 5.2, None, None, ""]]])   # a watch's
        self.assertEqual([ranges.judge(p[1], *p[2:6], None, (p + [None] * 8)[7] or "")[2] for p in (got[1][0], got[2][0], got[5][0])], ["H", "H", "H"],
                         "each copy judged High: 150 against 135–145, 5.2 against 3.5–5.1, 5 against “<5”")
        self.assertEqual(own, [s["pts"] for s in was], "the records' own lists as they were")
        self.assertEqual([s.get("apart") for s in labs], [None] * 8, "one result each")

    def test_copies_with_two_ranges(self):
        """#847: copies of one result (the same lab, unit, time and value as given) are one result only where their own
        ranges agree or one has none. Two whose reports print different ranges, in the edge ("<5" and "<=5", ">=60" and
        ">60") or in the limits (13.5–17.5 and 12–16), are two results, each with its own range and flag, in either import
        order (crossmatch.copies' "apart"; the summary's points the same): the first's range and High were given to the
        second, beside its own "<=5". A third copy with no range joins the first result (in import order) and takes its
        range; with a range of its own like the second's, it's the second's."""
        t = day(-2)
        crp = lambda imp, rng, edge, flag: {"name": "CRP", "unit": "mg/L", "imp": imp, "pts": [[t, 5, None, rng, flag, "5", None, edge]]}
        egfr = lambda imp, edge, flag: {"name": "eGFR", "unit": "mL/min/1.73m2", "imp": imp, "pts": [[t, 60, 60, None, flag, "60", None, edge]]}
        hgb = lambda imp, lo, hi, flag: {"name": "Hemoglobin", "unit": "g/dL", "imp": imp, "pts": [[t, 13.0, lo, hi, flag, "13.0"]]}
        flag = lambda p: ranges.judge(p[1], *p[2:6], None, (p + [None] * 8)[7] or "")[2]
        for first, second in ((1, 2), (2, 1)):
            labs = sorted([crp(first, 5, "<", "H"), crp(second, 5, "", ""), egfr(first, "", ""), egfr(second, ">", "L"),
                           hgb(first, 13.5, 17.5, "L"), hgb(second, 12, 16, "")], key=lambda s: s["imp"])
            got = crossmatch.copies({"labs": json.loads(json.dumps(labs))})["labs"]
            said = {(s["name"], s["imp"]): [[flag(p), p[2], p[3], (p + [None] * 8)[7] or ""] for p in s["pts"]] for s in got}
            self.assertEqual(said, {(s["name"], s["imp"]): [[flag(p), p[2], p[3], (p + [None] * 8)[7] or ""] for p in s["pts"]] for s in labs},
                             f"{first} first: each copy its own range and flag")
            self.assertEqual(sorted((s["name"], s["imp"], s["apart"]) for s in got),
                             sorted((s["name"], s["imp"], {"0": 0 if s["imp"] == 1 else 1}) for s in labs), f"{first} first: two results each")
            self.assertEqual([s.get("rangeFrom") for s in got], [None] * 6, "no range from the other")
            series = summary._series({"labs": got})
            self.assertEqual({n: [[p["flag"], p["lo"], p["hi"], p["edge"]] for p in series[n]["pts"]] for n in ("CRP", "eGFR", "Hemoglobin")},
                             {"CRP": [["", None, 5, ""], ["H", None, 5, "<"]], "eGFR": [["", 60, None, ""], ["L", 60, None, ">"]],
                              "Hemoglobin": [["", 12, 16, ""], ["L", 13.5, 17.5, ""]]}, f"{first} first: the summary's, two each, the flagged last")
        three = [crp(1, None, "", ""), crp(2, 5, "<", "H"), crp(3, 5, "", ""), crp(4, None, "", ""), crp(5, 5, "", "")]
        got = crossmatch.copies({"labs": three})["labs"]
        self.assertEqual([[s["apart"]["0"], flag(s["pts"][0]), s["pts"][0][3], (s["pts"][0] + [None] * 8)[7] or ""] for s in got],
                         [[0, "H", 5, "<"], [0, "H", 5, "<"], [1, "", 5, ""], [0, "H", 5, "<"], [1, "", 5, ""]],
                         "copies with no range join the first result; one with the second's range, the second")

    def test_a_stored_flag(self):
        """#839: a result's flag as stored is read as its report's, by the importers' one rule for a printed flag
        (ranges.read_flag: common.report_flag), so an older import's flag stored as printed reads as a new import of it
        would: ">" and "HH" High, "<" and "LL" Low, whatever the range; "AA" placed by the range where it can be, else
        "A"; "N" and "Normal" no flag, so the range says it, and a range from Settings applies where its report printed
        none; what an import stores now reads as it is. The summary says it so (judge, out_of_range), and a result's
        copies are put together with each copy's flag as read (crossmatch.copies, Same result's _carry and the summary's):
        a copy's "N" keeps no other copy's flag from it. The page reads it the same way (ranges.js readFlag:
        tests/browser_check.py)."""
        from server.importers import common
        f = ranges.read_flag
        self.assertEqual([f(p, 13, 11, 15) for p in (">", "HH", "High", "H", "<", "LL", "low", "L", "AA", "A", "Abnormal", "N", "Normal", "", None, "Final")],
                         ["H"] * 4 + ["L"] * 4 + ["A"] * 3 + [""] * 5)
        self.assertEqual([f(p, 16, 11, 15) for p in ("N", "Normal", "AA", "<", "")] + [f(p, 10, 11, 15) for p in ("N", "AA", ">")],
                         ["H", "H", "H", "L", "H", "L", "L", "H"])
        self.assertEqual([f(p, None, None, None) for p in ("N", ">", "<", "AA", "Normal", "")], ["", "H", "L", "A", "", ""], "a text result")
        self.assertEqual([f("A>", 13, 11, 15), f("A<", 13, 11, 15)], ["H", "L"], "as the summary read them, its words now the rule's")
        self.assertEqual([f("N", 15, 15, 60, "<15"), f("Normal", 5, None, 5, "5", "<"), f("N", 5, None, 5, "5")], ["L", "H", ""],
                         "a bound, a limit that leaves its edge out (#780)")
        for p in ("H", "L", "A", "AA", ""):   # what an import stores reads as it is
            for v, lo, hi in ((13, 11, 15), (16, 11, 15), (10, 11, 15), (13, None, None), (None, None, None)):
                stored = common.report_flag(p, v, lo, hi)
                self.assertEqual(f(stored, v, lo, hi), stored, (p, v, lo, hi))
        row = {"low": 8.6, "high": 10.3}   # (a range from Settings)
        self.assertEqual([ranges.judge(10.4, None, None, p, "10.4", row) for p in ("Normal", "N", "", ">")],
                         [(8.6, 10.3, "H", ranges.SOURCE)] * 3 + [(None, None, "H", ranges.REPORT)])
        self.assertEqual([summary.out_of_range({"flag": ranges.judge(v, lo, hi, p, str(v))[2]})
                          for p, v, lo, hi in (("N", 13, 11, 15), ("N", 16, 11, 15), (">", 13, 11, 15), ("<", 13, None, None), ("AA", 13, 11, 15),
                                               ("AA", 16, 11, 15), ("Normal", 13, None, None))],
                         [None, "High", "High", "Low", "Flagged", "High", None])
        # a result's copies: the first's "N" (no range) keeps no flag from the second's "H"; "AA" with no range takes the
        # other's range, then placed by it
        t = day(-2)
        data = lambda: {"labs": [{"name": "Potassium", "unit": "mmol/L", "imp": 1, "pts": [[t, 4.0, None, None, "N", "4.0"]]},
                                 {"name": "ALT", "unit": "U/L", "imp": 1, "pts": [[t, 60, None, None, "AA", "60"]]},
                                 {"name": "Potassium", "unit": "mmol/L", "imp": 2, "pts": [[t, 4.0, None, None, "H", "4.0"]]},
                                 {"name": "ALT", "unit": "U/L", "imp": 2, "pts": [[t, 60, 7, 56, "", "60"]]}]}
        got = [ranges.judge(p[1], *p[2:6])[2] for s in crossmatch.copies(data())["labs"] for p in s["pts"]]
        self.assertEqual(got, ["H"] * 4, "each copy as the one result: Potassium the second's H, ALT's AA placed High by 7–56")
        series = summary._series(crossmatch.copies(data()))
        self.assertEqual([summary.out_of_range(p) for n in ("Potassium", "ALT") for p in series[n]["pts"]], ["High", "High"],
                         "the summary's, once each, the same (the records' copies as crossmatch.copies gives them)")
        ks = {"name": "Potassium", "unit": "mmol/L"}
        self.assertEqual(crossmatch._carry([t, 4.0, None, None, "N", "4.0"], ks, [t, 4.0, None, None, "H", "4.0"], ks)[4], "H",
                         "Same result: the kept copy's “N” keeps no flag from the other's “H”")

    # a flag read by its letter or word, in a script (comments left out): a regex on its first letter (/^h/i,
    # /^\s*[hl]/i), a comparison with a flag's letter ("H", "L", "A", "HH", "LL", "AA", "N"), a case of one, a map or
    # list keyed by them or by the summary's words ({H: ...}, {High: ...}, ["H", "L"])
    FLAG_READS = re.compile(r"""/\^(?:\\s\*)?(?:\[[hlHL]{1,4}\]|[hlHL])/|[=!]==?\s*["'](?:[HLA]|HH|LL|AA|N)["']|["'](?:[HLA]|HH|LL|AA|N)["']\s*[=!]==?"""
                            r"""|\bcase\s*["'][HLA]["']|[{,]\s*["']?(?:H|HH|High)["']?\s*:|\[\s*["'](?:H|High)["']\s*,\s*["'](?:L|Low)["']""")

    def test_flags_read_in_one_place(self):
        """#845: no view reads a flag's letter itself. Every script in www/js reads a result's flag through ranges.js
        (readFlag for one as stored, FLAG_OF for its mark, word and way, flagWord, flagOut, flagSaysNeither, statusOf,
        flaggedWords): Labs' list marked a ◆ with ▼ ("H" or else ▼), Settings' tile preview read H and L only, and the
        lab panel counted a ◆ inside its range as outside it. ranges.js is the one file that may (FLAG_WAY, FLAG_OF)."""
        js_dir, found = os.path.join(ROOT, "www", "js"), []
        for n in sorted(os.listdir(js_dir)):
            if not n.endswith(".js") or n == "ranges.js":
                continue
            with open(os.path.join(js_dir, n), encoding="utf-8") as f:
                text = re.sub(r"/\*.*?\*/|(?<![:\"'`\\])//[^\n]*", lambda m: re.sub(r"[^\n]", " ", m.group()), f.read(), flags=re.S)
            for m in self.FLAG_READS.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                found.append(f"www/js/{n}:{line}: {text.splitlines()[line - 1].strip()[:120]}")
        self.assertEqual(found, [], "a flag read by its letter outside ranges.js: read it with FLAG_OF, flagWord, flagOut or statusOf")
        for t, want in (('f === "H"', 1), ("/^h/i.test(f)", 1), ('/^\\s*[hl]/i.test(f)', 1), ('{H: "▲", L: "▼"}[f]', 1), ('{High: "▲ High"}', 1),
                        ('["H", "L"].includes(f)', 1), ('case "A":', 1), ('"L" !== f', 1), ('/^https?:/', 0), ('{value: "H", text: "High"}', 0),
                        ('d: "M0,0L1,1"', 0), ('abnMark(x, y, "H")', 0), ('/^\\d+$/', 0), ('{Hospital: 1}', 0), ('{L: LW, R}', 0), ('/^hi/', 0)):
            self.assertEqual(len(self.FLAG_READS.findall(t)), want, t)   # (the search itself)

    # #874: the page reads a value as printed, its number and its bound, by ranges.js valueRead (and rangeCmp) only.
    # Every other pattern in www/js of a number's digits with a decimal mark (#870's, test_importers NUMBER_SHAPE) or of a
    # bound ("[<>", "≤|≥", "<=|"), by (file, the piece found): how many, and why it reads no value
    NUMBER_SHAPE = re.compile(r"\\d[+*]?\)?\(?(?:\?:)?(?:\\\.|\[[^\]]*[.,][^\]]*\])|\\d[+*]?,\\d|\[(?:\\d|0-9)[^\]]*[.,]|\\d\{1,3\}\(\?:[,.]"
                              r"|\[<>|≤\|≥|<=\||=<\|")
    PAGE_NUMBERS = {
        ("drawer.js", r"\d+(?:[.,]"): (1, "factSays: a fact's one number as written, matched to a key fact's value as written"),
        ("trends.js", "[<>"): (1, "rangeNote: a range as rangeWords writes it, kept on one line"),
        ("trends.js", r"[\d."): (2, "rangeNote: a range as rangeWords writes it, kept on one line"),
        ("util.js", r"[\d."): (3, "keepDates: a date kept on one line; ipHost: an address"),
        ("util.js", r"\d+)\."): (2, "ip4: an IP address"),
        ("util.js", r"\d+\."): (1, "ip4: an IP address"),
        ("util.js", r"\d[\d,]"): (1, "a heading's count, \"(1,234)\", left off the name an ⓘ says"),
    }

    def test_numbers_read_in_one_place_on_the_page(self):
        """#874: the page reads a value as printed by one reader, ranges.js valueRead (beside rangeCmp), as the server
        reads one by common.value_number (#870). units.js (ucNum, ucText, ucRange, ucSig and its own test of a bound) and
        drawer.js (rowSaid) had patterns of their own that read "=<5", "> OR = 60" and "4,5" as no number, so a converted
        unit said "=<5" as "0.500" (its bound gone) and a drawer row "> OR = 60" as text. Every pattern of a number's
        digits with a decimal mark, or of a bound, in www/js outside ranges.js is one PAGE_NUMBERS lists."""
        js_dir, found = os.path.join(ROOT, "www", "js"), collections.Counter()
        for n in sorted(os.listdir(js_dir)):
            if not n.endswith(".js") or n == "ranges.js":
                continue
            with open(os.path.join(js_dir, n), encoding="utf-8") as f:
                text = re.sub(r"/\*.*?\*/|(?<![:\"'`\\])//[^\n]*", lambda m: re.sub(r"[^\n]", " ", m.group()), f.read(), flags=re.S)
            found.update((n, m.group()) for m in self.NUMBER_SHAPE.finditer(text))
        self.assertEqual(dict(found), {k: c for k, (c, _) in self.PAGE_NUMBERS.items()},
                         "a value read outside ranges.js: read it with valueRead (its number, bound and figures) or rangeCmp")
        for t, want in ((r"/^\s*([<>]=?|≤|≥)?\s*(-?\d+(?:\.\d+)?)\s*$/", 3), (r"/^\s*-?\d+(\.\d+)?\s*$/", 1), (r"/[\d.-]+/", 1),
                        (r"/^\s*[<>≤≥]/", 1), (r"/(<=|>=|=<|=>)/", 2), (r"/^\d{4}-\d{2}-\d{2}$/", 0), (r"/\d+/", 0), (r"/^(\d+)px$/", 0)):
            self.assertEqual(len(self.NUMBER_SHAPE.findall(t)), want, t)   # (the search itself)


# a portal export: ferritin with no range (twice), one with its own range, one flagged by the lab with no range,
# one in another unit; vitamin D with no range at all
DATA = {"built": day(0), "source": "fictional",
        "events": [{"id": 1, "t": day(-60), "k": "visit", "title": "Office visit"}],
        "details": {}, "costs": [], "upcoming": [],
        "labs": [{"name": "Ferritin", "unit": "ng/mL", "panel": "Iron", "n": 4, "txt": [],
                  "pts": [[day(-40), 230, None, None, "", "230"], [day(-30), 180, None, None, "", "180"],
                          [day(-20), 260, 30, 400, "", "260"], [day(-10), 15, None, None, "", "15"]]},
                 {"name": "Ferritin", "unit": "ug/L", "panel": "Iron", "n": 1, "txt": [], "pts": [[day(-50), 900, None, None, "", "900"]]},
                 {"name": "Vitamin D, 25-OH", "unit": "ng/mL", "panel": "", "n": 2, "txt": [],
                  "pts": [[day(-25), 12, None, None, "", "12"], [day(-5), 18, None, None, "L", "18"]]},
                 # #649: typed in with no range, the first before the visit
                 {"name": "Vitamin D", "unit": "ng/mL", "panel": "", "n": 4, "txt": [],
                  "pts": [[day(-70), 22, None, None, "", "22"], [day(-40), 26, None, None, "", "26"],
                          [day(-20), 34, None, None, "", "34"], [day(-5), 41, None, None, "", "41"]]},
                 # #717: the first typed in with no range, before the visit; the two since with their report's 3-17
                 {"name": "Folate", "unit": "ng/mL", "panel": "", "n": 3, "txt": [],
                  "pts": [[day(-70), 4, None, None, "", "4"], [day(-30), 6, 3, 17, "", "6"], [day(-10), 7, 3, 17, "", "7"]]}]}


class _Drawn:
    """A PDF page that keeps what's drawn on it: each text as (x0, x1, baseline, text), each polyline's points, each
    rectangle's (x0, x1), and each result's mark's x (a dot's, or a triangle's or diamond's middle)."""
    def __init__(self):
        self.texts, self.lines, self.rects, self.marks = [], [], [], []

    def text(self, x, y, s, size=10, bold=False, color=0):
        self.texts.append((x, x + summary.pdfdoc.width(s, size, bold), y, s))

    def text_right(self, x, y, s, size=10, bold=False, color=0):
        self.text(x - summary.pdfdoc.width(s, size, bold), y, s, size, bold, color)

    def polyline(self, pts, *a):
        self.lines.append(pts)

    def rect(self, x, y, w, h, *a):
        self.rects.append((x, x + w))

    def dot(self, x, y, *a):
        self.marks.append(x)

    def shape(self, pts, *a):
        self.marks.append(pts[0][0])

    def __getattr__(self, name):
        return lambda *a, **k: None


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dbm.init()
        cls.ctx = TestClient(main.app, base_url="http://testserver").__enter__()
        db = dbm.connect()
        uid = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        cls.pid = prof.create_profile(db, "Robin (fictional)", owner_id=uid["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (uid["kim"], cls.pid))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (uid["lee"], cls.pid))
        with dbm.transaction(db):
            records.store(db, cls.pid, DATA, "upload", fmt="fhir")
        db.close()
        cls.c = {}
        for n in PW:
            c = TestClient(main.app, base_url="http://testserver")
            assert c.post("/api/login", json={"username": n, "password": PW[n]}, headers=H).status_code == 200
            cls.c[n] = c

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        shutil.rmtree(TMP, True)

    def put(self, key, value, who="robin"):
        return self.c[who].put(f"/api/profiles/{self.pid}/settings/{key}", json={"value": value}, headers=H)

    def preview(self):
        r = self.c["kim"].post(f"/api/profiles/{self.pid}/summary/preview", headers=H, json={
            "appointment": None, "since": day(-45)[:10], "sections": ["out_of_range", "trends"], "labs": ["Ferritin", "Vitamin D, 25-OH"]})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_1_settings(self):
        secs = self.c["robin"].get(f"/api/profiles/{self.pid}/settings/sections").json()["sections"]
        sec = next(s for s in secs if s["id"] == "ranges")
        # the birth date and sex are About this person's since #194 (Sharing; tests/test_person.py), not this section's
        self.assertEqual([s["key"] for s in sec["settings"]], ["ranges.overrides"])
        self.assertIn("range from settings", sec["help"])
        self.assertIn("About this person", sec["settings"][0]["help"])
        self.assertEqual(self.put("ranges.overrides", [row(low=300)]).status_code, 400)
        self.assertIn("low is above the high", self.put("ranges.overrides", [row(low=300)]).json()["detail"])
        self.assertEqual(self.put("ranges.birthDate", "1980-06-15").status_code, 404, "the old name is gone")
        person = f"/api/profiles/{self.pid}/person"
        self.assertEqual(self.c["robin"].put(person, json={"birthDate": "1980-02-30"}, headers=H).status_code, 400)
        self.assertEqual(self.c["kim"].put(person, json={"sex": "female"}, headers=H).status_code, 403, "a viewer can't change them")
        for body, who in [({"birthDate": "1980-06-15"}, "robin"), ({"sex": "female"}, "lee")]:
            r = self.c[who].put(person, json=body, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
        r = self.put("ranges.overrides", [row(), row(lab="Vitamin D, 25-OH", low=30, high=100)], "lee")
        self.assertEqual(r.status_code, 200, r.text)
        merged = self.c["kim"].get(f"/api/profiles/{self.pid}/settings").json()
        self.assertEqual(ranges.config(merged)["birthDate"], "1980-06-15")
        self.assertEqual((merged["person"]["birthDate"], merged["person"]["sex"], len(merged["ranges"]["overrides"])), ("1980-06-15", "female", 2))
        db = dbm.connect()
        try:
            rows = db.execute("SELECT key, value, sealed FROM settings WHERE key LIKE 'ranges.%' OR key LIKE 'person.%'").fetchall()
        finally:
            db.close()
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["value"] is None and r["sealed"] for r in rows), "encrypted, like other health settings")

    def test_2_summary(self):
        s = self.preview()
        oor = {r["name"]: r for r in s["out_of_range"]}
        fe = oor["Ferritin"]   # 230 high and 15 low by the range from settings; 180 in it; 260 in its own 30-400
        self.assertEqual((fe["value"], fe["flag"], fe["range"], fe["out"], fe["of"]), ("15", "Low", "20–200 (settings)", 2, 4))
        vd = oor["Vitamin D, 25-OH"]   # 12 low by the range from settings; 18 flagged L by its own lab, with no range
        self.assertEqual((vd["value"], vd["flag"], vd["range"], vd["out"]), ("18", "Low", "", 2))
        # the ferritin in ug/L a row of its own after the one in ng/mL (the latest result's), not left out (#781)
        self.assertEqual([(t["name"], t["unit"]) for t in s["trends"]][:2], [("Ferritin", "ng/mL"), ("Ferritin", "ug/L")])
        self.assertEqual((s["trends"][1]["then"]["value"], s["trends"][1]["now"]), ("900", None))
        tr = {t["name"]: t for t in s["trends"][::-1]}   # (a name's first row: Ferritin's in ng/mL)
        self.assertEqual([x[2:6] for x in tr["Ferritin"]["spark"]], [["High", 20, 200, "settings"], [None, 20, 200, "settings"],
                                                                    [None, 30, 400, None], ["Low", 20, 200, "settings"]],
                         "each with its flag and its own range, from settings where its report gives none, and said so (#299, #649)")
        self.assertEqual(tr["Ferritin"]["now"]["range"], "20–200 (settings)")
        self.assertIn("range from settings", s["settings_note"])
        self.assertIn("a range set in Settings", s["notice"])
        r = self.c["kim"].post(f"/api/profiles/{self.pid}/summary/pdf", headers=H, json={
            "appointment": None, "since": day(-45)[:10], "sections": ["out_of_range", "trends"], "labs": ["Ferritin"]})
        self.assertEqual(r.status_code, 200, r.text[:200])
        text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages)
        self.assertIn("20–200 (settings)", text)
        self.assertIn("a range from settings, set by you", text)
        # a ferritin in ug/L (900) gets no range: it isn't the unit the range was set in, and nothing is converted
        pts = summary._series({"labs": [DATA["labs"][1]]}, {"overrides": [row()]})["Ferritin"]["pts"]
        self.assertEqual((pts[0]["lo"], pts[0]["hi"], pts[0]["rs"]), (None, None, "report"))

    def test_2_summary_charts(self):
        """#649: Vitamin D 22, 26, 34 and 41 typed with no range, a range from settings of 30–100: the trends' chart, its
        sparkline, the table and the PDF agree. The band is said to be from settings (the chart's end, its note), and
        the same results are flagged in all: 22 and 26 Low (22 the table's Then), 34 and 41 not (41 its Now)."""
        rows = [row(), row(lab="Vitamin D, 25-OH", low=30, high=100), row(lab="Vitamin D", low=30, high=100)]
        self.assertEqual(self.put("ranges.overrides", rows).status_code, 200)
        body = {"appointment": None, "since": day(-45)[:10], "sections": ["trends"], "labs": ["Vitamin D"]}
        r = self.c["kim"].post(f"/api/profiles/{self.pid}/summary/preview", headers=H, json=body)
        self.assertEqual(r.status_code, 200, r.text)
        s = r.json()
        vd = next(t for t in s["trends"] if t["name"] == "Vitamin D")
        self.assertEqual([p[1:6] for p in vd["spark"]],
                         [[22, "Low", 30, 100, "settings"], [26, "Low", 30, 100, "settings"], [34, None, 30, 100, "settings"],
                          [41, None, 30, 100, "settings"]], "the chart's and sparkline's points: each flag, and its range from settings")
        self.assertEqual((vd["then"], vd["now"]["flag"], vd["now"]["range"]),
                         ({"value": "22", "date": day(-70)[:10], "flag": "Low"}, None, "30–100 (settings)"),
                         "the table's Then flagged as its chart is, against the row's range from settings (so its cell needn't "
                         "say it, #752), Now in range, its range from settings")
        self.assertNotIn("own", vd["now"], "Now: its range is the row's")
        self.assertIn("range from settings", s["settings_note"])
        self.assertTrue(summary.uses_settings({**vd, "now": {**vd["now"], "range": "30–100"}}), "its chart's band alone is enough for the note")
        r = self.c["kim"].post(f"/api/profiles/{self.pid}/summary/pdf", headers=H, json=body)
        self.assertEqual(r.status_code, 200, r.text[:200])
        text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages)
        self.assertIn(f"22 Low  {summary._short(day(-70)[:10])}", text, "Then, flagged in words")
        self.assertIn(f"41  {summary._short(day(-5)[:10])}", text, "Now, in range")
        self.assertIn("30–100 from settings", text, "the chart's band, said to be from settings at its end")
        self.assertIn("or, for a result whose report gives none, the range from settings", " ".join(text.split()), "the charts' note")
        self.assertIn("a range from settings, set by you", text)
        ops = b"".join(zlib.decompress(re.search(rb"stream\n(.*?)\nendstream", o, re.S)[1]) for o in r.content.split(b" obj")
                       if b"/FlateDecode" in o)
        self.assertEqual(ops.count(b" h f"), 2, "two triangles on the chart: 22 and 26, below their range; the sparkline's 41 a dot")

    def test_2_summary_mixed(self):
        """#717: #649's case mixed: Folate 4 typed with no range before the visit, 5–20 from settings, then 6 and 7 with
        their report's 3–17. The Then flagged against the range from settings says so in its cell, beside a row whose only
        range is the report's 3–17 (4 is inside it); the chart's earlier step, from settings, is marked so, not only its
        end; and the PDF's two notes say what each flag is judged against, a range from settings among them."""
        rows = [row(), row(lab="Vitamin D", low=30, high=100), row(lab="Folate", low=5, high=20)]
        self.assertEqual(self.put("ranges.overrides", rows).status_code, 200)
        body = {"appointment": None, "since": day(-45)[:10], "sections": ["out_of_range", "trends"], "labs": ["Folate", "Vitamin D"]}
        r = self.c["kim"].post(f"/api/profiles/{self.pid}/summary/preview", headers=H, json=body)
        self.assertEqual(r.status_code, 200, r.text)
        s = r.json()
        fo = next(t for t in s["trends"] if t["name"] == "Folate")
        self.assertEqual(fo["then"], {"value": "4", "date": day(-70)[:10], "flag": "Low", "own": ["settings range", "5–20"]},
                         "Then flagged against the range from settings, which it carries")
        self.assertEqual((fo["now"], [p[1:6] for p in fo["spark"]]),
                         ({"value": "7", "date": day(-10)[:10], "flag": None, "range": "3–17"},
                          [[4, "Low", 5, 20, "settings"], [6, None, 3, 17, None], [7, None, 3, 17, None]]),
                         "Now in range by its report's 3–17; the chart's points each with its range and its source")
        r = self.c["kim"].post(f"/api/profiles/{self.pid}/summary/pdf", headers=H, json=body)
        self.assertEqual(r.status_code, 200, r.text[:200])
        text = " ".join("\n".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages).split())
        self.assertIn(f"Folate (ng/mL) 4 Low {summary._short(day(-70)[:10])} (settings range 5–20) 7", text, "the Then cell says what it's flagged against")
        self.assertIn("Each is judged against the range printed on its own report, or, where its report gives none, the range from "
                      "settings, its Range marked “(settings)”.", text, "the out-of-range note: Vitamin D's 26 is judged against one")
        self.assertIn("High or Low (in red): outside the range on that result's own report, or, where its report gives none, outside "
                      "the range from settings. A Then judged against a range other than its row's (its Now's) gives it in its cell "
                      "(“report range” or “settings range” and its numbers).", text, "the trends' note")
        # the chart: "from settings" over its first step (4's, from settings), the end the report's
        page, vpage, vd = _Drawn(), _Drawn(), next(t for t in s["trends"] if t["name"] == "Vitamin D")
        token = summary._DATES.set(("", TODAY.year))
        try:
            summary._chart(page, fo, ["Folate (ng/mL)"], 0, 200, 120)
            summary._chart(vpage, vd, ["Vitamin D (ng/mL)"], 0, 200, 120)
        finally:
            summary._DATES.reset(token)
        (a, b), = [(x0, x1) for x0, x1, _, t in page.texts if t.endswith("from settings")]
        (p4, p6, _), = page.lines
        self.assertLess((a + b) / 2, (p4[0] + p6[0]) / 2, "said over the first step, which ends halfway from 4 to 6")
        self.assertEqual([t for *_, t in vpage.texts if "settings" in t], ["30–100 from settings"], "one step, its end said as before (#649)")

    def copies_views(self, pid, token, body, when, seen, once, trends, out, days, both="Potassium"):
        """What the server gives of a profile's copies (tests/ranges_matrix.py's COPIES and UPLOADED): the records page's
        results (seen); the summary's points (once), its trends, out-of-range rows (both: one whose source names each
        import) and PDF (body: what's asked for); a share link's results (token); and each import's lab days (days:
        each row's test as printed and its result's flag, #782). -> the records"""
        c, word = self.c["robin"], M.SAID   # (a ◆ "Flagged", #819: ALT's and AST's "A", #817)
        db = dbm.connect()
        try:
            data = records.assemble(db, pid)
        finally:
            db.close()
        page = {}   # the records page's series (same.js joinSeries: by name and unit), each result by the one rule
        for s in data["labs"]:
            page.setdefault((s["name"], s["unit"]), []).extend(s["pts"])
        edge = lambda p: p[7] if len(p) > 7 and p[7] else ""   # (#780: a one-sided range's edge, pts[7])
        page = {k: [[ranges.judge(p[1], p[2], p[3], p[4], p[5], None, edge(p))[2], p[2], p[3], *filter(None, [edge(p)])]
                    for p in page_once(pts)] for k, pts in page.items()}
        self.assertEqual(page, seen, f"{when}: the records page's results")
        series = list(summary.each(summary._series(data)))
        self.assertEqual({(s["name"], s["unit"]): [[p["flag"], p["lo"], p["hi"], *filter(None, [p["edge"]])] for p in s["pts"]] for s in series}, once,
                         f"{when}: the summary's results: a result twice once, with any range and flag either copy has")
        labels = [data["imports"][k]["label"] for k in sorted(data["imports"], key=int)]
        s = c.post(f"/api/profiles/{pid}/summary/preview", headers=H, json=body).json()
        cell = lambda x: x and [x["value"], x["flag"]]
        got = {(t["name"], t["unit"]): [cell(t["then"]), cell(t["now"]), (t["now"] or {}).get("range", "")] for t in s["trends"]}
        self.assertEqual(list(got.items()), list(trends.items()), f"{when}: the trends, each unit its row, the latest result's first")
        pts = collections.defaultdict(list)   # each day's results, in order (#847: two at one time)
        for x in series:
            for p in x["pts"]:
                pts[(x["name"], x["unit"], p["d"])].append([summary.out_of_range(p), p["lo"], p["hi"]])
        for t in s["trends"]:
            self.assertEqual([q[2:5] for q in t["spark"]], [r for d in dict.fromkeys(q[0] for q in t["spark"]) for r in pts[(t["name"], t["unit"], d)]],
                             f"{when}: {t['name']}'s chart")
        self.assertEqual({(o["name"], o["unit"]): [o["value"], o["flag"], o["range"], o["out"], o["of"]] for o in s["out_of_range"]},
                         out, f"{when}: out of range since then")
        k = next(o for o in s["out_of_range"] if o["name"] == both)
        self.assertEqual(k["source"], "; ".join(dict.fromkeys(labels)), f"{when}: the sources of both copies (each once: #868's typed twice)")
        r = c.post(f"/api/profiles/{pid}/summary/pdf", headers=H, json=body)
        self.assertEqual(r.status_code, 200, r.text[:200])
        text = " ".join("\n".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages).split())
        for (n, u), (v, f, rg, *_) in out.items():
            self.assertIn(f"{v} {u} {f} {rg}", text, f"{when}: the PDF's out-of-range {n}")
        for n, u in trends:
            self.assertIn(f"{M.SHOWN.get(n, n)} ({u})", text, f"{when}: the PDF's trend of {n} in {u}")
        view = TestClient(main.app, base_url="http://testserver").post("/api/share/view", json={"token": token}, headers=H).json()
        self.assertEqual({(x["name"], x["unit"]): [[q["flag"], q["lo"], q["hi"], q["range"], *filter(None, [q.get("edge")])] for q in x["results"][::-1]]
                          for x in view["results"]},
                         {k: [[word.get(f), lo, hi, summary.range_words(lo, hi, *e), *e] for f, lo, hi, *e in v] for k, v in once.items()},
                         f"{when}: a share link's results")
        # each import's lab days (#782): its rows named with their series (records._row_series), each flagged as its
        # result is judged (the drawer's flag, the Timeline's count): the kept copy's with the other's range, flag and
        # edge after Same result (crossmatch._carry), the other's rows folded into it and its lab day left out
        imps = sorted(data["imports"], key=int)
        got = {}
        for e in data["events"]:
            if e["k"] != "lab":
                continue
            rows = []
            for i in data["details"][str(e["id"])]["ids"]:
                d = data["details"][str(i)]
                for r, n in zip(d["rows"], d.get("series") or [None] * len(d["rows"])):
                    p = next((p for s in data["labs"] if s["name"] == n and s.get("imp") == e.get("imp") for p in s["pts"]
                              if p[0][:10] == e["t"][:10] and p[5] == r[1]), None)
                    rows.append((r[0], p and ranges.judge(p[1], p[2], p[3], p[4], p[5], None, edge(p))[2]))
            got[(imps.index(str(e["imp"])) + 1, (TODAY - datetime.date.fromisoformat(e["t"][:10])).days)] = rows
        self.assertEqual(got, days, f"{when}: each import's lab days, their rows' tests and flags")
        return data

    def test_2_summary_copies(self):
        """#781: one result twice (from two imports) and a lab in two units, through the server (tests/ranges_matrix.py's
        COPIES), before and after "Same result". The records page's results, each judged by the one rule (both copies, a
        series for each unit); the summary's points, trends, out-of-range rows and the sources they name, its PDF, and a
        share link: every result, a result twice once with any range and flag either copy has (ranges.copies), a lab in
        two units a row in each (none dropped: the latest's unit alone was kept, and a copy's Low lost). Then each pair
        linked as the same result: everywhere once, the older import's copy kept with the other's range and flag (a
        range in g/L given in g/dL, the kept copy's unit). A one-sided range's edge goes with its range (#780: "<5" kept
        with 5 left out, the copy High), and each import's lab days (#782) name their rows' series, each row's result
        flagged as COPIES_DAYS has it (the drawer's flags, the Timeline's counts), the other's rows folded into the kept
        one's after Same result. A copy's "A" with no range beside its other copy's range (#825 with #817, ALT's first
        and AST's second): placed by it whichever came first, High past it, "Flagged" inside it (#819)."""
        db = dbm.connect()
        try:
            owner = db.execute("SELECT id FROM users WHERE username='robin'").fetchone()["id"]
            pid = prof.create_profile(db, "Copies (fictional)", owner_id=owner)
            for imp, fmt in ((1, "csv"), (2, "fhir")):
                with dbm.transaction(db):
                    records.store(db, pid, M.copies(imp, TODAY), "upload", fmt=fmt)
        finally:
            db.close()
        c = self.c["robin"]
        self.assertEqual(c.put(f"/api/profiles/{pid}/settings/overview.keyLabs", json={"value": M.COPIES_LABS}, headers=H).status_code, 200)
        r = c.post(f"/api/profiles/{pid}/links", json={"sections": ["results"]}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        token = r.json()["path"].split("#", 1)[1]
        body = {"appointment": None, "since": M.day(M.COPIES_SINCE, TODAY)[:10], "sections": ["out_of_range", "trends"], "labs": []}

        data = self.copies_views(pid, token, body, "before", M.COPIES_SEEN, M.COPIES_ONCE, M.COPIES_TRENDS, M.COPIES_OUT, M.COPIES_DAYS)
        self.assertEqual(sorted(x["a"]["name"] for x in data["possible"]), M.COPIES_LINKED)
        for x in data["possible"]:
            self.assertEqual(c.post(f"/api/profiles/{pid}/same", json={"ref": x["ref"]}, headers=H).status_code, 201)
        self.copies_views(pid, token, body, "after Same result", M.COPIES_SAME, M.COPIES_SAME, M.COPIES_TRENDS_SAME, M.COPIES_OUT_SAME,
                          M.COPIES_DAYS_SAME)
        db = dbm.connect()
        try:
            prof.delete_profile(db, pid)
        finally:
            db.close()

    def upload(self, pid, name, text):
        """name and text uploaded to profile pid and imported as a person does (#846): through the import's preview, then
        saved. -> (the job at its preview, the job done)"""
        c = self.c["robin"]
        up = c.post(f"/api/profiles/{pid}/uploads", headers=H).json()["id"]
        self.assertEqual(c.put(f"/api/profiles/{pid}/uploads/{up}/files", params={"path": name}, content=text.encode(), headers=H).status_code, 200)
        self.assertEqual(c.post(f"/api/profiles/{pid}/uploads/{up}/finish", headers=H).status_code, 200)
        r = c.post(f"/api/profiles/{pid}/uploads/{up}/import", json={}, headers=H)
        self.assertEqual(r.status_code, 202, r.text)
        jid, seen = r.json()["id"], None

        def until(states):
            for _ in range(600):
                j = c.get(f"/api/profiles/{pid}/jobs/{jid}").json()
                if j["state"] in states:
                    return j
                time.sleep(0.05)
            self.fail(f"{name}: never {states}: {j}")
        seen = until(("preview", "failed"))
        self.assertEqual(seen["state"], "preview", seen)
        self.assertEqual(c.post(f"/api/profiles/{pid}/jobs/{jid}/confirm", json={}, headers=H).status_code, 202)
        done = until(("done", "failed", "discarded"))
        self.assertEqual(done["state"], "done", done)
        return seen, done

    def test_2_uploaded_copies(self):
        """#846: one result twice through real uploads, in each order (tests/ranges_matrix.py's UPLOADED): a spreadsheet and
        a C-CDA document of the same results at one time, a range or a flag in one file only, each put through the
        import's preview and saved. The second's preview counted each result already here a duplicate by its test, time
        and value, and left its copy out with its range and flag: the spreadsheet first, every view said each unflagged
        and the C-CDA's lab day "Out of range"; the C-CDA first, the spreadsheet's lab day "None". Now its review says how
        many it adds again with its report's range and flag and skips none, and every view gives each as one result
        with them, each file's lab day flagging its rows. #847: two copies whose reports print different ranges (in the
        edge, in the limits) are two results in every view, each with its own range and flag."""
        db = dbm.connect()
        try:
            owner = db.execute("SELECT id FROM users WHERE username='robin'").fetchone()["id"]
        finally:
            db.close()
        c = self.c["robin"]
        for order in M.UPLOAD_ORDERS:
            when = " then ".join(order)
            db = dbm.connect()
            try:
                pid = prof.create_profile(db, f"Uploaded copies, {when} (fictional)", owner_id=owner)
            finally:
                db.close()
            first, _ = self.upload(pid, *M.uploaded(order[0], TODAY))
            second, _ = self.upload(pid, *M.uploaded(order[1], TODAY))
            self.assertEqual({k: first["duplicates"].get(k, 0) for k in M.UPLOADED_HERE}, {"copies": 0, "results": 0}, f"{when}: the first's review")
            self.assertEqual({k: second["duplicates"].get(k, 0) for k in M.UPLOADED_HERE}, M.UPLOADED_HERE,
                             f"{when}: the second's review: its results already here added again with its report's range and flag, none skipped")
            self.assertEqual(second["duplicates"].get("brings"), M.UPLOADED_BRINGS[order[1]], f"{when}: what each copy brings (#868)")
            self.assertEqual([len(first["possible"]), len(second["possible"])], [0, M.UPLOADED_POSSIBLE], f"{when}: possible matches (#847's eGFR)")
            self.assertEqual(c.put(f"/api/profiles/{pid}/settings/overview.keyLabs", json={"value": M.UPLOADED_LABS}, headers=H).status_code, 200)
            r = c.post(f"/api/profiles/{pid}/links", json={"sections": ["results"]}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            body = {"appointment": None, "since": M.day(M.UPLOADED_SINCE, TODAY)[:10], "sections": ["out_of_range", "trends"], "labs": []}
            self.copies_views(pid, r.json()["path"].split("#", 1)[1], body, when, M.UPLOADED_SEEN, M.UPLOADED_SEEN, M.UPLOADED_TRENDS,
                              M.UPLOADED_OUT, M.uploaded_days(order), both="Sodium, Blood")
            db = dbm.connect()
            try:
                prof.delete_profile(db, pid)
            finally:
                db.close()

    def test_2_resent(self):
        """#869: the same record sent again by its source (tests/ranges_matrix.py's RESENT), a FHIR file's results
        uploaded, then uploaded again amended: a range corrected (and a Low taken back), a range given, a flag changed.
        The second added a copy of each, its lab day's rows reading the first's flags beside the new ranges. Now its
        review says it updates them, and every view gives one result each as the second file says, the lab day's rows
        with the ranges it prints now (RESENT_RANGES) beside their results' flags (RESENT_DAYS)."""
        db = dbm.connect()
        try:
            owner = db.execute("SELECT id FROM users WHERE username='robin'").fetchone()["id"]
            pid = prof.create_profile(db, "Sent again (fictional)", owner_id=owner)
        finally:
            db.close()
        c = self.c["robin"]
        self.upload(pid, *M.resent(0, TODAY))
        second, done = self.upload(pid, *M.resent(1, TODAY))
        self.assertEqual({k: second["duplicates"].get(k, 0) for k in M.RESENT_HERE}, M.RESENT_HERE, "the second's review: each updated in place")
        self.assertEqual((second["new"], done["counts"].get("updated")), (True, M.RESENT_HERE["updates"]), "and its save")
        self.assertEqual(c.put(f"/api/profiles/{pid}/settings/overview.keyLabs", json={"value": M.RESENT_LABS}, headers=H).status_code, 200)
        link = c.post(f"/api/profiles/{pid}/links", json={"sections": ["results"]}, headers=H)
        self.assertEqual(link.status_code, 200, link.text)
        body = {"appointment": None, "since": M.day(M.RESENT_SINCE, TODAY)[:10], "sections": ["out_of_range", "trends"], "labs": []}
        data = self.copies_views(pid, link.json()["path"].split("#", 1)[1], body, "sent again", M.RESENT_SEEN, M.RESENT_SEEN, M.RESENT_TRENDS,
                                 M.RESENT_OUT, M.RESENT_DAYS, both="Potassium, Blood")
        rows = [r for e in data["events"] if e["k"] == "lab" for i in data["details"][str(e["id"])]["ids"] for r in data["details"][str(i)]["rows"]]
        self.assertEqual([r[3] for r in rows], M.RESENT_RANGES, "each row's range as its source prints it now")
        db = dbm.connect()
        try:
            prof.delete_profile(db, pid)
        finally:
            db.close()

    def test_2_typed_copies(self):
        """#868: a result already here typed in again (tests/ranges_matrix.py's TYPED), Type them in's rule an upload's:
        kept as a copy only with its own row, and said by what it brings. The second save's rows are duplicates by key
        (each keyed by its test, time and value), so their labitems were left out while Potassium's and CRP's results,
        bringing a range, were kept: "won't be saved again", "Saved nothing new", and copies with no row of their own.
        Now each row says what it brings (TYPED_SAID), the save counts the two it adds and the one it leaves out, and
        every view gives each result as TYPED_SEEN, each typed import's lab day its own rows (TYPED_DAYS)."""
        db = dbm.connect()
        try:
            owner = db.execute("SELECT id FROM users WHERE username='robin'").fetchone()["id"]
            pid = prof.create_profile(db, "Typed again (fictional)", owner_id=owner)
        finally:
            db.close()
        c = self.c["robin"]
        first = c.post(f"/api/profiles/{pid}/manual", json={"rows": M.typed(0, TODAY)[0], "save": True}, headers=H)
        self.assertEqual(first.status_code, 200, first.text)
        rows = M.typed(1, TODAY)[0]
        j = c.post(f"/api/profiles/{pid}/manual", json={"rows": rows}, headers=H).json()
        self.assertEqual([x["copy"] for x in j["rows"]], M.TYPED_SAID, "what each row brings")
        self.assertEqual([x["duplicate"] for x in j["rows"]], [x is None for x in M.TYPED_SAID], "and which are duplicates")
        r = c.post(f"/api/profiles/{pid}/manual", json={"rows": rows, "save": True}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        saved = r.json()
        self.assertEqual((saved["counts"]["typed"], saved["skipped"], saved["counts"]["results"]), ({"labs": 2}, 1, 2), "the save counts its copies")
        self.assertIsNotNone(saved["import_id"])
        self.assertEqual(c.put(f"/api/profiles/{pid}/settings/overview.keyLabs", json={"value": M.TYPED_LABS}, headers=H).status_code, 200)
        link = c.post(f"/api/profiles/{pid}/links", json={"sections": ["results"]}, headers=H)
        self.assertEqual(link.status_code, 200, link.text)
        body = {"appointment": None, "since": M.day(M.TYPED_SINCE, TODAY)[:10], "sections": ["out_of_range", "trends"], "labs": []}
        self.copies_views(pid, link.json()["path"].split("#", 1)[1], body, "typed again", M.TYPED_SEEN, M.TYPED_SEEN, M.TYPED_TRENDS,
                          M.TYPED_OUT, M.TYPED_DAYS, both="Potassium, Blood")
        db = dbm.connect()
        try:
            prof.delete_profile(db, pid)
        finally:
            db.close()

    def test_2_summary_latest_copies(self):
        """#825: one result twice at one time, its latest, a range on one copy only, in each import order (tests/
        ranges_matrix.py's LATEST), through the server: each copy is given the one result's range and flag
        (crossmatch.copies), so the records page shows it once and flagged (Labs and the Overview read it from there),
        each import's lab day flags its row (the drawer, the Timeline's badge), and the summary says it High."""
        db = dbm.connect()
        try:
            owner = db.execute("SELECT id FROM users WHERE username='robin'").fetchone()["id"]
            pid = prof.create_profile(db, "Latest copies (fictional)", owner_id=owner)
            for imp, fmt in ((1, "csv"), (2, "fhir")):
                with dbm.transaction(db):
                    records.store(db, pid, M.copies(imp, TODAY, M.LATEST), "upload", fmt=fmt)
            data = records.assemble(db, pid)
        finally:
            db.close()
        flag = lambda p: ranges.judge(p[1], *p[2:6], None, (p + [None] * 8)[7] or "")[2]
        page = {}
        for s in data["labs"]:
            page.setdefault(s["name"], []).extend(s["pts"])
        self.assertEqual({n: [[flag(p), p[2], p[3]] for p in page_once(pts)] for n, pts in page.items()}, M.LATEST_JUDGED,
                         "the records page's results: the latest once, flagged")
        latest = M.day(2, TODAY)
        self.assertEqual(sorted((s["name"], s["imp"], flag(p)) for s in data["labs"] for p in s["pts"] if p[0] == latest),
                         sorted((n, i, "H") for n in M.LATEST_LABS for i in {s["imp"] for s in data["labs"]}), "each copy flagged")
        imps = sorted(data["imports"], key=int)
        self.assertEqual(sorted((s["name"], imps.index(str(s["imp"])) + 1, s["pts"][int(n)][0] == latest, imps.index(str(f)) + 1)
                                for s in data["labs"] for n, f in (s.get("rangeFrom") or {}).items()),
                         [("Potassium", 2, True, 1), ("Sodium", 1, True, 2)],
                         "#834: the copy whose report gave no range says whose copy's did (Potassium's the first import's, Sodium's the second's)")
        got = {}
        for e in data["events"]:
            if e["k"] == "lab" and e["t"] == latest:
                rows = []
                for i in data["details"][str(e["id"])]["ids"]:
                    d = data["details"][str(i)]
                    for r, n in zip(d["rows"], d.get("series") or [None] * len(d["rows"])):
                        p = next((p for s in data["labs"] if s["name"] == n and s.get("imp") == e.get("imp") for p in s["pts"]
                                  if p[0][:10] == e["t"][:10] and p[5] == r[1]), None)
                        rows.append((n, p and flag(p)))
                got[(imps.index(str(e["imp"])) + 1, 2)] = rows
        self.assertEqual(got, M.LATEST_DAYS, "each import's lab day of the latest: its rows' series, each flagged")
        c = self.c["robin"]
        self.assertEqual(c.put(f"/api/profiles/{pid}/settings/overview.keyLabs", json={"value": M.LATEST_LABS}, headers=H).status_code, 200)
        s = c.post(f"/api/profiles/{pid}/summary/preview", headers=H, json={"appointment": None, "since": M.day(15, TODAY)[:10],
                                                                         "sections": ["out_of_range", "trends"], "labs": []}).json()
        self.assertEqual({o["name"]: [o["value"], o["flag"], o["range"]] for o in s["out_of_range"]},
                         {n: [v.split(" ")[0], "High", r.removeprefix("ref ")] for n, (v, r) in M.LATEST_OUT.items()}, "the summary's out of range")
        self.assertEqual({t["name"]: [t["then"]["value"], t["now"]["value"], t["now"]["flag"]] for t in s["trends"]},
                         {"Potassium": ["4.4", "5.2", "High"], "Sodium": ["141", "150", "High"]}, "the summary's trends: Then the result before")
        db = dbm.connect()
        try:
            prof.delete_profile(db, pid)
        finally:
            db.close()

    def test_2_summary_not_same(self):
        """#833: LATEST's latest pairs marked "Not the same" (tests/ranges_matrix.py's NOT_SAME_*): two results, each with
        its own range and flag, everywhere. The records page shows both (crossmatch.copies keeps them apart and says so,
        the series' "apart"), each import's lab day flags its own row, and the summary and a share link give both, the
        out-of-range row "1 of 2" and a trend's Now the flagged one. Undone, they're one result again."""
        db = dbm.connect()
        try:
            owner = db.execute("SELECT id FROM users WHERE username='robin'").fetchone()["id"]
            pid = prof.create_profile(db, "Not the same (fictional)", owner_id=owner)
            for imp, fmt in ((1, "csv"), (2, "fhir")):
                with dbm.transaction(db):
                    records.store(db, pid, M.copies(imp, TODAY, M.LATEST), "upload", fmt=fmt)
        finally:
            db.close()
        c = self.c["robin"]

        def data():
            db = dbm.connect()
            try:
                return records.assemble(db, pid)
            finally:
                db.close()

        possible = data()["possible"]
        self.assertEqual(sorted(x["a"]["name"] for x in possible), M.LATEST_LABS)
        ids = [c.post(f"/api/profiles/{pid}/same", json={"ref": x["ref"], "same": False}, headers=H).json()["id"] for x in possible]
        d = data()
        flag = lambda p: ranges.judge(p[1], *p[2:6], None, (p + [None] * 8)[7] or "")[2]
        page, apart = {}, {}
        for s in d["labs"]:
            page.setdefault(s["name"], []).extend(s["pts"])
            apart.setdefault(s["name"], []).extend((s.get("apart") or {}).get(str(n)) for n in range(len(s["pts"])))
        self.assertEqual([s["rangeFrom"] for s in d["labs"] if s.get("rangeFrom")], [], "#834: no range from the other, so none said")
        self.assertEqual({n: [[flag(p), p[2], p[3]] for p in page_once(pts, apart[n])] for n, pts in page.items()}, M.NOT_SAME_JUDGED,
                         "the records page's results: both, each its own range and flag")
        imps, latest, got = sorted(d["imports"], key=int), M.day(2, TODAY), {}
        for e in d["events"]:
            if e["k"] == "lab" and e["t"] == latest:
                rows = []
                for i in d["details"][str(e["id"])]["ids"]:
                    det = d["details"][str(i)]
                    for r, n in zip(det["rows"], det.get("series") or [None] * len(det["rows"])):
                        p = next((p for s in d["labs"] if s["name"] == n and s.get("imp") == e.get("imp") for p in s["pts"]
                                  if p[0][:10] == e["t"][:10] and p[5] == r[1]), None)
                        rows.append((n, p and flag(p)))
                got[(imps.index(str(e["imp"])) + 1, 2)] = rows
        self.assertEqual(got, M.NOT_SAME_DAYS, "each import's lab day of the latest: its own row's flag")
        self.assertEqual(c.put(f"/api/profiles/{pid}/settings/overview.keyLabs", json={"value": M.LATEST_LABS}, headers=H).status_code, 200)
        body = {"appointment": None, "since": M.day(M.NOT_SAME_SINCE, TODAY)[:10], "sections": ["out_of_range", "trends"], "labs": []}
        s = c.post(f"/api/profiles/{pid}/summary/preview", headers=H, json=body).json()
        self.assertEqual({o["name"]: [o["value"], o["flag"], o["range"], o["out"], o["of"]] for o in s["out_of_range"]}, M.NOT_SAME_OUT,
                         "the summary's out of range: one of the two")
        self.assertEqual({t["name"]: [t["then"]["value"], t["now"]["value"], t["now"]["flag"]] for t in s["trends"]}, M.NOT_SAME_TRENDS,
                         "the summary's trends: Now the flagged one")
        r = c.post(f"/api/profiles/{pid}/links", json={"sections": ["results"]}, headers=H)
        view = TestClient(main.app, base_url="http://testserver").post("/api/share/view", json={"token": r.json()["path"].split("#", 1)[1]},
                                                                       headers=H).json()
        self.assertEqual({x["name"]: [len(x["results"]), sorted(str(q["flag"]) for q in x["results"] if q["t"] == latest)] for x in view["results"]},
                         {n: [21, ["High", "None"]] for n in M.LATEST_LABS}, "a share link: both")
        for i in ids:   # undone: one result again, flagged
            self.assertEqual(c.delete(f"/api/profiles/{pid}/same/{i}", headers=H).status_code, 200)
        d = data()
        self.assertEqual({n: [[flag(p), p[2], p[3]] for p in page_once([p for s in d["labs"] if s["name"] == n for p in s["pts"]])]
                          for n in M.LATEST_LABS}, M.LATEST_JUDGED, "undone: one result again")
        db = dbm.connect()
        try:
            prof.delete_profile(db, pid)
        finally:
            db.close()

    def test_2_one_sided_ranges(self):
        """#780: a one-sided range keeps whether its limit includes its edge, from each importer that reads one through
        storage and the FHIR export, and the one rule honours it. The edge as printed and the rule on their own (5
        against "<5" is high, against "<=5" neither; 60 against ">60" low; ">=5" against "<5" high); then the matrix's
        one-sided cases (ranges_matrix PRINTED: "<5" then "<=5", ">60" then ">=60", at and past the edge, each spelled as
        reports print it, "less than 5", "=>60", "< OR = 5", #850) as a CSV, as Quest-like PDF reports ("< OR = 5"), as a
        FHIR bundle (a source's own high beside its printed "<5", a printed range alone) and as a C-CDA document (each
        range its text alone, no IVL, #827: it was dropped): each importer's
        points have the matrix's range and edge, each judged as JUDGED; and the FHIR
        export says a limit that leaves its edge out in its text alone (FHIR's low and high include theirs), which the
        FHIR importer reads back as it was, with each report's own flag (#817: a report's "A" inside its range came back
        with none). A value given as a bound (">=5", "<=60") is one in a FHIR bundle too, its Quantity's comparator, and
        read back (#870: the FHIR importer kept it as text, so neither had its number, range or flag)."""
        import types, zipfile
        from server import fhir_export
        from server.importers import ccda, common, csv_labs, fhir, pdf_labs
        self.assertEqual([common.range_edge(t, lo, hi) for t, lo, hi in [
            ("<5", None, 5), ("< 5 mg/L", None, 5), ("less than 5", None, 5), ("under 5", None, 5), ("<=5", None, 5),
            ("≤5", None, 5), ("=<5", None, 5), ("< OR = 5", None, 5), ("less than or equal to 5", None, 5), ("up to 5", None, 5),
            (">60", 60, None), ("> 60", 60, None), ("greater than 60", 60, None), (">=60", 60, None), ("≥ 60", 60, None),
            ("> OR = 60", 60, None), ("<5", None, 6), ("<5", 1, 5), ("3.5-5.1", 3.5, 5.1), ("<5,5", None, 5.5), ("", None, 5)]],
            ["<", "<", "<", "<", "", "", "", "", "", "", ">", ">", ">", "", "", "", "", "", "", "<", ""])
        self.assertEqual([ranges.flag(v, None, 5, ranges.comparator(t), e) for v, t, e in [
            (5, "5", "<"), (5, "5", ""), (4.9, "4.9", "<"), (5, ">=5", "<"), (5, ">=5", ""), (5, ">5", ""), (5, "<5", "<"), (5, "<=5", "<")]],
            ["H", "", "", "H", "", "H", "", ""])
        self.assertEqual([ranges.flag(v, 60, None, ranges.comparator(t), e) for v, t, e in [
            (60, "60", ">"), (60, "60", ""), (60.1, "60.1", ">"), (60, "<=60", ">"), (60, "<=60", ""), (60, "<60", ""), (60, ">60", ">")]],
            ["L", "", "", "L", "", "L", ""])

        rows, tmp = M.printed(TODAY), tempfile.mkdtemp(dir=TMP)
        edge = lambda p: p[7] if len(p) > 7 else ""

        def same(data, how):
            """each one-sided lab's points as the matrix has them: value, range, value as given and edge, the flag
            stored and as judged"""
            by = {s["name"]: s["pts"] for s in data["labs"]}
            for n in M.PRINTED:
                want = [[v, lo, hi, t, e, f, f] for (_, v, lo, hi, _, t), e, (f, *_) in zip(M.CASES[n], M.EDGES[n], M.JUDGED[n])]
                got = [[p[1], p[2], p[3], p[5], edge(p), ranges.judge(p[1], p[2], p[3], p[4], p[5], None, edge(p))[2], p[4]] for p in by.get(n, [])]
                self.assertEqual(got, want, f"{how}: {n}")

        with open(os.path.join(tmp, "one-sided.csv"), "w", encoding="utf-8") as f:
            f.write("Date,Time,Test,Value,Unit,Reference range\n" + "".join(f"{t[:10]},{t[11:16]},{n},{v},{u},{r}\n" for n, u, t, v, r, _ in rows))
        same(csv_labs.parse({"one-sided.csv": os.path.join(tmp, "one-sided.csv")})["data"], "a CSV")

        gen = types.ModuleType("pdf_fixture_generator")
        gen.__file__ = os.path.join(ROOT, "tests", "fixtures", "importers", "pdf-labs", "generate.py")
        with open(gen.__file__, encoding="utf-8") as f:
            exec(compile(f.read(), gen.__file__, "exec"), gen.__dict__)
        pdfs = {}   # (a range in words as Quest prints it, its column too narrow for "greater than or equal to 60 mL/min/1.73m2")
        quest = lambda r, n, j: (r if not re.search("[a-z]", r) else M.printed_range(*M.CASES[n][j][2:4], M.EDGES[n][j]))   # noqa: E731
        for t in sorted({r[2] for r in rows}):   # a report a day, Quest's way of printing "<=5": "< OR = 5"
            pdfs[f"quest-{t[:10]}.pdf"] = path = os.path.join(tmp, f"quest-{t[:10]}.pdf")
            gen.write(path, gen.quest([("MATRIX PANEL (FICTIONAL)", None)] + [(n, v, "", quest(r, n, j).replace("<=", "< OR = ").replace(">=", "> OR = "), u)
                                                                             for n, u, at, v, r, j in rows if at == t],
                                      collected=f"{t[5:7]}/{t[8:10]}/{t[:4]} / 08:00 EST"))
        same(pdf_labs.parse(pdfs)["data"], "PDF reports")   # (none printed: the range's, by the one rule, #817)

        def obs(i, n, u, t, v, r, j):
            c, (lo, hi) = ranges.comparator(v), M.CASES[n][j][2:4]
            q = {"value": float(v[len(c):]) if "." in v else int(v[len(c):]), "unit": u, **({"comparator": c} if c else {})}
            rr = {"text": r} if not M.EDGES[n][j] else {("high" if hi is not None else "low"): {"value": lo if hi is None else hi}, "text": r}   # a source's own limit beside "<5"
            return {"resource": {"resourceType": "Observation", "id": f"m{i}", "status": "final", "category": [{"coding": [{"code": "laboratory"}]}],
                                 "code": {"text": n}, "effectiveDateTime": t, "valueQuantity": q, "referenceRange": [rr]}}
        with open(os.path.join(tmp, "one-sided.json"), "w", encoding="utf-8") as f:
            json.dump({"resourceType": "Bundle", "type": "collection", "entry": [obs(i, *r) for i, r in enumerate(rows)]}, f)
        same(fhir.parse({"one-sided.json": os.path.join(tmp, "one-sided.json")})["data"], "a FHIR bundle")
        with open(os.path.join(tmp, "one-sided.xml"), "w", encoding="utf-8") as f:
            f.write(M.ccda([r[:5] for r in rows]))
        same(ccda.parse({"one-sided.xml": os.path.join(tmp, "one-sided.xml")})["data"], "a C-CDA document, its ranges as text (#827)")

        db = dbm.connect()
        try:   # the FHIR export of the matrix's records, and read back
            owner = db.execute("SELECT id FROM users WHERE username='robin'").fetchone()["id"]
            pid = prof.create_profile(db, "One-sided (fictional)", owner_id=owner)
            with dbm.transaction(db):
                records.store(db, pid, M.records(TODAY), "upload", fmt="csv")
            blob = b"".join(fhir_export.stream(fhir_export.plan(db, pid, {"labs"}), "robin", zone="America/New_York"))
            prof.delete_profile(db, pid)
        finally:
            db.close()
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            z.extract("bundle.json", tmp)
            bundle = json.loads(z.read("bundle.json"))
        said = {}
        for e in bundle["entry"]:
            r = e["resource"]
            if r["resourceType"] == "Observation" and r["code"].get("text") in M.PRINTED:
                rr = (r.get("referenceRange") or [{}])[0]
                said.setdefault(r["code"]["text"], []).append((r["effectiveDateTime"][:10], rr.get("low", {}).get("value"), rr.get("high", {}).get("value"), rr.get("text")))
        for n in M.PRINTED:   # "<5" as its text alone, "<=5" as a high of 5, with its lab day's row's text as printed (#782)
            want = [(None, None, r) if e else (lo, hi, r) for (_, _, lo, hi, *_), e, r in zip(M.CASES[n], M.EDGES[n], M.PRINTED[n])]
            self.assertEqual([x[1:] for x in sorted(said.get(n, []))], want, f"the FHIR export's {n}")
        back = fhir.parse({"bundle.json": os.path.join(tmp, "bundle.json")})["data"]
        same(back, "the FHIR export, read back")
        flags = {s["name"]: [p[4] for p in s["pts"]] for s in back["labs"]}
        for n in ("Chloride, Blood", "ALT"):   # a report's own flag with them, "A" too (#817: it came back with none)
            self.assertEqual(flags.get(n), [f for f, *_ in M.JUDGED[n]], f"the FHIR export's {n}, read back")
        # #839: flags stored as printed ("N", ">", "HH", "Normal") go out as printed and come back as every view reads them
        # (a range from Settings stays out of the export, as out of a share link)
        self.assertEqual(flags.get("Calcium"), [f for f, *_ in M.share_judged("Calcium")], "the FHIR export's Calcium, read back")

    def test_2_text_ranges(self):
        """#827, #836: a range given as text alone, no low and high of a source's own (ranges_matrix RANGE_TEXT: AST's
        "10 - 40", "<40", "<=40", "≤ 40", "<40 U/L", "less than 40"), through each importer that reads one: a C-CDA
        document (as records() reads AST), a CareSpace export and a MyChart export. Each point has the matrix's range and
        edge, its flag stored and judged as JUDGED (CareSpace and MyChart dropped such a range: no range, no flag)."""
        from server.importers import carespace, ccda, epic_mychart
        tmp, n = tempfile.mkdtemp(dir=TMP), "AST"
        rows = M.text_ranged(n, TODAY)
        with open(os.path.join(tmp, "ranges.xml"), "w", encoding="utf-8") as f:
            f.write(M.ccda(rows))
        want = [[v, lo, hi, t, e, f, f] for (_, v, lo, hi, _, t), e, (f, *_) in zip(M.CASES[n], M.EDGES[n], M.JUDGED[n])]
        for how, data in (("a C-CDA document (#827)", ccda.parse({"ranges.xml": os.path.join(tmp, "ranges.xml")})["data"]),
                          ("a CareSpace export", carespace.parse(M.carespace(rows, os.path.join(tmp, "carespace")))["data"]),
                          ("a MyChart export", epic_mychart.parse(M.epic(rows, os.path.join(tmp, "mychart")))["data"])):
            pts = next((s["pts"] for s in data["labs"] if s["name"] == n), [])
            got = [[p[1], p[2], p[3], p[5], p[7] if len(p) > 7 else "", p[4], ranges.judge(p[1], p[2], p[3], p[4], p[5], None, p[7] if len(p) > 7 else "")[2]]
                   for p in pts]
            self.assertEqual(got, want, f"{how}: {n}")

    def _read_back(self, data, name="Read back"):
        """data stored in a profile of its own, as an import stores it, and its FHIR export read back by the FHIR
        importer: (the data read back, the bundle)."""
        import zipfile
        from server import fhir_export
        from server.importers import fhir
        db = dbm.connect()
        try:
            owner = db.execute("SELECT id FROM users WHERE username='robin'").fetchone()["id"]
            pid = prof.create_profile(db, f"{name} (fictional)", owner_id=owner)
            with dbm.transaction(db):
                records.store(db, pid, data, "upload", fmt="csv")
            blob = b"".join(fhir_export.stream(fhir_export.plan(db, pid, {"labs"}), "robin", zone="America/New_York"))
            prof.delete_profile(db, pid)
        finally:
            db.close()
        path = os.path.join(tempfile.mkdtemp(dir=TMP), "bundle.json")
        with zipfile.ZipFile(io.BytesIO(blob)) as z, open(path, "wb") as f:
            f.write(z.read("bundle.json"))
        with open(path, encoding="utf-8") as f:
            bundle = json.load(f)
        return fhir.parse({"bundle.json": path})["data"], bundle

    def _every_importer(self, rows, tmp):
        """rows [(test, unit, time, value as given, range as printed)] through every importer that reads lab results, as
        an upload is, and the FHIR export: [(how, its data, the rows' indexes it was given or None for all, the bundle
        or None)]. A spreadsheet (comma-delimited, and with semicolons, its values' decimal points commas), Quest-like
        PDF reports (the ASCII rows with a number in their range, each range its whole cell: Quest's font has no "≤" or
        "–"), a C-CDA document, a FHIR bundle (a bound as its Quantity's comparator), a CareSpace export and a MyChart
        export, each range its text alone; and the FHIR export read back, of the spreadsheet's import (each result's
        range as read) and of lab days whose rows have no result of their own (the export reading each row)."""
        import csv, types
        from server.importers import carespace, ccda, csv_labs, epic_mychart, fhir, pdf_labs
        out = []

        def sheet(name, delim):
            path = os.path.join(tmp, name)
            with open(path, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f, delimiter=delim)
                w.writerow(["Date", "Time", "Test", "Value", "Unit", "Reference range"])
                w.writerows([t[:10], t[11:16], n, v.replace(".", ",") if delim == ";" else v, u, r] for n, u, t, v, r in rows)
            return csv_labs.parse({name: path})["data"]
        by_csv = sheet("rows.csv", ",")
        out += [("a spreadsheet", by_csv, None, None),
                ("a spreadsheet with semicolons and decimal commas", sheet("rows-eu.csv", ";"), None, None)]

        gen = types.ModuleType("pdf_fixture_generator")
        gen.__file__ = os.path.join(ROOT, "tests", "fixtures", "importers", "pdf-labs", "generate.py")
        with open(gen.__file__, encoding="utf-8") as f:
            exec(compile(f.read(), gen.__file__, "exec"), gen.__dict__)
        printable, pdfs = [i for i, (_, _, _, v, rng) in enumerate(rows) if v.isascii() and rng.isascii() and re.search(r"\d", rng)], {}
        for i in printable:   # a report a day, its range and unit one cell, the range read as a whole cell
            n, u, t, v, rng = rows[i]
            pdfs[f"quest-{t[:10]}.pdf"] = path = os.path.join(tmp, f"quest-{t[:10]}.pdf")
            gen.write(path, gen.quest([("MATRIX (FICTIONAL)", None), (n, v, "", rng, "" if rng.endswith(u) else u)],
                                      collected=f"{t[5:7]}/{t[8:10]}/{t[:4]} / 08:00 EST"))
        out.append(("PDF reports", pdf_labs.parse(pdfs)["data"], printable, None))

        with open(os.path.join(tmp, "rows.xml"), "w", encoding="utf-8") as f:
            f.write(M.ccda(rows))
        out.append(("a C-CDA document", ccda.parse({"rows.xml": os.path.join(tmp, "rows.xml")})["data"], None, None))
        with open(os.path.join(tmp, "rows.json"), "w", encoding="utf-8") as f:
            f.write(M.fhir(rows))
        out.append(("a FHIR bundle", fhir.parse({"rows.json": os.path.join(tmp, "rows.json")})["data"], None, None))
        out.append(("a CareSpace export", carespace.parse(M.carespace(rows, os.path.join(tmp, "carespace")))["data"], None, None))
        out.append(("a MyChart export", epic_mychart.parse(M.epic(rows, os.path.join(tmp, "mychart")))["data"], None, None))

        rowed = {"built": M.day(0, TODAY)[:10], "source": "fictional", "costs": [], "upcoming": [], "labs": [],
                 "events": [{"id": i + 1, "t": t, "k": "labitem", "title": "Matrix (fictional)"} for i, (_, _, t, _, _) in enumerate(rows)],
                 "details": {str(i + 1): {"type": "result", "order": "Matrix (fictional)", "rows": [[n, v, u, rng, ""]]}
                             for i, (n, u, _, v, rng) in enumerate(rows)}}
        for how, data in (("a spreadsheet's import", by_csv), ("rows alone", rowed)):
            back, bundle = self._read_back(data)
            out.append((f"the FHIR export of {how}, read back", back, None, bundle))
        return out

    def test_2_range_spellings(self):
        """#850: one parser of a range as text (common.range_text) for every importer, Type them in and the FHIR export.
        The ways a report prints a range (ranges_matrix SPELLINGS: "=>60", "< OR = 5", "less than or equal to 5",
        "greater than or equal to 60", "5,0 - 10,0" with decimal commas, "3.5-5.1", "<5", "under 5", #870's "5.0E3-1.0E4"
        and "<1.0E4" ...), each beside a value at or past its limit, give the same value, low, high, edge and flag
        through every importer (_every_importer: spreadsheets, Quest-like PDF reports, C-CDA, FHIR, CareSpace, MyChart and
        the FHIR export read back) and Type them in's check. The CSV importer and Type them in read "=>60" as a high of 60
        and "< OR = 5", "> OR = 60" and the words not at all; the C-CDA, FHIR, CareSpace and MyChart importers no decimal
        comma; a PDF report no words; the export "≤ 5", "< OR = 5" and "=>60" as text alone; and every one "<1.0E4" as a
        high of 1, "5.0E3-1.0E4" as none (#870)."""
        rows, tmp, (lab, unit) = M.spelled(TODAY), tempfile.mkdtemp(dir=TMP), M.SPELLED_AS
        want = [[float(v), lo, hi, e, f] for _, v, lo, hi, e, f in M.SPELLINGS]
        for how, data, only, bundle in self._every_importer(rows, tmp):
            pts = next((s["pts"] for s in data["labs"] if s["name"] == lab), [])   # [value, low, high, edge, flag]
            self.assertEqual([[p[1], p[2], p[3], p[7] if len(p) > 7 else "", p[4]] for p in pts],
                             [w for i, w in enumerate(want) if only is None or i in only], how)
            if how.endswith("rows alone, read back"):   # "≤ 5", "< OR = 5", "=>60" with their numbers (they went out as text alone)
                out = [((e["resource"].get("referenceRange") or [{}])[0]) for e in bundle["entry"] if e["resource"]["resourceType"] == "Observation"]
                self.assertEqual([[x.get("low", {}).get("value"), x.get("high", {}).get("value"), x.get("text")] for x in out],
                                 [[None if e else lo, None if e else hi, rng] for (rng, _, lo, hi, e, _) in M.SPELLINGS], "the export's ranges")

        r = self.c["robin"].post(f"/api/profiles/{self.pid}/manual", headers=H, json={"rows": [
            {"date": t[:10], "time": t[11:16], "test": n, "value": v, "unit": u, "range": rng} for n, u, t, v, rng in rows]})
        self.assertEqual(r.status_code, 200, r.text)
        typed = r.json()["rows"]
        self.assertEqual([[x["value"], x["low"], x["high"], x["edge"], x["flag"]] for x in typed], want, "Type them in's check")
        self.assertEqual([i for i, x in enumerate(typed) if "the range couldn't be read, so it isn't used for the flag" in x["notes"]],
                         [i for i, (_, _, lo, hi, *_) in enumerate(M.SPELLINGS) if lo is None and hi is None], "its notes")

    def test_2_bound_values(self):
        """#870: a value given as a bound, as reports print one (ranges_matrix BOUND_VALUES: "=<5", "≤ 5", "< 5", "≥60",
        "=>60", "> 10", ">= 60", "<0.5", Quest's "> OR = 60"), beside a range, gives the same number, comparator and flag through every
        importer (_every_importer: spreadsheets, Quest-like PDF reports, C-CDA, FHIR, CareSpace, MyChart) and Type them
        in's check, and through the FHIR export read back, of a spreadsheet's import and of rows alone. The export sent
        "=<5", "≤ 5" and "≥60" as text alone (its own reader read "<", "<=", ">" and ">=" only), and the FHIR importer
        kept every bound as text, so read back they had no number, range or flag."""
        rows, tmp, (lab, unit) = M.bounded(TODAY), tempfile.mkdtemp(dir=TMP), M.SPELLED_AS
        want = [[x, c, f] for _, _, x, c, f in M.BOUND_VALUES]
        for how, data, only, bundle in self._every_importer(rows, tmp):
            pts = next((s["pts"] for s in data["labs"] if s["name"] == lab), [])   # [value, its comparator, flag]
            self.assertEqual([[p[1], ranges.comparator(p[5]), p[4]] for p in pts], [w for i, w in enumerate(want) if only is None or i in only], how)
            if bundle:   # each a Quantity with its comparator (FHIR's "<", "<=", ">=", ">")
                q = [e["resource"].get("valueQuantity") or {} for e in bundle["entry"] if e["resource"]["resourceType"] == "Observation"]
                self.assertEqual([[x.get("value"), x.get("comparator")] for x in q], [[x, c] for _, _, x, c, _ in M.BOUND_VALUES], how)
        r = self.c["robin"].post(f"/api/profiles/{self.pid}/manual", headers=H, json={"rows": [
            {"date": t[:10], "time": t[11:16], "test": n, "value": v, "unit": u, "range": rng} for n, u, t, v, rng in rows]})
        self.assertEqual(r.status_code, 200, r.text)
        # (#875: with its comparator as read, for its check line to say "≤ 5 U/L", not "5 U/L")
        self.assertEqual([[x["value"], x["cmp"], x["flag"]] for x in r.json()["rows"]], [[x, c, f] for _, _, x, c, f in M.BOUND_VALUES],
                         "Type them in's check")

    def test_2_commas_in_each_mode(self):
        """#859: no thousands follow a leading 0 (common.number_text). ranges_matrix COMMAS' numbers ("0,500", "0,05",
        "1,250", "12,500", and #870's "1.2E-3" and "5,0E3"), each as a value and in a range ("<0,500"), read as each mode
        reads them: where a file's commas group thousands (a comma-delimited spreadsheet that writes decimal points, Type
        them in, and Quest's PDF reports, a US lab's whose layout says so) "0,500" is unread, kept as printed and said so
        (the upload's warning, Type them in's notes), "1,250" 1250; where they're decimal (a spreadsheet with semicolons, or
        with "0,500" among its values) "0,500" is 0.5 and "1,250" 1.25; where that isn't known (C-CDA, FHIR, CareSpace,
        MyChart, a PDF report from a lab whose layout doesn't say) "0,500" is 0.5 and "1,250" unread, as a value (#870: the
        PDF read "0,500" as 500 whatever its lab, "0,05" not at all) and in a range. And the FHIR
        export of each import, read back, has the same values, ranges and flags (#870: it sent "0,500" and "1,250" as
        text alone, so they came back with no number, range or flag; a limit printed "<1,250" in a file whose commas
        group thousands came back with none, and one its import couldn't read, "<0,500" there, as 0.5)."""
        import csv, types
        from unittest import mock
        from server.importers import carespace, ccda, csv_labs, epic_mychart, fhir, pdf_labs, pdf_layouts
        tmp, (lab, unit) = tempfile.mkdtemp(dir=TMP), M.SPELLED_AS
        nums = list(M.COMMAS)
        values = [(lab, unit, M.day(100 - i, TODAY), n, "") for i, n in enumerate(nums)]   # each a value, no range
        ranged = [(lab, unit, M.day(50 - i, TODAY), "5", "<" + n) for i, n in enumerate(nums)]   # 5 beside "<0,500"...
        flags = lambda highs: [ranges.flag(5, None, h, "", "<") for h in highs]   # noqa: E731  (5 against each, its edge left out)
        want = lambda mode, kept=True: ([M.COMMAS[n][mode] if kept else None for n in nums], [M.COMMAS[n][mode] for n in nums],   # noqa: E731
                                        flags(M.COMMAS[n][mode] for n in nums))

        def read(data):
            """[each value as read (None: kept as text)], [each range's high as read], [each ranged one's flag]"""
            s = next((s for s in data["labs"] if s["name"] == lab), {"pts": [], "txt": []})
            at = {p[0][:16]: p for p in s["pts"]}
            return ([(at.get(t[:16]) or [None, None])[1] for _, _, t, _, _ in values],
                    [(at.get(t[:16]) or [None] * 4)[3] for _, _, t, _, _ in ranged],
                    [(at.get(t[:16]) or [None] * 5)[4] for _, _, t, _, _ in ranged])

        def back(data, how):   # the FHIR export of an import, read back: the same values, ranges and flags (#870)
            self.assertEqual(read(self._read_back(data, "Commas")[0]), read(data), f"the FHIR export of {how}, read back")

        def sheet(name, delim, extra):
            path = os.path.join(tmp, name)
            with open(path, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f, delimiter=delim)
                w.writerow(["Date", "Time", "Test", "Value", "Unit", "Reference range"])
                w.writerows([t[:10], t[11:16], n, v, u, r] for n, u, t, v, r in values + ranged + extra)
            return csv_labs.parse({name: path})
        points = [("Potassium", "mmol/L", M.day(10, TODAY), "4.2", "3.5 - 5.1")]   # (writes decimal points)
        commas = [("Potassium", "mmol/L", M.day(10, TODAY), "4,2", "3,5 - 5,1")]   # (writes decimal commas)
        thousands = sheet("in-thousands.csv", ",", points)
        self.assertEqual(read(thousands["data"]), want(M.IN_THOUSANDS), "a comma-delimited spreadsheet that writes decimal points")
        self.assertIn("1 value couldn't be read as a number, so it was kept as text, not charted: in-thousands.csv line 2 ('0,500').",
                      thousands["warnings"])
        self.assertIn(f"1 range couldn't be read, so it isn't used for the flag: in-thousands.csv line {2 + len(nums)} ('<0,500').",
                      thousands["warnings"])
        back(thousands["data"], "a comma-delimited spreadsheet")
        for name, delim, extra, how in (("decimal.csv", ";", commas, "a spreadsheet with semicolons"),
                                        ("decimal-quoted.csv", ",", [], "a comma-delimited one with \"0,500\" among its values")):
            data = sheet(name, delim, extra)["data"]
            self.assertEqual(read(data), want(M.DECIMAL), how)
            back(data, how)

        r = self.c["robin"].post(f"/api/profiles/{self.pid}/manual", headers=H, json={"rows": [
            {"date": t[:10], "time": t[11:16], "test": n, "value": v, "unit": u, "range": rng} for n, u, t, v, rng in values + ranged]})
        self.assertEqual(r.status_code, 200, r.text)
        typed = r.json()["rows"]
        self.assertEqual(([x["value"] for x in typed[:len(nums)]], [x["high"] for x in typed[len(nums):]], [x["flag"] for x in typed[len(nums):]]),
                         want(M.IN_THOUSANDS), "Type them in")
        self.assertEqual([[n for n in x["notes"] if "read" in n or "not a number" in n] for x in typed],
                         [["not a number: kept as a text result, not charted"] if M.COMMAS[n][M.IN_THOUSANDS] is None else [] for n in nums]
                         + [["the range couldn't be read, so it isn't used for the flag"] if M.COMMAS[n][M.IN_THOUSANDS] is None else []
                            for n in nums], "Type them in's notes")

        # where decimal commas aren't known: a value as printed (C-CDA's text, CareSpace's, a PDF report's), a range as
        # text alone (a FHIR bundle's and a MyChart export's values are numbers of their own) and a PDF report's range cell
        docs = {}   # (a document a result: the export finds a report's rows at its time)
        for i, row in enumerate(values + ranged):
            docs[f"commas-{i}.xml"] = path = os.path.join(tmp, f"commas-{i}.xml")
            with open(path, "w", encoding="utf-8") as f:
                f.write(M.ccda([row]))
        with open(os.path.join(tmp, "commas.json"), "w", encoding="utf-8") as f:
            json.dump({"resourceType": "Bundle", "type": "collection", "entry": [{"resource": {
                "resourceType": "Observation", "id": f"c{i}", "status": "final", "category": [{"coding": [{"code": "laboratory"}]}],
                "code": {"text": n}, "effectiveDateTime": t, "valueQuantity": {"value": 5, "unit": u}, "referenceRange": [{"text": rng}]}}
                for i, (n, u, t, v, rng) in enumerate(ranged)]}, f)
        gen = types.ModuleType("pdf_fixture_generator")
        gen.__file__ = os.path.join(ROOT, "tests", "fixtures", "importers", "pdf-labs", "generate.py")
        with open(gen.__file__, encoding="utf-8") as f:
            exec(compile(f.read(), gen.__file__, "exec"), gen.__dict__)
        pdfs = {}
        for n, u, t, v, rng in values + ranged:
            pdfs[f"quest-{t[:10]}.pdf"] = path = os.path.join(tmp, f"quest-{t[:10]}.pdf")
            gen.write(path, gen.quest([("COMMAS (FICTIONAL)", None), (n, v, "", rng, u)], collected=f"{t[5:7]}/{t[8:10]}/{t[:4]} / 08:00 EST"))
        by_pdf = pdf_labs.parse(pdfs)   # (Quest's: a US lab's, so its commas group thousands)
        pdf_labs._cache.clear()
        with mock.patch.object(pdf_layouts, "layouts", lambda: [M.pdf_layout_unsaid()]):
            unsaid_pdf = pdf_labs.parse(pdfs)
        for how, data, kept in (("C-CDA documents", ccda.parse(docs)["data"], True),
                                ("a FHIR bundle", fhir.parse({"commas.json": os.path.join(tmp, "commas.json")})["data"], False),
                                ("a CareSpace export", carespace.parse(M.carespace(values + ranged, os.path.join(tmp, "carespace")))["data"], True),
                                ("a MyChart export", epic_mychart.parse(M.epic(ranged, os.path.join(tmp, "mychart")))["data"], False),
                                ("PDF reports from a lab whose layout doesn't say its decimal mark", unsaid_pdf["data"], True)):
            self.assertEqual(read(data), want(M.UNKNOWN, kept), how)
            back(data, how)
        self.assertEqual(read(by_pdf["data"]), want(M.IN_THOUSANDS), "Quest's PDF reports")
        back(by_pdf["data"], "Quest's PDF reports")
        for out, mode in ((by_pdf, M.IN_THOUSANDS), (unsaid_pdf, M.UNKNOWN)):   # a PDF report's says each, as a spreadsheet's does
            self.assertEqual(sorted(w for w in out["warnings"] if "couldn't be read" in w), sorted(
                [f"quest-{t[:10]}.pdf: 1 value couldn't be read as a number, so it was kept as text, not charted: {lab} ('{v}')."
                 for _, _, t, v, _ in values if M.COMMAS[v][mode] is None]
                + [f"quest-{t[:10]}.pdf: 1 range couldn't be read, so it isn't used for the flag: {lab} ('{r}')."
                   for _, _, t, _, r in ranged if M.COMMAS[r[1:]][mode] is None]), mode)

    def test_2_read_again_adds_nothing(self):
        """#874: a file read again after #870 changed its reading between text and a number adds nothing. #870's
        spellings (ranges_matrix BOUND_VALUES' "=<5", "≤ 5", "≥60", "> 10"...; COMMAS' "0,500", "1,250", "1.2E-3",
        "5,0E3"...; commas_back()'s two spreadsheets) through every importer (_every_importer) and the FHIR export of the
        spreadsheets' import read back, each stored as an older reading kept it (ranges_matrix read_before(): each result
        as text, as given or as printed, as the FHIR importer kept a bound, C-CDA "4,5" and the export sent "=<5" and
        "1,250"; and each text result now a point, as a PDF report's "1,250" was), then read again as it reads now; and
        the other way round. preview.find matched points only with points and text only with text, so each was added again beside
        its older copy."""
        import copy
        from server import preview
        from server.importers import csv_labs
        tmp, (lab, unit) = tempfile.mkdtemp(dir=TMP), M.SPELLED_AS
        rows = M.bounded(TODAY) + [(lab, unit, M.day(60 - i, TODAY), n, "") for i, n in enumerate(M.COMMAS)]
        printed = {t[:10]: v for _, _, t, v, _ in rows}   # (a day each)
        reads = [(how, data, lambda s, p: printed.get(str(p[0])[:10])) for how, data, *_ in self._every_importer(rows, tmp)]
        for name, text in M.commas_back(TODAY):   # #870's two spreadsheets, and the FHIR export of each's import read back
            with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
                f.write(text)
            data = csv_labs.parse({name: os.path.join(tmp, name)})["data"]
            sent = {(s["name"], str(p[0])[:10]): p[5] for s in data["labs"] for p in s["pts"]}   # what the export sent as text
            reads += [(name, data, None), (f"{name}'s FHIR export, read back", self._read_back(data, "Commas")[0],
                                           lambda s, p, sent=sent: sent.get((s["name"], str(p[0])[:10])))]

        db = dbm.connect()
        try:
            owner = db.execute("SELECT id FROM users WHERE username='robin'").fetchone()["id"]
            for how, data, text in reads:
                older = M.read_before(data, text)
                self.assertTrue(any(s.get("txt") for s in older["labs"]), how)
                for first, then, which in ((older, data, "read again as it reads now"), (data, older, "an older reading of it again")):
                    pid = prof.create_profile(db, "Read again (fictional)", owner_id=owner)
                    with dbm.transaction(db):
                        records.store(db, pid, copy.deepcopy(first), "upload", fmt="csv")
                    dups = preview.find(db, pid, "csv", then)
                    new = preview.trim(then, dups)
                    self.assertEqual(([(s["name"], [p[5] for p in s["pts"]], s.get("txt")) for s in new["labs"] if isinstance(s, dict)],
                                      preview.adds_anything(new), preview.updates(dups)), ([], False, 0), f"{how}: {which}")
                    prof.delete_profile(db, pid)
        finally:
            db.close()

    def test_2_summary_matrix(self):
        """#752: one rule for a result's range, flag and source, through the server. The matrix (tests/ranges_matrix.py): a
        report's range then none; none then a report's; Settings then a report's; a report's then Settings; a range that
        changes; "<" and ">" values at and past each edge of a report's range and of one from Settings. The same flags,
        ranges and sources in the summary's points, its cells (each Then judged against a range other than its row's
        names it), its out-of-range rows, its notes, the PDF's cells and charts (a band step for each result with a
        range, none beside one without, each said with its source) and a share link (its report's ranges only); the
        importers' flag the same rule."""
        db = dbm.connect()
        try:
            owner = db.execute("SELECT id FROM users WHERE username='robin'").fetchone()["id"]
            pid = prof.create_profile(db, "Matrix (fictional)", owner_id=owner)
            with dbm.transaction(db):
                records.store(db, pid, M.records(TODAY), "upload", fmt="csv")
        finally:
            db.close()
        c, labs = self.c["robin"], list(M.CASES)
        for key, value in (("ranges.overrides", M.OVERRIDES), ("overview.keyLabs", labs)):
            self.assertEqual(c.put(f"/api/profiles/{pid}/settings/{key}", json={"value": value}, headers=H).status_code, 200)
        db = dbm.connect()
        try:
            data = records.assemble(db, pid)
            cfg = ranges.config(settings.merged(db, pid, owner))
        finally:
            db.close()
        # #782: each lab day's rows named with their series, as the page reads their flags: "CHLORIDE" is Chloride,
        # Blood's, "CREAT" Creatinine's
        rows = {e["t"]: data["details"][str(e["id"])] for e in data["events"] if e["k"] == "labitem"}
        self.assertEqual({t: r.get("series") for t, r in rows.items()}, {M.day(d, TODAY): [n for n, _ in M.day_results(d)] for d in M.draw_days()})
        self.assertEqual({r[0] for d in rows.values() for r in d["rows"]} - set(labs), {"CHLORIDE", "CREAT"})
        series = summary._series(data, cfg)
        judged = {n: [[p["flag"], p["lo"], p["hi"], p["rs"]] for p in series[n]["pts"]] for n in labs}
        self.assertEqual(judged, M.JUDGED, "each result judged by the one rule: its report's, worked out against its report's "
                                           "range, or a range from settings (\"<15\" against a low of 15 is low)")
        self.assertEqual({n: [p["edge"] for p in series[n]["pts"]] for n in labs}, {n: M.edges(n) for n in labs},
                         "each one-sided range keeps whether its limit includes its edge (#780)")
        # the band's steps: each covers the results with its range, none beside a result without one, each said with its source
        for n in labs:
            ts = [datetime.date.fromisoformat(p["d"]).toordinal() for p in series[n]["pts"]]
            got = [[[i for i, t in enumerate(ts) if a < t < b], f"{summary.range_words(lo, hi, e)} {summary.band_source(rs)}"]
                   for a, b, lo, hi, rs, e in ranges.steps([(t, p["lo"], p["hi"], p["rs"], p["edge"]) for t, p in zip(ts, series[n]["pts"])],
                                                           -float("inf"), float("inf"))]
            self.assertEqual(got, M.STEPS[n], n)
        # the importers' flag is the rule's (common.report_flag, #817): a CSV's "<30" against its own 30–110 is low, as
        # Labs and the summary say
        from server.importers import common
        for d, v, lo, hi, f, t in M.CASES["Amylase"]:
            self.assertEqual(common.report_flag("", v, lo, hi, ranges.comparator(t)), ranges.flag(v, lo, hi, ranges.comparator(t)), t)
        self.assertEqual([ranges.flag(30, 30, 110, ranges.comparator(t)) for t in ("<30", "<=30", "≤30", "30", ">=30")], ["L", "", "", "", ""])
        self.assertEqual([ranges.flag(110, 30, 110, ranges.comparator(t)) for t in (">110", ">=110", "≥110", "110", "<=110")], ["H", "", "", "", ""])

        body = {"appointment": None, "since": M.day(M.SINCE, TODAY)[:10], "sections": ["out_of_range", "trends"], "labs": labs}
        r = c.post(f"/api/profiles/{pid}/summary/preview", headers=H, json=body)
        self.assertEqual(r.status_code, 200, r.text)
        s = r.json()
        tr = {t["name"]: t for t in s["trends"]}
        for n, (then, now, rng) in M.TRENDS.items():
            cell = lambda x: (x["value"], x["flag"], x.get("own"))
            self.assertEqual((cell(tr[n]["then"]), cell(tr[n]["now"]), tr[n]["now"]["range"]), (then, now, rng), n)
            line = [p for p in series[n]["pts"] if p["d"] <= body["since"]][-1:] + [p for p in series[n]["pts"] if p["d"] > body["since"]]
            want = [[M.SAID.get(j[0]), j[1], j[2], ranges.SOURCE if j[3] == ranges.SOURCE else None]
                    for p, j in zip(series[n]["pts"], M.JUDGED[n]) if p in line]
            self.assertEqual([q[2:6] for q in tr[n]["spark"]], want, f"{n}: the chart's and sparkline's points")
            self.assertEqual([q[7] for q in tr[n]["spark"]], [e for p, e in zip(series[n]["pts"], M.edges(n)) if p in line],
                             f"{n}: the chart's points' edges (#780)")
        oor = {o["name"]: [o["value"], o["flag"], o["range"], o["out"], o["of"]] for o in s["out_of_range"]}
        self.assertEqual(oor, M.OUT_OF_RANGE, "out of range since then: \"<30\", \"<25\", \">110\" and \">120\" among them")
        notes = s["notes"]
        self.assertIn("A Then judged against a range other than its row's (its Now's) gives it in its cell", notes["trends"])
        self.assertIn(summary.BOUND_NOTE.strip(), notes["trends"])
        self.assertIn(summary.BOUND_NOTE.strip(), notes["out_of_range"])
        self.assertIn("or, where its report gives none, the range from settings", notes["out_of_range"])
        self.assertIn("each step marked “on report” or “from settings”, none beside a result with neither", notes["charts"])
        # #819: Chloride, Blood's "A" (◆, inside its range) is "Flagged", and the notes and the heading say what that is
        self.assertIn(f"how many of the results since then were {M.flagged(['A'], 'short')}.", notes["out_of_range"])
        for k in ("out_of_range", "trends"):
            self.assertIn(M.FLAGGED_NOTE, notes[k], f"#819: the {k} note says what Flagged is")
        self.assertIn("; a red diamond, one flagged on its report.", notes["charts"])
        for form, (plain, a) in M.FLAGGED_WORDS.items():
            self.assertEqual([ranges.flagged_words(f, form) for f in ([], ["H", "Low"], ["High", "A"], ["Flagged"])], [plain, plain, a, a], form)

        r = c.post(f"/api/profiles/{pid}/summary/pdf", headers=H, json=body)
        self.assertEqual(r.status_code, 200, r.text[:200])
        text = " ".join("\n".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages).split())
        self.assertIn(f"{M.flagged(['A'], 'short').capitalize()} since", text, "#819: the PDF's heading says a ◆ is in it")
        for n, (then, _, _) in M.TRENDS.items():
            words = f"{then[0]}{' ' + then[1] if then[1] else ''} {summary._short(tr[n]['then']['date'])}"
            self.assertIn(words + (f" ({' '.join(w for w in then[2] if w)})" if then[2] else ""), text, f"{n}: the PDF's Then cell")
        for note in (notes["trends"], notes["out_of_range"], notes["charts"]):
            self.assertIn(note, text)
        token = summary._DATES.set(("", TODAY.year))
        try:
            for n in labs:
                page, row = _Drawn(), tr[n]
                summary._chart(page, row, [n], 0, 200, 120)
                covered = [any(a <= x <= b for a, b in page.rects) for x in page.marks]
                spark = [p for p in series[n]["pts"] if p["d"] <= body["since"]][-1:] + [p for p in series[n]["pts"] if p["d"] > body["since"]]
                self.assertEqual(covered, [p["lo"] is not None or p["hi"] is not None for p in spark], f"{n}: a band under each result with a range only")
                said = [t for *_, t in page.texts if t.endswith(("on report", "from settings"))]
                steps = {w for idx, w in M.STEPS[n] if any(series[n]["pts"][i] in spark for i in idx)}
                self.assertTrue(said and all(t in steps or t in ("on report", "from settings") for t in said),
                                f"{n}: each band label a step's words, or its source alone: {said}")
                self.assertEqual({t.endswith("from settings") for t in said}, {w.endswith("from settings") for w in steps},
                                 f"{n}: each source the band has said on it: {said}")
        finally:
            summary._DATES.reset(token)

        # a share link: its report's ranges and flags only, the same as the summary's for those
        r = c.post(f"/api/profiles/{pid}/links", json={"sections": ["results"]}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        view = TestClient(main.app, base_url="http://testserver").post("/api/share/view", json={"token": r.json()["path"].split("#", 1)[1]}, headers=H)
        self.assertEqual(view.status_code, 200, view.text)
        got = {x["name"]: [[q["flag"], q["lo"], q["hi"], q["range"], q.get("edge", "")] for q in x["results"][::-1]] for x in view.json()["results"]}
        want = {n: [[M.SAID.get(f), lo, hi, M.words(lo, hi, e), e] for (f, lo, hi, _), e in zip(M.share_judged(n), M.edges(n))]
                for n in labs}
        self.assertEqual(got, want, "a share link's flags and ranges: its reports' (none from Settings), each worked out by the one rule")
        db = dbm.connect()
        try:
            prof.delete_profile(db, pid)
        finally:
            db.close()

    def test_3_without_settings(self):
        self.assertEqual(self.c["robin"].post(f"/api/profiles/{self.pid}/settings/reset", json={"section": "ranges"}, headers=H).status_code, 200)
        s = self.preview()
        names = {r["name"] for r in s["out_of_range"]}
        self.assertNotIn("Ferritin", names, "with no range from settings, results without a range aren't judged")
        self.assertNotIn("settings_note", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
