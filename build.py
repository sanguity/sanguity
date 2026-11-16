#!/usr/bin/env python3
"""Build data.json for Sanguity from the record exports listed in a sources file.

Usage: python3 build.py --config sources.json --out www/data.json

sources.json (per install, see sources.example.json) names each export folder, the organisation it
came from and the insurer behind each claims export. Folder names may contain wildcards; the
alphabetically last match is used, so date-stamped export folders pick the newest automatically.
Re-run whenever new exports are added.

Each export is read by its importer plug-in (server/importers/: epic_mychart, carespace, claims_uhc, claims_bcbs),
the same code the in-app import uses. What's left here is building one profile from several sources: the names and
options in sources.json, joining the sources' records, lab series shared between sources, facility bills inside
another source's hospital stays, transfers between hospitals, payments, milestones and writing the file.

Standard library only: the plug-ins are imported by name, never through the registry, so an importer that needs
more than the standard library (the PDF one) is never loaded here.
"""
import json, os, sys, glob, collections
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # -I leaves the script's folder off the path
from server.importers import files_in, epic_mychart, carespace, claims_uhc, claims_bcbs   # noqa: E402
from server.importers.common import ext_alias, lab_defs, unit_for, unit_key   # noqa: E402


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


def files_prefix(src, d):   # URL prefix for a source's documents; default: served from /records/<folder>/
    return src.get("files") or f"records/{os.path.basename(d)}/"


_P = CONF.get("primary") or sys.exit("sources.json needs a \"primary\" Epic MyChart export")
EXP = find_dir(_P["dir"]) or sys.exit(f"no folder matches primary dir {_P['dir']!r} under {ROOT}")
PRIMARY_ORG = _P.get("org", "")
CLAIMS = {"uhc": claims_uhc, "bcbs": claims_bcbs}

# ------------------------------------------------------------------ the sources, in the order they're joined
# (label for unit clashes, folder, files prefix, plug-in, parse options). The primary export's records carry no
# organisation (it's the person's own health system), as before; the others carry the one sources.json gives.
SOURCES = [("", EXP, files_prefix(_P, EXP), epic_mychart, {"org": "", "tag": ""})]
for s in CONF.get("epic", []):
    if (d := find_dir(s["dir"])):
        SOURCES.append((s.get("org", ""), d, files_prefix(s, d), epic_mychart, {"org": s.get("org", ""), "tag": s.get("tag", "")}))
_CS = CONF.get("carespace") or {}
if _CS.get("dir") and (d := find_dir(_CS["dir"])):
    SOURCES.append((_CS.get("org", ""), d, files_prefix(_CS, d), carespace, {"org": _CS.get("org"), "tag": _CS.get("tag", "CS")}))
for c in CONF.get("claims", []):
    if c["format"] not in CLAIMS:
        print(f"sources.json: claims format {c['format']!r} isn't uhc or bcbs; left out", file=sys.stderr)
    elif (d := find_dir(c["dir"])):
        SOURCES.append(("", d, "", CLAIMS[c["format"]], {"insurer": c.get("insurer") or c["format"]}))

events, details, costs, upcoming, problems = [], {}, [], [], []
labs = {}            # series name -> {"unit", "pts", "txt", "panels": Counter}
weights = {}         # minute -> [time, pounds, organisation]; the first source to give a minute keeps it
facility = set()     # ids of facility bills, for re-filing the ones inside a hospital stay


def series_name(name, unit, label):
    """Where another source's series goes: the series of that name when the units agree, else "<name> (<label>)"."""
    s = labs.get(name)
    if s is None or not s["unit"] or not unit or unit_for(name, s["unit"]) == unit_for(name, unit) \
            or unit_key(s["unit"]) == unit_key(unit):
        return name
    return f"{name} ({label or unit})"


def add_series(s, label):
    name = series_name(s["name"], s["unit"], label)
    into = labs.setdefault(name, {"unit": s["unit"], "pts": [], "txt": [], "panels": collections.Counter()})
    into["pts"] += s.get("pts") or []
    into["txt"] += s.get("txt") or []
    if s.get("panel"):
        into["panels"][s["panel"]] += max(s.get("n") or 0, 1)


def join(data, label, prefix):
    """Add one source's records, with new ids after the ones before it (in the order the plug-in made them)."""
    first = len(events) + 1
    renum = {e["id"]: first + n for n, e in enumerate(sorted(data.get("events") or [], key=lambda e: e["id"]))}
    for e in data.get("events") or []:
        old = e["id"]
        e = {k: v for k, v in e.items() if k != "key"}   # edits to a setup build find events by content, not source keys
        e["id"] = renum[old]
        d = (data.get("details") or {}).get(old) or (data.get("details") or {}).get(str(old))
        if d:
            d = dict(d)
            if d.get("type") == "labday":
                d["ids"] = [renum[i] for i in d["ids"] if i in renum]
            elif d.get("type") == "file" and prefix:
                d["url"] = prefix + d["url"]
            elif d.get("type") == "claim":
                if (d.pop("visit", None) or {}).get("facility"):
                    facility.add(e["id"])
            details[e["id"]] = d
        events.append(e)
    for c in data.get("costs") or []:
        costs.append({**c, "id": renum.get(c["id"], c["id"])})
    upcoming.extend({k: v for k, v in x.items() if k != "key"} for x in data.get("upcoming") or [])
    problems.extend({k: v for k, v in x.items() if k != "key"} for x in data.get("problems") or [])
    for w in data.get("clinicWeights") or []:
        weights.setdefault(w[0][:16], [w[0], w[1], w[2] or PRIMARY_ORG])
    for s in data.get("labs") or []:
        add_series(s, label)


