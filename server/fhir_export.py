"""Export a profile's records as FHIR R4 (#205): one Bundle that other health apps can read, in a zip with the
documents' files beside it. docs/fhir-export.md has the whole mapping; this is the short of it.

The zip:
  bundle.json           a Bundle of type "collection": the Patient, then every record as a resource, then the
                        Practitioners, Organizations, Locations, Devices and Coverages they name
  documents/<id>/...    each document's file (and an HTML record's pictures and stylesheets), which its
                        DocumentReference's attachment points at by a path relative to bundle.json
  letters/<n>.html      a letter written in the records themselves, as the file its DocumentReference points at
  README.txt            what this is, what's in it, and what a FHIR bundle has no place for

What becomes what (US Core's codes and categories where it applies: LOINC for vitals and labs where known, UCUM
units, CVX vaccine codes, the HL7 category and status code systems):
  lab results           a DiagnosticReport (category LAB) per report, its results Observations (category
                        laboratory) with the value as reported, the unit, each result's own reference range (#58)
                        and its H/L flag; a result in a lab series but in no report is an Observation on its own
  vitals at visits      Observations (category vital-signs, the base FHIR vital-signs profiles where the unit is a
                        UCUM unit): blood pressure as one Observation with systolic and diastolic components;
                        clinic weights as body weight
  visits, stays         Encounters (class AMB, IMP); upcoming visits Appointments (booked)
  conditions, allergies Conditions and AllergyIntolerances
  medications           MedicationStatements, each based on a contained MedicationRequest naming the prescriber
                        when the record does; pharmacy fills MedicationDispenses
  immunizations         Immunizations (CVX); procedures Procedures; imaging and other studies DiagnosticReports
                        (an imaging study read from an ImagingStudy, an ImagingStudy again)
  documents, letters    DocumentReferences, the file beside the bundle (never inline base64: files can be large,
                        base64 makes them a third larger, and a reader would have to hold them in the JSON)
  messages              a Communication per message, each answering the one before
  claims                CARIN Blue Button-shaped ExplanationOfBenefits, with a Coverage per insurer and plan
  home readings         Observations with the Patient as performer and the phone's device as Observation.device
                        (R4's performer can't be a Device): blood pressure as one Observation with two components
                        (and the pulse as a third), temperature, blood oxygen, glucose in mg/dL; typed-in ones
                        have no device. The phone's daily measures, CGM days as daily summaries (never each
                        reading), one Observation a day each
  ECGs                  an Observation with the device's classification as it said it and the waveform as
                        SampledData (at most ecg.MAX_SAMPLES samples, so tens of kilobytes each); irregular rhythm
                        notifications an Observation each
  About this person     Patient.birthDate; the sex used for reference ranges as the patient-sexParameterForClinicalUse
                        extension (HL7's Gender Harmony element, carried in R4 by the extensions pack), never as
                        Patient.gender, which is administrative and which Sanguity doesn't hold; heights as body
                        height Observations
  cycle records         only when ticked for this export (#183, as Export everything): Observations

Ids are stable: a uuid5 of the profile and of each record's source key (else its kind, time and title), so the same
record exported again has the same id, and fullUrls are urn:uuid: of them.
Not in it, as FHIR has no place for them: the journal, settings (other than About this person), workouts,
out-of-pocket payments, the activity log and the original uploads; and whatever was left unticked.
Written as it's sent, a resource at a time, like Export everything (export.py).
"""
import datetime, html, json, os, re, urllib.parse, uuid, zipfile

from . import crypto, ecg, export, profiles as prof, settings
from .importers import common

# what can be ticked, in the order the page lists them (www/js/sharing.js asks for them: GET .../export/fhir)
PARTS = [
    ("labs", "Lab results, and vitals taken at visits"),
    ("visits", "Visits, hospital stays and upcoming appointments"),
    ("problems", "Conditions and allergies"),
    ("meds", "Medications and pharmacy fills"),
    ("immunizations", "Immunizations"),
    ("procedures", "Procedures, imaging and other studies"),
    ("documents", "Documents and letters, with their files"),
    ("messages", "Messages"),
    ("claims", "Claims"),
    ("home", "Home readings and the phone's daily measures"),
    ("ecg", "ECGs, with their waveforms"),
    ("person", "About this person: birth date, sex used for reference ranges, heights"),
]
PART_KEYS = [k for k, _ in PARTS]
NOT_MAPPED = ["the journal", "settings (other than About this person)", "workouts", "out-of-pocket payments",
              "the activity log", "the original uploads"]

NS = uuid.UUID("6d1c3f2e-2a5b-5c1f-9a0e-205f4e1b0c3d")   # the namespace of every id this writes (uuid5)
DOC = "https://github.com/sanguity/sanguity/blob/main/docs/fhir-export.md"
MEASURE = DOC + "#measures"         # Sanguity's own codes, for what no standard code says: the phone's daily measures,
MEASURE_GROUP = DOC + "#measure-groups"   # their groups, the kind of record, ECG and cycle record kinds
KIND = DOC + "#record-kinds"
LOINC = "http://loinc.org"
UCUM = "http://unitsofmeasure.org"
RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
CVX = "http://hl7.org/fhir/sid/cvx"
NDC = "http://hl7.org/fhir/sid/ndc"
MDC = "urn:oid:2.16.840.1.113883.6.24"
OBS_CAT = "http://terminology.hl7.org/CodeSystem/observation-category"
INTERP = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"
ACT = "http://terminology.hl7.org/CodeSystem/v3-ActCode"
V2_0074 = "http://terminology.hl7.org/CodeSystem/v2-0074"
DCM = "http://dicom.nema.org/resources/ontology/DCM"
SEX_EXT = "http://hl7.org/fhir/StructureDefinition/patient-sexParameterForClinicalUse"
SEX_CS = "http://terminology.hl7.org/CodeSystem/sex-parameter-for-clinical-use"
ABSENT = "http://hl7.org/fhir/StructureDefinition/data-absent-reason"
C4BB_ADJ = "http://hl7.org/fhir/us/carin-bb/CodeSystem/C4BBAdjudication"
C4BB_ID = "http://hl7.org/fhir/us/carin-bb/CodeSystem/C4BBIdentifierType"
C4BB_INFO = "http://hl7.org/fhir/us/carin-bb/CodeSystem/C4BBSupportingInfoType"
ADJ = "http://terminology.hl7.org/CodeSystem/adjudication"
VITALS = "http://hl7.org/fhir/StructureDefinition/"

# a unit as Sanguity has it -> UCUM; a unit not here goes as it is, with no code
UCUM_OF = {"mmhg": "mm[Hg]", "mm hg": "mm[Hg]", "mm[hg]": "mm[Hg]", "bpm": "/min", "/min": "/min", "breaths/min": "/min",
           "beats/min": "/min", "%": "%", "°c": "Cel", "cel": "Cel", "c": "Cel", "°f": "[degF]", "f": "[degF]", "[degf]": "[degF]",
           "lb": "[lb_av]", "lbs": "[lb_av]", "kg": "kg", "g": "g", "cm": "cm", "in": "[in_i]", "mg/dl": "mg/dL", "g/dl": "g/dL",
           "mmol/l": "mmol/L", "u/l": "U/L", "iu/l": "[IU]/L", "k/ul": "10*3/uL", "x10e3/ul": "10*3/uL", "10*3/ul": "10*3/uL",
           "m/ul": "10*6/uL", "x10e6/ul": "10*6/uL", "10*6/ul": "10*6/uL", "ml/kg/min": "mL/kg/min", "ms": "ms", "steps": "{steps}",
           "h": "h", "hours": "h", "min": "min", "kcal/day": "kcal/d", "readings": "{readings}", "ng/ml": "ng/mL", "mg/l": "mg/L",
           "meq/l": "meq/L", "fl": "fL", "pg": "pg", "ml/min/1.73m2": "mL/min/{1.73_m2}", "µv": "uV", "uv": "uV"}
# LOINC codes of vital signs -> (base FHIR profile, the UCUM units it allows)
VITAL_PROFILE = {"85354-9": ("bp", {"mm[Hg]"}), "8867-4": ("heartrate", {"/min"}), "9279-1": ("resprate", {"/min"}),
                 "8310-5": ("bodytemp", {"Cel", "[degF]"}), "8302-2": ("bodyheight", {"cm", "[in_i]"}),
                 "29463-7": ("bodyweight", {"kg", "g", "[lb_av]"}), "39156-5": ("bmi", {"kg/m2"}), "2708-6": ("oxygensat", {"%"})}
