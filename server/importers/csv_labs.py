"""Generic CSV lab importer: the catch-all for lab results from any source no other importer reads.

A spreadsheet with one result per row and columns for date, test, value, unit and reference range (or low and
high limits), plus an optional flag, panel, time and lab column. Headers are matched by common spellings; a
caller can pass `mapping` to `parse` for files whose headers differ (the column-mapping step of issue #27).

Comma, semicolon and tab delimiters, a UTF-8 or UTF-16 byte order mark, quoted fields, decimal commas, ISO and
common US/European dates, "<5" and ">200" style values and ranges like "13.5 - 17.5", "<200" or ">60" are read.
Rows that can't be read are skipped with a warning. Lab names are kept as reported.

The result is the data.json shape that build.py writes: one hidden `labitem` event per test and time, one `lab`
event per day linking to them, and a `labs` series per test.

Every event and series carries a stable source `key`, so a re-import of the same or an extended file matches
instead of duplicating: "csv:h:" + 16 hex characters of a SHA-256 over date-time, test (trimmed, lower case),
value as reported and unit for a labitem; "csv:labday:<YYYY-MM-DD>" for a day; "loinc:<code>" or
"csv:<test lower case>|<unit>" for a series. File names, row numbers and column order are left out.

read_row (one row's result), date_order, timestamp, number, dedupe and build are public: hand entry (routes_manual.py) reads typed rows
with them, so a typed result gets the same keys, names, ranges and flags as the same row in a spreadsheet.
"""
import collections
import csv
import hashlib
import io
import os
import re
from datetime import datetime

from .common import canonical_lab, lab_day_sub, lab_day_title, loinc_of

NAME = "csv"
LABEL = "Lab results spreadsheet (CSV)"
SOURCE = "CSV"   # source tag on lab points and lab-day events

EXTENSIONS = (".csv", ".tsv", ".txt")
MAX_BYTES = 50 * 1024 * 1024
HEADER_SCAN = 10   # a title or export note may sit above the header row

# field -> header spellings, compared after lower-casing and turning punctuation into spaces
FIELDS = {
    "date": ["date", "collected", "collection date", "collection date time", "collected date", "date collected",
             "specimen date", "sample date", "draw date", "date drawn", "test date", "observation date",
             "result date", "date of service", "service date", "date time", "datetime", "timestamp",
             "effective date", "reported", "date reported", "resulted"],
    "time": ["time", "collection time", "collected time", "time collected", "draw time"],
    "test": ["test", "test name", "tests", "analyte", "analyte name", "component", "component name", "name",
             "lab test", "lab name", "lab test name", "parameter", "item", "observation", "observation name",
             "result name", "measure", "marker", "biomarker", "description", "test description"],
    "value": ["value", "result", "result value", "results", "numeric value", "value numeric", "measurement",
              "reading", "observed value", "observation value", "your value", "your result", "level", "amount"],
    "unit": ["unit", "units", "uom", "unit of measure", "units of measure", "result unit", "result units",
             "measurement unit"],
    "range": ["range", "reference range", "ref range", "reference interval", "ref interval", "normal range",
              "reference", "standard range", "reference values", "reference value", "expected range",
              "normal", "normal values", "ref", "ref ranges", "reference ranges", "target range"],
    "low": ["low", "ref low", "reference low", "range low", "low range", "lower limit", "lower bound", "min",
            "minimum", "normal low", "low limit", "low normal", "lo", "ref range low", "reference range low",
            "lower reference limit", "lower ref"],
    "high": ["high", "ref high", "reference high", "range high", "high range", "upper limit", "upper bound", "max",
             "maximum", "normal high", "high limit", "high normal", "hi", "ref range high",
             "reference range high", "upper reference limit", "upper ref"],
    "flag": ["flag", "flags", "abnormal flag", "abnormal flags", "abnormal", "abn", "h l", "hl", "interpretation",
             "abnormal indicator", "result flag", "out of range", "indicator"],
    "panel": ["panel", "panel name", "order", "order name", "ordered test", "test group", "group", "category",
              "battery", "profile", "test panel"],
    "lab": ["lab", "laboratory", "performing lab", "performing laboratory", "lab name performing", "source",
            "provider", "facility", "performed at", "resulting lab"],
    "loinc": ["loinc", "loinc code", "loinc number", "loinc id", "loinc num"],
}
REQUIRED = ("date", "test", "value")
DESCRIBE = {"date": "the date", "test": "the test name", "value": "the result value"}

