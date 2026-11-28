"""Insurance cards (#1137): each health plan of a profile, with what its card says and a photo of each side.

A plan is a row of insurance_plans (schema version 55, db.py): its body, encrypted at rest (atrest.FIELDS), is
{"name", "member", "group", "phones": [{"label", "number"}]}: the plan's name, the member ID, the group number and
the phone numbers on the card; and since #1168 (still the body, so no schema step: a plan kept before has them empty,
DEFAULTS) "kind" (KINDS: medical, dental, vision, pharmacy, or ""), "order" (primary, secondary, or ""), "holder" and
"relation" (the policyholder's name and the person's relationship to them, RELATIONS), "start" and "end" (the
plan's dates, "YYYY-MM-DD" or ""; a plan whose end has passed is listed under Past, ended()), "rxbin", "rxpcn",
"rxgroup" (the pharmacy numbers), "payer" (the payer ID), "website" (an http(s) address), "years" (the starts of the
Costs tab's plan years it's linked to, costplan.py #1127: a start names one, an export's included), "payers" (the
insurer names its claims carry, as the records' Costs rows say them) and "card" (CARD_PARTS: what the emergency
card's line says besides its name and numbers, as the person chooses). A member ID is as good as a password at a front desk, so it's never logged: the
activity log says only that a plan was added, changed or removed, or a photo put on or taken off ("insurance",
{"change", "side"?}), never its name or numbers.

The photos are kept like the journal's (journal_photos.py): checked and cleaned the same way (JPEG, PNG or WebP, its
metadata out, at most JOURNAL_PHOTO_MAX_MB), sealed (crypto.write_file) under a random name in
profiles/<id>/insurance/, the row naming the file and its type for each side. A plan is saved first; then each side's
photo is sent on its own (PUT .../insurance/{id}/front, the request body the picture), replacing the one there, its
file removed once that's saved (dbm.after_commit). Removing the plan removes its photos. At start-up, a file in an
insurance folder no plan names (a stop between the two) is removed (sweep_all).

Owners and editors add, change and remove plans; everyone the profile is shared with sees them (routes_insurance.py).
A share link shows them only when its maker ticks Insurance cards (share_links.py, off by default); "Export
everything" has them (data.json's "insurance", insurance/<plan>-<side>.<ext>), and loading an export puts them back
(restore). Backups have the profile's folder and the database, so their rows and photos with it (backup.named_files
checks the photos are there).

card_lines(db, pid) gives each plan's name, member ID and group number, no photos, for the printable emergency card
(#1126), and the new fields under keys of their own (#1168).

The photos are of the card alone: the page finds the card's edges in the picture taken or chosen, straightens and crops
it (www/js/cardcrop.js, in the browser), and sends only that; the original never leaves the device.

Help filling a plan in (#1168), never saved by itself: from_text(text) reads what text recognition (ocr.read_picture,
on this server) found on a card's photo for the member ID, group number, plan name, pharmacy numbers, payer ID,
website and phone numbers; from_records(data, plans, years) suggests plans from the imported claims (the FHIR
Coverage an ExplanationOfBenefit names: _fhir_claims.py keeps its payer, plan, member ID and the rest on the claim's
detail) and lists the claims' insurers and the plan years to link to. The page shows each as a suggestion to use or
leave; only what the person then saves is kept."""
import datetime, json, os, re, secrets, urllib.parse

from . import config, crypto, db as dbm, journal_photos as photos

SIDES = ("front", "back")
MAX_PLANS, MAX_PHONES, MAX_LINKS = 10, 4, 20
NAME_MAX, ID_MAX, LABEL_MAX, NUMBER_MAX, URL_MAX = 120, 60, 40, 40, 200
KINDS = {"medical": "Medical", "dental": "Dental", "vision": "Vision", "pharmacy": "Pharmacy"}
ORDERS = {"primary": "Primary", "secondary": "Secondary"}
RELATIONS = {"self": "Self", "spouse": "Spouse or partner", "child": "Child", "other": "Other"}
# what the emergency card's line can say besides the plan's name, member ID and group number (#1162, #1168)
CARD_PARTS = {"kind": "Plan type and order", "holder": "Policyholder", "rx": "Pharmacy numbers", "payer": "Payer ID",
              "phones": "Phone numbers"}
