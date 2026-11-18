#!/usr/bin/env python3
"""Out-of-pocket costs without a finance-app export (server/oop.py, server/routes_costs.py, issue #55): where each
amount came from (typed in, a finance export, a claim's share); payments typed in with an HSA/FSA tag and a receipt
kept as a document; payments matched to claims by amount, date and provider, or by hand; the same payment arriving
two ways counted once; marks made on the Costs tab (roles, the CSRF check, the activity log, encrypted at rest);
and "oop" never stored from an import. Fictional data only.

  python3 tests/test_costs.py
"""
import json, os, shutil, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-costs-test-")
BUILD = os.path.join(TMP, "build")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=BUILD,
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_CORRECTIONS=os.path.join(TMP, "config", "corrections.json"))
for d in ("records", "keys", "build", "config"):
    os.makedirs(os.path.join(TMP, d))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, crypto, db as dbm, main, oop, records   # noqa: E402
from tests.whole_records import whole   # noqa: E402  records with every record's details (#217)
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites"}
CLINIC, IMAGING = "Lakeside Clinic (fictional)", "Harbor Imaging (fictional)"


def claim(eid, key, t, title, prov, owe):
    return ({"id": eid, "t": t + "T12:00:00", "k": "claim", "title": title, "sub": prov, "key": key},
            {"id": eid, "d": t, "ins": "Example Health (fictional)", "billed": owe * 4, "paid": owe * 3, "owe": owe,
             "status": "Paid", "kind": "medical", "cat": "Doctor visits", "prov": prov, "title": title})


CLAIMS = [claim(10, "uhc:CL-1", "2026-03-02", "Office visit", CLINIC, 60.0),
          claim(11, "uhc:CL-2", "2026-04-10", "MRI brain", IMAGING, 250.0),
          claim(12, "uhc:CL-3", "2026-05-01", "Follow-up visit", CLINIC, 40.0)]
EXPORT = [   # a finance app's export (build.py's payments.json): no keys of their own
    {"d": "2026-02-01", "payee": "Example Health (fictional)", "group": "Health insurance premiums", "amount": 300.0, "account": "Checking", "memo": "", "est": False},
    {"d": "2026-03-20", "payee": "LAKESIDE CLINIC 0423", "group": "Medical bills", "amount": 60.0, "account": "HSA Card", "memo": "Statement", "est": False},
    {"d": "2026-04-25", "payee": "Harbor Imaging", "group": "Medical bills", "amount": 100.0, "account": "Visa", "memo": "Plan payment 1", "est": False},
]
BUILT = {"built": "2026-11-01T00:00:00+00:00", "source": "fictional",
         "events": [e for e, _ in CLAIMS], "details": {}, "labs": [], "costs": [c for _, c in CLAIMS],
         "upcoming": [], "problems": [], "milestones": [], "outOfPocket": {"payments": EXPORT, "exported": "2026-10-31"}}


def client():
    return TestClient(main.app, base_url="http://testserver")


