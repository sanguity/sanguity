"""ECG recordings and irregular-rhythm notifications from a watch (#182): what each source says of them, read into one
shape, so the same recording gives the same Timeline event whichever way it came.

What the device said only. A recording's classification is kept word for word as its source wrote it ("Sinus
Rhythm", "Atrial Fibrillation", "Inconclusive", "Not Set"), and nothing here or in the app reads, measures or judges
the trace: no heart rate worked out from it, no words of Sanguity's own about whether it is normal.

Where they come from:
  Apple Health, via Health Auto Export (#148)   its "ECG" data type, `data.ecg`: [{"start", "end" ("yyyy-MM-dd
      HH:mm:ss Z"), "classification", "averageHeartRate" (bpm), "numberOfVoltageMeasurements", "samplingFrequency"
      (Hz), "source", "voltageMeasurements": [{"date" (Unix seconds), "voltage", "units": "mcV"}]}]; and its "Heart
      Rate Notifications" data type, `data.heartRateNotifications`: [{"heartNotification", "start", "end", "source":
      {"name", "identifier"}, "threshold"?, "heartRateData"}]. Of the notifications only the irregular rhythm ones are
      read (their identifier has "irregular" in it); high and low heart rate alerts are left out (they stay in raw/).
      Its help center documents both shapes (help.healthyapps.dev, Export Format: ECG, Heart Rate Notifications);
      the identifier of an irregular rhythm notification isn't spelled out there, hence the loose match.
  An uploaded Apple Health export (the "Export All Health Data" zip)   one CSV file a recording in
      apple_health_export/electrocardiograms/: header lines "Name", "Date of Birth", "Recorded Date",
      "Classification", "Symptoms", "Software Version", "Device", "Sample Rate" ("512 hertz"), "Lead", "Unit" ("µV"),
      a blank line, then one sample a line. The name and birth date lines are read past and never kept. An export
      made in another language names the header lines in that language: they are then taken by their place, in
      Apple's order. export.xml's HKCategoryTypeIdentifierIrregularHeartRhythmEvent records are the notifications.
      The export holds no average heart rate for a recording, so one that came only that way shows none.
  Health Connect (Android)   has no ECG or irregular-rhythm record type, so Life Dashboard Companion sends none.

The shapes (in a watch summary's "ecg" list: daily.json, and the imports' summaries, watch_store.py):
  {"key": "ecg:<16 hex>", "type": "ecg", "start", "end" (UTC, ISO), "classification", "avg_hr", "symptoms",
   "device", "software", "lead", "rate" (Hz), "samples" (how many), "unit": "µV", "via", "wave": true}
  {"key": "rhythm:<16 hex>", "type": "irregular_rhythm", "start", "end", "device", "via"}
  Fields a source doesn't give are left out. The key is a hash of the kind and the start in UTC to the second, the
  same from either source and in every upload or export that holds it, so a recording sent again, or both sent by
  the phone and in an export, is one event (merge() fills each one's missing fields from the other).
The samples are kept apart from the summary, so the records page's data stays small (a 30-second recording at 512 Hz
is 15,360 samples): a waveform {"key", "rate", "unit": "µV", "lead", "samples": [µV]} is a file of its own,
ecg/<16 hex>.json.gz beside the summary that lists it, gzipped and then sealed with the data key (watch_store.py for
imports, watch/receiver.py for the phone's), read only when its drawer opens (GET /api/profiles/{pid}/watch/ecg/<hex>).

Standard library only: the Apple Health importer uses it too.
"""
import csv, gzip, hashlib, io, json, re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

KEY_RE = re.compile(r"^(ecg|rhythm):([0-9a-f]{16})$")
NAME_RE = re.compile(r"^[0-9a-f]{16}$")   # a waveform's file name, less .json.gz
VOLT = {"mcv": 1, "µv": 1, "μv": 1, "uv": 1, "microvolts": 1, "mv": 1000, "v": 1_000_000}   # a unit -> µV
CSV_FIELDS = ["name", "date of birth", "recorded date", "classification", "symptoms", "software version", "device",
              "sample rate", "lead", "unit"]   # Apple's header lines, in its order
DROP = {"name", "date of birth"}   # read past, never kept
MAX_SAMPLES = 200_000   # far over a 30-second recording at 512 Hz (15,360); a file with more is cut there
EXPORT_DIR = "electrocardiograms"
IRREGULAR = "HKCategoryTypeIdentifierIrregularHeartRhythmEvent"


def key(kind, start):
    """The stable key of a recording ("ecg") or a notification ("rhythm") starting at start (an aware datetime)."""
    ident = f"{kind}|{start.astimezone(timezone.utc).replace(microsecond=0).isoformat()}"
    return f"{kind}:{hashlib.sha256(ident.encode()).hexdigest()[:16]}"


