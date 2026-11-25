#!/usr/bin/env python3
"""The medication timeline (server/medications.py, issue #50): listings grouped by medication, laid out as spans
with their dose changes, stops, prescribers and RxNorm codes, and pharmacy fills marked; hidden records left out;
the result in the records the page loads (only when there are medications, never stored from an import), with a
correction applied; and the FHIR importer's prescriber, end date and RxNorm code. Fictional data only.

  python3 tests/test_medications.py
"""
import json, os, shutil, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-meds-test-")
BUILD = os.path.join(TMP, "build")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=BUILD,
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_CORRECTIONS=os.path.join(TMP, "config", "corrections.json"))
for d in ("records", "keys", "build", "config"):
    os.makedirs(os.path.join(TMP, d))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, db as dbm, main, medications, records   # noqa: E402
from server.importers import fhir   # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = "harbour lantern pebble crow"


def med(i, t, title, **d):
    return {"id": i, "t": t, "k": "med", "title": title}, {"type": "med", **d}


def records_of(*rows, rx=()):
    events, details = [], {}
    for e, d in rows:
        events.append(e)
        details[str(e["id"])] = d
    events += list(rx)
    return {"events": events, "details": details}


def fill(i, t, title, **extra):
    return {"id": i, "t": t, "k": "rx", "title": title, "sub": extra.pop("sub", "Pharmacy fill"), **extra}


