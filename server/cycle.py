"""Menstrual cycle tracking from the phone apps, opt-in (#183): which records are read, what the watch receiver keeps
of them, and what the page is given. Standard library only: watch/receiver.py imports it, and so does the app
(server/routes_cycle.py, which has the setting, the reading and the deletion).

Privacy first. This is among the most sensitive data a person has, so:
  - It is off for every profile until one of its owners turns it on (Sources → Phone and watch). While it is off the
    receiver takes every cycle record out of an upload before the upload is archived (keep()), so it is never stored
    anywhere: not in the receiver's database, not in its raw/ archive of uploads, not in any summary.
  - Sexual activity (Health Connect's SexualActivityRecord, HealthKit's HKCategoryTypeIdentifierSexualActivity) is
    taken out of every upload, on or off, and never kept: nothing Sanguity shows needs it, it says more about a
    person than anything else the phone sends, and a record that isn't kept can't be leaked, subpoenaed or shown to
    the wrong person.
  - Basal body temperature is cycle data too (it is how temperature-based cycle tracking works): it is taken out
    while tracking is off, and while it is on it stays in the archived uploads only (nothing shows it yet).
  - What is kept is in the receiver's own database, sealed with the app's key like the rest of it (watch/receiver.py),
    and in its own summary file, cycle.json beside daily.json, sealed the same way. It is never in daily.json, so
    nothing that reads the watch summary (the Body and activity tab, share links, the export's watch/daily.json, the
    visit summary) can carry it by accident. The app's database never holds the records: only the setting.
  - Only owners and editors see it; viewers don't, and aren't told it exists. It is never in a share link; it is in
    the export and the visit summary only when ticked at the time, each time.
  - The words describe what was recorded ("Flow recorded on 4 days", "Ovulation test: positive"); nothing here
    predicts a period, a fertile window or ovulation, or says what a record means.

Record types (both apps' payloads are worked out from the receiver's existing patterns; neither app documents these
types for us, so the field names below are assumptions, and anything unexpected is skipped rather than guessed):
  Health Connect, via Life Dashboard Companion: a list per record type, under the type's name in snake case, each
  entry with "uuid", "time" (an instant, ISO 8601) and "source", like its other samples; deletions in
  "deleted_records" with that type name. Values are Health Connect's own constants, as numbers or their names:
    menstruation_flow        {"flow": 0 unknown, 1 light, 2 medium, 3 heavy}
    menstruation_period      {"start_time", "end_time"}, like sleep and exercise sessions (a period as the person
                             logged it)
    ovulation_test           {"result": 0 inconclusive, 1 positive, 2 high, 3 negative}
    cervical_mucus           {"appearance": 1 dry, 2 sticky, 3 creamy, 4 watery, 5 egg white, 6 unusual;
                              "sensation": 1 light, 2 medium, 3 heavy}
    intermenstrual_bleeding  (nothing but its time): spotting
    sexual_activity          never kept (above)
  Apple Health, via Health Auto Export (#148): a metric per type in "data.metrics", named in snake case like its other
  metrics ("menstrual_flow", "ovulation_test_result", "cervical_mucus_quality", "intermenstrual_bleeding",
  "sexual_activity"; HealthKit's own identifiers, HKCategoryTypeIdentifierMenstrualFlow and so on, are read too), each
  row {"date" (or "startDate"), "value" (or "qty"), "source"}. A value is HealthKit's word ("Medium", "LH surge",
  "HKCategoryValueCervicalMucusQualityEggWhite") or its number:
    menstrual_flow           1 unspecified, 2 light, 3 medium, 4 heavy, 5 none; a row marked as a cycle's start
                             ("isCycleStart", HealthKit's HKMenstrualCycleStart) starts a new run of days
    ovulation_test_result    1 negative, 2 positive (LH surge), 3 inconclusive (indeterminate), 4 high (estrogen surge)
    cervical_mucus_quality   1 dry, 2 sticky, 3 creamy, 4 watery, 5 egg white
    intermenstrual_bleeding  spotting
HealthKit has no period record: a period is the days with flow, as Health Connect's flow records are when no period
was logged. Each record's day is its own local date (Health Connect's in the upload's time zone, Health Auto Export's
in the offset written with it). A flow of "none" is a day logged as no flow, and isn't shown. Symptoms (cramps and
the like) aren't read. An uploaded Apple Health export.xml's are read (#223, server/importers/apple_health.py, with
from_hae) only for a profile with tracking on (cycle_tracking.is_on), and kept apart from its records and its watch
summary (cycle_tracking.save_imported); its upload is kept as it came.

What the page gets (view()): spans, each a period as logged in Health Connect, or a run of consecutive days with flow
recorded outside any logged period, with each day's flow; and marks: ovulation tests, cervical mucus and spotting,
each on its day. On a day both apps sent flow, Health Connect's is the day's, as for the other metrics; the same mark
from both apps is shown once.
"""
import json, re
from datetime import date, datetime, timedelta, timezone