# a downloadable starting point for hand-made spreadsheets (served by the import UI once it exists)
TEMPLATE = ("Date,Test,Value,Unit,Reference range,Flag,Panel\r\n"
            "2025-03-14,Hemoglobin,14.2,g/dL,13.5 - 17.5,,CBC\r\n"
            "2025-03-14,LDL Cholesterol,131,mg/dL,<100,H,Lipid panel\r\n")


def _norm(h):
    return " ".join(re.sub(r"[^0-9a-z]+", " ", (h or "").lower().replace("µ", "u")).split())


_LOOKUP = {s: f for f, spellings in FIELDS.items() for s in spellings}


def _field(header):
    """The field a header names, or None. Parenthesised notes ("Value (numeric)") are tried without."""
    n = _norm(header)
    if n in _LOOKUP:
        return _LOOKUP[n]
    bare = _norm(re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", header or ""))
    return _LOOKUP.get(bare)


def _columns(header):
    """{field: column index} for a header row; the first column wins when two name the same field."""
    cols = {}
    for i, h in enumerate(header):
        f = _field(h)
        if f and f not in cols:
            cols[f] = i
    return cols


# ------------------------------------------------------------------ reading text
def _decode(raw):
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _read_text(path, limit=None):
    with open(path, "rb") as f:
        raw = f.read(limit if limit else MAX_BYTES + 1)
    if limit is None and len(raw) > MAX_BYTES:
        raise ValueError(f"{os.path.basename(path)} is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    if limit and raw[:2] in (b"\xff\xfe", b"\xfe\xff") and len(raw) % 2:
        raw = raw[:-1]
    text = _decode(raw)
    if limit and len(raw) >= limit - 1:   # drop a line that the byte limit cut in half
        text = text.rsplit("\n", 1)[0] if "\n" in text else text
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _delimiter(line):
    """The delimiter of a header line: the one of tab, semicolon and comma seen most outside quotes."""
    counts, quoted = collections.Counter(), False
    for ch in line:
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch in "\t;,":
            counts[ch] += 1
    if not counts:
        return ","
    return max("\t;,", key=lambda c: (counts[c], c == "\t", c == ";"))


def _header(lines):
    """(index, delimiter, cells, columns) of the best header row among the first lines."""
    best = None
    for i, line in enumerate(lines[:HEADER_SCAN]):
        if not line.strip():
            continue
        d = _delimiter(line)
        cells = next(csv.reader([line], delimiter=d), [])
        cols = _columns(cells)
        score = sum(f in cols for f in REQUIRED) * 10 + len(cols)
        if best is None or score > best[0]:
            best = (score, i, d, cells, cols)
        if all(f in cols for f in REQUIRED):
            break
    return best[1:] if best else None


def _candidates(files):
    return sorted((rel, p) for rel, p in files.items() if rel.lower().endswith(EXTENSIONS) and os.path.isfile(p))


# ------------------------------------------------------------------ detect
def detect(files):
    """How sure we are (0 to 1) that the upload is a lab-results spreadsheet, from header rows only.

    Capped below 1 so a source-specific importer that also recognises the file wins."""
    best = 0.0
    for _, path in _candidates(files):
        try:
            text = _read_text(path, limit=16384)
        except OSError:
            continue
        h = _header(text.split("\n"))
        if not h:
            continue
        cols = h[3]
        if not all(f in cols for f in REQUIRED):
            score = 0.1 if sum(f in cols for f in REQUIRED) == 2 else 0.0   # close enough to offer the mapper
        else:
            score = 0.5 + 0.1 * ("unit" in cols) + 0.1 * ("range" in cols or "low" in cols or "high" in cols)
            score += 0.05 * ("flag" in cols)
        best = max(best, score)
    return round(best, 2)


# ------------------------------------------------------------------ numbers, values and ranges
_NUM = r"[-+]?(?:\d[\d.,'  ]*\d|\d)(?:[eE][-+]?\d+)?|[-+]?[.,]\d+"


def _number(s, decimal_comma):
    """A float from "1,234.5", "1.234,5", "4,5" or "4.5"; None when it isn't one number."""
    s = (s or "").strip().replace(" ", "").replace(" ", "").replace("'", "")
    if not s or not re.fullmatch(r"[-+]?[\d.,]*\d(?:[eE][-+]?\d+)?|[-+]?[\d.,]*[.,]\d+", s):
        return None
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        # "1,200" in a comma-delimited file is a thousands separator; "4,5" or "1,25" is a decimal comma
        if not decimal_comma and re.fullmatch(r"[-+]?\d{1,3}(,\d{3})+", s):
            s = s.replace(",", "")
        elif s.count(",") == 1:
            s = s.replace(",", ".")
        else:
            return None
    elif s.count(".") > 1:
        if decimal_comma and re.fullmatch(r"[-+]?\d{1,3}(\.\d{3})+", s):
            s = s.replace(".", "")
        else:
            return None
    try:
        x = float(s)
    except ValueError:
        return None
    return int(x) if x.is_integer() and "." not in s and "e" not in s.lower() else x


_CMP = {"<": "<", "<=": "<=", "=<": "<=", "≤": "<=", ">": ">", ">=": ">=", "=>": ">=", "≥": ">="}
_VALUE_RE = re.compile(r"\s*(<=|>=|=<|=>|<|>|≤|≥)?\s*(" + _NUM + r")\s*(.*?)\s*$")
_UNIT_RE = re.compile(r"(?=.*[A-Za-z%µμ])[A-Za-zµμ%/^*0-9.\s]+")
_TRAILING_FLAG = re.compile(r"^(?:(.*?)\s+)?(HH|LL|H|L|(?i:high|low))\s*\*?$")   # a flag after the value or unit


def _value(s, decimal_comma):
    """(number or None, comparator or "", trailing text) for a reported value like "<5", "4,5" or "18.1 H"."""
    m = _VALUE_RE.match(s or "")
    if not m:
        return None, "", ""
    x = _number(m.group(2), decimal_comma)
    if x is None:
        return None, "", ""
    return x, _CMP.get(m.group(1) or "", ""), m.group(3)


_RANGE_RE = re.compile(r"\s*(" + _NUM + r")\s*(?:-|–|—|to|\.\.)\s*(" + _NUM + r")\s*(.*)$", re.I)
_BOUND_RE = re.compile(r"\s*(<=|>=|=<|=>|<|>|≤|≥|up to|less than|greater than|under|over|below|above)\s*(" + _NUM + r")\s*(.*)$",
                       re.I)


def _range(s, decimal_comma):
    """(low, high) from "13.5 - 17.5", "13,5-17,5", "<200", ">= 60" or "up to 5"; (None, None) when unreadable."""
    s = (s or "").strip()
    if not s:
        return None, None
    m = _RANGE_RE.match(s)
    if m:
        lo, hi = _number(m.group(1), decimal_comma), _number(m.group(2), decimal_comma)
        if lo is not None and hi is not None and lo <= hi:
            return lo, hi
    m = _BOUND_RE.match(s)
    if m:
        x = _number(m.group(2), decimal_comma)
        if x is not None:
            op = m.group(1).lower()
            return (None, x) if op[0] in "<=≤" or op in ("up to", "less than", "under", "below") else (x, None)
    return None, None


def _fmt(x):
    return "" if x is None else str(x)


_FLAGS = {"h": "H", "hh": "H", "high": "H", "hi": "H", "a h": "H", ">": "H", "above": "H", "above high normal": "H",
          "critical high": "H", "panic high": "H", "l": "L", "ll": "L", "low": "L", "lo": "L", "<": "L",
          "below": "L", "below low normal": "L", "critical low": "L", "panic low": "L"}


def _flag(text, x, cmp, lo, hi):
    """H, L or "" from a flag column, falling back to the range."""
    t = _norm(text) or (text or "").strip()
    if t in _FLAGS:
        return _FLAGS[t]
    direction = ""
    if x is not None:
        if cmp in (">", ">=") and hi is not None and x >= hi or not cmp.startswith("<") and hi is not None and x > hi:
            direction = "H"
        elif cmp in ("<", "<=") and lo is not None and x <= lo or not cmp.startswith(">") and lo is not None and x < lo:
            direction = "L"
    return direction   # "A", "Abnormal", "N", "Normal" and the like: the range says which way


# ------------------------------------------------------------------ dates
_MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_ISO = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[T ]+(.*))?$")
_COMPACT = re.compile(r"(\d{4})(\d{2})(\d{2})(?:[T ]?(\d{2})(\d{2})(\d{2})?)?$")
_NUMERIC = re.compile(r"(\d{1,2})([-/.])(\d{1,2})\2(\d{2}|\d{4})(?:[T ,]+(.*))?$")
_TEXT_DMY = re.compile(r"(\d{1,2})[- ]([A-Za-z]{3,9})\.?[- ,]+(\d{2}|\d{4})(?:[T ,]+(.*))?$")
_TEXT_MDY = re.compile(r"([A-Za-z]{3,9})\.? (\d{1,2})(?:st|nd|rd|th)?,? (\d{4})(?:[T ,]+(.*))?$")
_TIME = re.compile(r"(\d{1,2})[:.h](\d{2})(?:[:.](\d{2}))?(?:[.,]\d+)?\s*([AaPp]\.?[Mm]\.?)?\s*(Z|[+-]\d{2}:?\d{2})?\s*$")


def _year(y):
    y = int(y)
    return y if y >= 100 else (2000 + y if y < 70 else 1900 + y)


def _time(s):
    """(h, m, s, offset) from "08:30", "8:30 PM", "08:30:15+01:00"; None when unreadable, () when empty."""
    s = (s or "").strip()
    if not s:
        return ()
    m = _TIME.match(s)
    if not m:
        return None
    h, mi, sec = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    ap = (m.group(4) or "").lower().replace(".", "")
    if ap:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if ap == "pm" else 0)
    if h > 23 or mi > 59 or sec > 59:
        return None
    off = m.group(5) or ""
    if off and off != "Z" and ":" not in off:
        off = off[:3] + ":" + off[3:]
    return h, mi, sec, ("+00:00" if off == "Z" else off)


