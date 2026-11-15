"""FHIR R4 importer: a Bundle, an NDJSON bulk export, or a folder of single resources -> data.json records.

Apple Health clinical records (a folder of one resource per .json file, some still DSTU2) and CMS Blue Button
(Bundles) both arrive in these shapes, as do many patient-portal exports. Standard library only.

Contract (shared with the importer registry, #18):
    NAME, LABEL
    detect(files) -> float 0..1       files: {relative POSIX path in the upload: absolute path on disk}
    parse(files)  -> {"data": <data.json shape>, "warnings": [str]}; ValueError for input we can't read

What becomes what:
    Observation (laboratory)       one `labitem` event (hidden) per report or per day, under a `lab` event per day,
                                   and a point in `labs` [t, value, lo, hi, flag, as reported, source]
    Observation (vital-signs)      a point in `labs` (panel "Vitals"); body weight goes to `clinicWeights` in lb
    DiagnosticReport               groups its lab Observations; without lab results it is a `study`/`imaging` event
                                   with its conclusion; presentedForm attachments become documents
    Condition                      `problems`
    MedicationRequest/Statement    `med` events (DSTU2 MedicationOrder too)
    Encounter                      `visit`; inpatient classes become a `stay` with `end`; planned ones go to `upcoming`
    DocumentReference              `document` events pointing at the attached file inside the upload

Lab names stay as reported, with the LOINC code on the series; mapping to canonical names is a later step (#23).
Inline (base64) attachments are skipped with a warning for now: the upload has no place to write them yet.
"""
import hashlib, json, posixpath, re
from collections import Counter, defaultdict
from datetime import datetime, timezone

NAME = "fhir"
LABEL = "FHIR records (Apple Health, Blue Button, patient portals)"

LOINC = "http://loinc.org"
SOURCE = "FHIR"   # source tag on lab points when the performing organisation is unknown

# resource types that are only looked up through references, never imported on their own
SUPPORTING = {"Patient", "Practitioner", "PractitionerRole", "Organization", "Location", "Medication", "Specimen",
              "Provenance", "RelatedPerson"}
IMPORTED = {"Observation", "DiagnosticReport", "Condition", "MedicationRequest", "MedicationStatement", "MedicationOrder",
            "Encounter", "DocumentReference"}
KNOWN = SUPPORTING | IMPORTED | {
    "Bundle", "AllergyIntolerance", "Immunization", "Procedure", "CarePlan", "CareTeam", "Goal", "Device", "Claim",
    "ExplanationOfBenefit", "Coverage", "ImagingStudy", "MedicationAdministration", "MedicationDispense", "ServiceRequest",
    "QuestionnaireResponse", "FamilyMemberHistory", "Composition", "List", "Appointment", "Communication", "Binary"}

INPATIENT = {"IMP", "ACUTE", "NONAC", "inpatient", "inpatient encounter"}
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
                        r = json.loads(line)
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
                r = json.load(f)
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


def _unbundle(r):
    if r.get("resourceType") != "Bundle":
        return [r]
    out = []
    for e in r.get("entry") or []:
        res = (e or {}).get("resource")
        if isinstance(res, dict) and res.get("resourceType"):
            if e.get("fullUrl"):
                res = dict(res, _fullUrl=e["fullUrl"])
            out += _unbundle(res)
    return out


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


def codes(cc, system=None):
    out = []
    for c in (cc or {}).get("coding") or [] if isinstance(cc, dict) else []:
        if c.get("code") and (system is None or (c.get("system") or "").rstrip("/") == system):
            out.append(c["code"])
    return out


def num(x):
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return x
    try:
        return int(x) if re.fullmatch(r"\s*-?\d+\s*", str(x)) else float(x)
    except (TypeError, ValueError):
        return None


def fmt(v):
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


def _flag(interp, v, lo, hi):
    """data.json flags are "H", "L" or "": take the lab's interpretation, else compare against the range."""
    code = ""
    for c in (interp if isinstance(interp, list) else [interp] if interp else []):
        code = (codes(c) or [text_of(c)])[0] if c else ""
        if code:
            break
    code = (code or "").upper()
    if code in ("H", "HH", "HU", ">", "HIGH", "CRITICAL HIGH", "ABOVE HIGH NORMAL"):
        return "H"
    if code in ("L", "LL", "LU", "<", "LOW", "CRITICAL LOW", "BELOW LOW NORMAL"):
        return "L"
    if isinstance(v, (int, float)):
        if lo is not None and v < lo:
            return "L"
        if hi is not None and v > hi:
            return "H"
    return ""   # "A" (abnormal, no direction) has no slot in data.json's H/L flags


