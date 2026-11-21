"""Lab names lab-aliases.json doesn't know, matched to a series during import (#23), so the same test from another
lab lands in the same series on Trends.

Importers name a lab series with common.canonical_lab: lab-aliases.json's name for its LOINC code or spelling, else
the name as reported. For each series of an import whose name is neither canonical nor already one of the profile's
series, the preview suggests a series to put it in:

  1. the profile's own series, then lab-aliases.json's names, with the same LOINC code      "same LOINC code"
  2. then names alike once case, punctuation and common abbreviations are evened out        "similar name, same unit"

and only where the units agree (common.unit_for); a name from lab-aliases.json the profile doesn't chart yet has no
unit to compare, and says so. The person confirms the match, picks another series, or keeps it as a series of its
own; confirm sends {"labs": {"s:<series index>": "<series name>" | null}} and the import is saved with those names.
Only the series' name changes: its source key (LOINC or source based) stays, so duplicates are still found by it, and
the results' own rows (a lab day's details) keep the name as reported.

Each choice is remembered for the install (table labmaps, schema version 16), keyed by the importer and the name,
unit and LOINC code as reported, normalised: "keep as a series of its own" too (target NULL), so the next import
doesn't ask again. Remembered choices are applied as soon as an import is parsed, before its duplicates are found,
and to results typed in (routes_manual.py); the preview shows them as "matched as before", and changing one there
changes that import only. They affect every profile, so only admins change or remove them (routes_labmaps.py), but
for one way: undoing the import whose review made a choice forgets it, unless another import has been saved with it
since or an admin has changed it (labmaps.import_id and used, schema version 36; history.undo, #500).

The name, unit and code (source) and the target are health details, so encrypted (crypto.enc); rows are found by
lookup, a keyed hash of the normalised identity (security.pepper_mac), never by decrypting them all.
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


def compatible(target, u1, u2):
    return common.unit_for(target, u1) == common.unit_for(target, u2)


def lookup(importer, name, unit, loinc):
    """The keyed hash a remembered choice is found by."""
    ident = json.dumps([str(importer), norm(name), common.unit_norm(unit), str(loinc or "").strip()], ensure_ascii=False)
    return sec.pepper_mac(b"sanguity lab map\x00" + ident.encode("utf-8")).hex()[:32]


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
    """Up to SUGGESTIONS series for s: [{"name", "unit", "reason", "sure"}], best first. sure: the units could be
    compared (or the LOINC code is the same), so the preview chooses it unless told otherwise."""
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
        r = difflib.SequenceMatcher(None, mine, even(n)).ratio()
        if r >= SIMILAR and compatible(n, v["unit"], unit):
            alike.append((r, 0, n, v["unit"], "similar name, same unit"))
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

      {"listed": [{"ref", "name", "unit", "loinc", "n", "suggestions", "before": {"target"} | None}],
       "auto": {ref: target or None}   remembered choices, applied without asking
       "ids": {ref: labmaps id}        theirs, marked used once the import is saved (used, #500)
       "targets": [{"name", "unit"}]   every series one can be put in (only when something is listed)}

    A series is listed when lab-aliases.json doesn't know its name and the profile has no series of that name (or
    when a choice for it was remembered). keep: refs listed even so, a preview read again asking about what it asked
    before (jobs.freshen, #497), so a choice typed in its review isn't lost; such an entry names the series of its
    name ("same") the profile has by now."""
    have = profile_series(db, pid)
    lower = {n.lower(): n for n in have}
    candidates = {}
    for i, s in enumerate(data.get("labs") or []):
        if isinstance(s, dict) and isinstance(s.get("name"), str) and s["name"].strip() and not _known(s["name"]):
            candidates[f"s:{i}"] = s
    found = remembered(db, [importer], {ref: _ident(s) for ref, s in candidates.items()})
    listed, auto, ids = [], {}, {}
    for ref, s in candidates.items():
        before, same = found.get(ref), lower.get(s["name"].strip().lower())
        if before is None and same and ref not in keep:
            continue
        entry = {"ref": ref, "name": s["name"], "unit": str(s.get("unit") or ""), "loinc": series_loinc(s),
                 "n": len(s.get("pts") or []) + len(s.get("txt") or []), "suggestions": suggest(s, have), "before": None}
        if before is None and same:
            entry["same"] = same   # (kept: left as a series of its own, it joins that one)
        if before is not None:
            entry["before"] = {"target": before["target"]}
            auto[ref], ids[ref] = before["target"], before["id"]
        listed.append(entry)
    return {"listed": listed, "auto": auto, "ids": ids, "targets": targets(have) if listed else []}


def _ident(s):
    return (str(s.get("name") or ""), str(s.get("unit") or ""), series_loinc(s))


