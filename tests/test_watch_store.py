"""The watch summary store (server/watch_store.py): Apple Health imports saved per profile, merged with a receiver's
daily.json, served by /api/profiles/{pid}/watch, included in "Export everything", and the manage command.

  python3 -m pytest tests/test_watch_store.py
"""
import atexit, io, json, os, shutil, sys, tempfile, unittest, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if "server.config" not in sys.modules:   # alone; run with tests/test_api.py, the app is already set up there
    TMP = tempfile.mkdtemp(prefix="sanguity-watch-test-")
    atexit.register(shutil.rmtree, TMP, True)
    os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"),
                      SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), PASSWORD_MIN_LENGTH="15",
                      SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"), SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"))
    os.makedirs(os.path.join(TMP, "records"), exist_ok=True)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, atrest, config, crypto, db as dbm, export, main, manage, profiles as prof, watch_store   # noqa: E402
from server.importers import apple_health   # noqa: E402

EXPORT_XML = os.path.join(ROOT, "tests", "fixtures", "apple_health", "apple_health_export", "export.xml")
H = {"X-Sanguity": "1"}
PW = "copper lantern harbor quietly"
WREN, OTTO = "watchstore-wren", "watchstore-otto"   # not names tests/test_api.py uses, when both run together


def summary(metrics=None, exercise=None, **top):
    return {"updated": "2024-01-01T00:00:00+00:00", "tz": "UTC", "last_payload": None, **top,
            "metrics": [{"key": k, "name": k, "unit": "u", "group": "Activity", "pts": sorted(p.items())}
                        for k, p in (metrics or {}).items()],
            "exercise": exercise or []}


def pts(s, key):
    return dict(next((m["pts"] for m in s["metrics"] if m["key"] == key), []))


def workout(start, key=None, minutes=30, kind="running"):
    x = {"start": start, "end": start, "name": kind.capitalize(), "kind": kind, "minutes": minutes}
    return {**x, "key": key} if key else x


