#!/usr/bin/env python3
"""Big records stay fast (#201): the performance budgets, checked on ten years of fictional records.

The fixture is the demo with --big (demo/generate.py): about 5,000 lab results in 47 tests, 2,000 claims, 1,000
documents (a small PDF each) and ten years of daily watch data, loaded as the setup build into its own temporary data
folder and served by the real backend. On it this measures, and fails when a budget in BUDGETS is missed:

  - the records API (GET /api/profiles/{pid}/data): its size as sent and as nginx compresses it, and how long it takes
    when the records changed since the last request (put together from the database) and when they didn't (kept);
    a lab day's details as the drawer opens it (#217: each record's details come when it's opened, so they aren't in
    the records' size; what they were with them is printed as records_whole_bytes); the watch data's size and time
    likewise
  - an import of a large export through the background job path: the profile's export zip uploaded into an empty
    profile, previewed and saved, and a ten-year Apple Health export.xml the same way
  - a connected source's first pull from the fake SMART on FHIR server (tests/fakefhir) serving a big patient, with
    its preview saved
  - in a headless browser: the records page ready on each tab (from the page's request to the app's
    "sanguity:ready" mark, app.js), each tab's first draw with the period "All", and how many elements each tab has
  - the app's static files (#253): its stylesheets, fonts and SVG icons, in bytes

Times are the median of several runs and their budgets leave room for CI's slower machines; sizes and element counts
are exact, so those budgets are tight. docs/performance.md says what each budget is for and what was measured.

  python3 tests/test_performance.py            (PERF_REPORT=path.json also writes the measurements there)
"""
import base64, gzip, json, os, random, shutil, socket, statistics, subprocess, sys, tempfile, threading, time, unittest
from datetime import date, datetime, timedelta
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-perf-test-")
END = "2026-11-15"
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "build"),
                  SANGUITY_SERVE_STATIC="1", SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"),
                  SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"), SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"))
os.makedirs(os.path.join(TMP, "records"))
os.makedirs(os.path.join(TMP, "keys"))
sys.path.insert(0, ROOT)

# ---------------------------------------------------------------- the budgets (docs/performance.md)
# Sizes and element counts are of the big fixture and exact, so their budgets are close to what they are. Times are in
# seconds (_s) or milliseconds (_ms), the median of RUNS runs, with room for CI's slower machines: about ten times
# what a developer's machine measures (docs/performance.md has those numbers).
BUDGETS = {
    "records_bytes": 2_600_000,         # GET /api/profiles/{pid}/data, the JSON the records page reads (details apart, #217)
    "records_gzip_bytes": 350_000,      # the same as nginx sends it (gzip at nginx's default level, 1)
    "records_cold_s": 2.5,              # .../data right after the records changed: put together from the database
    "records_s": 0.25,                  # .../data again, nothing changed (kept, records.served)
    "details_s": 0.25,                  # GET .../details?ids=: a lab day's results, as the drawer opens it (#217)
    "watch_bytes": 1_500_000,           # GET .../watch: ten years of daily watch data, and home readings (#180, #184)
    "watch_s": 0.5,
    "build_load_s": 5,                  # the big build loaded into a profile (the first start, `setup rebuild`)
    "export_s": 6,                      # "Export everything" of the big profile, a zip with its 1,000 documents
    "fhir_export_s": 16,                # the same as FHIR R4 (#205): every record a resource, ten years of daily measures
    "import_s": 10,                     # that zip imported into an empty profile: uploaded, previewed and saved
    "apple_health_s": 10,               # a ten-year Apple Health export.xml (17 MB) imported the same way
    "sync_s": 15,                       # a connected source's first pull of a big patient, its preview saved
    "page_ready_ms": 1500,              # the records page ready, loaded on each tab
    "tab_draw_ms": 1000,                # a tab's first draw with the period "All", the page loaded on another tab
    "tab_elements": 18_000,             # elements in a tab with the period "All"
    "phone_page_ready_ms": 5000,        # the same at 375 px with the CPU four times slower, like a phone's
    "phone_tab_draw_ms": 3000,
    "static_bytes": 390_000,            # www/'s stylesheets, fonts and SVG icons as on disk, whatever the records (#253):
                                        # room for an icon sprite (10 to 20 KB) and a little more CSS, no more (#328)
}
RUNS = 3

