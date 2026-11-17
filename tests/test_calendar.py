#!/usr/bin/env python3
"""Calendar feeds (server/calendar.py, server/routes_calendar.py, issue #51): a feed is valid iCalendar (parsed back:
nesting, required properties, CRLF, folding at 75 octets, escaping), its UIDs are the same on every fetch, minimal
details are the default and keep titles, places and sources out; full details show them and the source. Appointments
from the records' upcoming list (imported and typed in), and expected labs only for a steady rhythm (all-day,
tentative, "expected, not scheduled"). Only the token's hash is stored; rotation and revocation stop the old address;
a wrong token, a wrong profile or a person whose access ended all get the same bare 404; no cookie is set; fetches are
throttled; creation, rotation and revocation are logged but fetches aren't. Fictional data only.

  python3 tests/test_calendar.py
"""
import os, re, sys, tempfile, unittest
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-calendar-test-")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "no-build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"))
os.makedirs(os.path.join(TMP, "records"))
os.makedirs(os.path.join(TMP, "keys"))
sys.path.insert(0, ROOT)

import logging, shutil   # noqa: E402

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, activity, calendar, db as dbm, main, profiles as prof, records, security as sec   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites", "lee": "maple tunnel brisk saddle",
      "zed": "a stranger to every profile"}
TODAY = date.today()
D = lambda n: (TODAY + timedelta(days=n)).isoformat()


def lab_series(name, panel, days):
    return {"name": name, "unit": "g/dL", "panel": panel, "n": len(days), "txt": [],
            "pts": [[(TODAY + timedelta(days=d)).isoformat() + "T08:00:00", 13.1, 12, 16, ""] for d in days]}


# a fictional person: two imported appointments (one with a time, one a whole day), one long-past, labs every ~90 days
DATA = {"built": "2026-11-01T00:00:00+00:00", "source": "fictional",
        "events": [{"id": 1, "t": D(-30) + "T09:00:00", "k": "visit", "title": "Annual physical", "sub": ""}],
        "details": {}, "costs": [],
        "labs": [lab_series("Hemoglobin", "CBC", [-300, -210, -121, -30]),
                 lab_series("Platelets", "CBC", [-300, -210, -121, -30]),
                 lab_series("Sodium", "Metabolic panel", [-500, -400, -20, -5])],   # no steady rhythm
        "upcoming": [{"t": D(12) + "T10:30:00", "title": "Follow-up visit; bring list, please", "who": "Dr. Fictional Example",
                      "where": "Riverside Clinic, Suite 4", "dx": "Fictional diagnosis", "procs": ["Fictional procedure"]},
                     {"t": D(40), "title": "Physical therapy evaluation", "who": "", "where": "Lakeside Rehab", "dx": "", "procs": []},
                     {"t": D(-3), "title": "Already happened", "who": "", "where": "", "dx": "", "procs": []}]}


def client():
    return TestClient(main.app, base_url="http://testserver")


def unfold(text):
    assert text.endswith("\r\n"), "lines end with CRLF"
    raw = text[:-2].split("\r\n")
    for line in raw:
        assert len(line.encode("utf-8")) <= 75, f"line longer than 75 octets: {line!r}"
        assert "\n" not in line and "\r" not in line
    lines = []
    for line in raw:
        if line.startswith(" "):
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def parse(text):
    """iCalendar text -> {"props": {...}, "events": [{name: (params, value)}]}, checking it's well formed."""
    stack, cal, events, cur = [], {}, [], None
    for line in unfold(text):
        name_part, _, value = line.partition(":")
        name, *params = name_part.split(";")
        if name == "BEGIN":
            stack.append(value)
            if value == "VEVENT":
                cur = {}
            continue
        if name == "END":
            assert stack and stack.pop() == value, f"END:{value} doesn't close what's open"
            if value == "VEVENT":
                events.append(cur)
                cur = None
            continue
        assert stack, f"{line!r} outside the calendar"
        (cur if cur is not None else cal)[name] = (params, value)
    assert not stack, "unclosed components"
    return {"props": cal, "events": events}


def unescape(v):
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v)


