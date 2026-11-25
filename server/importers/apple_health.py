"""Apple Health importer: the export.xml in an iPhone's "Export All Health Data" zip, as a daily watch summary.

Wearable data doesn't belong in data.json's events: the Health tab reads the watch summary that watch/receiver.py
writes for Health Connect (daily.json). So parse() returns {"events": [], "watch": <that shape>}, with the same metric
keys, units (masses in pounds) and per-day aggregates, and the workouts as `exercise`.

Where the summary goes (proposal, to settle once #17 and #18 land): the import step writes data["watch"] sealed to
the profile's own folder, <profile_dir>/watch/daily.json (the path a profile restore already uses, server/export.py),
and points profiles.watch_file at it when the profile has none. When the profile already reads a receiver's file,
the import is kept beside it as <profile_dir>/watch/apple-health.json and /api/profiles/{pid}/watch merges the two
per (metric key, day), the receiver winning, and exercise de-duplicated on start time. A later export that overlaps
an earlier one replaces by identity, not by appending: each daily point by (metric key, day), each workout by its
`key` ("apple-health:workout:" + the first 16 hex of a SHA-256 over its UTC start, UTC end and HKWorkoutActivityType,
so the same workout gets the same key in every export).

The file can be gigabytes: it is streamed with iterparse, each top-level element cleared once read, and only per-day
totals (plus the sleep segments, a few per night) are kept. Days are the records' own local dates: every date in
the export carries its UTC offset ("2024-03-09 23:41:07 -0500"), so a trip abroad lands on the days as lived.
Entity declarations are refused before the parser sees them (Apple's own DOCTYPE only declares elements and
attributes), and so is any encoding but UTF-8.

Health Records (#165): when the Health app holds records from hospitals and clinics (Apple's Health Records), the
export has them beside export.xml as FHIR JSON, one resource per file, under clinical-records/ (some still DSTU2).
They're read by the FHIR importer (fhir.py) and come in with the same import: visits, labs, medications, problems,
reports and the rest, with the same source keys as when that folder is uploaded on its own, so neither duplicates
the other. If they can't be read, the rest still comes in, with a warning.

ECGs (#182, server/ecg.py): the recordings in electrocardiograms/ beside export.xml (one CSV file each; the name and
birth date at their top are never kept) and export.xml's irregular rhythm notifications go into the summary's "ecg"
list, each recording's samples into "ecg_waves" ({key: waveform}), which watch_store.save_import keeps as files of
their own, apart from the summary.

The phone apps' newer measures (#223), read as watch/receiver.py reads Health Auto Export's, so an uploaded export
and the app give the same days, and a reading in both counts once (the same keys and rules; watch_store merges them):
  - Home blood pressure: each HKCorrelationTypeIdentifierBloodPressure, its systolic and diastolic records together,
    with the pulse when a heart rate record is in the correlation (from an app that wrote the two records without a
    correlation: the pair with the same source and time). Body temperature (HKQuantityTypeIdentifierBodyTemperature,
    °C or °F). Both kept reading by reading, keyed by source and time, and summed up by day as the receiver does
    (once() and home_summary(), which the receiver uses too): the bp_* and body_temp* metrics and the summary's
    "bp_readings" and "temp_readings" lists.
  - Blood glucose (HKQuantityTypeIdentifierBloodGlucose, mg/dL or mmol/L by the app's exact conversion, #41): kept as
    server/glucose.py keeps Health Auto Export's readings, which then decides meter or CGM by how often each source
    sends and makes the same glucose_* metrics.
  - Height (HKQuantityTypeIdentifierHeight): the latest of each day, in the summary's "heights" list ([day, cm,
    source, unit typed]), never a metric. About this person shows them beside the height history, marked as from the
    phone, each with "Add to the history" like a clinic's (#194, routes_person.py); none is added by itself.
  - Menstrual cycle records (#183, server/cycle.py), only when parse is asked to (cycle=True, which jobs.py passes
    only for a profile with cycle tracking on): flow, ovulation tests, cervical mucus and spotting, read as Health
    Auto Export's (cycle.from_hae), in the summary's "cycle" list, which watch_store.save_import takes out and keeps
    apart (cycle_tracking.py), never in daily.json. Otherwise they are skipped as they stream past: not kept, not
    even in the preview. Sexual activity and basal body temperature are never read. The uploaded export.xml itself is
    written again without what isn't kept (without(), #555: cycle_tracking.strip_uploads), the rest byte for byte.

Standard library only.
"""
import hashlib
import re
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from . import fhir
from .. import cycle as cycles, ecg, glucose   # cycle records (#183), ECGs (#182), blood glucose (#181)

NAME = "apple-health"
LABEL = "Apple Health (export.xml)"
READS_CYCLE = True   # parse(files, cycle=True) reads cycle records too (jobs.py, only while tracking is on; #223)

HEAD_BYTES = 256 * 1024   # Apple's DTD is ~10 KB; the <HealthData root follows it
LB = 2.20462              # as watch/receiver.py

