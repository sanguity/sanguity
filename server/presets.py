"""Dashboard presets by condition (#186): Settings → Dashboard → "Start from a condition".

condition-presets.json, beside lab-aliases.json, holds the definitions: for each condition its id, name, body system
(the picker's groups), synonyms (the picker's search, and the problem list's suggestion), one or more published
sources, the labs commonly followed for it by LOINC code, most important first, and the readings taken at home that
go with it. A code lab-aliases.json knows takes its name and spellings from there; the file's own "labs" table names
the others, with spellings to match a series by when it has no code. check() says what's wrong with a file
(tests/test_condition_presets.py runs it); docs/condition-presets.md says how to add a condition.

offer() is what the Settings page gets: each condition with its labs matched to this profile's own series (the
series name a tile uses, or None: no results here), its home readings with the Overview tile and the visit summary
section each fills in, and conditions the problem list names (problems only, never allergies; nothing is applied
without a click). For a child's profile (under 18 by About this person's birth date) a lab marked adult is left
out, and said. Nothing is changed here: the page fills in the settings' editors, and the person saves them as any
other change, once; a later change to a preset never touches anyone's settings. Not medical advice: a preset only
chooses what's shown, "commonly followed for" a condition by its cited guideline; it never judges or suggests tests.
"""
import functools, json, os, re

from . import person, records, settings
from .importers import common

