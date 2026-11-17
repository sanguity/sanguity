#!/usr/bin/env python3
"""Where every record came from (#100: records.assemble's "imports" list and each record's "imp"): the records name
their import, and the list matches the import history; an undone import's records and its entry are gone, and the
cached records follow; a merged event lists both sources; an import's last sync time (#91) is carried; an export
loaded again names its new import, not the old ones; the demo profile's records grow by only a few percent; an
import given its own name (#38) is called that in the records and the history, kept encrypted.
Fictional data only.

  python3 tests/test_sources.py
"""
import json, os, shutil, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-sources-test-")
BUILD = os.path.join(TMP, "build")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=BUILD,
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_CORRECTIONS=os.path.join(TMP, "none", "corrections.json"))
for d in ("records", "keys"):
    os.makedirs(os.path.join(TMP, d))
sys.path.insert(0, ROOT)
# the demo as the setup build, so the profile made from it at start-up is the demo profile
subprocess.run([sys.executable, "-I", os.path.join(ROOT, "demo", "generate.py"), BUILD, "--end", "2026-11-15"],
               check=True, stdout=subprocess.DEVNULL)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, crypto, db as dbm, main, profiles as prof, records   # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = "harbour lantern pebble crow"
GROWTH = 1.03   # the demo profile's records may grow by at most this much for their sources

# a data folder loaded from the command line ("dataset"): a visit, a lab day with one result and a series
FIRST = {
    "built": "2026-10-01T00:00:00+00:00", "source": "fictional",
    "events": [
        {"id": 1, "t": "2026-06-01T09:00:00", "k": "visit", "title": "Clinic visit", "sub": "Dr. Fictional", "key": "t:visit:1"},
        {"id": 2, "t": "2026-06-03T08:00:00", "k": "lab", "title": "Lab draw", "sub": "1 result", "key": "t:lab:2"},
        {"id": 3, "t": "2026-06-03T08:00:00", "k": "labitem", "title": "Ferritin", "sub": "", "key": "t:res:3"},
    ],
    "details": {"1": {"type": "visit", "who": "Dr. Fictional"}, "2": {"type": "labday", "ids": [3]},
                "3": {"type": "result", "order": "Ferritin", "rows": [["Ferritin", 40, "ng/mL", "20-200", ""]]}},
    "labs": [{"name": "Ferritin", "unit": "ng/mL", "panel": "Iron", "n": 1, "txt": [], "key": "t:ser:ferritin",
              "pts": [["2026-06-03T08:00:00", 40, 20, 200, "", "", "LAB"]]}],
    "costs": [],
}
# an upload read by an importer: a note from the same day as the visit, and a second series
SECOND = {
    "events": [{"id": 1, "t": "2026-06-01T09:30:00", "k": "document", "title": "Visit note", "sub": "", "key": "f:doc:1"}],
    "details": {"1": {"type": "visit", "where": "Clinic B", "dx": "Fictional diagnosis"}},
    "labs": [{"name": "Vitamin D", "unit": "ng/mL", "panel": "Vitamins", "n": 1, "txt": [], "key": "f:ser:vitd",
              "pts": [["2026-06-04T08:00:00", 31, 30, 100, "", ""]]}],
    "costs": [],
}


def client():
    return TestClient(main.app, base_url="http://testserver")


def stored(pid, data, origin, fmt):
    db = dbm.connect()
    try:
        with dbm.transaction(db):
            return records.store(db, pid, data, origin, fmt)
    finally:
        db.close()


