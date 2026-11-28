#!/usr/bin/env python3
"""Costs' tax-year report (server/taxreport.py, routes_costs.py, #1136): the payments of a tax year, exactly Costs'
(counted once, a refund as it is), at the year's boundaries and with a tax year starting in April; the zip's
summary.pdf, payments.csv and receipts/, their names (no payee or file names, nothing outside the zip's own folders)
and a CSV cell that would be a formula kept as text; roles, the year checked, the download logged. Fictional data only.

  python3 tests/test_taxreport.py
"""
import csv, io, json, os, shutil, sys, tempfile, unittest, zipfile, zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-tax-test-")
BUILD = os.path.join(TMP, "build")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=BUILD,
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_CORRECTIONS=os.path.join(TMP, "config", "corrections.json"))
for d in ("records", "keys", "build", "config"):
    os.makedirs(os.path.join(TMP, d))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, crypto, db as dbm, main, taxreport   # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites"}


def pay(d, payee, amount, group="Medical bills", account="Visa"):
    return {"d": d, "payee": payee, "group": group, "amount": amount, "account": account, "memo": "", "est": False}


EXPORT = [pay("2025-12-31", "Lakeside Clinic", 40.0),           # the day before 2026
          pay("2026-01-01", "Lakeside Clinic", 60.0),           # its first day
          pay("2026-03-31", "Harbor Imaging", 250.0, account="HSA Card"),   # the day before an April tax year
          pay("2026-04-01", "=HYPERLINK(\"x\")", 15.5),         # its first day; a payee a spreadsheet would run
          pay("2026-06-10", "Harbor Imaging", -25.0),           # a refund, as it is
          pay("2026-12-31", "Example Health", 300.0, group="Health insurance premiums"),   # the last day of 2026
          pay("2027-03-31", "Lakeside Clinic", 80.0)]           # the April tax year's last day
BUILT = {"built": "2026-11-01T00:00:00+00:00", "source": "fictional", "events": [], "details": {}, "labs": [], "costs": [],
         "upcoming": [], "problems": [], "milestones": [], "outOfPocket": {"payments": EXPORT, "exported": "2027-04-01"}}


def client():
    return TestClient(main.app, base_url="http://testserver")


