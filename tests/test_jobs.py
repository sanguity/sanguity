#!/usr/bin/env python3
"""Background imports (server/jobs.py, server/routes_jobs.py): an upload imported through the registry, the message
when nothing recognises it, nothing left behind by a failed import, roles, jobs a stop interrupted, the API staying
responsive while a slow importer runs, and documents from an upload opening through the document route.

Uploads go through server/uploads.py (#19), as the import page's do. Fictional data only.

  python3 tests/test_jobs.py
"""
import contextlib, io, json, os, shutil, sys, tempfile, threading, time, types, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-jobs-test-")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "no-build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"))
os.makedirs(os.path.join(TMP, "records"))
os.makedirs(os.path.join(TMP, "keys"))
sys.path.insert(0, ROOT)

from server import crypto, profiles as prof   # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "importers", "sanguity", "export")


from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, activity, db as dbm, importers as reg, jobs, main, records, uploads   # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
logging.getLogger("sanguity.jobs").setLevel(logging.CRITICAL)   # failures are expected here; their tracebacks are noise
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites", "lee": "maple tunnel brisk saddle"}


# ---------------------------------------------------------------- stand-in importers, alongside the real ones

GATE = threading.Event()


def _data(title="Fictional visit", file=None):
    d = {"events": [{"id": 1, "t": "2025-04-01T10:00:00", "k": "visit", "title": title}], "details": {}, "labs": [], "costs": []}
    if file:
        d["details"]["1"] = {"type": "file", "url": file}
    return d


def _marker(name):
    return lambda files: 1.0 if f"{name}.txt" in files else 0.0


def _slow(files):
    GATE.wait(20)
    return {"data": _data("Slow visit"), "warnings": []}


def _boom(files):
    raise RuntimeError("an importer bug, halfway through")


FAKES = [types.SimpleNamespace(NAME="slow", LABEL="Slow test format", detect=_marker("slow"), parse=_slow),
         types.SimpleNamespace(NAME="boom", LABEL="Broken test format", detect=_marker("boom"), parse=_boom),
         types.SimpleNamespace(NAME="badshape", LABEL="Bad shape test format", detect=_marker("badshape"),
                               parse=lambda files: {"data": {"events": [{"id": 1, "t": "2025-01-01"}], "details": {}, "labs": [], "costs": []}}),
         types.SimpleNamespace(NAME="withdoc", LABEL="Document test format", detect=_marker("withdoc"),
                               parse=lambda files: {"data": _data("Visit with a note", "notes/visit.txt"), "warnings": ["one warning"]})]
_real_importers = reg.importers


def client():
    return TestClient(main.app, base_url="http://testserver")


def count(pid):
    db = dbm.connect()
    try:
        return {t: db.execute(f"SELECT COUNT(*) FROM {t} WHERE profile_id=?", (pid,)).fetchone()[0]
                for t in ("imports", "events", "series", "results", "documents")}
    finally:
        db.close()


