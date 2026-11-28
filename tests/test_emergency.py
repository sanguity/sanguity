#!/usr/bin/env python3
"""The emergency card (#1126, #1162; server/ecard.py, server/emergency.py, server/routes_emergency.py).

- Nothing on it by itself: a card never saved is empty whatever the records hold; the records only suggest (allergies
  with their reactions, "No known allergies" only when a record says so, the blood type from a lab result and the one
  typed, the contacts, the current medications with their dose from Sanguity's own list, the active problems, the
  care team from the visits, insurance plans, key documents on file, the birth date and the latest weight), and a
  suggestion on the card (added from it, or in its words) isn't offered again.
- The "changed since" note: a suggestion there wasn't when the card was saved is new, counted, and the card unchanged.
- What goes where: sections in the person's order, Critical alerts always first, each on the printed card, the lock
  screen, both or neither (the lock screen's at first: alerts, allergies, blood type, contacts); the legal name over
  the profile's; Personal details as fields; "Card last updated".
- The QR code: off at first; its text the card's own words, at most 800 bytes, alerts, allergies, blood type, contacts
  and medications kept first, what's left out said; made by www/js/qr.js, it reads back (tests/test_qr.py's reader)
  to that text, and the PDF draws it.
- The PDF, full page and wallet card, on Letter and A4, its information dictionary the title alone; a full page too
  long goes on to a second, never cut; the wallet card says "and N more".
- Stored sealed per profile, in the export's settings; owners and editors change it, viewers make its files, strangers
  can't, and each file made or save is logged without what's on it.
Fictional data only.

  python3 tests/test_emergency.py
"""
import datetime, io, json, os, re, sys, tempfile, unittest, zipfile, zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-emergency-test-")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_CORRECTIONS=os.path.join(TMP, "config", "corrections.json"))
for d in ("records", "keys", "build", "config", "profiles"):
    os.makedirs(os.path.join(TMP, d), exist_ok=True)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, db as dbm, ecard, emergency, main, medications, person, profiles as prof, records  # noqa: E402
