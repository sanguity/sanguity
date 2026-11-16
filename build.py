#!/usr/bin/env python3
"""Build data.json for Sanguity from the record exports listed in a sources file.

Usage: python3 build.py --config sources.json --out www/data.json

sources.json (per install, see sources.example.json) names each export folder, the organisation it
came from and the insurer behind each claims export. Folder names may contain wildcards; the
alphabetically last match is used, so date-stamped export folders pick the newest automatically.
Re-run whenever new exports are added.
"""
import json, os, re, sys, glob, html, zipfile, collections
from datetime import datetime, timezone, timedelta
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # -I leaves the script's folder off the path
from server.importers.common import (iso, strip_html, num, ymd, unit_key, lab_defs, lab_alias, ext_alias,   # noqa: E402
                                     resolve_name as _resolve_name, stay, inferred_stays)


def _arg(flag):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv[:-1] else None


if not _arg("--config") or not _arg("--out"):
    sys.exit("usage: build.py --config sources.json --out data.json")
CONF_FILE, OUT = _arg("--config"), _arg("--out")
with open(CONF_FILE, encoding="utf-8") as f:
    CONF = json.load(f)
# RECORDS_ROOT (set by the Docker setup service) wins, so one sources.json works inside and outside Docker
ROOT = os.environ.get("RECORDS_ROOT") or CONF.get("root") or os.path.dirname(os.path.abspath(CONF_FILE))


def find_dir(pattern):
    """Newest folder matching a pattern (relative to root); None when nothing matches."""
    hits = sorted(p for p in glob.glob(os.path.join(ROOT, pattern)) if os.path.isdir(p))
    return hits[-1] if hits else None


_P = CONF.get("primary") or sys.exit("sources.json needs a \"primary\" Epic MyChart export")
EXP = find_dir(_P["dir"]) or sys.exit(f"no folder matches primary dir {_P['dir']!r} under {ROOT}")
def files_prefix(src, d):   # URL prefix for a source's documents; default: served from /records/<folder>/
    return src.get("files") or f"records/{os.path.basename(d)}/"


FILES = files_prefix(_P, EXP)
PRIMARY_ORG = _P.get("org", "")
# extra Epic MyChart sources: [dir, org name, tag, files prefix]
EPIC_EXTRA = [[d, s.get("org", ""), s.get("tag", ""), files_prefix(s, d)] for s in CONF.get("epic", []) if (d := find_dir(s["dir"]))]


# lab names: canonical series, LOINC codes and other spellings (lab-aliases.json next to this script)
LAB_DEFS, LAB_ALIAS, EXT_ALIAS = lab_defs(), lab_alias(), ext_alias()


def load(p):
    with open(os.path.join(EXP, p), encoding="utf-8") as f:
        return json.load(f)


def exp_file(sub, name):
    """Primary-export file whose name ends with `name`; export tools prefix it with the portal (<portal>_letters.json)."""
    hits = sorted(glob.glob(os.path.join(EXP, sub, "*" + name)))
    return os.path.relpath(hits[0], EXP).replace(os.sep, "/") if hits else None


events, details, labs = [], {}, {}
eid = 0


def add(t, kind, title, sub="", detail=None, end=None, **extra):
    global eid
    if not t:
        return
    eid += 1
    e = {"id": eid, "t": t, "k": kind, "title": title, "sub": sub}
    if end:
        e["end"] = end
    e.update({k: v for k, v in extra.items() if v not in (None, "", [])})
    if detail:
        details[eid] = detail
    events.append(e)
    return eid


# ---------------------------------------------------------------- test results (any Epic MyChart)
KIND = {"LAB": "lab", "IMAGING": "imaging", "PROCEDURE": "procedure", "OTHER": "study",
        1: "lab", 2: "imaging", 3: "study", 4: "procedure", "1": "lab", "2": "imaging", "3": "study", "4": "procedure"}
panel_votes = collections.defaultdict(collections.Counter)




def resolve_name(name, unit, org):
    """Map another Epic system's component name onto an existing series when the units agree."""
    return _resolve_name(name, unit, org, labs)


