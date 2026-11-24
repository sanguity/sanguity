"""PDF lab reports (Quest, Labcorp): the results printed in a lab's patient report, read from the PDF's text.

Patient portals of the big reference labs give results as PDFs, so for many people a PDF is the only copy they have.
The text of each page is extracted (no OCR: a scanned report with no text layer is refused), and a layout template
(`pdf_layouts/`, one small module per lab) recognises the report and reads each line of its results table: test
name, value, flag, unit and reference range, plus the collection date, ordering provider and specimen number above
the table. A line in the table that the template doesn't read is a warning naming its page and line, never a
guessed value, and a PDF no template recognises is refused.

The upload must be PDFs only, so a document that came with another export (a C-CDA's or FHIR bundle's attachment, a
portal's documents folder) is left to that export's importer.

PDFs are hostile input: each is read by pypdf in a separate Python process, with a byte and page cap, a time limit,
and (where the system allows) a memory limit. Nothing is rendered and nothing in the file is run.

Records: per report, one `document` event with the PDF itself; one hidden `labitem` per panel (or lone test) with
the panel's rows; one `lab` event per collection day; a `labs` series per test. Keys: "pdf-labs:<layout>:<specimen
number>|<panel lower case>" for a labitem (a hash of layout, collection time and panel when the report shows no
specimen number), "pdf-labs:labday:<YYYY-MM-DD>" for a day, "pdf-labs:file:<16 hex of the file's SHA-256>" for the
document, and "loinc:<code>" or "pdf-labs:<test lower case>|<unit>" for a series.
"""
import collections, hashlib, json, os, re, subprocess, sys
from datetime import datetime

from . import pdf_layouts
from .common import canonical_lab, comparator, lab_day_sub, lab_day_title, loinc_of, range_text, report_flag, value_number

NAME = "pdf-labs"
LABEL = "Lab report PDF (Quest, Labcorp)"

MAX_BYTES = 20 * 1024 * 1024   # a lab report is a few hundred KB
MAX_PAGES = 40                 # ... and a few pages
MAX_PAGE_CHARS = 200_000       # text kept per page
TIMEOUT = 30                   # seconds to read one PDF
DETECT_TIMEOUT = 10            # seconds to read page 1 when detecting
MEMORY = 768 * 1024 * 1024     # address-space limit of the reading process, where the system has one
JUNK = re.compile(r"(?:^|/)(?:__MACOSX/|\.[^/]*$|Thumbs\.db$|desktop\.ini$)", re.I)

# Runs in its own process (python -I -B: no user site, environment or current directory on the path, no .pyc written).
# Prints JSON:
# {"count": pages, "pages": [text or null per page read]} or {"error": "encrypted" | "pages" | "unreadable" | "nopypdf"}.
_READER = r"""
import json, sys
try:
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (int(sys.argv[4]), int(sys.argv[4])))
except Exception:
    pass
try:
    import pypdf
except ImportError:
    print(json.dumps({"error": "nopypdf"})); sys.exit(0)
import logging
logging.disable(logging.CRITICAL)
path, max_pages, read, max_chars = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[5])
try:
    r = pypdf.PdfReader(path)
    if r.is_encrypted and not r.decrypt(""):
        print(json.dumps({"error": "encrypted"})); sys.exit(0)
    n = len(r.pages)
    if n > max_pages:
        print(json.dumps({"error": "pages", "count": n})); sys.exit(0)
    pages = []
    for i in range(min(n, read)):
        try:
            pages.append((r.pages[i].extract_text(extraction_mode="layout") or "")[:max_chars])
        except Exception:
            pages.append(None)
    print(json.dumps({"count": n, "pages": pages}))
except Exception:
    print(json.dumps({"error": "unreadable"}))
"""

_cache = collections.OrderedDict()   # (path, size, mtime, pages read) -> pages, so parse doesn't read page 1 twice


class _NotRead(ValueError):
    """A PDF that couldn't be read at all (scanned, encrypted, too big...): the message says why."""


