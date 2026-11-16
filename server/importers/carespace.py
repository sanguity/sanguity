"""CareSpace / OncoEMR patient-portal export: the reader build.py has had since the start, as an importer.

The export is a folder (often zipped, sometimes inside a folder of its own) laid out like this:

    api/lab_results.json        {"data": [lab result, ...]}: one result per test, with its panel's id
    api/lab_panels.json         {"data": [{"id", "testName"}, ...]}: the panels (orders) the results belong to
    carespace_ccd_full.xml      the full-range C-CDA (preferred), or carespace_ccd.xml (the portal's default window)
    documents/<date>_<type>_<title>.pdf

Lab results become one hidden `labitem` per panel and day under a `lab` day event, and chart series for the tests
lab-aliases.json knows. The C-CDA's narrative tables give visits (future ones become `upcoming`), medications,
problems and vitals; weights go to `clinicWeights`. The output matches build.py's for the same export, apart from
event ids and the keys.

The organisation is the C-CDA's custodian (or its author's organisation). An export that names neither is
labelled DEFAULT_ORG, with a warning asking the person to rename the source. Encounters dated after the C-CDA was
made are upcoming (build.py compares with today; the export's own date keeps the output the same every time).

Keys: "carespace:labitem:<day>|<panel lower-cased>" (results are grouped by day and panel name, so no single
source record owns a labitem), "carespace:labday:<day>", "carespace:document/<file name>", and "carespace:h:" plus a
hash of the identifying fields for visits, upcoming visits, medications and problems, which have no ids in the
export. Series: "loinc:<code>" or "carespace:name|<name lower-cased>", with "|<unit>" when a test is split by unit.
"""
import collections, hashlib, json, posixpath, re
import xml.etree.ElementTree as ET

from .common import canonical_lab, iso, loinc_of, num, unit_key, ymd

NAME = "carespace"
LABEL = "CareSpace / OncoEMR portal export"
DEFAULT_ORG = "CareSpace clinic"

RESULTS, PANELS = "api/lab_results.json", "api/lab_panels.json"
CCDS = ("carespace_ccd_full.xml", "carespace_ccd.xml")   # first found wins
MAX_BYTES = 200 * 1024 * 1024
H = "{urn:hl7-org:v3}"
NS = {"h": "urn:hl7-org:v3"}
DECL = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.I)

FLAGS = {"H": "H", "HH": "H", "L": "L", "LL": "L"}
VITALS = [("Body weight", "Weight", "lb"), ("Body height", "Height", "in"), ("Body temperature", "Temperature", "°F"),
          ("Heart rate", "Heart rate (clinic)", "bpm"), ("Systolic", "Blood pressure, systolic", "mmHg"),
          ("Diastolic", "Blood pressure, diastolic", "mmHg"), ("Oxygen saturation", "SpO2 (clinic)", "%"),
          ("Respiratory rate", "Respiratory rate (clinic)", "/min"), ("Body mass index", "BMI", "kg/m²")]
WEIGHT_UNITS = {"kg": 2.20462, "lb": 1.0, "[lb_av]": 1.0, "lbs": 1.0, "g": 0.00220462, "[oz_av]": 0.0625}
VISIT_RENAME = {"Chart Check, patient results": "Chart review", "CALL OUT TO PATIENT": "Phone call",
                "PCM RN 30min": "Nurse care management", "RN Lab Fax": "Chart review"}
CHART_NOTE = ("No visit took place: the clinic's system logs chart activity, such as reviewing results sent from other "
              "hospitals, as an encounter. Most of these fall within a few days after a lab draw.")
DOC_RE = re.compile(r"(\d{4}-\d{2}-\d{2})_([^_]+)_(.+?)(?:_\d+)?\.pdf$")


def hkey(*fields):
    return NAME + ":h:" + hashlib.sha256("\x1f".join(str(f or "").strip().lower() for f in fields).encode()).hexdigest()[:16]


# ------------------------------------------------------------------ finding the export

def _root(files):
    """The folder inside the upload that holds api/lab_results.json and api/lab_panels.json ("" or "x/"), or None."""
    hits = sorted((p[:-len(RESULTS)] for p in files if p == RESULTS or p.endswith("/" + RESULTS)),
                  key=lambda r: (r.count("/"), r))
    for r in hits:
        if r + PANELS in files:
            return r
    return None


def _head(path, n=4096):
    try:
        with open(path, "rb") as f:
            return f.read(n).decode("utf-8", "replace")
    except OSError:
        return ""


def detect(files):
    """1.0 with the api/ JSON pair and a carespace_ccd*.xml beside it, 0.97 with the pair alone (above the C-CDA
    importer's 0.95, which a CareSpace C-CDA would get), 0 otherwise."""
    root = _root(files)
    if root is None or not _head(files[root + RESULTS]).lstrip().startswith("{"):
        return 0.0
    return 1.0 if any(root + c in files for c in CCDS) else 0.97


