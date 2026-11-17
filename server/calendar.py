"""Reminders as a calendar feed (#51): a private iCalendar (RFC 5545) address per person and profile, which their own
calendar app fetches every few hours. Sanguity sends nothing and contacts no one; the calendar app asks.

A calendar app can't sign in, so the address carries the only credential: a random token (security.new_token, 256
bits), of which only the SHA-256 is stored (calendar_feeds.token_hash), like a session cookie's. The token is shown
once, when the feed is made or rotated. Each feed belongs to one person on one profile, and works only while that
person can still view the profile (role and account checked at every fetch): removing someone's access, or
disabling their account, stops their feed too.

What a feed holds:
  appointments   the records' `upcoming` list (imported or typed in), from today on
  expected labs  a guess, for a panel drawn at a steady rhythm (DRAWS_MIN draws or more, the recent gaps within
                 RHYTHM_SPREAD of each other): the next draw one usual gap after the last, as a tentative all-day
                 event, "expected, not scheduled". Never a time of day: the records don't know one.
  medication reminders (#162, med_reminders.py)  each time of day of each reminder turned on, as a recurring event
                 (RRULE) in the reminder's own time zone (TZID, with a VTIMEZONE), ending on its until day or the day
                 the records say the medication stopped; listed while the feed's "meds" is on (the default)

A calendar may sync to a phone vendor's cloud, so how much it says is the person's choice, per feed:
  minimal (the default)   "Appointment" / "Labs expected" / "Medication", the time, and a link back to the app
  full                    the record's title, where, with whom, which panels, and the source it came from; a
                          medication's name and its instructions as the records list them
Neither ever carries results, diagnoses or procedures.

Each event's UID is a keyed hash of the profile and the record (its source key, or its date and title), so a
calendar app replaces an event on the next fetch rather than adding a copy, and the UID tells nothing.
Times: the records hold local times with no zone. The feed is made with the zone of the browser that set it up
(calendar_feeds.tz) and sends UTC; if this server doesn't know that zone, local "floating" times (RFC 5545 3.3.5),
which calendar apps show as written, with X-WR-TIMEZONE naming the zone.
"""
import hashlib, hmac, re
from datetime import date, datetime, timedelta, timezone

from . import db as dbm, security as sec

DETAILS = ("minimal", "full")
DRAWS_MIN = 4               # draws of a panel before a rhythm is guessed (three gaps)
GAP_MIN, GAP_MAX = 21, 400  # days: a rhythm from three weeks to about a year
RHYTHM_SPREAD = 0.25        # each recent gap within 25% of their median
OVERDUE_KEEP = 0.5          # an expected draw stays listed until half a gap past due, then the rhythm is taken as over
AHEAD_DAYS = 400            # appointments further ahead than this are left out
APPOINTMENT_MINUTES = 60    # an appointment with a time and no end
FETCH_NOTE_SECONDS = 60     # last_fetched is written at most this often


class FeedThrottle(sec.Throttle):
    """The sign-in throttle's rules, for feed fetches: wrong tokens per address (bad:), and every fetch per feed
    (feed:), so a leaked address can't be hammered. A calendar app asks every 15 minutes to a few hours."""
    FREE = {"bad": 10, "feed": 30}


throttle = FeedThrottle()


# ---------------------------------------------------------------- feeds and their tokens

def _row(r):
    return None if r is None else {"id": r["id"], "details": r["details"], "labs": bool(r["labs"]), "meds": bool(r["meds"]), "tz": r["tz"],
                                   "created": r["created"], "rotated": r["rotated"], "last_fetched": r["last_fetched"]}


def mine(db, pid, uid):
    return _row(db.execute("SELECT * FROM calendar_feeds WHERE profile_id=? AND user_id=?", (pid, uid)).fetchone())


def everyone(db, pid):
    """Every feed of the profile, with whose it is (for its owners)."""
    return [{**_row(r), "user_id": r["user_id"], "username": r["username"]} for r in db.execute(
        """SELECT f.*, u.username FROM calendar_feeds f JOIN users u ON u.id = f.user_id
           WHERE f.profile_id=? ORDER BY f.created""", (pid,))]


def _tz(tz):
    tz = (tz or "").strip()
    return tz if re.fullmatch(r"[A-Za-z0-9_+\-/]{1,64}", tz) else None