class Sources(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = client().__enter__()
        db = dbm.connect()
        uid = accounts.create(db, "robin", password=PW)[0]
        cls.demo = db.execute("SELECT id FROM profiles WHERE linked_build=1").fetchone()[0]
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'owner')", (uid, cls.demo))
        cls.pid = prof.create_profile(db, "Sam (fictional)", owner_id=uid)
        db.close()
        cls.c = client()
        r = cls.c.post("/api/login", json={"username": "robin", "password": PW}, headers=H)
        assert r.status_code == 200, r.text
        cls.first = stored(cls.pid, FIRST, "dataset", records.FORMAT)

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        shutil.rmtree(TMP, True)

    def data(self, pid=None):
        r = self.c.get(f"/api/profiles/{pid or self.pid}/data")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def history(self, pid=None):
        r = self.c.get(f"/api/profiles/{pid or self.pid}/imports")
        self.assertEqual(r.status_code, 200, r.text)
        return [e for e in r.json()["imports"] if e["state"] == "active"]

    def ev(self, d, title):
        hits = [e for e in d["events"] if e["title"] == title]
        self.assertEqual(len(hits), 1, title)
        return hits[0]

    def check_matches_history(self, d, pid=None):
        hist = {str(e["id"]): e for e in self.history(pid)}
        self.assertEqual(set(d["imports"]), set(hist))
        for iid, s in d["imports"].items():
            h = hist[iid]
            self.assertEqual((s["origin"], s["format"], s["at"], s["label"]), (h["origin"], h["format"], h["at"], h["label"]))
            self.assertIsNone(s["synced"])
            self.assertTrue(s["label"])
        for x in d["events"] + d["labs"]:
            self.assertIn(str(x["imp"]), d["imports"], x)

    # ---------------------------------------------------------------- each record names its import

    def test_records_name_their_import(self):
        d = self.data()
        self.check_matches_history(d)
        self.assertEqual({e["imp"] for e in d["events"]}, {self.first})
        self.assertEqual(d["imports"][str(self.first)]["label"], "Sanguity data file")
        self.assertEqual([s["imp"] for s in d["labs"]], [self.first])
        self.assertEqual(len(d["labs"][0]["pts"][0]), 7, "points are as they were: no source repeated per point")

        demo = self.data(self.demo)
        self.check_matches_history(demo, self.demo)
        (only,) = demo["imports"].values()
        self.assertEqual((only["origin"], only["label"]), ("build", "Setup build"))

    def test_undone_import_is_gone_with_its_records(self):
        iid = stored(self.pid, SECOND, "upload", "fhir")
        d = self.data()
        self.check_matches_history(d)
        self.assertEqual(d["imports"][str(iid)]["label"], records.source_label("upload", "fhir"))
        self.assertEqual(self.ev(d, "Visit note")["imp"], iid)
        self.assertEqual(next(s for s in d["labs"] if s["name"] == "Vitamin D")["imp"], iid)

        r = self.c.post(f"/api/profiles/{self.pid}/imports/{iid}/undo", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        d = self.data()   # served again, not the cached records from before the undo
        self.assertNotIn(str(iid), d["imports"])
        self.assertFalse([x for x in d["events"] + d["labs"] if x["imp"] == iid])
        self.assertNotIn("Visit note", [e["title"] for e in d["events"]])
        self.check_matches_history(d)

    def test_merged_event_lists_both_sources(self):
        iid = stored(self.pid, SECOND, "upload", "fhir")
        try:
            d = self.data()
            visit, note = self.ev(d, "Clinic visit"), self.ev(d, "Visit note")
            r = self.c.post(f"/api/profiles/{self.pid}/edits", json={"event": visit["id"], "action": "merge", "other": note["id"]}, headers=H)
            self.assertEqual(r.status_code, 201, r.text)
            d = self.data()
            visit = self.ev(d, "Clinic visit")
            self.assertEqual(visit["imp"], self.first)
            merged = d["details"][str(visit["id"])]["merged"]
            self.assertEqual([(m["title"], m["imp"]) for m in merged], [("Visit note", iid)])
            self.assertEqual({str(self.first), str(iid)} - set(d["imports"]), set())
            self.assertTrue(any(x["action"] == "merge" and visit["id"] in x["ids"] for x in d["edits"]), "shown as edited")
        finally:
            db = dbm.connect()
            db.execute("DELETE FROM edits WHERE profile_id=?", (self.pid,))
            records.drop(db, self.pid, [iid])
            db.close()

    def test_last_sync_is_carried(self):
        db = dbm.connect()
        try:
            db.execute("UPDATE imports SET synced=? WHERE id=?", (1794000000, self.first))
            records._bump(db, self.pid)   # as whatever syncs (#91) must
            self.assertEqual(self.data()["imports"][str(self.first)]["synced"], 1794000000)
        finally:
            db.execute("UPDATE imports SET synced=NULL WHERE id=?", (self.first,))
            records._bump(db, self.pid)
            db.close()

    def test_export_loaded_again_names_its_new_import(self):
        db = dbm.connect()
        try:
            other = prof.create_profile(db, "Copy (fictional)")
            exported = records.assemble(db, self.pid)   # what an export's data.json holds, imports and imp included
        finally:
            db.close()
        iid = stored(other, exported, "upload", "sanguity")
        db = dbm.connect()
        try:
            got = records.assemble(db, other)
        finally:
            db.close()
        self.assertEqual(list(got["imports"]), [str(iid)])
        self.assertEqual({x["imp"] for x in got["events"] + got["labs"]}, {iid})
        self.assertEqual(got["imports"][str(iid)]["label"], records.source_label("upload", "sanguity"))

    # ---------------------------------------------------------------- an import's own name (#38)

    def test_import_named(self):
        iid = stored(self.pid, SECOND, "upload", "fhir")
        label = records.source_label("upload", "fhir")
        name = lambda body, i=iid, pid=self.pid, c=self.c, h=H: c.put(f"/api/profiles/{pid}/imports/{i}/name", json=body, headers=h)
        r = name({"name": "  Clinic B  portal "})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"id": iid, "label": label, "name": "Clinic B portal"})
        got = self.data()["imports"][str(iid)]   # served again, not the cached records from before
        self.assertEqual((got["label"], got["name"]), (label, "Clinic B portal"))
        self.assertNotIn("name", self.data()["imports"][str(self.first)])
        hist = {e["id"]: e for e in self.history()}
        self.assertEqual((hist[iid]["name"], hist[iid]["label"], hist[self.first]["name"]), ("Clinic B portal", label, None))
        # encrypted at rest; logged without the name
        db = dbm.connect()
        try:
            raw = db.execute("SELECT name FROM imports WHERE id=?", (iid,)).fetchone()[0]
        finally:
            db.close()
        self.assertTrue(raw.startswith(crypto.FIELD) and "Clinic B" not in raw, raw)
        log = self.c.get(f"/api/profiles/{self.pid}/activity?action=source").json()
        self.assertEqual(log[0]["detail"], {"import": iid, "label": label, "named": True})
        self.assertNotIn("Clinic B portal", json.dumps(log))
        # refused: too long, no such import, another profile's import, without the CSRF header
        self.assertEqual(name({"name": "x" * 81}).status_code, 400)
        self.assertEqual(name({"name": "x"}, i=999999).status_code, 404)
        self.assertEqual(name({"name": "x"}, i=self.first, pid=self.demo).status_code, 404)
        self.assertEqual(name({"name": "x"}, h={}).status_code, 403)
        # back to the label: no name, or the label itself
        self.assertEqual(name({"name": label}).json()["name"], None)
        name({"name": "Again"})
        self.assertIsNone(name({}).json()["name"])
        self.assertNotIn("name", self.data()["imports"][str(iid)])
        self.assertEqual(self.c.get(f"/api/profiles/{self.pid}/activity?action=source").json()[0]["detail"]["named"], False)
        self.assertEqual(self.c.post(f"/api/profiles/{self.pid}/imports/{iid}/undo", headers=H).status_code, 200)

    # ---------------------------------------------------------------- the page's data stays small

    def test_demo_payload_grows_only_a_little(self):
        r = self.c.get(f"/api/profiles/{self.demo}/data")
        self.assertEqual(r.status_code, 200)
        with_sources = len(r.content)
        d = r.json()
        self.assertGreater(len(d["events"]), 100, "the demo, with its many records")
        d.pop("imports")
        for x in d["events"] + d["labs"]:
            x.pop("imp")
        without = len(json.dumps(d, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        self.assertLessEqual(with_sources, without * GROWTH, (with_sources, without))


if __name__ == "__main__":
    unittest.main(verbosity=2)
