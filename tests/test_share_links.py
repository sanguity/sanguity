#!/usr/bin/env python3
"""Share links (server/share_links.py, server/routes_share_links.py, issue #47): a link shows only the sections, labs
and date range its owner chose, and nothing else (no documents, no report text, no other section or profile); it is
read-only; only its token's hash is stored, with its name and scope encrypted; it stops working when it expires, when
it's revoked, or when its maker stops being an owner; anything refused is the same bare 404; viewers get no cookie
and theirs are ignored; views are throttled; every view is logged in the profile's activity without the token; only
owners make, list and revoke links. Fictional data only.

  python3 tests/test_share_links.py
"""
import json, os, re, sys, tempfile, unittest
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-share-test-")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "no-build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"))
os.makedirs(os.path.join(TMP, "records"))
os.makedirs(os.path.join(TMP, "keys"))
sys.path.insert(0, ROOT)

import logging, shutil   # noqa: E402

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, crypto, db as dbm, main, measures, profiles as prof, records, share_links   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites", "lee": "maple tunnel brisk saddle",
      "ash": "willow kettle amber stone", "zed": "a stranger to every profile"}
TODAY = date.today()
D = lambda n: (TODAY + timedelta(days=n)).isoformat()

# a fictional person: visits inside and outside the range, two labs, a medication, a procedure, claims, appointments
DATA = {"built": "2026-11-01T00:00:00+00:00", "source": "fictional",
        "events": [{"id": 1, "t": D(-30) + "T09:00:00", "k": "visit", "title": "Annual physical", "sub": "Dr. Fictional Example"},
                   {"id": 2, "t": D(-400), "k": "visit", "title": "Long-ago visit OLDVISIT", "sub": ""},
                   {"id": 3, "t": D(-60), "k": "procedure", "title": "Fictional colonoscopy", "sub": ""},
                   {"id": 4, "t": D(-90), "k": "med", "title": "Examplecillin 250 mg", "sub": ""},
                   {"id": 5, "t": D(-20), "k": "imaging", "title": "Chest X-ray", "sub": ""},
                   {"id": 6, "t": D(-10), "k": "visit", "title": "Hidden visit HIDDENVISIT", "sub": "", "hidden": True}],
        "details": {"3": {"text": "REPORTTEXT the full report of the procedure"}, "4": {"instructions": "Take one daily", "status": "active"},
                    "1": {"note": "DETAILNOTE private visit note"}},
        "costs": [{"id": 1, "d": D(-30), "billed": 200.0, "paid": 150.0, "owe": 50.0, "title": "Office visit CLAIMTITLE", "prov": "Riverside Clinic"}],
        "labs": [{"name": "Hemoglobin", "unit": "g/dL", "panel": "CBC", "n": 2, "txt": [],
                  "pts": [[D(-30) + "T08:00:00", 11.1, 12, 16, "L"], [D(-400) + "T08:00:00", 13.5, 12, 16, ""]]},
                 {"name": "SodiumSECRET", "unit": "mmol/L", "panel": "BMP", "n": 1, "txt": [], "pts": [[D(-30) + "T08:00:00", 140, 135, 145, ""]]}],
        "upcoming": [{"t": D(12) + "T10:30:00", "title": "Follow-up visit", "who": "Dr. Fictional Example", "where": "Riverside Clinic"},
                     {"t": D(-3), "title": "Already happened PASTAPPT", "who": "", "where": ""}]}


# #225: a fictional phone's readings, in the watch summary's shape (watch/receiver.py)
U = lambda n, h: f"{D(n)}T{h:02d}:00:00+00:00"
PHONE = {"tz": "UTC", "exercise": [], "metrics": [
    {"key": "spo2_avg", "unit": "%", "pts": [[D(-10), 97.2], [D(-1), 96.0]]},
    {"key": "glucose_meter", "unit": "mg/dL", "pts": [[D(-4) + "T07:12", 104.0]]},
    *({"key": "glucose_" + k, "unit": "", "pts": [[D(-5), v]]} for k, v in (("avg", 131.0), ("min", 62.0), ("max", 212.0), ("n", 288),
                                                                          ("hours", 24.0), ("lt54", 0.0), ("lt70", 4.0), ("in", 78.0), ("gt180", 18.0)))],
    "bp_readings": [[U(-40, 8), 150, 95, 70, "Fictional cuff", "Health Connect"], [U(-9, 8), 132, 84, 68, "Fictional cuff", "Health Connect"],
                    [U(-2, 20), 126, 80, None, None, "Apple Health"]],
    "temp_readings": [[U(-8, 7), 37.4, None, "Health Connect"]],
    "ecg": [{"key": "ecg:0123456789abcdef", "type": "ecg", "start": U(-6, 10), "classification": "Sinus Rhythm", "avg_hr": 72}]}

