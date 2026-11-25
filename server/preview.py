"""What an import would add, before it's saved (#22): duplicates of the profile's records, found and dropped.

A parsed record is a duplicate when the profile already has one with the same source key (docs/importers.md,
"Source keys"), found by its key_hash (records.py) without decrypting the rows. A record without a key falls back
to the same importer, date, kind and content: the bodies of keyless records that earlier imports of the same format
brought are compared as they are (importers give keys nowadays, so these are few).

  events      the event's key, else its body (date, kind, title...) without the id
  lab results a point is its series' key (or, without one, the series name and unit) and its time and value as
              reported; a series adds only the points the profile doesn't have. A text result is a point already
              here, and a point one, where it reads as that point (#874: _readings, its value read by the one reader
              of a number, common.value_number, and its comparator): a file read again after a reading of it changed
              between text and a number (#870's: a FHIR bound, a C-CDA's "4,5", the FHIR export's "=<5" and "1,250"
              sent as text before, as Quantities now) added its results again beside their older copies
  the rest    entries of the profile-wide lists (problems, upcoming, clinicWeights, milestones, outOfPocket.payments)
              already there, by key or by content

Duplicates are dropped, not replaced. A lab day is kept while any of its results is new (else they couldn't be
opened) and dropped when all of them are duplicates; keeping one anyway keeps its results, and keeping a result
keeps its row and its day, holding only it (#887: not its report's other results). jobs.py re-runs importers.check()
on what's left before saving it.

A lab result already here (#846) is never dropped with what its copy here lacks: its copy in the upload is kept
("copies", not a duplicate) where it says anything of its range and flag that no copy here says (a range where there's
none, a flag, its own range where they differ: crossmatch.said, ranges.copies and ranges.agree), or where its row in
the upload's lab day is kept (a result new to the profile: another file's report of it), so that row reads its own
result's flag as every row does (#782). The page reads the copies as one result (crossmatch.copies, #825). It was
dropped with them: a spreadsheet with no range, then a C-CDA of the same results with its report's 135–145 and High,
left Labs, the Overview, the summary and share links saying 150 unflagged while the C-CDA's lab day said "Out of
range 3"; the other way round, the spreadsheet's lab day said "Out of range: None".

One rule for a result already here (#868), an upload's, a sync's and Type them in's alike (they all ask find()):
  - it's kept as a copy only with its own row: the row's labitem in the upload is kept with it, its key here or not,
    and its lab day with that; a copy the upload gives no row of its own is left out, a duplicate, never a point
    without its lab day (one was kept with its row left out by key: Type them in's "won't be saved again" saved it,
    and every lab day of its date counted it, util.js labDraws)
  - what it brings is said ("brings": "range", "flag", or "row" where it's kept only for its row), and counted as
    added, with an Undo
  - the same record sent again by its source, saying something else of its range and flag (#869: its row and its
    result's row here one record of one source, #885: the same Observation by its id, or the same report; never by a
    key scoped to a day nor a hash of the result's content, which another source's copy has too), isn't a copy: it's
    updated in place ("updates"), its range and flag and its row as the source gives them now
    (update(), with what each was kept, so undoing the import puts them back). A re-pull or a re-sent FHIR Observation
    with its range corrected added a copy, one result shown twice beside a drawer row reading the other's flag

  dups = find(db, pid, fmt, data)
  report(data, dups) -> {"counts", "samples", "range", "duplicates": {...}, "total"}   for the page
  trim(data, dups, keep) -> the data to save                               keep: refs of duplicates to save anyway
  update(db, pid, iid, data, dups) -> how many results here it updated, once import iid is saved
"""
import collections, copy, json, re

from . import crossmatch, ranges, records
from .importers import common

ROWS = records.ROWS
ITEMS_SHOWN = 500   # duplicates listed for the page, at most
SAMPLES = 3         # what's new shown on the review screen, of each kind (#282)


def _canon(x):
    return json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _body(e):
    return _canon({k: v for k, v in e.items() if k != "id"})


def _point(p):
    """A result point's identity: its time and the value as reported (else the number)."""
    if isinstance(p, list) and p:
        shown = p[5] if len(p) > 5 and p[5] not in (None, "") else p[1] if len(p) > 1 else None
        return _canon([str(p[0]), str(shown)])
    return _canon(p)


def _text(t):
    return _canon(t[:2]) if isinstance(t, list) else _canon(t)


def _readings(t, shown, x=None):
    """A result's value as read (#874): {(its time, its comparator, its number)}, for a text result to match a point it
    reads as. Its number as imported (x, a point's), else each reading of its value as reported (shown) by the one
    reader of a number, common.value_number, whichever decimal mark its file writes ("1,250" is 1250 or 1.25, "0,500"
    0.5, "=<5" "<=" 5); none for a value that isn't a number ("Negative"). A text result here and a point it reads as
    are one result: the same file read again, its reading changed between text and a number (#870), added it again."""
    cmp = common.comparator(shown)
    xs = {x} if crossmatch._num(x) is not None else {common.value_number(shown, dc) for dc in (None, True, False)}
    return {(str(t), cmp, n) for n in xs if n is not None}


