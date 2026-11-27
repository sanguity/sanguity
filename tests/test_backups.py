#!/usr/bin/env python3
"""Backups on the Admin page (#200; server/backup_jobs.py, server/routes_backups.py, server/backup.py): Back up now
in the background, the schedule's backups (daily, weekly, off), pruning only Sanguity's own backup files, the late
warning (and a failed backup), Check a backup on a good backup and on damaged ones, that a check never touches the
live data, permission refusals, the activity log, and what a stop in the middle leaves behind; and the watch receiver's
data in backups (#227): taken as it is, counted by a check, and put back by a restore. Fictional data only.

  python3 tests/test_backups.py
"""
import base64, contextlib, hashlib, importlib.util, io, json, os, shutil, sqlite3, stat, sys, tarfile, tempfile, threading, time, unittest
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

from server import accounts, atrest, backup, backup_jobs, crypto, db as dbm, main, manage, profiles as prof, twofactor, uploads   # noqa: E402
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
    from server import config
    files, data = {}, config.DATA_DIR   # (the data folder as the app has it: a test may point it elsewhere, #1055)
    for root, dirs, names in os.walk(data):
        for n in names:
            p = os.path.join(root, n)
            if n.startswith("sanguity.db") or n == "app.lock":
                continue
            with open(p, "rb") as f:
                files[os.path.relpath(p, data)] = (hashlib.sha256(f.read()).hexdigest(), os.stat(p).st_mtime_ns)
    con = sqlite3.connect(f"file:{os.path.join(data, 'sanguity.db')}?mode=ro", uri=True)
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
        self.assertGreater(backup.counts_of(manifest["counts"])["events"], 0)   # what a check expects, recorded at backup time
        self.assertTrue(manifest["counts"].startswith(crypto.FIELD), "sealed (#935): the plain manifest names no table")
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
        # the plain manifest names none of their tables (#935): their rows are sealed
        with tarfile.open(path) as tar:
            plain = tar.extractfile("manifest.json").read().decode()
        for t in ("cycle", "samples", "events", "journal", "share_links"):
            self.assertNotIn(f'"{t}"', plain, t)
        self.assertTrue(all(v["counts"].startswith(crypto.FIELD) for v in w.values()), w)
        counts = backup.counts_of(w[""]["counts"])
        self.assertEqual(counts, backup._tables(first.db))   # every table's rows, as the receiver had them
        self.assertEqual((w[""]["uploads"], w[""]["after"], w["5"]["uploads"], w["5"]["after"]), (2, 1, 1, 0), w)
        self.assertEqual(counts["cycle"], 1, "a backup made while tracking is on has the cycle records")
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
        # a backup from before #935, its counts plain, is checked as before
        with tarfile.open(path, "r:gz") as tar:
            items = [(m.name, tar.extractfile(m).read()) for m in tar.getmembers()]
        old = json.loads(dict(items)["manifest.json"])
        old["counts"] = backup.counts_of(old["counts"])
        for v in old["watch"].values():
            v["counts"] = backup.counts_of(v["counts"])
        before935 = os.path.join(TMP, "before-935.tar.gz")
        with tarfile.open(before935, "w:gz") as tar:
            for n, body in items:
                body = json.dumps(old).encode() if n == "manifest.json" else body
                info = tarfile.TarInfo(n)
                info.size = len(body)
                tar.addfile(info, io.BytesIO(body))
        got = backup.check(before935)
        self.assertTrue(got["ok"], got["problems"])
        self.assertEqual(got["watch"]["counts"]["readings"], {"backup": live, "expected": live})
        os.remove(before935)

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
            # listed beside what the restore replaced, deleted as long after (#603; test_9c has the rest)
            left = os.path.basename(m["watch_restored"]["folder"])
            listed = {r["name"]: r for r in backup.replaced()}
            self.assertEqual((listed[left]["kind"], listed[os.path.basename(aside)]["kind"]), ("watch", "data"))
            self.assertAlmostEqual(listed[left]["deletes"] - listed[left]["made"], backup.RESTORE_KEEP, delta=1)
            with mock.patch.object(config, "WATCH_KEYS", os.path.join(TMP, "no-keys-here", "receiver-secrets.json")):
                again, _ = backup._stage_watch(path, backup.verify(path), left[len("watch-restored-"):])
            self.assertEqual(os.path.basename(again), left + "-1", "a second restore in the same second: its own")
            shutil.rmtree(again)
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

    # ---------------------------------------------------------------- a profile's deletion leaves no folder (#932)

    def doomed(self, name):
        """A profile with a file in its folder, as a journal photo or an upload leaves one: (its id, the file)."""
        db = dbm.connect()
        try:
            pid = prof.create_profile(db, name, owner_id=self.uids["robin"])
        finally:
            db.close()
        path = os.path.join(prof.profile_dir(pid), "journal", "kept.img")
        os.makedirs(os.path.dirname(path))
        with open(path, "wb") as f:
            f.write(b"a fictional photo, sealed")
        return pid, path

    def test_11_a_deleted_profiles_folder_goes_with_it(self):
        """The folder is moved aside only under the write lock: a save holding it writes first, and what it wrote goes
        with the profile; a deletion that can't get the lock moves nothing; one that fails puts the folder back beside
        what was written meanwhile, never onto it; and what a stop part-way leaves is put back or removed at the next
        start, the id count moved past it."""
        aside = lambda pid: os.path.join(DATA, "profiles", f".deleting-{pid}")
        # a pull saving what its source sent holds the write lock: the deletion waits, then takes that too
        pid, _ = self.doomed("Deleted while a pull saves (fictional)")
        holder = dbm.connect()
        holder.execute("BEGIN IMMEDIATE")
        out = {}

        def delete(pid=pid):
            db = dbm.connect()
            try:
                out["gone"] = prof.delete_profile(db, pid)
            finally:
                db.close()
        t = threading.Thread(target=delete)
        t.start()
        time.sleep(0.4)
        moved_early = os.path.exists(aside(pid))
        store = os.path.join(prof.profile_dir(pid), "connections", "1")
        os.makedirs(os.path.dirname(store), exist_ok=True)
        with open(store, "wb") as f:   # as sync._keep_store writes it, inside its transaction
            f.write(b"what a fictional source sent, sealed")
        holder.execute("COMMIT")
        holder.close()
        t.join(30)
        self.assertEqual(out["gone"]["name"], "Deleted while a pull saves (fictional)")
        self.assertFalse(moved_early, "not moved before the deletion has the write lock")
        self.assertFalse(os.path.exists(prof.profile_dir(pid)), "what the save wrote went with the profile")
        self.assertFalse(os.path.exists(aside(pid)))

        # it can't get the lock (a save holding it past the busy timeout): nothing is moved, and what the save writes
        # meanwhile is beside the profile's files
        pid, kept = self.doomed("Not deleted: busy (fictional)")
        holder = dbm.connect()
        holder.execute("BEGIN IMMEDIATE")
        late = os.path.join(prof.profile_dir(pid), "connections", "1")

        def write_late():
            time.sleep(0.1)
            os.makedirs(os.path.dirname(late), exist_ok=True)
            with open(late, "wb") as f:
                f.write(b"what a fictional source sent, sealed")
        w = threading.Thread(target=write_late)
        w.start()
        db = dbm.connect()
        db.execute("PRAGMA busy_timeout=400")
        try:
            with self.assertRaises(sqlite3.OperationalError):
                prof.delete_profile(db, pid)
        finally:
            w.join()
            holder.execute("ROLLBACK")
            holder.close()
        self.assertTrue(db.execute("SELECT 1 FROM profiles WHERE id=?", (pid,)).fetchone())
        db.close()
        self.assertTrue(os.path.isfile(kept) and os.path.isfile(late), "the profile's files where they were")
        self.assertFalse(os.path.exists(aside(pid)))

        # it fails once the folder is aside, after an upload's file arrived in a new profiles/<id>/: put back beside it
        arrived = os.path.join(prof.profile_dir(pid), "uploads", "77", "piece")
        from server import labmap

        def fail(db, p):
            os.makedirs(os.path.dirname(arrived))
            with open(arrived, "wb") as f:
                f.write(b"a fictional upload's piece, sealed")
            raise RuntimeError("a fictional failure")
        db = dbm.connect()
        try:
            with mock.patch.object(labmap, "forget_profile", fail), self.assertRaises(RuntimeError):
                prof.delete_profile(db, pid)
        finally:
            db.close()
        self.assertTrue(all(os.path.isfile(p) for p in (kept, late, arrived)), os.listdir(prof.profile_dir(pid)))
        self.assertFalse(os.path.exists(aside(pid)), "nothing left aside")
        self.assertEqual(self.robin.get(f"/api/profiles/{pid}/journal").status_code, 200, "the profile is still there")

        # a stop part-way: a live profile's folder aside (before the deletion was saved) is put back; a deleted one's
        # (after), and a folder written after its profile was deleted, go; nothing else in profiles/ is touched
        os.replace(prof.profile_dir(pid), aside(pid))
        os.makedirs(prof.profile_dir(pid))
        with open(os.path.join(prof.profile_dir(pid), "new"), "wb") as f:
            f.write(b"written since, sealed")
        db = dbm.connect()
        top = db.execute("SELECT seq FROM sqlite_sequence WHERE name='profiles'").fetchone()[0]
        gone = [os.path.join(DATA, "profiles", n) for n in (f".deleting-{top + 5}", str(top + 9))]
        for d in gone:
            os.makedirs(os.path.join(d, "watch"))
            with open(os.path.join(d, "watch", "daily.json"), "wb") as f:
                f.write(b"a deleted profile's watch data, sealed")
        notes, outside = os.path.join(DATA, "profiles", "notes"), os.path.join(TMP, "outside-932")
        os.makedirs(notes)
        os.makedirs(outside)
        link = os.path.join(DATA, "profiles", str(top + 12))
        os.symlink(outside, link)
        try:
            self.assertEqual(prof.sweep(db), 2)
            self.assertTrue(all(os.path.isfile(p) for p in (kept, late, arrived, os.path.join(prof.profile_dir(pid), "new"))))
            self.assertFalse(os.path.exists(aside(pid)))
            for d in gone:
                self.assertFalse(os.path.exists(d), d)
            self.assertTrue(os.path.isdir(notes) and os.path.islink(link) and os.path.isdir(outside))
            self.assertGreater(prof.create_profile(db, "Made after (fictional)", owner_id=self.uids["robin"]), top + 9,
                               "never one of their ids")
        finally:
            os.remove(link)
            shutil.rmtree(notes)
            db.close()

    # ---------------------------------------------------------------- readable only by the app's own user (#935)

    def test_12_readable_only_by_the_apps_user(self):
        """The app makes what it keeps with umask 077: the database and its WAL 0600, the data folder and the profiles'
        0700; what an older version left (0644, 0755) is made so at start; a link is never followed."""
        mode = lambda p: stat.S_IMODE(os.lstat(p).st_mode)
        db = dbm.connect()   # (open, after a write: the WAL is there)
        try:
            db.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('test.935', '1')")
            for p in (DATA, os.path.join(DATA, "profiles"), prof.profile_dir(self.pid)):
                self.assertEqual(mode(p), 0o700, p)
            for n in ("sanguity.db", "sanguity.db-wal", "sanguity.db-shm"):
                self.assertEqual(mode(os.path.join(DATA, n)), 0o600, n)
        finally:
            db.close()
        made = os.path.join(prof.profile_dir(self.pid), "made-935")
        os.makedirs(made)
        with open(os.path.join(made, "f"), "wb") as f:
            f.write(b"x")
        self.assertEqual((mode(made), mode(os.path.join(made, "f"))), (0o700, 0o600), "the process's umask is 077")
        # as an older version left them: made the app's user's alone at the next start
        outside = os.path.join(TMP, "outside-935")
        os.makedirs(outside)
        os.chmod(outside, 0o755)
        link = os.path.join(DATA, "profiles", "link-935")
        os.symlink(outside, link)
        try:
            for p, m in ((DATA, 0o755), (os.path.join(DATA, "sanguity.db"), 0o644), (made, 0o755), (os.path.join(made, "f"), 0o644)):
                os.chmod(p, m)
            self.assertGreaterEqual(atrest.keep_private(), 4)
            self.assertEqual([mode(p) for p in (DATA, os.path.join(DATA, "sanguity.db"), made, os.path.join(made, "f"))],
                             [0o700, 0o600, 0o700, 0o600])
            self.assertEqual(mode(outside), 0o755, "a link's target is never changed")
            self.assertEqual(atrest.keep_private(), 0, "once")
        finally:
            os.remove(link)
            shutil.rmtree(made)
            shutil.rmtree(outside)

    # ---------------------------------------------------------------- a restore keeps today's access (#917)

    def restore_into(self, folder, path, today=None, change=None, damage=None):
        """server.manage restore of the backup at path into a data folder of its own (the app stopped there), its
        database a copy of today's (or none: disaster recovery), changed by change(con) first, or its file by
        damage(path). Returns (what the restore said, the restored database opened)."""
        os.makedirs(folder)
        if today:
            src, dst = sqlite3.connect(os.path.join(DATA, "sanguity.db")), sqlite3.connect(os.path.join(folder, "sanguity.db"))
            src.backup(dst)
            src.close()
            if change:
                change(dst)
                dst.commit()
            dst.close()
            if damage:
                damage(os.path.join(folder, "sanguity.db"))
        out = io.StringIO()
        with mock.patch.object(backup, "_app_is_running", return_value=False), contextlib.redirect_stdout(out), \
                mock.patch.multiple(config, DATA_DIR=folder, DB_PATH=os.path.join(folder, "sanguity.db")):
            manage.run(["restore", path, "--yes"])
        con = sqlite3.connect(os.path.join(folder, "sanguity.db"))
        self.addCleanup(con.close)
        return out.getvalue(), con

    def test_13_a_restore_keeps_todays_access(self):
        """The owner's decision: a restore brings back records only. Access ended after the backup stays ended (a link
        revoked, a member removed and her calendar feed with it, her password changed and passkey removed, an account
        removed); access made after it is kept (an account, a member, a link, a feed, two-factor sign-in, the install's
        two-factor rule, a phone upload secret); every session ends; a profile deleted since is back with nobody's access
        and one made since isn't, both named. With no database to replace, or another install's, all of it is the
        backup's, and the restore says so."""
        pws = {"kim": "kim's fictional passphrase, long enough", "lee": "lee's fictional passphrase, long enough"}
        db = dbm.connect()
        uid = {n: accounts.create(db, n, password=pw)[0] for n, pw in pws.items()}
        robin = self.uids["robin"]
        pid = prof.create_profile(db, "Shared (fictional)", owner_id=robin)
        gone_pid = prof.create_profile(db, "Deleted after (fictional)", owner_id=robin)
        secret = lambda s: crypto.enc(json.dumps({"secret": s, "slot": str(pid), "created": 0, "by": robin}))
        db.execute("INSERT INTO settings(key, profile_id, sealed, changed) VALUES('_watch.receiver', ?, ?, 0)", (pid, secret("then")))
        db.close()
        self.assertEqual(self.robin.put(f"/api/profiles/{pid}/members", json={"username": "kim", "role": "editor"}, headers=H).status_code, 200)
        kim = client()
        self.assertEqual(kim.post("/api/login", json={"username": "kim", "password": pws["kim"]}, headers=H).status_code, 200)
        self.assertEqual(kim.post(f"/api/profiles/{pid}/calendar", json={"tz": "UTC"}, headers=H).status_code, 200)
        db = dbm.connect()   # a passkey of hers (signed in already: it would ask for it)
        db.execute("INSERT INTO passkeys(user_id, credential_id, public_key, rp_id, name, created) VALUES(?, 'kim-917', x'00', "
                   "'testserver', 'Kim''s phone (fictional)', 0)", (uid["kim"],))
        db.close()
        r = self.robin.post(f"/api/profiles/{pid}/links", json={"sections": ["journal"], "hours": 168}, headers=H)
        revoked = r.json()["link"]["id"]
        note = lambda t: self.robin.post(f"/api/profiles/{pid}/journal", json={"kind": "note", "t": "2026-04-03", "note": t}, headers=H)
        self.assertEqual(note("Written before the backup (fictional)").status_code, 201)
        path = backup.create(os.path.join(TMP, "backups-917"))

        # after the backup: access ended...
        self.assertEqual(self.robin.delete(f"/api/profiles/{pid}/links/{revoked}", headers=H).status_code, 200)
        self.assertEqual(self.robin.delete(f"/api/profiles/{pid}/members/{uid['kim']}", headers=H).status_code, 200)
        self.assertEqual(kim.post("/api/me/password", json={"current": pws["kim"], "new": "kim's new fictional passphrase, long"},
                                  headers=H).status_code, 200)
        db = dbm.connect()
        db.execute("DELETE FROM passkeys WHERE user_id=?", (uid["kim"],))
        accounts.remove(db, uid["lee"])
        prof.delete_profile(db, gone_pid)
        # ...and made
        mx = accounts.create(db, "max", password="max's fictional passphrase, long enough")[0]
        db.execute("UPDATE users SET totp_secret=? WHERE id=?", (crypto.enc(twofactor.new_secret()), uid["kim"]))
        new_pid = prof.create_profile(db, "Made after (fictional)", owner_id=robin)
        today = {r["username"]: dict(r) for r in db.execute("SELECT * FROM users")}
        db.close()
        self.assertEqual(self.robin.put(f"/api/profiles/{pid}/members", json={"username": "max", "role": "viewer"}, headers=H).status_code, 200)
        made = self.robin.post(f"/api/profiles/{pid}/links", json={"sections": ["journal"], "hours": 168}, headers=H).json()["link"]["id"]
        self.assertEqual(self.robin.post(f"/api/profiles/{pid}/calendar", json={"tz": "UTC"}, headers=H).status_code, 200)
        self.assertEqual(note("Written after the backup (fictional)").status_code, 201)

        def now(con):   # in today's database, as an admin and an owner would set them
            con.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('require_2fa', '1')")
            con.execute("UPDATE settings SET sealed=? WHERE key='_watch.receiver' AND profile_id=?", (secret("now"), pid))
        said, con = self.restore_into(os.path.join(TMP, "restored-917"), path, today=True, change=now)
        users = {r[0]: r[1:] for r in con.execute("SELECT username, pw_hash, totp_secret, is_admin, disabled FROM users")}
        self.assertEqual(set(users), {"ada", "robin", "kim", "max"}, "lee was removed after the backup; max made after it")
        self.assertEqual(users["kim"][0], today["kim"]["pw_hash"], "her old password doesn't sign in")
        self.assertEqual(users["kim"][1], today["kim"]["totp_secret"])
        self.assertIsNotNone(users["kim"][1])
        self.assertEqual(con.execute("SELECT COUNT(*) FROM passkeys WHERE user_id=?", (uid["kim"],)).fetchone()[0], 0)
        self.assertEqual(dict(con.execute("SELECT user_id, role FROM memberships WHERE profile_id=?", (pid,)).fetchall()),
                         {robin: "owner", mx: "viewer"})
        self.assertEqual(con.execute("SELECT user_id FROM calendar_feeds WHERE profile_id=?", (pid,)).fetchall(), [(robin,)])
        links = dict(con.execute("SELECT id, revoked IS NOT NULL FROM share_links WHERE profile_id=?", (pid,)).fetchall())
        self.assertEqual(links, {revoked: 1, made: 0})
        self.assertEqual(con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0, "everyone is signed out")
        self.assertEqual(con.execute("SELECT v FROM meta WHERE k='require_2fa'").fetchone(), ("1",))
        sealed = con.execute("SELECT sealed FROM settings WHERE key='_watch.receiver' AND profile_id=?", (pid,)).fetchone()[0]
        self.assertEqual(json.loads(crypto.dec(sealed))["secret"], "now")
        notes = [json.loads(crypto.dec(b)).get("note") for (b,) in con.execute("SELECT body FROM journal WHERE profile_id=?", (pid,))]
        self.assertEqual(notes, ["Written before the backup (fictional)"], "the records are the backup's")
        profiles = dict(con.execute("SELECT id, name FROM profiles").fetchall())
        self.assertEqual((profiles.get(gone_pid), new_pid in profiles), ("Deleted after (fictional)", False))
        self.assertEqual(con.execute("SELECT COUNT(*) FROM memberships WHERE profile_id=?", (gone_pid,)).fetchone()[0], 0)
        self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertGreaterEqual(con.execute("SELECT seq FROM sqlite_sequence WHERE name='profiles'").fetchone()[0], new_pid,
                                "ids never given again (#588)")
        self.assertIn("Today's accounts, passwords, passkeys, two-factor sign-in, who may see each profile, share links, "
                      "calendar feeds, phone upload secrets and connected sources' sign-ins were kept", said)
        self.assertRegex(said, r"Everyone is signed out \([1-9]\d* sign-ins? ended\)")
        self.assertIn(f"Profile {gone_pid} (Deleted after (fictional)) was deleted after the backup was made and is back, with "
                      "nobody given access to it", said)
        self.assertIn(f"Profile {new_pid} (Made after (fictional)) was made after the backup and isn't in it", said)

        # disaster recovery (no database to replace), and another install's database: all of it the backup's, said so
        def other(con):
            con.execute("UPDATE meta SET v=? WHERE k='pepper'", (crypto.enc(base64.b64encode(os.urandom(32)).decode()),))
        for label, today, change, words in (("none", False, None, "There was no database here to keep them from"),
                                            ("other", True, other, "The database it replaced isn't this install's")):
            said, con = self.restore_into(os.path.join(TMP, f"restored-917-{label}"), path, today=today, change=change)
            self.assertIn(words, said)
            self.assertIn(": the accounts, passwords, passkeys, two-factor sign-in, who may see each profile, share links, "
                          "calendar feeds, phone upload secrets and connected sources' sign-ins are the backup's, as they were on", said)
            self.assertIn("check Settings → Install → Accounts and each profile's Sharing", said)
            self.assertEqual(con.execute("SELECT revoked FROM share_links WHERE id=?", (revoked,)).fetchone(), (None,), label)
            self.assertIn("lee", {r[0] for r in con.execute("SELECT username FROM users")}, label)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0, label)
        db = dbm.connect()
        for p in (pid, new_pid):
            prof.delete_profile(db, p)
        for n in ("kim", "max"):
            accounts.remove(db, db.execute("SELECT id FROM users WHERE username=?", (n,)).fetchone()[0])
        db.close()

    def test_14_a_restore_keeps_todays_connected_sources(self):
        """A connected source's sign-in is access too (#917): a restore takes the connections and the vendors' apps as
        they are now. One disconnected since stays so, its refresh token not brought back and what it read (its store)
        dropped; one still connected keeps today's tokens and choices and the backup's place in what it pulled (its
        cursor), so it pulls again what came since; one connected since is kept, to pull from the start (what it
        brought isn't in the backup); a deleted profile's comes back disconnected, its store dropped; a vendor's app
        removed since is gone, one added since is there, each with today's client secret."""
        from server import sync
        robin, far = self.uids["robin"], dbm.now() + 10**6   # never due: the app's sync thread leaves them alone
        token = lambda refresh: crypto.enc(json.dumps({"refresh": refresh, "access": "a", "token_url": "https://fhir.example.test/token"}))
        db = dbm.connect()
        pid = prof.create_profile(db, "Connected (fictional)", owner_id=robin)
        gone_pid = prof.create_profile(db, "Connected, deleted after (fictional)", owner_id=robin)

        def app(n, secret):
            return db.execute("""INSERT INTO smart_clients(name, vendor, issuer, client_id, secret, auth, scopes, created)
                                 VALUES(?, 'epic', ?, ?, ?, 'confidential', 'patient/*.read', 0)""",
                              (f"Fictional portal {n}", f"https://fhir-{n}.example.test/r4", f"app-{n}", crypto.enc(secret))).lastrowid

        def connect(p, k, refresh):
            return db.execute("""INSERT INTO connections(profile_id, client_id, source, issuer, fhir_base, patient, credential,
                                 secret, scopes, expires, state, cursor, next_sync, created, created_by)
                                 VALUES(?, ?, 'Fictional portal', 'https://fhir.example.test/r4', 'https://fhir.example.test/r4', ?,
                                 'refresh', ?, 'patient/*.read', 0, 'active', '{"at": "then"}', ?, 0, ?)""",
                              (p, k, crypto.enc("pat-1"), token(refresh), far, robin)).lastrowid
        kept_app, removed_app = app("a", "app secret then"), app("b", "app secret then")
        dropped, kept, with_profile = connect(pid, kept_app, "refresh one"), connect(pid, kept_app, "refresh two"), \
            connect(gone_pid, removed_app, "refresh three")
        db.close()
        for p, c in ((pid, dropped), (pid, kept), (gone_pid, with_profile)):
            sync._save_store(p, c, {"resources": {"Observation/1": {"resourceType": "Observation", "id": "1"}}})
        path = backup.create(os.path.join(TMP, "backups-917c"))

        # after the backup: one disconnected, one's refresh token rotated and pulled on, a profile with one deleted, a
        # vendor's app removed, another's secret changed, a new app and a new connection
        db = dbm.connect()
        db.execute("UPDATE connections SET state='disconnected', secret=NULL, patient=NULL, disconnected=? WHERE id=?", (dbm.now(), dropped))
        sync.forget_store(pid, dropped)
        db.execute("""UPDATE connections SET secret=?, cursor='{"at": "now"}', last_sync=?, kinds='["labs"]' WHERE id=?""",
                   (token("refresh two, rotated"), dbm.now(), kept))
        prof.delete_profile(db, gone_pid)
        db.execute("DELETE FROM smart_clients WHERE id=?", (removed_app,))
        db.execute("UPDATE smart_clients SET secret=? WHERE id=?", (crypto.enc("app secret now"), kept_app))
        new_app = app("c", "app secret new")
        new = connect(pid, new_app, "refresh new")
        db.execute("""UPDATE connections SET cursor='{"at": "now"}', last_sync=? WHERE id=?""", (dbm.now(), new))
        db.close()

        said, con = self.restore_into(os.path.join(TMP, "restored-917c"), path, today=True)
        rows = {r[0]: r[1:] for r in con.execute("SELECT id, state, secret, patient, cursor, kinds, last_sync, first_job FROM connections")}
        refresh = lambda v: json.loads(crypto.dec(v))["refresh"] if v else None
        self.assertEqual((rows[dropped][0], rows[dropped][1], rows[dropped][2]), ("disconnected", None, None),
                         "disconnected since: its refresh token not brought back")
        self.assertEqual((rows[kept][0], refresh(rows[kept][1]), rows[kept][3], rows[kept][4]),
                         ("active", "refresh two, rotated", '{"at": "then"}', '["labs"]'),
                         "today's tokens and choices, the backup's cursor: it pulls again what came since")
        self.assertEqual((rows[new][0], refresh(rows[new][1]), rows[new][3], rows[new][5], rows[new][6]),
                         ("active", "refresh new", None, None, None), "connected since: kept, to pull from the start")
        self.assertEqual((rows[with_profile][0], rows[with_profile][1]), ("disconnected", None), "a deleted profile's")
        apps = {r[0]: crypto.dec(r[1]) for r in con.execute("SELECT id, secret FROM smart_clients")}
        self.assertEqual(apps, {kept_app: "app secret now", new_app: "app secret new"})
        store = lambda p, c: os.path.join(TMP, "restored-917c", "profiles", str(p), "connections", str(c))
        self.assertTrue(os.path.isfile(store(pid, kept)))
        for p, c in ((pid, dropped), (gone_pid, with_profile)):
            self.assertFalse(os.path.exists(store(p, c)), (p, c))
        self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertIn("connected sources' sign-ins were kept", said)
        db = dbm.connect()
        prof.delete_profile(db, pid)
        db.execute("DELETE FROM smart_clients WHERE id IN (?, ?)", (kept_app, new_app))
        db.close()

    def test_15_a_restore_and_the_access_the_pass_made(self):
        """#917's restore beside the pass's other access fixes. An account's wrong authenticator codes since its last
        right one (users.totp_fails, #928) are today's, as the rest of its row is: codes stopped since stay stopped, and
        working again since (a recovery code used) stay working. A phone upload secret taken away since with its
        maker's access (#929: the profile's _watch.receiver settings row, which the receiver's secrets file is written
        from) stays away, in the database and in that file. The lab-name matches reviews made (labmaps, each its
        profile's own since #927, keyed with it) are records, not access: each is what an import of that profile chose,
        forgotten with that import's Undo, and only the backup's imports are restored, so they're the backup's, each
        still its own profile's."""
        from server import labmap, watch_keys
        pws = {"noa": "noa's fictional passphrase, long enough", "uri": "uri's fictional passphrase, long enough"}
        robin = self.uids["robin"]
        db = dbm.connect()
        uid = {n: accounts.create(db, n, password=pw)[0] for n, pw in pws.items()}
        for n in pws:
            db.execute("UPDATE users SET totp_secret=? WHERE id=?", (crypto.enc(twofactor.new_secret()), uid[n]))
        db.execute("UPDATE users SET totp_fails=? WHERE id=?", (twofactor.STOP_AFTER, uid["uri"]))   # stopped at the backup
        pid = prof.create_profile(db, "Phone and matches (fictional)", owner_id=robin)
        other = prof.create_profile(db, "Another's matches (fictional)", owner_id=robin)
        db.close()
        self.assertEqual(self.robin.put(f"/api/profiles/{pid}/members", json={"username": "noa", "role": "editor"}, headers=H).status_code, 200)
        db = dbm.connect()   # the secret noa made (as watch_keys.rotate keeps it), for her phone
        db.execute("INSERT INTO settings(key, profile_id, sealed, changed) VALUES('_watch.receiver', ?, ?, 0)",
                   (pid, crypto.enc(json.dumps({"secret": "noa's", "slot": str(pid), "created": 0, "by": uid["noa"]}))))

        def match(p, name):   # a review's choice, as labmap.remember keeps it: the profile's own, keyed with it
            return db.execute("""INSERT INTO labmaps(lookup, importer, source, target, created_by, created_name, created, checked,
                                 profile_id) VALUES(?, 'fhir', ?, ?, ?, 'robin', 0, 0, ?)""",
                              (labmap.lookup("fhir", name, "mg/dL", "", p), crypto.enc(json.dumps({"name": name, "unit": "mg/dL", "loinc": ""})),
                               crypto.enc("Glucose"), robin, p)).lastrowid
        kept = match(pid, "Glucose, fasting (fictional)")
        db.close()
        path = backup.create(os.path.join(TMP, "backups-917a"))

        # after the backup: noa's codes stop and uri's work again; noa is removed, and her secret goes with her access;
        # the match is forgotten, and another profile's made
        db = dbm.connect()
        db.execute("UPDATE users SET totp_fails=? WHERE id=?", (twofactor.STOP_AFTER, uid["noa"]))
        accounts.code_right(db, uid["uri"])
        db.close()
        live_keys = os.path.join(TMP, "keys-917a", "receiver-secrets.json")
        os.makedirs(os.path.dirname(live_keys))
        with mock.patch.object(config, "WATCH_KEYS", live_keys):
            self.assertEqual(self.robin.delete(f"/api/profiles/{pid}/members/{uid['noa']}", headers=H).status_code, 200)
        self.assertNotIn(pid, {x["profile"] for x in json.loads(crypto.read_file(live_keys))["profiles"]})
        db = dbm.connect()
        self.assertIsNone(watch_keys.maker(db, pid))
        labmap.remove(db, kept)
        since = match(other, "Glucose (fictional)")
        db.close()

        folder = os.path.join(TMP, "restored-917a")
        said, con = self.restore_into(folder, path, today=True)
        self.assertIn("phone upload secrets", said)
        self.assertEqual(dict(con.execute("SELECT username, totp_fails FROM users WHERE username IN ('noa', 'uri')").fetchall()),
                         {"noa": twofactor.STOP_AFTER, "uri": 0}, "today's: noa's codes stay stopped, uri's working")
        self.assertIsNone(con.execute("SELECT 1 FROM settings WHERE key='_watch.receiver' AND profile_id=?", (pid,)).fetchone(),
                          "the secret noa made, gone with her access, isn't brought back")
        keys = os.path.join(TMP, "restored-917a-keys", "receiver-secrets.json")
        os.makedirs(os.path.dirname(keys))
        with mock.patch.multiple(config, DB_PATH=os.path.join(folder, "sanguity.db"), WATCH_KEYS=keys):
            restored = dbm.connect()
            try:
                watch_keys.publish(restored)   # as the app writes it when it starts on the restored database
            finally:
                restored.close()
        self.assertNotIn(pid, {x["profile"] for x in json.loads(crypto.read_file(keys))["profiles"]})
        mine = lambda c: {r[0]: tuple(r[1:]) for r in c.execute("SELECT id, profile_id, lookup FROM labmaps WHERE profile_id IN (?, ?)",
                                                                 (pid, other))}
        self.assertEqual(mine(con), {kept: (pid, labmap.lookup("fhir", "Glucose, fasting (fictional)", "mg/dL", "", pid))},
                         "the backup's matches, each its own profile's: records, not access")
        self.assertNotIn(since, mine(con))
        self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
        # what the backup itself holds, as a restore with no database to keep access from shows: her secret, uri's
        # codes stopped and noa's working
        _, con = self.restore_into(os.path.join(TMP, "restored-917a-none"), path)
        self.assertIsNotNone(con.execute("SELECT 1 FROM settings WHERE key='_watch.receiver' AND profile_id=?", (pid,)).fetchone())
        self.assertEqual(dict(con.execute("SELECT username, totp_fails FROM users WHERE username IN ('noa', 'uri')").fetchall()),
                         {"noa": 0, "uri": twofactor.STOP_AFTER})
        db = dbm.connect()
        for p in (pid, other):
            prof.delete_profile(db, p)
        for n in pws:
            accounts.remove(db, uid[n])
        db.close()

    def test_16_a_restore_and_records_updated_in_place(self):
        """#920 beside #917's restore (records only, the backup's): a portal's FHIR export and an insurer's claims
        spreadsheet uploaded, the backup made, then their next files, the medication stopped, the problem resolved and the
        claim processed, each updated in place (sync_changes keeping what they were, for the Undo). Restored, the records
        are the backup's: the medication current, the problem active, the claim pending, nothing of the later imports
        left, their changes' rows with them. And those next files, read against the restored database, again update the
        three in place, nothing added beside them."""
        from server import importers, preview, records
        robin = self.uids["robin"]

        def bundle(*resources):
            return json.dumps({"resourceType": "Bundle", "type": "collection",
                               "entry": [{"fullUrl": f"https://portal.example/fhir/{r['resourceType']}/{r['id']}", "resource": r} for r in resources]})
        status = lambda code: {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": code}]}   # noqa: E731
        pat = {"resourceType": "Patient", "id": "p1", "name": [{"family": "Example", "given": ["Pat"]}], "birthDate": "1980-02-02"}
        med = {"resourceType": "MedicationRequest", "id": "mr-1", "status": "active", "intent": "order", "subject": {"reference": "Patient/p1"},
               "medicationCodeableConcept": {"text": "Metoprolol succinate 25 mg tablet"}, "authoredOn": "2026-03-01"}
        cond = {"resourceType": "Condition", "id": "c-1", "subject": {"reference": "Patient/p1"}, "code": {"text": "Atrial fibrillation"},
                "clinicalStatus": status("active"), "onsetDateTime": "2026-02-20", "recordedDate": "2026-02-20"}
        head = "Claim Number,Date of Service,Provider,Description,Amount Billed,Plan Paid,You Owe,Claim Status,Insurer\n"
        claim = lambda paid, owe, st: f"C-1001,2026-09-28,Lakeside Family Medicine (fictional),Office visit,180.00,{paid},{owe},{st},Example Health Plan (fictional)\n"   # noqa: E731
        later_files = {"portal-2026-11.json": bundle(pat, {**med, "status": "stopped"},
                                                     {**cond, "clinicalStatus": status("resolved"), "abatementDateTime": "2026-10-15"}),
                       "claims-2026-11.csv": head + claim("120.00", "45.00", "Processed")}
        db = dbm.connect()
        pid = prof.create_profile(db, "Updated in place (fictional)", owner_id=robin)
        db.close()

        def imported(name, body):   # uploaded, read and saved, as the import page does
            p = f"/api/profiles/{pid}"
            up = self.robin.post(p + "/uploads", headers=H).json()["id"]
            self.assertEqual(self.robin.put(f"{p}/uploads/{up}/files", params={"path": name}, content=body.encode(),
                                            headers={**H, "Content-Type": "application/octet-stream"}).status_code, 200)
            self.assertEqual(self.robin.post(f"{p}/uploads/{up}/finish", headers=H).status_code, 200)
            jid = self.robin.post(f"{p}/uploads/{up}/import", json={}, headers=H).json()["id"]

            def until(states):
                end = time.time() + 30
                while time.time() < end:
                    j = self.robin.get(f"{p}/jobs/{jid}").json()
                    if j["state"] in states:
                        return j
                    time.sleep(0.05)
                self.fail(f"job {jid} never reached {states}")
            self.assertEqual(until(("preview", "failed"))["state"], "preview")
            self.assertEqual(self.robin.post(f"{p}/jobs/{jid}/confirm", json={"keep": []}, headers=H).status_code, 202)
            j = until(("done", "failed", "discarded"))
            self.assertEqual(j["state"], "done", j)
            return j

        def said(d):
            return ([(m["name"], m["spans"][-1]["open"]) for m in d.get("medications") or []],
                    [(x.get("name"), x.get("status")) for x in d.get("problems") or []],
                    [(c["title"], c["paid"], c["owe"], c["status"]) for c in d.get("costs") or []])
        imported("portal-2026-03.json", bundle(pat, med, cond))
        imported("claims-2026-10.csv", head + claim("0.00", "0.00", "Pending"))
        path = backup.create(os.path.join(TMP, "backups-920"))
        later = [imported(name, body) for name, body in later_files.items()]
        self.assertEqual([j["counts"].get("changed") for j in later], [2, 1], later)
        sent = []   # each later file as its importer reads it
        for name, body in later_files.items():
            d = tempfile.mkdtemp(dir=TMP)
            with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                f.write(body)
            out = importers.run(importers.files_in(d))
            sent.append((out["importer"], out["data"]))
        db = dbm.connect()
        self.assertEqual(said(records.assemble(db, pid)), ([("Metoprolol succinate 25 mg tablet", False)], [("Atrial fibrillation", "Resolved")],
                                                           [("Office visit", 120.0, 45.0, "Processed")]))
        self.assertEqual([preview.changes(preview.find(db, pid, fmt, data)) for fmt, data in sent], [0, 0], "today: they say it already")
        db.close()

        folder = os.path.join(TMP, "restored-920")
        _, con = self.restore_into(folder, path, today=True)
        for j in later:
            self.assertIsNone(con.execute("SELECT 1 FROM imports WHERE id=?", (j["import_id"],)).fetchone(), "the later imports aren't the backup's")
            self.assertEqual(con.execute("SELECT COUNT(*) FROM sync_changes WHERE import_id=?", (j["import_id"],)).fetchone()[0], 0)
        self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
        with mock.patch.multiple(config, DB_PATH=os.path.join(folder, "sanguity.db"), DATA_DIR=folder):
            restored = dbm.connect()
            try:
                self.assertEqual(said(records.assemble(restored, pid)), ([("Metoprolol succinate 25 mg tablet", True)], [("Atrial fibrillation", "Active")],
                                                                         [("Office visit", 0.0, 0.0, "Pending")]),
                                 "the backup's records: the medication current, the problem active, the claim pending")
                self.assertEqual([preview.changes(preview.find(restored, pid, fmt, data)) for fmt, data in sent], [2, 1],
                                 "the next files update the three in place again, nothing added beside")
            finally:
                restored.close()
        db = dbm.connect()
        prof.delete_profile(db, pid)
        db.close()

    def test_17_a_restore_over_a_damaged_database(self):   # #958
        """Today's database damaged, the reason many restores are made: not a database at all, or its pages overwritten.
        It can't be read, so it's as if there were none: the restore goes ahead with the backup's access and says why,
        the damaged file kept with what it replaced. The phone data is staged only once the restore will go ahead: one
        that fails leaves no watch-restored folder, and nothing for the receiver."""
        path = backup.create(os.path.join(TMP, "backups-958"))

        def garbage(p):
            with open(p, "r+b") as f:
                f.write(os.urandom(4096))

        def pages(p):
            size = os.path.getsize(p)
            with open(p, "r+b") as f:
                f.seek(4096)
                f.write(os.urandom(min(size - 4096, 64 * 4096)))
        for label, damage in (("garbage", garbage), ("pages", pages)):
            folder = os.path.join(TMP, f"restored-958-{label}")
            said, con = self.restore_into(folder, path, today=True, damage=damage)
            self.assertIn("The database it replaced can't be read", said, label)
            self.assertIn("are the backup's, as they were on", said, label)
            self.assertTrue({"ada", "robin"} <= {r[0] for r in con.execute("SELECT username FROM users")}, label)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0, label)
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok", label)
            aside = [n for n in os.listdir(folder) if n.startswith("before-restore-")]
            self.assertEqual(len(aside), 1, label)
            self.assertTrue(os.path.isfile(os.path.join(folder, aside[0], "sanguity.db")), "the damaged file is kept aside")

        # a restore that fails before anything is replaced stages none of the phone data, either way
        store = os.path.join(TMP, "store-958")
        os.makedirs(store)
        w = sqlite3.connect(":memory:")
        w.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
        w.commit()
        with open(os.path.join(store, "watch.db"), "wb") as f:
            f.write(crypto.seal(w.serialize()))
        w.close()
        with mock.patch.object(config, "WATCH_STORE", store):
            wpath = backup.create(os.path.join(TMP, "backups-958w"))
        self.assertIn("watch/watch.db", backup.verify(wpath)["files"])
        folder, keys = os.path.join(TMP, "restored-958w"), os.path.join(TMP, "keys-958")
        os.makedirs(folder)
        os.makedirs(keys)
        shutil.copy(os.path.join(DATA, "sanguity.db"), folder)
        for where in (os.path.join(TMP, "no-keys-958", "receiver-secrets.json"), os.path.join(keys, "receiver-secrets.json")):
            with mock.patch.object(backup, "_app_is_running", return_value=False), \
                    mock.patch.multiple(config, DATA_DIR=folder, DB_PATH=os.path.join(folder, "sanguity.db"), WATCH_KEYS=where), \
                    mock.patch.object(backup, "_ready", side_effect=RuntimeError("a fictional failure")):
                with self.assertRaises(RuntimeError):
                    backup.restore(wpath)
            self.assertEqual(sorted(os.listdir(folder)), ["sanguity.db"], "nothing left in the data folder")
            self.assertEqual(os.listdir(keys), [], "nothing staged for the receiver")

    def test_18_a_backup_made_without_the_key(self):   # #959
        """server.manage backup with the encryption key missing (a cron line without the key file): the backup is written
        with its files as they are, its row counts left out, as they can't be sealed (#935), and it says so; Check a
        backup, with the key, then says the counts aren't there."""
        saved, dest, out = (config.KEY_FILE, crypto.keyring.path), os.path.join(TMP, "backups-959"), io.StringIO()
        config.KEY_FILE = crypto.keyring.path = os.path.join(TMP, "keys", "no-such-key")
        crypto.keyring.reset()
        try:
            with contextlib.redirect_stdout(out):
                manage.run(["backup", dest])
        finally:
            config.KEY_FILE, crypto.keyring.path = saved
            crypto.keyring.reset()
        [path] = backup.list_backups(dest)
        m = backup.verify(path)
        self.assertIn("counts", m)
        self.assertIsNone(m["counts"])
        self.assertTrue(out.getvalue().startswith("Backup written"), out.getvalue())
        self.assertIn("without its row counts", out.getvalue())
        got = backup.check(path)
        self.assertFalse(got["ok"], got)
        self.assertTrue(any("doesn't say how many rows its database had" in p for p in got["problems"]), got["problems"])
        self.assertTrue(backup.check(backup.create(os.path.join(TMP, "backups-959b")))["ok"], "with the key, as before")


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

    def test_19_a_restore_keeps_todays_cycle_tracking(self):   # #964
        """#917 kept today's access through a restore but brought back each profile's cycle tracking with the records:
        turned off after the backup, it was on again once restored, the backup's cycle records with it. As the owner's
        decision extends, each profile's tracking is today's, on or off (its setting, none where today's has none), and
        where today's doesn't keep the cycle records the backup holds (off now, its deletion done or not, or everything
        deleted since) they aren't brought back: an uploaded export's own and those in an Apple Health export uploaded,
        as turning it off deletes them (the receiver deletes its copy, a slot with tracking off, when it starts). One on
        in both keeps them; one turned on since is on. The restore says so, naming no profile. With no database to replace,
        the backup's, said so too."""
        from server import cycle_tracking as ct, records
        with open(os.path.join(ROOT, "tests", "fixtures", "apple_health", "apple_health_export", "export.xml"), "rb") as f:
            src = f.read()
        flow = b"hkcategorytypeidentifiermenstrualflow"
        self.assertIn(flow, src.lower())
        robin = self.uids["robin"]
        visit = {"events": [{"id": 1, "t": "2026-05-01T09:00:00", "k": "visit", "title": "Visit (fictional)"}]}
        db = dbm.connect()
        try:
            pids = {n: prof.create_profile(db, f"Cycle {n} (fictional)", owner_id=robin) for n in ("off", "done", "deleted", "on", "kept")}
            iids = {}
            for n in ("off", "done", "deleted", "kept"):   # tracking on at the backup, an export's records kept, one uploaded
                with dbm.transaction(db):
                    ct.turn(db, pids[n], True, robin)
                    iids[n] = records.store(db, pids[n], visit, "upload")
                    self.assertEqual(ct.save_imported(db, pids[n], iids[n], [{"kind": "flow", "day": "2026-05-01", "value": "medium", "app": "Apple Health"}]), 1)
                up = uploads.create(db, pids[n], robin)
                uploads.add_file(db, pids[n], up, "apple_health_export/export.xml", io.BytesIO(src))
                uploads.finish(db, pids[n], up)
        finally:
            db.close()
        path = backup.create(os.path.join(TMP, "backups-964"))
        db = dbm.connect()   # after it: turned off, twice (one's deletion done since, its row gone: #935), everything deleted, on
        try:
            with dbm.transaction(db):
                for n in ("off", "done"):
                    ct.turn(db, pids[n], False, robin)
                ct.delete_all(db, pids["deleted"], robin)
                ct.turn(db, pids["on"], True, robin)
            for n in ("off", "done", "deleted"):
                ct.drop_imported(db, pids[n])
            today = {n: ct.setting(db, p) for n, p in pids.items()}
        finally:
            db.close()
        self.assertEqual({n: v.get("on") for n, v in today.items()}, {"off": False, "done": False, "deleted": True, "on": True, "kept": True})

        def held(folder, con):   # each profile's tracking as restored, and whether its import's and its upload's cycle records are there
            rows = {p: json.loads(crypto.dec(s)) for p, s in con.execute("SELECT profile_id, sealed FROM settings WHERE key='_cycle' AND user_id IS NULL")}
            out = {}
            for n, p in pids.items():
                ups = os.path.join(folder, "profiles", str(p), "uploads")
                files = [os.path.join(d, f) for d, _, fs in os.walk(ups) for f in fs]
                out[n] = (rows.get(p, {}).get("on"), n in iids and os.path.isfile(os.path.join(folder, "profiles", str(p), "watch", "cycle", f"{iids[n]}.json")),
                          any(flow in crypto.read_file(f).lower() for f in files))
            return rows, out
        folder = os.path.join(TMP, "restored-964")
        said, con = self.restore_into(folder, path, today=True,   # its deletion done since: its row gone, as forget_done does
                                      change=lambda c: c.execute("DELETE FROM settings WHERE key='_cycle' AND profile_id=?", (pids["done"],)))
        rows, got = held(folder, con)
        self.assertEqual(got, {"off": (False, False, False), "done": (None, False, False), "deleted": (True, False, False), "on": (True, False, False),
                               "kept": (True, True, True)}, "today's tracking, and the backup's cycle records only where it keeps them")
        self.assertEqual({n: rows.get(p) for n, p in pids.items() if n != "done"}, {n: v for n, v in today.items() if n != "done"}, "today's settings, as they were")
        self.assertIsNone(con.execute("SELECT v FROM meta WHERE k=?", (ct.RESTORED,)).fetchone(), "nothing left to do, nor saying which profiles")
        self.assertIn(manage.CYCLE_KEPT, said)
        self.assertFalse([p for p in pids.values() if f"Profile {p} " in said], "no profile named")

        said, con = self.restore_into(os.path.join(TMP, "restored-964-none"), path)   # disaster recovery: the backup's
        self.assertEqual(held(os.path.join(TMP, "restored-964-none"), con)[1],
                         {"off": (True, True, True), "done": (True, True, True), "deleted": (True, True, True), "on": (None, False, False),
                          "kept": (True, True, True)})
        self.assertIn(manage.CYCLE_THEN, said)
        db = dbm.connect()
        try:
            for p in pids.values():
                prof.delete_profile(db, p)
        finally:
            db.close()

    def test_20_a_restore_keeps_todays_install_settings(self):   # #1005
        """#917 kept today's two-factor rule through a restore, but the install's other settings came back as the backup
        had them: the backup schedule and how many it keeps, the sync defaults, General's. As #917 keeps today's access,
        they're today's, every install setting the registry has (whatever is added to it later: each is changed after
        the backup here, one taken away since, one saved since) and an install-wide value of a profile's setting; the
        restore says so. With no database to replace (disaster recovery) they're the backup's, and the restore names
        them."""
        from server import settings
        keys = [k for k, s in settings.SETTINGS.items() if s.scope == "install"]
        self.assertIn("activity.days", keys)
        self.assertTrue({"sync.staleDays", "sync.attachAuto"} <= set(keys))
        rows = lambda con: sorted(map(tuple, con.execute(f"SELECT key, value FROM settings WHERE {settings.INSTALL_ROWS}")))   # noqa: E731
        meta = lambda con: dict(map(tuple, con.execute("SELECT k, v FROM meta WHERE k IN ('backup.schedule', 'backup.keep')")))   # noqa: E731
        then = keys[:-1] + ["overview.keyLabs"]   # (the last one saved only after the backup; a profile's setting install-wide)
        db = dbm.connect()
        try:
            for k in then:
                db.execute("INSERT INTO settings(key, value, changed) VALUES(?, ?, 0)", (k, json.dumps(f"backup's {k}")))
            backup_jobs.set_schedule(db, "daily", 9)
            backup_ = rows(db)
        finally:
            db.close()
        path = backup.create(os.path.join(TMP, "backups-1005"))

        def now(con):   # each changed since by an admin, one taken away, one saved; the schedule changed
            con.execute(f"UPDATE settings SET value=json_quote('today''s ' || key) WHERE {settings.INSTALL_ROWS}")
            con.execute(f"DELETE FROM settings WHERE key=? AND {settings.INSTALL_ROWS}", (then[0],))
            con.execute("INSERT INTO settings(key, value, changed) VALUES(?, ?, 0)", (keys[-1], json.dumps(f"today's {keys[-1]}")))
            con.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('backup.schedule', 'weekly')")
            con.execute("DELETE FROM meta WHERE k='backup.keep'")
        folder = os.path.join(TMP, "restored-1005")
        said, con = self.restore_into(folder, path, today=True, change=now)
        today = sorted([(k, json.dumps(f"today's {k}")) for k in then[1:] + [keys[-1]]])
        self.assertEqual(rows(con), today)
        self.assertEqual(meta(con), {"backup.schedule": "weekly"})
        self.assertIn(manage.SETTINGS_KEPT, said)
        aside = [n for n in os.listdir(folder) if n.startswith("before-restore-")]
        self.assertEqual(len(aside), 1)

        said, con = self.restore_into(os.path.join(TMP, "restored-1005-none"), path)   # disaster recovery: the backup's
        self.assertEqual((rows(con), meta(con)), (backup_, {"backup.schedule": "daily", "backup.keep": "9"}))
        self.assertIn("The server's own settings are the backup's too, as they were then: ", said)
        for k in then:
            self.assertIn(settings.SETTINGS[k].label, said)
        self.assertIn("the backup schedule (daily, keeping 9)", said)
        self.assertNotIn(manage.SETTINGS_KEPT, said)
        db = dbm.connect()
        try:
            db.execute(f"DELETE FROM settings WHERE {settings.INSTALL_ROWS}")
            backup_jobs.set_schedule(db, "off", backup_jobs.KEEP_DEFAULT)
        finally:
            db.close()

    def test_21b_a_move_aside_cut_short_keeps_both_halves(self):   # #1055, its closing recheck
        """A move of profiles/ to another disk copies, then deletes: cut short between the two, part of today's profiles/
        is in each place. Nothing of it is removed, and the restore says where each is rather than \"Nothing was
        changed\"."""
        path = backup.create(os.path.join(TMP, "backups-1055b"))
        real_move = shutil.move
        folder = os.path.join(TMP, "restored-1055-split")
        shutil.copytree(DATA, folder, ignore=shutil.ignore_patterns("sanguity.db*", "app.lock", "before-restore-*"))
        src, dst = sqlite3.connect(os.path.join(DATA, "sanguity.db")), sqlite3.connect(os.path.join(folder, "sanguity.db"))
        src.backup(dst)
        src.close()
        dst.close()

        def split(a, b):   # profiles/ half copied to the folder, then the disk fills; today's still in place
            if "before-restore-" in b and os.path.basename(a) == "profiles":
                shutil.copytree(a, b, ignore=lambda d, names: names[len(names) // 2:])
                raise OSError(28, "No space left on device")
            return real_move(a, b)
        with mock.patch.multiple(config, DATA_DIR=folder, DB_PATH=os.path.join(folder, "sanguity.db")):
            before = live_state()
            with mock.patch.object(backup, "_app_is_running", return_value=False), contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(backup.shutil, "move", side_effect=split), self.assertRaises(SystemExit) as cm:
                manage.run(["restore", path, "--yes"])
            said = str(cm.exception.code)
            self.assertIn("except profiles, which was being moved aside", said)
            self.assertNotIn("Nothing was changed", said)
            now = live_state()   # (the folder holding the half copy, kept and named, aside)
            now = ({k: v for k, v in now[0].items() if not k.startswith("before-restore-")}, *now[1:])
            self.assertEqual(now, before, "today's data as it was: profiles/ kept where it is, the database back")
            asides = [n for n in os.listdir(folder) if n.startswith("before-restore-")]
            self.assertEqual(len(asides), 1, "the folder with the half copy is kept, named")
            self.assertIn(asides[0], said)

    def test_21c_put_back_failing_after_a_cut_short_move(self):   # #1055, the final recheck
        """A move of profiles/ cut short half copied, then putting today's database back failing too: the message names
        only what is in the folder aside to move back, and says today's profiles/ is still in the data folder and not to
        be replaced by the half copy (it said to move the profiles folder back, replacing any there)."""
        path = backup.create(os.path.join(TMP, "backups-1055c"))
        real_move = shutil.move
        folder = os.path.join(TMP, "restored-1055-split2")
        shutil.copytree(DATA, folder, ignore=shutil.ignore_patterns("sanguity.db*", "app.lock", "before-restore-*"))
        src, dst = sqlite3.connect(os.path.join(DATA, "sanguity.db")), sqlite3.connect(os.path.join(folder, "sanguity.db"))
        src.backup(dst)
        src.close()
        dst.close()

        def split_then_stuck(a, b):
            if "before-restore-" in b and os.path.basename(a) == "profiles":
                shutil.copytree(a, b, ignore=lambda d, names: names[len(names) // 2:])
                raise OSError(28, "No space left on device")
            if "before-restore-" in a:   # moving today's back out of the folder aside
                raise OSError(30, "Read-only file system")
            return real_move(a, b)
        with mock.patch.multiple(config, DATA_DIR=folder, DB_PATH=os.path.join(folder, "sanguity.db")):
            with mock.patch.object(backup, "_app_is_running", return_value=False), contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(backup.shutil, "move", side_effect=split_then_stuck), self.assertRaises(SystemExit) as cm:
                manage.run(["restore", path, "--yes"])
            said = str(cm.exception.code)
            self.assertIn("couldn't all be put back", said)
            self.assertIn("move sanguity.db from there", said)
            self.assertNotIn("the profiles and build folders", said)
            self.assertIn("Today's profiles is still in the data folder", said)
            self.assertIn("don't replace it", said)
            self.assertTrue(os.path.isdir(os.path.join(folder, "profiles")), "today's profiles/ left where it is")

    def test_21_a_restore_that_fails_part_way(self):   # #1055
        """A restore failing between moving today's files aside and putting the backup's database in place (moving them,
        writing the backup's profiles/ as the disk fills, the replace) left only before-restore-<time>/ and no database:
        the app then started empty. Now today's data is put back as it was, the app starts with its accounts, and the
        restore says what happened and names the folder."""
        path = backup.create(os.path.join(TMP, "backups-1055"))
        self.assertTrue(any(n.startswith("profiles/") for n in backup.verify(path)["files"]), "a backup with profiles' files")
        real_move, real_extract, real_replace = shutil.move, backup._extract, os.replace

        def moving(src, dst):   # profiles/ can't be moved aside (the database already was)
            if "before-restore-" in dst and os.path.basename(src) in backup.CONTENTS:
                raise OSError(28, "No space left on device")
            return real_move(src, dst)

        def extracting(tar, m, target):   # the disk fills while profiles/ is written
            real_extract(tar, m, target)
            if m.name.startswith("profiles/"):
                raise OSError(28, "No space left on device")

        def replacing(src, dst):   # the backup's database can't be put in place
            if src.endswith(".restoring.db"):
                raise OSError(5, "Input/output error")
            return real_replace(src, dst)
        for label, target, fake, step in (("moving", backup.shutil, ("move", moving), "moving today's data aside"),
                                          ("extracting", backup, ("_extract", extracting), "writing the backup's profiles/"),
                                          ("replace", backup.os, ("replace", replacing), "putting the backup's database in place")):
            folder = os.path.join(TMP, f"restored-1055-{label}")
            shutil.copytree(DATA, folder, ignore=shutil.ignore_patterns("sanguity.db*", "app.lock", "before-restore-*"))
            src, dst = sqlite3.connect(os.path.join(DATA, "sanguity.db")), sqlite3.connect(os.path.join(folder, "sanguity.db"))
            src.backup(dst)
            src.close()
            dst.close()
            with mock.patch.multiple(config, DATA_DIR=folder, DB_PATH=os.path.join(folder, "sanguity.db")):
                before = live_state()
                out = io.StringIO()
                with mock.patch.object(backup, "_app_is_running", return_value=False), contextlib.redirect_stdout(out), \
                        mock.patch.object(target, fake[0], side_effect=fake[1]), self.assertRaises(SystemExit) as cm:
                    manage.run(["restore", path, "--yes"])
                said = str(cm.exception.code)
                self.assertIn(f"The restore failed while {step}", said, label)
                self.assertIn("Nothing was changed: today's data, moved aside to " + os.path.join(folder, "before-restore-"), said, label)
                self.assertNotIn("Restored.", out.getvalue(), label)
                self.assertEqual(live_state(), before, f"{label}: today's data as it was")
                self.assertEqual([n for n in os.listdir(folder) if n.startswith("before-restore-")], [], label)
                dbm.init()   # the app starts with today's accounts, not first-run setup
                con = dbm.connect()
                try:
                    self.assertEqual({r[0] for r in con.execute("SELECT username FROM users")} & {"ada", "robin"}, {"ada", "robin"}, label)
                finally:
                    con.close()

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

    def test_9c_the_phone_data_a_restore_left_to_move_in(self):
        """The owner's decision (#603): the phone and watch data a restore leaves in DATA_DIR/watch-restored-<time>/,
        where the receiver's keys folder can't be written, is listed on Admin → Backups beside what restores replaced,
        deleted RESTORE_KEEP after the restore at start and hourly, logged, or by an admin's Delete now. Never in a backup."""
        keep = backup.RESTORE_KEEP / 86400
        stamp = lambda d: time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time() - d * 86400))
        old, recent = (self.aside(d, "watch-restored-" + stamp(d)) for d in (keep + 1, 2))
        halfway = self.aside(0, backup.DELETING + "watch-restored-" + "20200101-000000")
        others = self.aside(keep * 3, "watch-restored-notes")   # not a name restore() gives: left alone
        st = self.status()
        rows = [(r["name"], r["kind"]) for r in st["replaced"]]
        self.assertEqual(rows, [(os.path.basename(old), "watch"), (os.path.basename(recent), "watch")], st["replaced"])
        row = st["replaced"][1]
        self.assertAlmostEqual(row["deletes"] - row["made"], backup.RESTORE_KEEP, delta=1)
        name = self.backup_now()["file"]
        self.assertFalse([n for n in backup.verify(os.path.join(FOLDER, name))["files"] if "watch-restored" in n])
        # the sweep: the old one goes, logged; the recent one and what isn't restore()'s stay
        backup_jobs.stop()
        try:
            db = dbm.connect()
            try:
                self.assertEqual(backup_jobs.sweep_replaced(db, force=True), [os.path.basename(old)])
            finally:
                db.close()
        finally:
            backup_jobs.start()
        self.assertFalse(os.path.exists(old) or os.path.exists(halfway))
        self.assertTrue(os.path.isdir(recent) and os.path.isdir(others))
        logged = [e for e in self.actions("backup.replaced") if e["detail"]["name"] == os.path.basename(old)]
        self.assertEqual([(e["actor"], e["detail"]["days"]) for e in logged], [("time limit", 30)])
        # Delete now, as for what a restore replaced
        path = f"/api/backups/replaced/{os.path.basename(recent)}"
        self.assertEqual(self.robin.delete(path, headers=H).status_code, 403)
        r = self.ada.delete(path, headers=H)
        if r.status_code == 403 and r.json().get("detail") == "reauth_required":
            self.assertEqual(self.ada.post("/api/me/reauth", json={"secret": PW["ada"]}, headers=H).status_code, 200)
            r = self.ada.delete(path, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["replaced"], [])
        self.assertFalse(os.path.exists(recent))
        self.assertEqual(self.ada.delete(f"/api/backups/replaced/{os.path.basename(others)}", headers=H).status_code, 404)
        self.assertTrue(os.path.isdir(others))
        shutil.rmtree(others)
        os.remove(os.path.join(FOLDER, name))


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
