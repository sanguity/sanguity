"""Records other than lab results typed in on the import page (hand entry, routes_manual.py).

Each typed record is {"kind": ..., <field>: <text>, ...}. read() checks every one, field by field, and says what it
became; build() turns those that read into the shape importers produce (docs/importers.md), so the timeline, the
drawer, Trends, Health and Costs show them as they show imported records. Every value arrives as text, as typed.

  kind       fields (required in capitals)                 becomes
  visit      DATE time title who where dx note            a "visit" event: detail {who, where, dx, note, procs, src}
  stay       ADMIT admit_time DISCHARGE discharge_time     a "stay" event from common.stay(): detail {admit, discharge,
             where how note                                where, how: "Typed in", admission (how: the admission type),
                                                           note (the discharge note)}
  procedure, DATE time TITLE where who text               a "procedure", "imaging" or "study" event with a "result"
  imaging,                                                detail as the demo's studies: the text is its "Findings"
  study
  med        NAME instructions START stop prescriber       a "med" event on the start date: detail {instructions,
             status                                        status, src, end, prescriber}
  rx         DATE DRUG pharmacy supply billed paid owe     an "rx" event (pharmacy fill); with an amount, a Pharmacy
             insurer                                       claim detail and its Costs row, as the claims importers
  problem    NAME status since type                       an entry of `problems`
  immunization DATE VACCINE dose site lot who where       an "immunization" event (#179), with the detail the C-CDA
                                                           and FHIR importers give one (common.immunization)
  claim      DATE PROVIDER-or-TITLE title BILLED paid owe  a "claim" event, its claim detail (with the claims
             status insurer claim category                 importers' `visit` hint) and its Costs row, filed under the
                                                           care category given or worked out by _claims.care_category
  payment    DATE PAYEE AMOUNT group account memo hsa      an entry of outOfPocket.payments; hsa "HSA" or "FSA" tags it
             upload file                                   (oop.py, #55); with a receipt (upload and file, as a
                                                           document's), also a "document" event "Receipt: <payee>"
                                                           opening it, which the payment names by key ("receipt")
  upcoming   DATE time TITLE who where                     an entry of `upcoming`
  vitals     DATE time where bp hr temp temp_unit height   points in the "Vitals" panel's series, named and keyed as
             height_unit spo2 resp weight weight_unit      the C-CDA importer names them (loinc:<code>); a weight goes
             (at least one measurement)                    to clinicWeights in pounds
  home       DATE weight weight_unit steps                 daily points of the watch summary's "weight" and "steps"
             (at least one)                                metrics (watch_store.save_import, as the import's own)
  home_bp    DATE time SYS DIA pulse                       readings taken at home (#224), one each, in the watch
  home_glucose DATE time GLUCOSE glucose_unit meal         summary's lists of readings the phone's are in, marked
  home_temp  DATE time TEMP temp_unit                      "Typed in" where the phone's name their app (READINGS):
  home_spo2  DATE time SPO2                                bp_readings, glucose_readings, temp_readings, spo2_readings.
                                                           Glucose is kept in mg/dL and temperature in °C, as the
                                                           receiver keeps the phone's; a meal note only when chosen
  height     DATE HEIGHT height_unit                       an entry of About this person's height history (#194,
                                                           person.py), not of the import: see HEIGHT below
  workout    DATE time MINUTES ACTIVITY                    a workout in the watch summary's "exercise"
  document   DATE time TITLE doctype where UPLOAD FILE     a "document" (or "letter") event whose "file" detail opens
                                                           the file: a finished upload holding it (routes_uploads.py),
                                                           indexed as a document of the import (profiles.index_documents)

Dates and times are read as typed lab results' are (csv_labs.timestamp: ISO, 03/04/2026 with the order settled by
the other dates typed, 3 Apr 2026, Apr 3, 2026...); a date alone is noon. Amounts take "$1,234.50".

Source keys: "manual:h:" and 16 hex characters of a SHA-256 over the fields that identify the record, normalised
(_hkey), so the same record typed again gets the same key and the import preview's duplicate check finds it. Vitals
are series points (keyed by series), clinic weights and watch points have no key and are found by their content.

Home measurements and workouts belong here as well as in Health: hand entry is the one place to type in anything
the app can import, and saved here they are the import's own, so undoing the import takes them away too.

Readings typed in at home (#224) are the import's own watch data too, so Body and activity draws them on the cards
the phone's are on (health.js), saying "Typed in", and undoing the save takes them away. A reading is found again by
its minute: typed again for the same date and time with other values, the later save's is the one shown (watch_store
merges a later import over an earlier one), which is how a reading typed wrong is corrected; with the same values it
is a duplicate, as is one the phone sent already (the same values within a minute, in the summary's time zone).
HEIGHT: a height typed in goes straight into About this person's history, the person's own list of heights (a
clinic's heights in the records wait there to be added, because an import can be undone; one the person typed is one
they accept). There it is changed or removed (Sharing → About this person); undoing the save leaves it, as undo
leaves every setting.
"""
import hashlib, os, re
from datetime import date, datetime, timedelta, timezone

from .importers import _claims, common, csv_labs

NAME = "manual"
SOURCE = "Typed in"
SHORT, LONG = 300, 4000   # characters a field may hold: a line, a note
DOC_TYPES = ("pdf", "png", "jpg", "jpeg", "gif")   # what the drawer shows inline
CLAIM_CATEGORIES = ("Doctor visits", "Hospital stay (facility)", "Hospital outpatient (facility)", "Hospital physician care",
                    "Lab & pathology", "Imaging & tests", "Procedures", "Chemo, infusions & drugs", "Radiation therapy",
                    "Physical therapy", "Home health", "Equipment & supplies", "Pharmacy", "Other medical")

# vitals: field -> (series name, unit, LOINC code), as ccda.VITALS names them
VITALS = {"sys": ("Blood pressure, systolic", "mmHg", "8480-6"), "dia": ("Blood pressure, diastolic", "mmHg", "8462-4"),
          "hr": ("Heart rate (clinic)", "bpm", "8867-4"), "temp": ("Temperature", "°F", "8310-5"),
          "height": ("Height", "in", "8302-2"), "spo2": ("SpO2 (clinic)", "%", "59408-5"),
          "resp": ("Respiratory rate (clinic)", "/min", "9279-1")}
