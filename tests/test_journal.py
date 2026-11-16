#!/usr/bin/env python3
"""The journal (server/journal.py, server/routes_journal.py, issue #49): notes, symptoms and side effects added,
changed and deleted; what can't be saved; roles and the CSRF check; the activity log (the kind, never the words);
encryption at rest; the entries in the records the page loads, kept when an import is undone; and in "Export
everything". Fictional data only.

  python3 tests/test_journal.py
"""
import io, json, os, shutil, sys, tempfile, unittest, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-journal-test-")
BUILD = os.path.join(TMP, "build")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=BUILD,
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_CORRECTIONS=os.path.join(TMP, "config", "corrections.json"))
for d in ("records", "keys", "build", "config"):
    os.makedirs(os.path.join(TMP, d))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, config, crypto, db as dbm, main, records   # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites", "lee": "maple tunnel brisk saddle"}

BUILT = {
    "built": "2026-11-01T00:00:00+00:00", "source": "fictional",
    "events": [
        {"id": 1, "t": "2026-02-10T09:15:00", "k": "visit", "title": "Outpatient visit", "sub": "Dr. Fictional"},
        {"id": 2, "t": "2026-03-01T08:00:00", "k": "med", "title": "Examplomab 10 mg", "sub": "Active"},
        {"id": 3, "t": "2026-04-02T08:00:00", "k": "rx", "title": "Fictorin 5 mg tablet", "sub": "Pharmacy fill"},
    ],
    "details": {"2": {"type": "med", "instructions": "Once daily", "status": "Active", "src": "Fictional clinic"}},
    "labs": [], "upcoming": [], "problems": [], "milestones": [], "costs": [],
}
SECRET = "Fictional throbbing behind the left eye"


def client():
    return TestClient(main.app, base_url="http://testserver")