# HealthKit quantity type -> (our key, kind); kind decides the unit conversion and how a day is aggregated
QUANTITY = {
    "HKQuantityTypeIdentifierStepCount": ("steps", "count"),
    "HKQuantityTypeIdentifierDistanceWalkingRunning": ("distance_km", "distance"),
    "HKQuantityTypeIdentifierActiveEnergyBurned": ("active_cal", "energy"),
    "HKQuantityTypeIdentifierHeartRate": ("hr", "rate"),
    "HKQuantityTypeIdentifierRestingHeartRate": ("hr_rest", "rate"),
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": ("hrv", "ms"),
    "HKQuantityTypeIdentifierOxygenSaturation": ("spo2", "percent"),
    "HKQuantityTypeIdentifierRespiratoryRate": ("resp", "rate"),
    "HKQuantityTypeIdentifierBodyMass": ("weight", "mass"),
    "HKQuantityTypeIdentifierBodyFatPercentage": ("body_fat", "percent"),
    "HKQuantityTypeIdentifierLeanBodyMass": ("lean_mass", "mass"),
    "HKQuantityTypeIdentifierVO2Max": ("vo2max", "vo2"),   # cardio fitness, the watch's estimate (#226)
}
CUMULATIVE = {"steps", "distance_km", "active_cal"}   # summed over the day; the rest are averaged
SLEEP = "HKCategoryTypeIdentifierSleepAnalysis"
STAGE = {   # HKCategoryValueSleepAnalysis* -> receiver stage; InBed overlaps the others and isn't sleep
    "HKCategoryValueSleepAnalysisAsleep": "sleeping",               # before iOS 16
    "HKCategoryValueSleepAnalysisAsleepUnspecified": "sleeping",
    "HKCategoryValueSleepAnalysisAsleepCore": "light",
    "HKCategoryValueSleepAnalysisAsleepDeep": "deep",
    "HKCategoryValueSleepAnalysisAsleepREM": "rem",
    "HKCategoryValueSleepAnalysisAwake": "awake",
    "HKCategoryValueSleepAnalysisInBed": None,
}
ASLEEP = {"sleeping", "light", "deep", "rem"}
SESSION_GAP = timedelta(hours=2)   # segments closer than this are one night (a nap is a session of its own)

UNITS = {   # kind -> {HealthKit unit: factor to the receiver's unit}
    "count": {"count": 1},
    "rate": {"count/min": 1},
    "ms": {"ms": 1},
    "percent": {"%": 100},   # HealthKit stores a fraction (0.97) under "%"
    "mass": {"lb": 1, "kg": LB, "g": LB / 1000, "st": 14, "oz": 1 / 16},
    "distance": {"km": 1, "m": 0.001, "mi": 1.609344, "ft": 0.0003048, "yd": 0.0009144},
    "energy": {"kcal": 1, "Cal": 1, "cal": 0.001, "kJ": 1 / 4.184, "J": 1 / 4184},
    "vo2": {"mL/min·kg": 1, "mL/(kg·min)": 1, "ml/(kg·min)": 1, "mL/kg/min": 1},   # HealthKit writes mL/min·kg (#226)
}

META = {   # watch/receiver.py's, in its order, for the keys Apple Health has
    "steps": ("Steps", "steps", "Activity"), "distance_km": ("Distance", "km", "Activity"),
    "active_cal": ("Active calories", "kcal", "Activity"),
    "hr_rest": ("Resting heart rate", "bpm", "Heart"), "hr_avg": ("Heart rate, daily average", "bpm", "Heart"),
    "hr_min": ("Heart rate, daily low", "bpm", "Heart"), "hr_max": ("Heart rate, daily high", "bpm", "Heart"),
    "hrv": ("HRV (SDNN)", "ms", "Heart"),   # Apple measures SDNN; Health Connect's is RMSSD
    "spo2_avg": ("SpO2, daily average", "%", "Oxygen & breathing"), "spo2_min": ("SpO2, daily low", "%", "Oxygen & breathing"),
    "resp": ("Respiratory rate", "breaths/min", "Oxygen & breathing"),
    "vo2max": ("Cardio fitness (VO2 max)", "mL/kg/min", "Heart"),   # (#226)
    "sleep_h": ("Sleep", "hours", "Sleep"), "sleep_deep_min": ("Deep sleep", "min", "Sleep"),
    "sleep_rem_min": ("REM sleep", "min", "Sleep"), "sleep_light_min": ("Light sleep", "min", "Sleep"),
    "sleep_awake_min": ("Awake during sleep", "min", "Sleep"),
    "weight": ("Weight", "lb", "Body"), "body_fat": ("Body fat", "%", "Body"), "lean_mass": ("Lean mass", "lb", "Body"),
}
# ---- the phone apps' newer measures (#223): read reading by reading, summed up as the receiver does
BP, BP_SYS, BP_DIA = ("HKCorrelationTypeIdentifierBloodPressure", "HKQuantityTypeIdentifierBloodPressureSystolic",
                      "HKQuantityTypeIdentifierBloodPressureDiastolic")
