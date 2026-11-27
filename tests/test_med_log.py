#!/usr/bin/env python3
"""The medication log (server/med_log.py, routes_meds.py, issue #235): doses logged as taken, taken late or skipped by
owners and editors, corrected and removed; refusals said plainly; encrypted, logged in the activity without the
medication or the note; kept when imports are undone; in the records the page loads and in Export everything; counted
per week of what was logged, never of what was due; in the visit summary and share links only when ticked, the
phone's (Apple Health's Medications, through the watch receiver) beside the typed ones. Fictional data only.

  python3 tests/test_med_log.py
"""
import io, json, os, shutil, sys, tempfile, unittest, zipfile
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-medlog-test-")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "no-build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"))
os.makedirs(os.path.join(TMP, "records"))
os.makedirs(os.path.join(TMP, "keys"))
sys.path.insert(0, ROOT)

import logging   # noqa: E402

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, db as dbm, main, measures, med_log, profiles as prof, records, share_links   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites", "lee": "maple tunnel brisk saddle"}
TODAY = date.today()
D = lambda n: (TODAY + timedelta(days=n)).isoformat()
NOTE = "Fictional note: later than usual, after the school run"
MEDS = {"built": "2026-11-01T00:00:00+00:00", "source": "fictional meds",
        "events": [{"id": 1, "t": D(-200), "k": "med", "title": "Fictionamide 10 mg tablet", "sub": ""}],
        "details": {"1": {"type": "med", "instructions": "One tablet by mouth, with breakfast"}}, "labs": [], "costs": []}


def client():
    return TestClient(main.app, base_url="http://testserver")


