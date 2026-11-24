"""Helpers shared by build.py and the importers: dates, numbers, lab names and units, hospital stays.

Standard library only: build.py imports this module and must keep running on a bare Python 3.
Everything here is a plain function of its arguments, so an importer can use any piece of it on its own.
"""
import functools, html, json, os, re
from datetime import datetime

# ------------------------------------------------------------------ dates, numbers, text

def iso(s):
    """Normalise to an ISO string with offset (local -05/-06 kept when present); a bare date becomes noon."""
    if not s:
        return None
    s = s.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s + "T12:00:00"
    return s.replace("Z", "+00:00")


def ymd(s):
    """YYYY-MM-DD from the start of "2026-03-01...", "20260301..." and the like; None when there's no date."""
    m = re.match(r"(\d{4})-?(\d{2})-?(\d{2})", (s or "").strip())
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def num(s):
    """The number in a result value such as "4.2", "<0.5" or ">= 60"; None for anything else."""
    m = re.match(r"^\s*([<>]=?)?\s*(-?\d+(?:\.\d+)?)\s*$", str(s or ""))
    return float(m.group(2)) if m else None


_BOUND = re.compile(r"\s*(<=|>=|=<|=>|<|>|≤|≥)")
_CMP = {"=<": "<=", "≤": "<=", "=>": ">=", "≥": ">="}


def comparator(text):
    """A value given as a bound ("<15", "≤ 0.5", ">90"): its comparator, "<", "<=", ">" or ">=", else "" (#718)."""
    m = _BOUND.match(str(text or ""))
    return _CMP.get(m.group(1), m.group(1)) if m else ""


def range_flag(v, lo, hi, cmp="", edge=""):
    """The one rule for a flag worked out from a range (#752; server/ranges.py flag is this, kept here for build.py):
    "H" above the high, "L" below the low, whatever value a bound (cmp, comparator()) stands for, else "": "<15"
    against a low of 15 is low (every value under 15 is), ">90" against a high of 90 high; "<15" against a low of 10 is
    neither (it may be 12 or 9). edge (range_edge()) holds "<" where the high leaves its edge out, ">" where the low does
    (#780): 5 against "<5" is high, 60 against ">60" low, as ">=5" against "<5" is; against "<=5" and ">=60" neither.
    The importers' (a report's range and no flag), a range from settings (#44), the visit summary and share links all
    use it; www/js/ranges.js rangeFlag is the same."""
    if v is None:
        return ""
    edge = str(edge or "")
    if hi is not None and not cmp.startswith("<") and (v >= hi if cmp == ">" or "<" in edge else v > hi):
        return "H"
    if lo is not None and not cmp.startswith(">") and (v <= lo if cmp == "<" or ">" in edge else v < lo):
        return "L"
    return ""


def number_text(s, decimal_comma=None):
    """A number as printed: "4.5", "1,234.5", "1.234,5", "4,5", "1'234"; None when it isn't one number. decimal_comma
    says whether the file writes decimal commas, where that's known: True where it does (a spreadsheet with semicolons
    or tabs, or with "4,5" among its values, csv_labs: "1,250" is 1.25), False where it doesn't (a comma-delimited
    spreadsheet's, a row typed in: "1,250" is 1250), None where it isn't known (a C-CDA document, FHIR, CareSpace,
    MyChart, a PDF report): a comma that can't be a thousands separator is a decimal one ("3,5" is 3.5), one that can
    ("1,250") leaves the number unread, never a guess (#850). The CSV importer's values are read by it, and every range
    given as text (range_text)."""
    s = str(s or "").strip().replace("\u00a0", "").replace("\u202f", "").replace("'", "")
    if not s or not re.fullmatch(r"[-+]?[\d.,]*\d(?:[eE][-+]?\d+)?|[-+]?[\d.,]*[.,]\d+", s):
        return None
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        grouped = re.fullmatch(r"[-+]?\d{1,3}(,\d{3})+", s)   # "1,250": in thousands, or 1.25 with a decimal comma
        if grouped and decimal_comma is None:
            return None
        if grouped and not decimal_comma:
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