# plausible ranges, in the series' units: a typo like 1200 for a heart rate is refused, not charted
PLAUSIBLE = {"sys": (50, 260), "dia": (20, 200), "hr": (20, 250), "temp": (86, 113), "height": (10, 100), "spo2": (50, 100),
             "resp": (4, 80), "weight": (2, 900), "steps": (0, 200000), "minutes": (1, 1440),
             "pulse": (20, 250)}   # #224: a home reading's pulse; its glucose and temperature by unit in HOME_RANGE
WATCH_META = {"weight": ("Weight", "lb", "Body"), "steps": ("Steps", "steps", "Activity")}   # watch/receiver.py's
# ---- readings typed in at home (#224): the watch summary's list each kind goes in, and where its app is in a row
READINGS = {"home_bp": "bp_readings", "home_glucose": "glucose_readings", "home_temp": "temp_readings", "home_spo2": "spo2_readings"}
APP_AT = {"bp_readings": 5, "glucose_readings": 3, "temp_readings": 3, "spo2_readings": 3}   # [time, value(s)..., source, app]
VALUES = {"bp_readings": (1, 2), "glucose_readings": (1,), "temp_readings": (1,), "spo2_readings": (1,)}   # what's compared
HOME_RANGE = {("glucose", "mg/dL"): (20, 600), ("glucose", "mmol/L"): (1.1, 33.3),   # what home meters read
              ("temp", "°C"): (30, 45), ("temp", "°F"): (86, 113)}
GLUCOSE_UNITS = {"mg/dl": "mg/dL", "mmol/l": "mmol/L"}
MEALS = {"before": "before a meal", "after": "after a meal", "other": "other"}
MGDL_PER_MMOL = 18.0156   # server/glucose.py's, the app's exact conversion (units.py)

LABEL = {"labs": "Lab result", "visit": "Visit", "stay": "Hospital stay", "procedure": "Procedure", "imaging": "Imaging",
         "study": "Report or study", "med": "Medication", "rx": "Pharmacy fill", "problem": "Problem or diagnosis",
         "immunization": "Immunization",
         "claim": "Insurance claim", "payment": "Out-of-pocket payment", "upcoming": "Upcoming appointment",
         "vitals": "Vitals", "home": "Weight or steps at home", "workout": "Workout", "document": "Document",
         "home_bp": "Blood pressure at home", "home_glucose": "Blood glucose at home", "home_temp": "Temperature at home",
         "home_spo2": "Blood oxygen at home", "height": "Height"}   # #224

_STUDY = ("date", "time", "title", "where", "who", "text")
FIELDS = {   # kind -> its fields; LONG ones may hold a note
    "visit": ("date", "time", "title", "who", "where", "dx", "note"),
    "stay": ("admit", "admit_time", "discharge", "discharge_time", "where", "how", "note"),
    "procedure": _STUDY, "imaging": _STUDY, "study": _STUDY,
    "med": ("name", "instructions", "start", "stop", "prescriber", "status"),
    "rx": ("date", "drug", "pharmacy", "supply", "billed", "paid", "owe", "insurer"),
    "problem": ("name", "status", "since", "type"),
    "immunization": ("date", "vaccine", "dose", "site", "lot", "who", "where"),   # #179
    "claim": ("date", "provider", "title", "billed", "paid", "owe", "status", "insurer", "claim", "category"),
    "payment": ("date", "payee", "amount", "group", "account", "memo", "hsa", "upload", "file"),
    "upcoming": ("date", "time", "title", "who", "where"),
    "vitals": ("date", "time", "where", "bp", "hr", "temp", "temp_unit", "height", "height_unit", "spo2", "resp", "weight", "weight_unit"),
    "home": ("date", "weight", "weight_unit", "steps"),
    "home_bp": ("date", "time", "sys", "dia", "pulse"),   # #224
    "home_glucose": ("date", "time", "glucose", "glucose_unit", "meal"),
    "home_temp": ("date", "time", "temp", "temp_unit"),
    "home_spo2": ("date", "time", "spo2"),
    "height": ("date", "height", "height_unit"),
    "workout": ("date", "time", "minutes", "activity"),
    "document": ("date", "time", "title", "doctype", "where", "upload", "file"),
}
LONG_FIELDS = {"note", "text", "dx", "instructions"}
DATE_FIELDS = {"date", "admit", "discharge", "start", "stop", "since"}
# filled in by the page for every record of a kind, so a record with only these is still empty
DEFAULTS = {"temp_unit", "height_unit", "weight_unit", "doctype", "status", "type", "glucose_unit"}
NEED = {"date": "a date", "admit": "the admission date", "discharge": "the discharge date", "start": "the date it was started",
        "title": "a title", "name": "a name", "drug": "the drug's name", "billed": "the amount billed", "payee": "who was paid",
        "amount": "the amount paid", "minutes": "how many minutes", "activity": "the activity", "upload": "a file",
        "provider": "the provider or a title", "vaccine": "the vaccine's name",
        "sys": "the systolic pressure (the higher number)", "dia": "the diastolic pressure (the lower number)",   # #224
        "glucose": "the glucose reading", "temp": "the temperature", "spo2": "the oxygen saturation (SpO2)", "height": "the height"}
CAPTION = {"admit_time": "admission time", "discharge_time": "discharge time", "bp": "blood pressure", "hr": "heart rate",
           "temp": "temperature", "spo2": "SpO2", "resp": "breathing rate", "owe": "amount you owe", "paid": "amount paid",
           "billed": "amount billed", "amount": "amount", "supply": "days' supply", "stop": "stop date", "since": "date",
           "start": "start date", "sys": "systolic pressure", "dia": "diastolic pressure", "glucose": "glucose reading"}


def _norm(f):
    return " ".join(str(f if f is not None else "").split()).lower()


def _hkey(*fields):
    """"manual:h:" + 16 hex of a SHA-256 over the normalised identifying fields (docs/importers.md, Source keys)."""
    return NAME + ":h:" + hashlib.sha256("\x1f".join(_norm(f) for f in fields).encode("utf-8")).hexdigest()[:16]


