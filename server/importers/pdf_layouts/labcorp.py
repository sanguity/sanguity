"""Labcorp patient report.

    Tests                         Result     Flag     Units          Reference Interval     Lab
    CBC With Differential/Platelet
      WBC                         7.2                 x10E3/uL       3.4-10.8               01
      RBC                         4.09       Low      x10E6/uL       4.14-5.80              01
      Neutrophils                 58                  %              Not Estab.             01
      Hepatitis C Virus Ab        Non Reactive                       Non Reactive           01

Result, flag (High, Low, Abnormal, Critical...), units and reference interval are separate columns, and any of the
flag, units and reference may be empty; the last cell is the performing lab's number.
"""
import re

from . import is_range, is_unit, is_value

NAME = "labcorp"
LABEL = "Labcorp"
SOURCE = "Labcorp"

HEADER = re.compile(r"^\s*Tests\s+Result\s+Flag\s+Units\s+Reference Interval\s+Lab\s*$")
END = re.compile(r"^\s*(?:Performing Labs?|Lab\s+Performing Laboratory)\b", re.I)
IGNORE = [re.compile(r"^\s*Page \d+ of \d+\s*$", re.I),
          re.compile(r"^\s*Patient Name:.*\bSpecimen ID:", re.I),   # the patient banner, where a page repeats it below the headings
          re.compile(r"^\s*Final Report\s*$", re.I)]
PANEL = re.compile(r"[A-Z][A-Za-z0-9 ,/()&+.%-]*")   # no colon: "Please Note:" isn't a panel
COLLECTED = re.compile(r"Date Collected:\s*(?P<date>\d{2}/\d{2}/\d{4})(?:.*?Time Collected:\s*(?P<time>\d{1,2}:\d{2})"
                       r"(?:\s*(?P<ampm>[AP]M)\b)?(?:\s*(?P<tz>[ECMP][SD]T)\b)?)?")
PROVIDER = re.compile(r"Ordering Physician:\s*(\S.*?)(?:\s{2,}|$)")
SPECIMEN = re.compile(r"Specimen ID:\s*([0-9][0-9-]{5,})")

_LAB_CODE = re.compile(r"\d{2}|[A-Z]{2}")
_FLAG = re.compile(r"(?:Critical |Panic )?(?:High|Low)|Abnormal|Critical|Alert|HH|LL|H|L|A")


def recognise(lines):
    text = "\n".join(lines)
    return bool(re.search(r"\blabcorp\b|Laboratory Corporation of America", text, re.I)) and \
        any(HEADER.match(line) for line in lines)


def row(cells):
    if len(cells) < 3 or not _LAB_CODE.fullmatch(cells[-1]):
        return None
    name, value, rest = cells[0], cells[1], list(cells[2:-1])
    if not is_value(value):
        return None
    flag = rest.pop(0) if rest and _FLAG.fullmatch(rest[0]) else ""
    unit = rng = ""
    if len(rest) == 2:
        unit, rng = rest
        if not is_unit(unit) or not is_range(rng):
            return None
    elif len(rest) == 1:
        if is_range(rest[0]):
            rng = rest[0]
        elif is_unit(rest[0]):
            unit = rest[0]
        else:
            return None
    elif rest:
        return None
    return {"name": name, "value": value, "flag": flag, "unit": unit, "range": rng}
