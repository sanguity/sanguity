#!/usr/bin/env python3
"""Lab results typed in on the import page (server/routes_manual.py, issue #27): typed rows become lab results with
their ranges, flags and canonical names; the same rows typed and in an imported CSV get the same keys; rows that
can't be read are refused with their reasons and nothing is saved; viewers can't save; the save is one import,
logged with its counts. Every other kind of record (visits, stays, studies, medications and fills, problems, claims,
payments, appointments, vitals, home measurements, workouts and documents with a file) lands where the page reads its
kind; mixed kinds save as one import that undo removes whole, watch data and the document's file included; typed
records the profile has already are flagged and left out. Fictional data only.

  python3 tests/test_manual.py
"""
import os, re, sys, tempfile, time, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-manual-test-")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "no-build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"))
os.makedirs(os.path.join(TMP, "records"))
os.makedirs(os.path.join(TMP, "keys"))
sys.path.insert(0, ROOT)

import io, json, logging   # noqa: E402

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, activity, crypto, db as dbm, main, profiles as prof, uploads   # noqa: E402
from tests.whole_records import whole   # noqa: E402  records with every record's details (#217)

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites", "ash": "copper meadow sleepy anchor"}
CSV = os.path.join(ROOT, "tests", "fixtures", "csv", "us_comma.csv")

# us_comma.csv's readable rows, typed in with ISO dates (the key is over the time read, not the text of the date)
TYPED = [
    {"date": "2025-03-14", "test": "Hemoglobin", "value": "12.9", "unit": "g/dL", "range": "13.5 - 17.5", "flag": "L"},
    {"date": "2025-03-14", "test": "Platelet Count", "value": "1,200", "unit": "K/uL", "range": "150-400"},
    {"date": "2025-03-14", "test": "LDL Cholesterol", "value": "131", "unit": "mg/dL", "range": "<100"},
    {"date": "2025-03-14", "test": "HDL Cholesterol", "value": "72", "unit": "mg/dL", "range": ">40"},
    {"date": "2025-03-14", "test": "Urine Protein", "value": "Negative", "range": "Negative"},
    {"date": "2025-04-02", "test": "Hemoglobin", "value": "13.8", "unit": "g/dL", "range": "13.5 - 17.5"},
    {"date": "2025-04-02", "test": "CRP", "value": "<5", "unit": "mg/L", "range": "<10"},
]


def client():
    return TestClient(main.app, base_url="http://testserver")


def count(pid):
    db = dbm.connect()
    try:
        return {t: db.execute(f"SELECT COUNT(*) FROM {t} WHERE profile_id=?", (pid,)).fetchone()[0]
                for t in ("imports", "events", "series", "results")}
    finally:
        db.close()


def keys(data):
    return ({e["key"] for e in data["events"] if e.get("key")}, {s["key"] for s in data["labs"] if s.get("key")})


