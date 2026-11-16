#!/usr/bin/env python3
"""Lab names matched during import (#23): a lab spelled in a way lab-aliases.json doesn't know is suggested an
existing series, by LOINC code first and then by a similar name with a compatible unit; confirming saves its points
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
        return self.robin.get(f"/api/profiles/{pid}/data").json()

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
        self.assertEqual(e["suggestions"][0], {"name": "Vitamin D 25-Hydroxy", "unit": "ng/mL", "reason": "similar name, same unit", "sure": True})
        j = self.previewed(pid, csv(["2026-02-05", "VIT D 25-OH", "70", "nmol/L", "75-250", "L", "", ""]))
        e = self.entry(j, "VIT D 25-OH")
        self.assertNotIn("Vitamin D 25-Hydroxy", [s["name"] for s in e["suggestions"]])

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