class TaxReport(unittest.TestCase):
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
        # a payment typed in with its receipt, and the same $60 typed again (counted once: the export's)
        up = cls.robin.post(f"/api/profiles/{cls.pid}/uploads", headers=H).json()["id"]
        cls.robin.put(f"/api/profiles/{cls.pid}/uploads/{up}/files", params={"path": "Robin's receipt.pdf"}, content=b"%PDF-1.4 fictional receipt", headers=H)
        cls.robin.post(f"/api/profiles/{cls.pid}/uploads/{up}/finish", headers=H)
        r = cls.robin.post(f"/api/profiles/{cls.pid}/manual", json={"save": True, "items": [
            {"kind": "payment", "date": "2026-05-03", "payee": "Lakeside Clinic", "amount": "$45.00", "hsa": "FSA", "upload": str(up), "file": "Robin's receipt.pdf"},
            {"kind": "payment", "date": "2026-01-02", "payee": "Lakeside Clinic", "amount": "60"}]}, headers=H)
        assert r.status_code == 200, r.text

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

    def preview(self, year, status=200, c=None):
        r = (c or self.robin).get(f"/api/profiles/{self.pid}/costs/tax-report", params={"year": year, "preview": 1})
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def start(self, month):
        r = self.robin.put(f"/api/profiles/{self.pid}/settings/costs.taxStart", json={"value": month}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)

    def test_1_calendar_years(self):
        self.start(1)
        p = self.preview(2026)
        # 60 + 250 + 15.50 - 25 + 300 + 45 (the typed 60 counted once): the boundaries' 40 and 80 are other years'
        self.assertEqual((p["from"], p["to"], p["label"], p["count"], p["total"], p["receipts"], p["missing"]),
                         ("2026-01-01", "2026-12-31", "2026", 6, 645.5, 1, 5))
        self.assertEqual((self.preview(2025)["total"], self.preview(2027)["total"]), (40.0, 80.0))
        # (#1163) the premium apart from what was paid for care, with both totals
        self.assertEqual((p["care"], p["careCount"], p["premiums"], p["premiumCount"]), (345.5, 5, 300.0, 1))
        pdf = taxreport.pdf_bytes(taxreport.plan(dbm.connect(), self.pid, 2026, 1))
        text = zlib.decompress(pdf.split(b"stream\n", 1)[1].split(b"\nendstream", 1)[0])
        self.assertIn(b"Paid for care: $345.50 in 5 payments", text)
        self.assertIn(b"Insurance premiums: $300.00 in 1 payment", text)

    def test_2_a_tax_year_from_april(self):
        self.start(4)
        p = self.preview(2026)
        self.assertEqual((p["from"], p["to"], p["label"], p["count"], p["total"]), ("2026-04-01", "2027-03-31", "2026-27", 5, 415.5))
        p = self.preview(2025)
        self.assertEqual((p["from"], p["to"], p["count"], p["total"]), ("2025-04-01", "2026-03-31", 3, 350.0))
        self.assertEqual(taxreport.years({"outOfPocket": {"payments": EXPORT}}, 4), [2026, 2025])
        self.start(1)

    def test_3_the_zip(self):
        r = self.kim.get(f"/api/profiles/{self.pid}/costs/tax-report", params={"year": 2026})   # a viewer may
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["content-type"], "application/zip")
        self.assertIn('filename="sanguity-tax-year-2026.zip"', r.headers["content-disposition"])
        z = zipfile.ZipFile(io.BytesIO(r.content))
        names = z.namelist()
        self.assertEqual(names[:2], ["summary.pdf", "payments.csv"])
        self.assertEqual(names[2:], ["receipts/004-2026-05-03.pdf"], "named by its row and date, not the file's name")
        self.assertTrue(all(not n.startswith("/") and ".." not in n for n in names))
        self.assertEqual(z.read("receipts/004-2026-05-03.pdf"), b"%PDF-1.4 fictional receipt")
        self.assertTrue(z.read("summary.pdf").startswith(b"%PDF-"))
        rows = list(csv.reader(io.StringIO(z.read("payments.csv").decode("utf-8-sig"))))
        self.assertEqual(rows[0], ["Row", "Date", "Paid to", "For", "Amount", "HSA/FSA", "From", "Receipt file"])
        self.assertEqual([r[1] for r in rows[1:]], ["2026-01-01", "2026-03-31", "2026-04-01", "2026-05-03", "2026-06-10", "2026-12-31"])
        self.assertEqual(rows[3][2], "'=HYPERLINK(\"x\")", "a formula kept as text")
        self.assertEqual(rows[4][4:], ["45.00", "FSA", "Typed in by you", "receipts/004-2026-05-03.pdf"])
        self.assertEqual(rows[2][5], "HSA")
        self.assertEqual(rows[5][4], "-25.00", "a refund as it is")
        self.assertEqual(sum(float(r[4]) for r in rows[1:]), 645.5, "the preview's total")
        db = dbm.connect()
        try:
            logged = [json.loads(crypto.dec(x[0])) for x in db.execute("SELECT detail FROM activity WHERE action='export' AND profile_id=?", (self.pid,))]
            self.assertIn({"format": "tax", "year": 2026}, logged)
        finally:
            db.close()

    def test_4_refused(self):
        self.preview(1800, 400)
        r = client().get(f"/api/profiles/{self.pid}/costs/tax-report", params={"year": 2026, "preview": 1})
        self.assertEqual(r.status_code, 401)
        r = self.robin.put(f"/api/profiles/{self.pid}/settings/costs.taxStart", json={"value": 13}, headers=H)
        self.assertEqual(r.status_code, 400)
        r = self.kim.put(f"/api/profiles/{self.pid}/settings/costs.taxStart", json={"value": 4}, headers=H)
        self.assertEqual(r.status_code, 403, "a viewer can't change it")
        self.assertEqual(self.preview(2031)["count"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
