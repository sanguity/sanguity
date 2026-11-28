"""The emergency card (#1126): what someone helping in an emergency needs, from a profile's records and its own
About fields, as a file the person carries. Never a page anyone opens without signing in: no share link, no address.

card(db, pid, uid, name) is the card as a plain object, the page's preview and both files made from it:
  allergies    the records' allergies (problems of type "Allergy", not resolved, inactive, refuted or entered in
               error), each with its reactions and severity as recorded, one per substance. A record that says there
               are no known allergies (NKA_CODES, or its words say so) is "nka": its own words, said only while no
               allergy is recorded. Neither: "No allergies recorded", never "No known allergies".
  medications  the current medications, Sanguity's own list (summary._medications: the records' with your own
               entries and changes, those hidden left out, #1091, #1093)
  problems     the problem list's active problems (status "active", as the Overview's), one per name
  blood        the latest blood type result in the records (blood_from_labs: LOINC 882-1 ABO and Rh group, or 883-9
               ABO with 10331-7 Rh, or a series named so), "from your lab result of <date>"; else the one typed under
               About this person; else none
  contacts     the emergency contacts typed under About this person, in order
  asOf         today, for the person making it: everything on the card is as of then
  documents    [{kind, kept}] key documents marked as on file (#1135, keydocs.py), said without the document
  insurance    [] for now: a place for #1137's insurance cards (plan, member ID, group number), shown only when there
               are some
Nothing is guessed: an empty part says "None recorded".

render_pdf(card, paper) lays it out on one page, Letter or A4: everything, in type that reads at arm's length, and
under it a wallet-size cut-out (3.375 by 2.125 in, front and back, to fold), black and grey only, so it reads the same
in greyscale. The PDF's information dictionary holds its title only (pdfdoc.Document(author=None)).
The lock-screen image is drawn on the page (www/js/emergency.js) from the same object: the name, allergies with their
reactions, blood type and contacts; "Medications and conditions: see the card in my wallet" only when the person
turns it on.
"""
import datetime, re

from . import keydocs, pdfdoc, person, records, settings, summary

NKA_CODES = {"716186003", "409137002", "429625007", "428607008"}   # SNOMED CT: no known allergy, drug, food, environmental
_NKA = re.compile(r"^\s*(no known|nka\b|nkda\b|no allerg)", re.I)
_ENDED = re.compile(r"^(resolved|inactive|refuted|entered in error)$", re.I)
ABO_RH, ABO, RH = "882-1", "883-9", "10331-7"
_ABO_RH_NAME = re.compile(r"^(abo\s*(/|and|&|\+)?\s*rh|blood (type|group)\b|abo[- ]rh)", re.I)
_ABO_NAME = re.compile(r"^abo( group| type| grouping)?$", re.I)
_RH_NAME = re.compile(r"^rh(\s*\(d\))?( type| typing| factor)?$", re.I)
NONE = "None recorded"
NO_ALLERGIES = "No allergies recorded"
WALLET_NOTE = "Medications and conditions: see the card in my wallet"


def _text(v):
    return " ".join(str(v or "").split())


def _results(s):
    """A series' results as (time, words as reported), oldest first: its text results and its points' values."""
    out = [(str(t[0]), _text(t[1])) for t in s.get("txt") or [] if isinstance(t, list) and len(t) >= 2]
    out += [(str(p[0]), _text(p[5] if len(p) > 5 and p[5] not in (None, "") else p[1])) for p in s.get("pts") or []
            if isinstance(p, list) and p]
    return sorted(x for x in out if x[1])


def _abo(words):
    m = re.search(r"(?<![A-Z])(AB|A|B|O)(?![A-Z])", words.upper())
    return m.group(1) if m else None


def _rh(words):
    w = words.upper()
    if re.search(r"NEG|(?<![A-Z])RH\s*-|-\s*$|\bD-", w):
        return "-"
    if re.search(r"POS|(?<![A-Z])RH\s*\+|\+\s*$|\bD\+", w):
        return "+"
    return None


