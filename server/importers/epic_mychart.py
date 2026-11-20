"""Epic MyChart importer: the JSON a MyChart export tool saves, plus the C-CDA visit records Epic offers beside it.

An export is a folder laid out like this (the file names carry the portal's name as a prefix, which is ignored):

    test-results/<portal>_test_results_raw.json               results, with their reports and comments
    messages/<portal>_messages.json                            conversations
    messages/<portal>_messages_long_conversations_full.json    optional: the whole of long conversations
    letters/<portal>_letters.json                              letters
    documents/[<folder>/]<YYYY-MM-DD>_<name>[_<n>].<ext>       documents
    visit-records/**/*.xml, *.zip                              C-CDA visit records ("Download my record"): visits,
                                                               body weights and the vital signs taken at each visit
    admissions.json                                            optional: {"admissions": [{"admit", "discharge", "where"}]}

One upload may hold several exports, each in its own folder (one per health system), as build.py reads a primary
export and any number of others. They're read in folder order into one set of records, so a test measured in other
units at the second system gets its own series, as in build.py.

This was build.py's Epic reader, moved here (#28); build.py now reads every Epic export in sources.json through
parse(files, org=..., tag=...). Compared with the old reader in build.py, its output differs only in these ways:
- lab series take their canonical names from lab-aliases.json for every export (build.py does that only for the
  extra ones), so "Sodium" from one system and "SODIUM" from another share the "Sodium, Blood" chart;
- the organisation comes from the export itself (the C-CDA visit records' custodian), unless the caller names it
  (build.py, from sources.json). An export without visit records has none, so its events carry no organisation and its lab points no source tag, and
  a test it reports in other units is named "<test> (<unit>)" rather than "<test> (<organisation>)";
- C-CDA visit records are read with the C-CDA importer's own functions (ccda.py): their times keep the document's
  UTC offset, and a visit's details have "type": "visit" (build.py overwrote it with the encounter type, so the
  drawer showed nothing) with the C-CDA importer's fields;
- stays are guessed from inpatient results whenever the export has no admissions.json, for every export (build.py
  skipped the guess for an extra export with visit records); corrections.json and the cross-source steps (transfers
  between hospitals, insurer claims) aren't applied here;
- every event and series has a source key, and document links point into the upload.

Standard library only, plus ccda.py from this package.
"""
import collections
import hashlib
import html
import json
import re
import zipfile

from . import ccda
from .common import (canonical_lab, ext_alias, inferred_stays, iso, lab_alias, lab_day_sub, lab_day_title, lab_defs,
                     resolve_name, stay, strip_html, unit_key)

NAME = "epic-mychart"
LABEL = "Epic MyChart export"

ROOT = r"^(?P<root>(?:[^/]+/)*?)"
PARTS = {   # what marks an export folder, and where its root is
    "results": re.compile(ROOT + r"test-results/[^/]*test_results_raw\.json$"),
    "messages": re.compile(ROOT + r"messages/[^/]*messages\.json$"),
    "long": re.compile(ROOT + r"messages/[^/]*messages_long_conversations_full\.json$"),
    "letters": re.compile(ROOT + r"letters/[^/]*letters\.json$"),
}
DOC_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})(?:T[\d:Z]+)?_(.+?)(?:_\d+)?\.(\w+)$")   # 2026-01-10_Visit_Summary_1.pdf
HEAD_BYTES = 4096
MAX_ZIP_MEMBERS = 5000          # C-CDA files read from the zips in visit-records/, at most
MAX_ZIP_BYTES = 1024 ** 3       # and at most this much of them, unpacked

KIND = {"LAB": "lab", "IMAGING": "imaging", "PROCEDURE": "procedure", "OTHER": "study",
        1: "lab", 2: "imaging", 3: "study", 4: "procedure", "1": "lab", "2": "imaging", "3": "study", "4": "procedure"}


# ------------------------------------------------------------------ finding exports

def _roots(files):
    """{export folder ("" or "path/"): {"results"|"messages"|"long"|"letters": the first file of that kind}}."""
    roots = collections.defaultdict(dict)
    for rel in sorted(files):
        for part, rx in PARTS.items():
            m = rx.match(rel)
            if m:
                roots[m["root"]].setdefault(part, rel)
    return {r: p for r, p in sorted(roots.items()) if set(p) & {"results", "messages", "letters"}}


