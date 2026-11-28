"""Payments spreadsheet (CSV) for Costs (#1163): what someone paid for care, from an export they downloaded themselves
from a bank, card, HSA provider or budgeting app. Sanguity connects to none of them; this reads the file, nothing more.

One row per payment, with columns for the date, who was paid and the amount; optionally what it was paid with (an
HSA card, a checking account, a card: "Paid with"), a memo, what it was for (a category) and an HSA/FSA column.
Headers are matched by common spellings, as the claims and lab spreadsheets' are (claims_csv, csv_labs, whose
delimiter, encoding, number and date handling this reuses), and a caller can pass `mapping` to `parse` for files whose
headers differ. The review before saving lists the payments, and leaves out those already in the profile; the import
is undone as any other.

Each row is an entry of outOfPocket.payments: {d, payee, group, amount, account (what it was paid with), memo, est,
key}, hsa where the file says HSA or FSA (else oop.py reads it from what it was paid with). What it was for is read
from the category column, else the memo and payee: a premium, the pharmacy, dental, vision, equipment and supplies, or
medical bills. Amounts in a file where every one is negative (a bank's or budgeting app's way of writing money out)
are read as payments; in a file with both, a negative one is a refund, counted as it is.

Source keys: "payments-csv:h:" + a hash of the date, payee, amount, paid with and memo, with ":2", ":3"... for a second
and third row the same in one file (two copays on one day are two payments). The same file uploaded again, or an
export overlapping an earlier one, adds only the payments not here yet.
"""
import collections
import csv
import io
import re

from . import claims_csv as _claims_csv
from . import csv_labs as _csv
from ._claims import hkey

NAME = "payments-csv"
LABEL = "Payments spreadsheet (CSV)"

FIELDS = {
    "date": ["date", "payment date", "date paid", "paid on", "transaction date", "trans date", "posted date", "post date",
             "posting date", "posted", "date of payment", "date of service", "service date"],
    "payee": ["payee", "paid to", "pay to", "merchant", "merchant name", "vendor", "provider", "description", "name",
              "recipient", "paid to provider", "who was paid", "to"],
    "amount": ["amount", "payment amount", "amount paid", "paid amount", "payment", "debit", "debit amount", "withdrawal",
               "withdrawal amount", "charge", "charge amount", "total", "transaction amount", "amount usd"],
    "account": ["paid with", "paid from", "account", "account name", "payment method", "method", "card", "paid by",
                "source", "funding source", "from account", "pay method"],
    "memo": ["memo", "note", "notes", "comment", "comments", "reference", "details"],
    "group": ["for", "category", "type", "expense type", "expense category", "kind", "purpose"],
    "hsa": ["hsa", "fsa", "hsa fsa", "hsa or fsa", "account type", "tax advantaged"],
}
REQUIRED = ("date", "payee", "amount")
DESCRIBE = {"date": "the date", "payee": "who was paid", "amount": "the amount"}
NOT_PAYMENTS = ("billed", "owe", "unit", "range", "flag", "loinc")   # a claims or lab spreadsheet's columns

TEMPLATE = ("Date,Paid to,Amount,Paid with,Memo\r\n"
            "2026-03-21,Lakeside Clinic,40.00,HSA card,Office visit copay\r\n"
            "2026-04-03,Example Health Plan,186.00,Checking,Monthly premium\r\n")

GROUPS = (("Health insurance premiums", r"premium"), ("Pharmacy", r"pharm|\brx\b|prescription|drug"),
          ("Dental", r"dental|dentist|orthodont"), ("Vision", r"vision|optic|optometr|eye ?care|glasses|contact lens"),
          ("Equipment & supplies", r"equipment|supplies|supply|device"))
_LOOKUP = {s: f for f, spellings in FIELDS.items() for s in spellings}
_OTHERS = {s for f, spellings in _claims_csv.FIELDS.items() if f in ("billed", "owe") for s in spellings} | \
          {s for f, spellings in _csv.FIELDS.items() if f in ("unit", "range", "flag", "loinc") for s in spellings}


