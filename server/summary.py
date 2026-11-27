"""The visit summary (#48): one or two printed pages to bring to an appointment, made from a profile's records.

build() reads the records the page reads (records.assemble: corrections, matched lab names and every import put
together) and the profile's settings (the Overview's key labs, lab nicknames), and returns the summary as a plain
object; the page previews that object, and render() lays the same object out as a PDF (pdfdoc.py, standard
library only). The PDF is made in memory for each request and streamed: it is never written to disk or kept.

Sections, each optional (SECTIONS):
  out_of_range   results since the "since" date that are out of range, each judged against the reference range
                 printed on its own report (the result's own low and high, or the lab's own flag): never one range
                 across results or labs. A result whose report gives no range and no flag is judged against the
                 range the person set in Settings for that lab and unit (#44, server/ranges.py), if one fits; the
                 range is then printed with "(settings)" after it, and the note under the table says what that is.
                 Every flag worked out from a range is by ranges.judge, the one rule (#752: "<15" against a low of 15 is
                 low). A result that came twice is one, with any range and flag either copy has; a lab in two units is a
                 row in each, every result in one (_series, #781)
  trends         the key labs (overview.keyLabs) and any the person picked: the last result on or before the
                 "since" date, the latest, the change (none from a value given as "<5", #718), and a sparkline of
                 the results between (each [day, value, flag, low, high, "settings" for a range from settings, the
                 value as given], drawn against their ranges, #299), and a
                 chart of them (#302) whose band has a step for each result with a range (none beside one without,
                 ranges.steps, #752), each said with its source ("on report", "from settings": #649, #717, #752); a
                 Then judged against a range other than its row's (its Now's) says so in its cell (#717, #752); under
                 each table and the charts, what their flags are judged against (notes(), the preview's and the PDF's)
  medications    current medications (status active, or no status and no end date)
  appointments   upcoming appointments
  questions      the person's own questions, typed before generating
  measures       the phone's and watch's measures and the newer kinds of record (#225, server/measures.py): home
                 blood pressure, glucose, temperature, blood oxygen, respiratory rate, VO2 max, immunizations, ECGs,
                 BMI and growth; each offered only when the profile has some, and unticked unless the person ticks it
                 (or Settings → Visit summary ticks it at first, #186)
  extra sections registered by other features (register_section), such as the journal's recent symptoms (#49)

The "since" point is the last visit before today, or another past visit's date the person picks; with no past
visit, 90 days back. The appointment it is for is the next upcoming one by default.

The questions are kept as a draft per person and profile (#219: each account its own, so one person's never
replaces another's), encrypted, in summary_drafts. It isn't a setting, so it never shows on the Settings screen or in
a settings export; it goes with the account, the profile, or the account's access to the profile. (Until #219 it was
one draft per profile, the settings key QUESTIONS_KEY; schema step 31 moved each to the people who saw it.)

Sanguity shows what the records say. The summary doesn't interpret anything, and carries the "not medical advice"
notice on every page.
"""
import contextvars, datetime, json, math, re

from . import crypto, db as dbm, measures, pdfdoc, ranges, records, settings, units

SECTIONS = [("out_of_range", "Out-of-range results"), ("trends", "Trends since the last visit"),
            ("medications", "Current medications"), ("appointments", "Upcoming appointments"),
            ("questions", "My questions")]
NOTICE = ("Made by Sanguity from your own records. This isn't medical advice: it shows what the records say, and "
          "out-of-range results are flagged against the reference range printed on each result's own report "
          "(or, for a result whose report gives none, a range set in Settings, marked as such). "
          "Your care team interprets them in context.")
SETTINGS_NOTE = "(settings): a range from settings, set by you for results whose report gives none; not from the lab."
QUESTIONS_KEY = "summary.questions"
MAX_QUESTIONS, MAX_QUESTION_TEXT, MAX_PICKED = 30, 4000, 20
MAX_PAGES = 2
DEFAULT_DAYS = 90   # the "since" point with no past visit
ACTIVE = {"active", "current", "taking", "ongoing", "in progress"}

# Other features add a section here (register_section): (id, label, rows(db, pid, data, since, today) -> [{"when",
# "title", "text"}]). The journal (#49) is meant to add "symptoms", recent symptoms; rows are shown as a list.
# A watch's ECGs and irregular rhythm notifications (#182) are watch data, not records: measures.py offers them, with
# the other phone and watch measures, as sections of their own, left unticked (#225).
EXTRA = []


def register_section(sid, label, rows):
    if sid in {s for s, _ in SECTIONS} or sid in measures.IDS or sid in {s for s, _, _ in EXTRA}:
        raise ValueError(f"summary section {sid} is registered twice")
    EXTRA.append((sid, label, rows))


def sections():
    return [*SECTIONS, *((s, l) for s, l, _ in measures.SECTIONS), *((s, l) for s, l, _ in EXTRA)]


class SummaryError(ValueError):
    pass


def today(db=None, uid=None):
    """Today for the person making the summary: in their own time zone (#42), else the server's."""
    return settings.local_now(db, uid).date() if db is not None and uid is not None else datetime.date.today()


def date_format(db, uid):
    """The person's date format (#42): "mdy", "dmy", "ymd", or "" (not chosen: written as "Nov 15, 2026")."""
    return settings.effective(db, None, uid)["dates.format"][0] or ""


# the date format and the prepared year of the summary being drawn (render sets them; each request has its own)
_DATES = contextvars.ContextVar("summary_dates", default=("", None))


# ---------------------------------------------------------------- the questions draft

def questions(db, pid, uid):
    """This person's own draft for the profile (#219)."""
    r = db.execute("SELECT sealed FROM summary_drafts WHERE profile_id=? AND user_id=?", (pid, uid)).fetchone()
    if not r:
        return ""
    try:
        return json.loads(crypto.dec(r["sealed"])) or ""
    except (ValueError, TypeError):
        return ""


def clean_questions(text):
    if text is None:
        return ""
    if not isinstance(text, str):
        raise SummaryError("The questions must be text.")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > MAX_QUESTION_TEXT:
        raise SummaryError(f"The questions are too long (at most {MAX_QUESTION_TEXT} characters).")
    return text


def save_questions(db, pid, uid, text):
    """Keep this person's draft for the profile (call inside a transaction). Empty text removes it."""
    text = clean_questions(text)
    if not text.strip():
        db.execute("DELETE FROM summary_drafts WHERE profile_id=? AND user_id=?", (pid, uid))
        return ""
    db.execute("""INSERT INTO summary_drafts(profile_id, user_id, sealed, changed) VALUES(?, ?, ?, ?)
                  ON CONFLICT(profile_id, user_id) DO UPDATE SET sealed=excluded.sealed, changed=excluded.changed""",
               (pid, uid, crypto.enc(json.dumps(text, ensure_ascii=False)), dbm.now()))
    return text


