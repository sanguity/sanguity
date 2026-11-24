#!/usr/bin/env python3
"""Importer tests: the registry (best match, nothing matched, ties), every importer against its fixtures, the
shared helpers, and that build.py still writes exactly what it did, with the standard library alone.

Fixtures (fictional, never anyone's real records) live in tests/fixtures/importers/<NAME>/<case>/, one folder per
upload. The registry must pick <NAME> for each, and parse() must keep to the contract. An optional
<case>.expected.json next to the folder holds the exact {"data", "warnings"} parse() should return. A new importer
needs no change here: add its module and a fixture folder.

  python -m unittest discover -s tests -p "test_importer*.py" -v
"""
import glob, json, os, re, shutil, subprocess, sys, tempfile, types, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
sys.path.insert(0, ROOT)

from server import importers as reg   # noqa: E402
from server.importers import common   # noqa: E402


def fake(name, score, data=None):
    """A stand-in importer that always scores `score`."""
    def detect(files):
        if isinstance(score, Exception):
            raise score
        return score
    return types.SimpleNamespace(NAME=name, LABEL=name.title(), detect=detect,
                                 parse=lambda files: {"data": data or {"events": [], "details": {}, "labs": [], "costs": []},
                                                      "warnings": [f"read by {name}"]})


FILES = {"a.txt": "/nowhere/a.txt"}


class Registry(unittest.TestCase):
    def test_best_match(self):
        hit = reg.match(FILES, [fake("low", 0.3), fake("high", 0.9), fake("none", 0.0)])
        self.assertEqual(hit["importer"].NAME, "high")
        self.assertEqual(hit["score"], 0.9)
        self.assertEqual(hit["tied"], [])
        self.assertEqual(reg.run(FILES, modules=[fake("low", 0.3), fake("high", 0.9)])["importer"], "high")

    def test_nothing_matched(self):
        self.assertIsNone(reg.match(FILES, [fake("a", 0.0), fake("b", 0.0)])["importer"])
        self.assertIsNone(reg.match(FILES, [fake("a", reg.THRESHOLD / 2)])["importer"])   # below the threshold
        self.assertIsNone(reg.match(FILES, [])["importer"])
        with self.assertRaisesRegex(ValueError, "None of the importers recognised these files"):
            reg.run(FILES, modules=[fake("a", 0.0)])
        with self.assertRaisesRegex(ValueError, "empty"):
            reg.run({}, modules=[fake("a", 1.0)])

    def test_ties_go_to_the_first_name_and_say_so(self):
        mods = [fake("zeta", 0.8), fake("alpha", 0.8), fake("mid", 0.5)]
        for order in (mods, mods[::-1]):
            hit = reg.match(FILES, order)
            self.assertEqual(hit["importer"].NAME, "alpha")
            self.assertEqual([m.NAME for m in hit["tied"]], ["zeta"])
        out = reg.run(FILES, modules=mods)
        self.assertEqual(out["importer"], "alpha")
        self.assertIn("Zeta", out["warnings"][0])
        self.assertEqual(out["warnings"][1:], ["read by alpha"])

    def test_detect_that_raises_or_overshoots(self):
        with self.assertLogs("sanguity.importers", "ERROR"):
            ranked = reg.scores(FILES, [fake("broken", RuntimeError("boom")), fake("loud", 7)])
        self.assertEqual([(s, m.NAME) for s, m in ranked], [(1.0, "loud"), (0.0, "broken")])

    def test_named_importer(self):
        mods = [fake("a", 0.0), fake("b", 1.0)]
        self.assertEqual(reg.run(FILES, name="a", modules=mods)["importer"], "a")
        with self.assertRaisesRegex(ValueError, "no importer called"):
            reg.run(FILES, name="nope", modules=mods)

    def test_bad_output_is_refused(self):
        bad = fake("bad", 1.0, data={"events": [{"id": 1}], "details": {"9": {}}, "labs": [], "costs": []})
        with self.assertRaisesRegex(ValueError, "can't load"):
            reg.run(FILES, modules=[bad])
        self.assertEqual(reg.check({"data": {"events": []}}), ["data has no 'details'", "data has no 'labs'", "data has no 'costs'"])
        self.assertIn("document 'x.pdf' isn't in the upload",
                      reg.check({"data": {"events": [{"id": 1, "t": "2026-01-01", "k": "document", "title": "X"}],
                                          "details": {"1": {"type": "file", "url": "x.pdf#page=1"}}, "labs": [], "costs": []}}, FILES))

    def test_keys(self):
        def out(events, labs=()):
            return {"data": {"events": [dict(id=i + 1, t="2026-01-01", k="visit", title="V", **e) for i, e in enumerate(events)],
                             "details": {}, "labs": [dict(name=f"L{i}", pts=[], **s) for i, s in enumerate(labs)], "costs": []}}
        self.assertEqual(reg.check(out([{"key": "x:1"}, {"key": "x:2"}, {}], [{"key": "loinc:718-7"}, {}])), [])
        self.assertEqual(reg.check(out([{"key": "x:1"}, {"key": "x:1"}])), ["event 2: key 'x:1' is used twice"])
        self.assertEqual(reg.check(out([{"key": 7}, {"key": " "}])), ["event 1: key must be a non-empty string",
                                                                    "event 2: key must be a non-empty string"])
        self.assertEqual(reg.check(out([], [{"key": "loinc:1"}, {"key": "loinc:1"}])), ["lab series 'L1': key 'loinc:1' is used twice"])
        self.assertEqual(reg.check(out([{"key": "x:1"}], [{"key": "x:1"}])), [])   # events and series are separate spaces
        split = out([], [{"key": "loinc:2345-7|mg/dL"}, {"key": "loinc:2345-7|mmol/L"}])   # one code split by unit
        self.assertEqual(reg.check(split), [])
        o = out([{"key": "x:1"}])
        o["data"]["problems"] = [{"name": "A", "key": "x:p1"}, {"name": "B", "key": "x:p1"}]
        o["data"]["upcoming"] = [{"t": "2026-12-01", "key": "x:1"}, {"t": "2026-12-02", "key": ""}]
        self.assertEqual(reg.check(o), ["problems 1: key 'x:p1' is used twice", "upcoming 1: key must be a non-empty string"])

    def test_discovery(self):
        names = [m.NAME for m in reg.importers()]
        self.assertIn("sanguity", names)
        self.assertNotIn("common", names)
        self.assertEqual(names, sorted(names))
        self.assertEqual(len(names), len(set(names)))   # the file name is free, NAME must be unique
        for m in reg.importers():
            self.assertRegex(m.NAME, reg.NAME_RE)
            self.assertTrue(m.LABEL)
        self.assertEqual(reg.get("sanguity").NAME, "sanguity")

    def test_names_checked(self):
        def mod(file, name):
            m = fake(name, 0.0)
            m.__name__ = f"server.importers.{file}"
            return m
        with self.assertLogs("sanguity.importers", "WARNING") as logs:
            got = reg.accept([mod("csv_labs", "csv"), mod("apple_health", "apple-health"), mod("a", "Bad_Name"),
                              mod("b", "9lives"), mod("one", "dup"), mod("two", "dup")])
        self.assertEqual([m.NAME for m in got], ["apple-health", "csv"])
        self.assertTrue(any("'dup'" in line and "one" in line and "two" in line for line in logs.output))