def _display(t):
    return t[:10] if t.endswith("T12:00:00") else t[:16].replace("T", " ")


# a summary's own dates are marked, so the page writes those in the person's format and the rest as it was typed (#317)
_MARK = "\x00"


def _d(t):
    """A date in a summary, marked: "YYYY-MM-DD" or "YYYY-MM-DD HH:MM"."""
    return _MARK + t + _MARK


def _dt(t):
    return _d(_display(t))


def _fmt(x):
    return str(int(x)) if float(x).is_integer() else str(x)


def clean(item):
    """(kind, {field: text}, {field: problem}) for one typed record as sent: unknown fields are ignored; values are
    trimmed text, collapsed to one line except in the note fields."""
    kind = str(item.get("kind") or "").strip() if isinstance(item, dict) else ""
    if kind not in FIELDS:
        return kind, {}, {"kind": "isn't a kind of record Sanguity knows" + (f" ({kind[:40]!r})" if kind else "")}
    values, bad = {}, {}
    for f in FIELDS[kind]:
        v = item.get(f)
        if isinstance(v, bool) or not isinstance(v, (str, int, float, type(None))):
            bad[f] = f"has a {CAPTION.get(f, f)} that isn't text"
            v = ""
        v = "" if v is None else str(v).replace(_MARK, "")   # never in typed text: it marks a summary's dates
        v = v.replace("\r\n", "\n").strip() if f in LONG_FIELDS else " ".join(v.split())
        if len(v) > (LONG if f in LONG_FIELDS else SHORT):
            bad[f] = f"has a {CAPTION.get(f, f)} longer than {LONG if f in LONG_FIELDS else SHORT} characters"
            v = v[:LONG if f in LONG_FIELDS else SHORT]
        values[f] = v
    return kind, values, bad


def is_empty(kind, values):
    return kind in FIELDS and not any(v for f, v in values.items() if f not in DEFAULTS)


def dates_of(values):
    """The date fields' text, for csv_labs.date_order over everything typed in one save."""
    return [v for f, v in values.items() if f in DATE_FIELDS and v]


class Reader:
    """Reads one typed record: each helper notes a problem against its field and returns None when it can't read it."""

    def __init__(self, n, kind, values, bad, order, guessed, uploads=None):
        self.n, self.kind, self.v, self.fields, self.notes = n, kind, values, dict(bad), []
        self.order, self.guessed, self.uploads = order, guessed, uploads

    def bad(self, f, why):
        self.fields.setdefault(f, why)
        return None

    def need(self, *fs):
        for f in fs:
            if not self.v.get(f):
                self.bad(f, "needs " + NEED.get(f, "a " + f))
        return all(self.v.get(f) for f in fs)

    def when(self, f, tf=None, required=True):
        """The ISO time of a date field (with its time field), or None."""
        d, tm = self.v.get(f, ""), self.v.get(tf, "") if tf else ""
        if not d:
            if required:
                self.bad(f, "needs " + NEED.get(f, "a date"))
            elif tm:
                self.bad(f, "needs a date for the time")
            return None
        if csv_labs.guessed_date(d, self.guessed):
            return self.bad(f, f"has a date, {d}, that could be day/month or month/day: write it as YYYY-MM-DD")
        t = csv_labs.timestamp(d, tm, self.order)
        if t:
            if not 1900 <= int(t[:4]) <= 2100:
                return self.bad(f, f"has a date that couldn't be read ({d})")
            return t
        if tm and csv_labs.timestamp(d, "", self.order):
            return self.bad(tf, f"has a {CAPTION.get(tf, 'time')} that couldn't be read ({tm})")
        return self.bad(f, f"has a date that couldn't be read ({d})")

    def number(self, f, what=None, unit=None):
        s = self.v.get(f, "")
        if not s:
            return None
        x = csv_labs.number(re.sub(r"\s*" + re.escape(unit) + r"\s*$", "", s, flags=re.I) if unit else s)
        if x is None:
            return self.bad(f, f"has a {what or CAPTION.get(f, f)} that isn't a number ({s})")
        return x

    def money(self, f):
        s = self.v.get(f, "")
        if not s:
            return None
        x = csv_labs.number(s.replace("$", "").replace("USD", "").strip())
        if x is None or x < 0:
            return self.bad(f, f"has an {CAPTION.get(f, f)} that isn't an amount ({s})")
        return round(float(x), 2)

    def plausible(self, f, x, what):
        lo, hi = PLAUSIBLE[f]
        if x is not None and not lo <= x <= hi:
            return self.bad(f, f"has a {what} that doesn't look right ({_fmt(x)}; expected {lo} to {hi})")
        return x


# ---------------------------------------------------------------- one record

def read(n, item, order, guessed, uploads):
    """One typed record read: {"n", "kind", "ok", "problems", "fields", "notes", "summary", "summary_parts", "parts",
    "ident"}. summary is one line of what it became; summary_parts is the same line split at its own dates, the dates
    at the odd places ("2026-05-04" or "2026-05-04 07:30", as stored), for the page to write those in the person's
    format and the rest as typed (#317). parts says what it adds (see build()); ident is what makes it the same as another typed in the same save.
    uploads(upload_id, file) -> (the stored file, None) or (None, why it can't be used)."""
    kind, values, bad = clean(item)
    if kind not in FIELDS:
        return {"n": n, "kind": kind, "ok": False, "problems": [bad["kind"]], "fields": bad, "notes": [], "parts": None}
    r = Reader(n, kind, values, bad, order, guessed, uploads)
    parts, summary, ident = (globals()["_" + kind](r) if not bad else (None, None, None))
    problems = list(dict.fromkeys(r.fields.values()))
    out = {"n": n, "kind": kind, "label": LABEL[kind], "ok": not problems, "problems": problems, "fields": r.fields,
           "notes": r.notes, "parts": parts if not problems else None, "ident": ident}
    if summary and not problems:
        out["summary_parts"] = summary.split(_MARK)
        out["summary"] = "".join(out["summary_parts"])
    return out