def epic_results(tr, src=None, org="", infer_stays=True):
    """Add results from an Epic test-results export. src tags lab points (None = the primary export)."""
    group_of = {k: g for g in tr["groups"] for k in g["resultList"]}
    lab_days = collections.defaultdict(lambda: {"n": 0, "abn": 0, "orders": set(), "ids": []})
    admissions, inpatient_ts = collections.defaultdict(list), []
    for key, d in tr["details"].items():
        g = group_of.get(key, {})
        for r in d["results"]:
            m = r.get("orderMetadata") or {}
            t = iso(m.get("prioritizedInstantISO"))
            if not t:
                continue
            kind = KIND.get(m.get("resultType"), "study")
            if g.get("isInpatient"):
                if g.get("formattedEncounterDate"):
                    admissions[g["formattedEncounterDate"]].append(t)
                else:
                    inpatient_ts.append(t)
            comps = r.get("resultComponents") or []
            abn_here, rows = 0, []
            for c in comps:
                ci, cr = c.get("componentInfo") or {}, c.get("componentResultInfo") or {}
                name = ci.get("name") or "?"
                rr = cr.get("referenceRange") or {}
                v = cr.get("numericValue")
                lo, hi = rr.get("low"), rr.get("high")
                flag = ""
                if isinstance(v, (int, float)):
                    if lo is not None and v < lo:
                        flag = "L"
                    elif hi is not None and v > hi:
                        flag = "H"
                if flag:
                    abn_here += 1
                rows.append([name, cr.get("value"), ci.get("units") or "", rr.get("formattedReferenceRange") or "", flag])
                if src:
                    name = resolve_name(name, ci.get("units") or "", org)
                panel_votes[name][d.get("orderName") or r.get("name") or ""] += 1
                s = labs.setdefault(name, {"unit": ci.get("units") or "", "pts": [], "txt": []})
                if isinstance(v, (int, float)):
                    s["pts"].append([t, v, lo, hi, flag, (cr.get("value") or "")] + ([src] if src else []))
                else:
                    s["txt"].append([t, cr.get("value") or ""])
            study = r.get("studyResult") or {}
            report = []
            for part in ("impression", "narrative", "combinedRTFNarrativeImpression"):
                p = study.get(part) or {}
                if p.get("hasContent"):
                    h = p.get("contentAsHtml") or ""
                    if re.search(r"<[a-z][^>]*>", h, re.I):
                        report.append([part, h, 1])
                    else:
                        report.append([part, p.get("contentAsString") or html.unescape(h), 0])
            for a in study.get("addenda") or []:
                report.append(["addendum", json.dumps(a, indent=1), 0])
            comments = [strip_html(json.dumps(pc)) for pc in r.get("providerComments") or []]
            sub = " · ".join(x for x in [m.get("orderProviderName"), m.get("resultStatus"), g.get("contactType"), org] if x)
            det = {"type": "result", "order": d.get("orderName"), "name": r.get("name"),
                   "collected": m.get("collectionTimestampsDisplay"), "resulted": m.get("resultTimestampDisplay"),
                   "provider": m.get("orderProviderName"), "status": m.get("resultStatus"),
                   "specimen": m.get("specimensDisplay"), "lab": (m.get("resultingLab") or {}).get("name"),
                   "dx": m.get("associatedDiagnoses") or [], "rows": rows, "report": report, "comments": comments}
            if kind == "lab" and not report:
                ld = lab_days[t[:10]]
                ld["n"] += 1
                ld["abn"] += abn_here
                ld["orders"].add(d.get("orderName") or "")
                ld["ids"].append(add(t, "labitem", d.get("orderName") or "Lab", sub, det, abn=abn_here, hidden=1))
            else:
                add(t, kind if kind != "lab" else "study", d.get("orderName") or r.get("name") or "Result", sub, det,
                    abn=abn_here or None)
    # one visible "lab draw" event per day, linking to that day's individual results
    for day, ld in lab_days.items():
        first = min((e for e in events if e["id"] in ld["ids"]), key=lambda e: e["t"])
        add(first["t"], "lab", f"{ld['n']} lab result{'s' if ld['n'] != 1 else ''}" + (f" · {org}" if org else ""),
            ", ".join(sorted(o for o in ld["orders"] if o))[:160], {"type": "labday", "ids": ld["ids"]},
            abn=ld["abn"] or None, src=src)
    # hospital stays inferred from lab results, only when the source has no real admit/discharge data
    if not infer_stays:
        return
    for st in inferred_stays(admissions, inpatient_ts, org):
        add(st["t"], "stay", st["title"], st["sub"], st["detail"], end=st["end"])


tr = load(exp_file("test-results", "test_results_raw.json") or sys.exit(f"no test-results/*test_results_raw.json in {EXP}"))
ADM_FILE = os.path.join(EXP, "admissions.json")
epic_results(tr, infer_stays=not os.path.exists(ADM_FILE))
stay_days = set()
# corrections.json next to build.py: only its milestones are read here now. Corrections to events (its older
# "not_hospital_stays") are edits made in the app, applied to every import of this build (server/edits.py); the
# app moved an existing corrections.json's into edits once, at start-up
CORR_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corrections.json")
CORR = json.load(open(CORR_FILE, encoding="utf-8")) if os.path.exists(CORR_FILE) else {}


def add_stay(admit, discharge, where, org, how, exact=None):
    """One hospital stay with real admit/discharge dates (deduplicated on admit day + org)."""
    key = (admit[:10], org)
    if key in stay_days:
        return
    stay_days.add(key)
    st = stay(admit, discharge, where, org, how, exact)
    add(st["t"], "stay", st["title"], st["sub"], st["detail"], end=st["end"])


if os.path.exists(ADM_FILE):
    for x in load("admissions.json")["admissions"]:
        add_stay(x["admit"], x["discharge"], x.get("where", ""), "", "Visit list")

# ------------------------------------------------------------------ messages (any Epic MyChart)
FOLDER = {1: "Conversation", 7: "Appointment", 6: "Automated"}


