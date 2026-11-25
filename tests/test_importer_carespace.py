"""CareSpace / OncoEMR importer (server/importers/carespace.py) against fictional exports in
tests/fixtures/importers/carespace/ (tests/test_importers.py also checks the registry picks it for each one).

The port must read an export the way build.py did (build.py now reads it through this importer, so BuildParity also
checks build.py's joining leaves one source's records alone): BuildParity runs build.py on the same fictional export (through
a sources.json in a temporary folder, beside an empty Epic export, which build.py requires) and compares. Allowed
differences: event ids, keys and document URLs.

  python -m unittest discover -s tests -p "test_importer*.py" -v
"""
import collections, json, os, re, shutil, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "importers", "carespace")
sys.path.insert(0, ROOT)

from server import importers as reg   # noqa: E402
from server.importers import carespace   # noqa: E402

ORG = "Lakeshore Oncology Associates"


def files(case):
    return reg.files_in(os.path.join(FIX, case))


def copy(case, d):
    """The fixture copied into d/export, for cases a test changes; returns its files dict."""
    dst = os.path.join(d, "export")
    shutil.copytree(os.path.join(FIX, case), dst)
    return dst


def by_kind(data, k):
    return [e for e in data["events"] if e["k"] == k]


def series(data, name):
    return next(s for s in data["labs"] if s["name"] == name)


class Detect(unittest.TestCase):
    def test_layout(self):
        self.assertEqual(carespace.detect(files("full")), 1.0)
        self.assertEqual(reg.match(files("full"))["importer"].NAME, "carespace")   # beats ccda on carespace_ccd*.xml

    def test_api_pair_without_ccd(self):
        f = {k: v for k, v in files("full").items() if not k.endswith(".xml")}
        self.assertEqual(carespace.detect(f), 0.97)

    def test_inside_a_folder(self):
        f = {"cs-export/" + k: v for k, v in files("full").items()}
        self.assertEqual(carespace.detect(f), 1.0)
        out = carespace.parse(f)
        self.assertEqual(reg.check(out, f), [])
        docs = by_kind(out["data"], "document")
        self.assertEqual(out["data"]["details"][docs[0]["id"]]["url"], "cs-export/documents/2026-03-02_Pathology_Core biopsy report.pdf")

    def test_not_carespace(self):
        self.assertEqual(carespace.detect({}), 0.0)
        f = files("full")
        self.assertEqual(carespace.detect({k: v for k, v in f.items() if k != "api/lab_panels.json"}), 0.0)
        self.assertEqual(carespace.detect({"carespace_ccd_full.xml": f["carespace_ccd_full.xml"]}), 0.0)
        demo = os.path.join(ROOT, "tests", "fixtures", "importers", "fhir", "bundle")
        self.assertEqual(carespace.detect(reg.files_in(demo)), 0.0)


