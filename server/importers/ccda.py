"""C-CDA importer: the HL7 "download my record" XML most US patient portals offer.

Reads the header encounter and the Results, Vital Signs, Medications, Problems, Allergies, Immunizations and Encounters
sections of one or more C-CDA documents (CCD summaries or per-visit records) into the data.json shape build.py writes.
Allergies and immunizations take the shape common.allergy and common.immunization give them, which the FHIR importer
and hand entry share (#179).

Lab series take their canonical names from lab-aliases.json (by LOINC code, then name or alias), so the same test
from different sources lines up; names it doesn't know stay as reported, for the mapping step (#23).
Vitals use the series names build.py gives them, and body weight goes to clinicWeights as it does there.

Standard library only. Documents that declare a DOCTYPE or ENTITY are refused before parsing, so entity expansion
(billion laughs) and external entities can't happen.
"""
import collections
import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import date

from . import common
from .common import canonical_lab

NAME = "ccda"
LABEL = "C-CDA health summary (XML)"

H = "{urn:hl7-org:v3}"
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"
HEAD_BYTES = 8192            # detect() reads at most this much of each file
MAX_BYTES = 200 * 1024 ** 2  # one C-CDA larger than this is not a health summary

# section templateIds (C-CDA R1.1 / R2.1 roots; the ".1" "entries required" and the plain variant) and LOINC codes
SECTIONS = {
    "results": ({"2.16.840.1.113883.10.20.22.2.3.1", "2.16.840.1.113883.10.20.22.2.3"}, {"30954-2"}),
    "vitals": ({"2.16.840.1.113883.10.20.22.2.4.1", "2.16.840.1.113883.10.20.22.2.4"}, {"8716-3"}),
    "meds": ({"2.16.840.1.113883.10.20.22.2.1.1", "2.16.840.1.113883.10.20.22.2.1"}, {"10160-0"}),
    "problems": ({"2.16.840.1.113883.10.20.22.2.5.1", "2.16.840.1.113883.10.20.22.2.5"}, {"11450-4"}),
    "allergies": ({"2.16.840.1.113883.10.20.22.2.6.1", "2.16.840.1.113883.10.20.22.2.6"}, {"48765-2"}),
    "encounters": ({"2.16.840.1.113883.10.20.22.2.22.1", "2.16.840.1.113883.10.20.22.2.22"}, {"46240-8"}),
    "immunizations": ({"2.16.840.1.113883.10.20.22.2.2.1", "2.16.840.1.113883.10.20.22.2.2"}, {"11369-6"}),   # #179
}
CODE_SYSTEMS = {"2.16.840.1.113883.6.1": "LOINC", "2.16.840.1.113883.6.96": "SNOMED CT", "2.16.840.1.113883.6.90": "ICD-10-CM",
                "2.16.840.1.113883.6.103": "ICD-9-CM", "2.16.840.1.113883.6.88": "RxNorm", "2.16.840.1.113883.6.12": "CPT",
                "2.16.840.1.113883.12.292": "CVX", "2.16.840.1.113883.6.69": "NDC"}
CVX = "2.16.840.1.113883.12.292"
# allergy entry templates (#179): reaction, severity, criticality and status observations
REACTION, SEVERITY, CRITICALITY, ALLERGY_STATUS = ("2.16.840.1.113883.10.20.22.4.9", "2.16.840.1.113883.10.20.22.4.8",
                                                   "2.16.840.1.113883.10.20.22.4.145", "2.16.840.1.113883.10.20.22.4.28")

# vitals by LOINC: (series name as build.py names it, unit)
VITALS = {"8302-2": ("Height", "in"), "8306-3": ("Height", "in"), "8310-5": ("Temperature", "°F"),
          "8867-4": ("Heart rate (clinic)", "bpm"), "8480-6": ("Blood pressure, systolic", "mmHg"),
          "8462-4": ("Blood pressure, diastolic", "mmHg"), "59408-5": ("SpO2 (clinic)", "%"), "2708-6": ("SpO2 (clinic)", "%"),
          "9279-1": ("Respiratory rate (clinic)", "/min"), "39156-5": ("BMI", "kg/m²"),
          "9843-4": ("Head circumference", "cm"), "8287-5": ("Head circumference", "cm")}   # head circumference: #196
WEIGHT_LOINC = "29463-7"
WEIGHT_UNITS = {"kg": 2.20462, "lb": 1.0, "[lb_av]": 1.0, "lbs": 1.0, "g": 0.00220462, "[oz_av]": 0.0625}

