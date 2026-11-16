"""ExplanationOfBenefit -> Costs in the FHIR importer (server/importers/_fhir_claims.py, #92), on a fictional Blue
Button bulk export (tests/fixtures/importers/fhir/blue-button/, NDJSON) and resources written on the fly. The
records are checked against the shapes the claims importers (claims_uhc, claims_bcbs, claims_csv) produce, so an
insurer's API and an insurer's export file land on the Costs tab the same way.

  python -m unittest tests/test_importer_fhir_claims.py -v
"""
import json, os, shutil, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "importers")
sys.path.insert(0, ROOT)

from server import importers as reg   # noqa: E402
from server.importers import claims_bcbs, claims_csv, claims_uhc, fhir   # noqa: E402
from tests.test_importer_fhir import Shape   # noqa: E402

BB = os.path.join(FIX, "fhir", "blue-button")
ADJ = "http://hl7.org/fhir/us/carin-bb/CodeSystem/C4BBAdjudication"
COST_KEYS = {"id", "d", "ins", "billed", "paid", "owe", "status", "kind", "cat", "prov", "title"}


def amt(code, v):
    return {"category": {"coding": [{"system": ADJ, "code": code}]}, "amount": {"value": v}}


def eob(rid, **kw):
    r = {"resourceType": "ExplanationOfBenefit", "id": rid, "status": "active", "use": "claim", "outcome": "complete",
         "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/claim-type", "code": "professional"}]},
         "patient": {"reference": "Patient/p1"}, "insurer": {"display": "Sample Plan (fictional)"},
         "provider": {"display": "Sample Clinic (fictional)"}, "billablePeriod": {"start": "2026-03-02"},
         "item": [{"sequence": 1, "productOrService": {"text": "Office visit"}, "servicedDate": "2026-03-02",
                   "adjudication": [amt("submitted", 100.0), amt("benefit", 70.0), amt("copay", 20.0)]}]}
    r.update(kw)
    return r


def claims_of(data):
    return [e for e in data["events"] if e["k"] in ("claim", "rx")]


def by_key(data, rid):
    e = next(e for e in data["events"] if e.get("key") == f"fhir:ExplanationOfBenefit/{rid}")
    cost = next((c for c in data["costs"] if c["id"] == e["id"]), None)
    return e, data["details"][e["id"]], cost


