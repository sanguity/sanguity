#!/usr/bin/env python3
"""Corrections in the app (server/edits.py, server/routes_edits.py, issue #33): each kind of edit applied to the
records, edits surviving a rebuild of the setup build (new ids, same content) and the undo and redo of a keyed
import, an edit whose event is gone reported as not found, roles, the CSRF check, the activity log, encryption at
rest, and corrections.json moving into edits once. Fictional data only.

  python3 tests/test_edits.py
"""
import io, json, os, shutil, sys, tempfile, time, unittest, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-edits-test-")
BUILD = os.path.join(TMP, "build")
CORR = os.path.join(TMP, "config", "corrections.json")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=BUILD,
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_CORRECTIONS=CORR)
for d in ("records", "keys", "build", "config"):
    os.makedirs(os.path.join(TMP, d))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, crypto, db as dbm, edits, fhir_export, main, profiles as prof, records   # noqa: E402
from tests.whole_records import whole   # noqa: E402  records with every record's details (#217)
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites", "lee": "maple tunnel brisk saddle"}

# a setup build as build.py writes it: no source keys, ids that are only positions
BUILT = {
    "built": "2026-11-01T00:00:00+00:00", "source": "fictional",
    "events": [
        {"id": 1, "t": "2026-02-10T09:15:00", "k": "visit", "title": "Outpatient visit", "sub": "Dr. Fictional"},
        {"id": 2, "t": "2026-03-01T14:00:00", "k": "visit", "title": "Hospital admission", "sub": "Example Health",
         "end": "2026-03-01T18:00:00"},
        {"id": 3, "t": "2026-03-01T14:00:00", "k": "stay", "title": "Hospital stay · Example Health",
         "sub": "Ward 3 · same day", "end": "2026-03-01T23:59:00"},
        {"id": 4, "t": "2026-04-02T08:00:00", "k": "lab", "title": "Lab draw", "sub": "2 results"},
        {"id": 5, "t": "2026-04-02T08:00:00", "k": "labitem", "title": "CBC", "sub": ""},
        {"id": 6, "t": "2026-04-02T08:00:00", "k": "labitem", "title": "Ferritin", "sub": "", "abn": True},
        {"id": 7, "t": "2026-04-05T00:00:00", "k": "claim", "title": "Claim · Imaging", "sub": "$120"},
        {"id": 8, "t": "2026-05-20T10:00:00", "k": "visit", "title": "Follow-up visit", "sub": "", "end": "2026-05-20T11:30:00"},
        {"id": 9, "t": "2026-05-20T10:05:00", "k": "document", "title": "Follow-up note", "sub": ""},
    ],
    "details": {
        "2": {"type": "visit", "who": "Dr. Example", "where": "Example Health", "src": "visit-records/a.xml"},
        "3": {"type": "stay", "admit": "2026-03-01", "discharge": "2026-03-01", "where": "Ward 3", "how": "C-CDA encounter"},
        "4": {"type": "labday", "ids": [5, 6]},
        "5": {"type": "result", "order": "CBC", "rows": []},
        "6": {"type": "result", "order": "Ferritin", "rows": []},
        "8": {"type": "visit", "who": "Dr. Fictional"},
        "9": {"type": "visit", "where": "Clinic B", "dx": "Fictional diagnosis"},
    },
    "labs": [], "upcoming": [], "problems": [], "milestones": [],
    "costs": [{"id": 7, "d": "2026-04-05", "billed": 120, "paid": 100, "owe": 20}],
}