def epic_messages(msg, long_full=None, org=""):
    users = msg.get("users", {})
    long_full = long_full or {}

    def author(a, extra_users):
        if not a:
            return ""
        for k in ("wprKey", "empKey", "userKey", "key"):
            u = (extra_users.get(a.get(k)) or users.get(a.get(k))) if a.get(k) else None
            if u:
                return u.get("name") or ""
        return a.get("displayName") or ""

    for hid, c in msg["details"].items():
        full = long_full.get(hid)
        msgs = (full or c)["messages"]
        xu = (full or {}).get("users") or c.get("users") or {}
        msgs = sorted(msgs, key=lambda x: x.get("deliveryInstantISO") or "")
        if not msgs:
            continue
        folders = (msg["list"].get(hid) or {}).get("folders") or []
        kind = "appt_msg" if 7 in folders else "auto_msg" if 6 in folders else "message"
        people = sorted({author(x.get("author"), xu) for x in msgs} - {""})
        det = {"type": "conversation", "subject": c.get("subject"),
               "messages": [{"t": x.get("deliveryInstantISO"), "from": author(x.get("author"), xu) or "You",
                             "body": x.get("body") or "",
                             "att": [a.get("name") for a in x.get("attachments") or []]} for x in msgs]}
        add(iso(msgs[0]["deliveryInstantISO"]), kind, (c.get("subject") or "(no subject)").strip(),
            f"{len(msgs)} message{'s' if len(msgs) != 1 else ''}" + (f" · {', '.join(people[:3])}" if people else "") + (f" · {org}" if org else ""),
            det, end=iso(msgs[-1]["deliveryInstantISO"]) if len(msgs) > 1 else None)


_long = exp_file("messages", "messages_long_conversations_full.json")
lf = load(_long) if _long else {}
epic_messages(load(exp_file("messages", "messages.json") or sys.exit(f"no messages/*messages.json in {EXP}")), lf)

# ------------------------------------------------------------------- letters
let = load(exp_file("letters", "letters.json") or sys.exit(f"no letters/*letters.json in {EXP}"))
deps = let.get("departments", {})
for l in let["letters"]:
    body = (l.get("detail") or {}).get("bodyHTML") or ""
    txt = strip_html(body).strip()
    lines = [" ".join(ln.split()) for ln in txt.splitlines()]
    title = next((ln for ln in lines if len(ln) > 25 and not re.match(r"(Dear|Hello|Hi)\b", ln)
                  and not re.match(r"^[A-Z][a-z]+ \d{1,2}, \d{4}$", ln)), "Letter")[:90]
    if re.search(r"authoriz", txt, re.I):
        proc = re.search(r"Procedures?:\s*(.+?)(?:\n|Authorization|$)", txt, re.S)
        auth = re.search(r"Authorization:\s*([^\n]+)", txt)
        what = " ".join(proc.group(1).split())[:70] if proc else ""
        what = re.sub(r"^\S+\s*\(Custom\)\s*-\s*", "", what)
        title = "Insurance authorization" + (f": {what}" if what else "") + (f" ({auth.group(1).strip()})" if auth else "")
    add(iso(l.get("dateISO")), "letter", title, (deps.get(l.get("depId")) or {}).get("name", ""),
        {"type": "letter", "html": body})

# ----------------------------------------------------------------- documents
for path in sorted(glob.glob(os.path.join(EXP, "documents", "*", "*"))):
    rel = os.path.relpath(path, EXP).replace(os.sep, "/")
    base = os.path.basename(path)
    mm = re.match(r"(\d{4}-\d{2}-\d{2})(?:T[\d:Z]+)?_(.+)\.(\w+)$", base)
    if not mm:
        continue
    date, name, ext = mm.groups()
    name = re.sub(r"_\d+$", "", name)
    add(iso(date), "document", name, ext.upper(), {"type": "file", "url": FILES + rel, "ext": ext.lower()})

# ------------------------------------------------- visits (C-CDA, when present)
NS = {"h": "urn:hl7-org:v3"}


def cda_time(v):
    if not v:
        return None
    m = re.match(r"(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?", v)
    if not m:
        return None
    y, mo, d, hh, mi = m.groups()
    return f"{y}-{mo}-{d}T{hh or '12'}:{mi or '00'}:00"


CLINIC_W = {}  # (kind, minute) -> [iso time, value, org]
WEIGHT_UNITS = {"kg": 2.20462, "lb": 1.0, "[lb_av]": 1.0, "lbs": 1.0, "g": 0.00220462, "[oz_av]": 0.0625}


def vitals_from_root(root, org):
    for obs in root.iter("{urn:hl7-org:v3}observation"):
        code = obs.find("h:code", NS)
        if code is None or code.get("code") != "29463-7":
            continue
        val, et = obs.find("h:value", NS), obs.find("h:effectiveTime", NS)
        if val is None or et is None:
            continue
        f = WEIGHT_UNITS.get((val.get("unit") or "").strip().lower())
        t = cda_time(et.get("value") or (et.find("h:low", NS).get("value") if et.find("h:low", NS) is not None else None))
        try:
            lb = float(val.get("value")) * f
        except (TypeError, ValueError):
            continue
        if t and f and 50 < lb < 600:
            CLINIC_W.setdefault(("weight", t[:16]), [t, round(lb, 1), org or PRIMARY_ORG])


def visits_from_xml(xml_bytes, src, org=""):
    out = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return out
    vitals_from_root(root, org)
    ee = root.find("h:componentOf/h:encompassingEncounter", NS)
    if ee is not None:
        lo = ee.find("h:effectiveTime/h:low", NS)
        hi = ee.find("h:effectiveTime/h:high", NS)
        code = ee.find("h:code", NS)
        loc = ee.find(".//h:healthCareFacility//h:name", NS)
        prov = ee.find(".//h:encounterParticipant//h:name", NS)
        t = cda_time(lo.get("value") if lo is not None else (ee.find("h:effectiveTime", NS).get("value") if ee.find("h:effectiveTime", NS) is not None else None))
        if t:
            out.append({"t": t, "end": cda_time(hi.get("value")) if hi is not None else None,
                        "type": (code.get("displayName") if code is not None else None) or "Visit",
                        "where": "".join(loc.itertext()).strip() if loc is not None else "",
                        "who": " ".join(t.strip() for t in prov.itertext() if t.strip()) if prov is not None else "",
                        "src": src})
    return out


