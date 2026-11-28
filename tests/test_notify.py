#!/usr/bin/env python3
"""Notifications (#1130, server/notify.py), against a fake ntfy server: off until an admin sets a notification server
and someone turns them on; https only unless the server is on the local network, an http name connected to at the
address checked; the access token write-only (never in an answer), sent as Authorization: Bearer, the environment's
winning (a bad one ignored, never quoted), each sent only to the scheme and host it's for; each person's own topic
(unguessable, made new on request), a profile's notifications only to the topics of the people who turned them on
and can still see it, one each; each person's choice per profile they can see and per kind; new results (a pull's own call is test_connections.py's, in
its incremental pull) and doses due, each sent once (a restart makes none again), a paused line or one with Remind me
off none, in the line's time zone; retried, and logged in Install activity without its words when it never goes
through; listed under Outside connections; and no notification carries anything of the records (every payload's words
checked). Fictional people only; nothing real is contacted.

  python3 tests/test_notify.py
"""
import json, logging, os, socket, sys, tempfile, time, unittest
from datetime import date, datetime, timedelta
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-notify-test-")
ORIGIN = "https://health.example.test"
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "no-build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"), PUBLIC_ORIGINS=ORIGIN)
os.environ.pop("NOTIFY_TOKEN", None)
os.makedirs(os.path.join(TMP, "records"))
os.makedirs(os.path.join(TMP, "keys"))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import (accounts, activity, atrest, crypto, db as dbm, main, med_reminders, notify, profiles as prof,   # noqa: E402
                    records, settings, twofactor)
from tests import netguard   # noqa: E402
from tests.fakentfy import FakeNtfy   # noqa: E402

H = {"X-Sanguity": "1"}
PW = {"ada": "a fictional admin's long passphrase", "kim": "seventeen quiet orange kites", "lee": "maple tunnel brisk saddle"}
TOKEN = "tk_fictional0token0never0shown"
TODAY = date.today()
D = lambda n: (TODAY + timedelta(days=n)).isoformat()
# what the records hold: none of it may reach a notification
MED, LAB = "Fictionamide 10 mg tablet", "Fictional glucose"
RECORDS = {"built": "2026-11-01T00:00:00+00:00", "source": "fictional",
           "events": [{"id": 1, "t": D(-200), "k": "med", "title": MED, "sub": ""},
                      {"id": 2, "t": D(-3), "k": "visit", "title": "Annual physical (fictional)", "sub": "Dr Example"}],
           "details": {"1": {"type": "med", "instructions": "One tablet by mouth, with breakfast"}},
           "labs": [{"id": 3, "name": LAB, "unit": "mg/dL", "pts": [[D(-3), 187]]}], "costs": []}
RECORD_WORDS = ("fictionamide", "10 mg", "tablet", "breakfast", "glucose", "187", "mg/dl", "physical", "dr example",
                "08:00", "dose of", "metformin")
logging.getLogger("sanguity.security").setLevel(logging.ERROR)
logging.getLogger("sanguity.notify").setLevel(logging.ERROR)


def client():
    return TestClient(main.app, base_url="http://testserver")


def setUpModule():
    netguard.install()


def tearDownModule():
    netguard.uninstall()
    netguard.check()