HR = "HKQuantityTypeIdentifierHeartRate"
GLUCOSE, TEMP, HEIGHT = ("HKQuantityTypeIdentifierBloodGlucose", "HKQuantityTypeIdentifierBodyTemperature",
                         "HKQuantityTypeIdentifierHeight")
CYCLE = ("HKCategoryTypeIdentifierMenstrualFlow", "HKCategoryTypeIdentifierOvulationTestResult",
         "HKCategoryTypeIdentifierCervicalMucusQuality", "HKCategoryTypeIdentifierIntermenstrualBleeding")   # cycle.py's
TEMP_SCALE = {"degc": "C", "°c": "C", "c": "C", "cel": "C", "degf": "F", "°f": "F", "f": "F", "[degf]": "F"}   # spellings
HEIGHT_CM = {"cm": 1, "m": 100, "mm": 0.1, "in": 2.54, "ft": 30.48}
VITALS_META = {   # the daily summary of home blood pressure and body temperature (#180, #184), after the other metrics
    "bp_sys": ("Blood pressure, systolic, daily average", "mmHg", "Blood pressure"),
    "bp_dia": ("Blood pressure, diastolic, daily average", "mmHg", "Blood pressure"),
    "bp_sys_max": ("Blood pressure, systolic, daily highest", "mmHg", "Blood pressure"),
    "bp_dia_max": ("Blood pressure, diastolic, daily highest", "mmHg", "Blood pressure"),
    "bp_n": ("Blood pressure readings", "readings", "Blood pressure"),
    "body_temp": ("Body temperature, daily average", "°C", "Body temperature"),
    "body_temp_max": ("Body temperature, daily highest", "°C", "Body temperature"),
}
WORKOUT_PREFIX = "HKWorkoutActivityType"
DURATION = {"min": 60, "s": 1, "sec": 1, "hr": 3600, "h": 3600}


# ------------------------------------------------------------------ files and safety
def _export_file(files):
    """The export.xml among the upload's files (not export_cda.xml, the clinical document beside it)."""
    named = sorted((p for p in files if p.rsplit("/", 1)[-1].lower() == "export.xml"), key=lambda p: (p.count("/"), p))
    for p in named:
        if "apple_health_export/" in p.lower() + "/":
            return files[p]
    if named:
        return files[named[0]]
    for p in sorted(files):
        if p.lower().endswith(".xml") and b"<HealthData" in _head(files[p]):
            return files[p]
    return None


def _head(path, n=HEAD_BYTES):
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except OSError:
        return b""


