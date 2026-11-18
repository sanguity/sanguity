#!/usr/bin/env python3
"""Chart activity and recategorizing (#236, #237): encounters that weren't visits moved to "contact", visits titled by
a billing code given plain names and reminders made appointment messages, all when the records are put together
(so records stored before are fixed, and corrections made before still find their events); an entry moved to another
category, the same change made to similar entries at once and undone at once, rules that keep doing it for a source's
new imports, roles, and schema version 34 keeping every edit. Fictional data only.

  python3 tests/test_recategorize.py
"""
import json, os, shutil, sqlite3, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-recat-test-")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_CORRECTIONS=os.path.join(TMP, "config", "corrections.json"))
for d in ("records", "keys", "build", "config"):
    os.makedirs(os.path.join(TMP, d))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, db as dbm, edits, encounters, main, profiles as prof, records   # noqa: E402
from tests.whole_records import whole   # noqa: E402  records with every record's details (#217)
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites"}


def visit(i, t, title, **detail):
    return {"id": i, "t": t, "k": "visit", "title": title, "sub": "Fictional Clinic"}, {"type": "visit", "who": "Dr. Example", **detail}


def upload(events, details=None):
    return {"events": events, "details": details or {}, "labs": [], "costs": []}


def by_title(data, title):
    return [e for e in data["events"] if e["title"] == title]


class Classifier(unittest.TestCase):
    def test_not_visits(self):
        for t in ("Chart review", "Chart Check, patient results", "Phone call", "Telephone", "CALL OUT TO PATIENT",
                  "RN Lab Fax", "Orders Only", "Refill", "Patient Message", "Documentation", "Nurse care management",
                  "PCM RN 30min", "Results Follow-Up", "Letter (Out)", "Scanning", "Erroneous Encounter"):
            self.assertTrue(encounters.not_a_visit(t), t)
        for t in ("Office visit", "Outpatient visit", "Telephone visit", "Video Visit", "Physical therapy", "Nurse visit",
                  "Hospital admission", "Callahan Clinic visit", "Lab draw", "Emergency", ""):
            self.assertFalse(encounters.not_a_visit(t), t)

    def test_billing_titles(self):
        cases = {"PR OFFICE/OUTPATIENT ESTABLISHED HIGH MDM 40 MIN": "Office visit",
                 "PR OFFICE/OUTPATIENT NEW MODERATE MDM 45 MINUTES": "Office visit (new patient)",
                 "PR SYNCHRONOUS AUDIO-VIDEO VST EST HIGH MDM 40 MIN": "Video visit",
                 "PR INITIAL HOSPITAL CARE/DAY HIGH MDM 75": "Hospital visit",
                 "EMERGENCY DEPT VISIT HIGH MDM": "Emergency visit"}
        for t, plain in cases.items():
            self.assertEqual(encounters.plain_visit_title(t), plain, t)
        for t in ("Office Visit", "99214 Office visit", "MRI BRAIN W/O CONTRAST", "Infusion, chemo 1st hr"):
            self.assertIsNone(encounters.plain_visit_title(t), t)

    def test_tidy(self):
        events = [{"id": 1, "k": "visit", "t": "2026-01-05T10:00:00", "title": "Chart review"},
                  {"id": 2, "k": "visit", "t": "2026-01-06T10:00:00", "title": "PR OFFICE/OUTPATIENT EST LOW MDM 20 MIN"},
                  {"id": 3, "k": "auto_msg", "t": "2026-03-27T07:00:00", "title": "Appointment Reminder for 3/27/26"},
                  {"id": 4, "k": "appt_msg", "t": "2026-03-20T07:00:00", "title": "Appointment Reminder for 3/27/26"},
                  {"id": 5, "k": "visit", "t": "2026-01-07T10:00:00", "title": "Office visit"},
                  {"id": 6, "k": "lab", "t": "2026-01-07T10:00:00", "title": "Chart review"}]
        details = {"1": {"type": "visit"}, "2": {"type": "visit", "who": "Dr. Example"}}
        self.assertEqual(encounters.tidy(events, details), 3)
        self.assertEqual([(e["k"], e["title"]) for e in events],
                         [("contact", "Chart review"), ("visit", "Office visit"), ("appt_msg", "Appointment Reminder"),
                          ("appt_msg", "Appointment Reminder for 3/27/26"),   # the appointment's date, not the message's: kept
                          ("visit", "Office visit"), ("lab", "Chart review")])
        self.assertEqual(events[0]["orig"], {"k": "visit", "title": "Chart review"})
        self.assertNotIn("orig", events[4])
        self.assertEqual(details["1"]["note"], encounters.VISIT_NOTE)
        self.assertEqual(details["2"], {"type": "visit", "who": "Dr. Example", "billed": "PR OFFICE/OUTPATIENT EST LOW MDM 20 MIN"})