def blood_from_labs(data):
    """The latest blood type in the records: {"type": "A+" (None when the result's words don't say one plainly),
    "said": the words shown, "date": YYYY-MM-DD, "reported": the result as reported}, or None."""
    both, abo, rh = [], [], []
    for s in (data or {}).get("labs") or []:
        if not isinstance(s, dict) or s.get("watch"):
            continue
        code, name = str(s.get("loinc") or "").strip(), _text(s.get("name"))
        if re.search(r"antibod|screen|antigen|weak", name, re.I):
            continue
        part = both if code == ABO_RH or (not code and _ABO_RH_NAME.search(name)) else \
            abo if code == ABO or (not code and _ABO_NAME.match(name)) else \
            rh if code == RH or (not code and _RH_NAME.match(name)) else None
        if part is not None:
            part.extend(_results(s))
    found = []
    if both:
        t, w = both[-1]
        found.append((t, w, _abo(w), _rh(w)))
    if abo:
        t, w = abo[-1]
        r = rh[-1] if rh else None
        found.append((max(t, r[0]) if r else t, w + (f", Rh {r[1]}" if r else ""), _abo(w), _rh(r[1]) if r else None))
    if not found:
        return None
    t, words, a, r = max(found, key=lambda x: x[0])
    typ = f"{a}{r}" if a and r else None
    said = person.BLOOD_TYPES[typ] if typ else f"{a} (Rh not in the result)" if a and not r else f"{words} (as reported)"
    return {"type": typ, "said": said, "date": t[:10], "reported": words}


def _allergies(data):
    """(allergies [{name, reactions, severity, date}], the words of a record saying there are none, or "")."""
    seen, nka = {}, ""
    for p in (data or {}).get("problems") or []:
        if not isinstance(p, dict) or p.get("type") != "Allergy" or _ENDED.match(_text(p.get("status"))):
            continue
        name = _text(p.get("substance")) or re.sub(r"^Allergy:\s*", "", _text(p.get("name")))
        if str(p.get("code") or "") in NKA_CODES or _NKA.match(name):
            nka = nka or (name if _NKA.match(name) else "No known allergies")
            continue
        if not name:
            continue
        a = seen.setdefault(name.lower(), {"name": name, "reactions": [], "severity": "", "date": ""})
        for r in p.get("reactions") or []:
            r = _text(r)
            if r and r.lower() not in (x.lower() for x in a["reactions"]):
                a["reactions"].append(r)
        a["severity"] = a["severity"] or _text(p.get("severity"))
        a["date"] = max(a["date"], str(p.get("date") or "")[:10])
    return sorted(seen.values(), key=lambda a: a["name"].lower()), nka


def _problems(data):
    seen = {}
    for p in (data or {}).get("problems") or []:
        if isinstance(p, dict) and p.get("type") != "Allergy" and _text(p.get("status")).lower() == "active" and _text(p.get("name")):
            q = seen.setdefault(_text(p["name"]).lower(), {"name": _text(p["name"]), "date": ""})
            q["date"] = max(q["date"], str(p.get("date") or "")[:10])
    return sorted(seen.values(), key=lambda p: p["name"].lower())


# other parts of the card, registered by the features that have them (#1135 key documents; #1137 insurance cards):
# name -> rows(db, pid, data) giving the list the card holds under that name
PARTS = {}