def file_name(k):
    """A recording's key -> its waveform's file name, or None for anything else."""
    m = KEY_RE.match(k or "")
    return f"{m.group(2)}.json.gz" if m and m.group(1) == "ecg" else None


def when(s):
    """"2026-10-03 08:15:00 -0400" (or ISO 8601 with an offset) -> an aware datetime, or None."""
    if not isinstance(s, str):
        return None
    s = " ".join(s.replace(" ", " ").split())
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %I:%M:%S %p %z"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else None


def _num(v):
    if isinstance(v, dict):
        v = v.get("qty")
    if isinstance(v, bool):
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def _text(v, n=200):
    """A source's own words, trimmed to one line of at most n characters ("" for none)."""
    if isinstance(v, dict):
        v = v.get("name")
    return " ".join(str(v).split())[:n] if isinstance(v, (str, int, float)) and not isinstance(v, bool) else ""


def _compact(v):
    v = round(v, 3)
    return int(v) if v == int(v) else v


def _event(kind, start, end=None, **fields):
    ev = {"key": key("ecg" if kind == "ecg" else "rhythm", start), "type": kind,
          "start": start.astimezone(timezone.utc).isoformat()}
    if end is not None and end >= start:
        ev["end"] = end.astimezone(timezone.utc).isoformat()
    ev.update({k: v for k, v in fields.items() if v not in (None, "")})
    return ev


def _recording(start, samples, rate, unit_factor, end=None, **fields):
    """(event, waveform or None) of one recording: samples in the source's unit, turned into µV."""
    samples = [_compact(v * unit_factor) for v in samples[:MAX_SAMPLES]]
    if samples and rate and rate > 0:
        end = start + timedelta(seconds=len(samples) / rate)
    rate = _compact(rate) if rate else None
    ev = _event("ecg", start, end, rate=rate, samples=len(samples) or None, unit="µV" if samples else None, **fields)
    if not samples:
        return ev, None
    ev["wave"] = True
    return ev, {"key": ev["key"], "rate": rate, "unit": "µV", **({"lead": ev["lead"]} if ev.get("lead") else {}),
                "samples": samples}


# ------------------------------------------------------------------ Health Auto Export
def from_hae(d, parse_time=when):
    """Health Auto Export's upload data ({"ecg": [...], "heartRateNotifications": [...]}) -> (events, {key: waveform},
    left out: how many entries couldn't be read or aren't irregular rhythm notifications)."""
    events, waves, skipped = [], {}, 0
    for e in d.get("ecg") or [] if isinstance(d.get("ecg"), list) else []:
        start = parse_time(e.get("start")) if isinstance(e, dict) else None
        if start is None:
            skipped += 1
            continue
        vm = [m for m in e.get("voltageMeasurements") or [] if isinstance(m, dict)] if isinstance(e.get("voltageMeasurements"), list) else []
        vm.sort(key=lambda m: _num(m.get("date")) or 0)
        unit = VOLT.get(str((vm[0].get("units") if vm else None) or "mcV").strip().lower())
        vals = [_num(m.get("voltage")) for m in vm] if unit else []
        if any(v is None for v in vals):
            vals = []
        hr = _num(e.get("averageHeartRate"))
        ev, wave = _recording(start, vals, _num(e.get("samplingFrequency")), unit or 1, parse_time(e.get("end")),
                              classification=_text(e.get("classification")), avg_hr=None if hr is None else _compact(hr),
                              device=_text(e.get("source")), via="Health Auto Export")
        events.append(ev)
        if wave:
            waves[ev["key"]] = wave
    for n in d.get("heartRateNotifications") or [] if isinstance(d.get("heartRateNotifications"), list) else []:
        start = parse_time(n.get("start")) if isinstance(n, dict) else None
        if start is None or "irregular" not in str(n.get("heartNotification") or "").lower():
            skipped += 1
            continue
        events.append(_event("irregular_rhythm", start, parse_time(n.get("end")), device=_text(n.get("source")),
                             via="Health Auto Export"))
    return events, waves, skipped


# ------------------------------------------------------------------ an uploaded Apple Health export
def export_files(files):
    """The upload's ECG files: {path: file on disk} in an electrocardiograms folder."""
    return {p: a for p, a in files.items() if p.lower().endswith(".csv") and EXPORT_DIR in p.lower().split("/")[:-1]}


