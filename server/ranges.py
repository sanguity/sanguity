"""Reference ranges from settings (#44): the person's own ranges for results whose report gives none.

A lab report usually prints a reference range beside each result, and Sanguity colours a result against that
range only. Some results come without one (a typed-in result, a CSV, some portals). For those, the profile's
owners and editors can set ranges of their own in Settings, by lab and unit, and optionally by sex and age band.
Such a range is the person's choice, not advice: wherever it is used it is labelled "range from settings".

The rules (www/js/ranges.js does the same on the page; tests/test_ranges.py and tests/browser_check.py check
they agree):

- A range from settings is used only for a numeric result whose report gives no low, no high and no flag of its
  own. It never replaces anything a report says.
- A row applies to a result of the same lab (its name in the records, ignoring case and extra spaces) and the same
  unit (ignoring spaces only: "mg/dL" and "mg / dL" are the same; "mg/dL" and "mmol/L" are not, and no value is
  converted). A result in another unit gets no range from settings.
- Sex: a row for "female" or "male" applies only when the profile's sex used for reference ranges is set to that; a
  row for "any" applies either way. A row for a sex wins over a row for any.
- Age: a row with an age band ("from" and "to" in whole years, both included, either may be left open) applies
  only when the profile has a birth date, by the person's age on the day of the result. Rows for the same lab,
  unit and sex may not have overlapping age bands, so at most one row fits.
- The result is "H" above the high, "L" below the low, else in range.

The ranges are a profile setting (server/settings.py, section "ranges"), encrypted: ranges.overrides. The birth date
and sex are About this person's (#194, server/person.py: person.birthDate and person.sex; before #194 they were
ranges.birthDate and ranges.sex, renamed in place by schema step 30). config() puts the three together for find().
"""
from .person import age_on   # noqa: F401  (whole years on a day; here since #44)

ROW_SEXES = ("any", "female", "male")
MOST = 200
AGE_MAX = 130
SOURCE = "settings"   # what a point's range source is called when it came from here


class RangeError(ValueError):
    """A value the setting doesn't accept, said in plain language."""


def norm_name(n):
    return " ".join(str(n or "").split()).casefold()


def norm_unit(u):
    return "".join(str(u or "").split())


def _number(v, what):
    if v is None or v == "":
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
        raise RangeError(f"{what} must be a number.")
    return int(v) if float(v).is_integer() and abs(v) < 1e15 else float(v)


def _age(v, what):
    v = _number(v, what)
    if v is None:
        return None
    if v != int(v) or not 0 <= v <= AGE_MAX:
        raise RangeError(f"{what} must be a whole number of years from 0 to {AGE_MAX}.")
    return int(v)


def _text(v, what, most):
    if not isinstance(v, str) or not v.strip():
        raise RangeError(f"{what} is missing.")
    v = " ".join(v.split())
    if len(v) > most:
        raise RangeError(f"{what} is too long (at most {most} characters).")
    return v


def check_overrides(v):
    """A list of {lab, unit, sex, ageFrom, ageTo, low, high}, cleaned. Refuses a row with neither low nor high, a
    low above its high, and two rows for the same lab, unit and sex whose ages overlap."""
    if not isinstance(v, list):
        raise RangeError("This must be a list of ranges.")
    if len(v) > MOST:
        raise RangeError(f"At most {MOST} ranges.")
    out = []
    for r in v:
        if not isinstance(r, dict):
            raise RangeError("Each range needs a lab, a unit and a low or a high.")
        lab = _text(r.get("lab"), "A range's lab", 120)
        unit = _text(r.get("unit"), f"The unit of the range for “{lab}”", 30)
        sex = r.get("sex") or "any"
        if sex not in ROW_SEXES:
            raise RangeError(f"The range for “{lab}”: sex must be any, female or male.")
        a, b = _age(r.get("ageFrom"), f"The range for “{lab}”: the age from"), _age(r.get("ageTo"), f"The range for “{lab}”: the age to")
        if a is not None and b is not None and a > b:
            raise RangeError(f"The range for “{lab}”: the age from is above the age to.")
        lo, hi = _number(r.get("low"), f"The low of the range for “{lab}”"), _number(r.get("high"), f"The high of the range for “{lab}”")
        if lo is None and hi is None:
            raise RangeError(f"The range for “{lab}” needs a low, a high, or both.")
        if lo is not None and hi is not None and lo > hi:
            raise RangeError(f"The range for “{lab}”: the low is above the high.")
        row = {"lab": lab, "unit": unit, "sex": sex, "ageFrom": a, "ageTo": b, "low": lo, "high": hi}
        for o in out:
            if (norm_name(o["lab"]), norm_unit(o["unit"]), o["sex"]) == (norm_name(lab), norm_unit(unit), sex) and _overlap(o, row):
                who = "anyone" if sex == "any" else sex
                raise RangeError(f"Two ranges for “{lab}” in {unit} for {who} cover the same ages; "
                                 "make their age bands separate (one row per band).")
        out.append(row)
    return out


def _overlap(a, b):
    a0, a1 = a["ageFrom"] if a["ageFrom"] is not None else 0, a["ageTo"] if a["ageTo"] is not None else AGE_MAX
    b0, b1 = b["ageFrom"] if b["ageFrom"] is not None else 0, b["ageTo"] if b["ageTo"] is not None else AGE_MAX
    return a0 <= b1 and b0 <= a1


def config(cfg):
    """What find() takes, from the merged settings (settings.merged): the ranges set, and the birth date and sex in
    About this person (#194)."""
    p = (cfg or {}).get("person") or {}
    return {"birthDate": p.get("birthDate") or "", "sex": p.get("sex") or "",
            "overrides": ((cfg or {}).get("ranges") or {}).get("overrides") or []}


def find(cfg, lab, unit, day):
    """The override row that applies to a result of lab in unit on day, or None. cfg: {"birthDate", "sex",
    "overrides"} (config() of the merged settings)."""
    rows = (cfg or {}).get("overrides") or []
    if not rows:
        return None
    sex, age = (cfg or {}).get("sex") or "", age_on((cfg or {}).get("birthDate"), day)
    n, u = norm_name(lab), norm_unit(unit)
    best = None
    for r in rows:
        if norm_name(r.get("lab")) != n or norm_unit(r.get("unit")) != u:
            continue
        if r.get("sex", "any") != "any" and r.get("sex") != sex:
            continue
        a, b = r.get("ageFrom"), r.get("ageTo")
        if a is not None or b is not None:
            if age is None or (a is not None and age < a) or (b is not None and age > b):
                continue
        if best is None or (best.get("sex", "any") == "any" and r.get("sex", "any") != "any"):
            best = r
    return best


def has_own(lo, hi, flag):
    return lo is not None or hi is not None or bool(str(flag or "").strip())


def flag_for(v, row):
    if row.get("high") is not None and v > row["high"]:
        return "H"
    if row.get("low") is not None and v < row["low"]:
        return "L"
    return ""
