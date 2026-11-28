#!/usr/bin/env python3
"""The settings that need a restart (#1121, server/server_settings.py): changed on General → Needs a restart, kept in
data/server-settings.json (0600), and read as the server starts, a restart simulated by calling the start-up path
(server_settings.start) in-process. Each applies only after a restart, the environment's wins, a value past its
bounds is refused, a pending value that doesn't come up healthy falls back to the last good one and says so,
Restart now asks for the fresh check, is logged and copies the receiver's for it, and server.manage config reset puts
the defaults back. Fictional data only.

  python3 tests/test_server_settings.py
"""
import contextlib, importlib.util, io, json, os, shutil, stat, sys, tempfile, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-server-settings-test-")
RESTARTS = ("UPLOAD_MAX_FILE_MB", "UPLOAD_MAX_TOTAL_MB", "UPLOAD_MAX_FILES", "JOURNAL_PHOTO_MAX_MB", "SYNC_HOURS",
            "SYNC_MAX_RESOURCES", "SYNC_MAX_ATTACHMENT_MB", "CONNECT_TIMEOUT", "SHL_MAX_MB", "SHL_MAX_FILES", "SHL_TIMEOUT",
            "HASH_PARALLEL", "FORWARDED_ALLOW_IPS", "TZ", "SLOT_GRACE_DAYS", "RESTORE_KEEP_DAYS")
for name in RESTARTS + ("SANGUITY_SAVED_SETTINGS", "TZ_DEFAULT", "PUBLIC_ORIGINS"):
    os.environ.pop(name, None)
os.environ["CONNECTED_SOURCES"] = "1"
KEYS = os.path.join(TMP, "watch-rx", "keys")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch-rx", "public", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  SANGUITY_WATCH_KEYS=os.path.join(KEYS, "receiver-secrets.json"), SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"))
for d in ("records", "keys", "build", KEYS, os.path.join("watch-rx", "public")):
    os.makedirs(os.path.join(TMP, d), exist_ok=True)
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, config, db as dbm, main, manage, server_settings as ss   # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = "an admin of the fictional install"
QUIET = lambda *a, **k: None   # noqa: E731
# a value for each that isn't its default, and one past its bounds (or not one it takes)
GOOD = {"UPLOAD_MAX_FILE_MB": 2048, "UPLOAD_MAX_TOTAL_MB": 8192, "UPLOAD_MAX_FILES": 5000, "JOURNAL_PHOTO_MAX_MB": 10,
        "SYNC_HOURS": 6, "SYNC_MAX_RESOURCES": 5000, "SYNC_MAX_ATTACHMENT_MB": 100, "CONNECT_TIMEOUT": 60, "SHL_MAX_MB": 32,
        "SHL_MAX_FILES": 20, "SHL_TIMEOUT": 40, "HASH_PARALLEL": 4, "FORWARDED_ALLOW_IPS": "172.16.0.0/12, 10.0.0.5",
        "TZ": "America/Chicago", "SLOT_GRACE_DAYS": 14, "RESTORE_KEEP_DAYS": 60}
BAD = {"UPLOAD_MAX_FILE_MB": 5000, "UPLOAD_MAX_TOTAL_MB": 0, "UPLOAD_MAX_FILES": 10**6, "JOURNAL_PHOTO_MAX_MB": 26,
       "SYNC_HOURS": 0, "SYNC_MAX_RESOURCES": 10, "SYNC_MAX_ATTACHMENT_MB": 2048, "CONNECT_TIMEOUT": 1, "SHL_MAX_MB": 1000,
       "SHL_MAX_FILES": 0, "SHL_TIMEOUT": 600, "HASH_PARALLEL": 64, "FORWARDED_ALLOW_IPS": "proxy.example.test",
       "TZ": "Mars/Olympus_Mons", "SLOT_GRACE_DAYS": 0, "RESTORE_KEEP_DAYS": 1.5}


def restart(**kw):
    """The start-up path, as python -m server runs it: what this process uses from the file."""
    return ss.start(say=QUIET, **kw)


class RestartSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with contextlib.redirect_stdout(io.StringIO()):
            cls.ctx = TestClient(main.app).__enter__()
        db = dbm.connect()
        accounts.create(db, "ada", password=PW, is_admin=True)
        db.close()
        cls.c = TestClient(main.app)
        r = cls.c.post("/api/login", json={"username": "ada", "password": PW}, headers=H)
        assert r.status_code == 200, r.text

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        shutil.rmtree(TMP, True)

    def setUp(self):
        for p in (ss.path(), ss.receiver_path()):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(p)
        ss.forget()
        restart()

    def put(self, values):
        return self.c.put("/api/settings/install/restart/values", json={"values": values}, headers=H)

    def state(self):
        return self.c.get("/api/settings/install/restart").json()

    def row(self, name):
        return next(r for r in self.state()["settings"] if r["name"] == name)

    def test_a_the_registry(self):
        """Every variable of config.RESTART is defined here, with its config.VARS default; none is one an install
        setting saved in the database stands for (that one applies at once instead)."""
        self.assertEqual(set(config.RESTART), set(ss.DEFS) & set(config.VARS))
        self.assertEqual(set(ss.DEFS), set(RESTARTS))
        from server import settings
        for name in config.RESTART:
            self.assertIsNone(config.VARS[name].setting, name)
            self.assertEqual(ss.DEFS[name].default, config.VARS[name].default, name)
        self.assertEqual(set(GOOD), set(ss.DEFS))
        self.assertTrue(settings.SETTINGS)

    def test_b_each_applies_after_a_restart(self):
        """Saved, each waits for a restart (Restart needed, its next value shown) and the running server keeps the one
        it started with; after the start-up path, each is the one in force where it's used."""
        before = {n: ss.lookup(n)[1] for n in ss.DEFS}
        r = self.put(GOOD)
        self.assertEqual(r.status_code, 200, r.text)
        st = r.json()
        self.assertTrue(st["restart_needed"])
        self.assertEqual({x["name"]: x["next"] for x in st["settings"]}, GOOD)
        self.assertEqual({n: ss.lookup(n)[1] for n in ss.DEFS}, before, "nothing changes until a restart")
        restart()
        for name, v in GOOD.items():
            self.assertEqual(ss.lookup(name), ("file", v), name)
        for name in config.RESTART:   # where the server reads them: config.setting
            self.assertEqual(config.setting(name), GOOD[name], name)
        self.assertEqual(ss.uvicorn_options()["forwarded_allow_ips"], "172.16.0.0/12, 10.0.0.5")   # uvicorn's, at start
        phone = self.c.get("/api/settings/phone").json()
        self.assertEqual((phone["tz"], phone["slot_grace_days"]), ("America/Chicago", 14))
        rows = {x["name"]: x for x in self.c.get("/api/settings/install/restart").json()["settings"]}   # (#1119: no longer on Host)
        self.assertEqual((rows["FORWARDED_ALLOW_IPS"]["value"], rows["FORWARDED_ALLOW_IPS"]["from"]), ("172.16.0.0/12, 10.0.0.5", "file"))
        about = {l["name"]: l for g in self.c.get("/api/settings/install/about").json()["limits"] for l in g["limits"]}
        self.assertEqual((about["UPLOAD_MAX_FILE_MB"]["value"], about["UPLOAD_MAX_FILE_MB"]["from"]), (2048, "file"))
        st = self.state()
        self.assertFalse(st["restart_needed"])
        self.assertTrue(st["trying"], "being tried until it has run healthy")
        self.assertTrue(ss.promote(say=QUIET))
        self.assertFalse(self.state()["trying"])
        restart()   # and kept at the next start
        self.assertEqual({n: ss.lookup(n)[1] for n in ss.DEFS}, GOOD)
        self.assertIsNone(self.state()["fell_back"])
        r = self.put({n: None for n in GOOD})   # back to the defaults, at the next start
        self.assertTrue(r.json()["restart_needed"])
        restart()
        ss.promote(say=QUIET)
        self.assertEqual({n: ss.lookup(n) for n in ss.DEFS}, {n: ("default", d.default) for n, d in ss.DEFS.items()})

    def test_c_the_environment_wins(self):
        """A variable set in the environment is the one in force whatever the file holds, and isn't changed here (409)."""
        self.put({"UPLOAD_MAX_FILE_MB": 2048, "FORWARDED_ALLOW_IPS": "10.0.0.5", "TZ": "Europe/Paris"})
        restart()
        env = {"UPLOAD_MAX_FILE_MB": "1024", "FORWARDED_ALLOW_IPS": "*", "TZ": "Asia/Tokyo"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(ss.lookup("UPLOAD_MAX_FILE_MB"), ("environment", 1024))
            self.assertEqual(config.setting("UPLOAD_MAX_FILE_MB"), 1024)
            self.assertEqual(ss.uvicorn_options()["forwarded_allow_ips"], "*")
            self.assertEqual(self.c.get("/api/settings/phone").json()["tz"], "Asia/Tokyo")
            row = self.row("UPLOAD_MAX_FILE_MB")
            self.assertEqual((row["value"], row["from"], row["can_change"]), (1024, "environment", False))
            for name in env:
                r = self.put({name: GOOD[name]})
                self.assertEqual(r.status_code, 409, name)
                self.assertIn(name, r.json()["detail"])
        with mock.patch.dict(os.environ, {"UPLOAD_MAX_FILE_MB": ""}):   # compose's ${NAME:-}, unset in .env
            self.assertEqual(ss.lookup("UPLOAD_MAX_FILE_MB"), ("file", 2048))

    def test_d_bounds_refused(self):
        """A value past its bounds (or not one it takes) is refused, 400, and nothing is saved."""
        for name, v in BAD.items():
            r = self.put({name: v})
            self.assertEqual(r.status_code, 400, (name, v, r.text))
        for v in ("", "*, 10.0.0.1", "::/0, 10.0.0.1", "10.0.0.1/40", ["10.0.0.1"] * 21):
            self.assertEqual(self.put({"FORWARDED_ALLOW_IPS": v}).status_code, 400 if v else 200, v)
        self.assertEqual(self.put({"NOT_A_SETTING": 1}).status_code, 404)
        self.assertEqual(self.put({"SESSION_DAYS": 3}).status_code, 404, "one that applies at once isn't changed here")
        self.assertEqual(ss.next_values(), {})
        self.assertEqual(ss.clean({"HASH_PARALLEL": 64, "TZ": "Mars/Olympus_Mons", "SHL_TIMEOUT": 30}), {"SHL_TIMEOUT": 30},
                         "a value no longer taken, read from the file, is left at its default")

    def test_e_a_pending_value_that_fails_falls_back(self):
        """Tried at start, a value that never runs healthy (the server stopped before PROMOTE_SECONDS, a crash) is
        dropped at the next start, the good ones used, and why said on General and in the install log."""
        self.put({"UPLOAD_MAX_FILE_MB": 2048})
        restart()
        ss.promote(say=QUIET)   # 2048 is good now
        self.put({"HASH_PARALLEL": 8, "FORWARDED_ALLOW_IPS": "10.0.0.5"})
        self.assertEqual(restart(), {"UPLOAD_MAX_FILE_MB": 2048, "HASH_PARALLEL": 8, "FORWARDED_ALLOW_IPS": "10.0.0.5"})
        # ...and it stops before it has answered its health check for long enough: started again
        self.assertEqual(restart(), {"UPLOAD_MAX_FILE_MB": 2048})
        self.assertEqual(config.setting("HASH_PARALLEL"), 2)
        self.assertEqual(ss.uvicorn_options()["forwarded_allow_ips"], "127.0.0.1")
        st = self.state()
        self.assertFalse(st["restart_needed"], "the values that failed aren't tried again")
        self.assertIn("stopped before it had run healthy", st["fell_back"]["why"])
        self.assertEqual(st["fell_back"]["values"], {"UPLOAD_MAX_FILE_MB": 2048, "HASH_PARALLEL": 8, "FORWARDED_ALLOW_IPS": "10.0.0.5"})
        db = dbm.connect()
        try:
            ss.log_start(db)
            row = db.execute("SELECT actor, detail FROM activity WHERE action='server.restart' ORDER BY id DESC").fetchone()
        finally:
            db.close()
        from server import crypto
        detail = json.loads(crypto.dec(row["detail"]))
        self.assertEqual(row["actor"], "start-up")
        self.assertEqual({c["setting"]: (c["from"], c["to"]) for c in detail["changes"]},
                         {"HASH_PARALLEL": (8, 2), "FORWARDED_ALLOW_IPS": ("10.0.0.5", "127.0.0.1")})
        self.put({"SHL_TIMEOUT": 30})   # a change made after says no more of it
        self.assertIsNone(self.state()["fell_back"])

    def test_f_watch_promotes_or_gives_up(self):
        """python -m server's watch: healthy for PROMOTE_SECONDS, the values tried are kept; not healthy within
        GIVE_UP_SECONDS, the server is stopped and the next start falls back, saying why."""
        with mock.patch.multiple(ss, POLL=0.01, PROMOTE_SECONDS=0, GIVE_UP_SECONDS=5):
            self.put({"SHL_TIMEOUT": 30})
            restart()
            ss.watch(lambda: True).join(5)
        self.assertFalse(self.state()["trying"])
        restart()
        self.assertEqual(ss.lookup("SHL_TIMEOUT"), ("file", 30))
        stopped = []
        with mock.patch.multiple(ss, POLL=0.01, GIVE_UP_SECONDS=0):
            self.put({"SHL_TIMEOUT": 50})
            restart()
            with contextlib.redirect_stdout(io.StringIO()):
                ss.watch(lambda: False, stop=lambda: stopped.append(1)).join(5)
        self.assertEqual(stopped, [1])
        restart()
        self.assertEqual(ss.lookup("SHL_TIMEOUT"), ("file", 30))
        self.assertIn("wasn't up and serving", self.state()["fell_back"]["why"])
        self.assertIsNone(ss.watch(lambda: True), "nothing to watch while nothing is tried")

    def test_g_the_file(self):
        """Mode 0600, written whole; one that can't be read leaves the defaults in force, and says so, and a start
        that can't write its marker doesn't try pending values (it could never fall back from them)."""
        self.put({"SHL_MAX_FILES": 20})
        self.assertEqual(stat.S_IMODE(os.stat(ss.path()).st_mode), 0o600)
        self.assertEqual([n for n in os.listdir(TMP) if n.startswith(".server-settings")], [], "no temporary file left")
        restart()
        ss.promote(say=QUIET)
        with open(ss.path(), "w") as f:
            f.write("{not json")
        restart()
        self.assertEqual(ss.lookup("SHL_MAX_FILES"), ("default", 50))
        self.assertIn("couldn't be read", self.state()["unreadable"])
        os.unlink(ss.path())
        self.put({"SHL_MAX_FILES": 30})
        with mock.patch.object(ss, "write_doc", side_effect=PermissionError("read-only")):
            self.assertEqual(restart(), {})
        self.assertFalse(ss.read_doc()[0].get("trying"))

    def test_h_restart_now(self):
        """Restart now: the fresh check (403 reauth_required), logged with what the next start tries, the receiver's
        copied for it, then the app stops (after answering)."""
        self.put({"SHL_MAX_MB": 32})
        restart()   # still being checked: a stop now would drop them, so it's refused until they're kept
        with mock.patch.object(ss, "stop_soon") as stop:
            r = self.c.post("/api/settings/install/restart", headers=H)
            self.assertEqual(r.status_code, 409, r.text)
            self.assertFalse(stop.called)
        ss.promote(say=QUIET)
        self.put({"TZ": "Europe/Paris", "SLOT_GRACE_DAYS": 10, "UPLOAD_MAX_FILES": 900})
        db = dbm.connect()
        db.execute("UPDATE sessions SET verified_at=0")
        db.close()
        with mock.patch.object(ss, "stop_soon") as stop:
            self.assertEqual(self.c.post("/api/settings/install/restart", headers=H).json()["detail"], "reauth_required")
            self.assertEqual(self.put({"SHL_TIMEOUT": 30}).json()["detail"], "reauth_required")
            self.assertFalse(stop.called)
            self.assertEqual(self.c.post("/api/me/reauth", json={"secret": PW}, headers=H).status_code, 200)
            r = self.c.post("/api/settings/install/restart", headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(stop.called)
        self.assertEqual({c["setting"]: c["to"] for c in r.json()["changes"]},
                         {"TZ": "Europe/Paris", "SLOT_GRACE_DAYS": 10, "UPLOAD_MAX_FILES": 900})
        with open(ss.receiver_path()) as f:
            self.assertEqual(json.load(f), {"TZ": "Europe/Paris", "SLOT_GRACE_DAYS": 10})
        self.assertEqual(stat.S_IMODE(os.stat(ss.receiver_path()).st_mode), 0o600)
        db = dbm.connect()
        try:
            rows = [r["action"] for r in db.execute("SELECT action FROM activity WHERE action IN ('server.restart', 'settings.install') ORDER BY id")]
        finally:
            db.close()
        self.assertEqual(rows[-4:], ["settings.install"] * 3 + ["server.restart"])

    def test_i_the_receiver_reads_its_copy(self):
        """The receiver takes the app's copy as it starts, never its own environment's (#1158), a zone it doesn't know
        left at UTC; and it sees the copy change (then restarts)."""
        ss.write_receiver({"TZ": "Europe/Paris", "SLOT_GRACE_DAYS": 10, "RESTORE_KEEP_DAYS": 60}, say=QUIET)

        def receiver(**env):
            with mock.patch.dict(os.environ, {"KEYS_FILE": os.path.join(KEYS, "receiver-secrets.json"), "DATA": TMP, **env}):
                spec = importlib.util.spec_from_file_location("rx_settings", os.path.join(ROOT, "watch", "receiver.py"))
                rx = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(rx)
                return rx
        rx = receiver()
        self.assertEqual((rx.DEFAULT_TZ, rx.SLOT_GRACE, rx.RESTORE_KEEP), ("Europe/Paris", 10 * 86400, 60 * 86400))
        self.assertFalse(rx.settings_changed())
        rx2 = receiver(TZ_DEFAULT="Asia/Tokyo", TZ="Asia/Tokyo", SLOT_GRACE_DAYS="3", RESTORE_KEEP_DAYS="5")
        self.assertEqual((rx2.DEFAULT_TZ, rx2.SLOT_GRACE, rx2.RESTORE_KEEP), ("Europe/Paris", 10 * 86400, 60 * 86400))
        ss.write_receiver({"SLOT_GRACE_DAYS": 11, "HASH_PARALLEL": 4}, say=QUIET)
        self.assertTrue(rx.settings_changed())
        with open(ss.receiver_path()) as f:
            self.assertEqual(json.load(f), {"SLOT_GRACE_DAYS": 11}, "only the receiver's")
        with open(ss.receiver_path(), "w") as f:
            json.dump({"TZ": "Mars/Olympus_Mons"}, f)
        self.assertEqual(receiver().DEFAULT_TZ, "UTC")

    def test_j_manage_config_reset(self):
        """server.manage config show lists them (and the value after a restart); config reset NAME, or --all, puts
        the defaults back for the next start, the pending ones included, logged by the command line."""
        self.put({"UPLOAD_MAX_FILE_MB": 2048, "TZ": "Europe/Paris"})
        restart()
        ss.promote(say=QUIET)
        self.put({"UPLOAD_MAX_FILE_MB": 1024, "SHL_TIMEOUT": 30})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            manage.run(["config", "show"])
        line = next(x for x in out.getvalue().splitlines() if x.startswith("UPLOAD_MAX_FILE_MB"))
        self.assertIn("2048", line)
        self.assertIn("after a restart: 1024", line)
        self.assertTrue(any(x.startswith("TZ ") and "Europe/Paris" in x for x in out.getvalue().splitlines()))
        with contextlib.redirect_stdout(io.StringIO()):
            manage.run(["config", "reset", "UPLOAD_MAX_FILE_MB"])
        self.assertEqual(ss.next_values(), {"TZ": "Europe/Paris", "SHL_TIMEOUT": 30})
        self.assertEqual(ss.read_doc()[0]["good"], {"TZ": "Europe/Paris"})
        with contextlib.redirect_stdout(io.StringIO()):
            manage.run(["config", "reset", "--all"])
        self.assertEqual(ss.next_values(), {})
        restart()
        self.assertEqual({n: ss.lookup(n)[0] for n in ss.DEFS}, {n: "default" for n in ss.DEFS})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            manage.run(["config", "reset", "SHL_TIMEOUT"])
        self.assertIn("nothing to reset", out.getvalue())

    def test_k_the_environment_always_wins(self):
        """#1131's review: a variable the environment sets is in force even where these checks wouldn't take it (more
        than 20 proxies, as Cloudflare's 22 ranges, a "unix:" one, * among others, a zone this machine doesn't know, a
        number it can't read): never dropped for the file's or the default's in silence. Locked on the page, given to
        uvicorn as it's set, and a warning logged."""
        self.put({"FORWARDED_ALLOW_IPS": "10.0.0.5", "SLOT_GRACE_DAYS": 30, "UPLOAD_MAX_FILE_MB": 2048})
        restart()
        cloudflare = ", ".join(f"198.51.{i}.0/24" for i in range(22))
        for env, want in ((cloudflare, cloudflare), ("unix:/run/proxy.sock", "unix:/run/proxy.sock"), ("*, 10.0.0.1", "*, 10.0.0.1")):
            with mock.patch.dict(os.environ, {"FORWARDED_ALLOW_IPS": env}), self.assertLogs("sanguity", "WARNING") as said:
                ss._warned.clear()
                self.assertEqual(ss.lookup("FORWARDED_ALLOW_IPS"), ("environment", want))
                self.assertEqual(ss.uvicorn_options()["forwarded_allow_ips"], want)
                row = self.row("FORWARDED_ALLOW_IPS")
                self.assertEqual((row["from"], row["can_change"]), ("environment", False))
                self.assertEqual(self.put({"FORWARDED_ALLOW_IPS": "10.0.0.6"}).status_code, 409)
            self.assertIn("FORWARDED_ALLOW_IPS", said.output[0])
        with mock.patch.dict(os.environ, {"TZ": "Mars/Olympus_Mons", "SLOT_GRACE_DAYS": "soon", "UPLOAD_MAX_FILE_MB": "lots"}):
            with self.assertLogs("sanguity", "WARNING"):
                ss._warned.clear()
                self.assertEqual(ss.lookup("TZ"), ("environment", "Mars/Olympus_Mons"))
                self.assertEqual(ss.lookup("SLOT_GRACE_DAYS"), ("environment", 7), "the receiver's reading of one it can't")
            self.assertEqual(ss.lookup("UPLOAD_MAX_FILE_MB"), ("environment", 4096), "not the file's 2048")
            self.assertEqual({r["name"]: r["can_change"] for r in self.state()["settings"] if r["name"] in ("TZ", "SLOT_GRACE_DAYS", "UPLOAD_MAX_FILE_MB")},
                             {"TZ": False, "SLOT_GRACE_DAYS": False, "UPLOAD_MAX_FILE_MB": False})

    def test_l_trusting_everyone_and_the_proxy_in_between(self):
        """#1131's review: 0.0.0.0/0 and ::/0 are *; trusting everyone is typed to confirm first (the one rule's 409); a
        list that leaves out the proxy the saving request came through is refused."""
        self.assertEqual(ss.proxies("0.0.0.0/0"), "*")
        self.assertEqual(ss.proxies("::/0"), "*")
        with self.assertRaises(ss.Refused):
            ss.proxies("0.0.0.0/0, 10.0.0.1")
        for v in ("*", "0.0.0.0/0"):
            r = self.put({"FORWARDED_ALLOW_IPS": v})
            self.assertEqual(r.status_code, 409, r.text)
            self.assertEqual(r.json()["detail"]["code"], "confirm")
            self.assertIn("any address", r.json()["detail"]["confirm"]["message"])
        self.assertEqual(ss.next_values(), {})
        r = self.c.put("/api/settings/install/restart/values", json={"values": {"FORWARDED_ALLOW_IPS": "*"}, "confirm": "trust everyone"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(ss.next_values(), {"FORWARDED_ALLOW_IPS": "*"})
        self.put({"FORWARDED_ALLOW_IPS": None})
        # through a proxy at 10.0.0.5 that the server trusts now (python -m server's app: PeerKept keeps the peer)
        via = TestClient(ss.asgi_app(main.app, "10.0.0.5"), client=("10.0.0.5", 50000))
        self.assertEqual(via.post("/api/login", json={"username": "ada", "password": PW}, headers=H).status_code, 200)
        fwd = {**H, "X-Forwarded-For": "203.0.113.9"}
        r = via.put("/api/settings/install/restart/values", json={"values": {"FORWARDED_ALLOW_IPS": "172.16.0.0/12"}}, headers=fwd)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("10.0.0.5", r.json()["detail"])
        r = via.put("/api/settings/install/restart/values", json={"values": {"FORWARDED_ALLOW_IPS": None}}, headers=fwd)
        self.assertEqual(r.status_code, 409, "the default, 127.0.0.1, leaves it out too")
        r = via.put("/api/settings/install/restart/values", json={"values": {"FORWARDED_ALLOW_IPS": "10.0.0.0/8"}}, headers=fwd)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(ss.next_values(), {"FORWARDED_ALLOW_IPS": "10.0.0.0/8"})
        direct = TestClient(ss.asgi_app(main.app, "127.0.0.1"), client=("192.0.2.7", 50000))   # no proxy in between: no guard
        self.assertEqual(direct.post("/api/login", json={"username": "ada", "password": PW}, headers=H).status_code, 200)
        r = direct.put("/api/settings/install/restart/values", json={"values": {"FORWARDED_ALLOW_IPS": "172.16.0.0/12"}}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)

    def test_m_restart_now_says_who_starts_it_again(self):
        """#1131's review: Restart now's question says Sanguity starts again only where its container restarts it; on an
        install no template names, that nothing is known to."""
        with mock.patch.dict(os.environ, {"SANGUITY_DEPLOYMENT": "compose"}):
            self.assertIn("starts again only because its container restarts it automatically", self.state()["restart_says"])
        with mock.patch.dict(os.environ, {"SANGUITY_DEPLOYMENT": ""}):
            says = self.state()["restart_says"]
        self.assertIn("nothing is known to start it again", says)
        self.assertIn("stays stopped until you start it yourself", says)

    def test_n_proxies_exactly_as_given(self):
        """#1131's re-review: the environment's proxies reach uvicorn as its own text, never widened (10.0.0.5/8 isn't
        made 10.0.0.0/8); on the page a network with host bits set is refused, saying which it's in; networks that
        together are every IPv4 or IPv6 address are refused (* says so, and is confirmed); the typed confirmation
        matches whatever its case."""
        for env in ("10.0.0.5/8", "10.0.0.5, 172.16.0.0/12", " 10.0.0.1 ,10.0.0.2"):
            with mock.patch.dict(os.environ, {"FORWARDED_ALLOW_IPS": env}), self.assertNoLogs("sanguity", "WARNING") if env != "10.0.0.5/8" else self.assertLogs("sanguity", "WARNING"):
                ss._warned.clear()
                self.assertEqual(ss.uvicorn_options()["forwarded_allow_ips"], env.strip())
                self.assertEqual(ss.lookup("FORWARDED_ALLOW_IPS"), ("environment", env.strip()))
        with self.assertRaisesRegex(ss.Refused, r"10\.0\.0\.0/8"):
            ss.proxies("10.0.0.5/8")
        r = self.put({"FORWARDED_ALLOW_IPS": "10.0.0.5/8"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("10.0.0.0/8", r.json()["detail"])
        for v in ("0.0.0.0/1, 128.0.0.0/1", "::/1, 8000::/1", "10.0.0.1, 0.0.0.0/2, 64.0.0.0/2, 128.0.0.0/1"):
            r = self.put({"FORWARDED_ALLOW_IPS": v})
            self.assertEqual(r.status_code, 400, v)
            self.assertIn("every IPv", r.json()["detail"])
        self.assertEqual(ss.proxies("0.0.0.0/1, 10.0.0.1"), "0.0.0.0/1, 10.0.0.1", "half isn't all")
        r = self.c.put("/api/settings/install/restart/values", json={"values": {"FORWARDED_ALLOW_IPS": "*"}, "confirm": "  Trust Everyone "}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(ss.next_values(), {"FORWARDED_ALLOW_IPS": "*"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
