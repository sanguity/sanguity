"""Costs' tax-year report (#1136): one zip of a tax year's out-of-pocket payments, for itemized deductions and HSA
records. Facts only: what was paid, when, to whom, for what, and whether a receipt is on file; no tax rules assumed.

The payments are exactly Costs' out-of-pocket payments (oop.py, data["oop"]): every payment typed in or exported,
counted once (one that arrived twice is left out, as Costs counts it once), a refund or adjustment as it is. A claim's
share with no payment isn't a payment, so it isn't here. The tax year is the calendar year, or the year from the
month the profile's setting costs.taxStart says (4: April to March), named by the year it starts in ("2026", or
"2026-27" when it doesn't start in January).

The zip (streamed, a receipt a piece at a time, so a large year never sits in memory whole):
  summary.pdf    totals for the year, by kind (the payment's group) and by who was paid, and a note to check what
                 counts with a tax adviser
  payments.csv   every payment: Row, Date, Paid to, For, Amount, HSA/FSA, From (typed in or an export), Receipt file
  receipts/      each payment's receipt, named by its row and date (receipts/003-2026-03-21.pdf), as its CSV row says
Names hold nothing of the person's but the dates: no names of payees or files, no account numbers or memos.
"""
import csv, datetime, io, json, os, re, zipfile

from . import crypto, pdfdoc, records

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
DOC_URL = re.compile(r"^api/profiles/\d+/documents/([^/#?]+)")
NOTE = ("These are the payments recorded in Sanguity, typed in or from a finance export. What counts for a deduction "
        "or an HSA depends on where you file: check with a tax adviser.")


def span(year, start=1):
    """(first day, last day) of the tax year that starts in year, in month start, as ISO dates."""
    a = datetime.date(year, start, 1)
    b = datetime.date(year + 1, start, 1) - datetime.timedelta(days=1) if start > 1 else datetime.date(year, 12, 31)
    return a.isoformat(), b.isoformat()


def label(year, start=1):
    return str(year) if start == 1 else f"{year}-{str(year + 1)[2:]}"


def year_of(d, start=1):
    """The tax year an ISO date is in (the year it starts in)."""
    y, m = int(d[:4]), int(d[5:7])
    return y if m >= start else y - 1


def _money(x):
    return round(float(x), 2) if isinstance(x, (int, float)) and not isinstance(x, bool) else 0.0


def payments(data, year, start=1):
    """The tax year's payments, by date: [{n, d, payee, group, amount, hsa, src, receipt (document id or None),
    ext}]."""
    a, b = span(year, start)
    pays = ((data.get("outOfPocket") or {}).get("payments")) if isinstance(data.get("outOfPocket"), dict) else None
    pays = pays if isinstance(pays, list) else []
    info = {p["i"]: p for p in (data.get("oop") or {}).get("payments") or []}
    details = data.get("details") or {}
    out = []
    for i, p in enumerate(pays):
        x = info.get(i) or {}
        if not isinstance(p, dict) or x.get("dup") is not None or not isinstance(p.get("d"), str) or not a <= p["d"][:10] <= b:
            continue
        doc = None
        if x.get("receipt") is not None:
            m = DOC_URL.match(str((details.get(str(x["receipt"])) or {}).get("url") or ""))
            doc = m.group(1) if m else None
        out.append({"d": p["d"][:10], "payee": str(p.get("payee") or ""), "group": str(p.get("group") or "Other"),
                    "amount": _money(p.get("amount")), "hsa": x.get("hsa") or "", "src": "Typed in" if x.get("src") == "typed" else "Finance export",
                    "receipt": doc})
    out.sort(key=lambda r: (r["d"], r["payee"].lower(), r["amount"]))
    for n, r in enumerate(out, start=1):
        r["n"] = n
    return out


def years(data, start=1):
    """The tax years with a payment, newest first."""
    pays = ((data.get("outOfPocket") or {}).get("payments")) if isinstance(data.get("outOfPocket"), dict) else None
    ds = {p["d"][:10] for p in pays or [] if isinstance(p, dict) and isinstance(p.get("d"), str) and re.match(r"\d{4}-\d\d-\d\d", p["d"])}
    return sorted({year_of(d, start) for d in ds}, reverse=True)


def plan(db, pid, year, start=1):
    """What the report needs, gathered now (the stream runs after the request's database connection closes):
    {year, start, label, from, to, rows, files {document id: (path, ext)}, total, count, receipts, missing}."""
    body = records.served(db, pid)
    data = json.loads(body) if body else {}
    rows = payments(data, year, start)
    wanted = {r["receipt"] for r in rows if r["receipt"]}
    files = {}
    for r in db.execute("SELECT id, path, ext FROM documents WHERE profile_id=?", (pid,)):
        if r["id"] in wanted:
            path = crypto.dec(r["path"])
            if os.path.isfile(path):
                files[r["id"]] = (path, re.sub(r"[^a-z0-9]", "", str(r["ext"] or "").lower())[:5] or "bin")
    for r in rows:
        if r["receipt"] not in files:
            r["receipt"] = None
        r["file"] = f"receipts/{r['n']:03d}-{r['d']}.{files[r['receipt']][1]}" if r["receipt"] else ""
    a, b = span(year, start)
    return {"year": year, "start": start, "label": label(year, start), "from": a, "to": b, "rows": rows, "files": files,
            "total": round(sum(r["amount"] for r in rows), 2), "count": len(rows),
            "receipts": sum(1 for r in rows if r["file"]), "missing": sum(1 for r in rows if not r["file"])}