VITAL_LOINC = {"8480-6", "8462-4", "8867-4", "9279-1", "8310-5", "8302-2", "29463-7", "39156-5", "2708-6", "59408-5", "8287-5",
               "9843-4", "3141-9", "8478-0"}
SYSTOLIC, DIASTOLIC, PULSE = "8480-6", "8462-4", "8867-4"
# the phone's daily measures (watch/receiver.py's META and glucose.META): a LOINC code where one says the same
MEASURE_LOINC = {"steps": ("41950-7", "Number of steps in 24 hour Measured"), "hr_rest": ("40443-4", "Heart rate --resting"),
                 "weight": ("29463-7", "Body weight"), "body_fat": ("41982-0", "Percentage of body fat Measured"),
                 "sleep_h": ("93832-4", "Sleep duration"), "resp": ("9279-1", "Respiratory rate"),
                 "glucose_meter": ("41653-7", "Glucose [Mass/volume] in Capillary blood by Glucometer")}
HOME = {  # a list of readings one by one -> (LOINC, its name, the unit as kept, UCUM, profile)
    "temp_readings": ("8310-5", "Body temperature", "°C", "Cel", "bodytemp"),
    "spo2_readings": ("2708-6", "Oxygen saturation in Arterial blood", "%", "%", "oxygensat"),
    "glucose_readings": ("2339-0", "Glucose [Mass/volume] in Blood", "mg/dL", "mg/dL", None),
}
DIAG_STATUS = {"final": "final", "preliminary": "preliminary", "corrected": "corrected", "amended": "amended",
               "cancelled": "cancelled", "partial": "partial", "registered": "registered", "appended": "appended"}
OBS_STATUS = {"final": "final", "preliminary": "preliminary", "corrected": "corrected", "amended": "amended",
              "cancelled": "cancelled", "registered": "registered"}
MED_STATUS = {"active": "active", "completed": "completed", "complete": "completed", "stopped": "stopped",
              "discontinued": "stopped", "on hold": "on-hold", "on-hold": "on-hold", "intended": "intended",
              "not taken": "not-taken", "inactive": "completed", "entered in error": "entered-in-error"}
CONDITION_STATUS = {"active": "active", "recurrence": "recurrence", "relapse": "relapse", "inactive": "inactive",
                    "remission": "remission", "resolved": "resolved"}
ALLERGY_STATUS = {"active": "active", "inactive": "inactive", "resolved": "resolved"}
MODALITY = {"CT": "CT", "MRI": "MR", "X-ray": "DX", "Ultrasound": "US", "Mammogram": "MG", "Nuclear medicine": "NM", "PET": "PT",
            "Angiography": "XA", "Fluoroscopy": "RF", "Bone density": "BMD"}   # the importer's plain words -> DICOM's codes
DOC_KINDS = {"document", "letter", "imaging", "study", "procedure", "visit", "contact", "message"}   # kinds a file may be shown under
MSG_KINDS = {"message", "appt_msg", "auto_msg"}
CTYPE = {"pdf": "application/pdf", "txt": "text/plain", "html": "text/html", "htm": "text/html", "jpg": "image/jpeg",
         "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp", "tif": "image/tiff",
         "tiff": "image/tiff", "xml": "application/xml", "rtf": "application/rtf", "dcm": "application/dicom",
         "zip": "application/zip"}


def parts_of(spec):
    """The parts ticked, from the request's include= (comma-separated keys): every part when it's empty."""
    want = [p for p in (spec or "").split(",") if p]
    bad = [p for p in want if p not in PART_KEYS]
    if bad:
        raise ValueError("Unknown part to include: " + ", ".join(bad) + ".")
    return set(want) if want else set(PART_KEYS)


def plan(db, pid, parts, cycle=False):
    """Everything the export needs, gathered now (the stream runs after the request's database connection closes),
    as export.plan gathers it. parts: the keys of PARTS ticked; cycle: the cycle records too, ticked for this export."""
    base = export.plan(db, pid, cycle=cycle)
    have = settings.stored(db, "profile", pid)
    created = db.execute("SELECT created FROM profiles WHERE id=?", (pid,)).fetchone()["created"]
    return {**base, "parts": set(parts), "created": created,
            "person": {"birthDate": have.get("person.birthDate") or "", "sex": have.get("person.sex") or "",
                       "heights": have.get("person.heights") or []}}


def filename(name, now=None):
    return export.filename(name, now)[:-4] + "-fhir.zip"


# ---------------------------------------------------------------- small pieces

def _zone(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name) if name else None
    except Exception:
        return None


