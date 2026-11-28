#!/usr/bin/env python3
"""The emergency card (#1126, server/emergency.py, server/routes_emergency.py): what goes on it, and the files.

- What goes on it: Sanguity's own medication list (your own entries in, hidden ones out, stopped ones out); the
  records' allergies with their reactions (ended ones out; "No allergies recorded" with none, "No known allergies" only
  when a record says so, and never beside an allergy); the active problems; the blood type from a lab result (ABO/Rh,
  or ABO with Rh) over one typed, else the one typed, else "None recorded"; the emergency contacts in order.
- Blood type and contacts under About this person: checked, kept as profile settings, owners and editors change them.
- The PDF on Letter and A4, one page, its information dictionary the title alone; the lock-screen image's parts
  (short: no medications or problems). Viewers may make it, strangers can't, and each file made is logged without
  what's on it. No share link carries it.
Fictional data only.

  python3 tests/test_emergency.py
"""
import datetime, os, re, sys, tempfile, unittest, zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-emergency-test-")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_CORRECTIONS=os.path.join(TMP, "config", "corrections.json"))
for d in ("records", "keys", "build", "config", "profiles"):
    os.makedirs(os.path.join(TMP, d), exist_ok=True)
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, db as dbm, emergency, main, medications, person, profiles as prof, records  # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites", "lee": "maple tunnel brisk saddle"}
TODAY = datetime.date.today()


def day(n):
    return (TODAY + datetime.timedelta(days=n)).isoformat()


ALLERGY = {"type": "Allergy", "status": "Active", "src": "Maple Street Clinic"}
DATA = {
    "built": day(0), "source": "fictional", "costs": [], "upcoming": [],
    "events": [
        {"id": 1, "t": day(-200), "k": "med", "title": "Lisinopril 10 mg tablet", "sub": "Take 1 tablet every morning · Active"},
        {"id": 2, "t": day(-100), "k": "med", "title": "Metformin 500 mg tablet", "sub": "Take 1 tablet twice a day · Active"},
        {"id": 3, "t": day(-90), "k": "med", "title": "Amoxicillin 500 mg capsule", "sub": "Completed"},
    ],
    "details": {"1": {"type": "med", "instructions": "Take 1 tablet every morning", "status": "Active"},
                "2": {"type": "med", "instructions": "Take 1 tablet twice a day", "status": "Active"},
                "3": {"type": "med", "instructions": "Take 1 capsule 3 times a day", "status": "Completed"}},
    "problems": [
        {**ALLERGY, "name": "Allergy: Penicillin (Hives)", "substance": "Penicillin", "reactions": ["Hives"], "severity": "Severe", "date": "2015-04-02"},
        {**ALLERGY, "name": "Allergy: Penicillin (Wheezing)", "substance": "Penicillin", "reactions": ["Wheezing"], "date": "2018-01-09"},
        {**ALLERGY, "name": "Allergy: Latex", "substance": "Latex", "status": "Resolved"},
        {"type": "Problem", "name": "Asthma", "status": "Active", "date": "2010-06-01"},
        {"type": "Problem", "name": "Ankle sprain", "status": "Resolved", "date": "2019-06-01"},
    ],
    "labs": [{"name": "ABO Group", "loinc": "883-9", "unit": "", "panel": "Blood bank", "n": 0, "pts": [],
              "txt": [["2021-03-02T08:00:00", "O"]]},
             {"name": "Rh Type", "loinc": "10331-7", "unit": "", "panel": "Blood bank", "n": 0, "pts": [],
              "txt": [["2021-03-02T08:00:00", "Negative"]]}],
}


