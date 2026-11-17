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
