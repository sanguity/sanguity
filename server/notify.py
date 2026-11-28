"""Notifications, opt-in, only to the install's own notification server (#1130).

Off by default: nothing is sent until an admin sets a notification server (the install setting notify.url, a
self-hosted ntfy server's address; General → Notifications) and someone turns notifications on for a profile they can
see (notify_optins, schema 54; Settings → Notifications), for each kind:

  results   a connected source's pull brought new records (sync.py calls new_results inside the pull's transaction)
  doses     a dose is due: each time of a schedule line with Remind me on, not paused, not As needed, of a medication
            still in the records and not ended (med_reminders.py), in the line's own zone (the person who set it,
            as the calendar feed reads it), else the zone of the first person who turned them on, else the server's

Each person has a topic of their own on that server (notify_topics, encrypted: "sanguity-" and 24 random url-safe
characters, unguessable), which only they subscribe to (Settings → Notifications shows its address, and Make a new
topic replaces it). Every topic is made new when the server's origin (scheme, host and port) changes (TOPICS_META
keeps the one they were made for; the change is confirmed first, saying so: confirm_url), so an admin pointing it at
a host of their own learns no topic that's used afterwards; and a person's is made new when their account's password
or second factors are reset, by an admin or themself, or they're signed out everywhere (renew, from
accounts.sign_out_everywhere and a password change), as their calendar feeds end then. A profile's notification goes to the topic of each person who turned that kind on for it and can
still see it (a membership, the account not disabled), once each: never to a topic someone without access reads.

What is sent carries no health details, said in one place: message() makes the only words a notification may have,
from its kind and the profile's name ("Sanguity: new results for Ben", "Sanguity: a dose is due for Ben"), and
request() the only request: POST of those words to the person's topic, with Title "Sanguity", Click the install's
public address (tapping opens Sanguity, where its person signs in to see what it is) and the access token, when one
goes to this server, as Authorization: Bearer. Nothing else: no result, no medication, no amount, no time.

Each notification is made once per person, a row of notify_sent keyed by what it's for and whose
("results:<import id>:<user>", "doses:<profile id>:<day>:<HH:MM>:<user>"), so a restart never makes it again; a dose
whose time had passed WINDOW before it was noticed (the server was stopped) isn't made at all. One thread
("sanguity-notify", beside backup_jobs.py's and sync.py's) looks every TICK seconds for doses due, then sends what's
queued, each tried TRIES times in all, BACKOFF apart; one that never goes through is logged in Install activity
(notify.fail: its kind, the tries and why, never its words or its topic). One no longer wanted (the opt-in withdrawn,
the access gone, the server taken away) or a dose's an hour late is dropped.

The access token is a secret, never given to a page (only whether there is one), and sent only to the server it is
for, by scheme and host (an origin: https://ntfy.example.com, never its http:// on the same host):
  - one saved here (General → Notifications) is encrypted in meta (TOKEN_META) with the origin it was saved for;
  - the server's environment's NOTIFY_TOKEN, which wins, goes to the origin it was first sent to, kept in meta
    (ENV_META, with a hash of the token): a server at another origin gets none until the environment's token is
    changed, when the next origin it's sent to is the one it's kept for.
A token is letters, digits and signs (0x21-0x7e) only: one from the environment that isn't is ignored, said in the
log without its value. Every contact goes through net.py's gateway under a permit for PURPOSE (outside.py lists it
under Outside connections), to the server's host alone. https only, unless the host is on the local network
(settings.notify_url); a name sent to over http is resolved first, refused unless every address it has is a local
one, and connected to at the address checked (never looked up again).
"""
import hashlib, json, logging, os, secrets, threading, time, urllib.parse
from datetime import datetime, timedelta, time as Time

from . import activity, config, crypto, db as dbm, net, outside, settings

log = logging.getLogger("sanguity.notify")