def _range(o):
    rr = (o.get("referenceRange") or [{}])[0] or {}
    lo, hi = num((rr.get("low") or {}).get("value")), num((rr.get("high") or {}).get("value"))
    txt = rr.get("text") or ""
    if not txt:
        txt = (f"{fmt(lo)} - {fmt(hi)}" if lo is not None and hi is not None else f">= {fmt(lo)}" if lo is not None
               else f"<= {fmt(hi)}" if hi is not None else "")
    if lo is None and hi is None and txt:   # only a text range such as "3.5-5.1 mmol/L"
        m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*[-–]\s*(-?\d+(?:\.\d+)?)\s*\S*\s*", txt)
        if m:
            lo, hi = num(m.group(1)), num(m.group(2))
        m = re.fullmatch(r"\s*([<>])=?\s*(-?\d+(?:\.\d+)?)\s*\S*\s*", txt)   # "<130 mg/dL", ">=60"
        if m:
            lo, hi = (num(m.group(2)), None) if m.group(1) == ">" else (None, num(m.group(2)))
    return lo, hi, txt


def _value(o):
    """(numeric value or None, value as reported, unit)."""
    q = o.get("valueQuantity")
    if isinstance(q, dict) and q.get("value") is not None:
        v = num(q["value"])
        unit = q.get("unit") or q.get("code") or ""
        rep = (q.get("comparator") or "") + fmt(q["value"])
        return (None if q.get("comparator") else v), rep, unit
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
    """The server a resource came from: meta.source, else the base of an absolute fullUrl; "" when unknown."""
    src = (r.get("meta") or {}).get("source")
    if isinstance(src, str) and src:
        return src
    m = re.match(r"(https?://.+?)/[A-Za-z]+/[^/]+/?$", r.get("_fullUrl") or "")
    return m.group(1) if m else ""


def _coding_keys(cc):
    return sorted(f"{c.get('system') or ''}|{c['code']}" for c in (cc or {}).get("coding") or [] if c.get("code")) \
        if isinstance(cc, dict) else []


HASH_TIME = ("effectiveDateTime", "effectiveInstant", "issued", "authoredOn", "dateWritten", "dateAsserted",
             "onsetDateTime", "recordedDate", "dateRecorded", "date", "created", "indexed")
