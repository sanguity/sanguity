#!/usr/bin/env python3
"""The same lab result from two sources (#96, server/crossmatch.py): the match rule (units, the date-only
tolerance, nothing on a different value); a CSV file imported and then the same results pulled by a connected source
(the fake SMART on FHIR server, tests/fakefhir) are flagged in the sync's preview with both sources named; confirming
shows the result once with both sources, in Trends' series and in the drawer's rows; undoing the link shows both
again; undoing either import leaves the other; linking from the drawer; "not the same" stops the flag; viewers can't
decide; decisions are encrypted and logged. Fictional data only.

  python3 tests/test_crossmatch.py
"""
import io, json, os, sqlite3, sys, tempfile, time, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-crossmatch-test-")
ORIGIN = "http://sanguity.test:8730"
RETURN = ORIGIN + "/connect/return"
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "no-build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"), PUBLIC_ORIGINS=ORIGIN, CONNECT_ALLOW_HTTP="1",
                  SANGUITY_CORRECTIONS=os.path.join(TMP, "none", "corrections.json"))
os.makedirs(os.path.join(TMP, "records"))
os.makedirs(os.path.join(TMP, "keys"))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, config, crossmatch as cm, crypto, db as dbm, main, profiles as prof, records, smart, sync, uploads   # noqa: E402
from server.importers import common   # noqa: E402
from tests.whole_records import whole   # noqa: E402  records with every record's details (#217)
from tests.fakefhir import FakeFHIR   # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites"}

# the fake server's fictional patient has, on 4 March 2025 at 09:45 -06:00: Hemoglobin 12.1 g/dL, Platelet Count
# 455 10*3/uL and LDL Cholesterol 162 mg/dL. The same lab's printout typed into a spreadsheet, with dates only and
# other units, as a CSV: the first two are the same results; the LDL value differs (a typo), so it isn't one.
CSV = ("Date,Test,Result,Units,Reference Range,Flag,Panel\n"
       "03/04/2025,Hemoglobin,121,g/L,135-175,L,CBC\n"
       "03/04/2025,Platelet Count,455,x10E9/L,150-400,H,CBC\n"
       "03/04/2025,LDL Cholesterol,126,mg/dL,<130,,Lipids\n")


def S(name, unit, loinc=None, imp=1, key=None):
    return {"name": name, "unit": unit, "imp": imp, **({"loinc": loinc} if loinc else {}), **({"key": key} if key else {})}


def P(t, v, shown=None):
    return [t, float(v) if shown is None or common.value_number(str(v)) is not None else None, None, None, "", str(shown if shown is not None else v)]


