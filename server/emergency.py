"""The emergency card (#1126, #1162): what someone helping in an emergency needs, as a file the person carries. Never a
page anyone opens without signing in: no share link, no address.

Nothing goes on it by itself (the owner's decision, #1162): the person makes it in its editor (Sharing → Emergency
card), and the records only suggest. ecard.py keeps what they made (the profile setting emergency.card, encrypted).

suggestions(db, pid, uid) are what the records and About this person offer, each {key, section, text, from, field?}
with Add in the editor: the allergies with their reactions (or a record's own words that there are none), the blood
type from a lab result (blood_from_labs: LOINC 882-1 ABO and Rh group, or 883-9 ABO with 10331-7 Rh, or a series named
so) and the one typed under About this person, the emergency contacts typed there, the current medications with their
dose (summary._medications: Sanguity's own list, #1091), the active problems as conditions, the care team (the
providers and clinics of the visits and appointments), the insurance plans (#1137, insurance.card_lines) and the key
documents on file (#1135, keydocs.on_card); the birth date and the latest clinic weight for Personal details. A
suggestion's key is its section and words hashed: a dose changed is a new suggestion. One already on the card (added
from it, or in the same words) isn't offered again; one there wasn't when the card was saved is "new", and the editor
says how many, never changing the card itself (offered).

view(card, profile, date_format) is the card as its files say it: its name (the legal name, else the profile's),
"Card last updated <date>", its parts in order ({id, heading, lines, print, lock, nouns}; a part with no lines, or on
neither file, left out), and its QR code's text (qr_text) when it has one.

render_pdf(view, paper, form, qr) lays it out on Letter or A4, black and grey only, so it reads the same in greyscale:
form "page" (everything on the printed card, in type that reads at arm's length, onto more pages only if it must) or
"wallet" (a strip a bank card wide, four panels to fold like a fan into one, the cut and folds drawn; what doesn't fit
said as "and N more"). qr: the modules of the page's QR code of the view's qr text (www/js/qr.js, the app's one
encoder), drawn dark on the paper. The PDF's information dictionary holds its title only (pdfdoc.Document(author=None)).
The lock-screen image is drawn on the page (www/js/emergency.js) from the same view's lock parts.
"""
import datetime, hashlib, re

from . import ecard, insurance, keydocs, pdfdoc, person, records, settings, summary

NKA_CODES = {"716186003", "409137002", "429625007", "428607008"}   # SNOMED CT: no known allergy, drug, food, environmental
_NKA = re.compile(r"^\s*(no known|nka\b|nkda\b|no allerg)", re.I)
_ENDED = re.compile(r"^(resolved|inactive|refuted|entered in error)$", re.I)
ABO_RH, ABO, RH = "882-1", "883-9", "10331-7"
_ABO_RH_NAME = re.compile(r"^(abo\s*(/|and|&|\+)?\s*rh|blood (type|group)\b|abo[- ]rh)", re.I)
_ABO_NAME = re.compile(r"^abo( group| type| grouping)?$", re.I)
_RH_NAME = re.compile(r"^rh(\s*\(d\))?( type| typing| factor)?$", re.I)
WALLET_NOTE = "More: see the card in my wallet"   # the lock-screen image, when the person carries the printed card


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


def allergy_line(a):
    return a["name"] + (f" ({', '.join(a['reactions'])})" if a["reactions"] else "") + (f", {a['severity'].lower()}" if a["severity"] else "")


def contact_line(c):
    return c["name"] + (f" ({c['relationship']})" if c["relationship"] else "") + f": {c['phone']}"


def _date(c, d):
    try:
        x = datetime.date.fromisoformat(str(d)[:10])
    except ValueError:
        return str(d or "")
    f = c.get("date_format") or ""
    return x.isoformat() if f == "ymd" else f"{x.day} {x.strftime('%b')} {x.year}" if f == "dmy" else f"{x.strftime('%b')} {x.day}, {x.year}"


# ---------------------------------------------------------------- what the records suggest
def _key(section, text):
    return hashlib.sha256(f"{section}\n{text}".encode()).hexdigest()[:16]