APP = {"hc": "Health Connect", "hae": "Apple Health"}

# ---------------------------------------------------------------- which records are cycle records
HC = {"menstruation_flow": "flow", "menstruation_period": "period", "ovulation_test": "ovulation",
      "cervical_mucus": "mucus", "intermenstrual_bleeding": "spotting"}
HC_NEVER = {"sexual_activity"}                 # never kept, on or off
HC_ALSO = {"basal_body_temperature"}           # cycle data the receiver doesn't show: off, it isn't kept either
HAE = {"menstrual_flow": "flow", "menstruation": "flow", "menstruation_flow": "flow",
       "ovulation_test_result": "ovulation", "ovulation_test": "ovulation",
       "cervical_mucus_quality": "mucus", "cervical_mucus": "mucus",
       "intermenstrual_bleeding": "spotting", "spotting": "spotting",
       "hkcategorytypeidentifiermenstrualflow": "flow", "hkcategorytypeidentifierovulationtestresult": "ovulation",
       "hkcategorytypeidentifiercervicalmucusquality": "mucus",
       "hkcategorytypeidentifierintermenstrualbleeding": "spotting"}
HAE_NEVER = {"sexual_activity", "hkcategorytypeidentifiersexualactivity"}
HAE_ALSO = {"basal_body_temperature", "hkquantitytypeidentifierbasalbodytemperature"}

LABEL = {"flow": "Flow", "period": "Period", "ovulation": "Ovulation test", "mucus": "Cervical mucus",
         "spotting": "Spotting"}

# values: Health Connect's numbers, HealthKit's numbers, and the words either may send
FLOW = {"hc": {0: "unspecified", 1: "light", 2: "medium", 3: "heavy"},
        "hae": {1: "unspecified", 2: "light", 3: "medium", 4: "heavy", 5: "none"}}
FLOW_WORDS = {"light": "light", "medium": "medium", "heavy": "heavy", "unspecified": "unspecified",
              "unknown": "unspecified", "none": "none", "noflow": "none"}
OVULATION = {"hc": {0: "inconclusive", 1: "positive", 2: "high", 3: "negative"},
             "hae": {1: "negative", 2: "positive", 3: "inconclusive", 4: "high"}}
OVULATION_WORDS = {"positive": "positive", "negative": "negative", "high": "high", "inconclusive": "inconclusive",
                   "indeterminate": "inconclusive", "luteinizinghormonesurge": "positive", "lhsurge": "positive",
                   "estrogensurge": "high"}
MUCUS = {"hc": {1: "dry", 2: "sticky", 3: "creamy", 4: "watery", 5: "egg white", 6: "unusual"},
         "hae": {1: "dry", 2: "sticky", 3: "creamy", 4: "watery", 5: "egg white"}}
MUCUS_WORDS = {"dry": "dry", "sticky": "sticky", "creamy": "creamy", "watery": "watery", "eggwhite": "egg white",
               "unusual": "unusual"}
SENSATION = {1: "light", 2: "medium", 3: "heavy"}
SENSATION_WORDS = {"light": "light", "medium": "medium", "heavy": "heavy"}
PREFIXES = ("hkcategoryvaluemenstrualflow", "hkcategoryvalueovulationtestresult", "hkcategoryvaluecervicalmucusquality",
            "flow", "result", "appearance", "sensation")

TABLE = """CREATE TABLE IF NOT EXISTS cycle(app TEXT, kind TEXT, id TEXT, src TEXT, day TEXT, end_day TEXT, v TEXT,
    v2 TEXT, PRIMARY KEY(app, kind, id)) WITHOUT ROWID;"""


def hae_name(name):
    """A Health Auto Export metric's name as the tables above spell it: lower case, words joined by _."""
    s = str(name or "").strip().lower()
    return s if s.startswith("hk") else "_".join(s.replace("-", " ").split())


