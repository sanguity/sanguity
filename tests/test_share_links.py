#!/usr/bin/env python3
"""Share links (server/share_links.py, server/routes_share_links.py, issue #47): a link shows only the sections, labs
and date range its owner chose, and nothing else (no documents, no report text, no other section or profile); it is
read-only; only its token's hash is stored, with its name and scope encrypted; it stops working when it expires, when
it's revoked, or when its maker stops being an owner; anything refused is the same bare 404; viewers get no cookie
and theirs are ignored; views are throttled; every view is logged in the profile's activity without the token; only
owners make, list and revoke links. Fictional data only.

  python3 tests/test_share_links.py
"""
import json, os, re, sys, tempfile, unittest
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-share-test-")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "no-build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"))
os.makedirs(os.path.join(TMP, "records"))
os.makedirs(os.path.join(TMP, "keys"))
sys.path.insert(0, ROOT)

import logging, shutil   # noqa: E402

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, crypto, db as dbm, main, measures, medications, profiles as prof, records, share_links   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites", "lee": "maple tunnel brisk saddle",
      "ash": "willow kettle amber stone", "zed": "a stranger to every profile"}
TODAY = date.today()
D = lambda n: (TODAY + timedelta(days=n)).isoformat()

# a fictional person: visits inside and outside the range, two labs, a medication, a procedure, claims, appointments
DATA = {"built": "2026-11-01T00:00:00+00:00", "source": "fictional",
        "events": [{"id": 1, "t": D(-30) + "T09:00:00", "k": "visit", "title": "Annual physical", "sub": "Dr. Fictional Example"},
                   {"id": 2, "t": D(-400), "k": "visit", "title": "Long-ago visit OLDVISIT", "sub": ""},
                   {"id": 3, "t": D(-60), "k": "procedure", "title": "Fictional colonoscopy", "sub": ""},
                   {"id": 4, "t": D(-90), "k": "med", "title": "Examplecillin 250 mg", "sub": ""},
                   {"id": 5, "t": D(-20), "k": "imaging", "title": "Chest X-ray", "sub": ""},
                   {"id": 6, "t": D(-10), "k": "visit", "title": "Hidden visit HIDDENVISIT", "sub": "", "hidden": True}],
        "details": {"3": {"text": "REPORTTEXT the full report of the procedure"}, "4": {"instructions": "Take one daily", "status": "active"},
                    "1": {"note": "DETAILNOTE private visit note"}},
        "costs": [{"id": 1, "d": D(-30), "billed": 200.0, "paid": 150.0, "owe": 50.0, "title": "Office visit CLAIMTITLE", "prov": "Riverside Clinic"}],
        "labs": [{"name": "Hemoglobin", "unit": "g/dL", "panel": "CBC", "n": 2, "txt": [],
                  "pts": [[D(-30) + "T08:00:00", 11.1, 12, 16, "L"], [D(-400) + "T08:00:00", 13.5, 12, 16, ""]]},
                 {"name": "SodiumSECRET", "unit": "mmol/L", "panel": "BMP", "n": 1, "txt": [], "pts": [[D(-30) + "T08:00:00", 140, 135, 145, ""]]}],
        "upcoming": [{"t": D(12) + "T10:30:00", "title": "Follow-up visit", "who": "Dr. Fictional Example", "where": "Riverside Clinic"},
                     {"t": D(-3), "title": "Already happened PASTAPPT", "who": "", "where": ""}]}


# #225: a fictional phone's readings, in the watch summary's shape (watch/receiver.py)
U = lambda n, h: f"{D(n)}T{h:02d}:00:00+00:00"
PHONE = {"tz": "UTC", "exercise": [], "metrics": [
    {"key": "spo2_avg", "unit": "%", "pts": [[D(-10), 97.2], [D(-1), 96.0]]},
    {"key": "glucose_meter", "unit": "mg/dL", "pts": [[D(-4) + "T07:12", 104.0]]},
    *({"key": "glucose_" + k, "unit": "", "pts": [[D(-5), v]]} for k, v in (("avg", 131.0), ("min", 62.0), ("max", 212.0), ("n", 288),
                                                                          ("hours", 24.0), ("lt54", 0.0), ("lt70", 4.0), ("in", 78.0), ("gt180", 18.0)))],
    "bp_readings": [[U(-40, 8), 150, 95, 70, "Fictional cuff", "Health Connect"], [U(-9, 8), 132, 84, 68, "Fictional cuff", "Health Connect"],
                    [U(-2, 20), 126, 80, None, None, "Apple Health"]],
    "temp_readings": [[U(-8, 7), 37.4, None, "Health Connect"]],
    "ecg": [{"key": "ecg:0123456789abcdef", "type": "ecg", "start": U(-6, 10), "classification": "Sinus Rhythm", "avg_hr": 72}]}

# #232: glucose and blood oxygen typed in at home (#224: local wall times, glucose in mg/dL with a meal note), beside the
# phone's daily blood oxygen; one glucose the phone's meter sent too
TYPED = {"tz": "America/New_York", "exercise": [], "metrics": [
    {"key": "spo2_avg", "unit": "%", "pts": [[D(-8), 96.5]]}, {"key": "spo2_min", "unit": "%", "pts": [[D(-8), 93.0]]},
    {"key": "glucose_meter", "unit": "mg/dL", "pts": [[D(-6) + "T07:12", 99.0]]}],
    "glucose_readings": [[D(-6) + "T07:12:00", 99.0, None, "Typed in", None], [D(-5) + "T07:45:00", 104.49, None, "Typed in", "before"],
                         [D(-30) + "T08:00:00", 140.0, None, "Typed in", "after"]],
    "spo2_readings": [[D(-4) + "T08:05:00", 97, None, "Typed in"], [D(-40) + "T08:05:00", 91, None, "Typed in"]]}