# a reference range as text (range_text): "3.5 - 5.1", "70–99", "10 to 40", "5,0 - 10,0"; a one-sided limit that keeps
# its edge in the range, "<=5", "=<5", "≤ 5", "< OR = 5", "less than or equal to 5", "up to 5", ">=60", "=>60", "≥ 60",
# "> OR = 60", "greater than or equal to 60"; one that leaves it out (#780), "<5", "less than 5", "under 5", "below 5",
# ">60", "greater than 60", "more than 60", "over 60", "above 60"; a unit or words may follow ("<130 mg/dL"). A number
# runs to its end, as number_text reads it: "1,000" and "3,5" are never 1 and 3
_RNUM = r"([-+]?(?:\d{1,3}(?:['\u00a0\u202f]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)*|[.,]\d+))(?![.,]?\d)"
_RSPAN = re.compile(rf"\s*{_RNUM}\s*(?:-|–|—|\.\.|to\b)\s*{_RNUM}", re.I)
_RBOUND = re.compile(r"\s*(?:(<\s*(?:or\s*)?=|=\s*<|≤|less\s+than\s+or\s+equal\s+to|up\s+to)"   # a high, its edge in it
                     r"|(>\s*(?:or\s*)?=|=\s*>|≥|(?:greater|more)\s+than\s+or\s+equal\s+to)"   # a low, its edge in it
                     r"|(<|less\s+than|under|below)|(>|greater\s+than|more\s+than|over|above))"   # a high, a low, left out
                     rf"\s*{_RNUM}", re.I)


def _range_at(text, decimal_comma):
    """(low, high, edge, end) of the range text starts with, end where it ends (0 for none); low and high None where
    it's printed as a range but its numbers can't be read ("5-3", "1,000-2,000" where decimal commas aren't known)."""
    t = str(text or "")
    m = _RSPAN.match(t)
    if m:
        lo, hi = number_text(m.group(1), decimal_comma), number_text(m.group(2), decimal_comma)
        return (lo, hi, "", m.end()) if lo is not None and hi is not None and lo <= hi else (None, None, "", m.end())
    m = _RBOUND.match(t)
    if not m:
        return None, None, "", 0
    x, high = number_text(m.group(5), decimal_comma), m.group(1) or m.group(3)
    if x is None:
        return None, None, "", m.end()
    return ((None, x) if high else (x, None)) + ("<" if m.group(3) else ">" if m.group(4) else "", m.end())


def range_text(text, decimal_comma=None, whole=False):
    """The one parser of a reference range given as text (#827, #836, #850): every importer's (CSV and Type them in, PDF,
    C-CDA, FHIR, CareSpace, MyChart, where a source gives no low and high of its own) and the FHIR export's. (low, high,
    edge): edge "<" where a one-sided high leaves its edge out ("<5": 5 is above it), ">" where a low does (">60"), ""
    else (#780); (None, None, "") for text it can't read ("See note", "Negative", "1,000-2,000" where decimal commas
    aren't known), never a guess. decimal_comma as number_text takes it; whole: the text is the range and nothing else
    (a PDF report's cell: "<5 see note" isn't read), else words may follow it ("<130 mg/dL")."""
    t = str(text or "")
    lo, hi, edge, end = _range_at(t, decimal_comma)
    return (None, None, "") if whole and t[end:].strip() else (lo, hi, edge)


def range_part(text, decimal_comma=None):
    """(the range text starts with, as printed; what follows it): ("65-99", "mg/dL") from "65-99 mg/dL", ("> OR = 60",
    "mL/min/1.73m2"); ("", text) where it starts with none. By range_text's reading, for a PDF layout's cell that holds a
    range and a unit (pdf_layouts.range_and_unit, is_range)."""
    t = str(text or "").strip()
    end = _range_at(t, decimal_comma)[3]
    return t[:end].strip(), t[end:].strip()