from server import atrest, config, db as dbm, main, profiles as prof, records, security, sync   # noqa: E402
from tests.fakefhir import FakeFHIR   # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
USER, PW = "demo", "a long demo passphrase"
MEASURED = {}
BASE = None


def measure(name, value):
    MEASURED[name] = value
    return value


def median_time(f, runs=RUNS):
    times = []
    for _ in range(runs):
        t = time.perf_counter()
        f()
        times.append(time.perf_counter() - t)
    return statistics.median(times)


# ---------------------------------------------------------------- the server, on the big fixture

def setUpModule():
    global BASE, CLIENT, SYNC_PATCH, SERVER
    subprocess.run([sys.executable, "-I", os.path.join(ROOT, "demo", "generate.py"), os.path.join(TMP, "build"), "--end", END, "--big",
                    "--watch", os.path.join(TMP, "watch")], check=True, stdout=subprocess.DEVNULL)
    dbm.init()
    db = dbm.connect()
    atrest.check_key(db)
    atrest.load_pepper(db)
    t = time.perf_counter()
    prof.bootstrap(db)   # the build into profile 1, as at the first start
    measure("build_load_s", time.perf_counter() - t)
    uid = db.execute("INSERT INTO users(username, pw_hash, is_admin, created) VALUES(?, ?, 1, 0)", (USER, security.hash_password(PW))).lastrowid
    db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, 1, 'owner')", (uid,))
    for name in ("Imported (fictional)", "Apple Health (fictional)", "Connected (fictional)"):
        prof.create_profile(db, name, owner_id=uid)
    db.close()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    BASE = f"http://127.0.0.1:{port}"
    config.PUBLIC_ORIGINS.add(BASE)       # the return address a connected source signs in back to
    config.CONNECT_ALLOW_HTTP = True      # the fake SMART on FHIR server is plain HTTP on this machine
    SYNC_PATCH = mock.patch.object(sync, "start", lambda: None)   # the pull is started by the test, and timed
    SYNC_PATCH.start()
    import uvicorn
    SERVER = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=SERVER.run, daemon=True).start()
    while not SERVER.started:
        time.sleep(0.05)
    import httpx
    CLIENT = httpx.Client(base_url=BASE, timeout=120)
    r = CLIENT.post("/api/login", json={"username": USER, "password": PW}, headers=H)
    assert r.status_code == 200, r.text


def tearDownModule():
    CLIENT.close()
    SERVER.should_exit = True
    SYNC_PATCH.stop()
    width = max(map(len, BUDGETS))
    print("\nmeasured on the big fixture (budget):", file=sys.stderr)
    for k, budget in BUDGETS.items():
        v = MEASURED.get(k)
        shown = "-" if v is None else f"{v:,.2f}" if isinstance(v, float) else f"{v:,}"
        print(f"  {k:<{width}}  {shown:>12}  ({budget:,})", file=sys.stderr)
    for k, v in MEASURED.items():
        if k not in BUDGETS:
            print(f"  {k:<{width}}  {json.dumps(v)}", file=sys.stderr)
    if os.environ.get("PERF_REPORT"):
        with open(os.environ["PERF_REPORT"], "w", encoding="utf-8") as f:
            json.dump(MEASURED, f, indent=1)
    shutil.rmtree(TMP, ignore_errors=True)


def wait_job(pid, jid, timeout=300):
    """The job once done or failed; its preview is confirmed (saved) on the way."""
    end = time.time() + timeout
    while time.time() < end:
        j = CLIENT.get(f"/api/profiles/{pid}/jobs/{jid}").json()
        if j["state"] == "preview":
            r = CLIENT.post(f"/api/profiles/{pid}/jobs/{jid}/confirm", headers=H)
            assert r.status_code == 202, r.text
        elif j["state"] in ("done", "failed"):
            return j
        time.sleep(0.05)
    raise AssertionError(f"job {jid} didn't finish: {j}")


