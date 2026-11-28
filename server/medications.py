"""The medication timeline (issue #50): each medication's records put side by side, as spans.

The records hold medications as events of two kinds. A "med" event is a line of a medication list or a prescription
(FHIR MedicationRequest/Statement, a C-CDA's medications, a portal's table, one typed in): its time is when it was
listed or started, and its detail may say how to take it (instructions), its status, when it ended (end), who
prescribed it (prescriber) and its RxNorm code (rxnorm). An "rx" event is a pharmacy fill from a claim. history()
groups both by medication and lays each one out as what the records say, in order:

  {"name": what it's shown as (the newest listing's title),
   "key": the grouping key (the drug's leading words, before any strength: "tacrolimus" for "Tacrolimus 1 mg
          capsule" and "TACROLIMUS 0.5MG CAP"),
   "rxnorm": [the RxNorm codes its listings carry],
   "spans": [{"from": YYYY-MM-DD, "to": YYYY-MM-DD | null, "open": the records give no end, "stopped": a listing
              says it was stopped (a status like Stopped, Completed or Discontinued, or an end date),
              "dose": the instructions, "title": the listing's title (its strength), "status", "prescriber",
              "sources": [where it came from], "ids": [its event ids], "rxnorm"}],
   "changes": [{"t": YYYY-MM-DD, "from": the listing before, "to": the listing after (each its title and
                instructions), "id": the event that says so}],
   "fills": [{"t", "id", "title", "pharmacy", "supply": days' supply when the claim gives it}],
   "start": the first span's from (or the first fill's day), "stop": the last span's to when it was stopped}

How listings become spans, as plainly as the records allow:
  - listings of the same medication on the same day with the same instructions are one (two sources saying it once)
  - each listing runs from its day to its own end date, or else to the next listing's day; the last one, without
    an end date and not stopped, is open (the records don't say it ended: the page draws it to today)
  - a listing that is stopped but gives no end date ends where it starts: the records say it stopped, not when
  - consecutive listings with the same instructions and title are one span (a medication listed again unchanged)
  - where the instructions or the title (the strength) differ from the span before, that's a dose change

A rename (edits.py) groups a record as it's shown: under its new name's key it joins a medication of that name, or
leaves one it shared a key with. What hangs on a medication's key goes with it (#954, follow(), in the rename's own
transaction): its reminders (med_reminders.py), its doses (med_log.py) and the side effects linked to it by name
(journal.py) go to the medication its newest record is in now (its newest listing, which names it, else its newest
fill), and undoing the rename puts back what it moved (back()). A source's rename is the same (#960): a listing or a
fill its source sends again under another name, updated in place (an upload's, preview.change; a pull's,
sync._update_in_place), takes what hangs on its medication with it in the import's own transaction, what it moved
kept with what the record was (sync_changes), so undoing the import puts each back (sync._unapply, history.undo). A
record the person renamed keeps their name, so it moves nothing.

Nothing here judges a dose, a gap or an overlap; the page shows the spans and the records behind each. Pharmacy
fills are matched to a medication by the same key and never stretch or start a span. Hidden events (edits.py) are
left out. records._assemble adds the result as "medications", only when the profile has medication records.
"""
import json, re

MED_KINDS = ("med", "rx")
STOPPED = {"stopped", "completed", "discontinued", "inactive", "ended", "cancelled", "canceled", "aborted",
           "entered in error", "not taken", "held", "on hold", "suspended"}
_STRENGTH = re.compile(r"[\d(\[]|\b(?:tab|tabs|tablet|tablets|cap|caps|capsule|capsules|oral|solution|injection|"
                       r"suspension|cream|ointment|patch|inhaler|er|xr|sr|dr|hcl|hydrochloride|sodium|calcium|"
                       r"potassium|mg|mcg|ml)\b", re.I)