FIELDS = ("kind", "order", "holder", "relation", "start", "end", "rxbin", "rxpcn", "rxgroup", "payer", "website")
DEFAULTS = {**dict.fromkeys(FIELDS, ""), "years": [], "payers": [], "card": []}
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NUMBER = re.compile(r"^[0-9A-Za-z+().,#*/\- ]+$")   # a phone number as a card prints it: "1-800-555-0100 ext. 2"


class InsuranceError(ValueError):
    """Refused, with a message for the person and an HTTP status."""
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _text(v, n, what, need=False):
    if v is None:
        v = ""
    if not isinstance(v, str):
        raise InsuranceError(f"The {what} must be text.")
    v = " ".join(v.split())
    if need and not v:
        raise InsuranceError(f"Give the {what}.")
    if len(v) > n:
        raise InsuranceError(f"The {what} can be at most {n} characters.")
    return v


def check(body):
    """The plan as kept, checked: {"name", "member", "group", "phones"}, or InsuranceError saying what's wrong."""
    if not isinstance(body, dict):
        raise InsuranceError("A plan must be an object.")
    phones = body.get("phones") or []
    if not isinstance(phones, list):
        raise InsuranceError("The phone numbers must be a list.")
    out = []
    for p in phones:
        if not isinstance(p, dict):
            raise InsuranceError("Each phone number must be an object.")
        label, number = _text(p.get("label"), LABEL_MAX, "phone number's label"), _text(p.get("number"), NUMBER_MAX, "phone number")
        if not number:
            if label:
                raise InsuranceError(f"Give the number for “{label}”, or leave its label empty.")
            continue
        if not _NUMBER.match(number):
            raise InsuranceError("A phone number can have digits, letters, spaces and + - ( ) . , # * / only.")
        out.append({"label": label, "number": number})
    if len(out) > MAX_PHONES:
        raise InsuranceError(f"A plan can have at most {MAX_PHONES} phone numbers.")
    start, end = _day(body.get("start"), "start date"), _day(body.get("end"), "end date")
    if start and end and end < start:
        raise InsuranceError("The end date can't be before the start date.")
    years = sorted({_day(y, "plan year's start") for y in _list(body.get("years"), "plan years")} - {""})
    payers = []
    for x in _list(body.get("payers"), "insurers of the claims"):
        x = _text(x, NAME_MAX, "insurer's name")
        if x and x.lower() not in {p.lower() for p in payers}:
            payers.append(x)
    card = set(_list(body.get("card"), "emergency card's parts"))
    return {"name": _text(body.get("name"), NAME_MAX, "plan's name", need=True),
            "member": _text(body.get("member"), ID_MAX, "member ID"),
            "group": _text(body.get("group"), ID_MAX, "group number"), "phones": out,
            "kind": _one(body.get("kind"), KINDS, "plan type"), "order": _one(body.get("order"), ORDERS, "plan's order"),
            "holder": _text(body.get("holder"), NAME_MAX, "policyholder's name"),
            "relation": _one(body.get("relation"), RELATIONS, "relationship to the policyholder"),
            "start": start, "end": end,
            "rxbin": _text(body.get("rxbin"), ID_MAX, "RxBIN"), "rxpcn": _text(body.get("rxpcn"), ID_MAX, "RxPCN"),
            "rxgroup": _text(body.get("rxgroup"), ID_MAX, "RxGroup"), "payer": _text(body.get("payer"), ID_MAX, "payer ID"),
            "website": _website(body.get("website")), "years": years, "payers": payers,
            "card": [k for k in CARD_PARTS if k in card]}


def _list(v, what):
    if v is None:
        return []
    if not isinstance(v, list) or len(v) > MAX_LINKS:
        raise InsuranceError(f"The {what} must be a list of at most {MAX_LINKS}.")
    return v