def _looks_json(path):
    try:
        with open(path, "rb") as f:
            head = f.read(HEAD_BYTES)
    except OSError:
        return False
    return head.lstrip(b"\xef\xbb\xbf \t\r\n")[:1] == b"{"


def detect(files):
    """1.0 for a folder with Epic's test-results/*test_results_raw.json; 0.97 for messages and letters without it
    (above the C-CDA importer's score for the visit records an export holds); 0.6 for only one of those. A plain
    C-CDA zip has none of these files and scores 0. Reads the first 4 KB of at most one file per export."""
    best = 0.0
    for root, parts in _roots(files).items():
        if "results" in parts:
            score = 1.0 if _looks_json(files[parts["results"]]) else 0.3
        elif "messages" in parts and "letters" in parts:
            score = 0.97 if _looks_json(files[parts["messages"]]) else 0.3
        else:
            score = 0.6 if _looks_json(files[parts.get("messages") or parts["letters"]]) else 0.0
        best = max(best, score)
    return best


# ------------------------------------------------------------------ records

def _hkey(*fields):
    """Source key for a record without a usable id: a hash of its normalised identifying fields."""
    norm = "\x1f".join(" ".join(str(f if f is not None else "").split()).lower() for f in fields)
    return NAME + ":h:" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


class _Out:
    """The records collected so far. Its add() and seen_visits are what ccda._visit() needs, so the C-CDA importer's
    code reads the visit records straight into these."""

    def __init__(self):
        self.events, self.details, self.eid, self.keys = [], {}, 0, set()
        self.labs, self.panel_votes = {}, collections.defaultdict(collections.Counter)
        self.weights, self.warnings = {}, []
        self.seen_visits, self.stay_days = set(), set()

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
        if kind == "stay" and detail and detail.get("discharge"):   # a real stay: one per admit day and hospital, as build.py
            day = (t[:10], title)
            if day in self.stay_days:
                return None
            self.stay_days.add(day)
        self.eid += 1
        e = {"id": self.eid, "t": t, "k": kind, "title": title, "sub": sub}
        if key:
            if key.startswith("ccda:"):          # from ccda._visit(): the same id, under this importer's name
                key = NAME + key[len("ccda"):]
            e["key"] = self.unique(key, t, kind, title, sub)
        if end:
            e["end"] = end
        e.update({k: v for k, v in extra.items() if v not in (None, "", [])})
        if detail:
            self.details[self.eid] = detail
        self.events.append(e)
        return self.eid


def _load_json(files, rel, out):
    """A JSON file of the export, or None (with a warning) when it can't be read."""
    try:
        with open(files[rel], encoding="utf-8-sig") as f:
            doc = json.load(f)
    except (OSError, UnicodeDecodeError, ValueError) as e:
        out.warnings.append(f"{rel} couldn't be read ({type(e).__name__}: {str(e)[:80]}) and was left out.")
        return None
    if not isinstance(doc, dict):
        out.warnings.append(f"{rel} isn't the JSON object an Epic export writes, so it was left out.")
        return None
    return doc


# ------------------------------------------------------------------ test results

def _series(out, name, unit, label):
    """The series a component goes into: its canonical name (lab-aliases.json) when the units agree with what's
    there, "<name> (<label>)" when they don't. "... (External)" names stay as they are until they're folded into
    their canonical series at the end, as build.py does."""
    if name in ext_alias() or name.endswith(" (External)"):
        return name
    return resolve_name(name, unit, label, out.labs)


