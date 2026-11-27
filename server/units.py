"""Units: which values can be shown in another unit, and by exactly what factor (#41). One table for the whole app:
the cross-source match (crossmatch.py, #96) compares values with exact_factor, and the records page shows values in
the person's preferred units with conversions().

Only conversions that are exact or fixed by definition, never an estimate:

  prefix    a change of metric prefix, a power of ten: 121 g/L is 12.1 g/dL, 5.0 mmol/L is 5000 umol/L, 10*9/L is
            10*3/uL. Units are compared after common.unit_for, so "mEq/L" is "mmol/L" only for monovalent ions.
  molar     mass to amount of substance (mg/dL to mmol/L) only for a test named in TESTS with the molar mass of the
            one substance it measures: glucose, creatinine, calcium, magnesium, and urea nitrogen (BUN, measured as
            nitrogen: each urea has two nitrogen atoms, 2 x 14.007 g/mol). Tests whose factor rests on an assumed
            molecule (triglycerides as triolein, bilirubin) or a mix of substances are left out, and stay as reported.
            crossmatch.py never uses these: two sources must agree on the value itself, not on our arithmetic.
  weight    lb and kg: 1 lb is 0.45359237 kg by definition.
  temp      degrees Fahrenheit and Celsius: C = (F - 32) x 5/9. Only for a temperature itself; a change in temperature
            (a watch's "skin temperature change") would need no offset, so it is never converted.
  length    centimetres and inches (#194, a clinic's heights): 1 in is 2.54 cm by definition. The choice "ftin" (feet
            and inches) shows a series in inches; About this person says a single height in feet and inches.

A series that can be shown in another unit carries "conv" in the records the page loads (annotate, from
records._assemble): {choice: [unit, a, b]}, the value in that unit being value x a + b. The choices are the values
of the "units" setting (settings.py): "conventional" (mg/dL, g/dL) or "si" (mmol/L, umol/L, g/L) for lab tests,
"lb" or "kg" for weights, "F" or "C" for temperatures, "cm" or "ftin" for heights. A choice the series is already in,
or has no exact conversion to, is left out. The page keeps the value and unit as reported beside the converted one (www/js/units.js).
"""
import re
from decimal import Decimal

from .importers import common

# exact prefixes: mass, amount of substance and volume; a unit "<amount>/<volume>" scales by their ratio
_MASS = {"g": 0, "mg": -3, "ug": -6, "mcg": -6, "ng": -9, "pg": -12}
_MOL = {"mol": 0, "mmol": -3, "umol": -6, "nmol": -9, "pmol": -12, "meq": -3, "eq": 0}   # meq kept apart from mmol
_VOL = {"l": 0, "dl": -1, "ml": -3, "ul": -6, "fl": -15}
_COUNTS = {"10*9/l": "k/ul", "x10e9/l": "k/ul", "10^9/l": "k/ul", "giga/l": "k/ul",
           "10*12/l": "m/ul", "x10e12/l": "m/ul", "10^12/l": "m/ul", "tera/l": "m/ul"}

# canonical test (lab-aliases.json) -> (conventional unit, SI unit, molar mass in g/mol or None for a prefix change only)
TESTS = {
    "Glucose, Blood": ("mg/dL", "mmol/L", "180.156"),
    "Creatinine": ("mg/dL", "umol/L", "113.12"),
    "BUN": ("mg/dL", "mmol/L", "28.014"),
    "Calcium": ("mg/dL", "mmol/L", "40.078"),
    "Magnesium": ("mg/dL", "mmol/L", "24.305"),
    "Hemoglobin": ("g/dL", "g/L", None),
    "MCHC": ("g/dL", "g/L", None),
    "Albumin": ("g/dL", "g/L", None),
    "Total Protein": ("g/dL", "g/L", None),
}