def _pages(rel, path, read=None, timeout=None):
    """The text of the first `read` pages of a PDF (all of them by default), as a list (None for a page that
    couldn't be read). Raises _NotRead with the reason when the PDF can't be read at all."""
    read, timeout = read or MAX_PAGES, timeout or TIMEOUT
    try:
        st = os.stat(path)
    except OSError:
        raise _NotRead(f"{rel} couldn't be opened.") from None
    if st.st_size > MAX_BYTES:
        raise _NotRead(f"{rel} is larger than {MAX_BYTES // (1024 * 1024)} MB, which is too big for a lab report.")
    key = (path, st.st_size, st.st_mtime_ns, read, MAX_PAGES)
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    try:
        p = subprocess.run([sys.executable, "-I", "-B", "-c", _READER, path, str(MAX_PAGES), str(read), str(MEMORY),
                            str(MAX_PAGE_CHARS)], stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = json.loads(p.stdout.decode("utf-8", "replace") or "{}")
    except subprocess.TimeoutExpired:
        raise _NotRead(f"{rel} took more than {timeout} seconds to read, so it was left out.") from None
    except ValueError:
        out = {"error": "unreadable"}
    err = out.get("error") or ("unreadable" if not isinstance(out.get("pages"), list) else None)
    if err == "nopypdf":
        raise _NotRead("Reading PDFs needs the pypdf package, which isn't installed on this server.")
    if err == "encrypted":
        raise _NotRead(f"{rel} is password-protected. Save a copy without the password and upload that.")
    if err == "pages":
        raise _NotRead(f"{rel} has {out.get('count')} pages; lab reports of more than {MAX_PAGES} pages aren't read.")
    if err:
        raise _NotRead(f"{rel} isn't a PDF that can be read (it may be damaged).")
    pages = out["pages"]
    _cache[key] = pages
    while len(_cache) > 16:
        _cache.popitem(last=False)
    return pages


def _lines(text):
    """The page's non-blank lines, right-trimmed."""
    return [line.rstrip() for line in (text or "").replace("\r", "").split("\n") if line.strip()]


def _pdfs(files):
    """[(rel, path)] of the upload's PDFs, sorted; None when the upload holds anything other than PDFs."""
    out = []
    for rel, path in sorted(files.items()):
        if JUNK.search(rel):
            continue
        if not rel.lower().endswith(".pdf"):
            return None
        out.append((rel, path))
    return out


def _is_pdf(path):
    try:
        with open(path, "rb") as f:
            return b"%PDF-" in f.read(1024)
    except OSError:
        return False


def _layout_for(lines):
    """The layouts that recognise page 1's lines: a report is read only when exactly one does."""
    hits = []
    for lay in pdf_layouts.layouts():
        try:
            if lay.recognise(lines):
                hits.append(lay)
        except Exception:
            continue
    return hits


# ------------------------------------------------------------------ detect
def detect(files):
    """0.9 for an upload of PDFs whose first page a layout recognises; 0.3 for any other upload of PDFs only, so an
    unrecognised or scanned report gets this importer's explanation rather than "nothing matched"; 0 otherwise."""
    pdfs = _pdfs(files)
    if not pdfs or not all(_is_pdf(p) for _, p in pdfs):
        return 0.0
    for rel, path in pdfs[:3]:
        try:
            pages = _pages(rel, path, read=1, timeout=DETECT_TIMEOUT)
        except ValueError:
            continue
        if pages and len(_layout_for(_lines(pages[0]))) == 1:
            return 0.9
    return 0.3


# ------------------------------------------------------------------ reading one report
_TZ = {"EST": "-05:00", "EDT": "-04:00", "CST": "-06:00", "CDT": "-05:00", "MST": "-07:00", "MDT": "-06:00",
       "PST": "-08:00", "PDT": "-07:00"}


def _when(m):
    """ISO time from a COLLECTED match: date MM/DD/YYYY, optional time, AM/PM and US time zone; noon for a date."""
    mo, d, y = (int(x) for x in m.group("date").split("/"))
    try:
        datetime(y, mo, d)
    except ValueError:
        return None
    t = m.groupdict().get("time")
    if not t:
        return f"{y:04d}-{mo:02d}-{d:02d}T12:00:00"
    h, mi = (int(x) for x in t.split(":"))
    ap = (m.groupdict().get("ampm") or "").upper()
    if ap:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if ap == "PM" else 0)
    if h > 23 or mi > 59:
        return None
    return f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:00" + _TZ.get((m.groupdict().get("tz") or "").upper(), "")