def _point_read(p):
    """_readings of a point [time, number, low, high, flag, value as reported...]."""
    return _readings(p[0], p[5] if len(p) > 5 else "", p[1] if len(p) > 1 else None) if isinstance(p, list) and p else set()


def _text_read(t):
    """_readings of a text result [time, value as reported]."""
    return _readings(t[0], t[1]) if isinstance(t, list) and len(t) > 1 else set()


def _entry(x):
    return "k:" + x["key"] if isinstance(x, dict) and isinstance(x.get("key"), str) and x["key"].strip() else "c:" + _canon(x)


def _labdays(data):
    """{lab day event id: [its result event ids]} (details type "labday")."""
    out = {}
    for k, d in (data.get("details") or {}).items():
        if isinstance(d, dict) and d.get("type") == "labday" and isinstance(d.get("ids"), list):
            out[str(k)] = [str(i) for i in d["ids"]]
    return out


def _meta_lists(data):
    """The profile-wide lists in data: {path: list}, path "problems" or "outOfPocket.payments". Not the parts an
    export's data.json has that are never stored with an import (records.SERVED: its journal, corrections... which
    export.restore puts back, #589)."""
    out = {}
    for k, v in data.items():
        if k in ROWS or k == "watch" or k in records.SERVED:
            continue
        if isinstance(v, list):
            out[k] = v
        elif isinstance(v, dict):
            out.update({f"{k}.{k2}": v2 for k2, v2 in v.items() if isinstance(v2, list)})
    return out


def _brings(here, p):
    """What point p, a copy of a result already here, says of its range and flag that none of its copies here (here:
    their points) says (crossmatch.said), or None: "range" where it gives a range and each of them has none (read as one
    with it, ranges.copies, it would have that range) or another (it's another result, its report's range not theirs,
    ranges.agree, #847); else "flag" where read as one with them it would change their flag."""
    if not isinstance(p, list):
        return None
    mine, theirs = crossmatch.said(p), [crossmatch.said(o) for o in here]
    if any(ranges.agree(o, mine) and ranges.copies(o, mine) == o for o in theirs):
        return None
    ranged = lambda x: x[:2] != (None, None)
    return "range" if ranged(mine) and all(not ranged(o) or not ranges.agree(o, mine) for o in theirs) else "flag"


def _drawer(data):
    """The upload's drawer rows (crossmatch._rows), its events' ids as text (an importer's may be numbers)."""
    details = data.get("details") if isinstance(data.get("details"), dict) else {}
    return crossmatch._rows({"events": [e for e in data.get("events") or [] if isinstance(e, dict) and "id" in e],
                             "details": {str(k): d for k, d in details.items()}})


