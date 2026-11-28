#!/usr/bin/env python3
"""Insurance cards (server/insurance.py, server/routes_insurance.py, issue #1137): plans added, changed and removed by
owners and editors, read by viewers, refused to strangers; what can't be saved; the member ID encrypted at rest and in
no activity entry; the card's photos checked and cleaned as journal photos are, sealed, replaced and removed with their
files; card_lines for the emergency card (#1126); a share link shows nothing about insurance unless Insurance cards
is ticked, its photos then too and only then; in "Export everything" and loaded back from it; and in a backup's check.
Fictional data only.

  python3 tests/test_insurance.py
"""
import base64, io, json, os, re, shutil, sqlite3, struct, sys, tempfile, time, unittest, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-insurance-test-")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "no-build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"))
for d in ("records", "keys"):
    os.makedirs(os.path.join(TMP, d))
sys.path.insert(0, ROOT)

import logging   # noqa: E402

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, backup, config, crypto, db as dbm, export, insurance, main, profiles as prof, records, share_links   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites", "lee": "maple tunnel brisk saddle",
      "zed": "a stranger to every profile"}
MEMBER, GROUP, PLAN = "FICTMEMBER9876543", "FICTGROUP-0042", "Fictional Health Plan Gold"
with open(os.path.join(ROOT, "tests", "test_journal.py"), encoding="utf-8") as f:   # its tiny fictional picture
    JPEG = base64.b64decode("".join(re.findall(r'"([A-Za-z0-9+/=]+)"', f.read().split("JPEG = base64.b64decode(", 1)[1].split(")", 1)[0])))
COMMENT = b"FICTIONAL-CAMERA-SERIAL-1137"
PHOTO = JPEG[:20] + b"\xff\xfe" + struct.pack(">H", len(COMMENT) + 2) + COMMENT + JPEG[20:]   # a comment block: taken out


def client():
    return TestClient(main.app, base_url="http://testserver")