visits = []
for root_dir, org in [(EXP, "")] + [(e[0], e[1]) for e in EPIC_EXTRA]:
    vis_dir = os.path.join(root_dir, "visit-records") if root_dir else None
    if not (vis_dir and os.path.isdir(vis_dir)):
        continue
    found = []
    for p in glob.glob(os.path.join(vis_dir, "**", "*"), recursive=True):
        if p.lower().endswith(".xml"):
            with open(p, "rb") as f:
                found += visits_from_xml(f.read(), os.path.relpath(p, root_dir).replace(os.sep, "/"), org)
        elif p.lower().endswith(".zip"):
            with zipfile.ZipFile(p) as z:
                for n in z.namelist():
                    if n.lower().endswith(".xml"):
                        found += visits_from_xml(z.read(n), os.path.relpath(p, root_dir).replace(os.sep, "/") + "!" + n, org)
    for v in found:
        v["org"] = org
    visits += found
seen = set()
for v in sorted(visits, key=lambda v: v["t"]):
    k = (v["t"], v["type"])
    if k in seen:
        continue
    seen.add(k)
    who = "" if re.search(r"not in system|^him provider|lab rm\d*|^ach |unknown", v["who"] or "", re.I) else v["who"]
    vtype = {"Ambulatory": "Outpatient visit", "Inpatient Encounter": "Hospital admission", "Emergency": "Emergency visit"}.get(v["type"], v["type"])
    if vtype == "Hospital admission":
        end = v.get("end") or v["t"]
        add_stay(v["t"], end, v["where"], v.get("org", ""), "C-CDA encounter", exact=(v["t"], end if end > v["t"] else v["t"][:10] + "T23:59:00"))
    add(v["t"], "visit", vtype, " · ".join(x for x in [who, v["where"], v.get("org")] if x),
        {"type": "visit", **v}, end=v.get("end") if v.get("end") and v["end"] > v["t"] else None)

# ------------------------------------------------------------ other Epic MyChart portals
for src_dir, org, tag, prefix in EPIC_EXTRA:
    if not os.path.isdir(src_dir):
        continue
    def src_load(p):
        with open(os.path.join(src_dir, p), encoding="utf-8") as f:
            return json.load(f)
    if os.path.exists(os.path.join(src_dir, "test-results/test_results_raw.json")):
        epic_results(src_load("test-results/test_results_raw.json"), src=tag, org=org,
                     infer_stays=not os.path.isdir(os.path.join(src_dir, "visit-records")))
    if os.path.exists(os.path.join(src_dir, "messages/messages.json")):
        epic_messages(src_load("messages/messages.json"), org=org)
    for path in sorted(glob.glob(os.path.join(src_dir, "documents", "*.*"))):
        base = os.path.basename(path)
        mm = re.match(r"(\d{4}-\d{2}-\d{2})_(.+?)(?:_\d+)?\.(\w+)$", base)
        if not mm or mm.group(3).lower() == "json":
            continue
        date, name, ext = mm.groups()
        add(iso(date), "document", name, f"{ext.upper()} · {org}", {"type": "file", "url": prefix + "documents/" + base, "ext": ext.lower()})

# ------------------------------------------- CareSpace / OncoEMR patient-portal export (optional)
_CS = CONF.get("carespace") or {}
CS = find_dir(_CS["dir"]) if _CS.get("dir") else None
CS_FILES = files_prefix(_CS, CS) if CS else ""
CS_NAME = _CS.get("org", "CareSpace")
CS_TAG = _CS.get("tag", "CS")
upcoming, problems = [], []


def cs_table(root, title):
    NS = {"h": "urn:hl7-org:v3"}
    for sec in root.iter("{urn:hl7-org:v3}section"):
        t = sec.find("h:title", NS)
        if t is not None and (t.text or "").strip().upper() == title.upper():
            tb = sec.find("h:text//h:table", NS)
            if tb is None:
                return []
            hdr = [" ".join("".join(th.itertext()).split()) for th in tb.iter("{urn:hl7-org:v3}th")]
            rows = []
            for tr in tb.iter("{urn:hl7-org:v3}tr"):
                tds = [" ".join("".join(td.itertext()).split()) for td in tr.findall("h:td", NS)]
                if tds:
                    rows.append(dict(zip(hdr, tds)))
            return rows
    return []