LB_KG = Decimal("0.45359237")
_WEIGHT = {"lb": "lb", "lbs": "lb", "[lb_av]": "lb", "kg": "kg"}
_TEMP = {"°f": "F", "f": "F", "degf": "F", "[degf]": "F", "°c": "C", "c": "C", "cel": "C", "degc": "C"}
WEIGHT_UNITS, TEMP_UNITS = {"lb": "lb", "kg": "kg"}, {"F": "°F", "C": "°C"}   # choice -> the unit shown
IN_CM = Decimal("2.54")
_LENGTH = {"cm": "cm", "in": "in", "[in_i]": "in", "inch": "in", "inches": "in"}
LENGTH_UNITS = {"cm": "cm", "ftin": "in"}   # #194: a chart of heights in feet and inches is drawn in inches


def _unit(canon, u):
    u = common.unit_for(canon, u)
    return _COUNTS.get(u, u)


def _scale(u):
    """(dimension, power of ten) for a "<prefix>/<volume>" unit, or None."""
    top, _, bottom = u.partition("/")
    if bottom not in _VOL:
        return None
    for kind, table in (("mass", _MASS), ("mol", _MOL)):
        if top in table:
            dim = kind + (":eq" if top.endswith("eq") else "")
            return dim, table[top] - _VOL[bottom]
    return None


def exact_factor(canon, ua, ub):
    """The exact Decimal that turns a value in unit ua into one in ub by a change of prefix, or None."""
    a, b = _unit(canon, ua), _unit(canon, ub)
    if a == b:
        return Decimal(1)
    sa, sb = _scale(a), _scale(b)
    if sa and sb and sa[0] == sb[0]:
        return Decimal(10) ** (sa[1] - sb[1])
    return None


def lab_factor(canon, ua, ub):
    """exact_factor, or for a test in TESTS with a molar mass, mass per volume to amount per volume (and back)."""
    f = exact_factor(canon, ua, ub)
    if f is not None or canon not in TESTS or TESTS[canon][2] is None:
        return f
    sa, sb = _scale(_unit(canon, ua)), _scale(_unit(canon, ub))
    if not sa or not sb or {sa[0], sb[0]} != {"mass", "mol"}:
        return None
    m = Decimal(TESTS[canon][2])
    f = Decimal(10) ** (sa[1] - sb[1])
    return f / m if sa[0] == "mass" else f * m


def _key(u):
    return (u or "").strip().lower().replace(" ", "")


def _num(d):
    return float(d)


def conversions(canon, unit, change=False):
    """{choice: [unit, a, b]} for a series of this canonical test (or None) in this unit: see the docstring.
    change: the values are differences (a temperature change), so nothing with an offset applies."""
    out = {}
    k = _key(unit)
    if k in _WEIGHT:
        have = _WEIGHT[k]
        for choice, shown in WEIGHT_UNITS.items():
            if choice != have:
                out[choice] = [shown, _num(LB_KG if have == "lb" else 1 / LB_KG), 0]
        return out
    if k in _LENGTH:   # #194
        have = _LENGTH[k]
        for choice, shown in LENGTH_UNITS.items():
            if shown != have:
                out[choice] = [shown, _num(IN_CM if have == "in" else 1 / IN_CM), 0]
        return out
    if k in _TEMP:
        have = _TEMP[k]
        if change:
            return out
        for choice, shown in TEMP_UNITS.items():
            if choice == have:
                continue
            # C = (F - 32) x 5/9 = F x 5/9 - 160/9; F = C x 9/5 + 32
            out[choice] = [shown, 5 / 9, -160 / 9] if have == "F" else [shown, 9 / 5, 32]
        return out
    if canon in TESTS:
        conv, si, _ = TESTS[canon]
        for choice, target in (("conventional", conv), ("si", si)):
            if _unit(canon, unit) == _unit(canon, target):
                continue
            f = lab_factor(canon, unit, target)
            if f is not None:
                out[choice] = [target.replace("umol", "µmol"), _num(f), 0]
    return out


