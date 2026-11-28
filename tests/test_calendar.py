#!/usr/bin/env python3
"""Calendar feeds (server/calendar.py, server/routes_calendar.py, issue #51): a feed is valid iCalendar (parsed back:
nesting, required properties, CRLF, folding at 75 octets, escaping), its UIDs are the same on every fetch, minimal
details are the default and keep titles, places and sources out; full details show them and the source. Appointments
from the records' upcoming list (imported and typed in), and expected labs only for a steady rhythm (all-day,
tentative, "expected, not scheduled"). Medication reminders (#162): each time a recurring event (RRULE) in its own
time zone, with a VTIMEZONE checked against the tz database through clock changes in both hemispheres, a zone whose
rules changed and one with none; DTSTART on a day of the rule, UNTIL in UTC (floating with floating times), ended by
its until day or by the records saying the medication stopped; "Medication" unless full details; off, or a feed
without them, lists none; who may set them, refusals, encrypted, kept when imports are undone. Only the token's hash is stored; rotation and revocation stop the old address;
a wrong token, a wrong profile or a person whose access ended all get the same bare 404; no cookie is set; fetches are
throttled; creation, rotation and revocation are logged but fetches aren't. Fictional data only.

  python3 tests/test_calendar.py
"""
import json, os, re, sys, tempfile, unittest
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

from server import accounts, activity, calendar, crypto, db as dbm, main, profiles as prof, records, security as sec   # noqa: E402
import given   # noqa: E402  (#1158: a setting as the app holds it)

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
    """iCalendar text -> {"props": {...}, "events": [{name: (params, value)}], "zones": {TZID: [observance]}},
    checking it's well formed. An event's VALARM is its "VALARM" ({name: value}); each VTIMEZONE observance is
    {"kind": "STANDARD" | "DAYLIGHT", name: value...} (#162)."""
    stack, cal, events, cur, zones, zone, obs, alarm = [], {}, [], None, {}, None, None, None
    for line in unfold(text):
        name_part, _, value = line.partition(":")
        name, *params = name_part.split(";")
        if name == "BEGIN":
            assert value != "VEVENT" or stack == ["VCALENDAR"], "a VEVENT inside the calendar only"
            assert value != "VALARM" or stack[-1:] == ["VEVENT"], "a VALARM inside a VEVENT only"
            stack.append(value)
            if value == "VEVENT":
                cur = {}
            elif value == "VTIMEZONE":
                zone = []
            elif value in ("STANDARD", "DAYLIGHT"):
                assert stack[-2] == "VTIMEZONE"
                obs = {"kind": value}
            elif value == "VALARM":
                alarm = {}
            continue
        if name == "END":
            assert stack and stack.pop() == value, f"END:{value} doesn't close what's open"
            if value == "VEVENT":
                events.append(cur)
                cur = None
            elif value == "VALARM":
                cur["VALARM"], alarm = alarm, None
            elif value in ("STANDARD", "DAYLIGHT"):
                zone.append(obs)
            elif value == "VTIMEZONE":
                zones[zone_id] = zone
            continue
        assert stack, f"{line!r} outside the calendar"
        if alarm is not None:
            alarm[name] = value
        elif stack[-1] in ("STANDARD", "DAYLIGHT"):
            obs[name] = value
        elif stack[-1] == "VTIMEZONE":
            assert name == "TZID"
            zone_id = value
        else:
            (cur if cur is not None else cal)[name] = (params, value)
    assert not stack, "unclosed components"
    return {"props": cal, "events": events, "zones": zones}


# ---- a reader of what the feed's reminders say, written apart from the server's code (#162): RFC 5545's yearly
# observances (BYMONTH, BYDAY nth or -1, UNTIL) and an event's DAILY or WEEKLY rule, enough to check every instance
DOW = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def _local(v):
    return datetime.strptime(v, "%Y%m%dT%H%M%S")


def _off(v):
    sign = -1 if v[0] == "-" else 1
    return sign * timedelta(hours=int(v[1:3]), minutes=int(v[3:5]), seconds=int(v[5:7] or 0))


def onsets(o, last_year):
    """Each onset of an observance as (local wall time in its TZOFFSETFROM, UTC)."""
    start, before = _local(o["DTSTART"]), _off(o["TZOFFSETFROM"])
    if "RRULE" not in o:
        return [(start, start - before)]
    rule = dict(x.split("=") for x in o["RRULE"].split(";"))
    assert rule["FREQ"] == "YEARLY", rule
    until = datetime.strptime(rule["UNTIL"], "%Y%m%dT%H%M%SZ") if "UNTIL" in rule else None
    month, nth, wd = int(rule["BYMONTH"]), int(rule["BYDAY"][:-2]), DOW.index(rule["BYDAY"][-2:])
    out = []
    for y in range(start.year, last_year + 1):
        days = [date(y, month, d) for d in range(1, 32) if _valid(y, month, d) and date(y, month, d).weekday() == wd]
        d = days[nth - 1] if nth > 0 else days[nth]
        local = datetime.combine(d, start.time())
        if local < start:
            continue
        if until and local - before > until:
            break
        out.append((local, local - before))
    return out


def _valid(y, m, d):
    try:
        date(y, m, d)
        return True
    except ValueError:
        return False


def offset_at(zone, local, last_year):
    """The UTC offset the VTIMEZONE gives a local wall time: the latest onset at or before it (away from the hour a
    clock change makes ambiguous, which these tests keep clear of)."""
    best = None
    for o in zone:
        for wall, _ in onsets(o, last_year):
            if wall <= local and (best is None or wall > best[0]):
                best = (wall, _off(o["TZOFFSETTO"]))
    assert best, f"no observance before {local}"
    return best[1]