def from_csv(text):
    """One file of an export's electrocardiograms folder -> (event, waveform or None). Raises ValueError when it
    isn't one (no date it can read)."""
    rows = csv.reader(io.StringIO(text.lstrip("﻿")))
    head, samples = [], []
    for row in rows:
        cells = [c.strip() for c in row]
        if not any(cells):
            if head:
                break   # the blank line after the header
            continue
        if _num(cells[0]) is not None and head:
            samples.append(_num(cells[0]))
            break
        head.append((cells[0].lower(), ",".join(c for c in cells[1:] if c) if len(cells) > 1 else ""))
        if len(head) > 30:
            raise ValueError("its header doesn't end")
    for row in rows:
        if samples and len(samples) >= MAX_SAMPLES:
            break
        v = _num(row[0].strip()) if row else None
        if v is not None:
            samples.append(v)
    named = {k: v for k, v in head if k in CSV_FIELDS}
    if "recorded date" not in named and len(head) >= len(CSV_FIELDS):   # another language: Apple's order
        named = {CSV_FIELDS[i]: v for i, (_, v) in enumerate(head[:len(CSV_FIELDS)])}
    for k in DROP:   # never kept, not even for a moment longer than this
        named.pop(k, None)
    head = None
    start = when(named.get("recorded date"))
    if start is None:
        raise ValueError("no recording date it can read")
    rate = _num((re.match(r"\s*([\d.]+)", named.get("sample rate") or "") or [None, None])[1])
    unit = VOLT.get((named.get("unit") or "µV").strip().lower())
    return _recording(start, samples if unit else [], rate, unit or 1, classification=_text(named.get("classification")),
                      symptoms=_text(named.get("symptoms"), 500), software=_text(named.get("software version")),
                      device=_text(named.get("device")), lead=_text(named.get("lead")), via="Apple Health export")


def from_export(files, max_bytes=8 * 2**20):
    """The ECG files of an uploaded export -> (events, {key: waveform}, warnings)."""
    events, waves, bad = {}, {}, 0
    for p, a in sorted(export_files(files).items()):
        try:
            with open(a, "rb") as f:
                raw = f.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ValueError("too big")
            ev, wave = from_csv(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError, csv.Error):
            bad += 1
            continue
        events[ev["key"]] = ev
        if wave:
            waves[ev["key"]] = wave
    warnings = [f"{bad} ECG file{'s' * (bad != 1)} in the export couldn't be read, so {'they were' if bad != 1 else 'it was'} "
                "left out."] if bad else []
    return list(events.values()), waves, warnings


def from_record(el, parse_time=when):
    """An export.xml <Record> (an element, or its attributes) of an irregular rhythm notification -> an event, or None."""
    get = el.get
    start = parse_time(get("startDate"))
    if get("type") != IRREGULAR or start is None:
        return None
    return _event("irregular_rhythm", start, parse_time(get("endDate")), device=_text(get("sourceName")),
                  via="Apple Health export")


# ------------------------------------------------------------------ summaries and waveforms
def merge(low, high):
    """Two "ecg" lists laid together by key: high's fields win, and each event keeps what only low had (an export's
    lead and software version beside the phone's average heart rate). Sorted by start."""
    out = {}
    for src in (low or [], high or []):
        for e in src:
            if isinstance(e, dict) and KEY_RE.match(str(e.get("key") or "")):
                out[e["key"]] = {**out.get(e["key"], {}), **e}
    return sorted(out.values(), key=lambda e: (str(e.get("start")), e["key"]))


def pack(wave):
    """A waveform as the bytes of its file (gzipped JSON; the caller seals them)."""
    return gzip.compress(json.dumps(wave, separators=(",", ":"), ensure_ascii=False).encode("utf-8"), mtime=0)


def unpack(raw):
    v = json.loads(gzip.decompress(raw))
    return v if isinstance(v, dict) and isinstance(v.get("samples"), list) else None


def shared_rows(summary, start=None, end=None):
    """The hook for #225, which may let a share link or the visit summary include ECGs when its owner ticks them (they
    never are now): [{"when": local day, "title", "text"}] for the events from start to end (ISO days, both optional),
    newest first, with what the device said only and never the samples."""
    out = []
    try:   # the day in the summary's own time zone, else UTC's
        tz = ZoneInfo(str((summary or {}).get("tz") or "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        tz = timezone.utc
    for e in (summary or {}).get("ecg") or []:
        t = when(e.get("start")) if isinstance(e, dict) else None
        day = t.astimezone(tz).date().isoformat() if t else ""
        if not day or (start and day < start) or (end and day > end):
            continue
        rec = e.get("type") == "ecg"
        out.append({"when": day, "title": ("ECG: " + e["classification"]) if rec and e.get("classification") else
                    "ECG" if rec else "Irregular rhythm notification",
                    "text": " · ".join(x for x in (f"{e['avg_hr']} bpm average" if rec and e.get("avg_hr") is not None else "",
                                                   e.get("device") or "") if x)})
    return sorted(out, key=lambda r: r["when"], reverse=True)


def counts(summary):
    """{"ecgs": recordings, "rhythm": irregular rhythm notifications} in a watch summary, those it has (for the
    import's counts)."""
    evs = [e for e in (summary or {}).get("ecg") or [] if isinstance(e, dict)]
    n = {"ecgs": sum(e.get("type") == "ecg" for e in evs), "rhythm": sum(e.get("type") == "irregular_rhythm" for e in evs)}
    return {k: v for k, v in n.items() if v}
