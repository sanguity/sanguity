"""Generic insurance claims spreadsheet (EOB CSV): the catch-all for insurers without an importer of their own.

One row per claim, or per service line of a claim, with columns for the date of service, the provider, the amount
billed, what the plan paid and what the member owes; optionally the claim status, claim number, a description of
the service, its CPT/HCPCS/revenue code, the claim type (medical or pharmacy), the insurer and a last date of service.
Headers are matched by common spellings, and a caller can pass `mapping` to `parse` for files whose headers differ,
as with the lab spreadsheet importer (csv_labs), whose delimiter, encoding, number and date handling this reuses.

Rows that share a claim number are one claim: their amounts are added up and each row is a service line. Medical
claims become `claim` events and pharmacy rows `rx` events, each with a Costs row whose care category is found the
way build.py finds it for UHC and BCBS claims (from the codes, else the descriptions and provider).

Source keys: "claims-csv:<claim number>", or for a row without one "claims-csv:h:" + a hash of the date, provider,
description and amounts. Two identical rows without a claim number are one claim listed twice: the second is left
out with a warning. How claims link to visits: see `_claims`.
"""
import collections
import csv
import io
import re

from . import csv_labs as _csv
from ._claims import Claims, hkey, nice_drug

NAME = "claims-csv"
LABEL = "Insurance claims spreadsheet (EOB CSV)"
INSURER = "Insurance"   # when the file has no insurer column and the caller names none

FIELDS = {
    "date": ["date", "date of service", "service date", "dos", "date s of service", "service from", "service from date",
             "from date", "service start date", "start date", "date of visit", "visit date", "service begin date",
             "first date of service", "claim date", "date received", "date of care"],
    "end": ["to date", "service to", "service to date", "service end date", "end date", "last date of service",
            "through date", "thru date"],
    "provider": ["provider", "provider name", "doctor", "physician", "facility", "rendering provider", "billing provider",
                 "servicing provider", "provider facility", "paid to", "payee", "seen by", "pharmacy", "vendor",
                 "provider or facility", "doctor facility"],
    "billed": ["billed", "amount billed", "billed amount", "total billed", "billed charges", "charges", "charge",
               "total charges", "total charge", "amount charged", "charged", "provider charges", "provider billed",
               "submitted", "submitted charges", "amount submitted", "billed charge", "charge amount"],
    "paid": ["plan paid", "insurance paid", "paid by plan", "health plan paid", "plan pays", "plan paid amount",
             "paid", "paid amount", "amount paid", "insurer paid", "benefit paid", "benefits paid", "paid by insurance",
             "insurance payment", "plan payment", "plan responsibility", "your plan paid", "plan paid provider",
             "amount plan paid", "insurance paid amount", "plan discount and payment"],
    "owe": ["member owes", "you owe", "your share", "patient responsibility", "your responsibility",
            "member responsibility", "amount you owe", "patient owes", "you may owe", "what you owe", "member liability",
            "patient pays", "your cost", "out of pocket", "patient balance", "member pays", "your total cost",
            "member cost", "patient responsibility amount", "patient share", "member share", "you pay", "amount owed",
            "your portion", "patient portion", "total you owe", "you may be billed", "member responsibility amount"],
    "status": ["status", "claim status", "claim state", "processing status"],
    "claim": ["claim", "claim id", "claim number", "claim no", "claim ref", "claim reference", "claim num",
              "claim nbr", "claim identifier", "reference number", "eob number"],
    "desc": ["description", "service", "services", "service description", "procedure", "procedure description",
             "description of service", "type of service", "service type", "details", "drug", "drug name",
             "medication", "item", "service provided", "treatment"],
    "code": ["code", "cpt", "cpt code", "procedure code", "hcpcs", "hcpcs code", "service code", "revenue code",
             "cpt hcpcs", "cpt hcpcs code", "billing code"],
    "kind": ["claim type", "type", "kind", "type of claim", "benefit type", "coverage type"],
    "insurer": ["insurer", "insurance", "plan", "payer", "payor", "health plan", "carrier", "plan name", "insurance company",
                "insurance plan"],
}
REQUIRED = ("date", "provider", "billed", "paid", "owe")
DESCRIBE = {"date": "the date of service", "provider": "the provider", "billed": "the amount billed",
            "paid": "what the plan paid", "owe": "what you owe"}

