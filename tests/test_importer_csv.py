#!/usr/bin/env python3
"""Tests for the generic CSV lab importer (server/importers/csv_labs.py), on fictional spreadsheets in
tests/fixtures/csv/ and a few written on the fly.

  python3 -m pytest tests/test_importer_csv.py
"""
import os, shutil, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "csv")
sys.path.insert(0, ROOT)

from server.importers import csv_labs   # noqa: E402


def fixture(*names):
    return {n: os.path.join(FIX, n) for n in names}


def series(data, name):
    return next(s for s in data["labs"] if s["name"] == name)


class Written(unittest.TestCase):
    """Helpers for files written in a temporary folder."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sanguity-csv-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, rel, text, encoding="utf-8"):
        path = os.path.join(self.dir, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding=encoding, newline="") as f:
            f.write(text)
        return {rel: path}


class Contract(unittest.TestCase):
    def test_names(self):
        self.assertEqual(csv_labs.NAME, "csv")
        self.assertTrue(csv_labs.LABEL)

    def test_template_imports(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "template.csv")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(csv_labs.TEMPLATE)
        self.assertGreaterEqual(csv_labs.detect({"template.csv": p}), 0.5)
        out = csv_labs.parse({"template.csv": p})
        self.assertEqual(series(out["data"], "LDL Cholesterol")["pts"][0][4], "H")


class Detect(Written):
    def test_lab_layouts_score(self):
        for name in ("us_comma.csv", "european_semicolon.csv", "tab_iso.tsv", "excel_utf16.txt", "ambiguous_dates.csv"):
            with self.subTest(name):
                score = csv_labs.detect(fixture(name))
                self.assertGreaterEqual(score, 0.5)
                self.assertLess(score, 1.0)   # a source-specific importer should win

    def test_fuller_header_scores_higher(self):
        bare = self.write("a.csv", "Date,Test,Value\n")
        full = self.write("b.csv", "Date,Test,Value,Unit,Reference range,Flag\n")
        self.assertGreater(csv_labs.detect(full), csv_labs.detect(bare))

    def test_not_labs(self):
        self.assertEqual(csv_labs.detect(fixture("not_labs.csv")), 0.0)
        self.assertEqual(csv_labs.detect(fixture("odd_headers.csv")), 0.0)
        self.assertEqual(csv_labs.detect({}), 0.0)
        self.assertEqual(csv_labs.detect(self.write("notes.md", "Date,Test,Value\n")), 0.0)   # not a spreadsheet

    def test_reads_only_the_start(self):
        big = self.write("big.csv", "Date,Test,Value,Unit\n" + "2025-01-01,Sodium,140,mmol/L\n" * 200000)
        self.assertGreater(os.path.getsize(next(iter(big.values()))), 16384 * 10)
        self.assertGreaterEqual(csv_labs.detect(big), 0.5)

    def test_close_miss_offers_mapper(self):
        self.assertEqual(csv_labs.detect(self.write("x.csv", "When,Test,Value\n")), 0.1)


class Shape(unittest.TestCase):
    """The data.json shape build.py writes."""

    @classmethod
    def setUpClass(cls):
        cls.out = csv_labs.parse(fixture("us_comma.csv"))
        cls.data = cls.out["data"]

    def test_top_level(self):
        self.assertEqual(set(self.data), {"events", "details", "labs", "costs"})
        self.assertIsInstance(self.out["warnings"], list)

    def test_events(self):
        ids = [e["id"] for e in self.data["events"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(self.data["events"], sorted(self.data["events"], key=lambda e: e["t"]))
        for e in self.data["events"]:
            self.assertEqual(set(e) - {"abn", "hidden", "src"}, {"id", "t", "k", "title", "sub", "key"})
            self.assertIn(e["k"], ("lab", "labitem"))
            self.assertIn(e["id"], self.data["details"])

    def test_labitems_per_test_and_day(self):
        items = [e for e in self.data["events"] if e["k"] == "labitem"]
        self.assertTrue(all(e["hidden"] == 1 for e in items))
        self.assertEqual(sorted((e["t"][:10], e["title"]) for e in items),
                         [("2025-03-14", "HDL Cholesterol"), ("2025-03-14", "Hemoglobin"),
                          ("2025-03-14", "LDL Cholesterol"), ("2025-03-14", "Platelet Count"),
                          ("2025-03-14", "Urine Protein"), ("2025-04-02", "CRP"), ("2025-04-02", "Hemoglobin")])
        hb = next(e for e in items if e["title"] == "Hemoglobin" and e["t"].startswith("2025-03-14"))
        det = self.data["details"][hb["id"]]
        self.assertEqual(det["type"], "result")
        self.assertEqual(det["order"], "CBC")
        self.assertEqual(det["rows"], [["Hemoglobin", "12.9", "g/dL", "13.5 - 17.5", "L"]])
        self.assertEqual(det["report"], [])
        self.assertEqual((hb["abn"], hb["sub"]), (1, "CBC"))
        self.assertRegex(hb["key"], r"^csv:h:[0-9a-f]{16}$")

    def test_lab_day_links_its_items(self):
        days = [e for e in self.data["events"] if e["k"] == "lab"]
        self.assertEqual(len(days), 2)
        first = days[0]
        self.assertEqual(first["key"], "csv:labday:2025-03-14")
        self.assertEqual(first["sub"], "CBC, Lipid Panel, Urinalysis")
        det = self.data["details"][first["id"]]
        self.assertEqual(det["type"], "labday")
        self.assertEqual(len(det["ids"]), 5)
        for i in det["ids"]:
            self.assertEqual(self.data["details"][i]["type"], "result")
        self.assertEqual(first["title"], "5 lab results")
        self.assertEqual(first["src"], "CSV")
        self.assertEqual(first["abn"], 3)   # low hemoglobin, high platelets, high LDL

    def test_series(self):
        for s in self.data["labs"]:
            self.assertEqual(set(s), {"name", "key", "unit", "panel", "n", "pts", "txt"})
            self.assertEqual(s["n"], len(s["pts"]))
            for p in s["pts"]:
                self.assertEqual(len(p), 7 + bool(p[7:]))
                self.assertIsInstance(p[1], (int, float))
                self.assertEqual(p[6], "CSV")
                self.assertIn(p[7:], ([], ["<"], [">"]))   # an edge left out of a one-sided range (#780)
        hb = series(self.data, "Hemoglobin")
        self.assertEqual(hb["unit"], "g/dL")
        self.assertEqual(hb["panel"], "CBC")
        self.assertEqual(hb["pts"], [["2025-03-14T12:00:00", 12.9, 13.5, 17.5, "L", "12.9", "CSV"],
                                     ["2025-04-02T12:00:00", 13.8, 13.5, 17.5, "", "13.8", "CSV"]])
        self.assertEqual(self.data["labs"][0]["name"], "Hemoglobin")   # most points first

    def test_text_results(self):
        up = series(self.data, "Urine Protein")
        self.assertEqual((up["n"], up["txt"]), (0, [["2025-03-14T12:00:00", "Negative"]]))


class Values(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.us = csv_labs.parse(fixture("us_comma.csv"))
        cls.eu = csv_labs.parse(fixture("european_semicolon.csv"))
        cls.tab = csv_labs.parse(fixture("tab_iso.tsv"))

    def test_comparators(self):
        crp = series(self.us["data"], "CRP")["pts"][0]
        self.assertEqual((crp[1], crp[5], crp[3], crp[4], crp[7]), (5, "<5", 10, "", "<"))
        fer = series(self.eu["data"], "Ferritin")["pts"][0]
        self.assertEqual((fer[1], fer[5], fer[4]), (1000, ">1000", "H"))
        vit = series(self.tab["data"], "Vitamin D")["pts"][0]
        self.assertEqual((vit[1], vit[5], vit[4]), (150, ">150", "H"))

    def test_one_sided_ranges(self):
        ldl = series(self.us["data"], "LDL Cholesterol")["pts"][0]
        self.assertEqual(ldl[2:5] + ldl[7:], [None, 100, "H", "<"])   # "<100": 100 itself is outside it (#780)
        hdl = series(self.us["data"], "HDL Cholesterol")["pts"][0]
        self.assertEqual(hdl[2:5] + hdl[7:], [40, None, "", ">"])   # ">40"
        rows = [("Date", "Test", "Value", "Unit", "Reference range"), ("2025-03-14", "LDL Cholesterol", "100", "mg/dL", "<100"),
                ("2025-03-15", "LDL Cholesterol", "100", "mg/dL", "<=100"), ("2025-03-16", "LDL Cholesterol", "100", "mg/dL", "up to 100"),
                ("2025-03-17", "LDL Cholesterol", "100", "mg/dL", "less than 100"), ("2025-03-14", "eGFR", "60", "mL/min", ">60"),
                ("2025-03-15", "eGFR", "60", "mL/min", ">= 60"), ("2025-03-16", "eGFR", "<=60", "mL/min", ">60")]
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "edges.csv"), "w", encoding="utf-8") as f:
                f.write("".join(",".join(r) + "\n" for r in rows))
            got = csv_labs.parse({"edges.csv": os.path.join(d, "edges.csv")})["data"]
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual([[p[3], p[4]] + p[7:] for p in series(got, "LDL Cholesterol")["pts"]],
                         [[100, "H", "<"], [100, ""], [100, ""], [100, "H", "<"]], "at the edge: high where it's left out (#780)")
        self.assertEqual([[p[2], p[4]] + p[7:] for p in series(got, "eGFR")["pts"]], [[60, "L", ">"], [60, ""], [60, "L", ">"]])

    def test_thousands_separator(self):
        self.assertEqual(series(self.us["data"], "Platelet Count")["pts"][0][1], 1200)

    def test_decimal_commas_and_low_high(self):
        hb = series(self.eu["data"], "Hämoglobin")["pts"]
        self.assertEqual(hb[0], ["2025-03-14T08:15:00", 14.2, 13.5, 17.5, "", "14,2", "CSV"])
        self.assertEqual(hb[1][4], "L")
        self.assertEqual(series(self.eu["data"], "Kreatinin")["pts"][0][4], "H")   # from the limits, no flag given
        row = next(d for d in self.eu["data"]["details"].values() if d["type"] == "result")
        self.assertEqual(row["rows"][0][3], "13.5 - 17.5")   # range text built from low and high

    def test_quoted_field_and_title_line(self):
        self.assertEqual(series(self.eu["data"], "TSH; basal")["pts"][0][1], 2.1)
        self.assertEqual(self.eu["warnings"], [])

    def test_value_with_unit_in_one_cell(self):
        g = series(self.tab["data"], "Glucose, Blood")
        self.assertEqual(g["unit"], "mmol/L")
        self.assertEqual([p[1:5] for p in g["pts"]], [[5.2, 3.9, 5.5, ""], [6.1, 3.9, 5.5, "H"]])

    def test_titer_is_text(self):
        ana = series(self.tab["data"], "ANA titer")
        self.assertEqual((ana["pts"], ana["txt"][0][1]), ([], "1:160"))

    def test_skipped_rows_warned(self):
        w = " ".join(self.us["warnings"])
        self.assertIn("no test name: us_comma.csv line 10", w)
        self.assertIn("couldn't be read: us_comma.csv line 11 ('04/31/2025')", w)
        self.assertIn("no value: us_comma.csv line 12", w)
        self.assertIn("'yesterday'", " ".join(self.tab["warnings"]))


class Dates(Written):
    def test_iso_forms(self):
        f = self.write("d.csv", "Date,Test,Value\n2025-03-14,A,1\n2025/03/15 07:05,A,2\n20250316,A,3\n"
                                "2025-03-17T08:00:00Z,A,4\n2025-03-18 08:00:00+0100,A,5\n")
        pts = series(csv_labs.parse(f)["data"], "A")["pts"]
        self.assertEqual([p[0] for p in pts], ["2025-03-14T12:00:00", "2025-03-15T07:05:00", "2025-03-16T12:00:00",
                                               "2025-03-17T08:00:00+00:00", "2025-03-18T08:00:00+01:00"])

    def test_month_names_and_time_column(self):
        out = csv_labs.parse(fixture("excel_utf16.txt"))
        self.assertEqual([p[0] for p in series(out["data"], "Triglycerides")["pts"]],
                         ["2025-01-15T12:00:00", "2025-01-22T12:00:00"])
        f = self.write("t.csv", "Date,Time,Test,Value\n14 March 2025,2:30 PM,A,1\n")
        self.assertEqual(series(csv_labs.parse(f)["data"], "A")["pts"][0][0], "2025-03-14T14:30:00")

    def test_file_settles_day_or_month_first(self):
        us = self.write("us.csv", "Date,Test,Value\n03/04/2025,A,1\n03/14/2025,A,2\n")
        out = csv_labs.parse(us)
        self.assertEqual([p[0][:10] for p in series(out["data"], "A")["pts"]], ["2025-03-04", "2025-03-14"])
        self.assertEqual(out["warnings"], [])
        eu = self.write("eu.csv", "Date,Test,Value\n03/04/2025,A,1\n14/03/2025,A,2\n")
        out = csv_labs.parse(eu)
        self.assertEqual([p[0][:10] for p in series(out["data"], "A")["pts"]], ["2025-03-14", "2025-04-03"])
        self.assertEqual(out["warnings"], [])

    def test_ambiguous_dates_warned(self):
        out = csv_labs.parse(fixture("ambiguous_dates.csv"))
        self.assertEqual([p[0][:10] for p in series(out["data"], "Sodium, Blood")["pts"]], ["2025-03-04", "2025-05-06"])
        self.assertEqual(len(out["warnings"]), 1)
        self.assertIn("month/day", out["warnings"][0])

    def test_dotted_dates_day_first(self):
        f = self.write("d.csv", "Date;Test;Value\n03.04.2025;A;1\n")
        out = csv_labs.parse(f)
        self.assertEqual(series(out["data"], "A")["pts"][0][0][:10], "2025-04-03")
        self.assertIn("day/month", out["warnings"][0])

    def test_mixed_orders_skip_the_unclear(self):
        f = self.write("m.csv", "Date,Test,Value\n14/03/2025,A,1\n03/14/2025,A,2\n05/06/2025,A,3\n")
        out = csv_labs.parse(f)
        self.assertEqual(len(series(out["data"], "A")["pts"]), 2)
        w = " ".join(out["warnings"])
        self.assertIn("mixes day-first and month-first", w)
        self.assertIn("'05/06/2025'", w)


class Layouts(Written):
    def test_tab_and_bom(self):
        f = self.write("t.tsv", "﻿Date\tAnalyte\tResult\tUOM\tRef Range\n2025-02-01\tWBC\t11,4\tK/uL\t4,0 - 11,0\n")
        p = series(csv_labs.parse(f)["data"], "WBC")["pts"][0]
        self.assertEqual(p[1:5], [11.4, 4.0, 11.0, "H"])

    def test_quoted_decimal_commas_in_comma_file(self):
        f = self.write("q.csv", 'Date,Test,Value,Unit,Range\n2025-02-01,Potassium,"3,2",mmol/L,"3,5 - 5,1"\n')
        p = series(csv_labs.parse(f)["data"], "Potassium, Blood")["pts"][0]
        self.assertEqual(p[1:5], [3.2, 3.5, 5.1, "L"])

    def test_flag_column_wins_and_words(self):
        f = self.write("f.csv", "Date,Test,Value,Range,Flag\n2025-01-01,A,5,1-10,High\n2025-01-01,B,0.5,1-10,A\n"
                                "2025-01-01,C,5,1-10,N\n2025-01-01,D,18.1 H,,\n")
        d = csv_labs.parse(f)["data"]
        self.assertEqual([series(d, n)["pts"][0][4] for n in "ABCD"], ["H", "L", "", "H"])

    def test_one_test_in_two_units(self):
        f = self.write("u.csv", "Date,Test,Value,Unit\n2025-01-01,Glucose,95,mg/dL\n2025-02-01,Glucose,100,mg/dL\n"
                                "2025-03-01,Glucose,5.4,mmol/L\n")
        d = csv_labs.parse(f)["data"]
        self.assertEqual(series(d, "Glucose, Blood")["n"], 2)
        self.assertEqual(series(d, "Glucose, Blood (mmol/l)")["unit"], "mmol/L")

    def test_several_files_and_duplicates(self):
        files = {**self.write("a/one.csv", "Date,Test,Value\n2025-01-01,A,1\n2025-01-02,A,2\n"),
                 **self.write("b/two.csv", "Date,Test,Value\n2025-01-02,A,2\n2025-01-03,A,3\n"),
                 **self.write("b/readme.csv", "Name,Email\n")}
        out = csv_labs.parse(files)
        self.assertEqual([p[1] for p in series(out["data"], "A")["pts"]], [1, 2, 3])
        w = " ".join(out["warnings"])
        self.assertIn("appeared twice", w)
        self.assertIn("Not read: b/readme.csv", w)

    def test_lab_column_in_detail(self):
        f = self.write("l.csv", "Date,Test,Value,Performing Lab\n2025-01-01,A,1,Fictional Labs\n")
        d = csv_labs.parse(f)["data"]
        item = next(e for e in d["events"] if e["k"] == "labitem")
        self.assertEqual(d["details"][item["id"]]["lab"], "Fictional Labs")
        self.assertIn("Fictional Labs", item["sub"])

    def test_many_bad_rows_fold_into_one_warning(self):
        f = self.write("b.csv", "Date,Test,Value\n" + "2025-01-01,A,\n" * 12 + "2025-01-01,A,1\n")
        w = csv_labs.parse(f)["warnings"]
        self.assertEqual(len(w), 1)
        self.assertIn("Skipped 12 rows with no value", w[0])
        self.assertIn("and 7 more", w[0])


class Keys(Written):
    """Stable source keys, so a re-import matches instead of duplicating."""

    ROWS = [("2025-03-14", "Hemoglobin", "12.9", "g/dL", "CBC"), ("2025-03-14", "Platelet Count", "250", "K/uL", "CBC"),
            ("2025-04-02", "CRP", "<5", "mg/L", ""), ("2025-04-02", "Urine Protein", "Negative", "", "")]

    @staticmethod
    def keys(data):
        return ({e["key"]: (e["k"], e["title"]) for e in data["events"]}, {s["key"]: s["name"] for s in data["labs"]})

    def test_reordered_file_with_other_delimiter_same_keys(self):
        a = self.write("a.csv", "Date,Test,Value,Unit,Panel\n" + "".join(",".join(r) + "\n" for r in self.ROWS))
        # other columns order, semicolons, US dates, other test-name case and spacing, rows reversed, an extra column
        b = self.write("export/b.csv", "Panel;Units;Result;Analyte;Collected;Notes\n" + "".join(
            f"{p};{u};{v};  {t.upper()} ;{d[5:7]}/{d[8:]}/{d[:4]};x\n" for d, t, v, u, p in reversed(self.ROWS)))
        ka, kb = self.keys(csv_labs.parse(a)["data"]), self.keys(csv_labs.parse(b)["data"])
        self.assertEqual(set(ka[0]), set(kb[0]))
        self.assertEqual(len(ka[0]), 6)   # four labitems, two days
        self.assertEqual(set(ka[1]), set(kb[1]))
        self.assertIn("csv:labday:2025-04-02", ka[0])
        self.assertIn("loinc:718-7", ka[1])   # hemoglobin: lab-aliases.json gives the code

    def test_extended_file_keeps_old_keys(self):
        a = self.write("a.csv", "Date,Test,Value,Unit\n2025-01-01,A,1,mg\n")
        b = self.write("b.csv", "Date,Test,Value,Unit\n2025-01-01,A,1,mg\n2025-01-01,B,2,mg\n2025-02-01,A,3,mg\n")
        ka, kb = self.keys(csv_labs.parse(a)["data"]), self.keys(csv_labs.parse(b)["data"])
        self.assertLessEqual(set(ka[0]), set(kb[0]))
        self.assertEqual(len(kb[0]), 5)

    def test_changed_value_new_key(self):
        a = self.write("a.csv", "Date,Test,Value\n2025-01-01,A,1\n")
        b = self.write("b.csv", "Date,Test,Value\n2025-01-01,A,1.5\n")
        item = lambda f: next(k for k, v in self.keys(csv_labs.parse(f)["data"])[0].items() if v[0] == "labitem")  # noqa: E731
        self.assertNotEqual(item(a), item(b))

    def test_duplicate_rows_kept_once(self):
        f = self.write("d.csv", "Date,Test,Value\n2025-01-01,A,1\n2025-01-01, a ,1\n")
        out = csv_labs.parse(f)
        self.assertEqual(sum(e["k"] == "labitem" for e in out["data"]["events"]), 1)
        self.assertIn("appeared twice", " ".join(out["warnings"]))

    def test_loinc_series_key(self):
        f = self.write("l.csv", "Date,Test,Value,Unit,LOINC Code\n2025-01-01,Hemoglobin,14,g/dL,718-7\n")
        self.assertEqual(csv_labs.parse(f)["data"]["labs"][0]["key"], "loinc:718-7")


class Mapping(unittest.TestCase):
    def test_by_header(self):
        out = csv_labs.parse(fixture("odd_headers.csv"),
                             mapping={"date": "When", "test": "What", "value": "How much", "unit": "Measured in"})
        self.assertEqual(series(out["data"], "Magnesium")["pts"][0][:2], ["2025-06-01T12:00:00", 1.9])
        self.assertEqual(series(out["data"], "Magnesium")["unit"], "mg/dL")

    def test_by_index(self):
        out = csv_labs.parse(fixture("odd_headers.csv"), mapping={"date": 0, "test": 1, "value": 2})
        self.assertEqual(series(out["data"], "Magnesium")["n"], 1)

    def test_unknown_column(self):
        with self.assertRaisesRegex(ValueError, "no column called 'Wann'"):
            csv_labs.parse(fixture("odd_headers.csv"), mapping={"date": "Wann", "test": "What", "value": "How much"})


class Errors(Written):
    def test_missing_column_named(self):
        with self.assertRaises(ValueError) as cm:
            csv_labs.parse(fixture("odd_headers.csv"))
        msg = str(cm.exception)
        self.assertIn("no column for the date and the test name and the result value", msg)
        self.assertIn("Columns found: When, What, How much, Measured in", msg)
        f = self.write("x.csv", "Date,Test,Units\n2025-01-01,A,mg\n")
        with self.assertRaisesRegex(ValueError, "no column for the result value"):
            csv_labs.parse(f)

    def test_nothing_to_read(self):
        with self.assertRaisesRegex(ValueError, "no spreadsheet"):
            csv_labs.parse(self.write("notes.pdf", "%PDF"))
        with self.assertRaisesRegex(ValueError, "is empty"):
            csv_labs.parse(self.write("e.csv", ""))

    def test_no_readable_rows(self):
        with self.assertRaisesRegex(ValueError, "No lab results could be read.*no value"):
            csv_labs.parse(self.write("e.csv", "Date,Test,Value\n2025-01-01,A,\n"))


class Contract(unittest.TestCase):
    def test_output_passes_the_registry_check(self):   # the shape the app stores (server/importers/__init__.py)
        from server import importers as registry
        for name in ['ambiguous_dates.csv', 'european_semicolon.csv', 'excel_utf16.txt', 'tab_iso.tsv', 'us_comma.csv']:
            with self.subTest(name):
                files = {name: os.path.join(FIX, name)}
                self.assertEqual(registry.check(csv_labs.parse(files), files), [])


if __name__ == "__main__":
    unittest.main()