def preview(p):
    return {k: p[k] for k in ("year", "start", "label", "from", "to", "total", "count", "receipts", "missing")}


def filename(p):
    return f"sanguity-tax-year-{p['label']}.zip"


def _cell(v):
    """A CSV cell a spreadsheet won't read as a formula."""
    s = str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def csv_bytes(p):
    f = io.StringIO()
    w = csv.writer(f, lineterminator="\r\n")
    w.writerow(["Row", "Date", "Paid to", "For", "Amount", "HSA/FSA", "From", "Receipt file"])
    for r in p["rows"]:
        w.writerow([r["n"], r["d"], _cell(r["payee"]), _cell(r["group"]), f"{r['amount']:.2f}", r["hsa"], r["src"], r["file"]])
    return ("﻿" + f.getvalue()).encode("utf-8")   # with a BOM, so a spreadsheet reads it as UTF-8


def _date(d):
    return f"{MONTHS[int(d[5:7]) - 1]} {int(d[8:10])}, {d[:4]}"


def _usd(v):
    return ("-" if v < 0 else "") + f"${abs(v):,.2f}"


def pdf_bytes(p):
    doc = pdfdoc.Document(title=f"Out-of-pocket payments, tax year {p['label']}")
    page, y = doc.page(), 740
    L, R = 54, pdfdoc.WIDTH - 54

    def room(need):
        nonlocal page, y
        if y - need < 60:
            page, y = doc.page(), 740

    def line(text, size=10, bold=False, gap=14):
        nonlocal y
        for part in pdfdoc.wrap(text, size, R - L, bold) or [""]:
            room(gap)
            page.text(L, y, part, size, bold)
            y -= gap

    def table(title, groups):
        nonlocal y
        y -= 8
        line(title, 12, True, 18)
        room(16)
        page.text_right(R - 90, y, "Payments", 9, True)
        page.text_right(R, y, "Amount", 9, True)
        y -= 4
        page.line(L, y, R, y)
        y -= 12
        for name, (n, v) in groups:
            room(14)
            cut = name
            while pdfdoc.width(cut, 10) > R - L - 170 and len(cut) > 1:
                cut = cut[:-2] + "…"
            page.text(L, y, cut, 10)
            page.text_right(R - 90, y, str(n), 10)
            page.text_right(R, y, _usd(v), 10)
            y -= 14

    def grouped(key):
        g = {}
        for r in p["rows"]:
            n, v = g.get(key(r), (0, 0.0))
            g[key(r)] = (n + 1, round(v + r["amount"], 2))
        return sorted(g.items(), key=lambda kv: (-kv[1][1], kv[0].lower()))

    line(f"Out-of-pocket payments, tax year {p['label']}", 16, True, 22)
    line(f"{_date(p['from'])} to {_date(p['to'])}", 10, False, 20)
    line(f"Total paid: {_usd(p['total'])} in {p['count']} payment{'' if p['count'] == 1 else 's'}", 12, True, 18)
    line(f"Receipts on file: {p['receipts']} · without a receipt: {p['missing']}")
    for tag in ("HSA", "FSA"):
        tagged = [r for r in p["rows"] if r["hsa"] == tag]
        if tagged:
            line(f"Paid with an {tag}: {_usd(round(sum(r['amount'] for r in tagged), 2))} in {len(tagged)} payment{'' if len(tagged) == 1 else 's'}")
    refunds = [r for r in p["rows"] if r["amount"] < 0]
    if refunds:
        line(f"Refunds and adjustments, counted as they are: {_usd(round(sum(r['amount'] for r in refunds), 2))} in {len(refunds)} payment{'' if len(refunds) == 1 else 's'}")
    if p["rows"]:
        table("By kind", grouped(lambda r: r["group"]))
        table("By who was paid", grouped(lambda r: r["payee"] or "(no payee)"))
    y -= 10
    line(NOTE, 9, False, 12)
    line("Every payment is in payments.csv, its receipt (when there is one) in receipts/ under the name its row gives.", 9, False, 12)
    return doc.output()


def stream(p, now=None):
    """The zip, a piece at a time."""
    from .export import _Sink
    stamp = (now or datetime.datetime.now()).timetuple()[:6]
    sink = _Sink()
    zf = zipfile.ZipFile(sink, "w", zipfile.ZIP_DEFLATED, compresslevel=6)

    def info(name, compress=True):
        i = zipfile.ZipInfo(name, stamp)
        i.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
        i.external_attr = 0o600 << 16
        return i

    zf.writestr(info("summary.pdf", False), pdf_bytes(p))
    yield sink.take()
    zf.writestr(info("payments.csv"), csv_bytes(p))
    yield sink.take()
    for r in p["rows"]:
        if not r["file"]:
            continue
        path, ext = p["files"][r["receipt"]]
        with zf.open(info(r["file"], ext not in ("pdf", "jpg", "jpeg", "png", "gif", "webp")), "w", force_zip64=True) as dst:
            for piece in crypto.iter_file(path):
                dst.write(piece)
                yield sink.take()
        yield sink.take()
    zf.close()
    yield sink.take()
