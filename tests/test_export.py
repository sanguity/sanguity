#!/usr/bin/env python3
"""Export everything, loaded into another profile (server/export.py, issue #589): the records come back with the
corrections made to them (a hidden record, a merge, a rename, a change of category kept for new imports), a
same-result decision, the journal with a photo, the medication log and a reminder, all of them still undoable
there; both ways of loading it (server.manage import, and an upload on the import page); and the activity log only
in an owner's export. Fictional data only.

  python3 tests/test_export.py
"""
import io, json, os, struct, sys, tempfile, time, unittest, zipfile, zlib
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-export-test-")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "no-build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"))
os.makedirs(os.path.join(TMP, "records"))
os.makedirs(os.path.join(TMP, "keys"))
sys.path.insert(0, ROOT)

import logging   # noqa: E402

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, db as dbm, export, main, profiles as prof, records, uploads   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "lee": "maple tunnel brisk saddle"}
D = lambda n: (date.today() + timedelta(days=n)).isoformat()

# two sources: a file (visits, a medication, a letter, a haemoglobin result) and a FHIR pull with the same result in
# other units, so the page flags them as possibly the same
FILE = {"built": "2026-11-01T00:00:00+00:00", "source": "fictional file",
        "events": [{"id": 1, "t": D(-60) + "T09:00:00", "k": "visit", "title": "Annual physical (fictional)", "sub": "", "key": "f:v1"},
                   {"id": 2, "t": D(-50) + "T09:00:00", "k": "visit", "title": "Follow-up (fictional)", "sub": "", "key": "f:v2"},
                   {"id": 3, "t": D(-50) + "T09:05:00", "k": "visit", "title": "Clinic visit (fictional)", "sub": "", "key": "f:v3"},
                   {"id": 4, "t": D(-40) + "T10:00:00", "k": "visit", "title": "Telehealth (fictional)", "sub": "", "key": "f:v4"},
                   {"id": 5, "t": D(-200), "k": "med", "title": "Fictionamide 10 mg tablet", "sub": "", "key": "f:m1"}],
        "details": {"2": {"type": "visit", "note": "Fictional follow-up"}, "3": {"type": "visit", "who": "Dr Example (fictional)"},
                    "5": {"type": "med", "instructions": "One tablet by mouth"}},
        "labs": [{"name": "Hemoglobin", "unit": "g/dL", "loinc": "718-7", "key": "f:hgb", "panel": "CBC",
                  "pts": [[D(-60) + "T09:00:00", 13.5, 12, 16, "", "13.5"]]}],
        "costs": []}
PULL = {"events": [{"id": 1, "t": D(-30) + "T11:00:00", "k": "letter", "title": "Results letter (fictional)", "sub": "", "key": "p:l1"},
                   {"id": 2, "t": D(-20) + "T11:00:00", "k": "letter", "title": "Results letter (fictional)", "sub": "", "key": "p:l2"}],
        "details": {"1": {"type": "letter", "text": "Fictional letter"}, "2": {"type": "letter", "text": "Another fictional letter"}},
        "labs": [{"name": "Hemoglobin", "unit": "g/L", "loinc": "718-7", "key": "p:hgb", "panel": "CBC",
                  "pts": [[D(-60) + "T09:00:00", 135, 120, 160, "", "135"]]}], "costs": []}


