"""Lab names lab-aliases.json doesn't know, matched to a series during import (#23), so the same test from another
lab lands in the same series on Trends.

Importers name a lab series with common.canonical_lab: lab-aliases.json's name for its LOINC code or spelling, else
the name as reported. For each series of an import whose name is neither canonical nor already one of the profile's
series, the preview suggests a series to put it in:

  1. the profile's own series, then lab-aliases.json's names, with the same LOINC code      "same LOINC code"
  2. then the same name once case, punctuation and common abbreviations are evened out      "same name written another way, same unit"
  3. then names alike once evened out                                                      "similar name, same unit"

and only where the units agree (common.unit_for); a name from lab-aliases.json the profile doesn't chart yet has no
unit to compare, and says so. Only 1 and 2 are "sure", put in the box for the person to confirm; a similar name (3)
is a "Maybe" beside an empty box, chosen only by hand (#546: "Vitamin B6" was filed under "Vitamin B1" as if sure,
for every profile), so names differing by a number or a letter (B6 and B1, Factor 3 and Factor 1, IgG and IgA) are
never matched unasked. The person confirms the match, picks another series, or keeps it as a series of its
own; confirm sends {"labs": {"s:<series index>": "<series name>" | null}} and the import is saved with those names.
Only the series' name changes: its source key (LOINC or source based) stays, so duplicates are still found by it, and
the results' own rows (a lab day's details) keep the name as reported.

Each choice is remembered (table labmaps, schema version 16), keyed by the importer and the name, unit and LOINC code
as reported, normalised: "keep as a series of its own" too (target NULL), so the next import doesn't ask again. A
review's choice is its profile's (#927, schema version 41: labmaps.profile_id, and its lookup keyed with the profile):
applied to that profile's later imports only, never to another person's, and only a series the review offered or a
test Sanguity's list knows (offered, check_sent: never any text sent). One an admin made before is for every
profile (profile_id NULL), and stays so; a profile's own is used before it. Remembered choices are applied as soon as
an import is parsed, before its duplicates are found, to a pull (sync.py) and to results typed in (routes_manual.py);
the preview shows them as "matched as before", and changing one there changes that import only. A profile's owners and
editors change or remove its own on Settings, and make them there or from Labs, re-filing results already saved
(#1097: set_matches, below; a name Sanguity knows too, once matched); admins, those for every profile
(routes_labmaps.py). Undoing the import whose review made a choice forgets it, unless another import has been saved
with it since or someone has changed it (labmaps.import_id and used, schema version 36; history.undo, #500); and
deleting a profile forgets those its reviews made (forget_profile, #591).

The name, unit and code (source) and the target are health details, so encrypted (crypto.enc); rows are found by
lookup, a keyed hash of the normalised identity (security.pepper_mac), never by decrypting them all.

Before #546 a look-alike (Vitamin B6 for Vitamin B1) could be filled in as sure and remembered on Add. A choice
remembered then (checked NULL, schema version 38) that #546's rule wouldn't make (fits: not the same name, an alias,
the list's LOINC code or the same name written another way) is "to check" (#569): never applied, a review that would
use it shows it as a "Maybe", and Admin, Lab name matches lists it first with Keep and Forget. A choice made since,
by hand or by the rule, and one an admin keeps or changes, is checked; so is one an admin had changed before the
upgrade (#575), and a review's choice for a name whose match is to check replaces it. A preview planned before a
match was to check, forgotten or changed doesn't apply it when it's saved, and plans again when it's read (#583:
recheck, and jobs._stamp, which has rev, counting every change to labmaps). A name's specimen (blood,
serum, urine...) is part of it: "Ketones, Blood" isn't "Ketones" written another way (SPECIMEN).
"""
import collections, copy, difflib, json, re

from . import crypto, db as dbm, security as sec
from .importers import common

SUGGESTIONS = 3            # per series, at most
SIMILAR = 0.82             # difflib ratio of the evened-out names
NAME_MAX = 120             # a target series name, at most

# spellings labs shorten tests to, and words that don't tell two tests apart
ABBREV = {"hgb": "hemoglobin", "hb": "hemoglobin", "hct": "hematocrit", "plt": "platelets", "plts": "platelets",
          "platelet": "platelets", "wbc": "white blood cells", "rbc": "red blood cells", "vit": "vitamin",
          "abs": "absolute", "absol": "absolute", "tot": "total", "chol": "cholesterol", "gluc": "glucose",
          "creat": "creatinine", "crea": "creatinine", "alk": "alkaline", "phos": "phosphatase", "bili": "bilirubin",
          "oh": "hydroxy", "25oh": "25 hydroxy", "ferr": "ferritin", "trig": "triglycerides", "trigs": "triglycerides",
          "mag": "magnesium", "mg": "magnesium", "k": "potassium", "na": "sodium", "cl": "chloride", "ca": "calcium",
          "alb": "albumin", "pct": "%", "percent": "%", "lymph": "lymphocytes", "lymphs": "lymphocytes",
          "mono": "monocytes", "monos": "monocytes", "eos": "eosinophils", "baso": "basophils", "neut": "neutrophils",
          "neuts": "neutrophils", "cnt": "count", "ct": "count"}
FILLER = {"serum", "plasma", "ser", "plas", "bld", "blood", "level", "lvl", "whole", "quant", "quantitative", "result",
          "test", "s", "p", "b", "ext", "external"}
# what a test was done on: left out when names are compared for a "Maybe", but a name with one is the same name
# written another way only as a name with the same (#569: "Ketones, Blood" may be another series than "Ketones")
SPECIMEN = {"serum": "serum", "ser": "serum", "plasma": "plasma", "plas": "plasma", "blood": "blood", "bld": "blood",
            "whole": "blood", "urine": "urine", "ur": "urine", "urn": "urine", "csf": "csf", "spinal": "csf",
            "saliva": "saliva", "stool": "stool", "fecal": "stool", "feces": "stool"}


