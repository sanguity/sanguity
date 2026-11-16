#!/usr/bin/env python3
"""Tests for the insurer claims importers: UHC and BCBS claims.json (server/importers/claims_uhc.py, claims_bcbs.py)
and the generic EOB spreadsheet (claims_csv.py), on the fictional uploads in tests/fixtures/importers/{uhc,bcbs,
claims-csv}/ and files written on the fly.

The UHC and BCBS importers are build.py's claims readers moved into plug-ins, so `SameAsBuild` runs build.py on
the same claims.json files (through a sources.json in a temporary folder) and compares the claim events, their
details and the Costs rows. Allowed differences: event ids, source keys, document links (none here), the `visit`
hint each claim detail gains, and the one place build.py looks beyond the claims: a facility bill inside a hospital
stay from another source is "Hospital stay (facility)" in the build and "Hospital outpatient (facility)" in an
import of the claims alone, with `visit.facility` set so a later step can re-file it.

  python -m unittest tests/test_importer_claims.py -v
"""
import json, os, shutil, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
IMP = os.path.join(FIXTURES, "importers")
sys.path.insert(0, ROOT)

from server import importers as reg   # noqa: E402
from server.importers import _claims, claims_bcbs, claims_csv, claims_uhc, csv_labs   # noqa: E402

INSURERS = {"uhc": "UnitedHealthcare", "bcbs": "Blue Cross Blue Shield"}


def fixture(name, case):
    return reg.files_in(os.path.join(IMP, name, case))


class Written(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sanguity-claims-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, rel, text, encoding="utf-8"):
        path = os.path.join(self.dir, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding=encoding, newline="") as f:
            f.write(text if isinstance(text, str) else json.dumps(text))
        return {rel: path}