class Rule(unittest.TestCase):
    """crossmatch.match() on its own."""

    def m(self, sa, pa, sb, pb):
        return cm.match(sa, pa, sb, pb)

    def test_same_value_same_unit(self):
        self.assertTrue(self.m(S("Hemoglobin", "g/dL", "718-7"), P("2025-03-04T09:45:00", 12.1),
                               S("Hemoglobin", "g/dL", "718-7", imp=2), P("2025-03-04T09:45:30", "12.10")))

    def test_unit_conversions_exact_only(self):
        hgb = lambda u, imp=1: S("Hemoglobin", u, "718-7", imp)
        t = "2025-03-04T09:45:00"
        self.assertTrue(self.m(hgb("g/L"), P(t, 121), hgb("g/dL", 2), P(t, 12.1)), "g/L to g/dL")
        self.assertTrue(self.m(hgb("gm/dL"), P(t, 12.1), hgb("g/dl", 2), P(t, 12.1)), "spellings")
        plt = lambda u, imp=1: S("Platelet Count", u, "777-3", imp)
        self.assertTrue(self.m(plt("x10E9/L"), P(t, 455), plt("10*3/uL", 2), P(t, 455)), "10^9/L is 10^3/uL")
        self.assertTrue(self.m(plt("K/uL"), P(t, 455), plt("Thou/cmm", 2), P(t, 455)))
        glu = lambda u, imp=1: S("Glucose", u, "2345-7", imp)
        self.assertTrue(self.m(glu("mmol/L"), P(t, 5.5), glu("umol/L", 2), P(t, 5500)), "mmol to umol")
        self.assertTrue(self.m(glu("mg/dL"), P(t, 99), glu("mg/L", 2), P(t, 990)), "mg/dL to mg/L")
        self.assertFalse(self.m(glu("mg/dL"), P(t, 99), glu("mmol/L", 2), P(t, 5.5)), "needs a molar mass: never")
        na = lambda u, imp=1: S("Sodium, Blood", u, None, imp)
        self.assertTrue(self.m(na("mEq/L"), P(t, 140), na("mmol/L", 2), P(t, 140)), "monovalent ions, as unit_for")
        k = lambda u, imp=1: S("Calcium", u, None, imp)
        self.assertFalse(self.m(k("mEq/L"), P(t, 2.5), k("mmol/L", 2), P(t, 2.5)), "mEq is not mmol for others")
        self.assertFalse(self.m(hgb("%"), P(t, 12.1), hgb("g/dL", 2), P(t, 12.1)), "unrelated units")

    def test_different_value_never(self):
        t = "2025-03-04T09:45:00"
        a, b = S("LDL Cholesterol", "mg/dL", "13457-7"), S("LDL Cholesterol", "mg/dL", "13457-7", imp=2)
        self.assertFalse(self.m(a, P(t, 162), b, P(t, 126)))
        self.assertFalse(self.m(a, P(t, 162), b, P(t, 162.1)))
        self.assertFalse(self.m(a, P(t, 0.5, "<0.5"), b, P(t, 0.5)), "a comparator on one side only")
        self.assertTrue(self.m(a, P(t, 0.5, "<0.5"), b, P(t, 0.5, "< 0.50")))
        self.assertFalse(self.m(a, [t, None, None, None, "", "Yellow"], b, [t, None, None, None, "", "Yellow"]), "numbers only")

    def test_a_value_read_by_the_one_reader_of_a_number(self):
        """#874: each value read by common.value_number, its comparator by common.comparator, as every importer reads
        it (#870): a copy printed "4,5", "=<5", "≤ 5", "≥60" or "1.2E-3" was "not a number" to crossmatch's own reader
        (_NUM), so never possibly the same result as its copy printed "4.5", "<=5" or "0.0012", nor a drawer row printed
        so found by its result. Its number as imported first: a spreadsheet with semicolons' "1,250" is its 1.25."""
        t = "2025-03-04T09:45:00"
        a, b = S("Potassium", "mmol/L", "2823-3"), S("Potassium", "mmol/L", "2823-3", imp=2)
        pt = lambda x, shown: [t, x, None, None, "", shown]   # noqa: E731
        for (x, shown), (y, other) in (((4.5, "4,5"), (4.5, "4.5")), ((5, "=<5"), (5, "<=5")), ((5, "≤ 5"), (5, "<=5")),
                                       ((60, "≥60"), (60, ">= 60")), ((0.0012, "1.2E-3"), (0.0012, "0.0012")),
                                       ((1.25, "1,250"), (1.25, "1.25")), ((None, "4,5"), (4.5, "4.5")), ((None, "=<5"), (5, "<=5"))):
            with self.subTest(shown=shown, other=other):
                self.assertTrue(self.m(a, pt(x, shown), b, pt(y, other)), "possibly the same result")
        for (x, shown), (y, other) in (((5, "=<5"), (5, "<5")), ((5, "≤ 5"), (5, "5")), ((1.25, "1,250"), (1250, "1250")),
                                       ((None, "1,250"), (1250, "1250"))):
            with self.subTest(shown=shown, other=other):
                self.assertFalse(self.m(a, pt(x, shown), b, pt(y, other)), "another comparator, another number, or unread")
        # a drawer row printed "4,5" or "=<5" is found by its result, whose value as reported is written otherwise
        rows = {(1, t[:10]): [(7, 0, ["Potassium", "4,5", "mmol/L", "3.5-5.1", ""]), (7, 1, ["Sodium", "=<5", "mmol/L", "", ""])]}
        self.assertEqual(cm._row_of(rows, S("Potassium", "mmol/L"), pt(4.5, "4.5")), (7, 0))
        self.assertEqual(cm._row_of(rows, S("Sodium, Blood", "mmol/L"), pt(5, "<=5")), (7, 1))
        self.assertIsNone(cm._row_of(rows, S("Sodium, Blood", "mmol/L"), pt(5, "<5")), "another comparator: not its row")

    def test_time_to_the_minute_and_date_tolerance(self):
        a, b = S("Hemoglobin", "g/dL", "718-7"), S("Hemoglobin", "g/dL", "718-7", imp=2)
        self.assertFalse(self.m(a, P("2025-03-04T09:45:00", 12.1), b, P("2025-03-04T09:46:00", 12.1)), "another minute")
        self.assertTrue(self.m(a, P("2025-03-04T09:45:00-06:00", 12.1), b, P("2025-03-04T15:45:00+00:00", 12.1)), "one instant")
        self.assertFalse(self.m(a, P("2025-03-04T09:45:00-06:00", 12.1), b, P("2025-03-04T09:45:00+00:00", 12.1)))
        self.assertTrue(self.m(a, P("2025-03-04T09:45:00-06:00", 12.1), b, P("2025-03-04T09:45:00", 12.1)), "no offset: as written")
        self.assertTrue(self.m(a, P("2025-03-04T12:00:00", 12.1), b, P("2025-03-04T09:45:00-06:00", 12.1)), "a date only: that day")
        self.assertTrue(self.m(a, P("2025-03-04", 12.1), b, P("2025-03-04T18:10:00", 12.1)))
        self.assertFalse(self.m(a, P("2025-03-04T12:00:00", 12.1), b, P("2025-03-05T09:45:00", 12.1)), "another day")

    def test_test_identity(self):
        t = "2025-03-04T09:45:00"
        self.assertFalse(self.m(S("Hemoglobin", "g/dL", "718-7"), P(t, 12.1), S("Hemoglobin", "g/dL", "20509-6", imp=2), P(t, 12.1)),
                         "two codes that differ")
        self.assertTrue(self.m(S("Hemoglobin", "g/dL", "718-7"), P(t, 12.1), S("HGB", "g/dL", None, imp=2), P(t, 12.1)),
                        "a code on one side: the canonical series")
        self.assertTrue(self.m(S("Hemoglobin", "g/dL", key="loinc:718-7"), P(t, 12.1), S("hemoglobin", "g/dL", imp=2), P(t, 12.1)))
        self.assertFalse(self.m(S("Hemoglobin", "g/dL"), P(t, 12.1), S("Hematocrit", "g/dL", imp=2), P(t, 12.1)))
        self.assertFalse(self.m(S("Hemoglobin", "g/dL"), P(t, 12.1), S("Hemoglobin", "g/dL"), P(t, 12.1)), "the same import")

    def test_candidates_order_and_pairs(self):
        labs = [S("Hemoglobin", "g/dL", "718-7", imp=5) | {"pts": [P("2025-03-04T09:45:00", 12.1)]},
                S("Hemoglobin", "g/L", "718-7", imp=3) | {"pts": [P("2025-03-04", 121), P("2025-04-01", 130)]},
                S("Platelet Count", "K/uL", imp=4) | {"pts": [P("2025-03-04", 121)]}]
        self.assertEqual(cm.candidates(labs), [((1, 0), (0, 0))], "older import first; another test never")

    def test_the_kept_copy_carries_the_others_range_and_flag(self):
        """#781: linked as the same result, the copy kept has any range and flag the other has where it says none
        (ranges.copies), a range in the other's unit given in its own by the exact factor; its drawer row the same."""
        t = "2025-03-04T09:45:00"
        k, hgb = S("Potassium", "mmol/L"), S("Hemoglobin", "g/dL", "718-7")
        self.assertEqual(cm._carry(P(t, 3.2), k, [t, 3.2, 3.5, 5.1, "L", "3.2"], S("Potassium", "mmol/L", imp=2)),
                         [t, 3.2, 3.5, 5.1, "L", "3.2"], "none: the other's range and Low")
        self.assertEqual(cm._carry(P(t, 11.0, "11.0"), hgb, [t, 110, 120, 160, "L", "110"], S("Hemoglobin", "g/L", "718-7", 2)),
                         [t, 11.0, 12.0, 16.0, "L", "11.0"], "a range in g/L, given in g/dL")
        own = [t, 3.2, 3.4, 5.0, "", "3.2"]
        self.assertEqual(cm._carry(own, k, [t, 3.2, 3.5, 5.1, "L", "3.2"], k), own, "its own range kept, a Low against another not taken")
        kept = cm._carry([t, 133, None, None, "L", "133"], k, [t, 133, 135, 145, "", "133"], k)
        self.assertEqual(kept[2:5], [135, 145, "L"], "a flag in one, the range in the other")
        # #780: a one-sided range's edge goes with it (pts[7], after the source tag or null in its place), in another unit too
        trop, ng, src = S("Troponin I", "ng/mL"), S("Troponin I", "ng/L", imp=2), [t, 40, None, 40, "", "40", None, "<"]
        # (the other's flag as read, #839: its 40 against its "<40" is "H", which the kept copy's 0.04 against "<0.04" is too)
        self.assertEqual(cm._carry(P(t, 0.04, "0.04"), trop, src, ng), [t, 0.04, None, 0.04, "H", "0.04", None, "<"],
                         "a report's \"<40\" ng/L, given as \"<0.04\" ng/mL")
        own = [t, 0.04, None, 0.04, "", "0.04", "RCC"]
        self.assertEqual(cm._carry(own, trop, src, ng), own, "its own range (\"≤ 0.04\") kept, without the other's edge")
        crp = S("C-Reactive Protein", "mg/L")
        self.assertEqual(cm._carry(P(t, 5), crp, [t, 5, None, 5, "", "5", None, "<"], crp | {"imp": 2}), [t, 5, None, 5, "H", "5", None, "<"])
        self.assertEqual(cm._carry_row(["Hemoglobin", "11.0", "g/dL", "", ""], ["HGB", "110", "g/L", "120-160", "L"]),
                         ["Hemoglobin", "11.0", "g/dL", "120-160 g/L", "L"], "the drawer's row: a range in another unit said with it")
        row = ["Potassium", "3.2", "mmol/L", "3.5-5.1", ""]
        self.assertIs(cm._carry_row(row, ["Potassium", "3.2", "mmol/L", "", ""]), row, "nothing to carry")
        self.assertEqual(cm._carry_row(["Potassium", "3.2", "mmol/L"], ["K", "3.2", "mmol / L", "3.5-5.1", "L"]),
                         ["Potassium", "3.2", "mmol/L", "3.5-5.1", "L"])