# ---------------------------------------------------------------- names and identities

def norm(name):
    """Lower case, punctuation as spaces, runs of spaces as one: how a reported name is remembered."""
    return " ".join(re.sub(r"[^\w%]+", " ", str(name or "").lower()).split())


def even(name):
    """norm, with common abbreviations spelled out and filler words left out: for comparing names."""
    words = []
    for w in norm(name).replace("_", " ").split():
        words += ABBREV.get(w, w).split()
    kept = [w for w in words if w not in FILLER]
    return " ".join(kept or words)


def series_loinc(s):
    """A series' LOINC code: its own "loinc", else the one its source key names ("loinc:718-7")."""
    code = s.get("loinc")
    if not code and isinstance(s.get("key"), str) and s["key"].startswith("loinc:"):
        code = s["key"][6:]
    return str(code).strip() if code else ""


def specimens(name):
    """The specimens a name says it was measured in ("blood" in "Red blood cells" names the cells, not a specimen)."""
    words = norm(name).replace("_", " ").split()
    return {SPECIMEN[w] for i, w in enumerate(words) if w in SPECIMEN and words[i + 1:i + 2] not in (["cells"], ["cell"])}


def same_name(a, b):
    """Whether two names are one test written another way (#546): evened out alike, no single letter left out of
    one as a filler word ("Vitamin B" isn't "Vitamin"), and the same specimens (#569)."""
    def letters(n):
        return {w for w in norm(n).replace("_", " ").split() if len(w) == 1}
    return even(a) == even(b) and letters(a) == letters(b) and specimens(a) == specimens(b)


def fits(name, loinc, target):
    """Whether #546's rule would match name to target unasked, without a profile to look in: kept separate, the same
    name, an alias or the list's LOINC code for it, or the same name written another way. A remembered choice that
    doesn't fit and wasn't checked is "to check" (#569)."""
    code = str(loinc or "").strip()
    return (target is None or norm(name) == norm(target) or common.canonical_lab(name, code) == target
            or bool(code and common.loinc_of(target) == code) or same_name(name, target))


def compatible(target, u1, u2):
    return common.unit_for(target, u1) == common.unit_for(target, u2)


def lookup(importer, name, unit, loinc, pid=None):
    """The keyed hash a remembered choice is found by: a profile's own (#927) keyed with the profile too."""
    ident = [str(importer), norm(name), common.unit_norm(unit), str(loinc or "").strip()] + ([int(pid)] if pid is not None else [])
    return sec.pepper_mac(b"sanguity lab map\x00" + json.dumps(ident, ensure_ascii=False).encode("utf-8")).hex()[:32]


def _known(name):
    return bool(common.lab_alias().get(str(name or "").strip().lower()))


# ---------------------------------------------------------------- what's there to match to

def profile_series(db, pid):
    """The profile's lab series: {name: {"unit": the unit most of its rows have, "loinc": {codes}}}."""
    units, codes = collections.defaultdict(collections.Counter), collections.defaultdict(set)
    shown = charted(db, pid)   # as the records chart them (#1097: a series re-filed under another test is that test's)
    for r in db.execute("SELECT id, body FROM series WHERE profile_id=?", (pid,)):
        try:
            s = json.loads(crypto.dec(r["body"]))
        except (ValueError, TypeError):
            continue
        if not isinstance(s, dict) or not isinstance(s.get("name"), str):
            continue
        name = shown.get(r["id"], s["name"])
        units[name][str(s.get("unit") or "")] += 1
        if series_loinc(s):
            codes[name].add(series_loinc(s))
    return {n: {"unit": u.most_common(1)[0][0], "loinc": codes[n]} for n, u in units.items()}


def targets(have):
    """Every series a lab can be put in, for the preview's list: the profile's (with their units), then
    lab-aliases.json's names it doesn't chart yet."""
    out = [{"name": n, "unit": v["unit"]} for n, v in sorted(have.items(), key=lambda x: x[0].lower())]
    out += [{"name": n, "unit": ""} for n in sorted(common.lab_defs(), key=str.lower) if n not in have]
    return out


def suggest(s, have):
    """Up to SUGGESTIONS series for s: [{"name", "unit", "reason", "sure"}], best first. sure: the same LOINC code, or
    the same name written another way (same_name), with units that agree, so the preview puts it in the box; a
    similar name is never sure (#546), only offered."""
    name, unit, code = str(s.get("name") or ""), str(s.get("unit") or ""), series_loinc(s)
    out, seen = [], set()

    def add(target, t_unit, reason, sure=True):
        if target not in seen and target != name:
            seen.add(target)
            out.append({"name": target, "unit": t_unit, "reason": reason, "sure": sure})

    if code:
        for n, v in sorted(have.items()):
            if code in v["loinc"] and compatible(n, v["unit"], unit):
                add(n, v["unit"], "same LOINC code")
        for n, d in common.lab_defs().items():
            if d.get("loinc") == code:
                if n in have:
                    if compatible(n, have[n]["unit"], unit):
                        add(n, have[n]["unit"], "same LOINC code")
                else:
                    add(n, "", "same LOINC code (from Sanguity's list of tests; no unit to compare)")
    mine = even(name)
    alike = []
    for n, v in have.items():
        if not compatible(n, v["unit"], unit):
            continue
        if same_name(name, n):
            alike.append((2, 0, n, v["unit"], "same name written another way, same unit"))
        elif (r := difflib.SequenceMatcher(None, mine, even(n)).ratio()) >= SIMILAR:
            alike.append((r, 1, n, v["unit"], "similar name, same unit"))
    for n, d in common.lab_defs().items():
        if n in have:
            continue
        r = max(difflib.SequenceMatcher(None, mine, even(a)).ratio() for a in [n] + d.get("aliases", []))
        if r >= SIMILAR:
            alike.append((r, 1, n, "", "similar name (from Sanguity's list of tests; no unit to compare)"))
    for _, weak, n, u, why in sorted(alike, key=lambda x: (-x[0], x[1], x[2])):
        add(n, u, why, not weak)
    return out[:SUGGESTIONS]


