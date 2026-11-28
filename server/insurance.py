"""Insurance cards (#1137): each health plan of a profile, with what its card says and a photo of each side.

A plan is a row of insurance_plans (schema version 55, db.py): its body, encrypted at rest (atrest.FIELDS), is
{"name", "member", "group", "phones": [{"label", "number"}]}: the plan's name, the member ID, the group number and
the phone numbers on the card. A member ID is as good as a password at a front desk, so it's never logged: the
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
(#1126)."""
import json, os, re, secrets

from . import config, crypto, db as dbm, journal_photos as photos

SIDES = ("front", "back")
MAX_PLANS, MAX_PHONES = 10, 4
NAME_MAX, ID_MAX, LABEL_MAX, NUMBER_MAX = 120, 60, 40, 40
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
    return {"name": _text(body.get("name"), NAME_MAX, "plan's name", need=True),
            "member": _text(body.get("member"), ID_MAX, "member ID"),
            "group": _text(body.get("group"), ID_MAX, "group number"), "phones": out}


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
    return {"id": r["id"], **_dec(r["body"]),
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
    """For the printable emergency card (#1126): [{"line", "name", "member", "group"}] of each plan, in order; line
    the plan in one plain line ("Fictional Plan · Member ID M1 · Group G1", the parts it has); no photos, no phone
    numbers."""
    out = []
    for p in listing(db, pid):
        line = " · ".join([p["name"], *([f"Member ID {p['member']}"] if p["member"] else []), *([f"Group {p['group']}"] if p["group"] else [])])
        out.append({"line": line, **{k: p[k] for k in ("name", "member", "group")}})
    return out


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