def range_edge(text, lo, hi, decimal_comma=None):
    """Whether a one-sided reference range, as printed (text), leaves its limit's edge out (#780): "<" for "<5" (5 is
    above it), ">" for ">60" (60 is below it), "" for one that includes it ("<=5", "≤ 5", "< OR = 5", "up to 5", ">=60")
    and for a range with a low and a high. lo and hi are the range's numbers as read (from the text, or from a source's
    own low and high beside it): the edge is said only where the text's number is the one limit there is. The text is
    read by range_text (#850). Kept with the range on each point (pts[7], docs/importers.md), the flag rule
    (range_flag) and its words honour it."""
    if (lo is None) == (hi is None):
        return ""
    tlo, thi, edge = range_text(text, decimal_comma)
    return edge if (edge == "<" and thi == hi) or (edge == ">" and tlo == lo) else ""


# a flag as reports print it (flag_word(): lower case, punctuation as spaces): one that says which way, as Quest, Labcorp,
# a spreadsheet's Flag column, C-CDA and FHIR interpretation codes (HL7's ObservationInterpretation) and CareSpace give
# it ("A>" and "A<" as the visit summary read them, #839); one that marks a result abnormal without saying which way
# (#817), a yes in an "Abnormal" or "Out of range" column among them (#835); one that says neither, a no in such a column
# too; and which of them say it's critical, for its HL7 code (flag_code). The first two are www/js/ranges.js FLAG_WAY and
# FLAG_NO_WAY too, for the page's reading of a stored flag (#839, server/ranges.py read_flag)
_FLAG_WAY = {**dict.fromkeys(("h", "hh", "hu", ">", "hi", "high", "a h", "a>", "above", "above high normal", "critical high",
                              "panic high", "significantly high", "off scale high"), "H"),
             **dict.fromkeys(("l", "ll", "lu", "<", "lo", "low", "a<", "below", "below low normal", "critical low", "panic low",
                              "significantly low", "off scale low"), "L")}
_FLAG_NO_WAY = {"a", "aa", "abn", "abnormal", "critical", "critical abnormal", "panic", "alert", "y", "yes", "true"}
_FLAG_NEITHER = {"", "n", "normal", "no", "false"}
_FLAG_CRITICAL = {"hh", "critical high", "panic high", "ll", "critical low", "panic low", "aa", "critical", "critical abnormal",
                  "panic"}


def flag_word(text):
    """A printed flag as _FLAG_WAY and _FLAG_NO_WAY spell it: "H*" as "h", "Critical-High" as "critical high", "A/H" as
    "a h"."""
    return " ".join(re.sub(r"[^0-9a-z<>]+", " ", str(text or "").lower()).split())


def report_flag(printed, v, lo, hi, cmp="", edge=""):
    """The one rule for a result's flag from its report (#817), every importer's (docs/importers.md): "H" or "L" where
    the flag it prints says which way ("H", "HH", "High", "Critical high", ">"); where it prints one that marks the
    result abnormal without a direction ("A", "AA", "Abnormal", "Critical", "Alert"), the way its range places it
    (range_flag: "A" on a value above it is "H"), else "A", a report's own flag first: a value it flags inside its
    printed range, or with none printed, is kept flagged (◆ on Labs, the heatmap, the summary and share links), never
    dropped; where it prints none, or one that says neither ("N", "Normal"), by the range alone. A yes ("Y", "Yes",
    "True") in a flag column says it's abnormal, a no ("No", "False") neither (#835). printed: the flag as
    printed, or several (a C-CDA's or a FHIR resource's interpretation codes and words; the first that says which way
    wins). v: the value as a number, or None (text); cmp and edge as for range_flag."""
    words = [flag_word(p) for p in (printed if isinstance(printed, (list, tuple)) else [printed])]
    way = next((_FLAG_WAY[w] for w in words if w in _FLAG_WAY), "")
    return way or range_flag(v, lo, hi, cmp, edge) or ("A" if any(w in _FLAG_NO_WAY for w in words) else "")