# ---------------------------------------------------------------- an import's plan

def plan(db, pid, importer, data, keep=()):
    """What the preview asks about data's lab series, and what's settled already:

      {"listed": [{"ref", "name", "unit", "loinc", "n", "suggestions", "before": {"target"} | None, ("check": {"target"})}],
       "auto": {ref: target or None}   remembered choices, applied without asking
       "ids": {ref: labmaps id}        theirs, marked used once the import is saved (used, #500)
       "targets": [{"name", "unit"}]   every series one can be put in (only when something is listed)}

    A series is listed when lab-aliases.json doesn't know its name and the profile has no series of that name (or
    when a choice for it was remembered). keep: refs listed even so, a preview read again asking about what it asked
    before (jobs.freshen, #497), so a choice typed in its review isn't lost; such an entry names the series of its
    name ("same") the profile has by now. A remembered choice to check (#569) isn't applied: its entry has "check"
    instead of "before", offered as a "Maybe"."""
    have = profile_series(db, pid)
    lower = {n.lower(): n for n in have}
    candidates = {}
    for i, s in enumerate(data.get("labs") or []):
        if isinstance(s, dict) and isinstance(s.get("name"), str) and s["name"].strip():
            candidates[f"s:{i}"] = s
    found = remembered(db, pid, [importer], {ref: _ident(s) for ref, s in candidates.items()}, flagged=True)
    listed, auto, ids = [], {}, {}
    for ref, s in candidates.items():
        before, same = found.get(ref), lower.get(s["name"].strip().lower())
        if before is None and (_known(s["name"]) or same and ref not in keep):   # (#1097: a name Sanguity knows, only once matched)
            continue
        check = before if before and before["check"] else None
        before = None if check else before
        entry = {"ref": ref, "name": s["name"], "unit": str(s.get("unit") or ""), "loinc": series_loinc(s),
                 "n": len(s.get("pts") or []) + len(s.get("txt") or []), "suggestions": suggest(s, have), "before": None}
        if before is None and same:
            entry["same"] = same   # (kept: left as a series of its own, it joins that one)
        if check:
            entry["check"] = {"target": check["target"]}
        if before is not None:
            entry["before"] = {"target": before["target"]}
            auto[ref], ids[ref] = before["target"], before["id"]
        listed.append(entry)
    return {"listed": listed, "auto": auto, "ids": ids, "targets": targets(have) if listed else []}


def _ident(s):
    return (str(s.get("name") or ""), str(s.get("unit") or ""), series_loinc(s))


def choices(lab_plan, sent):
    """The names to save the import's series under: the remembered ones, overridden by what was sent for series the
    preview listed, each one it offered (#927; one it didn't is left out). A series the preview listed but nothing
    was sent for keeps its own name."""
    out = dict((lab_plan or {}).get("auto") or {})
    listed = {e["ref"]: e for e in (lab_plan or {}).get("listed") or []}
    for ref, target in (sent or {}).items():
        if ref in listed:
            ok, target = _chosen(listed[ref], (lab_plan or {}).get("targets"), target)
            if ok:
                out[ref] = target
    return out


class NotOffered(ValueError):
    """A review's choice of a series it didn't offer (#927)."""


def offered(entry, targets):
    """{lower-case name: name} of the series a review offered for a listed series (#927): its own name, the match
    made before or to check, its suggestions, every series in the list (the profile's, then Sanguity's list of tests
    it doesn't chart yet), and any test that list knows."""
    names = [entry.get("name")] + [x.get("target") for x in (entry.get("before"), entry.get("check")) if x]
    names += [x.get("name") for x in entry.get("suggestions") or []] + [entry.get("same")] + [t.get("name") for t in targets or []]
    names += sorted(set(common.lab_alias().values()))
    out = {}
    for n in names:
        if isinstance(n, str) and n.strip():
            out.setdefault(n.lower(), n)
    return out


def _chosen(entry, targets, target):
    """(whether the review offered it, the name in its own spelling); None (a series of its own) is always offered."""
    target = clean_target(target)
    if target is None:
        return True, None
    named = offered(entry, targets).get(target.lower())
    return named is not None, named


def check_sent(listed, targets, sent):
    """The choices sent with a confirm, checked against what the review offered (its listed series and its list of
    targets, as the job's summary has them): {ref: name in its own spelling, or None}. Raises NotOffered."""
    by = {e["ref"]: e for e in listed or [] if isinstance(e, dict)}
    out = {}
    for ref, target in (sent or {}).items():
        e = by.get(ref)
        ok, named = _chosen(e, targets, target) if e else (True, clean_target(target))
        if not ok:
            raise NotOffered(f"“{clean_target(target)}” isn't a series this review offered for {e['name']}. Pick one "
                             "from its list, or leave it empty to keep it a series of its own.")
        out[ref] = named
    return out


def clean_target(target):
    if target is None:
        return None
    t = " ".join(str(target).split())[:NAME_MAX]
    return t or None