TEMPLATE = ("Date of service,Provider,Description,Code,Billed,Plan paid,You owe,Status,Claim number\r\n"
            "2025-03-14,Valley Reference Laboratory,Comprehensive metabolic panel,80053,142.00,61.18,12.35,Processed,EX-1001\r\n"
            "2025-03-20,Dr. Avery Lin,Office visit,99214,385.00,212.40,40.00,Processed,EX-1002\r\n")

_LOOKUP = {s: f for f, spellings in FIELDS.items() for s in spellings}
_PHARMACY = re.compile(r"pharm|\brx\b|drug|prescription", re.I)
_DENIED = re.compile(r"denied|rejected|not covered", re.I)


def _field(header):
    n = _csv._norm(header)
    if n in _LOOKUP:
        return _LOOKUP[n]
    bare = _csv._norm(re.sub(r"\([^)]*\)|\[[^\]]*\]|\$", " ", header or ""))
    if bare in _LOOKUP:
        return _LOOKUP[bare]
    bare = re.sub(r"\b(amount|amt|total)\b", " ", bare)   # "Billed Amt", "Total Plan Paid"
    return _LOOKUP.get(" ".join(bare.split()))


def _columns(header):
    cols = {}
    for i, h in enumerate(header):
        f = _field(h)
        if f and f not in cols:
            cols[f] = i
    return cols


def _header(lines):
    """(index, delimiter, cells, columns) of the best header row among the first lines."""
    best = None
    for i, line in enumerate(lines[:_csv.HEADER_SCAN]):
        if not line.strip():
            continue
        d = _csv._delimiter(line)
        cells = next(csv.reader([line], delimiter=d), [])
        cols = _columns(cells)
        score = sum(f in cols for f in REQUIRED) * 10 + len(cols)
        if best is None or score > best[0]:
            best = (score, i, d, cells, cols)
        if all(f in cols for f in REQUIRED):
            break
    return best[1:] if best else None


def detect(files):
    """How sure we are that the upload is a claims spreadsheet, from header rows only.

    0.8 with every required column, above the lab spreadsheet importer's best (0.75); 0.5 without a provider
    column; 0.1 when it looks close enough to offer the column mapper."""
    best = 0.0
    for _, path in _csv._candidates(files):
        try:
            text = _csv._read_text(path, limit=16384)
        except OSError:
            continue
        h = _header(text.split("\n"))
        if not h:
            continue
        cols = h[3]
        money_cols = sum(f in cols for f in ("billed", "paid", "owe"))
        if all(f in cols for f in REQUIRED):
            score = 0.8
        elif "date" in cols and "billed" in cols and money_cols >= 2:
            score = 0.5
        elif "date" in cols and money_cols >= 2:
            score = 0.1
        else:
            score = 0.0
        best = max(best, score)
    return best


def _resolve_mapping(header, mapping):
    cols = {}
    for f, h in mapping.items():
        if f not in FIELDS or h in (None, ""):
            continue
        if isinstance(h, int):
            if not 0 <= h < len(header):
                raise ValueError(f"Column {h + 1} for {DESCRIBE.get(f, f)} isn't in the file.")
            cols[f] = h
            continue
        hits = [i for i, c in enumerate(header) if c.strip() == str(h).strip()] or \
               [i for i, c in enumerate(header) if _csv._norm(c) == _csv._norm(str(h))]
        if not hits:
            raise ValueError(f"There's no column called {h!r} in the file.")
        cols[f] = hits[0]
    return cols