def upload(pid, files):
    """An upload of {path: bytes} through the API, as the import page sends one; returns its id."""
    up = CLIENT.post(f"/api/profiles/{pid}/uploads", headers=H).json()["id"]
    for path, data in files.items():
        r = CLIENT.put(f"/api/profiles/{pid}/uploads/{up}/files", params={"path": path}, content=data,
                       headers={**H, "Content-Type": "application/octet-stream"})
        assert r.status_code == 200, r.text
    r = CLIENT.post(f"/api/profiles/{pid}/uploads/{up}/finish", headers=H)
    assert r.status_code == 200, r.text
    return up


def count(pid, table):
    db = dbm.connect()
    try:
        return db.execute(f"SELECT COUNT(*) FROM {table} WHERE profile_id=?", (pid,)).fetchone()[0]
    finally:
        db.close()


# ---------------------------------------------------------------- a ten-year Apple Health export, fictional

def apple_health_xml(days=3652, seed=201):
    """export.xml with what a watch and a scale record each day for ten years: steps and heart rate through the day,
    resting heart rate, HRV, a night's sleep in stages, a weight every few days and a workout every few days; and
    (#223) a glucose meter's reading a day, home blood pressure every other day and a temperature once a week."""
    rnd, end = random.Random(seed), date.fromisoformat(END)
    out = ['<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE HealthData [\n<!ELEMENT HealthData (ExportDate,Me,(Record|Workout)*)>\n]>\n'
           '<HealthData locale="en_US">\n <ExportDate value="2026-11-15 09:00:00 -0500"/>\n <Me HKCharacteristicTypeIdentifierBiologicalSex="HKBiologicalSexNotSet"/>\n']

    def rec(kind, unit, value, start, minutes=1):
        s, e = start.strftime("%Y-%m-%d %H:%M:%S -0500"), (start + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S -0500")
        out.append(f' <Record type="HKQuantityTypeIdentifier{kind}" sourceName="Example Watch" unit="{unit}" creationDate="{e}" '
                   f'startDate="{s}" endDate="{e}" value="{value}"/>\n')
    for n in range(days):
        d = datetime.combine(end - timedelta(days=days - n), datetime.min.time())
        for h in (8, 11, 14, 17, 19, 21):
            rec("StepCount", "count", rnd.randint(300, 2500), d + timedelta(hours=h), 30)
            rec("HeartRate", "count/min", rnd.randint(58, 110), d + timedelta(hours=h, minutes=5))
        rec("RestingHeartRate", "count/min", rnd.randint(54, 64), d + timedelta(hours=7))
        rec("HeartRateVariabilitySDNN", "ms", rnd.randint(30, 60), d + timedelta(hours=3))
        night = d - timedelta(hours=1)
        for stage, mins in (("AsleepCore", 240), ("AsleepDeep", 70), ("AsleepREM", 95)):
            s, e = night.strftime("%Y-%m-%d %H:%M:%S -0500"), (night + timedelta(minutes=mins)).strftime("%Y-%m-%d %H:%M:%S -0500")
            out.append(f' <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Example Watch" creationDate="{e}" '
                       f'startDate="{s}" endDate="{e}" value="HKCategoryValueSleepAnalysis{stage}"/>\n')
            night += timedelta(minutes=mins)
        rec("BloodGlucose", "mg/dL", rnd.randint(80, 140), d + timedelta(hours=7, minutes=20))   # a meter's, daily (#223)
        if n % 2 == 0:   # home blood pressure every other day, as Apple writes it: the records, and the correlation (#223)
            s = (d + timedelta(hours=7, minutes=30)).strftime("%Y-%m-%d %H:%M:%S -0500")
            sys_, dia = rnd.randint(110, 135), rnd.randint(70, 88)
            parts = [f'<Record type="HKQuantityTypeIdentifierBloodPressure{k}" sourceName="Example Cuff" unit="mmHg" startDate="{s}" endDate="{s}" value="{v}"/>'
                     for k, v in (("Systolic", sys_), ("Diastolic", dia))]
            out.append("".join(f" {p}\n" for p in parts) + f' <Correlation type="HKCorrelationTypeIdentifierBloodPressure" sourceName="Example Cuff" '
                       f'startDate="{s}" endDate="{s}">\n' + "".join(f"  {p}\n" for p in parts) + " </Correlation>\n")
        if n % 7 == 0:
            rec("BodyTemperature", "degF", round(97.8 + rnd.uniform(0, 1.2), 1), d + timedelta(hours=7, minutes=40))   # (#223)
        if n % 3 == 0:
            rec("BodyMass", "lb", round(178 + rnd.uniform(-2, 2), 1), d + timedelta(hours=7, minutes=10))
            s, e = (d + timedelta(hours=18)).strftime("%Y-%m-%d %H:%M:%S -0500"), (d + timedelta(hours=18, minutes=40)).strftime("%Y-%m-%d %H:%M:%S -0500")
            out.append(f' <Workout workoutActivityType="HKWorkoutActivityTypeWalking" duration="40" durationUnit="min" sourceName="Example Watch" '
                       f'creationDate="{e}" startDate="{s}" endDate="{e}"/>\n')
    out.append("</HealthData>\n")
    return "".join(out).encode()


# ---------------------------------------------------------------- a big patient on the fake FHIR server, fictional

LOINC = [("718-7", "Hemoglobin", "g/dL", 13.5, 17.5), ("777-3", "Platelets", "10*3/uL", 150, 400), ("6690-2", "WBC", "10*3/uL", 4, 11),
         ("2160-0", "Creatinine", "mg/dL", 0.7, 1.3), ("2345-7", "Glucose", "mg/dL", 70, 99), ("2951-2", "Sodium", "mmol/L", 136, 145),
         ("2823-3", "Potassium", "mmol/L", 3.5, 5.1), ("1742-6", "ALT", "U/L", 7, 56), ("1920-8", "AST", "U/L", 10, 40),
         ("2093-3", "Cholesterol", "mg/dL", 125, 200)]


def big_patient(fake, seed=201):
    """Ten years of a fictional patient's records added to pat-1: 5,000 lab results on 500 days, each day's in a
    report, 500 visits, 1,000 claims and 200 documents with a small PDF each."""
    rnd, end = random.Random(seed), date.fromisoformat(END)
    for n in range(500):
        day = end - timedelta(days=3650 - n * 7)
        t = f"{day.isoformat()}T08:{rnd.randint(10, 59)}:00-05:00"
        obs = []
        for code, name, unit, lo, hi in LOINC:
            v = round(rnd.uniform(lo * 0.9, hi * 1.05), 1)
            obs.append(fake.add({"resourceType": "Observation", "status": "final",
                                 "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "laboratory"}]}],
                                 "code": {"coding": [{"system": "http://loinc.org", "code": code, "display": name}], "text": name},
                                 "effectiveDateTime": t, "valueQuantity": {"value": v, "unit": unit},
                                 "referenceRange": [{"low": {"value": lo, "unit": unit}, "high": {"value": hi, "unit": unit}}]}, patient="pat-1"))
        fake.add({"resourceType": "DiagnosticReport", "status": "final", "category": [{"coding": [{"code": "LAB"}]}],
                  "code": {"text": "ROUTINE PANEL"}, "effectiveDateTime": t, "result": [{"reference": f"Observation/{o['id']}"} for o in obs]},
                 patient="pat-1")
        fake.add({"resourceType": "Encounter", "status": "finished", "class": {"code": "AMB"}, "type": [{"text": "Office visit"}],
                  "period": {"start": t, "end": t}, "reasonCode": [{"text": "Routine check-up"}]}, patient="pat-1")
        for k in range(2):
            billed = round(rnd.uniform(90, 480), 2)
            fake.add({"resourceType": "ExplanationOfBenefit", "status": "active", "use": "claim", "outcome": "complete",
                      "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/claim-type", "code": "professional"}]},
                      "created": f"{day.isoformat()}T00:00:00Z", "billablePeriod": {"start": day.isoformat(), "end": day.isoformat()},
                      "insurer": {"display": "Example Health Plan (fictional)"}, "provider": {"display": "Lakeside Family Medicine (fictional)"},
                      "item": [{"sequence": 1, "productOrService": {"text": ["Office visit", "Laboratory tests"][k]}, "servicedDate": day.isoformat(),
                                "adjudication": [{"category": {"coding": [{"code": "submitted"}]}, "amount": {"value": billed, "currency": "USD"}},
                                                 {"category": {"coding": [{"code": "benefit"}]}, "amount": {"value": round(billed * 0.6, 2), "currency": "USD"}}]}],
                      "total": [{"category": {"coding": [{"code": "submitted"}]}, "amount": {"value": billed, "currency": "USD"}}]}, patient="pat-1")
        if n % 5 == 0:
            pdf = b"%PDF-1.4\n% fictional document " + str(n).encode() + b"\n%%EOF\n"
            b = fake.add({"resourceType": "Binary", "contentType": "application/pdf", "data": base64.b64encode(pdf).decode()})
            fake._owner[("Binary", b["id"])] = "pat-1"
            fake.add({"resourceType": "DocumentReference", "status": "current", "type": {"text": "Visit summary"}, "date": t,
                      "content": [{"attachment": {"contentType": "application/pdf", "url": f"Binary/{b['id']}", "title": "Visit summary"}}]},
                     patient="pat-1")


