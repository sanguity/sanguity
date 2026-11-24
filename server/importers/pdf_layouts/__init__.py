"""Layout templates for PDF lab reports: one small module per lab's report layout, read by `pdf_labs.py`.

A layout module (docs/importers.md, "PDF lab reports", has the full version):

    NAME = "quest"                    short id: lower-case letters, digits and dashes
    LABEL = "Quest Diagnostics"       the lab, as shown on the results
    SOURCE = "Quest"                  short source tag on lab points and lab days
    def recognise(lines) -> bool      page 1's text lines: is this the lab's layout? Look for the lab's name and the
                                      results table's column headings, never for one of them alone
    HEADER = re.compile(...)          the results table's column headings; rows are read only below it, on each page
    END = re.compile(...) or None     a line that ends the table on its page (a performing-lab block), optional
    IGNORE = [re.compile(...)]        lines inside the table that are neither results nor panels (page footers)
    PANEL = re.compile(...)           a panel heading: one cell, at the headings' indent, fully matching this
    COLLECTED = re.compile(...)       the collection date: groups date (MM/DD/YYYY), and optionally time, ampm, tz
    PROVIDER = re.compile(...)        the ordering provider, group 1 (optional)
    SPECIMEN = re.compile(...)        the specimen or accession number, group 1 (optional)
    def row(cells) -> dict or None    one table line, split into cells where the text has a gap of 2+ spaces:
                                      {"name", "value", "flag", "unit", "range"} as printed, or None when the line
                                      isn't a result in this layout (it becomes a warning, never a guessed value)

The helpers below read the pieces most layouts share: values, flags, reference ranges and units.

Standard library only.
"""
import importlib, logging, pkgutil, re

from ..common import BOUNDS, NUMBER, range_part

log = logging.getLogger("sanguity.importers")

REQUIRED = ("NAME", "LABEL", "SOURCE", "recognise", "HEADER", "PANEL", "COLLECTED", "row")


def layouts():
    """Every layout module in this package, sorted by NAME. One that fails to load or lacks part of the contract is
    logged and left out."""
    found = []
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_") or info.ispkg:
            continue
        try:
            mod = importlib.import_module(f"{__name__}.{info.name}")
        except Exception:
            log.exception("PDF lab layout %s failed to load", info.name)
            continue
        missing = [a for a in REQUIRED if not hasattr(mod, a)]
        if missing:
            log.warning("PDF lab layout %s left out: no %s", info.name, ", ".join(missing))
            continue
        found.append(mod)
    return sorted(found, key=lambda m: m.NAME)


# ------------------------------------------------------------------ shared pieces of a row
_NUMERIC_VALUE = re.compile(rf"(?:{BOUNDS})?\s*{NUMBER}")   # a number as printed, a bound's too: read by number_text (#870)
_OTHER_VALUE = re.compile(r"\d+\+|1:\d+|[A-Za-z][A-Za-z ,'/-]{0,39}")   # "2+", "1:40", "NON-REACTIVE", "Yellow"
NOT_VALUES = {"see note", "see below", "see comment", "tnp", "dnr", "qns", "cancelled", "canceled", "pending",
              "not performed", "test not performed", "comment", "note"}
_UNIT = re.compile(r"[A-Za-z%µμ][A-Za-z0-9%µμ/.*^ ]*|[x×]?10\^?[Ee]?\d+/[A-Za-z]+")
_UNIT_WORDS = re.compile(r"(?i)fl|pg|sec|seconds|ratio|index|units?|copies|mm|cm|kg|lbs?|titer|cells|mosm|mIU|IU|U")
NOT_ESTABLISHED = re.compile(r"(?i)not estab(?:lished|\.)?")


def is_number(s):
    """A result value printed as a number, or a bound's ("88", "<0.5", "=<5", "1,250", "4,5", "1.2E-3"), whether or not
    common.value_number can read it ("1,250" where the decimal mark isn't known)."""
    return bool(_NUMERIC_VALUE.fullmatch((s or "").strip()))


def is_value(s):
    """A result value as printed: a number ("88", "<0.5", "1,250", "0,05", "1.2E-3": common.NUMBER, which pdf_labs reads
    by common.value_number, #870: "0,05" and "4,5" weren't values, so their rows were left out) or a short text result
    ("NON-REACTIVE", "2+"). "SEE NOTE" and the like are not values."""
    s = (s or "").strip()
    if not s or s.lower() in NOT_VALUES:
        return False
    return is_number(s) or bool(_OTHER_VALUE.fullmatch(s))


def is_text_value(s):
    """A short text result or reference such as "NEGATIVE" or "Non Reactive": letters only, no digits."""
    s = (s or "").strip()
    return bool(s) and s.lower() not in NOT_VALUES and bool(re.fullmatch(r"[A-Za-z][A-Za-z ,'-]{0,39}", s))


def is_range(s):
    """A reference range as printed, the whole cell: "65-99", "<200", "> OR = 60", "less than or equal to 5" (by
    common.range_part, the one parser of a range as text's reading, #850), "Not Estab." or a text result like
    "NEGATIVE"."""
    s = (s or "").strip()
    rng, rest = range_part(s)
    return bool(rng and not rest or NOT_ESTABLISHED.fullmatch(s) or is_text_value(s))


def is_unit(s):
    """A unit as printed: "mg/dL", "x10E3/uL", "%", "fL", "mL/min/1.73m2". A word without a slash or % counts only
    when it's a known unit word, so a text reference like "NEGATIVE" isn't read as one."""
    s = (s or "").strip()
    if not s or not _UNIT.fullmatch(s):
        return False
    return "/" in s or "%" in s or bool(_UNIT_WORDS.fullmatch(s))


def range_and_unit(s):
    """(range, unit) from one cell holding both, as Quest prints them ("65-99 mg/dL", "<5.7 % of total Hgb",
    "> OR = 60 mL/min/1.73m2", "NON-REACTIVE", "mg/dL" with no range; the range where common.range_part finds it,
    #850); None when the cell is neither. The range's low, high and edge are read by common.range_text with the whole
    cell (pdf_labs.py)."""
    s = (s or "").strip()
    rng, rest = range_part(s)
    if rng and (not rest or re.fullmatch(r"[A-Za-z%µμ][^<>]*", rest)):
        return rng, rest
    if is_unit(s):
        return "", s
    if is_text_value(s) or NOT_ESTABLISHED.fullmatch(s):
        return s, ""
    return None
