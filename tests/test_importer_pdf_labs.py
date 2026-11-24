#!/usr/bin/env python3
"""PDF lab report importer (issue #95): server/importers/pdf_labs.py and its layouts in server/importers/pdf_layouts/.

The fixtures are fictional Quest-like and Labcorp-like reports written by tests/fixtures/importers/pdf-labs/generate.py,
whose tables are the source of truth here: every value, unit, range and flag printed in the PDF must come back.

  python3 tests/test_importer_pdf_labs.py
"""
import hashlib, os, re, shutil, sys, tempfile, types, unittest, zlib
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import importers as reg   # noqa: E402
from server.importers import pdf_labs, pdf_layouts   # noqa: E402

BASE = os.path.join(ROOT, "tests", "fixtures", "importers", "pdf-labs")
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
gen = types.ModuleType("pdf_fixture_generator")   # run from source: no __pycache__ among the fixtures
gen.__file__ = os.path.join(BASE, "generate.py")
with open(gen.__file__, encoding="utf-8") as _f:
    exec(compile(_f.read(), gen.__file__, "exec"), gen.__dict__)


def rows_of(out):
    """{(order, test): [name, value, unit, range, flag]} from the labitems."""
    d = out["data"]
    return {(e["title"], r[0]): r for e in d["events"] if e["k"] == "labitem" for r in d["details"][e["id"]]["rows"]}


_STREAM = re.compile(rb"stream\n(.*?)\nendstream", re.S)


def pdf_content(raw):
    """What a fixture PDF holds, whatever zlib compressed it: its objects with every stream decompressed, and without
    the stream lengths and byte offsets that depend on how well the streams compressed (zlib builds differ)."""
    parts = _STREAM.split(raw)   # [text, stream, text, stream, ..., text]
    out = []
    for i, part in enumerate(parts):
        if i % 2:
            out.append(zlib.decompress(part))
        else:
            part = re.sub(rb"/Length \d+", b"/Length ?", part)
            part = re.sub(rb"xref\n.*?trailer", b"xref ? trailer", part, flags=re.S)
            out.append(re.sub(rb"startxref\n\d+", b"startxref ?", part))
    return out


