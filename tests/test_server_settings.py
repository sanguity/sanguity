#!/usr/bin/env python3
"""The settings that need a restart (#1121, server/server_settings.py): changed on General → Needs a restart, kept in
data/server-settings.json (0600), and read as the server starts, a restart simulated by calling the start-up path
(server_settings.start) in-process. Each applies only after a restart, the environment's is taken in once and
ignored after (#1158), a value past its
bounds is refused, a pending value that doesn't come up healthy falls back to the last good one and says so,
Restart now asks for the fresh check, is logged and copies the receiver's for it, and server.manage config reset puts
the defaults back. Fictional data only.

  python3 tests/test_server_settings.py
"""
import contextlib, importlib.util, io, json, os, shutil, stat, sys, tempfile, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-server-settings-test-")
RESTARTS = ("UPLOAD_MAX_FILE_MB", "UPLOAD_MAX_TOTAL_MB", "UPLOAD_MAX_FILES", "UPLOAD_MAX_ZIP_RATIO", "JOURNAL_PHOTO_MAX_MB", "SYNC_HOURS",
            "SYNC_MAX_RESOURCES", "SYNC_MAX_ATTACHMENT_MB", "CONNECT_TIMEOUT", "SHL_MAX_MB", "SHL_MAX_FILES", "SHL_TIMEOUT",
            "HASH_PARALLEL", "FORWARDED_ALLOW_IPS", "TZ", "SLOT_GRACE_DAYS", "RESTORE_KEEP_DAYS", "OIDC_TIMEOUT", "NOTIFY_TIMEOUT",
            "DIRECTORY_TIMEOUT", "DOWNLOAD_TIMEOUT", "URL_BASE", "HTTPS_CERT", "HTTPS_KEY", "HTTPS_PORT", "LOG_LEVEL",
            "OUTBOUND_PROXY", "OUTBOUND_PROXY_USER", "OUTBOUND_PROXY_BYPASS")
for name in RESTARTS + ("SANGUITY_SAVED_SETTINGS", "TZ_DEFAULT", "PUBLIC_ORIGINS", "SANGUITY_DEPLOYMENT"):
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
GOOD = {"UPLOAD_MAX_FILE_MB": 2048, "UPLOAD_MAX_TOTAL_MB": 8192, "UPLOAD_MAX_FILES": 5000, "UPLOAD_MAX_ZIP_RATIO": 100, "JOURNAL_PHOTO_MAX_MB": 10,
        "SYNC_HOURS": 6, "SYNC_MAX_RESOURCES": 5000, "SYNC_MAX_ATTACHMENT_MB": 100, "CONNECT_TIMEOUT": 60, "SHL_MAX_MB": 32,
        "SHL_MAX_FILES": 20, "SHL_TIMEOUT": 40, "HASH_PARALLEL": 4, "FORWARDED_ALLOW_IPS": "172.16.0.0/12, 10.0.0.5",
        "TZ": "America/Chicago", "SLOT_GRACE_DAYS": 14, "RESTORE_KEEP_DAYS": 60, "OIDC_TIMEOUT": 30, "NOTIFY_TIMEOUT": 20,
        "DIRECTORY_TIMEOUT": 120, "DOWNLOAD_TIMEOUT": 90, "URL_BASE": "/sanguity", "HTTPS_CERT": "tls/cert.pem",
        "HTTPS_KEY": "tls/key.pem", "HTTPS_PORT": 9443, "LOG_LEVEL": "debug", "OUTBOUND_PROXY": "http://proxy.lan:3128",
        "OUTBOUND_PROXY_USER": "sanguity", "OUTBOUND_PROXY_BYPASS": ".example.org, 10.0.0.0/8"}
BAD = {"UPLOAD_MAX_FILE_MB": 5000, "UPLOAD_MAX_TOTAL_MB": 0, "UPLOAD_MAX_FILES": 10**6, "UPLOAD_MAX_ZIP_RATIO": 5, "JOURNAL_PHOTO_MAX_MB": 26,
       "SYNC_HOURS": 0, "SYNC_MAX_RESOURCES": 10, "SYNC_MAX_ATTACHMENT_MB": 2048, "CONNECT_TIMEOUT": 1, "SHL_MAX_MB": 1000,
       "SHL_MAX_FILES": 0, "SHL_TIMEOUT": 600, "HASH_PARALLEL": 64, "FORWARDED_ALLOW_IPS": "proxy.example.test",
       "TZ": "Mars/Olympus_Mons", "SLOT_GRACE_DAYS": 0, "RESTORE_KEEP_DAYS": 1.5, "OIDC_TIMEOUT": 61, "NOTIFY_TIMEOUT": 2,
       "DIRECTORY_TIMEOUT": 9, "DOWNLOAD_TIMEOUT": 301, "URL_BASE": "/api/x", "HTTPS_CERT": "../elsewhere.pem",
       "HTTPS_KEY": "/etc/ssl/private/key.pem", "HTTPS_PORT": 80, "LOG_LEVEL": "verbose", "OUTBOUND_PROXY": "ftp://proxy.lan:21",
       "OUTBOUND_PROXY_USER": "two words", "OUTBOUND_PROXY_BYPASS": "not a host!"}
