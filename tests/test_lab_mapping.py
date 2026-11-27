#!/usr/bin/env python3
"""Lab names matched during import (#23): a lab spelled in a way lab-aliases.json doesn't know is suggested an
existing series, by LOINC code first and then by a similar name with a compatible unit (filled in only for the same
code or the same name written another way, #546; a similar name is only offered); confirming saves its points
under that series (the results' own rows keep the name as reported); the choice is remembered for that profile (#927:
only a series the review offered), so its next import of that spelling is matched by itself; "keep as a new series" is
remembered too, as a choice to keep it separate; only admins change or remove what's remembered, and it's logged; the
names are encrypted at rest; and results typed in use the remembered matches.

lab-aliases.json lists Hemoglobin's LOINC code (718-7), so an importer would name "HGB-X" with that code Hemoglobin
by itself. Here it runs with a copy of the list without that code, standing for the many codes the list doesn't have.

Fictional data only.

  python3 tests/test_lab_mapping.py
"""
import io, json, os, shutil, sys, tempfile, time, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-labmap-test-")
with open(os.path.join(ROOT, "lab-aliases.json"), encoding="utf-8") as f:
    ALIASES = json.load(f)
ALIASES["labs"]["Hemoglobin"].pop("loinc", None)
with open(os.path.join(TMP, "lab-aliases.json"), "w", encoding="utf-8") as f:
    json.dump(ALIASES, f)
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "no-build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_LAB_ALIASES=os.path.join(TMP, "lab-aliases.json"))
os.makedirs(os.path.join(TMP, "records"))
os.makedirs(os.path.join(TMP, "keys"))
sys.path.insert(0, ROOT)

import logging   # noqa: E402

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, activity, crypto, db as dbm, jobs, labmap, main, profiles as prof, twofactor, uploads   # noqa: E402
from tests.whole_records import whole   # noqa: E402  records with every record's details (#217)

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "ash": "copper meadow lantern drift", "ada": "granite willow ember compass",
      "vera": "saffron harbour thistle owl", "olga": "quartz meadow willow fern"}
HEAD = "Date,Test,Result,Units,Reference Range,Flag,Panel,LOINC\n"


def csv(*rows):
    return {"labs.csv": HEAD + "".join(",".join(r) + "\n" for r in rows)}


def client():
    return TestClient(main.app, base_url="http://testserver")


