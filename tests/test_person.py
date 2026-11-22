#!/usr/bin/env python3
"""About this person (#194, server/person.py, server/routes_person.py): the checks on a birth date, the sex used for
reference ranges and a height history (one a day, 20 to 272 cm, never before the birth date); the helpers what is
built on them uses (age in years and months on a day, the height in effect on a day, feet and inches); the heights a
clinic recorded, found in the records; the API (viewers read, owners and editors change, each change logged without
its value, everything encrypted at rest); the move of #44's ranges.birthDate and ranges.sex (schema step 30, and a
settings.json from before); the export; and share links, which leave it out unless "About this person" is ticked.
And targets of your own for home readings (#234, server/targets.py): their checks, kept in one unit each, set, changed
and cleared one measure at a time by owners and editors, encrypted, and time in a CGM range of the person's own.
Fictional data only.

  python3 tests/test_person.py
"""
import datetime, json, os, shutil, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-person-test-")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_CORRECTIONS=os.path.join(TMP, "config", "corrections.json"))
for d in ("records", "keys", "build", "config", "profiles"):
    os.makedirs(os.path.join(TMP, d), exist_ok=True)
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, crypto, db as dbm, main, person, profiles as prof, ranges, records, settings, share_links, targets  # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites", "lee": "maple tunnel brisk saddle"}
TODAY = datetime.date.today()


def day(n):
    return (TODAY + datetime.timedelta(days=n)).isoformat()


# a clinic's vitals: heights in inches on two days (one is also typed into the history below), a weight that isn't a
# height, and a value no height can be
DATA = {"built": day(0), "source": "fictional", "events": [{"id": 1, "t": day(-60) + "T09:00:00", "k": "visit", "title": "Office visit"}],
        "details": {}, "costs": [], "upcoming": [],
        "labs": [{"name": "Height", "unit": "in", "panel": "Vitals", "n": 3, "txt": [],
                  "pts": [[day(-400) + "T09:00:00", 66.0, None, None, "", "66"], [day(-60) + "T09:00:00", 66.5, None, None, "", "66.5"],
                          [day(-30) + "T09:00:00", 1.0, None, None, "", "1"]]},
                 {"name": "Weight", "unit": "lb", "panel": "Vitals", "n": 1, "txt": [], "pts": [[day(-60) + "T09:00:00", 150, None, None, "", "150"]]}]}


