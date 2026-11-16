#!/usr/bin/env python3
"""Corrections in the app (server/edits.py, server/routes_edits.py, issue #33): each kind of edit applied to the
records, edits surviving a rebuild of the setup build (new ids, same content) and the undo and redo of a keyed
import, an edit whose event is gone reported as not found, roles, the CSRF check, the activity log, encryption at
rest, and corrections.json moving into edits once. Fictional data only.

  python3 tests/test_edits.py
"""
import json, os, shutil, sys, tempfile, time, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-edits-test-")
BUILD = os.path.join(TMP, "build")
CORR = os.path.join(TMP, "config", "corrections.json")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=BUILD,
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_CORRECTIONS=CORR)
for d in ("records", "keys", "build", "config"):
    os.makedirs(os.path.join(TMP, d))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, crypto, db as dbm, edits, main, profiles as prof, records   # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites", "lee": "maple tunnel brisk saddle"}

# a setup build as build.py writes it: no source keys, ids that are only positions
BUILT = {
    "built": "2026-11-01T00:00:00+00:00", "source": "fictional",
    "events": [
        {"id": 1, "t": "2026-02-10T09:15:00", "k": "visit", "title": "Outpatient visit", "sub": "Dr. Fictional"},
        {"id": 2, "t": "2026-03-01T14:00:00", "k": "visit", "title": "Hospital admission", "sub": "Example Health",
         "end": "2026-03-01T18:00:00"},
        {"id": 3, "t": "2026-03-01T14:00:00", "k": "stay", "title": "Hospital stay · Example Health",
         "sub": "Ward 3 · same day", "end": "2026-03-01T23:59:00"},
        {"id": 4, "t": "2026-04-02T08:00:00", "k": "lab", "title": "Lab draw", "sub": "2 results"},
        {"id": 5, "t": "2026-04-02T08:00:00", "k": "labitem", "title": "CBC", "sub": ""},
        {"id": 6, "t": "2026-04-02T08:00:00", "k": "labitem", "title": "Ferritin", "sub": "", "abn": True},
        {"id": 7, "t": "2026-04-05T00:00:00", "k": "claim", "title": "Claim · Imaging", "sub": "$120"},
        {"id": 8, "t": "2026-05-20T10:00:00", "k": "visit", "title": "Follow-up visit", "sub": "", "end": "2026-05-20T11:30:00"},
        {"id": 9, "t": "2026-05-20T10:05:00", "k": "document", "title": "Follow-up note", "sub": ""},
    ],
    "details": {
        "2": {"type": "visit", "who": "Dr. Example", "where": "Example Health", "src": "visit-records/a.xml"},
        "3": {"type": "stay", "admit": "2026-03-01", "discharge": "2026-03-01", "where": "Ward 3", "how": "C-CDA encounter"},
        "4": {"type": "labday", "ids": [5, 6]},
        "5": {"type": "result", "order": "CBC", "rows": []},
        "6": {"type": "result", "order": "Ferritin", "rows": []},
        "8": {"type": "visit", "who": "Dr. Fictional"},
        "9": {"type": "visit", "where": "Clinic B", "dx": "Fictional diagnosis"},
    },
    "labs": [], "upcoming": [], "problems": [], "milestones": [],
    "costs": [{"id": 7, "d": "2026-04-05", "billed": 120, "paid": 100, "owe": 20}],
}


