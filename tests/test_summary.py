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

from server import accounts, db as dbm, main, pdfdoc, profiles as prof, records, settings, summary   # noqa: E402
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