def _event(t, k, title, sub, detail=None, key=None, end=None, cost=None, **extra):
    e = {"t": t, "k": k, "title": title, "sub": sub}
    if end:
        e["end"] = end
    e.update({a: b for a, b in extra.items() if b not in (None, "", [], 0)})
    if key:
        e["key"] = key
    return {"event": e, "detail": detail, "cost": cost}


def _join(*xs):
    return " · ".join(x for x in xs if x)


def _visit(r):
    v = r.v
    t = r.when("date", "time")
    if not t:
        return None, None, None
    title = v["title"] or "Visit"
    det = {"type": "visit", "who": v["who"], "where": v["where"], "dx": v["dx"], "note": v["note"], "procs": [], "src": SOURCE}
    key = _hkey("visit", t, title, v["where"], v["who"])
    return {"events": [_event(t, "visit", title, _join(v["who"], v["where"]), det, key)]}, _join(_dt(t), title, v["who"], v["where"]), key


def _stay(r):
    v = r.v
    a, d = r.when("admit", "admit_time"), r.when("discharge", "discharge_time")
    if not (a and d):
        return None, None, None
    exact = (a if v["admit_time"] else a[:10] + "T00:00:00", d if v["discharge_time"] else d[:10] + "T23:59:00")
    if exact[1][:19] < exact[0][:19]:
        r.bad("discharge", "has a discharge before the admission")
        return None, None, None
    s = common.stay(a[:10], d[:10], v["where"] or "Hospital", "", SOURCE, exact=exact)
    det = {**s["detail"], "admit": exact[0], "discharge": exact[1], "where": v["where"]}
    if v["how"]:
        det["admission"] = v["how"]
    if v["note"]:
        det["note"] = v["note"]
    key = _hkey("stay", a[:10], d[:10], v["where"])
    ev = _event(s["t"], "stay", s["title"], _join(s["sub"], v["how"]), det, key, end=s["end"])
    nights = s["sub"].rsplit(" · ", 1)[-1]
    return {"events": [ev]}, _join(f"{_d(a[:10])} to {_d(d[:10])}", nights, v["where"], v["how"]), key


def _study(r):
    v = r.v
    t = r.when("date", "time")
    r.need("title")
    if not (t and v["title"]):
        return None, None, None
    det = {"type": "result", "order": v["title"], "name": v["title"], "collected": _display(t), "resulted": t[:10],
           "provider": v["who"] or None, "status": None, "lab": v["where"] or None, "dx": [], "rows": [],
           "report": [["Findings", v["text"], 0]] if v["text"] else [], "comments": [], "src": SOURCE}
    key = _hkey(r.kind, t, v["title"], v["where"])
    return ({"events": [_event(t, r.kind, v["title"], _join(v["who"], v["where"]), det, key)]},
            _join(_dt(t), v["title"], v["where"]), key)


_procedure = _imaging = _study   # three kinds, one form: "study" is a report


def _med(r):
    v = r.v
    r.need("name")
    t = r.when("start")
    end = r.when("stop", required=False)
    if t and end and end[:10] < t[:10]:
        r.bad("stop", "has a stop date before the start")
    if not (t and v["name"]) or "stop" in r.fields:
        return None, None, None
    status = v["status"] or ("Stopped" if end else "Active")
    det = {"type": "med", "instructions": v["instructions"] or None, "status": status, "src": SOURCE}
    if end:
        det["end"] = end
    if v["prescriber"]:
        det["prescriber"] = v["prescriber"]
    key = _hkey("med", t[:10], v["name"], v["instructions"])
    ev = _event(t, "med", v["name"], _join(v["instructions"], status), det, key)
    return {"events": [ev]}, _join(_d(t[:10]) + (f" to {_d(end[:10])}" if end else ""), v["name"], v["instructions"], status), key


def _rx(r):
    v = r.v
    r.need("drug")
    t = r.when("date")
    supply = r.number("supply", "days' supply")
    billed, paid, owe = r.money("billed"), r.money("paid"), r.money("owe")
    if not (t and v["drug"]) or set(r.fields) & {"supply", "billed", "paid", "owe"}:
        return None, None, None
    sub = _join("Pharmacy fill", v["pharmacy"], f"{_fmt(supply)}-day supply" if supply else "", v["insurer"])
    key = _hkey("rx", t[:10], v["drug"], v["pharmacy"], owe if owe is not None else "")
    det = cost = None
    if any(x is not None for x in (billed, paid, owe)):
        billed, paid, owe = billed or 0.0, paid or 0.0, owe or 0.0
        det = {"type": "claim", "insurer": v["insurer"] or None, "kind": "Pharmacy", "drug": v["drug"],
               "provider": v["pharmacy"] or None, "claimId": None, "billed": billed, "paid": paid, "owe": owe, "status": "",
               "src": SOURCE, "visit": {"date": t[:10], **({"provider": v["pharmacy"]} if v["pharmacy"] else {})}}
        cost = {"d": t[:10], "ins": v["insurer"], "billed": billed, "paid": paid, "owe": owe, "status": "", "kind": "pharmacy",
                "cat": "Pharmacy", "prov": v["pharmacy"], "title": v["drug"]}
    return ({"events": [_event(t, "rx", v["drug"], sub, det, key, cost=cost)]},
            _join(_d(t[:10]), v["drug"], v["pharmacy"], f"you paid ${owe:,.2f}" if cost else ""), key)


def _problem(r):
    v = r.v
    r.need("name")
    since = r.when("since", required=False)
    if not v["name"] or "since" in r.fields:
        return None, None, None
    key = _hkey("problem", v["name"], since[:10] if since else "")
    p = {"name": v["name"], "type": v["type"] or "Diagnosis", "date": since[:10] if since else None,
         "status": v["status"] or "Active", "src": SOURCE, "key": key}
    return {"problems": [p]}, _join(v["name"], p["status"], f"since {_d(p['date'])}" if p["date"] else ""), key


