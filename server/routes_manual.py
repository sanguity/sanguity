"""Records typed in on the import page (issue #27, hand entry): lab results and every other kind Sanguity imports.

  GET  /api/lab-names                   lab-aliases.json's canonical test names, for the test field's suggestions
  POST /api/profiles/{pid}/manual       {"rows": [...], "items": [...], "save": false}: read them, say what each became
                                        {"rows": [...], "items": [...], "save": true}: the same, then save them as one import
                                        "keep_duplicates": true also saves what the profile has already

rows are lab results: date, time, test, value, unit, range (or low and high), flag and lab, as text. Each is read by
the CSV importer's own row reader (csv_labs.read_row) and the result is built by its build(), so a typed result is
the same as that result in a spreadsheet: the same source keys, canonical names, ranges and flags. Only the source
tag on the points ("Typed in") differs. A test name matched to a series on an earlier import's preview (labmap.py:
remembered for hand entry, else for a spreadsheet) goes into that series here too, in the readback and when saved.

items are the other kinds: {"kind": "visit" | "stay" | "procedure" | "imaging" | "study" | "med" | "rx" | "problem" |
"immunization" | "claim" | "payment" | "upcoming" | "vitals" | "home" | "workout" | "document", <field>: <text>...}; manual_items.py
lists each kind's fields and what it becomes. Both lists are optional (a body with only "rows" works as it always
has), and one save may hold any mix of them. A document's file isn't in the body (the API's bodies stay under nginx's
64 KB cap): the page sends it through the upload routes first (routes_uploads.py: nginx's upload route and its
limits) and names the finished upload and the file in it ("upload", "file"). The file stays where the upload stored
it, sealed, and is indexed as a document of the import.

The readback says, per row and per record: ok, its problems (records also "fields": {field: problem}, for the page to
mark), notes, what it became, and "duplicate" when the profile has it already. Duplicates are found by the import
preview's own check (preview.find over everything typed, as format "manual"), and for home measurements and
workouts in the profile's watch summary. A row or record that can't be read, or that repeats another typed in the
same save, is refused with its reason, and nothing is saved until everything reads. Duplicates are left out of a
save, as the preview leaves them out of an upload, unless keep_duplicates; a save of nothing but duplicates is
refused.

A save is one import (records.store, origin "manual", format "manual"), run through importers.check() first, in a
transaction with its documents and its activity log entry ("import", {"importer": "manual", "counts"}, with how many
of each kind were typed in counts["typed"]). Home measurements and workouts are saved as that import's own watch data
(watch_store.save_import with its id). So undoing the import (history.py) takes all of it away: the records, the
documents and the uploads holding their files, and the watch data. Owners and editors only; the CSRF check in
main.guard covers the POST like every other change.
"""
import collections, os
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import (activity, db as dbm, importers as registry, jobs, labmap, manual_items, preview, profiles as prof, records,
               uploads, watch_store)
from .importers import common, csv_labs
from .main import _client_ip, get_db, need, user

router = APIRouter()

NAME = "manual"            # imports.origin and imports.format, and the activity log's importer
SOURCE = "Typed in"        # source tag on the points, shown on Trends
MATCHED_AS = (NAME, csv_labs.NAME)   # whose remembered lab-name matches apply, in this order: typed rows are built as a CSV's
MAX_ROWS = 200             # the API's bodies stay under nginx's 64 KB cap
MAX_ITEMS = 100
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
    rows: List[Row] = Field(default_factory=list, max_length=MAX_ROWS)
    items: List[Dict[str, Any]] = Field(default_factory=list, max_length=MAX_ITEMS)
    save: bool = False
    keep_duplicates: bool = False


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


def _describe(n, r, problems, notes, matched=None, dup=False):
    d = {"row": n, "ok": not problems, "problems": problems, "notes": notes, "duplicate": dup}
    if r:
        d.update(date=_display(r["t"]), name=(matched or {}).get(n) or common.canonical_lab(r["test"], r["loinc"]) or r["test"],
                 value=r["x"], text=r["reported"] if r["x"] is None else None, unit=r["unit"], low=r["lo"], high=r["hi"],
                 flag=r["flag"])
    return d


# ---------------------------------------------------------------- the other kinds of record (manual_items.py)