def flag_code(printed):
    """A printed flag as HL7's ObservationInterpretation code, by the same words as report_flag (#835: the FHIR export's,
    which the FHIR importer reads back to the same flag): "H" or "L" where it says which way, "HH" or "LL" where it says
    so critically ("Critical high", "Panic low"); "A" where it says no way, "AA" critically ("Critical", "Panic"); ""
    for none, one that says neither, or one the rule doesn't read."""
    w = flag_word(printed)
    code = _FLAG_WAY.get(w) or ("A" if w in _FLAG_NO_WAY else "")
    return code * 2 if code and w in _FLAG_CRITICAL else code


def flag_known(printed):
    """Whether report_flag reads a flag as printed: one that says which way, no way or neither (none printed too); a
    flag it doesn't ("Final", "see note") leaves the range alone to say it, which Type them in notes (#835)."""
    w = flag_word(printed)
    return w in _FLAG_WAY or w in _FLAG_NO_WAY or w in _FLAG_NEITHER


TAG_RE = re.compile(r"<[^>]+>")


def strip_html(h):
    return html.unescape(TAG_RE.sub(" ", h or "")).replace("\xa0", " ")


# ------------------------------------------------------------------ lab names

LAB_ALIASES_FILE = os.environ.get("SANGUITY_LAB_ALIASES") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, "lab-aliases.json")


@functools.lru_cache(maxsize=None)
def lab_defs():
    """lab-aliases.json's "labs": {canonical name: {"loinc": ..., "aliases": [...]}}."""
    with open(LAB_ALIASES_FILE, encoding="utf-8") as f:
        return json.load(f)["labs"]


@functools.lru_cache(maxsize=None)
def lab_alias():
    """{lower-case canonical name or alias: canonical name}."""
    return {a.lower(): c for c, d in lab_defs().items() for a in [c] + d.get("aliases", [])}


@functools.lru_cache(maxsize=None)
def ext_alias():
    """{"... (External)" alias: canonical name}: the spellings Epic gives results copied in from elsewhere."""
    return {a: c for c, d in lab_defs().items() for a in d.get("aliases", []) if a.endswith(" (External)")}


@functools.lru_cache(maxsize=None)
def _by_loinc():
    return {d["loinc"]: c for c, d in lab_defs().items() if d.get("loinc")}


def canonical_lab(name=None, loinc=None):
    """The canonical series name for a lab, by LOINC code first, then by name or alias (case-insensitive).
    None when lab-aliases.json doesn't know it: keep the source's own name then."""
    return (loinc and _by_loinc().get(str(loinc).strip())) or (name and lab_alias().get(name.strip().lower())) or None


def loinc_of(canon):
    return lab_defs().get(canon, {}).get("loinc")


# ------------------------------------------------------------------ units

def unit_norm(u):
    """A unit reduced to a comparable form: "x10E3/uL" and "10*3/uL" both become "k/ul"."""
    u = (u or "").strip().lower().replace(" ", "").replace("µ", "u")
    for a, c in (("gm/dl", "g/dl"), ("x10e3/ul", "k/ul"), ("10*3/ul", "k/ul"), ("x10e6/ul", "m/ul"), ("10*6/ul", "m/ul"),
                 ("iu/l", "u/l"), ("ml/min/1.73m2", "ml/min"), ("/100wbc", "")):
        u = u.replace(a, c)
    return u


MONOVALENT = {"Sodium, Blood", "Potassium, Blood", "Chloride, Blood", "Anion Gap", "CO2"}   # mEq/L == mmol/L


def unit_for(canon, u):
    """unit_norm plus spellings that mean the same for this series (cells per cmm, mEq/L for monovalent ions)."""
    u = unit_norm(u)
    for a, c in (("tho/cmm", "k/ul"), ("thou/cmm", "k/ul"), ("k/cmm", "k/ul"), ("mil/cmm", "m/ul"), ("m/cmm", "m/ul"),
                 ("seconds", "sec"), ("secs", "sec")):
        u = u.replace(a, c)
    if canon in MONOVALENT:
        u = u.replace("meq/l", "mmol/l")
    if canon == "MCHC":
        u = u.replace("%", "g/dl")
    return u