class Journal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(BUILD, "data.json"), "w", encoding="utf-8") as f:
            json.dump(BUILT, f)
        cls.ctx = client().__enter__()
        db = dbm.connect()
        uids = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        cls.pid = db.execute("SELECT id FROM profiles WHERE linked_build=1").fetchone()[0]
        for who, role in (("robin", "owner"), ("lee", "editor"), ("kim", "viewer")):
            db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, ?)", (uids[who], cls.pid, role))
        db.close()
        cls.robin, cls.kim, cls.lee = (cls.login(n) for n in ("robin", "kim", "lee"))

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
        db.execute("DELETE FROM journal")
        records._bump(db, self.pid)
        db.close()

    def url(self, rest=""):
        return f"/api/profiles/{self.pid}/journal{rest}"

    def add(self, c=None, status=201, **entry):
        r = (c or self.robin).post(self.url(), json=entry, headers=H)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def data(self, c=None):
        r = (c or self.robin).get(f"/api/profiles/{self.pid}/data")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_add_change_delete(self):
        sid = self.add(kind="symptom", t="2026-04-03", title="Headache", severity=6, note=SECRET)["id"]
        nid = self.add(kind="note", t="2026-04-04T21:30", note="Slept badly, fictional")["id"]
        fid = self.add(kind="side_effect", t="2026-04-05T08:00", title="Nausea", severity=3, med="Fictorin 5 mg tablet")["id"]
        listed = self.robin.get(self.url()).json()
        self.assertEqual(listed["medications"], ["Examplomab 10 mg", "Fictorin 5 mg tablet"])
        got = {e["id"]: e for e in listed["entries"]}
        self.assertEqual([e["id"] for e in listed["entries"]], [sid, nid, fid], "oldest first, by date")
        self.assertEqual({k: got[sid][k] for k in ("kind", "t", "title", "severity", "note", "med", "by")},
                         {"kind": "symptom", "t": "2026-04-03", "title": "Headache", "severity": 6, "note": SECRET, "med": None, "by": "robin"})
        self.assertEqual((got[nid]["kind"], got[nid]["severity"], got[nid]["t"]), ("note", None, "2026-04-04T21:30"))
        self.assertEqual(got[fid]["med"], "Fictorin 5 mg tablet")

        r = self.lee.put(self.url(f"/{sid}"), json={"kind": "symptom", "t": "2026-04-03T07:00", "title": "Headache", "severity": 8}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        e = {x["id"]: x for x in self.robin.get(self.url()).json()["entries"]}[sid]
        self.assertEqual((e["severity"], e["t"], e["note"], e["by"], e["changed_by"]), (8, "2026-04-03T07:00", "", "robin", "lee"))
        self.assertTrue(e["changed"])

        self.assertEqual(self.robin.delete(self.url(f"/{nid}"), headers=H).status_code, 200)
        self.assertEqual(self.robin.delete(self.url(f"/{nid}"), headers=H).status_code, 404)
        self.assertEqual(self.robin.put(self.url(f"/{nid}"), json={"kind": "note", "t": "2026-04-04", "note": "x"}, headers=H).status_code, 404)
        self.assertEqual([x["id"] for x in self.robin.get(self.url()).json()["entries"]], [sid, fid])

    def test_what_cant_be_saved(self):
        bad = [
            {"kind": "mood", "t": "2026-04-03", "note": "x"},
            {"kind": "note", "t": "2026-04-03", "note": "   "},                                       # empty note
            {"kind": "note", "t": "2026-04-03", "note": "x", "severity": 4},                         # notes aren't rated
            {"kind": "symptom", "t": "2026-04-03", "title": "Cough"},                                # no severity
            {"kind": "symptom", "t": "2026-04-03", "title": "Cough", "severity": 11},
            {"kind": "symptom", "t": "2026-04-03", "title": "Cough", "severity": -1},
            {"kind": "symptom", "t": "2026-04-03", "severity": 2},                                   # what was it?
            {"kind": "symptom", "t": "03/04/2026", "title": "Cough", "severity": 2},
            {"kind": "symptom", "t": "2026-02-30", "title": "Cough", "severity": 2},
            {"kind": "side_effect", "t": "2026-04-03", "title": "Rash", "severity": 2, "med": "Not in the records"},
            {"kind": "note", "t": "2026-04-03", "note": "x" * 5001},
        ]
        for b in bad:
            r = self.robin.post(self.url(), json=b, headers=H)
            self.assertIn(r.status_code, (400, 422), b)
        self.assertEqual(self.robin.get(self.url()).json()["entries"], [])

    def test_roles_csrf_and_activity(self):
        jid = self.add(c=self.lee, kind="symptom", t="2026-04-03", title="Fatigue", severity=4, note=SECRET)["id"]   # editors may
        self.assertEqual(self.kim.get(self.url()).status_code, 200, "viewers read")
        self.assertEqual([e["title"] for e in self.data(self.kim)["journal"]], ["Fatigue"])
        self.add(c=self.kim, status=403, kind="note", t="2026-04-03", note="x")
        self.assertEqual(self.kim.put(self.url(f"/{jid}"), json={"kind": "note", "t": "2026-04-03", "note": "x"}, headers=H).status_code, 403)
        self.assertEqual(self.kim.delete(self.url(f"/{jid}"), headers=H).status_code, 403)
        self.assertEqual(self.robin.post(self.url(), json={"kind": "note", "t": "2026-04-03", "note": "x"}).status_code, 403, "CSRF: no X-Sanguity")
        self.assertEqual(self.robin.delete(self.url(f"/{jid}")).status_code, 403)
        self.assertEqual(client().get(self.url()).status_code, 401)
        self.assertEqual(self.robin.put(self.url(f"/{jid}"), json={"kind": "symptom", "t": "2026-04-03", "title": "Fatigue", "severity": 5}, headers=H).status_code, 200)
        self.assertEqual(self.robin.delete(self.url(f"/{jid}"), headers=H).status_code, 200)
        log = self.robin.get(f"/api/profiles/{self.pid}/activity?action=journal").json()
        entries = log["entries"] if isinstance(log, dict) else log
        self.assertEqual([(e["actor"], e["detail"]) for e in entries][:3],
                         [("robin", {"action": "delete", "kind": "symptom"}), ("robin", {"action": "change", "kind": "symptom"}),
                          ("lee", {"action": "add", "kind": "symptom"})])
        self.assertNotIn("Fatigue", json.dumps(entries))

    def test_encrypted_at_rest(self):
        self.add(kind="side_effect", t="2026-04-05", title="Fictional tingling", severity=7, note=SECRET, med="Examplomab 10 mg")
        db = dbm.connect()
        rows = db.execute("SELECT * FROM journal").fetchall()
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.close()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["body"].startswith(crypto.FIELD))
        raw = json.dumps([tuple(r) for r in rows])
        for plain in (SECRET, "Fictional tingling", "Examplomab", "side_effect", "2026-04-05"):
            self.assertNotIn(plain, raw)
        for path in (config.DB_PATH, config.DB_PATH + "-wal"):
            if os.path.exists(path):
                with open(path, "rb") as f:
                    blob = f.read()
                self.assertNotIn(SECRET.encode(), blob, path)
                self.assertNotIn(b"Fictional tingling", blob, path)

    def test_in_the_records_and_kept_through_undo(self):
        self.add(kind="symptom", t="2026-04-03", title="Headache", severity=6)
        d = self.data()
        self.assertEqual([(e["kind"], e["title"], e["severity"]) for e in d["journal"]], [("symptom", "Headache", 6)])
        self.assertEqual(len(d["events"]), len(BUILT["events"]), "entries are their own list, not events")
        # an import added and undone: the journal stays
        db = dbm.connect()
        with dbm.transaction(db):
            iid = records.store(db, self.pid, {"events": [{"id": "a", "t": "2026-05-01T10:00:00", "k": "visit", "title": "Video visit",
                                                           "key": "fhir:Encounter/fict-9"}], "details": {}, "labs": [], "costs": []},
                                "upload", fmt="fhir")
        db.close()
        self.assertEqual(len(self.data()["events"]), len(BUILT["events"]) + 1)
        db = dbm.connect()
        with dbm.transaction(db):
            records.drop(db, self.pid, [iid])
        db.close()
        self.assertEqual([e["title"] for e in self.data()["journal"]], ["Headache"])
        # a stored import never takes a journal list in (an export's data.json has one)
        db = dbm.connect()
        with dbm.transaction(db):
            iid = records.store(db, self.pid, {"events": [], "journal": [{"kind": "note", "t": "2026-01-01", "note": "from a file"}]}, "upload")
            records.drop(db, self.pid, [iid])
        db.close()
        self.assertEqual(len(self.data()["journal"]), 1)

    def test_in_export(self):
        self.add(kind="symptom", t="2026-04-03", title="Headache", severity=6, note=SECRET)
        self.add(kind="note", t="2026-04-04", note="Fictional note for the export")
        r = self.lee.get(f"/api/profiles/{self.pid}/export")
        self.assertEqual(r.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            data = json.loads(z.read("data.json"))
            readme = z.read("README.txt").decode()
        self.assertEqual([(e["kind"], e.get("title"), e["note"]) for e in data["journal"]],
                         [("symptom", "Headache", SECRET), ("note", "", "Fictional note for the export")])
        self.assertIn('"journal": 2 entries', readme)
        self.assertEqual(self.kim.get(f"/api/profiles/{self.pid}/export").status_code, 403)

    def test_medication_kept_after_its_import_goes(self):
        db = dbm.connect()
        with dbm.transaction(db):
            iid = records.store(db, self.pid, {"events": [{"id": "m", "t": "2026-06-01T08:00:00", "k": "med", "title": "Fictazole 2 mg",
                                                           "key": "fhir:MedicationRequest/fict-1"}]}, "upload", fmt="fhir")
        db.close()
        jid = self.add(kind="side_effect", t="2026-06-02", title="Dizziness", severity=2, med="Fictazole 2 mg")["id"]
        db = dbm.connect()
        with dbm.transaction(db):
            records.drop(db, self.pid, [iid])
        db.close()
        self.assertNotIn("Fictazole 2 mg", self.robin.get(self.url()).json()["medications"])
        ok = {"kind": "side_effect", "t": "2026-06-02", "title": "Dizziness", "severity": 4, "med": "Fictazole 2 mg"}
        self.assertEqual(self.robin.put(self.url(f"/{jid}"), json=ok, headers=H).status_code, 200, "its own link is kept")
        self.add(status=400, **ok)   # but a new entry can't link to it

    def test_profile_deleted_takes_its_journal(self):
        db = dbm.connect()
        pid = db.execute("INSERT INTO profiles(name, created) VALUES('Temp (fictional)', 1)").lastrowid
        uid = db.execute("SELECT id FROM users WHERE username='robin'").fetchone()[0]
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'owner')", (uid, pid))
        db.execute("INSERT INTO journal(profile_id, body, created_name, created) VALUES(?, ?, 'robin', 1)", (pid, crypto.enc("{}")))
        db.execute("DELETE FROM profiles WHERE id=?", (pid,))
        self.assertEqual(db.execute("SELECT COUNT(*) FROM journal WHERE profile_id=?", (pid,)).fetchone()[0], 0)
        db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
