"""Targets of your own for home readings (#234): a target the person's care team gave them, typed in on Settings → About
this person, for each measure taken at home. Sanguity never suggests one or says what one should be; with none
entered nothing changes anywhere; the standard published ranges (CGM time in range, 70-180 mg/dL) stay labelled as
the publisher's, and a target is always worded as "your target, as your care team gave it".

Kept as one profile setting, person.targets (server/settings.py, sealed like every setting that can hold health
information, not on the Settings screen): {measure: target}, only the measures that have one. A target is

  bp        {"sysLow", "sysHigh", "diaLow", "diaHigh"}   mmHg, systolic and diastolic, each bound optional
  glucose   {"low", "high", "unit"}                      a meter's readings (finger-stick), mg/dL
  cgm       {"low", "high", "unit"}                      the range a CGM's time in range is counted in, mg/dL
  temp      {"low", "high", "unit"}                      body temperature, °C
  spo2      {"low", "high"}                              blood oxygen, %
  weight    {"low", "high", "unit"}                      kg
  steps     {"low", "high"}                              steps a day, the phone's count of each day (#298)

each with "by" (who set it, "" when not said) and "on" ("" or the day it was set, YYYY-MM-DD). Values are stored in
the one unit each measure is kept in (glucose mg/dL, temperature °C, weight kg: #41's canonical units, to two
decimals); "unit" is how it was typed ("mg/dL" or "mmol/L", "C" or "F", "kg" or "lb"), so a page can offer it the
same way again. A bound left out is null; at least one is given, and a low is below its high.

A reading is above the target when it is over the high, below when under the low, both compared as the reading is
shown (side(): at the places the page writes it, so "7.8" is never "above a target of 7.8"). CGM: a day's readings
are counted by whole mg/dL (glucose.py's glucose_dist), the target's bounds rounded to whole mg/dL as the standard
ranges are (3.9 to 10.0 mmol/L is 70 to 180), and own_shares() gives the share below, in and above it. The page gets
those shares (annotate(), in GET /watch), never the counts.

Who sees it: everyone the profile is shared with, on Body and activity and About this person; the visit summary and
share links only when their "Your targets for home readings" section is ticked (measures.py), never by default.
"""
import datetime, math

MEASURES = {   # id: (name, the unit it's kept in, typed units -> factor to it, (lowest, highest) in it, places shown)
    "bp": ("Blood pressure", "mmHg", {}, None, 0),
    "glucose": ("Blood glucose, meter readings", "mg/dL", {"mg/dL": 1.0, "mmol/L": None}, (20, 600), 0),
    "cgm": ("CGM time in range", "mg/dL", {"mg/dL": 1.0, "mmol/L": None}, (40, 400), 0),
    "temp": ("Body temperature", "°C", {"C": 1.0, "F": None}, (34, 42), 1),
    "spo2": ("Blood oxygen", "%", {}, (70, 100), 0),
    "weight": ("Weight", "kg", {"kg": 1.0, "lb": None}, (1, 450), 1),
    "steps": ("Steps a day", "steps", {}, (100, 100000), 0),
}
BP = {"sys": ("Systolic", 60, 250), "dia": ("Diastolic", 30, 150)}
LIMITS = {"glucose": "20 to 600 mg/dL (1.2 to 33.3 mmol/L)", "cgm": "40 to 400 mg/dL (2.3 to 22.2 mmol/L)",
          "temp": "34 to 42 °C (93.2 to 107.6 °F)", "spo2": "70 to 100%", "weight": "1 to 450 kg (2.3 to 992 lb)", "steps": "100 to 100,000 steps",
          "sys": "60 to 250 mmHg", "dia": "30 to 150 mmHg"}   # what each accepts, in words
LB_KG = 0.45359237
BY_MOST = 80


class TargetError(ValueError):
    """A target this doesn't accept, said in plain language."""


def mgdl_per_mmol():
    from . import glucose
    return glucose.MGDL_PER_MMOL


def to_canonical(measure, v, unit):
    """A value typed in unit, in the measure's own unit (glucose mg/dL, temperature °C, weight kg)."""
    if v is None:
        return None
    if measure in ("glucose", "cgm") and unit == "mmol/L":
        return v * mgdl_per_mmol()
    if measure == "temp" and unit == "F":
        return (v - 32) * 5 / 9
    if measure == "weight" and unit == "lb":
        return v * LB_KG
    return v


