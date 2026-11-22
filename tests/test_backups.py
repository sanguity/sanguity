#!/usr/bin/env python3
"""Backups on the Admin page (#200; server/backup_jobs.py, server/routes_backups.py, server/backup.py): Back up now
in the background, the schedule's backups (daily, weekly, off), pruning only Sanguity's own backup files, the late
warning (and a failed backup), Check a backup on a good backup and on damaged ones, that a check never touches the
live data, permission refusals, the activity log, and what a stop in the middle leaves behind; and the watch receiver's
data in backups (#227): taken as it is, counted by a check, and put back by a restore. Fictional data only.

  python3 tests/test_backups.py
"""
import contextlib, hashlib, importlib.util, io, json, os, shutil, sqlite3, sys, tarfile, tempfile, threading, time, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-backups-test-")
DATA, FOLDER = os.path.join(TMP, "data"), os.path.join(TMP, "backups")   # the backups folder apart from the data
STORE = os.path.join(TMP, "watch")   # the watch receiver's folder (#227), as compose's WATCH_DIR: empty until test_9
os.environ.update(SANGUITY_DATA=DATA, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(DATA, "build"),
                  SANGUITY_WATCH_FILE=os.path.join(STORE, "public", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  SANGUITY_WATCH_STORE=STORE, SANGUITY_WATCH_KEYS=os.path.join(STORE, "keys", "receiver-secrets.json"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"), SANGUITY_BACKUPS=FOLDER)
for d in ("records", "keys", "watch/public", "watch/keys"):
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
                          ("/api/backups/replaced/{name}", "DELETE"), ("/api/backups/schedule", "PUT")])

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

    # ---------------------------------------------------------------- the watch receiver's data (#227)

    def test_9_phone_and_watch_data(self):
        """The receiver's databases and archived uploads go in a backup as they are (sealed); its summaries and keys
        don't. A check opens and counts them; a restore hands them to the receiver, which moves them in when it starts,
        keeping what it had aside, and brings the readings back. Without a receiver, nothing changes."""
        # no receiver data yet: the backup has none, and says so
        st = self.status()["watch"]   # only public/ and keys/, as a compose file from before #227 mounts them
        self.assertEqual((st["folder"], st["visible"], st["found"]), (STORE, False, False))
        os.makedirs(os.path.join(STORE, "raw"))   # the receiver's own folder, empty: seen, nothing in it yet
        self.assertEqual((self.status()["watch"]["visible"], self.status()["watch"]["found"]), (True, False))
        os.rmdir(os.path.join(STORE, "raw"))
        plain = self.backup_now()["file"]
        self.assertNotIn("watch", backup.verify(os.path.join(FOLDER, plain)))
        self.assertIsNone(self.check(plain)["result"]["watch"])
        # the receiver: the first profile's readings, a second profile's, cycle records kept while tracking is on, and
        # an upload archived after its last save (the receiver stopped before folding it in)
        rx.DATA, rx.KEYS_FILE = STORE, os.path.join(STORE, "keys", "receiver-secrets.json")
        first, other = rx.Store(""), rx.Store("5")
        for store, rows, seq in ((first, BP, 1), (other, BP[:2], 1)):
            body = json.dumps(ldc(rows, seq)).encode()
            rx.archive(body, store.slot)
            self.assertTrue(store.ingest(json.loads(body), hashlib.sha256(body).hexdigest()).endswith("records"))
        first.db.execute("INSERT INTO cycle(app, kind, id, src, day) VALUES('hc', 'flow', 'c-1', 'Fictional Phone', '2024-03-09')")
        for store in (first, other):
            store.save()
            store.write_summary()
        late = json.dumps(ldc([("2024-03-12 07:10:00", 118, 76, 64)], 2)).encode()
        time.sleep(0.01)
        rx.archive(late, "")
        st = self.status()["watch"]
        self.assertEqual((st["found"], st["profiles"], st["after"]), (True, 2, 1), st)
        self.assertGreater(st["size"], 0)

        run = self.backup_now()
        path = os.path.join(FOLDER, run["file"])
        manifest = backup.verify(path)
        names = set(manifest["files"])
        self.assertTrue({"watch/watch.db", "watch/profiles/5/watch.db"} <= names, names)
        self.assertEqual(sum(n.startswith("watch/raw/") for n in names), 2)
        self.assertEqual(sum(n.startswith("watch/profiles/5/raw/") for n in names), 1)
        self.assertFalse([n for n in names if n.startswith(("watch/public/", "watch/keys/"))], "summaries and keys stay out")
        with tarfile.open(path) as tar:   # as it is on disk: sealed with the data key, never opened up
            data = tar.extractfile("watch/watch.db").read()
        self.assertEqual(data, read(os.path.join(STORE, "watch.db")))
        self.assertTrue(data.startswith(crypto.MAGIC))
        self.assertNotIn(b"Fictional Cuff", data)
        w = manifest["watch"]
        self.assertEqual(set(w), {"", "5"})
        self.assertEqual(w[""]["counts"], backup._tables(first.db))   # every table's rows, as the receiver had them
        self.assertEqual((w[""]["uploads"], w[""]["after"], w["5"]["uploads"], w["5"]["after"]), (2, 1, 1, 0), w)
        self.assertEqual(w[""]["counts"]["cycle"], 1, "a backup made while tracking is on has the cycle records")
        self.assertTrue(w[""]["saved"].endswith("+00:00"))

        # Check a backup counts them, beside the live data
        res = self.check(run["file"])
        self.assertTrue(res["ok"], res)
        cw = res["result"]["watch"]
        self.assertEqual((cw["profiles"], cw["after"]), (2, 1))
        live = backup.watch_words({s.slot: backup._tables(s.db) for s in (first, other)})["readings"]
        self.assertEqual(live, len(BP) + 2)
        self.assertEqual(cw["counts"]["readings"], {"backup": live, "expected": live})
        self.assertEqual(cw["counts"]["uploads"], {"backup": 3, "expected": 3})
        self.assertNotIn("cycle", cw["counts"], "compared, but not named to admins (#183: owners and editors only)")
        self.assertEqual(cw["now"]["readings"], live)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            manage.run(["check", path])
        self.assertIn(f"the phone and watch data of 2 profiles ({live} readings, 3 archived uploads)", out.getvalue())

        # damage a checksum can't see: a database with a reading fewer, one that isn't a database, one another key sealed
        def fewer(name, data):
            if name != "watch/watch.db":
                return data
            con = backup._open_watch_db(data)
            con.execute("DELETE FROM samples WHERE rowid IN (SELECT rowid FROM samples LIMIT 1)")
            con.commit()
            return crypto.seal(con.serialize())
        cases = {"fewer": (fewer, "rows in samples"),
                 "garbage": (lambda n, d: crypto.seal(b"not a database" * 50) if n == "watch/profiles/5/watch.db" else d,
                             "profile 5's data can't be read")}
        for label, (change, said) in cases.items():
            bad = rewrite(path, os.path.join(TMP, f"watch-{label}.tar.gz"), change)
            got = backup.check(bad)
            self.assertFalse(got["ok"], label)
            self.assertTrue(any(said in p for p in got["problems"]), (label, got["problems"]))

        # restore (on a machine with the app stopped): the records as usual, and the receiver's data staged for it
        changed = json.dumps(ldc([("2024-03-13 07:00:00", 140, 90, 80)], 3)).encode()   # after the backup: put aside
        first.ingest(json.loads(changed), hashlib.sha256(changed).hexdigest())
        first.save()
        after_backup = read(os.path.join(STORE, "watch.db"))
        public_dir = os.stat(os.path.join(STORE, "public")).st_ino
        new_data = os.path.join(TMP, "restored-data")
        with mock.patch.object(backup, "_app_is_running", return_value=False), \
                mock.patch.multiple(config, DATA_DIR=new_data, DB_PATH=os.path.join(new_data, "sanguity.db")):
            os.makedirs(new_data)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                manage.run(["restore", path, "--yes"])
            stage = os.path.join(STORE, "keys", "restore")
            self.assertIn(f"waiting for the watch receiver in {stage}", out.getvalue())
            self.assertIn("Restart the receiver", out.getvalue())
            self.assertTrue(os.path.isfile(os.path.join(new_data, "sanguity.db")))
            self.assertFalse(os.path.exists(os.path.join(new_data, "watch")), "the receiver's data isn't put in DATA_DIR")
            self.assertEqual(sorted(os.listdir(stage)), ["profiles", "raw", "restore.json", "watch.db"])
            self.assertEqual(read(os.path.join(stage, "watch.db")), data)
            self.assertEqual(read(os.path.join(STORE, "watch.db")), after_backup, "nothing moved until it starts")
            # where the keys folder can't be written, the data goes beside the records, to be moved in by hand
            time.sleep(1.05)   # each restore's before-restore-<time>/ is its own
            with mock.patch.object(config, "WATCH_KEYS", os.path.join(TMP, "no-keys-here", "receiver-secrets.json")):
                m, aside = backup.restore(path)
            self.assertFalse(m["watch_restored"]["staged"])
            self.assertTrue(os.path.isfile(os.path.join(m["watch_restored"]["folder"], "watch.db")))
            self.assertTrue(m["watch_restored"]["folder"].startswith(os.path.join(new_data, "watch-restored-")))
            time.sleep(1.05)
            m, aside = backup.restore(path)   # staged again, as the operator would
            self.assertTrue(m["watch_restored"]["staged"])

        # the receiver starts: what it had goes aside, the backup's comes in, the summary is written again from it, and
        # the upload that came after the backup's snapshot is folded in
        aside = rx.apply_restore()
        self.assertTrue(os.path.isdir(aside) and os.path.dirname(aside) == STORE)
        self.assertEqual(read(os.path.join(aside, "watch.db")), after_backup)
        self.assertTrue(os.path.isfile(os.path.join(aside, "public", "daily.json")))
        self.assertEqual(os.stat(os.path.join(STORE, "public")).st_ino, public_dir, "public/ stays: the app has it mounted")
        self.assertFalse(os.path.exists(os.path.join(STORE, "public", "daily.json")))
        self.assertFalse(os.path.exists(stage))
        self.assertIsNone(rx.apply_restore(), "once")
        back = rx.Store("")
        self.assertEqual(back.catch_up(), 1)
        back.save()
        back.write_summary()
        days = dict(next(m["pts"] for m in json.loads(crypto.read_file(os.path.join(STORE, "public", "daily.json")))["metrics"]
                         if m["key"] == "bp_n"))
        self.assertEqual(days, {"2024-03-09": 3, "2024-03-10": 1, "2024-03-11": 1, "2024-03-12": 1}, days)
        self.assertEqual(backup._tables(rx.Store("5").db), backup._tables(other.db), "the second profile's too")

    # ---------------------------------------------------------------- a file removed while a backup is made (#592)

    def photo_entry(self, title):
        """A journal entry with a photo, as the journal page saves one: (entry id, the photo's name in a backup)."""
        r = self.robin.put(f"/api/profiles/{self.pid}/journal/photos", content=tiny_png(), headers={**H, "Content-Type": "image/png"})
        self.assertEqual(r.status_code, 201, r.text)
        ph = r.json()["id"]
        r = self.robin.post(f"/api/profiles/{self.pid}/journal", json={"kind": "symptom", "t": "2026-11-21", "title": title,
                                                                       "severity": 2, "photos": [{"id": ph, "alt": "a fictional rash"}]}, headers=H)
        self.assertEqual(r.status_code, 201, r.text)
        db = dbm.connect()
        file = db.execute("SELECT file FROM journal_photos WHERE id=?", (ph,)).fetchone()[0]
        db.close()
        return r.json()["id"], f"profiles/{self.pid}/journal/{file}"

    def lists_and_holds(self, path):
        """(the photos a backup's database lists, the files it holds) of the archive at path."""
        with tarfile.open(path, "r:gz") as tar:
            names = set(tar.getnames())
            with tempfile.TemporaryDirectory() as t:
                with open(os.path.join(t, "db"), "wb") as f:
                    f.write(tar.extractfile("sanguity.db").read())
                con = sqlite3.connect(os.path.join(t, "db"))
                listed = {f"profiles/{p}/journal/{n}" for p, n in con.execute("SELECT profile_id, file FROM journal_photos")}
                con.close()
        return listed, names

    def test_10_a_file_removed_while_backing_up(self):
        """An entry deleted while its photo would be read: the backup's database doesn't list a photo it hasn't, and
        Check says it's fine; a backup missing a file its database names is reported by Check."""
        os.makedirs(FOLDER, exist_ok=True)   # (it runs second, before Back up now has made the folder)
        for when in ("tick", "snapshot"):   # at the first progress tick (the proof's), and just after the snapshot
            jid, photo = self.photo_entry(f"Rash ({when})")
            gone = []

            def delete():
                if not gone:
                    gone.append(1)
                    self.assertEqual(self.robin.delete(f"/api/profiles/{self.pid}/journal/{jid}", headers=H).status_code, 200)
            if when == "tick":
                path = backup.create(FOLDER, progress=lambda d, t: delete(), name=backup.new_name(FOLDER))
            else:
                real = backup._snapshot
                with mock.patch.object(backup, "_snapshot", lambda copy: (real(copy), delete())[0]):
                    path = backup.create(FOLDER, name=backup.new_name(FOLDER))
            self.assertTrue(gone)
            listed, held = self.lists_and_holds(path)
            self.assertFalse(listed - held, f"{when}: the backup's database lists a photo the backup hasn't")
            got = backup.check(path)
            self.assertTrue(got["ok"], (when, got["problems"]))
            os.remove(path)
        # a backup whose database names a photo and an upload's file it hasn't: Check says so, in words
        jid, photo = self.photo_entry("Rash (kept)")
        path = backup.create(FOLDER, name=backup.new_name(FOLDER))
        db = dbm.connect()
        up = db.execute("SELECT id, manifest FROM uploads WHERE profile_id=? AND state='ready' ORDER BY id", (self.pid,)).fetchone()
        db.close()
        upload = f"profiles/{self.pid}/uploads/{up['id']}/" + json.loads(crypto.dec(up["manifest"]))[0]["f"]
        with tarfile.open(path, "r:gz") as tar:
            items = [(m.name, tar.extractfile(m).read()) for m in tar.getmembers()]
        self.assertTrue({photo, upload} <= {n for n, _ in items})
        manifest = json.loads(dict(items)["manifest.json"])
        thin = os.path.join(FOLDER, backup.new_name(FOLDER, datetime_later(path)))
        with tarfile.open(thin, "w:gz") as tar:
            for n, data in items:
                if n in (photo, upload):
                    manifest["files"].pop(n)
                    continue
                data = json.dumps(manifest).encode() if n == "manifest.json" else data
                info = tarfile.TarInfo(n)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        got = backup.check(thin)
        self.assertFalse(got["ok"], got)
        self.assertTrue(any("names files that aren't in it" in p and "1 journal photo" in p and "1 uploaded file" in p
                            for p in got["problems"]), got["problems"])
        self.assertTrue(backup.check(path)["ok"])
        self.robin.delete(f"/api/profiles/{self.pid}/journal/{jid}", headers=H)
        self.clear_folder()


    # ---------------------------------------------------------------- what a restore replaced (#595)

    @staticmethod
    def aside(days_ago, name=None, top=DATA):
        """A before-restore-<time>/ folder as restore() leaves it, made days_ago: a database and a profile's file."""
        t = time.time() - days_ago * 86400
        d = os.path.join(top, name or "before-restore-" + time.strftime("%Y%m%d-%H%M%S", time.localtime(t)))
        os.makedirs(os.path.join(d, "profiles", "9"))
        for p, data in (("sanguity.db", b"a deleted profile's records, sealed"),
                        ("profiles/9/note.bin", b"a deleted profile's document, sealed")):
            with open(os.path.join(d, p), "wb") as f:
                f.write(data)
        os.utime(d, (t, t))
        return d

    def test_9b_what_a_restore_replaced(self):
        """What a restore moved aside (it may hold a profile deleted since) is deleted RESTORE_KEEP after the restore,
        at start and now and then while the app runs, or by an admin's Delete now; only folders named as restore()
        names them. It is never in a backup."""
        keep = backup.RESTORE_KEEP / 86400
        self.assertEqual(keep, 30)
        old = self.aside(keep + 1)
        again = self.aside(keep + 1, os.path.basename(old) + "-1")   # a second restore in the same second
        recent = self.aside(1)
        touched = self.aside(keep + 5)   # old by its name, but changed since: kept from its last change
        t = time.time() - 2 * 86400
        os.utime(touched, (t, t))
        notes = self.aside(keep * 3, "before-restore-notes")   # not a name restore() gives: left alone
        outside = self.aside(keep * 3, "elsewhere", TMP)
        link = os.path.join(DATA, "before-restore-20200101-000000")   # a link with restore()'s name: left alone
        os.symlink(outside, link)
        st = self.status()
        self.assertEqual(st["replaced_days"], 30)
        self.assertEqual([r["name"] for r in st["replaced"]],   # oldest first
                         [os.path.basename(p) for p in (old, again, touched, recent)], st["replaced"])
        row = next(r for r in st["replaced"] if r["name"] == os.path.basename(recent))
        self.assertAlmostEqual(row["deletes"] - row["made"], backup.RESTORE_KEEP, delta=1)
        self.assertAlmostEqual(row["made"], time.time() - 86400, delta=5)

        # at start: those past their time go, the others stay, and what a deletion stopped halfway left goes
        halfway = self.aside(0, backup.DELETING + "before-restore-20200101-000000")
        backup_jobs.stop()
        try:
            db = dbm.connect()
            try:
                backup_jobs.recover(db)
            finally:
                db.close()
            for d in (old, again, halfway):
                self.assertFalse(os.path.exists(d), d)
            for d in (recent, touched, notes, link, outside):
                self.assertTrue(os.path.exists(d), d)
            self.assertTrue(os.path.isfile(os.path.join(outside, "sanguity.db")), "a link's target is never touched")
            logged = self.actions("backup.replaced")
            self.assertEqual(sorted(e["detail"]["name"] for e in logged), sorted(os.path.basename(d) for d in (old, again)))
            self.assertEqual({(e["actor"], e["detail"]["days"]) for e in logged}, {("time limit", 30)})
            # while the app runs: the backups thread looks again an hour on
            t = time.time() - (keep + 1) * 86400
            os.utime(touched, (t, t))
            self.assertEqual(backup_jobs.sweep_replaced(now=time.time() + 60), [], "not again within the hour")
            self.assertTrue(os.path.exists(touched))
            self.assertEqual(backup_jobs.sweep_replaced(now=time.time() + 3700), [os.path.basename(touched)])
            self.assertFalse(os.path.exists(touched))

            # a backup made after a restore doesn't hold it (nor the receiver's, test_9's): the database, profiles/, build/, watch/
            name = self.backup_now_by_hand()
            files = backup.verify(os.path.join(FOLDER, name))["files"]
            self.assertFalse([n for n in files if "before-restore" in n], files)
            self.assertLessEqual({n.split("/")[0] for n in files}, {"sanguity.db", "profiles", "build", "watch"}, files)
            # ...nor after a real restore, into another data folder
            new_data = os.path.join(TMP, "restored-595")
            os.makedirs(new_data)
            shutil.copy(os.path.join(DATA, "sanguity.db"), new_data)   # a database there for the restore to replace
            with mock.patch.object(backup, "_app_is_running", return_value=False), \
                    mock.patch.multiple(config, DATA_DIR=new_data, DB_PATH=os.path.join(new_data, "sanguity.db")):
                _, moved = backup.restore(os.path.join(FOLDER, name))
                self.assertEqual(os.path.dirname(moved), new_data)
                self.assertTrue(os.path.isfile(os.path.join(moved, "sanguity.db")))
                self.assertEqual([r["name"] for r in backup.replaced()], [os.path.basename(moved)])
                after = backup.create(os.path.join(TMP, "after-595"))
                self.assertFalse([n for n in backup.verify(after)["files"] if "before-restore" in n])
        finally:
            backup_jobs.start()

        # Delete now: admins only, by name only, with the fresh check, and logged
        gone = os.path.basename(recent)
        path = f"/api/backups/replaced/{gone}"
        for c, code in ((client(), 401), (self.robin, 403)):
            self.assertEqual(c.delete(path, headers=H).status_code, code)
        self.assertEqual(self.ada.delete(path).status_code, 403, "the CSRF check")
        for bad in ("before-restore-notes", os.path.basename(link), "elsewhere", "before-restore-20990101-000000",
                    "sanguity.db", "profiles", "..%2Fprofiles", "before-restore-x%2F..%2F..%2Fprofiles"):
            r = self.ada.delete(f"/api/backups/replaced/{bad}", headers=H)
            self.assertEqual(r.status_code, 404, (bad, r.text))
        db = dbm.connect()
        db.execute("UPDATE sessions SET verified_at=0 WHERE user_id=?", (self.uids["ada"],))
        db.close()
        r = self.ada.delete(path, headers=H)
        self.assertEqual((r.status_code, r.json()["detail"]), (403, "reauth_required"))
        self.assertTrue(os.path.isdir(recent), "nothing deleted")
        self.assertEqual(self.ada.post("/api/me/reauth", json={"secret": PW["ada"]}, headers=H).status_code, 200)
        with mock.patch.object(os, "rename", side_effect=PermissionError("read-only")):   # can't be moved aside: left whole
            r = self.ada.delete(path, headers=H)
        self.assertEqual(r.status_code, 500, r.text)
        self.assertIn("couldn't be deleted, and is as it was", r.json()["detail"])
        self.assertTrue(os.path.isfile(os.path.join(recent, "profiles", "9", "note.bin")))
        r = self.ada.delete(path, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["replaced"], [])
        self.assertFalse(os.path.exists(recent))
        self.assertEqual(self.ada.delete(path, headers=H).status_code, 404, "once")
        by = [e for e in self.actions("backup.replaced") if e["detail"]["name"] == gone]
        self.assertEqual([(e["actor"], e["detail"].get("days")) for e in by], [("ada", None)])
        for d in (notes, link, outside, os.path.join(DATA, "profiles")):
            self.assertTrue(os.path.exists(d), d)
        os.remove(link)
        shutil.rmtree(notes)


def tiny_png():
    """A one-pixel PNG."""
    import struct, zlib

    def chunk(t, b):
        return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))