def _providers(data):
    """The care team the records name: each visit's and appointment's provider (with the clinic) or clinic, newest
    first, one per name, at most 12."""
    det, seen = (data or {}).get("details") or {}, {}
    rows = [(str(e.get("t") or ""), det.get(str(e.get("id"))) or {}) for e in (data or {}).get("events") or []
            if isinstance(e, dict) and e.get("k") == "visit" and not e.get("hidden")]
    rows += [(str(u.get("t") or ""), u) for u in (data or {}).get("upcoming") or [] if isinstance(u, dict)]
    for _, d in sorted(rows, key=lambda r: r[0], reverse=True):
        who, where = _text(d.get("who")), _text(d.get("where"))
        if (who or where) and (who or where).lower() not in seen:
            seen[(who or where).lower()] = f"{who}, {where}" if who and where else who or where
    return list(seen.values())[:12]


def _weight(data):
    """The latest clinic weight (clinicWeights: [date, pounds]) as the card says it, and its day; or None."""
    w = sorted((x for x in (data or {}).get("clinicWeights") or [] if isinstance(x, list) and len(x) >= 2
                and isinstance(x[1], (int, float)) and not isinstance(x[1], bool) and x[1] > 0), key=lambda x: str(x[0]))
    return (f"{w[-1][1] * 0.45359237:.1f} kg ({w[-1][1]:.0f} lb)", str(w[-1][0])[:10]) if w else None


# other parts of the records the card suggests from, registered by the features that have them (#1137 insurance
# cards): name -> rows(db, pid, data)
PARTS = {"insurance": lambda db, pid, data: [{"line": p["line"]} for p in insurance.card_lines(db, pid)]}


def suggestions(db, pid, uid):
    """What the records and About this person offer (module docstring), in the sections' order."""
    data = records.assemble(db, pid) or {}
    today = summary.today(db, uid).isoformat()
    about = person.of(settings.merged(db, pid, uid))
    fmt = {"date_format": summary.date_format(db, uid)}
    out = []

    def add(section, text, why, field=None):
        out.append({"key": _key(section + (f".{field}" if field else ""), text), "section": section, "text": text,
                    "from": why, **({"field": field} if field else {})})
    allergies, nka = _allergies(data)
    for a in allergies:
        add("allergies", allergy_line(a), "records")
    if nka and not allergies:
        add("allergies", nka, "records")
    lab = blood_from_labs(data)
    if lab:
        add("blood", f"{lab['said']} (from a lab result of {_date(fmt, lab['date'])})", "records")
    if about["bloodType"]:
        add("blood", person.BLOOD_TYPES[about["bloodType"]], "about")
    for c in about["contacts"]:
        add("contacts", contact_line(c), "about")
    for m in summary._medications(data, today):
        add("medications", m["name"] + (f": {m['instructions']}" if m["instructions"] else ""), "records")
    for p in _problems(data):
        add("conditions", p["name"], "records")
    for line in _providers(data):
        add("careteam", line, "records")
    for i in PARTS["insurance"](db, pid, data):
        add("insurance", i["line"], "insurance")
    for d in keydocs.on_card(data):
        add("documents", d["line"], "records")
    if about["birthDate"]:
        add("details", _date(fmt, about["birthDate"]), "about", "dob")
    w = _weight(data)
    if w:
        add("details", f"{w[0]} on {_date(fmt, w[1])}", "records", "weight")
    return sorted(out, key=lambda x: ecard.IDS.index(x["section"]))


def offered(card, found):
    """The suggestions found less those on the card (added from it, or in its words), each marked "new" when the card
    was saved without it; and the keys of the card's lines added from a suggestion not found now. -> (offered, gone)"""
    taken, words = set(card["detailsFrom"].values()), set()
    for i, s in card["sections"].items():
        taken.update(x["from"] for x in s["lines"] if x["from"])
        words.update((i, x["text"].lower()) for x in s["lines"])
    words.update((f"details.{k}", v.lower()) for k, v in card["details"].items() if v)
    seen, keys = set(card["seen"]), {f["key"] for f in found}
    out = [{**f, "new": bool(card["saved"]) and f["key"] not in seen} for f in found
           if f["key"] not in taken and (f"details.{f['field']}" if "field" in f else f["section"], f["text"].lower()) not in words]
    return out, sorted(k for k in taken if k not in keys)