class Parse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = carespace.parse(files("full"))
        cls.data = cls.out["data"]

    def test_contract(self):
        self.assertEqual(reg.check(self.out, files("full")), [])
        self.assertEqual(self.out["warnings"], [])
        again = carespace.parse(files("full"))
        self.assertEqual(json.dumps(again, sort_keys=True), json.dumps(self.out, sort_keys=True))   # deterministic
        self.assertTrue(all(e["key"].startswith("carespace:") for e in self.data["events"]))

    def test_organisation_from_the_ccd(self):
        self.assertTrue(all(e["sub"].endswith(ORG) for e in self.data["events"] if e["k"] in ("labitem", "visit", "document")))
        self.assertTrue(all(p[6] == ORG for s in self.data["labs"] for p in s["pts"]))
        self.assertEqual({p["src"] for p in self.data["problems"]}, {ORG})

    def test_labs(self):
        days = by_kind(self.data, "lab")
        self.assertEqual([(e["t"], e["title"], e.get("abn")) for e in days],
                         [("2026-03-02T08:30:00-06:00", f"9 lab results · {ORG}", 3),
                          ("2026-04-01T07:45:00-05:00", f"4 lab results · {ORG}", 1),
                          ("2026-04-12T12:00:00", f"1 lab result · {ORG}", 1)])
        cbc = self.data["details"][self.data["details"][days[0]["id"]]["ids"][0]]
        self.assertEqual(cbc["rows"][0], ["HGB", "11.2", "g/dL", "12.0-16.0", "L"])
        self.assertEqual(cbc["rows"][3][4], "L")                                  # below the range, no code given
        hb = series(self.data, "Hemoglobin")
        self.assertEqual((hb["n"], hb["loinc"], hb["key"]), (2, "718-7", "loinc:718-7"))   # same-day repeat counted once
        self.assertEqual(series(self.data, "CRP")["pts"][0][1], 0.5)              # "<0.5"
        uc = series(self.data, "Urine color")   # a text result: its series' text, as the other importers keep one (#851)
        self.assertEqual((uc["key"], uc["n"], uc["pts"], uc["txt"]), ("carespace:name|urine color", 0, [], [["2026-03-02T08:40:00-06:00", "Yellow"]]))
        glu = [s for s in self.data["labs"] if s["name"].startswith("Glucose, Blood")]
        self.assertEqual(sorted((s["name"], s["key"]) for s in glu),
                         [("Glucose, Blood", "loinc:2345-7|mg/dL"), (f"Glucose, Blood ({ORG})", "loinc:2345-7|mmol/L")])

    def test_ccd_tables(self):
        visits = {e["title"]: e for e in by_kind(self.data, "visit")}
        self.assertEqual(sorted(visits), ["Chart review", "Doctor visit", "Hospital visit", "Infusion, chemo 1st hr"])
        doc = self.data["details"][visits["Doctor visit"]["id"]]
        self.assertEqual(doc["dx"], "C50.911 Malignant neoplasm of breast")       # repeated diagnosis once
        self.assertIn("No visit took place", self.data["details"][visits["Chart review"]["id"]]["note"])
        self.assertEqual([(u["t"], u["title"]) for u in self.data["upcoming"]], [("2036-12-05", "Nurse visit")])
        self.assertEqual([e["title"] for e in by_kind(self.data, "med")], ["Ondansetron 8 mg tablet", "Dexamethasone 4 mg tablet"])
        self.assertEqual([p["name"] for p in self.data["problems"]], ["Malignant neoplasm of breast", "Iron deficiency anemia"])
        self.assertEqual(series(self.data, "Height")["pts"][0][1], 65.0)          # 165 cm
        self.assertEqual(self.data["clinicWeights"], [["2026-03-02T08:45:00", 150.1, ORG], ["2026-03-02T12:00:00", 150.0, ORG],
                                                      ["2026-04-01T12:00:00", 148.8, ORG]])

    def test_vitals_named_by_their_loinc_long_names(self):
        with tempfile.TemporaryDirectory() as d:
            dst = copy("full", d)
            p = os.path.join(dst, "carespace_ccd_full.xml")
            with open(p, encoding="utf-8") as f:
                xml = f.read()
            xml = xml.replace("<td>Systolic blood pressure</td>", "<td>Intravascular systolic Arterial system Pres Pt Qn</td>") \
                     .replace("<td>Diastolic blood pressure</td>", "<td>Intravascular diastolic Arterial system Pres Pt Qn</td>")
            self.assertIn("Intravascular diastolic", xml)
            with open(p, "w", encoding="utf-8") as f:
                f.write(xml)
            data = carespace.parse(reg.files_in(dst))["data"]
        self.assertEqual([p[1] for p in series(data, "Blood pressure, systolic")["pts"]], [118.0])
        self.assertEqual([p[1] for p in series(data, "Blood pressure, diastolic")["pts"]], [76.0])

    def test_documents(self):
        docs = by_kind(self.data, "document")
        self.assertEqual([(e["title"], e["sub"]) for e in docs],
                         [("Core biopsy report", f"Pathology · {ORG}"), ("Hospital summary", f"Discharge · {ORG}")])
        self.assertEqual(self.data["details"][docs[1]["id"]], {"type": "file", "url": "documents/2026-04-10_Discharge_Hospital summary_2.pdf", "ext": "pdf"})


