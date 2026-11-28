"""Notifications, opt-in, only to the install's own notification server (#1130).

Off by default: nothing is sent until an admin sets a notification server (the install setting notify.url, a
self-hosted ntfy topic's address; General → Notifications) and someone turns notifications on for a profile they can
see (notify_optins, schema 50; Settings → Notifications), for each kind:

  results   a connected source's pull brought new records (sync.py calls new_results inside the pull's transaction)
  doses     a dose is due: each time of a schedule line with Remind me on, not paused, not As needed, of a medication
            still in the records and not ended (med_reminders.py), in the line's own zone (the person who set it,
            as the calendar feed reads it), else the zone of the first person who turned them on, else the server's

What is sent carries no health details, said in one place: message() makes the only words a notification may have,
from its kind and the profile's name ("Sanguity: new results for Ben", "Sanguity: a dose is due for Ben"), and
request() the only request: POST of those words to the topic's address, with Title "Sanguity", Click the install's
public address (tapping opens Sanguity, where its person signs in to see what it is) and the access token, when one is
saved, as Authorization: Bearer. Nothing else: no result, no medication, no amount, no time.

Each notification is made once, a row of notify_sent keyed by what it's for ("results:<import id>",
"doses:<profile id>:<day>:<HH:MM>"), so a restart never makes it again; a dose whose time had passed WINDOW before it
was noticed (the server was stopped) isn't made at all. One thread ("sanguity-notify", beside backup_jobs.py's and
sync.py's) looks every TICK seconds for doses due, then sends what's queued, each tried TRIES times in all, BACKOFF
apart; one that never goes through is logged in Install activity (notify.fail: its kind, the tries and why, never its
words). One no longer wanted (the opt-ins withdrawn, the server taken away) or a dose's an hour late is dropped.

The access token is a secret: encrypted in meta (TOKEN_META) and never given to a page (only whether one is saved), or
the server's environment's NOTIFY_TOKEN, which wins, as the sign-in provider's client secret is kept (oidc.py). One
saved here is kept with the host it was saved for, and sent to that host alone: a server changed to another host has
none until one is saved for it. Every
contact goes through net.py's gateway under a permit for PURPOSE (outside.py lists it under Outside connections), to
the topic's host alone. https only, unless the host is on the local network (settings.notify_url); a name sent to over
http is resolved first, and refused unless every address it has is a local one.
"""
import json, logging, os, threading, time, urllib.parse
from datetime import datetime, timedelta, time as Time

from . import activity, config, crypto, db as dbm, net, outside, settings

log = logging.getLogger("sanguity.notify")

KINDS = ("results", "doses")
TOKEN_META, TOKEN_ENV = "notify.token", "NOTIFY_TOKEN"
PURPOSE = "Notification server"
TITLE = "Sanguity"
TEXTS = {"results": "Sanguity: new results for {name}", "doses": "Sanguity: a dose is due for {name}",
         "test": "Sanguity: a test notification. Notifications from this server reach this topic."}
NAME_MOST = 60       # characters of the profile's name in a message
TICK = 30            # seconds between looks for doses due and notifications to send
TRIES = 4            # sends of one notification in all
BACKOFF = (60, 300, 1800)   # seconds after the first, second and third failure
WINDOW = 15 * 60     # a dose whose time passed longer ago than this when first noticed isn't sent
DOSE_LATE = 3600     # a dose's notification not sent by this long after it was made is dropped
KEEP = 7 * 86400     # rows of notify_sent kept after they're done
TIMEOUT = 10
TOKEN_MOST = 500


class SendError(Exception):
    """Why a notification didn't go through, in plain words (never what it said)."""


# ---------------------------------------------------------------- the server and its token

def url():
    return settings.install_value("notify.url") or ""


def configured():
    return bool(url())


def hosts():
    u = url()
    return {net.host_of(u)} if u else set()


def _saved(db):
    """The token saved here, for the host it was saved for: {"host", "token"} or None."""
    stored = dbm.meta_get(db, TOKEN_META)
    try:
        return json.loads(crypto.dec(stored)) if stored else None
    except ValueError:
        return None