def find(db, pid, fmt, data, whole=None):
    """The duplicates in data: {"events": {event id}, "points": {series index: {point index}}, "txt": {series index:
    {index}}, "series": {series index} (wholly known, nothing in it), "meta": {path: {index}}}; "copies": {series
    index: {point index}}, its results already here that are kept with it, not duplicates (#846), each with its own row
    (#868: "at", {series index: {point index: (event id, row index)}} in the upload) and what it brings ("brings":
    {series index: {point index: "range" | "flag" | "row"}}); and "updates" (#869: _same_records), {series index: {point
    index: [{"result", "event", "row", "at"}]}}, its results already here that their source sends again, each the result
    here it updates (its id), that result's row here (its event's id and the row's index) and its own in the upload;
    "known" (#879), {labitem id: {row index}}, the upload's rows that are records here (the same Observation, whichever
    bundle it came in). whole: the records the upload's rows are in, where they aren't its own (a sync's: the whole set
    read so far, a lab day grouping results across pulls)."""
    events = [e for e in data.get("events") or [] if isinstance(e, dict)]
    keyed = {str(e["id"]): e["key"] for e in events if isinstance(e.get("key"), str) and e["key"].strip()}
    hashes = records.key_hashes(db, keyed.values())
    known = records.known_event_keys(db, pid, hashes.values())
    dup = {i for i, k in keyed.items() if hashes[k] in known}

    labs = [s for s in data.get("labs") or []]
    keyless = [e for e in events if str(e["id"]) not in keyed]
    keyless_labs = [s for s in labs if isinstance(s, dict) and not (isinstance(s.get("key"), str) and s["key"].strip())]
    old_events, old_series = records.keyless(db, pid, fmt) if keyless or keyless_labs else ([], [])
    if keyless:
        seen = {_body(b) for b in old_events if isinstance(b, dict)}
        dup |= {str(e["id"]) for e in keyless if _body(e) in seen}

    s_hashes = records.key_hashes(db, [s.get("key") for s in labs if isinstance(s, dict)])
    s_known = records.known_series(db, pid, s_hashes.values())
    by_name = collections.defaultdict(list)
    for s in old_series:
        by_name[(str(s.get("name")), str(s.get("unit")))].append(s)
    points, txt, empty, copies, brings, olds = {}, {}, set(), {}, {}, {}
    for i, s in enumerate(labs):
        if not isinstance(s, dict):
            continue
        h = s_hashes.get(s.get("key"))
        if h:
            if h not in s_known:
                continue
            old_pts, old_txt = records.series_points(db, pid, s_known[h])
            olds[i] = s_known[h]
        else:
            same = by_name.get((str(s.get("name")), str(s.get("unit"))))
            if not same:
                continue
            old_pts = [p for o in same for p in o.get("pts") or []]
            old_txt = [t for o in same for t in o.get("txt") or []]
        have_p, have_t = collections.defaultdict(list), {_text(t) for t in old_txt}
        for p in old_pts:   # each result here, its copies
            have_p[_point(p)].append(p if isinstance(p, list) else [])
        here = {n for n, p in enumerate(s.get("pts") or []) if _point(p) in have_p}
        said = {n: what for n in here if (what := _brings(have_p[_point(s["pts"][n])], s["pts"][n]))}
        copies[i], brings[i] = set(said), said   # (#846) kept with what it says
        # (#874) a text result here that a point reads as is that result (its reading changed, #870), and a text
        # result a point here reads as
        read_t = set().union(*map(_text_read, old_txt)) if old_txt else set()
        read_p = set().union(*map(_point_read, old_pts)) if s.get("txt") else set()
        points[i] = (here - copies[i]) | {n for n, p in enumerate(s.get("pts") or []) if n not in here and old_txt
                                          and (_point(p) in have_t or _point_read(p) & read_t)}
        if i in olds:   # (#869) those saying something else of their range and flag than a copy here: sent again?
            olds[i] = (olds[i], here, {n for n in here if any(crossmatch.said(o) != crossmatch.said(s["pts"][n]) for o in have_p[_point(s["pts"][n])])})
        txt[i] = {n for n, t in enumerate(s.get("txt") or []) if _text(t) in have_t or _text(t) in have_p or _text_read(t) & read_p}
        if not (s.get("pts") or s.get("txt")):
            empty.add(i)

    old_meta = _meta_lists(records.metas(db, pid))
    meta = {}
    for path, items in _meta_lists(data).items():
        have = {_entry(x) for x in old_meta.get(path) or []}
        meta[path] = {n for n, x in enumerate(items) if _entry(x) in have}

    dups = {"events": dup, "points": points, "txt": txt, "series": empty, "meta": meta, "copies": copies, "brings": brings}
    _same_records(db, pid, data, dups, {i: o for i, o in olds.items() if isinstance(o, tuple) and o[1]}, whole)
    _settle(data, dups, set())
    return dups


# a source key scoped to a day (docs/importers.md, "Source keys": "<NAME>:labitem:<YYYY-MM-DD>", with "|<org>" or a
# panel after it, and "<NAME>:labday:<YYYY-MM-DD>"), not one record's: every source's results of that day have it (#885)
DAY_KEY = re.compile(r"[^:|]+:lab(?:item|day):\d{4}-\d{2}-\d{2}(?:\||$)")


def day_key(key):
    """Whether a source key is scoped to a day (DAY_KEY): a FHIR file's results on their own, a CareSpace day's panel."""
    return isinstance(key, str) and bool(DAY_KEY.match(key))


def _record_key(key):
    """Whether a source key names one record of its source (a report's id, a document's, an Observation's:
    docs/importers.md, "Source keys"): never a hash of the result's content (":h:"), which every copy of it has (#869),
    nor a key scoped to a day (day_key), which another source's results of that day have too (#885)."""
    return isinstance(key, str) and bool(key.strip()) and ":h:" not in key and not day_key(key)


def _row_record(event, detail, r):
    """(#885) Row r of a labitem as the record it is: (the labitem's key, the row's own record's key or None). A row's
    own is in its details' "keys" (the FHIR importer's: each row's Observation, its labitem a day's or a report's)."""
    own = detail.get("keys") if isinstance(detail, dict) else None
    own = own[r] if isinstance(own, list) and r < len(own) and _record_key(own[r]) else None
    return (event or {}).get("key"), own


def _same_record(a, b):
    """(#885) Whether two rows (_row_record) are one record of one source: each with its own record's key, the same
    one; else the same labitem key where it names one record (a report's; a row kept before rows had keys of their
    own). Never by a key scoped to a day, which another source's copy of a result at that time has too: a hospital's
    Potassium (3.5–5.1, High), then a health plan's, another Observation with no range, was taken for the hospital's
    amended and wiped out its range and flag."""
    if a[1] and b[1]:
        return a[1] == b[1]
    return _record_key(a[0]) and a[0] == b[0]


