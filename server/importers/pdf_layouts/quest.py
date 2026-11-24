"""Quest Diagnostics patient report.

    Test Name                     In Range    Out Of Range    Reference Range          Lab
    COMPREHENSIVE METABOLIC PANEL
       GLUCOSE                    88                          65-99 mg/dL              AMD
       CREATININE                             1.32 H          0.50-1.05 mg/dL          AMD
       LDL-CHOLESTEROL                        134 H           <100 mg/dL (calc)        AMD
       HEPATITIS C ANTIBODY       NON-REACTIVE                NON-REACTIVE             AMD

The value sits in the "In Range" or the "Out Of Range" column, with its flag (H, L, HH, LL, A) after it; the
reference range and the unit share one cell; the last cell is the performing lab's code. "(calc)" after a unit
marks a calculated value and is dropped from the unit.
"""
import re

from . import is_value, range_and_unit

NAME = "quest"
LABEL = "Quest Diagnostics"
SOURCE = "Quest"
DECIMAL_COMMA = False   # a US lab: "1,250" is 1250 (#870)

HEADER = re.compile(r"^\s*Test Name\s+In Range\s+Out Of Range\s+Reference Range\s+Lab\s*$")
END = re.compile(r"^\s*Performing Sites?:", re.I)
IGNORE = [re.compile(r"^\s*Page \d+ of \d+\s*$", re.I),
          re.compile(r"^\s*Patient:.*\bSpecimen:", re.I)]   # the patient banner, where a page repeats it below the headings
PANEL = re.compile(r"[A-Z0-9][A-Za-z0-9 ,/()&+.%-]*")       # a panel name as Quest prints it
COLLECTED = re.compile(r"Collected:\s*(?P<date>\d{2}/\d{2}/\d{4})(?:\s*/\s*(?P<time>\d{1,2}:\d{2})"
                       r"(?:\s*(?P<ampm>[AP]M)\b)?(?:\s*(?P<tz>[ECMP][SD]T)\b)?)?")
PROVIDER = re.compile(r"Ordering Physician:\s*(\S.*?)(?:\s{2,}|$)")
SPECIMEN = re.compile(r"Specimen:\s*([A-Z0-9-]{4,})")

_LAB_CODE = re.compile(r"[A-Z0-9]{2,4}")
_VALUE_FLAG = re.compile(r"(?P<value>.+?)(?:\s+(?P<flag>HH|LL|H|L|AA|A))?")


def recognise(lines):
    text = "\n".join(lines)
    return bool(re.search(r"Quest Diagnostics", text, re.I)) and any(HEADER.match(line) for line in lines)


def row(cells):
    if len(cells) not in (3, 4) or not _LAB_CODE.fullmatch(cells[-1]):
        return None
    name, mid = cells[0], cells[1:-1]
    m = _VALUE_FLAG.fullmatch(mid[0])
    if not m or not is_value(m["value"]):
        return None
    rng = unit = ""
    if len(mid) == 2:
        ru = range_and_unit(mid[1])
        if ru is None:
            return None
        rng, unit = ru
    unit = re.sub(r"\s*\(calc\)$", "", unit, flags=re.I)
    return {"name": name, "value": m["value"], "flag": m["flag"] or "", "unit": unit, "range": rng}