def token_source(db):
    """Where the access token comes from: "environment", "install" or None (none: ntfy's topic is open). One saved
    for another host than today's server's is none: it's never sent anywhere else than where it was saved for."""
    if os.environ.get(TOKEN_ENV):
        return "environment"
    t = _saved(db)
    return "install" if t and t.get("host") in hosts() else None


def token(db):
    if (v := os.environ.get(TOKEN_ENV)):
        return v
    t = _saved(db)
    return t["token"] if t and t.get("host") in hosts() else ""


def set_token(db, value):
    """A new access token for today's server ("" removes it). Raises SendError for one that can't be sent in a header,
    or with no server set."""
    value = (value or "").strip()
    if len(value) > TOKEN_MOST or any(not 0x21 <= ord(c) <= 0x7e for c in value):
        raise SendError(f"An access token is letters, digits and signs, with no spaces, at most {TOKEN_MOST}.")
    if value and not configured():
        raise SendError("Set the notification server first: the token is kept for it alone.")
    dbm.meta_set(db, TOKEN_META, crypto.enc(json.dumps({"host": next(iter(hosts())), "token": value})) if value else None)


def permit(db, who=None, ip=None, actor=None):
    return net.Permit(PURPOSE, hosts, lambda host, purpose: outside.record(db, None, host, purpose, who, ip, actor))


# ---------------------------------------------------------------- the only words, and the only request

def message(kind, name=None):
    """What a notification says: one of TEXTS, with the profile's name and nothing else."""
    if kind not in TEXTS:
        raise ValueError(f"no notification of kind {kind!r}")
    name = " ".join(str(name or "").split())[:NAME_MOST] or "a profile"
    return TEXTS[kind].format(name=name)


def request(db, kind, name=None):
    """(body, headers) of the POST to the topic: message() and the headers ntfy reads, no more."""
    headers = {"Title": TITLE, "Content-Type": "text/plain; charset=utf-8"}
    if (click := config.public_address()):
        headers["Click"] = click
    if (t := token(db)):
        headers["Authorization"] = "Bearer " + t
    return message(kind, name).encode("utf-8"), headers


def _local_only(p, u):
    """An http:// topic named by a name: every address the name has is on the local network, else SendError."""
    import ipaddress
    host = urllib.parse.urlsplit(u).hostname or ""
    try:
        ipaddress.ip_address(host.split("%")[0])
        return   # an address: settings.notify_url checked it
    except ValueError:
        pass
    try:
        ips = net.resolve(p, u)
    except net.Errors as e:
        raise SendError(f"The notification server couldn't be reached: {net.why(e)}.") from None
    if not ips or not all(settings.local_address(ipaddress.ip_address(i.split("%")[0])) for i in ips):
        raise SendError(f"{host} isn't on the local network, so it would be sent unencrypted over the internet: use https://.")


def send(db, kind, name=None, p=None):
    """Send one notification now. Raises SendError."""
    u = url()
    if not u:
        raise SendError("No notification server is set.")
    p = p or permit(db)
    body, headers = request(db, kind, name)
    parts = urllib.parse.urlsplit(u)
    try:
        if parts.scheme == "http":
            _local_only(p, u)
        conn = net.connection(p, u, timeout=TIMEOUT)
        try:
            conn.request("POST", parts.path or "/", body, headers)
            r = conn.getresponse()
            r.read(4096)
        finally:
            conn.close()
    except net.Refused as e:
        raise SendError(str(e)) from None
    except net.Errors as e:
        raise SendError(f"The notification server couldn't be reached: {net.why(e)}.") from None
    if r.status in (401, 403):
        raise SendError(f"The notification server refused the access token (it answered {r.status}).")
    if not 200 <= r.status < 300:
        raise SendError(f"The notification server answered {r.status}.")


