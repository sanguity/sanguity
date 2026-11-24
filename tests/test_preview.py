#!/usr/bin/env python3
"""Previewing an import before it's saved (#22) and the stable source keys it matches on (#89): re-uploading the
same export shows everything as duplicates and saving it adds nothing (C-CDA, FHIR, CSV and a Sanguity export); an
overlapping export adds only what's new; a duplicate can be kept anyway; a trimmed lab day still holds together;
cancelling leaves no rows, no preview file and no upload; roles and CSRF; key hashes backfilled at start-up; the
preview encrypted on disk; and every importer's keys unique and stable whatever order the files come in.

Fictional data only.

  python3 tests/test_preview.py
"""
import io, json, os, shutil, sys, tempfile, threading, time, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-preview-test-")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "no-build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"))
os.makedirs(os.path.join(TMP, "records"))
os.makedirs(os.path.join(TMP, "keys"))
sys.path.insert(0, ROOT)

import logging   # noqa: E402

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, activity, crypto, db as dbm, importers as reg, jobs, main, preview, profiles as prof, records, uploads   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites", "ash": "copper meadow lantern drift",
      "lee": "maple tunnel brisk saddle"}
FIX = os.path.join(ROOT, "tests", "fixtures", "importers")
CASES = {"ccda": "ccda/maple-grove-ccd", "fhir": "fhir/bundle", "csv": "csv/us_comma", "sanguity": "sanguity/export"}

CSV_HEAD = "Date,Test,Result,Units,Reference Range,Flag,Panel\n"
MARCH = ("03/14/2025,Hemoglobin,12.9,g/dL,13.5 - 17.5,L,CBC\n"
         "03/14/2025,LDL Cholesterol,131,mg/dL,<100,,Lipid Panel\n")
APRIL = ("04/02/2025,Hemoglobin,13.8,g/dL,13.5 - 17.5,,CBC\n"
         "04/02/2025,Ferritin,85,ng/mL,30-400,,\n")


def client():
    return TestClient(main.app, base_url="http://testserver")


def count(pid):
    db = dbm.connect()
    try:
        return {t: db.execute(f"SELECT COUNT(*) FROM {t} WHERE profile_id=?", (pid,)).fetchone()[0]
                for t in ("imports", "events", "series", "results", "documents")}
    finally:
        db.close()


def assembled(pid):
    db = dbm.connect()
    try:
        return records.assemble(db, pid)
    finally:
        db.close()


def rowless(data):
    """The lab results with no row of their own (#868): [(series, time, import)] for each point no labitem of its own
    import lists on its day, in a lab day (crossmatch._row_of, as the drawer finds a row's result, drawer.js rowPoint).
    data: the records as assembled, every record's details with them."""
    from server import crossmatch
    listed = {str(i) for e in data["events"] if e["k"] == "lab" for i in (data["details"].get(str(e["id"])) or {}).get("ids") or []}
    rows = crossmatch._rows({"events": [e for e in data["events"] if str(e["id"]) in listed], "details": data["details"]})
    return [(s["name"], p[0], s.get("imp")) for s in data["labs"] if not s.get("watch") and s.get("panel") != "Vitals"
            for p in s["pts"] if not crossmatch._row_of(rows, s, p)]


def folder_files(path):
    out = {}
    for d, _, names in os.walk(path):
        for n in names:
            p = os.path.join(d, n)
            with open(p, "rb") as f:
                out[os.path.relpath(p, path).replace(os.sep, "/")] = f.read()
    return out