def instances(ev, zones, most=800):
    """An event's instances as (local wall time, UTC or None for floating), following its RRULE."""
    params, v = ev["DTSTART"]
    tzid = next((p[5:] for p in params if p.startswith("TZID=")), None)
    start = _local(v)
    rule = dict(x.split("=") for x in ev["RRULE"][1].split(";"))
    days = rule.get("BYDAY", ",".join(DOW)).split(",") if rule["FREQ"] == "WEEKLY" else DOW
    assert rule["FREQ"] in ("DAILY", "WEEKLY")
    until = rule.get("UNTIL")
    if tzid:
        assert until is None or until.endswith("Z"), "with a TZID, UNTIL is in UTC (RFC 5545 3.3.10)"
    else:
        assert until is None or not until.endswith("Z"), "with floating times, a floating UNTIL"
    out, d = [], start
    while len(out) < most:
        if DOW[d.weekday()] in days:
            utc = d - offset_at(zones[tzid], d, d.year + 1) if tzid else None
            if until and ((utc and utc > datetime.strptime(until, "%Y%m%dT%H%M%SZ")) or (not tzid and d > _local(until))):
                break
            out.append((d, utc))
        d += timedelta(days=1)
    return out


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
        pass   # tearDownModule: the medication reminders' tests (#162) use these accounts and sessions too

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

    def test_given_at_the_public_address_where_one_is_set(self):   # #1009
        """A feed's address is given at the public address where one is set (the first https:// one by name, else the
        first), as "base" beside its path, when it's made and when it's rotated, and each event links back there too;
        without one, no base, and the link back is the address the request reached."""
        from unittest import mock
        with given.settings(PUBLIC_ORIGINS=""):
            j = self.make(details="full")
            try:
                self.assertNotIn("base", j)
                self.assertIn("URL:http://testserver/", self.fetch(j["path"]).text)
            finally:
                self.drop()
        with given.settings(PUBLIC_ORIGINS="http://sanguity.lan:8730, https://health.example.test, https://zz-second.example.test"):
            j = self.make(details="full")
            try:
                r = self.c["robin"].post(f"/api/profiles/{self.pid}/calendar/rotate", headers=H).json()
                self.assertEqual((j["base"], r["base"]), ("https://health.example.test/", "https://health.example.test/"))
                ics = self.fetch(r["path"]).text
                self.assertIn("URL:https://health.example.test/", ics)
                self.assertNotIn("sanguity.lan", ics)
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
        # however its person's access ended, the feed is gone for good once it's fetched (#590: it answered again
        # once they could view the profile again)
        j = self.make(who="lee")
        self.fetch(j["path"])
        db = dbm.connect()
        db.execute("DELETE FROM memberships WHERE user_id=? AND profile_id=?", (self.uid["lee"], self.pid))
        try:
            self.fetch(j["path"], 404)
        finally:
            db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (self.uid["lee"], self.pid))
        self.fetch(j["path"], 404)
        j = self.make(who="lee")
        db.execute("UPDATE users SET disabled=1 WHERE id=?", (self.uid["lee"],))
        try:
            self.fetch(j["path"], 404)
        finally:
            db.execute("UPDATE users SET disabled=0 WHERE id=?", (self.uid["lee"],))
            db.close()
        self.fetch(j["path"], 404)
        self.assertIsNone(self.c["lee"].get(f"/api/profiles/{self.pid}/calendar").json()["mine"])

    def test_a_feed_ends_for_good_when_access_ends(self):   # #590
        put = lambda role: self.assertEqual(self.c["robin"].put(f"/api/profiles/{self.pid}/members", json={"username": "lee", "role": role},
                                                                headers=H).status_code, 200)
        # removed by an owner, then added again: the old address stays dead, and nobody is left with a feed they can't see
        j = self.make(who="lee")
        self.assertEqual(self.c["robin"].delete(f"/api/profiles/{self.pid}/members/{self.uid['lee']}", headers=H).status_code, 200)
        self.assertNotIn("lee", {f["username"] for f in self.c["robin"].get(f"/api/profiles/{self.pid}/calendar").json()["everyone"]})
        put("editor")
        self.fetch(j["path"], 404)
        self.assertIsNone(self.c["lee"].get(f"/api/profiles/{self.pid}/calendar").json()["mine"])
        # one left behind by a removal before this fix, never fetched since: gone when they're added again
        j = self.make(who="lee")
        db = dbm.connect()
        try:
            db.execute("DELETE FROM memberships WHERE user_id=? AND profile_id=?", (self.uid["lee"], self.pid))
        finally:
            db.close()
        put("editor")
        self.fetch(j["path"], 404)
        # a change of role keeps it: they can still view the profile
        j = self.make(who="lee")
        put("viewer")
        self.fetch(j["path"])
        put("editor")
        self.fetch(j["path"])
        self.drop("lee")

    def test_a_feed_ends_with_sign_out_everywhere_a_reset_and_a_recovery_code(self):   # #590: each may follow a lost phone
        import time
        from server import twofactor
        code = lambda secret, ahead=0: twofactor._code(secret, int(time.time() // twofactor.PERIOD) + ahead)
        pw, pw2, admin_pw = "nell keeps a long passphrase", "nell keeps another long passphrase", "ops keeps a long passphrase"
        db = dbm.connect()
        nell = accounts.create(db, "nell", password=pw)[0]
        accounts.create(db, "ops", is_admin=True, password=admin_pw)
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (nell, self.pid))
        db.close()

        def signin(name, password):
            c = client()
            r = c.post("/api/login", json={"username": name, "password": password}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            return c, r.json()

        def feed(c):
            r = c.post(f"/api/profiles/{self.pid}/calendar", json={"tz": "UTC"}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            self.fetch(r.json()["path"])
            return r.json()["path"]

        # Sign out everywhere else, from another of her devices
        phone, _ = signin("nell", pw)
        laptop, _ = signin("nell", pw)
        path = feed(phone)
        r = laptop.post("/api/sessions/revoke-others", headers=H)
        self.assertEqual((r.status_code, r.json()["revoked"], r.json()["calendar_feeds"]), (200, 1, 1), r.text)
        self.fetch(path, 404)
        # an admin's reset of her password
        path = feed(laptop)
        ops, _ = signin("ops", admin_pw)   # an admin with a second factor of their own (admin routes need it)
        secret = ops.post("/api/me/2fa/start", json={"password": admin_pw}, headers=H).json()["secret"]
        self.assertEqual(ops.post("/api/me/2fa/confirm", json={"code": code(secret), "password": admin_pw}, headers=H).status_code, 200)
        r = ops.patch(f"/api/users/{nell}", json={"reset_password": True}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.fetch(path, 404)
        # signing in with a recovery code
        temp = r.json()["temporary_password"]
        c, _ = signin("nell", temp)
        self.assertEqual(c.post("/api/me/password", json={"current": temp, "new": pw2}, headers=H).status_code, 200)
        secret = c.post("/api/me/2fa/start", json={"password": pw2}, headers=H).json()["secret"]
        recovery = c.post("/api/me/2fa/confirm", json={"code": code(secret), "password": pw2}, headers=H).json()["recovery_codes"]
        path = feed(c)
        c2, first = signin("nell", pw2)
        self.assertEqual(c2.post("/api/login/code", json={"ticket": first["ticket"], "code": recovery[0]}, headers=H).status_code, 200)
        self.fetch(path, 404)
        # a code from the app ends nothing
        path = feed(c2)
        c3, first = signin("nell", pw2)
        self.assertEqual(c3.post("/api/login/code", json={"ticket": first["ticket"], "code": code(secret, 1)}, headers=H).status_code, 200)
        self.fetch(path)
        self.assertEqual(self.c["robin"].delete(f"/api/profiles/{self.pid}/members/{nell}", headers=H).status_code, 200)   # as it was
        self.fetch(path, 404)

    def test_a_feed_ends_with_a_password_change_and_a_passkeys_removal(self):   # #597, the owner's decision
        """Changing your own password signed every other device out but kept the account's calendar feeds, so an address
        someone else copied kept working; removing a passkey (whose device may be lost, its calendar app holding the
        addresses) the same. Both end them now, as Sign out everywhere else does (#590), and the account page is told how
        many beforehand (GET /api/me) and after. Turning off your own authenticator app keeps them, as before."""
        import time
        from server import twofactor
        pw, pw2 = "otto keeps a long passphrase", "otto keeps another long passphrase"
        db = dbm.connect()
        otto = accounts.create(db, "otto", password=pw)[0]
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (otto, self.pid))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (otto, self.other))
        db.close()
        c = client()
        self.assertEqual(c.post("/api/login", json={"username": "otto", "password": pw}, headers=H).status_code, 200)

        def feeds():   # one in each profile, fetched once each
            paths = []
            for pid in (self.pid, self.other):
                r = c.post(f"/api/profiles/{pid}/calendar", json={"tz": "UTC"}, headers=H)
                self.assertEqual(r.status_code, 200, r.text)
                self.fetch(r.json()["path"])
                paths.append(r.json()["path"])
            self.assertEqual(c.get("/api/me").json()["calendar_feeds"], 2, "said beforehand: how many there are")
            return paths

        # a password change
        paths = feeds()
        r = c.post("/api/me/password", json={"current": pw, "new": pw2}, headers=H)
        self.assertEqual((r.status_code, r.json().get("calendar_feeds")), (200, 2), r.text)
        for p in paths:
            self.fetch(p, 404)
        self.assertEqual(c.get("/api/me").json()["calendar_feeds"], 0)
        # a wrong current password changes nothing, feeds included
        paths = feeds()
        self.assertEqual(c.post("/api/me/password", json={"current": "not otto's password at all", "new": pw}, headers=H).status_code, 403)
        self.fetch(paths[0])
        # a passkey's removal
        db = dbm.connect()
        pk = db.execute("INSERT INTO passkeys(user_id, credential_id, public_key, rp_id, name, created) VALUES(?, 'otto-597', x'00', 'localhost', ?, 1)",
                        (otto, "Otto's phone (fictional)")).lastrowid
        db.close()
        r = c.request("DELETE", f"/api/me/passkeys/{pk}", json={"password": pw2}, headers=H)
        self.assertEqual((r.status_code, r.json().get("calendar_feeds")), (200, 2), r.text)
        for p in paths:
            self.fetch(p, 404)
        # turning off your own authenticator app keeps them (accounts.turn_off_2fa, deliberate)
        secret = c.post("/api/me/2fa/start", json={"password": pw2}, headers=H).json()["secret"]
        code = twofactor._code(secret, int(time.time() // twofactor.PERIOD))
        self.assertEqual(c.post("/api/me/2fa/confirm", json={"code": code, "password": pw2}, headers=H).status_code, 200)
        paths = feeds()
        self.assertEqual(c.post("/api/me/2fa/off", json={"password": pw2}, headers=H).status_code, 200)
        for p in paths:
            self.fetch(p)
        for pid in (self.pid, self.other):
            self.assertEqual(self.c["robin"].delete(f"/api/profiles/{pid}/members/{otto}", headers=H).status_code, 200)   # as it was
        for p in paths:
            self.fetch(p, 404)

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



# ---------------------------------------------------------------- medication reminders (#162)
MEDS = {"built": "2026-11-01T00:00:00+00:00", "source": "fictional meds",
        "events": [{"id": 1, "t": D(-200), "k": "med", "title": "Fictionamide 10 mg tablet", "sub": ""},
                   {"id": 2, "t": D(-300), "k": "med", "title": "Placebocillin 250 mg capsule", "sub": ""}],
        "details": {"1": {"type": "med", "instructions": "One tablet by mouth, with breakfast"},
                    "2": {"type": "med", "instructions": "Twice a day", "status": "Completed", "end": D(-250)}},
        "labs": [], "costs": []}


class Reminders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db = dbm.connect()
        cls.pid = prof.create_profile(db, "Mira Example (fictional)", owner_id=Calendar.uid["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (Calendar.uid["kim"], cls.pid))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (Calendar.uid["lee"], cls.pid))
        with dbm.transaction(db):
            cls.iid = records.store(db, cls.pid, MEDS, "dataset")
        db.close()
        cls.c = Calendar.c

    def setUp(self):
        calendar.throttle._fails.clear()
        db = dbm.connect()
        db.execute("DELETE FROM med_reminders")
        db.close()

    def path(self, rest=""):
        return f"/api/profiles/{self.pid}/medications/reminders{rest}"

    def set(self, who="lee", status=201, **body):
        r = self.c[who].post(self.path(), json={"med": "fictionamide", "times": ["08:00"], "days": DOW_L, "start": D(0),
                                                "tz": "America/Chicago", **body}, headers=H)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def feed(self, **body):
        r = self.c["robin"].post(f"/api/profiles/{self.pid}/calendar", json={"tz": "America/Chicago", **body}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        try:
            text = client().get("/" + r.json()["path"]).text
        finally:
            self.c["robin"].delete(f"/api/profiles/{self.pid}/calendar", headers=H)
        return text, parse(text)

    def reminders(self, cal):
        return [e for e in cal["events"] if "RRULE" in e]

    def test_daily_through_clock_changes(self):
        self.set(times=["20:00", "08:00"], until=D(400))
        text, cal = self.feed()
        evs = self.reminders(cal)
        self.assertEqual(len(evs), 2, "one recurring event for each time of day")
        self.assertEqual(sorted(e["DTSTART"][1][-6:] for e in evs), ["080000", "200000"])
        zone = cal["zones"]["America/Chicago"]
        self.assertEqual({o["kind"] for o in zone}, {"STANDARD", "DAYLIGHT"})
        from zoneinfo import ZoneInfo
        chicago = ZoneInfo("America/Chicago")
        for ev in evs:
            self.assertEqual(ev["DTSTART"][0], ["TZID=America/Chicago"])
            self.assertEqual(ev["DTEND"][0], ["TZID=America/Chicago"])
            self.assertEqual(ev["RRULE"][1].split(";")[0], "FREQ=DAILY")
            got = instances(ev, cal["zones"])
            self.assertEqual(len(got), 401, "every day from today to the until day, that day included")
            self.assertEqual(got[0][0].date(), TODAY)
            self.assertEqual(got[-1][0].date(), TODAY + timedelta(days=400))
            hour = int(ev["DTSTART"][1][9:11])
            self.assertTrue(all(w.hour == hour and w.minute == 0 for w, _ in got), "the same wall-clock time every day")
            for wall, utc in got:   # each instant as the tz database has it
                self.assertEqual(utc, wall.replace(tzinfo=chicago).astimezone(timezone.utc).replace(tzinfo=None), wall)
            self.assertEqual(len({utc.hour for _, utc in got}), 2, "the UTC hour moves when the clocks change, the local one doesn't")
            self.assertIn("VALARM", ev)
            self.assertEqual((ev["VALARM"]["ACTION"], ev["VALARM"]["TRIGGER"]), ("DISPLAY", "PT0M"))
            self.assertEqual((ev["TRANSP"][1], ev["CLASS"][1]), ("TRANSPARENT", "PRIVATE"))
        self.assertLess(text.index("BEGIN:VTIMEZONE"), text.index("BEGIN:VEVENT"))

    def test_minimal_and_full_wording(self):
        self.set()
        text, cal = self.feed()
        ev = self.reminders(cal)[0]
        self.assertEqual(unescape(ev["SUMMARY"][1]), "Medication")
        self.assertEqual(unescape(ev["VALARM"]["DESCRIPTION"]), "Medication")
        for word in ("Fictionamide", "fictionamide", "tablet", "breakfast", "Mira"):
            self.assertNotIn(word, text)
        text, cal = self.feed(details="full")
        ev = self.reminders(cal)[0]
        self.assertEqual(unescape(ev["SUMMARY"][1]), "Fictionamide 10 mg tablet")
        desc = unescape(ev["DESCRIPTION"][1])
        self.assertIn("As the records list it: One tablet by mouth, with breakfast", desc)
        for word in ("due", "should", "take ", "must"):
            self.assertNotIn(word, desc.lower(), "never says a dose is due or should be taken")
        text, cal = self.feed(meds=False)
        self.assertEqual(self.reminders(cal), [], "a feed without reminders")
        self.assertNotIn("VTIMEZONE", text)

    def test_weekly_days_and_first_day(self):
        self.set(days=["mo", "we", "fr"], times=["07:30"], until=D(60))
        _, cal = self.feed()
        ev = self.reminders(cal)[0]
        self.assertEqual(ev["RRULE"][1].split(";")[:2], ["FREQ=WEEKLY", "BYDAY=MO,WE,FR"])
        first = _local(ev["DTSTART"][1])
        self.assertIn(first.weekday(), (0, 2, 4), "DTSTART is one of the rule's own days (RFC 5545 3.8.5.3)")
        self.assertLess((first.date() - TODAY).days, 3)
        got = instances(ev, cal["zones"])
        self.assertTrue(all(w.weekday() in (0, 2, 4) for w, _ in got))
        self.assertLessEqual(got[-1][0].date(), TODAY + timedelta(days=60))
        self.assertGreater(got[-1][0].date(), TODAY + timedelta(days=60 - 3))

    def test_a_medication_stopped_in_the_records_ends_it(self):
        self.set(until=D(300))
        db = dbm.connect()
        with dbm.transaction(db):
            later = records.store(db, self.pid, {"events": [{"id": 9, "t": D(10), "k": "med", "title": "Fictionamide 10 mg tablet", "sub": ""}],
                                                 "details": {"9": {"type": "med", "instructions": "One tablet by mouth, with breakfast",
                                                                   "status": "Stopped", "end": D(20)}}, "labs": [], "costs": []}, "upload")
        db.close()
        try:
            _, cal = self.feed()
            got = instances(self.reminders(cal)[0], cal["zones"])
            self.assertEqual(got[-1][0].date(), TODAY + timedelta(days=20), "until the day the records say it stopped")
            data = self.c["kim"].get(f"/api/profiles/{self.pid}/data").json()
            med = next(m for m in data["medications"] if m["key"] == "fictionamide")
            self.assertEqual(med["stop"], D(20))
            self.assertEqual(len(data["reminders"]), 1, "the page has the reminder, to say it ended")
            # a medication that stopped: no new reminders for it
            r = self.c["lee"].post(self.path(), json={"med": "fictionamide", "times": ["09:00"], "days": DOW_L, "start": D(0)}, headers=H)
            self.assertEqual(r.status_code, 400)
            self.assertIn("current", r.json()["detail"])
        finally:
            db = dbm.connect()
            with dbm.transaction(db):
                records.drop(db, self.pid, [later])
            db.close()

    def test_off_ended_and_undone(self):
        j = self.set()
        rid = j["id"]
        r = self.c["lee"].patch(self.path(f"/{rid}"), json={"on": False}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.reminders(self.feed()[1]), [], "turned off: not listed")
        self.c["lee"].patch(self.path(f"/{rid}"), json={"on": True}, headers=H)
        self.assertEqual(len(self.reminders(self.feed()[1])), 1)
        # an until day already past: over (two days back, so it's past in Chicago too, whose today the feed uses,
        # while this machine's day is already the next one there)
        r = self.c["lee"].put(self.path(f"/{rid}"), json={"med": "fictionamide", "times": ["08:00"], "days": DOW_L,
                                                         "start": D(-30), "until": D(-2), "tz": "America/Chicago"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.reminders(self.feed()[1]), [])
        # the medication's import undone: the reminder is kept, and out of the feed until the medication is back
        self.set()
        db = dbm.connect()
        with dbm.transaction(db):
            records.drop(db, self.pid, [self.iid])
        db.close()
        try:
            self.assertEqual(len(self.c["kim"].get(self.path()).json()["reminders"]), 2)
            self.assertEqual(self.reminders(self.feed()[1]), [])
        finally:
            db = dbm.connect()
            with dbm.transaction(db):
                type(self).iid = records.store(db, self.pid, MEDS, "dataset")
            db.close()
        self.assertEqual(len(self.reminders(self.feed()[1])), 1, "back with the medication")

    def test_replies_carry_what_changed(self):
        """#271: each change's reply has the reminder as the records list it, so the page updates in place"""
        j = self.set()
        listed = lambda: {r["id"]: r for r in self.c["kim"].get(f"/api/profiles/{self.pid}/data").json()["reminders"]}
        self.assertEqual(j["reminders"], [listed()[j["id"]]])
        r = self.c["lee"].patch(self.path(f"/{j['id']}"), json={"on": False}, headers=H)
        self.assertEqual(r.json()["reminders"], [listed()[j["id"]]])
        self.assertFalse(r.json()["reminders"][0]["on"])
        r = self.c["lee"].put(self.path(f"/{j['id']}"), json={"med": "fictionamide", "times": ["09:00"], "days": DOW_L, "start": D(0),
                                                             "tz": "America/Chicago"}, headers=H)
        self.assertEqual(r.json()["reminders"], [listed()[j["id"]]])
        self.assertEqual(r.json()["reminders"][0]["times"], ["09:00"])
        r = self.c["lee"].delete(self.path(f"/{j['id']}"), headers=H)
        self.assertEqual(r.json(), {"ok": True, "removed": [j["id"]]})

    def test_replies_say_the_records_revision(self):
        """#405: each change's reply says the records' revision after it, as the page's data does, so the offline copy
        can follow the change"""
        rev = lambda: self.c["kim"].get(f"/api/profiles/{self.pid}/data").headers["X-Records-Rev"]
        before = rev()
        r = self.c["lee"].post(self.path(), json={"med": "fictionamide", "times": ["08:00"], "days": DOW_L, "start": D(0)}, headers=H)
        self.assertEqual(r.headers["X-Records-Rev"], rev())
        self.assertNotEqual(r.headers["X-Records-Rev"], before)
        rid = r.json()["id"]
        for change in (lambda: self.c["lee"].patch(self.path(f"/{rid}"), json={"on": False}, headers=H),
                       lambda: self.c["lee"].put(self.path(f"/{rid}"), json={"med": "fictionamide", "times": ["09:00"], "days": DOW_L, "start": D(0)}, headers=H),
                       lambda: self.c["lee"].delete(self.path(f"/{rid}"), headers=H)):
            r = change()
            self.assertEqual((r.status_code, r.headers["X-Records-Rev"]), (200, rev()))

    def test_who_and_refusals(self):
        self.set(who="kim", status=403)
        self.set(who="robin")
        bad = [({"med": "placebocillin"}, "current"), ({"med": "no such"}, "current"), ({"times": []}, "time"),
               ({"times": ["8am"]}, "HH:MM"), ({"times": ["24:00"]}, "HH:MM"), ({"days": []}, "day"), ({"days": ["xx"]}, "day"),
               ({"start": "2026-02-30"}, "exist"), ({"until": D(-5)}, "before"), ({"tz": "Nowhere/Fictional"}, "time zone"),
               ({"times": ["08:00"] * 25}, "At most")]
        for body, word in bad:
            r = self.set(status=400, **body)
            self.assertIn(word, r["detail"], (body, r))
        self.assertEqual(self.c["lee"].delete(self.path("/99999"), headers=H).status_code, 404)

    def test_stored_encrypted_and_logged_without_the_medication(self):
        j = self.set()
        db = dbm.connect()
        row = db.execute("SELECT * FROM med_reminders WHERE id=?", (j["id"],)).fetchone()
        db.close()
        self.assertNotIn("fictionamide", " ".join(str(v) for v in tuple(row)).lower())
        self.assertNotIn("08:00", row["body"])
        log = self.c["robin"].get(f"/api/profiles/{self.pid}/activity?action=medication").json()
        entries = log.get("entries", log) if isinstance(log, dict) else log
        self.assertTrue(entries)
        self.assertNotIn("fictionamide", str(entries).lower())
        self.assertEqual(self.c["lee"].delete(self.path(f"/{j['id']}"), headers=H).status_code, 200)
        self.assertEqual(self.c["kim"].get(self.path()).json()["reminders"], [])

    def test_default_zone_is_the_persons(self):
        r = self.c["lee"].put("/api/settings/user/dates.timeZone", json={"value": "Europe/Paris"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        try:
            j = self.set(tz=None)
            self.assertEqual(j["reminder"]["tz"], "Europe/Paris", "the person's own zone (#42)")
        finally:
            self.c["lee"].put("/api/settings/user/dates.timeZone", json={"value": ""}, headers=H)

    def test_floating_when_the_zone_is_unknown(self):
        from server import med_reminders as mr
        r = {"from": "2026-03-02", "days": list(mr.DAYS), "tz": "Nowhere/Fictional"}
        lines = mr.recurrence(r, "08:15", date(2026, 3, 31), None)
        self.assertEqual(lines[0], "DTSTART:20260302T081500")
        self.assertEqual(lines[2], "RRULE:FREQ=DAILY;UNTIL=20260331T235959", "floating DTSTART, floating UNTIL")
        self.assertIsNone(mr.recurrence(r, "08:15", date(2026, 3, 1), None), "an end before the first day: none")
        self.assertIsNone(mr.vtimezone("Nowhere/Fictional", 2026, 2027))

    def test_vtimezones_against_the_tz_database(self):
        from server import med_reminders as mr
        from zoneinfo import ZoneInfo
        for name in ("America/Chicago", "Europe/London", "Australia/Sydney", "Asia/Kolkata", "America/Sao_Paulo",
                     "Pacific/Chatham", "UTC"):
            lines = mr.vtimezone(name, 2015, 2030)
            text = "BEGIN:VCALENDAR\r\n" + "".join(calendar.fold(l) for l in lines) + "END:VCALENDAR\r\n"
            zone = parse(text)["zones"][name]
            tz = ZoneInfo(name)
            d = datetime(2015, 1, 3, 8, 0)
            while d.year <= 2030:   # every fourth day at 08:00 and 20:00, local
                for wall in (d, d.replace(hour=20)):
                    want = wall.replace(tzinfo=tz).utcoffset()
                    self.assertEqual(offset_at(zone, wall, 2031), want, (name, wall))
                d += timedelta(days=4)
        sp = mr.vtimezone("America/Sao_Paulo", 2015, 2030)
        self.assertTrue(any(l.startswith("RRULE:") and "UNTIL=" in l for l in sp), "Brazil's clock changes ended in 2019")
        self.assertEqual(sum(l.startswith("BEGIN:STANDARD") for l in mr.vtimezone("Asia/Kolkata", 2026, 2027)), 1)

    # ---- a medication's schedule (#1094): the reminders' lines, with amounts, several a medication, As needed

    def test_a_schedule_of_three_times_with_amounts(self):
        tabs = [{"amount": 2, "unit": "tablets"}, {"amount": 1, "unit": "tablet"}, {"amount": 0.5, "unit": "ml"}]
        j = self.set(times=["20:00", "08:00", "13:00"], amounts=[tabs[2], tabs[0], tabs[1]], until=D(30))
        got = j["reminders"][0]
        self.assertEqual(got["times"], ["08:00", "13:00", "20:00"], "in order of the day, each time with its own amount")
        self.assertEqual(got["amounts"], [{"amount": 2, "unit": "tablets"}, {"amount": 1, "unit": "tablet"}, {"amount": 0.5, "unit": "ml"}])
        self.assertFalse(got["asneeded"])
        _, cal = self.feed(details="full")
        evs = sorted(self.reminders(cal), key=lambda e: e["DTSTART"][1])
        self.assertEqual([unescape(e["SUMMARY"][1]) for e in evs], ["Fictionamide 10 mg tablet · 2 tablets",
                         "Fictionamide 10 mg tablet · 1 tablet", "Fictionamide 10 mg tablet · 0.5 ml"], "the amount at each time")
        self.assertIn("Amount: 2 tablets", unescape(evs[0]["DESCRIPTION"][1]))
        self.assertEqual(unescape(evs[0]["VALARM"]["DESCRIPTION"]), "Fictionamide 10 mg tablet · 2 tablets")
        text, cal = self.feed()
        self.assertEqual({unescape(e["SUMMARY"][1]) for e in self.reminders(cal)}, {"Medication"}, "minimal: no medication, no amount")
        self.assertNotIn("tablets", text)
        # two doses at one time: two in the schedule, one event saying both
        self.set(times=["08:00", "08:00"], amounts=[{"amount": 1, "unit": "tablet"}, {"amount": 5, "unit": "ml"}])
        lines = self.c["kim"].get(self.path()).json()["reminders"]
        self.assertEqual(lines[-1]["times"], ["08:00", "08:00"])
        _, cal = self.feed(details="full")
        self.assertIn("Fictionamide 10 mg tablet · 1 tablet and 5 ml", [unescape(e["SUMMARY"][1]) for e in self.reminders(cal)])
        # two alike: "2 doses of 1 tablet", not "1 tablet and 1 tablet" (#1095's check); with no amounts, "2 doses"
        self.set(times=["09:00", "09:00"], amounts=[{"amount": 1, "unit": "tablet"}] * 2)
        self.set(times=["10:00", "10:00"])
        said = [unescape(e["SUMMARY"][1]) for e in self.reminders(self.feed(details="full")[1])]
        self.assertIn("Fictionamide 10 mg tablet · 2 doses of 1 tablet", said)
        self.assertIn("Fictionamide 10 mg tablet · 2 doses", said)
        self.assertFalse([x for x in said if "1 tablet and 1 tablet" in x], said)
        bad = [({"amounts": [{"amount": 1}]}, "each time"), ({"amounts": [{"amount": 0}]}, "above 0"), ({"amounts": [{"amount": -2}]}, "above 0"),
               ({"amounts": [{"amount": 1, "unit": "x" * 41}]}, "too long"), ({"times": ["08:00"] * 25}, "At most")]
        for body, word in bad:
            if "times" not in body:
                body = {"times": ["08:00", "20:00"] if word == "each time" else ["08:00"], **body}
            self.assertIn(word, self.set(status=400, **body)["detail"], body)

    def test_lines_with_their_own_days(self):
        """The owner's two (#1094): every day at 8:00 and Mondays and Fridays at 20:00; and Mondays and Fridays only,
        morning and night. Each line's times on its own days, in the feed"""
        a = self.set(times=["08:00"], amounts=[{"amount": 1, "unit": "tablet"}], until=D(60))["id"]
        b = self.set(times=["20:00"], days=["mo", "fr"], amounts=[{"amount": 2, "unit": "tablets"}], until=D(60))["id"]
        _, cal = self.feed(details="full")
        evs = {_local(e["DTSTART"][1]).strftime("%H:%M"): e for e in self.reminders(cal)}
        self.assertEqual(sorted(evs), ["08:00", "20:00"])
        self.assertEqual(evs["08:00"]["RRULE"][1].split(";")[0], "FREQ=DAILY")
        self.assertEqual(evs["20:00"]["RRULE"][1].split(";")[:2], ["FREQ=WEEKLY", "BYDAY=MO,FR"])
        self.assertEqual(len(instances(evs["08:00"], cal["zones"])), 61)
        nights = instances(evs["20:00"], cal["zones"])
        self.assertTrue(nights and all(w.weekday() in (0, 4) and w.hour == 20 for w, _ in nights), "only Mondays and Fridays")
        self.assertEqual(unescape(evs["20:00"]["SUMMARY"][1]), "Fictionamide 10 mg tablet · 2 tablets")
        for rid in (a, b):
            self.c["lee"].delete(self.path(f"/{rid}"), headers=H)
        self.set(times=["20:00", "07:30"], days=["fr", "mo"], amounts=[{"amount": 1, "unit": "tablet"}] * 2, until=D(30))
        _, cal = self.feed()
        evs = self.reminders(cal)
        self.assertEqual(len(evs), 2, "morning and night")
        for e in evs:
            self.assertEqual(e["RRULE"][1].split(";")[:2], ["FREQ=WEEKLY", "BYDAY=MO,FR"])
            self.assertTrue(all(w.weekday() in (0, 4) for w, _ in instances(e, cal["zones"])))

    def test_as_needed(self):
        j = self.set(times=[], asneeded=True, dose={"amount": 2, "unit": "puffs"}, on=False)
        got = j["reminders"][0]
        self.assertEqual((got["asneeded"], got["times"], got["dose"]), (True, [], {"amount": 2, "unit": "puffs"}))
        self.set(times=["08:00"], asneeded=True)   # its times dropped: As needed has none
        self.assertEqual(self.c["kim"].get(self.path()).json()["reminders"][-1]["times"], [])
        self.assertEqual(self.reminders(self.feed()[1]), [], "As needed: nothing to remind of")
        self.assertIn("above 0", self.set(status=400, times=[], asneeded=True, dose={"amount": 0})["detail"])

    def test_reminders_from_before_become_schedules(self):
        """Schema step 47, from a schema-45 database: each reminder a line of its medication's schedule, its times
        with no amount; one on stays on (Remind me), one turned off is a paused line, Remind me on to resume as it was
        (the owner, on #1090); the feed lists the one on's events, and the paused one's once it's resumed"""
        import json
        from server import config, crypto, med_reminders as mr
        path = os.path.join(TMP, "before-1094.db")
        old = config.DB_PATH
        try:
            config.DB_PATH = path
            schema = dbm.SCHEMA
            here = dbm.SCHEMA.index(next(s for s in dbm.SCHEMA if "RENAME COLUMN active TO remind" in s))
            self.assertEqual(here, 46, "steps 46 and 47 still to run: a schema-45 database")
            dbm.SCHEMA = schema[:here - 1]
            try:
                db = dbm.connect()
                dbm.upgrade(db)
            finally:
                dbm.SCHEMA = schema
            db.execute("PRAGMA foreign_keys=OFF")
            body = lambda times, days: crypto.enc(json.dumps({"med": "fictionamide", "name": "Fictionamide 10 mg tablet", "times": times,
                                                              "days": days, "from": D(0), "until": D(40), "tz": "America/Chicago"}))
            db.execute("INSERT INTO med_reminders(id, profile_id, body, active, created_name, created) VALUES(1, ?, ?, 1, 'lee', 1)",
                       (self.pid, body(["08:00", "20:00"], ["mo", "we", "fr"])))
            db.execute("INSERT INTO med_reminders(id, profile_id, body, active, created_name, created) VALUES(2, ?, ?, 0, 'lee', 1)",
                       (self.pid, body(["12:00"], DOW_L)))
            dbm.upgrade(db)
            self.assertIn("remind", [c[1] for c in db.execute("PRAGMA table_info(med_reminders)")])
            got = mr.listing(db, self.pid)
            self.assertEqual([(r["times"], r["amounts"], r["days"], r["on"], r["paused"], r["asneeded"]) for r in got],
                             [(["08:00", "20:00"], [{"amount": None, "unit": ""}] * 2, ["mo", "we", "fr"], True, False, False),
                              (["12:00"], [{"amount": None, "unit": ""}], DOW_L, True, True, False)])
            db.close()
        finally:
            config.DB_PATH = old
        # the same lines in the profile's own database: the feed reminds as before, the one turned off (paused) not at all
        db = dbm.connect()
        ids = [db.execute("INSERT INTO med_reminders(profile_id, body, remind, paused, created_name, created) VALUES(?, ?, ?, ?, 'lee', 1)",
                          (self.pid, crypto.enc(json.dumps({k: r[k] for k in ("med", "name", "times", "days", "from", "until", "tz")})),
                           int(r["on"]), int(r["paused"]))).lastrowid for r in got]
        db.close()
        _, cal = self.feed()
        evs = sorted(self.reminders(cal), key=lambda e: e["DTSTART"][1])
        self.assertEqual(len(evs), 2, "the one on: its two times; the paused one: none")
        for e, at in zip(evs, ("08:00", "20:00")):
            want = mr.recurrence({**got[0], "tz": "America/Chicago"}, at, date.fromisoformat(D(40)), mr._zone("America/Chicago"))
            self.assertEqual([e["DTSTART"][1], e["RRULE"][1]], [want[0].split(":", 1)[1], want[2].split(":", 1)[1]])
            self.assertEqual(unescape(e["SUMMARY"][1]), "Medication")
        # resumed, it reminds as it did; paused again, it's out again; Remind me still turns on and off by itself
        r = self.c["lee"].patch(self.path(f"/{ids[1]}"), headers=H, json={"paused": False})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["reminders"][0]["paused"], r.json()["reminders"][0]["on"]), (False, True))
        self.assertEqual(len(self.reminders(self.feed()[1])), 3, "resumed: its 12:00 too")
        self.assertEqual(self.c["lee"].patch(self.path(f"/{ids[1]}"), headers=H, json={"paused": True}).status_code, 200)
        self.assertEqual(len(self.reminders(self.feed()[1])), 2, "paused again")
        r = self.c["lee"].patch(self.path(f"/{ids[1]}"), headers=H, json={"on": False})
        self.assertEqual((r.json()["reminders"][0]["paused"], r.json()["reminders"][0]["on"]), (True, False), "still paused")
        self.assertEqual(self.c["lee"].patch(self.path(f"/{ids[1]}"), headers=H, json={}).status_code, 400)


class MonthArithmetic(unittest.TestCase):
    """(#1138, #1133) "in N months" is calendar months: the same day, or the month's last day where it has fewer"""
    def test_calendar_months_clamped_to_the_month_end(self):
        for d, n, want in (("2026-01-31", 1, "2026-02-28"), ("2028-01-31", 1, "2028-02-29"), ("2026-03-31", 1, "2026-04-30"),
                           ("2026-01-15", 1, "2026-02-15"), ("2026-11-30", 3, "2027-02-28"), ("2026-12-31", 2, "2027-02-28"),
                           ("2026-01-31", 3, "2026-04-30"), ("2026-05-31", 12, "2027-05-31"), ("2026-08-31", 18, "2028-02-29")):
            self.assertEqual(calendar.add_months(date.fromisoformat(d), n).isoformat(), want, (d, n))
        self.assertEqual(calendar.add(date(2026, 3, 3), 2, "weeks"), date(2026, 3, 17))
        self.assertEqual(calendar.add(date(2026, 3, 3), 30, "days"), date(2026, 4, 2))


class FollowUps(unittest.TestCase):
    """Follow-ups from a visit (#1138): added from a visit ("in 3 months" from its day) or with a date; viewers can't
    write; in the calendar feed as "Sanguity follow-up" unless that one shows its words, done ones and those with no
    date left out; in the records, encrypted, in Export everything and put back from it"""
    @classmethod
    def setUpClass(cls):
        db = dbm.connect()
        cls.pid = prof.create_profile(db, "Ada Example (fictional)", owner_id=Calendar.uid["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (Calendar.uid["kim"], cls.pid))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (Calendar.uid["lee"], cls.pid))
        db.close()
        cls.c = Calendar.c

    def setUp(self):
        calendar.throttle._fails.clear()
        db = dbm.connect()
        db.execute("DELETE FROM followups")
        db.close()

    def add(self, who="lee", status=201, **body):
        r = self.c[who].post(f"/api/profiles/{self.pid}/followups", json=body, headers=H)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def feed(self):
        r = self.c["robin"].post(f"/api/profiles/{self.pid}/calendar", json={"tz": "UTC", "details": "full"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        try:
            text = client().get("/" + r.json()["path"]).text
        finally:
            self.c["robin"].delete(f"/api/profiles/{self.pid}/calendar", headers=H)
        return text, [e for e in parse(text)["events"] if "RRULE" not in e]

    def test_added_from_a_visit_and_listed_in_the_records(self):
        j = self.add(text="  Repeat   bloodwork ", in_n=3, in_unit="months", about={"id": 7, "title": "Annual physical", "t": "2026-11-30T09:00:00"})
        self.assertEqual(j["records"]["imports"], {}, "a profile with nothing imported: records of its follow-ups")
        (f,) = j["records"]["followups"]
        self.assertEqual((f["text"], f["due"], f["said"], f["from"], f["about"], f["show"], f["done"], f["by"]),
                         ("Repeat bloodwork", "2027-02-28", "in 3 months", "2026-11-30", {"title": "Annual physical", "id": 7, "t": "2026-11-30"},
                          False, False, "lee"))
        self.add(who="kim", status=403, text="A viewer's")
        self.add(status=400, text="")
        self.add(status=400, text="x", due="2026-02-30")
        self.add(status=400, text="x", in_n=0, in_unit="weeks")
        # one week, said as such; no visit: from the day it's added
        j = self.add(text="Call the clinic", in_n=1, in_unit="weeks", **{"from": "2026-03-03"})
        f = next(x for x in j["records"]["followups"] if x["id"] == j["id"])
        self.assertEqual((f["due"], f["said"]), ("2026-03-10", "in 1 week"))
        # done, then changed: its wording kept while its date is
        r = self.c["lee"].put(f"/api/profiles/{self.pid}/followups/{j['id']}", json={**f, "done": True}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        f2 = next(x for x in r.json()["records"]["followups"] if x["id"] == j["id"])
        self.assertEqual((f2["done"], f2["said"]), (True, "in 1 week"))
        self.assertIsInstance(f2["done_at"], int)
        r = self.c["lee"].delete(f"/api/profiles/{self.pid}/followups/{j['id']}", headers=H)
        self.assertEqual(r.json()["removed"]["text"], "Call the clinic")
        self.assertEqual(self.c["lee"].delete(f"/api/profiles/{self.pid}/followups/{j['id']}", headers=H).status_code, 404)
        db = dbm.connect()
        try:
            for (body,) in db.execute("SELECT body FROM followups"):
                self.assertNotIn("bloodwork", body)
            acts = [json.loads(crypto.dec(d)) for (d,) in db.execute("SELECT detail FROM activity WHERE action='followup' AND profile_id=?", (self.pid,))]
            self.assertEqual([a["change"] for a in acts], ["add", "add", "done", "delete"])
        finally:
            db.close()

    def test_feed_wording_both_ways_and_done_ones_left_out(self):
        self.add(text="Book the MRI", due=D(10))
        self.add(text="Ask about the rash", due=D(20), show=True)
        done = self.add(text="Pick up the referral", due=D(5), done=True)
        self.add(text="Someday", show=True)
        text, evs = self.feed()
        got = sorted((e["DTSTART"][1], unescape(e["SUMMARY"][1])) for e in evs)
        self.assertEqual([s for _, s in got], ["Sanguity follow-up", "Ask about the rash"])
        self.assertEqual(got[0][0], (TODAY + timedelta(days=10)).strftime("%Y%m%d"))
        for word in ("Book the MRI", "MRI", "referral", "Someday"):
            self.assertNotIn(word, text, "a follow-up's words only where it shows them (even with full details), done ones left out")
        r = self.c["lee"].put(f"/api/profiles/{self.pid}/followups/{done['id']}", json={"text": "Pick up the referral", "due": D(5)}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(self.feed()[1]), 3, "not done again: back in the feed")

    def test_in_the_export_and_put_back_from_it(self):
        from server import export, followups
        self.add(text="Repeat bloodwork", in_n=6, in_unit="weeks", **{"from": "2026-01-05"})
        db = dbm.connect()
        try:
            data = export.plan(db, self.pid)["data"]
            (x,) = data["followups"]
            self.assertEqual((x["text"], x["due"], x["said"]), ("Repeat bloodwork", "2026-02-16", "in 6 weeks"))
            self.assertEqual(export.brings(data), {"followups": 1})
            other = prof.create_profile(db, "Restored (fictional)", owner_id=Calendar.uid["robin"])
            with dbm.transaction(db):
                n = export.restore(db, other, data, lambda f: None, [])
            self.assertEqual((n["followups"], n["here"]), (1, 0))
            with dbm.transaction(db):
                self.assertEqual(export.restore(db, other, data, lambda f: None, [])["here"], 1, "not twice")
            (y,) = followups.listing(db, other)
            self.assertEqual((y["text"], y["due"], y["said"], y["by"]), ("Repeat bloodwork", "2026-02-16", "in 6 weeks", "lee"))
            with self.assertRaises(ValueError):
                export.check_parts({"followups": [{"text": ""}]})
        finally:
            db.close()


class RecordsRequests(unittest.TestCase):
    """Records requests (#1133): the response date under each rule (HIPAA 30 days and 30 more; GDPR one calendar month
    and two more, clamped to the month's end; the person's own days and extension), the sent day today in the person's
    time zone, linking to an import of the same profile (received on its day) and undone with it, viewers can't write,
    in the calendar feed worded as follow-ups are, encrypted, and in Export everything and put back from it"""
    @classmethod
    def setUpClass(cls):
        db = dbm.connect()
        cls.pid = prof.create_profile(db, "Bea Example (fictional)", owner_id=Calendar.uid["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (Calendar.uid["kim"], cls.pid))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (Calendar.uid["lee"], cls.pid))
        db.close()
        cls.c = Calendar.c

    def setUp(self):
        calendar.throttle._fails.clear()
        db = dbm.connect()
        db.execute("DELETE FROM records_requests")
        db.close()

    def add(self, who="lee", want=201, **body):
        r = self.c[who].post(f"/api/profiles/{self.pid}/requests", json={"to": "Riverside Clinic (fictional)", **body}, headers=H)
        self.assertEqual(r.status_code, want, r.text)
        return r.json()

    def one(self, j):
        return next(x for x in j["records"]["requests"] if x["id"] == j["id"])

    def test_deadlines_under_each_rule(self):
        from server import rec_requests as rq
        for rule, sent, extra, first, ext in (("hipaa", "2026-03-03", {}, "2026-04-02", "2026-05-02"),
                                              ("hipaa", "2026-12-15", {}, "2027-01-14", "2027-02-13"),
                                              ("gdpr", "2026-01-31", {}, "2026-02-28", "2026-04-30"),
                                              ("gdpr", "2028-01-31", {}, "2028-02-29", "2028-04-30"),
                                              ("gdpr", "2026-03-15", {}, "2026-04-15", "2026-06-15"),
                                              ("gdpr", "2026-11-30", {}, "2026-12-30", "2027-02-28"),
                                              ("other", "2026-03-03", {"days": 45}, "2026-04-17", None),
                                              ("other", "2026-03-03", {"days": 10, "ext_days": 5}, "2026-03-13", "2026-03-18")):
            b = rq.check({"to": "x", "rule": rule, "sent": sent, **extra}, date.today())
            self.assertEqual([d and d.isoformat() for d in rq.due(b)], [first, ext], (rule, sent))
        j = self.add(rule="gdpr", sent="2026-01-31")
        x = self.one(j)
        self.assertEqual((x["due"], x["due_first"], x["due_ext"], x["rule_words"], x["status"]), ("2026-02-28", "2026-02-28", "2026-04-30", "UK/EU GDPR, one month", "waiting"))
        r = self.c["lee"].put(f"/api/profiles/{self.pid}/requests/{j['id']}", json={**x, "status": "extended"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        x = self.one({**r.json(), "id": j["id"]})
        self.assertEqual((x["due"], x["extended"]), ("2026-04-30", True), "extended: two months more, from the request")
        self.assertEqual(self.one(self.add(rule="hipaa", sent="2026-03-03", extended=True))["due"], "2026-05-02")
        self.add(want=400, rule="other", sent="2026-03-03")   # no days
        self.add(want=400, rule="other", sent="2026-03-03", days=10, extended=True)   # an extension it hasn't got
        self.add(want=400, rule="nowhere")
        self.add(want=400, to=" ")
        self.add(want=400, sent="2026-02-30")
        self.add(who="kim", want=403)

    def test_sent_today_in_the_persons_time_zone(self):
        from zoneinfo import ZoneInfo
        from server import rec_requests as rq
        for zone in ("Pacific/Kiritimati", "Pacific/Pago_Pago"):   # 25 hours apart: never the same day
            r = self.c["lee"].put("/api/settings/user/dates.timeZone", json={"value": zone}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            x = self.one(self.add(rule="gdpr"))
            today = datetime.now(ZoneInfo(zone)).date()
            self.assertEqual((x["sent"], x["due"]), (today.isoformat(), calendar.add_months(today, 1).isoformat()), zone)
        self.c["lee"].delete("/api/settings/user/dates.timeZone", headers=H)

    def test_linked_to_an_import_and_undone_with_it(self):
        x = self.one(self.add(rule="hipaa", sent="2026-03-03"))
        db = dbm.connect()
        try:
            with dbm.transaction(db):
                iid = records.store(db, self.pid, {"events": [{"id": 1, "t": "2026-03-20", "k": "visit", "title": "A visit"},
                                                              {"id": 2, "t": "2026-03-21", "k": "visit", "title": "Another"}], "details": {}}, "upload")
                other = records.store(db, Calendar.other, {"events": [], "details": {}}, "upload")
            day = datetime.fromtimestamp(db.execute("SELECT at FROM imports WHERE id=?", (iid,)).fetchone()[0], timezone.utc).date().isoformat()
        finally:
            db.close()
        path = f"/api/profiles/{self.pid}/requests/{x['id']}/import"
        self.assertEqual(self.c["lee"].put(path, json={"import_id": other}, headers=H).status_code, 404, "another profile's import")
        self.assertEqual(self.c["kim"].put(path, json={"import_id": iid}, headers=H).status_code, 403)
        r = self.c["lee"].put(path, json={"import_id": iid}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        y = self.one({**r.json(), "id": x["id"]})
        self.assertEqual((y["status"], y["received"], y["import"]["id"], y["import"]["records"]), ("received", day, iid, 2))
        self.assertEqual(self.c["lee"].get(f"/api/profiles/{self.pid}/requests", headers=H).json()["requests"][0]["import"]["id"], iid)
        self.assertEqual(self.c["kim"].get(f"/api/profiles/{self.pid}/requests", headers=H).status_code, 200)
        r = self.c["lee"].post(f"/api/profiles/{self.pid}/imports/{iid}/undo", headers=H)
        db = dbm.connect()
        try:
            if r.status_code != 200:   # however the import is undone, its link goes with it
                with dbm.transaction(db):
                    db.execute("DELETE FROM imports WHERE id=?", (iid,))
            from server import rec_requests as rq
            (z,) = rq.listing(db, self.pid)
            self.assertNotIn("import", z)
            self.assertEqual(z["status"], "received", "what it said stays")
        finally:
            db.close()

    def test_feed_wording_both_ways_and_closed_ones_left_out(self):
        self.add(rule="other", sent=D(0), days=10)
        self.add(rule="other", sent=D(0), days=20, show=True, to="Lakeside Hospital (fictional)")
        self.add(rule="other", sent=D(0), days=5, status="received", to="Received Place (fictional)")
        self.add(rule="other", sent=D(0), days=6, status="closed", to="Closed Place (fictional)")
        r = self.c["robin"].post(f"/api/profiles/{self.pid}/calendar", json={"tz": "UTC", "details": "full"}, headers=H)
        try:
            text = client().get("/" + r.json()["path"]).text
        finally:
            self.c["robin"].delete(f"/api/profiles/{self.pid}/calendar", headers=H)
        got = sorted((e["DTSTART"][1], unescape(e["SUMMARY"][1])) for e in parse(text)["events"])
        self.assertEqual(got, [((TODAY + timedelta(days=10)).strftime("%Y%m%d"), "Sanguity records request"),
                               ((TODAY + timedelta(days=20)).strftime("%Y%m%d"), "Records request to Lakeside Hospital (fictional): response due")])
        for word in ("Riverside", "Received Place", "Closed Place"):
            self.assertNotIn(word, text)

    def test_encrypted_and_in_the_export(self):
        from server import export, rec_requests as rq
        self.add(rule="gdpr", sent="2026-01-31", name="Bea Example", kinds="lab results", **{"from": "2020-01-01"}, until="2025-12-31", format="fhir")
        db = dbm.connect()
        try:
            for (body,) in db.execute("SELECT body FROM records_requests"):
                self.assertNotIn("Riverside", body)
            data = export.plan(db, self.pid)["data"]
            (x,) = data["requests"]
            self.assertNotIn("due", x, "as kept: worked out again where it's loaded")
            other = prof.create_profile(db, "Restored requests (fictional)", owner_id=Calendar.uid["robin"])
            with dbm.transaction(db):
                self.assertEqual(export.restore(db, other, data, lambda f: None, [])["requests"], 1)
            with dbm.transaction(db):
                self.assertEqual(export.restore(db, other, data, lambda f: None, [])["here"], 1)
            (y,) = rq.listing(db, other)
            self.assertEqual((y["to"], y["due"], y["from"], y["until"], y["kinds"], y["format"], y["name"], y["by"]),
                             ("Riverside Clinic (fictional)", "2026-02-28", "2020-01-01", "2025-12-31", "lab results", "fhir", "Bea Example", "lee"))
            acts = [json.loads(crypto.dec(d)) for (d,) in db.execute("SELECT detail FROM activity WHERE action='request' AND profile_id=?", (self.pid,))]
            self.assertTrue(acts and all(set(a) == {"change"} for a in acts), acts)
        finally:
            db.close()


DOW_L =["mo", "tu", "we", "th", "fr", "sa", "su"]


def tearDownModule():
    Calendar.ctx.__exit__(None, None, None)
    shutil.rmtree(TMP, True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