# encounter types: HL7 ActCode codes and the display names portals use
VISIT_TITLES = {"AMB": "Outpatient visit", "IMP": "Hospital admission", "ACUTE": "Hospital admission", "NONAC": "Hospital admission",
                "EMER": "Emergency visit", "Ambulatory": "Outpatient visit", "Inpatient Encounter": "Hospital admission",
                "Inpatient": "Hospital admission", "Emergency": "Emergency visit"}
NOT_A_PERSON = re.compile(r"not in system|^him provider|lab rm\d*|^ach |unknown", re.I)
PROBLEM_STATUS = {"active": "Active", "completed": "Resolved", "aborted": "Inactive", "suspended": "Inactive"}


# ------------------------------------------------------------------ reading files safely

def _head(path, n=HEAD_BYTES):
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except OSError:
        return b""


def _text(b):
    """Bytes to text for sniffing: UTF-16 by its byte-order mark, otherwise UTF-8 (lossy)."""
    if b[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return b.decode("utf-16", "ignore")
    return b.decode("utf-8", "ignore").lstrip("﻿")


def _score(head):
    t = _text(head)
    if not re.search(r"<(\w+:)?ClinicalDocument\b", t):
        return 0.0
    if "2.16.840.1.113883.10.20.22.1." in t:     # a C-CDA document-level templateId (CCD, discharge summary...)
        return 0.95
    if "urn:hl7-org:v3" in t:                     # CDA, not (visibly) C-CDA: older HITSP C32 and the like
        return 0.6
    return 0.3


def detect(files):
    """How sure we are (0 to 1) that the upload holds a C-CDA document. Reads only the start of each file."""
    best = 0.0
    for rel, path in files.items():
        if rel.lower().endswith((".xml", ".cda", ".ccd", ".ccda", ".txt")) or "." not in rel.rsplit("/", 1)[-1]:
            best = max(best, _score(_head(path)))
    return best


DECL = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.I)
DECL16 = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.I)


def _load(rel, path):
    """Parse one file, refusing anything that declares a DOCTYPE or entities. Raises ValueError in plain language."""
    with open(path, "rb") as f:
        raw = f.read(MAX_BYTES + 1)
    return load_bytes(rel, raw)


def load_bytes(rel, raw):
    """_load for a document already read into memory (a member of a zip, say): the same refusals, in the same words.
    `rel` names it in messages; pass at most MAX_BYTES + 1 bytes."""
    if len(raw) > MAX_BYTES:
        raise ValueError(f"{rel} is larger than {MAX_BYTES // 1024 ** 2} MB, too large for a health summary.")
    if DECL.search(raw) or (raw[:2] in (b"\xff\xfe", b"\xfe\xff") and DECL16.search(raw.decode("utf-16", "ignore"))):
        raise ValueError(f"{rel} contains a DOCTYPE or ENTITY declaration. Health summaries never need one, "
                         "and it can be used to attack the server, so the file was not read.")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise ValueError(f"{rel} is not well-formed XML ({e}). The file may be cut short or damaged; "
                         "try downloading it from the portal again.") from None
    if root.tag != H + "ClinicalDocument":
        raise ValueError(f"{rel} is XML but not an HL7 C-CDA ClinicalDocument.")
    return root


# ------------------------------------------------------------------ small helpers

def _f(el, path):
    return el.find(path.replace("h:", H)) if el is not None else None


def _all(el, path):
    return el.findall(path.replace("h:", H)) if el is not None else []


def _txt(el):
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def cda_time(v):
    """HL7 TS (YYYYMMDDHHMMSS±ZZZZ, any precision past the year) to an ISO string, as build.py writes them."""
    m = re.match(r"(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?(?:\.\d+)?([+-]\d{4})?$", (v or "").strip())
    if not m:
        return None
    y, mo, d, hh, mi, ss, tz = m.groups()
    s = f"{y}-{mo or '01'}-{d or '01'}T{hh or '12'}:{mi or '00'}:{ss or '00'}"
    return s + (f"{tz[:3]}:{tz[3:]}" if tz and hh else "")


def _when(el, which="low"):
    """Time of an element's effectiveTime: its value, or its low (or high) bound."""
    for et in _all(el, "h:effectiveTime"):
        if et.get(XSI_TYPE, "").endswith(("PIVL_TS", "EIVL_TS")):   # a dosing frequency, not a date
            continue
        if et.get("value"):
            return cda_time(et.get("value"))
        b = _f(et, "h:" + which)
        if b is not None and b.get("value"):
            return cda_time(b.get("value"))
    return None


def _num(s):
    m = re.match(r"^\s*([<>]=?)?\s*(-?\d+(?:\.\d+)?)\s*$", str(s if s is not None else ""))
    return float(m.group(2)) if m else None