def _results(out, tr, rel, org, tag, infer_stays):
    """build.py's epic_results(): one event per result, a lab-day event per day, stays guessed from inpatient results."""
    groups, results = tr.get("groups") or [], tr.get("details")
    if not isinstance(results, dict) or not isinstance(groups, list):
        out.warnings.append(f"{rel} has no \"details\" of test results, so no results were read.")
        return
    group_of = {k: g for g in groups if isinstance(g, dict) for k in g.get("resultList") or []}
    lab_days = collections.defaultdict(lambda: {"n": 0, "abn": 0, "orders": set(), "ids": []})
    admissions, inpatient_ts = collections.defaultdict(list), []
    undated = 0
    for rkey, d in results.items():
        g = group_of.get(rkey, {})
        for r in (d or {}).get("results") or []:
            m = r.get("orderMetadata") or {}
            t = iso(m.get("prioritizedInstantISO"))
            if not t:
                undated += 1
                continue
            kind = KIND.get(m.get("resultType"), "study")
            if g.get("isInpatient"):
                if g.get("formattedEncounterDate"):
                    admissions[g["formattedEncounterDate"]].append(t)
                else:
                    inpatient_ts.append(t)
            abn_here, rows = 0, []
            for c in r.get("resultComponents") or []:
                ci, cr = c.get("componentInfo") or {}, c.get("componentResultInfo") or {}
                name, unit = ci.get("name") or "?", ci.get("units") or ""
                rr = cr.get("referenceRange") or {}
                v = cr.get("numericValue")
                lo, hi = rr.get("low"), rr.get("high")
                flag = ""
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    if lo is not None and v < lo:
                        flag = "L"
                    elif hi is not None and v > hi:
                        flag = "H"
                if flag:
                    abn_here += 1
                rows.append([name, cr.get("value"), unit, rr.get("formattedReferenceRange") or "", flag])
                sname = _series(out, name, unit, org or unit)
                out.panel_votes[sname][d.get("orderName") or r.get("name") or ""] += 1
                s = out.labs.get(sname)
                if s is None:
                    s = out.labs[sname] = {"unit": unit, "pts": [], "txt": [], "code": ci.get("componentID")}
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    s["pts"].append([t, v, lo, hi, flag, (cr.get("value") or "")] + ([tag] if tag else []))
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
            key = _hkey(t, kind, d.get("orderName"), r.get("name"), m.get("orderProviderName"), org)
            if kind == "lab" and not report:
                ld = lab_days[t[:10]]
                ld["n"] += len(rows)
                ld["abn"] += abn_here
                ld["orders"].add(d.get("orderName") or "")
                ld["ids"].append(out.add(t, "labitem", d.get("orderName") or "Lab", sub, det, abn=abn_here, hidden=1, key=key))
            else:
                out.add(t, kind if kind != "lab" else "study", d.get("orderName") or r.get("name") or "Result", sub, det,
                        abn=abn_here or None, key=key)
    if undated:
        out.warnings.append(f"{undated} result{'s' if undated != 1 else ''} in {rel} had no date and "
                            f"{'were' if undated != 1 else 'was'} left out.")
    by_id = {e["id"]: e for e in out.events}
    for day, ld in lab_days.items():   # one visible "lab draw" event per day, linking to that day's results
        first = min((by_id[i] for i in ld["ids"]), key=lambda e: e["t"])
        out.add(first["t"], "lab", lab_day_title(ld["n"], org), lab_day_sub(sorted(o for o in ld["orders"] if o)),
                {"type": "labday", "ids": ld["ids"]},
                abn=ld["abn"] or None, src=tag, key=f"{NAME}:labday:{day}" + (f"|{org}" if org else ""))
    if infer_stays:   # hospital stays guessed from inpatient results, when the export has no admit/discharge dates
        for st in inferred_stays(admissions, inpatient_ts, org):
            out.add(st["t"], "stay", st["title"], st["sub"], st["detail"], end=st["end"],
                    key=_hkey(st["t"], "stay", st["title"], st["detail"].get("admit")))


# ------------------------------------------------------------------ messages, letters, documents