class Rules(unittest.TestCase):
    def test_checks(self):
        self.assertEqual(person.check_birth(""), "")
        self.assertEqual(person.check_birth("1980-04-30"), "1980-04-30")
        for bad in ("1980-02-30", "30/04/1980", "1899-12-31", day(1), 1980):
            with self.assertRaises(person.PersonError, msg=bad):
                person.check_birth(bad)
        self.assertEqual((person.check_due(""), person.check_due("2024-05-01")), ("", "2024-05-01"))   # #196
        for bad in ("2024-02-30", "1899-12-31", day(200), 20240501):
            with self.assertRaises(person.PersonError, msg=bad):
                person.check_due(bad)
        person.check_together("2024-03-14", [], "2024-04-25")   # six weeks early
        self.assertEqual(person.check_sex("male"), "male")
        for bad in ("f", "other", "Female"):
            with self.assertRaises(person.PersonError):
                person.check_sex(bad)
        self.assertEqual(person.check_heights([{"date": "2020-05-01", "cm": 170.184, "unit": "ftin"}, {"date": "2010-01-01", "cm": 120}]),
                         [{"date": "2010-01-01", "cm": 120, "unit": "cm"}, {"date": "2020-05-01", "cm": 170.18, "unit": "ftin"}],
                         "oldest first, to two decimals, cm unless said")
        for bad, says in [([{"date": "2020-05-01", "cm": 19}], "from 20 to 272"), ([{"date": "2020-05-01", "cm": 300}], "from 20 to 272"),
                          ([{"date": "2020-05-01", "cm": "170"}], "number"), ([{"date": "2020-05-01", "cm": True}], "number"),
                          ([{"date": "2020-5-1", "cm": 170}], "a day"), ([{"date": day(1), "cm": 170}], "to today"),
                          ([{"date": "2020-05-01", "cm": 170, "unit": "m"}], "feet and inches"),
                          ([{"date": "2020-05-01", "cm": 170}, {"date": "2020-05-01", "cm": 171}], "one per day"),
                          ("x", "list"), ([{"date": "2020-05-01", "cm": 170}] * 401, "At most")]:
            with self.assertRaises(person.PersonError, msg=says) as e:
                person.check_heights(bad)
            self.assertIn(says, str(e.exception))
        person.check_together("2010-01-01", [{"date": "2010-01-01", "cm": 50, "unit": "cm"}])   # on the birth day is fine
        with self.assertRaises(person.PersonError) as e:
            person.check_together("2010-01-02", [{"date": "2010-01-01", "cm": 50, "unit": "cm"}])
        self.assertIn("before the birth date", str(e.exception))

    def test_helpers(self):
        self.assertEqual(person.age_on("1980-04-30", "2026-04-29"), 45)
        self.assertEqual(person.age_on("1980-04-30", "2026-04-30T08:00:00"), 46)
        self.assertIsNone(person.age_on("", "2026-04-30"))
        self.assertIsNone(person.age_on("2000-01-01", "1999-12-31"))
        self.assertIs(ranges.age_on, person.age_on, "#44 uses the same age")
        months = person.age_months_on
        self.assertEqual([months("2023-03-15", d) for d in ("2023-03-15", "2023-04-14", "2023-04-15", "2024-03-14", "2024-03-15")], [0, 0, 1, 11, 12])
        self.assertEqual([months("2024-01-31", d) for d in ("2024-02-28", "2024-02-29", "2024-03-30", "2024-03-31")], [0, 1, 1, 2],
                         "born on the 31st: a month is complete on a shorter month's last day")
        self.assertIsNone(months("2024-01-31", "2024-01-30"))
        hs = [{"date": "2010-01-01", "cm": 120, "unit": "cm"}, {"date": "2020-05-01", "cm": 170.18, "unit": "ftin"}]
        self.assertIsNone(person.height_on(hs, "2009-12-31"), "no height known by then")
        self.assertEqual(person.height_on(hs, "2010-01-01")["cm"], 120)
        self.assertEqual(person.height_on(hs, "2020-04-30T23:00:00")["cm"], 120)
        self.assertEqual(person.height_on(hs, "2026-01-01")["cm"], 170.18)
        self.assertEqual(person.from_ftin(5, 7), 170.18)
        self.assertEqual((person.text(170.18), person.text(170.18, "ftin"), person.text(182.88, "ftin")), ("170.2 cm", "5 ft 7 in", "6 ft 0 in"))
        cfg = {"person": {"birthDate": "1990-03-01", "sex": "female", "heights": hs}, "ranges": {"overrides": [{"lab": "Ferritin"}]}}
        self.assertEqual(person.sex_for_ranges(cfg), "female")
        self.assertEqual(person.of({}), {"birthDate": "", "sex": "", "heights": [], "dueDate": ""})
        self.assertEqual(ranges.config(cfg), {"birthDate": "1990-03-01", "sex": "female", "overrides": [{"lab": "Ferritin"}]},
                         "#44 reads its birth date and sex from About this person")

    def test_recorded(self):
        got = person.recorded({**DATA, "imports": {"7": {"label": "C-CDA health summary", "name": "Riverside Clinic"}},
                               "labs": [{**s, "imp": 7} for s in DATA["labs"]]})
        self.assertEqual([(h["date"], h["cm"], h["unit"], h["source"]) for h in got],
                         [(day(-60), 168.91, "ftin", "Riverside Clinic"), (day(-400), 167.64, "ftin", "Riverside Clinic")],
                         "newest first, in cm, the 1-inch value and the weight left out")
        self.assertEqual(person.recorded({"labs": [{"name": "Body height", "unit": "cm", "pts": [["2020-01-01", 150, None, None, ""]]},
                                                   {"name": "Height percentile", "unit": "%", "pts": [["2020-01-01", 50, None, None, ""]]},
                                                   {"name": "Stature", "loinc": "8302-2", "unit": "m", "pts": [["2021-01-01", 1.5]]}]}),
                         [{"date": "2021-01-01", "cm": 150.0, "unit": "cm", "source": ""}, {"date": "2020-01-01", "cm": 150.0, "unit": "cm", "source": ""}])


