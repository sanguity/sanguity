"""Share links (#47): a read-only view of part of a profile for someone without an account, such as a clinician or a
relative. An owner of the profile makes the link and chooses what it shows: which sections, which labs, and a date
range. It stops working when it expires (at most MAX_HOURS after it was made) or when any owner revokes it.

The link is share.html#<token>. The token is random (security.new_token, 256 bits); only its SHA-256 is stored
(share_links.token_hash), like a session cookie's or a calendar feed's (calendar.py), and it is shown once, when
the link is made. It sits in the address's fragment, which the browser never sends: the page posts it to
POST /api/share/view in the request body, so it stays out of every URL a server, proxy or log sees.

A link works only while:
  - it hasn't expired and hasn't been revoked;
  - the owner who made it is still an owner of the profile, with an account that isn't disabled. An owner who
    leaves the profile, loses the owner role or has their account removed takes their links with them.

What a holder sees is built here from the records at each view (records.served, with corrections applied), cut to
the link's scope; nothing else of the profile is reachable through it: no documents, no report text, no settings,
no other profile, nothing to change. Records without a date are left out, since the range can't be judged. About
this person (#194: birth date, sex used for reference ranges, heights) is shown only when its section is ticked, and
the owner's page leaves it unticked. So are the phone's and watch's measures and the newer kinds of record (#225,
server/measures.py: home blood pressure, glucose, temperature, blood oxygen, respiratory rate, VO2 max, immunizations,
ECGs and irregular rhythm notifications as the device stated them, never their samples, BMI and growth): each a
section of its own, offered only when the profile has some, shown only when ticked, in the units of the owner who made
the link. Cycle tracking (#183) is never offered here, whatever the profile's setting. Labs go by the names the profile
gives them (Settings → Names, labNicknames; #289), each with its report's name and the panel it came in.

  sections   chosen from SECTIONS; each says plainly what it shows (the owner's page repeats it)
  labs       for "results": the lab series to show, by name; empty = every lab
  from, to   ISO days, both optional (no "to": records added later in the range show too). Upcoming appointments
             are also only those from the day of the view on.

A long section (#498: ten years are 2,000 claims and 1,000 readings of a home measure) comes in two steps: the view
sends its latest FIRST rows when it has more than MANY, and says how many there are ("cut": {section: rows}); the
page's Show all asks for that one section in full ({"token", "section"}), which is logged as a view of it.

Every view is logged in the profile's activity ("link", {"change": "view", "link", "label"}, and "all": the section's
name for a Show all) under the actor SHARE_LINK, with the viewer's address, never the token; so are making and
revoking ("create", "revoke"). The
label and scope are encrypted at rest (atrest.FIELDS): lab names and a clinician's name are health information.
"""
import json, re
from datetime import date

from . import activity, crypto, db as dbm, journal, journal_photos, measures, person, records, security as sec, settings, summary

SECTIONS = [
    ("visits", "Visits and hospital stays", "the date, the title and the clinic or clinician the record names"),
    ("results", "Lab results", "each result's value and unit, with the reference range and flag printed on its own report"),
    ("medications", "Medications", "the name, the instructions and the dates"),
    ("procedures", "Procedures, imaging and reports", "titles and dates only: not the reports' text, and no documents"),
    ("appointments", "Upcoming appointments", "the date and time, the title, with whom and where"),
    ("journal", "Journal", "the notes, symptoms, side effects and moods written here, with what they say, the medication "
                "an entry is linked to and where on the body (not who wrote them)"),   # #523: any entry's, as sent
    ("journal_photos", "Photos in the journal", "the photos attached to the journal entries shown, with what each "
                       "shows (only with Journal; offered when there are some)"),   # #161: never unless ticked
    ("costs", "Costs and claims", "each claim's date, provider, what was billed, what insurance paid and your share"),
    ("about", "About this person", "the birth date, the sex labs base reference ranges on, and the heights written down "
              "(those in the dates chosen)"),   # #194: never unless ticked (the owner's page leaves it unticked)
    *measures.SECTIONS,   # #225: the phone's and watch's measures, immunizations, ECGs, BMI; never unless ticked
]
SECTION_IDS = [s for s, _, _ in SECTIONS]
VISIT_KINDS, PROCEDURE_KINDS = ("visit", "stay"), ("procedure", "imaging", "study")
MAX_HOURS = 90 * 24          # a link lasts at most 90 days
MAX_ACTIVE = 20              # working links per profile
MAX_LABS, LABEL_MAX = 100, 80
KEEP_ENDED_DAYS = 30         # expired or revoked links stay listed (for the owner's record) this long
SHARE_LINK = activity.SHARE_LINK
FIRST, MANY = 20, 40         # a long section's view: its latest FIRST rows when it has more than MANY (#498)
LONG = ("visits", "procedures", "costs", *measures.IDS)   # the sections that can be long (newest first), as share.js cuts them