class Fixtures(unittest.TestCase):
    """Each importer reads each of its fixture uploads, is the one the registry picks, and keeps to the contract."""

    def test_every_fixture(self):
        base = os.path.join(FIXTURES, "importers")
        cases = [(n, c) for n in sorted(os.listdir(base)) if os.path.isdir(os.path.join(base, n))
                 for c in sorted(os.listdir(os.path.join(base, n))) if os.path.isdir(os.path.join(base, n, c))
                 and not c.startswith(("_", "."))]   # __pycache__ beside a fixture script isn't a case
        self.assertIn(("sanguity", "export"), cases)
        known = {m.NAME for m in reg.importers()}
        for name, case in cases:
            with self.subTest(importer=name, fixture=case):
                if name not in known:
                    self.fail(f"tests/fixtures/importers/{name}/ has no importer server/importers/{name}.py")
                files = reg.files_in(os.path.join(base, name, case))
                hit = reg.match(files)
                self.assertEqual(hit["importer"] and hit["importer"].NAME, name, f"scores: {[(s, m.NAME) for s, m in reg.scores(files)]}")
                out = reg.get(name).parse(files)
                self.assertEqual(reg.check(out, files), [])
                self.assertTrue(all(isinstance(w, str) for w in out["warnings"]))
                expected = os.path.join(base, name, case + ".expected.json")
                if os.path.exists(expected):
                    with open(expected, encoding="utf-8") as f:
                        self.assertEqual(json.loads(json.dumps(out)), json.load(f))


