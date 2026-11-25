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