class BlueButtonExport(Shape, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = reg.files_in(BB)
        cls.out = fhir.parse(cls.files)
        cls.data = cls.out["data"]
        # the claims importers' own records, for their shapes
        cls.uhc = claims_uhc.parse(reg.files_in(os.path.join(FIX, "uhc", "member-export")))["data"]
        cls.bcbs = claims_bcbs.parse(reg.files_in(os.path.join(FIX, "bcbs", "member-export")))["data"]
        csv_dir = os.path.join(FIX, "claims-csv")
        cls.csv = claims_csv.parse(reg.files_in(os.path.join(csv_dir, sorted(os.listdir(csv_dir))[0])))["data"] \
            if os.path.isdir(csv_dir) else None

    def test_shape(self):
        self.assertDataShape(self.data)
        self.assertEqual(reg.check(self.out, self.files), [])

    def test_registry_hands_the_export_to_fhir(self):
        self.assertEqual(reg.match(self.files)["importer"].NAME, "fhir")

    def test_same_shapes_as_the_claims_importers(self):
        for name, other in (("uhc", self.uhc), ("bcbs", self.bcbs), ("claims-csv", self.csv)):
            if other is None:
                continue
            with self.subTest(name):
                self.assertEqual({k for c in other["costs"] for k in c}, COST_KEYS)
                theirs = {k for e in claims_of(other) if e["k"] == "claim" for k in other["details"][e["id"]]}
                ours = {k for e in claims_of(self.data) if e["k"] == "claim" for k in self.data["details"][e["id"]]}
                required = {"type", "insurer", "kind", "provider", "claimId", "billed", "paid", "owe", "status", "lines", "visit"}
                self.assertLessEqual(required & theirs, ours)
                line_keys = {k for e in claims_of(other) if e["k"] == "claim" for l in other["details"][e["id"]].get("lines") or [] for k in l}
                ours_lines = {k for e in claims_of(self.data) if e["k"] == "claim" for l in self.data["details"][e["id"]]["lines"] for k in l}
                self.assertLessEqual(line_keys & {"date", "desc", "code", "billed", "denied"}, ours_lines)
        for c in self.data["costs"]:
            self.assertEqual(set(c), COST_KEYS)
            self.assertIn(c["kind"], ("medical", "pharmacy"))
            self.assertEqual(c["d"], next(e for e in self.data["events"] if e["id"] == c["id"])["t"][:10])
        # one Costs row per claim event, and none for anything else
        self.assertEqual(sorted(c["id"] for c in self.data["costs"]), sorted(e["id"] for e in claims_of(self.data)))

    def test_medical_claim(self):
        e, d, c = by_key(self.data, "carrier-fict-1001")
        self.assertEqual((e["k"], e["title"], e["t"]), ("claim", "Office visit, established patient", "2026-01-06T12:00:00"))
        self.assertEqual((c["billed"], c["paid"], c["owe"], c["status"]), (255.0, 126.5, 28.0, "Paid"))
        self.assertEqual((c["ins"], c["prov"], c["cat"], c["kind"]),
                         ("Medicare (fictional sandbox)", "Harbor View Internal Medicine (fictional)", "Doctor visits", "medical"))
        self.assertEqual((d["claimId"], d["kind"], d["plan"], d["network"]), ("FICT-1001", "Medical", "Medicare Part B (fictional)", "In network"))
        self.assertEqual([(l["code"], l["billed"]) for l in d["lines"]], [("99214", 210.0), ("80053", 45.0)])
        self.assertEqual(d["visit"], {"date": "2026-01-06", "provider": "Harbor View Internal Medicine (fictional)"})

    def test_facility_bill_and_a_denied_line(self):
        e, d, c = by_key(self.data, "outpatient-fict-2001")
        self.assertEqual([l["code"] for l in d["lines"]], ["00324", "00730", "00636"])   # revenue codes, as UHC's
        self.assertEqual(c["cat"], "Hospital outpatient (facility)")
        self.assertTrue(d["visit"]["facility"])
        self.assertEqual((c["billed"], c["paid"], c["owe"]), (540.0, 175.0, 65.0))   # owe: deductible + coinsurance
        self.assertEqual(c["status"], "Paid")                    # one line denied, the claim paid
        self.assertEqual(d["lines"][1]["denied"], ["Not medically necessary (fictional)"])
        self.assertNotIn("abn", e)

    def test_denied_claim(self):
        e, d, c = by_key(self.data, "carrier-fict-1002")
        self.assertEqual(c["status"], "Denied")
        self.assertRegex(c["status"], "(?i)DENIED")             # what the Costs tab counts as denied
        self.assertEqual((c["paid"], c["owe"], e["abn"]), (0.0, 0.0, 1))
        self.assertTrue(e["sub"].endswith("· denied"))
        self.assertEqual(d["denied"], ["Prior authorization absent (fictional)"])
        self.assertEqual(c["cat"], "Imaging & tests")

    def test_adjusted_claim_replaces_the_original(self):
        e, d, c = by_key(self.data, "carrier-fict-1003a")
        self.assertEqual((c["billed"], c["paid"], c["owe"], c["status"]), (160.0, 104.0, 16.0, "Paid"))
        self.assertEqual([l["billed"] for l in d["lines"]], [160.0, 0.0])   # the correction line
        old, od, oc = by_key(self.data, "carrier-fict-1003")
        self.assertEqual((oc["billed"], oc["paid"], oc["owe"], oc["status"]), (0.0, 0.0, 0.0, "Replaced"))
        self.assertEqual((old["hidden"], od["replacedBy"], od["original"]), (1, "FICT-1003-A1", {"billed": 160.0, "paid": 96.0, "owe": 24.0}))
        self.assertTrue(any("adjusted claim" in w for w in self.out["warnings"]))
        # the plan paid, all told: each claim counted once
        self.assertEqual(round(sum(c["paid"] for c in self.data["costs"]), 2), 126.5 + 175.0 + 0 + 104.0 + 27.4)

    def test_pharmacy_fill(self):
        e, d, c = by_key(self.data, "pde-fict-3001")
        self.assertEqual((e["k"], e["title"]), ("rx", "Atorvastatin Calcium 20mg tablet"))   # the claims importers' drug names
        self.assertTrue(e["sub"].startswith("Pharmacy fill · Maple Street Pharmacy (fictional)"))
        self.assertEqual((c["kind"], c["cat"], c["billed"], c["paid"], c["owe"]), ("pharmacy", "Pharmacy", 32.4, 27.4, 5.0))
        self.assertEqual((d["kind"], d["drug"], d["ndc"], d["daysSupply"]), ("Pharmacy", "ATORVASTATIN CALCIUM TAB 20MG", "00000-0000-01", 30))
        self.assertNotIn("lines", d)

    def test_cancelled_left_out_and_coverage_not_skipped(self):
        keys = {e.get("key") for e in self.data["events"]}
        self.assertNotIn("fhir:ExplanationOfBenefit/carrier-fict-1004", keys)
        w = " ".join(self.out["warnings"])
        self.assertIn("cancelled", w)
        self.assertNotIn("Coverage", w)              # read for the plan's name, not "skipped"
        self.assertNotIn("ExplanationOfBenefit", w)

    def test_keys_stable(self):
        again = fhir.parse(reg.files_in(BB))["data"]
        self.assertEqual(again, self.data)
        self.assertTrue(all(e["key"].startswith("fhir:ExplanationOfBenefit/") for e in claims_of(self.data)))


class Written(Shape, unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sanguity-eob-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def parse(self, *resources):
        with open(os.path.join(self.dir, "Export.ndjson"), "w", encoding="utf-8") as f:
            for r in resources:
                f.write(json.dumps(r) + "\n")
        out = fhir.parse(reg.files_in(self.dir))
        self.assertDataShape(out["data"])
        return out

    def test_facility_bill_inside_a_stay_of_the_same_import(self):
        stay = {"resourceType": "Encounter", "id": "enc-1", "status": "finished", "class": {"code": "IMP"},
                "period": {"start": "2026-03-01T09:00:00Z", "end": "2026-03-04T11:00:00Z"},
                "serviceProvider": {"display": "Sample Hospital (fictional)"}}
        bill = eob("inst-1", type={"coding": [{"code": "institutional"}]}, billablePeriod={"start": "2026-03-02", "end": "2026-03-03"},
                   item=[{"sequence": 1, "revenue": {"coding": [{"code": "0120", "display": "Room and board, semi-private"}]},
                          "productOrService": {"coding": [{"code": "0120"}]}, "servicedDate": "2026-03-02",
                          "adjudication": [amt("submitted", 3000.0), amt("benefit", 2500.0), amt("deductible", 300.0)]}])
        data = self.parse(stay, bill)["data"]
        e, d, c = by_key(data, "inst-1")
        self.assertEqual(c["cat"], "Hospital stay (facility)")
        self.assertEqual((e["end"], d["visit"]), ("2026-03-03T12:00:00", {"date": "2026-03-02", "end": "2026-03-03",
                                                                         "provider": "Sample Clinic (fictional)", "facility": True}))
        self.assertEqual(d["lines"][0]["desc"], "Room and board, semi-private")
        alone = self.parse(bill)["data"]
        self.assertEqual(by_key(alone, "inst-1")[2]["cat"], "Hospital outpatient (facility)")

    def test_amounts_from_lines_when_there_are_no_totals(self):
        r = eob("e1", payment={"amount": {"value": 70.0}})
        data = self.parse(r)["data"]
        self.assertEqual(by_key(data, "e1")[2], {"id": 1, "d": "2026-03-02", "ins": "Sample Plan (fictional)", "billed": 100.0,
                                                 "paid": 70.0, "owe": 20.0, "status": "Paid", "kind": "medical",
                                                 "cat": "Doctor visits", "prov": "Sample Clinic (fictional)", "title": "Office visit"})

    def test_paid_parts_payment_and_member_liability(self):
        r = eob("e2", item=[{"sequence": 1, "productOrService": {"text": "Office visit"}, "adjudication": [amt("submitted", 100.0)]}],
                total=[amt("submitted", 100.0), amt("paidtoprovider", 60.0), amt("paidtopatient", 5.0), amt("memberliability", 35.0),
                       amt("copay", 10.0)])
        c = by_key(self.parse(r)["data"], "e2")[2]
        self.assertEqual((c["paid"], c["owe"]), (65.0, 35.0))   # memberliability wins over its parts
        r = eob("e3", item=[{"sequence": 1, "productOrService": {"text": "Office visit"}, "adjudication": [amt("submitted", 80.0)]}],
                payment={"amount": {"value": 50.0}})
        self.assertEqual(by_key(self.parse(r)["data"], "e3")[2]["paid"], 50.0)

    def test_negative_adjustment_counts_as_it_is(self):
        r = eob("e4", total=[amt("submitted", 100.0), amt("benefit", 70.0), amt("benefit", -10.0), amt("memberliability", 30.0)])
        c = by_key(self.parse(r)["data"], "e4")[2]
        self.assertEqual((c["paid"], c["owe"]), (60.0, 30.0))

    def test_pending_and_processed(self):
        q = eob("e5", outcome="queued", item=[{"sequence": 1, "productOrService": {"text": "Office visit"}, "adjudication": [amt("submitted", 50.0)]}])
        z = eob("e6", item=[{"sequence": 1, "productOrService": {"text": "Office visit"}, "adjudication": [amt("submitted", 50.0), amt("benefit", 0.0), amt("deductible", 50.0)]}])
        data = self.parse(q, z)["data"]
        self.assertEqual(by_key(data, "e5")[2]["status"], "Pending")
        self.assertEqual((by_key(data, "e6")[2]["status"], by_key(data, "e6")[2]["owe"]), ("Processed", 50.0))

    def test_denied_by_payer_adjudication_status(self):
        r = eob("e7", item=[{"sequence": 1, "productOrService": {"text": "Office visit"},
                             "adjudication": [amt("submitted", 50.0),
                                              {"category": {"coding": [{"code": "denied"}]}, "reason": {"text": "Not covered (fictional)"}}]}])
        e, d, c = by_key(self.parse(r)["data"], "e7")
        self.assertEqual((c["status"], e["abn"], d["lines"][0]["denied"]), ("Denied", 1, ["Not covered (fictional)"]))

    def test_replacedby_and_insurer_from_coverage(self):
        cov = {"resourceType": "Coverage", "id": "c1", "status": "active", "beneficiary": {"reference": "Patient/p1"},
               "payor": [{"reference": "Organization/o1"}],
               "class": [{"type": {"coding": [{"code": "group"}]}, "value": "G-1", "name": "Sample Group (fictional)"}]}
        org = {"resourceType": "Organization", "id": "o1", "name": "SAMPLE MUTUAL (FICTIONAL)"}
        old = eob("old", identifier=[{"value": "C-1"}], insurer=None, insurance=[{"focal": True, "coverage": {"reference": "Coverage/c1"}}],
                  related=[{"claim": {"identifier": {"value": "C-1R"}}, "relationship": {"coding": [{"code": "replacedby"}]}}])
        new = eob("new", identifier=[{"value": "C-1R"}], insurer=None, insurance=[{"focal": True, "coverage": {"reference": "Coverage/c1"}}])
        data = self.parse(cov, org, old, new)["data"]
        self.assertEqual(by_key(data, "old")[2]["status"], "Replaced")
        e, d, c = by_key(data, "new")
        self.assertEqual((c["status"], c["ins"], d["plan"]), ("Paid", "Sample Mutual (Fictional)", "Sample Group (fictional)"))
        # a replacedby whose newer claim isn't in the upload keeps the claim as it is
        self.assertEqual(by_key(self.parse(cov, org, old)["data"], "old")[2]["status"], "Paid")

    def test_claims_only_upload_is_enough(self):
        out = self.parse(eob("e8"))
        self.assertEqual(len(out["data"]["costs"]), 1)

    def test_no_date_is_a_warning(self):
        r = eob("e9", billablePeriod=None, item=[{"sequence": 1, "productOrService": {"text": "x"}}])
        r.pop("billablePeriod")
        with self.assertRaises(ValueError):   # nothing left to show
            self.parse(r)
        out = self.parse(r, eob("e10"))
        self.assertTrue(any("no service date" in w for w in out["warnings"]))


class FakeServerClaims(unittest.TestCase):
    """The fake FHIR server's claims fixtures (tests/fixtures/fakefhir/) parse into Costs rows too."""

    def test_fixture_claims(self):
        for name, n in (("robin-example.json", 2), ("sam-sample.json", 1), (os.path.join("payer", "casey-member.json"), 5)):
            with self.subTest(name):
                with open(os.path.join(ROOT, "tests", "fixtures", "fakefhir", name), encoding="utf-8") as f:
                    spec = json.load(f)
                d = tempfile.mkdtemp(prefix="sanguity-eob-")
                self.addCleanup(shutil.rmtree, d, True)
                with open(os.path.join(d, "b.json"), "w", encoding="utf-8") as f:
                    json.dump({"resourceType": "Bundle", "type": "collection",
                               "entry": [{"resource": r} for r in spec["resources"]]}, f)
                data = fhir.parse(reg.files_in(d))["data"]
                self.assertEqual(len(data["costs"]), n)


if __name__ == "__main__":
    unittest.main(verbosity=2)