def card(db, pid, uid, name):
    """The card as a plain object (module docstring)."""
    data = records.assemble(db, pid) or {}
    today = summary.today(db, uid).isoformat()
    about = person.of(settings.merged(db, pid, uid))
    allergies, nka = _allergies(data)
    lab = blood_from_labs(data)
    typed = about["bloodType"]
    blood = ({"said": lab["said"], "from": "lab", "date": lab["date"], "reported": lab["reported"]} if lab else
             {"said": person.BLOOD_TYPES[typed], "from": "typed", "date": ""} if typed else None)
    meds = [{"name": m["name"], "instructions": m["instructions"]} for m in summary._medications(data, today)]
    out = {"profile": name, "asOf": today, "date_format": summary.date_format(db, uid),
           "allergies": allergies, "nka": "" if allergies else nka, "medications": meds, "problems": _problems(data),
           "blood": blood, "contacts": about["contacts"], "documents": keydocs.on_card(data), "insurance": []}
    for k, rows in PARTS.items():
        out[k] = rows(db, pid, data)
    return out


def allergy_line(a):
    return a["name"] + (f" ({', '.join(a['reactions'])})" if a["reactions"] else "") + (f", {a['severity'].lower()}" if a["severity"] else "")


def contact_line(c):
    return c["name"] + (f" ({c['relationship']})" if c["relationship"] else "") + f": {c['phone']}"


def blood_line(c):
    b = c["blood"]
    if not b:
        return NONE
    return b["said"] + (f" (from your lab result of {_date(c, b['date'])})" if b["from"] == "lab" else " (as typed in)")


def _date(c, d):
    try:
        x = datetime.date.fromisoformat(str(d)[:10])
    except ValueError:
        return str(d or "")
    f = c.get("date_format") or ""
    return x.isoformat() if f == "ymd" else f"{x.day} {x.strftime('%b')} {x.year}" if f == "dmy" else f"{x.strftime('%b')} {x.day}, {x.year}"


def sections(c):
    """The card's parts in order: [(heading, [lines])], each with its lines as the card says them."""
    out = [("Allergies", [allergy_line(a) for a in c["allergies"]] or [c["nka"] or NO_ALLERGIES]),
           ("Blood type", [blood_line(c)]),
           ("Emergency contacts", [contact_line(x) for x in c["contacts"]] or [NONE]),
           ("Current medications", [m["name"] + (f": {m['instructions']}" if m["instructions"] else "") for m in c["medications"]] or [NONE]),
           ("Active problems", [p["name"] for p in c["problems"]] or [NONE])]
    if c.get("documents"):
        out.append(("Key documents", [d["line"] for d in c["documents"]]))
    if c.get("insurance"):   # #1137: [{"line"}], shown only when there are some
        out.append(("Insurance", [i["line"] for i in c["insurance"]]))
    return out


def short(c):
    """The lock-screen image's parts (the owner's decision: short): allergies with their reactions, blood type and
    emergency contacts, [{heading, lines}]. Medications and problems are never on it."""
    return [{"heading": "Allergies", "lines": [allergy_line(a) for a in c["allergies"]] or [c["nka"] or NO_ALLERGIES]},
            {"heading": "Blood type", "lines": [c["blood"]["said"] if c["blood"] else NONE]},
            {"heading": "Emergency contacts", "lines": [contact_line(x) for x in c["contacts"]] or [NONE]}]


# ---------------------------------------------------------------- the PDF
PAPER = {"letter": (612, 792), "a4": (595.28, 841.89)}
MARGIN = 40
WALLET_W, WALLET_H = 243, 153   # 3.375 by 2.125 in, a bank card's size
GREY = 0.35


def filename(c, kind):
    slug = re.sub(r"[^A-Za-z0-9]+", "-", c["profile"]).strip("-")[:40] or "profile"
    return f"emergency-card-{slug}-{c['asOf']}.{kind}"