# ------------------------------------------------------------------ reading

def _json(files, rel):
    with open(files[rel], "rb") as f:
        raw = f.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError(f"{rel} is larger than {MAX_BYTES // 1024 ** 2} MB, too large for a portal export.")
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise ValueError(f"{rel} isn't readable JSON. The export may be cut short; try downloading it again.") from None
    rows = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"{rel} doesn't look like a CareSpace export: it has no \"data\" list.")
    return [r for r in rows if isinstance(r, dict)]


def _ccd(files, rel):
    with open(files[rel], "rb") as f:
        raw = f.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError(f"{rel} is larger than {MAX_BYTES // 1024 ** 2} MB, too large for a health summary.")
    if DECL.search(raw):
        raise ValueError(f"{rel} contains a DOCTYPE or ENTITY declaration. Health summaries never need one, "
                         "and it can be used to attack the server, so the file was not read.")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise ValueError(f"{rel} is not well-formed XML. The export may be cut short; try downloading it again.") from None
    if root.tag != H + "ClinicalDocument":
        raise ValueError(f"{rel} is XML but not a C-CDA ClinicalDocument.")
    return root


def _text(el):
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def _org(ccd):
    for path in ("h:custodian/h:assignedCustodian/h:representedCustodianOrganization/h:name",
                 "h:author/h:assignedAuthor/h:representedOrganization/h:name"):
        name = _text(ccd.find(path, NS))
        if name:
            return name
    return None


def _table(root, title):
    """The rows of a C-CDA section's narrative table, as {header: cell text}."""
    for sec in root.iter(H + "section"):
        t = sec.find("h:title", NS)
        if t is not None and (t.text or "").strip().upper() == title.upper():
            tb = sec.find("h:text//h:table", NS)
            if tb is None:
                return []
            hdr = [" ".join("".join(th.itertext()).split()) for th in tb.iter(H + "th")]
            rows = []
            for tr in tb.iter(H + "tr"):
                tds = [" ".join("".join(td.itertext()).split()) for td in tr.findall("h:td", NS)]
                if tds:
                    rows.append(dict(zip(hdr, tds)))
            return rows
    return []


def _cda_time(v):
    m = re.match(r"(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?", v or "")
    if not m:
        return None
    y, mo, d, hh, mi = m.groups()
    return f"{y}-{mo}-{d}T{hh or '12'}:{mi or '00'}:00"


# ------------------------------------------------------------------ parse

