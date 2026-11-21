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
                 range is then printed with "(settings)" after it, and the note under the table says what that is
  trends         the key labs (overview.keyLabs) and any the person picked: the last result on or before the
                 "since" date, the latest, the change, and a sparkline of the results between
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
import contextvars, datetime, json, re

from . import crypto, db as dbm, measures, pdfdoc, ranges, records, settings

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


def _pt(p):
    """A result point [time, value, low, high, flag, text, ...] as a dict."""
    p = list(p) + [None] * (7 - len(p)) if isinstance(p, (list, tuple)) else [None] * 7
    return {"t": p[0], "d": _day(p[0]), "v": _num(p[1]), "lo": _num(p[2]), "hi": _num(p[3]),
            "flag": str(p[4] or "").strip(), "text": str(p[5] or "").strip(), "rs": "report"}


def _from_settings(p, name, unit, rcfg):
    """A result with no range and no flag on its report takes the range set in Settings that fits it (#44)."""
    if p["v"] is None or ranges.has_own(p["lo"], p["hi"], p["flag"]):
        return p
    row = ranges.find(rcfg, name, unit, p["d"])
    if row is None:
        return p
    return {**p, "lo": row["low"], "hi": row["high"], "flag": ranges.flag_for(p["v"], row), "rs": ranges.SOURCE}


def out_of_range(p):
    """"High", "Low" or "Abnormal" by the result's own report (its flag, else its own low and high), or None."""
    f = p["flag"].upper()
    if f in ("H", "HH", "HI", "HIGH", ">", "H*", "A>"):
        return "High"
    if f in ("L", "LL", "LO", "LOW", "<", "L*", "A<"):
        return "Low"
    if f in ("N", "NORMAL", "NEG", "NEGATIVE"):
        return None
    if f:
        return "Abnormal"
    if p["v"] is not None:
        if p["hi"] is not None and p["v"] > p["hi"]:
            return "High"
        if p["lo"] is not None and p["v"] < p["lo"]:
            return "Low"
    return None


def range_text(p):
    lo, hi = p["lo"], p["hi"]
    mark = " (settings)" if p.get("rs") == ranges.SOURCE else ""
    if lo is not None and hi is not None:
        return f"{fmt_num(lo)}–{fmt_num(hi)}{mark}"
    if hi is not None:
        return f"≤ {fmt_num(hi)}{mark}"
    if lo is not None:
        return f"≥ {fmt_num(lo)}{mark}"
    return ""


def value_text(p):
    return p["text"] or fmt_num(p["v"])