class Store(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = TestClient(main.app).__enter__()   # app start-up: database, key, pepper
        cls.db = dbm.connect()
        cls.uid, _ = accounts.create(cls.db, WREN, password=PW)
        accounts.create(cls.db, OTTO, password=PW)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.ctx.__exit__(None, None, None)

    def profile(self, watch_file=None, viewer=None):
        pid = prof.create_profile(self.db, "Watch test", owner_id=None if viewer else self.uid, watch_file=watch_file)
        if viewer:
            self.db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, ?)", (self.uid, pid, viewer))
        return pid

    def receiver_file(self, data):
        d = tempfile.mkdtemp(prefix="receiver-", dir=config.DATA_DIR)
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "daily.json")
        crypto.write_file(path, json.dumps(data).encode())
        return path

    def watch_file(self, pid):
        return self.db.execute("SELECT watch_file FROM profiles WHERE id=?", (pid,)).fetchone()[0]

    # ---------------------------------------------------------------- saving
    def test_first_import_into_a_profile_without_a_watch_file(self):
        pid = self.profile()
        self.assertIsNone(watch_store.load(self.db, pid))
        imported = apple_health.parse({"export.xml": EXPORT_XML})["data"]["watch"]
        stored = watch_store.save_import(self.db, pid, imported)
        self.assertEqual(stored["path"], watch_store.own_path(pid))
        self.assertEqual(self.watch_file(pid), watch_store.own_path(pid))
        self.assertFalse(os.path.exists(watch_store.side_path(pid)))
        got = watch_store.load(self.db, pid)
        self.assertEqual(got["metrics"], imported["metrics"])
        self.assertEqual(got["exercise"], imported["exercise"])
        self.assertEqual((stored["days"], stored["workouts"]), (5, 2))   # 9, 10, 11 (home readings, #223), 12 and 13 March

    def test_file_on_disk_is_encrypted(self):
        pid = self.profile()
        watch_store.save_import(self.db, pid, summary({"steps": {"2024-03-09": 1234}}))
        path = watch_store.own_path(pid)
        self.assertTrue(crypto.file_is_encrypted(path))
        with open(path, "rb") as f:
            raw = f.read()
        self.assertNotIn(b"steps", raw)
        self.assertNotIn(b"2024-03-09", raw)
        self.assertEqual(pts(json.loads(crypto.read_file(path)), "steps"), {"2024-03-09": 1234})
        # inside the profile's folder: what encryption at rest re-seals, backups copy and deletion removes
        self.assertIn(path, list(atrest._files()))

    def test_beside_a_receiver_file_the_receiver_wins(self):
        rx = self.receiver_file(summary({"steps": {"2024-03-09": 5000, "2024-03-10": 6000}, "hr_rest": {"2024-03-10": 52}},
                                        [workout("2024-03-10T11:00:00+00:00", minutes=31)], last_payload="2024-03-11T08:00:00+00:00"))
        pid = self.profile(watch_file=rx)
        imported = summary({"steps": {"2024-03-08": 4000, "2024-03-09": 1200}, "weight": {"2024-03-09": 176.37}},
                           [workout("2024-03-10T07:00:00-04:00", "apple-health:workout:aaaa", 32),   # the same run
                            workout("2024-03-12T18:00:00-07:00", "apple-health:workout:bbbb", 45, "strength")])
        stored = watch_store.save_import(self.db, pid, imported)
        self.assertEqual(stored["path"], watch_store.side_path(pid))
        self.assertEqual(self.watch_file(pid), rx)   # still the receiver's
        self.assertFalse(os.path.exists(watch_store.own_path(pid)))
        got = watch_store.load(self.db, pid)
        self.assertEqual(pts(got, "steps"), {"2024-03-08": 4000, "2024-03-09": 5000, "2024-03-10": 6000})
        self.assertEqual(pts(got, "weight"), {"2024-03-09": 176.37})
        self.assertEqual(pts(got, "hr_rest"), {"2024-03-10": 52})
        self.assertEqual([(x["start"], x["minutes"]) for x in got["exercise"]],
                         [("2024-03-10T11:00:00+00:00", 31), ("2024-03-12T18:00:00-07:00", 45)])   # not twice
        self.assertEqual(got["last_payload"], "2024-03-11T08:00:00+00:00")
        with open(rx, "rb") as f:   # the receiver's file is never written to
            self.assertEqual(json.loads(crypto.unseal(f.read()))["exercise"][0]["minutes"], 31)

    def test_a_later_overlapping_import_replaces(self):
        pid = self.profile()
        watch_store.save_import(self.db, pid, summary(
            {"steps": {"2024-03-01": 100, "2024-03-02": 200}},
            [workout("2024-03-01T10:00:00+00:00", "apple-health:workout:1111", 20),
             workout("2024-03-02T10:00:00+00:00", "apple-health:workout:2222", 25)]))
        watch_store.save_import(self.db, pid, summary(
            {"steps": {"2024-03-02": 250, "2024-03-03": 300}},
            [workout("2024-03-02T10:00:00+00:00", "apple-health:workout:2222", 26),
             workout("2024-03-03T10:00:00+00:00", "apple-health:workout:3333", 40)]))
        got = watch_store.load(self.db, pid)
        self.assertEqual(pts(got, "steps"), {"2024-03-01": 100, "2024-03-02": 250, "2024-03-03": 300})
        self.assertEqual([(x["key"][-4:], x["minutes"]) for x in got["exercise"]], [("1111", 20), ("2222", 26), ("3333", 40)])

    def test_the_same_export_twice_changes_nothing(self):
        pid = self.profile()
        files = {"export.xml": EXPORT_XML}
        watch_store.save_import(self.db, pid, apple_health.parse(files)["data"]["watch"])
        first = watch_store.load(self.db, pid)
        watch_store.save_import(self.db, pid, apple_health.parse(files)["data"]["watch"])
        again = watch_store.load(self.db, pid)
        self.assertEqual((again["metrics"], again["exercise"]), (first["metrics"], first["exercise"]))

    # ---------------------------------------------------------------- the API and export
    def client(self, name=WREN):
        c = TestClient(main.app, base_url="http://testserver")
        r = c.post("/api/login", json={"username": name, "password": PW}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return c

    def test_api_needs_the_viewer_role(self):
        pid = self.profile(viewer="viewer")
        wren, otto = self.client(WREN), self.client(OTTO)
        self.assertEqual(wren.get(f"/api/profiles/{pid}/watch").status_code, 204)   # nothing yet
        rx = self.receiver_file(summary({"steps": {"2024-03-09": 5000}}))
        self.db.execute("UPDATE profiles SET watch_file=? WHERE id=?", (rx, pid))
        watch_store.save_import(self.db, pid, summary({"steps": {"2024-03-08": 4000, "2024-03-09": 1}}))
        r = wren.get(f"/api/profiles/{pid}/watch")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "application/json")
        self.assertEqual(pts(r.json(), "steps"), {"2024-03-08": 4000, "2024-03-09": 5000})
        self.assertIn(otto.get(f"/api/profiles/{pid}/watch").status_code, (403, 404))   # not shared with otto
        self.assertEqual(TestClient(main.app).get(f"/api/profiles/{pid}/watch").status_code, 401)

    def test_export_includes_the_merged_summary(self):
        rx = self.receiver_file(summary({"steps": {"2024-03-09": 5000}}, [workout("2024-03-10T11:00:00+00:00")]))
        pid = self.profile(watch_file=rx)
        watch_store.save_import(self.db, pid, summary({"weight": {"2024-03-09": 170.5}},
                                                      [workout("2024-03-12T10:00:00+00:00", "apple-health:workout:cccc")]))
        z = zipfile.ZipFile(io.BytesIO(b"".join(export.stream(export.plan(self.db, pid), WREN))))
        w = json.loads(z.read("watch/daily.json"))
        self.assertEqual(pts(w, "steps"), {"2024-03-09": 5000})
        self.assertEqual(pts(w, "weight"), {"2024-03-09": 170.5})
        self.assertEqual(len(w["exercise"]), 2)
        self.assertNotIn("watch and scale summary (none)", z.read("README.txt").decode())

    # ---------------------------------------------------------------- the manage command
    def zip_of(self, entries):
        d = tempfile.mkdtemp(prefix="ah-zip-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "export.zip")
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in entries.items():
                zf.writestr(name, data)
        return path

    def manage(self, *args):
        manage._command(self.db, list(args))

    def test_manage_imports_the_zip(self):
        pid = self.profile()
        with open(EXPORT_XML, "rb") as f:
            xml = f.read()
        path = self.zip_of({"apple_health_export/export.xml": xml, "apple_health_export/export_cda.xml": b"<x/>",
                            "apple_health_export/workout-routes/route_1.gpx": b"<gpx/>"})
        self.manage("import-apple-health", str(pid), path)
        self.assertEqual(pts(watch_store.load(self.db, pid), "steps")["2024-03-09"], 1200)
        log = self.db.execute("SELECT action, detail FROM activity WHERE profile_id=? ORDER BY id DESC", (pid,)).fetchone()
        self.assertEqual(log["action"], "import")
        self.assertEqual(json.loads(crypto.dec(log["detail"]))["source"], "apple-health")
        self.manage("import-apple-health", str(pid), EXPORT_XML)   # export.xml on its own too

    def test_manage_rejects_paths_outside_export_xml(self):
        pid = self.profile()
        with open(EXPORT_XML, "rb") as f:
            xml = f.read()
        for bad in ("../evil.xml", "/etc/evil", "apple_health_export/../../evil", "C:/evil", "a\\..\\evil", "a//b"):
            with self.subTest(bad=bad), self.assertRaises(SystemExit) as e:
                self.manage("import-apple-health", str(pid), self.zip_of({"apple_health_export/export.xml": xml, bad: b"x"}))
            self.assertIn("unexpected entry", str(e.exception.code))
        with self.assertRaises(SystemExit) as e:
            self.manage("import-apple-health", str(pid), self.zip_of({"notes/readme.txt": b"x"}))
        self.assertIn("no export.xml", str(e.exception.code))
        self.assertIsNone(watch_store.load(self.db, pid))   # nothing was stored
        self.assertFalse(os.path.exists(os.path.join(prof.profile_dir(pid), "evil.xml")))

    def test_manage_enforces_the_size_cap(self):
        pid = self.profile()
        path = self.zip_of({"apple_health_export/export.xml": b"<HealthData>" + b" " * 4096 + b"</HealthData>"})
        saved, manage.APPLE_HEALTH_MAX_BYTES = manage.APPLE_HEALTH_MAX_BYTES, 1024
        try:
            with self.assertRaises(SystemExit) as e:
                self.manage("import-apple-health", str(pid), path)
        finally:
            manage.APPLE_HEALTH_MAX_BYTES = saved
        self.assertIn("GB unpacked", str(e.exception.code))


if __name__ == "__main__":
    unittest.main()
