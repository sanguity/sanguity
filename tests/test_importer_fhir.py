"""FHIR R4 importer (server/importers/fhir.py) against fictional exports: tests/fixtures/importers/fhir/ holds
uploads the registry must hand to fhir (tests/test_importers.py checks those too), tests/fixtures/fhir/ ones that
parse() must refuse.

  python -m unittest discover -s tests -p "test_importer*.py" -v
"""
import json, os, shutil, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "importers", "fhir")   # uploads the registry must give to fhir
BAD = os.path.join(ROOT, "tests", "fixtures", "fhir")                # uploads parse() must refuse
sys.path.insert(0, ROOT)

from server.importers import fhir   # noqa: E402


def upload(folder):
    """The `files` argument the registry passes: {relative POSIX path: absolute path}."""
    out = {}
    for r, _, fs in os.walk(folder):
        for f in fs:
            p = os.path.join(r, f)
            out[os.path.relpath(p, folder).replace(os.sep, "/")] = os.path.abspath(p)
    return out


def by_kind(data, k):
    return [e for e in data["events"] if e["k"] == k]


def series(data, name):
    return next(s for s in data["labs"] if s["name"] == name)


class Shape:
    """Checks every importer result has the data.json shape build.py writes."""

    def assertDataShape(self, data):
        for key in ("events", "details", "labs", "upcoming", "problems", "clinicWeights"):
            self.assertIn(key, data)
        ids = [e["id"] for e in data["events"]]
        self.assertEqual(len(ids), len(set(ids)))
        for e in data["events"]:
            self.assertTrue({"id", "t", "k", "title", "sub"} <= set(e), e)
            self.assertRegex(e["t"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
            if "end" in e:
                self.assertGreaterEqual(e["end"][:10], e["t"][:10])
        self.assertEqual([e["t"] for e in data["events"]], sorted(e["t"] for e in data["events"]))
        for i in data["details"]:
            self.assertIn(i, ids)
        for e in by_kind(data, "lab"):
            d = data["details"][e["id"]]
            self.assertEqual(d["type"], "labday")
            for i in d["ids"]:
                item = next(x for x in data["events"] if x["id"] == i)
                self.assertEqual((item["k"], item["hidden"]), ("labitem", 1))
                self.assertEqual(data["details"][i]["type"], "result")
                for row in data["details"][i]["rows"]:
                    self.assertEqual(len(row), 5)
        for s in data["labs"]:
            self.assertTrue({"name", "unit", "panel", "n", "pts", "txt"} <= set(s), s)
            self.assertEqual(s["n"], len(s["pts"]))
            for p in s["pts"]:
                self.assertEqual(len(p), 7)
                self.assertIsInstance(p[1], (int, float))
                self.assertIn(p[4], ("", "H", "L"))
            for p in s["txt"]:
                self.assertEqual(len(p), 2)
        json.dumps(data)   # serialisable as data.json


class Detect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sanguity-fhir-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, name, content):
        p = os.path.join(self.tmp, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content if isinstance(content, str) else json.dumps(content))
        return {name: p}

    def test_fhir_shapes_detected(self):
        for d in ("bundle", "ndjson", "apple-health"):
            with self.subTest(d):
                self.assertGreaterEqual(fhir.detect(upload(os.path.join(FIX, d))), 0.8)

    def test_sanguity_data_json_not_detected(self):
        # a data.json may mention FHIR words anywhere inside; only a top-level resourceType counts
        data = {"built": "2026-01-01T00:00:00+00:00", "source": "x", "events": [{"id": 1, "t": "2026-01-01T12:00:00",
                "k": "visit", "title": "Visit", "sub": "", "note": {"resourceType": "Observation"}}],
                "details": {}, "labs": [], "problems": []}
        self.assertEqual(fhir.detect(self.write("data.json", data)), 0.0)

    def test_other_json_and_files_not_detected(self):
        self.assertEqual(fhir.detect(self.write("settings.json", {"visitWorkouts": []})), 0.0)
        self.assertEqual(fhir.detect(self.write("list.json", [{"resourceType": "Observation"}])), 0.0)
        self.assertEqual(fhir.detect(self.write("notes.txt", '{"resourceType": "Bundle"}')), 0.0)
        self.assertEqual(fhir.detect({}), 0.0)

    def test_detect_reads_only_the_start(self):
        big = '{"resourceType": "Bundle", "entry": [' + ",".join(['{"resource": {"resourceType": "Observation"}}'] * 20000) + "]}"
        self.assertGreater(len(big), fhir.HEAD)
        self.assertGreaterEqual(fhir.detect(self.write("big.json", big)), 0.9)


class Bundle(Shape, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = fhir.parse(upload(os.path.join(FIX, "bundle")))
        cls.data = cls.out["data"]

    def test_shape(self):
        self.assertDataShape(self.data)

    def test_report_groups_its_labs_under_one_lab_day(self):
        days = by_kind(self.data, "lab")
        self.assertEqual(len(days), 1)
        items = [self.data["details"][i] for i in self.data["details"][days[0]["id"]]["ids"]]
        cbc = next(d for d in items if d["order"] == "CBC WITH DIFFERENTIAL")
        self.assertEqual([r[0] for r in cbc["rows"]], ["Hemoglobin", "Platelet Count"])
        self.assertEqual(cbc["lab"], "Maple Grove Clinic")
        self.assertEqual(cbc["provider"], "Dr. Casey Rivera")
        self.assertEqual(days[0]["abn"], 3)   # low hemoglobin, high platelets, high LDL

    def test_units_ranges_flags_and_loinc(self):
        hgb = series(self.data, "Hemoglobin")
        self.assertEqual((hgb["unit"], hgb["loinc"], hgb["panel"]), ("g/dL", "718-7", "CBC WITH DIFFERENTIAL"))
        self.assertEqual(hgb["pts"], [["2025-03-04T09:45:00-06:00", 12.1, 13.5, 17.5, "L", "12.1", "Maple Grove Clinic"]])
        plt = series(self.data, "Platelet Count")   # no interpretation: flag from the range
        self.assertEqual((plt["unit"], plt["pts"][0][4]), ("10*3/uL", "H"))
        ldl = series(self.data, "LDL Cholesterol")  # text-only range "<130 mg/dL"
        self.assertEqual(ldl["pts"][0][2:5], [None, 130, "H"])

    def test_text_values_kept(self):
        s = series(self.data, "Color of Urine")
        self.assertEqual((s["n"], s["txt"]), (0, [["2025-03-04T09:50:00-06:00", "Yellow"]]))

    def test_vitals(self):
        self.assertEqual(series(self.data, "Systolic blood pressure")["pts"][0][1], 128)
        self.assertEqual(series(self.data, "Diastolic blood pressure")["panel"], "Vitals")
        self.assertEqual(self.data["clinicWeights"], [["2025-03-04T09:35:00-06:00", 176.4, "FHIR"]])   # 80 kg in lb

    def test_encounters(self):
        v = by_kind(self.data, "visit")
        self.assertEqual(len(v), 1)
        self.assertEqual((v[0]["title"], v[0]["end"]), ("Office visit", "2025-03-04T10:00:00-06:00"))
        d = self.data["details"][v[0]["id"]]
        self.assertEqual((d["who"], d["where"], d["dx"]), ("Dr. Casey Rivera", "Maple Grove Clinic, Suite 200", "Annual physical"))
        self.assertEqual([u["title"] for u in self.data["upcoming"]], ["Follow-up visit"])

    def test_inpatient_encounter_becomes_stay(self):
        (s,) = by_kind(self.data, "stay")
        self.assertEqual((s["t"], s["end"]), ("2025-06-10T21:40:00-05:00", "2025-06-13T14:10:00-05:00"))
        self.assertIn("3 nights", s["sub"])
        self.assertEqual(self.data["details"][s["id"]]["type"], "stay")

    def test_conditions_and_meds(self):
        self.assertEqual([(p["name"], p["status"], p["date"]) for p in self.data["problems"]],
                         [("Hyperlipidemia", "Active", "2024-11-20"), ("Pneumonia", "Resolved", "2025-06-10")])
        (m,) = by_kind(self.data, "med")
        self.assertEqual(m["title"], "Atorvastatin 20 mg tablet")
        self.assertEqual(self.data["details"][m["id"]]["instructions"], "Take 1 tablet by mouth daily")

    def test_imaging_and_documents(self):
        (x,) = by_kind(self.data, "imaging")
        self.assertIn("pneumonia", self.data["details"][x["id"]]["report"][0][1])
        docs = {self.data["details"][e["id"]]["url"]: self.data["details"][e["id"]] for e in by_kind(self.data, "document")}
        self.assertEqual(set(docs), {"attachments/chest-xr.pdf", "attachments/discharge-summary.pdf"})
        self.assertEqual(docs["attachments/chest-xr.pdf"], {"type": "file", "url": "attachments/chest-xr.pdf", "ext": "pdf"})

    def test_warnings_name_what_was_skipped(self):
        w = " ".join(self.out["warnings"])
        self.assertIn("Observation (social-history)", w)
        self.assertNotIn("Immunization", w)   # imported since #165, its own kind since #179
        self.assertIn("inline", w)
        self.assertEqual([e["title"] for e in by_kind(self.data, "immunization")], ["Influenza vaccine"])
        self.assertEqual(by_kind(self.data, "procedure"), [])


class Ndjson(Shape, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = fhir.parse(upload(os.path.join(FIX, "ndjson")))["data"]

    def test_shape(self):
        self.assertDataShape(self.data)

    def test_series_across_days(self):
        g = series(self.data, "Glucose, Blood")
        self.assertEqual([p[1] for p in g["pts"]], [104, 92])   # the entered-in-error 999 is dropped
        self.assertEqual([p[4] for p in g["pts"]], ["H", ""])
        self.assertEqual(len(by_kind(self.data, "lab")), 2)

    def test_comparator_value_is_text(self):
        self.assertEqual(series(self.data, "eGFR")["txt"], [["2025-04-09T07:20:00-05:00", ">60"]])

    def test_other_resource_files(self):
        self.assertEqual(self.data["problems"][0]["name"], "Prediabetes")
        self.assertEqual(by_kind(self.data, "med")[0]["title"], "Metformin 500 mg tablet")

    def test_bad_line_is_a_warning(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        p = os.path.join(tmp, "Observation.ndjson")
        shutil.copy(os.path.join(FIX, "ndjson", "Observation.ndjson"), p)
        with open(p, "a", encoding="utf-8") as f:
            f.write('{"resourceType": "Observation", "id": \n')
        out = fhir.parse({"Observation.ndjson": p})
        self.assertEqual(series(out["data"], "Glucose, Blood")["n"], 2)
        self.assertTrue(any("1 line" in w for w in out["warnings"]))


class AppleHealthFolder(Shape, unittest.TestCase):
    """A folder of single resources, as Apple Health writes clinical-records/ (DSTU2 fields included)."""

    @classmethod
    def setUpClass(cls):
        cls.data = fhir.parse(upload(os.path.join(FIX, "apple-health")))["data"]

    def test_shape(self):
        self.assertDataShape(self.data)

    def test_contents(self):
        chol = series(self.data, "CHOLESTEROL, TOTAL")
        self.assertEqual(chol["pts"][0][1:5], [212, None, 199, "H"])
        self.assertEqual(chol["loinc"], "2093-3")
        (m,) = by_kind(self.data, "med")
        self.assertEqual((m["title"], self.data["details"][m["id"]]["src"]), ("Amoxicillin 500 mg capsule", "Dr. Jamie Patel"))
        self.assertEqual(self.data["problems"][0]["status"], "Active")
        (s,) = by_kind(self.data, "stay")   # DSTU2 class "inpatient", no discharge time
        self.assertEqual(s["end"], "2024-02-03T23:59:00")

    def test_allergies_immunizations_and_procedures(self):   # #165: every kind Apple's Health Records hold
        out = fhir.parse(upload(os.path.join(FIX, "apple-health")))
        self.assertFalse(any("doesn't import yet" in w for w in out["warnings"]), out["warnings"])
        allergy = next(p for p in self.data["problems"] if p["type"] == "Allergy")
        self.assertEqual((allergy["name"], allergy["date"], allergy["status"]), ("Allergy: Penicillin (Hives, Itching)", "2024-05-20", "Active"))
        procs = {e["title"]: e for e in by_kind(self.data, "procedure")}
        self.assertEqual(sorted(procs), ["Removal of skin lesion"])
        (flu,) = by_kind(self.data, "immunization")   # #179: a kind of its own; not the one not given
        self.assertEqual((flu["title"], flu["sub"], flu["key"]), ("Influenza, seasonal, injectable", "Avery Lin, RN", "fhir:Immunization/L3M4"))
        self.assertEqual(self.data["details"][flu["id"]], {"type": "immunization", "vaccine": "Influenza, seasonal, injectable",
                                                          "site": "Left arm", "lot": "FICT-0042", "performer": "Avery Lin, RN",
                                                          "status": "Completed", "src": "Avery Lin, RN"})
        self.assertEqual((allergy["substance"], allergy["reactions"], allergy["criticality"]), ("Penicillin", ["Hives", "Itching"], "Low"))
        cut = procs["Removal of skin lesion"]
        self.assertEqual((cut["t"], cut["sub"]), ("2024-06-14T10:30:00+00:00", "Dr. Jamie Patel · Harbor View Hospital"))
        self.assertEqual(self.data["details"][cut["id"]]["report"], [["Body site", "Left forearm", 0], ["Notes", "Healed well", 0]])
        self.assertEqual(cut["key"], "fhir:Procedure/N5P6")


class PortalEverything(Shape, unittest.TestCase):
    """#179: a portal's bundle with immunizations, allergies, a procedure, pharmacy fills, appointments and imaging
    studies, each into the kind C-CDA (tests/fixtures/importers/ccda/harbor-view-ccd, the same records), the claims'
    fills or a planned Encounter gives it."""

    @classmethod
    def setUpClass(cls):
        cls.out = fhir.parse(upload(os.path.join(FIX, "portal-everything")))
        cls.data = cls.out["data"]
        from server.importers import ccda
        cls.ccda = ccda.parse(upload(os.path.join(ROOT, "tests", "fixtures", "importers", "ccda", "harbor-view-ccd")))["data"]

    def test_contract(self):
        from server import importers
        self.assertDataShape(self.data)
        self.assertEqual(importers.check(self.out), [])
        self.assertEqual(self.out["warnings"], [])   # every kind in it is read now

    def detail(self, e):
        return self.data["details"][e["id"]]

    def test_immunizations(self):
        flu, hepb = sorted(by_kind(self.data, "immunization"), key=lambda e: e["title"], reverse=True)   # not the one not done
        self.assertEqual(len(by_kind(self.data, "immunization")), 2)
        self.assertEqual((flu["title"], flu["t"], flu["sub"], flu["key"]),
                         ("Influenza vaccine", "2025-10-02T10:15:00-05:00", "Dose 1 · Sam Ortega, RN · Harbor View Clinic", "fhir:Immunization/hv-imm-flu"))
        self.assertEqual(self.detail(flu), {"type": "immunization", "vaccine": "Influenza vaccine", "code": "150", "system": "CVX",
                                            "dose": "1", "amount": "0.5 mL", "route": "Intramuscular", "site": "Left deltoid",
                                            "lot": "FICT-FLU-11", "manufacturer": "Fictional Vaccines Inc.", "performer": "Sam Ortega, RN",
                                            "where": "Harbor View Clinic", "status": "Completed", "src": "Harbor View Clinic"})
        self.assertEqual((hepb["t"], self.detail(hepb)["dose"], self.detail(hepb).get("performer")), ("2025-04-10T12:00:00", "2 of 3", None))
        self.assertEqual(by_kind(self.data, "procedure")[0]["title"], "Colonoscopy")   # immunizations are no procedures (#165 made them so)

    def test_immunizations_take_the_shape_ccda_gives_them(self):
        def shape(data):   # C-CDA writes a person's name "Sam Ortega RN", FHIR's text "Sam Ortega, RN"
            return {e["title"]: (e["k"], e["t"], e["sub"].replace(",", ""),
                                 {k: v.replace(",", "") if k == "performer" else v for k, v in data["details"][e["id"]].items()})
                    for e in by_kind(data, "immunization")}
        fh, cd = shape(self.data), shape(self.ccda)
        self.assertEqual(fh["Influenza vaccine"], cd["Influenza vaccine"])
        # where FHIR gives the series' doses it says "2 of 3"; C-CDA has the dose number alone
        hb = cd["Hepatitis B vaccine"]
        self.assertEqual(fh["Hepatitis B vaccine"], (hb[0], hb[1], "Dose 2 of 3 · Harbor View Clinic", {**hb[3], "dose": "2 of 3"}))

    def test_allergies_as_ccda_reads_them(self):
        by = {p["substance"]: p for p in self.data["problems"] if p["type"] == "Allergy"}
        self.assertEqual(sorted(by), ["Peanuts", "Penicillin G"])   # not the refuted one
        pcn = by["Penicillin G"]
        self.assertEqual({k: v for k, v in pcn.items() if k not in ("key", "src", "date")},
                         {k: v for k, v in self.ccda["problems"][0].items() if k not in ("key", "src", "date")})
        self.assertEqual((pcn["name"], pcn["reactions"], pcn["severity"], pcn["criticality"], pcn["status"], pcn["code"], pcn["system"]),
                         ("Allergy: Penicillin G (Hives)", ["Hives"], "Moderate", "High", "Active", "7980", "RxNorm"))
        self.assertEqual(set(pcn), set(self.ccda["problems"][0]))
        self.assertEqual((by["Peanuts"]["reactions"], by["Peanuts"]["severity"]), (["Anaphylaxis", "Rash"], "Severe"))   # the worst reaction's

    def test_pharmacy_fills(self):
        (fill,) = by_kind(self.data, "rx")   # not the cancelled one
        self.assertEqual((fill["title"], fill["sub"], fill["key"]),
                         ("Atorvastatin 20 mg tablet", "Pharmacy fill · Harbor View Pharmacy · 30-day supply", "fhir:MedicationDispense/hv-md-1"))
        self.assertEqual(self.detail(fill), {"type": "med", "instructions": "Take 1 tablet by mouth daily", "status": "Completed",
                                             "src": "Harbor View Pharmacy", "pharmacy": "Harbor View Pharmacy", "daysSupply": 30,
                                             "quantity": "30 tablet", "prescriber": "Dr. Priya Lindqvist", "rxnorm": "617311"})
        from server import medications   # #50: the fill sits under its prescription's medication, with its pharmacy and supply
        (m,) = medications.history(json.loads(json.dumps(self.data)))   # as the app has them: detail ids as text
        self.assertEqual(m["fills"], [{"t": "2025-09-01", "id": fill["id"], "title": "Atorvastatin 20 mg tablet",
                                       "pharmacy": "Harbor View Pharmacy", "supply": 30}])

    def test_appointments(self):
        self.assertEqual(self.data["upcoming"], [{"t": "2027-02-12T09:30:00-06:00", "title": "Annual physical", "who": "Dr. Priya Lindqvist",
                                                  "where": "Harbor View Clinic, Building B", "dx": "Yearly check-up", "procs": [],
                                                  "key": "fhir:Appointment/hv-ap-1"}])   # not the fulfilled or cancelled ones
        # the fields a planned Encounter's and an appointment typed in have (manual_items._upcoming)
        self.assertEqual(set(self.data["upcoming"][0]), {"t", "title", "who", "where", "dx", "procs", "key"})

    def test_imaging_studies(self):
        x = {e["title"]: e for e in by_kind(self.data, "imaging")}
        self.assertEqual(sorted(x), ["Chest X-ray, 2 views", "Knee X-ray, 3 views"])   # the chest study is its report's, once
        knee = x["Knee X-ray, 3 views"]
        self.assertEqual((knee["sub"], knee["key"]), ("Dr. Priya Lindqvist · Harbor View Clinic, Building B", "fhir:ImagingStudy/hv-is-knee"))
        self.assertEqual(self.detail(knee)["report"], [["Modality", "X-ray", 0], ["Body site", "Knee", 0], ["Series", "1", 0], ["Images", "3", 0]])

    def test_keys_stable_and_unique(self):
        again = fhir.parse(upload(os.path.join(FIX, "portal-everything")))["data"]
        self.assertEqual(keys(again), keys(self.data))
        ks = [e["key"] for e in self.data["events"]]
        self.assertEqual(len(ks), len(set(ks)))


class HealthCard(unittest.TestCase):
    """#179: a SMART Health Card's vaccination bundle (tests/shl_server.vaccination_bundle): no ids, only CVX codes,
    a display for who gave it. Each dose is an immunization of its own, keyed by its day and code, the same on every
    import."""

    def parse(self):
        sys.path.insert(0, os.path.join(ROOT, "tests"))
        import shl_server
        tmp = tempfile.mkdtemp(prefix="sanguity-shc-")
        self.addCleanup(shutil.rmtree, tmp, True)
        with open(os.path.join(tmp, "card.json"), "w", encoding="utf-8") as f:
            json.dump(shl_server.vaccination_bundle(), f)
        return fhir.parse(upload(tmp))

    def test_doses(self):
        out = self.parse()
        from server import importers
        self.assertEqual(importers.check(out), [])
        imms = by_kind(out["data"], "immunization")
        self.assertEqual([(e["t"], e["title"], e["sub"]) for e in imms],
                         [("2025-01-15T12:00:00", "COVID-19 vaccine", "Fictional County Health Department"),
                          ("2025-02-12T12:00:00", "COVID-19 vaccine", "Fictional County Health Department")])
        self.assertEqual(out["data"]["details"][imms[0]["id"]],
                         {"type": "immunization", "vaccine": "COVID-19 vaccine", "code": "207", "system": "CVX", "lot": "FICT-0001",
                          "performer": "Fictional County Health Department", "status": "Completed",
                          "src": "Fictional County Health Department"})
        self.assertTrue(all(e["key"].startswith("fhir:h:") for e in imms) and imms[0]["key"] != imms[1]["key"])
        self.assertEqual([e["key"] for e in by_kind(self.parse()["data"], "immunization")], [e["key"] for e in imms])


def keys(data):
    return {"events": sorted((e["k"], e["key"]) for e in data["events"]),
            "labs": sorted((s["name"], s.get("key")) for s in data["labs"]),
            "problems": sorted(p["key"] for p in data["problems"]),
            "upcoming": sorted(u["key"] for u in data["upcoming"])}


class Keys(unittest.TestCase):
    """Stable source keys (contract amendment to #18): same input, same keys, whatever the file layout."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sanguity-fhir-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bundle = fhir.parse(upload(os.path.join(FIX, "bundle")))["data"]

    def bundle_as_ndjson(self):
        """The bundle fixture rewritten as a bulk export: one NDJSON file per type, references as Type/id."""
        with open(os.path.join(FIX, "bundle", "patient-everything.json"), encoding="utf-8") as f:
            entries = json.load(f)["entry"]
        refs = {e["fullUrl"]: f"{e['resource']['resourceType']}/{e['resource']['id']}" for e in entries}
        by_type = {}
        for e in entries:
            text = json.dumps(e["resource"])
            for full, rel in refs.items():
                text = text.replace(f'"{full}"', f'"{rel}"')
            by_type.setdefault(e["resource"]["resourceType"], []).append(text)
        for rt, lines in by_type.items():
            with open(os.path.join(self.tmp, f"{rt}.ndjson"), "w", encoding="utf-8") as f:
                f.write("\n".join(reversed(lines)) + "\n")   # a different order must not change the keys
        shutil.copytree(os.path.join(FIX, "bundle", "attachments"), os.path.join(self.tmp, "attachments"))
        return upload(self.tmp)

    def test_every_event_has_a_unique_key(self):
        ks = [e.get("key") for e in self.bundle["events"]]
        self.assertTrue(all(ks), self.bundle["events"])
        self.assertEqual(len(ks), len(set(ks)))

    def test_key_forms(self):
        ev = {e["k"]: e["key"] for e in self.bundle["events"]}
        self.assertEqual(ev["visit"], "fhir:Encounter/enc-1")
        self.assertEqual(ev["stay"], "fhir:Encounter/enc-2")
        self.assertEqual(ev["med"], "fhir:MedicationRequest/med-1")
        self.assertEqual(ev["lab"], "fhir:labday:2025-03-04")
        self.assertEqual(ev["imaging"], "fhir:DiagnosticReport/dr-xr")
        items = {e["key"] for e in by_kind(self.bundle, "labitem")}
        self.assertEqual(items, {"fhir:DiagnosticReport/dr-cbc", "fhir:labitem:2025-03-04"})
        docs = {e["key"] for e in by_kind(self.bundle, "document")}
        self.assertEqual(docs, {"fhir:DocumentReference/doc-1", "fhir:DiagnosticReport/dr-xr#presentedForm/1"})
        self.assertEqual(series(self.bundle, "Hemoglobin")["key"], "loinc:718-7")
        self.assertEqual(self.bundle["problems"][0]["key"], "fhir:Condition/cond-1")

    def test_same_input_twice_same_keys(self):
        again = fhir.parse(upload(os.path.join(FIX, "bundle")))["data"]
        self.assertEqual(keys(again), keys(self.bundle))
        self.assertEqual(again, self.bundle)   # and the same records: nothing depends on the clock

    def test_registry_accepts_every_fixture(self):
        from server import importers as reg
        for d in ("bundle", "ndjson", "apple-health", "two-units", "blue-button"):
            with self.subTest(d):
                files = reg.files_in(os.path.join(FIX, d))
                self.assertEqual(reg.match(files)["importer"].NAME, "fhir")
                self.assertEqual(reg.check(fhir.parse(files), files), [])

    def test_document_not_in_upload_keeps_its_entry(self):
        doc = {"resourceType": "DocumentReference", "id": "d9", "status": "current", "date": "2025-02-01",
               "type": {"text": "Visit note"},
               "content": [{"attachment": {"contentType": "application/pdf", "url": "notes/visit.pdf"}},
                           {"attachment": {"contentType": "application/pdf", "url": "https://fhir.example.org/Binary/77"}}]}
        p = os.path.join(self.tmp, "DocumentReference-d9.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        out = fhir.parse({"DocumentReference-d9.json": p})
        docs = {e["key"]: out["data"]["details"][e["id"]] for e in by_kind(out["data"], "document")}
        self.assertEqual(docs, {"fhir:DocumentReference/d9": {"type": "file", "url": None, "ext": "pdf"},
                                "fhir:DocumentReference/d9#content/2": {"type": "file", "url": None, "ext": "pdf"}})
        w = " ".join(out["warnings"])
        self.assertIn("isn't in the upload", w)
        self.assertIn("web address", w)

    def test_bundle_and_ndjson_give_same_keys(self):
        nd = fhir.parse(self.bundle_as_ndjson())["data"]
        self.assertEqual(keys(nd), keys(self.bundle))

    def test_series_keys_unique_when_one_code_has_two_units(self):
        labs = fhir.parse(upload(os.path.join(FIX, "two-units")))["data"]["labs"]
        ks = [s["key"] for s in labs]
        self.assertEqual(len(ks), len(set(ks)))
        self.assertEqual(sorted(ks), ["loinc:2160-0", "loinc:2345-7|mg/dL", "loinc:2345-7|mmol/L"])
        self.assertEqual(series({"labs": labs}, "Glucose, Blood (mmol/L)")["pts"][0][1], 5.4)
        for d in ("bundle", "ndjson", "apple-health"):   # no split: plain keys, still unique
            ks = [s["key"] for s in fhir.parse(upload(os.path.join(FIX, d)))["data"]["labs"] if "key" in s]
            self.assertEqual(len(ks), len(set(ks)), d)
            self.assertFalse(any("|" in k for k in ks if k.startswith("loinc:")), d)

    def test_resource_without_id(self):
        obs = {"resourceType": "Observation", "status": "final", "category": [{"coding": [{"code": "laboratory"}]}],
               "code": {"coding": [{"system": "http://loinc.org", "code": "2345-7"}]},
               "effectiveDateTime": "2025-01-08T07:15:00-06:00", "valueQuantity": {"value": 104, "unit": "mg/dL"}}
        k = fhir.resource_key(obs)
        self.assertRegex(k, r"^fhir:h:[0-9a-f]{16}$")
        self.assertEqual(k, fhir.resource_key(json.loads(json.dumps(obs))))
        self.assertNotEqual(k, fhir.resource_key(dict(obs, valueQuantity={"value": 105, "unit": "mg/dL"})))
        ident = dict(obs, identifier=[{"system": "urn:example:lab", "value": "A-17"}])
        self.assertEqual(fhir.resource_key(ident), "fhir:Observation?identifier=urn:example:lab|A-17")

    def test_mixed_servers_qualify_keys(self):
        bundle = {"resourceType": "Bundle", "entry": [
            {"fullUrl": f"https://{host}.example.org/fhir/Encounter/1",
             "resource": {"resourceType": "Encounter", "id": "1", "status": "finished", "class": {"code": "AMB"},
                          "period": {"start": f"2025-0{n}-01T09:00:00Z"}}}
            for n, host in ((1, "one"), (2, "two"))]}
        p = os.path.join(self.tmp, "mixed.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(bundle, f)
        ks = sorted(e["key"] for e in fhir.parse({"mixed.json": p})["data"]["events"])
        self.assertEqual(ks, ["fhir:https://one.example.org/fhir|Encounter/1", "fhir:https://two.example.org/fhir|Encounter/1"])


class Malformed(unittest.TestCase):
    def test_broken_json_raises_plain_message(self):
        with self.assertRaises(ValueError) as cm:
            fhir.parse(upload(os.path.join(BAD, "malformed")))
        msg = str(cm.exception)
        self.assertIn("Observation-broken.json", msg)
        self.assertIn("isn't valid JSON", msg)

    def test_broken_file_beside_good_ones_is_a_warning(self):
        files = {**upload(os.path.join(FIX, "apple-health")), **upload(os.path.join(BAD, "malformed"))}
        out = fhir.parse(files)
        self.assertTrue(any("Observation-broken.json" in w for w in out["warnings"]))

    def test_no_fhir_raises(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        p = os.path.join(tmp, "data.json")
        with open(p, "w") as f:
            json.dump({"events": []}, f)
        with self.assertRaises(ValueError):
            fhir.parse({"data.json": p})


if __name__ == "__main__":
    unittest.main()