class Recategorize(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = TestClient(main.app, base_url="http://testserver").__enter__()
        db = dbm.connect()
        uids = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        cls.pid = prof.create_profile(db, "Robin (fictional)", owner_id=uids["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (uids["kim"], cls.pid))
        db.close()
        cls.robin, cls.kim = cls.login("robin"), cls.login("kim")

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        shutil.rmtree(TMP, True)

    @staticmethod
    def login(name):
        c = TestClient(main.app, base_url="http://testserver")
        r = c.post("/api/login", json={"username": name, "password": PW[name]}, headers=H)
        assert r.status_code == 200, r.text
        return c

    def setUp(self):
        db = dbm.connect()
        with dbm.transaction(db):
            db.execute("DELETE FROM edits")
            records.drop(db, self.pid, records.imports(db, self.pid))
        db.close()

    def store(self, data, fmt="epic-mychart", origin="upload"):
        db = dbm.connect()
        with dbm.transaction(db):
            iid = records.store(db, self.pid, json.loads(json.dumps(data)), origin, fmt=fmt)
        db.close()
        return iid

    def data(self, c=None):
        return whole(c or self.robin, self.pid)

    def edit(self, action, event, c=None, status=201, **kw):
        r = (c or self.robin).post(f"/api/profiles/{self.pid}/edits", json={"event": event, "action": action, **kw}, headers=H)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def similar(self, event, c=None, status=200):
        r = (c or self.robin).get(f"/api/profiles/{self.pid}/edits/similar?event={event}")
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    # ---------------------------------------------------------------- #236
    def test_records_are_tidied_when_put_together(self):
        a, da = visit(1, "2026-01-05T10:00:00", "Chart review")
        b, db_ = visit(2, "2026-01-06T09:00:00", "PR OFFICE/OUTPATIENT ESTABLISHED MOD MDM 30 MIN")
        c, dc = visit(3, "2026-01-07T09:00:00", "Office visit")
        rem = {"id": 4, "t": "2026-02-03T07:00:00", "k": "auto_msg", "title": "Appointment Reminder for 2/3/26", "sub": ""}
        self.store(upload([a, b, c, rem], {"1": da, "2": db_, "3": dc}))
        d = self.data()
        self.assertEqual({(e["k"], e["title"]) for e in d["events"]},
                         {("contact", "Chart review"), ("visit", "Office visit"), ("appt_msg", "Appointment Reminder")})
        self.assertFalse(any("orig" in e for e in d["events"]), "what tidy() changed stays on the server")
        office = sorted((e for e in d["events"] if e["title"] == "Office visit"), key=lambda e: e["t"])
        self.assertEqual(d["details"][str(office[0]["id"])]["billed"], "PR OFFICE/OUTPATIENT ESTABLISHED MOD MDM 30 MIN")
        self.assertNotIn("billed", d["details"][str(office[1]["id"])])
        db = dbm.connect()   # stored as imported: only what's served changes
        kinds = {records._dec(r["body"])["k"] for r in db.execute("SELECT body FROM events WHERE profile_id=?", (self.pid,))}
        db.close()
        self.assertEqual(kinds, {"visit", "auto_msg"})

    def test_corrections_made_before_still_find_their_events(self):
        a, da = visit(1, "2026-01-05T10:00:00", "PR OFFICE/OUTPATIENT EST LOW MDM 20 MIN")
        b, db_ = visit(2, "2026-01-06T10:00:00", "Chart review")
        self.store(upload([a, b], {"1": da, "2": db_}), fmt="sanguity-data")   # keyless: found by kind, time and title
        db = dbm.connect()   # made before #236, against the titles as imported
        with dbm.transaction(db):
            edits._insert(db, self.pid, None, "rename", edits.content_key("visit", a["t"], a["title"]),
                          {"title": "Check-up", "was": {"k": "visit", "t": a["t"], "title": a["title"]}})
            edits._insert(db, self.pid, None, "hide", edits.content_key("visit", b["t"], b["title"]),
                          {"was": {"k": "visit", "t": b["t"], "title": b["title"]}})
            records._bump(db, self.pid)
        db.close()
        d = self.data()
        self.assertEqual([e["title"] for e in d["events"]], ["Check-up"])
        self.assertTrue(all(x["found"] for x in d["edits"]))
        # one made now, on the tidied entry, is stored against what it was imported as too
        moved = self.edit("kind", d["events"][0]["id"], k="procedure")
        self.assertEqual(self.data()["events"][0]["k"], "procedure")
        self.assertTrue(moved["id"])

    # ---------------------------------------------------------------- #237
    def test_change_category_and_undo(self):
        a, da = visit(1, "2026-01-05T10:00:00", "Nurse visit")
        lab = {"id": 2, "t": "2026-01-05T10:00:00", "k": "labitem", "title": "Ferritin", "sub": ""}
        claim = {"id": 3, "t": "2026-01-05T00:00:00", "k": "claim", "title": "Claim · Fictional", "sub": ""}
        self.store(upload([a, lab, claim], {"1": da}))
        d = self.data()
        nv = by_title(d, "Nurse visit")[0]
        eid = self.edit("kind", nv["id"], k="contact")["id"]
        d = self.data()
        self.assertEqual(by_title(d, "Nurse visit")[0]["k"], "contact")
        self.assertEqual(d["details"][str(by_title(d, "Nurse visit")[0]["id"])]["who"], "Dr. Example")   # detail as it was
        x = next(x for x in d["edits"] if x["id"] == eid)
        self.assertEqual((x["action"], x["value"], x["was"]["k"]), ("kind", {"k": "contact"}, "visit"))
        self.edit("kind", by_title(d, "Nurse visit")[0]["id"], k="letter")        # a second replaces the first
        d = self.data()
        self.assertEqual((by_title(d, "Nurse visit")[0]["k"], len(d["edits"])), ("letter", 1))
        r = self.robin.delete(f"/api/profiles/{self.pid}/edits/{d['edits'][0]['id']}", headers=H)
        self.assertEqual((r.status_code, r.json()["n"]), (200, 1))
        self.assertEqual(by_title(self.data(), "Nurse visit")[0]["k"], "visit")
        for title, k in (("Ferritin", "visit"), ("Claim · Fictional", "visit"), ("Nurse visit", "labitem"), ("Nurse visit", None)):
            self.edit("kind", by_title(self.data(), title)[0]["id"], status=400, **({"k": k} if k else {}))

    def test_similar_entries_together_and_undone_together(self):
        letters = [{"id": i, "t": f"2026-0{i}-01T09:00:00", "k": "letter", "title": "Patient Letter", "sub": ""} for i in (1, 2, 3)]
        letters.append({"id": 4, "t": "2026-04-01T09:00:00", "k": "letter", "title": "patient letter ", "sub": ""})   # case, spaces
        letters.append({"id": 5, "t": "2026-05-01T09:00:00", "k": "letter", "title": "Other letter", "sub": ""})
        self.store(upload(letters))
        self.store(upload([{"id": 1, "t": "2026-06-01T09:00:00", "k": "letter", "title": "Patient Letter", "sub": ""}]), fmt="ccda")
        d = self.data()
        first = min(by_title(d, "Patient Letter"), key=lambda e: e["t"])
        look = self.similar(first["id"])
        self.assertEqual((look["event"], look["source"]), ({"k": "letter", "title": "Patient Letter"}, "upload|epic-mychart"))
        self.assertEqual(len(look["items"]), 3, "same source, kind and title; not the other source's, not another title")
        out = self.edit("kind", first["id"], k="document", also=[x["id"] for x in look["items"]][:2])
        self.assertEqual(len(out["ids"]), 3)
        d = self.data()
        kinds = sorted((e["t"][:7], e["k"]) for e in d["events"] if e["title"].strip().casefold() == "patient letter")
        self.assertEqual(kinds, [("2026-01", "document"), ("2026-02", "document"), ("2026-03", "document"),
                                 ("2026-04", "letter"), ("2026-06", "letter")])
        batch = {x["value"].get("batch") for x in d["edits"]}
        self.assertEqual(len(batch), 1)
        self.edit("kind", first["id"], k="document", also=[by_title(d, "Other letter")[0]["id"]], status=400)   # not similar
        r = self.robin.delete(f"/api/profiles/{self.pid}/edits/{d['edits'][1]['id']}", headers=H)   # any one of them...
        self.assertEqual(r.json()["n"], 3)                                                            # ...undoes the batch
        self.assertFalse([e for e in self.data()["events"] if e["k"] == "document"])
        # a rename to similar entries
        d = self.data()
        first = min(by_title(d, "Patient Letter"), key=lambda e: e["t"])
        self.edit("rename", first["id"], title="Letter from the clinic", also=[x["id"] for x in self.similar(first["id"])["items"]])
        self.assertEqual(len(by_title(self.data(), "Letter from the clinic")), 4)

    def test_a_rule_for_new_imports(self):
        a, da = visit(1, "2026-01-05T10:00:00", "Care coordination")
        b, db_ = visit(2, "2026-02-05T10:00:00", "Care coordination")
        self.store(upload([a, b], {"1": da, "2": db_}), origin="sync", fmt="fhir")
        d = self.data()
        first = min(by_title(d, "Care coordination"), key=lambda e: e["t"])
        out = self.edit("kind", first["id"], k="contact", rule=True)
        self.assertTrue(out["rule"])
        self.assertEqual({e["k"] for e in self.data()["events"]}, {"contact"}, "the rule moves the other one now")
        c, dc = visit(1, "2026-03-05T10:00:00", "Care coordination")
        self.store(upload([c], {"1": dc}), origin="sync", fmt="fhir")                 # the next sync
        c2, dc2 = visit(1, "2026-03-06T10:00:00", "Care coordination")
        self.store(upload([c2], {"1": dc2}), origin="upload", fmt="ccda")            # another source: left alone
        d = self.data()
        self.assertEqual(sorted(e["k"] for e in d["events"]), ["contact", "contact", "contact", "visit"])
        rule = next(x for x in d["edits"] if x["action"] == "kind_rule")
        # the first was moved by its own change, so the rule moves the other two
        self.assertEqual((rule["value"]["from"], rule["value"]["k"], len(rule["ids"]), rule["found"]), ("visit", "contact", 2, True))
        # an entry moved on its own stays where it was put
        third = max((e for e in d["events"] if e["k"] == "contact"), key=lambda e: e["t"])
        self.edit("kind", third["id"], k="procedure")
        self.assertEqual(sorted(e["k"] for e in self.data()["events"]), ["contact", "contact", "procedure", "visit"])
        r = self.robin.delete(f"/api/profiles/{self.pid}/edits/{rule['id']}", headers=H)   # the rule, with its first change
        self.assertEqual(r.json()["n"], 2)
        self.assertEqual(sorted(e["k"] for e in self.data()["events"]), ["procedure", "visit", "visit", "visit"])
        self.edit("rename", first["id"], title="x", rule=True, status=400)   # only a change of category makes a rule

    def test_roles_and_activity(self):
        a, da = visit(1, "2026-01-05T10:00:00", "Nurse visit")
        self.store(upload([a], {"1": da}))
        eid = self.data()["events"][0]["id"]
        self.similar(eid, c=self.kim, status=403)
        self.edit("kind", eid, c=self.kim, status=403, k="contact")
        r = self.robin.post(f"/api/profiles/{self.pid}/edits", json={"event": eid, "action": "kind", "k": "contact"})
        self.assertEqual(r.status_code, 403, "the CSRF header is needed")
        self.edit("kind", eid, k="contact", rule=True)
        log = self.robin.get(f"/api/profiles/{self.pid}/activity").json()
        entries = log if isinstance(log, list) else log.get("entries") or log.get("activity") or []
        mine = [x for x in entries if x.get("action") == "edit"]
        self.assertTrue(mine and mine[0]["detail"].get("to") == "contact" and mine[0]["detail"].get("rule"), mine[:1])


class Schema(unittest.TestCase):
    def test_version_34_keeps_every_edit(self):
        d = tempfile.mkdtemp(prefix="sanguity-schema34-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "schema-34.db")
        db = sqlite3.connect(path, isolation_level=None)
        for v, sql in enumerate(dbm.SCHEMA[:33], start=1):
            db.executescript("BEGIN;" + sql + f"PRAGMA user_version={v};COMMIT;")
        db.execute("INSERT INTO profiles(name, created) VALUES('Fictional', 1)")
        db.execute("INSERT INTO edits(profile_id, target, target_key, action, value, at) VALUES(1, 't', 'k', 'rename', 'v', 5)")
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("INSERT INTO edits(profile_id, target, target_key, action, at) VALUES(1, 't', 'k', 'kind', 5)")
        db.executescript("BEGIN;" + dbm.SCHEMA[33] + "PRAGMA user_version=34;COMMIT;")
        self.assertEqual(db.execute("SELECT profile_id, target, target_key, action, value, at FROM edits").fetchall(),
                         [(1, "t", "k", "rename", "v", 5)])
        for a in ("kind", "kind_rule"):
            db.execute("INSERT INTO edits(profile_id, target, target_key, action, at) VALUES(1, 't', 'k', ?, 5)", (a,))
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("INSERT INTO edits(profile_id, target, target_key, action, at) VALUES(1, 't', 'k', 'nonsense', 5)")
        self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE name='edits_target'").fetchone()[0], 1)
        db.close()


if __name__ == "__main__":
    unittest.main()