class Edges(unittest.TestCase):
    def test_no_name_gets_a_default_and_a_warning(self):
        out = carespace.parse(files("unnamed"))
        self.assertEqual(reg.check(out, files("unnamed")), [])
        self.assertEqual(len(out["warnings"]), 1)
        self.assertIn(carespace.DEFAULT_ORG, out["warnings"][0])
        self.assertIn("Rename the source", out["warnings"][0])
        self.assertTrue(all(e["sub"].endswith(carespace.DEFAULT_ORG) for e in by_kind(out["data"], "labitem")))

    def test_author_organisation_when_no_custodian(self):
        with tempfile.TemporaryDirectory() as d:
            dst = copy("unnamed", d)
            p = os.path.join(dst, "carespace_ccd.xml")
            with open(p, encoding="utf-8") as f:
                x = f.read()
            x = x.replace("<component><structuredBody>", "<author><assignedAuthor><representedOrganization><name>Pinecrest "
                          "Cancer Care</name></representedOrganization></assignedAuthor></author><component><structuredBody>", 1)
            with open(p, "w", encoding="utf-8") as f:
                f.write(x)
            out = carespace.parse(reg.files_in(dst))
        self.assertEqual(out["warnings"], [])
        self.assertEqual(out["data"]["problems"][0]["src"], "Pinecrest Cancer Care")

    def test_without_ccd(self):
        f = {k: v for k, v in files("full").items() if not k.endswith(".xml")}
        out = carespace.parse(f)
        self.assertEqual(reg.check(out, f), [])
        self.assertTrue(any("no carespace_ccd.xml" in w for w in out["warnings"]))
        self.assertEqual({e["k"] for e in out["data"]["events"]}, {"lab", "labitem", "document"})

    def test_results_without_date_or_name(self):
        with tempfile.TemporaryDirectory() as d:
            dst = copy("unnamed", d)
            p = os.path.join(dst, "api", "lab_results.json")
            with open(p, encoding="utf-8") as f:
                doc = json.load(f)
            doc["data"] += [{"name": "HGB", "value": {"value": "13"}}, {"value": {"value": "13"}, "observationDate": "2026-05-01"}]
            with open(p, "w", encoding="utf-8") as f:
                json.dump(doc, f)
            out = carespace.parse(reg.files_in(dst))
        self.assertIn("1 lab result had no date and was left out.", out["warnings"])
        self.assertIn("1 lab result had no test name and was left out.", out["warnings"])

    def test_a_flag_that_says_no_way_is_kept(self):
        """#817: a report's own flag comes first. An interpretationCode "A" or "AA" (abnormal, no way) is placed by the
        range where it can be, and kept as "A" where it can't (a value inside its range, or a text result), on the point
        and the row; before, only H, HH, L and LL were read, so it was kept with none."""
        codes = {(0, "WBC"): "A", (0, "POTASSIUM"): "AA", (0, "HCT"): "A", (0, "Urine color"): "A", (0, "CREAT"): "N"}
        with tempfile.TemporaryDirectory() as d:
            dst = copy("full", d)
            p = os.path.join(dst, "api", "lab_results.json")
            with open(p, encoding="utf-8") as f:
                doc = json.load(f)
            seen = collections.Counter()
            for r in doc["data"]:
                k = (seen[r["name"]], r["name"])
                seen[r["name"]] += 1
                if k in codes:
                    r["interpretationCode"] = codes[k]
            with open(p, "w", encoding="utf-8") as f:
                json.dump(doc, f)
            out = carespace.parse(reg.files_in(dst))
        days = sorted((e for e in by_kind(out["data"], "lab")), key=lambda e: e["t"])
        rows = {r[0]: r[4] for i in out["data"]["details"][days[0]["id"]]["ids"] for r in out["data"]["details"][i]["rows"]}
        self.assertEqual({n: rows[n] for _, n in codes}, {"WBC": "A", "POTASSIUM": "A", "HCT": "L", "Urine color": "A", "CREAT": ""})
        pts = {n: series(out["data"], n)["pts"][0][4] for n in ("WBC", "Potassium, Blood", "Hematocrit", "Creatinine")}
        self.assertEqual(pts, {"WBC": "A", "Potassium, Blood": "A", "Hematocrit": "L", "Creatinine": ""})
        self.assertEqual(days[0].get("abn"), 3 + 3, "the lab day counts them: HGB, GLUCOSE and HCT were flagged before")

    def test_a_test_it_doesnt_know_is_charted_by_its_own_name(self):
        """#851: a result whose name lab-aliases.json doesn't know ("C-Reactive Protein", "eGFR") was kept in its
        report's rows alone: no series, so Labs had nothing of it and the upload said nothing. It's a series of its own
        name now, as the FHIR, C-CDA and spreadsheet importers name one (its range, flag and source as any other's), so
        it charts and the review offers to match its name; a value that isn't a number is its text result."""
        new = [{"id": "lr-901", "name": "C-Reactive Protein", "value": {"value": "12.4", "unit": "mg/L"},
                "referenceRange": {"upperLimit": 10, "unit": "mg/L"}, "referenceRangeDisplay": "<10.0", "interpretationCode": "H",
                "observationDateTz": "2026-04-01T07:45:00-05:00", "labPanelRecordId": "pn-103"},
               {"id": "lr-902", "name": "C-Reactive Protein", "value": {"value": "3,1", "unit": "mg/L"},
                "referenceRange": {"upperLimit": 10, "unit": "mg/L"}, "referenceRangeDisplay": "<10.0",
                "observationDateTz": "2026-03-02T08:30:00-06:00", "labPanelRecordId": "pn-101"},
               {"id": "lr-903", "name": "eGFR", "value": {"value": ">60", "unit": "mL/min/1.73m2"}, "referenceRangeDisplay": ">=60",
                "observationDateTz": "2026-04-01T07:45:00-05:00", "labPanelRecordId": "pn-103"},
               {"id": "lr-904", "name": "eGFR", "value": {"value": "See comment", "unit": "mL/min/1.73m2"},
                "observationDateTz": "2026-03-02T08:30:00-06:00", "labPanelRecordId": "pn-101"}]
        with tempfile.TemporaryDirectory() as d:
            dst = copy("full", d)
            p = os.path.join(dst, "api", "lab_results.json")
            with open(p, encoding="utf-8") as f:
                doc = json.load(f)
            doc["data"] += new
            with open(p, "w", encoding="utf-8") as f:
                json.dump(doc, f)
            out = carespace.parse(reg.files_in(dst))
            self.assertEqual(reg.check(out, reg.files_in(dst)), [])
        crp, egfr = series(out["data"], "C-Reactive Protein"), series(out["data"], "eGFR")
        self.assertEqual((crp["key"], crp["unit"], crp["n"]), ("carespace:name|c-reactive protein", "mg/L", 2))
        self.assertEqual([p[:7] for p in crp["pts"]], [["2026-03-02T08:30:00-06:00", 3.1, None, 10, "", "3,1", ORG],
                                                      ["2026-04-01T07:45:00-05:00", 12.4, None, 10, "H", "12.4", ORG]])
        self.assertEqual((egfr["key"], egfr["n"], [p[1:5] for p in egfr["pts"]], egfr["txt"]),
                         ("carespace:name|egfr", 1, [[60, 60, None, ""]], [["2026-03-02T08:30:00-06:00", "See comment"]]))
        self.assertEqual(series(out["data"], "CRP")["n"], 1, "a test it knows keeps its own series")
        self.assertEqual(out["warnings"], [])

    def test_a_range_given_as_text_alone(self):
        """#836: a result whose range is only displayed ("<16.0", no lowerLimit or upperLimit) is read through the one
        range parser (common.range_text), its edge kept: it was dropped, no range and no flag on Labs while the drawer
        showed the text beside the value. The row keeps the text as displayed."""
        got = []
        for value, text in (("16.0", "<16.0"), ("16.0", "less than 16.0"), ("16.0", "<=16.0"), ("12.0", ">12.0"), ("12.0", "≥ 12.0"),
                            ("11.2", "12.0-16.0 g/dL"), ("13", "See note")):
            with tempfile.TemporaryDirectory() as d:
                dst = copy("full", d)
                p = os.path.join(dst, "api", "lab_results.json")
                with open(p, encoding="utf-8") as f:
                    doc = json.load(f)
                hgb = doc["data"][0]   # the first HGB, 11.2 against 12.0-16.0
                hgb.update(value={"value": value, "unit": "g/dL"}, referenceRange={"unit": "g/dL"}, referenceRangeDisplay=text, interpretationCode=None)
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(doc, f)
                out = carespace.parse(reg.files_in(dst))
            pt = series(out["data"], "Hemoglobin")["pts"][0]
            row = next(r for e in out["data"]["details"].values() if isinstance(e, dict) for r in e.get("rows") or [] if r[0] == "HGB" and r[1] == value)
            got.append([pt[2], pt[3], pt[4], (pt[7:] or [""])[0], row[3]])
        self.assertEqual(got, [[None, 16, "H", "<", "<16.0"], [None, 16, "H", "<", "less than 16.0"], [None, 16, "", "", "<=16.0"],
                               [12, None, "L", ">", ">12.0"], [12, None, "", "", "≥ 12.0"], [12, 16, "L", "", "12.0-16.0 g/dL"],
                               [None, None, "", "", "See note"]])

    def test_repeated_rows_are_left_out_not_keyed_twice(self):
        with tempfile.TemporaryDirectory() as d:
            dst = copy("full", d)
            p = os.path.join(dst, "carespace_ccd_full.xml")
            with open(p, encoding="utf-8") as f:
                x = f.read()
            row = "<tr><td>Iron deficiency anemia</td><td>Diagnosis</td><td>2026-03-02</td><td>Active</td></tr>"
            with open(p, "w", encoding="utf-8") as f:
                f.write(x.replace(row, row * 2))
            out = carespace.parse(reg.files_in(dst))
        self.assertEqual(reg.check(out), [])
        self.assertEqual(len(out["data"]["problems"]), 2)
        self.assertIn("1 entry repeated another exactly and was left out.", out["warnings"])

    def test_unreadable_input(self):
        for name, body, msg in [("api/lab_results.json", "{not json", "isn't readable JSON"),
                                ("api/lab_panels.json", '{"rows": []}', 'no "data" list'),
                                ("carespace_ccd_full.xml", "<ClinicalDocument", "not well-formed XML"),
                                ("carespace_ccd_full.xml", '<!DOCTYPE x [<!ENTITY a "b">]><x/>', "DOCTYPE"),
                                ("carespace_ccd_full.xml", "<x/>", "not a C-CDA")]:
            with self.subTest(file=name, body=body), tempfile.TemporaryDirectory() as d:
                dst = copy("full", d)
                with open(os.path.join(dst, *name.split("/")), "w", encoding="utf-8") as f:
                    f.write(body)
                with self.assertRaisesRegex(ValueError, msg):
                    carespace.parse(reg.files_in(dst))
        with self.assertRaisesRegex(ValueError, "isn't a CareSpace export"):
            carespace.parse({})


