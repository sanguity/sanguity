#!/usr/bin/env python3
"""Lab results typed in on the import page (server/routes_manual.py, issue #27): typed rows become lab results with
their ranges, flags and canonical names; the same rows typed and in an imported CSV get the same keys; rows that
can't be read are refused with their reasons and nothing is saved; viewers can't save; the save is one import,
logged with its counts. Fictional data only.

  python3 tests/test_manual.py
"""
import os, sys, tempfile, time, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-manual-test-")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "no-build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"))
os.makedirs(os.path.join(TMP, "records"))
os.makedirs(os.path.join(TMP, "keys"))
sys.path.insert(0, ROOT)

import io, logging   # noqa: E402

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, activity, db as dbm, main, profiles as prof, uploads   # noqa: E402

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
        r = self.robin.get(f"/api/profiles/{pid}/data")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
