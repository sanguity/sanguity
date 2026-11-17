#!/usr/bin/env python3
"""Reference ranges from settings (#44, server/ranges.py): the checks on a range (a low or a high, low not above
high, whole-year ages, no two rows for the same lab, unit and sex covering the same ages), which row fits a result
(lab name ignoring case and spaces, the same unit only, sex, the age on the result's day, a row for the sex before
one for anyone), the three settings through the API (owners and editors set them, viewers can't, values encrypted),
and the visit summary: a result whose report gives no range is judged against the range from settings and printed
with "(settings)"; a result with its own range or flag keeps it; a result in another unit gets none. Fictional data
only.

  python3 tests/test_ranges.py
"""
import datetime, io, json, os, shutil, sys, tempfile, unittest

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
        self.assertEqual(ranges.check_birth(""), "")
        self.assertEqual(ranges.check_birth("1980-04-30"), "1980-04-30")
        for bad in ("1980-02-30", "30/04/1980", "1899-12-31", (TODAY + datetime.timedelta(days=1)).isoformat(), 1980):
            with self.assertRaises(ranges.RangeError):
                ranges.check_birth(bad)
        self.assertEqual(ranges.check_sex("female"), "female")
        with self.assertRaises(ranges.RangeError):
            ranges.check_sex("f")

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
        self.assertEqual(ranges.flag_for(201, rows[2]), "H")
        self.assertEqual(ranges.flag_for(19.9, rows[2]), "L")
        self.assertEqual(ranges.flag_for(20, rows[2]), "")
        self.assertEqual(ranges.flag_for(5, {"low": None, "high": 4}), "H")


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
                  "pts": [[day(-25), 12, None, None, "", "12"], [day(-5), 18, None, None, "L", "18"]]}]}


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
        self.assertEqual([s["key"] for s in sec["settings"]], ["ranges.birthDate", "ranges.sex", "ranges.overrides"])
        self.assertIn("range from settings", sec["help"])
        self.assertEqual(self.put("ranges.overrides", [row(low=300)]).status_code, 400)
        self.assertIn("low is above the high", self.put("ranges.overrides", [row(low=300)]).json()["detail"])
        self.assertEqual(self.put("ranges.birthDate", "1980-02-30").status_code, 400)
        self.assertEqual(self.put("ranges.sex", "female", who="kim").status_code, 403, "a viewer can't change them")
        for k, v, who in [("ranges.birthDate", "1980-06-15", "robin"), ("ranges.sex", "female", "lee"),
                          ("ranges.overrides", [row(), row(lab="Vitamin D, 25-OH", low=30, high=100)], "lee")]:
            r = self.put(k, v, who)
            self.assertEqual(r.status_code, 200, r.text)
        merged = self.c["kim"].get(f"/api/profiles/{self.pid}/settings").json()
        self.assertEqual((merged["ranges"]["birthDate"], merged["ranges"]["sex"], len(merged["ranges"]["overrides"])), ("1980-06-15", "female", 2))
        db = dbm.connect()
        try:
            rows = db.execute("SELECT key, value, sealed FROM settings WHERE key LIKE 'ranges.%'").fetchall()
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
        tr = {t["name"]: t for t in s["trends"]}
        self.assertEqual([x[2] for x in tr["Ferritin"]["spark"]], [True, False, False, True])
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

    def test_3_without_settings(self):
        self.assertEqual(self.c["robin"].post(f"/api/profiles/{self.pid}/settings/reset", json={"section": "ranges"}, headers=H).status_code, 200)
        s = self.preview()
        names = {r["name"] for r in s["out_of_range"]}
        self.assertNotIn("Ferritin", names, "with no range from settings, results without a range aren't judged")
        self.assertNotIn("settings_note", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