class Insurance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = client().__enter__()
        db = dbm.connect()
        uid = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        cls.pid = prof.create_profile(db, "Robin Example (fictional)", owner_id=uid["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (uid["kim"], cls.pid))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (uid["lee"], cls.pid))
        with dbm.transaction(db):
            records.store(db, cls.pid, {"events": [{"id": 1, "t": "2026-10-01T09:00:00", "k": "visit", "title": "Annual physical", "sub": ""}],
                                        "details": {}, "labs": [], "costs": []}, "dataset")
        db.close()
        cls.c = {}
        for n in PW:
            c = client()
            assert c.post("/api/login", json={"username": n, "password": PW[n]}, headers=H).status_code == 200
            cls.c[n] = c

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        shutil.rmtree(TMP, True)

    def setUp(self):
        share_links.throttle._fails.clear()
        db = dbm.connect()
        for iid in [r[0] for r in db.execute("SELECT id FROM insurance_plans")]:
            with dbm.transaction(db):
                insurance.remove(db, self.pid, iid)
        db.close()

    def url(self, rest="", pid=None):
        return f"/api/profiles/{pid or self.pid}/insurance{rest}"

    def add(self, who="robin", status=201, **plan):
        plan = {"name": PLAN, "member": MEMBER, "group": GROUP,
                "phones": [{"label": "Member services", "number": "1-800-555-0137"}], **plan}
        r = self.c[who].post(self.url(), json=plan, headers=H)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def photo(self, iid, side="front", data=PHOTO, who="robin", status=201):
        r = self.c[who].put(self.url(f"/{iid}/{side}"), content=data, headers={**H, "Content-Type": "application/octet-stream"})
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def files(self):
        d = insurance.folder(self.pid)
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    # ---------------------------------------------------------------- a plan

    def test_add_change_remove_and_who_may(self):
        iid = self.add()["id"]
        got = self.c["kim"].get(self.url()).json()
        self.assertFalse(got["can_change"], "a viewer reads them")
        p = got["plans"][0]
        self.assertEqual((p["name"], p["member"], p["group"], p["phones"], p["front"], p["back"], p["by"]),
                         (PLAN, MEMBER, GROUP, [{"label": "Member services", "number": "1-800-555-0137"}], None, None, "robin"))
        self.assertTrue(self.c["lee"].get(self.url()).json()["can_change"])
        self.add(who="kim", status=403)
        self.assertEqual(self.c["kim"].put(self.url(f"/{iid}"), json={"name": "X"}, headers=H).status_code, 403)
        self.assertEqual(self.c["kim"].delete(self.url(f"/{iid}"), headers=H).status_code, 403)
        self.photo(iid, who="kim", status=403)
        self.assertEqual(self.c["zed"].get(self.url()).status_code, 404, "not shared with them: not found")
        self.assertEqual(self.c["zed"].get(self.url(f"/{iid}/front")).status_code, 404)
        self.assertEqual(client().get(self.url()).status_code, 401)
        self.assertEqual(self.c["robin"].post(self.url(), json={"name": PLAN}).status_code, 403, "the CSRF check, as every change")
        # an editor changes it; its photos stay
        self.photo(iid)
        r = self.c["lee"].put(self.url(f"/{iid}"), json={"name": "Fictional Dental", "member": "D-1", "group": "", "phones": []}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        p = r.json()["plans"][0]
        self.assertEqual((p["name"], p["member"], p["group"], p["phones"], p["changed_by"]), ("Fictional Dental", "D-1", "", [], "lee"))
        self.assertEqual(p["front"], {"type": "jpeg"})
        self.assertEqual(self.c["lee"].put(self.url("/99999"), json={"name": "X"}, headers=H).status_code, 404)
        self.assertEqual(self.c["robin"].delete(self.url(f"/{iid}"), headers=H).json()["plans"], [])
        self.assertEqual(self.files(), [], "its photos went with it")
        self.assertEqual(self.c["robin"].delete(self.url(f"/{iid}"), headers=H).status_code, 404)

    def test_what_cant_be_saved(self):
        for plan, words in (({"name": " "}, "Give the plan's name"), ({"name": "x" * 121}, "at most 120"),
                            ({"name": "P", "member": "1" * 61}, "member ID can be at most"),
                            ({"name": "P", "phones": [{"label": "Claims", "number": ""}]}, "Give the number for “Claims”"),
                            ({"name": "P", "phones": [{"number": "<script>"}]}, "digits, letters"),
                            ({"name": "P", "phones": [{"number": str(i)} for i in range(5)]}, "at most 4")):
            r = self.c["robin"].post(self.url(), json=plan, headers=H)
            self.assertEqual(r.status_code, 400, plan)
            self.assertIn(words, r.json()["detail"])
        for _ in range(insurance.MAX_PLANS):
            self.add()
        self.assertIn("at most 10", self.add(status=409)["detail"])

    def test_member_id_encrypted_and_never_logged(self):
        iid = self.add()["id"]
        self.photo(iid)
        self.c["robin"].put(self.url(f"/{iid}"), json={"name": PLAN, "member": MEMBER + "X"}, headers=H)
        con = sqlite3.connect(config.DB_PATH)
        try:
            dump = "\n".join(con.iterdump())
        finally:
            con.close()
        for word in (MEMBER, GROUP, PLAN, "0137"):
            self.assertNotIn(word, dump, "encrypted at rest (atrest.FIELDS)")
        log = self.c["robin"].get(f"/api/profiles/{self.pid}/activity?action=insurance").json()
        self.assertIn({"change": "photo", "side": "front"}, [e["detail"] for e in log])
        self.assertLessEqual({"add", "change", "photo"}, {e["detail"]["change"] for e in log})
        self.assertNotIn(MEMBER, json.dumps(log))
        self.assertNotIn(PLAN, json.dumps(log))
        with open(os.path.join(insurance.folder(self.pid), self.files()[0]), "rb") as f:
            self.assertTrue(crypto.file_is_encrypted(f.name), "the photo is sealed")
            self.assertNotIn(b"\xff\xd8\xff", f.read(64))

    def test_card_lines_for_the_emergency_card(self):
        iid = self.add()["id"]
        self.photo(iid)
        self.add(name="Fictional Dental", member="D-77", group="", phones=[])
        db = dbm.connect()
        try:
            self.assertEqual(insurance.card_lines(db, self.pid), [
                {"line": f"{PLAN} · Member ID {MEMBER} · Group {GROUP}", "name": PLAN, "member": MEMBER, "group": GROUP},
                {"line": "Fictional Dental · Member ID D-77", "name": "Fictional Dental", "member": "D-77", "group": ""}])
        finally:
            db.close()

    # ---------------------------------------------------------------- the photos

    def test_photos_checked_cleaned_replaced_removed(self):
        iid = self.add()["id"]
        got = self.photo(iid)
        self.assertEqual((got["type"], got["removed"]), ("jpeg", ["other details saved in the file"]))
        r = self.c["kim"].get(self.url(f"/{iid}/front"))
        self.assertEqual((r.status_code, r.headers["content-type"]), (200, "image/jpeg"), "a viewer sees it")
        self.assertNotIn(COMMENT, r.content, "its metadata taken out")
        self.assertIn("default-src 'none'", r.headers["content-security-policy"])
        self.assertEqual(self.c["kim"].get(self.url(f"/{iid}/back")).status_code, 404, "none on the back yet")
        self.assertEqual(self.c["kim"].get(self.url(f"/{iid}/side")).status_code, 422)
        first = self.files()
        self.photo(iid, "back")
        self.photo(iid)   # in place of the front's
        now = self.files()
        self.assertEqual(len(now), 2)
        self.assertFalse(set(first) & set(now), "the old front's file removed")
        self.assertIn("isn't a picture", self.photo(iid, data=b"%PDF-1.4 fictional", status=400)["detail"])
        self.assertEqual(self.photo(99999, status=404)["detail"], "That plan isn't here any more.")
        self.assertEqual(self.files(), now, "nothing written for one refused")
        r = self.c["lee"].delete(self.url(f"/{iid}/back"), headers=H)
        self.assertEqual(r.json()["plans"][0]["back"], None)
        self.assertEqual(len(self.files()), 1)
        self.assertEqual(self.c["lee"].delete(self.url(f"/{iid}/back"), headers=H).status_code, 404)
        # a file no plan names (a stop between writing and saving) is swept at start-up
        with open(os.path.join(insurance.folder(self.pid), "0" * 32 + ".img"), "wb") as f:
            f.write(b"x")
        db = dbm.connect()
        try:
            self.assertEqual(insurance.sweep_all(db), 1)
        finally:
            db.close()
        self.assertEqual(len(self.files()), 1)

    # ---------------------------------------------------------------- share links

    def link(self, sections):
        r = self.c["robin"].post(f"/api/profiles/{self.pid}/links", json={"sections": sections, "hours": 2}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["path"].split("#", 1)[1]

    def view(self, token, **extra):
        return client().post("/api/share/view", json={"token": token, **extra}, headers=H)

    def test_share_link_only_when_ticked(self):
        offered = lambda: [s["id"] for s in self.c["robin"].get(f"/api/profiles/{self.pid}/links").json()["sections"]]
        self.assertNotIn("insurance", offered(), "not offered while there's no plan")
        iid = self.add()["id"]
        self.photo(iid)
        self.photo(iid, "back")
        self.assertIn("insurance", offered())
        # every other section, not Insurance cards: nothing about insurance, and its photos aren't served
        others = [s for s in share_links.SECTION_IDS if s != "insurance"]
        token = self.link(others)
        r = self.view(token)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("insurance", r.json())
        for word in (MEMBER, GROUP, PLAN, "Insurance cards", "1-800-555-0137"):
            self.assertNotIn(word, r.text)
        for side in ("front", "back"):
            r = self.view(token, card=iid, side=side)
            self.assertEqual((r.status_code, r.text), (404, "Not found.\n"), "not ticked: not served")
        # ticked: the plan, and its photos
        token = self.link(["visits", "insurance"])
        j = self.view(token).json()
        self.assertEqual(j["insurance"], [{"id": iid, "name": PLAN, "member": MEMBER, "group": GROUP, "front": True, "back": True,
                                           "phones": [{"label": "Member services", "number": "1-800-555-0137"}]}])
        self.assertEqual([s["label"] for s in j["sections"]], ["Visits and hospital stays", "Insurance cards"])
        r = self.view(token, card=iid, side="back")
        self.assertEqual((r.status_code, r.headers["content-type"], r.headers["cache-control"]), (200, "image/jpeg", "no-store"))
        self.assertNotIn(COMMENT, r.content)
        self.assertEqual(self.view(token, card=iid + 1000, side="front").status_code, 404)
        for bad in ({"card": iid, "side": "middle"}, {"card": True, "side": "front"}):   # not a card's photo: the view itself
            self.assertEqual(self.view(token, **bad).headers["content-type"], "application/json", bad)
        self.assertNotIn("set-cookie", self.view(token, card=iid, side="front").headers)
        # another profile's plan through this link: not found
        db = dbm.connect()
        try:
            other = prof.create_profile(db, "Another (fictional)", owner_id=db.execute("SELECT id FROM users WHERE username='robin'").fetchone()[0])
            with dbm.transaction(db):
                oid = insurance.add(db, other, {"id": None, "username": "robin"}, insurance.check({"name": "Other plan"}))
                insurance.set_photo(db, other, oid, "front", JPEG, {"id": None, "username": "robin"})
        finally:
            db.close()
        self.assertEqual(self.view(token, card=oid, side="front").status_code, 404)
        # revoked: nothing
        lid = self.c["robin"].get(f"/api/profiles/{self.pid}/links").json()["links"][0]["id"]
        self.c["robin"].delete(f"/api/profiles/{self.pid}/links/{lid}", headers=H)
        self.assertEqual(self.view(token, card=iid, side="front").status_code, 404)

    # ---------------------------------------------------------------- export and backup

    def test_in_export_and_loaded_back(self):
        iid = self.add()["id"]
        self.photo(iid)
        r = self.c["lee"].get(f"/api/profiles/{self.pid}/export")
        self.assertEqual(r.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            data = json.loads(z.read("data.json"))
            name = f"insurance/{iid}-front.jpg"
            self.assertEqual(z.read(name)[:3], b"\xff\xd8\xff")
            self.assertNotIn(COMMENT, z.read(name))
            readme = z.read("README.txt").decode()
        plan = data["insurance"][0]
        self.assertEqual((plan["name"], plan["member"], plan["group"], plan["front"], plan["back"]), (PLAN, MEMBER, GROUP, name, None))
        self.assertIn("insurance/         the photos of the insurance cards: 1", readme)
        tmp = os.path.join(TMP, "export.zip")
        with open(tmp, "wb") as f:
            f.write(r.content)
        db = dbm.connect()
        try:
            pid2 = db.execute("INSERT INTO profiles(name, created) VALUES('Loaded (fictional)', 1)").lastrowid
            export.import_archive(db, pid2, tmp)
            back = insurance.listing(db, pid2)
            self.assertEqual([(p["name"], p["member"], p["phones"][0]["number"], p["front"], p["back"]) for p in back],
                             [(PLAN, MEMBER, "1-800-555-0137", {"type": "jpeg"}, None)])
            self.assertEqual(insurance.photo(db, pid2, back[0]["id"], "front")[0][:3], b"\xff\xd8\xff")
            export.import_archive(db, pid2, tmp)   # loaded again: the same plan isn't added twice
            self.assertEqual(len(insurance.listing(db, pid2)), 1)
            # a backup's check finds each card's photo among the files
            # uploaded on another profile's import page instead: the plan and its photo put back with the records
            pid3 = prof.create_profile(db, "Uploaded (fictional)", owner_id=db.execute("SELECT id FROM users WHERE username='robin'").fetchone()[0])
        finally:
            db.close()
        c = self.c["robin"]
        up = c.post(f"/api/profiles/{pid3}/uploads", headers=H).json()["id"]
        self.assertEqual(c.put(f"/api/profiles/{pid3}/uploads/{up}/files", params={"path": "sanguity-export.zip"}, content=r.content,
                               headers={**H, "Content-Type": "application/octet-stream"}).status_code, 200)
        self.assertEqual(c.post(f"/api/profiles/{pid3}/uploads/{up}/finish", headers=H).status_code, 200)
        jid, end = c.post(f"/api/profiles/{pid3}/uploads/{up}/import", json={}, headers=H).json()["id"], time.time() + 30
        while time.time() < end:
            j = c.get(f"/api/profiles/{pid3}/jobs/{jid}").json()
            if j["state"] == "preview":
                c.post(f"/api/profiles/{pid3}/jobs/{jid}/confirm", json={"keep": []}, headers=H)
            elif j["state"] in ("done", "failed", "discarded"):
                break
            time.sleep(0.05)
        self.assertEqual(j["state"], "done", j)
        got = c.get(self.url(pid=pid3)).json()["plans"]
        self.assertEqual([(p["name"], p["member"], p["front"]) for p in got], [(PLAN, MEMBER, {"type": "jpeg"})])
        self.assertEqual(c.get(self.url(f"/{got[0]['id']}/front", pid=pid3)).content[:3], b"\xff\xd8\xff")
        db = dbm.connect()
        try:
            self.assertEqual(backup.named_files(db).get(f"profiles/{pid2}/insurance/{db.execute('SELECT front FROM insurance_plans WHERE profile_id=?', (pid2,)).fetchone()[0]}"),
                             "card")
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
