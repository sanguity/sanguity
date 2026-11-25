#!/usr/bin/env python3
"""Ids that never come back (#588). A row's id that a page, a file or another table holds is never given to another row
once it's deleted: a deleted profile's phone data isn't in the next profile made (its receiver slot is deleted too,
and an id with a slot left on disk is skipped), a page loaded before an undo can't edit the record an import brought
since, a stale page's save or delete of a journal entry, a dose, a reminder or an edit is refused (404) instead of
changing the one made after, and a sign-in after another signs out has its views logged. And the upgrade to schema
version 39, which makes those tables again with AUTOINCREMENT, keeps every row and key. And what the receiver keeps
(#593): a slot nothing names goes after a grace period, a restore's folder after its time, and an upload whose secret
went while it was written isn't kept. Fictional data only.

  python3 tests/test_ids.py
"""
import importlib.util, json, os, shutil, sqlite3, sys, tempfile, threading, time, unittest, urllib.error, urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-ids-test-")
RX = os.path.join(TMP, "rx")   # the receiver's DATA, laid out as compose mounts it for the app
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "build"),
                  SANGUITY_WATCH_FILE=os.path.join(RX, "public", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  SANGUITY_WATCH_KEYS=os.path.join(RX, "keys", "receiver-secrets.json"), SANGUITY_WATCH_STORE=RX,
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_CORRECTIONS=os.path.join(TMP, "config", "corrections.json"))
for d in ("records", "keys", "build", "config", "profiles", "rx/keys", "rx/public"):
    os.makedirs(os.path.join(TMP, d), exist_ok=True)
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, activity, config, crypto, db as dbm, main, med_log, profiles as prof, watch_keys   # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
spec = importlib.util.spec_from_file_location("watch_receiver", os.path.join(ROOT, "watch", "receiver.py"))
rx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rx)
rx.DATA, rx.KEYS_FILE, rx.SECRET_FILE = RX, os.environ["SANGUITY_WATCH_KEYS"], os.path.join(TMP, "no-legacy-secret")

H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites", "lee": "maple tunnel brisk saddle"}
BUILT = {
    "built": "2026-11-01T00:00:00+00:00", "source": "fictional",
    "events": [
        {"id": 1, "t": "2026-02-10T09:15:00", "k": "visit", "title": "Outpatient visit", "sub": "Dr. Fictional"},
        {"id": 2, "t": "2026-03-01T08:00:00", "k": "med", "title": "Examplomab 10 mg", "sub": "Active"},
    ],
    "details": {"2": {"type": "med", "instructions": "Once daily", "status": "Active", "src": "Fictional clinic"}},
    "labs": [], "upcoming": [], "problems": [], "milestones": [], "costs": [],
}


def client():
    return TestClient(main.app, base_url="http://testserver")


def steps(day, n):
    return {"metrics": [{"key": "steps", "pts": [[day, n]]}]}


