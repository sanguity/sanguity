#!/usr/bin/env python3
"""Reference ranges from settings (#44, server/ranges.py): the checks on a range (a low or a high, low not above
high, whole-year ages, no two rows for the same lab, unit and sex covering the same ages), which row fits a result
(lab name ignoring case and spaces, the same unit only, sex, the age on the result's day, a row for the sex before
one for anyone), the ranges setting and About this person's birth date and sex (#194) through the API (owners and
editors set them, viewers can't, values encrypted), and the visit summary: a result whose report gives no range is judged against the range from settings and printed
with "(settings)"; a result with its own range or flag keeps it; a result in another unit gets none. Fictional data
only.

  python3 tests/test_ranges.py
"""
import datetime, io, json, os, re, shutil, sys, tempfile, unittest, zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-ranges-test-")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_CORRECTIONS=os.path.join(TMP, "config", "corrections.json"))
for d in ("records", "keys", "build", "config", "profiles"):
    os.makedirs(os.path.join(TMP, d), exist_ok=True)
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402
from pypdf import PdfReader   # noqa: E402

from server import accounts, db as dbm, main, profiles as prof, ranges, records, settings, summary   # noqa: E402
import logging   # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ranges_matrix as M   # noqa: E402  (#752's matrix, shared with tests/browser_check.py)

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites", "lee": "maple tunnel brisk saddle"}
TODAY = datetime.date.today()


def day(n):
    return (TODAY + datetime.timedelta(days=n)).isoformat() + "T09:00:00"


def row(**kw):
    return {"lab": "Ferritin", "unit": "ng/mL", "sex": "any", "ageFrom": None, "ageTo": None, "low": 20, "high": 200, **kw}


class Rules(unittest.TestCase):
    def test_checks(self):
        self.assertEqual(ranges.check_overrides([{"lab": "  Vitamin  D ", "unit": "ng/mL", "high": 100.0}]),
                         [{"lab": "Vitamin D", "unit": "ng/mL", "sex": "any", "ageFrom": None, "ageTo": None, "low": None, "high": 100}])
        for bad, says in [([{"lab": "Ferritin", "unit": "ng/mL"}], "needs a low, a high"),
                          ([row(low=300)], "low is above the high"),
                          ([row(ageFrom=40, ageTo=20)], "age from is above"),
                          ([row(ageFrom=1.5)], "whole number"),
                          ([row(ageTo=200)], "whole number"),
                          ([row(sex="other")], "sex must be"),
                          ([row(unit="")], "unit"),
                          ([row(low="12")], "must be a number"),
                          ([row(low=True)], "must be a number"),
                          ([row(), row(lab="ferritin", unit="ng / mL", ageFrom=18)], "cover the same ages"),
                          ([row(ageTo=17), row(ageFrom=17)], "cover the same ages"),
                          ("x", "list"), ([row()] * 201, "At most")]:
            with self.assertRaises(ranges.RangeError, msg=bad if isinstance(bad, str) else bad[:2]) as e:
                ranges.check_overrides(bad)
            self.assertIn(says, str(e.exception))
        # separate bands, or the same band for different sexes, or another unit, are fine
        self.assertEqual(len(ranges.check_overrides([row(ageTo=17), row(ageFrom=18), row(sex="female"), row(sex="male"),
                                                     row(unit="ug/L")])), 5)
        # the birth date and sex are About this person's since #194 (their checks: tests/test_person.py)

    def test_age_on(self):
        self.assertEqual(ranges.age_on("1980-04-30", "2026-04-29"), 45)
        self.assertEqual(ranges.age_on("1980-04-30", "2026-04-30T08:00:00"), 46)
        self.assertIsNone(ranges.age_on("", "2026-04-30"))
        self.assertIsNone(ranges.age_on("2000-01-01", "1999-12-31"))

    def test_which_row_fits(self):
        rows = [row(low=10, high=100), row(sex="female", low=15, high=150, ageFrom=18, ageTo=49),
                row(sex="female", low=20, high=200, ageFrom=50), row(sex="male", low=30, high=300)]
        cfg = {"birthDate": "1980-06-15", "sex": "female", "overrides": rows}
        find = lambda c, lab="Ferritin", unit="ng/mL", on="2020-01-01": ranges.find(c, lab, unit, on)
        self.assertEqual(find(cfg)["high"], 150, "female, 39: the female 18-49 row, before the one for anyone")
        self.assertEqual(find(cfg, on="2030-06-15")["high"], 200, "50 on her birthday: the next band")
        self.assertEqual(find(cfg, on="1990-01-01")["high"], 100, "9: no female band fits, so the one for anyone")
        self.assertEqual(find(cfg, lab="  FERRITIN ", unit="ng / mL")["high"], 150, "case and spaces don't matter")
        self.assertIsNone(find(cfg, unit="ug/L"), "another unit: nothing, and nothing converted")
        self.assertIsNone(find(cfg, lab="Ferritin, serum"))
        self.assertEqual(find({**cfg, "sex": "male"})["high"], 300)
        self.assertEqual(find({**cfg, "sex": ""})["high"], 100, "no sex set: only the rows for anyone")
        self.assertEqual(find({**cfg, "birthDate": ""})["high"], 100, "no birth date: no row with an age band")
        self.assertIsNone(find({**cfg, "birthDate": "", "overrides": rows[1:3]}))
        self.assertIsNone(find({}))
        self.assertEqual(ranges.flag(201, 20, 200), "H")
        self.assertEqual(ranges.flag(19.9, 20, 200), "L")
        self.assertEqual(ranges.flag(20, 20, 200), "")
        self.assertEqual(ranges.flag(5, None, 4), "H")

    def test_copies(self):
        """#781: one result twice, kept as one: the kept copy's range and flag, what it lacks from the other's, as
        edits.py's merge fills an event; a flag only where it was given against the range kept."""
        for a, b, want in [((None, None, ""), (3.5, 5.1, "L"), (3.5, 5.1, "L")),   # the issue's: none, then the report's
                           ((8.5, 10.5, ""), (None, None, ""), (8.5, 10.5, "")),   # the kept's own, the flag worked out later
                           ((None, None, "L"), (135, 145, ""), (135, 145, "L")),   # a flag in one, the range in the other
                           ((135, 145, ""), (None, None, " L "), (135, 145, "L")),
                           ((3.5, 5.1, "H"), (3.4, 5.0, "L"), (3.5, 5.1, "H")),   # the kept's own wins
                           ((3.5, 5.1, ""), (3.4, 5.0, "L"), (3.5, 5.1, "")),   # a Low against another range isn't taken
                           ((None, 5, ""), (1, 5, "H"), (None, 5, "")),   # a range is its low and high together
                           ((None, None, None), (None, None, None), (None, None, ""))]:
            self.assertEqual(ranges.copies(a, b), want, (a, b))


