#!/usr/bin/env python3
"""Headless browser check of the app with the fictional demo data, served by the real backend.

Starts the API (with www/ served by it) on a temporary data folder holding the demo build, signs in through the
login page, opens every tab and checks its main content rendered, opens the detail drawer for every kind of
event, drags and scroll-zooms the charts, toggles the event sort, switches profiles, uploads a zip and files on
the import page (a fictional C-CDA and a FHIR export, previewed, saved in the background and then shown on the timeline;
the C-CDA again, previewed as all duplicates and cancelled; the FHIR one then undone from the import history, and
gone from the timeline; UHC, BCBS and spreadsheet claims, shown on the Costs tab; a CareSpace and an Epic MyChart
export), types two lab results in on the import page and finds them on Trends, types a visit, a medication, a
claim and a letter with a PDF into their own forms, finds them on the timeline and Costs and opens the PDF, types in a payment with
its receipt and an HSA tag and sees it matched to the claim, counted once and retagged FSA on Costs (#55), uploads a lab with a spelling Sanguity
doesn't know, accepts the series suggested at the preview and finds it on Trends, then sees it matched as before on
the next upload, pastes a fictional SMART Health Link served by a local HTTPS server (what it says is shown and
nothing is fetched until Fetch; then its preview, and saved), sets up a connected source against the fake SMART on
FHIR server (tests/fakefhir), connects a profile by signing in with a choice of what to pull, saves its first pull's
preview, sees the attachments the install's rules left out and fetches one anyway, syncs a new result, changes what
the connection pulls, and is asked to reconnect when access ends, with a warning on the profile's main page, uploads a
Blue Button export and connects a health plan's patient-access API (the fake again) and finds both sets of claims on
Costs, sees the source's and the Health Link's hosts under Admin → Outside connections, changes settings on the
Settings page and resets them (a milestone added there marked on Trends and a "since" period on Health; a
workout rule, a catastrophic pattern refused first, counted on Health; drags and keyboard-moves the key-lab tiles, hides and
reorders tabs, sets a default theme a fresh browser picks up, picks SI units and kg and sees them on Trends, the
Overview tiles and the drawer with the reported values beside them, sets ranges of its own for results whose
report gives none and sees them used and labelled on Trends, the heatmap, the Overview and the visit summary, #44),
builds a visit summary (sections, a lab, questions
kept as a draft), previews it and downloads the PDF, reading it back, has two accounts in different time zones each see days split at their own midnight and
dates in their own format, journal times and the visit summary page included (#42), makes the profile's watch receiver secret on the Settings page, shown once and then
masked (#46), with no Settings card showing a stray "null" (#175), opens the account
page and the Admin page (#167: accounts, the sync defaults, the provider directory, the lab name match listed, the
install's activity; old addresses landing there; someone who isn't an admin told it isn't for them and shown no server
setting or command on the everyday pages), sees each record's source in the drawer, on Trends and on the heatmap, links the
same result from a file and the sync at the first pull's preview and sees it once on Trends and in the drawer (undone
and linked again there)
and follows its link to the import history, makes a share link on the account page and opens it with no session
under the real CSP (only the chosen sections and lab, no cookie, at 375 px too), finds the view in the activity and
revokes it (#47), checks the "not medical advice" notice on every tab and beside
out-of-range highlighting, sees a medication's start, dose change, stop and pharmacy fills as spans on the
timeline, in its history in the drawer and, when ticked, under the Trends charts (#50, at 375 px too), opens every tab
and every page's groups at 375 px in light and dark with nothing scrolling sideways and no control cut off or
overlapping (#61), runs axe-core's WCAG 2.0, 2.1 and 2.2 A and AA rules on about a hundred pages and states in light
and dark (a serious or critical finding fails), Tabs and Shift+Tabs through the pages at 375 px (focus always shown,
never under a sticky bar), fits every page at 320 px and with WCAG's text spacing, and checks forced colours and the
sign-in's autofill and paste (#202, docs/accessibility.md), and fails
on any script error, console error, failed request, request to another host or NaN written into an SVG attribute.

  pip install -r requirements-dev.txt && playwright install chromium
  python3 tests/browser_check.py
"""
import json, os, re, shutil, socket, subprocess, sys, tempfile, threading, time, urllib.parse, urllib.request

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-browser-")
USER, PW = "demo", "a long demo passphrase"
USER2, PW2 = "pat", "another long demo passphrase"   # a second account, viewing profile 1 from another time zone (#42)
SHL = None   # the local HTTPS host of a fictional SMART Health Link (tests/shl_server.py), started with the app
NAN_HOOK = """window.__nan = []; const sa = Element.prototype.setAttribute;
Element.prototype.setAttribute = function (k, v) { if (String(v) === "NaN") window.__nan.push(this.tagName + "." + k); return sa.call(this, k, v); };"""


HTML_RECORD = 990001


def add_html_record(build):
    """An .htm record with a stylesheet and a picture saved beside it, like portal exports have (the demo's only
    document is a PDF), so the drawer is checked with a record that loads its own parts."""
    folder = os.path.join(build, "files", "visit summary_files")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(build, "files", "visit summary.htm"), "w", encoding="utf-8") as f:
        f.write('<link rel="stylesheet" href="visit summary_files/style.css"><p id="t">Visit summary</p>'
                '<img id="i" src="visit summary_files/logo.png">'
                '<meta http-equiv="refresh" content="1; url=https://example.invalid/moved">')   # it mustn't move its tab (#221)
    with open(os.path.join(folder, "style.css"), "w", encoding="utf-8") as f:
        f.write("#t { color: rgb(255, 0, 0) }")
    with open(os.path.join(folder, "logo.png"), "wb") as f:   # a 1x1 PNG
        f.write(bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c63"
                              "f8ffff3f0005fe02fea7d6a4ba0000000049454e44ae426082"))
    path = os.path.join(build, "data.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data["events"].append({"id": HTML_RECORD, "t": "2025-10-01T10:00:00", "k": "document", "title": "Visit summary (HTM)", "sub": "Test"})
    data["details"][str(HTML_RECORD)] = {"type": "file", "ext": "htm", "url": "files/visit summary.htm"}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


MED_RECORDS = 990101


def add_med_records(build):
    """A fictional medication with a dose change and a stop, listed by a clinic, with pharmacy fills (#50): the demo's
    own medications are each listed once, with no end."""
    path = os.path.join(build, "data.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    i = MED_RECORDS
    rows = [("2025-02-03T09:00:00", "Examplocin 5 mg tablet", "Active", None), ("2025-05-12T10:00:00", "Examplocin 10 mg tablet", "Active", None),
            ("2025-08-20T10:00:00", "Examplocin 10 mg tablet", "Discontinued", "2025-09-30")]
    for t, title, status, end in rows:
        data["events"].append({"id": i, "t": t, "k": "med", "title": title, "sub": f"Take 1 tablet daily · {status}"})
        data["details"][str(i)] = {"type": "med", "instructions": "Take 1 tablet daily", "status": status, "src": "Fictional Clinic",
                                   "prescriber": "Dr. Rowan Vale", "rxnorm": "9990001", **({"end": end} if end else {})}
        i += 1
    for t, title in (("2025-02-05T17:00:00", "EXAMPLOCIN 5MG TAB"), ("2025-03-07T17:00:00", "EXAMPLOCIN 5MG TAB"), ("2025-05-14T17:00:00", "EXAMPLOCIN 10MG TAB")):
        data["events"].append({"id": i, "t": t, "k": "rx", "title": title, "sub": "Pharmacy fill · 30-day supply"})
        i += 1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


RANGE_RECORDS = 990201


def add_range_records(build):
    """Two creatinine results from a fictional outside lab whose range (0.6–1.2 mg/dL) isn't the demo clinic's
    (0.7–1.3), between the clinic's own (#58): one chart with two ranges. 1.25 is high by its own lab's range, though
    inside the clinic's."""
    path = os.path.join(build, "data.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    cr = next(s for s in data["labs"] if s["name"] == "Creatinine")
    i = RANGE_RECORDS
    for t, v, flag in (("2026-07-15T09:10:00", 0.95, ""), ("2026-09-10T09:05:00", 1.25, "H")):
        cr["pts"].append([t, v, 0.6, 1.2, flag, str(v)])
        data["events"].append({"id": i, "t": t, "k": "lab", "title": "1 lab result", "sub": "CREATININE"})
        data["details"][str(i)] = {"type": "labday", "ids": [i + 1]}
        data["events"].append({"id": i + 1, "t": t, "k": "labitem", "title": "CREATININE", "sub": "Final · Fictional Outside Lab", "hidden": 1})
        data["details"][str(i + 1)] = {"type": "result", "order": "CREATININE", "collected": t[:16].replace("T", " "), "resulted": t[:10],
                                       "status": "Final", "specimen": "Blood", "lab": "Fictional Outside Lab", "dx": [],
                                       "rows": [["Creatinine", str(v), "mg/dL", "0.6 - 1.2", flag]], "report": [], "comments": []}
        i += 2
    cr["pts"].sort(key=lambda p: p[0])
    cr["n"] = len(cr["pts"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


GLUCOSE_GAP_DAY = "2026-11-09"   # the CGM's day with a six-hour gap in its readings (#181)


def add_glucose_watch(watch):
    """Blood glucose for Body and activity (#181), made from fictional readings by the receiver's own summary
    (server/glucose.py): a meter's finger-sticks twice a day from October 1, then a CGM every five minutes for the
    last two weeks, with a sensor gap on GLUCOSE_GAP_DAY. The demo itself has none."""
    import math, random, sqlite3
    from datetime import datetime, timedelta, timezone
    sys.path.insert(0, ROOT)
    from server import glucose
    rnd, off = random.Random(181), timezone(timedelta(hours=-5))
    con = sqlite3.connect(":memory:")
    con.executescript(glucose.TABLE)
    put = lambda src, at, v, kind: con.execute("INSERT INTO glucose VALUES('hc', ?, ?, ?, ?, ?, ?)", (src, str(int(at.timestamp())),
                                               int(at.timestamp()), at.strftime("%Y-%m-%dT%H:%M"), v, kind))
    for d in range(30):   # October: a meter before breakfast and after dinner
        day = datetime(2026, 10, 1, tzinfo=off) + timedelta(days=d)
        put("com.example.fictionalmeter", day.replace(hour=7, minute=rnd.randint(0, 40)), rnd.randint(88, 118), "meter")
        put("com.example.fictionalmeter", day.replace(hour=20, minute=rnd.randint(0, 40)), rnd.randint(110, 175), "meter")
    start = datetime(2026, 11, 1, tzinfo=off)
    for i in range(14 * 288):   # November 1 to 14: a sensor every five minutes, meals bringing peaks
        at = start + timedelta(minutes=5 * i)
        if at.strftime("%Y-%m-%d") == GLUCOSE_GAP_DAY and 1 <= at.hour < 7:
            continue
        h = at.hour + at.minute / 60
        v = 104 + sum(k * math.exp(-((h - m) / 1.1) ** 2) for m, k in ((8.2, 62), (13, 48), (19.4, 78)))
        v += -40 * math.exp(-((h - 3.5) / 1.2) ** 2) * (1 if at.day in (4, 11) else 0) + rnd.gauss(0, 6)
        put("com.example.fictionalcgm", at, round(v), "cgm")
    put("com.example.fictionalmeter", datetime(2026, 11, 6, 12, 30, tzinfo=off), 152, "meter")   # a finger-stick while it ran
    path = os.path.join(watch, "daily.json")
    with open(path, encoding="utf-8") as f:
        daily = json.load(f)
    daily["metrics"] += glucose.summary(con)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(daily, f)


IMM_RECORDS = 990301


def add_immunization_records(build):
    """#179: fictional immunizations, allergies and a pharmacy fill as the C-CDA and FHIR importers write them
    (common.immunization, common.allergy; a MedicationDispense's fill): the demo has none."""
    sys.path.insert(0, ROOT)
    from server.importers import common
    path = os.path.join(build, "data.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    i = IMM_RECORDS
    for t, vaccine, fields in (
            ("2024-03-14T09:20:00", "Tdap vaccine", {"code": "115", "system": "CVX", "dose": "1", "site": "Right arm", "lot": "FICT-TDAP-3",
                                                      "where": "Fictional Corner Pharmacy"}),
            ("2025-09-10T15:00:00", "COVID-19 vaccine", {"code": "208", "system": "CVX", "lot": "FICT-0002", "where": "Fictional County Health Department"}),
            ("2025-10-02T10:15:00", "Influenza vaccine", {"code": "150", "system": "CVX", "dose": "1", "amount": "0.5 mL", "route": "Intramuscular",
                                                           "site": "Left deltoid", "lot": "FICT-FLU-11", "manufacturer": "Fictional Vaccines Inc.",
                                                           "performer": "Sam Ortega, RN", "where": "Fictional Clinic", "status": "Completed"})):
        title, sub, det = common.immunization(vaccine, fields.get("where", ""), **fields)
        data["events"].append({"id": i, "t": t, "k": "immunization", "title": title, "sub": sub})
        data["details"][str(i)] = det
        i += 1
    data["events"].append({"id": i, "t": "2025-09-01T16:30:00", "k": "rx", "title": "Fictionalol 25 mg tablet",
                           "sub": "Pharmacy fill · Fictional Corner Pharmacy · 30-day supply"})
    data["details"][str(i)] = {"type": "med", "instructions": "Take 1 tablet daily", "status": "Completed", "src": "Fictional Corner Pharmacy",
                               "pharmacy": "Fictional Corner Pharmacy", "daysSupply": 30, "quantity": "30 tablet", "prescriber": "Dr. Rowan Vale"}
    data["problems"] = data.get("problems", []) + [
        common.allergy("Penicillin G", ["Hives"], severity="Moderate", criticality="High", status="Active", date="2010-01-01", src="Fictional Clinic"),
        common.allergy("Peanuts", ["Anaphylaxis", "Rash"], severity="Severe", status="Active", date="2019-03-08", src="Fictional Clinic"),
        common.allergy("Latex", ["Itching"], status="Resolved", date="2015-05-01", src="Fictional Clinic")]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def receiver():
    """watch/receiver.py, loaded as a module (it folds the fictional phone uploads below as it folds real ones)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("watch_receiver", os.path.join(ROOT, "watch", "receiver.py"))
    rx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rx)
    return rx


def home_readings():
    """Fictional home blood pressure (#180), morning and some evenings from July to mid-November 2026, local times in
    New York: [(local time, systolic, diastolic, pulse or None)]. Made by formula, so the same on every run."""
    from datetime import date, timedelta
    out, d, i = [], date(2026, 7, 1), 0
    while d <= date(2026, 11, 14):
        if i % 6 != 5:   # a few mornings missed
            out.append((f"{d}T07:{(i * 7) % 40 + 5:02d}:00", 120 + (i * 7) % 11 + (4 if i % 9 == 0 else 0), 76 + (i * 5) % 7, None if i % 4 == 3 else 62 + i % 9))
        if i % 3 == 0:
            out.append((f"{d}T21:{(i * 3) % 30 + 10:02d}:00", 116 + (i * 5) % 9, 74 + (i * 3) % 6, 70 + i % 5))
        d += timedelta(days=1)
        i += 1
    return out


def temp_spo2_readings():
    """Fictional body temperature and blood oxygen (#184), local times in New York: a cold in August and one in October,
    a thermometer's readings morning and evening [(local time, °C)]; and a watch's blood oxygen three times a night from
    August to mid-November [(local time, %)]. Made by formula, so the same on every run."""
    from datetime import date, timedelta
    temps = [(f"{d}T{h}:{10 + (i * 7) % 40:02d}:00", round(37.0 + (0.9 if i < 3 else 0.2) - 0.1 * (i % 3), 1))
             for start in (date(2026, 8, 20), date(2026, 10, 2)) for i, (d, h) in enumerate((start + timedelta(days=k), h) for k in range(4) for h in ("08", "20"))]
    import random
    rnd, spo2, d, i = random.Random(184), [], date(2026, 8, 1), 0
    while d <= date(2026, 11, 14):
        for h in (1, 3, 5):
            spo2.append((f"{d}T0{h}:{(i * 11) % 50 + 5:02d}:00", rnd.choice((93, 94, 95, 95, 96, 96, 96, 97, 97, 98))))
        d += timedelta(days=1)
        i += 1
    return temps, spo2


def add_vitals_records(build, watch_dir):
    """Home blood pressure from both phone apps (#180), folded by the receiver itself into the demo's watch summary:
    through Health Auto Export until mid-September (some uploads sent twice, overlapping), then Life Dashboard
    Companion, each upload sent again; and blood pressure taken at visits, in the records as systolic and diastolic
    series, one of them flagged high by its report and one the same as a home reading (typed in as vitals too).
    Body temperature and blood oxygen the same way (#184): Health Auto Export's in °F, Life Dashboard's in °C; at the
    visits, a temperature in °F (one flagged high) and SpO2."""
    import hashlib, tempfile as tf
    from datetime import datetime
    from zoneinfo import ZoneInfo
    rx = receiver()
    rx.DATA = tf.mkdtemp(prefix="rx-", dir=TMP)
    store = rx.Store()
    ny = ZoneInfo("America/New_York")
    at = lambda local: datetime.fromisoformat(local).replace(tzinfo=ny)
    rows = home_readings()
    iphone, android = [r for r in rows if r[0] < "2026-09-15"], [r for r in rows if r[0] >= "2026-09-15"]

    def send(p):
        b = json.dumps(p).encode()
        store.ingest(json.loads(b), hashlib.sha256(b).hexdigest())
    for chunk in (iphone[:60], iphone[50:], iphone[50:]):   # overlapping, and one sent twice
        send({"data": {"metrics": [{"name": "blood_pressure", "units": "mmHg", "data": [
            {"date": at(t).strftime("%Y-%m-%d %H:%M:%S %z"), "systolic": s, "diastolic": d, "source": "Fictional Cuff",
             **({"pulse": p} if p is not None else {})} for t, s, d, p in chunk]}]}})
    for seq in (1, 2):
        send({"source": "health_connect", "timezone": "America/New_York", "sequence": seq, "timestamp": "2026-11-15T12:00:00Z",
              "blood_pressure": [{"uuid": f"fictional-bp-{t}", "time": at(t).astimezone(ZoneInfo("UTC")).isoformat(), "systolic": s, "diastolic": d,
                                  "source": "Fictional Cuff", **({"pulse": p} if p is not None else {})} for t, s, d, p in android]})
    temps, spo2 = temp_spo2_readings()   # #184: before mid-September from the iPhone, in °F; then from Health Connect
    hae_when = lambda t: at(t).strftime("%Y-%m-%d %H:%M:%S %z")
    for _ in (1, 2):
        send({"data": {"metrics": [
            {"name": "body_temperature", "units": "degF", "data": [{"date": hae_when(t), "qty": round(c * 9 / 5 + 32, 2), "source": "Fictional Thermometer"} for t, c in temps if t < "2026-09-15"]},
            {"name": "blood_oxygen_saturation", "units": "%", "data": [{"date": hae_when(t), "qty": v, "source": "Fictional Watch"} for t, v in spo2 if t < "2026-09-15"]}]}})
    utc = lambda t: at(t).astimezone(ZoneInfo("UTC")).isoformat()
    send({"source": "health_connect", "timezone": "America/New_York", "sequence": 3, "timestamp": "2026-11-15T12:00:00Z",
          "body_temperature": [{"uuid": f"fictional-t-{t}", "time": utc(t), "celsius": c, "source": "Fictional Thermometer"} for t, c in temps if t >= "2026-09-15"],
          "oxygen_saturation": [{"uuid": f"fictional-o-{t}", "time": utc(t), "percentage": v, "source": "Fictional Watch"} for t, v in spo2 if t >= "2026-09-15"]})
    got = store.summary()
    path = os.path.join(watch_dir, "daily.json")
    with open(path, encoding="utf-8") as f:
        watch = json.load(f)
    watch["metrics"] += [m for m in got["metrics"] if m["key"] in rx.VITALS_META or m["key"] in ("spo2_avg", "spo2_min")]
    watch["bp_readings"], watch["temp_readings"] = got["bp_readings"], got["temp_readings"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(watch, f)
    same = next(r for r in rows if r[0].startswith("2026-10-20T07"))   # a home reading typed in as vitals as well
    visits = [("2025-01-14T10:20:00-05:00", 126, 80, "", ""), ("2025-06-10T10:05:00-04:00", 131, 84, "", ""),
              ("2026-08-12T10:15:00-04:00", 138, 86, "", ""), ("2026-10-06T10:10:00-04:00", 146, 92, "H", "H"),
              (at(same[0]).isoformat(), same[1], same[2], "", "")]
    data_path = os.path.join(build, "data.json")
    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)
    for name, loinc, i in (("Blood pressure, systolic", "8480-6", 1), ("Blood pressure, diastolic", "8462-4", 2)):
        pts = [[v[0], v[i], None, None, v[i + 2], str(v[i]), "Typed in" if v[0] == at(same[0]).isoformat() else "Lakeside Family Medicine"] for v in visits]
        data["labs"].append({"name": name, "key": f"loinc:{loinc}", "loinc": loinc, "unit": "mmHg", "panel": "Vitals", "n": len(pts), "pts": pts, "txt": []})
    for name, loinc, unit, vals in (("Temperature", "8310-5", "°F", [(98.4, ""), (100.6, "H"), (98.1, ""), (98.6, "")]),
                                    ("SpO2 (clinic)", "59408-5", "%", [(98, ""), (97, ""), (97, ""), (96, "")])):   # #184
        pts = [[v[0], x, None, None, f, str(x), "Lakeside Family Medicine"] for v, (x, f) in zip(visits, vals)]
        data["labs"].append({"name": name, "key": f"loinc:{loinc}", "loinc": loinc, "unit": unit, "panel": "Vitals", "n": len(pts), "pts": pts, "txt": []})
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f)


CYCLE_RX = {}   # the receiver that folded the cycle fixture in, kept to carry out the deletion the page asks for (#183)


def add_cycle_watch(watch_dir):
    """Menstrual cycle records (#183), folded by the receiver itself from fictional uploads of both phone apps, with
    cycle tracking on for its slot: Health Auto Export's flow and ovulation tests from January to April 2026, then
    Life Dashboard Companion's periods (logged as periods), flow, ovulation tests, cervical mucus and one spotting, and
    sexual activity, which is never kept. It writes cycle.json beside the demo's daily.json, as the receiver does; the
    app shows none of it until the profile's owner turns cycle tracking on (the demo has it off)."""
    import hashlib, tempfile as tf
    from datetime import date, datetime, timedelta, timezone
    rx = receiver()
    rx.DATA = tf.mkdtemp(prefix="rx-cycle-", dir=TMP)
    rx.KEYS_FILE = os.path.join(rx.DATA, "keys.json")
    from server import crypto
    crypto.write_file(rx.KEYS_FILE, json.dumps({"profiles": [], "cycle": [""]}).encode())
    rx.cycle_file = lambda slot: os.path.join(watch_dir, "cycle.json")
    store = rx.Store("")

    def send(p):
        b = rx.cycle.keep(json.dumps(p).encode(), True)
        store.ingest(json.loads(b), hashlib.sha256(b).hexdigest())
    starts = [date(2026, 1, 4) + timedelta(days=sum(28 + (k % 3 == 1) for k in range(i))) for i in range(12)]   # every 28 or 29 days
    flow = ("Light", "Medium", "Heavy", "Medium", "Light")
    off = "-0500"
    send({"data": {"metrics": [
        {"name": "menstrual_flow", "units": "", "data": [{"date": f"{s + timedelta(days=d)} 08:00:00 {off}", "value": v, "source": "Fictional Phone"}
                                                         for s in starts[:4] for d, v in enumerate(flow)]},
        {"name": "ovulation_test_result", "units": "", "data": [{"date": f"{s + timedelta(days=d)} 07:30:00 {off}", "value": v, "source": "Fictional Phone"}
                                                                for s in starts[:4] for d, v in ((12, "Negative"), (14, "LH surge"))]},
        {"name": "sexual_activity", "units": "", "data": [{"date": f"{starts[1] + timedelta(days=9)} 22:00:00 {off}", "qty": 1}]}]}})
    utc = lambda d, h: (datetime.fromisoformat(f"{d}T{h:02d}:00:00") + timedelta(hours=5)).replace(tzinfo=timezone.utc).isoformat()
    hc = starts[4:]
    send({"source": "health_connect", "timezone": "America/New_York", "sequence": 1, "timestamp": "2026-11-15T12:00:00Z",
          "menstruation_period": [{"uuid": f"fictional-period-{s}", "start_time": utc(s, 6), "end_time": utc(s + timedelta(days=4), 20), "source": "Fictional Cycle App"} for s in hc],
          "menstruation_flow": [{"uuid": f"fictional-flow-{s}-{d}", "time": utc(s + timedelta(days=d), 9), "flow": [1, 2, 3, 2, 1][d], "source": "Fictional Cycle App"}
                                for s in hc for d in range(5)],
          "ovulation_test": [{"uuid": f"fictional-ovu-{s}", "time": utc(s + timedelta(days=14), 7), "result": 1, "source": "Fictional Cycle App"} for s in hc],
          "cervical_mucus": [{"uuid": f"fictional-mucus-{s}", "time": utc(s + timedelta(days=13), 7), "appearance": 5, "sensation": 2, "source": "Fictional Cycle App"} for s in hc],
          "intermenstrual_bleeding": [{"uuid": "fictional-spot-1", "time": utc(hc[3] + timedelta(days=19), 18), "source": "Fictional Cycle App"}],
          "sexual_activity": [{"uuid": "fictional-sa-1", "time": utc(hc[2] + timedelta(days=10), 22)}]})
    store.save()
    store.write_summary()
    CYCLE_RX.update(rx=rx, store=store)


def cycle_receiver_sweep():
    """The receiver's worker between uploads (#183): read the app's secrets file and carry out what it asks."""
    rx, store = CYCLE_RX["rx"], CYCLE_RX["store"]
    rx.KEYS_FILE = os.environ["SANGUITY_WATCH_KEYS"]
    rx._keys.update(sig=False, doc=None)
    rx.cycle_sweep({"": store})


ECG_FIXTURE = os.path.join(ROOT, "tests", "fixtures", "importers", "apple-health", "ecg", "apple_health_export")


def add_ecg_watch(db, watch_dir):
    """#182: a watch's fictional ECGs and irregular rhythm notifications (tests/fixtures/importers/apple-health/ecg, made
    by tests/test_ecg.py) for profile 1 both ways, as they would come: Health Auto Export's uploads folded by the receiver
    into daily.json, its samples beside it in ecg/, and the same recordings in an uploaded Apple Health export saved by
    watch_store (only its ECGs). The app merges the two by key: five events, each with the phone's average heart rate and
    the export's lead and software version."""
    import hashlib, tempfile as tf
    from datetime import datetime, timedelta, timezone
    from server import ecg, watch_store
    from server.importers import apple_health
    rx = receiver()
    rx.DATA = tf.mkdtemp(prefix="rx-ecg-", dir=TMP)
    store, off = rx.Store(), timezone(timedelta(hours=-4))
    files = {"apple_health_export/export.xml": os.path.join(ECG_FIXTURE, "export.xml"),
             **{f"apple_health_export/electrocardiograms/{n}": os.path.join(ECG_FIXTURE, "electrocardiograms", n)
                for n in sorted(os.listdir(os.path.join(ECG_FIXTURE, "electrocardiograms")))}}
    w = apple_health.parse(files)["data"]["watch"]
    hae = lambda iso: datetime.fromisoformat(iso).astimezone(off).strftime("%Y-%m-%d %H:%M:%S %z")
    recs = [e for e in w["ecg"] if e["type"] == "ecg"]
    payload = {"data": {"ecg": [{"start": hae(e["start"]), "end": hae(e["end"]), "classification": e["classification"],
                                 "averageHeartRate": 65 + 16 * i, "samplingFrequency": e["rate"], "source": "Fictional Watch",
                                 "voltageMeasurements": [{"date": i, "voltage": v, "units": "mcV"} for i, v in enumerate(w["ecg_waves"][e["key"]]["samples"])]}
                                for i, e in enumerate(recs)],
                        "heartRateNotifications": [{"heartNotification": "irregular_rhythm_notification", "start": hae(e["start"]), "end": hae(e["end"]),
                                                    "source": {"name": "Fictional Watch"}} for e in w["ecg"] if e["type"] == "irregular_rhythm"]}}
    b = json.dumps(payload).encode()
    store.ingest(json.loads(b), hashlib.sha256(b).hexdigest())
    path = os.path.join(watch_dir, "daily.json")
    with open(path, encoding="utf-8") as f:
        daily = json.load(f)
    daily["ecg"] = store.summary()["ecg"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(daily, f)
    os.makedirs(os.path.join(watch_dir, "ecg"), exist_ok=True)
    for k, blob in store.db.execute("SELECT key, wave FROM ecg WHERE wave IS NOT NULL"):
        with open(os.path.join(watch_dir, "ecg", ecg.file_name(k)), "wb") as f:
            f.write(blob)   # as the receiver writes them, here unsealed like the demo's daily.json
    watch_store.save_import(db, 1, {"updated": w["updated"], "tz": w["tz"], "metrics": [], "exercise": [], "ecg": w["ecg"],
                                    "ecg_waves": w["ecg_waves"]})

def fitness_readings():
    """Fictional respiratory rate and VO2 max (#226), local times in New York, from August to mid-November 2026: a
    watch's breathing rate twice a night [(local time, breaths a minute)], and its VO2 max estimate after an evening walk
    every four days [(local time, mL/kg/min)]. Made by formula, so the same on every run."""
    from datetime import date, timedelta
    resp, vo2, d, i = [], [], date(2026, 8, 1), 0
    while d <= date(2026, 11, 14):
        for h in (2, 4):
            resp.append((f"{d}T0{h}:{(i * 13) % 50 + 5:02d}:00", round(14 + (i % 7) * 0.3 + (h - 3) * 0.4, 1)))
        if i % 4 == 1:
            vo2.append((f"{d}T18:{(i * 7) % 50 + 5:02d}:00", round(39.5 + i * 0.02 + (i % 3) * 0.3, 1)))
        d += timedelta(days=1)
        i += 1
    return resp, vo2


RESP_VISITS = [("2025-01-14T10:20:00-05:00", 16, ""), ("2025-06-10T10:05:00-04:00", 14, ""), ("2026-08-12T10:15:00-04:00", 22, "H"),
               ("2026-10-06T10:10:00-04:00", 15, "")]   # respiratory rates taken at visits (#226), one flagged by its report


def add_fitness_records(build, watch_dir):
    """Respiratory rate and VO2 max from both phone apps (#226), folded by the receiver itself into the demo's watch
    summary: Health Auto Export's until mid-September (each upload sent twice), then Life Dashboard Companion's; and
    respiratory rates taken at visits, in the records as a vitals series (RESP_VISITS)."""
    import hashlib, tempfile as tf
    from datetime import datetime
    from zoneinfo import ZoneInfo
    rx = receiver()
    rx.DATA = tf.mkdtemp(prefix="rx-", dir=TMP)
    store = rx.Store()
    ny, utc = ZoneInfo("America/New_York"), ZoneInfo("UTC")
    at = lambda local: datetime.fromisoformat(local).replace(tzinfo=ny)

    def send(p):
        b = json.dumps(p).encode()
        store.ingest(json.loads(b), hashlib.sha256(b).hexdigest())
    resp, vo2 = fitness_readings()
    hae = lambda rows: [{"date": at(t).strftime("%Y-%m-%d %H:%M:%S %z"), "qty": v, "source": "Fictional Watch"} for t, v in rows if t < "2026-09-15"]
    for _ in (1, 2):
        send({"data": {"metrics": [{"name": "respiratory_rate", "units": "count/min", "data": hae(resp)},
                                   {"name": "vo2_max", "units": "ml/(kg·min)", "data": hae(vo2)}]}})
    send({"source": "health_connect", "timezone": "America/New_York", "sequence": 1, "timestamp": "2026-11-15T12:00:00Z",
          "respiratory_rate": [{"uuid": f"fictional-r-{t}", "time": at(t).astimezone(utc).isoformat(), "rate": v, "source": "Fictional Watch"}
                               for t, v in resp if t >= "2026-09-15"],
          "vo2_max": [{"uuid": f"fictional-v-{t}", "time": at(t).astimezone(utc).isoformat(), "vo2_milliliters_per_minute_kilogram": v,
                       "source": "Fictional Watch"} for t, v in vo2 if t >= "2026-09-15"]})
    got = store.summary()
    path = os.path.join(watch_dir, "daily.json")
    with open(path, encoding="utf-8") as f:
        watch = json.load(f)
    watch["metrics"] += [m for m in got["metrics"] if m["key"] in ("resp", "vo2max")]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(watch, f)
    data_path = os.path.join(build, "data.json")
    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)
    pts = [[t, v, 12, 20, flag, str(v), "Lakeside Family Medicine"] for t, v, flag in RESP_VISITS]
    data["labs"].append({"name": "Respiratory rate", "key": "loinc:9279-1", "loinc": "9279-1", "unit": "/min", "panel": "Vitals", "n": len(pts), "pts": pts, "txt": []})
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f)


ZONE_BIRTH = "1990-09-10"   # #197: a birthday between the first workouts below and the later ones (35, then 36)


def add_workout_hr(watch_dir):
    """Workouts with heart rate from both phone apps (#197), folded by the receiver into the demo's watch summary (the
    demo's own workouts have none): through Health Auto Export, a run on 8 September with its minute-by-minute readings
    (5 min at 100 bpm, 10 at 120, 10 at 140, 5 at 160) and a ride on 12 September with only its average and highest;
    through Life Dashboard Companion, a walk on 20 October and a run on 10 November, with heart rate samples every 5
    seconds during them (15 min at 105 and 30 at 125; 10 min at 150, 15 at 165, 5 at 175). Local times in New York."""
    import hashlib, tempfile as tf
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    rx = receiver()
    rx.DATA = tf.mkdtemp(prefix="rx-", dir=TMP)
    store = rx.Store()
    ny, utc = ZoneInfo("America/New_York"), ZoneInfo("UTC")
    at = lambda local: datetime.fromisoformat(local).replace(tzinfo=ny)
    hae_t = lambda t: t.strftime("%Y-%m-%d %H:%M:%S %z")

    def send(p):
        b = json.dumps(p).encode()
        store.ingest(json.loads(b), hashlib.sha256(b).hexdigest())
    run = at("2026-09-08T07:00:00")
    rows = [{"date": hae_t(run + timedelta(minutes=i)), "Min": v - 3, "Avg": v, "Max": v + 2, "units": "count/min", "source": "Fictional Watch"}
            for i, v in enumerate([100] * 5 + [120] * 10 + [140] * 10 + [160] * 5)]
    ride = at("2026-09-12T18:00:00")
    send({"data": {"workouts": [
        {"id": "FICTIONAL-RUN", "name": "Outdoor Run", "start": hae_t(run), "end": hae_t(run + timedelta(minutes=30)), "duration": 1800,
         "heartRateData": rows, "heartRate": {"avg": {"qty": 130, "units": "bpm"}, "max": {"qty": 162, "units": "bpm"}}},
        {"id": "FICTIONAL-RIDE", "name": "Outdoor Cycle", "start": hae_t(ride), "end": hae_t(ride + timedelta(minutes=40)), "duration": 2400,
         "avgHeartRate": {"qty": 128, "units": "bpm"}, "maxHeartRate": {"qty": 158, "units": "bpm"}}]}})
    hc, samples = [], []
    for name, start, runs in (("walking", at("2026-10-20T07:00:00"), [(15, 105), (30, 125)]),
                              ("running", at("2026-11-10T18:00:00"), [(10, 150), (15, 165), (5, 175)])):
        bpm = [v for m, v in runs for _ in range(m * 12)]
        samples += [{"uuid": f"fictional-hr-{name}-{i}", "time": (start + timedelta(seconds=5 * i)).astimezone(utc).isoformat(), "bpm": v,
                     "source": "Fictional Watch"} for i, v in enumerate(bpm)]
        hc.append({"uuid": f"fictional-{name}", "start_time": start.astimezone(utc).isoformat(), "end_time": (start + timedelta(seconds=5 * len(bpm))).astimezone(utc).isoformat(),
                   "type": 79 if name == "walking" else 56, "name": name, "title": name.capitalize(), "duration_seconds": 5 * len(bpm), "source": "Fictional Watch"})
    send({"source": "health_connect", "timezone": "America/New_York", "sequence": 1, "timestamp": "2026-11-15T12:00:00Z", "heart_rate": samples, "exercise": hc})
    path = os.path.join(watch_dir, "daily.json")
    with open(path, encoding="utf-8") as f:
        watch = json.load(f)
    watch["exercise"] = sorted(watch["exercise"] + store.summary()["exercise"], key=lambda x: x["start"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(watch, f)


CHILD_BIRTH, CHILD_DUE = "2022-03-14", "2022-04-25"   # a fictional girl, born six weeks early (#196)
# her well-child visits: (day, weight kg, length or height cm, head circumference cm); the first before her due date
CHILD_VISITS = [("2022-03-16", 2.1, 44.0, 31.0), ("2022-05-02", 3.4, 50.0, 34.6), ("2022-06-13", 4.6, 54.5, 37.0),
                ("2022-08-15", 6.0, 60.0, 39.6), ("2022-10-17", 7.0, 64.0, 41.5), ("2023-01-16", 8.1, 68.5, 43.0),
                ("2023-04-17", 8.9, 72.5, 44.2), ("2023-07-17", 9.6, 76.0, 45.0), ("2023-10-16", 10.2, 79.0, 45.7),
                ("2024-03-18", 11.6, 85.5, None), ("2024-09-16", 12.8, 90.5, None), ("2025-03-17", 13.9, 95.0, None),
                ("2026-03-16", 15.9, 102.5, None), ("2026-09-14", 16.8, 106.0, None)]


def child_profile(db, owner):
    """A fictional child's profile (#196): her clinic's weights (clinicWeights, in lb as importers keep them), her length
    to 18 months and head circumference in the records as a clinic's vitals (in inches and cm, as C-CDA and FHIR bring
    them), and from 2 years her heights in About this person's history, with her birth date, sex and due date."""
    from server import db as dbm, profiles as prof_r, records as rec_r, settings as settings_r
    pid = prof_r.create_profile(db, "Robin (fictional child)", owner_id=owner)
    t = lambda d: d + "T09:30:00"
    infant = [v for v in CHILD_VISITS if v[3] is not None]
    with dbm.transaction(db):
        rec_r.store(db, pid, {"built": "2026-11-15", "source": "fictional",
                              "events": [{"id": i + 1, "t": t(v[0]), "k": "visit", "title": "Well-child visit", "sub": "Maple Street Pediatrics"}
                                         for i, v in enumerate(CHILD_VISITS)],
                              "details": {}, "costs": [], "upcoming": [],
                              "clinicWeights": [[t(d), round(kg / 0.45359237, 1), "Maple Street Pediatrics"] for d, kg, _, _ in CHILD_VISITS],
                              "labs": [{"name": "Height", "loinc": "8302-2", "unit": "in", "panel": "Vitals", "n": len(infant), "txt": [],
                                        "pts": [[t(d), round(cm / 2.54, 2), None, None, "", str(round(cm / 2.54, 2)), "Maple Street Pediatrics"] for d, _, cm, _ in infant]},
                                       {"name": "Head Occipital-frontal circumference", "loinc": "9843-4", "unit": "cm", "panel": "Vitals", "n": len(infant), "txt": [],
                                        "pts": [[t(d), hc, None, None, "", str(hc), "Maple Street Pediatrics"] for d, _, _, hc in infant]}]}, "upload", fmt="ccda")
    for key, value in (("person.birthDate", CHILD_BIRTH), ("person.sex", "female"), ("person.dueDate", CHILD_DUE),
                       ("person.heights", [{"date": d, "cm": cm, "unit": "cm"} for d, _, cm, hc in CHILD_VISITS if hc is None])):
        settings_r.put(db, key, value, "profile", pid)
    return pid


def start_app():
    """Demo build in a temporary data folder, one account owning it plus a second empty profile, then the API."""
    os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_SERVE_STATIC="1",
                      SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                      SANGUITY_WATCH_KEYS=os.path.join(TMP, "watch-keys", "receiver-secrets.json"))
    os.makedirs(os.path.join(TMP, "watch-keys"), exist_ok=True)
    subprocess.run([sys.executable, "-I", os.path.join(ROOT, "demo", "generate.py"), os.path.join(TMP, "build"), "--end", "2026-11-15",
                    "--watch", os.path.join(TMP, "watch")], check=True, stdout=subprocess.DEVNULL)
    add_html_record(os.path.join(TMP, "build"))
    add_med_records(os.path.join(TMP, "build"))
    add_range_records(os.path.join(TMP, "build"))
    add_glucose_watch(os.path.join(TMP, "watch"))
    add_immunization_records(os.path.join(TMP, "build"))   # #179
    add_vitals_records(os.path.join(TMP, "build"), os.path.join(TMP, "watch"))
    add_fitness_records(os.path.join(TMP, "build"), os.path.join(TMP, "watch"))   # #226
    add_workout_hr(os.path.join(TMP, "watch"))   # #197
    sys.path.insert(0, ROOT)
    import uvicorn
    from server import atrest, db as dbm, main, profiles, security
    dbm.init()
    db = dbm.connect()
    atrest.check_key(db)   # as the app does at start-up: the key, then the pepper passwords are hashed with
    atrest.load_pepper(db)
    add_cycle_watch(os.path.join(TMP, "watch"))   # #183 (sealed with the key just made): shown only once turned on
    profiles.bootstrap(db)
    add_ecg_watch(db, os.path.join(TMP, "watch"))   # #182, once profile 1 reads that watch file
    uid = db.execute("INSERT INTO users(username, pw_hash, is_admin, created) VALUES(?, ?, 1, 0)", (USER, security.hash_password(PW))).lastrowid
    db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, 1, 'owner')", (uid,))
    profiles.create_profile(db, "Second person", owner_id=uid)
    db.close()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    from server import config
    config.PUBLIC_ORIGINS.add(f"http://127.0.0.1:{port}")   # the return address connected sources sign in back to
    config.CONNECT_ALLOW_HTTP = True                         # the fake SMART on FHIR server is plain HTTP on this machine
    global SHL
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    import shl_server
    SHL = shl_server.Server().start()   # a local HTTPS host for a SMART Health Link (#94); server.shl may reach it
    SHL.trust()
    srv = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.05)
    return srv, f"http://127.0.0.1:{port}"


USER_MENU = {"#logout", "#settingsLink", "#sharingLink", "#theme"}


def menu_click(pg, item):
    """Click an item of the top bar's menus (nav.js): open its menu first, as a person would."""
    pg.click("#userMenuBtn" if item in USER_MENU else "#addMenuBtn")
    pg.click(item)


def stopped(e):
    """A stopped check's error, with the lines of Playwright's call log that say why (what covered an element,
    what it waited for), so a failure on CI's machines can be read without running it again."""
    lines = str(e).splitlines()
    why = [l.strip() for l in lines[1:] if any(w in l for w in ("intercepts pointer", "not visible", "not stable", "outside of the viewport",
                                                                  "waiting for", "element is", "retrying", "resolved to"))]
    seen = []
    for l in why:
        if l not in seen:
            seen.append(l)
    return lines[0] + ("" if not seen else " | " + " | ".join(seen[:6]))


# what phone_fits measures, in the page (#61)
PHONE_FITS_JS = r"""(() => {
  const vw = document.documentElement.clientWidth, out = [];
  const name = e => {
    const t = (e.getAttribute("aria-label") || e.textContent || e.value || e.getAttribute("placeholder") || "").replace(/\s+/g, " ").trim().slice(0, 30);
    return e.tagName.toLowerCase() + (e.id ? "#" + e.id : "") + (t ? ` "${t}"` : "");
  };
  const sw = document.documentElement.scrollWidth;
  if (sw > vw) {   // name the outermost boxes that stick out, leaving out what scrolls inside a box of its own or is fixed
    const free = e => { for (let a = e.parentElement; a && a !== document.body; a = a.parentElement) { const cs = getComputedStyle(a);
      if (cs.overflowX !== "visible" || cs.position === "fixed") return false; } return getComputedStyle(e).position !== "fixed"; };
    const wide = [...document.body.querySelectorAll("*")].filter(e => e.getBoundingClientRect().right > vw + 0.5 && !e.closest("svg *") && free(e))
      .map(e => [e, e.getBoundingClientRect().right]);
    const top = wide.filter(([e]) => !wide.some(([p]) => p !== e && p.contains(e))).slice(0, 3).map(([e, r]) => name(e) + " to " + Math.round(r));
    out.push(`the page scrolls sideways: ${sw} px wide (${top.join(", ")})`);
  }
  const CONTROLS = "a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [role=tab], [role=switch], [role=checkbox], [role=radio], [tabindex]:not([tabindex='-1'])";
  const layer = e => {   // a control's layer: a fixed or sticky bar, a popup or dialog, or the page itself
    for (let a = e; a && a !== document.body; a = a.parentElement) {
      const cs = getComputedStyle(a);
      if (cs.position === "fixed" || cs.position === "sticky" || a.matches("dialog, [popover]") || (cs.position === "absolute" && cs.zIndex !== "auto")) return a;
    }
    return document.body;
  };
  const items = [];
  for (const e of document.querySelectorAll(CONTROLS)) {
    if (e.closest("svg, [inert]") || !e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) continue;
    // the part that can be seen: cut to every box around it that scrolls or hides its overflow (a row of tabs that
    // scrolls sideways shows only some of them); none if it's clipped away (visually hidden)
    let rects = [...e.getClientRects()].map(r => ({l: r.left, r: r.right, t: r.top, b: r.bottom})), hidden = false;
    for (let a = e; a && a !== document.documentElement; a = a.parentElement) {
      const cs = getComputedStyle(a);
      if ((cs.clip && cs.clip !== "auto") || (cs.clipPath && cs.clipPath !== "none")) { hidden = true; break; }
      if (a !== e && (cs.overflowX !== "visible" || cs.overflowY !== "visible")) {
        const c = a.getBoundingClientRect(), x = cs.overflowX !== "visible", y = cs.overflowY !== "visible";
        rects = rects.map(r => ({l: x ? Math.max(r.l, c.left) : r.l, r: x ? Math.min(r.r, c.right) : r.r, t: y ? Math.max(r.t, c.top) : r.t, b: y ? Math.min(r.b, c.bottom) : r.b}));
      }
      if (cs.position === "fixed") break;
    }
    rects = rects.filter(r => r.r - r.l > 2 && r.b - r.t > 2);   // nothing left to see, or a 1 px visually-hidden box
    if (hidden || !rects.length) continue;
    const l = Math.min(...rects.map(r => r.l)), r = Math.max(...rects.map(r => r.r)), b = Math.max(...rects.map(r => r.b)), lay = layer(e);
    if (r <= 0 || b + scrollY <= 0 || (l >= vw && lay !== document.body)) continue;   // moved off the screen on purpose: a skip link, a closed drawer
    if (l < -0.5 || r > vw + 0.5) out.push(`${name(e)} is cut off at the screen's edge (${Math.round(l)} to ${Math.round(r)} px)`);
    items.push({e, rects, lay});
  }
  for (let i = 0; i < items.length; i++) for (let j = i + 1; j < items.length; j++) {
    const a = items[i], b = items[j];
    if (a.lay !== b.lay || a.e.contains(b.e) || b.e.contains(a.e)) continue;
    if (a.rects.some(p => b.rects.some(q => Math.min(p.r, q.r) - Math.max(p.l, q.l) > 2 && Math.min(p.b, q.b) - Math.max(p.t, q.t) > 2)))
      out.push(`${name(a.e)} overlaps ${name(b.e)}`);
  }
  return out;
})()"""


def phone_fits(page, label="", ignore=()):
    """What doesn't fit a phone-width page (#61), as a list of problems, empty when it fits. Set the viewport first
    (375 px wide; the sweep "every page at 375 px" uses 375 x 800) and open what is to be measured: a tab, a group, a
    menu or panel. It measures what is drawn now and changes nothing, so it also works under the real CSP.

    Reported:
    - the page scrolls sideways (the document is wider than the viewport), naming the outermost boxes that stick out;
    - a control cut off at the screen's edge: part of it on screen, part off;
    - two controls overlapping by more than 2 px each way, in the same layer.
    A control is a link, button, input, select, textarea, summary, an element with a widget role (button, tab,
    switch, checkbox, radio) or one in the tab order. SVG content (chart marks) is left out.

    Not reported, so these don't need ignoring: controls that aren't drawn (display:none, visibility:hidden,
    opacity 0, inside a closed <details> or an [inert] part); the part of a control scrolled out of sight inside a
    box that scrolls (the tab bar, the period presets, a sidebar's row of groups, a wide table's wrapper) or hidden
    by its overflow; a control clipped away or 2 px or less across (a visually-hidden skip link), or moved wholly
    off the screen to the left or top (a skip link until it's focused), or to the right inside a fixed panel (the
    closed drawer); a control inside another (a button in a summary); and controls in different layers — a fixed or
    sticky bar, a <dialog> or [popover], or an absolutely positioned box with a z-index (an open menu, the drawer) is
    meant to lie over the page.

    label goes before each problem ("Sources #upload: …"). ignore is a list of substrings: a problem containing one
    is dropped, for a known one owned by another issue (say which, beside it)."""
    found = [p for p in page.evaluate(PHONE_FITS_JS) if not any(i in p for i in ignore)]
    return [f"{label}: {p}" if label else p for p in found]


def answer_ask(page, yes=True, fill=None, timeout=10000):
    """Answer the app's own dialog (ask() in util.js, #210) as a person would, after the click that opens it: wait for
    it, type fill into its box if given, then click its action button (yes) or Cancel, and wait until it's gone.
    Returns [title, message], for checks on what it asked. (The app asks nothing in the browser's own pop-ups: main()
    counts any as a problem.)"""
    box = page.wait_for_selector("dialog.sg-ask[open]", timeout=timeout)
    said = box.evaluate("d => [d.querySelector('h2').textContent, d.querySelector('.sg-ask-msg').textContent]")
    if fill is not None:
        box.query_selector("input").fill(fill)
    box.query_selector(".sg-ask-acts button" + (":last-child" if yes else ":first-child")).click()
    box.wait_for_element_state("hidden", timeout=timeout)
    return said


# ---- axe-core (#202): Deque's accessibility engine, from the axe-playwright-python package (requirements-dev.txt), which
# bundles a pinned axe.min.js. Only that script is used: put into the page under test by page.evaluate, which the
# Content-Security-Policy doesn't apply to, so the strict pages are checked as they are; the app never loads it.
AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]   # WCAG 2.0, 2.1 and 2.2, levels A and AA
try:
    import axe_playwright_python
    with open(os.path.join(os.path.dirname(axe_playwright_python.__file__), "axe.min.js"), encoding="utf-8") as _f:
        AXE_JS = _f.read()
except (ImportError, OSError):
    AXE_JS = None
AXE_RUN = """tags => axe.run(document, {runOnly: {type: 'tag', values: tags}, resultTypes: ['violations']}).then(r => r.violations.map(v => ({
  id: v.id, impact: v.impact, n: v.nodes.length, at: v.nodes.slice(0, 3).map(n => n.target.join(' ')),
  why: (v.nodes[0].failureSummary || '').split('\\n').slice(1, 2).join('').trim().slice(0, 160)})))"""


CLEAR_OF_BAR_JS = r"""(() => {
  const bar = document.querySelector('header.topbar'); if (!bar) return 0;
  const b = bar.getBoundingClientRect().bottom; let moved = 0;
  for (let i = 0; i < 4 && scrollY > 0; i++) {
    const cut = [...document.querySelectorAll('main :is(a[href], button, input, select, textarea, summary, [role=button])')].filter(e => e.checkVisibility())
      .map(e => e.getBoundingClientRect()).filter(r => r.height > 0 && r.top < b && r.bottom > b);
    if (!cut.length) break;
    const d = Math.max(...cut.map(r => b - r.top)) + 4;
    scrollBy(0, -d); moved += d;
  }
  return moved;
})()"""


def clear_of_bar(page):
    """Scroll up just enough that no control lies half under the sticky top bar, before axe looks at a part scrolled
    into view: axe counts a control the bar half covers as too small a target (2.5.8), so where the scroll happened to
    stop would decide the check. Returns how far it scrolled."""
    return page.evaluate(CLEAR_OF_BAR_JS)


def axe_check(page, label):
    """axe-core's WCAG A and AA rules on the page as it is drawn now (open what is to be checked first: a tab, a menu, a
    dialog). Returns (serious, other): the serious and critical findings, which fail a check, and the moderate and minor
    ones, which are listed; each "label: rule (impact) ×n at selector, … — what's wrong"."""
    if AXE_JS is None:
        return [f"{label}: axe-core isn't installed (pip install --require-hashes -r requirements-dev.txt)"], []
    if not page.evaluate("typeof axe === 'object' && typeof axe.run === 'function'"):
        page.evaluate(AXE_JS)
    serious, other = [], []
    for v in page.evaluate(AXE_RUN, AXE_TAGS):
        (serious if v["impact"] in ("serious", "critical") else other).append(
            f"{label}: {v['id']} ({v['impact']}) ×{v['n']} at {', '.join(v['at'])} — {v['why']}")
    return serious, other


# a page's heading outline (#331): its headings, drawn or there for screen readers only (.vh), as "h2 Key labs", and
# those that skip a level (the first heading counts as following an h0)
OUTLINE_JS = r"""() => {
  const hs = [...document.querySelectorAll('h1, h2, h3, h4, h5, h6, [role=heading]')].filter(h => h.getClientRects().length && !h.closest('[aria-hidden=true], [inert]'));
  const o = hs.map(h => [+(h.getAttribute('aria-level') || h.tagName[1]), h.textContent.trim().replace(/\s+/g, ' ').slice(0, 40)]);
  return {outline: o.map(([l, t]) => `h${l} ${t}`), h1: o.filter(h => h[0] === 1).length,
    skipped: o.filter((h, i) => h[0] > (i ? o[i - 1][0] : 0) + 1).map(([l, t]) => `h${l} ${t}`)};
}"""
AXE_HEADINGS = """() => axe.run(document, {runOnly: {type: 'rule', values: ['heading-order', 'page-has-heading-one', 'empty-heading']}})
  .then(r => r.violations.map(v => v.id + ' at ' + v.nodes.slice(0, 3).map(n => n.target.join(' ')).join(', ')))"""


def heading_outline(page, label):
    """The page's heading outline as it is drawn now, as one line ("label: h1 Costs > h2 …"), and what's wrong with it
    (#331): not exactly one h1, a level skipped, or axe-core's heading rules (heading-order, page-has-heading-one,
    empty-heading: best practice, so not among axe_check's WCAG rules) failing."""
    o = page.evaluate(OUTLINE_JS)
    bad = ([f"{label}: {o['h1']} h1s"] if o["h1"] != 1 else []) + [f"{label}: {h} skips a level" for h in o["skipped"]]
    if AXE_JS is not None:
        if not page.evaluate("typeof axe === 'object' && typeof axe.run === 'function'"):
            page.evaluate(AXE_JS)
        bad += [f"{label}: axe {v}" for v in page.evaluate(AXE_HEADINGS)]
    return f"{label}: " + " > ".join(o["outline"]), bad


# #320: on a phone an account page's table (.acct .tbl) hides its header row, and each cell shows its column's name
# before its value instead (labelCells in util.js, td::before in app.css). Of each such table drawn: where it is, whether
# its headings are hidden, its rows shown, and its cells labelled; and what's wrong: a cell with a value (text or a
# control) under a named column that isn't labelled with that name, or a label where there should be none (the headings
# shown, an empty heading over a row's buttons, a cell spanning columns or an empty one)
CELL_LABELS_JS = r"""() => {
  const out = {tables: [], problems: []};
  for (const t of document.querySelectorAll('main.acct table.tbl')) {
    if (!t.checkVisibility() || !t.tHead) continue;
    const where = ((t.closest('[id]') || {}).id || '') + ' table.' + t.className.trim().split(/\s+/).join('.');
    const hidden = getComputedStyle(t.tHead).display === 'none', heads = [...t.tHead.rows[0].cells].map(h => h.textContent.trim());
    let rows = 0, cells = 0;
    for (const r of [...t.tBodies].flatMap(b => [...b.rows]).filter(r => r.checkVisibility())) {
      rows++;
      let col = 0;
      for (const c of r.cells) {
        const head = c.colSpan === 1 && c.tagName === 'TD' ? heads[col] : '';
        col += c.colSpan;
        const value = c.textContent.trim() || (c.querySelector('input, select, textarea, button, img, svg') ? '(a control)' : '');
        const shows = getComputedStyle(c, '::before').content, want = hidden && head && value ? JSON.stringify(head) : 'none';
        if (shows === want || (want === 'none' && shows === 'normal')) { if (want !== 'none') cells++; }
        else out.problems.push(`${where}: "${value.slice(0, 30)}" under "${head}" shows ${shows}, not ${want}`);
      }
    }
    out.tables.push([where, hidden, rows, cells]);
  }
  return out;
}"""


def cell_labels(page, where):
    """#320: the account page's tables drawn now, each [where, headings hidden, rows, cells labelled], and what's wrong
    (CELL_LABELS_JS), with any table whose headings are hidden but still given to screen readers as column headers:
    there the label drawn before each value is the one a screen reader reads, so it isn't heard twice."""
    got = page.evaluate(CELL_LABELS_JS)
    bad = [f"{where}: {p}" for p in got["problems"]]
    tables = page.locator("main.acct table.tbl")
    for i in range(tables.count()):
        t = tables.nth(i)
        if t.is_visible() and t.evaluate("t => !!t.tHead && getComputedStyle(t.tHead).display === 'none'") and "columnheader" in t.aria_snapshot():
            bad.append(f"{where}: table.{t.get_attribute('class')} gives its hidden headings to screen readers too")
    return got["tables"], bad


# a stop of Tab (#202): what has focus, whether it looks any different from when it hasn't (2.4.7), and whether all of
# it is under a fixed or sticky bar (2.4.11). Focus is taken away and given back to compare, which keeps :focus-visible.
FOCUS_STOP_JS = r"""(() => {
  const a = document.activeElement; if (!a || a === document.body || a === document.documentElement) return null;
  const t = (a.getAttribute('aria-label') || a.textContent || a.value || '').replace(/\s+/g, ' ').trim().slice(0, 30);
  const name = a.tagName.toLowerCase() + (a.id ? '#' + a.id : '') + (t ? ` "${t}"` : '');
  const again = window.__fw.has(a); window.__fw.add(a);
  const look = () => { const c = getComputedStyle(a); return [c.outlineStyle !== 'none' && parseFloat(c.outlineWidth) > 0 ? c.outlineStyle + c.outlineWidth + c.outlineColor : '',
    c.boxShadow, c.backgroundColor, c.borderColor, c.color, c.textDecorationLine, c.fill, c.stroke, c.strokeWidth, c.transform].join('|'); };
  const on = look(), r = a.getBoundingClientRect();
  a.blur(); const still = a.matches(':focus-within'), off = look(); a.focus({preventScroll: true});   // still: a date field's calendar button keeps focus
  const bar = e => { for (let p = e; p && p !== document.body; p = p.parentElement) { const cs = getComputedStyle(p); if (cs.position === 'fixed' || cs.position === 'sticky') return true; } return false; };
  const covered = r.width > 4 && r.height > 4 && [[r.left + 2, r.top + 2], [r.right - 2, r.top + 2], [r.left + 2, r.bottom - 2], [r.right - 2, r.bottom - 2], [(r.left + r.right) / 2, (r.top + r.bottom) / 2]]
    .every(([x, y]) => { const e = document.elementFromPoint(x, y); return e && e !== a && !a.contains(e) && !e.contains(a) && !bar(a) && bar(e); });
  return {name, again, shown: still ? on.split('|')[0] !== '' : on !== off, covered};
})()"""


def ring_walk(page, label, stops=12):
    """#310: Tab through a page from its start in light and dark; each stop's outline should be the app's one ring, the
    accent at 2 px (sign-in and setup had the browser's own on their buttons). The problems, and the stops seen."""
    bad, seen = [], []
    for theme in ("light", "dark"):
        page.emulate_media(color_scheme=theme)
        # from the page's start: a focused stand-in there, gone (a blur alone leaves Tab going on from what had focus)
        page.evaluate("(() => { const s = document.createElement('span'); s.tabIndex = -1; document.body.prepend(s); s.focus(); s.remove(); scrollTo(0, 0); })()")
        for _ in range(stops):
            page.keyboard.press("Tab")
            stop = page.evaluate("""(() => { const a = document.activeElement; if (!a || a === document.body) return null;
              const i = document.createElement('i'); i.style.color = 'var(--accent)'; document.body.append(i);
              const accent = getComputedStyle(i).color, c = getComputedStyle(a); i.remove();
              return [a.id || a.tagName.toLowerCase(), c.outlineStyle, c.outlineWidth, c.outlineColor, accent]; })()""")
            if not stop or f"{theme} {stop[0]}" in seen:
                break
            seen.append(f"{theme} {stop[0]}")
            if stop[1:4] != ["solid", "2px", stop[4]]:
                bad.append(f"{label}, {theme}: #{stop[0]} {stop[1]} {stop[2]} {stop[3]} (the accent is {stop[4]})")
    page.emulate_media(color_scheme="no-preference")
    return bad, seen


# a table's wrapper that scrolls, sideways or down (#335): each drawn now (of those the selector given matches, every
# .tablewrap by default), as [its name, tabindex, role, the id it's in]. One that scrolls
# must take focus and have a name, so a keyboard can reach what's out of sight and a screen reader says what it is
SCROLL_TABLES_JS = r"""sel => [...document.querySelectorAll(sel || '.tablewrap')]
  .filter(w => w.checkVisibility() && (w.scrollWidth > w.clientWidth + 1 || w.scrollHeight > w.clientHeight + 1))
  .map(w => [w.getAttribute('aria-label') || (w.getAttribute('aria-labelledby') && document.getElementById(w.getAttribute('aria-labelledby'))?.textContent) || '',
             w.tabIndex, w.getAttribute('role'), (w.closest('[id]') || {}).id || ''])"""


# a field's look (#332), in light and dark (the theme set on the page, then put back): its background, edge and corner.
# Sign-in's and setup's fields are compared with one on Account
FIELD_LOOK_JS = r"""sel => Object.fromEntries(['light', 'dark'].map(t => {
  const r = document.documentElement, was = r.dataset.theme; r.dataset.theme = t;
  const c = getComputedStyle(document.querySelector(sel));
  const look = [c.backgroundColor, `${c.borderTopWidth} ${c.borderTopStyle} ${c.borderTopColor}`, c.borderTopLeftRadius];
  if (was) r.dataset.theme = was; else delete r.dataset.theme;
  return [t, look]; }))"""


def focus_walk(page, label, back=False, stops=60):
    """Tab (or Shift+Tab from the page's end) through up to `stops` controls; the problems found, each "label: control …".
    A stop that shows no change when focused, or lies wholly under a sticky or fixed bar, is a problem."""
    if back:
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight); (f => { if (f) { f.tabIndex = -1; f.focus(); } })(document.querySelector('.pagefoot, footer'))")
    else:
        page.evaluate("document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0)")
    page.evaluate("window.__fw = new WeakSet()")
    out = []
    for _ in range(stops):
        page.keyboard.press("Shift+Tab" if back else "Tab")
        s = page.evaluate(FOCUS_STOP_JS)
        if not s:
            continue
        if s["again"]:   # round to the start
            break
        if not s["shown"]:
            out.append(f"{label}: {s['name']} shows no sign of focus")
        if s["covered"]:
            out.append(f"{label}: {s['name']} has focus under a sticky bar")
    return out


# the labels on a time chart (#312): of each chart matching the selector (an argument) that is drawn, its aria-label,
# whether it has milestones, and its milestone labels ([text, the full names and dates in its title]); how many
# procedures are named; and what's wrong: a label running past its plot's left or right edge, or a procedure's or
# imaging's mark (● ■) on a label's text
CHART_LABELS_JS = r"""sel => {
  const out = {charts: [], ev: 0, problems: []};
  for (const svg of document.querySelectorAll(sel)) {
    const g = svg._geom || (svg.id === 'tl' ? tlScale(svg.viewBox.baseVal.width) : null);
    if (!g || !svg.checkVisibility() || !svg.getBoundingClientRect().width) continue;
    const said = svg.getAttribute('aria-label') || '', name = said.split(/[:,]/)[0], heads = [...svg.querySelectorAll('.evhead')].map(h => h.getBBox());
    const c = {said, marked: !!svg.querySelector('.msmark'), ms: []}; out.charts.push(c);
    for (const t of svg.querySelectorAll('.mslabel, .evlabel')) {
      const b = t.getBBox(), text = t.firstChild.textContent;
      if (t.classList.contains('mslabel')) c.ms.push([text, t.querySelector('title')?.textContent || '']); else out.ev++;
      if (b.x < g.L - .5 || b.x + b.width > g.R + .5)
        out.problems.push(`${name}: "${text}" runs past the plot (${Math.round(b.x)} to ${Math.round(b.x + b.width)}, plot ${Math.round(g.L)} to ${Math.round(g.R)})`);
      if (heads.some(h => h.x < b.x + b.width && h.x + h.width > b.x && h.y < b.y + b.height && h.y + h.height > b.y))
        out.problems.push(`${name}: a mark lies on "${text}"`);
    }
  }
  return out;
}"""

# what a chart draws in the accent (#334): of each drawn chart matching the selector (an argument), every element whose
# computed fill or stroke is the accent's colour (read from an element that uses --accent), and how many milestone rules,
# milestone labels and cursors were looked at, so the check can't pass on charts with none. A cursor is hidden until a
# point is hovered or picked by key, and styled all the same, so it's read hidden
CHART_ACCENT_JS = r"""sel => {
  const i = document.createElement('i'); i.style.color = 'var(--accent)'; document.body.append(i);
  const accent = getComputedStyle(i).color; i.remove();
  const out = {accent, bad: [], rules: 0, labels: 0, cursors: 0};
  for (const svg of document.querySelectorAll(sel)) {
    if (!svg.checkVisibility() || !svg.getBoundingClientRect().width) continue;
    out.rules += svg.querySelectorAll('.msmark').length; out.labels += svg.querySelectorAll('.mslabel').length;
    out.cursors += svg.querySelectorAll('.cross, .ecg-cursor').length;
    for (const e of svg.querySelectorAll('*')) {
      const c = getComputedStyle(e);
      if (c.fill === accent || c.stroke === accent)
        out.bad.push(`${(svg.getAttribute('aria-label') || svg.id || sel).split(/[:,]/)[0]}: <${e.tagName} class="${e.getAttribute('class') || ''}"> in the accent`);
    }
  }
  return out;
}"""


# a tile's change line and a stat's note, as laid out (#330): each one that wraps, as its lines, when it ends in a short
# fragment (its last line under 40% of its longest, such as "▼ −0.03 since Aug 18," over "2026") or breaks a date; and
# each row of tiles whose sparklines don't start at the same height ([the tiles' container, the tops])
TILE_LINES_JS = r"""() => {
  const lines = e => {   // {text, left, right} per line the element's words are laid out on
    const out = [], walk = document.createTreeWalker(e, NodeFilter.SHOW_TEXT);
    for (let n; (n = walk.nextNode());) for (const m of n.data.matchAll(/\S+/g)) {
      const r = document.createRange(); r.setStart(n, m.index); r.setEnd(n, m.index + m[0].length);
      const rs = [...r.getClientRects()], last = out.at(-1); if (!rs.length) continue;
      if (last && Math.abs(last.top - rs[0].top) < 4) { last.text += " " + m[0]; last.r = rs.at(-1).right; }
      else out.push({top: rs[0].top, text: m[0], l: rs[0].left, r: rs.at(-1).right});
    }
    return out;
  };
  const date = /(\p{L}{3,}\.? \d{1,2},|\d{1,2} \p{L}{3,}\.?|\p{L}{3,}\.?)$/u, year = /^(\d{1,2},? )?\d{4}\b/;
  const out = {n: 0, bad: [], uneven: []};
  for (const e of document.querySelectorAll('.tile .delta, .stat .l + .l')) {
    if (!e.checkVisibility() || !e.textContent.trim()) continue;
    out.n++;
    const ls = lines(e), w = ls.map(l => l.r - l.l);
    if (ls.length > 1 && (w.at(-1) < .4 * Math.max(...w) || ls.some((l, i) => i && year.test(l.text) && date.test(ls[i - 1].text))))
      out.bad.push(ls.map(l => l.text));
  }
  for (const g of document.querySelectorAll('.tiles')) {
    if (!g.checkVisibility()) continue;
    const rows = {};
    for (const t of g.children) {
      const s = t.querySelector('svg'); if (!s) continue;
      (rows[Math.round(t.getBoundingClientRect().top)] ||= []).push(Math.round(s.getBoundingClientRect().top));
    }
    for (const v of Object.values(rows)) if (Math.max(...v) - Math.min(...v) > 1) out.uneven.push([g.parentElement.id || g.parentElement.className, v]);
  }
  return out;
}"""


# the swap to the fonts (#329): the cards and tiles a page draws, by name, with their heights
SWAP_BOXES_JS = """() => Object.fromEntries([...document.querySelectorAll('main .card, main .tile, main .stat, main .ov-mini, .sets .set')]
  .filter(e => e.checkVisibility()).map((e, i) => [`${e.className.split(' ')[0]} ${i} ${(e.querySelector('h1,h2,h3,.lbl,.l')?.textContent || '').trim().slice(0, 40)}`,
    Math.round(e.getBoundingClientRect().height * 10) / 10]))"""
SWAP_FONTS_JS = "() => [...document.fonts].filter(f => f.status === 'loaded').map(f => f.family).filter((f, i, a) => a.indexOf(f) === i).sort()"


def font_swap(browser, state, url, width):
    """A page loaded with www/fonts/ held back, so it's drawn in base.css's fallbacks, then the fonts let through (#329).
    Returns {box: (height in the fallback, height in the font)} for each card or tile whose height changed, how many
    boxes there were, the layout shift after the fonts were let through, and the font families loaded before and
    after. No service worker, so the fonts come from the network."""
    ctx = browser.new_context(storage_state=state, service_workers="block", viewport={"width": width, "height": 900})
    held = []
    ctx.route("**/fonts/**", lambda route: held.append(route))   # answered once the page is drawn
    pg = ctx.new_page()
    pg.goto(url, wait_until="domcontentloaded")   # not "load": that waits for the fonts
    for _ in range(100):
        if pg.evaluate("performance.getEntriesByName('sanguity:ready').length > 0 || !!document.querySelector('.sets .set')"):
            break
        pg.wait_for_timeout(100)
    pg.wait_for_timeout(1000)
    before, fonts_before = pg.evaluate(SWAP_BOXES_JS), pg.evaluate(SWAP_FONTS_JS)
    pg.evaluate("""() => { window.__swapShift = 0; new PerformanceObserver(l => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__swapShift += e.value; })
      .observe({type: 'layout-shift'}); }""")
    for r in held:
        r.continue_()
    ctx.unroute("**/fonts/**")
    pg.evaluate("document.fonts.ready.then(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))))")
    pg.wait_for_timeout(500)
    after, fonts_after, shift = pg.evaluate(SWAP_BOXES_JS), pg.evaluate(SWAP_FONTS_JS), pg.evaluate("window.__swapShift")
    ctx.close()
    changed = {k: (h, after[k]) for k, h in before.items() if k in after and abs(after[k] - h) > 0.5}
    return changed, len(before), round(shift, 4), fonts_before, fonts_after


def main():
    srv, base = start_app()
    problems, passed = [], []
    acct_field = {}   # #332: a field on Account, light and dark, for sign-in's and setup's to match

    def check(name, ok, detail=""):
        (passed if ok else problems).append(f"{name}{': ' + str(detail) if detail != '' else ''}")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        # the app asks in its own dialog (#210): a browser pop-up (confirm, alert, prompt) on any page is a problem,
        # dismissed, never quietly accepted. Every page made from here on watches for one.
        def native_dialog(d):
            problems.append(f"a browser {d.type}() pop-up (#210: use ask() or sayIn): {d.message[:120]}")
            d.dismiss()
        new_page, new_context = browser.new_page, browser.new_context
        def watched_page(*a, **k):
            pg = new_page(*a, **k)
            pg.on("dialog", native_dialog)
            return pg
        def watched_context(*a, **k):
            c = new_context(*a, **k)
            c.on("page", lambda pg: pg.on("dialog", native_dialog))
            return c
        browser.new_page, browser.new_context = watched_page, watched_context
        # the pages under their real Content-Security-Policy (no inline or eval'd script): every page's own scripts
        # run, with no violation reported. Only page.evaluate here, which the CSP doesn't apply to.
        strict = browser.new_page(viewport={"width": 1400, "height": 900})
        # no page contacts another host (#59): only the app itself, and the fake vendor's sign-in page while a test
        # signs in there (the browser visits a vendor's sign-in; the server never contacts anything unlisted)
        allowed = {base}
        origin = lambda u: "/".join(u.split("/")[:3])
        elsewhere = lambda r: (not r.url.startswith(("data:", "blob:", "about:")) and origin(r.url) not in allowed
                               and problems.append(f"request to another host: {r.url}"))
        strict.on("request", elsewhere)
        csp_problems = []
        strict.on("pageerror", lambda e: csp_problems.append(f"script error: {e}"))
        strict.on("console", lambda m: m.type == "error" and "Content Security Policy" in m.text and csp_problems.append(m.text))
        r = strict.goto(base + "/setup.html")
        check("pages are sent with a CSP that allows no inline script", "script-src 'self';" in (r.headers.get("content-security-policy") or ""))
        strict.wait_for_url("**/login.html", timeout=10000)   # setup.js ran: setup is done, so it sends you to sign in
        strict.evaluate("localStorage.setItem('ht-theme', 'dark')")
        strict.reload()
        check("under the CSP: theme.js runs", strict.evaluate("document.documentElement.dataset.theme") == "dark")
        # login.js rewrites the note from /api/setup (the page's own text breaks the line, so only its version matches)
        strict.wait_for_function("document.getElementById('stay').textContent.includes('a passkey, otherwise')", timeout=5000)
        stay = strict.evaluate("document.getElementById('stay').textContent")
        check("sign-in page says how long a sign-in lasts", "log out: up to 30 days with two-factor sign-in or a passkey, otherwise 12 hours." in stay, stay)
        strict.fill("#u", USER)
        strict.fill("#p", PW)
        strict.click("#b")
        for _ in range(100):   # login.js signs in, then the app loads the records
            if strict.evaluate("location.pathname.endsWith('/') && typeof EV !== 'undefined' && EV.length > 0"):
                break
            strict.wait_for_timeout(200)
        check("under the CSP: login.js signs in and the app starts", strict.evaluate("typeof EV !== 'undefined' && EV.length > 0"))
        # the fonts (#253): Inter loads from www/fonts/ under the real CSP (default-src 'self' covers font-src; a
        # violation would be in csp_problems), and the page's text is in it, in light and dark. No font is preloaded
        # (#328: on a slow connection a preload took bandwidth from the stylesheets and held up the first paint), and
        # Source Serif is the weight-only file, the one the Overview's titles and values ask for
        fonts, theme_before = {}, strict.evaluate("document.documentElement.dataset.theme || null")
        for theme in ("light", "dark"):
            strict.evaluate(f"document.documentElement.dataset.theme = '{theme}'")
            fonts[theme] = strict.evaluate("""document.fonts.ready.then(() => [[...document.fonts].some(f => f.family === 'Inter' && f.status === 'loaded'),
              document.fonts.check('14px Inter'), getComputedStyle(document.body).fontFamily.split(',')[0].trim(),
              getComputedStyle(document.documentElement).colorScheme])""")
        preload = strict.evaluate("[...document.querySelectorAll('link[rel=preload]')].map(l => new URL(l.href).pathname)")
        fetched = strict.evaluate("[...performance.getEntriesByType('resource')].map(e => new URL(e.name).pathname).filter(p => p.startsWith('/fonts/')).sort()")
        check("fonts (#253, #328): Inter and Source Serif load from the app itself under the CSP, nothing is preloaded, and the page's text is in Inter, in light and dark",
              all(f == [True, True, "Inter", theme] for theme, f in fonts.items()) and preload == []
              and fetched == ["/fonts/inter-v4.001-latin.woff2", "/fonts/source-serif-4-v4.004-wght-latin.woff2"], (fonts, preload, fetched))
        # the swap moves nothing (#329): each page drawn with the fonts held back is drawn in the fallbacks (base.css),
        # and when the fonts arrive the layout shift is under 0.005; at 1400 px no card or tile changes height, at 375 px
        # none changes by more than a line: there a few lines end within a pixel or two of their box, where Inter's and
        # Arial's widths for that text differ by under 1% either way, which no one size-adjust removes
        state = strict.context.storage_state()
        for name, path in (("Overview", "/#overview"), ("Settings", "/settings.html"), ("Costs", "/#costs"), ("Body and activity", "/#health")):
            for width in (1400, 375):
                changed, boxes, shift, fonts_before, fonts_after = font_swap(browser, state, base + path, width)
                drawn = (boxes > 3 and "Inter fallback" in fonts_before and "Inter" not in fonts_before
                         and "Source Serif 4" not in fonts_before and {"Inter", "Source Serif 4"} <= set(fonts_after))
                worst = max((abs(b - a) for a, b in changed.values()), default=0)
                check(f"fonts (#329): {name} at {width} px, drawn in the fallbacks and then the fonts, moves less than 0.005 and "
                      + ("no card or tile changes height" if width > 640 else "no card or tile by more than a line"),
                      drawn and shift < 0.005 and (not changed if width > 640 else worst <= 24),
                      [boxes, shift, changed, fonts_before, fonts_after] if not drawn else [shift, changed])
        # the colours (#254): each token is one light-dark() pair, so the system's dark (no theme chosen) and a chosen
        # dark under a light system must give the same colours, likewise light; and the two themes differ
        with open(os.path.join(ROOT, "www", "css", "tokens.css"), encoding="utf-8") as f:
            names = sorted(set(re.findall(r"(--(?!font-)[\w-]+)\s*:", re.sub(r"/\*.*?\*/", "", f.read(), flags=re.S))))
        read_tokens = """names => Object.fromEntries(names.map(n => { const s = document.createElement('span'); s.style.color = `var(${n})`;
          document.body.append(s); const c = getComputedStyle(s).color; s.remove(); return [n, c]; }))"""
        resolved = {}
        for state, system, chosen in (("light", "light", None), ("dark", "dark", None), ("chosen light", "dark", "light"), ("chosen dark", "light", "dark")):
            strict.emulate_media(color_scheme=system)
            strict.evaluate("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", chosen)
            resolved[state] = strict.evaluate(read_tokens, names)
        strict.emulate_media(color_scheme="light")
        differ = [n for a, b in (("light", "chosen light"), ("dark", "chosen dark")) for n in names if resolved[a][n] != resolved[b][n]]
        check("colours (#254): every token is the same in the system's light or dark as in the chosen theme, and the themes differ",
              not differ and len(names) > 40 and all(resolved["light"][n] != resolved["dark"][n] for n in ("--page", "--surface", "--ink", "--accent")),
              differ[:8] or [resolved["light"]["--page"], resolved["dark"]["--page"]])
        strict.evaluate("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
        # the Sources page (#166): its old addresses open their sections, each part drawn by its own script
        strict.goto(base + "/import.html")
        strict.wait_for_url("**/sources.html#upload", timeout=10000)
        strict.wait_for_selector("#importer", state="visible", timeout=10000)
        check("under the CSP: the Sources page's scripts run, import.html opening Upload files",
              strict.evaluate("document.querySelectorAll('#profile option').length") == 2 and strict.evaluate(
                  "[...document.querySelectorAll('#srcNav a')].map(a => a.textContent)") == ["Overview", "Upload files", "Type records in",
                  "Paste a Health Link", "Portals and health plans", "Phone and watch", "Import history"])
        for sel in ("#srcList h3", "#historyList li", "#watch .w-addr", "#connList p"):
            strict.wait_for_selector(sel, state="attached", timeout=10000)
        check("…every section drawn, only the group's shown", strict.evaluate(
              "['importer', 'typeIn', 'healthLink', 'mine', 'watch', 'history', 'srcOverview'].filter(id => getComputedStyle(document.getElementById(id)).display !== 'none')") == ["importer"])
        null_cards = "[...document.querySelectorAll('main .card')].filter(c => /\\bnull\\b/.test(c.textContent)).map(c => c.id || c.className)"
        check("no Sources card shows a stray “null”", strict.evaluate(null_cards) == [], strict.evaluate(null_cards))
        strict.goto(base + "/settings.html")
        strict.wait_for_selector("#sec-overview .set-labs li", timeout=10000)
        check("under the CSP: settings.js runs", strict.evaluate("document.querySelectorAll('#sections > section').length") == 11)   # Visit summary (#186)
        check("no Settings card shows a stray “null” (#175)", strict.evaluate(null_cards) == [], strict.evaluate(null_cards))
        check("…and Settings has no Watch and scale any more (#166)", not strict.evaluate("!!document.getElementById('watch')")
              and "Watch and scale" not in strict.evaluate("document.getElementById('setNav').textContent"))
        strict.goto(base + "/settings.html?profile=1#watch")   # its old address: Sources → Phone and watch
        strict.wait_for_url("**/sources.html?profile=1#phone", timeout=10000)
        strict.wait_for_selector("#watch h2", timeout=10000)
        check("under the CSP: the watch card shows the upload address, on Sources", strict.evaluate("document.querySelector('#watch .w-addr').textContent").endswith(":8731/ingest")
              and strict.evaluate("document.querySelector('#srcNav a[aria-current=page]').dataset.group") == "phone")
        # the Admin page (#167): its sidebar of groups, each part drawn by its own script
        strict.goto(base + "/admin.html")
        for sel in ("#userList table", "#sec-sync .set", "#sourceList", "#dirSources ul", "#labmapList", "#outsideBody h3", "#acctLog tbody tr", "#bkStatus .kv"):
            strict.wait_for_selector(sel, state="attached", timeout=10000)
        groups = strict.evaluate("[...document.querySelectorAll('#admNav a')].map(a => a.textContent)")
        check("under the CSP: the Admin page's scripts run, with a sidebar of its groups", groups ==
              ["Accounts", "Backups", "Connected-source apps", "Provider directory", "Lab name matches", "Outside connections", "Install activity"], groups)
        strict.wait_for_selector("#adminLink:not([hidden])", state="attached", timeout=10000)
        check("…and the menu offers Admin to an admin", strict.evaluate("document.getElementById('adminLink').getAttribute('href')") == "admin.html")
        check("no Admin card shows a stray “null”", strict.evaluate(null_cards) == [], strict.evaluate(null_cards))
        # the Sharing page (#168): its sidebar, each part drawn by its own script
        strict.goto(base + "/sharing.html#summary")   # the visit summary (#48)
        strict.wait_for_selector("#build:not([hidden]) #sections input", timeout=10000)
        check("under the CSP: summary.js runs", strict.evaluate("document.querySelectorAll('#sections input:checked').length") == 5)   # #225: measures unticked
        strict.wait_for_selector("#calendar:not([hidden])", state="attached", timeout=10000)   # in its own group, so not visible
        strict.wait_for_selector("#memberList tbody tr", state="attached", timeout=10000)
        strict.click("#shrNav a[data-group=activity]")   # drawn when its group is opened
        strict.wait_for_selector("#actBody .activity tbody tr", timeout=10000)
        groups = strict.evaluate("[...document.querySelectorAll('#shrNav a')].map(a => a.textContent)")
        check("under the CSP: the Sharing page's scripts run, with a sidebar of its groups, Profile and About this person set apart (#194)", groups ==
              ["People", "Share links", "Calendar feed", "Visit summary", "Export everything", "Activity", "About this person", "Profile"]
              and strict.evaluate("document.querySelector('#shrNav a[data-group=about]').closest('li').previousElementSibling.classList.contains('set-nav-part')"), groups)
        check("no Sharing card shows a stray “null”", strict.evaluate(null_cards) == [], strict.evaluate(null_cards))
        # ---- shorter help text (#176): a card leads with a short line, and the rest of its explanation is behind one
        # "More about this" toggle, the same on every page: closed at first, still in the page when closed, opened and
        # closed by keyboard, readable in dark mode (this page's theme) and at 375 px. What someone needs to decide
        # safely stays in the lead, outside the toggle.
        more_js = """[...document.querySelectorAll('details.more')].map(d => { const s = d.querySelector(':scope > summary'), cs = getComputedStyle(s);
            return {at: (d.parentElement.closest('[id]') || {}).id, open: d.open, says: s.textContent, inside: d.textContent.length > s.textContent.length + 20,
                    look: [cs.fontSize, cs.color, cs.cursor].join(' ')}; })"""
        looks, seen_more = set(), {}
        for path, ready in (("sources.html?profile=1#upload", "#watch .w-addr"), ("sharing.html?profile=1#links", "#links details.more"),
                            ("admin.html#labs", "#dirAdd details.more"), ("", "#view-overview .card")):
            strict.goto(base + "/" + path)
            strict.wait_for_selector(ready, state="attached", timeout=10000)
            found = strict.evaluate(more_js)
            seen_more[path] = sorted({f["at"] for f in found})
            looks |= {f["look"] for f in found}
            check(f"{path or 'records'}: help beyond a card's lead is behind “More about this”, closed, still in the page (#176)",
                  found and all(not f["open"] and f["says"] == "More about this" and f["inside"] for f in found), found)
        check("…on the cards of every page that has them", all(set(seen_more[k]) >= want for k, want in (
              ("sources.html?profile=1#upload", {"importer", "panel-manual", "panel-link", "mine", "finder", "watch"}),
              ("sharing.html?profile=1#links", {"linksBody", "calendar", "summaryIntro", "export"}),
              ("admin.html#labs", {"users", "admin", "directory", "dirAdd", "labmaps", "installLog"}),
              ("", {"view-timeline", "labsChart", "labsHeatmap"}))), seen_more)
        dark = [int(x) for x in re.findall(r"\d+", next(iter(looks)).split(" ", 1)[1])[:3]] if looks else [0, 0, 0]
        check("…styled once: the same toggle everywhere, light on the dark theme", len(looks) == 1 and sum(dark) > 450, looks)
        strict.goto(base + "/sharing.html?profile=1#links")
        strict.wait_for_selector("#links details.more", timeout=10000)
        links_lead = strict.evaluate("document.querySelector('#links .share-links > p.hint').textContent")
        strict.click("#shrNav a[data-group=export]")
        export_lead = strict.evaluate("document.querySelector('#export > p.hint').textContent")
        check("…while what a person needs to decide stays in the leads: a share link's reach, an export's privacy", "Whoever has the link can open it" in links_lead
              and "as private as the records" in export_lead, (links_lead, export_lead))
        strict.goto(base + "/sources.html?profile=1#history")
        strict.wait_for_selector("#historyList li", timeout=10000)
        check("…and that undoing an import removes what it brought, with Fetch the only time a Health Link's server is contacted",
              "Undoing an import removes the records" in strict.inner_text("#history")
              and "only when you choose Fetch" in strict.evaluate("document.querySelector('#healthLink p.hint').textContent"))
        strict.click("#srcNav a[data-group=upload]")
        summ = "#importer details.more > summary"
        strict.focus(summ)
        strict.keyboard.press("Enter")
        opened = strict.evaluate("[document.querySelector('#importer details.more').open, document.querySelector('#importer details.more p').checkVisibility()]")
        strict.set_viewport_size({"width": 375, "height": 800})
        strict.wait_for_timeout(200)
        fits = strict.evaluate("document.documentElement.scrollWidth <= 375 && document.querySelector('#importer details.more').getBoundingClientRect().right <= 375")
        strict.keyboard.press("Enter")
        check("…opened by keyboard, its text shown, fitting 375 px; closed again by keyboard", opened == [True, True] and fits
              and not strict.evaluate("document.querySelector('#importer details.more').open"), (opened, fits))
        strict.set_viewport_size({"width": 1400, "height": 900})
        # ---- keyboard and screen-reader basics (#209), under the CSP: on every page with the top bar, Tab's first stop
        # is "Skip to content", shown while it has focus (readable on this page's dark theme, at 1400 and 375 px), and
        # Enter moves focus to the page's main part without changing the address; the browser tab's title names the
        # view shown; a group picked in a sidebar is said through the page's one shared status line (util.js)
        def contrast(c1, c2):   # WCAG contrast of two computed "rgb(…)" colours
            def lum(c):
                f = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
                r, g, b = [f(int(x) / 255) for x in re.findall(r"\d+", c)[:3]]
                return 0.2126 * r + 0.7152 * g + 0.0722 * b
            hi, lo = sorted((lum(c1), lum(c2)), reverse=True)
            return round((hi + 0.05) / (lo + 0.05), 1)

        def said(pg, box="sgStatus", tries=30):   # what the shared status line (or alert) says, once it says it
            for _ in range(tries):
                pg.wait_for_timeout(50)
                t = pg.evaluate(f"document.getElementById('{box}').textContent")
                if t:
                    return t
            return ""
        focused = """(() => { const a = document.activeElement, r = a.getBoundingClientRect(), cs = getComputedStyle(a);
            return {text: a.textContent, top: r.top, right: r.right, color: cs.color, bg: cs.backgroundColor}; })()"""
        skips, titles = {}, {}
        for path, ready, title, width in (("", "#view-overview .card", None, 1400), ("sources.html?profile=1", "#srcList h3", "Sources: Overview", 375),
                                          ("settings.html?profile=1", "#sec-overview .set-labs li", "Settings: Dashboard", 1400),
                                          ("sharing.html?profile=1", "#memberList tbody tr", "Sharing: People", 375),
                                          ("account.html", "#acctNav a", "Account: Sign-in and security", 1400), ("admin.html", "#userList table", "Admin: Accounts", 375),
                                          ("guide.html?profile=1", "#step-name:not([hidden])", "Getting started: Whose records", 375)):   # #199
            strict.set_viewport_size({"width": width, "height": 800})
            strict.goto(base + "/" + path)
            strict.wait_for_selector(ready, state="attached", timeout=10000)
            strict.wait_for_timeout(200)
            title = title or strict.evaluate("document.querySelector('[role=tab][aria-selected=true]').textContent") + " · My records"
            hash_was = strict.evaluate("location.hash")
            strict.keyboard.press("Tab")
            f = strict.evaluate(focused)
            strict.keyboard.press("Enter")
            skips[path or "records"] = [f["text"], f["top"] >= 0 and f["right"] <= width, contrast(f["color"], f["bg"]),
                                        strict.evaluate("document.activeElement === document.querySelector('main')"), strict.evaluate("location.hash") == hash_was]
            titles[path or "records"] = [strict.title(), title + " · Sanguity"]
        strict.set_viewport_size({"width": 1400, "height": 900})
        check("on every page with the top bar, Tab's first stop is “Skip to content”, shown while focused, readable on the dark theme, at 1400 and 375 px (#209)",
              len(skips) == 7 and all(v[0] == "Skip to content" and v[1] and v[2] >= 4.5 for v in skips.values()), skips)
        check("…Enter moves focus to the page's main part, the address unchanged", all(v[3] and v[4] for v in skips.values()), skips)
        # #247: hidden, it casts no shadow (its 16 px shadow showed at the top-left of every page in light mode); focused, it does
        skip_look = "(() => { const s = document.querySelector('.skip-link'); return [s === document.activeElement, getComputedStyle(s).boxShadow, s.getBoundingClientRect().bottom]; })()"
        shadows = {}
        for theme in ("light", "dark"):
            strict.evaluate(f"document.documentElement.dataset.theme = '{theme}'; document.activeElement && document.activeElement.blur(); scrollTo(0, 0)")
            hidden = strict.evaluate(skip_look)
            strict.focus(".skip-link")
            shadows[theme] = [hidden, strict.evaluate(skip_look)]
        check("…hidden, it casts no shadow on the page's top-left, and focused it has one, in light and dark (#247)",
              all(h[1] == "none" and h[2] <= 0 and not h[0] and f[0] and f[1] != "none" for h, f in shadows.values()), shadows)
        check("…and the browser tab's title names what's shown: “Labs · My records · Sanguity”, “Settings: Dashboard · Sanguity”",
              all(a == b for a, b in titles.values()), titles)
        check("…every page has the one shared status line: role=status, and role=alert for errors", strict.evaluate(
              "[...document.querySelectorAll('.sg-live')].map(x => [x.id, x.getAttribute('role')])") == [["sgStatus", "status"], ["sgAlert", "alert"]])
        group_said = {}
        for path, ready, link, want in (("sharing.html?profile=1", "#memberList tbody tr", "#shrNav a[data-group=links]", "Sharing: Share links"),
                                        ("settings.html?profile=1", "#sec-overview .set-labs li", "#setNav a[data-group=display]", "Settings: Display")):
            strict.goto(base + "/" + path)
            strict.wait_for_selector(ready, state="attached", timeout=10000)
            strict.wait_for_timeout(200)
            strict.evaluate("document.getElementById('sgStatus').textContent = ''")
            strict.click(link)
            group_said[path] = [strict.title(), said(strict), want]
        check("…a group picked in a sidebar becomes the title, and is said through the status line (a link says nothing of what it shows)",
              all(t == w + " · Sanguity" and s == w for t, s, w in group_said.values()), group_said)
        strict.goto(base + "/providers.html?q=medicare")   # find your provider (#93): the bundled list, searched
        strict.wait_for_selector(".dir-hit", timeout=10000)
        check("under the CSP: providers.js searches the directory", "Medicare" in strict.evaluate("document.querySelector('.dir-hit').textContent"))
        strict.wait_for_selector("#srcNote:not([hidden])", timeout=10000)
        note = strict.evaluate("document.getElementById('srcNote').textContent")
        check("…saying which lists aren't loaded, with no command: an admin gets a link to Admin (#167)", "build_directory" not in note and "python" not in note
              and strict.evaluate("document.querySelector('#srcNote a').getAttribute('href')") == "admin.html#directory", note)
        check("…in the connect step of Sources → Portals and health plans (#166)", strict.evaluate("location.pathname + location.search + location.hash")
              == "/sources.html?q=medicare#portals" and strict.evaluate("getComputedStyle(document.getElementById('mine')).display") != "none")
        strict.click(".dir-hit")
        strict.wait_for_selector("#detail h3", timeout=10000)
        detail = strict.inner_text("#detail")
        set_up = strict.evaluate("document.querySelector('#detail a[href^=\"admin.html?\"]').getAttribute('href')")
        check("…a provider not set up here sends an admin to set it up, with no copy of the vendor's steps",
              "Not set up on this server yet" in detail and "Return address" not in detail and "Scopes" not in detail
              and set_up.startswith("admin.html?vendor=insurer&issuer=https") and set_up.endswith("#apps"), (detail[:300], set_up))
        strict.goto(base + "/" + set_up)
        strict.wait_for_selector("#guide ol li", timeout=10000)
        check("…where Admin has the steps (one copy), the form filled in with the provider",
              strict.evaluate("[document.getElementById('fVendor').value, document.getElementById('fIssuer').value.startsWith('https://'), "
                              "document.getElementById('fName').value]") == ["insurer", True, "Medicare (Blue Button 2.0)"]
              and "Blue Button" in strict.inner_text("#guide"), strict.evaluate("document.getElementById('fName').value"))
        # ---- share links (#47), under the CSP: an owner makes one on the Sharing page, saying what a holder sees; a
        # browser with no session opens it (at 1400 and 375 px) and sees only the chosen sections and lab, with no
        # cookie set; the view is in the profile's activity; once revoked, the link says it doesn't work
        def sl_until(pg, expr, tries=100):   # wait_for_function evaluates a string, which the CSP forbids; evaluate isn't
            for _ in range(tries):
                if pg.evaluate(expr):
                    return True
                pg.wait_for_timeout(100)
            raise TimeoutError(expr)
        # #306: New share link clicked while the panel's links are still on their way waits for them, then opens the form
        # (the form is built from the sections and labs that come with them; it used to throw, and nothing opened)
        held, links_api = [], "**/api/profiles/1/links"
        strict.route(links_api, lambda route: held.append(route))
        strict.goto(base + "/sharing.html?profile=1#links")
        strict.wait_for_selector("#links .share-links button:has-text('New share link')", timeout=10000)
        strict.locator("#links button", has_text="New share link").click()
        strict.wait_for_timeout(300)
        early = [len(held), strict.locator("#links .share-form").count()]
        for r in held:
            r.continue_()
        strict.unroute(links_api)
        strict.wait_for_selector("#links .share-form input[aria-label='Name for this link']", timeout=10000)
        check("share links: New share link clicked before the panel has loaded opens the form once it has (#306)",
              early == [1, 0] and strict.locator("#links .share-form").count() == 1, early)
        strict.goto(base + "/sharing.html?profile=1#links")
        strict.wait_for_selector("#links .share-links button:has-text('New share link')", timeout=10000)
        mine = strict.locator("#links")
        check("share links: the panel is open in its group, with no button to open it",
              strict.evaluate("document.querySelector('#links .share-links-wrap > button').hidden && !document.querySelector('#links .share-links').hidden"))
        mine.locator("button", has_text="New share link").click()
        form = mine.locator(".share-form")
        form.locator("input[aria-label='Name for this link']").fill("Dr. Fictional")
        form.locator("input[value=medications]").uncheck()
        form.locator("input[aria-label=From]").fill("")
        form.locator(".share-labs input[type=radio]").nth(1).check()
        lab = form.locator(".share-labpick input").first.get_attribute("value")
        form.locator(".share-labpick input").first.check()
        sees = form.locator(".share-sees").inner_text()
        check("share links: the form says plainly what a holder of the link can see",
              all(w in sees for w in ("Anyone who has this link", "My records", "Visits and hospital stays", f"only {lab}", "of any date",
                                      "no documents", "nothing can be changed", "logged")) and "Medications" not in sees, sees[:400])
        form.locator("button[type=submit]").click()
        strict.wait_for_selector(".share-made .share-url", timeout=10000)
        share_url = strict.input_value(".share-made .share-url")
        check("share links: the link is shown once, with its token after the #", re.fullmatch(re.escape(base) + r"/share\.html#[\w-]{43}", share_url), share_url)
        check("share links: the list names it, not yet opened", "Dr. Fictional" in mine.locator(".share-links table").inner_text()
              and "never" in mine.locator(".share-links table").inner_text())
        holder = browser.new_context(viewport={"width": 1400, "height": 900})   # the real CSP, no cookies, no session
        hp = holder.new_page()
        hp.on("request", elsewhere)
        hp.on("pageerror", lambda e: csp_problems.append(f"share page script error: {e}"))
        hp.on("console", lambda m: m.type == "error" and "Content Security Policy" in m.text and csp_problems.append(m.text))
        api_paths = []
        hp.on("request", lambda r: "/api/" in r.url and api_paths.append(r.url.split("/api/", 1)[1]))
        r = hp.goto(share_url)
        check("share links: the page is sent with the pages' CSP", "script-src 'self'" in (r.headers.get("content-security-policy") or ""))
        hp.wait_for_selector("#share-visits", timeout=10000)
        shown = hp.evaluate("[...document.querySelectorAll('#shareBody > section')].map(s => s.id)")
        check("share links: only the chosen sections", shown == ["share-visits", "share-results"], shown)
        labs_shown = hp.evaluate("[...document.querySelectorAll('#share-results h3')].map(h => h.textContent)")
        check("share links: only the chosen lab", len(labs_shown) == 1 and labs_shown[0].startswith(lab), labs_shown)
        check("share links: the page says whose records, until when, and not medical advice",
              "My records" in hp.inner_text("#shareHead") and "stops working on" in hp.inner_text("#shareHead") and "Not medical advice" in hp.inner_text("main"))
        check("share links: the token never appears in an API address, and only the view is asked for", api_paths == ["share/view"], api_paths)
        check("share links: no cookie is set for the holder", holder.cookies() == [], holder.cookies())
        hp.set_viewport_size({"width": 375, "height": 800})
        hp.wait_for_timeout(200)
        check("share links: the shared page fits 375 px", hp.evaluate("document.documentElement.scrollWidth") <= 375, hp.evaluate("document.documentElement.scrollWidth"))
        cl_tables, cl_bad = cell_labels(hp, "share.html")   # #320: each table's values under their columns' names
        check("share links: …each table's cells labelled with their columns' names, its headings hidden (#320)",
              not cl_bad and any(t[1] and t[3] for t in cl_tables), cl_bad[:6] or cl_tables)
        shared_bad = phone_fits(hp, "light")   # #61: and no control cut off or overlapping, in light and dark
        shared_axe = axe_check(hp, "share.html, light")   # #202: and axe-core's WCAG A and AA rules, under the real CSP
        hp.emulate_media(color_scheme="dark")
        hp.wait_for_timeout(100)
        shared_bad += phone_fits(hp, "dark") + ([] if hp.evaluate("matchMedia('(prefers-color-scheme: dark)').matches") else ["dark: not dark"])
        shared_axe = [a + b for a, b in zip(shared_axe, axe_check(hp, "share.html, dark"))]
        hp.emulate_media(color_scheme=None)
        check("share links: …with no control cut off or overlapping, in light and dark (#61)", not shared_bad, shared_bad[:8] or "")
        check("share links: …and no serious or critical axe-core finding, in light and dark (WCAG 2.2 AA, #202)", not shared_axe[0],
              (shared_axe[0] + shared_axe[1])[:8] or "")
        shared_outline, shared_hbad = heading_outline(hp, "share.html")
        check("share links: …one h1, whose records they are, and no heading level skipped (#331)", not shared_hbad, shared_hbad or shared_outline)
        strict.set_viewport_size({"width": 375, "height": 800})
        strict.wait_for_timeout(200)
        check("share links: the owner's panel fits 375 px", strict.evaluate("document.documentElement.scrollWidth") <= 375, strict.evaluate("document.documentElement.scrollWidth"))
        cl_tables, cl_bad = cell_labels(strict, "Sharing → Share links")
        check("share links: …the list's cells labelled with their columns' names: Link, Shows, Ends, Opened (#320)",
              not cl_bad and any(t[1] and t[3] >= 4 for t in cl_tables), cl_bad[:6] or cl_tables)
        strict.set_viewport_size({"width": 1400, "height": 900})
        strict.goto(base + "/sharing.html?profile=1#activity")   # the same page: Activity is read afresh when it's opened
        sl_until(strict, "[...document.querySelectorAll('#actBody .activity tbody tr')].some(r => r.textContent.includes('opened the share link “Dr. Fictional”'))")
        row = strict.evaluate("[...document.querySelectorAll('#actBody .activity tbody tr')].find(r => r.textContent.includes('opened the share link')).textContent")
        check("share links: the view is in the profile's activity, as the share link", "share link" in row and share_url.split("#")[1] not in row, row)
        strict.click("#shrNav a[data-group=links]")
        strict.wait_for_selector("#links .share-links button:has-text('Revoke')", timeout=10000)
        mine.locator(".share-links button", has_text="Revoke").first.click()
        answer_ask(strict)
        sl_until(strict, "document.querySelector('#links .share-links table').textContent.includes('revoked')")
        hp.reload()
        sl_until(hp, "document.querySelector('#shareHead h1').textContent === \"This link doesn't work\"")
        check("share links: a revoked link shows nothing", hp.evaluate("document.getElementById('shareBody').children.length") == 0)
        hp.goto(base + "/share.html#not-a-real-token-at-all-0000000000000000000")
        sl_until(hp, "document.querySelector('#shareHead h1').textContent === \"This link doesn't work\"")
        check("share links: a made-up link says it doesn't work", True)
        holder.close()
        strict.goto(base + "/account.html")
        strict.wait_for_selector("#userMenuBtn", state="visible")
        strict.wait_for_timeout(500)
        menu_click(strict, "#logout")
        strict.wait_for_url("**/login.html", timeout=10000)
        check("under the CSP: no violations", not csp_problems, "; ".join(csp_problems)[:500])
        strict.evaluate("localStorage.clear()")
        strict.close()
        # the checks below drive the page with Playwright's waits, which evaluate script the CSP would refuse
        page = browser.new_page(viewport={"width": 1400, "height": 900}, bypass_csp=True)
        page.add_init_script(NAN_HOOK)
        page.on("pageerror", lambda e: problems.append(f"script error: {e}"))
        page.on("console", lambda m: m.type == "error" and problems.append(f"console error: {m.text}"))
        page.on("response", lambda r: r.status >= 400 and problems.append(f"HTTP {r.status}: {r.url}"))
        page.on("request", elsewhere)
        page.goto(base + "/")
        page.wait_for_url("**/login.html", timeout=10000)   # signed out: the app sends you to sign in
        page.fill("#u", USER)
        page.fill("#p", "wrong password")
        page.click("#b")
        page.wait_for_function("document.getElementById('e').textContent.length > 0")
        check("wrong password refused", "Wrong username or password" in page.inner_text("#e"))
        check("no plain-HTTP warning on this machine", page.is_hidden("#insecure") and page.evaluate("plainHttpWarning()") is None)
        problems[:] = [x for x in problems if "401" not in x]   # signed out so far: the 401s were the point
        page.evaluate("localStorage.setItem('ht-state', JSON.stringify({tab: 'heatmap', preset: '1y', v: 2}))")   # a view saved before profiles
        page.evaluate("localStorage.setItem('ht-health', JSON.stringify({period: '1y', stays: false}))")
        page.fill("#p", PW)
        page.click("#b")
        try:   # booted: switchTab has run (EV is set before the watch data loads)
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && typeof booted !== 'undefined' && booted", timeout=20000)
        except Exception:
            print("App did not start. Page says:", page.inner_text("main")[:300].replace("\n", " "))
            for x in problems:
                print("FAIL  ", x)
            sys.exit(1)
        settle = lambda ms=500: page.wait_for_timeout(ms)
        js = page.evaluate
        check("Body and activity setting saved before profiles carries over", js("hState.stays === false && localStorage.getItem('ht-health') === null"))
        check("view saved before profiles carries over, its Lab heatmap tab now Labs as a heatmap (#172)",
              js("state.tab === 'labs' && state.labsView === 'heatmap' && state.preset === '1y' && localStorage.getItem('ht-state') === null && !!localStorage.getItem('ht-state:1')"))

        def open_tab(t):   # "heatmap" and "labs": the Labs tab as a heatmap or as charts (#172)
            page.click("#tab-" + ("labs" if t == "heatmap" else t))
            if t in ("labs", "heatmap"):
                page.click("#labsView button[data-view=%s]" % ("heatmap" if t == "heatmap" else "chart"))
        view_of = lambda t: "labs" if t == "heatmap" else t

        try:
            # ---- every tab renders its main content (Labs as a heatmap first, so it's left showing charts)
            expect = {
                "overview": "document.querySelectorAll('#view-overview .tile').length",
                "health": "[...document.querySelectorAll('#view-health svg')].filter(s => s._draw && s.childNodes.length > 3).length",
                "timeline": "document.querySelectorAll('#tl circle.ev').length + ',' + document.querySelectorAll('#list .tl-row').length",
                "heatmap": "document.querySelectorAll('#hm rect.cell').length + ',' + +!document.getElementById('labsHeatmap').hidden + ',' + +document.getElementById('labsChart').hidden",
                "labs": "[...document.querySelectorAll('#charts .chart svg')].filter(s => s.childNodes.length > 3).length + ',' + +!document.getElementById('labsChart').hidden",
                "costs": "document.querySelectorAll('#view-costs .stat').length",
            }
            for tab, probe in expect.items():
                open_tab(tab)
                settle()
                n = js(probe)
                visible = js(f"!document.getElementById('view-{view_of(tab)}').hidden")
                check(f"{ {'heatmap': 'Labs (heatmap)', 'labs': 'Labs (charts)'}.get(tab, tab)} tab renders", visible and all(int(x) > 0 for x in str(n).split(",")), n)
                if tab == "heatmap":   # a cell's tooltip says where the result came from (#100)
                    page.hover("#hm rect.cell[data-s]")
                    settle(100)
                    tipped = js("document.getElementById('tip').innerText")
                    check("hovering a heatmap cell shows its source", "From Setup build, imported" in tipped, tipped[:200])
                    page.mouse.move(0, 0)
                page.locator("#notice").scroll_into_view_if_needed()
                check(f"{tab} tab: the 'not medical advice' notice shows in the footer",
                      page.is_visible("#notice") and "Not medical advice." in page.inner_text("#notice")
                      and "your care team interprets them" in page.inner_text("#notice"))

            # ---- next to out-of-range highlighting (#57): a caption on each tab that colours it, in light and dark, at 375 px
            flagged = {   # the tab's out-of-range highlighting, and its caption (Labs has one for both its views)
                "overview": "#view-overview .tile .st.hi, #view-overview .tile .st.lo",
                "heatmap": "#hm rect.cell[style*='--div-hi'], #hm rect.cell[style*='--div-lo']",
                "labs": "#charts .pt.abn",
            }
            theme_before = js("document.documentElement.dataset.theme || null")
            for theme in ("light", "dark"):
                js("t => { document.documentElement.dataset.theme = t; }", theme)
                for width in (1400, 375):
                    page.set_viewport_size({"width": width, "height": 900 if width > 375 else 800})
                    for tab, marks in flagged.items():
                        open_tab(tab)
                        settle(300)
                        note = page.locator(f"#view-{view_of(tab)} .range-note")
                        shown = note.count() == 1 and note.is_visible()
                        text = note.inner_text() if shown else ""
                        check(f"{tab} ({theme}, {width} px): caption beside the out-of-range highlighting",
                              shown and js(f"document.querySelectorAll({marks!r}).length") > 0
                              and "reference ranges in your own lab reports" in text and "isn't medical advice" in text, text)
                        legible = js("""(() => { const n = document.querySelector('#view-%s .range-note'), f = document.getElementById('notice');
                            const bg = getComputedStyle(document.body).backgroundColor;
                            return getComputedStyle(n).color !== bg && getComputedStyle(f).color !== bg; })()""" % view_of(tab))
                        check(f"{tab} ({theme}, {width} px): caption and footer readable, page fits",
                              legible and js("document.documentElement.scrollWidth") <= width, js("document.documentElement.scrollWidth"))
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            page.set_viewport_size({"width": 1400, "height": 900})

            # ---- tiles (#330): on the Overview (key labs, home readings, Watch & scale), Body and activity and Costs, at
            # 1400, 1100, 375 and 320 px, no change line ends in a short fragment (a lone year) or breaks a date, and the
            # sparklines in a row of tiles start at the same height
            js("window._was330 = [state.tab, state.preset, state.from, state.to]")
            for width in (1400, 1100, 375, 320):
                page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                for tab, preset in (("overview", None), ("health", "90d"), ("costs", "all")):
                    js("([t, p]) => { if (p) setPeriod(p); switchTab(t); }", [tab, preset])
                    settle(300)
                    got = js(TILE_LINES_JS)
                    check(f"{tab} ({width} px, #330): no tile's change line ends in a short fragment or breaks a date"
                          + (", and a row's sparklines line up" if tab == "overview" else ""),
                          got["n"] > 0 and not got["bad"] and not got["uneven"], got)
            js("[state.preset, state.from, state.to] = window._was330.slice(1); switchTab(window._was330[0]); renderAll()")
            page.set_viewport_size({"width": 1400, "height": 900})

            # ---- charts for everyone (#60): every chart has Chart | Table (a pick-one pair, #193) and its table has
            # column headers and rows; the heatmap's table has a header per lab and per draw day; the Timeline's chart
            # points to its list; a Labs chart and a Body and activity chart take focus and step through their points
            # from the keyboard, with the hover's readout and tooltip, said to screen readers; out-of-range results are
            # marked by shape or words as well as colour (Labs charts, heatmap cells and table, Overview tiles, the
            # drawer's results); the tables fit 375 px in light and dark
            js("window._was60 = [new Set(state.sel), state.preset, state.from, state.to]; state.tables.clear(); state.sel = new Set(['Hemoglobin']); applyPreset('all'); switchTab('labs', 'chart'); renderLabs()")
            settle(400)
            seg = js("""(() => { const s = document.querySelector('#charts .chart .hd .view-seg');
                return s && [s.getAttribute('role'), s.getAttribute('aria-label'), [...s.children].map(b => [b.textContent, b.getAttribute('aria-pressed')])]; })()""")
            check("charts (#60): a Labs chart has Chart | Table, a pick-one pair with Chart selected",
                  seg == ["group", "Show Hemoglobin as", [["Chart", "true"], ["Table", "false"]]], seg)
            flagged = js("SERIES.find(s => s.name === 'Hemoglobin').pts.filter(p => p.ts >= state.from && p.ts <= state.to && p[4]).map(p => p[4])")
            marks = js("""[...document.querySelectorAll('#charts .chart svg .pt.abn')].map(m => [m.tagName, m.getAttribute('d') || ''])""")
            check("…its out-of-range results are triangles, ▲ above the range and ▼ below, not only red dots",
                  len(flagged) > 0 and len(marks) == len(flagged) and all(t == "path" and d.count("L") == 2 for t, d in marks)
                  and "▲▼" in page.inner_text("#labsChart .legend"), [len(flagged), len(marks), marks[:2]])
            # procedures and imaging, shown (#250): a ● and a ■ at the top, never a triangle like ▲ or ▼ on the same chart;
            # the legend shows both shapes while they're on, and not while they're off
            js("window._marks250 = state.marks; state.marks = true; renderCharts()")
            settle(300)
            ev = js("""(() => { const svg = document.querySelector('#charts .chart svg'), want = markEvents();
                const heads = [...svg.querySelectorAll('.evhead')];
                const tri = [...svg.querySelectorAll('path')].filter(p => /var\\(--c-(procedure|imaging)\\)/.test(p.getAttribute('fill') || ''));
                const keys = [...document.querySelectorAll('#labsChart .legend .marks-key')].filter(k => !k.hidden);
                return {want: want.length, kinds: [...new Set(want.map(e => e.k))].sort(), heads: heads.map(h => [h.dataset.k, h.tagName]).filter((h, i, a) => a.findIndex(o => o + '' === h + '') === i),
                  n: heads.length, tri: tri.length, legend: keys.map(k => [k.textContent, k.querySelector('svg .evhead')?.tagName])}; })()""")
            js("state.marks = window._marks250; renderCharts()")
            settle(200)
            off = js("[...document.querySelectorAll('#labsChart .legend .marks-key')].every(k => k.hidden) === !state.marks")
            check("Labs (#250): procedures are a ● and imaging a ■ on the charts, no triangle, and the legend shows both while they're on",
                  ev["want"] > 0 and ev["n"] == ev["want"] and ev["kinds"] == ["imaging", "procedure"] and not ev["tri"]
                  and sorted(ev["heads"]) == [["imaging", "rect"], ["procedure", "circle"]]
                  and ev["legend"] == [["Procedure", "circle"], ["Imaging", "rect"]] and off, ev)
            # …and their names (#312): at 1400, 375 and 320 px a procedure is named only inside the chart and where no
            # other mark falls on its name, and every mark is still drawn (one left unnamed opens its drawer: below, with
            # the milestone labels, after the #217 check of what details the records come with). At 1400 px there's room
            # for at least one name (the demo has 6), so the check can't pass with the names gone (#321)
            js("window._marks312 = state.marks; state.marks = true; renderCharts()")
            for width in (1400, 375, 320):
                page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                js("renderCharts()")
                settle(400)
                got = js(CHART_LABELS_JS, "#charts .chart svg")
                heads = js("[document.querySelectorAll('#charts .chart svg .evhead').length, markEvents().length]")
                check(f"Labs ({width} px, #312): a procedure's name is inside its chart with no mark on it, and every mark is still drawn"
                      + (", and at least one is named" if width == 1400 else ""),
                      got["charts"] and not got["problems"] and heads[1] > 0 and heads[0] == heads[1] * len(got["charts"])
                      and (width != 1400 or got["ev"] > 0), [got["problems"], got["ev"], heads])
            js("state.marks = window._marks312; renderCharts()")
            page.set_viewport_size({"width": 1400, "height": 900})
            settle(300)
            label = js("document.querySelector('#charts .chart svg').getAttribute('aria-label')")
            check("…the chart takes focus, as a chart summed up in words",
                  js("(s => [s.getAttribute('tabindex'), s.getAttribute('role'), s.getAttribute('aria-roledescription')])(document.querySelector('#charts .chart svg'))") == ["0", "application", "chart"]
                  and re.match(r"Hemoglobin: \d+ results from .+ to .+, latest .+ g/dL, \d+ outside reference range\. Left and right arrow keys", label or ""), label)
            page.focus("#charts .chart svg")
            page.keyboard.press("End")
            settle(150)
            last = js("(s => { const p = s.pts.filter(p => p.ts <= state.to).at(-1); return [p[5] || fmtNum(p[1]), fmtDT(p.ts)]; })(SERIES.find(s => s.name === 'Hemoglobin'))")
            tipped = js("[getComputedStyle(tip).display, tip.innerText, document.querySelector('#charts .chart .read').textContent]")
            check("…End shows the last result, its tooltip and readout as when hovering", tipped[0] == "block" and last[0] in tipped[1]
                  and last[1] in tipped[1] and last[1] in tipped[2] and not tipped[2].startswith("Latest"), tipped)
            page.keyboard.press("ArrowLeft")
            settle(200)
            before = js("tip.innerText")
            check("…← the one before it, said to screen readers",
                  before != tipped[1] and before.split("\n")[0] in js("document.getElementById('sgStatus').textContent"), [before, js("document.getElementById('sgStatus').textContent")])
            page.keyboard.press("Home")
            settle(150)
            first = js("(s => fmtDT(s.pts.filter(p => p.ts >= state.from)[0].ts))(SERIES.find(s => s.name === 'Hemoglobin'))")
            check("…Home the first", first in js("tip.innerText"), js("tip.innerText"))
            page.keyboard.press("Escape")
            settle(100)
            check("…and Escape lets go: no tooltip, the readout back to the latest, focus kept",
                  js("getComputedStyle(tip).display") == "none" and js("document.querySelector('#charts .chart .read').textContent").startswith("Latest")
                  and js("document.activeElement === document.querySelector('#charts .chart svg')"))
            ring = js("(s => [getComputedStyle(s).outlineStyle, s.matches(':focus-visible')])(document.querySelector('#charts .chart svg'))")
            check("…a ring shows the focus", ring == ["solid", True], ring)
            page.click("#charts .chart .view-seg button:text-is('Table')")
            settle(200)
            t = js("""(() => { const t = document.querySelector('#charts .chart table.data');
                return t && {heads: [...t.querySelectorAll('thead th')].map(h => [h.getAttribute('scope'), h.textContent]), rows: t.querySelectorAll('tbody tr').length,
                  abn: [...t.querySelectorAll('tbody td.abn:last-child')].map(td => td.textContent), focus: document.activeElement.textContent + ':' + document.activeElement.getAttribute('aria-pressed')}; })()""")
            check("…Table shows its table instead, headed, a row per result, each out-of-range one saying ▲ High or ▼ Low; focus stays on Table",
                  t and t["heads"] == [["col", "Date"], ["col", "Value"], ["col", "Ref range"], ["col", "Flag"]] and t["rows"] > len(flagged) - 1
                  and len(t["abn"]) == len(flagged) and all(a in ("▲ High", "▼ Low") for a in t["abn"]) and t["focus"] == "Table:true"
                  and not js("document.querySelector('#charts .chart svg')"), t)
            # the heatmap: a mark on every out-of-range cell, and a table with a header per lab and per draw day
            js("state.tables.clear(); switchTab('labs', 'heatmap')")
            settle(400)
            cells = js("[document.querySelectorAll('#hm rect.cell[style*=\"--div-hi\"], #hm rect.cell[style*=\"--div-lo\"]').length, document.querySelectorAll('#hm .hm-mark').length]")
            check("heatmap (#60): every out-of-range cell is marked ▲ or ▼ as well as coloured, and the legend says so",
                  cells[0] > 0 and cells[0] == cells[1] and "▲ above the range" in page.inner_text("#hmLegend"), cells)
            nrows, ncols = js("document.querySelectorAll('#hm text.row').length"), js("new Set([...document.querySelectorAll('#hm rect.cell')].map(c => c.dataset.k)).size")
            page.click("#hmHd .view-seg button:text-is('Table')")
            settle(300)
            ht = js("""(() => { const t = document.querySelector('#hmTable table');
                return t && [document.getElementById('hmWrap').hidden, document.getElementById('hmTable').hidden, t.querySelectorAll('tbody th[scope=row]').length,
                  t.querySelectorAll('thead th[scope=col]').length, [...t.querySelectorAll('td.abn .vh')].map(v => v.textContent.trim()),
                  t.querySelectorAll('tbody th[scope=rowgroup]').length]; })()""")
            check("…Table: a real table, a header for each lab and each draw day, out-of-range results said high or low",
                  ht and ht[0] and not ht[1] and ht[2] == nrows and ht[3] == ncols + 1 and len(ht[4]) == cells[0]
                  and set(ht[4]) <= {"high", "low"} and ht[5] > 0, ht and ht[:4] + [len(ht[4]), ht[5]] + [nrows, ncols])
            # Body and activity: each card's charts, or one table; a chart steps from the keyboard
            js("state.tables.clear(); switchTab('health')")
            settle(500)
            check("Body and activity (#60): every card with charts has Chart | Table",
                  js("[...document.querySelectorAll('#view-health .view-seg')].map(s => s.dataset.key)") == ["#h-body", "#h-glucose", "#h-sleep", "#h-activity", "#h-heart", "#h-bp", "#h-temp", "#h-oxygen", "#h-resp", "#h-vo2"])
            page.focus("#h-body svg")
            page.keyboard.press("Home")
            settle(150)
            hk = js("[document.querySelector('#h-body svg').getAttribute('aria-label'), getComputedStyle(tip).display, tip.innerText, document.querySelector('#h-body svg .cross').getAttribute('visibility')]")
            check("…its charts step from the keyboard too, with the hover's crosshair and tooltip", re.match(r"Weight: \d+ readings from", hk[0] or "")
                  and hk[1] == "block" and " lb" in hk[2] and hk[3] == "visible", hk)
            page.keyboard.press("Escape")
            for k in ("#h-body", "#h-glucose", "#h-sleep", "#h-activity", "#h-heart", "#h-bp", "#h-temp", "#h-oxygen", "#h-resp", "#h-vo2"):
                page.click(f"{k} .view-seg button:text-is('Table')")
                settle(200)
            ht = js("['#h-body', '#h-glucose', '#h-sleep', '#h-activity', '#h-heart', '#h-bp', '#h-temp', '#h-oxygen', '#h-resp', '#h-vo2'].map(k => document.querySelector(k + ' svg') ? -1 : document.querySelectorAll(k + ' table.data tbody tr').length)")
            check("…Table shows each card's values as a table, a row per day or week", all(n > 0 for n in ht), ht)
            # Costs: both charts
            js("state.tables.clear(); switchTab('costs')")
            settle(500)
            for key in ("#costBars", "#paidCum"):
                page.click(f"#view-costs .view-seg[data-key='{key}'] button:text-is('Table')")
                settle(200)
            ct = js("['#costBars', '#paidCum'].map(k => document.querySelector(`.view-seg[data-key='${k}']`).closest('.card').querySelectorAll('.chart-table tbody tr').length)")
            check("Costs (#60): the claims by month and the running totals each have a table", all(n > 0 for n in ct), ct)
            # the running totals' end labels (#250): the text in an ink token, 4.5:1 against the card in both themes, the
            # series' colour on a short line key before it, not on the text
            js("state.tables.clear(); renderCosts()")
            settle(400)
            theme_before = js("document.documentElement.dataset.theme || null")
            ends = []
            for theme in ("light", "dark"):
                js("t => { document.documentElement.dataset.theme = t; }", theme)
                settle(150)
                ends += js("""t => { const tok = v => { const s = document.createElement('span'); s.style.color = `var(${v})`; document.body.append(s); const c = getComputedStyle(s).color; s.remove(); return c; };
                  const ink = [tok('--ink'), tok('--ink2')], keys = [tok('--c-imaging'), tok('--c-procedure')];
                  return [...document.querySelectorAll('#view-costs svg text.cum-end')].map((e, i) => { const k = e.previousElementSibling;
                    return [t, e.textContent, getComputedStyle(e).fill, ink.includes(getComputedStyle(e).fill), getComputedStyle(e.closest('.card')).backgroundColor,
                      !!k && k.matches('line.cum-key') && getComputedStyle(k).stroke === keys[i]]; }); }""", theme)
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            check("Costs (#250): the running totals' end labels are in ink, 4.5:1 against the card in light and dark, each after a line key in its colour",
                  len(ends) == 4 and all(i and k and contrast(f, bg) >= 4.5 for _, _, f, i, bg, k in ends), [e + [contrast(e[2], e[4])] for e in ends])
            # the Timeline: its list is the table
            js("switchTab('timeline')")
            settle(300)
            check("Timeline (#60): the chart says its events are listed below, and they are",
                  "listed under Events, below" in js("document.getElementById('tl').getAttribute('aria-label')") and js("document.querySelectorAll('#list .tl-row').length") > 0)
            # #247: scrolled into the list, the month heading sticks just under the top bar, on top and readable, in both themes
            # (it stuck at the page's top, behind the bar, never seen)
            stuck_month = """(() => { const bar = document.querySelector('header.topbar').getBoundingClientRect().bottom;
              const m = [...document.querySelectorAll('#list .month')].filter(m => Math.abs(m.getBoundingClientRect().top - bar) < 1.5).at(-1);
              if (!m) return null;
              const r = m.getBoundingClientRect(), cs = getComputedStyle(m);
              return [m.textContent, document.elementFromPoint(r.left + 8, (r.top + r.bottom) / 2) === m, cs.color, cs.backgroundColor]; })()"""
            theme_before, months = js("document.documentElement.dataset.theme || null"), {}
            for theme in ("light", "dark"):
                js(f"document.documentElement.dataset.theme = '{theme}'")
                js("(() => { const m = document.querySelectorAll('#list .month')[1]; scrollTo(0, m.getBoundingClientRect().top + scrollY + 200); })()")
                settle(150)
                months[theme] = js(stuck_month)
                months[theme] = months[theme] and [*months[theme][:2], contrast(*months[theme][2:])]
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; scrollTo(0, 0); }", theme_before)
            check("…its month headings stick under the top bar as the list scrolls, readable, in light and dark (#247)",
                  all(v and v[1] and v[2] >= 4.5 for v in months.values()), months)
            # #308: under 500 px tall the bar doesn't stick, and a heading stuck --bar-h down the screen (98 px at 667×375)
            # with rows scrolling above it; there it sticks at the top
            size_before = page.viewport_size
            page.set_viewport_size({"width": 667, "height": 375})
            settle(200)
            js("(() => { const m = document.querySelectorAll('#list .month')[1]; scrollTo(0, m.getBoundingClientRect().top + scrollY + 200); })()")
            settle(150)
            short_month = js("""(() => { const m = [...document.querySelectorAll('#list .month')].filter(m => Math.abs(m.getBoundingClientRect().top) < 1.5).at(-1);
              return m ? [m.textContent, document.elementFromPoint(m.getBoundingClientRect().left + 8, m.getBoundingClientRect().bottom - 4) === m]
                : [...document.querySelectorAll('#list .month')].map(m => Math.round(m.getBoundingClientRect().top)).filter(t => t > -60 && t < 375); })()""")
            js("scrollTo(0, 0)")
            page.set_viewport_size(size_before)
            settle(200)
            check("…and on a short screen (667×375), where the bar scrolls away, a month heading sticks at the top of the screen (#308)",
                  bool(short_month) and isinstance(short_month[0], str) and short_month[1] is True, short_month)
            tl_abn = js("""(() => { const days = [...document.querySelectorAll('#tl circle.ev')].filter(m => { const e = byId.get(+m.dataset.id); return e?.k === 'lab' && e.abn; });
                const tri = [...document.querySelectorAll('#tl path.tl-abn')];
                return {days: days.length, badges: tri.length, shapes: tri.every(p => (p.getAttribute('d').match(/L/g) || []).length === 2 && getComputedStyle(p).pointerEvents === 'none'),
                  above: days.every(m => tri.some(p => { const a = m.getBBox(), b = p.getBBox(); return Math.abs(b.x + b.width / 2 - (a.x + a.width / 2)) < .5 && b.y + b.height <= a.y; })),
                  dots: document.querySelectorAll('#tl circle[fill="var(--crit)"]').length,
                  said: [...document.querySelectorAll('#list .badge.abn')].every(b => / out of range$/.test(b.textContent)) && !!document.querySelector('#list .badge.abn')}; })()""")
            check("Timeline (#250): a lab day with a result out of range has a ▲ over it (not a red dot), and the list says out of range in words",
                  tl_abn["days"] > 0 and tl_abn["badges"] == tl_abn["days"] and tl_abn["shapes"] and tl_abn["above"] and not tl_abn["dots"] and tl_abn["said"], tl_abn)
            # not by colour alone, elsewhere: the Overview's tiles and the drawer's results
            js("switchTab('overview')")
            settle(300)
            ov = js("""[...document.querySelectorAll('#view-overview .tile')].filter(t => t.querySelector('.st.hi, .st.lo'))
                .map(t => [t.querySelector('.st').textContent, !!t.querySelector('svg .spark-abn'), !t.querySelector('svg circle')])""")
            check("Overview (#60): an out-of-range tile says High or Low, and its sparkline ends in ▲ or ▼, not a red dot",
                  ov and all(s in ("▲ High", "▼ Low") and m and c for s, m, c in ov), ov)
            # ---- a record's details, loaded when it's opened (#217): the records come with a lab day's list of results and
            # what the Overview lists of an immunization, nothing else of details; opening a lab day shows it at once and asks
            # once for its results, which then stay for the page; one that couldn't be loaded says why, and is asked for again
            asked = []
            page.on("request", lambda r: "/details?ids=" in r.url and asked.append(r.url))
            look = js("""(() => { const v = Object.values(DATA.details); return {n: v.length, events: DATA.events.length, rows: v.some(d => d.rows || d.lines),
                listed: v.every(d => d.type === 'labday' || d.partial || d.type === 'exercise' || d.type === 'ecg')}; })()""")
            check("details (#217): the records come without each record's details, only a lab day's list and what the Overview lists",
                  look["listed"] and not look["rows"] and 0 < look["n"] < look["events"], look)
            day = js("EV.find(e => e.k === 'lab' && !e.hidden && (DATA.details[e.id]?.ids || []).length > 1 && DATA.details[e.id].ids.every(i => !detailsHave(i))).id")
            first = js("id => { const p = openEvent(byId.get(id)); return [!!p, !!document.querySelector('#drawer.open #dbody .details-wait'), document.getElementById('dtitle').textContent]; }", day)
            page.wait_for_selector("#dbody details.res table.data", timeout=5000)
            n = len(asked)
            js("id => { closeDrawer(); return openEvent(byId.get(id)); }", day)
            check("…opening a lab day shows it at once, saying its details are loading, then its results, asked for in one request",
                  first[0] and first[1] and first[2] and n == 1 and js("id => DATA.details[id].ids.every(i => detailsHave(i))", day), (first, asked))
            check("…and opened again they're kept for the page: no new request", len(asked) == 1 and js("document.querySelectorAll('#dbody details.res').length") > 1, asked)
            claim = js("EV.find(e => e.k === 'claim' && !detailsHave(e.id)).id")
            before = len(problems)
            busy = re.compile(r"/api/profiles/\d+/details\?")
            page.route(busy, lambda route: route.fulfill(status=503, content_type="application/json", body='{"detail": "The server is busy (a fictional outage)."}'))
            js("id => openEvent(byId.get(id))", claim)
            miss = js("[document.querySelector('#dbody .details-missed')?.textContent || '', document.getElementById('dtitle').textContent, !!document.querySelector('#dbody p.source')]")
            page.unroute(busy)
            js("id => openEvent(byId.get(id))", claim)
            again = js("[!!document.querySelector('#dbody .details-missed'), document.querySelector('#dbody dl.kv')?.textContent || '']")
            problems[before:] = [x for x in problems[before:] if "/details?ids=" not in x and "503" not in x]
            check("…one that couldn't be loaded says why, keeping its title and source, and is asked for again when opened next",
                  "couldn't be loaded" in miss[0] and "fictional outage" in miss[0] and miss[1] and miss[2] and not again[0] and ("Insurer" in again[1] or "Billed" in again[1]),
                  (miss, again))
            js("closeDrawer()")
            js("openEvent(EV.find(e => e.k === 'lab' && e.abn))")
            settle(300)
            dr = js("""[...document.querySelectorAll('#dbody table.data tbody tr')].filter(r => r.querySelector('td.abn')).map(r => r.lastElementChild.textContent)""")
            check("drawer (#60): every out-of-range result says ▲ High or ▼ Low beside its colour, and the count says what it counts",
                  dr and all(x in ("▲ High", "▼ Low") for x in dr) and "outside reference range" in js("document.querySelector('#dbody .badge.abn')?.textContent || ''"), dr)
            page.keyboard.press("Escape")
            # the tables and the Chart | Table pairs at 375 px, light and dark
            theme_before = js("document.documentElement.dataset.theme || null")
            page.set_viewport_size({"width": 375, "height": 800})
            bad = []
            for theme in ("light", "dark"):
                js("t => { document.documentElement.dataset.theme = t; }", theme)
                for tab, keys in (("labs", ["Hemoglobin"]), ("heatmap", ["#hm"]), ("health", ["#h-body", "#h-glucose", "#h-sleep", "#h-activity", "#h-heart", "#h-bp", "#h-temp", "#h-oxygen", "#h-resp", "#h-vo2"]), ("costs", ["#costBars", "#paidCum"])):
                    for table in (False, True):
                        js("([keys, on]) => { state.tables.clear(); if (on) keys.forEach(k => state.tables.add(k)); }", [keys, table])
                        js("t => t === 'heatmap' ? switchTab('labs', 'heatmap') : t === 'labs' ? switchTab('labs', 'chart') : switchTab(t)", tab)
                        settle(400)
                        bad += phone_fits(page, f"{tab} {'table' if table else 'chart'} ({theme})")
                        if table:
                            look = js("""(() => { const td = document.querySelector('.chart-table td, .hm-table td'); const bg = getComputedStyle(document.body).backgroundColor;
                                return td && [getComputedStyle(td).color, bg]; })()""")
                            if not look or look[0] == look[1]:
                                bad.append(f"{tab} table ({theme}): unreadable {look}")
            check("charts (#60): the tables and Chart | Table fit 375 px and read, in light and dark", not bad, bad[:8])
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            js("state.tables.clear(); [state.sel, state.preset, state.from, state.to] = window._was60; state.labsView = 'chart'; save()")
            page.set_viewport_size({"width": 1400, "height": 900})

            # ---- the Sleep tile's stages (#216): the nights' deep and REM averages are hours on the page (H.sleep), shown in
            # minutes as the chart's tooltip and table give them; it said "1 min deep · 2 min REM". Read against the
            # watch summary's own minutes (sleep_deep_min, sleep_rem_min) for the nights in the period
            js("window._was216 = [state.preset, state.from, state.to]; state.tables.clear(); applyPreset('90d'); switchTab('health')")
            settle(400)
            st = js("""(() => { const [a, b] = hRange(), ns = H.sleep.filter(p => p[0] >= a && p[0] <= b && p[2] + p[3] + p[4] > 0);
                const day = k => Object.fromEntries(WATCH.metrics.find(m => m.key === k).pts), deep = day('sleep_deep_min'), rem = day('sleep_rem_min');
                const avg = m => Math.round(ns.reduce((s, p) => s + (m[isoDay(p[0])] || 0), 0) / ns.length);
                const tile = [...document.querySelectorAll('#view-health .tile')].find(t => t.querySelector('.lbl').textContent === 'Sleep');
                return [tile.querySelector('.delta').textContent, `${avg(deep)} min deep · ${avg(rem)} min REM`, ns.length, avg(deep)]; })()""")
            check("Sleep tile (#216): the period's average deep and REM sleep a night, in minutes as the watch gave them, not hours called minutes",
                  st[2] > 30 and st[0] == st[1] and 30 <= st[3] <= 150, st)
            js("[state.preset, state.from, state.to] = window._was216; save()")

            # ---- blood glucose (#181): a meter's readings and a CGM's days on Body and activity (add_glucose_watch): the
            # tile, time in ranges labelled as the standard ranges, a day with a sensor gap, the chart from the keyboard,
            # its table, mmol/L with the SI choice, lab glucose alongside only when asked, a period without any; no
            # five-minute reading in the page's data; at 1400 and 375 px, light and dark, axe clean
            js("window._was181 = [state.preset, state.from, state.to]; state.tables.clear(); applyPreset('30d'); switchTab('health')")
            settle(500)
            gl = js("""(() => { const c = document.getElementById('h-glucose'); if (!c) return null;
                const tile = [...document.querySelectorAll('#view-health .tile')].find(t => t.querySelector('.lbl').textContent === 'Glucose');
                const [a, b] = hRange();
                return {tile: tile ? tile.innerText.replace(/\\s+/g, ' ') : null, tir: (c.querySelector('.glu-tir')?.innerText || '').replace(/\\s+/g, ' '),
                  dots: c.querySelectorAll('svg circle').length, want: G.meter.filter(p => p[0] >= a && p[0] <= b).length,
                  pts: WATCH.metrics.filter(m => m.key.startsWith('glucose_')).reduce((n, m) => n + m.pts.length, 0),
                  after: [...document.querySelectorAll('#view-health > .card')].map(x => x.id).indexOf('h-glucose')}; })()""")
            check("glucose (#181): a Blood glucose card after weight, and a Glucose tile with the period's CGM average and its share in 70–180",
                  gl and gl["after"] == 1 and gl["tile"] and re.match(r"Glucose \d+ ?mg/dL avg \d+% of CGM readings in 70–180$", gl["tile"]), gl)
            pcts = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)%", gl["tir"] if gl else "")]
            check("…time in ranges for the period's CGM days, below 54 and below 70 apart, adding up, labelled the standard published ranges and not advice",
                  gl and gl["tir"].startswith("Time in ranges · 14 CGM days in this period") and all(w in gl["tir"] for w in (
                      "Below 54 mg/dL:", "Below 70 (with below 54):", "70–180:", "Above 180:", "standard published ranges", "not advice"))
                  and len(pcts) >= 4 and abs(pcts[-3] + pcts[-2] + pcts[-1] - 100) < 1.5, gl and gl["tir"])
            check("…meter readings as points, every one in the period", gl and gl["dots"] == gl["want"] > 20, gl)
            check("…and none of the CGM's 4,000 five-minute readings in the page's data, only its days", gl and gl["pts"] < 200, gl and gl["pts"])
            page.focus("#h-glucose svg")
            page.keyboard.press("End")
            settle(150)
            gk = [js("[document.querySelector('#h-glucose svg').getAttribute('aria-label'), tip.innerText]")]
            for _ in range(5):
                page.keyboard.press("ArrowLeft")
            settle(150)
            gk.append(js("tip.innerText"))
            page.keyboard.press("Escape")
            check("…its chart steps a day at a time from the keyboard: each day's CGM figures, and the day with a sensor gap says so",
                  re.match(r"Blood glucose: \d+ days from", gk[0][0] or "") and "Nov 14, 2026" in gk[0][1] and "CGM average" in gk[0][1]
                  and "Nov 9, 2026" in gk[1] and "18 h of readings (216)" in gk[1], gk)
            page.click("#h-glucose .view-seg button:text-is('Table')")
            settle(200)
            gt = js("""(() => { const rows = [...document.querySelectorAll('#h-glucose table.data tbody tr')].map(r => [...r.cells].map(c => c.textContent));
                return [[...document.querySelectorAll('#h-glucose table.data th')].map(h => h.textContent), rows.length, rows.find(r => r[0] === 'Nov 9, 2026'),
                        rows.find(r => r[0] === 'Nov 6, 2026')]; })()""")
            check("…its table: a row a day, the CGM's figures, hours with readings and the meter's readings with their times",
                  gt[0][:4] == ["Date", "Average (mg/dL)", "Low–high (mg/dL)", "70–180 (%)"] and gt[1] >= 25 and gt[2] and gt[2][7] == "18 h (216 readings)"
                  and gt[3] and re.search(r"12:30\s?PM 152", gt[3][8]), gt)
            js("state.tables.clear(); renderHealth()")
            js("window._u181 = CFG.units; CFG.units = {...(CFG.units || {}), lab: 'si'}; G = undefined; renderHealth()")
            settle(300)
            si = js("[[...document.querySelectorAll('#view-health .tile')].find(t => t.querySelector('.lbl').textContent === 'Glucose').innerText.replace(/\\s+/g, ' '), document.querySelector('#h-glucose .glu-tir').innerText.replace(/\\s+/g, ' ')]")
            check("…in mmol/L with the SI choice (#41), the standard ranges written in mmol/L too", re.match(r"Glucose \d+\.\d ?mmol/L avg .* in 3\.9–10\.0$", si[0])
                  and "Below 3.0 mmol/L:" in si[1] and "Below 3.9 (with below 3.0):" in si[1], si)
            js("CFG.units = window._u181; G = undefined; renderHealth()")
            settle(200)
            check("…no lab glucose to show alongside in the demo: no option for it", js("!document.querySelector('#h-glucose input[type=checkbox]')"))
            js("""SERIES.push({name: 'Glucose, Fasting', unit: 'mg/dL', watch: false, n: 2, txt: [], key: 'browser-check-181', pts: [['2026-11-03T08:10:00', 104, 70, 99, 'H', '104'],
                   ['2026-10-20T08:05:00', 92, 70, 99, '', '92']].map(p => Object.assign(p, {ts: zParse(p[0])}))}); G = undefined; renderHealth()""")
            settle(300)
            lab_dots = "document.querySelectorAll('#h-glucose svg circle[fill=\"var(--surface)\"]').length"
            off = [js("!!document.querySelector('#h-glucose input[type=checkbox]') && !document.querySelector('#h-glucose input[type=checkbox]').checked"), js(lab_dots)]
            page.locator("#h-glucose label", has_text="Show lab glucose results alongside").click()
            settle(300)
            on = [js(lab_dots), "Lab result" in js("document.querySelector('#h-glucose .legend').innerText"), js("localStorage.getItem('ht-glucose:' + PROFILE.id)")]
            page.click("#h-glucose .view-seg button:text-is('Table')")
            settle(200)
            lab_row = js("[...document.querySelectorAll('#h-glucose table.data tbody tr')].map(r => [...r.cells].map(c => c.textContent)).find(r => r[0] === 'Nov 3, 2026')")
            check("…lab glucose stays a lab series, drawn alongside only when asked (remembered), its result said high in the table",
                  off == [True, 0] and on[0] == 2 and on[1] and on[2] == '{"labs":true}' and lab_row and lab_row[-1] == "104 (Glucose, Fasting) ▲ High", [off, on, lab_row])
            bad, serious = [], []
            for width in (1400, 375):
                page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                for theme in ("light", "dark"):
                    js("t => { document.documentElement.dataset.theme = t; }", theme)
                    for table in (False, True):
                        js("on => { if (on) state.tables.add('#h-glucose'); else state.tables.delete('#h-glucose'); renderHealth(); }", table)
                        settle(400)
                        page.locator("#h-glucose").scroll_into_view_if_needed()
                        if width < 400:
                            bad += phone_fits(page, f"glucose {'table' if table else 'chart'} ({theme})")
                        serious += axe_check(page, f"Body and activity with glucose, {'table' if table else 'chart'} at {width} px ({theme})")[0]
            check("…its card, time in ranges, lab option, chart and table fit 375 px, in light and dark", not bad, bad[:6])
            check("…and pass axe-core's WCAG A and AA rules at 1400 and 375 px, in light and dark", not serious, serious[:6])
            page.set_viewport_size({"width": 1400, "height": 900})
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            js("""state.tables.clear(); gOpt.labs = false; localStorage.removeItem('ht-glucose:' + PROFILE.id); SERIES.splice(SERIES.findIndex(s => s.key === 'browser-check-181'), 1);
                  G = undefined; state.from = dayTs('2025-03-01'); state.to = dayTs('2025-04-01'); state.preset = 'custom'; renderHealth()""")
            settle(300)
            none = js("[document.getElementById('h-glucose').innerText, [...document.querySelectorAll('#view-health .tile')].find(t => t.querySelector('.lbl').textContent === 'Glucose').innerText.replace(/\\s+/g, ' ')]")
            check("…a period without glucose says so, with the last reading's date", "No glucose readings in this period (the last on Nov 14, 2026)." in none[0]
                  and re.match(r"Glucose – ?mg/dL last Nov 14, 2026", none[1]) and "Time in ranges" not in none[0], none)
            js("[state.preset, state.from, state.to] = window._was181; renderHealth()")

            # ---- home blood pressure beside clinic readings (#180): the receiver folded fictional uploads from both phone
            # apps (add_vitals_records: overlapping and repeated ones) into the summary, each reading once; Body and activity
            # charts them with the records' systolic and diastolic series, home and clinic told apart, one clinic reading
            # flagged by its report and one that is also a home reading shown once; the readings listed, Chart | Table and
            # the keyboard like the other charts; Labs' blood pressure links there; 1400 and 375 px, light and dark, axe clean
            want_home = len(home_readings())
            js("window._was180 = [state.preset, state.from, state.to, state.tab]; state.tables.clear(); setRange(dayTs('2026-08-01'), dayTs('2026-10-31')); switchTab('health')")
            settle(500)
            bp = js("""(() => { const c = document.getElementById('h-bp'), s = c && c.querySelector('svg');
                return c && {readings: (WATCH.bp_readings || []).length, apps: [...new Set(WATCH.bp_readings.map(r => r[5]))].sort(), home: H.vit.bp.home.length,
                  clinic: H.vit.bp.clinic.length, label: s.getAttribute('aria-label'), legend: c.querySelector('.legend').textContent,
                  pairs: [...s.querySelectorAll('line[stroke]')].filter(l => l.getAttribute('stroke') !== 'var(--axis)').length,
                  flagged: s.querySelectorAll('path.pt.abn').length, hollow: s.querySelectorAll('circle[fill="var(--surface)"]').length,
                  also: H.vit.bp.home.filter(h => h.also).map(h => [isoDay(h.t), h.also]),
                  inPeriod: [...H.vit.bp.home, ...H.vit.bp.clinic].filter(r => r.t >= state.from && r.t <= state.to).length,
                  list: [...c.querySelectorAll('.vital-list li')].map(l => l.textContent), tile: [...document.querySelectorAll('#view-health .tile')].find(t => t.textContent.startsWith('Blood pressure'))?.textContent}; })()""")
            check("blood pressure (#180): every home reading from both phone apps once, though uploads overlapped and came twice",
                  bp and bp["readings"] == want_home == bp["home"] and bp["apps"] == ["Apple Health", "Health Connect"], bp and [bp["readings"], want_home, bp["apps"]])
            check("…and the receiver's daily summary: readings, average and highest each day",
                  js("['bp_sys', 'bp_dia', 'bp_sys_max', 'bp_dia_max', 'bp_n'].every(k => WATCH.metrics.some(m => m.key === k && m.pts.length > 100))")
                  and js("WATCH.metrics.find(m => m.key === 'bp_n').pts.reduce((s, p) => s + p[1], 0)") == want_home)
            check("…on Body and activity one chart with both pressures as paired points, at home and at visits told apart, the clinic's readings from the records' systolic and diastolic series",
                  bp and bp["clinic"] == 4 and "At home" in bp["legend"] and "At a visit" in bp["legend"] and bp["pairs"] == bp["inPeriod"] > 30
                  and bp["hollow"] >= 2 and re.match(r"Blood pressure: \d+ readings from", bp["label"] or ""), bp and {k: bp[k] for k in ("clinic", "legend", "pairs", "inPeriod", "hollow", "label")})
            check("…a clinic reading's own flag shows (▲, its report said high); nothing is judged otherwise",
                  bp and bp["flagged"] == 2 and "flagged as high or low" in page.inner_text("#h-bp .hint")
                  and not re.search(r"(?i)\b(normal|elevated|hypertension|stage|target|goal)\b", page.inner_text("#h-bp")), bp and bp["flagged"])
            check("…a home reading also in the records (typed in as vitals) is shown once, saying so",
                  bp and bp["also"] == [["2026-10-20", "Typed in"]], bp and bp["also"])
            check("…the latest readings listed with their time and source, and a tile with the latest",
                  bp and len(bp["list"]) == 5 and all("mmHg" in l and "Fictional Cuff (Health Connect)" in l for l in bp["list"])
                  and bp["tile"] and "mmHg" in bp["tile"] and "latest at home" in bp["tile"], bp and [bp["list"][:2], bp["tile"]])
            page.focus("#h-bp svg")
            page.keyboard.press("End")
            settle(150)
            tipped = js("tip.innerText")
            page.keyboard.press("Home")
            settle(150)
            first = js("tip.innerText")
            check("…its readings step from the keyboard, the tooltip saying the pressures, when and where",
                  "mmHg" in tipped and "at home · Fictional Cuff" in tipped and first != tipped, [first, tipped])
            page.keyboard.press("Escape")
            js("setRange(dayTs('2026-10-01'), dayTs('2026-10-10'))")
            settle(300)
            page.focus("#h-bp svg")
            for _ in range(40):   # to the visit on 6 October
                if "at a visit" in js("tip.innerText") and "Systolic ▲ High" in js("tip.innerText"):
                    break
                page.keyboard.press("ArrowRight")
                settle(60)
            check("…a visit's reading says where it came from and its flags", "at a visit · Lakeside Family Medicine" in js("tip.innerText")
                  and "Systolic ▲ High, Diastolic ▲ High" in js("tip.innerText"), js("tip.innerText"))
            page.keyboard.press("Escape")
            page.click("#h-bp .view-seg button:text-is('Table')")
            settle(200)
            tb = js("""(() => { const t = document.querySelector('#h-bp table.data'); return t && {heads: [...t.querySelectorAll('thead th')].map(h => h.textContent),
                rows: [...t.querySelectorAll('tbody tr')].map(r => [...r.children].map(td => td.textContent))}; })()""")
            n_in = js("[...H.vit.bp.home, ...H.vit.bp.clinic].filter(r => r.t >= state.from && r.t <= state.to).length")
            check("…Table lists every reading in the period: time, both pressures, pulse, where and source; the flags in words",
                  tb and tb["heads"] == ["Date and time", "Systolic (mmHg)", "Diastolic (mmHg)", "Pulse (bpm)", "Where", "Source"] and len(tb["rows"]) == n_in
                  and any(r[4] == "Clinic visit" and r[1].endswith("▲ High") and r[5] == "Lakeside Family Medicine" for r in tb["rows"])
                  and any(r[4] == "Home" and r[3] for r in tb["rows"]), tb and [tb["heads"], len(tb["rows"]), n_in, tb["rows"][:2]])
            js("state.tables.clear(); applyPreset('all')")
            settle(400)
            wk = js("[document.querySelector('#h-bp svg').getAttribute('aria-label'), document.querySelectorAll('#h-bp svg circle').length, document.querySelectorAll('#h-bp svg path[opacity=\"0.16\"]').length]")
            check("…over a long period the home readings are weekly averages, a band from diastolic to systolic, the visits' readings still one by one",
                  wk[0].startswith("Blood pressure, home readings by week:") and wk[2] == 1 and 0 < wk[1] <= 2 * 5, wk)
            # from Labs: the blood pressure series link to the chart
            js("state.sel = new Set(['Blood pressure, systolic']); switchTab('labs', 'chart')")
            settle(400)
            link = page.locator("#charts .chart .vital-link a")
            check("…Labs' blood pressure chart says the home readings are on Body and activity", link.count() == 1
                  and "Readings taken at home are beside these" in page.inner_text("#charts .chart .vital-link"))
            link.click()
            settle(600)
            check("…and its link opens Body and activity at the blood pressure chart",
                  js("state.tab") == "health" and js("document.activeElement.id") == "h-bp"
                  and js("(r => r.top < innerHeight && r.bottom > 0)(document.getElementById('h-bp').getBoundingClientRect())"), [js("state.tab"), js("document.activeElement.id")])
            js("setRange(dayTs('2026-09-25'), dayTs('2026-10-31')); switchTab('health')")
            bad, axe_bp = [], []
            theme_before = js("document.documentElement.dataset.theme || null")
            for width in (1400, 375):
                page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                for theme in ("light", "dark"):
                    js("t => { document.documentElement.dataset.theme = t; }", theme)
                    for table in (False, True):
                        js("on => { state.tables.clear(); if (on) state.tables.add('#h-bp'); renderHealth(); }", table)
                        settle(300)
                        page.locator("#h-bp").scroll_into_view_if_needed()
                        if width == 375:
                            bad += phone_fits(page, f"blood pressure {'table' if table else 'chart'} ({theme})")
                        axe_bp += axe_check(page, f"blood pressure {'table' if table else 'chart'}, {width} px, {theme}")[0]
            check("…the chart, its list and its table fit 375 px, and axe-core finds nothing serious at 1400 and 375 px, light and dark",
                  not bad and not axe_bp, (bad + axe_bp)[:6])
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            page.set_viewport_size({"width": 1400, "height": 900})
            js("state.tables.clear(); [state.preset, state.from, state.to] = window._was180; state.sel = new Set(window._was60[0]); switchTab(window._was180[3]); save()")

            # ---- body temperature and blood oxygen in full (#184): the receiver folded a thermometer's readings from both
            # apps (Health Auto Export's in °F, sent twice; Life Dashboard's in °C) and a watch's blood oxygen into the
            # summary; Body and activity has a small card for each beside the visits' readings (a temperature in °F, one
            # flagged high; SpO2), in the person's temperature unit (#41); Chart | Table, the keyboard, the Labs links, and
            # 1400 and 375 px in light and dark with axe-core
            want_t, want_o = temp_spo2_readings()
            js("window._was184 = [state.preset, state.from, state.to, state.tab]; state.tables.clear(); setRange(dayTs('2026-08-01'), dayTs('2026-11-14')); switchTab('health')")
            settle(500)
            tc = js("""(() => { const c = document.getElementById('h-temp'), o = document.getElementById('h-oxygen'), day = k => (WATCH.metrics.find(m => m.key === k) || {pts: []}).pts;
                return c && o && {readings: (WATCH.temp_readings || []).length, apps: [...new Set(WATCH.temp_readings.map(r => r[3]))].sort(), days: day('body_temp').length,
                  max: day('body_temp_max').length, unit: H.vit.temp.unit, home: H.vit.temp.home.length, clinic: H.vit.temp.clinic.length, hint: c.querySelector('.hint').textContent,
                  label: c.querySelector('svg').getAttribute('aria-label'), legend: c.querySelector('.legend').textContent, list: [...c.querySelectorAll('.vital-list li')].map(l => l.textContent),
                  first: H.vit.temp.home[0].v[0], o2days: day('spo2_avg').length, o2low: day('spo2_min').length, o2home: H.vit.spo2.home.length, o2clinic: H.vit.spo2.clinic.length,
                  o2label: o.querySelector('svg').getAttribute('aria-label'), o2band: o.querySelectorAll('svg path[opacity="0.16"]').length,
                  o2hollow: o.querySelectorAll('svg circle[fill="var(--surface)"]').length, o2legend: o.querySelector('.legend').textContent}; })()""")
            spo2_days = len({t[:10] for t, _ in want_o})
            check("body temperature (#184): every reading from both phone apps once, in °C whatever unit it came in, and each day's average and highest",
                  tc and tc["readings"] == len(want_t) == tc["home"] and tc["apps"] == ["Apple Health", "Health Connect"] and tc["days"] == tc["max"] == 8,
                  tc and {k: tc[k] for k in ("readings", "apps", "days", "max")})
            check("…a card on Body and activity: the readings at home and at visits told apart, in the records' unit (°F) while Units says “as reported”",
                  tc and tc["unit"] == "°F" and tc["clinic"] == 4 and abs(tc["first"] - (37.9 * 9 / 5 + 32)) < 0.01 and "At home" in tc["legend"] and "At a visit" in tc["legend"]
                  and "in °F." in tc["hint"] and re.match(r"Body temperature: \d+ readings from", tc["label"] or ""), tc and {k: tc[k] for k in ("unit", "clinic", "first", "hint", "label")})
            check("…its latest readings listed with time and source", tc and len(tc["list"]) == 3 and all("°F" in l and ("Fictional Thermometer" in l or "Lakeside" in l) for l in tc["list"]), tc and tc["list"])
            check("blood oxygen (#184): the phone's daily average and low from both apps, a card with the days as a band and the visits' readings apart",
                  tc and tc["o2days"] == tc["o2low"] == tc["o2home"] == spo2_days and tc["o2clinic"] == 4 and tc["o2band"] == 1 and tc["o2hollow"] >= 1
                  and "Phone, daily average" in tc["o2legend"] and tc["o2label"].startswith("Blood oxygen:"), tc and {k: tc[k] for k in ("o2days", "o2low", "o2home", "o2clinic", "o2band", "o2hollow", "o2label")})
            page.focus("#h-temp svg")
            page.keyboard.press("Home")   # the first: the visit on 12 August, then the cold's first reading at home
            settle(150)
            tipped = [js("tip.innerText")]
            page.keyboard.press("ArrowRight")
            settle(150)
            tipped.append(js("tip.innerText"))
            check("…the temperature chart steps from the keyboard, the tooltip saying the reading, when and where",
                  "98.1 °F" in tipped[0] and "at a visit · Lakeside Family Medicine" in tipped[0] and "100.2 °F" in tipped[1]
                  and "at home · Fictional Thermometer (Apple Health)" in tipped[1], tipped)
            page.keyboard.press("Escape")
            page.focus("#h-oxygen svg")
            page.keyboard.press("End")
            settle(150)
            check("…and so does blood oxygen's", "% average, low" in js("tip.innerText"), js("tip.innerText"))
            page.keyboard.press("Escape")
            js("setRange(dayTs('2025-06-01'), dayTs('2025-06-30'))")
            settle(300)
            check("…a visit's temperature keeps its report's flag (▲), and the hint says what it means",
                  js("document.querySelectorAll('#h-temp svg path.pt.abn').length") == 1 and "flagged as high or low" in page.inner_text("#h-temp .hint")
                  and not re.search(r"(?i)\b(normal|fever|low oxygen|hypox|target|goal)\b", page.inner_text("#h-temp") + page.inner_text("#h-oxygen")))
            js("setRange(dayTs('2026-08-01'), dayTs('2026-11-14'))")
            settle(300)
            for k in ("#h-temp", "#h-oxygen"):
                page.click(f"{k} .view-seg button:text-is('Table')")
                settle(200)
            tb = js("""['#h-temp', '#h-oxygen'].map(k => { const t = document.querySelector(k + ' table.data'); return t && [[...t.querySelectorAll('thead th')].map(h => h.textContent),
                t.querySelectorAll('tbody tr').length, [...t.querySelectorAll('tbody tr')].map(r => r.children[2].textContent)]; })""")
            check("…Table lists every reading: time, value, where and source (blood oxygen by day, with the day's low)",
                  tb[0] and tb[0][0] == ["Date and time", "Temperature (°F)", "Where", "Source"] and tb[0][1] == len(want_t) + 2 and {"Home", "Clinic visit"} <= set(tb[0][2])
                  and tb[1] and tb[1][0] == ["Date", "SpO2 (%)", "Day's low (%)", "Where", "Source"] and tb[1][1] == spo2_days + 2, tb and [tb[0] and tb[0][:2], tb[1] and tb[1][:2]])
            # the unit preference (#41): °C chosen, the visits' °F readings shown in °C too
            js("CFG.units = {...CFG.units, temperature: 'C'}; H.vit = null; state.tables.clear(); renderHealth()")
            settle(300)
            tu = js("[H.vit.temp.unit, H.vit.temp.clinic.map(r => +r.v[0].toFixed(1)), document.querySelector('#h-temp .hint').textContent, H.vit.temp.home[0].v[0]]")
            check("…with °C chosen in Settings → Units, every reading is in °C, the visits' converted (100.6 °F is 38.1 °C)",
                  tu[0] == "°C" and 38.1 in tu[1] and "in °C (Settings → Units)" in tu[2] and tu[3] == 37.9, tu)
            js("CFG.units = {...CFG.units, temperature: 'reported'}; H.vit = null; renderHealth()")
            # from Labs: the temperature and SpO2 charts link to their cards
            for series, card in (("Temperature", "h-temp"), ("SpO2 (clinic)", "h-oxygen")):
                js("n => { state.sel = new Set([n]); switchTab('labs', 'chart'); }", series)
                settle(400)
                link = page.locator("#charts .chart .vital-link a")
                ok = link.count() == 1
                if ok:
                    link.click()
                    settle(600)
                check(f"…Labs' {series} chart links to its card on Body and activity", ok and js("state.tab") == "health" and js("document.activeElement.id") == card,
                      [ok, js("state.tab"), js("document.activeElement.id")])
            js("setRange(dayTs('2026-08-01'), dayTs('2026-11-14')); switchTab('health')")
            bad, axe_tc = [], []
            theme_before = js("document.documentElement.dataset.theme || null")
            for width in (1400, 375):
                page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                for theme in ("light", "dark"):
                    js("t => { document.documentElement.dataset.theme = t; }", theme)
                    for table in (False, True):
                        js("on => { state.tables.clear(); if (on) { state.tables.add('#h-temp'); state.tables.add('#h-oxygen'); } renderHealth(); }", table)
                        settle(300)
                        page.locator("#h-temp").scroll_into_view_if_needed()
                        if width == 375:
                            bad += phone_fits(page, f"temperature and oxygen {'tables' if table else 'charts'} ({theme})")
                        axe_tc += axe_check(page, f"temperature and oxygen {'tables' if table else 'charts'}, {width} px, {theme}")[0]
            check("…both cards fit 375 px, and axe-core finds nothing serious at 1400 and 375 px, light and dark", not bad and not axe_tc, (bad + axe_tc)[:6])
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            page.set_viewport_size({"width": 1400, "height": 900})
            js("state.tables.clear(); [state.preset, state.from, state.to] = window._was184; state.sel = new Set(window._was60[0]); switchTab(window._was184[3]); save()")

            # ---- respiratory rate and cardio fitness (#226): the receiver folded a watch's breathing rate and VO2 max from
            # both phone apps (add_fitness_records: Health Auto Export's sent twice, then Life Dashboard's) into the
            # summary, a day each whichever app sent it; Body and activity has a small card for each, the visits'
            # respiratory rates beside the phone's and told apart (one flagged by its report), VO2 max as the watch gives
            # it with no fitness levels; Chart | Table, the keyboard, weeks over a long period; 1400 and 375 px, light and
            # dark, axe clean
            want_r, want_v = fitness_readings()
            js("window._was226 = [state.preset, state.from, state.to, state.tab]; state.tables.clear(); setRange(dayTs('2026-08-01'), dayTs('2026-11-14')); switchTab('health')")
            settle(500)
            fc = js("""(() => { const r = document.getElementById('h-resp'), v = document.getElementById('h-vo2'), day = k => (WATCH.metrics.find(m => m.key === k) || {pts: []}).pts;
                return r && v && {rdays: day('resp').length, vdays: day('vo2max').length, first: [day('resp')[0], day('vo2max')[0]], rhome: H.fit.resp.home.length,
                  rclinic: H.fit.resp.clinic.length, vhome: H.fit.vo2.home.length, vunit: H.fit.vo2.unit, rlabel: r.querySelector('svg').getAttribute('aria-label'),
                  vlabel: v.querySelector('svg').getAttribute('aria-label'), rlegend: r.querySelector('.legend').textContent, vlegend: v.querySelector('.legend').textContent,
                  rhint: r.querySelector('.hint').textContent, vhint: v.querySelector('.hint').textContent, vtext: v.innerText,
                  marks: [r.querySelectorAll('svg circle[fill="var(--surface)"]').length, r.querySelectorAll('svg path.pt.abn').length],
                  order: [...document.querySelectorAll('#view-health > .card')].map(c => c.id).filter(Boolean).slice(-3),
                  list: [...r.querySelectorAll('.vital-list li')].map(l => l.textContent)}; })()""")
            r_days, v_days = len({t[:10] for t, _ in want_r}), len({t[:10] for t, _ in want_v})
            check("respiratory rate and VO2 max (#226): read from both phone apps, a day each whichever app sent it",
                  fc and fc["rdays"] == fc["rhome"] == r_days and fc["vdays"] == fc["vhome"] == v_days and fc["first"] == [["2026-08-01", 14], ["2026-08-02", 39.8]],
                  fc and {k: fc[k] for k in ("rdays", "vdays", "first")})
            check("…a small card each, after blood oxygen: the phone's days and the visits' respiratory rates told apart, a flagged one ▲",
                  fc and fc["order"] == ["h-oxygen", "h-resp", "h-vo2"] and fc["rclinic"] == len(RESP_VISITS) and fc["marks"] == [1, 1]
                  and "Phone, by day" in fc["rlegend"] and "At a visit" in fc["rlegend"] and "flagged as high or low" in fc["rhint"]
                  and re.match(r"Respiratory rate: \d+ readings from", fc["rlabel"] or ""), fc and {k: fc[k] for k in ("order", "rclinic", "marks", "rlegend", "rlabel")})
            check("…VO2 max as the watch gives it, in mL/kg/min, without fitness levels or judging words",
                  fc and fc["vunit"] == "mL/kg/min" and "without fitness levels" in fc["vhint"] and "At a visit" not in fc["vlegend"]
                  and re.match(r"Cardio fitness \(VO2 max\): \d+ days from", fc["vlabel"] or "")
                  and not re.search(r"(?i)\b(poor|fair|good|excellent|superior|elite|normal|low|high|below|above|target|goal)\b", fc["vtext"]),
                  fc and {k: fc[k] for k in ("vunit", "vhint", "vlabel")})
            check("…its latest listed with the day and where it came from", fc and len(fc["list"]) == 3 and all("breaths/min" in l and "phone, the day's average" in l for l in fc["list"]),
                  fc and fc["list"])
            page.focus("#h-resp svg")
            page.keyboard.press("Home")
            settle(150)
            tipped, visit = js("tip.innerText"), ""
            for _ in range(16):   # on to the visit of 12 August
                page.keyboard.press("ArrowRight")
                settle(60)
                if "at a visit" in js("tip.innerText"):
                    visit = js("tip.innerText")
                    break
            check("…the respiratory rate chart steps from the keyboard: the phone's day, then the visit's reading with its report's flag",
                  "14 breaths/min" in tipped and js("fmtD(dayTs('2026-08-01'))") in tipped and "phone, the day's average" in tipped
                  and "22 breaths/min" in visit and "at a visit · Lakeside Family Medicine" in visit and "Respiratory rate ▲" in visit, [tipped, visit])
            page.keyboard.press("Escape")
            page.focus("#h-vo2 svg")
            page.keyboard.press("End")
            settle(150)
            check("…and so does VO2 max's", "41.6 mL/kg/min" in js("tip.innerText") and "the watch's estimate" in js("tip.innerText"), js("tip.innerText"))
            page.keyboard.press("Escape")
            for k in ("#h-resp", "#h-vo2"):
                page.click(f"{k} .view-seg button:text-is('Table')")
                settle(200)
            tb = js("""['#h-resp', '#h-vo2'].map(k => { const t = document.querySelector(k + ' table.data'); return t && [[...t.querySelectorAll('thead th')].map(h => h.textContent),
                t.querySelectorAll('tbody tr').length, t.querySelectorAll('tbody td.abn').length, t.querySelector('tbody tr').textContent]; })""")
            check("…Table lists every day and reading: the date, the value, where and from what",
                  tb[0] and tb[0][0] == ["Date", "Respiratory rate (breaths/min)", "Where", "Source"] and tb[0][1] == r_days + 2 and tb[0][2] == 1
                  and tb[1] and tb[1][0] == ["Date", "VO2 max (mL/kg/min)", "Source"] and tb[1][1] == v_days and "Phone, the watch's estimate" in tb[1][3], tb)
            js("state.tables.clear(); applyPreset('1y'); renderHealth()")
            settle(400)
            wk = js("[document.querySelector('#h-resp svg').getAttribute('aria-label'), document.querySelector('#h-vo2 svg').getAttribute('aria-label'), document.querySelector('#h-resp .hint').textContent]")
            check("…over a long period, weekly averages, said so", wk[0].startswith("Respiratory rate, weekly: ") and wk[1].startswith("Cardio fitness (VO2 max), weekly: ")
                  and "daily average by week" in wk[2], wk)
            js("setRange(dayTs('2026-08-01'), dayTs('2026-11-14'))")
            bad, axe_f = [], []
            theme_before = js("document.documentElement.dataset.theme || null")
            for width in (1400, 375):
                page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                for theme in ("light", "dark"):
                    js("t => { document.documentElement.dataset.theme = t; }", theme)
                    for table in (False, True):
                        js("on => { state.tables.clear(); if (on) { state.tables.add('#h-resp'); state.tables.add('#h-vo2'); } renderHealth(); }", table)
                        settle(300)
                        page.locator("#h-resp").scroll_into_view_if_needed()
                        if width == 375:
                            bad += phone_fits(page, f"respiratory rate and VO2 max {'tables' if table else 'charts'} ({theme})")
                        axe_f += axe_check(page, f"respiratory rate and VO2 max {'tables' if table else 'charts'}, {width} px, {theme}")[0]
            check("…both cards fit 375 px, and axe-core finds nothing serious at 1400 and 375 px, light and dark", not bad and not axe_f, (bad + axe_f)[:6])
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            page.set_viewport_size({"width": 1400, "height": 900})
            js("state.tables.clear(); [state.preset, state.from, state.to] = window._was226; state.sel = new Set(window._was60[0]); switchTab(window._was226[3]); save()")

            # ---- heart rate zones on workouts (#197): the receiver kept the heart rate of four workouts from both phone apps
            # (add_workout_hr): without a birth date or a measured maximum, no zones, and the card and the workout's
            # details say how to add one (About this person); with a birth date, five zones from 220 minus the age on each
            # workout's day (a birthday between them), the time in each from the readings, a ride with only its average
            # and highest shown against them; minutes a week in each zone on Body and activity, Chart | Table and the
            # keyboard, the period's workouts listed, each opening its details; a measured maximum typed in About this
            # person replaces the estimate; 1400 and 375 px, light and dark, axe clean
            put_person = lambda body: js("b => fetch('api/profiles/1/person', {method: 'PUT', headers: {'X-Sanguity': '1', 'Content-Type': 'application/json'}, "
                                         "body: JSON.stringify(b)}).then(r => r.status)", body)
            was197 = js("[state.preset, state.from, state.to, state.tab, [...state.sel]]")   # the page loads again below: kept here
            js("state.tables.clear(); setRange(dayTs('2026-08-01'), dayTs('2026-11-14')); switchTab('health')")
            settle(500)
            zn = js("""(() => { const c = document.getElementById('h-zones'); return c && {text: c.innerText, link: c.querySelector('.zones-need a')?.getAttribute('href'),
                svg: !!c.querySelector('svg'), seg: !!c.querySelector('.view-seg'), basis: WATCH.hrZones, hr: WATCH.exercise.filter(x => x.hr).map(x => x.hr)}; })()""")
            check("heart rate zones (#197): without a birth date or a measured maximum, no zones: the card says how to add one, linking to About this person",
                  zn and "need a birth date or a measured maximum heart rate" in zn["text"] and zn["link"] == "sharing.html?profile=1#about" and not zn["svg"] and not zn["seg"]
                  and zn["basis"] == {"basis": None, "maxHr": None, "workouts": True} and len(zn["hr"]) == 4 and not any("zones" in h or "hist" in h for h in zn["hr"]),
                  zn and {k: zn[k] for k in ("link", "svg", "basis")})
            js("openEvent(EV.find(e => e.k === 'exercise' && e.ts === zParse('2026-09-08T11:00:00+00:00')))")
            page.wait_for_selector("#drawer.open .zones-one", timeout=5000)
            dz = page.inner_text("#dbody")
            check("…and so do the workout's details, beside its average and highest", "Average 130 bpm · highest 162 bpm" in dz and "add one in About this person" in dz, dz[-300:])
            js("closeDrawer()")
            check("…a birth date saved in About this person", put_person({"birthDate": ZONE_BIRTH}) == 200)
            page.goto(base + "/")
            page.wait_for_function("typeof booted !== 'undefined' && booted && PROFILE.id === 1", timeout=20000)
            js("state.tables.clear(); setRange(dayTs('2026-08-01'), dayTs('2026-11-14')); switchTab('health')")
            settle(500)
            hrs = js("WATCH.exercise.filter(x => x.hr).map(x => [x.start.slice(0, 10), x.hr.zmax, x.hr.age, x.hr.basis, x.hr.zones || null, !!x.hr.hist])")
            check("…then five zones from 220 minus the age on each workout's day (35, then 36 after the birthday), the time in each from the readings",
                  hrs == [["2026-09-08", 185, 35, "age", [0, 300, 600, 600, 300, 0], False], ["2026-09-12", 184, 36, "age", None, False],
                          ["2026-10-20", 184, 36, "age", [0, 900, 1800, 0, 0, 0], False], ["2026-11-10", 184, 36, "age", [0, 0, 0, 0, 1500, 300], False]], hrs)
            zc = js("""(() => { const c = document.getElementById('h-zones'); return c && {hint: c.querySelector('.hint').textContent, label: c.querySelector('svg')?.getAttribute('aria-label'),
                legend: [...c.querySelectorAll('.legend span')].map(s => s.textContent), list: [...c.querySelectorAll('.zones-list li')].map(l => l.textContent),
                order: [...document.querySelectorAll('#view-health > .card')].map(c => c.id).filter(Boolean)}; })()""")
            check("…a card after Activity: minutes a week in each zone, the zones said to be from the common formula, 220 minus age, and not advice",
                  zc and zc["order"][zc["order"].index("h-activity") + 1] == "h-zones" and "220 minus age (the common formula)" in zc["hint"]
                  and "184 bpm at 36 on the latest here" in zc["hint"] and "not advice" in zc["hint"] and re.match(r"Heart rate zones, minutes a week: 3 weeks from", zc["label"] or "")
                  and zc["legend"] == ["Zone 1 · 50–60%", "Zone 2 · 60–70%", "Zone 3 · 70–80%", "Zone 4 · 80–90%", "Zone 5 · 90% and above"], zc)
            check("…the period's workouts with heart rate listed, the ride with its average and highest only",
                  zc and len(zc["list"]) == 4 and "most time in zone 4" in zc["list"][0] and "average and highest only" in zc["list"][2]
                  and "most time in zones 2 and 3" in zc["list"][3]
                  and not re.search(r"(?i)\b(good|great|poor|bad|too|risk|danger|normal)\b", " ".join(zc["list"]) + zc["hint"]), zc and zc["list"])
            page.focus("#h-zones svg")
            page.keyboard.press("End")
            settle(150)
            check("…the chart steps a week at a time from the keyboard, the tooltip giving each zone's minutes",
                  all(w in js("tip.innerText") for w in ("Week of " + js("fmtD(dayTs('2026-11-09'))"), "1 workout with readings", "Zone 5: 5 min", "Zone 4: 25 min")), js("tip.innerText"))
            page.keyboard.press("Escape")
            page.click("#h-zones .view-seg button:text-is('Table')")
            settle(200)
            zt = js("""(() => { const t = document.querySelector('#h-zones table.data'); return t && [[...t.querySelectorAll('thead th')].map(h => h.textContent),
                [...t.querySelectorAll('tbody tr')].map(r => [...r.cells].map(c => c.textContent))]; })()""")
            check("…Table: a row a week, the minutes in each zone, below zone 1 and the workouts counted",
                  zt and zt[0] == ["Week", "Zone 1 (min)", "Zone 2 (min)", "Zone 3 (min)", "Zone 4 (min)", "Zone 5 (min)", "Below zone 1 (min)", "Workouts with readings"]
                  and [r[1:] for r in zt[1]] == [["0", "0", "0", "25", "5", "0", "1"], ["15", "30", "0", "0", "0", "0", "1"], ["5", "10", "10", "5", "0", "0", "1"]], zt)
            js("state.tables.clear(); renderHealth()")
            settle(300)
            page.click("#h-zones .zones-list li:last-child .zone-open")   # the run on 8 September: its details
            page.wait_for_selector("#drawer.open .zones-tbl", timeout=5000)
            rows_ = js("[...document.querySelectorAll('#dbody .zones-tbl tbody tr')].map(r => [...r.cells].map(c => c.textContent))")
            dz = page.inner_text("#dbody")
            check("…a workout's details: the time in each zone with its heart rate range, and where the zones come from",
                  rows_ == [["Zone 5", "167 bpm and above", "0 min"], ["Zone 4", "148–166 bpm", "5 min (17%)"], ["Zone 3", "130–147 bpm", "10 min (33%)"],
                            ["Zone 2", "111–129 bpm", "10 min (33%)"], ["Zone 1", "93–110 bpm", "5 min (17%)"], ["Below zone 1", "under 93 bpm", "0 min"]]
                  and "Average 130 bpm (zone 3) · highest 162 bpm (zone 4)" in dz and "an estimated maximum heart rate of 185 bpm: 220 minus age 35, the common formula" in dz, [rows_, dz[-400:]])
            js("closeDrawer(); openEvent(EV.find(e => e.k === 'exercise' && e.ts === zParse('2026-09-12T22:00:00+00:00')))")
            page.wait_for_selector("#drawer.open .zones-one", timeout=5000)
            dz = page.inner_text("#dbody")
            check("…a workout with only its average and highest: both against the zones, and why there's no time in each",
                  "Average 128 bpm (zone 2) · highest 158 bpm (zone 4)" in dz and "only its average and highest" in dz and not js("!!document.querySelector('#dbody .zones-tbl')"), dz[-300:])
            js("closeDrawer()")
            # a measured maximum, typed in About this person
            page.goto(base + "/sharing.html?profile=1#about")
            page.wait_for_selector("#about[data-profile='1'] #aboutMaxHr", timeout=10000)
            page.fill("#aboutMaxHr", "190")
            page.click("#aboutSave")
            page.wait_for_function("document.getElementById('aboutMsg').textContent === 'Saved.'", timeout=10000)
            check("…a measured maximum heart rate typed in About this person (owners and editors)",
                  js("fetch('api/profiles/1/person').then(r => r.json())")["maxHr"] == 190 and "220 minus the age" in page.inner_text("#about"))
            page.goto(base + "/")
            page.wait_for_function("typeof booted !== 'undefined' && booted && PROFILE.id === 1", timeout=20000)
            js("state.tables.clear(); setRange(dayTs('2026-08-01'), dayTs('2026-11-14')); switchTab('health')")
            settle(500)
            mz = js("[document.querySelector('#h-zones .hint').textContent, WATCH.exercise.filter(x => x.hr).map(x => [x.hr.zmax, x.hr.basis, x.hr.zones || null])]")
            check("…replaces the estimate for every workout, and the card says so",
                  "the maximum heart rate entered in About this person, 190 bpm" in mz[0] and mz[1][3] == [190, "measured", [0, 0, 0, 600, 900, 300]]
                  and all(z[0] == 190 for z in mz[1]), mz)
            bad, axe_z = [], []
            theme_before = js("document.documentElement.dataset.theme || null")
            for width in (1400, 375):
                page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                for theme in ("light", "dark"):
                    js("t => { document.documentElement.dataset.theme = t; }", theme)
                    for table in (False, True):
                        js("on => { closeDrawer(); state.tables.clear(); if (on) state.tables.add('#h-zones'); renderHealth(); }", table)
                        settle(300)
                        page.locator("#h-zones").scroll_into_view_if_needed()
                        if width == 375:
                            bad += phone_fits(page, f"heart rate zones {'table' if table else 'chart'} ({theme})")
                        axe_z += axe_check(page, f"heart rate zones {'table' if table else 'chart'}, {width} px, {theme}")[0]
                    js("openEvent(EV.find(e => e.k === 'exercise' && e.ts === zParse('2026-09-08T11:00:00+00:00')))")
                    page.wait_for_selector("#drawer.open .zones-tbl", timeout=5000)
                    settle(200)
                    if width == 375:
                        bad += phone_fits(page, f"a workout's zones in the drawer ({theme})")
                    axe_z += axe_check(page, f"a workout's zones in the drawer, {width} px, {theme}")[0]
                    js("closeDrawer()")
            check("…the card, its table and a workout's details fit 375 px, and axe-core finds nothing serious at 1400 and 375 px, light and dark",
                  not bad and not axe_z, (bad + axe_z)[:6])
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            page.set_viewport_size({"width": 1400, "height": 900})
            check("…(the birth date and maximum cleared again for the checks after)", put_person({"birthDate": "", "maxHr": 0}) == 200)
            page.goto(base + "/")
            page.wait_for_function("typeof booted !== 'undefined' && booted && PROFILE.id === 1", timeout=20000)
            js("w => { state.tables.clear(); [state.preset, state.from, state.to] = w; state.sel = new Set(w[4]); switchTab(w[3]); save(); }", was197)
            settle(300)

            # ---- BMI over time (#195): with a height in About this person's history (typed in cm, then in feet and
            # inches), each weight's BMI with the height in effect on its day, as server/person.py works it out; under
            # the weight chart, in a tile and in the card's table; the number only, until the WHO's adult categories
            # are turned on (named as the WHO's, remembered); the calculation in kg and lb, cm and ft/in; under 20 on a
            # weight's day, never the adult categories, and a child's BMI only from a height of the 3 months before;
            # 1400 and 375 px, light and dark, axe clean
            from server import person as person_b
            was195 = js("[state.preset, state.from, state.to, state.tab]")
            api195 = lambda m, path, body=None: js("([m, p, b]) => fetch('api/' + p, {method: m, headers: {'X-Sanguity': '1', 'Content-Type': 'application/json'}, "
                                                   "body: b == null ? undefined : JSON.stringify(b)}).then(r => r.status)", [m, path, body])

            def health195():
                page.reload()
                page.wait_for_function("typeof booted !== 'undefined' && booted && PROFILE.id === 1", timeout=20000)
                js("state.tables.clear(); setRange(dayTs('2025-06-01'), dayTs('2026-11-14')); switchTab('health')")
                settle(500)
            js("state.tables.clear(); setRange(dayTs('2025-06-01'), dayTs('2026-11-14')); switchTab('health')")
            settle(400)
            none = page.inner_text("#h-body .bmi") if page.locator("#h-body .bmi").count() else ""
            check("BMI (#195): with no height, the weight card says a height in About this person gives each weight's BMI, and there's no BMI tile",
                  "With a height under About this person" in none and js("document.querySelector('#h-body .bmi a').getAttribute('href')") == "sharing.html?profile=1#about"
                  and "BMI" not in js("[...document.querySelectorAll('#view-health .tile .lbl')].map(l => l.textContent)"), none)
            st195 = [api195("POST", "profiles/1/person/heights", {"date": "2019-01-10", "unit": "cm", "cm": 178}),
                     api195("POST", "profiles/1/person/heights", {"date": "2026-03-02", "unit": "ftin", "ft": 5, "inches": 9.9})]
            health195()
            hs195 = js("fetch('api/profiles/1/person').then(r => r.json())")["heights"]
            got = js("H.bmi.map(r => [isoDay(r.t), r.w[1], r.v, r.h.date, r.child])")
            want = [person_b.bmi(w_, "lb", person_b.height_on(hs195, d_)["cm"]) for d_, w_, *_ in got]
            check("…each weight's BMI with the height in effect on its day (both heights used), as the server works it out",
                  st195 == [200, 200] and len(got) == js("H.scale.length + H.clinic.length") and all(abs(g[2] - w_) < 1e-9 for g, w_ in zip(got, want))
                  and {g[3] for g in got} == {"2019-01-10", "2026-03-02"} and all((g[0] >= "2026-03-02") == (g[3] == "2026-03-02") for g in got) and not any(g[4] for g in got),
                  (st195, got[:2], want[:2]))
            units = js("[bmiOf(70, 'kg', 175), bmiOf(154.32358352941432, 'lb', 175), bmiOf(160, 'lb', 175.26), bmiOf(180, 'lb', 180), bmiOf(0, 'kg', 170), bmiOf(70, 'st', 170), bmiOf(70, 'kg', null)]")
            want_u = [person_b.bmi(70, "kg", 175), person_b.bmi(154.32358352941432, "lb", 175), person_b.bmi(160, "lb", person_b.from_ftin(5, 9)), person_b.bmi(180, "lb", 180), None, None, None]
            check("…the page's calculation in kg and lb, with heights in cm and in feet and inches, the server's", all(
                (u is None and w_ is None) or (u is not None and w_ is not None and abs(u - w_) < 1e-9) for u, w_ in zip(units, want_u)) and abs(units[0] - 22.857142857) < 1e-8, units)
            cats = js("[16, 18.44, 18.45, 24.94, 24.95, 29.96, 30, 41.2].map(v => whoBmi(v).range)")
            check("…the WHO's categories read at one decimal, as BMI is shown, the same as the server", cats == [person_b.who_bmi(v)["range"] for v in (16, 18.44, 18.45, 24.94, 24.95, 29.96, 30, 41.2)], cats)
            tiles = js("[...document.querySelectorAll('#view-health .tile')].map(t => [t.querySelector('.lbl').textContent, t.querySelector('.val').textContent, t.querySelector('.delta').textContent])")
            bt = next((t for t in tiles if t[0] == "BMI"), None)
            last195 = js("(() => { const r = H.bmi.filter(r => r.t >= state.from && r.t <= state.to).at(-1); return [bmiRound(r.v).toFixed(1), fmtD(r.t)]; })()")
            body195 = page.inner_text("#h-body")
            check("…the latest in a tile beside the weight's, the number only (no category) by default",
                  bt and [x[0] for x in tiles].index("BMI") == [x[0] for x in tiles].index("Weight") + 1 and bt[1] == last195[0] + "kg/m²" and bt[2] == "latest " + last195[1]
                  and "World Health Organization" not in body195 and "WHO" not in bt[2] and not js("document.getElementById('bmiWho').checked") and js("document.querySelectorAll('#h-body .band-label').length") == 0
                  and not re.search(r"(?i)\b(normal|overweight|obes|underweight|healthy|ideal|target|goal)", body195), (bt, last195))
            check("…a chart under the weight's, labelled and said in words", js("document.querySelector('#h-body .bmi svg').getAttribute('aria-label')").startswith("BMI: ")
                  and "each weight with the height in effect on its day" in page.inner_text("#h-body .bmi .hint"))
            page.click("#bmiWho")
            settle(400)
            bands = js("[...document.querySelectorAll('#h-body .bmi .band-label')].map(t => t.textContent)")
            bt = next(t for t in js("[...document.querySelectorAll('#view-health .tile')].map(t => [t.querySelector('.lbl').textContent, t.querySelector('.delta').textContent])") if t[0] == "BMI")
            check("…turned on, the WHO's categories for adults as labelled bands, the tile naming the WHO's range, the hint saying whose they are",
                  bands and all(b_.startswith("WHO ") for b_ in bands) and bt[1] == "WHO: " + person_b.who_bmi(float(last195[0]))["range"]
                  and "World Health Organization's categories of BMI for adults" in page.inner_text("#h-body .bmi") and "not advice" in page.inner_text("#h-body .bmi"), (bands, bt))
            page.focus("#h-body .bmi svg")
            page.keyboard.press("End")
            settle(150)
            tipt = js("tip.innerText")
            page.keyboard.press("Escape")
            check("…the chart steps from the keyboard: the BMI, the weight and where it was taken, the height used, the WHO's category",
                  tipt.startswith("BMI " + last195[0]) and "lb on the smart scale" in tipt and "Height 5 ft 9.9 in, from" in tipt and "The WHO's category for adults: " in tipt, tipt)
            page.click("#h-body .view-seg button:text-is('Table')")
            settle(300)
            tb195 = js("(() => { const t = document.querySelector('#h-body table.data'); return [[...t.querySelectorAll('thead th')].map(h => h.textContent), "
                       "[...t.querySelectorAll('tbody tr')].map(r => [...r.children].map(c => c.textContent))]; })()")
            hd = tb195[0]
            check("…Table has each weight's BMI and, turned on, the WHO's category",
                  hd[:5] == ["Date", "Weight (lb)", "From", "BMI (kg/m²)", "WHO adult category"] and tb195[1][0][3] == last195[0]
                  and tb195[1][0][4] == js(f"whoWords({float(last195[0])})") and "(“" in tb195[1][0][4], (hd, tb195[1][:1]))
            page.click("#h-body .view-seg button:text-is('Chart')")
            health195()
            check("…the choice is remembered for this profile", js("hState.bmiWho") and js("document.getElementById('bmiWho').checked"))
            bad, axe195 = [], []
            theme_before = js("document.documentElement.dataset.theme || null")
            for width in (1400, 375):
                page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                for theme in ("light", "dark"):
                    js("t => { document.documentElement.dataset.theme = t; }", theme)
                    for table in (False, True):
                        js("on => { state.tables.clear(); if (on) state.tables.add('#h-body'); renderHealth(); }", table)
                        settle(300)
                        page.locator("#h-body").scroll_into_view_if_needed()
                        if width == 375:
                            bad += phone_fits(page, f"BMI {'table' if table else 'chart'} ({theme})")
                        axe195 += axe_check(page, f"BMI {'table' if table else 'chart'}, {width} px, {theme}")[0]
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            page.set_viewport_size({"width": 1400, "height": 900})
            check("…fits 375 px, and axe-core finds nothing serious at 1400 and 375 px, light and dark, with the WHO's categories on", not bad and not axe195, (bad + axe195)[:6])
            # under 20 on a weight's day: no adult categories, and a height from the 3 months before the weight
            st195 = api195("PUT", "profiles/1/person", {"birthDate": "2008-01-15"})
            health195()
            kid = js("H.bmi.map(r => [isoDay(r.t), r.h.date, r.age, r.child])")
            check("…for someone under 20 on a weight's day: no adult categories offered, and only the weights of the 3 months after a height",
                  st195 == 200 and kid and all(k[3] and k[2] < 20 and k[1] == "2026-03-02" and "2026-03-02" <= k[0] <= "2026-06-02" for k in kid)
                  and not js("!!document.getElementById('bmiWho')") and js("document.querySelectorAll('#h-body .band-label').length") == 0
                  and "Under 20, it's shown without the adult categories" in page.inner_text("#h-body .bmi"), kid[:3])
            # put back: no birth date, no heights, the categories off
            st195 = [api195("PUT", "profiles/1/person", {"birthDate": ""}), api195("DELETE", "profiles/1/person/heights/2019-01-10"),
                     api195("DELETE", "profiles/1/person/heights/2026-03-02")]
            js("hState.bmiWho = false; hSave()")
            page.reload()
            page.wait_for_function("typeof booted !== 'undefined' && booted && PROFILE.id === 1", timeout=20000)
            js("w => { state.tables.clear(); [state.preset, state.from, state.to] = w; switchTab(w[3]); save(); }", was195)
            settle(300)
            check("…and put back as it was (no height, no birth date)", st195 == [200, 200, 200] and js("!personOf().heights.length && !personOf().birthDate"), st195)

            # ---- each result against its own range (#58): the demo's creatinine (0.7–1.3 mg/dL) with two results from
            # an outside lab (0.6–1.2, add_range_records): the chart's band steps with each result's range and its
            # header says the ranges differ; its table gives each row its own; 1.25 is high by its own lab's range though
            # inside the clinic's; the heatmap and the drawer judge each result by its own; a series whose ranges agree
            # has one band and no note; the chart and its table fit 375 px, in light and dark
            js("window._was58 = [new Set(state.sel), state.preset, state.from, state.to]; state.tables.clear(); state.sel = new Set(['Creatinine', 'Hemoglobin']); applyPreset('all'); switchTab('labs', 'chart'); renderLabs()")
            settle(400)
            cr_card = "[...document.querySelectorAll('#charts .chart')].find(c => c.querySelector('.name').textContent === 'Creatinine')"
            hb_card = "[...document.querySelectorAll('#charts .chart')].find(c => c.querySelector('.name').textContent === 'Hemoglobin')"
            note = js(f"{cr_card}.querySelector('.hd .rdiff')?.textContent || ''")
            check("own ranges (#58): a chart whose results' ranges differ says which, in its header",
                  note.startswith("Reference range differs between results: 0.7–1.3 and 0.6–1.2 mg/dL.") and "steps with each result's own range" in note, note)
            bands = js(f"""[...{cr_card}.querySelectorAll('svg .refband')].map(r => [+r.getAttribute('y'), +r.getAttribute('height'), +r.getAttribute('x'), +r.getAttribute('width')])""")
            geo = js(f"{cr_card}.querySelector('svg')._geom")
            check("…its band steps with each result's own range: two ranges, five steps (clinic, outside, clinic, outside, clinic) across the chart",
                  len(bands) == 5 and len({(round(b[0], 1), round(b[1], 1)) for b in bands}) == 2
                  and abs(bands[0][2] - geo["L"]) < .5 and abs(sum(b[3] for b in bands) - (geo["R"] - geo["L"])) < 1, [bands, geo])
            check("…its edges are each one stepped line", js(f"{cr_card}.querySelectorAll('svg path.refedge').length") == 2
                  and "V" in js(f"{cr_card}.querySelector('svg path.refedge').getAttribute('d')"))
            hi = js("SERIES.find(s => s.name === 'Creatinine').pts.filter(p => p[4]).map(p => [p[0], p[1], p[2], p[3], p[4]])")
            check("…1.25 mg/dL is high by its own lab's range (0.6–1.2), though inside the clinic's, and is marked ▲",
                  ["2026-09-10T09:05:00", 1.25, 0.6, 1.2, "H"] in hi and js(f"{cr_card}.querySelectorAll('svg .pt.abn').length") == len(hi), hi)
            check("…the chart says so to screen readers",
                  "Reference range differs between results: 0.7–1.3 and 0.6–1.2 mg/dL" in js(f"{cr_card}.querySelector('svg').getAttribute('aria-label')"))
            check("…a chart whose results' ranges agree has one band and no note",
                  js(f"{hb_card}.querySelectorAll('svg .refband').length") == 1 and not js(f"!!{hb_card}.querySelector('.rdiff')"))
            js("state.tables.add('Creatinine'); renderCharts()")
            settle(200)
            rows = js(f"""[...{cr_card}.querySelectorAll('table.data tbody tr')].map(r => [...r.cells].map(c => c.textContent))""")
            outside = [r for r in rows if r[0].startswith(("Jul 15, 2026", "Sep 10, 2026"))]
            check("…its table gives each row its own range, and the note says so",
                  len(outside) == 2 and all(r[2].startswith("0.6 – 1.2") for r in outside)
                  and all(r[2].startswith("0.7 – 1.3") for r in rows if r not in outside) and "▲ High" in [r[3] for r in outside]
                  and js(f"{cr_card}.querySelector('.rdiff').textContent").endswith("Each row gives its own."), outside)
            js("state.tables.clear(); switchTab('labs', 'heatmap')")
            settle(400)
            hm = js("""(() => { const s = SERIES.find(s => s.name === 'Creatinine'), p = s.pts.find(p => p[0].startsWith('2026-09-10'));
                return [hmLevel(p[1], p[2], p[3]), [...document.querySelectorAll('#hm text.row')].find(t => t.firstChild.textContent === 'Creatinine')?.querySelector('title').textContent]; })()""")
            check("heatmap (#58): the outside lab's 1.25 is coloured high against its own range; the row's name says the ranges differ",
                  hm[0] > 0 and "Reference range differs between results: 0.7–1.3 and 0.6–1.2 mg/dL" in (hm[1] or ""), hm)
            js("openEvent(EV.find(e => e.k === 'lab' && e.t.startsWith('2026-09-10')))")   # its id is the database's
            page.wait_for_selector("#drawer.open #dbody table.data", timeout=10000)
            dr = js("[...document.querySelectorAll('#dbody table.data tbody tr')].map(r => [...r.cells].map(c => c.textContent)).find(c => c[0].includes('Creatinine'))")
            check("drawer (#58): the outside lab's result with its own range, said High", dr and re.match(r"0\.6 ?[-–] ?1\.2", dr[2]) and "High" in dr[-1], dr)
            page.keyboard.press("Escape")
            js("switchTab('overview')")
            settle(300)
            sp = js("""[...document.querySelectorAll('#view-overview .tile')].find(t => t.textContent.includes('Creatinine'))?.querySelectorAll('svg .spark-ref').length ?? -1""")
            check("Overview (#58): the creatinine tile's sparkline shades each result's own range, stepped", sp == 5, sp)
            # 375 px, light and dark: the note under the name, in the chart and in the table
            theme_before = js("document.documentElement.dataset.theme || null")
            page.set_viewport_size({"width": 375, "height": 800})
            bad = []
            for theme in ("light", "dark"):
                js("t => { document.documentElement.dataset.theme = t; }", theme)
                for table in (False, True):
                    js("on => { state.tables.clear(); if (on) state.tables.add('Creatinine'); switchTab('labs', 'chart'); }", table)
                    settle(400)
                    bad += phone_fits(page, f"Creatinine {'table' if table else 'chart'} ({theme})")
                    look = js(f"""(n => n && [getComputedStyle(n).color, getComputedStyle(document.body).backgroundColor, n.getBoundingClientRect().right])({cr_card}.querySelector('.rdiff'))""")
                    if not look or look[0] == look[1] or look[2] > 375:
                        bad.append(f"note ({theme}, {'table' if table else 'chart'}): {look}")
            check("own ranges (#58): the chart and table with the note fit 375 px and read, in light and dark", not bad, bad[:6])
            page.set_viewport_size({"width": 1400, "height": 900})
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            js("state.tables.clear(); [state.sel, state.preset, state.from, state.to] = window._was58; state.labsView = 'chart'; save()")

            # ---- detail drawer for every kind of event; none writes "null" or "undefined" where a part is left out (#245),
            # such as a workout without heart rate's zones or a hospital stay's note
            said_null = []
            nulls_js = "/\\b(null|undefined)\\b/.test(document.getElementById('drawer').innerText)"
            for k in js("[...new Set(EV.filter(e => !e.hidden).map(e => e.k))].sort()"):
                js("k => openEvent(EV.find(e => e.k === k && !e.hidden))", k)
                settle(200)
                ok = js("document.getElementById('drawer').classList.contains('open') && document.getElementById('dtitle').textContent.length > 0")
                check(f"drawer opens for {k}", ok)
                if js(nulls_js):
                    said_null.append(k)
                page.keyboard.press("Escape")
            check("drawer closes on Escape", not js("document.getElementById('drawer').classList.contains('open')"))
            for what, find in (("a workout without heart rate", "e => e.k === 'exercise' && !e.hidden && !DATA.details[e.id]?.hr"),
                               ("a hospital stay", "e => e.k === 'stay' && !e.hidden")):
                found = js(f"(() => {{ const e = EV.find({find}); if (!e) return false; return Promise.resolve(openEvent(e)).then(() => true); }})()")
                settle(200)
                if not found or js(nulls_js):
                    said_null.append(what if found else f"{what}: none in the demo")
                page.keyboard.press("Escape")
            check("…and no drawer says “null” or “undefined” (#245): every kind, a workout without heart rate, a hospital stay", not said_null, said_null)

            # ---- immunizations and allergies (#179): the Overview lists the allergies with their reactions, severity and
            # criticality as recorded (not the resolved one, and not again in the problem list) and the immunizations newest
            # first; Enter on one opens it in the drawer with what the record says; a pharmacy fill (a FHIR
            # MedicationDispense's) shows its pharmacy and supply; the Timeline has the kind's lane and chip in its own
            # colour, 3:1 against the page in light and dark; at 1400 and 375 px in both themes it fits and axe finds nothing
            open_tab("overview")
            settle(300)
            al = js("[...document.querySelectorAll('#ovAllergies .ov-row')].map(r => [r.querySelector('.n').textContent, r.querySelector('.s').textContent])")
            check("immunizations (#179): the Overview lists allergies with their reactions, severity and criticality as recorded, not the resolved one",
                  al == [["Penicillin G", "Reaction: Hives · Severity: Moderate · Criticality: High"], ["Peanuts", "Reaction: Anaphylaxis, Rash · Severity: Severe"]], al)
            check("…and not again in the problem list", not js("[...document.querySelectorAll('#view-overview .card')].some(c => /Active problem list/.test(c.textContent) && /Penicillin/.test(c.textContent))"))
            im = js("[...document.querySelectorAll('#ovImmunizations .ov-row')].map(r => [r.querySelector('.n').textContent, r.querySelector('.r').textContent, r.querySelector('.s').textContent])")
            check("…and the immunizations, newest first, with the dose and where", [[n, s] for n, _, s in im] ==
                  [["Influenza vaccine", "Dose 1 · Fictional Clinic"], ["COVID-19 vaccine", "Fictional County Health Department"],
                   ["Tdap vaccine", "Dose 1 · Fictional Corner Pharmacy"]] and all(r for _, r, _ in im), im)
            page.focus("#ovImmunizations .ov-row")
            page.keyboard.press("Enter")
            settle(300)
            kv = js("Object.fromEntries([...document.querySelectorAll('#dbody dl.kv dt')].map(dt => [dt.textContent, dt.nextElementSibling.textContent]))")
            want = {"Vaccine": "Influenza vaccine", "Code": "CVX 150", "Dose": "1", "Amount": "0.5 mL", "Route": "Intramuscular", "Site": "Left deltoid",
                    "Lot": "FICT-FLU-11", "Made by": "Fictional Vaccines Inc.", "Given by": "Sam Ortega, RN", "Where": "Fictional Clinic", "Status": "Completed"}
            check("…Enter on one opens it in the drawer, said as an Immunization, with what the record says",
                  js("document.getElementById('drawer').classList.contains('open')") and js("document.getElementById('dtitle').textContent") == "Influenza vaccine"
                  and js("document.getElementById('dsub').textContent").startswith("Immunization · ") and {k: kv.get(k) for k in want} == want, kv)
            imm_axe = axe_check(page, "immunization drawer, light")
            page.keyboard.press("Escape")
            js("openEvent(EV.find(e => e.title === 'Fictionalol 25 mg tablet'))")
            settle(200)
            kv = js("Object.fromEntries([...document.querySelectorAll('#dbody dl.kv dt')].map(dt => [dt.textContent, dt.nextElementSibling.textContent]))")
            check("…a pharmacy fill from FHIR shows its pharmacy, quantity and supply, not a medication list's note",
                  {k: kv.get(k) for k in ("Pharmacy", "Quantity", "Days' supply", "Prescribed by")} ==
                  {"Pharmacy": "Fictional Corner Pharmacy", "Quantity": "30 tablet", "Days' supply": "30", "Prescribed by": "Dr. Rowan Vale"}
                  and "medication list" not in js("document.getElementById('dbody').textContent"), kv)
            page.keyboard.press("Escape")
            open_tab("timeline")
            js("applyPreset('all'); state.cats.add('immunization'); renderAll()")
            settle(300)
            lane = js("""(() => { const c = CATS.find(c => c.k === 'immunization'), chip = document.querySelector('#chips .toggle[data-k=immunization]');
              const marks = [...document.querySelectorAll('#tl circle.ev')].filter(m => byId.get(+m.dataset.id)?.k === 'immunization');
              return {chip: chip && chip.getAttribute('aria-label'), label: [...document.querySelectorAll('#tl .lane-label')].map(t => t.textContent).includes('Immunizations'),
                      marks: marks.length, fill: marks[0] && getComputedStyle(marks[0]).fill, key: chip && getComputedStyle(chip.querySelector('.key')).backgroundColor}; })()""")
            check("…the Timeline has an Immunizations lane, its marks in the kind's colour, and a chip saying how many",
                  lane["chip"] == "Immunizations, 3, shown" and lane["label"] and lane["marks"] == 3 and lane["fill"] == lane["key"], lane)
            theme_before, bad, axe_bad = js("document.documentElement.dataset.theme || null"), [], list(imm_axe[0])
            for theme in ("light", "dark"):
                js("t => { document.documentElement.dataset.theme = t; }", theme)
                for width in (1400, 375):
                    page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                    open_tab("timeline")
                    settle(250)
                    c = js("""(() => { const m = [...document.querySelectorAll('#tl circle.ev')].find(m => byId.get(+m.dataset.id)?.k === 'immunization');
                      return [getComputedStyle(m).fill, getComputedStyle(document.querySelector('.card') || document.body).backgroundColor, getComputedStyle(document.body).backgroundColor]; })()""")
                    if min(contrast(c[0], c[1]), contrast(c[0], c[2])) < 3:
                        bad.append(f"{theme} {width}: the lane's colour {c[0]} is {min(contrast(c[0], c[1]), contrast(c[0], c[2]))}:1 against the page")
                    if width == 375:
                        bad += phone_fits(page, f"timeline, {theme}")
                    axe_bad += axe_check(page, f"timeline {theme} {width}")[0]
                    open_tab("overview")
                    settle(250)
                    page.locator("#ovImmunizations").scroll_into_view_if_needed()
                    if width == 375:
                        bad += phone_fits(page, f"overview, {theme}")
                        box = js("[...document.querySelectorAll('#ovAllergies, #ovImmunizations')].map(c => c.getBoundingClientRect().right)")
                        if len(box) != 2 or max(box) > 375:
                            bad.append(f"{theme}: the cards stick out at 375 px {box}")
                    axe_bad += axe_check(page, f"overview {theme} {width}")[0]
                    page.focus("#ovImmunizations .ov-row")
                    page.keyboard.press("Enter")
                    settle(300)
                    if width == 375:
                        bad += phone_fits(page, f"immunization drawer, {theme}")
                    axe_bad += axe_check(page, f"immunization drawer {theme} {width}")[0]
                    page.keyboard.press("Escape")
                    settle(200)
            page.set_viewport_size({"width": 1400, "height": 900})
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            check("…the lane is 3:1 against the page, and the Timeline, Overview and drawer fit 375 px, in light and dark", not bad, bad[:6])
            check("…and axe-core finds nothing serious on them at 1400 and 375 px, in light and dark (#202)", not axe_bad, axe_bad[:6])
            open_tab("overview")
            settle(200)

            # ---- menstrual cycle tracking, opt-in (#183): off, the receiver's cycle.json (the fixture, both phone apps')
            # shows nowhere; an owner turns it on under Sources → Phone and watch, asked first with who sees it and where
            # it never goes; then a Timeline lane of its own (periods as bars, the rest as marks), its chip, the drawer,
            # and a row under the Labs charts when ticked; never on the Overview; a viewer's page doesn't ask for it;
            # Export everything and the visit summary offer it unticked; deleting hides it at once, the receiver deletes
            # it and the card says what deleting can't reach; turning it off deletes too. At 1400 and 375 px, light and
            # dark, the lane is 3:1 against the page, everything fits and axe finds nothing serious.
            from server import crypto as _crypto
            keys_doc = lambda: json.loads(_crypto.read_file(os.environ["SANGUITY_WATCH_KEYS"]))
            cy_axe, cy_bad, theme_before = [], [], js("document.documentElement.dataset.theme || null")
            check("cycle tracking (#183): off by default, the receiver's file for this profile shows nowhere",
                  os.path.isfile(os.path.join(TMP, "watch", "cycle.json")) and not js("CYCLE.length || EV.some(e => e.k === 'cycle') || CATS.filter(hasLane).some(c => c.k === 'cycle')")
                  and not js("!!document.getElementById('optCycle')"))
            page.goto(base + "/sources.html?profile=1#phone")
            page.wait_for_selector("#cycle h2", timeout=10000)
            check("…Sources → Phone and watch has a Cycle tracking card saying it's off and nothing is kept",
                  page.inner_text("#cycleState").startswith("Off.") and "left out of every upload before anything is kept" in page.inner_text("#cycleState")
                  and "Not reached: backups" in page.inner_text("#cycle"), page.inner_text("#cycle")[:200])
            for theme in ("light", "dark"):
                js("t => { document.documentElement.dataset.theme = t; }", theme)
                for width in (1400, 375):
                    page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                    settle(200)
                    cy_axe += axe_check(page, f"Sources cycle card off, {theme} {width}")[0]
                    if width == 375:
                        cy_bad += phone_fits(page, f"Sources cycle card off, {theme}")
            page.set_viewport_size({"width": 1400, "height": 900})
            page.click("#cycleOn")
            asked = answer_ask(page, yes=True)
            page.wait_for_function("document.getElementById('cycleState') && document.getElementById('cycleState').textContent.startsWith('On since')", timeout=10000)
            check("…turning it on asks first, saying who sees it, where it never goes and that sexual activity is never kept",
                  asked[0] == "Turn on cycle tracking?" and all(w in asked[1] for w in ("Viewers don't", "never in a share link", "Sexual activity is never kept", "delete all of it")), asked)
            check("…then the receiver is told (its secrets file lists this profile's slot)", keys_doc().get("cycle") == [""], keys_doc().get("cycle"))
            for theme in ("light", "dark"):
                js("t => { document.documentElement.dataset.theme = t; }", theme)
                for width in (1400, 375):
                    page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                    settle(200)
                    cy_axe += axe_check(page, f"Sources cycle card on, {theme} {width}")[0]
                    if width == 375:
                        cy_bad += phone_fits(page, f"Sources cycle card on, {theme}")
            page.set_viewport_size({"width": 1400, "height": 900})
            page.goto(base + "/")
            page.wait_for_function("typeof booted !== 'undefined' && booted", timeout=20000)
            open_tab("timeline")
            js("applyPreset('all'); state.cats.add('cycle'); renderAll()")
            settle(300)
            lane = js("""(() => { const chip = document.querySelector('#chips .toggle[data-k=cycle]');
              const bars = [...document.querySelectorAll('#tl rect.cymark')], marks = [...document.querySelectorAll('#tl .cymark:not(rect)')];
              return {chip: chip && chip.getAttribute('aria-label'), label: [...document.querySelectorAll('#tl .lane-label')].map(t => t.textContent).includes('Cycle tracking'),
                      bars: bars.length, marks: marks.length, spans: CYCLE.filter(e => e.cy.kind === 'span').map(e => [e.cy.from, e.cy.to, e.cy.logged, e.title]),
                      fill: bars[0] && getComputedStyle(bars[0]).fill, key: chip && getComputedStyle(chip.querySelector('.key')).backgroundColor,
                      rows: [...document.querySelectorAll('#list .tl-row')].filter(r => r.textContent.includes('Cycle tracking')).length}; })()""")
            check("…on: a Cycle tracking lane on the Timeline, both apps' periods as bars and the other records as marks, in its colour, with a chip",
                  lane["label"] and lane["chip"] == "Cycle tracking, 37, shown" and lane["bars"] == 12 and lane["marks"] == 25 and lane["fill"] == lane["key"]
                  and lane["spans"][0] == ["2026-01-04", "2026-01-08", False, "Flow recorded on 5 days"] and lane["spans"][-1][2:] == [True, "Period, 5 days"]
                  and lane["rows"] > 0, {k: lane[k] for k in ("label", "chip", "bars", "marks", "fill", "key", "rows")} | {"first": lane["spans"][:1], "last": lane["spans"][-1:]})
            page.locator("#tl rect.cymark").last.click()
            settle(300)
            kv = js("Object.fromEntries([...document.querySelectorAll('#dbody dl.kv dt')].map(dt => [dt.textContent, dt.nextElementSibling.textContent]))")
            check("…a bar opens the drawer: the period as logged, its flow day by day, said as recorded and not advice",
                  js("document.getElementById('dtitle').textContent") == "Period, 5 days" and js("document.getElementById('dsub').textContent").startswith("Cycle tracking · ")
                  and kv.get("Recorded as") == "A period, logged as one" and kv.get("From the app") == "Health Connect"
                  and js("[...document.querySelectorAll('#dbody table tbody tr')].map(r => r.cells[1].textContent)") == ["light", "medium", "heavy", "medium", "light"]
                  and "doesn't predict periods or fertile days" in page.inner_text("#dbody"), kv)
            cy_axe += axe_check(page, "cycle drawer, light")[0]
            page.keyboard.press("Escape")
            said_words = js("CYCLE.map(e => e.title + ' ' + e.sub).join(' | ')")
            check("…every record in words that describe, none that predict or judge",
                  not re.search(r"(?i)predict|fertil|expected|next period|likely|window|irregular|normal|should", said_words), said_words[:300])
            open_tab("overview")
            settle(200)
            check("…never on the Overview's recent list", "Cycle tracking" not in page.inner_text("#view-overview") and not re.search(r"Period, \d|Flow recorded|Ovulation test", page.inner_text("#view-overview")))
            open_tab("labs")
            settle(300)
            check("…Labs offers “Cycle tracking” under Show on charts, unticked", js("!!document.getElementById('optCycle') && !document.getElementById('optCycle').checked")
                  and not js("document.querySelectorAll('#charts .cymark').length"))
            page.check("#optCycle")
            settle(400)
            n_marks = js("document.querySelectorAll('#charts .chart svg .cymark').length")
            check("…ticked, each chart gets a row of them, with a legend", n_marks > 0 and js("!document.getElementById('cycleLegend').hidden"), n_marks)
            page.focus("#charts .chart svg .cymark")
            page.keyboard.press("Enter")
            settle(300)
            check("…a mark there takes focus and Enter opens it in the drawer", js("document.getElementById('drawer').classList.contains('open') && document.getElementById('dsub').textContent.startsWith('Cycle tracking')"))
            page.keyboard.press("Escape")
            for theme in ("light", "dark"):
                js("t => { document.documentElement.dataset.theme = t; }", theme)
                for width in (1400, 375):
                    page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                    open_tab("labs")
                    settle(300)
                    if width == 375:
                        cy_bad += phone_fits(page, f"Labs with cycle tracking, {theme}")
                    cy_axe += axe_check(page, f"Labs with cycle tracking {theme} {width}")[0]
                    open_tab("timeline")
                    settle(300)
                    c = js("""(() => { const m = document.querySelector('#tl rect.cymark');
                      return [getComputedStyle(m).fill, getComputedStyle(document.querySelector('.card') || document.body).backgroundColor, getComputedStyle(document.body).backgroundColor]; })()""")
                    if min(contrast(c[0], c[1]), contrast(c[0], c[2])) < 3:
                        cy_bad.append(f"{theme} {width}: the lane's colour {c[0]} is {min(contrast(c[0], c[1]), contrast(c[0], c[2]))}:1 against the page")
                    if width == 375:
                        cy_bad += phone_fits(page, f"timeline with cycle tracking, {theme}")
                    cy_axe += axe_check(page, f"timeline with cycle tracking {theme} {width}")[0]
                    js("openEvent(CYCLE.find(e => e.cy.kind === 'mucus'))")
                    settle(300)
                    if width == 375:
                        cy_bad += phone_fits(page, f"cycle drawer, {theme}")
                    cy_axe += axe_check(page, f"cycle drawer {theme} {width}")[0]
                    page.keyboard.press("Escape")
                    settle(150)
            page.set_viewport_size({"width": 1400, "height": 900})
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            open_tab("labs")
            page.uncheck("#optCycle")
            check("…a viewer's page doesn't ask for it", js("""(async () => { const was = PROFILE.role, seen = []; const f = window.fetch;
              window.fetch = (u, o) => { seen.push(String(u)); return f(u, o); }; PROFILE.role = 'viewer';
              const before = CYCLE.length; await cycleLoad(); const n = CYCLE.length; PROFILE.role = was; window.fetch = f;
              await cycleLoad(); return before > 0 && n === 0 && !seen.some(u => u.includes('/cycle')); })()"""))
            page.goto(base + "/sharing.html?profile=1#export")
            page.wait_for_selector("#exportCycleWrap:not([hidden])", timeout=10000)
            href = lambda: js("document.getElementById('exportLink').getAttribute('href')")
            plain = href()
            page.check("#exportCycle")
            check("…Export everything offers the cycle records unticked, ticked for that export only",
                  plain == "api/profiles/1/export" and href() == "api/profiles/1/export?cycle=1", [plain, href()])
            page.uncheck("#exportCycle")
            cy_axe += axe_check(page, "Sharing export with the cycle tick")[0]
            # ---- Export as FHIR (#205): the format, what to include and a note of what a bundle has no place for; the
            # cycle tick goes with it; the download a FHIR R4 bundle; at 1400 and 375 px, light and dark
            import io as _io, zipfile as _zip
            fx_bad, fx_axe, fx_theme = [], [], js("document.documentElement.dataset.theme || null")
            page.check("input[name=exportFormat][value=fhir]")
            check("Export everything offers FHIR R4: chosen, it lists what to include, all ticked, and what a bundle has no place for",
                  js("!document.getElementById('exportParts').hidden && [...document.querySelectorAll('input[name=exportPart]')].every(x => x.checked)")
                  and "the journal, settings" in page.inner_text("#exportNotMapped") and page.inner_text("#exportLink") == "Export as FHIR"
                  and href() == "api/profiles/1/export?format=fhir", [href(), page.inner_text("#exportLink")])
            page.uncheck("input[name=exportPart][value=claims]")
            page.check("#exportCycle")
            check("…unticking one names the others in the link, and the cycle tick goes with it",
                  href() == "api/profiles/1/export?format=fhir&include=labs,visits,problems,meds,immunizations,procedures,documents,messages,"
                            "home,ecg,person&cycle=1", href())
            got = page.request.get(base + "/" + href())
            fz = _zip.ZipFile(_io.BytesIO(got.body())) if got.ok else None
            fb = json.loads(fz.read("bundle.json")) if fz else {}
            kinds = {e["resource"]["resourceType"] for e in fb.get("entry", [])}
            check("…and downloads a FHIR R4 bundle of what was ticked: no claims, the cycle records, the documents' files beside it",
                  fb.get("resourceType") == "Bundle" and "ExplanationOfBenefit" not in kinds and {"DiagnosticReport", "Encounter", "DocumentReference"} <= kinds
                  and "cycle_flow" in json.dumps(fb) and any(n.startswith("documents/") for n in fz.namelist())
                  and got.headers.get("content-disposition", "").endswith('-fhir.zip"'), [got.status, sorted(kinds)])
            page.uncheck("#exportCycle")
            page.check("input[name=exportPart][value=claims]")
            for x in js("[...document.querySelectorAll('input[name=exportPart]')].map(x => x.value)"):
                page.uncheck(f"input[name=exportPart][value={x}]")
            page.click("#exportLink")
            page.wait_for_selector("#exportMsg.bad", timeout=5000)
            check("…with nothing ticked it says so and doesn't download", page.inner_text("#exportMsg") == "Tick at least one kind of record to include.")
            for x in js("[...document.querySelectorAll('input[name=exportPart]')].map(x => x.value)"):
                page.check(f"input[name=exportPart][value={x}]")
            for theme in ("light", "dark"):
                js("t => { document.documentElement.dataset.theme = t; }", theme)
                for width in (1400, 375):
                    page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                    settle(200)
                    if width == 375:
                        fx_bad += phone_fits(page, f"Sharing export as FHIR, {theme}")
                    fx_axe += axe_check(page, f"Sharing export as FHIR {theme} {width}")[0]
            page.set_viewport_size({"width": 1400, "height": 900})
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", fx_theme)
            check("…it fits at 375 px, and axe-core finds nothing serious at 1400 and 375 px in light and dark", not fx_bad and not fx_axe, (fx_bad + fx_axe)[:6])
            page.check("input[name=exportFormat][value=sanguity]")
            check("…and Sanguity's own again: the list goes, the link is the plain export", js("document.getElementById('exportParts').hidden")
                  and href() == "api/profiles/1/export" and page.inner_text("#exportLink") == "Export everything", href())
            page.goto(base + "/sharing.html?profile=1#summary")
            page.wait_for_selector("#sections input[value=cycle]", state="attached", timeout=10000)
            check("…the visit summary offers it too, unticked each time while the other sections are ticked",
                  js("!document.querySelector('#sections input[value=cycle]').checked && [...document.querySelectorAll('#sections input')].filter(i => i.value !== 'cycle' && !OPTS.sections.find(x => x.id === i.value).off).every(i => i.checked)"))   # #225: measures unticked too
            page.goto(base + "/sources.html?profile=1#phone")
            page.wait_for_selector("#cycleDelete", timeout=10000)
            page.click("#cycleDelete")
            asked = answer_ask(page, yes=True)
            page.wait_for_selector("#cycleDeleted", timeout=10000)
            check("…Delete all cycle data asks, saying what it can't reach; then nothing is shown until the receiver has done it",
                  asked[0] == "Delete all cycle data?" and "can't reach them" in asked[1] and "Deleting what was kept" in page.inner_text("#cycleState")
                  and js("fetch('api/profiles/1/cycle').then(r => r.json()).then(c => c.spans.length + c.marks.length)") == 0
                  and keys_doc().get("cycle_purge", {}).get(""), [asked, page.inner_text("#cycleState")])
            cycle_receiver_sweep()   # the receiver's worker, between uploads
            rx_store = CYCLE_RX["store"]
            page.reload()
            page.wait_for_selector("#cycleDeleted", timeout=10000)
            check("…the receiver deletes its copy and every archived upload's, and the card says when",
                  rx_store.db.execute("SELECT COUNT(*) FROM cycle").fetchone()[0] == 0 and page.inner_text("#cycleDeleted").startswith("Last deleted")
                  and page.inner_text("#cycleState").endswith("Nothing received yet."), page.inner_text("#cycle")[:300])
            page.click("#cycleOff")
            asked = answer_ask(page, yes=True)
            page.wait_for_function("document.getElementById('cycleState').textContent.startsWith('Off.')", timeout=10000)
            check("…turning it off asks, deletes too and tells the receiver", asked[1].startswith("Turning it off deletes everything kept") and keys_doc().get("cycle") == [],
                  [asked, keys_doc().get("cycle")])
            cycle_receiver_sweep()
            check("…the cycle pages fit 375 px and the lane is 3:1 against the page, in light and dark", not cy_bad, cy_bad[:6])
            check("…and axe-core finds nothing serious on them at 1400 and 375 px, in light and dark (#202)", not cy_axe, cy_axe[:6])
            page.goto(base + "/")
            page.wait_for_function("typeof booted !== 'undefined' && booted", timeout=20000)
            settle(300)

            # ---- ECG recordings and irregular rhythm notifications (#182, add_ecg_watch): a lane and chip of their own in a
            # colour 3:1 against the page; the page's data holds no samples, loaded only when a recording's drawer opens; the
            # drawer says what the device said (classification word for word, average heart rate, device, lead, software)
            # and nothing of its own; the trace is drawn, takes focus with a text summary, and ← → step through it saying
            # the voltage; its summary table and "Download as CSV"; a notification is a hollow mark with no trace; at 1400
            # and 375 px in light and dark it fits and axe finds nothing serious (screenshots too, when SANGUITY_SHOTS names a folder)
            shots = os.environ.get("SANGUITY_SHOTS")
            open_tab("timeline")
            js("applyPreset('all'); state.cats.add('ecg'); renderAll()")
            settle(300)
            lane = js("""(() => { const chip = document.querySelector('#chips .toggle[data-k=ecg]');
              const marks = [...document.querySelectorAll('#tl circle.ev')].filter(m => ['ecg', 'rhythm'].includes(byId.get(+m.dataset.id)?.k));
              return {chip: chip && chip.getAttribute('aria-label'), label: [...document.querySelectorAll('#tl .lane-label')].map(t => t.textContent).includes('ECG & rhythm'),
                      filled: marks.filter(m => byId.get(+m.dataset.id).k === 'ecg').map(m => getComputedStyle(m).fill),
                      hollow: marks.filter(m => byId.get(+m.dataset.id).k === 'rhythm').map(m => getComputedStyle(m).stroke),
                      key: chip && getComputedStyle(chip.querySelector('.key')).backgroundColor,
                      samples: /"samples":\\[/.test(JSON.stringify(WATCH)), n: (WATCH.ecg || []).length}; })()""")
            check("ECG (#182): the Timeline has an ECG & rhythm lane, three recordings as dots and two notifications as rings in its colour, and a chip",
                  lane["chip"] == "ECG & rhythm, 5, shown" and lane["label"] and len(lane["filled"]) == 3 and len(lane["hollow"]) == 2
                  and set(lane["filled"]) == set(lane["hollow"]) == {lane["key"]}, lane)
            check("…the page's watch data lists the five events (phone and export merged by key) and holds none of the samples",
                  lane["n"] == 5 and not lane["samples"], lane)
            js("openEvent(EV.find(e => e.k === 'ecg' && /Atrial/.test(e.title)))")
            page.wait_for_selector("#dbody svg.ecg-svg polyline", timeout=10000)
            kv = js("Object.fromEntries([...document.querySelectorAll('#dbody dl.kv dt')].map(dt => [dt.textContent, dt.nextElementSibling.textContent]))")
            want = {"Classification": "Atrial Fibrillation", "Average heart rate": "81 bpm", "Symptoms": "Rapid, pounding, or fluttering heartbeat",
                    "Recorded with": "Fictional Watch", "Software version": "2.0"}
            check("…a recording's drawer says what the device said: its classification word for word, average heart rate, symptoms, device",
                  js("document.getElementById('dtitle').textContent") == "ECG: Atrial Fibrillation"
                  and js("document.getElementById('dsub').textContent").startswith("ECG (watch) · ") and {k: kv.get(k) for k in want} == want, kv)
            text = js("document.getElementById('dbody').textContent")
            check("…and no word of Sanguity's own on it: not normal or abnormal, and not medical advice said",
                  not re.search(r"(?i)\b(ab)?normal\b|irregular heartbeat detected|healthy", text) and "isn't medical advice" in text, text[:300])
            tr = js("""(() => { const s = document.querySelector('#dbody svg.ecg-svg');
              return {rows: s.querySelectorAll('polyline.ecg-line').length, pts: [...s.querySelectorAll('polyline')].reduce((n, p) => n + p.points.length, 0),
                      ticks: [...s.querySelectorAll('.ecg-tick')].map(t => t.textContent), tab: s.getAttribute('tabindex'), role: s.getAttribute('role'),
                      label: s.getAttribute('aria-label'), sum: Object.fromEntries([...document.querySelectorAll('#dbody table.ecg-sum tr')].map(r => [r.cells[0] ? r.querySelector('th').textContent : '', r.querySelector('td').textContent])),
                      edits: !!document.querySelector('#dbody .edit-acts')}; })()""")
            check("…its trace is drawn small, three rows of 10 seconds with a time and mV scale, each column's highs and lows",
                  tr["rows"] == 3 and 1500 < tr["pts"] < 4000 and {"0 s", "10 s", "20 s", "30 s", "+1 mV", "−1 mV", "0"} <= set(tr["ticks"]), {k: tr[k] for k in ("rows", "pts")})
            check("…it takes focus with a text summary for screen readers: what it is, how long, the device's classification, and the keys",
                  tr["tab"] == "0" and tr["role"] == "application" and all(w in (tr["label"] or "") for w in ("30 seconds", "512 Hz", "Lead I", "Atrial Fibrillation", "arrow keys")), tr["label"])
            check("…and a summary table in place of 15,360 rows: duration, sample rate, lead, classification, samples",
                  {k: tr["sum"].get(k) for k in ("Duration", "Sample rate", "Lead", "Classification", "Samples")} ==
                  {"Duration": "30 s", "Sample rate": "512 Hz", "Lead": "Lead I", "Classification": "Atrial Fibrillation", "Samples": "15,360"}
                  and not tr["edits"], tr["sum"])
            page.focus("#dbody svg.ecg-svg")
            page.keyboard.press("ArrowRight")
            first = js("document.querySelector('#dbody .ecg-readout').textContent")
            for _ in range(3):
                page.keyboard.press("ArrowRight")
            later = js("document.querySelector('#dbody .ecg-readout').textContent")
            page.keyboard.press("End")
            settle(150)
            end = js("document.querySelector('#dbody .ecg-readout').textContent")
            heard = js("document.getElementById('sgStatus').textContent")
            cursor = js("document.querySelector('#dbody .ecg-cursor').getAttribute('visibility')")
            check("…← → step through it half a second at a time and End goes to its end, saying the voltage there",
                  re.fullmatch(r"At 0 s: −?[\d.]+ mV", first.replace("-", "−")) and later.startswith("At 1.5 s: ") and end.startswith("At 30 s: ")
                  and heard.endswith(end) and cursor == "visible", [first, later, end, heard, cursor])
            page.keyboard.press("Escape")
            check("…Escape lets the trace go, and the drawer stays open", js("document.querySelector('#dbody .ecg-cursor').getAttribute('visibility')") == "hidden"
                  and js("document.getElementById('drawer').classList.contains('open')"))
            with page.expect_download(timeout=20000) as dl:
                page.click("#dbody button:has-text('Download as CSV')")
            with open(dl.value.path(), encoding="utf-8") as f:
                lines = f.read().splitlines()
            check("…Download as CSV gives every sample with its time, in µV", re.fullmatch(r"ecg-2026-10-0[34]-\d{4}\.csv", dl.value.suggested_filename)
                  and lines[0] == "Time (s),Voltage (µV)" and len(lines) == 15361 and lines[1].startswith("0,") and lines[-1].startswith("29.998"),
                  [dl.value.suggested_filename, lines[:2], lines[-1:], len(lines)])
            ecg_axe = list(axe_check(page, "ECG drawer, light")[0])
            page.keyboard.press("Escape")
            js("openEvent(EV.find(e => e.k === 'rhythm'))")
            settle(200)
            kv = js("Object.fromEntries([...document.querySelectorAll('#dbody dl.kv dt')].map(dt => [dt.textContent, dt.nextElementSibling.textContent]))")
            check("…a notification's drawer says what it is, when, the period it covers and the device, with no trace",
                  js("document.getElementById('dtitle').textContent") == "Irregular rhythm notification" and kv.get("Recorded with") == "Fictional Watch"
                  and "Period it covers" in kv and not js("!!document.querySelector('#dbody svg.ecg-svg')"), kv)
            page.keyboard.press("Escape")
            theme_before, bad = js("document.documentElement.dataset.theme || null"), []
            for theme in ("light", "dark"):
                js("t => { document.documentElement.dataset.theme = t; }", theme)
                for width in (1400, 375):
                    page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                    open_tab("timeline")
                    settle(250)
                    c = js("""(() => { const m = [...document.querySelectorAll('#tl circle.ev')].find(m => byId.get(+m.dataset.id)?.k === 'ecg');
                      return [getComputedStyle(m).fill, getComputedStyle(document.querySelector('.card') || document.body).backgroundColor, getComputedStyle(document.body).backgroundColor]; })()""")
                    if min(contrast(c[0], c[1]), contrast(c[0], c[2])) < 3:
                        bad.append(f"{theme} {width}: the lane's colour {c[0]} is {min(contrast(c[0], c[1]), contrast(c[0], c[2]))}:1 against the page")
                    if width == 375:
                        bad += phone_fits(page, f"timeline with ECGs, {theme}")
                    js("openEvent(EV.find(e => e.k === 'ecg' && /Sinus/.test(e.title)))")
                    page.wait_for_selector("#dbody svg.ecg-svg polyline", timeout=10000)
                    settle(150)
                    ink = js("""(() => { const l = document.querySelector('#dbody .ecg-line'), t = document.querySelector('#dbody .ecg-tick'), bg = getComputedStyle(document.querySelector('#dbody .ecg-svg')).backgroundColor;
                      return [getComputedStyle(l).stroke, getComputedStyle(t).fill, bg, document.querySelector('#dbody .ecg-svg').getBoundingClientRect().right, document.getElementById('drawer').getBoundingClientRect().right]; })()""")
                    if contrast(ink[0], ink[2]) < 4.5 or contrast(ink[1], ink[2]) < 4.5:
                        bad.append(f"{theme} {width}: the trace {ink[0]} or its scale {ink[1]} is faint against {ink[2]}")
                    cur = js(CHART_ACCENT_JS, "#dbody svg.ecg-svg")
                    if cur["bad"] or not cur["cursors"]:   # #334: its cursor is --cursor, not the accent
                        bad.append(f"{theme} {width}: the trace's cursor or marks are in the accent, or it has no cursor {cur}")
                    if ink[3] > ink[4] + 0.5:
                        bad.append(f"{theme} {width}: the trace sticks out of the drawer {ink[3:]}")
                    if width == 375:
                        bad += phone_fits(page, f"ECG drawer, {theme}")
                        page.screenshot(path=os.path.join(shots, f"182-drawer-375-{theme}.png")) if shots else None
                    elif shots:
                        page.screenshot(path=os.path.join(shots, f"182-drawer-1400-{theme}.png"))
                    ecg_axe += axe_check(page, f"ECG drawer {theme} {width}")[0]
                    page.keyboard.press("Escape")
                    settle(200)
                    if shots:
                        page.screenshot(path=os.path.join(shots, f"182-timeline-{width}-{theme}.png"))
                    ecg_axe += axe_check(page, f"timeline with ECGs {theme} {width}")[0]
            page.set_viewport_size({"width": 1400, "height": 900})
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            check("…the lane is 3:1 and the trace 4.5:1 against the page, nothing on it in the accent (#334), and the Timeline and drawer fit 375 px, in light and dark", not bad, bad[:6])
            check("…and axe-core finds nothing serious on them at 1400 and 375 px, in light and dark (#202)", not ecg_axe, ecg_axe[:6])
            open_tab("overview")
            settle(200)

            # ---- renaming where a name is shown (#38): a lab from its Trends chart shows the new name on every tab and in
            # the drawer, the name in the records on hover; back to it from the heatmap; an import from the drawer's
            # Source line; a source tag from under a Trends table; the form fits 375 px; viewers get no ✎
            lab = "Hemoglobin"
            js("n => { switchTab('labs'); applyPreset('all'); state.sel.add(n); renderLabs(); }", lab)
            settle(300)
            page.click(f"#charts .chart .hd [data-rename=lab][data-key='{lab}']")
            page.wait_for_selector(".rename-box input", timeout=5000)
            check("rename: ✎ on a Trends chart opens a form with the name in it", js("document.querySelector('.rename-box input').value") == lab)
            page.fill(".rename-box input", "Hgb (renamed)")
            page.keyboard.press("Enter")
            page.wait_for_function("n => CFG.labNicknames[n] === 'Hgb (renamed)' && !document.querySelector('.rename-box')", arg=lab, timeout=10000)
            settle(300)
            hd = js("n => { const b = document.querySelector(`#charts .chart .hd [data-key='${n}']`), h = b && b.parentElement; return h && [h.querySelector('.name').textContent, h.querySelector('.name').title]; }", lab)
            check("…the chart is called by the new name, the name in the records on hover", hd == ["Hgb (renamed)", f"In the records: {lab}"], hd)
            check("…and so is the list of labs", "Hgb (renamed)" in js("[...document.querySelectorAll('#series .nm')].map(x => x.textContent)"))
            check("…focus goes back to its ✎", js("document.activeElement && document.activeElement.dataset.rename") == "lab")
            check("…kept on the server", js("n => api('profiles/1/settings').then(r => r.json()).then(j => j.labNicknames[n])", lab) == "Hgb (renamed)")
            js("switchTab('overview')")
            settle(300)
            check("…the Overview tile too", js("n => [...document.querySelectorAll('#view-overview .tile .lbl span[title]')].filter(s => s.title === n).map(s => s.textContent)", lab) == ["Hgb (renamed)"])
            js("switchTab('heatmap')")
            settle(300)
            row = f"#hm text.row[data-key='{lab}']"
            check("…the heatmap row too", page.locator(row).count() == 1 and page.locator(row).text_content().startswith("Hgb (renamed)"),
                  page.locator(row).text_content() if page.locator(row).count() else None)
            rid = js("async n => { await loadDetails(DATA.events.map(e => e.id)); const e = EV.find(e => (DATA.details[e.id] || {}).type === 'result' && (DATA.details[e.id].rows || []).some(r => r[0] === n)); return e && e.id; }", lab)
            js("id => openEvent(byId.get(id))", rid)
            settle(200)
            cell = js("n => { const td = [...document.querySelectorAll('#dbody table.data td')].find(td => td.title === 'In the records: ' + n); return td && td.textContent; }", lab)
            check("…and the drawer's results, with the name in the records beside it", cell and "Hgb (renamed)" in cell and f"({lab})" in cell, cell)
            page.keyboard.press("Escape")
            page.click(row)
            page.wait_for_selector(".rename-box", timeout=5000)
            page.click(".rename-box button:text-is('Use the original name')")
            page.wait_for_function("n => !(n in CFG.labNicknames) && !document.querySelector('.rename-box')", arg=lab, timeout=10000)
            settle(300)
            check("…and back to the name in the records from the heatmap", page.locator(row).text_content().startswith(lab))
            # an import, from the drawer's Source line
            js("id => openEvent(byId.get(id))", rid)
            settle(200)
            imp = js("id => byId.get(id).imp", rid)
            label = js("i => DATA.imports[i].label", imp)
            page.click("#dbody > .source [data-rename=import]")
            page.wait_for_selector(".rename-box input", timeout=5000)
            page.fill(".rename-box input", "Fictional clinic portal")
            page.keyboard.press("Enter")
            page.wait_for_function("i => DATA.imports[i].name === 'Fictional clinic portal' && !document.querySelector('.rename-box')", arg=imp, timeout=10000)
            settle(200)
            src = js("document.querySelector('#dbody > .source') && document.querySelector('#dbody > .source').textContent")
            check("rename: an import from the drawer, its label kept beside it", js("document.getElementById('drawer').classList.contains('open')")
                  and src and "Fictional clinic portal" in src and f"({label})" in src, src)
            check("…in the import history too", js("i => api('profiles/1/imports').then(r => r.json()).then(j => j.imports.find(e => e.id === i).name)", imp) == "Fictional clinic portal")
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            page.click("#dbody > .source [data-rename=import]")
            page.wait_for_selector(".rename-box input", timeout=5000)
            box = js("(() => { const r = document.querySelector('.rename-box').getBoundingClientRect(); return [r.left, r.right, document.documentElement.scrollWidth]; })()")
            check("…the rename form fits 375 px", box[0] >= 0 and box[1] <= 375 and box[2] <= 375, box)
            page.keyboard.press("Escape")
            check("…Escape closes the form, not the drawer", js("!document.querySelector('.rename-box') && document.getElementById('drawer').classList.contains('open')"))
            page.set_viewport_size({"width": 1400, "height": 900})
            settle(200)
            page.click("#dbody > .source [data-rename=import]")
            page.wait_for_selector(".rename-box", timeout=5000)
            page.click(".rename-box button:text-is('Use the original name')")
            page.wait_for_function("i => !DATA.imports[i].name", arg=imp, timeout=10000)
            page.keyboard.press("Escape")
            # a source tag, from under the Trends table (the demo's results carry none, so one is given here)
            js("n => { switchTab('labs', 'chart'); const s = SERIES.find(s => s.name === n); s.pts[s.pts.length - 1][6] = 'EXT'; state.tables.add(n); renderLabs(); }", lab)
            settle(200)
            page.click("#charts .tags [data-rename=tag][data-key=EXT]")
            page.wait_for_selector(".rename-box input", timeout=5000)
            page.fill(".rename-box input", "Outside lab")
            page.keyboard.press("Enter")
            page.wait_for_function("CFG.sources.EXT === 'Outside lab' && !document.querySelector('.rename-box')", timeout=10000)
            settle(200)
            check("rename: a source tag from under the Trends table, shown in the table",
                  js("[...document.querySelectorAll('#charts table.data td')].some(td => td.textContent.includes('Outside lab'))")
                  and "Outside lab (EXT)" in js("document.querySelector('#charts .tags').textContent"))
            page.click("#charts .tags [data-rename=tag][data-key=EXT]")
            page.wait_for_selector(".rename-box", timeout=5000)
            page.click(".rename-box button:text-is('Use the original name')")
            page.wait_for_function("!('EXT' in CFG.sources)", timeout=10000)
            check("…viewers get no ✎", js("""(() => { const role = PROFILE.role; PROFILE.role = 'viewer'; renderLabs();
                const n = document.querySelectorAll('#charts [data-rename], #dbody [data-rename]').length; PROFILE.role = role; return n; })()""") == 0)
            js("n => { const s = SERIES.find(s => s.name === n); s.pts[s.pts.length - 1].length = 6; state.tables.delete(n); renderLabs(); }", lab)

            # ---- an HTML record shows in the drawer with the stylesheet and picture saved beside it, scripts off
            js("openEvent(DATA.events.find(e => e.title === 'Visit summary (HTM)'))")   # ids are the database's
            page.wait_for_function("""(() => { const f = document.querySelector('#dbody iframe.doc'), d = f && f.contentDocument;
                return d && d.getElementById('i') && d.getElementById('i').complete && d.readyState === 'complete'; })()""", timeout=10000)
            settle(300)
            look = js("""(() => { const f = document.querySelector('#dbody iframe.doc'), d = f.contentDocument;
                return {color: getComputedStyle(d.getElementById('t')).color, img: d.getElementById('i').naturalWidth,
                        sandbox: f.getAttribute('sandbox')}; })()""")
            check("HTML record loads its own stylesheet and picture", look["color"] == "rgb(255, 0, 0)" and look["img"] == 1, look)
            check("HTML record can't run scripts", "allow-scripts" not in (look["sandbox"] or "") and look["sandbox"] is not None, look["sandbox"])
            # ---- an HTML record opened in its own tab (Open in new tab) can't send it elsewhere (#221): it's served with
            # "sandbox allow-same-origin" in its CSP, so its refresh is refused as in the drawer's frame; PDFs and
            # pictures are served without it, so they still open. A context of its own: bypass_csp would drop the sandbox.
            doc_src = js("document.querySelector('#dbody iframe.doc').getAttribute('src')")
            pdf_src = js("(async () => { await loadDetails(DATA.events.map(e => e.id)); return Object.values(DATA.details).find(d => d && d.type === 'file' && (d.ext || '').toLowerCase() === 'pdf' && d.url)?.url; })()")
            page.keyboard.press("Escape")
            tab_ctx =browser.new_context(viewport={"width": 1400, "height": 900})
            tab_ctx.add_cookies(page.context.cookies())
            tab = tab_ctx.new_page()
            tab.on("request", elsewhere)
            doc_abs = urllib.parse.urljoin(page.url, doc_src)
            served = tab.goto(doc_abs)
            tab.wait_for_timeout(2000)   # the record asks for its refresh after 1 second
            csp = lambda r: r.headers.get("content-security-policy") or ""   # noqa: E731
            check("an HTML record opened in its own tab stays put: its refresh doesn't move the tab (#221)",
                  tab.url == doc_abs and tab.inner_text("#t") == "Visit summary" and "sandbox allow-same-origin" in csp(served),
                  [tab.url, csp(served)])
            pic = tab_ctx.request.get(doc_abs.rsplit("/", 1)[0] + "/visit%20summary_files/logo.png")
            pdf = tab_ctx.request.get(urllib.parse.urljoin(page.url, pdf_src)) if pdf_src else None
            check("…a PDF and a record's picture are served without the sandbox, so they still open",
                  pdf is not None and pdf.status == 200 and pdf.headers["content-type"] == "application/pdf" and "sandbox" not in csp(pdf)
                  and pic.status == 200 and pic.headers["content-type"] == "image/png" and "sandbox" not in csp(pic) and "script-src 'none'" in csp(pic),
                  [pdf_src, pdf and pdf.status, pdf and csp(pdf), pic.status, csp(pic)])
            tab_ctx.close()
            problems[:] = [x for x in problems if "Refused to execute the redirect" not in x]   # the drawer's frame refused it too

            # ---- corrections (#33): hide an event from the drawer, see it leave the timeline, undo; rename shows "Edited"
            page.click("#tab-timeline")
            js("applyPreset('all')")
            settle()
            pick = js("""(() => { const e = EV.find(e => !['exercise', 'labitem', 'lab'].includes(e.k) && EV.filter(x => x.title === e.title).length === 1);
                return e && {id: e.id, t: e.t, title: e.title}; })()""")
            there = "t => EV.some(e => e.title === t.title && e.t === t.t)"
            listed = "t => [...document.querySelectorAll('#list .tl-row')].some(r => r.textContent.includes(t.title))"
            check("an event to correct is on the timeline", pick and js(f"({there})", pick) and js(f"({listed})", pick), pick)
            js("id => openEvent(byId.get(id))", pick["id"])
            settle(200)
            page.click("#dbody .edit-acts button:text-is('Hide')")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && document.getElementById('drawer').classList.contains('open') && "
                                   "document.getElementById('dtitle').textContent === 'Edits to these records'", timeout=20000)
            settle()
            check("hidden from the drawer: the event leaves the timeline", not js(f"({there})", pick) and not js(f"({listed})", pick))
            check("…and the panel lists the edit, with Undo", pick["title"] in page.inner_text("#dbody") and page.locator("#dbody button[data-edit]").count() == 1,
                  page.inner_text("#dbody")[:200])
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the edits panel fits 375 px", js("document.documentElement.scrollWidth <= 375 && document.getElementById('dbody').scrollWidth <= document.getElementById('dbody').clientWidth"),
                  js("[document.documentElement.scrollWidth, document.getElementById('dbody').scrollWidth]"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.click("#dbody button[data-edit]")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && (DATA.edits || []).length === 0 && booted", timeout=20000)
            settle()
            check("undoing the edit brings it back", js(f"({there})", pick) and js(f"({listed})", pick))
            again = js(f"(t => EV.find(e => e.title === t.title && e.t === t.t).id)", pick)
            js("id => openEvent(byId.get(id))", again)
            settle(200)
            page.click("#dbody .edit-acts button:text-is('Rename')")
            page.fill("#dbody .edit-form input", "Renamed in the browser check")
            page.click("#dbody .edit-form button:text-is('Save')")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && document.getElementById('dtitle').textContent === 'Renamed in the browser check'", timeout=20000)
            check("a rename shows in the drawer, marked Edited", "Edited" in page.inner_text("#dbody .edited") and page.locator("#dbody .edited button[data-edit]").count() == 1)
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the event panel with its corrections fits 375 px", js("document.documentElement.scrollWidth <= 375 && document.getElementById('dbody').scrollWidth <= document.getElementById('dbody').clientWidth"),
                  js("[document.documentElement.scrollWidth, document.getElementById('dbody').scrollWidth]"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.click("#dbody .edited button[data-edit]")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && (DATA.edits || []).length === 0 && booted", timeout=20000)
            check("…and undoing it restores the title", js(f"({there})", pick))
            page.keyboard.press("Escape")

            # ---- chart activity (#236): the demo's chart review and phone call aren't visits; a billing-code title is a plain one
            got = js("""(async () => { await loadDetails(EV.filter(e => e.k === 'visit').map(e => e.id));   // loaded when opened (#217)
                const billed = EV.find(e => e.k === 'visit' && (DATA.details[e.id] || {}).billed);
                return {contact: EV.filter(e => e.k === 'contact').map(e => e.title).sort(), cat: CATS.some(c => c.k === 'contact'),
                        billed: billed && [billed.title, DATA.details[billed.id].billed], pr: EV.some(e => /^PR /.test(e.title))}; })()""")
            check("chart reviews and calls are Chart activity, and a billing-code visit has a plain name (#236)",
                  got["contact"] == ["Chart review", "Telephone"] and got["cat"] and not got["pr"]
                  and got["billed"] == ["Office visit", "PR OFFICE/OUTPATIENT ESTABLISHED MOD MDM 30 MIN"], got)
            # ---- change category (#237): the chart review back to a visit from its drawer, then undone
            js("openEvent(EV.find(e => e.title === 'Chart review'))")
            try:   # its details load as it opens (#217)
                page.wait_for_function("/no visit took place|wasn't a visit/.test(document.getElementById('dbody').textContent)", timeout=10000)
            except Exception:
                pass
            check("…its drawer says it wasn't a visit", "wasn't a visit" in page.inner_text("#dbody") or "no visit took place" in page.inner_text("#dbody"),
                  page.inner_text("#dbody")[:200])
            page.click("#dbody .edit-acts button:text-is('Change category')")
            page.wait_for_function("document.querySelector('#dbody .similar') && !document.querySelector('#dbody .similar .hint')", timeout=10000)
            check("Change category offers the other categories and a rule for new imports",
                  js("[...document.querySelectorAll('#dbody .edit-form select option')].map(o => o.value).includes('visit') && "
                     "![...document.querySelectorAll('#dbody .edit-form select option')].some(o => o.value === 'contact')")
                  and page.locator("#dbody input[data-similar=rule]").count() == 1)
            page.select_option("#dbody .edit-form select", "visit")
            page.click("#dbody .edit-form button:text-is('Save')")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && booted && (DATA.edits || []).some(x => x.action === 'kind')", timeout=20000)
            settle()
            check("…moves it to Visits, marked Edited", js("EV.find(e => e.title === 'Chart review').k === 'visit'")
                  and "moved to Visit" in page.inner_text("#dbody .edited"), page.inner_text("#dbody")[:300])
            page.click("#dbody .edited button[data-edit]")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && (DATA.edits || []).length === 0 && booted", timeout=20000)
            check("…and Undo puts it back in Chart activity", js("EV.find(e => e.title === 'Chart review').k === 'contact'"))
            # ---- similar entries (#237): one rename for every physical therapy visit from the same source (not the claims of that name), undone at once
            before_pt = js("EV.filter(e => e.k === 'visit' && e.title === 'Physical therapy session').length")
            js("openEvent(EV.find(e => e.k === 'visit' && e.title === 'Physical therapy session'))")
            settle(200)
            page.click("#dbody .edit-acts button:text-is('Rename')")
            page.wait_for_function("document.querySelector('#dbody .similar input[data-similar=all]')", timeout=10000)
            offer = re.search(r"Also the (\d+) other entries titled “Physical therapy session” from (.+)", page.inner_text("#dbody .similar"))
            n_pt = int(offer.group(1)) + 1 if offer else 0
            same_src = js("""(() => { const e = EV.find(e => e.k === 'visit' && e.title === 'Physical therapy session'), imps = DATA.imports || {};
                const src = x => { const i = imps[x.imp] || {}; return `${i.origin}|${i.format}`; };
                const raw = new Map(DATA.events.map(x => [x.id, x]));   // the records as loaded carry their import
                return EV.filter(x => x.k === e.k && x.title === e.title && src(raw.get(x.id) || {}) === src(raw.get(e.id) || {})).length; })()""")
            check("Rename offers the other entries with the same title from the same source", offer and n_pt == same_src > 1,
                  [page.inner_text("#dbody .similar"), same_src])
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("…and the form fits 375 px", js("document.documentElement.scrollWidth <= 375 && document.getElementById('dbody').scrollWidth <= document.getElementById('dbody').clientWidth"),
                  js("[document.documentElement.scrollWidth, document.getElementById('dbody').scrollWidth]"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.fill("#dbody .edit-form input[type=text]", "PT session")
            page.click("#dbody .edit-form button:text-is('Save')")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && booted && (DATA.edits || []).length > 0", timeout=20000)
            settle()
            check("…renaming them all at once, and only them", js("EV.filter(e => e.k === 'visit' && e.title === 'PT session').length") == n_pt
                  and js("EV.filter(e => e.k === 'visit' && e.title === 'Physical therapy session').length") == before_pt - n_pt,
                  [n_pt, before_pt, js("EV.filter(e => e.k === 'visit' && e.title === 'PT session').length")])
            check("…with one Undo for all of them", page.inner_text("#dbody .edited button[data-edit]") == f"Undo all {n_pt}",
                  page.inner_text("#dbody .edited"))
            page.click("#dbody .edited button[data-edit]")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && (DATA.edits || []).length === 0 && booted", timeout=20000)
            check("…which brings every title back", js("EV.filter(e => e.k === 'visit' && e.title === 'Physical therapy session').length") == before_pt)
            page.keyboard.press("Escape")

            # ---- the journal (#49): a symptom added from an event's drawer, pre-dated to it, shown in the Journal lane
            # and as a marker on Trends sized by its severity; its home on the Timeline tab with the filters (#173); deleted
            # again at the end
            check("the journal starts empty", js("JOURNAL.length === 0 && !document.getElementById('journalBtn').hidden"))
            js("switchTab('overview')")
            menu_click(page, "#journalBtn")
            settle(200)
            check("Add → Journal entry opens the new-entry form, over the tab you were on (#173)",
                  js("document.getElementById('dtitle').textContent === 'New journal entry' && state.tab === 'overview'")
                  and page.input_value("#dbody input[type=date]") == js("isoDay(Date.now())") and page.is_visible("#dbody .jform input[type=text]"),
                  js("[document.getElementById('dtitle').textContent, state.tab]"))
            check("…saying whose journal, with more than one profile", js("document.getElementById('dsub').textContent").endswith(" · My records"),
                  js("document.getElementById('dsub').textContent"))
            page.click("#dbody .jform button:text-is('Cancel')")
            settle(300)
            check("…and Cancel closes it, giving focus back to Add", js("!drawer.classList.contains('open') && document.activeElement.id === 'addMenuBtn'"),
                  js("document.activeElement.id"))
            page.click("#tab-timeline")
            settle()
            home = js("""(() => { const c = document.getElementById('journalHome'), cards = [...document.querySelectorAll('#view-timeline > .card')];
                return c && {title: c.querySelector('h2').textContent, at: cards.indexOf(c), tl: cards.findIndex(x => x.contains(document.getElementById('tl'))),
                ev: cards.findIndex(x => x.contains(document.getElementById('list'))), add: !!c.querySelector('button.primary'), rows: c.querySelectorAll('.jrow').length}; })()""")
            check("the journal has a home on the Timeline tab, between the timeline and the events, with New entry even when empty",
                  home and home["title"] == "Journal" and home["tl"] < home["at"] < home["ev"] and home["add"] and home["rows"] == 0, home)
            day_of = js("(() => { const e = EV.find(e => e.k === 'visit' && !e.hidden && /T\\d\\d:\\d\\d/.test(e.t)); openEvent(e); return {id: e.id, day: isoDay(e.ts)}; })()")
            settle(200)
            page.click("#dbody .jadd button")
            check("“Add a journal note” from an event's drawer opens the form dated to it",
                  js("document.getElementById('dtitle').textContent") == "New journal entry" and page.input_value("#dbody input[type=date]") == day_of["day"],
                  page.input_value("#dbody input[type=date]"))
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the journal form fits 375 px", js("document.documentElement.scrollWidth <= 375 && document.getElementById('dbody').scrollWidth <= document.getElementById('dbody').clientWidth"),
                  js("[document.documentElement.scrollWidth, document.getElementById('dbody').scrollWidth]"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.focus("#dbody .jkinds button[aria-checked=true]")   # the kind by keyboard: Note -> Symptom
            page.keyboard.press("ArrowRight")
            check("the kind is chosen with the arrow keys", js("document.activeElement.dataset.kind") == "symptom" and page.is_visible("#dbody input[type=range]"))
            page.fill("#dbody .jform input[type=text]", "Headache (browser check)")
            page.focus("#dbody input[type=range]")
            page.keyboard.press("Home")
            for _ in range(7):
                page.keyboard.press("ArrowRight")
            check("severity set by keyboard, said in words", page.inner_text("#dbody output") == "7/10 · severe", page.inner_text("#dbody output"))
            meds = js("[...document.querySelectorAll('#dbody select option')].map(o => o.value).filter(Boolean)")
            if meds:
                page.select_option("#dbody .jform select", meds[0])
            page.fill("#dbody textarea", "Started after lunch (fictional)")
            page.click("#dbody .jform button[type=submit]")
            page.wait_for_function("typeof JOURNAL !== 'undefined' && JOURNAL.length === 1 && document.getElementById('dtitle').textContent === 'Headache (browser check)'", timeout=20000)
            settle()
            entry = js("({id: JOURNAL[0].id, day: isoDay(JOURNAL[0].ts), sev: JOURNAL[0].j.severity, med: JOURNAL[0].j.med, kind: JOURNAL[0].j.kind})")
            check("the symptom is saved, dated to the event, and opens in the drawer",
                  entry["day"] == day_of["day"] and entry["sev"] == 7 and entry["kind"] == "symptom" and entry["med"] == (meds[0] if meds else None)
                  and "7/10" in page.inner_text("#dbody"), entry)
            page.click("#tab-timeline")
            js("id => setRange(byId.get(id).ts - 20 * DAY, byId.get(id).ts + 20 * DAY)", entry["id"])
            settle()
            lane = js("""(() => { const m = document.querySelector('#tl .jmark'); return {lane: [...document.querySelectorAll('#tl .lane-label')].some(t => t.textContent === 'Journal'),
                n: document.querySelectorAll('#tl .jmark').length, r: m && +m.getAttribute('r'), shape: m && m.tagName}; })()""")
            check("…on the timeline in its own Journal lane, sized by severity", lane["lane"] and lane["n"] == 1 and lane["shape"] == "circle" and abs(lane["r"] - 6.15) < 0.01, lane)
            check("…and in the event list", "Headache (browser check)" in page.inner_text("#list"))
            js("closeDrawer()")   # the entry's panel covers the tab bar's right end on narrower or wider fonts
            page.click("#tab-labs")
            settle()
            if not js("document.querySelectorAll('#charts .chart svg').length"):
                js("state.sel.add(SERIES.find(s => !s.watch && s.n >= 2).name); renderLabs()")
                settle()
            marks = js("[...document.querySelectorAll('#charts .jmark')].map(m => m.getAttribute('aria-label'))")
            charts = js("document.querySelectorAll('#charts .chart svg').length")
            check("…and as a marker under every Trends chart, labelled with its severity", len(marks) == charts > 0 and all("severity 7/10" in m and "Headache" in m for m in marks), marks[:2])
            check("the Trends legend says what the marker sizes mean", "bigger = more severe" in page.inner_text("#view-labs .legend"))
            page.focus("#charts .jmark")
            page.keyboard.press("Enter")
            settle(200)
            check("a Trends marker opens its entry from the keyboard", js("document.getElementById('dtitle').textContent") == "Headache (browser check)")
            page.keyboard.press("Escape")
            check("…and Escape takes focus back to the marker", js("document.activeElement.classList.contains('jmark')"))
            page.uncheck("#optJournal")
            settle(200)
            check("the Journal box on Trends hides the markers", js("document.querySelectorAll('#charts .jmark').length") == 0)
            page.check("#optJournal")
            js("id => openEvent(byId.get(id))", entry["id"])
            settle(200)
            page.click("#dbody .btns button:text-is('All journal entries')")
            settle(300)
            check("“All journal entries” goes to the journal's home on the Timeline tab, focused",
                  js("state.tab === 'timeline' && !drawer.classList.contains('open') && document.activeElement.id === 'journalHome'")
                  and page.inner_text("#journalHome h2") == "Journal (1)" and page.locator("#journalHome .jrow").count() == 1,
                  js("[state.tab, document.activeElement.id]"))
            jtoggles = r"""[...document.querySelectorAll('#journalHome .jfilters .toggle')].map(t => [t.textContent.trim().replace(/ \d+$/, ''),
              t.querySelector(':scope > input[type=checkbox]') ? String(t.querySelector('input').checked) : t.getAttribute('aria-pressed')])"""
            check("the journal's kinds and “Only the dates shown” are on/off toggle chips that say true or false (#193)",
                  js(jtoggles) == [["Notes", "true"], ["Symptoms", "true"], ["Side effects", "true"], ["Moods", "true"], ["Only the dates shown", "false"]], js(jtoggles))
            jsum = "(p => p && !p.hidden ? p.textContent : null)(document.querySelector('#journalHome .fsum'))"   # what the filters leave out (#190)
            check("the journal's filters as they start: no filter line (#190)", js(jsum) is None, js(jsum))
            page.click("#journalHome .jfilters .toggle[data-kind=symptom]")   # the kinds are on/off chips (#193)
            check("…filtered by kind", page.locator("#journalHome .jrow").count() == 0 and "No entries match" in page.inner_text("#journalHome .jlist")
                  and js("document.querySelector('#journalHome .toggle[data-kind=symptom]').getAttribute('aria-pressed')") == "false")
            check("…a line under them says so, with Reset filters (#190)", js(jsum) == "Showing 3 of 4 kinds · Reset filters", js(jsum))
            page.click("#journalHome .jfilters .toggle[data-kind=symptom]")
            page.fill("#journalHome .jfilters input[type=search]", "after lunch")
            check("…and by what they say", page.locator("#journalHome .jrow").count() == 1)
            check("…said on that line too (#190)", js(jsum) == "Showing entries matching “after lunch” · Reset filters", js(jsum))
            page.check("#journalHome .jinrange input")
            js("id => setRange(byId.get(id).ts + 40 * DAY, byId.get(id).ts + 80 * DAY)", entry["id"])
            settle(300)
            check("…and, ticked, by the timeline's dates as they change", page.locator("#journalHome .jrow").count() == 0)
            js("id => setRange(byId.get(id).ts - 20 * DAY, byId.get(id).ts + 20 * DAY)", entry["id"])
            settle(300)
            check("…back in the dates shown, it's listed again", page.locator("#journalHome .jrow").count() == 1)
            page.uncheck("#journalHome .jinrange input")
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the journal's home fits 375 px", js("(c => document.documentElement.scrollWidth <= 375 && c.scrollWidth <= c.clientWidth)(document.getElementById('journalHome'))"),
                  js("(c => [document.documentElement.scrollWidth, c.scrollWidth, c.clientWidth])(document.getElementById('journalHome'))"))
            theme_before = js("document.documentElement.dataset.theme || null")
            jbad = []
            for theme in ("light", "dark"):   # filtered, with its line (#190)
                js("t => { document.documentElement.dataset.theme = t; }", theme)
                settle(100)
                jbad += phone_fits(page, f"journal filtered, {theme}")
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            check("…filtered, its line and Reset filters fit too, in light and dark (#190)", js(jsum) and not jbad, jbad)
            page.set_viewport_size({"width": 1400, "height": 900})
            js("document.getElementById('sgStatus').textContent = ''")
            page.click("#journalHome .fsum-reset")
            check("…Reset filters puts every filter back, says so, focus in the journal's search (#190)", js(jsum) is None
                  and page.input_value("#journalHome .jfilters input[type=search]") == "" and page.locator("#journalHome .jrow").count() == 1
                  and js("[...document.querySelectorAll('#journalHome .jfilters .toggle')].every(t => (t.getAttribute('aria-pressed') || String(t.querySelector('input').checked)) === (t.dataset.kind ? 'true' : 'false'))")
                  and said(page) == "Filters reset." and js("document.activeElement.matches('#journalHome .jfilters input[type=search]')"), js(jsum))
            js("TAB_HIDDEN.add('timeline'); journalHome()")
            check("…with the Timeline tab hidden, the list opens in the panel instead",
                  js("document.getElementById('dtitle').textContent") == "Journal" and page.locator("#dbody .jrow").count() == 1)
            js("TAB_HIDDEN.delete('timeline'); closeDrawer()")
            settle(300)
            page.click("#journalHome .jrow")
            settle(200)
            page.click("#dbody .btns button:text-is('Delete')")
            page.click("#dbody .btns button.primary:text-is('Delete')")
            page.wait_for_function("typeof JOURNAL !== 'undefined' && EV.length > 0 && JOURNAL.length === 0 && booted && document.activeElement.id === 'journalHome'", timeout=20000)
            check("deleting the entry takes it off the timeline and Trends, back to the journal's home",
                  js("document.querySelectorAll('#charts .jmark, #tl .jmark').length") == 0 and page.inner_text("#journalHome h2") == "Journal")
            page.goto(base + "/account.html")
            page.wait_for_selector("#addMenuBtn", timeout=10000)
            menu_click(page, "#journalBtn")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && booted && document.getElementById('dtitle').textContent === 'New journal entry'", timeout=20000)
            check("Add → Journal entry on another page opens the records page with the form, the address naming its tab again (#208)",
                  js("drawer.classList.contains('open') && location.hash === tabHash() && location.hash !== '#new-journal-entry'"), js("location.href"))
            page.keyboard.press("Escape")
            page.click("#tab-timeline")
            settle()

            # ---- the journal's symptom groups, where on the body, moods and photos (#161): a group made and logged in
            # one go (each symptom its own severity, a place picked on the outline by keyboard and from the list), a
            # day's mood in its own lane and Trends row, a photo whose GPS location is taken out on the way in, the body
            # map of where symptoms have been with its table; at 1400 and 375 px, light and dark, axe-clean; then removed
            page.set_viewport_size({"width": 1400, "height": 900})
            js("switchTab('timeline')")
            settle()
            page.click("#journalHome .jgroup-btns button:text-is('Symptom groups')")
            settle(200)
            page.click("#dbody .btns button:text-is('New group')")
            page.fill("#dbody .jform input[type=text]", "Migraine day (browser check)")
            page.fill("#dbody .jform textarea", "Headache\nNausea\nLight sensitivity")
            page.click("#dbody .jform button[type=submit]")
            page.wait_for_function("(CFG.journal?.groups || []).length === 1 && document.getElementById('dtitle').textContent === 'Symptom groups'", timeout=10000)
            check("a symptom group is made from the journal's card, kept as the profile's setting (#161)",
                  js("JSON.stringify(CFG.journal.groups)") == '[{"name":"Migraine day (browser check)","symptoms":["Headache","Nausea","Light sensitivity"]}]'
                  and "Headache, Nausea, Light sensitivity" in page.inner_text("#dbody .jgroups"), js("JSON.stringify(CFG.journal.groups)"))
            page.click("#dbody .jgroups button:text-is('Log it')")
            settle(200)
            check("…logged in one go: one row a symptom, each with its own severity",
                  js("document.getElementById('dtitle').textContent") == "Log a symptom group" and page.locator("#dbody .jgf-row").count() == 3
                  and page.locator("#dbody .jgf-row input[type=range]").count() == 3)
            page.focus("#dbody .jgf-row >> nth=0 >> input[type=range]")
            page.keyboard.press("Home")
            for _ in range(6):
                page.keyboard.press("ArrowRight")
            page.uncheck("#dbody .jgf-row >> nth=1 >> input[type=checkbox]")
            page.click("#dbody .jgf-row >> nth=0 >> summary")
            page.focus("#dbody .jgf-row >> nth=0 >> svg[data-view=f] .jreg[tabindex='0']")
            page.keyboard.press("ArrowRight")   # head -> neck: the arrow keys move inside the outline
            page.keyboard.press("ArrowLeft")
            page.keyboard.press("Space")
            pick = js("""(() => { const r = document.querySelector('#dbody .jgf-row'); return {focus: document.activeElement.dataset.region,
                checked: [...r.querySelectorAll('.jreg[aria-checked=true]')].map(p => p.dataset.region), said: r.querySelector('.jbsaid').textContent,
                box: [...r.querySelectorAll('.jblist input:checked')].length, stops: [...r.querySelectorAll('svg .jreg')].filter(p => p.tabIndex === 0).length}; })()""")
            check("…a place picked on the outline by keyboard: one stop per outline, the arrow keys move, Space ticks; the list follows",
                  pick["focus"] == "head" and pick["checked"] == ["head"] and pick["said"] == "Chosen: Head" and pick["box"] == 1 and pick["stops"] == 2, pick)
            page.click("#dbody .jgf-row >> nth=0 >> .jblist summary")
            page.check("#dbody .jgf-row >> nth=0 >> .jblist label:text-is('Back of the neck') input")
            check("…and from the list, the outline following", js("[...document.querySelector('#dbody .jgf-row').querySelectorAll('.jreg.on')].map(p => p.dataset.region)") == ["head", "back-neck"])
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            gbad = []
            for theme in ("light", "dark"):
                js("t => { document.documentElement.dataset.theme = t; }", theme)
                settle(100)
                gbad += phone_fits(page, f"group form with the outline and list open, {theme}") + axe_check(page, f"group form, 375 px, {theme}")[0]
            check("…the group's form fits 375 px and passes axe, light and dark", not gbad, gbad)
            js("delete document.documentElement.dataset.theme")
            page.set_viewport_size({"width": 1400, "height": 900})
            page.click("#dbody .jform button[type=submit]")
            page.wait_for_function("typeof JOURNAL !== 'undefined' && JOURNAL.length === 2 && booted", timeout=20000)
            settle()
            got = js("JOURNAL.map(e => [e.j.title, e.j.severity, e.j.group, e.j.where || []]).sort()")
            check("…saved as two symptoms (the one unticked left out), each its own severity and place, naming the group",
                  got == [["Headache", 6, "Migraine day (browser check)", ["head", "back-neck"]], ["Light sensitivity", 5, "Migraine day (browser check)", []]], got)

            # a day's mood, its kind picked by keyboard; its own lane, and its own row on Trends
            js("journalNew()")
            settle(200)
            page.focus("#dbody .jkinds button[aria-checked=true]")
            page.keyboard.press("ArrowRight")
            page.keyboard.press("ArrowRight")
            check("a mood is the form's fourth kind: no time, no photos, a mood and an energy each on 0-10",
                  js("document.activeElement.dataset.kind") == "mood" and page.locator("#dbody .jscale input[type=range]").count() == 2
                  and not page.is_visible("#dbody input[type=time]") and not page.is_visible("#dbody .jphoto-in") and not page.is_visible("#dbody .jform input[type=text]"))
            page.focus("#dbody .jscale >> nth=0 >> input[type=range]")
            page.keyboard.press("End")
            page.keyboard.press("ArrowLeft")
            page.uncheck("#dbody .jscale >> nth=1 >> input[type=checkbox]")
            check("…its words said: 9/10 · very high, energy left out", page.inner_text("#dbody .jscale >> nth=0 >> output") == "9/10 · very high"
                  and js("document.querySelectorAll('#dbody .jscale input[type=range]')[1].disabled"))
            page.click("#dbody .jform button[type=submit]")
            page.wait_for_function("typeof JOURNAL !== 'undefined' && JOURNAL.length === 3 && booted && document.getElementById('dtitle').textContent === 'Mood 9/10'", timeout=20000)
            mood_day = js("isoDay(JOURNAL.find(e => e.k === 'mood').ts)")
            js("closeDrawer(); switchTab('timeline'); setRange(Date.now() - 20 * DAY, Date.now() + 5 * DAY)")
            settle()
            lane = js("""({lane: [...document.querySelectorAll('#tl .lane-label')].some(t => t.textContent === 'Mood & energy'), n: document.querySelectorAll('#tl .jmood').length,
                bars: document.querySelectorAll('#tl .jmood rect').length})""")
            check("…in its own Mood & energy lane on the timeline, a bar for the mood", lane["lane"] and lane["n"] == 1 and lane["bars"] == 2, lane)
            js("switchTab('labs')")
            settle()
            if not js("document.querySelectorAll('#charts .chart svg').length"):
                js("state.sel.add(SERIES.find(s => !s.watch && s.n >= 2).name); renderLabs()")
                settle()
            moods = js("[...document.querySelectorAll('#charts .jmood')].map(m => m.getAttribute('aria-label'))")
            check("…and its own row under every Trends chart, labelled", len(moods) == js("document.querySelectorAll('#charts .chart svg').length") > 0
                  and all(m.startswith("Mood 9/10, ") for m in moods), moods[:2])
            page.focus("#charts .jmood")
            page.keyboard.press("Enter")
            settle(200)
            check("…opened from the keyboard there", js("document.getElementById('dtitle').textContent") == "Mood 9/10" and "Mood and energy" in page.inner_text("#dbody"))
            page.keyboard.press("Escape")

            # a photo: its GPS location and camera taken out on the way in, shown to whoever can view the profile
            jpeg = __import__("base64").b64decode(js("""(() => { const c = document.createElement('canvas'); c.width = 160; c.height = 120; const x = c.getContext('2d');
                x.fillStyle = '#e9c2ad'; x.fillRect(0, 0, 160, 120); x.fillStyle = '#c8574f'; x.beginPath(); x.arc(80, 60, 22, 0, 7); x.fill();
                return c.toDataURL('image/jpeg', .8).split(',')[1]; })()"""))
            make, place = b"BrowserCheckFictionalPhone", b"GPS fictional place"
            tiff = (b"MM\x00*" + (8).to_bytes(4, "big") + (2).to_bytes(2, "big") + bytes.fromhex("010f0002") + (len(make) + 1).to_bytes(4, "big")
                    + (38).to_bytes(4, "big") + bytes.fromhex("8825000400000001") + (38 + len(make) + 1).to_bytes(4, "big") + b"\0\0\0\0" + make + b"\0"
                    + (1).to_bytes(2, "big") + bytes.fromhex("0001000200000002") + b"N\0\0\0" + b"\0\0\0\0" + place)
            app1 = b"Exif\0\0" + tiff
            jpeg = jpeg[:2] + b"\xff\xe1" + (len(app1) + 2).to_bytes(2, "big") + app1 + jpeg[2:]
            js("journalNew()")
            settle(200)
            page.fill("#dbody .jform input[type=text]", "Rash (browser check)")
            page.set_input_files("#dbody .jphoto-in", files=[{"name": "rash.jpg", "mimeType": "image/jpeg", "buffer": jpeg}])
            page.wait_for_selector("#dbody .jpics-edit .jpic img", timeout=10000)
            settle(200)
            msg = page.inner_text("#dbody .jphoto-msg")
            check("a photo attached to an entry says what was taken out of it: where it was taken, the camera",
                  "where it was taken (GPS location)" in msg and "the camera or phone it was taken with" in msg
                  and js("document.querySelector('#dbody .jpics-edit img').src.startsWith('data:image/jpeg')"), msg)
            page.fill("#dbody .jpics-edit input[type=text]", "Red patch, fictional")
            page.click("#dbody .jwhere summary")
            page.click("#dbody .jwhere svg[data-view=f] .jreg[data-region=l-forearm]")
            check("…a place tapped on the outline", js("[...document.querySelectorAll('#dbody .jwhere .jreg.on')].map(p => p.dataset.region)") == ["l-forearm"]
                  and page.is_checked("#dbody .jblist label:text-is('Left forearm') input"))
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            fbad = []
            for theme in ("light", "dark"):
                js("t => { document.documentElement.dataset.theme = t; }", theme)
                settle(100)
                fbad += phone_fits(page, f"entry form with a photo and the outline, {theme}") + axe_check(page, f"entry form with a photo, 375 px, {theme}")[0]
            js("delete document.documentElement.dataset.theme")
            check("…the form with its photo and the outline fits 375 px and passes axe, light and dark", not fbad, fbad)
            page.set_viewport_size({"width": 1400, "height": 900})
            page.click("#dbody .jform button[type=submit]")
            page.wait_for_function("typeof JOURNAL !== 'undefined' && JOURNAL.length === 4 && booted && document.getElementById('dtitle').textContent === 'Rash (browser check)'", timeout=20000)
            page.wait_for_function("(i => i && i.complete && i.naturalWidth > 0)(document.querySelector('#dbody .jpic img'))", timeout=10000)
            pic = js("""(async () => { const i = document.querySelector('#dbody .jpic img'), b = new Uint8Array(await (await fetch(i.src)).arrayBuffer());
                const s = Array.from(b, c => String.fromCharCode(c)).join(''); return {alt: i.alt, w: i.naturalWidth, gps: s.includes('GPS'), make: s.includes('BrowserCheck'),
                caption: document.querySelector('#dbody .jpic figcaption')?.textContent, where: document.querySelector('#dbody .jbmini .jreg.on')?.dataset.region}; })()""")
            check("…saved: shown in the entry's panel with what it shows, served without the location or camera",
                  pic["alt"] == "Red patch, fictional" and pic["w"] == 160 and not pic["gps"] and not pic["make"] and pic["caption"] == "Red patch, fictional"
                  and pic["where"] == "l-forearm", pic)
            dbad = []
            for width in (1400, 375):
                page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                for theme in ("light", "dark"):
                    js("t => { document.documentElement.dataset.theme = t; }", theme)
                    settle(100)
                    dbad += axe_check(page, f"entry with a photo, {width} px, {theme}")[0] + (phone_fits(page, f"entry with a photo, {theme}") if width < 400 else [])
            js("delete document.documentElement.dataset.theme")
            page.set_viewport_size({"width": 1400, "height": 900})
            check("…its panel passes axe at 1400 and 375 px, light and dark, and fits a phone", not dbad, dbad)
            page.keyboard.press("Escape")

            # where symptoms have been: the body map with counts, its places buttons; its table (#60)
            js("closeDrawer(); switchTab('timeline'); setRange(Date.now() - 20 * DAY, Date.now() + 5 * DAY)")
            settle()
            bmap = js("""(() => { const m = document.querySelector('#journalHome .jwhere-map'); if (!m) return null;
                return {n: [...m.querySelectorAll('.jreg.has')].map(p => [p.dataset.region, p.getAttribute('aria-label')]),
                nums: [...m.querySelectorAll('.jbn')].map(t => t.textContent), hint: m.querySelector('.hint').textContent}; })()""")
            check("the journal's card shows where symptoms have been in the dates shown, each place's count written in it, not by shading alone",
                  bmap and sorted(r for r, _ in bmap["n"]) == ["back-neck", "head", "l-forearm"] and bmap["nums"] == ["1", "1", "1"]
                  and "Head: 1 entry, the worst 6/10. Show them in the list" in [a for _, a in bmap["n"]] and "2 symptoms and side effects say where on the body" in bmap["hint"], bmap)
            page.focus("#journalHome .jwhere-map .jreg[data-region=l-forearm]")
            page.keyboard.press("Enter")
            settle(200)
            check("…a place opens its entries in the list, said on the filter line",
                  page.locator("#journalHome .jrow").count() == 1 and "Rash (browser check)" in page.inner_text("#journalHome .jlist")
                  and "about the left forearm" in (js("document.querySelector('#journalHome .fsum').textContent") or ""), js("document.querySelector('#journalHome .fsum').textContent"))
            page.click("#journalHome .fsum-reset")
            page.click("#journalHome .jwhere-map .view-seg button:text-is('Table')")
            settle(200)
            rows = js("[...document.querySelectorAll('#journalHome .jwhere-map table tbody tr')].map(r => [...r.cells].map(c => c.textContent))")
            check("…and as a table: where, how many, the worst, the latest (#60)", sorted(r[0] for r in rows) == ["Back of the neck", "Head", "Left forearm"]
                  and ["Head", "1", "6/10"] == next(r for r in rows if r[0] == "Head")[:3], rows)
            mbad = []
            for width in (1400, 375):
                page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                for table in (False, True):
                    js("t => { if (t !== state.tables.has('#journal-where')) document.querySelector('#journalHome .jwhere-map .view-seg button[aria-pressed=false]').click(); }", table)
                    for theme in ("light", "dark"):
                        js("t => { document.documentElement.dataset.theme = t; }", theme)
                        settle(150)
                        js("document.querySelector('#journalHome .jwhere-map').scrollIntoView()")
                        mbad += axe_check(page, f"journal card with the body {'table' if table else 'map'}, {width} px, {theme}")[0]
                        if width < 400:
                            mbad += phone_fits(page, f"journal card with the body {'table' if table else 'map'}, {theme}")
                            if not table and theme == "dark" and shots:
                                page.screenshot(path=os.path.join(shots, "161-body-map-375-dark.png"))
            js("delete document.documentElement.dataset.theme")
            check("…the card with the body map and its table passes axe at 1400 and 375 px, light and dark, and fits a phone", not mbad, mbad)
            page.set_viewport_size({"width": 375, "height": 800})
            js("if (state.tables.has('#journal-where')) document.querySelector('#journalHome .jwhere-map .view-seg button[aria-pressed=false]').click()")
            page.focus("#journalHome .jwhere-map .jreg[data-region=head]")
            page.keyboard.press("Tab")
            check("…its places with entries are stops of the keyboard's own", js("document.activeElement.classList.contains('jreg')"))
            page.set_viewport_size({"width": 1400, "height": 900})
            # a share link shows the photo only when "Photos in the journal" is ticked, under the real CSP, with no session
            made = js("""(async () => { const r = await api(`profiles/${PROFILE.id}/links`); const offered = (await r.json()).sections.map(s => s.id);
                const mk = async secs => (await (await api(`profiles/${PROFILE.id}/links`, {method: 'POST', body: JSON.stringify({sections: secs, hours: 1, label: 'Journal (browser check)'})})).json());
                return {offered, without: await mk(['journal']), with: await mk(['journal', 'journal_photos'])}; })()""")
            holder = browser.new_context(viewport={"width": 375, "height": 800})
            hp = holder.new_page()
            hp.on("console", lambda m: m.type == "error" and problems.append(f"share page console error: {m.text}"))
            hp.goto(base + "/" + made["without"]["path"])
            hp.wait_for_selector("#share-journal .share-entry", timeout=10000)
            plain = hp.evaluate("""({pics: document.querySelectorAll('.share-pic').length, where: [...document.querySelectorAll('#share-journal .share-entry p.muted')].map(p => p.textContent)})""")
            hp.goto(base + "/" + made["with"]["path"])
            hp.reload()
            for _ in range(100):   # under the CSP: evaluate, not wait_for_function (a string the CSP won't run)
                if hp.evaluate("(i => !!i && i.complete && i.naturalWidth > 0)(document.querySelector('.share-pic img'))"):
                    break
                hp.wait_for_timeout(100)
            shared = hp.evaluate("""({alt: document.querySelector('.share-pic img').alt, src: document.querySelector('.share-pic img').src.slice(0, 15),
                cards: [...document.querySelectorAll('#shareBody > section')].map(s => s.id)})""")
            sbad = []
            for theme in ("light", "dark"):
                hp.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
                hp.wait_for_timeout(100)
                sbad += phone_fits(hp, f"share link with a journal photo, {theme}") + axe_check(hp, f"share link with a journal photo, {theme}")[0]
            holder.close()
            check("a share link offers “Photos in the journal” once there are some; without it ticked no photo, with it the photo, under the real CSP, at 375 px, light and dark, axe-clean",
                  made["offered"][made["offered"].index("journal") + 1] == "journal_photos" and plain["pics"] == 0
                  and any("Where: Head, Back of the neck" in w for w in plain["where"]) and shared["alt"] == "Red patch, fictional"
                  and shared["src"] == "data:image/jpeg" and "share-journal_photos" not in shared["cards"] and not sbad, [made["offered"], plain, shared, sbad])
            js("ids => Promise.all(ids.map(i => api(`profiles/${PROFILE.id}/links/${i}`, {method: 'DELETE'})))", [made["without"]["link"]["id"], made["with"]["link"]["id"]])
            # removed again: the entries (and the photo with its entry) and the group
            js("""Promise.all(JOURNAL.map(e => api(`profiles/${PROFILE.id}/journal/${e.j.id}`, {method: 'DELETE'})))
                .then(() => api(`profiles/${PROFILE.id}/settings/journal.groups`, {method: 'PUT', body: JSON.stringify({value: []})}))""")
            page.reload()
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && booted", timeout=20000)
            check("…all removed again", js("JOURNAL.length === 0 && (CFG.journal?.groups || []).length === 0 && !document.querySelector('#tl .jmood')"))
            page.click("#tab-timeline")
            settle()

            # ---- the medication timeline (#50): spans with the dose change, the stop and the fills; the history in the
            # drawer; the rows under each Trends chart when ticked; at 375 px
            page.click("#tab-timeline")
            js("setRange(dayTs('2025-01-01'), dayTs('2025-12-31'))")
            settle()
            med = js("""(() => { const m = MEDS.find(m => m.key === 'examplocin'); return m && {i: m.i, spans: m.spans.length, changes: m.changes.length,
                fills: m.fills.length, stop: m.stop, open: m.spans.map(s => s.open)}; })()""")
            check("the records' medication is laid out as spans: a dose change, a stop, three fills",
                  med and med["spans"] == 2 and med["changes"] == 1 and med["fills"] == 3 and med["stop"] == "2025-09-30" and med["open"] == [False, False], med)
            drawn = js(f"""({{bars: document.querySelectorAll('#tl .medbar[data-med="{med["i"]}"]').length,
                change: document.querySelectorAll('#tl .medchange').length, stop: document.querySelectorAll('#tl .medstop').length,
                fills: document.querySelectorAll('#tl .medfill[data-med="{med["i"]}"]').length,
                name: [...document.querySelectorAll('#tl text.medname')].some(t => t.textContent.startsWith('Examplocin')),
                dots: [...document.querySelectorAll('#tl circle[data-id]')].filter(c => ['med', 'rx'].includes(byId.get(+c.dataset.id)?.k)).length,
                demoOpen: MEDS.filter(m => m.spans.some(s => s.open)).length}})""") if med else {}
            check("…drawn in the Medications lane: two bars, a change tick, a stop cap and a fill mark each, no dots",
                  drawn.get("bars") == 2 and drawn.get("change", 0) >= 1 and drawn.get("stop", 0) >= 1 and drawn.get("fills") == 3 and drawn.get("name") and drawn.get("dots") == 0, drawn)
            check("…the demo's medications, listed with no end, run on as open spans", drawn.get("demoOpen", 0) >= 3, drawn)
            if med:
                bar = page.locator(f'#tl .medbar[data-med="{med["i"]}"][data-span="0"]')
                bar.hover()
                tip = page.inner_text("#tip")
                check("hovering a span says what the records list, dated in this person's format", "Examplocin" in tip and "Dr. Rowan Vale" in tip
                      and js("fmtD(dayTs('2025-02-03'))") in tip, tip)
                bar.click()
                settle(200)
                body = page.inner_text("#dbody")
                check("clicking a span opens the medication's history: its listings, the change and the fills",
                      js("document.getElementById('dtitle').textContent").startswith("Examplocin") and page.locator("#dbody table.medspans tbody tr").count() == 2
                      and page.locator("#dbody .medchanges li").count() == 1 and page.locator("#dbody table.medfills tbody tr").count() == 3
                      and "Discontinued" in body and "9990001" in body and js("fmtD(dayTs('2025-09-30'))") in body, body[:400])
                check("…saying what the records say, not advice", "This isn't medical advice" in body
                      and not re.search(r"\b(should|recommend|missed|too (high|low))\b", body, re.I), body[-300:])
                page.click("#dbody table.medfills tbody tr >> nth=0")
                settle(200)
                check("a fill in the history opens the fill, which leads back to the history",
                      js("byId.get(state.selEv)?.k") == "rx" and page.locator("#dbody button:text-is('Medication history')").count() == 1)
                page.click("#dbody button:text-is('Medication history')")
                settle(200)
                page.set_viewport_size({"width": 375, "height": 800})
                settle(300)
                check("the medication history fits 375 px", js("document.documentElement.scrollWidth <= 375 && document.getElementById('dbody').scrollWidth <= document.getElementById('dbody').clientWidth"),
                      js("[document.documentElement.scrollWidth, document.getElementById('dbody').scrollWidth]"))
                page.keyboard.press("Escape")
                settle(200)
                narrow = js(f"""(() => {{ const svg = document.getElementById('tl'), r = svg.getBoundingClientRect();
                    const bars = [...svg.querySelectorAll('.medbar[data-med="{med["i"]}"]')].map(b => b.getBoundingClientRect());
                    return {{fits: document.documentElement.scrollWidth <= 375 && r.right <= 375, bars: bars.length, inside: bars.every(b => b.left >= r.left - 1 && b.right <= r.right + 1),
                      names: [...svg.querySelectorAll('text.medname')].every(t => t.firstChild.textContent.length <= 13)}}; }})()""")
                check("the Medications lane fits 375 px, names shortened", narrow["fits"] and narrow["bars"] == 2 and narrow["inside"] and narrow["names"], narrow)
                page.set_viewport_size({"width": 1400, "height": 900})
                settle(200)
                page.click("#tab-labs")
                settle()
                if not js("document.querySelectorAll('#charts .chart svg').length"):
                    js("state.sel.add(SERIES.find(s => !s.watch && s.n >= 2).name); renderLabs()")
                    settle()
                check("Trends leaves the medications out until they're ticked", not page.is_checked("#optMeds") and js("document.querySelectorAll('#charts .medbar').length") == 0)
                page.check("#optMeds")
                settle()
                rows = js(f"""[...document.querySelectorAll('#charts .chart svg')].map(s => s.querySelectorAll('.medbar[data-med="{med["i"]}"]').length)""")
                check("…then every chart has the medication's spans under its time axis", len(rows) > 0 and all(n == 2 for n in rows), rows)
                page.locator(f'#charts .chart svg .medbar[data-med="{med["i"]}"] >> nth=0').click()
                settle(200)
                check("…and a span there opens the medication's history", js("document.getElementById('dtitle').textContent").startswith("Examplocin"))
                page.keyboard.press("Escape")
                page.set_viewport_size({"width": 375, "height": 800})
                settle(300)
                check("Trends with the medications fits 375 px", js("document.documentElement.scrollWidth <= 375"), js("document.documentElement.scrollWidth"))
                page.set_viewport_size({"width": 1400, "height": 900})
                page.uncheck("#optMeds")
                settle(200)
                page.click("#tab-timeline")
                settle()

            # ---- medication reminders through the calendar feed (#162): set on a current medication's drawer (two times,
            # weekdays, an until day), listed there as on; turned off and on again; a stopped medication's reminder says it
            # ended when the records say it stopped, and none can be added; 1400 and 375 px, light and dark, axe clean.
            # The calendar feed's check (#51) below finds it in the feed as a repeating event.
            cur = js("(() => { const m = MEDS.find(m => m.spans.length && m.spans.at(-1).open); return m && {key: m.key, name: m.name}; })()")
            check("the demo has a current medication to set a reminder for", bool(cur), cur)
            if cur:
                js("k => openMedication(MEDS.find(m => m.key === k))", cur["key"])
                settle(200)
                check("…its drawer offers to add a reminder, and says the calendar feed brings it",
                      page.locator("#dbody button:text-is('Add a reminder')").count() == 1 and "Sharing → Calendar feed" in page.inner_text("#dbody"))
                page.click("#dbody button:text-is('Add a reminder')")
                settle(200)
                form162 = js("""({title: document.getElementById('dtitle').textContent, times: document.querySelectorAll('#dbody .mr-time input').length,
                    first: document.querySelector('#dbody .mr-time input').value, days: [...document.querySelectorAll('#dbody .mr-days .toggle')].map(b => b.getAttribute('aria-pressed')).join(),
                    from: document.querySelector('#dbody input[type=date]').value, today: zToday(), zone: document.querySelector('#dbody .jform > p.hint').textContent})""")
                check("…the form starts at 08:00, every day, from today, in the person's time zone",
                      form162["title"] == "New reminder" and form162["times"] == 1 and form162["first"] == "08:00" and form162["days"] == ",".join(["true"] * 7)
                      and form162["from"] == form162["today"] and "your time zone" in form162["zone"], form162)
                bad162, axe162 = [], []
                theme162 = js("document.documentElement.dataset.theme || null")
                for width in (1400, 375):
                    page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                    for theme in ("light", "dark"):
                        js("t => { document.documentElement.dataset.theme = t; }", theme)
                        settle(200)
                        if width < 400:
                            bad162 += phone_fits(page, f"reminder form ({theme})")
                        axe162 += axe_check(page, f"reminder form, {width} px, {theme}")[0]
                page.set_viewport_size({"width": 1400, "height": 900})
                page.click("#dbody button:text-is('Add another time')")
                page.locator("#dbody .mr-time input >> nth=1").fill("20:00")
                page.click("#dbody .mr-days button[aria-label=Saturday]")
                page.click("#dbody .mr-days button[aria-label=Sunday]")
                until162 = js("isoDay(Date.now() + 90 * DAY)")
                page.locator("#dbody input[type=date] >> nth=1").fill(until162)
                page.click("#dbody button:text-is('Add reminder')")
                page.wait_for_load_state("load")
                page.wait_for_function("typeof MEDS !== 'undefined' && document.querySelector('#dbody .mr-item')", timeout=20000)
                settle(300)
                item = js("document.querySelector('#dbody .mr-item').innerText")
                check("saved: the page opens the medication again with the reminder, on, in the person's own words",
                      js("document.getElementById('dtitle').textContent") == cur["name"] and "8:00" in item and "on weekdays" in item
                      and "On: in the calendar feed" in item and js("d => fmtD(dayTs(d))", until162) in item, item)
                check("…it's in the records the page loaded, with the zone it was set in",
                      js("(DATA.reminders || []).length === 1 && DATA.reminders[0].times.join() === '08:00,20:00' && DATA.reminders[0].days.join() === 'mo,tu,we,th,fr' && !!DATA.reminders[0].tz"))
                for width in (1400, 375):
                    page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                    for theme in ("light", "dark"):
                        js("t => { document.documentElement.dataset.theme = t; }", theme)
                        settle(200)
                        if width < 400:
                            bad162 += phone_fits(page, f"medication drawer with a reminder ({theme})")
                        axe162 += axe_check(page, f"medication drawer with a reminder, {width} px, {theme}")[0]
                page.set_viewport_size({"width": 1400, "height": 900})
                js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme162)
                check("…the form and the reminder fit 375 px, in light and dark", not bad162, bad162[:6])
                check("…and pass axe-core's WCAG A and AA rules at 1400 and 375 px, in light and dark", not axe162, axe162[:6])
                page.click("#dbody .mr-item button:text-is('Turn off')")
                page.wait_for_load_state("load")
                page.wait_for_function("typeof MEDS !== 'undefined' && document.querySelector('#dbody .mr-item.off')", timeout=20000)
                check("turned off: kept, and said to be out of the calendar feed", "Off: not in the calendar feed" in page.inner_text("#dbody .mr-item"))
                page.click("#dbody .mr-item button:text-is('Turn on')")
                page.wait_for_load_state("load")
                page.wait_for_function("typeof MEDS !== 'undefined' && document.querySelector('#dbody .mr-item.on')", timeout=20000)
                check("…and on again", "On: in the calendar feed" in page.inner_text("#dbody .mr-item"))
                page.keyboard.press("Escape")
                # a reminder whose medication the records say stopped (the demo's Examplocin, 2025-09-30), as the server lists one
                ended = js("""(() => { const m = MEDS.find(m => m.key === 'examplocin');
                    DATA.reminders.push({id: 99999, med: 'examplocin', name: m.name, times: ['09:00'], days: ['mo','tu','we','th','fr','sa','su'], from: '2025-06-01', until: null, tz: 'UTC', on: true});
                    openMedication(m); const t = document.getElementById('dbody').innerText; DATA.reminders.pop();
                    return {ended: t.includes('Ended ' + fmtD(dayTs('2025-09-30')) + ': the records say this medication stopped then'),
                            add: [...document.querySelectorAll('#dbody button')].some(b => /^Add (another )?reminder$/.test(b.textContent)),
                            none: t.includes('no new reminders can be set'),
                            off: [...document.querySelectorAll('#dbody .mr-item button')].map(b => b.textContent)}; })()""")
                check("a medication stopped in the records: its reminder says it ended then, and no new one can be set",
                      ended["ended"] and not ended["add"] and ended["none"] and "Turn off" not in ended["off"], ended)
                page.keyboard.press("Escape")
                settle(200)

            # ---- the medication log (#235): on the medication with #162's reminder, a dose logged from its drawer (taken
            # late, with a note), corrected and removed; today's doses on the Overview, built from the reminder's times when
            # today is one of its days, a dose logged there with one click; the doses as marks on the medication's span on
            # the Timeline and, when ticked, as a row under the Labs charts (keyboard too); the weekly count of what was
            # logged; never "due"; the visit summary's "Medication log" when ticked; 1400 and 375 px, light and dark, axe
            if cur:
                js("k => openMedication(MEDS.find(m => m.key === k))", cur["key"])
                settle(200)
                check("medication log: a current medication's drawer offers Log a dose", page.locator("#dbody button:text-is('Log a dose')").count() == 1)
                page.click("#dbody button:text-is('Log a dose')")
                settle(200)
                f235 = js("""({title: document.getElementById('dtitle').textContent, st: document.querySelector('#dbody [role=radiogroup] [aria-checked=true]').textContent,
                    focus: document.activeElement.textContent, date: document.querySelector('#dbody input[type=date]').value, today: zToday(),
                    time: !!document.querySelector('#dbody input[type=time]').value})""")
                check("…its form starts at Taken, today, now", f235["title"] == "Log a dose" and f235["st"] == "Taken" and f235["focus"] == "Taken"
                      and f235["date"] == f235["today"] and f235["time"], f235)
                page.keyboard.press("ArrowRight")
                check("…the arrow keys pick what happened", js("document.querySelector('#dbody [role=radiogroup] [aria-checked=true]').textContent") == "Taken late")
                bad235, axe235 = [], []
                theme235 = js("document.documentElement.dataset.theme || null")

                def sweep235(label, where=None):
                    for width in (1400, 375):
                        page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                        for theme in ("light", "dark"):
                            js("t => { document.documentElement.dataset.theme = t; }", theme)
                            settle(250)
                            if where:
                                page.locator(where).first.scroll_into_view_if_needed()
                                clear_of_bar(page)
                            if width < 400:
                                bad235.extend(phone_fits(page, f"{label} ({theme})"))
                            axe235.extend(axe_check(page, f"{label}, {width} px, {theme}")[0])
                    page.set_viewport_size({"width": 1400, "height": 900})
                    js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme235)
                sweep235("dose form")
                page.fill("#dbody textarea", "Fictional note: later than usual")
                page.click("#dbody button:text-is('Log dose')")
                page.wait_for_load_state("load")
                page.wait_for_function("typeof MEDS !== 'undefined' && document.querySelector('#dbody .dose-item')", timeout=20000)
                settle(300)
                item = page.inner_text("#dbody .dose-item")
                week = page.inner_text("#dbody .dose-week")
                check("logged: the medication opens again with the dose, its note and who logged it, and this week's count of what was logged",
                      item.startswith("Taken late") and "Fictional note: later than usual" in item and f"Logged by {USER}" in item
                      and week == "This week: 1 of 1 logged dose taken (1 late)", [item, week])
                body235 = page.inner_text("#dbody")
                check("…the drawer never says a dose was due or should be taken", not re.search(r"(?i)\b(due|should|missed|overdue|adherence|forgot)\b", body235), body235[-400:])
                sweep235("medication drawer with its log", "#dbody .doselist")
                page.click("#dbody .dose-item button:text-is('Edit')")
                settle(200)
                page.click("#dbody [role=radiogroup] button:text-is('Taken')")
                page.click("#dbody button:text-is('Save')")
                page.wait_for_load_state("load")
                page.wait_for_function("typeof MEDS !== 'undefined' && document.querySelector('#dbody .dose-item')", timeout=20000)
                check("…corrected: Taken, the note kept", page.inner_text("#dbody .dose-item").startswith("Taken ·") and "later than usual" in page.inner_text("#dbody .dose-item"))
                page.keyboard.press("Escape")
                # today's doses on the Overview: the reminder's times when today is one of its days, else one row for it
                js("switchTab('overview')")
                settle(300)
                today235 = js("""(() => { const c = document.getElementById('ovDoses'); if (!c) return null;
                    const k = MR_DAYS[(zdate(Date.now()).getDay() + 6) % 7][0];
                    return {rows: [...c.querySelectorAll('.dose-today')].map(r => [r.querySelector('.dose-slot').textContent, r.querySelector('.dose-med').textContent,
                              r.querySelectorAll('.dose-quick button').length, r.querySelector('.dose-got')?.textContent || '']),
                            weekday: ['mo', 'tu', 'we', 'th', 'fr'].includes(k), cur: MEDS.filter(mrCurrent).length,
                            week: c.querySelector('.dose-week').textContent, text: c.textContent}; })()""")
                mine = [r for r in (today235 or {}).get("rows", []) if r[1] == cur["name"]]
                want = ["8:00 AM", "8:00 PM", "Also"] if today235 and today235["weekday"] else ["Today"]
                check("Overview: today's medication doses, a row for each of the reminder's times today (or one), the dose logged beside it",
                      today235 and [r[0] for r in mine] == want and "Taken" in mine[-1][3] and len({r[1] for r in today235["rows"]}) == today235["cur"], today235)
                check("…one click logs a dose, three choices a row; the week's count; never \"due\"",
                      today235 and all(r[2] in (0, 3) for r in today235["rows"]) and today235["week"].startswith("This week: ")
                      and not re.search(r"(?i)\b(due|should|missed|overdue)\b", today235["text"]), today235 and today235["week"])
                sweep235("Overview with today's doses", "#ovDoses")
                row = page.locator("#ovDoses .dose-today", has=page.locator(".dose-quick")).first
                what = row.locator(".dose-med").inner_text()
                row.locator(".dose-quick button:text-is('Skipped')").click()
                page.wait_for_load_state("load")
                page.wait_for_function("typeof booted !== 'undefined' && booted && document.getElementById('ovDoses')", timeout=20000)
                settle(300)
                check("…clicked Skipped: logged now, and the row says so", js("""w => [...document.querySelectorAll('#ovDoses .dose-today')].some(r =>
                    r.querySelector('.dose-med').textContent === w && /Skipped · /.test(r.querySelector('.dose-got')?.textContent || ''))""", what), what)
                # the Timeline: marks on the span, by shape, with a tooltip
                js("setRange(Date.now() - 20 * DAY, Date.now() + 5 * DAY); switchTab('timeline')")
                settle(400)
                marks = js("""k => { const m = MEDS.find(m => m.key === k);
                    return [...document.querySelectorAll(`#tl .dosemark[data-med="${m.i}"]`)].map(n => n.getAttribute('class')); }""", cur["key"])
                check("Timeline: each dose a mark on the medication's span (● taken, ○ skipped)", sorted(marks) == ["dosemark skipped", "dosemark taken"], marks)
                page.locator("#tl .dosemark").last.hover()   # the last drawn: two logged a moment apart overlap
                settle(150)
                check("…hovering one says what happened and when", re.match(r"(Taken|Skipped)", page.inner_text("#tip")) and cur["name"] in page.inner_text("#tip"), page.inner_text("#tip"))
                # Labs: a row of doses under each chart when ticked
                page.click("#tab-labs")
                settle()
                if not js("document.querySelectorAll('#charts .chart svg').length"):
                    js("state.sel.add(SERIES.find(s => !s.watch && s.n >= 2).name); renderLabs()")
                    settle()
                check("Labs: \"Doses logged\" under Show on charts, off until ticked", page.locator("#optDoses").count() == 1 and not page.is_checked("#optDoses")
                      and js("document.querySelectorAll('#charts .dosemark').length") == 0)
                page.check("#optDoses")
                settle(300)
                per = js("[...document.querySelectorAll('#charts .chart svg')].map(s => s.querySelectorAll('.dosemark').length)")
                check("…ticked, every chart has a row of the doses, and its legend says the shapes", per and all(n >= 2 for n in per)
                      and js("!document.getElementById('doseLegend').hidden") and "◆ taken late" in page.inner_text("#doseLegend"), per)
                page.locator("#charts .chart svg .dosemark").first.focus()
                page.keyboard.press("Enter")
                settle(200)
                check("…a mark there opens the medication by keyboard", js("document.getElementById('dtitle').textContent") == cur["name"])
                page.keyboard.press("Escape")
                sweep235("Labs with the doses row", "#charts .chart")
                page.uncheck("#optDoses")
                settle(200)
                # the visit summary: "Medication log", unticked until chosen
                page.goto(base + "/sharing.html?profile=1#summary")
                page.wait_for_function("OPTS && OPTS.profile.id === 1 && !document.getElementById('build').hidden", timeout=10000)
                check("visit summary: the medication log offered unticked", page.locator("#sections input[value=medlog]").count() == 1
                      and not page.is_checked("#sections input[value=medlog]"))
                js("document.getElementById('since').selectedIndex = document.getElementById('since').options.length - 1")
                for k in js("[...document.querySelectorAll('#sections input')].map(i => i.value)"):
                    (page.check if k == "medlog" else page.uncheck)(f"#sections input[value={k}]")
                page.click("#previewBtn")
                page.wait_for_selector("#preview:not([hidden]) h3", timeout=10000)
                pv = page.inner_text("#preview")
                check("…ticked, the preview lists each dose with what happened and the count of what was logged",
                      js("[...document.querySelectorAll('#preview h3')].map(h => h.textContent)")[0].startswith("Medication log")
                      and re.search(re.escape(cur["name"]) + r": 1 of 2 logged doses taken, 1 skipped", pv)
                      and "Skipped" in pv and not re.search(r"(?i)\b(due|should|missed)\b", pv), pv[:400])
                sweep235("summary preview with the medication log", "#preview")
                check("the medication log's form, drawer, today's doses, Labs row and summary fit 375 px, light and dark", not bad235, bad235[:6])
                check("…and pass axe-core's WCAG A and AA rules at 1400 and 375 px, light and dark", not axe235, axe235[:6])
                page.goto(base + "/")
                page.wait_for_function("typeof booted !== 'undefined' && booted", timeout=20000)
                js("setRange(dayTs('2025-01-01'), dayTs('2025-12-31')); switchTab('timeline')")
                settle()

            # ---- where each record came from (#100): a Source line in the drawer, linking to the import in the history
            visit = js("EV.find(e => e.k === 'visit' && !e.hidden).id")
            js("id => openEvent(byId.get(id))", visit)
            settle(200)
            src = page.locator("#dbody p.source")
            text = src.inner_text() if src.count() == 1 else ""
            check("the drawer shows an event's source and when it was imported", "Setup build" in text and "imported" in text, text)
            js("openEvent(EV.find(e => e.k === 'lab' && (DATA.details[e.id] || {}).ids && DATA.details[e.id].ids.length > 1))")
            settle(200)
            n = js("DATA.details[state.selEv].ids.filter(i => DATA.details[i] && byId.get(i)).length")
            check("each result of a lab day shows its source", n > 1 and page.locator("#dbody details.res p.source").count() == n,
                  (n, page.locator("#dbody details.res p.source").count()))
            js("id => openEvent(byId.get(id))", visit)
            settle(200)
            page.focus("#dbody p.source a")   # by keyboard: the link takes focus and Enter follows it
            page.keyboard.press("Enter")
            page.wait_for_url("**/sources.html?profile=1#import-*", timeout=10000)
            page.wait_for_selector("#historyList li.hl", timeout=10000)
            check("the source link opens that import in the history, highlighted and focused",
                  js("document.querySelectorAll('#historyList li.hl').length === 1 && document.activeElement.id === location.hash.slice(1)"
                     " && document.activeElement.classList.contains('hl') && document.activeElement.textContent.includes('setup build')"),
                  js("[location.hash, document.activeElement.id, document.activeElement.textContent.slice(0, 80)]"))
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0", timeout=20000)
            settle()

            # ---- Trends: drag pans every chart together, ctrl+wheel zooms, milestones drawn on All
            page.click("#tab-labs")
            js("applyPreset('1y')")
            settle()
            box = page.locator("#charts .chart svg").first.bounding_box()
            before = js("[state.from, state.to]")
            page.mouse.move(box["x"] + box["width"] * 0.6, box["y"] + box["height"] * 0.5)
            page.mouse.down()
            page.mouse.move(box["x"] + box["width"] * 0.3, box["y"] + box["height"] * 0.5, steps=8)
            page.mouse.up()
            settle()
            after = js("[state.from, state.to]")
            check("Trends drag pans", after[0] > before[0] and abs((after[1] - after[0]) - (before[1] - before[0])) < 1000, f"moved {(after[0] - before[0]) / 864e5:.0f} days")
            span = after[1] - after[0]
            page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5)
            page.keyboard.down("Control")
            page.mouse.wheel(0, -300)
            page.keyboard.up("Control")
            settle(700)
            z = js("state.to - state.from")
            check("Trends wheel zooms", z < span * 0.95, f"{span / 864e5:.0f} -> {z / 864e5:.0f} days")
            js("applyPreset('all')")
            settle()
            check("milestones drawn on Trends", js("document.querySelectorAll('#charts .msmark').length") > 0)
            # …in their own colour, not the accent's (#334): on the Timeline, every Labs chart and every Body and activity
            # chart, in light and dark, no mark, label, milestone or cursor is filled or stroked in the accent, which is
            # for controls (the ECG trace's cursor is checked with the ECGs, below)
            js("window._was334 = [state.marks, document.documentElement.dataset.theme || null, scrollY]; state.marks = true")
            accented, seen = [], {}
            for theme in ("light", "dark"):
                js("t => { document.documentElement.dataset.theme = t; }", theme)
                for tab, sel in (("timeline", "#tl"), ("labs", "#charts .chart svg"), ("health", "#view-health svg.pz")):
                    js("t => switchTab(t, t === 'labs' ? 'chart' : undefined)", tab)
                    settle(400)
                    got = js(CHART_ACCENT_JS, sel)
                    accented += [f"{theme}, {tab}: {b}" for b in got["bad"]]
                    seen[tab] = [got["rules"], got["labels"], got["cursors"]]
            js("([m, t]) => { state.marks = m; if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; switchTab('labs', 'chart'); }",
               js("window._was334.slice(0, 2)"))
            settle(300)
            js("scrollTo(0, window._was334[2])")
            check("milestones and cursors (#334): no chart mark, label, milestone or cursor on the Timeline, Labs or Body and activity is in the accent, in light and dark",
                  not accented and all(r > 0 and l > 0 for r, l, _ in seen.values()) and seen["labs"][2] > 0 and seen["health"][2] > 0,
                  accented[:8] or seen)
            # their labels (#312): at 1400, 375 and 320 px each one on the Timeline, every Labs chart (procedures shown)
            # and every Body and activity chart is inside its plot with no mark on it; one shortened to fit ends in "…"
            # and keeps the full names in its title; a Labs chart's and a Body and activity chart's aria-label names them
            js("window._was312 = [state.marks, scrollY]; state.marks = true")
            for width in (1400, 375, 320):
                page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                for tab, sel in (("timeline", "#tl"), ("labs", "#charts .chart svg"), ("health", "#view-health svg.pz")):
                    js("t => switchTab(t, t === 'labs' ? 'chart' : undefined)", tab)
                    settle(500)
                    got = js(CHART_LABELS_JS, sel)
                    names = sorted(js("(([a, b]) => MILESTONES.filter(m => dayTs(m.date) >= a && dayTs(m.date) <= b).map(m => m.label))(state.tab === 'health' ? hRange() : [state.from, state.to])"))
                    wrong = list(got["problems"])
                    for c in (c for c in got["charts"] if c["marked"]):
                        full = [" · ".join(n.rsplit(" · ", 1)[0] for n in title.split("\n")) for _, title in c["ms"]]
                        wrong += [f"{c['said'][:30]}: \"{t}\" is neither \"{f}\" nor its start" for (t, _), f in zip(c["ms"], full)
                                  if t != f and not (t.endswith("…") and f.startswith(t[:-1].rstrip()))]
                        if sorted(n for f in full for n in f.split(" · ")) != names:
                            wrong.append(f"{c['said'][:30]}: labelled {full}, not {names}")
                        if tab != "timeline" and not all(n in c["said"] for n in names):
                            wrong.append(f"{c['said'][:30]}: its aria-label doesn't name every milestone")
                    check(f"{tab} ({width} px, #312): milestone labels inside every chart, no mark on them, their full names kept",
                          bool(names) and any(c["marked"] for c in got["charts"]) and not wrong, wrong or got)
            js("switchTab('labs', 'chart')")   # still 320 px, marks on: a procedure left unnamed still opens its drawer
            settle(500)
            unnamed = js("""(() => { const svg = document.querySelector('#charts .chart svg'), g = svg._geom, xs = [...svg.querySelectorAll('.evlabel')].map(t => +t.getAttribute('x'));
                const h = [...svg.querySelectorAll('.evhead[data-k=procedure]')].find(h => !xs.some(x => Math.abs(x - 6 - h.cx.baseVal.value) < .5)); if (!h) return null;
                svg.scrollIntoView({block: 'center'}); const px = h.cx.baseVal.value, r = h.getBoundingClientRect(), xOf = t => g.L + (t - state.from) / (state.to - state.from) * (g.R - g.L);
                return {x: r.x + r.width / 2, y: r.y + r.height / 2, titles: markEvents().filter(e => Math.abs(xOf(e.ts) - px) < 8).map(e => e.title)}; })()""")
            if unnamed:
                page.mouse.click(unnamed["x"], unnamed["y"])
                page.wait_for_selector("#drawer.open", timeout=5000)
            check("…a procedure left unnamed at 320 px still opens its drawer when its mark is clicked",
                  unnamed is not None and js("document.getElementById('dtitle').textContent") in unnamed["titles"], unnamed)
            js("closeDrawer()")
            js("state.marks = window._was312[0]; switchTab('labs', 'chart')")
            page.set_viewport_size({"width": 1400, "height": 900})
            settle()
            js("scrollTo(0, window._was312[1])")   # where it was before the click scrolled the chart into view
            settle(200)
            # hovering a point shows where it came from (#100)
            box = page.locator("#charts .chart svg").first.bounding_box()
            tipped = ""
            for f in (0.5, 0.6, 0.4, 0.7, 0.3, 0.8, 0.2, 0.9):
                page.mouse.move(box["x"] + box["width"] * f, box["y"] + box["height"] * 0.5)
                settle(100)
                tipped = js("getComputedStyle(document.getElementById('tip')).display === 'block' ? document.getElementById('tip').innerText : ''")
                if "From " in tipped:
                    break
            check("hovering a Trends point shows its source and import date", "From Setup build, imported" in tipped, tipped[:200])
            page.mouse.move(0, 0)

            # ---- saved Trends views (#37): save what's charted as a view, switch between views, make one the default
            # (Trends starts with it when this browser has chosen nothing), on Settings too, at 375 px; deleted again
            kept = js("localStorage.getItem(stateKey())")
            labs3 = js("SERIES.filter(s => !s.watch && s.n >= 2).slice(0, 3).map(s => s.name)")
            picked = lambda: sorted(js("[...state.sel]"))
            js(f"state.sel = new Set({json.dumps(labs3[:2])}); renderLabs()")
            page.click("#viewSaveOpen")
            page.fill("#viewName", "CBC (browser check)")
            page.press("#viewName", "Enter")
            page.wait_for_function("document.getElementById('viewPick') && document.getElementById('viewPick').value === 'CBC (browser check)'", timeout=10000)
            check("a view is saved from what's charted, and shown as the current one",
                  js("CFG.trends.views.map(v => [v.name, v.labs])") == [["CBC (browser check)", labs3[:2]]]
                  and "Saved “CBC (browser check)”" in page.inner_text("#tvBox"), page.inner_text("#tvBox"))
            page.click(f"#series input#s_{re.sub(r'[^A-Za-z0-9_]', '_', labs3[2])}")
            settle(200)
            check("…ticking another lab leaves the view (the picker says so)", js("document.getElementById('viewPick').value") == "")
            js(f"state.sel = new Set({json.dumps(labs3[2:])}); renderLabs()")
            page.click("#viewSaveOpen")
            page.fill("#viewName", "Kidney (browser check)")
            page.click("#viewSave")
            page.wait_for_function("CFG.trends.views.length === 2", timeout=10000)
            page.select_option("#viewPick", "CBC (browser check)")
            settle(300)
            charted = js("[...document.querySelectorAll('#charts .chart .name')].map(n => n.textContent)")
            shown = js(f"{json.dumps(labs3[:2])}.map(nice)")   # charts show a lab's nickname, if it has one (#38)
            check("picking a view charts its labs", picked() == sorted(labs3[:2]) and sorted(charted) == sorted(shown), charted)
            page.click("#tvBox button:text-is('Make default')")
            page.wait_for_function("CFG.trends.views.some(v => v.default && v.name === 'CBC (browser check)')", timeout=10000)
            check("…and making it the default marks it", js("document.querySelector('#tvBox [aria-pressed=true]').textContent") == "Default ✓"
                  and "(default)" in js("document.getElementById('viewPick').selectedOptions[0].textContent"))
            page.select_option("#viewPick", "Kidney (browser check)")
            settle(200)
            check("switching views charts the other's labs", picked() == labs3[2:3])
            js("localStorage.removeItem(stateKey())")   # nothing chosen in this browser: Trends starts with the default view
            page.reload()
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0", timeout=20000)
            js("switchTab('labs')")
            settle(300)
            check("with nothing chosen in this browser, Trends starts with the default view",
                  picked() == sorted(labs3[:2]) and js("document.getElementById('viewPick').value") == "CBC (browser check)", picked())
            saved = js("api('profiles/' + PROFILE.id + '/settings').then(r => r.json()).then(j => j.trends.views.map(v => [v.name, v.default]))")
            check("…the views are kept by the server, for the profile", saved == [["CBC (browser check)", True], ["Kidney (browser check)", False]], saved)
            page.set_viewport_size({"width": 375, "height": 800})
            page.click("#viewSaveOpen")
            settle(200)
            check("the saved-views bar, with its form open, fits 375 px",
                  js("document.documentElement.scrollWidth <= 375 && document.getElementById('series').scrollWidth <= document.getElementById('series').clientWidth"),
                  js("[document.documentElement.scrollWidth, document.getElementById('series').scrollWidth]"))
            page.goto(base + "/settings.html?profile=1#sec-trends")
            page.wait_for_selector("[data-key='trends.views'] .set-view", timeout=10000)
            check("Settings lists the saved views, the default one ticked",
                  js("[...document.querySelectorAll(\"[data-key='trends.views'] .set-view\")].map(v => [v.querySelector('[data-k=name]').value, v.querySelector('[data-k=default]').checked])")
                  == [["CBC (browser check)", True], ["Kidney (browser check)", False]])
            check("…and fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.click("[data-key='trends.views'] button[aria-label='Remove the view Kidney (browser check)']")
            page.click("#sec-trends .set-foot button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-trends .set-foot > .msg').textContent.startsWith('Saved')", timeout=10000)
            page.set_viewport_size({"width": 1400, "height": 900})
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0", timeout=20000)
            js("switchTab('labs')")
            settle(300)
            check("a view removed on Settings is gone from Trends", js("[...document.querySelectorAll('#viewPick option')].map(o => o.value)") == ["CBC (browser check)"])
            page.click("#tvBox button:text-is('Delete')")
            answer_ask(page)
            page.wait_for_function("CFG.trends.views.length === 0", timeout=10000)
            check("deleting the last view from Trends leaves none", js("document.getElementById('viewPick').disabled") is True)
            if kept is not None:
                js(f"localStorage.setItem(stateKey(), {json.dumps(kept)})")
            page.reload()
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0", timeout=20000)
            js("switchTab('labs')")
            settle(300)

            # ---- keyboard and screen-reader basics (#209) on the records page (light theme): "Skip to content" readable;
            # the browser tab's title follows the tab, said only when the tab is opened from elsewhere (the tab bar
            # already says which tab is selected); a save and an error said through the shared status line and alert,
            # the message shown beside them too; the details drawer, covering the page at 375 px, keeps Tab inside
            # it, the page behind inert and out of Chromium's accessibility tree, focus given back on Escape; less
            # motion when the system asks for it
            page.reload()
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && booted", timeout=20000)
            page.keyboard.press("Tab")
            f = js(focused)
            check("records, light theme: “Skip to content” first, shown and readable (#209)", f["text"] == "Skip to content" and f["top"] >= 0
                  and contrast(f["color"], f["bg"]) >= 4.5, [f, contrast(f["color"], f["bg"])])
            js("switchTab('overview')")
            settle(200)
            js("document.getElementById('sgStatus').textContent = ''")
            page.click("#tab-labs")
            settle(300)
            in_bar = [page.title(), js("document.getElementById('sgStatus').textContent")]
            page.click("#tab-overview")
            settle(300)
            page.locator("#view-overview button", has_text="Open Costs").first.click()
            elsewhere_said = [page.title(), said(page)]
            check("the tab's title is the tab and the profile; picked in the tab bar, nothing more is said",
                  in_bar == ["Labs · My records · Sanguity", ""], in_bar)
            check("…opened from a button elsewhere, the tab is said through the status line", elsewhere_said == ["Costs · My records · Sanguity", "Costs"], elsewhere_said)
            # #255: a heading outline on every records tab: one h1 in <main>, the tab's name (visually hidden: the tab bar
            # shows it), over its sections' h2s and their parts' h3s, no level skipped (axe's heading-order and empty-heading)
            if AXE_JS and not js("typeof axe === 'object' && typeof axe.run === 'function'"):
                js(AXE_JS)
            outline = []
            for t in ("overview", "health", "timeline", "labs", "costs"):
                js(f"switchTab('{t}')")
                settle(300)
                outline.append([t, js("[...document.querySelectorAll('main h1')].filter(h => !h.closest('[hidden]')).map(h => h.textContent)"),
                                js("document.getElementById('tab-' + state.tab).textContent"),
                                js("() => axe.run(document, {runOnly: {type: 'rule', values: ['heading-order', 'empty-heading']}})"
                                   ".then(r => r.violations.map(v => v.id + ': ' + v.nodes.map(n => n.target.join(' ')).join(', ')))") if AXE_JS else ["no axe-core"]])
            check("every records tab has one h1 in its <main>, the tab's name, and its headings skip no level (#255)",
                  all(len(h1) == 1 and h1[0] == name and not bad for _, h1, name, bad in outline), outline)
            js("switchTab('labs')")
            settle(300)
            js(f"state.sel = new Set({json.dumps(labs3[:1])}); renderLabs()")
            page.click("#viewSaveOpen")
            page.fill("#viewName", "")
            page.click("#viewSave")
            err = [said(page, "sgAlert"), js("document.querySelector('#tvBox .tv-msg').textContent"),
                   js("document.querySelector('#tvBox .tv-msg').classList.contains('bad')"), js("document.querySelector('#tvBox .tv-msg').getAttribute('role')")]
            js("document.getElementById('sgStatus').textContent = ''")
            page.fill("#viewName", "Said (browser check)")
            page.click("#viewSave")
            page.wait_for_function("CFG.trends.views.length === 1", timeout=10000)
            saved_said = [said(page), page.inner_text("#tvBox .tv-msg")]
            check("an error is said at once through the alert (role=alert), and shown beside the form, not said twice",
                  err == ["Give the view a name.", "Give the view a name.", True, None], err)
            check("…a save through the status line (role=status), shown beside it too", saved_said[0].startswith("Saved “Said (browser check)”")
                  and saved_said[0] == saved_said[1], saved_said)
            js("api('profiles/' + PROFILE.id + '/settings/trends.views', {method: 'PUT', body: JSON.stringify({value: []})})")   # (no view left behind)
            page.set_viewport_size({"width": 375, "height": 800})
            js("switchTab('timeline')")
            settle(300)
            page.focus("#list .tl-row")
            page.keyboard.press("Enter")
            page.wait_for_selector("#drawer.open", timeout=5000)
            settle(300)
            behind = js("[...document.body.children].filter(x => x.inert).map(x => x.id || x.tagName.toLowerCase())")
            stops = []
            for _ in range(30):
                page.keyboard.press("Tab")
                stops.append(js("document.getElementById('drawer').contains(document.activeElement) ? 'drawer' : document.activeElement === document.body ? 'body' : document.activeElement.id || document.activeElement.className"))
            def ax_roles():   # the landmarks in Chromium's own accessibility tree, what a screen reader is given
                cdp = page.context.new_cdp_session(page)
                tree = cdp.send("Accessibility.getFullAXTree")["nodes"]
                cdp.detach()
                return sorted({n["role"]["value"] for n in tree if not n.get("ignored") and n.get("role", {}).get("value") in ("main", "banner", "complementary")})
            exposed = ax_roles()
            check("at 375 px the drawer covers the page: Tab stays inside it, the page behind inert (#209)",
                  "content" in behind and "drawer" not in behind and set(stops) <= {"drawer", "body"} and stops.count("drawer") > 20, [behind, stops])
            check("…and out of Chromium's accessibility tree: only the drawer is there", "main" not in exposed and "banner" not in exposed and "complementary" in exposed, exposed)
            page.keyboard.press("Escape")
            settle(300)
            check("…Escape closes it, the page usable again and focus back on the event it came from",
                  js("![...document.body.children].some(x => x.inert && x.id !== 'drawer') && document.getElementById('drawer').inert && document.activeElement.matches('#list .tl-row')"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.focus("#list .tl-row")
            page.keyboard.press("Enter")
            page.wait_for_selector("#drawer.open", timeout=5000)
            check("…at 1400 px it covers only part of the page, which stays usable", not js("document.querySelector('main').inert")
                  and ax_roles() == ["banner", "complementary", "main"], ax_roles())
            page.keyboard.press("Escape")
            # the drawer's slide (--dur-2) and a tab's hover (--dur-1, #256), pointed at
            motion = "[getComputedStyle(document.getElementById('drawer')).transitionDuration, smoothly(), getComputedStyle(document.getElementById('tab-labs')).transitionDuration]"
            page.hover("#tab-labs")
            page.emulate_media(reduced_motion="reduce")
            less = js(motion)
            page.emulate_media(reduced_motion="no-preference")
            check("less motion when asked for: no transitions, no smooth scrolling (and both back when not)", less == ["0s", "auto", "0s"]
                  and js(motion) == ["0.2s", "smooth", "0.12s, 0.12s, 0.12s"], [less, js(motion)])
            page.reload()   # the saved views as the server has them again (none)
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && booted", timeout=20000)

            # ---- Body and activity: drag changes the records bar's period (#171)
            page.click("#tab-health")
            settle()
            hbox = page.locator("#view-health svg.pz").first.bounding_box()
            hbefore = js("hRange()")
            page.mouse.move(hbox["x"] + hbox["width"] * 0.4, hbox["y"] + hbox["height"] * 0.5)
            page.mouse.down()
            page.mouse.move(hbox["x"] + hbox["width"] * 0.7, hbox["y"] + hbox["height"] * 0.5, steps=8)
            page.mouse.up()
            settle()
            check("Body and activity drag pans", js("hRange()[0]") < hbefore[0] and js("state.preset") == "custom")
            check("…its own range: the date boxes show it, and More… says Custom dates (#191)",
                  js("[!document.getElementById('dates').hidden, document.getElementById('from').value === isoDay(state.from), document.getElementById('periodMoreBtn').textContent]")
                  == [True, True, "Custom dates▾"], js("[document.getElementById('dates').hidden, document.getElementById('periodMoreBtn').textContent]"))

            # ---- one period for every tab with time (#171): picked on one tab, the same dates on the others (Labs in
            # both its views, #172); a calendar year, a milestone or dates typed in; kept on a reload; Costs by month or
            # by year; the records bar at 375 px on each tab, and none on the Overview
            page.click("#tab-timeline")
            page.click("#presets button:text-is('1y')")
            settle()
            want = js("[state.from, state.to]")
            seen = {}
            for t in ("labs", "heatmap", "health", "costs", "timeline"):
                open_tab(t)
                settle(300)
                seen[t] = js("""[state.preset, document.querySelector('#presets [aria-pressed=true]')?.textContent,
                    document.getElementById('from').value, getComputedStyle(document.querySelector('.filters')).display !== 'none']""")
            check("period: 1y picked on Timeline is the period on Labs (charts and heatmap), Body and activity and Costs",
                  all(v[:2] == ["1y", "1y"] and v[3] for v in seen.values()) and len({v[2] for v in seen.values()}) == 1, seen)
            check("…Body and activity charts those dates", js("hRange()") == want, [js("hRange()"), want])
            open_tab("costs")
            settle(300)
            check("…Costs counts the claims in them, and says so",
                  js("costRows().length > 0 && costRows().every(r => r.d >= isoDay(state.from) && r.d <= isoDay(state.to))")
                  and "the last year" in page.inner_text("#paidCard h2"), page.inner_text("#paidCard h2"))
            open_tab("overview")
            check("…the Overview has no records bar", js("getComputedStyle(document.querySelector('.filters')).display") == "none")
            year = js("periodYears().find(y => (DATA.costs || []).some(r => r.d.startsWith(y)))")
            open_tab("costs")
            page.click("#periodMoreBtn")   # the other periods are in More… (#191)
            opaque = r"c => { const m = c.match(/rgba?\(([^)]+)\)/); return !!m && (m[1].split(',').length < 4 || +m[1].split(',')[3] === 1); }"
            check("…the More… menu is readable: a solid background (not see-through over the page)",
                  js(f"(({opaque}))(getComputedStyle(document.getElementById('periodMoreList')).backgroundColor)"),
                  js("getComputedStyle(document.getElementById('periodMoreList')).backgroundColor"))
            page.click(f"#periodMoreList [data-period='{year}']")
            settle(300)
            check("period: a calendar year picked from More…", js("[state.preset, isoDay(state.from), isoDay(state.to)]") == [year, f"{year}-01-01", f"{year}-12-31"]
                  and js("y => { const b = document.getElementById('periodMoreBtn'); return b.classList.contains('on') && b.textContent.startsWith(y) && !document.querySelector('#presets [aria-pressed=true]'); }", year),
                  js("[state.preset, isoDay(state.from), isoDay(state.to), document.getElementById('periodMoreBtn').textContent]"))
            check("…Costs lists that year's claims", js("costRows().length") == js("y => DATA.costs.filter(r => r.d.startsWith(y)).length", year) > 0,
                  js("costRows().length"))
            js("applyPreset('all')")
            page.click("#costBy button:text-is('By year')")
            settle(300)
            labels = js("[...document.querySelectorAll('#view-costs svg[aria-label=\"Yearly insurance costs\"] text')].map(t => t.textContent).filter(t => !t.startsWith('$'))")
            check("…and groups them by year when asked", labels and all(re.fullmatch(r"\d{4}", x) for x in labels)
                  and "Insurance claims by year" in page.inner_text("#view-costs"), labels)
            page.click("#costBy button:text-is('By month')")
            js("costState.by = ''")
            other = js("(m => m ? msKey(m) : null)(MILESTONES.find(m => m !== primaryMs()))")   # the primary one is a button
            check("the demo has a milestone besides the primary one, for More…", other is not None)
            if other:
                page.click("#periodMoreBtn")
                page.click(f"#periodMoreList [data-period={other}]")
                settle(300)
                check("period: since a milestone, picked from More…", js("[state.preset, isoDay(state.from)]") == [other, js("k => msOf(k).date", other)],
                      js("[state.preset, isoDay(state.from)]"))
            open_tab("health")
            start = js("isoDay(state.max - 120 * DAY)")
            page.click("#periodMoreBtn")
            page.click("#periodMoreList [data-period=custom]")
            page.fill("#from", start)
            settle(300)
            check("period: dates typed in on Body and activity", js("[state.preset, isoDay(state.from), isoDay(hRange()[0])]") == ["custom", start, start],
                  js("[state.preset, isoDay(state.from), isoDay(hRange()[0])]"))
            open_tab("timeline")
            settle(300)
            check("…are the Timeline's too", js("[document.getElementById('from').value, isoDay(state.from)]") == [start, start]
                  and js("!document.getElementById('dates').hidden"), js("document.getElementById('from').value"))
            # the kinds' chips say which are shown: aria-pressed "true" or "false" (never left out), and an off one has no
            # tint (#193: on/off toggle chips, not faded)
            chips = r"[...document.querySelectorAll('#chips .toggle')].map(b => [b.textContent.trim().split(/ \d/)[0], b.getAttribute('aria-pressed'), getComputedStyle(b).backgroundColor])"
            clear = "rgba(0, 0, 0, 0)"
            check("Timeline: every kind's chip says whether it's shown, and a hidden kind has no tint (Insurance claims start hidden)",
                  all(p in ("true", "false") for _, p, _ in js(chips)) and all((p == "false") == (bg == clear) for _, p, bg in js(chips))
                  and any(n.startswith("Insurance claims") and p == "false" for n, p, _ in js(chips)), js(chips))
            page.click("#chips .kind:nth-child(2) .toggle")   # each chip with its ⋯ menu (#189)
            settle(200)
            check("…and clicking one hides it, showing so", js(chips)[1][1] == "false" and js(chips)[1][2] == clear, js(chips)[1])
            page.click("#chips .kind:nth-child(2) .toggle")   # each chip with its ⋯ menu (#189)
            settle(200)
            page.reload()
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && booted", timeout=20000)
            check("…and kept on a reload, the date boxes showing them", js("[state.preset, document.getElementById('from').value, !document.getElementById('dates').hidden]") == ["custom", start, True],
                  js("[state.preset, document.getElementById('from').value, !document.getElementById('dates').hidden]"))
            page.set_viewport_size({"width": 375, "height": 800})
            for t in ("health", "timeline", "labs", "heatmap", "costs"):
                open_tab(t)
                settle(300)
                fits = js("document.documentElement.scrollWidth")
                check(f"the records bar on {t} fits 375 px, with its period showing", fits <= 375 and
                      js("['presets', 'periodMoreBtn'].every(id => { const r = document.getElementById(id).getBoundingClientRect(); return r.width > 0 && r.right <= 375; })"), fits)
            page.set_viewport_size({"width": 1400, "height": 900})

            # ---- the period's controls (#191): each kind of period (a preset, All, the primary milestone's button, another
            # milestone and a calendar year from More…, custom dates) shows exactly one chosen control and its words; the
            # date boxes only for custom dates, Clear dates going back; More… is a menu worked by keyboard (arrow keys,
            # Escape back to its button), closed by a click outside, nothing in it cut off and its text readable, in light
            # and dark at 1400 and 375 px; old saved period keys still open
            period = r"""(() => {
              const more = document.getElementById('periodMoreBtn'), words = document.getElementById('periodWords');
              return {chosen: [...[...document.querySelectorAll('#presets [aria-pressed=true]')].map(b => b.textContent),
                               ...(more.classList.contains('on') ? ['More: ' + more.textContent.replace('▾', '')] : [])],
                      words: words.hidden ? null : words.textContent, dates: !document.getElementById('dates').hidden,
                      ticked: [...document.querySelectorAll('#periodMoreList [aria-checked=true]')].map(i => i.dataset.period)};
            })()"""
            was = js("[state.from, state.to]")   # the custom dates above, put back after
            open_tab("timeline")
            settle(200)
            pm = js("[msKey(primaryMs()), 'Since ' + lcLabel(primaryMs().label), fmtD(dayTs(primaryMs().date))]")
            om = js("(m => [msKey(m), 'Since ' + lcLabel(m.label), fmtD(dayTs(m.date))])(MILESTONES.find(m => m !== primaryMs()))")
            yr = js("periodYears()[1]")   # a whole year before this one
            kinds = [("2y", "#presets button:text-is('2y')", ["2y"], "Last 2 years", []),
                     ("all", "#presets button:text-is('All')", ["All"], "Everything on file", []),
                     (pm[0], f"#presets button:text-is('{pm[1]}')", [pm[1]], pm[2] + " – today", []),
                     (om[0], f"#periodMoreList [data-period={om[0]}]", ["More: " + om[1]], om[2] + " – today", [om[0]]),
                     (yr, f"#periodMoreList [data-period='{yr}']", ["More: " + yr], yr, [yr])]
            for key, sel, chosen, words, ticked in kinds:
                if sel.startswith("#periodMoreList"):
                    page.click("#periodMoreBtn")
                page.click(sel)
                settle(250)
                got = js(period)
                check(f"period {key}: one control chosen, its words beside it, no date boxes", js("state.preset") == key
                      and got == {"chosen": chosen, "words": words, "dates": False, "ticked": ticked}, got)
            page.click("#periodMoreBtn")
            page.click("#periodMoreList [data-period=custom]")
            settle(250)
            got = js(period)
            check("period custom: Custom dates… shows the date boxes (the period's dates, to change), More… the one chosen",
                  got == {"chosen": ["More: Custom dates"], "words": None, "dates": True, "ticked": ["custom"]} and js("document.activeElement.id") == "from"
                  and js("[document.getElementById('from').value, document.getElementById('to').value]") == [f"{yr}-01-01", f"{yr}-12-31"],
                  [got, js("document.activeElement.id")])
            page.click("#datesClear")
            settle(250)
            check("…and Clear dates goes back to the period before, the boxes gone", js(period)["chosen"] == ["More: " + yr] and not js(period)["dates"]
                  and js("document.activeElement.id") == "periodMoreBtn", [js(period), js("document.activeElement.id")])
            js("applyPreset('2y')")
            settle(200)
            menu_state = "[document.getElementById('periodMoreBtn').getAttribute('aria-expanded'), document.getElementById('periodMoreList').hidden, document.activeElement.id || document.activeElement.textContent]"
            check("More… is a menu button: aria-haspopup, aria-expanded and a menu of radio items",
                  js("(b => [b.getAttribute('aria-haspopup'), b.getAttribute('aria-expanded'), document.getElementById(b.getAttribute('aria-controls')).getAttribute('role')])(document.getElementById('periodMoreBtn'))")
                  == ["menu", "false", "menu"] and js("[...document.querySelectorAll('#periodMoreList button')].every(i => i.getAttribute('role') === 'menuitemradio' && i.tabIndex === -1)"))
            page.focus("#periodMoreBtn")
            page.keyboard.press("Enter")
            first = js(menu_state)
            page.keyboard.press("ArrowDown")
            second = js("document.activeElement.dataset.period")
            page.keyboard.press("End")
            last = js("document.activeElement.dataset.period")
            page.keyboard.press("Escape")
            check("…Enter opens it on its first item, arrow keys and End move, Escape closes it and focus goes back to the button",
                  first[:2] == ["true", False] and first[2].startswith(om[1]) and second and last == "custom"
                  and js(menu_state) == ["false", True, "periodMoreBtn"], [first, second, last, js(menu_state)])
            page.keyboard.press("ArrowDown")
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Enter")
            settle(250)
            check("…Enter on an item picks it, focus back on More…", js("state.preset") == second and js(menu_state) == ["false", True, "periodMoreBtn"],
                  [js("state.preset"), js(menu_state)])
            page.click("#periodMoreBtn")
            page.mouse.click(1300, page.locator("#periodMoreBtn").bounding_box()["y"] + 10)   # the records bar's empty end
            check("…and a click outside closes it", js(menu_state)[:2] == ["false", True], js(menu_state)[:2])
            menu_look = r"""(() => {
              const rgb = c => { const x = document.createElement('canvas').getContext('2d'); x.fillStyle = c; x.fillRect(0, 0, 1, 1); return [...x.getImageData(0, 0, 1, 1).data]; };
              const bg = n => { for (; n; n = n.parentElement) { const c = rgb(getComputedStyle(n).backgroundColor); if (c[3] > 0) return c; } return [255, 255, 255, 255]; };
              const lum = c => { const v = c.slice(0, 3).map(x => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; }); return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
              const ratio = n => { const [a, b] = [lum(rgb(getComputedStyle(n).color)), lum(bg(n))].sort((p, q) => q - p); return Math.round((a + 0.05) / (b + 0.05) * 10) / 10; };
              const list = document.getElementById('periodMoreList'), r = list.getBoundingClientRect(), items = [...list.querySelectorAll('[role=menuitemradio]')];
              return {inside: r.left >= 0 && r.right <= document.documentElement.clientWidth, solid: rgb(getComputedStyle(list).backgroundColor)[3] === 255,
                      cut: items.filter(i => i.scrollWidth > i.clientWidth + 1 || i.getBoundingClientRect().right > r.right).map(i => i.textContent),
                      text: Math.min(...items.map(ratio)), quiet: Math.min(...[...list.querySelectorAll('.menu-head, .menu-note')].map(ratio)),
                      chosen: ratio(document.getElementById('periodMoreBtn')), words: ratio(document.getElementById('periodWords'))};
            })()"""
            theme_before = js("document.documentElement.dataset.theme || null")
            for width in (1400, 375):
                page.set_viewport_size({"width": width, "height": 800 if width == 375 else 900})
                for theme in ("light", "dark"):
                    js("t => { document.documentElement.dataset.theme = t; }", theme)
                    js(f"applyPreset('{yr}')")   # More… chosen, its item ticked
                    settle(200)
                    bad = phone_fits(page, f"{theme}, {yr}") if width == 375 else []
                    page.click("#periodMoreBtn")
                    settle(100)
                    look = js(menu_look)
                    if width == 375:
                        bad += phone_fits(page, f"{theme}, More… open")
                    page.keyboard.press("Escape")
                    check(f"period at {width} px, {theme}: the More… menu fits the screen, nothing in it cut off, its text and the chosen period readable",
                          look["inside"] and look["solid"] and not look["cut"] and look["text"] >= 4.5 and look["quiet"] >= 4.5
                          and look["chosen"] >= 4.5 and look["words"] >= 4.5, look)
                    if width == 375:
                        page.click("#periodMoreBtn")
                        page.click("#periodMoreList [data-period=custom]")
                        settle(200)
                        bad += phone_fits(page, f"{theme}, custom dates")
                        check(f"period at 375 px, {theme}: the presets, More…, the words and the date boxes fit, no control cut off or overlapping",
                              not bad and js("['presets', 'periodMoreBtn', 'from', 'to', 'datesClear'].every(id => { const r = document.getElementById(id).getBoundingClientRect(); return r.width > 0 && r.right <= 375; })"),
                              bad[:6])
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            page.set_viewport_size({"width": 1400, "height": 900})
            # periods saved before #191: "dx" (the primary milestone), a milestone, a year
            for key, chosen in (("dx", [pm[1]]), (om[0], ["More: " + om[1]]), (yr, ["More: " + yr])):
                js("k => localStorage.setItem(stateKey(), JSON.stringify({...JSON.parse(localStorage.getItem(stateKey())), preset: k}))", key)
                page.reload()
                page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && booted", timeout=20000)
                check(f"a period saved as {key!r} opens as it was", js(period)["chosen"] == chosen and not js(period)["dates"], js(period))
            js("([a, b]) => setRange(a, b)", was)
            open_tab("labs")

            # ---- pick one and on or off (#193): every pick-one group (.seg) on each records tab, and the journal form's
            # kinds, has exactly one selected; every toggle chip (.toggle) says "true" or "false"; each kind has one look in
            # light, dark and forced colors, told apart by more than opacity; focus shows; the chips fit 375 px
            controls = r"""(() => {
              const vis = n => n.getClientRects().length > 0;
              const on = b => b.getAttribute('aria-pressed') === 'true' || b.getAttribute('aria-checked') === 'true' || !!b.querySelector(':scope > input:checked');
              const said = b => ['true', 'false'].includes(b.getAttribute('aria-pressed') || b.getAttribute('aria-checked')) || !!b.querySelector(':scope > input[type=radio]');
              const groups = [...document.querySelectorAll('.seg')].filter(vis).map(g => {
                let n = [...g.children].filter(on).length;   // the period: a preset, or More… naming what it picked (#191)
                if (g.id === 'presets') n += document.getElementById('periodMoreBtn').classList.contains('on');
                return [g.id || g.getAttribute('aria-label'), n, [...g.children].every(said)];
              });
              const toggles = [...document.querySelectorAll('.toggle')].filter(vis).map(t => {
                const box = t.querySelector(':scope > input[type=checkbox]');
                return [t.textContent.trim(), box ? String(box.checked) : t.getAttribute('aria-pressed')];
              });
              return {groups, toggles};
            })()"""
            want_groups = {"timeline": {"presets", "sortSeg"}, "labs": {"presets", "labsView"}, "heatmap": {"presets", "labsView"}, "costs": {"presets", "costBy"}}
            want_toggles = {"timeline": "Visits", "labs": "Procedures & imaging", "heatmap": None, "costs": None}
            for t in ("timeline", "labs", "heatmap", "costs"):
                open_tab(t)
                settle(300)
                c = js(controls)
                names = {g[0] for g in c["groups"]}
                check(f"pick one ({t}): every group has exactly one selected, each choice saying so (#193)",
                      want_groups[t] <= names and all(n == 1 and said for _, n, said in c["groups"]), c["groups"])
                check(f"on or off ({t}): every toggle says true or false (#193)", all(s in ("true", "false") for _, s in c["toggles"])
                      and (want_toggles[t] is None or any(n.startswith(want_toggles[t]) for n, _ in c["toggles"])), c["toggles"])
            open_tab("labs")
            check("…the chart overlays are toggle chips: Procedures & imaging, Medications, Journal",
                  js("['optMarks', 'optMeds', 'optJournal'].every(id => document.getElementById(id).parentElement.classList.contains('toggle'))"))
            js("journalNew()")
            settle(200)
            c = js(controls)
            check("pick one: the journal form's kinds have exactly one checked", [g[1:] for g in c["groups"] if g[0] == "Kind of entry"] == [[1, True]], c["groups"])
            js("closeDrawer()")
            open_tab("timeline")
            look = r"""(() => {
              const cs = (n, p) => getComputedStyle(n, p || null), lab = [...document.querySelectorAll('#chips .toggle')].find(b => b.textContent.startsWith('Labs'));
              const vis = [...document.querySelectorAll('#chips .toggle')].find(b => b.textContent.startsWith('Visits'));
              const s = [...document.querySelectorAll('#sortSeg button')], sel = s.find(b => b.getAttribute('aria-pressed') === 'true'), un = s.find(b => b !== sel);
              const t = b => ({bg: cs(b).backgroundColor, color: cs(b).color, border: cs(b).borderColor + ' ' + cs(b).borderStyle, mark: cs(b, '::before').content,
                               key: cs(b.querySelector('.key')).backgroundColor, opacity: +cs(b).opacity});
              return {shown: t(vis), hidden: t(lab), sel: [cs(sel).backgroundColor, cs(sel).boxShadow, cs(sel).fontWeight, +cs(sel).opacity],
                      un: [cs(un).backgroundColor, cs(un).boxShadow, cs(un).fontWeight, +cs(un).opacity]};
            })()"""
            theme_before = js("document.documentElement.dataset.theme || null")
            js("state.cats.delete('lab'); state.cats.add('visit'); renderAll()")
            for mode in ("light", "dark", "forced colors"):
                if mode == "forced colors":
                    page.emulate_media(forced_colors="active")
                else:
                    js("t => { document.documentElement.dataset.theme = t; }", mode)
                settle(200)
                lk = js(look)
                sh, hd = lk["shown"], lk["hidden"]
                check(f"on or off ({mode}): shown has a fill, a ✓ and its colour key; hidden is outlined only, no ✓, a grey key; not by opacity",
                      sh["bg"] != hd["bg"] and (mode == "forced colors" or hd["bg"] == "rgba(0, 0, 0, 0)")
                      and "✓" in sh["mark"] and hd["mark"] in ("none", "normal") and sh["key"] != hd["key"] and sh["border"] != hd["border"]
                      and sh["opacity"] == hd["opacity"] == 1, lk)
                check(f"pick one ({mode}): the selected choice has its own fill, and a bar or the system highlight; not by opacity",
                      lk["sel"][0] != lk["un"][0] and lk["sel"][3] == lk["un"][3] == 1
                      and (mode == "forced colors" or ("inset" in lk["sel"][1] and lk["un"][1] == "none" and int(lk["sel"][2]) > int(lk["un"][2]))), lk)
            page.emulate_media(forced_colors="none")
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            js("state.cats.add('lab'); renderAll()")
            settle(200)
            page.focus("#chips .kind-more[data-k=watch]")
            page.keyboard.press("Shift+Tab")   # back from its ⋯ menu button (#189): the Watch chip
            ring = "(e => [e.className, e.matches(':focus-visible'), getComputedStyle(e).outlineStyle, getComputedStyle(e).outlineWidth])(document.activeElement)"
            check("focus shows on a toggle chip reached by keyboard", js(ring)[0] == "toggle" and js(ring)[1:3] == [True, "solid"], js(ring))
            page.focus("#sortSeg [data-sort=desc]")
            page.keyboard.press("Tab")
            check("…and on a pick-one choice", js("document.activeElement.dataset.sort") == "asc" and js(ring)[1:3] == [True, "solid"], js(ring))
            page.set_viewport_size({"width": 375, "height": 800})
            open_tab("timeline")
            page.click("#ftoggle")
            settle(200)
            # the page's width, then each shown control's right edge
            fit = "sel => [document.documentElement.scrollWidth, ...[...document.querySelectorAll(sel)].filter(n => n.getClientRects().length).map(n => Math.round(n.getBoundingClientRect().right))]"
            check("on or off at 375 px: the kinds' chips wrap inside the screen", max(js(fit, "#chips .toggle")) <= 375 and len(js(fit, "#chips .toggle")) > 3, js(fit, "#chips .toggle"))
            page.click("#ftoggle")
            open_tab("labs")
            settle(300)
            check("…and the chart overlays", max(js(fit, "#view-labs .toggle")) <= 375   # and Doses logged once doses are (#235)
                  and len(js(fit, "#view-labs .toggle")) == 1 + 3 + (1 if js("DOSES.length") else 0), js(fit, "#view-labs .toggle"))
            page.set_viewport_size({"width": 1400, "height": 900})

            # ---- Timeline kinds: each chip's name says its state; Show all and Only this by mouse, keyboard and on a
            # phone; a hidden kind's count muted and its ⋯ untinted in light, dark and forced colors; kept on a reload (#189)
            open_tab("timeline")
            settle(300)
            kinds = r"""[...document.querySelectorAll('#chips .kind')].map(k => { const b = k.querySelector('.toggle');
              return [b.dataset.k, b.getAttribute('aria-pressed'), b.getAttribute('aria-label'), b.querySelector('.n').textContent]; })"""
            lanes = js("CATS.filter(hasLane).map(c => c.k)")
            ks, names = js(kinds), js("Object.fromEntries(CATS.map(c => [c.k, c.label]))")
            check("kinds (#189): each chip's name says its kind, count and whether it's shown, as its pressed state does",
                  [k for k, *_ in ks] == lanes and all(lab == f"{names[k]}, {n}, {'shown' if p == 'true' else 'hidden'}" for k, p, lab, n in ks)
                  and ["claim", "false", f"{names['claim']}, {ks[lanes.index('claim')][3]}, hidden"] == ks[lanes.index("claim")][:3], ks)
            vis_n = names["visit"]
            check("…found by that name (\"Visits, 31, shown\")", page.get_by_role("button", name=re.compile(rf"^{vis_n}, \d+, shown$")).count() == 1)
            check("…and as kinds start (Insurance claims hidden), no \"Show all\" button beside the chips and no filter line: Reset filters took that place (#190)",
                  not js("!!document.querySelector('#chips .kinds-all')") and page.is_hidden("#fsum"))
            # Only this, by mouse: the ⋯ beside Visits, then "Show only Visits"
            page.click("#chips .kind-more[data-k=visit]")
            menu = "[...document.querySelectorAll('#chips .menu-list')].filter(m => !m.hidden).map(m => [m.id, ...[...m.querySelectorAll('[role=menuitem]')].map(i => i.textContent)])"
            check("kinds: a chip's ⋯ opens its menu, Show only this and Show all", js(menu) == [["kindMenu-visit", f"Show only {vis_n}", "Show all"]]
                  and js("document.querySelector('#chips .kind-more[data-k=visit]').getAttribute('aria-expanded')") == "true", js(menu))
            js("document.getElementById('sgStatus').textContent = ''")
            page.click("#kindMenu-visit [role=menuitem]:first-child")
            settle(200)
            only = lambda: [k for k, p, *_ in js(kinds) if p == "true"]
            check("kinds: Show only Visits shows Visits alone, on the chips and the timeline, and says so",
                  only() == ["visit"] and js(menu) == [] and said(page) == f"Showing only {vis_n}"
                  and js("[...document.querySelectorAll('#list .tl-row')].length > 0 && EV.filter(visible).every(e => CAT_OF[e.k].k === 'visit')"), only())
            page.reload()
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && booted", timeout=20000)
            settle(300)
            check("…kept on a reload", only() == ["visit"] and js("JSON.parse(localStorage.getItem(stateKey())).cats.filter(k => CATS.filter(hasLane).some(c => c.k === k))") == ["visit"], only())
            # a hidden kind's look: the count muted, the key grey and its ⋯ untinted, in each theme (#193 checks the chip itself)
            part = r"""k => { const w = document.querySelector(`#chips .toggle[data-k=${k}]`).parentElement, cs = n => getComputedStyle(n);
              return {n: cs(w.querySelector('.n')).color, key: cs(w.querySelector('.key')).backgroundColor, more: cs(w.querySelector('.kind-more')).backgroundColor}; }"""
            theme_before = js("document.documentElement.dataset.theme || null")
            for mode in ("light", "dark", "forced colors"):
                if mode == "forced colors":
                    page.emulate_media(forced_colors="active")
                else:
                    js("t => { document.documentElement.dataset.theme = t; }", mode)
                settle(150)
                sh, hd = js(part, "visit"), js(part, "lab")
                check(f"kinds ({mode}): a hidden kind's count is muted, its key grey and its ⋯ untinted, unlike a shown one's",
                      sh["n"] != hd["n"] and sh["key"] != hd["key"] and sh["more"] != hd["more"] if mode != "forced colors"
                      else sh["key"] != hd["key"] and sh["more"] != hd["more"], [sh, hd])
            page.emulate_media(forced_colors="none")
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            # Show all, by keyboard from a chip's menu: Enter on the ⋯ opens it on its first item, arrow down, Enter
            page.focus("#chips .kind-more[data-k=lab]")
            page.keyboard.press("Enter")
            check("kinds by keyboard: Enter on a ⋯ opens its menu, focus on its first item",
                  js("[document.activeElement.textContent, document.activeElement.closest('[role=menu]')?.id]") == [f"Show only {names['lab']}", "kindMenu-lab"],
                  js("document.activeElement.outerHTML"))
            page.keyboard.press("Escape")
            check("…Escape closes it, focus back on the ⋯", js("document.activeElement.matches('.kind-more[data-k=lab]')") and js(menu) == [])
            page.keyboard.press("ArrowDown")
            page.keyboard.press("ArrowDown")
            js("document.getElementById('sgStatus').textContent = ''")
            page.keyboard.press("Enter")
            settle(200)
            check("…Show all from it shows every kind, says so, focus kept on that ⋯, and \"Show all\" goes",
                  only() == lanes and said(page) == "Showing every kind" and js("document.activeElement.matches('.kind-more[data-k=lab]')")
                  and page.is_hidden("#fsum"), [only(), js("document.activeElement.outerHTML")])
            # a chip toggled by keyboard keeps focus; right-click (or the menu key, or a long press) on a chip opens its menu
            page.focus("#chips .toggle[data-k=imaging]")
            page.keyboard.press("Space")
            settle(150)
            check("kinds by keyboard: Space hides a kind, focus kept on its chip, its name saying hidden",
                  js("document.activeElement.matches('.toggle[data-k=imaging]') && document.activeElement.getAttribute('aria-label').endsWith(', hidden')"))
            page.click("#chips .toggle[data-k=study]", button="right")
            check("kinds: right-click on a chip opens its menu", js(menu)[:1] == [["kindMenu-study", f"Show only {names['study']}", "Show all"]], js(menu))
            page.keyboard.press("Escape")
            page.click("#fsum .fsum-reset")   # the filter line's Reset filters (#190), where a "Show all" button was
            settle(200)
            check("kinds: the filter line's Reset filters shows every kind (Insurance claims shown, as they were), focus on the first chip", only() == lanes
                  and js("document.activeElement === document.querySelector('#chips .toggle')"), only())
            # 375 px, light and dark: the chips and a menu open on the last one fit
            page.set_viewport_size({"width": 375, "height": 800})
            page.click("#ftoggle")
            js("state.cats.delete('claim'); renderAll()")
            for theme in ("light", "dark"):
                js("t => { document.documentElement.dataset.theme = t; }", theme)
                last = lanes[-1]
                page.click(f"#chips .kind-more[data-k={last}]")
                settle(150)
                box = js(f"(r => [r.left, r.right])(document.getElementById('kindMenu-{last}').getBoundingClientRect())")
                check(f"kinds at 375 px ({theme}): chips and an open menu fit", phone_fits(page, "Timeline kinds") == [] and box[0] >= 0 and box[1] <= 375,
                      [phone_fits(page, "Timeline kinds"), box])
                page.click(f"#chips .kind-more[data-k={last}]")
            page.click("#chips .kind-more[data-k=visit]")
            page.click("#kindMenu-visit [role=menuitem]:first-child")
            settle(200)
            check("kinds at 375 px: Show only this from the ⋯", only() == ["visit"] and phone_fits(page, "Timeline kinds") == [], only())
            page.click("#fsum .fsum-reset")
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            js("state.cats.delete('claim'); renderAll()")   # as the checks after this expect: Insurance claims hidden
            page.click("#ftoggle")
            page.set_viewport_size({"width": 1400, "height": 900})

            # ---- what the filters leave out (#190): on each tab that filters, one line saying how, ending in Reset filters,
            # which puts that tab's filters back but not the period. The Timeline's is under the records bar, still shown on
            # a phone with the panel closed, the Filters button counting the filters on and its counts captioned; the Labs
            # name filter's is under its box (charts and heatmap); each Costs list's under its filters, their totals then
            # for what's shown. Filtered and not, at 1400 and 375 px, light and dark (phone_fits, and the line's contrast).
            # The journal's card is checked with the journal, above.
            fsum = lambda sel="#fsum": js("s => (p => p && !p.hidden && p.getClientRects().length ? p.textContent : null)(document.querySelector(s))", sel)
            start_kinds = "CATS.filter(hasLane).every(c => state.cats.has(c.k) === (c.k !== 'claim'))"
            ftog = lambda: js("document.getElementById('ftoggle').textContent")
            theme_before = js("document.documentElement.dataset.theme || null")
            set_theme = lambda t: js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", t)
            def line_look(sel):   # the line's words and its Reset against the background behind them: [contrast, contrast]
                c = js("""s => { const p = document.querySelector(s), bg = n => { for (; n; n = n.parentElement) { const b = getComputedStyle(n).backgroundColor;
                  if (b !== 'rgba(0, 0, 0, 0)' && b !== 'transparent') return b; } return 'rgb(255, 255, 255)'; };
                  return [getComputedStyle(p.querySelector('.fsum-t')).color, getComputedStyle(p.querySelector('.fsum-reset')).color, bg(p)]; }""", sel)
                return [contrast(c[0], c[2]), contrast(c[1], c[2])]
            open_tab("timeline")
            tl_preset = js("state.preset")
            js("applyPreset('1y')")
            settle(300)
            check("filters (#190): the Timeline as it starts (only Insurance claims hidden): no filter line, no count on Filters, its counts not captioned",
                  fsum() is None and js(start_kinds) and js("state.q") == "" and ftog() == "Filters" and page.is_hidden("#statsNote"), [fsum(), ftog()])
            page.click("#chips .toggle[data-k=lab]")
            js("document.getElementById('sgStatus').textContent = ''")
            page.click("#q")
            page.locator("#q").press_sequentially("Visit", delay=60)
            settle(300)
            want = js("(k => `Showing ${k.filter(c => state.cats.has(c.k)).length} of ${k.length} kinds · matching “Visit” · Reset filters`)(CATS.filter(hasLane))")
            check("…a kind hidden and a search typed: one line under the records bar says so, “Showing 9 of 11 kinds · matching “Visit” · Reset filters”",
                  fsum() == want and js("document.getElementById('fsum').parentElement.classList.contains('filters')"), [fsum(), want])
            n_vis = js("EV.filter(visible).length")
            check("…its counts say they're for what's shown, as the list's are", page.is_visible("#statsNote") and "for what's shown" in page.inner_text("#statsNote")
                  and page.inner_text("#listTitle") == f"Events ({n_vis:,})", page.inner_text("#listTitle"))
            n_said = f"{n_vis:,} event{'s' if n_vis != 1 else ''} match." if n_vis else "No events match."
            check("…how many events match is said once typing stops, not for each key", said(page, tries=40) == n_said,
                  [js("document.getElementById('sgStatus').textContent"), n_said])
            for theme in ("light", "dark"):
                set_theme(theme)
                settle(100)
                look = line_look("#fsum")
                check(f"…at 1400 px ({theme}): the line and its Reset filters are readable", min(look) >= 4.5 and fsum() == want, look)
            page.set_viewport_size({"width": 375, "height": 800})
            for theme in ("light", "dark"):
                set_theme(theme)
                settle(200)
                closed = [fsum() == want, page.is_hidden("#chips"), page.is_hidden("#q"), ftog()]
                bad = phone_fits(page, f"Timeline filtered, {theme}")
                page.click("#ftoggle")
                settle(150)
                bad += phone_fits(page, f"Timeline filtered, panel open, {theme}")
                page.click("#ftoggle")
                look = line_look("#fsum")
                check(f"filters at 375 px ({theme}): with the panel closed the line stays, “Filters (2)” says how many are on; it fits, open or closed, and reads",
                      closed == [True, True, True, "Filters (2)"] and not bad and min(look) >= 4.5, [closed, bad, look])
            js("document.getElementById('sgStatus').textContent = ''")
            page.click("#fsum .fsum-reset")
            settle(200)
            check("…Reset filters there puts the kinds and the search back, not the period; said; focus on the Filters button",
                  fsum() is None and js(start_kinds) and js("state.q") == "" and page.input_value("#q") == "" and js("state.preset") == "1y"
                  and ftog() == "Filters" and page.is_hidden("#statsNote") and said(page) == "Filters reset." and js("document.activeElement.id") == "ftoggle",
                  [fsum(), js("state.preset"), js("document.activeElement.id")])
            for theme in ("light", "dark"):
                set_theme(theme)
                settle(150)
                bad = phone_fits(page, f"Timeline unfiltered, {theme}")
                page.click("#ftoggle")
                bad += phone_fits(page, f"Timeline unfiltered, panel open, {theme}")
                page.click("#ftoggle")
                check(f"…and unfiltered at 375 px ({theme}), it all fits, open or closed", not bad and fsum() is None, bad)
            page.set_viewport_size({"width": 1400, "height": 900})
            set_theme(theme_before)
            page.click("#chips .kind-more[data-k=visit]")
            page.click("#kindMenu-visit [role=menuitem]:first-child")
            settle(200)
            check("…Show only Visits: “Showing only Visits · Reset filters”", fsum() == f"Showing only {names['visit']} · Reset filters", fsum())
            page.focus("#fsum .fsum-reset")
            page.keyboard.press("Enter")
            settle(200)
            check("…Reset filters by keyboard: as kinds start, focus on the first chip", fsum() is None and js(start_kinds)
                  and js("document.activeElement === document.querySelector('#chips .toggle')"), js("document.activeElement.outerHTML"))
            # Labs: the name filter's line, under its box in the list's header and the heatmap's
            open_tab("labs")
            js("clearLabFilter()")
            settle(300)
            check("filters on Labs (#190): no name filter, no line", fsum("#labSum") is None and ftog() == "Dates")
            page.fill("#labQ", "hemo")
            settle(400)
            lab_want = "Showing lab names matching “hemo” · Reset filters"
            check("…filtered: under the box, “Showing lab names matching “hemo” · Reset filters”", fsum("#labSum") == lab_want
                  and js("document.getElementById('labSum').previousElementSibling.contains(document.getElementById('labQ'))"), fsum("#labSum"))
            page.set_viewport_size({"width": 375, "height": 800})
            for theme in ("light", "dark"):
                set_theme(theme)
                settle(200)
                bad, look = phone_fits(page, f"Labs filtered, {theme}"), line_look("#labSum")
                check(f"…at 375 px ({theme}) it fits and reads", not bad and min(look) >= 4.5 and fsum("#labSum") == lab_want, [bad, look])
            page.set_viewport_size({"width": 1400, "height": 900})
            set_theme(theme_before)
            open_tab("heatmap")
            settle(400)
            check("…the heatmap's header has the same line", fsum("#hmSum") == lab_want, fsum("#hmSum"))
            js("document.getElementById('sgStatus').textContent = ''")
            page.click("#hmSum .fsum-reset")
            settle(300)
            check("…its Reset filters empties both boxes, said, focus in the heatmap's box", fsum("#hmSum") is None and js("state.labQ") == ""
                  and page.input_value("#hmQ") == page.input_value("#labQ") == "" and said(page) == "Filters reset." and js("document.activeElement.id") == "hmQ")
            open_tab("labs")
            settle(300)
            check("…and on the chart, no line", fsum("#labSum") is None)
            # Costs: each list's filters, the claims' and the out-of-pocket amounts'
            open_tab("costs")
            js("applyPreset('all')")
            settle(400)
            claims_hint = "document.querySelector('#costTable .toolbar ~ p.hint').textContent"
            check("filters on Costs (#190): unfiltered, no line by either list, the claims' totals for them all",
                  fsum("#costTable .fsum") is None and fsum("#paidTable .fsum") is None and re.match(r"^[\d,]+ claims · billed", js(claims_hint)), js(claims_hint))
            page.select_option("#costTable select.search", "Pharmacy")
            settle(400)
            c_want = js("(r => `Showing ${r.filter(x => x.cat === 'Pharmacy').length.toLocaleString()} of ${r.length.toLocaleString()} claims · type of care: Pharmacy · Reset filters`)(costRows())")
            check("…a type of care picked: “Showing 13 of 96 claims · type of care: Pharmacy · Reset filters”, the totals for the claims shown",
                  fsum("#costTable .fsum") == c_want and js(claims_hint).startswith("For the claims shown: billed $"), [fsum("#costTable .fsum"), c_want, js(claims_hint)])
            page.click("#paidTable .toggle")   # HSA only
            settle(400)
            p_want = js("(r => `Showing ${r.filter(x => x.hsa === 'HSA').length.toLocaleString()} of ${r.length.toLocaleString()} amounts · paid with an HSA · Reset filters`)(paidRows())")
            check("…HSA only: “Showing 12 of 40 amounts · paid with an HSA · Reset filters”, the total for the amounts shown",
                  fsum("#paidTable .fsum") == p_want and page.inner_text("#paidTable .toolbar .hint").startswith("For the amounts shown: $"),
                  [fsum("#paidTable .fsum"), p_want, page.inner_text("#paidTable .toolbar .hint")])
            page.set_viewport_size({"width": 375, "height": 800})
            for theme in ("light", "dark"):
                set_theme(theme)
                settle(200)
                bad, look = phone_fits(page, f"Costs filtered, {theme}"), line_look("#paidTable .fsum") + line_look("#costTable .fsum")
                check(f"…at 375 px ({theme}) both fit and read", not bad and min(look) >= 4.5, [bad, look])
            page.set_viewport_size({"width": 1400, "height": 900})
            set_theme(theme_before)
            settle(300)   # the resize redraws the tab first (timeline.js), which would take the focus Reset gives
            page.click("#costTable .fsum-reset")
            settle(300)
            check("…the claims' Reset filters clears their filters only, focus in their search",
                  fsum("#costTable .fsum") is None and js("[costState.q, costState.cat, costState.deniedOnly]") == ["", "", False]
                  and fsum("#paidTable .fsum") == p_want and js("document.activeElement.matches('#costTable input.search')"),
                  [js("[costState.q, costState.cat, costState.deniedOnly]"), fsum("#paidTable .fsum"), p_want, js("document.activeElement.outerHTML.slice(0, 90)")])
            page.click("#paidTable .fsum-reset")
            settle(300)
            check("…the amounts' puts HSA only off, focus on that toggle; the period kept", fsum("#paidTable .fsum") is None and js("paidState.q") == ""
                  and js("document.activeElement.matches('#paidTable .toggle') && document.activeElement.getAttribute('aria-pressed') === 'false'")
                  and js("state.preset") == "all" and re.match(r"^[\d,]+ amounts? · \$", page.inner_text("#paidTable .toolbar .hint")))
            page.set_viewport_size({"width": 375, "height": 800})
            for theme in ("light", "dark"):
                set_theme(theme)
                settle(200)
                bad = phone_fits(page, f"Costs unfiltered, {theme}")
                check(f"…and unfiltered at 375 px ({theme}), it fits", not bad, bad)
            page.set_viewport_size({"width": 1400, "height": 900})
            set_theme(theme_before)
            js("p => applyPreset(p)", tl_preset)
            settle(200)

            # ---- Labs: the name filter in the lab list's header with its count, the overlays grouped apart from the
            # legend (#192); one filter for the chart's list and the heatmap, apart from the Timeline's search
            open_tab("labs")
            js("clearLabFilter()")
            settle(300)
            check("Labs: the records bar shows no search box (Timeline's alone)", not page.is_visible("#q") and js("document.getElementById('ftoggle').textContent") == "Dates")
            order = js("""(() => { const y = s => document.querySelector(s).getBoundingClientRect().top, hd = document.querySelector('#series .series-hd');
              return [hd.contains(document.getElementById('labQ')), hd.contains(document.getElementById('viewPick')),
                      [...hd.querySelectorAll('#seriesSel button')].map(b => b.textContent), y('#labQ') < y('#seriesSel') && y('#seriesSel') < y('#tvBox'),
                      y('#tvBox') < y('#seriesRows')]; })()""")
            check("…the name filter heads the lab list, above what's selected, Clear, Defaults and the saved views",
                  order == [True, True, ["Clear", "Defaults"], True, True], order)
            n_all = js("SERIES.filter(s => !s.watch && s.n >= 2).length")
            rows = lambda: js("document.querySelectorAll('#seriesRows .srow').length")
            check("…its count says how many labs there are", page.inner_text("#labQn") == f"{n_all} labs" and rows() == n_all, [page.inner_text("#labQn"), rows()])
            sel_before = js("[...state.sel].sort()")
            page.click("#labQ")
            page.locator("#labQ").press_sequentially("hemo", delay=40)
            settle(400)
            want = js("SERIES.filter(s => !s.watch && s.n >= 2 && [s.name, nice(s.name), s.panel].some(t => t.toLowerCase().includes('hemo'))).length")
            check("…typing filters the list and counts the matches (\"k of n\"), keeping the focus in the box",
                  0 < want < n_all and rows() == want and page.inner_text("#labQn") == f"{want} of {n_all}"
                  and js("document.activeElement.id") == "labQ" and js("state.labQ") == "hemo", [rows(), want, page.inner_text("#labQn")])
            check("…said to a screen reader, and the charts and their selection stay as they were",
                  said(page) == f"{want} of {n_all} labs match." and js("[...state.sel].sort()") == sel_before)
            page.click("#seriesSel button:text-is('Clear')")
            settle(300)
            check("…Clear still clears while filtering, the filter kept", js("state.sel.size") == 0 and rows() == want and page.input_value("#labQ") == "hemo")
            page.click("#seriesSel button:text-is('Defaults')")
            settle(300)
            check("…and Defaults brings the starting labs back", js("[...state.sel].sort()") == js("[...startLabs()].sort()") and js("state.sel.size") > 0)
            check("…the saved views stay beside them", page.is_visible("#viewPick"))
            page.click("#labsView button[data-view=heatmap]")
            settle(400)
            hm_all = js("SERIES.filter(s => !s.watch && s.n >= 20 && s.pts.some(p => p[2] != null || p[3] != null)).length")
            hm_want = js("SERIES.filter(s => !s.watch && s.n >= 20 && s.pts.some(p => p[2] != null || p[3] != null) && labMatch(s)).length")
            hm_rows = lambda: js("document.querySelectorAll('#hm text.row').length")
            check("…the heatmap has the same filter in its header, its rows and count filtered by it",
                  page.input_value("#hmQ") == "hemo" and hm_rows() == hm_want > 0 and page.inner_text("#hmQn") == f"{hm_want} of {hm_all}",
                  [page.input_value("#hmQ"), hm_rows(), hm_want, page.inner_text("#hmQn")])
            page.fill("#hmQ", "")
            settle(400)
            check("…emptied there, every lab with ranges", hm_rows() == hm_all and page.inner_text("#hmQn") == f"{hm_all} labs" and js("state.labQ") == "")
            page.fill("#hmQ", "zzzz")
            settle(400)
            check("…and none matching says so", page.inner_text("#hmQn") == f"None of {hm_all}" and "No lab names match" in js("document.getElementById('hm').textContent"))
            page.click("#labsView button[data-view=chart]")
            settle(400)
            check("…back on the chart, the list has what was typed there", page.input_value("#labQ") == "zzzz" and rows() == 0
                  and page.inner_text("#labQn") == f"None of {n_all}" and "No lab names match “zzzz”" in page.inner_text("#seriesRows"))
            page.click("#seriesRows button:text-is('Clear the filter')")
            settle(300)
            check("…its Clear the filter shows them all again, focus back in the box",
                  rows() == n_all and page.input_value("#labQ") == "" and page.input_value("#hmQ") == "" and js("document.activeElement.id") == "labQ")
            open_tab("timeline")
            page.fill("#q", "hemo")
            settle(400)
            check("Timeline: its search keeps its own words, apart from the Labs filter", page.get_attribute("#q", "placeholder") == "Search events…"
                  and js("state.q") == "hemo" and js("state.labQ") == "")
            open_tab("labs")
            settle(300)
            check("…and doesn't filter the Labs list", rows() == n_all and page.input_value("#labQ") == "")
            open_tab("timeline")
            page.fill("#q", "")
            settle(300)
            open_tab("labs")
            settle(300)
            grp = js("""(() => { const g = document.getElementById('optMarks').closest('[role=group]'), lg = document.querySelector('#labsChart .legend');
              return [g && document.getElementById(g.getAttribute('aria-labelledby')).textContent, ['optMarks', 'optMeds', 'optJournal'].map(id => g && g.contains(document.getElementById(id))),
                      [...g.querySelectorAll('.toggle')].filter(t => !t.hidden).map(t => t.textContent.trim()),
                      !!lg && !g.contains(lg) && !lg.querySelector('input, button') && lg.textContent.includes('Out of range')]; })()""")
            check("…the chart overlays grouped under “Show on charts:”, the legend apart from them and only a legend",
                  grp == ["Show on charts:", [True, True, True], ["Procedures & imaging", "Medications"] + (["Doses logged"] if js("DOSES.length") else [])
                          + ["Journal"], True], grp)   # Doses logged once doses are (#235)
            marks0 = js("state.marks")
            page.click("label:has(#optMarks)")
            settle(300)
            check("…Procedures & imaging still turns its marks off and on from there", js("state.marks") == (not marks0)
                  and page.is_checked("#optMarks") == (not marks0))
            page.click("label:has(#optMarks)")
            settle(300)
            theme_before = js("document.documentElement.dataset.theme || null")
            for mode in ("light", "dark"):
                js("t => { document.documentElement.dataset.theme = t; }", mode)
                page.set_viewport_size({"width": 375, "height": 800})
                open_tab("labs")
                page.fill("#labQ", "hemo")
                settle(400)
                bad = phone_fits(page, f"Labs, filtered ({mode})")
                look = js("""(() => { const i = document.getElementById('labQ'), n = document.getElementById('labQn'), r = i.getBoundingClientRect(), c = n.getBoundingClientRect();
                  return [getComputedStyle(i).color !== getComputedStyle(i).backgroundColor, getComputedStyle(n).color !== getComputedStyle(document.querySelector('#series .series-hd')).backgroundColor,
                          Math.abs(r.top - c.top) < r.height, c.right <= innerWidth]; })()""")
                check(f"…at 375 px ({mode}): the filter and its count on one line, readable, the list and overlays fitting", look == [True, True, True, True] and not bad, [look, bad])
                page.fill("#labQ", "zzzz")
                settle(400)
                bad = phone_fits(page, f"Labs, nothing matching ({mode})")
                check(f"…nothing matching fits too ({mode})", page.is_visible("#seriesRows .lab-none") and not bad, bad)
                page.click("#labsView button[data-view=heatmap]")
                settle(400)
                page.fill("#hmQ", "hemo")
                settle(400)
                bad = phone_fits(page, f"Labs heatmap, filtered ({mode})")
                check(f"…and the heatmap's filter ({mode})", page.is_visible("#hmQ") and not bad, bad)
                js("clearLabFilter()")
                page.click("#labsView button[data-view=chart]")
                settle(300)
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            page.set_viewport_size({"width": 1400, "height": 900})

            # ---- "What is this test?" (#163): beside a lab on Labs (its chart's header and the list), the heatmap (a ? in
            # the grid, clicked; a link in its table), the Overview's tiles and the drawer's results, a link to the lab's
            # plain-language page from lab-test-pages.json, and none where it has no page (the vital signs). Each is the
            # page's own address and nothing else, opens a new tab with no referrer and says so, and following one changes
            # nothing on the page; nothing is fetched from that host (main() fails on a request to another one)
            with open(os.path.join(ROOT, "lab-test-pages.json"), encoding="utf-8") as f:
                tp_urls = {p["url"] for p in json.load(f)["pages"]}
            TP_LINKS = """sel => [...document.querySelectorAll(sel)].map(a => ({href: a.getAttribute('href'), target: a.getAttribute('target'),
              rel: a.getAttribute('rel'), name: a.getAttribute('aria-label'), text: a.textContent}))"""

            def tp_bad(links, where):
                bad = []
                for a in links:
                    if a["href"] not in tp_urls or not a["href"].startswith("https://medlineplus.gov/") or any(c in a["href"] for c in "?#"):
                        bad.append(f"{where}: {a['href']} isn't a page of the table alone")
                    if a["target"] != "_blank" or sorted((a["rel"] or "").split()) != ["noopener", "noreferrer"]:
                        bad.append(f"{where}: {a['href']} opens with {a['target']} {a['rel']}")
                    if not (a["name"] or "").startswith("What is this test? ") or not a["name"].endswith("on MedlinePlus, opens in a new tab"):
                        bad.append(f"{where}: named {a['name']!r}")
                return bad
            open_tab("labs")
            js("clearLabFilter()")
            settle(300)
            rows_tp = js("""[...document.querySelectorAll('#seriesRows .srow')].map(r => [r.querySelector('input').id,
              (r.querySelector('a.test-page') || {}).href || null])""")
            want_tp = js("SERIES.filter(s => !s.watch && s.n >= 2).map(s => ['s_' + s.name.replace(/\\W/g, '_'), (testPage(s.name) || {}).url || null])")
            check("What is this test? (#163): the Labs list has a ? beside each lab with a page, none beside the rest",
                  sorted(rows_tp) == sorted(want_tp) and sum(1 for _, u in rows_tp if u) >= 11
                  and dict(rows_tp).get("s_Hemoglobin") == "https://medlineplus.gov/lab-tests/hemoglobin-test/"
                  and dict(rows_tp).get("s_Blood_pressure__systolic", "x") is None, [rows_tp, want_tp])
            hd_tp = js("[...document.querySelectorAll('#charts .chart .hd')].map(h => [h.querySelector('.name').textContent, (h.querySelector('a.test-page') || {}).textContent || null])")
            check("…each chart's header says “What is this test?” where its lab has a page",
                  hd_tp and all(t == "What is this test? ↗" for _, t in hd_tp if t) and sum(1 for _, t in hd_tp if t) == js(
                      "SERIES.filter(s => !s.watch && state.sel.has(s.name) && testPage(s.name)).length") > 0, hd_tp)
            tp_all = js(TP_LINKS, "#seriesRows a.test-page") + js(TP_LINKS, "#charts a.test-page")
            check("…each the page's own address, nothing of the result or the person, in a new tab with no referrer, named for where it goes",
                  tp_all and not tp_bad(tp_all, "Labs"), tp_bad(tp_all, "Labs")[:4] or tp_all[:2])
            check("…the words say what it's about and where, as “What is this test? <lab>: “<page>” on MedlinePlus, opens in a new tab”",
                  js("document.querySelector('#seriesRows label[for=s_Hemoglobin] a.test-page').getAttribute('aria-label')")
                  == "What is this test? Hemoglobin: “Hemoglobin Test” on MedlinePlus, opens in a new tab")
            # following one (its default action stopped here, so nothing leaves the machine) neither ticks the row's lab nor
            # opens the drawer row's trend
            follow = """sel => { const a = document.querySelector(sel); let went = null; a.addEventListener('click', e => { went = a.href; e.preventDefault(); }, {once: true});
              a.click(); return went; }"""
            was = js("document.getElementById('s_Hemoglobin').checked")
            went = js(follow, "#seriesRows label[for=s_Hemoglobin] a.test-page")
            check("…following the list's ? leaves its lab ticked or not as it was", went and js("document.getElementById('s_Hemoglobin').checked") == was, [went, was])
            open_tab("heatmap")
            settle(400)
            hm_tp = js("[...document.querySelectorAll('#hm g.hm-tq')].map(g => [g.querySelector('circle').dataset.tp, g.querySelector('title').textContent])")
            hm_want = js("SERIES.filter(s => !s.watch && s.n >= 20 && s.pts.some(p => p[2] != null || p[3] != null) && testPage(s.name)).length")
            js("window.__open = []; window.__realOpen = window.open; window.open = (...a) => { window.__open.push(a); return null; }; null")
            js("document.querySelector('#hm g.hm-tq text').dispatchEvent(new MouseEvent('click', {bubbles: true}))")
            opened = js("window.__open")
            js("window.open = window.__realOpen; null")
            check("…the heatmap's grid has a ? beside each such lab's name, opening its page alone in a new tab with no referrer",
                  hm_tp and len(hm_tp) == hm_want and all(u in tp_urls and n.startswith("What is this test? ") for u, n in hm_tp)
                  and opened == [[hm_tp[0][0], "_blank", "noopener,noreferrer"]] and js("state.tab") == "labs", [hm_tp[:2], hm_want, opened])
            js("state.tables.add('#hm'); renderHeatmap()")
            settle(300)
            hmt = js(TP_LINKS, "#hmTable th a.test-page")
            check("…its table has the links themselves, for the keyboard (the grid is one picture)", len(hmt) == hm_want and not tp_bad(hmt, "heatmap table"),
                  [len(hmt), hm_want, tp_bad(hmt, "heatmap table")[:3]])
            js("state.tables.delete('#hm'); renderHeatmap()")
            open_tab("overview")
            settle(400)
            ov_tp = js("""[...document.querySelectorAll('#view-overview .tiles .tile')].filter(t => t.querySelector('.lbl span[title]')).map(t => [t.querySelector('.lbl span[title]').title,
              t.parentElement.classList.contains('tile-tq') ? t.parentElement.querySelector('a.test-page').getAttribute('href') : null])""")
            check("…each Overview lab tile with a page has a ? beside it, outside the tile's own button",
                  ov_tp and all((u is not None) == js("n => !!testPage(n)", n) for n, u in ov_tp) and any(u for _, u in ov_tp)
                  and not js("document.querySelector('#view-overview .tile a, #view-overview button a')")
                  and not tp_bad(js(TP_LINKS, "#view-overview a.test-page"), "Overview"), ov_tp)
            went = js(follow, "#view-overview .tile-tq a.test-page")
            check("…following it stays on the Overview (the tile's own click isn't made)", went and js("state.tab") == "overview", [went, js("state.tab")])
            js("openEvent(EV.filter(e => e.k === 'lab' && DATA.details[e.id]).pop())")
            settle(400)
            dr = js(TP_LINKS, "#dbody table.data a.test-page")
            check("…the drawer's results have a ? beside each component with a page", dr and not tp_bad(dr, "drawer"), tp_bad(dr, "drawer")[:3] or dr)
            went = js(follow, "#dbody table.data a.test-page")
            check("…following it leaves the drawer open where it was (the row's Show trend isn't made)",
                  went and js("document.getElementById('drawer').classList.contains('open')") and js("state.tab") == "overview")
            js("closeDrawer()")
            tp_seen, theme_before = [], js("document.documentElement.dataset.theme || null")
            for mode in ("light", "dark"):
                js("t => { document.documentElement.dataset.theme = t; }", mode)
                for width in (1400, 375):
                    page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                    for view in ("labs", "heatmap", "overview"):
                        open_tab(view)
                        settle(400)
                        if view == "heatmap":
                            js("state.tables.add('#hm'); renderHeatmap()")
                            settle(300)
                        tp_seen += axe_check(page, f"What is this test? on {view}, {width} px, {mode}")[0]
                        if width < 400:
                            tp_seen += phone_fits(page, f"What is this test? on {view} ({mode})")
                        if view == "heatmap":
                            js("state.tables.delete('#hm'); renderHeatmap()")
                    js("openEvent(EV.filter(e => e.k === 'lab' && DATA.details[e.id]).pop())")
                    settle(400)
                    tp_seen += axe_check(page, f"What is this test? in the drawer, {width} px, {mode}")[0]
                    if width < 400:
                        tp_seen += phone_fits(page, f"What is this test? in the drawer ({mode})")
                    js("closeDrawer()")
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            page.set_viewport_size({"width": 1400, "height": 900})
            check("…on Labs, the heatmap's table, the Overview and the drawer at 1400 and 375 px, light and dark: axe-core finds nothing serious and it all fits",
                  not tp_seen, tp_seen[:6])
            open_tab("labs")
            settle(300)

            # ---- Timeline: event sort toggle
            page.click("#tab-timeline")
            settle()
            first_new = js("document.querySelector('#list .tl-row .d').textContent")
            page.click("#sortSeg [data-sort=asc]")
            settle(200)
            first_old = js("document.querySelector('#list .tl-row .d').textContent")
            check("event sort toggles", first_new != first_old, f"{first_new} -> {first_old}")

            nan = js("window.__nan")
            check("no NaN SVG attributes", not nan, nan[:5] if nan else "")

            # ---- profile switcher: the second profile has no records yet, and its own saved view
            check("profile switcher shown", js("!document.getElementById('profile').hidden && document.getElementById('profile').options.length === 2"))
            js("applyPreset('30d')")
            page.select_option("#profile", label="Second person (owner)")
            page.wait_for_function("document.querySelector('main .empty') && PROFILE && PROFILE.name === 'Second person'", timeout=10000)
            check("the period is kept per profile (not carried to another)", js("state.preset") == "2y", js("state.preset"))
            check("empty profile explains itself", "no records yet" in page.inner_text("main"))
            check("empty profile hides the tabs and filters", js("document.querySelector('.tabs').hidden && document.querySelector('.filters').hidden && getComputedStyle(document.querySelector('.tabs')).display === 'none'"))
            check("empty profile's one next step is the getting-started guide, with no redirect there for an account with records elsewhere (#199)",
                  js("[...document.querySelectorAll('main .empty-next')].map(a => a.getAttribute('href'))") == ["guide.html?profile=2"]
                  and js("location.pathname") == "/", js("[...document.querySelectorAll('main .empty-next')].map(a => a.getAttribute('href'))"))
            check("the header links to the import page", js("!document.getElementById('importLink').hidden"))

            # ---- import page: upload a fictional C-CDA in a zip into the empty profile; it's imported in the background
            # (#20) and its records reach the timeline. Then single files, with #20's hook stood in for. Reached through
            # the guide (#199): Get started, past its first step, then Upload an export
            page.click("main .empty a")
            page.wait_for_selector("#step-name:not([hidden])", timeout=10000)
            page.click("#step-name [data-skip]")
            page.click("#step-records a[data-to=upload]")
            page.wait_for_selector("#importer", state="visible", timeout=10000)
            check("…whose Upload an export opens Sources → Upload files for that profile, with a way back to the guide (#199)",
                  js("location.search + location.hash") == "?profile=2&guide=records#upload" and page.is_visible("#guideBar")
                  and js("document.getElementById('guideBack').getAttribute('href')") == "guide.html?profile=2#records", js("location.href"))
            check("import page offers the profiles you can import into, the linked one chosen",
                  js("[...document.querySelectorAll('#profile option')].map(o => o.textContent)") == ["My records (owner)", "Second person (owner)"]
                  and js("document.getElementById('profile').value") == "2")
            import io, zipfile
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(os.path.join(ROOT, "tests", "fixtures", "importers", "ccda", "maple-grove-ccd", "maple-grove-ccd.xml"),
                        "export/summary.xml")
                z.writestr("export/README.txt", "A fictional portal export, for the browser check.\n")
            page.set_input_files("#pickZip", files=[{"name": "portal-export.zip", "mimeType": "application/zip", "buffer": buf.getvalue()}])
            page.wait_for_function("!document.getElementById('go').disabled", timeout=5000)
            check("the chosen zip is listed", "portal-export.zip" in page.inner_text("#list"))
            page.click("#go")
            page.wait_for_function("document.getElementById('msg').textContent.startsWith('Uploaded')", timeout=20000)
            msg = page.inner_text("#msg")
            check("a zip uploads through the page", msg == "Uploaded 1 file (2 once unpacked).", msg)
            check("each file's progress reaches the end", js("[...document.querySelectorAll('#list progress')].every(p => p.value === 1)"))
            from server import db as dbm, uploads
            db = dbm.connect()
            up = db.execute("SELECT id, state FROM uploads WHERE profile_id=2 ORDER BY id DESC").fetchone()
            check("the upload is stored, unpacked and ready", up and up["state"] == "ready" and
                  sorted(uploads.manifest(db, 2, up["id"])) == ["export/README.txt", "export/summary.xml"])
            page.wait_for_selector(".import-job", timeout=5000)   # the job's card, which shows its stage while it runs
            last_card = "[...document.querySelectorAll('.import-job')].pop()"
            page.wait_for_function(f"/^(Ready to import|All duplicates|Not imported)/.test({last_card}.textContent)", timeout=30000)
            card = js(f"{last_card}.innerText")
            check("the import stops at its preview, with what it would add, the dates, and Save and Cancel (#22)",
                  card.startswith("Ready to import.") and "C-CDA" in card and "visit" in card and "lab result" in card
                  and js(f"[...{last_card}.querySelectorAll('.imp-actions button')].map(b => b.textContent)") == ["Save", "Cancel"], card)
            check("…and nothing is saved yet", db.execute("SELECT COUNT(*) FROM imports WHERE profile_id=2").fetchone()[0] == 0)
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the preview fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            js(f"{last_card}.querySelector('.imp-actions .primary').click()")
            page.wait_for_function(f"/^(Imported|Not imported)/.test({last_card}.textContent)", timeout=30000)
            card = js(f"{last_card}.innerText")
            check("the upload is imported in the background, as C-CDA, and the page says what came in",
                  card.startswith("Imported.") and "C-CDA" in card and "visit" in card and "lab result" in card, card)
            job = db.execute("SELECT state, importer FROM jobs WHERE upload_id=?", (up["id"],)).fetchone()
            check("the job is done, read by the C-CDA importer", job is not None and tuple(job) == ("done", "ccda"), job and tuple(job))
            # the same export again: everything is a duplicate; cancelling it leaves nothing behind
            imports_before = db.execute("SELECT COUNT(*) FROM imports WHERE profile_id=2").fetchone()[0]
            page.set_input_files("#pickZip", files=[{"name": "portal-export.zip", "mimeType": "application/zip", "buffer": buf.getvalue()}])
            page.wait_for_function("!document.getElementById('go').disabled", timeout=5000)
            page.click("#go")
            page.wait_for_function(f"document.querySelectorAll('.import-job').length >= 2 && "
                                   f"/^(Ready to import|All duplicates|Not imported)/.test({last_card}.textContent)", timeout=30000)
            card = js(f"{last_card}.innerText")
            check("the same export uploaded again shows as all duplicates", card.startswith("All duplicates.") and "duplicates skipped" in card, card)
            js(f"{last_card}.querySelector('.imp-dups').open = true")
            check("…and the duplicates can be seen, each with a box to keep it anyway",
                  js(f"{last_card}.querySelectorAll('.imp-dups li input[type=checkbox]').length") > 0
                  and "visit" in js(f"{last_card}.querySelector('.imp-dups ul').innerText"))
            again = db.execute("SELECT id FROM uploads WHERE profile_id=2 ORDER BY id DESC").fetchone()["id"]
            js(f"[...{last_card}.querySelectorAll('.imp-actions button')].find(b => b.textContent === 'Cancel').click()")
            page.wait_for_function(f"{last_card}.textContent.startsWith('Cancelled.')", timeout=10000)
            job = db.execute("SELECT id, state FROM jobs WHERE upload_id=?", (again,)).fetchone()
            check("cancelling discards the import, its preview and its upload",
                  job["state"] == "discarded" and db.execute("SELECT state FROM uploads WHERE id=?", (again,)).fetchone()[0] == "discarded"
                  and db.execute("SELECT COUNT(*) FROM imports WHERE profile_id=2").fetchone()[0] == imports_before
                  and not os.path.exists(os.path.join(TMP, "profiles", "2", "previews", str(job["id"]))), tuple(job))
            # ...and a fictional FHIR export (a Bundle with two PDF attachments) into the same profile: Phase 2 is done
            # when both a C-CDA file and a FHIR export import without a terminal
            fhir_dir = os.path.join(ROOT, "tests", "fixtures", "importers", "fhir", "bundle")
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for d, _, names in os.walk(fhir_dir):
                    for n in names:
                        full = os.path.join(d, n)
                        z.write(full, "fhir-export/" + os.path.relpath(full, fhir_dir).replace(os.sep, "/"))
            page.set_input_files("#pickZip", files=[{"name": "fhir-export.zip", "mimeType": "application/zip", "buffer": buf.getvalue()}])
            page.wait_for_function("!document.getElementById('go').disabled", timeout=5000)
            page.click("#go")
            page.wait_for_function(f"document.querySelectorAll('.import-job').length >= 3 && "
                                   f"/^(Ready to import|All duplicates|Not imported)/.test({last_card}.textContent)", timeout=30000)
            js(f"{last_card}.querySelector('.imp-actions .primary').click()")
            page.wait_for_function(f"/^(Imported|Not imported)/.test({last_card}.textContent)", timeout=30000)
            card = js(f"{last_card}.textContent")
            check("a FHIR export uploads and is imported in the background, as FHIR",
                  card.startswith("Imported.") and "FHIR" in card and "document" in card, card)
            up = db.execute("SELECT id FROM uploads WHERE profile_id=2 ORDER BY id DESC").fetchone()
            job = db.execute("SELECT state, importer FROM jobs WHERE upload_id=?", (up["id"],)).fetchone()
            check("the job is done, read by the FHIR importer", job is not None and tuple(job) == ("done", "fhir"), job and tuple(job))
            # ---- the import preview lists the new kinds (#179): a portal's bundle with immunizations, allergies, a fill and an
            # appointment, previewed, then cancelled so the profile stays as it was
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(os.path.join(ROOT, "tests", "fixtures", "importers", "fhir", "portal-everything", "bundle.json"), "portal/bundle.json")
            page.set_input_files("#pickZip", files=[{"name": "portal-everything.zip", "mimeType": "application/zip", "buffer": buf.getvalue()}])
            page.wait_for_function("!document.getElementById('go').disabled", timeout=5000)
            cards = js("document.querySelectorAll('.import-job').length")
            page.click("#go")
            page.wait_for_function(f"document.querySelectorAll('.import-job').length > {cards} && "
                                   f"/^(Ready to import|All duplicates|Not imported)/.test({last_card}.textContent)", timeout=30000)
            items = js(f"[...{last_card}.querySelectorAll('li')].map(li => li.textContent)")
            check("import preview (#179): it lists the immunizations, allergies, pharmacy fill and upcoming appointment it would add",
                  all(x in items for x in ("2 immunizations", "2 allergies", "1 pharmacy fill", "1 upcoming appointment", "1 procedure", "2 imaging studies")), items)
            js(f"[...{last_card}.querySelectorAll('.imp-actions button')].find(b => b.textContent === 'Cancel').click()")
            page.wait_for_function(f"{last_card}.textContent.startsWith('Cancelled.')", timeout=10000)
            # ...and insurer claims (#30): a UHC and a BCBS claims.json in zips, and a generic EOB spreadsheet on its own
            claims_fix = os.path.join(ROOT, "tests", "fixtures", "importers")
            for n, (name, rel) in enumerate([("uhc", "uhc/member-export/claims.json"), ("bcbs", "bcbs/member-export/claims.json"),
                                             ("claims-csv", "claims-csv/eob/claims.csv")]):
                with open(os.path.join(claims_fix, *rel.split("/")), "rb") as f:
                    raw = f.read()
                if name == "claims-csv":
                    page.click("#tab-files")
                    page.set_input_files("#pickFiles", files=[{"name": "eob.csv", "mimeType": "text/csv", "buffer": raw}])
                else:
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                        z.writestr(f"{name}-claims/claims.json", raw)
                    page.set_input_files("#pickZip", files=[{"name": f"{name}-claims.zip", "mimeType": "application/zip", "buffer": buf.getvalue()}])
                page.wait_for_function("!document.getElementById('go').disabled", timeout=5000)
                cards = js("document.querySelectorAll('.import-job').length")
                page.click("#go")
                page.wait_for_function(f"document.querySelectorAll('.import-job').length > {cards} && "
                                       f"/^(Ready to import|All duplicates|Not imported)/.test({last_card}.textContent)", timeout=30000)
                js(f"{last_card}.querySelector('.imp-actions .primary')?.click()")   # Save, at its preview (#22)
                page.wait_for_function(f"/^(Imported|Not imported)/.test({last_card}.textContent)", timeout=30000)
                card = js(f"{last_card}.textContent")
                up = db.execute("SELECT id FROM uploads WHERE profile_id=2 ORDER BY id DESC").fetchone()
                job = db.execute("SELECT state, importer FROM jobs WHERE upload_id=?", (up["id"],)).fetchone()
                check(f"claims upload is imported in the background by the {name} importer",
                      card.startswith("Imported.") and job is not None and tuple(job) == ("done", name), (card, job and tuple(job)))
            page.click("#tab-zip")
            # ...and a fictional CareSpace / OncoEMR portal export (api/*.json, its C-CDA and two PDFs), zipped in a folder
            cs_dir = os.path.join(ROOT, "tests", "fixtures", "importers", "carespace", "full")
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for d, _, names in os.walk(cs_dir):
                    for n in names:
                        full = os.path.join(d, n)
                        z.write(full, "carespace-export/" + os.path.relpath(full, cs_dir).replace(os.sep, "/"))
            page.set_input_files("#pickZip", files=[{"name": "carespace-export.zip", "mimeType": "application/zip", "buffer": buf.getvalue()}])
            page.wait_for_function("!document.getElementById('go').disabled", timeout=5000)
            cards = js("document.querySelectorAll('.import-job').length")
            page.click("#go")
            page.wait_for_function(f"document.querySelectorAll('.import-job').length > {cards} && "
                                   f"/^(Ready to import|All duplicates|Not imported)/.test({last_card}.textContent)", timeout=30000)
            js(f"{last_card}.querySelector('.imp-actions .primary')?.click()")   # Save, at its preview (#22)
            page.wait_for_function(f"/^(Imported|Not imported)/.test({last_card}.textContent)", timeout=30000)
            card = js(f"{last_card}.textContent")
            check("a CareSpace export uploads and is imported in the background, as CareSpace",
                  card.startswith("Imported.") and "CareSpace" in card and "lab result" in card, card)
            up = db.execute("SELECT id FROM uploads WHERE profile_id=2 ORDER BY id DESC").fetchone()
            job = db.execute("SELECT state, importer FROM jobs WHERE upload_id=?", (up["id"],)).fetchone()
            check("the job is done, read by the CareSpace importer", job is not None and tuple(job) == ("done", "carespace"), job and tuple(job))
            # ...and the fictional Epic MyChart export build.py reads (#28): its JSON, documents and C-CDA visit record
            epic_dir = os.path.join(ROOT, "tests", "fixtures", "build", "mychart-20261101")
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for d, _, names in os.walk(epic_dir):
                    for n in names:
                        full = os.path.join(d, n)
                        z.write(full, "mychart-20261101/" + os.path.relpath(full, epic_dir).replace(os.sep, "/"))
            page.set_input_files("#pickZip", files=[{"name": "mychart-export.zip", "mimeType": "application/zip", "buffer": buf.getvalue()}])
            page.wait_for_function("!document.getElementById('go').disabled", timeout=5000)
            cards = js("document.querySelectorAll('.import-job').length")
            page.click("#go")
            page.wait_for_function(f"document.querySelectorAll('.import-job').length > {cards} && "
                                   f"/^(Ready to import|All duplicates|Not imported)/.test({last_card}.textContent)", timeout=30000)
            js(f"{last_card}.querySelector('.imp-actions .primary')?.click()")   # Save, at its preview (#22)
            page.wait_for_function(f"/^(Imported|Not imported)/.test({last_card}.textContent)", timeout=30000)
            card = js(f"{last_card}.textContent")
            check("an Epic MyChart export uploads and is imported in the background, as Epic MyChart",
                  card.startswith("Imported.") and "Epic MyChart" in card and "lab result" in card, card)
            up = db.execute("SELECT id FROM uploads WHERE profile_id=2 ORDER BY id DESC").fetchone()
            job = db.execute("SELECT state, importer FROM jobs WHERE upload_id=?", (up["id"],)).fetchone()
            check("the job is done, read by the Epic MyChart importer", job is not None and tuple(job) == ("done", "epic-mychart"),
                  job and tuple(job))
            # ---- an Apple Health export's newer measures (#223): the preview counts them; cycle records aren't read
            # for a profile without cycle tracking (this one), so it doesn't; cancelled, so nothing is saved
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(os.path.join(ROOT, "tests", "fixtures", "apple_health", "apple_health_export", "export.xml"), "apple_health_export/export.xml")
            page.set_input_files("#pickZip", files=[{"name": "export.zip", "mimeType": "application/zip", "buffer": buf.getvalue()}])
            page.wait_for_function("!document.getElementById('go').disabled", timeout=5000)
            cards = js("document.querySelectorAll('.import-job').length")
            page.click("#go")
            page.wait_for_function(f"document.querySelectorAll('.import-job').length > {cards} && "
                                   f"/^(Ready to import|All duplicates|Not imported)/.test({last_card}.textContent)", timeout=30000)
            card = js(f"{last_card}.innerText")
            check("an uploaded Apple Health export's preview counts its blood pressure, temperature, glucose and heights (#223)",
                  card.startswith("Ready to import.") and all(w in card for w in ("3 blood pressure readings", "2 body temperature readings",
                  "2 glucose meter readings", "1 day of CGM readings", "2 heights, offered for About this person")) and "cycle" not in card.lower(), card)
            js(f"[...{last_card}.querySelectorAll('.imp-actions button')].find(b => b.textContent === 'Cancel').click()")
            page.wait_for_function(f"!/^Ready to import/.test({last_card}.textContent)", timeout=10000)
            js("window.__hook = []; window.sanguityImport = {uploaded: a => window.__hook.push(a)}")
            page.click("#tab-files")
            page.set_input_files("#pickFiles", files=[{"name": n, "mimeType": "text/csv", "buffer": b"date,test,value\n2026-02-01,WBC,6.1\n"}
                                                      for n in ("a.csv", "b.csv")])
            page.click("#go")
            page.wait_for_function("window.__hook.length === 1", timeout=20000)
            hook = js("window.__hook[0]")
            last = db.execute("SELECT id FROM uploads WHERE profile_id=2 ORDER BY id DESC").fetchone()["id"]
            check("after the upload, #20's hook is called with the profile and upload", hook == {"profileId": 2, "uploadId": last}, hook)
            check("…and the page says how many were uploaded", page.inner_text("#msg") == "Uploaded 2 files.", page.inner_text("#msg"))
            db.close()
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("import page fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            check("…its link names the profile and the tab in the address (#208)",
                  js("document.querySelector('.import-job a[href$=\"#timeline\"]')?.getAttribute('href')") == "index.html?profile=2#timeline")
            page.click(".import-job a[href$='#timeline']")   # "See them on the timeline"
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE && PROFILE.name === 'Second person' && "
                                   "TABS.some(t => !document.getElementById('view-' + t).hidden)", timeout=20000)   # the saved view restored
            check("the imported records open on the timeline of the profile they went into", js("state.tab") == "timeline", js("state.tab"))
            check("…where the C-CDA's visits are listed", "Outpatient visit" in page.inner_text("#list"), page.inner_text("#list")[:300])
            listed = page.inner_text("#list")
            check("…and the FHIR export's stay and documents, beside them", "Riverside General Hospital" in listed and "Discharge summary" in listed,
                  listed[:400])
            check("…and the Epic MyChart export's conversation and hospital admission", "Lab question" in listed and "Hospital admission" in listed,
                  listed[:400])
            doc = js("""(async () => { const e = EV.find(e => e.title === 'Discharge summary'); if (e) await loadDetails([e.id]); const d = e && DATA.details[e.id];
                if (!d || !d.url) return null; const r = await fetch(d.url);
                return {status: r.status, type: r.headers.get('content-type'), pdf: (await r.text()).startsWith('%PDF')}; })()""")
            check("…whose attached PDF, stored with the upload, opens", doc == {"status": 200, "type": "application/pdf", "pdf": True}, doc)
            # the imported claims are on the Costs tab: rows from each insurer, with their care categories and denials
            page.click("#tab-costs")
            settle()
            js("applyPreset('all')")
            settle(200)
            rows = js("[...document.querySelectorAll('#costTable tbody tr')].map(tr => [...tr.cells].map(td => td.textContent))")
            day = {js("d => fmtD(dayTs(d))", d): d for d in ("2026-10-07", "2026-06-24", "2026-02-03")}   # in their date format (#248)
            by = {(day.get(r[0], r[0]), r[2]): r for r in rows}
            check("Costs tab lists every imported claim (6 UHC, 5 BCBS, 5 from the spreadsheet)", len(rows) == 16, len(rows))
            check("…a UHC claim with its category and your share",
                  by.get(("2026-10-07", "Riverbend Infusion Clinic"), [None] * 8)[3] == "Chemo, infusions & drugs", by.get(("2026-10-07", "Riverbend Infusion Clinic")))
            check("…a denied BCBS claim, marked denied",
                  (by.get(("2026-06-24", "Harbor Physical Therapy")) or [""] * 8)[7] == "denied", by.get(("2026-06-24", "Harbor Physical Therapy")))
            check("…a claim from the spreadsheet, its two service lines added up",
                  (by.get(("2026-02-03", "Valley Reference Laboratory")) or [""] * 8)[3:5] == ["Lab & pathology", "$200"],
                  by.get(("2026-02-03", "Valley Reference Laboratory")))
            check("…and the insurers named", {"UnitedHealthcare", "Blue Cross Blue Shield", "Insurance"} <= set(js("DATA.costs.map(c => c.ins)")))
            page.select_option("#profile", label="My records (owner)")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records' && "
                                   "TABS.some(t => !document.getElementById('view-' + t).hidden)", timeout=20000)   # its saved view restored
            check("switching back restores the profile's tab", js("state.tab") == "timeline", js("state.tab"))
            check("switching back restores the profile's period", js("state.preset") == "30d", js("state.preset"))

            # ---- import history (#24): the C-CDA and FHIR imports are listed; undoing the FHIR one takes its records
            # off the timeline and leaves the C-CDA's. The setup build's import can't be undone, and says why.
            page.goto(base + "/sources.html?profile=1#history")
            page.wait_for_selector("#history li[data-import]", timeout=10000)
            check("the setup build's import is listed, with why it can't be undone and no Undo",
                  js("(() => { const li = document.querySelector('#history li[data-import]'); return !!li.querySelector('.note') && "
                     "li.querySelector('.note').textContent.includes('rebuild') && !li.querySelector('button.undo'); })()"))
            page.select_option("#profile", label="Second person (owner)")
            page.wait_for_selector("#history li[data-format='fhir']", timeout=10000)
            hist = js("[...document.querySelectorAll('#history li[data-import]')].map(li => li.dataset.format)")
            check("the import history lists the C-CDA, FHIR, claims, CareSpace and Epic imports, newest first",
                  hist == ["epic-mychart", "carespace", "claims-csv", "bcbs", "uhc", "fhir", "ccda"], hist)
            check("…with the uploaded files", "fhir-export/" in page.inner_text("#history li[data-format='fhir'] .files"))
            page.click("#history li[data-format='fhir'] button.undo")
            page.click("#history li[data-format='fhir'] .confirm button.danger")
            page.wait_for_selector("#history li.undone[data-format='fhir']", timeout=10000)
            check("…undoing the FHIR one marks it undone, the C-CDA one still there",
                  js("!document.querySelector(\"#history li[data-format='ccda']\").classList.contains('undone')") and
                  page.inner_text("#historyMsg").startswith("Undone"), page.inner_text("#historyMsg"))
            js("localStorage.setItem('ht-profile', '2')")
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE && PROFILE.name === 'Second person' && "
                                   "TABS.some(t => !document.getElementById('view-' + t).hidden)", timeout=20000)
            js("switchTab('timeline')")   # the claims check left the profile on Costs
            settle(300)
            listed = page.inner_text("#list")
            check("…and the FHIR export's records leave the timeline, the C-CDA's staying",
                  "Riverside General Hospital" not in listed and "Discharge summary" not in listed and "Outpatient visit" in listed, listed[:400])

            # ---- import page, "Type them in" (#27): two results typed in, checked as they're typed, saved, on Trends
            page.goto(base + "/import.html?profile=2#manual")   # its old address: Sources → Type records in (#166)
            page.wait_for_url("**/sources.html?profile=2#manual", timeout=10000)
            page.wait_for_selector("#typeIn", state="visible", timeout=10000)
            check("Type records in shows its rows, not the upload controls",
                  js("!document.getElementById('panel-manual').hidden && document.querySelectorAll('.man-row').length === 1 && "
                     "getComputedStyle(document.getElementById('importer')).display === 'none'"))
            page.wait_for_function("document.querySelectorAll('#labNames option').length > 10", timeout=5000)
            check("test names are suggested from lab-aliases.json", js("[...document.querySelectorAll('#labNames option')].some(o => o.value === 'Hemoglobin')")
                  and js("document.querySelector('.man-row [data-f=test]').getAttribute('list')") == "labNames")
            row = lambda i, f: f".man-row:nth-child({i}) [data-f={f}]"
            page.fill(row(1, "date"), "2026-11-02")
            page.fill(row(1, "time"), "08:10")
            page.fill(row(1, "test"), "HGB")
            page.fill(row(1, "value"), "12.4")
            page.fill(row(1, "unit"), "g/dL")
            page.fill(row(1, "range"), "13.5 - 17.5")
            page.wait_for_function("document.querySelector('.man-row .man-check').textContent.includes('Hemoglobin')", timeout=5000)
            first = js("document.querySelector('.man-row .man-check').textContent")
            check("a typed row is checked as you go: its canonical name, range and flag", "Hemoglobin" in first and "13.5 – 17.5" in first
                  and "Low" in first, first)
            typed_at = js("recordDate('2026-11-02 08:10')")
            check("…and its date and time in the person's format, not as typed (#311)", f" · {typed_at} · " in first and "2026-11-02" not in typed_at, first)
            page.click("#manAdd")
            check("a new row takes the last one's date and time", js(f"document.querySelector('{row(2, 'date')}').value") == "2026-11-02"
                  and js(f"document.querySelector('{row(2, 'time')}').value") == "08:10")
            page.fill(row(2, "test"), "Ferritin")
            page.fill(row(2, "value"), "abc")
            page.wait_for_function("document.querySelectorAll('.man-check')[1].textContent.includes('text result')", timeout=5000)
            check("a value that isn't a number is pointed out", True)
            page.fill(row(2, "value"), "88")
            page.fill(row(2, "unit"), "ng/mL")
            page.fill(row(2, "range"), "30-400")
            page.wait_for_function("document.getElementById('manMsg').textContent === '2 results ready to save.'", timeout=5000)
            # a block of cells pasted from a spreadsheet fills rows of its own; removing them leaves the two typed
            js("""(() => { const dt = new DataTransfer(); dt.setData('text/plain', 'Date\\tTest\\tResult\\tUnits\\n2026-11-02\\tWBC\\t6.1\\tK/uL\\n'
                   + '2026-11-02\\tPlatelets\\t250\\tK/uL\\n'); document.getElementById('manAdd').click();
                   document.querySelector('.man-row:nth-child(3) [data-f=date]').dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true})); })()""")
            check("rows pasted from a spreadsheet fill in by their header", js("[...document.querySelectorAll('.man-row [data-f=test]')].map(i => i.value)")
                  == ["HGB", "Ferritin", "WBC", "Platelets"], js("[...document.querySelectorAll('.man-row [data-f=test]')].map(i => i.value)"))
            page.wait_for_function("document.getElementById('manMsg').textContent === '4 results ready to save.'", timeout=5000)
            page.click(".man-row:nth-child(4) .del")
            page.click(".man-row:nth-child(3) .del")
            page.wait_for_function("document.getElementById('manMsg').textContent === '2 results ready to save.' && !document.getElementById('manSave').disabled", timeout=5000)
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the Type them in tab fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.click("#manSave")
            page.wait_for_function("document.getElementById('manMsg').textContent.startsWith('Saved')", timeout=10000)
            check("the typed results save", page.inner_text("#manMsg").startswith("Saved 2 results."), page.inner_text("#manMsg"))
            db = dbm.connect()
            imp = db.execute("SELECT origin, format FROM imports WHERE profile_id=2 ORDER BY id DESC").fetchone()
            db.close()
            check("…as one import of their own", imp is not None and tuple(imp) == ("manual", "manual"), imp and tuple(imp))
            page.click("#manMsg a")   # "See them on Trends"
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE && PROFILE.name === 'Second person' && "
                                   "!document.getElementById('view-labs').hidden && document.querySelectorAll('#charts .chart').length > 0", timeout=20000)
            typed = js("""SERIES.filter(s => ['Hemoglobin', 'Ferritin'].includes(s.name))
                          .map(s => [s.name, s.pts.filter(p => p[6] === 'Typed in').map(p => [p[1], p[4]])]).filter(x => x[1].length)""")
            check("the typed results are on Trends, charted under their canonical names",
                  sorted(map(tuple, [(n, tuple(map(tuple, p))) for n, p in typed])) == [("Ferritin", ((88, ""),)), ("Hemoglobin", ((12.4, "L"),))], typed)
            charts = page.inner_text("#charts")
            check("…with a chart for each", "Hemoglobin" in charts and "Ferritin" in charts, charts[:300])

            # ---- the other kinds typed in: a visit, a medication, a claim and a letter with a PDF, saved as one import;
            # the visit on the timeline, the claim on Costs, the letter opening in the drawer
            page.goto(base + "/sources.html?profile=2")
            page.wait_for_selector("#srcOverview", state="visible", timeout=10000)
            page.click("#srcNav a[data-group=manual]")
            page.wait_for_selector("#typeIn", state="visible", timeout=10000)
            card = lambda i, f: f".man-item:nth-child({i}) [data-f={f}]"

            def add_record(kind):
                page.select_option("#manKind", kind)
                page.click("#manAddKind")
            # #245: a form of each kind, with a hint under its title or without, says no "null" or "undefined"; then removed
            kinds = js("[...document.querySelectorAll('#manKind option')].map(o => o.value).filter(k => k !== 'labs')")
            for kind in kinds:
                add_record(kind)
            said_null = js("[...document.querySelectorAll('.man-item')].filter(c => /\\b(null|undefined)\\b/.test(c.innerText)).map(c => c.dataset.kind)")
            check("Type records in (#245): no record's form, of any kind, says “null” or “undefined”",
                  not said_null and js("document.querySelectorAll('.man-item').length") == len(kinds) > 5, said_null)
            while js("document.querySelectorAll('.man-item').length"):
                page.click(".man-item .del")
            add_record("visit")
            check("a record's form opens with its date filled in and the next field focused",
                  js("document.querySelectorAll('.man-item').length === 1 && /^\\d{4}-\\d\\d-\\d\\d$/.test(document.querySelector('.man-item [data-f=date]').value)")
                  and js("document.activeElement.dataset.f") == "time", js("document.activeElement.dataset.f"))
            page.fill(card(1, "date"), "2026-11-03")
            page.fill(card(1, "date"), "2026-13-03")
            page.fill(card(1, "title"), "Typed follow-up visit")
            page.wait_for_function("document.querySelector('.man-item [data-f=date]').getAttribute('aria-invalid') === 'true'", timeout=5000)
            check("a field that can't be read is marked, with why", "couldn't be read" in js("document.querySelector('.man-item .man-err').textContent")
                  and page.is_disabled("#manSave"), js("document.querySelector('.man-item .man-err').textContent"))
            page.fill(card(1, "date"), "2026-11-03")
            page.fill(card(1, "who"), "Dr. Ada Quill (fictional)")
            page.fill(card(1, "where"), "Lakeside Clinic (fictional)")
            add_record("med")
            page.fill(card(2, "name"), "Typed ferrous sulfate 325 mg")
            page.fill(card(2, "instructions"), "1 tablet daily")
            add_record("claim")
            page.fill(card(3, "provider"), "Lakeside Clinic (fictional)")
            page.fill(card(3, "title"), "Typed office visit claim")
            page.fill(card(3, "billed"), "$250.00")
            page.fill(card(3, "paid"), "200")
            page.fill(card(3, "owe"), "50")
            add_record("document")
            page.fill(card(4, "title"), "Typed referral letter")
            page.select_option(card(4, "doctype"), "letter")
            pdf_path = os.path.join(TMP, "typed-letter-2026-11-02.pdf")   # a date in its name, kept as typed (#317)
            with open(pdf_path, "wb") as f:   # a one-page PDF with fictional text
                body = b"BT /F1 12 Tf 72 720 Td (Fictional referral letter) Tj ET"
                objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
                        b"<< /Length %d >>\nstream\n" % len(body) + body + b"\nendstream", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
                out, offs = b"%PDF-1.4\n", []
                for i, o in enumerate(objs):
                    offs.append(len(out))
                    out += b"%d 0 obj\n" % (i + 1) + o + b"\nendobj\n"
                xref = len(out)
                out += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(b"%010d 00000 n \n" % o for o in offs)
                out += b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % xref
                f.write(out)
            page.set_input_files(card(4, "file"), pdf_path)
            page.wait_for_function("document.querySelector('.man-item:nth-child(4) .man-file').textContent.includes('uploaded')", timeout=10000)
            page.wait_for_function("document.getElementById('manMsg').textContent === '4 records ready to save.' && !document.getElementById('manSave').disabled", timeout=5000)
            checks = js("[...document.querySelectorAll('.man-item .man-check')].map(c => c.textContent)")
            check("each record says what it became", "Typed follow-up visit" in checks[0] and "Doctor visits" in checks[2]
                  and "typed-letter-2026-11-02.pdf" in checks[3], checks)
            letter_on = js(f"recordDate(document.querySelector('{card(4, 'date')}').value)")
            check("…its own date in the person's format, and a date in the file's name as typed (#317)",
                  checks[3].endswith(" · Typed referral letter · typed-letter-2026-11-02.pdf") and f"Document · {letter_on}" in checks[3], [checks[3], letter_on])
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the record forms fit 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.click("#manSave")
            page.wait_for_function("document.getElementById('manMsg').textContent.startsWith('Saved')", timeout=10000)
            check("the typed records save as one import", page.inner_text("#manMsg").startswith("Saved 4 records."), page.inner_text("#manMsg"))
            page.click("#manMsg a")   # "See them on the timeline"
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE && PROFILE.name === 'Second person' && "
                                   "!document.getElementById('view-timeline').hidden", timeout=20000)
            typed = js("['Typed follow-up visit', 'Typed ferrous sulfate 325 mg', 'Typed referral letter'].map(t => (EV.find(e => e.title === t) || {}).k)")
            check("the visit, the medication and the letter are on the timeline", typed == ["visit", "med", "letter"], typed)
            js("openEvent(EV.find(e => e.title === 'Typed referral letter'))")
            page.wait_for_selector("#drawer.open #dbody iframe.doc", timeout=5000)
            src = js("document.querySelector('#dbody iframe.doc').getAttribute('src')")
            got = js(f"fetch({json.dumps(src.split('#')[0])}).then(async r => [r.status, r.headers.get('content-type'), (await r.text()).slice(0, 8)])")
            check("…and the letter's PDF opens in the drawer", "/documents/" in src and got == [200, "application/pdf", "%PDF-1.4"], [src, got])
            js("closeDrawer(); switchTab('costs')")
            page.wait_for_function("!document.getElementById('view-costs').hidden", timeout=5000)
            js("applyPreset('all')")
            settle(300)
            costs = page.inner_text("#view-costs")
            check("the typed claim is on Costs, under its care category", "Typed office visit claim" in costs and "Doctor visits" in costs, costs[:300])

            # ---- out-of-pocket costs without a finance export (#55): the typed claim's share is counted until a
            # payment typed in with its receipt and an HSA tag matches it; the receipt opens; the tag changed to FSA
            # on the Costs tab; HSA/FSA totals; the list fits 375 px
            share = js("oopRows().filter(r => r.type === 'claim' && r.claim.title === 'Typed office visit claim').map(r => [r.counted, srcText(r)])")
            check("the typed claim's share is counted out of pocket, saying it came from the claim",
                  len(share) == 1 and share[0][0] == 50 and share[0][1].startswith("Claim: your share"), share)
            page.goto(base + "/import.html?profile=2#payment")
            page.wait_for_selector(".man-item[data-kind=payment]", timeout=10000)
            check("the Costs tab's link opens a payment's form", js("document.querySelectorAll('.man-item').length") == 1)
            page.fill(card(1, "date"), "2026-11-05")
            page.fill(card(1, "payee"), "Lakeside Clinic (fictional)")
            page.fill(card(1, "amount"), "$50.00")
            page.select_option(card(1, "hsa"), "HSA")
            page.set_input_files(card(1, "file"), pdf_path)
            page.wait_for_function("document.querySelector('.man-item .man-file').textContent.includes('uploaded')", timeout=10000)
            page.wait_for_function("document.getElementById('manMsg').textContent === '1 record ready to save.' && !document.getElementById('manSave').disabled", timeout=5000)
            check("…which says it's paid with an HSA and has a receipt", "HSA" in js("document.querySelector('.man-check').textContent")
                  and "receipt" in js("document.querySelector('.man-check').textContent"), js("document.querySelector('.man-check').textContent"))
            page.click("#manSave")
            page.wait_for_function("document.getElementById('manMsg').textContent.startsWith('Saved')", timeout=10000)
            page.click("#manMsg a")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE && PROFILE.name === 'Second person' && DATA.oop", timeout=20000)
            js("switchTab('costs')")
            page.wait_for_function("!document.getElementById('view-costs').hidden", timeout=5000)
            js("paidState.q = ''; applyPreset('all')")
            settle(300)
            pay = js("oopRows().filter(r => r.type === 'pay' && r.who === 'Lakeside Clinic (fictional)' && r.amount === 50)"
                     ".map(r => [r.src, r.hsa, r.claim && r.claim.title, r.receipt != null, r.match])")
            check("the typed payment is listed as typed in, HSA, with its receipt, and matched to the claim",
                  pay == [["typed", "HSA", "Typed office visit claim", True, "auto"]], pay)
            check("…so the claim's share isn't counted again",
                  js("oopRows().some(r => r.type === 'claim' && r.claim.title === 'Typed office visit claim')") is False)
            check("the HSA total counts it, and HSA and FSA are listed by year",
                  "$50" in page.inner_text("#oopHsa") and "2026" in page.inner_text("#oopTax"), [page.inner_text("#oopHsa"), js("!!document.getElementById('oopTax')")])
            row = js("oopKey(oopRows().find(r => r.type === 'pay' && r.who === 'Lakeside Clinic (fictional)' && r.amount === 50))")
            page.click(f"#paidTable tr[data-oop={row}]")
            page.wait_for_selector("#oopEdit", timeout=5000)
            check("a payment says where its number came from", "typed in" in page.inner_text("#oopEdit") and "matched by amount" in page.inner_text("#oopEdit"),
                  page.inner_text("#oopEdit")[:300])
            page.click("#oopEdit >> text=Open receipt")
            page.wait_for_selector("#drawer.open #dbody iframe.doc", timeout=5000)
            check("…and its receipt opens in the drawer as a document", "/documents/" in js("document.querySelector('#dbody iframe.doc').getAttribute('src')"))
            js("closeDrawer()")
            page.select_option("#oopHsaSel", "FSA")
            page.click("#oopEdit >> text=Save")
            page.wait_for_function("document.querySelector('#oopEdit .oop-msg') && document.querySelector('#oopEdit .oop-msg').textContent === 'Saved.'", timeout=10000)
            check("tagged FSA on the Costs tab, it moves to the FSA total", "$50" in page.inner_text("#oopFsa") and "$0" in page.inner_text("#oopHsa"),
                  [page.inner_text("#oopFsa"), page.inner_text("#oopHsa")])
            page.set_viewport_size({"width": 375, "height": 800})
            settle(300)
            check("the out-of-pocket list and its editor fit 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            only = js("""(() => { const had = DATA.costs, o = DATA.oop; DATA.costs = []; DATA.oop = {payments: o.payments.map(p => ({...p, claim: null})), claims: []};
                           renderCosts(); const out = [!!document.getElementById('paidCard'), !!document.getElementById('costTable'),
                           document.getElementById('oopTotal').textContent]; DATA.costs = had; DATA.oop = o; return out; })()""")
            check("with no claims at all, Costs shows the payments alone", only[0] and not only[1] and "Total out of pocket" in only[2], only)
            js("paidState.open = null; renderCosts()")

            # ---- readings typed in at home (#224): blood pressure, glucose, temperature and blood oxygen typed on Sources
            # for the demo person, whose phone sends the same measures; checked as typed (plausible values, the unit),
            # one the phone sent already found; saved, each on its card beside the phone's and the visits', "Typed in";
            # typed again for its minute it's corrected; undone from the import history, gone. A height into About this
            # person's history. 1400 and 375 px, light and dark, axe-core
            def typed_form(pid):
                page.goto(base + f"/sources.html?profile={pid}")
                page.wait_for_selector("#srcOverview", state="visible", timeout=10000)
                page.click("#srcNav a[data-group=manual]")
                page.wait_for_selector("#typeIn", state="visible", timeout=10000)

            def add_record(kind):
                page.select_option("#manKind", kind)
                page.click("#manAddKind")
            typed_form(1)
            kinds = js("[...document.querySelectorAll('#manKind option')].map(o => o.textContent)")
            check("typed in at home (#224): a form each for blood pressure, glucose, temperature, blood oxygen and height",
                  all(k in kinds for k in ("Blood pressure at home", "Blood glucose at home", "Temperature at home", "Blood oxygen at home",
                                           "Weight or steps at home", "Height")), kinds)
            add_record("home_bp")
            page.fill(card(1, "date"), "2026-11-10")
            page.fill(card(1, "time"), "05:55")
            page.fill(card(1, "sys"), "300")
            page.fill(card(1, "dia"), "81")
            add_record("home_glucose")
            page.fill(card(2, "date"), "2026-11-10")
            page.fill(card(2, "time"), "06:10")
            page.fill(card(2, "glucose"), "104")
            page.select_option(card(2, "glucose_unit"), "mmol/L")
            add_record("home_temp")
            page.fill(card(3, "date"), "2026-11-10")
            page.fill(card(3, "time"), "06:20")
            page.fill(card(3, "temp"), "37.4")
            add_record("home_spo2")
            page.fill(card(4, "date"), "2026-11-10")
            page.fill(card(4, "time"), "06:25")
            page.fill(card(4, "spo2"), "97")
            page.wait_for_function("document.querySelectorAll('.man-item [aria-invalid=true]').length === 3", timeout=5000)
            errs224 = js("[...document.querySelectorAll('.man-item .man-err')].map(e => e.textContent).filter(Boolean)")
            check("…a value that doesn't look right is refused, saying what was expected, and a unit chosen wrong is named",
                  any("expected 50 to 260" in e for e in errs224) and any("looks like mg/dL, not mmol/L" in e for e in errs224)
                  and any("looks like °C, not °F" in e for e in errs224) and page.is_disabled("#manSave"), errs224)
            page.fill(card(1, "sys"), "126")
            page.fill(card(1, "pulse"), "66")
            page.select_option(card(2, "glucose_unit"), "mg/dL")
            page.select_option(card(2, "meal"), "before")
            page.select_option(card(3, "temp_unit"), "C")
            page.wait_for_function("document.getElementById('manMsg').textContent === '4 records ready to save.'", timeout=5000)
            said = js("[...document.querySelectorAll('.man-item .man-check')].map(c => c.textContent)")
            at = lambda t: js(f"recordDate('2026-11-10 {t}')")   # noqa: E731 (in the person's date format, #311)
            check("…fixed, each says what it becomes", said == [f"✓ Blood pressure at home · {at('05:55')} · 126/81 mmHg · pulse 66",
                  f"✓ Blood glucose at home · {at('06:10')} · glucose 104 mg/dL · before a meal", f"✓ Temperature at home · {at('06:20')} · 37.4 °C",
                  f"✓ Blood oxygen at home · {at('06:25')} · SpO2 97%"] and "2026-11-10" not in at("05:55"), said)
            wall = js("""fetch('api/profiles/1/watch').then(r => r.json()).then(w => { const r = w.bp_readings.filter(x => x[5] !== 'Typed in').at(-1);
                         const p = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {timeZone: w.tz, year: 'numeric', month: '2-digit', day: '2-digit',
                           hour: '2-digit', minute: '2-digit', hourCycle: 'h23'}).formatToParts(new Date(r[0])).map(x => [x.type, x.value]));
                         return [`${p.year}-${p.month}-${p.day}`, `${p.hour}:${p.minute}`, String(r[1]), String(r[2])]; })""")
            add_record("home_bp")
            for f, v in zip(("date", "time", "sys", "dia"), wall):
                page.fill(card(5, f), v)
            page.wait_for_function("document.querySelectorAll('.man-item .dup').length === 1", timeout=5000)
            check("…one the phone sent already is found, and left out", "already in this profile" in js("document.querySelector('.man-item:nth-child(5) .man-check').textContent"),
                  [wall, js("document.querySelector('.man-item:nth-child(5) .man-check').textContent")])
            page.click(".man-item:nth-child(5) .del")
            page.wait_for_function("document.getElementById('manMsg').textContent === '4 records ready to save.'", timeout=5000)
            bad, axe224 = [], []
            for width in (1400, 375):
                page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                for theme in ("light", "dark"):
                    js("t => { document.documentElement.dataset.theme = t; }", theme)
                    settle(200)
                    if width == 375:
                        bad += phone_fits(page, f"home reading forms ({theme})")
                    axe224 += axe_check(page, f"home reading forms, {width} px, {theme}")[0]
            js("delete document.documentElement.dataset.theme")
            page.set_viewport_size({"width": 1400, "height": 900})
            check("…the forms fit 375 px, and axe-core finds nothing serious at 1400 and 375 px, light and dark", not bad and not axe224, (bad + axe224)[:6])
            page.click("#manSave")
            page.wait_for_function("document.getElementById('manMsg').textContent.startsWith('Saved')", timeout=10000)
            check("…saved as one import, with a link to Body and activity", page.inner_text("#manMsg").startswith("Saved 4 records.")
                  and "See them on Body and activity" in page.inner_text("#manMsg"), page.inner_text("#manMsg"))
            page.click("#manMsg a")
            page.wait_for_function("typeof booted !== 'undefined' && booted && PROFILE && PROFILE.id === 1 && state.tab === 'health'", timeout=20000)
            js("window._was224 = [state.preset, state.from, state.to]; state.tables.clear(); setRange(dayTs('2026-10-20'), dayTs('2026-11-14')); switchTab('health')")
            settle(500)
            got = js("""(() => { const one = (rs, f) => rs.filter(r => r.typed).map(f);
                return {bp: one(H.vit.bp.home, r => [r.v, r.pulse, r.src]), phone: H.vit.bp.home.filter(r => !r.typed).length, clinic: H.vit.bp.clinic.length,
                  temp: one(H.vit.temp.home, r => [+r.v[0].toFixed(1), r.src]), tempUnit: H.vit.temp.unit, o2: H.vit.spo2.typed.map(r => [r.v, r.src]), o2days: H.vit.spo2.home.length,
                  glu: G.meter.filter(m => m[2]).map(m => [m[1], m[2], m[3]]), gluPhone: G.meter.filter(m => !m[2]).length,
                  o2legend: document.querySelector('#h-oxygen .legend').textContent, gluLegend: document.querySelector('#h-glucose .legend').textContent}; })()""")
            check("…each on its card beside the phone's readings, told apart as typed in: blood pressure with its pulse, the temperature in the records' unit",
                  got["bp"] == [[[126, 81], 66, "Typed in"]] and got["phone"] > 20 and got["temp"] == [[99.3, "Typed in"]] and got["tempUnit"] == "°F"
                  and got["o2"] == [[[97], "Typed in"]] and got["o2days"] > 20 and "At home, one reading" in got["o2legend"], got)
            check("…glucose with the meter's readings, its meal note said", got["glu"] == [[104, "Typed in", "before a meal"]] and got["gluPhone"] > 0
                  and "from the phone or typed in" in got["gluLegend"], got["glu"])
            js("setRange(dayTs('2026-11-09'), zParse('2026-11-10T06:30'))")   # up to just after them: the latest listed
            settle(300)
            lists = js("['#h-bp', '#h-temp', '#h-oxygen'].map(k => [...document.querySelectorAll(k + ' .vital-list li')].map(l => l.textContent).find(t => t.includes('Typed in')) || '')")
            check("…listed with time and source among the latest readings", all("at home · Typed in" in l for l in lists), lists)
            js("setRange(dayTs('2026-10-20'), dayTs('2026-11-14'))")
            settle(300)
            page.focus("#h-temp svg")
            page.keyboard.press("End")   # the latest temperature: the one typed in
            settle(150)
            check("…the keyboard reaches a typed reading, the tooltip saying where it came from", "at home · Typed in" in js("tip.innerText"), js("tip.innerText"))
            page.keyboard.press("Escape")
            for k in ("#h-bp", "#h-temp", "#h-oxygen", "#h-glucose"):
                page.click(f"{k} .view-seg button:text-is('Table')")
                settle(200)
            tb = js("""['#h-bp', '#h-temp', '#h-oxygen', '#h-glucose'].map(k => [...document.querySelectorAll(k + ' table.data tbody tr')].map(r => r.textContent).filter(t => /typed in/i.test(t)))""")
            check("…and in each card's Table", [len(x) for x in tb] == [1, 1, 1, 1] and "Home, one reading" in tb[2][0] and "(typed in, before a meal)" in tb[3][0], tb)
            bad, axe224 = [], []
            for width in (1400, 375):
                page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                for theme in ("light", "dark"):
                    js("t => { document.documentElement.dataset.theme = t; }", theme)
                    for table in (False, True):
                        js("on => { state.tables.clear(); if (on) for (const k of ['#h-bp', '#h-temp', '#h-oxygen', '#h-glucose']) state.tables.add(k); renderHealth(); }", table)
                        settle(300)
                        page.locator("#h-bp").scroll_into_view_if_needed()
                        if width == 375:
                            bad += phone_fits(page, f"typed readings' cards, {'tables' if table else 'charts'} ({theme})")
                        axe224 += axe_check(page, f"typed readings' cards, {'tables' if table else 'charts'}, {width} px, {theme}")[0]
            js("delete document.documentElement.dataset.theme; state.tables.clear(); renderHealth()")
            page.set_viewport_size({"width": 1400, "height": 900})
            check("…the cards with them fit 375 px, and axe-core finds nothing serious at 1400 and 375 px, light and dark", not bad and not axe224, (bad + axe224)[:6])
            # corrected: typed again for the same date and time, the later one is shown, once
            typed_form(1)
            add_record("home_bp")
            for f, v in (("date", "2026-11-10"), ("time", "05:55"), ("sys", "124"), ("dia", "80")):
                page.fill(card(1, f), v)
            page.wait_for_function("document.querySelector('.man-item .man-check').textContent.startsWith('✓')", timeout=5000)
            check("…typed again for its date and time, a reading says it replaces the one before",
                  "replaces the reading typed in before for that time" in js("document.querySelector('.man-item .man-check').textContent"),
                  js("document.querySelector('.man-item .man-check').textContent"))
            page.click("#manSave")
            page.wait_for_function("document.getElementById('manMsg').textContent.startsWith('Saved')", timeout=10000)
            fixed = js("fetch('api/profiles/1/watch').then(r => r.json()).then(w => w.bp_readings.filter(r => r[5] === 'Typed in'))")
            check("…and once saved it is the one shown", fixed == [["2026-11-10T05:55:00", 124, 80, None, None, "Typed in"]], fixed)
            # undone from the import history, newest first: the typed readings go, the phone's stay
            page.goto(base + "/sources.html?profile=1#history")
            page.reload()   # the same page as the form: only its hash changed, so the list is loaded afresh
            page.wait_for_function("document.querySelectorAll(\"#historyList li[data-format='manual']\").length === 2", timeout=10000)
            what = js("[...document.querySelectorAll(\"#historyList li[data-format='manual'] .what\")].map(p => p.textContent).slice(0, 2)")
            check("…the import history lists each save with its readings taken at home",
                  len(what) == 2 and what[0].startswith("1 reading taken at home") and what[1].startswith("4 readings taken at home"), what)
            for _ in range(2):
                page.click("#history li[data-format='manual']:not(.undone) button.undo")
                page.click("#history li[data-format='manual']:not(.undone) .confirm button.danger")
                page.wait_for_function("document.getElementById('historyMsg').textContent.startsWith('Undone')", timeout=10000)
                js("document.getElementById('historyMsg').textContent = ''")
            left = js("fetch('api/profiles/1/watch').then(r => r.json()).then(w => [['bp_readings', 'temp_readings', 'spo2_readings', 'glucose_readings']"
                      ".map(k => (w[k] || []).filter(r => r.includes('Typed in')).length), w.bp_readings.length > 20])")
            check("…undone, the typed readings are gone and the phone's stay", left == [[0, 0, 0, 0], True], left)
            # a height typed in goes into About this person's history, which is where it's changed or removed
            typed_form(2)
            add_record("height")
            page.fill(card(1, "date"), "2026-11-02")
            page.select_option(card(1, "height_unit"), "in")
            page.fill(card(1, "height"), "5 ft 7 in")
            page.wait_for_function("document.getElementById('manMsg').textContent === '1 record ready to save.'", timeout=5000)
            check("…a height says it goes into About this person's history", "height 5 ft 7 in · into About this person's height history"
                  in js("document.querySelector('.man-item .man-check').textContent"), js("document.querySelector('.man-item .man-check').textContent"))
            page.click("#manSave")
            page.wait_for_function("document.getElementById('manMsg').textContent.startsWith('Saved')", timeout=10000)
            page.click("#manMsg a:text-is('See the height history')")
            page.wait_for_selector("#about .about-heights", timeout=10000)
            check("…and is there, as typed", "5 ft 7 in" in page.inner_text("#about .about-heights"), page.inner_text("#about .about-heights")[:200])
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            cl_tables, cl_bad = cell_labels(page, "Sharing → About this person")
            check("…at 375 px with its date and height under their columns' names (#320)",
                  not cl_bad and any("about-heights" in t[0] and t[1] and t[3] >= 2 for t in cl_tables), cl_bad[:6] or cl_tables)
            page.set_viewport_size({"width": 1400, "height": 900})
            js("fetch('api/profiles/2/person/heights/2026-11-02', {method: 'DELETE', headers: {'X-Sanguity': '1'}})")
            js("localStorage.setItem('ht-profile', '1')")
            page.goto(base + "/")
            page.wait_for_function("typeof booted !== 'undefined' && booted && PROFILE && PROFILE.id === 1", timeout=20000)
            js("[state.preset, state.from, state.to] = window._was224 || [state.preset, state.from, state.to]; save()")

            # ---- lab names matched on import (#23): a spelling lab-aliases.json doesn't know is suggested the profile's
            # Hemoglobin series at the preview; accepted, its point is charted with Hemoglobin; uploaded again, it's
            # matched as before without being asked
            def upload_csv(name, body):
                page.goto(base + "/import.html?profile=2")
                page.wait_for_selector("#importer", state="visible", timeout=10000)
                page.click("#tab-files")
                page.set_input_files("#pickFiles", files=[{"name": name, "mimeType": "text/csv", "buffer": body}])
                page.wait_for_function("!document.getElementById('go').disabled", timeout=5000)
                page.click("#go")
                page.wait_for_function(f"document.querySelectorAll('.import-job').length && "
                                       f"/^(Ready to import|All duplicates|Not imported)/.test({last_card}.textContent)", timeout=30000)
            lab_csv = "Date,Test,Result,Units,Reference Range\n{},Hemoglobin Whole Bld,{},g/dL,13.5 - 17.5\n"
            upload_csv("other-lab.csv", lab_csv.format("2026-11-09", "12.9").encode())
            labs = js(f"""(() => {{ const li = {last_card}.querySelector('.imp-labs li'); if (!li) return null;
                const sel = li.querySelector('select'); return {{text: li.textContent, value: sel.value,
                chosen: sel.selectedOptions[0].textContent, options: sel.options.length}}; }})()""")
            check("the preview lists the unknown lab name, with the profile's Hemoglobin series suggested and chosen",
                  labs is not None and "Hemoglobin Whole Bld" in labs["text"] and labs["value"] == "Hemoglobin"
                  and "similar name, same unit" in labs["chosen"] and labs["options"] > 3, labs)
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the preview with lab names to match fits 375 px", js("document.documentElement.scrollWidth") <= 375,
                  js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            js(f"{last_card}.querySelector('.imp-actions .primary').click()")   # Save, accepting the suggestion
            page.wait_for_function(f"/^(Imported|Not imported)/.test({last_card}.textContent)", timeout=30000)
            card = js(f"{last_card}.textContent")
            check("…saved, saying the name went into an existing series", card.startswith("Imported.") and "1 lab name put in an existing series" in card, card)
            page.goto(base + "/")
            page.wait_for_function("typeof SERIES !== 'undefined' && SERIES.length > 0 && PROFILE && PROFILE.name === 'Second person'", timeout=20000)
            js("switchTab('labs')")
            settle(300)
            hgb = js("SERIES.filter(s => s.name === 'Hemoglobin').flatMap(s => s.pts.map(p => [p[0].slice(0, 10), p[1]]))")
            check("…and its result is on Trends in the existing Hemoglobin series",
                  ["2026-11-09", 12.9] in hgb and not js("SERIES.some(s => s.name === 'Hemoglobin Whole Bld')"), hgb)
            check("…charted there", "Hemoglobin" in page.inner_text("#charts"), page.inner_text("#charts")[:200])
            upload_csv("other-lab-again.csv", lab_csv.format("2026-11-12", "13.2").encode())
            again = js(f"(() => {{ const li = {last_card}.querySelector('.imp-labs li'); return li && [li.textContent, li.querySelector('select').value]; }})()")
            check("uploaded again, the name is matched as before", again is not None and "Matched as before" in again[0] and again[1] == "Hemoglobin", again)
            js(f"{last_card}.querySelector('.imp-actions .primary').click()")
            page.wait_for_function(f"/^(Imported|Not imported)/.test({last_card}.textContent)", timeout=30000)
            check("…and saved into Hemoglobin again", js(f"{last_card}.textContent").startswith("Imported."))

            # ---- a SMART Health Link pasted (#94): its host, label and passcode flag are shown and nothing is fetched;
            # Fetch and preview fetches it once and shows the import's preview, which saves like an upload
            import shl_server
            SHL.seen.clear()
            link = SHL.share(shl_server.bundle("2026-11-10", 13.9), passcode="fictional-2468", label="Fictional clinic",
                             viewer="https://viewer.example.org/")
            page.goto(base + "/sources.html?profile=2#link")
            page.wait_for_selector("#healthLink", state="visible", timeout=10000)
            check("Paste a Health Link hides the file drop", js("!document.getElementById('panel-link').hidden && getComputedStyle(document.getElementById('importer')).display === 'none'"))
            page.fill("#shlText", link)
            page.wait_for_selector("#shlAbout", state="visible", timeout=5000)
            facts = page.inner_text("#shlFacts")
            check("the pasted link's host, label and passcode flag are shown", f"localhost:{SHL.port}" in facts and "Fictional clinic" in facts
                  and "needed" in facts and js("!document.getElementById('shlPassRow').hidden"), facts)
            check("…and nothing was fetched from it", SHL.seen == [], SHL.seen)
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the Paste a link tab fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.fill("#shlPass", "fictional-2468")
            page.click("#shlGo")
            page.wait_for_function(f"document.querySelectorAll('.import-job').length && /^(Ready to import|All duplicates|Not imported)/.test({last_card}.textContent)", timeout=30000)
            card = js(f"{last_card}.textContent")
            check("Fetch and preview fetches it once and shows the preview", card.startswith("Ready to import") and "FHIR" in card
                  and len(SHL.seen) == 1, (card[:200], len(SHL.seen)))
            js(f"{last_card}.querySelector('.imp-actions .primary').click()")
            page.wait_for_function(f"/^(Imported|Not imported)/.test({last_card}.textContent)", timeout=30000)
            check("…and saves it", js(f"{last_card}.textContent").startswith("Imported."), js(f"{last_card}.textContent")[:200])
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE && PROFILE.name === 'Second person'", timeout=20000)
            page.select_option("#profile", label="My records (owner)")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records'", timeout=20000)

            # ---- Settings (#35): change settings on the screen, and the dashboard follows; reset a section
            tiles = "[...document.querySelectorAll('#view-overview .tiles .tile .lbl span[title]')].map(s => [s.title, s.textContent])"
            js("switchTab('overview')")
            settle(300)
            before = js(tiles)
            check("the header links to Settings for this profile", js("document.getElementById('settingsLink').getAttribute('href')") == "settings.html?profile=1")
            menu_click(page, "#settingsLink")
            page.wait_for_selector("#sec-overview .set-labs li", timeout=10000)
            check("Settings shows every section", js("[...document.querySelectorAll('#sections > section h2')].map(h => h.textContent)") ==
                  ["Overview", "Lab charts", "Names", "Body and activity", "Visit summary", "Milestones", "Tabs", "Appearance", "Dates and times", "Units", "Reference ranges"], js("[...document.querySelectorAll('#sections > section h2')].map(h => h.textContent)"))
            check("…but not the server's sync defaults or outside connections: those are on the Admin page (#167)",
                  js("!document.getElementById('sec-sync') && !document.getElementById('outside') && !document.querySelector('.set-nav-list a[data-group=sources], .set-nav-list a[data-group=outside]')"))
            # ---- the Settings sidebar (settings-nav.js) and the top bar's menus (nav.js)
            display = "id => getComputedStyle(document.getElementById(id)).display"
            check("Settings: a sidebar of groups, one shown at a time",
                  js("[...document.querySelectorAll('.set-nav-list a')].map(a => a.textContent)")[:4] == ["Dashboard", "Labs and ranges", "Milestones and activity", "Display"]
                  and js(display, "sec-overview") != "none" and js(display, "sec-units") == "none", js("[...document.querySelectorAll('.set-nav-list a')].map(a => a.textContent)"))
            page.fill("#setFind", "time zone")
            check("…Find a setting shows the settings that match, from every group",
                  js(display, "sec-dates") != "none" and js(display, "sec-overview") == "none" and js(display, "sec-units") == "none")
            # #247: a search that matches nothing said nothing, a blank page; it says so, readable in both themes, with a
            # button that clears it
            page.fill("#setFind", "no such setting zq")
            settle(150)
            none_look = """(() => { const b = document.getElementById('setNone'), p = b?.querySelector('p'), c = b?.querySelector('.empty-state'), btn = b?.querySelector('button');
              if (!p || !c) return [null, null, 'rgb(0, 0, 0)', 'rgb(0, 0, 0)', -1];
              return [b.checkVisibility() ? p.textContent : null, btn && btn.textContent, getComputedStyle(p).color, getComputedStyle(c).backgroundColor,
                      [...document.querySelectorAll('#sections > section')].filter(s => s.checkVisibility()).length]; })()"""
            theme_before, nones = js("document.documentElement.dataset.theme || null"), {}
            for theme in ("light", "dark"):
                js(f"document.documentElement.dataset.theme = '{theme}'")
                v = js(none_look)
                nones[theme] = [*v[:2], contrast(v[2], v[3]), v[4]]
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            check("…a search matching nothing says so, readable in light and dark, with a button to clear it (#247)",
                  all(v[0] == "No setting matches “no such setting zq”." and v[1] == "Clear the search" and v[2] >= 4.5 and v[3] == 0 for v in nones.values()), nones)
            page.click("#setNone button")
            check("…which empties the search, back in the box, and shows the group again", js("document.getElementById('setFind').value") == ""
                  and js("document.activeElement.id") == "setFind" and page.is_hidden("#setNone") and js(display, "sec-overview") != "none")
            page.fill("#setFind", "")
            page.click(".set-nav-list a[data-group=display]")
            check("…a group picked in the sidebar is the one shown, and the address says so",
                  js(display, "sec-units") != "none" and js(display, "sec-overview") == "none" and js("location.hash") == "#display")
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("…Settings and its sidebar fit 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            # ---- #174: the group's title and whose settings these are above it (no card of its own), one Save per
            # section with a way back, and on a phone the page's title whole and each key lab one row with a menu
            check("Settings at 375 px: the top bar's title is whole",
                  js("(t => t.textContent === 'Settings' && t.scrollWidth <= t.clientWidth && t.scrollHeight <= t.clientHeight + 1)(document.querySelector('header .page-title'))"),
                  js("(t => [t.scrollWidth, t.clientWidth, t.scrollHeight, t.clientHeight])(document.querySelector('header .page-title'))"))
            check("…Display says, under its title, that its settings are just for you",
                  js("document.getElementById('groupTitle').textContent") == "Display" and js("document.getElementById('introHint').textContent").startswith("Just for you")
                  and js("getComputedStyle(document.getElementById('intro')).borderTopWidth") == "0px", js("document.getElementById('introHint').textContent"))
            groups = js("""(() => { const out = {}; for (const a of document.querySelectorAll('.set-nav-list a')) { a.click();
                out[a.dataset.group] = [document.getElementById('introHint').textContent.split(/[,:]/)[0],
                  [...new Set([...document.querySelectorAll('#sections > section:not(.grp-off) .set[data-scope]')].map(s => s.dataset.scope))]]; }
                return out; })()""")
            say = {"profile": "This profile", "user": "Just for you", "install": "This server"}
            check("…and every group says whose its settings are, as their scope has it",
                  groups["dashboard"][0] == "This profile" and groups["display"][0] == "Just for you" and "calendar" not in groups
                  and all(w in say.values() and len(sc) <= 1 and (not sc or say[sc[0]] == w) for w, sc in groups.values()), groups)
            saves = js("[...document.querySelectorAll('#sections > section')].map(s => [...s.querySelectorAll('button')].filter(b => b.textContent === 'Save').length)")
            check("…one Save per section (none per setting), and nothing marked unsaved on arrival",
                  set(saves) == {1} and not js("[...document.querySelectorAll('#sections .set button')].some(b => b.textContent === 'Save')")
                  and not js("document.querySelector('#sections > [data-dirty], .set-changed')"), saves)
            page.click(".set-nav-list a[data-group=dashboard]")
            rows = "[...document.querySelectorAll('#sec-overview .set-order li')]"
            check("…on a phone each key lab is one row: its handle, name and a menu, the ↑ ↓ Remove buttons put away",
                  js(f"""{rows}.every(li => {{ const r = li.getBoundingClientRect(), m = li.querySelector('.set-more-btn').getBoundingClientRect(),
                      g = li.querySelector('.set-grip').getBoundingClientRect();
                      return m.top - r.top < 30 && g.top - r.top < 30 && m.top >= r.top && m.bottom <= r.bottom && g.top >= r.top && g.bottom <= r.bottom && !li.querySelector('.set-acts').offsetParent; }})"""),
                  js(f"{rows}.map(li => Math.round(li.getBoundingClientRect().height))"))
            order = "[...document.querySelectorAll('#sec-overview .set-more-btn')].map(b => b.getAttribute('aria-label').slice('Move or remove '.length))"
            o0 = js(order)
            page.click("#sec-overview .set-order li:nth-child(1) .set-more-btn")
            page.click("#sec-overview .set-order li:nth-child(1) .menu-item:text-is('Move down')")
            check("…its menu, tapped, moves the lab down", js(order)[:2] == [o0[1], o0[0]], js(order)[:3])
            check("…and it shows it isn't saved: on the setting, with its section's Save kept in view, and its group in the sidebar",
                  js("document.querySelector(\"[data-key='overview.keyLabs']\").classList.contains('set-changed')")
                  and js("getComputedStyle(document.querySelector('#sec-overview .set-foot')).position") == "sticky"
                  and js("document.querySelector('.set-nav-list a[data-group=dashboard]').textContent") == "Dashboard, changes not saved")
            page.focus("#sec-overview .set-order li:nth-child(2) .set-more-btn")
            page.keyboard.press("Enter")
            first_act = js("document.activeElement.textContent")
            page.keyboard.press("ArrowDown")
            check("…the menu works from the keyboard too: Enter opens it on its first action, the arrows move through it",
                  first_act == "Move up" and js("document.activeElement.textContent") == "Move down", first_act)
            page.keyboard.press("Escape")
            check("…and Escape closes it, back on its button",
                  js("document.activeElement.getAttribute('aria-label')") == f"Move or remove {o0[0]}" and js("document.querySelector('#sec-overview .set-order li:nth-child(2) .menu-list').hidden"))
            page.keyboard.press("Enter")
            page.keyboard.press("End")
            page.keyboard.press("Enter")
            check("…its last action takes the lab out, the focus on the row now in its place",
                  o0[0] not in js(order) and js("document.activeElement.getAttribute('aria-label')") == f"Move or remove {o0[2]}", js(order)[:3])
            leave = "(() => { const e = new Event('beforeunload', {cancelable: true}); dispatchEvent(e); return e.defaultPrevented; })()"
            check("…leaving the page with it not saved asks first", js(leave))
            page.select_option("#profile", label="Second person (owner)")
            answer_ask(page, yes=False)
            check("…so does switching profiles, which stays put when told to",
                  js("PID") == 1 and js("document.getElementById('profile').value") == "1" and js("document.getElementById('sec-overview').hasAttribute('data-dirty')"))
            page.click("#sec-overview .set-foot button:text-is('Discard changes')")
            check("…Discard changes puts it back as saved, and nothing's left unsaved",
                  js(order) == o0 and not js("document.querySelector('#sections > [data-dirty]')") and not js(leave)
                  and js("document.querySelector('.set-nav-list a[data-group=dashboard]').textContent") == "Dashboard"
                  and js("document.querySelector('#sec-overview .set-foot > .msg').textContent").startswith("Changes discarded"), js(order)[:3])
            settle(200)
            check("…and the key labs fit 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            # (#210) told yes in the app's dialog, switching profile with changes not saved goes on to the other profile
            page.click("#sec-overview .set-order li:nth-child(1) .set-more-btn")
            page.click("#sec-overview .set-order li:nth-child(1) .menu-item:text-is('Move down')")
            page.select_option("#profile", label="Second person (owner)")
            switch_asked = answer_ask(page)
            page.wait_for_function("PID === 2 && !document.querySelector('#sections > [data-dirty]')", timeout=10000)
            check("…and told yes in the app's dialog, it switches, the changes left behind (#210)",
                  switch_asked[0] == "Switch profile and lose your changes?" and js("document.getElementById('profile').value") == "2", switch_asked)
            page.select_option("#profile", "1")
            page.wait_for_function("PID === 1", timeout=10000)
            settle(400)
            check("…and back on the first profile, its key labs are as saved", js(order) == o0, js(order)[:3])
            page.set_viewport_size({"width": 1400, "height": 900})
            page.click(".set-nav-list a[data-group=dashboard]")
            page.click("#userMenuBtn")
            check("the top bar: your menu opens with its items, Admin among them for an admin", page.is_visible("#settingsLink") and page.is_visible("#logout")
                  and page.is_visible("#adminLink") and js("document.getElementById('userMenuBtn').getAttribute('aria-expanded')") == "true")
            page.keyboard.press("Escape")
            check("…Escape closes it and puts the focus back on its button", page.is_hidden("#userMenuList") and js("document.activeElement.id") == "userMenuBtn")
            page.focus("#addMenuBtn")
            page.keyboard.press("ArrowDown")
            check("…the Add menu opens from the keyboard, on its first item", js("document.activeElement.textContent") == "Upload files", js("document.activeElement.textContent"))
            page.keyboard.press("ArrowDown")
            check("…and the arrow keys move through it", js("document.activeElement.textContent") == "Connect a portal or plan", js("document.activeElement.textContent"))
            check("…each way in opening its section of Sources (#166)", js("[...document.querySelectorAll('#addMenuList a[data-needs=import]')].map(a => a.getAttribute('href'))")
                  == ["sources.html#upload", "sources.html#portals", "sources.html#manual", "sources.html#link"])
            page.keyboard.press("Escape")
            # #244: on a phone the bar wraps, and a menu pinned to its button's right edge opened off the screen's left;
            # each now opens the screen's width under the bar, so every item is on the screen
            menu_out = """id => { const vw = document.documentElement.clientWidth, vh = innerHeight, bar = document.querySelector('header.topbar').getBoundingClientRect().bottom;
                const its = [...document.querySelectorAll(`#${id} [role^=menuitem]`)].filter(x => x.checkVisibility());
                return its.length ? its.filter(x => { const r = x.getBoundingClientRect(); return r.left < 0 || r.right > vw || r.top < bar || r.bottom > vh; })
                  .map(x => x.textContent.trim()) : ['nothing shown']; }"""
            bad = []
            for path in ("/", "/settings.html#display"):
                for w in (320, 375, 390):
                    page.set_viewport_size({"width": w, "height": 800})
                    page.goto(base + path)
                    page.wait_for_selector("#userMenuBtn", state="visible", timeout=10000)
                    settle(300)
                    for m in ("addMenu", "userMenu"):
                        page.click(f"#{m}Btn")
                        bad += [f"{path} at {w} px, {m}: {x}" for x in js(menu_out, m + "List")]
                        page.keyboard.press("Escape")
            check("at 320, 375 and 390 px, the top bar's Add and your menu open under the bar with every item on the screen (#244)", not bad, bad[:6])
            # #308: under 500 px tall the bar scrolls with the page, and the menus opened --bar-h down the screen, a gap under
            # a bar that had scrolled up; opened with the bar scrolled up to the buttons, each sits right under the bar, on the screen
            # (its foot may run below the screen's: it's as tall as its items, and the page scrolls to it, #319)
            menu_short = """id => { const vw = document.documentElement.clientWidth, vh = innerHeight, bar = document.querySelector('header.topbar').getBoundingClientRect().bottom;
                const r = document.getElementById(id).getBoundingClientRect();
                return r.top - bar >= 0 && r.top - bar <= 8 && r.top >= 0 && r.bottom + scrollY <= document.documentElement.scrollHeight && r.left >= 0 && r.right <= vw ? null
                  : [Math.round(bar), Math.round(r.top), Math.round(r.bottom), Math.round(r.left), Math.round(r.right), vw, vh]; }"""
            page.set_viewport_size({"width": 667, "height": 375})
            bad = []
            for path in ("/", "/settings.html#display"):
                page.goto(base + path)
                page.wait_for_selector("#userMenuBtn", state="visible", timeout=10000)
                settle(300)
                for m in ("addMenu", "userMenu"):
                    # scrolled to the button's top edge and tapped (a click from Playwright or the keyboard would scroll the bar back)
                    js("id => { const b = document.getElementById(id); scrollTo(0, b.getBoundingClientRect().top + scrollY); }", m + "Btn")
                    settle(100)
                    scrolled = js("scrollY")
                    js("id => document.getElementById(id).click()", m + "Btn")
                    settle(100)
                    where = js(menu_short, m + "List")
                    if where or not scrolled:
                        bad.append(f"{path} at 667×375 scrolled {scrolled} px, {m}: bar bottom, menu top, bottom, left, right, screen {where}")
                    page.keyboard.press("Escape")
            check("…and at 667×375, where the bar scrolls away, each opened after scrolling sits right under the bar, on the screen (#308)", not bad, bad[:6])
            # #319: between 376 and 451 px the records bar had room for Add on its first row but not your menu, which dropped
            # onto a row of its own; the two are one box now, side by side on one row at every width, on the screen. The
            # records page is checked as it is (the picker a select on a row of its own) and with one profile (a label
            # beside the brand, where it happened)
            menus_row = """() => { const a = document.getElementById('addMenu').getBoundingClientRect(), u = document.getElementById('userMenu').getBoundingClientRect();
                return Math.abs(a.top - u.top) < 1 && a.right <= u.left && a.left >= 0 && u.right <= document.documentElement.clientWidth ? null
                  : [Math.round(a.top), Math.round(a.left), Math.round(a.right), Math.round(u.top), Math.round(u.left), Math.round(u.right)]; }"""
            bad = []
            for path, one in (("/", False), ("/", True), ("/settings.html#display", False)):
                page.set_viewport_size({"width": 1400, "height": 800})
                page.goto(base + path)
                page.wait_for_selector("#userMenuBtn", state="visible", timeout=10000)
                if one:
                    page.wait_for_function("typeof booted !== 'undefined' && booted", timeout=20000)
                    js("SgNav.profiles([{id: PROFILE.id, name: PROFILE.name, role: 'owner'}], PROFILE.id)")
                for w in (320, 375, 400, 451, 452):
                    page.set_viewport_size({"width": w, "height": 800})
                    settle(150)
                    where = js(menus_row)
                    if where:
                        bad.append(f"{path}{' (one profile)' if one else ''} at {w} px: Add, your menu (top, left, right) {where}")
            check("at 320, 375, 400, 451 and 452 px the top bar's Add and your menu sit side by side on one row, on the records page and a page with a title (#319)", not bad, bad[:6])
            # #319: under 500 px tall the menus scroll with the page, and kept a height of the screen less the bar's: at
            # 320×180 (1280×720 at 400%) a box of 37 px showing one item at a time. They're as tall as their items now
            menu_whole = """id => { const l = document.getElementById(id), its = [...l.querySelectorAll('[role^=menuitem]')].filter(x => x.checkVisibility());
                const foot = Math.round(l.getBoundingClientRect().bottom + scrollY), page = document.documentElement.scrollHeight;
                return its.length && l.scrollHeight <= l.clientHeight + 1 && foot <= page ? null : [its.length, l.scrollHeight, l.clientHeight, foot, page]; }"""
            page.set_viewport_size({"width": 320, "height": 180})
            bad = []
            for path in ("/", "/settings.html#display"):
                page.goto(base + path)
                page.wait_for_selector("#userMenuBtn", state="visible", timeout=10000)
                settle(300)
                if js(menus_row):
                    bad.append(f"{path} at 320×180: Add, your menu {js(menus_row)}")
                for m in ("addMenu", "userMenu"):
                    js("id => document.getElementById(id).click()", m + "Btn")
                    settle(100)
                    where = js(menu_whole, m + "List")
                    if where:
                        bad.append(f"{path} at 320×180, {m}: items, scroll height, height, foot, page's height {where}")
                    page.keyboard.press("Escape")
            check("…and at 320×180 both share a row and each menu opens with all its items, no scrolling inside it, the page scrolling to its foot (#319)", not bad, bad[:6])
            page.set_viewport_size({"width": 1400, "height": 900})
            # ---- the Admin page (#167): the server's sync defaults, from their old address on Settings
            page.goto(base + "/settings.html#sources")
            page.wait_for_url("**/admin.html#apps", timeout=10000)
            page.wait_for_selector("#sec-sync .set", timeout=10000)
            check("Admin: an old Settings address opens its new place, the sync defaults (#167)",
                  js(display, "sec-sync") != "none" and js(display, "admin") != "none" and js(display, "users") == "none")
            check("…as a switch, numbers and choices, with one Save for the section (#101, #102, #174)",
                  js("[document.querySelectorAll('#sec-sync input[type=checkbox]').length, document.querySelectorAll('#sec-sync input[type=number]').length, "
                     "document.querySelectorAll('#sec-sync button.primary').length]") == [5, 3, 1], js("document.getElementById('sec-sync').innerText.slice(0, 300)"))
            page.fill("#sec-sync [data-key='sync.staleDays'] input", "5")
            page.click("#syncSave")
            page.wait_for_function("document.querySelector('#sec-sync .msg').textContent.startsWith('Saved')", timeout=10000)
            stale = "fetch('api/profiles/1/settings').then(r => r.json()).then(s => s.sync.staleDays)"
            check("…saved for every connection on the server", js(stale) == 5 and "set for this server" in page.inner_text("#sec-sync [data-key='sync.staleDays']"), js(stale))
            page.click("#sec-sync .set-hd button")
            answer_ask(page)
            page.wait_for_function("document.querySelector('#sec-sync .msg').textContent.includes('back to')", timeout=10000)
            check("…and Reset to defaults puts them back", js(stale) == 3, js(stale))
            page.click("#admNav a[data-group=directory]")
            dir_text = page.inner_text("#directory")
            check("Admin: a group picked in the sidebar is the one shown, and the address says so",
                  js(display, "directory") != "none" and js(display, "sec-sync") == "none" and js("location.hash") == "#directory")
            check("…the provider directory says what's loaded, and how to add Epic's and Oracle Health's lists",
                  "Medicare" in dir_text and "not loaded" in dir_text and "python3 tools/build_directory.py" in dir_text, dir_text[:400])
            for gid in ("accounts", "backups", "apps", "directory", "labs", "outside", "activity"):
                page.click(f"#admNav a[data-group={gid}]")
                page.set_viewport_size({"width": 375, "height": 800})
                settle(150)
                if js("document.documentElement.scrollWidth") > 375:
                    check(f"Admin's {gid} group fits 375 px", False, js("document.documentElement.scrollWidth"))
                page.set_viewport_size({"width": 1400, "height": 900})
            check("…and each of its groups fits 375 px", True)
            # ---- backups in Admin (#200): the key reminder in the card's lead; Back up now runs in the background and
            # then says what it made (when, size, where); Check a backup says it's good, its temporary copy gone; the
            # schedule is a pick-one with how many to keep, and turning it off is asked first in the app's dialog; a
            # backup gone stale shows the warning on every group, beside Backups in the sidebar and on the Admin menu
            # item. In light and dark, at 1400 and 375 px.
            bk_dir = os.path.join(TMP, "backups")
            page.click("#admNav a[data-group=backups]")
            page.wait_for_selector("#bkStatus .kv", timeout=10000)
            check("Backups (#200): its own group, the key reminder in the lead (not behind More about this)",
                  js(display, "backups") != "none" and js("location.hash") == "#backups"
                  and "encryption key isn't in the backups" in js("document.querySelector('#backups > .bk-key').textContent")
                  and js("document.querySelector('#backups > .bk-key').checkVisibility()"), js("location.hash"))
            check("…no backup yet, said in words", js("document.getElementById('bkState').textContent").startswith("No backups yet"))
            # ---- the watch receiver's data in backups (#227): what's in them and what isn't, said on the Backups group;
            # first with no receiver's folder in sight, then with a receiver's (fictional readings, one upload after its
            # last save); Check a backup counts them beside the folder's now
            more = js("document.querySelector('#backups details.more').textContent")
            check("Backups (#227): says the phone and watch data is in them, and what isn't (exports, the key, the configuration)",
                  "the watch receiver's data" in more and "Not in it: your original exports, the encryption key, and the configuration" in more
                  and "phone and watch data" in js("document.querySelector('#backups > .hint').textContent"), more[:300])
            check("…no receiver's folder in sight: not in the backups, said in words",
                  js("document.getElementById('bkWatch').textContent").startswith("Not in the backups: the app can't see the watch receiver's folder"),
                  js("document.getElementById('bkWatch').textContent"))
            import hashlib as _hl, tempfile as _tf
            from server import config as _config
            rx227 = receiver()
            rx227.DATA = _tf.mkdtemp(prefix="rx-backup-", dir=TMP)
            st227 = rx227.Store()
            for seq, rows in ((1, [("2026-11-10T12:05:00Z", 124, 80), ("2026-11-11T12:00:00Z", 121, 79)]), (2, [("2026-11-12T12:10:00Z", 126, 81)])):
                body = json.dumps({"source": "health_connect", "timezone": "America/New_York", "sequence": seq, "timestamp": "2026-11-12T13:00:00Z",
                                   "blood_pressure": [{"uuid": f"fictional-bk-{t}", "time": t, "systolic": s, "diastolic": d, "source": "Fictional Cuff"}
                                                      for t, s, d in rows]}).encode()
                rx227.archive(body)
                if seq == 1:   # the second upload came after the receiver's last save
                    st227.ingest(json.loads(body), _hl.sha256(body).hexdigest())
                    st227.save()
                    time.sleep(0.01)
            _config.WATCH_STORE = rx227.DATA
            page.click("#bkNow")
            page.wait_for_function("document.getElementById('bkNowMsg').textContent.startsWith('Backed up')", timeout=30000)
            made = sorted(n for n in os.listdir(bk_dir) if n.startswith("sanguity-backup-"))
            bk_text = page.inner_text("#bkStatus")
            check("…Back up now runs in the background, then says what it made: when, how big and where", len(made) == 1
                  and made[0] in js("document.getElementById('bkNowMsg').textContent") and "✓ Up to date." in bk_text
                  and bk_dir in bk_text and "1 backup in the folder" in bk_text and js("document.getElementById('bkProgress').hidden"), (made, bk_text[:300]))
            page.click("#bkList tbody button")
            page.wait_for_selector("#bkResult .bk-state", timeout=30000)
            res = page.inner_text("#bkResult")
            check("…Check a backup restores a copy, says it's good with the counts, and the copy is gone",
                  res.startswith("✓ Good.") and "The copy was deleted" in res and js("document.querySelectorAll('#bkResult .bk-counts:not(.bk-watch-counts) tbody tr').length") == 6
                  and sorted(os.listdir(bk_dir)) == made and "✓ good" in page.inner_text("#bkList tbody"), (res[:300], os.listdir(bk_dir)))
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            cl_tables, cl_bad = cell_labels(page, "Admin → Backups")
            check("…at 375 px the backups' cells are labelled with their columns' names, the counts a table with its headings and no labels (#320)",
                  not cl_bad and any("bk-list" in t[0] and t[1] and t[3] >= 3 for t in cl_tables)
                  and any("bk-counts" in t[0] and not t[1] and not t[3] for t in cl_tables), cl_bad[:6] or cl_tables)
            page.set_viewport_size({"width": 1400, "height": 900})
            bk_watch = js("document.getElementById('bkWatch').textContent")   # (#227)
            check("…(#227) the receiver's data is in it: for how many profiles, when it was last saved, the upload that came after",
                  bk_watch.startswith("In the backups: the receiver's data for 1 profile, as the receiver last saved it,") and "every upload it archived" in bk_watch
                  and "1 upload came after that save: in the backup as it arrived, and folded in when the receiver starts after a restore." in bk_watch, bk_watch)
            wrows = js("[...document.querySelectorAll('#bkResult .bk-watch-counts tbody tr')].map(r => [...r.children].map(c => c.textContent))")
            check("…(#227) Check a backup opens and counts the phone and watch data, beside the receiver's folder now, never naming cycle records",
                  "Its phone and watch data, for 1 profile, opens and is intact too, as the receiver last saved it" in res
                  and "with 1 upload that came after that save" in res
                  and wrows[0] == ["Readings", "2", "2", "2"] and wrows[-1] == ["Archived uploads", "2", "2", "2"]
                  and [r[0] for r in wrows] == ["Readings", "Sleep records", "Workouts", "ECGs", "Archived uploads"]
                  and "cycle" not in res.lower(), (res[-400:], wrows))
            page.click("#bkSchedSeg label:has-text('Daily')")
            page.fill("#bkKeep", "3")
            page.click("#bkSchedule button[type=submit]")
            page.wait_for_function("document.getElementById('bkNowMsg').textContent.startsWith('Backups are scheduled daily')", timeout=10000)
            sched = "fetch('api/backups').then(r => r.json()).then(s => [s.schedule, s.keep])"
            check("…the schedule: daily, keeping the newest 3, saved", js(sched) == ["daily", 3] and "daily, keeping the newest 3" in page.inner_text("#bkStatus"), js(sched))
            page.click("#bkSchedSeg label:has-text('Off')")
            refused_from = len(problems)   # a sign-in more than REAUTH_MINUTES old is refused (403) and the page asks
            page.click("#bkSchedule button[type=submit]")
            asked = answer_ask(page)
            for _ in range(50):   # the fresh check, if the sign-in was more than a few minutes ago
                if js("!document.getElementById('bkReauth').hidden || document.getElementById('bkNowMsg').textContent.startsWith('Scheduled backups are off')"):
                    break
                settle(100)
            if js("!document.getElementById('bkReauth').hidden"):
                page.fill("#bkReauth input[type=password]", PW)
                page.click("#bkReauth button[type=submit]")
            page.wait_for_function("document.getElementById('bkNowMsg').textContent.startsWith('Scheduled backups are off')", timeout=10000)
            problems[refused_from:] = [x for x in problems[refused_from:] if "403" not in x]   # that refusal is what asks for the fresh check
            check("…turning it off is asked first, in the app's dialog", asked[0] == "Turn off scheduled backups?" and js(sched)[0] == "off", asked)
            # stale: the backup made 10 days ago, as far as the app can tell (its name says when it was made)
            stale_name = time.strftime("sanguity-backup-%Y%m%d-%H%M%S.tar.gz", time.localtime(time.time() - 10 * 86400))
            os.replace(os.path.join(bk_dir, made[0]), os.path.join(bk_dir, stale_name))
            page.goto(base + "/admin.html#accounts")
            page.reload()   # only the address's #part changed: load the page again
            page.wait_for_selector("#backupNotice:not([hidden])", timeout=10000)
            notice = page.inner_text("#backupNotice")
            check("…a late backup: the warning on every group (Accounts here), saying how old and linking to Backups", "Backups need attention" in notice
                  and "10 days old" in notice and js("document.querySelector('#backupNotice a').getAttribute('href')") == "#backups", notice)
            check("…and beside Backups in the sidebar, in words", js("document.querySelector('#admNav a[data-group=backups] .bk-late')?.textContent") == "late")
            page.click("#userMenuBtn")
            check("…and on the Admin menu item", js("document.querySelector('#adminLink .badge')?.textContent") == "⚠ backups late",
                  js("document.getElementById('adminLink').textContent"))
            page.keyboard.press("Escape")
            page.click("#backupNotice a")
            page.wait_for_selector("#bkStatus .kv", timeout=10000)
            theme_was_200 = js("localStorage.getItem('ht-theme')")
            for theme in ("light", "dark"):
                js(f"localStorage.setItem('ht-theme', '{theme}'); applyTheme()")
                settle(150)
                low = js("""[...document.querySelectorAll('#backups .bk-state, #backups .bk-key, #backups .kv dd, #bkResult .bk-state, #bkResult .bk-watch, #backupNotice')]
                            .map(e => [e.id || e.className, getComputedStyle(e).color, getComputedStyle(document.getElementById('backups')).backgroundColor])""")
                low = [(w, contrast(c, bg)) for w, c, bg in low if contrast(c, bg) < 4.5]
                check(f"…readable in the {theme} theme (text contrast 4.5 or more)", low == [], low)
                for width in (1400, 375):
                    page.set_viewport_size({"width": width, "height": 900})
                    settle(150)
                    fits = phone_fits(page, f"Admin Backups, {theme}, {width} px")
                    check(f"…Backups fits {width} px in the {theme} theme", fits == [], fits)
                page.set_viewport_size({"width": 1400, "height": 900})
            js(f"localStorage.setItem('ht-theme', {json.dumps(theme_was_200)}); applyTheme()" if theme_was_200 else "localStorage.removeItem('ht-theme'); applyTheme()")
            os.replace(os.path.join(bk_dir, stale_name), os.path.join(bk_dir, made[0]))   # up to date again for the checks below
            _config.WATCH_STORE = ""   # (#227) as before, for the checks below
            page.goto(base + "/settings.html?profile=1")   # back to Settings for the checks below
            page.wait_for_selector("#sec-overview .set-labs li", timeout=10000)
            first = before[0][0]
            page.click(f"#sec-overview button[aria-label='Move {first} down']")
            page.click("#sec-overview .set-foot button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-overview .set-foot > .msg').textContent.startsWith('Saved')", timeout=10000)
            page.click("[data-key=labNicknames] button:text-is('Add a row')")
            page.fill("[data-key=labNicknames] .set-row:last-child input[data-k=from]", first)
            page.fill("[data-key=labNicknames] .set-row:last-child input[data-k=to]", "Renamed in Settings")
            page.click("#sec-names .set-foot button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-names .set-foot > .msg').textContent.startsWith('Saved')", timeout=10000)
            # ---- calendar feed (#51): made on the Sharing page (#168; Settings' old address opens it there), its address
            # shown once with setup steps, and the address works for a calendar app with no session
            page.goto(base + "/settings.html?profile=1#calendar")
            page.wait_for_url("**/sharing.html?profile=1#calendar", timeout=10000)
            page.wait_for_selector("#calendar:not([hidden]) #calNew:not([hidden])", timeout=10000)
            check("Settings' old calendar address opens Sharing → Calendar feed", js("document.querySelector('#shrNav [aria-current=page]').textContent") == "Calendar feed")
            check("calendar feed: minimal details are the default", js("document.querySelector('input[name=calDetails]:checked').value") == "minimal")
            page.click("#calCreate")
            page.wait_for_selector("#calSetup:not([hidden])", timeout=10000)
            feed_url = js("document.getElementById('calUrl').value")
            steps = page.inner_text("#calSetup")
            check("…creating one shows its address and setup steps for each calendar app",
                  re.match(re.escape(base) + r"/api/profiles/1/calendar\.ics\?token=[\w-]{40,}$", feed_url) is not None
                  and all(w in steps for w in ("iPhone", "Mac", "Google Calendar", "Outlook")) and page.locator("#calCopy").is_visible(), feed_url)
            check("…and warns that a local plain-HTTP address won't reach a calendar elsewhere", "plain HTTP" in page.inner_text("#calReach"))
            with urllib.request.urlopen(feed_url) as resp:
                ics = resp.read().decode("utf-8")
                cache = resp.headers.get("Cache-Control")
            check("…the address gives an iCalendar feed with the upcoming visit, titles only",
                  ics.startswith("BEGIN:VCALENDAR\r\n") and "SUMMARY:Appointment\r\n" in ics and "Follow-up" not in ics and cache.startswith("private"), ics[:200])
            check("…the owner sees it among the profile's feeds", USER in page.inner_text("#calList"))
            # medication reminders (#162): the one set on the records page, as a repeating event in its own time zone
            check("…with the medication reminder set earlier, repeating on weekdays in its own zone, minimal ('Medication')",
                  "SUMMARY:Medication\r\n" in ics and "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;UNTIL=" in ics and "BEGIN:VTIMEZONE" in ics
                  and "DTSTART;TZID=" in ics and "BEGIN:VALARM" in ics and page.is_checked("#calMedsNow") and page.is_checked("#calLabsNow"), ics[-900:])
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the calendar feed's setup panel fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.click("#calDone")
            check("…and the address isn't kept on the page once done", js("document.getElementById('calUrl').value") == "")
            settle(200)
            check("the Sharing page's calendar feed fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            cl_tables, cl_bad = cell_labels(page, "Sharing → Calendar feed")
            check("…its feeds' cells labelled with their columns' names: Person, Says, Last fetched (#320)",
                  not cl_bad and any(t[0].startswith("calOthers") and t[1] and t[3] >= 3 for t in cl_tables), cl_bad[:6] or cl_tables)
            page.set_viewport_size({"width": 1400, "height": 900})
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records'", timeout=20000)
            js("switchTab('overview')")
            settle(300)
            after = js(tiles)
            check("…the Overview tiles follow the new order and name",
                  [t for t, _ in after[:2]] == [before[1][0], first] and after[1][1] == "Renamed in Settings", after[:3])
            page.goto(base + "/settings.html?profile=1#sec-overview")
            page.wait_for_selector("#sec-overview .set-labs li", timeout=10000)
            for sec in ("overview", "names"):
                page.click(f"#sec-{sec} button:text-is('Reset to defaults')")
                answer_ask(page)
                page.wait_for_function(f"document.querySelector('#sec-{sec} .set-foot > .msg').textContent.includes('back to their defaults')", timeout=10000)
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records'", timeout=20000)
            js("switchTab('overview')")
            settle(300)
            check("…and Reset to defaults brings them back", js(tiles) == before, js(tiles)[:3])

            # ---- #36 key-lab tiles: drag or keyboard-move them, with a live preview, and the Overview follows
            page.goto(base + "/settings.html?profile=1#sec-overview")
            page.wait_for_selector("#sec-overview .set-preview .tile", timeout=10000)
            preview = "[...document.querySelectorAll('#sec-overview .set-preview .tile')].map(t => t.dataset.lab)"
            p0 = js(preview)
            check("key labs: the preview shows the Overview's tiles", p0 == [t for t, _ in before], p0[:4])
            page.click("#sec-overview button[aria-label='Remove Magnesium']")   # the demo has just the 12 default labs
            check("…a lab taken out leaves the preview", "Magnesium" not in js(preview))
            sugg_ = js("[...document.querySelectorAll('#sec-overview .set-sugg .chip')].map(b => b.getAttribute('aria-label'))")
            check("…and is suggested among the profile's most-measured labs",   # first: the vitals taken at visits (#180, #184, #226) have fewer
                  sugg_ == ["Add Magnesium", "Add Blood pressure, diastolic", "Add Blood pressure, systolic", "Add Respiratory rate", "Add SpO2 (clinic)", "Add Temperature"], sugg_)
            page.click("#sec-overview .set-sugg .chip")
            check("…to put back", js(preview) == p0, js(preview)[-2:])
            grips = page.locator("#sec-overview .set-grip")
            grips.nth(0).scroll_into_view_if_needed()   # (the section is taller since #186: the chip's click scrolled them away)
            g2, g0 = grips.nth(2).bounding_box(), grips.nth(0).bounding_box()
            page.mouse.move(g2["x"] + g2["width"] / 2, g2["y"] + g2["height"] / 2)
            page.mouse.down()
            page.mouse.move(g0["x"] + g0["width"] / 2, g0["y"] + 2, steps=8)
            page.mouse.up()
            p1 = js(preview)
            check("…dragging a lab by its handle moves it, and the preview follows at once", p1[:3] == [p0[2], p0[0], p0[1]], p1[:3])
            grips.nth(0).focus()
            page.keyboard.press("ArrowDown")
            page.keyboard.press("ArrowDown")
            page.keyboard.press("ArrowUp")
            p2 = js(preview)
            check("…and so do the arrow keys on its handle, which keeps the focus",
                  p2[:3] == [p0[0], p0[2], p0[1]] and js("document.activeElement.dataset.act") == "grip", p2[:3])
            page.click("#sec-overview .set-foot button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-overview .set-foot > .msg').textContent.startsWith('Saved')", timeout=10000)
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records'", timeout=20000)
            js("switchTab('overview')")
            settle(300)
            check("…the Overview follows the new order", [t for t, _ in js(tiles)[:3]] == p2[:3], js(tiles)[:3])

            # ---- Start from a condition (#186): Settings → Dashboard offers the condition the problem list names (the demo's
            # lymphoma; not its resolved anemia), finds another by a synonym, and previews both: each list's guideline,
            # the tiles interleaved most important first, the labs with no results here named without suggesting a test.
            # Filled in, the Overview section is changed but not saved, and Discard changes puts it back; filled in again
            # with a Labs view and the visit summary's ticks, each section saved, the Overview shows the preset's tiles,
            # and Reset to defaults undoes it. At 1400 and 375 px, light and dark, axe-clean. A child's profile: below (#196).
            api_get = "p => fetch('api/' + p, {cache: 'no-store'}).then(r => r.json())"
            api_put = "([p, v]) => fetch('api/' + p, {method: 'PUT', headers: {'X-Sanguity': '1', 'Content-Type': 'application/json'}, body: JSON.stringify({value: v})}).then(r => r.status)"
            saved_views = js(api_get, "profiles/1/settings")["trends"]["views"]
            pre_tiles = "[...document.querySelectorAll('#preOut .tile')].map(t => t.dataset.lab)"
            def presets_open():
                page.goto(base + "/settings.html?profile=1#sec-overview")
                page.wait_for_selector("#presets .pre-offer button", timeout=10000)
                page.wait_for_load_state("networkidle")
                settle(200)
            with open(os.path.join(ROOT, "condition-presets.json"), encoding="utf-8") as f:
                n_presets = len(json.load(f)["conditions"])   # the fuller list (#187)
            presets_open()
            offer = page.inner_text("#presets .pre-suggest")
            check("presets (#186): the problem list's condition is offered, never applied by itself",
                  "Hodgkin lymphoma, in remission" in offer and "Cancer, during treatment" in offer and "Anemia" not in offer
                  and js("document.getElementById('prePanel').hidden && !document.querySelector('#sec-overview[data-dirty]')"), offer)
            page.click("#presets .pre-offer button")
            check("…choosing it opens the picker with it ticked, grouped by body system",
                  js("document.querySelector('label[data-id=cancer-treatment] input').checked") and js("[...document.querySelectorAll('#preList legend')].map(l => l.textContent)")[:2]
                  == ["Diabetes, thyroid, hormones and metabolism", "Heart and blood vessels"], js("[...document.querySelectorAll('#preList legend')].map(l => l.textContent)"))
            page.fill("#preFind", "CKD")
            settle(100)
            check("…the search finds a condition by a synonym, and says how many match",
                  js("[...document.querySelectorAll('#preList label[data-id]')].filter(l => !l.hidden).map(l => l.textContent)") == ["Chronic kidney disease · also called CKD"]
                  and page.inner_text("#preCount") == f"1 of {n_presets} match", page.inner_text("#preCount"))
            page.check("label[data-id='chronic-kidney-disease'] input")
            page.fill("#preFind", "")
            src = page.inner_text("#preOut .pre-src")
            check("…the preview names each list's guideline, worded as commonly followed, not yet reviewed by a clinician",
                  "commonly followed for it according to KDIGO" in src and "ASCO-ONS Standards (2024)" in src and src.count("Not reviewed by a clinician yet") == 2
                  and js("[...document.querySelectorAll('#preOut .pre-src a')].every(a => a.href.startsWith('https://') && a.rel.includes('noopener'))"), src[:300])
            check("…Add to my tiles first: these labs are among the tiles already, so the tiles stay as they are",
                  js(pre_tiles) == p2 and not js("document.querySelector('#preOut .pre-new')"), js(pre_tiles))
            page.click("#prePanel input[value='replace']")
            check("…Replace my tiles: both conditions' labs with results here, interleaved, most important first, at most 12",
                  js(pre_tiles) == ["WBC", "Automated Abs Neutrophil Cnt", "Creatinine", "Hemoglobin", "Platelet Count", "Potassium, Blood",
                                    "Lymphocytes Absolute", "Sodium, Blood", "ALT", "AST", "Total Bilirubin", "Magnesium"], js(pre_tiles))
            # ---- A fuller list of conditions (#187): the long list is searched by synonym, accents and apostrophes aside,
            # and by a group's name; with two conditions chosen, each previewed tile and each lab with no results says
            # which it's from, and each condition's own list is behind "Its list" (kept open as the preview redraws).
            # Axe at 1400 and 375 px, light and dark, with the two chosen and a list open.
            check("fuller list (#187): the picker lists every condition in the file, in its groups",
                  js("document.querySelectorAll('#preList label[data-id]').length") == n_presets and n_presets >= 60
                  and js("document.querySelectorAll('#preList fieldset').length") == 11, js("document.querySelectorAll('#preList label[data-id]').length"))
            shown = "[...document.querySelectorAll('#preList label[data-id]')].filter(l => !l.hidden).map(l => l.dataset.id)"
            page.fill("#preFind", "crohns")
            settle(100)
            check("…the search ignores apostrophes: “crohns” finds Crohn's disease", js(shown) == ["crohns-disease"], js(shown))
            page.fill("#preFind", "hashimoto")
            settle(100)
            check("…and finds a merged name as a synonym of the condition it's part of",
                  js(shown) == ["hypothyroidism"] and "also called Hashimoto" in page.inner_text("label[data-id=hypothyroidism]"), page.inner_text("#preList"))
            page.fill("#preFind", "cancer")
            settle(100)
            check("…a group's name shows the whole group: Cancer includes multiple myeloma and CLL",
                  {"multiple-myeloma", "chronic-lymphocytic-leukemia", "cancer-treatment"} <= set(js(shown))
                  and js("[...document.querySelectorAll('#preList fieldset')].filter(f => !f.hidden).map(f => f.dataset.system)") == ["cancer"], js(shown))
            page.fill("#preFind", "")
            settle(100)
            froms = js("Object.fromEntries([...document.querySelectorAll('#preOut .tile')].map(t => [t.dataset.lab, t.querySelector('.pre-from')?.textContent || null]))")
            check("…each previewed tile says which chosen conditions it's from",
                  froms.get("Creatinine") == "From Cancer, during treatment and Chronic kidney disease" and froms.get("WBC") == "From Cancer, during treatment"
                  and froms.get("Sodium, Blood") == "From Cancer, during treatment and Chronic kidney disease", froms)
            check("…and so does each lab with no results here", "CKD-EPI eGFR (Chronic kidney disease)" in page.inner_text("#preMissing"), page.inner_text("#preMissing"))
            page.click("#preOut .pre-def[data-id=chronic-kidney-disease] summary")
            own = js("[...document.querySelectorAll('#preOut .pre-def[data-id=chronic-kidney-disease] li')].map(l => l.textContent)")
            check("…“Its list” shows the condition's own labs, most important first, those with no results said so",
                  own[:2] == ["CKD-EPI eGFR · no results here", "Creatinine"] and len(own) == 12
                  and "At home: Blood pressure at home." in page.inner_text("#preOut .pre-def[data-id=chronic-kidney-disease]"), own[:4])
            page.click("#prePanel input[value='add']")
            check("…and stays open as the preview is drawn again", js("document.querySelector('#preOut .pre-def[data-id=chronic-kidney-disease]').open"))
            page.click("#prePanel input[value='replace']")
            for width in (1400, 375):
                page.set_viewport_size({"width": width, "height": 900 if width > 375 else 800})
                for theme in ("light", "dark"):
                    page.emulate_media(color_scheme=theme)
                    settle(150)
                    serious, other = axe_check(page, f"Settings fuller condition list {width} {theme}")
                    check(f"…two conditions previewed, a list open, at {width} px, {theme}: axe finds nothing serious", not serious, "; ".join(serious + other)[:600])
                    if width == 375:
                        fit = phone_fits(page, f"presets 187 {theme}")
                        check(f"…and it fits 375 px ({theme})", not fit, "; ".join(fit)[:400])
                    if os.environ.get("SANGUITY_SHOTS"):
                        page.locator("#presets").screenshot(path=os.path.join(os.environ["SANGUITY_SHOTS"], f"187-preview-{width}-{theme}.png"))
            page.emulate_media(color_scheme=None)
            page.set_viewport_size({"width": 1400, "height": 900})
            page.click("#preChosen button[aria-label='Remove Cancer, during treatment']")
            missing = page.inner_text("#preMissing")
            check("…one taken away: its tiles go, and the labs with no results here are named, suggesting no test",
                  js(pre_tiles) == ["Creatinine", "Potassium, Blood", "Sodium, Blood", "Hemoglobin"]
                  and missing.startswith("No results in this profile's records, so no tile: CKD-EPI eGFR; Urine Albumin/Creatinine Ratio;")
                  and not re.search(r"should|need|recommend|get tested|ask your", page.inner_text("#presets"), re.I), missing)
            check("…its home reading, blood pressure, as an Overview tile", "Overview tiles: Blood pressure." in page.inner_text("#preHome"), page.inner_text("#preHome"))
            for width in (1400, 375):
                page.set_viewport_size({"width": width, "height": 900 if width > 375 else 800})
                for theme in ("light", "dark"):
                    page.emulate_media(color_scheme=theme)
                    settle(150)
                    serious, other = axe_check(page, f"Settings start from a condition {width} {theme}")
                    check(f"…the picker and its preview at {width} px, {theme}: axe finds nothing serious", not serious, "; ".join(serious + other)[:600])
                    if width == 375:
                        fit = phone_fits(page, f"presets {theme}")
                        check(f"…and it fits 375 px ({theme})", not fit, "; ".join(fit)[:400])
                    if os.environ.get("SANGUITY_SHOTS"):
                        page.locator("#presets").screenshot(path=os.path.join(os.environ["SANGUITY_SHOTS"], f"186-picker-{width}-{theme}.png"))
            page.emulate_media(color_scheme=None)
            page.set_viewport_size({"width": 1400, "height": 900})
            page.click("#preFill")
            check("…Fill these in changes the key labs below, not saved yet",
                  js("SET_ITEMS['overview.keyLabs'].value()") == ["Creatinine", "Potassium, Blood", "Sodium, Blood", "Hemoglobin"]
                  and js("!!document.querySelector('#sec-overview[data-dirty] [data-key=\"overview.keyLabs\"].set-changed')")
                  and page.inner_text("#preOut .msg").startswith("Filled in: Key labs") and "Discard changes" in page.inner_text("#preOut .msg"),
                  page.inner_text("#preOut .msg"))
            check("…and its tiles' preview below follows", js("[...document.querySelectorAll('#sec-overview .set-preview .tile')].map(t => t.dataset.lab)")
                  == ["Creatinine", "Potassium, Blood", "Sodium, Blood", "Hemoglobin"])
            page.click("#sec-overview .set-foot button:text-is('Discard changes')")
            check("…Discard changes puts them back as saved", js("SET_ITEMS['overview.keyLabs'].value()") == p2 and not js("document.querySelector('#sections > section[data-dirty]')"))
            page.click("#preOpen")
            page.fill("#preFind", "kidney")
            page.check("label[data-id='chronic-kidney-disease'] input")
            page.click("#prePanel input[value='replace']")
            page.check("#preViews")
            page.check("#preSummary")
            page.click("#preFill")
            check("…filled in with a view on Labs and the visit summary's ticks: three sections changed",
                  js("[...document.querySelectorAll('#sections > section[data-dirty]')].map(s => s.id)") == ["sec-overview", "sec-trends", "sec-summary"],
                  js("[...document.querySelectorAll('#sections > section[data-dirty]')].map(s => s.id)"))
            for sec in ("overview", "trends", "summary"):
                page.click(f"#sec-{sec} .set-foot button:text-is('Save')")
                page.wait_for_function(f"document.querySelector('#sec-{sec} .set-foot > .msg').textContent.startsWith('Saved')", timeout=10000)
            cfg = js(api_get, "profiles/1/settings")
            check("…saved: the key labs, a Labs view named for the condition, blood pressure at home as a tile and ticked in the summary",
                  cfg["overview"]["keyLabs"] == ["Creatinine", "Potassium, Blood", "Sodium, Blood", "Hemoglobin"] and cfg["overview"]["homeTiles"] == ["bp"]
                  and {"name": "Chronic kidney disease", "labs": ["Creatinine", "Potassium, Blood", "Sodium, Blood", "Hemoglobin"], "default": False} in cfg["trends"]["views"]
                  and cfg["summary"]["ticked"] == ["home_bp"], [cfg["overview"], cfg["summary"]])
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records'", timeout=20000)
            js("switchTab('overview')")
            settle(300)
            check("…the Overview shows the preset's tiles, once: nothing is linked to the preset", [t for t, _ in js(tiles)][:4] == ["Creatinine", "Potassium, Blood", "Sodium, Blood", "Hemoglobin"],
                  js(tiles)[:5])
            presets_open()
            for sec in ("overview", "summary"):
                page.click(f"#sec-{sec} button:text-is('Reset to defaults')")
                answer_ask(page)
                page.wait_for_function(f"document.querySelector('#sec-{sec} .set-foot > .msg').textContent.includes('back to their defaults')", timeout=10000)
            cfg = js(api_get, "profiles/1/settings")
            KEY_LABS_DEFAULT = __import__("server.settings", fromlist=["KEY_LABS"]).KEY_LABS
            check("…and Reset to defaults undoes it", cfg["overview"]["keyLabs"] == KEY_LABS_DEFAULT and cfg["overview"]["homeTiles"] == [] and cfg["summary"]["ticked"] == [],
                  cfg["overview"])
            check("…(the Labs view and the key labs' order put back as they were, for the checks below)",
                  js(api_put, ["profiles/1/settings/trends.views", saved_views]) == 200 and js(api_put, ["profiles/1/settings/overview.keyLabs", p2]) == 200)
            page.goto(base + "/")   # back on the records page, as the checks below expect
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records'", timeout=20000)

            # ---- #40 tabs: hide Costs and move Timeline up; gone from the bar and the arrow keys, a saved view or a
            # link to it opens the first shown tab, and one tab at least stays shown
            js("switchTab('costs')")   # the saved view now names Costs
            page.goto(base + "/settings.html?profile=1#sec-tabs")
            page.wait_for_selector("#sec-tabs input[data-tab=costs]", timeout=10000)
            page.uncheck("#sec-tabs input[data-tab=costs]")
            page.click("#sec-tabs button[aria-label='Move Timeline up']")
            page.click("#sec-tabs .set-foot button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-tabs .set-foot > .msg').textContent.startsWith('Saved')", timeout=10000)
            shown_tabs = "[...document.querySelectorAll('.tabs [role=tab]')].filter(b => !b.hidden).map(b => b.id.slice(4))"
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records' && booted", timeout=20000)
            check("tabs: a hidden tab leaves the bar, the others in their new order",
                  js(shown_tabs) == ["overview", "timeline", "health", "labs"], js(shown_tabs))
            check("…a saved view naming it opens the first shown tab", js("state.tab") == "overview" and js("!document.getElementById('view-overview').hidden"), js("state.tab"))
            page.focus("#tab-overview")
            page.keyboard.press("ArrowLeft")
            check("…the arrow keys skip it", js("state.tab") == "labs" and js("document.activeElement.id") == "tab-labs", js("state.tab"))
            page.keyboard.press("ArrowRight")
            js("switchTab('costs')")
            check("…asking for it opens the first shown tab", js("state.tab") == "overview", js("state.tab"))
            page.goto(base + "/#costs")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records' && booted", timeout=20000)
            check("…so does a link to it", js("state.tab") == "overview" and js("document.getElementById('view-costs').hidden"), js("state.tab"))
            page.goto(base + "/#timeline")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records' && booted", timeout=20000)
            check("…while a link to a shown tab opens it", js("state.tab") == "timeline", js("state.tab"))
            for old, view in (("heatmap", "heatmap"), ("trends", "chart"), ("labs", "chart")):   # #172: the old tabs' links open Labs
                page.goto(base + "/#" + old)
                page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records' && booted", timeout=20000)
                check(f"…a link to #{old} opens Labs as a {view}", js("[state.tab, state.labsView]") == ["labs", view]
                      and js("id => !document.getElementById('view-labs').hidden && !document.getElementById(id).hidden", "labsHeatmap" if view == "heatmap" else "labsChart"),
                      js("[state.tab, state.labsView]"))
            page.goto(base + "/settings.html?profile=1#sec-tabs")
            page.wait_for_selector("#sec-tabs input[data-tab=costs]", timeout=10000)
            for t in ("overview", "health", "labs"):
                page.uncheck(f"#sec-tabs input[data-tab={t}]")
            check("…and the last shown tab can't be hidden", js("document.querySelector('#sec-tabs input[data-tab=timeline]').disabled"))
            page.click("#sec-tabs button:text-is('Reset to defaults')")
            answer_ask(page)
            page.wait_for_function("document.querySelector('#sec-tabs .set-foot > .msg').textContent.includes('back to their defaults')", timeout=10000)
            page.click("#sec-overview button:text-is('Reset to defaults')")
            answer_ask(page)
            page.wait_for_function("document.querySelector('#sec-overview .set-foot > .msg').textContent.includes('back to their defaults')", timeout=10000)
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records' && booted", timeout=20000)
            check("…Reset to defaults shows every tab again", js(shown_tabs) == ["overview", "health", "timeline", "labs", "costs"], js(shown_tabs))
            # a layout saved before #172, with Trends and Lab heatmap: Labs takes the first one's place, and stays shown
            # while either was; the Settings page lists the five tabs
            db = dbm.connect()
            db.execute("INSERT INTO settings(key, profile_id, user_id, value, sealed, changed, changed_by) VALUES('tabs', 1, NULL, ?, NULL, ?, NULL)",
                       (json.dumps({"order": ["overview", "heatmap", "health", "timeline", "labs", "costs"], "hidden": ["labs", "costs"]}), int(time.time())))
            db.close()
            page.reload()
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records' && booted", timeout=20000)
            check("…a layout saved with Trends and Lab heatmap shows Labs where the heatmap was, shown as it was",
                  js(shown_tabs) == ["overview", "labs", "health", "timeline"] and js("CFG.tabs") == {"order": ["overview", "labs", "health", "timeline", "costs"], "hidden": ["costs"]},
                  [js(shown_tabs), js("CFG.tabs")])
            page.goto(base + "/settings.html?profile=1#sec-tabs")
            page.wait_for_selector("#sec-tabs input[data-tab=costs]", timeout=10000)
            check("…and Settings lists the five tabs, Labs shown", js("[...document.querySelectorAll('#sec-tabs input[data-tab]')].map(i => [i.dataset.tab, i.checked])")
                  == [["overview", True], ["labs", True], ["health", True], ["timeline", True], ["costs", False]],
                  js("[...document.querySelectorAll('#sec-tabs input[data-tab]')].map(i => [i.dataset.tab, i.checked])"))
            page.click("#sec-tabs button:text-is('Reset to defaults')")
            answer_ask(page)
            page.wait_for_function("document.querySelector('#sec-tabs .set-foot > .msg').textContent.includes('back to their defaults')", timeout=10000)
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records' && booted", timeout=20000)
            js("switchTab('overview')")

            # ---- the tab in the address (#208), in a browser of its own (its own history and saved view): picking a
            # tab adds a history entry, so Back and Forward move between tabs; the app's own switches and the Labs view
            # replace it; a reload, an old alias and a copied address in a new browser open the tab named; ?profile=
            # stays beside the #, and an address with no # opens the tab last used
            ready = "typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records' && booted"
            session = [{k: c[k] for k in ("name", "value", "domain", "path", "httpOnly", "sameSite")} for c in page.context.cookies() if c["name"] == "sg_session"]
            tctx = browser.new_context(viewport={"width": 1400, "height": 900}, bypass_csp=True)
            tctx.add_cookies(session)
            tp = tctx.new_page()
            tp.on("pageerror", lambda e: problems.append(f"script error (tab address): {e}"))
            blank = tp.evaluate("history.length")   # the new tab's about:blank
            tp.goto(base + "/")
            tp.wait_for_function(ready, timeout=20000)
            addr = lambda: tp.evaluate("[location.search, location.hash, state.tab, state.labsView, history.length]")
            n0 = tp.evaluate("history.length")
            check("tab address: opening the page with no # names the tab it opens, without a history entry of its own",
                  addr()[1:] == ["#overview", "overview", "chart", n0] and n0 == blank + 1, [addr(), blank])
            for t in ("timeline", "labs", "costs"):
                tp.click("#tab-" + t)
            check("…picking Timeline, Labs and Costs adds an entry each, the address naming the tab", addr()[1:] == ["#costs", "costs", "chart", n0 + 3], addr())
            tp.go_back()
            tp.go_back()
            tp.wait_for_function("state.tab === 'timeline'", timeout=5000)
            check("…Back twice lands on Timeline", addr()[1:3] == ["#timeline", "timeline"] and tp.evaluate("!document.getElementById('view-timeline').hidden"), addr())
            tp.go_forward()
            tp.wait_for_function("state.tab === 'labs'", timeout=5000)
            check("…and Forward on Labs", addr()[1:3] == ["#labs", "labs"], addr())
            tp.evaluate("switchTab('overview'); switchTab('costs'); switchTab('heatmap')")
            check("…the app's own switches replace the entry (Labs as the heatmap: #labs-heatmap)", addr()[1:] == ["#labs-heatmap", "labs", "heatmap", n0 + 3], addr())
            tp.click("#labsView button[data-view=chart]")
            check("…so does the Labs view", addr()[1:] == ["#labs", "labs", "chart", n0 + 3], addr())
            tp.click("#labsView button[data-view=heatmap]")
            tp.reload()
            tp.wait_for_function(ready, timeout=20000)
            check("…a reload keeps the tab and its view", addr()[1:4] == ["#labs-heatmap", "labs", "heatmap"], addr())
            tp.evaluate("location.hash = '#trends'")
            tp.wait_for_function("state.labsView === 'chart'", timeout=5000)
            check("…an old alias opens its tab, the address then naming it", addr()[1:4] == ["#labs", "labs", "chart"], addr())
            tp.goto(base + "/?profile=1#costs")
            tp.wait_for_function(ready + " && state.tab === 'costs'", timeout=20000)
            tp.click("#tab-health")
            tp.click("#tab-labs")
            tp.click("#labsView button[data-view=heatmap]")
            check("…?profile= stays beside the #", addr()[:2] == ["?profile=1", "#labs-heatmap"], addr())
            copied = tp.url
            other = browser.new_context(viewport={"width": 1400, "height": 900}, bypass_csp=True)   # no saved view
            other.add_cookies(session)
            op = other.new_page()
            op.on("pageerror", lambda e: problems.append(f"script error (copied address): {e}"))
            op.goto(copied)
            op.wait_for_function(ready, timeout=20000)
            check("…a copied address opens the same tab in a new browser",
                  op.evaluate("[location.search, location.hash, state.tab, state.labsView, !document.getElementById('labsHeatmap').hidden]")
                  == ["?profile=1", "#labs-heatmap", "labs", "heatmap", True], op.evaluate("[location.href, state.tab, state.labsView]"))
            other.close()
            tp.go_back()
            tp.wait_for_function("state.tab === 'health'", timeout=5000)
            check("…and Back from there returns to Body and activity, the profile kept", addr()[:3] == ["?profile=1", "#health", "health"], addr())
            tp.goto(base + "/")
            tp.wait_for_function(ready, timeout=20000)
            check("…while the address with no # opens the tab last used", addr()[1:3] == ["#health", "health"], addr())
            tctx.close()

            # ---- #45 a default theme for the account: a fresh browser signed in as the same person uses it; the ◐
            # toggle still overrides it there; Reset to defaults follows the system again
            js("localStorage.removeItem('ht-theme')")
            page.goto(base + "/settings.html?profile=1#sec-appearance")
            page.wait_for_selector("#sec-appearance input[value=dark]", timeout=10000)
            page.check("#sec-appearance input[value=dark]")
            page.click("#sec-appearance .set-foot button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-appearance .set-foot > .msg').textContent.startsWith('Saved')", timeout=10000)
            check("theme: saving a default applies it here", js("document.documentElement.dataset.theme") == "dark")
            fresh = browser.new_context(viewport={"width": 375, "height": 800}, bypass_csp=True)
            fresh.add_cookies([{k: c[k] for k in ("name", "value", "domain", "path", "httpOnly", "sameSite")}
                               for c in page.context.cookies() if c["name"] == "sg_session"])
            fp = fresh.new_page()
            fp.on("pageerror", lambda e: problems.append(f"script error (fresh browser): {e}"))
            fp.goto(base + "/")
            fp.wait_for_function("typeof EV !== 'undefined' && EV.length > 0", timeout=20000)
            check("…a fresh browser uses it once signed in", fp.evaluate("document.documentElement.dataset.theme") == "dark")
            fp.goto(base + "/account.html")   # a page that loads no settings: theme.js has it from the last visit
            check("…and so does its next page, before it draws", fp.evaluate("document.documentElement.dataset.theme") == "dark")
            fp.goto(base + "/")
            fp.wait_for_function("typeof EV !== 'undefined' && EV.length > 0", timeout=20000)
            menu_click(fp, "#theme")
            fp.reload()
            fp.wait_for_function("typeof EV !== 'undefined' && EV.length > 0", timeout=20000)
            check("…where the ◐ toggle still overrides it", fp.evaluate("document.documentElement.dataset.theme") == "light")
            fresh.close()
            page.click("#sec-appearance button:text-is('Reset to defaults')")
            answer_ask(page)
            page.wait_for_function("document.querySelector('#sec-appearance .set-foot > .msg').textContent.includes('back to their defaults')", timeout=10000)
            check("…and Reset to defaults follows the system again",
                  js("document.documentElement.dataset.theme === undefined && localStorage.getItem('ht-theme-default') === null"))
            # ---- #41 unit preferences: SI units and kg, picked on the Settings page, convert the demo's creatinine
            # (mg/dL to umol/L), magnesium and hemoglobin and the weights on Trends, the Overview tiles and the drawer, the
            # reported value beside each; bilirubin has no exact conversion and stays as reported; at 375 px too
            page.goto(base + "/settings.html?profile=1#sec-units")
            page.wait_for_selector("#sec-units input[name=units-lab][value=si]", timeout=10000)
            page.check("#sec-units input[name=units-lab][value=si]")
            page.check("#sec-units input[name=units-weight][value=kg]")
            # ---- pick one on Settings (#193): the theme, each part's units and the sex for reference ranges are segmented
            # controls of radios, each with exactly one checked, and the radio picked (above, through its see-through box)
            # is the one that looks selected
            radios = r"""[...document.querySelectorAll('fieldset.set-radios > .seg')].map(g => [g.closest('[id^=sec-]').id + ' ' + (g.parentElement.dataset.part || ''),
              g.querySelectorAll(':scope > label > input[type=radio]:checked').length, g.children.length,
              [...g.children].filter(l => getComputedStyle(l).boxShadow.includes('inset')).map(l => l.querySelector('input').value).join()])"""
            got = js(radios)
            check("pick one on Settings: every group of radios is one control with exactly one checked (theme, units, sex for ranges)",
                  len(got) == 5 and all(n == 1 and k > 1 for _, n, k, _ in got), got)
            check("…and the one picked looks selected", [x[3] for x in got if x[0] in ("sec-units lab", "sec-units weight")] == ["si", "kg"], got)
            page.click("#sec-units .set-foot button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-units .set-foot > .msg').textContent.startsWith('Saved')", timeout=10000)
            page.goto(base + "/")
            page.wait_for_function("typeof booted !== 'undefined' && booted && PROFILE.id === 1", timeout=20000)
            unit_of = "n => { const s = SERIES.find(s => s.name === n); const p = s && s.pts.at(-1); return s && {unit: s.unit, from: s.uc ? s.uc.from : null, v: p[1], orig: p.orig ? p.orig.v : null, lo: p[2]}; }"
            cr, mg, hb, bili = (js(unit_of, n) for n in ("Creatinine", "Magnesium", "Hemoglobin", "Total Bilirubin"))
            check("units: SI shows creatinine in µmol/L, converted exactly from what was reported",
                  cr["unit"] == "µmol/L" and cr["from"] == "mg/dL" and abs(cr["v"] - float(cr["orig"]) * 10000 / 113.12) < 1e-6
                  and abs(cr["lo"] - 61.9) < 1e-9, cr)
            check("…magnesium in mmol/L and hemoglobin in g/L", (mg["unit"], hb["unit"]) == ("mmol/L", "g/L")
                  and abs(hb["v"] - float(hb["orig"]) * 10) < 1e-9, (mg, hb))
            check("…bilirubin, with no exact conversion, stays as reported", bili["unit"] == "mg/dL" and bili["from"] is None, bili)
            check("…and the weights are in kg", js("SERIES.find(s => s.key === 'weight').unit") == "kg")
            js("switchTab('overview')")
            tile = js("[...document.querySelectorAll('#view-overview .tile')].find(t => t.textContent.includes('Creatinine'))?.textContent || ''")
            check("Overview: the creatinine tile in µmol/L, with the reported value", "µmol/L" in tile and re.search(r"Reported [\d.]+ mg/dL", tile), tile)
            wtile = js("[...document.querySelectorAll('#view-overview .tile')].find(t => t.textContent.startsWith('Weight'))?.textContent || ''")
            check("…the weight tile in kg, with the reported lb", "kg" in wtile and re.search(r"Reported [\d.]+ lb", wtile), wtile)
            # over all of the records: the period earlier checks can leave saved (the last 30 days) starts hours after the
            # demo's last creatinine, so the charts had no results and the table no rows in some runs
            js("state.sel = new Set(['Creatinine', 'Total Bilirubin']); state.tables.clear(); setPeriod('all'); switchTab('labs'); renderLabs()")
            page.wait_for_selector("#charts .chart .unit", timeout=10000)
            heads = js("[...document.querySelectorAll('#charts .chart .unit')].map(u => u.textContent)")
            check("Trends: the chart says it's converted; bilirubin's doesn't", len(heads) == 2   # charts come in the series' order
                  and sorted(h.split(" · ")[0] for h in heads) == ["mg/dL", "µmol/L (converted from mg/dL)"], heads)
            rd = js("document.querySelector('#charts .chart .rdiff')?.textContent || ''")   # #58: ranges compared once converted
            check("…and the two labs' creatinine ranges, converted, still differ", rd.startswith("Reference range differs between results: 61.9–115 and 53–106 µmol/L"), rd)
            js("state.tables.add('Creatinine'); renderCharts()")
            cell = js("document.querySelector('#charts table.data tbody td:nth-child(2)').textContent")
            check("…its table shows the reported value beside each", re.search(r"µmol/L \(reported [\d.]+ mg/dL, ref 0\.7–1\.3\)", cell), cell)
            js("state.tables.clear()")
            ev = js("(async () => { await loadDetails(DATA.events.map(e => e.id)); return EV.find(e => e.k === 'lab' && (DATA.details[e.id]?.ids || []).some(i => (DATA.details[i]?.rows || []).some(r => r[0] === 'Creatinine')))?.id; })()")
            js("id => openEvent(byId.get(id))", ev)
            page.wait_for_selector("#drawer.open #dbody td small.orig", timeout=10000)
            row = js("[...document.querySelectorAll('#dbody tr')].map(r => [...r.cells].map(c => c.textContent)).find(c => c[0].replace(/[✎?]/g, '').trim() === 'Creatinine')")   # the ✎ and "What is this test?" (#163) beside the name
            check("drawer: the result in µmol/L, its range converted, the reported value and range under it",
                  row and re.match(r"[\d.]+ µmol/LReported [\d.]+ mg/dL, ref 0\.7", row[1]) and re.match(r"61\.9–115", row[2]), row)
            page.set_viewport_size({"width": 375, "height": 800})
            check("…and the drawer fits 375 px", js("document.documentElement.scrollWidth <= 375 && document.getElementById('dbody').scrollWidth <= document.getElementById('dbody').clientWidth"),
                  js("document.getElementById('dbody').scrollWidth"))
            js("closeDrawer(); switchTab('overview')")
            check("…the Overview with converted tiles fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.goto(base + "/settings.html?profile=1#sec-units")
            page.wait_for_selector("#sec-units input[name=units-lab][value=si]", timeout=10000)
            check("the Settings page with the units fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.click("#sec-units button:text-is('Reset to defaults')")
            answer_ask(page)
            page.wait_for_function("document.querySelector('#sec-units .set-foot > .msg').textContent.includes('back to their defaults')", timeout=10000)
            check("…Reset to defaults shows units as reported again", js("document.querySelector('#sec-units input[name=units-lab]:checked').value") == "reported")
            # ---- visit summary (#48): pick the visit and sections, add a lab, type questions (kept as a draft),
            # preview, then download the PDF and read it back
            page.goto(base + "/")   # from the main page, after the settings checks above
            page.wait_for_function("typeof booted !== 'undefined' && booted", timeout=20000)
            menu = js("[...document.querySelectorAll('#userMenuList [role=menuitem]')].map(x => x.textContent)")
            check("your menu has one Sharing item, in place of Visit summary and Share links (#168)",
                  js("document.getElementById('sharingLink').getAttribute('href')") == "sharing.html" and "Sharing" in menu
                  and "Visit summary" not in menu and "Share links" not in menu, menu)
            was_profile = js("localStorage.getItem('ht-profile')")   # the Sharing page remembers the profile picked, as Settings does
            page.goto(base + "/summary.html?profile=2")   # the second profile: results from several imports, Ferritin typed in
            page.wait_for_url("**/sharing.html?profile=2#summary", timeout=10000)   # the old address opens Sharing → Visit summary
            page.wait_for_selector("#build:not([hidden])", timeout=10000)
            check("the old visit summary address opens Sharing → Visit summary, on that profile",
                  js("document.getElementById('profile').value") == "2" and js("document.querySelector('#shrNav [aria-current=page]').textContent") == "Visit summary")
            check("the summary page offers every section", js("[...document.querySelectorAll('#sections input')].map(i => i.value)") ==
                  ["out_of_range", "trends", "medications", "appointments", "questions"])
            check("…the last visit is the default since point", js("document.querySelector('#since option').textContent.startsWith('The last visit (')"))
            page.uncheck("#sections input[value=appointments]")
            page.fill("#labIn", "Ferritin")
            page.click("#labAdd")
            check("…a lab added to the trends", js("PICKED") == ["Ferritin"], js("PICKED"))
            page.fill("#questions", "Is the ferritin result worth rechecking?\nShould the next draw be fasting?")
            page.wait_for_function("document.getElementById('qMsg').textContent === 'Draft saved.'", timeout=10000)
            page.click("#previewBtn")
            page.wait_for_selector("#preview:not([hidden]) h3", timeout=10000)
            heads = js("[...document.querySelectorAll('#preview h3')].map(h => h.textContent.replace(/ \\(\\d+\\)$/, ''))")
            check("…the preview shows the ticked sections", len(heads) == 4 and heads[0].startswith("Out of range since") and heads[-1] == "My questions", heads)
            check("…with the questions and the notice", "Should the next draw be fasting?" in js("document.getElementById('preview').textContent")
                  and "isn't medical advice" in js("document.getElementById('preview').textContent"))
            check("…and Ferritin among the trends", "Ferritin" in js("document.getElementById('preview').textContent"))
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the visit summary and its preview fit 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            cl_tables, cl_bad = cell_labels(page, "Sharing → Visit summary")
            check("…its preview's tables with each cell under its column's name (#320)",
                  not cl_bad and sum(1 for t in cl_tables if t[0].startswith("preview") and t[1] and t[3]) >= 2, cl_bad[:6] or cl_tables)
            # #324: no preview cell is wider than its row (a long test name beside an unbroken date was cut at the card's edge)
            wide = {}
            for w in (375, 320):
                page.set_viewport_size({"width": w, "height": 800})
                settle(200)
                wide[w] = js("""[...document.querySelectorAll('#preview .tbl td')].filter(td => {
                    const r = td.closest('tr').getBoundingClientRect(), c = td.getBoundingClientRect();
                    return td.offsetParent && (c.right > r.right + 0.5 || td.scrollWidth > td.clientWidth + 1); })
                  .map(td => td.textContent.slice(0, 40))""")
            check("…and no preview cell is wider than its row at 375 and 320 px (#324)", not wide[375] and not wide[320], wide)
            page.set_viewport_size({"width": 1400, "height": 900})
            with page.expect_download(timeout=20000) as dl:
                page.click("#download")
            pdf_path = os.path.join(TMP, "summary-download.pdf")
            dl.value.save_as(pdf_path)
            from pypdf import PdfReader
            reader = PdfReader(pdf_path)
            text = "\n".join(p_.extract_text() for p_ in reader.pages)
            check("the summary downloads as a PDF of one or two pages", dl.value.suggested_filename.startswith("visit-summary-Second-person-")
                  and len(reader.pages) in (1, 2), (dl.value.suggested_filename, len(reader.pages)))
            check("…holding the sections, the questions and the notice",
                  all(w in text for w in ("Visit summary", "Second person", "Out of range since", "Trends since", "Ferritin",
                                          "Current medications", "Should the next draw be fasting?", "isn't medical advice"))
                  and "Upcoming appointments" not in text, text[:300])
            page.wait_for_function("document.getElementById('buildMsg').textContent.startsWith('Downloaded')", timeout=5000)
            page.reload()
            page.wait_for_selector("#build:not([hidden])", timeout=10000)
            check("…and the questions draft is still there", js("document.getElementById('questions').value").startswith("Is the ferritin result"))
            page.fill("#questions", "")   # leave no draft behind
            page.wait_for_function("document.getElementById('qMsg').textContent === 'Draft saved.'", timeout=10000)
            js("v => v === null ? localStorage.removeItem('ht-profile') : localStorage.setItem('ht-profile', v)", was_profile)
            # ---- the phone's and watch's measures in the visit summary, share links and Overview tiles (#225): on profile 1
            # (the readings browser_check folds in: home blood pressure, glucose, temperature, oxygen, respiratory rate,
            # VO2 max, ECGs, immunizations), each measure is a summary section of its own, offered unticked; ticked, the
            # preview and the PDF show it (glucose's time in ranges labelled as the standard); a share link's form offers
            # them unticked and its page shows the ones ticked; Settings → Overview → Home readings puts blood pressure,
            # glucose and temperature tiles after the key labs, with the latest value, its date and where it came from;
            # 1400 and 375 px, light and dark, axe-core clean
            shots = os.environ.get("SANGUITY_SHOTS")
            m225 = ["home_bp", "glucose", "temperature", "oxygen", "respiratory", "vo2max", "immunizations", "ecg",
                    "medlog"]   # the medication log (#235): doses logged above
            was_profile = js("localStorage.getItem('ht-profile')")
            page.goto(base + "/sharing.html?profile=1#summary")
            page.wait_for_function("document.getElementById('profile').value === '1' && OPTS && OPTS.profile.id === 1 && "
                                   "!document.getElementById('build').hidden", timeout=10000)
            offered = js("[...document.querySelectorAll('#sections input')].map(i => [i.value, i.checked])")
            got = dict(offered)
            check("visit summary (#225): the phone's and watch's measures are offered as sections of their own, unticked",
                  all(k in got and got[k] is False for k in m225) and all(got[k] for k in ("out_of_range", "trends", "medications", "appointments", "questions"))
                  and len(got) == len(offered), offered)
            js("document.getElementById('since').selectedIndex = document.getElementById('since').options.length - 1")   # the oldest visit: more readings
            for k in got:
                (page.check if k in ("home_bp", "glucose", "ecg", "immunizations", "temperature") else page.uncheck)(f"#sections input[value={k}]")
            page.click("#previewBtn")
            page.wait_for_selector("#preview:not([hidden]) h3", timeout=10000)
            heads = js("[...document.querySelectorAll('#preview h3')].map(h => h.textContent)")
            pv = js("document.getElementById('preview').textContent")
            check("…ticked, the preview shows each: blood pressure at home with the count, average and highest, and its readings",
                  len(heads) == 5 and heads[0].startswith("Blood pressure at home since") and re.search(r"\d+ readings? · average \d+/\d+ mmHg · highest systolic \d+", pv)
                  and js("document.querySelectorAll('#preview table').length") == 5, heads)
            check("…glucose by day, CGM time in ranges labelled as the standard published ranges (not anyone's targets)",
                  "Blood glucose at home since" in pv and "time in the standard ranges" in pv and "standard published ranges" in pv
                  and "not anyone's own targets" in pv and not re.search(r"(?i)\b(normal|good|bad|target range)\b", pv), heads)
            check("…immunizations as a list, and ECGs as the watch said them (no traces)", any(h.startswith("Immunizations (") for h in heads)
                  and "traces themselves aren't included" in pv and re.search(r"ECG: \w", pv) and "samples" not in pv, heads)
            p225_bad, p225_axe = [], []
            for width in (1400, 375):
                page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                for theme in ("light", "dark"):
                    js("t => { document.documentElement.dataset.theme = t; }", theme)
                    settle(200)
                    if width == 375:
                        p225_bad += phone_fits(page, f"summary preview with measures ({theme})")
                        p225_bad += [] if js("document.documentElement.scrollWidth") <= 375 else [f"summary page {js('document.documentElement.scrollWidth')} px wide"]
                    p225_axe += axe_check(page, f"summary preview with measures, {width} px, {theme}")[0]
            js("delete document.documentElement.dataset.theme")
            page.set_viewport_size({"width": 1400, "height": 900})
            check("…the summary with the measures fits 375 px, and axe-core finds nothing serious, light and dark", not p225_bad and not p225_axe, (p225_bad + p225_axe)[:6])
            with page.expect_download(timeout=20000) as dl:
                page.click("#download")
            pdf_path = os.path.join(TMP, "summary-measures.pdf")
            dl.value.save_as(pdf_path)
            from pypdf import PdfReader
            text = "\n".join(p_.extract_text() for p_ in PdfReader(pdf_path).pages)
            check("…and the PDF has them, in words (black-and-white safe): the readings, time in ranges, the immunizations, the ECGs",
                  all(w in text for w in ("Blood pressure at home since", "SYSTOLIC/DIASTOLIC", "Blood glucose at home since", "standard published ranges",
                                          "Body temperature at home", "isn't medical advice")), text[:400])
            # share links: the form offers them unticked; a link made with two shows those two on its page
            page.goto(base + "/sharing.html?profile=1#links")
            page.wait_for_selector("#links .share-links button:has-text('New share link')", timeout=10000)
            page.locator("#links .share-links button", has_text="New share link").click()
            form = page.locator("#links .share-form")
            form.wait_for(timeout=10000)
            boxes = js("Object.fromEntries([...document.querySelectorAll('#links .share-form .share-sections input')].map(i => [i.value, i.checked]))")
            check("share links (#225): the form offers the measures as sections, unticked; never cycle tracking",
                  all(boxes.get(k) is False for k in m225) and "cycle" not in boxes and boxes.get("visits") is True, boxes)
            for k in boxes:
                (form.locator(f".share-sections input[value={k}]").check if k in ("home_bp", "glucose") else form.locator(f".share-sections input[value={k}]").uncheck)()
            form.locator("input[type=date]").first.fill("")
            check("…and says what each one shows", "Blood pressure at home: each reading taken at home" in form.locator(".share-sees").inner_text(), form.locator(".share-sees").inner_text()[:200])
            form.locator("button[type=submit]").click()
            page.wait_for_selector(".share-made .share-url", timeout=10000)
            url225 = page.input_value(".share-made .share-url")
            holder = browser.new_context(viewport={"width": 1400, "height": 900}, device_scale_factor=1)
            hp = holder.new_page()
            hp.goto(url225)
            hp.wait_for_selector("#share-home_bp", timeout=10000)
            sh = hp.evaluate("""[[...document.querySelectorAll('#shareBody > section')].map(s => s.id),
              [...document.querySelectorAll('#share-home_bp tbody tr')].length, document.querySelector('#share-glucose').textContent]""")
            check("…its page shows only those two: the readings at home, and glucose with the standard ranges labelled",
                  sh[0] == ["share-home_bp", "share-glucose"] and sh[1] > 0 and "standard published ranges" in sh[2], sh[:2])
            sl_bad, sl_axe = [], []
            for width in (1400, 375):
                hp.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                for theme in ("light", "dark"):
                    hp.emulate_media(color_scheme=theme)
                    hp.wait_for_timeout(200)
                    if width == 375:
                        sl_bad += phone_fits(hp, f"share page with measures ({theme})")
                        sl_bad += [] if hp.evaluate("document.documentElement.scrollWidth") <= 375 else ["share page too wide"]
                    sl_axe += axe_check(hp, f"share page with measures, {width} px, {theme}")[0]
            if shots:
                hp.set_viewport_size({"width": 375, "height": 800})
                hp.emulate_media(color_scheme="dark")
                hp.screenshot(path=os.path.join(shots, "225-share-375-dark.png"), full_page=True)
            hp.emulate_media(color_scheme=None)
            holder.close()
            check("…which fits 375 px, and axe-core finds nothing serious, light and dark", not sl_bad and not sl_axe, (sl_bad + sl_axe)[:6])
            page.locator("#links .share-links button", has_text="Revoke").first.click()
            answer_ask(page, yes=True)
            # Overview tiles: chosen under Settings → Overview → Home readings
            page.goto(base + "/settings.html?profile=1#sec-overview")
            page.wait_for_selector("#sec-overview .set[data-key='overview.homeTiles'] input[type=checkbox]", timeout=10000)
            ht = js("[...document.querySelectorAll(\"#sec-overview .set[data-key='overview.homeTiles'] input\")].map(i => [i.value, i.checked, i.parentElement.textContent.trim()])")
            check("Overview tiles (#225): Settings → Overview offers home readings as tiles, none chosen by default",
                  ht == [["bp", False, "Blood pressure"], ["glucose", False, "Blood glucose"], ["temp", False, "Body temperature"]], ht)
            for k in ("bp", "glucose", "temp"):
                page.check(f"#sec-overview .set[data-key='overview.homeTiles'] input[value={k}]")
            page.click("#sec-overview .set-foot button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-overview .set-foot > .msg').textContent.startsWith('Saved')", timeout=10000)
            page.goto(base + "/?profile=1")
            page.wait_for_function("typeof booted !== 'undefined' && booted", timeout=20000)
            js("switchTab('overview')")
            settle(400)
            tiles = js("""[...document.querySelectorAll('#view-overview .tiles .tile.home-tile')].map(t => [t.dataset.home, t.querySelector('.lbl').textContent,
              t.querySelector('.val').textContent, t.querySelector('.delta').textContent])""")
            check("…chosen, the Overview shows them after the key labs: the latest value, its date and where it came from",
                  [t[0] for t in tiles] == ["bp", "glucose", "temp"] and re.match(r"\d+/\d+mmHg", tiles[0][2] if tiles else "")
                  and all(re.search(r"\d{4}", t[3]) and ("at home" in t[3] or "at a visit" in t[3] or "phone app" in t[3]) for t in tiles)
                  and js("document.querySelector('#view-overview .tiles .tile:last-child').classList.contains('home-tile')"), tiles)
            ov_bad, ov_axe = [], []
            for width in (1400, 375):
                page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                for theme in ("light", "dark"):
                    js("t => { document.documentElement.dataset.theme = t; }", theme)
                    settle(200)
                    page.locator("#view-overview .home-tile").first.scroll_into_view_if_needed()
                    if width == 375:
                        ov_bad += phone_fits(page, f"Overview with home tiles ({theme})")
                        ov_bad += [] if js("document.documentElement.scrollWidth") <= 375 else ["Overview too wide"]
                    if shots and width == 375 and theme == "dark":
                        page.screenshot(path=os.path.join(shots, "225-tiles-375-dark.png"))
                    ov_axe += axe_check(page, f"Overview with home tiles, {width} px, {theme}")[0]
            js("delete document.documentElement.dataset.theme")
            page.set_viewport_size({"width": 1400, "height": 900})
            check("…the tiles fit 375 px, and axe-core finds nothing serious, light and dark", not ov_bad and not ov_axe, (ov_bad + ov_axe)[:6])
            js("window.scrollTo(0, 0)")
            page.click("#view-overview .home-tile[data-home=bp]")
            js("window.__redrawn = false; setTimeout(() => { renderHealth(); window.__redrawn = true; }, 120)")   # Body and activity drawn again as its data finish loading (as on CI's slower machines)
            page.wait_for_function("window.__redrawn === true", timeout=5000)   # checked only after that redraw, not before it
            try:
                page.wait_for_function("state.tab === 'health' && document.activeElement && document.activeElement.id === 'h-bp'", timeout=5000)
            except Exception:
                pass
            check("…a tile opens its card on Body and activity", js("state.tab") == "health" and js("document.activeElement.id") == "h-bp",
                  [js("state.tab"), js("document.activeElement.id"), js("!!document.getElementById('h-bp')"), js("document.activeElement.tagName")])
            js("fetch('api/profiles/1/settings/overview.homeTiles', {method: 'DELETE', headers: {'X-Sanguity': '1'}})")
            js("v => v === null ? localStorage.removeItem('ht-profile') : localStorage.setItem('ht-profile', v)", was_profile)
            # ---- glucose and blood oxygen typed in, in the visit summary, a share link and the Overview's glucose tile
            # (#232): on profile 2, which has no phone, two glucose readings (each with a meal note) and a blood oxygen
            # typed in through Sources' route; the summary offers both sections, and ticked, its preview and PDF list each
            # one (glucose with its time and meal note, oxygen one by one), a share link's page too; the glucose tile says
            # it was typed in, and Body and activity draws them with no phone's glucose; 1400 and 375 px, light and dark,
            # axe-core; then the save is undone
            was_profile = js("localStorage.getItem('ht-profile')")
            saved232 = js("""fetch('api/profiles/2/manual', {method: 'POST', headers: {'X-Sanguity': '1', 'Content-Type': 'application/json'},
              body: JSON.stringify({save: true, items: [
                {kind: 'home_glucose', date: '2026-11-08', time: '07:45', glucose: '104', glucose_unit: 'mg/dL', meal: 'before'},
                {kind: 'home_glucose', date: '2026-11-09', time: '19:10', glucose: '151', glucose_unit: 'mg/dL', meal: 'after'},
                {kind: 'home_spo2', date: '2026-11-09', time: '08:05', spo2: '97'}]})}).then(r => r.json())""")
            iid232 = saved232.get("import_id")
            check("typed in, in shares (#232): glucose and blood oxygen typed in for a profile without a phone",
                  bool(iid232) and (saved232.get("counts") or {}).get("readings") == 3, saved232)
            page.goto(base + "/sharing.html?profile=2#summary")
            page.wait_for_function("document.getElementById('profile').value === '2' && OPTS && OPTS.profile.id === 2 && "
                                   "!document.getElementById('build').hidden", timeout=10000)
            got = dict(js("[...document.querySelectorAll('#sections input')].map(i => [i.value, i.checked])"))
            check("…the visit summary offers their glucose and blood oxygen sections, unticked", got.get("glucose") is False and got.get("oxygen") is False, got)
            js("document.getElementById('since').selectedIndex = document.getElementById('since').options.length - 1")
            for k in got:
                (page.check if k in ("glucose", "oxygen") else page.uncheck)(f"#sections input[value={k}]")
            page.click("#previewBtn")
            page.wait_for_selector("#preview:not([hidden]) h3", timeout=10000)
            pv = js("document.getElementById('preview').textContent")
            rows232 = js("[...document.querySelectorAll('#preview table tbody tr')].map(r => [...r.cells].map(c => c.textContent))")
            check("…ticked, the preview lists each glucose reading typed in with its time and meal note, and the blood oxygen one",
                  any("Typed in, 07:45, before a meal" in r for r in rows232) and any("Typed in, 19:10, after a meal" in r for r in rows232)
                  and any("Typed in, 08:05" in r and "97" in r for r in rows232) and "2 meter readings typed in · average 128 mg/dL" in pv
                  and "1 reading typed in · average 97%" in pv and pv.count("typed in at home") >= 2
                  and js("[...document.querySelectorAll('#preview table')].map(t => t.querySelectorAll('th').length)") == [4, 4], [rows232, pv[:300]])
            s232_bad, s232_axe = [], []
            for width in (1400, 375):
                page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                for theme in ("light", "dark"):
                    js("t => { document.documentElement.dataset.theme = t; }", theme)
                    settle(200)
                    page.locator("#preview table").first.scroll_into_view_if_needed()
                    if width == 375:
                        s232_bad += phone_fits(page, f"summary preview with typed readings ({theme})")
                        s232_bad += [] if js("document.documentElement.scrollWidth") <= 375 else ["summary page too wide"]
                    if shots:
                        page.screenshot(path=os.path.join(shots, f"232-summary-{width}-{theme}.png"))
                    s232_axe += axe_check(page, f"summary preview with typed readings, {width} px, {theme}")[0]
            js("delete document.documentElement.dataset.theme")
            page.set_viewport_size({"width": 1400, "height": 900})
            check("…which fits 375 px, and axe-core finds nothing serious, light and dark", not s232_bad and not s232_axe, (s232_bad + s232_axe)[:6])
            with page.expect_download(timeout=20000) as dl:
                page.click("#download")
            pdf_path = os.path.join(TMP, "summary-typed.pdf")
            dl.value.save_as(pdf_path)
            from pypdf import PdfReader
            text = "\n".join(p_.extract_text() for p_ in PdfReader(pdf_path).pages)
            check("…and the PDF has them in words", all(w in text for w in ("Typed in, 07:45, before a meal", "Typed in, 19:10, after a meal",
                  "2 meter readings typed in", "Typed in, 08:05", "1 reading typed in")), text[:400])
            # a share link with the two: its page lists them as the summary does
            made = js("""fetch('api/profiles/2/links', {method: 'POST', headers: {'X-Sanguity': '1', 'Content-Type': 'application/json'},
              body: JSON.stringify({sections: ['glucose', 'oxygen']})}).then(r => r.json())""")
            holder = browser.new_context(viewport={"width": 1400, "height": 900}, device_scale_factor=1)
            hp = holder.new_page()
            hp.goto(base + "/" + made["path"])
            hp.wait_for_selector("#share-glucose tbody tr", timeout=10000)
            sh = hp.evaluate("""['#share-glucose', '#share-oxygen'].map(k => [...document.querySelectorAll(k + ' tbody tr')].map(r => r.textContent))""")
            check("…a share link's page lists them too: glucose each with its meal note, blood oxygen one by one",
                  [len(x) for x in sh] == [2, 1] and "after a meal" in sh[0][0] and "before a meal" in sh[0][1] and "Typed in, 08:05" in sh[1][0]
                  and "typed in at home" in hp.inner_text("#share-oxygen"), sh)
            l232_bad, l232_axe = [], []
            for width in (1400, 375):
                hp.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                for theme in ("light", "dark"):
                    hp.emulate_media(color_scheme=theme)
                    hp.wait_for_timeout(200)
                    if width == 375:
                        l232_bad += phone_fits(hp, f"share page with typed readings ({theme})")
                        l232_bad += [] if hp.evaluate("document.documentElement.scrollWidth") <= 375 else ["share page too wide"]
                    if shots:
                        hp.screenshot(path=os.path.join(shots, f"232-share-{width}-{theme}.png"), full_page=True)
                    l232_axe += axe_check(hp, f"share page with typed readings, {width} px, {theme}")[0]
            holder.close()
            check("…which fits 375 px, and axe-core finds nothing serious, light and dark", not l232_bad and not l232_axe, (l232_bad + l232_axe)[:6])
            js("id => fetch('api/profiles/2/links/' + id, {method: 'DELETE', headers: {'X-Sanguity': '1'}})", made["link"]["id"])
            # the Overview's glucose tile, and Body and activity with typed glucose only (no phone's)
            js("""fetch('api/profiles/2/settings/overview.homeTiles', {method: 'PUT', headers: {'X-Sanguity': '1', 'Content-Type': 'application/json'},
              body: JSON.stringify({value: ['glucose']})})""")
            page.goto(base + "/?profile=2")
            page.wait_for_function("typeof booted !== 'undefined' && booted && PROFILE && PROFILE.id === 2", timeout=20000)
            js("switchTab('overview')")
            settle(400)
            tile232 = js("(t => t && [t.querySelector('.val').textContent, t.querySelector('.delta').textContent])(document.querySelector('#view-overview .home-tile[data-home=glucose]'))")
            check("…the Overview's glucose tile shows the latest typed in, with its meal note",
                  tile232 and tile232[0].startswith("151") and "a meter reading at home · Typed in, after a meal" in tile232[1], tile232)
            t232_bad, t232_axe = [], []
            for width in (1400, 375):
                page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                for theme in ("light", "dark"):
                    js("t => { document.documentElement.dataset.theme = t; }", theme)
                    settle(200)
                    page.locator("#view-overview .home-tile").first.scroll_into_view_if_needed()
                    if width == 375:
                        t232_bad += phone_fits(page, f"Overview with a typed glucose tile ({theme})")
                    if shots:
                        page.screenshot(path=os.path.join(shots, f"232-tile-{width}-{theme}.png"))
                    t232_axe += axe_check(page, f"Overview with a typed glucose tile, {width} px, {theme}")[0]
            js("delete document.documentElement.dataset.theme")
            page.set_viewport_size({"width": 1400, "height": 900})
            check("…which fits 375 px, and axe-core finds nothing serious, light and dark", not t232_bad and not t232_axe, (t232_bad + t232_axe)[:6])
            js("switchTab('health')")
            settle(400)
            check("…and Body and activity draws glucose typed in with no phone's", js("!!document.getElementById('h-glucose') && G && G.meter.length === 2")
                  and js("!!document.getElementById('h-oxygen')"), js("[!!document.getElementById('h-glucose'), G && G.meter]"))
            js("fetch('api/profiles/2/settings/overview.homeTiles', {method: 'DELETE', headers: {'X-Sanguity': '1'}})")
            undone = js("id => fetch('api/profiles/2/imports/' + id + '/undo', {method: 'POST', headers: {'X-Sanguity': '1'}}).then(r => r.status)", iid232)
            check("…undone, they're gone", undone == 200 and js("fetch('api/profiles/2/watch').then(r => r.status === 200 ? r.json() : {}).then(w => (w.glucose_readings || []).length + (w.spo2_readings || []).length)") == 0, undone)
            js("v => v === null ? localStorage.removeItem('ht-profile') : localStorage.setItem('ht-profile', v)", was_profile)
            # ---- targets of your own for home readings (#234): on profile 1, Sharing → About this person has a card of its
            # own, every measure without a target at first and Body and activity drawing none; an editor's targets are
            # typed in, changed and cleared per measure (one refused, in words), kept in one unit each; Body and activity
            # then draws each as a band or line, labelled, marks readings at home outside it ▲ or ▼ with words in its
            # table and list, says "N of M … above it" (counted as the page shows them), and shows CGM time in the
            # person's own range beside the standard published one, each labelled; the Overview's home tiles say it in
            # words; the visit summary and a share link offer it unticked; at 1400 and 375 px, light and dark, axe clean;
            # cleared at the end, nothing is left drawn
            was_profile = js("localStorage.getItem('ht-profile')")
            no_marks = "document.querySelectorAll('#view-health .tgt-line, #view-health .tgt-band, #view-health .tgt-edge, #view-health .glu-own').length"
            page.goto(base + "/?profile=1")
            page.wait_for_function("typeof booted !== 'undefined' && booted && PROFILE && PROFILE.id === 1", timeout=20000)
            js("state.tables.clear(); state.from = dayTs('2026-08-15'); state.to = dayTs('2026-11-15'); state.preset = 'custom'; switchTab('health')")
            settle(500)
            check("targets of your own (#234): with none entered, Body and activity draws none, and says nothing of any",
                  js(no_marks) == 0 and "your target" not in js("document.getElementById('view-health').innerText").lower())
            page.goto(base + "/sharing.html?profile=1#about")
            page.wait_for_selector("#about[data-profile='1']", timeout=10000)
            tcard = js("[!document.getElementById('targets').hidden && !document.getElementById('targets').classList.contains('grp-off'), "
                       "document.getElementById('targets').innerText]")
            check("…a card of its own beside About this person, worded as the care team's, every measure without one at first",
                  tcard[0] and "Targets of your own" in tcard[1] and "as your care team gave it" in tcard[1] and tcard[1].count("No target entered.") == 6
                  and all(n in tcard[1] for n in ("Blood pressure", "Blood glucose, meter readings", "CGM time in range", "Body temperature", "Blood oxygen", "Weight")),
                  tcard[1][:300])
            def t_save(k, vals, said_start):
                for name, v in vals.items():
                    page.fill(f"#tgt-{k}-save >> xpath=ancestor::form//input[@name='{name}']", v)
                page.click(f"#tgt-{k}-save")
                page.wait_for_function(f"document.getElementById('targetsMsg').textContent.startsWith({json.dumps(said_start)})", timeout=10000)
                return js("document.getElementById('targetsMsg').textContent")
            said = [t_save("bp", {"sysHigh": "130", "diaHigh": "80", "by": "Dr. Rowan Vale (fictional)", "on": "2026-06-02"}, "Saved the target for blood pressure"),
                    t_save("glucose", {"low": "80", "high": "140"}, "Saved the target for blood glucose"),
                    t_save("cgm", {"low": "70", "high": "140", "by": "Fictional diabetes clinic"}, "Saved the target for CGM"),
                    t_save("temp", {"high": "37.5"}, "Saved the target for body temperature"),
                    t_save("spo2", {"low": "95"}, "Saved the target for blood oxygen"),
                    t_save("weight", {"high": "170"}, "Saved the target for weight")]
            check("…typed in per measure by an owner or editor, each said back in words", said[0] == "Saved the target for blood pressure: up to 130/80 mmHg."
                  and said[1] == "Saved the target for blood glucose, meter readings: 80–140 mg/dL." and said[5].endswith("up to 170 lb.")
                  and js("document.activeElement.id") == "tgt-weight-save", said)
            said_ch = t_save("bp", {"sysHigh": "125"}, "Changed the target for blood pressure")
            n_problems = len(problems)
            page.fill("#tgt-glucose-save >> xpath=ancestor::form//input[@name='low']", "150")
            page.click("#tgt-glucose-save")
            page.wait_for_function("document.getElementById('targetsMsg').textContent.includes('below the high end')", timeout=10000)
            settle(200)
            del problems[n_problems:]   # the refusal's 400 was asked for
            page.click("#tgt-spo2-clear")
            asked = answer_ask(page)
            page.wait_for_function("document.getElementById('targetsMsg').textContent === 'Cleared the target for blood oxygen.'", timeout=10000)
            stored = js("fetch('api/profiles/1/person').then(r => r.json())")["targets"]
            check("…changed, refused in words (a low end above the high end), and cleared after asking; the others kept",
                  said_ch == "Changed the target for blood pressure: up to 125/80 mmHg." and asked[0] == "Clear this target?" and "at least 95%" in asked[1]
                  and sorted(stored) == ["bp", "cgm", "glucose", "temp", "weight"] and stored["glucose"]["low"] == 80
                  and js("document.activeElement.id") == "tgt-spo2-hd", [said_ch, asked, sorted(stored)])
            check("…kept in one unit each (weight in kg, as typed in lb), with who set it and when",
                  stored["weight"]["high"] == 77.11 and stored["weight"]["unit"] == "lb" and stored["bp"]["by"] == "Dr. Rowan Vale (fictional)"
                  and stored["bp"]["on"] == "2026-06-02" and stored["bp"]["sysHigh"] == 125, stored)
            t234_bad, t234_axe = [], []
            for width in (1400, 375):
                page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                for theme in ("light", "dark"):
                    js("t => { document.documentElement.dataset.theme = t; }", theme)
                    settle(200)
                    page.locator("#targets").scroll_into_view_if_needed()
                    if width == 375:
                        t234_bad += phone_fits(page, f"About this person's targets ({theme})")
                    if shots:
                        page.locator("#targets").screenshot(path=os.path.join(shots, f"234-about-{width}-{theme}.png"))
                    t234_axe += axe_check(page, f"About this person's targets, {width} px, {theme}")[0]
            js("delete document.documentElement.dataset.theme")
            page.set_viewport_size({"width": 1400, "height": 900})
            page.goto(base + "/?profile=1")
            page.wait_for_function("typeof booted !== 'undefined' && booted && PROFILE && PROFILE.id === 1", timeout=20000)
            js("state.tables.clear(); state.from = dayTs('2026-08-15'); state.to = dayTs('2026-11-15'); state.preset = 'custom'; switchTab('health')")
            settle(600)
            bp = js("""(() => { const c = document.getElementById('h-bp'), [a, b] = hRange();
              const home = H.vit.bp.home.filter(r => r.t >= a && r.t <= b), above = home.filter(r => r.v[0] > 125 || r.v[1] > 80).length;
              return {line: c.querySelector('.tgt-line').textContent, edges: c.querySelectorAll('svg .tgt-edge').length,
                labels: [...c.querySelectorAll('svg .tgt-label')].map(t => t.textContent), marks: c.querySelectorAll('svg path.abn').length,
                above, n: home.length, list: c.querySelector('.vital-list').innerText, legend: c.querySelector('.legend').innerText}; })()""")
            check("…Body and activity's blood pressure: the target as a line each, labelled, in the legend, with who set it and when",
                  bp["edges"] == 2 and bp["labels"] == ["Your systolic target", "Your diastolic target"] and "Your target, as your care team gave it" in bp["legend"]
                  and bp["line"].startswith("Your target, as your care team gave it: up to 125/80 mmHg (set by Dr. Rowan Vale (fictional), Jun 2, 2026)."), bp)
            check("…“N of M readings at home in this period above it”, as the page counts them; those marked ▲ on the chart and said in words in the list",
                  f"{bp['above']} of {bp['n']} readings at home in this period above it." in bp["line"] and bp["above"] > 0 and bp["marks"] >= bp["above"]
                  and "above your target" in bp["list"], bp)
            page.click("#h-bp .view-seg button:text-is('Table')")
            settle(300)
            bpt = js("[...document.querySelectorAll('#h-bp table.data tbody td.abn')].map(t => t.textContent)")
            check("…its table says each one in words: “▲ Above your target”, a clinic's flag still its report's",
                  any(t.endswith("▲ Above your target") for t in bpt) and any(t.endswith("▲ High") for t in bpt)
                  and js("!!document.querySelector('#h-bp .tgt-line')"), bpt[:6])
            js("state.tables.clear(); renderHealth()")
            settle(400)
            gl = js("""(() => { const c = document.getElementById('h-glucose');
              return {std: c.querySelector('.glu-tir:not(.glu-own)').innerText.replace(/\\s+/g, ' '), own: (c.querySelector('.glu-own') || {innerText: ''}).innerText.replace(/\\s+/g, ' '),
                line: c.querySelector('.tgt-line').textContent, marks: c.querySelectorAll('svg path.abn').length, edges: c.querySelectorAll('svg .tgt-edge').length,
                dist: WATCH.metrics.some(m => m.key === 'glucose_dist'), days: WATCH.metrics.filter(m => m.key.startsWith('glucose_own_')).map(m => m.pts.length)}; })()""")
            own = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)%", gl["own"])]
            check("…glucose: CGM time in the person's own range beside the standard published one, each labelled, adding up",
                  "standard published ranges" in gl["std"] and gl["own"].startswith("Time in your own range · 70–140 mg/dL, your target as your care team gave it (set by Fictional diabetes clinic)")
                  and all(w in gl["own"] for w in ("Below it:", "In it:", "Above it:", "labelled as the publisher's")) and len(own) == 3 and abs(sum(own) - 100) < 1.5, gl)
            check("…from each day's shares the server worked out: the CGM's counts by mg/dL never reach the page",
                  not gl["dist"] and gl["days"] == [14, 14, 14], gl)
            check("…the meter readings' own target as a band on the chart, readings above it marked", gl["edges"] == 2 and gl["marks"] > 0
                  and re.search(r"80–140 mg/dL\. \d+ of \d+ meter readings in this period above it", gl["line"]), gl)
            others = js("['h-body', 'h-temp'].map(id => document.querySelector('#' + id + ' .tgt-line')?.textContent || '')")
            check("…weight and body temperature each with theirs, in the units each card shows (lb; the temperature's own)",
                  others[0].startswith("Your target, as your care team gave it: up to 170 lb.") and "smart scale weights in this period" in others[0]
                  and re.match(r"Your target, as your care team gave it: up to (37\.5 °C|99\.5 °F)\.", others[1]), others)
            for width in (1400, 375):
                page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                for theme in ("light", "dark"):
                    js("t => { document.documentElement.dataset.theme = t; }", theme)
                    for table in (False, True):
                        js("on => { for (const k of ['#h-bp', '#h-glucose']) { if (on) state.tables.add(k); else state.tables.delete(k); } renderHealth(); }", table)
                        settle(400)
                        for card in ("#h-bp", "#h-glucose"):
                            page.locator(card).scroll_into_view_if_needed()
                            if width == 375:
                                t234_bad += phone_fits(page, f"{card} with targets, {'table' if table else 'chart'} ({theme})")
                            if shots and not table:
                                page.locator(card).screenshot(path=os.path.join(shots, f"234-{card[3:]}-{width}-{theme}.png"))
                        t234_axe += axe_check(page, f"Body and activity with targets, {'table' if table else 'chart'}, {width} px, {theme}")[0]
            js("delete document.documentElement.dataset.theme; state.tables.clear()")
            page.set_viewport_size({"width": 1400, "height": 900})
            js("CFG.overview.homeTiles = ['bp', 'glucose', 'temp']; switchTab('overview')")
            settle(400)
            tiles234 = js("Object.fromEntries([...document.querySelectorAll('#view-overview .home-tile')].map(t => [t.dataset.home, t.querySelector('.delta').textContent]))")
            check("…the Overview's home tiles say it in words: the latest reading at home above its target, the CGM day's time in the person's own range",
                  tiles234.get("bp", "").endswith("· systolic ▲ above your target") and re.search(r"· \d+(\.\d)?% in your own range$", tiles234.get("glucose", "")), tiles234)
            js("CFG.overview.homeTiles = []; switchTab('health')")
            page.goto(base + "/sharing.html?profile=1#summary")
            page.wait_for_function("document.getElementById('profile').value === '1' && OPTS && OPTS.profile.id === 1 && "
                                   "!document.getElementById('build').hidden", timeout=10000)
            got = dict(js("[...document.querySelectorAll('#sections input')].map(i => [i.value, i.checked])"))
            js("document.getElementById('since').selectedIndex = document.getElementById('since').options.length - 1")   # the oldest visit: the CGM's days too
            for k in got:
                (page.check if k == "targets" else page.uncheck)(f"#sections input[value={k}]")
            page.click("#previewBtn")
            page.wait_for_selector("#preview:not([hidden]) h3", timeout=10000)
            pv = js("document.getElementById('preview').innerText")
            check("…the visit summary offers them unticked; ticked, each target with who set it and the period's readings against it",
                  got.get("targets") is False and "Your targets for home readings since" in pv and "Sanguity doesn't suggest targets" in pv
                  and "up to 125/80 mmHg" in pv and "Dr. Rowan Vale (fictional)" in pv and "CGM time in your own range (70–140 mg/dL, your target)" in pv
                  and "standard published range" in pv, pv[:600])
            page.goto(base + "/sharing.html?profile=1#links")
            page.wait_for_selector("#links .share-links button:has-text('New share link')", timeout=10000)
            page.locator("#links .share-links button", has_text="New share link").click()
            page.locator("#links .share-form").wait_for(timeout=10000)
            boxes = js("Object.fromEntries([...document.querySelectorAll('#links .share-form .share-sections input')].map(i => [i.value, i.checked]))")
            check("…and a share link's form offers them, unticked", boxes.get("targets") is False, boxes)
            check("…every view of them fits 375 px, and axe-core finds nothing serious, light and dark", not t234_bad and not t234_axe, (t234_bad + t234_axe)[:6])
            cleared = js("fetch('api/profiles/1/person', {method: 'PUT', headers: {'X-Sanguity': '1', 'Content-Type': 'application/json'}, "
                         "body: JSON.stringify({targets: {bp: null, glucose: null, cgm: null, temp: null, weight: null}})}).then(r => r.json()).then(a => a.targets)")
            page.goto(base + "/?profile=1")
            page.wait_for_function("typeof booted !== 'undefined' && booted && PROFILE && PROFILE.id === 1", timeout=20000)
            js("state.from = dayTs('2026-08-15'); state.to = dayTs('2026-11-15'); state.preset = 'custom'; switchTab('health')")
            settle(500)
            check("…cleared, nothing of them is left drawn or said", cleared == {} and js(no_marks) == 0
                  and not js("WATCH.metrics.some(m => m.key.startsWith('glucose_own_') || m.key === 'glucose_dist')"), cleared)
            js("v => v === null ? localStorage.removeItem('ht-profile') : localStorage.setItem('ht-profile', v)", was_profile)
            # ---- the profile picker in the top bar (#170): on every page about a profile, each profile by name and
            # your role; the page follows it and keeps its section; the address's ?profile= and this browser follow it
            was_profile = js("localStorage.getItem('ht-profile')")
            both = ["My records (owner)", "Second person (owner)"]
            for path, ready in (("/", "typeof booted !== 'undefined' && booted"), ("/sources.html#portals", "!document.getElementById('intro').hidden"),
                                ("/sharing.html#people", "!!document.querySelector('#memberList tbody tr')"),
                                ("/settings.html#display", "!!document.querySelector('#sections > section')")):
                page.goto(base + path)
                page.wait_for_function(ready, timeout=20000)
                check(f"{path}: the profile picker is in the top bar, each profile by name and role, and the page has no Profile choice of its own",
                      js("document.querySelectorAll('#profile').length === 1 && !!document.querySelector('header.topbar #profilePick > select#profile') "
                         "&& !document.querySelector('main #profile, main label.set-profile')")
                      and page.get_by_role("combobox", name="Profile").is_visible()
                      and js("[...document.getElementById('profile').options].map(o => o.textContent)") == both,
                      js("[...document.querySelectorAll('#profile option')].map(o => o.textContent)"))
            page.focus("header .brand-link")
            page.keyboard.press("Tab")
            check("…reached from the keyboard right after the brand and the page's title", js("document.activeElement.id") == "profile", js("document.activeElement.id"))
            for path in ("/account.html", "/admin.html"):
                page.goto(base + path)
                page.wait_for_load_state("networkidle")
                check(f"{path}: no profile picker, the page isn't about a profile", js("!document.getElementById('profile') && !document.getElementById('profilePick')"))
            page.goto(base + "/sources.html?profile=2#portals")
            page.wait_for_function("!document.getElementById('intro').hidden && document.getElementById('introHint').textContent.includes('Second person')", timeout=10000)
            check("an address naming a profile (?profile=2) opens it, and this browser remembers it",
                  js("document.getElementById('profile').value") == "2" and js("localStorage.getItem('ht-profile')") == "2")
            page.select_option("#profile", "1")
            page.wait_for_function("document.getElementById('introHint').textContent.includes('My records')", timeout=10000)
            check("switching profile redraws the page for it, in the same section, and the address and this browser follow",
                  js("[location.search, location.hash, document.querySelector('#srcNav [aria-current=page]').textContent, localStorage.getItem('ht-profile')]")
                  == ["?profile=1", "#portals", "Portals and health plans", "1"],
                  js("[location.search, location.hash, localStorage.getItem('ht-profile')]"))
            page.goto(base + "/settings.html#display")
            page.wait_for_selector("#sections > section", state="attached", timeout=10000)
            page.select_option("#profile", "2")
            page.wait_for_function("PID === 2 && !!document.querySelector('#sections > section')", timeout=10000)
            check("…on Settings too, keeping its group", js("location.hash") == "#display" and js("localStorage.getItem('ht-profile')") == "2")
            page.goto(base + "/")
            page.wait_for_function("typeof booted !== 'undefined' && booted || !!document.querySelector('main .empty')", timeout=20000)
            check("…and the records page opens the profile chosen there", js("PROFILE.id") == 2 and js("document.getElementById('profile').value") == "2")
            page.select_option("#profile", "1")   # the records page loads again for it
            page.wait_for_function("typeof booted !== 'undefined' && booted && PROFILE.id === 1", timeout=20000)
            check("…switching there opens the other", js("document.getElementById('profile').value") == "1" and js("localStorage.getItem('ht-profile')") == "1")
            page.set_viewport_size({"width": 375, "height": 800})
            box = "(s => { const r = document.querySelector(s).getBoundingClientRect(); return [r.top, r.bottom, r.left, r.right]; })"
            check("at 375 px the records page fits, the picker a row of its own between the menus and the tabs",
                  js("document.documentElement.scrollWidth") <= 375 and js(box + "('#addMenu')")[1] <= js(box + "('#profilePick')")[0]
                  and js(box + "('#profilePick')")[1] <= js(box + "('header .tabs')")[0] + 1 and js(box + "('#profilePick')")[3] <= 375,
                  [js("document.documentElement.scrollWidth"), js(box + "('#addMenu')"), js(box + "('#profilePick')"), js(box + "('header .tabs')")])
            for path, ready in (("/sources.html#portals", "!document.getElementById('intro').hidden"), ("/sharing.html#people", "!!document.querySelector('#memberList tbody tr')"),
                                ("/settings.html#display", "!!document.querySelector('#sections > section')")):
                page.goto(base + path)
                page.wait_for_function(ready, timeout=20000)
                check(f"…{path} fits too, the picker beside the page's title under the menus (#315), whole",
                      js("document.documentElement.scrollWidth") <= 375 and page.is_visible("#profile")
                      and js(box + "('#addMenu')")[1] <= min(js(box + "('.page-title')")[0], js(box + "('#profilePick')")[0])
                      and js(box + "('.page-title')")[3] <= js(box + "('#profilePick')")[2] and js(box + "('#profilePick')")[3] <= 375
                      and js(box + "('#profilePick')")[1] <= js(box + "('header.topbar')")[1],
                      [js("document.documentElement.scrollWidth"), js(box + "('#addMenu')"), js(box + "('.page-title')"), js(box + "('#profilePick')")])
            # #315: at 320 px the title beside the brand and the menus had 23 px, "S." for Settings; on its own row under them
            # (with the picker, where there is one), each page's title is whole, on the screen and clear of the menus
            title_whole = """(() => { const t = document.querySelector('header .page-title'), r = t.getBoundingClientRect(),
                menus = [...document.querySelectorAll('header .menu')].filter(m => m.checkVisibility()).map(m => m.getBoundingClientRect());
              return [t.textContent, t.checkVisibility() && t.scrollWidth <= t.clientWidth && t.scrollHeight <= t.clientHeight + 1 && r.left >= 0 && r.right <= 320
                && menus.every(m => r.top >= m.bottom || r.bottom <= m.top || r.right <= m.left || r.left >= m.right),
                [t.scrollWidth, t.clientWidth, t.scrollHeight, t.clientHeight, Math.round(r.left), Math.round(r.right)]]; })()"""
            page.set_viewport_size({"width": 320, "height": 800})
            titles = {}
            for path in ("sources.html", "sharing.html", "settings.html", "account.html", "admin.html", "guide.html"):
                page.goto(base + "/" + path)
                page.wait_for_selector("header .page-title", timeout=10000)
                settle(300)
                titles[path] = js(title_whole)
            check("at 320 px every page's title in the top bar is whole, on a row of its own under the menus (#315)",
                  all(ok and name in ("Sources", "Sharing", "Settings", "Account", "Admin", "Getting started") for name, ok, _ in titles.values()), titles)
            page.set_viewport_size({"width": 1400, "height": 900})
            js("v => v === null ? localStorage.removeItem('ht-profile') : localStorage.setItem('ht-profile', v)", was_profile)
            # ---- milestones (#39) and visits counted as workouts (#43) on the Settings page: a milestone added (primary)
            # shows as a marker on Trends and a "since" period on Health; a rule (a bad regular expression refused
            # first) makes the Health tab count the matching visits; both sections reset
            page.goto(base + "/")   # from the main page, after the visit summary checks above
            page.wait_for_function("typeof booted !== 'undefined' && booted && PROFILE.name === 'My records'", timeout=20000)
            check("the demo's milestones moved into the settings", js("MILESTONES.length") > 0, js("MILESTONES"))
            visit = js("(() => { const n = {}; for (const e of DATA.events) if (e.k === 'visit' && e.ts < Date.now()) n[e.title] = (n[e.title] || 0) + 1;"
                       " return Object.entries(n).sort((a, b) => b[1] - a[1])[0][0]; })()")
            want = visit.split(" ")[0].lower()
            days = js(f"new Set(DATA.events.filter(e => e.k === 'visit' && e.ts < Date.now() && e.title.replace(/\\s+/g, ' ').trim().slice(0, 200).toLowerCase().includes({json.dumps(want)})).map(e => isoDay(e.ts))).size")
            page.goto(base + "/settings.html?profile=1#sec-milestones")
            page.wait_for_selector("#sec-milestones [data-key=milestones] .set-row:not(.set-head)", timeout=10000)
            check("Settings shows the Milestones section", "Milestones" in js("[...document.querySelectorAll('#sections > section h2')].map(h => h.textContent)"))
            ms = "[data-key=milestones]"
            page.click(f"{ms} button:text-is('Add a row')")
            page.fill(f"{ms} .set-row:last-child input[data-k=date]", "2026-08-01")
            page.fill(f"{ms} .set-row:last-child input[data-k=label]", "Browser check milestone")
            page.check(f"{ms} .set-row:last-child input[data-k=primary]")
            check("…one primary at a time", js(f"document.querySelectorAll('{ms} input[data-k=primary]:checked').length") == 1)
            page.click("#sec-milestones .set-foot button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-milestones .set-foot > .msg').textContent.startsWith('Saved')", timeout=10000)
            rules = "[data-key=visitWorkouts]"
            page.click(f"{rules} button:text-is('Add a row')")
            page.fill(f"{rules} .set-row:last-child input[data-k=match]", "(a+)+$")
            page.fill(f"{rules} .set-row:last-child input[data-k=name]", "Browser check rule")
            page.fill(f"{rules} .set-row:last-child input[data-k=minutes]", "40")
            page.check(f"{rules} .set-row:last-child input[data-k=regex]")
            n0 = len(problems)
            page.click("#sec-health .set-foot button:text-is('Save')")
            page.wait_for_function(f"document.querySelector('{rules} .msg').textContent.includes('very slow')", timeout=10000)
            settle(200)
            refused = [p for p in problems[n0:] if ("HTTP 400" in p and p.endswith("/settings/visitWorkouts")) or "status of 400" in p]
            problems[n0:] = [p for p in problems[n0:] if p not in refused]   # this refusal is the point, not a fault
            check("a catastrophic regular expression is refused before saving",
                  "very slow" in js(f"document.querySelector('{rules} .msg').textContent") and len(refused) == 2, refused)
            page.fill(f"{rules} .set-row:last-child input[data-k=match]", want)
            page.uncheck(f"{rules} .set-row:last-child input[data-k=regex]")
            page.click("#sec-health .set-foot button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-health .set-foot > .msg').textContent.startsWith('Saved')", timeout=10000)
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the milestones and workout rules fit 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records'", timeout=20000)
            page.click("#tab-labs")
            js("applyPreset('all')")
            settle()
            check("…the new milestone is marked on Trends",
                  js("[...document.querySelectorAll('#charts .mslabel')].some(t => t.textContent.includes('Browser check milestone'))"))
            page.click("#tab-health")
            settle()
            page.click("#presets button:text-is('Since browser check milestone')")
            settle()
            check("…and is a “since” period in the records bar, on Body and activity", js("isoDay(hRange()[0])") == "2026-08-01",
                  js("[state.preset, new Date(hRange()[0]).toISOString()]"))
            check("…the primary one, the Overview counts from", js("primaryMs().label") == "Browser check milestone")
            page.click("#presets button:text-is('All')")
            settle()
            counted = js("H.ex.filter(x => x.pt && x.rule === 'Browser check rule').length")
            check("the Health tab counts visits matching the rule", counted == days and days > 0, f"{counted} of {days} days ({want})")
            check("…and says so", f"{days} browser check rule appointment" in js("document.getElementById('view-health').innerText"))
            page.goto(base + "/settings.html?profile=1#sec-milestones")
            page.wait_for_selector("#sec-milestones", timeout=10000)
            for sec in ("milestones", "health"):
                page.click(f"#sec-{sec} button:text-is('Reset to defaults')")
                answer_ask(page)
                page.wait_for_function(f"document.querySelector('#sec-{sec} .set-foot > .msg').textContent.includes('back to their defaults')", timeout=10000)
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records'", timeout=20000)
            check("…both reset", js("MILESTONES.length === 0 && !CFG.visitWorkouts.length"), js("[MILESTONES, CFG.visitWorkouts]"))
            # ---- each person's own time zone and date format (#42): two accounts on one install, their browsers both
            # in Chicago, each set from the browser on first sign-in; then one chooses Los Angeles on the Settings page
            # and the other Auckland, and each sees days split at their own midnight
            def sign_in(ctx, user, pw):
                pg = ctx.new_page()
                pg.on("pageerror", lambda e: problems.append(f"script error ({user}): {e}"))
                pg.goto(base + "/login.html")
                pg.fill("#u", user)
                pg.fill("#p", pw)
                pg.click("#b")
                pg.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE && PROFILE.id === 1", timeout=20000)
                return pg
            from server import db as dbm, security   # a second account only for this (with two, an admin needs a second factor)
            sdb = dbm.connect()
            uid2 = sdb.execute("INSERT INTO users(username, pw_hash, is_admin, created) VALUES(?, ?, 0, 0)", (USER2, security.hash_password(PW2))).lastrowid
            sdb.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, 1, 'viewer')", (uid2,))
            zctx = {u: browser.new_context(viewport={"width": 1400, "height": 900}, bypass_csp=True, timezone_id="America/Chicago")
                    for u in (USER, USER2)}
            za, zb = sign_in(zctx[USER], USER, PW), sign_in(zctx[USER2], USER2, PW2)
            mine = lambda pg: pg.evaluate("fetch('api/settings/user').then(r => r.json())")
            check("time zone and date format set from the browser on first sign-in",
                  mine(zb)["dates.timeZone"] == "America/Chicago" and mine(zb)["dates.format"] == "mdy", mine(zb))
            before = mine(za)   # the demo account's, put back afterwards for the checks below
            za.goto(base + "/settings.html?profile=1#sec-dates")
            za.wait_for_selector("#sec-dates select", timeout=10000)
            za.select_option("[data-key='dates.timeZone'] select", "America/Los_Angeles")
            za.select_option("[data-key='dates.format'] select", "dmy")
            za.click("#sec-dates .set-foot button:text-is('Save')")   # one Save for the section's two changes (#174)
            za.wait_for_function("document.querySelector('#sec-dates .set-foot > .msg').textContent == 'Saved “Time zone” and “Date format”.'", timeout=10000)
            check("…changed on the Settings page, just for that person", mine(za)["dates.timeZone"] == "America/Los_Angeles"
                  and mine(zb)["dates.timeZone"] == "America/Chicago", (mine(za), mine(zb)))
            zb.goto(base + "/settings.html?profile=1#dashboard")   # #174: a viewer of the profile
            zb.wait_for_selector("#sec-dates .set-foot", state="attached", timeout=10000)
            check("a viewer's Settings: the profile's groups say they're shown as they are, with no Save, while their own settings have one",
                  zb.evaluate("document.getElementById('introHint').textContent").startswith("This profile: you can view it")
                  and zb.evaluate("!document.querySelector('#sec-overview .set-foot, #sec-names .set-foot') && !!document.querySelector('#sec-units .set-foot')"),
                  zb.evaluate("document.getElementById('introHint').textContent"))
            # #169: with no profile at all, Settings still has their own Display settings, says how to get a profile,
            # and saves; an old address opened by a link within the page goes on to its new home too
            sdb.execute("DELETE FROM memberships WHERE user_id=? AND profile_id=1", (uid2,))
            zb.goto(base + "/settings.html")
            zb.wait_for_selector("#sec-dates .set-foot", state="attached", timeout=10000)
            check("no profile: Settings shows Display (their own), says how to get a profile, and no profile choice",
                  zb.evaluate("[...document.querySelectorAll('#setNav a')].map(a => a.dataset.group).join()") == "display"
                  and zb.evaluate("[...document.querySelectorAll('#sections > section')].map(s => s.id).join()") == "sec-appearance,sec-dates,sec-units"
                  and zb.is_visible("#noProfiles") and zb.locator("#noProfiles a[href='sharing.html#profile']").count() == 1
                  and zb.is_hidden("#profile") and zb.evaluate("document.getElementById('groupTitle').textContent") == "Display",
                  zb.evaluate("[...document.querySelectorAll('#sections > section')].map(s => s.id).join()"))
            zb.select_option("[data-key='dates.format'] select", "dmy")
            zb.click("#sec-dates .set-foot button:text-is('Save')")
            zb.wait_for_function("document.querySelector('#sec-dates .set-foot > .msg').textContent == 'Saved “Date format”.'", timeout=10000)
            check("…and saves them", mine(zb)["dates.format"] == "dmy", mine(zb))
            zb.evaluate("location.hash = '#watch'")
            zb.wait_for_url("**/sources.html#phone", timeout=10000)
            check("…an old Settings address followed within the page opens its new home (#watch → Sources)", True)
            sdb.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, 1, 'viewer')", (uid2,))
            zb.evaluate("""Promise.all([['dates.timeZone', 'Pacific/Auckland'], ['dates.format', 'ymd']].map(([k, v]) =>
                api('settings/user/' + k, {method: 'PUT', body: JSON.stringify({value: v})})))""")
            for pg in (za, zb):
                pg.goto(base + "/")
                pg.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.id === 1", timeout=20000)
            late = "Date.parse('2026-03-10T23:30:00Z')"   # 4:30 pm in Los Angeles, 12:30 pm the next day in Auckland
            # the timeline's day ticks at each person's midnight: 07:00 UTC in Los Angeles (PDT), 11:00 UTC in Auckland (NZDT)
            first_tick = "new Date(ticks(Date.parse('2026-03-09T12:00:00Z'), Date.parse('2026-03-13T12:00:00Z'), 1000)[0][0]).toISOString()"
            got = {u: (pg.evaluate(f"isoDay({late})"), pg.evaluate(first_tick), pg.evaluate(f"fmtD({late})"))
                   for u, pg in ((USER, za), (USER2, zb))}
            check("two people in different time zones each see days split at their own midnight",
                  got[USER][:2] == ("2026-03-10", "2026-03-10T07:00:00.000Z") and got[USER2][:2] == ("2026-03-11", "2026-03-10T11:00:00.000Z"), got)
            check("…and dates in their own format", got[USER][2] == "10 Mar 2026" and got[USER2][2] == "2026-03-11", got)
            # #248: a result's Collected and Resulted, a claim's services and the Costs claims table in that format too,
            # not as the record's ISO text; and the drawer's buttons name the Labs tab by its name
            dmy = r"\d{1,2} [A-Z][a-z]{2} \d{4}"
            kv_js = "Object.fromEntries([...document.querySelectorAll('#dbody dl.kv dt')].map(dt => [dt.textContent, dt.nextElementSibling.textContent]))"
            btns_js = "[...document.querySelectorAll('#dbody button.btn')].map(b => b.textContent)"
            za.evaluate("Promise.resolve(openEvent(EV.find(e => e.k === 'lab' && !e.hidden && DATA.details[e.id]?.type === 'labday')))")
            za.wait_for_selector("#dbody details.res dl.kv", timeout=5000)
            lab_kv, lab_btns = za.evaluate(kv_js), za.evaluate(btns_js)
            za.evaluate("closeDrawer(); Promise.resolve(openEvent(EV.find(e => e.k === 'claim' && !e.hidden)))")
            za.wait_for_selector("#dbody table.data tbody td", timeout=5000)
            line_day = za.evaluate("document.querySelector('#dbody table.data tbody td').textContent")
            za.evaluate("closeDrawer(); Promise.resolve(openEvent(EV.find(e => e.k === 'stay' && !e.hidden)))")
            za.wait_for_selector("#dbody dl.kv", timeout=5000)
            stay_btns = za.evaluate(btns_js)
            za.evaluate("closeDrawer(); applyPreset('all'); switchTab('costs')")
            za.wait_for_selector("#costTable tbody tr td", timeout=10000)
            claim_days = za.evaluate("[...document.querySelectorAll('#costTable tbody tr')].slice(0, 5).map(r => r.cells[0].textContent)")
            check("…a result's Collected and Resulted, a claim's services and the Costs claims table too (#248)",
                  re.fullmatch(dmy + r", \d{1,2}:\d{2}.*", lab_kv.get("Collected", "")) and re.fullmatch(dmy, lab_kv.get("Resulted", ""))
                  and re.fullmatch(dmy, line_day) and claim_days and all(re.fullmatch(dmy, d) for d in claim_days), (lab_kv, line_day, claim_days))
            check("…and the drawer's buttons say Labs, the tab's name, not Lab trends (#248)",
                  "Show this day in Labs" in lab_btns and "Labs during this stay" in stay_btns
                  and not any("trends" in b.lower() for b in lab_btns + stay_btns), (lab_btns, stay_btns))
            # #311: the provider directory's and the provider finder's "read" dates in that format too, and no page,
            # tab or group shows a date as it's stored ("2026-11-15"; the Date format choice's own example aside)
            iso_js = r"""(() => { const out = [], w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
              for (let n; (n = w.nextNode());) { const p = n.parentElement;
                if (p && !p.closest('select, script, style') && p.checkVisibility() && /\b\d{4}-\d{2}-\d{2}\b/.test(n.data)) out.push(n.data.trim().slice(0, 120)); }
              return out; })()"""
            iso_seen = {}
            def iso_scan(where):
                za.wait_for_timeout(300)
                if found := za.evaluate(iso_js):
                    iso_seen[where] = found
            for tab in za.evaluate("[...document.querySelectorAll('[role=tablist] [role=tab][id^=\"tab-\"]')].filter(t => !t.hidden).map(t => t.id.slice(4))"):
                za.evaluate(f"switchTab('{tab}')")
                iso_scan("records: " + tab)
            for path, nav in (("sources.html?profile=1", "#srcNav"), ("sharing.html?profile=1", "#shrNav"), ("settings.html?profile=1", "#setNav"),
                              ("account.html", "#acctNav"), ("admin.html", "#admNav"), ("guide.html?profile=1", None), ("providers.html?q=medicare", None)):
                za.goto(base + "/" + path)
                za.wait_for_load_state("networkidle")
                if nav:
                    za.wait_for_selector(f"{nav} a[data-group]", timeout=10000)
                for g in za.evaluate(f"[...document.querySelectorAll('{nav} a[data-group]')].map(a => a.dataset.group)") if nav else [None]:
                    if g:
                        za.click(f"{nav} a[data-group='{g}']")
                    iso_scan(path + (f"#{g}" if g else ""))
            za.click(".dir-hit")   # the finder: a provider's details
            za.wait_for_selector("#detail .dir-from", timeout=10000)
            finder_read = za.inner_text("#detail .dir-from")
            iso_scan("the provider finder's details")
            za.goto(base + "/admin.html#directory")
            za.wait_for_selector("#dirSources li", timeout=10000)
            dir_read = [li for li in za.inner_text("#dirSources").splitlines() if ", read " in li]
            check("…the provider directory and the provider finder's “read” dates too (#311)",
                  dir_read and all(re.search(r", read " + dmy + r"\.", li) for li in dir_read) and re.search(r", read " + dmy + r"\.$", finder_read),
                  (dir_read, finder_read))
            check("…and no page, tab or group shows a date as it's stored (#311)", not iso_seen, iso_seen)
            # #317: nor Import history's span of an import's dates: the second profile's uploads (its FHIR export's, ...)
            za.goto(base + "/sources.html?profile=2#history")
            za.wait_for_selector("#history li[data-format='fhir'] .what", timeout=10000)
            spans = za.evaluate("[...document.querySelectorAll('#history li[data-import] .what')].map(p => p.textContent)")
            check("…nor Import history's span of an import's dates (#317)",
                  any(re.search(r"\(" + dmy + " to " + dmy + r"\)$", t) for t in spans) and not any(re.search(r"\d{4}-\d{2}-\d{2}", t) for t in spans), spans)
            za.evaluate("localStorage.setItem('ht-profile', '1')")   # the profile this browser opens next, as before
            za.goto(base + "/")
            za.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.id === 1", timeout=20000)
            za.evaluate("switchTab('overview')")
            # Health's daily charts and workout calendar, the drawer and Costs by month bin with the same days
            weekly_key = "new Date(weekly([[Date.parse('2026-03-08T20:00:00Z'), 1]])[0][0] - 3 * DAY).toISOString()"
            check("…Health's weekly bins start at their own Monday",
                  (za.evaluate(weekly_key), zb.evaluate(weekly_key)) == ("2026-03-02T20:00:00.000Z", "2026-03-08T23:00:00.000Z"),
                  (za.evaluate(weekly_key), zb.evaluate(weekly_key)))
            zb.click("#tab-timeline")
            zb.wait_for_selector("#list .tl-row .d", timeout=10000)
            shown = zb.evaluate("document.querySelector('#list .tl-row .d').textContent")
            check("…the timeline's event list in that format", bool(__import__("re").fullmatch(r"\d{4}-\d{2}-\d{2}", shown)), shown)
            check("a viewer gets no Journal entry under Add, and no journal card while no one has written in it (#173)",
                  zb.evaluate("document.getElementById('journalBtn').hidden && !document.getElementById('journalHome')"))
            check("…and the header", " built 20" in zb.evaluate("document.getElementById('meta').textContent"), zb.evaluate("document.getElementById('meta').textContent"))
            check("…a journal entry's time is theirs too: 11:30 pm in Auckland, not in this browser's Chicago",
                  zb.evaluate("jTime('2026-03-10T23:30') === Date.parse('2026-03-10T10:30:00Z') && isoDay(jTime('2026-03-10')) === '2026-03-10'"),
                  zb.evaluate("new Date(jTime('2026-03-10T23:30')).toISOString()"))
            zb.goto(base + "/sharing.html?profile=1#summary")
            zb.wait_for_selector("#build:not([hidden]) #since option", state="attached", timeout=10000)
            since_opt = zb.evaluate("document.querySelector('#since option').textContent")
            check("…the visit summary writes dates in their format", bool(__import__("re").search(r"\(.*, \d{4}-\d{2}-\d{2}\)", since_opt)), since_opt)
            # a viewer's Sharing page (#168): who has access, the visit summary and their own calendar feed, and no
            # owner's controls; an address for an owner's group shows People instead, saying why
            zb.wait_for_selector("#memberList tbody tr", state="attached", timeout=10000)
            groups = zb.evaluate("[...document.querySelectorAll('#shrNav a')].map(a => a.textContent)")
            check("a viewer's Sharing page: People, Calendar feed, Visit summary, About this person (#194) and Profile only",
                  groups == ["People", "Calendar feed", "Visit summary", "About this person", "Profile"], groups)
            zb.click("#shrNav a[data-group=people]")
            people = zb.inner_text("#people")
            check("…who has access and as what, with Leave for themselves and no role menus, Remove or Share",
                  USER in people and "owner" in people and USER2 in people and zb.evaluate("!document.querySelector('#people select, #people form')")
                  and zb.evaluate("[...document.querySelectorAll('#people button')].map(b => b.textContent).join()") == "Leave"
                  and f"ask {USER}" in people, people[:300])
            zb.click("#shrNav a[data-group=calendar]")
            zb.wait_for_selector("#calendar:not([hidden]) #calNew:not([hidden])", timeout=10000)
            check("…their own calendar feed can be made, with no list of everyone's", zb.evaluate("document.getElementById('calOthers').hidden"))
            zb.click("#shrNav a[data-group=profile]")
            check("…Profile: no Rename or Delete, only New profile", zb.evaluate("!document.querySelector('#profileCard button, #profileCard input')")
                  and zb.is_visible("#profNew"), zb.inner_text("#profileCard"))
            zb.goto(base + "/sharing.html?profile=1#links")
            zb.wait_for_selector("#memberList tbody tr", timeout=10000)
            check("…an owner's group in the address shows People, saying share links are for owners",
                  zb.evaluate("document.querySelector('#shrNav [aria-current=page]').textContent") == "People" and "owners" in zb.inner_text("#pageMsg")
                  and zb.evaluate("location.hash") == "#people" and zb.evaluate("document.getElementById('links').hidden && document.getElementById('linksBody').children.length === 0"))
            check("…with one profile, the top bar names it and their role, as a plain label (#170)",
                  zb.evaluate("document.getElementById('profile').hidden && !document.getElementById('profileOne').hidden")
                  and zb.evaluate("document.getElementById('profileOne').textContent") == "Profile: My records view only" and zb.is_visible("#profileOne .profile-role"),
                  zb.evaluate("document.getElementById('profileOne').textContent"))
            zb.goto(base + "/import.html")
            zb.wait_for_selector("#importer", state="attached", timeout=10000)
            zb.evaluate("datesReady().then(() => true)")   # the zone read from the server, not only from the last visit
            check("…the import pages read them too", zb.evaluate("DATES.zone") == "Pacific/Auckland" and zb.evaluate("zToday()") == zb.evaluate(
                "new Intl.DateTimeFormat('en-CA', {timeZone: 'Pacific/Auckland'}).format(new Date())"), zb.evaluate("DATES.zone"))
            # Sources for someone who can only view the profile (#166): what reaches it and its history, nothing to add
            zb.wait_for_selector("#viewOnly", state="visible", timeout=10000)
            shown_src = "['importer', 'viewOnly', 'watch'].filter(id => getComputedStyle(document.getElementById(id)).display !== 'none')"
            check("Sources, view only: a note in place of Upload files", zb.evaluate(shown_src) == ["viewOnly"]
                  and "you can view this profile" in zb.inner_text("#introHint"), (zb.evaluate(shown_src), zb.inner_text("#introHint")))
            zb.click("#srcNav a[data-group=phone]")
            check("…and of Phone and watch, which only owners and editors set up", zb.evaluate(shown_src) == ["viewOnly"], zb.evaluate(shown_src))
            zb.click("#srcNav a[data-group=overview]")
            zb.wait_for_selector("#srcList h3", timeout=10000)
            ov = zb.inner_text("#srcOverview")
            check("…the Overview lists what reached the profile, with no phone and nothing to add",
                  "Setup build" in ov and "Phone app" not in ov and "Connect a portal" not in ov, ov[:300])
            zb.click("#srcNav a[data-group=history]")
            zb.wait_for_selector("#historyList li[data-import]", timeout=10000)
            check("…and the import history, with no Undo", zb.evaluate("location.hash") == "#history"
                  and zb.evaluate("!document.querySelector('#historyList button.undo')"))
            # someone who isn't an admin (#167): no Admin in their menu, the Admin page (and its old addresses) says it isn't
            # for them, and the everyday pages name no server setting, file or command, only what they can do
            sdb.execute("UPDATE memberships SET role='editor' WHERE user_id=? AND profile_id=1", (uid2,))   # sees the watch card
            jargon = re.compile(r"PUBLIC_ORIGINS|WATCH_DIR|WATCH_UPLOAD_URL|watch_secret|build_directory|connected-sources-oauth|sources\.json|\.env\b|docker compose")
            zb.goto(base + "/account.html#admin")
            zb.wait_for_url("**/admin.html", timeout=10000)
            zb.wait_for_selector("#notAdmin:not([hidden])", timeout=10000)
            shown_parts = "[...document.querySelectorAll('main > *')].filter(c => !c.hidden).map(c => c.id).join()"
            check("Admin: its old address opens it, and someone who isn't an admin gets only a page saying it isn't for them",
                  zb.evaluate(shown_parts) == "notAdmin" and not zb.evaluate("!!document.getElementById('admNav')"), zb.evaluate(shown_parts))
            zb.wait_for_load_state("networkidle")
            check("…and their menu has no Admin item", zb.evaluate("document.getElementById('adminLink').hidden"))
            said = {}
            for path, ready in (("settings.html?profile=1#watch", "#watch #watchRotate"), ("connect.html", "#noSources:not([hidden])"),
                                ("providers.html", "#srcNote:not([hidden])")):
                zb.goto(base + "/" + path)
                zb.wait_for_selector(ready, timeout=10000)
                said[path] = zb.inner_text("main")
            check("…the everyday pages name no server setting, file or command for them", not any(jargon.search(t) for t in said.values()),
                  {k: jargon.findall(t) for k, t in said.items()})
            check("…and say to ask their admin where something isn't set up",
                  "ask them" in said["connect.html"] and "Ask your admin" in said["providers.html"] and "original receiver secret" in said["settings.html?profile=1#watch"],
                  {k: t[:200] for k, t in said.items()})
            # ---- the getting-started guide by role (#199): someone given another person's empty profile gets none of
            # its owner's steps. As a viewer, the records page says its owners and editors bring records in and links
            # to who has access, and the guide has no steps; as an editor, Get started opens the guide at the first
            # records, with no naming the profile. Neither is sent to the guide by itself.
            from server import profiles as sprof
            pid3 = sprof.create_profile(sdb, "Shared empty profile", owner_id=sdb.execute("SELECT id FROM users WHERE username=?", (USER,)).fetchone()[0])
            sdb.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (uid2, pid3))
            zb.goto(base + f"/?profile={pid3}")
            zb.wait_for_selector("main .empty-state", timeout=10000)
            nexts = "[...document.querySelectorAll('main .empty-next')].map(a => [a.textContent, a.getAttribute('href')])"
            check("guide by role: a viewer of an empty profile is told who brings records in, with one link to who has access",
                  zb.evaluate("location.pathname") == "/" and "its owners and editors bring them in" in zb.inner_text("main")
                  and zb.evaluate(nexts) == [["See who has access", f"sharing.html?profile={pid3}#people"]], zb.evaluate(nexts))
            zb.goto(base + f"/guide.html?profile={pid3}")
            zb.wait_for_selector("#step-view:not([hidden])", timeout=10000)
            check("…and the guide gives them no steps, only the Overview", zb.evaluate(
                  "[...document.querySelectorAll('.guide-step')].filter(s => !s.hidden).map(s => s.id).join() + '|' + document.getElementById('guideSteps').hidden + '|' + document.getElementById('guideSkip').hidden")
                  == "step-view|true|true" and "owners and editors" in zb.inner_text("#step-view"), zb.inner_text("main")[:300])
            sdb.execute("UPDATE memberships SET role='editor' WHERE user_id=? AND profile_id=?", (uid2, pid3))
            zb.goto(base + f"/?profile={pid3}")
            zb.wait_for_selector("main .empty-state", timeout=10000)
            check("…an editor gets Get started", zb.evaluate(nexts) == [["Get started", f"guide.html?profile={pid3}"]], zb.evaluate(nexts))
            zb.click("main .empty-next")
            zb.wait_for_selector("#step-records:not([hidden])", timeout=10000)
            check("…which opens the guide at the first records, with no step naming the profile (its owners' to do)",
                  zb.evaluate("[...document.querySelectorAll('#guideSteps li')].map(li => li.textContent.replace(/^\\d/, ''))") == ["First records", "Phone (optional)", "Overview"]
                  and zb.evaluate("location.hash") == "#records", zb.evaluate("[...document.querySelectorAll('#guideSteps li')].map(li => li.textContent)"))
            sprof.delete_profile(sdb, pid3)
            za.evaluate("""v => Promise.all(Object.entries(v).map(([k, x]) =>
                api('settings/user/' + k, {method: 'PUT', body: JSON.stringify({value: x})})))""", before)
            for c in zctx.values():
                c.close()
            sdb.execute("DELETE FROM users WHERE id=?", (uid2,))
            sdb.close()

            # ---- a watch receiver secret per profile (#46): made on Sources → Phone and watch (#166), shown once, then masked
            page.goto(base + "/sources.html?profile=1#phone")
            page.wait_for_selector("#watch #watchRotate", timeout=10000)
            check("watch card: the server's original secret is still this profile's", "original receiver secret" in page.inner_text("#watch"),
                  page.inner_text("#watch")[:300])
            check("…saying when the phone last sent something", page.inner_text("#watchLast") != "", page.inner_text("#watchLast"))
            page.click("#watchRotate")
            answer_ask(page)
            page.wait_for_selector("#watchSecret", timeout=10000)
            made = js("document.getElementById('watchSecret').textContent")
            check("…makes a secret and shows it once, with a copy button", len(made) >= 40 and js("!!document.querySelector('#watch button[aria-label=\"Copy the secret\"]')"), made)
            check("…with no stray “null” in the card (#175)", not re.search(r"\bnull\b", page.inner_text("#watch")), page.inner_text("#watch")[:300])
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("…and the card fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            # iPhone (#148): Health Auto Export's setup steps beside Life Dashboard's: address, header, data, how often
            hae_txt = page.inner_text("#watchHae")
            check("watch card: Health Auto Export's steps (address, header, data, how often, workouts)",
                  all(w in hae_txt for w in ("Health Auto Export", "REST API", ":8731/ingest", "Authorization", "Bearer " + made,
                                             "heart rate variability", "sleep analysis", "Since last sync", "Workouts"))
                  and "Life Dashboard Companion" in page.inner_text("#watch") and len(js("[...document.querySelectorAll('#watchHae li')]")) >= 6,
                  hae_txt[:400])
            check("…and one for blood glucose, each reading on its own (#181)", "only blood glucose and no time grouping" in hae_txt, hae_txt[-300:])
            check("…with the header value to copy while the secret is shown",
                  js("!!document.querySelector('#watchHae button[aria-label=\"Copy the header value\"]')"))
            check("…and the steps fit 375 px", js("(() => { const b = document.getElementById('watchHae').getBoundingClientRect(); "
                                                  "return b.width > 0 && b.right <= 375 && [...document.querySelectorAll('#watchHae code, #watchHae li')]"
                                                  ".every(e => e.getBoundingClientRect().right <= 375.5); })()"),
                  js("document.getElementById('watchHae').getBoundingClientRect().right"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.reload()
            page.wait_for_selector("#watch .w-secret", timeout=10000)
            masked = js("document.querySelector('#watch .w-secret').textContent")
            check("…then only its last characters", masked.endswith(made[-4:]) and made not in page.content(), masked)
            check("…and the iPhone steps name the secret without showing it",
                  "Bearer <the secret>" in page.inner_text("#watchHae") and not js("!!document.querySelector('#watchHae button')"))
            # ---- reference ranges from settings (#44): a profile whose results come without a range; its own ranges,
            # set on the Settings page, colour those results on Trends, the heatmap, the Overview and the visit
            # summary, labelled "range from settings", while a report's own range and flag stay; the page's rules
            # (ranges.js) give the same answers as the server's (server/ranges.py); fits 375 px
            from server import db as dbm, profiles as prof_r, ranges as rng, records as rec_r
            was_profile = js("localStorage.getItem('ht-profile')")
            days = [f"2025-{m:02d}-15" for m in range(6, 13)] + [f"2026-{m:02d}-{d_:02d}" for m in range(1, 11) for d_ in (5, 20)]
            vals = [60, 90, 120, 250, 180, 160, 140, 130, 170, 210, 95, 12, 40, 80, 110, 140, 145, 160, 175, 120, 100, 90, 85, 70, 65, 300, 400]
            fe = [[f"{d_}T08:00:00", v, None, None, "", str(v)] for d_, v in zip(days, vals)]
            fe[3] = [fe[3][0], 250, 30, 400, "", "250"]   # its own report's range: in range by it, though above the setting's
            fe[11] = [fe[11][0], 12, None, None, "L", "12"]   # flagged by its own lab, with no range: the flag stays
            ug = [["2026-02-05T08:00:00", 500, None, None, "", "500"], ["2026-06-05T08:00:00", 90, None, None, "", "90"]]
            db = dbm.connect()
            owner = db.execute("SELECT id FROM users WHERE username=?", (USER,)).fetchone()["id"]
            rpid = prof_r.create_profile(db, "Ranges (fictional)", owner_id=owner)
            with dbm.transaction(db):
                rec_r.store(db, rpid, {"built": "2026-11-15", "source": "fictional",
                                       "events": [{"id": i + 1, "t": f"{d_}T08:00:00", "k": "lab", "title": "Lab results"} for i, d_ in enumerate(days)],
                                       "details": {}, "costs": [], "upcoming": [],
                                       "labs": [{"name": "Ferritin", "unit": "ng/mL", "panel": "Iron", "n": len(fe), "txt": [], "pts": fe},
                                                {"name": "Ferritin", "unit": "ug/L", "panel": "Iron", "n": 2, "txt": [], "pts": ug}]},
                            "upload", fmt="csv")
            db.close()
            # the birth date and sex are About this person's since #194 (Sharing), and Settings says so beside the ranges
            page.goto(base + f"/sharing.html?profile={rpid}#about")
            page.wait_for_selector(f"#about[data-profile='{rpid}'] #aboutBirth", timeout=10000)
            page.fill("#aboutBirth", "1990-03-01")
            page.check("#about input[name=aboutSex][value=female]")
            page.click("#aboutSave")
            page.wait_for_function("document.getElementById('aboutMsg').textContent === 'Saved.'", timeout=10000)
            page.goto(base + f"/settings.html?profile={rpid}#sec-ranges")
            page.wait_for_selector("#sec-ranges [data-key='ranges.overrides'] .set-sugg .chip", timeout=10000)
            chips = js("[...document.querySelectorAll('#sec-ranges .set-sugg .chip')].map(b => b.textContent)")
            check("ranges: Settings suggests the labs whose results have no range, by unit",
                  len(chips) == 2 and chips[0] == f"+ Ferritin (ng/mL) · {len(fe) - 2}", chips)
            uses = js("document.getElementById('rangesUses').textContent")
            check("…and says the ages and sexes are matched against About this person's, with a link there (#194)",
                  "born" in uses and "1990" in uses and "female" in uses
                  and js("document.querySelector('#rangesUses a').getAttribute('href')") == f"sharing.html?profile={rpid}#about"
                  and not js("!!document.querySelector(\"#sec-ranges [data-key='ranges.birthDate'], #sec-ranges [data-key='ranges.sex']\")"), uses)
            ov = "#sec-ranges [data-key='ranges.overrides']"
            page.wait_for_selector(f"{ov} .set-sugg .chip", timeout=10000)
            page.locator(f"{ov} .set-sugg .chip").first.click()
            r1 = page.locator(f"{ov} .set-row:not(.set-head)").nth(0)
            check("…a suggestion adds a row with its lab and unit", r1.locator("[data-k=lab]").input_value() == "Ferritin"
                  and r1.locator("[data-k=unit]").input_value() == "ng/mL")
            r1.locator("[data-k=low]").fill("200")
            r1.locator("[data-k=high]").fill("20")
            n_problems = len(problems)
            page.locator("#sec-ranges .set-foot button:text-is('Save')").click()
            page.wait_for_function(f"document.querySelector(\"{ov} .msg\").textContent.includes('low is above the high')", timeout=10000)
            check("…a low above the high is refused, in words, beside the setting and its section's Save",
                  "isn't saved" in js("document.querySelector('#sec-ranges .set-foot > .msg').textContent")
                  and js("document.getElementById('sec-ranges').hasAttribute('data-dirty')"), js("document.querySelector('#sec-ranges .set-foot > .msg').textContent"))
            settle(200)
            del problems[n_problems:]   # the refusal's 400 was asked for
            r1.locator("[data-k=low]").fill("20")
            r1.locator("[data-k=high]").fill("200")
            page.locator(f"{ov} button:text-is('Add a range')").click()
            r2 = page.locator(f"{ov} .set-row:not(.set-head)").nth(1)
            r2.locator("[data-k=lab]").fill("Ferritin")
            r2.locator("[data-k=unit]").fill("ng/mL")
            r2.locator("[data-k=sex]").select_option("female")
            r2.locator("[data-k=ageFrom]").fill("36")
            r2.locator("[data-k=low]").fill("15")
            r2.locator("[data-k=high]").fill("150")
            pv = js("document.getElementById('rangesPreview').textContent")
            check("…the preview says how many results the rows fit", pv.startswith(f"{len(fe)} results here have no range") and f"fit {len(fe) - 2}" in pv, pv)
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("…and the ranges editor fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.locator("#sec-ranges .set-foot button:text-is('Save')").click()
            page.wait_for_function("document.querySelector('#sec-ranges .set-foot > .msg').textContent.startsWith('Saved “Ranges')", timeout=10000)
            page.goto(base + "/")
            page.wait_for_function("typeof booted !== 'undefined' && booted && PROFILE.name === 'Ranges (fictional)'", timeout=20000)
            got = js("SERIES.filter(s => !s.watch).flatMap(s => s.pts.map(p => [s.name, s.unit, p[0], p[2], p[3], p[4] || '', p.rs || '']))")
            cfg = js("rangeConfig(CFG)")   # the ranges, with About this person's birth date and sex (#194)
            want = []
            for unit, pts in (("ng/mL", fe), ("ug/L", ug)):
                for q in pts:
                    hit = None if rng.has_own(q[2], q[3], q[4]) else rng.find(cfg, "Ferritin", unit, q[0])
                    want.append(["Ferritin", unit, q[0], hit["low"] if hit else q[2], hit["high"] if hit else q[3],
                                 rng.flag_for(q[1], hit) if hit else q[4], "settings" if hit else ""])
            check("…the page applies them as the server does (ranges.js and server/ranges.py agree)", sorted(got) == sorted(want),
                  [g for g in got if g not in want][:3])
            check("…by the age on each result's day: the female 36+ row from 2026-03-01, the row for anyone before",
                  [g[4] for g in got if g[1] == "ng/mL" and g[2][:10] in ("2026-02-20", "2026-03-20")] == [200, 150])
            check("…never over a report's own range or flag, nor in another unit",
                  [g[3:] for g in got if g[2].startswith(days[3])][0] == [30, 400, "", ""] and [g for g in got if g[2].startswith(days[11])][0][5:] == ["L", ""]
                  and all(g[6] == "" for g in got if g[1] == "ug/L"))
            js("state.sel = new Set(['Ferritin']); applyPreset('all'); switchTab('labs'); renderLabs()")
            settle(400)
            card = "[...document.querySelectorAll('#charts .chart')].find(c => c.querySelector('.unit').textContent.startsWith('ng/mL'))"
            check("Trends: the chart says where the range came from, and colours the results outside it",
                  "range from settings" in js(f"{card}.querySelector('.unit').textContent") and js(f"{card}.querySelectorAll('.pt.abn').length") > 0
                  and js(f"!!{card}.querySelector('.refband')"), js(f"{card}.querySelector('.unit').textContent"))
            js(f"{card}.querySelector('.hd .view-seg button:last-child').click()")   # its table: Chart | Table (#60)
            settle(200)
            table = js("document.querySelector('#charts .tablewrap').textContent")
            check("…its table labels each such range, and keeps the report's own", "(range from settings)" in table and "30 – 400" in table, table[:200])
            check("…and the caption says so", "range from settings" in page.inner_text("#view-labs .range-note")
                  and "reference ranges in your own lab reports" in page.inner_text("#view-labs .range-note"))
            js("state.tables.clear(); renderCharts()")
            for width in (1400, 375):
                page.set_viewport_size({"width": width, "height": 900 if width > 375 else 800})
                js("switchTab('labs', 'heatmap')")
                settle(300)
                check(f"heatmap ({width} px): the results coloured against the range from settings, and fits",
                      js("document.querySelectorAll('#hm rect.cell[style*=\"--div-hi\"], #hm rect.cell[style*=\"--div-lo\"]').length") > 0
                      and "range from settings" in page.inner_text("#view-labs .range-note") and js("document.documentElement.scrollWidth") <= width,
                      js("document.documentElement.scrollWidth"))
                js("switchTab('overview')")
                settle(300)
                check(f"Overview ({width} px): the flag says it's against a range from settings, and fits",
                      js("document.querySelector('#view-overview .tile .st')?.title") == "Against a range from settings"
                      and "(range from settings)" in page.inner_text("#view-overview .ov-row .r")
                      and "range from settings" in page.inner_text("#view-overview .range-note") and js("document.documentElement.scrollWidth") <= width,
                      js("document.querySelector('#view-overview .tile .st')?.title"))
                js("switchTab('labs', 'chart')")
                settle(300)
                check(f"Labs as charts ({width} px) fits", js("document.documentElement.scrollWidth") <= width, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.goto(base + f"/sharing.html?profile={rpid}#summary")
            page.wait_for_selector("#build:not([hidden])", timeout=10000)
            page.click("#previewBtn")
            page.wait_for_selector("#preview:not([hidden]) h3", timeout=10000)
            check("visit summary: a result without a range is judged against the range from settings, marked so",
                  "(settings)" in js("document.getElementById('preview').textContent")
                  and "range from settings" in js("document.getElementById('sumSettingsNote')?.textContent || ''"))
            db = dbm.connect()
            prof_r.delete_profile(db, rpid)
            db.close()
            js("v => v === null ? localStorage.removeItem('ht-profile') : localStorage.setItem('ht-profile', v)", was_profile)
            # ---- About this person (#194): on Sharing, a group of its own under Profiles: the birth date and the sex
            # used for reference ranges (worded as such, apart from gender identity), saved together; a dated height
            # history typed in cm or in feet and inches, shown in the person's Units choice with the other beside; a
            # clinic's heights from the records beside it, added only on request; a viewer sees them and can't change
            # them; the records page has the helpers wave B builds on (age on a day, in years and months, the sex for
            # ranges, the height in effect on a day), agreeing with server/person.py; a share link leaves it unticked;
            # at 1400 and 375 px, light and dark, axe clean
            from server import person as person_r, settings as settings_r
            db = dbm.connect()
            apid = prof_r.create_profile(db, "About (fictional)", owner_id=owner)
            with dbm.transaction(db):
                rec_r.store(db, apid, {"built": "2026-11-15", "source": "fictional",
                                       "events": [{"id": 1, "t": "2025-04-02T09:00:00", "k": "visit", "title": "Well-child visit"}],
                                       "details": {}, "costs": [], "upcoming": [],
                                       "labs": [{"name": "Height", "unit": "in", "panel": "Vitals", "n": 2, "txt": [],
                                                 "pts": [["2025-04-02T09:00:00", 52.0, None, None, "", "52"],
                                                         ["2026-04-06T09:00:00", 54.5, None, None, "", "54.5"]]}]}, "upload", fmt="ccda")
            vpid = prof_r.create_profile(db, "Viewed (fictional)")   # this account only views it
            db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (owner, vpid))
            settings_r.put(db, "person.birthDate", "1958-11-02", "profile", vpid)
            settings_r.put(db, "person.heights", [{"date": "2020-01-10", "cm": 165, "unit": "cm"}], "profile", vpid)
            db.close()
            about = lambda pid_: page.wait_for_selector(f"#about[data-profile='{pid_}']", timeout=10000)
            page.goto("about:blank")   # the new profile may have the id the one above had: a fresh page, not a change of #
            page.goto(base + f"/sharing.html?profile={apid}#about")
            about(apid)
            groups = js("[...document.querySelectorAll('#shrNav li')].map(li => li.textContent)")
            atext = page.inner_text("#about")
            unset = [js("document.getElementById('aboutBirth').value"), js("document.querySelector('#about input[name=aboutSex]:checked')?.value"),
                     "No height written down yet." in atext]
            check("About this person: its own group on Sharing, under Profiles, every detail optional and unset at first",
                  groups[-3:] == ["Profiles", "About this person", "Profile"] and unset == ["", "", True], (groups[-3:], unset))
            check("…the sex worded as what labs base reference ranges on, apart from gender identity",
                  "Sex used for reference ranges" in atext and "What labs base their reference ranges on" in atext and "isn't gender identity" in atext, atext[:300])
            page.fill("#aboutBirth", "2016-05-20")
            page.check("#about input[name=aboutSex][value=male]")
            page.click("#aboutSave")
            page.wait_for_function("document.getElementById('aboutMsg').textContent === 'Saved.'", timeout=10000)
            got = js(f"fetch('api/profiles/{apid}/person').then(r => r.json())")
            check("…the birth date and sex saved together, the age today beside it", (got["birthDate"], got["sex"]) == ("2016-05-20", "male")
                  and f"aged {person_r.age_on('2016-05-20', js('isoDay(Date.now())'))} today" in page.inner_text("#about"), got)
            hist = "#about .about-heights:not(.about-recorded) tbody tr"
            rows_of = lambda sel: js(f"[...document.querySelectorAll({json.dumps(sel)})].filter(r => !r.hidden).map(r => r.textContent)")
            for d_, unit, vals in (("2024-09-01", "cm", {"#heightCm": "128.5"}), ("2025-09-01", "ftin", {"#heightFt": "4", "#heightIn": "5"})):
                page.fill("#heightDate", d_)
                page.check(f"#about input[name=heightUnit][value={unit}]")
                for sel, v in vals.items():
                    page.fill(sel, v)
                page.click("#heightAdd")
                page.wait_for_function(f"document.getElementById('aboutMsg').textContent.startsWith('Added the height') && document.querySelectorAll({json.dumps(hist)}).length === {1 if unit == 'cm' else 2}", timeout=10000)
            hs = rows_of(hist)
            check("…a dated height history, newest first, each as it was typed with the other unit beside",
                  "4 ft 5 in" in hs[0] and "(134.6 cm)" in hs[0] and "128.5 cm" in hs[1] and "(4 ft 2.6 in)" in hs[1], hs)
            stored = js(f"fetch('api/profiles/{apid}/person').then(r => r.json())")["heights"]
            check("…kept in one unit (cm), with how each was typed", stored == [{"date": "2024-09-01", "cm": 128.5, "unit": "cm"},
                                                                             {"date": "2025-09-01", "cm": 134.62, "unit": "ftin"}], stored)
            n_problems = len(problems)
            r_ = js(f"fetch('api/profiles/{apid}/person/heights', {{method: 'POST', headers: {{'X-Sanguity': '1', 'Content-Type': 'application/json'}}, "
                    "body: JSON.stringify({date: '2015-01-01', cm: 100})}).then(async r => [r.status, (await r.json()).detail])")
            check("…a height before the birth date is refused, in words", r_[0] == 400 and "before the birth date" in r_[1], r_)
            settle(200)
            del problems[n_problems:]   # the refusal's 400 was asked for
            rec_rows = rows_of("#about .about-recorded tbody tr")
            check("…a clinic's heights from the records beside the history, not in it, each offered",
                  len(rec_rows) == 2 and js("fmtD(zParse('2026-04-06'))") in rec_rows[0] and "4 ft 6.5 in" in rec_rows[0] and len(hs) == 2
                  and js("document.querySelectorAll('#about .about-recorded button').length") == 2, rec_rows)
            page.click("#about .about-recorded tbody tr:first-child button")
            page.wait_for_function(f"document.querySelectorAll({json.dumps(hist)}).length === 3", timeout=10000)
            check("…added on request, then marked as in the history", "In the history" in rows_of("#about .about-recorded tbody tr")[0]
                  and "4 ft 6.5 in" in rows_of(hist)[0])
            units_was = js("fetch('api/settings/user').then(r => r.json())")["units"]
            put_units = lambda u: js("u => fetch('api/settings/user/units', {method: 'PUT', headers: {'X-Sanguity': '1', 'Content-Type': 'application/json'}, "
                                     "body: JSON.stringify({value: u})}).then(r => r.status)", u)
            check("…the person's Units choice for heights (Settings → Display)", put_units({**units_was, "height": "cm"}) == 200)
            page.reload()
            about(apid)
            hs = rows_of(hist)
            check("…shows every height in it, the other unit beside", "138.4 cm" in hs[0] and "(4 ft 6.5 in)" in hs[0] and "134.6 cm" in hs[1], hs)
            page.click(f"{hist}:nth-child(2) button")
            said = answer_ask(page)
            page.wait_for_function(f"document.querySelectorAll({json.dumps(hist)}).length === 2", timeout=10000)
            check("…a height removed after asking", said[0] == "Remove this height?" and "134.6 cm" in said[1]
                  and js(f"fetch('api/profiles/{apid}/person').then(r => r.json())")["heights"][-1]["date"] == "2026-04-06", said)
            # ---- heights from the phone (#223): an uploaded Apple Health export's, beside the history like a clinic's,
            # marked as from the phone, added only on request
            from server import watch_store as ws_r
            db = dbm.connect()
            with dbm.transaction(db):
                ws_r.save_import(db, apid, {"metrics": [], "exercise": [], "heights": [["2026-10-01", 140.2, "Fictional Phone", "cm"]]})
            db.close()
            page.reload()
            about(apid)
            rec_rows = rows_of("#about .about-recorded tbody tr")
            check("…the heights an uploaded Apple Health export brought, beside them, marked as from the phone and offered, not added (#223)",
                  len(rec_rows) == 3 and "Apple Health on the phone (Fictional Phone)" in rec_rows[0] and "140.2 cm" in rec_rows[0]
                  and "Add to the history" in rec_rows[0] and "in the phone's Health app" in page.inner_text("#about") and len(rows_of(hist)) == 2, rec_rows)
            page.click("#about .about-recorded tbody tr:first-child button")
            page.wait_for_function(f"document.querySelectorAll({json.dumps(hist)}).length === 3", timeout=10000)
            check("…one added on request, like a clinic's", "In the history" in rows_of("#about .about-recorded tbody tr")[0] and "140.2 cm" in rows_of(hist)[0])
            # wave B's helpers (person.js) on the records page, as server/person.py works them out
            page.goto(base + f"/?profile={apid}")
            page.wait_for_function("typeof booted !== 'undefined' && booted && PROFILE.name === 'About (fictional)'", timeout=20000)
            cfg_ = {"person": js("CFG.person")}
            days_ = ["2016-05-19", "2016-06-19", "2016-06-20", "2024-08-31", "2024-09-01", "2026-05-20", "2026-11-15"]
            web = js("ds => ds.map(d => [personAgeOn(d), personAgeMonthsOn(d), rangeSexOf(), heightOn(d)?.cm ?? null])", days_)
            srv_ = [[person_r.age_on("2016-05-20", d_), person_r.age_months_on("2016-05-20", d_), person_r.sex_for_ranges(cfg_),
                     (person_r.height_on(cfg_["person"]["heights"], d_) or {}).get("cm")] for d_ in days_]
            check("…the records page has them (CFG.person), with the age in years and months, the sex for ranges and the height on a day, "
                  "as the server works them out", web == srv_ and web[2][:2] == [0, 1] and web[4][3] == 128.5 and web[0] == [None, None, "male", None], web)
            # a viewer
            page.goto(base + f"/sharing.html?profile={vpid}#about")
            about(vpid)
            vt = page.inner_text("#about")
            check("…a viewer sees them as text, with nothing to change", js("fmtD(zParse('1958-11-02'))") in vt and "Not set" in vt and "165 cm" in vt
                  and "Only the owners and editors of " in vt and "can change these" in vt and not js("!!document.querySelector('#about input, #about select, #aboutBody button')"), vt[:300])
            # a share link leaves it unticked
            page.goto(base + f"/sharing.html?profile={apid}#links")
            page.wait_for_selector("#links .share-links button:has-text('New share link')", timeout=10000)
            page.click("#links button:has-text('New share link')")
            box_ = page.locator("#links .share-form input[value=about]")
            check("…a share link offers it, unticked", box_.count() == 1 and not box_.is_checked()
                  and "About this person" in page.inner_text("#links .share-sections") and "About this person" not in page.inner_text("#links .share-sees"))
            page.click("#links .share-form button:text-is('Cancel')")
            # 1400 and 375 px, light and dark: fits, and axe-core finds nothing serious
            theme_was = js("localStorage.getItem('ht-theme')")
            a_bad, a_axe, a_other = [], [], []
            for theme in ("light", "dark"):
                js(f"localStorage.setItem('ht-theme', '{theme}')")
                for width in (1400, 375):
                    page.set_viewport_size({"width": width, "height": 900 if width > 375 else 800})
                    for pid_ in (apid, vpid):
                        page.goto("about:blank")   # a page load each time (the theme is read as it loads)
                        page.goto(base + f"/sharing.html?profile={pid_}#about")
                        about(pid_)
                        settle(150)
                        a_bad += phone_fits(page, f"About this person {pid_} at {width} px, {theme}")
                        b_, o_ = axe_check(page, f"About this person {pid_} at {width} px, {theme}")
                        a_axe += b_; a_other += o_
            js(f"localStorage.setItem('ht-theme', {json.dumps(theme_was)})" if theme_was else "localStorage.removeItem('ht-theme')")
            page.set_viewport_size({"width": 1400, "height": 900})
            check("…at 1400 and 375 px, light and dark, for an owner and a viewer: no control cut off or overlapping (#61)", not a_bad, a_bad[:6] or "")
            check("…and axe-core finds nothing serious (#202)", not a_axe, a_axe[:6] or "; ".join(a_other[:4]))
            put_units(units_was)
            db = dbm.connect()
            prof_r.delete_profile(db, apid)
            prof_r.delete_profile(db, vpid)
            db.close()
            js("v => v === null ? localStorage.removeItem('ht-profile') : localStorage.setItem('ht-profile', v)", was_profile)
            # ---- children's growth (#196): a fictional girl born in 2022, six weeks early (child_profile: her clinic's
            # weights, her length and head circumference in the records, heights from 2 in About this person, her due
            # date). The page's percentiles match the WHO's and the CDC's published ones and tools/growth_tables.py's;
            # Body and activity (no phone) has a Growth charts card: the CDC's charts from 2 and the WHO's to 2 picked
            # as a pair, each measurement over the publisher's curves, its percentile in the readout, the table and the
            # latest list; the age corrected before 2; nothing without a birth date and sex, and none for an adult;
            # kg or lb as chosen; 1400 and 375 px, light and dark, chart and table, axe clean
            sys.path.insert(0, os.path.join(ROOT, "tools"))
            import growth_tables as gt
            db = dbm.connect()
            cpid = child_profile(db, owner)
            db.close()
            page.goto("about:blank")
            page.goto(base + f"/?profile={cpid}#health")
            page.wait_for_function("typeof booted !== 'undefined' && booted && PROFILE.name === 'Robin (fictional child)'", timeout=20000)
            js("switchTab('health')")
            page.wait_for_selector("#h-growth svg circle", timeout=15000)
            settle(300)
            with open(os.path.join(ROOT, "www", "js", "growth-lms.json"), encoding="utf-8") as f:
                tables = json.load(f)
            M_ = 30.4375
            # published values: the WHO's expanded percentile tables (by day) and the CDC's percentile columns (by half month)
            refs = [("weight", "male", 0, 2.507, 3), ("weight", "male", 0, 4.35, 97), ("weight", "male", 365, 9.646, 50), ("weight", "female", 61, 4.481, 15),
                    ("weight", "female", 700, 14.337, 97), ("height", "male", 180, 63.463, 3), ("height", "male", 730, 87.802, 50),
                    ("height", "female", 14, 53.484, 85), ("head", "female", 91, 41.855, 97), ("head", "male", 200, 42.442, 15),
                    ("head", "female", 730, 44.555, 3), ("weight", "female", 120.5 * M_, 26.33131782, 10), ("weight", "female", 120.5 * M_, 43.85001322, 90),
                    ("height", "male", 60.5 * M_, 101.4523027, 5), ("height", "male", 60.5 * M_, 116.7549954, 95), ("height", "female", 200.5 * M_, 162.8272889, 50),
                    ("weight", "male", 30.5 * M_, 11.12786972, 3), ("bmi", "male", 180.5 * M_, 23.4512, 85), ("bmi", "male", 180.5 * M_, 26.8369, 95),
                    ("bmi", "male", 180.5 * M_, 31.9956, 98), ("bmi", "female", 96.5 * M_, 25.4977, 99), ("bmi", "female", 96.5 * M_, 13.5405, 5)]
            page_p = js("refs => growthTables().then(T => refs.map(([k, s, d, x]) => growthPct(T, k, s, d, x)))", [r[:4] for r in refs])
            off = [(r, round(p_, 3)) for r, p_ in zip(refs, page_p) if p_ is None or abs(p_ - r[4]) > 0.1]
            check("growth (#196): the page's percentiles match the published ones, the WHO's to 2 years and the CDC's from 2 to 20 (BMI above the 95th by the 2022 extended charts)",
                  not off, off)
            mine = js("growthData().meas.map(m => [m.key, m.day, m.x, m.age.days, m.age.chrono, m.age.corrected, m.set, m.p])")
            srv_p = [gt.percentile(tables, k, "female", d, x) for k, _, x, d, *_ in mine]
            diff = [(m_, s_) for m_, s_ in zip(mine, srv_p) if s_ is None or abs(m_[7] - s_) > 1e-4]
            early = 42   # days between her birth and due dates
            visits = [v for v in CHILD_VISITS if v[0] != "2022-03-16"]
            check("…every measurement of hers: weights, length and head circumference to 2, heights and BMI from 2, each with the percentile tools/growth_tables.py gives",
                  not diff and sorted({m_[0] for m_ in mine}) == ["bmi", "head", "height", "weight"]
                  and sum(m_[0] == "weight" for m_ in mine) == len(visits) and sum(m_[0] == "head" for m_ in mine) == 8 and sum(m_[0] == "bmi" for m_ in mine) == 5, diff[:3])
            check("…before 2 years the age is corrected for her due date (six weeks); from 2, it isn't; the visit before her due date has none",
                  all(m_[5] == (m_[4] <= 730) and m_[3] == m_[4] - (early if m_[5] else 0) for m_ in mine) and not any(m_[1] == "2022-03-16" for m_ in mine)
                  and {m_[6] for m_ in mine if m_[4] <= 730} == {"who"} and {m_[6] for m_ in mine if m_[4] > 730} == {"cdc"}, mine[:2])
            gtext = page.inner_text("#h-growth")
            seg_ = js("[...document.querySelectorAll('#h-growth .growth-set button')].map(b => [b.textContent, b.getAttribute('aria-pressed')])")
            heads = js("[...document.querySelectorAll('#h-growth .growth-cell h3')].map(h => h.textContent)")
            curves = js("[...document.querySelectorAll('#h-growth .growth-cell svg')].map(s => [s.querySelectorAll('path.growth-curve').length, s.querySelectorAll('circle').length, "
                        "[...s.querySelectorAll('text.growth-pct')].map(t => t.textContent)])")
            check("…a Growth charts card on Body and activity, with no phone: the CDC's from 2 to 20 shown first (her latest), the WHO's to 2 beside it as a pair",
                  seg_ == [["Birth to 2 years (WHO)", "false"], ["2 to 20 years (CDC)", "true"]] and heads == ["Weight for age (kg)", "Height for age (cm)", "BMI for age (kg/m²)"]
                  and "as the CDC recommends" in gtext and "not what that means" in gtext and js("!!document.querySelector('#view-health .empty-state, #view-health .empty')"), (seg_, heads))
            check("…each chart: the CDC's 5th to 95th percentile curves (and the 85th for BMI), named at their ends, and her measurements",
                  [c_[0] for c_ in curves] == [7, 7, 8] and [c_[1] for c_ in curves] == [5, 5, 5] and curves[2][2] == ["95th", "90th", "85th", "75th", "50th", "25th", "10th", "5th"], curves)
            last_w = next(m_ for m_ in reversed(mine) if m_[0] == "weight")
            lst = js("[...document.querySelectorAll('#h-growth .vital-list li')].map(l => l.textContent)")
            pt = js(f"pctText({last_w[7]})")
            check("…the latest of each listed with its percentile, date, age and where it came from",
                  len(lst) == 3 and lst[0].startswith(f"Weight {last_w[2]:.1f} kg · {pt} percentile") and "aged 4 years 6 months" in lst[0] and "Maple Street Pediatrics" in lst[0]
                  and "About this person" in lst[1] and lst[2].startswith("BMI "), lst)
            page.focus("#h-growth .growth-cell svg")
            page.keyboard.press("End")
            settle(150)
            tipg = js("tip.innerText")
            page.keyboard.press("Escape")
            check("…a chart steps from the keyboard: the measurement, its percentile, when, the age and the chart it's read on",
                  f"{last_w[2]:.1f} kg · {pt} percentile" in tipg and "aged 4 years 6 months" in tipg and "CDC chart" in tipg
                  and js("document.querySelector('#h-growth .growth-cell svg').getAttribute('aria-label')").startswith("Weight for age, the CDC's chart, 2 to 20 years: 5 measurements"), tipg)
            page.click("#h-growth .growth-set button:has-text('Birth to 2 years')")
            settle(400)
            heads = js("[...document.querySelectorAll('#h-growth .growth-cell h3')].map(h => h.textContent)")
            curves = js("[...document.querySelectorAll('#h-growth .growth-cell svg')].map(s => [s.querySelectorAll('path.growth-curve').length, s.querySelectorAll('circle').length])")
            lst = js("[...document.querySelectorAll('#h-growth .vital-list li')].map(l => l.textContent)")
            check("…the WHO's to 2: weight, length (lying down) and head circumference from the records, the WHO's 3rd to 97th curves, ages corrected",
                  heads == ["Weight for age (kg)", "Length for age (cm)", "Head circumference for age (cm)"] and curves == [[5, 8], [5, 8], [5, 8]]
                  and all("(corrected: 17 months)" in l_ for l_ in lst) and "Before 2 years, ages are corrected for being born early" in page.inner_text("#h-growth")
                  and js("document.activeElement.textContent") == "Birth to 2 years (WHO)", (heads, curves, lst[:1]))
            page.click("#h-growth .view-seg button:text-is('Table')")
            settle(300)
            tbg = js("(() => { const t = document.querySelector('#h-growth table.data'); return [[...t.querySelectorAll('thead th')].map(h => h.textContent), "
                     "[...t.querySelectorAll('tbody tr')].map(r => [...r.children].map(c => c.textContent))]; })()")
            want_rows = len(mine)
            first_who = next(r_ for r_ in reversed(tbg[1]) if r_[5] == "WHO" and r_[2] == "Weight")
            check("…Table lists every measurement, newest first: date, age (corrected beside), what, value, percentile, chart and source",
                  tbg[0] == ["Date", "Age", "Measurement", "Value", "Percentile", "Chart", "From"] and len(tbg[1]) == want_rows
                  and first_who[1] == "1 month (corrected: 7 days)" and first_who[3] == "3.4 kg" and first_who[4] == js(f"pctText({next(m_ for m_ in mine if m_[0] == 'weight')[7]})"), (tbg[0], first_who))
            page.click("#h-growth .view-seg button:text-is('Chart')")
            # kg or lb, as chosen under Settings → Units
            js("CFG.units = {...CFG.units, weight: 'lb'}; renderHealth()")
            settle(300)
            h3s = js("[...document.querySelectorAll('#h-growth .growth-cell h3')].map(h => h.textContent)")
            lw = js("document.querySelector('#h-growth .vital-list li b').textContent")
            check("…in lb when lb is chosen, the curves with them", h3s[0] == "Weight for age (lb)" and lw == f"Weight {10.2 / 0.45359237:.1f} lb", (h3s, lw))
            js("CFG.units = {...CFG.units, weight: 'reported'}; renderHealth()")
            bw = js("(r => [bmiWords(r), pctText(growthPct(GT, 'bmi', 'female', growthAge(isoDay(r.t)).days, r.v)), r.child])(H.bmi.at(-1))")
            check("…and her BMI where #195 shows it says its percentile for age, the same", bw[2] and bw[0].endswith(f"({bw[1]} percentile for age)"), bw)
            # nothing without a birth date or a sex, and not for an adult (the demo, above, shows no card)
            gone = js("""(() => { const p = CFG.person, out = [];
                for (const q of [{...p, sex: ''}, {...p, birthDate: '', dueDate: ''}]) { CFG.person = q; renderHealth(); out.push(!!document.getElementById('h-growth')); }
                CFG.person = p; renderHealth(); out.push(!!document.getElementById('h-growth')); return out; })()""")
            check("…nothing shows without a birth date, or without the sex used for reference ranges", gone == [False, False, True], gone)
            check("…and no judging words: where measurements fall, sourced and labelled", not re.search(r"(?i)\b(normal|healthy|underweight|overweight|obes|too (low|high)|concern|should)\b", page.inner_text("#h-growth")))
            g_bad, g_axe = [], []
            theme_before = js("document.documentElement.dataset.theme || null")
            for width in (1400, 375):
                page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                for theme in ("light", "dark"):
                    js("t => { document.documentElement.dataset.theme = t; }", theme)
                    for set_, table in (("cdc", False), ("who", False), ("who", True)):
                        js("([s, on]) => { grState.set = s; state.tables.clear(); if (on) state.tables.add('#h-growth'); renderHealth(); }", [set_, table])
                        settle(300)
                        page.locator("#h-growth").scroll_into_view_if_needed()
                        if width == 375:
                            g_bad += phone_fits(page, f"growth {set_} {'table' if table else 'charts'} ({theme})")
                        g_axe += axe_check(page, f"growth {set_} {'table' if table else 'charts'}, {width} px, {theme}")[0]
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            page.set_viewport_size({"width": 1400, "height": 900})
            js("state.tables.clear()")
            check("…at 1400 and 375 px, light and dark, both charts' sets and the table: nothing cut off or overlapping, and axe-core finds nothing serious",
                  not g_bad and not g_axe, (g_bad + g_axe)[:6])
            # the due date is About this person's, changed by owners and editors
            page.goto(base + f"/sharing.html?profile={cpid}#about")
            page.wait_for_selector(f"#about[data-profile='{cpid}'] #aboutDue", timeout=10000)
            check("…the due date is in About this person, beside the birth date, saying what it's for",
                  js("document.getElementById('aboutDue').value") == CHILD_DUE and "growth charts use the age counted from it" in page.inner_text("#about"))
            page.fill("#aboutDue", "2022-09-20")
            n_problems = len(problems)
            page.click("#aboutSave")
            page.wait_for_function("document.getElementById('aboutMsg').textContent.includes('more than 20 weeks after the birth date')", timeout=10000)
            settle(200)
            del problems[n_problems:]   # the refusal's 400 was asked for
            page.fill("#aboutDue", "")
            page.click("#aboutSave")
            page.wait_for_function("document.getElementById('aboutMsg').textContent === 'Saved.'", timeout=10000)
            check("…a due date that can't be (more than 20 weeks after the birth) is refused in words; cleared, it's saved",
                  js(f"fetch('api/profiles/{cpid}/person').then(r => r.json())")["dueDate"] == "")
            # ---- a condition's preset on a child's profile (#186): measures for adults left out, and said
            page.goto(base + f"/settings.html?profile={cpid}#sec-overview")
            page.wait_for_selector("#preKid", state="attached", timeout=10000)
            page.wait_for_load_state("networkidle")
            page.click("#preOpen")
            page.check("label[data-id='chronic-kidney-disease'] input")
            check("presets on a child's profile (#186): the adult-only eGFR is left out, and said",
                  "a child's profile" in page.inner_text("#preKid") and page.inner_text("#preChild").startswith("Left out for a child's profile")
                  and "CKD-EPI eGFR" in page.inner_text("#preChild") and "CKD-EPI eGFR" not in page.inner_text("#preMissing"), page.inner_text("#preOut")[:300])
            db = dbm.connect()
            prof_r.delete_profile(db, cpid)
            db.close()
            js("v => v === null ? localStorage.removeItem('ht-profile') : localStorage.setItem('ht-profile', v)", was_profile)
            # ---- connected sources (#32, #91), against the fake SMART on FHIR server standing in for an Epic sandbox:
            # an admin sets the source up and tests it, the profile connects by signing in, the first pull is previewed
            # and saved, Sync now brings a new result, and when the patient ends access the page asks to reconnect
            from tests.fakefhir import FakeFHIR
            fake = FakeFHIR(patients=["robin-example"]).start()
            fake.signin_patient = "pat-1"   # who "signs in" at the fake's authorize page
            fake.register_client("sanguity-browser-check", [base + "/connect/return"])
            allowed.add(origin(fake.fhir_base))   # its sign-in page, which the browser visits
            page.goto(base + "/connect.html#admin")   # its old address: the setup is on the Admin page now (#167)
            page.wait_for_url("**/admin.html#admin", timeout=10000)
            page.wait_for_selector("#sourceList", state="attached")
            page.wait_for_function("document.getElementById('returnAddr').textContent.length > 5 && !document.getElementById('admin').hidden", timeout=10000)
            check("connected sources: the setup screen gives the exact return address to register",
                  js("document.getElementById('returnAddr').textContent") == base + "/connect/return", js("document.getElementById('returnAddr').textContent"))
            check("…and warns that a plain-HTTP, LAN-only address is unlikely to be accepted",
                  "plain HTTP" in page.inner_text("#addrWarn") and "own network" in page.inner_text("#addrWarn"), page.inner_text("#addrWarn"))
            check("…with a guide per vendor: where to register, and the scopes", "fhir.epic.com" in page.inner_text("#guide")
                  and "patient/Observation" in js("document.getElementById('fScopes').value"))
            # ---- the new kinds' scopes in the walk-through (#228)
            walk_scopes = js("[...document.querySelectorAll('#guide [data-paste=types]')].map(c => c.textContent).join(' ')")
            check("…the walk-through's types and scopes include immunizations, allergies, fills, appointments and imaging studies (#228)",
                  all(t in walk_scopes and f"patient/{t}." in js("document.getElementById('fScopes').value")
                      for t in ("Immunization", "AllergyIntolerance", "MedicationDispense", "Appointment", "ImagingStudy"))
                  and "added at the vendor" in page.inner_text("#guide"), walk_scopes)
            page.fill("#fName", "Fake Epic sandbox")
            page.fill("#fIssuer", fake.fhir_base)
            page.fill("#fClient", "sanguity-browser-check")
            page.click("#fSave")
            page.wait_for_function("document.getElementById('sourceList').textContent.includes('Fake Epic sandbox')", timeout=10000)
            page.locator("#sourceList .prof", has_text="Fake Epic sandbox").get_by_role("button", name="Test this connection").click()
            page.wait_for_function("document.getElementById('sourceList').textContent.includes('Discovery works') || document.getElementById('sourceList').textContent.includes('tested ')", timeout=10000)
            page.goto(base + "/connect.html")   # its old address: Sources → Portals and health plans (#166)
            page.wait_for_url("**/sources.html#portals", timeout=10000)
            page.wait_for_selector("#adminNote:not([hidden])", timeout=10000)
            check("connected sources: no setup on the everyday page, and an admin is pointed to Admin (#167)",
                  js("!document.getElementById('srcForm') && document.querySelector('#adminNote a').getAttribute('href')") == "admin.html#apps")
            page.select_option("#profile", label="Second person (owner)")
            page.wait_for_selector("#sourceButtons button", timeout=10000)
            conn_pid = js("+document.getElementById('profile').value")
            # the same results (#96): a spreadsheet of the fake patient's 4 March 2025 labs, with dates only and
            # Hemoglobin in g/L, saved in the profile first; the first pull below brings them again
            from server import uploads as up_mod
            db = dbm.connect()
            try:
                csv_up = up_mod.create(db, conn_pid, None)
                up_mod.add_file(db, conn_pid, csv_up, "labs.csv", io.BytesIO(
                    b"Date,Test,Result,Units,Reference Range,Flag,Panel\n03/04/2025,Hemoglobin,121,g/L,135-175,L,CBC\n"
                    b"06/02/2025,Hemoglobin,128,g/L,135-175,L,CBC\n"))
                up_mod.finish(db, conn_pid, csv_up)
            finally:
                db.close()
            write = "{method: 'POST', credentials: 'same-origin', headers: {'X-Sanguity': '1', 'Content-Type': 'application/json'}, body: '{}'}"
            csv_job = js(f"fetch('api/profiles/{conn_pid}/uploads/{csv_up}/import', {write}).then(r => r.json()).then(j => j.id)")
            job_state = lambda: js(f"fetch('api/profiles/{conn_pid}/jobs/{csv_job}').then(r => r.json()).then(j => j.state)")
            for want in ("preview", "done"):
                for _ in range(150):
                    if job_state() == want:
                        break
                    page.wait_for_timeout(200)
                if want == "preview":
                    js(f"fetch('api/profiles/{conn_pid}/jobs/{csv_job}/confirm', {write})")
            check("the same results from a file are in the profile before the source connects", job_state() == "done", job_state())
            csv_label = js(f"fetch('api/profiles/{conn_pid}/jobs/{csv_job}').then(r => r.json()).then(j => j.label)")
            # the install's attachment rules (#101, settings registry): only text attachments are fetched by themselves
            check("the attachment defaults are install settings", js(f"api('profiles/{conn_pid}/settings/sync.attachTypes', "
                  "{method: 'PUT', body: JSON.stringify({value: ['text']})}).then(r => r.status)") == 200)
            page.click("#sourceButtons button")   # choose what to pull (#99), then on to the fake's sign-in
            page.wait_for_selector(".conn-choose", timeout=10000)
            check("connecting shows what each kind of record brings", "reference ranges" in page.inner_text(".conn-choose")
                  and page.locator(".conn-choose input[type=checkbox]").count() == 11, page.inner_text(".conn-choose")[:300])
            new_kinds = js("[...document.querySelectorAll('.conn-choose .conn-kind')].filter(l => ['appointments', 'allergies', 'fills', "
                           "'immunizations'].includes(l.querySelector('input').value)).map(l => [l.querySelector('b').textContent, l.querySelector('input').checked])")
            check("…immunizations, allergies, pharmacy fills and appointments among them, ticked like the rest (#228)",
                  new_kinds == [["Appointments", True], ["Allergies", True], ["Pharmacy fills", True], ["Immunizations", True]], new_kinds)
            page.uncheck(".conn-choose input[value=visits]")
            page.locator(".conn-choose").get_by_role("button", name="Sign in at").click()   # approves and comes back
            page.wait_for_url("**/sources.html?*#portals", timeout=20000)
            page.wait_for_selector("#import-status button.primary", timeout=30000)   # the first pull's preview
            check("signing in connects the source, and its first pull is previewed like an upload",
                  "Ready to import" in page.inner_text("#import-status"), page.inner_text("#import-status")[:300])
            same_li = page.locator("#import-status .imp-same li", has_text="Hemoglobin")
            flag = same_li.inner_text() if same_li.count() == 1 else page.inner_text("#import-status")[:600]
            check("…flagging a result the file brought as possibly the same, with both sources named (#96)",
                  "Hemoglobin 12.1 g/dL" in flag and "Possibly the same result as Hemoglobin 121 g/L" in flag
                  and f"from {csv_label}" in flag, flag)
            check("…and nothing ticked until the person says so", js("[...document.querySelectorAll('#import-status .imp-same input')].every(b => !b.checked)"))
            same_li.get_by_label("Same result: Hemoglobin 12.1 g/dL").check()
            page.click("#import-status button.primary")
            page.wait_for_function("document.getElementById('import-status').textContent.includes('Imported.')", timeout=30000)
            check("…saved, with the result shown once", "shown once, as the same result from another source" in page.inner_text("#import-status"),
                  page.inner_text("#import-status")[:400])
            page.wait_for_function("document.getElementById('connList').textContent.includes('New records come in on a schedule')", timeout=20000)
            check("…saved, and the connection then syncs on a schedule", True)
            page.reload()
            page.wait_for_function("document.getElementById('connList').textContent.includes('Attachments left out')", timeout=20000)
            chosen = page.inner_text(".conn-chosen")
            check("the connection says what it pulls, without the kind left unticked", "lab results" in chosen
                  and "visits" not in chosen and "only when you ask" not in chosen, chosen)
            page.click(".conn-skipped summary")
            skipped = page.inner_text(".conn-skipped")
            check("attachments the rules left out are listed, with why", "Attachments left out (2)" in skipped
                  and "PDF" in skipped and "doesn't fetch" in skipped, skipped)
            page.locator(".conn-skipped li").first.get_by_role("button", name="Fetch anyway").click()
            page.wait_for_function("document.getElementById('connList').textContent.includes('Fetched')", timeout=20000)
            check("…and Fetch anyway brings one in", "Attachments left out (1)" in page.inner_text("#connList"), page.inner_text("#connList")[:400])
            # on Trends: one Hemoglobin chart, one point on 4 March 2025 listing both sources; the drawer the same
            profile_before_96 = js("localStorage.getItem('ht-profile')")
            js(f"localStorage.setItem('ht-profile', '{conn_pid}')")
            page.goto(base + "/")
            page.wait_for_function(f"typeof SERIES !== 'undefined' && PROFILE && PROFILE.id === {conn_pid} && SERIES.length > 0", timeout=20000)
            hgb_day = ("(() => { const s = SERIES.filter(s => s.name === 'Hemoglobin' && !s.watch); "
                       "return s.map(x => x.pts.filter(p => p[0].startsWith('2025-03-04')).map(p => pointSources(x, p))).flat(); })()")
            on_day = js(hgb_day)
            check("Trends: the same result from the file and the sync is one point, with both sources (#96)",
                  len(on_day) == 1 and on_day[0].count(";") == 1 and "same result" in on_day[0], on_day)
            js("setRange(Date.parse('2025-01-01'), Date.parse('2025-12-31')); state.sel = new Set(['Hemoglobin']); state.tables.add('Hemoglobin'); switchTab('labs')")
            page.wait_for_selector("#charts table.data tbody tr", timeout=10000)
            rows = js("[...document.querySelectorAll('#charts .chart')].filter(c => c.querySelector('.name').textContent === 'Hemoglobin')"
                      ".map(c => c.querySelectorAll('tbody tr').length)")
            check("…one Hemoglobin chart with 2025's results (the C-CDA's g/dL one has none then), one row per result",
                  [n for n in rows if n] == [2], rows)
            check("…the table says the 4 March result came from one more source, and lists both",
                  "same result from 1 more source" in page.inner_text("#charts") and "Sources" in page.inner_text("#charts .source"),
                  page.inner_text("#charts")[:400])
            page.set_viewport_size({"width": 375, "height": 800})
            day_ev = js("(async () => { await loadDetails(DATA.events.map(e => e.id)); return DATA.events.find(e => e.k === 'lab' && e.t.startsWith('2025-03-04') && "
                        "(DATA.details[e.id]?.ids || []).some(i => (DATA.details[i]?.same || []).length)).id; })()")
            js(f"openEvent(byId.get({day_ev}))")
            page.wait_for_selector("#dbody .same-line", timeout=5000)
            check("the drawer: the result once, saying the same result also came from the sync, with Undo link",
                  "the same result also came from" in page.inner_text("#dbody .same")
                  and page.locator("#dbody .same button", has_text="Undo link").count() == 1
                  and js("[...document.querySelectorAll('#dbody td')].filter(td => td.title === 'Hemoglobin').length") == 1,
                  page.inner_text("#dbody")[:500])
            check("…and fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.click("#dbody .same button:has-text('Undo link')")
            page.wait_for_selector("#dbody .same-line.possible", timeout=20000)   # reloaded, the panel reopened
            check("undoing the link shows both again, flagged as possibly the same", len(js(hgb_day)) == 2
                  and page.locator("#dbody .same-line.possible button", has_text="Same result").count() >= 1, js(hgb_day))
            page.click("#dbody .same-line.possible:has-text('Hemoglobin') button:has-text('Same result')")
            page.wait_for_selector("#dbody .same-line:not(.possible)", timeout=20000)
            check("…and linking it again from the drawer shows it once", len(js(hgb_day)) == 1, js(hgb_day))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.goto(base + "/sources.html#portals")
            page.select_option("#profile", label="Second person (owner)")
            page.wait_for_function("document.getElementById('connList').textContent.includes('New records come in on a schedule')", timeout=20000)
            imports = lambda: js(f"fetch('api/profiles/{conn_pid}/connections').then(r => r.json()).then(j => j.connections[0].imports)")
            fake.add({"resourceType": "Observation", "status": "final", "effectiveDateTime": "2026-11-12T08:00:00Z",
                      "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "laboratory"}]}],
                      "code": {"coding": [{"system": "http://loinc.org", "code": "2345-7"}], "text": "Glucose"},
                      "valueQuantity": {"value": 101, "unit": "mg/dL"}}, patient="pat-1")
            page.get_by_role("button", name="Sync now").click()
            for _ in range(100):
                if imports() == 2:
                    break
                page.wait_for_timeout(200)
            check("Sync now brings the new result in, as an import of its own", imports() == 2, imports())
            page.set_viewport_size({"width": 375, "height": 800})
            page.reload()
            page.wait_for_function("document.getElementById('connList').textContent.includes('Fake Epic sandbox')", timeout=10000)
            page.get_by_role("button", name="What it pulls…").click()
            page.wait_for_selector(".conn-options", timeout=10000)
            check("connected sources fit 375 px, with a connection's choices open",
                  js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.check(".conn-options input[value=visits]")   # granted at sign-in? no: unticked, so it asks to sign in again
            check("a kind the access doesn't cover says it needs signing in again",
                  "Needs signing in again" in page.inner_text(".conn-options"), page.inner_text(".conn-options")[:300])
            page.uncheck(".conn-options input[value=visits]")
            page.uncheck(".conn-options input[value=medications]")
            check("…and a kind turned off offers to remove what it brought",
                  page.get_by_role("button", name="Remove medications it brought").count() == 1)
            page.locator(".conn-options").get_by_role("button", name="Save").click()
            page.wait_for_function("document.getElementById('connList').textContent.includes('Saved. The next sync')", timeout=10000)
            check("…saved for the next sync", "medications" not in page.inner_text(".conn-chosen"), page.inner_text(".conn-chosen"))
            # ---- a connection made before #228: its access doesn't cover the new kinds, so they ask for a sign-in (#228)
            conn_id = js(f"fetch('api/profiles/{conn_pid}/connections').then(r => r.json()).then(j => j.connections[0].id)")
            db = dbm.connect()
            was_228 = dict(db.execute("SELECT scopes, kinds FROM connections WHERE id=?", (conn_id,)).fetchone())
            db.execute("UPDATE connections SET scopes=?, kinds=? WHERE id=?", (" ".join(
                x for x in was_228["scopes"].split() if x.split("/")[-1].split(".")[0] not in
                ("Immunization", "AllergyIntolerance", "MedicationDispense", "Appointment", "ImagingStudy")),
                json.dumps(["labs", "vitals", "problems", "documents", "claims"]), conn_id))
            db.close()
            theme_before = js("document.documentElement.dataset.theme || null")
            bad, serious, said = [], [], None
            for width in (1400, 375):
                page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                for theme in ("light", "dark"):
                    page.reload()
                    page.wait_for_function("document.getElementById('connList').textContent.includes('Fake Epic sandbox')", timeout=10000)
                    js("t => { document.documentElement.dataset.theme = t; }", theme)
                    page.get_by_role("button", name="What it pulls…").click()
                    page.wait_for_selector(".conn-options", timeout=10000)
                    said = said or js("[...document.querySelectorAll('.conn-options .conn-kind')].filter(l => ['appointments', 'allergies', 'fills', "
                                      "'immunizations'].includes(l.querySelector('input').value)).map(l => [l.querySelector('input').checked, "
                                      "!l.querySelector('input').disabled, l.textContent.includes('Needs signing in again.')])")
                    page.locator(".conn-options").scroll_into_view_if_needed()
                    clear_of_bar(page)
                    if width < 400:
                        bad += phone_fits(page, f"a connection's kinds ({theme})")
                    serious += axe_check(page, f"a connection's kinds at {width} px ({theme})")[0]
                    if os.environ.get("SANGUITY_SHOTS"):   # screenshots when asked, as #182's
                        page.locator(".conn-options .conn-kinds").screenshot(path=os.path.join(os.environ["SANGUITY_SHOTS"], f"228-kinds-{width}-{theme}.png"))
            check("a connection made before: the new kinds are unticked, and say they need signing in again (#228)",
                  said == [[False, True, True]] * 4, said)
            check("…with no Remove button for kinds it never pulled",
                  page.locator(".conn-options .conn-remove button", has_text="immunizations").count() == 0
                  and page.locator(".conn-options .conn-remove button", has_text="Remove medications").count() == 1, page.inner_text(".conn-options .conn-remove"))
            check("…the kinds fit 375 px, in light and dark", not bad, bad[:6])
            check("…and pass axe-core's WCAG A and AA rules at 1400 and 375 px, in light and dark", not serious, serious[:6])
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            db = dbm.connect()
            db.execute("UPDATE connections SET scopes=?, kinds=? WHERE id=?", (was_228["scopes"], was_228["kinds"], conn_id))
            db.close()
            page.set_viewport_size({"width": 1400, "height": 900})
            fake.revoke_all("pat-1")    # the patient ends access in their portal
            fake.expire_access_tokens()
            page.reload()
            page.get_by_role("button", name="Sync now").click()
            page.wait_for_timeout(1500)
            page.reload()
            page.wait_for_selector("#reconnect:not([hidden])", timeout=20000)
            check("when access ends, the page asks to sign in again", "access ended" in page.inner_text("#reconnect")
                  and page.get_by_role("button", name="Sign in again").count() == 1, page.inner_text("#connList")[:300])
            page.click("#srcNav a[data-group=overview]")   # Sources' overview (#166): every way in, the one that stopped first
            page.wait_for_selector("#srcWarn .src-alert", timeout=10000)
            ov = page.inner_text("#srcOverview")
            check("the Sources overview warns that the source stopped, and lists it with when it last brought records",
                  "access ended" in page.inner_text("#srcWarn") and "Fake Epic sandbox" in ov and "Last brought records" in ov
                  and page.locator("#srcList .src-row.bad", has_text="Fake Epic sandbox").count() == 1, ov[:500])
            page.click("#srcWarn a")
            page.wait_for_function("location.hash.startsWith('#conn-') && getComputedStyle(document.getElementById('mine')).display !== 'none'", timeout=5000)
            check("…its link opens the connection under Portals and health plans",
                  js("document.querySelector('#srcNav a[aria-current=page]').dataset.group") == "portals")
            page.set_viewport_size({"width": 375, "height": 800})
            page.click("#srcNav a[data-group=overview]")
            settle(200)
            check("…and the overview fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            prev_profile = js("localStorage.getItem('ht-profile')")
            js(f"localStorage.setItem('ht-profile', '{conn_pid}')")   # the profile's main page warns too (#102)
            page.goto(base + "/")
            page.wait_for_selector("#syncWarn:not([hidden])", timeout=20000)
            warn = page.inner_text("#syncWarn")
            check("the profile's main page warns that the source stopped syncing, with a link to it",
                  "access ended" in warn and js("document.querySelector('#syncWarn a').getAttribute('href')").startswith(f"sources.html?profile={conn_pid}#conn-"), warn)
            page.set_viewport_size({"width": 375, "height": 800})
            check("…and the warning fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            js(f"api('profiles/{conn_pid}/settings/sync.attachTypes', {{method: 'DELETE'}})")
            js(f"localStorage.setItem('ht-profile', {json.dumps(prev_profile)} || '')")
            check("only the source's own host was contacted", fake.hosts() == {f"127.0.0.1:{fake.port}"}, fake.hosts())

            # ---- outside connections (#97, #59): the Admin page lists every host the install may contact, by purpose
            page.goto(base + "/settings.html#outside")   # its old address (#167)
            page.wait_for_url("**/admin.html#outside", timeout=10000)
            page.wait_for_function("document.getElementById('outsideBody').textContent.includes('May contact now')", timeout=10000)
            out = page.inner_text("#outside")
            check("Admin says plainly that nothing else is contacted", "Nothing else leaves this server" in out, out[:200])
            check("…lists the connected source with its host, scopes and last contact",
                  "Fake Epic sandbox" in out and f"127.0.0.1:{fake.port}" in out and "patient/Observation" in out
                  and "Second person" in out and "Last contact" in out, out[:600])
            check("…and the Health Link's host", f"localhost:{SHL.port}" in out and "SMART Health Link" in out, out[-600:])
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the outside connections fit 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            allowed.discard(origin(fake.fhir_base))
            fake.stop()
            js("(p => p == null ? localStorage.removeItem('ht-profile') : localStorage.setItem('ht-profile', p))", profile_before_96)   # as before #96's visit

            # ---- insurers (#92): a Blue Button bulk export (NDJSON) uploaded on the import page, then a health plan's
            # patient-access API (the fake, standing in for Blue Button's sandbox: a confidential client) set up as an
            # insurer source and connected; both bring their claims onto the Costs tab
            from tests.fakefhir import PAYER_FIXTURES
            bb_dir = os.path.join(ROOT, "tests", "fixtures", "importers", "fhir", "blue-button")
            page.goto(base + f"/import.html?profile={conn_pid}")
            page.wait_for_selector("#importer", state="visible", timeout=10000)
            page.click("#tab-files")
            bb_files = []
            for n in sorted(os.listdir(bb_dir)):
                with open(os.path.join(bb_dir, n), "rb") as f:
                    bb_files.append({"name": n, "mimeType": "application/x-ndjson", "buffer": f.read()})
            page.set_input_files("#pickFiles", files=bb_files)
            page.wait_for_function("!document.getElementById('go').disabled", timeout=5000)
            cards = js("document.querySelectorAll('.import-job').length")
            page.click("#go")
            page.wait_for_function(f"document.querySelectorAll('.import-job').length > {cards} && "
                                   f"/^(Ready to import|All duplicates|Not imported)/.test({last_card}.textContent)", timeout=30000)
            js(f"{last_card}.querySelector('.imp-actions .primary')?.click()")
            page.wait_for_function(f"/^(Imported|Not imported)/.test({last_card}.textContent)", timeout=30000)
            card = js(f"{last_card}.textContent")
            db = dbm.connect()
            up = db.execute("SELECT id FROM uploads WHERE profile_id=? ORDER BY id DESC", (conn_pid,)).fetchone()
            job = db.execute("SELECT state, importer FROM jobs WHERE upload_id=?", (up["id"],)).fetchone()
            db.close()
            check("a Blue Button export (NDJSON) uploads and is imported, as FHIR, with its claims",
                  card.startswith("Imported.") and job is not None and tuple(job) == ("done", "fhir") and "claim" in card, (card, job and tuple(job)))

            payer = FakeFHIR(fixtures=PAYER_FIXTURES).start()
            allowed.add(origin(payer.fhir_base))   # its sign-in page too
            payer.signin_patient = "mem-1"
            payer.register_client("sanguity-payer-check", [base + "/connect/return"], secret="fictional-payer-secret")
            page.goto(base + "/admin.html#apps")
            page.wait_for_function("!document.getElementById('admin').hidden && document.getElementById('returnAddr').textContent.length > 5", timeout=10000)
            page.select_option("#fVendor", "insurer")
            guide_text = page.inner_text("#guide")
            check("an insurer source has its own guide: CARIN Blue Button scopes, Blue Button's sandbox, payers' portals marked unverified",
                  "Blue Button" in guide_text and "patient/ExplanationOfBenefit.read" in guide_text and "unverified" in guide_text
                  and "sandbox.bluebutton.cms.gov" in guide_text, guide_text[:400])
            check("…and its scopes ask for claims, coverage and the patient only",
                  js("document.getElementById('fScopes').value") == js("SETUP.insurer_scopes")
                  and "Observation" not in js("document.getElementById('fScopes').value"), js("document.getElementById('fScopes').value"))
            page.fill("#fName", "Fake health plan")
            page.fill("#fIssuer", payer.fhir_base)
            page.fill("#fClient", "sanguity-payer-check")
            page.select_option("#fAuth", "confidential")
            page.fill("#fSecret", "fictional-payer-secret")
            page.click("#fSave")
            page.wait_for_function("document.getElementById('sourceList').textContent.includes('Fake health plan')", timeout=10000)
            page.locator("#sourceList .prof", has_text="Fake health plan").get_by_role("button", name="Test this connection").click()
            page.wait_for_function("[...document.querySelectorAll('#sourceList .prof')].some(p => p.textContent.includes('Fake health plan') && "
                                   "(p.textContent.includes('Discovery works') || p.textContent.includes('tested ')))", timeout=10000)
            page.goto(base + "/sources.html#portals")
            page.select_option("#profile", label="Second person (owner)")
            page.wait_for_selector("#sourceButtons button:has-text('Fake health plan')", timeout=10000)
            page.click("#sourceButtons button:has-text('Fake health plan')")   # what to pull (#99), then sign in
            page.wait_for_selector(".conn-choose", timeout=10000)
            page.locator(".conn-choose").get_by_role("button", name="Sign in at").click()
            page.wait_for_url("**/sources.html?*#portals", timeout=20000)
            page.wait_for_selector("#import-status button.primary", timeout=30000)
            check("connecting the health plan previews its claims like an upload",
                  "Ready to import" in page.inner_text("#import-status") and "claim" in page.inner_text("#import-status"),
                  page.inner_text("#import-status")[:300])
            page.click("#import-status button.primary")
            page.wait_for_function("document.getElementById('import-status').textContent.includes('Imported.')", timeout=30000)
            check("…pulling only Patient, ExplanationOfBenefit and Coverage",
                  {r.path for r in payer.requests if r.path.startswith("/fhir/") and r.path != "/fhir/metadata" and ".well-known" not in r.path}
                  == {"/fhir/Patient/mem-1", "/fhir/ExplanationOfBenefit", "/fhir/Coverage"},
                  sorted({r.path for r in payer.requests}))
            prev_profile = js("localStorage.getItem('ht-profile')")   # put back after: later checks rely on it
            js(f"localStorage.setItem('ht-profile', '{conn_pid}')")
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && PROFILE && PROFILE.name === 'Second person' && "
                                   "TABS.some(t => !document.getElementById('view-' + t).hidden)", timeout=20000)
            js("switchTab('costs')")
            page.wait_for_function("!document.getElementById('view-costs').hidden", timeout=5000)
            js("applyPreset('all')")
            settle(200)
            rows = js("[...document.querySelectorAll('#costTable tbody tr')].map(tr => [...tr.cells].map(td => td.textContent))")
            ins = set(js("DATA.costs.map(c => c.ins)"))
            check("the Costs tab shows the claims from the Blue Button export and from the health plan's API",
                  {"Medicare (fictional sandbox)", "Lakeview Health Plan (fictional)"} <= ins, sorted(ins))
            mri = [r for r in rows if r[1] == "MRI lumbar spine without contrast"]
            check("…a denied claim from each, marked denied", len(mri) == 2 and all(r[7] == "denied" for r in mri), mri)
            fills = [r for r in rows if r[1] == "Atorvastatin Calcium 20mg tablet"]
            check("…and the pharmacy fills, under Pharmacy", len(fills) == 2 and all(r[3] == "Pharmacy" for r in fills), fills)
            check("only the health plan's own host was contacted for it", payer.hosts() == {f"127.0.0.1:{payer.port}"}, payer.hosts())
            allowed.discard(origin(payer.fhir_base))
            payer.stop()
            js(f"localStorage.setItem('ht-profile', {json.dumps(prev_profile or '')})" if prev_profile else "localStorage.removeItem('ht-profile')")

            # ---- connecting a portal or health plan made easier (#165): Admin's walk-through per vendor (numbered steps,
            # the values to paste with Copy, the form as a step of its own, Test this connection last), a source saved as
            # waiting for the vendor's review, and what an everyday user sees for it on Portals and health plans (what's
            # coming, and to upload the portal's export meanwhile, no connect button and no setup words), at 1400 and
            # 375 px in light and dark; and the upload page's note on an iPhone's Health Records
            look_165 = r"""sel => {
              const rgb = c => { const x = document.createElement('canvas').getContext('2d'); x.fillStyle = c; x.fillRect(0, 0, 1, 1); return [...x.getImageData(0, 0, 1, 1).data]; };
              const bg = n => { for (; n; n = n.parentElement) { const c = rgb(getComputedStyle(n).backgroundColor); if (c[3] > 0) return c; } return [255, 255, 255, 255]; };
              const lum = c => { const v = c.slice(0, 3).map(x => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; }); return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
              const ratio = n => { const [a, b] = [lum(rgb(getComputedStyle(n).color)), lum(bg(n))].sort((p, q) => q - p); return Math.round((a + 0.05) / (b + 0.05) * 10) / 10; };
              const ns = [...document.querySelectorAll(sel)].filter(n => n.getClientRects().length);
              return ns.length ? Math.min(...ns.map(ratio)) : 0;
            }"""
            theme_before_165 = js("document.documentElement.dataset.theme || null")
            page.set_viewport_size({"width": 1400, "height": 900})
            page.goto(base + "/admin.html#apps")
            page.wait_for_function("!document.getElementById('admin').hidden && document.querySelector('#walk > li')", timeout=10000)
            walk = lambda: js("""(() => { const w = document.getElementById('walk'); return {
                n: w.children.length, steps: SETUP.guides[document.getElementById('fVendor').value].steps.length,
                copies: [...w.querySelectorAll('.walk-copy')].map(c => [c.querySelector('.walk-k').textContent, c.querySelector('code').textContent,
                                                                     !!c.querySelector('button')]),
                form: !!w.querySelector(':scope > li #srcFields #fClient'), last: w.lastElementChild.querySelector('.walk-t').textContent,
                test: !!w.lastElementChild.querySelector('#walkTest')}; })()""")
            per = {}
            for v in ("epic", "oracle", "insurer", "other"):
                page.select_option("#fVendor", v)
                per[v] = walk()
            check("Admin's walk-through (#165): every vendor's steps numbered, then the form as a step, then Test this connection",
                  all(w["n"] == w["steps"] + 2 and w["steps"] >= 4 and w["form"] and w["last"] == "Test this connection" and w["test"]
                      for w in per.values()), per)
            check("…each step with the values to paste and a Copy button: the return address for every vendor, the types Epic's "
                  "scopes read, an insurer's scopes, a sandbox's FHIR base",
                  all(any(c[0] == "Return address" and c[1] == base + "/connect/return" and c[2] for c in w["copies"]) for w in per.values())
                  and any(c[0] == "Types it reads" and "Observation" in c[1] for c in per["epic"]["copies"])
                  and any(c[0] == "Scopes" and "patient/ExplanationOfBenefit.read" in c[1] for c in per["insurer"]["copies"])
                  and any(c[0] == "Sandbox FHIR base" and "bluebutton" in c[1] for c in per["insurer"]["copies"]),
                  {k: w["copies"] for k, w in per.items()})
            page.select_option("#fVendor", "epic")
            ret = page.locator("#walk .walk-copy", has_text="Return address")
            ret.get_by_role("button", name="Copy the return address").click()
            page.wait_for_function("document.querySelector('#walk .walk-copy .msg').textContent !== ''", timeout=5000)
            said = ret.locator(".msg").inner_text()
            check("…Copy copies it, or selects it where the browser keeps the clipboard from the page", said in ("Copied.", "Selected: press Ctrl+C or ⌘C."), said)
            page.fill("#fScopes", "launch/patient patient/Patient.read patient/Observation.read")
            check("…the types to pick follow the scopes typed in", js("document.querySelector('#walk [data-paste=types]').textContent") == "Patient, Observation",
                  js("document.querySelector('#walk [data-paste=types]').textContent"))
            check("…and the last step can't test anything until the source is saved", js("document.getElementById('walkTest').disabled"))
            # saved while the vendor reviews the app: first refused without a client ID, then saved as waiting
            page.fill("#fName", "Lakeside Health (fictional)")
            page.fill("#fIssuer", "https://fhir.lakeside.example/api/FHIR/R4")
            page.fill("#fClient", "")
            page.click("#fSave")
            page.wait_for_function("document.getElementById('fMsg').textContent.includes('Waiting for the vendor')", timeout=10000)
            check("a source with no client ID yet is refused, saying to tick Waiting for the vendor's review",
                  "bad" in js("document.getElementById('fMsg').className"), page.inner_text("#fMsg"))
            problems[:] = [x for x in problems if not ("HTTP 400: " in x and "/api/connect/sources" in x)
                           and "status of 400" not in x]   # refused on purpose: that was the point
            page.check("#fWaiting")
            page.click("#fSave")
            page.wait_for_function("document.getElementById('sourceList').textContent.includes('Lakeside Health (fictional)') && "
                                   "document.getElementById('walkTest').textContent === 'Test Lakeside Health (fictional)'", timeout=10000)
            row = page.locator("#sourceList .prof", has_text="Lakeside Health (fictional)")
            check("…then saved as waiting: tagged in the list, the walk-through staying on it at its last step, ready to test",
                  "Waiting for review" in row.inner_text() and "people are told to upload" in row.inner_text()
                  and js("document.activeElement.id") == "walkTest" and not js("document.getElementById('walkTest').disabled")
                  and "waiting for the vendor's review" in page.inner_text("#fMsg"), [row.inner_text()[:300], page.inner_text("#fMsg")])
            wait_id = js("SETUP.sources.find(s => s.name === 'Lakeside Health (fictional)').id")
            for width in (1400, 375):
                page.set_viewport_size({"width": width, "height": 800 if width == 375 else 900})
                for theme in ("light", "dark"):
                    js("t => { document.documentElement.dataset.theme = t; }", theme)
                    settle(150)
                    bad = phone_fits(page, f"Admin walk-through, {theme}") if width == 375 else []
                    looks = {s: js(look_165, s) for s in ("#walk .walk-t", "#walk .walk-v", "#walk .walk-k", "#walk .walk-step > p",
                                                         "#sourceList .walk-tag", "label.walk-wait")}
                    wide = js("document.documentElement.scrollWidth")
                    check(f"…the walk-through at {width} px, {theme}: readable steps, values, tags and the waiting switch"
                          + (", nothing cut off, overlapping or scrolling sideways" if width == 375 else ""),
                          not bad and wide <= width and all(v >= 4.5 for v in looks.values()), [bad[:6], wide, looks])
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before_165)
            page.set_viewport_size({"width": 1400, "height": 900})
            # what an editor who isn't an admin sees for it (a second account, removed afterwards)
            from server import db as dbm, security
            sdb = dbm.connect()
            uid3 = sdb.execute("INSERT INTO users(username, pw_hash, is_admin, created) VALUES(?, ?, 0, 0)", ("lee", security.hash_password(PW2))).lastrowid
            sdb.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (uid3, conn_pid))
            lctx = browser.new_context(viewport={"width": 1400, "height": 900}, bypass_csp=True)
            lp = lctx.new_page()
            lp.on("pageerror", lambda e: problems.append(f"script error (lee): {e}"))
            lp.goto(base + "/login.html")
            lp.fill("#u", "lee")
            lp.fill("#p", PW2)
            lp.click("#b")
            lp.wait_for_function("typeof PROFILE !== 'undefined' && PROFILE", timeout=20000)
            lp.goto(base + f"/sources.html?profile={conn_pid}#portals")
            lp.wait_for_selector(f"#wait-{wait_id}", timeout=10000)
            note = lp.inner_text(f"#wait-{wait_id}")
            setup_words = re.compile(r"client ID|FHIR|scope|discovery|redirect|issuer|PUBLIC_ORIGINS|Admin|\.env|vendor's developer", re.I)
            check("Portals and health plans: a source waiting for review says it's coming and to upload the export meanwhile, "
                  "in place of a connect button",
                  "isn't ready to connect yet: it's waiting for Epic's review" in note and "upload them under Sources → Upload files" in note
                  and not lp.locator("#sourceButtons button", has_text="Lakeside").count()
                  and lp.locator(f"#wait-{wait_id} a", has_text="Upload files").get_attribute("href") == "#upload", note)
            check("…in plain words, with nothing about the server's setup for someone who isn't an admin (#167)",
                  not setup_words.search(note) and lp.is_hidden("#adminNote"), setup_words.findall(note))
            for width in (1400, 375):
                lp.set_viewport_size({"width": width, "height": 800 if width == 375 else 900})
                for theme in ("light", "dark"):
                    lp.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
                    lp.wait_for_timeout(150)
                    bad = phone_fits(lp, f"Portals waiting, {theme}") if width == 375 else []
                    looks = [lp.evaluate(look_165, s) for s in (f"#wait-{wait_id} p", f"#wait-{wait_id} .tag", f"#wait-{wait_id} a")]
                    check(f"…the waiting note at {width} px, {theme}: readable" + (", and it fits" if width == 375 else ""),
                          not bad and min(looks) >= 4.5 and lp.evaluate("document.documentElement.scrollWidth") <= width, [bad[:6], looks])
            lp.set_viewport_size({"width": 1400, "height": 900})
            lp.click(f"#wait-{wait_id} a")
            lp.wait_for_function("document.querySelector('main.src').dataset.group === 'upload'", timeout=5000)
            apple = lp.inner_text("#appleRecords")
            check("…its Upload files opens the upload page, which says an iPhone's Apple Health export holds its Health Records",
                  lp.evaluate("location.hash") == "#upload" and lp.is_visible("#appleRecords") and "Export All Health Data" in apple
                  and "Health Records" in apple and "FHIR" in apple, apple)
            lctx.close()
            sdb.execute("DELETE FROM users WHERE id=?", (uid3,))
            sdb.close()
            # the review done: no longer waiting, so not shown to people until it has a client ID and a test; then removed
            row = page.locator("#sourceList .prof", has_text="Lakeside Health (fictional)")
            row.get_by_role("button", name="Review done").click()
            page.wait_for_function("document.querySelector('#sourceList').textContent.includes('Paste the client ID')", timeout=10000)
            check("Review done needs the client ID first, and says so", "Waiting for review" in row.inner_text(), row.inner_text()[:200])
            problems[:] = [x for x in problems if not ("HTTP 400: " in x and "/api/connect/sources" in x)
                           and "status of 400" not in x]   # refused on purpose: that was the point
            page.evaluate(f"api('connect/sources/{wait_id}', {{method: 'DELETE'}}).then(r => r.status)")
            page.reload()
            page.wait_for_function("typeof SETUP !== 'undefined' && SETUP && !SETUP.sources.some(s => s.name === 'Lakeside Health (fictional)')", timeout=10000)

            # ---- #246: the Timeline list's row is .tl-row, so the pages' rows of buttons (.row) have no divider under them
            # and no pointer, and what's typed into a field isn't its label's semibold; every group of each page
            rows_typed = r"""(() => {
              const name = e => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + ` "${(e.textContent || e.value || '').replace(/\s+/g, ' ').trim().slice(0, 30)}"`, out = [];
              for (const r of document.querySelectorAll('main .row')) {
                const cs = getComputedStyle(r);
                if (r.checkVisibility() && (parseFloat(cs.borderBottomWidth) > 0 || cs.cursor === 'pointer')) out.push(`row ${name(r)}: ${cs.borderBottomWidth} border, ${cs.cursor} cursor`);
              }
              for (const f of document.querySelectorAll('main :is(input:not([type=checkbox],[type=radio],[type=range],[type=file],[type=hidden],[type=color],[type=submit],[type=button]),select,textarea)'))
                if (f.checkVisibility() && getComputedStyle(f).fontWeight !== '400') out.push(`field ${name(f)}: weight ${getComputedStyle(f).fontWeight}`);
              return out;
            })()"""
            # #309: and taking .row's padding off left the rows of buttons with no space under them ("Back up now" sat on
            # Admin → Backups' Schedule heading): each row is 8 px or more from what follows it, in its card, at 1400 and 320 px
            rows_gap = r"""(() => {
              const vis = e => e.checkVisibility() && e.getBoundingClientRect().height > 0, out = [];
              const name = e => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + ` "${(e.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 24)}"`;
              for (const r of document.querySelectorAll('main .row')) {
                if (!vis(r)) continue;
                const bottom = r.getBoundingClientRect().bottom;
                let n = r, next = null;
                while (!next && n.parentElement && !n.matches('section, .card, main, details, li, td')) {
                  for (let s = n.nextElementSibling; s && !next; s = s.nextElementSibling) if (vis(s) && s.getBoundingClientRect().top >= bottom - 2) next = s;
                  n = n.parentElement;
                }
                const gap = next && next.getBoundingClientRect().top - bottom;
                if (next && gap < 7.5) out.push(`row ${name(r)}: ${gap.toFixed(1)} px above ${name(next)}`);
              }
              return out;
            })()"""
            bad, seen, gaps = [], [], []
            for width in (1400, 320):
                page.set_viewport_size({"width": width, "height": 900})
                for path in (f"sources.html?profile={conn_pid}", "sharing.html?profile=1", "settings.html?profile=1", "account.html", "admin.html"):
                    page.goto(base + "/" + path)
                    page.wait_for_selector(".set-nav a[data-group]", timeout=10000)
                    settle(300)
                    for g in js("[...document.querySelectorAll('.set-nav a[data-group]')].map(a => a.dataset.group)"):
                        page.click(f".set-nav a[data-group={g}]")
                        settle(150)
                        if width == 1400:
                            bad += [f"{path.split('.')[0]} #{g}: {x}" for x in js(rows_typed)]
                            seen.append(g)
                        gaps += [f"{path.split('.')[0]} #{g} at {width} px: {x}" for x in js(rows_gap)]
            page.set_viewport_size({"width": 1400, "height": 900})
            check("every group of Sources, Sharing, Settings, Account and Admin: rows of buttons have no divider or pointer, "
                  "and typed values are weight 400 (#246)", len(seen) >= 25 and not bad, bad[:8] or f"{len(seen)} groups")
            check("…and each row of buttons is at least 8 px above what follows it, at 1400 and 320 px (#309)", not gaps, gaps[:8])

            # ---- every page at 375 px (#61): each records tab (Labs as charts and as a heatmap) with its filters open, an
            # event's drawer and the top bar's menus; every group of Sources (the profile with connected sources and
            # imports), Sharing, Settings, Account and Admin; and the sign-in page with no session; in light and dark:
            # nothing scrolls sideways, and no control is cut off at the edge or overlaps another (phone_fits). The
            # shared page (share.html) is checked the same way with the share links, under the real CSP.
            phone_known = ()   # problems owned by another issue, each with the issue's number beside it: none now
            theme_was = js("localStorage.getItem('ht-theme')")
            page.set_viewport_size({"width": 375, "height": 800})
            for theme in ("light", "dark"):
                js(f"localStorage.setItem('ht-theme', '{theme}')")
                page.goto(base + "/")
                page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && typeof booted !== 'undefined' && booted", timeout=20000)
                bad = []
                for t in ("overview", "health", "timeline", "heatmap", "labs", "costs"):
                    open_tab(t)
                    settle(250)
                    bad += phone_fits(page, t, phone_known)
                    if page.is_visible("#ftoggle"):
                        page.click("#ftoggle")
                        settle(150)
                        bad += phone_fits(page, t + " with its filters open", phone_known)
                        page.click("#ftoggle")
                    if t == "timeline":
                        page.click("#list .tl-row >> nth=0")
                        page.wait_for_function("document.getElementById('drawer').classList.contains('open')", timeout=5000)
                        settle(250)
                        bad += phone_fits(page, "the drawer", phone_known)
                        page.keyboard.press("Escape")
                        settle(250)
                for m in ("#addMenuBtn", "#userMenuBtn"):
                    page.click(m)
                    bad += phone_fits(page, m + " open", phone_known)
                    page.keyboard.press("Escape")
                check(f"at 375 px, {theme}: every records tab, with its filters open, the drawer and the menus fit, no control cut off or overlapping (#61)",
                      js("document.documentElement.dataset.theme") == theme and not bad, bad[:8] or "")
                bad, seen = [], []
                for path in (f"sources.html?profile={conn_pid}", "sharing.html?profile=1", "settings.html?profile=1", "account.html", "admin.html"):
                    page.goto(base + "/" + path)
                    page.wait_for_selector(".set-nav a[data-group]", timeout=10000)
                    settle(300)
                    for g in js("[...document.querySelectorAll('.set-nav a[data-group]')].map(a => a.dataset.group)"):
                        page.click(f".set-nav a[data-group={g}]")
                        settle(150)
                        bad += phone_fits(page, f"{path.split('.')[0]} #{g}", phone_known)
                        seen.append(f"{path.split('.')[0]}#{g}")
                ctx = browser.new_context(viewport={"width": 375, "height": 800}, color_scheme=theme)   # no session: the sign-in page
                lp = ctx.new_page()
                lp.goto(base + "/login.html")
                lp.wait_for_selector("#u", timeout=10000)
                bad += phone_fits(lp, "sign-in", phone_known)
                ctx.close()
                check(f"at 375 px, {theme}: every group of Sources, Sharing, Settings, Account and Admin, and the sign-in page, fit, "
                      "no control cut off or overlapping (#61)", len(seen) >= 25 and not bad, bad[:8] or f"{len(seen)} groups")
            js(f"localStorage.setItem('ht-theme', {json.dumps(theme_was)})" if theme_was else "localStorage.removeItem('ht-theme')")
            page.set_viewport_size({"width": 1400, "height": 900})

            # ---- WCAG 2.2 AA (#202, docs/accessibility.md): axe-core's WCAG 2.0, 2.1 and 2.2 A and AA rules (axe_check) on
            # every page and its main states, in light and dark: each records tab (Labs as charts and as a heatmap, each also
            # as its table), an event's drawer for several kinds, the app's own dialog (ask(), with a box to type in), the
            # More… period menu, a kind's ⋯ menu and the top bar's two menus; every group of Sources, Sharing, Settings,
            # Account and Admin; the guide and the provider finder; the sign-in page with no session. A serious or critical
            # finding fails; moderate and minor ones are listed under a passing check. (The shared page, setup and a fresh
            # install's empty tabs are checked where they're opened: share links and first run.) Then the keyboard: Tab and
            # Shift+Tab through each records tab and page at 375 px, every stop showing focus and none under a sticky bar;
            # every page at 320 px (400% zoom) and with WCAG's text spacing fitting; and in forced colours, the filled
            # buttons keep an edge. On each of them too, no link is in the browser's own blue (#247: links are in the accent).
            t_axe, axe_bad, axe_other, axe_seen = time.time(), [], [], []
            ua_links, links_seen = [], [0]
            outlines, outline_bad = [], []   # #331: each page's heading outline (in light: it's the same in dark)
            # Chromium's own link colour, light and dark (a visited link's purple reads as this to scripts too)
            UA_LINKS = """(() => { const as = [...document.querySelectorAll('a[href]')].filter(a => a.checkVisibility());
              return [as.length, as.filter(a => ['rgb(0, 0, 238)', 'rgb(158, 158, 255)'].includes(getComputedStyle(a).color)).map(a => a.textContent.trim().slice(0, 40))]; })()"""
            def axe_at(pg, label, outline=False):
                bad_, other_ = axe_check(pg, label)
                axe_bad.extend(bad_); axe_other.extend(other_); axe_seen.append(label)
                if outline and label.startswith("light "):
                    o, ob = heading_outline(pg, label[6:])
                    outlines.append(o); outline_bad.extend(ob)
                n, blue = pg.evaluate(UA_LINKS)
                links_seen[0] += n
                ua_links.extend(f"{label}: “{t}”" for t in blue)
            theme_was = js("localStorage.getItem('ht-theme')")
            for theme in ("light", "dark"):
                js(f"localStorage.setItem('ht-theme', '{theme}')")
                page.goto(base + "/")
                page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && typeof booted !== 'undefined' && booted", timeout=20000)
                for t in ("overview", "health", "timeline", "heatmap", "labs", "costs"):
                    open_tab(t)
                    settle(300)
                    axe_at(page, f"{theme} {t}", outline=True)
                    if t == "heatmap" or (t == "labs" and js("document.querySelectorAll('#charts .chart .view-seg').length")):   # as its table (#60)
                        seg = "#hmHd .view-seg button" if t == "heatmap" else "#charts .chart .view-seg button"
                        page.click(f"{seg} >> nth=1")
                        settle(200)
                        axe_at(page, f"{theme} {t} as a table")
                        page.click(f"{seg} >> nth=0")
                        settle(200)
                for k in ("lab", "claim", "med", "letter", "visit"):   # an event's drawer
                    if js("k => !!EV.find(e => e.k === k && !e.hidden)", k):
                        js("k => openEvent(EV.find(e => e.k === k && !e.hidden))", k)
                        settle(250)
                        axe_at(page, f"{theme} the drawer, {k}")
                        page.keyboard.press("Escape")
                        settle(150)
                open_tab("timeline")
                settle(200)
                page.click("#periodMoreBtn")
                axe_at(page, f"{theme} the More… period menu")
                page.keyboard.press("Escape")
                page.click("#chips .kind-more >> nth=0")
                axe_at(page, f"{theme} a kind's ⋯ menu")
                page.keyboard.press("Escape")
                for m in ("#addMenuBtn", "#userMenuBtn"):
                    page.click(m)
                    axe_at(page, f"{theme} {m} open")
                    page.keyboard.press("Escape")
                js("""() => { const i = el('input', {type: 'text'});
                    ask({title: 'Rename this view? (browser check)', message: ['A check of the dialog (#202).', el('label', {}, 'New name', i)], action: 'Rename', field: i}); }""")
                page.wait_for_selector("dialog.sg-ask[open]", timeout=5000)
                axe_at(page, f"{theme} the app's own dialog")
                answer_ask(page, yes=False)
                for path in (f"sources.html?profile={conn_pid}", "sharing.html?profile=1", "settings.html?profile=1", "account.html", "admin.html"):
                    page.goto(base + "/" + path)
                    page.wait_for_selector(".set-nav a[data-group]", timeout=10000)
                    settle(300)
                    for g in js("[...document.querySelectorAll('.set-nav a[data-group]')].map(a => a.dataset.group)"):
                        page.click(f".set-nav a[data-group={g}]")
                        settle(150)
                        axe_at(page, f"{theme} {path.split('.')[0]}#{g}", outline=True)
                for path, ready in (("guide.html?profile=1", "#step-name:not([hidden])"), ("providers.html?q=medicare", ".dir-hit")):
                    page.goto(base + "/" + path)
                    page.wait_for_selector(ready, timeout=10000)
                    settle(200)
                    axe_at(page, f"{theme} {path.split('?')[0]}", outline=True)
                ctx = browser.new_context(viewport={"width": 1400, "height": 900}, color_scheme=theme)   # no session, the real CSP
                lp = ctx.new_page()
                lp.goto(base + "/login.html")
                lp.wait_for_selector("#u", timeout=10000)
                axe_at(lp, f"{theme} sign-in", outline=True)
                ctx.close()
            js(f"localStorage.setItem('ht-theme', {json.dumps(theme_was)})" if theme_was else "localStorage.removeItem('ht-theme')")
            check(f"WCAG 2.2 AA, axe-core: no serious or critical finding on {len(axe_seen)} pages and states, in light and dark (#202)",
                  len(axe_seen) >= 90 and not axe_bad, axe_bad[:10] or f"{len(axe_seen)} checked in {time.time() - t_axe:.0f} s")
            check("…its moderate and minor findings, listed (none fail)", True, "; ".join(axe_other[:12]) or "none")
            check("…and no link in the browser's own blue or purple, on all of them in light and dark: links are in the accent (#247)",
                  links_seen[0] > 100 and not ua_links, ua_links[:8] or f"{links_seen[0]} links")
            check(f"…and on each of the {len(outlines)} pages, exactly one h1 (the page's, not the brand), no heading level skipped and "
                  "axe-core's heading rules passing; each page's outline (#331)", len(outlines) >= 35 and not outline_bad,
                  outline_bad[:10] or " | ".join(outlines))
            # the keyboard at 375 px, where the top bar is tallest: Tab from the start and Shift+Tab from the end
            t_keys, kbad = time.time(), []
            page.set_viewport_size({"width": 375, "height": 800})
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && typeof booted !== 'undefined' && booted", timeout=20000)
            for t in ("overview", "timeline", "heatmap", "labs", "costs"):
                open_tab(t)
                settle(250)
                kbad += focus_walk(page, t) + focus_walk(page, t, back=True)
            for path, g in (("sources.html?profile=1", "manual"), ("settings.html?profile=1", "labs"), ("sharing.html?profile=1", "summary"), ("admin.html", "apps")):
                page.goto(base + "/" + path)
                page.wait_for_selector(".set-nav a[data-group]", timeout=10000)
                page.click(f".set-nav a[data-group={g}]")
                settle(200)
                kbad += focus_walk(page, f"{path.split('.')[0]}#{g}") + focus_walk(page, f"{path.split('.')[0]}#{g}", back=True)
            check("keyboard at 375 px: Tab and Shift+Tab through each records tab and a group of each page, every stop showing focus "
                  "and none hidden under the top bar or a sticky header (2.4.7, 2.4.11, #202)", not kbad, kbad[:8] or f"{time.time() - t_keys:.0f} s")
            # reflow at 320 px (1280 px at 400%), light; and WCAG's text spacing (1.4.12) at 375 px
            spacing = "* { line-height: 1.5 !important; letter-spacing: .12em !important; word-spacing: .16em !important } p { margin-bottom: 2em !important }"
            for width, spaced in ((320, False), (375, True)):
                page.set_viewport_size({"width": width, "height": 640 if width == 320 else 800})
                rbad = []
                page.goto(base + "/")
                page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && typeof booted !== 'undefined' && booted", timeout=20000)
                if spaced:
                    page.add_style_tag(content=spacing)
                if not spaced:   # #335: every record, so Costs has its HSA and FSA table
                    js("window._was335 = [state.preset, state.from, state.to]; setPeriod('all')")
                    axe335, wraps335 = [], []
                for t in ("overview", "health", "timeline", "heatmap", "labs", "costs"):
                    open_tab(t)
                    settle(250)
                    rbad += phone_fits(page, t)
                    if not spaced:   # #335: axe-core in light and dark, and the table wrappers that scroll
                        for theme in ("light", "dark"):
                            js("t => { document.documentElement.dataset.theme = t; }", theme)
                            axe335 += axe_check(page, f"{t}, 320 px, {theme}")[0]
                        js("delete document.documentElement.dataset.theme")
                        wraps335 += [[t] + w for w in js(SCROLL_TABLES_JS)]
                if not spaced:   # and the drawer's tables: a lab's results, a claim's services, a medication's listings and fills
                    for k in ("lab", "claim", "med"):
                        js("k => openEvent(EV.find(e => e.k === k && !e.hidden && (k !== 'claim' || (DATA.details[e.id] || {}).lines?.length)) || EV.find(e => e.k === k && !e.hidden))", k)
                        settle(300)
                        wraps335 += [[f"the drawer, {k}"] + w for w in js(SCROLL_TABLES_JS, "#drawer .tablewrap")]
                        page.keyboard.press("Escape")
                        settle(150)
                    js("[state.preset, state.from, state.to] = window._was335; save()")
                    check("at 320 px (#335): axe-core finds nothing serious on any records tab, every record shown, in light and dark",
                          not axe335, axe335[:8])
                    unreached = [w for w in wraps335 if w[2] != 0 or not w[1].strip() or w[3] != "region"]
                    check("…and every table wrapper that scrolls takes focus and is named, a region (the tabs and the drawer)",
                          any(w[0] == "costs" for w in wraps335) and not unreached, unreached[:8] or wraps335)
                for path in (f"sources.html?profile={conn_pid}", "sharing.html?profile=1", "settings.html?profile=1", "account.html", "admin.html"):
                    page.goto(base + "/" + path)
                    page.wait_for_selector(".set-nav a[data-group]", timeout=10000)
                    if spaced:
                        page.add_style_tag(content=spacing)
                    settle(250)
                    for g in js("[...document.querySelectorAll('.set-nav a[data-group]')].map(a => a.dataset.group)"):
                        page.click(f".set-nav a[data-group={g}]")
                        settle(100)
                        rbad += phone_fits(page, f"{path.split('.')[0]}#{g}")
                check(f"at {width} px{' with WCAG text spacing' if spaced else ' (400% zoom)'}: every records tab and every page's groups fit, "
                      "nothing scrolling sideways, no control cut off or overlapping (1.4.10, 1.4.12, #202)", not rbad, rbad[:8] or "")
            page.set_viewport_size({"width": 1400, "height": 900})
            # forced colours (Windows' high contrast): a filled button (Save, Sign in, a dialog's action) keeps an edge
            ctx = browser.new_context(viewport={"width": 1400, "height": 900}, forced_colors="active")
            lp = ctx.new_page()
            lp.goto(base + "/login.html")
            lp.wait_for_selector("#b", timeout=10000)
            edges = {"sign-in": lp.evaluate("(c => c.borderStyle !== 'none' || c.outlineStyle !== 'none' || c.backgroundColor !== getComputedStyle(document.querySelector('form')).backgroundColor)(getComputedStyle(document.getElementById('b')))")}
            ctx.close()
            page.emulate_media(forced_colors="active")
            page.goto(base + "/account.html")
            page.wait_for_selector(".btn.primary", timeout=10000)
            edges["account"] = js("[...document.querySelectorAll('.btn.primary, .btn.danger')].filter(b => b.checkVisibility()).every(b => { const c = getComputedStyle(b); return c.borderStyle !== 'none' && parseFloat(c.borderWidth) >= 1; })")
            page.emulate_media(forced_colors="none")
            check("in forced colours, the filled buttons keep an edge (#202)", all(edges.values()), edges)
            # accessible sign-in (3.3.8): a password manager can fill the fields, and pasting isn't stopped
            ctx = browser.new_context(viewport={"width": 1400, "height": 900})
            lp = ctx.new_page()
            lp.goto(base + "/login.html")
            lp.wait_for_selector("#u", timeout=10000)
            paste = """ids => ids.map(id => { const i = document.getElementById(id), ev = new ClipboardEvent('paste', {bubbles: true, cancelable: true});
                i.dispatchEvent(ev); return [id, i.getAttribute('autocomplete'), ev.defaultPrevented]; })"""
            got = lp.evaluate(paste, ["u", "p", "c"])
            ctx.close()
            check("sign-in: fields a password manager fills (username, current-password, one-time-code) and pasting allowed (3.3.8, #202)",
                  got == [["u", "username", False], ["p", "current-password", False], ["c", "one-time-code", False]], got)

            # ---- account page: password and devices; profiles and their sharing on the Sharing page (#168); the Admin
            # page: accounts (#167)
            page.goto(base + "/account.html")
            page.wait_for_function("document.querySelectorAll('#sessList tbody tr').length > 0", timeout=10000)
            check("account page lists devices, and nothing of the server's (#167) or of profiles (#168)",
                  js("!document.getElementById('tab-admin') && !document.getElementById('users') && !document.getElementById('labmaps') && !document.getElementById('secNotice')")
                  and js("!document.getElementById('acctTabs') && !document.getElementById('profiles') && !document.getElementById('profList')"))
            # #320: on a phone the account pages' tables hid their headings and left the values on their own (Admin's "off" and
            # "10", two dates per device): each cell is labelled with its column's name now, the one name a screen reader
            # hears as the hidden headings are hidden from it too; axe-core clean, light and dark
            page.set_viewport_size({"width": 375, "height": 800})
            cl_seen, cl_bad, cl_axe = {}, [], []
            for path, ready in (("/account.html#sessions", "#sessList tbody tr"), ("/admin.html#users", "#userList tbody tr"),
                                ("/admin.html#labmaps", "#labmapList tbody tr"), ("/admin.html#installLog", "#acctLog tbody tr"),
                                ("/sharing.html?profile=1#people", "#memberList tbody tr"), ("/sharing.html?profile=1#activity", "#actBody tbody tr")):
                page.goto(base + path)
                page.wait_for_selector(ready, timeout=10000)
                settle(200)
                tables, bad = cell_labels(page, path)
                cl_seen[path] = [t for t in tables if t[1] and t[3]]
                cl_bad += bad
                for theme in ("light", "dark"):
                    page.emulate_media(color_scheme=theme)
                    cl_axe += axe_check(page, f"{path} at 375 px, {theme}")[0]
                page.emulate_media(color_scheme=None)
            check("at 375 px every account page's table labels each cell with its column's name: Account (sessions), Admin (accounts, lab "
                  "names, activity), Sharing (people, activity), its headings hidden from screen readers too (#320)",
                  not cl_bad and all(cl_seen.values()), cl_bad[:6] or cl_seen)
            check("…and axe-core finds nothing serious or critical there, light and dark (#320)", not cl_axe, cl_axe[:6])
            page.set_viewport_size({"width": 1400, "height": 900})
            page.goto(base + "/account.html#users")   # the old Admin tab's addresses open the Admin page
            page.wait_for_url("**/admin.html#users", timeout=10000)
            page.wait_for_function("!document.getElementById('users').hidden && document.querySelectorAll('#userList tbody tr').length > 0", timeout=10000)
            check("the old Admin tab's address opens the Admin page, on Accounts", js(display, "users") != "none" and js(display, "labmaps") == "none")
            page.goto(base + "/account.html#labmaps")
            page.wait_for_url("**/admin.html#labmaps", timeout=10000)
            page.wait_for_function("document.querySelector('#labmapList tr[data-labmap]')", timeout=10000)
            lm = js("[...document.querySelectorAll('#labmapList tr[data-labmap]')].map(tr => tr.textContent)")
            check("Admin lists the lab name match, who made it and from what, with Change and Remove (#23)",
                  any("Hemoglobin Whole Bld" in t and "Hemoglobin" in t and "demo" in t and "CSV" in t and "Change" in t and "Remove" in t for t in lm)
                  and "only admins can change or remove" in page.inner_text("#labmaps") and js(display, "labmaps") != "none", lm)
            # the old Profiles tab's address opens Sharing → People (#168); a section link still lands on Account
            page.goto(base + "/account.html#profiles")
            page.wait_for_url("**/sharing.html#people", timeout=10000)
            page.wait_for_selector("#memberList tbody tr", timeout=10000)
            check("the old Profiles tab's address opens Sharing → People", js("document.querySelector('#shrNav [aria-current=page]').textContent") == "People")
            page.goto(base + "/account.html#twofa")
            page.wait_for_function("document.getElementById('tfHint').textContent.length > 0", timeout=10000)
            check("an old section link still opens Account, in its group", js(display, "twofa") != "none" and js("location.hash") == "#twofa"
                  and js("document.querySelector('#acctNav [aria-current=page]').textContent") == "Sign-in and security", js("location.hash"))
            # #169: Account is you and this device, a group each in its sidebar; how things look is on Settings
            check("Account: two groups, sign-in and security first, and nothing else on the page",
                  js("[...document.querySelectorAll('#acctNav a')].map(a => a.textContent).join('|')") == "Sign-in and security|This device"
                  and js(display, "installApp") == "none" and js(display, "offlineRecords") == "none"
                  and js("document.getElementById('groupTitle').textContent") == "Sign-in and security"
                  and js("[...document.querySelectorAll('main > section')].every(s => ['password', 'twofa', 'passkeys', 'sessions', 'installApp', 'offlineRecords'].includes(s.id))"))
            page.click("#acctNav a[data-group=device]")
            check("…This device: installing it and offline records, and the address says so",
                  js(display, "installApp") != "none" and js(display, "offlineRecords") != "none" and js(display, "password") == "none"
                  and js(display, "sessions") == "none" and js("location.hash") == "#device"
                  and js("document.getElementById('groupTitle').textContent") == "This device")
            for addr, shows in (("#offline", "offlineRecords"), ("#install", "installApp"), ("#security", "password"), ("#sessions", "sessions")):
                page.goto(base + "/account.html" + addr)
                page.wait_for_function(f"getComputedStyle(document.getElementById('{shows}')).display !== 'none'", timeout=10000)
            check("…each of its addresses opens its group (#offline, #install, #security, #sessions)", True)
            page.set_viewport_size({"width": 375, "height": 800})
            for g in ("security", "device"):
                page.click(f"#acctNav a[data-group={g}]")
                settle(150)
                check(f"…Account's {g} group fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.goto(base + "/admin.html#accounts")
            page.wait_for_function("document.querySelectorAll('#userList tbody tr').length > 0", timeout=10000)
            # #318: the only account, with no second factor of its own: requiring two-factor for everyone isn't asked (the
            # server would refuse it); it says at once to turn on your own first, with the way there, and stays off.
            # Requiring it with a second factor of your own is asked: the #314 check, below
            page.click("#reqTf")
            page.wait_for_function("document.getElementById('reqTfMsg').textContent.includes('your own account')", timeout=10000)
            settle(300)
            tf_msg = js("[document.getElementById('reqTfMsg').textContent, document.getElementById('reqTfMsg').classList.contains('bad'), "
                        "document.querySelector('#reqTfMsg a')?.getAttribute('href')]")
            check("with no second factor of your own, requiring two-factor for everyone isn't asked: it says to turn on yours first, "
                  "with a link to Account → Sign-in & security, and stays off (#318)",
                  not js("document.querySelector('dialog.sg-ask[open]')") and tf_msg[0].startswith("To require it for everyone, first turn on two-factor sign-in")
                  and tf_msg[1:] == [True, "account.html#security"] and not page.is_checked("#reqTf")
                  and js("fetch('api/settings').then(r => r.json()).then(s => s.require_2fa)") is False, tf_msg)
            near = {}
            for w, h in ((1400, 900), (375, 800), (667, 375)):
                page.set_viewport_size({"width": w, "height": h})
                js("document.getElementById('reqTf').scrollIntoView({block: 'center'})")
                settle(150)
                near[f"{w}x{h}"] = js("""(() => { const b = document.getElementById('reqTf').closest('label').getBoundingClientRect(),
                    m = document.getElementById('reqTfMsg').getBoundingClientRect();
                    return [Math.round(m.top - b.bottom), m.top >= 0 && m.bottom <= innerHeight, m.height > 0]; })()""")
            page.set_viewport_size({"width": 1400, "height": 900})
            check("…and the reason is right under the checkbox, on screen at 1400×900, 375×800 and 667×375 (#323)",
                  all(0 <= gap <= 12 and seen and shown for gap, seen, shown in near.values()), near)
            page.fill("#userName", "alex")
            page.click("#userNew button[type=submit]")
            page.wait_for_function("document.querySelector('#userMsg code')", timeout=10000)
            check("admin creates an account with a one-time password", len(js("document.querySelector('#userMsg code').textContent")) >= 20)
            # no longer the only account: admin actions now wait for the admin's own second factor (issue #75)
            check("with a second account, the admin is asked for a second factor",
                  js("!document.getElementById('adminTf').hidden && document.getElementById('adminTools').hidden"))
            check("…and every other part of Admin the server now refuses says so", js("['admin', 'sec-sync', 'labmaps', 'installLog'].every(id => document.querySelector('#' + id + ' .adm-gate'))"))
            # two-factor sign-in: turn it on from the key shown, confirm with a code, get recovery codes
            from server import twofactor
            import time as _time
            totp = lambda key, ahead=0: twofactor._code(key, int(_time.time() // twofactor.PERIOD) + ahead)
            check("the admin's note links to Account's security tab", js("document.querySelector('#adminTf a').getAttribute('href')") == "account.html#security")
            page.click("#adminTf a")
            page.wait_for_url("**/account.html#security", timeout=10000)
            page.wait_for_function("document.getElementById('tfHint').textContent.length > 0", timeout=10000)
            check("which opens it", js(display, "twofa") != "none")
            page.locator("#twofa").get_by_role("button", name="Turn on").click()
            page.wait_for_selector("#twofa .tf-key")
            key = js("document.querySelector('#twofa .tf-key').textContent").replace(" ", "")
            check("2FA setup shows a QR code", js("document.querySelector('#twofa .tf-setup img').naturalWidth > 100"))
            page.locator("#twofa input").fill(totp(key))
            page.locator("#twofa").get_by_role("button", name="Confirm").click()
            page.wait_for_selector("#twofa .tf-codes")
            check("2FA confirmed, 10 recovery codes shown", len(js("document.querySelector('#twofa .tf-codes').textContent").split()) == 10)
            page.locator("#twofa").get_by_role("button", name="I've saved them").click()
            page.wait_for_function("document.getElementById('tfHint').textContent.startsWith('On.') && document.querySelector('#tfMsg a[href=\"admin.html\"]')", timeout=10000)
            check("with 2FA on, Account says Admin is open again", True)
            page.click("#tfMsg a")
            page.wait_for_function("!document.getElementById('adminTools').hidden && document.querySelector('#userList').textContent.includes('alex')", timeout=10000)
            check("with 2FA on, the account tools are back", js("document.getElementById('adminTf').hidden && !document.querySelector('.adm-gate')"))
            # activity: the profile's log (Sharing → Activity, #168) shows the records being opened (by this run), and
            # filters by kind; the admin's account log shows the account just created
            page.goto(base + "/sharing.html?profile=1#activity")
            page.wait_for_function("[...document.querySelectorAll('#actBody .activity tbody tr')].some(r => r.textContent.includes('opened the records'))", timeout=10000)
            check("profile activity shows the records being opened", js(display, "activity") != "none" and js(display, "people") == "none")
            page.locator("#actBody .activity select").nth(1).select_option("document")
            page.wait_for_function("[...document.querySelectorAll('#actBody .activity tbody tr')].every(r => r.textContent.includes('opened') && !r.textContent.includes('opened the records'))", timeout=10000)
            check("activity filters by kind", js("document.querySelector('#actBody .activity tbody').textContent.includes('demo-report.pdf') || document.querySelector('#actBody .activity tbody').textContent.includes('Nothing yet')"))
            page.goto(base + "/admin.html#activity")
            page.wait_for_function("document.querySelector('#acctLog tbody') && document.querySelector('#acctLog tbody').textContent.includes('created the account alex')", timeout=10000)
            check("admin sees the install's activity, under Admin → Install activity", js(display, "installLog") != "none" and js(display, "users") == "none")
            page.goto(base + "/sharing.html?profile=1#export")
            page.wait_for_selector("#exportLink", timeout=10000)
            href = js("document.getElementById('exportLink').getAttribute('href')")
            head = js(f"fetch('{href}').then(r => r.arrayBuffer()).then(b => [...new Uint8Array(b.slice(0, 2))])")
            check("Export everything downloads a zip", href == "api/profiles/1/export" and head == [0x50, 0x4B], (href, head))
            # People on the second profile (picked at the top): sharing with someone who already has access asks before
            # changing their role, and declining keeps it; then sharing for real, and a role changed in the table
            page.select_option("#profile", "2")
            page.click("#shrNav a[data-group=people]")
            page.wait_for_function("document.querySelector('#introHint').textContent.includes('Second person') && document.getElementById('people').dataset.profile === '2'", timeout=10000)
            check("picking another profile shows its people, and the address keeps the group", js("location.hash") == "#people"
                  and USER in page.inner_text("#memberList"))
            form = page.locator("#shareWith form")
            form.locator("input").fill(USER)
            form.locator("select").select_option("viewer")
            form.get_by_role("button", name="Share").click()
            page.wait_for_selector("dialog.sg-ask[open]", timeout=10000)
            share_focus = js("document.activeElement.textContent")
            asked_share = answer_ask(page, yes=False)
            settle(300)
            check("sharing with an existing member asks first", "already has access to Second person as owner" in asked_share[1]
                  and asked_share[0] == f"Change {USER} to viewer?", asked_share)
            check("declining keeps their role", js("fetch('api/profiles/2/members').then(r => r.json()).then(m => m[0].role)") == "owner")

            # ---- the app's own dialog (#210): asked in a <dialog> over the page, not the browser's pop-up (main() counts
            # any of those as a problem). At 375 px, in each theme: it fits, has a title and Cancel beside a button named for
            # the action, red for one that can't be undone with focus starting on Cancel; Escape cancels and focus goes back
            # to the button that opened it. A change that can be undone starts on its action. Later, Remove alex is opened
            # and confirmed by keyboard alone (Enter, Tab, Enter).
            check("…its question starts on the action, a change that can be undone (#210)", share_focus == "Change role", share_focus)
            theme_was_210 = js("localStorage.getItem('ht-theme')")
            page.set_viewport_size({"width": 375, "height": 800})
            dialog_js = """(() => { const d = document.querySelector('dialog.sg-ask[open]'), b = [...d.querySelectorAll('.sg-ask-acts button')];
              const rgb = c => c.match(/[\\d.]+/g).slice(0, 3).map(Number), r = d.getBoundingClientRect();
              return {modal: d.matches(':modal'), role: d.getAttribute('role'), title: d.querySelector('h2').textContent,
                named: document.getElementById(d.getAttribute('aria-labelledby')).textContent, buttons: b.map(x => x.textContent),
                onCancel: document.activeElement === b[0], bg: rgb(getComputedStyle(d).backgroundColor), ink: rgb(getComputedStyle(d).color),
                action: rgb(getComputedStyle(b[1]).backgroundColor), inside: r.left >= 16 && r.right <= innerWidth - 16 && r.top >= 0 && r.bottom <= innerHeight}; })()"""
            for theme in ("light", "dark"):
                js(f"localStorage.setItem('ht-theme', '{theme}'); applyTheme()")
                page.locator("#memberList button", has_text="Leave").click()
                page.wait_for_selector("dialog.sg-ask[open]", timeout=10000)
                d = js(dialog_js)
                fits = phone_fits(page, f"the dialog, {theme}")
                dark_bg = sum(d["bg"]) < 200 and sum(d["ink"]) > 600
                check(f"at 375 px, {theme}: Leave asks in the app's own dialog, titled, Cancel and a red Leave profile, focus on Cancel (#210)",
                      d["modal"] and d["role"] == "alertdialog" and d["title"] == d["named"] == "Leave Second person?"
                      and d["buttons"] == ["Cancel", "Leave profile"] and d["onCancel"] and d["action"] == [179, 38, 30]   # --crit-fill, #b3261e in both themes: white text on it is 6.5:1 (#202, #254)
                      and d["inside"] and (dark_bg if theme == "dark" else sum(d["bg"]) > 700 and sum(d["ink"]) < 100) and not fits, fits or d)
                page.keyboard.press("Escape")
                page.wait_for_selector("dialog.sg-ask", state="detached", timeout=10000)
                check(f"…{theme}: Escape cancels it, and focus is back on Leave",
                      js("document.activeElement.textContent") == "Leave" and USER in page.inner_text("#memberList")
                      and js("fetch('api/profiles/2/members').then(r => r.json()).then(m => m.length)") == 1, js("document.activeElement.outerHTML"))
            # the sign-in page's note after a recovery code: on the page with a Continue, not an alert (#210)
            ctx = browser.new_context(viewport={"width": 375, "height": 800}, color_scheme="dark")   # no session: the sign-in page
            lp = ctx.new_page()
            lp.goto(base + "/login.html")
            lp.wait_for_selector("#u", timeout=10000)
            lp.evaluate("signedIn({recovery_codes_left: 7})")
            lp.wait_for_timeout(300)   # (the real CSP: no wait_for_function) announce() says it after a moment
            check("after a recovery code, the sign-in page says what it means, with Continue focused (#210)",
                  "Signed in with a recovery code. You have 7 left" in lp.inner_text("#recoveryNote")
                  and "You have 7 left" in lp.evaluate("document.getElementById('sgStatus').textContent")   # said, not shown twice:
                  and lp.evaluate("document.getElementById('sgStatus').getBoundingClientRect().width") <= 1
                  and lp.evaluate("document.activeElement.id") == "recoveryGo" and not phone_fits(lp, "sign-in note"), phone_fits(lp, "sign-in note"))
            ctx.close()
            form.locator("input").fill("alex")
            form.locator("select").select_option("editor")
            form.get_by_role("button", name="Share").click()
            page.wait_for_function("document.querySelectorAll('#memberList tbody tr').length === 2 && document.getElementById('peopleMsg').textContent === 'Shared with alex as editor.'", timeout=10000)
            check("sharing adds them, as the role chosen", js("document.querySelector(\"#memberList select[aria-label=\\\"alex's role\\\"]\").value") == "editor")
            page.select_option("#memberList select[aria-label=\"alex's role\"]", "viewer")
            page.wait_for_function("document.getElementById('peopleMsg').textContent === 'alex is now a viewer.'", timeout=10000)
            role_of = lambda who: js(f"fetch('api/profiles/2/members').then(r => r.json()).then(m => m.find(x => x.username === {json.dumps(who)}).role)")  # noqa: E731
            check("changing a role in the table saves it", role_of("alex") == "viewer")
            check("…someone else's editor/viewer change isn't asked (#249)", not js("document.querySelector('dialog.sg-ask')"))
            # ---- role changes that are hard to take back ask first (#249): Cancel puts the select back and sends nothing
            alex_sel, my_sel = "#memberList select[aria-label=\"alex's role\"]", f"#memberList select[aria-label=\"{USER}'s role\"]"
            focus_js = "(() => { const d = document.querySelector('dialog.sg-ask[open]'); return [d.getAttribute('role'), document.activeElement.textContent]; })()"
            page.select_option(alex_sel, "owner")
            page.wait_for_selector("dialog.sg-ask[open]", timeout=10000)
            owner_focus = js(focus_js)
            asked = answer_ask(page, yes=False)
            settle(300)
            check("giving someone owner asks first, saying what an owner can do (#249), in red with focus on Cancel (#314)",
                  asked[0] == "Make alex an owner of Second person?" and "change anyone's role (yours too)" in asked[1]
                  and owner_focus == ["alertdialog", "Cancel"], [asked, owner_focus])
            check("…Cancel puts the select back and changes nothing", js(f"document.querySelector({json.dumps(alex_sel)}).value") == "viewer"
                  and role_of("alex") == "viewer")
            page.select_option(alex_sel, "owner")
            answer_ask(page)
            page.wait_for_function("document.getElementById('peopleMsg').textContent === 'alex is now an owner.'", timeout=10000)
            check("…confirming makes them an owner", role_of("alex") == "owner")
            # #314: a co-owner whose account is disabled isn't an owner to the server, so demoting yourself isn't asked
            # (it would be refused after); owners see the account is disabled
            from server import db as dbm_314
            db_314 = dbm_314.connect()
            db_314.execute("UPDATE users SET disabled=1 WHERE username='alex'")
            page.reload()
            page.wait_for_function(f"document.getElementById('people').dataset.profile === '2' && document.querySelector({json.dumps(my_sel)})", timeout=10000)
            before_314 = len(problems)
            page.select_option(my_sel, "editor")
            page.wait_for_function("document.getElementById('peopleMsg').textContent.includes('active owner')", timeout=10000)
            check("…with only a disabled co-owner, changing your own role isn't asked, and the server's refusal is said (#314)",
                  not js("document.querySelector('dialog.sg-ask')") and js(f"document.querySelector({json.dumps(my_sel)}).value") == "owner"
                  and role_of(USER) == "owner", js("document.getElementById('peopleMsg').textContent"))
            check("…and the People table says that account is disabled (#314)", "alex (account disabled)" in page.inner_text("#memberList"), page.inner_text("#memberList"))
            problems[before_314:] = [x for x in problems[before_314:] if "409" not in x]   # the refusal was the point
            db_314.execute("UPDATE users SET disabled=0 WHERE username='alex'")
            db_314.close()
            page.reload()
            page.wait_for_function(f"document.getElementById('people').dataset.profile === '2' && document.querySelector({json.dumps(my_sel)})", timeout=10000)
            page.select_option(my_sel, "editor")
            page.wait_for_selector("dialog.sg-ask[open]", timeout=10000)
            mine_focus = js(focus_js)
            asked = answer_ask(page, yes=False)
            settle(300)
            check("changing your own role asks first, in red with focus on Cancel, saying what you'd lose (#249)",
                  asked[0] == "Make yourself an editor of Second person?" and "no longer be able to share Second person" in asked[1]
                  and "rename or delete it" in asked[1] and mine_focus == ["alertdialog", "Cancel"], [asked, mine_focus])
            check("…cycle tracking's owner-only changes among them (#314)", "turn cycle tracking on or off or delete what it kept" in asked[1], asked)
            check("…Cancel keeps you an owner", js(f"document.querySelector({json.dumps(my_sel)}).value") == "owner" and role_of(USER) == "owner")
            page.select_option(my_sel, "editor")
            answer_ask(page)
            # the intro line is set before the people are fetched and drawn again (sharing.js, pick): wait for the list too (#337)
            try:
                page.wait_for_function("document.getElementById('introHint').textContent.startsWith(\"You're an editor\") "
                                       "&& !document.querySelector('#memberList select')", timeout=10000)
            except Exception:
                pass   # reported by the check below, with what it saw
            check("…confirming makes you an editor", role_of(USER) == "editor" and not js("document.querySelector('#memberList select')"),
                  [role_of(USER), js("document.getElementById('introHint').textContent"), js("document.querySelectorAll('#memberList select').length")])
            from server import db as dbm_249
            db_249 = dbm_249.connect()   # an owner again (only another owner could, in the app), for the steps below
            db_249.execute("UPDATE memberships SET role='owner' WHERE profile_id=2 AND user_id=(SELECT id FROM users WHERE username=?)", (USER,))
            db_249.close()
            page.reload()   # (not goto: the same address with a #hash wouldn't load the page again)
            page.wait_for_function(f"document.getElementById('people').dataset.profile === '2' && document.querySelector({json.dumps(alex_sel)})", timeout=10000)
            page.select_option(alex_sel, "viewer")
            asked = answer_ask(page, yes=False)
            settle(300)
            check("taking owner away from someone asks first (#249)", asked[0] == "Make alex a viewer of Second person?"
                  and "no longer be able to share it" in asked[1], asked)
            check("…Cancel keeps them an owner", js(f"document.querySelector({json.dumps(alex_sel)}).value") == "owner" and role_of("alex") == "owner")
            page.select_option(alex_sel, "viewer")
            answer_ask(page)
            page.wait_for_function("document.getElementById('peopleMsg').textContent === 'alex is now a viewer.'", timeout=10000)
            check("…confirming makes them a viewer", role_of("alex") == "viewer")
            page.focus("#memberList button[aria-label='Remove alex']")   # (#210: at 375 px, dark, by keyboard alone)
            page.keyboard.press("Enter")
            page.wait_for_selector("dialog.sg-ask[open]", timeout=10000)
            on_cancel = js("document.activeElement.textContent")
            page.keyboard.press("Tab")
            on_action = js("document.activeElement.textContent")
            page.keyboard.press("Enter")
            page.wait_for_function("document.querySelectorAll('#memberList tbody tr').length === 1", timeout=10000)
            check("Remove takes their access away", "alex" not in page.inner_text("#memberList"))
            check("…confirmed by keyboard in the app's dialog: Enter opens it on Cancel, Tab reaches Remove access, Enter does it (#210)",
                  on_cancel == "Cancel" and on_action == "Remove access" and not js("document.querySelector('dialog.sg-ask')"), (on_cancel, on_action))
            # #313: no possessive made from a profile's name ("They'll stop seeing My records's records"): the default profile
            db_313 = dbm_249.connect()
            alex_id = db_313.execute("SELECT id FROM users WHERE username='alex'").fetchone()[0]
            db_313.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, 1, 'viewer')", (alex_id,))
            page.goto(base + "/sharing.html?profile=1#people")
            page.wait_for_function("document.getElementById('people').dataset.profile === '1' && !!document.querySelector(\"#memberList button[aria-label='Remove alex']\")", timeout=10000)
            page.click("#memberList button[aria-label='Remove alex']")
            asked = answer_ask(page, yes=False)
            check("…Remove on the default profile says “the records in My records”, with no possessive made from its name (#313)",
                  asked == ["Remove alex's access?", "They'll stop seeing the records in My records. You can share it with them again later."], asked)
            db_313.execute("DELETE FROM memberships WHERE user_id=? AND profile_id=1", (alex_id,))
            db_313.close()
            page.goto(base + "/sharing.html?profile=2#people")
            page.wait_for_function("document.getElementById('people').dataset.profile === '2'", timeout=10000)
            js(f"localStorage.setItem('ht-theme', {json.dumps(theme_was_210)}); applyTheme()" if theme_was_210 else "localStorage.removeItem('ht-theme'); applyTheme()")
            page.set_viewport_size({"width": 1400, "height": 900})
            page.set_viewport_size({"width": 375, "height": 800})
            for g in ("people", "links", "calendar", "summary", "export", "activity", "profile"):
                page.click(f"#shrNav a[data-group={g}]")
                settle(200)
                check(f"Sharing → {g} fits 375 px, its item in sight in the sidebar's row", js("document.documentElement.scrollWidth") <= 375
                      and js(f"(() => {{ const a = document.querySelector('#shrNav a[data-group={g}]').getBoundingClientRect(), b = document.querySelector('#shrNav ul').getBoundingClientRect(); return a.left >= b.left - 1 && a.right <= b.right + 1; }})()"),
                      js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            # Profile, apart from sharing: a new profile is made and shown; deleting one needs its name typed exactly
            page.click("#shrNav a[data-group=profile]")
            page.fill("#profName", "Third person (fictional)")
            page.click("#profNew button[type=submit]")
            page.wait_for_function("document.getElementById('profMsg').textContent.startsWith('Created')", timeout=10000)
            third = js("document.getElementById('profile').value")
            check("New profile makes one, and the page shows it", js("document.querySelector('#profile option:checked').textContent") == "Third person (fictional) (owner)"
                  and js("document.querySelector('#profileBody input').value") == "Third person (fictional)", third)
            page.fill("#profileBody input", "Third person, renamed")
            page.click("#profileBody button:text-is('Rename')")
            page.wait_for_function("document.getElementById('profileMsg').textContent === 'Renamed.' && document.querySelector('#profile option:checked').textContent === 'Third person, renamed (owner)'", timeout=10000)
            check("Rename renames it, in the picker too", True)
            page.select_option("#profile", "2")
            page.wait_for_function("document.querySelector('#profileBody input').value === 'Second person'", timeout=10000)
            second = page.locator("#profileCard")
            second.get_by_role("button", name="Delete profile…").click()
            go = second.get_by_role("button", name="Delete it and everything in it")
            check("delete needs the profile's name first", go.is_disabled())
            second.get_by_label("Type Second person to confirm").fill("Second person")
            go.click()
            page.wait_for_function("![...document.querySelectorAll('#profile option')].some(o => o.textContent.includes('Second person')) && document.getElementById('pageMsg').textContent.startsWith('Deleted')", timeout=10000)
            check("…and another profile is shown after it", js("document.getElementById('profile').value") != "2")
            js(f"api('profiles/{third}', {{method: 'DELETE', body: JSON.stringify({{confirm_name: 'Third person, renamed'}})}}).then(r => r.status)")
            js("localStorage.setItem('ht-profile', '1')")
            gone_from = len(problems)
            check("deleting a profile removes it", js("fetch('api/profiles/2/data').then(r => r.status)") == 404)
            problems[gone_from:] = [x for x in problems[gone_from:] if "404" not in x]   # that 404 was the point
            # resetting a password needs a fresh check: once the sign-in is more than a few minutes old, it asks first
            from server import db as dbm, profiles
            db = dbm.connect()
            db.execute("UPDATE sessions SET verified_at=0")
            page.goto(base + "/admin.html#accounts")
            page.wait_for_function("document.querySelector('#userList').textContent.includes('alex')", timeout=10000)
            before = len(problems)
            alex_row = lambda: page.locator("#userList tr", has_text="alex")  # noqa: E731
            alex_is = lambda: js("fetch('api/users').then(r => r.json()).then(l => l.find(u => u.username === 'alex'))")  # noqa: E731
            focus_js = "(() => { const d = document.querySelector('dialog.sg-ask[open]'); return [d.getAttribute('role'), document.activeElement.textContent]; })()"
            pw_was = db.execute("SELECT pw_hash FROM users WHERE username='alex'").fetchone()[0]
            alex_row().get_by_role("button", name="Reset password").click()   # asked first (#249), before the fresh check
            page.wait_for_selector("dialog.sg-ask[open]", timeout=10000)
            reset_focus = js(focus_js)
            asked = answer_ask(page, yes=False)
            settle(300)
            check("Reset password asks first, naming the account, in red with focus on Cancel (#249)", asked[0] == "Reset alex's password?"
                  and "one-time password" in asked[1] and reset_focus == ["alertdialog", "Cancel"], [asked, reset_focus])
            check("…Cancel resets nothing, and asks for no fresh check", js("document.getElementById('reauth').hidden")
                  and db.execute("SELECT pw_hash FROM users WHERE username='alex'").fetchone()[0] == pw_was and not js("document.querySelector('#userMsg code')"))
            alex_row().get_by_role("button", name="Reset password").click()
            answer_ask(page)
            page.wait_for_selector("#reauth input", state="visible", timeout=10000)
            check("resetting a password asks to confirm it's you", "Confirm it's you" in page.inner_text("#reauth"))
            page.fill("#reauth input", PW)
            page.locator("#reauth").get_by_role("button", name="Confirm").click()
            page.wait_for_function("document.getElementById('reauth').hidden && document.querySelector('#userMsg code') && "
                                   "document.getElementById('userMsg').textContent.includes('alex')", timeout=10000)
            check("after the password, the reset goes ahead", True)
            problems[before:] = [x for x in problems[before:] if "403" not in x]   # the refusal that asks
            # Make admin and Remove admin ask first, naming the account (#249); Cancel leaves the flag as it was
            alex_row().get_by_role("button", name="Make admin").click()
            page.wait_for_selector("dialog.sg-ask[open]", timeout=10000)
            admin_focus = js(focus_js)
            asked = answer_ask(page, yes=False)
            settle(300)
            check("Make admin asks first, in red with focus on Cancel, saying what an admin can do (#249)", asked[0] == "Make alex an admin?"
                  and "reset passwords" in asked[1] and admin_focus == ["alertdialog", "Cancel"], [asked, admin_focus])
            check("…Cancel leaves them not an admin", not alex_is()["is_admin"])
            alex_row().get_by_role("button", name="Make admin").click()
            answer_ask(page)
            page.wait_for_function("[...document.querySelectorAll('#userList tr')].some(r => r.textContent.includes('alex') && r.querySelector('.tag'))", timeout=10000)
            check("…confirming makes them an admin", alex_is()["is_admin"])
            alex_row().get_by_role("button", name="Remove admin").click()
            asked = answer_ask(page, yes=False)
            settle(300)
            check("Remove admin asks first (#249)", asked[0] == "Remove alex's admin role?" and "Admin page" in asked[1] and alex_is()["is_admin"], asked)
            alex_row().get_by_role("button", name="Remove admin").click()
            answer_ask(page)
            page.wait_for_function("[...document.querySelectorAll('#userList tr')].some(r => r.textContent.includes('alex') && !r.querySelector('.tag') && r.textContent.includes('Make admin'))", timeout=10000)
            check("…confirming removes it", not alex_is()["is_admin"])
            # #314: requiring two-factor sign-in for everyone asks first, saying how many accounts have no second factor;
            # Cancel changes nothing. Turning it off again isn't asked. The admin has a second factor now, so the count
            # is of other accounts (without one, it isn't asked at all: #318, above)
            required = lambda: js("fetch('api/settings').then(r => r.json()).then(s => s.require_2fa)")  # noqa: E731
            lacking = js("fetch('api/users').then(r => r.json()).then(l => l.filter(u => !u.disabled && !u.two_factor).length)")
            mine_has = js("fetch('api/users').then(r => r.json()).then(l => l.find(u => u.id === ME.user.id).two_factor)")
            page.click("#reqTf")
            page.wait_for_selector("dialog.sg-ask[open]", timeout=10000)
            tf_focus = js(focus_js)
            asked = answer_ask(page, yes=False)
            settle(300)
            check("Require two-factor sign-in for everyone asks first, in red with focus on Cancel, saying how many accounts have no second factor (#314)",
                  asked[0] == "Require two-factor sign-in for everyone?" and lacking >= 1
                  and asked[1].startswith("1 account doesn't" if lacking == 1 else f"{lacking} accounts don't")
                  and mine_has and "yours" not in asked[1].lower() and tf_focus == ["alertdialog", "Cancel"], [asked, tf_focus, lacking, mine_has])
            check("…Cancel leaves it off", not page.is_checked("#reqTf") and required() is False)
            page.click("#reqTf")
            answer_ask(page)
            page.wait_for_function("document.getElementById('reqTfMsg').textContent === 'Everyone now needs two-factor sign-in.'", timeout=10000)
            check("…confirming turns it on", page.is_checked("#reqTf") and required() is True)
            page.click("#reqTf")
            page.wait_for_function("document.getElementById('reqTfMsg').textContent === 'Two-factor sign-in is now optional.'", timeout=10000)
            check("…and turning it off isn't asked", not js("document.querySelector('dialog.sg-ask')") and required() is False)
            # Disable asks first (#249); for the only owner of a profile, the server's refusal is asked once more, after it
            alex = db.execute("SELECT id FROM users WHERE username='alex'").fetchone()[0]
            profiles.create_profile(db, "Alex's records", owner_id=alex)
            db.close()
            alex_row().get_by_role("button", name="Disable").click()
            page.wait_for_selector("dialog.sg-ask[open]", timeout=10000)
            off_focus = js(focus_js)
            asked = answer_ask(page, yes=False)
            settle(300)
            check("Disable asks first, in red with focus on Cancel, saying they're signed out (#249)", asked[0] == "Disable alex?"
                  and "signed out everywhere" in asked[1] and off_focus == ["alertdialog", "Cancel"], [asked, off_focus])
            check("…Cancel leaves the account active", not alex_is()["disabled"] and "Disable" in alex_row().inner_text())
            before = len(problems)
            alex_row().get_by_role("button", name="Disable").click()
            answer_ask(page)
            asked = answer_ask(page)
            page.wait_for_function("[...document.querySelectorAll('#userList tr')].some(r => r.textContent.includes('alex') && r.textContent.includes('disabled'))", timeout=10000)
            problems[before:] = [x for x in problems[before:] if "409" not in x]   # the refusal that prompts the question
            check("disabling a profile's only owner then asks about that", asked[0] == "Disable alex anyway?" and "only owner of Alex's records" in asked[1], asked)
            page.wait_for_function("document.querySelector('#ownerless') && document.querySelector('#ownerless').textContent.includes(\"Alex's records\")", timeout=10000)
            check("an admin sees profiles left without an owner", True)
            # removing that account: confirmed, then asked who owns Alex's records instead (this admin)
            before_remove = len(problems)
            page.locator("#userList tr", has_text="alex").get_by_role("button", name="Remove").click()
            answer_ask(page)
            owner_asked = answer_ask(page, fill=USER)   # (#210: the app's dialog with a box for the name, not prompt)
            page.wait_for_function("!document.querySelector('#userList').textContent.includes('alex')", timeout=10000)
            check("…asked who takes over, in the app's dialog with a box for the name", owner_asked[0] == "Who takes over from alex?"
                  and "only owner of Alex's records" in owner_asked[1], owner_asked)
            problems[before_remove:] = [x for x in problems[before_remove:] if "409" not in x]   # the question's refusal
            mine_now = js("fetch('api/me').then(r => r.json()).then(j => j.profiles.map(p => p.name))")
            check("removing a sole owner hands their profile to whoever is named", "Alex's records" in mine_now, mine_now)
            # log out ends the session: the API now refuses
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0", timeout=20000)
            # ---- picked lab names cleared at sign-out (#220): sel goes from every profile's saved view, the rest stays
            js("""() => { state.sel = new Set(SERIES.filter(s => !s.watch).slice(0, 2).map(s => s.name)); save();
                         localStorage.setItem('ht-state:99', JSON.stringify({preset: '1y', tab: 'labs', cats: ['lab'], sel: ['Ferritin'], v: 2})); }""")
            views_before = js("[JSON.parse(localStorage.getItem(stateKey())), JSON.parse(localStorage.getItem('ht-state:99')), stateKey()]")
            before = len(problems)
            menu_click(page, "#logout")
            page.wait_for_url("**/login.html", timeout=10000)
            views = js("Object.keys(localStorage).filter(k => k.startsWith('ht-state')).map(k => [k, JSON.parse(localStorage.getItem(k))])")
            kept = lambda v: {k: v.get(k) for k in ("preset", "tab", "labsView", "cats")}   # noqa: E731  (from, to: the period's dates, drawn again)
            check("sign-out clears the picked lab names from every saved view, keeping its period, tab and kinds (#220)",
                  len(views_before[0]["sel"]) == 2 and views and all("sel" not in v for _, v in views)
                  and kept(dict(views).get("ht-state:99") or {}) == kept(views_before[1]) and kept(dict(views).get(views_before[2]) or {}) == kept(views_before[0]),
                  [views_before, views])
            page.evaluate("localStorage.setItem('ht-state:99', JSON.stringify({preset: '5y', sel: ['Ferritin'], v: 2}))")
            page.reload()
            check("…and reaching the sign-in page clears them too", page.evaluate("JSON.parse(localStorage.getItem('ht-state:99'))") == {"preset": "5y", "v": 2})
            page.evaluate("localStorage.removeItem('ht-state:99')")
            check("log out ends the session", js("fetch('api/me').then(r => r.status)") == 401)
            # signing in again takes the password, then a code
            page.fill("#u", USER)
            page.fill("#p", PW)
            page.click("#b")
            page.wait_for_selector("#c", state="visible")
            check("2FA: the password alone doesn't sign in", js("fetch('api/me').then(r => r.status)") == 401)
            page.fill("#c", totp(key, 1))
            page.click("#b")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0", timeout=20000)
            check("2FA: the code signs in", js("fetch('api/me').then(r => r.status)") == 200)
            problems[before:] = [x for x in problems[before:] if "401" not in x]   # only the 401s after logging out were the point
            # passkeys, with Chromium's virtual authenticator, at localhost: a secure context with a name (passkeys
            # refuse an IP address like 127.0.0.1). Same server; this session's cookie carried over to that name.
            local = base.replace("127.0.0.1", "localhost")
            SIGNED_IN_OR_ERROR = "!location.pathname.endsWith('login.html') || (document.getElementById('e') || {}).textContent"
            ctx = browser.new_context(viewport={"width": 1400, "height": 900}, bypass_csp=True)
            ctx.add_cookies([{**{k: c[k] for k in ("name", "value", "path", "httpOnly", "sameSite")}, "domain": "localhost"}
                             for c in page.context.cookies() if c["name"] == "sg_session"])
            pk = ctx.new_page()
            pk.on("pageerror", lambda e: problems.append(f"script error (localhost): {e}"))
            pk.on("console", lambda m: m.type == "error" and problems.append(f"console error (localhost): {m.text}"))
            before = len(problems)
            pk.on("response", lambda r: r.status >= 400 and problems.append(f"HTTP {r.status}: {r.url}"))
            cdp = ctx.new_cdp_session(pk)
            cdp.send("WebAuthn.enable")
            cdp.send("WebAuthn.addVirtualAuthenticator", {"options": {"protocol": "ctap2", "transport": "internal",
                     "hasResidentKey": True, "hasUserVerification": True, "isUserVerified": True}})
            pk.goto(local + "/account.html")
            pk.wait_for_selector("#pkBody form")
            pk.locator("#pkBody input[type=password]").fill(PW)
            pk.locator("#pkBody").get_by_role("button", name="Add a passkey").click()
            pk.wait_for_selector("#pkList tbody tr", timeout=10000)
            check("a passkey is added and listed", "never" in pk.inner_text("#pkList"), pk.inner_text("#pkMsg"))
            acct_field.update(pk.evaluate(FIELD_LOOK_JS, "#pkBody input[type=password]"))
            pk.set_viewport_size({"width": 375, "height": 800})
            pk.wait_for_timeout(200)
            cl_tables, cl_bad = cell_labels(pk, "Account → Passkeys")
            check("…at 375 px with its name, added and last used under their columns' names, the buttons unlabelled (#320)",
                  not cl_bad and any(t[0].startswith("pkList") and t[1] and t[3] == 3 * t[2] for t in cl_tables), cl_bad[:6] or cl_tables)
            pk.set_viewport_size({"width": 1400, "height": 900})
            menu_click(pk, "#logout")
            pk.wait_for_url("**/login.html", timeout=10000)
            check("sign-in page offers a passkey", pk.is_visible("#pk") and pk.inner_text("#pk") == "Sign in with a passkey")
            looks = {f: pk.evaluate(FIELD_LOOK_JS, f) for f in ("#u", "#p")}
            check("sign-in's fields look like the app's (#332): the same background, edge and corner as a field on Account, in light and dark",
                  acct_field and acct_field["light"] != acct_field["dark"] and all(v == acct_field for v in looks.values()), [acct_field, looks])
            ring_bad, ring_seen = ring_walk(pk, "sign-in")
            check("…and Tab through sign-in, light and dark, rings both fields, Sign in and Sign in with a passkey in the accent, 2 px (#310)",
                  not ring_bad and all(f"{t} {i}" in ring_seen for t in ("light", "dark") for i in ("u", "p", "b", "pk")), ring_bad[:6] or ring_seen)
            pk.click("#pk")
            pk.wait_for_function(SIGNED_IN_OR_ERROR, timeout=20000)
            check("a passkey signs in on its own", pk.evaluate("fetch('api/me').then(r => r.json()).then(j => j.user && j.user.username)") == USER,
                  pk.evaluate("(document.getElementById('e') || {}).textContent"))
            menu_click(pk, "#logout")
            pk.wait_for_url("**/login.html", timeout=10000)
            pk.fill("#u", USER)
            pk.fill("#p", PW)
            pk.click("#b")
            pk.wait_for_selector("#c", state="visible")
            check("after the password: the code, or a passkey", pk.is_visible("#pk") and pk.inner_text("#pk") == "Use a passkey")
            pk.click("#pk")
            pk.wait_for_function(SIGNED_IN_OR_ERROR, timeout=20000)
            check("a passkey finishes the sign-in after the password", pk.evaluate("fetch('api/me').then(r => r.status)") == 200)
            problems[before:] = [x for x in problems[before:] if "401" not in x]   # signed out on the way, on purpose
            ctx.close()
        except Exception as e:   # report what was caught so far instead of a bare traceback
            problems.append(f"check stopped: {stopped(e)}")
            try:   # what the page showed when it stopped, for failures that only happen on CI's machines
                problems.append("page then: " + page.url + " " + page.evaluate("JSON.stringify({profile: document.getElementById('profile')?.value, "
                                "group: document.querySelector('main')?.dataset.group, buttons: document.getElementById('sourceButtons')?.textContent.slice(0, 200), "
                                "mine: document.getElementById('mine')?.className, intro: document.getElementById('introHint')?.textContent.slice(0, 120), "
                                "drawer: document.getElementById('drawer')?.className})"))
            except Exception:
                pass
            if "pk" in locals():
                problems.append(f"passkey page at {pk.url}: " + str(pk.evaluate("document.body.innerText.slice(0, 300)")))
        # ---- install as a phone app (#56), on a phone-sized page under the real CSP: the manifest and service worker
        # meet the install criteria; offline, with offline records off, the app's shell opens and says records aren't
        # available; turned on, the last profile opens read-only with the time it was loaded; turning it off, the
        # sign-in's end time and signing out each delete the copy. Signed in with a session of its own.
        try:
            before = len(problems)
            from server import config as sg_config, db as sg_db, security as sg_sec
            db = sg_db.connect()
            token, now = sg_sec.new_token(), sg_db.now()
            uid = db.execute("SELECT id FROM users WHERE username=?", (USER,)).fetchone()[0]
            db.execute("INSERT INTO sessions(token_hash, user_id, created, last_seen, expires, ip, agent, verified_at) VALUES(?,?,?,?,?,?,?,?)",
                       (sg_sec.token_hash(token), uid, now, now, now + 3600, "127.0.0.1", "browser check", now))
            db.close()
            ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
            ctx.add_cookies([{"name": sg_config.COOKIE, "value": token, "url": base, "httpOnly": True, "sameSite": "Strict"}])
            ph = ctx.new_page()
            ph.on("pageerror", lambda e: problems.append(f"script error (phone): {e}"))
            ph.on("console", lambda m: m.type == "error" and problems.append(f"console error (phone): {m.text}"))
            ph.on("response", lambda r: r.status >= 400 and problems.append(f"HTTP {r.status} (phone): {r.url}"))
            LOADED = "typeof EV !== 'undefined' && EV.length > 0"
            COPY = "caches.open('sanguity-offline').then(c => c.keys().then(ks => ks.length === 1 ? c.match(ks[0]).then(r => r.json()) : null))"
            NO_COPY = "caches.has('sanguity-offline').then(h => !h)"

            def until(expr, ms):   # page.wait_for_function evaluates a string, which the page's CSP refuses
                for _ in range(ms // 100):
                    if ph.evaluate(expr):
                        return
                    ph.wait_for_timeout(100)
                raise TimeoutError(f"waited {ms} ms for {expr}")

            def kept_copy(full=False):   # the copy, once the records page has written it
                c = None
                for _ in range(50):
                    c = ph.evaluate(COPY)
                    if c and (not full or (c.get("settings") and c.get("watch"))):
                        break
                    ph.wait_for_timeout(200)
                return c

            ph.goto(base + "/")
            ph.evaluate("localStorage.setItem('ht-profile', '1')")
            ph.reload()
            until(LOADED, 20000)
            until("!!(navigator.serviceWorker && navigator.serviceWorker.controller)", 15000)
            check("PWA: the service worker controls the page", ph.evaluate("navigator.serviceWorker.controller.scriptURL").endswith("/sw.js"))
            man = ph.evaluate("fetch(document.querySelector('link[rel=manifest]').href).then(async r => ({type: r.headers.get('content-type'), j: await r.json()}))")
            check("PWA: the manifest is served as a manifest, with its icons",
                  man["type"].startswith("application/manifest+json") and man["j"]["display"] == "standalone"
                  and {"192x192", "512x512"} <= {i["sizes"] for i in man["j"]["icons"]} and any(i.get("purpose") == "maskable" for i in man["j"]["icons"]), man["type"])
            errs = ctx.new_cdp_session(ph).send("Page.getInstallabilityErrors")["installabilityErrors"]
            check("PWA: Chromium finds nothing stopping the install", not [e for e in errs if e["errorId"] != "in-incognito"], errs)
            # the worker keeps the app's own files, and nothing from the API
            kept = dict(ph.evaluate("caches.keys().then(ks => Promise.all(ks.map(k => caches.open(k).then(c => c.keys()).then(rs => [k, rs.map(r => r.url)]))))"))
            shell = kept.get("sanguity-shell-v3", [])
            check("PWA: the app's pages, scripts, fonts and icons are kept",
                  any(u.endswith("/js/app.js") for u in shell) and any(u.endswith("/icons/icon-192.png") for u in shell)
                  and sum("/fonts/" in u and u.endswith(".woff2") for u in shell) == 4, len(shell))
            check("PWA: nothing from /api/ is kept, and no records until they're turned on",
                  not any("/api/" in u for u in shell) and "sanguity-offline" not in kept, list(kept))

            # offline, with offline records off: the app's shell, and why there are no records
            ctx.set_offline(True)
            ph.reload()
            ph.wait_for_selector("#offlineBanner", timeout=10000)
            text = ph.inner_text("#offlineBanner")
            check("offline, records off: the app opens and says records aren't available offline",
                  "Records aren't available offline" in text and ph.is_visible("header .brand-name") and ph.evaluate("typeof EV === 'undefined' || !EV.length"), text)
            offline_font = ph.evaluate("document.fonts.ready.then(() => [[...document.fonts].some(f => f.family === 'Inter' && f.status === 'loaded'), getComputedStyle(document.body).fontFamily.split(',')[0]])")
            check("offline (#253): the page is still in Inter", offline_font == [True, "Inter"], offline_font)
            ctx.set_offline(False)

            # installing (#56): without the browser's install prompt, the Account page says where to look; with it (a
            # stand-in event: headless Chromium never offers one), a button opens it, and the header shows Install app
            FAKE_PROMPT = ("(() => { const e = new Event('beforeinstallprompt'); e.prompt = () => { window.sgPrompted = (window.sgPrompted || 0) + 1; }; "
                           "e.userChoice = Promise.resolve({outcome: 'accepted'}); dispatchEvent(e); })()")
            ph.goto(base + "/account.html#device")
            ph.wait_for_selector("#installApp h2")
            check("Account: how to install on this device, without a prompt from the browser",
                  "Install app" in ph.inner_text("#installApp") and ph.locator("#installApp button").count() == 0, ph.inner_text("#installApp"))
            ph.evaluate(FAKE_PROMPT)
            ph.locator("#installApp button", has_text="Install on this device").click()
            until("document.querySelector('#installApp .msg') && document.querySelector('#installApp .msg').textContent.startsWith('Installed')", 5000)
            check("…with the browser's prompt, Install on this device opens it", ph.evaluate("window.sgPrompted") == 1)
            ph.goto(base + "/")
            until(LOADED, 20000)
            check("the header has no Install app button until the browser offers to install", ph.locator("#installBtn").is_hidden())
            ph.evaluate(FAKE_PROMPT)
            check("…then it does, and it opens the browser's prompt", ph.locator("#installBtn").is_visible())
            ph.click("#installBtn")
            until("window.sgPrompted === 1", 5000)
            check("…and it goes once used (a prompt opens once)", ph.locator("#installBtn").is_hidden())
            check("the header still fits 390 px with it", ph.evaluate("document.documentElement.scrollWidth") <= 390, ph.evaluate("document.documentElement.scrollWidth"))
            iphone = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                                         user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
            iphone.add_cookies([{"name": sg_config.COOKIE, "value": token, "url": base, "httpOnly": True, "sameSite": "Strict"}])
            ip = iphone.new_page()
            ip.on("pageerror", lambda e: problems.append(f"script error (iPhone): {e}"))
            ip.goto(base + "/account.html#install")
            ip.wait_for_selector("#installApp .install-steps", timeout=10000)
            steps = ip.inner_text("#installApp")
            check("on an iPhone: Safari's Share, then Add to Home Screen", "Share button" in steps and "Add to Home Screen" in steps, steps)
            check("…and the page fits 390 px", ip.evaluate("document.documentElement.scrollWidth") <= 390)
            iphone.close()

            # turned on under Account (off until then); the records loaded next are kept
            ph.goto(base + "/account.html#offline")
            ph.wait_for_selector("#offlineOn")
            check("Account: offline records are off by default, with what's kept explained",
                  not ph.is_checked("#offlineOn") and "anyone who can use this device while it's unlocked" in ph.inner_text("#offlineRecords"))
            ph.check("#offlineOn")
            ph.goto(base + "/")
            until(LOADED, 20000)
            copy = kept_copy(full=True)
            check("records on: one profile's records, settings and watch data are kept",
                  bool(copy) and copy["profile"]["id"] == 1 and bool(json.loads(copy["data"])["events"]) and bool(copy.get("settings")) and bool(copy.get("watch")),
                  copy and sorted(copy))
            # ---- offline records and details opened (#217): a record's details come when it's opened, so the copy keeps
            # those of the records opened here (a lab day's results; the documents, loaded as if each were opened), with
            # the records they belong to, and a reload of the same records keeps them; the others' aren't kept
            day = ph.evaluate("""(async () => { const e = EV.find(e => e.k === 'lab' && (DATA.details[e.id]?.ids || []).length > 1);
                await openEvent(e); closeDrawer(); return [e.id, ...DATA.details[e.id].ids]; })()""")
            docs = ph.evaluate("(async () => { const ids = EV.filter(e => e.k === 'document').map(e => e.id); await loadDetails(ids); return ids; })()")
            KEPT = COPY + ".then(c => !!c && " + json.dumps([str(i) for i in day[1:] + docs]) + ".every(k => k in (c.details || {})) && c.loaded)"
            until(KEPT, 5000)
            loaded = ph.evaluate(KEPT)
            ph.reload()
            until(LOADED, 20000)
            until(f"{KEPT}.then(t => t > {loaded})", 10000)
            look = ph.evaluate(COPY + """.then(c => ({files: Object.values(c.details).filter(d => d && d.type === 'file').length,
                claims: Object.values(c.details).filter(d => d && d.type === 'claim').length,
                listed: Object.values(JSON.parse(c.data).details).every(d => d.type === 'labday' || d.partial), rev: c.rev}))""")
            check("offline records (#217): the copy keeps the details of the records opened here, with their records' revision, after a reload too",
                  look["files"] == len(docs) and look["claims"] == 0 and look["listed"] and bool(look["rev"]), look)
            # offline: that profile, read-only, with when it was loaded
            ctx.set_offline(True)
            ph.reload()
            until(LOADED, 20000)
            text = ph.inner_text("#offlineBanner")
            name = ph.evaluate("PROFILE.name")
            check("offline, records on: the last profile opens read-only, with when it was loaded",
                  "Offline · read-only" in text and f"{name}, as loaded on" in text, text)
            ph.click("#tab-labs")
            ph.wait_for_selector("#charts svg", timeout=10000)
            check("offline: Trends draws from the kept copy", ph.evaluate("document.querySelectorAll('#charts svg').length") > 0)
            check("offline: Import, Settings and Account say they need the server",
                  ph.evaluate("[...document.querySelectorAll('header a.acct')].every(a => a.getAttribute('aria-disabled') === 'true' && /offline/.test(a.title))"))
            ph.evaluate("document.querySelector('#settingsLink').click()")
            ph.wait_for_timeout(300)
            check("offline: a link to another page isn't followed", ph.evaluate("location.pathname") == "/")
            check("offline: a change is refused as needing the server",
                  ph.evaluate("api('profiles/1/edits', {method: 'POST', body: '{}'}).then(async r => [r.status, (await r.json()).detail])") == [503, "You're offline. This needs the server."])
            ph.evaluate("""(async () => { await loadDetails(EV.filter(e => e.k === 'document').map(e => e.id));   // kept, as opened before (#217)
                return openEvent(byId.get(+Object.keys(DATA.details).find(k => DATA.details[k].type === 'file' && byId.get(+k)))); })()""")   # a PDF
            ph.wait_for_selector("#dbody .offline-note", timeout=5000)
            check("offline: a document isn't loaded, and says why",
                  ph.evaluate("!document.querySelector('#dbody iframe[src], #dbody img[src]')") and "aren't kept on this device" in ph.inner_text("#dbody"))
            ph.evaluate("id => openEvent(byId.get(id))", day[0])   # (#217) opened online before: its results, from the copy
            shown = ph.evaluate("[document.querySelectorAll('#dbody details.res table.data').length, !!document.querySelector('#dbody .details-missed')]")
            check("offline (#217): a lab day opened before shows its results from the copy", shown == [len(day) - 1, False], (shown, len(day) - 1))
            ph.evaluate("openEvent(EV.find(e => e.k === 'claim'))")   # never opened on this device
            miss = ph.evaluate("document.querySelector('#dbody .details-missed')?.textContent || ''")
            check("offline (#217): a record not opened before says its details weren't kept, and still shows its title and source",
                  "weren't kept on this device" in miss and ph.evaluate("document.getElementById('dtitle').textContent.length > 0 && !!document.querySelector('#dbody p.source')"), miss)
            ph.evaluate("closeDrawer()")
            ctx.set_offline(False)

            # turned off: the copy goes
            ph.goto(base + "/account.html#offline")
            ph.wait_for_selector("#offlineOn")
            until("document.querySelector('#offlineRecords .msg').textContent.includes('Kept: the records in ')", 5000)
            ph.uncheck("#offlineOn")
            until(NO_COPY, 5000)
            check("records off: the kept copy is deleted", ph.evaluate(NO_COPY))
            # on again: once the sign-in's end time has passed, the copy isn't shown, and is deleted, with no connection
            ph.check("#offlineOn")
            ph.goto(base + "/")
            until(LOADED, 20000)
            kept_copy()
            ph.evaluate("""caches.open('sanguity-offline').then(async c => { const k = (await c.keys())[0], j = await (await c.match(k)).json();
                           j.ends = Date.now() / 1000 - 1; await c.put(k, new Response(JSON.stringify(j))); })""")
            ctx.set_offline(True)
            ph.reload()
            ph.wait_for_selector("#offlineBanner", timeout=10000)
            check("offline, past the sign-in's end: no records, and the copy is deleted",
                  "Records aren't available offline" in ph.inner_text("#offlineBanner") and ph.evaluate(NO_COPY))
            ctx.set_offline(False)
            # signing out deletes the copy and the choice; the app's own files stay
            ph.goto(base + "/")
            until(LOADED, 20000)
            check("records on: kept again", bool(kept_copy()))
            menu_click(ph, "#logout")
            ph.wait_for_url("**/login.html", timeout=10000)
            until(NO_COPY, 5000)
            check("sign-out: the kept copy and the choice are deleted, the app's own files stay",
                  ph.evaluate(NO_COPY) and ph.evaluate("localStorage.getItem('sg-offline-records')") is None and ph.evaluate("caches.has('sanguity-shell-v3')"))
            # offline, the browser reports each request it couldn't make; signing out ends in 401s
            problems[before:] = [x for x in problems[before:] if "ERR_INTERNET_DISCONNECTED" not in x and "401" not in x]
            ctx.close()
        except Exception as e:   # report what was caught so far instead of a bare traceback
            problems.append(f"PWA check stopped: {stopped(e)}")
            if "ph" in locals():
                problems.append(f"phone page at {ph.url}: " + str(ph.evaluate("document.body.innerText.slice(0, 300)")))
        # ---- first run (#199): a fresh install, a second app on a data folder of its own, under the real CSP: setup,
        # then the getting-started guide opens by itself on the new, empty profile; it's named, a fictional C-CDA is
        # uploaded through Sources and the guide's way back, the phone is skipped, and the Overview shows the records.
        # Then the empty states: a second, empty profile's one next step, and every records tab's (a profile with only
        # a visit), Sources' Overview and Import history; at 375 px in light and dark, the guide's steps too
        fresh = None
        try:
            before = len(problems)
            fdir = os.path.join(TMP, "fresh")
            for d in ("keys", "watch-keys"):
                os.makedirs(os.path.join(fdir, d), exist_ok=True)
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0)); fport = s.getsockname()[1]
            fresh = subprocess.Popen([sys.executable, "-m", "server"], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={
                **os.environ, "SANGUITY_DATA": fdir, "SANGUITY_RECORDS": os.path.join(fdir, "records"), "SANGUITY_KEY_FILE": os.path.join(fdir, "keys", "data_key"),
                "SANGUITY_WATCH_FILE": os.path.join(fdir, "watch", "daily.json"), "SANGUITY_WATCH_KEYS": os.path.join(fdir, "watch-keys", "receiver-secrets.json"),
                "SANGUITY_SERVE_STATIC": "1", "HOST": "127.0.0.1", "PORT": str(fport)})
            fbase = f"http://127.0.0.1:{fport}"
            code_file = os.path.join(fdir, "setup-code.txt")
            for _ in range(300):   # up, with its setup code written
                try:
                    if os.path.exists(code_file) and urllib.request.urlopen(fbase + "/api/setup", timeout=1).status == 200:
                        break
                except Exception:
                    pass
                time.sleep(0.1)
            allowed.add(fbase)
            fctx = browser.new_context(viewport={"width": 1400, "height": 900})   # the real CSP: evaluate only, no wait_for_function
            fp = fctx.new_page()
            fp.on("request", elsewhere)
            fp.on("pageerror", lambda e: problems.append(f"script error (first run): {e}"))
            fp.on("console", lambda m: m.type == "error" and "401" not in m.text and problems.append(f"console error (first run): {m.text}"))

            def f_until(expr, ms=20000):
                for _ in range(ms // 100):
                    if fp.evaluate(expr):
                        return True
                    fp.wait_for_timeout(100)
                raise TimeoutError(f"waited {ms} ms for {expr}")
            guide_is = lambda: fp.evaluate("fetch('api/settings/user').then(r => r.json()).then(j => j.guide)")
            fp.goto(fbase + "/")
            fp.wait_for_url("**/setup.html", timeout=10000)
            fp.wait_for_selector("#c", timeout=10000)
            setup_axe = axe_check(fp, "setup.html, light")   # #202: axe-core's WCAG A and AA rules, under the real CSP
            fp.emulate_media(color_scheme="dark")
            setup_axe = [a + b for a, b in zip(setup_axe, axe_check(fp, "setup.html, dark"))]
            fp.emulate_media(color_scheme="no-preference")
            check("first run: setup has no serious or critical axe-core finding, in light and dark (WCAG 2.2 AA, #202)", not setup_axe[0],
                  (setup_axe[0] + setup_axe[1])[:8] or "")
            looks = {f: fp.evaluate(FIELD_LOOK_JS, f) for f in ("#c", "#u", "#p", "#p2", "#n")}
            check("first run: setup's fields look like the app's (#332): the same background, edge and corner as a field on Account, in light and dark",
                  acct_field and all(v == acct_field for v in looks.values()), [acct_field, looks])
            ring_bad, ring_seen = ring_walk(fp, "setup")
            check("…and Tab through setup, light and dark, rings every field and Create account in the accent, 2 px (#310)",
                  not ring_bad and all(f"{t} {i}" in ring_seen for t in ("light", "dark") for i in ("c", "u", "p", "p2", "n", "b")), ring_bad[:6] or ring_seen)
            with open(code_file, encoding="utf-8") as f:
                fp.fill("#c", f.read().strip())
            fp.fill("#u", "robin")
            fp.fill("#p", "a fictional first-run passphrase")
            fp.fill("#p2", "a fictional first-run passphrase")
            fp.click("#b")
            fp.wait_for_url("**/guide.html?profile=1#name", timeout=15000)
            fp.wait_for_selector("#step-name:not([hidden])", timeout=10000)
            steps_js = "[...document.querySelectorAll('#guideSteps li')].map(li => li.textContent.replace(/^[\\d✓]/, '').replace(' (done)', ''))"
            check("first run: setup signs in and the guide opens by itself on the new, empty profile, at whose records it holds",
                  fp.evaluate(steps_js) == ["Whose records", "First records", "Phone (optional)", "Overview"] and fp.input_value("#profName") == "robin"
                  and fp.is_visible("#step-name [data-skip]") and fp.is_visible("#skipGuide") and guide_is() == "", fp.evaluate(steps_js))
            fp.fill("#profName", "Robin Example")
            fp.click("#nameGo")
            fp.wait_for_selector("#step-records:not([hidden])", timeout=10000)
            check("…naming the profile there (who it's for), then the first records, each way skippable",
                  fp.evaluate("fetch('api/me').then(r => r.json()).then(j => j.profiles.map(p => p.name))") == ["Robin Example"]
                  and "Robin Example" in fp.inner_text("#profileOne") and fp.evaluate("location.hash") == "#records"
                  and fp.is_visible("#step-records [data-skip]") and fp.is_hidden("#step-records [data-next]"))
            ways = fp.evaluate("[...document.querySelectorAll('#step-records .guide-way')].map(a => a.getAttribute('href'))")
            check("…its ways in are Sources' own sections: upload, a portal or plan (#165's section), typing in",
                  ways == [f"sources.html?profile=1&guide=records#{g}" for g in ("upload", "portals", "manual")], ways)
            fp.click("#step-records a[data-to=upload]")
            fp.wait_for_selector("#importer", state="visible", timeout=10000)
            check("…Upload an export opens Sources → Upload files, saying it's part of the guide, with Back to the guide",
                  fp.is_visible("#guideBar") and fp.get_attribute("#guideBack", "href") == "guide.html?profile=1#records")
            fp.click("#tab-files")
            fp.set_input_files("#pickFiles", os.path.join(ROOT, "tests", "fixtures", "importers", "ccda", "maple-grove-ccd", "maple-grove-ccd.xml"))
            f_until("!document.getElementById('go').disabled", 5000)
            fp.click("#go")
            last = "[...document.querySelectorAll('.import-job')].pop()"
            f_until(f"!!{last} && /^(Ready to import|All duplicates|Not imported)/.test({last}.textContent)", 30000)
            fp.evaluate(f"{last}.querySelector('.imp-actions .primary').click()")
            f_until(f"/^(Imported|Not imported)/.test({last}.textContent)", 30000)
            check("…where a fictional C-CDA is uploaded, previewed and saved", fp.evaluate(f"{last}.textContent").startswith("Imported."), fp.evaluate(f"{last}.textContent")[:200])
            fp.click("#guideBack")
            fp.wait_for_selector("#recordsHave:not([hidden])", timeout=10000)
            check("…and back in the guide, the step says the records are in, with Continue in place of Skip",
                  "Robin Example has records now, from 1 import" in fp.inner_text("#recordsHave") and fp.is_visible("#step-records [data-next]")
                  and fp.is_hidden("#step-records [data-skip]") and fp.evaluate("document.querySelector('#guideSteps li.done')?.textContent") == "✓First records (done)",
                  fp.inner_text("#step-records")[:300])
            fp.click("#step-records [data-next]")
            fp.wait_for_selector("#step-phone:not([hidden])", timeout=10000)
            check("…then a phone, optional, set up on Sources → Phone and watch",
                  "(optional)" in fp.inner_text("#phoneTitle") and fp.get_attribute("#step-phone .guide-way", "href") == "sources.html?profile=1&guide=phone#phone")
            fp.click("#step-phone [data-skip]")
            fp.wait_for_selector("#step-done:not([hidden])", timeout=10000)
            f_until("document.getElementById('doneSays').textContent.length > 0", 5000)
            check("…skipped, to the end: the Overview, with nothing left to skip, the guide not yet marked done",
                  "Robin Example has records now. The Overview" in fp.inner_text("#doneSays") and fp.is_hidden("#guideSkip") and guide_is() == "", fp.inner_text("#step-done"))
            # every step, and Sources with the way back, at 375 px in light and dark
            fp.set_viewport_size({"width": 375, "height": 800})
            bad = []
            for theme in ("light", "dark"):
                fp.emulate_media(color_scheme=theme)
                for step in ("name", "records", "phone", "done"):
                    fp.goto(fbase + f"/guide.html?profile=1#{step}")
                    fp.wait_for_selector(f"#step-{step}:not([hidden])", timeout=10000)
                    fp.wait_for_timeout(150)
                    bad += phone_fits(fp, f"{theme} guide #{step}")
                fp.goto(fbase + "/sources.html?profile=1&guide=records#upload")
                fp.wait_for_selector("#guideBar", timeout=10000)
                fp.wait_for_timeout(150)
                bad += phone_fits(fp, f"{theme} Sources with the way back")
            dark_ok = fp.evaluate("matchMedia('(prefers-color-scheme: dark)').matches")
            fp.emulate_media(color_scheme=None)
            fp.set_viewport_size({"width": 1400, "height": 900})
            check("…each step of the guide, and the way back on Sources, fit 375 px in light and dark", not bad and dark_ok, bad[:8] or "")
            fp.goto(fbase + "/guide.html?profile=1#done")
            fp.wait_for_selector("#step-done:not([hidden])", timeout=10000)
            fp.click("#finish")
            fp.wait_for_url("**/?profile=1#overview", timeout=10000)
            f_until("typeof booted !== 'undefined' && booted && EV.length > 0")
            check("…Open the Overview lands on the profile's records, the guide marked done",
                  fp.evaluate("document.querySelectorAll('#view-overview .tile').length") > 0 and guide_is() == "done", guide_is())
            fp.goto(fbase + "/")
            f_until("typeof booted !== 'undefined' && booted")
            check("…and signing in again opens the records, not the guide; the menu still has Getting started",
                  fp.evaluate("location.pathname") == "/" and fp.get_attribute("#guideLink", "href") == "guide.html")
            # the empty states: a second, empty profile
            new_profile = lambda name: fp.evaluate("""n => fetch('api/profiles', {method: 'POST', headers: {'X-Sanguity': '1', 'Content-Type': 'application/json'},
                body: JSON.stringify({name: n})}).then(r => r.json()).then(j => j.id)""", name)
            pid2 = new_profile("Sam Example")
            fp.goto(fbase + f"/?profile={pid2}")
            fp.wait_for_selector("main .empty-state", timeout=10000)
            nexts = "sel => [...document.querySelectorAll(sel + ' .empty-next')].map(a => [a.textContent, a.getAttribute('href')])"
            check("empty states: an empty profile says what would appear and has one next step, Get started (the guide)",
                  fp.evaluate(nexts, "main") == [["Get started", f"guide.html?profile={pid2}"]] and "no records yet" in fp.inner_text("main")
                  and fp.evaluate("location.pathname") == "/", fp.evaluate(nexts, "main"))
            fp.goto(fbase + f"/sources.html?profile={pid2}")
            fp.wait_for_selector("#srcList .empty-state", timeout=10000)
            src_next = fp.evaluate(nexts, "#srcList")
            fp.click("#srcNav a[data-group=history]")
            fp.wait_for_selector("#historyList .empty-state", timeout=10000)
            check("…Sources' Overview and Import history: what would be listed, and one next step, Upload files",
                  src_next == [["Upload files", "#upload"]] and fp.evaluate(nexts, "#historyList") == [["Upload files", "#upload"]], (src_next, fp.evaluate(nexts, "#historyList")))
            # a profile with only a visit: every records tab says what would be there, with one next step
            r = fp.evaluate(f"""fetch('api/profiles/{pid2}/manual', {{method: 'POST', headers: {{'X-Sanguity': '1', 'Content-Type': 'application/json'}},
                body: JSON.stringify({{save: true, items: [{{kind: 'visit', date: '2026-10-01', title: 'Fictional check-up', who: 'Dr. Example', where: 'Example Clinic'}}]}})}}).then(r => r.status)""")
            want = {"overview": [["Add lab results", f"sources.html?profile={pid2}#manual"]], "health": [["Connect a phone", f"sources.html?profile={pid2}#phone"]],
                    "timeline": [["Show everything", None]], "labs": [["Add lab results", f"sources.html?profile={pid2}#manual"]],
                    "costs": [["Type in a claim or payment", f"sources.html?profile={pid2}#payment"]]}
            got, bad = {}, []
            fp.goto(fbase + f"/?profile={pid2}#overview")
            f_until("typeof booted !== 'undefined' && booted && EV.length > 0")
            for t in want:
                fp.click("#tab-" + t)
                if t == "timeline":
                    fp.fill("#q", "no such event")
                    f_until("!!document.querySelector('#list .empty-state')", 5000)
                got[t] = fp.evaluate(nexts, "#view-" + t)
            check("…every records tab with nothing to show (a profile with only a visit) says what would be there, with one next step",
                  r == 200 and got == want and fp.is_visible("#notice") and "Not medical advice" in fp.inner_text("#notice"), (r, got))
            fp.click("#tab-timeline")
            fp.click("#list .empty-next")
            f_until("document.querySelectorAll('#list .tl-row').length === 1 && state.q === '' && state.preset === 'all'", 5000)
            check("…the Timeline's Show everything clears the search and the period", fp.evaluate("document.activeElement.id") == "listTitle")
            fp.set_viewport_size({"width": 375, "height": 800})
            empty_axe = [[], []]
            for theme in ("light", "dark"):
                fp.emulate_media(color_scheme=theme)
                fp.goto(fbase + f"/?profile={pid2}#overview")
                f_until("typeof booted !== 'undefined' && booted")
                for t in want:
                    fp.click("#tab-" + t)
                    fp.wait_for_timeout(150)
                    bad += phone_fits(fp, f"{theme} {t}")
                    empty_axe = [a + b for a, b in zip(empty_axe, axe_check(fp, f"empty {t}, {theme}"))]   # #202
            fp.emulate_media(color_scheme=None)
            fp.set_viewport_size({"width": 1400, "height": 900})
            check("…and each fits 375 px, in light and dark", not bad, bad[:8] or "")
            check("…with no serious or critical axe-core finding (WCAG 2.2 AA, #202)", not empty_axe[0], (empty_axe[0] + empty_axe[1])[:8] or "")
            fctx.close()
        except Exception as e:   # report what was caught so far instead of a bare traceback
            problems.append(f"first-run check stopped: {stopped(e)}")
            if "fp" in locals():
                problems.append(f"first-run page at {fp.url}: " + str(fp.evaluate("document.body.innerText.slice(0, 300)")))
        finally:
            if fresh:
                fresh.terminate()
                fresh.wait(10)
        browser.close()
    srv.should_exit = True
    SHL.stop()
    time.sleep(0.3)
    shutil.rmtree(TMP, ignore_errors=True)

    for x in passed:
        print("ok    ", x)
    for x in problems:
        print("FAIL  ", x)
    print(f"\n{len(passed)} passed, {len(problems)} failed")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
