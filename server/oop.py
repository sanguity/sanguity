"""Out-of-pocket costs (#55): where each amount came from, HSA/FSA tags, payments matched to claims, and nothing
counted twice. records._assemble calls annotate() last, and the Costs tab reads what it adds as data["oop"].

A profile's out-of-pocket amounts arrive three ways:

  typed     payments typed in on the Import page (manual_items.py, kind "payment"), maybe with a receipt (a
            "document" event of the same import) and an HSA/FSA tag
  records   payments imported with other records (a Sanguity data.json loaded back in, build.py's payments.json):
            any outOfPocket.payments entry an import other than hand entry brought
  claim     a claim's patient responsibility (its Costs row's "owe"), as the insurer reported it

Each is said one way wherever it's shown (SRC_LABELS: the Costs tab says the same, costs.js OOP_SRC, and the tax-year
report's CSV). Sanguity connects to no bank, card or HSA account: payments are what was typed in or uploaded (#1163).

The same money can arrive two of those ways, so annotate() works out, for every payment and every claim share:

  dup       a payment that is another one, arrived from another import: the same amount, within DUP_DAYS days, to
            a payee with a word in common (or no payee). The one from the earlier import is counted; this one is
            listed beside it but not counted. A mark {"same": false} says they are two payments.
  claim     the claim a payment paid. Typed or marked links first (a mark {"claim": <claim's key>}); then, for
            payments with no link and no mark {"claim": ""} ("no claim"), the claim whose share is the same
            amount, dated from MATCH_BEFORE days before to MATCH_AFTER days after it, with a payee sharing a word
            with the provider: the nearest in time, one payment per claim. Premiums and refunds are never matched.
  counted   a claim share is counted only while no payment is matched to it: once one is, the payments are what
            was paid (a bill can be reduced, or paid in parts), so the share isn't added to them.
  hsa       "HSA", "FSA" or None: a mark, else what was typed, else read from the account the export names
            ("HSA", "Health savings", "FSA", "Flexible spending").

Marks are what a person said about one payment or claim share on the Costs tab, kept in the cost_marks table
(schema version 27): {"hsa": "HSA" | "FSA" | "", "claim": <claim key> | "", "same": false}, each field present only
when said ("" is "none"; a field left out is worked out). Like edits (edits.py), a mark belongs to the profile, not
to an import, and finds its payment or claim again by key: a payment's own key ("manual:h:...") or, for an export's
payments, which have none, "pay:h:" and a hash of its date, payee, amount, account and memo; a claim's event key
(edits.key_of). A mark whose payment or claim is gone stays until its record comes back.

data["oop"] (only when the profile has payments, marks, claims or plan years, so records with none are served as
before):
  payments  [{i (place in outOfPocket.payments), key, src, imp, hsa, hsaFrom, claim (event id), match, dup (i of
             the payment counted instead), receipt (event id), marked}]
  claims    [{id (event id), key, owe, by ([i] matched), paid, counted, hsa, marked}]   claim shares over zero
  plans     the plan years typed in (costplan.plans, #1127)
  bills     the bills typed in, each lined up against a claim (costplan.lined_up, #1128)
  years     the deductible and out-of-pocket sums per plan year, or calendar year outside them (costplan.years)
Never stored from an import (records.SERVED): an export's data.json has it, worked out again when loaded.
"""
import hashlib, json, re
from datetime import date

from . import costplan, crypto, db as dbm, edits, security as sec

DUP_DAYS = 3
MATCH_BEFORE, MATCH_AFTER = 3, 180
HSA_TAGS = ("HSA", "FSA")
SRC_LABELS = {"typed": "Typed in by you", "records": "Imported with your records"}
_WORDS_IGNORED = {"the", "and", "inc", "llc", "pllc", "ltd", "corp", "dba", "of", "for", "fictional", "payment", "pmt",
                  "bill", "medical", "health", "center", "services", "group", "associates", "hospital", "clinic", "dr"}


# ---------------------------------------------------------------- keys

def _h(*fields):
    return hashlib.sha256("\x1f".join(" ".join(str(f if f is not None else "").split()).lower() for f in fields)
                          .encode("utf-8")).hexdigest()[:16]