def _idkey(el):
    """Source key from an entry's first usable <id>: "ccda:<root>^<extension>" (or "ccda:<root>"); None without one."""
    for i in _all(el, "h:id"):
        root, ext = (i.get("root") or "").strip(), (i.get("extension") or "").strip()
        if root and not i.get("nullFlavor"):
            return f"ccda:{root}^{ext}" if ext else f"ccda:{root}"
    return None


def _hkey(*fields):
    """Source key for an entry without an id: a hash of its normalised identifying fields (never of input order)."""
    norm = "\x1f".join(" ".join(str(f if f is not None else "").split()).lower() for f in fields)
    return "ccda:h:" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def _clean(x):
    return int(x) if isinstance(x, float) and x.is_integer() and abs(x) < 1e15 else x


class Doc:
    """One parsed C-CDA: its narrative ID index (for <reference value="#id"/>) and where it came from."""

    def __init__(self, rel, root):
        self.rel, self.root = rel, root
        self.ids = {el.get("ID"): el for el in root.iter() if el.get("ID")}
        org = _f(root, "h:custodian/h:assignedCustodian/h:representedCustodianOrganization/h:name")
        if org is None:
            org = _f(root, "h:author/h:assignedAuthor/h:representedOrganization/h:name")
        self.org = _txt(org)
        self.src = self.org or "C-CDA"

    def ref(self, el):
        """Text of an originalText/text element, following a reference into the narrative when there is one."""
        if el is None:
            return ""
        r = _f(el, "h:reference")
        if r is not None and (r.get("value") or "").startswith("#"):
            target = self.ids.get(r.get("value")[1:])
            if target is not None:
                return _txt(target)
        return _txt(el)

    def name(self, code):
        """Display name of a coded element: displayName, original text, or a translation's displayName."""
        if code is None:
            return ""
        if code.get("displayName"):
            return code.get("displayName").strip()
        ot = self.ref(_f(code, "h:originalText"))
        if ot:
            return ot
        for tr in _all(code, "h:translation"):
            if tr.get("displayName"):
                return tr.get("displayName").strip()
        return ""

    def sections(self, kind):
        roots, codes = SECTIONS[kind]
        for sec in self.root.iter(H + "section"):
            if any(t.get("root") in roots for t in _all(sec, "h:templateId")) or \
                    (_f(sec, "h:code") is not None and _f(sec, "h:code").get("code") in codes):
                yield sec


def _person(el):
    """A person's name from assignedPerson/name, or the name element itself."""
    n = _f(el, ".//h:assignedPerson/h:name")
    if n is None:
        n = _f(el, ".//h:name")
    if n is None:
        return ""
    parts = [_txt(_f(n, "h:prefix")), *[_txt(g) for g in _all(n, "h:given")], _txt(_f(n, "h:family")), _txt(_f(n, "h:suffix"))]
    return " ".join(p for p in parts if p) or _txt(n)


# ------------------------------------------------------------------ the importer

class _Out:
    def __init__(self):
        self.events, self.details, self.eid = [], {}, 0
        self.labs, self.panel_votes = {}, collections.defaultdict(collections.Counter)
        self.problems, self.weights, self.warnings = [], {}, []
        self.seen_visits, self.seen_meds, self.seen_problems, self.seen_results = set(), set(), set(), set()
        self.seen_immunizations = set()
        self.keys = set()

    def unique(self, key, *fields):
        """`key`, or (when another record already holds it) a content hash; a numbered suffix only for identical content."""
        if key in self.keys:
            key = _hkey(*fields)
        base, n = key, 1
        while key in self.keys:
            n += 1
            key = f"{base}:{n}"
        self.keys.add(key)
        return key

    def add(self, t, kind, title, sub="", detail=None, end=None, key=None, **extra):   # build.py's add(), plus a source key
        if not t:
            return None
        self.eid += 1
        e = {"id": self.eid, "t": t, "k": kind, "title": title, "sub": sub}
        if key:
            e["key"] = self.unique(key, t, kind, title, sub)
        if end:
            e["end"] = end
        e.update({k: v for k, v in extra.items() if v not in (None, "", [])})
        if detail:
            self.details[self.eid] = detail
        self.events.append(e)
        return self.eid

    def series(self, name, unit, loinc=None, code=None):
        """The series for a test; `code` is (code system OID, code) when the code isn't LOINC. Named canonically
        when lab-aliases.json knows the test (by LOINC code, then name), so labs from other sources line up."""
        name = canonical_lab(name, loinc) or name
        s = self.labs.get(name)
        if s is not None and unit and s["unit"] and s["unit"].replace(" ", "").lower() != unit.replace(" ", "").lower():
            name = f"{name} ({unit})"        # same test reported in another unit: its own series, never mixed
            s = self.labs.get(name)
        if s is None:
            s = self.labs[name] = {"unit": unit or "", "pts": [], "txt": [], "loinc": loinc, "code": code}
        if loinc and not s["loinc"]:
            s["loinc"] = loinc
        if code and not s["code"]:
            s["code"] = code
        return name, s

    @staticmethod
    def series_key(s):
        """"loinc:<code>", else "ccda:<code system>|<code>" (made unique across series in parse())."""
        return f"loinc:{s['loinc']}" if s["loinc"] else f"ccda:{s['code'][0]}|{s['code'][1]}" if s["code"] else None


