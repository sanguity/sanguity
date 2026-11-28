"""Plan years and bills on the Costs tab: how much of the deductible and the out-of-pocket maximum the claims have
met (#1127), and a provider's bill lined up against what the plan says you owe (#1128, "bills" below).

A plan year is typed in by the person (Costs → Plan years; routes_costs.py): the day it starts, the plan's deductible
and out-of-pocket maximum for one person, and the family amounts when they want them. It runs a year from its start
(2026-07-01 to 2027-06-30); two plan years may not overlap. Kept in the cost_plans table (schema version 51), one row
each, what it says encrypted, as the profile's own medications are (med_entries): never part of an import, so
undoing one or a later pull leaves it.

The amounts come from the claims already imported, their Costs rows (records' "costs"), as Costs shows them:

  out of pocket      a claim's share ("owe"), every claim's, a negative one (an adjustment) as it is
  toward deductible  the deductible part of that share ("ded"), where the claim gives one (FHIR EOBs' "deductible"
                     adjudication, _fhir_claims.py); a claim with none still counts toward out of pocket

Arithmetic only: the sums and the limits typed, nothing said about them. Claims outside every plan year typed (all of
them, when none is) are summed by calendar year instead, with no limits.

years(data, plans) -> [{start, end, plan (its id, or None for a calendar year), deductible, oop, familyDeductible,
familyOop (the limits typed, None when not), towardDeductible, outOfPocket, claims ([event ids] that count),
noSplit (how many of those give no deductible part)}], newest first; oop.annotate serves it as data["oop"]["years"]
and the plan years themselves as data["oop"]["plans"].
"""
import json, math
from datetime import date, timedelta

from . import crypto, db as dbm

PLAN_FIELDS = ("start", "deductible", "oop", "familyDeductible", "familyOop")
MAX_AMOUNT = 10_000_000
MAX_PLANS = 100


class PlanError(ValueError):
    """A plan year that can't be kept, said in plain language."""


# ---------------------------------------------------------------- the table (one row an entry, its body encrypted)

def _enc(obj):
    return crypto.enc(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))


def _rows(db, table, pid):
    return [(r, json.loads(crypto.dec(r["body"])))
            for r in db.execute(f"SELECT id, body, created_name, created, changed_name, changed FROM {table} "
                                "WHERE profile_id=? ORDER BY id", (pid,)).fetchall()]


def _listed(r, body):
    return {"id": r["id"], **body, "by": r["created_name"], "at": r["created"], "changed": r["changed"],
            "changed_by": r["changed_name"]}


def _save(db, table, pid, who, body, eid=None):
    from . import records
    if eid is None:
        eid = db.execute(f"INSERT INTO {table}(profile_id, body, created_by, created_name, created) VALUES(?, ?, ?, ?, ?)",
                         (pid, _enc(body), who["id"], who["username"], dbm.now())).lastrowid
    elif not db.execute(f"UPDATE {table} SET body=?, changed_by=?, changed_name=?, changed=? WHERE id=? AND profile_id=?",
                        (_enc(body), who["id"], who["username"], dbm.now(), eid, pid)).rowcount:
        return None
    records._bump(db, pid)
    return eid


def _drop(db, table, pid, eid):
    from . import records
    gone = db.execute(f"DELETE FROM {table} WHERE id=? AND profile_id=?", (eid, pid)).rowcount
    if gone:
        records._bump(db, pid)
    return bool(gone)


def _restore(db, table, pid, bodies, who_of):
    """Put back an export's entries (export.restore): one the same as one here is left. Returns (added, here)."""
    have = {json.dumps(b, sort_keys=True) for _, b in _rows(db, table, pid)}
    added = here = 0
    for x, body in bodies:
        k = json.dumps(body, sort_keys=True)
        if k in have:
            here += 1
            continue
        have.add(k)
        by, at, changed, changed_by = who_of(x)
        db.execute(f"""INSERT INTO {table}(profile_id, body, created_by, created_name, created, changed_by, changed_name,
                       changed) VALUES(?, ?, NULL, ?, ?, NULL, ?, ?)""", (pid, _enc(body), by, at or dbm.now(), changed_by, changed))
        added += 1
    return added, here


# ---------------------------------------------------------------- checking what's typed

def day(v, what):
    s = v if isinstance(v, str) else ""
    try:
        if len(s) != 10:
            raise ValueError(s)
        return date.fromisoformat(s)
    except ValueError:
        raise PlanError(f"The {what} isn't a date (YYYY-MM-DD).") from None


def amount(v, what, optional=False, negative=False):
    if v is None or v == "":
        if optional:
            return None
        raise PlanError(f"Type the {what}.")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise PlanError(f"The {what} isn't an amount.")
    if (v < 0 and not negative) or abs(v) > MAX_AMOUNT:
        raise PlanError(f"The {what} is from $0 to ${MAX_AMOUNT:,}.")
    return round(float(v), 2)


def end_of(start):
    """The last day of the plan year starting on start: the day before the same date a year on (Feb 29 -> Feb 28)."""
    try:
        nxt = start.replace(year=start.year + 1)
    except ValueError:
        nxt = date(start.year + 1, 3, 1)
    return nxt - timedelta(days=1)