def write_build(data=BUILT):
    path = os.path.join(BUILD, "data.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))   # a rebuild: a new modification time


def client():
    return TestClient(main.app, base_url="http://testserver")


def by_title(data, title):
    return [e for e in data["events"] if e["title"] == title]


class Edits(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        write_build()
        cls.ctx = client().__enter__()
        db = dbm.connect()
        uids = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        cls.pid = db.execute("SELECT id FROM profiles WHERE linked_build=1").fetchone()[0]   # made from the build at start-up
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'owner')", (uids["robin"], cls.pid))
        prof.create_profile(db, "Lee (fictional)", owner_id=uids["lee"])   # a later profile: corrections.json isn't for it
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (uids["kim"], cls.pid))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (uids["lee"], cls.pid))
        prof.sync_build(db, cls.pid)
        db.close()
        cls.robin, cls.kim, cls.lee = cls.login("robin"), cls.login("kim"), cls.login("lee")

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

    def setUp(self):
        db = dbm.connect()
        db.execute("DELETE FROM edits")
        records._bump(db, self.pid)
        db.close()

    def data(self, c=None):
        r = (c or self.robin).get(f"/api/profiles/{self.pid}/data")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def ev(self, title, data=None):
        hits = by_title(data or self.data(), title)
        self.assertEqual(len(hits), 1, title)
        return hits[0]

    def edit(self, action, event, c=None, status=201, **kw):
        r = (c or self.robin).post(f"/api/profiles/{self.pid}/edits", json={"event": event, "action": action, **kw}, headers=H)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def rebuild(self):
        before = {e["id"] for e in self.data()["events"]}
        write_build()
        db = dbm.connect()
        self.assertEqual(prof.sync_build(db, self.pid), {})
        db.close()
        after = self.data()
        self.assertFalse(before & {e["id"] for e in after["events"]}, "a rebuild gives every event a new id")
        return after

    def check_all_applied(self, d):
        titles = [e["title"] for e in d["events"]]
        self.assertNotIn("Claim · Imaging", titles)                     # hide
        self.assertEqual(d["costs"], [])                                # ...with its row on the Costs tab
        self.assertNotIn("Ferritin", titles)                            # hide a lab result...
        lab = self.ev("Lab draw", d)
        cbc = self.ev("CBC", d)
        self.assertEqual(d["details"][str(lab["id"])]["ids"], [cbc["id"]])   # ...and its place in the lab day
        self.assertIn("First visit", titles)                            # rename
        self.assertNotIn("Outpatient visit", titles)
        fu = self.ev("Follow-up visit", d)                              # date: the end moves too
        self.assertEqual((fu["t"], fu["end"]), ("2026-05-21T09:00:00", "2026-05-21T10:30:00"))
        self.assertEqual(d["events"], sorted(d["events"], key=lambda e: e["t"]))
        stay = self.ev("Hospital stay · Example Health", d)             # not_stay
        self.assertEqual(stay["k"], "visit")
        self.assertNotIn("end", stay)
        self.assertEqual(d["details"][str(stay["id"])]["type"], "visit")
        self.assertFalse([e for e in d["events"] if e["k"] == "stay"])
        adm = self.ev("Hospital admission", d)                          # merge: the note folds into the visit...
        self.assertNotIn("Follow-up note", titles)
        merged = d["details"][str(fu["id"])]
        self.assertEqual(merged["who"], "Dr. Fictional")                # ...which keeps its own fields
        self.assertEqual((merged["where"], merged["dx"]), ("Clinic B", "Fictional diagnosis"))   # and gains what it lacked
        self.assertEqual(merged["merged"][0]["title"], "Follow-up note")
        self.assertTrue(adm)
        self.assertTrue(all(x["found"] for x in d["edits"]))
        self.assertEqual(len(d["edits"]), 6)

    def test_each_action_survives_a_rebuild(self):
        d = self.data()
        self.edit("hide", self.ev("Claim · Imaging", d)["id"])
        self.edit("hide", self.ev("Ferritin", d)["id"])
        self.edit("rename", self.ev("Outpatient visit", d)["id"], title="Typo title")
        self.edit("rename", self.ev("Outpatient visit", d)["id"], title="  First visit ")   # replaces the first rename
        self.edit("date", self.ev("Follow-up visit", d)["id"], t="2026-05-21T09:00")
        self.edit("not_stay", self.ev("Hospital stay · Example Health", d)["id"])
        self.edit("merge", self.ev("Follow-up visit", d)["id"], other=self.ev("Follow-up note", d)["id"])
        self.check_all_applied(self.data())
        self.check_all_applied(self.rebuild())   # #33's done-when, for events without a source key

    def test_bad_edits_are_refused(self):
        d = self.data()
        visit = self.ev("Outpatient visit", d)["id"]
        self.edit("not_stay", visit, status=400)
        self.edit("rename", visit, title="   ", status=400)
        self.edit("date", visit, t="next tuesday", status=400)
        self.edit("merge", visit, other=visit, status=400)
        self.edit("hide", 999999, status=400)
        self.edit("explode", visit, status=422)

    def test_keyed_import_undone_and_redone(self):
        upload = {"events": [{"id": "a", "t": "2025-07-01T10:00:00", "k": "visit", "title": "Telehealth visit", "key": "fhir:Encounter/fict-1"},
                             {"id": "b", "t": "2025-07-02T10:00:00", "k": "visit", "title": "Video call", "key": "fhir:Encounter/fict-2"}],
                  "details": {}, "labs": [], "costs": []}

        def store():
            db = dbm.connect()
            with dbm.transaction(db):
                iid = records.store(db, self.pid, json.loads(json.dumps(upload)), "upload", fmt="fhir")
            db.close()
            return iid

        iid = store()
        d = self.data()
        self.edit("rename", self.ev("Telehealth visit", d)["id"], title="Phone visit")
        self.edit("hide", self.ev("Video call", d)["id"])
        d = self.data()
        self.assertTrue(by_title(d, "Phone visit") and not by_title(d, "Video call"))

        db = dbm.connect()
        with dbm.transaction(db):
            records.drop(db, self.pid, [iid])   # the import undone
        db.close()
        listed = self.robin.get(f"/api/profiles/{self.pid}/edits").json()["edits"]
        self.assertEqual(len(listed), 2, "the edits stay when their import goes")
        self.assertEqual([(x["found"], x["ids"]) for x in listed], [(False, []), (False, [])])
        self.assertEqual({x["was"]["title"] for x in listed}, {"Telehealth visit", "Video call"})

        iid = store()   # ...and redone: new ids, same source keys
        d = self.data()
        self.assertTrue(by_title(d, "Phone visit") and not by_title(d, "Video call") and not by_title(d, "Telehealth visit"))
        self.assertTrue(all(x["found"] for x in d["edits"]))
        db = dbm.connect()
        with dbm.transaction(db):
            records.drop(db, self.pid, [iid])
        db.close()

    def test_missing_target_is_reported(self):
        db = dbm.connect()
        with dbm.transaction(db):
            edits._insert(db, self.pid, None, "hide", edits.content_key("visit", "2020-01-01T10:00:00", "Long gone"),
                          {"was": {"k": "visit", "t": "2020-01-01T10:00:00", "title": "Long gone"}})
            records._bump(db, self.pid)
        db.close()
        d = self.data()
        self.assertEqual(len(d["events"]), len(BUILT["events"]))
        [x] = d["edits"]
        self.assertEqual((x["found"], x["ids"], x["was"]["title"]), (False, [], "Long gone"))

    def test_roles_csrf_and_activity(self):
        d = self.data(self.kim)   # a viewer sees the records, and what was edited in them
        visit = self.ev("Outpatient visit", d)["id"]
        self.edit("hide", visit, c=self.kim, status=403)
        self.assertEqual(self.kim.get(f"/api/profiles/{self.pid}/edits").status_code, 403)
        r = self.robin.post(f"/api/profiles/{self.pid}/edits", json={"event": visit, "action": "hide"})   # no X-Sanguity
        self.assertEqual(r.status_code, 403)
        eid = self.edit("rename", visit, c=self.lee, title="Renamed by an editor")["id"]   # editors may
        self.assertEqual([x["by"] for x in self.data(self.kim)["edits"]], ["lee"])
        self.assertEqual(self.kim.delete(f"/api/profiles/{self.pid}/edits/{eid}", headers=H).status_code, 403)
        self.assertEqual(self.robin.delete(f"/api/profiles/{self.pid}/edits/{eid}", headers=H).status_code, 200)
        self.assertEqual(self.robin.delete(f"/api/profiles/{self.pid}/edits/{eid}", headers=H).status_code, 404)
        self.assertTrue(by_title(self.data(), "Outpatient visit"))
        log = self.robin.get(f"/api/profiles/{self.pid}/activity?action=edit").json()
        entries = log["entries"] if isinstance(log, dict) else log
        details = [e["detail"] for e in entries if e["action"] == "edit"][:2]
        self.assertEqual(details[0], {"action": "rename", "title": "Outpatient visit", "undone": True})
        self.assertEqual(details[1], {"action": "rename", "title": "Outpatient visit", "to": "Renamed by an editor"})

    def test_encrypted_at_rest(self):
        self.edit("rename", self.ev("Outpatient visit")["id"], title="Secret fictional title")
        db = dbm.connect()
        rows = db.execute("SELECT target, target_key, value FROM edits").fetchall()
        db.close()
        self.assertEqual(len(rows), 1)
        for col in ("target_key", "value"):
            self.assertTrue(rows[0][col].startswith(crypto.FIELD), col)
        raw = json.dumps([tuple(r) for r in rows])
        for plain in ("Secret fictional title", "Outpatient visit", edits.CONTENT):
            self.assertNotIn(plain, raw)
        self.assertRegex(rows[0]["target"], r"^[0-9a-f]{32}$")

    def test_z_corrections_json_moves_once(self):   # last: it changes what the build shows
        db = dbm.connect()
        self.assertIsNone(dbm.meta_get(db, edits.MIGRATED), "nothing to move while there's no corrections.json")
        with open(CORR, "w", encoding="utf-8") as f:
            json.dump({"milestones": [{"date": "2026-01-15", "label": "Diagnosis"}],
                       "not_hospital_stays": [{"date": "2026-03-01", "org": "Example Health", "title": "Outpatient CT",
                                               "why": "Coded as inpatient"},
                                              {"date": "2026-06-01", "org": "", "why": "No such stay in these records"}]}, f)
        self.assertEqual(edits.migrate_corrections(db), 4)
        self.assertIsNone(edits.migrate_corrections(db), "only once")
        acts = [r[0] for r in db.execute("SELECT action FROM edits WHERE profile_id=? ORDER BY id", (self.pid,))]
        self.assertEqual(acts, ["not_stay", "rename", "merge", "not_stay"])
        db.close()
        d = self.data()
        ct = self.ev("Outpatient CT", d)   # one visit, as build.py's correction used to leave it
        self.assertEqual(ct["k"], "visit")
        self.assertFalse(by_title(d, "Hospital admission") or by_title(d, "Hospital stay · Example Health"))
        self.assertEqual(d["details"][str(ct["id"])]["who"], "Dr. Example")
        self.assertFalse([e for e in d["events"] if e["k"] == "stay"])
        self.assertEqual([x["found"] for x in d["edits"]], [True, True, True, False])
        self.assertEqual(d["edits"][3]["was"], {"k": "stay", "t": "2026-06-01", "title": "Hospital stay"})
        self.assertEqual(self.ev("Outpatient CT", self.rebuild())["k"], "visit")


if __name__ == "__main__":
    unittest.main(verbosity=2)
