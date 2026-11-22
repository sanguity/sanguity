#!/usr/bin/env python3
"""Import history with undo (#24: server/history.py, server/routes_imports.py, watch_store's per-import summaries).

Done when undoing an import restores the profile's timeline, trends and costs (/data), its Health tab (/watch) and
settings to exactly what they were before it. Also: undoing one of two keeps the other, an Apple Health import undone
beside a receiver's file leaves the receiver's data alone, roles, raw files kept while another import uses them, setup
build imports refused, command-line imports undone with a note, the activity log, and the start-up migration of
watch data imported before #24. Fictional data only.

  python3 tests/test_import_history.py
"""
import io, json, os, shutil, sys, tempfile, time, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-history-test-")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "no-build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"))
os.makedirs(os.path.join(TMP, "records"))
os.makedirs(os.path.join(TMP, "keys"))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, activity, crypto, db as dbm, history, jobs, main, profiles as prof, records, uploads, watch_store   # noqa: E402
from tests.whole_records import whole   # noqa: E402  records with every record's details (#217)
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"ines": "harbour lantern pebble crow", "tomas": "seventeen quiet orange kites", "vera": "maple tunnel brisk saddle"}
FIX = os.path.join(ROOT, "tests", "fixtures")
SANGUITY = os.path.join(FIX, "importers", "sanguity", "export")
FHIR = os.path.join(FIX, "importers", "fhir", "bundle")
APPLE = os.path.join(FIX, "apple_health", "apple_health_export", "export.xml")


def folder_files(top, prefix=""):
    out = {}
    for d, _, names in os.walk(top):
        for n in names:
            p = os.path.join(d, n)
            with open(p, "rb") as f:
                out[prefix + os.path.relpath(p, top).replace(os.sep, "/")] = f.read()
    return out


def apple_files():
    with open(APPLE, "rb") as f:
        return {"apple_health_export/export.xml": f.read()}


def summary(metrics, exercise=()):
    return {"updated": "2024-01-01T00:00:00+00:00", "tz": "UTC", "last_payload": None,
            "metrics": [{"key": k, "name": k, "unit": "u", "group": "Activity", "pts": sorted(p.items())} for k, p in metrics.items()],
            "exercise": list(exercise)}


