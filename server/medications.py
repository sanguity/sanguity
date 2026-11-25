"""The medication timeline (issue #50): each medication's records put side by side, as spans.

The records hold medications as events of two kinds. A "med" event is a line of a medication list or a prescription
(FHIR MedicationRequest/Statement, a C-CDA's medications, a portal's table, one typed in): its time is when it was
listed or started, and its detail may say how to take it (instructions), its status, when it ended (end), who
prescribed it (prescriber) and its RxNorm code (rxnorm). An "rx" event is a pharmacy fill from a claim. history()
groups both by medication and lays each one out as what the records say, in order:

  {"name": what it's shown as (the newest listing's title),
   "key": the grouping key (the drug's leading words, before any strength: "tacrolimus" for "Tacrolimus 1 mg
          capsule" and "TACROLIMUS 0.5MG CAP"), of each record's title as imported: a medication renamed in the app
          (edits.py) is shown by its new name and keeps its key, so its reminders, doses and fills, which name it by
          its key, stay with it (#921),
   "aka": [the keys of the names it's shown by, where a rename gave it another (no other medication's)], only then:
          what a name typed or picked as shown (a side effect's medication, a phone's dose) finds it by,
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

Nothing here judges a dose, a gap or an overlap; the page shows the spans and the records behind each. Pharmacy
fills are matched to a medication by the same key and never stretch or start a span. Hidden events (edits.py) are
left out. records._assemble adds the result as "medications", only when the profile has medication records.
"""
import re

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


def history(data, imported=None):
    """The profile's medications as spans, changes and fills (see above), sorted by name. data: the assembled records;
    imported: {event id: its title as imported, before any correction} (records._assemble), whose key groups it."""
    details, groups, imported = data.get("details") or {}, {}, imported or {}
    for e in data.get("events") or []:
        if not isinstance(e, dict) or e.get("k") not in MED_KINDS or e.get("hidden"):
            continue
        day, title = _day(e.get("t")), _text(e.get("title"))
        if not day or not title:
            continue
        d = details.get(str(e.get("id")))
        d = d if isinstance(d, dict) else {}
        g = groups.setdefault(med_key(_text(imported.get(e.get("id"))) or title), {"listings": [], "fills": []})
        if e["k"] == "rx":
            g["fills"].append({"t": day, "id": e.get("id"), "title": title,
                               "pharmacy": _text(d.get("provider") or d.get("pharmacy")) or None, "supply": _supply(d, e)})
            continue
        g["listings"].append({"t": day, "id": e.get("id"), "title": title, "dose": _text(d.get("instructions")),
                              "status": _text(d.get("status")), "end": _day(d.get("end") or d.get("stop") or e.get("end")),
                              "prescriber": _text(d.get("prescriber")) or None, "source": _text(d.get("src")) or None,
                              "rxnorm": _text(d.get("rxnorm")) or None})
    out = [_lay_out(key, g) for key, g in groups.items()]
    for m in out:   # (#921) the keys of the names a rename gave it, none another medication's own
        g = groups[m["key"]]
        aka = sorted({med_key(x["title"]) for x in g["listings"] + g["fills"]} - set(groups))
        if aka:
            m["aka"] = aka
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