if CS and os.path.isdir(CS):
    today = datetime.now().strftime("%Y-%m-%d")
    lr = json.load(open(os.path.join(CS, "api/lab_results.json"), encoding="utf-8"))["data"]
    panels = {p["id"]: p for p in json.load(open(os.path.join(CS, "api/lab_panels.json"), encoding="utf-8"))["data"]}
    by_day = collections.defaultdict(lambda: collections.defaultdict(list))
    first_t = {}
    for r in lr:
        v, ref = r.get("value") or {}, r.get("referenceRange") or {}
        t = r.get("observationDateTz") or r.get("observationDate")
        if not t:
            continue
        first_t.setdefault(t[:10], t)
        lo, hi = ref.get("lowerLimit"), ref.get("upperLimit")
        x = num(v.get("value"))
        flag = {"H": "H", "HH": "H", "L": "L", "LL": "L"}.get((r.get("interpretationCode") or "").upper(), "")
        if not flag and x is not None:
            flag = "L" if lo is not None and x < lo else "H" if hi is not None and x > hi else ""
        unit = v.get("unit") or ref.get("unit") or ""
        pan = panels.get(r.get("labPanelRecordId"), {}).get("testName") or "Lab panel"
        by_day[t[:10]][pan].append([r["name"], v.get("value"), unit, r.get("referenceRangeDisplay") or "", flag])
        if x is None or r["name"].lower() not in LAB_ALIAS:
            continue
        name = LAB_ALIAS[r["name"].lower()]
        if name in labs and unit_key(labs[name]["unit"]) != unit_key(unit):
            name = f"{name} ({CS_NAME})"
        s = labs.setdefault(name, {"unit": unit, "pts": [], "txt": []})
        s["pts"].append([iso(t), x, lo, hi, flag, str(v.get("value")), CS_TAG])
        panel_votes[name][pan] += 1
    for day, pans in by_day.items():
        ids = []
        for pan, rows in pans.items():
            ids.append(add(iso(first_t[day]), "labitem", pan, CS_NAME,
                           {"type": "result", "order": pan, "name": pan, "lab": CS_NAME, "rows": rows, "report": [], "comments": []},
                           abn=sum(1 for r in rows if r[4]) or None, hidden=1))
        n = sum(len(r) for r in pans.values())
        add(iso(first_t[day]), "lab", f"{n} lab results · {CS_NAME}", ", ".join(sorted(pans))[:160], {"type": "labday", "ids": ids},
            abn=sum(1 for rows in pans.values() for r in rows if r[4]) or None, src=CS_TAG)
    # full-range C-CDA (preferred) or default window
    ccd = os.path.join(CS, "carespace_ccd_full.xml")
    if not os.path.exists(ccd):
        ccd = os.path.join(CS, "carespace_ccd.xml")
    root = ET.parse(ccd).getroot()
    vitals_from_root(root, CS_NAME)
    procs_by_day = collections.defaultdict(list)
    for r in cs_table(root, "PROCEDURES"):
        d = ymd(r.get("Date and Time (Range)"))
        if d:
            procs_by_day[d].append(r.get("Description", ""))
    for r in cs_table(root, "ENCOUNTERS"):
        d = ymd(r.get("Encounter date"))
        if not d:
            continue
        procs = [p for p in procs_by_day.get(d, []) if p]
        where = r.get("Location") or ""
        key = next((p for p in procs if re.search(r"^(MD |RN Visit|PT )|Eval|Infusion|Inj", p)), "") or (procs[0] if procs else "")
        enc, who = r.get("Encounter") or "", r.get("Performer") or ""
        title = ("Hospital visit" if "hospital care" in enc.lower() or enc.startswith("9922") or enc.startswith("9923") else
                 "Physical therapy" if "Occupational Therapy" in where or key.startswith("PT") else
                 "Doctor visit" if key.startswith("MD") else "Nurse visit" if key.startswith("RN") else
                 "Cancellation" if key.startswith("Cancel") else "No-show" if key.startswith("No Show") else
                 key if key else ("Clinic visit" if where else "Chart review"))
        dx = "; ".join(dict.fromkeys(x.strip() for x in (r.get("Diagnosis") or "").split(";") if x.strip()))
        procs = list(dict.fromkeys(procs))
        title = {"Chart Check, patient results": "Chart review", "CALL OUT TO PATIENT": "Phone call",
                 "PCM RN 30min": "Nurse care management", "RN Lab Fax": "Chart review"}.get(title, title)
        item = {"t": d, "title": title, "who": r.get("Performer", ""), "where": where, "dx": dx, "procs": procs}
        if d > today:
            upcoming.append(item)
            continue
        note = ("No visit took place: the clinic's system logs chart activity, such as reviewing results sent from other "
                "hospitals, as an encounter. Most of these fall within a few days after a lab draw.") if title == "Chart review" else ""
        add(iso(d), "visit", title, " · ".join(x for x in [r.get("Performer"), CS_NAME] if x),
            {"type": "visit", "where": where, "who": r.get("Performer", ""), "dx": dx, "procs": procs, "src": CS_NAME, "note": note})
    for r in cs_table(root, "MEDICATIONS"):
        d = ymd(r.get("Date"))
        if d:
            add(iso(d), "med", r.get("Medications", "Medication"), " · ".join(x for x in [r.get("Instructions"), r.get("Status")] if x),
                {"type": "med", "instructions": r.get("Instructions"), "status": r.get("Status"), "src": CS_NAME})
    for r in cs_table(root, "PROBLEMS"):
        problems.append({"name": r.get("Problem"), "type": r.get("Problem Type"), "date": ymd(r.get("Date Observed")),
                         "status": r.get("Status"), "src": CS_NAME})
    VITALS = [("Body weight", "Weight", "lb"), ("Body height", "Height", "in"), ("Body temperature", "Temperature", "°F"),
              ("Heart rate", "Heart rate (clinic)", "bpm"), ("Systolic", "Blood pressure, systolic", "mmHg"),
              ("Diastolic", "Blood pressure, diastolic", "mmHg"), ("Oxygen saturation", "SpO2 (clinic)", "%"),
              ("Respiratory rate", "Respiratory rate (clinic)", "/min"), ("Body mass index", "BMI", "kg/m²")]
    for r in cs_table(root, "VITAL SIGNS"):
        obs, d = r.get("Observation", ""), ymd(r.get("Date Recorded"))
        m = re.match(r"\s*(-?\d+(?:\.\d+)?)\s*([a-zA-Z%/]*)", r.get("Value", ""))
        hit = next((v for v in VITALS if obs.lower().startswith(v[0].lower())), None)
        if not (d and m and hit):
            continue
        x, u = float(m.group(1)), m.group(2).lower()
        if hit[1] == "Weight" and u == "kg":
            x = round(x * 2.20462, 1)
        if hit[1] == "Weight":  # goes into the combined scale + clinic weight chart instead of a lab series
            CLINIC_W.setdefault(("weight", iso(d)[:16]), [iso(d), x, CS_NAME])
            continue
        if hit[1] == "Height" and u == "cm":
            x = round(x / 2.54, 1)
        s = labs.setdefault(hit[1], {"unit": hit[2], "pts": [], "txt": []})
        s["pts"].append([iso(d), x, None, None, "", r.get("Value", ""), CS_TAG])
        panel_votes[hit[1]]["Vitals"] += 1
    # documents (PDFs) saved under documents/
    for path in sorted(glob.glob(os.path.join(CS, "documents", "*.pdf"))):
        name = os.path.basename(path)
        mm = re.match(r"(\d{4}-\d{2}-\d{2})_([^_]+)_(.+?)(?:_\d+)?\.pdf$", name)
        if mm:
            d, typ, title = mm.groups()
            add(iso(d), "document", title, f"{typ} · {CS_NAME}", {"type": "file", "url": CS_FILES + "documents/" + name, "ext": "pdf"})
    upcoming.sort(key=lambda x: x["t"])