CHANGE_IT = "change it"   # (#1159) typed to confirm the URL base, built-in HTTPS and the proxy


def make_certificate(name, days=90, names=("health.example.test",)):
    """A self-signed certificate and its key in the data folder's tls/ (fictional names): (cert, key) relative paths."""
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.timezone.utc)
    subject = x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, names[0])])
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=2))
            .not_valid_after(now + datetime.timedelta(days=days))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in names]), critical=False).sign(key, hashes.SHA256()))
    os.makedirs(os.path.join(TMP, "tls"), exist_ok=True)
    with open(os.path.join(TMP, "tls", name + ".pem"), "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(os.path.join(TMP, "tls", name + "-key.pem"), "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return f"tls/{name}.pem", f"tls/{name}-key.pem"


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

    def setUp(self):
        for p in (ss.path(), ss.receiver_path()):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(p)
        ss.forget()
        restart()

    def put(self, values, confirm=None):
        return self.c.put("/api/settings/install/restart/values", json={"values": values, "confirm": confirm}, headers=H)

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
        cert, key = make_certificate("cert")
        os.replace(os.path.join(TMP, cert), os.path.join(TMP, GOOD["HTTPS_CERT"]))
        os.replace(os.path.join(TMP, key), os.path.join(TMP, GOOD["HTTPS_KEY"]))
        self.assertEqual(self.put(GOOD).status_code, 409, "the URL base, HTTPS and the proxy are asked first")
        r = self.put(GOOD, CHANGE_IT)
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
        self.assertEqual(config.url_base(), "/sanguity")
        ss.reset(["URL_BASE"])   # (the rest of the tests at the root)
        r = self.put({n: None for n in GOOD})   # back to the defaults, at the next start
        self.assertTrue(r.json()["restart_needed"])
        restart()
        ss.promote(say=QUIET)
        self.assertEqual({n: ss.lookup(n) for n in ss.DEFS}, {n: ("default", d.default) for n, d in ss.DEFS.items()})

    def test_c_the_environment_taken_in_once(self):
        """(#1158) The first start of this version copies what the environment sets into the file, as it was in force
        (over the file's own, pending ones too), records it, and says so; a value these checks don't take isn't copied
        (said). After that the environment changes nothing: the page changes each one, a later change to it is ignored
        with a warning naming it, and the file is never overwritten by it again."""
        self.put({"UPLOAD_MAX_FILE_MB": 2048, "TZ": "Europe/Paris"})   # pending, before the upgrade
        doc = json.load(open(ss.path()))
        doc.pop("taken")   # as a file from before this version
        ss.write_doc(doc)
        env = {"UPLOAD_MAX_FILE_MB": "1024", "FORWARDED_ALLOW_IPS": "10.0.0.0/8", "TZ": "Asia/Tokyo", "SHL_TIMEOUT": "forty",
               "HASH_PARALLEL": "2"}
        said = []
        with mock.patch.dict(os.environ, env):
            ss.forget()
            ss.start(say=said.append)
            ss.promote(say=QUIET)   # (it ran healthy)
            self.assertEqual(ss.lookup("UPLOAD_MAX_FILE_MB"), ("file", 1024))
            self.assertEqual(ss.uvicorn_options()["forwarded_allow_ips"], "10.0.0.0/8")
            self.assertEqual(self.c.get("/api/settings/phone").json()["tz"], "Asia/Tokyo")
            self.assertEqual(ss.lookup("SHL_TIMEOUT"), ("default", 20))
            self.assertEqual(ss.taken()["names"], ["UPLOAD_MAX_FILE_MB", "FORWARDED_ALLOW_IPS", "TZ"], "the default isn't one taken")
            self.assertTrue(any("SHL_TIMEOUT" in s and "couldn't be taken in" in s and "forty" in s for s in said), said)
            self.assertTrue(any("UPLOAD_MAX_FILE_MB, FORWARDED_ALLOW_IPS, TZ" in s for s in said), said)
            with open(ss.receiver_path()) as f:
                self.assertEqual(json.load(f), {"TZ": "Asia/Tokyo"}, "the receiver's copy, as it starts")
            self.assertNotIn("1024", json.dumps(json.load(open(ss.path()))["taken"]), "a fingerprint, not the text")
            row = self.row("UPLOAD_MAX_FILE_MB")
            self.assertEqual((row["value"], row["from"], row["can_change"]), (1024, "file", True))
            self.assertEqual(self.put({"UPLOAD_MAX_FILE_MB": 3000}).status_code, 200, "the page changes it")
        later = {**env, "UPLOAD_MAX_FILE_MB": "512", "SYNC_HOURS": "3"}   # .env changed later
        said = []
        with mock.patch.dict(os.environ, later):
            ss.forget()
            ss.start(say=said.append)
            ss.promote(say=QUIET)
            self.assertEqual(ss.lookup("UPLOAD_MAX_FILE_MB"), ("file", 3000), "the page's, never the environment's again")
            self.assertEqual(ss.lookup("SYNC_HOURS"), ("default", 12))
            warned = sorted(n for n in later if any(f"{n} is set in the environment" in s and "ignored" in s for s in said))
            self.assertEqual(warned, ["SYNC_HOURS", "UPLOAD_MAX_FILE_MB"], said)
            self.assertFalse(ss.take_late(say=QUIET), "once")
        ss.reset(["UPLOAD_MAX_FILE_MB"])
        with mock.patch.dict(os.environ, env):
            ss.forget()
            restart()
            self.assertEqual(ss.lookup("UPLOAD_MAX_FILE_MB"), ("default", 4096), "a reset isn't undone by the environment")

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

    def test_k_what_the_page_wouldnt_take_is_never_taken_weaker(self):
        """(#1158's review, #1131's) A value the environment sets that these checks wouldn't take is never taken in weaker
        than it was set: a number past its bounds is the nearest bound; the trusted proxies keep the entries that pass (a
        network with host bits set, which uvicorn trusted nobody for, more than 20, or * among others, left out: never
        widened), and with none this machine's own, never * (a template's default); a zone or a number it can't read at
        all isn't taken. Each is said, left out of "seen" and named with what was used, and said again at every start."""
        cloudflare = [f"198.51.{i}.0/24" for i in range(22)]
        d = ss.DEFS["FORWARDED_ALLOW_IPS"]
        for env, want in ((", ".join(cloudflare), ", ".join(cloudflare[:20])), ("172.18.0.5/16", "127.0.0.1"),
                          ("172.18.0.5/16, 10.0.0.1", "10.0.0.1"), ("*, 10.0.0.1", "10.0.0.1"), ("unix:/run/proxy.sock", "127.0.0.1")):
            os.unlink(ss.path())
            said = []
            with mock.patch.dict(os.environ, {"FORWARDED_ALLOW_IPS": env, "TZ": "Mars/Olympus_Mons", "SLOT_GRACE_DAYS": "soon",
                                              "UPLOAD_MAX_FILE_MB": "99999", "SHL_TIMEOUT": "2"}), mock.patch.object(d, "default", "*"):
                ss.forget()
                ss.start(say=said.append)
                self.assertEqual(ss.lookup("FORWARDED_ALLOW_IPS")[1], want, env)
                self.assertEqual(ss.uvicorn_options()["forwarded_allow_ips"], want)
                self.assertEqual((ss.lookup("UPLOAD_MAX_FILE_MB"), ss.lookup("SHL_TIMEOUT")), (("default", 4096), ("file", 5)))
                self.assertEqual((ss.lookup("TZ"), ss.lookup("SLOT_GRACE_DAYS")), (("default", "UTC"), ("default", 7)))
                t = ss.taken()
                self.assertEqual(sorted(t["adjusted"]), ["FORWARDED_ALLOW_IPS", "SHL_TIMEOUT", "SLOT_GRACE_DAYS", "TZ", "UPLOAD_MAX_FILE_MB"])
                self.assertIn("4096 is used", t["adjusted"]["UPLOAD_MAX_FILE_MB"])
                self.assertEqual(json.load(open(ss.path()))["taken"]["seen"], {}, "none of them seen: said at every start")
                said = []
                ss.forget()
                ss.start(say=said.append)
                self.assertTrue(any("UPLOAD_MAX_FILE_MB in the environment couldn't be taken in as it's set" in s for s in said), said)
        self.assertEqual({k: ss.proxy_default(k) for k in ("compose", "release", "truenas", "casaos", "unraid", "allinone", "", "other", "k8s")},
                         {"compose": "*", "release": "*", "truenas": "*", "casaos": "*", "unraid": "*", "allinone": "*",
                          "": "127.0.0.1", "other": "127.0.0.1", "k8s": "127.0.0.1"}, "* only under Sanguity's own templates")

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
        """#1131's re-review: on the page a network with host bits set is refused, saying which it's in (never widened:
        10.0.0.5/8 isn't made 10.0.0.0/8); networks that
        together are every IPv4 or IPv6 address are refused (* says so, and is confirmed); the typed confirmation
        matches whatever its case."""
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


class UrlBaseHttpsLogProxy(unittest.TestCase):
    """(#1159) The URL base, built-in HTTPS's certificate, the log level, and the outbound proxy's password."""
    c = None

    @classmethod
    def setUpClass(cls):
        cls.c = RestartSettings.c

    def setUp(self):
        for p in (ss.path(), ss.receiver_path()):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(p)
        ss.forget()
        restart()

    def put(self, values, confirm=CHANGE_IT):
        return self.c.put("/api/settings/install/restart/values", json={"values": values, "confirm": confirm}, headers=H)

    def test_a_a_certificate_is_checked_before_its_saved_and_its_names_and_expiry_shown(self):
        good, soon = make_certificate("good", 200, ("health.example.test", "sanguity.example.test")), make_certificate("soon", 20)
        other = make_certificate("other")
        r = self.put({"HTTPS_CERT": good[0]})
        self.assertEqual(r.status_code, 400, "both, or neither")
        r = self.put({"HTTPS_CERT": good[0], "HTTPS_KEY": other[1]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("don't load together", r.json()["detail"])
        self.assertEqual(self.put({"HTTPS_CERT": "tls/none.pem", "HTTPS_KEY": good[1]}).status_code, 400)
        self.assertEqual(self.put({"HTTPS_CERT": good[0], "HTTPS_KEY": good[1]}, confirm=None).status_code, 409, "asked first")
        r = self.put({"HTTPS_CERT": good[0], "HTTPS_KEY": good[1]})
        self.assertEqual(r.status_code, 200, r.text)
        h = r.json()["https"]
        self.assertEqual((h["names"], h["warn"]), (["health.example.test", "sanguity.example.test"], False))
        self.assertTrue(195 <= h["days_left"] <= 200, h)
        h = self.put({"HTTPS_CERT": soon[0], "HTTPS_KEY": soon[1]}).json()["https"]
        self.assertEqual((h["days_left"] <= 20, h["warn"]), (True, True), "warned 30 days before it expires")
        expired = make_certificate("expired", -1)
        self.assertIn("expired", self.put({"HTTPS_CERT": expired[0], "HTTPS_KEY": expired[1]}).json()["detail"])
        self.assertEqual(ss.https_files(ss.next_values()), (os.path.join(TMP, soon[0]), os.path.join(TMP, soon[1])))

    def test_b_the_url_base_everywhere(self):
        """Under /sanguity: requests with it reach the app, its cookies are at it (__Secure- over HTTPS: __Host- needs
        Path=/), and every address the app gives out has it."""
        self.assertEqual(self.put({"URL_BASE": "sanguity/"}).status_code, 200)
        restart()
        try:
            self.assertEqual(config.url_base(), "/sanguity")
            web = TestClient(main.app, base_url="https://health.example.test")
            self.assertEqual(web.get("/sanguity/api/healthz").status_code, 204)
            r = web.post("/sanguity/api/login", json={"username": "ada", "password": PW}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            cookies = r.headers.get_list("set-cookie")
            self.assertTrue(any(c.startswith("__Secure-sg_session=") and "Path=/sanguity/" in c and "Secure" in c for c in cookies), cookies)
            self.assertTrue(any(c.startswith("sg_device=") and "Path=/sanguity/api/login" in c for c in cookies), cookies)
            self.assertEqual(web.get("/sanguity/api/me").status_code, 200, "signed in under the base")
            self.assertEqual(config.public_address(["https://health.example.test"]), "https://health.example.test/sanguity")
            from server import oidc, routes_oidc
            from starlette.requests import Request
            req = Request({"type": "http", "scheme": "https", "path": "/", "headers": [(b"host", b"health.example.test")],
                           "server": ("health.example.test", 443), "query_string": b""})
            self.assertEqual(routes_oidc._cookie(req), ("__Secure-sanguity_oidc", "/sanguity/"))
            self.assertEqual(oidc.redirect_uri(routes_oidc._page_origin(req)), "https://health.example.test/sanguity/login.html")
            from server import routes_calendar
            self.assertEqual(routes_calendar._base_url(req), "https://health.example.test/sanguity")
            plain = TestClient(main.app, base_url="http://health.example.test")
            r = plain.post("/sanguity/api/login", json={"username": "ada", "password": PW}, headers=H)
            self.assertTrue(any(c.startswith("sg_session=") and "Path=/sanguity/" in c for c in r.headers.get_list("set-cookie")))
            r = plain.post("/sanguity/api/logout", headers=H)
            self.assertTrue(any(c.startswith("sg_session=") and "Path=/sanguity/" in c for c in r.headers.get_list("set-cookie")))
        finally:
            ss.reset(["URL_BASE"])
        for bad in ("/api", "/a b", "/../x", "x" * 101):
            self.assertEqual(self.put({"URL_BASE": bad}).status_code, 400, bad)

    def test_c_no_record_text_at_trace(self):
        """At the log level Trace, the app's log has each request's route and status, but never a record's text, a
        profile's name, a value typed in or a password."""
        from server import profiles as prof
        out = io.StringIO()
        h = logging.StreamHandler(out)
        lg = logging.getLogger("sanguity")
        lg.addHandler(h)
        main.set_log_level("trace")
        try:
            db = dbm.connect()
            uid = db.execute("SELECT id FROM users WHERE username='ada'").fetchone()[0]
            pid = prof.create_profile(db, "Wren Quillfeather (fictional)", owner_id=uid)
            db.close()
            secret = "Marmalade-coloured rash on the left elbow"
            r = self.c.post(f"/api/profiles/{pid}/journal", json={"kind": "symptom", "t": "2026-11-20", "title": secret,
                                                                   "severity": 3, "note": "Quillfeather note text"}, headers=H)
            self.assertEqual(r.status_code, 201, r.text)
            self.assertEqual(self.c.get(f"/api/profiles/{pid}/journal").status_code, 200)
            self.c.get("/api/directory", params={"q": "Marmalade elbow"})
            self.c.post(f"/api/profiles/{pid}/documents/search", json={"q": "Marmalade elbow"}, headers=H)
            self.c.post("/api/login", json={"username": "ada", "password": "a wrong passphrase Quillfeather"}, headers=H)
        finally:
            main.set_log_level("info")
            lg.removeHandler(h)
        text = out.getvalue()
        self.assertIn("request POST /api/profiles/{pid}/journal 201", text)
        self.assertIn("fields=q", text, "the names of the query's fields at Trace")
        for word in ("Marmalade", "elbow", "Quillfeather", "Wren", "wrong passphrase"):
            self.assertNotIn(word, text)

    def test_d_the_proxy_password_is_write_only_and_kept_for_its_proxy(self):
        from server import outside
        self.assertEqual(self.c.put("/api/outside/proxy-password", json={"value": "lantern"}, headers=H).status_code, 409,
                         "no proxy yet")
        self.assertEqual(self.put({"OUTBOUND_PROXY": "socks5://proxy.lan:1080", "OUTBOUND_PROXY_USER": "sg"}).status_code, 200)
        r = self.c.put("/api/outside/proxy-password", json={"value": "harbour lantern"}, headers=H)
        self.assertEqual(r.json(), {"source": "install"})
        st = self.c.get("/api/settings/install/restart").json()
        self.assertEqual(st["proxy_password"]["source"], "install")
        self.assertNotIn("harbour lantern", json.dumps(st))
        self.assertIsNone(outside.proxy(), "none in force until the restart")
        restart()
        ss.promote(say=QUIET)
        p = outside.proxy()
        self.assertEqual((p.scheme, p.host, p.port, p.user, p.password), ("socks5", "proxy.lan", 1080, "sg", "harbour lantern"))
        with mock.patch.dict(os.environ, {"OUTBOUND_PROXY_PASSWORD": "from the environment"}):
            self.assertEqual(outside.proxy().password, "from the environment", "managed by the environment, opt-in")
            self.assertEqual(self.c.put("/api/outside/proxy-password", json={"value": "x"}, headers=H).status_code, 409)
        self.assertEqual(self.put({"OUTBOUND_PROXY": "http://other.lan:3128"}).status_code, 200)
        restart()
        self.assertEqual(outside.proxy().password, "", "never sent to another proxy")
        ss.reset()


def tearDownModule():
    shutil.rmtree(TMP, True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
