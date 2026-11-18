"""'What is this test?' links (#163): a lab's plain-language page about the test itself, from a bundled table.

lab-test-pages.json, beside lab-aliases.json, lists reviewed pages (MedlinePlus's lab test pages first), each with the
labs it's about: LOINC codes, whose names and spellings come from lab-aliases.json or condition-presets.json's labs
table, or lab-aliases.json's names for labs it has no code for, and "aka" spellings for a test neither file names.
index() turns it into what the records page reads (GET /api/lab-test-pages, routes_manual.py): {"pages": [{url,
title, site}], "codes": {LOINC code: page}, "names": {lower-case name: page}}; the page links a series by its code
first, then by its name (www/js/test-pages.js). A lab with no page has no link.

Nothing is looked up at run time: the server contacts nothing (no outside connection, #59). A link is the page's own
address and nothing else, so no value, date or anything about the person can leave with it: check() refuses an
address that isn't https on an allowed host (HOSTS), or carries a query, a fragment, a port or a user name. Pages
explain what a test measures and why it's done, never what a result means. tests/test_lab_test_pages.py runs check();
CONTRIBUTING.md says how to add a page.
"""
import functools, json, os, urllib.parse

from . import presets
from .importers import common

PAGES_FILE = os.environ.get("SANGUITY_LAB_TEST_PAGES") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), os.pardir, "lab-test-pages.json")

# the hosts a page may be on: MedlinePlus, the U.S. National Library of Medicine's plain-language health site (its lab
# test pages and Medical Encyclopedia), free of advertising and written for patients. Another reputable, non-commercial
# host joins here, with why, and in the file's "sites" with the name a link gives it.
HOSTS = ("medlineplus.gov",)
_KEYS = {"url", "title", "labs", "aka"}


class PagesError(ValueError):
    pass


def _text(v):
    return isinstance(v, str) and v.strip() == v and bool(v) and "  " not in v


def url_ok(url):
    """An https address on an allowed host, with a path and nothing else: no query, fragment, port or user name."""
    if not isinstance(url, str) or not url.isascii() or any(c.isspace() for c in url):
        return False
    u = urllib.parse.urlsplit(url)
    return (u.scheme == "https" and u.hostname in HOSTS and u.netloc == u.hostname and u.path.startswith("/")
            and not u.query and not u.fragment and "?" not in url and "#" not in url)


def spellings(doc, lab):
    """(the lab's name, its lower-case spellings) for a code or a lab-aliases.json name, or None if neither file knows it
    (a code may be one neither file names: then only the code itself, and the page's aka, match)."""
    if presets.loinc_ok(lab):
        d = presets.lab(presets.load(), lab)
        return (d["name"], d["spellings"]) if d else (None, set())
    if lab in common.lab_defs():
        return lab, {s.lower() for s in [lab, *common.lab_defs()[lab].get("aliases", [])]}
    return None


def check(doc):
    """What's wrong with a pages file, as a list of plain sentences (empty: nothing)."""
    bad = []
    if not isinstance(doc, dict) or not isinstance(doc.get("pages"), list) or not doc["pages"]:
        return ["The file needs a list of pages."]
    for k in ("_about", "_review"):
        if not _text(doc.get(k)):
            bad.append(f"The file needs its {k} text.")
    sites = doc.get("sites")
    if not isinstance(sites, dict) or set(sites) - set(HOSTS) or not all(_text(v) for v in sites.values()):
        bad.append(f"sites must name each host a page is on, only from {list(HOSTS)}.")
        sites = {}
    urls, codes, names = set(), {}, {}
    for i, p in enumerate(doc["pages"]):
        where = f"page {i + 1}"
        if not isinstance(p, dict):
            bad.append(f"{where} isn't an object.")
            continue
        where = p.get("url") or where
        if set(p) - _KEYS:
            bad.append(f"{where}: unknown keys {sorted(set(p) - _KEYS)}.")
        if not url_ok(p.get("url")):
            bad.append(f"{where}: the address must be https on {' or '.join(HOSTS)}, with no query, fragment or port.")
        elif urllib.parse.urlsplit(p["url"]).hostname not in sites:
            bad.append(f"{where}: its host needs a name in sites.")
        if p.get("url") in urls:
            bad.append(f"{where}: the page is listed twice.")
        urls.add(p.get("url"))
        if not _text(p.get("title")):
            bad.append(f"{where}: needs the page's title.")
        labs, aka = p.get("labs"), p.get("aka", [])
        if not isinstance(labs, list) or not all(isinstance(x, str) for x in labs) or not isinstance(aka, list) \
                or not all(_text(a) for a in aka) or not (labs or aka):
            bad.append(f"{where}: labs must be a list of LOINC codes or lab-aliases.json's names, aka a list of names.")
            continue
        mine = set()
        for lab in labs:
            sp = spellings(doc, lab)
            if sp is None:
                bad.append(f"{where}: {lab} isn't a LOINC code (its form or check digit) or a name in lab-aliases.json.")
                continue
            if not presets.loinc_ok(lab) and common.loinc_of(lab):
                bad.append(f"{where}: list {lab} by its code, {common.loinc_of(lab)}.")
            if lab in codes:
                bad.append(f"{where}: {lab} is also on {codes[lab]}.")
            codes[lab] = where
            mine |= sp[1]
        for a in aka:
            other = common.canonical_lab(a)
            if other and other not in {spellings(doc, x)[0] for x in labs if spellings(doc, x)}:
                bad.append(f"{where}: “{a}” is {other} in lab-aliases.json, another test.")
            mine.add(a.lower())
        for s in mine:
            if names.setdefault(s, where) != where:
                bad.append(f"{where}: “{s}” also spells a lab on {names[s]}.")
    return bad


@functools.lru_cache(maxsize=None)
def load():
    with open(PAGES_FILE, encoding="utf-8") as f:
        doc = json.load(f)
    bad = check(doc)
    if bad:
        raise PagesError("lab-test-pages.json: " + " ".join(bad))
    return doc


@functools.lru_cache(maxsize=None)
def index():
    """What the records page reads: the pages, and each code's and spelling's page by its place in the list."""
    doc = load()
    pages, codes, names = [], {}, {}
    for n, p in enumerate(doc["pages"]):
        pages.append({"url": p["url"], "title": p["title"], "site": doc["sites"][urllib.parse.urlsplit(p["url"]).hostname]})
        for lab in p["labs"]:
            name, sp = spellings(doc, lab)
            if presets.loinc_ok(lab):
                codes[lab] = n
            for s in sp:
                names[s] = n
        for a in p.get("aka", []):
            names[a.lower()] = n
    return {"pages": pages, "codes": codes, "names": names}