def test(db, who=None, ip=None):
    """An admin's Test: a test notification sent now, logged as notify.test. Raises SendError."""
    try:
        send(db, "test", p=permit(db, who, ip))
    except SendError as e:
        activity.log(db, "notify.test", who, None, {"ok": False, "why": str(e)}, ip)
        raise
    activity.log(db, "notify.test", who, None, {"ok": True}, ip)
    return {"ok": True, "host": next(iter(hosts()), None)}


# ---------------------------------------------------------------- who wants which

_WANTED = """SELECT {what} FROM notify_optins o JOIN memberships m ON m.user_id = o.user_id AND m.profile_id = o.profile_id
             JOIN users u ON u.id = o.user_id WHERE o.profile_id=? AND o.kind=? AND u.disabled=0"""


def wanted(db, pid, kind):
    """Whether someone who can still see the profile has this kind turned on for it."""
    return db.execute(_WANTED.format(what="1") + " LIMIT 1", (pid, kind)).fetchone() is not None


def choices(db, uid):
    """The profiles this person can see, each with the kinds they turned on: [{"id", "name", "results", "doses"}]."""
    on = {}
    for r in db.execute("SELECT profile_id, kind FROM notify_optins WHERE user_id=?", (uid,)):
        on.setdefault(r["profile_id"], set()).add(r["kind"])
    return [{"id": r["id"], "name": r["name"], **{k: k in on.get(r["id"], ()) for k in KINDS}}
            for r in db.execute("""SELECT p.id, p.name FROM memberships m JOIN profiles p ON p.id = m.profile_id
                                   WHERE m.user_id=? ORDER BY p.name COLLATE NOCASE, p.id""", (uid,))]


def choose(db, uid, pid, kind, on):
    """Turn a kind on or off for a profile; True when that changed something."""
    if kind not in KINDS:
        raise ValueError(kind)
    if on:
        return db.execute("INSERT OR IGNORE INTO notify_optins(user_id, profile_id, kind, created) VALUES(?, ?, ?, ?)",
                          (uid, pid, kind, dbm.now())).rowcount > 0
    return db.execute("DELETE FROM notify_optins WHERE user_id=? AND profile_id=? AND kind=?", (uid, pid, kind)).rowcount > 0


# ---------------------------------------------------------------- making them

def _make(db, key, pid, kind):
    return db.execute("INSERT OR IGNORE INTO notify_sent(key, profile_id, kind, next_at, created) VALUES(?, ?, ?, ?, ?)",
                      (key, pid, kind, dbm.now(), dbm.now())).rowcount > 0


def new_results(db, pid, import_id):
    """A connected source's pull saved import_id, with new records: its notification made, when it's wanted (inside
    the pull's transaction: a pull rolled back makes none), and the thread woken once it's saved."""
    if configured() and wanted(db, pid, "results") and _make(db, f"results:{int(import_id)}", pid, "results"):
        dbm.after_commit(db, wake)


def _zone(db, r, pid):
    from . import med_reminders
    z = med_reminders._zone(r.get("tz"))
    if z is not None:
        return z
    first = db.execute(_WANTED.format(what="o.user_id") + " ORDER BY o.created LIMIT 1", (pid, "doses")).fetchone()
    if first:
        return med_reminders._zone(settings.effective(db, None, first[0])["dates.timeZone"][0])
    return None


def _at(day, hm, zone):
    """The moment a dose's wall-clock time is, on day, in zone (None: the server's)."""
    h, m = (int(x) for x in hm.split(":"))
    local = datetime.combine(day, Time(h, m))
    return (local.replace(tzinfo=zone) if zone is not None else local).timestamp()