def png():
    """A 2x2 PNG, with a text chunk that's taken out when it's sent."""
    def chunk(t, body):
        return struct.pack(">I", len(body)) + t + body + struct.pack(">I", zlib.crc32(t + body) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x10\x80\xc0" * 2 for _ in range(2))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
            + chunk(b"tEXt", b"Comment\x00fictional") + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def client():
    return TestClient(main.app, base_url="http://testserver")


def shown(data):
    """What the page shows of the records and what people wrote, ids aside."""
    return {"events": sorted((e["t"], e["k"], e["title"]) for e in data["events"]),
            "points": sorted((s["name"], s["unit"], tuple(map(tuple, s["pts"]))) for s in data["labs"]),
            "merged": sorted((x["title"], x["k"]) for d in data["details"].values() if isinstance(d, dict) for x in d.get("merged") or []),
            "edits": sorted((x["action"], x["found"], x["by"], json.dumps(x["was"], sort_keys=True)) for x in data.get("edits") or []),
            "same": sorted((x["same"], x["found"], x["by"]) for x in data.get("same") or []),
            "journal": [(e["kind"], e["t"], e["title"], e["severity"], e["note"], e.get("where"), e["by"], e["at"],
                         [(p["alt"], p["type"], p["bytes"]) for p in e.get("photos") or []]) for e in data.get("journal") or []],
            "doses": [(d["med"], d["t"], d["status"], d["note"], d["by"], d["at"]) for d in data.get("doses") or []],
            "reminders": [(r["med"], r["times"], r["days"], r["from"], r["on"], r["by"], r["at"]) for r in data.get("reminders") or []]}


class Export(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = client().__enter__()   # lifespan: database, key, the import worker
        db = dbm.connect()
        cls.uid = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        cls.pid = prof.create_profile(db, "Mira Example (fictional)", owner_id=cls.uid["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (cls.uid["lee"], cls.pid))
        with dbm.transaction(db):
            records.store(db, cls.pid, FILE, "dataset")
            records.store(db, cls.pid, PULL, "upload", fmt="fhir")
        db.close()
        cls.c = {}
        for n in PW:
            c = client()
            assert c.post("/api/login", json={"username": n, "password": PW[n]}, headers=H).status_code == 200
            cls.c[n] = c
        cls.robin, cls.lee = cls.c["robin"], cls.c["lee"]
        cls.correct()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

    @classmethod
    def data(cls, pid=None):
        from whole_records import whole
        return whole(cls.robin, pid or cls.pid)

    @classmethod
    def correct(cls):
        """Hide a visit, merge one into another, rename one, move the letter to Documents for new imports too, link
        the two haemoglobin results, write a journal entry with a photo, log a dose and set a reminder."""
        p = f"/api/profiles/{cls.pid}"
        d = cls.data()
        ev = {e["title"]: e["id"] for e in d["events"]}
        for body in ({"event": ev["Annual physical (fictional)"], "action": "hide"},
                     {"event": ev["Follow-up (fictional)"], "action": "merge", "other": ev["Clinic visit (fictional)"]},
                     {"event": ev["Telehealth (fictional)"], "action": "rename", "title": "Video visit (fictional)"},
                     {"event": ev["Results letter (fictional)"], "action": "kind", "k": "document", "rule": True}):
            r = cls.lee.post(p + "/edits", json=body, headers=H)
            assert r.status_code == 201, r.text
        ref = cls.data()["possible"][0]["ref"]
        assert cls.lee.post(p + "/same", json={"ref": ref, "same": True}, headers=H).status_code == 201
        sent = cls.lee.put(p + "/journal/photos", content=png(), headers={**H, "Content-Type": "application/octet-stream"}).json()
        r = cls.lee.post(p + "/journal", json={"kind": "symptom", "t": D(-5), "title": "Rash (fictional)", "severity": 4,
                                                "note": "Fictional note", "where": ["l-forearm"],
                                                "photos": [{"id": sent["id"], "alt": "Fictional patch"}]}, headers=H)
        assert r.status_code == 201, r.text
        med = cls.data()["medications"][0]["key"]
        r = cls.lee.post(p + "/medications/doses", json={"med": med, "t": D(-1) + "T08:00", "status": "late", "note": "Fictional"}, headers=H)
        assert r.status_code == 201, r.text
        r = cls.robin.post(p + "/medications/reminders", json={"med": med, "times": ["08:00"], "days": ["mo", "we", "fr"],
                                                               "start": D(-10), "tz": "Europe/London"}, headers=H)
        assert r.status_code == 201, r.text

    def export(self, who="robin"):
        r = self.c[who].get(f"/api/profiles/{self.pid}/export")
        self.assertEqual(r.status_code, 200, r.text[:300])
        return r.content

    def new_profile(self, name):
        db = dbm.connect()
        try:
            return prof.create_profile(db, name, owner_id=self.uid["robin"])
        finally:
            db.close()

    def check_copy(self, pid, before):
        """The copy shows what the original does, and each correction can be undone there."""
        copy = self.data(pid)
        got = shown(copy)
        # the rule's records arrive from another source than the rule names: the one it moved is moved on its own too,
        # and the rule is kept for new imports from its source, finding nothing here itself
        extra = [x for x in got["edits"] if x not in before["edits"]]
        self.assertEqual([x[:3] for x in extra], [("kind", True, "lee"), ("kind_rule", False, "lee")])
        self.assertIn(("kind_rule", True) + extra[1][2:], before["edits"])
        self.assertEqual({**got, "edits": [x for x in got["edits"] if x not in extra]},
                         {**before, "edits": [x for x in before["edits"] if x[0] != "kind_rule"]})
        titles = {e["title"] for e in copy["events"]}
        self.assertNotIn("Annual physical (fictional)", titles)
        self.assertNotIn("Clinic visit (fictional)", titles)
        self.assertIn("Video visit (fictional)", titles)
        self.assertEqual([e["k"] for e in copy["events"] if e["title"] == "Results letter (fictional)"], ["document", "document"])
        self.assertEqual([len(s["pts"]) for s in copy["labs"]], [1])   # the same result, shown once
        photo = copy["journal"][0]["photos"][0]
        got = self.robin.get(f"/api/profiles/{pid}/journal/photos/{photo['id']}")
        self.assertEqual(got.status_code, 200)
        db = dbm.connect()
        try:
            from server import journal_photos
            self.assertEqual(got.content, journal_photos.strip(png())[0])
        finally:
            db.close()
        # undoing each correction there brings back what it changed
        p = f"/api/profiles/{pid}"
        by = {x["action"]: x["id"] for x in copy["edits"]}
        for action in ("hide", "merge", "rename", "kind_rule"):
            self.assertEqual(self.robin.request("DELETE", f"{p}/edits/{by[action]}", headers=H).status_code, 200, action)
        self.assertEqual(self.robin.request("DELETE", f"{p}/same/{copy['same'][0]['id']}", headers=H).status_code, 200)
        undone = self.data(pid)
        titles = {e["title"]: e["k"] for e in undone["events"]}
        self.assertTrue({"Annual physical (fictional)", "Clinic visit (fictional)", "Telehealth (fictional)"} <= set(titles), titles)
        self.assertNotIn("Video visit (fictional)", titles)
        self.assertEqual([e["k"] for e in undone["events"] if e["title"] == "Results letter (fictional)"], ["letter", "letter"])
        self.assertEqual(sorted(len(s["pts"]) for s in undone["labs"]), [1, 1])
        self.assertFalse(undone.get("edits") or undone.get("same"))

    def test_moved_with_the_command_line(self):
        before = shown(self.data())
        self.assertEqual((len(before["edits"]), len(before["same"]), len(before["journal"]), len(before["doses"]), len(before["reminders"])),
                         (5, 1, 1, 1, 1))
        z = zipfile.ZipFile(io.BytesIO(self.export()))
        data = json.loads(z.read("data.json"))
        # the records as they were imported: the hidden and merged-in ones are there, under their own titles
        titles = {e["title"] for e in data["events"]}
        self.assertTrue({"Annual physical (fictional)", "Clinic visit (fictional)", "Telehealth (fictional)"} <= titles, titles)
        self.assertEqual(sorted(x["action"] for x in data["corrections"]["edits"]), ["hide", "kind", "kind_rule", "merge", "rename"])
        archive = os.path.join(TMP, "moved.zip")
        with open(archive, "wb") as f:
            f.write(z.fp.getvalue())
        pid = self.new_profile("Moved (fictional)")
        db = dbm.connect()
        try:
            export.import_archive(db, pid, archive)
            # loading it again over itself adds nothing twice
            export.import_archive(db, pid, archive)
        finally:
            db.close()
        self.check_copy(pid, before)

    def test_moved_by_upload(self):
        before = shown(self.data())
        pid = self.new_profile("Uploaded (fictional)")
        db = dbm.connect()
        try:
            up = uploads.create(db, pid, None)
            uploads.add_file(db, pid, up, "sanguity-export.zip", io.BytesIO(self.export()))
            uploads.finish(db, pid, up)
        finally:
            db.close()
        r = self.robin.post(f"/api/profiles/{pid}/uploads/{up}/import", headers=H)
        self.assertEqual(r.status_code, 202, r.text)
        jid = r.json()["id"]
        end = time.time() + 30
        while time.time() < end:
            j = self.robin.get(f"/api/profiles/{pid}/jobs/{jid}").json()
            if j["state"] == "preview":
                self.assertEqual(j.get("restores"), {"corrections": 6, "journal": 1, "photos": 1, "doses": 1, "reminders": 1}, j)
                self.assertEqual(self.robin.post(f"/api/profiles/{pid}/jobs/{jid}/confirm", headers=H).status_code, 202)
            elif j["state"] in ("done", "failed", "discarded"):
                break
            time.sleep(0.05)
        self.assertEqual(j["state"], "done", j)
        self.check_copy(pid, before)

    def test_a_damaged_export_is_refused_whole(self):
        z = zipfile.ZipFile(io.BytesIO(self.export()))
        data = json.loads(z.read("data.json"))
        data["doses"][0]["status"] = "maybe"
        bad = os.path.join(TMP, "bad.zip")
        with zipfile.ZipFile(bad, "w") as out:
            for name in z.namelist():
                out.writestr(name, json.dumps(data) if name == "data.json" else z.read(name))
        pid = self.new_profile("Refused (fictional)")
        db = dbm.connect()
        try:
            with self.assertRaisesRegex(ValueError, "logged dose 1 can't be loaded"):
                export.import_archive(db, pid, bad)
            self.assertIsNone(records.assemble(db, pid))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM journal WHERE profile_id=?", (pid,)).fetchone()[0], 0)
        finally:
            db.close()

    def test_activity_log_only_in_an_owners_export(self):
        self.assertEqual(self.lee.get(f"/api/profiles/{self.pid}/activity").status_code, 403)
        mine = zipfile.ZipFile(io.BytesIO(self.export("robin")))
        theirs = zipfile.ZipFile(io.BytesIO(self.export("lee")))
        self.assertIn("activity.json", mine.namelist())
        self.assertTrue(json.loads(mine.read("activity.json")))
        self.assertIn("activity.json      who opened", mine.read("README.txt").decode())
        self.assertNotIn("activity.json", theirs.namelist())
        self.assertNotIn("activity.json", theirs.read("README.txt").decode())
        # the rest is the same
        self.assertEqual(sorted(n for n in mine.namelist() if n != "activity.json"), sorted(theirs.namelist()))


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    unittest.main(verbosity=2)