# ---------------------------------------------------------------- the server's budgets

class ServerBudgets(unittest.TestCase):
    def assertBudget(self, name, value):
        measure(name, value)
        self.assertLessEqual(value, BUDGETS[name], f"{name} is over its budget (docs/performance.md): {value:,} > {BUDGETS[name]:,}")

    def test_build_load(self):
        self.assertGreater(count(1, "events"), 5000, "the big fixture was loaded")
        self.assertBudget("build_load_s", MEASURED["build_load_s"])

    def test_records_api(self):
        def cold():
            records.forget()   # as after an import or an edit (records_rev changes)
            r = CLIENT.get("/api/profiles/1/data")
            self.assertEqual(r.status_code, 200)
        self.assertBudget("records_cold_s", median_time(cold))
        body = CLIENT.get("/api/profiles/1/data").content
        self.assertBudget("records_s", median_time(lambda: CLIENT.get("/api/profiles/1/data")))
        self.assertBudget("records_bytes", len(body))
        self.assertBudget("records_gzip_bytes", len(gzip.compress(body, 1)))
        d = json.loads(body)
        measure("records_parts_bytes", {k: len(json.dumps(v, separators=(",", ":"), ensure_ascii=False).encode()) for k, v in d.items()
                                        if isinstance(v, (list, dict))})
        self.assertGreaterEqual(sum(s["n"] for s in d["labs"]), 5000)
        self.assertEqual(len(d["costs"]), 2000)
        self.assertGreaterEqual(sum(e["k"] == "document" for e in d["events"]), 1000)
        # #217: each record's details come when it's opened; what the page read before, with all of them
        db = dbm.connect()
        try:
            whole = records.served(db, 1)
        finally:
            db.close()
        measure("records_whole_bytes", len(whole))
        measure("records_whole_gzip_bytes", len(gzip.compress(whole, 1)))
        day = next(e for e in d["events"] if e["k"] == "lab" and len(d["details"].get(str(e["id"]), {}).get("ids", [])) > 5)
        url = f"/api/profiles/1/details?ids={','.join(map(str, d['details'][str(day['id'])]['ids']))}"
        got = CLIENT.get(url)
        self.assertEqual(got.status_code, 200)
        self.assertTrue(all(x["type"] == "result" and x["rows"] for x in got.json()["details"].values()))
        self.assertBudget("details_s", median_time(lambda: CLIENT.get(url)))
        measure("details_lab_day_bytes", len(got.content))

    def test_watch_api(self):
        body = CLIENT.get("/api/profiles/1/watch").content
        self.assertBudget("watch_bytes", len(body))
        self.assertBudget("watch_s", median_time(lambda: CLIENT.get("/api/profiles/1/watch")))
        self.assertGreaterEqual(len(json.loads(body)["metrics"][0]["pts"]), 3400, "ten years of days")

    def test_export_and_import(self):
        t = time.perf_counter()
        r = CLIENT.get("/api/profiles/1/export")
        self.assertBudget("export_s", time.perf_counter() - t)
        self.assertEqual(r.status_code, 200)
        measure("export_zip_bytes", len(r.content))
        t = time.perf_counter()
        f = CLIENT.get("/api/profiles/1/export?format=fhir")   # #205
        self.assertBudget("fhir_export_s", time.perf_counter() - t)
        self.assertEqual(f.status_code, 200)
        measure("fhir_export_zip_bytes", len(f.content))
        t = time.perf_counter()
        up = upload(2, {"export.zip": r.content})
        jid = CLIENT.post(f"/api/profiles/2/uploads/{up}/import", headers=H).json()["id"]
        j = wait_job(2, jid)
        self.assertBudget("import_s", time.perf_counter() - t)
        self.assertEqual((j["state"], j["importer"]), ("done", "sanguity"), j)
        self.assertEqual(count(2, "events"), count(1, "events"))
        self.assertEqual(count(2, "documents"), count(1, "documents"))

    def test_apple_health_import(self):
        xml = apple_health_xml()
        measure("apple_health_xml_bytes", len(xml))
        t = time.perf_counter()
        up = upload(3, {"apple_health_export/export.xml": xml})
        jid = CLIENT.post(f"/api/profiles/3/uploads/{up}/import", headers=H).json()["id"]
        j = wait_job(3, jid)
        self.assertBudget("apple_health_s", time.perf_counter() - t)
        self.assertEqual((j["state"], j["importer"]), ("done", "apple-health"), j)
        self.assertGreaterEqual(j["counts"]["watch_days"], 3600)

    def test_sync_pull(self):
        with FakeFHIR(patients=["robin-example"], max_count=1000) as fake:
            big_patient(fake)
            cid = f"perf-{fake.port}"
            fake.register_client(cid, [BASE + "/connect/return"])
            r = CLIENT.post("/api/connect/sources", headers=H, json={"name": "Fake portal (fictional)", "vendor": "epic",
                                                                       "issuer": fake.fhir_base, "client_id": cid, "auth": "public"})
            self.assertEqual(r.status_code, 201, r.text)
            sid = r.json()["id"]
            self.assertTrue(CLIENT.post(f"/api/connect/sources/{sid}/test", headers=H).json()["ok"])
            url = CLIENT.post("/api/profiles/4/connect/start", json={"source": sid}, headers=H).json()["url"]
            back = fake.authorize(url, patient="pat-1")
            r = CLIENT.post("/api/connect/return", headers=H, json={"code": back.code, "state": back.state})
            self.assertEqual(r.status_code, 200, r.text)
            conn = r.json()["connection"]
            t = time.perf_counter()
            db = dbm.connect()
            try:
                res = sync.pull(db, conn)
            finally:
                db.close()
            self.assertTrue(res["ok"], res)
            j = wait_job(4, res["job"])
            self.assertBudget("sync_s", time.perf_counter() - t)
            self.assertEqual(j["state"], "done", j)
            measure("sync_requests", len(fake.requests))
        self.assertGreaterEqual(count(4, "results"), 5000)


