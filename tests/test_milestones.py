#!/usr/bin/env python3
"""Milestones in the app and the rules for visits counted as workouts (issues #39 and #43).

Milestones (server/milestones.py, the "milestones" setting in server/settings.py): moved once at start-up from the
setup build's meta and corrections.json (before the rebuild that drops them is loaded) and from each import's meta,
never again; later imports leave them alone, except an export from before #39 loaded back in whole; added, changed
and removed through the settings API, sorted, one primary at most; bad ones refused; reset with their section;
owners and editors change them, viewers only read them; the CSRF check; the activity log without the dates;
encrypted at rest; build.py no longer reads corrections.json.

Workout rules (the "visitWorkouts" setting): plain text by default, a regular expression as an option, rules from
before #43 read as they were meant; a regular expression is checked before it's saved, and refused when it's
invalid or could backtrack catastrophically (too long, too many repeats, a repeat around a repeat or alternatives,
backreferences, lookarounds, inline flags); the accepted ones run fast on a hostile title. Fictional data only.

  python3 tests/test_milestones.py
"""
import contextlib, io, json, os, shutil, subprocess, sys, tempfile, time, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-milestones-test-")
BUILD = os.path.join(TMP, "build")
CORR = os.path.join(TMP, "config", "corrections.json")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=BUILD,
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_CORRECTIONS=CORR)
for d in ("records", "keys", "build", "config", "profiles", "dataset"):
    os.makedirs(os.path.join(TMP, d), exist_ok=True)
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, crypto, db as dbm, milestones, profiles as prof, settings   # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites", "lee": "maple tunnel brisk saddle",
      "zed": "a stranger to every profile"}


def data(**meta):
    return {"built": "2026-11-01T00:00:00+00:00", "source": "fictional",
            "events": [{"id": 1, "t": "2026-02-10T09:15:00", "k": "visit", "title": "Physical Therapy follow-up", "sub": ""}],
            "details": {}, "labs": [], "costs": [], **meta}


# the setup build as the old build.py wrote it, with corrections.json's milestones in it
OLD_BUILD = data(milestones=[{"date": "2026-01-15", "label": "Diagnosis", "primary": True}])
# corrections.json: the same diagnosis, a surgery also marked primary, and one that can't be kept
CORR_FILE = {"milestones": [{"date": "2026-01-15", "label": "Diagnosis", "primary": True},
                            {"date": "2026-04-01", "label": "Surgery", "primary": True},
                            {"date": "sometime", "label": "Unknown date"}],
             "diagnosis_date": "2020-01-01"}   # ignored when there are milestones


def write(path, obj, bump=0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    if bump:
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + bump * 1_000_000_000))


def client():
    return TestClient(main.app, base_url="http://testserver")


def old_install():
    """An install from before #39: a build-linked profile holding the old build, a profile whose import has an older
    data.json's single dxDate, and one with no milestones; then setup rebuilds with the new build.py (no milestones)."""
    write(os.path.join(BUILD, "data.json"), OLD_BUILD)
    write(CORR, CORR_FILE)
    crypto.make_key_file(os.environ["SANGUITY_KEY_FILE"])
    dbm.init()
    db = dbm.connect()
    p1 = prof.create_profile(db, "Built (fictional)", linked_build=True)
    prof.sync_build(db, p1)
    p2 = prof.create_profile(db, "Older export (fictional)")
    write(os.path.join(TMP, "dataset", "data.json"), data(dxDate="2025-11-03"))
    prof.import_dataset(db, p2, os.path.join(TMP, "dataset"))
    p3 = prof.create_profile(db, "None (fictional)")
    write(os.path.join(TMP, "dataset", "data.json"), data(milestones=[]))
    prof.import_dataset(db, p3, os.path.join(TMP, "dataset"))
    db.close()
    write(os.path.join(BUILD, "data.json"), data(milestones=[]), bump=5)   # the rebuild, not loaded yet
    return p1, p2, p3


from server import main   # noqa: E402  (after the environment is set)