def _immunization(r):   # #179
    v = r.v
    t = r.when("date")
    r.need("vaccine")
    if r.fields or not t:
        return None, None, None
    title, sub, det = common.immunization(v["vaccine"], SOURCE, dose=v["dose"], site=v["site"], lot=v["lot"],
                                          performer=v["who"], where=v["where"])
    key = _hkey("immunization", t[:10], v["vaccine"], v["dose"])
    return ({"events": [_event(t, "immunization", title, sub, det, key)]},
            _join(_d(t[:10]), v["vaccine"], f"dose {v['dose']}" if v["dose"] else "", v["where"]), key)


def _claim(r):
    v = r.v
    t = r.when("date")
    if not (v["provider"] or v["title"]):
        r.bad("provider", "needs " + NEED["provider"])
    r.need("billed")
    billed, paid, owe = r.money("billed"), r.money("paid"), r.money("owe")
    if v["category"] and v["category"] not in CLAIM_CATEGORIES:
        r.bad("category", f"has a type of care Sanguity doesn't know ({v['category']})")
    if r.fields or not t:
        return None, None, None
    paid, owe = paid or 0.0, owe or 0.0
    title = v["title"] or v["provider"]
    pharmacy = v["category"] == "Pharmacy"
    cat = v["category"] or _claims.care_category("medical", title, [], v["provider"], t[:10])
    denied = bool(re.search(r"denied|rejected", v["status"], re.I))
    det = {"type": "claim", "insurer": v["insurer"] or None, "kind": "Pharmacy" if pharmacy else "Medical",
           "provider": v["provider"] or None, "claimId": v["claim"] or None, "billed": billed, "paid": paid, "owe": owe,
           "status": v["status"], "lines": [], "src": SOURCE,
           "visit": {"date": t[:10], **({"provider": v["provider"]} if v["provider"] else {})}}
    cost = {"d": t[:10], "ins": v["insurer"], "billed": billed, "paid": paid, "owe": owe, "status": v["status"],
            "kind": "pharmacy" if pharmacy else "medical", "cat": cat, "prov": v["provider"], "title": title}
    key = _hkey("claim", t[:10], v["provider"], title, billed, v["claim"])
    sub = _join(v["provider"] if v["title"] else "", v["insurer"]) + (" · denied" if denied else "")
    ev = _event(t, "rx" if pharmacy else "claim", title, sub, det, key, cost=cost, abn=1 if denied else None)
    return {"events": [ev]}, _join(_d(t[:10]), title, f"billed ${billed:,.2f}", f"you owe ${owe:,.2f}", cat), key


def _payment(r):
    v = r.v
    t = r.when("date")
    r.need("payee", "amount")
    amount = r.money("amount")
    if v["hsa"] and v["hsa"] not in ("HSA", "FSA"):
        r.bad("hsa", f"has an account type Sanguity doesn't know ({v['hsa']}): HSA, FSA or none")
    stored = _file(r) if v["upload"] else None
    if r.fields or not t or (v["upload"] and not stored):
        return None, None, None
    key = _hkey("payment", t[:10], v["payee"], amount)
    p = {"d": t[:10], "payee": v["payee"], "group": v["group"] or "Medical bills", "amount": amount,
         "account": v["account"], "memo": v["memo"], "est": False, "key": key}
    if v["hsa"]:
        p["hsa"] = v["hsa"]
    parts = {"payments": [p]}
    if stored:   # the receipt: a document of its own, which the payment names
        ext = os.path.splitext(v["file"])[1].lstrip(".").lower()
        det = {"type": "file", "url": f"{int(v['upload'])}/{v['file']}", "ext": ext, "src": SOURCE}
        p["receipt"] = _hkey("receipt", t[:10], v["payee"], amount, v["file"])
        parts.update(events=[_event(t, "document", f"Receipt: {v['payee']}", f"${amount:,.2f} · {SOURCE}", det, p["receipt"])],
                     files={det["url"]: stored})
    return parts, _join(_d(t[:10]), v["payee"], f"${amount:,.2f}", p["group"], v["hsa"], "receipt " + v["file"] if stored else ""), key


def _file(r):
    """The stored file of a record's upload and file fields (a document's, a payment's receipt), or None."""
    v = r.v
    ext = os.path.splitext(v["file"])[1].lstrip(".").lower()
    if not re.fullmatch(r"\d{1,18}", v["upload"]) or not v["file"]:
        return r.bad("upload", "has a file that wasn't uploaded: choose it again")
    if ext not in DOC_TYPES:
        return r.bad("upload", f"has a file Sanguity can't show ({v['file']}): attach a PDF or a picture (PNG, JPEG or GIF)")
    stored, why = r.uploads(int(v["upload"]), v["file"])
    return r.bad("upload", why) if why else stored


def _upcoming(r):
    v = r.v
    t = r.when("date", "time")
    r.need("title")
    if r.fields or not t:
        return None, None, None
    t = t[:10] if t.endswith("T12:00:00") and not v["time"] else t
    key = _hkey("upcoming", t, v["title"])
    u = {"t": t, "title": v["title"], "who": v["who"], "where": v["where"], "dx": "", "procs": [], "key": key}
    return {"upcoming": [u]}, _join(_dt(t if "T" in t else t + "T12:00:00"), v["title"], v["who"], v["where"]), key


