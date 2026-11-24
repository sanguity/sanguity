"""The same lab result arriving from two sources (#96): spotted, flagged, and shown once when the person says so.

Source keys (#89) recognise a record that comes back from the same source. The same result read from a file and
then pulled by a connected source has different keys (or the same series key with the time or value written
differently), so without this rule it would show twice. It applies to lab results (series points) only.

The rule, match(): two results are possibly the same when all of these hold.

  test   the same LOINC code when both series have one; otherwise the same canonical series
         (common.canonical_lab, else the series name, case aside)
  time   the same collection time to the minute: as instants when both times carry an offset, as written when either
         doesn't. A time that is only a date (a bare date, or exactly 00:00:00 or 12:00:00 with no offset, which is how
         importers write a date with no time, common.iso) matches any time that day.
  value  the same value once units are made comparable: common.unit_for spellings, plus exact powers-of-ten changes
         of prefix (121 g/L is 12.1 g/dL; 5.0 mmol/L is 5000 umol/L; 10*9/L is 10*3/uL). Never a conversion that
         needs a molar mass or a factor that isn't exact (units.exact_factor, the table the page's unit preferences
         share). Values compare as decimals, so 12.10 is 12.1; a comparator ("<0.5") must be the same on both sides.
  source the two come from different imports (one import's own repeats are the preview's business, preview.py)

A match is never acted on by itself. It is flagged: in the import preview (for_preview, "possibly the same result
as ..." with both sources named) and in the records the page loads ("possible": the drawer says so beside the
result). The person decides: "same result" or "not the same". That decision is stored in the same_results table
(schema version 22, see db.py and docs/database.md), apart from the imports, so it survives re-imports and undo,
like edits.py's corrections.

  same_results   pair       keyed hash of both results' point keys (pair_hash): finds the pair, and is the "ref" the
                            page sends back. Plain, but tells nothing without the pepper.
                 a_key      the two point keys, encrypted. A point key is the series' source key (or, without one,
                 b_key      its name and unit), the time and the value as reported: what identifies a point across
                            re-imports, as preview.py identifies one.
                 same       1 "same result", 0 "not the same" (the flag goes away and isn't raised again)
                 value      encrypted JSON: what each side was when decided, for the listing when one is gone

apply() runs last when the records are put together (records._assemble). For each "same result" whose both points
are in the records, the point from the older import stays and the other is left out of its series (and the series,
if nothing is left in it), so Trends and the heatmap show it once; the kept point's series lists the other source
under "same" ({point index: [{imp, link}]}). The kept point carries any range and flag the other has where its own
report gives none (ranges.copies, #781: a range in the kept point's unit, by the match's exact factor, and whether its
limit includes its edge, #780), so a copy with no range kept beside one with its report's range and Low isn't shown
with neither. In the drawer, the other source's row is left out of its result (and the result, and its lab day, when
nothing else is left), and the kept row's result lists the other under "same", the kept row given the other's range
and flag in the same way. Three sources linked in pairs show once, with all three named. Removing the link shows
both again; undoing one side's import leaves the link stored but unapplied (found: false), and the other side shows
alone. copies() runs after it, on the copies of a result not linked (#825: the same lab, unit, time and value, from two
imports or twice from one): each is given the one result's range and flag in the same way, and the page shows it once;
two the person said aren't the same stay two results, each with its own (#833).

Visits and claims, later. A visit from a C-CDA and the same visit from a FHIR sync could match on the same
organisation, the same day and the same encounter type, and a claim could match a visit by date of service and
provider. Neither is safe yet: visits have no shared code (an encounter type is free text in most exports), a day
often holds two real visits at one clinic (a lab draw and an appointment), organisation names differ between sources
("Maple Grove Clinic" and "MGC Family Medicine"), and a claim's date of service and billing provider are not the
visit's own. A rule built on those would flag far more than it finds; it needs a shared identifier first (an
encounter id carried by both sources, or a claim's link to its encounter, which some insurer APIs give, #92).
"""
import collections, json, re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from . import crypto, db as dbm, ranges, security as sec, units
from .importers import common