def _uploads(db, pid):
    """(upload id, file name) -> (the sealed file a typed document names, None) or (None, why it can't be used)."""
    stored = None

    def find(upload_id, name):
        nonlocal stored
        if stored is None:
            stored = {(u, rel): path for u, rel, path in uploads.stored_files(db, pid)}
        path = stored.get((upload_id, name))
        if path and os.path.isfile(path):
            return os.path.realpath(path), None
        r = db.execute("SELECT state FROM uploads WHERE id=? AND profile_id=?", (upload_id, pid)).fetchone()
        if r and r["state"] == "receiving":
            return None, "has a file that's still being uploaded"
        return None, "has a file that isn't uploaded any more: choose it again"
    return find


def read_items(db, pid, items):
    """[record read (manual_items.read)] for the non-empty items, numbered from 1 as sent."""
    cleaned = [(n, it, *manual_items.clean(it)) for n, it in enumerate(items, start=1)]
    cleaned = [c for c in cleaned if not manual_items.is_empty(c[2], c[3])]
    order, guessed = csv_labs.date_order([d for c in cleaned for d in manual_items.dates_of(c[3])])
    find = _uploads(db, pid)
    out, seen = [], {}
    for n, it, *_ in cleaned:
        rec = manual_items.read(n, it, order, guessed, find)
        if rec["ok"] and rec.get("ident") is not None:
            if rec["ident"] in seen:
                rec["problems"].append(f"repeats record {seen[rec['ident']]}")
                rec["ok"], rec["parts"] = False, None
            seen.setdefault(rec["ident"], n)
        out.append(rec)
    return out


def _combine(lab, items):
    """The lab rows' data and the records' data as one object."""
    data = {k: list(lab.get(k) or []) for k in ("events", "labs", "costs")}
    data["details"] = {str(k): v for k, v in (lab.get("details") or {}).items()}
    data["events"] += items["events"]
    data["details"].update({str(k): v for k, v in items["details"].items()})
    data["labs"] += items["labs"]
    data["costs"] += items["costs"]
    data.update({k: v for k, v in items.items() if k not in records.ROWS})
    return data


def _duplicates(db, pid, data, lab, got, origin, have_watch):
    """(preview.find's duplicates, {row number: duplicate?}, {record number: "all" | "some"} of its parts)."""
    dups = preview.find(db, pid, NAME, data)
    by_key = {e.get("key"): str(e["id"]) for e in lab.get("events") or [] if e.get("k") == "labitem"}
    rows = {n: by_key.get(csv_labs._key(r)) in dups["events"] for n, r, p, _ in got if r and not p}
    offset = len(lab.get("labs") or [])
    index = {s["name"]: offset + i for i, s in enumerate(data["labs"][offset:])}
    parts = collections.defaultdict(list)   # record number -> [is each of its parts a duplicate]
    for eid, n in origin["events"].items():
        parts[n].append(eid in dups["events"])
    for (name, i), n in origin["points"].items():
        parts[n].append(i in dups["points"].get(index[name], ()))
    for (path, i), n in origin["meta"].items():
        parts[n].append(i in dups["meta"].get(path, ()))
    watch_dup = manual_items.watch_duplicates(have_watch, origin["watch"])
    for n, _ in origin["watch"]:
        parts[n].append(n in watch_dup)
    found = {n: "all" if all(xs) else "some" for n, xs in parts.items() if any(xs)}
    return dups, rows, found


def _item(rec, dup=None):
    d = {"n": rec["n"], "kind": rec["kind"], "label": rec.get("label") or rec["kind"], "ok": rec["ok"],
         "problems": rec["problems"], "fields": rec.get("fields") or {}, "notes": list(rec.get("notes") or []),
         "summary": rec.get("summary"), "duplicate": dup == "all"}
    if dup == "some":
        d["notes"].append("part of it is in this profile already, and that part won't be saved again")
    return d