# primary export "(External)" series fold into their canonical series (same units only)
for ext, canon in list(EXT_ALIAS.items()) + [(n, n[:-len(" (External)")]) for n in list(labs) if n.endswith(" (External)") and n not in EXT_ALIAS]:
    if ext in labs and canon in labs and (not labs[ext]["unit"] or not labs[canon]["unit"] or unit_key(labs[ext]["unit"]) == unit_key(labs[canon]["unit"])):
        for p in labs[ext]["pts"]:
            labs[canon]["pts"].append(p[:6] + ["EXT"])
        panel_votes[canon].update(panel_votes[ext])
        del labs[ext]
# drop same-day duplicates across sources (the primary export's own result wins: it sorts first)
for s in labs.values():
    seen, keep = set(), []
    for p in sorted(s["pts"], key=lambda p: (p[0][:10], p[6] if len(p) > 6 else "")):
        k = (p[0][:10], round(p[1], 4))
        if k not in seen:
            seen.add(k)
            keep.append(p)
    s["pts"] = keep

# ----------------------------------------------------------------- lab series
series = []
for name, s in labs.items():
    pts = sorted(s["pts"])
    panel = panel_votes[name].most_common(1)[0][0] if panel_votes[name] else ""
    series.append({"name": name, "unit": s["unit"], "panel": panel, "n": len(pts), **({"loinc": LAB_DEFS[name]["loinc"]} if "loinc" in LAB_DEFS.get(name, {}) else {}),
                   "pts": pts, "txt": sorted(s["txt"])[-50:]})
series.sort(key=lambda s: (-s["n"], s["name"]))

# ------------------------------------------------------------ insurance claims (sources.json "claims": format uhc | bcbs)
CLAIM_SRC = [[c["format"], d, c.get("insurer") or c["format"]] for c in CONF.get("claims", []) if (d := find_dir(c["dir"]))]
costs = []  # one row per claim, used by the Costs tab and the Overview card