class Rules(unittest.TestCase):
    def test_grouped_by_the_drug_not_its_strength_or_spelling(self):
        for name in ("Fictorin 5 mg tablet", "FICTORIN 10MG TAB", "fictorin (Brandix) 2.5 mg oral tablet", "Fictorin HCl 5 mg"):
            self.assertEqual(medications.med_key(name), "fictorin", name)
        self.assertEqual(medications.med_key("Examplo acid 400 mg"), "examplo acid")
        self.assertEqual(medications.med_key("Examplomab"), "examplomab")

    def test_start_dose_change_and_stop_as_spans_with_fills_marked(self):
        data = records_of(
            med(1, "2025-02-03T09:00:00", "Fictorin 5 mg tablet", instructions="Take 1 tablet daily", status="Active",
                src="Fictional Clinic", prescriber="Dr. Rowan Vale", rxnorm="9990001"),
            med(2, "2025-05-12T10:00:00", "Fictorin 10 mg tablet", instructions="Take 1 tablet daily", status="Active",
                src="Fictional Clinic", prescriber="Dr. Rowan Vale", rxnorm="9990002"),
            med(3, "2025-08-20T10:00:00", "Fictorin 10 mg tablet", instructions="Take 1 tablet daily", status="Discontinued",
                src="Fictional Clinic", end="2025-09-30"),
            rx=[fill(4, "2025-02-05T17:00:00", "FICTORIN 5MG TAB", sub="Pharmacy fill · 30-day supply"),
                fill(5, "2025-05-14T17:00:00", "FICTORIN 10MG TAB")])
        data["details"]["5"] = {"type": "claim", "kind": "Pharmacy", "provider": "Corner Pharmacy", "daysSupply": 90}
        (m,) = medications.history(data)
        self.assertEqual((m["name"], m["key"], m["rxnorm"], m["start"], m["stop"]),
                         ("Fictorin 10 mg tablet", "fictorin", ["9990001", "9990002"], "2025-02-03", "2025-09-30"))
        self.assertEqual([(s["from"], s["to"], s["open"], s["stopped"], s["title"]) for s in m["spans"]],
                         [("2025-02-03", "2025-05-12", False, False, "Fictorin 5 mg tablet"),
                          ("2025-05-12", "2025-09-30", False, True, "Fictorin 10 mg tablet")])
        self.assertEqual(m["spans"][1]["ids"], [2, 3])   # listed again, unchanged, then stopped: one span
        self.assertEqual(m["spans"][1]["status"], "Discontinued")
        self.assertEqual(m["spans"][0]["prescriber"], "Dr. Rowan Vale")
        self.assertEqual(m["changes"], [{"t": "2025-05-12", "from": "Fictorin 5 mg tablet · Take 1 tablet daily",
                                         "to": "Fictorin 10 mg tablet · Take 1 tablet daily", "id": 2}])
        self.assertEqual([(f["t"], f["id"], f["pharmacy"], f["supply"]) for f in m["fills"]],
                         [("2025-02-05", 4, None, 30), ("2025-05-14", 5, "Corner Pharmacy", 90)])

    def test_no_end_in_the_records_is_open(self):
        (m,) = medications.history(records_of(med(1, "2026-01-10", "Examplomab 10 mg", instructions="Once daily", status="Active")))
        self.assertEqual((m["spans"][0]["to"], m["spans"][0]["open"], m["stop"]), (None, True, None))

    def test_stopped_without_a_date_ends_where_it_was_listed(self):
        (m,) = medications.history(records_of(med(1, "2026-01-10", "Examplomab 10 mg", status="Completed")))
        self.assertEqual((m["spans"][0]["from"], m["spans"][0]["to"], m["spans"][0]["stopped"]), ("2026-01-10", "2026-01-10", True))

    def test_stopped_then_started_again_is_not_a_dose_change(self):
        (m,) = medications.history(records_of(
            med(1, "2025-01-01", "Examplomab 10 mg", instructions="Once daily", status="Active", end="2025-02-01"),
            med(2, "2025-06-01", "Examplomab 20 mg", instructions="Once daily", status="Active")))
        self.assertEqual([(s["from"], s["to"]) for s in m["spans"]], [("2025-01-01", "2025-02-01"), ("2025-06-01", None)])
        self.assertEqual(m["changes"], [])

    def test_the_same_listing_from_two_sources_is_one(self):
        (m,) = medications.history(records_of(
            med(1, "2025-03-01T08:00:00", "Fictorin 5 mg tablet", instructions="Once daily", status="Active", src="Clinic A"),
            med(2, "2025-03-01", "fictorin 5 mg tablet", instructions="once daily", src="Clinic B", prescriber="Dr. Ash Imaginary")))
        (s,) = m["spans"]
        self.assertEqual((s["ids"], s["sources"], s["prescriber"], s["status"]), ([1, 2], ["Clinic A", "Clinic B"], "Dr. Ash Imaginary", "Active"))

    def test_an_end_after_the_next_listing_gives_way_to_it(self):
        (m,) = medications.history(records_of(
            med(1, "2025-01-01", "Fictorin 5 mg", instructions="Once daily", end="2025-12-31"),
            med(2, "2025-04-01", "Fictorin 5 mg", instructions="Twice daily")))
        self.assertEqual([(s["from"], s["to"], s["stopped"]) for s in m["spans"]], [("2025-01-01", "2025-04-01", False), ("2025-04-01", None, False)])
        self.assertEqual(len(m["changes"]), 1)

    def test_fills_alone_and_hidden_records(self):
        data = records_of(med(1, "2025-01-01", "Examplomab 10 mg"), rx=[fill(2, "2025-02-01", "Fictazole 2 mg"), fill(3, "2025-03-01", "Fictazole 2 mg")])
        data["events"][0]["hidden"] = True
        (m,) = medications.history(data)
        self.assertEqual((m["name"], m["spans"], len(m["fills"]), m["start"], m["stop"]), ("Fictazole 2 mg", [], 2, "2025-02-01", None))
        self.assertEqual(medications.history({"events": [{"id": 1, "t": "2025-01-01", "k": "visit", "title": "Visit"}]}), [])

    def test_says_nothing_beyond_the_records(self):
        (m,) = medications.history(records_of(med(1, "2025-01-01", "Fictorin 5 mg", instructions="Once daily", status="Active")))
        self.assertEqual(set(m), {"name", "key", "rxnorm", "spans", "changes", "fills", "start", "stop"})
        self.assertEqual(set(m["spans"][0]), {"from", "to", "open", "stopped", "dose", "title", "status", "prescriber", "sources", "ids", "rxnorm"})


