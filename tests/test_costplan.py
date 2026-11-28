#!/usr/bin/env python3
"""Plan years and bills on the Costs tab (server/costplan.py, routes_costs.py): the deductible and out-of-pocket sums
per plan year typed in, at its boundaries, with family amounts, and per calendar year outside them; plan years that
overlap or don't add up refused (#1127). A provider's bill lined up against the matching claim's share, worked out
(provider, date of service, amount) or chosen, with the payments matched to that claim and its file; a claim imported
later offered (#1128). Roles, the activity log, encryption at rest, and the export's "costPlans" and "costBills".
Fictional data only.

  python3 tests/test_costplan.py
"""
import json, os, shutil, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-costplan-test-")
BUILD = os.path.join(TMP, "build")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=BUILD,
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_CORRECTIONS=os.path.join(TMP, "config", "corrections.json"))
for d in ("records", "keys", "build", "config"):
    os.makedirs(os.path.join(TMP, d))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, costplan, crypto, db as dbm, export, main, records   # noqa: E402
from tests.whole_records import whole   # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites"}
CLINIC = "Lakeside Clinic (fictional)"


def claim(eid, t, owe, ded=None, title="Office visit"):
    c = {"id": eid, "d": t, "ins": "Example Health (fictional)", "billed": 400.0, "paid": 200.0, "owe": owe,
         "status": "Paid", "kind": "medical", "cat": "Doctor visits", "prov": CLINIC, "title": title}
    if ded is not None:
        c["ded"] = ded
    return ({"id": eid, "t": t + "T12:00:00", "k": "claim", "title": title, "sub": CLINIC, "key": f"fict:CL-{eid}"}, c)


CLAIMS = [claim(10, "2025-06-30", 90.0, 90.0),     # the day before the plan year: 2025, a calendar year
          claim(11, "2025-07-01", 120.0, 100.0),   # its first day
          claim(12, "2026-01-15", 55.0),           # no deductible part: out of pocket only
          claim(13, "2026-03-01", -20.0, -20.0, "Adjustment"),   # an adjustment counts as it is
          claim(14, "2026-06-30", 300.0, 250.0),   # its last day
          claim(15, "2026-07-01", 40.0, 0.0)]      # the next one's first day
BUILT = {"built": "2026-11-01T00:00:00+00:00", "source": "fictional", "events": [e for e, _ in CLAIMS], "details": {},
         "labs": [], "costs": [c for _, c in CLAIMS], "upcoming": [], "problems": [], "milestones": [],
         "outOfPocket": {"payments": [   # paid the claim of 2025-07-01 (oop.py matches it)
             {"d": "2025-07-20", "payee": "Lakeside Clinic", "group": "Medical bills", "amount": 120.0, "account": "Visa", "memo": "", "est": False}]}}


def client():
    return TestClient(main.app, base_url="http://testserver")