# ---------------------------------------------------------------- the card as its files say it
NOUNS = {s[0]: s[4] for s in ecard.SECTIONS}
QR_MOST = 800   # the owner's decision (#1162): about 800 characters at most, so it scans at wallet size (version 23)
QR_FIRST = ("alerts", "allergies", "blood", "contacts", "medications")   # kept first when it can't hold everything


def _lines(card, i):
    if i == "details":
        return [f"{label}: {card['details'][k]}" for k, label in ecard.DETAILS if card["details"][k]]
    return [x["text"] for x in card["sections"][i]["lines"]]


def view(card, profile, date_format=""):
    """The card as its files say it (module docstring)."""
    fmt = {"date_format": date_format}
    parts = []
    for i in card["order"]:
        s, lines = card["sections"][i], _lines(card, i)
        if lines and (s["print"] or s["lock"]):
            parts.append({"id": i, "heading": ecard.HEADING[i], "lines": lines, "print": s["print"], "lock": s["lock"],
                          "nouns": list(NOUNS[i])})
    name = card["name"] or profile
    updated = f"Card last updated {_date(fmt, card['saved'])}" if card["saved"] else "Card not saved yet"
    out = {"name": name, "profile": profile, "saved": card["saved"], "updated": updated, "parts": parts,
           "qr": {"on": card["qr"]["on"], "lock": card["qr"]["lock"], "text": "", "left": [], "most": QR_MOST}}
    if card["qr"]["on"]:
        out["qr"]["text"], out["qr"]["left"] = qr_text(name, parts, updated)
    return out


def qr_text(name, parts, updated):
    """The QR code's text: the card's own words (never a link), its parts in order, at most QR_MOST bytes of UTF-8.
    When they don't all fit, critical alerts, allergies, blood type, contacts and medications are kept first, then the
    rest in the card's order, each part's lines from its first until one doesn't fit. -> (text, what was left out:
    [{id, heading, left, of}] in the card's order)"""
    head, foot = f"EMERGENCY CARD\n{name}", f"\n{updated}"
    room = QR_MOST - len((head + foot).encode())
    by, taken, left = {p["id"]: p for p in parts}, {}, []
    for i in [i for i in QR_FIRST if i in by] + [p["id"] for p in parts if p["id"] not in QR_FIRST]:
        p, n = by[i], 0
        for line in p["lines"]:
            cost = len(f"\n- {line}".encode()) + (0 if n else len(f"\n{p['heading'].upper()}".encode()))
            if cost > room:
                break
            room, n = room - cost, n + 1
        taken[i] = n
        if n < len(p["lines"]):
            left.append({"id": i, "heading": p["heading"], "left": len(p["lines"]) - n, "of": len(p["lines"])})
    body = "".join(f"\n{p['heading'].upper()}" + "".join(f"\n- {x}" for x in p["lines"][:taken[p['id']]])
                   for p in parts if taken.get(p["id"]))
    order = [p["id"] for p in parts]
    return head + body + foot, sorted(left, key=lambda x: order.index(x["id"]))


def lock_parts(v):
    return [p for p in v["parts"] if p["lock"]]


# ---------------------------------------------------------------- the PDF
PAPER = {"letter": (612, 792), "a4": (595.28, 841.89)}
MARGIN = 40
WALLET_W, WALLET_H = 243, 153   # 3.375 by 2.125 in, a bank card's size: each of the wallet strip's four panels
GREY = 0.35


def filename(v, kind, form="page"):
    slug = re.sub(r"[^A-Za-z0-9]+", "-", v["profile"]).strip("-")[:40] or "profile"
    return f"emergency-card-{slug}{'-wallet' if form == 'wallet' else ''}-{v['saved'] or 'unsaved'}.{kind}"