# #581: a profile with each kind of source: a device's and an app's readings, one at a time, an ECG's and a notification's
# device, a phone's dose with its app and amount, a CGM's day; stays transferred between hospitals, one told from
# inpatient results, and a pharmacy claim (its service the medicine)
SOURCES = {"tz": "UTC", "exercise": [], "metrics": [
    {"key": "spo2_avg", "unit": "%", "pts": [[D(-10), 97.2]]}, {"key": "spo2_min", "unit": "%", "pts": [[D(-10), 92.0]]},
    {"key": "glucose_meter", "unit": "mg/dL", "pts": [[D(-4) + "T07:12", 104.0]]},
    *({"key": "glucose_" + k, "unit": "", "pts": [[D(-5), v]]} for k, v in (("avg", 131.0), ("min", 62.0), ("max", 212.0), ("n", 288),
                                                                          ("hours", 23.5), ("lt54", 0.0), ("lt70", 4.0), ("in", 78.0), ("gt180", 18.0))),
    {"key": "steps", "unit": "count", "pts": [[D(-6), 9400], [D(-5), 4100]]},   # each day's count from the phone (#298)
    # #719: the CGM day's readings by whole mg/dL (glucose.py): 12 at 62, 200 at 120, 76 at 190, for a CGM target
    {"key": "glucose_dist", "unit": "readings", "pts": [[D(-5), [62, 12, *[0] * 57, 200, *[0] * 69, 76]]]}],
    "bp_readings": [[U(-9, 8), 132, 84, 68, "Fictional cuff", "Health Connect"], [D(-2) + "T20:00:00", 126, 80, None, None, "Typed in"]],
    "temp_readings": [[U(-8, 7), 37.4, "Fictional thermometer", "Health Connect"]],
    "spo2_readings": [[U(-4, 8), 95, "FICTIONAL-OXIMETER-X1", "Health Connect"], [D(-3) + "T09:00:00", 97, None, "Typed in"]],
    "glucose_readings": [[D(-6) + "T07:45:00", 104.0, None, "Typed in", "before"]],
    "ecg": [{"key": "ecg:0123456789abcdef", "type": "ecg", "start": U(-6, 10), "classification": "Sinus Rhythm", "avg_hr": 72,
             "device": "FICTIONAL-WATCH-DEVICE"},
            {"key": "rhythm:0123456789abcdee", "type": "irregular_rhythm", "start": U(-7, 3), "end": U(-7, 4), "device": "FICTIONAL-WATCH-DEVICE"}],
    "med_doses": [[U(-5, 9), "Fictionalol 10 mg tablet", "taken", 2, "FICTIONAL-UNITS", "FICTIONAL-APP"]]}
# #650: its targets, a steps target among them (#298), against each day's count from the phone (SOURCES' steps); and a
# CGM target (#719), against its CGM day
SOURCES_TARGETS = {"bp": {"sysHigh": 130, "diaHigh": 80, "by": "Dr. Fictional Example"}, "steps": {"low": 8000, "by": "Dr. Fictional Example"},
                   "cgm": {"low": 70, "high": 140, "unit": "mg/dL", "by": "Dr. Fictional Example"}}
SOURCES_DATA = {"events": [
    {"id": 1, "t": D(-15) + "T00:00:00", "end": D(-12) + "T23:59:00", "k": "stay", "title": "Hospital stay · Fictional General",
     "sub": "Ward 5 · 3 nights · transferred to Other Fictional Hospital"},
    {"id": 2, "t": D(-12) + "T00:00:00", "end": D(-10) + "T23:59:00", "k": "stay", "title": "Hospital stay · Other Fictional Hospital",
     "sub": "2 nights · transferred from Fictional General"},
    {"id": 3, "t": D(-50) + "T00:00:00", "end": D(-48) + "T23:59:00", "k": "stay", "title": "Hospital stay",
     "sub": f"Admitted {D(-50)} · last inpatient result {D(-48)}"},
    {"id": 4, "t": D(-25) + "T09:00:00", "k": "med", "title": "Fictionalol 10 mg tablet"}],
    "labs": [], "details": {"4": {"instructions": "Once daily", "status": "Active"}},
    "costs": [{"id": 4, "d": D(-25), "billed": 40.0, "paid": 30.0, "owe": 10.0, "kind": "pharmacy", "cat": "Pharmacy",
               "title": "Fictionalol 10 mg tablet", "prov": "Fictional Corner Pharmacy"}]}

def read(*path):
    with open(os.path.join(ROOT, *path), encoding="utf-8") as f:
        return f.read()


def client():
    return TestClient(main.app, base_url="http://testserver")


