#!/usr/bin/env python3
"""Key documents (#1135, server/keydocs.py, server/routes_keydocs.py): a document marked as an advance directive, a
power of attorney, a POLST...: owners and editors mark, change and unmark it (viewers see it, strangers get a 404);
the mark is a sealed profile setting found by the record's key, so it follows the document through a re-import, moves
the records' revision, goes in the profile's export, and is logged without what it says; the records carry it
("keyDoc"); the emergency card's editor suggests "<kind>: on file (kept at ...)" under Key documents (#1162: nothing on it by itself).
Fictional data only.

  python3 tests/test_keydocs.py
"""
import datetime, io, json, os, sys, tempfile, unittest, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-keydocs-test-")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_CORRECTIONS=os.path.join(TMP, "config", "corrections.json"))
for d in ("records", "keys", "build", "config", "profiles"):
    os.makedirs(os.path.join(TMP, d), exist_ok=True)
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, db as dbm, keydocs, main, profiles as prof, records  # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites", "lee": "maple tunnel brisk saddle"}
DAY = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
DATA = {"built": DAY, "source": "fictional", "costs": [], "upcoming": [], "labs": [],
        "events": [{"id": 1, "t": DAY + "T09:00:00", "k": "document", "title": "Scanned form", "key": "fhir:DocumentReference/ad-1"},
                   {"id": 2, "t": DAY + "T10:00:00", "k": "visit", "title": "Office visit", "key": "fhir:Encounter/v-1"},
                   {"id": 3, "t": DAY + "T11:00:00", "k": "med", "title": "Lisinopril 10 mg tablet", "key": "fhir:MedicationRequest/m-1"}],
        "details": {}}