def _date_parts(s):
    """("ymd", (y, m, d), time) for unambiguous forms; ("dm?", (a, b, y, sep), time) for 03/04/2025; None."""
    s = (s or "").strip()
    m = _ISO.match(s)
    if m:
        return "ymd", (int(m.group(1)), int(m.group(2)), int(m.group(3))), m.group(4)
    m = _COMPACT.match(s)
    if m:
        t = f"{m.group(4)}:{m.group(5)}:{m.group(6) or '00'}" if m.group(4) else None
        return "ymd", (int(m.group(1)), int(m.group(2)), int(m.group(3))), t
    m = _NUMERIC.match(s)
    if m:
        return "dm?", (int(m.group(1)), int(m.group(3)), _year(m.group(4)), m.group(2)), m.group(5)
    m = _TEXT_DMY.match(s)
    if m and m.group(2)[:3].lower() in _MONTHS:
        return "ymd", (_year(m.group(3)), _MONTHS[m.group(2)[:3].lower()], int(m.group(1))), m.group(4)
    m = _TEXT_MDY.match(s)
    if m and m.group(1)[:3].lower() in _MONTHS:
        return "ymd", (int(m.group(3)), _MONTHS[m.group(1)[:3].lower()], int(m.group(2))), m.group(4)
    return None