class Milestones(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p1, cls.p2, cls.p3 = old_install()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            cls.ctx = client().__enter__()
        cls.startup = out.getvalue()
        db = dbm.connect()
        cls.uid = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        for pid in (cls.p1, cls.p2, cls.p3):
            db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'owner')", (cls.uid["robin"], pid))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (cls.uid["kim"], cls.p1))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (cls.uid["lee"], cls.p1))
        db.close()
        cls.c = {n: cls.login(n) for n in PW}

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

    def ms(self, pid, who="robin"):
        r = self.c[who].get(f"/api/profiles/{pid}/settings")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["milestones"]

    def put(self, key, value, pid=None, who="robin", status=200, headers=H):
        r = self.c[who].put(f"/api/profiles/{pid or self.p1}/settings/{key}", json={"value": value}, headers=headers)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    # ---------------------------------------------------------------- the move, once

    def test_a_moved_once_at_start_up(self):
        self.assertIn("Moved the milestones of 2 profile(s) into their settings", self.startup)
        self.assertIn("a milestone was left out", self.startup)   # corrections.json's undated one
        # the build's and corrections.json's, joined; of two primaries the earliest stays primary
        self.assertEqual(self.ms(self.p1), [{"date": "2026-01-15", "label": "Diagnosis", "primary": True},
                                            {"date": "2026-04-01", "label": "Surgery"}])
        self.assertEqual(self.ms(self.p2), [{"date": "2025-11-03", "label": "Diagnosis"}])   # the older dxDate
        self.assertEqual(self.ms(self.p3), [])
        db = dbm.connect()
        self.assertTrue(dbm.meta_get(db, milestones.MIGRATED))
        self.assertNotIn("milestones", settings.stored(db, "profile", self.p3))   # nothing set: still the default
        # the rebuild was loaded after the move: the build's meta has no milestones any more, the setting keeps them
        from server import records
        self.assertEqual(records.metas(db, self.p1).get("milestones"), [])
        # moved once: removing them in the app (or a reset) isn't undone by another start
        self.put("milestones", [], pid=self.p2)
        self.c["robin"].post(f"/api/profiles/{self.p1}/settings/reset", json={"section": "milestones"}, headers=H)
        self.assertIsNone(milestones.migrate(db))
        db.close()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            with client():   # a whole restart
                pass
        self.assertNotIn("milestones", out.getvalue())
        self.assertEqual((self.ms(self.p1), self.ms(self.p2)), ([], []))

    def test_waits_for_a_profile(self):
        """corrections.json, but no profile yet (a new install starting): tried again on the next start."""
        db = dbm.connect()
        db.execute("BEGIN")
        try:
            dbm.meta_set(db, milestones.MIGRATED, None)
            db.execute("DELETE FROM profiles")
            self.assertIsNone(milestones.migrate(db))
            self.assertIsNone(dbm.meta_get(db, milestones.MIGRATED))
        finally:
            db.execute("ROLLBACK")
            db.close()

    def test_later_imports_leave_them_alone(self):
        """After the move an import never changes milestones (undo restores exactly), except an export from before
        #39 loaded back in whole: its settings.json has none, its data.json does, and the export replaces settings."""
        db = dbm.connect()
        pid = prof.create_profile(db, "Imported later (fictional)", owner_id=self.uid["robin"])
        folder = os.path.join(TMP, "older-export")
        write(os.path.join(folder, "data.json"), data(milestones=[{"date": "2026-06-01", "label": "Treatment start", "primary": True}]))
        prof.import_dataset(db, pid, folder)   # records only
        self.assertEqual(self.ms(pid), [])
        write(os.path.join(folder, "settings.json"), {"_format": settings.FORMAT, "sources": {"EXT": "Outside lab"}})
        prof.import_dataset(db, pid, folder)   # a whole export from before #39
        self.assertEqual(self.ms(pid), [{"date": "2026-06-01", "label": "Treatment start", "primary": True}])
        # a newer export's settings.json has its own (here none): those win
        write(os.path.join(folder, "settings.json"), {"_format": settings.FORMAT, "milestones": []})
        prof.import_dataset(db, pid, folder)
        self.assertEqual(self.ms(pid), [])
        db.close()

    # ---------------------------------------------------------------- editing

    def test_editing_and_primary(self):
        pid = self.p3
        # added out of order: kept by date; a repeated one kept once; spaces tidied
        got = self.put("milestones", [{"date": "2026-05-01", "label": "Surgery"},
                                      {"date": "2026-02-01", "label": " Diagnosis ", "primary": True},
                                      {"date": "2026-05-01", "label": "Surgery"}], pid=pid)["milestones"]
        self.assertEqual(got, [{"date": "2026-02-01", "label": "Diagnosis", "primary": True},
                               {"date": "2026-05-01", "label": "Surgery"}])
        # a different primary, a renamed one, one removed, one added
        got = self.put("milestones", [{"date": "2026-02-01", "label": "Diagnosis"},
                                      {"date": "2026-08-15", "label": "Treatment start", "primary": True}], pid=pid)["milestones"]
        self.assertEqual([m.get("primary", False) for m in got], [False, True])
        # no primary at all is fine (the page counts from the first)
        self.assertNotIn("primary", json.dumps(self.put("milestones", [{"date": "2026-02-01", "label": "Diagnosis"}], pid=pid)["milestones"]))
        before = self.ms(pid)
        for bad in ([{"date": "2026-02-01", "label": "A", "primary": True}, {"date": "2026-03-01", "label": "B", "primary": True}],
                    [{"date": "2026-02-30", "label": "No such day"}],
                    [{"date": "1066-10-14", "label": "Too long ago"}],
                    [{"label": "No date"}],
                    [{"date": "2026-02-01", "label": "x" * 61}],
                    [{"date": "2026-02-01", "label": "A", "primary": "yes"}],
                    [{"date": f"2026-01-{d:02d}", "label": f"M{d} {m}"} for d in range(1, 29) for m in range(2)],
                    "2026-02-01"):
            self.put("milestones", bad, pid=pid, status=400)
        self.assertEqual(self.ms(pid), before)
        # its section resets to none
        r = self.c["robin"].post(f"/api/profiles/{pid}/settings/reset", json={"section": "milestones"}, headers=H)
        self.assertEqual((r.status_code, r.json()["reset"]), (200, ["milestones"]))
        self.assertEqual(self.ms(pid), [])
        secs = self.c["robin"].get(f"/api/profiles/{pid}/settings/sections").json()["sections"]
        sec = next(s for s in secs if s["id"] == "milestones")
        self.assertEqual([(s["key"], s["kind"]) for s in sec["settings"]], [("milestones", "milestones")])

    def test_roles_csrf_log_and_encryption(self):
        one = [{"date": "2026-03-03", "label": "Fictional procedure Zeta"}]
        self.put("milestones", one, who="lee")             # an editor
        self.put("milestones", [], who="kim", status=403)  # a viewer
        self.put("milestones", [], who="zed", status=404)  # a stranger
        self.assertEqual(self.c["kim"].post(f"/api/profiles/{self.p1}/settings/reset", json={"section": "milestones"},
                                            headers=H).status_code, 403)
        self.assertEqual(self.ms(self.p1, "kim"), one)      # viewers see them
        self.put("milestones", [], headers={}, status=403)  # the CSRF check
        log = self.c["robin"].get(f"/api/profiles/{self.p1}/activity?action=settings").json()
        self.assertEqual((log[0]["actor"], log[0]["detail"]), ("lee", {"setting": "milestones", "label": "Milestones"}))
        self.assertNotIn("Zeta", json.dumps(log))
        self.assertNotIn("2026-03-03", json.dumps(log))
        db = dbm.connect()
        r = db.execute("SELECT value, sealed FROM settings WHERE key='milestones' AND profile_id=?", (self.p1,)).fetchone()
        self.assertIsNone(r["value"])
        self.assertTrue(r["sealed"].startswith(crypto.FIELD))
        self.assertNotIn("Zeta", "".join(str(x) for row in db.execute("SELECT * FROM settings") for x in row))
        db.close()
        self.put("milestones", [])

    def test_build_no_longer_reads_corrections_json(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "data.json")
            env = {k: v for k, v in os.environ.items() if k != "RECORDS_ROOT"}
            subprocess.run([sys.executable, "-I", os.path.join(ROOT, "build.py"), "--config",
                            os.path.join(ROOT, "tests", "fixtures", "build", "sources.json"), "--out", out],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={**env, "SANGUITY_CORRECTIONS": CORR})
            with open(out, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["milestones"], [])