BUILT = {
    "built": "2026-11-01T00:00:00+00:00", "source": "fictional",
    "events": [
        {"id": 1, "t": "2026-02-10T09:15:00", "k": "visit", "title": "Outpatient visit", "sub": "Dr. Fictional"},
    ],
    "details": {}, "labs": [], "upcoming": [], "problems": [], "milestones": [], "costs": [],
}


class InTheRecords(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(BUILD, "data.json"), "w", encoding="utf-8") as f:
            json.dump(BUILT, f)
        cls.ctx = TestClient(main.app, base_url="http://testserver").__enter__()
        db = dbm.connect()
        uid = accounts.create(db, "robin", password=PW)[0]
        cls.pid = db.execute("SELECT id FROM profiles WHERE linked_build=1").fetchone()[0]
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'owner')", (uid, cls.pid))
        db.close()
        cls.c = TestClient(main.app, base_url="http://testserver")
        assert cls.c.post("/api/login", json={"username": "robin", "password": PW}, headers=H).status_code == 200

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        shutil.rmtree(TMP, True)

    def data(self):
        r = self.c.get(f"/api/profiles/{self.pid}/data")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_only_when_there_are_medications_and_never_stored_from_an_import(self):
        self.assertNotIn("medications", self.data())
        db = dbm.connect()
        with dbm.transaction(db):
            e1, d1 = med("a", "2026-03-01T08:00:00", "Fictorin 5 mg tablet", instructions="Once daily", status="Active", src="Fictional clinic")
            e2, d2 = med("b", "2026-06-01T08:00:00", "Fictorin 10 mg tablet", instructions="Once daily", status="Active", src="Fictional clinic")
            iid = records.store(db, self.pid, {"events": [e1, e2, fill("c", "2026-03-03T12:00:00", "FICTORIN 5MG TAB")],
                                               "details": {"a": d1, "b": d2},
                                               "medications": [{"name": "Planted", "spans": [], "fills": []}]}, "upload")
        db.close()
        try:
            data = self.data()
            (m,) = data["medications"]
            ids = {e["title"]: e["id"] for e in data["events"]}
            self.assertEqual((m["name"], len(m["spans"]), len(m["changes"]), [f["id"] for f in m["fills"]]),
                             ("Fictorin 10 mg tablet", 2, 1, [ids["FICTORIN 5MG TAB"]]))
            self.assertEqual(m["spans"][0]["ids"], [ids["Fictorin 5 mg tablet"]])   # the ids the page has
            # a correction (edits.py) that hides the first listing leaves one span, open, and no change
            r = self.c.post(f"/api/profiles/{self.pid}/edits", headers=H,
                            json={"event": ids["Fictorin 5 mg tablet"], "action": "hide"})
            self.assertEqual(r.status_code, 201, r.text)
            (m,) = self.data()["medications"]
            self.assertEqual([(s["from"], s["open"]) for s in m["spans"]], [("2026-06-01", True)])
            self.assertEqual(m["changes"], [])
        finally:
            db = dbm.connect()
            with dbm.transaction(db):
                db.execute("DELETE FROM edits")
                records.drop(db, self.pid, [iid])
            db.close()
        self.assertNotIn("medications", self.data())

    def test_a_rename_takes_what_hangs_on_its_medication(self):   # #921, #954
        """A rename groups a medication as it's shown (as before #921): it joins a medication of its new name, or
        leaves one it shared a key with. What hangs on a medication's key goes with it in the same save (#954): its
        reminders (and so the calendar feed's events) and the side effects linked to it by name go to the medication
        its newest listing is in now, and undoing the rename puts them back. Four renames, each undone:
        - Lisinopril renamed Zestril (#921's case): Zestril takes the reminder and the side effect, its fill (still
          "LISINOPRIL") a medication of fills alone; the feed still has the reminder.
        - Zestril, another clinic's listing of it, renamed Lisinopril: one medication, Zestril's reminder with it.
        - Vitamin B6 renamed Pyridoxine (B6 and B12 shared the key "vitamin b", named for B6, its newest listing):
          two medications, the reminder with Pyridoxine, B12 alone under "vitamin b".
        - Lisinopril renamed Zestril, then back: the reminder back on Lisinopril, and Undo leaves it there."""
        from datetime import date
        from server import journal, med_reminders
        ALL = ["mo", "tu", "we", "th", "fr", "sa", "su"]

        def store(*rows, rx=()):
            db = dbm.connect()
            with dbm.transaction(db):
                iid = records.store(db, self.pid, {"events": [e for e, _ in rows] + list(rx), "details": {e["id"]: d for e, d in rows}}, "upload")
            db.close()
            return iid

        def remind(key):
            r = self.c.post(f"/api/profiles/{self.pid}/medications/reminders", headers=H,
                            json={"med": key, "times": ["08:00"], "days": ALL, "start": "2026-11-01", "tz": "America/New_York"})
            self.assertEqual(r.status_code, 201, r.text)

        def rename(title, new):
            eid = next(e["id"] for e in self.data()["events"] if e["k"] == "med" and e["title"] == title)
            r = self.c.post(f"/api/profiles/{self.pid}/edits", headers=H, json={"event": eid, "action": "rename", "title": new})
            self.assertEqual(r.status_code, 201, r.text)
            return r.json()["id"]

        def undo(edit):
            self.assertEqual(self.c.delete(f"/api/profiles/{self.pid}/edits/{edit}", headers=H).status_code, 200)

        def state():
            d = self.data()
            db = dbm.connect()
            try:
                feed = len(med_reminders.feed_events(db, self.pid, False, date(2026, 11, 20))[0])
                side = [e["med"] for e in journal.listing(db, self.pid) if e.get("med")]
            finally:
                db.close()
            return (sorted((m["key"], m["name"], len(m["spans"]), len(m["fills"])) for m in d["medications"]),
                    sorted(r["med"] for r in d.get("reminders") or []), feed, side)

        def clean(*iids):
            db = dbm.connect()
            with dbm.transaction(db):
                for t in ("edits", "med_reminders", "journal"):
                    db.execute(f"DELETE FROM {t}")
                records.drop(db, self.pid, list(iids))
            db.close()

        # Lisinopril, with its pharmacy's fill, renamed Zestril
        iid = store(med("a", "2026-01-05T08:00:00", "Lisinopril 10 mg tablet", instructions="One tablet daily", status="Active"),
                    rx=[fill("f", "2026-01-06T12:00:00", "LISINOPRIL 10MG TAB")])
        try:
            remind("lisinopril")
            r = self.c.post(f"/api/profiles/{self.pid}/journal", headers=H,
                            json={"kind": "side_effect", "t": "2026-11-02", "title": "Dry cough", "severity": 3, "med": "Lisinopril 10 mg tablet"})
            self.assertEqual(r.status_code, 201, r.text)
            before = state()
            self.assertEqual(before, ([("lisinopril", "Lisinopril 10 mg tablet", 1, 1)], ["lisinopril"], 1, ["Lisinopril 10 mg tablet"]))
            edit = rename("Lisinopril 10 mg tablet", "Zestril 10 mg tablet")
            self.assertEqual(state(), ([("lisinopril", "LISINOPRIL 10MG TAB", 0, 1), ("zestril", "Zestril 10 mg tablet", 1, 0)], ["zestril"], 1,
                                       ["Zestril 10 mg tablet"]), "the reminder and the side effect with the renamed medication")
            undo(edit)
            self.assertEqual(state(), before, "undone: each where it was")
        finally:
            clean(iid)

        # another clinic's Zestril, renamed Lisinopril to make the two one
        iid = store(med("a", "2025-01-05T08:00:00", "Lisinopril 10 mg tablet", instructions="One tablet daily", status="Active"),
                    med("b", "2026-01-05T08:00:00", "Zestril 10 mg tablet", instructions="One tablet daily", status="Active"))
        try:
            remind("zestril")
            before = state()
            edit = rename("Zestril 10 mg tablet", "Lisinopril 10 mg tablet")
            self.assertEqual(state()[:3], ([("lisinopril", "Lisinopril 10 mg tablet", 1, 0)], ["lisinopril"], 1), "merged, the reminder with it")
            undo(edit)
            self.assertEqual(state(), before, "undone: two again, the reminder with Zestril")
        finally:
            clean(iid)

        # Vitamin B6 renamed Pyridoxine, to tell it from B12
        iid = store(med("a", "2026-01-10T08:00:00", "Vitamin B12 1000 mcg tablet", instructions="One daily", status="Active"),
                    med("b", "2026-03-01T08:00:00", "Vitamin B6 50 mg tablet", instructions="One daily", status="Active"))
        try:
            remind("vitamin b")
            before = state()
            self.assertEqual(before[0], [("vitamin b", "Vitamin B6 50 mg tablet", 2, 0)])
            edit = rename("Vitamin B6 50 mg tablet", "Pyridoxine 50 mg tablet")
            self.assertEqual(state()[:3], ([("pyridoxine", "Pyridoxine 50 mg tablet", 1, 0), ("vitamin b", "Vitamin B12 1000 mcg tablet", 1, 0)],
                                           ["pyridoxine"], 1), "split, the reminder with the one it was set on, by its name")
            undo(edit)
            self.assertEqual(state(), before)
        finally:
            clean(iid)

        # renamed, then back
        iid = store(med("a", "2026-01-05T08:00:00", "Lisinopril 10 mg tablet", instructions="One tablet daily", status="Active"))
        try:
            remind("lisinopril")
            before = state()
            rename("Lisinopril 10 mg tablet", "Zestril 10 mg tablet")
            self.assertEqual(state()[1], ["zestril"])
            edit = rename("Zestril 10 mg tablet", "Lisinopril 10 mg tablet")
            self.assertEqual(state(), before, "named as it was: the reminder back")
            undo(edit)
            self.assertEqual(state(), before, "and its undoing leaves it so")
        finally:
            clean(iid)