HASH_CODE = ("code", "medicationCodeableConcept", "type", "vaccineCode")


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
    fields = [rt, (iso(when) or when or ""), code, _value(r)[1] if rt == "Observation" else ""]
    return "fhir:h:" + hashlib.sha256(json.dumps(fields, separators=(",", ":")).encode()).hexdigest()[:16]


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
    problems, upcoming, weights = [], [], []
    lab_days = defaultdict(list)            # day -> [labitem ids]
    lab_day_meta = defaultdict(lambda: {"t": None, "abn": 0, "orders": set()})
    used_obs = set()
    skipped, inline, remote, missing = Counter(), 0, 0, 0

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
        lo, hi, rtxt = _range(value_src if value_src.get("referenceRange") else o)
        flag = _flag(value_src.get("interpretation") or o.get("interpretation"), v, lo, hi)
        loinc = (codes(code_cc, LOINC) or [None])[0]
        key = name
        s = labs.get(key)
        if s and s["unit"] and unit and s["unit"] != unit:   # same name, different unit: keep the series apart
            key = f"{name} ({unit})"
            s = labs.get(key)
        if s is None:
            s = labs[key] = {"unit": unit, "loinc": loinc, "code": None, "pts": [], "txt": [], "panels": Counter()}
        s["loinc"] = s["loinc"] or loinc
        s["code"] = s["code"] or next(iter(_coding_keys(code_cc)), None)
        s["unit"] = s["unit"] or unit
        s["panels"][panel] += 1
        if v is not None:
            s["pts"].append([t, v, lo, hi, flag, rep, src])
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

    def add_labitem(key, t, order, members, report, prov, status, lab, specimen):
        rows, abn = [], 0
        for o in members:
            src = lab or SOURCE
            rr, a = obs_rows(o, order, src)
            rows += rr
            abn += a
        if not rows:
            return
        det = {"type": "result", "order": order, "name": order, "collected": t[:16].replace("T", " "),
               "resulted": t[:10], "provider": prov, "status": status, "specimen": specimen, "lab": lab, "dx": [],
               "rows": rows, "report": report, "comments": []}
        sub = " · ".join(x for x in [prov, status, lab] if x)
        i = b.add(t, "labitem", order, sub, det, abn=abn, hidden=1, key=key)
        day = t[:10]
        lab_days[day].append(i)
        m = lab_day_meta[day]
        m["t"] = min(m["t"] or t, t)
        m["abn"] += abn
        m["orders"].add(order)

    def document(key, t, title, att, owner, rel, org):
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
            target = None
            # Binary/123 or a full server URL: look for a file of that name in the upload
            base = url.rstrip("/").rsplit("/", 1)[-1]
            target = next((k for k in files if posixpath.basename(k) == base), None)
            if not target:
                remote += 1
                return
        else:
            target = _find(files, url, rel)
            if not target:
                missing += 1
                return
        ct = (att.get("contentType") or "").split(";")[0].strip().lower()
        ext = posixpath.splitext(target)[1].lstrip(".").lower() or EXT.get(ct, "")
        b.add(t, "document", title or att.get("title") or posixpath.basename(target),
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
        for i, att in enumerate(r.get("presentedForm") or []):   # the report's own key is taken by its result event
            document(f"{rkey(r)}#presentedForm/{i}", t, order, att, r, rel, org)

    # --- Observations not in a report
    loose = defaultdict(list)   # (day, org) -> observations
    for r, rel in resources:
        if r["resourceType"] != "Observation" or id(r) in used_obs:
            continue
        if (r.get("status") or "") in ("entered-in-error", "cancelled"):
            continue
        t = _obs_time(r)
        if not t:
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
    for (day, org), obs in sorted(loose.items()):
        obs.sort(key=lambda o: (_obs_time(o), rkey(o)))
        # several Observations share this labitem, so its key names the day (and lab), not one resource
        add_labitem(f"fhir:labitem:{day}" + (f"|{org}" if org else ""), _obs_time(obs[0]), "Lab results", obs, [], who_of(obs[0]),
                    cap(obs[0].get("status") or ""), org, text_of((idx.get(obs[0].get("specimen"), obs[0]) or {}).get("type")))

    # --- one visible lab event per day, linking that day's results
    for day, ids in sorted(lab_days.items()):
        m = lab_day_meta[day]
        orders = sorted(o for o in m["orders"] if o)
        b.add(m["t"], "lab", f"{len(ids)} lab result{'s' if len(ids) != 1 else ''}", ", ".join(orders)[:160],
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
            b.add(t, "med", name, " · ".join(x for x in [instr, status] if x),
                  {"type": "med", "instructions": instr, "status": status, "src": org or who_of(r)}, key=rkey(r))
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
            if r.get("status") == "planned" or (t and t > _now()):
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
            b.add(t, "visit", title, " · ".join(x for x in [who, where, org] if x),
                  {"type": "visit", "who": who, "where": where or org, "dx": dx, "procs": [], "src": org or SOURCE, "note": ""},
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
            for i, c in enumerate(r.get("content") or []):
                document(rkey(r) + (f"#content/{i}" if i else ""), t, title, c.get("attachment"), r, rel, org)
        elif rt not in IMPORTED and rt not in SUPPORTING:
            skipped[rt] += 1

    # --- warnings
    if skipped:
        parts = ", ".join(f"{k} ({n})" for k, n in sorted(skipped.items(), key=lambda kv: (-kv[1], kv[0])))
        warnings.append(f"Skipped {sum(skipped.values())} record{'s' if sum(skipped.values()) != 1 else ''} "
                        f"of kinds Sanguity doesn't import yet: {parts}.")
    if inline:
        warnings.append(f"Skipped {inline} attachment{'s' if inline != 1 else ''} embedded in the records themselves "
                        "(inline base64); only attachments included as separate files are imported for now.")
    if remote:
        warnings.append(f"Skipped {remote} attachment{'s' if remote != 1 else ''} that link to a web address "
                        "instead of a file in the upload.")
    if missing:
        warnings.append(f"{missing} attachment{'s' if missing != 1 else ''} named a file that isn't in the upload.")

    # --- assemble data.json
    series = []
    for name, s in labs.items():
        pts = sorted(s["pts"], key=lambda p: p[0])
        panel = s["panels"].most_common(1)[0][0] if s["panels"] else ""
        series.append({"name": name, "unit": s["unit"], "panel": panel, "n": len(pts),
                       **({"loinc": s["loinc"]} if s["loinc"] else {}),
                       **({"key": f"loinc:{s['loinc']}"} if s["loinc"] else {"key": f"fhir:{s['code']}"} if s["code"] else {}),
                       "pts": pts, "txt": sorted(s["txt"], key=lambda p: p[0])[-50:]})
    series.sort(key=lambda s: (-s["n"], s["name"]))
    b.events.sort(key=lambda e: e["t"])
    problems.sort(key=lambda p: (p["date"] or "", p["name"]))
    upcoming.sort(key=lambda u: u["t"])
    data = {"built": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": "FHIR import",
            "events": b.events, "details": b.details, "labs": series, "upcoming": upcoming, "problems": problems,
            "costs": [], "outOfPocket": {"payments": []}, "milestones": [], "clinicWeights": sorted(weights)}
    if not (b.events or series or problems or upcoming or weights):
        kinds = Counter(r["resourceType"] for r, _ in resources)
        raise ValueError("This FHIR export has no records Sanguity can show yet (it holds "
                         + ", ".join(f"{k} ({n})" for k, n in kinds.most_common()) + ").")
    return {"data": data, "warnings": warnings}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _find(files, url, rel):
    """The upload path an attachment URL points at: relative to the resource's file, to the upload root, or by name."""
    url = url[len("file://"):] if url.lower().startswith("file://") else url
    url = url.replace("\\", "/").lstrip("/")
    for cand in (posixpath.normpath(posixpath.join(posixpath.dirname(rel), url)), posixpath.normpath(url)):
        if cand in files:
            return cand
    base = posixpath.basename(url)
    hits = [k for k in files if posixpath.basename(k) == base]
    return hits[0] if len(hits) == 1 else None
