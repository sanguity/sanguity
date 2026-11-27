"""Heart rate zones on workouts (#197): the common five zones, from a maximum heart rate.

The maximum is the one the person entered (About this person's "Maximum heart rate", person.maxHr: measured in an
exercise test, say), or else estimated by the common formula, 220 minus the age on the workout's day (person.age_on
with the birth date, #194). Without either, no zones: the page says how to add one. The zones are the commonly
published five, as shares of that maximum:

  zone 1   50–60%     zone 2   60–70%     zone 3   70–80%     zone 4   80–90%     zone 5   90% and above

each zone's lower limit rounded to a whole bpm (half up, in whole numbers: bounds()), a reading in zone k when it is
at least zone k's limit and under zone k+1's; a reading under zone 1's is "below zone 1". What the formula and the
device say only: nothing here judges a workout. www/js/health.js shows them.

What each phone app sends per workout, and what's kept (watch/receiver.py writes it as the workout's "hr"):
  Health Auto Export (Apple Health)   a workout's heart rate as minute-by-minute readings ("heartRateData": [{date,
      Min, Avg, Max}]) and its average and maximum ("heartRate": {avg, max}, or avgHeartRate / maxHeartRate in older
      versions). The readings give time in each zone; without them, only the average and maximum.
  Health Connect (Life Dashboard)   an exercise session holds no heart rate: the heart rate samples the
      phone sent for the session's time (raw samples, not hourly buckets) are the workout's readings.
  An uploaded Apple Health export   each workout's heart rate statistics (WorkoutStatistics: average and maximum)
      only. Its heart rate readings are summed into days as they stream past, so time in zones isn't counted for it.
The receiver keeps a workout's readings as seconds at each whole bpm (histogram()): a reading counts for the time
to the next one, at most GAP seconds (a longer gap is time without readings), the last for the usual spacing. Zones
can then be worked out for any maximum when the page asks (annotate()), so a birth date or maximum entered later
applies to every workout, and the page gets five numbers a workout, never the readings.

Standard library only.
"""
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import person

PCTS = (50, 60, 70, 80, 90)   # each zone's lower limit, % of the maximum
ESTIMATE = 220                # the common formula: maximum = 220 - age
GAP = 120                     # seconds: a reading counts for at most this long
MAX_MIN, MAX_MAX = 100, 230   # a maximum heart rate typed in, bpm


class ZoneError(ValueError):
    pass


def check_max(v):
    """A measured maximum heart rate: 0 (not set) or a whole bpm from MAX_MIN to MAX_MAX."""
    if v in (None, "", 0):
        return 0
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v != int(v):
        raise ZoneError("The maximum heart rate must be a whole number of beats a minute.")
    if not MAX_MIN <= v <= MAX_MAX:
        raise ZoneError(f"The maximum heart rate must be from {MAX_MIN} to {MAX_MAX} beats a minute.")
    return int(v)


def bounds(zmax):
    """The lower limits of zones 1 to 5 for a maximum (whole bpm), each rounded half up."""
    return [(int(zmax) * p + 50) // 100 for p in PCTS]


def histogram(readings, start, end):
    """[(t seconds, bpm)] within a workout [start, end] (seconds) -> ({whole bpm: seconds}, average, maximum), the
    average weighed by the time each reading counts for; ({}, None, None) without readings."""
    pts = sorted((t, v) for t, v in readings if start <= t <= end and isinstance(v, (int, float)) and 20 <= v <= 260)
    if not pts:
        return {}, None, None
    gaps = sorted(b[0] - a[0] for a, b in zip(pts, pts[1:]) if b[0] > a[0])
    usual = min(GAP, gaps[len(gaps) // 2]) if gaps else min(GAP, 60)
    hist, total, weighed = {}, 0, 0.0
    for i, (t, v) in enumerate(pts):
        nxt = pts[i + 1][0] if i + 1 < len(pts) else None
        secs = min(GAP, nxt - t) if nxt is not None else min(usual, max(0, end - t))
        if secs <= 0:
            continue
        b = int(v + 0.5)
        hist[b] = hist.get(b, 0) + secs
        total += secs
        weighed += v * secs
    if not total:
        return {}, None, None
    return hist, weighed / total, max(v for _, v in pts)


def packed(hist, avg, mx):
    """A workout's "hr" as the receiver writes it: {"avg", "max", "hist": [[bpm, seconds]]}, numbers kept short."""
    out = {}
    if avg is not None:
        out["avg"] = round(avg, 1)
    if mx is not None:
        out["max"] = round(mx, 1)
    if hist:
        out["hist"] = [[b, round(s)] for b, s in sorted(hist.items()) if round(s)]
    return out


def in_zones(hist, zmax):
    """Seconds below zone 1 and in zones 1 to 5: [below, z1, z2, z3, z4, z5]."""
    lo = bounds(zmax)
    out = [0] * 6
    for b, secs in hist:
        k = sum(1 for x in lo if b >= x)   # 0: below zone 1
        out[k] += secs
    return out


def max_for(birth, measured, day):
    """(maximum, basis, age) for a workout's day: the measured maximum ("measured"), or 220 minus the age that day
    ("age"); (None, None, None) with neither (or a day before the birth date)."""
    if measured:
        return measured, "measured", person.age_on(birth, day) if birth else None
    age = person.age_on(birth, day) if birth else None
    if age is None:
        return None, None, None
    return ESTIMATE - age, "age", age


def _zone(name):
    try:
        return ZoneInfo(name) if name else None
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _day(start, tz):
    """A workout's local day: its start in the summary's time zone (the phone's), else as written."""
    try:
        t = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
    except ValueError:
        return str(start)[:10]
    return (t.astimezone(tz) if tz and t.tzinfo else t).date().isoformat()


def annotate(summary, birth, measured):
    """Work out each workout's zones for the page, in place: a workout's "hr" becomes {"avg", "max", and with a
    maximum "zmax", "basis", "age", and with readings "zones": [seconds below zone 1, in zones 1 to 5]}; the
    readings themselves ("hist") are left out. summary["hrZones"] says what the zones come from: {"basis": "measured",
    "age" or None, "maxHr": the measured one or None, "workouts": whether any workout has heart rate}. The workout's
    day is its start's date in the summary's time zone (the phone's)."""
    if not summary:
        return summary
    measured, tz, any_hr = measured or 0, _zone(summary.get("tz")), False
    for x in summary.get("exercise") or []:
        hr = x.get("hr") if isinstance(x, dict) else None
        if not isinstance(hr, dict):
            continue
        any_hr = True
        out = {k: hr[k] for k in ("avg", "max") if isinstance(hr.get(k), (int, float))}
        zmax, basis, age = max_for(birth, measured, _day(x.get("start"), tz))
        if zmax:
            out.update(zmax=zmax, basis=basis, age=age)
            hist = [(int(b), s) for b, s in hr.get("hist") or [] if isinstance(b, (int, float)) and isinstance(s, (int, float))]
            if hist:
                out["zones"] = in_zones(hist, zmax)
        x["hr"] = out
    summary["hrZones"] = {"basis": "measured" if measured else "age" if birth else None, "maxHr": measured or None,
                          "workouts": any_hr}
    return summary