def _date_order(values):
    """How to read numeric dates like 03/04/2025 in one file: (day_first by separator, ambiguous?).

    A first part over 12 means day first, a second part over 12 means month first. Dots and dashes are
    European (day first) unless the file shows otherwise; slashes with nothing to tell are read as US."""
    seen = collections.defaultdict(lambda: [False, False])   # sep -> [day first seen, month first seen]
    ambiguous = set()
    for v in values:
        p = _date_parts(v)
        if not p or p[0] != "dm?":
            continue
        a, b, _, sep = p[1]
        if a > 12 >= b:
            seen[sep][0] = True
        elif b > 12 >= a:
            seen[sep][1] = True
        elif a != b:
            ambiguous.add(sep)
    order = {}
    for sep in set(seen) | ambiguous:
        dayf, monf = seen[sep]
        if dayf and monf:
            order[sep] = None   # mixed in one file: can't tell
        elif dayf or monf:
            order[sep] = dayf
        else:
            order[sep] = sep != "/"
    guessed = {sep for sep in ambiguous if not any(seen[sep])}
    return order, guessed


def _timestamp(date_s, time_s, order):
    """ISO timestamp like build.py's iso(): date-only values get T12:00:00. None when unreadable."""
    p = _date_parts(date_s)
    if not p:
        return None
    kind, parts, t_inline = p
    if kind == "ymd":
        y, mo, d = parts
    else:
        a, b, y, sep = parts
        day_first = order.get(sep)
        if day_first is None and a != b and a <= 12 and b <= 12:
            return None
        if a > 12:
            day_first = True
        elif b > 12:
            day_first = False
        d, mo = (a, b) if day_first else (b, a)
    try:
        datetime(y, mo, d)
    except ValueError:
        return None
    tm = _time(time_s) if (time_s or "").strip() else _time(t_inline)
    if tm is None:
        return None
    if not tm:
        return f"{y:04d}-{mo:02d}-{d:02d}T12:00:00"
    h, mi, sec, off = tm
    return f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:{sec:02d}{off}"