def _missing_message(rel, header, cols):
    missing = [f for f in REQUIRED if f not in cols]
    names = ", ".join(DESCRIBE[f] for f in missing[:-1]) + (" and " if len(missing) > 1 else "") + DESCRIBE[missing[-1]]
    examples = "; ".join(f"{DESCRIBE[f]}: " + ", ".join(repr(s.title()) for s in FIELDS[f][:3]) for f in missing)
    found = ", ".join(h.strip() for h in header if h.strip()) or "nothing"
    return (f"{rel} has no column for {names}. Name the column{'s' if len(missing) > 1 else ''} with a header "
            f"such as {examples}, or choose the columns by hand. Columns found: {found}.")


def _rows(rel, path, mapping):
    lines = _csv._read_text(path).split("\n")
    h = _header(lines)
    if not h:
        raise ValueError(f"{rel} is empty.")
    i, delim, header, cols = h
    if mapping:
        cols = {**cols, **_resolve_mapping(header, mapping)}
    if any(f not in cols for f in REQUIRED):
        raise ValueError(_missing_message(rel, header, cols))
    out = []
    reader = csv.reader(io.StringIO("\n".join(lines[i + 1:])), delimiter=delim)
    try:
        for cells in reader:
            out.append((i + 1 + reader.line_num, cells))
    except csv.Error as e:
        raise ValueError(f"{rel} couldn't be read as a spreadsheet near line {i + 1 + reader.line_num}: {e}.") from None
    return out, cols


_MONEY_JUNK = re.compile(r"[$€£¥\s]|USD|EUR|GBP", re.I)