def _without_watch_dups(watch, origin, found, keep):
    """The watch summary less the points and workouts of records whose watch data the profile has already."""
    if not watch or keep:
        return watch
    drop = {part[:3] if part[0] == "m" else part[:2] for n, part in origin if found.get(n)}
    metrics = [{**m, "pts": [p for p in m["pts"] if ("m", m["key"], p[0]) not in drop]} for m in watch["metrics"]]
    metrics = [m for m in metrics if m["pts"]]
    exercise = [w for w in watch["exercise"] if ("w", w["key"]) not in drop]
    return {"metrics": metrics, "exercise": exercise} if metrics or exercise else None


@router.post("/api/profiles/{pid}/manual")
def manual(pid: int, body: Rows, request: Request, s=Depends(user), db=Depends(get_db)):
    """Check typed results and records, and with save, store them. A refusal (400) says which and why, and saves
    nothing."""
    need(db, s, pid, "editor")
    got = read(body.rows)
    matched = _matched(db, got)
    recs = read_items(db, pid, body.items)
    results = [r for _, r, p, _ in got if r and not p]
    lab = labmap.apply_remembered(db, MATCHED_AS, csv_labs.build(results, source=SOURCE, sub=SOURCE))[0] if results else {}
    base = max([e["id"] for e in lab.get("events") or []], default=0)
    built, origin = manual_items.build([r for r in recs if r["ok"]], base_id=base)
    data = _combine(lab, built["data"])
    have_watch = watch_store.load(db, pid) if origin["watch"] else None
    dups, dup_rows, found = _duplicates(db, pid, data, lab, got, origin, have_watch)

    shown = [_describe(*x, matched, dup_rows.get(x[0], False)) for x in got]
    items = [_item(r, found.get(r["n"])) for r in recs]
    bad = [f"Row {n} {'; '.join(p)}." for n, _, p, _ in got if p]
    bad += [f"Record {r['n']} ({(r.get('label') or 'record').lower()}) {'; '.join(r['problems'])}." for r in recs if r["problems"]]
    n_dups = sum(1 for d in dup_rows.values() if d) + sum(1 for d in found.values() if d == "all")
    answer = {"rows": shown, "items": items, "problems": bad, "duplicates": n_dups}
    if not body.save:
        return answer
    if not got and not recs:
        return JSONResponse({"detail": "There's nothing to save: type a result first.", **answer}, status_code=400)
    if bad:
        msg = " ".join(bad[:5]) + (f" And {len(bad) - 5} more." if len(bad) > 5 else "") + " Nothing was saved."
        return JSONResponse({"detail": msg, **answer}, status_code=400)
    new = data if body.keep_duplicates else preview.trim(data, dups)
    watch = _without_watch_dups(built["watch"], origin["watch"], found, body.keep_duplicates)
    if not preview.adds_anything(new) and not watch:
        return JSONResponse({"detail": "Everything here is in this profile already, so nothing was saved. To save it "
                                       "again anyway, tick “Save again what's already there”.", **answer}, status_code=400)
    if registry.check({"data": new, "warnings": []}):   # a bug, not the typist's doing
        raise HTTPException(500, "The typed records came out in a shape Sanguity can't load. Nothing was saved.")
    summary = jobs.summarise(new)
    counts = summary["counts"]
    skipped = 0 if body.keep_duplicates else n_dups
    saved_rows = sum(1 for n, r, p, _ in got if r and not p and (body.keep_duplicates or not dup_rows.get(n)))
    kinds = ["labs"] * saved_rows + [r["kind"] for r in recs if body.keep_duplicates or found.get(r["n"]) != "all"]
    counts["typed"] = dict(sorted(collections.Counter(kinds).items()))
    files = built["files"]
    with dbm.transaction(db):
        keepdocs, counts["documents"] = prof.index_documents(db, pid, new["details"], lambda url: files.get(url))
        iid = records.store(db, pid, new, NAME, fmt=NAME)
        prof.link_documents(db, iid, keepdocs)
        if watch:   # last: its file is written once everything else is in the transaction
            w = watch_store.save_import(db, pid, watch, import_id=iid)
            counts.update(watch_days=w["days"], workouts=w["workouts"])
        activity.log(db, "import", s, pid, {"importer": NAME, "counts": counts, **({"skipped": skipped} if skipped else {})},
                     ip=_client_ip(request))
    return {"import_id": iid, "counts": counts, "range": summary["range"], "rows": shown, "items": items,
            "skipped": skipped, "series": [x["name"] for x in new["labs"]]}
