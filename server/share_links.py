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
no other profile, nothing to change. Records without a date are left out, since the range can't be judged.

  sections   chosen from SECTIONS; each says plainly what it shows (the owner's page repeats it)
  labs       for "results": the lab series to show, by name; empty = every lab
  from, to   ISO days, both optional (no "to": records added later in the range show too). Upcoming appointments
             are also only those from the day of the view on.

Every view is logged in the profile's activity ("link", {"change": "view", "link", "label"}) under the actor
SHARE_LINK, with the viewer's address, never the token; so are making and revoking ("create", "revoke"). The
label and scope are encrypted at rest (atrest.FIELDS): lab names and a clinician's name are health information.
"""
import json, re
from datetime import date

from . import activity, crypto, db as dbm, records, security as sec, settings, summary

SECTIONS = [
    ("visits", "Visits and hospital stays", "the date, the title and the clinic or clinician the record names"),
    ("results", "Lab results", "each result's value and unit, with the reference range and flag printed on its own report"),
    ("medications", "Medications", "the name, the instructions and the dates"),
    ("procedures", "Procedures, imaging and reports", "titles and dates only: not the reports' text, and no documents"),
    ("appointments", "Upcoming appointments", "the date and time, the title, with whom and where"),
    ("journal", "Journal", "the notes, symptoms and side effects written here, with what they say (not who wrote them)"),
    ("costs", "Costs and claims", "each claim's date, provider, what was billed, what insurance paid and your share"),
]
SECTION_IDS = [s for s, _, _ in SECTIONS]
VISIT_KINDS, PROCEDURE_KINDS = ("visit", "stay"), ("procedure", "imaging", "study")
MAX_HOURS = 90 * 24          # a link lasts at most 90 days
MAX_ACTIVE = 20              # working links per profile
MAX_LABS, LABEL_MAX = 100, 80
KEEP_ENDED_DAYS = 30         # expired or revoked links stay listed (for the owner's record) this long
SHARE_LINK = activity.SHARE_LINK


class LinkError(ValueError):
    pass


class ShareThrottle(sec.Throttle):
    """The sign-in throttle's rules for views: wrong tokens per address (bad:), and every view per link (link:), so
    a leaked link can't be scraped quickly. Opening the page is one view."""
    FREE = {"bad": 10, "link": 60}


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


def viewed(db, link, ip):
    """Count the view and log it on the profile's activity (call inside a transaction)."""
    db.execute("UPDATE share_links SET views=views+1, last_viewed=? WHERE id=?", (dbm.now(), link["id"]))
    detail = {"change": "view", "link": link["id"]}
    if link["label"]:
        detail["label"] = crypto.dec(link["label"])
    activity.log(db, "link", None, link["profile_id"], detail, ip, actor=SHARE_LINK)


def _in(d, scope):
    return bool(d) and (not scope["from"] or d >= scope["from"]) and (not scope["to"] or d <= scope["to"])


def _text(v, n=500):
    return " ".join(str(v or "").split())[:n]


def build(db, link, today=None):
    """What the holder of the link sees: the profile's name, the link's terms, and each chosen section, cut to the
    range. Only fields named here leave the server."""
    scope, pid = json.loads(crypto.dec(link["scope"])), link["profile_id"]
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
    if "results" in want:
        pick = set(scope["labs"])
        labs = []
        for name, s in sorted(summary._series(data).items(), key=lambda kv: kv[0].lower()):
            if pick and name not in pick:
                continue
            pts = [{"t": _text(p["t"], 25), "value": summary.value_text(p), "range": summary.range_text(p),
                    "flag": summary.out_of_range(p)} for p in s["pts"] if _in(p["d"], scope)]
            if pts:
                labs.append({"name": name, "unit": s["unit"], "results": pts[::-1]})
        out["results"] = labs
    if "appointments" in want:
        ups = [u for u in data.get("upcoming") or [] if isinstance(u, dict)]
        out["appointments"] = sorted(({"t": _text(u.get("t"), 25), "title": _text(u.get("title"), 300) or "Appointment",
                                       "who": _text(u.get("who"), 200), "where": _text(u.get("where"), 200)}
                                      for u in ups if _in(summary._day(u.get("t")), scope) and summary._day(u.get("t")) >= today),
                                     key=lambda u: u["t"])
    if "journal" in want:
        out["journal"] = sorted(({"t": _text(j.get("t"), 25), "kind": j.get("kind"), "title": _text(j.get("title"), 300),
                                  "severity": j.get("severity"), "note": str(j.get("note") or "")[:4000]}
                                 for j in data.get("journal") or [] if isinstance(j, dict) and _in(summary._day(j.get("t")), scope)),
                                key=lambda j: j["t"], reverse=True)
    if "costs" in want:
        num = lambda v: v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        out["costs"] = sorted(({"t": _text(c.get("d"), 25), "title": _text(c.get("title"), 300), "provider": _text(c.get("prov"), 200),
                                "billed": num(c.get("billed")), "paid": num(c.get("paid")), "owe": num(c.get("owe"))}
                               for c in data.get("costs") or [] if isinstance(c, dict) and _in(summary._day(c.get("d")), scope)),
                              key=lambda c: c["t"], reverse=True)
    return out