def create(db, pid, uid, details="minimal", labs=True, tz=None, meds=True):
    """A new feed; returns its token, the only time it exists outside the calendar app. One per person and profile."""
    token = sec.new_token()
    db.execute("""INSERT INTO calendar_feeds(profile_id, user_id, token_hash, details, labs, tz, created, meds)
                  VALUES(?, ?, ?, ?, ?, ?, ?, ?)""", (pid, uid, sec.token_hash(token), details, int(bool(labs)), _tz(tz), dbm.now(),
                                                   int(bool(meds))))
    return token


def rotate(db, pid, uid):
    """A new token for the person's feed; the old address stops working at once. None if they have no feed."""
    token = sec.new_token()
    n = db.execute("UPDATE calendar_feeds SET token_hash=?, rotated=?, last_fetched=NULL WHERE profile_id=? AND user_id=?",
                   (sec.token_hash(token), dbm.now(), pid, uid)).rowcount
    return token if n else None


def change(db, pid, uid, details=None, labs=None, tz=None, meds=None):
    sets, args = [], []
    if meds is not None:   # medication reminders (#162)
        sets, args = sets + ["meds=?"], args + [int(bool(meds))]
    if details is not None:
        sets, args = sets + ["details=?"], args + [details]
    if labs is not None:
        sets, args = sets + ["labs=?"], args + [int(bool(labs))]
    if tz is not None:
        sets, args = sets + ["tz=?"], args + [_tz(tz)]
    if not sets:
        return bool(mine(db, pid, uid))
    return db.execute(f"UPDATE calendar_feeds SET {', '.join(sets)} WHERE profile_id=? AND user_id=?",
                      (*args, pid, uid)).rowcount > 0


def revoke(db, pid, feed_id=None, uid=None):
    """Remove a feed (by id, or the person's own); returns whose it was ({"user_id", "username"}) or None."""
    q = "SELECT f.id, f.user_id, u.username FROM calendar_feeds f JOIN users u ON u.id = f.user_id WHERE f.profile_id=? AND "
    r = db.execute(q + ("f.id=?" if feed_id is not None else "f.user_id=?"), (pid, feed_id if feed_id is not None else uid)).fetchone()
    if not r:
        return None
    db.execute("DELETE FROM calendar_feeds WHERE id=?", (r["id"],))
    return {"user_id": r["user_id"], "username": r["username"]}


def find(db, pid, token):
    """The feed this token opens on this profile, while its person may still view the profile; else None."""
    if not isinstance(token, str) or not 20 <= len(token) <= 100:
        return None
    r = db.execute("""SELECT f.*, p.name AS profile_name FROM calendar_feeds f
                      JOIN memberships m ON m.user_id = f.user_id AND m.profile_id = f.profile_id
                      JOIN users u ON u.id = f.user_id AND u.disabled = 0
                      JOIN profiles p ON p.id = f.profile_id
                      WHERE f.token_hash=? AND f.profile_id=?""", (sec.token_hash(token), pid)).fetchone()
    return r


def fetched(db, feed):
    now = dbm.now()
    if now - (feed["last_fetched"] or 0) >= FETCH_NOTE_SECONDS:
        db.execute("UPDATE calendar_feeds SET last_fetched=? WHERE id=?", (now, feed["id"]))


# ---------------------------------------------------------------- what's in a feed

def _day(t):
    try:
        return date.fromisoformat(str(t)[:10])
    except ValueError:
        return None


def _when(t):
    """(date, datetime or None) from a record's "t": "2026-12-01" or "2026-12-01T10:30[:00]"."""
    d = _day(t)
    if d is None:
        return None, None
    t = str(t)
    if len(t) > 10 and t[10] in "T ":
        try:
            dt = datetime.fromisoformat(t[:19].replace(" ", "T"))
            return d, dt.replace(tzinfo=None) if dt.tzinfo is None else dt
        except ValueError:
            pass
    return d, None


def appointments(db, pid, today):
    """The profile's upcoming appointments from today on, each with the label of the import it came from."""
    from . import records
    out, seen = [], set()
    for r in db.execute("SELECT id, origin, format, meta FROM imports WHERE profile_id=? ORDER BY id", (pid,)):
        meta = records._dec(r["meta"]) or {}
        label = records.source_label(r["origin"], r["format"])
        for u in meta.get("upcoming") or []:
            if not isinstance(u, dict):
                continue
            d, dt = _when(u.get("t"))
            if d is None or d < today or d > today + timedelta(days=AHEAD_DAYS):
                continue
            ident = u.get("key") or f"{u.get('t')}|{u.get('title')}|{u.get('where')}"
            if ident in seen:
                continue
            seen.add(ident)
            out.append({"day": d, "at": dt, "title": str(u.get("title") or "").strip(), "where": str(u.get("where") or "").strip(),
                        "who": str(u.get("who") or "").strip(), "source": label, "ident": "upcoming|" + ident})
    out.sort(key=lambda a: (a["day"], a["at"] or datetime.min))
    return out