PRESETS_FILE = os.environ.get("SANGUITY_CONDITION_PRESETS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), os.pardir, "condition-presets.json")

# a preset's home readings: (what the page calls it, the Overview tile (settings.HOME_TILES) or None, the visit
# summary's section (measures.IDS))
HOME = {"bp": ("Blood pressure at home", "bp", "home_bp"), "glucose": ("Blood glucose at home", "glucose", "glucose"),
        "temp": ("Body temperature at home", "temp", "temperature"), "oxygen": ("Blood oxygen at home", None, "oxygen"),
        "weight": ("Weight and BMI", None, "bmi")}
ADULT_AGE = 18
ENDED = {"resolved", "inactive", "entered-in-error", "refuted", "ended", "completed"}   # a problem no longer current
_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_LOINC = re.compile(r"^(\d{1,7})-(\d)$")
_KEYS = {"id", "name", "system", "synonyms", "except", "sources", "labs", "home", "reviewed"}
_SOURCE_KEYS = {"publisher", "title", "year", "url"}


class PresetError(ValueError):
    pass


def loinc_ok(code):
    """A LOINC code's form and its check digit (mod 10, as LOINC computes it)."""
    m = _LOINC.match(code) if isinstance(code, str) else None
    if not m:
        return False
    total = 0
    for i, ch in enumerate(reversed(m.group(1))):
        n = int(ch) * (2 if i % 2 == 0 else 1)
        total += n - 9 if n > 9 else n
    return (10 - total % 10) % 10 == int(m.group(2))


def _text(v):
    return isinstance(v, str) and v.strip() == v and bool(v) and "  " not in v


def lab(doc, code):
    """{"name", "spellings" (lower case), "adult"} for a code, or None when neither file knows it."""
    known = common._by_loinc().get(code)
    own = (doc.get("labs") or {}).get(code) or {}
    if not known and not own.get("name"):
        return None
    name = known or own["name"]
    spell = {name, *own.get("aka", []), *(common.lab_defs().get(known, {}).get("aliases", []) if known else [])}
    return {"name": name, "spellings": {s.lower() for s in spell}, "adult": own.get("adult") is True}


def check(doc):
    """What's wrong with a definitions file, as a list of plain sentences (empty: nothing)."""
    bad = []
    if not isinstance(doc, dict) or not isinstance(doc.get("conditions"), list):
        return ["The file needs a list of conditions."]
    for k in ("_about", "_review"):
        if not _text(doc.get(k)):
            bad.append(f"The file needs its {k} text.")
    systems = doc.get("systems")
    sys_ids = [s.get("id") for s in systems if isinstance(s, dict)] if isinstance(systems, list) else []
    if not sys_ids or any(not _ID.match(str(i)) for i in sys_ids) or len(set(sys_ids)) != len(sys_ids) or \
            any(not _text(s.get("name")) for s in systems):
        bad.append("systems must be a list of {id, name}, each id once.")
    by_code = common._by_loinc()
    for code, d in (doc.get("labs") or {}).items():
        if code == "_about":
            continue
        if not loinc_ok(code):
            bad.append(f"labs: {code} isn't a LOINC code (its form or check digit).")
        if not isinstance(d, dict) or set(d) - {"name", "aka", "adult"} or not _text(d.get("name")):
            bad.append(f"labs: {code} needs a name, and only aka and adult besides.")
            continue
        if code in by_code and d["name"] != by_code[code]:
            bad.append(f"labs: {code} is {by_code[code]} in lab-aliases.json, not {d['name']}.")
        aka = d.get("aka", [])
        if not isinstance(aka, list) or not all(_text(a) for a in aka):
            bad.append(f"labs: {code}'s aka must be a list of names.")
            continue
        low = [a.lower() for a in [d["name"], *aka]]
        if len(set(low)) != len(low):
            bad.append(f"labs: {code} has a spelling twice.")
        for a in [d["name"], *aka]:
            other = common.canonical_lab(a)
            if other and common.loinc_of(other) != code:
                bad.append(f"labs: {code}'s spelling “{a}” is {other} in lab-aliases.json, another test.")
        if "adult" in d and d["adult"] is not True:
            bad.append(f"labs: {code}'s adult is true or left out.")
    spelled = {}   # every spelling of every code in the file: one code each
    seen_ids, seen_words = set(), {}
    for i, c in enumerate(doc["conditions"]):
        where = f"condition {i + 1}"
        if not isinstance(c, dict):
            bad.append(f"{where} isn't an object.")
            continue
        where = c.get("id") or where
        if set(c) - _KEYS:
            bad.append(f"{where}: unknown keys {sorted(set(c) - _KEYS)}.")
        if not isinstance(c.get("id"), str) or not _ID.match(c["id"]):
            bad.append(f"{where}: the id must be lower case words joined by hyphens.")
        elif c["id"] in seen_ids:
            bad.append(f"{where}: the id is used twice.")
        seen_ids.add(c.get("id"))
        if not _text(c.get("name")):
            bad.append(f"{where}: needs a name.")
        if c.get("system") not in sys_ids:
            bad.append(f"{where}: its system must be one of {sys_ids}.")
        words = [c.get("name"), *(c.get("synonyms") or [])]
        if not isinstance(c.get("synonyms"), list) or not all(_text(w) for w in words):
            bad.append(f"{where}: synonyms must be a list of names.")
        else:
            for w in words:
                if w.lower() in seen_words:
                    bad.append(f"{where}: “{w}” is also {seen_words[w.lower()]}'s name or synonym.")
                seen_words[w.lower()] = where
        if "except" in c and (not isinstance(c["except"], list) or not all(_text(w) for w in c["except"])):
            bad.append(f"{where}: except must be a list of names.")
        src = c.get("sources")
        if not isinstance(src, list) or not src:
            bad.append(f"{where}: needs a source (a published guideline) at least.")
        else:
            for s in src:
                if not isinstance(s, dict) or set(s) != _SOURCE_KEYS or not all(_text(s.get(k)) for k in ("publisher", "title", "url")) \
                        or not isinstance(s.get("year"), int) or not 1990 <= s["year"] <= 2100 or not s["url"].startswith("https://"):
                    bad.append(f"{where}: each source needs its publisher, title, year and an https address.")
        labs = c.get("labs")
        if not isinstance(labs, list) or not labs:
            bad.append(f"{where}: needs its labs, by LOINC code.")
            labs = []
        if len(set(map(str, labs))) != len(labs):
            bad.append(f"{where}: a lab is listed twice.")
        names = {}
        for code in labs:
            d = lab(doc, code) if isinstance(code, str) else None
            if not loinc_ok(code):
                bad.append(f"{where}: {code} isn't a LOINC code (its form or check digit).")
            elif d is None:
                bad.append(f"{where}: lab-aliases.json doesn't know {code}; name it in the file's labs.")
            else:
                if d["name"] in names:
                    bad.append(f"{where}: {code} and {names[d['name']]} are both {d['name']}.")
                names[d["name"]] = code
                for sp in d["spellings"]:
                    if spelled.setdefault(sp, code) != code:
                        bad.append(f"{where}: “{sp}” spells both {code} and {spelled[sp]}.")
        home = c.get("home")
        if not isinstance(home, list) or set(home) - set(HOME) or len(set(home)) != len(home):
            bad.append(f"{where}: home must list some of {sorted(HOME)}, each once.")
        if not isinstance(c.get("reviewed"), bool):
            bad.append(f"{where}: reviewed must be true (a clinician checked it) or false.")
    return bad


@functools.lru_cache(maxsize=None)
def load():
    with open(PRESETS_FILE, encoding="utf-8") as f:
        doc = json.load(f)
    bad = check(doc)
    if bad:
        raise PresetError("condition-presets.json: " + " ".join(bad))
    return doc


# ---------------------------------------------------------------- one profile's view of them

def _code(s):
    """A series' LOINC code: its own, or the one in a "loinc:<code>" source key (as crossmatch.py reads it)."""
    c = str(s.get("loinc") or "").strip()
    if not c and isinstance(s.get("key"), str) and s["key"].startswith("loinc:"):
        c = s["key"][6:].split("|", 1)[0].strip()
    return c or None


def match(series, code, d):
    """The profile's series for a lab (its name), or None: by its code, then lab-aliases.json's name for it, then a
    spelling. The one with the most results when several fit (a second unit's "Name (unit)" series, say)."""
    hits = []
    for s in series:
        name = str(s.get("name") or "")
        sc = _code(s)
        if sc == code or (common.canonical_lab(name, sc) or "") == d["name"] or name.lower() in d["spellings"]:
            hits.append(s)
    return max(hits, key=lambda s: len(s.get("pts") or []))["name"] if hits else None


def _said(term, text):
    return re.search(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])", text) is not None


