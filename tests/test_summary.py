#!/usr/bin/env python3
"""The visit summary PDF (server/summary.py, server/pdfdoc.py, server/routes_summary.py, issue #48): the PDF opens
and its text (read back with pypdf) holds every section, out-of-range results are judged against their own
report's range (two sources of one test with different ranges), the questions draft is kept (encrypted) and saved
when a PDF is made, who may make one (viewers too; not someone the profile isn't shared with), each one is logged,
the download's headers, nothing is written to disk, and it stays within two pages. Fictional data only, dated from
today so the "upcoming" appointments stay upcoming.

  python3 tests/test_summary.py
"""
import datetime, io, json, os, shutil, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-summary-test-")
BUILD = os.path.join(TMP, "build")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=BUILD,
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_CORRECTIONS=os.path.join(TMP, "config", "corrections.json"))
for d in ("records", "keys", "build", "config", "profiles"):
    os.makedirs(os.path.join(TMP, d), exist_ok=True)
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402
from pypdf import PdfReader   # noqa: E402

from server import accounts, crypto, db as dbm, main, measures, pdfdoc, profiles as prof, records, settings, summary   # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites", "lee": "maple tunnel brisk saddle",
      "zed": "a stranger to every profile"}
TODAY = datetime.date.today()


def day(n, hour=9):
    return (TODAY + datetime.timedelta(days=n)).isoformat() + f"T{hour:02d}:00:00"


def d(n):
    return (TODAY + datetime.timedelta(days=n)).isoformat()


SINCE = d(-30)   # the last visit
# the first import: the clinic's portal export
CLINIC = {
    "built": day(0), "source": "fictional",
    "events": [
        {"id": 1, "t": day(-120), "k": "visit", "title": "Office visit", "sub": "Dr. Rowan Vale · Maple Street Clinic"},
        {"id": 2, "t": day(-30), "k": "visit", "title": "Follow-up visit", "sub": "Dr. Rowan Vale · Maple Street Clinic"},
        {"id": 3, "t": day(-200), "k": "med", "title": "Lisinopril 10 mg tablet", "sub": "Take 1 tablet every morning · Active"},
        {"id": 4, "t": day(-90), "k": "med", "title": "Amoxicillin 500 mg capsule", "sub": "Completed"},
        {"id": 5, "t": day(-80), "k": "med", "title": "Cetirizine 10 mg tablet", "end": day(-40)},
        {"id": 6, "t": day(-15), "k": "med", "title": "Vitamin D3 1000 unit capsule"},
    ],
    "details": {"3": {"type": "med", "instructions": "Take 1 tablet every morning", "status": "Active", "src": "Maple Street Clinic"},
                "4": {"type": "med", "instructions": "Take 1 capsule 3 times a day", "status": "Completed", "src": "Maple Street Clinic"},
                "5": {"type": "med", "instructions": "Take 1 tablet daily"},
                "6": {"type": "med", "instructions": "Take 1 capsule daily"}},
    "labs": [
        {"name": "Hemoglobin", "unit": "g/dL", "panel": "CBC", "n": 3, "txt": [],
         "pts": [[day(-60), 12.1, 12.0, 15.5, "", "12.1"], [day(-25), 11.4, 12.0, 15.5, "L", "11.4"], [day(-5), 11.8, 12.0, 15.5, "L", "11.8"]]},
        # in range by this lab's report (20-200)? No: 210 is high by its own range, with no flag given
        {"name": "Ferritin", "unit": "ng/mL", "panel": "Iron", "n": 1, "txt": [], "pts": [[day(-20), 210, 20, 200, "", "210"]]},
        {"name": "Glucose", "unit": "mg/dL", "panel": "BMP", "n": 1, "txt": [], "pts": [[day(-5), 105, 70, 99, "H", "105"]]},
        {"name": "Sodium, Blood", "unit": "mmol/L", "panel": "BMP", "n": 1, "txt": [], "pts": [[day(-100), 133, 135, 145, "L", "133"]]},
    ],
    "costs": [],
    "upcoming": [{"t": d(60), "title": "Annual physical", "who": "Dr. Rowan Vale", "where": "Maple Street Clinic"},
                 {"t": d(10), "title": "Follow-up visit", "who": "Dr. Rowan Vale", "where": "Maple Street Clinic"},
                 {"t": d(-3), "title": "A past appointment", "who": "", "where": ""}],
}
# the second import: an outside lab whose ferritin range is wider, so 250 is in range on its own report
OUTSIDE_LAB = {"events": [], "details": {}, "costs": [],
               "labs": [{"name": "Ferritin", "unit": "ng/mL", "panel": "Iron", "n": 1, "txt": [],
                         "pts": [[day(-10), 250, 30, 400, "", "250"]]}]}