def _offset(naive, zone):
    """"+HH:MM" of naive local time in zone (the server's own when zone is None)."""
    aware = naive.replace(tzinfo=zone) if zone else naive.astimezone()
    off = aware.utcoffset() or datetime.timedelta(0)
    mins = int(off.total_seconds() // 60)
    sign = "-" if mins < 0 else "+"
    return f"{sign}{abs(mins) // 60:02d}:{abs(mins) % 60:02d}"


DT = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(:\d{2}(?:\.\d+)?)?\s*(Z|[+-]\d{2}:?\d{2})?$")


def dt(s, zone, instant=False):
    """A record's time as a FHIR dateTime: a date stays a date; a time without an offset gets the offset of its
    zone on that day (FHIR requires one), so its wall-clock time is kept. instant: always with a time (noon for a
    date). None for what isn't a time."""
    s = str(s or "").strip()
    if re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", s):
        if not instant:
            return s
        if len(s) < 10:
            return None
        s += "T12:00:00"
    m = DT.match(s)
    if not m:
        return None
    base = f"{m[1]}T{m[2]}{m[3] or ':00'}"
    off = m[4]
    if off:
        return base + ("Z" if off == "Z" else off if ":" in off else f"{off[:3]}:{off[3:]}")
    try:
        return base + _offset(datetime.datetime.fromisoformat(base[:19]), zone)
    except ValueError:
        return None


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*", str(v or ""))
    if not m:
        return None
    return float(m[1]) if "." in m[1] else int(m[1])


def _text(s):
    s = " ".join(str(s or "").split())
    return s or None


def cc(text=None, *codings):
    """A CodeableConcept: the codings that have a code, and the text."""
    out = {}
    cs = [c for c in codings if c and c.get("code")]
    if cs:
        out["coding"] = [{k: v for k, v in c.items() if v} for c in cs]
    if _text(text):
        out["text"] = _text(text)
    return out or None


def coding(system, code, display=None):
    return {"system": system, "code": str(code), "display": display} if code not in (None, "") else None


def qty(value, unit):
    """A Quantity: the value, the unit as written and its UCUM code when it has one."""
    q = {"value": value}
    if _text(unit):
        q["unit"] = _text(unit)
        code = UCUM_OF.get(_text(unit).lower())
        if code:
            q.update(system=UCUM, code=code)
    return q


def _clean(x):
    """Drop None, empty strings, lists and dicts, recursively: FHIR allows no empty values."""
    if isinstance(x, dict):
        out = {k: _clean(v) for k, v in x.items()}
        return {k: v for k, v in out.items() if v not in (None, "", [], {})}
    if isinstance(x, list):
        out = [_clean(v) for v in x]
        return [v for v in out if v not in (None, "", [], {})]
    if isinstance(x, str):
        return x.strip() or None
    return x


def _range(text, lo=None, hi=None, unit=None, edge=""):
    """A referenceRange from a result's own range: the numbers when given or readable, and the text as printed. FHIR's
    low and high include their value, so a limit that leaves its edge out (edge, the point's: "<" for a range printed
    "<5", ">" for ">60", #780) is said in the text alone, "<5" where none was printed."""
    text = _text(text)
    if lo is None and hi is None and text:
        m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*[-–]\s*(-?\d+(?:\.\d+)?)\s*\S*\s*", text)
        if m:
            lo, hi = _num(m[1]), _num(m[2])
        m = re.fullmatch(r"\s*([<>])=?\s*(-?\d+(?:\.\d+)?)\s*\S*\s*", text)
        if m:
            lo, hi = (_num(m[2]), None) if m[1] == ">" else (None, _num(m[2]))
        edge = common.range_edge(text, lo, hi)
    edge, r = str(edge or ""), {}
    for k, v, side in (("low", lo, ">"), ("high", hi, "<")):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if side not in edge:
                r[k] = qty(v, unit)
            elif not text:
                text = side + (f"{v:g}" if isinstance(v, float) else str(v))
    if text:
        r["text"] = text
    return [r] if r else []


def _interp(flag):
    flag = str(flag or "").upper()
    names = {"H": "High", "L": "Low", "HH": "Critical high", "LL": "Critical low", "A": "Abnormal"}
    return [cc(None, coding(INTERP, flag, names[flag]))] if flag in names else []


def _value(rep, num=None, unit=None):
    """value[x] for a result as reported: a Quantity for a number (with its comparator: "<0.5"), else a string."""
    rep = str(rep if rep is not None else "").strip()
    m = re.fullmatch(r"(<=|>=|<|>)\s*(-?\d+(?:\.\d+)?)", rep)
    if m:
        return {"valueQuantity": {"comparator": m[1], **qty(_num(m[2]), unit)}}
    n = _num(rep) if rep else None
    if n is None and isinstance(num, (int, float)) and not isinstance(num, bool) and not rep:
        n = num
    if n is not None:
        return {"valueQuantity": qty(n, unit)}
    return {"valueString": rep} if rep else {}


def _div(*paras):
    """A narrative of what the record adds to the resource's elements, a paragraph each ("additional")."""
    body = "".join(f"<p>{html.escape(p)}</p>" for p in paras if _text(p))
    return {"status": "additional", "div": f'<div xmlns="http://www.w3.org/1999/xhtml">{body}</div>'} if body else None


class Bundle:
    """The resources of one export: ids, the shared resources records name, and what was left out."""

    def __init__(self, p, zone):
        self.zone, self.pid = zone, f"{p['pid']}|{p['created']}"
        self.shared, self.used, self.files, self.left = {}, set(), {}, {}
        self.patient = self.rid("Patient", "patient")

    def rid(self, rtype, *key):
        """A stable id: a uuid5 of the profile, the resource type and what names the record (the same record twice,
        with nothing to tell them apart, gets "|2" and so on, in the order the records come)."""
        base = "|".join([self.pid, rtype, *map(str, key)])
        i, n = str(uuid.uuid5(NS, base)), 1
        while i in self.used:
            n += 1
            i = str(uuid.uuid5(NS, f"{base}|{n}"))
        self.used.add(i)
        return i

    def ref(self, rtype, rid, display=None):
        return _clean({"reference": f"urn:uuid:{rid}", "display": display})

    def subject(self):
        return self.ref("Patient", self.patient)

    def party(self, rtype, name, **more):
        """A reference to a Practitioner, Organization or Location by its name, the resource kept to write at the end
        (one per name)."""
        name = _text(name)
        if not name:
            return None
        key = (rtype, name.lower(), json.dumps(more, sort_keys=True))
        if key not in self.shared:
            rid = str(uuid.uuid5(NS, "|".join([self.pid, "party", *key])))
            res = {"resourceType": rtype, "id": rid}
            if rtype == "Practitioner":
                res["name"] = [{"text": name}]
            elif rtype == "Device":   # named for the device itself; "other" for a reading that names only its app
                res["deviceName"] = [{"name": name, "type": "other" if more.get("nameless") else "user-friendly-name"}]
                if more.get("app"):
                    res["property"] = [{"type": cc("Sent through", coding(MEASURE, "app")), "valueCode": [{"text": more["app"]}]}]
                if more.get("version"):
                    res["version"] = [{"value": more["version"]}]
                res["patient"] = self.subject()
            else:
                res["name"] = name
            self.shared[key] = res
        return self.ref(rtype, self.shared[key]["id"], name)

    def leave(self, what, n=1):
        self.left[what] = self.left.get(what, 0) + n

    def keyed(self, e):
        return e.get("key") or f"{e.get('k')}|{e.get('t')}|{e.get('title')}|{e.get('sub')}"

    def t(self, s, instant=False):
        return dt(s, self.zone, instant)


def _obs(b, rtype_key, code, t, value=None, cats=("laboratory",), performer=None, device=None, profile=None, **more):
    o = {"resourceType": "Observation", "id": b.rid("Observation", *rtype_key), "status": more.pop("status", "final"),
         "category": [cc(None, coding(OBS_CAT, c, {"laboratory": "Laboratory", "vital-signs": "Vital Signs", "activity": "Activity",
                                                    "exam": "Exam", "survey": "Survey"}.get(c))) for c in cats if c],
         "code": code, "subject": b.subject()}
    if profile:
        o["meta"] = {"profile": [VITALS + profile]}
    if isinstance(t, dict):
        o["effectivePeriod"] = t
    elif t:
        o["effectiveDateTime"] = t
    if performer:
        o["performer"] = performer if isinstance(performer, list) else [performer]
    o.update(value or {})
    if device:
        o["device"] = device
    o.update(more)
    return _clean(o)


def _vital_profile(loinc, value):
    p = VITAL_PROFILE.get(loinc)
    q = (value or {}).get("valueQuantity") or {}
    return p[0] if p and q.get("code") in p[1] and "comparator" not in q else None


# ---------------------------------------------------------------- the records

def _series_index(data):
    """Each lab series' points by their minute: to find a report's results in them."""
    by = {}
    for s in data.get("labs") or []:
        if not isinstance(s, dict):
            continue
        for i, p in enumerate(s.get("pts") or []):
            if isinstance(p, list) and len(p) >= 2:
                by.setdefault(str(p[0])[:16], []).append((s, i))
    return by


def _named(series, name):
    """Whether a lab series holds results of this name: its own name, the canonical one (lab-aliases.json), or either
    with what the importers add to keep two units or two labs apart ("Glucose, Blood (mmol/L)", "... (Lab name)")."""
    have = str(series.get("name") or "").strip().lower()
    for n in dict.fromkeys([name.lower(), str(common.canonical_lab(name) or "").lower()]):
        if n and (have == n or have.startswith(n + " (")):
            return True
    return False


def _loinc(s, name=None):
    return (s or {}).get("loinc") or common.loinc_of((s or {}).get("name")) or (common.loinc_of(common.canonical_lab(name)) if name else None)


def _edge(p):
    """A point's edge (pts[7], #780): "<" where its range is printed "<5", leaving 5 out, ">" for ">60", else ""."""
    return str(p[7] or "") if isinstance(p, list) and len(p) > 7 else ""


def _lab_obs(b, key, name, loinc, t, rep, num, unit, lo, hi, rtext, flag, vital, performer, status="final", edge=""):
    code = cc(name, coding(LOINC, loinc))
    value = _value(rep, num, unit)
    prof_ = _vital_profile(loinc, value) if vital else None
    return _obs(b, key, code, t, value, ("vital-signs" if vital else "laboratory",), performer=performer, profile=prof_,
                status=OBS_STATUS.get(str(status or "").lower(), "final"), interpretation=_interp(flag),
                referenceRange=_range(rtext, lo, hi, unit, edge))


def _labs(b, data, used):
    """DiagnosticReports for the lab reports, their results as Observations; then every lab series' point that no
    report holds, and the clinic weights."""
    series = _series_index(data)
    details = data.get("details") or {}
    for e in data.get("events") or []:
        d = details.get(str(e.get("id")))
        if e.get("k") not in ("labitem", "lab") or not isinstance(d, dict) or d.get("type") != "result" or not d.get("rows"):
            continue
        t = b.t(e.get("t"))
        if not t:
            continue
        who, lab = b.party("Practitioner", d.get("provider")), b.party("Organization", d.get("lab"))
        results = []
        for n, row in enumerate(d.get("rows") or []):
            if not isinstance(row, list) or not row:
                continue
            row = (row + [""] * 5)[:5]
            name = _text(row[0]) or "Result"
            same = lambda s, p: str(p[5] if len(p) > 5 else "") == str(row[1]) or _num(row[1]) == p[1]
            at = series.get(str(e.get("t"))[:16], [])
            hit = next(((s, i) for s, i in at if (id(s), i) not in used and same(s, s["pts"][i]) and str(s.get("name") or "").strip().lower()
                        == name.lower()), None) or next(((s, i) for s, i in at if (id(s), i) not in used and same(s, s["pts"][i])
                                                          and _named(s, name)), None)
            s, p = (hit[0], hit[0]["pts"][hit[1]]) if hit else (None, None)
            if hit:
                used.add((id(s), hit[1]))
            o = _lab_obs(b, (b.keyed(e), "row", n), name, _loinc(s, name), t, row[1], p[1] if p else None, row[2] or (s or {}).get("unit"),
                         p[2] if p and len(p) > 2 else None, p[3] if p and len(p) > 3 else None, row[3], row[4], False,
                         [x for x in (lab,) if x], d.get("status"), _edge(p))
            results.append(o)
            yield o
        report = [x for x in d.get("report") or [] if isinstance(x, list) and len(x) >= 2 and _text(x[1])]
        yield _clean({"resourceType": "DiagnosticReport", "id": b.rid("DiagnosticReport", b.keyed(e)),
                      "status": DIAG_STATUS.get(str(d.get("status") or "").lower(), "final"),
                      "category": [cc(None, coding(V2_0074, "LAB", "Laboratory"))], "code": cc(d.get("order") or e.get("title") or "Lab results"),
                      "subject": b.subject(), "effectiveDateTime": t, "performer": [x for x in (who, lab) if x],
                      "result": [b.ref("Observation", o["id"], o["code"].get("text")) for o in results],
                      "conclusion": "\n\n".join(_report_text(x) for x in report) or None})
    # every series' point no report holds: clinic vitals, results typed in, a source's results without a report
    for s in data.get("labs") or []:
        if not isinstance(s, dict):
            continue
        name, unit = _text(s.get("name")) or "Result", s.get("unit")
        loinc = _loinc(s, name)
        vital = s.get("panel") == "Vitals" or loinc in VITAL_LOINC
        for i, p in enumerate(s.get("pts") or []):
            if (id(s), i) in used or not isinstance(p, list) or len(p) < 2:
                continue
            if vital and loinc in (SYSTOLIC, DIASTOLIC):
                continue   # paired into one blood pressure Observation below
            t = b.t(p[0])
            if not t:
                continue
            src = p[6] if len(p) > 6 else None
            yield _lab_obs(b, ("series", s.get("key") or name, p[0], p[5] if len(p) > 5 else p[1]), name, loinc, t,
                           p[5] if len(p) > 5 and _text(p[5]) else p[1], p[1], unit, p[2] if len(p) > 2 else None,
                           p[3] if len(p) > 3 else None, None, p[4] if len(p) > 4 else "", vital, _performer(b, src), edge=_edge(p))
    yield from _clinic_bp(b, data, used)
    for w in data.get("clinicWeights") or []:
        if isinstance(w, list) and len(w) >= 2 and isinstance(_num(w[1]), (int, float)) and b.t(w[0]):
            value = {"valueQuantity": qty(_num(w[1]), "lb")}
            yield _obs(b, ("weight", w[0], w[1]), cc("Body weight", coding(LOINC, "29463-7", "Body weight")), b.t(w[0]), value,
                       ("vital-signs",), performer=_performer(b, w[2] if len(w) > 2 else None), profile="bodyweight")


def _performer(b, src):
    """Who measured a result: the organisation its source tag names (none for "Typed in" or a placeholder)."""
    src = _text(src)
    return b.party("Organization", src) if src and src not in ("Typed in", "FHIR", "Demo data") else None


def _clinic_bp(b, data, used):
    """Blood pressure taken at visits: the systolic and diastolic series' points at the same time, one Observation
    with two components (FHIR's blood pressure profile); a point with no partner is a component alone."""
    sides = {}
    for s in data.get("labs") or []:
        if isinstance(s, dict) and _loinc(s, s.get("name")) in (SYSTOLIC, DIASTOLIC):
            for i, p in enumerate(s.get("pts") or []):
                if (id(s), i) not in used and isinstance(p, list) and len(p) >= 2 and b.t(p[0]):
                    sides.setdefault(str(p[0]), {})[_loinc(s, s.get("name"))] = (s, p)
    for t, both in sorted(sides.items()):
        comps, src = [], None
        for loinc in (SYSTOLIC, DIASTOLIC):
            if loinc in both:
                s, p = both[loinc]
                value = _value(p[5] if len(p) > 5 and _text(p[5]) else p[1], p[1], s.get("unit") or "mmHg")
                comps.append(_clean({"code": cc(s.get("name") or ("Systolic blood pressure" if loinc == SYSTOLIC else "Diastolic blood pressure"),
                                                coding(LOINC, loinc)), **value, "interpretation": _interp(p[4] if len(p) > 4 else ""),
                                     "referenceRange": _range(None, p[2] if len(p) > 2 else None, p[3] if len(p) > 3 else None, s.get("unit"), _edge(p))}))
                src = src or (p[6] if len(p) > 6 else None)
        whole = len(comps) == 2 and all((c.get("valueQuantity") or {}).get("code") == "mm[Hg]" for c in comps)
        yield _obs(b, ("bp", t), cc("Blood pressure panel", coding(LOINC, "85354-9", "Blood pressure panel with all children optional")),
                   b.t(t), None, ("vital-signs",), performer=_performer(b, src), profile="bp" if whole else None, component=comps)


def _report_text(part):
    name, text = str(part[0] or ""), str(part[1] or "")
    text = common.strip_html(text).strip() if len(part) > 2 and part[2] else text.strip()
    return text if name.lower() in ("impression", "", "narrative") else f"{name}: {text}"


def _encounters(b, data):
    details = data.get("details") or {}
    for e in data.get("events") or []:
        d = details.get(str(e.get("id"))) or {}
        t = b.t(e.get("t"))
        if not t or e.get("k") not in ("visit", "contact", "stay"):   # chart activity (#236) was an Encounter at its source
            continue
        end = b.t(e.get("end")) if e.get("end") else None
        if e["k"] == "stay":
            org = e.get("title", "").split(" · ", 1)[1] if " · " in e.get("title", "") else None
            end = b.t(d.get("discharge")) if isinstance(d.get("discharge"), str) and d.get("discharge") else end
            yield _clean({"resourceType": "Encounter", "id": b.rid("Encounter", b.keyed(e)), "status": "finished",
                          "class": coding(ACT, "IMP", "inpatient encounter"), "type": [cc("Hospital stay")], "subject": b.subject(),
                          "period": {"start": b.t(d.get("admit")) if isinstance(d.get("admit"), str) and d.get("admit") else t, "end": end},
                          "reasonCode": [cc(e.get("sub"))], "serviceProvider": b.party("Organization", org),
                          "location": [{"location": b.party("Location", d.get("where"))}] if _text(d.get("where")) else []})
            continue
        dx = [x for x in re.split(r";\s*", str(d.get("dx") or "")) if _text(x)]
        yield _clean({"resourceType": "Encounter", "id": b.rid("Encounter", b.keyed(e)), "status": "finished",
                      "class": coding(ACT, "AMB", "ambulatory"), "type": [cc(e.get("title") or "Visit")], "subject": b.subject(),
                      "participant": [{"individual": b.party("Practitioner", d.get("who"))}] if _text(d.get("who")) else [],
                      "period": {"start": t, "end": end if end and end > t else None}, "reasonCode": [cc(x) for x in dx],
                      "location": [{"location": b.party("Location", d.get("where"))}] if _text(d.get("where")) else [],
                      "text": _div(d.get("note"), "Procedures: " + "; ".join(str(x) for x in d.get("procs") or [] if _text(x))
                                   if any(_text(x) for x in d.get("procs") or []) else None)})
    for u in data.get("upcoming") or []:
        start = b.t(u.get("t"), instant=True) if isinstance(u, dict) else None
        if not start:
            continue
        yield _clean({"resourceType": "Appointment", "id": b.rid("Appointment", u.get("key") or f"{u.get('t')}|{u.get('title')}"),
                      "status": "booked", "description": u.get("title") or "Appointment", "start": start,
                      "reasonCode": [cc(x) for x in re.split(r";\s*", str(u.get("dx") or "")) if _text(x)],
                      "comment": "; ".join(str(x) for x in u.get("procs") or [] if _text(x)) or None,
                      "participant": [x for x in ({"actor": b.subject(), "status": "accepted"},
                                                  {"actor": b.party("Practitioner", u.get("who")), "status": "accepted"} if _text(u.get("who")) else None,
                                                  {"actor": b.party("Location", u.get("where")), "status": "accepted"} if _text(u.get("where")) else None) if x]})


def _problems(b, data):
    for p in data.get("problems") or []:
        if not isinstance(p, dict):
            continue
        key = p.get("key") or f"{p.get('type')}|{p.get('name')}|{p.get('date')}"
        status = str(p.get("status") or "").strip()
        date = str(p.get("date") or "")[:10] or None
        if p.get("type") == "Allergy":
            sev = str(p.get("severity") or "").lower()
            crit = {"high": "high", "low": "low", "unable to assess": "unable-to-assess"}.get(str(p.get("criticality") or "").lower())
            system = {"SNOMED CT": "http://snomed.info/sct", "RxNorm": RXNORM}.get(p.get("system"), p.get("system")
                                                                                   if str(p.get("system") or "").startswith(("http", "urn:")) else None)
            yield _clean({"resourceType": "AllergyIntolerance", "id": b.rid("AllergyIntolerance", key),
                          "clinicalStatus": cc(status, coding("http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                                                              ALLERGY_STATUS.get(status.lower()))) if status.lower() in ALLERGY_STATUS else None,
                          "criticality": crit, "code": cc(p.get("substance") or p.get("name"), coding(system, p.get("code")) if system else None),
                          "patient": b.subject(), "onsetDateTime": date,
                          "reaction": [{"manifestation": [cc(r) for r in p.get("reactions") or [] if _text(r)],
                                        "severity": sev if sev in ("mild", "moderate", "severe") else None}] if p.get("reactions") else []})
            continue
        code = CONDITION_STATUS.get(status.lower())
        cat = "health-concern" if p.get("type") == "Health concern" else "problem-list-item" if code else "encounter-diagnosis"
        yield _clean({"resourceType": "Condition", "id": b.rid("Condition", key),
                      "clinicalStatus": cc(status, coding("http://terminology.hl7.org/CodeSystem/condition-clinical", code)) if code else None,
                      "category": [cc(None, coding("http://terminology.hl7.org/CodeSystem/condition-category" if cat != "health-concern"
                                                   else "http://hl7.org/fhir/us/core/CodeSystem/condition-category", cat))],
                      "code": cc(p.get("name") or "Condition"), "subject": b.subject(), "onsetDateTime": date,
                      "abatementString": "Resolved" if code == "resolved" else None})


def _meds(b, data):
    details = data.get("details") or {}
    costs = {c.get("id") for c in data.get("costs") or [] if isinstance(c, dict)}
    for e in data.get("events") or []:
        d = details.get(str(e.get("id")))
        d = d if isinstance(d, dict) else {}
        t = b.t(e.get("t"))
        if not t or e.get("hidden"):
            continue
        if e.get("k") == "med":
            med = cc(e.get("title") or "Medication", coding(RXNORM, d.get("rxnorm")))
            end = b.t(d.get("end")) if d.get("end") else None
            contained, based = [], []
            if _text(d.get("prescriber")):   # the prescription it's from, naming who prescribed it
                contained.append(_clean({"resourceType": "MedicationRequest", "id": "rx", "status": "active" if not end else "completed",
                                         "intent": "order", "medicationCodeableConcept": med, "subject": b.subject(), "authoredOn": t,
                                         "requester": b.party("Practitioner", d.get("prescriber"))}))
                based.append({"reference": "#rx"})
            yield _clean({"resourceType": "MedicationStatement", "id": b.rid("MedicationStatement", b.keyed(e)), "contained": contained,
                          "basedOn": based, "status": MED_STATUS.get(str(d.get("status") or "").lower(), "active" if not end else "completed"),
                          "statusReason": [cc(d.get("status"))] if str(d.get("status") or "").lower() not in MED_STATUS and _text(d.get("status")) else [],
                          "medicationCodeableConcept": med, "subject": b.subject(),
                          "effectivePeriod": {"start": t, "end": end if end and end > t else None},
                          "informationSource": _performer(b, d.get("src")), "dosage": [{"text": d.get("instructions")}] if _text(d.get("instructions")) else []})
        elif e.get("k") == "rx" and d.get("type") != "claim" and e.get("id") not in costs:   # a fill; a claim's fill is its EOB
            supply = _num(d.get("daysSupply"))
            if supply is None:
                m = re.search(r"(\d+)-day supply", e.get("sub") or "")
                supply = int(m[1]) if m else None
            qm = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(.*)", str(d.get("quantity") or ""))
            contained, auth = [], []
            if _text(d.get("prescriber")):
                contained.append(_clean({"resourceType": "MedicationRequest", "id": "rx", "status": "completed", "intent": "order",
                                         "medicationCodeableConcept": cc(e.get("title") or "Medication"), "subject": b.subject(),
                                         "requester": b.party("Practitioner", d.get("prescriber"))}))
                auth.append({"reference": "#rx"})
            pharmacy = d.get("pharmacy") or (e.get("sub", "").split(" · ")[1] if e.get("sub", "").count(" · ") >= 2 else None)
            yield _clean({"resourceType": "MedicationDispense", "id": b.rid("MedicationDispense", b.keyed(e)), "contained": contained,
                          "status": "completed", "medicationCodeableConcept": cc(e.get("title") or "Medication", coding(RXNORM, d.get("rxnorm"))),
                          "subject": b.subject(), "performer": [{"actor": b.party("Organization", pharmacy)}] if _text(pharmacy) else [],
                          "authorizingPrescription": auth, "whenHandedOver": t,
                          "quantity": {"value": _num(qm[1]), "unit": _text(qm[2])} if qm else None,
                          "daysSupply": {"value": supply, "unit": "days", "system": UCUM, "code": "d"} if supply is not None else None,
                          "dosageInstruction": [{"text": d.get("instructions")}] if _text(d.get("instructions")) else []})


def _immunizations(b, data):
    details = data.get("details") or {}
    for e in data.get("events") or []:
        d = details.get(str(e.get("id"))) or {}
        t = b.t(e.get("t"))
        if e.get("k") != "immunization" or not t:
            continue
        system = CVX if d.get("system") == "CVX" else None
        dose = re.fullmatch(r"\s*(\S+)(?:\s+of\s+(\S+))?\s*", str(d.get("dose") or ""))
        am = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(.*)", str(d.get("amount") or ""))
        status = str(d.get("status") or "").lower()
        yield _clean({"resourceType": "Immunization", "id": b.rid("Immunization", b.keyed(e)),
                      "status": {"not done": "not-done", "entered in error": "entered-in-error"}.get(status, "completed"),
                      "vaccineCode": cc(d.get("vaccine") or e.get("title") or "Vaccine", coding(system, d.get("code")) if system else None),
                      "patient": b.subject(), "occurrenceDateTime": t, "lotNumber": d.get("lot"),
                      "manufacturer": b.party("Organization", d.get("manufacturer")), "site": cc(d.get("site")), "route": cc(d.get("route")),
                      "doseQuantity": qty(_num(am[1]), am[2]) if am else None,
                      "performer": [x for x in ({"actor": b.party("Practitioner", d.get("performer"))} if _text(d.get("performer")) else None,
                                                {"actor": b.party("Organization", d.get("where"))} if _text(d.get("where")) else None) if x],
                      "reasonCode": [cc(x) for x in re.split(r";\s*", str(d.get("reason") or "")) if _text(x)],
                      "note": [{"text": d["note"]}] if _text(d.get("note")) else [],
                      "protocolApplied": [{"doseNumberString": dose[1], "seriesDosesString": dose[2]}] if dose else []})


def _procedures(b, data):
    details = data.get("details") or {}
    for e in data.get("events") or []:
        d = details.get(str(e.get("id")))
        d = d if isinstance(d, dict) else {}
        t = b.t(e.get("t"))
        if not t or e.get("k") not in ("procedure", "imaging", "study") or d.get("type") not in ("result", None) or e.get("hidden"):
            continue
        if d.get("type") is None and e.get("k") != "procedure":
            continue
        report = [x for x in d.get("report") or [] if isinstance(x, list) and len(x) >= 2 and _text(x[1])]
        parts = {str(x[0]).lower(): str(x[1]) for x in report}
        who, where = d.get("provider"), d.get("lab")
        if e["k"] == "procedure":
            end = b.t(e.get("end")) if e.get("end") else None
            notes = [_report_text(x) if str(x[0]).lower() != "notes" else str(x[1]) for x in report
                     if str(x[0]).lower() not in ("reason", "body site", "outcome")]
            yield _clean({"resourceType": "Procedure", "id": b.rid("Procedure", b.keyed(e)),
                          "status": "completed", "code": cc(e.get("title") or "Procedure"), "subject": b.subject(),
                          **({"performedPeriod": {"start": t, "end": end}} if end and end > t else {"performedDateTime": t}),
                          "performer": [{"actor": b.party("Practitioner", who)}] if _text(who) else [],
                          "location": b.party("Location", where), "reasonCode": [cc(x) for x in re.split(r";\s*", parts.get("reason", "")) if _text(x)],
                          "bodySite": [cc(parts.get("body site"))] if parts.get("body site") else [], "outcome": cc(parts.get("outcome")),
                          "note": [{"text": n} for n in notes if _text(n)]})
        elif e["k"] == "imaging" and "modality" in parts:   # an imaging study, as it came (an ImagingStudy, #179)
            mods = [m.strip() for m in parts["modality"].split(",") if m.strip()]
            sid = b.rid("ImagingStudy", b.keyed(e))
            # the body site is a series' (with its DICOM UID, here one made from the study's id: 2.25 is UUID-based UIDs')
            series = [{"uid": f"2.25.{uuid.UUID(sid).int}", "modality": coding(DCM, MODALITY.get(mods[0], mods[0]), mods[0]),
                       "bodySite": {"display": site}} for site in [x.strip() for x in parts.get("body site", "").split(",") if x.strip()]] if mods else []
            yield _clean({"resourceType": "ImagingStudy", "id": sid, "status": "available", "series": series,
                          "modality": [coding(DCM, MODALITY.get(m, m), m) for m in mods], "subject": b.subject(), "started": t,
                          "referrer": b.party("Practitioner", who), "location": b.party("Location", where),
                          "numberOfSeries": _num(parts.get("series")), "numberOfInstances": _num(parts.get("images")),
                          "reasonCode": [cc(x) for x in re.split(r";\s*", parts.get("reason", "")) if _text(x)],
                          "description": e.get("title")})
        else:
            if not report:
                b.leave(f"{e['k']} events with no report")
                continue
            imaging = e["k"] == "imaging"
            text = [x for x in report if not (len(x) > 2 and x[2])]
            yield _clean({"resourceType": "DiagnosticReport", "id": b.rid("DiagnosticReport", b.keyed(e)),
                          "status": DIAG_STATUS.get(str(d.get("status") or "").lower(), "final"),
                          "category": [cc(None, coding(V2_0074, "RAD" if imaging else "OTH", "Radiology" if imaging else "Other"))],
                          "code": cc(d.get("order") or e.get("title") or "Report"), "subject": b.subject(), "effectiveDateTime": t,
                          "performer": [x for x in (b.party("Practitioner", who), b.party("Organization", where)) if x],
                          "conclusion": "\n\n".join(_report_text(x) for x in report),
                          "text": None if len(text) == len(report) else _div(*(_report_text(x) for x in report))})


def _documents(b, p, data):
    """A DocumentReference per document (its file beside the bundle) and per letter written in the records."""
    details = data.get("details") or {}
    n = 0
    for e in data.get("events") or []:
        d = details.get(str(e.get("id")))
        if not isinstance(d, dict) or e.get("hidden"):
            continue
        t = b.t(e.get("t"), instant=True)
        if d.get("type") == "letter" and _text(d.get("html")):
            n += 1
            path = f"letters/{n}.html"
            b.files[path] = ("letter", d["html"])
            att = {"contentType": "text/html", "url": path, "title": e.get("title")}
        elif d.get("type") == "file":
            m = export.DOC_URL.match(d.get("url") or "")
            doc = p["docs"].get(m.group(1)) if m else None
            if not doc or not os.path.isfile(doc[0]):
                b.leave("documents whose file is no longer on the server")
                continue
            path = f"documents/{m.group(1)}/{os.path.basename(doc[0])}"
            b.files[path] = ("doc", m.group(1), doc)
            ext = (d.get("ext") or doc[1] or "").lower()
            frag = d["url"].split("#", 1)[1] if "#" in d["url"] else ""   # a page in it (#page=2), as the link had it
            att = {"contentType": CTYPE.get(ext), "url": urllib.parse.quote(path) + (f"#{frag}" if frag else ""), "title": e.get("title")}
        else:
            continue
        kind = e.get("k") if e.get("k") in DOC_KINDS else "document"
        org = (e.get("sub") or "").split(" · ")[-1] if e.get("sub") else None
        yield _clean({"resourceType": "DocumentReference", "id": b.rid("DocumentReference", b.keyed(e)), "status": "current",
                      "type": cc(e.get("title")), "category": [cc(None, coding(KIND, kind, kind.capitalize()))] if kind != "document" else [],
                      "subject": b.subject(), "date": t, "description": e.get("title"),
                      "custodian": b.party("Organization", org) if org and not re.fullmatch(r"[A-Z]{2,5}", org) else None,
                      "content": [{"attachment": att}]})


def _messages(b, data):
    details = data.get("details") or {}
    for e in data.get("events") or []:
        d = details.get(str(e.get("id"))) or {}
        if e.get("k") not in MSG_KINDS or d.get("type") != "conversation":
            continue
        prev = None
        for i, m in enumerate(d.get("messages") or []):
            if not isinstance(m, dict):
                continue
            sent = b.t(m.get("t") or e.get("t"))
            who = _text(m.get("from"))
            sender = b.subject() if who in ("You", None) else {"display": who}   # a care team, as the message names it
            rid = b.rid("Communication", b.keyed(e), i)
            yield _clean({"resourceType": "Communication", "id": rid, "status": "completed",
                          "category": [cc(None, coding(KIND, e["k"], "Message"))], "subject": b.subject(),
                          "topic": cc(d.get("subject") or e.get("title")), "sent": sent, "sender": sender,
                          "inResponseTo": [b.ref("Communication", prev)] if prev else [],
                          "payload": [{"contentString": m.get("body")}] if _text(m.get("body")) else []})
            if any(m.get("att") or []):
                b.leave("attachments to messages")
            prev = rid


def _claims(b, data):
    details = data.get("details") or {}
    rows = {c.get("id"): c for c in data.get("costs") or [] if isinstance(c, dict)}
    coverage = {}
    for e in data.get("events") or []:
        d = details.get(str(e.get("id")))
        if e.get("k") not in ("claim", "rx") or not isinstance(d, dict) or d.get("type") != "claim" or e.get("hidden"):
            continue
        c = rows.get(e.get("id")) or {}
        day = str(c.get("d") or e.get("t") or "")[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            continue
        pharmacy = str(d.get("kind") or c.get("kind") or "").lower() == "pharmacy" or e.get("k") == "rx"
        insurer = d.get("insurer") or c.get("ins") or "Insurance"
        plan = d.get("plan")
        ckey = (insurer, plan)
        if ckey not in coverage:
            cid = b.rid("Coverage", insurer, plan or "")
            coverage[ckey] = cid
            yield _clean({"resourceType": "Coverage", "id": cid, "status": "active", "beneficiary": b.subject(),
                          "payor": [b.party("Organization", insurer)],
                          "class": [{"type": cc(None, coding("http://terminology.hl7.org/CodeSystem/coverage-class", "plan")),
                                     "value": plan, "name": plan}] if _text(plan) else []})
        lines = [x for x in d.get("lines") or [] if isinstance(x, dict)]
        institutional = not pharmacy and any(re.fullmatch(r"\d{5}", str(x.get("code") or "")) for x in lines)

        def adj(code, amount, system=ADJ):
            return {"category": cc(None, coding(system, code)), "amount": {"value": round(float(amount or 0), 2), "currency": "USD"}}
        items = []
        for n, x in enumerate(lines or [{"date": day, "desc": e.get("title"), "billed": d.get("billed")}], 1):
            code = str(x.get("code") or "")
            revenue = re.fullmatch(r"\d{5}", code) is not None
            service = cc(x.get("desc") or e.get("title") or "Service")
            if code and not revenue:   # a procedure code with no system the record names
                service["coding"] = [{"code": code}]
            items.append(_clean({"sequence": n, "revenue": cc(None, coding("https://www.nubc.org/CodeSystem/RevenueCodes", code)) if revenue else None,
                                 "productOrService": service, "servicedDate": str(x.get("date") or day)[:10],
                                 "adjudication": [adj("submitted", x.get("billed"))] +
                                                 [{"category": cc(None, coding(C4BB_ADJ, "denialreason")), "reason": cc(r)} for r in x.get("denied") or [] if _text(r)]}))
        if pharmacy:
            items = [_clean({"sequence": 1, "productOrService": cc(d.get("drug") or e.get("title") or "Prescription", coding(NDC, d.get("ndc"))),
                             "servicedDate": day, "adjudication": [adj("submitted", d.get("billed", c.get("billed")))]})]
        info = []
        if _text(d.get("network")):
            info.append({"sequence": len(info) + 1, "category": cc(None, coding(C4BB_INFO, "benefitpaymentstatus")), "code": cc(d["network"])})
        if pharmacy and _num(d.get("daysSupply")) is not None:
            info.append({"sequence": len(info) + 1, "category": cc(None, coding(C4BB_INFO, "dayssupply")),
                         "valueQuantity": {"value": _num(d["daysSupply"]), "unit": "days", "system": UCUM, "code": "d"}})
        if pharmacy and _text(d.get("rxNumber")):
            info.append({"sequence": len(info) + 1, "category": cc(None, coding(C4BB_INFO, "rxnumber")), "valueString": d["rxNumber"]})
        status = str(d.get("status") or c.get("status") or "").lower()
        billed, paid, owe = (d.get(k, c.get(x)) for k, x in (("billed", "billed"), ("paid", "paid"), ("owe", "owe")))
        end = str((d.get("visit") or {}).get("end") or d.get("end") or "")[:10] if not pharmacy else ""
        prov = d.get("provider") or c.get("prov")
        yield _clean({"resourceType": "ExplanationOfBenefit", "id": b.rid("ExplanationOfBenefit", b.keyed(e)), "status": "active",
                      "type": cc(None, coding("http://terminology.hl7.org/CodeSystem/claim-type",
                                              "pharmacy" if pharmacy else "institutional" if institutional else "professional")),
                      "use": "claim", "patient": b.subject(), "created": day,
                      "billablePeriod": {"start": day, "end": end if end > day else None},
                      "insurer": b.party("Organization", insurer),
                      "provider": b.party("Organization", prov) or {"extension": [{"url": ABSENT, "valueCode": "unknown"}]},
                      "identifier": [{"type": cc(None, coding(C4BB_ID, "uc")), "value": str(d.get("claimId"))}] if _text(d.get("claimId")) else [],
                      "outcome": "queued" if status == "pending" else "complete",
                      "adjudication": [{"category": cc(None, coding(C4BB_ADJ, "denialreason")), "reason": cc(r)} for r in d.get("denied") or [] if _text(r)],
                      "insurance": [{"focal": True, "coverage": b.ref("Coverage", coverage[ckey])}],
                      "supportingInfo": info, "item": items,
                      "total": [adj("submitted", billed), adj("benefit", paid), adj("memberliability", owe, C4BB_ADJ)],
                      "payment": {"amount": {"value": round(float(paid or 0), 2), "currency": "USD"}}})


# ---------------------------------------------------------------- the phone's and the person's

def _device(b, source, app):
    """Observation.device for a reading from the phone (the device that took it, and the app it came through as the
    Device's property "app"); None for one typed in, which has no device."""
    if app in (None, "", "Typed in") and not source or app == "Typed in":
        return None
    return b.party("Device", source or app, app=app, **({} if _text(source) else {"nameless": True}))


def _home(b, watch):
    zone = _zone((watch or {}).get("tz")) or b.zone
    for row in watch.get("bp_readings") or []:
        if not isinstance(row, list) or len(row) < 3 or b.t(row[0]) is None:
            continue
        row = (row + [None] * 6)[:6]
        comps = [{"code": cc("Systolic blood pressure", coding(LOINC, SYSTOLIC)), "valueQuantity": qty(row[1], "mmHg")},
                 {"code": cc("Diastolic blood pressure", coding(LOINC, DIASTOLIC)), "valueQuantity": qty(row[2], "mmHg")}]
        if isinstance(_num(row[3]), (int, float)):
            comps.append({"code": cc("Heart rate", coding(LOINC, PULSE)), "valueQuantity": qty(row[3], "/min")})
        yield _obs(b, ("home", "bp", row[0], row[4], row[5]), cc("Blood pressure panel", coding(LOINC, "85354-9", "Blood pressure panel with all children optional")),
                   dt(row[0], zone), None, ("vital-signs",), performer=b.subject(), device=_device(b, row[4], row[5]), profile="bp", component=comps)
    for key, (loinc, name, unit, code, profile) in HOME.items():
        for row in watch.get(key) or []:
            if not isinstance(row, list) or len(row) < 2 or not isinstance(_num(row[1]), (int, float)) or b.t(row[0]) is None:
                continue
            row = (row + [None] * 5)[:5]
            more = {}
            if key == "glucose_readings" and _text(row[4]):
                more["component"] = [{"code": cc("Relation to a meal", coding(MEASURE, "meal")), "valueCodeableConcept": cc(row[4], coding(MEASURE, row[4]))}]
            codes = [coding(LOINC, loinc, name)] + ([coding(LOINC, "59408-5", "Oxygen saturation in Arterial blood by Pulse oximetry")] if key == "spo2_readings" else [])
            yield _obs(b, ("home", key, row[0], row[2], row[3]), cc(name, *codes), dt(row[0], zone), {"valueQuantity": qty(_num(row[1]), unit)},
                       ("laboratory",) if key == "glucose_readings" else ("vital-signs",), performer=b.subject(),
                       device=_device(b, row[2], row[3]), profile=profile, **more)
    for m in watch.get("metrics") or []:
        if not isinstance(m, dict) or not m.get("key"):
            continue
        k = m["key"]
        loinc = MEASURE_LOINC.get(k)
        code = cc(m.get("name") or k, coding(MEASURE, k, m.get("name")), coding(LOINC, *loinc) if loinc else None)
        group = coding(MEASURE_GROUP, m.get("group"), m.get("group"))
        cat = "laboratory" if k.startswith("glucose") else "activity" if m.get("group") in ("Activity", "Sleep") else "vital-signs"
        # built once a measure and shared by its days (the bulk of a big profile: ten years of a dozen measures), each
        # day's Observation then made as it is, clean already
        cats = _clean([cc(None, coding(OBS_CAT, cat, {"laboratory": "Laboratory", "activity": "Activity", "vital-signs": "Vital Signs"}[cat])),
                       {"coding": [group]} if group else None])
        code, subj, unit = _clean(code), b.subject(), _clean(qty(0, m.get("unit")))
        for pt in m.get("pts") or []:
            # a day's value only when it's a number: a metric whose values are lists (a distribution, #234) has no
            # Observation shape here and stays out
            if not isinstance(pt, list) or len(pt) < 2 or isinstance(pt[1], (list, dict, str)) or not isinstance(_num(pt[1]), (int, float)):
                continue
            t = dt(pt[0], zone)
            if not t:
                continue
            yield {"resourceType": "Observation", "id": b.rid("Observation", "measure", k, pt[0]), "status": "final", "category": cats,
                   "code": code, "subject": subj, "effectiveDateTime": t, "performer": [subj], "valueQuantity": {**unit, "value": _num(pt[1])}}
    for h in watch.get("heights") or []:   # an uploaded Apple Health export's heights (#223)
        if isinstance(h, list) and len(h) >= 2 and isinstance(_num(h[1]), (int, float)) and dt(h[0], zone):
            yield _obs(b, ("phone-height", h[0]), cc("Body height", coding(LOINC, "8302-2", "Body height")), dt(h[0], zone),
                       {"valueQuantity": qty(_num(h[1]), "cm")}, ("vital-signs",), performer=b.subject(),
                       device=_device(b, h[2] if len(h) > 2 else None, "Apple Health"), profile="bodyheight")
    if watch.get("exercise"):
        b.leave("workouts", len(watch["exercise"]))


def _waveform(path):
    try:
        w = ecg.unpack(crypto.read_file(path))   # gzipped JSON, sealed (or a receiver's file from before sealing)
    except (OSError, ValueError, EOFError, TypeError, crypto.KeyError_):
        return None
    return w if isinstance(w, dict) and w.get("samples") else None


def _ecgs(b, p, watch):
    for e in watch.get("ecg") or []:
        if not isinstance(e, dict) or not e.get("start"):
            continue
        start, end = dt(e.get("start"), b.zone), dt(e.get("end"), b.zone) if e.get("end") else None
        device = b.party("Device", e.get("device") or e.get("via") or "Watch", app=e.get("via"), version=e.get("software"),
                         **({} if _text(e.get("device")) else {"nameless": True}))
        if e.get("type") == "irregular_rhythm":
            yield _obs(b, ("ecg", e.get("key") or e["start"]), cc("Irregular rhythm notification", coding(MEASURE, "irregular_rhythm")),
                       {"start": start, "end": end} if end else start, None, ("exam",), performer=b.subject(), device=device)
            continue
        comps = []
        if isinstance(_num(e.get("avg_hr")), (int, float)):
            comps.append({"code": cc("Heart rate, average", coding(LOINC, PULSE)), "valueQuantity": qty(_num(e["avg_hr"]), "/min")})
        name = ecg.file_name(e.get("key"))
        wave = _waveform(p["waves"][name[:-8]]) if name and name[:-8] in (p.get("waves") or {}) else None
        if wave:
            rate = _num(wave.get("rate") or e.get("rate")) or 512
            lead = wave.get("lead") or e.get("lead") or ""
            lead_i = lead.lower() in ("lead i", "i", "applewatchsimilartoleadi", "apple watch similar to lead i")
            comps.append({"code": cc(lead or "ECG voltage", coding(MDC, "131329", "MDC_ECG_ELEC_POTL_I") if lead_i else coding(MDC, "131328", "MDC_ECG_ELEC_POTL")),
                          "valueSampledData": {"origin": {"value": 0, "unit": "µV", "system": UCUM, "code": "uV"},
                                               "period": round(1000 / rate, 6), "dimensions": 1,
                                               "data": " ".join(f"{v:g}" if isinstance(v, float) else str(v) for v in wave["samples"][:ecg.MAX_SAMPLES])}})
        elif e.get("wave"):
            b.leave("ECG waveforms whose samples aren't on the server")
        yield _obs(b, ("ecg", e.get("key") or e["start"]), cc("ECG", coding(MDC, "131328", "MDC_ECG_ELEC_POTL"), coding(MEASURE, "ecg")),
                   {"start": start, "end": end} if end else start, {"valueCodeableConcept": cc(e.get("classification"))} if _text(e.get("classification")) else None,
                   ("exam",), performer=b.subject(), device=device, component=comps,
                   note=[{"text": f"Symptoms: {e['symptoms']}"}] if _text(e.get("symptoms")) else [])


def _person(b, p):
    for h in p["person"]["heights"] or []:
        if isinstance(h, dict) and isinstance(_num(h.get("cm")), (int, float)) and dt(h.get("date"), b.zone):
            yield _obs(b, ("height", h["date"]), cc("Body height", coding(LOINC, "8302-2", "Body height")), dt(h["date"], b.zone),
                       {"valueQuantity": qty(_num(h["cm"]), "cm")}, ("vital-signs",), performer=b.subject(), profile="bodyheight")


def _cycle(b, cycle):
    for s in cycle.get("spans") or []:
        if not isinstance(s, dict) or not dt(s.get("from"), b.zone):
            continue
        dev = _device(b, s.get("source"), s.get("app"))
        yield _obs(b, ("cycle", "span", s.get("from")), cc("Menstrual period" if s.get("logged") else "Days with menstrual flow", coding(MEASURE, "cycle_period")),
                   {"start": dt(s["from"], b.zone), "end": dt(s.get("to"), b.zone)}, None, ("survey",), performer=b.subject(), device=dev)
        for day, v in s.get("flow") or []:
            if dt(day, b.zone):
                yield _obs(b, ("cycle", "flow", day), cc("Menstrual flow", coding(LOINC, "49033-4", "Menstrual flow"), coding(MEASURE, "cycle_flow")),
                           dt(day, b.zone), {"valueCodeableConcept": cc(str(v))}, ("survey",), performer=b.subject(), device=dev)
    for m in cycle.get("marks") or []:
        if isinstance(m, dict) and dt(m.get("day"), b.zone):
            yield _obs(b, ("cycle", "mark", m.get("kind"), m.get("day")), cc(str(m.get("kind") or "Cycle record").replace("_", " ").capitalize(),
                                                                              coding(MEASURE, "cycle_" + str(m.get("kind") or "mark"))),
                       dt(m["day"], b.zone), {"valueString": " · ".join(str(x) for x in (m.get("value"), m.get("detail")) if _text(x))} if m.get("value") or m.get("detail") else None,
                       ("survey",), performer=b.subject(), device=_device(b, m.get("source"), m.get("app")))


def resources(p, b):
    """Every resource of the export, the Patient first and the shared ones last."""
    data, parts, person = p["data"] or {}, p["parts"], p["person"]
    pat = {"resourceType": "Patient", "id": b.patient, "name": [{"text": p["name"]}], "birthDate": None, "extension": []}
    if "person" in parts:
        pat["birthDate"] = person["birthDate"] or None
        if person["sex"] in ("female", "male"):
            pat["extension"].append({"url": SEX_EXT, "extension": [
                {"url": "value", "valueCodeableConcept": cc(f"{person['sex'].capitalize()}: the sex labs base reference ranges on",
                                                            coding(SEX_CS, f"{person['sex']}-typical",
                                                                   f"Apply {person['sex']}-typical setting or reference range"))}]})
    yield _clean(pat)
    used = set()
    if "labs" in parts:
        yield from _labs(b, data, used)
    if "visits" in parts:
        yield from _encounters(b, data)
    if "problems" in parts:
        yield from _problems(b, data)
    if "meds" in parts:
        yield from _meds(b, data)
    if "immunizations" in parts:
        yield from _immunizations(b, data)
    if "procedures" in parts:
        yield from _procedures(b, data)
    if "documents" in parts:
        yield from _documents(b, p, data)
    if "messages" in parts:
        yield from _messages(b, data)
    if "claims" in parts:
        yield from _claims(b, data)
    watch = p.get("watch") or {}
    if "home" in parts:
        yield from _home(b, watch)
    if "ecg" in parts:
        yield from _ecgs(b, p, watch)
    if "person" in parts:
        yield from _person(b, p)
    if p.get("cycle"):
        yield from _cycle(b, p["cycle"])
    if (data.get("outOfPocket") or {}).get("payments"):
        b.leave("out-of-pocket payments", len(data["outOfPocket"]["payments"]))
    if data.get("journal"):
        b.leave("journal entries", len(data["journal"]))
    yield from (_clean(r) for _, r in sorted(b.shared.items(), key=lambda kv: kv[1]["id"]))


def stream(p, exported_by, now=None, zone=None):
    """The zip, a piece at a time (export.stream's way): bundle.json written a resource at a time, then the files
    its DocumentReferences point at, then README.txt."""
    now = now or datetime.datetime.now()   # the exporting person's wall-clock time
    zone = _zone(zone) if isinstance(zone, str) else zone
    stamp = now.timetuple()[:6]
    sink = export._Sink()
    zf = zipfile.ZipFile(sink, "w", zipfile.ZIP_DEFLATED, compresslevel=6)
    b = Bundle(p, zone)
    when = now.replace(microsecond=0).isoformat() + _offset(now.replace(microsecond=0), zone)

    def info(name, compress=True):
        i = zipfile.ZipInfo(name, stamp)
        i.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
        i.external_attr = 0o600 << 16
        return i

    counts, wrote = {}, False
    with zf.open(info("bundle.json"), "w", force_zip64=True) as dst:
        head = {"resourceType": "Bundle", "id": str(uuid.uuid5(NS, f"{b.pid}|bundle|{when}")), "meta": {"lastUpdated": when},
                "type": "collection", "timestamp": when}
        dst.write(json.dumps(head, ensure_ascii=False)[:-1].encode() + b', "entry": [\n')
        pending, size = [], 0   # entries written to the zip about 64 KB at a time, and sent on about 256 KB at a time
        for r in resources(p, b):
            counts[r["resourceType"]] = counts.get(r["resourceType"], 0) + 1
            pending.append(json.dumps({"fullUrl": f"urn:uuid:{r['id']}", "resource": r}, ensure_ascii=False, separators=(",", ":")))
            size += len(pending[-1])
            if size > 1 << 16:
                dst.write(((",\n" if wrote else "") + ",\n".join(pending)).encode())
                wrote, pending, size = True, [], 0
                if len(sink.buf) > 1 << 18:
                    yield sink.take()
        dst.write(((",\n" if wrote and pending else "") + ",\n".join(pending) + "\n]}\n").encode())
    yield sink.take()
    for path, what in sorted(b.files.items()):
        if what[0] == "letter":
            zf.writestr(info(path), what[1].encode("utf-8"))
            yield sink.take()
            continue
        _, doc_id, (src, ext) = what
        with zf.open(info(path, ext not in export.STORED), "w", force_zip64=True) as out:
            for piece in crypto.iter_file(src):
                out.write(piece)
                yield sink.take()
        yield sink.take()
        if ext in prof.HTML_DOCS:
            for rel in export._parts(src):
                with zf.open(info(f"documents/{doc_id}/{rel}", not rel.lower().endswith(export.STORED)), "w", force_zip64=True) as out:
                    for piece in crypto.iter_file(os.path.join(os.path.dirname(src), *rel.split("/"))):
                        out.write(piece)
                        yield sink.take()
                yield sink.take()
    left_out = [label for k, label in PARTS if k not in p["parts"]]
    lines = "\n".join(f"  {k:<26}{n}" for k, n in sorted(counts.items()))
    gone = "\n".join(f"  {what}: {n}" for what, n in sorted(b.left.items()))
    readme = f"""Sanguity FHIR export: {p['name']}
Exported {now:%Y-%m-%d %H:%M} by {exported_by}.

This file holds someone's health records. Keep it somewhere private, ideally encrypted, and delete copies you
no longer need.

bundle.json is a FHIR R4 Bundle (type "collection") that other health apps can read; the documents' files are
beside it, in documents/ and letters/, where each DocumentReference points. docs/fhir-export.md in Sanguity's
source says what each record became. Another Sanguity reads it too, through its FHIR importer (upload this zip).

What it holds:
{lines}

Not in it, as a FHIR bundle has no place for them: {', '.join(NOT_MAPPED)}.
{'Left unticked for this export: ' + '; '.join(left_out) + '.' + chr(10) if left_out else ''}{'Cycle records: ticked in for this export.' + chr(10) if p.get('cycle') else ''}{'Also left out:' + chr(10) + gone + chr(10) if gone else ''}
To keep everything, settings and the journal included, use Export everything's Sanguity zip instead.
"""
    zf.writestr(info("README.txt"), readme.encode())
    zf.close()
    yield sink.take()