def payment_key(p):
    k = p.get("key")
    if isinstance(k, str) and k.strip():
        return k
    amount = p.get("amount")
    return "pay:h:" + _h(p.get("d"), p.get("payee"), f"{amount:.2f}" if isinstance(amount, (int, float)) else amount,
                         p.get("account"), p.get("memo"))


def target_hash(key):
    return sec.pepper_mac(b"sanguity cost mark\x00" + key.encode("utf-8")).hex()[:32]


# ---------------------------------------------------------------- marks

class MarkError(ValueError):
    """A mark that can't be made, said in plain language."""


def marks(db, pid):
    """{key: value} of the profile's marks."""
    return {crypto.dec(r["target_key"]): json.loads(crypto.dec(r["value"]))
            for r in db.execute("SELECT target_key, value FROM cost_marks WHERE profile_id=?", (pid,))}


def set_mark(db, pid, uid, key, **said):
    """Change the mark on key: each field given is set ("" for none), or with None taken away (worked out again).
    A mark left with nothing is removed. Call inside a transaction. Returns the mark as it is now ({} when none)."""
    th = target_hash(key)
    r = db.execute("SELECT id, value FROM cost_marks WHERE profile_id=? AND target=?", (pid, th)).fetchone()
    value = json.loads(crypto.dec(r["value"])) if r else {}
    for f, v in said.items():
        if v is None:
            value.pop(f, None)
        else:
            value[f] = v
    if r:
        db.execute("DELETE FROM cost_marks WHERE id=?", (r["id"],))
    if value:
        db.execute("INSERT INTO cost_marks(profile_id, target, target_key, value, created_by, at) VALUES(?, ?, ?, ?, ?, ?)",
                   (pid, th, crypto.enc(key), crypto.enc(json.dumps(value, separators=(",", ":"))), uid, dbm.now()))
    from . import records
    records._bump(db, pid)
    return value


# ---------------------------------------------------------------- working it out

def _day(d):
    try:
        return date.fromisoformat(str(d)[:10]).toordinal()
    except (TypeError, ValueError):
        return None


def words(name):
    """The words of a payee or provider that tell it apart ("Lakeside Clinic (fictional)" -> {"lakeside"})."""
    return {w for w in re.findall(r"[a-z0-9]+", str(name or "").lower()) if len(w) >= 3 and w not in _WORDS_IGNORED}


def _alike(a, b):
    wa, wb = words(a), words(b)
    return not wa or not wb or bool(wa & wb)


def _account_tag(account):
    a = str(account or "")
    if re.search(r"\bHSA\b|health\s+savings", a, re.I):
        return "HSA"
    if re.search(r"\bFSA\b|flexible\s+spending", a, re.I):
        return "FSA"
    return None


def _money(x):
    return round(float(x), 2) if isinstance(x, (int, float)) and not isinstance(x, bool) else 0.0