class WorkoutRules(unittest.TestCase):
    check = staticmethod(settings.SETTINGS["visitWorkouts"].clean)

    def refused(self, pattern, why):
        with self.assertRaises(settings.SettingError, msg=pattern) as e:
            self.check([{"match": pattern, "regex": True, "name": "X", "minutes": 30}])
        self.assertIn(why, str(e.exception), pattern)

    def test_plain_text_by_default(self):
        got = self.check([{"match": "physical  therapy", "name": "PT", "minutes": 45},
                          {"match": "(not a pattern)", "regex": False, "minutes": 20}])
        self.assertEqual(got, [{"match": "physical therapy", "regex": False, "name": "PT", "minutes": 45},
                               {"match": "(not a pattern)", "regex": False, "name": "(not a pattern)", "minutes": 20}])
        m = settings.rule_matches
        self.assertTrue(m(got[0], "Physical Therapy  follow-up"))
        self.assertTrue(m(got[0], "Outpatient physical therapy"))   # anywhere in the title
        self.assertFalse(m(got[0], "Physical exam"))
        self.assertTrue(m(got[1], "Visit (not a pattern)"))         # its signs are plain characters

    def test_rules_from_before_kept_their_meaning(self):
        """A rule without "regex" was always a regular expression: still one, unless it has none of their signs."""
        got = self.check([{"match": "^physical therapy\\b", "name": "PT", "minutes": 45},
                          {"match": "Occupational therapy", "name": "OT", "minutes": 30}])
        self.assertEqual([r["regex"] for r in got], [True, False])
        self.assertTrue(settings.rule_matches(got[0], "physical therapy session"))
        self.assertFalse(settings.rule_matches(got[0], "Outpatient physical therapy"))

    def test_regular_expressions_checked(self):
        ok = ["^physical therapy\\b", "^(physical|occupational) therapy", "PT|OT", "therap(y|ies)", "visit #\\d+",
              "[a-z]+ therapy", "(?:pt|ot) session", "a{2,5}", "^rehab.*gym$", "(physical )?therapy", "(\\w+)? therapy", "visit \\(?follow"]
        for p in ok:
            self.assertEqual(self.check([{"match": p, "regex": True, "name": "X", "minutes": 30}])[0]["match"], p)
        self.refused("([unclosed", "isn't a valid regular expression")
        self.refused("(a+)+$", "very slow")
        self.refused("(\\w+\\s?)*$", "very slow")
        self.refused("(a|aa)*b", "very slow")
        self.refused("(.*)*", "very slow")
        self.refused("((ab)*c)+", "very slow")
        self.refused("x" * (settings.REGEX_MAX + 1), "at most")
        self.refused("a?" * (settings.REGEX_REPEATS + 1), "at most")
        self.refused("a*a*a*b", "very slow")       # side by side: each one more multiplies the work
        self.refused(".*.*.*x", "very slow")
        self.refused("a{0,50}b+c*", "very slow")
        self.refused("a{1000}", "at most 100")
        self.refused("(a)\\1", "backreferences")
        self.refused("(?=physical)", "lookarounds")
        self.refused("(?<!x)pt", "lookarounds")
        self.refused("(?i)pt", "flags")
        self.refused("(?i:pt)", "flags")
        self.refused("(?P<n>pt)", "named groups")
        self.refused("pt(?#note)", "comments")
        self.refused("\\Apt\\Z", "^ and $")
        self.refused("a++", "possessive")
        with self.assertRaises(settings.SettingError):
            self.check([{"match": "pt", "regex": "yes", "name": "X", "minutes": 30}])
        with self.assertRaises(settings.SettingError):   # too long even as plain text
            self.check([{"match": "x" * (settings.PATTERN_MAX + 1), "name": "X", "minutes": 30}])

    def test_accepted_ones_run_fast(self):
        """The safe subset stays quick even on a long title made to make it backtrack."""
        hostile = "a" * 5000 + "!"
        for p in ["^(a|b)c", "a*a*b", ".*a.*x", "[a ]+$x", "(?:ab|a)c", "a?a?a?a?a?a?a?a?", "(a?b?)?c", "a+(?:b|c)?a*x"]:
            rule = self.check([{"match": p, "regex": True, "name": "X", "minutes": 30}])[0]
            t = time.monotonic()
            settings.rule_matches(rule, hostile)   # only its first TITLE_MAX characters are matched
            self.assertLess(time.monotonic() - t, 0.25, p)
        with open(os.path.join(ROOT, "www", "js", "health.js"), encoding="utf-8") as f:   # the page cuts titles the same
            self.assertIn(f"slice(0, {settings.TITLE_MAX})", f.read())


if __name__ == "__main__":
    unittest.main(verbosity=2)
