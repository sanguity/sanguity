"""About this person (#194): a profile's birth date, the sex used for reference ranges, and height with a history.

Some things Sanguity shows depend on who the person is: their age on each result's day, ranges of their own by sex
and age band (#44, server/ranges.py), and anything built on height (BMI, children's growth). These details are kept
together here, each optional:

  birthDate   "" (not set) or YYYY-MM-DD, a real day from 1900 to today
  sex         "", "female" or "male": the sex labs base their reference ranges on. Not gender identity, which
              Sanguity doesn't need and doesn't ask for.
  heights     [{date, cm, unit}], oldest first, at most one per day: cm is the height in centimetres (the one unit
              heights are stored in, to two decimals), unit how it was typed ("cm", or "ftin" for feet and inches),
              so "as reported" shows it that way again. From 20 to 272 cm, dated from 1900 to today, and not before
              the birth date.

Stored as three profile settings (server/settings.py, section "person", not on the Settings screen): person.birthDate,
person.sex and person.heights, sealed (encrypted) like every setting that can hold health information. So they are
in the merged settings every page loads (CFG.person on the records page), exported with the profile's settings.json
and loaded back with it, and backed up with the database. Owners and editors change them, on Sharing → About this
person (routes_person.py, which also checks the three agree: no height before the birth date); viewers see them.
A share link shows them only when its owner ticks "About this person" (share_links.py), never otherwise.

Before #194 the birth date and sex were the settings ranges.birthDate and ranges.sex (#44). Schema step 30 (db.py)
renamed them in place for every profile, once, sealed values unchanged; a settings.json from before (an export, the
setup build's) still says ranges.birthDate and ranges.sex, and settings.from_file reads those as person.* (LEGACY).

What it's for, and not: the age on a result's day, #44's age bands and sex, and what is built on height. Never to
judge a result or replace a report's own range.

Helpers for the features built on it (BMI #195, growth percentiles #196, heart-rate zones #197); www/js/person.js has
the same for the page:
  of(cfg)                   the three values from the merged settings (settings.merged), filled in
  age_on(birth, day)        whole years on that day (both YYYY-MM-DD...), or None
  age_months_on(birth, day) whole months on that day, or None (a child's age, for growth charts)
  sex_for_ranges(cfg)       "female", "male" or ""
  height_on(heights, day)   the entry in effect on that day: the latest dated on or before it, or None
  bmi(weight, unit, cm)     kg/m² from a weight in "kg" or "lb" and a height in cm (#195; www/js/growth.js bmiOf)
  who_bmi(v)                the WHO's adult category of a BMI, read at one decimal as it's shown (#195)
Clinic-recorded heights (a "Height" or "Body height" series in the records, recorded()) are shown beside the history,
each with "Add to the history"; they are never part of it by themselves: an import can be undone or replaced, and a
height measured with shoes on is the person's to accept or not.
"""
import datetime, math, re

SEXES = {"": "Not set", "female": "Female", "male": "Male"}
UNITS = ("cm", "ftin")
CM_MIN, CM_MAX = 20, 272
MOST = 400                # height entries
IN_CM = 2.54


class PersonError(ValueError):
    """A value About this person doesn't accept, said in plain language."""


def _day(v):
    try:
        return datetime.date.fromisoformat(str(v)[:10]) if isinstance(v, str) and len(v) >= 10 else None
    except ValueError:
        return None


def check_birth(v):
    """"" (not set) or YYYY-MM-DD, a real day from 1900 to today."""
    if v in ("", None):
        return ""
    d = _day(v) if isinstance(v, str) and len(v) == 10 else None
    if d is None:
        raise PersonError("The birth date must be a day, like 1980-04-30.")
    if not datetime.date(1900, 1, 1) <= d <= datetime.date.today():
        raise PersonError("The birth date must be from 1900 to today.")
    return d.isoformat()


def check_sex(v):
    if v is None:
        return ""
    if v not in SEXES:
        raise PersonError("Sex used for reference ranges must be female, male, or not set.")
    return v


def check_height(h):
    """One entry, cleaned: {date, cm, unit}."""
    if not isinstance(h, dict):
        raise PersonError("Each height needs a date and a height.")
    d = _day(h.get("date")) if isinstance(h.get("date"), str) and len(h["date"]) == 10 else None
    if d is None:
        raise PersonError("A height's date must be a day, like 2024-05-01.")
    if not datetime.date(1900, 1, 1) <= d <= datetime.date.today():
        raise PersonError("A height's date must be from 1900 to today.")
    cm = h.get("cm")
    if isinstance(cm, bool) or not isinstance(cm, (int, float)) or cm != cm:
        raise PersonError("A height must be a number.")
    if not CM_MIN <= cm <= CM_MAX:
        raise PersonError(f"A height must be from {CM_MIN} to {CM_MAX} cm (8 in to 8 ft 11 in).")
    unit = h.get("unit") or "cm"
    if unit not in UNITS:
        raise PersonError("A height is typed in cm, or in feet and inches.")
    cm = round(float(cm), 2)
    return {"date": d.isoformat(), "cm": int(cm) if cm.is_integer() else cm, "unit": unit}


def check_heights(v):
    """The history, cleaned: oldest first, one entry per day (two on one day are refused)."""
    if not isinstance(v, list):
        raise PersonError("This must be a list of heights.")
    if len(v) > MOST:
        raise PersonError(f"At most {MOST} heights.")
    out = sorted((check_height(h) for h in v), key=lambda h: h["date"])
    for a, b in zip(out, out[1:]):
        if a["date"] == b["date"]:
            raise PersonError(f"Two heights on {a['date']}: keep one per day.")
    return out