class History(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = TestClient(main.app, base_url="http://testserver").__enter__()   # lifespan: database, key, the worker
        cls.db = dbm.connect()
        cls.uids = {n: accounts.create(cls.db, n, password=pw)[0] for n, pw in PW.items()}
        cls.ines, cls.tomas, cls.vera = (cls.login(n) for n in ("ines", "tomas", "vera"))

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.ctx.__exit__(None, None, None)
        shutil.rmtree(TMP, ignore_errors=True)

    @staticmethod
    def login(name):
        c = TestClient(main.app, base_url="http://testserver")
        r = c.post("/api/login", json={"username": name, "password": PW[name]}, headers=H)
        assert r.status_code == 200, r.text
        return c

    # ---------------------------------------------------------------- helpers

    def profile(self, watch_file=None):
        """A profile Ines owns, Tomas views and Vera can't see."""
        pid = prof.create_profile(self.db, "Ines (fictional)", owner_id=self.uids["ines"], watch_file=watch_file)
        self.db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (self.uids["tomas"], pid))
        return pid

    def upload(self, pid, files):
        up = uploads.create(self.db, pid, self.uids["ines"])
        for rel, data in files.items():
            uploads.add_file(self.db, pid, up, rel, io.BytesIO(data))
        uploads.finish(self.db, pid, up)
        return up

    def wait(self, pid, jid, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            j = self.ines.get(f"/api/profiles/{pid}/jobs/{jid}").json()
            if j["state"] == "preview":   # since #22 an import waits at its preview: save it, as the page's Save does,
                # keeping any duplicates (these tests import the same files more than once on purpose)
                keep = [i["ref"] for i in ((j.get("duplicates") or {}).get("items") or []) if i["ref"]]   # (a list's entry has none, #497)
                r = self.ines.post(f"/api/profiles/{pid}/jobs/{jid}/confirm", json={"keep": keep}, headers=H)
                self.assertEqual(r.status_code, 202, r.text)
            elif j["state"] in ("done", "failed"):
                self.assertEqual(j["state"], "done", j)
                return j
            time.sleep(0.05)
        self.fail(f"job {jid} didn't finish")

    def imported(self, pid, files):
        """Upload files and import them through the API, as the page does. Returns the job."""
        up = self.upload(pid, files)
        r = self.ines.post(f"/api/profiles/{pid}/uploads/{up}/import", json={}, headers=H)
        self.assertEqual(r.status_code, 202, r.text)
        return self.wait(pid, r.json()["id"])

    def state(self, pid):
        """What the profile's pages read: records (timeline, trends, costs), watch summary, settings."""
        out = {}
        for part in ("data", "watch", "settings"):
            r = self.ines.get(f"/api/profiles/{pid}/{part}")
            self.assertIn(r.status_code, (200, 204), r.text)
            out[part] = (r.status_code, (whole(self.ines, pid) if part == "data" else r.json()) if r.status_code == 200 else None)   # #217
        return out

    def undo(self, pid, iid, c=None, status=200):
        r = (c or self.ines).post(f"/api/profiles/{pid}/imports/{iid}/undo", headers=H)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def history(self, pid, c=None):
        r = (c or self.ines).get(f"/api/profiles/{pid}/imports")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    # ---------------------------------------------------------------- done when

    def test_undo_restores_exactly_what_was_there(self):
        pid = self.profile()
        empty = self.state(pid)
        self.assertEqual(empty["data"][0], 204)
        a = self.imported(pid, folder_files(SANGUITY))
        one = self.state(pid)
        self.assertTrue(one["data"][1]["costs"], "the fixture has costs, so the Costs tab is compared too")
        b = self.imported(pid, folder_files(FHIR, "fhir-export/"))
        c = self.imported(pid, apple_files())
        three = self.state(pid)
        self.assertNotEqual(three["data"], one["data"])
        self.assertEqual(three["watch"][0], 200)

        self.undo(pid, c["import_id"])   # the Apple Health one: the watch summary goes, records stay
        after_c = self.state(pid)
        self.assertEqual(three["data"][1]["imports"].pop(str(c["import_id"]))["label"], "Apple Health (export.xml)")
        self.assertEqual(after_c["data"], three["data"])   # only its entry in the list of sources (#100) is gone
        self.assertEqual(after_c["watch"][0], 204)
        self.undo(pid, b["import_id"])
        self.assertEqual(self.state(pid), one)   # exactly as before the FHIR import: the JSON compares equal
        self.undo(pid, a["import_id"])
        self.assertEqual(self.state(pid), empty)
        self.assertIsNone(self.db.execute("SELECT watch_file FROM profiles WHERE id=?", (pid,)).fetchone()[0])
        for t in ("imports", "events", "series", "results", "documents"):
            self.assertEqual(self.db.execute(f"SELECT COUNT(*) FROM {t} WHERE profile_id=?", (pid,)).fetchone()[0], 0, t)

    def test_undoing_the_first_of_two_keeps_the_second(self):
        pid = self.profile()
        a = self.imported(pid, folder_files(SANGUITY))
        b = self.imported(pid, folder_files(FHIR, "fhir-export/"))
        before = self.state(pid)
        self.undo(pid, a["import_id"])
        self.assertEqual(records.imports(self.db, pid), [b["import_id"]])
        data = self.state(pid)["data"][1]
        fhir_titles = {e["title"] for e in before["data"][1]["events"]} - {e["title"] for e in self.sanguity_events()}
        self.assertTrue(fhir_titles and fhir_titles <= {e["title"] for e in data["events"]})
        kept = {r[0] for r in self.db.execute("SELECT id FROM events WHERE import_id=?", (b["import_id"],))}
        self.assertEqual({e["id"] for e in data["events"]}, kept)   # the FHIR import's events, and only those
        self.assertEqual(len(data["labs"]), self.db.execute("SELECT COUNT(*) FROM series WHERE import_id=?", (b["import_id"],)).fetchone()[0])
        # the FHIR import's attachments still open
        doc = next(d for d in data["details"].values() if isinstance(d, dict) and d.get("type") == "file" and d.get("url"))
        self.assertEqual(self.ines.get("/" + doc["url"].split("#")[0]).status_code, 200)

    @staticmethod
    def sanguity_events():
        with open(os.path.join(SANGUITY, "data.json"), encoding="utf-8") as f:
            return json.load(f)["events"]

    # ---------------------------------------------------------------- watch data

    def receiver(self, data):
        d = tempfile.mkdtemp(prefix="receiver-", dir=TMP)
        path = os.path.join(d, "daily.json")
        crypto.write_file(path, json.dumps(data).encode())
        return path

    def test_apple_health_undone_beside_a_receiver(self):
        rx = self.receiver(summary({"steps": {"2024-03-09": 5000, "2024-03-10": 6000}, "hr_rest": {"2024-03-10": 52}}))
        with open(rx, "rb") as f:
            rx_bytes = f.read()
        pid = self.profile(watch_file=rx)
        before = self.state(pid)
        self.assertEqual(before["watch"][0], 200)
        a = self.imported(pid, apple_files())
        self.assertNotEqual(self.state(pid)["watch"], before["watch"])
        self.assertTrue(os.path.isfile(watch_store.layer_path(pid, a["import_id"])))
        self.undo(pid, a["import_id"])
        self.assertEqual(self.state(pid), before)
        self.assertEqual(self.db.execute("SELECT watch_file FROM profiles WHERE id=?", (pid,)).fetchone()[0], rx)
        with open(rx, "rb") as f:
            self.assertEqual(f.read(), rx_bytes)   # the receiver's file is never written to
        self.assertFalse(os.path.exists(watch_store.side_path(pid)))
        self.assertFalse(os.path.exists(watch_store.layer_path(pid, a["import_id"])))

    def test_overlapping_watch_imports_undo_to_the_earlier(self):
        pid = self.profile()
        first = summary({"steps": {"2024-03-01": 100, "2024-03-02": 200}})
        second = summary({"steps": {"2024-03-02": 250, "2024-03-03": 300}})
        ids = []
        for s in (first, second):
            with dbm.transaction(self.db):
                iid = records.store(self.db, pid, {"events": []}, "upload", fmt="apple-health")
                watch_store.save_import(self.db, pid, s, import_id=iid)
            ids.append(iid)
        merged = self.state(pid)
        self.undo(pid, ids[1])
        got = self.state(pid)["watch"][1]
        self.assertEqual(dict(got["metrics"][0]["pts"]), {"2024-03-01": 100, "2024-03-02": 200})   # the earlier days back
        self.undo(pid, ids[0])
        self.assertEqual(self.state(pid)["watch"][0], 204)
        self.assertNotEqual(merged["watch"][0], 204)

    def test_watch_data_from_before_is_kept_and_cant_be_undone(self):
        pid = self.profile()
        own = watch_store.own_path(pid)
        os.makedirs(os.path.dirname(own), exist_ok=True)
        crypto.write_file(own, json.dumps(summary({"steps": {"2023-12-01": 999}})).encode())   # as save_import wrote it before #24
        self.db.execute("UPDATE profiles SET watch_file=? WHERE id=?", (own, pid))
        before = self.state(pid)
        self.assertGreaterEqual(watch_store.migrate(self.db), 1)
        self.assertTrue(os.path.isfile(watch_store.layer_path(pid)))
        self.assertEqual(self.state(pid), before)
        a = self.imported(pid, apple_files())
        self.undo(pid, a["import_id"])
        self.assertEqual(self.state(pid), before)   # the legacy data stays
        self.assertEqual(watch_store.migrate(self.db), 0)   # once only

    def test_start_up_drops_summaries_of_imports_that_are_gone(self):
        pid = self.profile()
        with dbm.transaction(self.db):
            iid = records.store(self.db, pid, {"events": []}, "upload", fmt="apple-health")
            watch_store.save_import(self.db, pid, summary({"steps": {"2024-05-01": 1}}), import_id=iid)
        records.drop(self.db, pid, [iid])   # as if its saving had failed after the file was written
        watch_store.migrate(self.db)
        self.assertFalse(os.path.exists(watch_store.layer_path(pid, iid)))
        self.assertEqual(self.state(pid)["watch"][0], 204)

    # ---------------------------------------------------------------- who may

    def test_roles(self):
        pid = self.profile()
        a = self.imported(pid, folder_files(SANGUITY))
        h = self.history(pid, self.tomas)   # a viewer sees the history...
        self.assertFalse(h["can_undo"])
        self.assertEqual([e["id"] for e in h["imports"]], [a["import_id"]])
        self.undo(pid, a["import_id"], self.tomas, 403)   # ...but can't undo
        self.assertEqual(self.vera.get(f"/api/profiles/{pid}/imports").status_code, 404)   # a stranger sees nothing
        self.undo(pid, a["import_id"], self.vera, 404)
        self.assertEqual(TestClient(main.app).get(f"/api/profiles/{pid}/imports").status_code, 401)
        r = self.ines.post(f"/api/profiles/{pid}/imports/{a['import_id']}/undo")   # no X-Sanguity: the CSRF check
        self.assertEqual(r.status_code, 403)
        self.assertEqual(records.imports(self.db, pid), [a["import_id"]])   # still there
        other = self.profile()
        self.undo(other, a["import_id"], status=404)   # an import of another profile
        self.assertTrue(self.history(pid)["can_undo"])

    # ---------------------------------------------------------------- what the history says

    def test_history_lists_and_keeps_undone_imports(self):
        pid = self.profile()
        a = self.imported(pid, folder_files(SANGUITY, "my-export/"))
        b = self.imported(pid, apple_files())
        h = self.history(pid)["imports"]
        self.assertEqual([e["id"] for e in h], [b["import_id"], a["import_id"]])   # newest first
        e = h[1]
        self.assertEqual((e["origin"], e["format"], e["by"], e["state"]), ("upload", "sanguity", "ines", "active"))
        self.assertEqual(e["label"], "Sanguity data or export")
        self.assertIn("my-export/data.json", e["files"])
        self.assertEqual(e["counts"], a["counts"])
        self.assertTrue(e["can_undo"])
        self.assertTrue(h[0]["watch"])
        undone = self.undo(pid, a["import_id"])
        self.assertEqual(undone["state"], "undone")
        h = self.history(pid)["imports"]
        self.assertEqual([(x["id"], x["state"]) for x in h], [(b["import_id"], "active"), (a["import_id"], "undone")])
        self.assertEqual(h[1]["undone_by"], "ines")
        self.assertIn("my-export/data.json", h[1]["files"])   # though the upload itself is gone
        self.undo(pid, a["import_id"], status=404)   # not twice
        # logged
        log = activity.for_profile(self.db, pid, action="undo")
        self.assertEqual(len(log), 1)
        self.assertEqual((log[0]["actor"], log[0]["detail"]["import"], log[0]["detail"]["importer"]), ("ines", a["import_id"], "sanguity"))
        # kept encrypted
        raw = self.db.execute("SELECT entry FROM import_undos WHERE profile_id=?", (pid,)).fetchone()[0]
        self.assertNotIn("my-export", raw)

    def test_an_undone_imports_id_never_comes_again(self):   # #512
        pid = self.profile()
        a = self.imported(pid, apple_files())
        self.undo(pid, a["import_id"])
        b = self.imported(pid, apple_files())
        self.assertGreater(b["import_id"], a["import_id"], "the next import has an id of its own, not the undone one's")
        r = self.ines.post(f"/api/profiles/{pid}/imports/{a['import_id']}/undo", headers=H)   # a stale Undo of A
        self.assertEqual((r.status_code, r.json()["detail"]), (404, "This import was undone already."))
        self.assertEqual(records.imports(self.db, pid), [b["import_id"]], "B is left as it was")
        at = next(e["at"] for e in self.history(pid)["imports"] if e["id"] == b["import_id"])
        r = self.ines.post(f"/api/profiles/{pid}/imports/{b['import_id']}/undo?at={at - 1}", headers=H)   # not the one asked
        self.assertEqual(r.status_code, 404, r.text)
        self.assertEqual(records.imports(self.db, pid), [b["import_id"]])
        r = self.ines.post(f"/api/profiles/{pid}/imports/{b['import_id']}/undo?at={at}", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual([(e["id"], e["state"]) for e in self.history(pid)["imports"]],
                         [(b["import_id"], "undone"), (a["import_id"], "undone")])

    def test_an_undo_racing_its_profiles_deletion(self):   # #542
        """The profile deleted between the import being read and its undo's transaction: refused as gone (404), not
        a failed insert (500)."""
        pid = self.profile()
        a = self.imported(pid, apple_files())
        real = history._entry

        def deleted_meanwhile(db, p, imp):
            out = real(db, p, imp)
            other = dbm.connect()
            try:
                prof.delete_profile(other, p)
            finally:
                other.close()
            return out
        history._entry = deleted_meanwhile
        try:
            r = self.ines.post(f"/api/profiles/{pid}/imports/{a['import_id']}/undo", headers=H)
        finally:
            history._entry = real
        self.assertEqual(r.status_code, 404, r.text)

    # ---------------------------------------------------------------- documents and raw files

    def test_raw_files_go_with_the_import(self):
        pid = self.profile()
        a = self.imported(pid, folder_files(FHIR, "fhir-export/"))
        up = a["upload_id"]
        docs = [d["url"] for d in self.state(pid)["data"][1]["details"].values() if isinstance(d, dict) and d.get("url")]
        self.assertTrue(docs)
        self.undo(pid, a["import_id"])
        self.assertEqual(self.db.execute("SELECT state FROM uploads WHERE id=?", (up,)).fetchone()[0], "discarded")
        self.assertFalse(os.path.exists(uploads.folder(pid, up)))
        for url in docs:
            self.assertEqual(self.ines.get("/" + url.split("#")[0]).status_code, 404)

    def test_raw_files_kept_while_another_import_uses_them(self):
        pid = self.profile()
        first = self.imported(pid, folder_files(FHIR, "fhir-export/"))
        up = first["upload_id"]
        second = self.wait(pid, self.second_import(pid, up))   # the same upload imported again (not offered by the page)
        self.undo(pid, first["import_id"])
        self.assertEqual(self.db.execute("SELECT state FROM uploads WHERE id=?", (up,)).fetchone()[0], "ready")
        self.assertTrue(os.listdir(uploads.folder(pid, up)))
        data = self.state(pid)["data"][1]
        urls = [d["url"] for d in data["details"].values() if isinstance(d, dict) and d.get("url")]
        self.assertTrue(urls)
        for url in urls:   # the second import's documents, stored in that upload, still open
            self.assertEqual(self.ines.get("/" + url.split("#")[0]).status_code, 200)
        self.undo(pid, second["import_id"])
        self.assertFalse(os.path.exists(uploads.folder(pid, up)))

    def second_import(self, pid, up):
        jid = jobs.submit(self.db, pid, up, self.uids["ines"])
        return jid

    # ---------------------------------------------------------------- by origin

    def test_setup_build_imports_are_refused(self):
        pid = self.profile()
        with open(os.path.join(SANGUITY, "data.json"), encoding="utf-8") as f:
            data = json.load(f)
        with dbm.transaction(self.db):
            iid = records.store(self.db, pid, data, "build")
        e = self.history(pid)["imports"][0]
        self.assertEqual((e["origin"], e["by"], e["can_undo"]), ("build", "setup build", False))
        self.assertIn("rebuild", e["note"])
        before = self.state(pid)
        r = self.ines.post(f"/api/profiles/{pid}/imports/{iid}/undo", headers=H)
        self.assertEqual(r.status_code, 409)
        self.assertIn("setup build", r.json()["detail"])
        self.assertEqual(self.state(pid), before)

    def test_command_line_imports_undo_with_a_note(self):
        pid = self.profile()
        prof.import_dataset(self.db, pid, SANGUITY)
        e = self.history(pid)["imports"][0]
        self.assertEqual((e["origin"], e["by"], e["can_undo"]), ("dataset", "command line", True))
        self.assertIn("can't be brought back", e["note"])
        self.assertEqual(e["counts"]["events"], 5)
        self.undo(pid, e["id"])
        self.assertEqual(self.state(pid)["data"][0], 204)

    def test_undo_rolls_back_whole(self):
        pid = self.profile()
        a = self.imported(pid, apple_files())
        before = self.state(pid)
        real = activity.log

        def broken(*args, **kw):
            raise RuntimeError("the disk filled up")
        activity.log = broken
        try:
            with self.assertRaises(RuntimeError):
                history.undo(self.db, pid, a["import_id"], {"id": self.uids["ines"], "username": "ines"})
        finally:
            activity.log = real
        self.assertEqual(self.state(pid), before)
        self.assertTrue(os.path.isfile(watch_store.layer_path(pid, a["import_id"])))
        self.assertEqual(self.db.execute("SELECT state FROM uploads WHERE id=?", (a["upload_id"],)).fetchone()[0], "ready")

        def failing_rebuild(db, p, **kw):   # the watch step itself fails: its summary is put back
            raise OSError("no space left")
        real_rebuild, watch_store.rebuild = watch_store.rebuild, failing_rebuild
        try:
            with self.assertRaises(OSError):
                history.undo(self.db, pid, a["import_id"], {"id": self.uids["ines"], "username": "ines"})
        finally:
            watch_store.rebuild = real_rebuild
        self.assertEqual(self.state(pid), before)
        self.assertTrue(os.path.isfile(watch_store.layer_path(pid, a["import_id"])))
        self.assertEqual(records.imports(self.db, pid), [a["import_id"]])
        self.undo(pid, a["import_id"])   # and it can still be undone
        self.assertEqual(self.state(pid)["watch"][0], 204)



class Schema(unittest.TestCase):
    def test_version_37_keeps_every_import_and_what_points_at_it(self):   # #512
        import sqlite3
        d = tempfile.mkdtemp(prefix="sanguity-schema37-")
        self.addCleanup(shutil.rmtree, d, True)
        db = sqlite3.connect(os.path.join(d, "schema-36.db"), isolation_level=None)
        db.execute("PRAGMA foreign_keys=ON")   # as the app's connections have it
        for v, sql in enumerate(dbm.SCHEMA[:36], start=1):
            db.executescript("BEGIN;" + sql + f"PRAGMA user_version={v};COMMIT;")
        db.execute("INSERT INTO users(id, username, pw_hash, created) VALUES(1, 'ines', 'x', 1)")
        db.execute("INSERT INTO profiles(id, name, created) VALUES(1, 'Fictional', 1)")
        for iid in (1, 2, 4):
            db.execute("INSERT INTO imports(id, profile_id, at, origin, format, meta, name, created_by) VALUES(?, 1, ?, 'upload', 'csv', 'm', 'n', 1)", (iid, 10 * iid))
        db.execute("INSERT INTO events(id, profile_id, import_id, body) VALUES(7, 1, 2, 'e')")
        db.execute("INSERT INTO series(id, profile_id, import_id, body) VALUES(8, 1, 2, 's')")
        db.execute("INSERT INTO results(id, profile_id, import_id, series_id, body) VALUES(9, 1, 2, 8, 'r')")
        db.execute("INSERT INTO documents(id, profile_id, path, ext, import_id) VALUES('doc', 1, 'p', 'pdf', 4)")
        db.execute("INSERT INTO uploads(id, profile_id, created, state) VALUES(1, 1, 1, 'ready')")
        db.execute("INSERT INTO jobs(id, profile_id, upload_id, state, import_id, created) VALUES(1, 1, 1, 'done', 2, 1)")
        db.execute("INSERT INTO labmaps(lookup, importer, source, created_name, created, import_id) VALUES('l', 'csv', 's', 'ines', 1, 4)")
        db.execute("INSERT INTO import_undos(profile_id, import_id, at, by_name, entry) VALUES(1, 6, 60, 'ines', 'gone')")   # the newest, undone
        db.execute("INSERT INTO activity(at, profile_id, actor, action, detail) VALUES(60, 1, 'ines', 'undo', '{}')")
        tables = ("imports", "events", "series", "results", "documents", "jobs", "labmaps", "import_undos", "activity")
        cols = {t: ", ".join(r[1] for r in db.execute(f"PRAGMA table_info({t})")) for t in tables}   # (later versions add some)
        before = {t: db.execute(f"SELECT {cols[t]} FROM {t} ORDER BY 1").fetchall() for t in tables}

        dbm.upgrade(db)
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], len(dbm.SCHEMA))
        self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1, "foreign keys on again")
        self.assertEqual({t: db.execute(f"SELECT {cols[t]} FROM {t} ORDER BY 1").fetchall() for t in tables}, before)
        self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual({r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE tbl_name='imports' AND type='index'")},
                         {"imports_profile", "imports_connection"})
        self.assertNotIn("imports_37", "".join(r[0] or "" for r in db.execute("SELECT sql FROM sqlite_master")))
        new = db.execute("INSERT INTO imports(profile_id, at, origin, format) VALUES(1, 70, 'upload', 'csv')").lastrowid
        self.assertEqual(new, 7, "after the highest id there has been, the undone one's included")
        db.execute("DELETE FROM imports WHERE id=?", (new,))
        self.assertEqual(db.execute("INSERT INTO imports(profile_id, at, origin, format) VALUES(1, 80, 'upload', 'csv')").lastrowid, 8)
        db.execute("DELETE FROM imports WHERE id=2")   # the references still cascade (and set null) from the new table
        self.assertEqual([db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("events", "series", "results")], [0, 0, 0])
        self.assertIsNone(db.execute("SELECT import_id FROM jobs WHERE id=1").fetchone()[0])
        db.execute("DELETE FROM imports WHERE id=4")
        self.assertEqual(db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)
        self.assertIsNone(db.execute("SELECT import_id FROM labmaps").fetchone()[0])
        db.close()


if __name__ == "__main__":
    unittest.main()