# ------------------------------------------------------------------ parse
class _Warnings:
    """Collects warnings, folding repeats of one kind into a count so a big file doesn't drown the list."""
    SHOW = 5

    def __init__(self):
        self.plain, self.kinds = [], collections.OrderedDict()

    def add(self, text):
        self.plain.append(text)

    def row(self, kind, where):
        self.kinds.setdefault(kind, []).append(where)

    def list(self):
        out = list(self.plain)
        for kind, where in self.kinds.items():
            shown = ", ".join(where[:self.SHOW]) + (f" and {len(where) - self.SHOW} more" if len(where) > self.SHOW else "")
            out.append(f"Skipped {len(where)} row{'s' if len(where) != 1 else ''} {kind}: {shown}.")
        return out


def _resolve_mapping(header, mapping):
    """{field: index} from a caller's {field: header text or index}."""
    cols = {}
    for f, h in mapping.items():
        if f not in FIELDS or h in (None, ""):
            continue
        if isinstance(h, int):
            if not 0 <= h < len(header):
                raise ValueError(f"Column {h + 1} for {DESCRIBE.get(f, f)} isn't in the file.")
            cols[f] = h
            continue
        hits = [i for i, c in enumerate(header) if c.strip() == str(h).strip()] or \
               [i for i, c in enumerate(header) if _norm(c) == _norm(str(h))]
        if not hits:
            raise ValueError(f"There's no column called {h!r} in the file.")
        cols[f] = hits[0]
    return cols


def _missing_message(rel, header, cols):
    missing = [f for f in REQUIRED if f not in cols]
    names = " and ".join(DESCRIBE[f] for f in missing)
    examples = "; ".join(f"{DESCRIBE[f]}: " + ", ".join(repr(s.title()) for s in FIELDS[f][:3]) for f in missing)
    found = ", ".join(h.strip() for h in header if h.strip()) or "nothing"
    return (f"{rel} has no column for {names}. Name the column{'s' if len(missing) > 1 else ''} with a header "
            f"such as {examples}, or choose the columns by hand. Columns found: {found}.")


def _rows(rel, path, mapping):
    """(cells of each data row with its line number, columns, delimiter) for one file."""
    text = _read_text(path)
    lines = text.split("\n")
    h = _header(lines)
    if not h:
        raise ValueError(f"{rel} is empty.")
    i, delim, header, cols = h
    if mapping:
        cols = {**cols, **_resolve_mapping(header, mapping)}
    if any(f not in cols for f in REQUIRED):
        raise ValueError(_missing_message(rel, header, cols))
    body = "\n".join(lines[i + 1:])
    out = []
    reader = csv.reader(io.StringIO(body), delimiter=delim)
    try:
        for cells in reader:
            out.append((i + 1 + reader.line_num, cells))
    except csv.Error as e:
        raise ValueError(f"{rel} couldn't be read as a spreadsheet near line {i + 1 + reader.line_num}: {e}.") from None
    return out, cols, delim


