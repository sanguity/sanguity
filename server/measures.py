"""The phone's and watch's measures, and the newer kinds of record, as sections of the visit summary (#48) and of
share links (#47): issue #225. Each is offered only when the profile has some of it, and left unticked by default:
the person ticks what they want a clinician or relative to see.

  home_bp        blood pressure taken at home (#180): the readings' count, average and highest, and the readings
  glucose        blood glucose from the phone (#181): each day's lowest to highest and average, a meter's or a
                 continuous monitor's (CGM); for CGM days the share of readings in the standard published ranges,
                 labelled as the standard, never as anyone's targets
  temperature    body temperature taken at home (#184): count, average, highest, and the readings
  oxygen         the watch's daily blood oxygen (#184): average and lowest
  respiratory    the watch's daily respiratory rate (#226)
  vo2max         the watch's cardio fitness estimates (#226)
  immunizations  the vaccines the records say were given (#179), as a list
  ecg            ECGs and irregular rhythm notifications (#182), by ecg.shared_rows: what the device said each one
                 showed, never the samples
  bmi            BMI from each weight and the height in effect on its day (#195, person.bmi), the number only; for a
                 child, where each weight, height and BMI falls on the WHO's or the CDC's growth charts (#196,
                 server/growth.py)
Cycle tracking (#183) isn't here: the visit summary has its own section for it (cycle_tracking.py), offered only to
owners and editors while it's on and ticked each time, and share links never offer it.

Each section is a block, the same for the summary's page and PDF and for a share link's page:
  {"lead": [lines], "cols": [[heading, kind, PDF width]], "rows": [[cell]], "total": rows before any were cut,
   "note": a line under the table, "empty": what to say with no rows, "history": a list of everything, not a period}
A cell of kind "day" is YYYY-MM-DD, "time" a local wall time YYYY-MM-DDTHH:MM (in the watch summary's own zone, as
the phone took it), "text" as it is; each page writes dates in its own way. Only the fields named here leave the
server. Values are in the units of the person making it (Settings → Units, #41): glucose in mg/dL or mmol/L,
temperatures in °C or °F, weights in lb or kg, heights in cm or feet and inches.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import ecg, glucose, growth, person, watch_store

SECTIONS = [   # (id, label, what a share link shows: said on the owner's form and the link's page)
    ("home_bp", "Blood pressure at home", "each reading taken at home with a phone app: the date and time, both pressures, "
                                          "the pulse and the device"),
    ("glucose", "Blood glucose from the phone", "each day's lowest and highest reading and the average, a meter's or a continuous "
                                                "monitor's (CGM), and for CGM days the time in the standard published ranges"),
    ("temperature", "Body temperature at home", "each reading taken at home with a phone app: the date and time, the temperature "
                                                "and the device"),
    ("oxygen", "Blood oxygen from the watch", "each day's average and lowest reading"),
    ("respiratory", "Respiratory rate from the watch", "each day's rate, in breaths a minute"),
    ("vo2max", "Cardio fitness (VO2 max)", "each estimate the watch made"),
    ("immunizations", "Immunizations", "each vaccine the records say was given: the date, the vaccine, the dose and where"),
    ("ecg", "ECGs and irregular rhythm notifications", "the date, what the watch said each recording showed and its average "
                                                       "heart rate; not the traces themselves"),
    ("bmi", "BMI and growth", "each weight with the height in effect on its day and the BMI from them; for a child, where each "
                              "measurement falls on the WHO's or the CDC's growth charts"),
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


def _s(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


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
        """A reading's time (UTC, ISO) as the local wall time it was taken, YYYY-MM-DDTHH:MM, or None."""
        try:
            x = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
        except ValueError:
            return None
        return (x if x.tzinfo else x.replace(tzinfo=timezone.utc)).astimezone(self.tz).strftime("%Y-%m-%dT%H:%M")

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


def _block(cols, rows, lead=(), note="", empty="", most=None, history=False):
    total = len(rows)
    return {"lead": [x for x in lead if x], "cols": [list(c) for c in cols], "rows": rows[:most] if most else rows,
            "total": total, "note": note, "empty": empty, "history": history}


def _src(name, app):
    return f"{name} ({app})" if name and app else name or app or "phone"


# ---------------------------------------------------------------- each section