class Manual(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = client().__enter__()   # lifespan: database, key, the import worker
        db = dbm.connect()
        uids = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        cls.pid = prof.create_profile(db, "Robin (fictional)", owner_id=uids["robin"])
        cls.csv_pid = prof.create_profile(db, "Robin's spreadsheet (fictional)", owner_id=uids["robin"])
        cls.typed_pid = prof.create_profile(db, "Robin typed (fictional)", owner_id=uids["robin"])
        cls.bad_pid = prof.create_profile(db, "Robin refused (fictional)", owner_id=uids["robin"])
        cls.kinds_pid, cls.mixed_pid, cls.dup_pid, cls.refuse_pid = (
            prof.create_profile(db, f"Robin {n} (fictional)", owner_id=uids["robin"]) for n in ("kinds", "mixed", "again", "items refused"))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (uids["kim"], cls.pid))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (uids["ash"], cls.pid))
        db.close()
        cls.robin, cls.kim, cls.ash = (cls.login(n) for n in ("robin", "kim", "ash"))

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

    @staticmethod
    def login(name):
        c = client()
        r = c.post("/api/login", json={"username": name, "password": PW[name]}, headers=H)
        assert r.status_code == 200, r.text
        return c

    def post(self, rows, save=True, pid=None, c=None):
        return (c or self.robin).post(f"/api/profiles/{pid or self.pid}/manual", json={"rows": rows, "save": save}, headers=H)

    def data(self, pid):
        return whole(self.robin, pid)

    # ---------------------------------------------------------------- saving

    def test_typed_rows_become_lab_results(self):
        r = self.post([{"date": "2026-02-03", "time": "08:15", "test": "HGB", "value": "12.1", "unit": "g/dL",
                        "range": "13.5 - 17.5", "lab": "Northfield Lab (fictional)"},
                       {"date": "2026-02-03", "time": "08:15", "test": "glucose", "value": "104", "unit": "mg/dL",
                        "low": "70", "high": "99"},
                       {"date": "2026-02-03", "test": "Vitamin Q (fictional)", "value": "3.3", "unit": "ng/mL", "flag": "H"},
                       {}])   # an empty row is left out
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertEqual(j["counts"]["results"], 3)
        self.assertEqual(j["counts"]["series"], 3)
        self.assertEqual(j["range"], {"from": "2026-02-03", "to": "2026-02-03"})
        labs = {s["name"]: s for s in self.data(self.pid)["labs"]}
        hgb = labs["Hemoglobin"]   # the canonical names lab-aliases.json gives HGB and glucose
        self.assertEqual(hgb["key"], "loinc:718-7")
        self.assertEqual(hgb["pts"], [["2026-02-03T08:15:00", 12.1, 13.5, 17.5, "L", "12.1", "Typed in"]])
        self.assertEqual(labs["Glucose, Blood"]["pts"][0][1:5], [104, 70, 99, "H"])
        self.assertEqual(labs["Vitamin Q (fictional)"]["pts"][0][4], "H")   # a flag typed with no range
        self.assertEqual(labs["Vitamin Q (fictional)"]["pts"][0][0], "2026-02-03T12:00:00")   # no time: midday, as CSVs
        data = self.data(self.pid)
        day = next(e for e in data["events"] if e.get("key") == "csv:labday:2026-02-03")
        self.assertEqual((day["k"], day["title"], day["abn"]), ("lab", "3 lab results", 3))
        item = next(e for e in data["events"] if e["k"] == "labitem" and e["title"] == "HGB")
        self.assertEqual(data["details"][str(item["id"])]["lab"], "Northfield Lab (fictional)")
        self.assertEqual(sorted(data["details"][str(day["id"])]["ids"]),
                         sorted(e["id"] for e in data["events"] if e["k"] == "labitem" and e["t"].startswith("2026-02-03")))
        db = dbm.connect()
        try:
            imp = db.execute("SELECT origin, format FROM imports WHERE profile_id=? ORDER BY id DESC", (self.pid,)).fetchone()
            self.assertEqual(tuple(imp), ("manual", "manual"))
            log = activity.for_profile(db, self.pid, action="import")[0]
            self.assertEqual((log["actor"], log["detail"]["importer"], log["detail"]["counts"]["results"]), ("robin", "manual", 3))
        finally:
            db.close()
        # the history counts what was typed (#513, #517: the save's own counts): three results in three tests
        e = self.robin.get(f"/api/profiles/{self.pid}/imports").json()["imports"][0]
        self.assertEqual((e["origin"], e["counts"]["typed"], e["counts"]["series"], e["counts"]["results"]), ("manual", {"labs": 3}, 3, 3))
        # one saved before the save's counts were kept (no note): counted from its rows, the lab day and its items not
        # as events
        db = dbm.connect()
        try:
            with dbm.transaction(db):
                db.execute("UPDATE imports SET note=NULL WHERE id=?", (e["id"],))
        finally:
            db.close()
        e = self.robin.get(f"/api/profiles/{self.pid}/imports").json()["imports"][0]
        self.assertEqual(e["counts"], {"events": 0, "kinds": {}, "series": 3, "results": 3, "documents": 0})

    def test_same_rows_as_a_csv_import_same_keys(self):
        db = dbm.connect()
        try:
            up = uploads.create(db, self.csv_pid, None)
            with open(CSV, "rb") as f:
                uploads.add_file(db, self.csv_pid, up, "labs.csv", io.BytesIO(f.read()))
            uploads.finish(db, self.csv_pid, up)
        finally:
            db.close()
        r = self.robin.post(f"/api/profiles/{self.csv_pid}/uploads/{up}/import", json={}, headers=H)
        self.assertEqual(r.status_code, 202, r.text)
        jid, end = r.json()["id"], time.time() + 20
        while (j := self.robin.get(f"/api/profiles/{self.csv_pid}/jobs/{jid}").json())["state"] not in ("done", "failed"):
            self.assertLess(time.time(), end, j)
            if j["state"] == "preview":   # an import waits at its preview (#22): save it, as the page's Save does
                c = self.robin.post(f"/api/profiles/{self.csv_pid}/jobs/{jid}/confirm", json={}, headers=H)
                self.assertEqual(c.status_code, 202, c.text)
            time.sleep(0.05)
        self.assertEqual((j["state"], j["importer"]), ("done", "csv"), j)
        r = self.post(TYPED, pid=self.typed_pid)
        self.assertEqual(r.status_code, 200, r.text)
        from_csv, typed = self.data(self.csv_pid), self.data(self.typed_pid)
        self.assertEqual(keys(typed), keys(from_csv))
        self.assertEqual(len(keys(typed)[0]), 9)   # seven results and two days
        norm = lambda d: sorted((s["name"], s["key"], s["unit"], [p[:6] for p in s["pts"]], s["txt"]) for s in d["labs"])
        self.assertEqual(norm(typed), norm(from_csv))   # names, ranges and flags read alike; only the source tag differs
        self.assertEqual({p[6] for s in typed["labs"] for p in s["pts"]}, {"Typed in"})

    # ---------------------------------------------------------------- refusing

    def test_bad_rows_refused_with_reasons_and_nothing_saved(self):
        before = count(self.bad_pid)
        rows = [{"date": "2026-01-05", "test": "WBC", "value": "6.1", "unit": "K/uL"},
                {"date": "2026-01-05", "value": "4.4"},
                {"test": "WBC", "value": "5"},
                {"date": "2026-13-40", "test": "WBC", "value": "5"},
                {"date": "2026-01-06", "test": "WBC"},
                {"date": "2026-01-05", "test": "wbc", "value": "6.1", "unit": "K/uL"}]
        r = self.post(rows, pid=self.bad_pid)
        self.assertEqual(r.status_code, 400, r.text)
        j = r.json()
        self.assertEqual(j["problems"], ["Row 2 needs a test name.", "Row 3 needs a date.",
                                         "Row 4 has a date that couldn't be read (2026-13-40).", "Row 5 needs a value.",
                                         "Row 6 repeats row 1."])
        self.assertTrue(j["detail"].startswith("Row 2 needs a test name.") and j["detail"].endswith("Nothing was saved."), j["detail"])
        self.assertEqual([x["ok"] for x in j["rows"]], [True, False, False, False, False, False])
        self.assertEqual(count(self.bad_pid), before)
        r = self.post([{}, {"date": "", "test": " "}], pid=self.bad_pid)
        self.assertEqual((r.status_code, r.json()["detail"]), (400, "There's nothing to save: type a result first."))
        r = self.post([{"date": "03/04/2026", "test": "WBC", "value": "5"}], pid=self.bad_pid)   # 3 April or 4 March?
        self.assertEqual(r.status_code, 400)
        self.assertIn("YYYY-MM-DD", r.json()["problems"][0])
        r = self.post([{"date": "03/04/2026", "test": "WBC", "value": "5"},   # another row settles it: day first
                       {"date": "25/04/2026", "test": "WBC", "value": "6"}], save=False, pid=self.bad_pid)
        self.assertEqual([x["date"] for x in r.json()["rows"]], ["2026-04-03", "2026-04-25"])
        self.assertEqual(count(self.bad_pid), before)

    def test_checking_saves_nothing_and_says_what_each_row_became(self):
        before = count(self.pid)
        r = self.post([{"date": "2026-03-01", "test": "hgb", "value": "18.1 H", "unit": "g/dL", "range": "13.5-17.5"},
                       {"date": "2026-03-01", "test": "Urine Protein", "value": "Trace", "range": "around normal"}], save=False)
        self.assertEqual(r.status_code, 200, r.text)
        a, b = r.json()["rows"]
        self.assertEqual({k: a[k] for k in ("ok", "date", "name", "value", "low", "high", "flag")},
                         {"ok": True, "date": "2026-03-01", "name": "Hemoglobin", "value": 18.1, "low": 13.5, "high": 17.5, "flag": "H"})
        self.assertEqual((b["value"], b["text"]), (None, "Trace"))
        self.assertTrue(any("text result" in n for n in b["notes"]) and any("range couldn't be read" in n for n in b["notes"]), b)
        self.assertEqual(r.json()["problems"], [])
        self.assertEqual(count(self.pid), before)

    def test_a_one_sided_range_says_its_edge(self):
        """#826: each row's range with its edge (common.range_edge, #780), for the page to say it as Labs does: "<5"
        leaves 5 out of it (5 is High), "<=5" keeps it; ">60" leaves 60 out (60 is Low), ">=60" keeps it; a range of two
        limits has none. The page said "under 5" for "<5" and "<=5" alike."""
        rows = [{"date": "2026-03-02", "time": f"{8 + i:02d}:00", "test": t, "value": v, "unit": u, "range": rg}
                for i, (t, v, u, rg) in enumerate([("CRP", "5", "mg/L", "<5"), ("CRP", "5", "mg/L", "<=5"), ("eGFR", "60", "mL/min", ">60"),
                                                   ("eGFR", "60", "mL/min", ">=60"), ("Potassium", "5.2", "mmol/L", "3.5-5.1")])]
        r = self.post(rows, save=False)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual([[x["low"], x["high"], x["edge"], x["flag"]] for x in r.json()["rows"]],
                         [[None, 5, "<", "H"], [None, 5, "", ""], [60, None, ">", "L"], [60, None, "", ""], [3.5, 5.1, "", "H"]])

    def test_a_pasted_flag_is_read_by_the_one_rule(self):
        """#835: a flag as pasted ("Abnormal", "A", "AA", "Critical", "Yes", "Y", "No", "High", "Critical high", a
        sentence the rule doesn't read) is sent as it is and read by the one rule (common.report_flag), as a spreadsheet's
        Flag column is: kept "A" inside the range, placed by it past it, the rest by the range alone, and noted only
        where it isn't a flag the rule reads. A flag up to 60 characters is taken, as a pasted cell may be."""
        flags = ["Abnormal", "A", "AA", "Critical", "Yes", "Y", "No", "High", "Critical high", "Abnormal", "Yes", "see the comment below this"]
        values = ["14"] * 9 + ["19", "12", "14"]
        r = self.post([{"date": "2026-03-02", "time": f"08:{i:02d}", "test": "Vitamin Q (fictional)", "value": v, "unit": "ng/mL",
                        "range": "13 - 17", "flag": f} for i, (f, v) in enumerate(zip(flags, values))], save=False)
        self.assertEqual(r.status_code, 200, r.text)
        got = [(x["flag"], [n for n in x["notes"] if "flag" in n]) for x in r.json()["rows"]]
        self.assertEqual([f for f, _ in got], ["A", "A", "A", "A", "A", "A", "", "H", "H", "H", "L", ""])
        self.assertEqual([bool(n) for _, n in got], [False] * 11 + [True], got)

    def test_roles_and_csrf(self):
        row = [{"date": "2026-04-01", "test": "WBC", "value": "7.0", "unit": "K/uL"}]
        before = count(self.pid)
        r = self.post(row, c=self.kim)
        self.assertEqual(r.status_code, 403, r.text)   # a viewer can't save
        self.assertEqual(self.post(row, save=False, c=self.kim).status_code, 403)
        self.assertEqual(count(self.pid), before)
        r = self.robin.post(f"/api/profiles/{self.pid}/manual", json={"rows": row, "save": True})   # no X-Sanguity
        self.assertEqual(r.status_code, 403)
        self.assertEqual(count(self.pid), before)
        self.assertEqual(self.post(row, c=self.ash).status_code, 200)   # an editor can
        self.assertEqual(count(self.pid)["imports"], before["imports"] + 1)
        self.assertEqual(self.post(row, pid=self.csv_pid, c=self.ash).status_code, 404)   # not shared with them
        self.assertEqual(client().get("/api/lab-names").status_code, 401)

    def test_lab_names_and_limits(self):
        names = self.kim.get("/api/lab-names").json()
        self.assertIn("Hemoglobin", names)
        self.assertNotIn("HGB", names)   # canonical names only
        self.assertEqual(names, sorted(names, key=str.lower))
        r = self.post([{"date": "2026-01-01", "test": "WBC", "value": "5"}] * 201)
        self.assertEqual(r.status_code, 422)
        r = self.post([{"date": "2026-01-01", "test": "x" * 500, "value": "5"}])
        self.assertEqual(r.status_code, 422)

    # ---------------------------------------------------------------- every other kind of record

    def items(self, items, save=True, pid=None, c=None, rows=None, **extra):
        body = {"items": items, "save": save, **extra}
        if rows is not None:
            body["rows"] = rows
        return (c or self.robin).post(f"/api/profiles/{pid or self.pid}/manual", json=body, headers=H)

    def upload(self, pid, name, content):
        """A file sent the way the page sends one: a new upload, the file, finished."""
        up = self.robin.post(f"/api/profiles/{pid}/uploads", headers=H).json()["id"]
        r = self.robin.put(f"/api/profiles/{pid}/uploads/{up}/files", params={"path": name}, content=content, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.robin.post(f"/api/profiles/{pid}/uploads/{up}/finish", headers=H).status_code, 200)
        return up

    def watch(self, pid):
        r = self.robin.get(f"/api/profiles/{pid}/watch")
        return None if r.status_code == 204 else r.json()

    def test_each_kind_lands_where_the_page_reads_it(self):
        pid = self.kinds_pid
        r = self.items([
            {"kind": "visit", "date": "2026-05-04", "time": "09:30", "title": "Follow-up visit", "who": "Dr. Ada Quill (fictional)",
             "where": "Lakeside Clinic (fictional)", "dx": "Anemia", "note": "Recheck in 3 months."},
            {"kind": "stay", "admit": "2026-05-10", "admit_time": "21:40", "discharge": "2026-05-13", "where": "Harbor Hospital (fictional)",
             "how": "Emergency", "note": "Discharged home."},
            {"kind": "procedure", "date": "2026-05-11", "title": "Bone marrow biopsy", "where": "Harbor Hospital (fictional)", "text": "Tolerated well."},
            {"kind": "imaging", "date": "2026-05-12", "title": "Chest X-ray", "who": "Dr. Ben Ito (fictional)", "text": "No acute findings."},
            {"kind": "study", "date": "2026-05-14", "title": "Pathology report", "text": "Normal cellularity."},
            {"kind": "med", "name": "Ferrous sulfate 325 mg tablet", "instructions": "1 tablet daily", "start": "2026-05-04",
             "stop": "2026-08-04", "prescriber": "Dr. Ada Quill (fictional)"},
            {"kind": "rx", "date": "2026-05-05", "drug": "Ferrous sulfate 325 mg tablet", "pharmacy": "Corner Pharmacy (fictional)",
             "supply": "30", "billed": "$12.00", "paid": "8", "owe": "4.00", "insurer": "Example Health (fictional)"},
            {"kind": "problem", "name": "Iron deficiency anemia", "status": "Active", "since": "2026-05-04"},
            {"kind": "immunization", "date": "2026-05-04", "vaccine": "Influenza vaccine", "dose": "1", "site": "Left arm",
             "lot": "FICT-0099", "who": "Sam Ortega, RN (fictional)", "where": "Lakeside Clinic (fictional)"},
            {"kind": "claim", "date": "2026-05-04", "provider": "Lakeside Clinic (fictional)", "title": "Office visit",
             "billed": "$240.00", "paid": "180", "owe": "60", "status": "Paid", "insurer": "Example Health (fictional)"},
            {"kind": "payment", "date": "2026-06-01", "payee": "Lakeside Clinic (fictional)", "amount": "60", "group": "Medical bills"},
            {"kind": "upcoming", "date": "2026-12-03", "time": "10:00", "title": "Follow-up visit", "who": "Dr. Ada Quill (fictional)",
             "where": "Lakeside Clinic (fictional)"},
            {"kind": "vitals", "date": "2026-05-04", "time": "09:35", "where": "Lakeside Clinic (fictional)", "bp": "118/76", "hr": "72",
             "temp": "37", "temp_unit": "C", "height": "170", "height_unit": "cm", "spo2": "98", "weight": "80", "weight_unit": "kg"},
            {"kind": "home", "date": "2026-05-06", "weight": "176.4", "steps": "8,210"},
            {"kind": "workout", "date": "2026-05-06", "time": "07:00", "minutes": "40", "activity": "Brisk walk"},
        ], pid=pid)
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertEqual(j["counts"]["typed"], {"claim": 1, "home": 1, "imaging": 1, "immunization": 1, "med": 1, "payment": 1, "problem": 1, "procedure": 1,
                                                "rx": 1, "stay": 1, "study": 1, "upcoming": 1, "visit": 1, "vitals": 1, "workout": 1})
        self.assertEqual((j["counts"]["watch_days"], j["counts"]["workouts"]), (1, 1))
        # #317: each summary split at its own dates (at the odd places, as stored), for the page to write those in the
        # person's format and the rest as typed
        for x in j["items"]:
            self.assertEqual("".join(x["summary_parts"]), x["summary"], x)
            self.assertTrue(all(re.fullmatch(r"\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?", d) for d in x["summary_parts"][1::2]), x)
        parts = {x["kind"]: x["summary_parts"] for x in j["items"]}
        self.assertEqual(parts["visit"][:3], ["", "2026-05-04 09:30", " · Follow-up visit · Dr. Ada Quill (fictional) · Lakeside Clinic (fictional)"])
        self.assertEqual(parts["stay"], ["", "2026-05-10", " to ", "2026-05-13", " · 3 nights · Harbor Hospital (fictional) · Emergency"])
        self.assertEqual(parts["problem"], ["Iron deficiency anemia · Active · since ", "2026-05-04", ""])
        typed = self.items([{"kind": "visit", "date": "2026-05-04", "title": "Scan 2026-05-01\x00 follow-up"}], save=False, pid=pid).json()
        self.assertEqual(typed["items"][0]["summary_parts"], ["", "2026-05-04", " · Scan 2026-05-01 follow-up"])   # a typed date isn't one
        data = self.data(pid)
        ev = {e["k"]: e for e in data["events"]}
        det = lambda k: data["details"][str(ev[k]["id"])]
        self.assertTrue(all(e["key"].startswith("manual:h:") for e in data["events"]), [e["key"] for e in data["events"]])
        self.assertEqual((ev["visit"]["t"], ev["visit"]["title"], ev["visit"]["sub"]),
                         ("2026-05-04T09:30:00", "Follow-up visit", "Dr. Ada Quill (fictional) · Lakeside Clinic (fictional)"))
        self.assertEqual({k: det("visit")[k] for k in ("type", "who", "where", "dx", "note", "procs")},
                         {"type": "visit", "who": "Dr. Ada Quill (fictional)", "where": "Lakeside Clinic (fictional)", "dx": "Anemia",
                          "note": "Recheck in 3 months.", "procs": []})
        self.assertEqual((ev["stay"]["t"], ev["stay"]["end"]), ("2026-05-10T21:40:00", "2026-05-13T23:59:00"))
        self.assertIn("3 nights", ev["stay"]["sub"])
        self.assertEqual({k: det("stay")[k] for k in ("type", "where", "admission", "note")},
                         {"type": "stay", "where": "Harbor Hospital (fictional)", "admission": "Emergency", "note": "Discharged home."})
        self.assertTrue(det("stay")["discharge"])   # the drawer shows admit and discharge times from it
        for k, text in (("procedure", "Tolerated well."), ("imaging", "No acute findings."), ("study", "Normal cellularity.")):
            self.assertEqual((det(k)["type"], det(k)["report"]), ("result", [["Findings", text, 0]]))
        self.assertEqual(det("imaging")["provider"], "Dr. Ben Ito (fictional)")
        self.assertEqual((ev["med"]["t"], det("med")["instructions"], det("med")["status"], det("med")["end"][:10], det("med")["prescriber"]),
                         ("2026-05-04T12:00:00", "1 tablet daily", "Stopped", "2026-08-04", "Dr. Ada Quill (fictional)"))
        costs = {c["kind"]: c for c in data["costs"]}
        self.assertEqual({k: costs["pharmacy"][k] for k in ("id", "d", "billed", "paid", "owe", "cat", "title")},
                         {"id": ev["rx"]["id"], "d": "2026-05-05", "billed": 12.0, "paid": 8.0, "owe": 4.0, "cat": "Pharmacy",
                          "title": "Ferrous sulfate 325 mg tablet"})
        self.assertEqual((det("rx")["type"], det("rx")["kind"]), ("claim", "Pharmacy"))
        self.assertEqual({k: costs["medical"][k] for k in ("id", "d", "ins", "billed", "paid", "owe", "status", "cat", "prov", "title")},
                         {"id": ev["claim"]["id"], "d": "2026-05-04", "ins": "Example Health (fictional)", "billed": 240.0, "paid": 180.0,
                          "owe": 60.0, "status": "Paid", "cat": "Doctor visits", "prov": "Lakeside Clinic (fictional)", "title": "Office visit"})
        self.assertEqual((det("claim")["type"], det("claim")["kind"], det("claim")["visit"]),
                         ("claim", "Medical", {"date": "2026-05-04", "provider": "Lakeside Clinic (fictional)"}))
        # #179: an immunization typed in has the detail the C-CDA and FHIR importers give one
        self.assertEqual((ev["immunization"]["t"], ev["immunization"]["title"], ev["immunization"]["sub"]),
                         ("2026-05-04T12:00:00", "Influenza vaccine", "Dose 1 · Sam Ortega, RN (fictional) · Lakeside Clinic (fictional)"))
        self.assertEqual(det("immunization"), {"type": "immunization", "vaccine": "Influenza vaccine", "dose": "1", "site": "Left arm",
                                               "lot": "FICT-0099", "performer": "Sam Ortega, RN (fictional)",
                                               "where": "Lakeside Clinic (fictional)", "src": "Typed in"})
        self.assertEqual({k: data["problems"][0][k] for k in ("name", "status", "date", "type")},
                         {"name": "Iron deficiency anemia", "status": "Active", "date": "2026-05-04", "type": "Diagnosis"})
        pay = data["outOfPocket"]["payments"][0]
        self.assertEqual({k: pay[k] for k in ("d", "payee", "amount", "group", "est")},
                         {"d": "2026-06-01", "payee": "Lakeside Clinic (fictional)", "amount": 60.0, "group": "Medical bills", "est": False})
        self.assertEqual({k: data["upcoming"][0][k] for k in ("t", "title", "who", "where")},
                         {"t": "2026-12-03T10:00:00", "title": "Follow-up visit", "who": "Dr. Ada Quill (fictional)", "where": "Lakeside Clinic (fictional)"})
        labs = {s["name"]: s for s in data["labs"]}
        self.assertEqual({n: (labs[n]["panel"], labs[n]["key"], labs[n]["unit"], labs[n]["pts"][0][1]) for n in labs},
                         {"Blood pressure, systolic": ("Vitals", "loinc:8480-6", "mmHg", 118), "Blood pressure, diastolic": ("Vitals", "loinc:8462-4", "mmHg", 76),
                          "Heart rate (clinic)": ("Vitals", "loinc:8867-4", "bpm", 72), "Temperature": ("Vitals", "loinc:8310-5", "°F", 98.6),
                          "Height": ("Vitals", "loinc:8302-2", "in", 66.9), "SpO2 (clinic)": ("Vitals", "loinc:59408-5", "%", 98)})
        self.assertEqual(labs["Heart rate (clinic)"]["pts"][0], ["2026-05-04T09:35:00", 72, None, None, "", "72 bpm", "Typed in"])
        self.assertEqual(data["clinicWeights"], [["2026-05-04T09:35:00", 176.4, "Lakeside Clinic (fictional)"]])
        w = self.watch(pid)
        self.assertEqual({m["key"]: (m["group"], m["unit"], m["pts"]) for m in w["metrics"]},
                         {"weight": ("Body", "lb", [["2026-05-06", 176.4]]), "steps": ("Activity", "steps", [["2026-05-06", 8210]])})
        self.assertEqual({m["key"]: m.get("typed") for m in w["metrics"]}, {"weight": ["2026-05-06"], "steps": ["2026-05-06"]},
                         "marked as typed in: a weight typed in is never judged as a scale's (#783)")
        self.assertEqual({k: w["exercise"][0][k] for k in ("start", "end", "name", "kind", "minutes")},
                         {"start": "2026-05-06T07:00:00", "end": "2026-05-06T07:40:00", "name": "Brisk walk", "kind": "brisk_walk", "minutes": 40})
        self.assertTrue(w["exercise"][0]["key"].startswith("manual:h:"))
        db = dbm.connect()
        try:
            log = activity.for_profile(db, pid, action="import")[0]
            self.assertEqual((log["detail"]["importer"], log["detail"]["counts"]["typed"]["visit"]), ("manual", 1))
        finally:
            db.close()

    def test_mixed_kinds_one_import_and_undo_removes_all(self):
        pid = self.mixed_pid
        pdf = b"%PDF-1.4\n% A fictional letter\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"
        up = self.upload(pid, "letter.pdf", pdf)
        before = count(pid)
        r = self.items([{"kind": "visit", "date": "2026-07-01", "title": "Annual exam", "where": "Lakeside Clinic (fictional)"},
                        {"kind": "home", "date": "2026-07-02", "steps": "5000"},
                        {"kind": "document", "date": "2026-07-01", "title": "Referral letter", "doctype": "letter",
                         "where": "Lakeside Clinic (fictional)", "upload": str(up), "file": "letter.pdf"}],
                       rows=[{"date": "2026-07-01", "test": "WBC", "value": "6.2", "unit": "K/uL"}], pid=pid)
        self.assertEqual(r.status_code, 200, r.text)
        iid = r.json()["import_id"]
        self.assertEqual(r.json()["counts"]["documents"], 1)
        after = count(pid)
        self.assertEqual(after["imports"], before["imports"] + 1)   # one import for all of it
        data = self.data(pid)
        doc = next(e for e in data["events"] if e["k"] == "letter")
        d = data["details"][str(doc["id"])]
        self.assertEqual((d["type"], d["ext"]), ("file", "pdf"))
        got = self.robin.get("/" + d["url"])
        self.assertEqual((got.status_code, got.content, got.headers["content-type"]), (200, pdf, "application/pdf"))
        # the file is kept sealed on disk, as the upload stored it
        db = dbm.connect()
        try:
            path = crypto.dec(db.execute("SELECT path FROM documents WHERE import_id=?", (iid,)).fetchone()["path"])
        finally:
            db.close()
        self.assertTrue(os.path.isfile(path))
        with open(path, "rb") as f:
            raw = f.read()
        self.assertNotIn(b"%PDF", raw)
        self.assertNotIn(b"fictional letter", raw)
        self.assertTrue(crypto.file_is_encrypted(path))
        self.assertTrue(self.watch(pid))
        r = self.robin.post(f"/api/profiles/{pid}/imports/{iid}/undo", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(count(pid), before)
        self.assertIsNone(self.watch(pid))   # the steps went with it
        self.assertFalse(os.path.exists(path))   # and the document's file, its upload discarded
        self.assertFalse(os.path.isdir(uploads.folder(pid, up)))
        self.assertEqual(self.robin.get("/" + d["url"]).status_code, 404)

    def test_the_history_counts_what_was_typed(self):
        """#517: a typed problem, appointment and payment were no records in Import history (they're in the profile's
        lists, not rows), a vitals entry 3 results in 3 tests: each is counted as the save counted it, by kind; one saved
        before the save's counts were kept counts the lists' entries."""
        db = dbm.connect()
        try:
            pid = prof.create_profile(db, "Robin history (fictional)", owner_id=db.execute("SELECT id FROM users WHERE username='robin'").fetchone()[0])
        finally:
            db.close()
        typed = [{"kind": "problem", "name": "Iron deficiency anemia", "status": "Active", "since": "2026-05-04"},
                 {"kind": "upcoming", "date": "2026-12-03", "time": "10:00", "title": "Follow-up visit"},
                 {"kind": "payment", "date": "2026-06-01", "payee": "Lakeside Clinic (fictional)", "amount": "60"},
                 {"kind": "vitals", "date": "2026-05-04", "time": "09:35", "bp": "118/76", "hr": "72"}]
        ids = []
        for it in typed:
            r = self.items([it], pid=pid)
            self.assertEqual(r.status_code, 200, r.text)
            ids.append(r.json()["import_id"])
        hist = {e["id"]: e for e in self.robin.get(f"/api/profiles/{pid}/imports").json()["imports"]}
        self.assertEqual([hist[i]["counts"]["typed"] for i in ids], [{"problem": 1}, {"upcoming": 1}, {"payment": 1}, {"vitals": 1}])
        db = dbm.connect()
        try:
            with dbm.transaction(db):
                db.execute("UPDATE imports SET note=NULL WHERE profile_id=?", (pid,))
        finally:
            db.close()
        hist = {e["id"]: e for e in self.robin.get(f"/api/profiles/{pid}/imports").json()["imports"]}
        self.assertEqual([hist[i]["counts"].get(k) for i, k in zip(ids, ("problems", "upcoming", "payments"))], [1, 1, 1])
        r = self.robin.post(f"/api/profiles/{pid}/imports/{ids[1]}/undo", headers=H)   # undone, it's still counted
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["counts"].get("upcoming"), 1)

    def test_typed_records_already_there_are_flagged_and_left_out(self):
        pid = self.dup_pid
        visit = {"kind": "visit", "date": "2026-08-01", "time": "10:00", "title": "Office visit", "where": "Lakeside Clinic (fictional)"}
        claim = {"kind": "claim", "date": "2026-08-01", "provider": "Lakeside Clinic (fictional)", "title": "Office visit", "billed": "200"}
        steps = {"kind": "home", "date": "2026-08-01", "steps": "4000"}
        row = {"date": "2026-08-01", "test": "WBC", "value": "6.0", "unit": "K/uL"}
        self.assertEqual(self.items([visit, claim, steps], rows=[row], pid=pid).status_code, 200)
        before = count(pid)
        problem = {"kind": "problem", "name": "Hypertension", "since": "2026-08-01"}
        r = self.items([dict(visit, title="  office VISIT "), claim, steps, problem], rows=[row], save=False, pid=pid)
        j = r.json()
        self.assertEqual([x["duplicate"] for x in j["items"]], [True, True, True, False])   # the same, typed again
        self.assertEqual([x["duplicate"] for x in j["rows"]], [True])
        self.assertEqual(j["duplicates"], 4)
        r = self.items([visit, claim, steps], rows=[row], pid=pid)
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("in this profile already", r.json()["detail"])
        self.assertEqual(count(pid), before)
        r = self.items([visit, claim, problem], pid=pid)   # the new one is saved; the others are left out
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["skipped"], r.json()["counts"]["typed"]), (2, {"problem": 1}))
        self.assertEqual(count(pid)["events"], before["events"])
        r = self.items([visit], pid=pid, keep_duplicates=True)   # saved again, as asked
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(count(pid)["events"], before["events"] + 1)

    def test_the_same_records_saved_twice_at_once_are_saved_once(self):
        """Two tabs (or a double click) save the same visit and result at once, both checked before either saved: one
        is saved, the other refused saying why, with nothing saved (#592)."""
        import threading
        from unittest import mock
        from server import routes_manual
        pid = self.dup_pid
        visit = {"kind": "visit", "date": "2026-08-12", "title": "Race visit", "where": "Lakeside Clinic (fictional)"}
        row = {"date": "2026-08-12", "test": "Ferritin", "value": "41", "unit": "ng/mL", "range": "15-150"}
        before = count(pid)
        both, real, calls, lock = threading.Barrier(2, timeout=10), routes_manual._duplicates, [0], threading.Lock()

        def checked(*a):   # each save's first check waits for the other's: both see the profile without them
            with lock:
                calls[0] += 1
                first = calls[0] <= 2
            got = real(*a)
            if first:
                both.wait()
            return got
        out = {}
        tabs = {n: self.login("robin") for n in (1, 2)}
        with mock.patch.object(routes_manual, "_duplicates", checked):
            ts = [threading.Thread(target=lambda n=n: out.__setitem__(n, self.items([visit], rows=[row], pid=pid, c=tabs[n])))
                  for n in tabs]
            [t.start() for t in ts]
            [t.join(30) for t in ts]
        codes = sorted(out[n].status_code for n in tabs)
        self.assertEqual(codes, [200, 409], [out[n].text for n in tabs])
        refused = next(out[n] for n in tabs if out[n].status_code == 409)
        self.assertIn("Nothing was saved", refused.json()["detail"])
        after = count(pid)
        self.assertEqual((after["imports"], after["events"] - before["events"]), (before["imports"] + 1, 3), after)   # one save's: the visit, the lab day, its result
        self.assertEqual(after["results"], before["results"] + 1)
        r = self.items([visit], rows=[row], pid=pid)   # saved again, it's found already there, as ever
        self.assertEqual(r.status_code, 400, r.text)

    def test_an_import_saved_while_the_same_rows_are_typed_in(self):
        """An upload's review saved by the import worker while the same results are typed in: the worker's duplicates
        are found under the write lock, so the rows typed in wait for it, are found there, and nothing is saved twice
        (#592)."""
        import threading
        from unittest import mock
        from server import jobs, preview
        db = dbm.connect()
        try:
            uid = db.execute("SELECT id FROM users WHERE username='robin'").fetchone()[0]
            pid = prof.create_profile(db, "Robin at once (fictional)", owner_id=uid)
            up = uploads.create(db, pid, None)
            with open(CSV, "rb") as f:
                uploads.add_file(db, pid, up, "labs.csv", io.BytesIO(f.read()))
            uploads.finish(db, pid, up)
        finally:
            db.close()
        jid = self.robin.post(f"/api/profiles/{pid}/uploads/{up}/import", json={}, headers=H).json()["id"]
        end = time.time() + 20
        while self.robin.get(f"/api/profiles/{pid}/jobs/{jid}").json()["state"] != "preview":
            self.assertLess(time.time(), end)
            time.sleep(0.05)
        typed, real = {}, preview.find

        def found(*a):   # the worker's save, once it has looked for duplicates: the same rows typed in, meanwhile
            got = real(*a)
            if threading.current_thread() is not threading.main_thread() and "t" not in typed:
                typed["t"] = threading.Thread(target=lambda: typed.__setitem__("r", self.post(TYPED, pid=pid)))
                typed["t"].start()
                typed["t"].join(1.5)   # done by now unless it waits for the worker's write lock
            return got
        with mock.patch.object(jobs.preview, "find", found):
            self.assertEqual(self.robin.post(f"/api/profiles/{pid}/jobs/{jid}/confirm", json={}, headers=H).status_code, 202)
            while (j := self.robin.get(f"/api/profiles/{pid}/jobs/{jid}").json())["state"] not in ("done", "failed"):
                self.assertLess(time.time(), end + 20, j)
                time.sleep(0.05)
            typed["t"].join(20)
        self.assertEqual(j["state"], "done", j)
        self.assertIn(typed["r"].status_code, (400, 409), typed["r"].text)   # found there: nothing typed saved
        self.assertEqual(count(pid)["imports"], 1, "the rows typed in weren't saved again as an import of their own")

    def test_bad_fields_refused_with_reasons_and_nothing_saved(self):
        pid = self.refuse_pid
        before = count(pid)
        r = self.items([{"kind": "visit", "date": "2026-13-40", "title": "Visit"},
                        {"kind": "claim", "date": "2026-02-01", "provider": "Clinic (fictional)", "billed": "lots"},
                        {"kind": "vitals", "date": "2026-02-01", "bp": "120", "hr": "720"},
                        {"kind": "med", "instructions": "daily"},
                        {"kind": "stay", "admit": "2026-02-05", "discharge": "2026-02-01"},
                        {"kind": "document", "date": "2026-02-01", "title": "Scan", "upload": "999999", "file": "scan.pdf"},
                        {"kind": "telepathy", "date": "2026-02-01"},
                        {"kind": "visit", "date": "2026-02-02", "title": "Fine visit"},
                        {"kind": "visit", "date": "2026-02-02", "title": "Fine visit"}],
                       rows=[{"date": "2026-02-01", "test": "WBC", "value": "6"}], pid=pid)
        self.assertEqual(r.status_code, 400, r.text)
        j = r.json()
        fields = [x["fields"] for x in j["items"]]
        self.assertEqual(fields[0], {"date": "has a date that couldn't be read (2026-13-40)"})
        self.assertEqual(fields[1], {"billed": "has an amount billed that isn't an amount (lots)"})
        self.assertEqual(set(fields[2]), {"bp", "hr"})
        self.assertEqual(set(fields[3]), {"name", "start"})
        self.assertEqual(fields[4], {"discharge": "has a discharge before the admission"})
        self.assertEqual(set(fields[5]), {"upload"})
        self.assertIn("kind", fields[6])
        self.assertEqual([x["ok"] for x in j["items"]], [False] * 7 + [True, False])
        self.assertEqual(j["items"][8]["problems"], ["repeats record 8"])
        self.assertTrue(j["detail"].startswith("Record 1 (visit) has a date that couldn't be read") and j["detail"].endswith("Nothing was saved."), j["detail"])
        self.assertEqual(len(j["problems"]), 8)
        self.assertEqual(count(pid), before)   # not even the good ones
        self.assertIsNone(self.watch(pid))
        r = self.items([{"kind": "visit", "date": "03/04/2026", "title": "Which day?"}], pid=pid)
        self.assertIn("YYYY-MM-DD", r.json()["items"][0]["fields"]["date"])
        r = self.items([{"kind": "visit", "note": "x" * 5000, "date": "2026-02-02"}], pid=pid)
        self.assertIn("longer than", r.json()["items"][0]["fields"]["note"])
        r = self.items([{"kind": "visit"}, {"kind": "vitals", "temp_unit": "F", "weight_unit": "lb"}], pid=pid)   # empty records
        self.assertEqual((r.status_code, r.json()["detail"]), (400, "There's nothing to save: type a result first."))
        self.assertEqual(self.items([{"kind": "visit", "date": "2026-01-01"}] * 101, pid=pid).status_code, 422)
        self.assertEqual(count(pid), before)

    def test_an_immunization_needs_its_vaccine_and_is_found_again(self):   # #179
        pid = self.refuse_pid
        r = self.items([{"kind": "immunization", "date": "2026-02-01", "dose": "2"}], save=False, pid=pid)
        self.assertEqual(r.json()["items"][0]["fields"], {"vaccine": "needs the vaccine's name"})
        imm = {"kind": "immunization", "date": "2026-02-01", "vaccine": "Tdap vaccine", "where": "Corner Pharmacy (fictional)"}
        self.assertEqual(self.items([imm], pid=pid).status_code, 200)
        r = self.items([dict(imm, vaccine=" tdap  VACCINE ")], save=False, pid=pid)
        self.assertEqual([x["duplicate"] for x in r.json()["items"]], [True])   # the same, typed again

    # ---------------------------------------------------------------- readings typed in at home, and height (#224)

    def home_profile(self, name, phone=None):
        """A profile of its own; with phone, a phone receiver's summary as its watch file (fictional readings)."""
        db = dbm.connect()
        try:
            owner = db.execute("SELECT id FROM users WHERE username='robin'").fetchone()[0]
            path = None
            if phone is not None:
                path = os.path.join(TMP, "phone", f"{name}.json")
                os.makedirs(os.path.dirname(path), exist_ok=True)
                crypto.write_file(path, json.dumps(phone).encode())
            return prof.create_profile(db, f"Robin {name} (fictional)", owner_id=owner, watch_file=path)
        finally:
            db.close()

    def person(self, pid):
        return self.robin.get(f"/api/profiles/{pid}/person").json()

    def test_home_readings_join_the_phones_told_apart(self):
        # the phone's summary (watch/receiver.py's shape): a reading at 07:30 New York time, which is 11:30 UTC in May
        phone = {"tz": "America/New_York", "metrics": [], "exercise": [],
                 "bp_readings": [["2026-05-03T11:30:00+00:00", 131, 84, 70, "Cuff (fictional)", "Health Connect"]],
                 "temp_readings": [["2026-05-03T12:00:00+00:00", 36.8, None, "Apple Health"]]}
        pid = self.home_profile("home readings", phone)
        items = [{"kind": "home_bp", "date": "2026-05-04", "time": "07:30", "sys": "128", "dia": "82", "pulse": "64"},
                 {"kind": "home_glucose", "date": "2026-05-04", "time": "07:45", "glucose": "5.8", "glucose_unit": "mmol/L", "meal": "before"},
                 {"kind": "home_temp", "date": "2026-05-04", "time": "08:00", "temp": "99.1", "temp_unit": "F"},
                 {"kind": "home_spo2", "date": "2026-05-04", "time": "08:05", "spo2": "97 %"},
                 {"kind": "height", "date": "2026-05-04", "height": "172", "height_unit": "cm"}]
        j = self.items(items, save=False, pid=pid).json()
        self.assertEqual([x["ok"] for x in j["items"]], [True] * 5, j["items"])
        self.assertEqual([x["label"] for x in j["items"]], ["Blood pressure at home", "Blood glucose at home", "Temperature at home",
                                                           "Blood oxygen at home", "Height"])
        self.assertEqual([x["summary"] for x in j["items"]], [
            "2026-05-04 07:30 · 128/82 mmHg · pulse 64", "2026-05-04 07:45 · glucose 5.8 mmol/L · before a meal",
            "2026-05-04 08:00 · 99.1 °F", "2026-05-04 08:05 · SpO2 97%", "2026-05-04 · height 172 cm · into About this person's height history"])
        r = self.items(items, pid=pid)
        self.assertEqual(r.status_code, 200, r.text)
        c = r.json()["counts"]
        self.assertEqual((c["typed"], c["readings"], c["heights_set"]),
                         ({"height": 1, "home_bp": 1, "home_glucose": 1, "home_spo2": 1, "home_temp": 1}, 4, 1))
        w = self.watch(pid)
        # in the lists the phone's readings are in, beside them, "Typed in" where the phone's name their app
        self.assertEqual(w["bp_readings"], [["2026-05-03T11:30:00+00:00", 131, 84, 70, "Cuff (fictional)", "Health Connect"],
                                            ["2026-05-04T07:30:00", 128, 82, 64, None, "Typed in"]])
        self.assertEqual(w["temp_readings"], [["2026-05-03T12:00:00+00:00", 36.8, None, "Apple Health"],
                                              ["2026-05-04T08:00:00", 37.28, None, "Typed in"]])   # kept in °C
        self.assertEqual(w["glucose_readings"], [["2026-05-04T07:45:00", 104.49, None, "Typed in", "before"]])   # kept in mg/dL
        self.assertEqual(w["glucose_conv"]["si"][0], "mmol/L")   # shown in mmol/L when chosen (#41)
        self.assertEqual(w["spo2_readings"], [["2026-05-04T08:05:00", 97, None, "Typed in"]])
        self.assertEqual(w["tz"], "America/New_York")   # the receiver's file as it was, the typed readings beside it
        # the height went into About this person's history, not the import
        self.assertEqual(self.person(pid)["heights"], [{"date": "2026-05-04", "cm": 172, "unit": "cm"}])
        # the phone's own reading typed in (07:30 in New York, a minute off), and the same typed again: already there
        again = [{"kind": "home_bp", "date": "2026-05-03", "time": "07:31", "sys": "131", "dia": "84"}, items[0], items[4]]
        j = self.items(again, save=False, pid=pid).json()
        self.assertEqual([x["duplicate"] for x in j["items"]], [True, True, True])
        self.assertEqual(self.items(again, pid=pid).status_code, 400)
        # undone, the typed readings go; the phone's stay; the height stays in the history (a setting, not the import)
        iid = r.json()["import_id"]
        self.assertEqual(self.robin.post(f"/api/profiles/{pid}/imports/{iid}/undo", headers=H).status_code, 200)
        w = self.watch(pid)
        self.assertEqual((w["bp_readings"], w["temp_readings"], w.get("glucose_readings"), w.get("spo2_readings")),
                         (phone["bp_readings"], phone["temp_readings"], None, None))
        self.assertEqual(len(self.person(pid)["heights"]), 1)

    def test_a_weight_typed_on_a_day_the_phones_scale_has_is_shown_said_so(self):
        """#822: a weight typed in for a day the phone's scale has one was saved, and shown nowhere: the receiver's file
        won the merge, drawn, tabled and sent with the scale's, and the check line said nothing. Now the one typed is
        the day's (and a step count's), the check line says what it replaces and that undoing the save brings it back,
        whichever came first (an export of that day saved after it leaves it shown); typed again, the later; undone,
        the scale's again."""
        from server import measures, records, watch_store
        phone = {"tz": "America/Chicago", "exercise": [], "metrics": [
            {"key": "weight", "name": "Weight", "unit": "lb", "group": "Body", "pts": [["2026-10-20", 160.4], ["2026-10-21", 160.6]]},
            {"key": "steps", "name": "Steps", "unit": "steps", "group": "Activity", "pts": [["2026-10-20", 6000]]}]}
        pid = self.home_profile("scale day", phone)
        day = lambda key, d: dict(map(tuple, next(m["pts"] for m in self.watch(pid)["metrics"] if m["key"] == key))).get(d)   # noqa: E731
        typed = [{"kind": "home", "date": "2026-10-20", "weight": "175.0", "weight_unit": "lb", "steps": "7,000"},
                 {"kind": "home", "date": "2026-10-22", "weight": "79.5", "weight_unit": "kg"}]   # a day the scale hasn't
        j = self.items(typed, save=False, pid=pid).json()
        self.assertEqual([(x["ok"], x["duplicate"], x["notes"]) for x in j["items"]], [
            (True, False, ["replaces the smart scale's 160.4 lb for that day (undoing this save brings it back)",
                           "replaces the phone's 6,000 steps for that day (undoing this save brings it back)"]), (True, False, [])])
        r = self.items(typed, pid=pid)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((day("weight", "2026-10-20"), day("steps", "2026-10-20"), day("weight", "2026-10-21"), day("weight", "2026-10-22")),
                         (175.0, 7000, 160.6, 175.3), "the one typed shown on its day, the scale's on the others")
        w = self.watch(pid)
        self.assertEqual(next(m.get("typed") for m in w["metrics"] if m["key"] == "weight"), ["2026-10-20", "2026-10-22"])
        db = dbm.connect()
        try:   # what's sent and printed (the visit summary's, a share link's weights) is the same, said as typed in
            weights = measures._weights(measures.context(db, pid, {}, {}))
            # an Apple Health export of that day saved after it: the one typed is still the one shown
            with dbm.transaction(db):
                iid = records.store(db, pid, {"events": [], "labs": [], "details": {}, "costs": []}, "upload", fmt="apple-health")
                watch_store.save_import(db, pid, {"metrics": [{"key": "weight", "name": "Weight", "unit": "lb", "group": "Body",
                                                               "pts": [["2026-10-20", 161.0], ["2026-10-23", 161.2]]}], "exercise": []}, import_id=iid)
        finally:
            db.close()
        self.assertEqual([(d, round(kg / 0.45359237, 1), where) for d, kg, where in weights],
                         [("2026-10-20", 175.0, "typed in"), ("2026-10-21", 160.6, "smart scale"), ("2026-10-22", 175.3, "typed in")])
        self.assertEqual((day("weight", "2026-10-20"), day("weight", "2026-10-23")), (175.0, 161.2))
        # typed again for that day: the later, said so
        again = [{"kind": "home", "date": "2026-10-20", "weight": "174.2", "weight_unit": "lb"}]
        self.assertEqual(self.items(again, save=False, pid=pid).json()["items"][0]["notes"],
                         ["replaces the weight typed in before for that day (175 lb) (undoing this save brings it back)"])
        r2 = self.items(again, pid=pid)
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertEqual(day("weight", "2026-10-20"), 174.2)
        # undone, newest first: the first typed, then the export's for that day (it came after the scale's), then the scale's
        self.assertEqual(self.robin.post(f"/api/profiles/{pid}/imports/{r2.json()['import_id']}/undo", headers=H).status_code, 200)
        self.assertEqual(day("weight", "2026-10-20"), 175.0)
        self.assertEqual(self.robin.post(f"/api/profiles/{pid}/imports/{r.json()['import_id']}/undo", headers=H).status_code, 200)
        self.assertEqual((day("weight", "2026-10-20"), day("steps", "2026-10-20"), day("weight", "2026-10-22")), (160.4, 6000, None),
                         "the phone's file wins over an export, as before")
        self.assertIsNone(next(m.get("typed") for m in self.watch(pid)["metrics"] if m["key"] == "weight"))

    def test_a_reading_typed_again_for_its_time_corrects_it(self):
        pid = self.home_profile("corrected")
        first = {"kind": "home_bp", "date": "2026-06-01", "time": "21:10", "sys": "141", "dia": "92"}
        r1 = self.items([first], pid=pid)
        self.assertEqual(r1.status_code, 200, r1.text)
        fixed = dict(first, sys="131", dia="82")
        j = self.items([fixed], save=False, pid=pid).json()
        self.assertEqual((j["items"][0]["duplicate"], j["items"][0]["notes"]),
                         (False, ["replaces the reading typed in before for that time (undoing this save brings it back)"]))
        r2 = self.items([fixed], pid=pid)
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertEqual(self.watch(pid)["bp_readings"], [["2026-06-01T21:10:00", 131, 82, None, None, "Typed in"]])   # once
        hist = self.robin.get(f"/api/profiles/{pid}/imports").json()["imports"]
        self.assertEqual([h["counts"].get("readings") for h in hist], [1, 1])   # the history says what each save brought
        self.assertEqual([(h["origin"], h["by"]) for h in hist], [("manual", "robin")] * 2)   # who typed it in, not "command line" (#325)
        db = dbm.connect()   # a typed-in import from before imports.created_by (or its account removed): no name, not "command line"
        db.execute("UPDATE imports SET created_by=NULL WHERE id=?", (r1.json()["import_id"],))
        db.close()
        hist = {h["id"]: h for h in self.robin.get(f"/api/profiles/{pid}/imports").json()["imports"]}
        self.assertEqual((hist[r1.json()["import_id"]]["by"], hist[r2.json()["import_id"]]["by"]), (None, "robin"))
        self.robin.post(f"/api/profiles/{pid}/imports/{r2.json()['import_id']}/undo", headers=H)
        self.assertEqual(self.watch(pid)["bp_readings"], [["2026-06-01T21:10:00", 141, 92, None, None, "Typed in"]])
        self.robin.post(f"/api/profiles/{pid}/imports/{r1.json()['import_id']}/undo", headers=H)
        self.assertIsNone(self.watch(pid))

    def test_home_readings_checked_with_clear_reasons(self):
        pid = self.home_profile("checked")
        r = self.items([
            {"kind": "home_bp", "date": "2026-06-01", "sys": "300", "dia": "80"},
            {"kind": "home_bp", "date": "2026-06-01", "sys": "80", "dia": "120", "pulse": "900"},
            {"kind": "home_bp", "date": "2026-06-01", "dia": "80"},
            {"kind": "home_glucose", "date": "2026-06-01", "glucose": "104", "glucose_unit": "mmol/L"},
            {"kind": "home_glucose", "date": "2026-06-01", "glucose": "5.8", "glucose_unit": "mg/dL"},
            {"kind": "home_glucose", "date": "2026-06-01", "glucose": "5000", "meal": "dinner"},
            {"kind": "home_temp", "date": "2026-06-01", "temp": "37.2", "temp_unit": "F"},
            {"kind": "home_temp", "date": "2026-06-01", "temp": "60", "temp_unit": "C"},
            {"kind": "home_spo2", "date": "2026-06-01", "spo2": "120"},
            {"kind": "home_spo2", "date": "2090-01-01", "spo2": "98"},
            {"kind": "height", "date": "2026-06-01", "height": "400", "height_unit": "cm"},
            {"kind": "height", "date": "2026-06-01", "height": "tall"},
        ], pid=pid)
        self.assertEqual(r.status_code, 400, r.text)
        f = [x["fields"] for x in r.json()["items"]]
        self.assertEqual(f[0], {"sys": "has a systolic pressure that doesn't look right (300; expected 50 to 260)"})
        self.assertEqual(f[1], {"dia": "is as high as the systolic or higher (120, 80): the systolic is the higher number, as in 120/80",
                                "pulse": "has a pulse that doesn't look right (900; expected 20 to 250)"})
        self.assertEqual(f[2], {"sys": "needs the systolic pressure (the higher number)"})
        self.assertEqual(f[3], {"glucose": "has a glucose reading that looks like mg/dL, not mmol/L (104): choose mg/dL, or check the number"})
        self.assertEqual(f[4], {"glucose": "has a glucose reading that looks like mmol/L, not mg/dL (5.8): choose mmol/L, or check the number"})
        self.assertEqual(set(f[5]), {"glucose", "meal"})
        self.assertIn("expected 20 to 600", f[5]["glucose"])
        self.assertEqual(f[6], {"temp": "has a temperature that looks like °C, not °F (37.2): choose °C, or check the number"})
        self.assertEqual(f[7], {"temp": "has a temperature that doesn't look right (60 °C; expected 30 to 45)"})
        self.assertEqual(f[8], {"spo2": "has an oxygen saturation (SpO2) that doesn't look right (120%; expected 50 to 100)"})
        self.assertEqual(f[9], {"date": "has a date after today (2090-01-01)"})
        self.assertIn("from 20 to 272 cm", f[10]["height"])
        self.assertEqual(f[11], {"height": "has a height that isn't a number (tall)"})
        self.assertIsNone(self.watch(pid))
        self.assertEqual(self.person(pid)["heights"], [])

    def test_a_height_typed_in_goes_into_the_history(self):
        pid = self.home_profile("height")
        self.assertEqual(self.robin.put(f"/api/profiles/{pid}/person", json={"birthDate": "1990-02-01"}, headers=H).status_code, 200)
        before = count(pid)["imports"]
        r = self.items([{"kind": "height", "date": "2026-03-01", "height": "5 ft 8 in", "height_unit": "in"}], pid=pid)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["import_id"], r.json()["counts"]["heights_set"]), (None, 1))   # a height alone isn't an import
        self.assertEqual(count(pid)["imports"], before)
        self.assertEqual(self.person(pid)["heights"], [{"date": "2026-03-01", "cm": 172.72, "unit": "ftin"}])
        j = self.items([{"kind": "height", "date": "2026-03-01", "height": "68", "height_unit": "in"},
                        {"kind": "height", "date": "2026-03-02", "height": "173", "height_unit": "cm"}], save=False, pid=pid).json()
        self.assertEqual([x["duplicate"] for x in j["items"]], [True, False])
        r = self.items([{"kind": "height", "date": "2026-03-01", "height": "173", "height_unit": "cm"}], save=False, pid=pid)
        self.assertEqual(r.json()["items"][0]["notes"], ["replaces the height of that day in About this person (5 ft 8 in)"])
        r = self.items([{"kind": "height", "date": "1989-12-01", "height": "50"}], pid=pid)
        self.assertEqual((r.status_code, r.json()["items"][0]["fields"]),
                         (400, {"date": "has a date before the birth date in About this person (1990-02-01)"}))

    def test_viewer_cant_save_records(self):
        before = count(self.pid)
        item = [{"kind": "visit", "date": "2026-04-01", "title": "Visit typed by a viewer"}]
        self.assertEqual(self.items(item, c=self.kim).status_code, 403)
        self.assertEqual(self.items(item, save=False, c=self.kim).status_code, 403)
        r = self.robin.post(f"/api/profiles/{self.pid}/manual", json={"items": item, "save": True})   # no X-Sanguity
        self.assertEqual(r.status_code, 403)
        self.assertEqual(count(self.pid), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