def _value(v, numbers, words, app):
    """A value as its word, from a number (that app's) or a word; None when it's neither."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)) and v == int(v):
        return numbers[app].get(int(v)) if isinstance(numbers, dict) and app in numbers else numbers.get(int(v))
    w = re.sub(r"[^a-z0-9]", "", str(v).lower())
    if w.isdigit():
        return _value(int(w), numbers, words, app)
    for p in PREFIXES:
        if w.startswith(p) and w != p:
            w = w[len(p):]
            break
    return words.get(w)


def _parse(kind, r, app):
    """(value, detail) of one record, or None when it can't be read. detail: cervical mucus's sensation, or "start"
    for flow marked as a cycle's start."""
    if kind == "flow":
        v = _value(r.get("flow", r.get("value", r.get("qty"))), FLOW, FLOW_WORDS, app)
        start = any(r.get(k) in (True, 1, "1", "true", "yes", "Yes") for k in
                    ("isCycleStart", "is_cycle_start", "cycleStart", "HKMenstrualCycleStart", "menstrualCycleStart"))
        return (v, "start" if start else None) if v else None
    if kind == "ovulation":
        v = _value(r.get("result", r.get("value", r.get("qty"))), OVULATION, OVULATION_WORDS, app)
        return (v, None) if v else None
    if kind == "mucus":
        v = _value(r.get("appearance", r.get("value", r.get("qty"))), MUCUS, MUCUS_WORDS, app)
        s = _value(r.get("sensation"), SENSATION, SENSATION_WORDS, app)
        return (v, s) if v or s else None
    return (None, None)   # spotting: its day is all it says


def _instant(s):
    if not isinstance(s, str):
        return None
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _put(c, app, kind, rid, src, day, end, v, v2):
    c.execute("INSERT OR REPLACE INTO cycle VALUES(?,?,?,?,?,?,?,?)", (app, kind, str(rid), src, day, end, v, v2))


# ---------------------------------------------------------------- the receiver: leaving out, then keeping
def strip(p, on):
    """Take out of an upload (a parsed payload, changed in place) what isn't kept: sexual activity always, and every
    cycle record while tracking is off (on False, or None when it isn't known). Returns how many entries went."""
    if not isinstance(p, dict):
        return 0
    n = 0
    d = p.get("data")
    if isinstance(d, dict) and isinstance(d.get("metrics"), list):   # Health Auto Export
        out = HAE_NEVER | (set() if on else set(HAE) | HAE_ALSO)
        keep = [m for m in d["metrics"] if not (isinstance(m, dict) and hae_name(m.get("name")) in out)]
        n += len(d["metrics"]) - len(keep)
        d["metrics"] = keep
        return n
    out = HC_NEVER | (set() if on else set(HC) | HC_ALSO)
    for k in out & set(p):
        n += 1
        del p[k]
    dels = p.get("deleted_records")
    if isinstance(dels, list):
        keep = [x for x in dels if not (isinstance(x, dict) and x.get("type") in out)]
        n += len(dels) - len(keep)
        p["deleted_records"] = keep
    return n


def keep(body, on):
    """An upload's bytes as the receiver may archive and fold them in: unchanged when there's nothing to take out
    (the same object), else the payload written again without it."""
    p = json.loads(body)
    return json.dumps(p, separators=(",", ":"), ensure_ascii=False).encode() if strip(p, on) else body


def has_any(p):
    """Whether an upload holds a cycle record (or a deletion of one)."""
    if not isinstance(p, dict):
        return False
    d = p.get("data")
    if isinstance(d, dict) and isinstance(d.get("metrics"), list):
        return any(isinstance(m, dict) and hae_name(m.get("name")) in HAE for m in d["metrics"])
    return any(p.get(k) for k in HC) or any(isinstance(x, dict) and x.get("type") in HC for x in p.get("deleted_records") or [])


def from_health_connect(c, p, tz):
    """Fold a Life Dashboard Companion upload's cycle records in (tz: the upload's zone, for each record's day).
    Returns how many were kept."""
    n = 0
    day = lambda t: t.astimezone(tz).date().isoformat()
    for key, kind in HC.items():
        for r in p.get(key) or []:
            if not isinstance(r, dict):
                continue
            src = str(r.get("source") or "")
            if kind == "period":
                s, e = _instant(r.get("start_time")), _instant(r.get("end_time"))
                if s is None or e is None or e < s or e - s > timedelta(days=31):
                    continue
                last = e - timedelta(microseconds=1) if e > s else e   # an end at midnight is the day before's
                _put(c, "hc", kind, r.get("uuid") or f"period@{r['start_time']}", src, day(s), day(last), None, None)
            else:
                t, got = _instant(r.get("time")), _parse(kind, r, "hc")
                if t is None or got is None:
                    continue
                _put(c, "hc", kind, r.get("uuid") or f"{kind}@{r['time']}", src, day(t), None, *got)
            n += 1
    for d in p.get("deleted_records") or []:
        if isinstance(d, dict) and d.get("type") in HC and d.get("uuid"):
            c.execute("DELETE FROM cycle WHERE app='hc' AND kind=? AND id=?", (HC[d["type"]], str(d["uuid"])))
    return n


