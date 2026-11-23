"""The phone's and watch's measures, and the newer kinds of record, as sections of the visit summary (#48) and of
share links (#47): issue #225. Each is offered only when the profile has some of it, and left unticked by default:
the person ticks what they want a clinician or relative to see.

  home_bp        blood pressure taken at home (#180): the readings' count, average and highest, and the readings
  glucose        blood glucose from the phone (#181): each day's lowest to highest and average, a meter's or a
                 continuous monitor's (CGM); for CGM days the share of readings in the standard published ranges,
                 labelled as the standard, never as anyone's targets; and each meter reading typed in at home (#224,
                 #232), with its meal note
  temperature    body temperature taken at home (#184): count, average, highest, and the readings
  oxygen         the watch's daily blood oxygen (#184): average and lowest; and each reading a device sent one at a
                 time or typed in at home (#224, #232), with its time and its device and app
  respiratory    the watch's daily respiratory rate (#226)
  vo2max         the watch's cardio fitness estimates (#226)
  immunizations  the vaccines the records say were given (#179), as a list
  ecg            ECGs and irregular rhythm notifications (#182), by ecg.shared_rows: what the device said each one
                 showed and which device it was, never the samples
  bmi            BMI from each weight and the height in effect on its day (#195, person.bmi), the number only; for a
                 child, where each weight, height and BMI falls on the WHO's or the CDC's growth charts (#196,
                 server/growth.py)
  targets        targets of the person's own for home readings (#234, server/targets.py), as their care team gave
                 them: each with who set it and when, and how many of the period's readings at home were above or
                 below it (for CGM, the time in the person's own range beside the standard one, each labelled)

  medlog         the medication log (#235, server/med_log.py): each dose logged, typed in or sent by the phone
                 (Apple Health's Medications, with its app and the dose taken), with what happened and its note; a
                 line per medication counting what was logged ("12 of 14 logged doses taken"), never what was expected
Cycle tracking (#183) isn't here: the visit summary has its own section for it (cycle_tracking.py), offered only to
owners and editors while it's on and ticked each time, and share links never offer it.

Each section is a block, the same for the summary's page and PDF and for a share link's page:
  {"lead": [lines], "cols": [[heading, kind, PDF width]], "rows": [[cell]], "total": rows before any were cut,
   "note": a line under the table, "empty": what to say with no rows, "history": a list of everything, not a period,
   "of": what the rows are, for "The latest 20 of 1,381 readings." (#531: "days and readings" where they mix)}
A cell of kind "day" is YYYY-MM-DD, "time" a local wall time YYYY-MM-DDTHH:MM (in the watch summary's own zone, as
the phone took it; a reading typed in is a wall time already), "text" as it is; each page writes dates in its own
way. Only the fields named here leave the server. Values are in the units of the person making it (Settings → Units,
#41): glucose in mg/dL or mmol/L, temperatures in °C or °F, weights in lb or kg, heights in cm or feet and inches.
A table's columns can follow what it has (#232): glucose without CGM days has no time-in-ranges columns, and blood
oxygen has a Readings column only when some were typed in.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import ecg, glucose, growth, med_log, medications, person, targets as tg, watch_store

FROM = "the device and app that sent it (or “Typed in”)"   # #581: where each reading came from, as its page says it
SECTIONS = [   # (id, label, what a share link shows: said on the owner's form and the link's page; #576, #581: everything it shows)
    ("home_bp", "Blood pressure at home", "each reading taken at home, sent by a phone app or typed in: the date and time, "
                                          f"both pressures, the pulse and {FROM}"),   # #571: as its page says
    ("glucose", "Blood glucose at home", "each day's readings sent by a phone app, a meter's or a continuous monitor's (CGM): "
                                         "the lowest and highest, the average and how many readings there were (a CGM's in how "
                                         "many hours worn), and for CGM days the time in the standard published ranges; and "
                                         "each reading typed in at home, with its time and meal note"),
    ("temperature", "Body temperature at home", "each reading taken at home, sent by a phone app or typed in: the date and time, "
                                                f"the temperature and {FROM}"),
    ("oxygen", "Blood oxygen at home", "the watch's average and lowest reading each day, sent by a phone app; and each reading "
                                       f"sent one at a time or typed in at home, with its time and {FROM}"),
    ("respiratory", "Respiratory rate from the watch", "each day's rate from the watch or phone, in breaths a minute"),
    ("vo2max", "Cardio fitness (VO2 max)", "each estimate the watch made, with its date"),
    ("immunizations", "Immunizations", "each vaccine the records say was given: the date, the vaccine, the dose and where or by whom"),
    ("ecg", "ECGs and irregular rhythm notifications", "the date, what the watch said each recording or notification showed, a "
                                                       "recording's average heart rate, and the device that made it; not the "
                                                       "traces themselves"),
    ("bmi", "BMI and growth", "each weight with the height in effect on its day and the BMI from them, and where each "
                              "weight came from (the smart scale, or a clinic visit and its clinic); for a child, where each "
                              "measurement falls on the WHO's or the CDC's growth charts"),
    ("targets", "Your targets for home readings", "each target typed in as the care team gave it, who set it and when, and how many "
                                                  "readings at home, or days of steps counted by the phone, were above or "
                                                  "below it"),   # #234; a steps target's days (#298, #650)

    ("medlog", "Medication log", "each dose logged as taken, taken late or skipped: the date and time, the medication, its "
                                 "note, and where it came from: “Typed in”, or the app that sent it and the dose taken"),   # (#235)
]
IDS = [s for s, _, _ in SECTIONS]
LABELS = {s: l for s, l, _ in SECTIONS}
HISTORY = {"immunizations"}   # a visit summary lists them all, not only those since the last visit
SUMMARY_MOST = 12             # rows listed in a visit summary (the lead line counts them all)
LINK_MOST = 1000              # and on a share link's page
STD_NOTE = ("Time in ranges: the share of the day's CGM readings in the standard published ranges for CGM ({} {}, with "
            "below {} and below {} counted apart; the international consensus of 2019). Not advice, and not anyone's own targets.")


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _n(v, places=0):
    if v is None:
        return ""
    s = f"{v:.{places}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def _day(t):
    t = str(t or "")
    return t[:10] if len(t) >= 10 and t[4:5] == "-" and t[7:8] == "-" else None


def _s(n, word):   # "1,381 readings", as a share link's note writes the count (#531)
    return f"{n:,} {word}{'' if n == 1 else 's'}"


class Ctx:
    """What the sections are made from: the watch summary (watch_store.load), the records, the merged settings."""
    def __init__(self, watch, data, cfg):
        self.watch, self.data, self.cfg = watch or {}, data or {}, cfg or {}
        self.metrics = {m.get("key"): m for m in self.watch.get("metrics") or [] if isinstance(m, dict)}
        try:
            self.tz = ZoneInfo(str(self.watch.get("tz") or "UTC"))
        except (ZoneInfoNotFoundError, ValueError):
            self.tz = timezone.utc
        self.units = self.cfg.get("units") or {}
        self.person = person.of(self.cfg)

    def local(self, t):
        """A reading's time as the local wall time it was taken, YYYY-MM-DDTHH:MM, or None: a phone's (an instant, ISO
        with its offset) in the summary's zone; one typed in (#224) or a meter's (no offset) is a wall time already,
        as the page reads it (dates.js zParse) (#232)."""
        try:
            x = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
        except ValueError:
            return None
        return (x.astimezone(self.tz) if x.tzinfo else x).strftime("%Y-%m-%dT%H:%M")

    def pts(self, key):
        """A daily metric's [[day, value]], oldest first (numbers only)."""
        m = self.metrics.get(key) or {}
        return [(str(d), v) for d, v in m.get("pts") or [] if _day(d) and _num(v) is not None]


def context(db, pid, data, cfg):
    try:
        watch = watch_store.load(db, pid)
    except (OSError, ValueError):
        watch = None
    return Ctx(watch, data, cfg)


def _block(cols, rows, lead=(), note="", empty="", most=None, history=False, of="readings"):
    total = len(rows)
    return {"lead": [x for x in lead if x], "cols": [list(c) for c in cols], "rows": rows[:most] if most else rows,
            "total": total, "note": note, "empty": empty, "history": history, "of": of}


def _src(name, app):
    return f"{name} ({app})" if name and app else name or app or "phone"


# ---------------------------------------------------------------- each section

TYPED = "Typed in"   # the app of a reading typed in on Sources (#224), as health.js's TYPED
G_MEAL = {"before": "before a meal", "after": "after a meal", "other": "other"}   # a typed glucose's note (health.js G_MEAL)


def _minute(t, tz=timezone.utc):
    """Minutes since the epoch of an ISO time, or None; one without an offset (typed in, or a meter's) is a wall time in tz."""
    try:
        x = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
    except ValueError:
        return None
    return int((x if x.tzinfo else x.replace(tzinfo=tz)).timestamp() // 60)


def _once_typed(rows, values, app, tz=timezone.utc, within=.05):
    """rows less each one typed in (#224) that a phone sent too: the same values (within `within`) within a minute, as
    the page shows it once (health.js onceTyped). values: the indexes of a row's values; app: the index of its app;
    tz: the summary's zone, for times without an offset."""
    sent = {}
    for r in rows:
        if r[app] != TYPED and _minute(r[0], tz) is not None:
            sent.setdefault(_minute(r[0], tz), []).append(r)
    def also_sent(r):
        m = _minute(r[0], tz)
        return m is not None and any(all(_num(p[i]) is not None and _num(r[i]) is not None and abs(_num(p[i]) - _num(r[i])) < within
                                         for i in values) for d in (0, -1, 1) for p in sent.get(m + d, []))
    return [r for r in rows if r[app] != TYPED or not also_sent(r)]


def _rows(c, readings, app):
    """A readings list's rows (bp_readings, temp_readings, spo2_readings, glucose_readings), padded to the app's index."""
    return [list(r) + [None] * (app + 1 - len(r)) for r in c.watch.get(readings) or [] if isinstance(r, list) and len(r) >= 2]


def _home(c, readings, keep, values, app, rows=None, within=.05):
    """[(local time, reading)] of a vitals list (bp_readings, temp_readings, spo2_readings) in the period, newest first,
    a reading typed in and sent by a phone counted once. rows: those to use instead of the list's own."""
    rows = _rows(c, readings, app) if rows is None else rows
    out = []
    for r in _once_typed(rows, values, app, c.tz, within):
        t = c.local(r[0])
        if t and keep(t[:10]):
            out.append((t, r))
    return sorted(out, key=lambda x: x[0], reverse=True)


def _bp(c, keep):
    """[(local time, systolic, diastolic, pulse, device)] taken at home in the period, newest first."""
    rs = []
    for t, r in _home(c, "bp_readings", keep, (1, 2), 5):
        r = list(r) + [None] * (6 - len(r))
        s, d = _num(r[1]), _num(r[2])
        if s is not None and d is not None:
            rs.append((t, s, d, _num(r[3]), _src(r[4], r[5])))
    return rs


def home_bp(c, keep, most):
    rs = _bp(c, keep)
    lead = []
    if rs:
        n = len(rs)
        lead.append(f"{_s(n, 'reading')} · average {_n(sum(x[1] for x in rs) / n)}/{_n(sum(x[2] for x in rs) / n)} mmHg · "
                    f"highest systolic {_n(max(x[1] for x in rs))}, highest diastolic {_n(max(x[2] for x in rs))}")
    return _block([("Date and time", "time", 110), ("Systolic/diastolic (mmHg)", "text", 130), ("Pulse (bpm)", "text", 70),
                   ("Device", "text", None)],
                  [[t, f"{_n(s)}/{_n(d)}", _n(p), src] for t, s, d, p, src in rs], lead,
                  "Taken at home, sent by a phone app or typed in, as the device measured them. Readings taken at visits are in the records.",
                  "No readings at home in this period.", most)


def temperature(c, keep, most):
    f = c.units.get("temperature") == "F"
    unit = "°F" if f else "°C"
    rs = []
    for t, r in _home(c, "temp_readings", keep, (1,), 3):
        r = list(r) + [None] * (4 - len(r))
        v = _num(r[1])
        if v is not None:
            rs.append((t, v * 9 / 5 + 32 if f else v, _src(r[2], r[3])))
    lead = []
    if rs:
        n = len(rs)
        lead.append(f"{_s(n, 'reading')} · average {_n(sum(x[1] for x in rs) / n, 1)} {unit} · highest {_n(max(x[1] for x in rs), 1)} {unit}")
    return _block([("Date and time", "time", 110), (f"Temperature ({unit})", "text", 110), ("Device", "text", None)],
                  [[t, _n(v, 1), src] for t, v, src in rs], lead, "Taken at home, sent by a phone app or typed in, as the thermometer measured them.",
                  "No readings at home in this period.", most)


def oxygen(c, keep, most):
    low = dict(c.pts("spo2_min"))
    days = [(d, v, _num(low.get(d))) for d, v in c.pts("spo2_avg") if keep(d)][::-1]
    lead = []
    if days:
        lows = [x[2] for x in days if x[2] is not None]
        lead.append(f"{_s(len(days), 'day')} · average {_n(sum(x[1] for x in days) / len(days), 1)}%" +
                    (f" · lowest {_n(min(lows), 1)}%" if lows else ""))
    # readings one at a time (#232): typed in at home (#224), [time, %, source, app], one a phone sent too once
    ones = [(t, _num(r[1]), r) for t, r in _home(c, "spo2_readings", keep, (1,), 3) if _num(r[1]) is not None]
    if not ones:   # the watch's days only: as before
        return _block([("Date", "day", 90), ("Average (%)", "text", 90), ("Lowest (%)", "text", None)],
                      [[d, _n(v, 1), _n(lo, 1)] for d, v, lo in days], lead, "The watch's readings through each day (SpO2), as it "
                      "measured them, sent by a phone app.", "No blood oxygen in this period.", most, of="days")
    vs, typed = [v for _, v, _ in ones], all(r[3] == TYPED for _, _, r in ones)
    lead.append(f"{_s(len(ones), 'reading')} {'typed in' if typed else 'one at a time'} · average {_n(sum(vs) / len(vs), 1)}%" +
                (f" · lowest {_n(min(vs), 1)}%" if len(vs) > 1 else ""))
    rows = [[d, "Watch, the day's average", _n(v, 1), _n(lo, 1), d + "T~"] for d, v, lo in days]
    rows += [[t[:10], f"{_src(r[2], r[3])}, {t[11:16]}", _n(v, 1), "", t] for t, v, r in ones]
    rows.sort(key=lambda x: x[4], reverse=True)   # newest first; a day's average above that day's readings
    return _block([("Date", "day", 90), ("Readings", "text", 150), ("SpO2 (%)", "text", 80), ("Lowest (%)", "text", None)],
                  [x[:4] for x in rows], lead, "Sent by a phone app (the watch's readings through each day, as it measured them) "
                  "or typed in at home, one reading at a time.", "No blood oxygen in this period.", most,
                  of="days and readings" if days else "readings")


def _daily(c, key, keep, most, what, note):
    m = c.metrics.get(key) or {}
    unit = str(m.get("unit") or "")
    days = [(d, v) for d, v in c.pts(key) if keep(d)][::-1]
    lead = []
    if days:
        vs = [v for _, v in days]
        lead.append(f"{_s(len(days), 'day')} · average {_n(sum(vs) / len(vs), 1)} {unit}" +
                    (f" · lowest {_n(min(vs), 1)}, highest {_n(max(vs), 1)}" if len(vs) > 1 else ""))
    return _block([("Date", "day", 90), (f"{what} ({unit})" if unit else what, "text", None)],
                  [[d, _n(v, 1)] for d, v in days], lead, note, "None from the watch in this period.", most, of="days")


def respiratory(c, keep, most):
    return _daily(c, "resp", keep, most, "Rate", "The watch's or phone's daily respiratory rate, as it measured it.")


def vo2max(c, keep, most):
    return _daily(c, "vo2max", keep, most, "VO2 max", "Estimated by the watch from heart rate and movement, as it gave them.")


def _pct(v):
    if v is None:
        return ""
    if 0 < v < 0.05:
        return "under 0.1%"
    return (_n(v, 1) if 0 < v < 9.95 else _n(round(v))) + "%"


def _meter(c, keep, mmol):
    """[(local time, row)] of a meter's readings in the period, the phone's ([local time, mg/dL]) and those typed in at
    home (#224, #232: [time, mg/dL, source, app, meal]), newest first, one typed that the phone sent too once (as the
    page: the same within .5 mg/dL, or .05 mmol/L)."""
    phone = [[str(w), v, None, "phone"] for w, v in (c.metrics.get("glucose_meter") or {}).get("pts") or []
             if _day(w) and _num(v) is not None]
    typed = [r for r in _rows(c, "glucose_readings", 4) if _num(r[1]) is not None]   # a phone's among them would be a meter's
    return _home(c, None, keep, (1,), 3, rows=phone + typed, within=.05 * glucose.MGDL_PER_MMOL if mmol else .5)


def glucose_block(c, keep, most):
    mmol = c.units.get("lab") == "si"
    unit = "mmol/L" if mmol else "mg/dL"
    cv = lambda v: "" if _num(v) is None else _n(v / glucose.MGDL_PER_MMOL, 1) if mmol else _n(v)
    s0, s1, s2 = ("3.0", "3.9", "10.0") if mmol else ("54", "70", "180")
    col = {k: dict(c.pts("glucose_" + k)) for k in ("avg", "min", "max", "n", "hours", "lt54", "lt70", "in", "gt180")}
    # a meter's readings, the phone's ([local time, mg/dL]) and those typed in at home (#224, #232: [time, mg/dL, source,
    # app, meal]), one typed that the phone sent too once (as the page: the same within .5 mg/dL, or .05 mmol/L)
    meter, ones = {}, []
    for t, r in _meter(c, keep, mmol):
        if r[3] == TYPED:
            ones.append((t, r[1], G_MEAL.get(r[4], "")))
        else:
            meter.setdefault(t[:10], []).append(r[1])
    cgm = sorted(d for d in col["avg"] if keep(d))
    rows = []
    for d in sorted(set(cgm) | set(meter) | {t[:10] for t, _, _ in ones}, reverse=True):
        if d in col["avg"] and keep(d):
            n, h = int(col["n"].get(d) or 0), _num(col["hours"].get(d))
            rows.append([d, f"CGM: {n:,}" +(f" in {_n(h, 1)} h" if h is not None else ""),
                         f"{cv(col['min'].get(d))}–{cv(col['max'].get(d))}", cv(col["avg"][d]), _pct(col["in"].get(d)),
                         _pct(col["lt70"].get(d)), _pct(col["lt54"].get(d)), _pct(col["gt180"].get(d))])
        if d in meter:
            vs = meter[d]
            rows.append([d, f"Meter: {len(vs):,}",f"{cv(min(vs))}–{cv(max(vs))}" if len(vs) > 1 else cv(vs[0]),
                         cv(sum(vs) / len(vs)), "", "", "", ""])
        for t, v, meal in (x for x in ones if x[0][:10] == d):   # each typed in, newest first, with its meal note
            rows.append([d, f"Typed in, {t[11:16]}" + (f", {meal}" if meal else ""), cv(v), cv(v), "", "", "", ""])
    lead = []
    if cgm:   # the period's CGM days together, each day weighed by its readings
        n = sum(int(col["n"].get(d) or 0) for d in cgm)
        share = (lambda k: sum((col[k].get(d) or 0) * int(col["n"].get(d) or 0) for d in cgm) / n) if n else (lambda k: None)
        lead.append(f"{_s(len(cgm), 'CGM day')} · average {cv(share('avg')) if n else ''} {unit} · time in the standard ranges: "
                    f"{_pct(share('in'))} {s1}–{s2}, {_pct(share('lt70'))} below {s1} ({_pct(share('lt54'))} below {s0}), "
                    f"{_pct(share('gt180'))} above {s2}")
    if meter or ones:
        vs, k = [v for x in meter.values() for v in x] + [v for _, v, _ in ones], len(ones)
        lead.append(f"{_s(len(vs), 'meter reading')}" + (" typed in" if k == len(vs) else f" ({k} typed in)" if k else "") +
                    f" · average {cv(sum(vs) / len(vs))} {unit} · lowest {cv(min(vs))}, highest {cv(max(vs))}")
    note = "Sent by a phone app or typed in at home, as the meter or monitor measured them."
    # a CGM's or a meter's day a row, a typed reading a row of its own; mixed, "336 CGM days · 728 meter readings"
    of = "readings" if not cgm and not meter else "days and readings" if ones or cgm and meter else "days"
    if not cgm:   # a meter's readings only (#232): no CGM columns, room for each typed reading's time and meal note
        return _block([("Date", "day", 60), ("Readings", "text", 180), (f"Low–high ({unit})", "text", 100), (f"Average ({unit})", "text", None)],
                      [r[:4] for r in rows], lead, note.replace("meter or monitor", "meter (finger-stick)"),
                      "No glucose in this period.", most, of=of)
    return _block([("Date", "day", 60), ("Readings", "text", 86), (f"Low–high ({unit})", "text", 86), ("Average", "text", 46),
                   (f"{s1}–{s2}", "text", 56), (f"Below {s1}", "text", 56), (f"Below {s0}", "text", 56), (f"Above {s2}", "text", None)],
                  rows, lead, STD_NOTE.format(f"{s1}–{s2}", unit, s1, s0) + " " + note, "No glucose in this period.", most, of=of)


def immunizations(c, keep, most):
    details, rows = c.data.get("details") or {}, []
    for e in c.data.get("events") or []:
        if not isinstance(e, dict) or e.get("k") != "immunization" or e.get("hidden"):
            continue
        d = _day(e.get("t"))
        if not d or not keep(d):
            continue
        x = details.get(str(e.get("id"))) or {}
        rows.append([d, str(e.get("title") or "Immunization")[:200], str(x.get("dose") or "")[:60],
                     str(x.get("where") or x.get("performer") or "")[:200]])
    rows.sort(key=lambda r: r[0], reverse=True)
    return _block([("Date", "day", 80), ("Vaccine", "text", 220), ("Dose", "text", 60), ("Where or by whom", "text", None)],
                  rows, (), "As the records say them.", "No immunizations in the records.", most, history=True, of="vaccines")


def ecg_block(c, keep, most):
    rows = [[r["when"], r["title"], r["text"]] for r in ecg.shared_rows(c.watch) if keep(r["when"])]
    return _block([("Date", "day", 80), ("What the watch said", "text", 220), ("Details", "text", None)], rows, (),
                  "As the watch classified each one; the traces themselves aren't included.",
                  "No ECGs or irregular rhythm notifications in this period.", most, of="ECGs and notifications")


def _weights(c):
    """[(day, kg, where)] oldest first: the smart scale's days, and the clinic visits' weights on other days."""
    m = c.metrics.get("weight") or {}
    kg = person.LB_KG if str(m.get("unit") or "lb") == "lb" else 1
    out = {d: (v * kg, "smart scale") for d, v in c.pts("weight") if v > 0}
    for w in c.data.get("clinicWeights") or []:
        if isinstance(w, list) and len(w) >= 2 and _day(w[0]) and _num(w[1]) and w[1] > 0 and _day(w[0]) not in out:
            out[_day(w[0])] = (w[1] * person.LB_KG, f"clinic visit ({w[2]})" if len(w) > 2 and w[2] else "clinic visit")
    return sorted((d, k, s) for d, (k, s) in out.items())


def bmi(c, keep, most):
    P, T = c.person, None
    kg_shown = c.units.get("weight") == "kg"
    w_text = lambda kg: f"{_n(kg, 1)} kg" if kg_shown else f"{_n(kg / person.LB_KG, 1)} lb"
    h_unit = "ftin" if c.units.get("height") == "ftin" else "cm"
    sexed = P["sex"] in growth.SEX_CODE and P["birthDate"]
    rows, child = [], False

    def pct(key, day, x):
        nonlocal T
        a = growth.age(day, P) if sexed else None
        if a is None:
            return None, ""
        T = T or growth.tables()
        return a, growth.pct_text(growth.pct(T, key, P["sex"], a["days"], x))

    for d, kg, src in _weights(c):
        if not keep(d):
            continue
        age = person.age_on(P["birthDate"], d) if P["birthDate"] else None
        a, p = pct("weight", d, kg)
        if a is not None:   # a child's weight, on the growth charts
            child = True
            rows.append([d, "Weight", w_text(kg), p, f"{src}; {'WHO' if growth.chart(a['days']) == 'who' else 'CDC'} chart"])
        h = person.height_on(P["heights"], d)
        if not h or (age is not None and age < 2):
            continue
        if age is not None and age < 20 and (datetime.fromisoformat(d) - datetime.fromisoformat(h["date"])).days > 92:
            continue   # a child grows: a height from months before isn't theirs now (as on the page)
        v = person.bmi(kg, "kg", h["cm"])
        if v is None:
            continue
        a, p = pct("bmi", d, v) if age is not None and age < 20 else (None, "")
        rows.append([d, "BMI", f"{_n(v, 1)} kg/m²", p if a and growth.chart(a["days"]) == "cdc" else "",
                     f"weight {w_text(kg)} ({src}), height {person.text(h['cm'], h_unit)}"])
    for h in P["heights"]:
        if keep(h["date"]):
            a, p = pct("height", h["date"], h["cm"])
            if a is not None:
                child = True
                rows.append([h["date"], "Length" if growth.chart(a["days"]) == "who" else "Height", person.text(h["cm"], h_unit), p,
                             f"About this person; {'WHO' if growth.chart(a['days']) == 'who' else 'CDC'} chart"])
    rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
    bmis = [r for r in rows if r[1] == "BMI"]
    lead = [f"{_s(len(bmis), 'BMI')} from the weights and heights · latest {bmis[0][2]}" if bmis else ""]
    note = ("BMI: each weight with the height in effect on its day (About this person); the number only. Percentiles: where a "
            "child's measurement falls, for age and sex, on the WHO Child Growth Standards (to 2 years) or the CDC growth charts "
            "(2 to 20); not what that means." if child else
            "BMI: each weight with the height in effect on its day (About this person); the number only, with no category.")
    return _block([("Date", "day", 70), ("Measure", "text", 50), ("Value", "text", 70), ("Percentile", "text", 70), ("From", "text", None)],
                  rows, lead, note, "No weights with a height in this period.", most, of="measurements")


# ---------------------------------------------------------------- #234 targets of your own (server/targets.py)

def _outside(sides, what="readings"):
    """"4 of 62 readings above it", "None of 62 outside it", "No readings": the period's, each "high", "low" or ""."""
    if not sides:
        return f"No {what}"
    hi, lo, n = sides.count("high"), sides.count("low"), len(sides)
    if not hi and not lo:
        return f"None of {n:,} outside it"
    w = what if n != 1 else what[:-1]   # "1 of 1 reading above it"; counts as the leads write them (#535)
    if hi and lo:
        return f"{hi:,} of {n:,} {w} above, {lo:,} below"
    return f"{hi or lo:,} of {n:,} {w} {'above' if hi else 'below'} it"


T_NAMES = {"bp": "Blood pressure", "glucose": "Glucose (meter)", "cgm": "CGM time in range", "temp": "Temperature",
           "spo2": "Blood oxygen", "weight": "Weight", "steps": "Steps a day"}


def targets_block(c, keep, most):
    """Each target the person typed in (#234), in the units of the person making it, with the period's readings at home
    against it (compared as they're shown); for CGM, the time in the person's own range beside the standard one, each
    labelled, in a line of its own. Only when the section is ticked; Sanguity never suggests one."""
    T, rows, lead = tg.of(c.cfg), [], []
    mmol, f_, kg = c.units.get("lab") == "si", c.units.get("temperature") == "F", c.units.get("weight") == "kg"
    g = ("mmol/L", "mmol/L", 1) if mmol else ("mg/dL", "mg/dL", 0)
    shown = {"glucose": g, "cgm": g, "temp": ("°F", "F", 1) if f_ else ("°C", "C", 1), "weight": ("kg", "kg", 1) if kg else ("lb", "lb", 1),
             "spo2": ("%", None, 0), "steps": ("steps", None, 0)}   # measure -> (unit written, unit converted to, places)
    for k in tg.MEASURES:
        t = T.get(k)
        if not t:
            continue
        if k == "bp":
            sides = [tg.side(s, t.get("sysLow"), t.get("sysHigh")) or tg.side(d, t.get("diaLow"), t.get("diaHigh")) for _, s, d, _, _ in _bp(c, keep)]
            rows.append([T_NAMES[k], tg.bp_text(t), t.get("by") or "", t.get("on") or "", _outside(sides)])
            continue
        unit, to, pl = shown[k]
        conv = (lambda v, to=to, k=k: tg.from_canonical(k, v, to)) if to else (lambda v: v)
        lo, hi = conv(t.get("low")), conv(t.get("high"))
        text = tg.range_text(lo, hi, (lambda v: f"{v:,.0f}") if k == "steps" else (lambda v, pl=pl: _n(v, pl)), unit)   # "8,000 steps"
        sides = lambda vals: [tg.side(v, lo, hi, pl) for v in vals if v is not None]
        if k == "glucose":
            when = _outside(sides([conv(_num(r[1])) for _, r in _meter(c, keep, mmol)]))
        elif k == "cgm":
            low, high = tg.cgm_bounds(t)
            got = [x for x in (tg.own_counts(v, low, high) for d, v in (c.metrics.get("glucose_dist") or {}).get("pts") or []
                               if _day(d) and keep(d)) if x]
            cgm = [d for d, _ in c.pts("glucose_avg") if keep(d)]
            when = "No CGM days" if not cgm else "Not known for these days" if not got else ""
            if got:
                n = sum(x[3] for x in got)
                pct = lambda i: _pct(100 * sum(x[i] for x in got) / n)
                s_in, s_n = dict(c.pts("glucose_in")), dict(c.pts("glucose_n"))
                sn = sum(int(s_n.get(d) or 0) for d in cgm)
                std = sum((s_in.get(d) or 0) * int(s_n.get(d) or 0) for d in cgm) / sn if sn else None
                s1, s2 = ("3.9", "10.0") if mmol else ("70", "180")
                when = f"{pct(1)} in it, {_s(len(got), 'CGM day')}"
                lead.append(f"CGM time in your own range ({text}, your target): {pct(1)} of readings over {_s(len(got), 'CGM day')}, "
                            f"{pct(0)} below it and {pct(2)} above it" +
                            (f"; in the standard published range ({s1}–{s2} {unit}): {_pct(std)}" if std is not None else "") + "." +
                            (f" {len(cgm) - len(got)} of the CGM days came before Sanguity kept what this needs." if len(cgm) > len(got) else ""))
        elif k == "temp":
            when = _outside(sides([conv(_num(r[1])) for _, r in _home(c, "temp_readings", keep, (1,), 3)]))
        elif k == "steps":   # each day's count from the phone (#298)
            when = _outside(sides([v for d, v in c.pts("steps") if keep(d)]), "days")
        elif k == "spo2":
            when = _outside(sides([v for d, v in c.pts("spo2_avg") if keep(d)] +
                                  [_num(r[1]) for _, r in _home(c, "spo2_readings", keep, (1,), 3)]))
        else:   # weight: the smart scale's days, in the unit shown
            m = c.metrics.get("weight") or {}
            to_kg = person.LB_KG if str(m.get("unit") or "lb") == "lb" else 1
            when = _outside(sides([conv(v * to_kg) for d, v in c.pts("weight") if v > 0 and keep(d)]), "weights")
        rows.append([T_NAMES[k], text, t.get("by") or "", t.get("on") or "", when])
    lead.insert(0, "Each target as the person's care team gave it, typed in by them or their family: Sanguity doesn't suggest targets. "
                   "The readings taken at home in this period" + (", and each day's steps counted by the phone," if T.get("steps") else "") +
                   " are compared with each, as they're shown here." if rows else "")   # as the section's line says (#650)
    return _block([("Measure", "text", 92), ("Your target", "text", 100), ("Set by", "text", 104), ("Set on", "day", 54),
                   ("In this period", "text", None)], rows, lead,
                  "A blood pressure reading is above its target when its systolic or diastolic is; blood oxygen is each day's average "
                  "from the watch and each reading typed in; weight, the smart scale's; steps, each day's count from the phone. Readings taken at visits aren't compared. "
                  "Standard published ranges stay labelled as their publisher's. Not medical advice.",
                  "No targets entered.", most, of="targets")


def _doses(c):
    """[(local wall time, medication, status, note, where from)] of the medication log (#235): typed in (records'
    "doses") and the phone's (the watch summary's "med_doses"), oldest first."""
    out = [(d["t"], d.get("name") or d.get("med") or "", d.get("status"), str(d.get("note") or "")[:1000], "Typed in")
           for d in c.data.get("doses") or [] if isinstance(d, dict) and _day(d.get("t")) and d.get("status") in med_log.STATUSES]
    out += [(d["t"], d["name"], d["status"], "", f"{d['app']}" + (f", {d['dose']}" if d["dose"] else ""))
            for d in med_log.phone_doses(c.watch, c.tz)]
    return sorted(out, key=lambda x: x[0])


def medlog(c, keep, most):
    rows = [d for d in _doses(c) if keep(d[0][:10])]
    lead, by = [], {}
    for d in rows:
        by.setdefault(medications.med_key(d[1]), []).append(d)
    for ds in sorted(by.values(), key=lambda ds: ds[-1][1].lower()):
        lead.append(f"{ds[-1][1]}: " + med_log.said(med_log.counts([{"status": d[2]} for d in ds])))
    rows = rows[::-1]   # newest first
    return _block([("Date and time", "time", 110), ("Medication", "text", 150), ("What happened", "text", 80), ("Note", "text", 150),
                   ("From", "text", None)],
                  [[t, name[:200], med_log.LABEL.get(st, st), note, src] for t, name, st, note, src in rows], lead,
                  "As logged, typed in or in Apple Health's Medications: what the person said happened, nothing more.",
                  "No doses logged in this period.", most, of="doses")


BUILD = {"home_bp": home_bp, "glucose": glucose_block, "temperature": temperature, "oxygen": oxygen, "respiratory": respiratory,
         "vo2max": vo2max, "immunizations": immunizations, "ecg": ecg_block, "bmi": bmi, "targets": targets_block,
         "medlog": medlog}


def offered(c):
    """The sections this profile has something for, in order."""
    has = {
        "home_bp": bool(c.watch.get("bp_readings")),
        "glucose": any(k.startswith("glucose_") and c.metrics[k].get("pts") for k in c.metrics if isinstance(k, str))
                   or bool(c.watch.get("glucose_readings")),   # or typed in at home (#232)
        "temperature": bool(c.watch.get("temp_readings")),
        "oxygen": bool(c.pts("spo2_avg")) or bool(c.watch.get("spo2_readings")),   # (#232)
        "respiratory": bool(c.pts("resp")),
        "vo2max": bool(c.pts("vo2max")),
        "immunizations": any(isinstance(e, dict) and e.get("k") == "immunization" and not e.get("hidden") for e in c.data.get("events") or []),
        "ecg": bool(c.watch.get("ecg")),
        "bmi": bool(c.person["heights"]) and (bool(_weights(c)) or bool(c.person["birthDate"] and c.person["sex"])),
        "targets": bool(tg.of(c.cfg)),   # #234: only when some target is entered

        "medlog": bool(_doses(c)),   # (#235)
    }
    return [s for s in IDS if has[s]]


def blocks(c, ids, keep, history=None, most=None):
    """{id: block} for the sections wanted (ids), with the days kept by keep(day); a history section (immunizations)
    by history(day) when given."""
    return {s: BUILD[s](c, history if s in HISTORY and history else keep, most) for s in IDS if s in ids}
