"""What an import would add, before it's saved (#22): duplicates of the profile's records, found and dropped.

A parsed record is a duplicate when the profile already has one with the same source key (docs/importers.md,
"Source keys"), found by its key_hash (records.py) without decrypting the rows. A record without a key falls back
to the same importer, date, kind and content: the bodies of keyless records that earlier imports of the same format
brought are compared as they are (importers give keys nowadays, so these are few).

  events      the event's key, else its body (date, kind, title...) without the id
  lab results a point is its series' key (or, without one, the series name and unit) and its time and value as
              reported; a series adds only the points the profile doesn't have
  the rest    entries of the profile-wide lists (problems, upcoming, clinicWeights, milestones, outOfPocket.payments)
              already there, by key or by content

Duplicates are dropped, not replaced. A lab day is kept while any of its results is new (else they couldn't be
opened) and dropped when all of them are duplicates; keeping one anyway keeps its results, and keeping a result
keeps its day. jobs.py re-runs importers.check() on what's left before saving it.

  dups = find(db, pid, fmt, data)
  report(data, dups) -> {"counts", "samples", "range", "duplicates": {...}, "total"}   for the page
  trim(data, dups, keep) -> the data to save                               keep: refs of duplicates to save anyway
"""
import collections, copy, json, re

from . import records

ROWS = records.ROWS
ITEMS_SHOWN = 500   # duplicates listed for the page, at most
SAMPLES = 3         # what's new shown on the review screen, of each kind (#282)
PLAIN = re.compile(r"[<>≤≥]?=?\s*-?[\d.,]+|[^\d]+")   # a value as reported that's only a value: "<5", "1,200", "Negative"


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
    """The profile-wide lists in data: {path: list}, path "problems" or "outOfPocket.payments"."""
    out = {}
    for k, v in data.items():
        if k in ROWS or k == "watch":
            continue
        if isinstance(v, list):
            out[k] = v
        elif isinstance(v, dict):
            out.update({f"{k}.{k2}": v2 for k2, v2 in v.items() if isinstance(v2, list)})
    return out


def find(db, pid, fmt, data):
    """The duplicates in data: {"events": {event id}, "points": {series index: {point index}}, "txt": {series index:
    {index}}, "series": {series index} (wholly known, nothing in it), "meta": {path: {index}}}."""
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
    points, txt, whole = {}, {}, set()
    for i, s in enumerate(labs):
        if not isinstance(s, dict):
            continue
        h = s_hashes.get(s.get("key"))
        if h:
            if h not in s_known:
                continue
            old_pts, old_txt = records.series_points(db, pid, s_known[h])
        else:
            same = by_name.get((str(s.get("name")), str(s.get("unit"))))
            if not same:
                continue
            old_pts = [p for o in same for p in o.get("pts") or []]
            old_txt = [t for o in same for t in o.get("txt") or []]
        have_p, have_t = {_point(p) for p in old_pts}, {_text(t) for t in old_txt}
        points[i] = {n for n, p in enumerate(s.get("pts") or []) if _point(p) in have_p}
        txt[i] = {n for n, t in enumerate(s.get("txt") or []) if _text(t) in have_t}
        if not (s.get("pts") or s.get("txt")):
            whole.add(i)

    old_meta = _meta_lists(records.metas(db, pid))
    meta = {}
    for path, items in _meta_lists(data).items():
        have = {_entry(x) for x in old_meta.get(path) or []}
        meta[path] = {n for n, x in enumerate(items) if _entry(x) in have}

    dups = {"events": dup, "points": points, "txt": txt, "series": whole, "meta": meta}
    _settle_labdays(data, dups, set())
    return dups


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
    for r in refs:
        if r.startswith("s:") and r[2:].isdigit():
            i = int(r[2:])
            dups["points"].pop(i, None)
            dups["txt"].pop(i, None)
            dups["series"].discard(i)
    _settle_labdays(data, dups, kept)
    return dups


def trim(data, dups):
    """A copy of data without the duplicates."""
    skip = dups["events"]
    out = {k: copy.deepcopy(v) for k, v in data.items() if k not in ROWS}
    out["events"] = [copy.deepcopy(e) for e in data.get("events") or [] if not (isinstance(e, dict) and str(e.get("id")) in skip)]
    details = {}
    for k, d in (data.get("details") or {}).items():
        if str(k) in skip:
            continue
        d = copy.deepcopy(d)
        if isinstance(d, dict) and d.get("type") == "labday" and isinstance(d.get("ids"), list):
            d["ids"] = [i for i in d["ids"] if str(i) not in skip]
        details[k] = d
    out["details"] = details
    out["costs"] = [copy.deepcopy(c) for c in data.get("costs") or []
                    if not (isinstance(c, dict) and c.get("id") is not None and str(c["id"]) in skip)]
    labs = []
    for i, s in enumerate(data.get("labs") or []):
        if not isinstance(s, dict):
            labs.append(s)
            continue
        if i in dups["series"]:
            continue
        drop_p, drop_t = dups["points"].get(i, set()), dups["txt"].get(i, set())
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
            if isinstance(p, list) and p:
                shown = str(p[5]) if len(p) > 5 and p[5] not in (None, "") else ""
                if not PLAIN.fullmatch(shown) and len(p) > 1 and isinstance(p[1], (int, float)):
                    shown = f"{p[1]:g}"   # the number, where the value as reported has more in it (its unit: "82 mm[Hg]")
                results.append({"t": str(p[0])[:10], "name": str(s.get("name") or ""), "value": str(shown), "unit": str(s.get("unit") or "")})
        for t in s.get("txt") or []:
            if isinstance(t, list) and len(t) > 1:
                results.append({"t": str(t[0])[:10], "name": str(s.get("name") or ""), "value": str(t[1]), "unit": ""})
    probs = [p for p in new.get("problems") or [] if isinstance(p, dict)]
    listed = lambda rows: _latest([{"t": str(p.get("date") or "")[:10], "title": str(p.get("name") or "")} for p in rows])
    return {"events": {k: _latest(v) for k, v in sorted(by_kind.items())}, "results": _latest(results),
            "allergies": listed([p for p in probs if p.get("type") == "Allergy"]),
            "problems": listed([p for p in probs if p.get("type") != "Allergy"]),
            "upcoming": _latest([{"t": str(u.get("t") or "")[:10], "title": str(u.get("title") or "")}
                                 for u in new.get("upcoming") or [] if isinstance(u, dict)])}


def report(data, dups, summarise):
    """For the page: what's new (counts by kind, as jobs.summarise counts them, and a few of each: samples), the date
    range the upload covers, and the duplicates (counted, and listed with a ref to keep one anyway; an entry of the
    profile-wide lists, an allergy or a problem, with none)."""
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
                         "items": items[:ITEMS_SHOWN], "more": max(0, len(items) - ITEMS_SHOWN)}
    out["total"] = whole["counts"]
    out["new"] = adds_anything(new)
    return out