def _one(v, choices, what):
    v = v or ""
    if v != "" and v not in choices:
        raise InsuranceError(f"That isn't a {what} this knows.")
    return v


def _day(v, what):
    v = _text(v, 10, what)
    if not v:
        return ""
    try:
        if not _DAY.match(v):
            raise ValueError
        datetime.date.fromisoformat(v)
    except ValueError:
        raise InsuranceError(f"The {what} must be a date (YYYY-MM-DD).") from None
    return v


def _website(v):
    v = _text(v, URL_MAX, "plan's website")
    if not v:
        return ""
    if not re.match(r"^https?://", v, re.I):
        v = "https://" + v
    try:
        u = urllib.parse.urlsplit(v)
        host = u.hostname
    except ValueError:
        host = None
    if not host or "." not in host or " " in v or u.username or u.password:
        raise InsuranceError("The plan's website must be an address such as www.example.com.")
    if len(v) > URL_MAX:
        raise InsuranceError(f"The plan's website can be at most {URL_MAX} characters.")
    return v


def ended(plan, today=None):
    """Whether a plan's end date has passed: it's listed under Past, and left off the emergency card."""
    return bool(plan.get("end")) and plan["end"] < (today or datetime.date.today()).isoformat()


# ---------------------------------------------------------------- stored

def folder(pid):
    return os.path.join(config.DATA_DIR, "profiles", str(int(pid)), "insurance")   # profiles.profile_dir(pid)/insurance


def _path(pid, file):
    return os.path.join(folder(pid), file)


def _unlink(pid, file):
    try:
        os.remove(_path(pid, file))
    except FileNotFoundError:
        pass


def _enc(body):
    return crypto.enc(json.dumps(body, separators=(",", ":"), ensure_ascii=False))


def _dec(text):
    return json.loads(crypto.dec(text))


def _rows(db, pid):
    return db.execute("SELECT * FROM insurance_plans WHERE profile_id=? ORDER BY id", (pid,)).fetchall()


def _out(r):
    return {"id": r["id"], **DEFAULTS, **_dec(r["body"]),
            **{s: ({"type": r[s + "_type"]} if r[s] else None) for s in SIDES},
            "by": r["created_name"], "at": r["created"], "changed_by": r["changed_name"], "changed": r["changed"]}


def listing(db, pid):
    """The profile's plans, in the order they were added: each with its fields, and for each side {"type"} or None."""
    return [_out(r) for r in _rows(db, pid)]


def get(db, pid, iid):
    r = db.execute("SELECT * FROM insurance_plans WHERE id=? AND profile_id=?", (iid, pid)).fetchone()
    return None if r is None else _out(r)


def any_plan(db, pid):
    return bool(db.execute("SELECT 1 FROM insurance_plans WHERE profile_id=? LIMIT 1", (pid,)).fetchone())


def card_lines(db, pid):
    """For the printable emergency card (#1126): [{"line", "name", "member", "group", ...}] of each plan, in order,
    but those that have ended (#1168). line: the plan in one plain line ("Fictional Plan · Member ID M1 · Group G1",
    the parts it has), then the parts of CARD_PARTS the person chose for it, its "card" ("Medical, primary",
    "Policyholder Sam Example (Spouse or partner)", "RxBIN 1 · RxPCN 2 · RxGroup 3", "Payer ID 4", "Member services
    1-800-..."); "full" the same with every part it has; "parts" {part: its words}; and each field under its own key
    (FIELDS, and "phones"), so a card can say what the person picks. No photos."""
    out = []
    for p in listing(db, pid):
        if ended(p):
            continue
        base = [p["name"], *([f"Member ID {p['member']}"] if p["member"] else []), *([f"Group {p['group']}"] if p["group"] else [])]
        parts = card_parts(p)
        out.append({"line": " · ".join(base + [parts[k] for k in p["card"] if parts[k]]),
                    "full": " · ".join(base + [v for v in parts.values() if v]), "parts": parts,
                    **{k: p[k] for k in ("name", "member", "group", *FIELDS)}, "phones": [dict(x) for x in p["phones"]]})
    return out


