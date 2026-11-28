"""FHIR R4 importer: a Bundle, an NDJSON bulk export, or a folder of single resources -> data.json records.

Apple Health clinical records (a folder of one resource per .json file, some still DSTU2) and CMS Blue Button
(Bundles) both arrive in these shapes, as do many patient-portal exports. Standard library only.

Contract (shared with the importer registry, #18):
    NAME, LABEL
    detect(files) -> float 0..1       files: {relative POSIX path in the upload: absolute path on disk}
    parse(files)  -> {"data": <data.json shape>, "warnings": [str]}; ValueError for input we can't read

What becomes what:
    Observation (laboratory)       one `labitem` event (hidden) per report or per day, under a `lab` event per day,
                                   and a point in `labs` [t, value, lo, hi, flag, as reported, source]; the labitem's
                                   "keys" name each row's Observation (#885: a day's key is every source's)
    Observation (vital-signs)      a point in `labs` (panel "Vitals"); body weight goes to `clinicWeights` in lb; a
                                   vital (any Observation) whose LOINC code common.VITALS knows is named and in the
                                   unit C-CDA's is, by that code first (#1102)
    DiagnosticReport               groups its lab Observations; without lab results it is a `study`/`imaging` event
                                   with its conclusion; presentedForm attachments become documents
    Condition                      `problems`
    AllergyIntolerance             `problems`, type "Allergy", with its substance, reactions, severity, criticality and
                                   status as recorded (#165; #179: common.allergy, the shape the C-CDA importer writes)
    Immunization                   an `immunization` event (#179, common.immunization): vaccine and its CVX code, dose
                                   number, amount, route, site, lot, manufacturer, who gave it and where (Health
                                   Cards' too, which have no ids: keyed by their day and vaccine code)
    Procedure                      a `procedure` event, with reason, site, outcome and notes where given (#165)
    MedicationRequest/Statement    `med` events (DSTU2 MedicationOrder too)
    MedicationDispense             an `rx` event (a pharmacy fill, #179), as the claims' fills: pharmacy, days' supply,
                                   quantity, prescriber and RxNorm code in a `med` detail
    Encounter                      `visit`; inpatient classes become a `stay` with `end`; planned ones go to `upcoming`
    Appointment                    booked (or pending) ones go to `upcoming` (#179); the others are visits or nothing
    ImagingStudy                   an `imaging` event, unless a DiagnosticReport here describes it (#179)
    DocumentReference              `document` events pointing at the attached file inside the upload
    ExplanationOfBenefit           a `claim` event (pharmacy: `rx`) with its claim detail and a `costs` row, as the
                                   claims importers make them (CARIN Blue Button; see _fhir_claims.py)
    Coverage                       read for the insurer's and plan's names on those claims
    FamilyMemberHistory            an entry of `familyHistory` (#1134, common.family_member): the relative (relationship,
                                   its v3 RoleCode in plain words, and name), each condition with its onset age, whether
                                   they have died and at what age, and the notes
    Communication                  a `message` event per conversation (#205): the messages that answer one another
                                   (inResponseTo), each with who sent it and when
    Observation, at home           (#205) one the Patient measured (the Patient its performer) of blood pressure, body
                                   temperature, blood oxygen or glucose: a home reading in `watch` (bp_readings ...),
                                   the device that took it as its source; the phone's daily measures, ECGs and
                                   irregular rhythm notifications in Sanguity's own FHIR export (its own codes,
                                   docs/fhir-export.md): `watch` metrics and `ecg`, a waveform's SampledData its
                                   samples. Its cycle records are left out, as loading a Sanguity export leaves them

Lab series take their canonical names from lab-aliases.json (by LOINC code, then name or alias), so the same test
from different sources lines up; names it doesn't know stay as reported, for the mapping step (#23).
Inline (base64) attachments are skipped with a warning for now: the upload has no place to write them yet.
"""
import hashlib, json, posixpath, re, urllib.parse
from collections import Counter, defaultdict
from datetime import datetime, timezone

from . import common
from .common import canonical_lab, comparator, range_edge, range_text, report_flag, unit_key

NAME = "fhir"
LABEL = "FHIR records (Apple Health, Blue Button, patient portals)"

LOINC = "http://loinc.org"
RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
CVX = "http://hl7.org/fhir/sid/cvx"
SYSTEMS = {CVX: "CVX", "urn:oid:2.16.840.1.113883.12.292": "CVX", "http://snomed.info/sct": "SNOMED CT", RXNORM: "RxNorm",
           "http://hl7.org/fhir/sid/ndc": "NDC", "http://www.whocc.no/atc": "ATC", "urn:oid:1.2.36.1.2001.1005.17": "AIR"}
# a Health Card's Immunization often carries only its CVX code: the common ones named in plain words (#179); any other
# is "Vaccine (CVX <code>)"
CVX_NAMES = {**dict.fromkeys(("207", "208", "210", "211", "212", "213"), "COVID-19 vaccine"),
             **dict.fromkeys(("88", "140", "141", "150", "158", "161", "171", "185", "186", "197"), "Influenza vaccine"),
             "115": "Tdap vaccine", "187": "Shingles vaccine (recombinant)", "33": "Pneumococcal vaccine (PPSV23)",
             "133": "Pneumococcal conjugate vaccine (PCV13)", "215": "Pneumococcal conjugate vaccine (PCV15)",
             "216": "Pneumococcal conjugate vaccine (PCV20)", "43": "Hepatitis B vaccine", "08": "Hepatitis B vaccine",
             "52": "Hepatitis A vaccine", "83": "Hepatitis A vaccine", "03": "MMR vaccine", "21": "Varicella vaccine",
             "165": "HPV vaccine"}
MODALITY = {"CT": "CT", "MR": "MRI", "DX": "X-ray", "CR": "X-ray", "US": "Ultrasound", "MG": "Mammogram", "NM": "Nuclear medicine",
            "PT": "PET", "XA": "Angiography", "RF": "Fluoroscopy", "BMD": "Bone density"}   # DICOM's codes, in plain words
CRITICALITY = {"CRITL": "Low", "CRITH": "High", "CRITU": "Unable to assess"}   # DSTU2's codes; R4's read as they are
SOURCE = "FHIR"   # source tag on lab points when the performing organisation is unknown

# resource types that are only looked up through references, never imported on their own
SUPPORTING = {"Patient", "Practitioner", "PractitionerRole", "Organization", "Location", "Medication", "Specimen",
              "Provenance", "RelatedPerson", "Coverage", "Device"}   # Device: what took a home reading (#205)
IMPORTED = {"Observation", "DiagnosticReport", "Condition", "MedicationRequest", "MedicationStatement", "MedicationOrder",
            "Encounter", "DocumentReference", "ExplanationOfBenefit", "AllergyIntolerance", "Immunization", "Procedure",
            "MedicationDispense", "Appointment", "ImagingStudy", "Communication", "FamilyMemberHistory"}
KNOWN = SUPPORTING | IMPORTED | {
    "Bundle", "AllergyIntolerance", "Immunization", "Procedure", "CarePlan", "CareTeam", "Goal", "Device", "Claim",
    "ExplanationOfBenefit", "Coverage", "ImagingStudy", "MedicationAdministration", "MedicationDispense", "ServiceRequest",
    "QuestionnaireResponse", "FamilyMemberHistory", "Composition", "List", "Appointment", "Communication", "Binary"}
# what a skipped record is, in plain words (#1103: "social history notes", not "Observation (social-history)"); a kind
# not here keeps its FHIR name
PLAIN_KINDS = {"CarePlan": "care plans", "CareTeam": "care teams", "Goal": "goals", "Claim": "claims",
               "MedicationAdministration": "medicines given", "ServiceRequest": "orders", "Composition": "summary documents",
               "QuestionnaireResponse": "questionnaire answers", "List": "lists",
               "Binary": "loose files"}
PLAIN_OBS = {"social-history": "social history notes (such as smoking)", "survey": "questionnaire answers",
             "exam": "exam findings", "imaging": "imaging measurements", "procedure": "procedure measurements",
             "therapy": "therapy notes", "activity": "activity notes", "uncategorised": "observations of no stated kind"}


def _plain_kind(k):
    """A skipped kind's key ("CarePlan", "Observation (social-history, survey)") in plain words."""
    if k.startswith("Observation (") and k.endswith(")"):
        return " and ".join(PLAIN_OBS.get(c, c + " observations") for c in k[13:-1].split(", "))
    return PLAIN_KINDS.get(k, k)

INPATIENT = {"IMP", "ACUTE", "NONAC", "inpatient", "inpatient encounter"}
DOC_KINDS = {"document", "letter", "imaging", "study", "procedure", "visit", "message"}   # a document's kind (#205)
MSG_KINDS = {"message", "appt_msg", "auto_msg"}   # a conversation's
EMERGENCY = {"EMER", "emergency"}
CLASS_TITLE = {"AMB": "Outpatient visit", "ambulatory": "Outpatient visit", "outpatient": "Outpatient visit",
               "EMER": "Emergency visit", "emergency": "Emergency visit", "VR": "Virtual visit", "HH": "Home visit",
               "OBSENC": "Observation stay", "PRENC": "Pre-admission visit", "SS": "Day surgery"}
VITAL_PANELS = {"85354-9", "55284-4", "35094-2"}   # blood pressure panels: each component becomes its own series
WEIGHT = {"29463-7", "3141-9"}
EXT = {"application/pdf": "pdf", "text/plain": "txt", "text/html": "html", "image/jpeg": "jpg", "image/png": "png",
       "image/tiff": "tif", "application/xml": "xml", "text/xml": "xml", "application/rtf": "rtf", "text/rtf": "rtf",
       "application/dicom": "dcm"}