class SanguityImporter(unittest.TestCase):
    def test_demo_data(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run([sys.executable, "-I", os.path.join(ROOT, "demo", "generate.py"), d, "--end", "2026-11-15",
                            "--watch", os.path.join(d, "watch")], check=True, stdout=subprocess.DEVNULL)
            out = reg.run(reg.files_in(d))
        self.assertEqual(out["importer"], "sanguity")
        self.assertEqual(out["warnings"], [])
        docs = [d for d in out["data"]["details"].values() if d.get("type") == "file"]
        self.assertTrue(docs and all(d["url"] for d in docs))

    def test_events_after_the_first_64_kb(self):   # a big record's export lists its payments first (#201)
        with tempfile.TemporaryDirectory() as d:
            payments = [{"d": "2026-01-03", "payee": "Example Health Plan", "amount": 186.0}] * 2000
            with open(os.path.join(d, "data.json"), "w", encoding="utf-8") as f:
                json.dump({"built": "2026-11-15T06:00:00", "outOfPocket": {"payments": payments}, "events": [], "details": {}}, f)
            self.assertGreater(os.path.getsize(os.path.join(d, "data.json")), 64 * 1024)
            self.assertEqual(reg.run(reg.files_in(d))["importer"], "sanguity")

    def test_keys_pass_through(self):
        out = reg.run(reg.files_in(os.path.join(FIXTURES, "importers", "sanguity", "export")))
        self.assertEqual({e["id"]: e["key"] for e in out["data"]["events"] if "key" in e},
                         {3: "fhir:DocumentReference/doc-0003", 5: "csv:h:5f1c0a9e2b7d4c61"})
        self.assertEqual(out["data"]["labs"][0]["key"], "loinc:718-7")

    def test_other_formats_score_zero(self):
        from server.importers import sanguity
        self.assertEqual(sanguity.detect(reg.files_in(os.path.join(FIXTURES, "build"))), 0.0)   # an Epic export
        self.assertEqual(sanguity.detect({}), 0.0)

    def test_unreadable(self):
        from server.importers import sanguity
        with tempfile.TemporaryDirectory() as d:
            for body, msg in ((b"{not json", "isn't readable JSON"), (b'{"labs": []}', "no list of events")):
                with open(os.path.join(d, "data.json"), "wb") as f:
                    f.write(body)
                with self.assertRaisesRegex(ValueError, msg):
                    sanguity.parse(reg.files_in(d))


class Common(unittest.TestCase):
    def test_units(self):
        self.assertEqual(common.unit_norm("x10E3/uL"), common.unit_norm("10*3/µL"))
        self.assertEqual(common.unit_for("Sodium, Blood", "mEq/L"), common.unit_for("Sodium, Blood", "mmol/L"))
        self.assertNotEqual(common.unit_for("Hemoglobin", "g/L"), common.unit_for("Hemoglobin", "g/dL"))
        self.assertEqual(common.unit_key("thou/uL"), "k/ul")

    def test_lab_names(self):
        self.assertEqual(common.canonical_lab("hgb"), "Hemoglobin")
        self.assertEqual(common.canonical_lab("Something else", loinc="1751-7"), "Albumin")
        self.assertIsNone(common.canonical_lab("Not a lab anyone knows"))
        labs = {"Hemoglobin": {"unit": "g/dL"}}
        self.assertEqual(common.resolve_name("HGB", "g/dL", "Other Clinic", labs), "Hemoglobin")
        self.assertEqual(common.resolve_name("HGB", "g/L", "Other Clinic", labs), "Hemoglobin (Other Clinic)")

    def test_a_report_flag(self):
        """#817: one rule for a result's flag from its report, every importer's (CSV and typed in, PDF, FHIR, C-CDA,
        CareSpace, MyChart): H or L as printed, whatever the range; a flag that says no way ("A", "AA", "Abnormal",
        "Critical", "Alert") placed by the range where it can be, else kept "A" (inside its range, none printed, a text
        result); none, or one that says neither ("N", "Normal", anything else), by the range alone."""
        f = common.report_flag
        self.assertEqual([f(p, 13, 11, 15) for p in ("H", "HH", "HU", "High", "Critical-High", "H*", ">", "L", "LL", "low", "Panic low", "<")],
                         ["H"] * 7 + ["L"] * 5)
        no_way = ("A", "AA", "Abnormal", "ABN", "Critical", "Alert", "Critical abnormal", "A*", ["N", "A"])
        self.assertEqual([f(p, 13, 11, 15) for p in no_way], ["A"] * len(no_way))
        self.assertEqual([f(p, 16, 11, 15) for p in no_way] + [f(p, 10, 11, 15) for p in no_way], ["H"] * len(no_way) + ["L"] * len(no_way))
        self.assertEqual([f("A", 13, None, None), f("Abnormal", None, None, None), f("AA", None, 11, 15)], ["A"] * 3)
        self.assertEqual([f(p, 13, 11, 15) for p in ("", None, [], "N", "Normal", "Final", "*", "POS")], [""] * 8)
        self.assertEqual([f(p, 16, 11, 15) for p in ("", None, "N", "Normal")], ["H"] * 4)
        self.assertEqual([f(["N", "H"], 13, 11, 15), f(["A", "L"], 16, 11, 15)], ["H", "L"])
        # by the one rule (range_flag): a bound ("<15" against a low of 15 is low), a limit that leaves its edge out (#780)
        self.assertEqual([f("A", 15, 15, 60, "<"), f("A", 5, None, 5, "", "<"), f("A", 5, None, 5), f("A", 5, None, 5, "", "")],
                         ["L", "H", "A", "A"])

    def test_a_flag_said_yes_and_as_hl7(self):
        """#835: the one rule reads a yes in a flag column ("Y", "Yes", "True") as a flag that says no way, and a no
        ("N", "No", "False") as one that says neither; and flag_code gives a printed flag's HL7 interpretation code, as
        the FHIR export writes it and the FHIR importer reads it back to the same flag."""
        f = common.report_flag
        self.assertEqual([f(p, 13, 11, 15) for p in ("Y", "yes", "TRUE", "N", "No", "false")], ["A"] * 3 + [""] * 3)
        self.assertEqual([f(p, 16, 11, 15) for p in ("Yes", "No")], ["H", "H"])
        printed = ["H", "HH", "High", "Critical High", "Panic high", "HU", ">", "L", "LL", "low", "Critical low", "<", "A", "AA",
                   "Abnormal", "Critical", "Critical abnormal", "Panic", "Alert", "ABN", "Yes", "", "N", "Normal", "No", "Final"]
        codes = ["H", "HH", "H", "HH", "HH", "H", "H", "L", "LL", "L", "LL", "L", "A", "AA",
                 "A", "AA", "AA", "AA", "A", "A", "A", "", "", "", "", ""]
        self.assertEqual([common.flag_code(p) for p in printed], codes)
        for p, c in zip(printed, codes):   # the code read back by the one rule gives the printed flag's own
            for v, lo, hi in ((13, 11, 15), (16, 11, 15), (10, 11, 15), (13, None, None)):
                self.assertEqual(f(c, v, lo, hi), f(p, v, lo, hi), (p, c, v, lo, hi))
        self.assertEqual([common.flag_known(p) for p in ("H", "Abnormal", "Yes", "N", "Normal", "No", "", "Final", "see note")],
                         [True] * 7 + [False] * 2)

    def test_a_range_as_text(self):
        """#827, #850: one parser for a reference range given as text: (low, high, edge), a one-sided limit's edge with
        it ("<5" leaves 5 out, "<=5" and "=>60" keep it, #780), a unit or words after it allowed; nothing it can't read.
        tests/test_ranges.py test_2_range_spellings has its spellings through every importer that reads one."""
        r = common.range_text
        self.assertEqual([r(t) for t in ("3.5-5.1", "3.5 - 5.1 mmol/L", "70–99", "10 to 40", "-2 - 2", "0.5-1.5mg/dL", "5..10")],
                         [(3.5, 5.1, ""), (3.5, 5.1, ""), (70, 99, ""), (10, 40, ""), (-2, 2, ""), (0.5, 1.5, ""), (5, 10, "")])
        self.assertEqual([r(t) for t in ("<5", "< 5 mg/L", "less than 5", "under 5", "below 5")], [(None, 5, "<")] * 5)
        self.assertEqual([r(t) for t in ("<=5", "≤ 5", "=<5", "< OR = 5", "<= 5.0", "up to 5", "less than or equal to 5", "< = 5")],
                         [(None, 5, "")] * 4 + [(None, 5.0, "")] + [(None, 5, "")] * 3)
        self.assertEqual([r(t) for t in (">60", "> 60 mL/min/1.73m2", "greater than 60", "more than 60", "over 60", "above 60")],
                         [(60, None, ">")] * 6)
        self.assertEqual([r(t) for t in (">=60", "≥ 60", "=>60", "> OR = 60", "greater than or equal to 60", "=> 60 mL/min")],
                         [(60, None, "")] * 6)
        self.assertEqual([r(t) for t in ("", None, "Negative", "See note", "5-3", "1,000-2,000", "Not Estab.", "5", "<1,000")],
                         [(None, None, "")] * 9)
        # #850: one parser for every importer, Type them in and the FHIR export, so it takes a file's decimal commas: a
        # comma that can't be a thousands separator is a decimal one anywhere ("3,5"); one that can ("1,000") only where
        # the file says which (decimal_comma: a spreadsheet's, csv_labs), never guessed. A whole cell (a PDF report's)
        # is the range and nothing else
        self.assertEqual([r(t) for t in ("3,5-5,1", "5,0 - 10,0", "<0,5", "=>1,5")],
                         [(3.5, 5.1, ""), (5.0, 10.0, ""), (None, 0.5, "<"), (1.5, None, "")])
        self.assertEqual([r(t, False) for t in ("1,000-2,000", "150,000 - 450,000", "<1,000", "1,234.5-2,000")],
                         [(1000, 2000, ""), (150000, 450000, ""), (None, 1000, "<"), (1234.5, 2000, "")])
        self.assertEqual([r(t, True) for t in ("1,000-2,000", "1.234,5-2.000,5", "3,5-5,1", "<1,250")],
                         [(1.0, 2.0, ""), (1234.5, 2000.5, ""), (3.5, 5.1, ""), (None, 1.25, "<")])
        self.assertEqual([r(t, whole=True) for t in ("<5", " 3.5 - 5.1 ", "<5 mg/L", "3.5-5.1 see note")],
                         [(None, 5, "<"), (3.5, 5.1, ""), (None, None, ""), (None, None, "")])
        self.assertEqual([common.range_part(t) for t in ("65-99 mg/dL", "> OR = 60 mL/min/1.73m2", "NEGATIVE", "9-3 mg")],
                         [("65-99", "mg/dL"), ("> OR = 60", "mL/min/1.73m2"), ("", "NEGATIVE"), ("9-3", "mg")])
        self.assertEqual([common.number_text(t, dc) for t in ("1,250", "3,5", "1.234,5", "1'250") for dc in (None, False, True)],
                         [None, 1250, 1.25, 3.5, 3.5, 3.5, 1234.5, 1234.5, 1234.5, 1250, 1250, 1250])
        # #859: no thousands follow a leading 0: "0,500" is 0.5 where commas can be decimal, unread where a comma before
        # three digits groups thousands (False), as is a grouping that can't be one; in a range too
        modes = (None, False, True)
        self.assertEqual({t: [common.number_text(t, dc) for dc in modes] for t in ("0,500", "0,05", "1,250", "12,500", "1234,500",
                                                                                     "12,34.5", "0,500.5", "0.500.000", "1.234.567")},
                         {"0,500": [0.5, None, 0.5], "0,05": [0.05] * 3, "1,250": [None, 1250, 1.25], "12,500": [None, 12500, 12.5],
                          "1234,500": [1234.5, None, 1234.5], "12,34.5": [None] * 3, "0,500.5": [None] * 3, "0.500.000": [None] * 3,
                          "1.234.567": [None, None, 1234567]})
        self.assertEqual([r("<0,500 - 1", dc)[:2] for dc in modes] + [r("0,500 - 1,250", dc)[:2] for dc in modes],
                         [(None, 0.5), (None, None), (None, 0.5), (None, None), (None, None), (0.5, 1.25)])
        # #870: scientific notation, a value's and a range's, in every mode ("<1.0E4" was a high of 1, "5.0E3-1.0E4" none)
        self.assertEqual({t: [common.number_text(t, dc) for dc in modes] for t in ("1.2E-3", "5.0E3", "5,0E3", "1e4", "1E999")},
                         {"1.2E-3": [0.0012] * 3, "5.0E3": [5000.0] * 3, "5,0E3": [5000.0] * 3, "1e4": [10000.0] * 3, "1E999": [None] * 3})
        self.assertEqual([r(t, dc) for t in ("5.0E3-1.0E4", "<1.0E4", "1.0E-3 - 5.0E-3", ">=1.2E-3 mg/L", "5,0E3 - 1,0E4") for dc in (None, True)],
                         [(5000.0, 10000.0, "")] * 2 + [(None, 10000.0, "<")] * 2 + [(0.001, 0.005, "")] * 2 + [(0.0012, None, "")] * 2
                         + [(5000.0, 10000.0, "")] * 2)
        self.assertEqual([common.range_part(t) for t in ("5.0E3-1.0E4 U/L", "3.4-10.8 x10E3/uL")],
                         [("5.0E3-1.0E4", "U/L"), ("3.4-10.8", "x10E3/uL")])
        # a result's value: a bound's number with its comparator aside, a number given as one as it is (#870)
        v = common.value_number
        self.assertEqual([v(t) for t in ("4.2", "=<5", "≤ 5", "< 5", "≥60", "=>60", ">= 60", "4,5", "0,500", "1.2E-3", "<1.0E4", " 7 ",
                                         "> OR = 60", "< or = 5")],
                         [4.2, 5, 5, 5, 60, 60, 60, 4.5, 0.5, 0.0012, 10000.0, 7, 60, 5])
        self.assertEqual([v(t) for t in ("1,250", "> OR 60", "Negative", "5 mg", "", None, True, float("nan"))], [None] * 8)
        self.assertEqual([v("1,250", False), v("0,500", False), v(12.5), v(7)], [1250, None, 12.5, 7])
        self.assertEqual([common.comparator(t) for t in ("=<5", "≤ 5", "< 5", "≥60", "=>60", ">= 60", "> 10", "5", "> OR = 60", "<or=5", "> ORANGE")],
                         ["<=", "<=", "<", ">=", ">=", ">=", ">", "", ">=", "<=", ">"])
        # the edge of a limit read from the text by the same parser, with a source's own low or high beside it (#780)
        self.assertEqual([common.range_edge(t, lo, hi, dc) for t, lo, hi, dc in (
            ("<5", None, 5, None), ("=>60", 60, None, None), (">60", 60, None, None), ("<1,000", None, 1000, None),
            ("<1,000", None, 1000, False), ("<5,5", None, 5.5, None), ("<5", 1, 5, None), ("<5", None, 6, None))],
            ["<", "", ">", "", "<", "<", "", ""])

    # #870: every float() or Decimal() call in server/importers/ and server/fhir_export.py, and every pattern of a
    # number's digits with a decimal mark, by (file, the function or name it's in, the call as written or the pattern's
    # piece), with why it isn't a reader of a number as printed. Any other is one: read it by common.number_text
    # (value_number for a result's value, range_text for a range, NUMBER for a number inside other text). Numbers a
    # source writes as data (a JSON number, FHIR's decimals) aren't printed, so they're taken as they are
    NUMBER_READS = {
        ("importers/common.py", "number_text", "float(s)"): "the one reader of a number as printed",
        ("importers/common.py", "number_text", "[\\d.,"): "the one reader: the shape of a number it reads",
        ("importers/common.py", "number_text", "\\d+,\\d"): "the one reader: a comma before three digits",
        ("importers/common.py", "NUMBER", "\\d+(?:[.,]"): "a number inside other text, whose match number_text reads",
        ("importers/csv_labs.py", "parse", "\\d+,\\d"): "whether a spreadsheet writes decimal commas (\"4,5\" among its values): no number read",
        ("importers/csv_labs.py", "parse", "\\d+\\."): "whether a spreadsheet writes decimal points (\"4.5\" among its values): no number read",
        ("importers/claims_csv.py", "parse", "\\d,\\d"): "whether a claims file writes decimal commas (\"12,50\"): no number read",
        ("importers/claims_csv.py", "parse", "\\d\\."): "whether a claims file writes decimal points (\"12.50\"): no number read",
        ("importers/claims_csv.py", "_money", "float(x)"): "an amount number_text read, as a float",
        ("importers/_claims.py", "money", "float(n)"): "an amount number_text read (or a JSON number), as a float",
        ("importers/__init__.py", "scores", "float(m.detect(files) or 0.0)"): "an importer's detect() score, a number",
        ("importers/apple_health.py", "record", 'float(el.get("value"))'): "HealthKit's own numbers in export.xml",
        ("importers/apple_health.py", "workout", 'float(el.get("duration"))'): "HealthKit's own numbers in export.xml",
        ("importers/apple_health.py", "_workout_hr", "float(st.get(a))"): "HealthKit's own numbers in export.xml",
        ("importers/apple_health.py", "_number", "float(v)"): "HealthKit's own numbers in export.xml",
        ("importers/apple_health.py", "_number", 'float("inf")'): "infinity, to leave it out",
        ("fhir_export.py", "adj", "float(amount or 0)"): "a claim's amount as stored, a number (_claims.money)",
        ("fhir_export.py", "_claims", "float(paid or 0)"): "a claim's amount as stored, a number (_claims.money)",
    }
    # a pattern of a number's digits with a decimal mark: \d+(?:\.\d+)?, \d+\.\d+, \d*[.,]\d+, \d+(?:[.,]\d+)*; \d+,\d{2};
    # [\d.]+, [\d.,]*, [0-9.]+; \d{1,3}(?:,\d{3})+ (not a time's fraction of a second, "\d{2}(?:\.\d+)?")
    NUMBER_SHAPE = re.compile(r"\\d[+*]?\)?\(?(?:\?:)?(?:\\\.|\[[^\]]*[.,][^\]]*\])|\\d[+*]?,\\d|\[(?:\\d|0-9)[^\]]*[.,]|\\d\{1,3\}\(\?:[,.]")

    def test_numbers_read_in_one_place(self):
        """#870: one reader of a number as printed, common.number_text, for every importer and the FHIR export. The PDF
        reports had their own (pdf_labs._number: "0,500" was 500, Troponin I ▲ High; "0,05" and "4,5" weren't values, so
        their rows were left out), and so did the C-CDA, CareSpace and FHIR importers and the export (_num, _value,
        num: "4,5", "=<5" and "≥60" went out as text beside a range and a flag, read back with none). Every float() and
        Decimal() call and every pattern of a number's digits with a decimal mark there is one NUMBER_READS lists."""
        import ast
        found = set()
        for path in sorted(glob.glob(os.path.join(ROOT, "server", "importers", "**", "*.py"), recursive=True)) + [
                os.path.join(ROOT, "server", "fhir_export.py")]:
            rel = os.path.relpath(path, os.path.join(ROOT, "server")).replace(os.sep, "/")
            with open(path, encoding="utf-8") as f:
                src = f.read()
            tree = ast.parse(src)
            docs = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                    and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}

            def visit(node, scope):
                for child in ast.iter_child_nodes(node):
                    s = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else \
                        child.targets[0].id if isinstance(child, ast.Assign) and scope == "<module>" and isinstance(child.targets[0], ast.Name) else scope
                    if isinstance(child, ast.Call) and (isinstance(child.func, ast.Name) and child.func.id in ("float", "Decimal")
                                                        or isinstance(child.func, ast.Attribute) and child.func.attr == "Decimal"):
                        found.add((rel, s, ast.get_source_segment(src, child)))
                    if isinstance(child, ast.Constant) and isinstance(child.value, str) and id(child) not in docs:
                        found.update((rel, s, m.group()) for m in self.NUMBER_SHAPE.finditer(child.value))
                    visit(child, s)
            visit(tree, "<module>")
        self.assertEqual(sorted(found - set(self.NUMBER_READS)), [],
                         "a number read outside common.number_text: read it with number_text, value_number, range_text or NUMBER")
        self.assertEqual(sorted(set(self.NUMBER_READS) - found), [], "listed in NUMBER_READS but gone: take it off")
        for t, want in ((r"-?\d+(?:\.\d+)?", 1), (r"\d+(\.\d+)?", 1), (r"[\d.]+", 1), (r"[0-9.,]+", 1), (r"\d{1,3}(?:,\d{3})+", 1),
                        (r"\d+\.\d+", 1), (r"\d*[.,]\d+", 1), (r"\d+,\d{1,2}", 1), (r"\d+(?:[.,]\d+)*", 1), (r"(\d+)-day supply", 0),
                        (r"(\d{4})-(\d{2})-(\d{2})", 0), (r"(:\d{2}(?:\.\d+)?)?", 0), (r"[x×]?10\^?[Ee]?\d+/[A-Za-z]+", 0), (r"\s*(\S+)", 0)):
            self.assertEqual(len(self.NUMBER_SHAPE.findall(t)), want, t)   # (the search itself)

    def test_stays(self):
        st = common.inferred_stays({"Mar 02, 2026": ["2026-03-02T06:00:00", "2026-03-04T06:00:00"]},
                                   ["2026-05-01T05:00:00", "2026-05-03T05:00:00", "2026-06-20T05:00:00"], "Other Clinic")
        self.assertEqual([(s["t"], s["end"]) for s in st], [("2026-03-02T00:00:00", "2026-03-04T23:59:00"),
                                                            ("2026-05-01T00:00:00", "2026-05-03T23:59:00"),
                                                            ("2026-06-20T00:00:00", "2026-06-20T23:59:00")])
        self.assertEqual(st[0]["title"], "Hospital stay · Other Clinic")
        s = common.stay("2026-03-01", "2026-03-05", "Northfield General Hospital", "", "Visit list")
        self.assertEqual((s["t"], s["end"], s["sub"]), ("2026-03-01T00:00:00", "2026-03-05T23:59:00", "Northfield General Hospital · 4 nights"))

    def test_lab_days(self):
        """A lab day's title counts its values; its sub is cut at a whole order name, with "…" (#458)."""
        self.assertEqual((common.lab_day_title(1), common.lab_day_title(12, "Northfield Lab")), ("1 lab result", "12 lab results · Northfield Lab"))
        orders = ["COMPREHENSIVE METABOLIC PANEL", "CBC WITH DIFFERENTIAL", "VITAMIN D, 25-HYDROXY", "HEPATIC FUNCTION PANEL",
                  "LIPID PANEL", "THYROID STIMULATING HORMONE", "HEMOGLOBIN A1C", "IRON AND TIBC", "MAGNESIUM"]
        self.assertEqual(common.lab_day_sub(orders[:3]), ", ".join(orders[:3]))
        cut = common.lab_day_sub(orders)
        self.assertLessEqual(len(cut), 160)
        self.assertTrue(cut.endswith(", …"), cut)
        self.assertTrue(all(o in orders for o in cut[:-3].split(", ") if o not in ("VITAMIN D", "25-HYDROXY")), cut)
        self.assertTrue(cut[:-3].startswith(", ".join(orders[:5])), cut)
        self.assertEqual(common.lab_day_sub(["WORD " * 40]), "WORD " * 31 + "WORD…")
        self.assertEqual(common.lab_day_sub(["", "CBC"]), "CBC")
        # a first name of every length around the limit, alone or with others, in words or one long word (#464: one of
        # 158 or 159 characters followed by others raised IndexError, failing the import)
        for n in range(150, 171):
            for name in (("WORD " * 40)[:n - 1] + "X", "X" * n):
                for rest in ([], ["CBC"]):
                    with self.subTest(n=n, words=" " in name, rest=rest):
                        got = common.lab_day_sub([name] + rest)
                        self.assertLessEqual(len(got), 160, got)
                        if n + len(", ".join([""] + rest)) <= 160:
                            self.assertEqual(got, ", ".join([name] + rest))
                        else:
                            self.assertTrue(got.endswith("…") and name.startswith(got[:-1].rstrip(", ")), got)