def from_canonical(measure, v, unit):
    if v is None:
        return None
    if measure in ("glucose", "cgm") and unit == "mmol/L":
        return v / mgdl_per_mmol()
    if measure == "temp" and unit == "F":
        return v * 9 / 5 + 32
    if measure == "weight" and unit == "lb":
        return v / LB_KG
    return v


def _bound(v, what):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise TargetError(f"{what} must be a number.")
    v = round(float(v), 2)
    return int(v) if v.is_integer() else v


def _pair(lo, hi, what, least, most, limits):
    if lo is not None and not least <= lo <= most or hi is not None and not least <= hi <= most:
        raise TargetError(f"{what}: a target is from {limits}.")
    if lo is not None and hi is not None and lo >= hi:
        raise TargetError(f"{what}: the low end must be below the high end.")


def check_one(measure, t):
    """One measure's target, cleaned (values in its own unit). Raises TargetError."""
    if measure not in MEASURES:
        raise TargetError(f"There's no measure called {str(measure)[:20]}.")
    name, unit, typed, limits, _ = MEASURES[measure]
    if not isinstance(t, dict):
        raise TargetError(f"{name}: a target needs a low end, a high end, or both.")
    out = {}
    if measure == "bp":
        for part, (pname, least, most) in BP.items():
            lo, hi = _bound(t.get(part + "Low"), f"{pname} low end"), _bound(t.get(part + "High"), f"{pname} high end")
            _pair(lo, hi, f"Blood pressure, {pname.lower()}", least, most, LIMITS[part])
            out[part + "Low"], out[part + "High"] = lo, hi
    else:
        lo, hi = _bound(t.get("low"), f"{name}: the low end"), _bound(t.get("high"), f"{name}: the high end")
        _pair(lo, hi, name, *limits, LIMITS[measure])
        out["low"], out["high"] = lo, hi
        if typed:
            u = t.get("unit") or next(iter(typed))
            if u not in typed:
                raise TargetError(f"{name}: a target is typed in {' or '.join(typed)}.")
            out["unit"] = u
    if all(v is None for k, v in out.items() if k != "unit"):
        raise TargetError(f"{name}: a target needs a low end, a high end, or both.")
    by = t.get("by") or ""
    if not isinstance(by, str):
        raise TargetError("Who set it must be some text.")
    by = " ".join(by.split())
    if len(by) > BY_MOST:
        raise TargetError(f"Who set it is too long (at most {BY_MOST} characters).")
    on = t.get("on") or ""
    if on:
        try:
            d = datetime.date.fromisoformat(on) if isinstance(on, str) and len(on) == 10 else None
        except ValueError:
            d = None
        if d is None or not datetime.date(1900, 1, 1) <= d <= datetime.date.today():
            raise TargetError("When it was set must be a day, from 1900 to today.")
        on = d.isoformat()
    return {**out, "by": by, "on": on}


def check(v):
    """person.targets: {measure: target}, each cleaned, in MEASURES' order."""
    if not isinstance(v, dict):
        raise TargetError("Targets must be a list of measures, each with its target.")
    bad = [k for k in v if k not in MEASURES]
    if bad:
        raise TargetError(f"There's no measure called {str(bad[0])[:20]}.")
    return {k: check_one(k, v[k]) for k in MEASURES if k in v}


def typed(measure, t):
    """A target as a page sends it, its values in t["unit"], with them in the measure's own unit: then check_one."""
    if not isinstance(t, dict):
        return check_one(measure, t)
    unit = t.get("unit")
    out = dict(t)
    for k in ("low", "high"):
        v = t.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = to_canonical(measure, v, unit)
    return check_one(measure, out)


def of(cfg):
    """{measure: target} from the merged settings (settings.merged)."""
    t = ((cfg or {}).get("person") or {}).get("targets")
    return t if isinstance(t, dict) else {}


def bands(measure, t):
    """[(part, low, high)]: bp's systolic and diastolic, else the one ("")."""
    if measure == "bp":
        return [(p, t.get(p + "Low"), t.get(p + "High")) for p in BP]
    return [("", t.get("low"), t.get("high"))]