class Notify(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dbm.init()
        db = dbm.connect()
        atrest.check_key(db); atrest.load_pepper(db); prof.bootstrap(db)
        from server import settings_import   # (#1158) what the environment sets (PUBLIC_ORIGINS, above), taken in once
        settings_import.take(db, say=lambda *a: None)
        cls.uid = {n: accounts.create(db, n, is_admin=n == "ada", password=pw)[0] for n, pw in PW.items()}
        db.execute("UPDATE users SET totp_secret=? WHERE id=?", (crypto.enc(twofactor.new_secret()), cls.uid["ada"]))
        cls.pid = prof.create_profile(db, "Ben", owner_id=cls.uid["kim"])
        cls.other = prof.create_profile(db, "Mira (fictional)", owner_id=cls.uid["lee"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (cls.uid["lee"], cls.pid))
        with dbm.transaction(db):
            records.store(db, cls.pid, RECORDS, "dataset")
        cls.med = next(iter(med_reminders.current(db, cls.pid)))
        db.close()
        cls.ntfy = FakeNtfy()
        cls.all_posts = []

    @classmethod
    def tearDownClass(cls):
        cls.ntfy.stop()

    def setUp(self):
        self.ntfy.posts, self.ntfy.status = [], 200
        main.sec.throttle._fails.clear()
        db = dbm.connect()
        for t in ("notify_optins", "notify_sent", "notify_topics", "med_reminders", "outside_contacts"):
            db.execute(f"DELETE FROM {t}")
        db.execute("DELETE FROM meta WHERE k IN (?, ?, ?)", (notify.TOKEN_META, notify.ENV_META, notify.TOPICS_META))
        notify._tested.clear()
        settings.clear(db, ["notify.url"], "install")
        db.close()

    def tearDown(self):
        self.all_posts.extend(self.ntfy.posts)

    # ------------------------------------------------------------ helpers
    def db(self):
        db = dbm.connect()
        self.addCleanup(db.close)
        return db

    def signed_in(self, name):
        c = client()
        r = c.post("/api/login", json={"username": name, "password": PW[name]}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        if r.json().get("two_factor"):
            db = dbm.connect()
            secret = crypto.dec(db.execute("SELECT totp_secret FROM users WHERE username=?", (name,)).fetchone()[0])
            db.execute("UPDATE users SET totp_last_step=0 WHERE username=?", (name,))
            db.close()
            code = twofactor._code(secret, int(time.time() // twofactor.PERIOD))
            r = c.post("/api/login/code", json={"ticket": r.json()["ticket"], "code": code}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
        return c

    def set_server(self, url=None):
        settings.put(self.db(), "notify.url", url or self.ntfy.url)

    def opt_in(self, name, kind, pid=None, ago=0):
        db = self.db()
        notify.choose(db, self.uid[name], pid or self.pid, kind, True)
        if ago:
            db.execute("UPDATE notify_optins SET created=created-? WHERE user_id=?", (ago, self.uid[name]))

    def line(self, times=("08:00",), tz="UTC", on=True, paused=False, asneeded=False):
        db = self.db()
        r = med_reminders.check(db, self.pid, self.med, list(times), list(med_reminders.DAYS), D(-30), None, tz,
                                amounts=[{"amount": 1, "unit": "tablet"} for _ in times], asneeded=asneeded)
        rid = med_reminders.add(db, self.pid, {"id": self.uid["kim"], "username": "kim"}, r, active=on)
        if paused:
            med_reminders.change(db, self.pid, rid, {"id": self.uid["kim"], "username": "kim"}, paused=True)
        return rid

    @staticmethod
    def at(hm, tz="UTC", day=None):
        from zoneinfo import ZoneInfo
        h, m = map(int, hm.split(":"))
        return datetime.combine(day or TODAY, datetime.min.time().replace(hour=h, minute=m), ZoneInfo(tz)).timestamp()

    def bodies(self):
        return [p["body"] for p in self.ntfy.posts]

    def topic(self, name):
        return "/" + notify.topic(self.db(), self.uid[name])

    # ------------------------------------------------------------ off by default
    def test_nothing_is_sent_or_contacted_before_a_server_is_set(self):
        self.assertFalse(notify.configured())
        kim = self.signed_in("kim")
        r = kim.put("/api/me/notify", json={"profile": self.pid, "kind": "results", "on": True}, headers=H)
        self.assertEqual(r.status_code, 409, "no opting in to a server that isn't there")
        self.assertFalse(kim.get("/api/me/notify").json()["set_up"])
        db = self.db()
        self.opt_in("kim", "doses", ago=86400)
        self.line(times=("08:00",))
        self.assertEqual(notify.due_doses(db, now=self.at("08:01")), 0)
        notify.new_results(db, self.pid, 1)
        self.assertEqual(notify.run_queued(db), 0)
        self.assertEqual(self.ntfy.posts, [])
        self.assertNotIn("notifications", kim.get("/api/outside").json())

    def test_https_only_unless_the_server_is_on_the_local_network(self):
        ok = ["https://ntfy.example.test", "http://192.168.1.20", "http://10.0.0.5:8080", "http://ntfy.lan/under",
              "http://ntfy", "http://[fd00::1]", "http://100.101.1.2", "https://ntfy.example.test/ntfy", ""]
        for u in ok:
            self.assertEqual(settings.notify_url(u), u)
        self.assertEqual(settings.notify_url("https://ntfy.example.test/"), "https://ntfy.example.test")
        for u in ["http://ntfy.example.test", "http://8.8.8.8", "ftp://ntfy.lan", "https://user:pw@ntfy.example.test",
                  "https://ntfy.example.test/?x=1", "https://ntfy.example.test/#t"]:
            with self.assertRaises(settings.SettingError, msg=u):
                settings.notify_url(u)
        # a name sent to over http: refused as it's sent unless every address it has is local
        port = self.ntfy.host.split(":")[1]
        self.set_server(f"http://ntfy.lan:{port}")
        db = self.db()
        notify.topic(db, self.uid["kim"])
        with mock.patch("server.net.resolve", return_value=["8.8.4.4"]):
            with self.assertRaisesRegex(notify.SendError, "isn't on the local network"):
                notify.send(db, "test", self.uid["kim"])
        # ...and connected to at the address checked, never looked up again (#1130's review: DNS rebinding): the name
        # resolves once, to this machine; a second lookup would go elsewhere
        answers, real = iter([["127.0.0.1"], ["8.8.4.4"]]), socket.getaddrinfo

        def numeric_only(host, *a, **k):   # (connecting to an address looks it up as itself: no name is looked up)
            if host != "127.0.0.1":
                raise AssertionError(f"looked up {host} again")
            return real(host, *a, **k)
        with mock.patch("server.net.resolve", side_effect=lambda *a: next(answers)), \
                mock.patch("socket.getaddrinfo", side_effect=numeric_only):
            notify.send(db, "test", self.uid["kim"])
        (p,) = self.ntfy.posts
        self.assertEqual((p["headers"]["Host"], p["path"]), (f"ntfy.lan:{port}", self.topic("kim")))

    # ------------------------------------------------------------ the admin's settings
    def test_the_server_its_write_only_token_and_test(self):
        ada = self.signed_in("ada")
        r = ada.put("/api/settings/install/notify.url", json={"value": "http://ntfy.example.test"}, headers=H)
        self.assertEqual(r.status_code, 400)
        self.assertIn("https://", r.json()["detail"])
        r = ada.put("/api/settings/install/notify.url", json={"value": self.ntfy.url}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        r = ada.put("/api/settings/install/notify/token", json={"token": TOKEN}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        for path in ("/api/settings/install/notify", "/api/settings/install", "/api/outside", "/api/activity"):
            got = ada.get(path)
            self.assertNotIn(TOKEN, got.text, path)
        d = ada.get("/api/settings/install/notify").json()
        self.assertEqual((d["token"], d["host"]), ("install", self.ntfy.host))
        r = ada.post("/api/settings/install/notify/test", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        (p,) = self.ntfy.posts
        self.assertEqual(p["path"], self.topic("ada"), "the admin's own topic")
        self.assertRegex(p["path"], r"^/sanguity-[A-Za-z0-9_-]{24}$")
        self.assertEqual(p["headers"]["Authorization"], "Bearer " + TOKEN)
        self.assertEqual(p["headers"]["Title"], "Sanguity")
        self.assertEqual(p["headers"]["Click"], ORIGIN, "tapping opens Sanguity")
        self.assertEqual(p["body"], notify.message("test"))
        # the token is kept for that scheme and host alone: a server elsewhere, or the same host over the other scheme,
        # has none until one is saved for it
        for elsewhere in ("https://ntfy.example.test", "https://" + self.ntfy.host):
            self.set_server(elsewhere)
            self.assertIsNone(notify.token_source(self.db()))
            self.assertNotIn("Authorization", notify.request(self.db(), "test", claim=True)[1])
        self.set_server()
        self.assertEqual(notify.token_source(self.db()), "install")
        # removed; and the environment's wins, and can't be changed here
        self.assertIsNone(ada.put("/api/settings/install/notify/token", json={"token": ""}, headers=H).json()["token"])
        with mock.patch.dict(os.environ, {"NOTIFY_TOKEN": "tk_from_the_environment"}):
            self.assertEqual(ada.get("/api/settings/install/notify").json()["token"], "environment")
            self.assertEqual(ada.put("/api/settings/install/notify/token", json={"token": "x"}, headers=H).status_code, 409)
            self.assertEqual(notify.request(self.db(), "test", claim=True)[1]["Authorization"], "Bearer tk_from_the_environment")
        # a failed test says why, and the admin's actions are in Install activity by name only
        self.ntfy.status = 403
        notify._tested.clear()
        r = ada.post("/api/settings/install/notify/test", headers=H)
        self.assertEqual(r.status_code, 502)
        self.assertIn("refused the access token", r.json()["detail"])
        acts = [e["action"] for e in activity.for_accounts(self.db())]
        for a in ("notify.test", "notify.token", "settings.install"):
            self.assertIn(a, acts)
        # someone who isn't an admin can't
        kim = self.signed_in("kim")
        self.assertEqual(kim.get("/api/settings/install/notify").status_code, 403)
        self.assertEqual(kim.post("/api/settings/install/notify/test", headers=H).status_code, 403)

    def test_the_token_change_needs_the_fresh_check(self):
        ada = self.signed_in("ada")
        self.set_server()
        self.db().execute("UPDATE sessions SET verified_at=0 WHERE user_id=?", (self.uid["ada"],))
        r = ada.put("/api/settings/install/notify/token", json={"token": TOKEN}, headers=H)
        self.assertEqual((r.status_code, r.json()["detail"]), (403, "reauth_required"))
        self.assertIsNone(notify.token_source(self.db()))

    # ------------------------------------------------------------ each person's choices
    def test_each_person_opts_in_per_profile_they_can_see_and_per_kind(self):
        self.set_server()
        kim, lee = self.signed_in("kim"), self.signed_in("lee")
        d = kim.get("/api/me/notify").json()
        self.assertTrue(d["set_up"])
        self.assertEqual(d["profiles"], [{"id": self.pid, "name": "Ben", "results": False, "doses": False}])
        r = kim.put("/api/me/notify", json={"profile": self.pid, "kind": "results", "on": True}, headers=H)
        self.assertEqual(r.json()["profiles"][0], {"id": self.pid, "name": "Ben", "results": True, "doses": False})
        self.assertEqual(kim.put("/api/me/notify", json={"profile": self.other, "kind": "results", "on": True}, headers=H).status_code,
                         404, "not a profile kim can see")
        self.assertEqual(kim.put("/api/me/notify", json={"profile": self.pid, "kind": "labs", "on": True}, headers=H).status_code, 400)
        # a viewer may too, on their own; lee's choice isn't kim's
        r = lee.put("/api/me/notify", json={"profile": self.pid, "kind": "doses", "on": True}, headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertEqual({p["id"]: p for p in r.json()["profiles"]}[self.pid], {"id": self.pid, "name": "Ben", "results": False, "doses": True})
        self.assertFalse(kim.get("/api/me/notify").json()["profiles"][0]["doses"])
        # in the profile's activity, for its owners
        log = [e for e in activity.for_profile(self.db(), self.pid, action="notify")]
        self.assertLessEqual({("kim", "results", True), ("lee", "doses", True)}, {(e["actor"], e["detail"]["kind"], e["detail"]["on"]) for e in log})
        # someone whose access ends gets none
        db = self.db()
        notify.choose(db, self.uid["lee"], self.pid, "results", True)
        db.execute("DELETE FROM memberships WHERE user_id=? AND profile_id=?", (self.uid["lee"], self.pid))
        try:
            db.execute("DELETE FROM notify_optins WHERE user_id=?", (self.uid["kim"],))
            self.assertEqual(notify.wanting(db, self.pid, "results"), [])
        finally:
            db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (self.uid["lee"], self.pid))

    # ------------------------------------------------------------ each person's own topic (#1130's review)
    def test_each_person_has_their_own_topic_and_gets_one_each(self):
        self.set_server()
        kim, lee = self.signed_in("kim"), self.signed_in("lee")
        for c in (kim, lee):
            c.put("/api/me/notify", json={"profile": self.pid, "kind": "results", "on": True}, headers=H)
        tk, tl = kim.get("/api/me/notify").json()["topic"], lee.get("/api/me/notify").json()["topic"]
        self.assertNotEqual(tk, tl)
        self.assertTrue(tk.startswith(self.ntfy.url + "/sanguity-"))
        db = self.db()
        with dbm.transaction(db):
            notify.new_results(db, self.pid, 80)
        self.assertEqual(notify.run_queued(db), 2)
        self.assertEqual(sorted(p["path"] for p in self.ntfy.posts), sorted([self.topic("kim"), self.topic("lee")]))
        self.assertEqual(self.bodies(), ["Sanguity: new results for Ben"] * 2)
        # kept encrypted
        raw = db.execute("SELECT topic FROM notify_topics WHERE user_id=?", (self.uid["kim"],)).fetchone()[0]
        self.assertNotIn("sanguity-", raw)

    def test_someone_without_access_never_gets_one(self):
        """lee's access to Ben ends: nothing more of Ben's reaches lee's topic, made or queued; kim's still does. And
        mira's profile, which kim can't see, never reaches kim's."""
        self.set_server()
        db = self.db()
        self.opt_in("kim", "results")
        self.opt_in("lee", "results")
        self.opt_in("lee", "results", pid=self.other)
        with dbm.transaction(db):
            notify.new_results(db, self.pid, 81)   # queued for both
        db.execute("DELETE FROM memberships WHERE user_id=? AND profile_id=?", (self.uid["lee"], self.pid))
        self.addCleanup(lambda: dbm.connect().execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')",
                                                       (self.uid["lee"], self.pid)))
        with dbm.transaction(db):
            notify.new_results(db, self.pid, 82)
            notify.new_results(db, self.other, 83)
        notify.run_queued(db)
        got = sorted((p["path"], p["body"]) for p in self.ntfy.posts)
        self.assertEqual(got, sorted([(self.topic("kim"), "Sanguity: new results for Ben")] * 2
                                     + [(self.topic("lee"), "Sanguity: new results for Mira (fictional)")]))
        self.assertEqual(db.execute("SELECT state FROM notify_sent WHERE key=?", (f"results:81:{self.uid['lee']}",)).fetchone()[0], "dropped")
        # a disabled account gets none either
        db.execute("UPDATE users SET disabled=1 WHERE id=?", (self.uid["kim"],))
        try:
            with dbm.transaction(db):
                notify.new_results(db, self.pid, 84)
            self.assertEqual(notify.wanting(db, self.pid, "results"), [])
        finally:
            db.execute("UPDATE users SET disabled=0 WHERE id=?", (self.uid["kim"],))

    def test_make_a_new_topic_and_a_test_to_ones_own(self):
        self.set_server()
        kim = self.signed_in("kim")
        old = kim.get("/api/me/notify").json()["topic"]
        self.assertEqual(kim.get("/api/me/notify").json()["topic"], old, "kept")
        new = kim.post("/api/me/notify/topic", headers=H).json()["topic"]
        self.assertNotEqual(new, old)
        self.assertEqual(kim.post("/api/me/notify/test", headers=H).status_code, 200)
        self.assertEqual([p["path"] for p in self.ntfy.posts], [self.topic("kim")])
        self.assertEqual(self.ntfy.posts[0]["path"], "/" + new.rsplit("/", 1)[1], "the new one, never the old")
        self.assertEqual(kim.post("/api/me/notify/test", headers=H).status_code, 502, "not again at once")
        acts = [e["action"] for e in activity.for_accounts(self.db())]
        self.assertIn("notify.topic", acts)
        self.assertFalse(any(old.rsplit("/", 1)[1] in json.dumps(e) or new.rsplit("/", 1)[1] in json.dumps(e)
                             for e in activity.for_accounts(self.db())), "no topic in any log")

    def test_another_server_makes_every_topic_new_asked_first(self):
        """(#1130's re-review) An admin pointing the server at a host of their own learns no topic used afterwards."""
        ada, kim = self.signed_in("ada"), self.signed_in("kim")
        self.assertEqual(ada.put("/api/settings/install/notify.url", json={"value": self.ntfy.url}, headers=H).status_code, 200)
        old = {n: kim.get("/api/me/notify").json()["topic"].rsplit("/", 1)[1] if n == "kim" else notify.topic(self.db(), self.uid[n])
               for n in ("kim", "lee")}
        # the same origin (a path under it): kept, nothing asked
        self.assertEqual(ada.put("/api/settings/install/notify.url", json={"value": self.ntfy.url + "/ntfy"}, headers=H).status_code, 200)
        self.assertEqual(notify.topic(self.db(), self.uid["kim"]), old["kim"])
        for other in ("https://" + self.ntfy.host, "https://ntfy.example.test"):   # another scheme, or host
            r = ada.put("/api/settings/install/notify.url", json={"value": other}, headers=H)
            self.assertEqual(r.status_code, 409, r.text)
            c = r.json()["detail"]["confirm"]
            self.assertEqual(c["count"], 2)
            self.assertIn("every person's topic new", c["message"])
            r = ada.put("/api/settings/install/notify.url", json={"value": other, "confirm": "2"}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            new = {n: notify.topic(self.db(), self.uid[n]) for n in ("kim", "lee")}
            self.assertTrue(all(new[n] != old[n] for n in new), (old, new))
            self.assertEqual(kim.get("/api/me/notify").json()["topic"], other + "/" + new["kim"], "the new address shown")
            old = new
        self.assertIn("another server", settings.get_def("notify.url").help)

    def test_a_reset_makes_the_persons_topic_new(self):
        """(#1130's re-review) An admin's password or second-factor reset, one's own password change or two-factor
        sign-in turned off, a recovery code's sign-in: that person's topic made new (no one else's; none made for
        someone who has none)."""
        self.set_server()
        db = self.db()
        for n in ("kim", "lee"):
            notify.topic(db, self.uid[n])

        def renewed(do):
            before = {n: notify.topic(db, self.uid[n], make=False) for n in ("kim", "lee")}
            do()
            after = {n: notify.topic(db, self.uid[n], make=False) for n in ("kim", "lee")}
            return after["kim"] != before["kim"] and after["lee"] == before["lee"]
        self.assertTrue(renewed(lambda: accounts.set_temp_password(db, self.uid["kim"])), "an admin's password reset")
        db.execute("UPDATE users SET pw_hash=?, must_change_pw=0, temp_pw_expires=NULL WHERE id=?",
                   (main.sec.hash_password(PW["kim"]), self.uid["kim"]))
        self.assertTrue(renewed(lambda: accounts.turn_off_2fa(db, self.uid["kim"], passkeys_too=True)), "an admin's 2FA reset")
        self.assertTrue(renewed(lambda: accounts.turn_off_2fa(db, self.uid["kim"])), "one's own 2FA turned off")
        kim = self.signed_in("kim")
        self.assertTrue(renewed(lambda: self.assertEqual(kim.post("/api/me/password", json={"current": PW["kim"], "new": "another fictional long passphrase"},
                                                                   headers=H).status_code, 200)), "one's own password changed")
        db.execute("UPDATE users SET pw_hash=? WHERE id=?", (main.sec.hash_password(PW["kim"]), self.uid["kim"]))
        accounts.set_temp_password(db, self.uid["ada"])   # someone with no topic gets none made
        self.assertIsNone(notify.topic(db, self.uid["ada"], make=False))
        db.execute("UPDATE users SET pw_hash=?, must_change_pw=0, temp_pw_expires=NULL WHERE id=?",
                   (main.sec.hash_password(PW["ada"]), self.uid["ada"]))

    def test_a_restore_keeps_todays_topics_choices_and_token(self):
        from server import backup
        self.assertLessEqual({"notify_topics", "notify_optins", "notify_sent"}, set(backup.ACCESS_TABLES))
        self.assertLessEqual({notify.TOKEN_META, notify.ENV_META}, set(backup.INSTALL_META))
        self.assertIn(("notify_topics", "user_id", "topic"), atrest.FIELDS)

    # ------------------------------------------------------------ the environment's token (#1130's review)
    def test_an_environment_token_that_cant_be_sent_is_ignored_unquoted(self):
        self.set_server()
        db = self.db()
        notify.topic(db, self.uid["kim"])
        bad = "tk_secret_word\nX-Injected: yes"
        notify._warned.clear()
        with mock.patch.dict(os.environ, {"NOTIFY_TOKEN": bad}), self.assertLogs("sanguity.notify", "WARNING") as logs:
            self.assertEqual(notify.token(db, claim=True), "")
            notify.send(db, "test", self.uid["kim"])
        self.assertNotIn("Authorization", self.ntfy.posts[0]["headers"])
        self.assertNotIn("X-Injected", self.ntfy.posts[0]["headers"])
        self.assertFalse(any("tk_secret_word" in m for m in logs.output))
        # a header http.client refuses all the same: a SendError that doesn't quote it
        with mock.patch.object(notify, "request", return_value=(b"x", {"Authorization": "Bearer tk_secret_word\r\nX: y"})):
            with self.assertRaises(notify.SendError) as e:
                notify.send(db, "test", self.uid["kim"])
        self.assertNotIn("tk_secret_word", str(e.exception))

    def test_the_environment_token_goes_to_the_first_scheme_and_host_alone(self):
        self.set_server()
        db = self.db()
        notify.topic(db, self.uid["kim"])
        with mock.patch.dict(os.environ, {"NOTIFY_TOKEN": "tk_env_one"}):
            notify.send(db, "test", self.uid["kim"])
            self.assertEqual(self.ntfy.posts[-1]["headers"]["Authorization"], "Bearer tk_env_one")
            for elsewhere in ("https://" + self.ntfy.host, "https://ntfy.example.test"):
                self.set_server(elsewhere)
                self.assertEqual(notify.token_source(db), "environment-elsewhere")
                self.assertNotIn("Authorization", notify.request(db, "test", claim=True)[1])
            self.set_server()
            self.assertEqual(notify.token_source(db), "environment")
        with mock.patch.dict(os.environ, {"NOTIFY_TOKEN": "tk_env_two"}):   # a new token: kept for the next server it goes to
            self.set_server("https://ntfy.example.test")
            self.assertEqual(notify.request(db, "test", claim=True)[1]["Authorization"], "Bearer tk_env_two")
            self.set_server()
            self.assertNotIn("Authorization", notify.request(db, "test", claim=True)[1])

    def test_the_loop_logs_no_exceptions_words(self):
        with mock.patch.object(notify, "tick", side_effect=ValueError("Invalid header value b'Bearer tk_secret_word'")), \
                mock.patch.object(notify._wake, "wait", side_effect=lambda t: notify._stop.set()), \
                self.assertLogs("sanguity.notify", "ERROR") as logs:
            notify._stop.clear()
            notify._loop()
        notify._stop.clear()
        self.assertEqual(logs.output, ["ERROR:sanguity.notify:notifications: ValueError"])

    # ------------------------------------------------------------ new results
    def test_new_results_once_each_and_only_when_wanted(self):
        self.set_server()
        db = self.db()
        with dbm.transaction(db):
            notify.new_results(db, self.pid, 41)   # nobody wants them yet
        self.assertEqual(notify.run_queued(db), 0)
        self.opt_in("kim", "doses")   # another kind isn't this one
        with dbm.transaction(db):
            notify.new_results(db, self.pid, 41)
        self.assertEqual(notify.run_queued(db), 0)
        self.opt_in("kim", "results")
        for _ in range(2):   # the same pull's twice (a restart) is one
            with dbm.transaction(db):
                notify.new_results(db, self.pid, 42)
        self.assertEqual(notify.run_queued(db), 1)
        self.assertEqual(notify.run_queued(db), 0)
        self.assertEqual(self.bodies(), ["Sanguity: new results for Ben"])
        # a pull rolled back makes none
        with self.assertRaises(RuntimeError), dbm.transaction(db):
            notify.new_results(db, self.pid, 43)
            raise RuntimeError("the pull failed")
        self.assertEqual(notify.run_queued(db), 0)
        # turned off before it's sent: dropped
        with dbm.transaction(db):
            notify.new_results(db, self.pid, 44)
        notify.choose(db, self.uid["kim"], self.pid, "results", False)
        self.assertEqual(notify.run_queued(db), 0)
        self.assertEqual(db.execute("SELECT state FROM notify_sent WHERE key LIKE 'results:44:%'").fetchone()[0], "dropped")

    # ------------------------------------------------------------ doses
    def test_a_dose_due_is_sent_once_at_its_time(self):
        self.set_server()
        db = self.db()
        self.opt_in("kim", "doses", ago=3 * 86400)
        self.line(times=("08:00", "08:00", "20:00"))   # two at one time: one notification
        self.assertEqual(notify.due_doses(db, now=self.at("07:59")), 0, "not before its time")
        self.assertEqual(notify.due_doses(db, now=self.at("08:01")), 1)
        self.assertEqual(notify.due_doses(db, now=self.at("08:02")), 0, "made once: again (a restart) makes none")
        self.assertEqual(notify.run_queued(db), 1)
        self.assertEqual(self.bodies(), ["Sanguity: a dose is due for Ben"])
        self.assertEqual(notify.due_doses(db, now=self.at("20:20")), 0, "noticed too late (the server was stopped): none")
        self.assertEqual(notify.due_doses(db, now=self.at("08:05", day=TODAY + timedelta(days=1))), 1, "the next day's")

    def test_paused_remind_me_off_and_as_needed_lines_notify_nothing(self):
        self.set_server()
        db = self.db()
        self.opt_in("kim", "doses", ago=3 * 86400)
        self.line(times=("08:00",), paused=True)
        self.line(times=("08:00",), on=False)
        self.line(asneeded=True, times=())
        self.assertEqual(notify.due_doses(db, now=self.at("08:01")), 0)
        self.opt_in("lee", "results")
        self.assertEqual(notify.due_doses(db, now=self.at("08:01")), 0)
        self.line(times=("08:00",))   # one that reminds, of a medication the records no longer have, or say stopped
        with mock.patch.object(med_reminders, "current", return_value={}):
            self.assertEqual(notify.due_doses(db, now=self.at("08:01")), 0)
        stopped = {self.med: {**med_reminders.current(db, self.pid)[self.med], "stop": D(-1)}}
        with mock.patch.object(med_reminders, "current", return_value=stopped):
            self.assertEqual(notify.due_doses(db, now=self.at("08:01")), 0)
        self.assertEqual(notify.due_doses(db, now=self.at("08:01")), 1)

    def test_times_before_the_opt_in_and_in_the_lines_zone(self):
        self.set_server()
        db = self.db()
        self.opt_in("kim", "doses")   # just now
        self.line(times=("08:00",), tz="America/Chicago")
        self.assertEqual(notify.due_doses(db, now=self.at("08:01", tz="America/Chicago") - 86400), 0, "before the opt-in")
        db.execute("UPDATE notify_optins SET created=created-?", (3 * 86400,))
        self.assertEqual(notify.due_doses(db, now=self.at("08:01", tz="UTC")), 0, "08:00 is Chicago's, not UTC's")
        self.assertEqual(notify.due_doses(db, now=self.at("08:01", tz="America/Chicago")), 1)

    def test_a_line_without_a_zone_uses_the_persons(self):
        self.set_server()
        db = self.db()
        settings.put(db, "dates.timeZone", "Asia/Tokyo", uid=self.uid["kim"])
        self.addCleanup(lambda: settings.clear(dbm.connect(), ["dates.timeZone"], "user", uid=self.uid["kim"]))
        self.opt_in("kim", "doses", ago=3 * 86400)
        self.line(times=("08:00",), tz="")
        self.assertEqual(notify.due_doses(db, now=self.at("08:01", tz="UTC")), 0)
        self.assertEqual(notify.due_doses(db, now=self.at("08:01", tz="Asia/Tokyo")), 1)

    # ------------------------------------------------------------ retries
    def test_retried_then_logged_in_install_activity_without_its_words(self):
        self.set_server()
        db = self.db()
        self.opt_in("kim", "results")
        with dbm.transaction(db):
            notify.new_results(db, self.pid, 50)
        self.ntfy.status = 500
        now = dbm.now()
        for i in range(notify.TRIES):
            self.assertEqual(notify.run_queued(db, now=now), 0)
            r = db.execute("SELECT state, tries, next_at FROM notify_sent WHERE key LIKE 'results:50:%'").fetchone()
            self.assertEqual(r["tries"], i + 1)
            if i < notify.TRIES - 1:
                self.assertEqual((r["state"], r["next_at"]), ("queued", now + notify.BACKOFF[i]))
                self.assertEqual(notify.run_queued(db, now=now), 0, "not again before its next try")
                now = r["next_at"]
        self.assertEqual(r["state"], "failed")
        self.assertEqual(len(self.ntfy.posts), notify.TRIES)
        (e,) = [e for e in activity.for_accounts(db) if e["action"] == "notify.fail"]
        self.assertEqual(e["actor"], activity.NOTIFICATIONS)
        self.assertEqual(e["detail"], {"kind": "results", "tries": notify.TRIES, "why": "The notification server answered 500."})
        self.assertNotIn("Ben", json.dumps(e))
        # one that goes through after a failure
        self.ntfy.status = 200
        with dbm.transaction(db):
            notify.new_results(db, self.pid, 51)
        self.ntfy.status = 503
        notify.run_queued(db)
        self.ntfy.status = 200
        self.assertEqual(notify.run_queued(db, now=dbm.now() + notify.BACKOFF[0]), 1)

    def test_the_server_taken_away_drops_whats_queued(self):
        self.set_server()
        db = self.db()
        self.opt_in("kim", "results")
        with dbm.transaction(db):
            notify.new_results(db, self.pid, 60)
        settings.clear(db, ["notify.url"], "install")
        self.assertEqual(notify.run_queued(db), 0)
        self.assertEqual(db.execute("SELECT state FROM notify_sent WHERE key LIKE 'results:60:%'").fetchone()[0], "dropped")
        self.assertEqual(self.ntfy.posts, [])

    # ------------------------------------------------------------ outside connections
    def test_listed_under_outside_connections(self):
        self.set_server()
        notify.topic(self.db(), self.uid["kim"])
        notify.send(self.db(), "test", self.uid["kim"], p=notify.permit(self.db()))
        host = self.ntfy.host
        ada, kim = self.signed_in("ada"), self.signed_in("kim")
        v = ada.get("/api/outside").json()
        self.assertIn(host, v["may_contact"])
        self.assertEqual(v["notifications"]["hosts"], [host])
        self.assertEqual([c["host"] for c in v["notifications"]["contacts"]], [host])
        self.assertEqual(kim.get("/api/outside").json()["notifications"]["hosts"], [host], "everyone sees where they'd go")

    # ------------------------------------------------------------ no health details
    def test_only_the_allowed_words_ever_reach_a_payload(self):
        """Every notification any test sent, and each kind's as made: one of the allowed texts with the profile's name,
        and none of the records' words."""
        self.set_server()
        db = self.db()
        self.opt_in("kim", "results")
        self.opt_in("kim", "doses", ago=3 * 86400)
        self.line(times=("08:00",))
        with dbm.transaction(db):
            notify.new_results(db, self.pid, 70)
        notify.due_doses(db, now=self.at("08:01"))
        notify.run_queued(db)
        notify.send(db, "test", self.uid["kim"])
        allowed = {notify.message(k, "Ben") for k in notify.TEXTS}
        posts = self.all_posts + self.ntfy.posts
        self.assertGreaterEqual(len(self.ntfy.posts), 3)
        for p in posts:
            self.assertIn(p["body"], allowed)
            said = (p["body"] + " " + " ".join(f"{k}: {v}" for k, v in p["headers"].items() if k.lower() != "authorization")).lower()
            for w in RECORD_WORDS:
                self.assertNotIn(w, said)
        for k in notify.TEXTS:
            body, headers = notify.request(db, k, "Ben")
            self.assertEqual(set(headers) - {"Authorization"}, {"Title", "Content-Type", "Click"})
            self.assertFalse(any(w in body.decode().lower() for w in RECORD_WORDS))
        with self.assertRaises(ValueError):
            notify.message("labs", "Ben")
        self.assertEqual(notify.message("results", "  Ben\n\tExample " + "x" * 100), "Sanguity: new results for Ben Example " + "x" * 48)


    def test_the_token_may_come_from_a_file(self):
        """#1158 (opt-in): NOTIFY_TOKEN_FILE, a Docker secret, as NOTIFY_TOKEN: it wins, read at each use (rotated, the new
        one is sent), named on the page as the variable that sets it; a file that can't be read is as if unset."""
        self.set_server()
        db = self.db()
        path = os.path.join(TMP, "notify-token")
        with open(path, "w") as f:
            f.write("tk_from_a_file_one\n")
        with mock.patch.dict(os.environ, {"NOTIFY_TOKEN_FILE": path}):
            self.assertEqual(notify.token_source(db), "environment")
            self.assertEqual(notify.request(db, "test", claim=True)[1]["Authorization"], "Bearer tk_from_a_file_one")
            with open(path, "w") as f:
                f.write("tk_from_a_file_two")
            self.assertEqual(notify.request(db, "test", claim=True)[1]["Authorization"], "Bearer tk_from_a_file_two")
            os.unlink(path)
            self.assertIsNone(notify.token_source(db))


if __name__ == "__main__":
    unittest.main(verbosity=2)
