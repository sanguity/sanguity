"""Lab results typed in on the import page (issue #27, hand entry).

  GET  /api/lab-names                   lab-aliases.json's canonical test names, for the test field's suggestions
  POST /api/profiles/{pid}/manual       {"rows": [...], "save": false}: read the rows and say what each became
                                        {"rows": [...], "save": true}: the same, then save them as one import

A row has date, time, test, value, unit, range (or low and high), flag and lab, as text. Each is read by the CSV
importer's own row reader (csv_labs.read_row) and the result is built by its build(), so a typed result is the same
as that result in a spreadsheet: the same source keys, canonical names, ranges and flags. Only the source tag on the
points ("Typed in") differs. Unlike a spreadsheet, where unreadable rows are skipped with a warning, a typed row that
can't be read is refused with its reason, and nothing is saved until every row reads. A test name matched to a
series on an earlier import's preview (labmap.py: remembered for hand entry, else for a spreadsheet) goes into that
series here too, in the readback and when saved.

A save is one import (records.store, origin "manual", format "manual") in a transaction with its activity log entry
("import", {"importer": "manual", "counts"}), so undoing an import covers it like any other. Owners and editors
only; the CSRF check in main.guard covers the POST like every other change.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import activity, db as dbm, importers as registry, jobs, labmap, records
from .importers import common, csv_labs
from .main import _client_ip, get_db, need, user

router = APIRouter()

NAME = "manual"            # imports.origin and imports.format, and the activity log's importer
SOURCE = "Typed in"        # source tag on the points, shown on Trends
MATCHED_AS = (NAME, csv_labs.NAME)   # whose remembered lab-name matches apply, in this order: typed rows are built as a CSV's
MAX_ROWS = 200             # the API's bodies stay under nginx's 64 KB cap
FIELDS = ("date", "time", "test", "value", "unit", "range", "low", "high", "flag", "lab")
WHY = {"with no test name": "needs a test name", "with no date": "needs a date",
       "with a date that couldn't be read": "has a date that couldn't be read", "with no value": "needs a value"}


class Row(BaseModel):
    date: str = Field("", max_length=40)
    time: str = Field("", max_length=20)
    test: str = Field("", max_length=120)
    value: str = Field("", max_length=60)
    unit: str = Field("", max_length=30)
    range: str = Field("", max_length=60)
    low: str = Field("", max_length=20)
    high: str = Field("", max_length=20)
    flag: str = Field("", max_length=20)
    lab: str = Field("", max_length=120)


class Rows(BaseModel):
    rows: List[Row] = Field(..., max_length=MAX_ROWS)
    save: bool = False


@router.get("/api/lab-names")
def lab_names(s=Depends(user)):
    """The canonical names results are charted under (no personal data: the app's own list)."""
    return sorted(common.lab_defs(), key=str.lower)


def _display(t):
    return t[:10] if t.endswith("T12:00:00") else t[:16].replace("T", " ")


def read(rows):
    """[(row number, result or None, [problems], [notes])] for the non-empty rows, numbered from 1 as shown."""
    cells = [{f: " ".join((getattr(r, f) or "").split()) for f in FIELDS} for r in rows]
    order, guessed = csv_labs.date_order(c["date"] for c in cells)
    out, seen = [], {}
    for n, c in enumerate(cells, start=1):
        if not any(c.values()):
            continue
        problems, notes = [], []
        if guessed and csv_labs.guessed_date(c["date"], guessed):
            problems.append(f"has a date, {c['date']}, that could be day/month or month/day: write it as YYYY-MM-DD")
            out.append((n, None, problems, notes))
            continue
        r, why = csv_labs.read_row(lambda f: c.get(f, ""), False, order, True)
        if why:
            problems.append(WHY.get(why[0], why[0]) + (f" ({c['date']})" if why[1] else ""))
            out.append((n, None, problems, notes))
            continue
        ident = csv_labs._identity(r)
        if ident in seen:
            problems.append(f"repeats row {seen[ident]}")
        seen.setdefault(ident, n)
        if r["x"] is None:
            notes.append("not a number: kept as a text result, not charted")
        if (c["range"] or c["low"] or c["high"]) and r["lo"] is None and r["hi"] is None:
            notes.append("the range couldn't be read, so it isn't used for the flag")
        if c["flag"] and c["flag"].upper() not in ("H", "L") and not r["flag"]:
            notes.append(f"flag {c['flag']!r} isn't high or low")
        out.append((n, r, problems, notes))
    return out


def _matched(db, got):
    """{row number: series name} for rows whose test name was matched to a series before (labmap.remembered)."""
    idents = {n: (r["test"], r["unit"], r["loinc"] or "") for n, r, _, _ in got
              if r and not common.canonical_lab(r["test"], r["loinc"])}
    return {n: f["target"] for n, f in labmap.remembered(db, MATCHED_AS, idents).items() if f["target"]}


def _describe(n, r, problems, notes, matched=None):
    d = {"row": n, "ok": not problems, "problems": problems, "notes": notes}
    if r:
        d.update(date=_display(r["t"]), name=(matched or {}).get(n) or common.canonical_lab(r["test"], r["loinc"]) or r["test"],
                 value=r["x"], text=r["reported"] if r["x"] is None else None, unit=r["unit"], low=r["lo"], high=r["hi"],
                 flag=r["flag"])
    return d


@router.post("/api/profiles/{pid}/manual")
def manual(pid: int, body: Rows, request: Request, s=Depends(user), db=Depends(get_db)):
    """Check typed results, and with save, store them. A refusal (400) says which rows and why, and saves nothing."""
    need(db, s, pid, "editor")
    got = read(body.rows)
    matched = _matched(db, got)
    shown = [_describe(*x, matched) for x in got]
    bad = [f"Row {n} {'; '.join(p)}." for n, _, p, _ in got if p]
    if not body.save:
        return {"rows": shown, "problems": bad}
    if not got:
        return JSONResponse({"detail": "There's nothing to save: type a result first.", "rows": shown, "problems": bad},
                            status_code=400)
    if bad:
        msg = " ".join(bad[:5]) + (f" And {len(bad) - 5} more." if len(bad) > 5 else "") + " Nothing was saved."
        return JSONResponse({"detail": msg, "rows": shown, "problems": bad}, status_code=400)
    results = [r for _, r, _, _ in got]
    data, _ = labmap.apply_remembered(db, MATCHED_AS, csv_labs.build(results, source=SOURCE, sub=SOURCE))
    out = {"data": data, "warnings": []}
    problems = registry.check(out)
    if problems:   # a bug, not the typist's doing
        raise HTTPException(500, "The typed results came out in a shape Sanguity can't load. Nothing was saved.")
    summary = jobs.summarise(out["data"])
    with dbm.transaction(db):
        iid = records.store(db, pid, out["data"], NAME, fmt=NAME)
        activity.log(db, "import", s, pid, {"importer": NAME, "counts": summary["counts"]}, ip=_client_ip(request))
    return {"import_id": iid, "counts": summary["counts"], "range": summary["range"], "rows": shown,
            "series": [x["name"] for x in out["data"]["labs"]]}