RESOURCE_RE = re.compile(rb'"resourceType"\s*:\s*"([A-Za-z]+)"')
# Sanguity's own FHIR export (#205, server/fhir_export.py): its code systems for what no standard code says
SANGUITY_DOC = "https://github.com/sanguity/sanguity/blob/main/docs/fhir-export.md"
MEASURE, MEASURE_GROUP, KIND = SANGUITY_DOC + "#measures", SANGUITY_DOC + "#measure-groups", SANGUITY_DOC + "#record-kinds"
MDC = "urn:oid:2.16.840.1.113883.6.24"
# a home reading's LOINC code (an Observation the Patient measured) -> its list in `watch` (watch_store.READINGS)
HOME_READINGS = {"85354-9": "bp_readings", "55284-4": "bp_readings", "8310-5": "temp_readings", "2708-6": "spo2_readings",
                 "59408-5": "spo2_readings", "2339-0": "glucose_readings", "41653-7": "glucose_readings", "15074-8": "glucose_readings",
                 "14743-9": "glucose_readings"}
TYPED_IN = "Typed in"   # a home reading with no device: the person's own, as hand entry marks it (manual_items.py)
HEAD = 64 * 1024


# ------------------------------------------------------------------ detection

def _is_json_name(rel):
    return rel.lower().endswith((".json", ".ndjson", ".jsonl", ".fhir"))


def _head(path, n=HEAD):
    with open(path, "rb") as f:
        return f.read(n)


def _top_level_type(head):
    """resourceType of the top-level JSON object in the first bytes of a file, or None.

    Only a resourceType at nesting depth 1 counts, so a JSON file that merely mentions FHIR somewhere inside
    (a Sanguity data.json, a settings file) is not mistaken for a FHIR resource.
    """
    text = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    if not text.startswith(b"{"):
        return None
    depth, i, n, in_str = 0, 0, len(text), False
    while i < n:
        c = text[i:i + 1]
        if in_str:
            if c == b"\\":
                i += 1
            elif c == b'"':
                in_str = False
        elif c == b'"':
            if depth == 1:
                m = RESOURCE_RE.match(text, i)
                if m:
                    return m.group(1).decode()
            in_str = True
        elif c in b"{[":
            depth += 1
        elif c in b"}]":
            depth -= 1
        i += 1
    return None


def detect(files):
    """How sure we are that this upload is FHIR JSON: 0.0 (not at all) to 1.0."""
    cands = sorted(r for r in files if _is_json_name(r))
    if not cands:
        return 0.0
    hits = tried = 0
    best = 0.0
    for rel in cands[:25]:   # a sample is enough; Apple Health folders hold thousands of files
        try:
            head = _head(files[rel])
        except OSError:
            continue
        tried += 1
        if rel.lower().endswith((".ndjson", ".jsonl")):
            line = head.split(b"\n", 1)[0]
            rt = _top_level_type(line)
        else:
            rt = _top_level_type(head)
        if rt in KNOWN:
            hits += 1
            best = max(best, 0.95 if rt == "Bundle" or rel.lower().endswith((".ndjson", ".jsonl")) else 0.9)
        elif rt:
            hits += 1
            best = max(best, 0.6)   # FHIR-shaped, a type we don't list
    if not hits:
        return 0.0
    return round(best * (0.5 + 0.5 * hits / tried), 3)


# ------------------------------------------------------------------ loading

def _load(files, warnings):
    """Every FHIR resource in the upload as (resource, relative path of the file it came from)."""
    out, bad = [], []
    for rel in sorted(files):
        if not _is_json_name(rel):
            continue
        path = files[rel]
        if rel.lower().endswith((".ndjson", ".jsonl")):
            with open(path, encoding="utf-8-sig") as f:
                skipped = 0
                for ln, line in enumerate(f, 1):
                    if not line.strip():
                        continue
                    try:
                        r = json.loads(line, parse_float=Decimal)
                    except ValueError:
                        skipped += 1
                        continue
                    if isinstance(r, dict) and r.get("resourceType"):
                        out += [(x, rel) for x in _unbundle(r)]
                if skipped:
                    warnings.append(f"{rel}: skipped {skipped} line{'s' if skipped != 1 else ''} that weren't valid JSON.")
            continue
        try:
            with open(path, encoding="utf-8-sig") as f:
                r = json.load(f, parse_float=Decimal)
        except ValueError as e:
            bad.append((rel, e))
            continue
        except UnicodeDecodeError:
            bad.append((rel, None))
            continue
        if isinstance(r, dict) and r.get("resourceType"):
            out += [(x, rel) for x in _unbundle(r)]
    if bad and not out:
        rel, e = bad[0]
        where = f" (line {e.lineno}, column {e.colno})" if getattr(e, "lineno", None) else ""
        raise ValueError(f"{rel} couldn't be read: it isn't valid JSON{where}. The file may be cut off or damaged; "
                         "try downloading the export again.")
    for rel, _ in bad:
        warnings.append(f"{rel} isn't valid JSON and was skipped.")
    return out


def _unbundle(r, made=None):
    """A Bundle's resources, each with its entry's fullUrl ("_fullUrl") and the date the bundle says it was made
    ("_bundled": its timestamp, else its meta.lastUpdated, #955), nested bundles too."""
    if r.get("resourceType") != "Bundle":
        return [dict(r, _bundled=made) if made and "_bundled" not in r else r]
    made = r.get("timestamp") or (r.get("meta") or {}).get("lastUpdated") or made
    out = []
    for e in r.get("entry") or []:
        res = (e or {}).get("resource")
        if isinstance(res, dict) and res.get("resourceType"):
            if e.get("fullUrl"):
                res = dict(res, _fullUrl=e["fullUrl"])
            out += _unbundle(res, made if isinstance(made, str) else None)
    return out


def _ver(r):
    """(#955) Which version of its record a resource is: {"server": the server it came from (_server), "at": its
    meta.lastUpdated, "of": the date its bundle says it was made}, each where it's given. The import's preview updates
    a record here in place only from the same server, never from an older version (preview._changed)."""
    out = {"server": _server(r), "at": (r.get("meta") or {}).get("lastUpdated"), "of": r.get("_bundled")}
    return {k: v for k, v in out.items() if isinstance(v, str) and v}


# ------------------------------------------------------------------ small FHIR helpers