KINDS = ("results", "doses")
TOKEN_META, ENV_META, TOKEN_ENV = "notify.token", "notify.token_env", "NOTIFY_TOKEN"
TOPICS_META = "notify.topics_origin"   # the server's origin the topics were made for
PURPOSE = "Notification server"
TITLE = "Sanguity"
TEXTS = {"results": "Sanguity: new results for {name}", "doses": "Sanguity: a dose is due for {name}",
         "test": "Sanguity: a test notification. Notifications reach this topic."}
TOPIC_PREFIX = "sanguity-"
NAME_MOST = 60       # characters of the profile's name in a message
TICK = 30            # seconds between looks for doses due and notifications to send
TRIES = 4            # sends of one notification in all
BACKOFF = (60, 300, 1800)   # seconds after the first, second and third failure
WINDOW = 15 * 60     # a dose whose time passed longer ago than this when first noticed isn't sent
DOSE_LATE = 3600     # a dose's notification not sent by this long after it was made is dropped
KEEP = 7 * 86400     # rows of notify_sent kept after they're done
TIMEOUT = 10
TOKEN_MOST = 500
TEST_EVERY = 10      # seconds between one person's test notifications


class SendError(Exception):
    """Why a notification didn't go through, in plain words (never what it said, its topic or a token)."""


# ---------------------------------------------------------------- the server and its token

def url():
    return settings.install_value("notify.url") or ""


def configured():
    return bool(url())


def hosts():
    u = url()
    return {net.host_of(u)} if u else set()


def origin(u=None):
    """scheme://host[:port] of the server: what a token is sent to."""
    u = url() if u is None else u
    return f"{urllib.parse.urlsplit(u).scheme.lower()}://{net.host_of(u)}" if u else ""


def clean_token(value):
    """A token as sent (stripped), or None when it can't be one: letters, digits and signs, no spaces, at most
    TOKEN_MOST."""
    value = (value or "").strip()
    if len(value) > TOKEN_MOST or any(not 0x21 <= ord(c) <= 0x7e for c in value):
        return None
    return value


_warned = set()


def _env_token():
    """The environment's token, cleaned; "" when it's unset, or isn't one (said in the log, once, without it)."""
    raw = os.environ.get(TOKEN_ENV)
    if not raw:
        return ""
    t = clean_token(raw)
    if not t:
        if TOKEN_ENV not in _warned:
            _warned.add(TOKEN_ENV)
            log.warning("%s isn't a token Sanguity can send (letters, digits and signs, no spaces, at most %d): ignored",
                        TOKEN_ENV, TOKEN_MOST)
        return ""
    return t


def _meta(db, k):
    stored = dbm.meta_get(db, k)
    try:
        return json.loads(crypto.dec(stored)) if stored else None
    except ValueError:
        return None


def _env_origin(db, t, claim=False):
    """The origin the environment's token t is kept for; with claim, today's server's, when it's kept for none yet (or
    for an earlier token)."""
    h = hashlib.sha256(t.encode()).hexdigest()
    kept = _meta(db, ENV_META)
    if kept and kept.get("hash") == h:
        return kept.get("origin")
    if claim and origin():
        dbm.meta_set(db, ENV_META, crypto.enc(json.dumps({"origin": origin(), "hash": h})))
        return origin()
    return None


def token_source(db):
    """Where the access token comes from: "environment", "install" or None (ntfy's topics are open). The environment's
    is "environment" while it goes to today's server (or hasn't gone anywhere yet); one saved here for another origin
    is none."""
    if (t := _env_token()):
        return "environment" if _env_origin(db, t) in (None, origin()) else "environment-elsewhere"
    s = _meta(db, TOKEN_META)
    return "install" if s and s.get("origin") == origin() else None


def token(db, claim=False):
    """The token to send to today's server, or "". claim: about to send it (the environment's is kept for this origin
    from the first time)."""
    if (t := _env_token()):
        return t if _env_origin(db, t, claim) == origin() or (not claim and _env_origin(db, t) is None) else ""
    s = _meta(db, TOKEN_META)
    return s["token"] if s and s.get("origin") == origin() and s.get("token") else ""