class Costs(unittest.TestCase):
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

    def data(self):
        return whole(self.robin, self.pid)

    def oop(self):
        d = self.data()
        return d, {p["i"]: p for p in d["oop"]["payments"]}, {c["id"]: c for c in d["oop"]["claims"]}

    def claim_id(self, d, key):
        return next(e["id"] for e in d["events"] if e.get("key") == key)

    def mark(self, status=200, c=None, **body):
        r = (c or self.robin).put(f"/api/profiles/{self.pid}/costs/mark", json=body, headers=H)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def upload(self, name, content):
        up = self.robin.post(f"/api/profiles/{self.pid}/uploads", headers=H).json()["id"]
        r = self.robin.put(f"/api/profiles/{self.pid}/uploads/{up}/files", params={"path": name}, content=content, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.robin.post(f"/api/profiles/{self.pid}/uploads/{up}/finish", headers=H).status_code, 200)
        return up

    def type_in(self, items, status=200):
        r = self.robin.post(f"/api/profiles/{self.pid}/manual", json={"items": items, "save": True}, headers=H)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    # ---------------------------------------------------------------- in order: export, typed, marks

    def test_1_export_and_claims(self):
        d, pays, claims = self.oop()
        c1, c2, c3 = (self.claim_id(d, k) for k in ("uhc:CL-1", "uhc:CL-2", "uhc:CL-3"))
        self.assertEqual({p["src"] for p in pays.values()}, {"export"})
        self.assertTrue(all(p["key"].startswith("pay:h:") for p in pays.values()), "export payments get content keys")
        # the $60 from the HSA card paid the office visit: matched by amount, date and a word of the provider's name
        self.assertEqual((pays[1]["claim"], pays[1]["match"], pays[1]["hsa"], pays[1]["hsaFrom"]), (c1, "auto", "HSA", "account"))
        self.assertEqual((claims[c1]["by"], claims[c1]["counted"]), ([1], 0.0), "a paid share isn't counted again")
        # $100 of a $250 share isn't the same amount: not matched, so the share is still counted
        self.assertEqual((pays[2]["claim"], claims[c2]["counted"], claims[c3]["counted"]), (None, 250.0, 40.0))
        self.assertIsNone(pays[0]["claim"], "a premium is never matched to a claim")
        self.assertIsNone(pays[0]["hsa"])
        # the rows themselves are served as imported
        self.assertEqual(d["outOfPocket"]["payments"], EXPORT)

    def test_2_typed_with_receipt_and_hsa(self):
        r = self.type_in([{"kind": "payment", "date": "2026-05-03", "payee": "Lakeside Clinic", "amount": "$40.00", "hsa": "IRA"}], 400)
        self.assertIn("account type", json.dumps(r))
        up = self.upload("receipt.pdf", b"%PDF-1.4 fictional receipt")
        out = self.type_in([
            {"kind": "payment", "date": "2026-05-03", "payee": "Lakeside Clinic", "amount": "$40.00", "hsa": "FSA",
             "account": "FSA card", "upload": str(up), "file": "receipt.pdf"},
            # the same $60 the export already has, typed in again: counted once
            {"kind": "payment", "date": "2026-03-21", "payee": "Lakeside Clinic", "amount": "60", "hsa": "HSA"}])
        self.assertEqual([x["ok"] for x in out["items"]], [True, True], out)
        d, pays, claims = self.oop()
        typed = {i: p for i, p in pays.items() if p["src"] == "typed"}
        self.assertEqual(len(typed), 2)
        rows = d["outOfPocket"]["payments"]
        fsa = next(i for i in typed if rows[i]["amount"] == 40.0)
        again = next(i for i in typed if rows[i]["amount"] == 60.0)
        # the receipt is a document of its own, which the payment names
        rec = next(e for e in d["events"] if e["id"] == typed[fsa]["receipt"])
        self.assertEqual((rec["k"], rec["title"]), ("document", "Receipt: Lakeside Clinic"))
        url = d["details"][str(rec["id"])]["url"]
        self.assertRegex(url, rf"^api/profiles/{self.pid}/documents/")
        self.assertEqual(self.robin.get("/" + url).content, b"%PDF-1.4 fictional receipt")
        c3 = self.claim_id(d, "uhc:CL-3")
        self.assertEqual((typed[fsa]["hsa"], typed[fsa]["hsaFrom"], typed[fsa]["claim"], typed[fsa]["match"]), ("FSA", "typed", c3, "auto"))
        self.assertEqual(claims[c3]["counted"], 0.0)
        # typed after the export: the export's is counted, this one points at it
        self.assertEqual((typed[again]["dup"], pays[1]["dup"], typed[again]["claim"]), (1, None, None))

    def test_3_marks(self):
        d, pays, claims = self.oop()
        c1, c2, c3 = (self.claim_id(d, k) for k in ("uhc:CL-1", "uhc:CL-2", "uhc:CL-3"))
        harbor = pays[2]["key"]
        # viewers can't; unknown targets are refused; the CSRF header is needed
        self.mark(403, c=self.kim, payment=harbor, hsa="HSA")
        self.mark(404, payment="pay:h:0000000000000000", hsa="HSA")
        self.mark(400, payment=harbor, event=c2, hsa="HSA")
        self.mark(400, event=c2, claim=c2)
        self.assertEqual(self.robin.put(f"/api/profiles/{self.pid}/costs/mark", json={"payment": harbor, "hsa": "HSA"}).status_code, 403)
        # the plan payment paid the MRI, by hand: its share is no longer counted
        self.assertEqual(self.mark(payment=harbor, claim=c2), {"mark": {"claim": True}})
        _, pays, claims = self.oop()
        self.assertEqual((pays[2]["claim"], pays[2]["match"], claims[c2]["by"], claims[c2]["counted"], claims[c2]["paid"]), (c2, "marked", [2], 0.0, 100.0))
        # the HSA payment paid no claim: the office visit's share is counted again
        self.mark(payment=pays[1]["key"], claim=0)
        _, pays, claims = self.oop()
        self.assertEqual((pays[1]["claim"], claims[c1]["counted"]), (None, 60.0))
        self.mark(payment=pays[1]["key"], claim=-1)   # matched again
        _, pays, claims = self.oop()
        self.assertEqual((pays[1]["claim"], pays[1]["marked"]), (c1, False), "a mark left with nothing is removed")
        # tags: a claim's share, and a payment's account read overruled
        self.mark(event=c2, hsa="FSA")
        self.mark(payment=pays[1]["key"], hsa="none")
        _, pays, claims = self.oop()
        self.assertEqual((claims[c2]["hsa"], pays[1]["hsa"], pays[1]["hsaFrom"]), ("FSA", None, "marked"))
        self.mark(payment=pays[1]["key"], hsa="auto")
        # two payments, not one: both counted
        typed_again = next(i for i, p in pays.items() if p["dup"] == 1)
        self.mark(payment=pays[typed_again]["key"], same=False)
        _, pays, _ = self.oop()
        self.assertIsNone(pays[typed_again]["dup"])
        # the activity log says what was marked, never an amount or a payee
        log = self.robin.get(f"/api/profiles/{self.pid}/activity?action=edit").json()
        entries = [e["detail"] for e in (log["entries"] if isinstance(log, dict) else log) if (e.get("detail") or {}).get("action") == "cost"]
        self.assertTrue(entries, log)
        self.assertNotIn("Harbor", json.dumps(entries))
        self.assertIn({"action": "cost", "of": "payment", "claim": "claim"}, entries)
        # encrypted at rest; the target is a keyed hash
        db = dbm.connect()
        for r in db.execute("SELECT target, target_key, value FROM cost_marks"):
            self.assertTrue(r["target_key"].startswith(crypto.FIELD) and r["value"].startswith(crypto.FIELD))
            self.assertNotIn("uhc:", r["target"])
        db.close()

    def test_4_oop_never_stored_from_an_import(self):
        db = dbm.connect()
        with dbm.transaction(db):
            records.store(db, self.pid, {"source": "export zip", "events": [], "oop": {"payments": [{"i": 0, "src": "forged"}]}}, "dataset")
        db.close()
        d = self.data()
        self.assertNotIn("forged", json.dumps(d["oop"]))
        self.assertIn("oop", records.SERVED)

    def test_5_matching_rules(self):
        """oop.annotate on its own: dates outside the window, other providers, refunds and premiums aren't matched."""
        data = {"imports": {"1": {"format": "sanguity-data"}},
                "events": [{"id": 1, "t": "2026-01-10T12:00:00", "k": "claim", "title": "Visit", "key": "a:1"},
                           {"id": 2, "t": "2026-01-10T12:00:00", "k": "claim", "title": "Visit", "key": "a:2"}],
                "costs": [{"id": 1, "d": "2026-01-10", "owe": 25.0, "prov": "Pine Ridge Clinic"},
                          {"id": 2, "d": "2026-01-10", "owe": 25.0, "prov": "Other Pediatrics"}],
                "outOfPocket": {"payments": [
                    {"d": "2025-12-01", "payee": "Pine Ridge", "amount": 25.0, "group": "Medical bills"},     # before the claim
                    {"d": "2026-01-12", "payee": "Pine Ridge", "amount": -25.0, "group": "Medical bills"},    # a refund
                    {"d": "2026-01-12", "payee": "Pine Ridge", "amount": 25.0, "group": "Health insurance premiums"},
                    {"d": "2026-01-15", "payee": "Pine Ridge Clinic", "amount": 25.0, "group": "Medical bills"}]}}
        db = dbm.connect()
        out = oop.annotate(db, 999999, data, [1, 1, 1, 1])["oop"]
        db.close()
        self.assertEqual([p["claim"] for p in out["payments"]], [None, None, None, 1], "only the one to the same provider, after it")
        self.assertEqual({c["id"]: c["counted"] for c in out["claims"]}, {1: 0.0, 2: 25.0})
        self.assertEqual(oop.words("Lakeside Clinic (fictional)"), {"lakeside"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