class PlanYears(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(BUILD, "data.json"), "w", encoding="utf-8") as f:
            json.dump(BUILT, f)
        cls.ctx = client().__enter__()
        db = dbm.connect()
        uids = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        cls.pid = db.execute("SELECT id FROM profiles WHERE linked_build=1").fetchone()[0]
        for who, role in (("robin", "owner"), ("kim", "viewer")):
            db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, ?)", (uids[who], cls.pid, role))
        db.close()
        cls.robin, cls.kim = cls.login("robin"), cls.login("kim")

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        shutil.rmtree(TMP, True)

    @staticmethod
    def login(name):
        c = client()
        r = c.post("/api/login", json={"username": name, "password": PW[name]}, headers=H)
        assert r.status_code == 200, r.text
        return c

    def years(self):
        d = whole(self.robin, self.pid)
        ids = {e["key"]: e["id"] for e in d["events"]}
        return d["oop"], {y["start"]: y for y in d["oop"]["years"]}, ids

    def send(self, method, path, body=None, status=200, c=None):
        r = (c or self.robin).request(method, f"/api/profiles/{self.pid}/costs/{path}", json=body, headers=H)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def test_1_calendar_years_with_none_typed(self):
        o, ys, ids = self.years()
        self.assertEqual(o["plans"], [])
        self.assertEqual(sorted(ys), ["2025-01-01", "2026-01-01"])
        y25, y26 = ys["2025-01-01"], ys["2026-01-01"]
        self.assertEqual((y25["towardDeductible"], y25["outOfPocket"], y25["deductible"], y25["plan"]), (190.0, 210.0, None, None))
        # 55 with no deductible part counts toward out of pocket; the adjustment's -20 counts as it is
        self.assertEqual((y26["towardDeductible"], y26["outOfPocket"], y26["noSplit"]), (230.0, 375.0, 1))
        self.assertEqual(len(y26["claims"]), 4)

    def test_2_plan_years_at_their_boundaries_and_family(self):
        self.send("POST", "plans", {"start": "2025-07-01", "deductible": 3000, "oop": 7500, "familyDeductible": 6000, "familyOop": 15000}, 201)
        o, ys, ids = self.years()
        p = o["plans"][0]
        self.assertEqual((p["start"], p["end"], p["deductible"], p["oop"], p["familyDeductible"], p["familyOop"]),
                         ("2025-07-01", "2026-06-30", 3000.0, 7500.0, 6000.0, 15000.0))
        y = ys["2025-07-01"]
        self.assertEqual(sorted(y["claims"]), sorted(ids[f"fict:CL-{n}"] for n in (11, 12, 13, 14)))   # first and last day in
        self.assertEqual((y["towardDeductible"], y["outOfPocket"], y["noSplit"], y["plan"]), (330.0, 455.0, 1, p["id"]))
        self.assertEqual((y["familyDeductible"], y["familyOop"]), (6000.0, 15000.0))
        # outside it, by calendar year: the day before (2025) and the day after (2026)
        self.assertEqual((ys["2025-01-01"]["claims"], ys["2025-01-01"]["outOfPocket"]), ([ids["fict:CL-10"]], 90.0))
        self.assertEqual((ys["2026-01-01"]["claims"], ys["2026-01-01"]["towardDeductible"]), ([ids["fict:CL-15"]], 0.0))
        self.assertEqual(list(ys), ["2026-01-01", "2025-07-01", "2025-01-01"], "newest first")

    def test_3_refused(self):
        for body, words in (({"start": "2026-03-01", "deductible": 1000, "oop": 5000}, "overlaps"),
                            ({"start": "2024-07-02", "deductible": 1000, "oop": 5000}, "overlaps"),   # ends 2025-07-01
                            ({"start": "2027-01-01", "deductible": -1, "oop": 5000}, "from $0"),
                            ({"start": "2027-01-01", "deductible": 2000, "oop": 1000}, "at least the deductible"),
                            ({"start": "2027-01-01", "deductible": 0, "oop": 0, "familyDeductible": 5, "familyOop": 1}, "family out-of-pocket"),
                            ({"start": "2027-02-30", "deductible": 0, "oop": 0}, "isn't a date"),
                            ({"start": "2027-01-01", "oop": 10}, "Type the deductible")):
            with self.subTest(body):
                self.assertIn(words, self.send("POST", "plans", body, 400)["detail"])
        self.send("POST", "plans", {"start": "2026-07-01", "deductible": 0, "oop": 0}, 403, c=self.kim)
        r = self.robin.post(f"/api/profiles/{self.pid}/costs/plans", json={"start": "2026-07-01", "deductible": 0, "oop": 0})
        self.assertEqual(r.status_code, 403, "the CSRF header is needed")
        self.send("PUT", "plans/99999", {"start": "2030-07-01", "deductible": 0, "oop": 0}, 404)
        # the next plan year, right after: no overlap. Changed and removed
        self.send("POST", "plans", {"start": "2026-07-01", "deductible": 1500, "oop": 4000}, 201)
        o, ys, ids = self.years()
        nxt = next(p for p in o["plans"] if p["start"] == "2026-07-01")
        self.assertEqual(ys["2026-07-01"]["claims"], [ids["fict:CL-15"]])
        self.assertNotIn("2026-01-01", ys, "every claim is in a plan year now")
        self.assertIn("overlaps", self.send("PUT", f"plans/{nxt['id']}", {"start": "2026-06-01", "deductible": 0, "oop": 0}, 400)["detail"])
        out = self.send("PUT", f"plans/{nxt['id']}", {"start": "2026-07-01", "deductible": 2000, "oop": 4000})
        self.assertEqual(next(p for p in out["oop"]["plans"] if p["id"] == nxt["id"])["deductible"], 2000.0)
        out = self.send("DELETE", f"plans/{nxt['id']}")
        self.assertEqual([p["start"] for p in out["oop"]["plans"]], ["2025-07-01"])
        db = dbm.connect()
        try:
            logged = [json.loads(crypto.dec(r[0]))
                      for r in db.execute("SELECT detail FROM activity WHERE action='edit' AND profile_id=?", (self.pid,))]
            self.assertIn({"action": "cost", "of": "plan", "change": "remove"}, logged)
            body = db.execute("SELECT body FROM cost_plans WHERE profile_id=?", (self.pid,)).fetchone()[0]
            self.assertNotIn("2025-07-01", body, "encrypted at rest")
        finally:
            db.close()

    def test_4_in_the_export(self):
        db = dbm.connect()
        try:
            raw = records.assemble(db, self.pid, raw=True)
            self.assertEqual([p["start"] for p in raw["costPlans"]], ["2025-07-01"])
            got = export.check_parts({"costPlans": raw["costPlans"]})
            self.assertEqual([b for _, b in got["costPlans"]],
                             [{"start": "2025-07-01", "deductible": 3000.0, "oop": 7500.0, "familyDeductible": 6000.0, "familyOop": 15000.0}])
            with self.assertRaises(ValueError):
                export.check_parts({"costPlans": [{"start": "2025-07-01", "deductible": 10, "oop": 5}]})
            # loaded back: the same plan year is here already, so it's left
            with dbm.transaction(db):
                self.assertEqual(costplan._restore(db, "cost_plans", self.pid, got["costPlans"], export._who), (0, 1))
        finally:
            db.close()

    # ---------------------------------------------------------------- bills (#1128)

    def bills(self, out=None):
        o = (out.get("records") or out)["oop"] if out else whole(self.robin, self.pid)["oop"]   # with a file: the records
        return {b["id"]: b for b in o["bills"]}

    def newest(self, out):
        return max(self.bills(out).values(), key=lambda b: b["id"])

    def test_5_bills_lined_up(self):
        o, ys, ids = self.years()
        # billed 400 two days after the claim of 2025-06-30 and one after 2025-07-01, both billed 400: the nearer
        out = self.send("POST", "bills", {"service": "2025-07-02", "provider": "Lakeside Clinic", "amount": 400, "due": "2025-08-01"}, 201)
        b1 = self.newest(out)
        pay = next(p["i"] for p in out["oop"]["payments"])
        self.assertEqual((b1["claim"], b1["match"], b1["owe"], b1["paid"], b1["payments"], b1["due"]),
                         (ids["fict:CL-11"], "auto", 120.0, 120.0, [pay], "2025-08-01"))
        # its share's amount first: 300 on the claim of 2026-06-30
        b2 = self.newest(self.send("POST", "bills", {"service": "2026-06-29", "provider": "LAKESIDE CLINIC", "amount": 300}, 201))
        self.assertEqual((b2["claim"], b2["owe"], b2["paid"]), (ids["fict:CL-14"], 300.0, 0.0))
        # another provider: no claim yet
        b3 = self.newest(self.send("POST", "bills", {"service": "2026-06-30", "provider": "Harbor Imaging", "amount": 80}, 201))
        self.assertEqual((b3["claim"], b3["match"], b3["none"]), (None, None, False))
        # chosen by hand, and "no claim yet"
        bs = self.bills(self.send("PUT", f"bills/{b3['id']}", {"service": "2026-06-30", "provider": "Harbor Imaging", "amount": 80, "claim": ids["fict:CL-12"]}))
        self.assertEqual((bs[b3["id"]]["claim"], bs[b3["id"]]["match"], bs[b3["id"]]["owe"]), (ids["fict:CL-12"], "marked", 55.0))
        bs = self.bills(self.send("PUT", f"bills/{b2['id']}", {"service": "2026-06-29", "provider": "Lakeside Clinic", "amount": 300, "claim": 0}))
        self.assertEqual((bs[b2["id"]]["claim"], bs[b2["id"]]["none"]), (None, True))
        bs = self.bills(self.send("PUT", f"bills/{b2['id']}", {"service": "2026-06-29", "provider": "Lakeside Clinic", "amount": 300, "claim": -1}))
        self.assertEqual(bs[b2["id"]]["claim"], ids["fict:CL-14"], "worked out again")
        for body, words in (({"service": "2026-01-01", "provider": " ", "amount": 5}, "who the bill is from"),
                            ({"service": "2026-01-01", "provider": "X", "amount": -5}, "from $0"),
                            ({"service": "2026-13-01", "provider": "X", "amount": 5}, "isn't a date"),
                            ({"service": "2026-01-01", "provider": "X", "amount": 5, "due": "soon"}, "due date"),
                            ({"service": "2026-01-01", "provider": "X", "amount": 5, "claim": 99999}, "claims"),
                            ({"service": "2026-01-01", "provider": "X", "amount": 5, "file_import": 99999}, "file")):
            with self.subTest(body):
                self.assertIn(words, self.send("POST", "bills", body, 400)["detail"])
        self.send("POST", "bills", {"service": "2026-01-01", "provider": "X", "amount": 5}, 403, c=self.kim)
        self.send("DELETE", "bills/99999", status=404)
        self.send("DELETE", f"bills/{b3['id']}")
        self.assertNotIn(b3["id"], self.bills())

    def test_6_a_file_and_a_later_claim(self):
        up = self.robin.post(f"/api/profiles/{self.pid}/uploads", headers=H).json()["id"]
        self.robin.put(f"/api/profiles/{self.pid}/uploads/{up}/files", params={"path": "bill.pdf"}, content=b"%PDF-1.4 fictional bill", headers=H)
        self.assertEqual(self.robin.post(f"/api/profiles/{self.pid}/uploads/{up}/finish", headers=H).status_code, 200)
        r = self.robin.post(f"/api/profiles/{self.pid}/manual", json={"save": True, "keep_duplicates": True, "items": [
            {"kind": "document", "date": "2027-02-01", "title": "Bill: Harbor Imaging", "upload": str(up), "file": "bill.pdf"}]}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        b = self.newest(self.send("POST", "bills", {"service": "2027-02-01", "provider": "Harbor Imaging (fictional)", "amount": 500,
                                                    "file_import": r.json()["import_id"]}, 201))
        d = whole(self.robin, self.pid)
        doc = next(e for e in d["events"] if e["id"] == b["file"])
        self.assertEqual((doc["k"], doc["title"], b["claim"]), ("document", "Bill: Harbor Imaging", None))
        self.assertEqual(self.robin.get("/" + d["details"][str(doc["id"])]["url"]).content, b"%PDF-1.4 fictional bill")
        # a claim imported later is offered
        r = self.robin.post(f"/api/profiles/{self.pid}/manual", json={"save": True, "items": [
            {"kind": "claim", "date": "2027-02-02", "provider": "Harbor Imaging", "title": "MRI", "billed": "500", "paid": "380", "owe": "120"}]}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        x = self.bills()[b["id"]]
        self.assertEqual((x["match"], x["owe"], x["file"]), ("auto", 120.0, b["file"]))
        # changed: the file kept; then dropped
        body = {"service": "2027-02-01", "provider": "Harbor Imaging", "amount": 500}
        self.assertEqual(self.bills(self.send("PUT", f"bills/{b['id']}", body))[b["id"]]["file"], b["file"])
        self.assertIsNone(self.bills(self.send("PUT", f"bills/{b['id']}", {**body, "drop_file": True}))[b["id"]]["file"])
        db = dbm.connect()
        try:
            self.assertNotIn("Harbor", db.execute("SELECT body FROM cost_bills WHERE id=?", (b["id"],)).fetchone()[0], "encrypted at rest")
            raw = records.assemble(db, self.pid, raw=True)
            got = export.check_parts({"costBills": raw["costBills"]})
            self.assertEqual(len(got["costBills"]), len(raw["costBills"]))
            self.assertEqual(got["costBills"][0][1]["service"], "2025-07-02")
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