def card_parts(p):
    """{part: its words} of each of CARD_PARTS for a plan ("" for one it hasn't)."""
    kind = ", ".join(x for x in (KINDS.get(p["kind"], ""), ORDERS.get(p["order"], "").lower()) if x)
    rx = " · ".join(f"{k} {p[f]}" for k, f in (("RxBIN", "rxbin"), ("RxPCN", "rxpcn"), ("RxGroup", "rxgroup")) if p[f])
    holder = f"Policyholder {p['holder']}" + (f" ({RELATIONS[p['relation']]})" if p["relation"] else "") if p["holder"] else ""
    return {"kind": kind[:1].upper() + kind[1:], "holder": holder, "rx": rx, "payer": f"Payer ID {p['payer']}" if p["payer"] else "",
            "phones": " · ".join((f"{x['label']} " if x["label"] else "") + x["number"] for x in p["phones"])}


def shared(p):
    """A plan as a share link with Insurance cards ticked shows it (share_links.py): what its card says and whether it
    has each side's photo; never what it's linked to on Costs, nor what the emergency card says."""
    return {"id": p["id"], "name": p["name"], "member": p["member"], "group": p["group"],
            "phones": [{"label": x["label"], "number": x["number"]} for x in p["phones"]], **{k: p[k] for k in FIELDS},
            "front": bool(p["front"]), "back": bool(p["back"])}


def add(db, pid, who, plan):
    """A new plan (check()'d; call inside a transaction). who: the session's row. -> its id"""
    if db.execute("SELECT COUNT(*) FROM insurance_plans WHERE profile_id=?", (pid,)).fetchone()[0] >= MAX_PLANS:
        raise InsuranceError(f"A profile can have at most {MAX_PLANS} insurance plans.", 409)
    return db.execute("INSERT INTO insurance_plans(profile_id, body, created_by, created_name, created) VALUES(?, ?, ?, ?, ?)",
                      (pid, _enc(plan), who["id"], who["username"], dbm.now())).lastrowid


def change(db, pid, iid, who, plan):
    """Change a plan's fields (check()'d); its photos stay. -> whether there was one"""
    return bool(db.execute("UPDATE insurance_plans SET body=?, changed_by=?, changed_name=?, changed=? WHERE id=? AND profile_id=?",
                           (_enc(plan), who["id"], who["username"], dbm.now(), iid, pid)).rowcount)


def remove(db, pid, iid):
    """Remove a plan and its photos (their files once that's committed). -> whether there was one"""
    r = db.execute("SELECT front, back FROM insurance_plans WHERE id=? AND profile_id=?", (iid, pid)).fetchone()
    if r is None:
        return False
    db.execute("DELETE FROM insurance_plans WHERE id=?", (iid,))
    for f in (r["front"], r["back"]):
        if f:
            dbm.after_commit(db, lambda f=f: _unlink(pid, f))
    return True


def _write(pid, clean):
    os.makedirs(folder(pid), mode=0o700, exist_ok=True)
    file = secrets.token_hex(16) + ".img"
    crypto.write_file(_path(pid, file), clean)
    return file


def set_photo(db, pid, iid, side, data, who):
    """A side's photo, checked and cleaned as a journal photo is, in place of the one there (call inside a
    transaction). -> {"type", "bytes", "removed"}, or InsuranceError / journal_photos.PhotoError"""
    if side not in SIDES:
        raise InsuranceError("A card has a front and a back.", 404)
    if len(data) > photos.max_bytes():
        raise photos.too_big()
    if not data:
        raise photos.PhotoError("That file is empty.")
    clean, kind, removed = photos.strip(data)
    r = db.execute(f"SELECT {side} FROM insurance_plans WHERE id=? AND profile_id=?", (iid, pid)).fetchone()
    if r is None:
        raise InsuranceError("That plan isn't here any more.", 404)
    file = _write(pid, clean)
    try:
        db.execute(f"UPDATE insurance_plans SET {side}=?, {side}_type=?, changed_by=?, changed_name=?, changed=? WHERE id=?",
                   (file, kind, who["id"], who["username"], dbm.now(), iid))
    except Exception:
        _unlink(pid, file)
        raise
    if r[side]:
        dbm.after_commit(db, lambda: _unlink(pid, r[side]))
    return {"type": kind, "bytes": len(clean), "removed": [photos.REMOVED[k] for k in removed]}