def apply(data, picked):
    """A copy of data with its lab series renamed as picked ({ref: target}; None keeps the name). Only the series'
    name changes."""
    if not any(picked.values()):
        return data
    out = dict(data)
    labs = []
    for i, s in enumerate(data.get("labs") or []):
        t = picked.get(f"s:{i}")
        # (#1097) the name as reported kept beside it, so the series is found by its match again (covered)
        labs.append({**copy.deepcopy(s), "name": t, "reported": s.get("reported") or s.get("name")}
                    if t and isinstance(s, dict) and t != s.get("name") else s)
    out["labs"] = labs
    return out


def remember(db, pid, importer, lab_plan, sent, data, who, import_id=None):
    """Keep the choices sent for series the preview listed and nothing was remembered for, as profile pid's own
    (#927), each a series the review offered. Inside the import's transaction. A choice the profile remembered
    meanwhile (by another of its imports, or an admin's change of it) wins. A choice for a name whose match of the
    profile's own is to check replaces it (#575: the review says it's used for later imports), unless an admin has
    kept or changed it since; one for every profile to check stays for admins to check. Returns how many were kept.
    Made by hand or by #546's rule, they're checked (#569). import_id: the import saved with them, whose Undo forgets
    them (forget, #500)."""
    listed = {e["ref"]: e for e in (lab_plan or {}).get("listed") or [] if not e.get("before")}
    labs = data.get("labs") or []
    n = 0
    for ref, target in (sent or {}).items():
        e = listed.get(ref)
        if not e or not ref[2:].isdigit() or int(ref[2:]) >= len(labs):
            continue
        ok, target = _chosen(e, lab_plan.get("targets"), target)
        if not ok:
            continue
        name, unit, code = _ident(labs[int(ref[2:])])
        n += db.execute("""INSERT INTO labmaps(lookup, importer, source, target, created_by, created_name, created, import_id,
                                              checked, profile_id)
                           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(lookup) """ + (_REPLACE if e.get("check") else "DO NOTHING"),
                        (lookup(importer, name, unit, code, pid), importer,
                         crypto.enc(json.dumps({"name": name, "unit": unit, "loinc": code}, ensure_ascii=False)),
                         _enc_target(target), who["id"] if who else None,
                         who["username"] if who else "removed account", dbm.now(), import_id, dbm.now(), pid)).rowcount
    if n:
        _changed(db)
    return n


# a review's choice in place of a match to check (#575), not one an admin has kept or changed meanwhile
_REPLACE = """DO UPDATE SET source=excluded.source, target=excluded.target, created_by=excluded.created_by,
              created_name=excluded.created_name, created=excluded.created, import_id=excluded.import_id, used=NULL,
              checked=excluded.checked WHERE labmaps.checked IS NULL"""


def recheck(db, pid, lab_plan):
    """A stored plan (a preview's, planned when it was read) with its remembered choices checked against labmaps as
    they are now: one is applied only while its row is there, isn't to check, goes to the same series (#583) and is
    profile pid's or every profile's (#927: one planned before the upgrade to schema version 41 may be another's). One
    forgotten or changed since (an admin's Forget or Change) is out of auto and ids, so its series keeps its own name
    unless a choice was sent for it, and it isn't marked used; its entry keeps "before", so what's sent for it is for
    this import only, as the review said. One now to check (#569; as after the upgrade to schema version 38) is offered,
    "check" in place of "before", as plan() would have it (#575)."""
    ids = (lab_plan or {}).get("ids") or {}
    if not ids:
        return lab_plan
    rows = {r["id"]: _row(r) for r in db.execute(f"SELECT * FROM labmaps WHERE id IN ({','.join('?' * len(ids))})",
                                                [int(i) for i in ids.values()])}
    auto = lab_plan.get("auto") or {}
    gone = {ref for ref, i in ids.items() if int(i) not in rows or rows[int(i)]["target"] != auto.get(ref)
            or rows[int(i)]["for"] not in (None, pid)}
    flagged = {ref for ref, i in ids.items() if ref not in gone and rows[int(i)]["check"]}
    if not gone and not flagged:
        return lab_plan
    out = copy.deepcopy(lab_plan)
    for ref in gone | flagged:
        out["auto"].pop(ref, None)
        del out["ids"][ref]
    for e in out.get("listed") or []:
        if e["ref"] in flagged and e.get("before"):
            e["check"], e["before"] = e["before"], None
    return out


def applied(lab_plan, sent):
    """The ids of the remembered choices an import is saved with: those not changed for it (choices)."""
    auto, ids, final = (lab_plan or {}).get("auto") or {}, (lab_plan or {}).get("ids") or {}, choices(lab_plan, sent)
    return [ids[ref] for ref in ids if ref in auto and final.get(ref) == auto[ref]]


def used(db, ids):
    """Mark remembered choices another import was saved with (inside its transaction): the import that made them
    no longer takes them away when it's undone (forget)."""
    ids = sorted({int(i) for i in ids or ()})
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        db.execute(f"UPDATE labmaps SET used=? WHERE id IN ({','.join('?' * len(part))})", (dbm.now(), *part))


# what an import's Undo forgets (#500): the choices its review made, unless another import has been saved with them
# since or an admin has changed them
_OWN = "FROM labmaps WHERE import_id=? AND used IS NULL AND changed IS NULL"


def forgettable(db, import_id):
    """How many remembered choices undoing the import would forget, for its Undo's question."""
    return db.execute("SELECT COUNT(*) " + _OWN, (import_id,)).fetchone()[0]


def forget(db, import_id):
    """Forget them, inside the Undo's transaction, so the next import of those names asks again. Returns how many."""
    n = db.execute("DELETE " + _OWN, (import_id,)).rowcount
    if n:
        _changed(db)
    return n