import test_qr   # noqa: E402  (its reader: the QR code read back)
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
        {"id": 4, "t": day(-20), "k": "visit", "title": "Transplant follow-up", "sub": "Dr Rivera"},
        {"id": 5, "t": day(-60), "k": "visit", "title": "Lab visit", "sub": "Fictional Lab"},
    ],
    "clinicWeights": [[day(-400), 160.0], [day(-30), 155.0]],
    "details": {"1": {"type": "med", "instructions": "Take 1 tablet every morning", "status": "Active"},
                "2": {"type": "med", "instructions": "Take 1 tablet twice a day", "status": "Active"},
                "3": {"type": "med", "instructions": "Take 1 capsule 3 times a day", "status": "Completed"},
                "4": {"type": "visit", "who": "Dr Rivera", "where": "Fictional Transplant Center"},
                "5": {"type": "visit", "who": "", "where": "Fictional Lab"}},
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

    def test_card_checked(self):
        c = ecard.check({"name": "  Robin  Q. Example ", "order": ["medications", "alerts", "nope", "medications"],
                         "sections": {"alerts": {"lines": [{"text": " Irradiated  blood products only "}, {"text": ""}, "DNR"]},
                                      "medications": {"print": False, "lock": 1}, "nope": {}},
                         "details": {"donor": "Yes", "x": "y"}, "qr": {"lock": True}, "seen": ["ab12", "not hex!"], "saved": "soon"})
        self.assertEqual(c["name"], "Robin Q. Example")
        self.assertEqual(c["order"][:2], ["alerts", "medications"], "critical alerts always first")
        self.assertEqual(sorted(c["order"]), sorted(ecard.IDS), "every section, once")
        self.assertEqual([x["text"] for x in c["sections"]["alerts"]["lines"]], ["Irradiated blood products only", "DNR"])
        self.assertEqual((c["sections"]["medications"]["print"], c["sections"]["medications"]["lock"]), (False, True))
        self.assertEqual((c["details"]["donor"], "x" in c["details"], c["qr"], c["seen"], c["saved"]), ("Yes", False, {"on": False, "lock": False}, ["ab12"], ""))
        e = ecard.empty()
        self.assertEqual([i for i in ecard.IDS if e["sections"][i]["lock"]], ["alerts", "allergies", "blood", "contacts"],
                         "the lock screen at first: alerts, allergies, blood type, contacts (and the name)")
        self.assertTrue(all(s["print"] for s in e["sections"].values()))
        for bad, says in [({"sections": {"devices": {"lines": ["x"] * 41}}}, "at most 40"), ({"name": "x" * 121}, "at most 120"),
                          ({"sections": {"alerts": {"lines": [{"text": 3}]}}}, "text"), ([], "object")]:
            with self.assertRaises(ValueError, msg=says) as err:
                ecard.check(bad)
            self.assertIn(says, str(err.exception))

    def test_qr_text(self):
        card = ecard.empty()
        card["sections"]["alerts"]["lines"] = [{"text": "Irradiated blood products only", "from": ""}]
        card["sections"]["devices"]["lines"] = [{"text": f"Fictional device {i} " + "x" * 40, "from": ""} for i in range(8)]
        card["sections"]["medications"]["lines"] = [{"text": f"Fictional medication {i}: 10 mg daily " + "y" * 30, "from": ""} for i in range(12)]
        card["sections"]["allergies"]["lines"] = [{"text": "Penicillin (hives), severe", "from": ""}]
        card["order"] = ["alerts", "devices", "medications", "allergies"] + [i for i in ecard.IDS if i not in ("alerts", "devices", "medications", "allergies")]
        card["qr"]["on"], card["saved"] = True, "2026-11-28"
        v = emergency.view(card, "Robin (fictional)")
        text, left = v["qr"]["text"], v["qr"]["left"]
        self.assertLessEqual(len(text.encode()), emergency.QR_MOST)
        self.assertTrue(text.startswith("EMERGENCY CARD\nRobin (fictional)\nCRITICAL ALERTS\n- Irradiated blood products only\n"), text[:120])
        self.assertTrue(text.endswith("\nCard last updated Nov 28, 2026"))
        self.assertIn("- Penicillin (hives), severe", text, "allergies before devices, though the card puts devices first")
        self.assertLess(text.index("CURRENT MEDICATIONS"), text.index("ALLERGIES"), "the card's order kept in the text")
        self.assertEqual([(x["id"], x["of"]) for x in left], [("devices", 8), ("medications", 12)], "in the card's order")
        self.assertEqual(left[0]["left"], 8, "medications come before the other parts: no room left for devices")
        self.assertGreater(12 - left[1]["left"], 0, "some medications kept")
        self.assertEqual(emergency._qr_note(v), f"Not in the QR code (too long for one): devices and implants, {left[1]['left']} of 12 current medications.")
        card["qr"]["on"] = False
        self.assertEqual(emergency.view(card, "Robin")["qr"]["text"], "", "off at first: no QR code")


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

    def get(self, who="lee"):
        r = self.c[who].get(self.url("/emergency"))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def said(self, got, section):
        return [x["text"] for x in got["suggestions"] if x["section"] == section]

    def pdf(self, who="kim", **body):
        r = self.c[who].post(self.url("/emergency/pdf"), json=body, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return r

    def test_1_about_fields(self):
        a = self.c["kim"].get(self.url("/person")).json()
        self.assertEqual((a["bloodType"], a["contacts"], a["bloodLab"]["said"]), ("", [], "O negative"), "the lab's, shown beside")
        self.assertEqual(self.c["kim"].put(self.url("/person"), json={"bloodType": "A+"}, headers=H).status_code, 403, "a viewer can't change them")
        r = self.c["lee"].put(self.url("/person"), json={"bloodType": "A+", "birthDate": "1980-01-02", "contacts": [
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

    def test_2_nothing_automatic_the_records_suggest(self):
        robin = self.c["robin"]
        key = medications.med_key("Metformin 500 mg tablet")
        self.assertEqual(robin.put(self.url("/medications/hidden"), json={"key": key, "hidden": True}, headers=H).status_code, 200)
        self.assertEqual(robin.post(self.url("/medications/own"), json={"name": "Vitamin D3", "dose": "1000 IU daily", "start": day(-10)}, headers=H).status_code, 201)
        got = self.get()
        self.assertEqual((got["view"]["parts"], got["view"]["name"], got["view"]["updated"], got["newSince"]),
                         ([], "Robin (fictional)", "Card not saved yet", 0), "never saved: nothing on it, whatever the records hold")
        self.assertNotIn(b"Penicillin", zlib.decompress(re.search(rb"stream\n(.*?)\nendstream", self.pdf().content, re.S).group(1)))
        self.assertEqual(self.said(got, "allergies"), ["Penicillin (Hives, Wheezing), severe"], "one per substance, its reactions; a resolved one out")
        self.assertEqual(self.said(got, "medications"), ["Lisinopril 10 mg tablet: Take 1 tablet every morning", "Vitamin D3: 1000 IU daily"],
                         "Sanguity's own list with the dose: your own in, hidden and completed ones out")
        self.assertEqual(self.said(got, "conditions"), ["Asthma"])
        self.assertEqual(self.said(got, "blood"), ["O negative (from a lab result of Mar 2, 2021)", "A positive"], "the lab's, and the one typed")
        self.assertEqual(self.said(got, "contacts"), ["Sam Example (Spouse): +1 555 0100", "Dr Lee: 555-0199"], "in order")
        self.assertEqual(self.said(got, "careteam"), ["Dr Rivera, Fictional Transplant Center", "Fictional Lab"], "the visits' providers, newest first")
        self.assertEqual([(x["field"], x["text"]) for x in got["suggestions"] if x["section"] == "details"],
                         [("dob", "Jan 2, 1980"), ("weight", "70.3 kg (155 lb) on " + emergency._date({}, day(-30)))])
        self.assertEqual(self.said(got, "insurance") + self.said(got, "documents"), [])
        self.assertEqual(self.get("kim").get("suggestions"), None, "a viewer isn't offered anything: they don't change it")

    def test_3_saved_placed_and_changed_since(self):
        got = self.get()
        pen = next(x for x in got["suggestions"] if x["section"] == "allergies")
        card = got["card"]
        card["name"] = "Robin Quinn Example"
        card["sections"]["alerts"]["lines"] = [{"text": "Irradiated blood products only"}, {"text": "Allogeneic stem cell transplant recipient"}]
        card["sections"]["allergies"]["lines"] = [{"text": "Penicillin (hives, wheezing): anaphylaxis risk", "from": pen["key"]}]   # reworded
        card["sections"]["conditions"]["lines"] = [{"text": "asthma"}]   # the suggestion's words, typed
        card["sections"]["contacts"]["lines"] = [{"text": "Sam Example (Spouse): +1 555 0100"}]
        card["sections"]["medications"]["lines"] = [{"text": "Lisinopril 10 mg tablet"}]
        card["sections"]["medications"]["lock"] = True
        card["sections"]["devices"]["lines"] = [{"text": "Port, right chest"}]
        card["sections"]["devices"]["print"], card["sections"]["devices"]["lock"] = False, True
        card["sections"]["documents"]["lines"] = [{"text": "Fictional directive: on file"}]
        card["sections"]["documents"]["print"] = False   # and not on the lock screen: on neither
        card["details"]["language"], card["details"]["donor"] = "English", "Yes"
        card["order"] = ["devices", "medications"] + [i for i in card["order"] if i not in ("devices", "medications")]
        self.assertEqual(self.c["kim"].put(self.url("/emergency"), json={"card": card}, headers=H).status_code, 403, "a viewer can't change it")
        pv = self.c["kim"].post(self.url("/emergency/preview"), json={"card": card}, headers=H).json()
        self.assertEqual(pv["updated"], "Card not saved yet", "a preview keeps nothing")
        self.assertEqual(self.get()["view"]["parts"], [])
        r = self.c["lee"].put(self.url("/emergency"), json={"card": card}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        got = r.json()
        v = got["view"]
        self.assertEqual((v["name"], v["updated"]), ("Robin Quinn Example", "Card last updated " + emergency._date({}, day(0))))
        self.assertEqual([(p["id"], p["print"], p["lock"]) for p in v["parts"]],
                         [("alerts", True, True), ("devices", False, True), ("medications", True, True), ("details", True, False),
                          ("allergies", True, True), ("contacts", True, True), ("conditions", True, False)],
                         "in the person's order, alerts first; key documents on neither file left out; empty sections left out")
        self.assertEqual(next(p for p in v["parts"] if p["id"] == "details")["lines"], ["Preferred language: English", "Organ donor: Yes"])
        self.assertEqual(self.said(got, "allergies") + self.said(got, "conditions") + self.said(got, "contacts"), ["Dr Lee: 555-0199"],
                         "on the card (added and reworded, or in its words): not offered again")
        self.assertEqual((got["newSince"], got["gone"]), (0, []))
        self.assertIn("Lisinopril 10 mg tablet: Take 1 tablet every morning", self.said(got, "medications"), "other words: still offered")
        db = dbm.connect()
        row = db.execute("SELECT value, sealed FROM settings WHERE key='emergency.card' AND profile_id=?", (self.pid,)).fetchone()
        db.close()
        self.assertIsNone(row["value"], "sealed")
        self.assertNotIn("Irradiated", row["sealed"])
        exported = self.c["robin"].get(self.url("/export"))
        self.assertEqual(exported.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(exported.content)) as z:
            name = next(n for n in z.namelist() if n.endswith("settings.json"))
            self.assertEqual(json.loads(z.read(name))["emergency"]["card"]["sections"]["alerts"]["lines"][0]["text"],
                             "Irradiated blood products only", "in the export")
        # the records change: a new medication is a new suggestion, counted; the card itself unchanged
        self.assertEqual(self.c["robin"].post(self.url("/medications/own"), json={"name": "Tacrolimus", "dose": "1 mg twice a day", "start": day(-1)},
                                              headers=H).status_code, 201)
        later = self.get()
        self.assertEqual(later["newSince"], 1)
        self.assertEqual([x["text"] for x in later["suggestions"] if x["new"]], ["Tacrolimus: 1 mg twice a day"])
        self.assertEqual(later["view"], v, "the card unchanged")
        self.assertEqual(self.get("kim")["view"], v, "a viewer sees the saved card")
        acts = self.c["robin"].get(self.url("/activity?action=emergency")).json()
        acts = acts.get("entries", acts) if isinstance(acts, dict) else acts
        self.assertIn({"saved": True}, [e["detail"] for e in acts])
        self.assertFalse(any("Irradiated" in str(e) for e in acts), "never what's on it")

    def test_4_files_every_format_and_paper(self):
        for form in ("page", "wallet"):
            for paper, box in (("letter", b"/MediaBox [0 0 612 792]"), ("a4", b"/MediaBox [0 0 595.28 841.89]")):
                with self.subTest(form=form, paper=paper):
                    r = self.pdf(paper=paper, form=form)
                    pdf = r.content
                    self.assertTrue(pdf.startswith(b"%PDF-") and box in pdf and pdf.count(b"/Type /Page ") == 1)
                    self.assertIn(f'filename="emergency-card-Robin-fictional{"-wallet" if form == "wallet" else ""}-{day(0)}.pdf"',
                                  r.headers["content-disposition"])
                    self.assertEqual(r.headers["cache-control"], "no-store")
                    info = re.search(rb"/Info (\d+) 0 R", pdf).group(1)
                    self.assertEqual(re.search(rb"\n" + info + rb" 0 obj\n(.*?)\nendobj", pdf, re.S).group(1),
                                     b"<< /Title (Emergency card for Robin Quinn Example) >>", "the title, and nothing else about it")
                    ops = zlib.decompress(re.search(rb"stream\n(.*?)\nendstream", pdf, re.S).group(1))
                    for words in (b"(Irradiated blood products only) Tj", b"(CRITICAL ALERTS) Tj", b"Lisinopril 10 mg tablet", b"Robin Quinn Example",
                                  b"Card last updated", b"Organ donor: Yes", b"Sam Example \\(Spouse\\): +1 555 0100"):
                        self.assertIn(words, ops)
                    self.assertIn(b"/F2 ", ops.split(b"(Irradiated blood products only) Tj")[0][-80:], "a critical alert in bold")
                    self.assertNotIn(b"Port, right chest", ops, "on the lock screen alone: not printed")
                    self.assertNotIn(b"Fictional directive", ops, "on neither file")
                    self.assertNotIn(b"Vitamin D3", ops, "a suggestion not added: not printed")
                    self.assertNotIn(b" rg", ops.replace(b"0.000 0.000 0.000 rg", b"").replace(b"0.350 0.350 0.350 rg", b""), "black and grey only")
                    self.assertNotIn(b" re f", ops, "no QR code: it's off")
        self.assertEqual(self.c["kim"].post(self.url("/emergency/pdf"), json={"paper": "legal"}, headers=H).status_code, 422)
        self.assertEqual(self.c["kim"].post(self.url("/emergency/pdf"), json={"form": "poster"}, headers=H).status_code, 422)
        r = self.c["kim"].post(self.url("/emergency/image"), json={"wallet": True}, headers=H)
        self.assertEqual((r.status_code, r.json()["filename"]), (200, f"emergency-card-Robin-fictional-{day(0)}.png"))
        self.assertEqual([p["id"] for p in emergency.lock_parts(r.json())], ["alerts", "devices", "medications", "allergies", "contacts"])
        acts = self.c["robin"].get(self.url("/activity?action=emergency")).json()
        acts = acts.get("entries", acts) if isinstance(acts, dict) else acts
        self.assertEqual(sorted(e["detail"].get("file", "") for e in acts if "file" in e["detail"])[-5:], ["pdf"] * 4 + ["png"], "each file made, logged")

    def test_5_qr_code_reads_back_to_the_cards_text(self):
        card = self.get()["card"]
        card["qr"] = {"on": True, "lock": True}
        card["sections"]["careteam"]["lines"] = [{"text": f"Fictional clinic {i}: +1 555 01{i:02d} (24-hour line) " + "z" * 30} for i in range(14)]
        got = self.c["lee"].put(self.url("/emergency"), json={"card": card}, headers=H).json()
        q = got["view"]["qr"]
        self.assertLessEqual(len(q["text"].encode()), 800)
        self.assertEqual([(x["heading"], x["of"]) for x in q["left"]], [("Care team", 14)], "what didn't fit, said")
        for p in got["view"]["parts"]:
            if p["id"] != "careteam":
                for line in p["lines"]:
                    self.assertIn(f"\n- {line}\n", q["text"] + "\n")
        mods = test_qr.qr_js("qrCode(DATA, {most: 23}).modules.map(r => r.join(''))", q["text"])
        self.assertEqual(test_qr.read_qr([[int(b) for b in r] for r in mods]).decode(), q["text"], "the code holds the card's text")
        self.assertLessEqual(len(mods), 17 + 4 * 23)
        for form in ("page", "wallet"):
            ops = zlib.decompress(re.search(rb"stream\n(.*?)\nendstream", self.pdf(form=form, qr=mods).content, re.S).group(1))
            runs = sum(len(re.findall("1+", r)) for r in mods)
            self.assertEqual(ops.count(b" re f"), runs, "each run of dark modules drawn")
            self.assertIn(b"(Scan with a", ops)
            self.assertIn(b"Not in the QR code", ops)
        for bad in (["01"] * 2, ["0" * 21] * 20, ["2" * 21] * 21):
            self.assertEqual(self.c["kim"].post(self.url("/emergency/pdf"), json={"qr": bad}, headers=H).status_code, 400)

    def test_6_a_long_card(self):
        """a full page too long goes on to the next, nothing cut; the wallet card says what didn't fit"""
        card = self.get()["card"]
        card["qr"] = {"on": False, "lock": False}
        for i in ecard.IDS:
            if i != "details":
                card["sections"][i]["lines"] = [{"text": f"Fictional {i} line {n} that goes on for a while to wrap on the wallet card"} for n in range(30)]
        self.assertEqual(self.c["lee"].put(self.url("/emergency"), json={"card": card}, headers=H).status_code, 200)
        for paper in ("letter", "a4"):
            pdf = self.pdf(paper=paper).content
            ops = b"".join(zlib.decompress(m) for m in re.findall(rb"stream\n(.*?)\nendstream", pdf, re.S))
            self.assertGreater(pdf.count(b"/Type /Page "), 1)
            self.assertEqual([i for i in ecard.IDS if i != "details" and card["sections"][i]["print"] and f"(Fictional {i} line 29 ".encode() not in ops], [],
                             "nothing printed is cut")
            wallet = zlib.decompress(re.search(rb"stream\n(.*?)\nendstream", self.pdf(paper=paper, form="wallet").content, re.S).group(1))
            self.assertRegex(wallet, rb"\(and \d+ more: see the full-page card\) Tj")
            self.assertRegex(wallet, rb"lines didn't fit the wallet card")

    def test_7_only_signed_in(self):
        self.assertIn(TestClient(main.app, base_url="http://testserver").get(self.url("/emergency")).status_code, (401, 403))
        db = dbm.connect()
        accounts.create(db, "ada", password="a stranger to every record")
        db.close()
        c = TestClient(main.app, base_url="http://testserver")
        c.post("/api/login", json={"username": "ada", "password": "a stranger to every record"}, headers=H)
        self.assertEqual(c.get(self.url("/emergency")).status_code, 404, "not shared: not found")
        self.assertEqual(c.post(self.url("/emergency/pdf"), json={}, headers=H).status_code, 404)
        self.assertEqual(c.put(self.url("/emergency"), json={"card": {}}, headers=H).status_code, 404)

    def test_8_insurance(self):
        """(#1137) each insurance plan suggested in one line (insurance.card_lines): its name, member ID and group
        number, never its phone numbers"""
        robin = self.c["robin"]
        r = robin.post(self.url("/insurance"), json={"name": "Fictional Health Plan", "member": "FX123456", "group": "G-77",
                                                     "phones": [{"label": "Member services", "number": "+1 555 0142"}]}, headers=H)
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(robin.post(self.url("/insurance"), json={"name": "Fictional Dental"}, headers=H).status_code, 201)
        try:
            got = self.get()
            self.assertEqual(self.said(got, "insurance"), ["Fictional Health Plan · Member ID FX123456 · Group G-77", "Fictional Dental"])
            self.assertEqual({x["from"] for x in got["suggestions"] if x["section"] == "insurance"}, {"insurance"})
            self.assertNotIn("555 0142", str(got["suggestions"]), "no phone numbers")
        finally:
            for p in robin.get(self.url("/insurance")).json()["plans"]:
                robin.delete(self.url(f"/insurance/{p['id']}"), headers=H)
        self.assertEqual(self.said(self.get(), "insurance"), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