# a portal export: ferritin with no range (twice), one with its own range, one flagged by the lab with no range,
# one in another unit; vitamin D with no range at all
DATA = {"built": day(0), "source": "fictional",
        "events": [{"id": 1, "t": day(-60), "k": "visit", "title": "Office visit"}],
        "details": {}, "costs": [], "upcoming": [],
        "labs": [{"name": "Ferritin", "unit": "ng/mL", "panel": "Iron", "n": 4, "txt": [],
                  "pts": [[day(-40), 230, None, None, "", "230"], [day(-30), 180, None, None, "", "180"],
                          [day(-20), 260, 30, 400, "", "260"], [day(-10), 15, None, None, "", "15"]]},
                 {"name": "Ferritin", "unit": "ug/L", "panel": "Iron", "n": 1, "txt": [], "pts": [[day(-50), 900, None, None, "", "900"]]},
                 {"name": "Vitamin D, 25-OH", "unit": "ng/mL", "panel": "", "n": 2, "txt": [],
                  "pts": [[day(-25), 12, None, None, "", "12"], [day(-5), 18, None, None, "L", "18"]]},
                 # #649: typed in with no range, the first before the visit
                 {"name": "Vitamin D", "unit": "ng/mL", "panel": "", "n": 4, "txt": [],
                  "pts": [[day(-70), 22, None, None, "", "22"], [day(-40), 26, None, None, "", "26"],
                          [day(-20), 34, None, None, "", "34"], [day(-5), 41, None, None, "", "41"]]},
                 # #717: the first typed in with no range, before the visit; the two since with their report's 3-17
                 {"name": "Folate", "unit": "ng/mL", "panel": "", "n": 3, "txt": [],
                  "pts": [[day(-70), 4, None, None, "", "4"], [day(-30), 6, 3, 17, "", "6"], [day(-10), 7, 3, 17, "", "7"]]}]}