def unit_key(u):
    """A looser comparable form than unit_norm, for deciding whether two sources' series can share a chart."""
    u = (u or "").strip().lower().replace(" ", "").replace("µ", "u").replace("μ", "u")
    for a, b in (("x10e3/ul", "k/ul"), ("10*3/ul", "k/ul"), ("thou/ul", "k/ul"), ("x10e6/ul", "m/ul"), ("10*6/ul", "m/ul"),
                 ("mil/ul", "m/ul"), ("iu/l", "u/l"), ("ml/min/1.73m2", "ml/min"), ("ml/min/1.73m^2", "ml/min"), ("ratio", "")):
        u = u.replace(a, b)
    return u


def resolve_name(name, unit, org, labs):
    """Map a component name onto an existing series in `labs` ({name: {"unit": ...}}) when the units agree; a known
    lab measured in other units becomes "<canonical> (<org>)" so the two don't share an axis."""
    lower = {k.lower(): k for k in labs}
    canon = lab_alias().get(name.lower()) or lower.get(name.lower())
    if not canon:
        return name
    if canon in labs and labs[canon]["unit"] and unit and unit_for(canon, labs[canon]["unit"]) != unit_for(canon, unit):
        return f"{canon} ({org})"
    return canon


# ------------------------------------------------------------------ lab days
# The one visible "lab" event per day that links the day's results (#458): its title counts the values, as the
# drawer's Results fact does, and its sub names the day's orders, cut at a whole name.

LAB_DAY_SUB = 160


def lab_day_title(n, org=""):
    """'12 lab results', or '1 lab result · Northfield Lab': n is the day's values (result rows), not its reports."""
    return f"{n} lab result{'s' if n != 1 else ''}" + (f" · {org}" if org else "")


def lab_day_sub(orders, limit=LAB_DAY_SUB):
    """The orders joined with ', ', the most that fit in limit characters as whole names, then '…' for the rest; a
    first name longer than that is cut at a word, and one that fits but leaves no room for ', …' is followed by '…'."""
    out, size = [], 0
    names = [o for o in orders if o]
    for i, o in enumerate(names):
        more = i < len(names) - 1
        if size + len(o) + (len(", …") if more else 0) > limit:
            if not out:
                cut = o[:limit - 1] if len(o) < limit or o[limit - 1] == " " else o[:limit - 1].rsplit(" ", 1)[0]   # (#464: o[limit - 1] past its end)
                out.append((cut.rstrip(" ,") or o[:limit - 1]) + "…")
            else:
                out.append("…")
            break
        out.append(o)
        size += len(o) + 2
    return ", ".join(out)


# ------------------------------------------------------------------ hospital stays
# Each returns event fields for a "stay" event: {"t", "title", "sub", "detail", "end"} (add "id" and "k": "stay").

def _stay_title(org):
    return "Hospital stay" + (f" · {org}" if org else "")


def stay(admit, discharge, where, org, how, exact=None):
    """A stay with real admit and discharge dates. `exact` = (start, end) times when known; otherwise the stay runs
    from the start of the admit day to the end of the discharge day. `how` says where the dates came from."""
    a = exact[0] if exact else admit[:10] + "T00:00:00"
    d = exact[1] if exact else discharge[:10] + "T23:59:00"
    nights = (datetime.fromisoformat(discharge[:10]) - datetime.fromisoformat(admit[:10])).days
    return {"t": a, "title": _stay_title(org),
            "sub": f"{where} · {nights} night{'s' if nights != 1 else ''}" if nights else f"{where} · same day",
            "detail": {"type": "stay", "admit": admit, "discharge": discharge, "where": where, "how": how}, "end": d}