def _day(t):
    t = str(t or "")
    return t[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", t) else None


def med_key(name):
    """The grouping key of a medication name: its leading words, lower case, before any strength or form."""
    name = " ".join(str(name or "").split())
    m = _STRENGTH.search(name)
    head = (name[:m.start()] if m else name).strip(" ,;:-/").lower()
    words = re.findall(r"[a-z][a-z'-]*", head)
    return " ".join(words) or " ".join(name.lower().split())


def hidden_test(data):
    """name -> whether it names a medication hidden by hand (#1093: data's "hiddenMeds"), by its grouping key or the
    name it was shown by: what's shared of a medication's name (the log, a journal entry's) leaves those out."""
    keys = {k for h in data.get("hiddenMeds") or [] if isinstance(h, dict)
            for k in (h.get("key"), med_key(h.get("name"))) if isinstance(k, str) and k}
    return lambda name: bool(keys) and isinstance(name, str) and bool(name.strip()) and med_key(name) in keys


def _text(v):
    return " ".join(str(v).split()) if isinstance(v, (str, int, float)) and str(v).strip() else ""


def _stopped(status):
    return status.lower() in STOPPED


def _supply(d, e):
    for k in ("daysSupply", "supply"):
        v = d.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            return v
        if isinstance(v, str) and v.strip().isdigit():
            return int(v)
    m = re.search(r"(\d+)-day supply", str(e.get("sub") or ""))
    return int(m.group(1)) if m else None


def history(data):
    """The profile's medications as spans, changes and fills (see above), sorted by name. data: the assembled records."""
    details, groups = data.get("details") or {}, {}
    for e in data.get("events") or []:
        if not isinstance(e, dict) or e.get("k") not in MED_KINDS or e.get("hidden"):
            continue
        day, title = _day(e.get("t")), _text(e.get("title"))
        if not day or not title:
            continue
        d = details.get(str(e.get("id")))
        d = d if isinstance(d, dict) else {}
        g = groups.setdefault(med_key(title), {"listings": [], "fills": []})
        if e["k"] == "rx":
            g["fills"].append({"t": day, "id": e.get("id"), "title": title,
                               "pharmacy": _text(d.get("provider") or d.get("pharmacy")) or None, "supply": _supply(d, e)})
            continue
        g["listings"].append({"t": day, "id": e.get("id"), "title": title, "dose": _text(d.get("instructions")),
                              "status": _text(d.get("status")), "end": _day(d.get("end") or d.get("stop") or e.get("end")),
                              "prescriber": _text(d.get("prescriber")) or None, "source": _text(d.get("src")) or None,
                              "rxnorm": _text(d.get("rxnorm")) or None})
    out = [_lay_out(key, g) for key, g in groups.items()]
    return sorted(out, key=lambda m: (m["name"].lower(), m["key"]))


def _said(span):
    """A span's listing as the records word it: its title, and its instructions when it has some."""
    return " · ".join(x for x in (span["title"], span["dose"]) if x)


def _lay_out(key, g):
    listings = sorted(g["listings"], key=lambda x: (x["t"], str(x["id"])))
    merged = []   # the same day, title and instructions from two sources: one listing
    for x in listings:
        same = next((m for m in merged if m["t"] == x["t"] and m["dose"].lower() == x["dose"].lower()
                     and m["title"].lower() == x["title"].lower()), None)
        if same is None:
            merged.append({**x, "ids": [x["id"]], "sources": [x["source"]] if x["source"] else []})
            continue
        same["ids"].append(x["id"])
        if x["source"] and x["source"] not in same["sources"]:
            same["sources"].append(x["source"])
        for k in ("end", "prescriber", "rxnorm"):
            same[k] = same[k] or x[k]
        if x["status"] and (not same["status"] or _stopped(x["status"])):
            same["status"] = x["status"]

    spans, changes = [], []
    for i, x in enumerate(merged):
        nxt = next((m["t"] for m in merged[i + 1:] if m["t"] > x["t"]), None)
        stopped = bool(x["end"]) or _stopped(x["status"])
        to = x["end"] if x["end"] else x["t"] if stopped else nxt
        if to and nxt and to > nxt:
            to, stopped = nxt, False   # a later listing takes over from its day
        to = max(to, x["t"]) if to else None
        span = {"from": x["t"], "to": to, "open": to is None, "stopped": stopped,
                "dose": x["dose"], "title": x["title"], "status": x["status"], "prescriber": x["prescriber"],
                "sources": x["sources"], "ids": x["ids"], "rxnorm": x["rxnorm"]}
        prev = spans[-1] if spans else None
        if prev and prev["dose"].lower() == span["dose"].lower() and prev["title"].lower() == span["title"].lower() \
                and prev["to"] is not None and prev["to"] >= span["from"] and not prev["stopped"]:
            prev.update({"to": span["to"], "open": span["open"], "stopped": span["stopped"], "status": span["status"] or prev["status"],
                         "prescriber": prev["prescriber"] or span["prescriber"], "rxnorm": prev["rxnorm"] or span["rxnorm"],
                         "ids": prev["ids"] + span["ids"],
                         "sources": prev["sources"] + [s for s in span["sources"] if s not in prev["sources"]]})
            continue
        if prev and (prev["dose"].lower() != span["dose"].lower() or prev["title"].lower() != span["title"].lower()) \
                and prev["to"] is not None and prev["to"] >= span["from"] and not prev["stopped"]:
            changes.append({"t": span["from"], "from": _said(prev), "to": _said(span), "id": span["ids"][0]})
        spans.append(span)

    fills = sorted(g["fills"], key=lambda f: (f["t"], str(f["id"])))
    last = spans[-1] if spans else None
    name = (merged[-1]["title"] if merged else fills[-1]["title"]) if (merged or fills) else key
    starts = [s["from"] for s in spans] + [f["t"] for f in fills]
    return {"name": name, "key": key, "rxnorm": sorted({s["rxnorm"] for s in spans if s["rxnorm"]}),
            "spans": spans, "changes": changes, "fills": fills, "start": min(starts) if starts else None,
            "stop": last["to"] if last and last["stopped"] else None}


# ---------------------------------------------------------------- what follows a medication through a rename (#954)

def of_records(db, pid):
    """The profile's medications as its records have them now (records.served: corrections applied). Inside a
    transaction they're put together afresh (records.assemble), never kept: served() keeps what it puts together by
    the records' revision, and a transaction rolled back gives that revision to the next change, which would be
    served what was rolled back."""
    from . import records
    if db.in_transaction:
        return (records.assemble(db, pid) or {}).get("medications") or []
    body = records.served(db, pid)
    return (json.loads(body).get("medications") or []) if body else []


def renames(pairs):
    """(#960) Whether records a source sends again rename a medication: any (its body here, its body as it'll be) of a
    listing or a fill whose title's key (med_key) changes, so follow() has something to do."""
    return any(isinstance(a, dict) and isinstance(b, dict) and (a.get("k") in MED_KINDS or b.get("k") in MED_KINDS)
               and med_key(a.get("title")) != med_key(b.get("title")) for a, b in pairs)


def _newest(m):
    """The record that names medication m: its newest listing, else its newest fill."""
    if m.get("spans") and m["spans"][-1].get("ids"):
        return m["spans"][-1]["ids"][-1]
    return m["fills"][-1]["id"] if m.get("fills") else None


def follow(db, pid, before, gone_only=False):
    """(#954) After a rename (or its undoing, gone_only: only medications no longer there), what hung on each
    medication of before (of_records() then) whose newest record is now in another goes with it to that one: its
    reminders and doses (by key) and the side effects linked to it (by name, given the other's name). Call inside the
    transaction that changed the records. -> what it moved, for back(): {"reminders" | "doses": [[id, from key, to
    key]], "journal": [[id, from name, to name]]}"""
    after = of_records(db, pid)
    where, now = {}, {m["key"] for m in after}
    for m in after:
        for i in [i for sp in m.get("spans") or [] for i in sp.get("ids") or []] + [f["id"] for f in m.get("fills") or []]:
            where[i] = m
    to = {}
    for m in before:
        if gone_only and m["key"] in now:
            continue
        there = where.get(_newest(m))
        if there is not None and there["key"] != m["key"]:
            to[m["key"]] = there
    return _move(db, pid, to) if to else {}


def _move(db, pid, to):
    from . import crypto, journal, med_log, med_reminders, records
    out = {"reminders": [], "doses": [], "journal": []}
    for table, field, enc in (("med_reminders", "reminders", med_reminders._enc), ("med_doses", "doses", med_log._enc)):
        for r in db.execute(f"SELECT id, body FROM {table} WHERE profile_id=?", (pid,)).fetchall():
            b = json.loads(crypto.dec(r["body"]))
            if b.get("med") in to:
                out[field].append([r["id"], b["med"], to[b["med"]]["key"]])
                db.execute(f"UPDATE {table} SET body=? WHERE id=?", (enc({**b, "med": to[b["med"]]["key"]}), r["id"]))
    for r in db.execute("SELECT id, body FROM journal WHERE profile_id=?", (pid,)).fetchall():
        b = json.loads(crypto.dec(r["body"]))
        k = med_key(b["med"]) if b.get("med") else None
        if k in to and b["med"] != to[k]["name"]:
            out["journal"].append([r["id"], b["med"], to[k]["name"]])
            db.execute("UPDATE journal SET body=? WHERE id=?", (journal._enc({**b, "med": to[k]["name"]}), r["id"]))
    out = {k: v for k, v in out.items() if v}
    if out:
        records._bump(db, pid)
    return out


def merged(a, b):
    """Two of follow()'s records as one: each item's first "from" and last "to" (a rename of a rename)."""
    out = {}
    for rec in (a or {}, b or {}):
        for field, items in rec.items():
            have = {x[0]: x for x in out.setdefault(field, [])}
            for i, frm, t in items:
                if i in have:
                    have[i][2] = t
                else:
                    have[i] = [i, frm, t]
                    out[field].append(have[i])
    return out


def back(db, pid, moved):
    """(#954) Undoing a rename: what follow() moved goes back, each where it was, if it's still where it went."""
    from . import crypto, journal, med_log, med_reminders, records
    n = 0
    for table, field, enc in (("med_reminders", "reminders", med_reminders._enc), ("med_doses", "doses", med_log._enc),
                              ("journal", "journal", journal._enc)):
        for i, frm, t in (moved or {}).get(field) or []:
            r = db.execute(f"SELECT body FROM {table} WHERE id=? AND profile_id=?", (i, pid)).fetchone()
            b = json.loads(crypto.dec(r["body"])) if r else None
            if b and b.get("med") == t:
                db.execute(f"UPDATE {table} SET body=? WHERE id=?", (enc({**b, "med": frm}), i))
                n += 1
    if n:
        records._bump(db, pid)
    return n


# ---------------------------------------------------------------- your own medications, and your changes (#1091)
# A profile's own medications and its changes to ones from the records (schema version 46, med_entries; the API in
# routes_meds.py). Each row's body is encrypted: {key, own, name, strength, dose, route, start, stop, status,
# prescriber, note, reason, hidden, again}.
#   an own entry  a medication of the person's own, every field theirs (a name and a start at least). Its key is "own:"
#                 and a random word, kept for good: what hangs on a key (reminders, doses) stays with it through a
#                 rename, and finds it again when its removal is undone (add_own(key=...)).
#   a change      a person's changes to a medication of the records, found by its grouping key (as reminders are): the
#                 records stay as they are, and a later pull, or the same records imported again, never undoes it.
#                 Only the fields that differ from what the records say are kept ("" for one taken away), applied over
#                 them (apply()). hidden: the medication is left out of DATA.medications (Current, Past, Today, the
#                 lanes, the calendar feed), its records kept, and listed in DATA.hiddenMeds to show again.
# Stopped taking (#1161): the person marks a current medication stopped, on a day (today by default) and with a reason
# in their own words or none: its stop, reason and status (Stopped) as a change's or an own entry's fields, so it leaves
# Current and its schedule ends there (med_reminders.ends: Today, the calendar feed, notifications). Started again
# adds a period after it, "again": [{from, to ("" while taken), reason}], each a span after the medication's own, the
# history before kept; stopping it again ends the latest. Both are kept by the medication's key, as every change is, so
# a later pull or re-import keeps them; Undo puts back the stop, reason, status and again they had (put_taking()).
# records._assemble adds both (assembled()): an own entry as a medication of one span with "own" (its row), one changed
# with "change" (its row) and "listed" (what the records say: the fields, and the spans as they were). A change whose
# records are gone (an import undone) is kept, unapplied, until they're back.
FIELDS = ("name", "strength", "dose", "route", "start", "stop", "status", "prescriber", "note", "reason")
OWN = "own:"
FIELD_MAX = {"name": 200, "strength": 100, "dose": 500, "route": 100, "status": 40, "prescriber": 200, "note": 2000, "reason": 500}
TAKING = ("stop", "reason", "status", "again")   # what Stopped taking and Started again change, and Undo puts back
AGAIN_MAX = 100
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_OWN_KEY = re.compile(r"^own:[0-9a-f]{8,32}$")


class EntryError(ValueError):
    """Something about a medication entry that can't be saved, said for the person who made it."""


def _enc(obj):
    from . import crypto
    return crypto.enc(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))