def from_hae(c, m, when):
    """Fold a Health Auto Export cycle metric in (when: the receiver's reading of its dates, an aware datetime in the
    record's own offset). Returns how many were kept. A row sent again (the same source and time) replaces itself."""
    kind, n = HAE.get(hae_name(m.get("name"))), 0
    if not kind:
        return 0
    for r in m.get("data") or []:
        if not isinstance(r, dict):
            continue
        t = when(r.get("date")) or when(r.get("startDate"))
        got = _parse(kind, r, "hae")
        if t is None or got is None:
            continue
        src = str(r.get("source") or "")
        _put(c, "hae", kind, f"{src}|{t.isoformat()}", src, t.date().isoformat(), None, *got)
        n += 1
    return n


def records(con):
    """Every cycle record kept, for cycle.json: [{kind, day, end?, value?, detail?, app, source}], by day."""
    out = []
    for app, kind, src, day, end, v, v2 in con.execute(
            "SELECT app, kind, src, day, end_day, v, v2 FROM cycle ORDER BY day, kind, app, src, id"):
        r = {"kind": kind, "day": day, "app": APP.get(app, app)}
        if end:
            r["end"] = end
        if v:
            r["value"] = v
        if v2:
            r["detail"] = v2
        if src:
            r["source"] = src
        out.append(r)
    return out


# ---------------------------------------------------------------- what the page is given
def _d(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def view(recs):
    """{"spans": [{from, to, logged, flow: [[day, value]], app, source}], "marks": [{kind, day, value?, detail?, app,
    source?}]} from cycle.json's records (see the docstring). Only what was recorded: no day is added or guessed."""
    recs = [r for r in recs or [] if isinstance(r, dict) and r.get("kind") in LABEL and _d(r.get("day"))]
    hc_first = lambda r: (r["day"], r.get("app") != APP["hc"], r.get("source") or "")
    periods, seen = [], set()
    for r in sorted((r for r in recs if r["kind"] == "period" and _d(r.get("end") or r["day"])), key=hc_first):
        a, b = r["day"], max(r["day"], r.get("end") or r["day"])
        if (a, b) not in seen:
            seen.add((a, b))
            periods.append({"from": a, "to": b, "logged": True, "flow": [], "app": r.get("app"), "source": r.get("source")})
    flows = {}
    for r in sorted((r for r in recs if r["kind"] == "flow"), key=hc_first):
        if r.get("app") != APP["hc"] and flows.get(r["day"], {}).get("app") == APP["hc"]:
            continue   # Health Connect's flow is the day's
        if r["day"] not in flows or r.get("detail") == "start":
            flows[r["day"]] = r
    runs, last = [], None
    for day in sorted(flows):
        r = flows[day]
        if r.get("value") == "none":
            continue
        inside = next((p for p in periods if p["from"] <= day <= p["to"]), None)
        if inside:
            inside["flow"].append([day, r["value"]])
            continue
        d = _d(day)
        if runs and last is not None and d - last == timedelta(days=1) and r.get("detail") != "start":
            runs[-1]["to"] = day
            runs[-1]["flow"].append([day, r["value"]])
            if r.get("app") != runs[-1]["app"]:
                runs[-1]["app"] = " and ".join(APP.values())
        else:
            runs.append({"from": day, "to": day, "logged": False, "flow": [[day, r["value"]]], "app": r.get("app"), "source": r.get("source")})
        last = d
    marks, seen = [], set()
    for r in sorted((r for r in recs if r["kind"] in ("ovulation", "mucus", "spotting")), key=hc_first):
        k = (r["kind"], r["day"], r.get("value"), r.get("detail"))
        if k in seen:
            continue
        seen.add(k)
        marks.append({x: r[x] for x in ("kind", "day", "value", "detail", "app", "source") if r.get(x)})
    spans = sorted(periods + runs, key=lambda s: (s["from"], s["to"]))
    for s in spans:
        if not s["source"]:
            del s["source"]
    return {"spans": spans, "marks": sorted(marks, key=lambda m: (m["day"], m["kind"]))}


def words(span):
    """A span's title and line in plain words: what was recorded, never what it means."""
    a, b = _d(span["from"]), _d(span["to"])
    days = (b - a).days + 1
    if span.get("logged"):
        title = f"Period, {days} day{'s' if days != 1 else ''}"
        line = f"Logged as a period in {span.get('app') or 'the phone app'}"
    else:
        title = f"Flow recorded on {len(span['flow'])} day{'s' if len(span['flow']) != 1 else ''}"
        line = f"From {span.get('app') or 'the phone app'}"
    levels = [v for _, v in span.get("flow") or [] if v and v != "unspecified"]
    if levels:
        line += " · flow " + ", ".join(dict.fromkeys(levels))
    return title, line


def mark_words(m):
    if m["kind"] == "spotting":
        return "Spotting"
    if m["kind"] == "mucus":
        v = m.get("value")
        return "Cervical mucus" + (f": {v}" if v else "") + (f" (sensation: {m['detail']})" if m.get("detail") else "")
    return f"{LABEL[m['kind']]}: {m.get('value')}"