class LabMapping(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = client().__enter__()
        db = dbm.connect()
        cls.uids = {n: accounts.create(db, n, password=pw, is_admin=n == "ada")[0] for n, pw in PW.items()}
        db.close()
        cls.robin, cls.ash, cls.ada, cls.vera, cls.olga = (cls.login(n) for n in ("robin", "ash", "ada", "vera", "olga"))
        db = dbm.connect()   # the admin's own second factor (admin actions need one once there are other accounts)
        db.execute("UPDATE users SET totp_secret=? WHERE id=?", (crypto.enc(twofactor.new_secret()), cls.uids["ada"]))
        db.close()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

    @staticmethod
    def login(name):
        c = client()
        r = c.post("/api/login", json={"username": name, "password": PW[name]}, headers=H)
        assert r.status_code == 200, r.text
        return c

    def profile(self, name):
        db = dbm.connect()
        try:
            pid = prof.create_profile(db, f"{name} (fictional)", owner_id=self.uids["robin"])
            db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (self.uids["ash"], pid))
            return pid
        finally:
            db.close()

    def previewed(self, pid, files, c=None):
        db = dbm.connect()
        try:
            up = uploads.create(db, pid, None)
            for rel, data in files.items():
                uploads.add_file(db, pid, up, rel, io.BytesIO(data.encode()))
            uploads.finish(db, pid, up)
        finally:
            db.close()
        r = (c or self.robin).post(f"/api/profiles/{pid}/uploads/{up}/import", json={}, headers=H)
        self.assertEqual(r.status_code, 202, r.text)
        j = self.until(pid, r.json()["id"], ("preview", "failed"))
        self.assertEqual(j["state"], "preview", j)
        return j

    def until(self, pid, jid, states, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            j = self.robin.get(f"/api/profiles/{pid}/jobs/{jid}").json()
            if j["state"] in states:
                return j
            time.sleep(0.05)
        self.fail(f"job {jid} never reached {states}: {j}")

    def save(self, pid, j, labs=None, c=None):
        r = (c or self.robin).post(f"/api/profiles/{pid}/jobs/{j['id']}/confirm", json={"keep": [], "labs": labs or {}}, headers=H)
        self.assertEqual(r.status_code, 202, r.text)
        done = self.until(pid, j["id"], ("done", "failed"))
        self.assertEqual(done["state"], "done", done)
        return done

    def data(self, pid):
        return whole(self.robin, pid)

    def points(self, pid, name):
        return sorted((p[0][:10], p[1]) for s in self.data(pid)["labs"] if s["name"] == name for p in s["pts"])

    def own(self, pid, c=None):
        r = (c or self.robin).get(f"/api/profiles/{pid}/labmaps")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["matches"]

    def entry(self, j, name):
        return next(e for e in j["labs"] if e["name"] == name)

    def with_hemoglobin(self, name):
        pid = self.profile(name)
        self.save(pid, self.previewed(pid, csv(["2026-01-05", "Hemoglobin", "13.1", "g/dL", "13.5 - 17.5", "L", "CBC", "718-7"])))
        return pid

    # ---------------------------------------------------------------- suggestions

    def test_suggested_by_loinc_code(self):
        pid = self.with_hemoglobin("Loinc")
        j = self.previewed(pid, csv(["2026-02-05", "HGB-X", "12.0", "g/dL", "13.5 - 17.5", "L", "CBC", "718-7"]))
        e = self.entry(j, "HGB-X")
        self.assertEqual(e["ref"][:2], "s:")
        self.assertEqual(e["suggestions"][0], {"name": "Hemoglobin", "unit": "g/dL", "reason": "same LOINC code", "sure": True})
        self.assertIsNone(e["before"])
        self.assertIn({"name": "Hemoglobin", "unit": "g/dL"}, j["lab_targets"])
        # a name the profile has already, and a name lab-aliases.json knows, aren't asked about
        j2 = self.previewed(pid, csv(["2026-02-06", "Hemoglobin", "12.2", "g/dL", "", "", "CBC", ""],
                                     ["2026-02-06", "Ferritin", "80", "ng/mL", "", "", "", ""]))
        self.assertEqual(j2["labs"], [])
        self.assertEqual(j2["lab_targets"], [])

    def test_suggested_by_similar_name_only_with_a_compatible_unit(self):
        pid = self.profile("Similar")
        self.save(pid, self.previewed(pid, csv(["2026-01-05", "Vitamin D 25-Hydroxy", "31", "ng/mL", "30-100", "", "", ""])))
        j = self.previewed(pid, csv(["2026-02-05", "VIT D 25-OH", "28", "ng/ml", "30-100", "L", "", ""]))
        e = self.entry(j, "VIT D 25-OH")
        self.assertEqual(e["suggestions"][0], {"name": "Vitamin D 25-Hydroxy", "unit": "ng/mL", "reason": "same name written another way, same unit", "sure": True})
        j = self.previewed(pid, csv(["2026-02-05", "VIT D 25-OH", "70", "nmol/L", "75-250", "L", "", ""]))
        e = self.entry(j, "VIT D 25-OH")
        self.assertNotIn("Vitamin D 25-Hydroxy", [s["name"] for s in e["suggestions"]])

    def test_a_similar_name_is_offered_never_filled_in(self):   # #546
        """A similar name with the same unit is only a "Maybe", never sure: names a number or a letter tells apart
        (B6 and B1, Factor 3 and Factor 1, IgG and IgA) aren't put in the box, and with nothing chosen the results keep
        their own name and nothing is remembered. The same name, an alias and a LOINC code still fill it."""
        pid = self.profile("Look-alikes")
        self.save(pid, self.previewed(pid, csv(["2026-01-05", "Vitamin B1", "120", "nmol/L", "70-180", "", "", ""],
                                               ["2026-01-05", "Factor 1", "250", "mg/dL", "200-400", "", "", ""],
                                               ["2026-01-05", "IgA", "180", "mg/dL", "70-400", "", "", ""])))
        j = self.previewed(pid, csv(["2026-02-05", "Vitamin B6", "40", "nmol/L", "20-125", "", "", ""],
                                    ["2026-02-05", "Zorblatt Factor 3", "90", "mg/dL", "50-150", "", "", ""],
                                    ["2026-02-05", "IgG", "900", "mg/dL", "700-1600", "", "", ""],
                                    ["2026-02-05", "Vitamin B 1", "130", "nmol/L", "70-180", "", "", ""]))
        b6, f3, igg, b1 = (self.entry(j, n) for n in ("Vitamin B6", "Zorblatt Factor 3", "IgG", "Vitamin B 1"))
        self.assertEqual(b6["suggestions"][0], {"name": "Vitamin B1", "unit": "nmol/L", "reason": "similar name, same unit", "sure": False})
        for e in (b6, f3, igg):
            self.assertFalse([s for s in e["suggestions"] if s["sure"]], e)
        self.assertNotIn("Factor 1", [s["name"] for s in f3["suggestions"] if s["sure"]])
        self.assertEqual(b1["suggestions"][0]["name"], "Vitamin B1")   # ("b" alone left out as a filler word: offered only)
        self.assertFalse(b1["suggestions"][0]["sure"])
        have = {"Vitamin B1": {"unit": "nmol/L", "loinc": set()}, "Factor 1": {"unit": "mg/dL", "loinc": set()},
                "Vitamin": {"unit": "nmol/L", "loinc": set()}}
        for name, unit in (("Vitamin B6", "nmol/L"), ("Factor 3", "mg/dL"), ("Vitamin B", "nmol/L")):
            self.assertFalse([s for s in labmap.suggest({"name": name, "unit": unit}, have) if s["sure"]], name)
        # the same name written another way, and the same LOINC code, still fill the box
        self.assertTrue(labmap.suggest({"name": "VIT. B1", "unit": "nmol/l"}, have)[0]["sure"])   # (not "... SERUM": #569)
        self.assertTrue(labmap.suggest({"name": "Thiamine X", "unit": "nmol/L", "loinc": "10-1"},
                                       {"Vitamin B1": {"unit": "nmol/L", "loinc": {"10-1"}}})[0]["sure"])
        # saved with nothing chosen: each keeps its own series, and nothing is remembered for later imports
        done = self.save(pid, j)
        self.assertEqual(done["lab_names"], {"matched": 0, "remembered": 0})
        self.assertEqual(self.points(pid, "Vitamin B6"), [("2026-02-05", 40.0)])
        self.assertEqual(self.points(pid, "Vitamin B1"), [("2026-01-05", 120.0)])
        self.assertEqual(self.points(pid, "Zorblatt Factor 3"), [("2026-02-05", 90.0)])
        # the exact name, and a name lab-aliases.json knows, aren't asked about: they go to their series
        j = self.previewed(pid, csv(["2026-03-05", "Vitamin B1", "125", "nmol/L", "70-180", "", "", ""],
                                    ["2026-03-05", "HGB", "13.0", "g/dL", "13.5 - 17.5", "L", "", ""]))
        self.assertEqual(j["labs"], [])
        self.save(pid, j)
        self.assertEqual(self.points(pid, "Vitamin B1"), [("2026-01-05", 120.0), ("2026-03-05", 125.0)])
        self.assertEqual(self.points(pid, "Hemoglobin"), [("2026-03-05", 13.0)])

    def test_never_offered_across_a_qualifier_or_another_loinc_code(self):   # #1102
        """HDL Cholesterol was offered as "Maybe: LDL Cholesterol, similar name, same unit", opposite tests. Names a
        qualifier tells apart (HDL and LDL, free and total, direct and calculated, ionized) are never offered for each
        other, nor a test whose LOINC code isn't the series' own; "Total" alone doesn't make another test."""
        pid = self.profile("Qualifiers")
        self.save(pid, self.previewed(pid, csv(["2025-01-05", "LDL Cholesterol", "131", "mg/dL", "<100", "", "", "13457-7"])))
        j = self.previewed(pid, csv(["2025-03-14", "HDL Cholesterol", "72", "mg/dL", ">40", "", "", ""]))
        self.assertNotIn("LDL Cholesterol", [s["name"] for s in self.entry(j, "HDL Cholesterol")["suggestions"]])
        have = {"LDL Cholesterol": {"unit": "mg/dL", "loinc": {"13457-7"}}, "Testosterone, Free": {"unit": "ng/dL", "loinc": set()},
                "Bilirubin, Direct": {"unit": "mg/dL", "loinc": set()}, "Calcium, Ionized": {"unit": "mg/dL", "loinc": set()},
                "Zorblatt": {"unit": "mg/dL", "loinc": {"99-1"}}, "Quorbin": {"unit": "mg/dL", "loinc": set()}}
        names = lambda name, unit, loinc=None: [s["name"] for s in labmap.suggest({"name": name, "unit": unit, "loinc": loinc}, have)]  # noqa: E731
        for name, unit, other in (("HDL Cholesterol", "mg/dL", "LDL Cholesterol"), ("Non-LDL Cholesterol", "mg/dL", "LDL Cholesterol"),
                                  ("LDL Cholesterol Direct", "mg/dL", "LDL Cholesterol"), ("Testosterone, Total", "ng/dL", "Testosterone, Free"),
                                  ("Testosterone", "ng/dL", "Testosterone, Free"), ("Bilirubin, Total", "mg/dL", "Bilirubin, Direct"),
                                  ("Bilirubin, Indirect", "mg/dL", "Bilirubin, Direct"), ("Calcium", "mg/dL", "Calcium, Ionized"),
                                  ("Zorblat", "mg/dL", "Zorblatt")):
            self.assertNotIn(other, names(name, unit, "98-2" if other == "Zorblatt" else None), name)
        self.assertIn("LDL Cholesterol", names("LDL Cholestrol", "mg/dL"), "a similar name still offered")
        self.assertIn("Zorblatt", names("Zorblat", "mg/dL"), "with no LOINC code of its own")
        self.assertFalse(labmap.told_apart("Bilirubin, Total", "BILI"), "Total alone")
        self.assertTrue(labmap.told_apart("Bilirubin, Total", "Bilirubin, Direct") and labmap.told_apart("T4", "Free T4"))
        self.assertNotIn("LDL Cholesterol", names("LDL Cholesterol", "mg/dL", "18262-6"), "another LOINC code: direct LDL")

    def test_a_specimen_is_part_of_the_name(self):   # #569
        """A name with a specimen (blood, serum, urine...) isn't the same name written another way as one without it,
        or with another: "Ketones, Blood" is only a "Maybe" for a profile's "Ketones", which may be a urine series.
        "Blood" in "Red blood cells" names the cells."""
        pid = self.profile("Specimens")
        self.save(pid, self.previewed(pid, csv(["2026-01-05", "Ketones", "5", "mg/dL", "0-5", "", "", ""])))
        j = self.previewed(pid, csv(["2026-02-05", "\"Ketones, Blood\"", "4", "mg/dL", "0-5", "", "", ""]))
        e = self.entry(j, "Ketones, Blood")
        self.assertEqual(e["suggestions"][0], {"name": "Ketones", "unit": "mg/dL", "reason": "similar name, same unit", "sure": False})
        done = self.save(pid, j)
        self.assertEqual(done["lab_names"], {"matched": 0, "remembered": 0})
        self.assertEqual(self.points(pid, "Ketones"), [("2026-01-05", 5.0)])
        self.assertEqual(self.points(pid, "Ketones, Blood"), [("2026-02-05", 4.0)])
        for a, b in (("Ketones, Blood", "Ketones"), ("Ketones, Urine", "Ketones, Blood"), ("Glucose, Serum", "Glucose, Plasma"),
                     ("Protein, Ur", "Protein"), ("Glucose CSF", "Glucose")):
            self.assertFalse(labmap.same_name(a, b), (a, b))
            self.assertFalse(labmap.same_name(b, a), (b, a))
        for a, b in (("Ketones, Blood", "KETONES BLD"), ("Lead, Whole Blood", "Lead, Blood"), ("Red blood cells", "RBC"),
                     ("Vitamin D 25-Hydroxy", "VIT D 25-OH")):
            self.assertTrue(labmap.same_name(a, b), (a, b))

    # ---------------------------------------------------------------- confirming, and the choice sticking

    def test_confirmed_match_saves_under_the_series_and_sticks(self):
        pid = self.with_hemoglobin("Sticks")
        j = self.previewed(pid, csv(["2026-02-05", "HGB-XX", "12.0", "g/dL", "13.5 - 17.5", "L", "CBC", "718-7"]), c=self.ash)
        ref = self.entry(j, "HGB-XX")["ref"]
        done = self.save(pid, j, {ref: "Hemoglobin"}, c=self.ash)   # an editor confirms
        self.assertEqual(done["lab_names"], {"matched": 1, "remembered": 1})
        self.assertEqual(self.points(pid, "Hemoglobin"), [("2026-01-05", 13.1), ("2026-02-05", 12.0)])
        d = self.data(pid)
        self.assertNotIn("HGB-XX", [s["name"] for s in d["labs"]])
        item = next(e for e in d["events"] if e["k"] == "labitem" and e["t"].startswith("2026-02-05"))
        self.assertEqual(item["title"], "HGB-XX")                               # the result's own row: as reported
        self.assertEqual(d["details"][str(item["id"])]["rows"][0][0], "HGB-XX")
        db = dbm.connect()
        try:
            a = [x for x in activity.for_profile(db, pid, action="import") if x["detail"].get("job") == j["id"]]
            self.assertEqual(a[0]["detail"]["labs"], {"matched": 1, "remembered": 1})
        finally:
            db.close()

        # the next import of the same spelling: matched as before, without being asked
        j = self.previewed(pid, csv(["2026-03-05", "HGB-XX", "12.6", "g/dL", "13.5 - 17.5", "L", "CBC", "718-7"]))
        e = self.entry(j, "HGB-XX")
        self.assertEqual(e["before"], {"target": "Hemoglobin"})
        done = self.save(pid, j)   # nothing sent: the remembered match applies
        self.assertEqual(done["lab_names"], {"matched": 1, "remembered": 0})
        self.assertEqual(self.points(pid, "Hemoglobin"), [("2026-01-05", 13.1), ("2026-02-05", 12.0), ("2026-03-05", 12.6)])
        # ...but not in another profile, which never had the name: asked there (#927: the match is this profile's)
        other = self.profile("Elsewhere")
        j = self.previewed(other, csv(["2026-03-05", "HGB-XX", "12.6", "g/dL", "13.5 - 17.5", "L", "CBC", "718-7"]))
        self.assertIsNone(self.entry(j, "HGB-XX")["before"])
        self.save(other, j)
        self.assertEqual((self.points(other, "HGB-XX"), self.points(other, "Hemoglobin")), ([("2026-03-05", 12.6)], []))
        # a duplicate is still a duplicate: the series keeps its key, so the same file again adds nothing
        j = self.previewed(pid, csv(["2026-03-05", "HGB-XX", "12.6", "g/dL", "13.5 - 17.5", "L", "CBC", "718-7"]))
        self.assertFalse(j["new"])
        self.assertEqual(j["duplicates"]["results"], 1)

    def test_a_reviews_choice_is_its_profiles_and_one_it_offered(self):   # #927
        """A choice made at one profile's review is remembered for that profile only: another profile, which its
        chooser has no role on, is asked about the same name, and results typed in there aren't matched by it; an
        admin's match for every profile still is. A series the review didn't offer, and no test Sanguity's list knows,
        is refused with nothing saved; one it offered is taken in its own spelling."""
        theirs = self.profile("Ash edits 927")   # robin's, ash an editor
        db = dbm.connect()
        try:
            mine = prof.create_profile(db, "Robin alone 927 (fictional)", owner_id=self.uids["robin"])   # ash has no role here
        finally:
            db.close()
        name = "Zorblatt Thyro Index 927"
        row = lambda day, v: csv([day, name, v, "ng/dL", "0.8-1.8", "", "", ""])
        j = self.previewed(theirs, row("2026-03-01", "1.9"), c=self.ash)
        ref = self.entry(j, name)["ref"]
        r = self.ash.post(f"/api/profiles/{theirs}/jobs/{j['id']}/confirm", json={"labs": {ref: "Hemoglobin A1c"}}, headers=H)
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("isn't a series this review offered", r.json()["detail"])
        self.assertEqual(self.robin.get(f"/api/profiles/{theirs}/jobs/{j['id']}").json()["state"], "preview")
        done = self.save(theirs, j, {ref: "hemoglobin"}, c=self.ash)   # a test Sanguity's list knows, in its spelling
        self.assertEqual(done["lab_names"], {"matched": 1, "remembered": 1})
        self.assertEqual(self.points(theirs, "Hemoglobin"), [("2026-03-01", 1.9)])
        m = next(x for x in self.own(theirs) if x["name"] == name)
        self.assertEqual(m["target"], "Hemoglobin")
        self.assertNotIn("for", m)   # (which profile isn't said)
        self.assertFalse([x for x in self.ada.get("/api/labmaps").json() if x["name"] == name])   # (#1097: not Install's)
        # the other profile: asked, nothing applied; typed in, the name stays
        j = self.previewed(mine, row("2026-03-02", "1.4"))
        self.assertIsNone(self.entry(j, name)["before"])
        self.save(mine, j)
        self.assertEqual((self.points(mine, name), self.points(mine, "Hemoglobin")), ([("2026-03-02", 1.4)], []))
        typed = {"date": "2026-03-03", "test": name, "value": "1.2", "unit": "ng/dL", "range": "0.8-1.8"}
        self.assertEqual(self.robin.post(f"/api/profiles/{mine}/manual", json={"rows": [typed], "save": False}, headers=H).json()["rows"][0]["name"], name)
        # its own profile: matched as before, typed in too
        j = self.previewed(theirs, row("2026-03-04", "1.3"))
        self.assertEqual(self.entry(j, name)["before"], {"target": "Hemoglobin"})
        self.robin.post(f"/api/profiles/{theirs}/jobs/{j['id']}/discard", headers=H)
        self.assertEqual(self.robin.post(f"/api/profiles/{theirs}/manual", json={"rows": [typed], "save": False}, headers=H).json()["rows"][0]["name"],
                         "Hemoglobin")
        # an admin's match, for every profile, applies to both
        mid = self.remembered_as("Zorblatt Other 927", "Hemoglobin", checked=1, changed=1, unit="ng/dL")
        for pid in (theirs, mine):
            j = self.previewed(pid, csv(["2026-03-05", "Zorblatt Other 927", "1.1", "ng/dL", "0.8-1.8", "", "", ""]))
            self.assertEqual(self.entry(j, "Zorblatt Other 927")["before"], {"target": "Hemoglobin"})
            self.robin.post(f"/api/profiles/{pid}/jobs/{j['id']}/discard", headers=H)
        self.assertFalse(next(x for x in self.ada.get("/api/labmaps").json() if x["id"] == mid)["one_profile"])
        self.ada.delete(f"/api/labmaps/{mid}", headers=H)
        self.robin.delete(f"/api/profiles/{theirs}/labmaps/{m['id']}", headers=H)

    def test_version_41_gives_a_reviews_match_its_profile(self):   # #927
        """The upgrade to schema version 41: a match a review made is its import's profile's, keyed with it at start-up
        (labmap.scope_old), so it's applied there only; an admin's stays for every profile; one whose import is gone
        is for every profile but to check, unless #546's rule would make it."""
        import sqlite3
        d = tempfile.mkdtemp(prefix="sanguity-schema41-")
        self.addCleanup(shutil.rmtree, d, True)
        old = sqlite3.connect(os.path.join(d, "schema-40.db"), isolation_level=None)
        old.row_factory = sqlite3.Row
        for v, sql in enumerate(dbm.SCHEMA[:40], start=1):
            old.executescript("BEGIN;" + sql + f"PRAGMA user_version={v};COMMIT;")
        old.execute("INSERT INTO profiles(id, name, created) VALUES(7, 'p', 1)")
        old.execute("INSERT INTO imports(id, profile_id, at, origin, format) VALUES(70, 7, 1, 'upload', 'csv')")
        for lookup, imp, changed in (("review", 70, None), ("admin", 70, 50), ("gone", None, None)):
            old.execute("INSERT INTO labmaps(lookup, importer, source, target, created_name, created, import_id, changed, checked)"
                        " VALUES(?, 'csv', 's', 't', 'ines', 1, ?, ?, 1)", (lookup, imp, changed))
        dbm.upgrade(old)
        self.assertEqual([tuple(r) for r in old.execute("SELECT lookup, profile_id, checked FROM labmaps ORDER BY lookup")],
                         [("admin", None, 1), ("gone", None, None), ("review", 7, 1)])
        self.assertEqual(old.execute("SELECT v FROM meta WHERE k='labmaps_scope'").fetchone()[0], "1")
        old.close()
        # at start-up: a review's match made before, now its profile's, keyed with it
        theirs, mine = self.profile("Scoped 927"), self.profile("Unscoped 927")
        done = self.save(theirs, self.previewed(theirs, csv(["2026-01-05", "Hemoglobin", "13.1", "g/dL", "13.5 - 17.5", "L", "CBC", "718-7"])))
        db = dbm.connect()
        try:
            from server import atrest
            atrest.load_pepper(db)
            mid = db.execute("INSERT INTO labmaps(lookup, importer, source, target, created_name, created, checked, import_id, profile_id)"
                             " VALUES(?, 'csv', ?, ?, 'robin', 1, 1, ?, ?)",
                             (labmap.lookup("csv", "HGB-927", "g/dL", ""), crypto.enc(json.dumps({"name": "HGB-927", "unit": "g/dL", "loinc": ""})),
                              crypto.enc("Hemoglobin"), done["import_id"], theirs)).lastrowid
            dbm.meta_set(db, "labmaps_scope", "1")
            self.assertEqual(labmap.scope_old(db), 1)   # (the matches since are keyed with their profile already)
            self.assertIsNone(dbm.meta_get(db, "labmaps_scope"))
            self.assertEqual(labmap.scope_old(db), 0)   # once
        finally:
            db.close()
        for pid, before in ((theirs, {"target": "Hemoglobin"}), (mine, None)):
            j = self.previewed(pid, csv(["2026-02-05", "HGB-927", "12.0", "g/dL", "13.5 - 17.5", "L", "CBC", ""]))
            self.assertEqual(self.entry(j, "HGB-927")["before"], before, pid)
            self.robin.post(f"/api/profiles/{pid}/jobs/{j['id']}/discard", headers=H)
        self.robin.delete(f"/api/profiles/{theirs}/labmaps/{mid}", headers=H)

    def test_keep_as_new_is_remembered_as_kept_separate(self):
        pid = self.with_hemoglobin("Separate")
        j = self.previewed(pid, csv(["2026-02-05", "HGB-Y", "12.0", "g/dL", "13.5 - 17.5", "L", "CBC", "718-7"]))
        e = self.entry(j, "HGB-Y")
        self.assertEqual(e["suggestions"][0]["name"], "Hemoglobin")
        done = self.save(pid, j, {e["ref"]: None})
        self.assertEqual(done["lab_names"], {"matched": 0, "remembered": 1})
        self.assertEqual(self.points(pid, "HGB-Y"), [("2026-02-05", 12.0)])
        db = dbm.connect()
        try:
            rows = db.execute("SELECT target FROM labmaps WHERE importer='csv'").fetchall()
            mine = [m for m in labmap.listing(db, pid) if m["name"] == "HGB-Y"]
        finally:
            db.close()
        self.assertEqual(len(mine), 1)
        self.assertIsNone(mine[0]["target"])
        self.assertTrue(any(r["target"] is None for r in rows))
        # next time: kept separate as before; changing it at the preview changes that import only
        j = self.previewed(pid, csv(["2026-03-05", "HGB-Y", "12.4", "g/dL", "13.5 - 17.5", "L", "CBC", "718-7"]))
        e = self.entry(j, "HGB-Y")
        self.assertEqual(e["before"], {"target": None})
        self.save(pid, j, {e["ref"]: "Hemoglobin"})
        self.assertIn(("2026-03-05", 12.4), self.points(pid, "Hemoglobin"))
        db = dbm.connect()
        try:
            self.assertIsNone(next(m for m in labmap.listing(db, pid) if m["name"] == "HGB-Y")["target"])   # still kept separate
        finally:
            db.close()
        j = self.previewed(pid, csv(["2026-04-05", "HGB-Y", "12.8", "g/dL", "13.5 - 17.5", "L", "CBC", "718-7"]))
        self.save(pid, j)
        self.assertEqual(self.points(pid, "HGB-Y"), [("2026-02-05", 12.0), ("2026-04-05", 12.8)])

    def test_nothing_sent_nothing_remembered(self):
        pid = self.with_hemoglobin("Unsent")
        j = self.previewed(pid, csv(["2026-02-05", "HGB-Z", "12.0", "g/dL", "13.5 - 17.5", "L", "CBC", "718-7"]))
        self.save(pid, j)   # an API client that doesn't know about lab names: names stay as reported
        self.assertEqual(self.points(pid, "HGB-Z"), [("2026-02-05", 12.0)])
        db = dbm.connect()
        try:
            self.assertFalse([m for m in labmap.listing(db, pid) if m["name"] == "HGB-Z"])
        finally:
            db.close()

    def test_undo_forgets_the_matches_its_import_made(self):
        """#500: undoing the import whose review made a match forgets it, so the next import asks again; the history
        entry counts it for the question asked first. A match another import has been saved with since stays."""
        pid = self.with_hemoglobin("Undone")
        hist = lambda: self.robin.get(f"/api/profiles/{pid}/imports").json()["imports"][0]
        j = self.previewed(pid, csv(["2026-02-05", "HGB-U", "12.0", "g/dL", "13.5 - 17.5", "L", "CBC", "718-7"]))
        done = self.save(pid, j, {self.entry(j, "HGB-U")["ref"]: "Hemoglobin"})
        self.assertEqual(hist()["lab_matches"], 1)
        r = self.robin.post(f"/api/profiles/{pid}/imports/{done['import_id']}/undo", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        j = self.previewed(pid, csv(["2026-02-05", "HGB-U", "12.0", "g/dL", "13.5 - 17.5", "L", "CBC", "718-7"]))
        self.assertIsNone(self.entry(j, "HGB-U")["before"])   # asked again
        # matched again, then used by another of its imports before the first is undone: it stays
        done = self.save(pid, j, {self.entry(j, "HGB-U")["ref"]: "Hemoglobin"})
        k = self.previewed(pid, csv(["2026-03-05", "HGB-U", "12.4", "g/dL", "13.5 - 17.5", "L", "CBC", "718-7"]))
        self.save(pid, k)
        self.assertEqual(next(i for i in self.robin.get(f"/api/profiles/{pid}/imports").json()["imports"]
                              if i["id"] == done["import_id"])["lab_matches"], 0)
        self.robin.post(f"/api/profiles/{pid}/imports/{done['import_id']}/undo", headers=H)
        j = self.previewed(pid, csv(["2026-04-05", "HGB-U", "12.8", "g/dL", "13.5 - 17.5", "L", "CBC", "718-7"]))
        self.assertEqual(self.entry(j, "HGB-U")["before"], {"target": "Hemoglobin"})
        self.robin.post(f"/api/profiles/{pid}/jobs/{j['id']}/discard", headers=H)

    def test_deleting_a_profile_forgets_the_matches_its_imports_made(self):   # #591, #927
        """A profile deleted takes the lab-name matches its own reviews made with it (the name as reported, who
        confirmed it), so they're no longer listed: each is that profile's own (#927), applied nowhere else, one an
        admin has kept too. Another profile's own, and an admin's for every profile, stay."""
        gone = self.with_hemoglobin("Deleted")
        other = self.with_hemoglobin("Staying")
        j = self.previewed(gone, csv(["2026-02-05", "HGB-D1", "12.0", "g/dL", "13.5 - 17.5", "L", "CBC", "718-7"],
                                     ["2026-02-05", "HGB-D2", "12.0", "g/dL", "13.5 - 17.5", "L", "CBC", "718-8"],
                                     ["2026-02-05", "HGB-D3", "12.0", "g/dL", "13.5 - 17.5", "L", "CBC", "718-9"]))
        self.save(gone, j, {self.entry(j, n)["ref"]: "Hemoglobin" for n in ("HGB-D1", "HGB-D2", "HGB-D3")})
        # HGB-D2: the other profile has it too, and is asked; HGB-D3: kept by an admin; HGB-D5: the other profile's own;
        # HGB-D6: an admin's, for every profile
        k = self.previewed(other, csv(["2026-03-05", "HGB-D2", "12.4", "g/dL", "13.5 - 17.5", "L", "CBC", "718-8"],
                                      ["2026-03-05", "HGB-D5", "12.4", "g/dL", "13.5 - 17.5", "L", "CBC", "718-5"]))
        self.assertIsNone(self.entry(k, "HGB-D2")["before"])
        self.save(other, k, {self.entry(k, "HGB-D5")["ref"]: "Hemoglobin"})
        everyone = self.remembered_as("HGB-D6", "Hemoglobin", checked=1, changed=1, unit="g/dL")
        def mine():
            db = dbm.connect()
            try:
                return {m["name"]: m for m in labmap.listing(db) + labmap.listing(db, gone) + labmap.listing(db, other) if m["name"].startswith("HGB-D")}
            finally:
                db.close()
        self.assertEqual(sorted(mine()), ["HGB-D1", "HGB-D2", "HGB-D3", "HGB-D5", "HGB-D6"])
        self.assertEqual(self.robin.patch(f"/api/profiles/{gone}/labmaps/{mine()['HGB-D3']['id']}", json={"target": "Hemoglobin", "keep": True},
                                          headers=H).status_code, 200)
        r = self.robin.request("DELETE", f"/api/profiles/{gone}", json={"confirm_name": "Deleted (fictional)"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["lab_matches"], 3)
        self.assertEqual(sorted(mine()), ["HGB-D5", "HGB-D6"])
        db = dbm.connect()
        try:   # and the activity log says how many went
            d = json.loads(crypto.dec(db.execute("SELECT detail FROM activity WHERE action='profile.delete' ORDER BY id DESC").fetchone()[0]))
        finally:
            db.close()
        self.assertEqual(d["lab_matches"], 3)
        # the admin's still applies to the other profile
        k = self.previewed(other, csv(["2026-04-05", "HGB-D6", "12.4", "g/dL", "13.5 - 17.5", "L", "CBC", ""]))
        self.assertEqual(self.entry(k, "HGB-D6")["before"], {"target": "Hemoglobin"})
        self.robin.post(f"/api/profiles/{other}/jobs/{k['id']}/discard", headers=H)
        self.ada.delete(f"/api/labmaps/{everyone}", headers=H)

    def test_a_match_remembered_before_546_is_checked_first(self):   # #569
        """A match remembered before #546 (checked NULL) that #546's rule wouldn't make, Vitamin B6 filled in as
        Vitamin B1, is to check: not applied to an import (its review offers it as a "Maybe") nor to results typed
        in, and listed first for admins; one the rule would make still applies. Once an admin keeps it, it applies."""
        pid = self.profile("To check")
        self.save(pid, self.previewed(pid, csv(["2026-01-05", "Vitamin B1", "120", "nmol/L", "70-180", "", "", ""])))
        db = dbm.connect()
        try:
            from server import atrest
            atrest.load_pepper(db)
            old = {}
            for importer, name, target in (("csv", "Vitamin B6", "Vitamin B1"), ("csv", "VIT. B1", "Vitamin B1"),
                                           ("manual", "Vitamin B6", "Vitamin B1"), ("csv", "Ketones, Blood", "Ketones")):
                old[(importer, name)] = db.execute(
                    "INSERT INTO labmaps(lookup, importer, source, target, created_name, created) VALUES(?, ?, ?, ?, 'robin', 1)",
                    (labmap.lookup(importer, name, "nmol/L", ""), importer,
                     crypto.enc(json.dumps({"name": name, "unit": "nmol/L", "loinc": ""})), crypto.enc(target))).lastrowid
        finally:
            db.close()
        j = self.previewed(pid, csv(["2026-02-05", "Vitamin B6", "40", "nmol/L", "20-125", "", "", ""],
                                    ["2026-02-05", "VIT. B1", "130", "nmol/L", "70-180", "", "", ""]))
        b6, b1 = self.entry(j, "Vitamin B6"), self.entry(j, "VIT. B1")
        self.assertIsNone(b6["before"])
        self.assertEqual(b6["check"], {"target": "Vitamin B1"})
        self.assertEqual(b1["before"], {"target": "Vitamin B1"})   # the same name written another way: applied
        self.assertNotIn("check", b1)
        done = self.save(pid, j)
        self.assertEqual(done["lab_names"], {"matched": 1, "remembered": 0})
        self.assertEqual(self.points(pid, "Vitamin B6"), [("2026-02-05", 40.0)])
        self.assertEqual(self.points(pid, "Vitamin B1"), [("2026-01-05", 120.0), ("2026-02-05", 130.0)])
        row = {"date": "2026-02-06", "test": "Vitamin B6", "value": "41", "unit": "nmol/L", "range": "20-125"}
        self.assertEqual(self.robin.post(f"/api/profiles/{pid}/manual", json={"rows": [row], "save": False}, headers=H).json()["rows"][0]["name"],
                         "Vitamin B6")
        # the list: those to check first, flagged; the others not
        listed = self.ada.get("/api/labmaps").json()
        flagged = [m["id"] for m in listed if m["check"]]
        self.assertEqual(listed[:len(flagged)], [m for m in listed if m["check"]])
        self.assertEqual(set(flagged), {old[("csv", "Vitamin B6")], old[("manual", "Vitamin B6")], old[("csv", "Ketones, Blood")]})
        self.assertFalse(next(m for m in listed if m["id"] == old[("csv", "VIT. B1")])["check"])
        # an admin keeps it: no longer to check, and applied from then on
        r = self.ada.patch(f"/api/labmaps/{old[('csv', 'Vitamin B6')]}", json={"target": "Vitamin B1"}, headers=H)
        self.assertEqual((r.status_code, r.json()["check"], r.json()["target"]), (200, False, "Vitamin B1"), r.text)
        j = self.previewed(pid, csv(["2026-03-05", "Vitamin B6", "42", "nmol/L", "20-125", "", "", ""]))
        e = self.entry(j, "Vitamin B6")
        self.assertEqual(e["before"], {"target": "Vitamin B1"})
        self.assertNotIn("check", e)
        self.save(pid, j)
        self.assertIn(("2026-03-05", 42.0), self.points(pid, "Vitamin B1"))
        for k in old.values():
            self.ada.delete(f"/api/labmaps/{k}", headers=H)

    def remembered_as(self, name, target, checked=None, changed=None, unit="nmol/L", pid=None):
        """A remembered choice made up here: checked None and not one #546's rule makes, it's to check (#569). pid:
        that profile's own (#927), else for every profile."""
        db = dbm.connect()
        try:
            from server import atrest
            atrest.load_pepper(db)
            return db.execute("INSERT INTO labmaps(lookup, importer, source, target, created_name, created, checked, changed, changed_name,"
                              " profile_id) VALUES(?, 'csv', ?, ?, 'robin', 1, ?, ?, ?, ?)",
                              (labmap.lookup("csv", name, unit, "", pid), crypto.enc(json.dumps({"name": name, "unit": unit, "loinc": ""})),
                               crypto.enc(target), checked, changed, changed and "ada", pid)).lastrowid
        finally:
            db.close()

    def labmap_row(self, mid):
        db = dbm.connect()
        try:
            r = db.execute("SELECT * FROM labmaps WHERE id=?", (mid,)).fetchone()
            return r and {**dict(r), "target": crypto.dec(r["target"]) if r["target"] else None}
        finally:
            db.close()

    def test_a_preview_planned_before_a_match_was_to_check_doesnt_apply_it(self):   # #575
        """A review waiting while a match it applied becomes one to check (as at the upgrade to schema version 38):
        saved, the match isn't applied; read again, it's a "Maybe"."""
        pid = self.profile("Flagged meanwhile")
        self.save(pid, self.previewed(pid, csv(["2026-01-05", "Vitamin B1", "120", "nmol/L", "70-180", "", "", ""])))
        mid = self.remembered_as("Vitamin B6 575", "Vitamin B1", checked=1)
        b6 = lambda day, v: csv([day, "Vitamin B6 575", v, "nmol/L", "20-125", "", "", ""])
        a, b = self.previewed(pid, b6("2026-02-05", "40")), self.previewed(pid, b6("2026-02-06", "41"))
        for j in (a, b):
            self.assertEqual(self.entry(j, "Vitamin B6 575")["before"], {"target": "Vitamin B1"})
        db = dbm.connect()
        try:
            db.execute("UPDATE labmaps SET checked=NULL WHERE id=?", (mid,))   # to check now
        finally:
            db.close()
        done = self.save(pid, a)   # confirmed as it was shown: the box filled, nothing changed, nothing sent
        self.assertEqual(done["lab_names"], {"matched": 0, "remembered": 0})
        self.assertEqual(self.points(pid, "Vitamin B6 575"), [("2026-02-05", 40.0)])
        self.assertEqual(self.points(pid, "Vitamin B1"), [("2026-01-05", 120.0)])
        e = self.entry(self.robin.get(f"/api/profiles/{pid}/jobs/{b['id']}").json(), "Vitamin B6 575")   # the other, read again
        self.assertEqual((e["before"], e["check"]), (None, {"target": "Vitamin B1"}))
        self.save(pid, b)
        self.assertEqual(self.points(pid, "Vitamin B6 575"), [("2026-02-05", 40.0), ("2026-02-06", 41.0)])
        self.ada.delete(f"/api/labmaps/{mid}", headers=H)

    def test_a_stored_plan_doesnt_apply_a_match_forgotten_or_changed_since(self):   # #583
        """A review waiting while an admin forgets one match it applied and changes another (both checked): saved as
        it was shown, each result keeps its own name and the changed match isn't marked used; another waiting review,
        read again, is planned again: the forgotten name asked, the changed one matched to the admin's new series."""
        pid = self.profile("Changed meanwhile")
        self.save(pid, self.previewed(pid, csv(["2026-01-05", "Magnesium", "2.0", "mg/dL", "1.7-2.2", "", "", ""],
                                               ["2026-01-05", "Potassium", "4.1", "mg/dL", "3.5-5.1", "", "", ""],
                                               ["2026-01-05", "Calcium", "9.4", "mg/dL", "8.6-10.3", "", "", ""])))
        mg = self.remembered_as("Magnesium RBC 583", "Magnesium", checked=1, unit="mg/dL")
        k = self.remembered_as("Potassium RBC 583", "Potassium, Blood", checked=1, unit="mg/dL")
        rbc = lambda day: csv([day, "Magnesium RBC 583", "5.0", "mg/dL", "4.2-6.8", "", "", ""],
                              [day, "Potassium RBC 583", "90", "mg/dL", "80-100", "", "", ""])
        a, b = self.previewed(pid, rbc("2026-02-05")), self.previewed(pid, rbc("2026-02-06"))
        for j in (a, b):
            self.assertEqual([self.entry(j, n)["before"] for n in ("Magnesium RBC 583", "Potassium RBC 583")],
                             [{"target": "Magnesium"}, {"target": "Potassium, Blood"}])
        self.assertEqual(self.ada.delete(f"/api/labmaps/{mg}", headers=H).status_code, 200)   # Forget
        self.assertEqual(self.ada.patch(f"/api/labmaps/{k}", json={"target": "Calcium"}, headers=H).status_code, 200)   # Change
        done = self.save(pid, a)   # confirmed as it was shown, nothing sent
        self.assertEqual(done["lab_names"], {"matched": 0, "remembered": 0})
        self.assertEqual(self.points(pid, "Magnesium RBC 583"), [("2026-02-05", 5.0)])
        self.assertEqual(self.points(pid, "Potassium RBC 583"), [("2026-02-05", 90.0)])
        for n, v in (("Magnesium", 2.0), ("Potassium, Blood", 4.1), ("Calcium", 9.4)):
            self.assertEqual(self.points(pid, n), [("2026-01-05", v)])
        self.assertIsNone(self.labmap_row(k)["used"])   # the stale match isn't marked used
        j = self.robin.get(f"/api/profiles/{pid}/jobs/{b['id']}").json()   # the other, read again: planned again
        self.assertEqual([self.entry(j, n)["before"] for n in ("Magnesium RBC 583", "Potassium RBC 583")], [None, {"target": "Calcium"}])
        self.save(pid, b)
        self.assertEqual(self.points(pid, "Calcium"), [("2026-01-05", 9.4), ("2026-02-06", 90.0)])
        self.assertEqual(self.points(pid, "Magnesium RBC 583"), [("2026-02-05", 5.0), ("2026-02-06", 5.0)])
        self.assertIsNotNone(self.labmap_row(k)["used"])
        self.ada.delete(f"/api/labmaps/{k}", headers=H)

    def test_any_change_to_the_matches_plans_a_waiting_review_again(self):   # #583
        """The stamp a preview is read against changes with every change to labmaps, not only how many are to check:
        an admin's Change or Keep of a checked match, a Forget, and a choice remembered by another review."""
        pid = self.profile("Stamped")
        db = dbm.connect()
        try:
            stamp = lambda: jobs._stamp(db, pid)
            before = stamp()
            mid = self.remembered_as("Vitamin B6 stamp", "Vitamin B1", checked=1)
            labmap.change(db, mid, "Vitamin B12", {"id": self.uids["ada"], "username": "ada"})
            self.assertNotEqual(stamp(), before)
            before = stamp()
            labmap.change(db, mid, "Vitamin B12", {"id": self.uids["ada"], "username": "ada"})   # Keep
            self.assertNotEqual(stamp(), before)
            before = stamp()
            labmap.used(db, [mid])   # an import saved with it: nothing to plan again
            self.assertEqual(stamp(), before)
            labmap.remove(db, mid)
            self.assertNotEqual(stamp(), before)
        finally:
            db.close()

    def test_keep_only_the_match_shown(self):   # #575
        """Keep sends the target it showed: once another admin has changed the match, it's refused, not the old
        look-alike put back as checked."""
        mid = self.remembered_as("Vitamin B6 keep", "Vitamin B1")
        self.assertTrue(next(m for m in self.ada.get("/api/labmaps").json() if m["id"] == mid)["check"])
        r = self.ada.patch(f"/api/labmaps/{mid}", json={"target": None}, headers=H)   # another admin: kept separate
        self.assertEqual(r.status_code, 200, r.text)
        r = self.ada.patch(f"/api/labmaps/{mid}", json={"target": "Vitamin B1", "keep": True}, headers=H)   # Keep, as drawn before
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("changed meanwhile", r.json()["detail"])
        self.assertIsNone(self.labmap_row(mid)["target"])
        r = self.ada.patch(f"/api/labmaps/{mid}", json={"target": None, "keep": True}, headers=H)   # what it is now: kept
        self.assertEqual((r.status_code, r.json()["target"]), (200, None), r.text)
        db = dbm.connect()
        try:   # logged as kept, the change before it as a change; the refused Keep not at all
            log = [json.loads(crypto.dec(r["detail"])) for r in db.execute(
                "SELECT detail FROM activity WHERE action='labmap.change' AND profile_id IS NULL ORDER BY id")]
        finally:
            db.close()
        mine = [(x["from"], x["to"], x.get("kept")) for x in log if x["name"] == "Vitamin B6 keep"]
        self.assertEqual(mine, [("Vitamin B1", None, None), (None, None, True)])
        self.ada.delete(f"/api/labmaps/{mid}", headers=H)

    def test_version_38_checks_what_an_admin_changed(self):   # #575
        """The upgrade to schema version 38 marks a match an admin had changed as checked (theirs, not one to check);
        one never changed is left to check."""
        import sqlite3
        d = tempfile.mkdtemp(prefix="sanguity-schema38-")
        self.addCleanup(shutil.rmtree, d, True)
        db = sqlite3.connect(os.path.join(d, "schema-37.db"), isolation_level=None)
        db.row_factory = sqlite3.Row
        for v, sql in enumerate(dbm.SCHEMA[:37], start=1):
            db.executescript("BEGIN;" + sql + f"PRAGMA user_version={v};COMMIT;")
        db.execute("INSERT INTO labmaps(lookup, importer, source, target, created_name, created) VALUES('a', 'csv', 's', 't', 'ines', 1)")
        db.execute("INSERT INTO labmaps(lookup, importer, source, target, created_name, created, changed, changed_name)"
                   " VALUES('b', 'csv', 's', 't', 'ines', 1, 50, 'ada')")
        dbm.upgrade(db)
        self.assertEqual([tuple(r) for r in db.execute("SELECT lookup, checked FROM labmaps ORDER BY lookup")], [("a", None), ("b", 50)])
        db.close()

    def test_a_reviews_choice_for_a_name_to_check_is_remembered(self):   # #575, #927
        """The review says a choice is used for later imports: one for a name whose match of the profile's own is to
        check (remembered before #546, given its profile by the upgrade to schema version 41) replaces it, checked, and
        undone with its import; one for a name whose match for every profile is to check is remembered as the
        profile's own (#927), that match left for admins to check; one an admin has kept meanwhile stays the admin's."""
        pid = self.profile("Chosen over")
        self.save(pid, self.previewed(pid, csv(["2026-01-05", "Vitamin B1", "120", "nmol/L", "70-180", "", "", ""])))
        mid = self.remembered_as("Vitamin B6 pick", "Vitamin B1", pid=pid)
        j = self.previewed(pid, csv(["2026-02-05", "Vitamin B6 pick", "40", "nmol/L", "20-125", "", "", ""]))
        e = self.entry(j, "Vitamin B6 pick")
        self.assertEqual(e["check"], {"target": "Vitamin B1"})
        done = self.save(pid, j, {e["ref"]: None})   # kept as a series of its own, by hand
        self.assertEqual(done["lab_names"], {"matched": 0, "remembered": 1})
        row = self.labmap_row(mid)
        self.assertEqual((row["target"], row["checked"] is not None, row["import_id"]), (None, True, done["import_id"]))
        j = self.previewed(pid, csv(["2026-03-05", "Vitamin B6 pick", "42", "nmol/L", "20-125", "", "", ""]))
        e = self.entry(j, "Vitamin B6 pick")
        self.assertEqual(e["before"], {"target": None})
        self.assertNotIn("check", e)
        self.robin.post(f"/api/profiles/{pid}/jobs/{j['id']}/discard", headers=H)
        self.robin.delete(f"/api/profiles/{pid}/labmaps/{mid}", headers=H)
        # one for every profile, to check: the review's choice is the profile's own, the admins' to check stays
        everyone = self.remembered_as("Vitamin B6 pick", "Vitamin B1")
        j = self.previewed(pid, csv(["2026-03-06", "Vitamin B6 pick", "43", "nmol/L", "20-125", "", "", ""]))
        e = self.entry(j, "Vitamin B6 pick")
        self.assertEqual(e["check"], {"target": "Vitamin B1"})
        done = self.save(pid, j, {e["ref"]: None})
        self.assertEqual(done["lab_names"], {"matched": 0, "remembered": 1})
        row = self.labmap_row(everyone)
        self.assertEqual((row["target"], row["checked"], row["profile_id"]), ("Vitamin B1", None, None))
        db = dbm.connect()
        try:
            own = db.execute("SELECT id, target, profile_id FROM labmaps WHERE import_id=?", (done["import_id"],)).fetchone()
        finally:
            db.close()
        self.assertEqual((own["target"], own["profile_id"]), (None, pid))
        self.assertTrue(next(m for m in self.ada.get("/api/labmaps").json() if m["id"] == everyone)["check"])
        self.robin.delete(f"/api/profiles/{pid}/labmaps/{own['id']}", headers=H)
        self.ada.delete(f"/api/labmaps/{everyone}", headers=H)
        # an admin keeps it while a review is waiting: the admin's stays
        mid = self.remembered_as("Vitamin B6 pick", "Vitamin B1", pid=pid)
        j = self.previewed(pid, csv(["2026-04-05", "Vitamin B6 pick", "44", "nmol/L", "20-125", "", "", ""]))
        self.assertEqual(self.robin.patch(f"/api/profiles/{pid}/labmaps/{mid}", json={"target": "Vitamin B1", "keep": True}, headers=H).status_code, 200)
        done = self.save(pid, j, {self.entry(j, "Vitamin B6 pick")["ref"]: None})
        self.assertEqual(done["lab_names"]["remembered"], 0)
        self.assertEqual(self.labmap_row(mid)["target"], "Vitamin B1")
        self.robin.delete(f"/api/profiles/{pid}/labmaps/{mid}", headers=H)

    # ---------------------------------------------------------------- Install's: admins only, logged

    def test_only_admins_edit_installs_list(self):
        """Install's list is the matches for every profile, made by admins before #927 (#1097): an admin changes or
        removes one there; a profile's own isn't listed or changed there."""
        pid = self.with_hemoglobin("Admins")
        own = self.previewed(pid, csv(["2026-02-04", "HGB-A own", "12.0", "g/dL", "13.5 - 17.5", "L", "CBC", ""]))
        self.save(pid, own, {self.entry(own, "HGB-A own")["ref"]: "Hemoglobin"})
        mid = self.remembered_as("HGB-A", "Hemoglobin", checked=1, unit="g/dL")
        for c in (self.robin, self.ash):   # the profile's owner and editor
            self.assertEqual(c.get("/api/labmaps").status_code, 403)
        listed = self.ada.get("/api/labmaps").json()
        self.assertFalse([x for x in listed if x["name"] == "HGB-A own"])   # (#1097: the profile's own, not Install's)
        mine = next(x for x in self.own(pid) if x["name"] == "HGB-A own")
        self.assertEqual(self.ada.patch(f"/api/labmaps/{mine['id']}", json={"target": None}, headers=H).status_code, 404)
        self.assertEqual(self.ada.delete(f"/api/labmaps/{mine['id']}", headers=H).status_code, 404)
        m = next(x for x in listed if x["name"] == "HGB-A")
        self.assertEqual((m["id"], m["target"], m["unit"], m["importer"], m["one_profile"]), (mid, "Hemoglobin", "g/dL", "csv", False))
        self.assertNotIn("results", m)   # (whose would they be)
        self.assertIn("CSV", m["label"])
        for c in (self.robin, self.ash):
            self.assertEqual(c.patch(f"/api/labmaps/{m['id']}", json={"target": "WBC"}, headers=H).status_code, 403)
            self.assertEqual(c.delete(f"/api/labmaps/{m['id']}", headers=H).status_code, 403)
            self.assertEqual(c.delete(f"/api/profiles/{pid}/labmaps/{m['id']}", headers=H).status_code, 404)   # (not the profile's)
        self.assertEqual(self.ada.patch(f"/api/labmaps/{m['id']}", json={"target": "WBC"}).status_code, 403)   # CSRF header
        r = self.ada.patch(f"/api/labmaps/{m['id']}", json={"target": "Hemoglobin "}, headers=H)
        self.assertEqual((r.status_code, r.json()["target"], r.json()["changed_by"]), (200, "Hemoglobin", "ada"), r.text)
        r = self.ada.patch(f"/api/labmaps/{m['id']}", json={"target": None}, headers=H)
        self.assertIsNone(r.json()["target"])
        self.assertEqual(self.ada.patch("/api/labmaps/999999", json={"target": None}, headers=H).status_code, 404)
        # the change applies to the next import
        j = self.previewed(pid, csv(["2026-03-05", "HGB-A", "12.1", "g/dL", "13.5 - 17.5", "L", "CBC", ""]))
        self.assertEqual(self.entry(j, "HGB-A")["before"], {"target": None})
        self.assertEqual(self.ada.delete(f"/api/labmaps/{m['id']}", headers=H).status_code, 200)
        self.assertFalse([x for x in self.ada.get("/api/labmaps").json() if x["id"] == m["id"]])
        j2 = self.previewed(pid, csv(["2026-03-06", "HGB-A", "12.2", "g/dL", "13.5 - 17.5", "L", "CBC", ""]))
        self.assertIsNone(self.entry(j2, "HGB-A")["before"])   # forgotten: asked again
        db = dbm.connect()
        try:
            log = [(r["action"], json.loads(crypto.dec(r["detail"]))) for r in db.execute(
                "SELECT action, detail FROM activity WHERE action LIKE 'labmap.%' AND profile_id IS NULL ORDER BY id")]
        finally:
            db.close()
        mine = [x for x in log if x[1]["name"] == "HGB-A"]
        self.assertEqual([a for a, _ in mine], ["labmap.change", "labmap.change", "labmap.remove"])
        self.assertEqual((mine[1][1]["from"], mine[1][1]["to"]), ("Hemoglobin", None))
        for x in (j, j2):
            self.robin.post(f"/api/profiles/{pid}/jobs/{x['id']}/discard", headers=H)

    # ---------------------------------------------------------------- a profile's own, on Settings (#1097)

    def test_a_profiles_own_for_its_owners_and_editors(self):
        """Each profile's matches are its owners' and editors' to see and change (its viewers only see them); a member
        of another profile, and an admin with no role on it, are refused (not found); each match counts the results
        it covers and from how many imports."""
        pid = self.with_hemoglobin("Own 1097")
        a = self.previewed(pid, csv(["2026-02-05", "HGB-O", "12.0", "g/dL", "13.5 - 17.5", "L", "CBC", ""],
                                    ["2026-02-06", "HGB-O", "12.2", "g/dL", "13.5 - 17.5", "L", "CBC", ""]))
        self.save(pid, a, {self.entry(a, "HGB-O")["ref"]: "Hemoglobin"})
        self.save(pid, self.previewed(pid, csv(["2026-03-05", "HGB-O", "12.4", "g/dL", "13.5 - 17.5", "L", "CBC", ""])))   # matched as before
        db = dbm.connect()
        try:
            db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (self.uids["vera"], pid))
            elsewhere = prof.create_profile(db, "Olga's 1097 (fictional)", owner_id=self.uids["olga"])
        finally:
            db.close()
        for c in (self.robin, self.ash, self.vera):
            got = c.get(f"/api/profiles/{pid}/labmaps").json()
            m = next(x for x in got["matches"] if x["name"] == "HGB-O")
            self.assertEqual((m["target"], m["results"], m["imports"], m["created_by"]), ("Hemoglobin", 3, 2, "robin"))
            self.assertEqual(got["can_change"], c is not self.vera)
            self.assertIn({"name": "Hemoglobin", "unit": "g/dL"}, got["tests"])
            self.assertNotIn("HGB-O", [t["name"] for t in got["tests"]])   # (charted as Hemoglobin)
        self.assertEqual(self.vera.patch(f"/api/profiles/{pid}/labmaps/{m['id']}", json={"target": None}, headers=H).status_code, 403)
        self.assertEqual(self.vera.delete(f"/api/profiles/{pid}/labmaps/{m['id']}", headers=H).status_code, 403)
        self.assertEqual(self.vera.post(f"/api/profiles/{pid}/labmaps", json={"name": "Hemoglobin", "target": None}, headers=H).status_code, 403)
        for c in (self.olga, self.ada):   # another profile's member; an admin with no role here
            self.assertEqual(c.get(f"/api/profiles/{pid}/labmaps").status_code, 404)
            self.assertEqual(c.patch(f"/api/profiles/{pid}/labmaps/{m['id']}", json={"target": None}, headers=H).status_code, 404)
            self.assertEqual(c.delete(f"/api/profiles/{pid}/labmaps/{m['id']}", headers=H).status_code, 404)
        self.assertEqual(self.olga.get(f"/api/profiles/{elsewhere}/labmaps").json()["matches"], [])   # (another's not there)
        self.assertEqual(self.olga.patch(f"/api/profiles/{elsewhere}/labmaps/{m['id']}", json={"target": None}, headers=H).status_code, 404)
        self.assertFalse([x for x in self.ada.get("/api/labmaps").json() if x["name"] == "HGB-O"])   # admins: Install's only

    def lab_names(self, pid):
        return sorted({s["name"] for s in self.data(pid)["labs"]})

    def test_a_change_refiles_saved_results_and_undo_puts_them_back(self):
        """Change a match and re-file: the results saved under it are charted under the new test (Labs, the summary
        and the calendar read the records as put together), the records themselves untouched; Undo puts the match
        and the results back exactly. Not re-filed: they stay. Later imports follow the match, and an import's Undo
        still takes away only its own results."""
        pid = self.with_hemoglobin("Refile 1097")
        self.save(pid, self.previewed(pid, csv(["2026-01-06", "Hematocrit", "41", "%", "40-50", "", "CBC", ""])))
        a = self.previewed(pid, csv(["2026-02-05", "HGB-R", "12.0", "g/dL", "13.5 - 17.5", "L", "CBC", ""]))
        first = self.save(pid, a, {self.entry(a, "HGB-R")["ref"]: "Hemoglobin"})
        second = self.save(pid, self.previewed(pid, csv(["2026-03-05", "HGB-R", "12.4", "g/dL", "13.5 - 17.5", "L", "CBC", ""])))
        m = next(x for x in self.own(pid) if x["name"] == "HGB-R")
        db = dbm.connect()
        try:
            stored = [tuple(r) for r in db.execute("SELECT id, body FROM series WHERE profile_id=? ORDER BY id", (pid,))]
        finally:
            db.close()
        # changed without re-filing: saved results stay; only later imports follow
        r = self.ash.patch(f"/api/profiles/{pid}/labmaps/{m['id']}", json={"target": "Hematocrit", "move": False}, headers=H)
        self.assertEqual((r.status_code, r.json()["results"]), (200, 0), r.text)
        self.assertEqual(self.points(pid, "Hemoglobin"), [("2026-01-05", 13.1), ("2026-02-05", 12.0), ("2026-03-05", 12.4)])
        self.assertEqual(self.ash.post(f"/api/profiles/{pid}/labmaps/undo/{r.json()['change']}", headers=H).status_code, 200)
        # re-filed: under Hematocrit now, everywhere the records are read
        r = self.ash.patch(f"/api/profiles/{pid}/labmaps/{m['id']}", json={"target": "Hematocrit", "move": True}, headers=H)
        self.assertEqual((r.status_code, r.json()["results"], r.json()["series"]), (200, 2, 2), r.text)
        self.assertEqual(self.points(pid, "Hemoglobin"), [("2026-01-05", 13.1)])
        self.assertEqual(self.points(pid, "Hematocrit"), [("2026-01-06", 41.0), ("2026-02-05", 12.0), ("2026-03-05", 12.4)])
        db = dbm.connect()
        try:
            from server import calendar, records
            self.assertEqual(stored, [tuple(r) for r in db.execute("SELECT id, body FROM series WHERE profile_id=? ORDER BY id", (pid,))])   # untouched
            self.assertEqual(sorted(len(s["pts"]) for s in calendar._series(db, pid) if s["name"] == "Hematocrit"), [1, 1, 1])
            labs = records.assemble(db, pid)["labs"]   # what the summary, share links and exports as shown read
            self.assertEqual(sorted(p[0][:10] for s in labs if s["name"] == "Hematocrit" for p in s["pts"]), ["2026-01-06", "2026-02-05", "2026-03-05"])
        finally:
            db.close()
        self.assertEqual(next(x for x in self.own(pid) if x["id"] == m["id"])["target"], "Hematocrit")
        # a later import follows the match; the same file again is still a duplicate
        k = self.previewed(pid, csv(["2026-04-05", "HGB-R", "12.6", "g/dL", "13.5 - 17.5", "L", "CBC", ""]))
        self.assertEqual(self.entry(k, "HGB-R")["before"], {"target": "Hematocrit"})
        self.save(pid, k)
        dup = self.previewed(pid, csv(["2026-03-05", "HGB-R", "12.4", "g/dL", "13.5 - 17.5", "L", "CBC", ""]))
        self.assertFalse(dup["new"])
        self.robin.post(f"/api/profiles/{pid}/jobs/{dup['id']}/discard", headers=H)
        # an import's Undo after a re-file takes its own results only
        self.assertEqual(self.robin.post(f"/api/profiles/{pid}/imports/{second['import_id']}/undo", headers=H).status_code, 200)
        self.assertEqual(self.points(pid, "Hematocrit"), [("2026-01-06", 41.0), ("2026-02-05", 12.0), ("2026-04-05", 12.6)])
        # Keep as its own test splits it back out; the re-file's Undo is refused while a later change stands
        r2 = self.robin.patch(f"/api/profiles/{pid}/labmaps/{m['id']}", json={"target": None, "move": True}, headers=H)
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertEqual(self.points(pid, "HGB-R"), [("2026-02-05", 12.0), ("2026-04-05", 12.6)])
        self.assertEqual(self.robin.post(f"/api/profiles/{pid}/labmaps/undo/{r.json()['change']}", headers=H).status_code, 409)
        self.assertEqual(self.robin.post(f"/api/profiles/{pid}/labmaps/undo/{r2.json()['change']}", headers=H).status_code, 200)
        self.assertEqual(self.points(pid, "Hematocrit"), [("2026-01-06", 41.0), ("2026-02-05", 12.0), ("2026-04-05", 12.6)])
        self.assertEqual(self.robin.post(f"/api/profiles/{pid}/labmaps/undo/{r.json()['change']}", headers=H).status_code, 200)
        self.assertEqual(self.robin.post(f"/api/profiles/{pid}/labmaps/undo/{r.json()['change']}", headers=H).status_code, 404)   # once
        # back as before the re-file: the match, and its results (the later import's came in under Hematocrit)
        self.assertEqual(next(x for x in self.own(pid) if x["id"] == m["id"])["target"], "Hemoglobin")
        self.assertEqual(self.points(pid, "Hemoglobin"), [("2026-01-05", 13.1), ("2026-02-05", 12.0)])
        self.assertEqual(self.points(pid, "Hematocrit"), [("2026-01-06", 41.0), ("2026-04-05", 12.6)])
        self.assertEqual(self.labmap_row(m["id"])["import_id"], first["import_id"])
        db = dbm.connect()
        try:
            log = [json.loads(crypto.dec(r["detail"]))["change"] for r in db.execute(
                "SELECT detail FROM activity WHERE action='labmap' AND profile_id=? ORDER BY id", (pid,))]
        finally:
            db.close()
        self.assertEqual(log, ["change", "undo", "change", "change", "undo", "undo"])

    def test_add_a_match_for_any_test_and_from_labs(self):
        """Add (the page) and Same test as… (Labs) match a test as charted, any of the profile's, one Sanguity's list
        knows too, to another: every name, unit and source charted under it, its results re-filed, later imports
        following it. Remove leaves the results where they are, with an Undo; Keep as its own test splits it out."""
        pid = self.with_hemoglobin("Add 1097")
        self.save(pid, self.previewed(pid, csv(["2026-01-07", "Glucose", "91", "mg/dL", "70-99", "", "BMP", ""])))
        self.save(pid, self.previewed(pid, csv(["2026-01-08", "Hematocrit", "41", "%", "40-50", "", "CBC", ""])))
        self.assertEqual(self.ash.post(f"/api/profiles/{pid}/labmaps", json={"name": "Nothing here", "target": "Hemoglobin"}, headers=H).status_code, 404)
        r = self.ash.post(f"/api/profiles/{pid}/labmaps", json={"name": "Hematocrit", "target": "Not a test"}, headers=H)
        self.assertEqual(r.status_code, 400, r.text)   # nothing that isn't one of its tests
        r = self.ash.post(f"/api/profiles/{pid}/labmaps", json={"name": "Hematocrit", "target": "Glucose, Blood", "move": True}, headers=H)
        self.assertEqual((r.status_code, r.json()["results"]), (200, 1), r.text)
        self.assertEqual(self.lab_names(pid), ["Glucose, Blood", "Hemoglobin"])
        self.assertEqual(self.points(pid, "Glucose, Blood"), [("2026-01-07", 91.0), ("2026-01-08", 41.0)])
        m = next(x for x in self.own(pid) if x["name"] == "Hematocrit")
        self.assertEqual((m["target"], m["results"], m["imports"], m["check"]), ("Glucose, Blood", 1, 1, False))
        # a later import of the known name follows it
        k = self.previewed(pid, csv(["2026-02-08", "Hematocrit", "42", "%", "40-50", "", "CBC", ""]))
        self.assertEqual(self.entry(k, "Hematocrit")["before"], {"target": "Glucose, Blood"})
        self.save(pid, k)
        self.assertIn(("2026-02-08", 42.0), self.points(pid, "Glucose, Blood"))
        # Remove: the results stay where they're charted; the next import doesn't follow it; Undo brings it back
        r = self.robin.delete(f"/api/profiles/{pid}/labmaps/{m['id']}", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.lab_names(pid), ["Glucose, Blood", "Hemoglobin"])
        self.assertFalse([x for x in self.own(pid) if x["name"] == "Hematocrit"])
        k = self.previewed(pid, csv(["2026-04-08", "Hematocrit", "44", "%", "40-50", "", "CBC", ""]))
        self.assertNotIn("Hematocrit", [e["name"] for e in k["labs"]])   # a name Sanguity knows: not asked, its own again
        self.robin.post(f"/api/profiles/{pid}/jobs/{k['id']}/discard", headers=H)
        self.assertEqual(self.robin.post(f"/api/profiles/{pid}/labmaps/undo/{r.json()['change']}", headers=H).status_code, 200)
        again = next(x for x in self.own(pid) if x["name"] == "Hematocrit")
        self.assertEqual((again["id"], again["target"], again["created_by"]), (m["id"], "Glucose, Blood", "ash"))
        # Same test as… from Labs, the chart's name (Glucose, Blood: its own and Hematocrit's re-filed), kept as its own
        # test: the matched lab split back out, with an Undo
        r = self.robin.post(f"/api/profiles/{pid}/labmaps", json={"name": "Glucose, Blood", "target": None, "move": True}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.lab_names(pid), ["Glucose, Blood", "Hematocrit", "Hemoglobin"])
        self.assertEqual(self.points(pid, "Glucose, Blood"), [("2026-01-07", 91.0)])
        self.assertEqual(self.robin.post(f"/api/profiles/{pid}/labmaps/undo/{r.json()['change']}", headers=H).status_code, 200)
        self.assertEqual(self.lab_names(pid), ["Glucose, Blood", "Hemoglobin"])

    def test_a_name_written_another_way_is_matched_not_kept(self):   # #1097
        """A test whose name differs from another only in case or punctuation ("HEMOGLOBIN." and Hemoglobin) is matched
        to it, its results moved: only the very same name is a name matched to itself."""
        pid = self.with_hemoglobin("Case 1097")
        j = self.previewed(pid, csv(["2026-02-05", "HEMOGLOBIN.", "12.0", "g/dL", "13.5 - 17.5", "L", "CBC", ""]))
        self.save(pid, j, {self.entry(j, "HEMOGLOBIN.")["ref"]: None})   # kept separate at the review
        r = self.robin.post(f"/api/profiles/{pid}/labmaps", json={"name": "HEMOGLOBIN.", "target": "Hemoglobin", "move": True}, headers=H)
        self.assertEqual((r.status_code, r.json()["results"]), (200, 1), r.text)
        self.assertEqual(next(x for x in self.own(pid) if x["name"] == "HEMOGLOBIN.")["target"], "Hemoglobin")
        self.assertEqual(self.points(pid, "Hemoglobin"), [("2026-01-05", 13.1), ("2026-02-05", 12.0)])

    def test_keep_separate_at_a_review_is_listed_and_honoured(self):
        """Keep separate, chosen at a review, is saved like a match: listed as kept as its own test, the next import
        not asking; changed on the page, the next import follows that."""
        pid = self.with_hemoglobin("Kept 1097")
        j = self.previewed(pid, csv(["2026-02-05", "HGB-K", "12.0", "g/dL", "13.5 - 17.5", "L", "CBC", ""]))
        self.save(pid, j, {self.entry(j, "HGB-K")["ref"]: None})
        m = next(x for x in self.own(pid) if x["name"] == "HGB-K")
        self.assertEqual((m["target"], m["results"]), (None, 1))
        k = self.previewed(pid, csv(["2026-03-05", "HGB-K", "12.4", "g/dL", "13.5 - 17.5", "L", "CBC", ""]))
        self.assertEqual(self.entry(k, "HGB-K")["before"], {"target": None})   # not asked: kept as before
        self.robin.post(f"/api/profiles/{pid}/jobs/{k['id']}/discard", headers=H)
        r = self.robin.patch(f"/api/profiles/{pid}/labmaps/{m['id']}", json={"target": "Hemoglobin", "move": True}, headers=H)
        self.assertEqual((r.status_code, r.json()["results"]), (200, 1), r.text)
        self.assertEqual(self.points(pid, "Hemoglobin"), [("2026-01-05", 13.1), ("2026-02-05", 12.0)])
        k = self.previewed(pid, csv(["2026-03-05", "HGB-K", "12.4", "g/dL", "13.5 - 17.5", "L", "CBC", ""]))
        self.assertEqual(self.entry(k, "HGB-K")["before"], {"target": "Hemoglobin"})
        self.robin.post(f"/api/profiles/{pid}/jobs/{k['id']}/discard", headers=H)

    def test_a_match_made_before_names_were_kept_is_refiled_too(self):
        """A series saved under a match before the name as reported was kept beside it is re-filed by that match too:
        the one its own import saved."""
        pid = self.with_hemoglobin("Legacy 1097")
        j = self.previewed(pid, csv(["2026-02-05", "HGB-L", "12.0", "g/dL", "13.5 - 17.5", "L", "CBC", ""]))
        done = self.save(pid, j, {self.entry(j, "HGB-L")["ref"]: "Hemoglobin"})
        db = dbm.connect()
        try:   # as saved before #1097: the series named for the match, nothing beside it
            for r in db.execute("SELECT id, body FROM series WHERE import_id=?", (done["import_id"],)).fetchall():
                b = json.loads(crypto.dec(r["body"]))
                b.pop("reported")
                db.execute("UPDATE series SET body=? WHERE id=?", (crypto.enc(json.dumps(b)), r["id"]))
        finally:
            db.close()
        m = next(x for x in self.own(pid) if x["name"] == "HGB-L")
        self.assertEqual(m["results"], 1)
        r = self.robin.patch(f"/api/profiles/{pid}/labmaps/{m['id']}", json={"target": None, "move": True}, headers=H)
        self.assertEqual(r.json()["results"], 1, r.text)
        self.assertEqual(self.points(pid, "HGB-L"), [("2026-02-05", 12.0)])
        self.assertEqual(self.points(pid, "Hemoglobin"), [("2026-01-05", 13.1)])

    # ---------------------------------------------------------------- at rest

    def test_names_are_encrypted(self):
        pid = self.with_hemoglobin("Sealed")
        j = self.previewed(pid, csv(["2026-02-05", "HGB-SECRETSPELLING", "12.0", "g/dL", "13.5 - 17.5", "L", "CBC", "718-7"]))
        ref = self.entry(j, "HGB-SECRETSPELLING")["ref"]
        r = self.robin.post(f"/api/profiles/{pid}/jobs/{j['id']}/confirm", json={"labs": {ref: "Hemoglobin"}}, headers=H)
        self.assertEqual(r.status_code, 202)
        db = dbm.connect()
        try:
            held = db.execute("SELECT labs FROM jobs WHERE id=?", (j["id"],)).fetchone()["labs"]
            self.assertTrue(held is None or held.startswith(crypto.FIELD))   # the choice, while it waits to be saved
        finally:
            db.close()
        self.until(pid, j["id"], ("done",))
        db = dbm.connect()
        try:
            rows = db.execute("SELECT * FROM labmaps").fetchall()
            self.assertTrue(rows)
            for row in rows:
                self.assertTrue(row["source"].startswith(crypto.FIELD))
                self.assertTrue(row["target"] is None or row["target"].startswith(crypto.FIELD))
                self.assertRegex(row["lookup"], r"^[0-9a-f]{32}$")
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            db.close()
        for suffix in ("", "-wal"):
            path = os.path.join(TMP, "sanguity.db") + suffix
            if os.path.exists(path):
                with open(path, "rb") as f:
                    raw = f.read()
                self.assertNotIn(b"SECRETSPELLING", raw.upper())

    # ---------------------------------------------------------------- hand entry

    def test_hand_entry_uses_the_matches(self):
        pid = self.profile("Typed")
        self.save(pid, self.previewed(pid, csv(["2026-01-05", "Vitamin D 25-Hydroxy", "31", "ng/mL", "30-100", "", "", ""])))
        j = self.previewed(pid, csv(["2026-02-05", "VIT D 25-OH", "28", "ng/mL", "30-100", "L", "", ""]))
        self.save(pid, j, {self.entry(j, "VIT D 25-OH")["ref"]: "Vitamin D 25-Hydroxy"})
        row = {"date": "2026-03-05", "test": "VIT D 25-OH", "value": "33", "unit": "ng/mL", "range": "30-100"}
        r = self.ash.post(f"/api/profiles/{pid}/manual", json={"rows": [row], "save": False}, headers=H).json()
        self.assertEqual(r["rows"][0]["name"], "Vitamin D 25-Hydroxy")
        r = self.ash.post(f"/api/profiles/{pid}/manual", json={"rows": [row], "save": True}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["series"], ["Vitamin D 25-Hydroxy"])
        self.assertEqual(self.points(pid, "Vitamin D 25-Hydroxy"), [("2026-01-05", 31), ("2026-02-05", 28), ("2026-03-05", 33)])
        self.assertNotIn("VIT D 25-OH", [s["name"] for s in self.data(pid)["labs"]])

    # ---------------------------------------------------------------- the pieces

    def test_names_evened_out(self):
        self.assertEqual(labmap.norm("  Vit. D,  25-OH "), "vit d 25 oh")
        self.assertEqual(labmap.even("VIT D 25-OH"), "vitamin d 25 hydroxy")
        self.assertEqual(labmap.even("Glucose, Serum"), "glucose")
        self.assertEqual(labmap.series_loinc({"key": "loinc:718-7"}), "718-7")
        self.assertEqual(labmap.series_loinc({"loinc": "2345-7", "key": "loinc:1"}), "2345-7")
        db = dbm.connect()
        try:
            from server import atrest
            atrest.load_pepper(db)
        finally:
            db.close()
        self.assertEqual(labmap.lookup("csv", "HGB-X", "g/dL", "718-7"), labmap.lookup("csv", "hgb x", "G/DL", " 718-7"))
        self.assertNotEqual(labmap.lookup("csv", "HGB-X", "g/dL", "718-7"), labmap.lookup("ccda", "HGB-X", "g/dL", "718-7"))

    # ---------------------------------------------------------------- the panels (#994)

    def test_the_panel_table_puts_each_lab_in_one_panel(self):
        """lab-aliases.json's "panels": each a name and the labs in it, each lab by the name the file gives it (its
        canonical name, so a spelling finds its panel through it), in one panel at most; a lab in none is under Other"""
        with open(os.path.join(ROOT, "lab-aliases.json"), encoding="utf-8") as f:
            table = json.load(f)
        panels, labs = table["panels"], table["labs"]
        named = [n for names in panels.values() for n in names]
        self.assertTrue(panels and all(isinstance(p, str) and p.strip() and names for p, names in panels.items()), panels)
        self.assertEqual(sorted(n for n in set(named) if named.count(n) > 1), [], "a lab in two panels")
        self.assertEqual([n for n in named if n not in labs], [], "a name lab-aliases.json's labs doesn't give")
        from server.importers import common
        self.assertEqual([common.lab_panel(n) for n in ("Hemoglobin", "hemoglobin", "Platelets", "POTASSIUM", "Potassium, Blood", "LD", "Not a lab", "", None)],
                         ["Blood count", "Blood count", "Blood count", "Metabolic", "Metabolic", "", "", "", ""])

    def test_a_series_with_no_panel_of_its_own_is_put_in_one(self):
        """the records the page reads: a series whose import gave no panel has the one another import gave a series of the
        same name, else the table's (a spelling's through its canonical name); one an import gave keeps it; none for a
        lab in no panel; what's stored keeps what each import gave"""
        pid = self.profile("Panels")
        self.save(pid, self.previewed(pid, csv(["2026-01-05", "WBC", "6.1", "K/uL", "4.0 - 11.0", "", "My clinic's own panel", ""])))
        self.save(pid, self.previewed(pid, csv(["2026-02-05", "WBC", "6.4", "K/uL", "4.0 - 11.0", "", "", ""],
                                               ["2026-02-05", "Hemoglobin", "14.1", "g/dL", "13.5 - 17.5", "", "", ""],
                                               ["2026-02-05", "POTASSIUM", "4.1", "mmol/L", "3.5 - 5.1", "", "", ""],
                                               ["2026-02-05", "Made-up assay (fictional)", "3", "U", "", "", "", ""])))
        got = sorted((s["name"], s["panel"]) for s in self.data(pid)["labs"])
        self.assertEqual(got, [("Hemoglobin", "Blood count"), ("Made-up assay (fictional)", ""), ("Potassium, Blood", "Metabolic"),
                               ("WBC", "My clinic's own panel"), ("WBC", "My clinic's own panel")])
        db = dbm.connect()
        try:
            from server import records
            stored = sorted((s["name"], s["panel"]) for s in records.assemble(db, pid, raw=True)["labs"])
        finally:
            db.close()
        self.assertEqual(stored, [("Hemoglobin", ""), ("Made-up assay (fictional)", ""), ("Potassium, Blood", ""), ("WBC", ""), ("WBC", "My clinic's own panel")])


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