def annotate(db, pid, data, imps=()):
    """Add data["oop"] (see the module's docstring) to the assembled records; imps[i] is the import payment i came
    from. Returns data."""
    data.pop("oop", None)   # never one an import brought along
    pays = ((data.get("outOfPocket") or {}).get("payments")) if isinstance(data.get("outOfPocket"), dict) else None
    pays = pays if isinstance(pays, list) else []
    said = marks(db, pid)
    typed, kept = costplan.plans(db, pid), costplan.bills(db, pid)
    if not pays and not said and not typed and not kept and not any(isinstance(c, dict) for c in data.get("costs") or []):
        return data
    imports = data.get("imports") or {}
    events = {e.get("id"): e for e in data.get("events") or [] if isinstance(e, dict)}
    by_key = {}
    for e in events.values():
        by_key.setdefault(edits.key_of(e), e)
    out_p = []
    for i, p in enumerate(pays):
        if not isinstance(p, dict):
            continue
        key = payment_key(p)
        imp = imps[i] if i < len(imps) else None
        fmt = (imports.get(str(imp)) or {}).get("format")
        m = said.get(key) or {}
        if m.get("hsa") is not None:
            hsa, src_tag = (m["hsa"] or None), "marked"
        elif p.get("hsa") in HSA_TAGS:
            hsa, src_tag = p["hsa"], "typed"
        else:
            hsa = _account_tag(p.get("account"))
            src_tag = "account" if hsa else None
        receipt = by_key.get(p.get("receipt")) if p.get("receipt") else None
        out_p.append({"i": i, "key": key, "src": "typed" if fmt == "manual" else "records", "imp": imp,
                      "hsa": hsa, "hsaFrom": src_tag, "claim": None, "match": None, "dup": None,
                      "receipt": receipt["id"] if receipt else None, "marked": bool(m),
                      "_p": p, "_m": m, "_day": _day(p.get("d")), "_amt": _money(p.get("amount"))})

    # the same payment from two imports: the earlier import's is counted
    for a_n, a in enumerate(out_p):
        if a["dup"] is not None or a["_m"].get("same") is False or a["_day"] is None:
            continue
        for b in out_p[a_n + 1:]:
            if b["dup"] is not None or b["_m"].get("same") is False or b["_day"] is None or b["imp"] == a["imp"]:
                continue
            if abs(a["_amt"] - b["_amt"]) < 0.005 and abs(a["_day"] - b["_day"]) <= DUP_DAYS and _alike(a["_p"].get("payee"), b["_p"].get("payee")):
                first, second = (a, b) if (a["imp"] or 0) <= (b["imp"] or 0) else (b, a)
                second["dup"] = first["i"]
                if first["hsa"] is None and first["hsaFrom"] != "marked" and second["hsa"]:   # what the other one says
                    first["hsa"], first["hsaFrom"] = second["hsa"], second["hsaFrom"]          # fills in what this one lacks
                if first["receipt"] is None:
                    first["receipt"] = second["receipt"]
                if first is b:
                    break
    counted = [p for p in out_p if p["dup"] is None]

    # claim shares
    claims, ckeys = [], {}
    for c in data.get("costs") or []:
        if not isinstance(c, dict) or c.get("id") not in events or _money(c.get("owe")) < 0.01:
            continue
        key = edits.key_of(events[c["id"]])
        m = said.get(key) or {}
        x = {"id": c["id"], "key": key, "owe": _money(c.get("owe")), "by": [], "paid": 0.0, "counted": 0.0,
             "hsa": (m.get("hsa") or None), "marked": bool(m), "_d": _day(c.get("d")), "_prov": c.get("prov") or c.get("title")}
        claims.append(x)
        ckeys.setdefault(key, x)

    def link(p, cl, how):
        p["claim"], p["match"] = cl["id"], how
        cl["by"].append(p["i"])
        cl["paid"] = round(cl["paid"] + p["_amt"], 2)

    # linked by a person first, then worked out
    for p in counted:
        k = p["_m"].get("claim") or p["_p"].get("claim")
        if k and k in ckeys:
            link(p, ckeys[k], "marked" if p["_m"].get("claim") else "typed")
    pairs = []
    for p in counted:
        if p["claim"] is not None or p["_m"].get("claim") == "" or p["_day"] is None or p["_amt"] <= 0:
            continue
        if re.search(r"premium", str(p["_p"].get("group") or ""), re.I):
            continue
        for cl in claims:
            if cl["by"] or cl["_d"] is None or abs(cl["owe"] - p["_amt"]) >= 0.005:
                continue
            gap = p["_day"] - cl["_d"]
            if -MATCH_BEFORE <= gap <= MATCH_AFTER and _alike(p["_p"].get("payee"), cl["_prov"]):
                pairs.append((abs(gap), p["i"], cl["id"], p, cl))
    pairs.sort(key=lambda t: t[:3])
    for _, _, _, p, cl in pairs:
        if p["claim"] is None and not cl["by"]:
            link(p, cl, "auto")
    for cl in claims:
        cl["counted"] = 0.0 if cl["by"] else cl["owe"]

    data["oop"] = {"payments": [{k: v for k, v in p.items() if not k.startswith("_")} for p in out_p],
                   "claims": [{k: v for k, v in c.items() if not k.startswith("_")} for c in claims],
                   "plans": typed, "years": costplan.years(data, typed),
                   "bills": costplan.lined_up(data, kept, {c["id"]: (c["paid"], c["by"]) for c in claims}, edits.key_of)}
    return data
