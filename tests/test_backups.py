#!/usr/bin/env python3
"""Backups on the Admin page (#200; server/backup_jobs.py, server/routes_backups.py, server/backup.py): Back up now
in the background, the schedule's backups (daily, weekly, off), pruning only Sanguity's own backup files, the late
warning (and a failed backup), Check a backup on a good backup and on damaged ones, that a check never touches the
live data, permission refusals, the activity log, and what a stop in the middle leaves behind. Fictional data only.

  python3 tests/test_backups.py
"""
import contextlib, hashlib, io, json, os, shutil, sqlite3, sys, tarfile, tempfile, threading, time, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-backups-test-")
DATA, FOLDER = os.path.join(TMP, "data"), os.path.join(TMP, "backups")   # the backups folder apart from the data
os.environ.update(SANGUITY_DATA=DATA, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(DATA, "build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"), SANGUITY_BACKUPS=FOLDER)
for d in ("records", "keys"):
    os.makedirs(os.path.join(TMP, d))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, backup, backup_jobs, crypto, db as dbm, main, manage, profiles as prof, twofactor, uploads   # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
logging.getLogger("sanguity.backups").setLevel(logging.CRITICAL)   # failures are expected here
H = {"X-Sanguity": "1"}
PW = {"ada": "granite willow ember compass", "robin": "harbour lantern pebble crow"}
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "importers", "sanguity", "export")


def client():
    return TestClient(main.app, base_url="http://testserver")


def live_state():
    """Everything a restore would replace, as it is now: each file under DATA_DIR but the database (its bytes and
    its time), and every row of the database except the logs a backup or a check writes to by design."""
    files = {}
    for root, dirs, names in os.walk(DATA):
        for n in names:
            p = os.path.join(root, n)
            if n.startswith("sanguity.db") or n == "app.lock":
                continue
            with open(p, "rb") as f:
                files[os.path.relpath(p, DATA)] = (hashlib.sha256(f.read()).hexdigest(), os.stat(p).st_mtime_ns)
    con = sqlite3.connect(f"file:{os.path.join(DATA, 'sanguity.db')}?mode=ro", uri=True)
    try:
        rows = {}
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            if t in ("activity", "backup_runs", "sessions", "meta"):
                continue
            rows[t] = hashlib.sha256(repr(con.execute(f'SELECT * FROM "{t}" ORDER BY rowid').fetchall()).encode()).hexdigest()
    finally:
        con.close()
    return files, rows


def rewrite(src, dest, change):
    """A copy of a backup with change(name, data) -> data applied to each file, and the manifest's checksums made to
    match (its counts left as they were): damage a checksum can't see."""
    with tarfile.open(src, "r:gz") as tar:
        items = [(m.name, tar.extractfile(m).read()) for m in tar.getmembers()]
    manifest = json.loads(dict(items)["manifest.json"])
    out = []
    for name, data in items:
        if name != "manifest.json":
            data = change(name, data)
            manifest["files"][name] = hashlib.sha256(data).hexdigest()
            out.append((name, data))
    out.append(("manifest.json", json.dumps(manifest).encode()))
    with tarfile.open(dest, "w:gz") as tar:
        for name, data in out:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return dest


@contextlib.contextmanager
def other_key():
    """The app pointed at a key file holding a different key for a while (as tests/test_api.py does)."""
    from server import config
    path, saved = os.path.join(TMP, "keys", "other_key"), (config.KEY_FILE, crypto.keyring.path)
    crypto.make_key_file(path)
    config.KEY_FILE = crypto.keyring.path = path
    crypto.keyring.reset()
    try:
        yield
    finally:
        config.KEY_FILE, crypto.keyring.path = saved
        crypto.keyring.reset()
        os.remove(path)