# #232: glucose and blood oxygen typed in at home (#224: local wall times, glucose in mg/dL with a meal note), beside the
# phone's daily blood oxygen; one glucose the phone's meter sent too
TYPED = {"tz": "America/New_York", "exercise": [], "metrics": [
    {"key": "spo2_avg", "unit": "%", "pts": [[D(-8), 96.5]]}, {"key": "spo2_min", "unit": "%", "pts": [[D(-8), 93.0]]},
    {"key": "glucose_meter", "unit": "mg/dL", "pts": [[D(-6) + "T07:12", 99.0]]}],
    "glucose_readings": [[D(-6) + "T07:12:00", 99.0, None, "Typed in", None], [D(-5) + "T07:45:00", 104.49, None, "Typed in", "before"],
                         [D(-30) + "T08:00:00", 140.0, None, "Typed in", "after"]],
    "spo2_readings": [[D(-4) + "T08:05:00", 97, None, "Typed in"], [D(-40) + "T08:05:00", 91, None, "Typed in"]]}


def read(*path):
    with open(os.path.join(ROOT, *path), encoding="utf-8") as f:
        return f.read()


def client():
    return TestClient(main.app, base_url="http://testserver")


class ShareLinks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = client().__enter__()
        db = dbm.connect()
        cls.uid = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        cls.pid = prof.create_profile(db, "Robin Example (fictional)", owner_id=cls.uid["robin"])
        cls.other = prof.create_profile(db, "Another (fictional)", owner_id=cls.uid["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (cls.uid["kim"], cls.pid))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (cls.uid["lee"], cls.pid))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'owner')", (cls.uid["ash"], cls.pid))
        with dbm.transaction(db):
            records.store(db, cls.pid, DATA, "dataset")
            records.store(db, cls.other, {**DATA, "events": [{"id": 1, "t": D(-5), "k": "visit", "title": "OTHERPROFILE visit", "sub": ""}],
                                          "labs": [], "costs": [], "upcoming": []}, "dataset")
        db.close()
        cls.c = {}
        for n in PW:
            c = client()
            r = c.post("/api/login", json={"username": n, "password": PW[n]}, headers=H)
            assert r.status_code == 200, r.text
            cls.c[n] = c
        r = cls.c["robin"].post(f"/api/profiles/{cls.pid}/journal", json={"kind": "symptom", "t": D(-15), "title": "Headache",
                                                                          "severity": 4, "note": "JOURNALNOTE after lunch"}, headers=H)
        assert r.status_code == 201, r.text

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        shutil.rmtree(TMP, True)

    def setUp(self):
        share_links.throttle._fails.clear()

    def make(self, who="robin", pid=None, status=200, **body):
        body = {"sections": ["visits"], **body}
        r = self.c[who].post(f"/api/profiles/{pid or self.pid}/links", json=body, headers=H)
        self.assertEqual(r.status_code, status, r.text)
        if status != 200:
            return r
        j = r.json()
        self.assertTrue(j["path"].startswith("share.html#"))
        return j["path"].split("#", 1)[1], j["link"]

    def view(self, token, status=200, c=None):
        r = (c or client()).post("/api/share/view", json={"token": token}, headers=H)
        self.assertEqual(r.status_code, status, r.text)
        return r

    def assert_bare(self, r):
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.text, "Not found.\n")
        self.assertTrue(r.headers["content-type"].startswith("text/plain"))
        self.assertNotIn("set-cookie", r.headers)

    # ---------------------------------------------------------------- what a link shows

    def test_only_the_chosen_sections_labs_and_range(self):
        token, link = self.make(label="Dr. Rivera", sections=["visits", "results", "procedures"], labs=["Hemoglobin"],
                                **{"from": D(-100), "to": D(0)})
        self.assertEqual(link["state"], "active")
        r = self.view(token)
        j = r.json()
        self.assertEqual(j["profile"], "Robin Example (fictional)")
        self.assertEqual([s["id"] for s in j["sections"]], ["visits", "results", "procedures"])
        for k in ("medications", "costs", "journal", "appointments", "events", "details", "labs", "imports", "documents"):
            self.assertNotIn(k, j)
        self.assertEqual([v["title"] for v in j["visits"]], ["Annual physical"])
        self.assertEqual(j["visits"][0]["sub"], "Dr. Fictional Example")
        self.assertEqual(sorted(p["title"] for p in j["procedures"]), ["Chest X-ray", "Fictional colonoscopy"])
        self.assertEqual([s["name"] for s in j["results"]], ["Hemoglobin"])
        pts = j["results"][0]["results"]
        self.assertEqual(len(pts), 1, "only the result inside the range")
        self.assertEqual((pts[0]["value"], pts[0]["range"], pts[0]["flag"]), ("11.1", "12–16", "Low"))
        for word in ("OLDVISIT", "HIDDENVISIT", "SodiumSECRET", "REPORTTEXT", "DETAILNOTE", "CLAIMTITLE", "Examplecillin",
                     "JOURNALNOTE", "Follow-up", "OTHERPROFILE", "Dr. Rivera", token):
            self.assertNotIn(word, r.text)

    def test_every_section(self):
        token, _ = self.make(sections=list(share_links.SECTION_IDS), **{"from": D(-100)})
        j = self.view(token).json()
        self.assertEqual(j["medications"], [{"name": "Examplecillin 250 mg", "instructions": "Take one daily", "since": D(-90),
                                             "until": None, "status": "active"}])
        self.assertEqual([a["title"] for a in j["appointments"]], ["Follow-up visit"], "only appointments from today on")
        self.assertEqual(j["journal"][0]["note"], "JOURNALNOTE after lunch")
        self.assertNotIn("by", j["journal"][0], "not who wrote it")
        self.assertEqual(j["costs"][0]["owe"], 50.0)
        self.assertEqual(sorted(s["name"] for s in j["results"]), ["Hemoglobin", "SodiumSECRET"], "no labs picked: every lab")
        self.assertNotIn("REPORTTEXT", json.dumps(j))
        self.assertNotIn("DETAILNOTE", json.dumps(j))

    def test_no_end_date_and_no_start(self):
        token, _ = self.make(sections=["visits"])
        self.assertEqual(sorted(v["title"] for v in self.view(token).json()["visits"]), ["Annual physical", "Long-ago visit OLDVISIT"])

    # ---------------------------------------------------------------- the token and what's stored

    def test_only_the_hash_is_stored_and_the_rest_is_encrypted(self):
        token, link = self.make(label="Dr. Rivera", sections=["results"], labs=["Hemoglobin"])
        db = dbm.connect()
        try:
            row = dict(db.execute("SELECT * FROM share_links WHERE id=?", (link["id"],)).fetchone())
            dump = json.dumps(row)
            self.assertNotIn(token, dump)
            self.assertNotIn("Rivera", dump)
            self.assertNotIn("Hemoglobin", dump)
            self.assertTrue(row["label"].startswith(crypto.FIELD) and row["scope"].startswith(crypto.FIELD))
            self.assertEqual(len(row["token_hash"]), 64)
            self.assertGreaterEqual(len(token), 43, "256 bits")
        finally:
            db.close()
        listed = self.c["robin"].get(f"/api/profiles/{self.pid}/links").json()
        self.assertNotIn(token, json.dumps(listed))
        self.assertIn("Hemoglobin", listed["labs"])
        self.assertEqual([s["id"] for s in listed["sections"]], [s for s in share_links.SECTION_IDS if s not in measures.IDS and s != "journal_photos"],
                         "every section, but a measure, or the journal's photos (#161), only when the profile has some (#225)")

    def test_no_cookie_is_set_and_none_is_read(self):
        token, _ = self.make()
        r = self.view(token)
        self.assertNotIn("set-cookie", r.headers)
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertEqual(r.headers["referrer-policy"], "no-referrer")
        # a signed-in browser opening it is still just a holder of the link: the view is logged as the link, not them
        self.view(token, c=self.c["kim"])
        log = self.c["robin"].get(f"/api/profiles/{self.pid}/activity?action=link").json()
        self.assertEqual(log[0]["actor"], "share link")
        self.assertIsNone(log[0]["actor_id"])
        self.assertFalse(log[0]["actor_removed"])

    # ---------------------------------------------------------------- refusals: always the same bare 404

    def test_wrong_malformed_and_missing_tokens(self):
        token, _ = self.make()
        for bad in [token[:-1] + ("A" if token[-1] != "A" else "B"), "x" * 43, "short", "x" * 200, "../../etc", ""]:
            self.assert_bare(self.view(bad, 404))
        share_links.throttle._fails.clear()
        for body in [b"not json", b"[1, 2]", b'{"token": 5}', b"{}", b'{"token": "' + b"a" * 2000 + b'"}']:
            r = client().post("/api/share/view", content=body, headers={**H, "Content-Type": "application/json"})
            self.assert_bare(r)
        self.assertEqual(client().get("/api/share/view").status_code, 405)
        self.assertEqual(client().post("/api/share/view", json={"token": token}).status_code, 403, "the cross-site check applies")

    def test_revoked_expired_and_maker_no_longer_owner(self):
        token, link = self.make(who="ash")
        self.view(token)
        r = self.c["robin"].delete(f"/api/profiles/{self.pid}/links/{link['id']}", headers=H)   # any owner can revoke
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["link"]["state"], "revoked")
        self.assertEqual(r.json()["link"]["revoked_by"], "robin")
        self.assert_bare(self.view(token, 404))
        self.assertEqual(self.c["robin"].delete(f"/api/profiles/{self.pid}/links/{link['id']}", headers=H).status_code, 404)

        token, link = self.make()
        db = dbm.connect()
        try:
            db.execute("UPDATE share_links SET expires=? WHERE id=?", (dbm.now() - 1, link["id"]))
            self.assert_bare(self.view(token, 404))
            states = {l["id"]: l["state"] for l in self.c["robin"].get(f"/api/profiles/{self.pid}/links").json()["links"]}
            self.assertEqual(states[link["id"]], "expired")

            token, link = self.make(who="ash")
            db.execute("UPDATE memberships SET role='editor' WHERE user_id=? AND profile_id=?", (self.uid["ash"], self.pid))
            self.assert_bare(self.view(token, 404))
            db.execute("UPDATE memberships SET role='owner' WHERE user_id=? AND profile_id=?", (self.uid["ash"], self.pid))
            self.view(token)
            db.execute("UPDATE users SET disabled=1 WHERE id=?", (self.uid["ash"],))
            self.assert_bare(self.view(token, 404))
            db.execute("UPDATE users SET disabled=0 WHERE id=?", (self.uid["ash"],))
            self.view(token)
        finally:
            db.close()

    def test_throttled(self):
        for _ in range(share_links.ShareThrottle.FREE["bad"]):
            self.view("y" * 43, 404)
        r = self.view("y" * 43, 429)
        self.assertIn("retry-after", r.headers)
        token, _ = self.make()
        self.view(token)   # a working link still opens from that address (#203: it may be a shared proxy's)
        share_links.throttle._fails.clear()
        for _ in range(share_links.ShareThrottle.FREE["link"]):
            self.view(token)
        self.view(token, 429)

    # ---------------------------------------------------------------- logging

    def test_every_view_is_logged_without_the_token(self):
        token, link = self.make(label="For Mum")
        for _ in range(3):
            self.view(token)
        log = self.c["robin"].get(f"/api/profiles/{self.pid}/activity?action=link").json()
        views = [e for e in log if e["detail"].get("change") == "view" and e["detail"].get("link") == link["id"]]
        self.assertEqual(len(views), 3)
        self.assertEqual(views[0]["detail"]["label"], "For Mum")
        self.assertTrue(views[0]["ip"])
        made = [e for e in log if e["detail"].get("change") == "create" and e["detail"].get("link") == link["id"]]
        self.assertEqual(made[0]["actor"], "robin")
        self.assertNotIn(token, json.dumps(log))
        listed = {l["id"]: l for l in self.c["robin"].get(f"/api/profiles/{self.pid}/links").json()["links"]}
        self.assertEqual(listed[link["id"]]["views"], 3)
        self.assertIsNotNone(listed[link["id"]]["last_viewed"])

    def test_the_security_log_never_holds_a_token(self):
        seen = []
        h = logging.Handler()
        h.emit = lambda rec: seen.append(rec.getMessage())
        lg = logging.getLogger("sanguity.security")
        lg.addHandler(h)
        old = lg.level
        lg.setLevel(logging.INFO)
        try:
            token, _ = self.make()
            self.view(token)
            self.view("z" * 43, 404)
        finally:
            lg.removeHandler(h)
            lg.setLevel(old)
        self.assertTrue(any("share.refused" in m for m in seen))
        self.assertFalse(any(token in m or "z" * 43 in m for m in seen))

    # ---------------------------------------------------------------- who manages links, and what they may ask for

    def test_only_owners_manage_links(self):
        token, link = self.make()
        for who, status in (("kim", 403), ("lee", 403), ("zed", 404)):
            self.assertEqual(self.c[who].get(f"/api/profiles/{self.pid}/links").status_code, status, who)
            self.make(who=who, status=status)
            self.assertEqual(self.c[who].delete(f"/api/profiles/{self.pid}/links/{link['id']}", headers=H).status_code, status, who)
        self.assertEqual(client().get(f"/api/profiles/{self.pid}/links").status_code, 401)
        # a link of one profile can't be revoked through another
        self.assertEqual(self.c["robin"].delete(f"/api/profiles/{self.other}/links/{link['id']}", headers=H).status_code, 404)
        self.view(token)

    def test_checks_what_it_is_asked_for(self):
        for body in ({"sections": []}, {"sections": ["everything"]}, {"sections": ["visits"], "labs": ["Hemoglobin"]},
                     {"from": D(0), "to": D(-5)}, {"from": "2026-02-30"}, {"from": "yesterday"}, {"hours": 0},
                     {"hours": share_links.MAX_HOURS + 1}, {"label": "x" * 81}):
            self.make(status=400, **body)
        self.make(status=422, hours="a week")
        token, link = self.make(hours=1)
        self.assertLessEqual(link["expires"] - link["created"], 3600)

    def test_at_most_so_many_working_links(self):
        db = dbm.connect()
        try:
            db.execute("UPDATE share_links SET revoked=? WHERE profile_id=?", (dbm.now(), self.other))
        finally:
            db.close()
        for _ in range(share_links.MAX_ACTIVE):
            self.make(pid=self.other)
        r = self.make(pid=self.other, status=400)
        self.assertIn("Revoke one first", r.json()["detail"])
        db = dbm.connect()
        try:
            db.execute("UPDATE share_links SET revoked=? WHERE profile_id=?", (dbm.now(), self.other))
        finally:
            db.close()
        self.make(pid=self.other)

    def test_ended_links_are_pruned_after_a_while(self):
        token, link = self.make()
        db = dbm.connect()
        try:
            db.execute("UPDATE share_links SET revoked=? WHERE id=?", (dbm.now() - (share_links.KEEP_ENDED_DAYS + 1) * 86400, link["id"]))
            self.make()   # (its row id may be the pruned one's again)
            from server import security as sec
            self.assertIsNone(db.execute("SELECT 1 FROM share_links WHERE token_hash=?", (sec.token_hash(token),)).fetchone())
        finally:
            db.close()

    def test_deleting_the_profile_deletes_its_links(self):
        db = dbm.connect()
        try:
            pid = prof.create_profile(db, "Short-lived (fictional)", owner_id=self.uid["robin"])
        finally:
            db.close()
        token, _ = self.make(pid=pid)
        r = self.c["robin"].request("DELETE", f"/api/profiles/{pid}", json={"confirm_name": "Short-lived (fictional)"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assert_bare(self.view(token, 404))
        db = dbm.connect()
        try:
            self.assertIsNone(db.execute("SELECT 1 FROM share_links WHERE profile_id=?", (pid,)).fetchone())
        finally:
            db.close()

    # ---------------------------------------------------------------- the page and the web server

    # ---------------------------------------------------------------- the phone's and watch's measures (#225)

    @classmethod
    def phone_profile(cls):
        if getattr(cls, "ppid", None):
            return cls.ppid
        path = os.path.join(TMP, "phone", "daily.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        crypto.write_file(path, json.dumps(PHONE).encode())
        db = dbm.connect()
        cls.ppid = prof.create_profile(db, "Sam Example (fictional)", owner_id=cls.uid["robin"], watch_file=path)
        with dbm.transaction(db):
            records.store(db, cls.ppid, {"events": [{"id": 1, "t": D(-200), "k": "immunization", "title": "Hepatitis B vaccine FICTIONAL"}],
                                         "labs": [], "details": {"1": {"dose": "1 mL"}}}, "dataset")
        db.close()
        return cls.ppid

    def test_measures_offered_when_present_and_only_when_ticked(self):
        pid = self.phone_profile()
        offered = [s["id"] for s in self.c["robin"].get(f"/api/profiles/{pid}/links").json()["sections"]]
        self.assertEqual([s for s in offered if s in measures.IDS], ["home_bp", "glucose", "temperature", "oxygen", "immunizations", "ecg"])
        self.assertNotIn("cycle", share_links.SECTION_IDS, "cycle tracking is never offered for a link (#183)")
        token, _ = self.make(pid=pid, sections=["visits", "results", "medications", "about"])   # the form's usual ones: none ticked
        text = self.view(token).text
        for word in ("Fictional cuff", "132/84", "Sinus", "Hepatitis", "glucose", "SpO2", "home_bp"):
            self.assertNotIn(word, text)

    def test_measures_in_a_link_and_its_range(self):
        pid = self.phone_profile()
        token, link = self.make(pid=pid, sections=["home_bp", "glucose", "temperature", "oxygen", "immunizations", "ecg"],
                                **{"from": D(-20), "to": D(-3)})
        self.assertEqual(link["sections"], ["home_bp", "glucose", "temperature", "oxygen", "immunizations", "ecg"])
        j = self.view(token).json()
        self.assertEqual([s["id"] for s in j["sections"]], link["sections"])
        self.assertTrue(all(s["what"] for s in j["sections"]))
        self.assertEqual(j["home_bp"]["rows"], [[D(-9) + "T08:00", "132/84", "68", "Fictional cuff (Health Connect)"]],
                         "only the readings in the range: not the one 40 days ago, nor the one 2 days ago")
        self.assertEqual(j["home_bp"]["lead"], ["1 reading · average 132/84 mmHg · highest systolic 132, highest diastolic 84"])
        self.assertEqual([r[0] for r in j["glucose"]["rows"]], [D(-4), D(-5)])
        self.assertIn("standard published ranges", j["glucose"]["note"])
        self.assertEqual(j["temperature"]["rows"][0][1], "37.4", "°C: the units of the owner who made it")
        self.assertEqual(j["oxygen"]["rows"], [[D(-10), "97.2", ""]])
        self.assertEqual(j["immunizations"]["rows"], [], "the vaccine was given before the range")
        self.assertEqual(j["ecg"]["rows"], [[D(-6), "ECG: Sinus Rhythm", "72 bpm average"]])
        for k in ("metrics", "bp_readings", "ecg_waves", "samples", "tz", "last_payload"):
            self.assertNotIn(f'"{k}"', json.dumps(j))
        token, _ = self.make(pid=pid, sections=["immunizations"])
        self.assertEqual(self.view(token).json()["immunizations"]["rows"], [[D(-200), "Hepatitis B vaccine FICTIONAL", "1 mL", ""]])

    def test_targets_only_when_ticked(self):   # #234
        pid = self.phone_profile()
        offered = lambda: [s["id"] for s in self.c["robin"].get(f"/api/profiles/{pid}/links").json()["sections"]]
        self.assertNotIn("targets", offered(), "none entered: not offered")
        put = lambda t: self.c["robin"].put(f"/api/profiles/{pid}/person", json={"targets": t}, headers=H)
        self.assertEqual(put({"bp": {"sysHigh": 130, "diaHigh": 80, "by": "Dr. Fictional Example"}}).status_code, 200)
        try:
            self.assertIn("targets", offered())
            token, _ = self.make(pid=pid, sections=["home_bp"], **{"from": D(-20), "to": D(-3)})
            text = self.view(token).text
            self.assertNotIn("Dr. Fictional Example", text, "not ticked: not shown")
            self.assertNotIn("targets", self.view(token).json())
            token, _ = self.make(pid=pid, sections=["home_bp", "targets"], **{"from": D(-20), "to": D(-3)})
            j = self.view(token).json()
            self.assertEqual(j["targets"]["rows"], [["Blood pressure", "up to 130/80 mmHg", "Dr. Fictional Example", "", "1 of 1 reading above it"]])
            self.assertIn("Sanguity doesn't suggest targets", j["targets"]["lead"][0])
        finally:
            put({"bp": None})

    def test_typed_glucose_and_oxygen_in_a_link(self):
        """#232: glucose and blood oxygen typed in at home are in a link's sections, in its dates and in the units of the
        owner who made it: glucose each with its time and meal note (one the phone sent too, once), oxygen one by one."""
        path = os.path.join(TMP, "typed", "daily.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        crypto.write_file(path, json.dumps(TYPED).encode())
        db = dbm.connect()
        pid = prof.create_profile(db, "Alex Example (fictional)", owner_id=self.uid["robin"], watch_file=path)
        db.close()
        offered = [s["id"] for s in self.c["robin"].get(f"/api/profiles/{pid}/links").json()["sections"]]
        self.assertEqual([s for s in offered if s in measures.IDS], ["glucose", "oxygen"])
        token, _ = self.make(pid=pid, sections=["glucose", "oxygen"], **{"from": D(-20), "to": D(-3)})
        j = self.view(token).json()
        g, o = j["glucose"], j["oxygen"]
        self.assertEqual(g["rows"], [[D(-5), "Typed in, 07:45, before a meal", "104", "104"], [D(-6), "Meter: 1", "99", "99"]],
                         "in the range only; the reading the phone sent too, once")
        self.assertEqual(g["lead"], ["2 meter readings (1 typed in) · average 102 mg/dL · lowest 99, highest 104"])
        self.assertIn("typed in", g["note"])
        self.assertEqual(o["rows"], [[D(-4), "Typed in, 08:05", "97", ""], [D(-8), "Watch, the day's average", "96.5", "93"]])
        self.assertEqual(o["lead"], ["1 day · average 96.5% · lowest 93%", "1 reading typed in · average 97%"])
        self.assertIn("typed in at home", o["note"])
        self.assertNotIn("glucose_readings", json.dumps(j))
        c = self.c["robin"]
        try:   # the owner's units (#41) at the time of viewing
            c.put("/api/settings/user/units", json={"value": {"lab": "si"}}, headers=H)
            g = self.view(token).json()["glucose"]
            self.assertEqual(g["cols"][2][0], "Low–high (mmol/L)")
            self.assertEqual(g["rows"][0][1:], ["Typed in, 07:45, before a meal", "5.8", "5.8"])
        finally:
            c.put("/api/settings/user/units", json={"value": {}}, headers=H)

    def test_page_and_nginx_routes(self):
        html = read("www", "share.html")
        self.assertIn('<script src="js/share.js"></script>', html)
        self.assertIn('<script src="js/dates.js"></script>', html)
        self.assertNotRegex(html, r"<script>(?!</script>)", "no inline script (CSP)")
        js = read("www", "js", "share.js")
        self.assertIn('credentials: "omit"', js)
        self.assertNotIn("innerHTML", js)
        self.assertIn("location.hash", js)
        conf = read("nginx.conf")
        self.assertIn("location = /share.html { }", conf)
        block = re.search(r"location = /api/share/view \{(.*?)\n    \}", conf, re.S).group(1)
        self.assertIn('proxy_set_header Cookie "";', block)
        self.assertIn("limit_except POST", block)
        self.assertIn("client_max_body_size 1k;", block)
        self.assertNotIn("add_header Content-Security-Policy", block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