def _field(header):
    n = _csv._norm(header)
    if n in _LOOKUP:
        return _LOOKUP[n]
    bare = _csv._norm(re.sub(r"\([^)]*\)|\[[^\]]*\]|\$", " ", header or ""))
    return _LOOKUP.get(bare)


def _columns(header):
    cols, other = {}, False
    for i, h in enumerate(header):
        f = _field(h)
        if f and f not in cols:
            cols[f] = i
        elif not f and _csv._norm(h) in _OTHERS:
            other = True
    return cols, other


def _header(lines):
    """(index, delimiter, cells, columns, another kind's column seen) of the best header row among the first lines."""
    best = None
    for i, line in enumerate(lines[:_csv.HEADER_SCAN]):
        if not line.strip():
            continue
        d = _csv._delimiter(line)
        cells = next(csv.reader([line], delimiter=d), [])
        cols, other = _columns(cells)
        score = sum(f in cols for f in REQUIRED) * 10 + len(cols)
        if best is None or score > best[0]:
            best = (score, i, d, cells, cols, other)
        if all(f in cols for f in REQUIRED):
            break
    return best[1:] if best else None


def detect(files):
    """0.85 with a date, payee and amount column and none of a claims or lab spreadsheet's (billed, you owe, unit,
    range, flag): above the lab spreadsheet's best (0.75), which a "Date, Description, Amount" file would otherwise
    be; 0.1 with two of the three, to offer the column mapper; 0 for a claims or lab spreadsheet."""
    best = 0.0
    for _, path in _csv._candidates(files):
        try:
            text = _csv._read_text(path, limit=16384)
        except OSError:
            continue
        h = _header(text.split("\n"))
        if not h:
            continue
        cols, other = h[3], h[4]
        n = sum(f in cols for f in REQUIRED)
        score = 0.0 if other else 0.85 if n == 3 else 0.1 if n == 2 and "amount" in cols else 0.0
        best = max(best, score)
    return best


def _rows(rel, path, mapping):
    lines = _csv._read_text(path).split("\n")
    h = _header(lines)
    if not h:
        raise ValueError(f"{rel} is empty.")
    i, delim, header, cols, _ = h
    if mapping:
        cols = {**cols, **_resolve(header, mapping)}
    missing = [f for f in REQUIRED if f not in cols]
    if missing:
        names = ", ".join(DESCRIBE[f] for f in missing[:-1]) + (" and " if len(missing) > 1 else "") + DESCRIBE[missing[-1]]
        found = ", ".join(c.strip() for c in header if c.strip()) or "nothing"
        raise ValueError(f"{rel} has no column for {names}. Name the columns Date, Paid to and Amount (Paid with and "
                         f"Memo if it has them), or choose the columns by hand. Columns found: {found}.")
    out = []
    reader = csv.reader(io.StringIO("\n".join(lines[i + 1:])), delimiter=delim)
    try:
        for cells in reader:
            out.append((i + 1 + reader.line_num, cells))
    except csv.Error as e:
        raise ValueError(f"{rel} couldn't be read as a spreadsheet near line {i + 1 + reader.line_num}: {e}.") from None
    return out, cols


def _resolve(header, mapping):
    cols = {}
    for f, h in mapping.items():
        if f not in FIELDS or h in (None, ""):
            continue
        if isinstance(h, int):
            if not 0 <= h < len(header):
                raise ValueError(f"Column {h + 1} for {DESCRIBE.get(f, f)} isn't in the file.")
            cols[f] = h
            continue
        hits = [k for k, c in enumerate(header) if c.strip() == str(h).strip()] or \
               [k for k, c in enumerate(header) if _csv._norm(c) == _csv._norm(str(h))]
        if not hits:
            raise ValueError(f"There's no column called {h!r} in the file.")
        cols[f] = hits[0]
    return cols


KNOWN = ("Medical bills", "Pharmacy", "Health insurance premiums", "Dental", "Vision", "Equipment & supplies", "Other medical")