def iso(s):
    """FHIR date/dateTime/instant -> the ISO form data.json uses (date only -> noon, Z -> +00:00)."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    if re.fullmatch(r"\d{4}", s):
        return s + "-07-01T12:00:00"
    if re.fullmatch(r"\d{4}-\d{2}", s):
        return s + "-15T12:00:00"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s + "T12:00:00"
    if not re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", s):
        return None
    s = s.replace("Z", "+00:00")
    return re.sub(r"T(\d{2}:\d{2})(?=[+-]|$)", r"T\1:00", s)


def text_of(cc):
    """Readable text of a CodeableConcept (or a bare Coding / string)."""
    if not cc:
        return ""
    if isinstance(cc, str):
        return cc
    if isinstance(cc, list):
        return next((t for t in (text_of(c) for c in cc) if t), "")
    if cc.get("text"):
        return cc["text"]
    if cc.get("display"):
        return cc["display"]
    for c in cc.get("coding") or []:
        if c.get("display"):
            return c["display"]
    return next((c.get("code") for c in cc.get("coding") or [] if c.get("code")), "")


def _age(q):
    """(#1134) A FHIR Age (a Quantity) as text: "52" in years, "18 months"."""
    return common.age_text(num(q.get("value")), q.get("code") or q.get("unit") or "") if isinstance(q, dict) else ""


def _age_range(r):
    """(#1134) A Range of ages as text: "40–45", "from 40", "to 45"."""
    if not isinstance(r, dict):
        return ""
    lo, hi = _age(r.get("low")), _age(r.get("high"))
    return f"{lo}–{hi}" if lo and hi else f"from {lo}" if lo else f"to {hi}" if hi else ""


def _notes(notes):
    """A resource's Annotations' text, one paragraph each."""
    return "\n\n".join(n["text"].strip() for n in notes or [] if isinstance(n, dict) and isinstance(n.get("text"), str) and n["text"].strip())


def codes(cc, system=None):
    out = []
    for c in (cc or {}).get("coding") or [] if isinstance(cc, dict) else []:
        if c.get("code") and (system is None or (c.get("system") or "").rstrip("/") == system):
            out.append(c["code"])
    return out


def num(x):
    """A FHIR number: a JSON number as it is; one written as text (a string where a decimal belongs, a SampledData's
    data) by common.number_text, the one reader of a number (#870); None for anything else."""
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return x
    return common.number_text(x) if isinstance(x, str) else None


class Decimal(float):
    """A JSON number with a fraction, as a float that remembers how it was written (#205): FHIR's decimals keep their
    precision ("2.0" is not "2"), and a result's value as reported should too."""
    __slots__ = ("text",)

    def __new__(cls, text):
        v = super().__new__(cls, text)
        v.text = text
        return v


def fmt(v):
    if isinstance(v, Decimal) and "e" not in v.text.lower():   # as the JSON wrote it ("2.0"), but for an exponent
        return v.text
    return f"{v:g}" if isinstance(v, float) else str(v)


def cap(s):
    return (s[:1].upper() + s[1:]).replace("-", " ") if s else ""


def status_of(x):
    """clinicalStatus / verificationStatus: a CodeableConcept in R4, a plain string in DSTU2."""
    return cap(x if isinstance(x, str) else text_of(x))


class Index:
    """Resolve references: "Type/id", "urn:uuid:..." full URLs, and contained resources ("#id")."""

    def __init__(self, resources):
        self.by = {}
        for r, _ in resources:
            if r.get("id"):
                self.by[f"{r['resourceType']}/{r['id']}"] = r
            if r.get("_fullUrl"):
                self.by[r["_fullUrl"]] = r
                tail = re.search(r"([A-Za-z]+/[^/]+)$", r["_fullUrl"])
                if tail:
                    self.by.setdefault(tail.group(1), r)

    def get(self, ref, owner=None):
        if not isinstance(ref, dict):
            return None
        s = ref.get("reference") or ""
        if s.startswith("#") and owner:
            return next((c for c in owner.get("contained") or [] if c.get("id") == s[1:]), None)
        if s in self.by:
            return self.by[s]
        m = re.search(r"([A-Za-z]+/[^/]+?)(?:/_history/.*)?$", s)
        return self.by.get(m.group(1)) if m else None

    def name(self, ref, owner=None):
        """Display name of a referenced Practitioner, Organization, Location, Medication..."""
        if not isinstance(ref, dict):
            return ""
        r = self.get(ref, owner)
        if r:
            if r.get("resourceType") == "Medication":
                return text_of(r.get("code"))
            n = r.get("name")
            if isinstance(n, str):
                return n
            if isinstance(n, dict):
                n = [n]
            if isinstance(n, list) and n:
                h = n[0]
                if h.get("text"):
                    return h["text"]
                given = " ".join(h.get("given") or [])
                pre = " ".join(h.get("prefix") or [])
                fam = h.get("family") if isinstance(h.get("family"), str) else " ".join(h.get("family") or [])
                full = " ".join(x for x in (pre, given, fam) if x)
                if full:
                    return full
        return ref.get("display") or ""


# ------------------------------------------------------------------ parse

class Builder:
    def __init__(self):
        self.events, self.details, self.eid = [], {}, 0

    def add(self, t, k, title, sub="", detail=None, end=None, **extra):
        if not t:
            return None
        self.eid += 1
        e = {"id": self.eid, "t": t, "k": k, "title": title or "", "sub": sub or ""}
        if end:
            e["end"] = end
        e.update({a: b for a, b in extra.items() if b not in (None, "", [], 0)})
        if detail:
            self.details[self.eid] = detail
        self.events.append(e)
        return self.eid


def _obs_time(o):
    return iso(o.get("effectiveDateTime") or (o.get("effectivePeriod") or {}).get("start") or o.get("effectiveInstant")
               or o.get("issued"))


def _category(r):
    cats = r.get("category") or []
    if isinstance(cats, dict):
        cats = [cats]
    out = set()
    for c in cats:
        out.update(x.lower() for x in codes(c))
        if c.get("text"):
            out.add(c["text"].lower())
    return out


def _flag(interp, v, lo, hi, edge="", cmp=""):
    """The result's flag from the lab's interpretation (each concept's codes and words) by the one rule
    (common.report_flag, #817): H or L where it says which way; "A" or "AA" (abnormal, no way) by the range, else kept
    "A" (inside its range, none given, a text result), as Sanguity's FHIR export writes it; none, against the range
    (edge: its limit leaving its edge out, #780; cmp: a value given as a bound, its comparator, "<15" against a low of
    15 low, #870)."""
    said = [w for c in (interp if isinstance(interp, list) else [interp] if interp else []) for w in (*codes(c), text_of(c))]
    x = v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    return report_flag(said, x, lo, hi, cmp, edge)


def _range(o):
    """(low, high, as printed, edge): edge "<" where a one-sided range leaves its limit's edge out, as "<130 mg/dL"
    does, ">" for ">60" (common.range_edge, #780). FHIR's own low and high include theirs, so a high of 130 beside the
    text "<130" is read as the text says; with no text, they're said "<= 130"."""
    rr = (o.get("referenceRange") or [{}])[0] or {}
    lo, hi = num((rr.get("low") or {}).get("value")), num((rr.get("high") or {}).get("value"))
    txt = rr.get("text") or ""
    if not txt:
        txt = (f"{fmt(lo)} - {fmt(hi)}" if lo is not None and hi is not None else f">= {fmt(lo)}" if lo is not None
               else f"<= {fmt(hi)}" if hi is not None else "")
    if lo is None and hi is None and txt:   # only a text range ("3.5-5.1 mmol/L", "<130 mg/dL", ">=60"): the one parser (#827)
        lo, hi, _ = range_text(txt)
    return lo, hi, txt, range_edge(txt, lo, hi)


def _value(o):
    """(numeric value or None, value as reported, unit). A Quantity with a comparator ("<", "<=", ">=", ">") is a value
    given as a bound, its number kept with it as every other importer keeps one ("<0.5", #870: it was text alone, so a
    bound read back from Sanguity's FHIR export had no number, range or flag); one it doesn't know ("ad") stays text."""
    q = o.get("valueQuantity")
    if isinstance(q, dict) and q.get("value") is not None:
        v = num(q["value"])
        unit = q.get("unit") or q.get("code") or ""
        rep = (q.get("comparator") or "") + fmt(q["value"])
        return (None if q.get("comparator") and not comparator(rep) else v), rep, unit
    for k in ("valueString", "valueInteger", "valueBoolean", "valueDateTime", "valueTime"):
        if k in o:
            v = o[k]
            if k == "valueInteger":
                return v, str(v), ""
            return None, str(v).lower() if k == "valueBoolean" else str(v), ""
    if "valueCodeableConcept" in o:
        return None, text_of(o["valueCodeableConcept"]), ""
    if isinstance(o.get("valueRange"), dict):
        lo, hi = (o["valueRange"].get("low") or {}), (o["valueRange"].get("high") or {})
        return None, f"{fmt(lo.get('value', ''))}-{fmt(hi.get('value', ''))}", lo.get("unit") or hi.get("unit") or ""
    if isinstance(o.get("valueRatio"), dict):
        n, d = o["valueRatio"].get("numerator") or {}, o["valueRatio"].get("denominator") or {}
        return None, f"{fmt(n.get('value', ''))}:{fmt(d.get('value', ''))}", ""
    return None, "", ""


def _server(r):
    """The server a resource came from: meta.source, else the base of an absolute fullUrl; "" when unknown. Only its
    stable part (#1022, common.stable_source): a server that stamps meta.source per write (HAPI FHIR's "#<request id>")
    is one source, never a new one for each record it changes."""
    src = common.stable_source((r.get("meta") or {}).get("source"))
    if src:
        return src
    m = re.match(r"(https?://.+?)/[A-Za-z]+/[^/]+/?$", r.get("_fullUrl") or "")
    return m.group(1) if m else ""


def _code_of(cc, prefer=None):
    """(code, system name) of a CodeableConcept: the coding in the preferred system, else the first coded one."""
    cs = [c for c in (cc or {}).get("coding") or [] if isinstance(c, dict) and c.get("code")] if isinstance(cc, dict) else []
    c = next((c for c in cs if (c.get("system") or "").rstrip("/") == prefer), None) if prefer else None
    c = c or (cs[0] if cs else None)
    if not c:
        return None, None
    system = (c.get("system") or "").rstrip("/")
    return c["code"], SYSTEMS.get(system, system or None)


def _coding_keys(cc):
    return sorted(f"{c.get('system') or ''}|{c['code']}" for c in (cc or {}).get("coding") or [] if c.get("code")) \
        if isinstance(cc, dict) else []


HASH_TIME = ("effectiveDateTime", "effectiveInstant", "issued", "authoredOn", "dateWritten", "dateAsserted",
             "onsetDateTime", "recordedDate", "dateRecorded", "date", "created", "indexed",
             # #179: the times of the kinds read since #165 (last, so no other kind's key changes); Health Cards'
             # immunizations have no id, and two doses of one vaccine differ only by their day
             "occurrenceDateTime", "performedDateTime", "whenHandedOver", "whenPrepared", "start", "started")
HASH_CODE = ("code", "medicationCodeableConcept", "type", "vaccineCode", "procedureCode")


def resource_key(r, mixed=False):
    """Stable source key for a resource (contract amendment to #18): "fhir:<type>/<id>", then an identifier,
    then a hash of its identifying fields. Never depends on input order. With resources from more than one
    server in the upload, the server is part of the key so ids from two servers can't collide."""
    rt = r.get("resourceType") or "Resource"
    if r.get("id"):
        srv = _server(r) if mixed else ""
        return f"fhir:{srv}|{rt}/{r['id']}" if srv else f"fhir:{rt}/{r['id']}"
    for ident in r.get("identifier") or []:
        if isinstance(ident, dict) and ident.get("value"):
            return f"fhir:{rt}?identifier={ident.get('system') or ''}|{ident['value']}"
    when = next((r[k] for k in HASH_TIME if isinstance(r.get(k), str)), None) or \
        next((r[k].get("start") for k in ("effectivePeriod", "period", "onsetPeriod") if isinstance(r.get(k), dict)), "")
    code = next((_coding_keys(r[k]) or [text_of(r[k])] for k in HASH_CODE if r.get(k)), [])
    fields = [rt, iso(when) or when or "", ",".join(code), _value(r)[1] if rt == "Observation" else ""]
    return hkey(*fields)


def hkey(*fields):
    """docs/importers.md's hash key: SHA-256 over the normalised fields, first 16 hex characters."""
    return NAME + ":h:" + hashlib.sha256("\x1f".join(str(f).strip().lower() for f in fields).encode()).hexdigest()[:16]


class Home:
    """#205: Observations measured at home, read into a watch summary (watch_store.py's shape) instead of the records:

    - a home reading: an Observation the Patient measured (performer: the Patient) of blood pressure (a panel with
      systolic, diastolic and maybe heart rate components), body temperature, blood oxygen or glucose, into
      bp_readings, temp_readings, spo2_readings or glucose_readings, [time, value(s)..., source, app]: the source is
      the device that took it (Observation.device), the app the one it came through (Sanguity's export names it as the
      device's property "app"), and one with no device was typed in;
    - Sanguity's export's own codes (MEASURE): the phone's daily measures into `metrics` (a CGM's day summaries among
      them, a meter's readings by their local time), an ECG ("ecg", or MDC's ECG code from the Patient) into `ecg`
      with its waveform's samples in `ecg_waves`, an irregular rhythm notification into `ecg`; cycle records left out
      (counted), as loading a Sanguity export leaves them out."""

    def __init__(self, idx):
        self.idx, self.metrics, self.lists, self.ecg, self.waves, self.cycle = idx, {}, defaultdict(list), [], {}, 0

    @staticmethod
    def code(cc, system=MEASURE):
        for c in (cc or {}).get("coding") or [] if isinstance(cc, dict) else []:
            if isinstance(c, dict) and c.get("system") == system and c.get("code"):
                return c["code"]
        return None

    def by_patient(self, r):
        for p in r.get("performer") or []:
            hit = self.idx.get(p, r)
            if (hit or {}).get("resourceType") == "Patient" or (isinstance(p, dict) and (p.get("reference") or "").startswith("Patient/")):
                return True
        return False

    def device(self, r):
        """(source, app) of a reading: (None, "Typed in") with no device."""
        ref = r.get("device")
        if not isinstance(ref, dict):
            return None, TYPED_IN
        dev = self.idx.get(ref, r) or {}
        names = [n for n in dev.get("deviceName") or [] if isinstance(n, dict) and n.get("name")]
        source = next((n["name"] for n in names if n.get("type") == "user-friendly-name"), None)
        app = next((v.get("text") for prop in dev.get("property") or [] if isinstance(prop, dict) and self.code(prop.get("type")) == "app"
                    for v in prop.get("valueCode") or [] if isinstance(v, dict) and v.get("text")), None)
        if not names and not app:
            source = ref.get("display") or None
        return source, app or (names[0]["name"] if names and not source else "FHIR")

    @staticmethod
    def value(q, to=None):
        """A Quantity's value, in mg/dL for glucose or °C for temperature when it's in another unit."""
        v = num((q or {}).get("value")) if isinstance(q, dict) else None
        if v is None:
            return None
        u = str(q.get("code") or q.get("unit") or "").lower()
        if to == "C" and u in ("[degf]", "°f", "degf", "f"):
            return round((v - 32) * 5 / 9, 2)
        if to == "mg/dL" and u in ("mmol/l", "mmol/l{glucose}"):
            return round(v * 18.0156, 1)
        return v

    def add(self, r):
        """Take r if it's measured at home or one of the export's own; False to leave it to the records."""
        k = self.code(r.get("code"))
        raw = r.get("effectiveDateTime") or (r.get("effectivePeriod") or {}).get("start") or ""
        if k and k.startswith("cycle_"):
            self.cycle += 1
            return True
        if k in ("ecg", "irregular_rhythm") or (MDC in {c.get("system") for c in (r.get("code") or {}).get("coding") or []
                                                         if isinstance(c, dict)} and self.by_patient(r)):
            return self.add_ecg(r, "irregular_rhythm" if k == "irregular_rhythm" else "ecg")
        if k and k not in ("app", "meal"):
            q = r.get("valueQuantity")
            v = num((q or {}).get("value")) if isinstance(q, dict) else None
            if v is None or not raw:
                return True
            cat = next((c for cc in r.get("category") or [] for c in [self.code(cc, MEASURE_GROUP)] if c), "")
            m = self.metrics.setdefault(k, {"key": k, "name": next((c.get("display") for c in r["code"].get("coding") or []
                                                                     if c.get("system") == MEASURE and c.get("display")), None) or text_of(r.get("code")) or k,
                                            "unit": q.get("unit") or q.get("code") or "", "group": cat, "pts": {}})
            m["pts"][raw[:16] if k == "glucose_meter" else raw[:10]] = v
            return True
        if not self.by_patient(r):
            return False
        loinc = set(codes(r.get("code"), LOINC))
        lst = next((HOME_READINGS[c] for c in sorted(loinc) if c in HOME_READINGS), None)
        t = iso(raw)
        if not lst or not t:
            return False
        source, app = self.device(r)
        comps = {c: x for x in r.get("component") or [] if isinstance(x, dict) for c in codes(x.get("code"), LOINC)}
        if lst == "bp_readings":
            sys_, dia = (self.value((comps.get(c) or {}).get("valueQuantity")) for c in ("8480-6", "8462-4"))
            if sys_ is None or dia is None:
                return False
            self.lists[lst].append([t, sys_, dia, self.value((comps.get("8867-4") or {}).get("valueQuantity")), source, app])
            return True
        v = self.value(r.get("valueQuantity"), {"temp_readings": "C", "glucose_readings": "mg/dL"}.get(lst))
        if v is None:
            return False
        row = [t, v, source, app]
        if lst == "glucose_readings":
            meal = next((self.code(x.get("valueCodeableConcept")) for x in r.get("component") or []
                         if isinstance(x, dict) and self.code(x.get("code")) == "meal"), None)
            row.append(meal)
        self.lists[lst].append(row)
        return True

    def add_ecg(self, r, kind):
        from .. import ecg   # standard library only, like this importer (the Apple Health importer uses it too)
        per = r.get("effectivePeriod") or {}
        start = ecg.when(per.get("start") or r.get("effectiveDateTime"))
        if not start:
            return True
        end = ecg.when(per.get("end")) if per.get("end") else None
        utc = lambda d: d.astimezone(timezone.utc).isoformat()
        dev = self.idx.get(r.get("device"), r) if isinstance(r.get("device"), dict) else None
        source, app = self.device(r)
        e = {"key": ecg.key("rhythm" if kind == "irregular_rhythm" else "ecg", start), "type": kind, "start": utc(start)}
        if end:
            e["end"] = utc(end)
        if kind == "ecg":
            hr = next((num((x.get("valueQuantity") or {}).get("value")) for x in r.get("component") or []
                       if isinstance(x, dict) and "8867-4" in codes(x.get("code"), LOINC)), None)
            wave = next((x for x in r.get("component") or [] if isinstance(x, dict) and isinstance(x.get("valueSampledData"), dict)), None)
            e.update({"classification": text_of(r.get("valueCodeableConcept")) or r.get("valueString") or None, "avg_hr": hr,
                      "symptoms": next((n["text"][len("Symptoms: "):] for n in r.get("note") or [] if isinstance(n, dict)
                                        and str(n.get("text") or "").startswith("Symptoms: ")), None),
                      "software": next((v.get("value") for v in (dev or {}).get("version") or [] if isinstance(v, dict) and v.get("value")), None)})
            if wave:
                sd = wave["valueSampledData"]
                samples = [num(x) for x in str(sd.get("data") or "").split()[:ecg.MAX_SAMPLES]]
                period = num(sd.get("period"))
                if samples and None not in samples and period:
                    rate = round(1000 / period, 3)
                    rate = int(rate) if rate == int(rate) else rate
                    lead = text_of(wave.get("code")) or None
                    e.update({"lead": lead, "rate": rate, "samples": len(samples), "unit": "µV", "wave": True})
                    self.waves[e["key"]] = {"key": e["key"], "rate": rate, "unit": "µV", "lead": lead, "samples": samples}
        e.update({"device": source, "via": app if app not in (TYPED_IN, "FHIR") else None})
        self.ecg.append({k: v for k, v in e.items() if v is not None})
        return True

    def summary(self):
        """The watch summary of what was taken, or None."""
        metrics = [{**m, "pts": sorted([d, v] for d, v in m["pts"].items())} for m in self.metrics.values() if m["pts"]]
        out = {"metrics": metrics, "exercise": []}
        for k, rows in self.lists.items():
            out[k] = sorted(rows, key=lambda x: (x[0], str(x[-2]), str(x[-1])))
        if self.ecg:
            out["ecg"] = sorted(self.ecg, key=lambda e: e["start"])
        if self.waves:
            out["ecg_waves"] = self.waves
        if not (metrics or self.lists or self.ecg):
            return None
        days = [str(p[0])[:10] for m in metrics for p in m["pts"]] + [str(x[0])[:10] for rows in self.lists.values() for x in rows] \
            + [e["start"][:10] for e in self.ecg]
        out["updated"] = max(days) + "T00:00:00+00:00"   # deterministic: the latest day it holds
        return out


def parse(files):
    warnings = []
    resources = _load(files, warnings)
    if not resources:
        raise ValueError("No FHIR records were found in this upload. Expected FHIR JSON: a Bundle, NDJSON with one "
                         "resource per line, or a folder of .json resources.")
    idx = Index(resources)
    mixed = len({_server(r) for r, _ in resources} - {""}) > 1

    def rkey(r):
        return resource_key(r, mixed)
    b = Builder()
    labs = {}                               # series name -> {unit, loinc, pts, txt, panels}
    problems, upcoming, weights, family = [], [], [], []
    lab_days = defaultdict(list)            # day -> [labitem ids]
    lab_day_meta = defaultdict(lambda: {"t": None, "abn": 0, "n": 0, "orders": set()})
    used_obs = set()
    reported_studies = set()                # ImagingStudies a report here describes (the report is the event)
    skipped, inline, remote, missing = Counter(), 0, 0, 0
    comms = []                              # Communications, put together into conversations (#205)
    keys_seen = set()

    def fresh(key):
        """#179: False for a record whose key this upload had already (the same immunization twice in a Health Card)."""
        if key in keys_seen:
            return False
        keys_seen.add(key)
        return True

    def org_of(r):
        for key in ("performer", "serviceProvider", "custodian", "author", "asserter", "recorder", "informationSource",
                    "requester"):
            refs = r.get(key)
            for ref in refs if isinstance(refs, list) else [refs] if refs else []:
                ref = ref.get("agent") or ref.get("onBehalfOf") or ref if isinstance(ref, dict) else ref
                hit = idx.get(ref, r)
                if hit and hit.get("resourceType") == "Organization":
                    return idx.name(ref, r)
                if isinstance(ref, dict) and (ref.get("reference") or "").startswith("Organization"):
                    return idx.name(ref, r)
        return ""

    def who_of(r):
        for key in ("performer", "requester", "prescriber", "asserter", "recorder", "author", "informationSource"):
            refs = r.get(key)
            for ref in refs if isinstance(refs, list) else [refs] if refs else []:
                ref = ref.get("actor") or ref.get("agent") or ref if isinstance(ref, dict) else ref
                hit = idx.get(ref, r)
                if (hit and hit.get("resourceType") in ("Practitioner", "PractitionerRole")) or \
                        (isinstance(ref, dict) and re.match(r"Practitioner", ref.get("reference") or "")):
                    return idx.name(ref, r)
        # DSTU2 exports (Apple Health) often carry only a display name: take it when the key names a person
        for key in ("prescriber", "requester", "asserter", "recorder"):
            ref = r.get(key)
            if isinstance(ref, dict) and not ref.get("reference") and ref.get("display"):
                return ref["display"]
        return ""

    def series_point(o, name, code_cc, value_src, t, panel, src):
        """Add one Observation (or component) to its lab series and return the detail row for it."""
        v, rep, unit = _value(value_src)
        lo, hi, rtxt, edge = _range(value_src if value_src.get("referenceRange") else o)
        flag = _flag(value_src.get("interpretation") or o.get("interpretation"), v, lo, hi, edge, comparator(rep))
        loincs = codes(code_cc, LOINC)
        vc = next((c for c in loincs if c in common.VITALS), None)
        loinc = vc or (loincs or [None])[0]
        x, s_unit, said = v, unit, rep
        if vc:   # a vital sign by its LOINC code first, named and in the unit every importer gives it (#1102)
            base, x, s_unit = common.vital(vc, v, unit)
            if x != v:   # converted: its value as reported keeps its unit ("37 Cel"), its range (in that unit) left out
                said, lo, hi, edge = f"{rep} {unit}".strip(), None, None, ""
        else:
            base = canonical_lab(name, loinc) or name   # the series: lab-aliases.json's name when it knows the test
        key = base
        s = labs.get(key)
        if s and s["unit"] and s_unit and unit_key(s["unit"]) != unit_key(s_unit):   # same name, other unit: keep apart
            key = f"{base} ({s_unit})"
            s = labs.get(key)
        if s is None:
            s = labs[key] = {"unit": s_unit, "loinc": loinc, "code": None, "pts": [], "txt": [], "panels": Counter()}
        s["loinc"] = s["loinc"] or loinc
        s["code"] = s["code"] or next(iter(_coding_keys(code_cc)), None)
        s["unit"] = s["unit"] or s_unit
        if panel:
            s["panels"][panel] += 1
        if v is not None:
            s["pts"].append([t, x, lo, hi, flag, said, src] + ([edge] if edge else []))
        elif rep:
            s["txt"].append([t, rep])
        return [name, rep, unit, rtxt, flag], bool(flag)

    def obs_rows(o, panel, src):
        """Detail rows for an Observation (one per component for panels such as blood pressure)."""
        t = _obs_time(o)
        rows, abn = [], 0
        comps = o.get("component") or []
        if comps and not any(k.startswith("value") for k in o):
            for c in comps:
                row, a = series_point(o, text_of(c.get("code")) or "Result", c.get("code"), c, t, panel, src)
                rows.append(row)
                abn += a
        else:
            name = text_of(o.get("code")) or "Result"
            row, a = series_point(o, name, o.get("code"), o, t, panel, src)
            rows.append(row)
            abn += a
        return rows, abn

    def add_labitem(key, t, order, members, report, prov, status, lab, specimen, panel=None):
        rows, keys, abn = [], [], 0
        for o in members:
            src = lab or SOURCE
            rr, a = obs_rows(o, order if panel is None else panel, src)
            rows += rr
            keys += [rkey(o)] * len(rr)   # (#885) the Observation each row is: a day's key isn't one source's record
            abn += a
        if not rows:
            return
        det = {"type": "result", "order": order, "name": order, "collected": t[:16].replace("T", " "),
               "resulted": t[:10], "provider": prov, "status": status, "specimen": specimen, "lab": lab, "dx": [],
               "rows": rows, "keys": keys, "report": report, "comments": []}
        sub = " · ".join(x for x in [prov, status, lab] if x)
        i = b.add(t, "labitem", order, sub, det, abn=abn, hidden=1, key=key)
        day = t[:10]
        lab_days[day].append(i)
        m = lab_day_meta[day]
        m["t"] = min(m["t"] or t, t)
        m["abn"] += abn
        m["n"] += len(rows)
        m["orders"].add(order)

    def document(key, t, title, att, owner, rel, org, kind="document"):
        nonlocal inline, remote, missing
        if not isinstance(att, dict):
            return
        if att.get("data") and not att.get("url"):
            inline += 1   # skipped: no place to write files from an upload yet (see module docstring)
            return
        url = att.get("url") or ""
        if not url:
            return
        if re.match(r"[a-z][a-z0-9+.-]*:", url, re.I) and not url.lower().startswith("file:"):
            # Binary/123 or a full server URL: use a file of that name if the upload has exactly one
            hits = sorted(k for k in files if posixpath.basename(k) == url.rstrip("/").rsplit("/", 1)[-1])
            target = hits[0] if len(hits) == 1 else None
            if not target:
                remote += 1
        else:
            target = _find(files, url, rel)
            if not target:
                missing += 1
        # a document the export names but didn't include still shows, with "url": null (docs/importers.md)
        ct = (att.get("contentType") or "").split(";")[0].strip().lower()
        ext = posixpath.splitext((target or url.split("?")[0]).split("#")[0])[1].lstrip(".").lower() or EXT.get(ct, "")
        b.add(t, kind, title or att.get("title") or posixpath.basename(target or url),
              " · ".join(x for x in [ext.upper(), org] if x), {"type": "file", "url": target, "ext": ext}, key=key)

    # --- DiagnosticReports first, so their Observations are grouped under them
    for r, rel in resources:
        if r["resourceType"] != "DiagnosticReport":
            continue
        t = iso(r.get("effectiveDateTime") or (r.get("effectivePeriod") or {}).get("start") or r.get("issued"))
        order = text_of(r.get("code")) or "Report"
        cats = _category(r)
        org, prov = org_of(r), who_of(r)
        status = cap(r.get("status") or "")
        members = []
        for ref in r.get("result") or []:
            o = idx.get(ref, r)
            if o and o.get("resourceType") == "Observation":
                members.append(o)
                used_obs.add(id(o))
        t = t or next((_obs_time(o) for o in members if _obs_time(o)), None)
        if not t:
            continue
        report = [["impression", r["conclusion"], 0]] if r.get("conclusion") else []
        if members:
            add_labitem(rkey(r), t, order, members, report, prov, status, org, "")
        elif report or (r.get("text") or {}).get("div"):
            if not report:
                report = [["narrative", r["text"]["div"], 1]]
            kind = "imaging" if cats & {"rad", "imaging", "radiology", "img"} else "study"
            b.add(t, kind, order, " · ".join(x for x in [prov, status, org] if x),
                  {"type": "result", "order": order, "name": order, "collected": t[:16].replace("T", " "),
                   "resulted": (iso(r.get("issued")) or t)[:10], "provider": prov, "status": status, "lab": org,
                   "dx": [], "rows": [], "report": report, "comments": []}, key=rkey(r))
        if members or report or r.get("presentedForm"):   # #179: the study this report is, not an event of its own
            reported_studies.update(id(idx.get(ref, r)) for ref in r.get("imagingStudy") or [] if idx.get(ref, r))
        # the report's plain key is on its result event, so each form adds #presentedForm/<position, from 1>
        for i, att in enumerate(r.get("presentedForm") or [], 1):
            document(f"{rkey(r)}#presentedForm/{i}", t, order, att, r, rel, org)

    # --- Observations not in a report
    loose = defaultdict(list)   # (day, org) -> observations
    home = Home(idx)            # #205: what the person measured at home, and the phone's measures, for `watch`
    for r, rel in resources:
        if r["resourceType"] != "Observation" or id(r) in used_obs:
            continue
        if (r.get("status") or "") in ("entered-in-error", "cancelled"):
            continue
        t = _obs_time(r)
        if not t or home.add(r):
            continue
        cats = _category(r)
        loinc = set(codes(r.get("code"), LOINC))
        if cats & {"vital-signs", "vital signs", "vitals"}:
            if loinc & WEIGHT and isinstance(r.get("valueQuantity"), dict):
                q = r["valueQuantity"]
                v, u = num(q.get("value")), (q.get("code") or q.get("unit") or "").lower()
                if v is not None:
                    lb = round(v * 2.20462, 1) if u in ("kg", "kilogram", "kilograms") else \
                        round(v / 453.592, 1) if u in ("g", "gram") else v
                    weights.append([t, lb, org_of(r) or SOURCE])
                continue
            obs_rows(r, "Vitals", org_of(r) or SOURCE)
            continue
        if cats and not cats & {"laboratory", "lab"} and not (loinc and not cats - {"exam"}):
            skipped[f"Observation ({', '.join(sorted(cats))})"] += 1
            continue
        if not cats and not loinc and r.get("valueQuantity") is None:
            skipped["Observation (uncategorised)"] += 1
            continue
        loose[(t[:10], org_of(r))].append(r)
    orgs_on = Counter(day for day, _ in loose)
    for (day, org), obs in sorted(loose.items()):
        obs.sort(key=lambda o: (_obs_time(o), rkey(o)))
        # several Observations share this labitem, so its key names the day (and the lab, when a day has several)
        add_labitem(f"fhir:labitem:{day}" + (f"|{org}" if orgs_on[day] > 1 else ""), _obs_time(obs[0]), "Lab results", obs, [], who_of(obs[0]),
                    cap(obs[0].get("status") or ""), org, text_of((idx.get(obs[0].get("specimen"), obs[0]) or {}).get("type")),
                    panel="")   # (#1102) "Lab results" names no panel: the records put the lab in its own (records._panels)

    # --- one visible lab event per day, linking that day's results
    for day, ids in sorted(lab_days.items()):
        m = lab_day_meta[day]
        orders = sorted(o for o in m["orders"] if o)
        b.add(m["t"], "lab", common.lab_day_title(m["n"]), common.lab_day_sub(orders),
              {"type": "labday", "ids": ids}, abn=m["abn"] or None, key=f"fhir:labday:{day}")

    # --- Conditions, medications, encounters, documents
    for r, rel in resources:
        rt = r["resourceType"]
        if rt == "Condition":
            vs = status_of(r.get("verificationStatus")).lower()
            if vs in ("entered in error", "refuted"):
                continue
            onset = r.get("onsetDateTime") or (r.get("onsetPeriod") or {}).get("start") or r.get("recordedDate") \
                or r.get("dateRecorded") or r.get("assertedDate")
            cat = set(codes((r.get("category") or [{}])[0] if isinstance(r.get("category"), list) else r.get("category")))
            problems.append({"name": text_of(r.get("code")) or "Condition",
                             "type": "Health concern" if "health-concern" in cat else "Diagnosis",
                             "date": (iso(onset) or "")[:10] or None,
                             "status": status_of(r.get("clinicalStatus")) or ("Resolved" if r.get("abatementDateTime") else ""),
                             "src": org_of(r) or who_of(r), "key": rkey(r)})
        elif rt == "AllergyIntolerance":   # #165, and #179: the parts C-CDA's are read into (common.allergy)
            vs = status_of(r.get("verificationStatus")).lower()
            if vs in ("entered in error", "refuted") or (r.get("status") or "") in ("entered-in-error", "refuted"):
                continue
            cc = r.get("code") or r.get("substance")   # DSTU2: substance
            reacts = [x for x in r.get("reaction") or [] if isinstance(x, dict)]
            rank = {"mild": 1, "moderate": 2, "severe": 3}
            sev = max((str(x.get("severity") or "") for x in reacts), key=lambda s: rank.get(s.lower(), 0), default="")
            onset = r.get("onsetDateTime") or r.get("onset") or r.get("recordedDate") or r.get("assertedDate")
            code, system = _code_of(cc)
            p = common.allergy(text_of(cc), [text_of(m) for x in reacts for m in x.get("manifestation") or []], severity=sev,
                               criticality=CRITICALITY.get(str(r.get("criticality") or ""), r.get("criticality") or ""),
                               status=status_of(r.get("clinicalStatus")) or cap(r.get("status") or ""),
                               date=(iso(onset) or "")[:10] or None, src=org_of(r) or who_of(r), code=code, system=system)
            problems.append({**p, "key": rkey(r)})
        elif rt == "Immunization":   # #179: a kind of its own (#165 made them procedures)
            if r.get("status") in ("entered-in-error", "not-done") or r.get("wasNotGiven"):
                continue
            t = iso(r.get("occurrenceDateTime") or r.get("date"))
            if not t or not fresh(rkey(r)):
                continue
            proto = next((x for x in r.get("protocolApplied") or r.get("vaccinationProtocol") or [] if isinstance(x, dict)), {})
            dose = r.get("doseQuantity") if isinstance(r.get("doseQuantity"), dict) else {}
            who, org = "", ""
            for p in r.get("performer") if isinstance(r.get("performer"), list) else [r.get("performer")]:
                actor = (p.get("actor") or p) if isinstance(p, dict) else None   # R4: {function, actor}; DSTU2: the actor
                hit = idx.get(actor, r) if actor else None
                kind = (hit or {}).get("resourceType") or ((actor or {}).get("reference") or "").split("/")[0]
                name = idx.name(actor, r) if actor else ""
                if kind == "Organization":
                    org = org or name
                elif name:
                    who = who or name   # a person, or (Health Cards) only a display: who gave it, as recorded
            code, system = _code_of(r.get("vaccineCode"), prefer=CVX)
            vc = r.get("vaccineCode") if isinstance(r.get("vaccineCode"), dict) else {}
            named = vc.get("text") or next((c.get("display") for c in vc.get("coding") or [] if isinstance(c, dict) and c.get("display")), "")
            if not named and system == "CVX":   # only a code: its plain name, or the code itself
                named = CVX_NAMES.get(code) or f"Vaccine (CVX {code})"
            title, sub, det = common.immunization(
                named or text_of(vc), org or who or SOURCE, code=code, system=system,
                dose=common.dose_text(proto.get("doseNumberPositiveInt") or proto.get("doseNumberString") or proto.get("doseSequence"),
                                      proto.get("seriesDosesPositiveInt") or proto.get("seriesDosesString") or proto.get("seriesDoses")),
                amount=" ".join(x for x in [fmt(num(dose.get("value"))) if num(dose.get("value")) is not None else "",
                                            dose.get("unit") or dose.get("code") or ""] if x),
                route=text_of(r.get("route")), site=text_of(r.get("site")),
                lot=r.get("lotNumber") if isinstance(r.get("lotNumber"), str) else "",
                manufacturer=idx.name(r.get("manufacturer"), r), performer=who,
                where=org or (idx.name(r.get("location"), r) if r.get("location") else ""), status=cap(r.get("status") or ""),
                reason="; ".join(text_of(c) for c in r.get("reasonCode") or ((r.get("explanation") or {}).get("reason") or [])
                                 if text_of(c)),
                note="; ".join(n.get("text") for n in r.get("note") or [] if isinstance(n, dict) and n.get("text")))
            b.add(t, "immunization", title, sub, det, key=rkey(r))
        elif rt == "Procedure":   # #165: a procedure event with what the record says
            if r.get("status") in ("entered-in-error", "not-done") or r.get("notPerformed"):
                continue
            per = r.get("performedPeriod") or {}
            t = iso(r.get("performedDateTime") or per.get("start"))
            if not t:
                continue
            title = text_of(r.get("code")) or "Procedure"
            who = who_of(r) or next((x.get("display") for p in (r.get("performer") if isinstance(r.get("performer"), list)
                                                               else [r.get("performer")])   # DSTU2: a display only
                                     for x in [(p or {}).get("actor") or p] if isinstance(x, dict) and x.get("display")
                                     and not x.get("reference")), "")
            org = org_of(r) or (idx.name(r.get("location"), r) if r.get("location") else "")
            report = [[k, v, 0] for k, v in (
                ("Reason", "; ".join(text_of(c) for c in r.get("reasonCode") or r.get("reason") or [] if text_of(c))),
                ("Body site", text_of(r.get("bodySite"))),
                ("Outcome", text_of(r.get("outcome"))),
                ("Notes", "; ".join(n.get("text") for n in r.get("note") or r.get("notes") or []   # DSTU2: notes
                                   if isinstance(n, dict) and n.get("text"))))
                if v]
            end = iso(per.get("end"))
            b.add(t, "procedure", title, " · ".join(x for x in [who, org] if x),
                  {"type": "result", "order": title, "name": title, "collected": t[:16].replace("T", " "),
                   "resulted": t[:10], "provider": who or None, "status": cap(r.get("status") or ""), "lab": org or None,
                   "dx": [], "rows": [], "report": report, "comments": []},
                  end=end if end and end > t else None, key=rkey(r))
        elif rt == "MedicationDispense":   # #179: a pharmacy fill, an `rx` event as the claims' and #50's fills
            if r.get("status") in ("entered-in-error", "cancelled", "declined", "stopped", "preparation", "in-progress"):
                continue
            t = iso(r.get("whenHandedOver") or r.get("whenPrepared"))
            if not t or not fresh(rkey(r)):
                continue
            name = text_of(r.get("medicationCodeableConcept")) or idx.name(r.get("medicationReference"), r) or "Medication"
            pharmacy = next((idx.name((p or {}).get("actor"), r) for p in r.get("performer") or [] if isinstance(p, dict)
                             and idx.name(p.get("actor"), r)), "") or (idx.name(r.get("location"), r) if r.get("location") else "")
            supply = num((r.get("daysSupply") or {}).get("value")) if isinstance(r.get("daysSupply"), dict) else None
            q = r.get("quantity") if isinstance(r.get("quantity"), dict) else {}
            med = idx.get(r.get("medicationReference"), r) if r.get("medicationReference") else None
            rx = codes(r.get("medicationCodeableConcept"), RXNORM) or codes((med or {}).get("code"), RXNORM)
            prescriber = next((who_of(idx.get(a, r)) for a in r.get("authorizingPrescription") or []
                               if isinstance(idx.get(a, r), dict)), "")
            det = {"type": "med", "instructions": "; ".join(d.get("text") for d in r.get("dosageInstruction") or []
                                                            if isinstance(d, dict) and d.get("text")) or None,
                   "status": cap(r.get("status") or "") or None, "src": pharmacy or SOURCE, "pharmacy": pharmacy or None,
                   "daysSupply": supply, "quantity": " ".join(x for x in [fmt(num(q.get("value"))) if num(q.get("value")) is not None
                                                                          else "", q.get("unit") or ""] if x) or None,
                   "prescriber": prescriber or None, "rxnorm": rx[0] if rx else None}
            b.add(t, "rx", name, " · ".join(x for x in ["Pharmacy fill", pharmacy, f"{fmt(supply)}-day supply" if supply else ""] if x),
                  {k: v for k, v in det.items() if v is not None}, key=rkey(r))
        elif rt == "Appointment":   # #179: booked visits go to `upcoming`, as planned Encounters do
            if r.get("status") not in ("booked", "pending"):
                continue   # fulfilled, arrived and checked-in ones are visits (their Encounter); cancelled and no-shows aren't
            t = iso(r.get("start"))
            if not t or not fresh(rkey(r)):
                continue
            who, where = "", ""
            for p in r.get("participant") or []:
                actor = (p or {}).get("actor") if isinstance(p, dict) else None
                hit = idx.get(actor, r) if actor else None
                kind = (hit or {}).get("resourceType") or ((actor or {}).get("reference") or "").split("/")[0]
                if kind in ("Practitioner", "PractitionerRole") and not who:
                    who = idx.name(actor, r)
                elif kind in ("Location", "HealthcareService", "Organization") and not where:
                    where = idx.name(actor, r)
            title = r.get("description") or text_of(r.get("serviceType")) or text_of(r.get("appointmentType")) \
                or text_of(r.get("type")) or "Appointment"
            upcoming.append({"t": t, "title": title, "who": who, "where": where,
                             "dx": "; ".join(text_of(c) for c in r.get("reasonCode") or r.get("reason") or [] if text_of(c)),
                             "procs": [], "key": rkey(r)})
        elif rt == "ImagingStudy":   # #179: a study no DiagnosticReport here describes is an `imaging` event of its own
            t = iso(r.get("started"))
            if not t or id(r) in reported_studies or r.get("status") in ("entered-in-error", "cancelled"):
                continue
            series = [x for x in r.get("series") or [] if isinstance(x, dict)]
            mods = list(dict.fromkeys(MODALITY.get(c, c) for x in series + [r] for m in (x.get("modality") if isinstance(x.get("modality"), list)
                                      else [x.get("modality")]) if isinstance(m, dict) for c in [m.get("display") or m.get("code")] if c))
            title = r.get("description") or text_of(r.get("procedureCode")) or (" / ".join(mods) + " study" if mods else "Imaging study")
            org = (idx.name(r.get("location"), r) if r.get("location") else "") or org_of(r)
            who = idx.name(r.get("referrer"), r) if r.get("referrer") else ""
            report = [[k, v, 0] for k, v in (
                ("Modality", ", ".join(mods)),
                ("Body site", ", ".join(dict.fromkeys(text_of(x.get("bodySite")) for x in series if text_of(x.get("bodySite"))))),
                ("Series", str(r.get("numberOfSeries") or len(series) or "")),
                ("Images", str(r.get("numberOfInstances") or "")),
                ("Reason", "; ".join(text_of(c) for c in r.get("reasonCode") or [] if text_of(c)))) if v]
            b.add(t, "imaging", title, " · ".join(x for x in [who, org] if x),
                  {"type": "result", "order": title, "name": title, "collected": t[:16].replace("T", " "), "resulted": t[:10],
                   "provider": who or None, "status": cap(r.get("status") or ""), "lab": org or None, "dx": [], "rows": [],
                   "report": report, "comments": []}, key=rkey(r))
        elif rt in ("MedicationRequest", "MedicationStatement", "MedicationOrder"):
            if r.get("status") in ("entered-in-error",):
                continue
            t = iso(r.get("authoredOn") or r.get("dateWritten") or (r.get("effectivePeriod") or {}).get("start")
                    or r.get("effectiveDateTime") or r.get("dateAsserted")
                    or ((r.get("dispenseRequest") or {}).get("validityPeriod") or {}).get("start"))
            name = text_of(r.get("medicationCodeableConcept")) or idx.name(r.get("medicationReference"), r) or "Medication"
            dose = r.get("dosageInstruction") or r.get("dosage") or []
            instr = "; ".join(d.get("text") for d in dose if isinstance(d, dict) and d.get("text"))
            status = cap(r.get("status") or "")
            org = org_of(r)
            det = {"type": "med", "instructions": instr, "status": status, "src": org or who_of(r)}
            # #50: who prescribed it, when it ended and its RxNorm code, when the resource says
            who = who_of(r) if rt != "MedicationStatement" else next(   # #205: a statement's prescriber is its request's
                (who_of(x) for ref in r.get("basedOn") or [] for x in [idx.get(ref, r)]
                 if isinstance(x, dict) and x.get("resourceType") == "MedicationRequest" and who_of(x)), "")
            if who:
                det["prescriber"] = who
            end = iso((r.get("effectivePeriod") or {}).get("end"))
            if end and t and end > t:
                det["end"] = end
            med = idx.get(r.get("medicationReference"), r) if r.get("medicationReference") else None
            rx = codes(r.get("medicationCodeableConcept"), RXNORM) or codes((med or {}).get("code"), RXNORM)
            if rx:
                det["rxnorm"] = rx[0]
            b.add(t, "med", name, " · ".join(x for x in [instr, status] if x), det, key=rkey(r))
        elif rt == "Encounter":
            if r.get("status") in ("entered-in-error", "cancelled"):
                continue
            per = r.get("period") or {}
            t, end = iso(per.get("start")), iso(per.get("end"))
            klass = r.get("class")
            if isinstance(klass, list):
                klass = klass[0] if klass else None
            kcode = (klass.get("code") if isinstance(klass, dict) else klass) or ""
            title = text_of(r.get("type")) or CLASS_TITLE.get(kcode) or text_of(klass) or "Visit"
            who = ""
            for p in r.get("participant") or []:
                who = idx.name(p.get("individual") or p.get("actor"), r)
                if who:
                    break
            where = next((idx.name(l.get("location"), r) for l in r.get("location") or [] if idx.name(l.get("location"), r)), "")
            org = idx.name(r.get("serviceProvider"), r) if r.get("serviceProvider") else ""
            dx = "; ".join(dict.fromkeys(x for x in
                           [text_of(c) for c in r.get("reasonCode") or r.get("reason") or []] +
                           [text_of((idx.get(d.get("condition"), r) or {}).get("code")) or (d.get("condition") or {}).get("display", "")
                            for d in r.get("diagnosis") or []] if x))
            if r.get("status") == "planned":   # by status, not the clock: the same upload gives the same records
                if t:
                    upcoming.append({"t": t, "title": title, "who": who, "where": where or org, "dx": dx, "procs": [],
                                     "key": rkey(r)})
                continue
            if kcode in INPATIENT or kcode.lower() in INPATIENT:
                if not t:
                    continue
                stop = end if end and end > t else t[:10] + "T23:59:00"
                nights = (datetime.fromisoformat(stop[:10]) - datetime.fromisoformat(t[:10])).days
                place = where or org
                b.add(t, "stay", "Hospital stay" + (f" · {org}" if org else ""),
                      " · ".join(x for x in [place, f"{nights} night{'s' if nights != 1 else ''}" if nights else "same day"] if x),
                      {"type": "stay", "admit": t, "discharge": end or "", "where": place, "how": "FHIR encounter"}, end=stop, key=rkey(r))
                continue
            # #205: the narrative a record adds to its Encounter ("additional", not one generated from its elements): the
            # visit's note, and the procedures done there as a paragraph "Procedures: a; b" (Sanguity's export writes them so)
            paras = [common.strip_html(x).strip() for x in re.findall(r"<p>(.*?)</p>", (r.get("text") or {}).get("div") or "", re.S)] \
                if (r.get("text") or {}).get("status") == "additional" else []
            procs = [x for p in paras if p.startswith("Procedures: ") for x in p[len("Procedures: "):].split("; ") if x]
            note = "\n\n".join(p for p in paras if p and not p.startswith("Procedures: "))
            b.add(t, "visit", title, " · ".join(x for x in [who, where, org] if x),
                  {"type": "visit", "who": who, "where": where or org, "dx": dx, "procs": procs, "src": org or SOURCE, "note": note},
                  end=end if end and t and end > t else None, key=rkey(r))
        elif rt == "DocumentReference":
            if r.get("status") == "entered-in-error":
                continue
            t = iso(r.get("date") or r.get("created") or r.get("indexed")
                    or (((r.get("context") or {}).get("period")) or {}).get("start"))
            if not t:
                t = next((iso((c.get("attachment") or {}).get("creation")) for c in r.get("content") or []
                          if iso((c.get("attachment") or {}).get("creation"))), None)
            title = r.get("description") or text_of(r.get("type")) or ""
            org = org_of(r)
            # #205: Sanguity's own export says what kind of record a file was shown as (a letter, an imaging report...)
            kind = next((c["code"] for cat in r.get("category") or [] if isinstance(cat, dict) for c in cat.get("coding") or []
                         if isinstance(c, dict) and c.get("system") == KIND and c.get("code") in DOC_KINDS), "document")
            for i, c in enumerate(r.get("content") or [], 1):   # first plain, the others #content/<position>
                document(rkey(r) + (f"#content/{i}" if i > 1 else ""), t, title, c.get("attachment"), r, rel, org, kind)
        elif rt == "FamilyMemberHistory":   # #1134
            if r.get("status") == "entered-in-error":
                continue
            rel = r.get("relationship") or {}
            code = next(iter(codes(rel, common.ROLE_CODE) or codes(rel)), "")
            conds = [{"name": text_of(c.get("code")), **dict(zip(("code", "system"), _code_of(c.get("code"), "http://snomed.info/sct"))),
                      "onset": _age(c.get("onsetAge")) or _age_range(c.get("onsetRange")) or c.get("onsetString") or "",
                      "death": c.get("contributedToDeath") is True, "note": _notes(c.get("note"))}
                     for c in r.get("condition") or [] if isinstance(c, dict)]
            dead = r.get("deceasedBoolean")
            if dead is None and any(k in r for k in ("deceasedAge", "deceasedRange", "deceasedDate", "deceasedString")):
                dead = True
            f = common.family_member(common.relation_word(code, text_of(rel)), conds, name=r.get("name") or "",
                                     deceased=dead if isinstance(dead, bool) else None,
                                     death_age=_age(r.get("deceasedAge")) or _age_range(r.get("deceasedRange")),
                                     note=_notes(r.get("note")), date=(iso(r.get("date")) or "")[:10] or None,
                                     src=org_of(r) or who_of(r), code=code)
            if f["conditions"] or "deceased" in f or f.get("note"):
                family.append({**f, "key": rkey(r)})
        elif rt == "Communication":   # #205: put together into conversations below
            if r.get("status") not in ("entered-in-error", "not-done"):
                comms.append(r)
        elif rt not in IMPORTED and rt not in SUPPORTING:
            skipped[rt] += 1

    # --- conversations (#205): the messages that answer one another (inResponseTo), from the first
    def first_of(r):
        seen = {id(r)}
        while True:
            up = next((x for ref in r.get("inResponseTo") or [] for x in [idx.get(ref, r)]
                       if isinstance(x, dict) and x.get("resourceType") == "Communication"), None)
            if not up or id(up) in seen:
                return r
            seen.add(id(up))
            r = up
    threads = defaultdict(list)
    for r in comms:
        threads[id(first_of(r))].append(r)
    for msgs in threads.values():
        root = first_of(msgs[0])
        rows = []
        for m in msgs:
            sender = idx.get(m.get("sender"), m) if isinstance(m.get("sender"), dict) else None
            who = "You" if (sender or {}).get("resourceType") == "Patient" else idx.name(m.get("sender"), m)
            body = "\n\n".join(x.get("contentString") for x in m.get("payload") or [] if isinstance(x, dict) and x.get("contentString"))
            rows.append({"from": who, "t": iso(m.get("sent") or m.get("received")), "body": body, "att": []})
        rows = sorted((x for x in rows if x["t"]), key=lambda x: x["t"])
        if not rows:
            continue
        kind = next((c["code"] for cat in root.get("category") or [] if isinstance(cat, dict) for c in cat.get("coding") or []
                     if isinstance(c, dict) and c.get("system") == KIND and c.get("code") in MSG_KINDS), "message")
        b.add(rows[0]["t"], kind, text_of(root.get("topic")) or "Message", f"{len(rows)} message{'s' if len(rows) != 1 else ''}",
              {"type": "conversation", "messages": rows}, key=rkey(root))

    # --- claims, after the encounters so a facility bill can be filed under its hospital stay
    from . import _fhir_claims
    costs = _fhir_claims.add_claims([r for r, _ in resources if r["resourceType"] == "ExplanationOfBenefit"],
                                    idx, rkey, b, warnings)

    # --- warnings
    if skipped:
        parts = ", ".join(f"{_plain_kind(k)} ({n})" for k, n in sorted(skipped.items(), key=lambda kv: (-kv[1], kv[0])))
        warnings.append(f"Skipped {sum(skipped.values())} record{'s' if sum(skipped.values()) != 1 else ''} "
                        f"of kinds Sanguity doesn't import yet: {parts}.")
    if inline:   # (#1103: in plain words, not "inline base64")
        warnings.append(f"Skipped {inline} attachment{'s' if inline != 1 else ''} stored inside the records themselves; "
                        "only attachments that come as separate files in the upload are imported for now.")
    if remote:
        warnings.append(f"{remote} document{'s' if remote != 1 else ''} link{'s' if remote == 1 else ''} to a web address "
                        "instead of a file in the upload, so only the entry was imported.")
    if missing:
        warnings.append(f"{missing} document{'s' if missing != 1 else ''} named a file that isn't in the upload, "
                        "so only the entry was imported.")
    if home.cycle:
        warnings.append(f"Left out {home.cycle} cycle record{'s' if home.cycle != 1 else ''}: cycle tracking is turned on for "
                        "each profile, and its records come only from the phone apps.")

    # --- assemble data.json
    series = []
    for name, s in labs.items():
        pts = sorted(s["pts"], key=lambda p: p[0])
        panel = s["panels"].most_common(1)[0][0] if s["panels"] else ""
        series.append({"name": name, "unit": s["unit"], "panel": panel, "n": len(pts),
                       **({"loinc": s["loinc"]} if s["loinc"] else {}),
                       **({"key": f"loinc:{s['loinc']}"} if s["loinc"] else {"key": f"fhir:{s['code']}"} if s["code"] else {}),
                       "pts": pts, "txt": sorted(s["txt"], key=lambda p: p[0])[-50:]})
    # series keys are unique within an import: one test code split by unit gets "|<unit as reported>" on each,
    # and (rarely) the same code and unit under two reported names gets "|<name>" as well
    for extra in (lambda s: s["unit"], lambda s: s["name"].strip().lower()):
        seen = Counter(s["key"] for s in series if "key" in s)
        for s in series:
            if seen.get(s.get("key"), 0) > 1:
                s["key"] += f"|{extra(s)}"
    series.sort(key=lambda s: (-s["n"], s["name"]))
    # (#955) each record of one resource says which version of it this is: its server, its lastUpdated, its bundle's date
    vers = {rkey(r): v for r, _ in resources if (v := _ver(r))}
    for x in [e for e in b.events if e.get("k") not in ("lab", "labitem")] + problems + upcoming + family:
        if isinstance(x.get("key"), str) and (v := vers.get(x["key"].split("#", 1)[0])):
            x["ver"] = v
    # (#970) a lab event says its server alone (its report's, else its rows' Observations'), each row being its own
    # record: another server's result with the same ids is told apart from this one's (preview._same_records)
    for e in b.events:
        if e.get("k") == "labitem":
            d = b.details.get(e["id"]) or {}
            srv = next((v["server"] for k in [e.get("key"), *(d.get("keys") or [])] if isinstance(k, str)
                        and (v := vers.get(k.split("#", 1)[0])) and v.get("server")), None)
            if srv:
                e["ver"] = {"server": srv}
    b.events.sort(key=lambda e: e["t"])
    problems.sort(key=lambda p: (p["date"] or "", p["name"]))
    upcoming.sort(key=lambda u: u["t"])
    data = {"source": "FHIR import",
            "events": b.events, "details": b.details, "labs": series, "upcoming": upcoming, "problems": problems,
            "costs": costs, "outOfPocket": {"payments": []}, "milestones": [], "clinicWeights": sorted(weights)}
    if family:
        data["familyHistory"] = family
    watch = home.summary()
    if watch:
        data["watch"] = watch
    if not (b.events or series or problems or upcoming or weights or watch or family):
        kinds = Counter(r["resourceType"] for r, _ in resources)
        raise ValueError("This FHIR export has no records Sanguity can show yet (it holds "
                         + ", ".join(f"{k} ({n})" for k, n in kinds.most_common()) + ").")
    return {"data": data, "warnings": warnings}


def _find(files, url, rel):
    """The upload path an attachment URL points at: relative to the resource's file, to the upload root, or by name."""
    url = url[len("file://"):] if url.lower().startswith("file://") else url
    url, _, frag = url.partition("#")   # a page in it ("#page=2") stays on the link
    frag = f"#{frag}" if frag else ""
    for u in dict.fromkeys([url, urllib.parse.unquote(url)]):   # a relative URL may be percent-encoded (#205)
        u = u.replace("\\", "/").lstrip("/")
        for cand in (posixpath.normpath(posixpath.join(posixpath.dirname(rel), u)), posixpath.normpath(u)):
            if cand in files:
                return cand + frag
    base = posixpath.basename(urllib.parse.unquote(url))
    hits = [k for k in files if posixpath.basename(k) == base]
    return hits[0] + frag if len(hits) == 1 else None