def set_token(db, value):
    """A new access token for today's server ("" removes it). Raises SendError for one that can't be sent in a header,
    or with no server set."""
    t = clean_token(value)
    if t is None:
        raise SendError(f"An access token is letters, digits and signs, with no spaces, at most {TOKEN_MOST}.")
    if t and not configured():
        raise SendError("Set the notification server first: the token is kept for it alone.")
    dbm.meta_set(db, TOKEN_META, crypto.enc(json.dumps({"origin": origin(), "token": t})) if t else None)


def permit(db, who=None, ip=None, actor=None):
    return net.Permit(PURPOSE, hosts, lambda host, purpose: outside.record(db, None, host, purpose, who, ip, actor))


# ---------------------------------------------------------------- each person's topic

def _follow(db):
    """Every topic made new (dropped, to be made again as each is next wanted) when the server's origin isn't the one
    they were made for."""
    now = origin()
    if not now:
        return
    was = dbm.meta_get(db, TOPICS_META)
    if was != now:
        if was is not None:
            db.execute("DELETE FROM notify_topics")
        dbm.meta_set(db, TOPICS_META, now)


def topics_made_new(db, value):
    """How many topics a change of the server to value would make new (0: the same origin, or none to make new)."""
    now, was = origin(value or ""), dbm.meta_get(db, TOPICS_META)
    if not now or was is None or now == was:
        return 0
    return db.execute("SELECT COUNT(*) FROM notify_topics").fetchone()[0]


def confirm_url(db, before, after, request=None, s=None):
    """(routes_settings._confirmed) A change of the server to another origin, with topics made: asked first, saying
    each is made new."""
    n = topics_made_new(db, after)
    if not n:
        return None
    return {"count": n, "title": "Change the notification server?", "action": "Change it",
            "message": f"Another server makes every person's topic new: {'the person' if n == 1 else f'the {n} people'} "
                       "with one see its new address under Settings, Notifications, and subscribe to it again; nothing "
                       "more goes to the old ones."}


settings.get_def("notify.url").confirm = confirm_url


def renew(db, uid):
    """This person's topic made new, if they have one (their password or second factors reset, signed out
    everywhere): whoever had the old address learns nothing more from it."""
    if db.execute("SELECT 1 FROM notify_topics WHERE user_id=?", (uid,)).fetchone():
        new_topic(db, uid)


def topic(db, uid, make=True):
    """This person's topic (made the first time, with make), or None."""
    _follow(db)
    r = db.execute("SELECT topic FROM notify_topics WHERE user_id=?", (uid,)).fetchone()
    if r:
        return crypto.dec(r["topic"])
    return new_topic(db, uid) if make else None


def new_topic(db, uid):
    """A new topic for this person, in place of the one they had: notifications stop reaching the old one."""
    _follow(db)
    t = TOPIC_PREFIX + secrets.token_urlsafe(18)
    db.execute("""INSERT INTO notify_topics(user_id, topic, created) VALUES(?, ?, ?)
                  ON CONFLICT(user_id) DO UPDATE SET topic=excluded.topic, created=excluded.created""",
               (uid, crypto.enc(t), dbm.now()))
    return t


def topic_url(t):
    return url().rstrip("/") + "/" + t if url() else None


# ---------------------------------------------------------------- the only words, and the only request

def message(kind, name=None):
    """What a notification says: one of TEXTS, with the profile's name and nothing else."""
    if kind not in TEXTS:
        raise ValueError(f"no notification of kind {kind!r}")
    name = " ".join(str(name or "").split())[:NAME_MOST] or "a profile"
    return TEXTS[kind].format(name=name)


def request(db, kind, name=None, claim=False):
    """(body, headers) of the POST to a topic: message() and the headers ntfy reads, no more."""
    headers = {"Title": TITLE, "Content-Type": "text/plain; charset=utf-8"}
    if (click := config.public_address()):
        headers["Click"] = click
    if (t := token(db, claim)):
        headers["Authorization"] = "Bearer " + t
    return message(kind, name).encode("utf-8"), headers