def _read_report(rel, pages, lay):
    """(results, meta, problems) for one report. problems: {"unmatched": ["page 1 line 14", ...], "undated": n,
    "unreadable_pages": [n, ...]}."""
    results, meta = [], {"provider": "", "specimen": ""}
    unmatched, undated, bad_pages = [], 0, []
    collected, panel = None, ""
    for p, text in enumerate(pages, 1):
        if text is None:
            bad_pages.append(p)
            continue
        in_table, header_indent = False, 0
        for n, line in enumerate(_lines(text), 1):
            stripped = line.strip()
            indent = len(line) - len(line.lstrip())
            if not in_table:
                m = lay.COLLECTED.search(line)
                if m:
                    collected = _when(m) or collected
                for field, rx in (("provider", getattr(lay, "PROVIDER", None)), ("specimen", getattr(lay, "SPECIMEN", None))):
                    m = rx and rx.search(line)
                    if m and not meta[field]:
                        meta[field] = m.group(1).strip()
                if lay.HEADER.match(line):
                    in_table, header_indent = True, indent
                continue
            end = getattr(lay, "END", None)
            if end and end.search(line):
                in_table = False
                continue
            if lay.HEADER.match(line) or any(rx.search(line) for rx in getattr(lay, "IGNORE", [])):
                continue
            cells = re.split(r"\s{2,}", stripped)
            r = lay.row(cells)
            if r:
                if not collected:
                    undated += 1
                    continue
                if indent <= header_indent + 1:   # a lone test, printed at the panels' level: its own order
                    panel = ""
                results.append({**r, "t": collected, "panel": panel})
            elif len(cells) == 1 and indent <= header_indent + 1 and lay.PANEL.fullmatch(stripped):
                panel = stripped
            else:
                unmatched.append((p, n, stripped))
    return results, meta, {"unmatched": unmatched, "undated": undated, "bad_pages": bad_pages}


def _quote(s):
    s = " ".join(s.split())
    return '"' + (s if len(s) <= 60 else s[:57] + "...") + '"'


def _report_warnings(rel, lay, problems):
    out = []
    um = problems["unmatched"]
    if um:
        shown = ", ".join(f"page {p} line {n} ({_quote(s)})" for p, n, s in um[:5]) + \
            (f" and {len(um) - 5} more" if len(um) > 5 else "")
        out.append(f"{rel}: {len(um)} line{'s' if len(um) != 1 else ''} in the results table didn't match the "
                   f"{lay.LABEL} layout and {'were' if len(um) != 1 else 'was'} left out: {shown}. Check them in the PDF.")
    if problems["undated"]:
        out.append(f"{rel}: {problems['undated']} result{'s' if problems['undated'] != 1 else ''} came before any "
                   f"collection date and {'were' if problems['undated'] != 1 else 'was'} left out.")
    if problems["bad_pages"]:
        out.append(f"{rel}: page{'s' if len(problems['bad_pages']) != 1 else ''} "
                   f"{', '.join(map(str, problems['bad_pages']))} couldn't be read and {'were' if len(problems['bad_pages']) != 1 else 'was'} left out.")
    return out