def _money(s, decimal_comma):
    """An amount from "$1,234.56", "1.234,56", "(12.00)" or "-12"; 0.0 for "" or "-"; None when unreadable."""
    s = _MONEY_JUNK.sub("", s or "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if s in ("", "-", "—", "–", "n/a", "N/A"):
        return 0.0
    x = _csv._number(s, decimal_comma)
    if x is None:
        return None
    return round(-float(x) if neg else float(x), 2)


def parse(files, mapping=None, insurer=None):
    """Claims from every spreadsheet in the upload, as {"data": data.json shape, "warnings": [...]}.

    mapping: optional {field: header text or column index} for files whose headers aren't recognised, with fields
    date, end, provider, billed, paid, owe, status, claim, desc, code, kind and insurer. insurer: the plan's name
    for files without an insurer column (default "Insurance"). Raises ValueError when no file can be read."""
    warn = _csv._Warnings()
    found, errors = [], []
    for rel, path in _csv._candidates(files):
        try:
            rows, cols = _rows(rel, path, mapping)
        except ValueError as e:
            errors.append(str(e))
            continue
        found.append((rel, rows, cols))
    if not found:
        if errors:
            raise ValueError(errors[0] if len(errors) == 1 else " ".join(errors))
        raise ValueError("The upload has no spreadsheet (.csv, .tsv or .txt file) to read claims from.")
    for e in errors:
        warn.add(f"Not read: {e}")

    claims = collections.OrderedDict()   # claim number (or row) -> rows, in file order
    for rel, rows, cols in found:
        get = lambda cells, f: cells[cols[f]].strip() if f in cols and cols[f] < len(cells) else ""   # noqa: E731
        amounts = [get(c, f) for _, c in rows for f in ("billed", "paid", "owe")]
        decimal_comma = any(re.search(r"\d,\d{2}\)?$", a) for a in amounts) and \
            not any(re.search(r"\d\.\d{2}\)?$", a) for a in amounts)
        order, guessed = _csv._date_order([get(c, "date") for _, c in rows] + [get(c, "end") for _, c in rows])
        for sep in sorted(guessed):
            how = "day/month" if order[sep] else "month/day"
            warn.add(f"{rel}: dates like 03{sep}04{sep}2025 could be read either way and nothing in the file "
                     f"settles it, so they were read as {how}. Check the dates if that's wrong.")
        for line, cells in rows:
            if not any(c.strip() for c in cells):
                continue
            where = f"{rel} line {line}"
            raw_date = get(cells, "date")
            if not raw_date:
                warn.row("with no date of service", where)
                continue
            t = _csv._timestamp(raw_date, "", order)
            if not t:
                warn.row("with a date that couldn't be read", f"{where} ({raw_date!r})")
                continue
            amt = {f: _money(get(cells, f), decimal_comma) for f in ("billed", "paid", "owe")}
            bad = [f for f, x in amt.items() if x is None]
            if bad:
                warn.row("with an amount that couldn't be read", f"{where} ({get(cells, bad[0])!r})")
                continue
            end = _csv._timestamp(get(cells, "end"), "", order) if get(cells, "end") else None
            row = {"t": t, "end": end, "provider": get(cells, "provider"), "desc": get(cells, "desc"),
                   "code": get(cells, "code"), "status": get(cells, "status"), "kind": get(cells, "kind"),
                   "insurer": get(cells, "insurer"), "claim": get(cells, "claim"), **amt}
            group = ("claim", row["insurer"], row["claim"]) if row["claim"] else ("row", rel, line)
            claims.setdefault(group, []).append(row)

    out = Claims(NAME)
    for group, rows in claims.items():
        _add(out, rows, insurer or INSURER)
    if out.repeated:
        warn.add(f"Left out {out.repeated} claim{'s' if out.repeated != 1 else ''} listed twice (same date, provider and amounts).")
        out.repeated = 0
    if not out.events:
        msgs = warn.list()
        raise ValueError("No claims could be read from the spreadsheet." + (" " + " ".join(msgs) if msgs else ""))
    res = out.result()
    res["warnings"] = warn.list() + res["warnings"]
    return res


def _add(out, rows, default_insurer):
    first = rows[0]
    t = min(r["t"] for r in rows)
    date = t[:10]
    last = max((r["end"] or r["t"]) for r in rows)[:10]
    billed, paid, owe = (round(sum(r[f] for r in rows), 2) for f in ("billed", "paid", "owe"))
    insurer = first["insurer"] or default_insurer
    prov = first["provider"]
    status = next((r["status"] for r in rows if r["status"]), "")
    denied = bool(_DENIED.search(status))
    claim_id = first["claim"] or None
    if claim_id:
        key = out.key(f"{NAME}:{claim_id}", f"{NAME}:{claim_id}|{date}")
    else:
        key = out.key(hkey(NAME, date, prov, first["desc"], first["code"], billed, paid, owe))
    if not key:
        return
    pharmacy = any(_PHARMACY.search(r["kind"]) for r in rows) or "pharmacy" in [r["kind"].lower() for r in rows]
    if pharmacy:
        name = first["desc"]
        drug = nice_drug(name) if name and name == name.upper() else (name or "Prescription")
        i = out.add(t, "rx", drug, " · ".join(x for x in ["Pharmacy fill", prov, insurer] if x),
                    {"type": "claim", "insurer": insurer, "kind": "Pharmacy", "drug": name or None, "provider": prov or None,
                     "claimId": claim_id, "billed": billed, "paid": paid, "owe": owe, "status": status}, key,
                    abn=1 if denied else None)
        out.cost(i, date, insurer, billed, paid, owe, status, "pharmacy", "Pharmacy", prov, drug)
        return
    lines = [{"date": (r["end"] or r["t"])[:10], "desc": r["desc"][:140], "code": r["code"], "billed": r["billed"], "denied": []}
             for r in rows if r["desc"] or r["code"]]
    title = next((l["desc"] for l in lines if l["desc"] and len(l["desc"]) < 70), "") or (prov or "Medical claim").title()
    detail = {"type": "claim", "insurer": insurer, "kind": "Medical", "provider": prov or None, "claimId": claim_id,
              "billed": billed, "paid": paid, "owe": owe, "status": status, "lines": lines}
    if last != date:
        detail["end"] = last
    i = out.add(t, "claim", title, " · ".join(x for x in [prov.title(), insurer] if x) + (" · denied" if denied else ""),
                detail, key, end=last + "T12:00:00" if last != date else None, abn=1 if denied else None)
    out.cost(i, date, insurer, billed, paid, owe, status, "medical", out.category(title, lines, prov, date), prov, title)