# ---------------------------------------------------------------- the static files' budget (#253)

class StaticBudgets(unittest.TestCase):
    def test_static_bytes(self):
        """the stylesheets, fonts and SVG icons in www/, as on disk: what the redesign adds to the app's own files. A
        page fetches its stylesheets and Inter's Latin file; the other fonts only when a page has a character or a
        title in them, and the service worker keeps them all"""
        def sizes(sub, ext):
            folder = os.path.join(ROOT, "www", sub)
            return {n: os.path.getsize(os.path.join(folder, n)) for n in sorted(os.listdir(folder)) if n.endswith(ext)}
        parts = {"css": sizes("css", ".css"), "fonts": sizes("fonts", ".woff2"), "icons": sizes("icons", ".svg")}
        self.assertTrue(parts["css"] and parts["fonts"], parts)
        measure("static_parts_bytes", {k: sum(v.values()) for k, v in parts.items()})
        css = b""
        for n in parts["css"]:
            with open(os.path.join(ROOT, "www", "css", n), "rb") as f:
                css += f.read()
        measure("static_css_gzip_bytes", len(gzip.compress(css, 1)))   # the stylesheets as nginx sends them
        total = measure("static_bytes", sum(sum(v.values()) for v in parts.values()))
        self.assertLessEqual(total, BUDGETS["static_bytes"],
                             f"static_bytes is over its budget (docs/performance.md): {total:,} > {BUDGETS['static_bytes']:,} {parts}")


