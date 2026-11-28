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

    def test_your_own_medications_and_your_changes(self):   # #1091, #1093
        """A medication of the profile's own: added, changed, deleted (its dose logged kept) and put back by its key. A
        change to one of the records': applied over them, what they said beside it, and kept through a later pull of
        the same source that says something new; put back as listed. One hidden: out of the medications, its records
        kept, still hidden after a pull, shown again. A viewer can do none of it; the bodies are encrypted; the
        profile's deletion takes them."""
        from server import crypto, profiles
        db = dbm.connect()
        pid = profiles.create_profile(db, "Own medications (fictional)", owner_id=db.execute("SELECT id FROM users WHERE username='robin'").fetchone()[0])
        vid = accounts.create(db, "sam", password=PW)[0]
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (vid, pid))
        with dbm.transaction(db):
            first = records.store(db, pid, records_of(
                med("a", "2026-03-01T08:00:00", "Fictorin 5 mg tablet", instructions="Once daily", status="Active", src="Fictional clinic"),
                med("b", "2026-02-01T08:00:00", "Placebex 2 mg tablet", instructions="At night", status="Active")), "connection:9", fmt="fhir")
        db.close()
        p = f"/api/profiles/{pid}"

        def data():
            r = self.c.get(p + "/data")
            self.assertEqual(r.status_code, 200, r.text)
            return r.json()

        def meds():
            return {m["name"]: m for m in data().get("medications") or []}

        def pull(dose):   # the same source again, saying something new of Fictorin: its import in place of the first
            nonlocal first
            db = dbm.connect()
            with dbm.transaction(db):
                records.drop(db, pid, [first])
                first = records.store(db, pid, records_of(
                    med("a", "2026-03-01T08:00:00", "Fictorin 5 mg tablet", instructions="Once daily", status="Active", src="Fictional clinic"),
                    med("c", "2026-09-01T08:00:00", "Fictorin 5 mg tablet", instructions=dose, status="Active", src="Fictional clinic"),
                    med("b", "2026-02-01T08:00:00", "Placebex 2 mg tablet", instructions="At night", status="Active")), "connection:9", fmt="fhir")
            db.close()

        # an own entry: added (it's current, a span from its start), changed, deleted with its dose kept, put back
        r = self.c.post(p + "/medications/own", headers=H, json={"name": "Calmora", "strength": "10 mg", "dose": "1 tablet in the morning",
                                                                  "start": "2026-10-01", "status": "Active", "note": "Fictional"})
        self.assertEqual(r.status_code, 201, r.text)
        eid, key = r.json()["id"], r.json()["key"]
        self.assertTrue(key.startswith("own:"))
        self.assertIn("Calmora", {m["name"] for m in r.json()["records"]["medications"]}, "the reply carries the records")
        m = meds()["Calmora"]
        self.assertEqual((m["key"], m["own"]["id"], m["spans"][0]["from"], m["spans"][0]["open"], m["spans"][0]["title"], m["spans"][0]["dose"], m["note"]),
                         (key, eid, "2026-10-01", True, "Calmora 10 mg", "1 tablet in the morning", "Fictional"))
        self.assertEqual(self.c.post(p + "/medications/own", headers=H, json={"name": "No start"}).status_code, 400)
        r = self.c.put(f"{p}/medications/own/{eid}", headers=H, json={"name": "Calmora XR", "strength": "20 mg", "dose": "1 tablet",
                                                                        "start": "2026-10-01", "stop": "2026-11-01", "status": "Stopped"})
        self.assertEqual(r.status_code, 200, r.text)
        m = meds()["Calmora XR"]
        self.assertEqual((m["key"], m["stop"], m["spans"][0]["open"]), (key, "2026-11-01", False), "the same key; stopped")
        r = self.c.post(p + "/medications/doses", headers=H, json={"med": key, "t": "2026-10-20T08:00", "status": "taken"})
        self.assertEqual(r.status_code, 201, r.text)
        r = self.c.delete(f"{p}/medications/own/{eid}", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        gone = r.json()["removed"]
        d = data()
        self.assertNotIn("Calmora XR", {m["name"] for m in d["medications"]})
        self.assertEqual([x["med"] for x in d["doses"]], [key], "its dose stays in the log")
        r = self.c.post(p + "/medications/own", headers=H, json=gone)   # Undo
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(meds()["Calmora XR"]["key"], key, "put back with its key: its dose finds it again")
        self.assertEqual(self.c.post(p + "/medications/own", headers=H, json=gone).status_code, 400, "not twice")

        # a change to Fictorin of the records: what Edit shows, the dose changed
        fic = meds()["Fictorin 5 mg tablet"]
        r = self.c.put(p + "/medications/listed", headers=H, json={"key": fic["key"], "name": "Fictorin 5 mg tablet", "strength": "",
                                                                    "dose": "Twice daily", "start": "2026-03-01", "stop": "", "status": "Active", "note": "Fictional"})
        self.assertEqual(r.status_code, 200, r.text)
        m = meds()["Fictorin 5 mg tablet"]
        self.assertEqual((m["spans"][-1]["dose"], m["listed"]["dose"], m["listed"]["spans"][-1]["dose"], m["note"]),
                         ("Twice daily", "Once daily", "Once daily", "Fictional"))
        self.assertEqual({k for k in m["change"] if k in medications.FIELDS}, {"dose", "note"}, "only what it changes is kept")
        pull("Once daily with food")
        m = meds()["Fictorin 5 mg tablet"]
        self.assertEqual((m["spans"][-1]["dose"], m["listed"]["dose"]), ("Twice daily", "Once daily with food"), "a pull keeps the change")
        listed = {k: v for k, v in m["listed"].items() if k in medications.FIELDS}
        r = self.c.put(p + "/medications/listed", headers=H, json={"key": fic["key"], **listed})   # Put back as listed
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()["id"])
        m = meds()["Fictorin 5 mg tablet"]
        self.assertEqual((m["spans"][-1]["dose"], "change" in m), ("Once daily with food", False))
        self.assertEqual(self.c.put(p + "/medications/listed", headers=H, json={"key": "no such", "dose": "x"}).status_code, 404)

        # Placebex hidden: out of the medications, its records kept, hidden still after a pull; shown again
        from datetime import date
        from server import med_reminders

        def feed():   # (#1093) its schedule: the calendar feed's reminders
            db = dbm.connect()
            try:
                return len(med_reminders.feed_events(db, pid, False, date(2026, 11, 20))[0])
            finally:
                db.close()
        plc = meds()["Placebex 2 mg tablet"]["key"]
        r = self.c.post(p + "/medications/reminders", headers=H, json={"med": plc, "times": ["21:00"], "days": ["mo", "tu", "we", "th", "fr", "sa", "su"],
                                                                        "start": "2026-11-01", "tz": "America/New_York"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(feed(), 1)
        r = self.c.put(p + "/medications/hidden", headers=H, json={"key": plc, "hidden": True})
        self.assertEqual(r.status_code, 200, r.text)
        pull("Once daily")
        d = data()
        self.assertNotIn("Placebex 2 mg tablet", {m["name"] for m in d["medications"]})
        self.assertEqual([(h["key"], h["name"]) for h in d["hiddenMeds"]], [(plc, "Placebex 2 mg tablet")])
        self.assertIn("Placebex 2 mg tablet", {e["title"] for e in d["events"]}, "its records stay")
        self.assertEqual((feed(), [x["med"] for x in d["reminders"]]), (0, [plc]), "out of the feed, its reminder kept")
        self.assertEqual(self.c.put(p + "/medications/hidden", headers=H, json={"key": plc, "hidden": False}).status_code, 200)
        d = data()
        self.assertIn("Placebex 2 mg tablet", {m["name"] for m in d["medications"]})
        self.assertNotIn("hiddenMeds", d)
        self.assertEqual(feed(), 1, "shown again: in the feed again")

        # a viewer reads them and changes none
        sam = TestClient(main.app, base_url="http://testserver")
        self.assertEqual(sam.post("/api/login", json={"username": "sam", "password": PW}, headers=H).status_code, 200)
        self.assertEqual(sam.get(p + "/medications/entries").status_code, 200)
        for method, path, body in (("POST", "/medications/own", {"name": "X", "start": "2026-01-01"}), ("PUT", f"/medications/own/{eid}", {"name": "X", "start": "2026-01-01"}),
                                   ("DELETE", f"/medications/own/{eid}", None), ("PUT", "/medications/listed", {"key": plc, "dose": "x"}),
                                   ("PUT", "/medications/hidden", {"key": plc, "hidden": True})):
            self.assertEqual(sam.request(method, p + path, headers=H, json=body).status_code, 403, (method, path))

        db = dbm.connect()
        try:
            bodies = [r[0] for r in db.execute("SELECT body FROM med_entries WHERE profile_id=?", (pid,))]
            self.assertTrue(bodies and all(b.startswith(crypto.FIELD) for b in bodies), "encrypted")
            profiles.delete_profile(db, pid)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM med_entries WHERE profile_id=?", (pid,)).fetchone()[0], 0, "gone with the profile")
        finally:
            db.close()

    def test_one_list_for_the_summary_share_links_and_fhir(self):   # #1091
        """The visit summary (preview and PDF), a share link and the FHIR export read the medications the page shows:
        one hidden is in none of them; your own entry is, and one you changed is, as changed. A profile with nothing
        imported but a medication of its own has records of it: the page's data lists it, and the summary too."""
        import io, zipfile
        from pypdf import PdfReader
        from server import profiles
        db = dbm.connect()
        uid = db.execute("SELECT id FROM users WHERE username='robin'").fetchone()[0]
        pid = profiles.create_profile(db, "One list (fictional)", owner_id=uid)
        empty = profiles.create_profile(db, "Nothing imported (fictional)", owner_id=uid)
        with dbm.transaction(db):
            records.store(db, pid, records_of(
                med("a", "2026-03-01T08:00:00", "Fictorin 5 mg tablet", instructions="Once daily", status="Active"),
                med("b", "2026-02-01T08:00:00", "Placebex 2 mg tablet", instructions="At night", status="Active")), "upload")
        db.close()
        p = f"/api/profiles/{pid}"
        meds = {m["name"]: m["key"] for m in self.c.get(p + "/data").json()["medications"]}
        for name in ("Placebex 2 mg tablet", "Fictorin 5 mg tablet"):   # a dose of each, logged before one's hidden
            r = self.c.post(p + "/medications/doses", headers=H, json={"med": meds[name], "t": "2026-11-20T08:00", "status": "taken", "note": name + " DOSENOTE"})
            self.assertEqual(r.status_code, 201, r.text)
            r = self.c.post(p + "/journal", headers=H, json={"kind": "side_effect", "t": "2026-11-20T09:00", "title": "Nausea " + name[:4],
                                                             "severity": 2, "med": name})   # and a side effect of each
            self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(self.c.put(p + "/medications/hidden", headers=H, json={"key": meds["Placebex 2 mg tablet"], "hidden": True}).status_code, 200)
        self.assertEqual(self.c.put(p + "/medications/listed", headers=H, json={"key": meds["Fictorin 5 mg tablet"], "dose": "Twice daily CHANGED"}).status_code, 200)
        self.assertEqual(self.c.post(p + "/medications/own", headers=H, json={"name": "Calmora OWNENTRY", "dose": "1 tablet", "start": "2026-10-01"}).status_code, 201)

        def said(text, where):
            self.assertIn("Calmora OWNENTRY", text, where)
            self.assertIn("Twice daily CHANGED", text, where)
            self.assertNotIn("Once daily", text, where + ": the records' words, changed")
            self.assertNotIn("Placebex", text, where + ": the hidden one")

        opts = {"sections": ["medications"]}
        r = self.c.post(p + "/summary/preview", headers=H, json=opts)
        self.assertEqual(r.status_code, 200, r.text)
        said(json.dumps(r.json()), "the summary")
        r = self.c.post(p + "/summary/pdf", headers=H, json=opts)
        self.assertEqual(r.status_code, 200, r.text[:300])
        said("\n".join(x.extract_text() for x in PdfReader(io.BytesIO(r.content)).pages), "the summary's PDF")
        r = self.c.post(p + "/links", headers=H, json={"sections": ["medications"]})
        self.assertEqual(r.status_code, 200, r.text)
        r = TestClient(main.app, base_url="http://testserver").post("/api/share/view", headers=H, json={"token": r.json()["path"].split("#", 1)[1]})
        self.assertEqual(r.status_code, 200, r.text)
        said(json.dumps(r.json()["medications"]), "the share link")
        # (#1093) its medication log: the hidden one's dose left out, as the medication is; the log keeps it
        r = self.c.post(p + "/links", headers=H, json={"sections": ["medlog"]})
        self.assertEqual(r.status_code, 200, r.text)
        r = TestClient(main.app, base_url="http://testserver").post("/api/share/view", headers=H, json={"token": r.json()["path"].split("#", 1)[1]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Fictorin 5 mg tablet DOSENOTE", r.text, "the medication log in the link")
        self.assertNotIn("Placebex", r.text, "not the hidden one's dose")
        r = self.c.post(p + "/summary/preview", headers=H, json={"sections": ["medlog"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Fictorin 5 mg tablet DOSENOTE", r.text)
        self.assertNotIn("Placebex", r.text, "nor in the visit summary's")
        self.assertEqual(len(self.c.get(p + "/data").json()["doses"]), 2, "both kept in the log")
        # your own entry deleted: its dose left out of both medication logs, as a hidden one's; the log keeps it
        own = next(m for m in self.c.get(p + "/data").json()["medications"] if m.get("own"))
        r = self.c.post(p + "/medications/doses", headers=H, json={"med": own["key"], "t": "2026-11-21T08:00", "status": "taken", "note": "OWNDOSE"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertIn("OWNDOSE", self.c.post(p + "/summary/preview", headers=H, json={"sections": ["medlog"]}).text, "while it's there")
        self.assertEqual(self.c.delete(p + f"/medications/own/{own['own']['id']}", headers=H).status_code, 200)
        r = self.c.post(p + "/summary/preview", headers=H, json={"sections": ["medlog"]})
        self.assertNotIn("OWNDOSE", r.text, "the summary's log, once it's deleted")
        self.assertIn("Fictorin 5 mg tablet DOSENOTE", r.text)
        r = self.c.post(p + "/links", headers=H, json={"sections": ["medlog"]})
        r = TestClient(main.app, base_url="http://testserver").post("/api/share/view", headers=H, json={"token": r.json()["path"].split("#", 1)[1]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("OWNDOSE", r.text, "nor a share link's")
        self.assertNotIn("Calmora", json.dumps(r.json().get("medlog")), "nor its name")
        self.assertIn("OWNDOSE", json.dumps(self.c.get(p + "/data").json()["doses"]), "kept in the log")
        self.assertEqual(self.c.post(p + "/medications/own", headers=H, json={"name": "Calmora OWNENTRY", "dose": "1 tablet", "start": "2026-10-01"}).status_code, 201)
        # (#1093) a journal entry in a link says the other medication, not the hidden one's name; the entry is still in
        # it, and in the journal with its medication; the journal form's picker leaves the hidden one out
        r = self.c.post(p + "/links", headers=H, json={"sections": ["journal"]})
        self.assertEqual(r.status_code, 200, r.text)
        r = TestClient(main.app, base_url="http://testserver").post("/api/share/view", headers=H, json={"token": r.json()["path"].split("#", 1)[1]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(sorted((j["title"], j.get("med")) for j in r.json()["journal"]),
                         [("Nausea Fict", "Fictorin 5 mg tablet"), ("Nausea Plac", None)])
        self.assertNotIn("Placebex", r.text, "not the hidden one's name")
        j = self.c.get(p + "/journal").json()
        self.assertIn("Placebex 2 mg tablet", [e["med"] for e in j["entries"]], "the journal keeps it")
        self.assertNotIn("Placebex 2 mg tablet", j["medications"], "the picker leaves it out")
        self.assertIn("Fictorin 5 mg tablet", j["medications"])
        r = self.c.get(p + "/export?format=fhir")
        self.assertEqual(r.status_code, 200, r.text[:300])
        z = zipfile.ZipFile(io.BytesIO(r.content))
        bundle = json.loads(z.read(next(n for n in z.namelist() if n.endswith(".json"))))
        sts = {x["resource"]["medicationCodeableConcept"]["text"]: x["resource"] for x in bundle["entry"]
               if x["resource"]["resourceType"] == "MedicationStatement"}
        self.assertEqual(sts["Calmora OWNENTRY"]["dosage"], [{"text": "1 tablet"}], "your own, as a MedicationStatement")
        self.assertEqual(sts["Calmora OWNENTRY"]["status"], "active")
        self.assertEqual(sts["Fictorin 5 mg tablet"]["dosage"], [{"text": "Twice daily CHANGED"}], "your change made")

        # nothing imported, a medication of its own: records of it, the summary listing it
        e = f"/api/profiles/{empty}"
        self.assertEqual(self.c.get(e + "/data").status_code, 204)
        self.assertEqual(self.c.post(e + "/medications/own", headers=H, json={"name": "Solely OWN", "start": "2026-10-01"}).status_code, 201)
        r = self.c.get(e + "/data")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(([m["name"] for m in r.json()["medications"]], r.json()["events"], r.json()["imports"]), (["Solely OWN"], [], {}))
        r = self.c.post(e + "/summary/preview", headers=H, json=opts)
        self.assertIn("Solely OWN", json.dumps(r.json()))
        (own,) = self.c.get(e + "/medications/entries").json()["entries"]
        self.assertEqual(self.c.delete(f"{e}/medications/own/{own['id']}", headers=H).status_code, 200)
        self.assertEqual(self.c.get(e + "/data").status_code, 204, "deleted: no records again")
        db = dbm.connect()
        try:
            profiles.delete_profile(db, pid)
            profiles.delete_profile(db, empty)
        finally:
            db.close()

    def test_stopped_taking_kept_in_the_history(self):   # #1161
        """Stopped taking: one of your own and one of the records' marked stopped on a day with a reason or none, out of
        Current with their dates and reason, the schedule ended there (the calendar feed, the doses a day gives), not on
        the visit summary or the emergency card's suggestions, the FHIR export saying stopped with the end date; kept through a
        re-import of the records; Undo puts each back; Started again a span after the time before, current again, and
        stopped again; what can't be (stopped already, a day before it started) refused; a viewer can do none of it.
        Records that later give an end date show as stopped the same way."""
        import io, zipfile
        from datetime import date
        from server import emergency, export, med_log, med_reminders, profiles
        db = dbm.connect()
        uid = db.execute("SELECT id FROM users WHERE username='robin'").fetchone()[0]
        pid = profiles.create_profile(db, "Stopped taking (fictional)", owner_id=uid)
        vid = accounts.create(db, "kit", password=PW)[0]
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (vid, pid))

        def store(*rows):
            db = dbm.connect()
            with dbm.transaction(db):
                for (iid,) in db.execute("SELECT id FROM imports WHERE profile_id=?", (pid,)).fetchall():
                    records.drop(db, pid, [iid])
                records.store(db, pid, records_of(*rows), "connection:9", fmt="fhir")
            db.close()
        FIC = med("a", "2026-03-01T08:00:00", "Fictorin 5 mg tablet", instructions="Once daily", status="Active", src="Fictional clinic")
        PLC = med("b", "2026-02-01T08:00:00", "Placebex 2 mg tablet", instructions="At night", status="Active", src="Fictional clinic")
        store(FIC, PLC)
        db.close()
        p = f"/api/profiles/{pid}"

        def meds():
            return {m["name"]: m for m in self.c.get(p + "/data").json().get("medications") or []}

        def current():
            return sorted(m["name"] for m in meds().values() if m["spans"] and m["spans"][-1]["open"])

        def summary_and_card():
            r = self.c.post(p + "/summary/preview", headers=H, json={"sections": ["medications"]})
            self.assertEqual(r.status_code, 200, r.text)
            db = dbm.connect()
            try:
                card = [s["text"] for s in emergency.suggestions(db, pid, uid) if s["section"] == "medications"]
            finally:
                db.close()
            return [n for n in ("Calmora", "Fictorin", "Placebex") if n in json.dumps(r.json())], [t.split(":")[0].split()[0] for t in card]

        def fhir():
            r = self.c.get(p + "/export?format=fhir")
            self.assertEqual(r.status_code, 200, r.text[:300])
            z = zipfile.ZipFile(io.BytesIO(r.content))
            bundle = json.loads(z.read(next(n for n in z.namelist() if n.endswith(".json"))))
            return sorted((x["resource"]["medicationCodeableConcept"]["text"].split()[0], x["resource"]["status"],
                           x["resource"]["effectivePeriod"].get("end"), [c.get("text") for c in x["resource"].get("statusReason") or []])
                          for x in bundle["entry"] if x["resource"]["resourceType"] == "MedicationStatement")

        def feed():
            db = dbm.connect()
            try:
                return len(med_reminders.feed_events(db, pid, False, date(2026, 11, 20))[0])
            finally:
                db.close()

        def doses(key, day):
            db = dbm.connect()
            try:
                m = med_reminders.current(db, pid)[key]
                return med_log.scheduled([r for r in self.c.get(p + "/data").json()["reminders"] if r["med"] == key], date.fromisoformat(day), m.get("stop"))
            finally:
                db.close()

        r = self.c.post(p + "/medications/own", headers=H, json={"name": "Calmora", "dose": "1 tablet", "start": "2026-10-01", "status": "Active"})
        self.assertEqual(r.status_code, 201, r.text)
        own, fic, plc = r.json()["key"], meds()["Fictorin 5 mg tablet"]["key"], meds()["Placebex 2 mg tablet"]["key"]
        for k in (own, fic):
            r = self.c.post(p + "/medications/reminders", headers=H, json={"med": k, "times": ["21:00"], "days": ["mo", "tu", "we", "th", "fr", "sa", "su"],
                                                                            "start": "2026-11-01", "tz": "America/New_York"})
            self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual((current(), feed(), summary_and_card()), (["Calmora", "Fictorin 5 mg tablet", "Placebex 2 mg tablet"], 2,
                                                                    (["Calmora", "Fictorin", "Placebex"], ["Calmora", "Fictorin", "Placebex"])))

        # marked stopped: your own with a reason, the records' without
        r = self.c.post(p + "/medications/stopped", headers=H, json={"key": own, "day": "2026-11-10", "reason": "  Made me dizzy, my words  "})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("records", r.json())
        was_own = r.json()["was"]
        r = self.c.post(p + "/medications/stopped", headers=H, json={"key": fic, "day": "2026-11-12"})
        self.assertEqual(r.status_code, 200, r.text)
        was_fic = r.json()["was"]
        self.assertEqual(was_fic, {}, "nothing changed of it before")
        m = meds()
        self.assertEqual(current(), ["Placebex 2 mg tablet"])
        self.assertEqual([(x["stop"], x["spans"][-1]["to"], x["spans"][-1]["stopped"], x["spans"][-1].get("reason"), x["spans"][0]["from"]) for x in (m["Calmora"], m["Fictorin 5 mg tablet"])],
                         [("2026-11-10", "2026-11-10", True, "Made me dizzy, my words", "2026-10-01"), ("2026-11-12", "2026-11-12", True, None, "2026-03-01")])
        self.assertEqual(m["Fictorin 5 mg tablet"]["listed"]["spans"][-1]["open"], True, "the records stay as they are")
        self.assertEqual((feed(), doses(own, "2026-11-10"), doses(own, "2026-11-11"), doses(fic, "2026-11-13")),
                         (0, [("21:00", {"amount": None, "unit": ""})], [], []), "the schedule ends on the day it stopped")
        self.assertEqual(summary_and_card(), (["Placebex"], ["Placebex"]), "the summary and the card list current ones only")
        self.assertEqual(fhir(), [("Calmora", "stopped", "2026-11-10", ["Made me dizzy, my words"]), ("Fictorin", "stopped", "2026-11-12", []),
                                  ("Placebex", "active", None, [])])
        for body, why in (({"key": own, "day": "2026-11-11"}, "stopped already"), ({"key": plc, "day": "2026-01-31"}, "before it started"),
                          ({"key": plc, "day": "2026-02-30"}, "no such day"), ({"key": plc, "day": "soon"}, "not a day"),
                          ({"key": plc, "day": "2026-11-11", "reason": "x" * 501}, "too long")):
            self.assertEqual(self.c.post(p + "/medications/stopped", headers=H, json=body).status_code, 400, why)
        self.assertEqual(self.c.post(p + "/medications/stopped", headers=H, json={"key": "no such", "day": "2026-11-11"}).status_code, 404)

        # a re-import that says something new of Fictorin keeps the mark; Undo puts each back
        store(FIC, med("c", "2026-06-01T08:00:00", "Fictorin 5 mg tablet", instructions="Once daily with food", status="Active", src="Fictional clinic"), PLC)
        m = meds()["Fictorin 5 mg tablet"]
        self.assertEqual((m["stop"], m["spans"][-1]["dose"], m["listed"]["dose"]), ("2026-11-12", "Once daily with food", "Once daily with food"), "kept through it")
        r = self.c.put(p + "/medications/taking", headers=H, json={"key": fic, "was": was_fic})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("change", meds()["Fictorin 5 mg tablet"], "Undo: as the records list it, nothing kept")
        self.assertEqual(self.c.put(p + "/medications/taking", headers=H, json={"key": own, "was": was_own}).status_code, 200)
        self.assertEqual((current(), feed()), (["Calmora", "Fictorin 5 mg tablet", "Placebex 2 mg tablet"], 2))
        self.assertEqual(meds()["Calmora"]["own"]["status"], "Active")

        # Started again: stopped, then taken again from a later day; the time before kept, current again; stopped again
        self.assertEqual(self.c.post(p + "/medications/stopped", headers=H, json={"key": own, "day": "2026-10-20", "reason": "Ran out"}).status_code, 200)
        self.assertEqual(self.c.post(p + "/medications/again", headers=H, json={"key": own, "day": "2026-10-19"}).status_code, 400, "before it stopped")
        self.assertEqual(self.c.post(p + "/medications/again", headers=H, json={"key": plc, "day": "2026-11-19"}).status_code, 400, "taken now")
        r = self.c.post(p + "/medications/again", headers=H, json={"key": own, "day": "2026-11-05"})
        self.assertEqual(r.status_code, 200, r.text)
        was_again = r.json()["was"]
        m = meds()["Calmora"]
        self.assertEqual([(s["from"], s["to"], s["open"], s.get("reason"), bool(s.get("again"))) for s in m["spans"]],
                         [("2026-10-01", "2026-10-20", False, "Ran out", False), ("2026-11-05", None, True, None, True)])
        self.assertEqual((m["stop"], "Calmora" in current(), feed(), summary_and_card()[1]), (None, True, 2, ["Calmora", "Fictorin", "Placebex"]))
        r = self.c.put(f"{p}/medications/own/{m['own']['id']}", headers=H, json={**{k: m["own"].get(k, "") for k in medications.FIELDS}, "note": "Edited"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(meds()["Calmora"]["spans"]), 2, "Edit keeps the times it was started again")
        self.assertEqual(self.c.post(p + "/medications/stopped", headers=H, json={"key": own, "day": "2026-11-15", "reason": "Done"}).status_code, 200)
        m = meds()["Calmora"]
        self.assertEqual([(s["from"], s["to"], s.get("reason")) for s in m["spans"]], [("2026-10-01", "2026-10-20", "Ran out"), ("2026-11-05", "2026-11-15", "Done")])
        self.assertEqual((m["stop"], feed()), ("2026-11-15", 1))
        self.assertIn(("Calmora", "stopped", "2026-11-15", ["Done"]), fhir())
        self.assertIn(("Calmora", "stopped", "2026-10-20", ["Ran out"]), fhir(), "each time, its own statement")
        # an export carries the times it was started again, and an import of it takes them
        (row,) = [x for x in self.c.get(p + "/medications/entries").json()["entries"] if x["key"] == own]
        self.assertEqual(export._med_entry(row)["again"], [{"from": "2026-11-05", "to": "2026-11-15", "reason": "Done"}])
        with self.assertRaises(ValueError):
            export._med_entry({**row, "again": [{"from": "2026-11-05", "to": "2026-11-01"}]})
        self.assertEqual(self.c.put(p + "/medications/taking", headers=H, json={"key": own, "was": was_again}).status_code, 200)
        self.assertEqual([(s["from"], s["to"]) for s in meds()["Calmora"]["spans"]], [("2026-10-01", "2026-10-20")], "Undo of Started again")

        # records that later give an end date: stopped the same way, current no more
        store(FIC, med("c", "2026-06-01T08:00:00", "Fictorin 5 mg tablet", instructions="Once daily", status="Completed", end="2026-11-01", src="Fictional clinic"), PLC)
        m = meds()["Fictorin 5 mg tablet"]
        self.assertEqual((m["stop"], "Fictorin 5 mg tablet" in current(), summary_and_card()[1]), ("2026-11-01", False, ["Placebex"]))
        r = self.c.post(p + "/medications/again", headers=H, json={"key": fic, "day": "2026-11-20"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(([s["from"] for s in meds()["Fictorin 5 mg tablet"]["spans"]][-1], "Fictorin 5 mg tablet" in current()), ("2026-11-20", True))

        kit = TestClient(main.app, base_url="http://testserver")
        self.assertEqual(kit.post("/api/login", json={"username": "kit", "password": PW}, headers=H).status_code, 200)
        for method, path, body in (("POST", "/medications/stopped", {"key": plc, "day": "2026-11-11"}), ("POST", "/medications/again", {"key": own, "day": "2026-11-11"}),
                                   ("PUT", "/medications/taking", {"key": own, "was": {}})):
            self.assertEqual(kit.request(method, p + path, headers=H, json=body).status_code, 403, path)
        db = dbm.connect()
        try:
            profiles.delete_profile(db, pid)
        finally:
            db.close()

    def test_the_days_between_the_times_it_was_taken(self):   # #1161
        """Started again after it stopped: the days between give no dose. Its schedule's counts don't take them as
        missed, the doses a day gives are none on them, the calendar feed starts its event on the day it was started
        again, and the visit summary lists it only while it's taken (not before a Started again to come)."""
        from datetime import date, datetime
        from server import med_log, med_reminders, profiles
        db = dbm.connect()
        uid = db.execute("SELECT id FROM users WHERE username='robin'").fetchone()[0]
        pid = profiles.create_profile(db, "The days between (fictional)", owner_id=uid)
        db.close()
        p = f"/api/profiles/{pid}"
        r = self.c.post(p + "/medications/own", headers=H, json={"name": "Calmora", "dose": "1 tablet", "start": "2026-10-01", "status": "Active"})
        self.assertEqual(r.status_code, 201, r.text)
        key = r.json()["key"]
        r = self.c.post(p + "/medications/reminders", headers=H, json={"med": key, "times": ["21:00"], "days": ["mo", "tu", "we", "th", "fr", "sa", "su"],
                                                                        "start": "2026-10-01", "tz": "UTC"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(self.c.post(p + "/medications/stopped", headers=H, json={"key": key, "day": "2026-10-10"}).status_code, 200)
        self.assertEqual(self.c.post(p + "/medications/again", headers=H, json={"key": key, "day": "2026-10-20"}).status_code, 200)
        d = self.c.get(p + "/data").json()
        (m,) = d["medications"]
        lines = [x for x in d["reminders"] if x["med"] == key]
        db = dbm.connect()
        try:
            events, _ = med_reminders.feed_events(db, pid, False, date(2026, 11, 20))
        finally:
            db.close()
        self.assertEqual([[x for x in e["lines"] if x.startswith("DTSTART")] for e in events], [["DTSTART;TZID=UTC:20261020T210000"]],
                         "the feed's event starts on the day it was started again, the time before over")
        self.assertEqual((medications.periods(m), medications.gaps(m)), ([("2026-10-01", "2026-10-10"), ("2026-10-20", None)], [("2026-10-11", "2026-10-19")]))
        gaps = medications.gaps(m)
        self.assertEqual([len(med_log.scheduled(lines, date(2026, 10, n), m.get("stop"), gaps)) for n in (10, 11, 19, 20)], [1, 0, 0, 1])
        days = [date(2026, 10, n) for n in range(1, 32)]
        n = med_log.schedule_counts(lines, [{"t": "2026-10-05T21:00", "status": "taken"}], days, datetime(2026, 10, 31, 22, 0), m.get("stop"), gaps)
        self.assertEqual((n["scheduled"], n["taken"]), (31 - 9, 1), "the nine days between aren't counted as missed")
        # a Started again to come: today falls between, so it's not current on the summary
        self.assertEqual(self.c.post(p + "/medications/stopped", headers=H, json={"key": key, "day": "2026-10-25"}).status_code, 200)
        self.assertEqual(self.c.post(p + "/medications/again", headers=H, json={"key": key, "day": "2099-01-01"}).status_code, 200)
        r = self.c.post(p + "/summary/preview", headers=H, json={"sections": ["medications"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("Calmora", json.dumps(r.json()), "not taken today")
        db = dbm.connect()
        try:
            (e,) = med_reminders.feed_events(db, pid, False, date(2026, 11, 20))[0]
            self.assertIn("DTSTART;TZID=UTC:20990101T210000", e["lines"], "the feed: from the day it's taken again")
            profiles.delete_profile(db, pid)
        finally:
            db.close()

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