class LinkError(ValueError):
    pass


class ShareThrottle(sec.Throttle):
    """The sign-in throttle's rules for views: wrong tokens per address (bad:), and every view per link (link:), so
    a leaked link can't be scraped quickly. Opening the page is one view."""
    FREE = {"bad": 10, "link": 60, "photo": 60}   # photo: the journal photos a page shows (#161)


throttle = ShareThrottle()


# ---------------------------------------------------------------- the owner's side

def _day(v, what):
    if v in (None, ""):
        return None
    if not isinstance(v, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        raise LinkError(f"The {what} date must look like 2026-11-15.")
    try:
        date.fromisoformat(v)
    except ValueError:
        raise LinkError(f"The {what} date isn't a real date.") from None
    return v


def clean(label, sections, labs, start, end, hours):
    """The link's settings, checked; returns (label, scope). Raises LinkError with what's wrong."""
    label = " ".join(str(label or "").split())
    if len(label) > LABEL_MAX:
        raise LinkError(f"The name can be at most {LABEL_MAX} characters.")
    if not isinstance(sections, list) or not sections:
        raise LinkError("Choose at least one section to share.")
    if any(s not in SECTION_IDS for s in sections):
        raise LinkError("There's no such section.")
    sections = [s for s in SECTION_IDS if s in sections]   # in the page's order, once each
    labs = sorted({" ".join(str(n).split()) for n in (labs or []) if str(n).strip()})
    if len(labs) > MAX_LABS or any(len(n) > 200 for n in labs):
        raise LinkError(f"Pick at most {MAX_LABS} labs.")
    if labs and "results" not in sections:
        raise LinkError("Labs were picked, but Lab results isn't one of the sections.")
    if "journal_photos" in sections and "journal" not in sections:   # #161
        raise LinkError("Photos in the journal are shown with their entries: tick Journal too.")
    start, end = _day(start, "start"), _day(end, "end")
    if start and end and start > end:
        raise LinkError("The start date is after the end date.")
    if not isinstance(hours, int) or isinstance(hours, bool) or not 1 <= hours <= MAX_HOURS:
        raise LinkError(f"A link can last from an hour to {MAX_HOURS // 24} days.")
    return label, {"sections": sections, "labs": labs, "from": start, "to": end}


def _prune(db, pid):
    db.execute("DELETE FROM share_links WHERE profile_id=? AND IFNULL(revoked, expires) < ?",
               (pid, dbm.now() - KEEP_ENDED_DAYS * 86400))


def _working_sql():
    """The conditions under which a link opens (alias l): not revoked, not expired, its maker still an owner."""
    return """l.revoked IS NULL AND l.expires > ?
              AND EXISTS (SELECT 1 FROM memberships m JOIN users u ON u.id = m.user_id
                          WHERE m.user_id = l.created_by AND m.profile_id = l.profile_id AND m.role = 'owner' AND u.disabled = 0)"""


def _out(db, r, now):
    scope = json.loads(crypto.dec(r["scope"]))
    working = bool(db.execute(f"SELECT 1 FROM share_links l WHERE l.id=? AND {_working_sql()}", (r["id"], now)).fetchone())
    state = ("revoked" if r["revoked"] else "expired" if r["expires"] <= now else "active" if working else "maker_not_owner")
    return {"id": r["id"], "label": crypto.dec(r["label"]) if r["label"] else "", **scope, "created": r["created"],
            "created_by": r["created_name"], "expires": r["expires"], "revoked": r["revoked"], "revoked_by": r["revoked_name"],
            "views": r["views"], "last_viewed": r["last_viewed"], "state": state}


def listing(db, pid):
    """The profile's links, newest first, working or ended within KEEP_ENDED_DAYS (never their tokens)."""
    now = dbm.now()
    return [_out(db, r, now) for r in db.execute("SELECT * FROM share_links WHERE profile_id=? ORDER BY id DESC", (pid,))]


def create(db, pid, who, label, scope, hours):
    """A new link (call inside a transaction); returns (its token, its id). who: the owner's session row."""
    _prune(db, pid)
    now = dbm.now()
    n = db.execute(f"SELECT COUNT(*) FROM share_links l WHERE l.profile_id=? AND {_working_sql()}", (pid, now)).fetchone()[0]
    if n >= MAX_ACTIVE:
        raise LinkError(f"This profile already has {MAX_ACTIVE} working links. Revoke one first.")
    token = sec.new_token()
    lid = db.execute("""INSERT INTO share_links(profile_id, created_by, created_name, token_hash, label, scope, created, expires)
                        VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
                     (pid, who["id"], who["username"], sec.token_hash(token), crypto.enc(label) if label else None,
                      crypto.enc(json.dumps(scope, separators=(",", ":"), ensure_ascii=False)), now, now + hours * 3600)).lastrowid
    return token, lid


def get(db, pid, lid):
    r = db.execute("SELECT * FROM share_links WHERE id=? AND profile_id=?", (lid, pid)).fetchone()
    return None if r is None else _out(db, r, dbm.now())


def revoke(db, pid, lid, who):
    """Stop a link at once (call inside a transaction). Returns its label, or None if there's no such working one."""
    r = db.execute("SELECT label FROM share_links WHERE id=? AND profile_id=? AND revoked IS NULL", (lid, pid)).fetchone()
    if r is None:
        return None
    db.execute("UPDATE share_links SET revoked=?, revoked_name=? WHERE id=?", (dbm.now(), who["username"], lid))
    return crypto.dec(r["label"]) if r["label"] else ""


def offered(db, pid, uid):
    """The sections the owner's form offers (#225): every one, but a measure only when the profile has some of it."""
    body = records.served(db, pid)
    have = set(measures.offered(measures.context(db, pid, json.loads(body) if body else {}, settings.merged(db, pid, uid))))
    photos = journal_photos.any_attached(db, pid)   # #161: offered only when an entry has some
    return [{"id": i, "label": l, "what": w} for i, l, w in SECTIONS
            if (i not in measures.IDS or i in have) and (i != "journal_photos" or photos)]


def lab_names(db, pid):
    """The profile's lab series names, to pick from (watch metrics aren't labs)."""
    body = records.served(db, pid)
    data = json.loads(body) if body else {}
    return sorted({s["name"] for s in data.get("labs") or [] if isinstance(s, dict) and s.get("name") and not s.get("watch")},
                  key=str.lower)


# ---------------------------------------------------------------- the holder's side

def find(db, token):
    """The working link this token opens, or None. Anything malformed is simply not found."""
    if not isinstance(token, str) or not 20 <= len(token) <= 100 or not re.fullmatch(r"[A-Za-z0-9_-]+", token):
        return None
    return db.execute(f"""SELECT l.*, p.name AS profile_name FROM share_links l JOIN profiles p ON p.id = l.profile_id
                          WHERE l.token_hash=? AND {_working_sql()}""", (sec.token_hash(token), dbm.now())).fetchone()


def viewed(db, link, ip, section=None):
    """Count the view and log it on the profile's activity (call inside a transaction). section: a long section asked
    for in full (#498), logged as a view of it but not counted as another opening of the link."""
    more = "" if section else "views=views+1, "
    db.execute(f"UPDATE share_links SET {more}last_viewed=? WHERE id=?", (dbm.now(), link["id"]))
    detail = {"change": "view", "link": link["id"]}
    if link["label"]:
        detail["label"] = crypto.dec(link["label"])
    if section:
        detail["all"] = next(l for i, l, _ in SECTIONS if i == section)
    activity.log(db, "link", None, link["profile_id"], detail, ip, actor=SHARE_LINK)


def photo(db, link, photo_id):
    """(bytes, media type) of a journal photo this link shows (#161): "Photos in the journal" ticked, and an entry in
    the link's dates lists it. None otherwise."""
    scope = json.loads(crypto.dec(link["scope"]))
    if "journal_photos" not in scope["sections"] or "journal" not in scope["sections"]:
        return None
    pid = link["profile_id"]
    if not any(p.get("id") == photo_id for j in journal.listing(db, pid) if _in(summary._day(j.get("t")), scope)
               for p in j.get("photos") or []):
        return None
    return journal_photos.get(db, pid, photo_id)


def _in(d, scope):
    return bool(d) and (not scope["from"] or d >= scope["from"]) and (not scope["to"] or d <= scope["to"])


def _text(v, n=500):
    return " ".join(str(v or "").split())[:n]


def shows(link, section):
    """Whether section is one of the link's long ones, which its Show all may ask for in full (#498)."""
    return section in LONG and section in json.loads(crypto.dec(link["scope"]))["sections"]


def build(db, link, today=None, section=None):
    """What the holder of the link sees: the profile's name, the link's terms, and each chosen section, cut to the
    range; a long one, to its latest rows, with how many there are ("cut", #498). section: only that one of the link's
    long sections (shows()), in full, for its Show all. Only fields named here leave the server."""
    out = _build(db, link, today, section)
    if section:
        return {section: out[section]}
    cut = {}
    for s in LONG:
        got = out.get(s)
        rows = got.get("rows") if isinstance(got, dict) else got
        if isinstance(rows, list) and len(rows) > MANY:
            cut[s] = len(rows)
            out[s] = {**got, "rows": rows[:FIRST]} if isinstance(got, dict) else rows[:FIRST]
    out["cut"] = cut
    return out


def _build(db, link, today, section):
    scope, pid = json.loads(crypto.dec(link["scope"])), link["profile_id"]
    if section:   # only that section's records are read and built
        scope = {**scope, "sections": [section]}
    today = today or settings.local_now(db, link["created_by"]).date().isoformat()
    body = records.served(db, pid)
    data = json.loads(body) if body else {}
    events = [e for e in data.get("events") or [] if isinstance(e, dict) and not e.get("hidden")]
    details = data.get("details") or {}
    out = {"profile": link["profile_name"], "expires": link["expires"], "from": scope["from"], "to": scope["to"],
           "sections": [{"id": s, "label": l, "what": w} for s, l, w in SECTIONS if s in scope["sections"]]}
    want = set(scope["sections"])

    def rows(kinds):
        got = [{"t": _text(e.get("t"), 25), "end": _text(e.get("end"), 25) or None, "kind": e.get("k"),
                "title": _text(e.get("title"), 300), "sub": _text(e.get("sub"), 300)}
               for e in events if e.get("k") in kinds and _in(summary._day(e.get("t")), scope)]
        return sorted(got, key=lambda r: r["t"], reverse=True)

    if "visits" in want:
        out["visits"] = rows(VISIT_KINDS)
    if "procedures" in want:
        out["procedures"] = rows(PROCEDURE_KINDS)
    if "medications" in want:
        meds = []
        for e in events:
            if e.get("k") != "med":
                continue
            d = details.get(str(e.get("id"))) or {}
            start = summary._day(e.get("t"))
            stop = summary._day(e.get("end") or d.get("end") or d.get("stop"))
            if not start or (scope["to"] and start > scope["to"]) or (scope["from"] and stop and stop < scope["from"]):
                continue
            meds.append({"name": _text(e.get("title") or d.get("name") or "Medication", 200),
                         "instructions": _text(d.get("instructions")), "since": start, "until": stop,
                         "status": _text(d.get("status"), 40)})
        out["medications"] = sorted(meds, key=lambda m: (m["name"].lower(), m["since"]))
    cfg = None

    def owners():   # the settings of the owner who made the link: their units (#225) and the profile's names for labs (#289)
        nonlocal cfg
        cfg = cfg or settings.merged(db, pid, link["created_by"])
        return cfg

    if "results" in want:   # (#289) each lab by the profile's name for it (Settings → Names), with its panel; v: the number, for a sparkline
        pick, nick = set(scope["labs"]), owners().get("labNicknames") or {}
        panel = {}
        for s in data.get("labs") or []:
            if isinstance(s, dict) and s.get("name") and s.get("panel") and not s.get("watch"):
                panel.setdefault(s["name"], _text(s["panel"], 120))
        labs = []
        for name, s in summary._series(data).items():
            if pick and name not in pick:
                continue
            pts = [{"t": _text(p["t"], 25), "value": summary.value_text(p), "v": p["v"], "range": summary.range_text(p),
                    "flag": summary.out_of_range(p)} for p in s["pts"] if _in(p["d"], scope)]
            if pts:
                label = nick.get(name) if isinstance(nick.get(name), str) and nick[name].strip() else name
                labs.append({"name": name, "label": _text(label, 200), "panel": panel.get(name, ""), "unit": s["unit"],
                             "results": pts[::-1]})
        out["results"] = sorted(labs, key=lambda x: x["label"].lower())
    if "appointments" in want:
        ups = [u for u in data.get("upcoming") or [] if isinstance(u, dict)]
        out["appointments"] = sorted(({"t": _text(u.get("t"), 25), "title": _text(u.get("title"), 300) or "Appointment",
                                       "who": _text(u.get("who"), 200), "where": _text(u.get("where"), 200)}
                                      for u in ups if _in(summary._day(u.get("t")), scope) and summary._day(u.get("t")) >= today),
                                     key=lambda u: u["t"])
    if "journal" in want:   # with where on the body, the group and the day's mood (#161), an entry's medication
        pics = "journal_photos" in want   # (#518); photos only when ticked
        out["journal"] = sorted(({"t": _text(j.get("t"), 25), "kind": j.get("kind"), "title": _text(j.get("title"), 300),
                                  "severity": j.get("severity"), "note": str(j.get("note") or "")[:4000],
                                  **({"med": _text(j["med"], journal.MED_MAX)} if j.get("med") else {}),
                                  **({"mood": j.get("mood"), "energy": j.get("energy")} if j.get("kind") == "mood" else {}),
                                  **({"where": [journal.REGIONS[w] for w in j["where"] if w in journal.REGIONS]} if j.get("where") else {}),
                                  **({"group": _text(j["group"], 80)} if j.get("group") else {}),
                                  **({"photos": [{"id": p["id"], "alt": _text(p.get("alt"), 300)} for p in j["photos"]]}
                                     if pics and j.get("photos") else {})}
                                 for j in data.get("journal") or [] if isinstance(j, dict) and _in(summary._day(j.get("t")), scope)),
                                key=lambda j: j["t"], reverse=True)
    if "costs" in want:
        num = lambda v: v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        out["costs"] = sorted(({"t": _text(c.get("d"), 25), "title": _text(c.get("title"), 300), "provider": _text(c.get("prov"), 200),
                                "billed": num(c.get("billed")), "paid": num(c.get("paid")), "owe": num(c.get("owe"))}
                               for c in data.get("costs") or [] if isinstance(c, dict) and _in(summary._day(c.get("d")), scope)),
                              key=lambda c: c["t"], reverse=True)
    mw = [s for s in scope["sections"] if s in measures.IDS]
    if mw:   # (#225) in the units of the owner who made the link
        c = measures.context(db, pid, data, owners())
        out.update(measures.blocks(c, mw, lambda d: _in(d, scope), most=measures.LINK_MOST))
    if "about" in want:   # About this person (#194), only when the owner ticked it
        p = person.of(settings.nest(settings.stored(db, "profile", pid)))
        out["about"] = {"birthDate": p["birthDate"], "sex": person.SEXES[p["sex"]] if p["sex"] else "",
                        "heights": [{"date": h["date"], "cm": h["cm"],
                                     "text": f"{person.text(h['cm'])} ({person.text(h['cm'], 'ftin')})"}
                                    for h in reversed(p["heights"]) if _in(h["date"], scope)]}
    return out