for label, d, prefix, mod, opts in SOURCES:
    try:
        out = mod.parse(files_in(d), **opts)
    except ValueError as e:
        if d == EXP:
            sys.exit(f"{d}: {e}")
        print(f"{d}: left out: {e}", file=sys.stderr)
        continue
    for w in out.get("warnings") or []:
        print(f"{d}: {w}", file=sys.stderr)
    join(out["data"], label, prefix)

# ------------------------------------------------------------------ lab series across sources
# "... (External)" series fold into their canonical series (same units only), now that every source is in
for ext, canon in list(ext_alias().items()) + [(n, n[:-len(" (External)")]) for n in list(labs) if n.endswith(" (External)") and n not in ext_alias()]:
    if ext in labs and canon in labs and (not labs[ext]["unit"] or not labs[canon]["unit"] or unit_key(labs[ext]["unit"]) == unit_key(labs[canon]["unit"])):
        labs[canon]["pts"] += [p[:6] + ["EXT"] for p in labs[ext]["pts"]]
        labs[canon]["txt"] += labs[ext]["txt"]
        labs[canon]["panels"].update(labs[ext]["panels"])
        del labs[ext]
LAB_DEFS = lab_defs()
series = []
for name, s in labs.items():
    seen, pts = set(), []
    for p in s["pts"]:   # the same value on the same day from two sources counts once; the earlier source's wins
        k = (p[0][:10], round(p[1], 4))
        if k not in seen:
            seen.add(k)
            pts.append(p)
    pts.sort(key=lambda p: (p[0], p[1]))
    series.append({"name": name, "unit": s["unit"], "panel": s["panels"].most_common(1)[0][0] if s["panels"] else "",
                   "n": len(pts), **({"loinc": LAB_DEFS[name]["loinc"]} if "loinc" in LAB_DEFS.get(name, {}) else {}),
                   "pts": pts, "txt": sorted(s["txt"])[-50:]})
series.sort(key=lambda s: (-s["n"], s["name"]))

# ------------------------------------------------------------------ claims against the other sources' records
# a facility bill whose date falls inside a hospital stay (from any source) is the stay's
stays = sorted([e for e in events if e["k"] == "stay"], key=lambda e: e["t"])
for c in costs:
    if c["id"] in facility and c["cat"] == "Hospital outpatient (facility)" and \
            any(e["t"][:10] <= c["d"] <= e.get("end", e["t"])[:10] for e in stays):
        c["cat"] = "Hospital stay (facility)"

# link transfers: one stay ends within a day of another starting at a different hospital
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

# corrections.json next to build.py: only its milestones are read here now. Corrections to events (its older
# "not_hospital_stays") are edits made in the app, applied to every import of this build (server/edits.py); the
# app moved an existing corrections.json's into edits once, at start-up
CORR_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corrections.json")
CORR = json.load(open(CORR_FILE, encoding="utf-8")) if os.path.exists(CORR_FILE) else {}
# named dates (diagnosis, surgery, treatment start...) for chart markers and "since" periods;
# the older single "diagnosis_date" still works and becomes a "Diagnosis" milestone
MILESTONES = sorted(({"date": m["date"][:10], "label": m.get("label") or "Milestone", **({"primary": True} if m.get("primary") else {})}
                     for m in CORR.get("milestones") or [] if m.get("date")), key=lambda m: m["date"])
if not MILESTONES and CORR.get("diagnosis_date"):
    MILESTONES = [{"date": CORR["diagnosis_date"][:10], "label": "Diagnosis"}]

events.sort(key=lambda e: (e["t"], e["id"]))
upcoming.sort(key=lambda x: x["t"])
data = {"built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": os.path.basename(os.path.abspath(EXP)),
        "events": events, "details": dict(sorted(details.items())), "labs": series, "upcoming": upcoming, "problems": problems, "costs": costs,
        "outOfPocket": {"payments": paid_out, **(paid_meta if paid_out else {})},
        "milestones": MILESTONES,
        "clinicWeights": sorted(weights.values())}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(data, f, separators=(",", ":"))
cnt = collections.Counter(e["k"] for e in events)
print(f"wrote {OUT}: {len(events)} events {dict(cnt)}, {len(series)} lab series, "
      f"{sum(1 for s in series if s['n'] >= 3)} with >=3 numeric points, {os.path.getsize(OUT)//1024} KB")