def _sha16(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _hkey(*fields):
    return NAME + ":h:" + hashlib.sha256("\x1f".join(str(f).strip().lower() for f in fields).encode()).hexdigest()[:16]


def _labels():
    return ", ".join(lay.LABEL for lay in pdf_layouts.layouts())


def _refusal(rel, pages):
    """Why a PDF was refused: no text at all (a scan), or a layout no template knows."""
    if not any((t or "").strip() for t in pages):
        return (f"{rel} has no text in it: it looks like a scan or a photo of a report. Sanguity can't read scanned "
                f"reports yet. Download the report from the lab's patient portal as a PDF instead, or type the results in.")
    return (f"{rel} isn't a lab report layout Sanguity knows. It can read reports from {_labels()}. If yours is from "
            f"one of those labs, its layout may have changed: please open an issue with an anonymised sample.")


# ------------------------------------------------------------------ parse
def parse(files):
    """Results from every lab report PDF in the upload. Raises ValueError when none can be read."""
    pdfs = _pdfs(files)
    if pdfs is None:
        raise ValueError("Lab report PDFs are read on their own: upload the PDFs without the other files.")
    if not pdfs:
        raise ValueError("The upload has no PDF in it.")
    warnings, refused, reports, seen = [], [], [], {}
    for rel, path in pdfs:
        try:
            if not _is_pdf(path):
                raise _NotRead(f"{rel} isn't a PDF, whatever its name says.")
            pages = _pages(rel, path)
        except ValueError as e:
            refused.append(str(e))
            continue
        hits = _layout_for(_lines(pages[0] if pages else ""))
        if len(hits) != 1:
            refused.append(_refusal(rel, pages) if not hits else
                           f"{rel} looks like more than one lab's layout ({', '.join(h.LABEL for h in hits)}), so it wasn't read.")
            continue
        lay = hits[0]
        digest = _sha16(path)
        if digest in seen:
            warnings.append(f"{rel} is the same file as {seen[digest]} and was read once.")
            continue
        seen[digest] = rel
        results, meta, problems = _read_report(rel, pages, lay)
        warnings += _report_warnings(rel, lay, problems)
        if not results:
            refused.append(f"{rel} looks like a {lay.LABEL} report, but no results could be read from it."
                           + ("" if problems["unmatched"] or problems["undated"] else " It has no collection date or results table."))
            continue
        reports.append({"rel": rel, "layout": lay, "results": results, "meta": meta, "digest": digest})
    if not reports:
        raise ValueError(" ".join(refused))
    warnings = [f"Not read: {r}" for r in refused] + warnings
    return {"data": _build(reports, warnings), "warnings": warnings}


def _display(t):
    return t[:10] if t[10:19] == "T12:00:00" and len(t) == 19 else t[:16].replace("T", " ")


def _unit_key(u):
    return (u or "").strip().lower().replace(" ", "").replace("µ", "u").replace("μ", "u")


def _build(reports, warnings):
    events, details = [], {}

    def add(t, kind, title, sub, detail, **extra):
        e = {"id": len(events) + 1, "t": t, "k": kind, "title": title, "sub": sub}
        e.update({k: v for k, v in extra.items() if v not in (None, "", [], 0)})
        if detail is not None:
            details[e["id"]] = detail
        events.append(e)
        return e["id"]

    # results across reports, each once: the same result in two reports of one specimen (a corrected copy) is one
    rows, dupes, seen = [], 0, set()
    for rep in reports:
        for r in rep["results"]:
            ident = (rep["layout"].NAME, r["t"], r["panel"].lower(), r["name"].lower(), r["value"], r["unit"])
            if ident in seen:
                dupes += 1
                continue
            seen.add(ident)
            # its value by the one reader of a number, as a report whose decimal mark isn't known (#870: _number, its own,
            # read "0,500" as 500 and "1.2E-3" as no number); its range cell by the one parser of a range as text (#850)
            x = value_number(r["value"])
            lo, hi, edge = range_text(r["range"], whole=True)
            # the point's flag by the one rule (common.report_flag, #817): H or L as printed; a printed "A", "Abnormal"
            # or "Critical" by the range, else kept "A"; none printed, the range's. The row keeps the flag as printed
            rows.append({**r, "rep": rep, "x": x, "lo": lo, "hi": hi, "edge": edge,
                         "dir": report_flag(r["flag"], x, lo, hi, comparator(r["value"]), edge)})
    if dupes:
        warnings.append(f"Left out {dupes} result{'s' if dupes != 1 else ''} that appeared twice (same test, time and value).")
    for rep in reports:   # a value printed as a number that can't be read ("1,250": thousands, or a decimal comma?), as
        # the spreadsheets' warnings say it (#859, #870)
        unread = [r for r in rows if r["rep"] is rep and r["x"] is None and pdf_layouts.is_number(r["value"])]
        if unread:
            n = len(unread)
            warnings.append(f"{rep['rel']}: {n} value{'s' if n != 1 else ''} couldn't be read as a number, so "
                            f"{'they were' if n != 1 else 'it was'} kept as text, not charted: "
                            + ", ".join(f"{r['name']} ({r['value']!r})" for r in unread[:5]) + (f" and {n - 5} more" if n > 5 else "") + ".")

    for rep in reports:   # the report itself, to open beside its results
        ts = sorted(r["t"] for r in rows if r["rep"] is rep)
        lay = rep["layout"]
        add(ts[0] if ts else rep["results"][0]["t"], "document", f"{lay.LABEL} lab report", rep["meta"]["provider"] or lay.LABEL,
            {"type": "file", "url": rep["rel"], "ext": "pdf"}, key=f"{NAME}:file:{rep['digest']}", src=lay.SOURCE)

    groups = collections.OrderedDict()   # (layout, specimen, time, panel) -> rows: one labitem each
    for r in sorted(rows, key=lambda r: (r["t"], r["rep"]["layout"].NAME, (r["panel"] or r["name"]).lower())):
        rep = r["rep"]
        groups.setdefault((rep["layout"].NAME, rep["meta"]["specimen"], r["t"], r["panel"] or r["name"]), []).append(r)
    days = collections.OrderedDict()
    for (lay_name, specimen, t, order), rs in groups.items():
        rep = rs[0]["rep"]
        lay, meta = rep["layout"], rep["meta"]
        key = f"{NAME}:{lay_name}:{specimen}|{order.lower()}" if specimen else _hkey(lay_name, t, order)
        det = {"type": "result", "order": order, "name": order, "collected": _display(t), "provider": meta["provider"] or None,
               "lab": lay.LABEL, "specimen": meta["specimen"] or None, "status": "Final", "dx": [],
               "rows": [[r["name"], r["value"], r["unit"], r["range"], r["flag"]] for r in rs],
               "report": [], "comments": [], "src": lay.SOURCE}
        sub = " · ".join(x for x in (lay.LABEL, meta["provider"]) if x)
        eid = add(t, "labitem", order, sub, det, key=key, abn=sum(1 for r in rs if r["flag"]), hidden=1, src=lay.SOURCE)
        days.setdefault(t[:10], []).append((eid, rs))
    for day, items in days.items():
        ids = [eid for eid, _ in items]
        rs = [r for _, group in items for r in group]
        orders = sorted({ev["title"] for ev in events if ev["id"] in ids})
        tags = sorted({r["rep"]["layout"].SOURCE for r in rs})
        add(min(r["t"] for r in rs), "lab", lab_day_title(len(rs)), lab_day_sub(orders),
            {"type": "labday", "ids": ids}, key=f"{NAME}:labday:{day}", abn=sum(1 for r in rs if r["flag"]),
            src=tags[0] if len(tags) == 1 else None)

    # series: the canonical name when lab-aliases.json knows the test, else the name as printed; a test reported in
    # two units gets a second series named with its unit
    spelling = {}   # a name lab-aliases.json doesn't know, in any letter case, is one series ("LDL" and "Ldl")
    for r in sorted(rows, key=lambda r: (r["t"], r["name"])):
        spelling.setdefault(r["name"].lower(), r["name"])
    base = {r["name"]: canonical_lab(r["name"]) or spelling[r["name"].lower()] for r in rows}
    units = collections.defaultdict(collections.Counter)
    for r in rows:
        if r["x"] is not None:
            units[base[r["name"]]][_unit_key(r["unit"])] += 1
    series = collections.OrderedDict()
    for r in sorted(rows, key=lambda r: (r["t"], r["name"].lower())):
        b = base[r["name"]]
        c = units.get(b)
        main = c.most_common(1)[0][0] if c else _unit_key(r["unit"])
        u = _unit_key(r["unit"])
        split = bool(c) and len(c) > 1
        name = b if r["x"] is None or u == main else f"{b} ({r['unit'] or 'no unit'})"
        s = series.setdefault(name, {"base": b, "unit": r["unit"] if r["x"] is not None else "", "unitk": u, "split": split,
                                     "pts": [], "txt": [], "panels": collections.Counter()})
        if r["x"] is not None:
            if not s["unit"]:
                s["unit"], s["unitk"] = r["unit"], u
            s["pts"].append([r["t"], r["x"], r["lo"], r["hi"], r["dir"], r["value"], r["rep"]["layout"].SOURCE] + ([r["edge"]] if r["edge"] else []))
        else:
            s["txt"].append([r["t"], r["value"]])
        s["panels"][r["panel"]] += 1
    labs = []
    for name, s in series.items():
        code = loinc_of(s["base"]) if canonical_lab(s["base"]) else None
        if code:
            key = f"loinc:{code}" + (f"|{s['unit'].strip()}" if s["split"] else "")
        else:
            key = f"{NAME}:{' '.join(name.split()).lower()}|{s['unit'].strip()}"
        lab = {"name": name, "key": key, "unit": s["unit"], "panel": s["panels"].most_common(1)[0][0],
               "n": len(s["pts"]), "pts": sorted(s["pts"], key=lambda p: (p[0], p[1])), "txt": sorted(s["txt"])}
        if code:
            lab["loinc"] = code
        labs.append(lab)
    labs.sort(key=lambda s: (-s["n"], s["name"]))
    events.sort(key=lambda e: (e["t"], e["id"]))
    return {"events": events, "details": details, "labs": labs, "costs": []}