def _check_encoding(head):
    if head[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in head[:4]:
        raise ValueError("This export.xml isn't UTF-8 text, so it isn't an Apple Health export as the iPhone writes it.")
    m = re.match(rb"<\?xml[^>]*?encoding\s*=\s*[\"']([A-Za-z0-9._-]+)", head)
    if m and m.group(1).lower().replace(b"-", b"").replace(b"_", b"") not in (b"utf8", b"usascii", b"ascii"):
        raise ValueError("This export.xml declares a text encoding other than UTF-8, which Apple Health never writes.")


class _Guarded:
    """A file reader that refuses entity declarations before the XML parser sees them. A literal "<!ENTITY" can only
    stand in a DTD, a comment or CDATA (in text and attribute values "<" must be escaped), and Apple writes none."""
    BAD = b"<!ENTITY"

    def __init__(self, f):
        self.f, self.tail = f, b""

    def read(self, n=-1):
        chunk = self.f.read(n)
        if self.BAD in self.tail + chunk:
            raise ValueError("This export.xml declares XML entities, which Apple Health exports never do; "
                             "it was refused rather than expanded.")
        self.tail = chunk[-(len(self.BAD) - 1):] if chunk else b""
        return chunk


def without(pieces, write, types):
    """An export.xml written again without its <Record>s of these types (HealthKit identifiers, lower-cased; cycle.py's
    never-kept and cycle records, #555), each with what it holds and the line it stands on; everything else byte for
    byte as it was. pieces: the file's bytes, a piece at a time; write(bytes). Streamed, so an export of gigabytes needs
    no more memory than a piece and a record. Returns how many records were left out (0: nothing changed). An entity
    declaration, or XML that isn't well-formed, is refused (ValueError) before the end: what was written then is no
    copy to keep."""
    from xml.parsers import expat
    p = expat.ParserCreate()
    p.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    buf, at = bytearray(), {"base": 0, "out": 0, "final": 0, "depth": 0, "gap": None, "cut": None, "opened": False, "n": 0}

    def bytes_at(a, b):
        return bytes(buf[a - at["base"]:b - at["base"]])

    def start(name, attrs):
        i = p.CurrentByteIndex
        at["depth"] += 1
        if at["cut"] is None and at["depth"] == 2 and name == "Record" and str(attrs.get("type") or "").lower() in types:
            begin = at["gap"] if at["gap"] is not None else i   # with the whitespace before it: its line
            if begin > at["out"]:
                write(bytes_at(at["out"], begin))
            at["cut"], at["out"] = at["depth"], begin
        elif at["cut"] is None:
            at["final"] = i
        at["gap"], at["opened"] = None, True

    def end(name):
        i, opened = p.CurrentByteIndex, at["opened"]
        at["opened"] = False
        if at["cut"] == at["depth"]:   # its end: right after "/>", or its end tag's ">"
            stop = i if opened and bytes_at(i - 2, i) == b"/>" else buf.index(b">", i - at["base"]) + 1 + at["base"]
            at["cut"], at["out"], at["final"] = None, stop, stop
            at["n"] += 1
        elif at["cut"] is None:
            at["final"] = i
        at["depth"] -= 1
        at["gap"] = None

    def text(data):
        at["opened"] = False
        if at["cut"] is not None:
            return
        i = p.CurrentByteIndex
        if at["depth"] == 1 and not data.strip():   # between the root's records: kept with the next one, or not
            if at["gap"] is None:
                at["gap"] = i
        else:
            at["gap"] = None
            at["final"] = i

    def other(*_):   # a comment or a processing instruction: kept where it is, never with a record left out
        at["opened"] = False
        if at["cut"] is None:
            at["gap"], at["final"] = None, p.CurrentByteIndex

    def refused(*_):
        raise ValueError("This export.xml declares XML entities, which Apple Health exports never do.")
    p.StartElementHandler, p.EndElementHandler, p.CharacterDataHandler = start, end, text
    p.CommentHandler = p.ProcessingInstructionHandler = p.StartCdataSectionHandler = other
    p.EntityDeclHandler = refused
    tail = b""
    try:
        for piece in pieces:
            if _Guarded.BAD in tail + piece:
                refused()
            tail = piece[-(len(_Guarded.BAD) - 1):]
            buf += piece
            p.Parse(piece, False)
            keep = max(at["out"], at["final"] if at["cut"] is None else at["out"])
            if at["cut"] is None and at["gap"] is not None:
                keep = max(at["out"], min(keep, at["gap"]))
            if keep > at["out"]:
                write(bytes_at(at["out"], keep))
                at["out"] = keep
            del buf[:at["out"] - at["base"]]
            at["base"] = at["out"]
        p.Parse(b"", True)
    except expat.ExpatError as e:
        raise ValueError(f"export.xml isn't well-formed XML ({e}).") from None
    write(bytes(buf[at["out"] - at["base"]:]))
    return at["n"]


# ------------------------------------------------------------------ dates
_ZONES = {}


def _zone(off):
    z = _ZONES.get(off)
    if z is None:
        sign = -1 if off[0] == "-" else 1
        z = _ZONES[off] = timezone(sign * timedelta(hours=int(off[1:3]), minutes=int(off[3:5])))
    return z


def _when(s):
    """"2024-03-09 23:41:07 -0500" -> an aware datetime in that offset (its local date is the record's day)."""
    try:
        if len(s) == 25 and s[19] == " ":
            return datetime.fromisoformat(s[:19]).replace(tzinfo=_zone(s[20:]))
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S %z")
    except (TypeError, ValueError, IndexError):
        return None


def _split_days(start, end):
    """[(day, fraction of the interval)] for an interval, by local midnights in the start's offset."""
    end = end.astimezone(start.tzinfo)
    total = (end - start).total_seconds()
    if total <= 0:
        return [(start.date().isoformat(), 1.0)]
    out, t = [], start
    while t < end:
        midnight = datetime.combine(t.date() + timedelta(days=1), datetime.min.time(), start.tzinfo)
        stop = min(midnight, end)
        out.append((t.date().isoformat(), (stop - t).total_seconds() / total))
        t = stop
    return out


# ------------------------------------------------------------------ detect
def detect(files):
    path = _export_file(files)
    if not path:
        return 0.0
    head = _head(path)
    if b"<HealthData" not in head:
        return 0.0
    if b"HKQuantityTypeIdentifier" in head or b"HKCategoryTypeIdentifier" in head or b"<ExportDate" in head:
        return 1.0
    return 0.9


# ------------------------------------------------------------------ parse
def parse(files, cycle=False):
    """cycle: also read menstrual cycle records (#183, #223); only for a profile that has cycle tracking on."""
    path = _export_file(files)
    if not path:
        raise ValueError("No export.xml found. In the Health app, tap your picture, then Export All Health Data, "
                         "and upload the zip it makes (or the export.xml inside it).")
    head = _head(path, 4096)
    _check_encoding(head)
    agg = _Aggregates(cycle)
    try:
        with open(path, "rb") as f:
            _stream(_Guarded(f), agg)
    except ET.ParseError as e:
        raise ValueError(f"export.xml isn't well-formed XML ({e}). The export may be cut short; "
                         "try exporting again from the Health app.") from None
    if not agg.root_seen:
        raise ValueError("This file isn't an Apple Health export: its root element isn't <HealthData>.")
    watch = agg.summary()
    warnings = agg.warnings()
    # ---- ECGs (#182): the electrocardiograms folder's recordings, and export.xml's irregular rhythm notifications
    ecgs, waves, ecg_warnings = ecg.from_export(files)
    if ecgs or agg.rhythm:
        watch["ecg"] = ecg.merge(list(agg.rhythm.values()), ecgs)
    if waves:
        watch["ecg_waves"] = waves
    warnings += ecg_warnings
    data = {"events": [], "details": {}, "labs": [], "costs": []}
    records = health_records(files)
    if records:
        try:
            clinical = fhir.parse(records)
        except ValueError as e:
            warnings.append(f"The Health Records in this export couldn't be read, so only the Health app's own data "
                            f"came in: {e}")
        else:
            data = clinical["data"]
            warnings += ["Health Records: " + w for w in clinical["warnings"]]
    if not watch["metrics"] and not watch["exercise"] and not watch.get("ecg") and not watch.get("heights") \
            and not watch.get("cycle") and not data["events"] and not data["labs"]:
        warnings.append("The export holds none of the data the Health tab shows (steps, heart rate, sleep, "
                        "weight and body composition, workouts, ECGs)" + (", and no Health Records." if records else "."))
    return {"data": {**data, "watch": watch}, "warnings": warnings}


RECORDS_DIR = "clinical-records"


def health_records(files):
    """The Health app's Health Records in the upload: the files in a clinical-records folder, with their paths as
    uploaded (so a document a record points to is found beside it)."""
    return {p: a for p, a in files.items() if RECORDS_DIR in p.lower().split("/")[:-1]}


def _stream(f, agg):
    depth, root = 0, None
    for ev, el in ET.iterparse(f, events=("start", "end")):
        if ev == "start":
            depth += 1
            if depth == 1:
                if el.tag != "HealthData":
                    return
                root, agg.root_seen = el, True
            continue
        depth -= 1
        if depth == 1:   # a child of <HealthData>, complete with its own children
            tag = el.tag
            if tag == "Record":
                agg.record(el)
            elif tag == "Workout":
                agg.workout(el)
            elif tag == "Correlation":   # home blood pressure (#223)
                agg.correlation(el)
            elif tag == "ExportDate":
                agg.exported = _when(el.get("value"))
            root.clear()   # drops this element and everything under it


def _tz_name(el):
    for m in el.iter("MetadataEntry"):
        if m.get("key") == "HKTimeZone" and m.get("value"):
            return m.get("value")
    return None


class _Aggregates:
    def __init__(self, cycle=False):
        self.root_seen = False
        self.exported = None
        self.cum = {}       # (key, day, source) -> total   (sources overlap: phone and watch both count steps)
        self.avg = {}       # (key, day) -> [sum, n, min, max]
        self.sleep = []     # (source, start, end, stage)
        self.workouts = {}  # (start, end, type) -> entry
        self.tz, self.tz_at = None, None
        self.bad_units = {}
        self.bad_dates = 0
        self.rhythm = {}    # key -> irregular rhythm notification (#182)
        # the phone apps' newer measures (#223)
        self.bp = {}        # (source, epoch) -> (time, systolic, diastolic, pulse): the correlations
        self.bp_part = {}   # (source, epoch) -> {type: (time, value)}: systolic and diastolic records outside one
        self.temp = {}      # (source, epoch) -> (time, °C)
        self.gl = None      # an in-memory database with glucose.TABLE, from the first reading
        self.gl_skipped = 0
        self.heights = {}   # day -> (time, cm, source, unit typed)
        self.cycle = {} if cycle else None   # HealthKit type -> rows as Health Auto Export sends them; None: not read

    def _note_tz(self, el, at):
        name = _tz_name(el) if len(el) else None
        if name and (self.tz_at is None or at >= self.tz_at):
            self.tz, self.tz_at = name, at

    def record(self, el):
        typ = el.get("type")
        if typ == SLEEP:
            return self._sleep(el)
        if typ == ecg.IRREGULAR:   # (#182)
            if (ev := ecg.from_record(el, _when)) is not None:
                self.rhythm[ev["key"]] = ev
            return
        q = QUANTITY.get(typ)
        if not q:
            if typ in MORE:   # (#223)
                MORE[typ](self, el)
            return
        key, kind = q
        start, end = _when(el.get("startDate")), _when(el.get("endDate"))
        if start is None or end is None:
            self.bad_dates += 1
            return
        unit = el.get("unit")
        f = UNITS[kind].get(unit)
        try:
            v = float(el.get("value"))
        except (TypeError, ValueError):
            return
        if f is None:
            self.bad_units[(typ, unit)] = self.bad_units.get((typ, unit), 0) + 1
            return
        v *= f
        if key in CUMULATIVE:
            src = el.get("sourceName") or ""
            for day, part in _split_days(start, end):
                k = (key, day, src)
                self.cum[k] = self.cum.get(k, 0.0) + v * part
            return
        day = start.date().isoformat()
        a = self.avg.get((key, day))
        if a is None:
            self.avg[(key, day)] = [v, 1, v, v]
        else:
            a[0] += v; a[1] += 1
            if v < a[2]: a[2] = v
            if v > a[3]: a[3] = v

    def _sleep(self, el):
        stage = STAGE.get(el.get("value"), None)
        start, end = _when(el.get("startDate")), _when(el.get("endDate"))
        if start is None or end is None:
            self.bad_dates += 1
            return
        self._note_tz(el, end)
        if stage is not None and end > start:
            self.sleep.append((el.get("sourceName") or "", start, end, stage))

    def workout(self, el):
        start, end = _when(el.get("startDate")), _when(el.get("endDate"))
        if start is None or end is None:
            self.bad_dates += 1
            return
        self._note_tz(el, end)
        hk_type = el.get("workoutActivityType") or ""
        typ = hk_type.removeprefix(WORKOUT_PREFIX) or "Other"
        try:
            sec = float(el.get("duration")) * DURATION.get(el.get("durationUnit") or "min", 60)
        except (TypeError, ValueError):
            sec = (end - start).total_seconds()
        words = "".join(" " + c.lower() if c.isupper() and i else c.lower() for i, c in enumerate(typ)).split()
        k = (start.astimezone(timezone.utc), end.astimezone(timezone.utc), typ)
        entry = self.workouts.setdefault(k, {
            "key": workout_key(k[0], k[1], hk_type), "start": k[0].isoformat(), "end": k[1].isoformat(),
            "name": " ".join(words).capitalize(), "kind": "_".join(words), "minutes": round(sec / 60)})
        hr = _workout_hr(el)
        if hr and "hr" not in entry:
            entry["hr"] = hr

    # -------------------------------------------------------------- the phone apps' newer measures (#223)
    def correlation(self, el):
        if el.get("type") != BP:
            return
        got = {}
        for r in el.iter("Record"):
            if r.get("type") in (BP_SYS, BP_DIA, HR) and r.get("type") not in got:
                got[r.get("type")] = _number(r.get("value"))
        t = _when(el.get("startDate"))
        if t is None:
            self.bad_dates += 1
        elif got.get(BP_SYS) is not None and got.get(BP_DIA) is not None:
            self.bp[(el.get("sourceName") or "", int(t.timestamp()))] = (t, got[BP_SYS], got[BP_DIA], got.get(HR))

    def _bp_part(self, el):   # the same records again at the top level (or there alone, from some apps)
        t, v = _when(el.get("startDate")), _number(el.get("value"))
        if t is not None and v is not None:
            self.bp_part.setdefault((el.get("sourceName") or "", int(t.timestamp())), {})[el.get("type")] = (t, v)

    def _temp(self, el):
        t, v, unit = _when(el.get("startDate")), _number(el.get("value")), el.get("unit")
        scale = TEMP_SCALE.get(str(unit or "").strip().lower())
        if t is None:
            self.bad_dates += 1
        elif v is not None and not scale:
            self.bad_units[(el.get("type"), unit)] = self.bad_units.get((el.get("type"), unit), 0) + 1
        elif v is not None:
            self.temp[(el.get("sourceName") or "", int(t.timestamp()))] = (t, round((v - 32) * 5 / 9, 2) if scale == "F" else v)

    def _glucose(self, el):
        t, v = _when(el.get("startDate")), glucose.to_mgdl(el.get("value"), el.get("unit"))
        if t is None or v is None:
            self.gl_skipped += 1
            return
        if self.gl is None:
            self.gl = sqlite3.connect(":memory:")
            self.gl.executescript(glucose.TABLE)
        glucose._put(self.gl, "hae", el.get("sourceName") or "", str(int(t.timestamp())), t, v, None)   # as the receiver's

    def _height(self, el):
        t, v, unit = _when(el.get("startDate")), _number(el.get("value")), el.get("unit") or ""
        f = HEIGHT_CM.get(unit)
        if t is None or v is None or f is None:
            return
        cm, day = round(v * f, 2), t.date().isoformat()
        if day not in self.heights or t >= self.heights[day][0]:
            self.heights[day] = (t, cm, el.get("sourceName") or "", "ftin" if unit in ("in", "ft") else "cm")

    def _cycle(self, el):
        if self.cycle is None:   # cycle tracking off: not read, not kept
            return
        row = {"date": el.get("startDate"), "value": el.get("value"), "source": el.get("sourceName") or ""}
        for m in el.iter("MetadataEntry"):   # HKMenstrualCycleStart: a flow record that starts a cycle
            row.setdefault(m.get("key"), m.get("value"))
        self.cycle.setdefault(el.get("type"), []).append(row)

    def _more(self):
        """The newer measures' part of the summary: (metrics, top-level fields)."""
        for k, parts in self.bp_part.items():   # a systolic and diastolic pair outside any correlation
            if k not in self.bp and BP_SYS in parts and BP_DIA in parts:
                self.bp[k] = (parts[BP_SYS][0], parts[BP_SYS][1], parts[BP_DIA][1], None)
        app = "Apple Health"
        bp = [(e, t.date().isoformat(), s, d, p, src, app) for (src, e), (t, s, d, p) in sorted(self.bp.items(), key=lambda x: (x[0][1], x[0][0]))]
        temp = [(e, t.date().isoformat(), c, None, None, src, app) for (src, e), (t, c) in sorted(self.temp.items(), key=lambda x: (x[0][1], x[0][0]))]
        out = home_summary(once(bp), once(temp))
        days = out.pop("days")
        metrics = [{"key": k, "name": VITALS_META[k][0], "unit": VITALS_META[k][1], "group": VITALS_META[k][2],
                    "pts": [[d, v] for d, v in sorted(days[k].items())]} for k in VITALS_META if k in days]
        if self.gl is not None:
            metrics += [{**m, "pts": [list(p) for p in m["pts"]]} for m in glucose.summary(self.gl)]
            self.gl.close()
            self.gl = None
        if self.heights:
            out["heights"] = [[d, cm, src or None, u] for d, (_, cm, src, u) in sorted(self.heights.items())]
        if self.cycle:
            con = sqlite3.connect(":memory:")
            con.executescript(cycles.TABLE)
            for typ, rows in self.cycle.items():
                cycles.from_hae(con, {"name": typ, "data": rows}, _when)
            if recs := cycles.records(con):
                out["cycle"] = recs
            con.close()
        return metrics, out

    # -------------------------------------------------------------- summary
    def _sleep_days(self):
        """Per day: the stage totals (seconds) of the one source that slept best that day, as the receiver's."""
        by_src = {}
        for src, s, e, st in self.sleep:
            by_src.setdefault(src, []).append((s, e, st))
        per = {}   # (day, src) -> totals
        for src, segs in by_src.items():
            segs.sort(key=lambda x: x[0])
            sessions, last_end = [], None
            for seg in segs:
                if last_end is None or seg[0] - last_end > SESSION_GAP:
                    sessions.append([])
                    last_end = seg[1]
                sessions[-1].append(seg)
                last_end = max(last_end, seg[1])
            for session in sessions:
                day = max(x[1] for x in session).date().isoformat()   # the session's end, local
                a = per.setdefault((day, src), {"asleep": 0, "deep": 0, "rem": 0, "light": 0, "awake": 0})
                for s, e, st in _without_overlap(session):
                    sec = (e - s).total_seconds()
                    if st in ASLEEP:
                        a["asleep"] += sec
                    if st in ("deep", "rem", "light", "awake"):
                        a[st] += sec
        best = {}
        for (day, src), a in per.items():
            rank = (a["deep"] + a["rem"] + a["light"] > 0, a["asleep"])
            if a["asleep"] and (day not in best or rank > best[day][0]):
                best[day] = (rank, a)
        return {d: a for d, (_, a) in best.items()}

    def summary(self):
        out = {}

        def put(key, d, v):
            if v is not None:
                out.setdefault(key, {})[d] = round(v, 2)

        days = {}
        for (key, day, src), v in self.cum.items():
            k = (key, day)
            days[k] = max(days.get(k, 0.0), v)   # the fullest source that day, not the sum of overlapping ones
        for (key, day), v in days.items():
            put(key, day, round(v) if key == "steps" else v)
        for (key, day), (s, n, mn, mx) in self.avg.items():
            if key == "hr":
                put("hr_avg", day, s / n); put("hr_min", day, mn); put("hr_max", day, mx)
            elif key == "spo2":
                put("spo2_avg", day, s / n); put("spo2_min", day, mn)
            else:
                put(key, day, s / n)
        for d, a in self._sleep_days().items():
            put("sleep_h", d, a["asleep"] / 3600)
            for k in ("deep", "rem", "light", "awake"):
                if a[k]:
                    put(f"sleep_{k}_min", d, a[k] / 60)
        metrics = [{"key": k, "name": META[k][0], "unit": META[k][1], "group": META[k][2],
                    "pts": [[d, v] for d, v in sorted(out[k].items())]} for k in META if k in out]
        more, extra = self._more()   # home blood pressure, temperature, glucose, heights and cycle records (#223)
        exercise = sorted(self.workouts.values(), key=lambda x: x["start"])
        exported = self.exported.isoformat() if self.exported else None
        tz = self.tz or (self.exported.strftime("UTC%z") if self.exported else "UTC")
        return {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tz": tz,
                "last_payload": exported, "source": NAME, "metrics": metrics + more, "exercise": exercise, **extra}

    def warnings(self):
        w = []
        for (typ, unit), n in sorted(self.bad_units.items()):
            w.append(f"Skipped {n} {typ.removeprefix('HKQuantityTypeIdentifier')} record{'s' * (n != 1)} "
                     f"in a unit it can't convert ({unit!r}).")
        if self.bad_dates:
            w.append(f"Skipped {self.bad_dates} record{'s' * (self.bad_dates != 1)} with a date it couldn't read.")
        if n := self.gl_skipped:   # (#223)
            w.append(f"Skipped {n} blood glucose record{'s' * (n != 1)} not in mg/dL or mmol/L, not a reading a meter "
                     "or sensor could give, or with a date it couldn't read.")
        return w


def _workout_hr(el):
    """A workout's heart rate (#197): its WorkoutStatistics for heart rate (iOS 16 and later), {"avg", "max"} in bpm,
    or None. The export's heart rate readings during it are summed into days as they stream past, not kept, so
    there's no time in zones for an uploaded export (server/hrzones.py)."""
    for st in el.iter("WorkoutStatistics"):
        if st.get("type") == "HKQuantityTypeIdentifierHeartRate" and st.get("unit") in (None, "count/min"):
            out = {}
            for k, a in (("avg", "average"), ("max", "maximum")):
                try:
                    out[k] = round(float(st.get(a)), 1)
                except (TypeError, ValueError):
                    pass
            return out or None
    return None


# ---- the phone apps' newer measures (#223): which record types are read by what, and the summing up the receiver shares
MORE = {BP_SYS: _Aggregates._bp_part, BP_DIA: _Aggregates._bp_part, TEMP: _Aggregates._temp,
        GLUCOSE: _Aggregates._glucose, HEIGHT: _Aggregates._height, **{t: _Aggregates._cycle for t in CYCLE}}


def _number(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def _compact(v):
    """128.0 -> 128, 36.65 -> 36.65 (the receiver's numbers, as short as they can be written)."""
    v = round(v, 2)
    return int(v) if v == int(v) else v


def once(rows):
    """Home readings [(epoch, day, v, v2, v3, source, app)], less any with the same minute and values as one before
    it (sent again under another id, by both phone apps, or in an export and the app), oldest first. The receiver's
    rule (watch/receiver.py) and an export's."""
    out, seen = [], set()
    for r in rows:
        k = (r[0] // 60, None if r[2] is None else round(r[2], 1), None if r[3] is None else round(r[3], 1))
        if r[2] is None or k in seen:
            continue
        seen.add(k)
        out.append(r)
    return sorted(out, key=lambda r: (r[0], r[6], r[5]))


def home_summary(bp, temp):
    """{"days": {metric key: {day: value}}, "bp_readings": [[time, systolic, diastolic, pulse, source, app]],
    "temp_readings": [[time, °C, source, app]]} from once()'s readings (app: its name, as shown): each day's average
    and highest, and the readings themselves (time in UTC). The receiver's summary and an export's (#180, #184)."""
    days, out = {}, {}

    def put(key, d, v):
        days.setdefault(key, {})[d] = round(v, 2)
    bp = [r for r in bp if r[3] is not None]
    by = {}
    for _, d, s, di, *_ in bp:
        by.setdefault(d, []).append((s, di))
    for d, xs in by.items():
        put("bp_sys", d, sum(s for s, _ in xs) / len(xs))
        put("bp_dia", d, sum(di for _, di in xs) / len(xs))
        put("bp_sys_max", d, max(s for s, _ in xs))
        put("bp_dia_max", d, max(di for _, di in xs))
        put("bp_n", d, len(xs))
    if bp:
        out["bp_readings"] = [[datetime.fromtimestamp(t, timezone.utc).isoformat(), _compact(s), _compact(di),
                               None if p is None else _compact(p), src or None, app] for t, _, s, di, p, src, app in bp]
    by = {}
    for _, d, c, *_ in temp:
        by.setdefault(d, []).append(c)
    for d, xs in by.items():
        put("body_temp", d, sum(xs) / len(xs))
        put("body_temp_max", d, max(xs))
    if temp:
        out["temp_readings"] = [[datetime.fromtimestamp(t, timezone.utc).isoformat(), _compact(c), src or None, app]
                                for t, _, c, _, _, src, app in temp]
    return {"days": days, **out}


def workout_key(start, end, hk_type):
    """A workout's stable identity: the same in every export that holds it, whatever else changed around it."""
    ident = f"{start.astimezone(timezone.utc).isoformat()}|{end.astimezone(timezone.utc).isoformat()}|{hk_type}"
    return f"{NAME}:workout:{hashlib.sha256(ident.encode()).hexdigest()[:16]}"


def _without_overlap(session):
    """Stage segments with overlaps trimmed (later segments yield to earlier ones), so no minute counts twice."""
    out, cursor = [], None
    for s, e, st in sorted(session, key=lambda x: (x[0], x[1])):
        if cursor is not None and s < cursor:
            s = cursor
        if e > s:
            out.append((s, e, st))
            cursor = e if cursor is None else max(cursor, e)
    return out