def is_own(key):
    return isinstance(key, str) and key.startswith(OWN)


def check(fields, own):
    """The fields as kept ({field: text} for each one given), or EntryError. own: an own entry, which needs a name and
    a start date; a change gives what Edit showed, the records' words among them (differs() keeps what it changes)."""
    from datetime import date
    out = {}
    for k in FIELDS:
        v = fields.get(k)
        if v is None:
            continue
        if not isinstance(v, str):
            raise EntryError(f"The {k} must be text.")
        v = v.strip() if k in ("note", "reason") else " ".join(v.split())
        if k in ("start", "stop"):
            if v and not _DAY.match(v):
                raise EntryError(f"Give the {k} date as YYYY-MM-DD.")
            try:
                v and date.fromisoformat(v)
            except ValueError:
                raise EntryError(f"The {k} date doesn't exist.") from None
        elif len(v) > FIELD_MAX[k]:
            raise EntryError(f"The {k} is too long (at most {FIELD_MAX[k]} characters).")
        out[k] = v
    if ("name" in out or own) and not out.get("name"):
        raise EntryError("Give the medication's name.")
    if own and not out.get("start"):
        raise EntryError("Give the date it started.")
    if out.get("start") and out.get("stop") and out["stop"] < out["start"]:
        raise EntryError("The stop date is before the start date.")
    return out