class Rules(unittest.TestCase):
    def test_check(self):
        self.assertEqual(keydocs.check({"k1": {"kind": "poa", "kept": "  hall   desk "}}), {"k1": {"kind": "poa", "kept": "hall desk"}})
        for bad in ({"k1": {"kind": "will"}}, {"k1": {"kind": "dnr", "kept": 3}}, {"k1": {"kind": "dnr", "kept": "x" * 201}}, {"": {"kind": "dnr"}}, []):
            with self.assertRaises(keydocs.KeyDocError):
                keydocs.check(bad)

    def test_on_card(self):
        data = {"events": [{"k": "document", "keyDoc": {"kind": "polst", "kept": ""}},
                           {"k": "document", "keyDoc": {"kind": "advance_directive", "kept": "hall desk"}},
                           {"k": "letter", "keyDoc": {"kind": "advance_directive", "kept": ""}}]}
        self.assertEqual([x["line"] for x in keydocs.on_card(data)],
                         ["Advance directive or living will: on file (kept at hall desk)", "POLST or MOLST: on file"],
                         "one line per kind, in order, never the document")


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dbm.init()
        cls.ctx = TestClient(main.app, base_url="http://testserver").__enter__()
        db = dbm.connect()
        cls.uid = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        cls.pid = prof.create_profile(db, "Robin (fictional)", owner_id=cls.uid["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (cls.uid["kim"], cls.pid))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (cls.uid["lee"], cls.pid))
        with dbm.transaction(db):
            records.store(db, cls.pid, DATA, "upload", fmt="fhir")
        db.close()
        cls.c = {}
        for n in PW:
            c = TestClient(main.app, base_url="http://testserver")
            assert c.post("/api/login", json={"username": n, "password": PW[n]}, headers=H).status_code == 200
            cls.c[n] = c

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

    def url(self, rest=""):
        return f"/api/profiles/{self.pid}{rest}"

    def doc(self, who="kim", title="Scanned form"):
        return next(e for e in self.c[who].get(self.url("/data")).json()["events"] if e["title"] == title)

    def test_1_mark_change_unmark(self):
        e = self.doc()
        mark = {"event": e["id"], "kind": "advance_directive", "kept": "Original in the hall desk; copy with Dr Lee"}
        self.assertEqual(self.c["kim"].put(self.url("/keydocs"), json=mark, headers=H).status_code, 403, "a viewer can't mark one")
        visit = self.doc(title="Office visit")
        self.assertEqual(self.c["lee"].put(self.url("/keydocs"), json={**mark, "event": self.doc(title="Lisinopril 10 mg tablet")["id"]}, headers=H).status_code, 400,
                         "only what can hold a document")
        self.assertEqual(self.c["lee"].put(self.url("/keydocs"), json={**mark, "event": 99999}, headers=H).status_code, 404)
        self.assertTrue(visit)
        r = self.c["lee"].put(self.url("/keydocs"), json=mark, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        mine = next(x for x in r.json()["records"]["events"] if x["id"] == e["id"])
        self.assertEqual(mine["keyDoc"], {"kind": "advance_directive", "kept": "Original in the hall desk; copy with Dr Lee"}, "the reply carries the records")
        self.assertTrue(r.headers["x-records-rev"])
        self.assertEqual(self.doc()["keyDoc"]["kind"], "advance_directive", "a viewer sees it")
        db = dbm.connect()
        row = db.execute("SELECT value, sealed FROM settings WHERE key='keyDocs' AND profile_id=?", (self.pid,)).fetchone()
        acts = [json.loads(r["detail"]) if isinstance(r["detail"], str) and r["detail"].startswith("{") else r["detail"]
                for r in db.execute("SELECT detail FROM activity WHERE profile_id=? AND action='settings'", (self.pid,))]
        db.close()
        self.assertIsNone(row["value"], "sealed")
        self.assertFalse(any("hall desk" in str(a) for a in acts), "logged without where it's kept")
        r = self.c["robin"].put(self.url("/keydocs"), json={**mark, "kind": "poa", "kept": ""}, headers=H)
        self.assertEqual(self.doc()["keyDoc"], {"kind": "poa", "kept": ""}, "changed")
        card = self.c["lee"].get(self.url("/emergency")).json()
        self.assertEqual([s["text"] for s in card["suggestions"] if s["section"] == "documents"], ["Healthcare power of attorney: on file"])
        self.assertEqual(card["view"]["parts"], [], "suggested, never put on the card by itself")
        self.assertEqual(self.c["lee"].delete(self.url(f"/keydocs/{e['id']}"), headers=H).status_code, 200)
        self.assertNotIn("keyDoc", self.doc())
        self.assertEqual(self.c["lee"].delete(self.url(f"/keydocs/{e['id']}"), headers=H).status_code, 404)
        card = self.c["lee"].get(self.url("/emergency")).json()
        self.assertNotIn("documents", [s["section"] for s in card["suggestions"]], "none marked: not suggested")

    def test_2_follows_a_reimport_and_goes_in_the_export(self):
        e = self.doc()
        self.c["robin"].put(self.url("/keydocs"), json={"event": e["id"], "kind": "dnr", "kept": "fridge door"}, headers=H)
        db = dbm.connect()
        with dbm.transaction(db):
            records.drop(db, self.pid, records.imports(db, self.pid))
            records.store(db, self.pid, DATA, "upload", fmt="fhir")
        db.close()
        again = self.doc()
        self.assertNotEqual(again["id"], e["id"], "a re-import gives the document a new id")
        self.assertEqual(again["keyDoc"], {"kind": "dnr", "kept": "fridge door"}, "the mark finds it by its key")
        r = self.c["robin"].get(self.url("/export"))
        self.assertEqual(r.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            name = next(n for n in z.namelist() if n.endswith("settings.json"))
            self.assertEqual(json.loads(z.read(name)).get("keyDocs"), {"fhir:DocumentReference/ad-1": {"kind": "dnr", "kept": "fridge door"}})


if __name__ == "__main__":
    unittest.main(verbosity=1)