class Log(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = client().__enter__()
        db = dbm.connect()
        cls.uid = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        cls.pid = prof.create_profile(db, "Mira Example (fictional)", owner_id=cls.uid["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (cls.uid["kim"], cls.pid))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (cls.uid["lee"], cls.pid))
        with dbm.transaction(db):
            cls.iid = records.store(db, cls.pid, MEDS, "dataset")
            records.store(db, cls.pid, {"events": [{"id": 2, "t": D(-300), "k": "visit", "title": "Annual physical (fictional)", "sub": ""}],
                                        "details": {}, "labs": [], "costs": []}, "upload")   # stays when the medication's is undone
        db.close()
        cls.c = {}
        for n in PW:
            c = client()
            assert c.post("/api/login", json={"username": n, "password": PW[n]}, headers=H).status_code == 200
            cls.c[n] = c

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        shutil.rmtree(TMP, True)

    def setUp(self):
        share_links.throttle._fails.clear()
        db = dbm.connect()
        db.execute("DELETE FROM med_doses")
        db.close()

    def path(self, rest=""):
        return f"/api/profiles/{self.pid}/medications/doses{rest}"

    def log(self, who="lee", code=201, **body):
        r = self.c[who].post(self.path(), json={"med": "fictionamide", "t": D(-1) + "T08:05", "status": "taken", **body}, headers=H)
        self.assertEqual(r.status_code, code, r.text)
        return r.json()

    def data(self):
        return self.c["kim"].get(f"/api/profiles/{self.pid}/data").json()

    def test_log_correct_and_remove(self):
        did = self.log(note=NOTE, slot="08:00")["id"]
        self.log(t=D(-1) + "T20:40", status="late")
        self.log(t=D(-2) + "T20:00", status="skipped")
        doses = self.data()["doses"]
        self.assertEqual([(d["t"][-5:], d["status"]) for d in doses], [("20:00", "skipped"), ("08:05", "taken"), ("20:40", "late")])
        first = next(d for d in doses if d["id"] == did)
        self.assertEqual((first["med"], first["name"], first["note"], first["slot"], first["by"]),
                         ("fictionamide", "Fictionamide 10 mg tablet", NOTE, "08:00", "lee"))
        r = self.c["robin"].put(self.path(f"/{did}"), json={"med": "fictionamide", "t": D(-1) + "T08:15", "status": "late", "note": ""}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        changed = next(d for d in self.data()["doses"] if d["id"] == did)
        self.assertEqual((changed["t"][-5:], changed["status"], changed["note"], changed["changed_by"]), ("08:15", "late", "", "robin"))
        self.assertEqual(self.c["lee"].delete(self.path(f"/{did}"), headers=H).status_code, 200)
        self.assertEqual(len(self.c["kim"].get(self.path()).json()["doses"]), 2)
        self.assertEqual(self.c["lee"].delete(self.path(f"/{did}"), headers=H).status_code, 404)

    def test_replies_carry_what_changed(self):
        """#271: each change's reply has the dose as the records list it, so the page updates in place"""
        j = self.log(note=NOTE)
        listed = lambda: {d["id"]: d for d in self.data()["doses"]}
        self.assertEqual(j["doses"], [listed()[j["id"]]])
        r = self.c["robin"].put(self.path(f"/{j['id']}"), json={"med": "fictionamide", "t": D(-1) + "T08:15", "status": "late"}, headers=H)
        self.assertEqual(r.json()["doses"], [listed()[j["id"]]])
        self.assertEqual(r.json()["doses"][0]["status"], "late")
        r = self.c["lee"].delete(self.path(f"/{j['id']}"), headers=H)
        self.assertEqual(r.json(), {"ok": True, "removed": [j["id"]]})

    def test_replies_say_the_records_revision(self):
        """#405: each change's reply says the records' revision after it, as the page's data does, so the offline copy
        can follow the change"""
        rev = lambda: self.c["kim"].get(f"/api/profiles/{self.pid}/data").headers["X-Records-Rev"]
        before = rev()
        r = self.c["lee"].post(self.path(), json={"med": "fictionamide", "t": D(-1) + "T08:05", "status": "taken"}, headers=H)
        self.assertEqual(r.headers["X-Records-Rev"], rev())
        self.assertNotEqual(r.headers["X-Records-Rev"], before)
        did = r.json()["id"]
        for change in (lambda: self.c["lee"].put(self.path(f"/{did}"), json={"med": "fictionamide", "t": D(-1) + "T08:15", "status": "late"}, headers=H),
                       lambda: self.c["lee"].delete(self.path(f"/{did}"), headers=H)):
            r = change()
            self.assertEqual((r.status_code, r.headers["X-Records-Rev"]), (200, rev()))

    def test_who_and_refusals(self):
        self.log(who="kim", code=403)
        bad = [({"med": "no such"}, "Pick the medication"), ({"status": "due"}, "taken, taken late or skipped"),
               ({"t": D(-1)}, "YYYY-MM-DDTHH:MM"), ({"t": "2026-02-30T08:00"}, "doesn't exist"),
               ({"t": D(5) + "T08:00"}, "after today"), ({"note": "x" * 1001}, "too long"), ({"slot": "8am"}, "HH:MM")]
        for body, word in bad:
            self.assertIn(word, self.log(code=400, **body)["detail"], body)
        self.assertEqual(self.c["lee"].put(self.path("/99999"), json={"med": "fictionamide", "t": D(-1) + "T08:00", "status": "taken"},
                                           headers=H).status_code, 404)

    def test_encrypted_and_logged_without_what_it_says(self):
        self.log(note=NOTE)
        db = dbm.connect()
        row = tuple(db.execute("SELECT * FROM med_doses").fetchone())
        db.close()
        text = " ".join(str(v) for v in row).lower()
        for word in ("fictionamide", "school run", "taken"):
            self.assertNotIn(word, text)
        log = self.c["robin"].get(f"/api/profiles/{self.pid}/activity?action=medication").json()
        self.assertIn('"dose.add"', json.dumps(log))
        for word in ("fictionamide", "school run"):
            self.assertNotIn(word, json.dumps(log).lower())

    def test_kept_when_the_import_is_undone_and_in_the_export(self):
        self.log(note=NOTE)
        db = dbm.connect()
        with dbm.transaction(db):
            records.drop(db, self.pid, [self.iid])
        db.close()
        try:
            data = self.data()
            self.assertNotIn("medications", data)
            self.assertEqual([d["name"] for d in data["doses"]], ["Fictionamide 10 mg tablet"], "the person's own entries stay")
            # its medication gone, a dose is still corrected (kept to its medication) but no new one is logged for it
            did = data["doses"][0]["id"]
            r = self.c["lee"].put(self.path(f"/{did}"), json={"med": "fictionamide", "t": D(-1) + "T09:00", "status": "taken", "note": NOTE}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            self.log(code=400)
        finally:
            db = dbm.connect()
            with dbm.transaction(db):
                type(self).iid = records.store(db, self.pid, MEDS, "dataset")
            db.close()
        # a stored import never takes a doses list in (an export's data.json has one)
        db = dbm.connect()
        with dbm.transaction(db):
            iid = records.store(db, self.pid, {"events": [], "doses": [{"med": "fictionamide", "t": D(-3) + "T08:00", "status": "taken"}]}, "upload")
            records.drop(db, self.pid, [iid])
        db.close()
        self.assertEqual(len(self.data()["doses"]), 1)
        r = self.c["robin"].get(f"/api/profiles/{self.pid}/export")
        self.assertEqual(r.status_code, 200, r.text[:200])
        z = zipfile.ZipFile(io.BytesIO(r.content))
        exported = json.loads(z.read("data.json"))
        self.assertEqual([(d["status"], d["note"]) for d in exported["doses"]], [("taken", NOTE)])
        self.assertIn('"doses": 1 logged', z.read("README.txt").decode())

    def test_a_dose_follows_its_medication_through_a_rename(self):   # #921, #954
        """Fictionamide renamed to its brand name: the medication is shown and grouped by its new name (as before #921),
        and its doses go with it in the same save (#954), so a dose is logged for it under the new key; undone, the doses
        it moved go back, and the one logged since under the new name, whose medication is gone, follows it back. A
        second listing of it under the brand name, renamed to the generic, merges into it with its doses."""
        did = self.log(note=NOTE)["id"]
        listing = next(e["id"] for e in self.data()["events"] if e["k"] == "med")
        r = self.c["robin"].post(f"/api/profiles/{self.pid}/edits", json={"event": listing, "action": "rename", "title": "Brandamide 10 mg tablet"}, headers=H)
        self.assertEqual(r.status_code, 201, r.text)
        edit = r.json()["id"]
        try:
            data = self.data()
            (m,) = data["medications"]
            self.assertEqual((m["key"], m["name"]), ("brandamide", "Brandamide 10 mg tablet"))
            self.assertEqual([d["med"] for d in data["doses"] if d["id"] == did], ["brandamide"], "the dose went with it")
            later = self.log(med="brandamide", t=D(-1) + "T20:00")["id"]
            self.log(code=400, t=D(-2) + "T08:00")   # its old key is no medication's now
        finally:
            self.assertEqual(self.c["robin"].delete(f"/api/profiles/{self.pid}/edits/{edit}", headers=H).status_code, 200)
        data = self.data()
        (m,) = data["medications"]
        self.assertEqual((m["key"], m["name"]), ("fictionamide", "Fictionamide 10 mg tablet"), "undone: as before")
        self.assertEqual(sorted(d["med"] for d in data["doses"] if d["id"] in (did, later)), ["fictionamide", "fictionamide"],
                         "the dose it moved back, and the one logged since with it")
        # another listing of it under the brand name, renamed to the generic: one medication, its dose with it
        db = dbm.connect()
        with dbm.transaction(db):
            iid = records.store(db, self.pid, {"events": [{"id": 9, "t": D(-50), "k": "med", "title": "Brandamide 10 mg tablet"}],
                                               "details": {"9": {"type": "med", "instructions": "One tablet by mouth, with breakfast"}}}, "upload")
        db.close()
        try:
            brand = self.log(med="brandamide", t=D(-3) + "T08:00")["id"]
            eid = next(e["id"] for e in self.data()["events"] if e.get("title") == "Brandamide 10 mg tablet")
            r = self.c["robin"].post(f"/api/profiles/{self.pid}/edits", json={"event": eid, "action": "rename", "title": "Fictionamide 10 mg tablet"}, headers=H)
            self.assertEqual(r.status_code, 201, r.text)
            data = self.data()
            self.assertEqual([m["key"] for m in data["medications"]], ["fictionamide"], "merged")
            self.assertEqual([d["med"] for d in data["doses"] if d["id"] == brand], ["fictionamide"])
            self.assertEqual(self.c["robin"].delete(f"/api/profiles/{self.pid}/edits/{r.json()['id']}", headers=H).status_code, 200)
            self.assertEqual([d["med"] for d in self.data()["doses"] if d["id"] == brand], ["brandamide"], "undone: back")
        finally:
            db = dbm.connect()
            with dbm.transaction(db):
                records.drop(db, self.pid, [iid])
            db.close()

    def test_counts_say_what_was_logged(self):
        c = med_log.counts([{"status": s} for s in ["taken"] * 11 + ["late", "skipped", "skipped"]])
        self.assertEqual(med_log.said(c), "12 of 14 logged doses taken (1 late), 2 skipped")
        self.assertEqual(med_log.said(med_log.counts([{"status": "taken"}])), "1 of 1 logged dose taken")
        self.assertEqual(med_log.said(med_log.counts([])), "No doses logged")
        self.assertEqual(med_log.week_of(date(2026, 11, 18)), date(2026, 11, 16), "weeks from Monday")

    # ---- doses against a schedule (#1095)

    def test_amounts_on_doses(self):
        did = self.log(slot="08:00", amount=2, unit="tablets")["id"]
        self.log(t=D(-1) + "T13:00", amount=0.5, unit=" ml ")
        self.log(t=D(-1) + "T14:00")
        got = {d["t"][-5:]: (d["amount"], d["unit"]) for d in self.data()["doses"]}
        self.assertEqual(got, {"08:05": (2, "tablets"), "13:00": (0.5, "ml"), "14:00": (None, "")})
        r = self.c["lee"].put(self.path(f"/{did}"), json={"med": "fictionamide", "t": D(-1) + "T08:05", "status": "taken", "slot": "08:00",
                                                          "amount": 1, "unit": "tablet"}, headers=H)
        self.assertEqual(r.json()["doses"][0]["amount"], 1, "corrected")
        for body, word in (({"amount": 0}, "above 0"), ({"amount": -1}, "above 0"), ({"unit": "u" * 41}, "too long")):
            self.assertIn(word, self.log(code=400, **body)["detail"])
        db = dbm.connect()
        row = db.execute("SELECT body FROM med_doses WHERE id=?", (did,)).fetchone()
        db.close()
        self.assertNotIn("tablet", row["body"], "encrypted with the rest")

    def test_counts_against_a_schedule(self):
        """The owner's lines (#1094): every day at 8:00, and Mondays and Fridays at 20:00, from a Monday; and a line at
        13:00 from the second week's Wednesday until its Friday; As needed gives none"""
        from server import med_reminders as mr
        mo = date(2026, 11, 2)   # a Monday
        line = lambda times, days, a, b=None: {"times": times, "amounts": [{"amount": 1, "unit": "tablet"}] * len(times), "days": days,
                                               "from": a.isoformat(), "until": b and b.isoformat(), "asneeded": False}
        lines = [line(["08:00"], list(mr.DAYS), mo), line(["20:00"], ["mo", "fr"], mo),
                 line(["13:00"], list(mr.DAYS), mo + timedelta(days=9), mo + timedelta(days=11)),
                 {**line([], list(mr.DAYS), mo), "asneeded": True}]
        week = [mo + timedelta(days=i) for i in range(7)]
        self.assertEqual([t for t, _ in med_log.scheduled(lines, mo)], ["08:00", "20:00"], "a Monday: both")
        self.assertEqual([t for t, _ in med_log.scheduled(lines, mo + timedelta(days=1))], ["08:00"], "a Tuesday: the morning's only")
        self.assertEqual([t for t, _ in med_log.scheduled(lines, mo + timedelta(days=10))], ["08:00", "13:00"], "from and until")
        self.assertEqual([t for t, _ in med_log.scheduled(lines, mo - timedelta(days=1))], [], "before from: none")
        self.assertEqual([t for t, _ in med_log.scheduled(lines, mo + timedelta(days=3), stop=(mo + timedelta(days=2)).isoformat())], [],
                         "after the records say it stopped: none")
        t = lambda d, hm: f"{d.isoformat()}T{hm}"
        doses = [{"t": t(mo, "08:02"), "status": "taken", "slot": "08:00"},
                 {"t": t(mo, "21:10"), "status": "late", "slot": "20:00"},
                 {"t": t(week[1], "08:40"), "status": "taken", "phone": True},          # the phone's, 40 minutes on: the 8:00 one
                 {"t": t(week[2], "11:30"), "status": "taken", "phone": True},          # 3½ hours on: none of them
                 {"t": t(week[3], "08:00"), "status": "skipped", "slot": "08:00"},
                 {"t": t(week[4], "08:00"), "status": "taken", "slot": "08:00"},
                 {"t": t(week[4], "08:01"), "status": "taken", "slot": "08:00"},        # a second at 8:00 with one scheduled: also
                 {"t": t(week[5], "15:00"), "status": "taken"}]                         # typed with no time of the schedule: also
        n = med_log.schedule_counts(lines, doses, week, datetime(2026, 11, 20))
        # 7 mornings + Monday's and Friday's nights = 9 scheduled; taken: Mon 8:00 and 20:00 (late), Tue's from the phone, Fri 8:00
        self.assertEqual(n, {"scheduled": 9, "taken": 4, "late": 1, "skipped": 1, "other": 3}, "others: 11:30, the second 8:00, 15:00")
        self.assertEqual(med_log.said_scheduled(n), "4 of 9 scheduled doses taken (1 late), 1 skipped; 3 other doses logged")
        # mid-Monday of the week: only the doses whose time has come, or that are logged
        n = med_log.schedule_counts(lines, doses, week, datetime(2026, 11, 2, 12, 0))
        self.assertEqual(n, {"scheduled": 2, "taken": 2, "late": 1, "skipped": 0, "other": 0}, "Monday's 20:00 logged already, so counted")
        n = med_log.schedule_counts(lines, doses[:1], week, datetime(2026, 11, 2, 12, 0))
        self.assertEqual(n["scheduled"], 1, "Monday's 20:00 not yet come")
        second = [mo + timedelta(days=7 + i) for i in range(7)]
        n = med_log.schedule_counts(lines, [], second, datetime(2026, 12, 1))
        self.assertEqual(n["scheduled"], 7 + 2 + 3, "the 13:00 line from its Wednesday until its Friday")
        self.assertEqual(med_log.said_scheduled({"scheduled": 1, "taken": 1, "late": 0, "skipped": 0}), "1 of 1 scheduled dose taken")
        self.assertEqual(med_log.said_scheduled({"scheduled": 0, "taken": 0, "late": 0, "skipped": 0}), "No scheduled doses in this period")
        self.assertEqual(med_log.said_scheduled({"scheduled": 0, "taken": 0, "late": 0, "skipped": 0, "other": 1}), "No scheduled doses in this period; 1 other dose logged")
        # two at one time: two, each taken by a dose logged against that time
        two = [line(["08:00", "08:00"], list(mr.DAYS), mo)]
        got, other = med_log.match(med_log.scheduled(two, mo), [{"t": t(mo, "08:01"), "status": "taken", "slot": "08:00"}])
        self.assertEqual([bool(g) for g in got], [True, False])
        got, other = med_log.match(med_log.scheduled(two, mo), [{"t": t(mo, "08:01"), "status": "taken", "slot": "08:00"},
                                                                {"t": t(mo, "08:03"), "status": "taken", "slot": "08:00"}])
        self.assertEqual(([bool(g) for g in got], other), ([True, True], []))

    def test_summary_counts_against_the_schedule(self):
        self.log(t=D(-1) + "T08:05", slot="08:00")
        self.log(t=D(-2) + "T08:10", status="skipped", slot="08:00")
        data = self.data()
        data["reminders"] = [{"med": "fictionamide", "times": ["08:00"], "amounts": [{"amount": 1, "unit": "tablet"}],
                              "days": ["mo", "tu", "we", "th", "fr", "sa", "su"], "from": D(-3), "until": None, "asneeded": False}]
        cx = measures.Ctx({"tz": "UTC"}, data, {})
        b = measures.blocks(cx, ["medlog"], lambda d: D(-3) <= d <= D(-1))["medlog"]
        self.assertEqual(b["lead"], ["Fictionamide 10 mg tablet: 1 of 3 scheduled doses taken, 1 skipped"])
        for word in ("due", "should", "missed", "adherence"):
            self.assertNotIn(word, json.dumps(b).lower())

    def test_phone_doses_read_as_logged(self):
        watch = {"med_doses": [["2026-11-16T08:05:00-06:00", "Fictionamide 10 mg tablet", "taken", 1, "count", "Apple Health"],
                               ["2026-11-16T20:00:00-06:00", "Fictionamide 10 mg tablet", "skipped", None, "", "Apple Health"],
                               ["2026-11-16T21:00:00-06:00", "Fictionamide 10 mg tablet", "snoozed", None, "", "Apple Health"],
                               ["not a time", "x", "taken"]]}
        from zoneinfo import ZoneInfo
        got = med_log.phone_doses(watch, ZoneInfo("America/Chicago"))
        self.assertEqual([(d["t"], d["status"], d["dose"], d["app"]) for d in got],
                         [("2026-11-16T08:05", "taken", "1 count", "Apple Health"), ("2026-11-16T20:00", "skipped", "", "Apple Health")])

    def test_summary_and_share_links_only_when_ticked(self):
        self.log(t=D(-1) + "T08:05", note=NOTE)
        self.log(t=D(-2) + "T08:10", status="skipped")
        # the visit summary offers it, unticked
        opts = self.c["robin"].get(f"/api/profiles/{self.pid}/summary").json()
        sec = next(s for s in opts["sections"] if s["id"] == "medlog")
        self.assertTrue(sec.get("off"), "unticked until chosen")
        cx = measures.Ctx({"tz": "America/Chicago", "med_doses": [[D(-1) + "T20:00:00-06:00", "Fictionamide 10 mg tablet", "taken", 1, "count", "Apple Health"]]},
                          self.data(), {})
        b = measures.blocks(cx, ["medlog"], lambda d: d >= D(-30))["medlog"]
        self.assertEqual([r[2] for r in b["rows"]], ["Taken", "Taken", "Skipped"], "newest first")
        self.assertEqual(b["rows"][0][4], "Apple Health, 1 count")
        self.assertEqual(b["rows"][1][3], NOTE)
        self.assertEqual(b["lead"], ["Fictionamide 10 mg tablet: 2 of 3 logged doses taken, 1 skipped"])
        for word in ("due", "should", "missed", "adherence"):
            self.assertNotIn(word, json.dumps(b).lower())
        # a share link: not offered unless the profile has doses, shown only when ticked
        listed = self.c["robin"].get(f"/api/profiles/{self.pid}/links").json()
        self.assertIn("medlog", [s["id"] for s in listed["sections"]])
        tok = self.c["robin"].post(f"/api/profiles/{self.pid}/links", json={"sections": ["visits"]}, headers=H).json()["path"].split("#")[1]
        view = client().post("/api/share/view", json={"token": tok}, headers=H).json()
        self.assertNotIn("school run", json.dumps(view))
        tok = self.c["robin"].post(f"/api/profiles/{self.pid}/links", json={"sections": ["medlog"]}, headers=H).json()["path"].split("#")[1]
        view = client().post("/api/share/view", json={"token": tok}, headers=H).json()
        self.assertIn(NOTE, json.dumps(view))


if __name__ == "__main__":
    unittest.main(verbosity=2)