def _value(obs):
    """(number or None, as reported, unit) of an observation's value."""
    v = _f(obs, "h:value")
    if v is None:
        return None, "", ""
    typ = v.get(XSI_TYPE, "").split(":")[-1]
    if typ in ("PQ", "INT", "REAL") or (not typ and v.get("value") is not None):
        raw = v.get("value") or ""
        x = _num(raw)
        unit = v.get("unit") or ""
        return x, raw, "" if unit == "1" else unit
    if typ == "IVL_PQ":
        lo, hi = _f(v, "h:low"), _f(v, "h:high")
        raw = "".join(s for s in [(">=" + lo.get("value")) if lo is not None and lo.get("value") and (hi is None or not hi.get("value")) else "",
                                   ("<=" + hi.get("value")) if hi is not None and hi.get("value") and (lo is None or not lo.get("value")) else ""])
        unit = (lo if lo is not None else hi).get("unit", "") if (lo is not None or hi is not None) else ""
        return None, raw, unit
    if typ in ("CD", "CE", "CO", "CV"):
        return None, (v.get("displayName") or _txt(_f(v, "h:originalText")) or v.get("code") or ""), ""
    raw = _txt(v)           # ST, ED and anything else: the text as written ("<0.5", "Negative")
    return _num(raw), raw, ""


def _range(doc, obs):
    """(low, high, as displayed) from an observation's referenceRange."""
    for rr in _all(obs, "h:referenceRange/h:observationRange"):
        v = _f(rr, "h:value")
        if v is not None:
            lo_el, hi_el = _f(v, "h:low"), _f(v, "h:high")
            lo_s = lo_el.get("value") if lo_el is not None and lo_el.get("nullFlavor") is None else None
            hi_s = hi_el.get("value") if hi_el is not None and hi_el.get("nullFlavor") is None else None
            lo, hi = _num(lo_s), _num(hi_s)
            if lo is not None or hi is not None:
                txt = f"{lo_s} - {hi_s}" if lo is not None and hi is not None else f">={lo_s}" if lo is not None else f"<={hi_s}"
                return lo, hi, doc.ref(_f(rr, "h:text")) or txt
        txt = doc.ref(_f(rr, "h:text"))
        if txt:
            m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*[-–]\s*(-?\d+(?:\.\d+)?)", txt)
            return (float(m.group(1)), float(m.group(2)), txt) if m else (None, None, txt)
    return None, None, ""


def _flag(obs, x, lo, hi):
    """H or L as the lab flagged it (or, unflagged, against the range); A for abnormal without a direction."""
    abnormal = False
    for ic in _all(obs, "h:interpretationCode"):
        c = (ic.get("code") or "").upper()
        if c in ("H", "HH", "HU", ">"):
            return "H"
        if c in ("L", "LL", "LU", "<"):
            return "L"
        abnormal = abnormal or c in ("A", "AA")
    if x is not None:
        if lo is not None and x < lo:
            return "L"
        if hi is not None and x > hi:
            return "H"
    return "A" if abnormal else ""