def _messages(out, msg, long_full, org):
    """build.py's epic_messages(): one event per conversation, the whole of it in the details."""
    users = msg.get("users") or {}
    listing = msg.get("list") or {}

    def author(a, extra_users):
        if not a:
            return ""
        for k in ("wprKey", "empKey", "userKey", "key"):
            u = (extra_users.get(a.get(k)) or users.get(a.get(k))) if a.get(k) else None
            if u:
                return u.get("name") or ""
        return a.get("displayName") or ""

    for hid, c in (msg.get("details") or {}).items():
        full = long_full.get(hid)
        msgs = (full or c).get("messages") or []
        xu = (full or {}).get("users") or c.get("users") or {}
        msgs = sorted(msgs, key=lambda x: x.get("deliveryInstantISO") or "")
        if not msgs or not msgs[0].get("deliveryInstantISO"):
            continue
        folders = (listing.get(hid) or {}).get("folders") or []
        kind = "appt_msg" if 7 in folders else "auto_msg" if 6 in folders else "message"
        people = sorted({author(x.get("author"), xu) for x in msgs} - {""})
        det = {"type": "conversation", "subject": c.get("subject"),
               "messages": [{"t": x.get("deliveryInstantISO"), "from": author(x.get("author"), xu) or "You",
                             "body": x.get("body") or "",
                             "att": [a.get("name") for a in x.get("attachments") or []]} for x in msgs]}
        t, subject = iso(msgs[0]["deliveryInstantISO"]), (c.get("subject") or "(no subject)").strip()
        out.add(t, kind, subject,
                f"{len(msgs)} message{'s' if len(msgs) != 1 else ''}" + (f" · {', '.join(people[:3])}" if people else "") + (f" · {org}" if org else ""),
                det, end=iso(msgs[-1].get("deliveryInstantISO")) if len(msgs) > 1 else None,
                key=_hkey(t, "conversation", subject, org))


def _letters(out, let):
    deps = let.get("departments") or {}
    undated = 0
    for l in let.get("letters") or []:
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
        t, dep = iso(l.get("dateISO")), (deps.get(l.get("depId")) or {}).get("name", "")
        if not t:
            undated += 1
            continue
        out.add(t, "letter", title, dep, {"type": "letter", "html": body},
                key=_hkey(t, "letter", dep, hashlib.sha256(body.encode("utf-8")).hexdigest()))
    if undated:
        out.warnings.append(f"{undated} letter{'s' if undated != 1 else ''} had no date and {'were' if undated != 1 else 'was'} left out.")


def _documents(out, files, root, org):
    """documents/<file> and documents/<folder>/<file>, named <date>_<name>[_<n>].<ext>."""
    skipped = 0
    pre = root + "documents/"
    for rel in sorted(files):
        if not rel.startswith(pre) or rel[len(pre):].count("/") > 1:
            continue
        mm = DOC_NAME.match(rel.rsplit("/", 1)[-1])
        if not mm or mm.group(3).lower() == "json":
            skipped += not rel.lower().endswith(".json")
            continue
        date, name, ext = mm.groups()
        out.add(iso(date), "document", name, ext.upper() + (f" · {org}" if org else ""),
                {"type": "file", "url": rel, "ext": ext.lower()}, key=f"{NAME}:document:{rel[len(root):]}")
    if skipped:
        out.warnings.append(f"{skipped} file{'s' if skipped != 1 else ''} in {pre} {'weren' if skipped != 1 else 'wasn'}'t named "
                            f"like an Epic export's documents (<date>_<name>.<ext>) and {'were' if skipped != 1 else 'was'} left out.")


# ------------------------------------------------------------------ C-CDA visit records

def _cda_files(out, files, root):
    """(name, bytes) of each C-CDA document under visit-records/, loose or inside a zip."""
    pre, members, total = root + "visit-records/", 0, 0
    for rel in sorted(k for k in files if k.startswith(pre)):
        low = rel.lower()
        if low.endswith(".xml"):
            with open(files[rel], "rb") as f:
                yield rel, f.read(ccda.MAX_BYTES + 1)
        elif low.endswith(".zip"):
            try:
                with zipfile.ZipFile(files[rel]) as z:
                    for info in sorted(z.infolist(), key=lambda i: i.filename):
                        if info.is_dir() or not info.filename.lower().endswith(".xml"):
                            continue
                        name = f"{rel}!{info.filename}"
                        if info.flag_bits & 0x1:
                            out.warnings.append(f"{name} is password-protected and was left out.")
                            continue
                        members += 1
                        if members > MAX_ZIP_MEMBERS or total + info.file_size > MAX_ZIP_BYTES:
                            out.warnings.append(f"The zips in {pre} hold more than can be read; the rest was left out.")
                            return
                        with z.open(info) as f:
                            raw = f.read(ccda.MAX_BYTES + 1)
                        total += len(raw)
                        yield name, raw
            except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, EOFError, OSError, RuntimeError) as e:
                out.warnings.append(f"{rel} couldn't be unpacked ({type(e).__name__}) and was left out.")