def money(x):
    try:
        return round(float((x or {}).get("value") if isinstance(x, dict) else x or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def code_category(code, desc):
    """Category for one billed line, from its CPT/HCPCS/revenue code (first 5 chars) and description."""
    c, t = (code or "").upper()[:5], (desc or "").lower()
    if "stem cell" in t or c.startswith("0081"):
        return "Stem cell transplant"
    if c == "00891" or "cell therapy" in t or c.startswith(("Q2041", "Q2042", "Q2053", "Q2054", "Q2055", "Q2056")):
        return "CAR-T cell therapy"
    if c.startswith(("963", "964", "965", "J")) or re.search(r"chemo|infusion|injection|transfus|immunotherap|pharmacy charges", t) or c in ("00636", "00250", "00260", "00335"):
        return "Chemo, infusions & drugs"
    if c.startswith("77") or "radiation" in t:
        return "Radiation therapy"
    if c == "OPS" or "outpatient services" in t:
        return "Hospital outpatient (facility)"
    if re.match(r"9922\d|9923\d|9921[89]|9947\d|9929\d", c) or re.search(r"room and board|inpatient|hospital care|intensive care|emergency|observation", t):
        return "Hospital physician care"
    if re.match(r"9920\d|9921[1-5]|9924\d|G2211|9944\d|9942\d", c) or re.search(r"office (o/p|visit)|consult|telehealth|telephone", t):
        return "Doctor visits"
    if c.startswith("97") or re.search(r"therapeutic exercise|physical therap|exercise therapy|gait|neuromuscular", t):
        return "Physical therapy"
    if c.startswith(("8", "36415")) or re.search(r"laborator|patholog|blood (count|sample)|metabolic|panel|culture|assay|cytometry|tissue exam", t):
        return "Lab & pathology"
    if re.match(r"7[0-6]\d\d\d|93\d\d\d|78\d\d\d", c) or re.search(r"radiolog|imaging|x-ray|ct|mri|ultrasound|echocardiog|pet|electrocardiog", t):
        return "Imaging & tests"
    if c.startswith(("S912", "S913", "G029", "G030", "T10")) or "nurse" in t and "home" in t or re.search(r"services of a (registered|licensed) (nurse|practical)", t):
        return "Home health"
    if c.startswith(("E", "K", "A4", "A6", "A7")) or re.search(r"pump|equipment|supplies|wheelchair|walker", t):
        return "Equipment & supplies"
    if re.match(r"[1-6]\d\d\d\d", c) or re.search(r"procedure|biopsy|puncture|catheter|aspiration", t):
        return "Procedures"
    return ""


def care_category(kind, title, lines, prov, date=None):
    """Category for a claim. Facility bills (revenue-code lines) are grouped as hospital facility charges,
    except CAR-T and stem cell transplant; otherwise the category of the most expensive line wins."""
    if kind == "pharmacy":
        return "Pharmacy"
    codes = [(l.get("code") or "").upper() for l in lines or []]
    if codes and sum(bool(re.fullmatch(r"00\d{3}", c)) for c in codes) >= max(1, len(codes) // 2):
        if any(c == "00891" for c in codes):
            return "CAR-T cell therapy"
        if any(c.startswith(("0081", "00362")) for c in codes):
            return "Stem cell transplant"
        in_stay = date and any(e["k"] == "stay" and e["t"][:10] <= date[:10] <= e.get("end", e["t"])[:10] for e in events)
        return "Hospital stay (facility)" if in_stay else "Hospital outpatient (facility)"
    best, amt = "", -1
    for l in lines or []:
        cat = code_category(l.get("code"), l.get("desc"))
        if cat and (l.get("billed") or 0) > amt:
            best, amt = cat, l.get("billed") or 0
    return best or code_category("", f"{title} {prov}") or "Other medical"


def nice_drug(n):
    n = " ".join((n or "").split())
    m = re.match(r"^([A-Z0-9/\-]+(?: [A-Z][A-Z/\-]+)?)\s+(TAB|CAP|SOL|INJ|SUSP|CRE|OIN|PAK|KIT|POW|SYR|PEN)\b\s*(.*)$", n)
    if m:
        name, form, dose = m.groups()
        return f"{name.title()} {dose.lower()} {dict(TAB='tablet', CAP='capsule', SOL='solution', INJ='injection', SUSP='suspension').get(form, form.lower())}".replace("  ", " ").strip()
    return n.title()


def cost_row(eid_, date, insurer, billed, paid, owe, status, kind, cat, prov, title):
    costs.append({"id": eid_, "d": date[:10], "ins": insurer, "billed": billed, "paid": paid, "owe": owe,
                  "status": status, "kind": kind, "cat": cat, "prov": (prov or "").strip().title(), "title": title})


for kind, cdir, insurer in CLAIM_SRC:
    path = os.path.join(cdir, "claims.json")
    if not os.path.exists(path):
        continue
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if kind == "uhc":
        det = {k: list(v.values())[0]["healthCareClaimDetails"] for k, v in raw["details"].items()}
        for c in raw["list"]:
            d, b = det.get(c["claimKey"]) or {}, c.get("balance") or {}
            billed, paid, owe = money(b.get("totalBilledAmount")), money(b.get("healthPlanPays")), money(b.get("patientResponsibility"))
            status, date = c.get("claimStatus") or "", c.get("serviceDate")
            if not date:
                continue
            if c.get("claimType") == "RX":
                drug = nice_drug(c.get("medicationName"))
                i = add(iso(date), "rx", drug, f"Pharmacy fill · {c.get('providerName') or ''} · {insurer}".replace(" ·  ·", " ·"),
                        {"type": "claim", "insurer": insurer, "kind": "Pharmacy", "drug": c.get("medicationName"), "rxNumber": c.get("prescriptionNumber"),
                         "provider": c.get("providerName"), "billed": billed, "paid": paid, "owe": owe, "status": status})
                cost_row(i, date, insurer, billed, paid, owe, status, "pharmacy", "Pharmacy", c.get("providerName"), drug)
                continue
            lines = []
            for ln in d.get("serviceLines") or []:
                procs = [p for p in ln.get("procedures") or [] if p.get("code") not in (None, "MISC")]
                desc = (procs[0]["description"] if procs else (ln.get("providedServiceInfo") or {}).get("description")) or ""
                adj = ln.get("serviceAdjudication") or {}
                lines.append({"date": ln.get("lastServiceDate"), "desc": desc[:140], "code": procs[0]["code"] if procs else "",
                              "billed": money(adj.get("chargedAmount")), "denied": [x.get("description") or str(x) for x in ln.get("deniedClaimInfo") or []]})
            title = next((l["desc"] for l in lines if l["desc"] and len(l["desc"]) < 70), "") or (c.get("providerName") or "Medical claim").title()
            i = add(iso(date), "claim", title, f"{(c.get('providerName') or '').title()} · {insurer}" + (" · denied" if "DENIED" in status else ""),
                    {"type": "claim", "insurer": insurer, "kind": "Medical", "provider": c.get("providerName"), "claimId": c.get("claimId"),
                     "billed": billed, "paid": paid, "owe": owe, "status": status, "lines": lines,
                     "network": d.get("claimNetworkStatus"), "end": c.get("lastServiceDate")},
                    end=iso(c["lastServiceDate"]) if c.get("lastServiceDate") and c["lastServiceDate"] != date else None,
                    abn=1 if "DENIED" in status else None)
            cost_row(i, date, insurer, billed, paid, owe, status, "medical", care_category("medical", title, lines, c.get("providerName"), date),
                     c.get("providerName"), title)
    elif kind == "bcbs":
        for c in raw["details"].values():
            date = (c.get("serviceFromDate") or "")[:10]
            if not date:
                continue
            billed, paid, owe = money(c.get("billedChargeAmount")), money(c.get("bcbsnePaidAmount")), money(c.get("memberPayableAmount"))
            status = c.get("claimStatus") or ""
            prov = ((c.get("renderingProvider") or {}).get("fullName") or "").title()
            if c.get("claimType") == "Pharmacy":
                drug = (c.get("drugName") or "Prescription").title()
                i = add(iso(date), "rx", drug, f"Pharmacy fill · {insurer}",
                        {"type": "claim", "insurer": insurer, "kind": "Pharmacy", "drug": c.get("drugName"),
                         "billed": billed, "paid": paid, "owe": owe, "status": status})
                cost_row(i, date, insurer, billed, paid, owe, status, "pharmacy", "Pharmacy", prov, drug)
                continue
            lines = [{"date": date, "desc": (l.get("serviceTitle") or l.get("serviceDescription") or "").capitalize(), "code": l.get("procedureCode") or "",
                      "billed": money(l.get("billedChargeAmount")), "denied": l.get("deniedReasons") or []} for l in c.get("serviceLines") or []]
            title = next((l["desc"] for l in lines if l["desc"]), "") or prov or "Medical claim"
            st = "DENIED" if c.get("isClaimDenied") else status
            i = add(iso(date), "claim", title, f"{prov} · {insurer}" + (" · denied" if c.get("isClaimDenied") else ""),
                    {"type": "claim", "insurer": insurer, "kind": "Medical", "provider": prov, "claimId": c.get("claimId"),
                     "billed": billed, "paid": paid, "owe": owe, "status": st, "lines": lines},
                    abn=1 if c.get("isClaimDenied") else None)
            cost_row(i, date, insurer, billed, paid, owe, st, "medical", care_category("medical", title, lines, prov, date), prov, title)

# link transfers: one stay ends within a day of another starting at a different hospital
stays = sorted([e for e in events if e["k"] == "stay"], key=lambda e: e["t"])
for a in stays:
    for c in stays:
        if a is c or a["title"] == c["title"]:
            continue
        gap = (datetime.fromisoformat(c["t"][:19]) - datetime.fromisoformat(a.get("end", a["t"])[:19])).total_seconds() / 3600
        if -12 <= gap <= 24:
            org_a = a["title"].split(" · ", 1)[1] if " · " in a["title"] else PRIMARY_ORG
            org_c = c["title"].split(" · ", 1)[1] if " · " in c["title"] else PRIMARY_ORG
            a["sub"] += f" · transferred to {org_c}"
            c["sub"] += f" · transferred from {org_a}"
            details[a["id"]]["transfer_to"] = org_c
            details[c["id"]]["transfer_from"] = org_a

# out-of-pocket payments: a payments.json export (sources.json "payments": {"dir": ...})
paid_out = []
_PAID = find_dir(CONF["payments"]["dir"]) if (CONF.get("payments") or {}).get("dir") else None
if _PAID and os.path.exists(os.path.join(_PAID, "payments.json")):
        with open(os.path.join(_PAID, "payments.json"), encoding="utf-8") as f:
            pj = json.load(f)
        paid_out = [{k: x.get(k) for k in ("d", "payee", "group", "amount", "account", "memo", "est")} for x in pj.get("payments") or []]
        paid_meta = {"exported": pj.get("exported"), "range": pj.get("range"), "excluded": pj.get("excluded"), "balance": pj.get("balance")}

# named dates (diagnosis, surgery, treatment start...) for chart markers and "since" periods;
# the older single "diagnosis_date" still works and becomes a "Diagnosis" milestone
MILESTONES = sorted(({"date": m["date"][:10], "label": m.get("label") or "Milestone", **({"primary": True} if m.get("primary") else {})}
                     for m in CORR.get("milestones") or [] if m.get("date")), key=lambda m: m["date"])
if not MILESTONES and CORR.get("diagnosis_date"):
    MILESTONES = [{"date": CORR["diagnosis_date"][:10], "label": "Diagnosis"}]

events.sort(key=lambda e: e["t"])
data = {"built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": os.path.basename(os.path.abspath(EXP)),
        "events": events, "details": details, "labs": series, "upcoming": upcoming, "problems": problems, "costs": costs,
        "outOfPocket": {"payments": paid_out, **(paid_meta if paid_out else {})},
        "milestones": MILESTONES,
        "clinicWeights": sorted(v for (k, _), v in CLINIC_W.items() if k == "weight")}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(data, f, separators=(",", ":"))
cnt = collections.Counter(e["k"] for e in events)
print(f"wrote {OUT}: {len(events)} events {dict(cnt)}, {len(series)} lab series, "
      f"{sum(1 for s in series if s['n'] >= 3)} with >=3 numeric points, {os.path.getsize(OUT)//1024} KB")