def _series(data, rcfg=None):
    """{name: {"name", "unit", "pts": [point dict with its source]}}: every import's series of a name put together
    (the same test from two sources is one trend), each result keeping its own range. With two units, the unit of
    the latest result is kept. rcfg: ranges.config(), for results whose report gives no range (#44)."""
    imports = data.get("imports") or {}
    by = {}
    for s in data.get("labs") or []:
        if not isinstance(s, dict) or not s.get("name") or s.get("watch"):
            continue
        src = (imports.get(str(s.get("imp"))) or {}).get("label") or ""
        for raw in s.get("pts") or []:
            p = _pt(raw)
            if p["d"]:
                p = _from_settings(p, s["name"], s.get("unit") or "", rcfg)
                by.setdefault(s["name"], []).append({**p, "unit": s.get("unit") or "", "src": src})
    out = {}
    for name, pts in by.items():
        pts.sort(key=lambda p: str(p["t"]))
        unit = pts[-1]["unit"]
        seen, kept = set(), []
        for p in pts:
            key = (p["t"], p["v"], p["text"])
            if p["unit"] == unit and key not in seen:   # the same result from two sources once
                seen.add(key)
                kept.append(p)
        out[name] = {"name": name, "unit": unit, "pts": kept}
    return out


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
    details, rows = data.get("details") or {}, {}
    for e in data.get("events") or []:
        if e.get("k") != "med" or e.get("hidden"):
            continue
        d = details.get(str(e.get("id"))) or {}
        status = str(d.get("status") or "").strip().lower()
        end = _day(e.get("end") or d.get("end") or d.get("stop"))
        if status and status not in ACTIVE:
            continue
        if not status and end and end < now:
            continue
        name = e.get("title") or d.get("name") or "Medication"
        key = " ".join(name.lower().split())
        row = {"name": name, "instructions": d.get("instructions") or "", "since": _day(e.get("t")) or "",
               "source": d.get("src") or ""}
        have = rows.get(key)   # the same medication from two sources: once, the newest
        if have is None or row["since"] > have["since"]:
            if have and have["source"] and have["source"] != row["source"]:
                row["source"] = ", ".join(x for x in (row["source"], have["source"]) if x)
            rows[key] = row
    return sorted(rows.values(), key=lambda r: r["name"].lower())


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
        for name, s in series.items():
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
        for name in trend_names:
            s = series[name]
            before = [p for p in s["pts"] if p["d"] <= since]
            after = [p for p in s["pts"] if p["d"] > since]
            then = before[-1] if before else None
            last = after[-1] if after else None
            change = None
            if then and last and then["v"] is not None and last["v"] is not None:
                change = last["v"] - then["v"]
            line = ([then] if then else []) + after
            if len(line) < 2:   # too few since: the last few results, for some shape
                line = s["pts"][-6:]
            rows.append({"name": name, "label": nick.get(name, name), "unit": s["unit"],
                         "then": then and {"value": value_text(then), "date": then["d"]},
                         "now": last and {"value": value_text(last), "date": last["d"], "range": range_text(last),
                                          "flag": out_of_range(last)},
                         "change": None if change is None else ("+" if change > 0 else "") + fmt_num(0 if abs(change) < 1e-9 else change),
                         "since": len(after),
                         "spark": [[p["d"], p["v"], bool(out_of_range(p))] for p in line if p["v"] is not None]})
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
    shown = [r["range"] for r in out.get("out_of_range") or []] + [(r["now"] or {}).get("range") or "" for r in out.get("trends") or []]
    if any(x.endswith("(settings)") for x in shown):
        out["settings_note"] = SETTINGS_NOTE   # the page shows it under the tables, as the PDF does
    return out


# ---------------------------------------------------------------- the PDF

MARGIN, TOP, FOOT = 46, 746, 64
INK, MUTED, RULE, SHADE, CRIT = 0.1, 0.42, 0.75, 0.95, (0.66, 0.11, 0.18)


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


def _spark(page, row, x, y, w, h=10):
    pts = row["spark"]
    if len(pts) < 2:
        return
    days = [datetime.date.fromisoformat(d).toordinal() for d, _, _ in pts]
    vs = [v for _, v, _ in pts]
    d0, d1, lo, hi = min(days), max(days), min(vs), max(vs)
    if hi == lo:
        lo, hi = lo - 1, hi + 1
    xy = [(x + (d - d0) / ((d1 - d0) or 1) * w, y + (v - lo) / (hi - lo) * h) for d, v in zip(days, vs)]
    page.polyline(xy, 0.8, MUTED)
    for (px, py), (_, _, bad) in zip(xy, pts):
        page.dot(px, py, 1.5 if bad else 1.0, CRIT if bad else INK)


# the trends table's columns: Now is wide enough for a value with its flag in words and a date from an earlier year,
# "123.4 High  Sep 30, 2024", so a black-and-white print still shows which results were out of range (#213). The
# PDF's standard fonts have no ▲ ▼ (Windows-1252 lacks them), so the flag is said in words.
TREND_COLS = [("TEST", 114), ("THEN", 88), ("NOW", 112), ("CHANGE", 38), ("SINCE", 28), ("", 52), ("NOW'S RANGE", None)]


def now_cell(now):
    """The trends table's Now cell: the value, its flag in words when out of range (bold and red: a tuple), the date."""
    if not now:
        return "no new result"
    if now["flag"]:
        return (f"{now['value']} {now['flag']}  {_short(now['date'])}",)
    return f"{now['value']}  {_short(now['date'])}"


def _when(d):
    return fmt_date(d) if d else ""