def choices(lab_plan, sent):
    """The names to save the import's series under: the remembered ones, overridden by what was sent for series the
    preview listed. A series the preview listed but nothing was sent for keeps its own name."""
    out = dict((lab_plan or {}).get("auto") or {})
    listed = {e["ref"] for e in (lab_plan or {}).get("listed") or []}
    for ref, target in (sent or {}).items():
        if ref in listed:
            out[ref] = clean_target(target)
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


def remember(db, importer, lab_plan, sent, data, who, import_id=None):
    """Keep the choices sent for series the preview listed and nothing was remembered for. Inside the import's
    transaction. A choice remembered meanwhile (by another import, or an admin) wins. Returns how many were kept.
    import_id: the import saved with them, whose Undo forgets them (forget, #500)."""
    listed = {e["ref"]: e for e in (lab_plan or {}).get("listed") or [] if not e.get("before")}
    labs = data.get("labs") or []
    n = 0
    for ref, target in (sent or {}).items():
        e = listed.get(ref)
        if not e or not ref[2:].isdigit() or int(ref[2:]) >= len(labs):
            continue
        name, unit, code = _ident(labs[int(ref[2:])])
        n += db.execute("""INSERT INTO labmaps(lookup, importer, source, target, created_by, created_name, created, import_id)
                           VALUES(?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(lookup) DO NOTHING""",
                        (lookup(importer, name, unit, code), importer,
                         crypto.enc(json.dumps({"name": name, "unit": unit, "loinc": code}, ensure_ascii=False)),
                         _enc_target(clean_target(target)), who["id"] if who else None,
                         who["username"] if who else "removed account", dbm.now(), import_id)).rowcount
    return n


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
    return db.execute("DELETE " + _OWN, (import_id,)).rowcount


def _enc_target(t):
    return None if t is None else crypto.enc(t)


def remembered(db, importers, idents):
    """{ref: {"id", "target"}} for the identities ({ref: (name, unit, loinc)}) a choice was remembered for, under the
    first of importers that has one."""
    if not idents:
        return {}
    keys = {(ref, imp): lookup(imp, *ident) for ref, ident in idents.items() for imp in importers}
    rows = {}
    hashes = sorted(set(keys.values()))
    for i in range(0, len(hashes), 500):
        part = hashes[i:i + 500]
        for r in db.execute(f"SELECT id, lookup, target FROM labmaps WHERE lookup IN ({','.join('?' * len(part))})", part):
            rows[r["lookup"]] = r
    out = {}
    for ref in idents:
        for imp in importers:
            r = rows.get(keys[(ref, imp)])
            if r is not None:
                out[ref] = {"id": r["id"], "target": crypto.dec(r["target"]) if r["target"] else None}
                break
    return out


def apply_remembered(db, importers, data):
    """data with the remembered choices applied to its series (hand entry): (data, {old name: new name}, [their ids],
    marked used once it's saved)."""
    idents = {f"s:{i}": _ident(s) for i, s in enumerate(data.get("labs") or [])
              if isinstance(s, dict) and isinstance(s.get("name"), str) and not _known(s["name"])}
    found = remembered(db, importers, idents)
    picked = {ref: f["target"] for ref, f in found.items()}
    renamed = {idents[ref][0]: t for ref, t in picked.items() if t}
    return apply(data, picked), renamed, [f["id"] for f in found.values()]


# ---------------------------------------------------------------- the list, for admins

def _row(r):
    src = json.loads(crypto.dec(r["source"]))
    return {"id": r["id"], "importer": r["importer"], "name": src.get("name"), "unit": src.get("unit") or "",
            "loinc": src.get("loinc") or "", "target": crypto.dec(r["target"]) if r["target"] else None,
            "created": r["created"], "created_by": r["created_name"], "changed": r["changed"], "changed_by": r["changed_name"]}


def listing(db):
    """Every remembered choice, last changed first: [{"id", "importer", "name", "unit", "loinc", "target",
    "created", "created_by", "changed", "changed_by"}]."""
    return [_row(r) for r in db.execute("SELECT * FROM labmaps ORDER BY IFNULL(changed, created) DESC, id DESC")]


def get(db, mid):
    r = db.execute("SELECT * FROM labmaps WHERE id=?", (mid,)).fetchone()
    return _row(r) if r else None


def change(db, mid, target, who):
    db.execute("UPDATE labmaps SET target=?, changed_by=?, changed_name=?, changed=? WHERE id=?",
               (_enc_target(clean_target(target)), who["id"], who["username"], dbm.now(), mid))


def remove(db, mid):
    db.execute("DELETE FROM labmaps WHERE id=?", (mid,))