def drop_photo(db, pid, iid, side, who):
    """Take a side's photo off (its file once that's committed). -> whether there was one"""
    if side not in SIDES:
        return False
    r = db.execute(f"SELECT {side} FROM insurance_plans WHERE id=? AND profile_id=?", (iid, pid)).fetchone()
    if r is None or not r[side]:
        return False
    db.execute(f"UPDATE insurance_plans SET {side}=NULL, {side}_type=NULL, changed_by=?, changed_name=?, changed=? WHERE id=?",
               (who["id"], who["username"], dbm.now(), iid))
    dbm.after_commit(db, lambda: _unlink(pid, r[side]))
    return True


def photo(db, pid, iid, side):
    """(the picture's bytes, its media type) of a side's photo, or None."""
    if side not in SIDES:
        return None
    r = db.execute(f"SELECT {side} AS f, {side}_type AS t FROM insurance_plans WHERE id=? AND profile_id=?", (iid, pid)).fetchone()
    if not r or not r["f"] or not os.path.isfile(_path(pid, r["f"])):
        return None
    return crypto.read_file(_path(pid, r["f"])), photos.TYPES.get(r["t"], "application/octet-stream")


def sweep_all(db):
    """At start-up: any file in an insurance folder no plan names (a stop between writing a photo and saving its row,
    or between saving a change and removing the old file). Returns how many went."""
    base, n = os.path.join(config.DATA_DIR, "profiles"), 0
    named = {(r["profile_id"], f) for r in db.execute("SELECT profile_id, front, back FROM insurance_plans")
             for f in (r["front"], r["back"]) if f}
    for p in os.listdir(base) if os.path.isdir(base) else ():
        d = folder(p) if p.isdigit() else None
        for f in os.listdir(d) if d and os.path.isdir(d) else ():
            if (int(p), f) not in named and os.path.isfile(os.path.join(d, f)):
                _unlink(p, f)
                n += 1
    return n


def stored(db, pid):
    """[(the name in an export, its sealed file)] of every photo, for Export everything."""
    out = []
    for r in _rows(db, pid):
        for s in SIDES:
            if r[s] and os.path.isfile(_path(pid, r[s])):
                out.append((export_name(r["id"], s, r[s + "_type"]), _path(pid, r[s])))
    return out


def export_name(iid, side, kind):
    return f"insurance/{int(iid)}-{side}.{'jpg' if kind == 'jpeg' else kind}"


EXPORT_NAME = re.compile(r"^insurance/\d+-(?:front|back)\.(?:jpg|png|webp)$")


def for_export(db, pid):
    """data.json's "insurance": each plan as listed, each side the name of its photo in the zip (or None)."""
    out = []
    for r in _rows(db, pid):
        p = _out(r)
        for s in SIDES:
            p[s] = export_name(r["id"], s, r[s + "_type"]) if r[s] and os.path.isfile(_path(pid, r[s])) else None
        p.pop("id")
        out.append(p)
    return out