def inferred_stays(admissions, inpatient_ts, org=""):
    """Hospital stays guessed from inpatient lab results, for sources with no admit/discharge data.

    admissions: {"Mar 02, 2026": [result times]} for results that name their admission date; inpatient_ts: times of
    inpatient results that don't, grouped into one stay wherever results are at most two days apart."""
    out = []
    for adm, ts in admissions.items():
        try:
            start = datetime.strptime(adm, "%b %d, %Y").strftime("%Y-%m-%dT00:00:00")
        except ValueError:
            continue
        out.append({"t": start, "title": _stay_title(org), "sub": f"Admitted {adm} · last inpatient result {max(ts)[:10]}",
                    "detail": {"type": "stay", "admit": adm, "lastResult": max(ts), "results": len(ts)},
                    "end": max(ts)[:10] + "T23:59:00"})
    clusters = []
    for t in sorted(inpatient_ts):
        if clusters and (datetime.fromisoformat(t[:19]) - datetime.fromisoformat(clusters[-1][-1][:19])).days <= 2:
            clusters[-1].append(t)
        else:
            clusters.append([t])
    for c in clusters:
        out.append({"t": c[0][:10] + "T00:00:00", "title": _stay_title(org), "sub": f"Inpatient results {c[0][:10]} – {c[-1][:10]}",
                    "detail": {"type": "stay", "admit": c[0][:10], "lastResult": c[-1], "results": len(c)},
                    "end": c[-1][:10] + "T23:59:00"})
    return out


# ---- immunizations and allergies (#179): one shape, whichever importer reads them (or hand entry types them)
IMMUNIZATION_FIELDS = ("code", "system", "dose", "amount", "route", "site", "lot", "manufacturer", "performer", "where",
                       "status", "reason", "note")


def _line(v):
    return " ".join(str(v).split()) if isinstance(v, (str, int, float)) and not isinstance(v, bool) else ""


def immunization(vaccine, src, **fields):
    """An immunization as (title, sub, detail) for an "immunization" event (docs/importers.md): the detail is
    {"type": "immunization", "vaccine", "src"} and those of IMMUNIZATION_FIELDS the record gives, as text: "dose" is
    the dose number as recorded ("2", or "2 of 3" with the series' doses), "amount" the dose given ("0.5 mL"),
    "performer" who gave it, "where" the clinic or pharmacy. The sub line is the dose, who and where."""
    vaccine = _line(vaccine) or "Vaccine"
    det = {"type": "immunization", "vaccine": vaccine}
    det.update({k: _line(fields[k]) for k in IMMUNIZATION_FIELDS if _line(fields.get(k))})
    det["src"] = _line(src)
    sub = " · ".join(x for x in [f"Dose {det['dose']}" if det.get("dose") else "", det.get("performer"), det.get("where")] if x)
    return vaccine, sub, det


def dose_text(number, of=None):
    """The dose number as recorded, with the series' number of doses when given: "2", "2 of 3"."""
    n, m = _line(number), _line(of)
    return f"{n} of {m}" if n and m else n


def allergy(substance, reactions=(), severity="", criticality="", status="", date=None, src="", code=None, system=None):
    """An entry of `problems` for an allergy or intolerance, as the C-CDA and FHIR importers both write it: name
    "Allergy: <substance> (<reactions>)", type "Allergy", and the parts as recorded: substance, reactions (a list),
    severity of the reaction ("Mild", "Moderate", "Severe"), criticality (the risk the record gives: "High", "Low"),
    the substance's code and its system."""
    substance = _line(substance) or "Allergy"
    reactions = list(dict.fromkeys(r for r in (_line(x) for x in reactions or ()) if r))
    p = {"name": f"Allergy: {substance}" + (f" ({', '.join(reactions)})" if reactions else ""), "type": "Allergy",
         "date": date or None, "status": _line(status), "src": _line(src), "substance": substance}
    if reactions:
        p["reactions"] = reactions
    for k, v in (("severity", severity), ("criticality", criticality)):
        v = _line(v).replace("-", " ")
        if v:
            p[k] = v[:1].upper() + v[1:]
    if _line(code):
        p["code"], p["system"] = _line(code), _line(system) or None
    return p
