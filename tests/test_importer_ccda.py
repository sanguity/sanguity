#!/usr/bin/env python3
"""C-CDA importer tests, on the fictional documents in tests/fixtures/ccda/ (two portal styles, plus non-C-CDA XML,
a truncated file and hostile entity files).

  python3 -m pytest tests/test_importer_ccda.py
"""
import json, os, shutil, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "ccda")
sys.path.insert(0, ROOT)

from server.importers import ccda   # noqa: E402


def files(*names, folder=""):
    return {folder + n: os.path.join(FIX, n) for n in names}


class Detect(unittest.TestCase):
    def test_ccda_documents(self):
        self.assertGreaterEqual(ccda.detect(files("maple-grove-ccd.xml")), 0.9)
        self.assertGreaterEqual(ccda.detect(files("riverbend-visit.xml")), 0.9)   # prefixed namespace

    def test_one_ccda_among_other_files(self):
        self.assertGreaterEqual(ccda.detect(files("not-ccda.xml", "notes.xml", "maple-grove-ccd.xml", folder="export/")), 0.9)

    def test_not_ccda(self):
        self.assertEqual(ccda.detect(files("not-ccda.xml")), 0.0)    # FHIR XML
        self.assertEqual(ccda.detect(files("notes.xml")), 0.0)       # mentions the HL7 namespace, isn't a document
        self.assertEqual(ccda.detect({}), 0.0)

    def test_reads_only_the_start(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        p = os.path.join(tmp, "big.xml")
        with open(p, "wb") as f:      # the root element only after the sniffing window: not detected
            f.write(b"<?xml version='1.0'?>\n<!--" + b" " * (ccda.HEAD_BYTES + 10) + b"-->\n<ClinicalDocument xmlns='urn:hl7-org:v3'/>")
        self.assertEqual(ccda.detect({"big.xml": p}), 0.0)

    def test_plain_cda_scores_lower(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        p = os.path.join(tmp, "c32.xml")
        with open(p, "w", encoding="utf-8") as f:
            f.write("<ClinicalDocument xmlns='urn:hl7-org:v3'><templateId root='2.16.840.1.113883.3.88.11.32.1'/></ClinicalDocument>")
        self.assertTrue(0 < ccda.detect({"c32.xml": p}) < 0.9)


class Parse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = ccda.parse(files("maple-grove-ccd.xml", "riverbend-visit.xml", "not-ccda.xml"))
        cls.d = cls.r["data"]
        cls.ev = {e["id"]: e for e in cls.d["events"]}
        cls.labs = {s["name"]: s for s in cls.d["labs"]}

    def kind(self, k):
        return [e for e in self.d["events"] if e["k"] == k]

    def test_contract(self):
        self.assertEqual(ccda.NAME, "ccda")
        self.assertTrue(ccda.LABEL)
        self.assertEqual(set(self.r), {"data", "warnings"})
        self.assertTrue(all(isinstance(w, str) for w in self.r["warnings"]))
        json.dumps(self.r)   # JSON-ready

    def test_event_shape(self):
        ids = [e["id"] for e in self.d["events"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual([e["t"] for e in self.d["events"]], sorted(e["t"] for e in self.d["events"]))
        for e in self.d["events"]:
            self.assertLessEqual({"id", "t", "k", "title", "sub"}, set(e))
            self.assertLessEqual(set(e), {"id", "t", "k", "title", "sub", "end", "abn", "hidden", "src", "key"})
            self.assertRegex(e["t"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
        self.assertLessEqual(set(self.d["details"]), set(ids))

    def test_lab_days(self):
        days = self.kind("lab")
        self.assertEqual(len(days), 3)
        march = next(e for e in days if e["t"].startswith("2026-03-02"))
        det = self.d["details"][march["id"]]
        self.assertEqual(det["type"], "labday")
        self.assertEqual(len(det["ids"]), 2)
        self.assertEqual(march["abn"], 3)     # low hemoglobin, high platelets, high glucose
        self.assertIn("Maple Grove Clinic", march["title"])
        for i in det["ids"]:
            self.assertEqual(self.ev[i]["k"], "labitem")
            self.assertEqual(self.ev[i]["hidden"], 1)
            self.assertEqual(self.d["details"][i]["type"], "result")
        every = {i for e in days for i in self.d["details"][e["id"]]["ids"]}
        self.assertEqual(every, {e["id"] for e in self.kind("labitem")})

    def test_result_rows(self):
        cbc = next(self.d["details"][e["id"]] for e in self.kind("labitem") if e["t"].startswith("2026-03-02") and "CBC" in e["title"])
        rows = {r[0]: r for r in cbc["rows"]}
        self.assertEqual(rows["Hemoglobin"], ["Hemoglobin", "11.2", "g/dL", "13.5 - 17.5 g/dL", "L"])   # name from the narrative
        self.assertEqual(rows["WBC"], ["WBC", "7.1", "10*3/uL", "4.0 - 11.0", ""])
        self.assertEqual(rows["Platelets"][4], "H")     # no interpretation code: flagged against the range
        self.assertTrue(all(len(r) == 5 for r in cbc["rows"]))

    def test_series_with_ranges_flags_and_loinc(self):
        hgb = self.labs["Hemoglobin"]
        self.assertEqual((hgb["unit"], hgb["loinc"], hgb["n"]), ("g/dL", "718-7", 2))
        self.assertEqual(hgb["panel"], "CBC panel - Blood by Automated count")
        self.assertEqual(hgb["pts"][0], ["2026-03-02T08:10:00-05:00", 11.2, 13.5, 17.5, "L", "11.2", "Maple Grove Clinic"])
        self.assertEqual(hgb["pts"][1][4], "")
        self.assertEqual(self.labs["Glucose"]["pts"][0][2:5], [70, 99, "H"])
        for s in self.d["labs"]:
            self.assertLessEqual({"name", "unit", "panel", "n", "txt", "pts"}, set(s))
            self.assertEqual(s["n"], len(s["pts"]))
            for p in s["pts"]:
                self.assertEqual(len(p), 7)
                self.assertIsInstance(p[1], (int, float))
                self.assertIn(p[4], ("", "H", "L"))

    def test_second_portal_style(self):
        na = self.labs["SODIUM"]                       # kept as reported; canonical names are another step's job
        self.assertEqual(na["loinc"], "2951-2")
        self.assertEqual(na["unit"], "mmol/L")         # only in the range text
        self.assertEqual(na["pts"][0][1:6], [128, 135.0, 145.0, "L", "128"])   # interpretation LL
        self.assertEqual(self.labs["TROPONIN I"]["pts"][0][5], "<0.01")     # as reported kept
        self.assertEqual(self.labs["Color of Urine"]["txt"], [["2026-03-02T08:10:00-05:00", "Yellow"]])
        chem = next(e for e in self.kind("labitem") if e["title"] == "BASIC CHEMISTRY")   # panel name from the narrative
        self.assertIn("Riverbend Health", chem["sub"])

    def test_vitals(self):
        self.assertEqual(self.labs["Height"]["pts"][0][1], 68.9)        # 175 cm
        self.assertEqual(self.labs["Temperature"]["pts"][0][1], 98.6)   # 37 Cel
        self.assertEqual(self.labs["Blood pressure, systolic"]["pts"][0][1], 128)
        self.assertEqual(self.labs["Blood pressure, diastolic"]["unit"], "mmHg")
        self.assertEqual(self.labs["Heart rate (clinic)"]["panel"], "Vitals")
        self.assertEqual(self.labs["SpO2 (clinic)"]["pts"][0][1], 96)
        self.assertNotIn("Body weight", self.labs)
        self.assertEqual(self.d["clinicWeights"], [["2026-03-02T09:18:00-05:00", 176.4, "Maple Grove Clinic"],
                                                   ["2026-08-14T22:15:00", 176.0, "Riverbend Health"]])

    def test_medications(self):
        meds = {e["title"]: e for e in self.kind("med")}
        self.assertEqual(set(meds), {"Lisinopril 10 MG Oral Tablet", "Amoxicillin 500 MG Oral Capsule", "Ondansetron 4 mg/2 mL injection"})
        lis = meds["Lisinopril 10 MG Oral Tablet"]
        self.assertEqual(lis["t"], "2025-01-15T12:00:00")
        self.assertEqual(lis["sub"], "Take 1 tablet by mouth once daily · Active")
        self.assertEqual(self.d["details"][lis["id"]], {"type": "med", "instructions": "Take 1 tablet by mouth once daily",
                                                         "status": "Active", "src": "Maple Grove Clinic", "rxnorm": "314076"})
        amox = self.d["details"][meds["Amoxicillin 500 MG Oral Capsule"]["id"]]
        self.assertEqual((amox["status"], amox["end"]), ("Completed", "2026-04-20T12:00:00"))
        ond = self.d["details"][meds["Ondansetron 4 mg/2 mL injection"]["id"]]
        self.assertEqual(ond["instructions"], "4 mg IV once for nausea")   # from the instructions act
        self.assertTrue(any("1 medication with no start date" in w for w in self.r["warnings"]))

    def test_problems(self):
        probs = {p["name"]: p for p in self.d["problems"]}
        htn = dict(probs["Essential hypertension"])
        self.assertEqual(htn.pop("key"), "ccda:2.16.840.1.113883.19.5.99999.40^PRB-4001")
        self.assertEqual(htn, {"name": "Essential hypertension", "type": "Problem", "date": "2025-01-15", "status": "Active",
                               "src": "Maple Grove Clinic", "code": "38341003", "system": "SNOMED CT"})
        ida = probs["Iron deficiency anemia, unspecified"]
        self.assertEqual((ida["status"], ida["system"], ida["code"], ida["type"]), ("Resolved", "ICD-10-CM", "D50.9", "Diagnosis"))
        hypo = probs["Low sodium (hyponatremia)"]      # name from originalText; type "diagnosis" capitalised
        self.assertEqual((hypo["type"], hypo["status"], hypo["src"]), ("Diagnosis", "Active", "Riverbend Health"))
        self.assertNotIn("Problem", probs)             # "no known problems" (negationInd) is not a problem
        self.assertEqual(probs["Allergy: Penicillin G (Hives)"]["type"], "Allergy")

    def test_visits_and_stays(self):
        visits = {e["title"]: e for e in self.kind("visit")}
        self.assertEqual(set(visits), {"Outpatient visit", "Hospital admission", "Emergency visit"})
        ov = visits["Outpatient visit"]
        self.assertEqual(ov["sub"], "Dr. Casey Fairweather · Maple Grove Family Practice · Maple Grove Clinic")
        self.assertEqual(self.d["details"][ov["id"]]["dx"], "Essential hypertension")
        er = visits["Emergency visit"]                  # from the header's encompassingEncounter
        self.assertEqual((er["t"], er["end"]), ("2026-08-14T22:10:00", "2026-08-15T01:40:00"))
        self.assertEqual(self.d["details"][er["id"]]["who"], "Morgan Quill MD")
        stays = self.kind("stay")
        self.assertEqual(len(stays), 1)
        self.assertIn("3 nights", stays[0]["sub"])
        self.assertEqual(self.d["details"][stays[0]["id"]]["type"], "stay")

    def test_same_document_twice_is_not_doubled(self):
        r = ccda.parse({"a.xml": os.path.join(FIX, "maple-grove-ccd.xml"), "b/a.xml": os.path.join(FIX, "maple-grove-ccd.xml")})
        self.assertEqual(len(r["data"]["events"]), len(ccda.parse(files("maple-grove-ccd.xml"))["data"]["events"]))
        self.assertEqual({s["name"]: s["n"] for s in r["data"]["labs"]}["Hemoglobin"], 2)


class Keys(unittest.TestCase):
    """Stable source keys: the same file gives the same keys, and no two records share one."""
    BOTH = ("maple-grove-ccd.xml", "riverbend-visit.xml")

    @staticmethod
    def keys(r):
        d = r["data"]
        return ([(e["k"], e["title"], e["t"], e.get("key")) for e in d["events"]], [(p["name"], p.get("key")) for p in d["problems"]],
                [(s["name"], s.get("key")) for s in d["labs"]])

    def test_stable_across_parses_and_input_order(self):
        a = ccda.parse(files(*self.BOTH))
        b = ccda.parse(files(*self.BOTH))
        self.assertEqual(self.keys(a), self.keys(b))
        tmp = tempfile.mkdtemp()     # same documents under other names and in the other order: same keys per record
        self.addCleanup(shutil.rmtree, tmp)
        swapped = {}
        for i, n in enumerate(reversed(self.BOTH)):
            shutil.copy(os.path.join(FIX, n), os.path.join(tmp, f"{i}.xml"))
            swapped[f"{i}.xml"] = os.path.join(tmp, f"{i}.xml")
        c = ccda.parse(swapped)
        by = lambda r: {(e["k"], e["t"], e["title"]): e["key"] for e in r["data"]["events"]}
        self.assertEqual(by(a), by(c))

    def test_every_record_keyed_and_unique(self):
        d = ccda.parse(files(*self.BOTH))["data"]
        keys = [e["key"] for e in d["events"]] + [p["key"] for p in d["problems"]]
        self.assertTrue(all(isinstance(k, str) and k.startswith("ccda:") for k in keys))
        self.assertEqual(len(keys), len(set(keys)))
        series = [s["key"] for s in d["labs"] if "key" in s]
        self.assertEqual(len(series), len(set(series)))
        for s in d["labs"]:
            for p in s["pts"]:
                self.assertEqual(len(p), 7)       # points stay positional, no key

    def test_key_forms(self):
        d = ccda.parse(files("maple-grove-ccd.xml"))["data"]
        ev = {(e["k"], e["title"], e["t"][:10]): e["key"] for e in d["events"]}
        self.assertEqual(ev[("visit", "Outpatient visit", "2026-03-02")], "ccda:2.16.840.1.113883.19.5.99999.10^ENC-1001")
        self.assertEqual(ev[("labitem", "CBC panel - Blood by Automated count", "2026-03-02")], "ccda:2.16.840.1.113883.19.5.99999.20^LAB-2001")
        self.assertEqual(ev[("med", "Lisinopril 10 MG Oral Tablet", "2025-01-15")], "ccda:2.16.840.1.113883.19.5.99999.30^MED-3001")
        self.assertRegex(ev[("med", "Amoxicillin 500 MG Oral Capsule", "2026-04-10")], r"^ccda:h:[0-9a-f]{16}$")   # no id: hashed
        self.assertRegex(ev[("visit", "Hospital admission", "2026-04-10")], r"^ccda:h:[0-9a-f]{16}$")
        self.assertEqual(ev[("stay", "Hospital stay · Maple Grove Clinic", "2026-04-10")], ev[("visit", "Hospital admission", "2026-04-10")] + ":stay")
        labs = {s["name"]: s.get("key") for s in d["labs"]}
        self.assertEqual(labs["Hemoglobin"], "loinc:718-7")
        self.assertEqual(labs["Height"], "loinc:8302-2")

    def test_reused_id_and_shared_code(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        with open(os.path.join(FIX, "maple-grove-ccd.xml"), encoding="utf-8") as f:
            s = f.read()
        # a portal that reuses one id for every medication, and sends hemoglobin under a second name, with a local code
        s = s.replace('<templateId root="2.16.840.1.113883.10.20.22.4.16" extension="2014-06-09"/>',
                      '<templateId root="2.16.840.1.113883.10.20.22.4.16" extension="2014-06-09"/><id root="1.2.3" extension="X"/>')
        s = s.replace('displayName="Hemoglobin"/>', 'displayName="HGB"/>').replace(
            '<code code="5778-6" codeSystem="2.16.840.1.113883.6.1"', '<code code="UCOLOR" codeSystem="1.2.3.4.99"')
        p = os.path.join(tmp, "x.xml")
        with open(p, "w", encoding="utf-8") as f:
            f.write(s)
        d = ccda.parse({"x.xml": p})["data"]
        meds = [e["key"] for e in d["events"] if e["k"] == "med"]
        self.assertEqual(len(meds), len(set(meds)))
        labs = {x["name"]: x.get("key") for x in d["labs"]}
        self.assertEqual({labs["Hemoglobin"], labs["HGB"]}, {"loinc:718-7|Hemoglobin", "loinc:718-7|HGB"})
        self.assertEqual(labs["Color of Urine"], "ccda:1.2.3.4.99|UCOLOR")

    def test_one_code_in_two_units(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        with open(os.path.join(FIX, "maple-grove-ccd.xml"), encoding="utf-8") as f:
            s = f.read()
        # the June hemoglobin reported in mmol/L: a series of its own, and both series' keys name their unit
        s = s.replace('<value xsi:type="PQ" value="13.9" unit="g/dL"/>', '<value xsi:type="PQ" value="8.6" unit="mmol/L"/>')
        p = os.path.join(tmp, "x.xml")
        with open(p, "w", encoding="utf-8") as f:
            f.write(s)
        labs = {x["name"]: x.get("key") for x in ccda.parse({"x.xml": p})["data"]["labs"]}
        self.assertEqual(labs["Hemoglobin"], "loinc:718-7|g/dL")
        self.assertEqual(labs["Hemoglobin (mmol/L)"], "loinc:718-7|mmol/L")
        self.assertEqual(labs["Glucose"], "loinc:2345-7")      # no split, no suffix


class Refuse(unittest.TestCase):
    def test_no_ccda(self):
        with self.assertRaisesRegex(ValueError, "No C-CDA"):
            ccda.parse(files("not-ccda.xml", "notes.xml"))

    def test_malformed(self):
        with self.assertRaisesRegex(ValueError, "not well-formed XML"):
            ccda.parse(files("truncated.xml"))

    def test_malformed_alongside_a_good_file_is_a_warning(self):
        r = ccda.parse(files("truncated.xml", "maple-grove-ccd.xml"))
        self.assertTrue(r["data"]["events"])
        self.assertTrue(any("truncated.xml" in w and "well-formed" in w for w in r["warnings"]))

    def test_entity_expansion(self):
        self.assertGreater(ccda.detect(files("billion-laughs.xml")), 0)   # detection doesn't parse
        with self.assertRaisesRegex(ValueError, "DOCTYPE or ENTITY"):
            ccda.parse(files("billion-laughs.xml"))

    def test_external_entity(self):
        with self.assertRaisesRegex(ValueError, "DOCTYPE or ENTITY"):
            ccda.parse(files("external-entity.xml"))

    def test_utf16_doctype(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        p = os.path.join(tmp, "u16.xml")
        with open(os.path.join(FIX, "billion-laughs.xml"), encoding="utf-8") as f:
            text = f.read().replace('encoding="UTF-8"', 'encoding="UTF-16"')
        with open(p, "w", encoding="utf-16") as f:
            f.write(text)
        self.assertGreater(ccda.detect({"u16.xml": p}), 0)
        with self.assertRaisesRegex(ValueError, "DOCTYPE or ENTITY"):
            ccda.parse({"u16.xml": p})


class Contract(unittest.TestCase):
    def test_output_passes_the_registry_check(self):   # the shape the app stores (server/importers/__init__.py)
        from server import importers as registry
        for name in ['maple-grove-ccd.xml', 'riverbend-visit.xml']:
            with self.subTest(name):
                files = {name: os.path.join(FIX, name)}
                self.assertEqual(registry.check(ccda.parse(files), files), [])


if __name__ == "__main__":
    unittest.main()