class TargetRules(unittest.TestCase):   # #234
    def test_checks_and_units(self):
        self.assertEqual(targets.check({}), {})
        t = targets.check({"bp": {"sysHigh": 130, "diaHigh": 80, "by": "  Dr.  Rowan Vale ", "on": "2026-03-03"}})
        self.assertEqual(t, {"bp": {"sysLow": None, "sysHigh": 130, "diaLow": None, "diaHigh": 80, "by": "Dr. Rowan Vale", "on": "2026-03-03"}})
        self.assertEqual(targets.bp_text(t["bp"]), "up to 130/80 mmHg")
        self.assertEqual(targets.bp_text({"sysLow": 100, "sysHigh": 130, "diaHigh": 80}), "systolic 100–130, diastolic up to 80 mmHg")
        g = targets.typed("glucose", {"low": 4.4, "high": 7.2, "unit": "mmol/L"})
        self.assertEqual((g["low"], g["high"], g["unit"]), (79.27, 129.71, "mmol/L"), "kept in mg/dL, with how it was typed")
        self.assertEqual(round(targets.from_canonical("glucose", g["high"], "mmol/L"), 1), 7.2)
        w = targets.typed("weight", {"high": 180, "unit": "lb"})
        self.assertEqual(w["high"], 81.65, "kept in kg")
        self.assertEqual(targets.typed("temp", {"high": 100.4, "unit": "F"})["high"], 38, "kept in °C")
        self.assertEqual(targets.check({"steps": {"low": 8000}})["steps"], {"low": 8000, "high": None, "by": "", "on": ""}, "steps a day (#298), no unit to choose")
        for m, t_, says in [("bp", {}, "a low end, a high end"), ("bp", {"sysLow": 140, "sysHigh": 130}, "below the high end"),
                            ("bp", {"sysHigh": 400}, "60 to 250"), ("glucose", {"low": 4, "unit": "mmol/L"}, "20 to 600 mg/dL"),
                            ("spo2", {"low": 101}, "70 to 100%"), ("glucose", {"high": 140, "unit": "mg"}, "mg/dL or mmol/L"),
                            ("spo2", {"low": True}, "a number"), ("weight", {"high": 80, "by": "x" * 81}, "too long"),
                            ("temp", {"high": 38, "on": day(1)}, "to today"), ("pulse", {"high": 90}, "no measure"),
                            ("steps", {"low": 50}, "100 to 100,000 steps")]:
            with self.assertRaises(targets.TargetError, msg=(m, t_)) as e:
                targets.check({m: t_})
            self.assertIn(says, str(e.exception), (m, t_))
        with self.assertRaises(settings.SettingError):
            settings.put(None, "person.targets", {"spo2": {}})

    def test_compared_as_shown(self):
        self.assertEqual([targets.side(v, 80, 130) for v in (79.4, 79.6, 130.4, 131, None)], ["low", "", "", "high", ""])
        self.assertEqual(targets.side(7.84, None, 7.8, 1), "", "7.8 shown is never above a target of 7.8")
        self.assertEqual(targets.range_text(92, None, lambda v: f"{v:g}", "%"), "at least 92%")
        self.assertEqual(targets.range_text(None, 140, lambda v: f"{v:g}", "mg/dL"), "up to 140 mg/dL")

    def test_cgm_own_range(self):
        dist = [60, 2, *[0] * 9, 4, *[0] * 60, 3, 1]   # 2 at 60, 4 at 70, 3 at 131, 1 at 132
        self.assertEqual(targets.own_counts(dist, 70, 131), (2, 7, 1, 10))
        self.assertEqual(targets.own_shares(dist, 70, 131), (20.0, 70.0, 10.0))
        self.assertEqual(targets.cgm_bounds({"low": 70.26, "high": 180.16}), (70, 180), "3.9 to 10.0 mmol/L: 70 to 180, as the standard")
        w = {"metrics": [{"key": "glucose_avg", "pts": [["2026-11-01", 120]]}, {"key": "glucose_dist", "pts": [["2026-11-01", dist]]}]}
        self.assertEqual([m["key"] for m in targets.annotate(json.loads(json.dumps(w)), {})["metrics"]], ["glucose_avg"],
                         "the counts never leave the server; with no CGM target, nothing added")
        got = {m["key"]: m["pts"] for m in targets.annotate(w, {"cgm": {"low": 70, "high": 131}})["metrics"]}
        self.assertEqual((got["glucose_own_lt"], got["glucose_own_in"], got["glucose_own_gt"]),
                         ([["2026-11-01", 20.0]], [["2026-11-01", 70.0]], [["2026-11-01", 10.0]]))
        self.assertNotIn("glucose_dist", got)


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
        return f"/api/profiles/{self.pid}/person{rest}"

    def test_1_empty_and_roles(self):
        a = self.c["kim"].get(self.url()).json()
        self.assertEqual((a["birthDate"], a["sex"], a["heights"], a["can_change"]), ("", "", [], False), "all optional, unset at first")
        self.assertEqual([(h["date"], h["inHistory"]) for h in a["recorded"]], [(day(-60), False), (day(-400), False)],
                         "a clinic's heights, beside the history, not in it")
        self.assertTrue(self.c["lee"].get(self.url()).json()["can_change"])
        for r in (self.c["kim"].put(self.url(), json={"birthDate": "1990-03-01"}, headers=H),
                  self.c["kim"].post(self.url("/heights"), json={"date": day(-1), "cm": 170}, headers=H),
                  self.c["kim"].put(f"/api/profiles/{self.pid}/settings/person.sex", json={"value": "female"}, headers=H)):
            self.assertEqual(r.status_code, 403, "a viewer can't change them")
        db = dbm.connect()
        stranger = accounts.create(db, "ada", password="a stranger to every record")[0]
        db.close()
        c = TestClient(main.app, base_url="http://testserver")
        c.post("/api/login", json={"username": "ada", "password": "a stranger to every record"}, headers=H)
        self.assertEqual(c.get(self.url()).status_code, 404, "not shared: not found")
        self.assertTrue(stranger)

    def test_2_change_and_history(self):
        r = self.c["lee"].put(self.url(), json={"birthDate": "1990-03-01", "sex": "female"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["birthDate"], r.json()["sex"]), ("1990-03-01", "female"))
        self.assertIn("female", self.c["robin"].put(self.url(), json={"birthDate": "1990-02-30"}, headers=H).text.lower() + "female")
        self.assertEqual(self.c["robin"].put(self.url(), json={"birthDate": "1990-02-30"}, headers=H).status_code, 400)
        self.assertEqual(self.c["robin"].put(self.url(), json={"sex": "intersex"}, headers=H).status_code, 400)
        add = lambda who="robin", **h: self.c[who].post(self.url("/heights"), json=h, headers=H)
        self.assertEqual(add(date="2000-06-01", cm=120.5).status_code, 200)
        r = add("lee", date=day(-60), unit="ftin", ft=5, inches=6.5)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["heights"], [{"date": "2000-06-01", "cm": 120.5, "unit": "cm"}, {"date": day(-60), "cm": 168.91, "unit": "ftin"}])
        self.assertTrue(next(h for h in r.json()["recorded"] if h["date"] == day(-60))["inHistory"])
        r = add(date="2000-06-01", cm=121)
        self.assertEqual([h["cm"] for h in r.json()["heights"]], [121, 168.91], "the same day again: replaced, not added")
        for h, says in [({"date": "1989-12-31", "cm": 50}, "before the birth date"), ({"date": day(1), "cm": 150}, "to today"),
                        ({"date": day(-2), "cm": 400}, "from 20 to 272"), ({"date": day(-2)}, "in cm"),
                        ({"date": day(-2), "unit": "ftin"}, "feet and inches"), ({"date": day(-2), "unit": "ftin", "ft": -1}, "0 or more")]:
            r = add(**h)
            self.assertEqual(r.status_code, 400, h)
            self.assertIn(says, r.json()["detail"])
        r = self.c["robin"].put(self.url(), json={"birthDate": "2001-01-01"}, headers=H)
        self.assertEqual(r.status_code, 400, "a birth date after a height written down is refused")
        self.assertIn("before the birth date", r.json()["detail"])
        self.assertEqual(self.c["lee"].delete(self.url("/heights/2000-06-01"), headers=H).status_code, 200)
        self.assertEqual(self.c["lee"].delete(self.url("/heights/2000-06-01"), headers=H).status_code, 404)
        # viewers see them, in the settings every page loads too
        merged = self.c["kim"].get(f"/api/profiles/{self.pid}/settings").json()
        self.assertEqual((merged["person"]["birthDate"], merged["person"]["sex"], [h["date"] for h in merged["person"]["heights"]]),
                         ("1990-03-01", "female", [day(-60)]))
        self.assertNotIn("person", {s["id"] for s in self.c["robin"].get(f"/api/profiles/{self.pid}/settings/sections").json()["sections"]},
                         "not on the Settings screen: About this person is on Sharing")
        self.assertNotIn("birthDate", merged["ranges"], "#44's own settings no longer hold them")
        # a due date, for a baby born early (#196): only with a birth date, after it, and at most 20 weeks after it
        for due, says in [("1990-02-28", "after the birth date"), ("1990-03-01", "after the birth date"), ("1990-08-01", "20 weeks"),
                          ("1990-3-20", "a day")]:
            r = self.c["lee"].put(self.url(), json={"dueDate": due}, headers=H)
            self.assertEqual(r.status_code, 400, due)
            self.assertIn(says, r.json()["detail"])
        r = self.c["lee"].put(self.url(), json={"dueDate": "1990-04-12"}, headers=H)
        self.assertEqual((r.status_code, r.json()["dueDate"]), (200, "1990-04-12"), r.text)
        self.assertEqual(self.c["kim"].put(self.url(), json={"dueDate": ""}, headers=H).status_code, 403, "a viewer can't change it")
        r = self.c["robin"].put(self.url(), json={"birthDate": ""}, headers=H)
        self.assertEqual(r.status_code, 400, "no birth date while there's a due date")
        self.assertIn("goes with a birth date", r.json()["detail"])
        self.assertEqual(self.c["kim"].get(f"/api/profiles/{self.pid}/settings").json()["person"]["dueDate"], "1990-04-12", "viewers see it")

    def test_3_encrypted_and_logged_without_values(self):
        db = dbm.connect()
        try:
            rows = db.execute("SELECT key, value, sealed FROM settings WHERE key LIKE 'person.%'").fetchall()
            log = [json.loads(crypto.dec(r["detail"])) for r in db.execute(
                "SELECT detail FROM activity WHERE profile_id=? AND action='settings'", (self.pid,))]
        finally:
            db.close()
        self.assertEqual(sorted(r["key"] for r in rows), ["person.birthDate", "person.dueDate", "person.heights", "person.sex"])
        self.assertTrue(all(r["value"] is None and r["sealed"].startswith(crypto.FIELD) for r in rows), "encrypted at rest")
        self.assertIn("1990-03-01", crypto.dec(next(r["sealed"] for r in rows if r["key"] == "person.birthDate")))
        raw = b""
        for name in ("sanguity.db", "sanguity.db-wal"):
            if os.path.exists(os.path.join(TMP, name)):
                with open(os.path.join(TMP, name), "rb") as f:
                    raw += f.read()
        self.assertNotIn(b"1990-03-01", raw)
        self.assertNotIn(b"168.91", raw)
        self.assertNotIn(b"1990-04-12", raw, "the due date too (#196)")
        self.assertEqual({(e["setting"], e["label"]) for e in log},
                         {("person.birthDate", "Birth date"), ("person.sex", "Sex used for reference ranges"), ("person.heights", "Height"),
                          ("person.dueDate", "Due date, if born early")})
        self.assertNotIn("1990", json.dumps(log), "which setting changed, never its value")
        self.assertIn({"setting": "person.heights", "label": "Height", "change": "remove"}, log)

    def test_4_share_links_leave_it_out_unless_ticked(self):
        self.assertIn("about", share_links.SECTION_IDS)

        def view(**body):
            r = self.c["robin"].post(f"/api/profiles/{self.pid}/links", json={"hours": 24, **body}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            return TestClient(main.app).post("/api/share/view", json={"token": r.json()["path"].split("#", 1)[1]}, headers=H).json()

        plain = view(sections=list(s for s in share_links.SECTION_IDS if s != "about"))
        self.assertNotIn("about", plain)
        for word in ("1990-03-01", "female", "Female", "168.91", "5 ft 6.5 in"):
            self.assertNotIn(word, json.dumps(plain), "every other section, and still nothing about the person")
        got = view(sections=["about"], **{"from": day(-90)})
        self.assertEqual(got["about"], {"birthDate": "1990-03-01", "sex": "Female",
                                        "heights": [{"date": day(-60), "cm": 168.91, "text": "168.9 cm (5 ft 6.5 in)"}]})
        self.assertEqual(view(sections=["about"], **{"to": day(-90)})["about"]["heights"], [], "heights only in the link's dates")

    def test_7_targets(self):   # #234
        put = lambda who, t: self.c[who].put(self.url(), json={"targets": t}, headers=H)
        self.assertEqual(self.c["kim"].get(self.url()).json()["targets"], {}, "none at first")
        self.assertEqual(put("kim", {"bp": {"sysHigh": 130}}).status_code, 403, "a viewer can't set one")
        r = put("lee", {"bp": {"sysHigh": 130, "diaHigh": 80, "by": "Dr. Rowan Vale", "on": day(-20)},
                        "glucose": {"low": 4.4, "high": 7.2, "unit": "mmol/L"}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(sorted(r.json()["targets"]), ["bp", "glucose"])
        self.assertEqual(r.json()["targets"]["glucose"]["high"], 129.71)
        self.assertEqual(r.json()["measures"]["cgm"], "CGM time in range")
        r = put("robin", {"bp": {"sysHigh": 135, "diaHigh": 85}})
        self.assertEqual((r.json()["targets"]["bp"]["sysHigh"], r.json()["targets"]["bp"]["by"], r.json()["targets"]["glucose"]["low"]),
                         (135, "", 79.27), "changed one measure; the other kept")
        r = put("robin", {"glucose": None})
        self.assertEqual(list(r.json()["targets"]), ["bp"], "cleared one measure")
        r = put("robin", {"spo2": {"low": 120}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("70 to 100%", r.json()["detail"])
        self.assertEqual(self.c["kim"].get(f"/api/profiles/{self.pid}/settings").json()["person"]["targets"]["bp"]["sysHigh"], 135,
                         "viewers see it, with every setting the records page loads")
        db = dbm.connect()
        try:
            row = db.execute("SELECT value, sealed FROM settings WHERE key='person.targets' AND profile_id=?", (self.pid,)).fetchone()
            log = [json.loads(crypto.dec(x["detail"])) for x in db.execute(
                "SELECT detail FROM activity WHERE profile_id=? AND action='settings'", (self.pid,))]
        finally:
            db.close()
        self.assertTrue(row["value"] is None and row["sealed"].startswith(crypto.FIELD), "encrypted at rest")
        self.assertIn({"setting": "person.targets", "label": "Targets of your own"}, log, "logged, without its value")
        self.assertNotIn("135", json.dumps(log))
        self.assertEqual(put("robin", {"bp": None}).json()["targets"], {}, "cleared: nothing left")

    def test_5_export_carries_it(self):
        db = dbm.connect()
        try:
            out = settings.to_file(db, self.pid)
        finally:
            db.close()
        self.assertEqual((out["person"]["birthDate"], out["person"]["sex"], len(out["person"]["heights"])), ("1990-03-01", "female", 1))
        self.assertNotIn("birthDate", out.get("ranges", {}))

    def test_6_heights_from_the_phone_are_offered(self):
        """#223: the heights an uploaded Apple Health export brought (the watch summary's "heights") are shown with a
        clinic's, marked as from the phone, never in the history until someone adds one."""
        from server import watch_store
        db = dbm.connect()
        try:
            with dbm.transaction(db):
                watch_store.save_import(db, self.pid, {"metrics": [], "exercise": [], "heights": [
                    [day(-10), 169.5, "Fictional Phone", "cm"], [day(-60), 168.9, None, "ftin"], ["not a day", 170, None, "cm"],
                    [day(-5), 400, None, "cm"]]})
        finally:
            db.close()
        before = self.c["kim"].get(self.url()).json()
        phone = [h for h in before["recorded"] if h.get("phone")]
        self.assertEqual([(h["date"], h["cm"], h["unit"], h["source"], h["inHistory"]) for h in phone], [
            (day(-10), 169.5, "cm", "Apple Health on the phone (Fictional Phone)", False),
            (day(-60), 168.9, "ftin", "Apple Health on the phone", True)])   # that day is in the history already
        self.assertEqual([h["date"] for h in before["recorded"]], [day(-10), day(-60), day(-60), day(-400)], "newest first, a clinic's first on a day")
        self.assertEqual(len(before["heights"]), 1, "none added by itself")
        r = self.c["lee"].post(self.url("/heights"), json={"date": day(-10), "unit": "cm", "cm": 169.5}, headers=H)   # "Add to the history"
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(next(h for h in r.json()["recorded"] if h["date"] == day(-10))["inHistory"])


class Move(unittest.TestCase):
    """#44's ranges.birthDate and ranges.sex move to About this person, once, for every profile (schema step 30)."""
    STEP = next(i for i, s in enumerate(dbm.SCHEMA) if "ranges.birthDate" in s)

    def test_schema_step_renames_them_in_place(self):
        dbm.init()
        db = dbm.connect()
        try:
            pids = [prof.create_profile(db, f"Move {i} (fictional)") for i in range(3)]
            sealed = {}
            for pid, birth, sex in ((pids[0], "1980-06-15", "female"), (pids[1], "2015-02-01", None)):
                rows = [("ranges.birthDate", birth), ("ranges.sex", sex), ("ranges.overrides", [{"lab": "Ferritin", "unit": "ng/mL", "sex": "female",
                                                                                                  "ageFrom": 18, "ageTo": None, "low": 15, "high": 150}])]
                for k, v in rows:
                    if v is None:
                        continue
                    sealed[(pid, k)] = crypto.enc(json.dumps(v))
                    db.execute("INSERT INTO settings(key, profile_id, value, sealed, changed) VALUES(?, ?, NULL, ?, 0)", (k, pid, sealed[(pid, k)]))
            # a profile that somehow has both: the newer (person.*) is kept
            db.execute("INSERT INTO settings(key, profile_id, value, sealed, changed) VALUES('ranges.sex', ?, NULL, ?, 0)", (pids[2], crypto.enc('"male"')))
            db.execute("INSERT INTO settings(key, profile_id, value, sealed, changed) VALUES('person.sex', ?, NULL, ?, 0)", (pids[2], crypto.enc('"female"')))
            db.executescript("BEGIN;" + dbm.SCHEMA[self.STEP] + "COMMIT;")
            left = db.execute("SELECT COUNT(*) FROM settings WHERE key IN ('ranges.birthDate', 'ranges.sex')").fetchone()[0]
            self.assertEqual(left, 0, "the old names are gone")
            got = {(r["profile_id"], r["key"]): r["sealed"] for r in db.execute("SELECT profile_id, key, sealed FROM settings WHERE profile_id IN (?, ?, ?)", pids)}
            self.assertEqual(got[(pids[0], "person.birthDate")], sealed[(pids[0], "ranges.birthDate")], "the sealed value itself, unchanged")
            self.assertEqual(got[(pids[0], "person.sex")], sealed[(pids[0], "ranges.sex")])
            self.assertEqual(got[(pids[1], "person.birthDate")], sealed[(pids[1], "ranges.birthDate")])
            self.assertNotIn((pids[1], "person.sex"), got)
            self.assertIn((pids[0], "ranges.overrides"), got, "the ranges themselves stay")
            m = settings.merged(db, pids[0], None)
            self.assertEqual((m["person"]["birthDate"], m["person"]["sex"]), ("1980-06-15", "female"))
            self.assertEqual(ranges.find(ranges.config(m), "Ferritin", "ng/mL", "2020-01-01")["high"], 150, "#44 works as before, from here")
            self.assertEqual(settings.merged(db, pids[2], None)["person"]["sex"], "female")
            db.executescript("BEGIN;" + dbm.SCHEMA[self.STEP] + "COMMIT;")   # again: nothing changes
            self.assertEqual(settings.merged(db, pids[0], None)["person"]["birthDate"], "1980-06-15")
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], len(dbm.SCHEMA))
        finally:
            for pid in pids:
                prof.delete_profile(db, pid)
            db.close()

    def test_a_settings_json_from_before_is_read(self):
        """An export from before #194 (or the setup build's settings.json) says ranges.birthDate and ranges.sex."""
        old = {"ranges": {"birthDate": "1980-06-15", "sex": "male", "overrides": []}}
        self.assertEqual({k: v for k, v in settings.from_file(old).items() if k.startswith(("person.", "ranges."))},
                         {"person.birthDate": "1980-06-15", "person.sex": "male", "ranges.overrides": []})
        both = {"ranges": {"birthDate": "1980-06-15"}, "person": {"birthDate": "1981-01-01"}}
        self.assertEqual(settings.from_file(both)["person.birthDate"], "1981-01-01", "the new name wins")
        self.assertEqual(settings.from_file({"ranges": {"sex": "f"}}), {}, "a value refused is left out, as before")
        self.assertNotIn("ranges.birthDate", settings.SETTINGS)


def tearDownModule():
    shutil.rmtree(TMP, True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