def restore(db, pid, plans, photo, written, who_of):
    """Put back an export's plans (export.restore, inside its transaction): each check()'d already ((x, body) pairs);
    one with the same name and member ID as a plan the profile has is left as it is. photo(name): a photo's bytes in
    the zip, or None. written: "insurance/<file>" of each file written, removed again if the transaction fails
    (export.photo_files). who_of(x): (by, at, changed, changed_by) as kept. -> {"insurance", "here", "photos",
    "photos_missing"}"""
    n = {"insurance": 0, "here": 0, "photos": 0, "photos_missing": 0}
    have = {(p["name"], p["member"]) for p in listing(db, pid)}
    for x, body in plans:
        if (body["name"], body["member"]) in have:
            n["here"] += 1
            continue
        if len(have) >= MAX_PLANS:
            raise ValueError(f"The export's insurance plans would make more than {MAX_PLANS} for this profile.")
        have.add((body["name"], body["member"]))
        by, at, changed, changed_by = who_of(x)
        cols = {}
        for s in SIDES:
            name = x.get(s) if isinstance(x.get(s), str) else None
            if not name:
                continue
            raw = photo(name)
            if raw is None:
                n["photos_missing"] += 1
                continue
            if len(raw) > photos.max_bytes():
                raise ValueError(f"A photo of an insurance card in the export ({name}) is larger than this server keeps.")
            try:
                clean, kind, _ = photos.strip(raw)
            except photos.PhotoError as e:
                raise ValueError(f"A photo of an insurance card in the export ({name}) can't be kept: {e}") from None
            file = _write(pid, clean)
            written.append("insurance/" + file)
            cols[s], cols[s + "_type"] = file, kind
            n["photos"] += 1
        db.execute(f"""INSERT INTO insurance_plans(profile_id, body, created_by, created_name, created, changed_name, changed
                       {''.join(', ' + c for c in cols)}) VALUES(?, ?, NULL, ?, ?, ?, ?{', ?' * len(cols)})""",
                   (pid, _enc(body), by, at or dbm.now(), changed_by, changed, *cols.values()))
        n["insurance"] += 1
    return n


# ---------------------------------------------------------------- suggestions (#1168): never saved by themselves

# a label on a card, then what it labels: the more particular first (RxGroup before Group, Payer ID before ID)
_LABEL = re.compile(r"\b(?:(?P<rxbin>rx\s*bin|bin)|(?P<rxpcn>rx\s*pcn|pcn)|(?P<rxgroup>rx\s*gr(?:ou)?p)|(?P<payer>payer\s*(?:id|#|no\b\.?))"
                    r"|(?P<group>gr(?:ou)?p(?:\s*(?:id|#|no\b\.?|number))?)"
                    r"|(?P<member>(?:member|subscriber|enrollee|identification)(?:\s*(?:id|#|no\b\.?|number))?|id(?:\s*(?:#|no\b\.?|number))?))"
                    r"\s*[:#.]?\s*(?P<value>[A-Za-z0-9][A-Za-z0-9\-]*)", re.I)
_ID_FIELDS = ("rxbin", "rxpcn", "rxgroup", "payer", "group", "member")
_PHONE = re.compile(r"(?<![\w-])(?:\+?1[\s.\-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.\-]?)\d{3}[\s.\-]?\d{4}(?:\s*(?:ext\.?|x)\s*\d{1,5})?(?![\w-])", re.I)
_SITE = re.compile(r"\b(?:https?://)?www\.[a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.[a-z]{2,}(?:/\S*)?", re.I)


def from_text(text):
    """What a card's text (text recognition's, of its front and back) seems to say: {field: value} of those found
    (IDs, "name", "website"; "phones" [{label, number}]). A guess for the person to check: the page says each as a
    suggestion, and nothing is saved by it."""
    out, phones = {}, []
    lines = [x for x in (" ".join(x.split()) for x in str(text or "").splitlines()) if x][:200]
    for line in lines:
        for m in _LABEL.finditer(line):
            field, value = next(k for k in _ID_FIELDS if m.group(k)), m.group("value")
            if field in out or not 3 <= len(value) <= ID_MAX:
                continue
            if field != "rxpcn" and not re.search(r"\d", value):   # an ID has digits ("Member: Sam Example" isn't one)
                continue
            out[field] = value.upper()
        for m in _PHONE.finditer(line):
            number = m.group(0).strip()
            label = re.split(r"\s{2,}|[|·]", line[:m.start()])[-1].strip(" :-–")
            label = re.sub(r"\s*\b(?:call|phone|tel\.?)$", "", label, flags=re.I).strip(" :-–")
            if not re.search(r"[^\W\d_]{3,}", label):
                label = ""
            if len(phones) < MAX_PHONES and number not in {p["number"] for p in phones}:
                phones.append({"label": label[:LABEL_MAX], "number": number[:NUMBER_MAX]})
        if "website" not in out and (m := _SITE.search(line)):
            try:
                out["website"] = _website(m.group(0).rstrip(".,;"))
            except InsuranceError:
                pass
    for line in lines[:4]:   # the plan's name: the first line of words near the top of the front that labels nothing
        if len(re.findall(r"[^\W\d_]{2,}", line)) >= 2 and not _LABEL.search(line) and not _PHONE.search(line) \
                and not _SITE.search(line) and sum(c.isdigit() for c in line) <= 2 and len(line) <= NAME_MAX:
            out["name"] = line.strip(" :-–·|")
            break
    if phones:
        out["phones"] = phones
    return out