class Preview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = client().__enter__()
        db = dbm.connect()
        cls.uids = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        cls.pid = prof.create_profile(db, "Robin (fictional)", owner_id=cls.uids["robin"])
        for name, role in (("kim", "viewer"), ("ash", "editor")):
            db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, ?)", (cls.uids[name], cls.pid, role))
        db.close()
        cls.robin, cls.kim, cls.ash, cls.lee = (cls.login(n) for n in ("robin", "kim", "ash", "lee"))

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
            return prof.create_profile(db, f"{name} (fictional)", owner_id=self.uids["robin"])
        finally:
            db.close()

    def upload(self, pid, files):
        db = dbm.connect()
        try:
            up = uploads.create(db, pid, None)
            for rel, data in files.items():
                uploads.add_file(db, pid, up, rel, io.BytesIO(data if isinstance(data, bytes) else data.encode()))
            uploads.finish(db, pid, up)
            return up
        finally:
            db.close()

    def previewed(self, pid, files):
        """Upload files and wait for the job's preview: (job, upload id)."""
        up = self.upload(pid, files)
        r = self.robin.post(f"/api/profiles/{pid}/uploads/{up}/import", json={}, headers=H)
        self.assertEqual(r.status_code, 202, r.text)
        return self.until(pid, r.json()["id"], ("preview", "failed")), up

    def until(self, pid, jid, states, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            j = self.robin.get(f"/api/profiles/{pid}/jobs/{jid}").json()
            if j["state"] in states:
                return j
            time.sleep(0.05)
        self.fail(f"job {jid} never reached {states}: {j}")

    def save(self, pid, j, keep=None, c=None):
        r = (c or self.robin).post(f"/api/profiles/{pid}/jobs/{j['id']}/confirm", json={"keep": keep or []}, headers=H)
        self.assertEqual(r.status_code, 202, r.text)
        return self.until(pid, j["id"], ("done", "failed", "discarded"))

    # ---------------------------------------------------------------- #22's done-when

    def test_same_export_again_is_all_duplicates(self):
        for name, rel in CASES.items():
            with self.subTest(name):
                pid = self.profile(name)
                files = folder_files(os.path.join(FIX, rel))
                first, _ = self.previewed(pid, files)
                self.assertEqual((first["state"], first["importer"]), ("preview", name), first)
                self.assertEqual(count(pid)["events"], 0)   # nothing saved by a preview
                self.assertEqual(first["duplicates"]["events"] + first["duplicates"]["results"], 0)
                self.assertTrue(first["new"])
                self.assertGreater(first["counts"]["events"], 0)
                self.assertRegex(first["range"]["from"], r"^\d{4}-\d{2}-\d{2}$")
                done = self.save(pid, first)
                self.assertEqual(done["state"], "done", done)
                after = count(pid)
                data = self.robin.get(f"/api/profiles/{pid}/data").json()

                again, _ = self.previewed(pid, files)
                self.assertEqual(again["state"], "preview", again)
                self.assertFalse(again["new"], again)
                self.assertEqual(again["counts"]["events"], 0, again["counts"])
                self.assertEqual(again["counts"]["results"], 0)
                self.assertEqual(again["duplicates"]["events"], first["counts"]["events"])
                self.assertEqual(again["duplicates"]["results"], first["counts"]["results"])
                self.assertEqual(again["range"], first["range"])
                self.assertTrue(again["duplicates"]["items"])
                self.assertTrue(all(i["ref"] is None or i["ref"][:2] in ("e:", "s:") for i in again["duplicates"]["items"]))   # (None: a list's entry, #497)
                done = self.save(pid, again)   # nothing new: cancelled, its upload removed (#547)
                self.assertEqual((done["state"], done["message"]), ("discarded", jobs.NOTHING_NEW), done)
                self.assertIsNone(done["import_id"])
                self.assertEqual(count(pid), after)   # saving added nothing
                self.assertEqual(self.robin.get(f"/api/profiles/{pid}/data").json(), data)
                self.assertFalse(os.path.exists(uploads.folder(pid, again["upload_id"])))

    def test_immunizations_allergies_and_appointments_listed_and_found_again(self):   # #179
        pid = self.profile("portal-everything")
        files = folder_files(os.path.join(FIX, "fhir", "portal-everything"))
        first, _ = self.previewed(pid, files)
        c = first["counts"]
        self.assertEqual((c["kinds"]["immunization"], c["kinds"]["rx"], c["kinds"]["imaging"], c["allergies"], c["upcoming"]), (2, 1, 2, 2, 1), c)
        self.assertNotIn("problems", c)   # only allergies: no other problem
        self.assertEqual(self.save(pid, first)["state"], "done")
        data = self.robin.get(f"/api/profiles/{pid}/data").json()
        self.assertEqual(sorted(e["title"] for e in data["events"] if e["k"] == "immunization"), ["Hepatitis B vaccine", "Influenza vaccine"])
        self.assertEqual(sorted(p["substance"] for p in data["problems"]), ["Peanuts", "Penicillin G"])
        again, _ = self.previewed(pid, files)   # the same export again: nothing new, by source key
        self.assertFalse(again["new"], again)
        self.assertEqual((again["counts"]["events"], again["duplicates"]["kinds"]["immunization"], again["duplicates"]["other"]),
                         (0, 2, 3), again)   # the two allergies and the appointment, by key
        self.assertNotIn("allergies", again["counts"])

    def test_an_overlapping_export_adds_only_whats_new(self):
        pid = self.profile("Overlap")
        j, _ = self.previewed(pid, {"labs.csv": CSV_HEAD + MARCH})
        self.save(pid, j)
        before = count(pid)
        j, _ = self.previewed(pid, {"labs.csv": CSV_HEAD + MARCH + APRIL})
        self.assertTrue(j["new"])
        self.assertEqual(j["duplicates"]["results"], 2)
        self.assertEqual(j["counts"]["results"], 2)
        self.assertEqual(j["counts"]["kinds"].get("lab"), 1)   # April's draw; March's is there already
        self.assertEqual(j["range"], {"from": "2025-03-14", "to": "2025-04-02"})
        self.assertEqual(self.save(pid, j)["state"], "done")
        after = count(pid)
        self.assertEqual(after["results"], before["results"] + 2)
        # the same as importing the whole export once
        fresh = self.profile("Whole")
        self.save(fresh, self.previewed(fresh, {"labs.csv": CSV_HEAD + MARCH + APRIL})[0])
        self.assertEqual(after["results"], count(fresh)["results"])
        self.assertEqual(after["events"], count(fresh)["events"])
        days = [e for e in self.robin.get(f"/api/profiles/{pid}/data").json()["events"] if e["k"] == "lab"]
        self.assertEqual(sorted(e["t"][:10] for e in days), ["2025-03-14", "2025-04-02"])

    def test_a_preview_left_open_is_read_again(self):   # #490: the same file saved meanwhile shows as already here
        pid = self.profile("Left open")
        files = {"labs.csv": CSV_HEAD + MARCH + APRIL}
        left, _ = self.previewed(pid, files)
        self.assertTrue(left["new"])
        self.assertEqual((left["counts"]["results"], left["duplicates"]["results"]), (4, 0))
        self.save(pid, self.previewed(pid, files)[0])   # the same file, uploaded again and saved
        listed = {j["id"]: j for j in self.robin.get(f"/api/profiles/{pid}/jobs", params={"state": "preview"}).json()}
        self.assertFalse(listed[left["id"]]["new"])   # read again for "Waiting for you"
        self.assertFalse(listed[left["id"]]["stale"])
        again = self.robin.get(f"/api/profiles/{pid}/jobs/{left['id']}").json()
        self.assertEqual((again["state"], again["new"], again["counts"]["results"]), ("preview", False, 0), again)
        self.assertEqual((again["duplicates"]["results"], again["duplicates"]["kinds"]), (4, {"lab": 2}))
        self.assertEqual(again["range"], left["range"])
        # each duplicate series with the days of its results: the first and the last (#490)
        days = {i["title"]: (i["t"], i.get("to")) for i in again["duplicates"]["items"] if i["kind"] == "results"}
        self.assertEqual(days["Hemoglobin"], ("2025-03-14", "2025-04-02"))
        self.assertEqual(days["Ferritin"], ("2025-04-02", None))
        done = self.save(pid, again)   # and saving it adds nothing, as confirm finds the duplicates again anyway
        self.assertEqual((done["state"], done["import_id"]), ("discarded", None), done)
        # read again only once the records change, and only the latest few when listed: the others say they're stale
        older = [self.previewed(pid, {"labs.csv": CSV_HEAD + f"05/0{n}/2025,Ferritin,9{n},ng/mL,30-400,,\n"})[0]["id"]
                 for n in range(1, jobs.FRESH + 2)]
        self.save(pid, self.previewed(pid, {"labs.csv": CSV_HEAD + "06/01/2025,Ferritin,70,ng/mL,30-400,,\n"})[0])
        listed = {j["id"]: j["stale"] for j in self.robin.get(f"/api/profiles/{pid}/jobs", params={"state": "preview"}).json()}
        self.assertEqual([listed[i] for i in older], [True] + [False] * jobs.FRESH)
        self.assertNotIn("stale", self.robin.get(f"/api/profiles/{pid}/jobs/{older[0]}").json())
        self.assertFalse(self.robin.get(f"/api/profiles/{pid}/jobs", params={"state": "preview"}).json()[-1]["stale"])

    def test_a_reopened_review_plans_its_lab_names_again(self):   # #497: a series the profile has by now isn't asked about
        pid = self.profile("Lab names again")
        files = {"labs.csv": CSV_HEAD + "03/14/2025,Zeta marker XQ,4.2,U/L,1 - 9,,\n"}
        left, _ = self.previewed(pid, files)
        self.assertEqual([e["name"] for e in left["labs"]], ["Zeta marker XQ"])
        self.save(pid, self.previewed(pid, files)[0])   # saved meanwhile, as a series of its own
        again = self.robin.get(f"/api/profiles/{pid}/jobs/{left['id']}").json()
        self.assertEqual((again["new"], again["labs"], again["lab_targets"]), (False, [], []), again)
        # what confirm reads the choices against is planned again too, what was asked still asked, naming the series
        listed = json.loads(crypto.read_file(jobs.preview_path(pid, left["id"])))["labs"]["listed"]
        self.assertEqual([(e["ref"], e.get("same")) for e in listed], [("s:0", "Zeta marker XQ")])
        done = self.save(pid, again)
        self.assertEqual((done["state"], done["import_id"]), ("discarded", None), done)
        # one with a result still new: still asked about, saying a series of its name is there now
        left, _ = self.previewed(pid, {"labs.csv": CSV_HEAD + "03/14/2025,Zeta marker XR,4.2,U/L,1 - 9,,\n03/15/2025,Zeta marker XR,4.4,U/L,1 - 9,,\n"})
        self.save(pid, self.previewed(pid, {"labs.csv": CSV_HEAD + "03/14/2025,Zeta marker XR,4.2,U/L,1 - 9,,\n"})[0])
        again = self.robin.get(f"/api/profiles/{pid}/jobs/{left['id']}").json()
        self.assertEqual([(e["name"], e.get("same")) for e in again["labs"]], [("Zeta marker XR", "Zeta marker XR")], again["labs"])
        self.assertTrue(again["lab_targets"])

    def test_two_refreshes_of_one_preview_together(self):   # #521: neither fails on the file, both see the new summary
        pid = self.profile("Refreshed together")
        files = {"labs.csv": CSV_HEAD + "03/14/2025,Zeta marker XS,4.2,U/L,1 - 9,,\n"}
        left = [self.previewed(pid, files)[0] for _ in range(5)]
        self.save(pid, self.previewed(pid, files)[0])   # each left open now has its lab names to plan again: a file to write

        def fresh(jid, ready):
            db = dbm.connect()
            try:
                ready.wait()
                jobs.freshen(db, pid, jid)
            finally:
                db.close()
        with self.assertNoLogs("sanguity.jobs", logging.WARNING):   # freshen logs what it can't read again, and returns
            for j in left:
                ready = threading.Barrier(2)
                ts = [threading.Thread(target=fresh, args=(j["id"], ready)) for _ in range(2)]
                for t in ts:
                    t.start()
                for t in ts:
                    t.join()
        for j in left:
            again = self.robin.get(f"/api/profiles/{pid}/jobs/{j['id']}").json()
            self.assertEqual((again["new"], again["labs"]), (False, []), again)
            listed = json.loads(crypto.read_file(jobs.preview_path(pid, j["id"])))["labs"]["listed"]
            self.assertEqual([e.get("same") for e in listed], ["Zeta marker XS"])
        folder = os.path.dirname(jobs.preview_path(pid, left[0]["id"]))
        self.assertEqual([n for n in os.listdir(folder) if n.endswith(".tmp")], [])   # nothing left half-written

        # cancelled while it's read again: its file doesn't come back
        files = {"labs.csv": CSV_HEAD + "03/14/2025,Zeta marker XT,4.2,U/L,1 - 9,,\n"}
        left, _ = self.previewed(pid, files)
        self.save(pid, self.previewed(pid, files)[0])
        write = crypto.write_file

        def cancelled_first(path, data):
            other = dbm.connect()
            try:
                jobs.discard(other, pid, left["id"], {"id": self.uids["robin"], "username": "robin"})
            finally:
                other.close()
            write(path, data)
        db = dbm.connect()
        try:
            with mock.patch.object(jobs.crypto, "write_file", cancelled_first):
                jobs.freshen(db, pid, left["id"])
        finally:
            db.close()
        self.assertFalse(os.path.exists(jobs.preview_path(pid, left["id"])))
        self.assertEqual(self.robin.get(f"/api/profiles/{pid}/jobs/{left['id']}").json()["state"], "discarded")

    def test_apple_health_again_is_all_duplicates(self):   # #497: its activity and workouts, and its lists' entries, too
        pid = self.profile("Apple Health again")
        files = {"apple_health_export/" + k: v for k, v in
                 folder_files(os.path.join(FIX, "apple-health", "health-records", "apple_health_export")).items()}
        first, _ = self.previewed(pid, files)
        self.assertTrue(first["new"])
        self.assertGreater(first["counts"]["watch_days"], 0)
        self.assertGreater(first["counts"]["workouts"], 0)
        self.assertNotIn("watch", first["duplicates"])
        self.assertEqual(self.save(pid, first)["state"], "done")
        again, _ = self.previewed(pid, files)
        self.assertFalse(again["new"], again)
        self.assertEqual((again["counts"]["watch_days"], again["counts"]["workouts"]), (0, 0), again["counts"])
        self.assertEqual(again["duplicates"]["watch"], {"watch_days": first["counts"]["watch_days"], "workouts": first["counts"]["workouts"]})
        # every duplicate counted is listed: the lists' entries (an allergy, a problem) with no box to keep them
        d = again["duplicates"]
        listed = {(i["kind"], i["ref"] is None) for i in d["items"]}
        self.assertTrue({("allergy", True), ("problem", True)} <= listed, d["items"])
        self.assertEqual(sum(1 for i in d["items"] if i["ref"] is None), d["other"])
        done = self.save(pid, again)
        # nothing new, its wearable's days and workouts too: no import at all, cancelled with its upload (#547)
        self.assertEqual((done["state"], done["import_id"], done["message"]), ("discarded", None, jobs.NOTHING_NEW), done)
        # a watch summary against the one the profile has: only what it lacks, or has with another value
        have = {"metrics": [{"key": "steps", "pts": [["2025-05-30", 3200], ["2025-05-31", 10]]}],
                "exercise": [{"start": "2025-05-30T07:00:00Z", "key": "w1"}], "bp_readings": [["2025-05-30T08:00:00Z", 120, 80, "Phone", "App"]]}
        new = {"metrics": [{"key": "steps", "pts": [["2025-05-30", 3200], ["2025-05-31", 11], ["2025-06-01", 5]]},
                           {"key": "sleep", "pts": [["2025-05-30", 7]]}],
               "exercise": [{"start": "2025-05-30T07:00:00+00:00"}, {"start": "2025-05-31T07:00:00Z", "key": "w1"},
                            {"start": "2025-06-01T07:00:00Z", "key": "w2"}],
               "bp_readings": [["2025-05-30T08:00:30Z", 120, 80, "Watch", "Health"], ["2025-05-30T09:00:00Z", 121, 80, "Watch", "Health"]]}
        from server import watch_store
        left = watch_store.unseen(have, new)
        self.assertEqual([(m["key"], m["pts"]) for m in left["metrics"]], [("steps", [["2025-05-31", 11], ["2025-06-01", 5]]), ("sleep", [["2025-05-30", 7]])])
        self.assertEqual([x.get("key") for x in left["exercise"]], ["w2"])   # the same start, or the same key: there
        self.assertEqual([r[0] for r in left["bp_readings"]], ["2025-05-30T09:00:00Z"])   # the same minute and values: there
        self.assertIs(watch_store.unseen(None, new), new)

    def test_review_samples_what_is_new(self):   # #282: a few of each kind, latest first, the duplicates left out
        pid = self.profile("Samples")
        j, _ = self.previewed(pid, {"labs.csv": CSV_HEAD + MARCH})
        self.save(pid, j)
        j, _ = self.previewed(pid, {"labs.csv": CSV_HEAD + MARCH + APRIL})
        self.assertEqual(j["profile_id"], pid)
        s = j["samples"]
        self.assertEqual(sorted((r["t"], r["name"], r["value"], r["unit"]) for r in s["results"]),
                         [("2025-04-02", "Ferritin", "85", "ng/mL"), ("2025-04-02", "Hemoglobin", "13.8", "g/dL")])   # not March's
        self.assertEqual([r["t"] for r in s["events"]["lab"]], ["2025-04-02"])
        self.assertEqual((s["allergies"], s["problems"], s["upcoming"]), ([], [], []))
        # a C-CDA: its kinds, each at most SAMPLES, latest first; a value as reported with its unit in it shown as the number
        pid = self.profile("Samples C-CDA")
        j, _ = self.previewed(pid, folder_files(os.path.join(FIX, CASES["ccda"])))
        s = j["samples"]
        for k, n in j["counts"]["kinds"].items():
            rows = s["events"].get(k, [])
            self.assertEqual(len(rows), min(n, preview.SAMPLES), k)
            self.assertEqual([r["t"] for r in rows], sorted((r["t"] for r in rows), reverse=True), k)
            self.assertTrue(all(r["title"] for r in rows), rows)
        self.assertEqual(len(s["results"]), preview.SAMPLES)
        self.assertTrue(all(preview.PLAIN.fullmatch(r["value"]) for r in s["results"]), s["results"])
        self.assertEqual(len(s["allergies"]), j["counts"]["allergies"])
        self.assertTrue(s["allergies"][0]["title"].startswith("Allergy: "), s["allergies"])
        self.assertEqual(len(s["problems"]), j["counts"]["problems"])

    def test_keep_a_duplicate_anyway(self):
        pid = self.profile("Keep")
        files = folder_files(os.path.join(FIX, CASES["fhir"]))
        self.save(pid, self.previewed(pid, files)[0])
        before = count(pid)
        j, _ = self.previewed(pid, files)
        visit = next(i for i in j["duplicates"]["items"] if i["kind"] == "visit")
        done = self.save(pid, j, keep=[visit["ref"]])
        self.assertEqual(done["state"], "done", done)
        self.assertEqual(count(pid)["events"], before["events"] + 1)
        db = dbm.connect()
        try:
            a = [x for x in activity.for_profile(db, pid, action="import") if x["detail"].get("job") == j["id"]]
            self.assertEqual(a[0]["detail"]["kept"], 1)
        finally:
            db.close()

    def test_a_trimmed_lab_day_still_holds_together(self):
        data = {"events": [{"id": 1, "t": "2025-05-01", "k": "lab", "title": "2 results", "key": "x:labday:2025-05-01"},
                           {"id": 2, "t": "2025-05-01", "k": "labitem", "title": "CBC", "hidden": True, "key": "x:r1"},
                           {"id": 3, "t": "2025-05-01", "k": "labitem", "title": "Iron", "hidden": True, "key": "x:r2"}],
                "details": {"1": {"type": "labday", "ids": [2, 3]}, "2": {"type": "result"}, "3": {"type": "result"}},
                "labs": [], "costs": []}
        dups = {"events": {"1", "2"}, "points": {}, "txt": {}, "series": set(), "meta": {}}
        preview._settle_labdays(data, dups, set())
        self.assertEqual(dups["events"], {"2"})   # the day stays: one of its results is new
        new = preview.trim(data, dups)
        self.assertEqual([e["id"] for e in new["events"]], [1, 3])
        self.assertEqual(new["details"]["1"]["ids"], [3])
        self.assertEqual(reg.check({"data": new, "warnings": []}), [])
        dups = {"events": {"2", "3"}, "points": {}, "txt": {}, "series": set(), "meta": {}}
        preview._settle_labdays(data, dups, set())
        self.assertEqual(dups["events"], {"1", "2", "3"})   # every result there already: the day goes too
        self.assertEqual(preview.keep(data, dups, ["e:3"])["events"], {"2"})   # keeping a result keeps its day

    def test_a_result_here_keeps_what_its_upload_says(self):   # #846, #868
        """A lab result already here (its test, time and value) is a duplicate only where its copy in the upload says
        nothing of its range and flag that a copy here doesn't, and its row in the upload's lab day goes too: the same
        file again is all duplicates; the same FHIR observation sent again with its reference range adds its copy with
        it, and with its own row (#868: its lab day kept with it, there already by key or not); a spreadsheet of it,
        its own lab day, adds its copy for that day's row; a copy with another range than the one here is another
        result's, kept with its row, and left out where the upload gives it none. The review counts each ("copies") by
        what it brings, and the records read the copies as one result, High (crossmatch.copies: the copy with none
        given the other's range)."""
        pid = self.profile("Copies with their own report")
        obs = lambda rng, value=5.2: {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": {
            "resourceType": "Observation", "id": "k1", "status": "final", "category": [{"coding": [{"code": "laboratory"}]}],
            "code": {"coding": [{"system": "http://loinc.org", "code": "2823-3"}], "text": "Potassium"}, "effectiveDateTime": "2025-06-02T08:30:00",
            "valueQuantity": {"value": value, "unit": "mmol/L"}, **({"referenceRange": [rng]} if rng else {})}}]}
        bare = {"potassium.json": json.dumps(obs(None))}
        self.assertEqual(self.save(pid, self.previewed(pid, bare)[0])["state"], "done")
        j, _ = self.previewed(pid, bare)   # the same file again
        self.assertEqual((j["new"], j["duplicates"]["results"], j["duplicates"].get("copies", 0)), (False, 1, 0), j["duplicates"])
        self.save(pid, j)
        ranged = {"potassium.json": json.dumps(obs({"low": {"value": 3.5}, "high": {"value": 5.1}, "text": "3.5-5.1"}))}
        j, _ = self.previewed(pid, ranged)   # sent again with its range: its result's copy kept with it, and its row
        self.assertEqual((j["new"], j["duplicates"]["results"], j["duplicates"].get("copies", 0), j["counts"]["results"]), (True, 0, 1, 1), j["duplicates"])
        self.assertEqual((j["duplicates"]["brings"], j["duplicates"]["kinds"], j["counts"]["kinds"]), ({"range": 1}, {}, {"lab": 1}),
                         "its range said, and its lab day kept with it, there already by key (#868: it was left out, the copy kept)")
        self.assertEqual(j["possible"], [], "said as a copy, not as possibly the same")
        self.assertEqual(self.save(pid, j)["state"], "done")
        j, _ = self.previewed(pid, ranged)   # and again: nothing it says isn't here now
        self.assertEqual((j["new"], j["duplicates"]["results"], j["duplicates"].get("copies", 0)), (False, 1, 0), j["duplicates"])
        self.save(pid, j)
        sheet = {"labs.csv": "Date,Time,Test,Value,Unit\n2025-06-02,08:30,Potassium,5.2,mmol/L\n"}
        j, _ = self.previewed(pid, sheet)   # a spreadsheet of it, no range: kept with its own lab day's row
        self.assertEqual((j["new"], j["duplicates"]["results"], j["duplicates"].get("copies", 0), j["duplicates"]["brings"]),
                         (True, 0, 1, {"row": 1}), j["duplicates"])
        self.assertEqual(self.save(pid, j)["state"], "done")
        other = {"labs.csv": "Date,Time,Test,Value,Unit,Reference range\n2025-06-02,08:30,Potassium,5.2,mmol/L,3.5-5.3\n"}
        db = dbm.connect()
        try:
            data = reg.get("csv").parse({"labs.csv": self.write_tmp(other["labs.csv"])})["data"]
            dups = preview.find(db, pid, "csv", data)
            self.assertEqual((dups["points"], dups["copies"], dups["brings"]), ({0: set()}, {0: {0}}, {0: {0: "range"}}),
                             "another range: another result's copy, kept with its row")
            data["events"], data["details"] = [], {}   # (#868: no lab day of its own, its range alone telling it apart)
            dups = preview.find(db, pid, "csv", data)
            self.assertEqual((dups["points"], dups["copies"]), ({0: {0}}, {0: set()}), "left out: never a result without its row")
            got = records.assemble(db, pid)
        finally:
            db.close()
        pts = [p for s in got["labs"] if s["name"] == "Potassium, Blood" for p in s["pts"]]
        self.assertEqual([(p[2], p[3], p[4]) for p in pts], [(3.5, 5.1, "H")] * 3, "each copy the one result's range, High")
        rows = [got["details"][str(e["id"])]["rows"][0][4] for e in got["events"] if e["k"] == "labitem"]
        self.assertEqual(len(rows), 3, "the FHIR files' lab days (the second's with its copy, #868) and the spreadsheet's")
        self.assertEqual(rowless(got), [], "every result with its row in its own import's lab day")

    def test_a_copy_only_with_its_own_row(self):   # #868
        """One rule for a result already here, an upload's and Type them in's alike (preview.find): kept as a copy only
        with its own row, its lab day with it, and said by what it brings. Potassium 5.3 typed with no range, then again
        with 3.5–5.1: the second's labitem (keyed by its test, time and value, so by key a duplicate) was left out while
        its result was kept, so the check said "won't be saved again", the save "nothing new" with no Undo, and every lab
        day of that date counted it. Now its row says it's added again with its range, the save counts it with an
        import to undo, and each typed import's lab day holds its own result; the same again is a duplicate."""
        pid, c = self.profile("Typed again with its range"), self.robin
        row = {"date": "2025-07-01", "time": "08:00", "test": "Potassium", "value": "5.3", "unit": "mmol/L"}
        typed = lambda r, save=False: c.post(f"/api/profiles/{pid}/manual", json={"rows": [r], "save": save}, headers=H)
        first = typed(row, True)
        self.assertEqual(first.status_code, 200, first.text)
        ranged = {**row, "range": "3.5-5.1"}
        j = typed(ranged).json()
        self.assertEqual([(x["duplicate"], x["copy"]) for x in j["rows"]], [(False, "range")], "already here, kept with its range")
        self.assertEqual(j["duplicates"], 0)
        r = typed(ranged, True)
        self.assertEqual(r.status_code, 200, r.text)
        saved = r.json()
        self.assertEqual((saved["counts"]["typed"], saved["skipped"], saved["import_id"] is not None, saved["counts"]["results"]),
                         ({"labs": 1}, 0, True, 1), "counted as saved, with an import to undo")
        j = typed(ranged).json()
        self.assertEqual([(x["duplicate"], x["copy"]) for x in j["rows"]], [(True, None)], "the same again: a duplicate")
        got = assembled(pid)
        days = [e for e in got["events"] if e["k"] == "lab"]
        self.assertEqual(sorted(len(got["details"][str(e["id"])]["ids"]) for e in days), [1, 1], "each typed import's lab day, its own row")
        self.assertEqual(rowless(got), [], "no result without its row in its own import's lab day")
        pts = [p for s in got["labs"] if s["name"] == "Potassium, Blood" for p in s["pts"]]
        self.assertEqual([(p[2], p[3], p[4]) for p in pts], [(3.5, 5.1, "H")] * 2, "read as one result, High (#825)")
        r = c.post(f"/api/profiles/{pid}/imports/{saved['import_id']}/undo", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        got = assembled(pid)
        self.assertEqual([(p[2], p[3], p[4]) for s in got["labs"] for p in s["pts"]], [(None, None, "")], "undone: as typed first")
        # an upload's alike: a C-CDA-like FHIR file with its range and a flag, then a spreadsheet of the same, one with a
        # flag of its own ("A"), one with nothing more: each copy said by what it brings, each with its lab day's row
        pid = self.profile("Uploaded again")
        obs = lambda oid, code, name, v, unit, rng: {"resource": {"resourceType": "Observation", "id": oid, "status": "final",
            "category": [{"coding": [{"code": "laboratory"}]}], "code": {"coding": [{"system": "http://loinc.org", "code": code}], "text": name},
            "effectiveDateTime": "2025-07-02T08:30:00", "valueQuantity": {"value": v, "unit": unit}, **({"referenceRange": [rng]} if rng else {})}}
        bundle = {"resourceType": "Bundle", "type": "collection", "entry": [obs("na", "2951-2", "Sodium", 150, "mmol/L", {"low": {"value": 135}, "high": {"value": 145}}),
                                                                             obs("alt", "1742-6", "ALT", 60, "U/L", None)]}
        self.assertEqual(self.save(pid, self.previewed(pid, {"labs.json": json.dumps(bundle)})[0])["state"], "done")
        sheet = ("Date,Time,Test,Value,Unit,Reference range,Flag\n2025-07-02,08:30,Sodium,150,mmol/L,,\n"
                 "2025-07-02,08:30,ALT,60,U/L,,A\n")
        j, _ = self.previewed(pid, {"labs.csv": sheet})
        self.assertEqual((j["duplicates"]["copies"], j["duplicates"]["brings"], j["duplicates"]["results"]), (2, {"flag": 1, "row": 1}, 0), j["duplicates"])
        self.assertEqual(self.save(pid, j)["state"], "done")
        self.assertEqual(rowless(assembled(pid)), [], "each copy with its row")

    def write_tmp(self, text):
        path = os.path.join(TMP, f"tmp-{time.time_ns()}.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    # ---------------------------------------------------------------- cancelling, roles, encryption

    def test_cancel_leaves_nothing(self):
        pid = self.profile("Cancel")
        j, up = self.previewed(pid, {"labs.csv": CSV_HEAD + MARCH})
        path = jobs.preview_path(pid, j["id"])
        self.assertTrue(os.path.isfile(path))
        r = self.robin.post(f"/api/profiles/{pid}/jobs/{j['id']}/discard", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.robin.get(f"/api/profiles/{pid}/jobs/{j['id']}").json()["state"], "discarded")
        self.assertFalse(os.path.exists(path))
        self.assertEqual(count(pid), {"imports": 0, "events": 0, "series": 0, "results": 0, "documents": 0})
        db = dbm.connect()
        try:
            self.assertEqual(uploads.info(db, pid, up)["state"], "discarded")
            self.assertFalse(os.path.exists(uploads.folder(pid, up)))
            self.assertEqual(len(activity.for_profile(db, pid, action="discard")), 1)
        finally:
            db.close()
        self.assertEqual(self.robin.post(f"/api/profiles/{pid}/jobs/{j['id']}/confirm", headers=H).status_code, 409)
        self.assertEqual(self.robin.post(f"/api/profiles/{pid}/jobs/{j['id']}/discard", headers=H).status_code, 409)

    def test_roles(self):
        j, _ = self.previewed(self.pid, {"labs.csv": CSV_HEAD + APRIL})
        for action in ("confirm", "discard"):
            url = f"/api/profiles/{self.pid}/jobs/{j['id']}/{action}"
            self.assertEqual(self.kim.post(url, headers=H).status_code, 403)    # a viewer can't
            self.assertEqual(self.lee.post(url, headers=H).status_code, 404)    # a stranger isn't told it exists
            self.assertEqual(self.robin.post(url).status_code, 403)             # the CSRF header
            self.assertEqual(client().post(url, headers=H).status_code, 401)
        self.assertEqual(self.robin.get(f"/api/profiles/{self.pid}/jobs/{j['id']}").json()["state"], "preview")
        self.assertEqual(self.robin.post(f"/api/profiles/{self.pid}/jobs/99999/confirm", headers=H).status_code, 404)
        done = self.save(self.pid, j, c=self.ash)   # an editor can
        self.assertEqual(done["state"], "done")
        db = dbm.connect()
        try:
            a = [x for x in activity.for_profile(db, self.pid, action="import") if x["detail"].get("job") == j["id"]]
            self.assertEqual(a[0]["actor"], "ash")
        finally:
            db.close()

    def test_preview_is_encrypted_on_disk(self):
        pid = self.profile("Sealed")
        j, _ = self.previewed(pid, {"labs.csv": CSV_HEAD + MARCH})
        with open(jobs.preview_path(pid, j["id"]), "rb") as f:
            raw = f.read()
        self.assertTrue(crypto.encrypted(raw))
        self.assertNotIn(b"Hemoglobin", raw)
        self.assertIn("Hemoglobin", crypto.read_file(jobs.preview_path(pid, j["id"])).decode())
        db = dbm.connect()
        try:
            summary = db.execute("SELECT summary FROM jobs WHERE id=?", (j["id"],)).fetchone()[0]
            self.assertTrue(summary.startswith(crypto.FIELD))
        finally:
            db.close()

    def test_key_hashes_are_backfilled_and_never_plain(self):
        pid = self.profile("Backfill")
        self.save(pid, self.previewed(pid, folder_files(os.path.join(FIX, CASES["ccda"])))[0])
        db = dbm.connect()
        try:
            rows = lambda t: dict(db.execute(f"SELECT id, key_hash FROM {t} WHERE profile_id=?", (pid,)).fetchall())
            before = {t: rows(t) for t in ("events", "series")}
            self.assertTrue(all(len(h) == 32 for h in before["events"].values()), before)
            keys = [json.loads(crypto.dec(r[0]))["key"] for r in db.execute("SELECT body FROM events WHERE profile_id=?", (pid,))]
            self.assertFalse(set(keys) & set(before["events"].values()))   # a hash, not the key
            for t in ("events", "series"):
                db.execute(f"UPDATE {t} SET key_hash=NULL WHERE profile_id=?", (pid,))
            n = records.backfill_key_hashes(db)
            self.assertGreaterEqual(n, len(before["events"]) + len(before["series"]))
            self.assertEqual({t: rows(t) for t in ("events", "series")}, before)
            self.assertEqual(records.backfill_key_hashes(db), 0)   # done once
            dump = "\n".join(str(tuple(r)) for t in ("events", "series", "jobs") for r in db.execute(f"SELECT * FROM {t}"))
            self.assertNotIn(keys[0], dump)
        finally:
            db.close()

    def test_same_upload_while_previewed_returns_the_job(self):
        pid = self.profile("Again")
        j, up = self.previewed(pid, {"labs.csv": CSV_HEAD + MARCH})
        r = self.robin.post(f"/api/profiles/{pid}/uploads/{up}/import", json={}, headers=H)
        self.assertEqual(r.json()["id"], j["id"])


class Keys(unittest.TestCase):
    """#89: every importer's output has unique, stable keys, whatever order the files come in."""

    def test_unique_and_stable(self):
        seen = 0
        for name in sorted(os.listdir(FIX)):
            for case in sorted(os.listdir(os.path.join(FIX, name))):
                root = os.path.join(FIX, name, case)
                if not os.path.isdir(root) or case.startswith(("_", ".")):   # __pycache__ beside a fixture script
                    continue
                with self.subTest(f"{name}/{case}"):
                    files = reg.files_in(root)
                    a = reg.run(files)["data"]
                    b = reg.run(dict(reversed(list(files.items()))))["data"]
                    for part in ("events", "labs"):
                        ka = [x.get("key") for x in a[part]]
                        kb = [x.get("key") for x in b[part]]
                        self.assertEqual(sorted(map(str, ka)), sorted(map(str, kb)))
                        present = [k for k in ka if k]
                        self.assertEqual(len(present), len(set(present)))
                        if name != "sanguity":   # it passes the original importer's keys through, or none
                            self.assertNotIn(None, ka, f"{part} without a key")
                            if part == "events":   # an Apple Health export's Health Records keep the FHIR importer's
                                # keys, so the same records uploaded on their own are duplicates (#165)
                                ok = (name + ":", "fhir:") if name == "apple-health" else name + ":"
                                self.assertTrue(all(k.startswith(ok) for k in ka), ka)
                    seen += 1
        self.assertGreater(seen, 5)


if __name__ == "__main__":
    try:
        ok = unittest.main(exit=False, verbosity=2).result.wasSuccessful()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(not ok)