class Fhir(unittest.TestCase):
    def test_prescriber_end_and_rxnorm(self):
        bundle = {"resourceType": "Bundle", "type": "collection", "entry": [
            {"resource": {"resourceType": "Practitioner", "id": "pr1", "name": [{"given": ["Rowan"], "family": "Vale", "prefix": ["Dr."]}]}},
            {"resource": {"resourceType": "MedicationRequest", "id": "mr1", "status": "active", "intent": "order",
                          "authoredOn": "2025-02-03", "requester": {"reference": "Practitioner/pr1"},
                          "medicationCodeableConcept": {"text": "Fictorin 5 mg tablet", "coding": [
                              {"system": "http://www.nlm.nih.gov/research/umls/rxnorm", "code": "9990001"}]},
                          "dosageInstruction": [{"text": "Take 1 tablet daily"}]}},
            {"resource": {"resourceType": "MedicationStatement", "id": "ms1", "status": "completed",
                          "effectivePeriod": {"start": "2024-01-01", "end": "2024-03-01"},
                          "medicationCodeableConcept": {"text": "Examplomab 10 mg"}}},
        ]}
        folder = os.path.join(TMP, "fhir-in")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "bundle.json"), "w", encoding="utf-8") as f:
            json.dump(bundle, f)
        data = fhir.parse({"bundle.json": os.path.join(folder, "bundle.json")})["data"]
        details = {str(k): v for k, v in data["details"].items()}
        meds = {e["title"]: details[str(e["id"])] for e in data["events"] if e["k"] == "med"}
        self.assertEqual({k: meds["Fictorin 5 mg tablet"].get(k) for k in ("prescriber", "rxnorm", "end")},
                         {"prescriber": "Dr. Rowan Vale", "rxnorm": "9990001", "end": None})
        self.assertTrue(str(meds["Examplomab 10 mg"].get("end", "")).startswith("2024-03-01"), meds["Examplomab 10 mg"])
        self.assertNotIn("prescriber", meds["Examplomab 10 mg"])
        (m,) = [m for m in medications.history({**data, "details": details}) if m["key"] == "examplomab"]
        self.assertEqual((m["spans"][0]["to"], m["stop"]), ("2024-03-01", "2024-03-01"))


if __name__ == "__main__":
    unittest.main()