def side(v, low, high, places=0):
    """"high" when v is above the target, "low" when below, else "": compared as each is shown, at places."""
    if v is None:
        return ""
    r = lambda x: round(x + 0.0, places)
    if high is not None and r(v) > r(high):
        return "high"
    if low is not None and r(v) < r(low):
        return "low"
    return ""


def range_text(low, high, fmt, unit):
    """"80–130 mg/dL", "up to 130 mg/dL", "at least 92%"."""
    sp = "" if unit == "%" else " "
    if low is not None and high is not None:
        return f"{fmt(low)}–{fmt(high)}{sp}{unit}"
    if high is not None:
        return f"up to {fmt(high)}{sp}{unit}"
    return f"at least {fmt(low)}{sp}{unit}"


def bp_text(t):
    """"up to 130/80 mmHg", or each part said apart when they differ in kind ("systolic 100–130, diastolic up to 80 mmHg")."""
    f = lambda v: f"{v:g}"
    sl, sh, dl, dh = (t.get(k) for k in ("sysLow", "sysHigh", "diaLow", "diaHigh"))
    if sl is None and dl is None and sh is not None and dh is not None:
        return f"up to {f(sh)}/{f(dh)} mmHg"
    if sh is None and dh is None and sl is not None and dl is not None:
        return f"at least {f(sl)}/{f(dl)} mmHg"
    parts = [f"{n.lower()} {range_text(lo, hi, f, '')}".rstrip() for p, (n, _, _) in BP.items()
             for lo, hi in [(t.get(p + "Low"), t.get(p + "High"))] if lo is not None or hi is not None]
    return ", ".join(parts) + " mmHg"


# ---------------------------------------------------------------- CGM: time in the person's own range

def cgm_bounds(t):
    """The CGM target's bounds in whole mg/dL, as the standard ranges are compared (None where not set)."""
    r = lambda v: None if v is None else int(math.floor(v + 0.5))
    return r(t.get("low")), r(t.get("high"))


def own_counts(dist, low, high):
    """(below, in, above, n) of a day's CGM readings, counted by whole mg/dL in dist: [lowest, how many at it, at the
    next, ...] (glucose.py's glucose_dist)."""
    if not isinstance(dist, list) or len(dist) < 2 or not isinstance(dist[0], (int, float)):
        return None
    lo0 = int(dist[0])
    below = inside = above = 0
    for i, n in enumerate(dist[1:]):
        if not isinstance(n, (int, float)) or n <= 0:
            continue
        v = lo0 + i
        if low is not None and v < low:
            below += n
        elif high is not None and v > high:
            above += n
        else:
            inside += n
    total = below + inside + above
    return (below, inside, above, total) if total else None


def own_shares(dist, low, high):
    """(below, in, above) in % of a day's CGM readings, or None."""
    c = own_counts(dist, low, high)
    if not c:
        return None
    pct = lambda k: round(100 * k / c[3], 1)
    return pct(c[0]), round(100 - pct(c[0]) - pct(c[2]), 1), pct(c[2])


OWN = {"glucose_own_lt": "CGM readings below your own range", "glucose_own_in": "CGM readings in your own range",
       "glucose_own_gt": "CGM readings above your own range"}


def annotate(summary, targets):
    """For GET /watch: each CGM day's share below, in and above the person's own range (when one is set), as metrics
    glucose_own_lt/in/gt in %; the day's counts by mg/dL (glucose_dist) never leave the server."""
    if not summary:
        return summary
    ms = summary.get("metrics") or []
    dist = next((m for m in ms if isinstance(m, dict) and m.get("key") == "glucose_dist"), None)
    summary["metrics"] = [m for m in ms if not (isinstance(m, dict) and m.get("key") == "glucose_dist")]
    t = (targets or {}).get("cgm")
    if not dist or not t:
        return summary
    low, high = cgm_bounds(t)
    rows = {k: [] for k in OWN}
    for d, v in dist.get("pts") or []:
        s = own_shares(v, low, high)
        if s:
            for k, x in zip(OWN, s):
                rows[k].append([d, x])
    summary["metrics"] += [{"key": k, "name": OWN[k], "unit": "%", "group": "Glucose", "pts": rows[k]} for k in OWN if rows[k]]
    return summary