def _results(doc, out):
    days = collections.defaultdict(lambda: {"ids": [], "abn": 0, "orders": set(), "n": 0})
    for sec in doc.sections("results"):
        for org in sec.iter(H + "organizer"):
            panel = doc.name(_f(org, "h:code")) or "Lab result"
            obs_list = [o for o in _all(org, "h:component/h:observation")]
            t_org = _when(org)
            rows, abn, t_first = [], 0, None
            for obs in obs_list:
                code = _f(obs, "h:code")
                name = doc.name(code) or "?"
                loinc = code.get("code") if code is not None and code.get("codeSystem") == "2.16.840.1.113883.6.1" else None
                other = (code.get("codeSystem"), code.get("code")) if code is not None and not loinc \
                    and code.get("codeSystem") and code.get("code") else None
                t = _when(obs) or t_org
                x, raw, unit = _value(obs)
                lo, hi, rng = _range(doc, obs)
                if not unit and x is not None:      # a string value: the unit may only be written in the range
                    m = re.match(r"^\s*-?[\d.]+\s*[-–]\s*-?[\d.]+\s+(\S+)\s*$", rng)
                    unit = m.group(1) if m else ""
                flag = _flag(obs, x, lo, hi)
                if not t:
                    out.warnings.append(f"{doc.rel}: skipped result {name!r} with no date.")
                    continue
                key = (t[:16], name, raw)
                if key in out.seen_results:     # the same result in two documents of one upload
                    continue
                out.seen_results.add(key)
                t_first = min(t_first or t, t)
                abn += bool(flag)
                rows.append([name, raw, unit, rng, flag])
                sname, s = out.series(name, unit, loinc, other)
                out.panel_votes[sname][panel] += 1
                if x is not None:
                    s["pts"].append([t, _clean(x), _clean(lo), _clean(hi), flag if flag in ("H", "L") else "", raw, doc.src])
                elif raw:
                    s["txt"].append([t, raw])
            if not rows:
                continue
            status = _f(org, "h:statusCode")
            status = {"completed": "Final", "active": "Preliminary"}.get(status.get("code") if status is not None else "", "")
            performer = _person(_f(org, "h:performer")) if _f(org, "h:performer") is not None else ""
            det = {"type": "result", "order": panel, "name": panel, "collected": t_first[:16].replace("T", " "),
                   "resulted": None, "provider": performer or None, "status": status or None, "specimen": None,
                   "lab": doc.org or None, "dx": [], "rows": rows, "report": [], "comments": []}
            sub = " · ".join(x for x in [performer, status, doc.org] if x)
            day = days[t_first[:10]]
            # the organizer is the labitem here (one event per panel), so its id is the key
            pcode = _f(org, "h:code")
            key = _idkey(org) or _hkey(t_first, "labitem", (pcode.get("code") if pcode is not None else None) or panel,
                                       *("|".join(r[:3]) for r in rows))
            day["ids"].append(out.add(t_first, "labitem", panel, sub, det, key=key, abn=abn or None, hidden=1))
            day["abn"] += abn
            day["n"] += 1
            day["orders"].add(panel)
    for d, day in days.items():     # one visible "lab draw" event per day, linking to that day's results
        first = min((e for e in out.events if e["id"] in day["ids"]), key=lambda e: e["t"])
        out.add(first["t"], "lab", f"{day['n']} lab result{'s' if day['n'] != 1 else ''}" + (f" · {doc.org}" if doc.org else ""),
                ", ".join(sorted(day["orders"]))[:160], {"type": "labday", "ids": day["ids"]}, abn=day["abn"] or None, src=doc.src,
                key=_hkey(d, "lab", doc.src, *sorted(e["key"] for e in out.events if e["id"] in day["ids"])))


def _vitals(doc, out):
    for sec in doc.sections("vitals"):
        pairs = []      # (observation, its organizer's time): organizers usually carry the time, observations sometimes don't
        for entry in _all(sec, "h:entry"):
            for org in _all(entry, "h:organizer"):
                pairs += [(o, _when(org)) for o in _all(org, "h:component/h:observation")]
            pairs += [(o, None) for o in _all(entry, "h:observation")]
        for obs, t_org in pairs:
            code = _f(obs, "h:code")
            c = code.get("code") if code is not None else None
            t = _when(obs) or t_org
            x, raw, unit = _value(obs)
            if not t or x is None:
                continue
            u = unit.strip().lower()
            if c == WEIGHT_LOINC:
                f = WEIGHT_UNITS.get(u)
                if f and 1 < x * f < 600:   # from 1 lb: a baby's weight too (#196)
                    out.weights.setdefault(t[:16], [t, round(x * f, 1), doc.src])
                continue
            if c not in VITALS:
                continue
            name, unit_out = VITALS[c]
            if name == "Height" and u == "cm":
                x = round(x / 2.54, 1)
            elif name == "Height" and u == "m":
                x = round(x / 0.0254, 1)
            elif name == "Head circumference" and u in ("in", "[in_i]"):   # #196
                x = round(x * 2.54, 1)
            elif name == "Temperature" and u in ("cel", "c", "°c"):
                x = round(x * 9 / 5 + 32, 1)
            sname, s = out.series(name, unit_out, c)
            s["pts"].append([t, _clean(x), None, None, "", raw + (f" {unit}" if unit else ""), doc.src])
            out.panel_votes[sname]["Vitals"] += 1