class CrossSource(unittest.TestCase):
    """A file import and a connected source's sync bringing the same results."""

    @classmethod
    def setUpClass(cls):
        cls.no_thread = mock.patch.object(sync, "start", lambda: None)
        cls.no_thread.start()
        cls.ctx = TestClient(main.app, base_url="http://testserver").__enter__()
        db = dbm.connect()
        cls.uids = {n: accounts.create(db, n, is_admin=(n == "robin"), password=pw)[0] for n, pw in PW.items()}
        db.close()
        cls.robin, cls.kim = cls.login("robin"), cls.login("kim")
        db = dbm.connect()
        db.execute("UPDATE users SET totp_secret=? WHERE id=?", (crypto.enc("JBSWY3DPEHPK3PXP"), cls.uids["robin"]))
        db.close()
        cls.no_sleep = mock.patch.object(smart, "sleep", lambda s: None)
        cls.no_sleep.start()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        cls.no_thread.stop()
        cls.no_sleep.stop()

    @staticmethod
    def login(name):
        c = TestClient(main.app, base_url="http://testserver")
        r = c.post("/api/login", json={"username": name, "password": PW[name]}, headers=H)
        assert r.status_code == 200, r.text
        return c

    def setUp(self):
        self.fake = FakeFHIR(patients=["robin-example"]).start()
        self.addCleanup(self.fake.stop)
        db = dbm.connect()
        self.pid = prof.create_profile(db, "Robin (fictional)", owner_id=self.uids["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (self.uids["kim"], self.pid))
        db.close()

    # ---------------------------------------------------------------- helpers

    def until(self, jid, states, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            j = self.robin.get(f"/api/profiles/{self.pid}/jobs/{jid}").json()
            if j["state"] in states:
                return j
            time.sleep(0.05)
        self.fail(f"job {jid} never reached {states}")

    def confirm(self, j, **body):
        r = self.robin.post(f"/api/profiles/{self.pid}/jobs/{j['id']}/confirm", json=body, headers=H)
        self.assertEqual(r.status_code, 202, r.text)
        j = self.until(j["id"], ("done", "failed"))
        self.assertEqual(j["state"], "done", j)
        return j

    def import_csv(self, same=None):
        db = dbm.connect()
        try:
            up = uploads.create(db, self.pid, None)
            uploads.add_file(db, self.pid, up, "labs.csv", io.BytesIO(CSV.encode()))
            uploads.finish(db, self.pid, up)
        finally:
            db.close()
        r = self.robin.post(f"/api/profiles/{self.pid}/uploads/{up}/import", json={}, headers=H)
        self.assertEqual(r.status_code, 202, r.text)
        j = self.until(r.json()["id"], ("preview", "failed"))
        self.assertEqual(j["state"], "preview", j)
        return j

    def sync_preview(self):
        """Set up the fake source, connect the profile and make its first pull: the job, in preview."""
        cid = f"app-{self.fake.port}"
        self.fake.register_client(cid, [RETURN])
        r = self.robin.post("/api/connect/sources", json={"name": f"Fake {self.fake.port}", "vendor": "epic",
                                                         "issuer": self.fake.fhir_base, "client_id": cid, "auth": "public"}, headers=H)
        self.assertEqual(r.status_code, 201, r.text)
        sid = r.json()["id"]
        t = self.robin.post(f"/api/connect/sources/{sid}/test", headers=H)
        self.assertTrue(t.status_code == 200 and t.json()["ok"], t.text)
        url = self.robin.post(f"/api/profiles/{self.pid}/connect/start", json={"source": sid}, headers=H).json()["url"]
        back = self.fake.authorize(url, patient="pat-1")
        r = self.robin.post("/api/connect/return", headers=H, json={"code": back.code, "state": back.state,
                                                                     "error": back.error, "error_description": back.error_description})
        self.assertEqual(r.status_code, 200, r.text)
        db = dbm.connect()
        try:
            res = sync.pull(db, r.json()["connection"], None)
        finally:
            db.close()
        self.assertTrue(res["ok"], res)
        j = self.until(res["job"], ("preview", "failed"))
        self.assertEqual(j["state"], "preview", j)
        return j

    def file_then_sync(self, link_hgb=True):
        """The CSV saved, then the sync's first pull previewed (Hemoglobin linked there when asked) and saved."""
        csv_job = self.confirm(self.import_csv())
        j = self.sync_preview()
        refs = {x["name"]: x["ref"] for x in j["possible"]}
        self.confirm(j, same=[refs["Hemoglobin"]] if link_hgb else [])
        return csv_job["import_id"], self.until(j["id"], ("done",))["import_id"], j

    def data(self, c=None):
        return whole(c or self.robin, self.pid)

    @staticmethod
    def points(d, name):
        """[(import, time, value as reported, same)] of every shown point of the series with this name."""
        out = []
        for s in d["labs"]:
            if s["name"] == name:
                for n, p in enumerate(s["pts"]):
                    out.append((s["imp"], p[0], p[5], (s.get("same") or {}).get(str(n))))
        return out

    @staticmethod
    def rows(d, name):
        """[(import, labitem event id)] of every drawer row with this name."""
        out = []
        for e in d["events"]:
            det = d["details"].get(str(e["id"])) or {}
            if e["k"] == "labitem":
                out += [(e["imp"], e["id"]) for r in det.get("rows") or [] if r[0] == name]
        return out

    def undo_import(self, iid):
        r = self.robin.post(f"/api/profiles/{self.pid}/imports/{iid}/undo", headers=H)
        self.assertEqual(r.status_code, 200, r.text)

    # ---------------------------------------------------------------- the preview

    def test_sync_preview_flags_the_files_results(self):
        self.confirm(self.import_csv())
        j = self.sync_preview()
        by = {x["name"]: x for x in j["possible"]}
        self.assertEqual(set(by), {"Hemoglobin", "Platelet Count"}, "LDL differs (126 and 162), so it isn't flagged")
        h = by["Hemoglobin"]
        self.assertEqual((h["value"], h["unit"], h["t"]), ("12.1", "g/dL", "2025-03-04T09:45:00-06:00"))
        self.assertEqual((h["other"]["value"], h["other"]["unit"], h["other"]["t"]), ("121", "g/L", "2025-03-04T12:00:00"))
        self.assertEqual(h["other"]["label"], self.fake_label("csv"), "the source it would duplicate is named")
        self.assertTrue(j["label"], "and the source being imported (the job's label)")
        self.assertNotIn("key", h)
        self.assertNotIn("other_key", h)

    def test_file_preview_flags_a_synced_result(self):
        """The other way round: synced first, then the file's preview flags it."""
        self.confirm(self.sync_preview())
        j = self.import_csv()
        self.assertEqual({x["name"] for x in j["possible"]}, {"Hemoglobin", "Platelet Count"})
        self.assertEqual({x["other"]["label"] for x in j["possible"]}, {self.fake_label("fhir")})

    @staticmethod
    def fake_label(name):
        from server import importers
        return importers.get(name).LABEL

    # ---------------------------------------------------------------- shown once, with both sources

    def test_confirmed_at_the_preview_shows_once_with_both_sources(self):
        csv_iid, sync_iid, _ = self.file_then_sync()
        d = self.data()
        hgb = self.points(d, "Hemoglobin")
        self.assertEqual(len(hgb), 1, hgb)
        imp, t, shown, same = hgb[0]
        self.assertEqual((imp, shown), (csv_iid, "121"), "the older import's point stays")
        self.assertEqual([x["imp"] for x in same], [sync_iid], "and lists the other source")
        self.assertEqual([r[0] for r in self.rows(d, "Hemoglobin")], [csv_iid], "the drawer shows one row")
        kept = d["details"][str(self.rows(d, "Hemoglobin")[0][1])]
        self.assertEqual([x["imp"] for x in kept["same"]], [sync_iid], "the drawer's result lists the other source")
        (link,) = d["same"]
        self.assertTrue(link["found"] and link["same"])
        self.assertEqual(link["by"], "robin")
        self.assertEqual(len(self.points(d, "Platelet Count")), 2, "only what was confirmed is linked")
        self.assertEqual([x["a"]["name"] for x in d["possible"]], ["Platelet Count"], "the other is still flagged")
        self.assertEqual({x["a"]["imp"] for x in d["possible"]} | {x["b"]["imp"] for x in d["possible"]}, {csv_iid, sync_iid})
        self.assertTrue(all(x["a"]["row"] and x["b"]["row"] for x in d["possible"]), "the drawer can show the flag on its rows")

        # undo the link: both show again, and the flag comes back
        r = self.robin.delete(f"/api/profiles/{self.pid}/same/{link['id']}", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        d = self.data()
        self.assertEqual(len(self.points(d, "Hemoglobin")), 2)
        self.assertEqual(len(self.rows(d, "Hemoglobin")), 2)
        self.assertNotIn("same", d)
        self.assertEqual({x["a"]["name"] for x in d["possible"]}, {"Hemoglobin", "Platelet Count"})

    def test_linking_from_the_drawer(self):
        csv_iid, sync_iid, _ = self.file_then_sync(link_hgb=False)
        d = self.data()
        refs = {x["a"]["name"]: x["ref"] for x in d["possible"]}
        for name in ("Hemoglobin", "Platelet Count"):
            r = self.robin.post(f"/api/profiles/{self.pid}/same", json={"ref": refs[name]}, headers=H)
            self.assertEqual(r.status_code, 201, r.text)
            self.assertEqual(r.json()["records"], self.robin.get(f"/api/profiles/{self.pid}/data").json(),
                             "the reply has the records as the page loads them (#271)")
        d = self.data()
        self.assertNotIn("possible", d)
        for name in ("Hemoglobin", "Platelet Count"):
            self.assertEqual([p[0] for p in self.points(d, name)], [csv_iid], name)
            self.assertEqual([r[0] for r in self.rows(d, name)], [csv_iid], name)
        self.assertNotIn("CBC WITH DIFFERENTIAL", [e["title"] for e in d["events"]],
                         "the synced result with nothing left in it is left out of its lab day")
        self.assertIn("LDL Cholesterol", [r[0] for e in d["events"] for r in (d["details"].get(str(e["id"])) or {}).get("rows") or []])
        r = self.robin.post(f"/api/profiles/{self.pid}/same", json={"ref": refs["Hemoglobin"]}, headers=H)
        self.assertEqual(r.status_code, 409, "no longer flagged: already linked")
        r = self.robin.post(f"/api/profiles/{self.pid}/same", json={"ref": "0" * 32}, headers=H)
        self.assertEqual(r.status_code, 409)

    def test_not_the_same_stops_the_flag(self):
        self.file_then_sync(link_hgb=False)
        ref = next(x["ref"] for x in self.data()["possible"] if x["a"]["name"] == "Platelet Count")
        r = self.robin.post(f"/api/profiles/{self.pid}/same", json={"ref": ref, "same": False}, headers=H)
        self.assertEqual(r.status_code, 201, r.text)
        d = self.data()
        self.assertEqual([x["a"]["name"] for x in d["possible"]], ["Hemoglobin"])
        self.assertEqual(len(self.points(d, "Platelet Count")), 2, "both still shown")
        self.assertEqual([x["same"] for x in d["same"]], [False])

    def test_three_sources_show_once_with_all_three(self):
        """A third source (a data file loaded on the server, its time in UTC): linked in pairs, shown once."""
        csv_iid, sync_iid, _ = self.file_then_sync()
        third = {"events": [{"id": 1, "t": "2025-03-04T15:45:00+00:00", "k": "lab", "title": "Lab draw", "key": "x:day"},
                            {"id": 2, "t": "2025-03-04T15:45:00+00:00", "k": "labitem", "title": "Blood count", "key": "x:item", "hidden": 1}],
                 "details": {"1": {"type": "labday", "ids": [2]},
                             "2": {"type": "result", "order": "Blood count", "rows": [["Platelets", "455", "K/uL", "", "H"],
                                                                                     ["HGB", "12.1", "g/dL", "", "L"]]}},
                 "labs": [{"name": "Hemoglobin", "unit": "g/dL", "panel": "CBC", "key": "x:hgb", "n": 1,
                           "pts": [["2025-03-04T15:45:00+00:00", 12.1, None, None, "L", "12.1"]]},
                          {"name": "Platelet Count", "unit": "K/uL", "panel": "CBC", "key": "x:plt", "n": 1,
                           "pts": [["2025-03-04T15:45:00+00:00", 455, None, None, "H", "455"]]}], "costs": []}
        db = dbm.connect()
        try:
            with dbm.transaction(db):
                x_iid = records.store(db, self.pid, third, "dataset")
        finally:
            db.close()
        for _ in range(4):   # link every pair still flagged, as someone would from the drawer
            d = self.data()
            if "possible" not in d:
                break
            r = self.robin.post(f"/api/profiles/{self.pid}/same", json={"ref": d["possible"][0]["ref"]}, headers=H)
            self.assertEqual(r.status_code, 201, r.text)
        d = self.data()
        self.assertNotIn("possible", d)
        for name in ("Hemoglobin", "Platelet Count"):
            (pt,) = self.points(d, name)
            self.assertEqual(pt[0], csv_iid, name)
            self.assertEqual(sorted(x["imp"] for x in pt[3]), sorted([sync_iid, x_iid]), name)
        kept = [d["details"][str(e["id"])] for e in d["events"] if e["imp"] == csv_iid and e["k"] == "labitem"]
        self.assertEqual(sorted(x["imp"] for k in kept for x in k.get("same") or []), sorted([sync_iid, x_iid] * 2))
        for k in kept:
            for x in k.get("same") or []:
                self.assertLess(x["row"], len(k["rows"]), "the row it names is there")
        self.assertNotIn("Blood count", [e["title"] for e in d["events"]], "the third's result, all linked away, is gone")

    # ---------------------------------------------------------------- undo

    def test_undoing_the_sync_leaves_the_file(self):
        csv_iid, sync_iid, _ = self.file_then_sync()
        self.undo_import(sync_iid)
        d = self.data()
        self.assertEqual([(p[0], p[3]) for p in self.points(d, "Hemoglobin")], [(csv_iid, None)])
        self.assertEqual([r[0] for r in self.rows(d, "Hemoglobin")], [csv_iid])
        self.assertNotIn("same", d["details"][str(self.rows(d, "Hemoglobin")[0][1])])
        self.assertEqual([x["found"] for x in d["same"]], [False], "the link stays, unapplied")
        self.assertNotIn("possible", d)

    def test_undoing_the_file_leaves_the_sync(self):
        csv_iid, sync_iid, _ = self.file_then_sync()
        self.undo_import(csv_iid)
        d = self.data()
        self.assertEqual([(p[0], p[2], p[3]) for p in self.points(d, "Hemoglobin")], [(sync_iid, "12.1", None)])
        self.assertEqual([r[0] for r in self.rows(d, "Hemoglobin")], [sync_iid])
        self.assertIn("CBC WITH DIFFERENTIAL", [e["title"] for e in d["events"]])

    def test_link_survives_a_reimport(self):
        """Undo the file and import it again: the link (kept apart from imports) applies to it again."""
        csv_iid, sync_iid, _ = self.file_then_sync()
        self.undo_import(csv_iid)
        again = self.confirm(self.import_csv())["import_id"]
        d = self.data()
        hgb = self.points(d, "Hemoglobin")
        self.assertEqual(len(hgb), 1, hgb)
        self.assertEqual(hgb[0][0], sync_iid, "the sync is now the older import, so its point stays")
        self.assertEqual([x["imp"] for x in hgb[0][3]], [again])
        self.assertEqual([x["found"] for x in d["same"]], [True])

    # ---------------------------------------------------------------- who, encryption, log

    def test_viewers_cant_link_or_undo(self):
        self.file_then_sync()
        d = self.data(self.kim)
        self.assertTrue(d["possible"] and d["same"], "viewers see the flags and links")
        r = self.kim.post(f"/api/profiles/{self.pid}/same", json={"ref": d["possible"][0]["ref"]}, headers=H)
        self.assertEqual(r.status_code, 403, r.text)
        r = self.kim.delete(f"/api/profiles/{self.pid}/same/{d['same'][0]['id']}", headers=H)
        self.assertEqual(r.status_code, 403, r.text)
        r = self.robin.post(f"/api/profiles/{self.pid}/same", json={"ref": d["possible"][0]["ref"]})
        self.assertEqual(r.status_code, 403, "the cross-site check")
        self.assertEqual(len(self.data()["same"]), 1)

    def test_encrypted_and_logged(self):
        self.file_then_sync(link_hgb=False)
        ref = self.data()["possible"][0]["ref"]
        self.assertEqual(self.robin.post(f"/api/profiles/{self.pid}/same", json={"ref": ref}, headers=H).status_code, 201)
        raw = sqlite3.connect(os.path.join(config.DATA_DIR, "sanguity.db"))
        try:
            rows = raw.execute("SELECT pair, a_key, b_key, value FROM same_results").fetchall()
        finally:
            raw.close()
        self.assertTrue(rows)
        for pair, *sealed in rows:
            self.assertRegex(pair, r"^[0-9a-f]{32}$")
            for v in sealed:
                self.assertNotIn("loinc", v)
                self.assertNotIn("2025-03-04", v)
                self.assertTrue(crypto.dec(v))
        items = self.robin.get(f"/api/profiles/{self.pid}/activity?action=edit").json()
        self.assertEqual([(a["detail"]["action"], a["detail"]["title"], a["actor"]) for a in items], [("same", "Hemoglobin", "robin")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