def _same_records(db, pid, data, dups, olds, whole=None):
    """(#869) A result here sent again by its source saying something else of its range and flag (the source amended
    it) is the same result: updated in place ("updates"), never added as a copy, nor left out a duplicate. It's the same
    record where its row in the upload (in whole's lab days, where given) and the row here of the result here (its
    series' key, time and value as reported the same) are one record of one source (_same_record, #885: the same
    Observation by its own id, or the same report; never a key scoped to a day, which another source's results of that
    day have too, nor a hash of the result's content, which a typed or spreadsheet result's copy has too, #868). A
    re-pull of an Observation whose range its source corrected (13.5–17.5 and Low, then 12.0–15.5) added a copy beside
    the labitem updated in place: two results, and the lab day's row read the old one's Low beside the new range.

    (#879) An Observation is found by its id whichever bundle it comes in: its row here is looked for in the labitems
    of its result's import (records.labitems_of) where none with the upload's labitem key holds it, as one that came in
    a report and comes alone now (or the other way round) is under another key (a report's, its day's). One saying what
    it says here is a duplicate, its row a record here ("known": {labitem id: {row index}}, which _settle keeps no
    labitem for): it was kept as a copy with a lab day of its own beside the report's, or, amended, as another result.
    olds: {series index: (its series here, the points here at their time and value, those saying something else than
    a copy here)}; those of the second asked about where their row, its own record's (an Observation's), is in a
    labitem new to the profile (an upload's)."""
    dups["updates"], known = {}, dups.setdefault("known", {})
    if not olds:
        return
    src = whole or data
    labs, drawer = data.get("labs") or [], _drawer(src)
    events = {str(e["id"]): e for e in src.get("events") or [] if isinstance(e, dict) and "id" in e}
    details = {str(k): d for k, d in (src.get("details") or {}).items()}
    want = {}
    for i, (_, here, other) in olds.items():
        for n in here:
            row = _row(drawer, labs[i], labs[i]["pts"][n])
            rec = row and _row_record(events.get(row[0]), details.get(row[0]), row[1])
            if rec and (rec[1] or _record_key(rec[0])) and (n in other or not whole and rec[1] and row[0] not in dups["events"]):
                want[(i, n)] = (row, rec)
    if not want:
        return
    by = collections.defaultdict(list)   # (series id, point) -> [(result id, its series as the records have it, point)]
    for rid, sid, imp, body, q in records.series_results(db, pid, {x for i, _ in want for x in olds[i][0]}):
        if isinstance(q, list) and q:
            by[(sid, _point(q))].append((rid, {**body, "imp": imp}, q))
    results = {(i, n): [r for sid in olds[i][0] for r in by[(sid, _point(labs[i]["pts"][n]))]] for i, n in want}
    here = records.events_with_keys(db, pid, {rec[0] for _, rec in want.values()})
    rows = crossmatch._rows({"events": here, "details": {str(e["id"]): e["detail"] for e in here}})
    away = {s["imp"] for (i, n), (_, rec) in want.items() if rec[1] for _, s, q in results[(i, n)] if not crossmatch._row_of(rows, s, q)}
    if away:   # (#879) a row here under another labitem key than the upload's: in its result's import
        had = {e["id"] for e in here}
        here += [e for e in records.labitems_of(db, pid, away) if e["id"] not in had]
        rows = crossmatch._rows({"events": here, "details": {str(e["id"]): e["detail"] for e in here}})
    keyed = {e["id"]: e for e in here}
    for (i, n), (row, rec) in want.items():
        p, found = labs[i]["pts"][n], []
        for rid, s, q in results[(i, n)]:
            at = crossmatch._row_of(rows, s, q)
            if at and _same_record(rec, _row_record(keyed[at[0]], keyed[at[0]].get("detail"), at[1])):
                found.append(({"result": rid, "event": at[0], "row": at[1], "at": None if whole else row}, q))
        if not found:
            continue
        dups["copies"].get(i, set()).discard(n)
        dups["brings"].get(i, {}).pop(n, None)
        if not whole:   # its row a record here: no labitem kept for it
            known.setdefault(row[0], set()).add(row[1])
        if any(crossmatch.said(q) != crossmatch.said(p) for _, q in found):
            dups["points"].get(i, set()).discard(n)
            dups["updates"].setdefault(i, {})[n] = [u for u, _ in found]
        else:   # (#879) the same record saying the same: a duplicate
            dups["points"].setdefault(i, set()).add(n)


def _row(drawer, s, p):
    """Point p's row in the upload's lab days (crossmatch._row_of: (event id as text, row index)), or None."""
    at = crossmatch._row_of(drawer, s, p) if isinstance(p, list) and p else None
    return (str(at[0]), at[1]) if at else None