def _home(c, readings, keep):
    """[(local time, reading)] of a vitals list (bp_readings, temp_readings) in the period, newest first."""
    out = []
    for r in c.watch.get(readings) or []:
        if not isinstance(r, list) or len(r) < 2:
            continue
        t = c.local(r[0])
        if t and keep(t[:10]):
            out.append((t, r))
    return sorted(out, key=lambda x: x[0], reverse=True)


def home_bp(c, keep, most):
    rs = []
    for t, r in _home(c, "bp_readings", keep):
        r = list(r) + [None] * (6 - len(r))
        s, d = _num(r[1]), _num(r[2])
        if s is not None and d is not None:
            rs.append((t, s, d, _num(r[3]), _src(r[4], r[5])))
    lead = []
    if rs:
        n = len(rs)
        lead.append(f"{_s(n, 'reading')} · average {_n(sum(x[1] for x in rs) / n)}/{_n(sum(x[2] for x in rs) / n)} mmHg · "
                    f"highest systolic {_n(max(x[1] for x in rs))}, highest diastolic {_n(max(x[2] for x in rs))}")
    return _block([("Date and time", "time", 110), ("Systolic/diastolic (mmHg)", "text", 130), ("Pulse (bpm)", "text", 70),
                   ("Device", "text", None)],
                  [[t, f"{_n(s)}/{_n(d)}", _n(p), src] for t, s, d, p, src in rs], lead,
                  "Taken at home and sent by a phone app, as the device measured them. Readings taken at visits are in the records.",
                  "No readings at home in this period.", most)


def temperature(c, keep, most):
    f = c.units.get("temperature") == "F"
    unit = "°F" if f else "°C"
    rs = []
    for t, r in _home(c, "temp_readings", keep):
        r = list(r) + [None] * (4 - len(r))
        v = _num(r[1])
        if v is not None:
            rs.append((t, v * 9 / 5 + 32 if f else v, _src(r[2], r[3])))
    lead = []
    if rs:
        n = len(rs)
        lead.append(f"{_s(n, 'reading')} · average {_n(sum(x[1] for x in rs) / n, 1)} {unit} · highest {_n(max(x[1] for x in rs), 1)} {unit}")
    return _block([("Date and time", "time", 110), (f"Temperature ({unit})", "text", 110), ("Device", "text", None)],
                  [[t, _n(v, 1), src] for t, v, src in rs], lead, "Taken at home and sent by a phone app, as the thermometer measured them.",
                  "No readings at home in this period.", most)


def oxygen(c, keep, most):
    low = dict(c.pts("spo2_min"))
    days = [(d, v, _num(low.get(d))) for d, v in c.pts("spo2_avg") if keep(d)][::-1]
    lead = []
    if days:
        lows = [x[2] for x in days if x[2] is not None]
        lead.append(f"{_s(len(days), 'day')} · average {_n(sum(x[1] for x in days) / len(days), 1)}%" +
                    (f" · lowest {_n(min(lows), 1)}%" if lows else ""))
    return _block([("Date", "day", 90), ("Average (%)", "text", 90), ("Lowest (%)", "text", None)],
                  [[d, _n(v, 1), _n(lo, 1)] for d, v, lo in days], lead, "The watch's readings through each day (SpO2), as it measured them.",
                  "No blood oxygen from the watch in this period.", most)


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
                  [[d, _n(v, 1)] for d, v in days], lead, note, "None from the watch in this period.", most)


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