# ------------------------------------------------------------------ the same results as build.py
class SameAsBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.path.exists(os.path.join(ROOT, "corrections.json")):
            raise unittest.SkipTest("a corrections.json next to build.py changes its output")
        with tempfile.TemporaryDirectory() as d:
            conf = {"root": FIXTURES,
                    "primary": {"dir": "build/mychart-*", "org": "Example Health System"},
                    "claims": [{"format": f, "dir": f"importers/{f}/member-export", "insurer": ins} for f, ins in INSURERS.items()]}
            with open(os.path.join(d, "sources.json"), "w", encoding="utf-8") as f:
                json.dump(conf, f)
            out = os.path.join(d, "data.json")
            env = {k: v for k, v in os.environ.items() if k != "RECORDS_ROOT"}
            subprocess.run([sys.executable, "-I", os.path.join(ROOT, "build.py"), "--config", os.path.join(d, "sources.json"),
                            "--out", out], check=True, stdout=subprocess.DEVNULL, env=env)
            with open(out, encoding="utf-8") as f:
                cls.build = json.load(f)

    def built(self, insurer):
        """(events, details, costs) for one insurer's claims in the build, ids renumbered 1.. in timeline order."""
        b = self.build
        evs = [e for e in b["events"] if e["k"] in ("claim", "rx") and b["details"][str(e["id"])]["insurer"] == insurer]
        new = {e["id"]: n for n, e in enumerate(sorted(evs, key=lambda e: e["id"]), 1)}   # creation order, like the importer
        events = [{**e, "id": new[e["id"]]} for e in evs]
        details = {new[e["id"]]: b["details"][str(e["id"])] for e in evs}
        costs = [{**c, "id": new[c["id"]]} for c in b["costs"] if c["id"] in new]
        return events, details, costs

    def compare(self, mod):
        insurer = INSURERS[mod.NAME]
        out = mod.parse(fixture(mod.NAME, "member-export"))
        self.assertEqual(out["warnings"], [])
        data = json.loads(json.dumps(out["data"]))   # as stored: detail keys become strings
        events, details, costs = self.built(insurer)
        self.assertTrue(events and len(events) == len(data["events"]))
        for e in data["events"]:
            self.assertTrue(e.pop("key").startswith(mod.NAME + ":"))
        self.assertEqual(data["events"], events)
        hints = {int(k): d.pop("visit") for k, d in data["details"].items()}
        self.assertEqual({int(k): d for k, d in data["details"].items()}, details)
        for i, h in hints.items():   # the key-based hint for a later link to visits: date and provider
            self.assertEqual(h["date"], next(e["t"][:10] for e in events if e["id"] == i))
            if details[i].get("provider"):
                self.assertEqual(h["provider"], details[i]["provider"])
        moved = [(b, g) for b, g in zip(costs, data["costs"]) if b != g]
        for b, g in moved:   # the only allowed difference: a facility bill during another source's hospital stay
            self.assertEqual((b["cat"], g["cat"]), ("Hospital stay (facility)", "Hospital outpatient (facility)"))
            self.assertEqual({**b, "cat": ""}, {**g, "cat": ""})
            self.assertTrue(hints[g["id"]].get("facility"))
            self.assertTrue(any(e["k"] == "stay" and e["t"][:10] <= g["d"] <= e["end"][:10] for e in self.build["events"]))
        return data, moved

    def test_uhc(self):
        data, moved = self.compare(claims_uhc)
        self.assertEqual([c["d"] for _, c in moved], ["2026-03-03"])   # the fixture's one claim inside a stay
        cats = {c["title"]: c["cat"] for c in data["costs"]}
        self.assertEqual(cats["OFFICE O/P EST MOD 30 MIN"], "Doctor visits")
        self.assertEqual(cats["PET IMAGE W/CT SKULL-THIGH"], "Imaging & tests")
        self.assertEqual(cats["Ondansetron 8mg tablet"], "Pharmacy")
        denied = next(c for c in data["costs"] if c["status"] == "DENIED")
        self.assertEqual((denied["owe"], denied["paid"]), (2150.0, 0.0))

    def test_bcbs(self):
        data, moved = self.compare(claims_bcbs)
        self.assertEqual(moved, [])
        denied = next(c for c in data["costs"] if c["status"] == "DENIED")
        self.assertEqual((denied["cat"], denied["title"]), ("Physical therapy", "Therapeutic exercises"))
        self.assertIn("· denied", next(e["sub"] for e in data["events"] if e["id"] == denied["id"]))


# ------------------------------------------------------------------ UHC and BCBS details
class Insurers(Written):
    def test_detect_only_own_format(self):
        uhc, bcbs = fixture("uhc", "member-export"), fixture("bcbs", "member-export")
        self.assertGreaterEqual(claims_uhc.detect(uhc), 0.9)
        self.assertEqual(claims_bcbs.detect(uhc), 0.0)
        self.assertGreaterEqual(claims_bcbs.detect(bcbs), 0.9)
        self.assertEqual(claims_uhc.detect(bcbs), 0.0)
        self.assertEqual(claims_uhc.detect(fixture("sanguity", "export")), 0.0)

    def test_insurer_name(self):
        out = claims_bcbs.parse(fixture("bcbs", "member-export"), insurer="BCBS Nebraska")
        self.assertEqual({c["ins"] for c in out["data"]["costs"]}, {"BCBS Nebraska"})

    def test_no_date_and_repeats_warn(self):
        files = self.write("claims.json", {"details": {
            "a": {"claimId": "X1", "serviceFromDate": "2026-01-02", "billedChargeAmount": 1, "memberPayableAmount": 1},
            "b": {"claimId": "X1", "serviceFromDate": "2026-01-02", "billedChargeAmount": 1, "memberPayableAmount": 1},
            "c": {"claimId": "X1", "serviceFromDate": "2026-01-09", "billedChargeAmount": 1, "memberPayableAmount": 1},
            "d": {"claimId": "X2", "billedChargeAmount": 5, "memberPayableAmount": 5}}})
        out = claims_bcbs.parse(files)
        self.assertEqual(reg.check(out, files), [])
        self.assertEqual([e["key"] for e in out["data"]["events"]], ["bcbs:id:a", "bcbs:id:b", "bcbs:X1|2026-01-09"])
        self.assertEqual(out["warnings"], ["1 claim had no service date and was left out."])

    def test_unreadable(self):
        files = self.write("claims.json", '{"list": [{"claimKey": "k", "patientResponsibility": 1}')
        with self.assertRaisesRegex(ValueError, "isn't readable JSON"):
            claims_uhc.parse(files)
        with self.assertRaisesRegex(ValueError, "no UnitedHealthcare claims"):
            claims_uhc.parse(self.write("other.json", {"hello": 1}))

    def test_keys_stable(self):
        a = claims_uhc.parse(fixture("uhc", "member-export"))
        b = claims_uhc.parse(fixture("uhc", "member-export"))
        self.assertEqual([e["key"] for e in a["data"]["events"]], [e["key"] for e in b["data"]["events"]])
        self.assertIn("uhc:UH26091500033", [e["key"] for e in a["data"]["events"]])