def listed(m):
    """What the records say of medication m (history()'s), as the fields an own entry has: what Edit starts from."""
    spans = m.get("spans") or []
    last = spans[-1] if spans else {}
    return {"name": m.get("name") or "", "strength": "", "dose": last.get("dose") or "", "route": "",
            "start": spans[0]["from"] if spans else m.get("start") or "", "stop": m.get("stop") or "",
            "status": last.get("status") or "", "prescriber": last.get("prescriber") or "", "note": "", "reason": ""}


def differs(was, fields):
    """Of a change's fields, those that differ from what the records say (was: listed()): what's kept. A name or start
    taken away isn't a change: the records' stays."""
    return {k: v for k, v in fields.items() if v != was.get(k, "") and (v or k not in ("name", "start"))}


def _rows(db, pid):
    from . import crypto
    return [(r, json.loads(crypto.dec(r["body"])))
            for r in db.execute("""SELECT id, body, created_name, created, changed_name, changed FROM med_entries
                                   WHERE profile_id=? ORDER BY id""", (pid,)).fetchall()]


def listing(db, pid):
    """The profile's own entries and changes, oldest first: {id, key, own, each field it has, hidden?, by, at, changed,
    changed_by} (an export's "medEntries")."""
    return [{"id": r["id"], "key": b.get("key"), "own": bool(b.get("own")), **{k: b[k] for k in FIELDS if k in b},
             **({"hidden": True} if b.get("hidden") else {}), **({"again": b["again"]} if b.get("again") else {}),
             "by": r["created_name"], "at": r["created"],
             "changed": r["changed"], "changed_by": r["changed_name"]} for r, b in _rows(db, pid)]