def _meds(doc, out):
    skipped = 0
    for sec in doc.sections("meds"):
        for sa in sec.iter(H + "substanceAdministration"):
            if sa.get("negationInd") == "true":
                continue
            mat = _f(sa, "h:consumable/h:manufacturedProduct/h:manufacturedMaterial")
            code = _f(mat, "h:code")
            name = doc.name(code) or _txt(_f(mat, "h:name"))
            if not name or (code is not None and code.get("nullFlavor") and not name):
                continue
            t, end = _when(sa, "low"), _when(sa, "high")
            if not t:
                skipped += 1
                continue
            instr = doc.ref(_f(sa, "h:text"))
            if not instr:
                for er in _all(sa, "h:entryRelationship/h:act"):
                    if er.get("moodCode") == "INT" or any(x.get("root") == "2.16.840.1.113883.10.20.22.4.20" for x in _all(er, "h:templateId")):
                        instr = doc.ref(_f(er, "h:text"))
                        break
            if instr and instr.lower().startswith(name.lower()):   # some portals start the sig with the drug name
                instr = instr[len(name):].lstrip(" ,;:-") or instr
            st = _f(sa, "h:statusCode")
            status = {"active": "Active", "completed": "Completed", "aborted": "Discontinued", "suspended": "On hold",
                      "nullified": "Entered in error"}.get(st.get("code") if st is not None else "", "")
            if status == "Entered in error":
                continue
            key = (t[:10], name.lower())
            if key in out.seen_meds:
                continue
            out.seen_meds.add(key)
            det = {"type": "med", "instructions": instr or None, "status": status or None, "src": doc.src}
            if end and end > t:
                det["end"] = end
            if code is not None and code.get("codeSystem") == "2.16.840.1.113883.6.88" and code.get("code"):
                det["rxnorm"] = code.get("code")
            out.add(t, "med", name, " · ".join(x for x in [instr, status] if x), det,
                    key=_idkey(sa) or _hkey(t, "med", det.get("rxnorm") or name, instr))
    if skipped:
        out.warnings.append(f"{doc.rel}: skipped {skipped} medication{'s' if skipped != 1 else ''} with no start date.")


def _problem_rows(doc, kind):
    """(observation, concern act) pairs from the Problems or Allergies section."""
    for sec in doc.sections(kind):
        for act in _all(sec, "h:entry/h:act"):
            for obs in _all(act, "h:entryRelationship/h:observation"):
                yield obs, act


def _problems(doc, out):
    for obs, act in _problem_rows(doc, "problems"):
        if obs.get("negationInd") == "true":        # "no known problems"
            continue
        v = _f(obs, "h:value")
        if v is None or (v.get("nullFlavor") and not doc.name(v)):
            continue
        name = doc.name(v) or v.get("code") or ""
        if not name:
            continue
        typ = doc.name(_f(obs, "h:code")) or "Problem"
        status = None
        for so in _all(obs, "h:entryRelationship/h:observation"):   # Problem Status observation, where sent
            if _f(so, "h:code") is not None and _f(so, "h:code").get("code") in ("33999-4",):
                status = doc.name(_f(so, "h:value")) or None
        if status is None:
            st = _f(act, "h:statusCode")
            status = PROBLEM_STATUS.get(st.get("code") if st is not None else "", None)
            if status == "Active" and _when(obs, "high"):          # an active concern with a resolved date
                status = "Resolved"
        p = {"name": name, "type": typ.capitalize() if typ.islower() else typ,
             "date": (_when(obs) or _when(act) or "")[:10] or None, "status": status, "src": doc.src}
        if v.get("code"):
            p["code"] = v.get("code")
            p["system"] = CODE_SYSTEMS.get(v.get("codeSystem"), v.get("codeSystem"))
        key = (name.lower(), p["date"])
        if key not in out.seen_problems:
            out.seen_problems.add(key)
            p["key"] = out.unique(_idkey(act) or _hkey(p["date"], "problem", p.get("code") or name),
                                  p["date"], "problem", p.get("code") or name, status)
            out.problems.append(p)


def _has_template(el, root):
    return any(x.get("root") == root for x in _all(el, "h:templateId"))