def _vitals(r):
    v = r.v
    t = r.when("date", "time")
    got = {}
    if v["bp"]:
        m = re.fullmatch(r"(\d{2,3}(?:\.\d)?)\s*(?:/|over)\s*(\d{2,3}(?:\.\d)?)\s*(?:mm\s*hg)?", v["bp"], re.I)
        if not m:
            r.bad("bp", f"has a blood pressure that couldn't be read ({v['bp']}): write it like 120/80")
        else:
            got["sys"], got["dia"] = float(m.group(1)), float(m.group(2))
            for f, what in (("sys", "systolic pressure"), ("dia", "diastolic pressure")):
                lo, hi = PLAUSIBLE[f]
                if not lo <= got[f] <= hi:
                    r.bad("bp", f"has a {what} that doesn't look right ({_fmt(got[f])}; expected {lo} to {hi})")
            if "bp" not in r.fields and got["dia"] >= got["sys"]:
                r.bad("bp", f"has the lower number first ({v['bp']}): write the systolic first, like 120/80")
    for f, unit in (("hr", "bpm"), ("spo2", "%"), ("resp", "/min")):
        x = r.number(f, unit=unit)
        if x is not None:
            got[f] = r.plausible(f, x, CAPTION[f])
    temp = r.number("temp", unit="°" + (v["temp_unit"] or "F"))
    if temp is not None:
        if (v["temp_unit"] or "F").upper().lstrip("°") == "C":
            temp = round(temp * 9 / 5 + 32, 1)
        got["temp"] = r.plausible("temp", temp, "temperature")
    height = r.number("height", unit=v["height_unit"] or "in")
    if height is not None:
        if (v["height_unit"] or "in").lower() == "cm":
            height = round(height / 2.54, 1)
        got["height"] = r.plausible("height", height, "height")
    weight = r.number("weight", unit=v["weight_unit"] or "lb")
    if weight is not None:
        if (v["weight_unit"] or "lb").lower() == "kg":
            weight = round(weight * 2.20462, 1)
        weight = r.plausible("weight", weight, "weight")
    if not r.fields and not got and weight is None:
        r.bad("bp", "needs at least one measurement")
    if r.fields or not t:
        return None, None, None
    points = []
    for f, x in got.items():   # as reported: the value in the series' unit (a temperature in °C is charted in °F)
        name, unit, loinc = VITALS[f]
        points.append(((name, unit, loinc), [t, x, None, None, "", f"{_fmt(x)} {unit}", SOURCE]))
    parts = {"points": points}
    shown = [f"BP {_fmt(got['sys'])}/{_fmt(got['dia'])}" if "sys" in got else "", f"HR {_fmt(got['hr'])}" if "hr" in got else "",
             f"{_fmt(got['temp'])} °F" if "temp" in got else "", f"height {_fmt(got['height'])} in" if "height" in got else "",
             f"SpO2 {_fmt(got['spo2'])}%" if "spo2" in got else "", f"breathing {_fmt(got['resp'])}/min" if "resp" in got else "",
             f"weight {_fmt(weight)} lb" if weight is not None else ""]
    if weight is not None:
        parts["weights"] = [[t, weight, v["where"]]]
    return parts, _join(_dt(t), *shown), ("vitals", t)


def _home(r):
    v = r.v
    t = r.when("date")
    weight = r.number("weight", unit=v["weight_unit"] or "lb")
    if weight is not None and (v["weight_unit"] or "lb").lower() == "kg":
        weight = round(weight * 2.20462, 1)
    weight = r.plausible("weight", weight, "weight")
    steps = r.plausible("steps", r.number("steps"), "step count")
    if steps is not None and not float(steps).is_integer():
        r.bad("steps", f"has a step count that isn't a whole number ({v['steps']})")
    if not r.fields and weight is None and steps is None:
        r.bad("weight", "needs a weight or a step count")
    if r.fields or not t:
        return None, None, None
    day = t[:10]
    pts = ([("weight", day, weight)] if weight is not None else []) + ([("steps", day, int(steps))] if steps is not None else [])
    return ({"watch": pts}, _join(_d(day), f"weight {_fmt(weight)} lb" if weight is not None else "",
                                  f"{int(steps):,} steps" if steps is not None else ""), ("home", day, weight is not None, steps is not None))


# ---- readings typed in at home (#224): a row each of the watch summary's lists of readings (READINGS)

def _home_when(r):
    """The reading's local time (a date alone is noon), or None; not after today (a typo, not a reading)."""
    t = r.when("date", "time")
    if t and t[:10] > (date.today() + timedelta(days=1)).isoformat():   # a day's slack for a time zone ahead
        return r.bad("date", f"has a date after today ({r.v['date']})")
    return t


def _in_range(r, f, x, what, kind, unit, other):
    """x within HOME_RANGE for the unit typed; one that fits the other unit says so (a unit chosen wrong)."""
    lo, hi = HOME_RANGE[(kind, unit)]
    if x is None or lo <= x <= hi:
        return x
    olo, ohi = HOME_RANGE[(kind, other)]
    if olo <= x <= ohi:
        return r.bad(f, f"has a {what} that looks like {other}, not {unit} ({_fmt(x)}): choose {other}, or check the number")
    return r.bad(f, f"has a {what} that doesn't look right ({_fmt(x)} {unit}; expected {_fmt(lo)} to {_fmt(hi)})")


def _reading(r, t, row, shown):
    """What a reading read becomes: its row in its list (READINGS), at the local time it was typed for."""
    return {"readings": [(READINGS[r.kind], [t, *row])]}, _join(_dt(t), *shown), (r.kind, t[:16])


def _home_bp(r):
    t = _home_when(r)
    r.need("sys", "dia")
    sys_ = r.plausible("sys", r.number("sys", unit="mmHg"), "systolic pressure")
    dia = r.plausible("dia", r.number("dia", unit="mmHg"), "diastolic pressure")
    pulse = r.plausible("pulse", r.number("pulse", "pulse", unit="bpm"), "pulse")
    if sys_ is not None and dia is not None and dia >= sys_:
        r.bad("dia", f"is as high as the systolic or higher ({_fmt(dia)}, {_fmt(sys_)}): the systolic is the higher number, as in 120/80")
    if r.fields or not t:
        return None, None, None
    return _reading(r, t, [sys_, dia, pulse, None, SOURCE],
                    [f"{_fmt(sys_)}/{_fmt(dia)} mmHg", f"pulse {_fmt(pulse)}" if pulse is not None else ""])


def _home_glucose(r):
    v = r.v
    t = _home_when(r)
    r.need("glucose")
    unit = GLUCOSE_UNITS.get((v["glucose_unit"] or "mg/dL").lower().replace(" ", ""))
    if not unit:
        r.bad("glucose_unit", f"has a unit Sanguity doesn't know for glucose ({v['glucose_unit']}): mg/dL or mmol/L")
    meal = v["meal"].lower()
    meal = next((k for k, w in MEALS.items() if meal in (k, w)), None) if meal else ""
    if meal is None:
        r.bad("meal", f"has a note Sanguity doesn't know ({v['meal']}): before a meal, after a meal, other, or none")
    x = r.number("glucose", "glucose reading", unit=unit) if unit else None
    x = _in_range(r, "glucose", x, "glucose reading", "glucose", unit, "mmol/L" if unit == "mg/dL" else "mg/dL") if unit else None
    if r.fields or not t:
        return None, None, None
    mgdl = round(x * MGDL_PER_MMOL, 2) if unit == "mmol/L" else x   # kept in mg/dL, as the phone's are (server/glucose.py)
    return _reading(r, t, [mgdl, None, SOURCE, meal or None], [f"glucose {_fmt(x)} {unit}", MEALS.get(meal, "")])