def get(db, pid, eid):
    from . import crypto
    r = db.execute("SELECT body FROM med_entries WHERE id=? AND profile_id=?", (eid, pid)).fetchone()
    return json.loads(crypto.dec(r["body"])) if r else None


def of_key(db, pid, key):
    """(id, body) of the change to the records' medication of this key, or (None, None)."""
    return next(((r["id"], b) for r, b in _rows(db, pid) if not b.get("own") and b.get("key") == key), (None, None))


def _span(f, title):
    stopped = bool(f.get("stop")) or _stopped(f.get("status") or "")
    to = f.get("stop") or (f["start"] if stopped else None)
    return {"from": f["start"], "to": to, "open": to is None, "stopped": stopped, "dose": f.get("dose") or "",
            "title": title, "status": f.get("status") or "", "prescriber": f.get("prescriber") or None,
            "sources": [], "ids": [], "rxnorm": None, **({"reason": f["reason"]} if stopped and f.get("reason") else {})}


def _own_med(row):
    f = {k: row.get(k) or "" for k in FIELDS}
    span = _span(f, " ".join(x for x in (f["name"], f["strength"]) if x))
    return {"name": f["name"], "key": row["key"], "rxnorm": [], "spans": [span], "changes": [], "fills": [],
            "start": f["start"], "stop": span["to"] if span["stopped"] else None, "own": row,
            **{k: f[k] for k in ("route", "note") if f[k]}}