def _cda_docs(out, files, root):
    docs = []
    for name, raw in _cda_files(out, files, root):
        if ccda._score(raw[:ccda.HEAD_BYTES]) == 0:     # not a C-CDA (an XDM METADATA.XML, a stylesheet...): not a record
            continue
        try:
            docs.append(ccda.Doc(name, ccda.load_bytes(name, raw)))
        except ValueError as e:
            out.warnings.append(str(e))
    return docs


def _weights(out, doc, org):
    """Body weights anywhere in the document (LOINC 29463-7) into clinicWeights, in pounds, as build.py does."""
    for obs in doc.root.iter(ccda.H + "observation"):
        code = ccda._f(obs, "h:code")
        if code is None or code.get("code") != ccda.WEIGHT_LOINC:
            continue
        x, _, unit = ccda._value(obs)
        f = ccda.WEIGHT_UNITS.get(unit.strip().lower())
        t = ccda._when(obs)
        if t and f and x is not None and 1 < x * f < 600:   # from 1 lb: a baby's weight too (#196)
            out.weights.setdefault(t[:16], [t, round(x * f, 1), doc.org or org])


def _vitals(out, doc, tag):
    """The visit record's Vital Signs section (blood pressure, heart rate, temperature, SpO2, respiratory rate,
    height, BMI) as series in the "Vitals" panel, named as the C-CDA importer names them. Each visit record repeats
    the last vitals taken, so the same reading often comes several times: _fold_external keeps it once. Weights are
    _weights()'s."""
    for v in ccda.vital_readings(doc):
        if v.get("weight"):
            continue
        name = canonical_lab(v["name"], v["loinc"]) or v["name"]
        s = out.labs.get(name)
        if s is not None and s["unit"] and unit_key(s["unit"]) != unit_key(v["unit"]):
            name = f"{name} ({v['unit']})"        # a result of the same name in other units: never one axis
            s = out.labs.get(name)
        if s is None:
            s = out.labs[name] = {"unit": v["unit"], "pts": [], "txt": [], "code": None}
        s["vital"], s["loinc"] = True, s.get("loinc") or v["loinc"]
        s["pts"].append([v["t"], v["x"], None, None, "", v["raw"]] + ([tag] if tag else []))
        out.panel_votes[name]["Vitals"] += 1


# ------------------------------------------------------------------ the importer

def _export(out, files, root, parts, org=None, tag=None):
    docs = _cda_docs(out, files, root)
    if org is None:
        orgs = collections.Counter(d.org for d in docs if d.org)
        org = orgs.most_common(1)[0][0] if orgs else ""   # the health system, as its own visit records name it
    else:   # named by the caller (build.py, from sources.json): the visit records' stays and visits use it too
        for d in docs:
            d.org = org
    tag = (org or None) if tag is None else (tag or None)
    adm_rel = root + "admissions.json"
    if parts.get("results"):
        tr = _load_json(files, parts["results"], out)
        if tr is not None:
            _results(out, tr, parts["results"], org, tag, infer_stays=adm_rel not in files)
    if adm_rel in files:
        adm = _load_json(files, adm_rel, out) or {}
        for x in adm.get("admissions") or []:
            if isinstance(x, dict) and x.get("admit") and x.get("discharge"):
                st = stay(x["admit"], x["discharge"], x.get("where", ""), org, "Visit list")
                out.add(st["t"], "stay", st["title"], st["sub"], st["detail"], end=st["end"],
                        key=_hkey(x["admit"][:10], "stay", x.get("where", ""), org))
    if parts.get("messages"):
        msg = _load_json(files, parts["messages"], out)
        long_full = (_load_json(files, parts["long"], out) or {}) if parts.get("long") else {}
        if msg is not None:
            _messages(out, msg, long_full, org)
    if parts.get("letters"):
        let = _load_json(files, parts["letters"], out)
        if let is not None:
            _letters(out, let)
    _documents(out, files, root, org)
    for doc in docs:
        _weights(out, doc, org)
        _vitals(out, doc, tag)
        ee = ccda._f(doc.root, "h:componentOf/h:encompassingEncounter")
        if ee is not None:
            ccda._visit(doc, out, ee)