def check_together(birth, heights):
    """No height dated before the birth date (a typo in one or the other)."""
    if birth and heights and heights[0]["date"] < birth:
        raise PersonError(f"There's a height on {heights[0]['date']}, before the birth date ({birth}). "
                          "Change or remove that height, or check the birth date.")


def from_ftin(ft, inch):
    """Feet and inches typed, in centimetres (to two decimals)."""
    for v, what in ((ft, "feet"), (inch, "inches")):
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v < 0):
            raise PersonError(f"The {what} must be a number, 0 or more.")
    return round(((ft or 0) * 12 + (inch or 0)) * IN_CM, 2)


# ---------------------------------------------------------------- helpers for what is built on it

def of(cfg):
    """{"birthDate", "sex", "heights"} from the merged settings (settings.merged), each filled in."""
    p = (cfg or {}).get("person") or {}
    return {"birthDate": p.get("birthDate") or "", "sex": p.get("sex") or "", "heights": list(p.get("heights") or [])}


def age_on(birth, day):
    """Whole years from the birth date to the day (both YYYY-MM-DD...), or None."""
    b, d = _day(birth), _day(day)
    if b is None or d is None or d < b:
        return None
    return d.year - b.year - ((d.month, d.day) < (b.month, b.day))


def age_months_on(birth, day):
    """Whole months from the birth date to the day, or None. A month is complete on the same day of the month (or on
    the month's last day, for a birth on the 29th to 31st of a longer month)."""
    b, d = _day(birth), _day(day)
    if b is None or d is None or d < b:
        return None
    months = (d.year - b.year) * 12 + d.month - b.month
    last = (datetime.date(d.year + d.month // 12, d.month % 12 + 1, 1) - datetime.timedelta(days=1)).day
    if d.day < min(b.day, last):
        months -= 1
    return months


def sex_for_ranges(cfg):
    return of(cfg)["sex"]


def height_on(heights, day):
    """The entry in effect on the day: the latest dated on or before it, or None (no height known by then)."""
    day = str(day or "")[:10]
    best = None
    for h in heights or []:
        if h["date"] <= day and (best is None or h["date"] > best["date"]):
            best = h
    return best


# ---------------------------------------------------------------- BMI (#195; www/js/growth.js does the same on the page)
LB_KG = 0.45359237   # by definition
WHO_BMI = ((float("-inf"), 18.5, "under 18.5", "underweight"), (18.5, 25, "18.5–24.9", "normal weight"),
           (25, 30, "25–29.9", "pre-obesity"), (30, float("inf"), "30 and over", "obesity"))   # the WHO's, for adults


def bmi(weight, unit, cm):
    """kg/m² from a weight in "kg" or "lb" and a height in cm, or None."""
    for v in (weight, cm):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not v > 0:
            return None
    kg = weight * LB_KG if unit == "lb" else weight if unit == "kg" else None
    return None if kg is None else kg / (cm / 100) ** 2


def who_bmi(v):
    """The WHO's adult category of a BMI, read at one decimal as it is shown: {"range", "name"}. Only for adults (20
    and over), only where the person asked for it, and always said as the WHO's."""
    r = math.floor(v * 10 + 0.5) / 10   # half up, as the page's Math.round
    for lo, hi, rng, name in WHO_BMI:
        if lo <= r < hi:
            return {"range": rng, "name": name}
    return None


def ftin(cm):
    """(feet, inches to one decimal) for a height in cm."""
    inch = round(cm / IN_CM, 1)
    ft = int(inch // 12)
    return ft, round(inch - ft * 12, 1)


def text(cm, unit="cm"):
    """"172.5 cm" or "5 ft 8 in"."""
    if unit == "ftin":
        ft, inch = ftin(cm)
        return f"{ft} ft {inch:g} in"
    return f"{round(cm, 1):g} cm"


# ---------------------------------------------------------------- heights in the records (a clinic's vitals)

_HEIGHT_NAME = re.compile(r"^(body )?height\b|^height$|^stature$", re.I)
_HEIGHT_LOINC = {"8302-2", "8306-3", "3137-7", "8308-9"}   # body height, lying length, measured, standing
_LEN = {"cm": 1.0, "in": IN_CM, "[in_i]": IN_CM, "inch": IN_CM, "inches": IN_CM, "m": 100.0}


def recorded(data, most=40):
    """Heights in the records the page loads (a "Height" series a clinic's vitals brought): [{date, cm, unit, source}],
    newest first, one per day, those a height can't be (outside CM_MIN to CM_MAX) left out."""
    out, imports = {}, (data or {}).get("imports") or {}
    for s in (data or {}).get("labs") or []:
        if not isinstance(s, dict):
            continue
        name, code = str(s.get("name") or "").strip(), str(s.get("loinc") or "").strip()
        if not (_HEIGHT_NAME.search(name) or code in _HEIGHT_LOINC):
            continue
        f = _LEN.get(str(s.get("unit") or "").strip().lower().replace(" ", ""))
        if f is None:
            continue
        imp = imports.get(str(s.get("imp"))) or {}
        source = str(imp.get("name") or imp.get("label") or "")[:80]
        for p in s.get("pts") or []:
            if not isinstance(p, list) or len(p) < 2 or isinstance(p[1], bool) or not isinstance(p[1], (int, float)):
                continue
            d = _day(p[0])
            cm = round(p[1] * f, 2)
            if d and CM_MIN <= cm <= CM_MAX and d.isoformat() not in out:
                out[d.isoformat()] = {"date": d.isoformat(), "cm": cm, "unit": "ftin" if f == IN_CM else "cm", "source": source}
    return sorted(out.values(), key=lambda h: h["date"], reverse=True)[:most]
