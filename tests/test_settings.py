#!/usr/bin/env python3
"""Settings in the database (server/settings.py, server/routes_settings.py, issue #35): every key of
settings.example.json round-trips through the API, defaults and the precedence order (default, install, profile,
user), who may change what by scope, bad values refused, reset per section, the CSRF check and the activity log,
settings.json moved into the database once at the upgrade (each profile's, and the setup build's only for a new
linked profile, never over what was changed in the app), export and import, and values encrypted at rest.
Also each person's own time zone and date format (#42), and a watch receiver secret per profile (#46), checked with
the real receiver: two profiles each get their own uploads, and the install's single secret from before keeps working
for the first profile until it makes one of its own.
Fictional data only.

  python3 tests/test_settings.py
"""
import contextlib, copy, io, json, os, shutil, sys, tempfile, unittest, zipfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-settings-test-")
BUILD = os.path.join(TMP, "build")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=BUILD,
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch-rx", "public", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  SANGUITY_WATCH_KEYS=os.path.join(TMP, "watch-rx", "keys", "receiver-secrets.json"), RECEIVER_PORT="8731",
                  PUBLIC_ORIGINS="https://health.example.test",
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_CORRECTIONS=os.path.join(TMP, "config", "corrections.json"))
for d in ("records", "keys", "build", "config", "profiles", os.path.join("watch-rx", "keys"), os.path.join("watch-rx", "public")):
    os.makedirs(os.path.join(TMP, d), exist_ok=True)
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, config, crypto, db as dbm, export, main, profiles as prof, settings, watch_keys, watch_store   # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
OPEN = open   # (#1158: the test that no .env file is read)
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites", "lee": "maple tunnel brisk saddle",
      "ada": "admin of the fictional install", "zed": "a stranger to every profile"}
with open(os.path.join(ROOT, "settings.example.json"), encoding="utf-8") as f:
    EXAMPLE = json.load(f)

DATA = {"built": "2026-11-01T00:00:00+00:00", "source": "fictional",
        "events": [{"id": 1, "t": "2026-02-10T09:15:00", "k": "visit", "title": "Physical therapy session", "sub": ""}],
        "details": {}, "labs": [], "costs": []}
BUILD_SETTINGS = {"overview": {"keyLabs": ["Ferritin", "Glucose"]}, "sources": {"LAB2": "Second fictional lab"}}
# what profile 1 kept in profiles/1/settings.json before the upgrade
OLD_FILE = {"trends": {"defaultLabs": ["Glucose"]}, "labNicknames": {"Glucose": "Sugar"},
            "visitWorkouts": [{"match": "^physical therapy\\b", "name": "Physical therapy", "minutes": 45}],
            "unknownKey": 1}

# settings the example file doesn't have, of the other two scopes, to check precedence and permissions
settings.section("testing", "Testing")
settings.define("test.mine", "testing", "Just mine", "light", lambda v: settings._text(v, "It"), scope="user", private=False)
settings.define("test.server", "testing", "Server-wide", 10, lambda v: int(v), scope="install", private=False)
# (#999) a server setting the environment can set too, as General's are: SYNC_HOURS, read where it's used
settings.section("testing-env", "Testing the environment")   # (its own: the reset test counts the section above's)
settings.define("testEnv.syncHours", "testing-env", "Sync every (hours)", 12, settings.whole(1, 168, "Hours"), scope="install",
                private=False, env="SYNC_HOURS")
# (#1006) an install setting that could hold health information: logged by name only
settings.define("testEnv.sealed", "testing-env", "Sealed", "", lambda v: str(v), scope="install")


def flat(obj, prefix=""):
    """{"overview": {"keyLabs": [...]}} -> {"overview.keyLabs": [...]}, down to the registry's keys"""
    out = {}
    for k, v in obj.items():
        if k.startswith("_"):   # "_about", "_format": notes, not settings
            continue
        key = prefix + k
        if key in settings.SETTINGS:
            out[key] = v
        elif isinstance(v, dict):
            out.update(flat(v, key + "."))
        else:
            out[key] = v
    return out


def write(path, obj, mtime_bump=0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    if mtime_bump:
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + mtime_bump * 1_000_000_000))


def client():
    return TestClient(main.app, base_url="http://testserver")


def old_install():
    """An install at schema version 17 (the step before settings): a profile with its own settings.json, and one linked to the setup build
    holding the copy of the build's settings.json that the app used to keep."""
    write(os.path.join(BUILD, "data.json"), DATA)
    write(os.path.join(BUILD, "settings.json"), BUILD_SETTINGS)
    full = dbm.SCHEMA
    dbm.SCHEMA = full[:17]
    try:
        dbm.init()
    finally:
        dbm.SCHEMA = full
    db = dbm.connect()
    db.execute("INSERT INTO profiles(id, name, created) VALUES(1, 'Robin (fictional)', 0)")
    db.execute("INSERT INTO profiles(id, name, created, linked_build, build_mtime) VALUES(2, 'Built (fictional)', 0, 1, ?)",
               (prof.build_version(),))
    db.close()
    write(os.path.join(TMP, "profiles", "1", "settings.json"), OLD_FILE)
    write(os.path.join(TMP, "profiles", "2", "settings.json"), BUILD_SETTINGS)