def apply(m, row):
    """Medication m (history()'s) with a change (listing()'s row) over it, "change" the row and "listed" what the
    records say. Its latest listing takes the strength, dose, status, prescriber and stop; its first the start."""
    ch = {k: row[k] for k in FIELDS if k in row}
    was = listed(m)
    m["listed"] = {**was, "spans": json.loads(json.dumps(m["spans"]))}
    m["change"] = row
    if "name" in ch:
        m["name"] = ch["name"]
    spans = m["spans"]
    if not spans and (ch.get("start") or m.get("start")) and any(k in ch for k in ("strength", "dose", "start", "stop", "status")):
        spans.append(_span({"start": ch.get("start") or m["start"]}, m["name"]))   # fills alone: a span of its own
    if spans:
        first, last = spans[0], spans[-1]
        if "name" in ch or "strength" in ch:
            last["title"] = " ".join(x for x in (m["name"], ch.get("strength")) if x)
        for k in ("dose", "status", "prescriber"):
            if k in ch:
                last[k] = ch[k] or (None if k == "prescriber" else "")
        if ch.get("reason"):
            last["reason"] = ch["reason"]
        if ch.get("start"):
            first["from"] = min(ch["start"], first["to"]) if first["to"] else ch["start"]
        stop = ch["stop"] if "stop" in ch else was["stop"]
        if ch.get("stop"):
            last.update(to=max(ch["stop"], last["from"]), open=False, stopped=True)
        elif not stop and _stopped(ch.get("status") or ""):
            last.update(to=last["to"] or last["from"], open=False, stopped=True)
        elif not stop and ("stop" in ch or ch.get("status")):   # its stop taken away, or a status that isn't: taken still
            last.update(to=None, open=True, stopped=False)
        m["start"] = min([s["from"] for s in spans] + [f["t"] for f in m.get("fills") or []])
        m["stop"] = last["to"] if last["stopped"] else None
    m.update({k: ch[k] for k in ("route", "note") if ch.get(k)})
    return m


def again(m, periods):
    """(#1161) Medication m with the periods it was started again in after it stopped, each a span after its own (the
    latest one's title and dose; "again": true; its reason when it stopped again), its stop the latest one's."""
    spans = m.get("spans") or []
    if not spans or not periods:
        return m
    for a in periods:
        last, to = spans[-1], a.get("to") or None
        spans.append({"from": a["from"], "to": to, "open": to is None, "stopped": bool(to), "dose": last.get("dose") or "",
                      "title": last.get("title") or m.get("name") or "", "status": "Stopped" if to else "",
                      "prescriber": last.get("prescriber"), "sources": [], "ids": [], "rxnorm": None, "again": True,
                      **({"reason": a["reason"]} if to and a.get("reason") else {})})
    m["stop"] = spans[-1]["to"] if spans[-1]["stopped"] else None
    return m


def with_entries(meds, rows):
    """(meds with the profile's own entries added and its changes applied, sorted by name; those hidden: [{key, name,
    id, start, stop}]). rows: listing()'s."""
    by_key, hidden, out = {m["key"]: m for m in meds}, [], list(meds)
    for row in rows:
        if row["own"]:
            out.append(again(_own_med(row), row.get("again")))
            continue
        m = by_key.get(row["key"])
        if m is None:
            continue
        again(apply(m, row), row.get("again"))
        if row.get("hidden"):
            out.remove(m)
            hidden.append({"key": m["key"], "name": m["name"], "id": row["id"], "start": m.get("start"), "stop": m.get("stop")})
    return sorted(out, key=lambda m: (m["name"].lower(), m["key"])), sorted(hidden, key=lambda h: h["name"].lower())


def assembled(db, pid, data):
    """(DATA.medications, DATA.hiddenMeds) for records._assemble: history() with the profile's own entries and changes."""
    return with_entries(history(data), listing(db, pid))


def record_listed(db, pid, key):
    """What the records say of their medication of this key (listed()), hidden or changed or not, or None."""
    from . import records
    if db.in_transaction:
        data = records.assemble(db, pid) or {}
    else:
        body = records.served(db, pid)
        data = json.loads(body) if body else {}
    return next((listed(m) for m in history(data) if m["key"] == key), None)


def _save(db, pid, who, body, eid=None):
    from . import db as dbm, records
    if eid is None:
        eid = db.execute("INSERT INTO med_entries(profile_id, body, created_by, created_name, created) VALUES(?, ?, ?, ?, ?)",
                         (pid, _enc(body), who["id"], who["username"], dbm.now())).lastrowid
    else:
        db.execute("UPDATE med_entries SET body=?, changed_by=?, changed_name=?, changed=? WHERE id=? AND profile_id=?",
                   (_enc(body), who["id"], who["username"], dbm.now(), eid, pid))
    records._bump(db, pid)
    return eid


def _drop(db, pid, eid):
    from . import records
    db.execute("DELETE FROM med_entries WHERE id=? AND profile_id=?", (eid, pid))
    records._bump(db, pid)


def add_own(db, pid, who, fields, key=None):
    """A new own entry (check()'d fields). key: the one it had, its removal undone (when no entry has it now). Call
    inside a transaction. -> its id"""
    import secrets
    if key is not None:
        if not (isinstance(key, str) and _OWN_KEY.match(key)):
            raise EntryError("That isn't an entry of your own to put back.")
        if any(b.get("key") == key for _, b in _rows(db, pid)):
            raise EntryError("That entry is here already.")
    return _save(db, pid, who, {"key": key or OWN + secrets.token_hex(8), "own": True, **fields})