def forget_profile(db, pid):
    """Forget the choices a profile's reviews made (#591), its own (#927: applied to nobody else, an admin's change of
    one too), inside its delete's transaction, so they're no longer listed on Admin with the name as reported and who
    confirmed it. Returns how many. (The profile's row going would take them too: profile_id cascades.)"""
    n = db.execute("DELETE FROM labmaps WHERE profile_id=?", (pid,)).rowcount
    if n:
        _changed(db)
    return n


# every change to labmaps but used, counted in the meta table (#583): a review waiting since plans again (jobs._stamp)
REV = "labmaps_rev"


def _changed(db):
    db.execute("INSERT INTO meta(k, v) VALUES(?, '1') ON CONFLICT(k) DO UPDATE SET v = CAST(v AS INTEGER) + 1", (REV,))


def rev(db):
    """How many times labmaps has changed: a choice remembered, kept, changed or forgotten."""
    return int(dbm.meta_get(db, REV) or 0)


def _enc_target(t):
    return None if t is None else crypto.enc(t)


def remembered(db, pid, importers, idents, flagged=False):
    """{ref: {"id", "target", "check"}} for the identities ({ref: (name, unit, loinc)}) a choice was remembered for
    in profile pid, under the first of importers that has one: the profile's own (#927), else one for every profile.
    One to check (#569: never checked, and #546's rule wouldn't make it) is left out unless flagged."""
    if not idents:
        return {}
    keys = {(ref, imp, own): lookup(imp, *ident, pid if own else None) for ref, ident in idents.items() for imp in importers
            for own in (True, False)}
    rows = {}
    hashes = sorted(set(keys.values()))
    for i in range(0, len(hashes), 500):
        part = hashes[i:i + 500]
        for r in db.execute(f"SELECT id, lookup, target, checked, profile_id FROM labmaps WHERE lookup IN ({','.join('?' * len(part))})",
                            part):
            if r["profile_id"] in (None, pid):   # (another's never: its lookup is keyed with its profile)
                rows[r["lookup"]] = r
    out = {}
    for ref in idents:
        for imp in importers:
            r = rows.get(keys[(ref, imp, True)]) or rows.get(keys[(ref, imp, False)])
            if r is not None:
                target = crypto.dec(r["target"]) if r["target"] else None
                check = r["checked"] is None and not fits(idents[ref][0], idents[ref][2], target)
                if flagged or not check:
                    out[ref] = {"id": r["id"], "target": target, "check": check}
                break
    return out


def apply_remembered(db, pid, importers, data):
    """data with profile pid's remembered choices applied to its series (hand entry): (data, {old name: new name},
    [their ids], marked used once it's saved)."""
    idents = {f"s:{i}": _ident(s) for i, s in enumerate(data.get("labs") or [])
              if isinstance(s, dict) and isinstance(s.get("name"), str)}   # (#1097: a known name too, once matched)
    found = remembered(db, pid, importers, idents)
    picked = {ref: f["target"] for ref, f in found.items()}
    renamed = {idents[ref][0]: t for ref, t in picked.items() if t}
    return apply(data, picked), renamed, [f["id"] for f in found.values()]


# ---------------------------------------------------------------- the list, for admins

def _row(r):
    """A labmaps row read; "for": the profile whose own it is, None for every profile (#927)."""
    src = json.loads(crypto.dec(r["source"]))
    target = crypto.dec(r["target"]) if r["target"] else None
    return {"id": r["id"], "importer": r["importer"], "name": src.get("name"), "unit": src.get("unit") or "",
            "loinc": src.get("loinc") or "", "target": target,
            "check": r["checked"] is None and not fits(src.get("name"), src.get("loinc"), target),
            "one_profile": r["profile_id"] is not None,   # (#927: which one isn't said)
            "for": r["profile_id"],
            "created": r["created"], "created_by": r["created_name"], "changed": r["changed"], "changed_by": r["changed_name"]}


def _shown(m):
    """A match as admins see it: without which profile is its own."""
    return {k: v for k, v in m.items() if k != "for"}


def listing(db, pid=None):
    """The remembered choices for every profile (pid None: Install's, for admins), else profile pid's own (#1097),
    those to check (#569) first, then last changed first: [{"id", "importer", "name", "unit", "loinc", "target",
    "check", "one_profile", "created", "created_by", "changed", "changed_by"}]."""
    rows = [_shown(_row(r)) for r in db.execute("SELECT * FROM labmaps WHERE profile_id IS ? ORDER BY IFNULL(changed, created) DESC, id DESC",
                                                (pid,))]
    return sorted(rows, key=lambda m: not m["check"])


def get(db, mid, pid=None):
    """A match for every profile (pid None), else profile pid's own (#1097); None if it isn't one."""
    r = db.execute("SELECT * FROM labmaps WHERE id=? AND profile_id IS ?", (mid, pid)).fetchone()
    return _shown(_row(r)) if r else None


SCOPE_OLD = "labmaps_scope"   # meta, set by schema step 41: scope_old hasn't run yet


def scope_old(db):
    """At start-up, once (#927): the choices reviews made before schema version 41, given their import's profile by
    that step, keyed with it, so they're found for that profile only (the lookup needs the pepper, which the step
    hasn't). Returns how many."""
    if not dbm.meta_get(db, SCOPE_OLD):
        return 0
    n = 0
    with dbm.transaction(db):
        for r in db.execute("SELECT id, lookup, importer, source, profile_id FROM labmaps WHERE profile_id IS NOT NULL").fetchall():
            src = json.loads(crypto.dec(r["source"]))
            key = lookup(r["importer"], src.get("name"), src.get("unit"), src.get("loinc"), r["profile_id"])
            if key != r["lookup"]:
                n += db.execute("UPDATE labmaps SET lookup=? WHERE id=?", (key, r["id"])).rowcount
        db.execute("DELETE FROM meta WHERE k=?", (SCOPE_OLD,))
        _changed(db)   # a review waiting since plans again (recheck also leaves another profile's out)
    return n