def parse(files):
    root = _root(files)
    if root is None:
        raise ValueError(f"This isn't a CareSpace export: there's no {RESULTS} with {PANELS} beside it.")
    results, panels = _json(files, root + RESULTS), _json(files, root + PANELS)
    panels = {p.get("id"): p for p in panels}
    ccd_rel = next((root + c for c in CCDS if root + c in files), None)
    ccd = _ccd(files, ccd_rel) if ccd_rel else None
    warnings = []
    org = _org(ccd) if ccd is not None else None
    if not org:
        org = DEFAULT_ORG
        warnings.append(f'The export doesn\'t name the clinic, so its records are labelled "{DEFAULT_ORG}". '
                        "Rename the source to the clinic's name.")
    tag = org   # source tag on lab points and lab days, as the other importers use the organisation's name

    events, details, labs = [], {}, {}
    panel_votes = collections.defaultdict(collections.Counter)
    clinic_w = {}
    keys, dupes = set(), collections.Counter()

    def add(t, kind, title, sub="", detail=None, key=None, **extra):
        if key in keys:
            dupes[kind] += 1
            return None
        keys.add(key)
        e = {"id": len(events) + 1, "t": t, "k": kind, "title": title, "sub": sub}
        e.update({k: v for k, v in extra.items() if v not in (None, "", [])})
        e["key"] = key
        if detail:
            details[e["id"]] = detail
        events.append(e)
        return e["id"]

    # ---- lab results
    by_day = collections.defaultdict(lambda: collections.defaultdict(list))
    first_t = {}
    no_date = no_name = 0
    for r in results:
        v, ref = r.get("value") or {}, r.get("referenceRange") or {}
        if not isinstance(v, dict):
            v = {"value": v}
        t = r.get("observationDateTz") or r.get("observationDate")
        if not t or not ymd(t):
            no_date += 1
            continue
        if not r.get("name"):
            no_name += 1
            continue
        first_t.setdefault(t[:10], t)
        lo, hi = ref.get("lowerLimit"), ref.get("upperLimit")
        x = num(v.get("value"))
        flag = FLAGS.get((r.get("interpretationCode") or "").upper(), "")
        if not flag and x is not None:
            flag = "L" if lo is not None and x < lo else "H" if hi is not None and x > hi else ""
        unit = v.get("unit") or ref.get("unit") or ""
        pan = (panels.get(r.get("labPanelRecordId")) or {}).get("testName") or "Lab panel"
        by_day[t[:10]][pan].append([r["name"], v.get("value"), unit, r.get("referenceRangeDisplay") or "", flag])
        canon = canonical_lab(r["name"])
        if x is None or not canon:
            continue
        name = canon
        if name in labs and unit_key(labs[name]["unit"]) != unit_key(unit):
            name = f"{canon} ({org})"
        s = labs.setdefault(name, {"unit": unit, "pts": [], "base": canon})
        s["pts"].append([iso(t), x, lo, hi, flag, str(v.get("value")), tag])
        panel_votes[name][pan] += 1
    if no_date:
        warnings.append(f"{no_date} lab result{'s' if no_date != 1 else ''} had no date and {'were' if no_date != 1 else 'was'} left out.")
    if no_name:
        warnings.append(f"{no_name} lab result{'s' if no_name != 1 else ''} had no test name and {'were' if no_name != 1 else 'was'} left out.")
    for day, pans in by_day.items():
        ids = []
        for pan, rows in pans.items():
            i = add(iso(first_t[day]), "labitem", pan, org,
                    {"type": "result", "order": pan, "name": pan, "lab": org, "rows": rows, "report": [], "comments": []},
                    key=f"{NAME}:labitem:{day}|{pan.strip().lower()}", abn=sum(1 for r in rows if r[4]) or None, hidden=1)
            if i:
                ids.append(i)
        n = sum(len(r) for r in pans.values())
        add(iso(first_t[day]), "lab", f"{n} lab results · {org}", ", ".join(sorted(pans))[:160], {"type": "labday", "ids": ids},
            key=f"{NAME}:labday:{day}", abn=sum(1 for rows in pans.values() for r in rows if r[4]) or None, src=tag)

    # ---- the C-CDA: weights in its entries, then its narrative tables
    upcoming, problems = [], []
    if ccd is None:
        warnings.append("The export has no carespace_ccd.xml, so only its lab results and documents were read "
                        "(no visits, medications, problems or vitals).")
    else:
        made_el = ccd.find("h:effectiveTime", NS)
        made = ymd(made_el.get("value")) if made_el is not None else None   # no date: every encounter is a past visit
        for obs in ccd.iter(H + "observation"):
            code = obs.find("h:code", NS)
            if code is None or code.get("code") != "29463-7":
                continue
            val, et = obs.find("h:value", NS), obs.find("h:effectiveTime", NS)
            if val is None or et is None:
                continue
            f = WEIGHT_UNITS.get((val.get("unit") or "").strip().lower())
            low = et.find("h:low", NS)
            t = _cda_time(et.get("value") or (low.get("value") if low is not None else None))
            try:
                lb = float(val.get("value")) * f
            except (TypeError, ValueError):
                continue
            if t and f and 50 < lb < 600:
                clinic_w.setdefault(t[:16], [t, round(lb, 1), org])

        procs_by_day = collections.defaultdict(list)
        for r in _table(ccd, "PROCEDURES"):
            d = ymd(r.get("Date and Time (Range)"))
            if d:
                procs_by_day[d].append(r.get("Description", ""))
        up_keys = set()
        for r in _table(ccd, "ENCOUNTERS"):
            d = ymd(r.get("Encounter date"))
            if not d:
                continue
            procs = [p for p in procs_by_day.get(d, []) if p]
            where = r.get("Location") or ""
            main = next((p for p in procs if re.search(r"^(MD |RN Visit|PT )|Eval|Infusion|Inj", p)), "") or (procs[0] if procs else "")
            enc = r.get("Encounter") or ""
            title = ("Hospital visit" if "hospital care" in enc.lower() or enc.startswith("9922") or enc.startswith("9923") else
                     "Physical therapy" if "Occupational Therapy" in where or main.startswith("PT") else
                     "Doctor visit" if main.startswith("MD") else "Nurse visit" if main.startswith("RN") else
                     "Cancellation" if main.startswith("Cancel") else "No-show" if main.startswith("No Show") else
                     main if main else ("Clinic visit" if where else "Chart review"))
            dx = "; ".join(dict.fromkeys(x.strip() for x in (r.get("Diagnosis") or "").split(";") if x.strip()))
            procs = list(dict.fromkeys(procs))
            title = VISIT_RENAME.get(title, title)
            key = hkey("encounter", d, enc, r.get("Performer"), where, dx)
            if made and d > made:
                if key in up_keys:
                    dupes["upcoming"] += 1
                    continue
                up_keys.add(key)
                upcoming.append({"t": d, "title": title, "who": r.get("Performer", ""), "where": where, "dx": dx, "procs": procs, "key": key})
                continue
            add(iso(d), "visit", title, " · ".join(x for x in [r.get("Performer"), org] if x),
                {"type": "visit", "where": where, "who": r.get("Performer", ""), "dx": dx, "procs": procs, "src": org,
                 "note": CHART_NOTE if title == "Chart review" else ""}, key=key)
        for r in _table(ccd, "MEDICATIONS"):
            d = ymd(r.get("Date"))
            if d:
                add(iso(d), "med", r.get("Medications", "Medication"), " · ".join(x for x in [r.get("Instructions"), r.get("Status")] if x),
                    {"type": "med", "instructions": r.get("Instructions"), "status": r.get("Status"), "src": org},
                    key=hkey("med", d, r.get("Medications"), r.get("Instructions"), r.get("Status")))
        prob_keys = set()
        for r in _table(ccd, "PROBLEMS"):
            p = {"name": r.get("Problem"), "type": r.get("Problem Type"), "date": ymd(r.get("Date Observed")),
                 "status": r.get("Status"), "src": org,
                 "key": hkey("problem", r.get("Problem"), r.get("Problem Type"), r.get("Date Observed"), r.get("Status"))}
            if p["key"] in prob_keys:
                dupes["problem"] += 1
                continue
            prob_keys.add(p["key"])
            problems.append(p)
        for r in _table(ccd, "VITAL SIGNS"):
            obs, d = r.get("Observation", ""), ymd(r.get("Date Recorded"))
            m = re.match(r"\s*(-?\d+(?:\.\d+)?)\s*([a-zA-Z%/]*)", r.get("Value", ""))
            hit = next((v for v in VITALS if obs.lower().startswith(v[0].lower())), None)
            if not (d and m and hit):
                continue
            x, u = float(m.group(1)), m.group(2).lower()
            if hit[1] == "Weight" and u == "kg":
                x = round(x * 2.20462, 1)
            if hit[1] == "Weight":   # charted with the scale's weights, not as a series
                clinic_w.setdefault(iso(d)[:16], [iso(d), x, org])
                continue
            if hit[1] == "Height" and u == "cm":
                x = round(x / 2.54, 1)
            s = labs.setdefault(hit[1], {"unit": hit[2], "pts": [], "base": hit[1], "vital": True})
            s["pts"].append([iso(d), x, None, None, "", r.get("Value", ""), tag])
            panel_votes[hit[1]]["Vitals"] += 1

    # ---- documents/<date>_<type>_<title>.pdf
    for rel in sorted(p for p in files if p.startswith(root + "documents/") and "/" not in p[len(root + "documents/"):]):
        name = posixpath.basename(rel)
        mm = DOC_RE.match(name)
        if mm:
            d, typ, title = mm.groups()
            add(iso(d), "document", title, f"{typ} · {org}", {"type": "file", "url": rel, "ext": "pdf"},
                key=f"{NAME}:document/{name}")
    upcoming.sort(key=lambda x: x["t"])

    # ---- lab series: same-day repeats of one value count once, as in build.py
    by_base = collections.Counter(s["base"] for s in labs.values())
    series = []
    for name, s in labs.items():
        seen, pts = set(), []
        for p in s["pts"]:
            k = (p[0][:10], round(p[1], 4))
            if k not in seen:
                seen.add(k)
                pts.append(p)
        pts.sort()
        loinc = loinc_of(name) or loinc_of(s["base"])
        key = (f"loinc:{loinc}" if loinc else f"{NAME}:{'vital' if s.get('vital') else 'name'}|{s['base'].lower()}")
        if by_base[s["base"]] > 1:
            key += f"|{s['unit']}"
        panel = panel_votes[name].most_common(1)[0][0] if panel_votes[name] else ""
        series.append({"name": name, "unit": s["unit"], "panel": panel, "n": len(pts),
                       **({"loinc": loinc_of(name)} if loinc_of(name) else {}), "key": key, "pts": pts, "txt": []})
    series.sort(key=lambda s: (-s["n"], s["name"]))

    if dupes:
        n = sum(dupes.values())
        warnings.append(f"{n} entr{'ies' if n != 1 else 'y'} repeated another exactly and {'were' if n != 1 else 'was'} left out.")
    order = sorted(events, key=lambda e: e["t"])
    return {"data": {"source": f"CareSpace export · {org}", "events": order, "details": details, "labs": series,
                     "upcoming": upcoming, "problems": problems, "costs": [],
                     "clinicWeights": sorted(clinic_w.values())},
            "warnings": warnings}