def _local_only(p, u):
    """An http:// server: the address to connect to, every address its name has checked to be on the local network
    (None for an address written as one: settings.notify_url checked it). Raises SendError."""
    import ipaddress
    host = urllib.parse.urlsplit(u).hostname or ""
    try:
        ipaddress.ip_address(host.split("%")[0])
        return None
    except ValueError:
        pass
    try:
        ips = net.resolve(p, u)
    except net.Errors as e:
        raise SendError(f"The notification server couldn't be reached: {net.why(e)}.") from None
    if not ips or not all(settings.local_address(ipaddress.ip_address(i.split("%")[0])) for i in ips):
        raise SendError(f"{host} isn't on the local network, so it would be sent unencrypted over the internet: use https://.")
    return ips[0]


def send(db, kind, uid, name=None, p=None):
    """Send one notification to this person's topic now. Raises SendError."""
    u = url()
    if not u:
        raise SendError("No notification server is set.")
    t = topic(db, uid)
    p = p or permit(db)
    body, headers = request(db, kind, name, claim=True)
    full = topic_url(t)
    path = urllib.parse.urlsplit(full).path
    try:
        ip = _local_only(p, u) if urllib.parse.urlsplit(u).scheme == "http" else None
        conn = net.connection(p, full, timeout=TIMEOUT, ip=ip)
        try:
            conn.request("POST", path, body, headers)
            r = conn.getresponse()
            r.read(4096)
        finally:
            conn.close()
    except net.Refused as e:
        raise SendError(str(e)) from None
    except net.Errors as e:
        raise SendError(f"The notification server couldn't be reached: {net.why(e)}.") from None
    except (ValueError, UnicodeError):   # a header http.client won't send: said without it (it may be a token)
        raise SendError("The request couldn't be made: a header held something that can't be sent.") from None
    if r.status in (401, 403):
        raise SendError(f"The notification server refused the access token (it answered {r.status}).")
    if not 200 <= r.status < 300:
        raise SendError(f"The notification server answered {r.status}.")


_tested = {}


def test(db, who, ip=None):
    """A test notification to this person's own topic, sent now (logged as notify.test). Raises SendError."""
    now = time.monotonic()
    if now - _tested.get(who["id"], -TEST_EVERY) < TEST_EVERY:
        raise SendError("A test was just sent: wait a few seconds.")
    _tested[who["id"]] = now
    topic(db, who["id"])
    try:
        send(db, "test", who["id"], p=permit(db, who, ip))
    except SendError as e:
        activity.log(db, "notify.test", who, None, {"ok": False, "why": str(e)}, ip)
        raise
    activity.log(db, "notify.test", who, None, {"ok": True}, ip)
    return {"ok": True, "host": next(iter(hosts()), None)}


# ---------------------------------------------------------------- who wants which

_WANTED = """SELECT {what} FROM notify_optins o JOIN memberships m ON m.user_id = o.user_id AND m.profile_id = o.profile_id
             JOIN users u ON u.id = o.user_id WHERE o.profile_id=? AND o.kind=? AND u.disabled=0"""


def wanting(db, pid, kind):
    """The people who turned this kind on for the profile and can still see it."""
    return [r[0] for r in db.execute(_WANTED.format(what="o.user_id") + " ORDER BY o.user_id", (pid, kind))]


def wants(db, uid, pid, kind):
    return db.execute(_WANTED.format(what="1") + " AND o.user_id=?", (pid, kind, uid)).fetchone() is not None


def choices(db, uid):
    """The profiles this person can see, each with the kinds they turned on: [{"id", "name", "results", "doses"}]."""
    on = {}
    for r in db.execute("SELECT profile_id, kind FROM notify_optins WHERE user_id=?", (uid,)):
        on.setdefault(r["profile_id"], set()).add(r["kind"])
    return [{"id": r["id"], "name": r["name"], **{k: k in on.get(r["id"], ()) for k in KINDS}}
            for r in db.execute("""SELECT p.id, p.name FROM memberships m JOIN profiles p ON p.id = m.profile_id
                                   WHERE m.user_id=? ORDER BY p.name COLLATE NOCASE, p.id""", (uid,))]


