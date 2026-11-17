#!/usr/bin/env python3
"""Previewing an import before it's saved (#22) and the stable source keys it matches on (#89): re-uploading the
same export shows everything as duplicates and saving it adds nothing (C-CDA, FHIR, CSV and a Sanguity export); an
overlapping export adds only what's new; a duplicate can be kept anyway; a trimmed lab day still holds together;
cancelling leaves no rows, no preview file and no upload; roles and CSRF; key hashes backfilled at start-up; the
preview encrypted on disk; and every importer's keys unique and stable whatever order the files come in.

Fictional data only.

  python3 tests/test_preview.py
"""
import io, json, os, shutil, sys, tempfile, time, unittest

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
        return self.until(pid, j["id"], ("done", "failed"))

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
                self.assertTrue(all(i["ref"][:2] in ("e:", "s:") for i in again["duplicates"]["items"]))
                done = self.save(pid, again)
                self.assertEqual(done["state"], "done", done)
                self.assertIsNone(done["import_id"])
                self.assertEqual(count(pid), after)   # saving added nothing
                self.assertEqual(self.robin.get(f"/api/profiles/{pid}/data").json(), data)
                self.assertEqual(done["skipped"]["events"], first["counts"]["events"])

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
