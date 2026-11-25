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
the preview shows them as "matched as before", and changing one there changes that import only. Only admins change or
remove them (routes_labmaps.py), but for one way: undoing the import whose review made a choice forgets it, unless
another import has been saved with it since or an admin has changed it (labmaps.import_id and used, schema version
36; history.undo, #500); and deleting a profile forgets those its reviews made (forget_profile, #591).

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
    for r in db.execute("SELECT body FROM series WHERE profile_id=?", (pid,)):
        try:
            s = json.loads(crypto.dec(r["body"]))
        except (ValueError, TypeError):
            continue
        if not isinstance(s, dict) or not isinstance(s.get("name"), str):
            continue
        units[s["name"]][str(s.get("unit") or "")] += 1
        if series_loinc(s):
            codes[s["name"]].add(series_loinc(s))
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
        if isinstance(s, dict) and isinstance(s.get("name"), str) and s["name"].strip() and not _known(s["name"]):
            candidates[f"s:{i}"] = s
    found = remembered(db, pid, [importer], {ref: _ident(s) for ref, s in candidates.items()}, flagged=True)
    listed, auto, ids = [], {}, {}
    for ref, s in candidates.items():
        before, same = found.get(ref), lower.get(s["name"].strip().lower())
        if before is None and same and ref not in keep:
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
        labs.append({**copy.deepcopy(s), "name": t} if t and isinstance(s, dict) else s)
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
              if isinstance(s, dict) and isinstance(s.get("name"), str) and not _known(s["name"])}
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


def listing(db):
    """Every remembered choice, those to check (#569) first, then last changed first: [{"id", "importer", "name",
    "unit", "loinc", "target", "check", "one_profile", "created", "created_by", "changed", "changed_by"}]."""
    rows = [_shown(_row(r)) for r in db.execute("SELECT * FROM labmaps ORDER BY IFNULL(changed, created) DESC, id DESC")]
    return sorted(rows, key=lambda m: not m["check"])


def get(db, mid):
    r = db.execute("SELECT * FROM labmaps WHERE id=?", (mid,)).fetchone()
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