QUESTIONS = "Should I keep taking lisinopril in the morning?\n- Is the ferritin (high) result worth rechecking?\n\n"
ALL = [s for s, _ in summary.SECTIONS]


def utc(n, hour):
    return f"{d(n)}T{hour:02d}:00:00+00:00"


# #225: a fictional phone's readings (the watch summary's shape, watch/receiver.py), the last visit 30 days ago
PHONE = {"updated": utc(0, 6), "tz": "UTC", "last_payload": None, "exercise": [], "metrics": [
    {"key": "spo2_avg", "name": "SpO2, daily average", "unit": "%", "group": "Oxygen & breathing", "pts": [[d(-40), 95.0], [d(-10), 97.2], [d(-5), 96.4]]},
    {"key": "spo2_min", "name": "SpO2, daily low", "unit": "%", "group": "Oxygen & breathing", "pts": [[d(-10), 91.0], [d(-5), 93.0]]},
    {"key": "resp", "name": "Respiratory rate", "unit": "breaths/min", "group": "Oxygen & breathing", "pts": [[d(-10), 14.2], [d(-5), 15.0]]},
    {"key": "vo2max", "name": "Cardio fitness (VO2 max)", "unit": "mL/(kg·min)", "group": "Heart", "pts": [[d(-20), 41.3]]},
    {"key": "weight", "name": "Weight", "unit": "lb", "group": "Body", "pts": [[d(-12), 165.0], [d(-3), 164.2]]},
    {"key": "glucose_meter", "name": "Blood glucose, meter readings", "unit": "mg/dL", "group": "Glucose",
     "pts": [[d(-4) + "T07:12", 104.0], [d(-4) + "T19:40", 131.0]]},
    *({"key": "glucose_" + k, "name": k, "unit": "", "group": "Glucose", "pts": [[d(-6), a], [d(-5), b]]} for k, a, b in (
        ("avg", 120.0, 131.0), ("min", 70.0, 62.0), ("max", 190.0, 212.0), ("n", 96, 288), ("hours", 8.0, 24.0),
        ("lt54", 2.0, 0.03), ("lt70", 8.0, 4.0), ("in", 70.0, 78.0), ("gt180", 22.0, 18.0)))],
    "bp_readings": [[utc(-40, 8), 150, 95, 70, "Fictional cuff", "Health Connect"], [utc(-9, 8), 132, 84, 68, "Fictional cuff", "Health Connect"],
                    [utc(-2, 20), 126, 80, None, None, "Apple Health"]],
    "temp_readings": [[utc(-8, 7), 37.4, "Fictional thermometer", "Health Connect"], [utc(-7, 7), 36.8, None, "Apple Health"]],
    "ecg": [{"key": "ecg:0123456789abcdef", "type": "ecg", "start": utc(-6, 10), "classification": "Sinus Rhythm", "avg_hr": 72,
             "device": "Fictional Watch"},
            {"key": "rhythm:fedcba9876543210", "type": "irregular_rhythm", "start": utc(-50, 3), "device": "Fictional Watch"}]}
MEASURED = {"built": day(0), "source": "fictional", "labs": [], "costs": [], "upcoming": [],
            "events": [{"id": 1, "t": day(-30), "k": "visit", "title": "Follow-up visit", "sub": ""},
                       {"id": 2, "t": day(-400), "k": "immunization", "title": "Tetanus vaccine (fictional)"},
                       {"id": 3, "t": day(-60), "k": "immunization", "title": "Influenza vaccine (fictional)"}],
            "details": {"3": {"dose": "0.5 mL", "where": "Maple Street Clinic"}}}


def client():
    return TestClient(main.app, base_url="http://testserver")


def text_of(pdf):
    r = PdfReader(io.BytesIO(pdf))
    return r, "\n".join(p.extract_text() for p in r.pages)


def files_under(root):
    out = {}
    for base, _, names in os.walk(root):
        for n in names:
            p = os.path.join(base, n)
            out[p] = os.path.getsize(p)
    return out