def _fold_external(out):
    """build.py's last steps on the series: "... (External)" series fold into their canonical one (same units only),
    and same-day duplicates across sources go (the export's own result wins: it sorts first)."""
    for ext, canon in list(ext_alias().items()) + [(n, n[:-len(" (External)")]) for n in list(out.labs)
                                                   if n.endswith(" (External)") and n not in ext_alias()]:
        if ext in out.labs and canon in out.labs:
            a, b = out.labs[ext], out.labs[canon]
            if not a["unit"] or not b["unit"] or unit_key(a["unit"]) == unit_key(b["unit"]):
                b["pts"] += [p[:6] + ["EXT"] for p in a["pts"]]
                b["txt"] += a["txt"]
                out.panel_votes[canon].update(out.panel_votes[ext])
                del out.labs[ext]
    for s in out.labs.values():
        when = 16 if s.get("vital") else 10   # vitals: the same reading at the same minute (two the same day are two)
        seen, keep = set(), []
        for p in sorted(s["pts"], key=lambda p: (p[0][:when], p[6] if len(p) > 6 else "")):
            k = (p[0][:when], round(p[1], 4))
            if k not in seen:
                seen.add(k)
                keep.append(p)
        s["pts"] = keep


def _series_list(out):
    defs, alias = lab_defs(), lab_alias()
    series = []
    for name, s in out.labs.items():
        pts = sorted(s["pts"], key=lambda p: (p[0], p[1]))
        votes = out.panel_votes[name]
        base = re.sub(r" \([^()]*\)$", "", name)          # "Hemoglobin (g/L)" is still hemoglobin, for its key
        canon = alias.get(name.lower()) or alias.get(base.lower())
        loinc = (defs.get(canon) or {}).get("loinc") or s.get("loinc")   # vitals carry their own LOINC code
        key = (f"loinc:{loinc}" if loinc else f"{NAME}:component|{s['code']}" if s.get("code")
               else f"{NAME}:name|{name.strip().lower()}")
        series.append({"name": name, "unit": s["unit"], "panel": votes.most_common(1)[0][0] if votes else "",
                       "n": len(pts), **({"loinc": defs[name]["loinc"]} if "loinc" in defs.get(name, {}) else {}),
                       "key": key, "pts": pts, "txt": sorted(s["txt"])[-50:]})
    groups = collections.defaultdict(list)   # one code in several units: each adds "|<unit as reported>"
    for s in series:
        groups[s["key"]].append(s)
    for group in groups.values():
        if len(group) > 1 and len({g["unit"] for g in group}) > 1:
            for g in group:
                g["key"] += f"|{g['unit']}"
    shared = collections.Counter(s["key"] for s in series)   # still shared: two names, one code, one unit
    for s in series:
        if shared[s["key"]] > 1:
            s["key"] += f"|{s['name'].strip().lower()}"
    series.sort(key=lambda s: (-s["n"], s["name"]))
    return series


def parse(files, org=None, tag=None):
    """Turn the Epic MyChart exports in an upload into data.json-shaped records: {"data": {...}, "warnings": [...]}.

    `org` names the health system instead of the visit records' custodian ("" for none), and `tag` is the source tag
    on lab points and lab days instead of the organisation's name ("" for none): build.py takes both from sources.json."""
    roots = _roots(files)
    if not roots:
        raise ValueError("This doesn't look like an Epic MyChart export: there's no test-results/, messages/ or "
                         "letters/ folder with the JSON files the export tool writes.")
    out = _Out()
    for root, parts in roots.items():
        _export(out, files, root, parts, org, tag)
    _fold_external(out)
    series = _series_list(out)
    out.events.sort(key=lambda e: e["t"])
    if not (out.events or series):
        if out.warnings:
            raise ValueError("Nothing in the Epic MyChart export could be read. " + " ".join(out.warnings[:3]))
        out.warnings.append("The Epic MyChart export was read but held no results, messages, letters, documents or visits.")
    data = {"events": out.events, "details": out.details, "labs": series, "costs": [], "upcoming": [], "problems": [],
            "clinicWeights": sorted(out.weights.values())}
    return {"data": data, "warnings": out.warnings}