def parse(files, mapping=None):
    """Lab results from every spreadsheet in the upload, as {"data": data.json shape, "warnings": [...]}.

    mapping: optional {field: header text or column index} for files whose headers aren't recognised, with
    fields date, time, test, value, unit, range, low, high, flag, panel and lab. Raises ValueError with a
    plain-language message when no file can be read."""
    warn = _Warnings()
    found, errors = [], []
    for rel, path in _candidates(files):
        try:
            rows, cols, delim = _rows(rel, path, mapping)
        except ValueError as e:
            errors.append(str(e))
            continue
        found.append((rel, rows, cols, delim))
    if not found:
        if errors:
            raise ValueError(errors[0] if len(errors) == 1 else " ".join(errors))
        raise ValueError("The upload has no spreadsheet (.csv, .tsv or .txt file) to read lab results from.")
    for e in errors:
        warn.add(f"Not read: {e}")

    results = []   # dicts per row, across files
    for rel, rows, cols, delim in found:
        decimal_comma = delim != ","
        if delim == ",":   # a comma-delimited file with decimal commas has them quoted: "4,5"
            vals = [c[cols["value"]] for _, c in rows if len(c) > cols["value"]]
            decimal_comma = any(re.fullmatch(r"\s*[<>≤≥=]*\s*-?\d+,\d{1,2}\s*", v) for v in vals) and \
                not any(re.fullmatch(r"\s*[<>≤≥=]*\s*-?\d+\.\d+\s*", v) for v in vals)
        get = lambda cells, f: cells[cols[f]].strip() if f in cols and cols[f] < len(cells) else ""   # noqa: E731
        order, guessed = _date_order(get(c, "date") for _, c in rows)
        for sep in sorted(guessed):
            how = "day/month" if order[sep] else "month/day"
            warn.add(f"{rel}: dates like 03{sep}04{sep}2025 could be read either way and nothing in the file "
                     f"settles it, so they were read as {how}. Check the dates if that's wrong.")
        if any(v is None for v in order.values()):
            warn.add(f"{rel}: the file mixes day-first and month-first dates, so dates where both parts are 12 or "
                     f"under were skipped.")
        bounds = "low" in cols or "high" in cols
        for line, cells in rows:
            if not any(c.strip() for c in cells):
                continue
            where = f"{rel} line {line}"
            r, why = read_row(lambda f: get(cells, f), decimal_comma, order, bounds)
            if why:
                warn.row(why[0], where + why[1])
                continue
            results.append({**r, "file": rel})
    results, dupes = dedupe(results)
    if dupes:
        warn.add(f"Left out {dupes} result{'s' if dupes != 1 else ''} that appeared twice (same test, date and value).")
    if not results:
        msgs = warn.list()
        raise ValueError("No lab results could be read from the spreadsheet." + (" " + " ".join(msgs) if msgs else ""))
    return {"data": build(results), "warnings": warn.list()}


