"""Blood glucose from the phone apps (#181): what the watch receiver keeps of each reading, and the daily summary it
writes for the Body and activity tab.

Where readings come from (watch/receiver.py hands each upload's glucose here):
  Health Connect, via Life Dashboard Companion   "blood_glucose": [{"uuid", "time", "millimoles_per_liter" or
      "milligrams_per_deciliter", "specimen_source", "source", ...}], one entry a reading, in the shape of its other
      samples (the value field named after Health Connect's unit, as "kilograms" and "celsius" are); deletions come in
      "deleted_records" with the type "blood_glucose". Readings sent in hourly buckets can't be counted into ranges,
      so they are left out (and logged).
  Apple Health, via Health Auto Export (#148)   the metric "blood_glucose" ({"units": "mg/dL", "mmol/L" or HealthKit's
      "mmol<180.1558800000541>/L", "data": [{"date", "qty", "source"}]}). It needs its own automation with no time
      grouping, so each reading comes on its own: a row grouped into hours (Avg, Min, Max) is left out (and logged).
Each reading is turned into mg/dL by the app's exact conversion (server/units.py, #41: glucose's molar mass,
180.156 g/mol, so 1 mmol/L is 18.0156 mg/dL) and kept in the receiver's database, sealed like the rest of it:
glucose(app "hc" or "hae", src, id, t, wall, v, kind). id is Health Connect's uuid, or the time for Health Auto
Export, so a reading sent again (overlapping uploads, "Today" sent every hour) replaces itself; wall is the local
time it was taken ("2026-03-04T07:12"), which also gives its day.

Meter or CGM. A reading that says what was measured is taken at its word: Health Connect's specimen source
"interstitial fluid" is a continuous glucose monitor (CGM, a sensor worn on the skin); capillary blood (a
finger-stick), plasma, serum, whole blood and tears are a meter's single readings. Otherwise it goes by how often its
source sends: a source (one app's one source name) whose readings are mostly 20 minutes apart or less, with at least
12 of them, is a CGM (sensors read every 1 to 15 minutes); any other is a meter (a few readings a day, hours apart).

The summary (summary(), metrics in daily.json's shape):
  glucose_meter          every meter reading, as [local time, mg/dL] (not [day, value] like the other metrics)
  glucose_avg/min/max    each CGM day's average, lowest and highest reading, mg/dL
  glucose_n              the day's CGM readings
  glucose_hours          hours of the day with readings: the quarter hours holding at least one reading, so a day with
                         a sensor gap says so ("18 h"), whatever the sensor's interval
  glucose_lt54/lt70/in/gt180   the share of the day's CGM readings below 54, below 70 (those below 54 included),
                         70 to 180 and above 180 mg/dL, in %: the standard published ranges for CGM time in range (the
                         international consensus of 2019), not advice and not anyone's own targets. Each reading is
                         compared as a whole number of mg/dL, as sensors measure, so 3.9 mmol/L (70.3) is in range
                         and 10.0 mmol/L (180.2) is too, as the same ranges in mmol/L (3.9 to 10.0) say.
A CGM day is one source's: the one with the most readings that day, so two apps passing on the same sensor, or two
sensors worn across a change, don't count twice.
  glucose_dist           (#234) the same day's readings counted by whole mg/dL, compared as the ranges above are:
                         [lowest, how many at it, at lowest + 1, ...], for time in a person's own range
                         (server/targets.py). The server gives the page only the shares in that range, never this. On a day Health Connect sent any glucose, its readings are the day's
and Health Auto Export's are left out, as for the other metrics. Not one five-minute reading reaches the page.

Meal and insulin entries (Health Connect's meal type and relation to meal, HealthKit's insulin delivery and meal
time) aren't read: what a CGM app writes there differs from app to app, and they would be the person's own notes
rather than measurements. The uploads keep them in raw/, so a later version can read them.

Standard library only (with server/units.py).
"""
from datetime import datetime, timezone

from . import units

MGDL_PER_MMOL = float(units.lab_factor("Glucose, Blood", "mmol/L", "mg/dL"))   # 18.0156, the app's exact conversion
LOW2, LOW1, HIGH1 = 54, 70, 180     # mg/dL: the standard published ranges (below 54, below 70, 70–180, above 180)
NEAR = 20 * 60                      # readings this close together, mostly, are a sensor's
CGM_MIN = 12                        # ...and a sensor sends at least this many
SLOT = 15 * 60                      # hours with readings are counted in quarter hours
PLAUSIBLE = (5, 1500)               # mg/dL; anything outside is a typing or unit mistake, not a reading