def _settle(data, dups, kept, anyway=None):
    """What's kept of the results already here, and the rows and lab days that go with them (#868). Each copy comes
    with its own row: the row's labitem is kept with it (its key here or not, so a typed row, keyed by its test, time
    and value, is kept with the range it brings), and its lab day with that; a copy the upload gives no row is left
    out, a duplicate, so no point is kept without its lab day (one was, its row left out by key: Type them in's "won't
    be saved again" saved it, and util.js labDraws put it in every lab day of its date); a vital's is kept, as no vital
    has a row (_vital, #880). anyway: {series index: {point index}}, results here kept anyway (keep()), whose rows are
    kept too, none left out. Then the results new to the profile with their rows (_new_with_rows, #878), the lab days
    (_settle_labdays, kept: events kept anyway), and the rows kept bring their results here as copies (_with_rows).
    A labitem the profile has already (a duplicate) is kept with those rows only (#887: "rows", {labitem id: {row
    index}}, trim() leaving out the rest): ticking one result of a report uploaded again added its others too, each
    kept for its row (_with_rows), and a second lab day of all of them."""
    labs, drawer = data.get("labs") or [], _drawer(data)
    at = dups["at"] = {}
    rows = dups.setdefault("rows", {})
    dups.setdefault("copies", {}), dups.setdefault("brings", {})
    # (#879) a labitem new to the profile keeps no row that's a record here (the same Observation in another bundle,
    # _same_records' "known"): holding nothing else, it's a duplicate
    held = {str(k): d for k, d in (data.get("details") or {}).items()}
    for eid, rs in (dups.get("known") or {}).items():
        if eid not in kept and eid not in dups["events"] and eid not in rows:
            left = set(range(len((held.get(eid) or {}).get("rows") or []))) - rs
            if left:
                rows[eid] = left
            else:
                dups["events"].add(eid)
    # (#869) a labitem whose rows are updated in place isn't added again for a copy's row: that copy is left out
    updated = {u["at"][0] for us in (dups.get("updates") or {}).values() for ul in us.values() for u in ul if u["at"]}
    for i, ns in list(dups["copies"].items()) + list((anyway or {}).items()):
        for n in sorted(ns):
            row = _row(drawer, labs[i], labs[i]["pts"][n])
            if row and row[0] not in updated:
                at.setdefault(i, {})[n] = row
                if row[0] in dups["events"] or row[0] in rows:   # (#887) a duplicate, kept for this row
                    rows.setdefault(row[0], set()).add(row[1])
                dups["events"].discard(row[0])   # its row with it
            elif not _vital(labs[i]):   # no row of its own here: left out (a vital has none, #880: kept)
                leave_out(dups, i, n)
    _new_with_rows(data, dups, drawer)
    _settle_labdays(data, dups, kept)
    _with_rows(data, dups, drawer)


def _new_with_rows(data, dups, drawer):
    """(#878) A result new to the profile, a text result too, is kept with its row, so none is saved without one: where
    its labitem is a duplicate by key (a report sent again with a result added; another source's results of that day,
    under the same day's key; a duplicate's text result ticked to add anyway, #901), it's kept with that row (_settle's
    "rows", #887), and its lab day with it. They were saved with no row and no lab day: the Timeline's badge counted
    them in every lab day of their date (util.js labDraws) while no drawer listed them."""
    labs, rows = data.get("labs") or [], dups["rows"]
    for i, s in enumerate(labs):
        if not isinstance(s, dict):
            continue
        here = (dups["points"].get(i, set()) | dups["copies"].get(i, set()) | set((dups.get("updates") or {}).get(i, ())),
                dups["txt"].get(i, set()))   # (a copy's row is _settle's to keep or leave out, an update's is here)
        for field, drop in zip(("pts", "txt"), here):
            for n, p in enumerate(s.get(field) or []):
                row = n not in drop and _row(drawer, s, p)
                if row and (row[0] in dups["events"] or row[0] in rows):
                    rows.setdefault(row[0], set()).add(row[1])
                    dups["events"].discard(row[0])


def _vital(s):
    """(#880) Whether series s is a visit's Vitals (a heart rate, a blood pressure): readings no lab day lists, with no
    row of their own (records.py, the page's labDraws, and crossmatch.lab_faults leave them out alike). A copy of one
    already here is kept with what it brings without a row: left out, a portal's heart rate with its range and High
    beside a hospital's with none left the reading with neither."""
    return isinstance(s, dict) and s.get("panel") == "Vitals"


def leave_out(dups, i, n):
    """Series i's point n, a copy of a result already here, left out after all: a duplicate (a sync's, #868)."""
    if n in dups["copies"].get(i, ()):
        dups["copies"][i].discard(n)
        dups["brings"].get(i, {}).pop(n, None)
        dups["at"].get(i, {}).pop(n, None)
        dups["points"].setdefault(i, set()).add(n)