def check_plan(fields):
    """A plan year as kept, from what was typed (dict of PLAN_FIELDS), or PlanError saying why not."""
    start = day(fields.get("start"), "plan year's start")
    body = {"start": start.isoformat(), "deductible": amount(fields.get("deductible"), "deductible"),
            "oop": amount(fields.get("oop"), "out-of-pocket maximum")}
    if body["oop"] < body["deductible"]:
        raise PlanError("The out-of-pocket maximum is at least the deductible.")
    fd = amount(fields.get("familyDeductible"), "family deductible", optional=True)
    fo = amount(fields.get("familyOop"), "family out-of-pocket maximum", optional=True)
    if fd is not None and fo is not None and fo < fd:
        raise PlanError("The family out-of-pocket maximum is at least the family deductible.")
    if fd is not None:
        body["familyDeductible"] = fd
    if fo is not None:
        body["familyOop"] = fo
    return body


def _overlap(body, others):
    a0 = date.fromisoformat(body["start"])
    a1 = end_of(a0)
    for o in others:
        b0 = date.fromisoformat(o["start"])
        if a0 <= end_of(b0) and b0 <= a1:
            raise PlanError(f"It overlaps the plan year from {b0.isoformat()} to {end_of(b0).isoformat()}: "
                            "plan years can't overlap.")


# ---------------------------------------------------------------- plan years

def plans(db, pid):
    """The profile's plan years, oldest first: {id, start, end, deductible, oop, familyDeductible?, familyOop?, by, at,
    changed, changed_by} (an export's "costPlans", without end)."""
    return sorted(({**_listed(r, b), "end": end_of(date.fromisoformat(b["start"])).isoformat()} for r, b in _rows(db, "cost_plans", pid)),
                  key=lambda p: p["start"])


def save_plan(db, pid, who, fields, eid=None):
    """Add a plan year (eid None) or change one. Call inside a transaction. Returns its id, or None when there's no
    such plan year; PlanError when it can't be kept."""
    body = check_plan(fields)
    have = [b for r, b in _rows(db, "cost_plans", pid) if r["id"] != eid]
    if eid is None and len(have) >= MAX_PLANS:
        raise PlanError(f"A profile keeps at most {MAX_PLANS} plan years.")
    _overlap(body, have)
    return _save(db, "cost_plans", pid, who, body, eid)


def drop_plan(db, pid, eid):
    return _drop(db, "cost_plans", pid, eid)


def _money(x):
    return round(float(x), 2) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else 0.0


def years(data, typed):
    """The plan years' sums (see the module's docstring), from the assembled records' Costs rows."""
    events = {e.get("id") for e in data.get("events") or [] if isinstance(e, dict)}
    rows = [c for c in data.get("costs") or [] if isinstance(c, dict) and c.get("id") in events
            and isinstance(c.get("d"), str) and len(c["d"]) >= 10 and (_money(c.get("owe")) or _money(c.get("ded")))]
    out = [{"start": p["start"], "end": p["end"], "plan": p["id"], "deductible": p["deductible"], "oop": p["oop"],
            "familyDeductible": p.get("familyDeductible"), "familyOop": p.get("familyOop"), "_rows": []} for p in typed]
    cal = {}
    for c in rows:
        d = c["d"][:10]
        y = next((y for y in out if y["start"] <= d <= y["end"]), None)
        if y is None:
            y = cal.get(d[:4])
            if y is None:
                y = cal[d[:4]] = {"start": d[:4] + "-01-01", "end": d[:4] + "-12-31", "plan": None, "deductible": None, "oop": None,
                                  "familyDeductible": None, "familyOop": None, "_rows": []}
        y["_rows"].append(c)
    for y in out + list(cal.values()):
        rs = y.pop("_rows")
        y.update(towardDeductible=round(sum(_money(c.get("ded")) for c in rs), 2), outOfPocket=round(sum(_money(c.get("owe")) for c in rs), 2),
                 claims=[c["id"] for c in rs], noSplit=sum(1 for c in rs if not isinstance(c.get("ded"), (int, float))))
    return sorted(out + list(cal.values()), key=lambda y: y["start"], reverse=True)


# ---------------------------------------------------------------- bills (#1128)
#
# A provider's bill, typed in on Costs: the date of service, the provider, the amount billed, and the due date when
# given. Its file, when there is one, is a document typed in on its own (the page saves it first, as hand entry saves
# a document: manual_items.py), which the bill names by its key; never read for numbers. Kept in cost_bills (schema
# version 52), as plan years are.
#
# A bill is lined up against a claim: the one a person chose (its key in "claim"), none ("claim": "", "no claim yet"),
# else the claim worked out the way payments are matched (oop.py): the provider sharing a word with the claim's, the
# date of service within BILL_DAYS days of the claim's, the amount billed the claim's share or its billed amount first,
# then the nearest in time; one bill a claim. A claim imported later is offered then, as it's worked out each time.
# Served as data["oop"]["bills"]: [{id, service, provider, amount, due, claim (event id), match ("marked" | "auto" |
# None), none (said to have no claim yet), owe (the claim's share), paid (payments matched to that claim, oop.py),
# payments ([i]), file (the document's event id), by, at, changed, changed_by}], newest service first.