def read_row(get, decimal_comma=False, order=None, bounds=True):
    """One result from one row: (result, None), or (None, (why, note)) when it can't be read.

    get(field) is the row's trimmed text for a field of FIELDS ("" when it has none). decimal_comma: read "4,5" as 4.5.
    order: numeric date order from date_order() over the rows read together. bounds: low and high are read
    (a spreadsheet only when it has those columns). why is a phrase after "Skipped N rows", like "with no date";
    note is "" or the unreadable text, as " ('31/31/2025')". Hand entry (routes_manual.py) reads rows with this too,
    so a typed result and the same result in a spreadsheet are one and the same."""
    test, raw_date, reported = get("test"), get("date"), get("value")
    if not test:
        return None, ("with no test name", "")
    if not raw_date:
        return None, ("with no date", "")
    t = _timestamp(raw_date, get("time"), order or {})
    if not t:
        return None, ("with a date that couldn't be read", f" ({raw_date!r})")
    if not reported:
        return None, ("with no value", "")
    unit = get("unit")
    x, cmp, rest = _value(reported, decimal_comma)
    flag_text = get("flag")
    if x is not None and rest:
        fm = _TRAILING_FLAG.match(rest)
        if fm:                       # "18.1 H", "18.1 g/dL H"
            flag_text, rest = flag_text or fm.group(2), fm.group(1)
        if rest and _UNIT_RE.fullmatch(rest):
            unit = unit or rest      # "5.2 mg/dL" in one cell
        elif rest:
            x = None                 # "1 of 3", "2+ casts": text
    range_text = get("range")   # kept as reported in the result row, even when unreadable
    lo, hi = _range(range_text, decimal_comma)
    if bounds:
        lo2 = _number(get("low"), decimal_comma)
        hi2 = _number(get("high"), decimal_comma)
        lo, hi = (lo2 if lo2 is not None else lo), (hi2 if hi2 is not None else hi)
        if not range_text:
            range_text = (f"{_fmt(lo)} - {_fmt(hi)}" if lo is not None and hi is not None else
                          f">={_fmt(lo)}" if lo is not None else f"<={_fmt(hi)}" if hi is not None else "")
    flag = _flag(flag_text, x, cmp, lo, hi)
    return {"t": t, "test": test, "x": x, "reported": reported, "unit": unit, "range": range_text,
            "lo": lo, "hi": hi, "flag": flag, "panel": get("panel"), "lab": get("lab"), "loinc": get("loinc")}, None


def date_order(values):
    """How numeric dates like 03/04/2025 read among these values: (order for read_row, separators only guessed)."""
    return _date_order(values)


def timestamp(date_s, time_s="", order=None):
    """A date (and optional time) as read_row reads them: an ISO timestamp, noon for a date alone; None when unreadable.
    Hand entry reads the dates of every kind of typed record with this, so they take the same formats."""
    return _timestamp(date_s, time_s, order or {})


def number(s):
    """A number as a spreadsheet cell reads ("1,234.5", "4.5"); None when it isn't one."""
    return _number(s, False)


def guessed_date(s, guessed):
    """True for a date like 03/04/2025 whose day and month could swap, among values that left its separator guessed."""
    p = _date_parts(s)
    return bool(p and p[0] == "dm?" and p[1][3] in guessed and p[1][0] != p[1][1] and max(p[1][:2]) <= 12)


def _identity(r):
    """The fields that identify a result, normalised so a re-exported or reordered file gives the same ones."""
    return r["t"], " ".join(r["test"].split()).lower(), r["reported"].strip(), r["unit"].strip()


def _key(r):
    """Stable source key of a result's labitem: no file name, row number or column order in it."""
    return "csv:h:" + hashlib.sha256("\x1f".join(_identity(r)).encode("utf-8")).hexdigest()[:16]


def dedupe(results):
    """The results with repeats (same identity) left out, and how many were."""
    seen, keep = set(), []
    for r in results:
        k = _identity(r)
        if k in seen:
            continue
        seen.add(k)
        keep.append(r)
    return keep, len(results) - len(keep)


def _base_name(r):
    """The series a test belongs to: its canonical name when lab-aliases.json knows it (by LOINC code, then by name
    or alias), else the name as reported. Names nobody knows are mapped later (#23)."""
    return canonical_lab(r["test"], r["loinc"]) or r["test"]


def _series_names(results):
    """Series name per (test, unit): the base name, with the unit added when one series comes in two units. Units are
    counted per series, so two spellings of one test ("HGB", "Hemoglobin") never mix units."""
    base = {r["test"]: _base_name(r) for r in results}
    units = collections.defaultdict(collections.Counter)
    for r in results:
        if r["x"] is not None:
            units[base[r["test"]]][_unit_key(r["unit"])] += 1
    names = {}
    for test, b in base.items():
        c = units.get(b)
        if not c:
            continue
        main = c.most_common(1)[0][0]
        for u in c:
            names[(test, u)] = b if u == main else f"{b} ({u or 'no unit'})"
    return names