def group_of(category, *texts):
    """What a payment was for: its category as written where it's one of Type records in's (KNOWN) or names a
    premium ("Dental insurance premiums"); else GROUPS' first that fits the category, memo or payee; else medical
    bills."""
    if category and (category.lower() in {k.lower() for k in KNOWN} or re.search(r"premium", category, re.I)):
        return next((k for k in KNOWN if k.lower() == category.lower()), category)
    t = " ".join(x for x in (category, *texts) if x)
    return next((g for g, rx in GROUPS if re.search(rx, t, re.I)), "Medical bills")


def parse(files, mapping=None):
    """Payments from every spreadsheet in the upload, as {"data": data.json shape, "warnings": [...]}.

    mapping: optional {field: header text or column index} with fields date, payee, amount, account, memo, group and
    hsa. Raises ValueError when no file can be read."""
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
        raise ValueError("The upload has no spreadsheet (.csv, .tsv or .txt file) to read payments from.")
    for e in errors:
        warn.add(f"Not read: {e}")

    payments, seen = [], collections.Counter()
    for rel, rows, cols in found:
        get = lambda cells, f: cells[cols[f]].strip() if f in cols and cols[f] < len(cells) else ""   # noqa: E731
        amounts = [get(c, "amount") for _, c in rows]
        decimal_comma = any(re.search(r"\d,\d{2}\)?$", a) for a in amounts) and not any(re.search(r"\d\.\d{2}\)?$", a) for a in amounts)
        order, guessed = _csv._date_order([get(c, "date") for _, c in rows])
        for sep in sorted(guessed):
            how = "day/month" if order[sep] else "month/day"
            warn.add(f"{rel}: dates like 03{sep}04{sep}2025 could be read either way and nothing in the file "
                     f"settles it, so they were read as {how}. Check the dates if that's wrong.")
        read = []
        for line, cells in rows:
            if not any(c.strip() for c in cells):
                continue
            where = f"{rel} line {line}"
            raw = get(cells, "date")
            t = _csv._timestamp(raw, "", order) if raw else None
            if not raw:
                warn.row("with no date", where)
                continue
            if not t:
                warn.row("with a date that couldn't be read", f"{where} ({raw!r})")
                continue
            amount = _claims_csv._money(get(cells, "amount"), decimal_comma)
            if amount is None or not get(cells, "amount"):
                warn.row("with no amount that could be read", f"{where} ({get(cells, 'amount')!r})")
                continue
            payee = get(cells, "payee")
            if not payee:
                warn.row("with no payee", where)
                continue
            read.append((t[:10], payee, amount, get(cells, "account"), get(cells, "memo"), get(cells, "group"), get(cells, "hsa")))
        nonzero = [r[2] for r in read if abs(r[2]) >= 0.005]
        flip = bool(nonzero) and all(a < 0 for a in nonzero)
        if flip:
            warn.add(f"{rel}: every amount is negative, as a bank or budgeting app writes money paid out, so they were read as payments.")
        elif any(a < 0 for a in nonzero):
            n = sum(1 for a in nonzero if a < 0)
            warn.add(f"{rel}: {n} amount{'s are' if n != 1 else ' is'} negative, read as {'refunds' if n != 1 else 'a refund'}.")
        for d, payee, amount, account, memo, group, hsa in read:
            amount = round(-amount if flip else amount, 2)
            base = hkey(NAME, d, payee, f"{amount:.2f}", account, memo)
            seen[base] += 1
            p = {"d": d, "payee": payee, "group": group_of(group, memo, payee), "amount": amount, "account": account,
                 "memo": memo, "est": False, "key": base if seen[base] == 1 else f"{base}:{seen[base]}"}
            tag = re.search(r"\b(HSA|FSA)\b", hsa, re.I) if hsa else None
            if tag:
                p["hsa"] = tag.group(1).upper()
            payments.append(p)
    if not payments:
        msgs = warn.list()
        raise ValueError("No payments could be read from the spreadsheet." + (" " + " ".join(msgs) if msgs else ""))
    payments.sort(key=lambda p: (p["d"], p["payee"].lower(), p["amount"]))
    return {"data": {"events": [], "details": {}, "labs": [], "costs": [], "outOfPocket": {"payments": payments}},
            "warnings": warn.list()}