class Ids(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(TMP, "build", "data.json"), "w", encoding="utf-8") as f:
            json.dump(BUILT, f)
        cls.ctx = client().__enter__()
        db = dbm.connect()
        cls.uids = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        cls.pid = db.execute("SELECT id FROM profiles WHERE linked_build=1").fetchone()[0]
        for who, role in (("robin", "owner"), ("lee", "editor"), ("kim", "viewer")):
            db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, ?)", (cls.uids[who], cls.pid, role))
        db.close()
        cls.robin = cls.login("robin")

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

    def ok(self, r, status=200):
        self.assertEqual(r.status_code, status, r.text)
        return r.json() if r.content else None

    def keys_doc(self):
        return json.loads(crypto.read_file(config.WATCH_KEYS))

    # ---------------------------------------------------------------- profiles and their phone data
    def new_profile(self, name):
        return self.ok(self.robin.post("/api/profiles", json={"name": name}, headers=H))["id"]

    def phone(self, pid, day, n):
        """Set up the profile's phone (its receiver secret) and have "the receiver" write what it sent: its slot's
        database and archive, and the summary the app reads."""
        self.ok(self.robin.post(f"/api/profiles/{pid}/watch/receiver/rotate", headers=H))
        db = dbm.connect()
        slot = watch_keys._slot_for(db, pid)
        db.close()
        for d in (rx.slot_dir(slot), os.path.join(rx.slot_dir(slot), "raw", "2026", "11", "10")):
            os.makedirs(d, exist_ok=True)
        crypto.write_file(os.path.join(rx.slot_dir(slot), "watch.db"), b"fictional")
        crypto.write_file(os.path.join(rx.slot_dir(slot), "raw", "2026", "11", "10", "080000000000-ab.json.gz"), b"fictional")
        os.makedirs(os.path.join(os.path.dirname(rx.summary_file(slot)), "ecg"), exist_ok=True)
        crypto.write_file(rx.summary_file(slot), json.dumps(steps(day, n)).encode())
        return slot

    def watch(self, pid):
        r = self.robin.get(f"/api/profiles/{pid}/watch")
        return None if r.status_code == 204 else self.ok(r)

    def delete(self, pid, name):
        self.ok(self.robin.request("DELETE", f"/api/profiles/{pid}", json={"confirm_name": name}, headers=H))

    def test_a_deleted_profiles_phone_data_never_reaches_the_next_profile(self):
        alex = self.new_profile("Alex")
        slot = self.phone(alex, "2026-11-10", 11111)
        self.assertEqual(slot, str(alex))
        self.assertEqual(self.watch(alex)["metrics"][0]["pts"], [["2026-11-10", 11111]])
        self.delete(alex, "Alex")
        doc = self.keys_doc()
        self.assertNotIn(slot, [p["slot"] for p in doc["profiles"]], "its secret stops working")
        self.assertIn(slot, doc["deleted"], "and the receiver is asked to delete what it kept")

        rx.slot_sweep({})   # the receiver does it
        self.assertFalse(os.path.exists(rx.slot_dir(slot)), "its database and archived uploads")
        self.assertFalse(os.path.exists(os.path.dirname(rx.summary_file(slot))), "its summary, ECG samples and cycle file")
        self.assertEqual([n for n in os.listdir(os.path.join(RX, "profiles")) if n.startswith(".deleting")], [])

        other = self.new_profile("Bob's own")
        self.assertGreater(other, alex, "a new id")
        self.assertIsNone(self.watch(other), "and no phone data")
        self.assertNotEqual(self.phone(other, "2026-11-11", 222), slot)
        self.assertEqual(self.watch(other)["metrics"][0]["pts"], [["2026-11-11", 222]], "only its own phone's")
        rx.slot_sweep({})   # a slot in use is never deleted
        self.assertTrue(os.path.exists(rx.summary_file(str(other))))

    def test_an_id_with_a_slot_left_on_disk_is_skipped(self):
        # profiles deleted before this change left their receiver slot (and a profile folder) behind
        db = dbm.connect()
        top = db.execute("SELECT seq FROM sqlite_sequence WHERE name='profiles'").fetchone()[0]
        db.close()
        os.makedirs(os.path.join(RX, "profiles", str(top + 3)))
        os.makedirs(os.path.join(RX, "public", "profiles", str(top + 5)))
        os.makedirs(os.path.join(config.DATA_DIR, "profiles", f".deleting-{top + 7}"))
        pid = self.new_profile("Casey")
        self.assertEqual(pid, top + 8)
        self.assertIsNone(self.watch(pid))
        self.delete(pid, "Casey")

    def test_the_first_profiles_slot(self):
        # the profile reading the receiver's own summary (slot ""), from before #46: deleting it retires the
        # install's single secret and asks for slot "" to be deleted, once; not while another profile reads it
        db = dbm.connect()
        db.execute("UPDATE profiles SET watch_file=NULL WHERE id=?", (self.pid,))   # (the setup build's profile reads it too)
        first = prof.create_profile(db, "First", owner_id=self.uids["robin"], watch_file=config.WATCH_FILE)
        second = prof.create_profile(db, "Second", owner_id=self.uids["robin"], watch_file=config.WATCH_FILE)
        db.execute("DELETE FROM meta WHERE k=?", (watch_keys.RETIRED,))
        self.assertEqual(watch_keys.first_profile(db), first)
        db.close()
        crypto.write_file(rx.summary_file(""), json.dumps(steps("2026-11-12", 3333)).encode())
        crypto.write_file(os.path.join(RX, "watch.db"), b"fictional")
        try:
            self.assertEqual(self.watch(first)["metrics"][0]["pts"], [["2026-11-12", 3333]])
            self.delete(first, "First")
            self.assertNotIn("", self.keys_doc().get("deleted") or {}, "the second profile still reads it")
            self.assertTrue(self.keys_doc()["legacy"])
            self.delete(second, "Second")
            doc = self.keys_doc()
            self.assertIn("", doc["deleted"])
            self.assertFalse(doc["legacy"], "the install's single secret stops working")
            rx.slot_sweep({})
            self.assertFalse(os.path.exists(rx.summary_file("")))
            self.assertFalse(os.path.exists(os.path.join(RX, "watch.db")))
            self.assertTrue(os.path.isdir(os.path.join(RX, "keys")), "the keys folder stays")
            # an upload checked just before the secret went, written after the deletion (#593): deleted too, as no
            # profile reads slot "" and no secret takes it
            rx.archive(b'{"data": {"metrics": []}}', "")
            crypto.write_file(rx.summary_file(""), b"{}")
            rx.slot_sweep({})
            self.assertEqual(self.kept(""), [])
            db = dbm.connect()   # written since a profile reads it again: kept
            db.execute("UPDATE profiles SET watch_file=? WHERE id=?", (config.WATCH_FILE, self.pid))
            watch_keys.publish(db)
            db.close()
            crypto.write_file(rx.summary_file(""), b"{}")
            rx.slot_sweep({})
            self.assertTrue(os.path.exists(rx.summary_file("")))
        finally:
            for f in (rx.summary_file(""), os.path.join(RX, "watch.db")):
                if os.path.exists(f):
                    os.remove(f)
            shutil.rmtree(os.path.join(RX, "raw"), True)
            db = dbm.connect()
            db.execute("UPDATE profiles SET watch_file=? WHERE id=?", (config.WATCH_FILE, self.pid))
            db.close()

    # ---------------------------------------------------------------- what the receiver keeps, and how long (#593)
    def kept(self, slot):
        return [p for p in rx.slot_paths(slot) if os.path.lexists(p)]

    def leftover(self, slot):
        """A slot as a profile deleted before #588 left it: its database, an archived upload and its summary."""
        raw = os.path.join(rx.slot_dir(slot), "raw", "2026", "10", "01")
        os.makedirs(raw, exist_ok=True)
        crypto.write_file(os.path.join(rx.slot_dir(slot), "watch.db"), b"fictional")
        crypto.write_file(os.path.join(raw, "080000000000-cd.json.gz"), b"fictional")
        os.makedirs(os.path.dirname(rx.summary_file(slot)), exist_ok=True)
        crypto.write_file(rx.summary_file(slot), json.dumps(steps("2026-10-01", 4444)).encode())

    def keys_file(self, doc):
        crypto.write_file(config.WATCH_KEYS, json.dumps(doc).encode())
        os.utime(config.WATCH_KEYS, ns=(time.time_ns(), time.time_ns()))   # the receiver sees it changed

    def test_a_slot_no_profile_or_secret_names_is_deleted_at_start_after_the_grace_period(self):
        db = dbm.connect()
        top = db.execute("SELECT seq FROM sqlite_sequence WHERE name='profiles'").fetchone()[0]
        db.close()
        gone, back = str(top + 50), str(top + 51)   # profiles deleted before #588: nothing names them
        self.leftover(gone)
        self.leftover(back)
        quiet = self.new_profile("Quinn")   # a profile without a phone, whose slot a restore brought back
        self.leftover(str(quiet))
        phoned = self.new_profile("Pat")
        slot = self.phone(phoned, "2026-11-13", 5)
        doc = self.keys_doc()
        self.assertIn(str(quiet), doc["slots"], "the app says which slots its profiles read")
        self.assertNotIn(gone, doc["slots"])
        try:
            old = {k: v for k, v in doc.items() if k != "slots"}   # an app from before this: nothing is taken
            self.keys_file(old)
            rx.start_sweep(now=time.time() + 10 * rx.SLOT_GRACE)
            self.assertTrue(self.kept(gone))
            self.assertFalse(os.path.exists(os.path.join(RX, rx.ORPHANS)))

            self.keys_file(doc)
            start = time.time()
            rx.start_sweep(now=start)
            self.assertEqual(len(self.kept(gone)), 2, "not at once")
            self.keys_file({**doc, "slots": doc["slots"] + [back]})   # read again in the meantime (a restore)
            rx.start_sweep(now=start + rx.SLOT_GRACE / 2)
            self.keys_file(doc)
            rx.start_sweep(now=start + rx.SLOT_GRACE - 60)
            self.assertEqual(len(self.kept(gone)), 2, "nor within the grace period")
            rx.start_sweep(now=start + rx.SLOT_GRACE + 60)
            self.assertEqual(self.kept(gone), [], "after it: its database, archived uploads and summary")
            self.assertFalse(os.path.exists(rx.slot_dir(gone)))
            self.assertEqual(len(self.kept(back)), 2, "the grace period starts again once nothing names it again")
            self.assertEqual(len(self.kept(str(quiet))), 2, "a profile's own slot stays")
            self.assertEqual(len(self.kept(slot)), 2, "and one a secret uploads to")
            with open(os.path.join(RX, rx.ORPHANS), encoding="utf-8") as f:
                self.assertEqual(list(json.load(f)), [back])
            rx.start_sweep(now=start + rx.SLOT_GRACE * 2)
            self.assertEqual(self.kept(back), [])
            self.assertEqual([n for d in ("profiles", "public/profiles") for n in os.listdir(os.path.join(RX, d))
                              if n.startswith(".deleting")], [])
        finally:
            for s in (gone, back, str(quiet)):
                shutil.rmtree(rx.slot_dir(s), True)
                shutil.rmtree(os.path.dirname(rx.summary_file(s)), True)
            db = dbm.connect()
            watch_keys.publish(db)
            db.close()
            for pid, name in ((quiet, "Quinn"), (phoned, "Pat")):
                self.delete(pid, name)
            rx.slot_sweep({})

    def test_a_restore_folder_past_its_time_is_deleted(self):
        now = time.time()

        def aside(t, name=None):
            d = os.path.join(RX, name or "before-restore-" + datetime.fromtimestamp(t).strftime("%Y%m%d-%H%M%S"))
            os.makedirs(os.path.join(d, "profiles", "9"))
            crypto.write_file(os.path.join(d, "profiles", "9", "watch.db"), b"fictional")
            os.utime(d, (t, t))
            return d

        old = aside(now - rx.RESTORE_KEEP - 3600)
        again = aside(now - rx.RESTORE_KEEP - 3600, os.path.basename(old) + "-1")   # a second restore that second
        recent = aside(now - 3600)
        other = aside(now - rx.RESTORE_KEEP * 2, "before-restore-notes")   # not one the receiver made: left alone
        try:
            rx.start_sweep(now=now)
            self.assertFalse(os.path.exists(old), "past its time")
            self.assertFalse(os.path.exists(again))
            self.assertTrue(os.path.exists(recent), "not yet")
            self.assertTrue(os.path.exists(other))
            rx.start_sweep(now=now + rx.RESTORE_KEEP)
            self.assertFalse(os.path.exists(recent))
            self.assertEqual([n for n in os.listdir(RX) if n.startswith(".deleting")], [])
        finally:
            for d in (old, again, recent, other):
                shutil.rmtree(d, True)

    def test_an_upload_checked_before_its_secret_went_is_not_kept(self):
        pid = self.new_profile("Rory")
        secret = self.ok(self.robin.post(f"/api/profiles/{pid}/watch/receiver/rotate", headers=H))["secret"]
        slot = str(pid)
        doc = self.keys_doc()
        http = rx.ThreadingHTTPServer(("127.0.0.1", 0), rx.H)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        real = rx._archive

        def racing(body, s):   # the profile deleted between the secret's check and the write
            self.keys_file({**doc, "profiles": [p for p in doc["profiles"] if p["slot"] != slot]})
            return real(body, s)

        rx._archive = racing
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{http.server_address[1]}/ingest", method="POST",
                                         data=b'{"data": {"metrics": []}}',
                                         headers={"Authorization": "Bearer " + secret, "Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(req, timeout=10)
            self.assertEqual(e.exception.code, 401)
            self.assertEqual(rx.raw_files(slot), [], "not archived")
            self.assertTrue(rx.work.empty(), "nor folded in")
            stores = {}   # and an upload already queued for it isn't folded in either
            rx.fold(stores, (slot, b'{"data": {"metrics": []}}', "0" * 64))
            self.assertNotIn(slot, stores)
        finally:
            rx._archive = real
            http.shutdown()
            http.server_close()
            shutil.rmtree(rx.slot_dir(slot), True)
            db = dbm.connect()
            watch_keys.publish(db)
            db.close()
            self.delete(pid, "Rory")
            rx.slot_sweep({})

    # ---------------------------------------------------------------- events
    def typed(self, title, date):
        r = self.ok(self.robin.post(f"/api/profiles/{self.pid}/manual", headers=H, json={
            "rows": [], "items": [{"kind": "visit", "date": date, "title": title, "where": "Fictional clinic"}], "save": True}))
        return r["import_id"]

    def events(self):
        return {e["id"]: e for e in self.ok(self.robin.get(f"/api/profiles/{self.pid}/data"))["events"]}

    def test_an_event_id_never_comes_again_after_an_undo(self):
        iid = self.typed("Knee check (typed by mistake)", "2026-11-01")
        seen = self.events()
        mine = max(seen)
        self.assertEqual(seen[mine]["title"], "Knee check (typed by mistake)")
        at = next(e["at"] for e in self.ok(self.robin.get(f"/api/profiles/{self.pid}/imports"))["imports"] if e["id"] == iid)
        self.ok(self.robin.post(f"/api/profiles/{self.pid}/imports/{iid}/undo?at={at}", headers=H))
        self.typed("Annual physical", "2026-11-03")
        now = self.events()
        self.assertNotIn(mine, now, "the undone event's id isn't given to the new one")
        new = max(now)
        self.assertEqual(now[new]["title"], "Annual physical")
        r = self.robin.post(f"/api/profiles/{self.pid}/edits", json={"event": mine, "action": "hide"}, headers=H)
        self.assertEqual(r.status_code, 400, "a page loaded before can't hide it")
        self.assertFalse(self.events()[new].get("hidden"))

    # ---------------------------------------------------------------- what people write in the app
    def test_a_stale_save_or_delete_never_reaches_the_entry_made_after(self):
        base = f"/api/profiles/{self.pid}"
        cases = [
            ("journal", lambda: {"kind": "note", "t": "2026-11-20", "note": "Fictional note one"},
             lambda: {"kind": "symptom", "t": "2026-11-21", "title": "Headache", "severity": 6, "note": "fictional"}),
        ]
        db = dbm.connect()
        med = next(iter(med_log.medications(db, self.pid)))
        db.close()
        cases.append(("medications/doses", lambda: {"med": med, "t": "2026-11-20T08:00", "status": "taken"},
                      lambda: {"med": med, "t": "2026-11-21T08:00", "status": "skipped", "note": "fictional"}))
        cases.append(("medications/reminders", lambda: {"med": med, "times": ["08:00"], "days": ["mo"], "start": "2026-11-01"},
                      lambda: {"med": med, "times": ["21:00"], "days": ["tu"], "start": "2026-11-02"}))
        for path, first, second in cases:
            with self.subTest(path):
                one = self.ok(self.robin.post(f"{base}/{path}", json=first(), headers=H), 201)["id"]
                self.ok(self.robin.request("DELETE", f"{base}/{path}/{one}", headers=H))
                two = self.ok(self.robin.post(f"{base}/{path}", json=second(), headers=H), 201)["id"]
                self.assertGreater(two, one)
                self.assertEqual(self.robin.put(f"{base}/{path}/{one}", json=first(), headers=H).status_code, 404, "a stale save")
                self.assertEqual(self.robin.request("DELETE", f"{base}/{path}/{one}", headers=H).status_code, 404, "a stale delete")
                listed = self.ok(self.robin.get(f"{base}/{path}"))
                rows = {e["id"]: e for e in next(v for k, v in listed.items() if k in ("entries", "doses", "reminders"))}
                self.assertIn(two, rows, "the one made after is still there")
        # an edit (a correction): undone, made again, and a stale undo of the first
        eid = min(self.events())
        one = self.ok(self.robin.post(f"{base}/edits", json={"event": eid, "action": "rename", "title": "Fictional one"}, headers=H), 201)["id"]
        self.ok(self.robin.request("DELETE", f"{base}/edits/{one}", headers=H))
        two = self.ok(self.robin.post(f"{base}/edits", json={"event": eid, "action": "hide"}, headers=H), 201)["id"]
        self.assertGreater(two, one)
        self.assertEqual(self.robin.request("DELETE", f"{base}/edits/{one}", headers=H).status_code, 404)
        self.assertEqual([e["id"] for e in self.ok(self.robin.get(f"{base}/edits"))["edits"]], [two], "the edit made after stays")
        self.assertNotIn(eid, self.events(), "and still hides its event")
        self.ok(self.robin.request("DELETE", f"{base}/edits/{two}", headers=H))

    # ---------------------------------------------------------------- sessions and the activity log
    def views(self):
        db = dbm.connect()
        rows = db.execute("SELECT actor, session_id FROM activity WHERE profile_id=? AND action='view' ORDER BY id", (self.pid,)).fetchall()
        db.close()
        return [tuple(r) for r in rows]

    def test_the_next_sign_in_after_a_sign_out_has_its_views_logged(self):
        kim = self.login("kim")
        self.ok(kim.get(f"/api/profiles/{self.pid}/data"))
        self.ok(kim.post("/api/logout", headers=H))
        before = self.views()
        lee = self.login("lee")
        self.ok(lee.get(f"/api/profiles/{self.pid}/data"))
        added = [v for v in self.views() if v not in before]
        self.assertEqual([a for a, _ in added], ["lee"], "the second person's view is logged")
        self.assertNotIn(added[0][1], [sid for _, sid in before], "under a session id of its own")

    def test_a_view_is_once_per_session_and_person(self):
        db = dbm.connect()
        sid = db.execute("SELECT IFNULL(MAX(id), 0) + 1000 FROM sessions").fetchone()[0]
        kim = {"id": self.uids["kim"], "username": "kim", "sid": sid}
        lee = {"id": self.uids["lee"], "username": "lee", "sid": sid}
        activity.once(db, kim, self.pid, "view", "127.0.0.1")
        activity.once(db, kim, self.pid, "view", "127.0.0.1")
        activity.once(db, lee, self.pid, "view", "127.0.0.1")   # an entry from before, with the same session id
        got = [r[0] for r in db.execute("SELECT actor FROM activity WHERE session_id=? ORDER BY id", (sid,))]
        db.execute("DELETE FROM activity WHERE session_id=?", (sid,))
        db.close()
        self.assertEqual(got, ["kim", "lee"])


# ---------------------------------------------------------------- the upgrade from schema version 38
TABLES = ("users", "profiles", "sessions", "passkeys", "uploads", "jobs", "events", "series", "edits", "labmaps", "smart_clients",
          "connections", "same_results", "calendar_feeds", "journal", "share_links", "journal_photos", "med_reminders", "med_doses",
          "sync_skipped")
KEPT = TABLES + ("memberships", "imports", "results", "documents", "activity", "upload_files", "devices", "recovery_codes",
                 "settings", "sync_changes", "connect_pending", "summary_drafts", "cost_marks", "import_undos")


class Schema(unittest.TestCase):
    def test_version_39_keeps_every_row_and_key(self):   # #588
        d = tempfile.mkdtemp(prefix="sanguity-schema39-")
        self.addCleanup(shutil.rmtree, d, True)
        db = sqlite3.connect(os.path.join(d, "schema-38.db"), isolation_level=None)
        db.execute("PRAGMA foreign_keys=ON")   # as the app's connections have it
        for v, sql in enumerate(dbm.SCHEMA[:38], start=1):
            db.executescript("BEGIN;" + sql + f"PRAGMA user_version={v};COMMIT;")
        x = db.execute
        x("INSERT INTO users(id, username, pw_hash, created, totp_secret, webauthn_handle) VALUES(3, 'ines', 'h', 1, 't', 'w')")
        x("INSERT INTO users(id, username, pw_hash, created) VALUES(5, 'olu', 'h', 1)")
        x("INSERT INTO profiles(id, name, created, watch_file, records_rev) VALUES(2, 'Fictional', 1, 'w', 4)")
        x("INSERT INTO profiles(id, name, created) VALUES(6, 'Other', 1)")
        x("INSERT INTO memberships(user_id, profile_id, role) VALUES(3, 2, 'owner'), (5, 6, 'viewer')")
        x("INSERT INTO sessions(id, token_hash, user_id, created, last_seen, expires, verified_at) VALUES(4, 't4', 3, 1, 1, 9, 1)")
        x("INSERT INTO devices(token_hash, user_id, created, last_seen) VALUES('d', 3, 1, 1)")
        x("INSERT INTO recovery_codes(user_id, code_hash) VALUES(3, 'c')")
        x("INSERT INTO passkeys(id, user_id, credential_id, public_key, rp_id, name, created) VALUES(2, 3, 'cr', x'00', 'r', 'n', 1)")
        x("INSERT INTO imports(id, profile_id, at, origin, format) VALUES(7, 2, 1, 'upload', 'csv')")
        x("INSERT INTO events(id, profile_id, import_id, body, key_hash) VALUES(40, 2, 7, 'e', 'k')")
        x("INSERT INTO series(id, profile_id, import_id, body) VALUES(8, 2, 7, 's')")
        x("INSERT INTO results(id, profile_id, import_id, series_id, body) VALUES(9, 2, 7, 8, 'r')")
        x("INSERT INTO documents(id, profile_id, path, ext, import_id) VALUES('doc', 2, 'p', 'pdf', 7)")
        x("INSERT INTO uploads(id, profile_id, created, created_by, state) VALUES(11, 2, 1, 3, 'ready')")
        x("INSERT INTO upload_files(upload_id, name, file, path, bytes) VALUES(11, 'n', 'f', 'p', 1)")
        x("INSERT INTO jobs(id, profile_id, upload_id, created_by, state, import_id, created, keep) VALUES(12, 2, 11, 3, 'done', 7, 1, 'k')")
        x("INSERT INTO edits(id, profile_id, target, target_key, action, created_by, at) VALUES(13, 2, 't', 'k', 'hide', 3, 1)")
        x("INSERT INTO labmaps(id, lookup, importer, source, created_name, created, import_id, checked) VALUES(14, 'l', 'csv', 's', 'ines', 1, 7, 1)")
        x("INSERT INTO smart_clients(id, name, vendor, issuer, client_id, auth, scopes, created) VALUES(15, 'n', 'v', 'i', 'c', 'public', 's', 1)")
        x("""INSERT INTO connections(id, profile_id, client_id, source, issuer, fhir_base, state, first_job, created)
             VALUES(16, 2, 15, 'src', 'i', 'f', 'active', 12, 1)""")
        x("UPDATE imports SET connection_id=16 WHERE id=7")
        x("""INSERT INTO connect_pending(state_hash, session_id, profile_id, client_id, connection_id, verifier, redirect_uri, scopes, token_url, expires)
             VALUES('st', 4, 2, 15, 16, 'v', 'r', 's', 't', 9)""")
        x("INSERT INTO sync_changes(import_id, event_id, previous) VALUES(7, 40, 'p')")
        x("INSERT INTO sync_skipped(id, connection_id, profile_id, ref_hash, detail, found, import_id) VALUES(17, 16, 2, 'h', 'd', 1, 7)")
        x("INSERT INTO same_results(id, profile_id, pair, a_key, b_key, same, at) VALUES(18, 2, 'p', 'a', 'b', 1, 1)")
        x("INSERT INTO calendar_feeds(id, profile_id, user_id, token_hash, created) VALUES(19, 2, 3, 'cf', 1)")
        x("INSERT INTO journal(id, profile_id, body, created_by, created_name, created) VALUES(20, 2, 'j', 3, 'ines', 1)")
        x("INSERT INTO journal_photos(id, profile_id, entry_id, file, type, bytes, created) VALUES(21, 2, 20, 'f', 'png', 1, 1)")
        x("INSERT INTO share_links(id, profile_id, created_by, created_name, token_hash, scope, created, expires) VALUES(22, 2, 3, 'ines', 'sl', 's', 1, 9)")
        x("INSERT INTO med_reminders(id, profile_id, body, created_by, created_name, created) VALUES(23, 2, 'r', 3, 'ines', 1)")
        x("INSERT INTO med_doses(id, profile_id, body, created_by, created_name, created) VALUES(24, 2, 'd', 3, 'ines', 1)")
        x("INSERT INTO settings(key, profile_id, value, changed, changed_by) VALUES('k', 2, '1', 1, 3)")
        x("INSERT INTO summary_drafts(profile_id, user_id, sealed, changed) VALUES(2, 3, 's', 1)")
        x("INSERT INTO cost_marks(profile_id, target, target_key, value, created_by, at) VALUES(2, 't', 'k', 'v', 3, 1)")
        x("INSERT INTO import_undos(profile_id, import_id, at, by_id, by_name, entry) VALUES(2, 30, 1, 3, 'ines', 'gone')")
        # a session signed out since (the newest, id 9) that the activity log still names
        x("INSERT INTO activity(at, profile_id, actor_id, actor, action, session_id) VALUES(1, 2, 3, 'ines', 'view', 9)")
        cols = {t: ", ".join(r[1] for r in db.execute(f"PRAGMA table_info({t})")) for t in KEPT}
        before = {t: db.execute(f"SELECT {cols[t]} FROM {t} ORDER BY 1, 2").fetchall() for t in KEPT}
        for t in KEPT:
            self.assertTrue(before[t], f"{t} has a row to keep")
        indexes = {r[0]: r[1] for r in db.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}

        dbm.upgrade(db)
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], len(dbm.SCHEMA))
        self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1, "foreign keys on again")
        self.assertEqual({t: db.execute(f"SELECT {cols[t]} FROM {t} ORDER BY 1, 2").fetchall() for t in KEPT}, before)
        now = {t: ", ".join(r[1] for r in db.execute(f"PRAGMA table_info({t})")) for t in KEPT}
        self.assertEqual({t: now[t][:len(cols[t])] for t in KEPT}, cols, "the same columns (a later version may add its own after them)")
        self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        now = {r[0]: r[1] for r in db.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
        self.assertEqual({k: now.get(k) for k in indexes}, indexes, "every index again")
        schema = "".join(r[0] or "" for r in db.execute("SELECT sql FROM sqlite_master"))
        self.assertNotIn("_39", schema)
        for t in TABLES:
            sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()[0]
            self.assertIn("AUTOINCREMENT", sql, t)
        seq = dict(db.execute("SELECT name, seq FROM sqlite_sequence"))
        self.assertEqual(len([r for r in db.execute("SELECT name FROM sqlite_sequence")]), len(seq), "one row each")
        self.assertEqual({t: seq.get(t) for t in ("users", "profiles", "sessions", "events", "journal", "med_doses")},
                         {"users": 5, "profiles": 6, "sessions": 9, "events": 40, "journal": 20, "med_doses": 24},
                         "after the highest id, a signed-out session the activity log names included")

        # new rows get new ids, deleted ones never come again
        x("DELETE FROM profiles WHERE id=6")
        self.assertEqual(x("INSERT INTO profiles(name, created) VALUES('New', 2)").lastrowid, 7)
        self.assertEqual(x("INSERT INTO sessions(token_hash, user_id, created, last_seen, expires) VALUES('t', 3, 1, 1, 9)").lastrowid, 10)
        x("DELETE FROM journal WHERE id=20")
        self.assertEqual(db.execute("SELECT COUNT(*) FROM journal_photos").fetchone()[0], 0, "references still cascade")
        self.assertEqual(x("INSERT INTO journal(profile_id, body, created_name, created) VALUES(2, 'j', 'ines', 1)").lastrowid, 21)
        x("DELETE FROM users WHERE id=3")   # and set null, from the new tables
        self.assertEqual(db.execute("SELECT COUNT(*) FROM sessions WHERE user_id=3").fetchone()[0], 0)
        self.assertIsNone(db.execute("SELECT created_by FROM uploads WHERE id=11").fetchone()[0])
        x("DELETE FROM profiles WHERE id=2")
        self.assertEqual([db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("events", "series", "results", "jobs", "connections")],
                         [0, 0, 0, 0, 0])
        db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