def _rows(parts, shown, size, room, more):
    """The parts as rows to draw at size within room points: [(x offset, text, size, bold, grey, space above)]; shown
    is how many lines of each part, the rest said as "and N more: <more>". A critical alert is in bold."""
    out = []
    for p, k in zip(parts, shown):
        out.append((0, p["heading"].upper(), size * 0.8, True, False, size * 0.5 if out else 0))
        bold = p["id"] == "alerts"
        for n, t in enumerate(p["lines"][:k]):
            out += [(8, w, size, bold, False, size * 0.15 if n and i == 0 else 0) for i, w in enumerate(pdfdoc.wrap(t, size, room - 8, bold))]
        if k < len(p["lines"]):
            out.append((8, f"and {len(p['lines']) - k} more: {more}", size, False, True, 0))
    return out


def _column(page, rows, x, top, bottom, folds=()):
    """Draws rows (when page is given) from top down; a row that would cross a fold starts below it. -> the end's y"""
    y = top
    for dx, t, size, bold, grey, above in rows:
        y -= above
        h = size * 1.3
        for f in folds:
            if y - h < f < y + 1:
                y = f - 4
        if page is not None and y - h >= bottom - 0.5:
            page.text(x + dx, y - size, t, size, bold, GREY if grey else 0)
        y -= h
    return y


def _fit(page, parts, x, top, bottom, room, sizes, more, folds=()):
    """parts drawn in the largest of sizes that fits between top and bottom; where even the smallest doesn't, the longest
    parts show fewer lines ("and N more", counted, never cut silently), then the last parts none. -> lines left out"""
    for size in sizes:
        shown = [len(p["lines"]) for p in parts]
        if _column(None, _rows(parts, shown, size, room, more), x, top, bottom, folds) >= bottom:
            break
    fits = lambda: _column(None, _rows(parts, shown, size, room, more), x, top, bottom, folds) >= bottom   # noqa: E731
    while not fits() and max(shown, default=0) > 1:
        i = max(range(len(shown)), key=lambda n: shown[n])
        shown[i] -= 1
    while not fits() and any(shown):
        shown[max(i for i, k in enumerate(shown) if k)] = 0
    _column(page, _rows(parts, shown, size, room, more), x, top, bottom, folds)
    return sum(len(p["lines"]) - k for p, k in zip(parts, shown))


def _qr(page, modules, x, y, side):
    """The QR code's modules, dark on the paper, with their 4-module quiet zone, in a square side wide from (x, y) down."""
    m = side / (len(modules) + 8)
    for r, row in enumerate(modules):
        for run in re.finditer("1+", row):
            page.rect(x + (run.start() + 4) * m, y - (r + 5) * m, len(run.group()) * m, m * 1.02, fill=0)


def _qr_note(v):
    left = v["qr"]["left"]
    return "" if not left else "Not in the QR code (too long for one): " + ", ".join(
        f"{x['heading'].lower()}" if x["left"] == x["of"] else f"{x['left']} of {x['of']} {x['heading'].lower()}" for x in left) + "."


QR_SAYS = "Scan with a phone's camera: the card's text, read offline. It isn't a link."


def render_pdf(v, paper="letter", form="page", qr=None):
    """The card on Letter or A4 (module docstring). qr: the QR code's rows of "0" and "1", or None."""
    W, H = PAPER[paper]
    doc = pdfdoc.Document(title=f"Emergency card for {v['name']}", author=None, size=(W, H))
    parts = [p for p in v["parts"] if p["print"]]
    qr = qr if v["qr"]["on"] else None
    (_page if form == "page" else _wallet)(doc, v, parts, W, H, qr)
    return doc.output()


