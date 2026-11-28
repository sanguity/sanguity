#!/usr/bin/env python3
"""Family history (#1134; server/family.py): read from FHIR's FamilyMemberHistory and a C-CDA's Family History section
(tests/fixtures/family/), a re-import adding no copy; the person's own entries added, changed, removed and put back,
the records' hidden and shown again (never edited), by owners and editors only, encrypted, gone with the profile; on
the visit summary (ticked at first; preview and PDF), in the FHIR export as FamilyMemberHistory, and through Export
everything. Fictional data only.

  python3 tests/test_family.py
"""
import io, json, os, shutil, sys, tempfile, unittest, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "family")
TMP = tempfile.mkdtemp(prefix="sanguity-family-test-")
BUILD = os.path.join(TMP, "build")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=BUILD,
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_CORRECTIONS=os.path.join(TMP, "config", "corrections.json"))
for d in ("records", "keys", "build", "config"):
    os.makedirs(os.path.join(TMP, d))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import fhir_schema_check   # noqa: E402
from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, crypto, db as dbm, export, main, preview, profiles, records   # noqa: E402
from server.importers import ccda, fhir   # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = "harbour lantern pebble crow"


def fhir_data():
    out = fhir.parse({"family-fhir.json": os.path.join(FIX, "family-fhir.json")})
    return out["data"], out["warnings"]


def ccda_data():
    out = ccda.parse({"family-ccda.xml": os.path.join(FIX, "family-ccda.xml")})
    return out["data"], out["warnings"]


class Importers(unittest.TestCase):
    def test_fhir_family_member_history(self):
        data, warnings = fhir_data()
        mother, brother = data["familyHistory"]
        self.assertEqual((mother["relation"], mother["code"], mother["name"], mother["deceased"], mother["deathAge"],
                          mother["note"], mother["date"], mother["key"]),
                         ("Mother", "MTH", "Ann", True, "78", "Diagnosed in her fifties.", "2025-06-12", "fhir:FamilyMemberHistory/fmh-mother"))
        self.assertEqual([(c["name"], c.get("code"), c.get("system"), c.get("onset"), c.get("death", False)) for c in mother["conditions"]],
                         [("Type 2 diabetes mellitus", "44054006", "SNOMED CT", "52", False),
                          ("Myocardial infarction", "22298006", "SNOMED CT", "78", True)])
        self.assertEqual((brother["relation"], brother["deceased"], brother["conditions"]),
                         ("Brother", False, [{"name": "Asthma", "onset": "18 months"}]), "the RoleCode's word; an age in months")
        self.assertNotIn("Gout", json.dumps(data), "entered in error: left out")
        self.assertFalse(any("family history" in w.lower() or "FamilyMemberHistory" in w for w in warnings), warnings)
        self.assertNotIn("FamilyMemberHistory", fhir.PLAIN_KINDS, "no longer a kind of record it skips")

    def test_ccda_family_history_section(self):
        data, warnings = ccda_data()
        father, gran = data["familyHistory"]
        self.assertEqual((father["relation"], father["code"], father["deceased"], father["src"], father["key"]),
                         ("Father", "FTH", True, "Maple Grove Clinic", "ccda:7c2b1e1a-61a4-4a8e-9a3d-2f0b5e7c1001"))
        self.assertEqual([(c["name"], c["onset"], c.get("death", False)) for c in father["conditions"]],
                         [("Hypertension", "45", False), ("Stroke", "70", True)])
        self.assertEqual((gran["relation"], gran["conditions"][0]["name"], gran["conditions"][0]["onset"], "deceased" in gran),
                         ("Maternal grandmother", "Breast cancer", "61", False), "living or not: not said")
        self.assertTrue(gran["key"].startswith("ccda:h:"))
        self.assertEqual(ccda.parse({"family-ccda.xml": os.path.join(FIX, "family-ccda.xml")})["data"]["familyHistory"][1]["key"],
                         gran["key"], "a key of no id is the same each time")
        self.assertEqual(warnings, [], "a document of family history alone is one with records")


