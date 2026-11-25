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

    def test_renamed_keeps_its_reminders_and_fills(self):   # #921
        """A listing renamed to its brand name in the drawer (edits.py): the medication was grouped by the name shown,
        so it took a new key ("zestril"), and its reminder (and dose) stayed under the old one: the calendar feed's
        events went, and the pharmacy's fill, still "LISINOPRIL", stayed behind as a medication of its own holding the
        reminder. Now it keeps its key, shown by its new name with the fill beside it; the reminder is its and in the
        feed; the new name finds it too ("aka": a side effect linked by name, a reminder set under it before). Undone,
        it's as before."""
        from datetime import date
        from server import med_reminders
        db = dbm.connect()
        with dbm.transaction(db):
            e, d = med("a", "2026-01-05T08:00:00", "Lisinopril 10 mg tablet", instructions="One tablet daily", status="Active")
            iid = records.store(db, self.pid, {"events": [e, fill("b", "2026-01-06T12:00:00", "LISINOPRIL 10MG TAB")],
                                               "details": {"a": d}}, "upload")
        db.close()
        try:
            data = self.data()
            listing = next(x["id"] for x in data["events"] if x["k"] == "med")
            r = self.c.post(f"/api/profiles/{self.pid}/medications/reminders", headers=H,
                            json={"med": "lisinopril", "times": ["08:00"], "days": ["mo", "tu", "we", "th", "fr", "sa", "su"],
                                  "start": "2026-11-01", "tz": "America/New_York"})
            self.assertEqual(r.status_code, 201, r.text)
            r = self.c.post(f"/api/profiles/{self.pid}/edits", headers=H, json={"event": listing, "action": "rename", "title": "Zestril 10 mg tablet"})
            self.assertEqual(r.status_code, 201, r.text)
            data = self.data()
            (m,) = data["medications"]
            self.assertEqual((m["key"], m["name"], m.get("aka"), len(m["fills"]), m["spans"][-1]["open"]),
                             ("lisinopril", "Zestril 10 mg tablet", ["zestril"], 1, True), "one medication, its fill beside it")
            self.assertEqual([x["med"] for x in data["reminders"]], ["lisinopril"])
            db = dbm.connect()
            try:
                events, _ = med_reminders.feed_events(db, self.pid, False, date(2026, 11, 20))
                self.assertEqual(len(events), 1, "the reminder still in the calendar feed")
                self.assertEqual(med_reminders.current(db, self.pid)["zestril"]["key"], "lisinopril", "set under its new name before: still its")
            finally:
                db.close()
            r = self.c.post(f"/api/profiles/{self.pid}/medications/reminders", headers=H,
                            json={"med": "lisinopril", "times": ["20:00"], "days": ["mo"], "start": "2026-11-01", "tz": "America/New_York"})
            self.assertEqual(r.status_code, 201, "a current medication still")
            (rename,) = self.c.get(f"/api/profiles/{self.pid}/edits").json()["edits"]
            self.assertEqual(self.c.delete(f"/api/profiles/{self.pid}/edits/{rename['id']}", headers=H).status_code, 200)
            (m,) = self.data()["medications"]
            self.assertEqual((m["key"], m["name"], m.get("aka")), ("lisinopril", "Lisinopril 10 mg tablet", None), "undone: as before")
        finally:
            db = dbm.connect()
            with dbm.transaction(db):
                db.execute("DELETE FROM edits")
                db.execute("DELETE FROM med_reminders")
                records.drop(db, self.pid, [iid])
            db.close()


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