def change(db, mid, target, who):
    """An admin's change, or Keep (the same target): checked from now on (#569)."""
    db.execute("UPDATE labmaps SET target=?, changed_by=?, changed_name=?, changed=?, checked=? WHERE id=?",
               (_enc_target(clean_target(target)), who["id"], who["username"], dbm.now(), dbm.now(), mid))
    _changed(db)


def remove(db, mid):
    db.execute("DELETE FROM labmaps WHERE id=?", (mid,))
    _changed(db)


# ---------------------------------------------------------------- a profile's own, on Settings and from Labs (#1097)
# Owners and editors see their profile's own matches (Settings → This person → Labs and ranges), and make, change and
# remove them there, or from a lab's chart (Same test as…). One rule wherever it's made, the same saved match as a
# review's: set_matches() matches the name as a lab reported it (each importer, unit and code it came with) to a test
# of the profile's, or keeps it as a test of its own (target None), for later imports, pulls and results typed in;
# and, asked to (move), re-files the results already saved under it: the series it covers (covered) are charted
# under that test from then on. A re-file is a change of the profile's (labmap_changes, schema version 48) applied
# as the records are put together (charted, records._assemble), never written into them: the records keep the name
# each import gave them, an import's Undo takes its own series away with it, and a change's Undo (undo) puts the
# matches back as they were and stops its re-filing, exactly. Removing a match leaves results where they're charted.

KEEP_FOR = 7 * 86400   # a change that re-filed nothing is kept this long, for its Undo, then forgotten


class Refused(ValueError):
    """A change that can't be made: a target that isn't one of the profile's tests, or an Undo of a match changed since."""


def charted(db, pid):
    """{series id: the name it's charted under} of profile pid's re-filed series: every change's, the latest last."""
    out = {}
    for r in db.execute("SELECT value FROM labmap_changes WHERE profile_id=? AND series>0 ORDER BY id", (pid,)):
        for sid, name in (json.loads(crypto.dec(r["value"])).get("series") or {}).items():
            out[int(sid)] = name
    return out


def _series(db, pid):
    """The profile's saved series: [(series id, import id, its importer, its body)]."""
    out = []
    for r in db.execute("""SELECT s.id, s.import_id, s.body, i.format FROM series s JOIN imports i ON i.id = s.import_id
                           WHERE s.profile_id=? ORDER BY s.id""", (pid,)):
        try:
            b = json.loads(crypto.dec(r["body"]))
        except (ValueError, TypeError):
            continue
        if isinstance(b, dict) and isinstance(b.get("name"), str):
            out.append((r["id"], r["import_id"], r["format"], b))
    return out


def _reported(b):
    """A saved series' name as its lab reported it: kept beside the name a match gave it (apply, #1097)."""
    return str(b.get("reported") or b.get("name") or "")


def covered(db, pid, rows, series=None):
    """{labmaps id: [series id]}: the profile's saved series each of these matches (labmaps rows, profile pid's own)
    is for: those its importer reported with its name, unit and code; and, saved before the name as reported was
    kept beside a matched one, those the match's own import saved under its test."""
    series = _series(db, pid) if series is None else series
    by, legacy = {r["lookup"]: r["id"] for r in rows}, {}
    for r in rows:
        if r["target"] and r["import_id"]:
            src = json.loads(crypto.dec(r["source"]))
            legacy[(r["import_id"], lookup(r["importer"], crypto.dec(r["target"]), src.get("unit"), src.get("loinc"), pid))] = r["id"]
    out = {r["id"]: [] for r in rows}
    for sid, iid, fmt, b in series:
        mid = by.get(lookup(fmt, _reported(b), b.get("unit"), series_loinc(b), pid))
        if mid is None and "reported" not in b and legacy:
            mid = legacy.get((iid, lookup(fmt, b["name"], b.get("unit"), series_loinc(b), pid)))
        if mid is not None:
            out[mid].append(sid)
    return out


def own(db, pid):
    """Profile pid's own matches, listing's, each with how many saved results ("results") from how many imports
    ("imports") it covers."""
    rows = db.execute("SELECT * FROM labmaps WHERE profile_id=?", (pid,)).fetchall()
    series = _series(db, pid)
    cov, of = covered(db, pid, rows, series), {sid: (iid, b) for sid, iid, _, b in series}
    n = {r[0]: r[1] for r in db.execute("SELECT series_id, COUNT(*) FROM results WHERE profile_id=? GROUP BY series_id", (pid,))}
    out = []
    for m in listing(db, pid):
        sids = cov.get(m["id"], [])
        out.append({**m, "results": sum(n.get(s, 0) + len(of[s][1].get("txt") or []) for s in sids),
                    "imports": len({of[s][0] for s in sids})})
    return out


def tests(db, pid):
    """The profile's tests as charted, for a match's picker: [{"name", "unit"}], by name."""
    return [{"name": n, "unit": v["unit"]} for n, v in sorted(profile_series(db, pid).items(), key=lambda x: x[0].lower())]


def charted_as(db, pid, name):
    """The identities (importer, name as reported, unit, code) of the profile's series charted under name."""
    shown, out = charted(db, pid), {}
    name = " ".join(str(name or "").split())
    for sid, _, fmt, b in _series(db, pid):
        if " ".join(shown.get(sid, b["name"]).split()) == name:
            ident = (fmt, _reported(b), str(b.get("unit") or ""), series_loinc(b))
            out.setdefault(lookup(*ident, pid), ident)
    return list(out.values())


