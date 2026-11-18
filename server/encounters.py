"""Encounters that weren't visits, visits titled by their billing code, and appointment reminders (#236).

A portal's encounter list holds more than appointments: chart reviews and result checks, phone calls, care
management, faxes, refills, documentation and orders-only encounters. Importers file them all as "visit", so
tidy() moves them to their own kind, CONTACT ("Chart activity"), which nothing counts as a visit (the Overview,
the visit summary, share links). It also gives a visit titled by its procedure code's description ("PR
OFFICE/OUTPATIENT ESTABLISHED HIGH MDM 40 MIN") a plain name, keeping the description in its detail ("billed"),
and makes every appointment reminder an "appt_msg" (some sources send them as automated messages).

It runs when the records are put together (records._assemble), on every stored event whatever import brought it,
so records imported before this are fixed without a re-import and every source is treated alike. Events keep their
source keys, and an event it changes keeps what it was in "orig" until the corrections have been applied
(edits.keys_of finds an event by its kind and title as imported), so corrections made before still find their
events; records._assemble drops "orig" after that. A correction (edits.py) then has the last word: a person can
move an entry back, or anywhere else.
"""
import re

CONTACT = "contact"

# Encounter titles that aren't an appointment someone went to (or joined by video or phone): matched against the
# whole title, case-insensitively. A scheduled telephone or video visit is a visit; a phone call is chart activity.
_NOT_VISIT = re.compile(r"""^\s*(?:
      chart\s*(?:review|check|activity|abstract(?:ion)?|correction)\b.*
    | (?:results?|lab|test)\s*(?:check|review|follow[\s-]*up|note)\b.*
    | (?:tele)?phone(?:\s*(?:call|encounter|note|message|contact))?
    | (?:tele)?phone\s+(?:call|encounter|note|message|contact)\b.*
    | call(?:\s*(?:out|back|in))?(?:\s+to\s+patient)?\b.*
    | (?:patient\s*)?(?:portal\s*|mychart\s*)?message\b.*
    | (?:e[\s-]?mail|letter)(?:\s*\((?:in|out)\))?\b.*
    | (?:\w+\s+)?(?:lab\s+)?fax\b.*
    | refill(?:\s*request)?\b.*
    | (?:medication\s+)?(?:refill|renewal)\b.*
    | orders?\s*only\b.*
    | documentation(?:\s*only)?\b.*
    | scann?(?:ed|ing)\b.*
    | (?:nurse\s+|rn\s+|pcm\s+rn\s+)?care\s*management\b.*
    | pcm\s+rn\b.*
    | questionnaire\b.*
    | (?:history|abstract)\s*$
    | erroneous\s+encounter\b.*
    | (?:outside|external)\s+(?:records?|results?)\b.*
    | prior\s*auth(?:orization)?\b.*
    | (?:referral|authorization)(?:\s*request)?\s*$
)\s*$""", re.I | re.X)