class BuildUnchanged(unittest.TestCase):
    """build.py on small fictional exports writes exactly the expected file (bar the time): tests/fixtures/build/
    sources.json (two Epic exports) gives expected.json, and all-sources.json (those, plus the CareSpace, UHC and BCBS
    importer fixtures) gives expected-all.json, so joining sources, shared lab series and claims inside another
    source's hospital stays are covered too."""

    def build(self, config, wrapper=None):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "data.json")
            env = {k: v for k, v in os.environ.items() if k != "RECORDS_ROOT"}
            args = ["--config", os.path.join(FIXTURES, "build", config), "--out", out]
            cmd = [sys.executable, "-I"] + (["-c", wrapper, os.path.join(ROOT, "build.py")] if wrapper else [os.path.join(ROOT, "build.py")])
            subprocess.run(cmd + args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
            with open(out, "rb") as f:
                return re.sub(rb'^\{"built":"[^"]*",', b"{", f.read())

    def test_output(self):
        with open(os.path.join(FIXTURES, "build", "expected.json"), "rb") as f:
            self.assertEqual(self.build("sources.json"), f.read())

    def test_all_sources(self):
        with open(os.path.join(FIXTURES, "build", "expected-all.json"), "rb") as f:
            self.assertEqual(self.build("all-sources.json"), f.read())

    def test_standard_library_only(self):
        """build.py loads nothing but the standard library and the importers it names (never the registry's scan, so
        an importer with a dependency, such as the PDF one, stays out of the setup container's way)."""
        allowed = {"server", "server.importers", "server.importers.common", "server.importers.epic_mychart",
                   "server.importers.ccda", "server.importers.carespace", "server.importers._claims",
                   "server.importers.claims_uhc", "server.importers.claims_bcbs"}
        wrapper = f"""
import runpy, sys
allowed = {allowed!r}
class Guard:
    def find_spec(self, name, path=None, target=None):
        top = name.split(".")[0]
        if top == "server" and name not in allowed or top != "server" and top not in sys.stdlib_module_names:
            raise ImportError("build.py imported " + name)
        return None
sys.meta_path.insert(0, Guard())
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""
        with open(os.path.join(FIXTURES, "build", "expected-all.json"), "rb") as f:
            self.assertEqual(self.build("all-sources.json", wrapper), f.read())


class BuildVitals(unittest.TestCase):
    """Clinic vitals from Epic visit records through build.py: a reading repeated in two exports counts once, but two
    readings of the same value on one day are two (lab results keep the same value on the same day once)."""

    @staticmethod
    def record(*readings):
        """A fictional C-CDA visit record whose Vital Signs section holds (time, systolic) readings."""
        entries = "".join(
            f'<entry><organizer classCode="CLUSTER" moodCode="EVN"><effectiveTime value="{t}"/><component>'
            f'<observation classCode="OBS" moodCode="EVN"><code code="8480-6" codeSystem="2.16.840.1.113883.6.1"/>'
            f'<value xsi:type="PQ" value="{v}" unit="mm[Hg]"/></observation></component></organizer></entry>'
            for t, v in readings)
        return ('<?xml version="1.0"?>\n<ClinicalDocument xmlns="urn:hl7-org:v3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                '<templateId root="2.16.840.1.113883.10.20.22.1.1"/><component><structuredBody><component><section>'
                f'<code code="8716-3"/>{entries}</section></component></structuredBody></component></ClinicalDocument>')

    def test_vitals_keep_each_reading(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("mychart-20261101", "otherclinic-20261020"):
                shutil.copytree(os.path.join(FIXTURES, "build", name), os.path.join(d, name))
            shutil.copy(os.path.join(FIXTURES, "build", "sources.json"), d)
            vr = os.path.join(d, "mychart-20261101", "visit-records")
            with open(os.path.join(vr, "vitals.xml"), "w", encoding="utf-8") as f:
                f.write(self.record(("202604010915", 128), ("202604011840", 128)))
            os.makedirs(os.path.join(d, "otherclinic-20261020", "visit-records"))
            with open(os.path.join(d, "otherclinic-20261020", "visit-records", "vitals.xml"), "w", encoding="utf-8") as f:
                f.write(self.record(("202604010915", 128)))          # the same reading, in the other export too
            out = os.path.join(d, "data.json")
            env = {k: v for k, v in os.environ.items() if k != "RECORDS_ROOT"}
            subprocess.run([sys.executable, "-I", os.path.join(ROOT, "build.py"), "--config", os.path.join(d, "sources.json"),
                            "--out", out], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
            with open(out, encoding="utf-8") as f:
                data = json.load(f)
        bp = next(s for s in data["labs"] if s["name"] == "Blood pressure, systolic")
        self.assertEqual(bp["panel"], "Vitals")
        self.assertEqual([p[:2] for p in bp["pts"]], [["2026-04-01T09:15:00", 128], ["2026-04-01T18:40:00", 128]])


if __name__ == "__main__":
    unittest.main()
