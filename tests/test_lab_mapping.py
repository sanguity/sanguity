#!/usr/bin/env python3
"""Lab names matched during import (#23): a lab spelled in a way lab-aliases.json doesn't know is suggested an
existing series, by LOINC code first and then by a similar name with a compatible unit (filled in only for the same
code or the same name written another way, #546; a similar name is only offered); confirming saves its points
under that series (the results' own rows keep the name as reported); the choice is remembered, so the next import of
that spelling is matched by itself; "keep as a new series" is remembered too, as a choice to keep it separate; only
admins change or remove what's remembered, and it's logged; the names are encrypted at rest; and results typed in
use the remembered matches.

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

from server import accounts, activity, crypto, db as dbm, labmap, main, profiles as prof, twofactor, uploads   # noqa: E402
from tests.whole_records import whole   # noqa: E402  records with every record's details (#217)

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "ash": "copper meadow lantern drift", "ada": "granite willow ember compass"}
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
        cls.robin, cls.ash, cls.ada = (cls.login(n) for n in ("robin", "ash", "ada"))
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
        # ...also in another profile, which never had the name (matches are per install)
        other = self.profile("Elsewhere")
        j = self.previewed(other, csv(["2026-03-05", "HGB-XX", "12.6", "g/dL", "13.5 - 17.5", "L", "CBC", "718-7"]))
        self.assertEqual(self.entry(j, "HGB-XX")["before"], {"target": "Hemoglobin"})
        self.save(other, j)
        self.assertEqual(self.points(other, "Hemoglobin"), [("2026-03-05", 12.6)])
        # a duplicate is still a duplicate: the series keeps its key, so the same file again adds nothing
        j = self.previewed(pid, csv(["2026-03-05", "HGB-XX", "12.6", "g/dL", "13.5 - 17.5", "L", "CBC", "718-7"]))
        self.assertFalse(j["new"])
        self.assertEqual(j["duplicates"]["results"], 1)

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
            mine = [m for m in labmap.listing(db) if m["name"] == "HGB-Y"]
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
            self.assertIsNone(next(m for m in labmap.listing(db) if m["name"] == "HGB-Y")["target"])   # still kept separate
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
            self.assertFalse([m for m in labmap.listing(db) if m["name"] == "HGB-Z"])
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
        # matched again, then used by another import (in another profile) before the first is undone: it stays
        done = self.save(pid, j, {self.entry(j, "HGB-U")["ref"]: "Hemoglobin"})
        other = self.profile("Undone elsewhere")
        k = self.previewed(other, csv(["2026-03-05", "HGB-U", "12.4", "g/dL", "13.5 - 17.5", "L", "CBC", "718-7"]))
        self.save(other, k)
        self.assertEqual(hist()["lab_matches"], 0)
        self.robin.post(f"/api/profiles/{pid}/imports/{done['import_id']}/undo", headers=H)
        j = self.previewed(pid, csv(["2026-04-05", "HGB-U", "12.8", "g/dL", "13.5 - 17.5", "L", "CBC", "718-7"]))
        self.assertEqual(self.entry(j, "HGB-U")["before"], {"target": "Hemoglobin"})
        self.robin.post(f"/api/profiles/{pid}/jobs/{j['id']}/discard", headers=H)

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

    def remembered_as(self, name, target, checked=None, changed=None, unit="nmol/L"):
        """A remembered choice made up here: checked None and not one #546's rule makes, it's to check (#569)."""
        db = dbm.connect()
        try:
            from server import atrest
            atrest.load_pepper(db)
            return db.execute("INSERT INTO labmaps(lookup, importer, source, target, created_name, created, checked, changed, changed_name)"
                              " VALUES(?, 'csv', ?, ?, 'robin', 1, ?, ?, ?)",
                              (labmap.lookup("csv", name, unit, ""), crypto.enc(json.dumps({"name": name, "unit": unit, "loinc": ""})),
                               crypto.enc(target), checked, changed, changed and "ada")).lastrowid
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

    def test_a_reviews_choice_for_a_name_to_check_is_remembered(self):   # #575
        """The review says a choice is used for later imports: one for a name whose match is to check replaces it,
        checked, and undone with its import; one an admin has kept meanwhile stays the admin's."""
        pid = self.profile("Chosen over")
        self.save(pid, self.previewed(pid, csv(["2026-01-05", "Vitamin B1", "120", "nmol/L", "70-180", "", "", ""])))
        mid = self.remembered_as("Vitamin B6 pick", "Vitamin B1")
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
        # an admin keeps it while a review is waiting: the admin's stays
        self.ada.delete(f"/api/labmaps/{mid}", headers=H)
        mid = self.remembered_as("Vitamin B6 pick", "Vitamin B1")
        j = self.previewed(pid, csv(["2026-04-05", "Vitamin B6 pick", "44", "nmol/L", "20-125", "", "", ""]))
        self.assertEqual(self.ada.patch(f"/api/labmaps/{mid}", json={"target": "Vitamin B1", "keep": True}, headers=H).status_code, 200)
        done = self.save(pid, j, {self.entry(j, "Vitamin B6 pick")["ref"]: None})
        self.assertEqual(done["lab_names"]["remembered"], 0)
        self.assertEqual(self.labmap_row(mid)["target"], "Vitamin B1")
        self.ada.delete(f"/api/labmaps/{mid}", headers=H)

    # ---------------------------------------------------------------- the list: admins only, logged

    def test_only_admins_edit_the_list(self):
        pid = self.with_hemoglobin("Admins")
        j = self.previewed(pid, csv(["2026-02-05", "HGB-A", "12.0", "g/dL", "13.5 - 17.5", "L", "CBC", "718-7"]))
        self.save(pid, j, {self.entry(j, "HGB-A")["ref"]: "Hemoglobin"})
        for c in (self.robin, self.ash):   # the profile's owner and editor
            self.assertEqual(c.get("/api/labmaps").status_code, 403)
        listed = self.ada.get("/api/labmaps").json()
        m = next(x for x in listed if x["name"] == "HGB-A")
        self.assertEqual((m["target"], m["unit"], m["loinc"], m["importer"], m["created_by"]), ("Hemoglobin", "g/dL", "718-7", "csv", "robin"))
        self.assertIn("CSV", m["label"])
        for c in (self.robin, self.ash):
            self.assertEqual(c.patch(f"/api/labmaps/{m['id']}", json={"target": "WBC"}, headers=H).status_code, 403)
            self.assertEqual(c.delete(f"/api/labmaps/{m['id']}", headers=H).status_code, 403)
        self.assertEqual(self.ada.patch(f"/api/labmaps/{m['id']}", json={"target": "WBC"}).status_code, 403)   # CSRF header
        r = self.ada.patch(f"/api/labmaps/{m['id']}", json={"target": "Hemoglobin "}, headers=H)
        self.assertEqual((r.status_code, r.json()["target"], r.json()["changed_by"]), (200, "Hemoglobin", "ada"), r.text)
        r = self.ada.patch(f"/api/labmaps/{m['id']}", json={"target": None}, headers=H)
        self.assertIsNone(r.json()["target"])
        self.assertEqual(self.ada.patch("/api/labmaps/999999", json={"target": None}, headers=H).status_code, 404)
        # the change applies to the next import
        j = self.previewed(pid, csv(["2026-03-05", "HGB-A", "12.1", "g/dL", "13.5 - 17.5", "L", "CBC", "718-7"]))
        self.assertEqual(self.entry(j, "HGB-A")["before"], {"target": None})
        self.assertEqual(self.ada.delete(f"/api/labmaps/{m['id']}", headers=H).status_code, 200)
        self.assertFalse([x for x in self.ada.get("/api/labmaps").json() if x["id"] == m["id"]])
        j2 = self.previewed(pid, csv(["2026-03-06", "HGB-A", "12.2", "g/dL", "13.5 - 17.5", "L", "CBC", "718-7"]))
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


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