def _allergies(doc, out):
    for obs, act in _problem_rows(doc, "allergies"):
        if obs.get("negationInd") == "true":        # "no known allergies"
            continue
        agent = _f(obs, "h:participant/h:participantRole/h:playingEntity/h:code")
        name = doc.name(agent) or _txt(_f(obs, "h:participant/h:participantRole/h:playingEntity/h:name"))
        if not name:
            continue
        inner = [r for r in obs.iter(H + "observation") if r is not obs and r.get("classCode", "OBS") == "OBS"]
        reactions = [doc.name(_f(r, "h:value")) for r in _all(obs, "h:entryRelationship/h:observation")
                     if r.get("classCode") == "OBS" and _f(r, "h:value") is not None and _has_template(r, REACTION)]

        def said(root):   # #179: the severity, criticality or status observation's value, as recorded
            return next((doc.name(_f(r, "h:value")) for r in inner if _has_template(r, root) and doc.name(_f(r, "h:value"))), "")
        st = _f(act, "h:statusCode")
        status = said(ALLERGY_STATUS) or PROBLEM_STATUS.get(st.get("code") if st is not None else "", "Active")
        agent_code = agent.get("code") if agent is not None and not agent.get("nullFlavor") else None
        p = common.allergy(name, reactions, severity=said(SEVERITY), criticality=said(CRITICALITY), status=status,
                           date=(_when(obs) or _when(act) or "")[:10] or None, src=doc.src, code=agent_code,
                           system=CODE_SYSTEMS.get(agent.get("codeSystem"), agent.get("codeSystem")) if agent_code else None)
        key = (p["name"].lower(), p["date"])
        if key not in out.seen_problems:
            out.seen_problems.add(key)
            p["key"] = out.unique(_idkey(act) or _hkey(p["date"], "allergy", agent_code or name),
                                  p["date"], "allergy", agent_code or name, p["name"])
            out.problems.append(p)


def _immunizations(doc, out):
    """#179: each immunization given (not refused, not planned) as an "immunization" event (common.immunization)."""
    for sec in doc.sections("immunizations"):
        for sa in _all(sec, "h:entry/h:substanceAdministration"):
            st = _f(sa, "h:statusCode")
            if sa.get("negationInd") == "true" or sa.get("moodCode", "EVN") != "EVN" or \
                    (st is not None and st.get("code") in ("nullified", "cancelled", "aborted")):
                continue
            t = _when(sa)
            prod = _f(sa, "h:consumable/h:manufacturedProduct")
            mat = _f(prod, "h:manufacturedMaterial")
            code = _f(mat, "h:code")
            name = doc.name(code) or _txt(_f(mat, "h:name"))
            if not t or not name:
                continue
            coded = [c for c in ([code] if code is not None else []) + _all(code, "h:translation") if c.get("code")]
            cvx = next((c for c in coded if c.get("codeSystem") == CVX), coded[0] if coded else None)
            if (t[:10], name.lower()) in out.seen_immunizations:   # the same history in two documents
                continue
            out.seen_immunizations.add((t[:10], name.lower()))
            perf = _f(sa, "h:performer")
            dq = _f(sa, "h:doseQuantity")
            reason = [doc.name(_f(o, "h:value")) for o in _all(sa, "h:entryRelationship[@typeCode='RSON']/h:observation")]
            rep = _f(sa, "h:repeatNumber")
            title, sub, det = common.immunization(
                name, doc.src, code=cvx.get("code") if cvx is not None else None,
                system=CODE_SYSTEMS.get(cvx.get("codeSystem"), cvx.get("codeSystem")) if cvx is not None else None,
                dose=rep.get("value") if rep is not None else None,
                amount=" ".join(x for x in [dq.get("value"), dq.get("unit") if dq.get("unit") != "1" else ""] if x) if dq is not None else "",
                route=doc.name(_f(sa, "h:routeCode")), site=doc.name(_f(sa, "h:approachSiteCode")),
                lot=_txt(_f(mat, "h:lotNumberText")), manufacturer=_txt(_f(prod, "h:manufacturerOrganization/h:name")),
                performer=_person(perf) if _f(perf, ".//h:assignedPerson") is not None else "",
                where=_txt(_f(perf, "h:assignedEntity/h:representedOrganization/h:name")),
                status=(st.get("code") or "").capitalize() if st is not None else "", reason="; ".join(x for x in reason if x))
            out.add(t, "immunization", title, sub, det,
                    key=_idkey(sa) or _hkey(t[:10], "immunization", (cvx.get("code") if cvx is not None else None) or name))