LISTED = 500   # possible matches listed for the page, at most
_NUM = re.compile(r"^\s*([<>]=?)?\s*(-?\d+(?:\.\d+)?)\s*$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# ---------------------------------------------------------------- the rule

def _code(s):
    """The series' LOINC code: its own "loinc", or the one in a "loinc:<code>" source key."""
    c = str(s.get("loinc") or "").strip()
    if not c and isinstance(s.get("key"), str) and s["key"].startswith("loinc:"):
        c = s["key"][6:].split("|", 1)[0].strip()
    return c or None


def _canon_name(s):
    """The series' canonical name (lab-aliases.json's, by code or name), else its own."""
    name = str(s.get("name") or "").strip()
    return common.canonical_lab(name, _code(s)) or name


def _canon(s):
    return _canon_name(s).lower()


def same_test(a, b):
    ca, cb = _code(a), _code(b)
    if ca and cb:
        return ca == cb
    return _canon(a) == _canon(b) != ""


def _when(t):
    """(day as written, datetime or None, whether it is only a date) or None."""
    s = str(t or "").strip()
    if _DATE.match(s):
        return s, None, True
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    only_date = dt.tzinfo is None and (dt.hour, dt.minute, dt.second) in ((0, 0, 0), (12, 0, 0))
    return s[:10], dt, only_date


def same_time(a, b):
    wa, wb = _when(a), _when(b)
    if not wa or not wb:
        return False
    if wa[2] or wb[2]:
        return wa[0] == wb[0]   # one has only the date: the same day will do
    da, db_ = wa[1], wb[1]
    if da.tzinfo and db_.tzinfo:
        da, db_ = da.astimezone(timezone.utc), db_.astimezone(timezone.utc)
    else:
        da, db_ = da.replace(tzinfo=None), db_.replace(tzinfo=None)
    return da.replace(second=0, microsecond=0) == db_.replace(second=0, microsecond=0)


# the exact prefix changes are units.py's table, shared with the page's unit preferences (#41); a molar mass is
# never used here
factor = units.exact_factor


def _shown(p):
    """The value as reported (else the number) of a point [time, number, low, high, flag, shown, src]."""
    if len(p) > 5 and p[5] not in (None, ""):
        return str(p[5])
    return "" if len(p) < 2 or p[1] is None else str(p[1])


def _value(p):
    """(comparator, Decimal) of a point's value, or None for one that isn't a number."""
    m = _NUM.match(_shown(p))
    if not m:
        return None
    try:
        return m.group(1) or "", Decimal(m.group(2))
    except InvalidOperation:
        return None


def same_value(sa, pa, sb, pb):
    va, vb = _value(pa), _value(pb)
    if not va or not vb or va[0] != vb[0]:
        return False
    f = factor(_canon_name(sa), sa.get("unit"), sb.get("unit"))
    return f is not None and va[1] * f == vb[1]


def match(sa, pa, sb, pb):
    """Whether point pa of series sa and point pb of series sb are possibly the same result (see the docstring).
    Series are the records' lab series ({name, unit, loinc?, key?, imp?}); points their pts entries."""
    if not (isinstance(pa, list) and isinstance(pb, list) and pa and pb):
        return False
    if sa.get("imp") is not None and sa.get("imp") == sb.get("imp"):
        return False
    return same_test(sa, sb) and same_time(pa[0], pb[0]) and same_value(sa, pa, sb, pb)


# ---------------------------------------------------------------- keys

def point_key(s, p):
    """What identifies a result across re-imports: its series' key (else name and unit), unit, time, value."""
    k = s.get("key")
    ident = k if isinstance(k, str) and k.strip() else "n:" + str(s.get("name") or "").strip().lower()
    return "\x1f".join(("pt", ident, common.unit_norm(s.get("unit")), str(p[0]), _shown(p)))


def pair_hash(ka, kb):
    """A keyed hash of the pair, the same whichever comes first: the table's lookup, and the page's ref."""
    a, b = sorted((ka, kb))
    return sec.pepper_mac(b"sanguity same result\x00" + a.encode("utf-8") + b"\x1e" + b.encode("utf-8")).hex()[:32]


def _day(t):
    return str(t or "")[:10]


def _tests(s):
    return {"n:" + _canon(s)} | ({"c:" + _code(s)} if _code(s) else set())


def _buckets(labs):
    """{(day, test): [(series index, point index)]}: a point goes in one bucket for its canonical name and one for its
    LOINC code, so two series compare when either agrees (match() then decides)."""
    out = collections.defaultdict(list)
    for i, s in enumerate(labs):
        if not isinstance(s, dict) or s.get("watch"):
            continue
        tests = _tests(s)
        for n, p in enumerate(s.get("pts") or []):
            if isinstance(p, list) and p:
                for t in tests:
                    out[(_day(p[0]), t)].append((i, n))
    return out


def candidates(labs):
    """Every possible match in one set of records: [((i, n), (j, m))], i's import older than j's (or i < j)."""
    found = set()
    for pts in _buckets(labs).values():
        if len(pts) < 2:
            continue
        for x in range(len(pts)):
            for y in range(x + 1, len(pts)):
                (i, n), (j, m) = pts[x], pts[y]
                if i == j:
                    continue
                a, b = labs[i], labs[j]
                if match(a, a["pts"][n], b, b["pts"][m]):
                    found.add(tuple(sorted(((i, n), (j, m)), key=lambda q: (_imp_order(labs[q[0]]), q))))
    return sorted(found)


def _imp_order(s):
    imp = s.get("imp")
    return imp if isinstance(imp, int) else 1 << 60


# ---------------------------------------------------------------- the drawer's rows

def _rows(data):
    """{(import, day): [(labitem event id, row index, row)]} for the result rows in the drawer."""
    out = collections.defaultdict(list)
    details = data.get("details") or {}
    for e in data.get("events") or []:
        if e.get("k") != "labitem":
            continue
        d = details.get(str(e["id"]))
        if isinstance(d, dict) and isinstance(d.get("rows"), list):
            for r, row in enumerate(d["rows"]):
                if isinstance(row, list) and len(row) >= 2:
                    out[(e.get("imp"), _day(e.get("t")))].append((e["id"], r, row))
    return out


def _row_of(rows, s, p):
    """The drawer row (event id, row index) showing point p of series s, or None when it can't be told apart."""
    v = _value(p)
    canon = _canon_name(s)
    name = str(s.get("name") or "").strip().lower()
    alike, named = [], []
    for eid, r, row in rows.get((s.get("imp"), _day(p[0]))) or []:
        rv = _value([None, None, None, None, None, row[1]])
        if str(row[1]).strip() != _shown(p).strip() and not (v and rv and rv == v):
            continue
        rname = str(row[0] or "").strip()
        if (common.canonical_lab(rname) or rname).lower() in (name, canon.lower()):
            named.append((eid, r))
        elif len(row) > 2 and common.unit_for(canon, row[2]) == common.unit_for(canon, s.get("unit")):
            alike.append((eid, r))
    if len(named) == 1:
        return named[0]
    return alike[0] if not named and len(alike) == 1 else None


# ---------------------------------------------------------------- stored decisions

def _enc(obj):
    return crypto.enc(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))