def render(s):
    """The summary object as a PDF: (bytes, {"pages", "fits", "left_out": {section: rows not shown}})."""
    token = _DATES.set((s.get("date_format") or "", int(str(s.get("prepared") or "0")[:4] or 0) or None))
    try:
        return _render(s)
    finally:
        _DATES.reset(token)


def _render(s):
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
    p.text(MARGIN, lay.y, pdfdoc.fit(f"For: {a['title']} on {fmt_date(a['date'])}" + (f" · {who}" if who else "") if a
                                     else "For: no particular appointment", 10, W - 2 * MARGIN), 10, False, INK)
    lay.y -= 13
    since = s["since"]
    p.text(MARGIN, lay.y, pdfdoc.fit(f"Since: {fmt_date(since['date'])}" + (f" ({since['label']})" if since.get("label") else ""),
                                     10, W - 2 * MARGIN), 10, False, INK)
    lay.y -= 8

    labels = dict(sections())
    for sid in s["sections"]:
        try:
            if sid == "out_of_range":
                rows = s["out_of_range"]
                lay.heading(f"Out of range since {fmt_date(since['date'])}", len(rows))
                if not rows:
                    lay.note("No results since then are outside the reference range on their own report.")
                    continue
                lay.table(sid, [("TEST", 140), ("RESULT", 72), ("FLAG", 42), ("RANGE", 96), ("DATE", 66), ("SOURCE", None)],
                          rows, lambda r: [r["label"] + (f"  ({r['out']} of {r['of']})" if r["of"] > 1 else ""),
                                           f"{r['value']} {r['unit']}".strip(), (r["flag"],), r["range"] or "not given",
                                           _when(r["date"]), r["source"]])
                lay.note("(2 of 3): how many of the results since then were out of range. Each is judged against the "
                         "range printed on its own report.", 7.5)
                if any(r["range"].endswith("(settings)") for r in rows):
                    lay.note(SETTINGS_NOTE, 7.5)
            elif sid == "trends":
                rows = s["trends"]
                lay.heading(f"Trends since {fmt_date(since['date'])}", len(rows))
                if not rows:
                    lay.note("None of the key labs has results. Pick labs on the summary page, or set the key labs in Settings.")
                    continue
                lay.table(sid, TREND_COLS,
                          rows, lambda r: [f"{r['label']}" + (f" ({r['unit']})" if r["unit"] else ""),
                                           f"{r['then']['value']}  {_short(r['then']['date'])}" if r["then"] else "none before",
                                           now_cell(r["now"]), r["change"] or "", str(r["since"]), None,
                                           (r["now"] or {}).get("range") or ""],
                          _spark)
                lay.note("Then: the last result on or before that day. Since: how many results since. High or Low (in "
                         "red): outside the range on that result's own report.", 7.5)
                if any(((r["now"] or {}).get("range") or "").endswith("(settings)") for r in rows):
                    lay.note(SETTINGS_NOTE, 7.5)
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
                lay.table(sid, [("DATE", 72), ("APPOINTMENT", 170), ("WITH", 130), ("WHERE", None)], rows,
                          lambda r: [(_when(r["date"]),) if r["this"] else _when(r["date"]),
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
                    try:
                        lay.need(13)
                    except _Full:
                        lay.left_out[sid] = len(ex["rows"]) - i
                        raise
                    lay.page.text(MARGIN + 2, lay.y, pdfdoc.fit("• " + text, 9.5, W - 2 * MARGIN - 4), 9.5, False, INK)
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
    return doc.output(), {"pages": n, "fits": not lay.left_out, "left_out": lay.left_out}


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
    """A measures block's cell (#225): a day or a local time written as the other dates are, else its text."""
    if kind == "day" and v:
        return _short(v[:10])
    if kind == "time" and v:
        return f"{_short(v[:10])} {v[11:16]}"
    return str(v if v is not None else "")


def filename(profile_name, day):
    slug = re.sub(r"[^A-Za-z0-9]+", "-", profile_name).strip("-")[:40] or "profile"
    return f"visit-summary-{slug}-{day}.pdf"