def _page(doc, v, parts, W, H, qr):
    p = doc.page()
    y = H - MARGIN
    p.text(MARGIN, y - 22, "EMERGENCY CARD", 22, True, 0)
    p.text_right(W - MARGIN, y - 12, v["updated"], 11, False, GREY)
    y -= 50
    for line in pdfdoc.wrap(v["name"], 18, W - 2 * MARGIN, True):
        p.text(MARGIN, y - 18, line, 18, True, 0)
        y -= 22
    p.line(MARGIN, y - 4, W - MARGIN, y - 4, 1.2, 0)
    y -= 14
    bottom, room = MARGIN, W - 2 * MARGIN
    if qr:   # the QR code at the foot, what it is beside it
        side = 150
        _qr(p, qr, W - MARGIN - side, MARGIN + side, side)
        ty = MARGIN + side - 14
        for t in pdfdoc.wrap(QR_SAYS, 10, room - side - 16) + pdfdoc.wrap(_qr_note(v), 10, room - side - 16):
            p.text(MARGIN, ty, t, 10, False, GREY)
            ty -= 13
        bottom = MARGIN + side + 12
    for size in (13, 12, 11, 10):
        rows = _rows(parts, [len(x["lines"]) for x in parts], size, room, "")
        if _column(None, rows, MARGIN, y, bottom) >= bottom:
            break
    # what doesn't fit one page goes on the next (never cut: this is the whole card)
    at, top = 0, y
    while at < len(rows):
        n, yy = at, top
        while n < len(rows):
            dx, t, size, bold, grey, above = rows[n]
            if yy - above - size * 1.3 < bottom and n > at:
                break
            yy -= above + size * 1.3
            n += 1
        _column(p, [(r[0], r[1], r[2], r[3], r[4], r[5] if i else 0) for i, r in enumerate(rows[at:n])], MARGIN, top, bottom)
        at = n
        if at < len(rows):
            p = doc.page()
            p.text(MARGIN, H - MARGIN - 12, f"EMERGENCY CARD (continued): {v['name']}", 11, True, 0)
            top, bottom = H - MARGIN - 26, MARGIN


def _wallet(doc, v, parts, W, H, qr):
    p = doc.page()
    top, x, pad = H - MARGIN - 20, MARGIN, 8
    p.text(MARGIN, H - MARGIN - 9, "EMERGENCY CARD: WALLET CARD", 11, True, 0)
    bottom = top - 4 * WALLET_H
    p.box(x, bottom, WALLET_W, 4 * WALLET_H, 0.9, 0)   # the cut
    folds = [top - k * WALLET_H for k in (1, 2, 3)]
    for f in folds:
        p.line(x, f, x + WALLET_W, f, 0.5, GREY, dash=(3, 3))
    p.text(x + pad, top - pad - 10, "EMERGENCY", 10, True, 0)
    nm = pdfdoc.wrap(v["name"], 9, WALLET_W - 2 * pad, True)
    for i, t in enumerate(nm[:2]):
        p.text(x + pad, top - pad - 22 - 10 * i, t, 9, True, 0)
    under = top - pad - 22 - 10 * min(len(nm), 2)
    p.text(x + pad, under, v["updated"], 6, False, GREY)
    text_bottom = bottom + pad
    if qr:   # the last panel: the QR code, and what it is beside it
        side = WALLET_H - 2 * pad + 4
        _qr(p, qr, x + pad - 4, bottom + WALLET_H - pad + 2, side)
        ty = bottom + WALLET_H - pad - 8
        for t in pdfdoc.wrap(QR_SAYS, 6, WALLET_W - side - 2 * pad):
            p.text(x + side + pad, ty, t, 6, False, GREY)
            ty -= 8
        text_bottom = bottom + WALLET_H + pad
    left = _fit(p, parts, x + pad - 4, under - 6, text_bottom, WALLET_W - 2 * pad + 4, (8, 7.5, 7, 6.5, 6, 5.5), "see the full-page card",
                [f for f in folds if f > text_bottom])
    # beside it: how to cut and fold it
    ix, room = x + WALLET_W + 28, W - MARGIN - (x + WALLET_W + 28)
    say = ["Cut out along the solid outline.", "Fold along the dashed lines, back and forth like a fan, into a card the "
           "size of a bank card, the panel with EMERGENCY and the name on the outside.", v["updated"] + "."]
    if left:
        say.append(f"{left} line{'s' if left != 1 else ''} didn't fit the wallet card: the full-page card has everything.")
    if qr:
        say += [QR_SAYS] + ([_qr_note(v)] if _qr_note(v) else [])
    ty = top - 4
    for s in say:
        for t in pdfdoc.wrap(s, 10, room):
            p.text(ix, ty - 10, t, 10, False, GREY if s is not say[0] and s is not say[1] else 0)
            ty -= 13
        ty -= 6
    return left