def normalise(data, url=lambda u: u):
    """What build.py and the importer must agree on: events (without ids and keys) with their details, the lab
    series, upcoming visits, problems and clinic weights. Lab days list their items by content instead of id."""
    ev = {e["id"]: e for e in data["events"]}
    strip = lambda e: {k: v for k, v in e.items() if k not in ("id", "key")}   # noqa: E731
    out = []
    for e in data["events"]:
        d = dict(data["details"].get(e["id"]) or data["details"].get(str(e["id"])) or {})
        if d.get("type") == "labday":
            d["ids"] = [strip(ev[i]) for i in d["ids"]]
        if d.get("type") == "file":
            d["url"] = url(d["url"])
        out.append({**strip(e), "detail": d})
    return {"events": sorted(out, key=lambda x: json.dumps(x, sort_keys=True)),
            "labs": [{k: v for k, v in s.items() if k != "key"} for s in data["labs"]],
            "upcoming": [{k: v for k, v in u.items() if k != "key"} for u in data["upcoming"]],
            "problems": [{k: v for k, v in p.items() if k != "key"} for p in data["problems"]],
            "clinicWeights": data["clinicWeights"]}


class BuildParity(unittest.TestCase):
    """The plug-in reads a fictional export as build.py does (the fixture's one upcoming visit is in 2036, since
    build.py compares with today and the importer with the export's date)."""

    def run_build(self, case, org):
        if os.path.exists(os.path.join(ROOT, "corrections.json")):
            self.skipTest("a corrections.json next to build.py changes its output")
        with tempfile.TemporaryDirectory() as d:
            for sub, name, body in [("test-results", "x_test_results_raw.json", {"groups": [], "details": {}}),
                                    ("messages", "x_messages.json", {"details": {}, "list": {}}),
                                    ("letters", "x_letters.json", {"letters": []})]:
                os.makedirs(os.path.join(d, "mychart", sub))
                with open(os.path.join(d, "mychart", sub, name), "w", encoding="utf-8") as f:
                    json.dump(body, f)
            shutil.copytree(os.path.join(FIX, case), os.path.join(d, "carespace-export"))
            with open(os.path.join(d, "sources.json"), "w", encoding="utf-8") as f:
                json.dump({"primary": {"dir": "mychart"}, "carespace": {"dir": "carespace-export", "org": org, "tag": org}}, f)
            env = {k: v for k, v in os.environ.items() if k != "RECORDS_ROOT"}
            subprocess.run([sys.executable, "-I", os.path.join(ROOT, "build.py"), "--config", os.path.join(d, "sources.json"),
                            "--out", os.path.join(d, "data.json")], check=True, stdout=subprocess.DEVNULL, env=env)
            with open(os.path.join(d, "data.json"), encoding="utf-8") as f:
                return json.load(f)

    def compare(self, case, org):
        built = self.run_build(case, org)
        mine = json.loads(json.dumps(carespace.parse(files(case))["data"]))
        self.assertTrue(built["events"])
        a = normalise(built, url=lambda u: re.sub(r"^records/carespace-export/", "", u))
        self.assertEqual(normalise(mine), a)

    def test_full(self):
        self.compare("full", ORG)

    def test_unnamed(self):
        self.compare("unnamed", carespace.DEFAULT_ORG)


if __name__ == "__main__":
    unittest.main()