BILL_DAYS = 3
MAX_BILLS = 2000
TEXT_MAX = 300


def check_bill(fields):
    """A bill as kept, from what was typed ({service, provider, amount, due}; claim and file kept as given), or
    PlanError saying why not."""
    body = {"service": day(fields.get("service"), "date of service").isoformat()}
    prov = " ".join(fields["provider"].split()) if isinstance(fields.get("provider"), str) else ""
    if not prov:
        raise PlanError("Type who the bill is from.")
    if len(prov) > TEXT_MAX:
        raise PlanError(f"The provider's name is at most {TEXT_MAX} characters.")
    body["provider"] = prov
    body["amount"] = amount(fields.get("amount"), "amount billed")
    if fields.get("due"):
        body["due"] = day(fields.get("due"), "due date").isoformat()
    for k in ("claim", "file"):
        v = fields.get(k)
        if v is not None:
            if not isinstance(v, str) or len(v) > 4000 or (k == "file" and not v):
                raise PlanError(f"The bill's {k} isn't one Sanguity keeps.")
            body[k] = v
    return body


def bills(db, pid):
    """The profile's bills as kept: {id, service, provider, amount, due?, claim?, file?, by, at, changed, changed_by}
    (an export's "costBills")."""
    return [_listed(r, b) for r, b in _rows(db, "cost_bills", pid)]


def bill(db, pid, eid):
    r = db.execute("SELECT body FROM cost_bills WHERE id=? AND profile_id=?", (eid, pid)).fetchone()
    return json.loads(crypto.dec(r["body"])) if r else None


def save_bill(db, pid, who, body, eid=None):
    """Add a bill (eid None) or change one; body as check_bill gives it. Call inside a transaction. Returns its id, or
    None when there's no such bill."""
    if eid is None and db.execute("SELECT COUNT(*) FROM cost_bills WHERE profile_id=?", (pid,)).fetchone()[0] >= MAX_BILLS:
        raise PlanError(f"A profile keeps at most {MAX_BILLS} bills.")
    return _save(db, "cost_bills", pid, who, body, eid)


def drop_bill(db, pid, eid):
    return _drop(db, "cost_bills", pid, eid)


def lined_up(data, kept, claim_paid, key_of):
    """The bills served (see above). kept: bills(); claim_paid: {claim event id: (paid, [payment i])} from oop.py;
    key_of(event) -> its key."""
    from . import oop
    events = {e.get("id"): e for e in data.get("events") or [] if isinstance(e, dict)}
    by_key = {}
    for e in events.values():
        by_key.setdefault(key_of(e), e)
    claims = [c for c in data.get("costs") or [] if isinstance(c, dict) and c.get("id") in events
              and isinstance(c.get("d"), str) and str(c.get("status") or "") != "Replaced"]
    ckey = {}
    for c in claims:
        ckey.setdefault(key_of(events[c["id"]]), c)
    out, taken = [], set()
    for b in kept:
        x = {k: b.get(k) for k in ("id", "service", "provider", "amount", "due", "by", "at", "changed", "changed_by")}
        doc = by_key.get(b.get("file")) if b.get("file") else None
        x.update(claim=None, match=None, none=b.get("claim") == "", owe=None, paid=0.0, payments=[],
                 file=doc["id"] if doc and doc.get("k") in ("document", "letter") else None)
        c = ckey.get(b.get("claim")) if b.get("claim") else None
        if c is not None:
            x["claim"], x["match"] = c["id"], "marked"
            taken.add(c["id"])
        out.append(x)
    pairs = []
    for x, b in zip(out, kept):
        if x["claim"] is not None or x["none"] or b.get("claim"):   # a claim chosen that's gone: not worked out instead
            continue
        d = date.fromisoformat(x["service"]).toordinal()
        for c in claims:
            try:
                gap = abs(date.fromisoformat(c["d"][:10]).toordinal() - d)
            except ValueError:
                continue
            if gap <= BILL_DAYS and oop._alike(x["provider"], c.get("prov") or c.get("title")):
                same = any(abs(_money(c.get(k)) - x["amount"]) < 0.005 for k in ("owe", "billed"))
                pairs.append((0 if same else 1, gap, x["id"], c["id"], x, c))
    pairs.sort(key=lambda t: t[:4])
    for _, _, _, _, x, c in pairs:
        if x["claim"] is None and c["id"] not in taken:
            x["claim"], x["match"] = c["id"], "auto"
            taken.add(c["id"])
    by_id = {c["id"]: c for c in claims}
    for x in out:
        if x["claim"] is not None:
            x["owe"] = _money(by_id[x["claim"]].get("owe"))
            x["paid"], x["payments"] = claim_paid.get(x["claim"], (0.0, []))
    return sorted(out, key=lambda x: (x["service"], x["id"]), reverse=True)