class InTheRecords(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(BUILD, "data.json"), "w", encoding="utf-8") as f:
            json.dump({"built": "2026-11-01T00:00:00+00:00", "source": "fictional", "events": [], "details": {}, "labs": [],
                       "upcoming": [], "problems": [], "milestones": [], "costs": []}, f)
        cls.ctx = TestClient(main.app, base_url="http://testserver").__enter__()
        db = dbm.connect()
        cls.uid = accounts.create(db, "robin", password=PW)[0]
        db.close()
        cls.c = TestClient(main.app, base_url="http://testserver")
        assert cls.c.post("/api/login", json={"username": "robin", "password": PW}, headers=H).status_code == 200

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        shutil.rmtree(TMP, True)

    def profile(self, name):
        db = dbm.connect()
        try:
            return profiles.create_profile(db, name, owner_id=self.uid)
        finally:
            db.close()

    def store(self, pid, data, fmt):
        db = dbm.connect()
        try:
            with dbm.transaction(db):
                return records.store(db, pid, data, "upload", fmt=fmt)
        finally:
            db.close()

    def data(self, pid):
        r = self.c.get(f"/api/profiles/{pid}/data")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_records_own_entries_and_hiding(self):
        pid = self.profile("Family (fictional)")
        p = f"/api/profiles/{pid}"
        fdata, cdata = fhir_data()[0], ccda_data()[0]
        self.store(pid, fdata, "fhir")
        self.store(pid, cdata, "ccda")
        fam = self.data(pid)["familyHistory"]
        self.assertEqual(sorted(x["relation"] for x in fam), ["Brother", "Father", "Maternal grandmother", "Mother"])
        # the same records again: the preview finds every entry here already, and stored twice they're still one each
        db = dbm.connect()
        try:
            for d, fmt in ((fdata, "fhir"), (cdata, "ccda")):
                dups = preview.find(db, pid, fmt, json.loads(json.dumps(d)))
                self.assertEqual(dups["meta"].get("familyHistory"), set(range(len(d["familyHistory"]))), fmt)
        finally:
            db.close()
        self.store(pid, fhir_data()[0], "fhir")
        self.assertEqual(len(self.data(pid)["familyHistory"]), 4, "one entry per record")

        # your own: added, changed, removed, put back with its key
        r = self.c.post(p + "/family/own", headers=H, json={"relation": "Sister", "name": "Jo", "status": "living",
                                                             "conditions": [{"name": "Migraine", "onset": "16"}, {"name": "", "onset": ""}]})
        self.assertEqual(r.status_code, 201, r.text)
        eid, key = r.json()["id"], r.json()["key"]
        self.assertTrue(key.startswith("own:"))
        own = next(x for x in r.json()["records"]["familyHistory"] if x.get("own"))
        self.assertEqual((own["relation"], own["name"], own["deceased"], own["conditions"], own["src"], own["own"]["id"], own["own"]["by"]),
                         ("Sister", "Jo", False, [{"name": "Migraine", "onset": "16"}], "Typed in", eid, "robin"))
        for bad in ({"relation": ""}, {"relation": "Aunt", "deathAge": "60"}, {"relation": "Aunt", "conditions": [{"onset": "40"}]},
                    {"relation": "Aunt", "status": "maybe"}, {"relation": "x" * 101}):
            self.assertEqual(self.c.post(p + "/family/own", headers=H, json=bad).status_code, 400, bad)
        r = self.c.put(f"{p}/family/own/{eid}", headers=H, json={"relation": "Sister", "name": "Jo", "status": "deceased", "deathAge": "40",
                                                                  "conditions": [{"name": "Migraine", "onset": "16"}], "note": "Fictional"})
        self.assertEqual(r.status_code, 200, r.text)
        own = next(x for x in self.data(pid)["familyHistory"] if x.get("own"))
        self.assertEqual((own["key"], own["deceased"], own["deathAge"], own["note"]), (key, True, "40", "Fictional"))
        r = self.c.delete(f"{p}/family/own/{eid}", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        gone = r.json()["removed"]
        self.assertFalse(any(x.get("own") for x in self.data(pid)["familyHistory"]))
        r = self.c.post(p + "/family/own", headers=H, json=gone)   # Undo
        self.assertEqual(r.status_code, 201, r.text)
        own = next(x for x in self.data(pid)["familyHistory"] if x.get("own"))
        self.assertEqual((own["key"], own["deathAge"], own["conditions"]), (key, "40", [{"name": "Migraine", "onset": "16"}]))
        self.assertEqual(self.c.post(p + "/family/own", headers=H, json=gone).status_code, 400, "not twice")
        rec = next(x for x in self.data(pid)["familyHistory"] if x["relation"] == "Mother")
        self.assertEqual(self.c.put(f"{p}/family/own/{eid + 99}", headers=H, json={"relation": "Mother"}).status_code, 404,
                         "the records' are never edited")

        # one of the records' hidden: out of the list, its records kept, still hidden when imported again; shown again
        r = self.c.put(p + "/family/hidden", headers=H, json={"key": rec["key"], "hidden": True})
        self.assertEqual(r.status_code, 200, r.text)
        self.store(pid, fhir_data()[0], "fhir")
        d = self.data(pid)
        self.assertNotIn("Mother", {x["relation"] for x in d["familyHistory"]})
        self.assertEqual([(h["key"], h["relation"], h["name"]) for h in d["hiddenFamily"]], [(rec["key"], "Mother", "Ann")])
        self.assertEqual(self.c.put(p + "/family/hidden", headers=H, json={"key": "fhir:no-such", "hidden": True}).status_code, 404)
        self.assertEqual(self.c.put(p + "/family/hidden", headers=H, json={"key": key, "hidden": True}).status_code, 404, "your own: removed, not hidden")
        self.assertEqual(self.c.put(p + "/family/hidden", headers=H, json={"key": rec["key"], "hidden": False}).status_code, 200)
        d = self.data(pid)
        self.assertIn("Mother", {x["relation"] for x in d["familyHistory"]})
        self.assertNotIn("hiddenFamily", d)

        # a viewer reads them and changes none
        db = dbm.connect()
        vid = accounts.create(db, "sam", password=PW)[0]
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (vid, pid))
        db.close()
        sam = TestClient(main.app, base_url="http://testserver")
        self.assertEqual(sam.post("/api/login", json={"username": "sam", "password": PW}, headers=H).status_code, 200)
        self.assertIn("familyHistory", sam.get(p + "/data").json())
        for method, path, body in (("POST", "/family/own", {"relation": "Aunt"}), ("PUT", f"/family/own/{eid}", {"relation": "Aunt"}),
                                   ("DELETE", f"/family/own/{eid}", None), ("PUT", "/family/hidden", {"key": rec["key"], "hidden": True})):
            self.assertEqual(sam.request(method, p + path, headers=H, json=body).status_code, 403, (method, path))

        db = dbm.connect()
        try:
            bodies = [r[0] for r in db.execute("SELECT body FROM family_entries WHERE profile_id=?", (pid,))]
            self.assertTrue(bodies and all(b.startswith(crypto.FIELD) for b in bodies), "encrypted")
            self.assertNotIn("Migraine", json.dumps([dict(r) for r in db.execute("SELECT * FROM activity WHERE profile_id=?", (pid,))]),
                             "the log says what was done, not what")
            profiles.delete_profile(db, pid)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM family_entries WHERE profile_id=?", (pid,)).fetchone()[0], 0, "gone with the profile")
        finally:
            db.close()

    def test_summary_fhir_export_and_export_everything(self):
        from pypdf import PdfReader
        pid = self.profile("Family summary (fictional)")
        p = f"/api/profiles/{pid}"
        self.store(pid, fhir_data()[0], "fhir")
        brother = next(x for x in self.data(pid)["familyHistory"] if x["relation"] == "Brother")
        self.assertEqual(self.c.put(p + "/family/hidden", headers=H, json={"key": brother["key"], "hidden": True}).status_code, 200)
        self.assertEqual(self.c.post(p + "/family/own", headers=H, json={"relation": "Maternal aunt", "status": "living",
                                                                          "conditions": [{"name": "Glaucoma OWNCOND", "onset": "50"}]}).status_code, 201)
        # the visit summary: offered ticked, each relative a row, the hidden one not in it
        opts = self.c.get(p + "/summary").json()
        sec = next(s for s in opts["sections"] if s["id"] == "family")
        self.assertEqual((sec["label"], sec.get("off"), sec.get("ask")), ("Family history", None, None), "ticked at first")
        r = self.c.post(p + "/summary/preview", headers=H, json={"sections": ["family"]})
        self.assertEqual(r.status_code, 200, r.text)
        rows = {x["relative"]: x for x in r.json()["family"]}
        self.assertEqual(rows["Mother (Ann)"]["conditions"],
                         "Type 2 diabetes mellitus (onset age 52); Myocardial infarction (onset age 78) (contributed to death)")
        self.assertEqual((rows["Mother (Ann)"]["status"], rows["Maternal aunt"]["status"], rows["Maternal aunt"]["source"]),
                         ("Deceased, age 78", "Living", "Typed in"))
        self.assertNotIn("Brother", rows)
        r = self.c.post(p + "/summary/pdf", headers=H, json={"sections": ["family", "questions"]})
        self.assertEqual(r.status_code, 200, r.text[:300])
        text = "\n".join(x.extract_text() for x in PdfReader(io.BytesIO(r.content)).pages)
        for said in ("Family history", "Mother (Ann)", "Glaucoma OWNCOND", "Deceased, age 78"):
            self.assertIn(said, text)
        self.assertNotIn("Asthma", text)
        # the FHIR export: a FamilyMemberHistory each, coded where the words are a RoleCode's
        r = self.c.get(p + "/export?format=fhir")
        self.assertEqual(r.status_code, 200, r.text[:300])
        z = zipfile.ZipFile(io.BytesIO(r.content))
        bundle = json.loads(z.read("bundle.json"))
        self.assertEqual(fhir_schema_check.errors(bundle), [], "valid FHIR R4")
        fmh = {x["resource"]["relationship"]["text"]: x["resource"] for x in bundle["entry"] if x["resource"]["resourceType"] == "FamilyMemberHistory"}
        self.assertEqual(sorted(fmh), ["Maternal aunt", "Mother"], "the hidden one left out")
        m = fmh["Mother"]
        self.assertEqual((m["status"], m["name"], m["relationship"]["coding"][0]["code"], m["deceasedAge"]["value"], m["deceasedAge"]["code"],
                          m["patient"]["reference"].startswith("urn:uuid:"), m["note"]),
                         ("completed", "Ann", "MTH", 78, "a", True, [{"text": "Diagnosed in her fifties."}]))
        self.assertEqual([(c["code"]["text"], c["code"]["coding"][0]["code"], c["onsetAge"]["value"], c.get("contributedToDeath")) for c in m["condition"]],
                         [("Type 2 diabetes mellitus", "44054006", 52, None), ("Myocardial infarction", "22298006", 78, True)])
        a = fmh["Maternal aunt"]
        self.assertEqual((a["relationship"]["coding"][0]["code"], a["deceasedBoolean"], a["condition"][0]["onsetAge"]["value"]), ("MAUNT", False, 50))
        again = fhir.parse({"b.json": self._write(z.read("bundle.json"))})["data"]["familyHistory"]
        self.assertEqual(sorted(x["relation"] for x in again), ["Maternal aunt", "Mother"], "read back by the importer")
        # Export everything: the records' as imported, the entries apart, put back into another profile
        db = dbm.connect()
        try:
            raw = records.assemble(db, pid, raw=True)
            self.assertEqual(len(raw["familyHistory"]), 2, "as imported, the hidden one too")
            self.assertEqual(sorted(("own" if x["own"] else "hidden") for x in raw["familyEntries"]), ["hidden", "own"])
            other = profiles.create_profile(db, "Family restored (fictional)", owner_id=self.uid)
            with dbm.transaction(db):
                records.store(db, other, {k: v for k, v in raw.items() if k not in records.SERVED}, "upload")
                n = export.restore(db, other, raw, lambda f: None, [])
            self.assertEqual(n["familyEntries"], 2)
        finally:
            db.close()
        self.assertEqual(sorted(x["relation"] for x in self.data(other)["familyHistory"]), ["Maternal aunt", "Mother"])

    def _write(self, raw):
        path = os.path.join(TMP, "bundle-back.json")
        with open(path, "wb") as f:
            f.write(raw)
        return path


if __name__ == "__main__":
    unittest.main()