def suggestions(doc, problems):
    """[{"id", "problem"}]: each condition a current problem names (by its name or a synonym), once, with the first
    such problem's name. Allergies (type "Allergy", #179) and problems resolved or inactive are never read."""
    out = []
    current = [p for p in problems or [] if isinstance(p, dict) and isinstance(p.get("name"), str)
               and not str(p.get("type") or "").lower().startswith("allerg")
               and str(p.get("status") or "").strip().lower() not in ENDED]
    for c in doc["conditions"]:
        for p in current:
            text = p["name"].lower()
            if any(_said(w, text) for w in [c["name"], *c["synonyms"]]) and not any(_said(w, text) for w in c.get("except", [])):
                out.append({"id": c["id"], "problem": p["name"]})
                break
    return out


def offer(db, pid, uid):
    """What Settings → Dashboard's "Start from a condition" shows for this profile (see the module's doc)."""
    doc = load()
    body = records.served(db, pid)
    data = json.loads(body) if body else {}
    series = [s for s in data.get("labs") or [] if isinstance(s, dict) and not s.get("watch") and s.get("pts")]
    cfg = settings.merged(db, pid, uid)
    age = person.age_on(person.of(cfg)["birthDate"], settings.local_now(db, uid).date().isoformat())
    child = age is not None and age < ADULT_AGE
    out = []
    for c in doc["conditions"]:
        labs, left = [], []
        for code in c["labs"]:
            d = lab(doc, code)
            if child and d["adult"]:
                left.append(d["name"])
            else:
                labs.append({"code": code, "name": d["name"], "series": match(series, code, d)})
        out.append({"id": c["id"], "name": c["name"], "system": c["system"], "synonyms": c["synonyms"],
                    "sources": c["sources"], "reviewed": c["reviewed"], "labs": labs, "adultOnly": left,
                    "home": [{"id": h, "label": HOME[h][0], "tile": HOME[h][1], "section": HOME[h][2]} for h in c["home"]]})
    return {"about": doc["_about"], "review": doc["_review"], "systems": doc["systems"], "child": child,
            "conditions": out, "suggest": suggestions(doc, data.get("problems"))}