class Summary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dbm.init()
        cls.ctx = client().__enter__()
        db = dbm.connect()
        cls.uid = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        cls.pid = prof.create_profile(db, "Robin (fictional)", owner_id=cls.uid["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (cls.uid["kim"], cls.pid))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (cls.uid["lee"], cls.pid))
        os.makedirs(os.path.join(TMP, "dataset"), exist_ok=True)
        with open(os.path.join(TMP, "dataset", "data.json"), "w", encoding="utf-8") as f:
            json.dump(CLINIC, f)
        prof.import_dataset(db, cls.pid, os.path.join(TMP, "dataset"))
        with dbm.transaction(db):
            records.store(db, cls.pid, OUTSIDE_LAB, "upload", fmt="fhir")
        db.close()
        cls.c = {}
        for n in PW:
            c = client()
            r = c.post("/api/login", json={"username": n, "password": PW[n]}, headers=H)
            assert r.status_code == 200, r.text
            cls.c[n] = c

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        shutil.rmtree(TMP, True)

    def opts(self, **kw):
        return {"appointment": 0, "since": None, "sections": ALL, "labs": [], "questions": QUESTIONS, **kw}

    def pdf(self, who="robin", status=200, **kw):
        r = self.c[who].post(f"/api/profiles/{self.pid}/summary/pdf", json=self.opts(**kw), headers=H)
        self.assertEqual(r.status_code, status, r.text[:300])
        return r

    def preview(self, who="robin", **kw):
        r = self.c[who].post(f"/api/profiles/{self.pid}/summary/preview", json=self.opts(**kw), headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_options(self):
        r = self.c["robin"].get(f"/api/profiles/{self.pid}/summary")
        self.assertEqual(r.status_code, 200, r.text)
        o = r.json()
        self.assertEqual([u["date"] for u in o["upcoming"]], [d(10), d(60)], "upcoming only, soonest first")
        self.assertEqual(o["visits"][0]["date"], SINCE, "the last visit is the default since point")
        self.assertIn("Hemoglobin", o["keyLabs"])
        self.assertIn({"name": "Ferritin", "label": "Ferritin"}, o["labs"])
        self.assertEqual([s["id"] for s in o["sections"]][:5], ALL)
        self.assertIn("isn't medical advice", o["notice"])

    def test_pdf_holds_every_section(self):
        r = self.pdf()
        self.assertEqual(r.headers["content-type"], "application/pdf")
        self.assertTrue(r.headers["content-disposition"].startswith("attachment; filename=\"visit-summary-Robin-fictional-"))
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertTrue(r.content.startswith(b"%PDF-1.4"))
        reader, text = text_of(r.content)
        self.assertIn(len(reader.pages), (1, 2))
        for want in ("Visit summary", "Robin (fictional)", "For: Follow-up visit on", "Dr. Rowan Vale",
                     "Out of range since", "Ferritin", "Glucose", "High", "Low",
                     "Trends since", "Hemoglobin", "Current medications", "Lisinopril 10 mg tablet",
                     "Take 1 tablet every morning", "Vitamin D3", "Upcoming appointments", "Annual physical", "(this visit)",
                     "My questions", "Should I keep taking lisinopril in the morning?", "Is the ferritin (high) result worth rechecking?",
                     "isn't medical advice", "Page 1 of"):
            self.assertIn(want, text)
        for gone in ("Amoxicillin", "Cetirizine", "A past appointment"):
            self.assertNotIn(gone, text, "stopped medications and past appointments are left out")
        self.assertIn("Sodium (mmol/L) 133", text, "a key lab with no result since: its last one, as a trend")
        self.assertIn("no new result", text)
        self.assertIn(f"Since: {summary.fmt_date(SINCE)} (Follow-up visit)", text)

    def test_dates_are_the_persons_own(self):
        """#42: "today" is the person's own day (here UTC+14, which is tomorrow for most of the world) and dates are
        written in their format; the questions draft and sections are unchanged."""
        import datetime, zoneinfo
        c = self.c["lee"]
        for k, v in (("dates.timeZone", "Pacific/Kiritimati"), ("dates.format", "ymd")):
            r = c.put(f"/api/settings/user/{k}", json={"value": v}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
        try:
            try:
                day = datetime.datetime.now(zoneinfo.ZoneInfo("Pacific/Kiritimati")).date().isoformat()
            except zoneinfo.ZoneInfoNotFoundError:   # no time zone database here (Windows): the server's day, as the app does
                day = datetime.date.today().isoformat()
            s = self.preview("lee")
            self.assertEqual(s["prepared"], day)
            self.assertEqual(s["date_format"], "ymd")
            _, text = text_of(self.pdf("lee").content)
            self.assertIn(f"Prepared {day}", text)
            self.assertIn(f"Since: {SINCE}", text, "written as YYYY-MM-DD")
            self.assertNotIn(summary.fmt_date(SINCE), text.replace(f"Since: {SINCE}", ""), "no other format left")
            c.put("/api/settings/user/dates.format", json={"value": "dmy"}, headers=H)
            _, text = text_of(self.pdf("lee").content)
            x = datetime.date.fromisoformat(SINCE)
            self.assertIn(f"Since: {x.day} {x.strftime('%b')} {x.year}", text)
        finally:
            for k in ("dates.timeZone", "dates.format"):
                c.put(f"/api/settings/user/{k}", json={"value": ""}, headers=H)

    def test_ranges_are_each_reports_own(self):
        s = self.preview()
        rows = {r["name"]: r for r in s["out_of_range"]}
        self.assertEqual(set(rows), {"Ferritin", "Glucose", "Hemoglobin"})
        f = rows["Ferritin"]   # 210 against 20-200 is high; 250 against its own 30-400 isn't, though it's higher
        self.assertEqual((f["value"], f["range"], f["flag"], f["out"], f["of"], f["date"]), ("210", "20–200", "High", 1, 2, d(-20)))
        self.assertEqual((rows["Glucose"]["flag"], rows["Glucose"]["range"]), ("High", "70–99"))
        self.assertEqual((rows["Hemoglobin"]["out"], rows["Hemoglobin"]["of"]), (2, 2))
        self.assertEqual(s["out_of_range"][0]["name"], "Hemoglobin", "the key labs first")
        hb = next(t for t in s["trends"] if t["name"] == "Hemoglobin")
        self.assertEqual((hb["then"], hb["now"]["value"], hb["now"]["flag"], hb["change"], hb["since"]),
                         ({"value": "12.1", "date": d(-60)}, "11.8", "Low", "-0.3", 2))
        self.assertEqual([p[2] for p in hb["spark"]], [False, True, True])
        # both sources' ferritin are one trend, each result with its own range
        s = self.preview(labs=["Ferritin", "Not a lab"])
        fe = next(t for t in s["trends"] if t["name"] == "Ferritin")
        self.assertEqual((fe["then"], fe["now"]["value"], fe["now"]["range"], fe["now"]["flag"], fe["since"]), (None, "250", "30–400", None, 2))
        self.assertNotIn("Not a lab", [t["name"] for t in s["trends"]])
        reader, text = text_of(self.pdf(labs=["Ferritin"]).content)
        self.assertIn("20–200", text)
        self.assertIn("30–400", text)
        # an earlier since point: the sodium from before the last visit is now included
        s = self.preview(since=d(-150))
        self.assertIn("Sodium, Blood", {r["name"] for r in s["out_of_range"]})
        self.assertEqual(next(r for r in s["out_of_range"] if r["name"] == "Sodium, Blood")["label"], "Sodium",
                         "the profile's lab nicknames are used")

    def test_now_says_high_or_low(self):
        # a trend's out-of-range Now value says High or Low in words, not by red alone, so a black-and-white print
        # shows it (#213); the fonts have no ▲ ▼, so words; and the Now column holds it with an earlier year's date
        reader, text = text_of(self.pdf().content)
        hb = next(t for t in self.preview()["trends"] if t["name"] == "Hemoglobin")
        self.assertIn(f"11.8 Low  {summary._short(d(-5))}", text)
        self.assertIn("High or Low (in red)", text)
        s = self.preview(labs=["Ferritin"])
        fe = next(t for t in s["trends"] if t["name"] == "Ferritin")
        self.assertIn(f"250  {summary._short(d(-10))}", text_of(self.pdf(labs=["Ferritin"]).content)[1],
                      "an in-range Now has no word")
        self.assertIsNone(fe["now"]["flag"])
        now_w = dict(summary.TREND_COLS)["NOW"]
        for flag in ("High", "Low"):
            worst = {**hb["now"], "value": "123.4", "flag": flag, "date": "2024-09-30"}
            cell = summary.now_cell(worst)[0]
            self.assertLessEqual(pdfdoc.width(cell, 9, True), now_w - 6, cell)
            big = {**s, "trends": [{**hb, "now": worst}]}
            self.assertIn(cell, text_of(summary.render(big)[0])[1], "not cut short")
        self.assertEqual(sum(w for _, w in summary.TREND_COLS if w), 432, "the columns still fit the page")

    def test_questions_draft(self):
        url = f"/api/profiles/{self.pid}/summary/questions"
        r = self.c["kim"].put(url, json={"text": "What do the numbers mean for me?"}, headers=H)   # a viewer may keep one
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.c["robin"].get(f"/api/profiles/{self.pid}/summary").json()["questions"], "What do the numbers mean for me?",
                         "the draft is the profile's, seen by everyone it's shared with")
        self.assertEqual(self.c["zed"].put(url, json={"text": "x"}, headers=H).status_code, 404)
        self.assertEqual(self.c["robin"].put(url, json={"text": "x"}).status_code, 403, "the CSRF header is needed")
        self.assertEqual(self.c["robin"].put(url, json={"text": "x" * 5000}, headers=H).status_code, 422)
        # making a PDF keeps its questions as the draft
        self.pdf(questions="Ask about the lisinopril dose")
        self.assertEqual(self.c["lee"].get(f"/api/profiles/{self.pid}/summary").json()["questions"], "Ask about the lisinopril dose")
        # encrypted at rest; not a setting the Settings screen or an export shows
        for f in os.listdir(TMP):
            if f.startswith("sanguity.db"):
                with open(os.path.join(TMP, f), "rb") as fh:
                    self.assertNotIn(b"lisinopril dose", fh.read())
        sections = self.c["robin"].get(f"/api/profiles/{self.pid}/settings/sections").json()["sections"]
        self.assertNotIn(summary.QUESTIONS_KEY, [x["key"] for s in sections for x in s["settings"]])
        db = dbm.connect()
        try:
            self.assertNotIn("summary", settings.to_file(db, self.pid))
        finally:
            db.close()
        r = self.c["robin"].put(url, json={"text": "  \n"}, headers=H)
        self.assertEqual((r.status_code, r.json()["questions"]), (200, ""))
        self.assertEqual(self.c["robin"].get(f"/api/profiles/{self.pid}/summary").json()["questions"], "")

    def test_roles_and_log(self):
        for who in ("robin", "lee", "kim"):   # owners, editors and viewers
            self.assertTrue(self.pdf(who).content.startswith(b"%PDF"))
        self.pdf("zed", 404)
        for path in ("summary", ):
            self.assertEqual(self.c["zed"].get(f"/api/profiles/{self.pid}/{path}").status_code, 404)
        self.assertEqual(client().get(f"/api/profiles/{self.pid}/summary").status_code, 401)
        r = self.c["robin"].post(f"/api/profiles/{self.pid}/summary/pdf", json=self.opts())   # no X-Sanguity
        self.assertEqual(r.status_code, 403)
        log = self.c["robin"].get(f"/api/profiles/{self.pid}/activity?action=summary").json()
        self.assertGreaterEqual(len(log), 3)
        self.assertEqual({e["actor"] for e in log} >= {"robin", "lee", "kim"}, True)
        e = log[0]["detail"]
        self.assertEqual((e["since"], e["for"], e["sections"], e["questions"]), (SINCE, d(10), ALL, 2))
        self.assertNotIn("lisinopril", json.dumps(log).lower(), "the questions are never logged")
        # previews are not logged
        n = len(log)
        self.preview()
        self.assertEqual(len(self.c["robin"].get(f"/api/profiles/{self.pid}/activity?action=summary").json()), n)

    def test_nothing_on_disk(self):
        before = files_under(TMP)
        tmp_before = set(os.listdir(tempfile.gettempdir()))
        pdf = self.pdf(questions="A fictional question about ferritin").content
        after = files_under(TMP)
        new = set(after) - set(before)
        self.assertFalse({p for p in new if not os.path.basename(p).startswith("sanguity.db")}, "no new files")
        self.assertFalse({n for n in set(os.listdir(tempfile.gettempdir())) - tmp_before if "summary" in n or n.endswith(".pdf")})
        for p in after:
            with open(p, "rb") as f:
                blob = f.read()
            self.assertNotIn(b"%PDF", blob, p)
            self.assertNotIn(pdf[-200:], blob, p)

    def test_options_refused(self):
        url = f"/api/profiles/{self.pid}/summary/pdf"
        for bad in ({"sections": []}, {"sections": ["nonsense"]}, {"appointment": 7}, {"since": d(1)}, {"since": "soon"},
                    {"labs": ["x"] * 30}):
            r = self.c["robin"].post(url, json=self.opts(**bad), headers=H)
            self.assertIn(r.status_code, (400, 422), (bad, r.text))
        # no appointment chosen, one section
        reader, text = text_of(self.pdf(appointment=None, sections=["medications"]).content)
        self.assertIn("For: no particular appointment", text)
        self.assertNotIn("Out of range since", text)
        self.assertIn("Notes from the visit", text, "room left on the page: lines for notes")

    def test_two_pages_at_most(self):
        qs = "\n".join(f"Question {i}: a fairly long fictional question about the results, the plan and what comes next?" for i in range(30))
        s = self.preview(questions=qs, labs=["Ferritin", "Glucose"])
        self.assertLessEqual(s["pages"], 2)
        # far more than two pages hold: the rest is left out, and the page is told what
        big = {**s, "medications": [{"name": f"Fictional medication {i}", "instructions": "Take 1 daily", "since": d(-i),
                                     "source": "Maple Street Clinic"} for i in range(120)]}
        pdf, fit = summary.render(big)
        self.assertEqual((fit["pages"], fit["fits"]), (2, False))
        self.assertGreater(fit["left_out"]["medications"], 0)
        self.assertEqual(fit["left_out"]["questions"], 30, "the sections after it are left out whole")
        reader, text = text_of(pdf)
        self.assertEqual(len(reader.pages), 2)
        self.assertIn("Page 2 of 2", text)
        self.assertIn("isn't medical advice", reader.pages[1].extract_text(), "the notice is on every page")

    def test_no_records(self):
        db = dbm.connect()
        pid = prof.create_profile(db, "Empty (fictional)", owner_id=self.uid["robin"])
        db.close()
        o = self.c["robin"].get(f"/api/profiles/{pid}/summary").json()
        self.assertEqual((o["upcoming"], o["visits"], o["has_records"]), ([], [], False))
        r = self.c["robin"].post(f"/api/profiles/{pid}/summary/pdf", json=self.opts(appointment=None), headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        reader, text = text_of(r.content)
        self.assertIn("No current medications in the records.", text)
        self.assertIn(f"the last {summary.DEFAULT_DAYS} days", text)

    # ---------------------------------------------------------------- the phone's and watch's measures (#225)

    @classmethod
    def measures_profile(cls):
        """A profile with a phone's readings (a fictional watch summary), immunizations and a height: made once."""
        if getattr(cls, "mpid", None):
            return cls.mpid
        db = dbm.connect()
        path = os.path.join(TMP, "phone", "daily.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        crypto.write_file(path, json.dumps(PHONE).encode())
        cls.mpid = prof.create_profile(db, "Sam (fictional)", owner_id=cls.uid["robin"], watch_file=path)
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (cls.uid["kim"], cls.mpid))
        with dbm.transaction(db):
            records.store(db, cls.mpid, MEASURED, "dataset")
        db.close()
        c = cls.c["robin"]
        assert c.post(f"/api/profiles/{cls.mpid}/person/heights", json={"date": d(-300), "cm": 175}, headers=H).status_code == 200
        return cls.mpid

    def mpreview(self, who="robin", **kw):
        pid = self.measures_profile()
        r = self.c[who].post(f"/api/profiles/{pid}/summary/preview", json=self.opts(appointment=None, **kw), headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_measures_offered_only_when_present_and_unticked(self):
        o = self.c["robin"].get(f"/api/profiles/{self.pid}/summary").json()
        self.assertFalse(set(measures.IDS) & {s["id"] for s in o["sections"]}, "a profile without them isn't offered them")
        o = self.c["kim"].get(f"/api/profiles/{self.measures_profile()}/summary").json()   # viewers too: they read them already
        secs = {s["id"]: s for s in o["sections"]}
        self.assertEqual([s for s in measures.IDS if s in secs], measures.IDS, "each one this profile has")
        self.assertTrue(all(secs[s].get("off") for s in measures.IDS), "unticked unless the person ticks them")
        self.assertFalse(any(secs[s].get("off") for s in ALL), "the summary's own sections stay ticked")
        self.assertNotIn("cycle", secs, "cycle tracking is off here: not offered (#183 has its own section)")
        self.assertEqual(len(o["sections"]), len(secs), "no section twice")

    def test_measures_since_the_last_visit(self):
        s = self.mpreview(sections=measures.IDS)
        self.assertEqual(s["since"]["date"], d(-30))
        m = s["measures"]
        bp = m["home_bp"]
        self.assertEqual([r[1] for r in bp["rows"]], ["126/80", "132/84"], "newest first; the reading before the visit left out")
        self.assertEqual(bp["lead"], ["2 readings · average 129/82 mmHg · highest systolic 132, highest diastolic 84"])
        self.assertEqual(bp["rows"][1], [d(-9) + "T08:00", "132/84", "68", "Fictional cuff (Health Connect)"])
        self.assertEqual(m["temperature"]["lead"], ["2 readings · average 37.1 °C · highest 37.4 °C"])
        self.assertEqual([r[0] for r in m["oxygen"]["rows"]], [d(-5), d(-10)])
        self.assertEqual(m["oxygen"]["lead"], ["2 days · average 96.8% · lowest 91%"])
        self.assertEqual(m["respiratory"]["rows"], [[d(-5), "15"], [d(-10), "14.2"]])
        self.assertEqual(m["vo2max"]["rows"], [[d(-20), "41.3"]])
        g = m["glucose"]
        self.assertEqual([r[:2] for r in g["rows"]], [[d(-4), "Meter: 2"], [d(-5), "CGM: 288 in 24 h"],
                                                     [d(-6), "CGM: 96 in 8 h"]])
        self.assertEqual(g["rows"][1][2:], ["62–212", "131", "78%", "4%", "under 0.1%", "18%"])
        self.assertIn("time in the standard ranges: 76% 70–180, 5% below 70 (0.5% below 54), 19% above 180", g["lead"][0])
        self.assertIn("standard published ranges", g["note"])
        self.assertIn("not anyone's own targets", g["note"])
        self.assertEqual([r[1] for r in m["immunizations"]["rows"]], ["Influenza vaccine (fictional)", "Tetanus vaccine (fictional)"],
                         "every immunization, not only since the visit")
        self.assertTrue(m["immunizations"]["history"])
        self.assertEqual(m["ecg"]["rows"], [[d(-6), "ECG: Sinus Rhythm", "72 bpm average · Fictional Watch"]], "since the visit only")
        self.assertEqual([r[1:3] for r in m["bmi"]["rows"]], [["BMI", "24.3 kg/m²"], ["BMI", "24.4 kg/m²"]])
        self.assertNotIn("samples", json.dumps(s))

    def test_measures_in_the_persons_units(self):
        c = self.c["robin"]
        try:
            c.put("/api/settings/user/units", json={"value": {"lab": "si", "temperature": "F", "weight": "kg"}}, headers=H)
            m = self.mpreview(sections=["glucose", "temperature", "bmi"])["measures"]
            self.assertEqual(m["glucose"]["cols"][2][0], "Low–high (mmol/L)")
            self.assertEqual(m["glucose"]["rows"][1][2:4], ["3.4–11.8", "7.3"])
            self.assertIn("3.9–10.0 mmol/L", m["glucose"]["note"])
            self.assertEqual(m["temperature"]["rows"][0][1], "98.2")
            self.assertIn("weight 74.5 kg", m["bmi"]["rows"][0][4])
        finally:
            c.put("/api/settings/user/units", json={"value": {}}, headers=H)

    def test_measures_in_the_pdf(self):
        pid = self.measures_profile()
        r = self.c["robin"].post(f"/api/profiles/{pid}/summary/pdf", json=self.opts(appointment=None, sections=measures.IDS), headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        reader, text = text_of(r.content)
        self.assertLessEqual(len(reader.pages), 2)
        for want in (f"Blood pressure at home since {summary.fmt_date(d(-30))} (2)", "2 readings · average 129/82 mmHg", "132/84",
                     "Fictional cuff (Health Connect)", "Body temperature at home", "37.4", "Blood glucose from the phone",
                     "CGM: 288 in 24 h", "under 0.1%", "standard published ranges", "Blood oxygen from the watch",
                     "Respiratory rate", "VO2 max", "Immunizations (2)", "Influenza vaccine (fictional)", "ECG: Sinus Rhythm",
                     "BMI and growth", "24.3 kg/m²", "isn't medical advice"):
            self.assertIn(want, text)
        self.assertNotIn("150/95", text, "before the visit")

    def test_measures_listed_at_most_so_many(self):
        many = {**PHONE, "bp_readings": [[f"{d(-1 - i // 4)}T{8 + i % 4:02d}:00:00+00:00", 120 + i % 9, 80, None, None, "Apple Health"]
                                         for i in range(60)]}
        c = measures.Ctx(many, {}, {})
        b = measures.blocks(c, ["home_bp"], lambda x: True, most=measures.SUMMARY_MOST)["home_bp"]
        self.assertEqual((len(b["rows"]), b["total"]), (measures.SUMMARY_MOST, 60))
        self.assertTrue(b["lead"][0].startswith("60 readings"))
        pdf, fit = summary.render({"profile": "x", "prepared": d(0), "date_format": "", "for": None, "since": {"date": d(-30), "label": ""},
                                   "sections": ["home_bp"], "notice": summary.NOTICE, "measures": {"home_bp": b}, "extra": []})
        self.assertIn(f"The latest {measures.SUMMARY_MOST} of 60", text_of(pdf)[1])

    def test_a_childs_growth_percentiles(self):
        """#196's percentiles on the server (growth.py, as growth.js): a child's weights, heights and BMI, each on the
        WHO's chart to 2 years or the CDC's from 2, for age and sex; an adult's BMI is the number only."""
        cfg = {"person": {"birthDate": d(-365 * 6), "sex": "female", "heights": [{"date": d(-20), "cm": 116.0, "unit": "cm"}]}}
        kid = {"metrics": [{"key": "weight", "unit": "lb", "pts": [[d(-10), 46.0]]}]}
        b = measures.blocks(measures.Ctx(kid, {}, cfg), ["bmi"], lambda x: True)["bmi"]
        rows = {r[1]: r for r in b["rows"]}
        self.assertEqual(set(rows), {"Weight", "Height", "BMI"})
        for r in rows.values():
            self.assertRegex(r[3], r"^\d+(st|nd|rd|th)$")
            self.assertIn("CDC chart" if r[1] != "BMI" else "height 116 cm", r[4])
        self.assertEqual(rows["BMI"][2], "15.5 kg/m²")
        self.assertIn("CDC growth charts", b["note"])
        baby = {"person": {"birthDate": d(-200), "sex": "male", "heights": [{"date": d(-5), "cm": 67.0, "unit": "cm"}]}}
        rows = measures.blocks(measures.Ctx({"metrics": [{"key": "weight", "unit": "lb", "pts": [[d(-3), 17.0]]}]}, {}, baby),
                               ["bmi"], lambda x: True)["bmi"]["rows"]
        self.assertEqual(sorted(r[1] for r in rows), ["Length", "Weight"], "no BMI before 2 years")
        self.assertTrue(all("WHO chart" in r[4] for r in rows))
        from server import growth
        self.assertAlmostEqual(growth.pct(growth.tables(), "weight", "male", 100, 8.1), 96.711, 2)   # the page's own number
        self.assertEqual([growth.pct_text(p) for p in (0.2, 1, 2.4, 22.6, 99.7)], ["below the 1st", "1st", "2nd", "23rd", "above the 99th"])


class Writer(unittest.TestCase):
    def test_text_and_widths(self):
        self.assertEqual(pdfdoc.plain("≤ 5 · µg “ok” ▲ naïve 日"), "<= 5 · µg “ok” ^ naïve ?")
        self.assertAlmostEqual(pdfdoc.width("Hi", 10), (722 + 222) / 100)
        self.assertGreater(pdfdoc.width("Hi", 10, bold=True), pdfdoc.width("Hi", 10))
        self.assertTrue(pdfdoc.fit("a long line of words to cut", 10, 60).endswith("…"))
        self.assertLessEqual(pdfdoc.width(pdfdoc.fit("a long line of words to cut", 10, 60), 10), 60)
        lines = pdfdoc.wrap("one two three four five six seven eight nine ten", 10, 80)
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(pdfdoc.width(l, 10) <= 80 for l in lines))

    def test_escapes_read_back(self):
        doc = pdfdoc.Document(title="Test (fictional)")
        p = doc.page()
        p.text(50, 700, r"Brackets (and) a back\slash, café – 5 µg")
        p.polyline([(50, 650), (100, 660), (150, 640)])
        p.dot(100, 660)
        reader, text = text_of(doc.output())
        self.assertIn(r"Brackets (and) a back\slash, café – 5 µg", text)
        self.assertEqual(reader.metadata.title, "Test (fictional)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