TABLE = """CREATE TABLE IF NOT EXISTS glucose(app TEXT, src TEXT, id TEXT, t INTEGER, wall TEXT, v REAL, kind TEXT,
    PRIMARY KEY(app, src, id)) WITHOUT ROWID;"""

# Health Connect's BloodGlucoseRecord.specimenSource: 1 interstitial fluid, 2 capillary blood, 3 plasma, 4 serum,
# 5 tears, 6 whole blood (0: unknown)
_SPECIMEN = {1: "cgm", 2: "meter", 3: "meter", 4: "meter", 5: "meter", 6: "meter"}
_SPECIMEN_WORDS = (("interstitial", "cgm"), ("capillary", "meter"), ("plasma", "meter"), ("serum", "meter"),
                   ("tears", "meter"), ("whole", "meter"), ("blood", "meter"))

META = {   # key: (name, unit, group), in the order the summary lists them
    "glucose_meter": ("Blood glucose, meter readings", "mg/dL", "Glucose"),
    "glucose_avg": ("Blood glucose, daily average (CGM)", "mg/dL", "Glucose"),
    "glucose_min": ("Blood glucose, daily low (CGM)", "mg/dL", "Glucose"),
    "glucose_max": ("Blood glucose, daily high (CGM)", "mg/dL", "Glucose"),
    "glucose_n": ("CGM readings", "readings", "Glucose"),
    "glucose_hours": ("CGM hours with readings", "hours", "Glucose"),
    "glucose_lt54": ("CGM readings below 54 mg/dL", "%", "Glucose"),
    "glucose_lt70": ("CGM readings below 70 mg/dL (below 54 included)", "%", "Glucose"),
    "glucose_in": ("CGM readings 70–180 mg/dL", "%", "Glucose"),
    "glucose_gt180": ("CGM readings above 180 mg/dL", "%", "Glucose"),
    "glucose_dist": ("CGM readings by whole mg/dL", "readings", "Glucose"),   # #234, see below
}
IN_MGDL = ("glucose_meter", "glucose_avg", "glucose_min", "glucose_max")   # the ones shown in mmol/L when chosen (#41)


def to_mgdl(value, unit):
    """A reading in mg/dL, from mg/dL or mmol/L (HealthKit writes "mmol<180.1558800000541>/L"); None when it isn't
    a number, isn't in either unit, or isn't a plausible reading."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    u = str(unit or "").strip().lower().replace(" ", "")
    if u in ("mg/dl", "mgdl", "mg_dl", "milligrams_per_deciliter"):
        pass
    elif (u.startswith("mmol") and u.endswith("/l")) or u in ("mmol_l", "millimoles_per_liter"):
        v *= MGDL_PER_MMOL
    else:
        return None
    return v if PLAUSIBLE[0] <= v <= PLAUSIBLE[1] else None


def specimen(s):
    """"cgm", "meter" or None, from Health Connect's specimen source (its number, or its name in words)."""
    if isinstance(s, bool) or s is None:
        return None
    if isinstance(s, (int, float)):
        return _SPECIMEN.get(int(s))
    w = str(s).strip().lower()
    if w.isdigit():
        return _SPECIMEN.get(int(w))
    return next((k for word, k in _SPECIMEN_WORDS if word in w), None)


def _instant(s):
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _put(c, app, src, rid, t, v, kind):
    c.execute("INSERT OR REPLACE INTO glucose VALUES(?,?,?,?,?,?,?)",
              (app, src, rid, int(t.timestamp()), t.strftime("%Y-%m-%dT%H:%M"), v, kind))


def from_health_connect(c, p, tz):
    """Fold a Life Dashboard Companion upload's glucose in (tz: the upload's zone, for the local time). Returns
    (readings, left out)."""
    n = skipped = 0
    for r in p.get("blood_glucose") or []:
        if not isinstance(r, dict):
            continue
        if "bucket_start" in r:
            skipped += 1
            continue
        t = _instant(r.get("time")) if r.get("time") else None
        v = to_mgdl(r["milligrams_per_deciliter"], "mg/dL") if r.get("milligrams_per_deciliter") is not None else \
            to_mgdl(r.get("millimoles_per_liter"), "mmol/L")
        if t is None or v is None:
            skipped += 1
            continue
        _put(c, "hc", str(r.get("source") or ""), str(r.get("uuid") or f"glucose@{r['time']}"), t.astimezone(tz), v,
             specimen(r.get("specimen_source")))
        n += 1
    for d in p.get("deleted_records") or []:
        if isinstance(d, dict) and d.get("type") == "blood_glucose" and d.get("uuid"):
            c.execute("DELETE FROM glucose WHERE app='hc' AND id=?", (str(d["uuid"]),))
    return n, skipped