# a Coverage's relationship (its subscriber-relationship code) as the plan's
_RELATION = {"self": "self", "spouse": "spouse", "common": "spouse", "child": "child", "parent": "other", "other": "other",
             "injured": "other"}


def _plain(v, n):
    return " ".join(str(v or "").split())[:n]


def from_records(data, plans, years):
    """Plans the imported records suggest, and what a plan can be linked to: {"plans": [{"name", "member", "group"?,
    the other fields the Coverage gives, "payers": [its insurer], "claims": how many, "last": the latest claim's
    day}], "payers": [{"name", "claims"}] (the claims' insurers, as their Costs rows say), "years": [{"start", "end"}]
    (the plan years typed on Costs)}. No suggestion is made with the member ID of a plan kept here, nor without a
    member ID for an insurer a plan here is named for or linked to. plans: listing()'s; years: costplan.plans()'s."""
    details = (data or {}).get("details") or {}
    got, payers = {}, {}
    for c in (data or {}).get("costs") or []:
        if not isinstance(c, dict):
            continue
        ins = _plain(c.get("ins"), NAME_MAX)
        ins = "" if ins == "Insurance" else ins   # (claims_csv's word for an insurer not named)
        if ins:
            payers[ins] = payers.get(ins, 0) + 1
        d = details.get(str(c.get("id"))) or {}
        cov = d.get("coverage") if isinstance(d, dict) and isinstance(d.get("coverage"), dict) else {}
        name = _plain(cov.get("plan") or cov.get("payor") or ins, NAME_MAX)
        if not name:
            continue
        member = _plain(cov.get("member"), ID_MAX)
        s = got.setdefault((name.lower(), member.lower()), {"name": name, "member": member, "claims": 0, "last": "", "kinds": set(),
                                                            "payers": [ins] if ins else []})
        s["claims"] += 1
        s["last"] = max(s["last"], str(c.get("d") or "")[:10])
        s["kinds"].add(c.get("kind"))
        for k in ("group", "rxbin", "rxpcn", "rxgroup"):
            if (v := _plain(cov.get(k), ID_MAX)):
                s.setdefault(k, v)
        for k in ("start", "end"):
            if _DAY.match(v := _plain(cov.get(k), 10)):
                s.setdefault(k, v)
        if cov.get("relationship") in _RELATION:
            s.setdefault("relation", _RELATION[cov["relationship"]])
    members = {p["member"].lower() for p in plans if p["member"]}
    named = {p["name"].lower() for p in plans} | {x.lower() for p in plans for x in p.get("payers") or []}
    out = []
    for s in got.values():
        if s["member"].lower() in members if s["member"] else ({s["name"].lower()} | {x.lower() for x in s["payers"]}) & named:
            continue
        kinds = s.pop("kinds")
        if kinds == {"pharmacy"}:
            s["kind"] = "pharmacy"
        elif "medical" in kinds:
            s["kind"] = "medical"
        out.append(s)
    out.sort(key=lambda s: (bool(s["member"]), s["last"]), reverse=True)
    return {"plans": out[:MAX_PLANS],
            "payers": [{"name": k, "claims": n} for k, n in sorted(payers.items(), key=lambda x: (-x[1], x[0].lower()))][:MAX_LINKS],
            "years": [{"start": y["start"], "end": y["end"]} for y in years][-MAX_LINKS:]}
