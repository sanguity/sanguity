#!/usr/bin/env python3
"""Previewing an import before it's saved (#22) and the stable source keys it matches on (#89): re-uploading the
same export shows everything as duplicates and saving it adds nothing (C-CDA, FHIR, CSV and a Sanguity export); an
overlapping export adds only what's new; a duplicate can be kept anyway; a trimmed lab day still holds together;
cancelling leaves no rows, no preview file and no upload; roles and CSRF; key hashes backfilled at start-up; the
preview encrypted on disk; and every importer's keys unique and stable whatever order the files come in.

Fictional data only.

  python3 tests/test_preview.py
"""
import io, json, os, re, shutil, sys, tempfile, threading, time, unittest
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

from server import accounts, activity, crossmatch, crypto, db as dbm, importers as reg, jobs, main, preview, profiles as prof, ranges, records, uploads   # noqa: E402
from server.importers import common   # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "tests"))
import ranges_matrix as M   # noqa: E402  (#752's matrix: RESENT, #869)

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
    """What's wrong with the records' lab days (crossmatch.lab_faults, the one check of what every lab day keeps to,
    #878): a lab result, a text result too, with no row of its own in a lab day (#868), or its row in two; a lab day
    listing an event that isn't there, titled or listing tests for other than what it holds (#889), or counting other
    results in its badge than its drawer shows. [] where none. data: the records as assembled, every record's details
    with them."""
    return crossmatch.lab_faults(data)


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
        self.assertTrue(all(not re.search(r"[A-Za-z\[]", r["value"]) for r in s["results"]), s["results"])
        # a value as reported that's only its number, as the one reader of a number reads it (#874: a reader of its
        # own, PLAIN, read "1.2E-3" as more than one), else its number
        self.assertEqual([preview.samples({"labs": [{"name": "K", "unit": "mmol/L", "pts": [["2025-01-0%d" % (n + 1), x, None, None, "", v]]}]})["results"][0]["value"]
                          for n, (x, v) in enumerate([(4.5, "4,5"), (5, "=<5"), (1250, "1,250"), (0.0012, "1.2E-3"), (82, "82 mm[Hg]"), (4.5, "")])],
                         ["4,5", "=<5", "1,250", "1.2E-3", "82", "4.5"])
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

    def test_one_ticked_adds_only_it(self):   # #887
        """Ticking one duplicate to add anyway adds only that result (preview.keep), with its own row, in a lab day
        holding only it. A C-CDA document of four results at one time uploaded again, ALT ticked: _settle kept its
        report's labitem for ALT's row, every row of it, and _with_rows took each other row's result for a copy kept for
        its row, so the review's button said "Add 1 lab result" and the save added 4, a second "4 lab results" for the
        same draw. Ticking the lab day adds each of its results, as before."""
        t = "2025-10-07T07:45:00"
        rows = [("ALT", "U/L", t, "45", "10-40"), ("C-Reactive Protein", "mg/L", t, "5", "<=5"), ("Potassium", "mmol/L", t, "5.1", "3.5-5.0"),
                ("Sodium", "mmol/L", t, "140", "135-145")]
        doc, pid = {"portal.xml": M.ccda(rows)}, self.profile("One ticked")
        self.assertEqual(self.save(pid, self.previewed(pid, doc)[0])["state"], "done")
        n = count(pid)

        def days():   # the lab days at that time: (title, each of its rows' test and value), as saved
            got = assembled(pid)
            self.assertEqual(rowless(got), [], "every result with its row in its own import's lab day")
            return sorted((e["title"], [r[:2] for i in got["details"][str(e["id"])]["ids"] for r in got["details"][str(i)]["rows"]])
                          for e in got["events"] if e["k"] == "lab" and e["t"][:16] == t[:16])
        every = ("4 lab results", [[x[0], x[3]] for x in rows])
        j, _ = self.previewed(pid, doc)
        refs = {i["title"]: i["ref"] for i in j["duplicates"]["items"]}
        self.assertEqual((j["new"], j["duplicates"]["results"]), (False, len(rows)), j["duplicates"])
        done = self.save(pid, j, keep=[refs["ALT"]])
        self.assertEqual((done["state"], done["counts"]["results"], done["counts"]["kinds"]), ("done", 1, {"lab": 1}), done)
        self.assertEqual(count(pid)["results"], n["results"] + 1, "ALT alone added")
        self.assertEqual(days(), [("1 lab result", [["ALT", "45"]]), every], "its own row, in a lab day holding only it")
        j, _ = self.previewed(pid, doc)   # the lab day ticked: each of its results
        refs = {i["kind"]: i["ref"] for i in j["duplicates"]["items"]}
        done = self.save(pid, j, keep=[refs["lab"]])
        self.assertEqual((done["state"], done["counts"]["results"]), ("done", len(rows)), done)
        self.assertEqual(days(), [("1 lab result", [["ALT", "45"]]), every, every])

    def test_a_text_result_ticked_adds_it_with_its_row(self):   # #901
        """A text result ticked to add anyway is kept with its row, in a lab day holding only it, as a number is
        (#887): a C-CDA document of a urine colour and two numbers uploaded again, the urine colour ticked under
        "Already here". keep() made its text results new but kept only its points' rows, so it was saved with no row and
        no lab day, counted in every lab day of its date."""
        t = "2025-10-08T07:45:00"
        rows = [("Color of Urine", "", t, "Yellow", ""), ("Potassium", "mmol/L", t, "5.1", "3.5-5.0"), ("Sodium", "mmol/L", t, "140", "135-145")]
        doc, pid = {"portal.xml": M.ccda(rows)}, self.profile("A text result ticked")
        self.assertEqual(self.save(pid, self.previewed(pid, doc)[0])["state"], "done")
        n = count(pid)
        j, _ = self.previewed(pid, doc)
        refs = {i["title"]: i["ref"] for i in j["duplicates"]["items"]}
        self.assertFalse(j["new"], j["duplicates"])
        done = self.save(pid, j, keep=[refs["Color of Urine"]])
        self.assertEqual((done["state"], done["counts"]["kinds"]), ("done", {"lab": 1}), done)
        got = assembled(pid)
        self.assertEqual(rowless(got), [], "kept with its row")
        self.assertEqual(count(pid)["results"], n["results"], "no number added")
        self.assertEqual(sorted((e["title"], [r[:2] for i in got["details"][str(e["id"])]["ids"] for r in got["details"][str(i)]["rows"]])
                                for e in got["events"] if e["k"] == "lab"),
                         [("1 lab result", [["Color of Urine", "Yellow"]]), ("3 lab results", [[x[0], x[3]] for x in rows])], "in a lab day holding only it")
        self.assertEqual([x[1] for s in got["labs"] if s["name"] == "Color of Urine" for x in s.get("txt") or []], ["Yellow", "Yellow"])

    def test_a_lab_day_says_what_it_holds_wherever_its_list_changes(self):   # #889
        """A lab day kept with less than it had, or more, is titled for what it holds and lists only its tests, by one
        function (common.lab_day_counted) wherever its list changes: an import's duplicates left out (preview.trim), a
        sync's lab day joined or undone (sync._labday_body), an event hidden (edits.apply), a result said to be the same
        as another (crossmatch._tidy). preview.trim counted its title (#887) but kept the importer's list of tests:
        Potassium typed in, then again with Glucose and Sodium, said "2 lab results · Glucose, Potassium, Sodium"; sync's
        "1 lab result" sat under "CBC with differential, Lab results" for the ALT alone. A title of another form (a
        build's "Lab draw") is kept, its list with it."""
        pid, c = self.profile("Typed, one here already"), self.robin
        row = lambda test, v, u, r: {"date": "2025-11-13", "time": "07:45", "test": test, "value": v, "unit": u, "range": r}   # noqa: E731
        k = row("Potassium", "5.1", "mmol/L", "3.5-5.1")
        typed = lambda rows: c.post(f"/api/profiles/{pid}/manual", json={"rows": rows, "save": True}, headers=H)   # noqa: E731
        self.assertEqual(typed([k]).status_code, 200)
        r = typed([k, row("Glucose", "99", "mg/dL", "70-99"), row("Sodium", "140", "mmol/L", "135-145")])
        self.assertEqual((r.status_code, r.json().get("skipped")), (200, 1), r.text)
        days = lambda pid: sorted((e["title"], e.get("sub"), e.get("abn")) for e in assembled(pid)["events"] if e["k"] == "lab")   # noqa: E731
        self.assertEqual(days(pid), [("1 lab result", "Potassium", None), ("2 lab results", "Glucose, Sodium", None)], "typed in")
        sodium = next(e["id"] for e in assembled(pid)["events"] if e["k"] == "labitem" and e["title"] == "Sodium")
        r = c.post(f"/api/profiles/{pid}/edits", json={"event": sodium, "action": "hide"}, headers=H)
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(days(pid), [("1 lab result", "Glucose", None), ("1 lab result", "Potassium", None)], "an event hidden")
        up = self.profile("Uploaded, one here already")
        sheet = "Date,Time,Test,Value,Unit,Reference range,Panel\n2025-11-13,07:45,Potassium,5.1,mmol/L,3.5-5.1,BMP\n"
        self.save(up, self.previewed(up, {"a.csv": sheet})[0])
        self.save(up, self.previewed(up, {"b.csv": sheet + "2025-11-13,07:45,Glucose,120,mg/dL,70-99,Glucose\n"})[0])
        self.assertEqual(days(up), [("1 lab result", "BMP", None), ("1 lab result", "Glucose", 1)], "an upload")
        # a sync's lab day (sync._labday_body: joined by a pull, handed over or put back on Undo) said for the labitems
        # it lists, and a lab day whose row is folded into another source's (crossmatch._tidy)
        from server import sync
        t = "2025-11-13T07:45:00"
        cbc = [["Hemoglobin", "12.1", "g/dL", "13.5-17.5", "L"], ["Platelet Count", "455", "10*3/uL", "150-400", "H"]]
        data = {"events": [{"id": 1, "t": t, "k": "lab", "title": "3 lab results", "sub": "CBC with differential, Lab results", "abn": 3, "key": "x:labday:1"},
                           {"id": 2, "t": t, "k": "labitem", "title": "CBC with differential", "hidden": 1, "abn": 2, "key": "x:r1"},
                           {"id": 3, "t": t, "k": "labitem", "title": "Lab results", "hidden": 1, "abn": 1, "key": "x:r2"}],
                "details": {"1": {"type": "labday", "ids": [2, 3]},
                            "2": {"type": "result", "order": "CBC with differential", "rows": cbc},
                            "3": {"type": "result", "order": "Lab results", "rows": [["ALT", "60", "U/L", "7-56", "H"]]}},
                "labs": [], "costs": []}
        sp, db = self.profile("A sync's lab day"), dbm.connect()
        try:
            with dbm.transaction(db):
                records.store(db, sp, data, "sync", fmt="fhir")
            ev = {(b := json.loads(crypto.dec(r["body"])))["title"]: (r["id"], b) for r in db.execute("SELECT id, body FROM events WHERE profile_id=?", (sp,))}
            said = sync._labday_body(db, ev["3 lab results"][1], [ev["Lab results"][0]])
        finally:
            db.close()
        self.assertEqual((said["title"], said["sub"], said["abn"]), ("1 lab result", "Lab results", 1), "a sync's")
        folded = json.loads(json.dumps(data))
        folded["details"]["2"]["rows"][1] = None   # (crossmatch._fold_row: the Platelets' row folded into another's)
        crossmatch._tidy(folded, {2})
        self.assertEqual([(e["title"], e["sub"], e.get("abn")) for e in folded["events"] if e["k"] == "lab"],
                         [("2 lab results", "CBC with differential, Lab results", 2)], "a row folded into another source's")
        built = {**data, "events": [{**data["events"][0], "title": "Lab draw", "sub": "3 results"}] + data["events"][1:]}
        self.assertEqual({k: v for k, v in common.lab_day_counted(built["events"][0], [(data["events"][2], data["details"]["3"])]).items() if k != "abn"},
                         {k: v for k, v in built["events"][0].items() if k != "abn"}, "a title of another form kept, its list with it")

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
        file again is all duplicates; the same FHIR observation sent again with its reference range is that result,
        updated in place with it (#869: it added a copy, #868 its row too); a spreadsheet of it, its own lab day, adds its
        copy for that day's row; a copy with another range than the one here is another result's, kept with its row,
        and left out where the upload gives it none (#868). The review counts each ("copies") by what it brings, and the
        records read the copies as one result, High (crossmatch.copies: the copy with none given the other's range)."""
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
        j, _ = self.previewed(pid, ranged)   # sent again with its range: the same result, updated in place with it (#869)
        self.assertEqual((j["new"], j["duplicates"]["results"], j["duplicates"].get("copies", 0), j["duplicates"]["updates"], j["counts"]["results"]),
                         (True, 0, 0, 1, 0), j["duplicates"])
        self.assertEqual(j["possible"], [], "said as updated, not as possibly the same")
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
        self.assertEqual([(p[2], p[3], p[4]) for p in pts], [(3.5, 5.1, "H")] * 2, "each copy the one result's range, High")
        rows = [got["details"][str(e["id"])]["rows"][0] for e in got["events"] if e["k"] == "labitem"]
        self.assertEqual([r[3] for r in rows], ["3.5-5.1", ""], "the FHIR file's lab day (its row as sent again, #869) and the spreadsheet's")
        self.assertEqual(rowless(got), [], "every result with its row in its own import's lab day")

    def test_a_result_sent_again_is_updated_in_place(self):   # #869
        """The same record sent again by its source, amended (tests/ranges_matrix.py's RESENT: a FHIR file's report and
        results on their own, then again with a range corrected and a Low taken back, a range given, a flag changed), is
        that result: updated in place, range and flag, its drawer row with it, never a copy. The second upload added a
        copy of each (one result shown twice, or read as one with the old one's flag), its lab day's rows reading the old
        point's flag beside the new range. Its review says so ("updates"), its save counts them ("updated") in an import
        to undo, each row's flag is its result's, the same file again is all duplicates, and Undo puts back what was."""
        pid = self.profile("Sent again")
        first, second = (dict([M.resent(k)]) for k in (0, 1))
        self.assertEqual(self.save(pid, self.previewed(pid, first)[0])["state"], "done")
        before, n = assembled(pid), count(pid)
        j, _ = self.previewed(pid, second)
        self.assertEqual({k: j["duplicates"].get(k, 0) for k in M.RESENT_HERE}, M.RESENT_HERE, j["duplicates"])
        self.assertEqual((j["new"], j["counts"]["results"], j["duplicates"]["kinds"], j["possible"]), (True, 0, {"lab": 1}, []), "nothing added: updated")
        done = self.save(pid, j)
        self.assertEqual((done["state"], done["counts"].get("updated")), ("done", M.RESENT_HERE["updates"]), done)
        self.assertEqual({k: v for k, v in count(pid).items() if k != "imports"}, {k: v for k, v in n.items() if k != "imports"}, "no rows added")
        got = assembled(pid)
        pts = {s["name"]: s["pts"] for s in got["labs"]}
        self.assertEqual({k: [[p[4], p[2], p[3], *filter(None, p[7:8])] for p in v] for k, v in pts.items()},
                         {n: v for (n, _), v in M.RESENT_SEEN.items()}, "one result each, as sent now (a bound's edge too)")
        drawer, printed = crossmatch._rows(got), [n for n, _ in M.RESENT_DAYS[(1, M.RESENT_BACK)]]
        for s in got["labs"]:
            for p in s["pts"]:
                eid, r = crossmatch._row_of(drawer, s, p)
                row = got["details"][str(eid)]["rows"][r]
                self.assertEqual((ranges.read_flag(row[4], p[1], p[2], p[3], p[5]), row[3]), (p[4], M.RESENT_RANGES[printed.index(row[0])]),
                                 f"{s['name']}: its row and its result agree")
        j, _ = self.previewed(pid, second)   # the same again: nothing it says isn't here now
        self.assertEqual((j["new"], j["duplicates"]["results"], j["duplicates"].get("updates")), (False, M.RESENT_HERE["updates"], 0), j["duplicates"])
        r = self.robin.post(f"/api/profiles/{pid}/jobs/{j['id']}/discard", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        r = self.robin.post(f"/api/profiles/{pid}/imports/{done['import_id']}/undo", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        after = assembled(pid)
        self.assertEqual(({s["name"]: s["pts"] for s in after["labs"]}, after["details"]), ({s["name"]: s["pts"] for s in before["labs"]}, before["details"]),
                         "undone: its results and rows as they were")

    def test_a_record_its_source_changed_is_updated_whatever_its_kind(self):   # #920
        """A portal's FHIR export uploaded, then its next one after the doctor stopped a medication and resolved a problem
        (the same MedicationRequest and Condition, changed); an insurer's claims spreadsheet, then the next month's with a
        pending claim processed (the same claim number, you owe $45 now). Each was left out as "already here": the
        medication still current, the problem Active, the Costs row Pending at $0. Now each is updated in place, said in
        the review by kind ("changed", not among the duplicates skipped), counted once saved ("changed") in an import to
        undo; the same file again is all duplicates, and Undo puts back what each said."""
        def bundle(*resources):
            return json.dumps({"resourceType": "Bundle", "type": "collection",
                               "entry": [{"fullUrl": f"https://portal.example/fhir/{r['resourceType']}/{r['id']}", "resource": r} for r in resources]})
        status = lambda code: {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": code}]}   # noqa: E731
        pat = {"resourceType": "Patient", "id": "p1", "name": [{"family": "Example", "given": ["Pat"]}], "birthDate": "1980-02-02"}
        med = {"resourceType": "MedicationRequest", "id": "mr-1", "status": "active", "intent": "order", "subject": {"reference": "Patient/p1"},
               "medicationCodeableConcept": {"text": "Metoprolol succinate 25 mg tablet"}, "authoredOn": "2026-03-01",
               "dosageInstruction": [{"text": "1 tablet daily"}]}
        cond = {"resourceType": "Condition", "id": "c-1", "subject": {"reference": "Patient/p1"}, "code": {"text": "Atrial fibrillation"},
                "clinicalStatus": status("active"), "onsetDateTime": "2026-02-20", "recordedDate": "2026-02-20"}
        head = "Claim Number,Date of Service,Provider,Description,Amount Billed,Plan Paid,You Owe,Claim Status,Insurer\n"
        claim = lambda paid, owe, st: f"C-1001,2026-09-28,Lakeside Family Medicine (fictional),Office visit,180.00,{paid},{owe},{st},Example Health Plan (fictional)\n"   # noqa: E731
        lab = "C-1000,2026-09-02,Lakeside Family Medicine (fictional),Lab work,90.00,70.00,20.00,Processed,Example Health Plan (fictional)\n"
        cases = {"fhir": ({"portal-2026-03.json": bundle(pat, med, cond)},
                          {"portal-2026-11.json": bundle(pat, {**med, "status": "stopped"},
                                                         {**cond, "clinicalStatus": status("resolved"), "abatementDateTime": "2026-10-15"})},
                          {"med": 1, "problem": 1}),
                 "claims": ({"claims-2026-10.csv": head + claim("0.00", "0.00", "Pending") + lab},
                            {"claims-2026-11.csv": head + claim("120.00", "45.00", "Processed") + lab}, {"claim": 1})}

        def said(d):
            meds = [(m["name"], m["spans"][-1]["open"], m["spans"][-1]["status"]) for m in d.get("medications") or []]
            probs = [(x.get("name"), x.get("status")) for x in d.get("problems") or []]
            return meds, probs, [(c["title"], c["paid"], c["owe"], c["status"]) for c in d.get("costs") or []]

        for name, (first, second, kinds) in cases.items():
            with self.subTest(name):
                pid = self.profile(f"Changed at the source, {name}")
                self.assertEqual(self.save(pid, self.previewed(pid, first)[0])["state"], "done")
                before, n = said(assembled(pid)), count(pid)
                j, _ = self.previewed(pid, second)
                self.assertEqual((j["new"], {k: j["duplicates"]["changed"][k] for k in ("n", "kinds")}), (True, {"n": sum(kinds.values()), "kinds": kinds}), j["duplicates"])
                self.assertFalse([x for x in j["duplicates"]["items"] if x["title"] in ("Metoprolol succinate 25 mg tablet", "Atrial fibrillation", "Office visit")],
                                 "not among the duplicates skipped")
                done = self.save(pid, j)
                self.assertEqual((done["state"], done["counts"].get("changed")), ("done", sum(kinds.values())), done)
                self.assertEqual({k: v for k, v in count(pid).items() if k != "imports"}, {k: v for k, v in n.items() if k != "imports"}, "nothing added")
                after = said(assembled(pid))
                if name == "fhir":
                    self.assertEqual(after[:2], ([("Metoprolol succinate 25 mg tablet", False, "Stopped")], [("Atrial fibrillation", "Resolved")]),
                                     "the medication stopped, the problem resolved")
                else:
                    self.assertEqual(sorted(after[2]), [("Lab work", 70.0, 20.0, "Processed"), ("Office visit", 120.0, 45.0, "Processed")],
                                     "the claim processed, its Costs row with it")
                j2, _ = self.previewed(pid, second)   # the same again: nothing it says isn't here now
                self.assertEqual((j2["new"], j2["duplicates"]["changed"]["n"]), (False, 0), j2["duplicates"])
                self.assertEqual(self.robin.post(f"/api/profiles/{pid}/jobs/{j2['id']}/discard", headers=H).status_code, 200)
                r = self.robin.post(f"/api/profiles/{pid}/imports/{done['import_id']}/undo", headers=H)
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(said(assembled(pid)), before, "undone: each as it was")

    def test_a_medication_its_source_renames_keeps_what_hangs_on_it(self):   # #960
        """A portal's export, then its next one naming the same MedicationRequest another way (Lisinopril, then Prinivil:
        #920 updates it in place). A medication's reminders, doses and the side effects linked to it hang on its key,
        its shown name's leading words, so the rename took the key with it and left them on "lisinopril", a medication
        no longer there: the reminder gone from the calendar feed, the doses from its log. Now what hangs on it goes
        with it in the same save, as a person's rename takes it (#954); undoing the import puts each back. A listing the
        person renamed (Zestril) keeps its name, and what hangs on it stays."""
        from datetime import date
        from server import journal, med_reminders
        P = lambda pid: f"/api/profiles/{pid}"   # noqa: E731

        def bundle(name, text, at):
            med = {"resourceType": "MedicationRequest", "id": "mr-1", "status": "active", "intent": "order", "subject": {"reference": "Patient/p1"},
                   "medicationCodeableConcept": {"text": text}, "authoredOn": "2026-03-01", "dosageInstruction": [{"text": "1 tablet daily"}],
                   "meta": {"lastUpdated": at}}
            other = {**med, "id": "mr-2", "medicationCodeableConcept": {"text": "Metformin 500 mg tablet"}, "meta": {"lastUpdated": "2026-03-01T10:00:00Z"}}
            pat = {"resourceType": "Patient", "id": "p1", "name": [{"family": "Example", "given": ["Pat"]}], "birthDate": "1980-02-02"}
            return {name: json.dumps({"resourceType": "Bundle", "type": "collection",
                                      "entry": [{"fullUrl": f"https://portal.example/fhir/{r['resourceType']}/{r['id']}", "resource": r} for r in (pat, med, other)]})}

        def state(pid):
            d = self.robin.get(P(pid) + "/data").json()
            db = dbm.connect()
            try:
                feed = len(med_reminders.feed_events(db, pid, False, date(2026, 11, 20))[0])
                side = [e["med"] for e in journal.listing(db, pid) if e.get("med")]
            finally:
                db.close()
            return (sorted(m["key"] for m in d["medications"]), [r["med"] for r in d.get("reminders") or []],
                    [x["med"] for x in d.get("doses") or []], feed, side)

        pid = self.profile("A medication renamed at its source")
        self.assertEqual(self.save(pid, self.previewed(pid, bundle("portal-2026-03.json", "Lisinopril 10 mg tablet", "2026-03-01T10:00:00Z"))[0])["state"], "done")
        r = self.robin.post(P(pid) + "/medications/reminders", headers=H, json={"med": "lisinopril", "times": ["08:00"],
                            "days": ["mo", "tu", "we", "th", "fr", "sa", "su"], "start": "2026-11-01", "tz": "America/New_York"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(self.robin.post(P(pid) + "/medications/doses", headers=H, json={"med": "lisinopril", "t": "2026-11-02T08:05", "status": "taken"}).status_code, 201)
        r = self.robin.post(P(pid) + "/journal", headers=H, json={"kind": "side_effect", "t": "2026-11-02", "title": "Dry cough", "severity": 3,
                                                                  "med": "Lisinopril 10 mg tablet"})
        self.assertEqual(r.status_code, 201, r.text)
        before = state(pid)
        self.assertEqual(before, (["lisinopril", "metformin"], ["lisinopril"], ["lisinopril"], 1, ["Lisinopril 10 mg tablet"]))
        renamed = bundle("portal-2026-11.json", "Prinivil 10 mg tablet", "2026-11-01T10:00:00Z")
        j, _ = self.previewed(pid, renamed)
        self.assertEqual({k: j["duplicates"]["changed"][k] for k in ("n", "kinds")}, {"n": 1, "kinds": {"med": 1}})
        done = self.save(pid, j)
        self.assertEqual((done["state"], done["counts"].get("changed")), ("done", 1), done)
        self.assertEqual(state(pid), (["metformin", "prinivil"], ["prinivil"], ["prinivil"], 1, ["Prinivil 10 mg tablet"]),
                         "its reminder, dose and side effect with it")
        r = self.robin.post(P(pid) + f"/imports/{done['import_id']}/undo", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(state(pid), before, "undone: each back on Lisinopril")

        # renamed by the person first: its name is theirs, so the source's rename moves nothing
        eid = next(e["id"] for e in self.robin.get(P(pid) + "/data").json()["events"] if e["k"] == "med" and e["title"].startswith("Lisinopril"))
        self.assertEqual(self.robin.post(P(pid) + "/edits", headers=H, json={"event": eid, "action": "rename", "title": "Zestril 10 mg tablet"}).status_code, 201)
        mine = state(pid)
        self.assertEqual(mine[:3], (["metformin", "zestril"], ["zestril"], ["zestril"]))
        self.assertEqual(self.save(pid, self.previewed(pid, renamed)[0])["state"], "done")
        self.assertEqual(state(pid), mine, "the person's name kept, and what hangs on it")

    def test_a_record_is_updated_only_by_its_own_source_and_never_by_an_older_or_smaller_file(self):   # #955
        """#920 updates a record in place when its source sends it again saying something else, and took any difference
        for a change: another portal's export with the same plain FHIR ids (fhir:<type>/<id>) put clinic B's medication
        and problem in place of hospital A's; November's export, then March's, put the medication stopped since and the
        problem resolved since back to Active; a smaller export of the same portal (the visit alone, without the
        Practitioner, Location and Condition it names) wiped the visit's who, where and diagnosis to "". And the review
        said only how many of each kind. Now a record is updated only from its own server (its VER, the FHIR importer's),
        never from an older version (its meta.lastUpdated where given, else the date of the upload it came in: a FHIR
        bundle's timestamp, else the latest date it holds), never emptying a field the upload leaves out, and the review
        names each record it would change and what changes ("changed": "items"), each with a box to leave it out ("x:"
        refs, sent with the duplicates kept)."""
        status = lambda code: {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": code}]}   # noqa: E731
        pat = {"resourceType": "Patient", "id": "1", "name": [{"family": "Example", "given": ["Pat"]}], "birthDate": "1980-02-02"}

        def med(mid, text, st="active", at=None):
            return {"resourceType": "MedicationRequest", "id": mid, "status": st, "intent": "order", "subject": {"reference": "Patient/1"},
                    "medicationCodeableConcept": {"text": text}, "authoredOn": "2026-03-01", "dosageInstruction": [{"text": "1 tablet daily"}],
                    **({"meta": {"lastUpdated": at}} if at else {})}

        def cond(cid, text, st="active", at=None):
            return {"resourceType": "Condition", "id": cid, "subject": {"reference": "Patient/1"}, "code": {"text": text},
                    "clinicalStatus": status(st), "onsetDateTime": "2026-02-20", **({"meta": {"lastUpdated": at}} if at else {})}

        def bundle(name, base, *rs, made=None):
            return {name: json.dumps({"resourceType": "Bundle", "type": "collection", **({"timestamp": made} if made else {}),
                                      "entry": [{"fullUrl": f"{base}/{r['resourceType']}/{r['id']}", "resource": r} for r in rs]})}

        def said(pid):
            d = assembled(pid)
            self.assertEqual(rowless(d), [])
            self.assertFalse([x for x in d["events"] + (d.get("problems") or []) if "ver" in x], "a record's version kept, never served")
            return ([(m["name"], m["spans"][-1]["status"]) for m in d.get("medications") or []], [(p["name"], p["status"]) for p in d.get("problems") or []])

        def again(pid, first, second, changed):   # first saved, second reviewed: its changes ("n", "kinds") -> the review
            self.assertEqual(self.save(pid, self.previewed(pid, first)[0])["state"], "done")
            j, _ = self.previewed(pid, second)
            self.assertEqual({k: j["duplicates"]["changed"][k] for k in ("n", "kinds")}, changed, j["duplicates"])
            return j
        a = "https://hospital-a.example/fhir"
        # another portal's export with the same plain ids: not the same records, hospital A's left as they were
        pid = self.profile("Two portals, the same ids")
        j = again(pid, bundle("hospital-a.json", a, pat, med("1", "Metformin 500 mg tablet"), cond("1", "Type 2 diabetes")),
                  bundle("clinic-b.json", "https://clinic-b.example/fhir", pat, med("1", "Lisinopril 10 mg tablet", "stopped"), cond("1", "Hypertension", "resolved")),
                  {"n": 0, "kinds": {}})
        self.assertEqual(said(pid), ([("Metformin 500 mg tablet", "Active")], [("Type 2 diabetes", "Active")]), "hospital A's as they were")
        # an older version: by lastUpdated, then by the date of the upload (a bundle's timestamp; else the latest it holds)
        nov, mar = ([med("mr-1", "Metoprolol 25 mg tablet", "stopped"), cond("c-1", "Atrial fibrillation", "resolved")],
                    [med("mr-1", "Metoprolol 25 mg tablet"), cond("c-1", "Atrial fibrillation")])
        visit = lambda day: {"resourceType": "Encounter", "id": f"e-{day}", "status": "finished", "class": {"code": "AMB"}, "type": [{"text": "Office visit"}],   # noqa: E731
                             "subject": {"reference": "Patient/1"}, "period": {"start": f"{day}T09:00:00"}}
        stamped = lambda rs, at: [{**r, "meta": {"lastUpdated": at}} for r in rs]   # noqa: E731
        older = {"its lastUpdated": (stamped(nov, "2026-11-02T10:00:00Z"), stamped(mar, "2026-03-05T10:00:00Z"), {}, {}),
                 "its bundle's timestamp": (nov, mar, {"made": "2026-11-20T08:00:00Z"}, {"made": "2026-03-10T08:00:00Z"}),
                 "the latest it holds": (nov + [visit("2026-11-05")], mar + [visit("2026-03-01")], {}, {})}
        for how, (n, m, made_n, made_m) in older.items():
            with self.subTest(older=how):
                pid = self.profile(f"An older file, {how}")
                again(pid, bundle("portal-2026-11.json", a, pat, *n, **made_n), bundle("portal-2026-03.json", a, pat, *m, **made_m), {"n": 0, "kinds": {}})
                self.assertEqual(said(pid), ([("Metoprolol 25 mg tablet", "Stopped")], [("Atrial fibrillation", "Resolved")]), "November's as it was")
                pid = self.profile(f"A newer file, {how}")   # the other way round: updated, as #920 has it
                j = again(pid, bundle("portal-2026-03.json", a, pat, *m, **made_m), bundle("portal-2026-11.json", a, pat, *n, **made_n),
                          {"n": 2, "kinds": {"med": 1, "problem": 1}})
                self.assertEqual(self.save(pid, j)["counts"].get("changed"), 2)
                self.assertEqual(said(pid), ([("Metoprolol 25 mg tablet", "Stopped")], [("Atrial fibrillation", "Resolved")]), "November's")
        # a smaller export of the same portal: the visit alone, without whom and where it names; then with its type changed
        pr = {"resourceType": "Practitioner", "id": "pr-1", "name": [{"family": "Okafor", "given": ["Ada"], "prefix": ["Dr."]}]}
        loc = {"resourceType": "Location", "id": "l-1", "name": "Lakeside Family Medicine (fictional)"}
        enc = {**visit("2026-05-04"), "id": "e-1", "participant": [{"individual": {"reference": "Practitioner/pr-1"}}],
               "location": [{"location": {"reference": "Location/l-1"}}], "diagnosis": [{"condition": {"reference": "Condition/c-9"}}]}

        def seen(pid):
            d = assembled(pid)
            return [(e["title"], e.get("sub"), {k: d["details"][str(e["id"])].get(k) for k in ("who", "where", "dx")}) for e in d["events"] if e["k"] == "visit"]
        pid = self.profile("A smaller file")
        whole = ("Dr. Ada Okafor · Lakeside Family Medicine (fictional)", {"who": "Dr. Ada Okafor", "where": "Lakeside Family Medicine (fictional)", "dx": "Seasonal allergic rhinitis"})
        j = again(pid, bundle("everything.json", a, pat, pr, loc, cond("c-9", "Seasonal allergic rhinitis"), enc), bundle("visits-may.json", a, pat, enc), {"n": 0, "kinds": {}})
        self.assertFalse(j["new"], "nothing it says that isn't here")
        self.assertEqual(seen(pid), [("Office visit", *whole)], "its who, where and diagnosis as they were")
        j, _ = self.previewed(pid, bundle("visits-may.json", a, pat, {**enc, "type": [{"text": "Follow-up visit"}]}))
        self.assertEqual([(x["kind"], x["title"], x["what"]) for x in j["duplicates"]["changed"]["items"]],
                         [("visit", "Follow-up visit", [["title", "Office visit", "Follow-up visit"]])], "named, with what changes")
        self.save(pid, j)
        self.assertEqual(seen(pid), [("Follow-up visit", *whole)], "updated, what the file leaves out kept")
        # the review names each, with what changes, and one ticked is left out
        pid = self.profile("One left out")
        j = again(pid, bundle("portal-2026-03.json", a, pat, *mar), bundle("portal-2026-11.json", a, pat, *nov), {"n": 2, "kinds": {"med": 1, "problem": 1}})
        items = j["duplicates"]["changed"]["items"]
        self.assertEqual([(x["kind"], x["title"], x["what"]) for x in items],
                         [("med", "Metoprolol 25 mg tablet", [["status", "Active", "Stopped"]]), ("problem", "Atrial fibrillation", [["status", "Active", "Resolved"]])])
        self.assertTrue(all(x["ref"].startswith("x:") for x in items), items)
        done = self.save(pid, j, keep=[items[0]["ref"]])
        self.assertEqual((done["state"], done["counts"].get("changed")), ("done", 1), done)
        self.assertEqual(said(pid), ([("Metoprolol 25 mg tablet", "Active")], [("Atrial fibrillation", "Resolved")]), "the medication left as it was")

    def test_another_servers_record_with_the_same_id_is_added_as_its_own(self):   # #967
        """A FHIR resource's key is its plain id, fhir:<type>/<id>, unless one upload mixes servers: clinic B's export
        after hospital A's, with the same ids, was taken for hospital A's records, left out as duplicates (#955 stops it
        replacing them). Now a record whose plain key a record of another server holds here (each says its server: its
        VER, the importer's) is added as its own, its key naming its server (fhir:<server>|<type>/<id>, as an upload
        mixing servers keys it); clinic B's file again is all duplicates, its newer one updates clinic B's records and
        never hospital A's, and hospital A's again finds its own."""
        status = lambda code: {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": code}]}   # noqa: E731
        pat = {"resourceType": "Patient", "id": "1", "name": [{"family": "Example", "given": ["Pat"]}], "birthDate": "1980-02-02"}
        med = lambda text, st="active", at="2026-03-01T10:00:00Z": {   # noqa: E731
            "resourceType": "MedicationRequest", "id": "1", "status": st, "intent": "order", "subject": {"reference": "Patient/1"},
            "medicationCodeableConcept": {"text": text}, "authoredOn": "2026-03-01", "dosageInstruction": [{"text": "1 tablet daily"}],
            "meta": {"lastUpdated": at}}
        cond = lambda text, st="active": {"resourceType": "Condition", "id": "1", "subject": {"reference": "Patient/1"}, "code": {"text": text},   # noqa: E731
                                          "clinicalStatus": status(st), "onsetDateTime": "2026-02-20"}

        def bundle(name, base, *rs):
            return {name: json.dumps({"resourceType": "Bundle", "type": "collection",
                                      "entry": [{"fullUrl": f"{base}/{r['resourceType']}/{r['id']}", "resource": r} for r in rs]})}

        def said(pid):
            d = assembled(pid)
            return (sorted((m["name"], m["spans"][-1]["status"]) for m in d.get("medications") or []),
                    sorted((p["name"], p["status"]) for p in d.get("problems") or []))
        a, b = "https://hospital-a.example/fhir", "https://clinic-b.example/fhir"
        hospital = bundle("hospital-a.json", a, pat, med("Metformin 500 mg tablet"), cond("Type 2 diabetes"))
        clinic = bundle("clinic-b.json", b, pat, med("Lisinopril 10 mg tablet", "stopped"), cond("Hypertension", "resolved"))
        pid = self.profile("Two portals, the same ids, both kept")
        self.assertEqual(self.save(pid, self.previewed(pid, hospital)[0])["state"], "done")
        j, _ = self.previewed(pid, clinic)
        self.assertEqual((j["counts"]["events"], j["duplicates"]["events"], j["duplicates"]["other"], j["duplicates"]["changed"]["n"]), (1, 0, 0, 0),
                         "clinic B's medication and problem are new")
        self.assertEqual(self.save(pid, j)["state"], "done")
        self.assertEqual(said(pid), ([("Lisinopril 10 mg tablet", "Stopped"), ("Metformin 500 mg tablet", "Active")],
                                     [("Hypertension", "Resolved"), ("Type 2 diabetes", "Active")]), "both portals'")
        db = dbm.connect()
        try:
            keys = sorted(e["key"] for e in records.assemble(db, pid, raw=True)["events"] if e.get("k") == "med")
        finally:
            db.close()
        self.assertEqual(keys, sorted([f"fhir:{b}|MedicationRequest/1", "fhir:MedicationRequest/1"]), "clinic B's key names its server")
        for files in (clinic, hospital):   # each portal's file again: its own records, nothing new
            j, _ = self.previewed(pid, files)
            self.assertFalse(j["new"], j["counts"])
            self.assertEqual((j["duplicates"]["events"], j["duplicates"]["other"]), (1, 1))
        # clinic B's newer file: its own medication updated, hospital A's as it was
        j, _ = self.previewed(pid, bundle("clinic-b-2.json", b, pat, med("Lisinopril 10 mg tablet", "active", "2026-09-01T10:00:00Z"),
                                          cond("Hypertension", "resolved")))
        self.assertEqual({k: j["duplicates"]["changed"][k] for k in ("n", "kinds")}, {"n": 1, "kinds": {"med": 1}})
        self.save(pid, j)
        self.assertEqual(said(pid)[0], [("Lisinopril 10 mg tablet", "Active"), ("Metformin 500 mg tablet", "Active")])
        self.assertEqual(rowless(assembled(pid)), [])

    def test_another_clinics_record_with_the_same_id_is_its_own(self):   # #968
        """#955 takes a record's source to be its key's importer prefix for every importer but FHIR (and a claim's
        insurer), so two C-CDA documents from two clinics whose ids collide (one root and extension) were one record: the
        second clinic's medication was put in the first's place, or left out as already here; and two CareSpace exports
        with a document of the same file name, the second clinic's left out. Now each says its source where the file says
        it (a C-CDA's custodian, a CareSpace export's clinic: its record's VER "source"), so the other clinic's is added
        as its own, its key naming it, and a record is updated only from its own clinic."""
        def ccd(custodian, med, status="active"):
            return {f"{custodian.split()[0].lower()}.xml": f"""<?xml version="1.0" encoding="UTF-8"?>
<ClinicalDocument xmlns="urn:hl7-org:v3">
  <typeId root="2.16.840.1.113883.1.3" extension="POCD_HD000040"/><templateId root="2.16.840.1.113883.10.20.22.1.2"/>
  <id root="2.16.840.1.113883.19.5" extension="doc-1"/><code code="34133-9" codeSystem="2.16.840.1.113883.6.1"/>
  <effectiveTime value="20260310"/>
  <recordTarget><patientRole><id extension="1" root="2.16.840.1.113883.19.5.2"/><patient><name><given>Pat</given><family>Example</family></name></patient></patientRole></recordTarget>
  <custodian><assignedCustodian><representedCustodianOrganization><name>{custodian}</name></representedCustodianOrganization></assignedCustodian></custodian>
  <component><structuredBody><component><section><code code="10160-0" codeSystem="2.16.840.1.113883.6.1"/><title>Medications</title>
    <entry><substanceAdministration classCode="SBADM" moodCode="EVN"><id root="2.16.840.1.113883.19.5.9" extension="med-1"/>
      <statusCode code="{status}"/><effectiveTime><low value="20260301"/></effectiveTime>
      <consumable><manufacturedProduct><manufacturedMaterial><code code="1" codeSystem="2.16.840.1.113883.6.88" displayName="{med}"/></manufacturedMaterial></manufacturedProduct></consumable>
    </substanceAdministration></entry>
  </section></component></structuredBody></component>
</ClinicalDocument>"""}

        def meds(pid):
            return sorted((m["name"], m["spans"][-1]["status"]) for m in assembled(pid).get("medications") or [])
        pid = self.profile("Two clinics' C-CDAs, the same ids")
        self.assertEqual(self.save(pid, self.previewed(pid, ccd("Harbor Clinic (fictional)", "Metformin 500 mg tablet"))[0])["state"], "done")
        lakeside = ccd("Lakeside Clinic (fictional)", "Lisinopril 10 mg tablet", "completed")
        j, _ = self.previewed(pid, lakeside)
        self.assertEqual((j["counts"]["events"], j["duplicates"]["events"], j["duplicates"]["changed"]["n"]), (1, 0, 0), "Lakeside's is new")
        self.save(pid, j)
        self.assertEqual(meds(pid), [("Lisinopril 10 mg tablet", "Completed"), ("Metformin 500 mg tablet", "Active")])
        j, _ = self.previewed(pid, lakeside)
        self.assertFalse(j["new"], "Lakeside's file again: its own, here")
        j, _ = self.previewed(pid, ccd("Lakeside Clinic (fictional)", "Lisinopril 10 mg tablet", "active"))
        self.assertEqual({k: j["duplicates"]["changed"][k] for k in ("n", "kinds")}, {"n": 1, "kinds": {"med": 1}})
        self.save(pid, j)
        self.assertEqual(meds(pid), [("Lisinopril 10 mg tablet", "Active"), ("Metformin 500 mg tablet", "Active")], "Harbor's as it was")

    def test_another_clinics_carespace_document_of_the_same_name_is_its_own(self):   # #968
        """Two CareSpace exports from two clinics, each with a document of the same file name (its key, carespace:
        document/<name>): the second clinic's were left out as already here. Each says its clinic now (its CCD's
        custodian), so they're added as its own, and found again by their key naming it."""
        full = folder_files(os.path.join(FIX, "carespace", "full"))
        other = {**full, "carespace_ccd_full.xml": full["carespace_ccd_full.xml"].replace(b"Lakeshore Oncology Associates", b"Pinecrest Oncology (fictional)")}
        self.assertNotEqual(other, full)
        pid = self.profile("Two clinics' CareSpace exports")
        self.assertEqual(self.save(pid, self.previewed(pid, full)[0])["state"], "done")
        j, _ = self.previewed(pid, other)
        self.assertEqual(j["counts"].get("documents"), 2, "Pinecrest's documents are its own")
        self.save(pid, j)
        docs = [e for e in assembled(pid)["events"] if e["k"] == "document"]
        self.assertEqual(sorted(e["sub"].split(" · ")[-1] for e in docs), ["Lakeshore Oncology Associates"] * 2 + ["Pinecrest Oncology (fictional)"] * 2)
        j, _ = self.previewed(pid, other)
        self.assertEqual(j["counts"].get("documents"), 0, "Pinecrest's again: here")

    def test_an_observation_is_found_by_its_id_whichever_bundle_it_comes_in(self):   # #879
        """A FHIR Observation is the same record by its id whichever bundle it comes in: inside a DiagnosticReport (its
        labitem keyed by the report), or on its own (keyed by its day). Uploaded alone after coming in a report, it was
        taken for another source's: kept as a copy, with a lab day of its own beside the report's, or, its range
        corrected, as a second result. Now the same again is a duplicate, nothing added; its range corrected updates it
        in place, its row with it, and Undo puts it back; the other way round alike. Another source's of that time, its
        own Observation, stays another result (#885)."""
        t = "2025-09-10T08:30:00"

        def obs(oid, rng, v=5.2):
            return {"resourceType": "Observation", "id": oid, "status": "final", "category": [{"coding": [{"code": "laboratory"}]}],
                    "code": {"coding": [{"system": "http://loinc.org", "code": "2823-3"}], "text": "Potassium"}, "effectiveDateTime": t,
                    "valueQuantity": {"value": v, "unit": "mmol/L"},
                    "referenceRange": [{"low": {"value": rng[0]}, "high": {"value": rng[1]}, "text": f"{rng[0]}-{rng[1]}"}]}
        na = {**obs("na-1", (135, 145), 140), "code": {"coding": [{"system": "http://loinc.org", "code": "2951-2"}], "text": "Sodium"}}

        def sent(*rs, report=False):
            rs = list(rs) + ([{"resourceType": "DiagnosticReport", "id": "bmp-1", "status": "final", "code": {"text": "Basic metabolic panel"},
                               "effectiveDateTime": t, "result": [{"reference": f"Observation/{r['id']}"} for r in rs]}] if report else [])
            return {"labs.json": json.dumps({"resourceType": "Bundle", "type": "collection", "entry": [{"resource": r} for r in rs]})}

        def said(pid):   # each Potassium result: its range and flag, and its row's range as printed
            got = assembled(pid)
            self.assertEqual(rowless(got), [], "every result with its row")
            drawer = crossmatch._rows(got)
            return sorted((p[2], p[3], p[4], got["details"][str(at[0])]["rows"][at[1]][3]) for s in got["labs"] if s["name"] == "Potassium, Blood"
                          for p in s["pts"] for at in [crossmatch._row_of(drawer, s, p)])
        for first, again in ((True, False), (False, True)):   # in its report, then alone; alone, then in its report
            with self.subTest(report_first=first):
                pid = self.profile(f"An Observation, {'its report' if first else 'alone'} first")
                self.assertEqual(self.save(pid, self.previewed(pid, sent(obs("k-1", (3.5, 5.3)), na, report=first))[0])["state"], "done")
                before, n = said(pid), count(pid)
                self.assertEqual(before, [(3.5, 5.3, "", "3.5-5.3")])
                j, _ = self.previewed(pid, sent(obs("k-1", (3.5, 5.3)), report=again))   # the same Observation
                self.assertEqual((j["new"], j["duplicates"]["results"], j["duplicates"]["copies"], j["duplicates"]["updates"]), (False, 1, 0, 0), j["duplicates"])
                self.assertEqual(self.save(pid, j)["state"], "discarded")
                self.assertEqual(count(pid), n, "nothing added")
                j, _ = self.previewed(pid, sent(obs("k-1", (3.5, 5.1)), report=again))   # its range corrected
                self.assertEqual((j["duplicates"]["results"], j["duplicates"]["copies"], j["duplicates"]["updates"], j["counts"]["results"]),
                                 (0, 0, 1, 0), j["duplicates"])
                done = self.save(pid, j)
                self.assertEqual((done["state"], done["counts"].get("updated")), ("done", 1), done)
                self.assertEqual(said(pid), [(3.5, 5.1, "H", "3.5-5.1")], "updated in place, its row with it")
                self.assertEqual({k: v for k, v in count(pid).items() if k != "imports"}, {k: v for k, v in n.items() if k != "imports"})
                r = self.robin.post(f"/api/profiles/{pid}/imports/{done['import_id']}/undo", headers=H)
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(said(pid), before, "undone: as it was")
                j, _ = self.previewed(pid, sent(obs("plan-k", (3.5, 5.1)), report=again))   # another source's at that time
                self.assertEqual((j["duplicates"]["copies"], j["duplicates"]["updates"]), (1, 0), j["duplicates"])
                self.assertEqual(self.save(pid, j)["state"], "done")
                self.assertEqual(said(pid), [(3.5, 5.1, "H", "3.5-5.1"), (3.5, 5.3, "", "3.5-5.3")], "another result, its own row")

    def test_another_source_at_one_time_is_never_the_same_record(self):   # #885
        """Only the same record of the same source is updated in place (#869): an Observation by its own id, a report
        by its own. A hospital's FHIR file of Potassium 5.05 (3.5–5.0, High) and Sodium, results on their own (no
        report), then a health plan's of that Potassium at that time, another Observation in a labitem keyed by the same
        day: with no range it's a duplicate, with another range (3.5–5.1) a copy with its own row, and the hospital's
        result and row keep their range and flag. Both were taken for the hospital's sent again ("1 lab result already
        here from the same source is updated"): the plan's no range and no flag, or its range, replaced the hospital's.
        The hospital's Observation sent again, its range corrected, is updated in place, the plan's copy as it was."""
        t = "2025-09-03T08:30:00"

        def obs(oid, code, name, v, unit, rng=None, flag=None):
            r = {"resourceType": "Observation", "id": oid, "status": "final", "category": [{"coding": [{"code": "laboratory"}]}],
                 "code": {"coding": [{"system": "http://loinc.org", "code": code}], "text": name}, "effectiveDateTime": t,
                 "valueQuantity": {"value": v, "unit": unit}}
            if rng:
                r["referenceRange"] = [{"low": {"value": rng[0]}, "high": {"value": rng[1]}, "text": f"{rng[0]}-{rng[1]}"}]
            if flag:
                r["interpretation"] = [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation", "code": flag}]}]
            return r
        sent = lambda name, *rs: {name: json.dumps({"resourceType": "Bundle", "type": "collection", "entry": [{"resource": r} for r in rs]})}   # noqa: E731
        k = lambda oid, rng=None, flag=None: obs(oid, "2823-3", "Potassium", 5.05, "mmol/L", rng, flag)   # noqa: E731

        def said(pid):   # each result: its range and flag, and its row's range as printed and flag as read
            got = assembled(pid)
            drawer, out = crossmatch._rows(got), {}
            for s in got["labs"]:
                for p in s["pts"]:
                    at = crossmatch._row_of(drawer, s, p)
                    row = at and got["details"][str(at[0])]["rows"][at[1]]
                    out.setdefault(s["name"], []).append((p[2], p[3], p[4], row and row[3], row and ranges.read_flag(row[4], p[1], p[2], p[3], p[5])))
            self.assertEqual(rowless(got), [], "every result with its own row")
            return {n: sorted(v) for n, v in out.items()}

        pid = self.profile("Two sources at one time")
        hospital = sent("hospital.json", k("hosp-k", (3.5, 5.0), "H"), obs("hosp-na", "2951-2", "Sodium", 140, "mmol/L", (135, 145)))
        self.assertEqual(self.save(pid, self.previewed(pid, hospital)[0])["state"], "done")
        before = said(pid)
        self.assertEqual(before, {"Potassium, Blood": [(3.5, 5.0, "H", "3.5-5.0", "H")], "Sodium, Blood": [(135, 145, "", "135-145", "")]})
        j, _ = self.previewed(pid, sent("plan.json", k("plan-77")))   # the plan's, no range: a duplicate
        self.assertEqual((j["new"], j["duplicates"]["results"], j["duplicates"].get("copies", 0), j["duplicates"].get("updates", 0)), (False, 1, 0, 0), j["duplicates"])
        self.assertEqual(self.save(pid, j)["state"], "discarded")
        self.assertEqual(said(pid), before, "the hospital's result and row as they were")
        j, _ = self.previewed(pid, sent("plan.json", k("plan-77", (3.5, 5.1))))   # its own range: a copy, with its own row
        self.assertEqual((j["new"], j["duplicates"]["results"], j["duplicates"].get("copies", 0), j["duplicates"].get("brings"), j["duplicates"].get("updates", 0)),
                         (True, 0, 1, {"range": 1}, 0), j["duplicates"])
        done = self.save(pid, j)
        self.assertEqual((done["state"], done["counts"]["results"], done["counts"].get("updated")), ("done", 1, None), done)
        self.assertEqual(said(pid), {**before, "Potassium, Blood": [(3.5, 5.0, "H", "3.5-5.0", "H"), (3.5, 5.1, "", "3.5-5.1", "")]},
                         "two results, each its own range and row")
        j, _ = self.previewed(pid, sent("hospital.json", k("hosp-k", (3.5, 5.2))))   # the hospital's own, amended
        self.assertEqual((j["duplicates"]["results"], j["duplicates"].get("copies", 0), j["duplicates"].get("updates", 0)), (0, 0, 1), j["duplicates"])
        self.assertEqual(self.save(pid, j)["counts"].get("updated"), 1)
        self.assertEqual(said(pid), {**before, "Potassium, Blood": [(3.5, 5.1, "", "3.5-5.1", ""), (3.5, 5.2, "", "3.5-5.2", "")]},
                         "the hospital's updated in place, the plan's copy as it was")

    def test_a_value_its_source_amends_is_updated_in_place(self):   # #877
        """A FHIR Observation sent again with its value amended (the hospital's Potassium 5.05, then 5.25, the same id)
        was added beside the old one, a second result with a second row, the old (wrong) value shown beside the
        corrected one. Now the same record from the same source is updated in place whatever changes, its value too,
        result and row, the old value kept for Undo; another source's result at that time with another value (a health
        plan's 5.4, another Observation) is its own result, never taken for the hospital's amended."""
        t = "2025-09-03T08:30:00"
        k = lambda oid, v, rng=(3.5, 5.0): {"resourceType": "Observation", "id": oid, "status": "final", "category": [{"coding": [{"code": "laboratory"}]}],   # noqa: E731
                                            "code": {"coding": [{"system": "http://loinc.org", "code": "2823-3"}], "text": "Potassium"}, "effectiveDateTime": t,
                                            "valueQuantity": {"value": v, "unit": "mmol/L"}, "referenceRange": [{"low": {"value": rng[0]}, "high": {"value": rng[1]}}]}
        sent = lambda name, *rs: {name: json.dumps({"resourceType": "Bundle", "type": "collection", "entry": [{"resource": r} for r in rs]})}   # noqa: E731

        def said(pid):   # each Potassium result: its value, and its row's value as printed
            got = assembled(pid)
            drawer, out = crossmatch._rows(got), []
            for s in got["labs"]:
                for p in s["pts"]:
                    at = crossmatch._row_of(drawer, s, p)
                    out.append((p[1], p[4], at and got["details"][str(at[0])]["rows"][at[1]][1]))
            self.assertEqual(crossmatch.lab_faults(got), [], "each result with its row, in one lab day")
            return sorted(out)

        pid = self.profile("A value amended")
        self.assertEqual(self.save(pid, self.previewed(pid, sent("hospital.json", k("hosp-k", 5.05)))[0])["state"], "done")
        before, n = said(pid), count(pid)
        self.assertEqual(before, [(5.05, "H", "5.05")])
        j, _ = self.previewed(pid, sent("hospital.json", k("hosp-k", 5.25)))
        self.assertEqual((j["new"], j["counts"]["results"], j["duplicates"].get("updates", 0)), (True, 0, 1), j["duplicates"])
        done = self.save(pid, j)
        self.assertEqual((done["state"], done["counts"]["results"], done["counts"].get("updated")), ("done", 0, 1), done)
        self.assertEqual((said(pid), {x: v for x, v in count(pid).items() if x != "imports"}), ([(5.25, "H", "5.25")], {x: v for x, v in n.items() if x != "imports"}),
                         "one result, its value and its row's as sent now, nothing added")
        report = {"resourceType": "DiagnosticReport", "id": "plan-bmp", "status": "final", "category": [{"coding": [{"code": "LAB"}]}],
                  "code": {"text": "Basic metabolic panel"}, "effectiveDateTime": t, "result": [{"reference": "Observation/plan-78"}]}
        j, _ = self.previewed(pid, sent("plan.json", report, k("plan-78", 5.4, (3.5, 5.1))))   # another source's report, at that time
        self.assertEqual((j["counts"]["results"], j["duplicates"].get("updates", 0)), (1, 0), j["duplicates"])
        plan = self.save(pid, j)
        self.assertEqual(said(pid), [(5.25, "H", "5.25"), (5.4, "H", "5.4")], "its own result, the hospital's as it was")
        for iid in (plan["import_id"], done["import_id"]):
            r = self.robin.post(f"/api/profiles/{pid}/imports/{iid}/undo", headers=H)
            self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(said(pid), before, "undone: 5.05 again, its row with it")

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

    def test_no_result_without_its_row(self):   # #878
        """No lab result is saved without its row, a text result neither, whatever the upload: after every save the
        records keep to what every lab day keeps to (rowless: crossmatch.lab_faults). A result new to the profile whose
        labitem the profile has by key was saved with no row and no lab day, the Timeline's badge counting it while no
        drawer listed it: a FHIR file's results on their own, then a health plan's of that day with a Glucose and a urine
        colour new (one key names a day's results); a report sent again with two Observations added; the portal's export
        after a hospital's file of that day (its LDL and urine colour on their own); a CareSpace panel, an Epic order and a
        C-CDA organizer sent again with a result added. Each is kept with its own row, in a lab day holding it, the rest
        duplicates as before. A spreadsheet and Type them in, keyed by result, alike. And each importer's fixtures,
        uploaded into a profile of their own and again."""
        t = "2025-12-02T08:30:00"

        def obs(oid, code, name, v, unit=""):
            return {"resourceType": "Observation", "id": oid, "status": "final", "category": [{"coding": [{"code": "laboratory"}]}],
                    "code": {"coding": [{"system": "http://loinc.org", "code": code}], "text": name}, "effectiveDateTime": t,
                    **({"valueQuantity": {"value": v, "unit": unit}} if isinstance(v, (int, float)) else {"valueString": v})}

        def fhir(*loose, report=None):   # report: (its id, [its Observations])
            rs = list(loose) + (report[1] if report else [])
            if report:
                rs.append({"resourceType": "DiagnosticReport", "id": report[0], "status": "final", "code": {"text": "Basic panel"},
                           "effectiveDateTime": t, "result": [{"reference": f"Observation/{o['id']}"} for o in report[1]]})
            return {"labs.json": json.dumps({"resourceType": "Bundle", "type": "collection", "entry": [{"resource": r} for r in rs]})}
        k, g = lambda oid: obs(oid, "2823-3", "Potassium", 4.1, "mmol/L"), lambda oid: obs(oid, "2345-7", "Glucose", 95, "mg/dL")   # noqa: E731
        colour = lambda oid: obs(oid, "5778-6", "Color of Urine", "Yellow")   # noqa: E731

        def epic(*parts):   # one order at one time, each part (test, unit, value) a component of its result
            details = {"r0": {"orderName": "Basic panel", "results": [{"name": "Basic panel", "orderMetadata": {"prioritizedInstantISO": t, "resultType": "LAB"},
                       "resultComponents": [{"componentInfo": {"name": n, "units": u}, "componentResultInfo": {"value": str(v), "numericValue": v if isinstance(v, (int, float)) else None,
                                                                                                              "referenceRange": {"formattedReferenceRange": ""}}} for n, u, v in parts]}]}}
            return {"test-results/fictional_test_results_raw.json": json.dumps({"groups": [{"resultList": ["r0"]}], "details": details})}

        def carespace(*rows):
            with tempfile.TemporaryDirectory() as into:
                out = {}
                for rel, path in M.carespace([(n, u, t, v, "") for n, u, v in rows], into).items():
                    with open(path, "rb") as f:
                        out[rel] = f.read()
                return out
        ccda = lambda *rows: {"labs.xml": M.ccda([(n, u, t, str(v), "") for n, u, v in rows]).replace(   # noqa: E731
            '<organizer classCode="BATTERY" moodCode="EVN">', '<organizer classCode="BATTERY" moodCode="EVN"><id root="2.16.840.1.113883.19.5.99" extension="panel-1"/>')}
        sheet = lambda *rows: {"labs.csv": "Date,Time,Test,Value,Unit\n" + "".join(f"2025-12-02,08:30,{n},{v},{u}\n" for n, u, v in rows)}   # noqa: E731
        K, G, C = ("Potassium", "mmol/L", 4.1), ("Glucose", "mg/dL", 95), ("Color of Urine", "", "Yellow")
        cases = {   # kind: (the first file, the second, the results the second adds: (series, value))
            "FHIR, another source's of the day": (fhir(k("hosp-k")), fhir(k("plan-k"), g("plan-g"), colour("plan-c")), {("Glucose, Blood", 95), ("Color of Urine", "Yellow")}),
            "FHIR, a report sent again": (fhir(report=("rep-1", [k("k1")])), fhir(report=("rep-1", [k("k1"), g("g1"), colour("c1")])),
                                          {("Glucose, Blood", 95), ("Color of Urine", "Yellow")}),
            "FHIR, a portal's export after a hospital's file": (fhir(k("h-k"), obs("h-na", "2951-2", "Sodium", 140, "mmol/L")),
                                                                 fhir(obs("p-ldl", "13457-7", "LDL Cholesterol", 162, "mg/dL"), colour("p-c"),
                                                                      report=("p-cbc", [obs("p-hgb", "718-7", "Hemoglobin", 12.1, "g/dL")])),
                                                                 {("LDL Cholesterol", 162), ("Color of Urine", "Yellow"), ("Hemoglobin", 12.1)}),
            "CareSpace, a panel sent again": (carespace(K), carespace(K, G), {("Glucose, Blood", 95)}),
            "Epic, an order sent again": (epic(K), epic(K, G, C), {("Glucose, Blood", 95), ("Color of Urine", "Yellow")}),
            "C-CDA, an organizer sent again": (ccda(K), ccda(K, G, C), {("Glucose, Blood", 95), ("Color of Urine", "Yellow")}),
            "a spreadsheet": (sheet(K), sheet(K, G, C), {("Glucose, Blood", 95), ("Color of Urine", "Yellow")}),
        }
        got = lambda d: {(s["name"], x[1]) for s in d["labs"] for x in s["pts"] + (s.get("txt") or [])}   # noqa: E731
        for kind, (first, second, adds) in cases.items():
            with self.subTest(kind):
                pid = self.profile(kind)
                self.assertEqual(self.save(pid, self.previewed(pid, first)[0])["state"], "done")
                self.assertEqual(rowless(assembled(pid)), [], "the first")
                before = got(assembled(pid))
                j, _ = self.previewed(pid, second)
                self.assertEqual(self.save(pid, j)["state"], "done", j)
                after = assembled(pid)
                self.assertEqual(rowless(after), [], "the second: each new result with its row")
                self.assertEqual({(n, float(v) if isinstance(v, (int, float)) else v) for n, v in got(after) - before},
                                 {(n, float(v) if isinstance(v, (int, float)) else v) for n, v in adds}, "what it adds, no more")
        pid, c = self.profile("Typed in"), self.robin   # Type them in: a row keyed by its result, as a spreadsheet's
        typed = lambda rows: c.post(f"/api/profiles/{pid}/manual", json={"rows": [{"date": t[:10], "time": t[11:16], "test": n, "value": str(v), "unit": u}   # noqa: E731
                                                                                  for n, u, v in rows], "save": True}, headers=H)
        self.assertEqual(typed([K]).status_code, 200)
        self.assertEqual(typed([K, G, C]).status_code, 200)
        self.assertEqual(rowless(assembled(pid)), [], "typed in")
        for name in sorted(os.listdir(FIX)):   # every importer's fixtures, uploaded and again
            for case in sorted(os.listdir(os.path.join(FIX, name))):
                root = os.path.join(FIX, name, case)
                if not os.path.isdir(root) or case.startswith(("_", ".")):
                    continue
                with self.subTest(f"{name}/{case}"):
                    pid, files = self.profile(f"{name}/{case}"), folder_files(root)
                    j, _ = self.previewed(pid, files)
                    self.assertEqual(self.save(pid, j)["state"], "done", j)
                    self.assertEqual(rowless(assembled(pid)), [], "uploaded")
                    self.save(pid, self.previewed(pid, files)[0])
                    self.assertEqual(rowless(assembled(pid)), [], "uploaded again")

    def test_a_vitals_copy_keeps_what_it_brings(self):   # #880
        """A vital sign already here whose copy from another source brings a range or a flag is kept as a copy (#846),
        with no row: a visit's Vitals have none, no lab day lists them (#868's rule is for lab results). A hospital's
        heart rate of 112 with no range, then a portal's of it with 60–100 and High: the copy was left out as a
        duplicate, so Body and activity and Labs said 112 with no range and no flag. Now it's kept, the reading is read
        as one with the range and flag (crossmatch.copies), and the same again is a duplicate."""
        t = "2025-08-20T10:15:00"

        def vital(oid, rng=None, flag=None):
            r = {"resourceType": "Observation", "id": oid, "status": "final", "category": [{"coding": [{"code": "vital-signs"}]}],
                 "code": {"coding": [{"system": "http://loinc.org", "code": "8867-4"}], "text": "Heart rate"}, "effectiveDateTime": t,
                 "valueQuantity": {"value": 112, "unit": "/min"}}
            if rng:
                r["referenceRange"] = [{"low": {"value": rng[0]}, "high": {"value": rng[1]}, "text": f"{rng[0]}-{rng[1]}"}]
            if flag:
                r["interpretation"] = [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation", "code": flag}]}]
            return {"vitals.json": json.dumps({"resourceType": "Bundle", "type": "collection", "entry": [{"resource": r}]})}
        pid = self.profile("A vital's copy")
        self.assertEqual(self.save(pid, self.previewed(pid, vital("hosp-hr"))[0])["state"], "done")
        portal = vital("portal-hr", (60, 100), "H")
        j, _ = self.previewed(pid, portal)
        self.assertEqual((j["new"], j["duplicates"]["results"], j["duplicates"]["copies"], j["duplicates"]["brings"]), (True, 0, 1, {"range": 1}), j["duplicates"])
        self.assertEqual(self.save(pid, j)["state"], "done")
        got = assembled(pid)
        pts = [p for s in got["labs"] if s.get("panel") == "Vitals" for p in s["pts"]]
        self.assertEqual([(p[1], p[2], p[3], p[4]) for p in pts], [(112, 60, 100, "H")] * 2, "the reading with its range and flag, either copy")
        self.assertEqual(rowless(got), [])
        j, _ = self.previewed(pid, portal)   # the same again
        self.assertEqual((j["new"], j["duplicates"]["results"], j["duplicates"]["copies"]), (False, 1, 0), j["duplicates"])

    def test_a_file_read_again_after_its_reading_changed(self):   # #874
        """A file imported before #870 changed how it reads (a FHIR Quantity's bound, "<=5" or ">60", kept as text; a
        C-CDA document's "4,5", "=<5" or "1.2E-3" too), uploaded again: its review says each result is already here
        and its save adds nothing. preview.find matched points only with points and text only with text, so each was
        added again beside its older copy (Labs charted the one and listed the other as text)."""
        from server.importers import ccda, fhir
        t = lambda d: f"2025-08-0{d}T08:00:00"   # noqa: E731
        rows = [("eGFR", "mL/min/1.73m2", t(1), ">60", ">=60"), ("C-Reactive Protein", "mg/L", t(2), "=<5", "<5"),
                ("Potassium", "mmol/L", t(3), "4,5", "3.5-5.1"), ("Troponin I", "ng/mL", t(4), "0,500", "<0.04"),
                ("AST", "U/L", t(5), "≥60", "10-40"), ("Bilirubin, Total", "mg/dL", t(6), "1.2E-3", "0.1-1.2")]
        for fmt, name, text, parse in (("fhir", "labs.json", M.fhir(rows), fhir.parse), ("ccda", "labs.xml", M.ccda(rows), ccda.parse)):
            with self.subTest(fmt=fmt):
                pid, path = self.profile(f"Read again, {fmt}"), self.write_tmp(text)
                data = parse({name: path})["data"]
                db = dbm.connect()
                try:
                    with dbm.transaction(db):   # as an import before #870 kept it: each a text result
                        records.store(db, pid, M.read_before(data), "upload", fmt=fmt)
                finally:
                    db.close()
                n, pts = count(pid), sum(len(s["pts"]) for s in data["labs"])
                self.assertEqual((n["results"], pts), (0, len(rows)), "kept as text, read as points now")
                j, _ = self.previewed(pid, {name: text})
                self.assertEqual((j["new"], j["counts"]["results"], j["duplicates"]["results"], j["duplicates"]["copies"], j["duplicates"]["updates"]),
                                 (False, 0, pts, 0, 0), j["duplicates"])
                self.assertEqual(self.save(pid, j)["state"], "discarded", "nothing to add")
                self.assertEqual(count(pid), n, "nothing added")

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