class VisitLink(unittest.TestCase):
    """Within one import a facility bill on a stay's day is a stay's, as in build.py; otherwise the hint says how to link."""

    LINES = [{"code": "00120", "desc": "Room and board", "billed": 100}, {"code": "00250", "desc": "Pharmacy", "billed": 50}]

    def test_stay_in_same_import(self):
        stay = [{"k": "stay", "t": "2026-03-01T00:00:00", "end": "2026-03-05T23:59:00"}]
        self.assertEqual(_claims.care_category("medical", "", self.LINES, "", "2026-03-03", stay), "Hospital stay (facility)")
        self.assertEqual(_claims.care_category("medical", "", self.LINES, "", "2026-03-07", stay), "Hospital outpatient (facility)")

    def test_hint(self):
        out = claims_uhc.parse(fixture("uhc", "member-export"))
        stay_claim = next(e for e in out["data"]["events"] if e["t"].startswith("2026-03-03"))
        self.assertEqual(out["data"]["details"][stay_claim["id"]]["visit"],
                         {"date": "2026-03-03", "end": "2026-03-05", "provider": "NORTHFIELD GENERAL HOSPITAL", "facility": True})


# ------------------------------------------------------------------ the generic spreadsheet
class Spreadsheet(Written):
    def test_detect_against_labs(self):
        for case in sorted(os.listdir(os.path.join(IMP, "csv"))):   # lab spreadsheets: the lab importer wins
            if not os.path.isdir(os.path.join(IMP, "csv", case)):
                continue
            with self.subTest(case):
                files = fixture("csv", case)
                self.assertLess(claims_csv.detect(files), 0.2)
                self.assertEqual(reg.match(files)["importer"].NAME, "csv")
        for name in os.listdir(os.path.join(FIXTURES, "csv")):
            with self.subTest(name):
                self.assertLess(claims_csv.detect({name: os.path.join(FIXTURES, "csv", name)}), 0.2)
        claims = fixture("claims-csv", "eob")   # a claims spreadsheet: the claims importer wins
        self.assertGreater(claims_csv.detect(claims), csv_labs.detect(claims))
        self.assertEqual(reg.match(claims)["importer"].NAME, "claims-csv")

    def test_lab_sheet_with_provider_and_description(self):
        files = self.write("labs.csv", "Date,Description,Result,Units,Provider\n2026-01-02,Hemoglobin,13.1,g/dL,Valley Lab\n")
        self.assertEqual(reg.match(files)["importer"].NAME, "csv")
        self.assertEqual(claims_csv.detect(files), 0.0)

    def test_fixture(self):
        out = claims_csv.parse(fixture("claims-csv", "eob"))
        costs = out["data"]["costs"]
        self.assertEqual(len(costs), 5)
        lab = next(c for c in costs if c["prov"] == "Valley Reference Laboratory")   # two rows, one claim
        self.assertEqual((lab["billed"], lab["paid"], lab["owe"], lab["cat"]), (200.0, 92.38, 17.45, "Lab & pathology"))
        denied = next(c for c in costs if c["status"] == "Denied")
        self.assertEqual((denied["owe"], denied["cat"]), (1840.0, "Imaging & tests"))
        rx = next(c for c in costs if c["kind"] == "pharmacy")
        self.assertEqual((rx["title"], rx["cat"]), ("Ondansetron 8mg tablet", "Pharmacy"))
        self.assertEqual([e["k"] for e in out["data"]["events"]], ["claim", "claim", "claim", "rx", "claim"])
        self.assertTrue(out["data"]["events"][-1]["key"].startswith("claims-csv:h:"))

    def test_mapping(self):
        text = "When,Who,Charged,Covered,Mine\n2026-04-01,Dr. Rowan Pike,200.00,150.00,50.00\n"
        files = self.write("eob.csv", text)
        self.assertEqual(claims_csv.detect(files), 0.0)
        with self.assertRaisesRegex(ValueError, "has no column for the date of service, the provider, what the plan paid and what you owe"):
            claims_csv.parse(files)
        by_name = claims_csv.parse(files, mapping={"date": "When", "provider": "Who", "billed": "Charged", "paid": "Covered", "owe": "Mine"})
        by_index = claims_csv.parse(files, mapping={"date": 0, "provider": 1, "billed": 2, "paid": 3, "owe": 4})
        self.assertEqual(by_name, by_index)
        self.assertEqual(by_name["data"]["costs"][0]["owe"], 50.0)
        with self.assertRaisesRegex(ValueError, "no column called 'Nope'"):
            claims_csv.parse(files, mapping={"date": "Nope"})

    def test_european_amounts_and_bad_rows(self):
        text = ("Datum;Provider;Billed;Plan paid;Member owes;Insurer\n"
                "03.04.2026;Klinik Am See;1.234,50;1.000,00;234,50;Example Kasse\n"
                "15.04.2026;Klinik Am See;(10,00);0;-;Example Kasse\n"
                "16.04.2026;Klinik Am See;zehn;0;0;Example Kasse\n"
                ";Klinik Am See;1,00;0;0;Example Kasse\n")
        out = claims_csv.parse(self.write("eob.csv", text, "utf-16"), mapping={"date": "Datum"})
        costs = out["data"]["costs"]
        self.assertEqual([(c["d"], c["billed"], c["paid"], c["owe"], c["ins"]) for c in costs],
                         [("2026-04-03", 1234.5, 1000.0, 234.5, "Example Kasse"), ("2026-04-15", -10.0, 0.0, 0.0, "Example Kasse")])
        self.assertEqual(len(out["warnings"]), 2)
        self.assertTrue(any("amount that couldn't be read" in w and "'zehn'" in w for w in out["warnings"]))
        self.assertTrue(any("no date of service" in w for w in out["warnings"]))

    def test_duplicate_rows(self):
        row = "2026-05-01,Dr. Rowan Pike,Office visit,99213,120,80,40\n"
        files = self.write("eob.csv", "Service date,Provider,Service,CPT,Billed,Plan paid,You owe\n" + row + row)
        out = claims_csv.parse(files)
        self.assertEqual(len(out["data"]["events"]), 1)
        self.assertEqual(out["warnings"], ["Left out 1 claim listed twice (same date, provider and amounts)."])
        self.assertEqual(reg.check(out, files), [])

    def test_template(self):
        files = self.write("template.csv", claims_csv.TEMPLATE)
        self.assertEqual(reg.match(files)["importer"].NAME, "claims-csv")
        self.assertEqual(len(claims_csv.parse(files)["data"]["costs"]), 2)

    def test_nothing_readable(self):
        with self.assertRaisesRegex(ValueError, "No claims could be read"):
            claims_csv.parse(self.write("eob.csv", "Date,Provider,Billed,Plan paid,You owe\n,,,,\nx,y,1,1,1\n"))


if __name__ == "__main__":
    unittest.main()