def datetime_later(path):
    """A time a minute after the backup at path was made: another backup's name beside it."""
    import datetime as dt
    return dt.datetime.fromtimestamp(backup.made_at(path)) + dt.timedelta(minutes=1)


# the watch receiver (#227), loaded as tests/test_receiver_*.py do, and a fictional cuff's readings
_spec = importlib.util.spec_from_file_location("watch_receiver", os.path.join(ROOT, "watch", "receiver.py"))
rx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rx)
from server import config   # noqa: E402
BP = [("2024-03-09 07:05:00", 128, 82, 71), ("2024-03-09 21:40:00", 121, 78, None), ("2024-03-09 22:30:00", 135, 88, 76),
      ("2024-03-10 07:00:00", 124, 80, 68), ("2024-03-11 06:55:00", 119, 77, 66)]


def read(path):
    with open(path, "rb") as f:
        return f.read()


def ldc(rows, seq):
    """Life Dashboard Companion's upload of blood pressure readings (local times in America/Chicago, -0600)."""
    import datetime as dt
    utc = lambda t: (dt.datetime.fromisoformat(t) + dt.timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"source": "health_connect", "timezone": "America/Chicago", "sequence": seq, "timestamp": "2024-03-13T13:00:00Z",
            "blood_pressure": [{"uuid": f"bp-{t}", "time": utc(t), "systolic": s, "diastolic": d, "source": "Fictional Cuff",
                                **({"pulse": p} if p is not None else {})} for t, s, d, p in rows]}


if __name__ == "__main__":
    unittest.main(verbosity=2)