def change_own(db, pid, who, eid, fields):
    """An own entry's fields replaced; the side effects linked to it by name (journal.py) follow a new name. -> what
    it was, or None"""
    from . import crypto, journal
    was = get(db, pid, eid)
    if was is None or not was.get("own"):
        return None
    _save(db, pid, who, {"key": was["key"], "own": True, **fields, **({"again": was["again"]} if was.get("again") else {})}, eid)
    if was.get("name") and was["name"] != fields.get("name"):
        for r in db.execute("SELECT id, body FROM journal WHERE profile_id=?", (pid,)).fetchall():
            b = json.loads(crypto.dec(r["body"]))
            if b.get("med") == was["name"]:
                db.execute("UPDATE journal SET body=? WHERE id=?", (journal._enc({**b, "med": fields["name"]}), r["id"]))
    return was


def remove_own(db, pid, eid):
    """An own entry deleted; its doses and reminders stay, by its key. -> what it was (its key with it), or None"""
    was = get(db, pid, eid)
    if was is None or not was.get("own"):
        return None
    _drop(db, pid, eid)
    return was


def set_change(db, pid, who, key, was, fields=None, hidden=None):
    """The change to the records' medication of this key (was: record_listed()): its fields (check()'d, all Edit
    showed: those the records say are dropped), and/or hidden or shown again. A change left with nothing is removed.
    -> (what it was, or None; the row's id, or None once removed)"""
    eid, body = of_key(db, pid, key)
    before = dict(body) if body else None
    body = dict(body or {"key": key})
    if fields is not None:
        body = {k: v for k, v in body.items() if k not in FIELDS}
        body.update(differs(was, fields))
    if hidden is not None:
        body.pop("hidden", None)
        body.update({"hidden": True} if hidden else {})
    if not any(k in body for k in (*FIELDS, "hidden", "again")):
        if eid is not None:
            _drop(db, pid, eid)
        return before, None
    return before, _save(db, pid, who, body, eid)


def _shift(day, n):
    from datetime import date, timedelta
    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()


def periods(m):
    """(#1161) The times medication m (history()'s, with_entries()') was taken, as [(from, to or None while it's
    taken)]: its spans run together, a new one where a span that stopped is followed by one starting after it (Started
    again, or records that list it again after it ended)."""
    out = []
    for s in m.get("spans") or []:
        a = s.get("from")
        if not isinstance(a, str) or not _DAY.match(a):
            continue
        b = None if s.get("open") else (s.get("to") or a)
        if out and (out[-1][1] is None or a <= _shift(out[-1][1], 1)):
            out[-1][1] = None if b is None or out[-1][1] is None else max(out[-1][1], b)
        else:
            out.append([a, b])
    return [tuple(x) for x in out]


def gaps(m):
    """(#1161) The days between those times, when it wasn't taken: [(first day, last day)]. Its schedule gives no dose
    on them (med_log.scheduled): none counted as missed, none on Today, in the calendar feed or notified."""
    ps = periods(m)
    return [(_shift(a[1], 1), _shift(b[0], -1)) for a, b in zip(ps, ps[1:]) if a[1] is not None]


def check_again(v):
    """(#1161) Started again's periods as kept ([{from, to, reason}], in order, none before the one before it ended),
    or EntryError."""
    from datetime import date
    if v is None:
        return []
    if not isinstance(v, list) or len(v) > AGAIN_MAX:
        raise EntryError("The times it was started again aren't a list.")
    out, end = [], None
    for i, a in enumerate(v):
        if not isinstance(a, dict):
            raise EntryError("A time it was started again isn't an object.")
        x = {k: " ".join(str(a.get(k) or "").split()) if k != "reason" else str(a.get(k) or "").strip() for k in ("from", "to", "reason")}
        for k in ("from", "to"):
            if (x[k] or k == "from") and not _DAY.match(x[k]):
                raise EntryError("Give the dates as YYYY-MM-DD.")
            try:
                x[k] and date.fromisoformat(x[k])
            except ValueError:
                raise EntryError("A date given doesn't exist.") from None
        if len(x["reason"]) > FIELD_MAX["reason"]:
            raise EntryError(f"The reason is too long (at most {FIELD_MAX['reason']} characters).")
        if (x["to"] and x["to"] < x["from"]) or (end is not None and x["from"] < end) or (i < len(v) - 1 and not x["to"]):
            raise EntryError("The times it was started again overlap.")
        out.append(x if x["to"] else {"from": x["from"], "to": "", "reason": ""})
        end = x["to"]
    return out