def _with_rows(data, dups, drawer):
    """(#846) A result already here whose row in the upload's lab day is kept (the row's result new to the profile:
    another file's report of it) is kept with it, a copy ("brings" "row"), so the row reads its own result's flag as
    every row does (#782, drawer.js rowPoint; the row matched as crossmatch._row_of matches it): left out, the lab day of
    a spreadsheet with no range said "Out of range: None" beside a C-CDA's copies with theirs and High. A row its
    labitem isn't kept with (#887: _settle's "rows") isn't one."""
    labs, rows = data.get("labs") or [], dups.get("rows") or {}
    for i, drop in dups["points"].items():
        for n in sorted(drop):
            row = _row(drawer, labs[i], labs[i]["pts"][n])
            if row and row[0] not in dups["events"] and row[1] in rows.get(row[0], (row[1],)):
                drop.discard(n)
                dups["copies"].setdefault(i, set()).add(n)
                dups["brings"].setdefault(i, {})[n] = "row"
                dups["at"].setdefault(i, {})[n] = row


def _settle_labdays(data, dups, kept):
    """A lab day goes with its results: kept while any result is kept, dropped when all are dropped."""
    for day, ids in _labdays(data).items():
        if not ids:
            continue
        if all(i in dups["events"] for i in ids):
            if day not in kept:
                dups["events"].add(day)
        else:
            dups["events"].discard(day)


def keep(data, dups, refs):
    """dups less the duplicates chosen to save anyway: refs "e:<event id>" and "s:<series index>"."""
    dups = copy.deepcopy(dups)
    refs = {str(r) for r in refs or ()}
    days = _labdays(data)
    kept = {r[2:] for r in refs if r.startswith("e:")}
    for day, ids in days.items():
        if day in kept:
            kept.update(ids)                 # a lab day kept keeps its results
        elif kept & set(ids):
            kept.add(day)                    # a result kept keeps its day
    dups["events"] -= kept
    for e in kept:   # (#887) kept whole, every row
        dups.get("rows", {}).pop(e, None)
    anyway = {}
    for r in refs:
        if r.startswith("s:") and r[2:].isdigit():
            i = int(r[2:])
            anyway[i] = dups["points"].pop(i, set())
            dups["txt"].pop(i, None)
            dups["series"].discard(i)
    # a result kept anyway keeps its row and lab day (#868), and an event kept its rows' results already here, as
    # copies (#846)
    _settle(data, dups, kept, anyway)
    return dups


def trim(data, dups):
    """A copy of data without the duplicates: a labitem kept for some of its rows only (#887, find()'s "rows") with
    those, its abn their flagged rows, and a lab day kept with less than it had said for what it holds (#889,
    common.lab_day_counted: "1 lab result · Potassium"), not the 4 of the report it was and their tests."""
    skip, part = dups["events"], dups.get("rows") or {}
    out = {k: copy.deepcopy(v) for k, v in data.items() if k not in ROWS}
    out["events"] = [copy.deepcopy(e) for e in data.get("events") or [] if not (isinstance(e, dict) and str(e.get("id")) in skip)]
    details, less = {}, set()
    for k, d in (data.get("details") or {}).items():
        if str(k) in skip:
            continue
        d = copy.deepcopy(d)
        if isinstance(d, dict) and d.get("type") == "labday" and isinstance(d.get("ids"), list):
            d["ids"] = [i for i in d["ids"] if str(i) not in skip]
            if len(d["ids"]) < len(data["details"][k]["ids"]) or any(str(i) in part for i in d["ids"]):
                less.add(str(k))
        elif str(k) in part and isinstance(d, dict):   # (#887) its rows of what's kept
            d.update({f: [x for r, x in enumerate(d[f]) if r in part[str(k)]] for f in ("rows", "keys") if isinstance(d.get(f), list)})
        details[k] = d
    out["details"] = details
    held = {str(k): d for k, d in details.items()}
    bodies = {str(e.get("id")): e for e in out["events"] if isinstance(e, dict)}
    for i in part:   # (#887) a labitem kept for some of its rows: its abn their flagged ones (#889)
        if i in bodies and isinstance(held.get(i), dict) and isinstance(held[i].get("rows"), list):
            n = sum(1 for r in held[i]["rows"] if isinstance(r, list) and len(r) > 4 and r[4])
            bodies[i].pop("abn", None)
            bodies[i].update({"abn": n} if n else {})
    common.lab_days_counted(out["events"], held, less)
    out["costs"] = [copy.deepcopy(c) for c in data.get("costs") or []
                    if not (isinstance(c, dict) and c.get("id") is not None and str(c["id"]) in skip)]
    labs = []
    for i, s in enumerate(data.get("labs") or []):
        if not isinstance(s, dict):
            labs.append(s)
            continue
        if i in dups["series"]:
            continue
        drop_p, drop_t = dups["points"].get(i, set()) | set((dups.get("updates") or {}).get(i, ())), dups["txt"].get(i, set())   # (updated in place, #869)
        pts = [copy.deepcopy(p) for n, p in enumerate(s.get("pts") or []) if n not in drop_p]
        txt = [copy.deepcopy(t) for n, t in enumerate(s.get("txt") or []) if n not in drop_t]
        if (s.get("pts") or s.get("txt")) and not (pts or txt):
            continue   # every result in it is there already
        s = {**copy.deepcopy(s), "pts": pts, "n": len(pts)}
        if "txt" in s or txt:
            s["txt"] = txt
        labs.append(s)
    out["labs"] = labs
    for path, drop in dups["meta"].items():
        if not drop:
            continue
        top, _, sub = path.partition(".")
        holder = out.get(top) if sub else out
        name = sub or top
        if isinstance(holder, dict) and isinstance(holder.get(name), list):
            holder[name] = [x for n, x in enumerate(holder[name]) if n not in drop]
    return out