# A procedure code's description as a visit's title (Epic writes "PR " before it), and a plain name for it
_BILLING = re.compile(r"^\s*(?:PR\s+|\d{5}\s+)?(?P<d>[A-Z0-9/&,\-\s().]{6,})$")
_PLAIN = [   # (pattern in the description, plain name); first match wins
    (r"SYNCHRONOUS\s+AUDIO[\s-]*VIDEO|VIDEO\s+V", "Video visit"),
    (r"SYNCHRONOUS\s+AUDIO[\s-]*ONLY|TELEPHONE\s+(?:E/M|EVAL|ASSESSMENT)|PHONE\s+E/M|PHYS/QHP\s+TELEPHONE", "Phone visit"),
    (r"ONLINE\s+DIGITAL|E[\s-]*VISIT|ONLINE\s+E/M", "Online visit"),
    (r"EMERGENCY\s+(?:DEPT|DEPARTMENT)", "Emergency visit"),
    (r"(?:INITIAL|SUBSQ|SUBSEQUENT)\s+HOSPITAL|HOSPITAL\s+(?:INPATIENT|CARE)|INPATIENT/OBSERVATION|OBSERVATION\s+CARE|DISCHARGE\s+DAY", "Hospital visit"),
    (r"CONSULT", "Consultation"),
    (r"PREVENTIVE|PREV\s+VISIT|ANNUAL\s+WELLNESS|WELL\s+(?:CHILD|VISIT)", "Preventive visit"),
    (r"NURSE\s+VISIT|MINIMAL\s+PROBLEM|99211", "Nurse visit"),
    (r"OFFICE/OUTPATIENT\s+(?:VISIT\s+)?NEW|OFFICE\s+(?:VISIT\s+)?NEW|\bNEW\s+PATIENT", "Office visit (new patient)"),
    (r"OFFICE/OUTPATIENT|OFFICE\s+VISIT|OFFICE\s+O/P|OUTPATIENT\s+VISIT", "Office visit"),
]
_PLAIN = [(re.compile(p), name) for p, name in _PLAIN]
# billing descriptions are written in capitals, and say what was billed: these words mark one
_BILLING_WORDS = re.compile(r"\b(?:E/M|MDM|MIN(?:UTES)?|EST(?:ABLISHED)?|SYNCHRONOUS|OFFICE/OUTPATIENT|VST|LEVEL\s+\d|"
                            r"HOSPITAL\s+CARE|SUBSQ)\b")

# Appointment reminders, whichever kind of message a source made them
_REMINDER = re.compile(r"^\s*(?:upcoming\s+)?appointment\s+(?:reminder|scheduled|confirmation|confirmed|"
                       r"cancel(?:l?ed|lation)|reschedul(?:ed|ing)|request(?:ed)?)\b|^\s*reminder:?\s+.*appointment", re.I)
_TITLE_DATE = re.compile(r"\s+(?:for|on)\s+(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})\s*$", re.I)

VISIT_NOTE = "Recorded by the portal as an encounter in the chart; it wasn't a visit."


def not_a_visit(title):
    """Whether an encounter with this title was chart activity rather than a visit."""
    return bool(title) and bool(_NOT_VISIT.match(str(title)))


def plain_visit_title(title):
    """A plain name for a visit titled by its procedure code's description, or None when the title isn't one."""
    m = _BILLING.match(str(title or ""))
    if not m or not _BILLING_WORDS.search(m.group("d").upper()) or m.group("d") != m.group("d").upper():
        return None
    d = m.group("d").upper()
    return next((name for p, name in _PLAIN if p.search(d)), None)


def _without_own_date(title, t):
    """The title without a trailing "for 3/27/26" or "on 3/27/26" that is the message's own date."""
    m = _TITLE_DATE.search(title)
    if not m or not t:
        return title
    mo, dd, yy = (int(g) for g in m.groups())
    yy = yy + 2000 if yy < 100 else yy
    return title[:m.start()].rstrip() if str(t)[:10] == f"{yy:04d}-{mo:02d}-{dd:02d}" else title


def tidy(events, details):
    """Reclassify and retitle events in place (see the module's docstring); returns how many changed."""
    n = 0
    for e in events:
        if not isinstance(e, dict):
            continue
        k, title = e.get("k"), e.get("title")
        if not isinstance(title, str):
            continue
        new_k, new_title, billed = k, title, None
        if k == "visit":
            if not_a_visit(title):
                new_k = CONTACT
            elif (plain := plain_visit_title(title)):
                new_title, billed = plain, title
        elif k in ("appt_msg", "auto_msg") and _REMINDER.search(title):
            new_k, new_title = "appt_msg", _without_own_date(title, e.get("t"))
        if (new_k, new_title) == (k, title):
            continue
        e.setdefault("orig", {"k": k, "title": title})
        e["k"], e["title"] = new_k, new_title
        d = details.get(str(e.get("id")))
        if isinstance(d, dict):
            if billed:
                details[str(e["id"])] = d = {**d, "billed": billed}
            if new_k == CONTACT and not d.get("note"):
                details[str(e["id"])] = {**d, "note": VISIT_NOTE}
        n += 1
    return n