class Settings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        old_install()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cls.ctx = client().__enter__()
        cls.startup = out.getvalue()
        db = dbm.connect()
        cls.uid = {n: accounts.create(db, n, password=pw, is_admin=(n == "ada"))[0] for n, pw in PW.items()}
        for pid in (1, 2):
            db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'owner')", (cls.uid["robin"], pid))
            db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (cls.uid["kim"], pid))
            db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (cls.uid["lee"], pid))
            db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (cls.uid["ada"], pid))
        write(os.path.join(TMP, "dataset", "data.json"), DATA)   # records for profile 1 (no settings.json: kept)
        prof.import_dataset(db, 1, os.path.join(TMP, "dataset"))
        db.close()
        cls.c = {n: cls.login(n) for n in PW}
        db = dbm.connect()   # an admin with an authenticator app (admins need a second factor), once signed in
        db.execute("UPDATE users SET totp_secret=? WHERE id=?", (crypto.enc("JBSWY3DPEHPK3PXP"), cls.uid["ada"]))
        db.close()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        shutil.rmtree(TMP, True)

    @staticmethod
    def login(name):
        c = client()
        r = c.post("/api/login", json={"username": name, "password": PW[name]}, headers=H)
        assert r.status_code == 200, r.text
        return c

    def merged(self, pid=1, who="robin"):
        r = self.c[who].get(f"/api/profiles/{pid}/settings")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def put(self, key, value, pid=1, who="robin", status=200):
        r = self.c[who].put(f"/api/profiles/{pid}/settings/{key}", json={"value": value}, headers=H)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def fresh_profile(self, name="Fresh (fictional)"):
        db = dbm.connect()
        pid = prof.create_profile(db, name, owner_id=self.uid["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (self.uid["lee"], pid))
        db.close()
        return pid

    # ---------------------------------------------------------------- the upgrade

    def test_a_migration_happened_once(self):
        self.assertIn("Moved the settings.json of 2 profile(s) into the database", self.startup)
        m = self.merged(1)
        self.assertEqual(m["trends"]["defaultLabs"], ["Glucose"])
        self.assertEqual(m["visitWorkouts"], [{**OLD_FILE["visitWorkouts"][0], "regex": True}])   # a pattern, as before #43
        # a name map in the file added to the default names, as the page used to merge it
        self.assertEqual(m["labNicknames"], {**settings.SETTINGS["labNicknames"].default, "Glucose": "Sugar"})
        self.assertEqual(m["overview"]["keyLabs"], settings.KEY_LABS)   # not in the file: the default
        self.assertNotIn("unknownKey", m)
        self.assertEqual(self.merged(2)["overview"]["keyLabs"], ["Ferritin", "Glucose"])
        for pid in (1, 2):   # deleted once loaded, not kept aside for good (#934)
            folder = os.path.join(TMP, "profiles", str(pid))
            self.assertFalse(os.path.exists(os.path.join(folder, "settings.json")))
            self.assertFalse(os.path.exists(os.path.join(folder, settings.MIGRATED)))
        # a second start changes nothing, even after the app changed a setting; the copy an older version kept goes
        self.put("trends.defaultLabs", ["Glucose", "Ferritin"])
        kept = os.path.join(TMP, "profiles", "1", settings.MIGRATED)
        with open(kept, "w", encoding="utf-8") as f:
            json.dump(OLD_FILE, f)
        db = dbm.connect()
        self.assertEqual(settings.migrate_files(db, prof.profile_dir), 0)
        db.close()
        self.assertFalse(os.path.exists(kept), "an older version's copy is deleted at the next start (#934)")
        self.assertEqual(self.merged(1)["trends"]["defaultLabs"], ["Glucose", "Ferritin"])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            with client():   # a whole restart
                pass
        self.assertNotIn("settings.json", out.getvalue())
        self.assertEqual(self.merged(1)["trends"]["defaultLabs"], ["Glucose", "Ferritin"])
        self.put("trends.defaultLabs", ["Glucose"])

    def test_b_rebuild_never_overrides(self):
        """A rebuild with a changed settings.json leaves the profile's settings alone, and says so."""
        self.put("sources", {"LAB2": "Renamed in the app"}, pid=2)
        write(os.path.join(BUILD, "settings.json"), {**BUILD_SETTINGS, "sources": {"LAB2": "From the rebuild"}}, mtime_bump=5)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(self.c["robin"].get("/api/profiles/2/data").status_code, 200)   # loads the rebuild
        self.assertIn("settings.json differs from profile 2's settings (sources)", out.getvalue())
        self.assertEqual(self.merged(2)["sources"], {"LAB2": "Renamed in the app"})
        # reset, then another rebuild: still the default, the build doesn't refill it
        self.c["robin"].delete("/api/profiles/2/settings/sources", headers=H)
        write(os.path.join(BUILD, "data.json"), DATA, mtime_bump=10)
        with contextlib.redirect_stdout(io.StringIO()):
            self.c["robin"].get("/api/profiles/2/data")
        self.assertEqual(self.merged(2)["sources"], {})

    def test_c_build_seeds_a_new_linked_profile(self):
        pid = self.fresh_profile("Linked later (fictional)")
        self.put("overview.keyLabs", ["Sodium, Blood"], pid=pid)   # set before it's linked: kept
        write(os.path.join(BUILD, "settings.json"), BUILD_SETTINGS, mtime_bump=20)
        db = dbm.connect()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(prof.link_build(db, pid), {})
        db.close()
        m = self.merged(pid)
        self.assertEqual(m["overview"]["keyLabs"], ["Sodium, Blood"])
        self.assertEqual(m["sources"], BUILD_SETTINGS["sources"])   # unset until now: from the build
        db = dbm.connect()
        self.assertEqual(db.execute("SELECT settings_seeded FROM profiles WHERE id=?", (pid,)).fetchone()[0], 1)
        db.close()

    # ---------------------------------------------------------------- values

    def test_every_example_key_round_trips(self):
        example = flat(EXAMPLE)
        self.assertEqual(set(example) - set(settings.SETTINGS), set(), "every key of settings.example.json is a setting")
        pid = self.fresh_profile()
        for key, value in example.items():
            self.put(key, value, pid=pid)
        self.assertEqual({k: v for k, v in flat(self.merged(pid)).items() if k in example}, example)
        secs = self.c["robin"].get(f"/api/profiles/{pid}/settings/sections").json()
        shown = {s["key"]: s for sec in secs["sections"] for s in sec["settings"]}
        for key, value in example.items():
            self.assertEqual((shown[key]["value"], shown[key]["from"], shown[key]["can_change"]), (value, "profile", True), key)

    def test_defaults(self):
        pid = self.fresh_profile()
        m = self.merged(pid)
        for key, s in settings.SETTINGS.items():
            if not settings.shown_to(key, False):   # (#998: an admin's alone, not given to robin)
                self.assertNotIn(key, flat(m))
                continue
            self.assertEqual(flat(m)[key], s.default, key)
        self.assertEqual(m["overview"]["keyLabs"], settings.KEY_LABS)
        # the page's own fallback (www/js/core.js) says the same as the server
        with open(os.path.join(ROOT, "www", "js", "core.js"), encoding="utf-8") as f:
            js = f.read()
        for n, nick in settings.SETTINGS["labNicknames"].default.items():
            self.assertIn(f'"{n}": "{nick}"', js)

    def test_precedence(self):
        """default, then the install's value, then the profile's, then the user's"""
        pid = self.fresh_profile()
        key = "trends.defaultLabs"
        default = settings.SETTINGS[key].default
        db = dbm.connect()
        try:
            self.assertEqual(self.merged(pid)["trends"]["defaultLabs"], default)
            settings.put(db, key, ["Install"], scope="install")
            self.assertEqual(self.merged(pid)["trends"]["defaultLabs"], ["Install"])
            self.assertEqual(self.merged(1)["trends"]["defaultLabs"], ["Glucose"])   # a profile's own value wins
            settings.put(db, key, ["Profile"], scope="profile", pid=pid)
            self.assertEqual(self.merged(pid)["trends"]["defaultLabs"], ["Profile"])
            settings.put(db, key, ["User"], scope="user", uid=self.uid["robin"])
            self.assertEqual(self.merged(pid)["trends"]["defaultLabs"], ["User"])
            self.assertEqual(self.merged(pid, "lee")["trends"]["defaultLabs"], ["Profile"])   # only for robin
            # resetting the profile's value shows the install's again (for those without their own)
            self.c["robin"].delete(f"/api/profiles/{pid}/settings/{key}", headers=H)
            self.assertEqual(self.merged(pid, "lee")["trends"]["defaultLabs"], ["Install"])
        finally:
            settings.clear(db, [key], "install")
            settings.clear(db, [key], "user", uid=self.uid["robin"])
            db.close()
        self.assertEqual(self.merged(pid)["trends"]["defaultLabs"], default)

    def test_bad_values_refused(self):
        bad = [("visitWorkouts", [{"match": "([unclosed", "name": "X", "minutes": 10}]),
               ("visitWorkouts", [{"match": "(a+)+$", "regex": True, "name": "X", "minutes": 10}]),   # more: test_milestones.py
               ("milestones", [{"date": "2026-02-30", "label": "No such day"}]),
               ("visitWorkouts", [{"match": "x", "name": "X", "minutes": 0}]),
               ("visitWorkouts", [{"match": "x", "name": "X", "minutes": 30.5}]),
               ("visitWorkouts", "not a list"),
               ("overview.keyLabs", "Hemoglobin"),
               ("overview.keyLabs", ["x" * 121]),
               ("overview.keyLabs", ["  "]),
               ("overview.keyLabs", [f"Lab {i}" for i in range(51)]),
               ("labNicknames", ["not", "a", "map"]),
               ("overview.homeTiles", ["bp", "steps"]), ("overview.homeTiles", "bp"),   # #225: home readings as tiles
               ("sources", {"EXT": ""})]
        before = self.merged(1)
        for key, value in bad:
            self.put(key, value, status=400)
        self.assertEqual(self.merged(1), before)
        self.assertEqual(self.c["robin"].put("/api/profiles/1/settings/no.such", json={"value": 1}, headers=H).status_code, 404)
        # what is kept is cleaned: spaces tidied, repeats dropped
        self.assertEqual(self.put("overview.keyLabs", [" Ferritin ", "Ferritin", "Hemo  globin"])["overview"]["keyLabs"],
                         ["Ferritin", "Hemo globin"])
        self.c["robin"].delete("/api/profiles/1/settings/overview.keyLabs", headers=H)
        self.assertEqual(before["overview"]["homeTiles"], [], "no home tiles unless chosen (#225)")
        self.assertEqual(self.put("overview.homeTiles", ["temp", "bp", "temp"])["overview"]["homeTiles"], ["bp", "temp"], "in the list's order")
        self.c["robin"].delete("/api/profiles/1/settings/overview.homeTiles", headers=H)

    # ---------------------------------------------------------------- who may

    def test_scope_permissions(self):
        # profile settings: owners and editors; viewers and strangers can't
        self.put("sources", {"A": "Owner's"}, who="robin")
        self.put("sources", {"A": "Editor's"}, who="lee")
        self.put("sources", {"A": "Viewer's"}, who="kim", status=403)
        self.put("sources", {"A": "Stranger's"}, who="zed", status=404)
        self.assertEqual(self.c["zed"].get("/api/profiles/1/settings").status_code, 404)
        self.assertEqual(self.c["kim"].delete("/api/profiles/1/settings/sources", headers=H).status_code, 403)
        self.assertEqual(self.merged(1, "kim")["sources"], {"A": "Editor's"})   # viewers read them
        # user settings: each person their own, a viewer too, and it's theirs on every profile
        self.put("test.mine", "dark", who="kim")
        self.assertEqual(self.merged(1, "kim")["test"]["mine"], "dark")
        self.assertEqual(self.merged(2, "kim")["test"]["mine"], "dark")
        self.assertEqual(self.merged(1, "robin")["test"]["mine"], "light")
        # install settings: admins only
        self.put("test.server", 20, who="robin", status=403)
        self.put("test.server", 30, who="ada")
        self.assertEqual(self.merged(1, "ada")["test"]["server"], 30)
        self.assertNotIn("server", self.merged(1, "robin")["test"])   # (#998: an admin's alone)
        secs = {s["key"]: s for sec in self.c["kim"].get("/api/profiles/1/settings/sections").json()["sections"] for s in sec["settings"]}
        self.assertEqual((secs["sources"]["can_change"], secs["test.mine"]["can_change"], "test.server" in secs), (False, True, False))
        # the CSRF check, as for every change
        self.assertEqual(self.c["robin"].put("/api/profiles/1/settings/sources", json={"value": {}}).status_code, 403)
        # logged without the values
        log = self.c["robin"].get("/api/profiles/1/activity?action=settings").json()
        self.assertEqual((log[0]["actor"], log[0]["detail"]), ("lee", {"setting": "sources", "label": "Result sources"}))
        self.assertNotIn("Editor's", json.dumps(log))
        acct = {e["action"]: e["detail"] for e in reversed(self.c["ada"].get("/api/activity").json())}
        self.assertEqual((acct["settings.user"], acct["settings.install"]),   # (#1006: an install setting's with its values)
                         ({"setting": "test.mine", "label": "Just mine"}, {"setting": "test.server", "label": "Server-wide", "from": 10, "to": 30}))
        for who, key in (("kim", "test.mine"), ("ada", "test.server")):
            self.c[who].delete(f"/api/profiles/1/settings/{key}", headers=H)
        self.c["robin"].delete("/api/profiles/1/settings/sources", headers=H)

    def test_install_settings_without_a_profile(self):
        """#167: the Admin page reads and changes the install's own settings (the sync defaults) without naming a
        profile: an admin may have none"""
        ada = self.c["ada"]
        got = ada.get("/api/settings/install").json()["sections"]
        sync = next(s for s in got if s["id"] == "sync")
        self.assertEqual(sync["title"], "Sync defaults")
        self.assertTrue(all(st["scope"] == "install" and st["can_change"] and st["from"] in ("install", "default")
                            for sec in got for st in sec["settings"]), got)   # (#1158: PUBLIC_ORIGINS, set above, taken in)
        self.assertEqual({st["key"]: st["from"] for st in sync["settings"]}["sync.staleDays"], "default")
        self.assertNotIn("names", {s["id"] for s in got})   # only the install's: no profile's or person's settings
        # only admins, and changes pass the CSRF check
        for who in ("robin", "kim"):
            self.assertEqual(self.c[who].get("/api/settings/install").status_code, 403)
            self.assertEqual(self.c[who].put("/api/settings/install/sync.staleDays", json={"value": 9}, headers=H).status_code, 403)
        self.assertEqual(ada.put("/api/settings/install/sync.staleDays", json={"value": 9}).status_code, 403)   # no X-Sanguity
        # changed, checked, refused for a setting of another scope, and logged as the server's
        r = ada.put("/api/settings/install/sync.staleDays", json={"value": 9}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        st = {s["key"]: s for s in next(x for x in r.json()["sections"] if x["id"] == "sync")["settings"]}["sync.staleDays"]
        self.assertEqual((st["value"], st["from"]), (9, "install"))
        self.assertEqual(self.merged(1, "robin")["sync"]["staleDays"], 9)   # every profile sees it
        self.assertEqual(ada.put("/api/settings/install/sync.staleDays", json={"value": 0}, headers=H).status_code, 400)
        self.assertEqual(ada.put("/api/settings/install/labNicknames", json={"value": {}}, headers=H).status_code, 404)
        self.assertEqual(ada.put("/api/settings/install/no.such", json={"value": 1}, headers=H).status_code, 404)
        acct = [e for e in self.c["ada"].get("/api/activity").json() if e["action"] == "settings.install"]
        self.assertEqual((acct[0]["actor"], acct[0]["detail"]), ("ada", {"setting": "sync.staleDays", "label": "Warn when a connection hasn't synced for (days)",
                                                                       "from": 3, "to": 9}))   # (#1006: its values)
        # Reset to defaults: the section's install values go
        self.assertEqual(ada.post("/api/settings/install/reset", json={"section": "names"}, headers=H).status_code, 404)
        r = ada.post("/api/settings/install/reset", json={"section": "sync"}, headers=H)
        self.assertEqual((r.status_code, r.json()["reset"]), (200, ["sync.staleDays"]), r.text)
        self.assertEqual(self.merged(1, "robin")["sync"]["staleDays"], 3)

    def test_a_value_only_in_the_process_environment_is_taken_in_once(self):
        """#1158: what an upgrade takes in is the process's environment, whatever put it there (compose's env_file or
        environment, docker run -e, the TrueNAS app's fields, Unraid's): no .env file is read. Taken in once, then a
        change to it is only warned about. (SESSION_HOURS_WITHOUT_2FA=0 too: SESSION_DAYS for everyone, as it meant.)"""
        from server import settings_import
        with contextlib.closing(dbm.connect()) as db:
            mark = dbm.meta_get(db, settings_import.MARK)
            try:
                dbm.meta_set(db, settings_import.MARK, None)   # as before this version
                with mock.patch.dict(os.environ, {"SESSION_IDLE_MINUTES": "45", "SESSION_HOURS_WITHOUT_2FA": "0"}), \
                        mock.patch("builtins.open", side_effect=lambda *a, **k: (_ for _ in ()).throw(AssertionError(f"read {a[0]}"))
                                   if str(a[0]).endswith(".env") else OPEN(*a, **k)):
                    taken = settings_import.take(db, say=lambda *a: None)
                    settings.forget_saved()
                    self.assertTrue({"SESSION_IDLE_MINUTES", "SESSION_HOURS_WITHOUT_2FA"} <= set(taken), taken)
                    self.assertEqual((config.setting("SESSION_IDLE_MINUTES"), config.setting("SESSION_HOURS_WITHOUT_2FA")), (45, 0))
                with mock.patch.dict(os.environ, {"SESSION_IDLE_MINUTES": "90"}):
                    said = []
                    self.assertEqual(settings_import.take(db, say=said.append), [], "once")
                    self.assertEqual(config.setting("SESSION_IDLE_MINUTES"), 45)
                    self.assertTrue(any("SESSION_IDLE_MINUTES is set in the environment" in s for s in said), said)
            finally:
                settings.clear(db, ["security.idleMinutes", "security.sessionHours"], "install")
                dbm.meta_set(db, settings_import.MARK, mark)
                settings.forget_saved()

    def test_the_environment_is_taken_in_once(self):
        """#1158 (in place of #999's "the environment wins"): the first start took what the environment set into the
        settings (PUBLIC_ORIGINS, set above), logged in Install activity by start-up and said once on General; after
        that a variable set in the environment changes nothing (each is changed here, at once, and only here), and one
        changed since it was taken in is warned about at each start, naming it. The container's own (RECEIVER_PORT)
        stay the environment's."""
        from server import settings_import, sync
        ada, key = self.c["ada"], "testEnv.syncHours"
        item = lambda: {st["key"]: st for sec in ada.get("/api/settings/install").json()["sections"] for st in sec["settings"]}[key]   # noqa: E731
        hours = lambda: round((sync._due_after_failure(100) - dbm.now()) / 3600)   # noqa: E731  (a pull's wait, at most SYNC_HOURS)
        got = ada.get("/api/settings/install").json()
        self.assertEqual(set(got["variables"]), set(config.VARS))
        self.assertEqual(got["variables"]["PUBLIC_ORIGINS"]["from"], "install")
        self.assertEqual({k: got["variables"]["RECEIVER_PORT"].get(k) for k in ("value", "from", "container")}, {"value": 8731, "from": "environment", "container": True})
        self.assertIn({"name": "PUBLIC_ORIGINS", "label": "Public addresses"}, got["taken"]["names"])
        with contextlib.closing(dbm.connect()) as db:
            logged = db.execute("SELECT COUNT(*) FROM activity WHERE action='settings.install' AND actor='start-up'").fetchone()[0]
        self.assertGreaterEqual(logged, 1, "logged by start-up (once here; the test above takes in once more)")
        self.assertEqual(ada.post("/api/settings/install/taken/seen", headers=H).status_code, 200)
        self.assertIsNone(ada.get("/api/settings/install").json()["taken"], "said once")
        with mock.patch.dict(os.environ, {"SYNC_HOURS": "24", "PUBLIC_ORIGINS": "https://health.example.test"}):
            st = item()
            self.assertEqual((st["value"], st["from"], st["env"], st["can_change"], hours()), (12, "default", "SYNC_HOURS", True, 12))
            r = ada.put(f"/api/settings/install/{key}", json={"value": 6}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual((config.setting("SYNC_HOURS"), config.source("SYNC_HOURS"), hours()), (6, "install", 6))
            said = []   # a later start: nothing taken in again; the one changed since is warned about, the one that isn't not
            with contextlib.closing(dbm.connect()) as db:
                self.assertEqual(settings_import.take(db, say=said.append), [])
            self.assertTrue(any(s.startswith("Warning: SYNC_HOURS is set in the environment") and "ignored" in s for s in said), said)
            self.assertFalse(any("PUBLIC_ORIGINS" in s for s in said), said)
            self.assertEqual(ada.delete(f"/api/profiles/1/settings/{key}", headers=H).status_code, 200)
            self.assertEqual((config.setting("SYNC_HOURS"), item()["from"], hours()), (12, "default", 12))
        self.assertEqual(config.unreadable(), [])
        with mock.patch.dict(os.environ, {"RECEIVER_PORT": "ninety"}):
            self.assertEqual(config.unreadable(), [("RECEIVER_PORT", "ninety")])
        # the registry keeps a variable's setting to what the variable can be: the install's, plain, its default
        for kw, why in (({"env": "NO_SUCH_VARIABLE"}, "isn't a variable"), ({"env": "SYNC_HOURS"}, "another setting"),
                        ({"env": "SHL_TIMEOUT", "private": True}, "private=False"), ({"env": "SHL_TIMEOUT", "default": 5}, "its default")):
            with self.assertRaises(ValueError) as e:
                settings.define("test.bad", "testing-env", "Bad", kw.pop("default", 20), settings.whole(1, 99, "It"),
                                scope="install", **{"private": False, **kw})
            self.assertIn(why, str(e.exception))
        self.assertNotIn("test.bad", settings.SETTINGS)

    def stale(self, who="ada"):
        """this person's sign-in more than REAUTH_MINUTES old, and no fresh check since"""
        with contextlib.closing(dbm.connect()) as db:
            db.execute("UPDATE sessions SET verified_at=0 WHERE user_id=?", (self.uid[who],))

    def install_log(self):
        return [e for e in self.c["ada"].get("/api/activity").json() if e["action"] == "settings.install"]

    def test_install_changes_need_the_fresh_check_and_are_logged(self):
        """#1006: every change to an install setting asks for the admin's fresh check (403 reauth_required), on the
        install's route and a profile page's, a reset of one with a value saved, and the two-factor rule; one that
        changes nothing doesn't. Each is in the install log with who, what, the old value and the new (none a secret);
        one that could hold health information, by name only."""
        ada, key = self.c["ada"], "sync.staleDays"

        def held():
            with contextlib.closing(dbm.connect()) as db:
                return settings.install_values(db, "sync.")[key]
        self.stale()
        refused = [ada.put(f"/api/settings/install/{key}", json={"value": 9}, headers=H),
                   ada.put(f"/api/profiles/1/settings/{key}", json={"value": 9}, headers=H),
                   ada.patch("/api/settings", json={"require_2fa": True}, headers=H)]
        self.assertEqual([(r.status_code, r.json()["detail"]) for r in refused], [(403, "reauth_required")] * 3)
        self.assertEqual((held(), self.c["ada"].get("/api/settings").json()["require_2fa"]), (3, False))
        self.assertEqual(ada.put(f"/api/settings/install/{key}", json={"value": 3}, headers=H).status_code, 200, "no change: not asked")
        self.assertEqual(ada.post("/api/settings/install/reset", json={"section": "sync"}, headers=H).json()["reset"], [], "nothing saved: not asked")
        self.assertEqual(ada.put(f"/api/settings/install/{key}", json={"value": 0}, headers=H).status_code, 400, "a bad value says so first")
        before = len(self.install_log())
        # the fresh check: then it's saved, and logged with its old value and its new
        self.assertEqual(ada.post("/api/me/reauth", json={"secret": PW["ada"]}, headers=H).status_code, 200)
        self.assertEqual(ada.put(f"/api/settings/install/{key}", json={"value": 9}, headers=H).status_code, 200)
        self.assertEqual(ada.put(f"/api/profiles/1/settings/{key}", json={"value": 11}, headers=H).status_code, 200)
        log = self.install_log()
        self.assertEqual(len(log), before + 2)
        label = settings.SETTINGS[key].label
        self.assertEqual([(e["actor"], e["detail"]) for e in log[:2]],
                         [("ada", {"setting": key, "label": label, "from": 9, "to": 11}), ("ada", {"setting": key, "label": label, "from": 3, "to": 9})])
        # a reset of one, or of the section, with a value saved: asked for, and logged with what it held
        self.stale()
        self.assertEqual(ada.delete(f"/api/profiles/1/settings/{key}", headers=H).json()["detail"], "reauth_required")
        self.assertEqual(ada.post("/api/settings/install/reset", json={"section": "sync"}, headers=H).json()["detail"], "reauth_required")
        self.assertEqual(ada.post("/api/profiles/1/settings/reset", json={"section": "sync"}, headers=H).json()["detail"], "reauth_required")
        self.assertEqual(held(), 11)
        ada.post("/api/me/reauth", json={"secret": PW["ada"]}, headers=H)
        self.assertEqual(ada.delete(f"/api/profiles/1/settings/{key}", headers=H).status_code, 200)
        self.assertEqual(self.install_log()[0]["detail"], {"setting": key, "label": label, "from": 11, "to": 3, "reset": True})
        ada.put(f"/api/settings/install/{key}", json={"value": 5}, headers=H)
        r = ada.post("/api/settings/install/reset", json={"section": "sync"}, headers=H)
        self.assertEqual(r.json()["reset"], [key])
        self.assertEqual(self.install_log()[0]["detail"], {"section": "sync", "label": "Sync defaults", "settings": [key], "reset": True,
                                                           "changes": [{"setting": key, "label": label, "from": 5, "to": 3}]})
        # the two-factor rule too (turned on and off again: everyone else has none)
        self.stale()
        self.assertEqual(ada.patch("/api/settings", json={"require_2fa": False}, headers=H).status_code, 200, "no change: not asked")
        ada.post("/api/me/reauth", json={"secret": PW["ada"]}, headers=H)
        self.assertEqual(ada.patch("/api/settings", json={"require_2fa": True}, headers=H).status_code, 200)
        self.assertEqual(ada.patch("/api/settings", json={"require_2fa": False}, headers=H).status_code, 200)
        # one that could hold health information: by name only
        self.assertEqual(ada.put("/api/settings/install/testEnv.sealed", json={"value": "fictional words"}, headers=H).status_code, 200)
        self.assertEqual(self.install_log()[0]["detail"], {"setting": "testEnv.sealed", "label": "Sealed"})
        self.assertNotIn("fictional words", json.dumps(self.install_log()))
        ada.delete("/api/profiles/1/settings/testEnv.sealed", headers=H)
        self.assertNotIn("fictional words", json.dumps(self.install_log()))

    def test_a_saved_settings_read_error_fails_closed(self):   # #1046
        """A read of the saved server settings that fails (busy past its timeout, the database briefly missing) gave
        none for SAVED_SECONDS: the defaults, the most lenient bounds and profiles for everyone. It keeps the values
        read before; with none read yet, Security's permissions and limits are at their strictest (profiles admins
        only), and the sign-ins' lengths and idle sign-out at their defaults: none is signed out."""
        import sqlite3
        keys = ("security.profiles", "security.passwordMin2fa")
        with contextlib.closing(dbm.connect()) as db:   # an admin's stricter values, saved
            for key, value in zip(keys, ("admins", 12)):
                db.execute("INSERT INTO settings(key, value, changed) VALUES(?, ?, 0)", (key, json.dumps(value)))

        def clear():
            with contextlib.closing(dbm.connect()) as db:
                db.execute(f"DELETE FROM settings WHERE key IN (?, ?) AND {settings.INSTALL_ROWS}", keys)
            settings.forget_saved()
        self.addCleanup(clear)
        settings.forget_saved()
        busy = mock.patch.object(settings.sqlite3, "connect", side_effect=sqlite3.OperationalError("database is locked"))
        with mock.patch.object(settings, "SAVED_SECONDS", 0):
            read = settings.saved_values()
            self.assertEqual((read["security.profiles"], config.setting("PASSWORD_MIN_LENGTH_2FA")), ("admins", 12))
            with busy:
                self.assertEqual(settings.saved_values(), read, "the values read before")
                self.assertEqual(config.setting("PASSWORD_MIN_LENGTH_2FA"), 12)
                missing = mock.patch.object(settings.os.path, "exists", return_value=False)
                with missing:
                    self.assertEqual(settings.saved_values(), read, "the database briefly missing: the same")
                with mock.patch.dict(settings._saved, good=None):   # none read yet
                    got = settings.saved_values()
                    self.assertEqual(got["security.profiles"], "admins")
                    self.assertEqual((config.setting("PASSWORD_MIN_LENGTH_2FA"), config.setting("REAUTH_MINUTES"),
                                      settings.install_value("security.triesAccount")), (64, 1, 10))
                    self.assertEqual((config.setting("SESSION_DAYS"), config.setting("SESSION_HOURS_WITHOUT_2FA"),
                                      config.setting("SESSION_IDLE_MINUTES")), (30, 12, 0), "sign-ins' lengths: the defaults, ending none")
                with mock.patch.dict(settings._saved, good=None), missing:   # no database at all: nothing saved
                    self.assertEqual(settings.saved_values(), {})
        settings.forget_saved()

    def test_command_line_shows_and_resets_server_settings(self):
        """#1006: python -m server.manage config show lists every server setting, its value and where it comes from;
        config reset SETTING (its variable or its key) and --all take away what's saved in the app, logged as the
        command line's; the running app reads it again within SAVED_SECONDS (here at once), with no restart."""
        from server import manage
        ada = self.c["ada"]
        ada.post("/api/me/reauth", json={"secret": PW["ada"]}, headers=H)
        for key, value in (("testEnv.syncHours", 6), ("sync.staleDays", 5)):
            self.assertEqual(ada.put(f"/api/settings/install/{key}", json={"value": value}, headers=H).status_code, 200)

        def run(*args):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                manage.run(["config", *args])
            return out.getvalue()
        shown = {line.split()[0]: line for line in run("show").splitlines()}
        self.assertRegex(shown["SYNC_HOURS"], r"^SYNC_HOURS \(testEnv\.syncHours\)\s+6\s+saved in the app$")
        self.assertRegex(shown["SESSION_DAYS"], r"30\s+the default$")
        self.assertRegex(shown["PUBLIC_ORIGINS"], r"https://health\.example\.test\s+saved in the app$")   # (taken in, #1158)
        self.assertRegex(shown["RECEIVER_PORT"], r"8731\s+set by the container$")
        self.assertRegex(shown["sync.staleDays"], r"5\s+saved in the app$")
        self.assertEqual(set(config.VARS) - set(shown), set(), "every variable")
        with mock.patch.dict(os.environ, {"SYNC_HOURS": "24"}):   # (#1158) the environment's isn't in force
            self.assertRegex(run("show"), r"(?m)^SYNC_HOURS \(testEnv\.syncHours\)\s+6\s+saved in the app$")
        # one by its variable, one by its key
        with mock.patch.object(settings, "SAVED_SECONDS", 0):   # (as the running app sees it a moment later)
            said = run("reset", "SYNC_HOURS")
            self.assertIn("SYNC_HOURS: the value saved in the app (6) is gone; it's 12 now, the default.", said)
            self.assertEqual((config.setting("SYNC_HOURS"), config.source("SYNC_HOURS")), (12, "default"))
        self.assertIn("Nothing to reset: testEnv.syncHours has no value saved in the app.", run("reset", "testEnv.syncHours"))
        self.assertIn("nothing to reset", run("reset", "SHL_TIMEOUT"))   # a variable no setting stands for
        with self.assertRaises(SystemExit) as e:
            run("reset", "no.such")
        self.assertIn("no server setting called 'no.such'", str(e.exception))
        log = self.install_log()[0]
        self.assertEqual((log["actor"], log["detail"]), ("command line", {"setting": "testEnv.syncHours", "label": "Sync every (hours)",
                                                                         "reset": True, "from": 6, "to": 12}))
        # all of them
        ada.put("/api/settings/install/testEnv.syncHours", json={"value": 7}, headers=H)
        said = run("reset", "--all")
        self.assertIn("sync.staleDays: the value saved in the app (5) is gone; it's 3 now, the default.", said)
        self.assertIn("SYNC_HOURS: the value saved in the app (7) is gone", said)
        with contextlib.closing(dbm.connect()) as db:
            self.assertEqual(settings.stored(db, "install"), {})
        self.assertIn("Nothing to reset: no setting has a value saved in the app.", run("reset", "--all"))
        self.assertEqual(ada.get("/api/settings/install").json()["sections"][0]["settings"][0]["from"], "default")
        # (#1158) config set NAME VALUE: the page's checks, saved, logged by the command line, in force at once
        with mock.patch.object(settings, "SAVED_SECONDS", 0):
            self.assertIn("PUBLIC_ORIGINS: https://health.example.test, saved in the app", run("set", "PUBLIC_ORIGINS", "https://health.example.test/"))
            self.assertEqual(config.setting("PUBLIC_ORIGINS"), ("https://health.example.test",))
            run("set", "sync.staleDays", "6")
            self.assertEqual(self.merged(1, "robin")["sync"]["staleDays"], 6)
            run("reset", "sync.staleDays")
            for bad in (("SESSION_DAYS", "90"), ("COOKIE_SECURE", "sometimes"), ("RECEIVER_PORT", "9000"), ("NO_SUCH", "1")):
                with self.assertRaises(SystemExit, msg=bad):
                    run("set", *bad)
        self.assertEqual(self.install_log()[0]["actor"], "command line")

    def test_saved_settings_off_lets_a_locked_out_install_back_in(self):
        """#1006: a value saved in the app that ends sign-ins (here 1 day, Sign-in rules's shortest, #1001: Zed's two
        days old) keeps people out; SANGUITY_SAVED_SETTINGS=off leaves saved values out, so they're in again and an admin
        puts it right; server.manage config reset does too, without the page."""
        key = "security.sessionDays"

        def signed_in():   # a sign-in two days old, with a second factor (so not ended at 12 hours), then a request
            c = self.login("zed")
            with contextlib.closing(dbm.connect()) as db:
                db.execute("UPDATE users SET totp_secret=? WHERE id=?", (crypto.enc("JBSWY3DPEHPK3PXP"), self.uid["zed"]))
                db.execute("UPDATE sessions SET created=created-2*86400 WHERE id=(SELECT MAX(id) FROM sessions WHERE user_id=?)", (self.uid["zed"],))
            try:
                return c.get("/api/me").status_code
            finally:
                with contextlib.closing(dbm.connect()) as db:
                    db.execute("UPDATE users SET totp_secret=NULL WHERE id=?", (self.uid["zed"],))

        def lock():   # as if saved by mistake
            with contextlib.closing(dbm.connect()) as db:
                settings.put(db, key, 1)
        self.assertEqual(signed_in(), 200)
        lock()
        self.assertEqual(signed_in(), 401, "locked out: every sign-in ends at once")
        with mock.patch.dict(os.environ, {"SANGUITY_SAVED_SETTINGS": "off"}):
            self.assertEqual(signed_in(), 200, "saved values left out")
            got = self.c["ada"].get("/api/settings/install").json()
            st = {s["key"]: s for sec in got["sections"] for s in sec["settings"]}[key]
            self.assertEqual((got["saved_ignored"], st["value"], st["from"], st["saved"], got["variables"]["SESSION_DAYS"]["from"]),
                             (True, 30, "default", 1, "default"))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main.say_settings()   # what the app's start says
            self.assertIn("SANGUITY_SAVED_SETTINGS=off: the server's settings saved in the app are left out", out.getvalue())
            self.c["ada"].post("/api/me/reauth", json={"secret": PW["ada"]}, headers=H)
            self.assertEqual(self.c["ada"].put(f"/api/settings/install/{key}", json={"value": 30}, headers=H).status_code, 200, "put right")
        self.assertEqual(signed_in(), 200, "and with it taken out again")
        # without the page: the command line
        from server import manage
        lock()
        self.assertEqual(signed_in(), 401)
        with contextlib.redirect_stdout(io.StringIO()):
            manage.run(["config", "reset", "SESSION_DAYS"])
        self.assertEqual(signed_in(), 200)

    # ---------------------------------------------------------------- Sign-in rules (#1001)

    def security_put(self, key, value, status=200, confirm=True):
        """confirm: what's asked to be typed is typed (#998's one rule), or nothing is (False)"""
        put = lambda typed=None: self.c["ada"].put(f"/api/settings/install/{key}", json={"value": value, "confirm": typed}, headers=H)   # noqa: E731
        r = put()
        if confirm and r.status_code == 409 and isinstance(r.json()["detail"], dict) and "confirm" in r.json()["detail"]:
            r = put(r.json()["detail"]["confirm"]["type"])
        self.assertEqual(r.status_code, status, f"{key} = {value!r}: {r.text}")
        return r

    def security_reset(self):
        self.c["ada"].post("/api/me/reauth", json={"secret": PW["ada"]}, headers=H)
        self.assertEqual(self.c["ada"].post("/api/settings/install/reset", json={"section": "security"}, headers=H).status_code, 200)

    def test_security_settings_are_bounded_never_more_lenient(self):
        """#1001: each of Sign-in rules's settings takes the values from its least to its most, Sanguity's own
        default never exceeded (#998), and refuses one past either (400), saying its bounds; the page is given them. A
        variable set in the environment (PASSWORD_MIN_LENGTH, here) changes nothing (#1158)."""
        ada = self.c["ada"]
        ada.post("/api/me/reauth", json={"secret": PW["ada"]}, headers=H)
        bounds = {"security.sessionDays": (1, 30), "security.sessionHours": (0, 12), "security.idleMinutes": (5, 1440),
                  "security.passwordMin": (15, 64), "security.passwordMin2fa": (8, 64), "security.tempPasswordDays": (1, 7),
                  "security.reauthMinutes": (1, 5), "security.triesHere": (3, 5), "security.triesAddress": (10, 20),
                  "security.triesAccount": (10, 30)}
        try:
            with mock.patch.dict(os.environ):
                os.environ.pop("PASSWORD_MIN_LENGTH")
                got = {st["key"]: st for sec in ada.get("/api/settings/install").json()["sections"] if sec["id"] == "security"
                       for st in sec["settings"]}
                self.assertEqual({k: (got[k]["min"], got[k]["max"], got[k]["default"]) for k in bounds},
                                 {**{k: (lo, hi, hi) for k, (lo, hi) in bounds.items()}, "security.idleMinutes": (0, 1440, 0),
                                  "security.passwordMin": (15, 64, 15), "security.passwordMin2fa": (8, 64, 8)})
                for key, (lo, hi) in bounds.items():
                    for v in (lo - 1, hi + 1, "10", 2.5, True):
                        if key == "security.idleMinutes" and v == lo - 1:
                            v = 1   # (0 is off; 1 to 4 aren't)
                        self.assertIn("must be", self.security_put(key, v, 400).json()["detail"])
                    for v in (lo, hi):
                        self.security_put(key, v)
                self.security_put("security.idleMinutes", 0)
                for v in ("nobody", ["admins"], None):
                    self.security_put("security.profiles", v, 400)
                self.assertEqual(got["security.profiles"]["choices"], {"everyone": "Everyone signed in", "admins": "Admins only"})
            st = {s["key"]: s for sec in ada.get("/api/settings/install").json()["sections"] for s in sec["settings"]}["security.passwordMin"]
            self.assertEqual((st["can_change"], st["env"]), (True, "PASSWORD_MIN_LENGTH"))
            self.security_put("security.passwordMin", 20)
        finally:
            self.security_reset()

    def test_security_settings_apply_at_once(self):
        """#1001: each of Sign-in rules's settings reaches what it governs at once, without a restart: a sign-in's
        lengths and idle sign-out (You → Sign-in and security is told them), who may create profiles (the server
        refuses the rest), the shortest passwords, a one-time password's days, the fresh check's minutes, and the
        sign-in throttle's free tries."""
        from server import security as sec
        ada = self.c["ada"]
        ada.post("/api/me/reauth", json={"secret": PW["ada"]}, headers=H)
        try:
            # who may create profiles: admins only, then everyone again
            self.security_put("security.profiles", "admins")
            r = self.c["robin"].post("/api/profiles", json={"name": "Not made (fictional)"}, headers=H)
            self.assertEqual((r.status_code, "only an admin" in r.json()["detail"]), (403, True), r.text)
            self.assertEqual((self.c["robin"].get("/api/me").json()["may_create_profiles"], ada.get("/api/me").json()["may_create_profiles"]), (False, True))
            made = ada.post("/api/profiles", json={"name": "Made by an admin (fictional)"}, headers=H)
            self.assertEqual(made.status_code, 200, made.text)
            ada.request("DELETE", f"/api/profiles/{made.json()['id']}", json={"confirm_name": "Made by an admin (fictional)"}, headers=H)
            self.security_put("security.profiles", "everyone")
            self.assertTrue(self.c["robin"].get("/api/me").json()["may_create_profiles"])
            # the lengths, as You → Sign-in and security and the sign-in page say them
            self.security_put("security.sessionDays", 10)
            self.security_put("security.idleMinutes", 60)
            self.assertEqual((ada.get("/api/me").json()["session"], client().get("/api/setup").json()["session"]),
                             ({"days": 10, "hours_without_2fa": 12, "idle_minutes": 60},) * 2)
            # the shortest password: asked of the next one set
            self.security_put("security.passwordMin2fa", 20)
            with mock.patch.dict(os.environ):
                os.environ.pop("PASSWORD_MIN_LENGTH")
                self.security_put("security.passwordMin", 25)
                zed = self.login("zed")
                self.assertEqual(zed.get("/api/me").json()["password_min"], 25)
                r = zed.post("/api/me/password", json={"current": PW["zed"], "new": "twenty one characters"}, headers=H)
                self.assertEqual(r.status_code, 400, r.text)
                self.assertIn("25", r.json()["detail"])
            self.assertEqual(ada.get("/api/me").json()["password_min"], 20)   # (Ada's: with two-factor sign-in on)
            # a one-time password's days, from those given now
            self.security_put("security.tempPasswordDays", 3)
            self.assertAlmostEqual(accounts.temp_expiry() - dbm.now(), 3 * 86400, delta=2)
            # the fresh check: given 2 minutes ago, with 1 minute it's asked again
            self.security_put("security.reauthMinutes", 1)
            with contextlib.closing(dbm.connect()) as db:
                db.execute("UPDATE sessions SET verified_at=? WHERE user_id=?", (dbm.now() - 120, self.uid["ada"]))
            self.assertEqual(self.security_put("security.triesHere", 3, 403).json()["detail"], "reauth_required")
            self.assertEqual(ada.get("/api/me").json()["reauth_minutes"], 1)
            ada.post("/api/me/reauth", json={"secret": PW["ada"]}, headers=H)
            # the throttle's free tries: 3 wrong ones from here, then a wait (5 before)
            keys = sec.login_keys("nobody1001", "testclient")
            sec.throttle.clear(*keys)
            tries = lambda: [client().post("/api/login", json={"username": "nobody1001", "password": "not a password here"}, headers=H).status_code  # noqa: E731
                             for _ in range(4)]
            self.assertEqual(tries(), [401] * 4)
            sec.throttle.clear(*keys)
            self.security_put("security.triesHere", 3)
            self.assertEqual(tries(), [401] * 3 + [429])
            sec.throttle.clear(*keys)
        finally:
            self.security_reset()
        self.assertEqual(self.c["ada"].get("/api/me").json()["session"], {"days": 30, "hours_without_2fa": 12, "idle_minutes": 0})

    def test_a_shorter_sign_in_counts_the_ones_it_ends_and_asks(self):
        """#1001: shortening a sign-in, or turning idle sign-out on, would end sign-ins: refused (409) with how many (and
        whether yours is one) until sent again confirmed; then they end at their next request, and the rest go on. A
        change that ends none isn't asked."""
        ada, who = self.c["ada"], self.uid["zed"]
        ada.post("/api/me/reauth", json={"secret": PW["ada"]}, headers=H)
        old, new = self.login("zed"), self.login("zed")
        with contextlib.closing(dbm.connect()) as db:
            db.execute("UPDATE sessions SET last_seen=?", (dbm.now(),))
            aged = db.execute("SELECT MIN(id) FROM sessions WHERE user_id=? AND id IN (SELECT id FROM sessions ORDER BY id DESC LIMIT 2)", (who,)).fetchone()[0]
            db.execute("UPDATE sessions SET created=created-3*3600, last_seen=last_seen-20*60 WHERE id=?", (aged,))
            self.assertEqual(main.sessions_ended(db, {"SESSION_HOURS_WITHOUT_2FA": 2}, aged), (1, True))
        try:
            r = self.security_put("security.sessionHours", 2, 409, confirm=False)
            c = r.json()["detail"]["confirm"]   # (#998: asked as every install setting's change is, the count typed back)
            self.assertEqual((r.json()["detail"]["code"], c["count"], c["type"], c["yours"]), ("confirm", 1, "1", False))
            self.assertEqual(r.json()["detail"]["message"], "1 sign-in is older, or unused for longer, than this change allows: it "
                                                           "ends at its next request, and its person signs in again. Type 1 to confirm.")
            self.assertEqual(self.c["ada"].put("/api/settings/install/security.sessionHours", json={"value": 2, "confirm": "2"},
                                               headers=H).status_code, 409, "a wrong count")
            self.assertEqual((old.get("/api/me").status_code, new.get("/api/me").status_code), (200, 200), "not yet")
            self.stale()
            self.assertEqual(self.security_put("security.sessionHours", 2, 403).json()["detail"], "reauth_required", "then the fresh check")
            ada.post("/api/me/reauth", json={"secret": PW["ada"]}, headers=H)
            self.security_put("security.sessionHours", 2)
            self.assertEqual((old.get("/api/me").status_code, new.get("/api/me").status_code), (401, 200))
            # idle sign-out: one unused for 20 minutes
            old = self.login("zed")
            with contextlib.closing(dbm.connect()) as db:
                db.execute("UPDATE sessions SET last_seen=?", (dbm.now(),))
                db.execute("UPDATE sessions SET last_seen=last_seen-20*60 WHERE id=(SELECT MAX(id) FROM sessions)")
            self.assertEqual(self.security_put("security.idleMinutes", 15, 409, confirm=False).json()["detail"]["confirm"]["count"], 1)
            self.security_put("security.idleMinutes", 30, confirm=False)   # ends none: not asked
            self.security_put("security.idleMinutes", 15)
            self.assertEqual((old.get("/api/me").status_code, new.get("/api/me").status_code), (401, 200))
            self.security_put("security.sessionDays", 20, confirm=False)   # none is that old
        finally:
            self.security_reset()

    def test_every_variable_a_page_names_is_one_compose_reads(self):
        """#1008, #1158: a variable a setting's help names, or any text the app shows sends someone to .env for ("NAME in
        .env"), is one the container sets (config.CONTAINER) or an install may manage there (config.MANAGED), never a
        page setting; one sent to .env for is one both compose files read from .env (${NAME:-...}). (#1008: the help said
        SYNC_MAX_ATTACHMENT_MB in .env, which no compose file passed; #1158: it's the page's now, and the help says where.)"""
        import glob, re
        var = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
        named = {}
        for d in settings.SETTINGS.values():
            for v in var.findall(d.public()["help"] or ""):
                named[v] = f"{d.key}'s help"
        for sid, _, help in settings.SECTIONS:
            for v in var.findall(help or ""):
                named[v] = f"section {sid}'s help"
        for path in glob.glob(os.path.join(ROOT, "server", "*.py")) + glob.glob(os.path.join(ROOT, "www", "js", "*.js")) + glob.glob(os.path.join(ROOT, "www", "*.html")):
            with open(path, encoding="utf-8") as f:
                for m in re.finditer(r"\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\b[^.\n]{0,12}\bin \.env\b", f.read()):
                    named[m.group(1)] = os.path.relpath(path, ROOT)
        self.assertIn("Needs a restart", settings.SETTINGS["sync.attachMaxMB"].public()["help"])
        composed = set()   # (the compose files' own: WEB_PORT, BACKUP_DIR... the container's, read by compose itself)
        for name in ("compose.yaml", "compose.release.yaml"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as f:
                composed |= set(re.findall(r"\$\{(\w+):-", f.read()))
        page = {v: where for v, where in named.items()
                if v not in config.CONTAINER and v not in composed and v.removesuffix("_FILE") not in config.MANAGED}
        self.assertEqual(page, {}, "a page setting named as if it were set in .env")
        dotenv = {v: where for v, where in named.items() if (v in config.CONTAINER or v in composed) and not where.endswith("help")}
        for name in ("compose.yaml", "compose.release.yaml"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as f:
                text = f.read()
            missing = {v: where for v, where in dotenv.items() if "${" + v + ":-" not in text}
            self.assertEqual(missing, {}, f"named, but {name} doesn't read it from .env")

    def test_a_whole_value_saved_from_an_old_read_is_refused(self):   # #923
        """A list a page saves whole, sent with the value it read ("was", its version): refused (409) and left as it is
        when it's changed since, in another tab or by someone else, so that change isn't lost under it; saved when it
        hasn't. A profile's (the symptom groups, two people), a person's own (Units, two tabs) and the install's (the
        attachment kinds, an admin). Sent without it, saved as before."""
        pid = self.fresh_profile("Whole values (fictional)")
        url = f"/api/profiles/{pid}/settings/journal.groups"
        put = lambda who, value, **kw: self.c[who].put(url, json={"value": value, **kw}, headers=H)   # noqa: E731
        migraine, asthma = {"name": "Migraine day", "symptoms": ["Headache"]}, {"name": "Asthma flare", "symptoms": ["Wheeze"]}
        self.assertEqual(put("robin", [migraine], was=[]).status_code, 200)
        read = self.merged(pid, "lee")["journal"]["groups"]   # lee's page reads them
        self.assertEqual(put("robin", [migraine, asthma], was=[migraine]).status_code, 200)   # robin adds one
        r = put("lee", [], was=read)   # lee deletes Migraine day from the list read before
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("changed since", r.json()["detail"])
        self.assertEqual(self.merged(pid)["journal"]["groups"], [migraine, asthma], "robin's group kept")
        now = self.merged(pid, "lee")["journal"]["groups"]
        self.assertEqual(put("lee", [asthma], was=now).status_code, 200, "made again on the groups as they are")
        self.assertEqual(self.merged(pid)["journal"]["groups"], [asthma])
        self.assertEqual(put("lee", [migraine], was=None).status_code, 409, "null read: none then, some now")
        self.assertEqual(put("lee", [migraine]).status_code, 200, "without it, as before")
        # a person's own: Units from two tabs
        units = self.c["robin"].get("/api/settings/user").json()["units"]
        r = self.c["robin"].put("/api/settings/user/units", json={"value": {**units, "weight": "lb"}, "was": units}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        r = self.c["robin"].put("/api/settings/user/units", json={"value": {**units, "height": "cm"}, "was": units}, headers=H)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(self.c["robin"].get("/api/settings/user").json()["units"]["weight"], "lb", "the first tab's kept")
        self.c["robin"].put("/api/settings/user/units", json={"value": units}, headers=H)
        # the install's: the attachment kinds, ticked on the Admin page
        ada = self.c["ada"]
        kinds = {st["key"]: st for sec in ada.get("/api/settings/install").json()["sections"] for st in sec["settings"]}["sync.attachTypes"]["value"]
        self.assertEqual(ada.put("/api/settings/install/sync.attachTypes", json={"value": kinds[:1], "was": kinds}, headers=H).status_code, 200)
        self.assertEqual(ada.put("/api/settings/install/sync.attachTypes", json={"value": kinds[:2], "was": kinds}, headers=H).status_code, 409)
        ada.post("/api/settings/install/reset", json={"section": "sync"}, headers=H)

    def test_fewer_days_of_activity_counts_and_confirms(self):   # #1002
        """General → Logging: keeping fewer days of activity counts the entries the next daily clean-up deletes, every
        profile's and the install's own, and is made only once that count is typed back (409 until then, saying what
        goes; a wrong count too). Nothing goes until that clean-up, which then deletes those and no others. More days,
        or fewer where nothing is that old, asks nothing; a reset to the default is asked as a change is."""
        from server import activity
        ada, key, now = self.c["ada"], "activity.days", dbm.now()
        ada.post("/api/me/reauth", json={"secret": PW["ada"]}, headers=H)
        ids = {}
        with contextlib.closing(dbm.connect()) as db:
            for days, pid in ((400, 1), (200, 1), (100, None), (40, 1), (10, None)):
                ids[days] = db.execute("INSERT INTO activity(at, profile_id, actor, action) VALUES(?, ?, 'fictional', 'view')",
                                       (now - days * 86400, pid)).lastrowid
        left = lambda: sorted(d for d, i in ids.items() if dbm.connect().execute("SELECT 1 FROM activity WHERE id=?", (i,)).fetchone())   # noqa: E731
        put = lambda v, **kw: ada.put(f"/api/settings/install/{key}", json={"value": v, **kw}, headers=H)   # noqa: E731
        r = put(90)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["detail"]["confirm"]["count"], 3)
        self.assertIn("Keeping 90 days of activity instead of 365 deletes 3 entries older than 90 days at the next daily "
                      "clean-up: 2 entries of the profiles' activity and 1 entry of the install's own.", r.json()["detail"]["message"])
        self.assertIn("Type 3 to confirm.", r.json()["detail"]["message"])
        self.assertEqual(put(90, confirm="2").status_code, 409, "a wrong count")
        self.assertEqual(config.setting("ACTIVITY_DAYS"), 365)
        self.assertEqual(put(90, confirm="3").status_code, 200)
        self.assertEqual((config.setting("ACTIVITY_DAYS"), left()), (90, [10, 40, 100, 200, 400]), "nothing goes yet")
        self.assertEqual(self.install_log()[0]["detail"], {"setting": key, "label": settings.SETTINGS[key].label, "from": 365, "to": 90})
        with contextlib.closing(dbm.connect()) as db:   # the next daily clean-up: a day after the last
            db.execute("UPDATE meta SET v='0' WHERE k='activity_pruned'")
            activity.log(db, "view", None, 1, actor="fictional")
        self.assertEqual(left(), [10, 40])
        self.assertEqual(put(400).status_code, 200, "more days: nothing goes")
        self.assertEqual(put(60).status_code, 200, "nothing that old")
        self.assertEqual(put(30).json()["detail"]["confirm"]["count"], 1)
        self.assertEqual(put(400).status_code, 200)
        self.assertEqual(put(7).status_code, 400, "bounded")
        r = ada.post("/api/settings/install/reset", json={"section": "logging"}, headers=H)   # 400 back to 365: nothing that old
        self.assertEqual((r.status_code, r.json()["reset"], config.setting("ACTIVITY_DAYS")), (200, [key], 365))
        # a reset of the one setting on a profile's route is asked the same way, and carries its confirmation (#998)
        self.assertEqual(put(400).status_code, 200)
        with contextlib.closing(dbm.connect()) as db:
            db.execute("INSERT INTO activity(at, profile_id, actor, action) VALUES(?, 1, 'fictional', 'view')", (now - 380 * 86400,))
        reset = lambda **kw: ada.request("DELETE", f"/api/profiles/1/settings/{key}", headers=H, **kw)   # noqa: E731
        r = reset()
        self.assertEqual((r.status_code, r.json()["detail"]["code"], r.json()["detail"]["confirm"]["count"]), (409, "confirm", 1), r.text)
        self.assertEqual(reset(json={"confirm": "2"}).status_code, 409, "a wrong count")
        self.assertEqual(config.setting("ACTIVITY_DAYS"), 400)
        self.assertEqual(reset(json={"confirm": "1"}).status_code, 200)
        self.assertEqual(config.setting("ACTIVITY_DAYS"), 365)

    def test_a_change_that_applies_later_is_asked_now(self):   # #1042
        """With saved settings left out (SANGUITY_SAVED_SETTINGS=off), a change to an install setting that's asked first
        is asked all the same, from the value saved here, the one that applies once the flag is taken out (it was saved
        unasked, and took effect then): fewer days of activity, a shorter sign-in. (#1158) The environment's changes
        nothing: a reset is asked from the value saved here."""
        ada, now = self.c["ada"], dbm.now()
        ada.post("/api/me/reauth", json={"secret": PW["ada"]}, headers=H)
        put = lambda k, v, **kw: ada.put(f"/api/settings/install/{k}", json={"value": v, **kw}, headers=H)   # noqa: E731
        self.assertEqual(put("activity.days", 400).status_code, 200)
        with contextlib.closing(dbm.connect()) as db:
            gone = db.execute("INSERT INTO activity(at, profile_id, actor, action) VALUES(?, 1, 'fictional', 'view')", (now - 380 * 86400,)).lastrowid
        self.login("zed")
        with contextlib.closing(dbm.connect()) as db:
            aged = db.execute("SELECT MAX(id) FROM sessions").fetchone()[0]
            db.execute("UPDATE sessions SET created=created-3*3600 WHERE id=?", (aged,))
        try:
            with mock.patch.dict(os.environ, {"SANGUITY_SAVED_SETTINGS": "off"}):
                self.assertEqual(config.setting("ACTIVITY_DAYS"), 365, "left out")
                r = put("activity.days", 90)
                self.assertEqual((r.status_code, r.json()["detail"]["code"]), (409, "confirm"), r.text)
                self.assertIn("instead of 400", r.json()["detail"]["message"])
                self.assertIn("this takes effect once it's taken out", r.json()["detail"]["message"])
                r = put("security.sessionHours", 2)
                self.assertEqual((r.status_code, r.json()["detail"]["code"]), (409, "confirm"), r.text)
                self.assertEqual(settings.stored(dbm.connect(), "install").get("security.sessionHours"), None, "not saved")
            self.assertEqual(config.setting("ACTIVITY_DAYS"), 400, "the flag taken out: nothing changed unasked")
            with mock.patch.dict(os.environ, {"ACTIVITY_DAYS": "500"}):
                self.assertEqual(config.setting("ACTIVITY_DAYS"), 400, "the environment's isn't in force")
                reset = lambda **kw: ada.request("DELETE", "/api/profiles/1/settings/activity.days", headers=H, **kw)   # noqa: E731
                r = reset()
                self.assertEqual((r.status_code, r.json()["detail"]["code"]), (409, "confirm"), r.text)
                self.assertNotIn("environment", r.json()["detail"]["message"])
                self.assertEqual(reset(json={"confirm": r.json()["detail"]["confirm"]["type"]}).status_code, 200)
            self.assertEqual(config.setting("ACTIVITY_DAYS"), 365)
        finally:
            with contextlib.closing(dbm.connect()) as db:
                db.execute("DELETE FROM activity WHERE id=?", (gone,))
                db.execute("DELETE FROM sessions WHERE id=?", (aged,))
            ada.post("/api/settings/install/reset", json={"section": "logging"}, headers=H)
            self.security_reset()

    def test_install_settings_a_member_is_given(self):   # #998
        """A profile's settings, merged or as the Settings screen's sections, give someone who isn't an admin only the
        install's settings a member's page reads (the sync defaults): not the public addresses, the upload address,
        sign-in's limits (a page reads how long a sign-in lasts from /api/me's session), the provider directory's or
        the activity kept. An admin is given every one."""
        def install_keys(c):
            flat = lambda o, at="": [k for n, v in o.items() for k in (flat(v, at + n + ".") if isinstance(v, dict) else [at + n])]   # noqa: E731
            merged = {k for k in flat(c.get("/api/profiles/1/settings").json()) if k in settings.SETTINGS}
            listed = {st["key"] for sec in c.get("/api/profiles/1/settings/sections").json()["sections"] for st in sec["settings"]}
            return {k for k in merged | listed if settings.SETTINGS[k].scope == "install"}
        everyone = {k for k, d in settings.SETTINGS.items() if d.scope == "install"}
        for who in ("robin", "kim", "lee"):
            got = install_keys(self.c[who])
            self.assertEqual(got, {k for k in everyone if k.startswith("sync.")}, who)
            self.assertFalse({k for k in got if k.split(".")[0] in ("host", "phone", "security", "directory", "activity")}, who)
        self.assertEqual(install_keys(self.c["ada"]), everyone)
        self.assertEqual(set(self.c["kim"].get("/api/me").json()["session"]), {"days", "hours_without_2fa", "idle_minutes"})

    def test_general_about_names_the_deployment(self):   # #1004
        """General → About: each deployment's kind named (SANGUITY_DEPLOYMENT, which every template sets: deploy/check.py),
        its folders each with what mounts it there, and its version where it has one (a checkout, or an image built
        without one, says so); unset, or a kind it doesn't know, "other"; the limits with their variables, values and
        where each comes from. Admins only."""
        from server import general
        ada = self.c["ada"]
        self.assertEqual(self.c["robin"].get("/api/settings/install/about").status_code, 403)
        for kind, version, want in (("truenas", "1.2.3", ("1.2.3", False)), ("release", "1.2.3", ("1.2.3", False)),
                                    ("compose", "", (None, True)), ("allinone", "dev", (None, True)), ("", "", (None, True)),
                                    ("no-such-kind", "1.2.3", ("1.2.3", False))):
            with mock.patch.dict(os.environ, SANGUITY_DEPLOYMENT=kind, SANGUITY_IMAGE_VERSION=version, SANGUITY_VERSION="9.9.9"):
                a = ada.get("/api/settings/install/about").json()   # (#1158: .env's SANGUITY_VERSION, handed to the app, never read)
            named = kind if kind in general.KINDS else "other"
            self.assertEqual((a["deployment"], a["deployment_name"]), (named, general.KINDS[named]["name"]))
            self.assertEqual((a["version"], a["checkout"]), want, kind)
            with mock.patch.dict(os.environ, SANGUITY_DEPLOYMENT=kind, SANGUITY_IMAGE_VERSION=version, SANGUITY_VERSION="9.9.9"):   # the records page's foot
                self.assertEqual(self.c["robin"].get("/api/me").json()["version"], want[0], kind)
            folders = {f["key"]: f for f in a["folders"]}
            self.assertEqual(folders["data"]["path"], config.DATA_DIR)
            self.assertEqual(folders["data"]["set_by"], general.KINDS[a["deployment"]]["mounts"].get("data"))
        for kind in set(general.KINDS) - {"other"}:   # every kind names what mounts each folder, and who it runs as
            self.assertEqual(set(general.KINDS[kind]["mounts"]), {"data", "backups", "key", "records", "watch", "config", "user"}, kind)
        limits = {x["name"]: x for g in a["limits"] for x in g["limits"]}
        self.assertEqual(limits["SHL_MAX_MB"], {"name": "SHL_MAX_MB", "what": "What one link brings", "unit": "MB", "value": 64,
                                                 "from": "default", "default": 64, "restart": True})
        with mock.patch.dict(os.environ, UPLOAD_MAX_FILES="500"):   # (#1158) the environment's isn't in force
            got = {x["name"]: x for g in ada.get("/api/settings/install/about").json()["limits"] for x in g["limits"]}["UPLOAD_MAX_FILES"]
        self.assertEqual((got["value"], got["from"]), (20000, "default"))
        self.assertEqual(a["schema"], len(dbm.SCHEMA))
        # (#1119) the server's own setup, Host's and Security's before, with where it comes from; the limits changed
        # under Needs a restart said so (#1121); and every card's settings say where this install's environment is set
        with mock.patch.dict(os.environ, COOKIE_SECURE="1", SANGUITY_DEPLOYMENT="truenas"):
            got = ada.get("/api/settings/install/about").json()
            at = ada.get("/api/settings/install").json()["settings_at"]
        self.assertEqual(got["server"]["cookie_secure"], {"from": "default", "value": "auto"})
        self.assertEqual({x["name"] for g in got["limits"] for x in g["limits"] if not x["restart"]}, set(), "each on Needs a restart")
        self.assertEqual(at, general.KINDS["truenas"]["settings"])

    def test_rename_one_name(self):
        """#38: one name of a name map changed where it's shown, the rest of the map kept"""
        pid = self.fresh_profile()
        default = settings.SETTINGS["labNicknames"].default
        rn = lambda key, body, who="robin": self.c[who].put(f"/api/profiles/{pid}/settings/{key}/name", json=body, headers=H)
        r = rn("labNicknames", {"name": "Fictional Marker Q", "to": "  Marker   Q "})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["labNicknames"], {**default, "Fictional Marker Q": "Marker Q"})
        # someone else renames another lab meanwhile: both names stay
        self.assertEqual(rn("labNicknames", {"name": "Ferritin", "to": "Iron store"}, "lee").status_code, 200)
        self.assertEqual(self.merged(pid)["labNicknames"], {**default, "Fictional Marker Q": "Marker Q", "Ferritin": "Iron store"})
        # back to the name in the records: no "to", an empty one, or the same name; a default nickname goes too
        rn("labNicknames", {"name": "Ferritin"})
        rn("labNicknames", {"name": "Fictional Marker Q", "to": "Fictional Marker Q"})
        first = next(iter(default))
        rn("labNicknames", {"name": first, "to": ""})
        self.assertEqual(self.merged(pid)["labNicknames"], {k: v for k, v in default.items() if k != first})
        # a source tag, the same way
        self.assertEqual(rn("sources", {"name": "EXT", "to": "Outside lab"}).json()["sources"], {"EXT": "Outside lab"})
        # refused: a viewer, a stranger, without the CSRF header, a setting that isn't names, no such setting, bad text
        self.assertEqual(rn("sources", {"name": "EXT", "to": "Viewer's"}, "kim").status_code, 404)   # not shared with kim
        db = dbm.connect()
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (self.uid["kim"], pid))
        db.close()
        self.assertEqual(rn("sources", {"name": "EXT", "to": "Viewer's"}, "kim").status_code, 403)
        self.assertEqual(rn("sources", {"name": "EXT", "to": "Stranger's"}, "zed").status_code, 404)
        self.assertEqual(self.c["robin"].put(f"/api/profiles/{pid}/settings/sources/name", json={"name": "EXT", "to": "x"}).status_code, 403)
        self.assertEqual(rn("visitWorkouts", {"name": "EXT", "to": "x"}).status_code, 400)
        self.assertEqual(rn("nope", {"name": "EXT", "to": "x"}).status_code, 404)
        self.assertEqual(rn("sources", {"name": " ", "to": "x"}).status_code, 400)
        self.assertEqual(rn("sources", {"name": "EXT", "to": "x" * 121}).status_code, 400)
        self.assertEqual(self.merged(pid)["sources"], {"EXT": "Outside lab"})
        # logged like any change, without the names
        log = self.c["robin"].get(f"/api/profiles/{pid}/activity?action=settings").json()
        self.assertEqual((log[0]["actor"], log[0]["detail"]), ("robin", {"setting": "sources", "label": "Result sources"}))
        self.assertNotIn("Outside lab", json.dumps(log))
        self.assertNotIn("Iron store", json.dumps(log))

    def test_reset_section(self):
        pid = self.fresh_profile()
        self.put("labNicknames", {"Glucose": "Sugar"}, pid=pid)
        self.put("sources", {"EXT": "Outside lab"}, pid=pid)
        self.put("overview.keyLabs", ["Glucose"], pid=pid)
        self.assertEqual(self.c["kim"].post(f"/api/profiles/{pid}/settings/reset", json={"section": "names"}, headers=H).status_code, 404)
        self.assertEqual(self.c["robin"].post(f"/api/profiles/{pid}/settings/reset", json={"section": "nope"}, headers=H).status_code, 404)
        r = self.c["robin"].post(f"/api/profiles/{pid}/settings/reset", json={"section": "names"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(sorted(r.json()["reset"]), ["labNicknames", "sources"])
        m = self.merged(pid)
        self.assertEqual((m["labNicknames"], m["sources"]), (settings.SETTINGS["labNicknames"].default, {}))
        self.assertEqual(m["overview"]["keyLabs"], ["Glucose"])   # other sections untouched
        # a viewer can't reset a profile's section; a section mixing scopes resets only what the person may change
        r = self.c["kim"].post("/api/profiles/1/settings/reset", json={"section": "names"}, headers=H)
        self.assertEqual(r.status_code, 403)
        self.put("test.mine", "dark", who="kim")
        self.put("test.server", 40, who="ada")
        r = self.c["kim"].post("/api/profiles/1/settings/reset", json={"section": "testing"}, headers=H)
        self.assertEqual((r.status_code, r.json()["reset"], r.json()["kept"]), (200, ["test.mine"], ["test.server"]))
        self.assertEqual((self.merged(1, "kim")["test"], self.merged(1, "ada")["test"]["server"]), ({"mine": "light"}, 40))
        self.c["ada"].delete("/api/profiles/1/settings/test.server", headers=H)

    # ---------------------------------------------------------------- #36 key-lab tiles, #40 tabs, #45 theme

    def test_key_labs_editor(self):
        """#36 extends the key-labs setting: the same key, its own editor, kept in the order given"""
        d = settings.SETTINGS["overview.keyLabs"]
        self.assertEqual((d.scope, d.section, d.kind, d.default), ("profile", "overview", "tiles", settings.KEY_LABS))
        pid = self.fresh_profile()
        self.assertEqual(self.put("overview.keyLabs", ["Glucose", "Ferritin", "Hemoglobin"], pid=pid)["overview"]["keyLabs"],
                         ["Glucose", "Ferritin", "Hemoglobin"])
        self.put("overview.keyLabs", ["Ferritin", "Glucose"], pid=pid, who="kim", status=404)   # not shared with kim
        log = self.c["robin"].get(f"/api/profiles/{pid}/activity?action=settings").json()
        self.assertEqual(log[0]["detail"], {"setting": "overview.keyLabs", "label": "Key labs"})

    def test_tabs(self):
        d = settings.SETTINGS["tabs"]
        self.assertEqual((d.scope, d.section, d.kind), ("profile", "tabs", "tabs"))
        pid = self.fresh_profile()
        self.assertEqual(self.merged(pid)["tabs"], {"order": list(settings.TABS), "hidden": []})
        # reordered and hidden; the hidden listed in the tabs' order, and a tab left out goes at the end, shown
        self.assertEqual(settings.TABS, ("overview", "health", "medications", "timeline", "labs", "costs"))   # Trends + Lab heatmap = Labs (#172)
        m = self.put("tabs", {"order": ["timeline", "overview", "costs", "labs", "medications"], "hidden": ["health", "costs"]}, pid=pid)
        self.assertEqual(m["tabs"], {"order": ["timeline", "overview", "costs", "labs", "medications", "health"], "hidden": ["costs", "health"]})
        self.assertEqual(self.put("tabs", {"hidden": ["costs"]}, pid=pid)["tabs"], {"order": list(settings.TABS), "hidden": ["costs"]})
        for bad in ({"order": ["overview", "nope"]}, {"hidden": list(settings.TABS)}, {"order": "overview"}, ["overview"],
                    {"hidden": [3]}, {"order": [], "hidden": [], "extra": 1}):
            self.put("tabs", bad, pid=pid, status=400)
        self.put("tabs", {"hidden": ["costs"]}, pid=pid, who="kim", status=404)
        self.put("tabs", {"hidden": ["costs"]}, pid=1, who="kim", status=403)   # a viewer of profile 1
        self.put("tabs", {"hidden": ["costs"]}, pid=pid, who="lee")              # an editor
        # its own section, with Reset to defaults; logged in the profile's activity
        r = self.c["robin"].post(f"/api/profiles/{pid}/settings/reset", json={"section": "tabs"}, headers=H)
        self.assertEqual((r.status_code, r.json()["reset"]), (200, ["tabs"]))
        self.assertEqual(self.merged(pid)["tabs"]["hidden"], [])
        actions = [(e["actor"], e["detail"].get("setting") or e["detail"].get("section")) for e in
                   self.c["robin"].get(f"/api/profiles/{pid}/activity?action=settings").json()]
        self.assertEqual(actions[:2], [("robin", "tabs"), ("lee", "tabs")])

    def test_tabs_before_labs(self):
        """#172: a layout naming Trends ("labs") and Lab heatmap ("heatmap"), saved before they were the one Labs tab,
        whether sent again, stored from before, or in an export: Labs takes the first one's place and stays shown
        while either was."""
        pid = self.fresh_profile("Old layout (fictional)")
        old = ["overview", "heatmap", "health", "timeline", "labs", "costs"]
        for value, want in (
                ({"order": old, "hidden": ["labs"]}, {"order": ["overview", "labs", "health", "medications", "timeline", "costs"], "hidden": []}),
                ({"order": old, "hidden": ["heatmap"]}, {"order": ["overview", "labs", "health", "medications", "timeline", "costs"], "hidden": []}),
                ({"order": old, "hidden": ["labs", "costs", "heatmap"]}, {"order": ["overview", "labs", "health", "medications", "timeline", "costs"], "hidden": ["labs", "costs"]}),
                ({"order": ["trends", "timeline"]}, {"order": ["labs", "timeline", "overview", "health", "medications", "costs"], "hidden": []}),
                ({"hidden": ["heatmap"]}, {"order": list(settings.TABS), "hidden": []})):
            self.assertEqual(self.put("tabs", value, pid=pid)["tabs"], want, value)
        self.put("tabs", {"hidden": [*old, "medications"]}, pid=pid, status=400)   # still one tab at least shown
        # a row stored before the upgrade is read in today's tabs (merged, the Settings page's values, an export)
        db = dbm.connect()
        db.execute("DELETE FROM settings WHERE key='tabs' AND profile_id=?", (pid,))
        db.execute("INSERT INTO settings(key, profile_id, user_id, value, sealed, changed, changed_by) VALUES('tabs', ?, NULL, ?, NULL, ?, NULL)",
                   (pid, json.dumps({"order": ["heatmap", "overview", "health", "timeline", "labs", "costs"], "hidden": ["labs", "health"]}), dbm.now()))
        stored = settings.stored(db, "profile", pid)["tabs"]
        exported = settings.to_file(db, pid)["tabs"]
        db.close()
        want = {"order": ["labs", "overview", "health", "medications", "timeline", "costs"], "hidden": ["health"]}
        self.assertEqual((self.merged(pid)["tabs"], stored, exported), (want, want, want))
        # a settings.json from before (an export loaded back in, a setup build's) the same
        self.assertEqual(settings.from_file({"tabs": {"order": old, "hidden": ["heatmap", "costs"]}})["tabs"],
                         {"order": ["overview", "labs", "health", "medications", "timeline", "costs"], "hidden": ["costs"]})

    def test_tabs_before_medications(self):
        """#275: a layout saved before the Medications tab gets it after Body and activity, wherever that is and whether or
        not it's hidden (shown itself), not at the end; a layout that names it keeps it where it is, hidden or not. Sent
        again, stored from before (merged, the Settings page's values, an export) or from a settings.json."""
        pid = self.fresh_profile("Before Medications (fictional)")
        before = ["costs", "overview", "health", "labs", "timeline"]
        for value, want in (
                ({"order": before}, {"order": ["costs", "overview", "health", "medications", "labs", "timeline"], "hidden": []}),
                ({"order": before, "hidden": ["health"]}, {"order": ["costs", "overview", "health", "medications", "labs", "timeline"], "hidden": ["health"]}),
                ({"order": ["labs", "timeline", "overview", "costs", "health"]}, {"order": ["labs", "timeline", "overview", "costs", "health", "medications"], "hidden": []}),
                ({"order": ["labs", "costs"]}, {"order": ["labs", "costs", "overview", "health", "medications", "timeline"], "hidden": []}),
                ({"order": ["medications", *before], "hidden": ["medications"]}, {"order": ["medications", *before], "hidden": ["medications"]}),
                ({"order": [*before, "medications"]}, {"order": [*before, "medications"], "hidden": []})):
            self.assertEqual(self.put("tabs", value, pid=pid)["tabs"], want, value)
        db = dbm.connect()
        db.execute("DELETE FROM settings WHERE key='tabs' AND profile_id=?", (pid,))
        db.execute("INSERT INTO settings(key, profile_id, user_id, value, sealed, changed, changed_by) VALUES('tabs', ?, NULL, ?, NULL, ?, NULL)",
                   (pid, json.dumps({"order": before, "hidden": ["costs"]}), dbm.now()))
        stored = settings.stored(db, "profile", pid)["tabs"]
        exported = settings.to_file(db, pid)["tabs"]
        db.close()
        want = {"order": ["costs", "overview", "health", "medications", "labs", "timeline"], "hidden": ["costs"]}
        self.assertEqual((self.merged(pid)["tabs"], stored, exported), (want, want, want))
        self.assertEqual(settings.from_file({"tabs": {"order": before, "hidden": ["costs"]}})["tabs"], want)

    def test_guide(self):
        """#199: whether the getting-started guide is done or skipped is each person's own, and kept off the Settings
        screen (the guide's own page sets it)."""
        d = settings.SETTINGS["guide"]
        self.assertEqual((d.scope, d.default, d.private, d.screen), ("user", "", False, False))
        for bad in ("finished", None, True, ["done"]):
            self.assertEqual(self.c["zed"].put("/api/settings/user/guide", json={"value": bad}, headers=H).status_code, 400, bad)
        self.assertEqual(self.c["zed"].get("/api/settings/user").json()["guide"], "")
        r = self.c["zed"].put("/api/settings/user/guide", json={"value": "skipped"}, headers=H)
        self.assertEqual((r.status_code, r.json()["guide"]), (200, "skipped"))
        self.assertEqual(self.c["robin"].get("/api/settings/user").json()["guide"], "")   # someone else's is their own
        shown = {st["key"] for sec in self.c["zed"].get("/api/settings/user/sections").json()["sections"] for st in sec["settings"]}
        shown |= {st["key"] for sec in self.c["robin"].get("/api/profiles/1/settings/sections").json()["sections"] for st in sec["settings"]}
        self.assertNotIn("guide", shown)
        self.c["zed"].put("/api/settings/user/guide", json={"value": ""}, headers=H)

    def test_theme(self):
        d = settings.SETTINGS["theme"]
        self.assertEqual((d.scope, d.section, d.kind, d.default, d.private), ("user", "appearance", "theme", "system", False))
        for bad in ("blue", "", None, 1, ["dark"]):
            self.put("theme", bad, who="kim", status=400)
        # each person's own, on every profile, a viewer too; logged as an account event
        self.put("theme", "dark", who="kim")
        self.assertEqual((self.merged(1, "kim")["theme"], self.merged(2, "kim")["theme"], self.merged(1, "robin")["theme"]),
                         ("dark", "dark", "system"))
        acct = [e for e in self.c["ada"].get("/api/activity").json() if e["action"] == "settings.user" and e["actor"] == "kim"]
        self.assertEqual(acct[0]["detail"], {"setting": "theme", "label": "Theme"})
        secs = {s["key"]: s for sec in self.c["kim"].get("/api/profiles/1/settings/sections").json()["sections"] for s in sec["settings"]}
        self.assertEqual((secs["theme"]["value"], secs["theme"]["from"], secs["theme"]["can_change"]), ("dark", "user", True))
        # Reset to defaults on its section, by the person themself
        r = self.c["kim"].post("/api/profiles/1/settings/reset", json={"section": "appearance"}, headers=H)
        self.assertEqual((r.status_code, r.json()["reset"]), (200, ["theme"]))
        self.assertEqual(self.merged(2, "kim")["theme"], "system")
        # not part of a profile's settings.json (it's a person's, not the profile's)
        self.assertNotIn("theme", settings.from_file({"theme": "dark"}))

    def test_in_range(self):
        """#1108: In-range results, grey (the default) or green, each person's own beside the theme, its choices listed"""
        d = settings.SETTINGS["inRange"]
        self.assertEqual((d.scope, d.section, d.kind, d.default, d.private), ("user", "appearance", "choice", "grey", False))
        for bad in ("red", "", None, 1, ["green"]):
            self.put("inRange", bad, who="kim", status=400)
        self.put("inRange", "green", who="kim")
        self.assertEqual((self.merged(1, "kim")["inRange"], self.merged(2, "kim")["inRange"], self.merged(1, "robin")["inRange"]),
                         ("green", "green", "grey"))
        secs = {s["key"]: s for sec in self.c["kim"].get("/api/profiles/1/settings/sections").json()["sections"] for s in sec["settings"]}
        self.assertEqual((secs["inRange"]["value"], secs["inRange"]["choices"]), ("green", {"grey": "Grey", "green": "Green"}))
        r = self.c["kim"].post("/api/profiles/1/settings/reset", json={"section": "appearance"}, headers=H)
        self.assertEqual((r.json()["reset"], self.merged(1, "kim")["inRange"]), (["inRange"], "grey"))

    def test_trend_views(self):
        """#37: named sets of labs for Trends, per profile, one at most the default"""
        d = settings.SETTINGS["trends.views"]
        self.assertEqual((d.scope, d.section, d.kind, d.default, d.private), ("profile", "trends", "views", [], True))
        pid = self.fresh_profile()
        self.assertEqual(self.merged(pid)["trends"]["views"], [])
        m = self.put("trends.views", [{"name": "  CBC ", "labs": ["Hemoglobin", "WBC", "Hemoglobin"]},
                                      {"name": "Liver", "labs": ["ALT", "AST"], "default": True}], pid=pid)
        self.assertEqual(m["trends"]["views"], [{"name": "CBC", "labs": ["Hemoglobin", "WBC"], "default": False},
                                                {"name": "Liver", "labs": ["ALT", "AST"], "default": True}])
        self.assertEqual(m["trends"]["defaultLabs"], settings.SETTINGS["trends.defaultLabs"].default)   # untouched
        for bad in ({"name": "CBC", "labs": ["WBC"]},                                   # not a list
                    [{"name": "CBC", "labs": ["WBC"]}, {"name": "cbc", "labs": ["ALT"]}],   # the same name twice
                    [{"name": "", "labs": ["WBC"]}], [{"labs": ["WBC"]}], [{"name": "CBC", "labs": []}],
                    [{"name": "CBC", "labs": "WBC"}], [{"name": "x" * 61, "labs": ["WBC"]}],
                    [{"name": "A", "labs": ["WBC"], "default": True}, {"name": "B", "labs": ["ALT"], "default": True}],
                    [{"name": "A", "labs": ["WBC"], "default": "yes"}], [{"name": "A", "labs": ["WBC"], "colour": "red"}],
                    [{"name": f"V{i}", "labs": ["WBC"]} for i in range(31)]):
            self.put("trends.views", bad, pid=pid, status=400)
        self.assertEqual(len(self.merged(pid)["trends"]["views"]), 2)   # nothing refused was kept
        # per profile: another profile has none; viewers read them but can't save one, editors can
        self.assertEqual(self.merged(2)["trends"]["views"], [])
        self.put("trends.views", [], pid=1, who="kim", status=403)
        self.assertEqual(self.merged(1, "kim")["trends"]["views"], [])
        self.put("trends.views", [{"name": "Kidney", "labs": ["Creatinine"]}], pid=pid, who="lee")
        self.assertEqual(self.merged(pid, "robin")["trends"]["views"][0]["name"], "Kidney")
        # sealed at rest (lab names), logged without them, and back to none with the Trends section's reset
        db = dbm.connect()
        r = db.execute("SELECT value, sealed FROM settings WHERE key='trends.views' AND profile_id=?", (pid,)).fetchone()
        db.close()
        self.assertIsNone(r["value"])
        self.assertTrue(r["sealed"].startswith(crypto.FIELD))
        log = self.c["robin"].get(f"/api/profiles/{pid}/activity?action=settings").json()
        self.assertEqual(log[0]["detail"], {"setting": "trends.views", "label": "Saved views"})
        self.assertNotIn("Creatinine", json.dumps(log))
        r = self.c["robin"].post(f"/api/profiles/{pid}/settings/reset", json={"section": "trends"}, headers=H)
        self.assertIn("trends.views", r.json()["reset"])
        self.assertEqual(self.merged(pid)["trends"]["views"], [])
        # in an exported settings.json, and read back from one
        self.put("trends.views", [{"name": "CBC", "labs": ["WBC"], "default": True}], pid=pid)
        db = dbm.connect()
        try:
            self.assertEqual(settings.to_file(db, pid)["trends"]["views"], [{"name": "CBC", "labs": ["WBC"], "default": True}])
        finally:
            db.close()
        self.assertEqual(settings.from_file({"trends": {"views": [{"name": "Kidney", "labs": ["Creatinine"]}]}})["trends.views"],
                         [{"name": "Kidney", "labs": ["Creatinine"], "default": False}])

    # ---------------------------------------------------------------- storage, export, import

    def test_encrypted_at_rest(self):
        self.put("labNicknames", {"Fictional Marker Q": "Marker"})
        db = dbm.connect()
        r = db.execute("SELECT value, sealed FROM settings WHERE key='labNicknames' AND profile_id=1").fetchone()
        self.assertIsNone(r["value"])
        self.assertTrue(r["sealed"].startswith(crypto.FIELD))
        self.assertNotIn("Fictional Marker Q", "".join(str(x) for row in db.execute("SELECT * FROM settings") for x in row))
        settings.put(db, "test.server", 5)   # a setting that holds no health information is plain
        self.assertEqual(db.execute("SELECT value FROM settings WHERE key='test.server'").fetchone()[0], "5")
        settings.clear(db, ["test.server"], "install")
        db.close()
        self.c["robin"].delete("/api/profiles/1/settings/labNicknames", headers=H)

    def test_export_and_import(self):
        self.put("overview.keyLabs", ["Ferritin"])
        self.put("labNicknames", {"Ferritin": "Iron store"})
        mine = self.merged(1)
        z = zipfile.ZipFile(io.BytesIO(self.c["robin"].get("/api/profiles/1/export").content))
        exported = json.loads(z.read("settings.json"))
        self.assertEqual(exported["overview"], {"keyLabs": ["Ferritin"]})
        self.assertEqual(exported["labNicknames"], {"Ferritin": "Iron store"})
        self.assertNotIn("test", exported)   # only the profile's own settings
        # loaded into another profile, it brings the same settings, and resets what the export doesn't set
        pid = self.fresh_profile("Copy (fictional)")
        self.put("sources", {"OLD": "Replaced by the import"}, pid=pid)
        archive = os.path.join(TMP, "export.zip")
        with open(archive, "wb") as f:
            f.write(self.c["robin"].get("/api/profiles/1/export").content)
        db = dbm.connect()
        export.import_archive(db, pid, archive)
        db.close()
        self.assertEqual(self.merged(pid), mine)
        # a profile at its defaults exports none, and loading that export resets the settings
        empty = self.fresh_profile()
        self.assertEqual(json.loads(zipfile.ZipFile(io.BytesIO(self.c["robin"].get(f"/api/profiles/{empty}/export").content))
                                    .read("settings.json")), {"_format": settings.FORMAT})
        with open(archive, "wb") as f:   # profile 1's records, with settings.json saying nothing is set
            zin = zipfile.ZipFile(io.BytesIO(self.c["robin"].get("/api/profiles/1/export").content))
            with zipfile.ZipFile(f, "w") as zout:
                for n in zin.namelist():
                    zout.writestr(n, json.dumps({"_format": settings.FORMAT}) if n == "settings.json" else zin.read(n))
        db = dbm.connect()
        export.import_archive(db, pid, archive)
        db.close()
        self.assertEqual(self.merged(pid)["labNicknames"], settings.SETTINGS["labNicknames"].default)
        for k in ("overview.keyLabs", "labNicknames"):
            self.c["robin"].delete(f"/api/profiles/1/settings/{k}", headers=H)

    def test_deleting_a_profile_removes_its_settings(self):
        pid = self.fresh_profile("Gone soon (fictional)")
        self.put("sources", {"X": "Y"}, pid=pid)
        db = dbm.connect()
        prof.delete_profile(db, pid)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM settings WHERE profile_id=?", (pid,)).fetchone()[0], 0)
        db.close()

    # ---------------------------------------------------------------- dates and times (#42)

    def test_dates_are_each_persons_own(self):
        def mine(who):
            r = self.c[who].get("/api/settings/user")
            self.assertEqual(r.status_code, 200, r.text)
            return r.json()

        def set_mine(who, key, value, status=200):
            r = self.c[who].put(f"/api/settings/user/{key}", json={"value": value}, headers=H)
            self.assertEqual(r.status_code, status, r.text)
            return r.json()
        # not chosen until the first page after signing in sets them from the browser
        self.assertEqual((mine("zed")["dates.timeZone"], mine("zed")["dates.format"]), ("", ""))
        self.assertEqual(client().get("/api/settings/user").status_code, 401)
        set_mine("robin", "dates.timeZone", "America/Los_Angeles")
        set_mine("robin", "dates.format", "mdy")
        set_mine("lee", "dates.timeZone", "Pacific/Auckland")
        set_mine("lee", "dates.format", "ymd")
        set_mine("zed", "dates.timeZone", "Europe/Paris")   # someone with no profile at all
        self.assertEqual((mine("robin")["dates.timeZone"], mine("lee")["dates.timeZone"], mine("zed")["dates.timeZone"]),
                         ("America/Los_Angeles", "Pacific/Auckland", "Europe/Paris"))
        # the same on every profile, in the merged settings the dashboard reads, and only for that person
        for pid in (1, 2):
            self.assertEqual(self.merged(pid, "lee")["dates"], {"timeZone": "Pacific/Auckland", "format": "ymd"})
        self.assertEqual(self.merged(1, "robin")["dates"], {"timeZone": "America/Los_Angeles", "format": "mdy"})
        self.assertEqual(self.merged(1, "kim")["dates"], {"timeZone": "", "format": ""})
        set_mine("kim", "dates.timeZone", "Asia/Tokyo")   # a viewer sets their own
        self.assertEqual(self.merged(1, "kim")["dates"]["timeZone"], "Asia/Tokyo")
        # refused: not a zone name, a zone that doesn't exist (where there's a time zone database), a format it doesn't have
        set_mine("robin", "dates.timeZone", "not a zone; DROP", 400)
        set_mine("robin", "dates.timeZone", 5, 400)
        if settings._zones():
            set_mine("robin", "dates.timeZone", "Mars/Olympus_Mons", 400)
        set_mine("robin", "dates.format", "yyyy/dd", 400)
        set_mine("robin", "sources", {}, 404)   # a profile's setting isn't changed here
        set_mine("robin", "no.such", 1, 404)
        self.assertEqual(self.c["robin"].put("/api/settings/user/dates.format", json={"value": "dmy"}).status_code, 403)   # CSRF
        self.assertEqual(mine("robin")["dates.format"], "mdy")
        acct = [e for e in self.c["ada"].get("/api/activity").json() if e["action"] == "settings.user"]
        self.assertIn({"setting": "dates.timeZone", "label": "Time zone"}, [e["detail"] for e in acct])
        self.assertNotIn("Pacific/Auckland", json.dumps(acct))   # which setting, never its value
        secs = {s["key"]: s for sec in self.c["kim"].get("/api/profiles/1/settings/sections").json()["sections"] for s in sec["settings"]}
        self.assertEqual((secs["dates.timeZone"]["kind"], secs["dates.timeZone"]["scope"], secs["dates.timeZone"]["can_change"]),
                         ("timezone", "user", True))
        for who in ("robin", "lee", "kim"):
            for key in ("dates.timeZone", "dates.format"):
                self.c[who].delete(f"/api/profiles/1/settings/{key}", headers=H)

    def test_dates_from_the_browser_arent_logged(self):
        """#524: the browser's time zone and date format, filled in by the first page after signing in (if_unset), are
        stored only if none is yet, and logged as no change; one the person chooses still is"""
        def logged():
            return [e["detail"]["setting"] for e in self.c["ada"].get("/api/activity").json()
                    if e["action"] == "settings.user" and e["actor"] == "kim" and "setting" in e["detail"]]
        before = logged()
        put = lambda key, body: self.c["kim"].put(f"/api/settings/user/{key}", json=body, headers=H)
        try:
            r = put("dates.timeZone", {"value": "Asia/Tokyo", "if_unset": True})
            self.assertEqual((r.status_code, r.json()["dates.timeZone"]), (200, "Asia/Tokyo"), r.text)
            self.assertEqual(put("dates.format", {"value": "ymd", "if_unset": True}).json()["dates.format"], "ymd")
            self.assertEqual(logged(), before)   # nobody changed anything
            # another tab's, once one is stored: the one stored kept, and said
            r = put("dates.timeZone", {"value": "Europe/Paris", "if_unset": True})
            self.assertEqual((r.status_code, r.json()["dates.timeZone"]), (200, "Asia/Tokyo"))
            self.assertEqual(put("dates.format", {"value": "nope", "if_unset": True}).status_code, 400)   # still checked
            # the person's own change: stored and logged
            r = put("dates.timeZone", {"value": "Europe/Paris"})
            self.assertEqual((r.status_code, r.json()["dates.timeZone"]), (200, "Europe/Paris"))
            self.assertEqual(logged(), ["dates.timeZone"] + before)
        finally:
            for key in ("dates.timeZone", "dates.format"):
                self.c["kim"].delete(f"/api/profiles/1/settings/{key}", headers=H)

    def test_own_settings_without_a_profile(self):
        """#169: someone with no profile still has Display on the Settings page: their own settings' sections,
        and Reset to defaults for one of them."""
        r = self.c["zed"].get("/api/settings/user/sections")
        self.assertEqual(r.status_code, 200, r.text)
        secs = {sec["id"]: sec for sec in r.json()["sections"]}
        self.assertEqual(set(secs) - {"testing"}, {"appearance", "dates", "units"})   # (this file's own section)
        every = [st for sec in secs.values() for st in sec["settings"]]
        self.assertTrue(all(st["scope"] == "user" and st["can_change"] for st in every))
        self.assertIn("choices", {st["key"]: st for st in every}["units"])   # what the units editor draws from
        self.assertEqual(client().get("/api/settings/user/sections").status_code, 401)
        self.assertEqual(self.c["zed"].put("/api/settings/user/dates.format", json={"value": "dmy"}, headers=H).status_code, 200)
        dates = {st["key"]: st for sec in self.c["zed"].get("/api/settings/user/sections").json()["sections"] for st in sec["settings"]}
        self.assertEqual((dates["dates.format"]["value"], dates["dates.format"]["from"]), ("dmy", "user"))
        self.assertEqual(self.c["zed"].post("/api/settings/user/reset", json={"section": "dates"}).status_code, 403)   # CSRF
        r = self.c["zed"].post("/api/settings/user/reset", json={"section": "dates"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("dates.format", r.json()["reset"])
        self.assertEqual(r.json()["settings"]["dates.format"], "")
        self.assertEqual(self.c["zed"].post("/api/settings/user/reset", json={"section": "ranges"}, headers=H).status_code, 404)   # a profile's

    # ---------------------------------------------------------------- a watch receiver secret per profile (#46)

    def keys_file(self):
        with open(config.WATCH_KEYS, "rb") as f:
            self.assertEqual(f.read(4), crypto.MAGIC)   # sealed, like everything the receiver keeps
        return json.loads(crypto.read_file(config.WATCH_KEYS))

    def rotate(self, pid, who="robin", status=200):
        r = self.c[who].post(f"/api/profiles/{pid}/watch/receiver/rotate", headers=H)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def test_connected_sources_start_off_and_an_upgrade_keeps_them_on_where_it_finds_them(self):   # #1096
        """A fresh install starts with connected sources off; an install from before the switch with a registered
        app (or a connection) has it turned on, once, at start-up; one an admin turned off since stays off. /api/me
        gives everyone the plain on or off, never the setting."""
        key = "experimental.connectedSources"
        db = dbm.connect()
        held = lambda: settings.stored(db, "install").get(key)
        try:
            self.assertTrue(dbm.meta_get(db, settings.SOURCES_KEPT), "the start-up's step has run")
            self.assertEqual((held(), settings.connected_sources()), (None, False), "a fresh install: off")
            self.assertFalse(settings.keep_sources_on(db), "once")
            dbm.meta_set(db, settings.SOURCES_KEPT, None)   # an install from before #1096, with an app registered
            db.execute("""INSERT INTO smart_clients(name, vendor, issuer, client_id, auth, scopes, created)
                          VALUES('Fake clinic (fictional)', 'epic', 'https://fhir.example.org/r4', 'app-1', 'public', 'openid', 0)""")
            self.assertFalse(settings.connected_sources())
            self.assertTrue(settings.keep_sources_on(db))
            self.assertEqual((held(), settings.connected_sources()), (True, True), "on, saved as an admin's would be")
            self.assertFalse(settings.keep_sources_on(db), "once")
            for who in ("kim", "ada"):
                me = self.c[who].get("/api/me").json()
                self.assertIs(me["connected_sources"], True, who)
                self.assertNotIn(key, json.dumps(me), "a plain on or off, not the setting")
            settings.put(db, key, False)   # an admin turns it off: the next start leaves it so
            dbm.meta_set(db, settings.SOURCES_KEPT, None)
            self.assertFalse(settings.keep_sources_on(db))
            self.assertEqual((held(), self.c["kim"].get("/api/me").json()["connected_sources"]), (False, False))
            with mock.patch.dict(os.environ, {"CONNECTED_SOURCES": "1"}):   # (#1158) the environment's changes nothing
                self.assertIs(self.c["kim"].get("/api/me").json()["connected_sources"], False)
        finally:
            db.execute("DELETE FROM smart_clients")
            db.execute("DELETE FROM settings WHERE key=?", (key,))
            dbm.meta_set(db, settings.SOURCES_KEPT, "1")
            settings.forget_saved()
            db.close()

    def test_watch_import_stays_beside_the_receiver(self):
        pid = self.fresh_profile("Imported watch (fictional)")
        db = dbm.connect()
        watch_store.save_import(db, pid, {"metrics": [{"key": "steps", "name": "Steps", "unit": "steps", "group": "Activity",
                                                        "pts": [["2026-09-01", 4000]]}], "exercise": []})
        self.assertTrue(os.path.isfile(watch_store.own_path(pid)))
        db.close()
        made = self.rotate(pid)
        db = dbm.connect()
        row = db.execute("SELECT watch_file FROM profiles WHERE id=?", (pid,)).fetchone()
        self.assertEqual(row["watch_file"], watch_keys.summary_path(str(pid)))   # the receiver's summary, in its own slot
        self.assertFalse(os.path.isfile(watch_store.own_path(pid)))             # the import moved beside it
        self.assertEqual(watch_store.load(db, pid)["metrics"][0]["pts"], [["2026-09-01", 4000]])
        sealed = db.execute("SELECT value, sealed FROM settings WHERE key=? AND profile_id=?", (watch_keys.KEY, pid)).fetchone()
        self.assertIsNone(sealed["value"])
        self.assertTrue(sealed["sealed"].startswith(crypto.FIELD) and made["secret"] not in sealed["sealed"])
        self.assertNotIn(watch_keys.KEY.split(".")[0], json.dumps(self.merged(pid)))   # never in the settings the page reads
        # deleting the profile takes its secret out of the receiver's file
        self.assertIn(made["secret"], [p["secret"] for p in self.keys_file()["profiles"]])
        db.close()
        r = self.c["robin"].request("DELETE", f"/api/profiles/{pid}", json={"confirm_name": "Imported watch (fictional)"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn(made["secret"], [p["secret"] for p in self.keys_file()["profiles"]])

    def test_watch_upload_address_behind_a_proxy(self):
        """#1007: with no public address, the phone upload address is the host this page was reached at: behind a reverse
        proxy the one it says it served (X-Forwarded-Host, as the calendar's and Find your provider's addresses take it),
        not the Host it sent on; on RECEIVER_PORT, which the TrueNAS, Unraid and CasaOS templates now pass (deploy/check.py)
        and the app reads at each use."""
        url = f"/api/profiles/{self.fresh_profile('Behind a proxy (fictional)')}/watch/receiver"   # (its own: a secret is made)
        saved = settings.saved_values
        with mock.patch.object(config, "saved", lambda: {k: v for k, v in saved().items() if k != "host.publicOrigins"}):
            got = lambda **h: self.c["robin"].get(url, headers=h).json()["addresses"]   # noqa: E731
            self.assertEqual(got(), ["http://testserver:8731/ingest"])   # the Host (TestClient's)
            self.assertEqual(got(**{"Host": "app:8000", "X-Forwarded-Host": "health.example.org"}), ["http://health.example.org:8731/ingest"])
            self.assertEqual(got(**{"X-Forwarded-Host": "health.example.org:8443, proxy.lan"}), ["http://health.example.org:8731/ingest"])
            with mock.patch.dict(os.environ, {"RECEIVER_PORT": "9731"}):
                self.assertEqual(got(**{"X-Forwarded-Host": "health.example.org"}), ["http://health.example.org:9731/ingest"])
            # and the answer that makes a secret says the same
            r = self.c["robin"].post(url + "/rotate", headers={**H, "X-Forwarded-Host": "health.example.org"})
            self.assertEqual((r.status_code, r.json()["addresses"]), (200, ["http://health.example.org:8731/ingest"]), r.text)
        # a public address set: its host, whatever the request says
        self.assertEqual(self.c["robin"].get(url, headers={"X-Forwarded-Host": "nas.lan"}).json()["addresses"],
                         ["http://health.example.test:8731/ingest"])

    def test_watch_two_profiles_with_the_real_receiver(self):
        import hashlib, hmac, socket, subprocess, time, urllib.error, urllib.request
        top = os.path.dirname(os.path.dirname(config.WATCH_KEYS))   # the receiver's DATA (WATCH_DIR)
        legacy = os.path.join(TMP, "keys", "watch_secret")
        with open(legacy, "w") as f:
            f.write("the-install-secret-from-before\n")
        db = dbm.connect()
        first = watch_keys.first_profile(db)
        if first is None:
            first = 1
            db.execute("UPDATE profiles SET watch_file=? WHERE id=1", (config.WATCH_FILE,))
        db.close()
        other = self.fresh_profile("Second watch (fictional)")
        # who may see and change it: owners and editors; not viewers, and not someone it isn't shared with
        db = dbm.connect()
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (self.uid["kim"], other))
        db.close()
        self.assertEqual(self.c["zed"].get(f"/api/profiles/{other}/watch/receiver").status_code, 404)   # not shared
        self.assertEqual(self.c["kim"].get(f"/api/profiles/{other}/watch/receiver").status_code, 403)   # a viewer
        self.rotate(other, "kim", 403)
        self.assertEqual(self.c["robin"].post(f"/api/profiles/{other}/watch/receiver/rotate").status_code, 403)   # CSRF
        st = self.c["lee"].get(f"/api/profiles/{first}/watch/receiver").json()
        self.assertEqual((st["legacy"], st["has_secret"], st["ready"]), (True, False, True))
        self.assertEqual(st["addresses"], ["http://health.example.test:8731/ingest"])   # PUBLIC_ORIGINS' host, the receiver's port
        self.assertFalse(self.c["lee"].get(f"/api/profiles/{other}/watch/receiver").json()["legacy"])

        env = {**os.environ, "DATA": top, "SECRET_FILE": legacy, "KEYS_FILE": config.WATCH_KEYS,
               "SANGUITY_KEY_FILE": config.KEY_FILE, "TZ_DEFAULT": "UTC"}

        def start():
            with socket.socket() as sk:
                sk.bind(("127.0.0.1", 0))
                port = sk.getsockname()[1]
            proc = subprocess.Popen([sys.executable, "-I", os.path.join(ROOT, "watch", "receiver.py")], env={**env, "PORT": str(port)},
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(100):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1)
                    return proc, port
                except OSError:
                    time.sleep(0.1)
            proc.kill()
            self.fail("the receiver didn't start")

        def post(port, secret, day, kg):
            body = json.dumps({"source": "health_connect", "timezone": "UTC", "timestamp": f"{day}T09:00:00Z",
                               "weight": [{"time": f"{day}T08:00:00Z", "kilograms": kg, "uuid": f"w-{day}-{kg}"}]}).encode()
            sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            try:
                return urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/ingest", data=body,
                                                                     headers={"X-Signature": sig}), timeout=5).status
            except urllib.error.HTTPError as e:
                return e.code

        def days(pid):
            r = self.c["robin"].get(f"/api/profiles/{pid}/watch")
            if r.status_code == 204:
                return {}
            return dict(next((m["pts"] for m in r.json()["metrics"] if m["key"] == "weight"), []))

        def until(cond):
            for _ in range(200):
                if cond():
                    return
                time.sleep(0.1)
            self.fail("the receiver didn't write the summary")

        proc, port = start()
        try:
            # an install from before #46: its single secret is the first profile's
            self.assertEqual(post(port, "the-install-secret-from-before", "2026-10-01", 70), 200)
            until(lambda: "2026-10-01" in days(first))
            # the second profile makes its own secret; its phone's uploads are its own
            self.assertIsNone(self.c["lee"].get(f"/api/profiles/{other}/watch/receiver").json()["last"])   # nothing sent yet
            made = self.rotate(other, "lee")
            self.assertEqual((made["has_secret"], made["legacy"], made["masked"][-4:]), (True, False, made["secret"][-4:]))
            self.assertNotIn(made["secret"], self.c["lee"].get(f"/api/profiles/{other}/watch/receiver").text)   # shown once
            self.assertEqual(post(port, made["secret"], "2026-10-02", 60), 200)
            until(lambda: "2026-10-02" in days(other))
            self.assertIsInstance(self.c["lee"].get(f"/api/profiles/{other}/watch/receiver").json()["last"], int)   # Sources' overview (#166)
            self.assertNotIn("2026-10-02", days(first))
            self.assertNotIn("2026-10-01", days(other))
            self.assertEqual(post(port, "a guess", "2026-10-03", 99), 401)
            # the first profile makes one of its own: the old secret stops working, and its history carries on
            mine = self.rotate(first)
            self.assertFalse(self.keys_file()["legacy"])
            self.assertEqual({p["profile"]: p["slot"] for p in self.keys_file()["profiles"]}[first], "")
            self.assertEqual(post(port, "the-install-secret-from-before", "2026-10-04", 71), 401)
            self.assertEqual(post(port, mine["secret"], "2026-10-05", 72), 200)
            until(lambda: "2026-10-05" in days(first))
            self.assertEqual(sorted(days(first)), ["2026-10-01", "2026-10-05"])
            # a new secret for the second profile: the old one stops working
            again = self.rotate(other)
            self.assertNotEqual(again["secret"], made["secret"])
            self.assertEqual(post(port, made["secret"], "2026-10-06", 61), 401)
            self.assertEqual(post(port, again["secret"], "2026-10-07", 62), 200)
            until(lambda: "2026-10-07" in days(other))
            self.assertEqual(sorted(days(other)), ["2026-10-02", "2026-10-07"])
        finally:
            proc.kill(); proc.wait()
        # each profile's data in its own place, sealed
        for path in (os.path.join(top, "public", "daily.json"), os.path.join(top, "public", "profiles", str(other), "daily.json"),
                     os.path.join(top, "profiles", str(other), "watch.db")):
            with open(path, "rb") as f:
                self.assertEqual(f.read(4), crypto.MAGIC, path)
        log = self.c["robin"].get(f"/api/profiles/{other}/activity?action=watch.secret").json()
        self.assertEqual([(e["actor"], e["detail"]) for e in log], [("robin", {"rotated": True}), ("lee", {"rotated": False})])
        self.assertNotIn(again["secret"], json.dumps(log))

    def test_page_served_with_the_csp(self):
        self.assertIn("settings", open(os.path.join(ROOT, "nginx.conf"), encoding="utf-8").read())
        with open(os.path.join(ROOT, "www", "settings.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertNotIn("<script>", html)          # strict CSP: scripts only from js/
        self.assertNotRegex(html, r"\son[a-z]+=")   # and no inline handlers


if __name__ == "__main__":
    unittest.main(verbosity=2)