def choose(db, uid, pid, kind, on):
    """Turn a kind on or off for a profile (the person's topic made, the first time); True when that changed something."""
    if kind not in KINDS:
        raise ValueError(kind)
    if on:
        topic(db, uid)
        return db.execute("INSERT OR IGNORE INTO notify_optins(user_id, profile_id, kind, created) VALUES(?, ?, ?, ?)",
                          (uid, pid, kind, dbm.now())).rowcount > 0
    return db.execute("DELETE FROM notify_optins WHERE user_id=? AND profile_id=? AND kind=?", (uid, pid, kind)).rowcount > 0


# ---------------------------------------------------------------- making them

def _make(db, key, uid, pid, kind):
    return db.execute("INSERT OR IGNORE INTO notify_sent(key, user_id, profile_id, kind, next_at, created) VALUES(?, ?, ?, ?, ?, ?)",
                      (key, uid, pid, kind, dbm.now(), dbm.now())).rowcount > 0


def new_results(db, pid, import_id):
    """A connected source's pull saved import_id, with new records: a notification for each person who wants them
    (inside the pull's transaction: a pull rolled back makes none), and the thread woken once it's saved."""
    if not configured():
        return
    made = [_make(db, f"results:{int(import_id)}:{uid}", uid, pid, "results") for uid in wanting(db, pid, "results")]
    if any(made):
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
    """Make the notifications of the doses due now: for each person who wants them, a time that passed less than
    WINDOW ago and after they turned them on. Returns how many were made."""
    from . import med_log, med_reminders, medications
    if not configured():
        return 0
    now, made = now or time.time(), 0
    for (pid,) in db.execute("SELECT DISTINCT profile_id FROM notify_optins WHERE kind='doses'").fetchall():
        people = {r[0]: r[1] for r in db.execute(_WANTED.format(what="o.user_id, o.created"), (pid, "doses"))}
        if not people:
            continue
        since = min(people.values())
        due = []   # (line, day, "HH:MM") whose time has come: the records read only when there's one
        for r in med_reminders.listing(db, pid):
            if not r["on"] or r["paused"] or r["asneeded"] or not r["times"] or not r["from"]:
                continue
            zone = _zone(db, r, pid)
            today = (datetime.fromtimestamp(now, zone) if zone is not None else datetime.fromtimestamp(now)).date()
            for day in (today - timedelta(days=1), today):
                for hm in sorted({at for at, _ in med_log.scheduled([r], day)}):
                    at = _at(day, hm, zone)
                    if max(since, now - WINDOW) <= at <= now:
                        due.append((r, day, hm, at))
        if not due:
            continue
        meds = med_reminders.current(db, pid)
        for r, day, hm, at in due:   # of a medication still in the records, and not ended by the day they say it stopped
            m = meds.get(r["med"])   # (#1161: nor on a day between the times it was taken)
            if not m or hm not in {t for t, _ in med_log.scheduled([r], day, m.get("stop"), medications.gaps(m))}:
                continue
            for uid, created in people.items():
                if at >= created and _make(db, f"doses:{pid}:{day.isoformat()}:{hm}:{uid}", uid, pid, "doses"):
                    made += 1
    return made


# ---------------------------------------------------------------- sending them

def _still_wanted(db, r, now):
    if r["profile_id"] is None or not wants(db, r["user_id"], r["profile_id"], r["kind"]):
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
            send(db, r["kind"], r["user_id"], name, p)
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
        except Exception as e:   # (its type alone: what it says could quote a header, a topic or a token)
            log.error("notifications: %s", type(e).__name__)
        _wake.wait(TICK)
        _wake.clear()