def _canon(s):
    name = str(s.get("name") or "").strip()
    code = str(s.get("loinc") or "").strip() or None
    if not code and isinstance(s.get("key"), str) and s["key"].startswith("loinc:"):
        code = s["key"][6:].split("|", 1)[0].strip() or None
    return common.canonical_lab(name, code) or name


def annotate(data):
    """Add "conv" to each lab series that can be shown in another unit, "uk" to each, its unit's comparable form
    (common.unit_key: the page charts a test's series in one unit spelled two ways together, www/js/same.js joinSeries;
    #1102: "10³/µL" and "K/µL" platelets were two), and "unitConv" for the units the page assumes itself (clinic weights,
    in lb). Returns data."""
    for s in data.get("labs") or []:
        if isinstance(s, dict):
            s["uk"] = common.unit_key(s.get("unit"))
            c = conversions(_canon(s), s.get("unit"))
            if c:
                s["conv"] = c
            else:
                s.pop("conv", None)
    data["unitConv"] = {"lb": conversions(None, "lb")}
    return data


CHANGES = {"skin_temp"}   # watch metrics that are differences, not values (watch/receiver.py)


GLUCOSE = {"glucose_meter", "glucose_avg", "glucose_min", "glucose_max"}   # in mg/dL, shown in mmol/L by choice (#181)


def annotate_watch(summary):
    """Add "conv" to each watch metric that can be shown in another unit (weights in lb; never a change; blood
    glucose by the lab choice, as lab glucose is)."""
    for m in (summary or {}).get("metrics") or []:
        if isinstance(m, dict):
            canon = "Glucose, Blood" if m.get("key") in GLUCOSE else None
            c = conversions(canon, m.get("unit"), change=m.get("key") in CHANGES)
            if c:
                m["conv"] = c
    if (summary or {}).get("glucose_readings"):   # typed in at home, mg/dL (#224): shown in this person's lab unit too
        summary["glucose_conv"] = conversions("Glucose, Blood", "mg/dL")
    return summary


# ---------------------------------------------------------------- a unit as people write it (#1103)

_SUP = str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹")
_A = re.ASCII   # (\b and the classes as JavaScript reads them)


def said(u):
    """A unit as people write it, the rule www/js/util.js unitSaid is for the page, step for step (tests/test_units.py
    holds them to the same answers): the codes reports carry (UCUM) said as on a lab printout, "10*3/uL" and "x10E3/uL"
    as 10³/µL, "mm[Hg]" mmHg, "[in_i]" in, "Cel" °C, "{beats}/min" beats/min, "kg/m2" kg/m², "umol/L" µmol/L; "U/L"
    (enzyme units) kept. Only how it's written: for what the server writes out itself (the visit summary's PDF, a share
    link's measures), never for what's stored or compared."""
    if not isinstance(u, str) or not u:
        return u
    if re.fullmatch(r"cel", u.strip(), re.I | _A):
        return "°C"
    u = re.sub(r"\[deg([FC])\]", lambda m: "°" + m.group(1).upper(), u, flags=re.I | _A)
    u = re.sub(r"\[([^\]_]+)(?:_[^\]]*)?\]", r"\1", u)
    u = re.sub(r"\{([^}]*)\}", lambda m: m.group(1).replace("_", " "), u)
    u = re.sub(r"(?<![A-Za-z\d.])(?:x\s*)?10\s*(?:\*|\^|e)\s*(-?\d+)", lambda m: "10" + m.group(1).translate(_SUP), u, flags=re.I | _A)
    u = re.sub(r"(?<![A-Za-z])u(?=(?:g|mol|L|l|IU|U|eq|m)\b)", "µ", u, flags=_A)
    return re.sub(r"(?<![A-Za-z])([cm]?m)([23])(?!\d)", lambda m: m.group(1) + m.group(2).translate(_SUP), u, flags=_A)