class Calendar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = client().__enter__()
        db = dbm.connect()
        cls.uid = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        cls.pid = prof.create_profile(db, "Robin Example (fictional)", owner_id=cls.uid["robin"])
        cls.other = prof.create_profile(db, "Another (fictional)", owner_id=cls.uid["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (cls.uid["kim"], cls.pid))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (cls.uid["lee"], cls.pid))
        with dbm.transaction(db):
            records.store(db, cls.pid, DATA, "dataset")
        db.close()
        cls.c = {}
        for n in PW:
            c = client()
            r = c.post("/api/login", json={"username": n, "password": PW[n]}, headers=H)
            assert r.status_code == 200, r.text
            cls.c[n] = c

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        shutil.rmtree(TMP, True)

    def setUp(self):
        calendar.throttle._fails.clear()

    def make(self, who="robin", pid=None, **body):
        pid = pid or self.pid
        r = self.c[who].post(f"/api/profiles/{pid}/calendar", json={"tz": "UTC", **body}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def drop(self, who="robin", pid=None):
        self.c[who].delete(f"/api/profiles/{pid or self.pid}/calendar", headers=H)

    def fetch(self, path, status=200):
        r = client().get("/" + path)   # a calendar app: no session
        self.assertEqual(r.status_code, status, r.text)
        return r

    # ---------------------------------------------------------------- the feed's format and contents

    def test_valid_ics_and_stable_uids(self):
        j = self.make()
        try:
            r = self.fetch(j["path"])
            self.assertTrue(r.headers["content-type"].startswith("text/calendar"))
            self.assertEqual(r.headers["cache-control"], "private, max-age=0")
            self.assertNotIn("set-cookie", r.headers)
            cal = parse(r.text)
            for k in ("VERSION", "PRODID", "CALSCALE", "METHOD"):
                self.assertIn(k, cal["props"])
            self.assertEqual(cal["props"]["VERSION"][1], "2.0")
            for ev in cal["events"]:
                for k in ("UID", "DTSTAMP", "DTSTART", "DTEND", "SUMMARY"):
                    self.assertIn(k, ev)
                self.assertRegex(ev["DTSTAMP"][1], r"^\d{8}T\d{6}Z$")
                self.assertTrue(ev["UID"][1].endswith("@sanguity"))
            uids = [e["UID"][1] for e in cal["events"]]
            self.assertEqual(len(uids), len(set(uids)), "one UID per event")
            again = parse(self.fetch(j["path"]).text)
            self.assertEqual(uids, [e["UID"][1] for e in again["events"]], "the same UIDs on every fetch")
        finally:
            self.drop()

    def test_minimal_is_the_default(self):
        j = self.make()
        try:
            self.assertEqual(j["feed"]["details"], "minimal")
            text = self.fetch(j["path"]).text
            cal = parse(text)
            summaries = sorted(unescape(e["SUMMARY"][1]) for e in cal["events"])
            self.assertEqual(summaries, ["Appointment", "Appointment", "Labs expected"], summaries)
            for word in ("Follow-up", "Riverside", "Fictional", "CBC", "Hemoglobin", "Robin", "Physical therapy", "Sanguity data file"):
                self.assertNotIn(word, text)
            self.assertTrue(all("LOCATION" not in e for e in cal["events"]))
            self.assertEqual(unescape(cal["props"]["X-WR-CALNAME"][1]), "Sanguity")
            self.assertTrue(all(e["URL"][1] == f"http://testserver/?profile={self.pid}" for e in cal["events"]))
        finally:
            self.drop()

    def test_full_details_escaping_and_times(self):
        j = self.make(details="full")
        try:
            cal = parse(self.fetch(j["path"]).text)
            by = {unescape(e["SUMMARY"][1]): e for e in cal["events"]}
            self.assertEqual(set(by), {"Follow-up visit; bring list, please", "Physical therapy evaluation", "Labs expected (CBC)"})
            fu = by["Follow-up visit; bring list, please"]
            self.assertIn("Follow-up visit\\; bring list\\, please", fu["SUMMARY"][1], "escaped ; and ,")
            self.assertEqual(unescape(fu["LOCATION"][1]), "Riverside Clinic, Suite 4")
            desc = unescape(fu["DESCRIPTION"][1])
            self.assertIn("With Dr. Fictional Example", desc)
            self.assertIn("From Sanguity data file", desc)   # where it came from
            self.assertNotIn("diagnosis", desc.lower())
            self.assertNotIn("procedure", desc.lower())
            self.assertEqual(fu["DTSTART"], ([], (TODAY + timedelta(days=12)).strftime("%Y%m%d") + "T103000Z"))   # UTC feed
            self.assertEqual(fu["DTEND"][1][-7:], "113000Z")
            pt = by["Physical therapy evaluation"]
            self.assertEqual(pt["DTSTART"], (["VALUE=DATE"], (TODAY + timedelta(days=40)).strftime("%Y%m%d")))
            self.assertNotIn("Already happened", by, "past appointments are left out")
        finally:
            self.drop()

    def test_typed_in_appointment_shows(self):
        r = self.c["lee"].post(f"/api/profiles/{self.pid}/manual", json={"items": [
            {"kind": "upcoming", "date": D(20), "time": "14:15", "title": "Dental cleaning (fictional)", "who": "", "where": "Smile Studio"}],
            "save": True}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        j = self.make(who="lee", details="full")
        try:
            cal = parse(self.fetch(j["path"]).text)
            ev = [e for e in cal["events"] if unescape(e["SUMMARY"][1]) == "Dental cleaning (fictional)"]
            self.assertEqual(len(ev), 1)
            self.assertIn("From ", unescape(ev[0]["DESCRIPTION"][1]))
        finally:
            self.drop("lee")

    def test_expected_labs_event(self):
        j = self.make(details="full")
        try:
            cal = parse(self.fetch(j["path"]).text)
            labs = [e for e in cal["events"] if unescape(e["SUMMARY"][1]).startswith("Labs expected")]
            self.assertEqual(len(labs), 1, "CBC only: the metabolic panel has no steady rhythm")
            ev = labs[0]
            self.assertEqual(ev["STATUS"][1], "TENTATIVE")
            self.assertEqual(ev["TRANSP"][1], "TRANSPARENT")
            self.assertEqual(ev["DTSTART"][0], ["VALUE=DATE"], "all day, never a made-up time")
            self.assertTrue(unescape(ev["DESCRIPTION"][1]).startswith("Expected, not scheduled."))
            self.assertEqual(ev["DTSTART"][1], (TODAY + timedelta(days=-30 + 90)).strftime("%Y%m%d"))
        finally:
            self.drop()
        j = self.make(labs=False)
        try:
            cal = parse(self.fetch(j["path"]).text)
            self.assertFalse([e for e in cal["events"] if "Labs" in e["SUMMARY"][1]])
        finally:
            self.drop()

    def test_folding_long_lines(self):
        line = "SUMMARY:" + calendar.escape("Ünïcödé follow-up visit with a very long fictional title, " * 4)
        folded = calendar.fold(line)
        for part in folded[:-2].split("\r\n"):
            self.assertLessEqual(len(part.encode("utf-8")), 75)
        self.assertEqual(folded[:-2].replace("\r\n ", ""), line, "unfolds to the same line, no character split")

    def test_zones(self):
        feed = {"profile_id": self.pid, "profile_name": "x", "details": "full", "labs": 0, "tz": "Nowhere/Fictional"}
        db = dbm.connect()
        try:
            text = calendar.build(db, feed, "http://testserver")
            self.assertIn("X-WR-TIMEZONE:Nowhere/Fictional", text, "an unknown zone: floating times, named")
            self.assertIn("DTSTART:" + (TODAY + timedelta(days=12)).strftime("%Y%m%d") + "T103000\r\n", text)
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo("America/Chicago")
            except Exception:
                return   # no tz database here (Windows without tzdata)
            day = TODAY + timedelta(days=12)
            want = datetime(day.year, day.month, day.day, 10, 30, tzinfo=ZoneInfo("America/Chicago")).astimezone(timezone.utc)
            text = calendar.build(db, {**feed, "tz": "America/Chicago"}, "http://testserver")
            self.assertIn("DTSTART:" + want.strftime("%Y%m%dT%H%M%SZ"), text)
            self.assertNotIn("X-WR-TIMEZONE", text)
        finally:
            db.close()

    # ---------------------------------------------------------------- tokens

    def test_only_the_hash_is_stored(self):
        j = self.make()
        try:
            token = j["path"].split("token=")[1]
            db = dbm.connect()
            row = db.execute("SELECT token_hash FROM calendar_feeds WHERE profile_id=? AND user_id=?", (self.pid, self.uid["robin"])).fetchone()
            self.assertEqual(row[0], sec.token_hash(token))
            db.close()
            with open(os.path.join(TMP, "sanguity.db"), "rb") as f:
                self.assertNotIn(token.encode(), f.read())
            st = self.c["robin"].get(f"/api/profiles/{self.pid}/calendar").json()
            self.assertNotIn(token, str(st), "the address is shown once, never again")
        finally:
            self.drop()

    def test_rotate_and_revoke(self):
        j = self.make()
        old = j["path"]
        self.fetch(old)
        r = self.c["robin"].post(f"/api/profiles/{self.pid}/calendar/rotate", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        new = r.json()["path"]
        self.assertNotEqual(old, new)
        self.assertEqual(self.fetch(old, 404).text, "Not found.\n")
        self.fetch(new)
        self.assertEqual(self.c["robin"].post(f"/api/profiles/{self.pid}/calendar", json={}, headers=H).status_code, 409)
        self.assertEqual(self.c["robin"].delete(f"/api/profiles/{self.pid}/calendar", headers=H).status_code, 200)
        self.fetch(new, 404)
        self.assertIsNone(self.c["robin"].get(f"/api/profiles/{self.pid}/calendar").json()["mine"])
        self.assertEqual(self.c["robin"].post(f"/api/profiles/{self.pid}/calendar/rotate", headers=H).status_code, 404)

    def test_wrong_token_tells_nothing(self):
        j = self.make()
        try:
            token = j["path"].split("token=")[1]
            answers = [self.fetch(p, 404) for p in (
                f"api/profiles/{self.pid}/calendar.ics?token=" + "x" * 43,
                f"api/profiles/{self.pid}/calendar.ics?token=",
                f"api/profiles/{self.pid}/calendar.ics",
                f"api/profiles/{self.other}/calendar.ics?token={token}",   # right token, another profile
                f"api/profiles/9999/calendar.ics?token={token}")]
            self.assertEqual({(a.text, a.headers["content-type"]) for a in answers}, {("Not found.\n", "text/plain; charset=utf-8")})
        finally:
            self.drop()

    def test_who_may_make_one(self):
        """Anyone who can view the profile makes one for themself (it shows only what they see in the app)."""
        j = self.make(who="kim")   # a viewer
        self.fetch(j["path"])
        r = self.c["zed"].post(f"/api/profiles/{self.pid}/calendar", json={}, headers=H)
        self.assertEqual(r.status_code, 404, "not shared with zed: as if there were no profile")
        self.assertEqual(self.c["robin"].post(f"/api/profiles/{self.pid}/calendar", json={}, headers={}).status_code, 403, "CSRF check")
        # owners see everyone's and can turn off any; others see only their own
        self.make()
        st = self.c["robin"].get(f"/api/profiles/{self.pid}/calendar").json()
        self.assertEqual({f["username"] for f in st["everyone"]}, {"robin", "kim"})
        self.assertNotIn("everyone", self.c["kim"].get(f"/api/profiles/{self.pid}/calendar").json())
        robins = st["mine"]["id"]
        self.assertEqual(self.c["kim"].delete(f"/api/profiles/{self.pid}/calendar/{robins}", headers=H).status_code, 404)
        kims = [f["id"] for f in st["everyone"] if f["username"] == "kim"][0]
        self.assertEqual(self.c["robin"].delete(f"/api/profiles/{self.pid}/calendar/{kims}", headers=H).status_code, 200)
        self.fetch(j["path"], 404)
        self.drop()

    def test_feed_stops_when_access_ends(self):
        j = self.make(who="lee")
        self.fetch(j["path"])
        db = dbm.connect()
        db.execute("DELETE FROM memberships WHERE user_id=? AND profile_id=?", (self.uid["lee"], self.pid))
        try:
            self.fetch(j["path"], 404)
        finally:
            db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (self.uid["lee"], self.pid))
        db.execute("UPDATE users SET disabled=1 WHERE id=?", (self.uid["lee"],))
        try:
            self.fetch(j["path"], 404)
        finally:
            db.execute("UPDATE users SET disabled=0 WHERE id=?", (self.uid["lee"],))
            db.close()
        self.fetch(j["path"])
        self.drop("lee")

    def test_change_details(self):
        j = self.make()
        try:
            r = self.c["robin"].patch(f"/api/profiles/{self.pid}/calendar", json={"details": "full"}, headers=H)
            self.assertEqual(r.json()["feed"]["details"], "full")
            self.assertIn("Riverside", self.fetch(j["path"]).text, "same address, more detail")
            self.assertEqual(self.c["robin"].patch(f"/api/profiles/{self.pid}/calendar", json={"details": "everything"}, headers=H).status_code, 422)
        finally:
            self.drop()

    def test_activity_logged_but_not_fetches(self):
        before = {e["id"] for e in activity.for_profile(dbm.connect(), self.pid, limit=500)}
        j = self.make()
        for _ in range(3):
            self.fetch(j["path"])
        self.c["robin"].post(f"/api/profiles/{self.pid}/calendar/rotate", headers=H)
        self.drop()
        new = [e for e in activity.for_profile(dbm.connect(), self.pid, limit=500) if e["id"] not in before]
        self.assertEqual([(e["action"], e["detail"]["change"]) for e in reversed(new)],
                         [("calendar", "create"), ("calendar", "rotate"), ("calendar", "revoke")])
        self.assertTrue(all("token" not in str(e["detail"]) for e in new))

    def test_last_fetched(self):
        j = self.make()
        try:
            self.assertIsNone(j["feed"]["last_fetched"])
            self.fetch(j["path"])
            st = self.c["robin"].get(f"/api/profiles/{self.pid}/calendar").json()["mine"]
            self.assertGreaterEqual(st["last_fetched"], dbm.now() - 5)
        finally:
            self.drop()

    def test_head(self):
        j = self.make()
        try:
            r = client().head("/" + j["path"])
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.content, b"")
        finally:
            self.drop()

    # ---------------------------------------------------------------- throttling

    def test_wrong_tokens_are_throttled(self):
        j = self.make()
        try:
            path = f"api/profiles/{self.pid}/calendar.ics?token=" + "y" * 43
            for _ in range(calendar.FeedThrottle.FREE["bad"]):
                self.fetch(path, 404)
            r = self.fetch(path, 429)
            self.assertTrue(int(r.headers["retry-after"]) > 0)
            self.fetch(j["path"])   # the right one still works from that address (#203: it may be a shared proxy's)
            self.fetch(path, 429)    # and a wrong one still waits
        finally:
            self.drop()

    def test_one_feed_fetched_too_often(self):
        j = self.make()
        try:
            for _ in range(calendar.FeedThrottle.FREE["feed"]):
                self.fetch(j["path"])
            self.fetch(j["path"], 429)
            other = self.make(who="kim")   # another feed is unaffected
            self.fetch(other["path"])
        finally:
            self.drop()
            self.drop("kim")

    # ---------------------------------------------------------------- the rhythm guess, on its own

    def test_rhythm_guess(self):
        series = lambda panel, days: {"name": panel, "panel": panel, "pts": [[(TODAY + timedelta(days=d)).isoformat()] for d in days]}
        got = calendar.expected_labs([
            series("Steady quarterly", [-280, -190, -100, -10]),        # 90 days: next in 80 days
            series("Steady monthly", [-95, -64, -33, -3]),              # ~31 days: next in ~28
            series("Too few", [-60, -30]),
            series("Irregular", [-400, -380, -100, -10]),
            series("Stopped", [-700, -610, -520, -430]),                # due 340 days ago
            series("Daily", list(range(-10, 0))),                       # too often to be a rhythm of visits
        ], TODAY)
        self.assertEqual([(e["panels"], (e["day"] - TODAY).days) for e in got],
                         [(["Steady monthly"], 28), (["Steady quarterly"], 80)])
        # two panels on the same rhythm make one event
        both = calendar.expected_labs([series("CBC", [-280, -190, -100, -10]), series("Lipids", [-280, -190, -100, -10])], TODAY)
        self.assertEqual([e["panels"] for e in both], [["CBC", "Lipids"]])
        # a little overdue stays; well overdue is dropped
        late = calendar.expected_labs([series("Late", [-300, -210, -120, -100 - 20])], TODAY)
        self.assertEqual(late, [], "irregular after all")
        late = calendar.expected_labs([series("Late", [-330, -240, -150, -60, ])], TODAY)
        self.assertEqual([(e["day"] - TODAY).days for e in late], [30])
        late = calendar.expected_labs([series("Late", [-400, -310, -220, -130])], TODAY)
        self.assertEqual([(e["day"] - TODAY).days for e in late], [-40], "40 days past due, within half the gap")


if __name__ == "__main__":
    unittest.main(verbosity=2)
