"""The emergency card as the person made it (#1162): what they chose to put on it, and where. Nothing goes on it by
itself: the records only suggest (emergency.suggestions), and a line is on the card once someone adds it here.

Kept as the profile setting emergency.card (settings.py: sealed, so encrypted like every setting that can hold health
information; in the profile's settings.json in an export and loaded back with it; backed up with the database). Owners
and editors change it (routes_emergency.py); everyone the profile is shared with can make its files.

  name         the full legal name for the card, "" for the profile's own name
  details      {dob, language, hospital, donor, weight}: Personal details, each "" when not given
  detailsFrom  {field: the suggestion's key} for a detail that came from a suggestion (so it isn't suggested again)
  order        the sections' ids in the card's order; Critical alerts always first
  sections     {id: {print, lock, lines: [{text, from}]}}: print and lock say whether it's on the printed card and
               the lock screen; lines in order, from the suggestion's key a line was added from ("" when typed)
  qr           {on, lock}: a QR code of the card's text on the printed card (on) and the lock screen too (lock)
  seen         the keys of the suggestions there were when it was saved: those since are "new"
  saved        YYYY-MM-DD, the day it was last saved ("" never): "Card last updated" on both files

check(v) gives the value cleaned (raising ValueError, said plainly); empty() a card with nothing on it.
"""
import re

# id, heading, on the printed card and the lock screen at first (the owner's decision: the lock screen has the name,
# critical alerts, allergies, blood type and contacts), and what its lines are ("and 2 more allergies")
SECTIONS = (("alerts", "Critical alerts", True, True, ("alert", "alerts")),
            ("details", "Personal details", True, False, ("detail", "details")),
            ("allergies", "Allergies", True, True, ("allergy", "allergies")),
            ("blood", "Blood type", True, True, ("line", "lines")),
            ("contacts", "Emergency contacts", True, True, ("contact", "contacts")),
            ("medications", "Current medications", True, False, ("medication", "medications")),
            ("conditions", "Conditions", True, False, ("condition", "conditions")),
            ("careteam", "Care team", True, False, ("provider", "providers")),
            ("devices", "Devices and implants", True, False, ("device", "devices")),
            ("insurance", "Insurance", True, False, ("plan", "plans")),
            ("documents", "Key documents", True, False, ("document", "documents")))
IDS = tuple(s[0] for s in SECTIONS)
HEADING = {s[0]: s[1] for s in SECTIONS}
DETAILS = (("dob", "Date of birth"), ("language", "Preferred language"), ("hospital", "Preferred hospital"),
           ("donor", "Organ donor"), ("weight", "Weight"))
MOST_LINES, MOST_TEXT, MOST_NAME, MOST_SEEN = 40, 300, 120, 600
_DAY = re.compile(r"^(\d{4}-\d{2}-\d{2})?$")


def empty():
    return {"name": "", "details": {k: "" for k, _ in DETAILS}, "detailsFrom": {}, "order": list(IDS),
            "sections": {i: {"print": p, "lock": lk, "lines": []} for i, _, p, lk, _ in SECTIONS},
            "qr": {"on": False, "lock": False}, "seen": [], "saved": ""}


def _text(v, most, what):
    if v is None:
        return ""
    if not isinstance(v, str):
        raise ValueError(f"The card's {what} must be text.")
    v = " ".join(v.split())
    if len(v) > most:
        raise ValueError(f"The card's {what} is at most {most} characters.")
    return v


def _key(v):
    return v if isinstance(v, str) and re.fullmatch(r"[0-9a-f]{0,32}", v) else ""


def check(v):
    """The card cleaned: anything left out takes empty()'s, unknown sections and fields are dropped."""
    if not isinstance(v, dict):
        raise ValueError("The emergency card must be an object.")
    out = empty()
    out["name"] = _text(v.get("name"), MOST_NAME, "name")
    d = v.get("details") if isinstance(v.get("details"), dict) else {}
    for k, label in DETAILS:
        out["details"][k] = _text(d.get(k), MOST_NAME, label.lower())
    f = v.get("detailsFrom") if isinstance(v.get("detailsFrom"), dict) else {}
    out["detailsFrom"] = {k: _key(f[k]) for k, _ in DETAILS if _key(f.get(k)) and out["details"][k]}
    order = [i for i in (v.get("order") or []) if i in IDS] if isinstance(v.get("order"), list) else []
    order = list(dict.fromkeys(order)) + [i for i in IDS if i not in order]
    out["order"] = ["alerts"] + [i for i in order if i != "alerts"]
    secs = v.get("sections") if isinstance(v.get("sections"), dict) else {}
    for i in IDS:
        s = secs.get(i)
        if not isinstance(s, dict):
            continue
        o = out["sections"][i]
        o["print"], o["lock"] = bool(s.get("print", o["print"])), bool(s.get("lock", o["lock"]))
        lines = s.get("lines") or []
        if not isinstance(lines, list):
            raise ValueError(f"{HEADING[i]}: its lines must be a list.")
        if len(lines) > MOST_LINES:
            raise ValueError(f"{HEADING[i]}: at most {MOST_LINES} lines.")
        for x in lines:
            x = x if isinstance(x, dict) else {"text": x}
            t = _text(x.get("text"), MOST_TEXT, f"line under {HEADING[i]}")
            if t:
                o["lines"].append({"text": t, "from": _key(x.get("from"))})
    q = v.get("qr") if isinstance(v.get("qr"), dict) else {}
    out["qr"] = {"on": bool(q.get("on")), "lock": bool(q.get("on")) and bool(q.get("lock"))}
    seen = v.get("seen") if isinstance(v.get("seen"), list) else []
    out["seen"] = list(dict.fromkeys(k for k in map(_key, seen) if k))[:MOST_SEEN]
    saved = v.get("saved") if isinstance(v.get("saved"), str) else ""
    out["saved"] = saved if _DAY.match(saved) else ""
    return out