def glucose_block(c, keep, most):
    mmol = c.units.get("lab") == "si"
    unit = "mmol/L" if mmol else "mg/dL"
    cv = lambda v: "" if _num(v) is None else _n(v / glucose.MGDL_PER_MMOL, 1) if mmol else _n(v)
    s0, s1, s2 = ("3.0", "3.9", "10.0") if mmol else ("54", "70", "180")
    col = {k: dict(c.pts("glucose_" + k)) for k in ("avg", "min", "max", "n", "hours", "lt54", "lt70", "in", "gt180")}
    meter = {}
    for w, v in (c.metrics.get("glucose_meter") or {}).get("pts") or []:
        if _day(w) and _num(v) is not None and keep(str(w)[:10]):
            meter.setdefault(str(w)[:10], []).append(v)
    cgm = sorted(d for d in col["avg"] if keep(d))
    rows = []
    for d in sorted(set(cgm) | set(meter), reverse=True):
        if d in col["avg"] and keep(d):
            n, h = int(col["n"].get(d) or 0), _num(col["hours"].get(d))
            rows.append([d, f"CGM: {n}" + (f" in {_n(h, 1)} h" if h is not None else ""),
                         f"{cv(col['min'].get(d))}–{cv(col['max'].get(d))}", cv(col["avg"][d]), _pct(col["in"].get(d)),
                         _pct(col["lt70"].get(d)), _pct(col["lt54"].get(d)), _pct(col["gt180"].get(d))])
        if d in meter:
            vs = meter[d]
            rows.append([d, f"Meter: {len(vs)}", f"{cv(min(vs))}–{cv(max(vs))}" if len(vs) > 1 else cv(vs[0]),
                         cv(sum(vs) / len(vs)), "", "", "", ""])
    lead = []
    if cgm:   # the period's CGM days together, each day weighed by its readings
        n = sum(int(col["n"].get(d) or 0) for d in cgm)
        share = (lambda k: sum((col[k].get(d) or 0) * int(col["n"].get(d) or 0) for d in cgm) / n) if n else (lambda k: None)
        lead.append(f"{_s(len(cgm), 'CGM day')} · average {cv(share('avg')) if n else ''} {unit} · time in the standard ranges: "
                    f"{_pct(share('in'))} {s1}–{s2}, {_pct(share('lt70'))} below {s1} ({_pct(share('lt54'))} below {s0}), "
                    f"{_pct(share('gt180'))} above {s2}")
    if meter:
        vs = [v for x in meter.values() for v in x]
        lead.append(f"{_s(len(vs), 'meter reading')} · average {cv(sum(vs) / len(vs))} {unit} · lowest {cv(min(vs))}, highest {cv(max(vs))}")
    return _block([("Date", "day", 60), ("Readings", "text", 86), (f"Low–high ({unit})", "text", 86), ("Average", "text", 46),
                   (f"{s1}–{s2}", "text", 56), (f"Below {s1}", "text", 56), (f"Below {s0}", "text", 56), (f"Above {s2}", "text", None)],
                  rows, lead, STD_NOTE.format(f"{s1}–{s2}", unit, s1, s0) if cgm else
                  "A meter's (finger-stick) readings, as the phone app sent them.", "No glucose from the phone in this period.", most)


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
                  rows, (), "As the records say them.", "No immunizations in the records.", most, history=True)


def ecg_block(c, keep, most):
    rows = [[r["when"], r["title"], r["text"]] for r in ecg.shared_rows(c.watch) if keep(r["when"])]
    return _block([("Date", "day", 80), ("What the watch said", "text", 220), ("Details", "text", None)], rows, (),
                  "As the watch classified each one; the traces themselves aren't included.",
                  "No ECGs or irregular rhythm notifications in this period.", most)


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
                  rows, lead, note, "No weights with a height in this period.", most)


BUILD = {"home_bp": home_bp, "glucose": glucose_block, "temperature": temperature, "oxygen": oxygen, "respiratory": respiratory,
         "vo2max": vo2max, "immunizations": immunizations, "ecg": ecg_block, "bmi": bmi}


def offered(c):
    """The sections this profile has something for, in order."""
    has = {
        "home_bp": bool(c.watch.get("bp_readings")),
        "glucose": any(k.startswith("glucose_") and c.metrics[k].get("pts") for k in c.metrics if isinstance(k, str)),
        "temperature": bool(c.watch.get("temp_readings")),
        "oxygen": bool(c.pts("spo2_avg")),
        "respiratory": bool(c.pts("resp")),
        "vo2max": bool(c.pts("vo2max")),
        "immunizations": any(isinstance(e, dict) and e.get("k") == "immunization" and not e.get("hidden") for e in c.data.get("events") or []),
        "ecg": bool(c.watch.get("ecg")),
        "bmi": bool(c.person["heights"]) and (bool(_weights(c)) or bool(c.person["birthDate"] and c.person["sex"])),
    }
    return [s for s in IDS if has[s]]


def blocks(c, ids, keep, history=None, most=None):
    """{id: block} for the sections wanted (ids), with the days kept by keep(day); a history section (immunizations)
    by history(day) when given."""
    return {s: BUILD[s](c, history if s in HISTORY and history else keep, most) for s in IDS if s in ids}