def _home_temp(r):
    v = r.v
    t = _home_when(r)
    r.need("temp")
    scale = (v["temp_unit"] or "F").upper().lstrip("°")
    if scale not in ("F", "C"):
        r.bad("temp_unit", f"has a unit Sanguity doesn't know for a temperature ({v['temp_unit']}): °F or °C")
    unit = "°" + scale if scale in ("F", "C") else None
    x = r.number("temp", unit=unit) if unit else None
    x = _in_range(r, "temp", x, "temperature", "temp", unit, "°C" if unit == "°F" else "°F") if unit else None
    if r.fields or not t:
        return None, None, None
    c = round((x - 32) * 5 / 9, 2) if scale == "F" else x   # kept in °C, as the receiver keeps the phone's
    return _reading(r, t, [c, None, SOURCE], [f"{_fmt(x)} {unit}"])


def _home_spo2(r):
    t = _home_when(r)
    r.need("spo2")
    x, (lo, hi) = r.number("spo2", "oxygen saturation", unit="%"), PLAUSIBLE["spo2"]
    if x is not None and not lo <= x <= hi:
        r.bad("spo2", f"has an oxygen saturation (SpO2) that doesn't look right ({_fmt(x)}%; expected {lo} to {hi})")
    if r.fields or not t:
        return None, None, None
    return _reading(r, t, [x, None, SOURCE], [f"SpO2 {_fmt(x)}%"])


_FTIN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:ft|feet|foot|')\s*(?:(\d+(?:\.\d+)?)\s*(?:in|inches|inch|\"|'')?)?", re.I)


def _height(r):
    """A height for About this person's history (person.py): {date, cm, unit}, unit "cm" or "ftin" as it was typed.
    That it isn't before the birth date is checked where the history is (routes_manual.py)."""
    from . import person
    v = r.v
    t = r.when("date")
    r.need("height")
    unit, cm = (v["height_unit"] or "cm").lower(), None
    if unit not in ("cm", "in"):
        r.bad("height_unit", f"has a unit Sanguity doesn't know for a height ({v['height_unit']}): cm, or feet and inches")
    elif v["height"]:
        m = _FTIN.fullmatch(v["height"]) if unit == "in" else None
        x = float(m.group(1)) * 12 + float(m.group(2) or 0) if m else r.number("height", unit=unit)
        cm = None if x is None else round(x * person.IN_CM if unit == "in" else x, 2)
    if t and t[:10] > date.today().isoformat():
        r.bad("date", f"has a date after today ({v['date']})")
    entry = None
    if cm is not None:
        try:
            entry = person.check_height({"date": t[:10] if t else "2000-01-01", "cm": cm, "unit": "ftin" if unit == "in" else "cm"})
        except person.PersonError as e:
            why = str(e)[0].lower() + str(e)[1:].rstrip(".")
            r.bad("height", f"has a height that doesn't look right ({v['height']} {'in' if unit == 'in' else 'cm'}): {why}")
    if r.fields or not t or not entry:
        return None, None, None
    shown = _join(_d(t[:10]), "height " + person.text(entry["cm"], entry["unit"]), "into About this person's height history")
    return {"heights": [entry]}, shown, ("height", t[:10])


def _workout(r):
    v = r.v
    t = r.when("date", "time")
    r.need("activity", "minutes")
    minutes = r.plausible("minutes", r.number("minutes", "number of minutes", unit="min"), "number of minutes")
    if r.fields or not t:
        return None, None, None
    from datetime import datetime, timedelta
    start = datetime.fromisoformat(t[:19])
    end = start + timedelta(minutes=float(minutes))
    words = v["activity"].lower().split()
    key = _hkey("workout", t, v["activity"], minutes)
    w = {"key": key, "start": start.isoformat(), "end": end.replace(microsecond=0).isoformat(), "name": v["activity"][:1].upper() + v["activity"][1:],
         "kind": "_".join(re.sub(r"[^\w]", "", x) for x in words) or "other", "minutes": round(float(minutes))}
    return {"workouts": [w]}, _join(_dt(t), w["name"], f"{w['minutes']} min"), key


def _document(r):
    v = r.v
    t = r.when("date", "time")
    r.need("title", "upload")
    if v["doctype"] and v["doctype"] not in ("document", "letter"):
        r.bad("doctype", f"has a kind of document Sanguity doesn't know ({v['doctype']})")
    stored = _file(r) if v["upload"] else None
    if r.fields or not t or not stored:
        return None, None, None
    k = v["doctype"] or "document"
    key = _hkey(k, t, v["title"])
    ext = os.path.splitext(v["file"])[1].lstrip(".").lower()
    det = {"type": "file", "url": f"{int(v['upload'])}/{v['file']}", "ext": ext, "src": SOURCE}
    return ({"events": [_event(t, k, v["title"], v["where"] or SOURCE, det, key)], "files": {det["url"]: stored}},
            _join(_dt(t), v["title"], v["file"]), key)


# ---------------------------------------------------------------- the records together