class Jobs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patch = mock.patch.object(reg, "importers", lambda: reg.accept(list(_real_importers()) + FAKES))
        cls.patch.start()
        cls.ctx = client().__enter__()   # lifespan: database, key, the worker
        db = dbm.connect()
        uids = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        cls.pid = prof.create_profile(db, "Robin (fictional)", owner_id=uids["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (uids["kim"], cls.pid))
        cls.other = prof.create_profile(db, "Lee (fictional)", owner_id=uids["lee"])
        db.close()
        cls.robin, cls.kim, cls.lee = cls.login("robin"), cls.login("kim"), cls.login("lee")

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        cls.patch.stop()
        GATE.set()

    @staticmethod
    def login(name):
        c = client()
        r = c.post("/api/login", json={"username": name, "password": PW[name]}, headers=H)
        assert r.status_code == 200, r.text
        return c

    def upload(self, files, pid=None, finish=True):
        db = dbm.connect()
        try:
            pid = pid or self.pid
            up = uploads.create(db, pid, None)
            for rel, data in files.items():
                uploads.add_file(db, pid, up, rel, io.BytesIO(data if isinstance(data, bytes) else data.encode()))
            if finish:
                uploads.finish(db, pid, up)
            return up
        finally:
            db.close()

    def fixture_upload(self):
        files = {}
        for d, _, names in os.walk(FIXTURE):
            for n in names:
                p = os.path.join(d, n)
                with open(p, "rb") as f:
                    files[os.path.relpath(p, FIXTURE).replace(os.sep, "/")] = f.read()
        return self.upload(files)

    def start(self, up, c=None, body=None, pid=None):
        r = (c or self.robin).post(f"/api/profiles/{pid or self.pid}/uploads/{up}/import", json=body, headers=H)
        self.assertEqual(r.status_code, 202, r.text)
        return r.json()["id"]

    def wait(self, jid, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            j = self.robin.get(f"/api/profiles/{self.pid}/jobs/{jid}").json()
            if j["state"] in ("done", "failed"):
                return j
            time.sleep(0.05)
        self.fail(f"job {jid} didn't finish: {j}")

    # ---------------------------------------------------------------- the tests

    def test_apple_health_upload_goes_to_the_watch_summary(self):   # wearable data isn't stored as records
        path = os.path.join(ROOT, "tests", "fixtures", "apple_health", "apple_health_export", "export.xml")
        with open(path, "rb") as f:
            up = self.upload({"apple_health_export/export.xml": f.read()})
        j = self.wait(self.start(up))
        self.assertEqual((j["state"], j["importer"]), ("done", "apple-health"), j)
        self.assertGreater(j["counts"]["watch_days"], 0)
        self.assertGreater(j["counts"]["workouts"], 0)
        w = self.robin.get(f"/api/profiles/{self.pid}/watch")
        self.assertEqual(w.status_code, 200)
        self.assertTrue({m["key"] for m in w.json()["metrics"]} >= {"steps"})
        self.assertNotIn("watch", self.robin.get(f"/api/profiles/{self.pid}/data").json())   # not copied into the records
        self.assertEqual(self.kim.post(f"/api/profiles/{self.pid}/uploads/{up}/import", headers=H).status_code, 403)

    def test_imports_through_the_registry(self):
        before = count(self.pid)
        jid = self.start(self.fixture_upload())
        j = self.wait(jid)
        self.assertEqual(j["state"], "done", j)
        self.assertEqual((j["importer"], j["label"]), ("sanguity", reg.get("sanguity").LABEL))
        self.assertIsNone(j["message"])
        self.assertEqual(j["counts"]["documents"], 1)   # the fixture links two; scan.png isn't in it (a warning says so)
        self.assertTrue(any("not in the upload" in w for w in j["warnings"]), j["warnings"])
        self.assertGreater(j["counts"]["events"], 0)
        self.assertGreater(j["counts"]["results"], 0)
        self.assertRegex(j["range"]["from"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertLessEqual(j["range"]["from"], j["range"]["to"])
        after = count(self.pid)
        self.assertEqual(after["imports"], before["imports"] + 1)
        self.assertEqual(after["documents"], before["documents"] + 1)

        data = self.robin.get(f"/api/profiles/{self.pid}/data").json()
        with open(os.path.join(FIXTURE, "data.json"), encoding="utf-8") as f:
            titles = {e["title"] for e in json.load(f)["events"]}
        self.assertTrue(titles <= {e["title"] for e in data["events"]})

        db = dbm.connect()
        try:
            r = db.execute("SELECT origin, format FROM imports WHERE id=?", (j["import_id"],)).fetchone()
            self.assertEqual(tuple(r), ("upload", "sanguity"))
            # logged, with the importer and counts
            a = [x for x in activity.for_profile(db, self.pid, action="import")
                 if x["detail"].get("job") == jid]
            self.assertEqual(len(a), 1)
            self.assertEqual(a[0]["actor"], "robin")
            self.assertEqual(a[0]["detail"]["importer"], "sanguity")
            self.assertEqual(a[0]["detail"]["counts"], j["counts"])
        finally:
            db.close()

        # listed, newest first
        listed = self.robin.get(f"/api/profiles/{self.pid}/jobs").json()
        self.assertIn(jid, [x["id"] for x in listed])
        # the same upload isn't imported twice
        r = self.robin.post(f"/api/profiles/{self.pid}/uploads/{j['upload_id']}/import", headers=H)
        self.assertEqual(r.status_code, 409)

    def test_uploads_add_to_the_records(self):
        first = self.wait(self.start(self.upload({"withdoc.txt": "x", "notes/visit.txt": "Fictional note."})))
        n = len(self.robin.get(f"/api/profiles/{self.pid}/data").json()["events"])
        second = self.wait(self.start(self.upload({"withdoc.txt": "x", "notes/visit.txt": "Another fictional note."})))
        self.assertEqual((first["state"], second["state"]), ("done", "done"))
        self.assertEqual(len(self.robin.get(f"/api/profiles/{self.pid}/data").json()["events"]), n + 1)

    def test_documents_open_through_the_document_route(self):
        up = self.upload({"withdoc.txt": "x", "notes/visit.txt": "A fictional visit note."})
        j = self.wait(self.start(up))
        self.assertEqual(j["state"], "done", j)
        self.assertEqual(j["warnings"], ["one warning"])
        db = dbm.connect()
        try:
            rows = db.execute("SELECT id, path FROM documents WHERE import_id=?", (j["import_id"],)).fetchall()
            event = db.execute("SELECT detail FROM events WHERE import_id=?", (j["import_id"],)).fetchone()
        finally:
            db.close()
        db = dbm.connect()
        try:
            sealed = [os.path.realpath(p) for i, rel, p in uploads.stored_files(db, self.pid) if i == up and rel == "notes/visit.txt"]
        finally:
            db.close()
        self.assertEqual([crypto.dec(r["path"]) for r in rows], sealed)   # the stored copy, not the temporary one
        self.assertEqual(len(sealed), 1)
        sealed = sealed[0]
        self.assertTrue(crypto.file_is_encrypted(sealed))
        url = json.loads(crypto.dec(event["detail"]))["url"]
        self.assertEqual(url, f"api/profiles/{self.pid}/documents/{rows[0]['id']}")
        r = self.robin.get("/" + url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "text/plain; charset=utf-8")
        self.assertEqual(r.content, b"A fictional visit note.")
        self.assertIn("script-src 'none'", r.headers["content-security-policy"])
        self.assertEqual(self.kim.get("/" + url).status_code, 200)   # viewers read the records, documents included
        self.assertEqual(self.lee.get("/" + url).status_code, 404)
        # an upload that isn't finished, or was discarded since, fails with uploads.py's own message
        j = self.wait(self.start(self.upload({"withdoc.txt": "x"}, finish=False)))
        self.assertEqual((j["state"], j["message"]), ("failed", "This upload isn't finished. Nothing was saved."))
        gone = self.upload({"withdoc.txt": "x"})
        db = dbm.connect()
        uploads.discard(db, self.pid, gone)
        db.close()
        self.assertEqual(self.wait(self.start(gone))["message"], "This upload was discarded. Nothing was saved.")

    def test_unrecognised_upload(self):
        before = count(self.pid)
        j = self.wait(self.start(self.upload({"holiday.jpg": b"\xff\xd8not records"})))
        self.assertEqual(j["state"], "failed")
        self.assertIn("didn't recognise these files", j["message"])
        self.assertIn("C-CDA" if reg.get("ccda") else reg.get("sanguity").LABEL, j["message"])
        self.assertIn("https://github.com/sanguity/sanguity/issues", j["message"])
        self.assertIn("anonymised sample", j["message"])
        self.assertIsNone(j["importer"])
        self.assertEqual(count(self.pid), before)

    def test_failures_leave_nothing_behind(self):
        cases = {"raises in parse": ({"boom.txt": "x"}, None),
                 "check() refuses it": ({"badshape.txt": "x"}, None),
                 "raises while saving": ({"withdoc.txt": "x", "notes/visit.txt": "Fictional."}, "save")}
        real_store = records.store
        for name, (files, when) in cases.items():
            with self.subTest(name):
                before = count(self.pid)
                up = self.upload(files)

                def store_then_fail(*a, **k):
                    real_store(*a, **k)   # rows and document rows written, then...
                    raise OSError("disk trouble, made up for the test")

                with mock.patch.object(records, "store", store_then_fail) if when == "save" else contextlib.nullcontext():
                    j = self.wait(self.start(up))
                self.assertEqual(j["state"], "failed")
                self.assertIn("Nothing was saved", j["message"])
                self.assertNotIn("Traceback", j["message"])
                self.assertNotIn("disk trouble", j["message"])
                self.assertNotIn("an importer bug", j["message"])
                self.assertEqual(count(self.pid), before)
                if name == "check() refuses it":
                    self.assertIn("can't load", j["message"])
                if when == "save":
                    self.assertIn("couldn't be saved", j["message"])
                # and it can be tried again after a failure
                self.assertEqual(self.robin.post(f"/api/profiles/{self.pid}/uploads/{up}/import", headers=H).status_code, 202)
                self.wait(self.robin.get(f"/api/profiles/{self.pid}/jobs").json()[0]["id"])

    def test_importer_override(self):
        up = self.upload({"holiday.jpg": b"\xff\xd8"})
        r = self.robin.post(f"/api/profiles/{self.pid}/uploads/{up}/import", json={"importer": "nope"}, headers=H)
        self.assertEqual(r.status_code, 400)
        j = self.wait(self.start(self.upload({"notes.txt": "x", "notes/visit.txt": "y"}), body={"importer": "withdoc"}))
        self.assertEqual((j["state"], j["importer"]), ("done", "withdoc"))

    def test_roles(self):
        up = self.upload({"withdoc.txt": "x", "notes/visit.txt": "y"})
        url = f"/api/profiles/{self.pid}/uploads/{up}/import"
        self.assertEqual(self.kim.post(url, headers=H).status_code, 403)      # a viewer can't start one
        self.assertEqual(self.lee.post(url, headers=H).status_code, 404)      # a stranger isn't told it exists
        self.assertEqual(self.robin.post(url).status_code, 403)               # the CSRF header, as for every change
        self.assertEqual(client().post(url, headers=H).status_code, 401)
        self.assertEqual(self.robin.post(f"/api/profiles/{self.pid}/uploads/nope/import", headers=H).status_code, 404)
        self.assertEqual(self.robin.post(f"/api/profiles/{self.pid}/uploads/9999/import", headers=H).status_code, 404)
        self.assertEqual(self.robin.post(f"/api/profiles/{self.pid}/uploads/..%2f..%2fx/import", headers=H).status_code, 404)
        lee_up = self.upload({"withdoc.txt": "x"}, pid=self.other)   # another profile's upload, by its id
        self.assertEqual(self.robin.post(f"/api/profiles/{self.pid}/uploads/{lee_up}/import", headers=H).status_code, 404)
        jid = self.start(up)
        self.wait(jid)
        for path in (f"/api/profiles/{self.pid}/jobs/{jid}", f"/api/profiles/{self.pid}/jobs"):
            self.assertEqual(self.kim.get(path).status_code, 403)
            self.assertEqual(self.lee.get(path).status_code, 404)             # a stranger can't read one
        self.assertEqual(self.lee.get(f"/api/profiles/{self.other}/jobs/{jid}").status_code, 404)   # nor by its own profile

    def test_api_answers_while_a_slow_import_runs(self):
        GATE.clear()
        try:
            jid = self.start(self.upload({"slow.txt": "x"}))
            end = time.time() + 10
            while self.robin.get(f"/api/profiles/{self.pid}/jobs/{jid}").json()["stage"] != "parsing":
                self.assertLess(time.time(), end, "the slow job never started parsing")
                time.sleep(0.02)
            for _ in range(5):
                t = time.time()
                self.assertEqual(self.robin.get("/api/me").status_code, 200)
                self.assertIn(self.robin.get(f"/api/profiles/{self.pid}/data").status_code, (200, 204))
                self.assertLess(time.time() - t, 2)
            j = self.robin.get(f"/api/profiles/{self.pid}/jobs/{jid}").json()
            self.assertEqual((j["state"], j["stage"], j["importer"]), ("running", "parsing", "slow"))
            # a second job waits its turn
            second = self.start(self.upload({"withdoc.txt": "x", "notes/visit.txt": "y"}))
            self.assertEqual(self.robin.get(f"/api/profiles/{self.pid}/jobs/{second}").json()["state"], "queued")
        finally:
            GATE.set()
        self.assertEqual(self.wait(jid)["state"], "done")
        self.assertEqual(self.wait(second)["state"], "done")

    def test_z_interrupted_jobs_fail_at_start_up(self):   # last: it restarts the app
        db = dbm.connect()
        try:
            up = self.upload({"withdoc.txt": "x"})
            ids = [db.execute("INSERT INTO jobs(profile_id, upload_id, state, stage, created, started) VALUES(?, ?, ?, ?, ?, ?)",
                              (self.pid, up, state, stage, dbm.now(), dbm.now())).lastrowid
                   for state, stage in (("running", "saving"), ("queued", None))]
        finally:
            db.close()
        type(self).ctx.__exit__(None, None, None)
        type(self).ctx = client().__enter__()
        for jid in ids:
            j = self.robin.get(f"/api/profiles/{self.pid}/jobs/{jid}").json()
            self.assertEqual(j["state"], "failed")
            self.assertIn("interrupted; nothing was saved", j["message"])
            self.assertIsNone(j["stage"])
        # and the new worker takes jobs
        self.assertEqual(self.wait(self.start(self.upload({"withdoc.txt": "x", "notes/visit.txt": "y"})))["state"], "done")


if __name__ == "__main__":
    try:
        ok = unittest.main(exit=False, verbosity=2).result.wasSuccessful()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(not ok)