def due_doses(db, now=None):
    """Make the notifications of the doses due now (their time passed less than WINDOW ago, and after the first opt-in
    to them). Returns how many were made."""
    from . import med_log, med_reminders
    if not configured():
        return 0
    now, made = now or time.time(), 0
    for (pid,) in db.execute("SELECT DISTINCT profile_id FROM notify_optins WHERE kind='doses'").fetchall():
        since = db.execute(_WANTED.format(what="MIN(o.created)"), (pid, "doses")).fetchone()[0]
        if since is None:
            continue
        due = []   # (line, day, "HH:MM") whose time has come, not made yet: the records read only when there's one
        for r in med_reminders.listing(db, pid):
            if not r["on"] or r["paused"] or r["asneeded"] or not r["times"] or not r["from"]:
                continue
            zone = _zone(db, r, pid)
            today = (datetime.fromtimestamp(now, zone) if zone is not None else datetime.fromtimestamp(now)).date()
            for day in (today - timedelta(days=1), today):
                for hm in sorted({at for at, _ in med_log.scheduled([r], day)}):
                    if (max(since, now - WINDOW) <= _at(day, hm, zone) <= now and not db.execute(
                            "SELECT 1 FROM notify_sent WHERE key=?", (f"doses:{pid}:{day.isoformat()}:{hm}",)).fetchone()):
                        due.append((r, day, hm))
        meds = med_reminders.current(db, pid) if due else {}
        for r, day, hm in due:   # of a medication still in the records, and not ended by the day they say it stopped
            m = meds.get(r["med"])
            if m and hm in {at for at, _ in med_log.scheduled([r], day, m.get("stop"))} \
                    and _make(db, f"doses:{pid}:{day.isoformat()}:{hm}", pid, "doses"):
                made += 1
    return made


# ---------------------------------------------------------------- sending them

def _still_wanted(db, r, now):
    if r["profile_id"] is None or not wanted(db, r["profile_id"], r["kind"]):
        return False
    return not (r["kind"] == "doses" and now - r["created"] > DOSE_LATE)


def run_queued(db, now=None):
    """Send what's due; returns how many went through."""
    now = now or dbm.now()
    if not configured():
        db.execute("UPDATE notify_sent SET state='dropped' WHERE state='queued'")
        return 0
    p, sent = permit(db, actor=activity.NOTIFICATIONS), 0
    for r in db.execute("SELECT * FROM notify_sent WHERE state='queued' AND next_at<=? ORDER BY id LIMIT 50", (now,)).fetchall():
        if not _still_wanted(db, r, now):
            db.execute("UPDATE notify_sent SET state='dropped' WHERE id=?", (r["id"],))
            continue
        name = (db.execute("SELECT name FROM profiles WHERE id=?", (r["profile_id"],)).fetchone() or {"name": ""})["name"]
        try:
            send(db, r["kind"], name, p)
        except SendError as e:
            tries = r["tries"] + 1
            if tries >= TRIES:
                db.execute("UPDATE notify_sent SET state='failed', tries=? WHERE id=?", (tries, r["id"]))
                activity.log(db, "notify.fail", None, None, {"kind": r["kind"], "tries": tries, "why": str(e)},
                             actor=activity.NOTIFICATIONS)
            else:
                db.execute("UPDATE notify_sent SET tries=?, next_at=? WHERE id=?", (tries, now + BACKOFF[tries - 1], r["id"]))
            log.warning("a notification (%s) didn't go through, try %d of %d (why: Install activity)", r["kind"], tries, TRIES)
            continue
        db.execute("UPDATE notify_sent SET state='sent', tries=tries+1, sent=? WHERE id=?", (now, r["id"]))
        sent += 1
    db.execute("DELETE FROM notify_sent WHERE state<>'queued' AND created<?", (now - KEEP,))
    return sent


_thread, _stop, _wake = None, threading.Event(), threading.Event()


def wake():
    _wake.set()


def start():
    global _thread
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="sanguity-notify", daemon=True)
    _thread.start()


def stop(timeout=5):
    global _thread
    _stop.set()
    _wake.set()
    if _thread is not None:
        _thread.join(timeout)
    _thread = None


def tick():
    db = dbm.connect()
    try:
        due_doses(db)
        run_queued(db)
    finally:
        db.close()


def _loop():
    while not _stop.is_set():
        try:
            tick()
        except Exception:
            log.exception("notifications")
        _wake.wait(TICK)
        _wake.clear()