def adds_anything(trimmed):
    """Whether saving trimmed would add any records (events, results, or entries of the profile-wide lists)."""
    if trimmed["events"] or trimmed["costs"] or any(isinstance(s, dict) for s in trimmed["labs"]):
        return True
    return any(_meta_lists(trimmed).values())


def documents(data):
    return sum(1 for d in (data.get("details") or {}).values()
               if isinstance(d, dict) and d.get("type") == "file" and d.get("url"))


def _latest(rows, n=SAMPLES):
    return sorted(rows, key=lambda r: r["t"], reverse=True)[:n]


def _sample_value(p):
    """A result point's value as the review's samples say it: as reported, else its number where the value as reported
    has more in it than its number (its unit: "82 mm[Hg]"), as it reads by the one reader of a number ("4,5", "<5",
    "1,200", "=<5" and "1.2E-3" are their numbers, #874: PLAIN, a reader of its own, said "=<5" as "5")."""
    shown = str(p[5]) if len(p) > 5 and p[5] not in (None, "") else ""
    x = crossmatch._num(p[1]) if len(p) > 1 else None
    if x is not None and (not shown or re.search(r"\d", shown) and not any(r[2] == x for r in _readings(p[0], shown))):
        return f"{x:g}"
    return shown


def samples(new):
    """A few of what's new for the review screen (#282), latest first, SAMPLES of each: {"events": {kind: [{"t",
    "title"}]} (visible events), "results": [{"t", "name", "value", "unit"}] (the value as reported), "allergies",
    "problems": [{"t", "title"}], "upcoming": [{"t", "title"}]}."""
    by_kind = collections.defaultdict(list)
    for e in new.get("events") or []:
        if isinstance(e, dict) and not e.get("hidden"):
            by_kind[str(e.get("k"))].append({"t": str(e.get("t") or "")[:10], "title": str(e.get("title") or "")})
    results = []
    for s in new.get("labs") or []:
        if not isinstance(s, dict):
            continue
        for p in s.get("pts") or []:
            if isinstance(p, list) and p:   # (its value said once it's among the few shown: _sample_value)
                results.append({"t": str(p[0])[:10], "name": str(s.get("name") or ""), "value": p, "unit": str(s.get("unit") or "")})
        for t in s.get("txt") or []:
            if isinstance(t, list) and len(t) > 1:
                results.append({"t": str(t[0])[:10], "name": str(s.get("name") or ""), "value": str(t[1]), "unit": ""})
    probs = [p for p in new.get("problems") or [] if isinstance(p, dict)]
    listed = lambda rows: _latest([{"t": str(p.get("date") or "")[:10], "title": str(p.get("name") or "")} for p in rows])
    return {"events": {k: _latest(v) for k, v in sorted(by_kind.items())},
            "results": [{**r, "value": _sample_value(r["value"]) if isinstance(r["value"], list) else r["value"]} for r in _latest(results)],
            "allergies": listed([p for p in probs if p.get("type") == "Allergy"]),
            "problems": listed([p for p in probs if p.get("type") != "Allergy"]),
            "upcoming": _latest([{"t": str(u.get("t") or "")[:10], "title": str(u.get("title") or "")}
                                 for u in new.get("upcoming") or [] if isinstance(u, dict)])}


