"""Pieces shared by the insurer claims importers (claims_uhc, claims_bcbs, claims_csv).

The amounts, the care categories and the drug names are build.py's claims code, moved here unchanged so that an
import gives the same Costs rows as a build of the same export. build.py keeps its own copy until it switches to
the importers (a follow-up of issue #30); `tests/test_importer_claims.py` compares the two.

The leading underscore keeps the registry from taking this module for an importer.

Linking claims to visits: build.py puts a facility bill (revenue-code lines) under "Hospital stay (facility)" when
its date falls inside a hospital stay among the events it has built so far. An import here does the same against
the stays in its own records, which a claims-only upload doesn't have, so such a bill comes out as "Hospital
outpatient (facility)". For a later step that sees the whole profile, every claim's detail carries a `visit` hint:
`{"date": "YYYY-MM-DD", "end": "YYYY-MM-DD" (when the claim spans days), "provider": "...", "facility": true (for a
facility bill)}`. Matching a visit or stay on that date (and provider, where the visit names one) is how the claim
links to it, and a facility bill inside a stay is then re-filed under "Hospital stay (facility)".
"""
import collections
import hashlib
import re

from .common import iso


def money(x):
    """A rounded amount from 12.5, "12.50" or {"value": 12.5}; 0.0 when there's none."""
    try:
        return round(float((x or {}).get("value") if isinstance(x, dict) else x or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def code_category(code, desc):
    """Category for one billed line, from its CPT/HCPCS/revenue code (first 5 chars) and description."""
    c, t = (code or "").upper()[:5], (desc or "").lower()
    if "stem cell" in t or c.startswith("0081"):
        return "Stem cell transplant"
    if c == "00891" or "cell therapy" in t or c.startswith(("Q2041", "Q2042", "Q2053", "Q2054", "Q2055", "Q2056")):
        return "CAR-T cell therapy"
    if c.startswith(("963", "964", "965", "J")) or re.search(r"chemo|infusion|injection|transfus|immunotherap|pharmacy charges", t) or c in ("00636", "00250", "00260", "00335"):
        return "Chemo, infusions & drugs"
    if c.startswith("77") or "radiation" in t:
        return "Radiation therapy"
    if c == "OPS" or "outpatient services" in t:
        return "Hospital outpatient (facility)"
    if re.match(r"9922\d|9923\d|9921[89]|9947\d|9929\d", c) or re.search(r"room and board|inpatient|hospital care|intensive care|emergency|observation", t):
        return "Hospital physician care"
    if re.match(r"9920\d|9921[1-5]|9924\d|G2211|9944\d|9942\d", c) or re.search(r"office (o/p|visit)|consult|telehealth|telephone", t):
        return "Doctor visits"
    if c.startswith("97") or re.search(r"therapeutic exercise|physical therap|exercise therapy|gait|neuromuscular", t):
        return "Physical therapy"
    if c.startswith(("8", "36415")) or re.search(r"laborator|patholog|blood (count|sample)|metabolic|panel|culture|assay|cytometry|tissue exam", t):
        return "Lab & pathology"
    if re.match(r"7[0-6]\d\d\d|93\d\d\d|78\d\d\d", c) or re.search(r"radiolog|imaging|x-ray|ct|mri|ultrasound|echocardiog|pet|electrocardiog", t):
        return "Imaging & tests"
    if c.startswith(("S912", "S913", "G029", "G030", "T10")) or "nurse" in t and "home" in t or re.search(r"services of a (registered|licensed) (nurse|practical)", t):
        return "Home health"
    if c.startswith(("E", "K", "A4", "A6", "A7")) or re.search(r"pump|equipment|supplies|wheelchair|walker", t):
        return "Equipment & supplies"
    if re.match(r"[1-6]\d\d\d\d", c) or re.search(r"procedure|biopsy|puncture|catheter|aspiration", t):
        return "Procedures"
    return ""


def is_facility(lines):
    """True for a facility bill: at least half its lines carry revenue codes (00xxx)."""
    codes = [(l.get("code") or "").upper() for l in lines or []]
    return bool(codes) and sum(bool(re.fullmatch(r"00\d{3}", c)) for c in codes) >= max(1, len(codes) // 2)


def care_category(kind, title, lines, prov, date=None, events=()):
    """Category for a claim. Facility bills (revenue-code lines) are grouped as hospital facility charges,
    except CAR-T and stem cell transplant; otherwise the category of the most expensive line wins.
    `events` are the records the claim is imported with: a facility bill inside one of their stays is a stay's."""
    if kind == "pharmacy":
        return "Pharmacy"
    codes = [(l.get("code") or "").upper() for l in lines or []]
    if is_facility(lines):
        if any(c == "00891" for c in codes):
            return "CAR-T cell therapy"
        if any(c.startswith(("0081", "00362")) for c in codes):
            return "Stem cell transplant"
        in_stay = date and any(e["k"] == "stay" and e["t"][:10] <= date[:10] <= e.get("end", e["t"])[:10] for e in events)
        return "Hospital stay (facility)" if in_stay else "Hospital outpatient (facility)"
    best, amt = "", -1
    for l in lines or []:
        cat = code_category(l.get("code"), l.get("desc"))
        if cat and (l.get("billed") or 0) > amt:
            best, amt = cat, l.get("billed") or 0
    return best or code_category("", f"{title} {prov}") or "Other medical"


def nice_drug(n):
    """ "OXYCODONE HCL TAB 5MG" -> "Oxycodone Hcl 5mg tablet"."""
    n = " ".join((n or "").split())
    m = re.match(r"^([A-Z0-9/\-]+(?: [A-Z][A-Z/\-]+)?)\s+(TAB|CAP|SOL|INJ|SUSP|CRE|OIN|PAK|KIT|POW|SYR|PEN)\b\s*(.*)$", n)
    if m:
        name, form, dose = m.groups()
        return f"{name.title()} {dose.lower()} {dict(TAB='tablet', CAP='capsule', SOL='solution', INJ='injection', SUSP='suspension').get(form, form.lower())}".replace("  ", " ").strip()
    return n.title()


def hkey(name, *fields):
    """A source key for a record without an id of its own (docs/importers.md, Source keys)."""
    return name + ":h:" + hashlib.sha256("\x1f".join(str(f).strip().lower() for f in fields).encode()).hexdigest()[:16]


def claim_keys(name, pairs):
    """A function (claim number, date) -> source key, from every (claim number, service date) in the file, so that
    keys don't depend on the order claims come in: "<name>:<number>", with "|<date>" added when the number appears on
    more than one date, and None (the caller falls back to the record's own id) when it appears twice on one date."""
    seen = collections.Counter((cid, date) for cid, date in pairs if cid and date)
    dates = collections.Counter(cid for cid, _ in seen)

    def key(cid, date):
        if not cid or seen[(cid, date)] > 1:
            return None
        return f"{name}:{cid}|{date}" if dates[cid] > 1 else f"{name}:{cid}"
    return key


class Claims:
    """Collects claim and pharmacy events, their details and Costs rows the way build.py's add() and cost_row() do."""

    def __init__(self, name):
        self.name = name
        self.events, self.details, self.costs, self.warnings = [], {}, [], []
        self._keys = set()
        self.repeated = 0

    def key(self, *candidates):
        """The first candidate key not used yet; None (and the claim is left out) when every one is taken."""
        for k in candidates:
            if k and k not in self._keys:
                self._keys.add(k)
                return k
        self.repeated += 1
        return None

    def add(self, t, kind, title, sub, detail, key, end=None, **extra):
        i = len(self.events) + 1
        e = {"id": i, "t": t, "k": kind, "title": title, "sub": sub}
        if end:
            e["end"] = end
        e.update({k: v for k, v in extra.items() if v not in (None, "", [])})
        e["key"] = key
        hint = {"date": t[:10]}
        if detail.get("end") and str(detail["end"])[:10] != t[:10]:
            hint["end"] = str(detail["end"])[:10]
        if detail.get("provider"):
            hint["provider"] = detail["provider"]
        if kind == "claim" and is_facility(detail.get("lines")):
            hint["facility"] = True
        detail["visit"] = hint
        self.details[i] = detail
        self.events.append(e)
        return i

    def cost(self, eid, date, insurer, billed, paid, owe, status, kind, cat, prov, title):
        self.costs.append({"id": eid, "d": date[:10], "ins": insurer, "billed": billed, "paid": paid, "owe": owe,
                           "status": status, "kind": kind, "cat": cat, "prov": (prov or "").strip().title(), "title": title})

    def category(self, title, lines, prov, date):
        return care_category("medical", title, lines, prov, date, self.events)

    def result(self, skipped_no_date=0):
        if skipped_no_date:
            self.warnings.append(f"{skipped_no_date} claim{'s' if skipped_no_date != 1 else ''} had no service date and "
                                 f"{'were' if skipped_no_date != 1 else 'was'} left out.")
        if self.repeated:
            self.warnings.append(f"{self.repeated} claim{'s' if self.repeated != 1 else ''} appeared twice "
                                 f"(same claim number and date) and {'were' if self.repeated != 1 else 'was'} left out.")
        order = sorted(range(len(self.events)), key=lambda n: self.events[n]["t"])   # build.py sorts by time, stably
        return {"data": {"events": [self.events[n] for n in order], "details": self.details, "labs": [],
                         "costs": self.costs},
                "warnings": self.warnings}


def load_json(files, rel):
    import json
    try:
        with open(files[rel], encoding="utf-8-sig") as f:
            return json.load(f)
    except (UnicodeDecodeError, ValueError) as e:
        raise ValueError(f"{rel} isn't readable JSON ({e.__class__.__name__}).") from None


def json_candidates(files, preferred="claims.json"):
    """JSON files at the top of the upload or one folder down, claims.json first."""
    hits = [p for p in files if p.lower().endswith(".json") and p.count("/") <= 1]
    return sorted(hits, key=lambda p: (p.rsplit("/", 1)[-1].lower() != preferred, p.count("/"), p))


def head(path, n=64 * 1024):
    with open(path, "rb") as f:
        return f.read(n).decode("utf-8", "replace")


__all__ = ["money", "code_category", "care_category", "nice_drug", "hkey", "claim_keys", "Claims", "iso", "is_facility"]