def _entry(db, pid, key):
    """(id, body) of the own entry or the change of this key, or (None, None)."""
    return next(((r["id"], b) for r, b in _rows(db, pid) if b.get("key") == key), (None, None))


def _taking(body):
    return {k: body[k] for k in TAKING if k in (body or {})}


def _med_now(db, pid, key):
    m = next((x for x in of_records(db, pid) if x.get("key") == key), None)
    if m is None:
        raise LookupError(key)
    return m


def _keep(db, pid, who, key, eid, body):
    """A change or own entry saved, or a change left with nothing removed. -> its id, or None"""
    if not body.get("own") and not any(k in body for k in (*FIELDS, "hidden", "again")):
        if eid is not None:
            _drop(db, pid, eid)
        return None
    return _save(db, pid, who, body, eid)


def _day_given(day, what):
    from datetime import date
    if not isinstance(day, str) or not _DAY.match(day):
        raise EntryError(f"Give the day it {what} as YYYY-MM-DD.")
    try:
        date.fromisoformat(day)
    except ValueError:
        raise EntryError("That day doesn't exist.") from None
    return day


def mark_stopped(db, pid, who, key, day, reason=""):
    """(#1161) Stopped taking: the medication of this key (your own or the records') stopped on day, with the
    person's reason or none. It must be current (its latest span open). LookupError: no such medication. -> what its
    entry had of TAKING before (put_taking() puts it back)"""
    m = _med_now(db, pid, key)
    spans = m.get("spans") or []
    if not spans or not spans[-1].get("open"):
        raise EntryError("It's listed as stopped already.")
    _day_given(day, "stopped")
    if day < spans[-1]["from"]:
        raise EntryError(f"It was taken from {spans[-1]['from']}: give that day or a later one.")
    reason = str(reason or "").strip()
    if len(reason) > FIELD_MAX["reason"]:
        raise EntryError(f"The reason is too long (at most {FIELD_MAX['reason']} characters).")
    eid, body = _entry(db, pid, key)
    if is_own(key) and eid is None:
        raise LookupError(key)
    before = _taking(body)
    body = dict(body or {"key": key})
    if body.get("again"):
        body["again"] = [*body["again"][:-1], {**body["again"][-1], "to": day, "reason": reason}]
    else:
        body.update(stop=day, status="Stopped", reason=reason)
        if not reason and not body.get("own"):
            body.pop("reason")
    _keep(db, pid, who, key, eid, body)
    return before


def started_again(db, pid, who, key, day):
    """(#1161) Started again: the medication of this key, stopped, taken again from day (on or after it stopped), the
    history before it kept. -> what its entry had of TAKING before"""
    m = _med_now(db, pid, key)
    spans = m.get("spans") or []
    if not spans or spans[-1].get("open"):
        raise EntryError("It's listed as taken now." if spans else "It has no dates to start again from.")
    _day_given(day, "started again")
    ended = spans[-1].get("to") or spans[-1]["from"]
    if day < ended:
        raise EntryError(f"It stopped on {ended}: give that day or a later one.")
    eid, body = _entry(db, pid, key)
    if is_own(key) and eid is None:
        raise LookupError(key)
    before = _taking(body)
    body = dict(body or {"key": key})
    if len(body.get("again") or []) >= AGAIN_MAX:
        raise EntryError("It has been started again too many times to keep another.")
    body["again"] = [*(body.get("again") or []), {"from": day, "to": "", "reason": ""}]
    _keep(db, pid, who, key, eid, body)
    return before


def put_taking(db, pid, who, key, was):
    """(#1161) Undo: the stop, reason, status and again of the medication of this key's entry as they were (was:
    mark_stopped's or started_again's reply; what it lacks, taken away). LookupError: no such entry or medication."""
    if not isinstance(was, dict):
        raise EntryError("Nothing to put back.")
    fields = check({k: was[k] for k in ("stop", "reason", "status") if k in was}, False)
    periods = check_again(was.get("again"))
    eid, body = _entry(db, pid, key)
    if eid is None:
        if is_own(key):
            raise LookupError(key)
        _med_now(db, pid, key)
    body = {k: v for k, v in (body or {"key": key}).items() if k not in TAKING}
    body.update(fields)
    if periods:
        body["again"] = periods
    _keep(db, pid, who, key, eid, body)


def log_detail(change_):
    """What the activity log keeps: what was done, never the medication."""
    return {"change": change_}