def report(data, dups, summarise):
    """For the page: what's new (counts by kind, as jobs.summarise counts them, and a few of each: samples), the date
    range the upload covers, and the duplicates (counted, and listed with a ref to keep one anyway; an entry of the
    profile-wide lists, an allergy or a problem, with none), and how many results already here it adds as copies with
    its own report of them (#846: "copies", counted in what's new too)."""
    new = trim(data, dups)
    whole = summarise(data)
    out = summarise(new)
    out["counts"]["documents"] = documents(new)
    out["samples"] = samples(new)
    out["range"] = whole["range"]
    events = {str(e.get("id")): e for e in data.get("events") or [] if isinstance(e, dict)}
    kinds = collections.Counter(str(events[i].get("k")) for i in dups["events"] if i in events and not events[i].get("hidden"))
    items = [{"ref": "e:" + i, "kind": str(e.get("k")), "t": str(e.get("t") or "")[:10], "title": str(e.get("title") or "")}
             for i, e in events.items() if i in dups["events"] and not e.get("hidden")]
    results = 0
    for i, s in enumerate(data.get("labs") or []):
        if not isinstance(s, dict):
            continue
        n = len(dups["points"].get(i, ())) + len(dups["txt"].get(i, ()))
        if n:
            results += len(dups["points"].get(i, ()))
            # the days its duplicates are from (#490), text results' too: the first, and the last where it's another
            days = sorted(str(x[0])[:10] for key, rows in (("points", s.get("pts")), ("txt", s.get("txt")))
                          for k, x in enumerate(rows or []) if k in dups[key].get(i, ()) and isinstance(x, list) and x and x[0])
            item = {"ref": f"s:{i}", "kind": "results", "t": days[0] if days else "", "title": str(s.get("name") or ""), "n": n}
            if days and days[-1] != days[0]:
                item["to"] = days[-1]
            items.append(item)
    # the profile-wide lists' entries there already (#497), listed too, with no ref: an entry isn't added twice
    lists = _meta_lists(data)
    for path, drop in dups["meta"].items():
        for n in sorted(drop):
            x = lists.get(path, [])[n] if n < len(lists.get(path, [])) else None
            if isinstance(x, dict):
                kind = ("allergy" if x.get("type") == "Allergy" else "problem") if path == "problems" else path
                items.append({"ref": None, "kind": kind, "t": str(x.get("date") or x.get("t") or "")[:10],
                              "title": str(x.get("name") or x.get("title") or "")})
    items.sort(key=lambda x: (x["t"], x["title"]))
    out["duplicates"] = {"events": sum(kinds.values()), "kinds": dict(sorted(kinds.items())), "results": results,
                         "other": sum(len(v) for v in dups["meta"].values()),
                         "items": items[:ITEMS_SHOWN], "more": max(0, len(items) - ITEMS_SHOWN),
                         # (#846) results already here, added with what this upload's report says of them, which the review says,
                         # and what each brings (#868: its report's range, its flag, or its own lab day's row)
                         "copies": sum(len(v) for v in (dups.get("copies") or {}).values()),
                         "brings": dict(sorted(collections.Counter(w for v in (dups.get("brings") or {}).values() for w in v.values()).items())),
                         # (#869) results here that their source sends again, updated in place with what it says now
                         "updates": updates(dups)}
    out["total"] = whole["counts"]
    out["new"] = adds_anything(new) or bool(updates(dups))
    return out


def updates(dups):
    """How many of the upload's results update a result here in place (#869: find()'s "updates")."""
    return sum(len(v) for v in (dups.get("updates") or {}).values())


def update(db, pid, iid, data, dups, rows=True):
    """Save find()'s updates (#869), once import iid is saved: each result here that its source sent again takes the
    point it sends now (its range, flag and edge), and its drawer row the row it sends now (rows: false where the row's
    labitem is updated in place whole, as a sync's are), with what each was kept (sync_changes, by the row's event: an
    earlier change of that event in this import gets them added) so undoing import iid puts them back (sync.unapply).
    -> how many of data's results updated one here"""
    labs = data.get("labs") or []
    details = {str(k): d for k, d in (data.get("details") or {}).items()}
    by_event, n = collections.defaultdict(list), 0
    for i, us in (dups.get("updates") or {}).items():
        for k, ul in us.items():
            n += 1
            for u in ul:
                by_event[u["event"]].append((labs[i]["pts"][k], u))
    for eid, items in by_event.items():
        r = db.execute("SELECT body, detail, cost FROM events WHERE id=? AND profile_id=?", (eid, pid)).fetchone()
        if r is None:
            continue
        detail = records._dec(r["detail"])
        after, before = copy.deepcopy(detail), []
        for p, u in items:
            old = db.execute("SELECT body FROM results WHERE id=? AND profile_id=?", (u["result"], pid)).fetchone()
            if old is None:
                continue
            before.append([u["result"], records._dec(old["body"])])
            db.execute("UPDATE results SET body=? WHERE id=?", (records._enc(p), u["result"]))
            sent = (details.get(u["at"][0]) or {}).get("rows") if rows and u["at"] else None
            if sent and u["at"][1] < len(sent) and isinstance(after, dict) and u["row"] < len(after.get("rows") or []):
                after["rows"][u["row"]] = copy.deepcopy(sent[u["at"][1]])
        if not before:
            continue
        had = db.execute("SELECT id, previous FROM sync_changes WHERE import_id=? AND event_id=?", (iid, eid)).fetchone()
        if had:
            prev = records._dec(had["previous"])
            db.execute("UPDATE sync_changes SET previous=? WHERE id=?", (records._enc({**prev, "results": (prev.get("results") or []) + before}), had["id"]))
        else:
            db.execute("INSERT INTO sync_changes(import_id, event_id, previous) VALUES(?, ?, ?)",
                       (iid, eid, records._enc({"body": records._dec(r["body"]), "detail": detail,
                                                "cost": records._dec(r["cost"]) if r["cost"] is not None else None, "results": before})))
        if after != detail:
            db.execute("UPDATE events SET detail=? WHERE id=?", (records._enc(after), eid))
    records._bump(db, pid)
    return n