class Base(unittest.TestCase):
    def setUp(self):
        pdf_labs._cache.clear()
        self.tmp = tempfile.mkdtemp(prefix="sanguity-pdf-labs-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pdf(self, name, pages=None, image=None):
        path = os.path.join(self.tmp, name)
        gen.write(path, pages or [], image=image)
        return path

    def run_files(self, **named):
        return reg.run({rel: path for rel, path in named.items()})


class Reports(Base):
    def test_quest_every_row_matches_the_pdf(self):
        out = reg.run(reg.files_in(os.path.join(BASE, "quest")))
        self.assertEqual(out["importer"], "pdf-labs")
        self.assertEqual(out["warnings"], [])
        got, want, panel = rows_of(out), {}, ""
        for r in gen.QUEST_ROWS:
            if r == "PAGE":
                continue
            if r[1] is None:
                panel = r[0]
                continue
            name, value, flag, rng, unit = r
            order = name if name in gen.QUEST_LONE else panel
            want[(order, name)] = [name, value, unit.replace(" (calc)", ""), rng, flag]
        self.assertEqual(got, want)
        self.assertEqual(len(got), 27)
        d = out["data"]
        item = next(e for e in d["events"] if e["title"] == "COMPREHENSIVE METABOLIC PANEL")
        det = d["details"][item["id"]]
        self.assertEqual(item["t"], "2026-03-14T08:32:00-04:00")   # EDT kept as an offset
        self.assertEqual((det["provider"], det["specimen"], det["lab"]), ("LIN, AVERY", "ZX918273A", "Quest Diagnostics"))
        self.assertEqual(item["key"], "pdf-labs:quest:ZX918273A|comprehensive metabolic panel")
        self.assertEqual(item["abn"], 2)
        labs = {s["name"]: s for s in d["labs"]}
        self.assertEqual(labs["Creatinine"]["pts"], [["2026-03-14T08:32:00-04:00", 1.32, 0.5, 1.05, "H", "1.32", "Quest"]])
        self.assertEqual(labs["Creatinine"]["key"], "loinc:2160-0")
        self.assertEqual(labs["BUN"]["pts"][0][1:4], [14, 7, 25])       # "UREA NITROGEN (BUN)", by lab-aliases.json
        self.assertEqual(labs["EGFR"]["pts"][0][1:5] + labs["EGFR"]["pts"][0][7:], [47, 60, None, "L"])   # "> OR = 60", 60 kept in it
        self.assertEqual(labs["TSH"]["pts"][0][1:6], [0.01, 0.4, 4.5, "L", "<0.01"])
        self.assertEqual(labs["HEPATITIS C ANTIBODY"]["txt"], [["2026-03-14T08:32:00-04:00", "NON-REACTIVE"]])
        day = next(e for e in d["events"] if e["k"] == "lab")
        self.assertEqual((day["title"], day["key"], day["abn"]), ("27 lab results", "pdf-labs:labday:2026-03-14", 6))
        doc = next(e for e in d["events"] if e["k"] == "document")
        self.assertEqual(d["details"][doc["id"]], {"type": "file", "url": "quest-report.pdf", "ext": "pdf"})
        with open(os.path.join(BASE, "quest", "quest-report.pdf"), "rb") as f:
            self.assertEqual(doc["key"], "pdf-labs:file:" + hashlib.sha256(f.read()).hexdigest()[:16])

    def test_labcorp_every_row_matches_the_pdf(self):
        out = reg.run(reg.files_in(os.path.join(BASE, "labcorp")))
        self.assertEqual((out["importer"], out["warnings"]), ("pdf-labs", []))
        got, want, panel = rows_of(out), {}, ""
        for r in gen.LABCORP_ROWS:
            if r == "PAGE":
                continue
            if r[1] is None:
                panel = r[0]
                continue
            name, value, flag, unit, rng = r
            want[(panel, name)] = [name, value, unit, rng, flag]
        self.assertEqual(got, want)
        self.assertEqual(len(got), 21)
        d = out["data"]
        labs = {s["name"]: s for s in d["labs"]}
        self.assertEqual(labs["RBC"]["pts"], [["2026-05-02T07:45:00-05:00", 4.09, 4.14, 5.8, "L", "4.09", "Labcorp"]])
        self.assertEqual(labs["Glucose, Blood"]["pts"][0][4], "H")   # "High"
        self.assertEqual(labs["Neutrophils"]["pts"][0][2:4], [None, None])   # "Not Estab."
        self.assertEqual(labs["Platelet Count"]["key"], "loinc:777-3")   # "Platelets"
        item = next(e for e in d["events"] if e["title"] == "Lipid Panel")
        self.assertEqual(d["details"][item["id"]]["provider"], "P SALAZAR")
        self.assertEqual(item["key"], "pdf-labs:labcorp:126-418-2290-0|lipid panel")

    def test_the_same_upload_parses_the_same(self):
        a = pdf_labs.parse(reg.files_in(os.path.join(BASE, "quest")))
        pdf_labs._cache.clear()
        b = pdf_labs.parse(reg.files_in(os.path.join(BASE, "quest")))
        self.assertEqual(a, b)

    def test_both_labs_in_one_upload(self):
        files = {"q.pdf": os.path.join(BASE, "quest", "quest-report.pdf"),
                 "l.pdf": os.path.join(BASE, "labcorp", "labcorp-report.pdf"),
                 "copy/q.pdf": os.path.join(BASE, "quest", "quest-report.pdf")}
        out = reg.run(files)
        self.assertEqual(reg.check({"data": out["data"], "warnings": out["warnings"]}, files), [])
        self.assertEqual(out["warnings"], ["q.pdf is the same file as copy/q.pdf and was read once."])
        self.assertEqual(len(rows_of(out)), 48)
        self.assertEqual(len([e for e in out["data"]["events"] if e["k"] == "lab"]), 2)
        hgb = next(s for s in out["data"]["labs"] if s["name"] == "Hemoglobin")
        self.assertEqual([p[6] for p in hgb["pts"]], ["Quest", "Labcorp"])


class NeverGuess(Base):
    def test_lines_the_layout_doesnt_read_are_warnings(self):
        rows = list(gen.QUEST_ROWS)
        rows.insert(3, "Specimen slightly hemolyzed; repeat if needed")          # a comment under a result
        rows.insert(5, ("FERRITIN", "SEE NOTE", "", "", "ng/mL"))                  # no value
        rows.insert(6, "MAGNESIUM 2.0 1.5-2.5 mg/dL AMD")                         # columns run together
        path = self.pdf("report.pdf", gen.quest(rows))
        out = self.run_files(**{"report.pdf": path})
        self.assertEqual(len(out["warnings"]), 1, out["warnings"])
        w = out["warnings"][0]
        self.assertTrue(w.startswith("report.pdf: 3 lines in the results table didn't match the Quest Diagnostics layout"), w)
        for where in ("page 1 line 14", "page 1 line 16", "page 1 line 17"):
            self.assertIn(where, w)
        self.assertIn('"MAGNESIUM 2.0 1.5-2.5 mg/dL AMD"', w)
        self.assertIn('"FERRITIN SEE NOTE ng/mL AMD"', w)
        names = {k[1] for k in rows_of(out)}
        self.assertNotIn("FERRITIN", names)
        self.assertFalse({n for n in names if "MAGNESIUM" in n or "hemolyzed" in n})
        self.assertEqual(len(names), 27)

    def test_no_collection_date(self):
        path = self.pdf("report.pdf", gen.quest(collected="pending"))
        with self.assertRaises(ValueError) as e:
            self.run_files(**{"report.pdf": path})
        self.assertIn("looks like a Quest Diagnostics report, but no results could be read", str(e.exception))

    def test_unrecognised_pdf_is_refused(self):
        files = {"unrecognised.pdf": os.path.join(BASE, "unrecognised.pdf")}
        self.assertEqual(reg.match(files)["importer"].NAME, "pdf-labs")   # its message, not "nothing matched"
        with self.assertRaises(ValueError) as e:
            reg.run(files)
        self.assertEqual(str(e.exception), "unrecognised.pdf isn't a lab report layout Sanguity knows. It can read "
                         "reports from Labcorp, Quest Diagnostics. If yours is from one of those labs, its layout may "
                         "have changed: please open an issue with an anonymised sample.")

    def test_scanned_pdf_is_refused(self):
        with self.assertRaises(ValueError) as e:
            reg.run({"scan.pdf": os.path.join(BASE, "scanned.pdf")})
        self.assertIn("scan.pdf has no text in it: it looks like a scan", str(e.exception))
        self.assertIn("can't read scanned reports yet", str(e.exception))

    def test_a_refused_pdf_beside_a_report_is_a_warning(self):
        out = reg.run({"a.pdf": os.path.join(BASE, "quest", "quest-report.pdf"), "b.pdf": os.path.join(BASE, "scanned.pdf")})
        self.assertEqual(len(out["warnings"]), 1)
        self.assertTrue(out["warnings"][0].startswith("Not read: b.pdf has no text in it"))


class Hostile(Base):
    def test_not_a_pdf(self):
        path = os.path.join(self.tmp, "x.pdf")
        with open(path, "wb") as f:
            f.write(b"MZ\x90\x00 not a pdf")
        self.assertEqual(pdf_labs.detect({"x.pdf": path}), 0.0)
        with self.assertRaises(ValueError) as e:
            pdf_labs.parse({"x.pdf": path})
        self.assertIn("isn't a PDF", str(e.exception))

    def test_damaged_pdf(self):
        path = os.path.join(self.tmp, "x.pdf")
        with open(path, "wb") as f:
            f.write(b"%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 9 0 R >> garbage\n%%EOF")
        with self.assertRaises(ValueError) as e:
            pdf_labs.parse({"x.pdf": path})
        self.assertIn("x.pdf", str(e.exception))

    def test_limits(self):
        files = {"r.pdf": os.path.join(BASE, "quest", "quest-report.pdf")}
        with mock.patch.object(pdf_labs, "MAX_BYTES", 1000):
            with self.assertRaises(ValueError) as e:
                pdf_labs.parse(files)
            self.assertIn("larger than 0 MB", str(e.exception))
        with mock.patch.object(pdf_labs, "MAX_PAGES", 1):
            with self.assertRaises(ValueError) as e:
                pdf_labs.parse(files)
            self.assertIn("has 2 pages; lab reports of more than 1 pages aren't read", str(e.exception))
        pdf_labs._cache.clear()
        with mock.patch.object(pdf_labs, "TIMEOUT", 0.001):
            with self.assertRaises(ValueError) as e:
                pdf_labs.parse(files)
            self.assertIn("took more than 0.001 seconds to read", str(e.exception))

    def test_password_protected(self):
        import pypdf
        w = pypdf.PdfWriter(clone_from=os.path.join(BASE, "quest", "quest-report.pdf"))
        w.encrypt("a fictional password")
        path = os.path.join(self.tmp, "locked.pdf")
        with open(path, "wb") as f:
            w.write(f)
        with self.assertRaises(ValueError) as e:
            pdf_labs.parse({"locked.pdf": path})
        self.assertIn("locked.pdf is password-protected", str(e.exception))


class Detect(Base):
    def test_only_uploads_of_pdfs(self):
        q = os.path.join(BASE, "quest", "quest-report.pdf")
        self.assertEqual(pdf_labs.detect({"q.pdf": q}), 0.9)
        self.assertEqual(pdf_labs.detect({"q.pdf": q, "__MACOSX/._q.pdf": q, ".DS_Store": q}), 0.9)   # junk ignored
        self.assertEqual(pdf_labs.detect({"q.pdf": q, "notes.txt": q}), 0.0)
        self.assertEqual(pdf_labs.detect({}), 0.0)
        self.assertEqual(pdf_labs.detect({"u.pdf": os.path.join(BASE, "unrecognised.pdf")}), 0.3)

    def test_pdfs_inside_other_exports_are_left_to_their_importers(self):
        for case in ("ccda/maple-grove-ccd", "fhir/bundle", "carespace/full", "epic-mychart/willowbrook-export",
                     "sanguity/export"):
            with self.subTest(case=case):
                files = reg.files_in(os.path.join(FIXTURES, "importers", *case.split("/")))
                self.assertEqual(pdf_labs.detect(files), 0.0)
                self.assertNotEqual(reg.match(files)["importer"].NAME, "pdf-labs")
        self.assertEqual(pdf_labs.detect(reg.files_in(os.path.join(FIXTURES, "build"))), 0.0)
        self.assertTrue(any(p.endswith(".pdf") for p in reg.files_in(os.path.join(FIXTURES, "importers", "fhir", "bundle"))))


class Layouts(unittest.TestCase):
    def test_each_layout_has_a_fixture(self):
        names = [lay.NAME for lay in pdf_layouts.layouts()]
        self.assertEqual(names, ["labcorp", "quest"])
        for n in names:
            self.assertTrue(os.path.isfile(os.path.join(BASE, n + ".expected.json")), n)

    def test_fixtures_are_what_the_generator_writes(self):
        with tempfile.TemporaryDirectory() as d:
            gen.main(d)
            for rel in ("quest/quest-report.pdf", "labcorp/labcorp-report.pdf", "unrecognised.pdf", "scanned.pdf"):
                with open(os.path.join(d, rel), "rb") as a, open(os.path.join(BASE, rel), "rb") as b:
                    self.assertEqual(pdf_content(a.read()), pdf_content(b.read()),
                                     f"{rel}: run tests/fixtures/importers/pdf-labs/generate.py")

    def test_helpers(self):
        from server.importers.pdf_layouts import bounds, is_range, is_unit, is_value, range_and_unit
        self.assertEqual([bounds(s) for s in ("65-99", "<200", "> OR = 60", ">59", "NEGATIVE", "9-3", "< OR = 5", "<=5")],
                         [(65, 99, ""), (None, 200, "<"), (60, None, ""), (59, None, ">"), (None, None, ""), (None, None, ""),
                          (None, 5, ""), (None, 5, "")])   # whether a one-sided limit leaves its edge out (#780)
        self.assertTrue(all(map(is_value, ("88", "<0.01", "1,250", "NON-REACTIVE", "2+", "1:40"))))
        self.assertFalse(any(map(is_value, ("SEE NOTE", "TNP", "", "88 H 4"))))
        self.assertTrue(all(map(is_unit, ("mg/dL", "x10E3/uL", "%", "fL", "mL/min/1.73m2"))))
        self.assertFalse(any(map(is_unit, ("NEGATIVE", "65-99"))))
        self.assertTrue(all(map(is_range, ("65-99", "Not Estab.", "Non Reactive", "<= 5"))))
        self.assertEqual(range_and_unit("<5.7 % of total Hgb"), ("<5.7", "% of total Hgb"))
        self.assertEqual(range_and_unit("mg/dL"), ("", "mg/dL"))
        self.assertEqual(range_and_unit("NEGATIVE"), ("NEGATIVE", ""))
        self.assertIsNone(range_and_unit("65-99 mg/dL <3"))


if __name__ == "__main__":
    unittest.main()