class Backups(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = client().__enter__()   # lifespan: database, key, and the backups thread
        db = dbm.connect()
        cls.uids = {n: accounts.create(db, n, password=pw, is_admin=n == "ada")[0] for n, pw in PW.items()}
        cls.pid = prof.create_profile(db, "Robin (fictional)", owner_id=cls.uids["robin"])
        prof.import_dataset(db, cls.pid, FIXTURE)   # records in the database...
        up = uploads.create(db, cls.pid, cls.uids["robin"])   # ...and an upload's sealed file in the profile's folder
        uploads.add_file(db, cls.pid, up, "notes/visit.txt", io.BytesIO(b"A fictional visit note. " * 4000))
        uploads.finish(db, cls.pid, up)
        db.close()
        cls.ada, cls.robin = cls.login("ada"), cls.login("robin")
        db = dbm.connect()   # the admin's own second factor (admin actions need one once there are other accounts)
        db.execute("UPDATE users SET totp_secret=? WHERE id=?", (crypto.enc(twofactor.new_secret()), cls.uids["ada"]))
        db.close()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        shutil.rmtree(TMP, ignore_errors=True)

    @staticmethod
    def login(name):
        c = client()
        r = c.post("/api/login", json={"username": name, "password": PW[name]}, headers=H)
        assert r.status_code == 200, r.text
        return c

    def status(self):
        r = self.ada.get("/api/backups")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def finish(self, rid, timeout=30):
        """Wait until run rid is done or failed (the backups thread runs it), and return it."""
        end = time.time() + timeout
        while time.time() < end:
            st = self.status()
            run = next((r for r in st["runs"] if r["id"] == rid), None)
            if run and run["state"] in ("done", "failed"):
                return run
            time.sleep(0.05)
        self.fail(f"run {rid} didn't finish: {run}")

    def backup_now(self):
        r = self.ada.post("/api/backups", headers=H)
        self.assertEqual(r.status_code, 202, r.text)
        run = self.finish(r.json()["id"])
        self.assertEqual(run["state"], "done", run)
        return run

    def check(self, name):
        r = self.ada.post("/api/backups/check", json={"name": name}, headers=H)
        self.assertEqual(r.status_code, 202, r.text)
        run = self.finish(r.json()["id"])
        self.assertEqual(run["state"], "done", run)
        return run

    def actions(self, action):
        return [e for e in self.ada.get("/api/activity?limit=500").json() if e["action"] == action]

    def stale(self, name, days):
        """Rename backup name as if it was made days ago; returns the new name."""
        made = backup.made_at(name) - int(days * 86400)
        new = time.strftime("sanguity-backup-%Y%m%d-%H%M%S.tar.gz", time.localtime(made))
        os.replace(os.path.join(FOLDER, name), os.path.join(FOLDER, new))
        return new

    def set_schedule(self, sched, keep=14, c=None):
        return (c or self.ada).put("/api/backups/schedule", json={"schedule": sched, "keep": keep}, headers=H)

    def clear_folder(self):
        for n in os.listdir(FOLDER):
            p = os.path.join(FOLDER, n)
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)

    # ---------------------------------------------------------------- Back up now

    def test_1_back_up_now_runs_in_the_background(self):
        """The request returns at once; the backup runs in the thread, one at a time, and is logged."""
        self.assertEqual(self.status()["state"], "none")
        gate, real = threading.Event(), backup.create
        with mock.patch.object(backup, "create", lambda **kw: (gate.wait(20), real(**kw))[1]):
            t = time.time()
            r = self.ada.post("/api/backups", headers=H)
            self.assertEqual(r.status_code, 202, r.text)
            self.assertLess(time.time() - t, 2, "Back up now waited for the backup")
            rid = r.json()["id"]
            st = self.status()
            self.assertEqual(st["running"]["id"], rid)
            # one at a time: another backup, or a check, waits until this one is done
            self.assertEqual(self.ada.post("/api/backups", headers=H).status_code, 409)
            gate.set()
            run = self.finish(rid)
        self.assertEqual((run["state"], run["origin"], run["created_name"]), ("done", "manual", "ada"), run)
        st = self.status()
        self.assertIsNone(st["running"])
        self.assertEqual(st["state"], "ok")
        self.assertEqual(st["newest"]["name"], run["file"])
        self.assertEqual(st["newest"]["size"], os.path.getsize(os.path.join(FOLDER, run["file"])))
        self.assertEqual(st["folder"], FOLDER)
        self.assertFalse(st["inside_data"])
        self.assertTrue(backup.NAME.match(run["file"]))
        manifest = backup.verify(os.path.join(FOLDER, run["file"]))
        self.assertTrue(any(n.startswith("profiles/") for n in manifest["files"]), "the profile's files are in it")
        self.assertGreater(manifest["counts"]["events"], 0)   # what a check expects, recorded at backup time
        logged = self.actions("backup.create")
        self.assertEqual((logged[0]["actor"], logged[0]["detail"]["name"]), ("ada", run["file"]), logged)
        self.assertEqual(os.listdir(FOLDER), [run["file"]], "nothing but the backup is left in the folder")

    # ---------------------------------------------------------------- Check a backup

    def test_2_check_a_good_backup_without_touching_live_data(self):
        name = self.backup_now()["file"]
        before = live_state()
        seen, real = [], backup_jobs._progress

        def watching(db, rid):   # where the restored copy is while the check runs
            say = real(db, rid)
            return lambda stage, d, t: (seen.append((stage, sorted(os.listdir(FOLDER)))), say(stage, d, t))
        with mock.patch.object(backup_jobs, "_progress", watching), \
                mock.patch.object(backup, "restore", side_effect=AssertionError("a check must never restore")):
            run = self.check(name)
        self.assertTrue(run["ok"], run)
        res = run["result"]
        self.assertEqual(res["problems"], [])
        self.assertTrue(all(c["backup"] == c["expected"] for c in res["counts"].values()), res["counts"])
        self.assertGreater(res["counts"]["events"]["backup"], 0)
        self.assertEqual(res["now"]["events"], res["counts"]["events"]["backup"])
        restoring = [names for stage, names in seen if stage == "restoring"]
        self.assertTrue(restoring and any(n.startswith(backup.CHECKING) for n in restoring[-1]),
                        "restored into a temporary folder beside the backup")
        self.assertEqual([n for n in os.listdir(FOLDER) if n.startswith(".")], [], "the temporary copy is deleted")
        self.assertFalse(any(n.startswith(backup.CHECKING) for n in os.listdir(DATA)))
        self.assertEqual(live_state(), before, "a check changed the live data")
        self.assertEqual(self.status()["backups"][0]["checked"]["ok"], True)
        self.assertEqual(self.actions("backup.check")[0]["detail"], {"name": name, "ok": True})
        # the same check from the command line (server.manage check), also leaving the live data as it was
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            manage.run(["check", os.path.join(FOLDER, name)])
        self.assertTrue(out.getvalue().startswith("Good: "), out.getvalue())
        self.assertEqual(live_state(), before)

    def test_3_check_damaged_backups(self):
        name = self.backup_now()["file"]
        src = os.path.join(FOLDER, name)
        later = lambda n: time.strftime("sanguity-backup-%Y%m%d-%H%M%S.tar.gz",
                                        time.localtime(backup.made_at(name) - n * 60))
        # a byte changed in the middle of the archive: the checksums (or gzip's own) catch it
        flipped = os.path.join(FOLDER, later(1))
        data = bytearray(open(src, "rb").read())
        data[len(data) // 2] ^= 0xFF
        open(flipped, "wb").write(data)
        # cut short
        short = os.path.join(FOLDER, later(2))
        open(short, "wb").write(bytes(data[: len(data) // 3]))
        # rows missing from the database, with checksums that match: only the counts can tell
        def drop_rows(n, d):
            if n != "sanguity.db":
                return d
            with tempfile.TemporaryDirectory() as t:
                p = os.path.join(t, "db")
                open(p, "wb").write(d)
                con = sqlite3.connect(p)
                con.execute("DELETE FROM events WHERE rowid IN (SELECT rowid FROM events LIMIT 3)")
                con.commit()
                con.execute("VACUUM")
                con.close()
                return open(p, "rb").read()
        thinner = rewrite(src, os.path.join(FOLDER, later(3)), drop_rows)
        # a database that isn't one any more, with checksums that match
        garbled = rewrite(src, os.path.join(FOLDER, later(4)), lambda n, d: d[:100] + b"\0" * 4096 + d[4196:] if n == "sanguity.db" else d)
        before = live_state()
        with mock.patch.object(backup, "restore", side_effect=AssertionError("a check must never restore")):
            for path, says in ((flipped, ("checksum", "damaged", "Can't read")), (short, ("Can't read", "missing", "no manifest")),
                               (thinner, ("rows in records",)), (garbled, ("damaged", "can't be read", "isn't the version"))):
                run = self.check(os.path.basename(path))
                self.assertFalse(run["ok"], (path, run))
                self.assertTrue(any(s in run["message"] for s in says), (os.path.basename(path), run["message"]))
                self.assertEqual([n for n in os.listdir(FOLDER) if n.startswith(".")], [])
            # a backup this install's key can't open (its key file was replaced, without the old key's line)
            with other_key():
                got = backup.check(src)
            self.assertFalse(got["ok"], got)
            self.assertIn("key", " ".join(got["problems"]))
        self.assertEqual(live_state(), before, "checking a damaged backup changed the live data")
        self.assertIn({"name": os.path.basename(thinner), "ok": False}, [e["detail"] for e in self.actions("backup.check")])
        checked = {b["name"]: b["checked"] for b in self.status()["backups"]}
        self.assertEqual(checked[os.path.basename(thinner)]["ok"], False)
        self.assertEqual(checked[name], None)
        for p in (flipped, short, thinner, garbled):
            os.remove(p)

    # ---------------------------------------------------------------- the schedule, pruning, late

    def test_4_scheduled_backups(self):
        backup_jobs.stop()   # the schedule is driven by hand here, at made-up times
        try:
            self.clear_folder()
            self.assertFalse(backup_jobs.run_due(), "nothing is due while the schedule is off")
            r = self.set_schedule("daily")
            self.assertEqual((r.status_code, r.json()["schedule"]), (200, "daily"), r.text)
            self.assertTrue(backup_jobs.run_due(), "no backup yet: one is due at once")
            st = self.status()
            run = st["runs"][0]
            self.assertEqual((run["origin"], run["state"], run["created_name"]), ("schedule", "done", "scheduled backup"), run)
            self.assertEqual(st["newest"]["name"], run["file"])
            made = st["newest"]["at"]
            self.assertEqual(st["next"], made + 86400)
            self.assertFalse(backup_jobs.run_due(made + 3600))
            self.assertFalse(backup_jobs.run_due(made + 86400 - 1))
            self.assertTrue(backup_jobs.run_due(made + 86400 + 1))
            self.assertEqual(len(backup.list_backups()), 2)
            e = self.actions("backup.create")[0]
            self.assertEqual((e["actor"], e["actor_id"], e["actor_removed"]), ("scheduled backup", None, False), e)
            # weekly
            self.assertEqual(self.set_schedule("weekly").status_code, 200)
            newest = self.status()["newest"]["at"]
            self.assertFalse(backup_jobs.run_due(newest + 6 * 86400))
            self.assertTrue(backup_jobs.run_due(newest + 7 * 86400 + 1))
            # a backup made another way (Back up now, or server.manage backup) counts: the next one is a day after it
            self.assertEqual(self.set_schedule("daily").status_code, 200)
            backup.create()
            newest = self.status()["newest"]["at"]
            self.assertFalse(backup_jobs.run_due(newest + 3600))
            self.assertEqual(self.actions("backup.schedule")[0]["detail"], {"schedule": "daily", "keep": 14, "from": "weekly"})
            # off: nothing more, though the last is old
            self.assertEqual(self.set_schedule("off").status_code, 200)
            self.assertFalse(backup_jobs.run_due(newest + 30 * 86400))
        finally:
            backup_jobs.start()

    def test_5_pruning_keeps_the_newest_and_only_touches_backups(self):
        backup_jobs.stop()
        try:
            self.clear_folder()
            olds = [self.stale(self.backup_now_by_hand(), d) for d in (3, 2)]
            others = ["notes.txt", "sanguity-backup-before-upgrade.tar.gz", "sanguity-backup-20200101-000000.tar.gz.bak"]
            for n in others:
                open(os.path.join(FOLDER, n), "w").write("someone else's file")
            os.mkdir(os.path.join(FOLDER, "offsite"))
            self.backup_now_by_hand()
            self.assertEqual(len(backup.list_backups()), 3, "with the schedule off, Back up now deletes nothing")
            # keeping fewer than there are: asked again who it is (the fresh check), then allowed
            db = dbm.connect()
            db.execute("UPDATE sessions SET verified_at=0 WHERE user_id=?", (self.uids["ada"],))
            db.close()
            r = self.set_schedule("daily", 2)
            self.assertEqual((r.status_code, r.json()["detail"]), (403, "reauth_required"))
            self.assertEqual(self.status()["schedule"], "off", "nothing changed")
            self.assertEqual(self.ada.post("/api/me/reauth", json={"secret": PW["ada"]}, headers=H).status_code, 200)
            self.assertEqual(self.set_schedule("daily", 2).status_code, 200)
            run = self.backup_now_by_hand()
            names = sorted(os.path.basename(p) for p in backup.list_backups())
            self.assertEqual(len(names), 2)
            self.assertIn(run, names)
            self.assertFalse(set(olds) & set(names), "the oldest went")
            got = self.status()["runs"][0]
            self.assertEqual(sorted(got["result"]["removed"]), sorted(olds))
            self.assertEqual(self.actions("backup.create")[0]["detail"]["removed"], 2)
            for n in others + ["offsite"]:
                self.assertTrue(os.path.exists(os.path.join(FOLDER, n)), f"{n} was touched")
            # the same keep again, or more: no fresh check needed
            db = dbm.connect()
            db.execute("UPDATE sessions SET verified_at=0 WHERE user_id=?", (self.uids["ada"],))
            db.close()
            self.assertEqual(self.set_schedule("daily", 5).status_code, 200)
            self.assertEqual(self.set_schedule("weekly", 5).status_code, 200)
            r = self.set_schedule("off", 5)   # turning backups off: the fresh check
            self.assertEqual((r.status_code, r.json()["detail"]), (403, "reauth_required"))
            self.assertEqual(self.ada.post("/api/me/reauth", json={"secret": PW["ada"]}, headers=H).status_code, 200)
            self.assertEqual(self.set_schedule("off", 5).status_code, 200)
            for n in others:
                os.remove(os.path.join(FOLDER, n))
            os.rmdir(os.path.join(FOLDER, "offsite"))
        finally:
            backup_jobs.start()

    def backup_now_by_hand(self):
        """Back up now, run here rather than by the thread (stopped in these tests); returns the file's name."""
        db = dbm.connect()
        try:
            s = db.execute("SELECT * FROM users WHERE id=?", (self.uids["ada"],)).fetchone()
            rid = backup_jobs.submit(db, "backup", s)
        finally:
            db.close()
        backup_jobs.run(rid)
        run = next(r for r in self.status()["runs"] if r["id"] == rid)
        self.assertEqual(run["state"], "done", run)
        return run["file"]

    def test_6_late_warning_and_failures(self):
        backup_jobs.stop()
        try:
            self.clear_folder()
            name = self.backup_now_by_hand()
            made = backup.made_at(name)
            db = dbm.connect()
            try:
                for sched, ok_at, late_at in (("daily", 29 * 3600, 31 * 3600), ("weekly", 7 * 86400 + 3600, 8 * 86400 + 3600),
                                              ("off", 7 * 86400, 9 * 86400)):
                    backup_jobs.set_schedule(db, sched, 14)
                    self.assertEqual(backup_jobs.status(db, made + ok_at)["state"], "ok", sched)
                    st = backup_jobs.status(db, made + late_at)
                    self.assertEqual((st["state"], st["late"]), ("late", True), sched)
                    self.assertIn("old", st["warning"])
                    self.assertIn("isn't scheduled" if sched == "off" else f"scheduled {sched}", st["warning"])
                backup_jobs.set_schedule(db, "daily", 14)
            finally:
                db.close()
            # the folder as the API and the menu see it: a backup two days old
            self.stale(name, 2)
            st = self.status()
            self.assertEqual(st["state"], "late")
            self.assertTrue(self.ada.get("/api/me").json()["backups_late"], "the Admin menu item says so")
            self.assertFalse(self.robin.get("/api/me").json()["backups_late"], "only to admins")
            # a backup that fails (the disk is full): said, logged once however often it's tried, retried an hour on
            full = OSError(28, "No space left on device")
            with mock.patch.object(backup, "create", side_effect=full):
                self.assertTrue(backup_jobs.run_due())
                st = self.status()
                self.assertEqual(st["state"], "failed")
                self.assertIn("No space left on device", st["warning"])
                fail = st["runs"][0]
                self.assertEqual(st["next"], fail["finished"] + backup_jobs.RETRY)
                self.assertFalse(backup_jobs.run_due(), "not tried again at once")
                self.assertTrue(backup_jobs.run_due(fail["finished"] + backup_jobs.RETRY + 1))
            self.assertEqual(len(self.actions("backup.fail")), 1, "a spell of failures is logged once")
            self.assertIn("No space", self.actions("backup.fail")[0]["detail"]["error"])
            self.assertTrue(self.ada.get("/api/me").json()["backups_late"])
            self.assertEqual([n for n in os.listdir(FOLDER) if not backup.NAME.match(n)], [], "a failure leaves nothing behind")
            # the next backup that works ends it
            self.backup_now_by_hand()
            self.assertEqual(self.status()["state"], "ok")
            self.assertFalse(self.ada.get("/api/me").json()["backups_late"])
            self.assertEqual(self.set_schedule("off").status_code, 200)
        finally:
            backup_jobs.start()

    # ---------------------------------------------------------------- who may

    def test_7_permissions(self):
        name = os.path.basename(backup.list_backups()[-1])
        anon = client()
        for c, code in ((anon, 401), (self.robin, 403)):   # signed out; not an admin
            self.assertEqual(c.get("/api/backups").status_code, code)
            self.assertEqual(c.post("/api/backups", headers=H).status_code, code)
            self.assertEqual(c.post("/api/backups/check", json={"name": name}, headers=H).status_code, code)
            self.assertEqual(c.put("/api/backups/schedule", json={"schedule": "daily", "keep": 3}, headers=H).status_code, code)
        # the CSRF check: no X-Sanguity header, nothing done
        n = len(self.status()["runs"])
        self.assertEqual(self.ada.post("/api/backups").status_code, 403)
        self.assertEqual(self.ada.put("/api/backups/schedule", json={"schedule": "daily", "keep": 3}).status_code, 403)
        self.assertEqual(len(self.status()["runs"]), n)
        # only one of Sanguity's backups in the folder, by name: never a path or another file
        open(os.path.join(TMP, "outside.tar.gz"), "wb").write(open(backup.list_backups()[-1], "rb").read())
        for bad in ("../outside.tar.gz", "/etc/passwd", "notes.txt", "sanguity-backup-20990101-000000.tar.gz",
                    "sanguity-backup-20260101-000000.tar.gz/../x", os.path.join(FOLDER, name)):
            self.assertEqual(self.ada.post("/api/backups/check", json={"name": bad}, headers=H).status_code, 404, bad)
        self.assertEqual(self.set_schedule("hourly").status_code, 422)
        for keep in (0, 366):
            self.assertEqual(self.set_schedule("daily", keep).status_code, 400)
        self.assertEqual(self.status()["schedule"], "off")
        # an admin without a second factor of their own, once there are other accounts: refused like other admin pages
        db = dbm.connect()
        accounts.create(db, "ben", password="lantern harbour copper drift", is_admin=True)
        db.close()
        ben = client()
        self.assertEqual(ben.post("/api/login", json={"username": "ben", "password": "lantern harbour copper drift"}, headers=H).status_code, 200)
        for r in (ben.get("/api/backups"), ben.post("/api/backups", headers=H),
                  ben.post("/api/backups/check", json={"name": name}, headers=H)):
            self.assertEqual((r.status_code, r.json()["detail"]), (403, "admin_two_factor_required"))
        self.assertFalse(ben.get("/api/me").json()["backups_late"])
        # nothing in the API hands a backup out or restores one
        from server import routes_backups
        self.assertEqual(sorted((r.path, m) for r in routes_backups.router.routes for m in r.methods),
                         [("/api/backups", "GET"), ("/api/backups", "POST"), ("/api/backups/check", "POST"),
                          ("/api/backups/schedule", "PUT")])

    # ---------------------------------------------------------------- a stop in the middle

    def test_8_recover_after_a_stop(self):
        backup_jobs.stop()
        try:
            done = os.path.basename(backup.list_backups()[-1])
            leftovers = [done + ".part", backup.CHECKING + "abc", backup.SNAPSHOT + "def"]
            open(os.path.join(FOLDER, leftovers[0]), "wb").write(b"half an archive")
            for d in leftovers[1:]:
                os.makedirs(os.path.join(FOLDER, d, "profiles"))
            open(os.path.join(FOLDER, "notes.txt"), "w").write("kept")
            db = dbm.connect()
            try:
                ins = lambda kind, f, state: db.execute(
                    "INSERT INTO backup_runs(kind, origin, state, file, created_name, created) VALUES(?, 'manual', ?, ?, 'ada', ?)",
                    (kind, state, f, dbm.now())).lastrowid
                finished = ins("backup", done, "running")       # written, then the app stopped (or restored from it)
                halfway = ins("backup", "sanguity-backup-20200101-000000.tar.gz", "running")
                checking = ins("check", done, "running")
                queued = ins("backup", None, "queued")
                self.assertEqual(backup_jobs.recover(db), 3)
                state = {r["id"]: (r["state"], r["message"]) for r in db.execute("SELECT * FROM backup_runs")}
            finally:
                db.close()
            self.assertEqual(state[finished], ("done", None))
            for rid in (halfway, checking, queued):
                self.assertEqual(state[rid], ("failed", backup_jobs.INTERRUPTED))
            self.assertEqual(sorted(n for n in os.listdir(FOLDER) if not backup.NAME.match(n)), ["notes.txt"])
            os.remove(os.path.join(FOLDER, "notes.txt"))
        finally:
            backup_jobs.start()


if __name__ == "__main__":
    unittest.main(verbosity=2)