class _Drawn:
    """A PDF page that keeps what's drawn on it: each text as (x0, x1, baseline, text), each polyline's points, each
    rectangle's (x0, x1), and each result's mark's x (a dot's, or a triangle's or diamond's middle)."""
    def __init__(self):
        self.texts, self.lines, self.rects, self.marks = [], [], [], []

    def text(self, x, y, s, size=10, bold=False, color=0):
        self.texts.append((x, x + summary.pdfdoc.width(s, size, bold), y, s))

    def text_right(self, x, y, s, size=10, bold=False, color=0):
        self.text(x - summary.pdfdoc.width(s, size, bold), y, s, size, bold, color)

    def polyline(self, pts, *a):
        self.lines.append(pts)

    def rect(self, x, y, w, h, *a):
        self.rects.append((x, x + w))

    def dot(self, x, y, *a):
        self.marks.append(x)

    def shape(self, pts, *a):
        self.marks.append(pts[0][0])

    def __getattr__(self, name):
        return lambda *a, **k: None


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dbm.init()
        cls.ctx = TestClient(main.app, base_url="http://testserver").__enter__()
        db = dbm.connect()
        uid = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        cls.pid = prof.create_profile(db, "Robin (fictional)", owner_id=uid["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (uid["kim"], cls.pid))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (uid["lee"], cls.pid))
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
        shutil.rmtree(TMP, True)

    def put(self, key, value, who="robin"):
        return self.c[who].put(f"/api/profiles/{self.pid}/settings/{key}", json={"value": value}, headers=H)

    def preview(self):
        r = self.c["kim"].post(f"/api/profiles/{self.pid}/summary/preview", headers=H, json={
            "appointment": None, "since": day(-45)[:10], "sections": ["out_of_range", "trends"], "labs": ["Ferritin", "Vitamin D, 25-OH"]})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_1_settings(self):
        secs = self.c["robin"].get(f"/api/profiles/{self.pid}/settings/sections").json()["sections"]
        sec = next(s for s in secs if s["id"] == "ranges")
        # the birth date and sex are About this person's since #194 (Sharing; tests/test_person.py), not this section's
        self.assertEqual([s["key"] for s in sec["settings"]], ["ranges.overrides"])
        self.assertIn("range from settings", sec["help"])
        self.assertIn("About this person", sec["settings"][0]["help"])
        self.assertEqual(self.put("ranges.overrides", [row(low=300)]).status_code, 400)
        self.assertIn("low is above the high", self.put("ranges.overrides", [row(low=300)]).json()["detail"])
        self.assertEqual(self.put("ranges.birthDate", "1980-06-15").status_code, 404, "the old name is gone")
        person = f"/api/profiles/{self.pid}/person"
        self.assertEqual(self.c["robin"].put(person, json={"birthDate": "1980-02-30"}, headers=H).status_code, 400)
        self.assertEqual(self.c["kim"].put(person, json={"sex": "female"}, headers=H).status_code, 403, "a viewer can't change them")
        for body, who in [({"birthDate": "1980-06-15"}, "robin"), ({"sex": "female"}, "lee")]:
            r = self.c[who].put(person, json=body, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
        r = self.put("ranges.overrides", [row(), row(lab="Vitamin D, 25-OH", low=30, high=100)], "lee")
        self.assertEqual(r.status_code, 200, r.text)
        merged = self.c["kim"].get(f"/api/profiles/{self.pid}/settings").json()
        self.assertEqual(ranges.config(merged)["birthDate"], "1980-06-15")
        self.assertEqual((merged["person"]["birthDate"], merged["person"]["sex"], len(merged["ranges"]["overrides"])), ("1980-06-15", "female", 2))
        db = dbm.connect()
        try:
            rows = db.execute("SELECT key, value, sealed FROM settings WHERE key LIKE 'ranges.%' OR key LIKE 'person.%'").fetchall()
        finally:
            db.close()
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["value"] is None and r["sealed"] for r in rows), "encrypted, like other health settings")

    def test_2_summary(self):
        s = self.preview()
        oor = {r["name"]: r for r in s["out_of_range"]}
        fe = oor["Ferritin"]   # 230 high and 15 low by the range from settings; 180 in it; 260 in its own 30-400
        self.assertEqual((fe["value"], fe["flag"], fe["range"], fe["out"], fe["of"]), ("15", "Low", "20–200 (settings)", 2, 4))
        vd = oor["Vitamin D, 25-OH"]   # 12 low by the range from settings; 18 flagged L by its own lab, with no range
        self.assertEqual((vd["value"], vd["flag"], vd["range"], vd["out"]), ("18", "Low", "", 2))
        # the ferritin in ug/L a row of its own after the one in ng/mL (the latest result's), not left out (#781)
        self.assertEqual([(t["name"], t["unit"]) for t in s["trends"]][:2], [("Ferritin", "ng/mL"), ("Ferritin", "ug/L")])
        self.assertEqual((s["trends"][1]["then"]["value"], s["trends"][1]["now"]), ("900", None))
        tr = {t["name"]: t for t in s["trends"][::-1]}   # (a name's first row: Ferritin's in ng/mL)
        self.assertEqual([x[2:6] for x in tr["Ferritin"]["spark"]], [["High", 20, 200, "settings"], [None, 20, 200, "settings"],
                                                                    [None, 30, 400, None], ["Low", 20, 200, "settings"]],
                         "each with its flag and its own range, from settings where its report gives none, and said so (#299, #649)")
        self.assertEqual(tr["Ferritin"]["now"]["range"], "20–200 (settings)")
        self.assertIn("range from settings", s["settings_note"])
        self.assertIn("a range set in Settings", s["notice"])
        r = self.c["kim"].post(f"/api/profiles/{self.pid}/summary/pdf", headers=H, json={
            "appointment": None, "since": day(-45)[:10], "sections": ["out_of_range", "trends"], "labs": ["Ferritin"]})
        self.assertEqual(r.status_code, 200, r.text[:200])
        text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages)
        self.assertIn("20–200 (settings)", text)
        self.assertIn("a range from settings, set by you", text)
        # a ferritin in ug/L (900) gets no range: it isn't the unit the range was set in, and nothing is converted
        pts = summary._series({"labs": [DATA["labs"][1]]}, {"overrides": [row()]})["Ferritin"]["pts"]
        self.assertEqual((pts[0]["lo"], pts[0]["hi"], pts[0]["rs"]), (None, None, "report"))

    def test_2_summary_charts(self):
        """#649: Vitamin D 22, 26, 34 and 41 typed with no range, a range from settings of 30–100: the trends' chart, its
        sparkline, the table and the PDF agree. The band is said to be from settings (the chart's end, its note), and
        the same results are flagged in all: 22 and 26 Low (22 the table's Then), 34 and 41 not (41 its Now)."""
        rows = [row(), row(lab="Vitamin D, 25-OH", low=30, high=100), row(lab="Vitamin D", low=30, high=100)]
        self.assertEqual(self.put("ranges.overrides", rows).status_code, 200)
        body = {"appointment": None, "since": day(-45)[:10], "sections": ["trends"], "labs": ["Vitamin D"]}
        r = self.c["kim"].post(f"/api/profiles/{self.pid}/summary/preview", headers=H, json=body)
        self.assertEqual(r.status_code, 200, r.text)
        s = r.json()
        vd = next(t for t in s["trends"] if t["name"] == "Vitamin D")
        self.assertEqual([p[1:6] for p in vd["spark"]],
                         [[22, "Low", 30, 100, "settings"], [26, "Low", 30, 100, "settings"], [34, None, 30, 100, "settings"],
                          [41, None, 30, 100, "settings"]], "the chart's and sparkline's points: each flag, and its range from settings")
        self.assertEqual((vd["then"], vd["now"]["flag"], vd["now"]["range"]),
                         ({"value": "22", "date": day(-70)[:10], "flag": "Low"}, None, "30–100 (settings)"),
                         "the table's Then flagged as its chart is, against the row's range from settings (so its cell needn't "
                         "say it, #752), Now in range, its range from settings")
        self.assertNotIn("own", vd["now"], "Now: its range is the row's")
        self.assertIn("range from settings", s["settings_note"])
        self.assertTrue(summary.uses_settings({**vd, "now": {**vd["now"], "range": "30–100"}}), "its chart's band alone is enough for the note")
        r = self.c["kim"].post(f"/api/profiles/{self.pid}/summary/pdf", headers=H, json=body)
        self.assertEqual(r.status_code, 200, r.text[:200])
        text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages)
        self.assertIn(f"22 Low  {summary._short(day(-70)[:10])}", text, "Then, flagged in words")
        self.assertIn(f"41  {summary._short(day(-5)[:10])}", text, "Now, in range")
        self.assertIn("30–100 from settings", text, "the chart's band, said to be from settings at its end")
        self.assertIn("or, for a result whose report gives none, the range from settings", " ".join(text.split()), "the charts' note")
        self.assertIn("a range from settings, set by you", text)
        ops = b"".join(zlib.decompress(re.search(rb"stream\n(.*?)\nendstream", o, re.S)[1]) for o in r.content.split(b" obj")
                       if b"/FlateDecode" in o)
        self.assertEqual(ops.count(b" h f"), 2, "two triangles on the chart: 22 and 26, below their range; the sparkline's 41 a dot")

    def test_2_summary_mixed(self):
        """#717: #649's case mixed: Folate 4 typed with no range before the visit, 5–20 from settings, then 6 and 7 with
        their report's 3–17. The Then flagged against the range from settings says so in its cell, beside a row whose only
        range is the report's 3–17 (4 is inside it); the chart's earlier step, from settings, is marked so, not only its
        end; and the PDF's two notes say what each flag is judged against, a range from settings among them."""
        rows = [row(), row(lab="Vitamin D", low=30, high=100), row(lab="Folate", low=5, high=20)]
        self.assertEqual(self.put("ranges.overrides", rows).status_code, 200)
        body = {"appointment": None, "since": day(-45)[:10], "sections": ["out_of_range", "trends"], "labs": ["Folate", "Vitamin D"]}
        r = self.c["kim"].post(f"/api/profiles/{self.pid}/summary/preview", headers=H, json=body)
        self.assertEqual(r.status_code, 200, r.text)
        s = r.json()
        fo = next(t for t in s["trends"] if t["name"] == "Folate")
        self.assertEqual(fo["then"], {"value": "4", "date": day(-70)[:10], "flag": "Low", "own": ["settings range", "5–20"]},
                         "Then flagged against the range from settings, which it carries")
        self.assertEqual((fo["now"], [p[1:6] for p in fo["spark"]]),
                         ({"value": "7", "date": day(-10)[:10], "flag": None, "range": "3–17"},
                          [[4, "Low", 5, 20, "settings"], [6, None, 3, 17, None], [7, None, 3, 17, None]]),
                         "Now in range by its report's 3–17; the chart's points each with its range and its source")
        r = self.c["kim"].post(f"/api/profiles/{self.pid}/summary/pdf", headers=H, json=body)
        self.assertEqual(r.status_code, 200, r.text[:200])
        text = " ".join("\n".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages).split())
        self.assertIn(f"Folate (ng/mL) 4 Low {summary._short(day(-70)[:10])} (settings range 5–20) 7", text, "the Then cell says what it's flagged against")
        self.assertIn("Each is judged against the range printed on its own report, or, where its report gives none, the range from "
                      "settings, its Range marked “(settings)”.", text, "the out-of-range note: Vitamin D's 26 is judged against one")
        self.assertIn("High or Low (in red): outside the range on that result's own report, or, where its report gives none, outside "
                      "the range from settings. A Then judged against a range other than its row's (its Now's) gives it in its cell "
                      "(“report range” or “settings range” and its numbers).", text, "the trends' note")
        # the chart: "from settings" over its first step (4's, from settings), the end the report's
        page, vpage, vd = _Drawn(), _Drawn(), next(t for t in s["trends"] if t["name"] == "Vitamin D")
        token = summary._DATES.set(("", TODAY.year))
        try:
            summary._chart(page, fo, ["Folate (ng/mL)"], 0, 200, 120)
            summary._chart(vpage, vd, ["Vitamin D (ng/mL)"], 0, 200, 120)
        finally:
            summary._DATES.reset(token)
        (a, b), = [(x0, x1) for x0, x1, _, t in page.texts if t.endswith("from settings")]
        (p4, p6, _), = page.lines
        self.assertLess((a + b) / 2, (p4[0] + p6[0]) / 2, "said over the first step, which ends halfway from 4 to 6")
        self.assertEqual([t for *_, t in vpage.texts if "settings" in t], ["30–100 from settings"], "one step, its end said as before (#649)")

    def test_2_summary_copies(self):
        """#781: one result twice (from two imports) and a lab in two units, through the server (tests/ranges_matrix.py's
        COPIES), before and after "Same result". The records page's results, each judged by the one rule (both copies, a
        series for each unit); the summary's points, trends, out-of-range rows and the sources they name, its PDF, and a
        share link: every result, a result twice once with any range and flag either copy has (ranges.copies), a lab in
        two units a row in each (none dropped: the latest's unit alone was kept, and a copy's Low lost). Then each pair
        linked as the same result: everywhere once, the older import's copy kept with the other's range and flag (a
        range in g/L given in g/dL, the kept copy's unit)."""
        db = dbm.connect()
        try:
            owner = db.execute("SELECT id FROM users WHERE username='robin'").fetchone()["id"]
            pid = prof.create_profile(db, "Copies (fictional)", owner_id=owner)
            for imp, fmt in ((1, "csv"), (2, "fhir")):
                with dbm.transaction(db):
                    records.store(db, pid, M.copies(imp, TODAY), "upload", fmt=fmt)
        finally:
            db.close()
        c, word = self.c["robin"], {"L": "Low", "H": "High"}
        self.assertEqual(c.put(f"/api/profiles/{pid}/settings/overview.keyLabs", json={"value": M.COPIES_LABS}, headers=H).status_code, 200)
        r = c.post(f"/api/profiles/{pid}/links", json={"sections": ["results"]}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        token = r.json()["path"].split("#", 1)[1]
        body = {"appointment": None, "since": M.day(M.COPIES_SINCE, TODAY)[:10], "sections": ["out_of_range", "trends"], "labs": []}

        def check(when, seen, once, trends, out):
            db = dbm.connect()
            try:
                data = records.assemble(db, pid)
            finally:
                db.close()
            page = {}   # the records page's series (same.js joinSeries: by name and unit), each result by the one rule
            for s in data["labs"]:
                page.setdefault((s["name"], s["unit"]), []).extend(s["pts"])
            page = {k: [[ranges.judge(p[1], p[2], p[3], p[4], p[5])[2], p[2], p[3]] for p in sorted(pts, key=lambda p: p[0])]
                    for k, pts in page.items()}
            self.assertEqual(page, seen, f"{when}: the records page's results")
            series = list(summary.each(summary._series(data)))
            self.assertEqual({(s["name"], s["unit"]): [[p["flag"], p["lo"], p["hi"]] for p in s["pts"]] for s in series}, once,
                             f"{when}: the summary's results: a result twice once, with any range and flag either copy has")
            labels = [data["imports"][k]["label"] for k in sorted(data["imports"], key=int)]
            s = c.post(f"/api/profiles/{pid}/summary/preview", headers=H, json=body).json()
            cell = lambda x: x and [x["value"], x["flag"]]
            got = {(t["name"], t["unit"]): [cell(t["then"]), cell(t["now"]), (t["now"] or {}).get("range", "")] for t in s["trends"]}
            self.assertEqual(list(got.items()), list(trends.items()), f"{when}: the trends, each unit its row, the latest result's first")
            pts = {(x["name"], x["unit"], p["d"]): [summary.out_of_range(p), p["lo"], p["hi"]] for x in series for p in x["pts"]}
            for t in s["trends"]:
                self.assertEqual([q[2:5] for q in t["spark"]], [pts[(t["name"], t["unit"], q[0])] for q in t["spark"]], f"{when}: {t['name']}'s chart")
            self.assertEqual({(o["name"], o["unit"]): [o["value"], o["flag"], o["range"], o["out"], o["of"]] for o in s["out_of_range"]},
                             out, f"{when}: out of range since then")
            k = next(o for o in s["out_of_range"] if o["name"] == "Potassium")
            self.assertEqual(k["source"], "; ".join(labels), f"{when}: the sources of both copies")
            r = c.post(f"/api/profiles/{pid}/summary/pdf", headers=H, json=body)
            self.assertEqual(r.status_code, 200, r.text[:200])
            text = " ".join("\n".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages).split())
            for (n, u), (v, f, rg, *_) in out.items():
                self.assertIn(f"{v} {u} {f} {rg}", text, f"{when}: the PDF's out-of-range {n}")
            for n, u in trends:
                self.assertIn(f"{n} ({u})", text, f"{when}: the PDF's trend of {n} in {u}")
            view = TestClient(main.app, base_url="http://testserver").post("/api/share/view", json={"token": token}, headers=H).json()
            self.assertEqual({(x["name"], x["unit"]): [[q["flag"], q["lo"], q["hi"], q["range"]] for q in x["results"][::-1]] for x in view["results"]},
                             {k: [[word.get(f), lo, hi, summary.range_words(lo, hi)] for f, lo, hi in v] for k, v in once.items()},
                             f"{when}: a share link's results")
            return data

        data = check("before", M.COPIES_SEEN, M.COPIES_ONCE, M.COPIES_TRENDS, M.COPIES_OUT)
        self.assertEqual(sorted(x["a"]["name"] for x in data["possible"]), ["Calcium", "Hemoglobin", "Potassium", "Sodium"])
        for x in data["possible"]:
            self.assertEqual(c.post(f"/api/profiles/{pid}/same", json={"ref": x["ref"]}, headers=H).status_code, 201)
        check("after Same result", M.COPIES_SAME, M.COPIES_SAME, M.COPIES_TRENDS_SAME, M.COPIES_OUT_SAME)
        db = dbm.connect()
        try:
            prof.delete_profile(db, pid)
        finally:
            db.close()

    def test_2_summary_matrix(self):
        """#752: one rule for a result's range, flag and source, through the server. The matrix (tests/ranges_matrix.py): a
        report's range then none; none then a report's; Settings then a report's; a report's then Settings; a range that
        changes; "<" and ">" values at and past each edge of a report's range and of one from Settings. The same flags,
        ranges and sources in the summary's points, its cells (each Then judged against a range other than its row's
        names it), its out-of-range rows, its notes, the PDF's cells and charts (a band step for each result with a
        range, none beside one without, each said with its source) and a share link (its report's ranges only); the
        importers' flag the same rule."""
        db = dbm.connect()
        try:
            owner = db.execute("SELECT id FROM users WHERE username='robin'").fetchone()["id"]
            pid = prof.create_profile(db, "Matrix (fictional)", owner_id=owner)
            with dbm.transaction(db):
                records.store(db, pid, M.records(TODAY), "upload", fmt="csv")
        finally:
            db.close()
        c, labs = self.c["robin"], list(M.CASES)
        for key, value in (("ranges.overrides", M.OVERRIDES), ("overview.keyLabs", labs)):
            self.assertEqual(c.put(f"/api/profiles/{pid}/settings/{key}", json={"value": value}, headers=H).status_code, 200)
        db = dbm.connect()
        try:
            data = records.assemble(db, pid)
            cfg = ranges.config(settings.merged(db, pid, owner))
        finally:
            db.close()
        series = summary._series(data, cfg)
        judged = {n: [[p["flag"], p["lo"], p["hi"], p["rs"]] for p in series[n]["pts"]] for n in labs}
        self.assertEqual(judged, M.JUDGED, "each result judged by the one rule: its report's, worked out against its report's "
                                           "range, or a range from settings (\"<15\" against a low of 15 is low)")
        # the band's steps: each covers the results with its range, none beside a result without one, each said with its source
        for n in labs:
            ts = [datetime.date.fromisoformat(p["d"]).toordinal() for p in series[n]["pts"]]
            got = [[[i for i, t in enumerate(ts) if a < t < b], f"{summary.range_words(lo, hi)} {summary.band_source(rs)}"]
                   for a, b, lo, hi, rs in ranges.steps([(t, p["lo"], p["hi"], p["rs"]) for t, p in zip(ts, series[n]["pts"])],
                                                        -float("inf"), float("inf"))]
            self.assertEqual(got, M.STEPS[n], n)
        # the importers' flag is the rule's: a CSV's "<30" against its own 30–110 is low, as Labs and the summary say
        from server.importers import csv_labs
        for d, v, lo, hi, f, t in M.CASES["Amylase"]:
            self.assertEqual(csv_labs._flag("", v, ranges.comparator(t), lo, hi), ranges.flag(v, lo, hi, ranges.comparator(t)), t)
        self.assertEqual([ranges.flag(30, 30, 110, ranges.comparator(t)) for t in ("<30", "<=30", "≤30", "30", ">=30")], ["L", "", "", "", ""])
        self.assertEqual([ranges.flag(110, 30, 110, ranges.comparator(t)) for t in (">110", ">=110", "≥110", "110", "<=110")], ["H", "", "", "", ""])

        body = {"appointment": None, "since": M.day(M.SINCE, TODAY)[:10], "sections": ["out_of_range", "trends"], "labs": labs}
        r = c.post(f"/api/profiles/{pid}/summary/preview", headers=H, json=body)
        self.assertEqual(r.status_code, 200, r.text)
        s = r.json()
        tr = {t["name"]: t for t in s["trends"]}
        for n, (then, now, rng) in M.TRENDS.items():
            cell = lambda x: (x["value"], x["flag"], x.get("own"))
            self.assertEqual((cell(tr[n]["then"]), cell(tr[n]["now"]), tr[n]["now"]["range"]), (then, now, rng), n)
            line = [p for p in series[n]["pts"] if p["d"] <= body["since"]][-1:] + [p for p in series[n]["pts"] if p["d"] > body["since"]]
            want = [[{"L": "Low", "H": "High"}.get(j[0]), j[1], j[2], ranges.SOURCE if j[3] == ranges.SOURCE else None]
                    for p, j in zip(series[n]["pts"], M.JUDGED[n]) if p in line]
            self.assertEqual([q[2:6] for q in tr[n]["spark"]], want, f"{n}: the chart's and sparkline's points")
        oor = {o["name"]: [o["value"], o["flag"], o["range"], o["out"], o["of"]] for o in s["out_of_range"]}
        self.assertEqual(oor, M.OUT_OF_RANGE, "out of range since then: \"<30\", \"<25\", \">110\" and \">120\" among them")
        notes = s["notes"]
        self.assertIn("A Then judged against a range other than its row's (its Now's) gives it in its cell", notes["trends"])
        self.assertIn(summary.BOUND_NOTE.strip(), notes["trends"])
        self.assertIn(summary.BOUND_NOTE.strip(), notes["out_of_range"])
        self.assertIn("or, where its report gives none, the range from settings", notes["out_of_range"])
        self.assertIn("each step marked “on report” or “from settings”, none beside a result with neither", notes["charts"])

        r = c.post(f"/api/profiles/{pid}/summary/pdf", headers=H, json=body)
        self.assertEqual(r.status_code, 200, r.text[:200])
        text = " ".join("\n".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages).split())
        for n, (then, _, _) in M.TRENDS.items():
            words = f"{then[0]}{' ' + then[1] if then[1] else ''} {summary._short(tr[n]['then']['date'])}"
            self.assertIn(words + (f" ({' '.join(w for w in then[2] if w)})" if then[2] else ""), text, f"{n}: the PDF's Then cell")
        for note in (notes["trends"], notes["out_of_range"], notes["charts"]):
            self.assertIn(note, text)
        token = summary._DATES.set(("", TODAY.year))
        try:
            for n in labs:
                page, row = _Drawn(), tr[n]
                summary._chart(page, row, [n], 0, 200, 120)
                covered = [any(a <= x <= b for a, b in page.rects) for x in page.marks]
                spark = [p for p in series[n]["pts"] if p["d"] <= body["since"]][-1:] + [p for p in series[n]["pts"] if p["d"] > body["since"]]
                self.assertEqual(covered, [p["lo"] is not None or p["hi"] is not None for p in spark], f"{n}: a band under each result with a range only")
                said = [t for *_, t in page.texts if t.endswith(("on report", "from settings"))]
                steps = {w for idx, w in M.STEPS[n] if any(series[n]["pts"][i] in spark for i in idx)}
                self.assertTrue(said and all(t in steps or t in ("on report", "from settings") for t in said),
                                f"{n}: each band label a step's words, or its source alone: {said}")
                self.assertEqual({t.endswith("from settings") for t in said}, {w.endswith("from settings") for w in steps},
                                 f"{n}: each source the band has said on it: {said}")
        finally:
            summary._DATES.reset(token)

        # a share link: its report's ranges and flags only, the same as the summary's for those
        r = c.post(f"/api/profiles/{pid}/links", json={"sections": ["results"]}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        view = TestClient(main.app, base_url="http://testserver").post("/api/share/view", json={"token": r.json()["path"].split("#", 1)[1]}, headers=H)
        self.assertEqual(view.status_code, 200, view.text)
        got = {x["name"]: [[q["flag"], q["lo"], q["hi"], q["range"]] for q in x["results"][::-1]] for x in view.json()["results"]}
        want = {n: [[{"L": "Low", "H": "High"}.get(f), lo, hi, summary.range_words(lo, hi)] for f, lo, hi, _ in M.share_judged(n)] for n in labs}
        self.assertEqual(got, want, "a share link's flags and ranges: its reports' (none from Settings), each worked out by the one rule")
        db = dbm.connect()
        try:
            prof.delete_profile(db, pid)
        finally:
            db.close()

    def test_3_without_settings(self):
        self.assertEqual(self.c["robin"].post(f"/api/profiles/{self.pid}/settings/reset", json={"section": "ranges"}, headers=H).status_code, 200)
        s = self.preview()
        names = {r["name"] for r in s["out_of_range"]}
        self.assertNotIn("Ferritin", names, "with no range from settings, results without a range aren't judged")
        self.assertNotIn("settings_note", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