# ---------------------------------------------------------------- the page's budgets, in a headless browser

READY_JS = "() => { const m = performance.getEntriesByName('sanguity:ready')[0]; return m ? m.startTime : null; }"
# a tab's first draw: from choosing it to the frame after (style, layout and paint included), and how many elements
# the tab has then (each tab's own; tabs not shown keep theirs, as they were last drawn)
DRAW_JS = """async t => { const [tab, view] = t.split("-");
  const s = performance.now(); switchTab(tab, view === "heatmap" ? "heatmap" : "chart", true);
  await new Promise(r => requestAnimationFrame(() => setTimeout(r)));
  return [performance.now() - s, document.getElementById("view-" + tab).getElementsByTagName("*").length]; }"""
TABS = ("overview", "health", "timeline", "labs", "labs-heatmap", "costs")


def page_times(viewport, throttle=1):
    """{tab: median ms to the page ready, loaded on that tab}, {tab: median ms of its first draw with the period
    "All"}, {tab: its elements}, any script or console errors, and what Costs' out-of-pocket list shows before and
    after Show 200 more. throttle: Chromium's CPU slowdown (4: a phone)."""
    from playwright.sync_api import sync_playwright
    errors, ready_ms, draw_ms, elements = [], {}, {}, {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=viewport)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(BASE + "/login.html")
        page.fill("#u", USER)
        page.fill("#p", PW)
        page.click("#b")
        page.wait_for_url(lambda u: "login" not in u)
        page.context.new_cdp_session(page).send("Emulation.setCPUThrottlingRate", {"rate": throttle})

        def ready():
            for _ in range(1200):
                v = page.evaluate(READY_JS)
                if v is not None:
                    return v
                time.sleep(0.05)
            raise AssertionError(f"the records page wasn't ready within a minute: {page.url} {page.inner_text('body')[:300]!r}")
        for tab in TABS:   # the page loaded on each tab, as a link or a reload would; profile 1 is the big one
            runs = []
            for n in range(RUNS):
                page.goto(f"{BASE}/index.html?profile=1&run={n}#{tab}")
                runs.append(ready())
            ready_ms[tab] = round(statistics.median(runs))
        page.evaluate("() => applyPreset('all')")   # the most there is to draw, ten years; kept for the loads below
        for tab in TABS:   # the first time the tab is drawn since the page loaded on another (what it works out once is in that)
            runs = []
            for n in range(RUNS):
                page.goto(f"{BASE}/index.html?profile=1&draw={n}#{'timeline' if tab == 'overview' else 'overview'}")
                ready()
                ms, elements[tab] = page.evaluate(DRAW_JS, tab)
                runs.append(ms)
            draw_ms[tab] = round(statistics.median(runs))
        # Costs lists the out-of-pocket amounts 200 at a time (the page is still on it): Show 200 more adds the next
        # 200, the first of them focused
        rows = "() => document.querySelectorAll('#paidTable tbody tr').length"
        more = page.locator("#paidTable > button.btn")
        paged = [page.evaluate(rows), more.inner_text()]
        more.click()
        paged += [page.evaluate(rows), page.evaluate("() => document.querySelector('#paidTable tbody tr:nth-child(201)').contains(document.activeElement)")]   # its button (#202)
        browser.close()
    return ready_ms, draw_ms, elements, errors, paged


class PageBudgets(unittest.TestCase):
    def check(self, prefix, by_tab):
        over = {}
        for name, values in by_tab.items():
            measure(prefix + name + "_by_tab", values)
            measure(prefix + name, max(values.values()))
            over.update({f"{prefix + name} on {t}": f"{v:,} > {BUDGETS[prefix + name]:,}" for t, v in values.items() if v > BUDGETS[prefix + name]})
        self.assertEqual(over, {}, "over budget (docs/performance.md)")

    def test_desktop(self):
        ready_ms, draw_ms, elements, errors, paged = page_times({"width": 1400, "height": 900})
        self.assertEqual(errors, [])
        self.assertEqual(paged, [200, "Show 200 more", 400, True])
        self.check("", {"page_ready_ms": ready_ms, "tab_draw_ms": draw_ms, "tab_elements": elements})

    def test_phone(self):   # 375 px wide, with the CPU four times slower
        ready_ms, draw_ms, _, errors, _ = page_times({"width": 375, "height": 800}, throttle=4)
        self.assertEqual(errors, [])
        self.check("phone_", {"page_ready_ms": ready_ms, "tab_draw_ms": draw_ms})


if __name__ == "__main__":
    unittest.main(verbosity=2)