def _fit(page, lines_of, x, y, room_w, room_h, sizes, more="see the full card"):
    """Draws [(heading, lines)] from y down within room_h, in the largest size of sizes that fits. Where even the
    smallest doesn't, the longest parts show fewer lines, each ending "and N more: <more>" (counted, never cut
    silently), so every part keeps its heading and its first line. Gives (the size used, lines left out)."""
    def wrapped(size):
        return [[pdfdoc.wrap(t, size, room_w - 8) for t in lines] for _, lines in lines_of]

    def need(size, rows, shown):
        lead = size * 1.3
        return sum(size * 1.95 + lead * (sum(len(w) for w in r[:k]) + (k < len(r))) for r, k in zip(rows, shown))
    for size in sizes:
        rows = wrapped(size)
        shown = [len(r) for r in rows]
        if need(size, rows, shown) <= room_h:
            break
    while need(size, rows, shown) > room_h and max(shown) > 1:
        i = max(range(len(shown)), key=lambda n: shown[n])
        shown[i] -= 1
    lead, left = size * 1.3, 0
    for (head, _), r, k in zip(lines_of, rows, shown):
        page.text(x, y - size, head.upper(), size * 0.8, True, 0)
        y -= size * 1.45
        for w in (w for one in r[:k] for w in one):
            page.text(x + 8, y - size, w, size, False, 0)
            y -= lead
        if k < len(r):
            page.text(x + 8, y - size, f"and {len(r) - k} more: {more}", size, False, GREY)
            y -= lead
            left += len(r) - k
        y -= size * 0.5
    return size, left


def render_pdf(c, paper="letter"):
    """The card as one page of PDF, Letter or A4."""
    W, H = PAPER[paper]
    doc = pdfdoc.Document(title=f"Emergency card for {c['profile']}", author=None, size=(W, H))
    p = doc.page()
    parts = sections(c)
    y = H - MARGIN
    p.text(MARGIN, y - 22, "EMERGENCY CARD", 22, True, 0)
    p.text_right(W - MARGIN, y - 12, f"As of {_date(c, c['asOf'])}", 11, False, GREY)
    p.text_right(W - MARGIN, y - 25, "from the records in Sanguity", 9, False, GREY)
    y -= 50
    for line in pdfdoc.wrap(c["profile"], 18, W - 2 * MARGIN, True):
        p.text(MARGIN, y - 18, line, 18, True, 0)
        y -= 22
    p.line(MARGIN, y - 4, W - MARGIN, y - 4, 1.2, 0)
    y -= 14
    cut = MARGIN + WALLET_H + 22   # the wallet card's top, with its label and the cut line above it
    _fit(p, parts, MARGIN, y, W - 2 * MARGIN, y - cut - 16, (13, 12, 11, 10, 9), "in Sanguity")
    # the wallet-size cut-out: front (who, allergies, blood type, contacts) and back (medications, problems, more)
    wx = (W - 2 * WALLET_W) / 2
    p.line(MARGIN, cut + 8, W - MARGIN, cut + 8, 0.5, GREY, dash=(2, 3))
    p.text(MARGIN, cut, "Cut out along the outline and fold in the middle: a wallet-size card.", 9, False, GREY)
    top = MARGIN + WALLET_H
    p.box(wx, MARGIN, 2 * WALLET_W, WALLET_H, 0.9, 0)   # the cut
    p.line(wx + WALLET_W, MARGIN, wx + WALLET_W, top, 0.5, GREY, dash=(3, 3))   # the fold
    pad = 8
    p.text(wx + pad, top - pad - 9, "EMERGENCY", 9, True, 0)
    p.text_right(wx + WALLET_W - pad, top - pad - 8, f"as of {_date(c, c['asOf'])}", 6, False, GREY)
    nm = pdfdoc.wrap(c["profile"], 9, WALLET_W - 2 * pad, True)[0]
    p.text(wx + pad, top - pad - 20, nm, 9, True, 0)
    _fit(p, [parts[0], parts[1], parts[2]], wx + pad - 4, top - pad - 24, WALLET_W - 2 * pad + 4, WALLET_H - 2 * pad - 24, (7, 6.5, 6, 5.5))
    _fit(p, parts[3:], wx + WALLET_W + pad - 4, top - pad, WALLET_W - 2 * pad + 4, WALLET_H - 2 * pad, (7, 6.5, 6, 5.5))
    return doc.output()
