#!/usr/bin/env python3
"""A fresh install starts empty, and the demo is a profile of its own, on request (#1101): the Docker setup
(docker/setup.py) without a sources.json builds nothing; first-run setup makes the person's own profile, empty (so the
getting-started guide opens), and with "demo" a separate profile, "Demo (fictional)", beside it; Settings → Install →
General adds one and Remove demo deletes it whole, and nothing of any other profile.

  pip install -r requirements-dev.txt
  python3 tests/test_first_run.py
"""
import atexit, json, os, shutil, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-first-run-")
atexit.register(shutil.rmtree, TMP, True)
DATA, WATCH = os.path.join(TMP, "data"), os.path.join(TMP, "watch")
os.environ.update(SANGUITY_DATA=DATA, SANGUITY_RECORDS=os.path.join(TMP, "records"),
                  SANGUITY_WATCH_FILE=os.path.join(WATCH, "public", "daily.json"), PASSWORD_MIN_LENGTH="15",
                  SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"), SANGUITY_KEY_FILE=os.path.join(TMP, "auth", "data_key"))
SETUP_ENV = {**os.environ, "SANGUITY_APP": ROOT, "SANGUITY_AUTH": os.path.join(TMP, "auth"), "SANGUITY_CONFIG": os.path.join(TMP, "config"),
             "SANGUITY_BACKUPS": os.path.join(TMP, "backups"), "SANGUITY_WATCH": WATCH, "PUID": str(os.getuid()), "PGID": str(os.getgid())}
os.makedirs(os.path.join(TMP, "config"))
sys.path.insert(0, ROOT)


def setup_py(env=None):
    """docker/setup.py's start, as the setup service runs it (here outside Docker, on this test's folders)."""
    r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "docker", "setup.py")], env={**SETUP_ENV, **(env or {})},
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


SETUP_SAID = setup_py()   # before the app starts: a fresh install's folders, key and (no) build

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, db as dbm, demo, main, profiles as prof, records, security as sec   # noqa: E402

H = {"X-Sanguity": "1"}
CODE, PW = "ABCD-EFGH-JKLM", "a long first run passphrase"


def client():
    return TestClient(main.app, base_url="http://testserver")


class FirstRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = client().__enter__()   # start-up: the database; no build, so no profile
        db = dbm.connect()
        dbm.meta_set(db, "setup_code", sec.token_hash(CODE))
        db.close()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

    def test_1_fresh_install_starts_empty(self):
        build = os.path.join(DATA, "build")
        self.assertFalse(os.path.exists(os.path.join(build, "data.json")), "setup built the demo on a fresh install")
        self.assertFalse(os.path.exists(os.path.join(WATCH, "public", "daily.json")), "setup wrote the demo's watch data")
        self.assertIn("starting empty", SETUP_SAID)
        self.assertIn("add a demo profile", SETUP_SAID)
        self.assertNotIn("showing the fictional demo", SETUP_SAID)
        self.assertNotIn("showing the fictional demo", setup_py())   # and again at the next start
        st = client().get("/api/setup").json()
        self.assertTrue(st["needed"])
        self.assertFalse(st["records"])   # nothing here for the first account to own

    def test_2_setup_with_the_demo(self):
        # the receiver writes an empty summary for its own slot as it starts (watch/receiver.py): it isn't the first
        # profile's, so that profile still has no records and the guide opens
        os.makedirs(os.path.join(WATCH, "public"), exist_ok=True)
        with open(os.path.join(WATCH, "public", "daily.json"), "w") as f:
            json.dump({"metrics": [], "exercise": []}, f)
        c = client()
        r = c.post("/api/setup", json={"code": CODE, "username": "ada", "password": PW, "profile_name": "Ada", "demo": True}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        profiles = c.get("/api/me").json()["profiles"]
        # the person's own first (shown first, so the guide opens there), then the demo, labelled
        self.assertEqual([(p["name"], p["role"], p["demo"]) for p in profiles],
                         [("Ada", "owner", False), ("Demo (fictional)", "owner", True)])
        own, shown = profiles[0]["id"], profiles[1]["id"]
        self.assertEqual(c.get(f"/api/profiles/{own}/data").status_code, 204)   # empty: the guide opens
        # (#1103) no phone set up: the install's single receiver secret (from before each profile had its own) isn't
        # counted for the first profile, which doesn't read the receiver's own summary, nor taken by the receiver
        rec = c.get(f"/api/profiles/{own}/watch/receiver").json()
        self.assertEqual((rec["has_secret"], rec["legacy"], rec["last"]), (False, False, None))
        db = dbm.connect()
        try:
            from server import watch_keys
            self.assertFalse(watch_keys.legacy_in_use(db))
        finally:
            db.close()
        data = c.get(f"/api/profiles/{shown}/data").json()
        self.assertGreater(len(data["events"]), 100)
        self.assertEqual(data["source"], "Sanguity demo data (fictional)")   # said under the records, too
        self.assertEqual(c.get(f"/api/profiles/{shown}/watch").status_code, 200)   # its watch and scale data
        # its document opens, from the profile's own folder, sealed there like everything it keeps
        ids = ",".join(str(e["id"]) for e in data["events"])
        details = c.get(f"/api/profiles/{shown}/details?ids={ids}").json()["details"]
        docs = [d["url"] for d in details.values() if isinstance(d, dict) and d.get("type") == "file" and d.get("url")]
        self.assertTrue(docs)
        r = c.get("/" + docs[0].split("#")[0])
        self.assertEqual((r.status_code, r.content[:5]), (200, b"%PDF-"))
        folder = prof.profile_dir(shown)
        kept = [os.path.join(r_, n) for r_, _, fs in os.walk(folder) for n in fs]
        self.assertTrue(kept)
        for p in kept:
            with open(p, "rb") as f:
                self.assertEqual(f.read(4), b"SGE1", p)
        self.assertEqual(c.get("/api/settings/install/demo").json()["profile"], {"id": shown, "name": "Demo (fictional)"})
        # asked again, the one there is
        r = c.post("/api/settings/install/demo", headers=H)
        self.assertEqual(r.json()["id"], shown)

    def test_3_remove_demo_removes_only_it(self):
        c = client()
        self.assertEqual(c.post("/api/login", json={"username": "ada", "password": PW}, headers=H).status_code, 200)
        own, shown = [p["id"] for p in c.get("/api/me").json()["profiles"]]
        # the person's own profile, with records, a setting and a file in its folder
        # its records from a sources.json added after setup (setup rebuild): the first profile picks them up, the
        # demo doesn't
        with open(os.path.join(DATA, "build", "data.json"), "w") as f:
            json.dump({"events": [{"id": 1, "t": "2026-01-05T09:00:00", "k": "visit", "title": "Checkup", "sub": "Dr. Example"}]}, f)
        db = dbm.connect()
        try:
            self.assertEqual(prof.sync_build(db), {})
            self.assertEqual(len(records.imports(db, own, "build")), 1)
            self.assertEqual(records.imports(db, shown, "build"), [])
        finally:
            db.close()
        self.assertEqual([e["title"] for e in c.get(f"/api/profiles/{own}/data").json()["events"]], ["Checkup"])
        self.assertEqual(c.put(f"/api/profiles/{own}/settings/sync.staleDays", json={"value": 9}, headers=H).status_code, 200)
        mine = os.path.join(prof.profile_dir(own), "keep-me")
        with open(mine, "w") as f:
            f.write("x")
        before = c.get(f"/api/profiles/{own}/data").json()
        r = c.delete("/api/settings/install/demo", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual([p["id"] for p in c.get("/api/me").json()["profiles"]], [own])
        self.assertFalse(os.path.exists(prof.profile_dir(shown)))
        self.assertEqual(c.get(f"/api/profiles/{shown}/data").status_code, 404)
        self.assertEqual(c.get(f"/api/profiles/{own}/data").json(), before)   # the person's own, untouched
        self.assertTrue(os.path.isfile(mine))
        self.assertEqual(c.get(f"/api/profiles/{own}/settings").json()["sync"]["staleDays"], 9)
        db = dbm.connect()
        try:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE profile_id=?", (shown,)).fetchone()[0], 0)
            self.assertEqual(len(records.imports(db, own)), 1)
            self.assertIsNone(demo.profile_id(db))
        finally:
            db.close()
        self.assertEqual(c.get("/api/settings/install/demo").json()["profile"], None)
        self.assertEqual(c.delete("/api/settings/install/demo", headers=H).status_code, 404)   # nothing else is removed
        self.assertEqual([p["id"] for p in c.get("/api/me").json()["profiles"]], [own])

        # added again from Settings → Install → General: a new profile, not the person's own
        r = c.post("/api/settings/install/demo", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn(r.json()["id"], (own, shown))
        self.assertEqual(r.json()["name"], "Demo (fictional)")
        self.assertEqual(c.get(f"/api/profiles/{own}/data").json(), before)
        # deleted as any profile can be, it's no demo any more
        self.assertEqual(c.request("DELETE", f"/api/profiles/{r.json()['id']}", json={"confirm_name": "Demo (fictional)"}, headers=H).status_code, 200)
        self.assertEqual(c.get("/api/settings/install/demo").json()["profile"], None)

        # someone who isn't an admin can't add one, nor remove it
        self.assertEqual(c.post("/api/settings/install/demo", headers=H).status_code, 200)
        db = dbm.connect()
        try:
            accounts.create(db, "bo", password=PW)
        finally:
            db.close()
        bo = client()
        self.assertEqual(bo.post("/api/login", json={"username": "bo", "password": PW}, headers=H).status_code, 200)
        self.assertEqual(bo.delete("/api/settings/install/demo", headers=H).status_code, 403)
        self.assertEqual(bo.post("/api/settings/install/demo", headers=H).status_code, 403)
        self.assertEqual(bo.get("/api/settings/install/demo").status_code, 403)

    def test_5_every_install_has_the_generator(self):
        """A checkout's compose.yaml runs the app on the image's Python with only some of the checkout mounted: the demo
        generator is one of them, or Add a demo profile has nothing to run (the released image copies it in)."""
        with open(os.path.join(ROOT, "compose.yaml"), encoding="utf-8") as f:
            app = f.read().split("\n  app:\n", 1)[1].split("\n  web:\n", 1)[0]
        self.assertIn("- ./demo/generate.py:/app/demo/generate.py:ro", app)
        with open(os.path.join(ROOT, "docker", "app.Dockerfile"), encoding="utf-8") as f:
            self.assertIn("COPY demo/generate.py /app/demo/generate.py", f.read())
        self.assertEqual(demo.GENERATOR, os.path.join(ROOT, "demo", "generate.py"))   # /app/demo/generate.py in the image

    def test_4_a_demo_build_from_before_stays(self):
        """An install set up before #1101, whose build is the demo, keeps it (regenerated, so its dates stay current)."""
        build = os.path.join(TMP, "old", "data", "build")
        subprocess.run([sys.executable, "-I", os.path.join(ROOT, "demo", "generate.py"), build, "--end", "2026-01-15",
                        "--watch", os.path.join(TMP, "old", "watch", "public")], check=True, stdout=subprocess.DEVNULL)
        said = setup_py({"SANGUITY_DATA": os.path.join(TMP, "old", "data"), "SANGUITY_WATCH": os.path.join(TMP, "old", "watch")})
        self.assertIn("showing the fictional demo data this install started with", said)
        from server import crypto
        self.assertNotEqual(json.loads(crypto.read_file(os.path.join(build, "data.json")))["built"][:10], "2026-01-15")


if __name__ == "__main__":
    unittest.main(verbosity=2)