def expected_labs(series, today):
    """Panels drawn at a steady rhythm, and when the next draw would fall: [{day, panels, gap, last}], one per
    expected day. series: the profile's lab series ({name, panel, pts: [[t, ...], ...]})."""
    draws = {}
    for s in series:
        if not isinstance(s, dict) or s.get("watch"):
            continue
        panel = str(s.get("panel") or s.get("name") or "").strip()
        if not panel:
            continue
        for p in s.get("pts") or []:
            d = _day(p[0] if isinstance(p, (list, tuple)) and p else p.get("t") if isinstance(p, dict) else None)
            if d:
                draws.setdefault(panel, set()).add(d)
    by_day = {}
    for panel, days in draws.items():
        days = sorted(days)
        if len(days) < DRAWS_MIN:
            continue
        gaps = [(b - a).days for a, b in zip(days, days[1:])][-(DRAWS_MIN - 1):]
        mid = sorted(gaps)[len(gaps) // 2]
        if not GAP_MIN <= mid <= GAP_MAX or any(abs(g - mid) > mid * RHYTHM_SPREAD for g in gaps):
            continue
        nxt = days[-1] + timedelta(days=mid)
        if nxt < today - timedelta(days=int(mid * OVERDUE_KEEP)):
            continue   # well past due: the rhythm has stopped
        e = by_day.setdefault(nxt, {"day": nxt, "panels": [], "gap": mid, "last": days[-1]})
        e["panels"].append(panel)
    out = sorted(by_day.values(), key=lambda e: e["day"])
    for e in out:
        e["panels"].sort()
    return out


def _series(db, pid):
    from . import records
    pts = {}
    for r in db.execute("SELECT series_id, body FROM results WHERE profile_id=?", (pid,)):
        p = records._dec(r["body"])
        if isinstance(p, (list, tuple)) and p:
            pts.setdefault(r["series_id"], []).append([p[0]])   # only the date is needed
        elif isinstance(p, dict):
            pts.setdefault(r["series_id"], []).append([p.get("t")])
    out = []
    for r in db.execute("SELECT id, body FROM series WHERE profile_id=?", (pid,)):
        b = records._dec(r["body"]) or {}
        out.append({"name": b.get("name"), "panel": b.get("panel"), "watch": b.get("watch"), "pts": pts.get(r["id"], [])})
    return out


# ---------------------------------------------------------------- iCalendar (RFC 5545)

def escape(text):
    """TEXT value escaping (3.3.11): backslash, semicolon, comma and newlines."""
    return (str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n"))


def fold(line):
    """Content lines longer than 75 octets are folded (3.1): CRLF and a space, never inside a UTF-8 character."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line + "\r\n"
    parts, start, limit = [], 0, 75
    while start < len(raw):
        end = min(start + limit, len(raw))
        while end < len(raw) and (raw[end] & 0xC0) == 0x80:   # back off a continuation byte
            end -= 1
        parts.append(raw[start:end].decode("utf-8"))
        start, limit = end, 74   # continuation lines start with the space
    return "\r\n ".join(parts) + "\r\n"


def _uid(secret, pid, ident):
    return hmac.new(secret, f"{pid}|{ident}".encode("utf-8"), hashlib.sha256).hexdigest()[:32] + "@sanguity"


def _uid_secret(db):
    from . import records
    return hashlib.sha256(b"sanguity calendar uids\x00" + records._key_secret(db)).digest()


def _zone(name):
    if not name:
        return None
    if name.upper() in ("UTC", "ETC/UTC", "GMT"):
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:   # unknown here (no tz database on this server, or a name it doesn't have)
        return None


def _stamp(dt):
    return dt.strftime("%Y%m%dT%H%M%SZ")


def build(db, feed, base_url, now=None):
    """The feed as iCalendar text (str, CRLF line ends)."""
    now = now or datetime.now(timezone.utc)
    zone = _zone(feed["tz"])
    today = (now.astimezone(zone) if zone else now).date()
    full = feed["details"] == "full"
    pid, secret = feed["profile_id"], _uid_secret(db)
    link = base_url.rstrip("/") + f"/?profile={pid}"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Sanguity//Calendar feed//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:" + escape("Sanguity" + (f" · {feed['profile_name']}" if full else "")),
             "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H"]
    if feed["tz"] and zone is None:
        lines.append("X-WR-TIMEZONE:" + escape(feed["tz"]))
    header = len(lines)   # where the VTIMEZONEs of medication reminders go (#162)
    stamp = _stamp(now.astimezone(timezone.utc))

    def event(ident, summary, desc, start, end, all_day, location=None, tentative=False, when=None):
        ev = ["BEGIN:VEVENT", "UID:" + _uid(secret, pid, ident), "DTSTAMP:" + stamp]
        if when:   # a reminder's own DTSTART, DTEND and RRULE (#162)
            ev += when
        elif all_day:
            ev += ["DTSTART;VALUE=DATE:" + start.strftime("%Y%m%d"), "DTEND;VALUE=DATE:" + end.strftime("%Y%m%d")]
        elif zone is not None:
            ev += ["DTSTART:" + _stamp(start.replace(tzinfo=zone).astimezone(timezone.utc)),
                   "DTEND:" + _stamp(end.replace(tzinfo=zone).astimezone(timezone.utc))]
        else:   # floating local time
            ev += ["DTSTART:" + start.strftime("%Y%m%dT%H%M%S"), "DTEND:" + end.strftime("%Y%m%dT%H%M%S")]
        ev += ["SUMMARY:" + escape(summary), "DESCRIPTION:" + escape(desc), "URL:" + link,
               "CLASS:PRIVATE", "TRANSP:" + ("TRANSPARENT" if tentative or all_day or when else "OPAQUE"),
               "STATUS:" + ("TENTATIVE" if tentative else "CONFIRMED")]
        if location:
            ev.append("LOCATION:" + escape(location))
        if when:   # a reminder: the calendar app reminds at the time itself (#162; many let a subscriber keep or drop alarms)
            ev += ["BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:" + escape(summary), "TRIGGER:PT0M", "END:VALARM"]
        lines.extend(ev + ["END:VEVENT"])

    for a in appointments(db, pid, today):
        if full:
            summary = a["title"] or "Appointment"
            bits = [f"With {a['who']}" if a["who"] else None, f"From {a['source']}" if a["source"] else None]
            desc = "\n".join([b for b in bits if b] + [f"Open Sanguity: {link}"])
        else:
            summary, desc = "Appointment", f"Details are in Sanguity: {link}"
        if a["at"] is None:   # a day with no time: all day
            event(a["ident"], summary, desc, a["day"], a["day"] + timedelta(days=1), True, a["where"] if full else None)
        else:
            start = a["at"]
            if start.tzinfo is not None:   # an import that gave a zone: that instant
                start = start.astimezone(zone or timezone.utc).replace(tzinfo=None)
            event(a["ident"], summary, desc, start, start + timedelta(minutes=APPOINTMENT_MINUTES), False,
                  a["where"] if full else None)

    if feed["labs"]:
        for e in expected_labs(_series(db, pid), today):
            weeks = round(e["gap"] / 7)
            rhythm = f"about every {weeks} weeks" if weeks < 9 else f"about every {round(e['gap'] / 30.4)} months"
            if full:
                summary = f"Labs expected ({', '.join(e['panels'])})"
                desc = (f"Expected, not scheduled. These labs have been drawn {rhythm}; the last was on "
                        f"{e['last'].isoformat()}. Nothing is booked: check with your care team.\nOpen Sanguity: {link}")
            else:
                summary = "Labs expected"
                desc = f"Expected, not scheduled: a guess from the usual rhythm of past draws. Details are in Sanguity: {link}"
            event("labs|" + "|".join(e["panels"]), summary, desc, e["day"], e["day"] + timedelta(days=1), True, tentative=True)

    if feed["meds"] if "meds" in feed.keys() else True:   # medication reminders (#162): recurring, in each reminder's own zone
        from . import med_reminders
        evs, zones = med_reminders.feed_events(db, pid, full, today)
        lines[header:header] = [x for name, (y0, y1) in sorted(zones.items()) for x in med_reminders.vtimezone(name, y0, y1) or []]
        for e in evs:
            if full:
                desc = "\n".join(["A reminder set in Sanguity."] + ([f"As the records list it: {e['dose']}"] if e["dose"] else []) +
                                 [f"Open Sanguity: {link}"])
            else:
                desc = f"A reminder set in Sanguity. Details are in Sanguity: {link}"
            event(e["ident"], e["summary"], desc, None, None, False, when=e["lines"])

    lines.append("END:VCALENDAR")
    return "".join(fold(l) for l in lines)