def set_matches(db, pid, idents, target, who, move=True, kept=False):
    """The one way a profile's match is made or changed outside a review (Add, Change, Keep as its own test, and Same
    test as… on Labs), inside a transaction: each identity (importer, name as reported, unit, code) matched to target,
    one of the profile's tests, or kept as a test of its own (None; so is a name matched to itself), checked (#569),
    for later imports. move: its saved results re-filed under it too. -> {"change", "matches": [ids], "series",
    "results"}. Raises Refused for a target that isn't one of the profile's tests (unless kept: Keep, a match to
    check kept as it is)."""
    target = clean_target(target)
    if target is not None and not kept and target not in {t["name"] for t in tests(db, pid)}:
        raise Refused(f"“{target}” isn't one of this profile's tests. Pick one from the list.")
    now, before, ids, seen = dbm.now(), [], [], set()
    probe = []   # each match as it was (or will be), for the series it covers: before its target changes (covered's legacy)
    for imp, name, unit, code in idents:
        key = lookup(imp, name, unit, code, pid)
        if key in seen:
            continue
        seen.add(key)
        to = None if target is None or norm(target) == norm(name) else target
        r = db.execute("SELECT * FROM labmaps WHERE lookup=?", (key,)).fetchone()
        before.append(_snap(r) if r else {"lookup": key})
        probe.append({"id": key, "lookup": key, "importer": imp, "target": r["target"] if r else None,
                      "import_id": r["import_id"] if r else None, "source": r["source"] if r else None, "name": name, "to": to})
        if r:
            db.execute("UPDATE labmaps SET target=?, changed_by=?, changed_name=?, changed=?, checked=? WHERE id=?",
                       (_enc_target(to), who["id"], who["username"], now, now, r["id"]))
            ids.append(r["id"])
        else:
            ids.append(db.execute("""INSERT INTO labmaps(lookup, importer, source, target, created_by, created_name, created, checked, profile_id)
                                     VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                  (key, imp, crypto.enc(json.dumps({"name": name, "unit": unit, "loinc": code}, ensure_ascii=False)),
                                   _enc_target(to), who["id"], who["username"], now, now, pid)).lastrowid)
    moved, results = {}, 0
    if move and probe:
        series = _series(db, pid)
        body, cov = {sid: b for sid, _, _, b in series}, covered(db, pid, probe, series)
        for p in probe:
            for sid in cov[p["id"]]:   # kept as its own: the name as reported (the match's, for one saved before it was kept)
                own_name = _reported(body[sid]) if norm(_reported(body[sid])) == norm(p["name"]) else p["name"]
                moved[sid] = p["to"] or own_name
        if moved:
            marks = ",".join("?" * len(moved))
            results = db.execute(f"SELECT COUNT(*) FROM results WHERE profile_id=? AND series_id IN ({marks})", (pid, *moved)).fetchone()[0]
            results += sum(len(body[s].get("txt") or []) for s in moved)
    cid = _record(db, pid, who, before, moved)
    return {"change": cid, "matches": ids, "series": len(moved), "results": results}


def remove_own(db, pid, mid, who):
    """Forget profile pid's own match mid, inside a transaction: the next import with that name asks again, and its
    saved results stay where they're charted. -> the change, for its Undo; None if there's no such match."""
    r = db.execute("SELECT * FROM labmaps WHERE id=? AND profile_id=?", (mid, pid)).fetchone()
    if r is None:
        return None
    db.execute("DELETE FROM labmaps WHERE id=?", (mid,))
    return _record(db, pid, who, [_snap(r)], {})


def for_export(db, pid):
    """Profile pid's own matches and its re-filed series, for Export everything's "corrections" (export.corrections):
    ({"labmaps": [{importer, name, unit, loinc, target, checked, at, by}], "refiled": [{key, name, unit, as}]}): each
    series charted under another test found by its source key and the name and unit it's saved with, as an export loaded
    elsewhere saves them again (the series' ids are this install's)."""
    out = {"labmaps": [], "refiled": []}
    for r in db.execute("SELECT * FROM labmaps WHERE profile_id=? ORDER BY id", (pid,)):
        m = _row(r)
        out["labmaps"].append({"importer": m["importer"], "name": m["name"], "unit": m["unit"], "loinc": m["loinc"],
                               "target": m["target"], "checked": r["checked"] is not None, "at": m["created"], "by": m["created_by"]})
    shown, seen = charted(db, pid), set()
    for sid, _, _, b in _series(db, pid) if shown else ():
        x = (b.get("key") if isinstance(b.get("key"), str) else None, b["name"], str(b.get("unit") or ""), shown.get(sid))
        if x[3] is not None and x[3] != b["name"] and x not in seen:
            seen.add(x)
            out["refiled"].append(dict(zip(("key", "name", "unit", "as"), x)))
    return {k: v for k, v in out.items() if v}


def check_export(data):
    """An export's matches and re-files (for_export's), checked: ([(importer, name, unit, loinc, target, checked, at,
    by)], [(key, name, unit, as)]). Raises ValueError for one that can't be loaded."""
    fix = (data.get("corrections") or {}) if isinstance(data, dict) else {}
    text = lambda v, n=4000, empty=False: v if isinstance(v, str) and (empty or v.strip()) and len(v) <= n else _bad()  # noqa: E731
    maps, refiled = [], []
    for n, x in enumerate(fix.get("labmaps") or [] if isinstance(fix, dict) else []):
        try:
            if not isinstance(x, dict):
                _bad()
            maps.append((text(x.get("importer"), 80), text(x.get("name")), text(x.get("unit") or "", 200, True),
                         text(x.get("loinc") or "", 80, True), None if x.get("target") is None else text(x["target"], NAME_MAX),
                         bool(x.get("checked")), x.get("at") if isinstance(x.get("at"), int) else None,
                         x["by"][:200] if isinstance(x.get("by"), str) and x["by"].strip() else None))
        except ValueError:
            raise ValueError(f"The export's lab name match {n + 1} can't be loaded: it isn't one Sanguity writes.") from None
    for n, x in enumerate(fix.get("refiled") or [] if isinstance(fix, dict) else []):
        try:
            if not isinstance(x, dict):
                _bad()
            refiled.append((None if x.get("key") is None else text(x["key"]), text(x.get("name")), text(x.get("unit") or "", 200, True),
                            text(x.get("as"), NAME_MAX)))
        except ValueError:
            raise ValueError(f"The export's re-filed lab {n + 1} can't be loaded: it isn't one Sanguity writes.") from None
    return maps, refiled


def _bad():
    raise ValueError


def from_export(db, pid, data):
    """An export's matches and re-files put back in profile pid (export.restore, and again once its records are saved,
    jobs.py: a series re-filed is found among them), inside the transaction that loads it. The profile's own are left
    as they are: a match of the same name, unit, code and source, and a series it charts already under another test.
    Returns how many matches were added."""
    maps, refiled = check_export(data)
    n = 0
    for imp, name, unit, code, target, checked, at, by in maps:
        n += db.execute("""INSERT INTO labmaps(lookup, importer, source, target, created_by, created_name, created, checked, profile_id)
                           VALUES(?, ?, ?, ?, NULL, ?, ?, ?, ?) ON CONFLICT(lookup) DO NOTHING""",
                        (lookup(imp, name, unit, code, pid), imp, crypto.enc(json.dumps({"name": name, "unit": unit, "loinc": code}, ensure_ascii=False)),
                         _enc_target(target), by or "export", at or dbm.now(), dbm.now() if checked else None, pid)).rowcount
    want = {(key, name, unit): to for key, name, unit, to in refiled}
    if want:
        shown, moved = charted(db, pid), {}
        for sid, _, _, b in _series(db, pid):
            to = want.get((b.get("key") if isinstance(b.get("key"), str) else None, b["name"], str(b.get("unit") or "")))
            if to is not None and sid not in shown:
                moved[sid] = to
        if moved:
            _record(db, pid, None, [], moved)
    if n:
        _changed(db)
    return n


_LOOSE = ("used",)   # what may change after a change without its Undo being refused: marked used by an import since


def _snap(r):
    """A labmaps row as kept in a change: its name and target opened (sealed again with the change's value, so a
    rotation of the data key, which seals labmaps' anew, leaves an Undo able to put it back)."""
    out = dict(r)
    out["source"], out["target"] = crypto.dec(r["source"]), crypto.dec(r["target"]) if r["target"] else None
    return out


def _state(r):
    return None if r is None else {k: v for k, v in _snap(r).items() if k not in _LOOSE}


def _record(db, pid, who, before, moved):
    after = [_state(db.execute("SELECT * FROM labmaps WHERE lookup=?", (b["lookup"],)).fetchone()) or {"lookup": b["lookup"], "gone": True}
             for b in before]
    now = dbm.now()
    cid = db.execute("INSERT INTO labmap_changes(profile_id, series, value, created_by, at) VALUES(?, ?, ?, ?, ?)",
                     (pid, len(moved), crypto.enc(json.dumps({"series": {str(k): v for k, v in moved.items()}, "before": before,
                                                              "after": after}, ensure_ascii=False)),
                      who["id"] if who else None, now)).lastrowid
    db.execute("DELETE FROM labmap_changes WHERE profile_id=? AND series=0 AND at<?", (pid, now - KEEP_FOR))
    _settled(db, pid)
    return cid


def _settled(db, pid):
    from . import records
    _changed(db)             # a review waiting since plans again (#583)
    records._bump(db, pid)   # the records as charted changed


def undo(db, pid, cid):
    """Undo profile pid's change cid, inside a transaction: its matches as they were before it, its results charted
    where they were. Raises LookupError if there's no such change, Refused if a match it left has been changed since
    (by another change, an import's review or its Undo)."""
    r = db.execute("SELECT value FROM labmap_changes WHERE id=? AND profile_id=?", (cid, pid)).fetchone()
    if r is None:
        raise LookupError(cid)
    v = json.loads(crypto.dec(r["value"]))
    for a in v["after"]:
        now = _state(db.execute("SELECT * FROM labmaps WHERE lookup=?", (a["lookup"],)).fetchone())
        if (now is None) != bool(a.get("gone")) or (now is not None and now != a):
            raise Refused("Not undone: this match was changed since. Look at it again.")
    for b in v["before"]:
        db.execute("DELETE FROM labmaps WHERE lookup=?", (b["lookup"],))
        if "id" in b:
            if db.execute("SELECT 1 FROM labmaps WHERE id=?", (b["id"],)).fetchone():
                b = {k: x for k, x in b.items() if k != "id"}
            if b.get("import_id") and not db.execute("SELECT 1 FROM imports WHERE id=?", (b["import_id"],)).fetchone():
                b = {**b, "import_id": None}   # (its import undone since)
            b = {**b, "source": crypto.enc(b["source"]), "target": _enc_target(b["target"])}
            cols = list(b)
            db.execute(f"INSERT INTO labmaps({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})", [b[c] for c in cols])
    db.execute("DELETE FROM labmap_changes WHERE id=?", (cid,))
    _settled(db, pid)