def _unit_key(u):
    return (u or "").strip().lower().replace(" ", "").replace("µ", "u").replace("μ", "u")


def build(results, source=SOURCE, sub="Imported spreadsheet"):
    """The data.json shape for read_row's results (deduplicated). source tags points and lab days; sub is a result's
    subtitle when it has no panel or lab."""
    events, details = [], {}

    def add(t, kind, title, sub, detail, **extra):
        e = {"id": len(events) + 1, "t": t, "k": kind, "title": title, "sub": sub}
        e.update({k: v for k, v in extra.items() if v not in (None, "", [], 0)})
        details[e["id"]] = detail
        events.append(e)
        return e["id"]

    # one hidden labitem per test and time, so each has a stable key; one visible lab event per day
    days = collections.OrderedDict()
    for r in sorted(results, key=lambda r: (r["t"], (r["panel"] or r["test"]).lower(), r["test"].lower())):
        days.setdefault(r["t"][:10], []).append(r)
    for day, rs in days.items():
        ids = []
        for r in rs:
            order = r["panel"] or r["test"]
            det = {"type": "result", "order": order, "name": r["test"], "collected": _display(r["t"]),
                   "lab": r["lab"] or None, "dx": [], "rows": [[r["test"], r["reported"], r["unit"], r["range"], r["flag"]]],
                   "report": [], "comments": [], "src": source}
            line = " · ".join(x for x in [r["panel"] if r["panel"] != r["test"] else "", r["lab"]] if x) or sub
            ids.append(add(r["t"], "labitem", r["test"], line, det, key=_key(r), abn=int(bool(r["flag"])), hidden=1))
        orders = sorted({r["panel"] or r["test"] for r in rs})
        add(rs[0]["t"], "lab", lab_day_title(len(rs)), lab_day_sub(orders),
            {"type": "labday", "ids": ids}, key=f"csv:labday:{day}", abn=sum(1 for r in rs if r["flag"]), src=source)

    names = _series_names(results)
    series, panels = {}, collections.defaultdict(collections.Counter)
    for r in results:
        if r["x"] is None:
            continue
        name = names[(r["test"], _unit_key(r["unit"]))]
        s = series.setdefault(name, {"unit": r["unit"], "pts": [], "txt": []})
        s["pts"].append([r["t"], r["x"], r["lo"], r["hi"], r["flag"], r["reported"], source])
        panels[name][r["panel"]] += 1
    for r in results:   # text results go with the test's main series, or one of their own
        if r["x"] is not None:
            continue
        name = names.get((r["test"], _unit_key(r["unit"])), _base_name(r))
        s = series.setdefault(name, {"unit": r["unit"], "pts": [], "txt": []})
        s["txt"].append([r["t"], r["reported"]])
        panels[name][r["panel"]] += 1
    loinc = collections.defaultdict(collections.Counter)
    for r in results:
        if r["loinc"]:
            loinc[names.get((r["test"], _unit_key(r["unit"])), _base_name(r))][r["loinc"]] += 1
    for name in series:   # a canonical series without a code in the file takes lab-aliases.json's
        if not loinc[name] and loinc_of(name):
            loinc[name][loinc_of(name)] += 1
    labs = [{"name": name, "key": f"loinc:{loinc[name].most_common(1)[0][0]}" if loinc[name] else
             f"csv:{' '.join(name.split()).lower()}|{s['unit'].strip()}",
             "unit": s["unit"], "panel": panels[name].most_common(1)[0][0] if panels[name] else "",
             "n": len(s["pts"]), "pts": sorted(s["pts"], key=lambda p: (p[0], p[1])), "txt": sorted(s["txt"])[-50:]}
            for name, s in series.items()]
    labs.sort(key=lambda s: (-s["n"], s["name"]))
    events.sort(key=lambda e: e["t"])
    return {"events": events, "details": details, "labs": labs, "costs": []}


def _display(t):
    return t[:10] if t[10:19] == "T12:00:00" and len(t) == 19 else t[:16].replace("T", " ")