class Rules(unittest.TestCase):
    def test_checks(self):
        self.assertEqual((person.check_blood(""), person.check_blood("AB-")), ("", "AB-"))
        for bad in ("AB", "o+", "A positive", 1):
            with self.assertRaises(person.PersonError):
                person.check_blood(bad)
        self.assertEqual(person.check_contacts([{"name": " Sam  Example ", "phone": "+1 (555) 010-0100"}]),
                         [{"name": "Sam Example", "relationship": "", "phone": "+1 (555) 010-0100"}])
        for bad, says in [([{"name": "Sam"}], "phone"), ([{"phone": "555 0100"}], "name"), ([{"name": "Sam", "phone": "call me"}], "digits"),
                          ([{"name": "Sam", "phone": "555 0100", "relationship": 3}], "text"), ([{"name": "x" * 81, "phone": "5550100"}], "at most 80"),
                          ([{"name": "Sam", "phone": "5550100"}] * 6, "At most 5"), ("Sam", "list")]:
            with self.assertRaises(person.PersonError, msg=says) as e:
                person.check_contacts(bad)
            self.assertIn(says, str(e.exception))

    def test_blood_from_labs(self):
        got = emergency.blood_from_labs(DATA)
        self.assertEqual((got["type"], got["said"], got["date"]), ("O-", "O negative", "2021-03-02"), "ABO with Rh, from the same draw")
        both = {"labs": [{"name": "ABO/Rh", "loinc": "882-1", "txt": [["2019-01-01", "A POS"], ["2022-05-05T09:00:00", "AB Rh(D) positive"]]}]}
        self.assertEqual(emergency.blood_from_labs(both)["said"], "AB positive", "the latest")
        self.assertEqual(emergency.blood_from_labs({"labs": [{"name": "Blood type", "txt": [["2020-01-01", "B neg"]]}]})["said"], "B negative",
                         "a series named so, with no code")
        self.assertEqual(emergency.blood_from_labs({"labs": [{"name": "ABO Group", "loinc": "883-9", "txt": [["2020-01-01", "A"]]}]})["said"],
                         "A (Rh not in the result)", "nothing guessed")
        self.assertEqual(emergency.blood_from_labs({"labs": [{"name": "ABO/Rh", "loinc": "882-1", "txt": [["2020-01-01", "See note"]]}]})["said"],
                         "See note (as reported)")
        self.assertIsNone(emergency.blood_from_labs({"labs": [{"name": "Antibody screen", "txt": [["2020-01-01", "Negative"]]},
                                                              {"name": "Hemoglobin", "loinc": "718-7", "pts": [["2020-01-01", 13, 12, 16, "", "13"]]}]}))

    def test_allergies(self):
        allergies, nka = emergency._allergies(DATA)
        self.assertEqual([(a["name"], a["reactions"], a["severity"]) for a in allergies], [("Penicillin", ["Hives", "Wheezing"], "Severe")],
                         "one per substance, its reactions together; a resolved one left out")
        self.assertEqual(nka, "")
        none = {"problems": [{**ALLERGY, "name": "Allergy: No known allergies", "substance": "No known allergies", "code": "716186003"}]}
        self.assertEqual(emergency._allergies(none), ([], "No known allergies"), "a record that says so, in its words")
        self.assertEqual(emergency._allergies({"problems": [{**ALLERGY, "name": "Allergy: NKDA", "substance": "NKDA"}]}), ([], "NKDA"))
        c = {"allergies": [], "nka": "", "blood": None, "contacts": [], "medications": [], "problems": []}
        self.assertEqual(emergency.sections(c)[0], ("Allergies", ["No allergies recorded"]), "never 'No known allergies' without a record")
        self.assertEqual([s for s in emergency.sections(c)], [("Allergies", ["No allergies recorded"]), ("Blood type", ["None recorded"]),
                         ("Emergency contacts", ["None recorded"]), ("Current medications", ["None recorded"]), ("Active problems", ["None recorded"])],
                         "nothing guessed; no Key documents or Insurance part while there are none")


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dbm.init()
        cls.ctx = TestClient(main.app, base_url="http://testserver").__enter__()
        db = dbm.connect()
        cls.uid = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        cls.pid = prof.create_profile(db, "Robin (fictional)", owner_id=cls.uid["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (cls.uid["kim"], cls.pid))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (cls.uid["lee"], cls.pid))
        with dbm.transaction(db):
            records.store(db, cls.pid, DATA, "upload", fmt="fhir")
        db.close()
        cls.c = {}
        for n in PW:
            c = TestClient(main.app, base_url="http://testserver")
            assert c.post("/api/login", json={"username": n, "password": PW[n]}, headers=H).status_code == 200
            cls.c[n] = c

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

    def url(self, rest=""):
        return f"/api/profiles/{self.pid}{rest}"

    def test_1_about_fields(self):
        a = self.c["kim"].get(self.url("/person")).json()
        self.assertEqual((a["bloodType"], a["contacts"], a["bloodLab"]["said"]), ("", [], "O negative"), "the lab's, shown beside")
        self.assertEqual(self.c["kim"].put(self.url("/person"), json={"bloodType": "A+"}, headers=H).status_code, 403, "a viewer can't change them")
        r = self.c["lee"].put(self.url("/person"), json={"bloodType": "A+", "contacts": [
            {"name": "Sam Example", "relationship": "Spouse", "phone": "+1 555 0100"}, {"name": "Dr Lee", "phone": "555-0199"}]}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(([c["name"] for c in r.json()["contacts"]], r.json()["bloodType"]), (["Sam Example", "Dr Lee"], "A+"))
        self.assertEqual(self.c["lee"].put(self.url("/person"), json={"contacts": [{"name": "Sam"}]}, headers=H).status_code, 400)
        self.assertEqual(self.c["lee"].put(self.url("/person"), json={"bloodType": "Z"}, headers=H).status_code, 400)
        db = dbm.connect()
        row = db.execute("SELECT value, sealed FROM settings WHERE key='person.contacts' AND profile_id=?", (self.pid,)).fetchone()
        db.close()
        self.assertIsNone(row["value"], "sealed, as every About field")
        self.assertNotIn(b"Sam Example", (row["sealed"] or "").encode() if isinstance(row["sealed"], str) else row["sealed"])

    def test_2_card(self):
        robin = self.c["robin"]
        key = medications.med_key("Metformin 500 mg tablet")
        self.assertEqual(robin.put(self.url("/medications/hidden"), json={"key": key, "hidden": True}, headers=H).status_code, 200)
        self.assertEqual(robin.post(self.url("/medications/own"), json={"name": "Vitamin D3", "dose": "1000 IU daily", "start": day(-10)}, headers=H).status_code, 201)
        c = self.c["kim"].get(self.url("/emergency")).json()   # a viewer may make it
        self.assertEqual([m["name"] for m in c["medications"]], ["Lisinopril 10 mg tablet", "Vitamin D3"],
                         "Sanguity's own list: your own in, hidden and completed ones out")
        self.assertEqual([(a["name"], a["reactions"]) for a in c["allergies"]], [("Penicillin", ["Hives", "Wheezing"])])
        self.assertEqual([p["name"] for p in c["problems"]], ["Asthma"])
        self.assertEqual((c["blood"]["said"], c["blood"]["from"], c["blood"]["date"]), ("O negative", "lab", "2021-03-02"),
                         "the lab's over the one typed")
        self.assertEqual([x["name"] for x in c["contacts"]], ["Sam Example", "Dr Lee"], "in order")
        self.assertEqual((c["asOf"], c["insurance"], c["documents"]), (day(0), [], []))
        heads = [s["heading"] for s in c["sections"]]
        self.assertEqual(heads, ["Allergies", "Blood type", "Emergency contacts", "Current medications", "Active problems"])
        self.assertTrue(c["sections"][1]["lines"][0].startswith("O negative (from your lab result of "))
        self.assertEqual([s["heading"] for s in c["short"]], ["Allergies", "Blood type", "Emergency contacts"],
                         "the lock-screen image: no medications or problems")
        self.assertEqual(c["short"][0]["lines"], ["Penicillin (Hives, Wheezing), severe"])

    def test_3_typed_blood_without_a_lab(self):
        db = dbm.connect()
        pid = prof.create_profile(db, "Kit (fictional)", owner_id=self.uid["robin"])
        db.close()
        robin = self.c["robin"]
        c = robin.get(f"/api/profiles/{pid}/emergency").json()
        self.assertEqual((c["blood"], c["sections"][0]["lines"], c["medications"]), (None, ["No allergies recorded"], []))
        robin.put(f"/api/profiles/{pid}/person", json={"bloodType": "B-"}, headers=H)
        c = robin.get(f"/api/profiles/{pid}/emergency").json()
        self.assertEqual((c["blood"]["said"], c["blood"]["from"], c["sections"][1]["lines"]), ("B negative", "typed", ["B negative (as typed in)"]))

    def test_4_files(self):
        for paper, box in (("letter", b"/MediaBox [0 0 612 792]"), ("a4", b"/MediaBox [0 0 595.28 841.89]")):
            r = self.c["kim"].post(self.url("/emergency/pdf"), json={"paper": paper}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            pdf = r.content
            self.assertTrue(pdf.startswith(b"%PDF-") and box in pdf and pdf.count(b"/Type /Page ") == 1, paper)
            self.assertIn(f'filename="emergency-card-Robin-fictional-{day(0)}.pdf"', r.headers["content-disposition"])
            self.assertEqual(r.headers["cache-control"], "no-store")
            info = re.search(rb"/Info (\d+) 0 R", pdf).group(1)
            self.assertEqual(re.search(rb"\n" + info + rb" 0 obj\n(.*?)\nendobj", pdf, re.S).group(1),
                             b"<< /Title (Emergency card for Robin \\(fictional\\)) >>", "the title, and nothing else about it")
            ops = zlib.decompress(re.search(rb"stream\n(.*?)\nendstream", pdf, re.S).group(1))
            for words in (b"Penicillin \\(Hives, Wheezing\\), severe", b"O negative", b"Sam Example \\(Spouse\\): +1 555 0100", b"Vitamin D3", b"Asthma"):
                self.assertIn(words, ops)
            self.assertNotIn(b"Metformin", ops, "hidden: left out")
            self.assertNotIn(b" rg", ops.replace(b"0.000 0.000 0.000 rg", b"").replace(b"0.350 0.350 0.350 rg", b""), "black and grey only")
        self.assertEqual(self.c["kim"].post(self.url("/emergency/pdf"), json={"paper": "legal"}, headers=H).status_code, 422)
        r = self.c["kim"].post(self.url("/emergency/image"), json={"wallet": True}, headers=H)
        self.assertEqual((r.status_code, r.json()["filename"]), (200, f"emergency-card-Robin-fictional-{day(0)}.png"))
        acts = self.c["robin"].get(self.url("/activity?action=emergency")).json()
        acts = acts.get("entries", acts) if isinstance(acts, dict) else acts
        self.assertEqual(sorted(e["detail"]["file"] for e in acts), ["pdf", "pdf", "png"], "each file made, logged")
        self.assertFalse(any("Penicillin" in str(e) for e in acts), "never what's on it")

    def test_5_only_signed_in(self):
        self.assertIn(TestClient(main.app, base_url="http://testserver").get(self.url("/emergency")).status_code, (401, 403))
        db = dbm.connect()
        accounts.create(db, "ada", password="a stranger to every record")
        db.close()
        c = TestClient(main.app, base_url="http://testserver")
        c.post("/api/login", json={"username": "ada", "password": "a stranger to every record"}, headers=H)
        self.assertEqual(c.get(self.url("/emergency")).status_code, 404, "not shared: not found")
        self.assertEqual(c.post(self.url("/emergency/pdf"), json={}, headers=H).status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=1)
