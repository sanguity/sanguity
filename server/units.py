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

A series that can be shown in another unit carries "conv" in the records the page loads (annotate, from
records._assemble): {choice: [unit, a, b]}, the value in that unit being value x a + b. The choices are the values
of the "units" setting (settings.py): "conventional" (mg/dL, g/dL) or "si" (mmol/L, umol/L, g/L) for lab tests,
"lb" or "kg" for weights, "F" or "C" for temperatures. A choice the series is already in, or has no exact conversion
to, is left out. The page keeps the value and unit as reported beside the converted one (www/js/units.js).
"""
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
    """Add "conv" to each lab series that can be shown in another unit, and "unitConv" for the units the page
    assumes itself (clinic weights, in lb). Returns data."""
    for s in data.get("labs") or []:
        if isinstance(s, dict):
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
    return summary