def _visit(doc, out, enc):
    """One visit (and, for an admission, a hospital stay) from an encompassingEncounter or an encounter entry."""
    t, end = _when(enc, "low"), _when(enc, "high")
    if not t:
        return
    code = _f(enc, "h:code")
    raw_type = doc.name(code) or ""
    vtype = VISIT_TITLES.get(code.get("code") if code is not None else "", None) or VISIT_TITLES.get(raw_type, raw_type) or "Visit"
    loc = _f(enc, ".//h:healthCareFacility//h:name")
    if loc is None:
        loc = _f(enc, "h:participant[@typeCode='LOC']/h:participantRole/h:playingEntity/h:name")
    where = _txt(loc)
    prov = _f(enc, ".//h:encounterParticipant")
    if prov is None:
        prov = _f(enc, "h:performer")
    who = _person(prov) if prov is not None else ""
    who = "" if NOT_A_PERSON.search(who) else who
    dx = "; ".join(dict.fromkeys(n for n in (doc.name(_f(o, "h:value")) for o in enc.iter(H + "observation")
                                               if _f(o, "h:value") is not None and _f(o, "h:value").get("codeSystem") in CODE_SYSTEMS) if n))
    key = (t[:16], vtype)
    if key in out.seen_visits:
        return
    out.seen_visits.add(key)
    end = end if end and end > t else None
    key = _idkey(enc) or _hkey(t, "visit", (code.get("code") if code is not None else None) or vtype, where)
    if vtype == "Hospital admission":
        nights = (date.fromisoformat(end[:10]) - date.fromisoformat(t[:10])).days if end else 0
        out.add(t, "stay", "Hospital stay" + (f" · {doc.org}" if doc.org else ""),
                f"{where} · {nights} night{'s' if nights != 1 else ''}" if nights else f"{where} · same day",
                {"type": "stay", "admit": t, "discharge": end or t, "where": where, "how": "C-CDA encounter"},
                end=end or t[:10] + "T23:59:00", key=key + ":stay")     # the stay comes from the same encounter
    det = {"type": "visit", "who": who, "where": where, "dx": dx, "src": doc.src, "procs": []}
    if raw_type and raw_type != vtype and raw_type not in VISIT_TITLES:
        det["note"] = f"Recorded as: {raw_type}"
    out.add(t, "visit", vtype, " · ".join(x for x in [who, where, doc.org] if x), det, end=end, key=key)


def _visits(doc, out):
    ee = _f(doc.root, "h:componentOf/h:encompassingEncounter")
    if ee is not None:
        _visit(doc, out, ee)
    for sec in doc.sections("encounters"):
        for enc in _all(sec, "h:entry/h:encounter"):
            _visit(doc, out, enc)


def parse(files):
    """Turn the C-CDA documents in an upload into data.json-shaped records: {"data": {...}, "warnings": [...]}."""
    picked = [(rel, path) for rel, path in sorted(files.items()) if _score(_head(path)) > 0]
    if not picked:
        raise ValueError("No C-CDA document was found. Upload the XML health summary from your patient portal "
                         "(often called \"Download my record\" or a CCD).")
    out, docs, errors = _Out(), [], []
    for rel, path in picked:
        try:
            docs.append(Doc(rel, _load(rel, path)))
        except ValueError as e:
            if len(picked) == 1:
                raise
            errors.append(str(e))
    if not docs:
        raise ValueError("None of the C-CDA documents could be read. " + " ".join(errors))
    out.warnings += errors
    for doc in docs:
        _visits(doc, out)
        _results(doc, out)
        _vitals(doc, out)
        _meds(doc, out)
        _problems(doc, out)
        _allergies(doc, out)
        _immunizations(doc, out)
    series = []
    for name, s in out.labs.items():
        pts = sorted(s["pts"], key=lambda p: p[0])
        votes = out.panel_votes[name]
        series.append({"name": name, "unit": s["unit"], "panel": votes.most_common(1)[0][0] if votes else "",
                       **({"loinc": s["loinc"]} if s["loinc"] else {}),
                       **({"key": _Out.series_key(s)} if _Out.series_key(s) else {}), "n": len(pts), "pts": pts,
                       "txt": sorted(s["txt"])[-50:]})
    # one code in several units: every one of those series adds "|<unit as reported>" (loinc:2345-7|mg/dL, ...|mmol/L)
    groups = collections.defaultdict(list)
    for s in series:
        if s.get("key"):
            groups[s["key"]].append(s)
    for group in groups.values():
        if len(group) > 1 and len({g["unit"] for g in group}) > 1:
            for g in group:
                g["key"] += f"|{g['unit']}"
    # still shared: two reported names, one code and one unit ("Hemoglobin" and "HGB", both g/dL); each adds "|<name>"
    shared = collections.Counter(s.get("key") for s in series)
    for s in series:
        if s.get("key") and shared[s["key"]] > 1:
            s["key"] += f"|{s['name']}"
    series.sort(key=lambda s: (-s["n"], s["name"]))
    out.events.sort(key=lambda e: e["t"])
    if not (out.events or series or out.problems or out.weights):
        out.warnings.append("The C-CDA document was read but held no visits, results, vitals, medications, problems or "
                            "immunizations.")
    data = {"events": out.events, "details": out.details, "labs": series, "costs": [], "problems": out.problems,
            "clinicWeights": sorted(out.weights.values())}
    return {"data": data, "warnings": out.warnings}