def build(records, base_id=0):
    """The data.json shape for records read with no problems (their "parts"), events numbered from base_id + 1, and
    where each part came from: ({"data", "watch", "files", "heights"}, {"events": {event id: n}, "points": {(series
    name, point index): n}, "meta": {(path, index): n}, "watch": [(n, part)], "heights": [(n, entry)]}). heights are
    About this person's (#224), kept apart from the import."""
    events, details, costs, series, problems, upcoming, payments, weights = [], {}, [], {}, [], [], [], []
    metrics, workouts, files, readings, heights = {}, [], {}, {}, []
    origin = {"events": {}, "points": {}, "meta": {}, "watch": [], "heights": heights}
    for rec in records:
        p, n = rec["parts"] or {}, rec["n"]
        for x in p.get("events", []):
            eid = base_id + len(events) + 1
            events.append({"id": eid, **x["event"]})
            if x["detail"]:
                details[eid] = x["detail"]
            if x["cost"]:
                costs.append({"id": eid, **x["cost"]})
            origin["events"][str(eid)] = n
        for (name, unit, loinc), pt in p.get("points", []):
            s = series.setdefault(name, {"name": name, "key": f"loinc:{loinc}", "unit": unit, "panel": "Vitals", "loinc": loinc, "pts": [], "txt": []})
            s["pts"].append(pt)
        for path, have, xs in (("problems", problems, p.get("problems", [])), ("upcoming", upcoming, p.get("upcoming", [])),
                               ("outOfPocket.payments", payments, p.get("payments", [])), ("clinicWeights", weights, p.get("weights", []))):
            for x in xs:
                origin["meta"][(path, len(have))] = n
                have.append(x)
        for metric, day, x in p.get("watch", []):
            m = metrics.setdefault(metric, {"key": metric, "name": WATCH_META[metric][0], "unit": WATCH_META[metric][1],
                                            "group": WATCH_META[metric][2], "pts": []})
            m["pts"].append([day, x])
            origin["watch"].append((n, ("m", metric, day, x)))
        for w in p.get("workouts", []):
            workouts.append(w)
            origin["watch"].append((n, ("w", w["key"], w["start"])))
        for lst, row in p.get("readings", []):   # #224
            readings.setdefault(lst, []).append(row)
            origin["watch"].append((n, ("r", lst, row[0], tuple(row[i] for i in VALUES[lst]))))
        heights += [(n, h) for h in p.get("heights", [])]
        files.update(p.get("files", {}))
    labs = []
    for s in series.values():
        s["pts"].sort(key=lambda q: q[0])
        s["n"] = len(s["pts"])
        labs.append(s)
    for s in labs:   # where each point sits once sorted
        for i, pt in enumerate(s["pts"]):
            for rec in records:
                if any(pt is q for _, q in (rec["parts"] or {}).get("points", [])):
                    origin["points"][(s["name"], i)] = rec["n"]
    data = {"events": events, "details": details, "labs": labs, "costs": costs}
    if problems:
        data["problems"] = problems
    if upcoming:
        data["upcoming"] = upcoming
    if payments:
        data["outOfPocket"] = {"payments": payments}
    if weights:
        data["clinicWeights"] = weights
    for m in metrics.values():
        m["pts"].sort()
    watch = None
    if metrics or workouts or readings:
        watch = {"metrics": list(metrics.values()), "exercise": sorted(workouts, key=lambda w: w["start"])}
        watch.update({lst: sorted(rows, key=lambda q: q[0]) for lst, rows in readings.items()})   # typed in at home (#224)
    return {"data": data, "watch": watch, "files": files, "heights": [h for _, h in heights]}, origin


def watch_duplicates(have, origin):
    """The records whose watch data the profile has already (have: watch_store.load): a daily point with the same
    value on that day, a workout with the same key or start."""
    if not have:
        return set()
    pts = {(m.get("key"), str(p[0])): p[1] for m in have.get("metrics") or [] if isinstance(m, dict)
           for p in m.get("pts") or [] if isinstance(p, list) and len(p) > 1}
    wk = {w.get("key") for w in have.get("exercise") or [] if isinstance(w, dict)} - {None}
    ws = {str(w.get("start"))[:16] for w in have.get("exercise") or [] if isinstance(w, dict)}
    rd = _readings_by_minute(have)
    seen, dup = {}, {}
    for n, part in origin:
        if part[0] == "m":
            is_dup = (part[1], part[2]) in pts and pts[(part[1], part[2])] == part[3]
        elif part[0] == "r":   # #224: the same values typed before for that minute, or sent by the phone within a minute of it
            is_dup = _same_reading(rd, part) is not None
        else:
            is_dup = part[1] in wk or part[2][:16] in ws
        seen[n] = True
        dup[n] = dup.get(n, True) and is_dup
    return {n for n, d in dup.items() if d}


# ---- readings typed in at home (#224): found again by their minute, in the summary's own time zone

def _round(vs):
    return tuple(None if x is None else round(float(x), 1) for x in vs)


def _readings_by_minute(have):
    """{(list, local minute "YYYY-MM-DDTHH:MM"): [(values, typed?)]} of the summary's readings. A phone's are instants
    (UTC), put in the summary's time zone (the receiver's "tz"; UTC without one); typed ones are local times already."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        tz = ZoneInfo(str(have.get("tz") or "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        tz = timezone.utc
    out = {}
    for lst, at in APP_AT.items():
        for row in have.get(lst) or []:
            if not isinstance(row, list) or len(row) <= at:
                continue
            typed = row[at] == SOURCE
            try:
                t = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
            except ValueError:
                continue
            wall = (t.astimezone(tz).replace(tzinfo=None) if t.tzinfo else t).isoformat()[:16]
            out.setdefault((lst, wall), []).append((_round(row[i] for i in VALUES[lst]), typed))
    return out


def _same_reading(rd, part):
    """("typed" or "phone", the values) of the reading the summary has already for a typed one, else None."""
    _, lst, t, vs = part
    vs, t0 = _round(vs), datetime.fromisoformat(t[:16])
    for d in (0, -1, 1):
        for have, typed in rd.get((lst, (t0 + timedelta(minutes=d)).isoformat()[:16]), ()):
            if have == vs and (typed is (d == 0) or not typed):
                return "typed" if typed else "phone"
    return None


def watch_replaces(have, origin):
    """{record number: note} for readings typed again for a minute with other values: the later is the one shown."""
    if not have:
        return {}
    rd, out = _readings_by_minute(have), {}
    for n, part in origin:
        if part[0] != "r" or _same_reading(rd, part):
            continue
        if any(typed for _, typed in rd.get((part[1], part[2][:16]), ())):
            out[n] = "replaces the reading typed in before for that time (undoing this save brings it back)"
    return out