def question_list(text):
    """The draft as a list of questions: one per line, any bullet or number in front taken off."""
    out = []
    for line in (text or "").split("\n"):
        q = re.sub(r"^\s*(?:[-*•·]+|\d{1,2}[.)])\s*", "", line).strip()
        if q:
            out.append(" ".join(q.split()))
    return out[:MAX_QUESTIONS]


# ---------------------------------------------------------------- reading the records

def _day(t):
    t = str(t or "")
    return t[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", t) else None


def fmt_date(d):
    if not d:
        return ""
    try:
        x = datetime.date.fromisoformat(d[:10])
    except ValueError:
        return str(d)
    f = _DATES.get()[0]
    if f == "ymd":
        return x.isoformat()
    return f"{x.day} {x.strftime('%b')} {x.year}" if f == "dmy" else f"{x.strftime('%b')} {x.day}, {x.year}"


def fmt_num(v):
    if v is None:
        return ""
    a = abs(v)
    if a >= 100:
        return f"{v:.0f}"
    s = f"{v:.1f}" if a >= 10 else f"{v:.2f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


OWN_UNIT = re.compile(r"(\S+)\s+([^\d\s-]\S*)")


def _apart(text, v):
    """A value reported with a unit of its own ("37 Cel", "72 /min": an import's vitals, put in the series' unit), as its
    number, or as none where that isn't v, the number in the series' unit (#1102: "37 Cel °F"); www/js/units.js
    saidApart is the same."""
    m = OWN_UNIT.fullmatch(text)
    if not m or v is None or ranges.common.value_number(text) is not None:
        return text
    n = ranges.common.value_number(m.group(1))
    return text if n is None else m.group(1) if n == v else text if ranges.comparator(text) else ""


def _pt(p):
    """A result point [time, value, low, high, flag, text, source tag, edge] as a dict (edge: whether its one-sided
    range leaves its limit's edge out, "<" or ">", #780), its flag as stored read as its report's (ranges.read_flag,
    #839: an older import's "N" is no flag, its ">" High, as the page reads them), before its copies are put together."""
    p = list(p) + [None] * (8 - len(p)) if isinstance(p, (list, tuple)) else [None] * 8
    v, lo, hi, text, edge = _num(p[1]), _num(p[2]), _num(p[3]), str(p[5] or "").strip(), str(p[7] or "")
    text = _apart(text, v)
    return {"t": p[0], "d": _day(p[0]), "v": v, "lo": lo, "hi": hi, "flag": ranges.read_flag(p[4], v, lo, hi, text, edge),
            "text": text, "rs": "report", "edge": edge}


def _judged(p, name, unit, rcfg):
    """A result's range, flag and its range's source by the one rule (#752, server/ranges.py judge): its report's range and
    flag; where it gives a range and no flag, the flag worked out against it; where it gives neither, the range set in
    Settings that fits it (#44, rcfg: ranges.config()), its flag worked out against that."""
    own = p["v"] is None or ranges.has_own(p["lo"], p["hi"], p["flag"])
    lo, hi, f, rs = ranges.judge(p["v"], p["lo"], p["hi"], p["flag"], p["text"], None if own else ranges.find(rcfg, name, unit, p["d"]),
                                 p["edge"])
    return {**p, "lo": lo, "hi": hi, "flag": f, "rs": rs, "edge": p["edge"] if rs == ranges.REPORT else ""}


def out_of_range(p):
    """"High", "Low" or "Flagged" by the result's flag (its report's, read as stored by the one rule, else the one
    worked out against its range: _pt, _judged), or None. "Flagged" for one its report gives without saying which way
    (an "A"), as every page says ◆ (#819: the summary and share links said "Abnormal"). (#839: it read a stored flag by a
    list of its own, "N" as none and ">" as High where the page read both as ◆; the one reading, ranges.read_flag, gives
    "H", "L", "A" or "".)"""
    f = p["flag"].upper()
    return {"H": "High", "L": "Low"}.get(f[:1], "Flagged") if f else None


def range_words(lo, hi, edge=""):
    """"0.7–1.3", "≤ 5" or "≥ 60" ("" with neither); a limit that leaves its edge out (edge, #780) as printed, "< 5",
    "> 60" (ranges.js rangeWords)."""
    if lo is not None and hi is not None:
        return f"{fmt_num(lo)}–{fmt_num(hi)}"
    if hi is not None:
        return f"{'<' if '<' in (edge or '') else '≤'} {fmt_num(hi)}"
    return f"{'>' if '>' in (edge or '') else '≥'} {fmt_num(lo)}" if lo is not None else ""


def range_text(p):
    w = range_words(p["lo"], p["hi"], p.get("edge"))
    return w + " (settings)" if w and p.get("rs") == ranges.SOURCE else w


def value_text(p):
    return p["text"] or fmt_num(p["v"])


NOT_WORKED_OUT = "not worked out"   # a trend's change from or to a value given as a bound (#718; lab-panel.js labStats)


def bound(p):
    """A result given as a bound, "<5" or ">90", not as the value measured: no change is worked out from it."""
    return bool(ranges.comparator(p["text"]))


def own_range(p):
    """The range a result is judged against, in words with its source (#752): ["report range", "1.7–2.4"], ["settings
    range", "5–20"]; with none, ["flagged on its report", ""] or ["no range given", ""]."""
    w = range_words(p["lo"], p["hi"], p.get("edge"))
    if w:
        return ["settings range" if p["rs"] == ranges.SOURCE else "report range", w]
    return ["flagged on its report" if p["flag"] else "no range given", ""]


def at_cell(p, row=False):
    """A trend's Then or Now: its value, day and flag in words (#649); and where the range it's judged against isn't the
    row's (row: its Now, whose range is the row's Now's range; None with none since, the row's range then blank), that
    range and its source, which its cell says ("own", #717, #752: a Then of 4 Low against 5–20 from settings sat beside
    the row's only range, the report's 3–17; a Then of 1.6 Low against its report's 1.7–2.4 beside 1.5–2.5 from settings)."""
    mine = own_range(p)
    other = row is not False and mine != (own_range(row) if row else ["no range given", ""])
    return {"value": value_text(p), "date": p["d"], "flag": out_of_range(p), **({"own": mine} if other else {})}


def _series(data, rcfg=None):
    """{name: {"name", "unit", "pts": [point dict with its source], "also": [the name's series in other units]}}: every
    import's series of a name and unit put together (the same test from two sources is one trend), each result keeping
    its own range. Two copies of one result (the same time, value as given and unit: from two sources, or twice from
    one), which the records' crossmatch.copies gave the one result's range (its edge with it, #780) and flag (#781: a
    copy with no range beside one with its report's range and Low kept the first, and the summary and a share link
    said 3.2 with neither), are one, with the sources of both (a "Same result" link's too: crossmatch.apply). Then each
    result is judged by the one rule (#752). A lab in two units is a series in each, as the records page keeps it
    (#438), none left out (#781: only the latest result's unit was kept, and a Low in the other went unsaid): the one
    with the latest result under its name, the others in its "also", newest first (each() gives every one). rcfg:
    ranges.config(), for results whose report gives no range (#44). Copies crossmatch.copies keeps apart stay two, each
    with its own range and flag (the series' "apart": two the person said aren't the same, #833; two whose reports print
    different ranges, #847), as do two copies saying different things, never one's range beside the other's flag, a
    flagged one after an unflagged one at their time, so a trend's Now and its chart's latest dot are the flagged one."""
    imports = data.get("imports") or {}
    label = lambda imp: (imports.get(str(imp)) or {}).get("label") or ""
    by = {}
    for s in data.get("labs") or []:
        if not isinstance(s, dict) or not s.get("name") or s.get("watch"):
            continue
        unit, same = s.get("unit") or "", s.get("same") if isinstance(s.get("same"), dict) else {}
        apart = s.get("apart") if isinstance(s.get("apart"), dict) else {}
        for n, raw in enumerate(s.get("pts") or []):
            p = _pt(raw)
            if p["d"]:
                srcs = [label(s.get("imp")), *(label(x.get("imp")) for x in same.get(str(n)) or () if isinstance(x, dict))]
                by.setdefault((s["name"], unit), []).append({**p, "unit": unit, "src": srcs, "apart": apart.get(str(n))})
    out = {}
    for (name, unit), pts in by.items():
        pts.sort(key=lambda p: str(p["t"]))
        kept, at = [], {}
        for p in pts:   # (as the page's same.js isCopy compares them)
            key = (p["t"], p["v"], p["text"], p["lo"], p["hi"], p["flag"], p["edge"], p.pop("apart"))
            k = at.get(key)
            if k is None:
                at[key] = p
                kept.append(p)
            else:   # the same result again (crossmatch.copies made its copies one): once, from both sources
                k["src"] += p["src"]
        pts = [{**_judged(p, name, unit, rcfg), "src": "; ".join(dict.fromkeys(x for x in p["src"] if x))} for p in kept]
        pts.sort(key=lambda p: (str(p["t"]), bool(p["flag"])))   # (#833) at one time, a flagged result last
        out.setdefault(name, []).append({"name": name, "unit": unit, "pts": pts})
    for name, ss in out.items():
        ss.sort(key=lambda s: str(s["pts"][-1]["t"]), reverse=True)   # the latest result's unit first
        out[name] = {**ss[0], "also": ss[1:]}
    return out


def each(series):
    """Every series of _series(): each name's, then its other units' (#781)."""
    for s in series.values():
        yield s
        yield from s["also"]


def _visits(data, now):
    past = [e for e in data.get("events") or [] if e.get("k") == "visit" and not e.get("hidden") and _day(e.get("t"))
            and _day(e.get("t")) < now]
    past.sort(key=lambda e: str(e.get("t")), reverse=True)
    return [{"date": _day(e["t"]), "title": e.get("title") or "Visit", "sub": e.get("sub") or ""} for e in past]


def _upcoming(data, now):
    rows = [u for u in data.get("upcoming") or [] if isinstance(u, dict) and _day(u.get("t")) and _day(u.get("t")) >= now]
    rows.sort(key=lambda u: str(u.get("t")))
    return [{"date": _day(u["t"]), "title": u.get("title") or "Appointment", "who": u.get("who") or "",
             "where": u.get("where") or ""} for u in rows]


def _medications(data, now):
    """The current medications: the medications the page shows (records._assemble's, medications.py: your own among
    them, your changes made, those hidden left out, #1091), each whose latest listing hasn't stopped or ended before
    now, and whose status, if it gives one, says it's taken."""
    rows = []
    for m in data.get("medications") or []:
        s = (m.get("spans") or [None])[-1]
        if not s or s.get("stopped") or (s.get("to") and s["to"] < now):
            continue
        status = str(s.get("status") or "").strip().lower()
        if status and status not in ACTIVE:
            continue
        rows.append({"name": m.get("name") or "Medication", "instructions": s.get("dose") or "", "since": s.get("from") or "",
                     "source": "Your own entry" if m.get("own") else ", ".join(s.get("sources") or [])})
    return sorted(rows, key=lambda r: r["name"].lower())


def options(db, pid, uid):
    """What the page offers before building: appointments, past visits, labs, the key labs and the draft."""
    now = today(db, uid).isoformat()
    data = records.assemble(db, pid) or {"events": [], "labs": []}
    cfg = settings.merged(db, pid, uid)
    series = _series(data)
    nick = cfg.get("labNicknames") or {}
    key_labs = [n for n in (cfg.get("overview") or {}).get("keyLabs") or [] if n in series]
    have = set(measures.offered(measures.context(db, pid, data, cfg)))   # (#225) those with something, unticked ("off")
    ticked = set((cfg.get("summary") or {}).get("ticked") or [])   # unless Settings ticks them at first (#186)
    secs = [{"id": s, "label": l, **({"off": True} if s in measures.IDS and s not in ticked else {})} for s, l in sections()
            if s not in measures.IDS or s in have]
    return {"today": now, "upcoming": _upcoming(data, now), "visits": _visits(data, now)[:40],
            "labs": sorted(({"name": n, "label": nick.get(n, n)} for n in series), key=lambda x: x["label"].lower()),
            "keyLabs": key_labs, "questions": questions(db, pid, uid), "sections": secs,
            "notice": NOTICE, "has_records": bool(data.get("events") or data.get("labs"))}


def build(db, pid, uid, profile_name, opts):
    """The summary as a plain object (what the page previews and render() prints). opts: appointment (index into the
    upcoming appointments, or None for none), since (a past day, YYYY-MM-DD, or None for the last visit), sections
    (ids), labs (more lab names for the trends), questions (text)."""
    now_d = today(db, uid)
    now = now_d.isoformat()
    data = records.assemble(db, pid) or {"events": [], "labs": []}
    cfg = settings.merged(db, pid, uid)
    nick = cfg.get("labNicknames") or {}
    series = _series(data, ranges.config(cfg))   # the ranges set, with About this person's birth date and sex (#194)
    visits, upcoming = _visits(data, now), _upcoming(data, now)
    wanted = [s for s in (opts.get("sections") or []) if s in {x for x, _ in sections()}]
    if not wanted:
        raise SummaryError("Pick at least one section.")

    appt = opts.get("appointment")
    if appt is not None and not (isinstance(appt, int) and 0 <= appt < len(upcoming)):
        raise SummaryError("That appointment isn't one of the upcoming ones.")
    since = opts.get("since")
    if since:
        if not isinstance(since, str) or not _day(since) or since[:10] >= now:
            raise SummaryError("The “since” date must be a day before today.")
        since = since[:10]
        label = next((v["title"] for v in visits if v["date"] == since), "")
    elif visits:
        since, label = visits[0]["date"], visits[0]["title"]
    else:
        since, label = (now_d - datetime.timedelta(days=DEFAULT_DAYS)).isoformat(), f"the last {DEFAULT_DAYS} days"

    picked = opts.get("labs") or []
    if not isinstance(picked, list) or len(picked) > MAX_PICKED:
        raise SummaryError(f"Pick at most {MAX_PICKED} more labs.")
    key_labs = [n for n in (cfg.get("overview") or {}).get("keyLabs") or [] if n in series]
    trend_names = key_labs + [n for n in picked if isinstance(n, str) and n in series and n not in key_labs]

    out = {"profile": profile_name, "prepared": now, "date_format": date_format(db, uid), "for": upcoming[appt] if appt is not None else None,
           "since": {"date": since, "label": label}, "sections": wanted, "notice": NOTICE}
    if "out_of_range" in wanted:
        rows = []
        for s in each(series):   # a lab in two units: each its own row (#781)
            name = s["name"]
            recent = [p for p in s["pts"] if p["d"] > since]
            flagged = [p for p in recent if out_of_range(p)]
            if not flagged:
                continue
            p = flagged[-1]
            rows.append({"name": name, "label": nick.get(name, name), "value": value_text(p), "unit": s["unit"],
                         "flag": out_of_range(p), "range": range_text(p), "date": p["d"], "source": p["src"],
                         "out": len(flagged), "of": len(recent), "key": name in key_labs})
        rows.sort(key=lambda r: r["label"].lower())
        rows.sort(key=lambda r: r["date"], reverse=True)   # newest first, the key labs before the rest
        rows.sort(key=lambda r: not r["key"])
        out["out_of_range"] = rows
    if "trends" in wanted:
        rows = []
        for name, s in ((n, s) for n in trend_names for s in [series[n], *series[n]["also"]]):   # each unit its row (#781)
            before = [p for p in s["pts"] if p["d"] <= since]
            after = [p for p in s["pts"] if p["d"] > since]
            then = before[-1] if before else None
            last = after[-1] if after else None
            change = None
            if then and last and then["v"] is not None and last["v"] is not None:
                # never from a value given as "<5" or ">90" (#718: "<5" then 6 was "+1"), said as the lab panel says it
                change = NOT_WORKED_OUT if bound(then) or bound(last) else last["v"] - then["v"]
            line = ([then] if then else []) + after
            if len(line) < 2:   # too few since: the last few results, for some shape
                line = s["pts"][-6:]
            # Then flagged as Now is, so the table and the chart agree on it (#649), saying the range it's judged against
            # where that isn't the row's (#717, #752); each spark point [day, value, flag, low, high, "settings" or None,
            # its value as given, its range's edge]: its range from settings where its report gave none (#44), the chart's
            # band marked so; its value as the table says it, "<5" (#718); "<" or ">" where its range leaves its limit's
            # edge out, "<5" (#780)
            rows.append({"name": name, "label": nick.get(name, name), "unit": s["unit"],
                         "then": then and at_cell(then, last), "now": last and {**at_cell(last), "range": range_text(last)},
                         "change": change if change in (None, NOT_WORKED_OUT) else ("+" if change > 0 else "") + fmt_num(0 if abs(change) < 1e-9 else change),
                         "since": len(after),
                         "spark": [[p["d"], p["v"], out_of_range(p), p["lo"], p["hi"], ranges.SOURCE if p["rs"] == ranges.SOURCE else None,
                                    value_text(p), p["edge"]] for p in line if p["v"] is not None]})
        out["trends"] = rows
    if "medications" in wanted:
        out["medications"] = _medications(data, now)
    if "appointments" in wanted:
        out["appointments"] = [{**u, "this": i == appt} for i, u in enumerate(upcoming[:10])]
    if "questions" in wanted:
        out["questions"] = question_list(clean_questions(opts.get("questions")))
    mw = [x for x in wanted if x in measures.IDS]   # the phone's and watch's measures (#225): since the "since" day
    if mw:
        out["measures"] = measures.blocks(measures.context(db, pid, data, cfg), mw, lambda d: since < d <= now,
                                          history=lambda d: d <= now, most=measures.SUMMARY_MOST)
    extra = []
    for sid, lbl, fn in EXTRA:
        if sid in wanted:
            extra.append({"id": sid, "label": lbl, "rows": list(fn(db, pid, data, since, now) or [])})
    out["extra"] = extra
    if any(r["range"].endswith("(settings)") for r in out.get("out_of_range") or []) or any(map(uses_settings, out.get("trends") or [])):
        out["settings_note"] = SETTINGS_NOTE   # the page shows it under the tables, as the PDF does
    out["notes"] = notes(out)
    return out


def uses_settings(r):
    """A trend whose Now's range, or its chart's and sparkline's band, is a range from settings somewhere (#44, #649)."""
    return ((r["now"] or {}).get("range") or "").endswith("(settings)") or any(p[5] == ranges.SOURCE for p in r["spark"])


BOUND_NOTE = " A value given as a bound (“<15”) is out of range only where every value it stands for is."
# what a ◆ is, said where there's one (#819: the notes named only High and Low, "outside the range")
FLAGGED_NOTE = " Flagged: flagged on its own report without saying high or low, its value inside its range or with none given."


def notes(s):
    """What the tables' and the charts' flags are judged against, said under each (#717, #752), in the preview and the
    PDF alike: {"out_of_range", "trends", "charts"} (each only with its section). A range from settings, a cell judged
    against a range other than its row's, a value given as a bound and a result flagged on its report without saying
    which way (#819, ranges.flagged_words) are said only where there's one."""
    out, oor, tr = {}, s.get("out_of_range"), s.get("trends")
    if oor:
        flags = [r["flag"] for r in oor]
        out["out_of_range"] = (f"(2 of 3): how many of the results since then were {ranges.flagged_words(flags, 'short')}. Each is "
                               "judged against the range printed on its own report" + (", or, where its report gives none, the range "
                                                                                      "from settings, its Range marked “(settings)”"
                                                                                      if any(r["range"].endswith("(settings)") for r in oor) else "")
                               + "." + (FLAGGED_NOTE if any(map(ranges.says_neither, flags)) else "")
                               + (BOUND_NOTE if any(ranges.comparator(r["value"]) for r in oor) else ""))
    if tr:
        sets = any(map(uses_settings, tr))   # its Now's range, or its sparkline's and chart's band (#649)
        flagged = any(ranges.says_neither((r[k] or {}).get("flag")) for r in tr for k in ("then", "now"))
        out["trends"] = ("Then: the last result on or before that day. Since: how many results since. High or Low (in red): "
                         "outside the range on that result's own report" + (", or, where its report gives none, outside the "
                                                                            "range from settings" if sets else "") + "."
                         + (FLAGGED_NOTE if flagged else "")
                         + (" A Then judged against a range other than its row's (its Now's) gives it in its cell (“report "
                            "range” or “settings range” and its numbers)." if any((r["then"] or {}).get("own") for r in tr) else "")
                         + (BOUND_NOTE if any(p[2] and ranges.comparator(p[6]) for r in tr for p in r["spark"]) else "")
                         + (" Change: not worked out from a value given as “<5” or “>90”." if any(r["change"] == NOT_WORKED_OUT for r in tr) else ""))
        drawn = [r for r in tr if sum(1 for p in r["spark"] if p[1] is not None) > 1]   # (_charts': two results or more)
        if drawn:
            sets = any(p[5] == ranges.SOURCE for r in drawn for p in r["spark"])
            out["charts"] = ("Charts: the last result on or before that day and each since (with none since, the last few), the "
                             "shaded band each result's own reference range" + (" or, for a result whose report gives none, the "
                                                                                "range from settings" if sets else "")
                             + ", each step marked “on report”" + (" or “from settings”, none beside a result with neither" if sets
                                                                   else ", none beside a result whose report gives none")
                             + "; a red triangle pointing up or down, a result above or below it"
                             + ("; a red diamond, one flagged on its report" if any(ranges.says_neither(p[2]) for r in drawn for p in r["spark"]) else "")
                             + ".")
    return out


# ---------------------------------------------------------------- the PDF

MARGIN, TOP, FOOT = 46, 746, 64
INK, MUTED, RULE, SHADE, CRIT, BAND = 0.1, 0.42, 0.75, 0.95, (0.66, 0.11, 0.18), 0.86   # BAND: a sparkline's range (#299)


class _Full(Exception):
    pass


class _Layout:
    def __init__(self, doc, s):
        self.doc, self.s, self.page, self.y = doc, s, None, 0
        self.left_out = {}
        self.new_page()

    def new_page(self):
        if len(self.doc.pages) >= MAX_PAGES:
            raise _Full()
        self.page = self.doc.page()
        self.y = TOP
        if len(self.doc.pages) > 1:
            self.page.text(MARGIN, self.y, f"Visit summary · {self.s['profile']}", 9, True, MUTED)
            self.page.text_right(pdfdoc.WIDTH - MARGIN, self.y, f"prepared {fmt_date(self.s['prepared'])}", 9, False, MUTED)
            self.y -= 22

    def need(self, h):
        if self.y - h < FOOT + 6:
            self.new_page()

    def heading(self, text, count=None):
        self.need(40)   # a heading with at least a row under it
        self.y -= 8
        self.page.text(MARGIN, self.y, text + (f" ({count:,})" if count is not None else ""), 12, True, INK)
        self.y -= 5
        self.page.line(MARGIN, self.y, pdfdoc.WIDTH - MARGIN, self.y, 0.6, RULE)
        self.y -= 13

    def note(self, text, size=8.5, color=MUTED):
        for line in pdfdoc.wrap(text, size, pdfdoc.WIDTH - 2 * MARGIN):
            self.need(12)
            self.page.text(MARGIN, self.y, line, size, False, color)
            self.y -= 12

    def table(self, sid, cols, rows, cells, draw=None):
        """cols [(header, width)], the last width None for the rest; cells(row) -> [text]; draw(page, row, x, y, w)
        for a column whose text is None (a sparkline)."""
        right = pdfdoc.WIDTH - MARGIN
        xs, x = [], MARGIN
        for _, w in cols:
            xs.append((x, (w if w is not None else right - x)))
            x += w or 0

        def header():
            for (h, _), (cx, _w) in zip(cols, xs):
                self.page.text(cx + 2, self.y, h, 7.5, True, MUTED)
            self.y -= 12
        self.need(12 + 15)
        header()
        for i, r in enumerate(rows):
            # each cell wrapped to its column, never cut (#535): the row is as tall as its tallest cell
            texts = cells(r)
            lines = [None if t is None else pdfdoc.wrap(t[0] if isinstance(t, tuple) else t, 9, cw - 6, isinstance(t, tuple))
                     for (_, cw), t in zip(xs, texts)]
            extra = 11 * (max((len(x) for x in lines if x), default=1) - 1)
            try:
                if self.y - 15 - extra < FOOT + 6:
                    self.new_page()
                    header()
            except _Full:
                self.left_out[sid] = len(rows) - i
                raise
            if i % 2 == 0:
                self.page.rect(MARGIN, self.y - 4 - extra, right - MARGIN, 14 + extra, SHADE)
            for (cx, cw), text, cell in zip(xs, texts, lines):
                if text is None:
                    if draw:
                        draw(self.page, r, cx + 2, self.y - 2, cw - 6)
                    continue
                bold = isinstance(text, tuple)
                for n, line in enumerate(cell):
                    self.page.text(cx + 2, self.y - 11 * n, line, 9, bold, CRIT if bold else INK)
            self.y -= 15 + extra


def spark_span(vs, lo, hi):
    """A sparkline's value scale (#299, as chartkit.js ckSparkSpan): the reference band the middle 60% of it (a one-sided
    range's edge at the middle), else the values' own, widened for every value."""
    v0, v1 = min(vs), max(vs)
    if lo is not None and hi is not None and hi > lo:
        a, b = lo - (hi - lo) / 3, hi + (hi - lo) / 3
    elif lo is not None or hi is not None:
        e = hi if hi is not None else lo
        k = max(e - v0, v1 - e, abs(e) / 10) or 1
        a, b = e - k, e + k
    else:
        k = (v1 - v0) / 10 or abs(v0) / 10 or 1
        a, b = v0 - k, v1 + k
    m = (b - a) / 20
    return min(a, v0 - m), max(b, v1 + m)


def _spark(page, row, x, y, w, h=10):
    """The trends' sparkline, as the preview's (#299): scaled to the band, each result's own range shaded as steps; with
    fewer than three results only the latest, against its range (nothing without one). The latest dot red when out."""
    pts = row["spark"]
    if not pts:
        return
    line = len(pts) > 2
    use = pts if line else pts[-1:]
    days = [datetime.date.fromisoformat(p[0]).toordinal() for p in use]
    band = ranges.steps([(t, p[3], p[4], p[5], p[7]) for t, p in zip(days, use)], -math.inf, math.inf)   # (#752: none beside a result without one)
    if not line and not band:
        return
    los, his = [r[2] for r in band if r[2] is not None], [r[3] for r in band if r[3] is not None]
    v0, v1 = spark_span([p[1] for p in use], min(los) if los else None, max(his) if his else None)
    t0, t1 = (days[0], days[-1]) if line else (days[0] - 1, days[0])
    X = lambda t: x + 2 + (t - t0) / ((t1 - t0) or 1) * (w - 4)   # the latest dot over the band's end, not past it
    Y = lambda v: y + (v - v0) / (v1 - v0) * h
    for ta, tb, lo, hi, *_ in band:   # each result's step from halfway after the result before to halfway to the next
        a, b = max(x, X(ta)), min(x + w, X(tb))
        bot, top = Y(lo) if lo is not None else y, Y(hi) if hi is not None else y + h
        if b > a and top > bot:
            page.rect(a, bot, b - a, top - bot, BAND)
    if line:
        page.polyline([(X(t), Y(p[1])) for t, p in zip(days, use)], 0.8, MUTED)
    page.dot(X(days[-1]), Y(use[-1][1]), 1.5, CRIT if use[-1][2] else INK)


# the trends' charts (#302), under their table: four a row, each plot 34 pt tall, in the data blue (app.css --c-lab), the
# band the table's light shade with its edges (a band filling most of the plot, as each does, in BAND was a grey box)
CHART_COLS, CHART_H, LAB = 4, 34, (0.165, 0.471, 0.839)


def _ticks(lo, hi, n):
    """About n round numbers from lo to hi (chartkit.js niceTicks): steps of 1, 2, 2.5 or 5 times a power of ten."""
    span = hi - lo
    mag = 10 ** math.floor(math.log10(span / n))
    step = next((m * mag for m in (1, 2, 2.5, 5) if span / (m * mag) <= n), 10 * mag)
    out, v = [], math.ceil(lo / step) * step
    while v <= hi + 1e-9:
        out.append(round(v, 10))
        v += step
    return out


def _chart(page, row, names, x, top, w):
    """A trend's chart (#302), as the preview's (chartkit.js ckSolo): its name over it (names, its lines); the value axis
    (round numbers, a hairline each); each result's own range shaded as steps, with its edges; the line through its
    results (row["spark"]: the last one on or before the "since" day and each after) and a dot each, a red triangle
    pointing up or down for one above or below its range and a diamond for another flag (the fonts have no ▲ ▼); its
    first and last days under it. top: its box's top; it takes _chart_h(names)."""
    pts = [p for p in row["spark"] if p[1] is not None]
    for n, line in enumerate(names):
        page.text(x, top - 8 - 9 * n, line, 8, True, INK)
    T = top - 8 - 9 * (len(names) - 1) - 7
    B = T - CHART_H
    days = [datetime.date.fromisoformat(p[0]).toordinal() for p in pts]
    pad = max((days[-1] - days[0]) * 0.03, 15 if days[0] == days[-1] else 1)
    t0, t1 = days[0] - pad, days[-1] + pad
    bounds = [v for p in pts for v in (p[1], p[3], p[4]) if v is not None]
    lo, hi = min(bounds), max(bounds)
    if lo == hi:
        lo, hi = lo - (abs(lo) * 0.1 or 1), hi + (abs(hi) * 0.1 or 1)
    lo, hi = lo - (hi - lo) * 0.08, hi + (hi - lo) * 0.08
    ticks = _ticks(lo, hi, 3)
    L = x + max(pdfdoc.width(fmt_num(v), 6.5) for v in ticks) + 3
    R = x + w
    X = lambda t: L + (t - t0) / (t1 - t0) * (R - L)
    Y = lambda v: B + (v - lo) / (hi - lo) * CHART_H
    for v in ticks:
        page.line(L, Y(v), R, Y(v), 0.3, RULE)
        page.text_right(L - 2, Y(v) - 2.2, fmt_num(v), 6.5, False, MUTED)
    # the band's steps (server/ranges.py steps, as ranges.js rangeSteps): each result's range from halfway after the
    # result before it to halfway to the next, none beside a result whose report gave none (#752); neighbours with the
    # same range from the same source one ("<5" and "≤ 5" two, #780)
    steps = [[X(a), X(b), rlo, rhi, rs, e] for a, b, rlo, rhi, rs, e in ranges.steps([(t, p[3], p[4], p[5], p[7]) for t, p in zip(days, pts)], t0, t1)]
    for a, b, rlo, rhi, *_ in steps:
        bot, tp = Y(rlo) if rlo is not None else B, Y(rhi) if rhi is not None else T
        page.rect(a, bot, b - a, tp - bot, SHADE)
        for e in (rlo, rhi):
            if e is not None:
                page.line(a, Y(e), b, Y(e), 0.4, MUTED)
    page.line(L, B, R, B, 0.5, MUTED)
    page.polyline([(X(t), Y(p[1])) for t, p in zip(days, pts)], 0.9, LAB)
    for t, p in zip(days, pts):
        cx, cy, f, s = X(t), Y(p[1]), p[2] or "", 2.8
        if f[:1].lower() == "h":
            page.shape([(cx, cy + s * 1.1), (cx + s, cy - s * 0.7), (cx - s, cy - s * 0.7)], CRIT)
        elif f[:1].lower() == "l":
            page.shape([(cx, cy - s * 1.1), (cx + s, cy + s * 0.7), (cx - s, cy + s * 0.7)], CRIT)
        elif f:
            page.shape([(cx, cy + s), (cx + s, cy), (cx, cy - s), (cx - s, cy)], CRIT)
        else:
            page.dot(cx, cy, 1.4, LAB)
    # each step said with its source, as the preview's (chartkit.js ckBandLabels, #649, #717, #752): the latest at the
    # plot's right where it reaches it, else over its own stretch, each before it over its own; then the rest clear of it
    marks, taken = [(X(t), Y(p[1])) for t, p in zip(days, pts)], []
    for n, (a, b, rlo, rhi, rs, e) in [*enumerate(steps)][::-1]:
        last = n == len(steps) - 1
        box = _band_label(page, f"{range_words(rlo, rhi, e)} {band_source(rs)}", rlo, rhi, marks, L, R, T, B, Y,
                          span=None if b >= R - 0.5 else (a, b), taken=taken, alone=last or rs != steps[n + 1][4], src=band_source(rs))
        taken += [box] if box else []
    page.text(L, B - 9, _short(pts[0][0]), 6.5, False, MUTED)
    page.text_right(R, B - 9, _short(pts[-1][0]), 6.5, False, MUTED)


def band_source(rs):
    """A band step's source in words (#752; ranges.js rangeSrc): "on report" or "from settings"."""
    return "from settings" if rs == ranges.SOURCE else "on report"


def _band_label(page, words, lo, hi, marks, L, R, T, B, Y, size=6.5, span=None, taken=(), alone=True, src=None):
    """A band step's words, its numbers and source (chartkit.js ckBandLabel, ckBandLabels): at the right of a chart's
    plot, inside the band under its top edge, else over its foot, else just over or under it, whichever is first clear
    of every result (marks) and inside the plot, B to T (y up); where the words are wider than the plot, its source (src)
    alone (as the preview's). span (a, b): a step that doesn't reach the plot's right end instead (#717, #752): centred
    on its stretch inside the plot; where its numbers don't fit there, its source alone ("on report", "from settings"),
    which may run past it if alone (the latest step, or one whose source isn't the next's), else only where it fits
    there. Never over a label placed already (taken: their boxes). -> its box (x0, x1, baseline), or None"""
    src = src or words
    if span:
        a, b = span
        if pdfdoc.width(words, size) > b - a - 4:
            words = src
        w = pdfdoc.width(words, size)
        if w > (R - L - 4 if alone else b - a - 4):
            return None
        x1 = max(L + 2, min(R - 2 - w, (a + b - w) / 2)) + w
    else:
        x1 = R - 2
        if x1 - pdfdoc.width(words, size) < L:
            words = src
    x0, cap = x1 - pdfdoc.width(words, size), size * 0.72
    top, foot = min(T, Y(hi)) if hi is not None else T, max(B, Y(lo)) if lo is not None else B
    at = [yb for yb in ([top - cap - 1.5, foot + 1.5] if top - foot >= cap + 4 else []) + [top + 1.5, foot - cap - 1.5]
          if B + 0.5 <= yb and yb + cap <= T and not any(p0 - 3 < x1 and x0 < p1 + 3 and abs(pb - yb) < cap + 3 for p0, p1, pb in taken)]
    clear = [yb for yb in at if not any(x0 - 3 < mx < x1 + 3 and yb - 3 < my < yb + cap + 3 for mx, my in marks)]
    if at:
        page.text_right(x1, (clear or at)[0], words, size, False, MUTED)
        return x0, x1, (clear or at)[0]
    return None


def _chart_h(names):
    return 8 + 9 * (len(names) - 1) + 7 + CHART_H + 16


def _charts(lay, rows, note):
    """The trends' charts (#302), CHART_COLS a row, each row kept whole on a page: those of two results or more; note
    under them (notes()'s "charts")."""
    rows = [r for r in rows if sum(1 for p in r["spark"] if p[1] is not None) > 1]
    gap = 12
    lay.y -= 4
    cw = (pdfdoc.WIDTH - 2 * MARGIN - gap * (CHART_COLS - 1)) / CHART_COLS
    for i in range(0, len(rows), CHART_COLS):
        part = rows[i:i + CHART_COLS]
        names = [pdfdoc.wrap(r["label"] + (f" ({units.said(r['unit'])})" if r["unit"] else ""), 8, cw, True)[:2] for r in part]
        h = max(_chart_h(n) for n in names)
        lay.need(h)
        for j, (r, n) in enumerate(zip(part, names)):
            _chart(lay.page, r, n, MARGIN + j * (cw + gap), lay.y + 6, cw)
        lay.y -= h
    if rows and note:   # what the band is (#649, #752): a range from settings said as such, the band isn't the report's there
        lay.note(note, 7.5)


# the trends table's columns: Now is wide enough for a value with its flag in words and a date from an earlier year,
# "123.4 High  Sep 30, 2024", so a black-and-white print still shows which results were out of range (#213). The
# PDF's standard fonts have no ▲ ▼ (Windows-1252 lacks them), so the flag is said in words.
TREND_COLS = [("TEST", 114), ("THEN", 88), ("NOW", 112), ("CHANGE", 38), ("SINCE", 28), ("", 52), ("NOW'S RANGE", None)]


def now_cell(now, none="no new result"):
    """The trends table's Now cell, and its Then cell (#649): the value, its flag in words when out of range (bold and
    red: a tuple), the date, and the range it's judged against where that isn't the row's (#717, #752: at_cell's own,
    on a line of its own, "4 Low  Aug 15" over "(settings range 5–20)", so the date isn't broken)."""
    if not now:
        return none
    own = f"\n({' '.join(w for w in now['own'] if w)})" if now.get("own") else ""
    if now.get("flag"):
        return (f"{now['value']} {now['flag']}  {_short(now['date'])}{own}",)
    return f"{now['value']}  {_short(now['date'])}{own}"


def _when(d):
    return fmt_date(d) if d else ""


def render(s):
    """The summary object as a PDF: (bytes, {"pages", "fits", "left_out": {section: rows not shown}, "charts"}). The
    trends' charts (#302) only where everything fits with them: else it's made again without them (charts false), so
    they never push the medications or the questions off the pages; the preview follows."""
    token = _DATES.set((s.get("date_format") or "", int(str(s.get("prepared") or "0")[:4] or 0) or None))
    try:
        out = _render(s, True)
        return out if out[1]["fits"] else _render(s, False)
    finally:
        _DATES.reset(token)


def _render(s, charts):
    doc = pdfdoc.Document(title=f"Visit summary for {s['profile']}")
    lay = _Layout(doc, s)
    p, W = lay.page, pdfdoc.WIDTH
    p.text(MARGIN, lay.y, "Visit summary", 20, True, INK)
    p.text_right(W - MARGIN, lay.y + 4, f"Prepared {fmt_date(s['prepared'])}", 9, False, MUTED)
    p.text_right(W - MARGIN, lay.y - 8, "from the records in Sanguity", 9, False, MUTED)
    lay.y -= 20
    p.text(MARGIN, lay.y, s["profile"], 13, True, INK)
    lay.y -= 15
    a = s.get("for")
    who = " · ".join(x for x in ((a or {}).get("who"), (a or {}).get("where")) if x)
    since = s["since"]
    # each wrapped as a cell is, never cut (#540: "For: Pediatric hematology … on Nov 25, 2026 · Dr. Alexandra Rivera…"
    # lost the clinician and the place)
    for line in [*pdfdoc.wrap(f"For: {a['title']} on {fmt_date(a['date'])}" + (f" · {who}" if who else "") if a
                              else "For: no particular appointment", 10, W - 2 * MARGIN),
                 *pdfdoc.wrap(f"Since: {fmt_date(since['date'])}" + (f" ({since['label']})" if since.get("label") else ""),
                              10, W - 2 * MARGIN)]:
        p.text(MARGIN, lay.y, line, 10, False, INK)
        lay.y -= 13
    lay.y += 5

    labels, said = dict(sections()), s.get("notes") or notes(s)
    for sid in s["sections"]:
        try:
            if sid == "out_of_range":
                rows = s["out_of_range"]
                flagged = ranges.flagged_words([r["flag"] for r in rows], "short")   # "Out of range or flagged" with a ◆ (#819)
                lay.heading(f"{flagged[0].upper()}{flagged[1:]} since {fmt_date(since['date'])}", len(rows))
                if not rows:
                    lay.note("No results since then are outside the reference range on their own report.")
                    continue
                lay.table(sid, [("TEST", 140), ("RESULT", 72), ("FLAG", 42), ("RANGE", 96), ("DATE", 66), ("SOURCE", None)],
                          rows, lambda r: [r["label"] + (f"  ({r['out']} of {r['of']})" if r["of"] > 1 else ""),
                                           f"{r['value']} {units.said(r['unit'])}".strip(), (r["flag"],), r["range"] or "not given",
                                           _when(r["date"]), r["source"]])
                lay.note(said["out_of_range"], 7.5)   # what each is judged against (#717, #752), as the preview says it
                if any(r["range"].endswith("(settings)") for r in rows):
                    lay.note(SETTINGS_NOTE, 7.5)
            elif sid == "trends":
                rows = s["trends"]
                lay.heading(f"Trends since {fmt_date(since['date'])}", len(rows))
                if not rows:
                    lay.note("None of the key labs has results. Pick labs on the summary page, or set the key labs in Settings.")
                    continue
                lay.table(sid, TREND_COLS,
                          rows, lambda r: [f"{r['label']}" + (f" ({units.said(r['unit'])})" if r["unit"] else ""),
                                           now_cell(r["then"], "none before"),
                                           now_cell(r["now"]), r["change"] or "", str(r["since"]), None,
                                           (r["now"] or {}).get("range") or ""],
                          _spark)
                # what each flag is judged against (#717: "marked so", and none was; #752), and why a change isn't given (#718)
                lay.note(said["trends"], 7.5)
                if any(map(uses_settings, rows)):   # its Now's range, or its sparkline's and chart's band (#649)
                    lay.note(SETTINGS_NOTE, 7.5)
                if charts:
                    _charts(lay, rows, said.get("charts"))
            elif sid == "medications":
                rows = s["medications"]
                lay.heading("Current medications", len(rows))
                if not rows:
                    lay.note("No current medications in the records.")
                    continue
                lay.table(sid, [("MEDICATION", 180), ("INSTRUCTIONS", 150), ("SINCE", 66), ("SOURCE", None)], rows,
                          lambda r: [r["name"], r["instructions"], _when(r["since"]), r["source"]])
            elif sid == "appointments":
                rows = s["appointments"]
                lay.heading("Upcoming appointments", len(rows))
                if not rows:
                    lay.note("No upcoming appointments in the records.")
                    continue
                # this visit's date in ink, as the others: red (a cell's tuple) is for out of range only (#299)
                lay.table(sid, [("DATE", 72), ("APPOINTMENT", 170), ("WITH", 130), ("WHERE", None)], rows,
                          lambda r: [_when(r["date"]),
                                     r["title"] + ("  (this visit)" if r["this"] else ""), r["who"], r["where"]])
            elif sid == "questions":
                qs = s["questions"]
                lay.heading("My questions", len(qs))
                if not qs:
                    lay.note("No questions written down.")
                for i, q in enumerate(qs):
                    lines = pdfdoc.wrap(q, 10, W - 2 * MARGIN - 20)
                    try:
                        lay.need(13 * len(lines) + 3)
                    except _Full:
                        lay.left_out[sid] = len(qs) - i
                        raise
                    lay.page.text(MARGIN + 2, lay.y, f"{i + 1}.", 10, True, INK)
                    for line in lines:
                        lay.page.text(MARGIN + 20, lay.y, line, 10, False, INK)
                        lay.y -= 13
                    lay.y -= 3
            elif sid in measures.IDS:   # (#225) a block: its lead lines, a table, and a note under it, in words
                b = s["measures"][sid]
                lay.need(26 + 12 * len(b["lead"]) + 27)   # the heading, its lead lines and a row under them, together
                lay.heading(labels.get(sid, sid) + ("" if b["history"] else f" since {fmt_date(since['date'])}"), b["total"])
                for line in b["lead"]:
                    lay.note(line, 9, INK)
                if not b["rows"]:
                    lay.note(b["empty"])
                    continue
                kinds = [k for _, k, _ in b["cols"]]
                lay.table(sid, [(h.upper(), w) for h, _, w in b["cols"]], b["rows"],
                          lambda r, kinds=kinds: [_cell(k, v) for k, v in zip(kinds, r)])
                if b["total"] > len(b["rows"]):
                    lay.note(f"The latest {len(b['rows'])} of {b['total']:,}; Sanguity has them all.", 7.5)   # as the lead (#531)
                if b["note"]:
                    lay.note(b["note"], 7.5)
            else:
                ex = next(x for x in s["extra"] if x["id"] == sid)
                lay.heading(labels.get(sid, sid), len(ex["rows"]))
                for i, r in enumerate(ex["rows"]):
                    text = " · ".join(x for x in (_when(r.get("when")), r.get("title"), r.get("text")) if x)
                    lines = pdfdoc.wrap(text, 9.5, W - 2 * MARGIN - 14)   # whole, as a cell is (#540: cut to one line)
                    try:
                        lay.need(13 * len(lines))
                    except _Full:
                        lay.left_out[sid] = len(ex["rows"]) - i
                        raise
                    lay.page.text(MARGIN + 2, lay.y, "•", 9.5, False, INK)
                    for line in lines:
                        lay.page.text(MARGIN + 12, lay.y, line, 9.5, False, INK)
                        lay.y -= 13
        except _Full:   # out of room on the last page: this section's remaining rows and every later section
            for x in s["sections"][s["sections"].index(sid):]:
                lay.left_out.setdefault(x, _count(s, x))
            break
    else:
        # room left on the last page: lines for notes taken at the visit
        if lay.y - FOOT > 70:
            lay.heading("Notes from the visit")
            while lay.y - 20 > FOOT + 6:
                lay.y -= 6
                lay.page.line(MARGIN, lay.y, W - MARGIN, lay.y, 0.4, RULE)
                lay.y -= 16

    n = len(doc.pages)
    for i, pg in enumerate(doc.pages, start=1):
        pg.line(MARGIN, FOOT - 6, W - MARGIN, FOOT - 6, 0.5, RULE)
        y = FOOT - 17
        for line in pdfdoc.wrap(NOTICE, 7.5, W - 2 * MARGIN - 60):
            pg.text(MARGIN, y, line, 7.5, False, MUTED)
            y -= 9.5
        pg.text_right(W - MARGIN, FOOT - 17, f"Page {i} of {n}", 7.5, False, MUTED)
    return doc.output(), {"pages": n, "fits": not lay.left_out, "left_out": lay.left_out, "charts": charts}


def _count(s, sid):
    if sid in ("out_of_range", "trends", "medications", "appointments", "questions"):
        return len(s.get(sid) or [])
    if sid in measures.IDS:
        return len(((s.get("measures") or {}).get(sid) or {}).get("rows") or [])
    return len(next((x["rows"] for x in s.get("extra") or [] if x["id"] == sid), []))


def _short(d):
    """A result's date in a table cell: "Sep 3" this year, "Sep 3, 2024" before."""
    if not d:
        return ""
    x = datetime.date.fromisoformat(d)
    f, year = _DATES.get()
    if f == "ymd" or x.year != (year or today().year):
        return fmt_date(d)
    return f"{x.day} {x.strftime('%b')}" if f == "dmy" else f"{x.strftime('%b')} {x.day}"


def _cell(kind, v):
    """A measures block's cell (#225): a day or a local time written as the other dates are, else its text. A time is on
    the preview's clock (#549: it read "Nov 12 08:30" to the preview's "8:30 AM"), after a comma but in ymd (fmtDT)."""
    if kind == "day" and v:
        return _short(v[:10])
    if kind == "time" and v:
        h = int(v[11:13])
        return f"{_short(v[:10])}{' ' if _DATES.get()[0] == 'ymd' else ', '}{h % 12 or 12}:{v[14:16]} {'AM' if h < 12 else 'PM'}"
    return str(v if v is not None else "")


def filename(profile_name, day):
    slug = re.sub(r"[^A-Za-z0-9]+", "-", profile_name).strip("-")[:40] or "profile"
    return f"visit-summary-{slug}-{day}.pdf"