def from_hae(c, m, when):
    """Fold a Health Auto Export metric "blood_glucose" in (when: the receiver's reading of its dates, an aware
    datetime in the reading's own offset). Returns (readings, left out)."""
    rows = [r for r in m.get("data") or [] if isinstance(r, dict)]
    n = skipped = 0
    for r in rows:
        t = when(r.get("date"))
        v = to_mgdl(r.get("qty"), m.get("units")) if r.get("qty") is not None else None
        if t is None or v is None:   # also a row grouped into hours: its Avg can't be put in a range
            skipped += 1
            continue
        _put(c, "hae", str(r.get("source") or ""), str(int(t.timestamp())), t, v, None)
        n += 1
    return n, skipped


# each reading's kind: its own, else its source's by how often it sends (see the docstring)
_KINDS = f"""
WITH gaps AS (SELECT app, src, t - LAG(t) OVER (PARTITION BY app, src ORDER BY t) AS gap FROM glucose WHERE kind IS NULL),
     sources AS (SELECT app, src, COUNT(*) AS n, SUM(gap <= {NEAR}) AS near FROM gaps GROUP BY app, src),
     r AS (SELECT g.app, g.src, g.t, g.wall, substr(g.wall, 1, 10) AS day, g.v,
                  COALESCE(g.kind, CASE WHEN s.n >= {CGM_MIN} AND s.near * 2 > s.n - 1 THEN 'cgm' ELSE 'meter' END) AS kind
           FROM glucose g LEFT JOIN sources s ON s.app = g.app AND s.src = g.src)
"""


def summary(con):
    """The glucose metrics for daily.json (see the docstring), from a database with the glucose table; [] when it
    holds no readings."""
    q = con.execute
    hc_days = {d for d, in q("SELECT DISTINCT substr(wall, 1, 10) FROM glucose WHERE app='hc'")}
    out = {k: {} for k in META}
    best = {}
    for app, src, day, n, avg, mn, mx, lt54, lt70, gt180, slots in q(_KINDS + f"""
            SELECT app, src, day, COUNT(*), AVG(v), MIN(v), MAX(v), SUM(ROUND(v) < {LOW2}), SUM(ROUND(v) < {LOW1}),
                   SUM(ROUND(v) > {HIGH1}), COUNT(DISTINCT t / {SLOT}) FROM r WHERE kind = 'cgm' GROUP BY app, src, day"""):
        if app == "hae" and day in hc_days:
            continue
        b = best.get(day)
        if b is None or n > b[0] or (n == b[0] and src < b[1]):
            best[day] = (n, src, avg, mn, mx, lt54, lt70, gt180, slots, app)
    dist = {}   # (app, src, day) -> {whole mg/dL: readings} (#234)
    for app, src, day, w, k in q(_KINDS + "SELECT app, src, day, CAST(ROUND(v) AS INTEGER), COUNT(*) FROM r WHERE kind = 'cgm' "
                                           "GROUP BY app, src, day, CAST(ROUND(v) AS INTEGER)"):
        dist.setdefault((app, src, day), {})[w] = k
    for day, (n, src, avg, mn, mx, lt54, lt70, gt180, slots, app) in best.items():
        c = dist.get((app, src, day)) or {}
        if c:
            lo = min(c)
            out["glucose_dist"][day] = [lo] + [c.get(v, 0) for v in range(lo, max(c) + 1)]
        pct = lambda k: round(100 * k / n, 1)
        for key, v in (("glucose_avg", round(avg, 1)), ("glucose_min", round(mn, 2)), ("glucose_max", round(mx, 2)),
                       ("glucose_n", n), ("glucose_hours", slots * SLOT / 3600), ("glucose_lt54", pct(lt54)),
                       ("glucose_lt70", pct(lt70)), ("glucose_in", round(100 - pct(lt70) - pct(gt180), 1)),
                       ("glucose_gt180", pct(gt180))):
            out[key][day] = v
    seen = set()
    for app, wall, day, v in q(_KINDS + "SELECT app, wall, day, v FROM r WHERE kind = 'meter' ORDER BY wall, app, src"):
        if app == "hae" and day in hc_days:
            continue
        if (wall, round(v)) in seen:   # one reading passed on by two of a person's apps
            continue
        seen.add((wall, round(v)))
        out["glucose_meter"][wall] = round(v, 2)
    return [{"key": k, "name": META[k][0], "unit": META[k][1], "group": META[k][2], "pts": sorted(out[k].items())}
            for k in META if out[k]]