class ShareLinks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = client().__enter__()
        db = dbm.connect()
        cls.uid = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        cls.pid = prof.create_profile(db, "Robin Example (fictional)", owner_id=cls.uid["robin"])
        cls.other = prof.create_profile(db, "Another (fictional)", owner_id=cls.uid["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (cls.uid["kim"], cls.pid))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (cls.uid["lee"], cls.pid))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'owner')", (cls.uid["ash"], cls.pid))
        with dbm.transaction(db):
            records.store(db, cls.pid, DATA, "dataset")
            records.store(db, cls.other, {**DATA, "events": [{"id": 1, "t": D(-5), "k": "visit", "title": "OTHERPROFILE visit", "sub": ""}],
                                          "labs": [], "costs": [], "upcoming": []}, "dataset")
        db.close()
        cls.c = {}
        for n in PW:
            c = client()
            r = c.post("/api/login", json={"username": n, "password": PW[n]}, headers=H)
            assert r.status_code == 200, r.text
            cls.c[n] = c
        r = cls.c["robin"].post(f"/api/profiles/{cls.pid}/journal", json={"kind": "symptom", "t": D(-15), "title": "Headache",
                                                                          "severity": 4, "note": "JOURNALNOTE after lunch"}, headers=H)
        assert r.status_code == 201, r.text
        r = cls.c["robin"].post(f"/api/profiles/{cls.pid}/journal", json={"kind": "side_effect", "t": D(-16), "title": "Nausea",
                                                                          "severity": 3, "med": "Examplecillin 250 mg"}, headers=H)
        assert r.status_code == 201, r.text

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        shutil.rmtree(TMP, True)

    def setUp(self):
        share_links.throttle._fails.clear()

    def make(self, who="robin", pid=None, status=200, **body):
        body = {"sections": ["visits"], **body}
        r = self.c[who].post(f"/api/profiles/{pid or self.pid}/links", json=body, headers=H)
        self.assertEqual(r.status_code, status, r.text)
        if status != 200:
            return r
        j = r.json()
        self.assertTrue(j["path"].startswith("share.html#"))
        return j["path"].split("#", 1)[1], j["link"]

    def view(self, token, status=200, c=None):
        r = (c or client()).post("/api/share/view", json={"token": token}, headers=H)
        self.assertEqual(r.status_code, status, r.text)
        return r

    def assert_bare(self, r):
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.text, "Not found.\n")
        self.assertTrue(r.headers["content-type"].startswith("text/plain"))
        self.assertNotIn("set-cookie", r.headers)

    def test_made_at_the_public_address_where_one_is_set(self):   # #1009
        """A link is given at the public address where one is set (the first https:// one of PUBLIC_ORIGINS by name,
        else the first), as "base" beside its path, which the page makes the link from in place of its own address;
        without one, none, and the page uses its own, as before. Read at each link: set or changed, it applies at once."""
        from unittest import mock
        with mock.patch.dict(os.environ, {"PUBLIC_ORIGINS": ""}):
            r = self.c["robin"].post(f"/api/profiles/{self.pid}/links", json={"sections": ["visits"]}, headers=H).json()
            self.assertNotIn("base", r)
        for origins, base in (("http://sanguity.lan:8730, https://health.example.test, https://zz-second.example.test", "https://health.example.test/"),
                              ("http://sanguity.lan:8730", "http://sanguity.lan:8730/")):
            with mock.patch.dict(os.environ, {"PUBLIC_ORIGINS": origins}):
                r = self.c["robin"].post(f"/api/profiles/{self.pid}/links", json={"sections": ["visits"]}, headers=H).json()
                self.assertEqual((r["base"], r["path"][:11]), (base, "share.html#"))
                self.view(r["path"].split("#", 1)[1])   # the same link, wherever it's opened

    # ---------------------------------------------------------------- what a link shows

    def test_only_the_chosen_sections_labs_and_range(self):
        token, link = self.make(label="Dr. Rivera", sections=["visits", "results", "procedures"], labs=["Hemoglobin"],
                                **{"from": D(-100), "to": D(0)})
        self.assertEqual(link["state"], "active")
        r = self.view(token)
        j = r.json()
        self.assertEqual(j["profile"], "Robin Example (fictional)")
        self.assertEqual([s["id"] for s in j["sections"]], ["visits", "results", "procedures"])
        for k in ("medications", "costs", "journal", "appointments", "events", "details", "labs", "imports", "documents"):
            self.assertNotIn(k, j)
        self.assertEqual([v["title"] for v in j["visits"]], ["Annual physical"])
        self.assertEqual(j["visits"][0]["sub"], "Dr. Fictional Example")
        self.assertEqual(sorted(p["title"] for p in j["procedures"]), ["Chest X-ray", "Fictional colonoscopy"])
        self.assertEqual([s["name"] for s in j["results"]], ["Hemoglobin"])
        pts = j["results"][0]["results"]
        self.assertEqual(len(pts), 1, "only the result inside the range")
        self.assertEqual((pts[0]["value"], pts[0]["range"], pts[0]["flag"]), ("11.1", "12–16", "Low"))
        for word in ("OLDVISIT", "HIDDENVISIT", "SodiumSECRET", "REPORTTEXT", "DETAILNOTE", "CLAIMTITLE", "Examplecillin",
                     "JOURNALNOTE", "Follow-up", "OTHERPROFILE", "Dr. Rivera", token):
            self.assertNotIn(word, r.text)

    def test_every_section(self):
        token, _ = self.make(sections=list(share_links.SECTION_IDS), **{"from": D(-100)})
        j = self.view(token).json()
        self.assertEqual(j["medications"], [{"name": "Examplecillin 250 mg", "instructions": "Take one daily", "since": D(-90),
                                             "until": None, "status": "active"}])
        self.assertEqual([a["title"] for a in j["appointments"]], ["Follow-up visit"], "only appointments from today on")
        self.assertEqual(j["journal"][0]["note"], "JOURNALNOTE after lunch")
        self.assertNotIn("by", j["journal"][0], "not who wrote it")
        self.assertNotIn("med", j["journal"][0], "a symptom with no medication: none sent")
        self.assertEqual((j["journal"][1]["kind"], j["journal"][1]["med"]), ("side_effect", "Examplecillin 250 mg"),
                         "a side effect's medication, which it's about (#518)")
        self.assertEqual(j["costs"][0]["owe"], 50.0)
        self.assertEqual(sorted(s["name"] for s in j["results"]), ["Hemoglobin", "SodiumSECRET"], "no labs picked: every lab")
        self.assertNotIn("REPORTTEXT", json.dumps(j))
        self.assertNotIn("DETAILNOTE", json.dumps(j))

    # #576: what each section sends, by field (a measure's block: by column), and the words of its line on the owner's
    # form (and the link's page) that name it. A field sent but not here, or named here but not in its line, fails
    SENT = {
        "visits": {"t": ["date"], "end": ["first and last days"], "kind": ["hospital stay"], "title": ["the title"],
                   "sub": ["the clinician and clinic", "where it was", "how many nights", "reason for admission", "admission type"]},
        "procedures": {"t": ["the date"], "kind": ["the kind"], "title": ["the title"]},
        "medications": {"name": ["the name"], "status": ["status"], "instructions": ["the instructions"],
                        "since": ["started"], "until": ["stopped"]},
        "results": {"label": ["the profile's name for it"], "name": ["its reports' name"], "panel": ["panel"], "unit": ["unit"],
                    "results": ["how many there are"], "results.t": ["date"], "results.value": ["value"],
                    "results.v": ["a trend line"], "results.range": ["reference range"], "results.lo": ["reference range"],
                    "results.hi": ["reference range"], "results.flag": ["flag"]},
        "appointments": {"t": ["the date and time"], "title": ["the title"], "who": ["with whom"], "where": ["where"]},
        "journal": {"t": ["date and time"], "kind": ["notes, symptoms, side effects and moods"], "title": ["its title"],
                    "note": ["what it says"], "severity": ["severity"], "mood": ["mood and energy"], "energy": ["mood and energy"],
                    "med": ["the medication an entry is linked to"], "where": ["where on the body"],
                    "group": ["the symptom group it was logged with"], "photos": ["the photos attached"],   # (photos: Photos in
                    "photos.id": ["the photos attached"], "photos.alt": ["with what each shows"]},       # the journal's line)
        "journal_photos": {"photos": ["the photos attached"]},   # (sent in the journal's entries, above)
        "costs": {"t": ["date"], "title": ["the service or medicine it was for"], "provider": ["the provider"], "billed": ["what was billed"],
                  "paid": ["what insurance paid"], "owe": ["your share"]},
        "about": {"birthDate": ["the birth date"], "sex": ["the sex labs base reference ranges on"], "heights": ["each height"],
                  "heights.date": ["with its date"], "heights.cm": ["each height"], "heights.text": ["each height"]},
        # the measures (measures.py): by each column's heading, its unit and a glucose range's numbers left out
        "home_bp": {"Date and time": ["the date and time"], "Systolic/diastolic": ["both pressures"], "Pulse": ["the pulse"],
                    "Device": ["the device"]},
        "glucose": {"Date": ["each day"], "Readings": ["each reading"], "Low–high": ["lowest and highest"], "Average": ["the average"],
                    "#–#": ["time in the standard published ranges"], "Below #": ["time in the standard published ranges"],
                    "Above #": ["time in the standard published ranges"]},
        "temperature": {"Date and time": ["the date and time"], "Temperature": ["the temperature"], "Device": ["the device"]},
        "oxygen": {"Date": ["each day"], "Readings": ["each reading sent one at a time or typed in"], "Average": ["average"], "Lowest": ["lowest"]},
        "respiratory": {"Date": ["each day"], "Rate": ["rate"]},
        "vo2max": {"Date": ["its date"], "VO2 max": ["each estimate"]},
        "immunizations": {"Date": ["the date"], "Vaccine": ["the vaccine"], "Dose": ["the dose"], "Where or by whom": ["where or by whom"]},
        "ecg": {"Date": ["the date"], "What the watch said": ["what the watch said"], "Details": ["average heart rate", "the device that made it"]},
        "bmi": {"Date": ["on its day"], "Measure": ["each weight", "the BMI", "each measurement"], "Value": ["each weight", "the BMI"],
                "Percentile": ["growth charts"], "From": ["where each weight came from"]},
        "targets": {"Measure": ["each target"], "Your target": ["each target"], "Set by": ["who set it"], "Set on": ["and when"],
                    "In this period": ["how many readings at home", "or days of steps counted by the phone", "were above or below it"]},
        "medlog": {"Date and time": ["the date and time"], "Medication": ["the medication"], "What happened": ["taken, taken late or skipped"],
                   "Note": ["its note"], "From": ["where it came from"]},
    }

    def test_each_sections_line_names_what_it_sends(self):   # #576
        lines = {s: w for s, _, w in share_links.SECTIONS}
        self.assertEqual(set(self.SENT), set(lines), "every section is here")
        for s, fields in self.SENT.items():
            line = lines[s] + (" " + lines["journal_photos"] if s == "journal" else "")
            for f, words in fields.items():
                for w in words:
                    self.assertIn(w, line, f"{s}: {f} is said in its line as “{w}”")
        # sent: a journal with a mood, and a symptom with where on the body and its group; a phone's measures
        pc = self.c["robin"]
        ids = [pc.post(f"/api/profiles/{self.pid}/journal", json=e, headers=H).json()["id"] for e in
               ({"kind": "mood", "t": D(-14), "mood": 6, "energy": 4},
                {"kind": "symptom", "t": D(-13), "title": "Rash", "severity": 2, "where": ["chest"], "group": "Fictional group"})]
        try:
            token, _ = self.make(sections=[s for s in share_links.SECTION_IDS if s not in measures.IDS], **{"from": D(-100)})
            j = self.view(token).json()
        finally:
            for i in ids:
                pc.delete(f"/api/profiles/{self.pid}/journal/{i}", headers=H)
        ptoken, _ = self.make(pid=self.phone_profile(), sections=list(measures.IDS))
        j.update({k: v for k, v in self.view(ptoken).json().items() if k in measures.IDS})

        def keys(rows, at=""):
            got = set()
            for r in rows if isinstance(rows, list) else [rows]:
                for k, v in r.items():
                    got.add(at + k)
                    if isinstance(v, list) and v and isinstance(v[0], dict):
                        got |= keys(v, at + k + ".")
            return got
        head = lambda c: re.sub(r"\b\d[\d.]*", "#", re.sub(r" \(.*\)$", "", c[0]))
        for s in self.SENT:
            if s == "journal_photos":   # (with the journal's entries)
                continue
            sent = {head(c) for c in j[s]["cols"]} if s in measures.IDS else keys(j[s])
            self.assertTrue(sent or s in ("journal",), s)
            self.assertLessEqual(sent, set(self.SENT[s]), f"{s} sends only what its line names")
        self.assertTrue({"mood", "energy", "where", "group", "severity", "note"} <= keys(j["journal"]), keys(j["journal"]))
        self.assertTrue(j["procedures"])
        for p in j["procedures"]:
            self.assertEqual(set(p), {"t", "kind", "title"}, "a procedure's page shows its date, kind and title: no sub, no end")

    # #581: what a column holds, not only its heading: each kind of thing a section's page can show (a cell of a measure's
    # table, a visit's details, a claim's service), as SOURCES' profile has it, and the words of its line that name it
    KINDS = [
        ("home_bp", "Fictional cuff (Health Connect)", ["the device and app that sent it"]),
        ("home_bp", "Typed in", ["(or “Typed in”)"]),
        ("temperature", "Fictional thermometer (Health Connect)", ["the device and app that sent it"]),
        ("oxygen", "Watch, the day's average", ["the watch's average"]),
        ("oxygen", "FICTIONAL-OXIMETER-X1 (Health Connect), 08:00", ["sent one at a time", "its time", "the device and app that sent it"]),
        ("oxygen", "Typed in, 09:00", ["typed in at home", "(or “Typed in”)"]),
        ("glucose", "CGM: 288 in 23.5 h", ["a continuous monitor's", "how many readings there were", "hours worn"]),
        ("glucose", "Meter: 1", ["a meter's", "how many readings there were"]),
        ("glucose", "Typed in, 07:45, before a meal", ["typed in at home", "its time and meal note"]),
        ("ecg", "72 bpm average · FICTIONAL-WATCH-DEVICE", ["average heart rate", "the device that made it"]),
        ("ecg", "FICTIONAL-WATCH-DEVICE", ["notification", "the device that made it"]),
        ("medlog", "FICTIONAL-APP, 2 FICTIONAL-UNITS", ["the app that sent it", "the dose taken"]),
        ("medlog", "Typed in", ["“Typed in”"]),
        ("visits", "transferred to Other Fictional Hospital", ["a transfer to or from another hospital"]),
        ("visits", "transferred from Fictional General", ["a transfer to or from another hospital"]),
        ("visits", "last inpatient result", ["the date of its last inpatient result"]),
        ("costs", "Fictionalol 10 mg tablet", ["the service or medicine it was for"]),   # a pharmacy claim's: the medicine
        ("targets", "1 of 2 readings above it", ["how many readings at home", "were above or below it"]),
        ("targets", "Steps a day", ["days of steps counted by the phone"]),   # a steps target (#298) and its days (#650)
        ("targets", "1 of 2 days below it", ["how many", "days of steps counted by the phone", "were above or below it"]),
        ("targets", "69% in it, 1 CGM day", ["for a CGM target", "the share of the CGM readings in it", "over how many days"]),   # (#719)
    ]
    # #719: and the lines a block leads with: every one of the targets' (a CGM target's sent the time in the standard
    # published range, which the line didn't name), each by what it holds and the words of the line that name that
    LEADS = [
        ("targets", "Each target as the person's care team gave it", ["each target typed in as the care team gave it"]),
        ("targets", "CGM time in your own range (70–140 mg/dL, your target): 69% of readings over 1 CGM day, 4.2% below it and 26% above it",
         ["for a CGM target", "the share of the CGM readings in it, below it and above it, over how many days"]),
        ("targets", "in the standard published range (70–180 mg/dL): 78%", ["their share in the standard published range"]),
    ]
    # and where each measure's readings came from, in its line
    SOURCE = {"home_bp": "the device and app that sent it", "temperature": "the device and app that sent it",
              "oxygen": "the device and app that sent it", "glucose": "sent by a phone app", "respiratory": "from the watch or phone",
              "vo2max": "the watch made", "immunizations": "the records say", "ecg": "the device that made it",
              "bmi": "where each weight came from", "targets": "typed in", "medlog": "where it came from"}

    def test_each_sections_line_names_what_its_columns_hold(self):   # #581
        lines = {s: w for s, _, w in share_links.SECTIONS}
        self.assertEqual(set(self.SOURCE), set(measures.IDS), "every measure")
        for s, w in self.SOURCE.items():
            self.assertIn(w, lines[s], f"{s}: its line says where each reading came from")
        path = os.path.join(TMP, "sources", "daily.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        crypto.write_file(path, json.dumps(SOURCES).encode())
        db = dbm.connect()
        pid = prof.create_profile(db, "Every source (fictional)", owner_id=self.uid["robin"], watch_file=path)
        with dbm.transaction(db):
            records.store(db, pid, SOURCES_DATA, "dataset")
        db.close()
        try:   # and a dose typed in, and the targets
            r = self.c["robin"].put(f"/api/profiles/{pid}/person", json={"targets": SOURCES_TARGETS}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            r = self.c["robin"].post(f"/api/profiles/{pid}/medications/doses", headers=H,
                                     json={"med": medications.med_key("Fictionalol 10 mg tablet"), "t": D(-4) + "T08:00", "status": "late"})
            self.assertEqual(r.status_code, 201, r.text)
            token, _ = self.make(pid=pid, sections=list(share_links.SECTION_IDS), **{"from": D(-100)})
            j = self.view(token).json()
        finally:
            db = dbm.connect()
            prof.delete_profile(db, pid)
            db.close()
        for s, shown, words in self.KINDS:
            got = ([v for r in j[s]["rows"] for v in r] if s in measures.IDS else
                   [r["sub"] for r in j[s]] if s == "visits" else [r["title"] for r in j[s]])
            self.assertTrue(any(shown == v or s == "visits" and shown in v for v in got), f"{s} shows “{shown}”: {got}")
            for w in words:
                self.assertIn(w, lines[s], f"{s}: “{shown}” is said in its line as “{w}”")
        for s in {s for s, _, _ in self.LEADS}:   # (#719) each line the block leads with is one of these
            for lead in j[s]["lead"]:
                self.assertTrue(any(shown in lead for t, shown, _ in self.LEADS if t == s), f"{s} leads with “{lead}”, which its line should name")
        for s, shown, words in self.LEADS:
            self.assertTrue(any(shown in lead for lead in j[s]["lead"]), f"{s} leads with “{shown}”: {j[s]['lead']}")
            for w in words:
                self.assertIn(w, lines[s], f"{s}: “{shown}” is said in its line as “{w}”")

    def test_no_issue_number_in_any_sections_words(self):   # #650: the Lab results line ended "(#302)"
        for s, label, line in share_links.SECTIONS:
            self.assertNotRegex(f"{label} {line}", r"#\d", f"{s}: its label and line, on the owner's form and the link's page")

    def test_labs_by_the_profiles_names_with_their_panels(self):   # #289: the clinician's page
        r = self.c["robin"].put(f"/api/profiles/{self.pid}/settings/labNicknames/name", json={"name": "SodiumSECRET", "to": "Sodium"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        try:
            token, link = self.make(sections=["results"])
            j = self.view(token).json()
            self.assertEqual([(s["label"], s["name"], s["panel"]) for s in j["results"]],
                             [("Hemoglobin", "Hemoglobin", "CBC"), ("Sodium", "SodiumSECRET", "BMP")], "by the owner's name, sorted by it")
            self.assertEqual([(p["value"], p["v"], p["flag"]) for p in j["results"][0]["results"]],
                             [("11.1", 11.1, "Low"), ("13.5", 13.5, None)], "newest first, each with its number for the sparkline")
            log = self.c["robin"].get(f"/api/profiles/{self.pid}/activity?action=link").json()
            self.assertEqual(len([e for e in log if e["detail"].get("change") == "view" and e["detail"].get("link") == link["id"]]), 1,
                             "the view is still logged")
        finally:
            self.c["robin"].put(f"/api/profiles/{self.pid}/settings/labNicknames/name", json={"name": "SodiumSECRET"}, headers=H)

    def test_no_end_date_and_no_start(self):
        token, _ = self.make(sections=["visits"])
        self.assertEqual(sorted(v["title"] for v in self.view(token).json()["visits"]), ["Annual physical", "Long-ago visit OLDVISIT"])

    def test_a_long_section_sends_its_latest_rows_and_show_all_the_rest(self):   # #498
        db = dbm.connect()
        try:
            pid = prof.create_profile(db, "Many claims (fictional)", owner_id=self.uid["robin"])
            with dbm.transaction(db):
                records.store(db, pid, {"events": [{"id": i, "t": D(-i), "k": "visit", "title": f"Visit {i}", "sub": ""} for i in range(1, 46)],
                                        "costs": [{"id": i, "d": D(-i), "billed": 10.0 * i, "title": f"Claim {i}", "prov": "Riverside Clinic"}
                                                  for i in range(1, 46)],
                                        "labs": [], "details": {}}, "dataset")
        finally:
            db.close()
        few, many = share_links.FIRST, share_links.MANY
        token, link = self.make(pid=pid, sections=["visits", "costs", "medications"], label="Long")
        r = self.view(token)
        j = r.json()
        self.assertEqual(j["cut"], {"visits": 45, "costs": 45}, "the long ones, with how many rows they have")
        self.assertEqual([c["title"] for c in j["costs"]], [f"Claim {i}" for i in range(1, few + 1)], "the latest rows only")
        self.assertEqual(len(j["visits"]), few)
        self.assertNotIn(f"Claim {few + 1}", r.text)
        all_ = client().post("/api/share/view", json={"token": token, "section": "costs"}, headers=H)
        self.assertEqual(all_.status_code, 200, all_.text)
        self.assertEqual(list(all_.json()), ["costs"], "that section and nothing else")
        self.assertEqual([c["title"] for c in all_.json()["costs"]], [f"Claim {i}" for i in range(1, 46)])
        for section in ("medications", "results", "journal", "nonsense", ""):   # not long, not in the link, or no section
            self.assert_bare(client().post("/api/share/view", json={"token": token, "section": section}, headers=H))
        self.assert_bare(client().post("/api/share/view", json={"token": "y" * 43, "section": "costs"}, headers=H))
        # each view logged: the opening, and the Show all as a view of its section (not another opening)
        log = self.c["robin"].get(f"/api/profiles/{pid}/activity?action=link").json()
        views = [e["detail"] for e in log if e["detail"].get("change") == "view" and e["detail"].get("link") == link["id"]]
        self.assertEqual(sorted(views, key=lambda d: "all" in d), [{"change": "view", "link": link["id"], "label": "Long"},
                                                                   {"change": "view", "link": link["id"], "label": "Long", "all": "Costs and claims"}])
        listed = {l["id"]: l for l in self.c["robin"].get(f"/api/profiles/{pid}/links").json()["links"]}
        self.assertEqual(listed[link["id"]]["views"], 1)
        token, _ = self.make(pid=pid, sections=["costs"], **{"from": D(-many)})   # 40 claims: all of them, nothing cut
        j = self.view(token).json()
        self.assertEqual((len(j["costs"]), j["cut"]), (many, {}))

    # ---------------------------------------------------------------- the token and what's stored

    def test_only_the_hash_is_stored_and_the_rest_is_encrypted(self):
        token, link = self.make(label="Dr. Rivera", sections=["results"], labs=["Hemoglobin"])
        db = dbm.connect()
        try:
            row = dict(db.execute("SELECT * FROM share_links WHERE id=?", (link["id"],)).fetchone())
            dump = json.dumps(row)
            self.assertNotIn(token, dump)
            self.assertNotIn("Rivera", dump)
            self.assertNotIn("Hemoglobin", dump)
            self.assertTrue(row["label"].startswith(crypto.FIELD) and row["scope"].startswith(crypto.FIELD))
            self.assertEqual(len(row["token_hash"]), 64)
            self.assertGreaterEqual(len(token), 43, "256 bits")
        finally:
            db.close()
        listed = self.c["robin"].get(f"/api/profiles/{self.pid}/links").json()
        self.assertNotIn(token, json.dumps(listed))
        self.assertIn("Hemoglobin", listed["labs"])
        self.assertEqual([s["id"] for s in listed["sections"]], [s for s in share_links.SECTION_IDS if s not in measures.IDS and s != "journal_photos"],
                         "every section, but a measure, or the journal's photos (#161), only when the profile has some (#225)")

    def test_no_cookie_is_set_and_none_is_read(self):
        token, _ = self.make()
        r = self.view(token)
        self.assertNotIn("set-cookie", r.headers)
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertEqual(r.headers["referrer-policy"], "no-referrer")
        # a signed-in browser opening it is still just a holder of the link: the view is logged as the link, not them
        self.view(token, c=self.c["kim"])
        log = self.c["robin"].get(f"/api/profiles/{self.pid}/activity?action=link").json()
        self.assertEqual(log[0]["actor"], "share link")
        self.assertIsNone(log[0]["actor_id"])
        self.assertFalse(log[0]["actor_removed"])

    # ---------------------------------------------------------------- refusals: always the same bare 404

    def test_wrong_malformed_and_missing_tokens(self):
        token, _ = self.make()
        for bad in [token[:-1] + ("A" if token[-1] != "A" else "B"), "x" * 43, "short", "x" * 200, "../../etc", ""]:
            self.assert_bare(self.view(bad, 404))
        share_links.throttle._fails.clear()
        for body in [b"not json", b"[1, 2]", b'{"token": 5}', b"{}", b'{"token": "' + b"a" * 2000 + b'"}']:
            r = client().post("/api/share/view", content=body, headers={**H, "Content-Type": "application/json"})
            self.assert_bare(r)
        self.assertEqual(client().get("/api/share/view").status_code, 405)
        self.assertEqual(client().post("/api/share/view", json={"token": token}).status_code, 403, "the cross-site check applies")

    def test_revoked_expired_and_maker_no_longer_owner(self):
        token, link = self.make(who="ash")
        self.view(token)
        r = self.c["robin"].delete(f"/api/profiles/{self.pid}/links/{link['id']}", headers=H)   # any owner can revoke
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["link"]["state"], "revoked")
        self.assertEqual(r.json()["link"]["revoked_by"], "robin")
        self.assert_bare(self.view(token, 404))
        self.assertEqual(self.c["robin"].delete(f"/api/profiles/{self.pid}/links/{link['id']}", headers=H).status_code, 404)

        token, link = self.make()
        db = dbm.connect()
        try:
            db.execute("UPDATE share_links SET expires=? WHERE id=?", (dbm.now() - 1, link["id"]))
            self.assert_bare(self.view(token, 404))
            states = {l["id"]: l["state"] for l in self.c["robin"].get(f"/api/profiles/{self.pid}/links").json()["links"]}
            self.assertEqual(states[link["id"]], "expired")

            # its maker no longer an owner, however that came about: stopped for good, as it's looked at (#590: it
            # opened again once they were an owner again)
            token, link = self.make(who="ash")
            db.execute("UPDATE memberships SET role='editor' WHERE user_id=? AND profile_id=?", (self.uid["ash"], self.pid))
            self.assert_bare(self.view(token, 404))
            db.execute("UPDATE memberships SET role='owner' WHERE user_id=? AND profile_id=?", (self.uid["ash"], self.pid))
            self.assert_bare(self.view(token, 404))
            token, link = self.make(who="ash")
            db.execute("UPDATE users SET disabled=1 WHERE id=?", (self.uid["ash"],))
            self.assert_bare(self.view(token, 404))
            db.execute("UPDATE users SET disabled=0 WHERE id=?", (self.uid["ash"],))
            self.assert_bare(self.view(token, 404))
            states = {l["id"]: l["state"] for l in self.c["robin"].get(f"/api/profiles/{self.pid}/links").json()["links"]}
            self.assertEqual(states[link["id"]], "maker_not_owner")
        finally:
            db.close()

    def test_a_link_stopped_with_its_maker_stays_stopped(self):   # #590
        states = lambda: {l["id"]: l["state"] for l in self.c["robin"].get(f"/api/profiles/{self.pid}/links").json()["links"]}
        put = lambda role: self.assertEqual(self.c["robin"].put(f"/api/profiles/{self.pid}/members", json={"username": "ash", "role": role},
                                                                headers=H).status_code, 200)
        # made a viewer, then an owner again
        token, link = self.make(who="ash")
        self.view(token)
        put("viewer")
        self.assertEqual(states()[link["id"]], "maker_not_owner")
        put("owner")
        self.assert_bare(self.view(token, 404))
        self.assertEqual(states()[link["id"]], "maker_not_owner")
        self.assertEqual(self.c["robin"].delete(f"/api/profiles/{self.pid}/links/{link['id']}", headers=H).status_code, 404)   # nothing left to revoke
        # taken off the profile, then given it back as an owner
        token, link = self.make(who="ash")
        r = self.c["robin"].delete(f"/api/profiles/{self.pid}/members/{self.uid['ash']}", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        put("owner")
        self.assert_bare(self.view(token, 404))
        self.assertEqual(states()[link["id"]], "maker_not_owner")
        # one stopped before this fix, never looked at since: it stays stopped when its maker is an owner again
        token, link = self.make(who="ash")
        db = dbm.connect()
        try:
            db.execute("UPDATE memberships SET role='editor' WHERE user_id=? AND profile_id=?", (self.uid["ash"], self.pid))
            put("owner")
            self.assert_bare(self.view(token, 404))
        finally:
            db.close()
        # an owner's other links, and other owners' links, are untouched
        token, link = self.make()
        put("editor")
        put("owner")
        self.view(token)

    def test_throttled(self):
        for _ in range(share_links.ShareThrottle.FREE["bad"]):
            self.view("y" * 43, 404)
        r = self.view("y" * 43, 429)
        self.assertIn("retry-after", r.headers)
        token, _ = self.make()
        self.view(token)   # a working link still opens from that address (#203: it may be a shared proxy's)
        share_links.throttle._fails.clear()
        for _ in range(share_links.ShareThrottle.FREE["link"]):
            self.view(token)
        self.view(token, 429)

    # ---------------------------------------------------------------- logging

    def test_every_view_is_logged_without_the_token(self):
        token, link = self.make(label="For Mum")
        for _ in range(3):
            self.view(token)
        log = self.c["robin"].get(f"/api/profiles/{self.pid}/activity?action=link").json()
        views = [e for e in log if e["detail"].get("change") == "view" and e["detail"].get("link") == link["id"]]
        self.assertEqual(len(views), 3)
        self.assertEqual(views[0]["detail"]["label"], "For Mum")
        self.assertTrue(views[0]["ip"])
        made = [e for e in log if e["detail"].get("change") == "create" and e["detail"].get("link") == link["id"]]
        self.assertEqual(made[0]["actor"], "robin")
        self.assertNotIn(token, json.dumps(log))
        listed = {l["id"]: l for l in self.c["robin"].get(f"/api/profiles/{self.pid}/links").json()["links"]}
        self.assertEqual(listed[link["id"]]["views"], 3)
        self.assertIsNotNone(listed[link["id"]]["last_viewed"])

    def test_the_security_log_never_holds_a_token(self):
        seen = []
        h = logging.Handler()
        h.emit = lambda rec: seen.append(rec.getMessage())
        lg = logging.getLogger("sanguity.security")
        lg.addHandler(h)
        old = lg.level
        lg.setLevel(logging.INFO)
        try:
            token, _ = self.make()
            self.view(token)
            self.view("z" * 43, 404)
        finally:
            lg.removeHandler(h)
            lg.setLevel(old)
        self.assertTrue(any("share.refused" in m for m in seen))
        self.assertFalse(any(token in m or "z" * 43 in m for m in seen))

    # ---------------------------------------------------------------- who manages links, and what they may ask for

    def test_only_owners_manage_links(self):
        token, link = self.make()
        for who, status in (("kim", 403), ("lee", 403), ("zed", 404)):
            self.assertEqual(self.c[who].get(f"/api/profiles/{self.pid}/links").status_code, status, who)
            self.make(who=who, status=status)
            self.assertEqual(self.c[who].delete(f"/api/profiles/{self.pid}/links/{link['id']}", headers=H).status_code, status, who)
        self.assertEqual(client().get(f"/api/profiles/{self.pid}/links").status_code, 401)
        # a link of one profile can't be revoked through another
        self.assertEqual(self.c["robin"].delete(f"/api/profiles/{self.other}/links/{link['id']}", headers=H).status_code, 404)
        self.view(token)

    def test_checks_what_it_is_asked_for(self):
        for body in ({"sections": []}, {"sections": ["everything"]}, {"sections": ["visits"], "labs": ["Hemoglobin"]},
                     {"from": D(0), "to": D(-5)}, {"from": "2026-02-30"}, {"from": "yesterday"}, {"hours": 0},
                     {"hours": share_links.MAX_HOURS + 1}, {"label": "x" * 81}):
            self.make(status=400, **body)
        self.make(status=422, hours="a week")
        token, link = self.make(hours=1)
        self.assertLessEqual(link["expires"] - link["created"], 3600)

    def test_at_most_so_many_working_links(self):
        db = dbm.connect()
        try:
            db.execute("UPDATE share_links SET revoked=? WHERE profile_id=?", (dbm.now(), self.other))
        finally:
            db.close()
        for _ in range(share_links.MAX_ACTIVE):
            self.make(pid=self.other)
        r = self.make(pid=self.other, status=400)
        self.assertIn("Revoke one first", r.json()["detail"])
        db = dbm.connect()
        try:
            db.execute("UPDATE share_links SET revoked=? WHERE profile_id=?", (dbm.now(), self.other))
        finally:
            db.close()
        self.make(pid=self.other)

    def test_ended_links_are_pruned_after_a_while(self):
        token, link = self.make()
        db = dbm.connect()
        try:
            db.execute("UPDATE share_links SET revoked=? WHERE id=?", (dbm.now() - (share_links.KEEP_ENDED_DAYS + 1) * 86400, link["id"]))
            self.make()   # (its row id may be the pruned one's again)
            from server import security as sec
            self.assertIsNone(db.execute("SELECT 1 FROM share_links WHERE token_hash=?", (sec.token_hash(token),)).fetchone())
        finally:
            db.close()

    def test_deleting_the_profile_deletes_its_links(self):
        db = dbm.connect()
        try:
            pid = prof.create_profile(db, "Short-lived (fictional)", owner_id=self.uid["robin"])
        finally:
            db.close()
        token, _ = self.make(pid=pid)
        r = self.c["robin"].request("DELETE", f"/api/profiles/{pid}", json={"confirm_name": "Short-lived (fictional)"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assert_bare(self.view(token, 404))
        db = dbm.connect()
        try:
            self.assertIsNone(db.execute("SELECT 1 FROM share_links WHERE profile_id=?", (pid,)).fetchone())
        finally:
            db.close()

    # ---------------------------------------------------------------- the page and the web server

    # ---------------------------------------------------------------- the phone's and watch's measures (#225)

    @classmethod
    def phone_profile(cls):
        if getattr(cls, "ppid", None):
            return cls.ppid
        path = os.path.join(TMP, "phone", "daily.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        crypto.write_file(path, json.dumps(PHONE).encode())
        db = dbm.connect()
        cls.ppid = prof.create_profile(db, "Sam Example (fictional)", owner_id=cls.uid["robin"], watch_file=path)
        with dbm.transaction(db):
            records.store(db, cls.ppid, {"events": [{"id": 1, "t": D(-200), "k": "immunization", "title": "Hepatitis B vaccine FICTIONAL"}],
                                         "labs": [], "details": {"1": {"dose": "1 mL"}}}, "dataset")
        db.close()
        return cls.ppid

    def test_measures_offered_when_present_and_only_when_ticked(self):
        pid = self.phone_profile()
        offered = [s["id"] for s in self.c["robin"].get(f"/api/profiles/{pid}/links").json()["sections"]]
        self.assertEqual([s for s in offered if s in measures.IDS], ["home_bp", "glucose", "temperature", "oxygen", "immunizations", "ecg"])
        self.assertNotIn("cycle", share_links.SECTION_IDS, "cycle tracking is never offered for a link (#183)")
        token, _ = self.make(pid=pid, sections=["visits", "results", "medications", "about"])   # the form's usual ones: none ticked
        text = self.view(token).text
        for word in ("Fictional cuff", "132/84", "Sinus", "Hepatitis", "glucose", "SpO2", "home_bp"):
            self.assertNotIn(word, text)

    def test_measures_in_a_link_and_its_range(self):
        pid = self.phone_profile()
        token, link = self.make(pid=pid, sections=["home_bp", "glucose", "temperature", "oxygen", "immunizations", "ecg"],
                                **{"from": D(-20), "to": D(-3)})
        self.assertEqual(link["sections"], ["home_bp", "glucose", "temperature", "oxygen", "immunizations", "ecg"])
        j = self.view(token).json()
        self.assertEqual([s["id"] for s in j["sections"]], link["sections"])
        self.assertTrue(all(s["what"] for s in j["sections"]))
        self.assertEqual(j["home_bp"]["rows"], [[D(-9) + "T08:00", "132/84", "68", "Fictional cuff (Health Connect)"]],
                         "only the readings in the range: not the one 40 days ago, nor the one 2 days ago")
        self.assertEqual(j["home_bp"]["lead"], ["1 reading · average 132/84 mmHg · highest systolic 132, highest diastolic 84"])
        self.assertEqual([r[0] for r in j["glucose"]["rows"]], [D(-4), D(-5)])
        self.assertIn("standard published ranges", j["glucose"]["note"])
        self.assertEqual(j["temperature"]["rows"][0][1], "37.4", "°C: the units of the owner who made it")
        self.assertEqual(j["oxygen"]["rows"], [[D(-10), "97.2", ""]])
        self.assertEqual(j["immunizations"]["rows"], [], "the vaccine was given before the range")
        self.assertEqual(j["ecg"]["rows"], [[D(-6), "ECG: Sinus Rhythm", "72 bpm average"]])
        for k in ("metrics", "bp_readings", "ecg_waves", "samples", "tz", "last_payload"):
            self.assertNotIn(f'"{k}"', json.dumps(j))
        token, _ = self.make(pid=pid, sections=["immunizations"])
        self.assertEqual(self.view(token).json()["immunizations"]["rows"], [[D(-200), "Hepatitis B vaccine FICTIONAL", "1 mL", ""]])

    def test_targets_only_when_ticked(self):   # #234
        pid = self.phone_profile()
        offered = lambda: [s["id"] for s in self.c["robin"].get(f"/api/profiles/{pid}/links").json()["sections"]]
        self.assertNotIn("targets", offered(), "none entered: not offered")
        put = lambda t: self.c["robin"].put(f"/api/profiles/{pid}/person", json={"targets": t}, headers=H)
        self.assertEqual(put({"bp": {"sysHigh": 130, "diaHigh": 80, "by": "Dr. Fictional Example"}}).status_code, 200)
        try:
            self.assertIn("targets", offered())
            token, _ = self.make(pid=pid, sections=["home_bp"], **{"from": D(-20), "to": D(-3)})
            text = self.view(token).text
            self.assertNotIn("Dr. Fictional Example", text, "not ticked: not shown")
            self.assertNotIn("targets", self.view(token).json())
            token, _ = self.make(pid=pid, sections=["home_bp", "targets"], **{"from": D(-20), "to": D(-3)})
            j = self.view(token).json()
            self.assertEqual(j["targets"]["rows"], [["Blood pressure", "up to 130/80 mmHg", "Dr. Fictional Example", "", "1 of 1 reading above it"]])
            self.assertIn("Sanguity doesn't suggest targets", j["targets"]["lead"][0])
        finally:
            put({"bp": None})

    def test_typed_glucose_and_oxygen_in_a_link(self):
        """#232: glucose and blood oxygen typed in at home are in a link's sections, in its dates and in the units of the
        owner who made it: glucose each with its time and meal note (one the phone sent too, once), oxygen one by one."""
        path = os.path.join(TMP, "typed", "daily.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        crypto.write_file(path, json.dumps(TYPED).encode())
        db = dbm.connect()
        pid = prof.create_profile(db, "Alex Example (fictional)", owner_id=self.uid["robin"], watch_file=path)
        db.close()
        offered = [s["id"] for s in self.c["robin"].get(f"/api/profiles/{pid}/links").json()["sections"]]
        self.assertEqual([s for s in offered if s in measures.IDS], ["glucose", "oxygen"])
        token, _ = self.make(pid=pid, sections=["glucose", "oxygen"], **{"from": D(-20), "to": D(-3)})
        j = self.view(token).json()
        g, o = j["glucose"], j["oxygen"]
        self.assertEqual(g["rows"], [[D(-5), "Typed in, 07:45, before a meal", "104", "104"], [D(-6), "Meter: 1", "99", "99"]],
                         "in the range only; the reading the phone sent too, once")
        self.assertEqual(g["lead"], ["2 meter readings (1 typed in) · average 102 mg/dL · lowest 99, highest 104"])
        self.assertIn("typed in", g["note"])
        self.assertEqual(o["rows"], [[D(-4), "Typed in, 08:05", "97", ""], [D(-8), "Watch, the day's average", "96.5", "93"]])
        self.assertEqual(o["lead"], ["1 day · average 96.5% · lowest 93%", "1 reading typed in · average 97%"])
        self.assertIn("typed in at home", o["note"])
        self.assertNotIn("glucose_readings", json.dumps(j))
        c = self.c["robin"]
        try:   # the owner's units (#41) at the time of viewing
            c.put("/api/settings/user/units", json={"value": {"lab": "si"}}, headers=H)
            g = self.view(token).json()["glucose"]
            self.assertEqual(g["cols"][2][0], "Low–high (mmol/L)")
            self.assertEqual(g["rows"][0][1:], ["Typed in, 07:45, before a meal", "5.8", "5.8"])
        finally:
            c.put("/api/settings/user/units", json={"value": {}}, headers=H)

    def test_page_and_nginx_routes(self):
        html = read("www", "share.html")
        self.assertIn('<script src="js/share.js"></script>', html)
        self.assertIn('<script src="js/dates.js"></script>', html)
        self.assertNotRegex(html, r"<script>(?!</script>)", "no inline script (CSP)")
        js = read("www", "js", "share.js")
        self.assertIn('credentials: "omit"', js)
        self.assertNotIn("innerHTML", js)
        self.assertIn("location.hash", js)
        conf = read("nginx.conf")
        self.assertIn("location = /share.html { }", conf)
        block = re.search(r"location = /api/share/view \{(.*?)\n    \}", conf, re.S).group(1)
        self.assertIn('proxy_set_header Cookie "";', block)
        self.assertIn("limit_except POST", block)
        self.assertIn("client_max_body_size 1k;", block)
        self.assertNotIn("add_header Content-Security-Policy", block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
