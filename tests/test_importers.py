#!/usr/bin/env python3
"""Importer tests: the registry (best match, nothing matched, ties), every importer against its fixtures, the
shared helpers, and that build.py still writes exactly what it did.

Fixtures (fictional, never anyone's real records) live in tests/fixtures/importers/<NAME>/<case>/, one folder per
upload. The registry must pick <NAME> for each, and parse() must keep to the contract. An optional
<case>.expected.json next to the folder holds the exact {"data", "warnings"} parse() should return. A new importer
needs no change here: add its module and a fixture folder.

  python -m unittest discover -s tests -p "test_importer*.py" -v
"""
import json, os, re, subprocess, sys, tempfile, types, unittest

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
                 for c in sorted(os.listdir(os.path.join(base, n))) if os.path.isdir(os.path.join(base, n, c))]
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

    def test_stays(self):
        st = common.inferred_stays({"Mar 02, 2026": ["2026-03-02T06:00:00", "2026-03-04T06:00:00"]},
                                   ["2026-05-01T05:00:00", "2026-05-03T05:00:00", "2026-06-20T05:00:00"], "Other Clinic")
        self.assertEqual([(s["t"], s["end"]) for s in st], [("2026-03-02T00:00:00", "2026-03-04T23:59:00"),
                                                            ("2026-05-01T00:00:00", "2026-05-03T23:59:00"),
                                                            ("2026-06-20T00:00:00", "2026-06-20T23:59:00")])
        self.assertEqual(st[0]["title"], "Hospital stay · Other Clinic")
        s = common.stay("2026-03-01", "2026-03-05", "Northfield General Hospital", "", "Visit list")
        self.assertEqual((s["t"], s["end"], s["sub"]), ("2026-03-01T00:00:00", "2026-03-05T23:59:00", "Northfield General Hospital · 4 nights"))


class BuildUnchanged(unittest.TestCase):
    """build.py on a small fictional Epic export writes exactly tests/fixtures/build/expected.json (bar the time)."""

    def test_output(self):
        if os.path.exists(os.path.join(ROOT, "corrections.json")):
            self.skipTest("a corrections.json next to build.py changes its output")
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "data.json")
            env = {k: v for k, v in os.environ.items() if k != "RECORDS_ROOT"}
            subprocess.run([sys.executable, "-I", os.path.join(ROOT, "build.py"), "--config",
                            os.path.join(FIXTURES, "build", "sources.json"), "--out", out],
                           check=True, stdout=subprocess.DEVNULL, env=env)
            with open(out, "rb") as f:
                got = re.sub(rb'^\{"built":"[^"]*",', b"{", f.read())
        with open(os.path.join(FIXTURES, "build", "expected.json"), "rb") as f:
            self.assertEqual(got, f.read())


if __name__ == "__main__":
    unittest.main()