def write_build(data=BUILT):
    path = os.path.join(BUILD, "data.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))   # a rebuild: a new modification time


def client():
    return TestClient(main.app, base_url="http://testserver")


def by_title(data, title):
    return [e for e in data["events"] if e["title"] == title]


class Edits(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        write_build()
        cls.ctx = client().__enter__()
        db = dbm.connect()
        uids = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        cls.pid = db.execute("SELECT id FROM profiles WHERE linked_build=1").fetchone()[0]   # made from the build at start-up
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'owner')", (uids["robin"], cls.pid))
        prof.create_profile(db, "Lee (fictional)", owner_id=uids["lee"])   # a later profile: corrections.json isn't for it
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (uids["kim"], cls.pid))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (uids["lee"], cls.pid))
        prof.sync_build(db, cls.pid)
        db.close()
        cls.robin, cls.kim, cls.lee = cls.login("robin"), cls.login("kim"), cls.login("lee")

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

    def setUp(self):
        db = dbm.connect()
        db.execute("DELETE FROM edits")
        records._bump(db, self.pid)
        db.close()

    def data(self, c=None):
        return whole(c or self.robin, self.pid)

    def ev(self, title, data=None):
        hits = by_title(data or self.data(), title)
        self.assertEqual(len(hits), 1, title)
        return hits[0]

    def edit(self, action, event, c=None, status=201, **kw):
        r = (c or self.robin).post(f"/api/profiles/{self.pid}/edits", json={"event": event, "action": action, **kw}, headers=H)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def rebuild(self):
        before = {e["id"] for e in self.data()["events"]}
        write_build()
        db = dbm.connect()
        self.assertEqual(prof.sync_build(db, self.pid), {})
        db.close()
        after = self.data()
        self.assertFalse(before & {e["id"] for e in after["events"]}, "a rebuild gives every event a new id")
        return after

    def check_all_applied(self, d):
        titles = [e["title"] for e in d["events"]]
        self.assertNotIn("Claim · Imaging", titles)                     # hide
        self.assertEqual(d["costs"], [])                                # ...with its row on the Costs tab
        self.assertNotIn("Ferritin", titles)                            # hide a lab result...
        lab = self.ev("Lab draw", d)
        cbc = self.ev("CBC", d)
        self.assertEqual(d["details"][str(lab["id"])]["ids"], [cbc["id"]])   # ...and its place in the lab day
        self.assertIn("First visit", titles)                            # rename
        self.assertNotIn("Outpatient visit", titles)
        fu = self.ev("Follow-up visit", d)                              # date: the end moves too
        self.assertEqual((fu["t"], fu["end"]), ("2026-05-21T09:00:00", "2026-05-21T10:30:00"))
        self.assertEqual(d["events"], sorted(d["events"], key=lambda e: e["t"]))
        stay = self.ev("Hospital stay · Example Health", d)             # not_stay
        self.assertEqual(stay["k"], "visit")
        self.assertNotIn("end", stay)
        self.assertEqual(d["details"][str(stay["id"])]["type"], "visit")
        self.assertFalse([e for e in d["events"] if e["k"] == "stay"])
        adm = self.ev("Hospital admission", d)                          # merge: the note folds into the visit...
        self.assertNotIn("Follow-up note", titles)
        merged = d["details"][str(fu["id"])]
        self.assertEqual(merged["who"], "Dr. Fictional")                # ...which keeps its own fields
        self.assertEqual((merged["where"], merged["dx"]), ("Clinic B", "Fictional diagnosis"))   # and gains what it lacked
        self.assertEqual(merged["merged"][0]["title"], "Follow-up note")
        self.assertTrue(adm)
        self.assertTrue(all(x["found"] for x in d["edits"]))
        self.assertEqual(len(d["edits"]), 6)

    def test_each_action_survives_a_rebuild(self):
        d = self.data()
        self.edit("hide", self.ev("Claim · Imaging", d)["id"])
        self.edit("hide", self.ev("Ferritin", d)["id"])
        self.edit("rename", self.ev("Outpatient visit", d)["id"], title="Typo title")
        self.edit("rename", self.ev("Outpatient visit", d)["id"], title="  First visit ")   # replaces the first rename
        self.edit("date", self.ev("Follow-up visit", d)["id"], t="2026-05-21T09:00")
        self.edit("not_stay", self.ev("Hospital stay · Example Health", d)["id"])
        self.edit("merge", self.ev("Follow-up visit", d)["id"], other=self.ev("Follow-up note", d)["id"])
        self.check_all_applied(self.data())
        self.check_all_applied(self.rebuild())   # #33's done-when, for events without a source key

    def test_a_lab_day_merged_into_another_lists_both(self):   # #915
        """Merging a lab day into another of its date keeps both lists: results typed in for the day (their own lab day)
        merged by hand into a portal's. _merge filled in only what the first lacked, so the merged lab day listed the
        portal's lab events alone: the typed results' rows were gone from its drawer while its badge still counted their
        flags (util.js labDraws, the typed lab day no longer there). Now it lists both days' lab events, is titled for
        them ("4 lab results", their tests), its badge and drawer agree (crossmatch.lab_faults), and undoing the merge
        gives back both lab days."""
        from server import crossmatch, importers as reg
        db = dbm.connect()
        uid = db.execute("SELECT id FROM users WHERE username='robin'").fetchone()[0]
        pid = prof.create_profile(db, "Merged lab days (fictional)", owner_id=uid)
        t = "2025-03-04T09:45:00"
        obs = lambda oid, code, name, v, unit, lo, hi, flag: {"resource": {   # noqa: E731
            "resourceType": "Observation", "id": oid, "status": "final", "category": [{"coding": [{"code": "laboratory"}]}],
            "code": {"coding": [{"system": "http://loinc.org", "code": code}], "text": name}, "effectiveDateTime": t,
            "valueQuantity": {"value": v, "unit": unit}, "referenceRange": [{"low": {"value": lo}, "high": {"value": hi}, "text": f"{lo}-{hi}"}],
            **({"interpretation": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation", "code": flag}]}]} if flag else {})}}
        path = os.path.join(TMP, "portal.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"resourceType": "Bundle", "type": "collection", "entry": [obs("hgb", "718-7", "Hemoglobin", 12.1, "g/dL", 13.5, 17.5, "L"),
                                                                                  obs("plt", "777-3", "Platelet Count", 255, "10*3/uL", 150, 400, None)]}, f)
        try:
            with dbm.transaction(db):
                records.store(db, pid, reg.run({"portal.json": path})["data"], "upload", fmt="fhir")
        finally:
            db.close()
        rows = [{"date": t[:10], "time": "10:30", "test": "Potassium", "value": "5.4", "unit": "mmol/L", "range": "3.5-5.1"},
                {"date": t[:10], "time": "10:30", "test": "Sodium", "value": "139", "unit": "mmol/L", "range": "135-145"}]
        r = self.robin.post(f"/api/profiles/{pid}/manual", json={"rows": rows, "save": True}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        before = whole(self.robin, pid)
        days = {e["imp"]: e for e in before["events"] if e["k"] == "lab"}
        self.assertEqual(len(days), 2)
        portal, typed = (days[i] for i in sorted(days))
        lists = {e["id"]: before["details"][str(e["id"])]["ids"] for e in days.values()}
        r = self.robin.post(f"/api/profiles/{pid}/edits", json={"event": portal["id"], "action": "merge", "other": typed["id"]}, headers=H)
        self.assertEqual(r.status_code, 201, r.text)
        d = whole(self.robin, pid)
        merged = [e for e in d["events"] if e["k"] == "lab"]
        self.assertEqual([e["id"] for e in merged], [portal["id"]], "one lab day")
        self.assertEqual(d["details"][str(portal["id"])]["ids"], lists[portal["id"]] + lists[typed["id"]], "both days' lab events")
        self.assertEqual((merged[0]["title"], merged[0]["sub"]), ("4 lab results", "Lab results, Potassium, Sodium"))
        self.assertEqual(crossmatch.lab_faults(d), [], "each result with its row, badge and drawer agreeing")
        self.robin.delete(f"/api/profiles/{pid}/edits/{r.json()['id']}", headers=H)
        after = whole(self.robin, pid)
        self.assertEqual(([e for e in after["events"] if e["k"] == "lab"], crossmatch.lab_faults(after)),
                         ([e for e in before["events"] if e["k"] == "lab"], []), "undone: both lab days as they were")

    def test_a_lab_event_merged_into_another_keeps_both_rows(self):   # #972
        """Merging a lab event (a report) into another, not a lab day: results typed in (their own lab event and lab
        day) merged by hand into a portal's lab event of that day, or of the day before. _merge filled in only what the
        first lacked, so the merged lab event kept the portal's rows alone: the typed results lost their row in the
        drawer (crossmatch.lab_faults: "no row"), and their lab day, listing the portal's lab event in its place, listed
        it twice. Now the lab event lists both's rows, each found by its own result (its import and day, "rowsFrom"),
        the typed lab day that listed nothing else is gone, the portal's lab day is titled for the four, and its badge
        counts what its drawer shows, never another lab day of that day (the event's "folded": util.js labDraws). Undone,
        both as they were."""
        from server import crossmatch, importers as reg
        db = dbm.connect()
        uid = db.execute("SELECT id FROM users WHERE username='robin'").fetchone()[0]
        t = "2025-03-04T09:45:00"
        obs = lambda oid, code, name, v, unit, lo, hi, flag: {"resource": {   # noqa: E731
            "resourceType": "Observation", "id": oid, "status": "final", "category": [{"coding": [{"code": "laboratory"}]}],
            "code": {"coding": [{"system": "http://loinc.org", "code": code}], "text": name}, "effectiveDateTime": t,
            "valueQuantity": {"value": v, "unit": unit}, "referenceRange": [{"low": {"value": lo}, "high": {"value": hi}, "text": f"{lo}-{hi}"}],
            **({"interpretation": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation", "code": flag}]}]} if flag else {})}}
        path = os.path.join(TMP, "portal-972.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"resourceType": "Bundle", "type": "collection", "entry": [obs("hgb", "718-7", "Hemoglobin", 12.1, "g/dL", 13.5, 17.5, "L"),
                                                                                  obs("plt", "777-3", "Platelet Count", 255, "10*3/uL", 150, 400, None)]}, f)
        data = reg.run({"portal.json": path})["data"]
        try:
            pids = {}
            for day in (t[:10], "2025-03-05"):
                pids[day] = prof.create_profile(db, f"Merged lab events, typed {day} (fictional)", owner_id=uid)
                with dbm.transaction(db):
                    records.store(db, pids[day], data, "upload", fmt="fhir")
        finally:
            db.close()
        for day, pid in pids.items():
            with self.subTest(typed=day):
                typed = [{"date": day, "time": "10:30", "test": "Potassium", "value": "5.4", "unit": "mmol/L", "range": "3.5-5.1"},
                         {"date": day, "time": "10:30", "test": "Sodium", "value": "139", "unit": "mmol/L", "range": "135-145"}]
                later = [{"date": t[:10], "time": "16:00", "test": "Glucose", "value": "98", "unit": "mg/dL", "range": "70-99"}]
                for rows in (typed, later):   # each its own import: its own lab event and lab day
                    r = self.robin.post(f"/api/profiles/{pid}/manual", json={"rows": rows, "save": True}, headers=H)
                    self.assertEqual(r.status_code, 200, r.text)
                before = whole(self.robin, pid)
                self.assertEqual(crossmatch.lab_faults(before), [])
                items = {}   # import -> its lab events (a typed result is one each)
                for e in before["events"]:
                    if e["k"] == "labitem":
                        items.setdefault(e["imp"], []).append(e)
                (portal,), mine, (other,) = (items[i] for i in sorted(items))
                self.assertEqual(len(mine), 2)
                made = []
                for n, x in enumerate(mine):   # Potassium's, then Sodium's
                    r = self.robin.post(f"/api/profiles/{pid}/edits", json={"event": portal["id"], "action": "merge", "other": x["id"]}, headers=H)
                    self.assertEqual(r.status_code, 201, r.text)
                    made.append(r.json()["id"])
                    d = whole(self.robin, pid)
                    self.assertNotIn(x["id"], [e["id"] for e in d["events"]], "one lab event")
                    held = d["details"][str(portal["id"])]
                    self.assertEqual([row[0] for row in held["rows"]], ["Hemoglobin", "Platelet Count", "Potassium", "Sodium"][:3 + n], "both's rows")
                    self.assertTrue(all(held["series"]), f"each row its result's: {held['series']}")
                    self.assertEqual(crossmatch.lab_faults(d), [], "each result with its row, badge and drawer agreeing")
                    labdays = {e["imp"]: e for e in d["events"] if e["k"] == "lab"}
                    self.assertEqual(labdays[portal["imp"]]["title"], f"{3 + n} lab results")
                    self.assertEqual(d["details"][str(labdays[other["imp"]]["id"])]["ids"], [other["id"]], "the other lab day as it was")
                self.assertEqual(sorted(labdays), sorted([portal["imp"], other["imp"]]), "the typed lab day, listing nothing else, gone")
                db = dbm.connect()   # Export as FHIR: each result once, in the lab event that holds its row
                try:
                    blob = b"".join(fhir_export.stream(fhir_export.plan(db, pid, {"labs"}), "robin", zone="America/New_York"))
                finally:
                    db.close()
                with zipfile.ZipFile(io.BytesIO(blob)) as z:
                    sent = [x["resource"] for x in json.loads(z.read("bundle.json"))["entry"]]
                obs = sorted(o["code"].get("text") or o["code"]["coding"][0].get("display") for o in sent if o["resourceType"] == "Observation")
                self.assertEqual(len(obs), 5, obs)
                report = next(r for r in sent if r["resourceType"] == "DiagnosticReport" and len(r.get("result") or []) == 4)
                self.assertEqual(len({x["reference"] for x in report["result"]}), 4)
                for i in made:
                    self.robin.delete(f"/api/profiles/{pid}/edits/{i}", headers=H)
                after = whole(self.robin, pid)
                self.assertEqual((sorted(e["id"] for e in after["events"]), crossmatch.lab_faults(after)),
                                 (sorted(e["id"] for e in before["events"]), []), "undone: as they were")
                self.assertEqual([e for e in after["events"] if e["k"] in ("lab", "labitem")], [e for e in before["events"] if e["k"] in ("lab", "labitem")])

    def test_only_kinds_that_can_be_one_are_merged(self):   # #1023
        """Merge with… listed every event within a week: a lab event merged into a lab day or a visit left its results
        with no row, a lab day merged into a lab event left its lab events listed by no lab day, and a lab day's badge
        counted what its drawer didn't show. Now a lab day merges only with a lab day and a lab event only with a lab
        event (edits.mergeable, which the drawer offers alone); any other pair is refused, saying so. Every merge of
        every two events here within a week: one allowed leaves crossmatch.lab_faults clean, nothing lost, undone as
        it was; and one of those kinds made before is not applied."""
        from server import crossmatch, importers as reg
        db = dbm.connect()
        uid = db.execute("SELECT id FROM users WHERE username='robin'").fetchone()[0]
        pid = prof.create_profile(db, "Merges of kinds (fictional)", owner_id=uid)
        t = "2025-03-04T09:45:00"
        obs = lambda oid, code, name, v, unit, lo, hi: {"resource": {   # noqa: E731
            "resourceType": "Observation", "id": oid, "status": "final", "category": [{"coding": [{"code": "laboratory"}]}],
            "code": {"coding": [{"system": "http://loinc.org", "code": code}], "text": name}, "effectiveDateTime": t,
            "valueQuantity": {"value": v, "unit": unit}, "referenceRange": [{"low": {"value": lo}, "high": {"value": hi}, "text": f"{lo}-{hi}"}]}}
        visit = lambda eid, day: {"resource": {"resourceType": "Encounter", "id": eid, "status": "finished",   # noqa: E731
                                               "class": {"code": "AMB"}, "period": {"start": f"{day}T10:00:00"}}}
        path = os.path.join(TMP, "portal-1023.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"resourceType": "Bundle", "type": "collection", "entry": [
                obs("hgb", "718-7", "Hemoglobin", 12.1, "g/dL", 13.5, 17.5), obs("plt", "777-3", "Platelet Count", 255, "10*3/uL", 150, 400),
                visit("v1", "2025-03-02"), visit("v2", "2025-03-03")]}, f)
        try:
            with dbm.transaction(db):
                records.store(db, pid, reg.run({"portal.json": path})["data"], "upload", fmt="fhir")
        finally:
            db.close()
        typed = [{"date": "2025-03-06", "time": "10:30", "test": "Potassium", "value": "5.4", "unit": "mmol/L", "range": "3.5-5.1"},
                 {"date": "2025-03-06", "time": "10:30", "test": "Sodium", "value": "139", "unit": "mmol/L", "range": "135-145"}]
        r = self.robin.post(f"/api/profiles/{pid}/manual", json={"rows": typed, "save": True}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        results = lambda d: sorted((s["name"], p[0]) for s in d["labs"] for p in s["pts"])   # noqa: E731
        before = whole(self.robin, pid)
        evs = sorted(before["events"], key=lambda e: (e["t"], e["k"], e["id"]))
        self.assertEqual(sorted(e["k"] for e in evs), ["lab", "lab", "labitem", "labitem", "labitem", "visit", "visit"])
        self.assertEqual(crossmatch.lab_faults(before), [])
        made = {}
        for a in evs:
            for b in evs:
                if a is b:
                    continue
                with self.subTest(into=(a["k"], a["title"]), merged=(b["k"], b["title"])):
                    r = self.robin.post(f"/api/profiles/{pid}/edits", json={"event": a["id"], "action": "merge", "other": b["id"]}, headers=H)
                    can = a["k"] == b["k"] or "lab" not in a["k"] and "lab" not in b["k"]
                    made[(a["k"], b["k"])] = r.status_code
                    if not can:
                        if r.status_code == 201:   # made after all: what it did said, then undone so the next starts as before
                            print("\n  merged", (a["k"], a["title"]), "<-", (b["k"], b["title"]), crossmatch.lab_faults(whole(self.robin, pid))[:2])
                            self.robin.delete(f"/api/profiles/{pid}/edits/{r.json()['id']}", headers=H)
                        self.assertEqual((r.status_code, r.json().get("detail")), (400, "A lab draw can be merged only with another lab draw, "
                                                                                    "and a lab result only with another lab result."))
                        continue
                    self.assertEqual(r.status_code, 201, r.text)
                    d = whole(self.robin, pid)
                    self.assertNotIn(b["id"], [e["id"] for e in d["events"]], "merged in")
                    self.assertEqual((crossmatch.lab_faults(d), results(d)), ([], results(before)), "each result with its row, nothing lost")
                    self.robin.delete(f"/api/profiles/{pid}/edits/{r.json()['id']}", headers=H)
                    after = whole(self.robin, pid)
                    self.assertEqual(([(e["id"], e["k"], e["title"]) for e in after["events"]], crossmatch.lab_faults(after)),
                                     ([(e["id"], e["k"], e["title"]) for e in before["events"]], []), "undone: as it was")
        self.assertEqual(sorted(k for k, s in made.items() if s == 201), [("lab", "lab"), ("labitem", "labitem"), ("visit", "visit")])
        item, day = next(e for e in evs if e["k"] == "labitem"), next(e for e in evs if e["k"] == "lab" and e["t"][:10] == "2025-03-06")
        db = dbm.connect()   # one made before: a lab event merged into a lab day
        try:
            with dbm.transaction(db):
                edits._insert(db, pid, uid, "merge", edits.key_of(edits._event(db, pid, day["id"])), {"was": edits._was(day)},
                              edits.key_of(edits._event(db, pid, item["id"])))
                edits._bump(db, pid)
        finally:
            db.close()
        d = whole(self.robin, pid)
        self.assertEqual(([(e["id"], e["k"]) for e in d["events"]], crossmatch.lab_faults(d)), ([(e["id"], e["k"]) for e in before["events"]], []),
                         "not applied")
        self.assertEqual([(x["action"], x["found"]) for x in d["edits"]], [("merge", False)], "listed, to undo")

    def test_hiding_one_lab_event_hides_only_it(self):   # #937
        """A lab event's key is its day's ("fhir:labitem:2025-03-04"), and a lab day's too, the same in every import of
        that day. A portal's export uploaded (Hemoglobin, Platelets), then downloaded again with an ALT added: the ALT
        has its row in a lab event of the second import's own (#878), under the same key. Hiding the ALT's lab event
        hid both, so the day's lab day showed no rows at all. Now it hides the ALT's alone; the upload's keeps its
        rows. And with a hospital's file of that day too, hiding the hospital's lab day leaves the portal's. Undoing
        each brings back what was there (crossmatch.lab_faults: none). An edit made again of the same lab event
        replaces its own, never the other one's; a lab event that holds nothing it held is no longer found."""
        from server import crossmatch, uploads
        db = dbm.connect()
        uid = db.execute("SELECT id FROM users WHERE username='robin'").fetchone()[0]
        pid = prof.create_profile(db, "One lab event hidden (fictional)", owner_id=uid)
        db.close()
        t = "2025-03-04T09:45:00"

        def obs(oid, code, name, v, unit, lo, hi, flag=None):
            return {"resourceType": "Observation", "id": oid, "status": "final", "category": [{"coding": [{"code": "laboratory"}]}],
                    "code": {"coding": [{"system": "http://loinc.org", "code": code}], "text": name}, "effectiveDateTime": t,
                    "valueQuantity": {"value": v, "unit": unit}, "referenceRange": [{"low": {"value": lo}, "high": {"value": hi}, "text": f"{lo}-{hi}"}],
                    **({"interpretation": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation", "code": flag}]}]} if flag else {})}

        def upload(name, resources):
            body = json.dumps({"resourceType": "Bundle", "type": "collection", "entry": [{"resource": r} for r in resources]}).encode()
            db = dbm.connect()
            try:
                up = uploads.create(db, pid, uid)
                uploads.add_file(db, pid, up, name, __import__("io").BytesIO(body))
                uploads.finish(db, pid, up)
            finally:
                db.close()
            r = self.robin.post(f"/api/profiles/{pid}/uploads/{up}/import", json={}, headers=H)
            self.assertEqual(r.status_code, 202, r.text)
            jid, end = r.json()["id"], time.time() + 30
            while time.time() < end:
                j = self.robin.get(f"/api/profiles/{pid}/jobs/{jid}").json()
                if j["state"] == "preview":
                    self.assertEqual(self.robin.post(f"/api/profiles/{pid}/jobs/{jid}/confirm", json={"keep": []}, headers=H).status_code, 202)
                elif j["state"] in ("done", "failed"):
                    self.assertEqual(j["state"], "done", j)
                    return j
                time.sleep(0.05)
            self.fail("the upload never finished")

        def rows(d):   # each lab day's rows, by its import
            out = {}
            for e in d["events"]:
                if e["k"] == "lab":
                    out[e["imp"]] = sorted(r[0] for i in d["details"][str(e["id"])]["ids"]
                                           for r in d["details"][str(i)]["rows"])
            return out

        def edit(**body):
            r = self.robin.post(f"/api/profiles/{pid}/edits", json=body, headers=H)
            self.assertEqual(r.status_code, 201, r.text)
            return r.json()["id"]

        portal = [obs("hgb", "718-7", "Hemoglobin", 12.1, "g/dL", 13.5, 17.5, "L"), obs("plt", "777-3", "Platelet Count", 255, "10*3/uL", 150, 400)]
        upload("portal-export.json", portal)
        upload("portal-export-2.json", portal + [obs("alt", "1742-6", "ALT", 60, "U/L", 7, 56, "H")])
        before = whole(self.robin, pid)
        items = [e for e in before["events"] if e["k"] == "labitem"]
        self.assertEqual(len({edits.key_of(e) for e in items}), 1, "the day's lab events share its key")
        alt = next(e["id"] for e in items if [r[0] for r in before["details"][str(e["id"])]["rows"]] == ["ALT"])
        self.assertEqual(sorted(sum(rows(before).values(), [])), ["ALT", "Hemoglobin", "Platelet Count"])
        self.assertEqual(crossmatch.lab_faults(before), [])

        hide = edit(event=alt, action="hide")
        d = whole(self.robin, pid)
        self.assertEqual(sorted(sum(rows(d).values(), [])), ["Hemoglobin", "Platelet Count"], "the ALT's alone is hidden")
        self.assertEqual([x["ids"] for x in d["edits"]], [[alt]])
        self.assertEqual(crossmatch.lab_faults(d), [], "its lab day, listing nothing else, hidden with it (#950)")
        again = edit(event=alt, action="hide")   # the same lab event hidden again: its own edit replaced
        self.assertEqual([x["id"] for x in whole(self.robin, pid)["edits"]], [again])
        self.robin.delete(f"/api/profiles/{pid}/edits/{again}", headers=H)
        d = whole(self.robin, pid)
        self.assertEqual((rows(d), crossmatch.lab_faults(d)), (rows(before), []), "shown again: as it was")

        # a hospital's file of the same day: its own lab day, under the same key as the portal's
        upload("hospital.json", [obs("h-na", "2951-2", "Sodium", 139, "mmol/L", 135, 145), obs("h-k", "2823-3", "Potassium", 5.4, "mmol/L", 3.5, 5.1, "H")])
        both = whole(self.robin, pid)
        days = [e for e in both["events"] if e["k"] == "lab"]
        self.assertEqual(len({edits.key_of(e) for e in days}), 1, "the day's lab days share its key")
        hospital = next(e for e in days if "Sodium" in rows(both)[e["imp"]])
        edit(event=alt, action="hide")
        hide = edit(event=hospital["id"], action="hide")
        d = whole(self.robin, pid)
        self.assertEqual(sorted(sum(rows(d).values(), [])), ["Hemoglobin", "Platelet Count"],
                         "the hospital's lab day hidden, the portal's shown, the ALT's lab event hidden still")
        self.assertEqual(crossmatch.lab_faults(d), [], "the hospital's results on no lab day (#950)")
        self.robin.delete(f"/api/profiles/{pid}/edits/{hide}", headers=H)
        d = whole(self.robin, pid)
        self.assertEqual(sorted(sum(rows(d).values(), [])), ["Hemoglobin", "Platelet Count", "Potassium", "Sodium"])
        for x in d["edits"]:
            self.robin.delete(f"/api/profiles/{pid}/edits/{x['id']}", headers=H)
        d = whole(self.robin, pid)
        self.assertEqual((rows(d), crossmatch.lab_faults(d)), (rows(both), []))

    def test_a_hidden_lab_events_results_stay_in_labs_on_no_lab_day(self):   # #950
        """Hiding a lab event (a report) by hand left it out of its lab day's drawer, but its results stayed in Labs (as
        they still do: Hide says so) and were still counted on its lab day's badge, its import's results that day being
        the lab day's (util.js labDraws): 4 against the drawer's 2. Now a result whose every row is in a lab event hidden
        is marked ("unlisted", by the drawer's own match of a row to its result) and counted on no lab day; a lab day all
        of whose lab events are hidden is hidden too, and hiding a lab day hides the lab events it lists. Each time the
        badge counts what the drawer shows (crossmatch.lab_faults: none), every result still in Labs; undone, as it was."""
        from server import crossmatch, uploads
        db = dbm.connect()
        uid = db.execute("SELECT id FROM users WHERE username='robin'").fetchone()[0]
        pid = prof.create_profile(db, "A hidden report (fictional)", owner_id=uid)
        db.close()
        t = "2025-06-03T08:15:00"

        def obs(oid, code, name, v, unit, lo, hi):
            return {"resourceType": "Observation", "id": oid, "status": "final", "category": [{"coding": [{"code": "laboratory"}]}],
                    "code": {"coding": [{"system": "http://loinc.org", "code": code}], "text": name}, "effectiveDateTime": t,
                    "valueQuantity": {"value": v, "unit": unit}, "referenceRange": [{"low": {"value": lo}, "high": {"value": hi}}]}

        def report(rid, name, *ids):
            return {"resourceType": "DiagnosticReport", "id": rid, "status": "final", "code": {"text": name}, "effectiveDateTime": t,
                    "result": [{"reference": f"Observation/{i}"} for i in ids]}

        def upload(name, resources):
            body = json.dumps({"resourceType": "Bundle", "type": "collection", "entry": [{"resource": r} for r in resources]}).encode()
            db = dbm.connect()
            try:
                up = uploads.create(db, pid, uid)
                uploads.add_file(db, pid, up, name, __import__("io").BytesIO(body))
                uploads.finish(db, pid, up)
            finally:
                db.close()
            jid = self.robin.post(f"/api/profiles/{pid}/uploads/{up}/import", json={}, headers=H).json()["id"]
            end = time.time() + 30
            while time.time() < end:
                j = self.robin.get(f"/api/profiles/{pid}/jobs/{jid}").json()
                if j["state"] == "preview":
                    self.robin.post(f"/api/profiles/{pid}/jobs/{jid}/confirm", json={"keep": []}, headers=H)
                elif j["state"] in ("done", "failed"):
                    self.assertEqual(j["state"], "done", j)
                    return
                time.sleep(0.05)
            self.fail("the upload never finished")

        def edit(**body):
            r = self.robin.post(f"/api/profiles/{pid}/edits", json=body, headers=H)
            self.assertEqual(r.status_code, 201, r.text)
            return r.json()["id"]

        def seen(d):   # (each lab day's rows, what's wrong with them, the results on no lab day, every result in Labs)
            days = {}
            for e in d["events"]:
                if e["k"] == "lab":
                    days[e["imp"]] = sorted(r[0] for i in d["details"][str(e["id"])]["ids"] for r in d["details"][str(i)]["rows"])
            off = sorted(s["name"] for s in d["labs"] for n in (s.get("unlisted") or {}))
            return days, crossmatch.lab_faults(d), off, sorted(s["name"] for s in d["labs"] if s["pts"])
        upload("portal.json", [obs("hgb", "718-7", "Hemoglobin", 12.1, "g/dL", 13.5, 17.5), obs("plt", "777-3", "Platelet Count", 255, "10*3/uL", 150, 400),
                               obs("alt", "1742-6", "ALT", 60, "U/L", 7, 56), obs("ast", "1920-8", "AST", 30, "U/L", 8, 33),
                               report("cbc", "CBC", "hgb", "plt"), report("lft", "Liver panel", "alt", "ast")])
        upload("hospital.json", [obs("h-na", "2951-2", "Sodium", 139, "mmol/L", 135, 145), report("bmp", "BMP", "h-na")])
        before = whole(self.robin, pid)
        labs = ["ALT", "AST", "Hemoglobin", "Platelet Count", "Sodium, Blood"]
        portal, hospital = sorted({e["imp"] for e in before["events"] if e["k"] == "lab"})
        self.assertEqual(seen(before), ({portal: ["ALT", "AST", "Hemoglobin", "Platelet Count"], hospital: ["Sodium"]}, [], [], labs))
        lft = next(e["id"] for e in before["events"] if e["k"] == "labitem" and [r[0] for r in before["details"][str(e["id"])]["rows"]] == ["ALT", "AST"])
        bmp = next(e["id"] for e in before["events"] if e["k"] == "labitem" and e["imp"] == hospital)
        day = lambda imp: next(e["id"] for e in before["events"] if e["k"] == "lab" and e["imp"] == imp)   # noqa: E731

        one = edit(event=lft, action="hide")   # one of the portal's two reports
        self.assertEqual(seen(whole(self.robin, pid)), ({portal: ["Hemoglobin", "Platelet Count"], hospital: ["Sodium"]}, [], ["ALT", "AST"], labs),
                         "its results on no lab day, still in Labs")
        edit(event=bmp, action="hide")   # the hospital's only report: its lab day goes with it
        d = whole(self.robin, pid)
        self.assertEqual(seen(d), ({portal: ["Hemoglobin", "Platelet Count"]}, [], ["ALT", "AST", "Sodium, Blood"], labs))
        for x in d["edits"]:
            self.robin.delete(f"/api/profiles/{pid}/edits/{x['id']}", headers=H)
        edit(event=day(portal), action="hide")   # a lab day: its reports with it
        d = whole(self.robin, pid)
        self.assertEqual(seen(d), ({hospital: ["Sodium"]}, [], ["ALT", "AST", "Hemoglobin", "Platelet Count"], labs))
        self.assertFalse([e for e in d["events"] if e["k"] == "labitem" and e["imp"] == portal], "its lab events hidden with it")
        for x in whole(self.robin, pid)["edits"]:
            self.robin.delete(f"/api/profiles/{pid}/edits/{x['id']}", headers=H)
        self.assertEqual(seen(whole(self.robin, pid)), seen(before), "undone, as it was")
        self.assertTrue(one)

    def test_replies_carry_the_records(self):
        """#271: an edit, and its undo, answer with the records as the page loads them, so it draws them in place"""
        d = self.data()
        r = self.robin.post(f"/api/profiles/{self.pid}/edits", json={"event": self.ev("Outpatient visit", d)["id"], "action": "rename", "title": "First visit"}, headers=H)
        self.assertEqual(r.status_code, 201, r.text)
        page = self.robin.get(f"/api/profiles/{self.pid}/data")
        got = r.json()
        self.assertEqual(got["records"], page.json())
        self.assertEqual(r.headers["X-Records-Rev"], page.headers["X-Records-Rev"])
        self.assertIn("First visit", [e["title"] for e in got["records"]["events"]])
        r = self.robin.delete(f"/api/profiles/{self.pid}/edits/{got['id']}", headers=H)
        self.assertEqual((r.json()["ok"], r.json()["n"]), (True, 1))
        self.assertEqual(r.json()["records"], self.robin.get(f"/api/profiles/{self.pid}/data").json())
        self.assertIn("Outpatient visit", [e["title"] for e in r.json()["records"]["events"]])

    def test_edits_follow_a_retitled_build_event(self):
        """A rebuild that names an event differently (same kind, same minute) keeps its edits on it; where that minute
        holds two events of the kind (the two lab results), nothing moves, since it can't tell which is which."""
        d = self.data()
        self.edit("hide", self.ev("Outpatient visit", d)["id"])
        self.edit("hide", self.ev("Ferritin", d)["id"])
        self.edit("merge", self.ev("Follow-up visit", d)["id"], other=self.ev("Follow-up note", d)["id"])
        renamed = {"Outpatient visit": "Office visit", "Ferritin": "Ferritin, serum", "CBC": "CBC with differential",
                   "Follow-up note": "Follow-up visit note"}
        try:
            write_build({**BUILT, "events": [{**e, "title": renamed.get(e["title"], e["title"])} for e in BUILT["events"]]})
            db = dbm.connect()
            self.assertEqual(prof.sync_build(db, self.pid), {})
            db.close()
            d = self.data()
            titles = [e["title"] for e in d["events"]]
            self.assertNotIn("Office visit", titles)                       # the hide followed the new title
            self.assertNotIn("Follow-up visit note", titles)               # and so did the merge's second event
            self.assertIn("Ferritin, serum", titles)                       # two lab results at 08:00: left alone
            self.assertEqual(sorted(x["found"] for x in d["edits"]), [False, True, True])
        finally:
            write_build()
            db = dbm.connect()
            prof.sync_build(db, self.pid)
            db.close()

    def test_bad_edits_are_refused(self):
        d = self.data()
        visit = self.ev("Outpatient visit", d)["id"]
        self.edit("not_stay", visit, status=400)
        self.edit("rename", visit, title="   ", status=400)
        self.edit("date", visit, t="next tuesday", status=400)
        self.edit("merge", visit, other=visit, status=400)
        self.edit("hide", 999999, status=400)
        self.edit("explode", visit, status=422)

    def test_keyed_import_undone_and_redone(self):
        upload = {"events": [{"id": "a", "t": "2025-07-01T10:00:00", "k": "visit", "title": "Telehealth visit", "key": "fhir:Encounter/fict-1"},
                             {"id": "b", "t": "2025-07-02T10:00:00", "k": "visit", "title": "Video call", "key": "fhir:Encounter/fict-2"}],
                  "details": {}, "labs": [], "costs": []}

        def store():
            db = dbm.connect()
            with dbm.transaction(db):
                iid = records.store(db, self.pid, json.loads(json.dumps(upload)), "upload", fmt="fhir")
            db.close()
            return iid

        iid = store()
        d = self.data()
        self.edit("rename", self.ev("Telehealth visit", d)["id"], title="Phone visit")
        self.edit("hide", self.ev("Video call", d)["id"])
        d = self.data()
        self.assertTrue(by_title(d, "Phone visit") and not by_title(d, "Video call"))

        db = dbm.connect()
        with dbm.transaction(db):
            records.drop(db, self.pid, [iid])   # the import undone
        db.close()
        listed = self.robin.get(f"/api/profiles/{self.pid}/edits").json()["edits"]
        self.assertEqual(len(listed), 2, "the edits stay when their import goes")
        self.assertEqual([(x["found"], x["ids"]) for x in listed], [(False, []), (False, [])])
        self.assertEqual({x["was"]["title"] for x in listed}, {"Telehealth visit", "Video call"})

        iid = store()   # ...and redone: new ids, same source keys
        d = self.data()
        self.assertTrue(by_title(d, "Phone visit") and not by_title(d, "Video call") and not by_title(d, "Telehealth visit"))
        self.assertTrue(all(x["found"] for x in d["edits"]))
        db = dbm.connect()
        with dbm.transaction(db):
            records.drop(db, self.pid, [iid])
        db.close()

    def test_missing_target_is_reported(self):
        db = dbm.connect()
        with dbm.transaction(db):
            edits._insert(db, self.pid, None, "hide", edits.content_key("visit", "2020-01-01T10:00:00", "Long gone"),
                          {"was": {"k": "visit", "t": "2020-01-01T10:00:00", "title": "Long gone"}})
            records._bump(db, self.pid)
        db.close()
        d = self.data()
        self.assertEqual(len(d["events"]), len(BUILT["events"]))
        [x] = d["edits"]
        self.assertEqual((x["found"], x["ids"], x["was"]["title"]), (False, [], "Long gone"))

    def test_roles_csrf_and_activity(self):
        d = self.data(self.kim)   # a viewer sees the records, and what was edited in them
        visit = self.ev("Outpatient visit", d)["id"]
        self.edit("hide", visit, c=self.kim, status=403)
        self.assertEqual(self.kim.get(f"/api/profiles/{self.pid}/edits").status_code, 403)
        r = self.robin.post(f"/api/profiles/{self.pid}/edits", json={"event": visit, "action": "hide"})   # no X-Sanguity
        self.assertEqual(r.status_code, 403)
        eid = self.edit("rename", visit, c=self.lee, title="Renamed by an editor")["id"]   # editors may
        self.assertEqual([x["by"] for x in self.data(self.kim)["edits"]], ["lee"])
        self.assertEqual(self.kim.delete(f"/api/profiles/{self.pid}/edits/{eid}", headers=H).status_code, 403)
        self.assertEqual(self.robin.delete(f"/api/profiles/{self.pid}/edits/{eid}", headers=H).status_code, 200)
        self.assertEqual(self.robin.delete(f"/api/profiles/{self.pid}/edits/{eid}", headers=H).status_code, 404)
        self.assertTrue(by_title(self.data(), "Outpatient visit"))
        log = self.robin.get(f"/api/profiles/{self.pid}/activity?action=edit").json()
        entries = log["entries"] if isinstance(log, dict) else log
        details = [e["detail"] for e in entries if e["action"] == "edit"][:2]
        self.assertEqual(details[0], {"action": "rename", "title": "Outpatient visit", "undone": True})
        self.assertEqual(details[1], {"action": "rename", "title": "Outpatient visit", "to": "Renamed by an editor"})

    def test_encrypted_at_rest(self):
        self.edit("rename", self.ev("Outpatient visit")["id"], title="Secret fictional title")
        db = dbm.connect()
        rows = db.execute("SELECT target, target_key, value FROM edits").fetchall()
        db.close()
        self.assertEqual(len(rows), 1)
        for col in ("target_key", "value"):
            self.assertTrue(rows[0][col].startswith(crypto.FIELD), col)
        raw = json.dumps([tuple(r) for r in rows])
        for plain in ("Secret fictional title", "Outpatient visit", edits.CONTENT):
            self.assertNotIn(plain, raw)
        self.assertRegex(rows[0]["target"], r"^[0-9a-f]{32}$")

    def test_z_corrections_json_moves_once(self):   # last: it changes what the build shows
        db = dbm.connect()
        self.assertIsNone(dbm.meta_get(db, edits.MIGRATED), "nothing to move while there's no corrections.json")
        with open(CORR, "w", encoding="utf-8") as f:
            json.dump({"milestones": [{"date": "2026-01-15", "label": "Diagnosis"}],
                       "not_hospital_stays": [{"date": "2026-03-01", "org": "Example Health", "title": "Outpatient CT",
                                               "why": "Coded as inpatient"},
                                              {"date": "2026-06-01", "org": "", "why": "No such stay in these records"}]}, f)
        self.assertEqual(edits.migrate_corrections(db), 4)
        self.assertIsNone(edits.migrate_corrections(db), "only once")
        acts = [r[0] for r in db.execute("SELECT action FROM edits WHERE profile_id=? ORDER BY id", (self.pid,))]
        self.assertEqual(acts, ["not_stay", "rename", "merge", "not_stay"])
        db.close()
        d = self.data()
        ct = self.ev("Outpatient CT", d)   # one visit, as build.py's correction used to leave it
        self.assertEqual(ct["k"], "visit")
        self.assertFalse(by_title(d, "Hospital admission") or by_title(d, "Hospital stay · Example Health"))
        self.assertEqual(d["details"][str(ct["id"])]["who"], "Dr. Example")
        self.assertFalse([e for e in d["events"] if e["k"] == "stay"])
        self.assertEqual([x["found"] for x in d["edits"]], [True, True, True, False])
        self.assertEqual(d["edits"][3]["was"], {"k": "stay", "t": "2026-06-01", "title": "Hospital stay"})
        self.assertEqual(self.ev("Outpatient CT", self.rebuild())["k"], "visit")


if __name__ == "__main__":
    unittest.main(verbosity=2)