def _dec(text):
    return None if text is None else json.loads(crypto.dec(text))


def _decided(db, pid):
    return db.execute("""SELECT x.id, x.pair, x.a_key, x.b_key, x.same, x.value, x.at, u.username
                         FROM same_results x LEFT JOIN users u ON u.id = x.created_by
                         WHERE x.profile_id=? ORDER BY x.id""", (pid,)).fetchall()


def describe(s, p):
    """One side of a pair, for the page and the listing."""
    return {"imp": s.get("imp"), "name": s.get("name"), "t": p[0], "value": _shown(p), "unit": s.get("unit") or ""}


def decide(db, pid, uid, ka, kb, same, sides):
    """Store "same result" (same=True) or "not the same" for the pair of point keys; a second decision on the same
    pair replaces the first. sides: [describe(a), describe(b)]. Call inside a transaction. Returns the row's id."""
    if ka == kb:
        raise ValueError("A result can't be the same as itself.")
    h = pair_hash(ka, kb)
    db.execute("DELETE FROM same_results WHERE profile_id=? AND pair=?", (pid, h))
    rid = db.execute("""INSERT INTO same_results(profile_id, pair, a_key, b_key, same, value, created_by, at)
                        VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
                     (pid, h, crypto.enc(ka), crypto.enc(kb), 1 if same else 0, _enc({"sides": sides}), uid, dbm.now())).lastrowid
    _bump(db, pid)
    return rid


def remove(db, pid, rid):
    """Undo a decision. Returns what it was ({"same", "sides"}), or None if there's no such one."""
    r = db.execute("SELECT same, value FROM same_results WHERE id=? AND profile_id=?", (rid, pid)).fetchone()
    if not r:
        return None
    db.execute("DELETE FROM same_results WHERE id=?", (rid,))
    _bump(db, pid)
    return {"same": bool(r["same"]), "sides": (_dec(r["value"]) or {}).get("sides") or []}


def _bump(db, pid):
    from . import records
    records._bump(db, pid)


# ---------------------------------------------------------------- applying

class _Groups:
    """Union-find over (series index, point index)."""
    def __init__(self):
        self.up = {}

    def find(self, x):
        self.up.setdefault(x, x)
        while self.up[x] != x:
            self.up[x] = self.up[self.up[x]]
            x = self.up[x]
        return x

    def join(self, a, b):
        self.up[self.find(a)] = self.find(b)


def apply(db, pid, data):
    """Apply the profile's "same result" links to its assembled records and list the possible matches still
    undecided. Adds "same" (the decisions: [{id, same, found, sides, by, at}]) and "possible" ([{ref, a, b}], each
    side {imp, name, t, value, unit, row: [event id, row index] | null}) only when there are any, so records with
    neither are served exactly as before."""
    data.pop("same", None)
    data.pop("possible", None)   # never ones an import brought along (an export's data.json)
    labs = data.get("labs") or []
    if not any(isinstance(s, dict) for s in labs):
        return data
    rows = _decided(db, pid)
    imps = {s.get("imp") for s in labs if isinstance(s, dict) and s.get("pts")}
    if not rows and len(imps) < 2:
        return data

    where = {}
    for i, s in enumerate(labs):
        if isinstance(s, dict) and not s.get("watch"):
            for n, p in enumerate(s.get("pts") or []):
                if isinstance(p, list) and p:
                    where.setdefault(point_key(s, p), (i, n))
    groups, links, listed, decided = _Groups(), collections.defaultdict(list), [], set()
    for r in rows:
        ka, kb = crypto.dec(r["a_key"]), crypto.dec(r["b_key"])
        a, b = where.get(ka), where.get(kb)
        decided.add(r["pair"])
        found = bool(a and b and a != b)
        value = _dec(r["value"]) or {}
        listed.append({"id": r["id"], "same": bool(r["same"]), "found": found, "sides": value.get("sides") or [],
                       "by": r["username"] or value.get("by"), "at": r["at"]})   # by: an export's, by name (#589)
        if found and r["same"]:
            groups.join(a, b)
            links[a].append(r["id"])
            links[b].append(r["id"])

    drawer, folded = _rows(data), set()
    members = collections.defaultdict(list)
    for x in list(groups.up):
        members[groups.find(x)].append(x)
    hidden, same = set(), collections.defaultdict(list)   # same: kept point -> [{imp, link}] of the others
    merged = {}   # kept point -> it, with any range and flag the others have (#781)
    for pts in members.values():
        if len(pts) < 2:
            continue
        pts.sort(key=lambda q: (_imp_order(labs[q[0]]), q))
        keep, rest = pts[0], pts[1:]
        ks, kp = labs[keep[0]], labs[keep[0]]["pts"][keep[1]]
        krow = _row_of(drawer, ks, kp)
        for q in rest:
            s, p = labs[q[0]], labs[q[0]]["pts"][q[1]]
            hidden.add(q)
            link = next((l for l in links[q] if l in links[keep]), links[q][0])
            same[keep].append({"imp": s.get("imp"), "link": link})
            merged[keep] = _carry(merged.get(keep, kp), ks, p, s)
            row = _row_of(drawer, s, p)
            if row and krow and _fold_row(data, row, krow, s.get("imp"), link):
                folded.add(row[0])

    _tidy(data, folded)
    drawer = _rows(data)   # again: folded rows are gone, so the rest may sit at other indexes
    # the possible matches still undecided, between points still shown
    possible = []
    for (a, b) in candidates(labs):
        if a in hidden or b in hidden:
            continue
        sa, pa, sb, pb = labs[a[0]], labs[a[0]]["pts"][a[1]], labs[b[0]], labs[b[0]]["pts"][b[1]]
        h = pair_hash(point_key(sa, pa), point_key(sb, pb))
        if h in decided or groups.find(a) == groups.find(b):
            continue
        ra, rb = _row_of(drawer, sa, pa), _row_of(drawer, sb, pb)
        possible.append({"ref": h, "a": {**describe(sa, pa), "row": list(ra) if ra else None},
                         "b": {**describe(sb, pb), "row": list(rb) if rb else None}})
        if len(possible) >= LISTED:
            break

    if hidden or same:
        out = []
        for i, s in enumerate(labs):
            if not isinstance(s, dict) or not any((i, n) in hidden or (i, n) in same for n in range(len(s.get("pts") or []))):
                out.append(s)
                continue
            pts, marks = [], {}
            for n, p in enumerate(s.get("pts") or []):
                if (i, n) in hidden:
                    continue
                if (i, n) in same:
                    marks[str(len(pts))] = same[(i, n)]
                pts.append(merged.get((i, n), p))
            if not pts and not s.get("txt"):
                continue   # every result in it shows in another series
            s = {**s, "pts": pts, "n": len(pts)}
            if marks:
                s["same"] = marks
            out.append(s)
        data["labs"] = out
    if listed:
        data["same"] = listed
    if possible:
        data["possible"] = possible
    return data


def _fold_row(data, row, keep_row, imp, link):
    """The drawer: row (event id, index) leaves its result, and keep_row's result lists its source instead."""
    details, eid = data["details"], row[0]
    d = details.get(str(eid))
    k = details.get(str(keep_row[0]))
    if not isinstance(d, dict) or not isinstance(k, dict) or eid == keep_row[0]:
        return False
    rows = list(d.get("rows") or [])
    if row[1] >= len(rows) or rows[row[1]] is None:
        return False
    taken = rows[row[1]]
    rows[row[1]] = None   # kept as a gap until every link is folded, so later indexes still hold
    d = details[str(eid)] = {**d, "rows": rows}
    krows = list(k.get("rows") or [])   # the kept row: with the range and flag of the one folded into it (#781)
    if keep_row[1] < len(krows) and isinstance(krows[keep_row[1]], list):
        krows[keep_row[1]] = _carry_row(krows[keep_row[1]], taken)
    k = details[str(keep_row[0])] = {**k, "rows": krows, "same": (k.get("same") or []) + [{"row": keep_row[1], "imp": imp, "link": link,
                                                                                            "name": taken[0]}]}
    return True


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _carry(kp, ks, p, s):
    """The kept point kp (of series ks), with what point p (of series s), the same result, says of its range and flag
    where kp says nothing (ranges.copies, #781: a copy with no range kept beside one with its report's 3.5–5.1 and Low
    showed 3.2 with neither everywhere): p's range in kp's unit, by the exact change of prefix the match allows, and its
    edge with it (pts[7], #780: a report's "<5" mg/L is "<0.5" mg/dL, 0.5 left out of it). A new list; the records' own
    are left as they are."""
    kp, p = list(kp), list(p) + [None] * (8 - len(p))
    lo, hi = _num(p[2]), _num(p[3])
    pf = ranges.read_flag(p[4], _num(p[1]), lo, hi, p[5], p[7])   # each flag as stored read as its report's (#839)
    if lo is not None or hi is not None:
        f = factor(_canon_name(s), s.get("unit"), ks.get("unit"))
        if f is None:   # (a chain of links: no exact factor between these two) nothing of p's is carried
            return kp
        if f != 1:
            lo, hi = (None if x is None else float(Decimal(str(x)) * f) for x in (lo, hi))
    have = (kp + [None] * 8)[1:8]
    was = (_num(have[1]), _num(have[2]), ranges.read_flag(have[3], _num(have[0]), _num(have[1]), _num(have[2]), have[4], have[6]),
           str(have[6] or ""))
    got = ranges.copies(was, (lo, hi, pf, p[7]))
    if got == was:
        return kp
    kp += [None] * (5 - len(kp))
    kp[2:5] = got[:3]
    if got[3] or len(kp) > 7:   # its edge after the source tag (null in its place where there's none)
        kp += [None] * (8 - len(kp))
        kp[7] = got[3] or None
    return kp


def _carry_row(kept, taken):
    """The kept drawer row [name, value, unit, range, flag], with the range and flag of the row folded into it where it
    says none (#781, as _carry): a range in another unit said with its unit."""
    k, t = list(kept) + [""] * (5 - len(kept)), list(taken) + [""] * (5 - len(taken))
    rng = str(t[3] or "").strip()
    if rng and common.unit_norm(t[2]) != common.unit_norm(k[2]):
        rng = f"{rng} {t[2]}".strip()
    r, f = str(k[3] or "").strip() or None, str(k[4] or "").strip()
    got = ranges.copies((r, None, f), (rng or None, None, t[4]))
    if (got[0], got[2]) == (r, f):
        return kept
    k[3], k[4] = got[0] or "", got[2]
    return k


def _tidy(data, folded):
    """Close the gaps folded rows left, and leave out a result with nothing left in it (and a lab day whose results
    are all gone)."""
    if not folded:
        return
    details, gone = data["details"], set()
    for eid in folded:
        d = details.get(str(eid))
        if not isinstance(d, dict):
            continue
        old = d.get("rows") or []
        at = {n: len([r for r in old[:n] if r is not None]) for n, r in enumerate(old) if r is not None}
        rows = [r for r in old if r is not None]
        d = details[str(eid)] = {**d, "rows": rows}
        if d.get("same"):   # its own rows linked to others' (three sources): where they are now
            d["same"] = [{**x, "row": at[x["row"]]} for x in d["same"] if x.get("row") in at]
        if not rows and not d.get("report"):
            gone.add(eid)
    for k, d in list(details.items()):
        if isinstance(d, dict) and d.get("type") == "labday" and isinstance(d.get("ids"), list) and gone & set(d["ids"]):
            ids = [i for i in d["ids"] if i not in gone]
            details[k] = {**d, "ids": ids}
            if not ids:
                gone.add(int(k) if str(k).isdigit() else k)
    if gone:
        data["events"] = [e for e in data["events"] if e["id"] not in gone]
        for i in gone:
            details.pop(str(i), None)


# ---------------------------------------------------------------- one result twice, not linked (#825)

def not_same(db, pid):
    """The pairs of results the person said aren't the same result (#833): their pair hashes (pair_hash), for copies()."""
    return {r["pair"] for r in db.execute("SELECT pair FROM same_results WHERE profile_id=? AND same=0", (pid,))}


def said(p):
    """What a copy of a result says of its range and flag, as ranges.copies takes it: (low, high, flag, edge), its flag
    as stored read as its report's (#839: an older import's "N" is none, so it keeps no other copy's flag from it, as
    the summary and the page read it). copies() puts a result's copies together by it, and the import's preview finds
    a result already here by it (preview.find, #846)."""
    p = list(p) + [None] * (8 - len(p))
    return _num(p[2]), _num(p[3]), ranges.read_flag(p[4], _num(p[1]), _num(p[2]), _num(p[3]), p[5], p[7]), str(p[7] or "")


def copies(data, apart=()):
    """Two copies of one result still shown apart (#825: from two imports not linked as the same result, or twice from
    one): the same lab and unit, time, value and value as given, as summary._series finds them. Each copy is given the
    range and flag of the one result by ranges.copies (#781), as the summary gives its one row: the first copy's own (in
    import order), what it lacks from the others', the range's edge with it (#780). So whichever copy is read, the
    later or the earlier, it's judged the same (a Potassium 5.2 with no range beside its copy with its report's 3.5–5.1
    is High in both): Labs and the Overview, which show it once (same.js joinSeries), and each import's lab day, its
    drawer row and its Timeline badge. The copies stay in their imports' series; the points changed are new lists.

    Two the person said aren't the same result (#833; apart: not_same(), their pair hashes) are two results: each copy
    joins the first result (in import order) none of whose copies it was said not to be, and the copies of each are
    one, so a pair marked "Not the same" each keeps its own range and flag. Where a time's copies are more than one
    result, each point's series says which it's in ("apart": {point index: 0, 1, ...}), so the page and the summary
    (summary._series) show each once and none in another.

    A copy whose own report gave no range, given its other copy's, says whose it is (#834: "rangeFrom": {point index:
    the import whose copy's report gave it}), so the drawer's row of its lab day names that range and where it came from
    beside the flag worked out against it."""
    labs, at = data.get("labs") or [], collections.defaultdict(list)
    for i, s in enumerate(labs):
        if isinstance(s, dict) and not s.get("watch"):
            for n, p in enumerate(s.get("pts") or []):
                if isinstance(p, list) and p:
                    q = p + [None] * (6 - len(p))
                    at[(s.get("name"), s.get("unit") or "", str(q[0]), _num(q[1]), str(q[5] or "").strip())].append((i, n))
    touched = set()

    def mine(i):   # series i as a new dict with its own list of points, the records' own left as they are
        if i not in touched:
            labs[i] = {**labs[i], "pts": list(labs[i]["pts"])}
            touched.add(i)
        return labs[i]

    for group in at.values():
        if len(group) < 2:
            continue
        results = [group]
        if apart:   # (#833) the results these copies are, by what the person said
            key = {m: point_key(labs[m[0]], labs[m[0]]["pts"][m[1]]) for m in group}
            results = []
            for m in group:
                r = next((r for r in results if all(key[m] == key[x] or pair_hash(key[m], key[x]) not in apart for x in r)), None)
                if r is None:
                    results.append([m])
                else:
                    r.append(m)
            if len(results) > 1:
                for k, r in enumerate(results):
                    for i, n in r:
                        s = mine(i)
                        s["apart"] = {**(s.get("apart") or {}), str(n): k}
        for result in results:
            if len(result) < 2:
                continue
            pts = [labs[i]["pts"][n] + [None] * (8 - len(labs[i]["pts"][n])) for i, n in result]
            got = said(pts[0])
            for p in pts[1:]:
                got = ranges.copies(got, said(p))
            # (#834) the import whose copy's report gave the range, for a copy whose own gave none
            src = next((labs[i].get("imp") for (i, _), p in zip(result, pts) if said(p)[:2] != (None, None)), None)
            for (i, n), p in zip(result, pts):
                if said(p)[:2] == (None, None) and got[:2] != (None, None):
                    s = mine(i)
                    s["rangeFrom"] = {**(s.get("rangeFrom") or {}), str(n): src}
                if said(p) == got:
                    continue
                kp = list(labs[i]["pts"][n])
                kp += [None] * (5 - len(kp))
                kp[2:5] = got[:3]
                if got[3] or len(kp) > 7:   # its edge after the source tag (null in its place where there's none)
                    kp += [None] * (8 - len(kp))
                    kp[7] = got[3] or None
                mine(i)["pts"][n] = kp
    return data


# ---------------------------------------------------------------- the import preview

def for_preview(db, pid, data, dups):
    """The results in an import being previewed (data, less dups as preview.find found them) that are possibly the
    same as one already in the profile from another import: [{ref: "m:<series index>:<point index>", name, t, value,
    unit, other: {imp, label, name, t, value, unit}, key, other_key}]. key and other_key are for jobs._save; the page
    gets the list without them (public()). Not a result already here that's added with its own report of it (#846,
    dups' "copies"): the review says those apart, and the page reads each as one with its copy here (copies())."""
    from . import records
    have = records.assemble(db, pid)
    if not have:
        return []
    old = [s for s in have.get("labs") or [] if isinstance(s, dict) and not s.get("watch")]
    if not old:
        return []
    sources = have.get("imports") or {}
    by = _buckets(old)

    def first_match(s, p):
        for t in _tests(s):
            for j, m in by.get((_day(p[0]), t), ()):
                if match({**s, "imp": None}, p, old[j], old[j]["pts"][m]):
                    return old[j], old[j]["pts"][m]
        return None

    out = []
    for i, s in enumerate(data.get("labs") or []):
        if not isinstance(s, dict) or i in dups.get("series", ()):
            continue
        drop = dups.get("points", {}).get(i, set()) | dups.get("copies", {}).get(i, set())
        for n, p in enumerate(s.get("pts") or []):
            if n in drop or not isinstance(p, list) or not p:
                continue
            hit = first_match(s, p)
            if hit:
                o, q = hit
                src = sources.get(str(o.get("imp"))) or {}
                out.append({"ref": f"m:{i}:{n}", **describe(s, p), "imp": None,
                            "other": {**describe(o, q), "label": src.get("label") or "", "at": src.get("at")},
                            "key": point_key(s, p), "other_key": point_key(o, q)})
    return out


def public(items):
    """for_preview's list as the page sees it (no point keys)."""
    return [{k: v for k, v in x.items() if k not in ("key", "other_key")} for x in items[:LISTED]]
