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
        for pid in (1, 2):   # kept aside, no longer read
            folder = os.path.join(TMP, "profiles", str(pid))
            self.assertFalse(os.path.exists(os.path.join(folder, "settings.json")))
            self.assertTrue(os.path.isfile(os.path.join(folder, settings.MIGRATED)))
        # a second start changes nothing, even after the app changed a setting
        self.put("trends.defaultLabs", ["Glucose", "Ferritin"])
        db = dbm.connect()
        self.assertEqual(settings.migrate_files(db, prof.profile_dir), 0)
        db.close()
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
        self.assertEqual(self.merged(1, "robin")["test"]["server"], 30)
        secs = {s["key"]: s for sec in self.c["kim"].get("/api/profiles/1/settings/sections").json()["sections"] for s in sec["settings"]}
        self.assertEqual((secs["sources"]["can_change"], secs["test.mine"]["can_change"], secs["test.server"]["can_change"]),
                         (False, True, False))
        # the CSRF check, as for every change
        self.assertEqual(self.c["robin"].put("/api/profiles/1/settings/sources", json={"value": {}}).status_code, 403)
        # logged without the values
        log = self.c["robin"].get("/api/profiles/1/activity?action=settings").json()
        self.assertEqual((log[0]["actor"], log[0]["detail"]), ("lee", {"setting": "sources", "label": "Result sources"}))
        self.assertNotIn("Editor's", json.dumps(log))
        acct = {e["action"]: e["detail"] for e in reversed(self.c["ada"].get("/api/activity").json())}
        self.assertEqual((acct["settings.user"], acct["settings.install"]),
                         ({"setting": "test.mine", "label": "Just mine"}, {"setting": "test.server", "label": "Server-wide"}))
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
        self.assertTrue(all(st["scope"] == "install" and st["can_change"] for sec in got for st in sec["settings"]), got)
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
        self.assertEqual((acct[0]["actor"], acct[0]["detail"]), ("ada", {"setting": "sync.staleDays", "label": "Warn when a connection hasn't synced for (days)"}))
        # Reset to defaults: the section's install values go
        self.assertEqual(ada.post("/api/settings/install/reset", json={"section": "names"}, headers=H).status_code, 404)
        r = ada.post("/api/settings/install/reset", json={"section": "sync"}, headers=H)
        self.assertEqual((r.status_code, r.json()["reset"]), (200, ["sync.staleDays"]), r.text)
        self.assertEqual(self.merged(1, "robin")["sync"]["staleDays"], 3)

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
        self.assertEqual(self.merged(1, "kim")["test"], {"mine": "light", "server": 40})
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
        self.assertEqual(settings.TABS, ("overview", "health", "timeline", "labs", "costs"))   # Trends + Lab heatmap = Labs (#172)
        m = self.put("tabs", {"order": ["timeline", "overview", "costs", "labs"], "hidden": ["health", "costs"]}, pid=pid)
        self.assertEqual(m["tabs"], {"order": ["timeline", "overview", "costs", "labs", "health"], "hidden": ["costs", "health"]})
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
                ({"order": old, "hidden": ["labs"]}, {"order": ["overview", "labs", "health", "timeline", "costs"], "hidden": []}),
                ({"order": old, "hidden": ["heatmap"]}, {"order": ["overview", "labs", "health", "timeline", "costs"], "hidden": []}),
                ({"order": old, "hidden": ["labs", "costs", "heatmap"]}, {"order": ["overview", "labs", "health", "timeline", "costs"], "hidden": ["labs", "costs"]}),
                ({"order": ["trends", "timeline"]}, {"order": ["labs", "timeline", "overview", "health", "costs"], "hidden": []}),
                ({"hidden": ["heatmap"]}, {"order": list(settings.TABS), "hidden": []})):
            self.assertEqual(self.put("tabs", value, pid=pid)["tabs"], want, value)
        self.put("tabs", {"hidden": old}, pid=pid, status=400)   # still one tab at least shown
        # a row stored before the upgrade is read in today's tabs (merged, the Settings page's values, an export)
        db = dbm.connect()
        db.execute("DELETE FROM settings WHERE key='tabs' AND profile_id=?", (pid,))
        db.execute("INSERT INTO settings(key, profile_id, user_id, value, sealed, changed, changed_by) VALUES('tabs', ?, NULL, ?, NULL, ?, NULL)",
                   (pid, json.dumps({"order": ["heatmap", "overview", "health", "timeline", "labs", "costs"], "hidden": ["labs", "health"]}), dbm.now()))
        stored = settings.stored(db, "profile", pid)["tabs"]
        exported = settings.to_file(db, pid)["tabs"]
        db.close()
        want = {"order": ["labs", "overview", "health", "timeline", "costs"], "hidden": ["health"]}
        self.assertEqual((self.merged(pid)["tabs"], stored, exported), (want, want, want))
        # a settings.json from before (an export loaded back in, a setup build's) the same
        self.assertEqual(settings.from_file({"tabs": {"order": old, "hidden": ["heatmap", "costs"]}})["tabs"],
                         {"order": ["overview", "labs", "health", "timeline", "costs"], "hidden": ["costs"]})

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
