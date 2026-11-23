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
    "tab_elements": 12_000,             # elements in a tab with the period "All" (Body and activity decimated, #292)
    "phone_page_ready_ms": 5000,        # the same at 375 px with the CPU four times slower, like a phone's
    "phone_tab_draw_ms": 3000,
    "pan_frame_ms": 150,                # a frame of dragging a tab's charts with the period "All" (#292): every chart drawn
    "phone_pan_frame_ms": 250,          # again for the moved window, to the frame after; on a phone as above (CI's runner
                                        # is about 2.2 times slower; the heatmap's pixel columns one path a colour, #300)
    "share_page_px": 20_000,            # a share link's page with every section, of any date, at 1400 px (#491)
    "static_bytes": 310_000,           # www/'s stylesheets, fonts and SVG icons as nginx sends them, whatever the records
                                        # (#253; compressed as sent since Redesign 4, the owner's choice): room for R4–R7
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
        """the stylesheets, fonts and SVG icons in www/, as nginx sends them: what the redesign adds to the app's own
        files. Stylesheets and icons go compressed (gzip_types in nginx.conf, level 1, files of 1 KB and more), fonts as
        they are (woff2 is compressed already), so a comment costs next to nothing and a rule what it really adds. A page
        fetches its stylesheets and Inter's Latin file; the other fonts only when a page has a character or a title in
        them, and the service worker keeps them all"""
        def sizes(sub, ext, packed):
            folder, out = os.path.join(ROOT, "www", sub), {}
            for n in sorted(os.listdir(folder)):
                if n.endswith(ext):
                    with open(os.path.join(folder, n), "rb") as f:
                        b = f.read()
                    out[n] = len(gzip.compress(b, 1)) if packed and len(b) >= 1024 else len(b)
            return out
        parts = {"css": sizes("css", ".css", True), "fonts": sizes("fonts", ".woff2", False), "icons": sizes("icons", ".svg", True)}
        self.assertTrue(parts["css"] and parts["fonts"], parts)
        measure("static_parts_bytes", {k: sum(v.values()) for k, v in parts.items()})
        raw = sum(os.path.getsize(os.path.join(ROOT, "www", "css", n)) for n in parts["css"])
        measure("static_css_raw_bytes", raw)   # the stylesheets on disk, comments and all
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
TABS = ("overview", "health", "medications", "timeline", "labs", "labs-heatmap", "costs")   # Medications since #275
# every card of Body and activity's with Chart | Table, shown as a table (#460): each its latest 200 rows, Show more for
# the rest; {all: every card's choice is Table, more: the Show more buttons}
H_TABLES = ["#h-body", "#h-heart", "#h-bp", "#h-glucose", "#h-temp", "#h-oxygen", "#h-resp", "#h-sleep", "#h-activity", "#h-zones", "#h-vo2", "#h-growth"]
H_TABLES_JS = """() => { const segs = [...document.querySelectorAll('#view-health .view-seg')];
  return {all: segs.length > 0 && segs.every(s => s.querySelector('[aria-pressed=true]').textContent === 'Table'), more: document.querySelectorAll('#view-health .h-more').length}; }"""
# Body and activity's sections on the ten years, each with its cards (#276)
SECTIONS = [["hs-body", ["h-body"]], ["hs-vitals", ["h-heart", "h-bp", "h-glucose", "h-temp", "h-oxygen"]], ["hs-sleep", ["h-sleep"]], ["hs-activity", ["h-activity"]]]
# the Timeline's Events list with the period "All" (#277): its first page, then Show more until every event it lists is
# drawn, each press timed to the frame after; {want: the events it lists, first: rows drawn at first, rows: drawn at the
# end, presses, ms: the slowest press, focused: the first new row has focus, elements: the tab's with everything shown}
TL_ALL_JS = """async () => { const frame = () => new Promise(r => requestAnimationFrame(() => setTimeout(r)));
  switchTab("timeline", "timeline"); await frame();
  const rows = () => document.querySelectorAll("#list .tl-row[data-id]").length, want = tlListed().length, first = rows(), times = [];
  for (let b; (b = document.getElementById("tlMore"));) { const s = performance.now(); b.click(); await frame(); times.push(performance.now() - s); }
  return {want, first, rows: rows(), presses: times.length, ms: Math.round(Math.max(0, ...times)), focused: !times.length || document.activeElement.matches("#list .tl-row"),
    elements: document.getElementById("view-timeline").getElementsByTagName("*").length}; }"""
# a drag across a tab's charts with the period "All" (#292): pointer events as the mouse sends them, 8 px a frame, left
# then back; each frame timed from the move to the frame after it (panZoom draws every chart again in it), the median
PAN_TABS = ("health", "labs", "labs-heatmap")
PAN_JS = """async t => { const [tab, view] = t.split("-"), frame = () => new Promise(r => requestAnimationFrame(() => setTimeout(r)));
  switchTab(tab, view === "heatmap" ? "heatmap" : "chart", true); await frame(); await frame();
  const svg = document.querySelector(tab === "health" ? "#view-health svg.ck" : view === "heatmap" ? "#hm" : "#charts .chart svg");
  svg.scrollIntoView({block: "center"}); await frame();
  const r = svg.getBoundingClientRect(), y = r.top + r.height / 2, times = [];
  const at = (type, x) => svg.dispatchEvent(new PointerEvent(type, {bubbles: true, pointerId: 7, pointerType: "mouse", button: 0, buttons: 1, isPrimary: true, clientX: x, clientY: y}));
  let x = r.left + r.width / 2; at("pointerdown", x);
  for (let i = 0; i < 24; i++) { x += i < 12 ? -8 : 8; const s = performance.now(); at("pointermove", x); await frame(); times.push(performance.now() - s); }
  at("pointerup", x); await new Promise(r => setTimeout(r, 400)); applyPreset("all"); await frame();   // after it springs back (#382)
  return Math.round(times.sort((p, q) => p - q)[12]); }"""


def page_times(viewport, throttle=1):
    """{tab: median ms to the page ready, loaded on that tab}, {tab: median ms of its first draw with the period
    "All"}, {tab: its elements}, {tab: the median frame of a drag across its charts (PAN_JS, #292)}, any script or console errors, what Costs' out-of-pocket list shows before and
    after Show 200 more, Body and activity's sections with their cards (#276), the Timeline's list shown whole
    (TL_ALL_JS, #277), and Body and activity with every card on Table (H_TABLES_JS, #460: its draw and elements are
    the tab's "health, every table"). throttle: Chromium's CPU slowdown (4: a phone)."""
    from playwright.sync_api import sync_playwright
    errors, ready_ms, draw_ms, elements = [], {}, {}, {}
    with sync_playwright() as p:
        browser = p.chromium.launch()

        def signed_in():   # a page in a context of its own, signed in, with the CPU as slow as asked
            page = browser.new_page(viewport=viewport)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.goto(BASE + "/login.html")
            page.fill("#u", USER)
            page.fill("#p", PW)
            page.click("#b")
            page.wait_for_url(lambda u: "login" not in u)
            page.context.new_cdp_session(page).send("Emulation.setCPUThrottlingRate", {"rate": throttle})
            return page
        page = signed_in()

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
            if tab == "health":
                sections = page.evaluate("() => [...document.querySelectorAll('#view-health .h-sec')].map(s => [s.id, [...s.querySelectorAll('.card')].map(c => c.id)])")
            draw_ms[tab] = round(statistics.median(runs))
        # Costs lists the out-of-pocket amounts 200 at a time (the page is still on it): Show 200 more adds the next
        # 200, the first of them focused. They're in its What you paid view (#278)
        page.evaluate("() => { costState.view = 'paid'; renderCosts(); }")
        rows = "() => document.querySelectorAll('#paidTable tbody tr').length"
        more = page.locator("#paidTable > button.btn")
        paged = [page.evaluate(rows), more.inner_text()]
        more.click()
        paged += [page.evaluate(rows), page.evaluate("() => document.querySelector('#paidTable tbody tr:nth-child(201)').contains(document.activeElement)")]   # its button (#202)
        tl_all = page.evaluate(TL_ALL_JS)
        runs = []
        for n in range(RUNS):   # Body and activity again, every card on Table (#460)
            page.goto(f"{BASE}/index.html?profile=1&tables={n}#overview")
            ready()
            page.evaluate("ks => { for (const k of ks) state.tables.add(k); }", H_TABLES)
            ms, elements["health, every table"] = page.evaluate(DRAW_JS, "health")
            runs.append(ms)
        draw_ms["health, every table"] = round(statistics.median(runs))
        tables = page.evaluate(H_TABLES_JS)
        pan_ms = {}
        page.close()   # the drags in a page of their own: this one has been through every tab, and kept their memory
        page = signed_in()
        page.goto(f"{BASE}/index.html?profile=1&pan=1#overview")
        ready()
        page.evaluate("() => applyPreset('all')")
        for tab in PAN_TABS:
            pan_ms[tab] = statistics.median(page.evaluate(PAN_JS, tab) for _ in range(RUNS))
        browser.close()
    return ready_ms, draw_ms, elements, errors, paged, sections, tl_all, tables, pan_ms


class PageBudgets(unittest.TestCase):
    def check(self, prefix, by_tab):
        over = {}
        for name, values in by_tab.items():
            measure(prefix + name + "_by_tab", values)
            measure(prefix + name, max(values.values()))
            over.update({f"{prefix + name} on {t}": f"{v:,} > {BUDGETS[prefix + name]:,}" for t, v in values.items() if v > BUDGETS[prefix + name]})
        self.assertEqual(over, {}, "over budget (docs/performance.md)")

    def check_timeline(self, tl_all, prefix):
        """ten years on the Timeline (#277): the list draws a page at first and, with Show more, every event it lists,
        the first new row focused each time; each press is within the tab draw budget (check, with the tabs)"""
        measure(prefix + "timeline_list", tl_all)
        self.assertTrue(tl_all["presses"] > 0 and tl_all["first"] < tl_all["want"], tl_all)
        self.assertEqual([tl_all["rows"], tl_all["focused"]], [tl_all["want"], True], tl_all)

    def test_desktop(self):
        ready_ms, draw_ms, elements, errors, paged, sections, tl_all, tables, pan_ms = page_times({"width": 1400, "height": 900})
        self.assertEqual(errors, [])
        self.assertEqual(paged, [200, "Show 200 more", 400, True])
        self.assertEqual(sections, SECTIONS, "Body and activity's sections (#276)")
        self.assertTrue(tables["all"] and tables["more"] > 0, f"Body and activity, every card on Table, paged (#460): {tables}")
        self.check_timeline(tl_all, "")
        self.check("", {"page_ready_ms": ready_ms, "tab_draw_ms": {**draw_ms, "timeline, Show more": tl_all["ms"]}, "tab_elements": elements, "pan_frame_ms": pan_ms})

    def test_phone(self):   # 375 px wide, with the CPU four times slower
        ready_ms, draw_ms, _, errors, _, _, tl_all, _, pan_ms = page_times({"width": 375, "height": 800}, throttle=4)
        self.assertEqual(errors, [])
        self.check_timeline(tl_all, "phone_")
        self.check("phone_", {"page_ready_ms": ready_ms, "tab_draw_ms": {**draw_ms, "timeline, Show more": tl_all["ms"]}, "pan_frame_ms": pan_ms})

    def test_event_track(self):
        """Labs' event track over the charts (#294) on the ten years, with the period "All", procedures and imaging and the
        medications on: at 1400, 375 and 320 px every event on it once and in no chart, named or counted under a "+N",
        no name on another, past the plot or with a leader through it (browser_check.py's TRACK_JS, read from that file:
        it checks the demo the same way)"""
        import ast
        from playwright.sync_api import sync_playwright
        with open(os.path.join(ROOT, "tests", "browser_check.py"), encoding="utf-8") as f:
            track_js = next(n.value.value for n in ast.parse(f.read()).body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "TRACK_JS")
        got, errors = {}, []
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(BASE + "/login.html")
            page.fill("#u", USER)
            page.fill("#p", PW)
            page.click("#b")
            page.wait_for_url(lambda u: "login" not in u)
            page.goto(f"{BASE}/index.html?profile=1#labs")
            page.wait_for_function(READY_JS, timeout=60000)
            page.evaluate("() => { applyPreset('all'); state.marks = true; medsOnTrends = true; renderAll(); }")
            for width in (1400, 375, 320):
                page.set_viewport_size({"width": width, "height": 900 if width > 400 else 800})
                page.evaluate("() => renderCharts()")
                page.wait_for_timeout(500)
                got[width] = page.evaluate(track_js)
            browser.close()
        measure("event_track", {w: {k: v for k, v in t.items() if k != "problems"} for w, t in got.items()})
        self.assertEqual(errors, [])
        for width, t in got.items():
            self.assertTrue(t["items"] > 0 and t["layers"] > 0 and t["inCharts"] == 0 and t["rows"] <= 3 and not t["problems"], (width, t))

    def test_share_page(self):
        """a share link with every section the big profile offers, of any date (#491): at 1400 px its page is within
        share_page_px, each long table (2,000 claims, 1,000 readings a home measure) showing its latest 20 and saying
        so, and the view sending only those (#498); Costs' Show all asks for every claim and lists them, the first
        one added focused"""
        from playwright.sync_api import sync_playwright
        offered = [s["id"] for s in CLIENT.get("/api/profiles/1/links", headers=H).json()["sections"]]
        r = CLIENT.post("/api/profiles/1/links", headers=H, json={"label": "Every section (performance)", "sections": offered, "hours": 1})
        self.assertEqual(r.status_code, 200, r.text)
        errors = []
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.on("pageerror", lambda e: errors.append(str(e)))
            with page.expect_response(lambda x: x.url.endswith("/api/share/view")) as first:
                page.goto(f"{BASE}/{r.json()['path']}")
            sent = first.value.json()   # #498: the view sends a long section's latest rows, with how many it has
            page.wait_for_selector("#share-costs .share-more", timeout=60000)
            page.wait_for_timeout(300)
            height = measure("share_page_px", page.evaluate("document.documentElement.scrollHeight"))
            long = page.evaluate("""() => [...document.querySelectorAll('#shareBody .share-more')].map(m => [m.closest('section').id,
              m.closest('section').querySelectorAll('tbody tr').length, m.querySelector('.hint').textContent, m.querySelector('button').textContent])""")
            page.locator("#share-costs .share-more button").click()   # asks the server for the rest (#498)
            page.wait_for_function("() => document.querySelectorAll('#share-costs tbody tr').length > 20", timeout=60000)
            costs = page.evaluate("""() => { const rows = [...document.querySelectorAll('#share-costs tbody tr')];
              return [rows.length, rows.indexOf(document.activeElement), !!document.querySelector('#share-costs .share-more')]; }""")
            browser.close()
        measure("share_page_sections", offered)
        self.assertEqual(errors, [])
        self.assertTrue({"costs", "home_bp", "glucose", "oxygen"} <= set(offered), offered)
        self.assertTrue(["share-costs", 20, "The latest 20 of 2,000 claims.", "Show all 2,000"] in long
                        and all(n == 20 and t.startswith("The latest 20 of ") for _, n, t, _ in long)
                        and {"share-costs", "share-home_bp", "share-glucose", "share-oxygen"} <= {s for s, *_ in long}, long)
        self.assertEqual(costs, [2000, 20, False])
        rows = {k: len(v["rows"] if isinstance(v, dict) else v) for k, v in sent.items()
                if k in ("visits", "procedures", "costs", "home_bp", "glucose", "oxygen", "temperature")}
        self.assertTrue(sent["cut"].get("costs") == 2000 and {"home_bp", "glucose", "oxygen"} <= set(sent["cut"])
                        and all(n <= 40 and (n == 20) == (k in sent["cut"]) for k, n in rows.items()), [sent["cut"], rows])
        self.assertLessEqual(height, BUDGETS["share_page_px"], "over budget (docs/performance.md)")

    def test_share_printed(self):
        """a year's link to visits, lab results and medications, and ten years of lab results (#509), each printed with
        every history open: no page before the last more than a quarter empty (#505 left the year's page 4 608 pt short,
        a history's last rows alone on it), and Latest results' column names above its first row on each page with its
        rows, never on a page holding only a history (#505: most of the ten years' 173 pages had them over the
        history's own). browser_check.py checks the breaks on the small demo (#501)"""
        import io
        from playwright.sync_api import sync_playwright
        from pypdf import PdfReader
        year = (date.fromisoformat(END) - timedelta(days=365)).isoformat()
        made = [CLIENT.post("/api/profiles/1/links", headers=H, json={"label": label, "sections": sections, "from": since, "hours": 1})
                for label, sections, since in (("A year, printed (performance)", ["visits", "results", "medications"], year),
                                               ("Ten years of results, printed (performance)", ["results"], "2016-11-15"))]
        self.assertEqual([r.status_code for r in made], [200, 200], [r.text for r in made])
        printed = {}
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            for r in made:
                page.goto("about:blank")
                page.goto(f"{BASE}/{r.json()['path']}")
                page.wait_for_selector("#share-results .share-latest", timeout=60000)
                page.locator("#share-results .share-tools button", has_text="Open every history").click()
                head = " ".join(page.evaluate("[...document.querySelectorAll('#share-results .share-latest>thead th')].map(c => c.textContent)"))
                tests = page.evaluate("[...document.querySelectorAll('#share-results .share-open')].map(b => b.textContent)")
                page.emulate_media(media="print")
                pdf = page.pdf(format="Letter")
                page.emulate_media(media="screen")
                back = page.evaluate("[!document.querySelector('.share-split'), document.querySelectorAll('#share-results .share-latest').length]")
                pages = []   # each page's lowest text, in points up from its foot (a Letter page is 792 tall), the first head and row
                for p_ in PdfReader(io.BytesIO(pdf)).pages:
                    ys = []
                    lines = [ln.strip() for ln in p_.extract_text(visitor_text=lambda t, cm, tm, fd, fs: ys.append(tm[5] * cm[3] + cm[5]) if t.strip() else None).splitlines()]
                    rows = [i for i, ln in enumerate(lines) if any(ln.startswith(t) for t in tests) and not ln.endswith(": every result")]
                    pages.append([round(min(ys)) if ys else 792, [i for i, ln in enumerate(lines) if ln == head][:1], rows[:1]])
                printed[r.json()["link"]["label"]] = [head, back, pages]
            browser.close()
        measure("share_printed_pages", {k: len(v[2]) for k, v in printed.items()})
        for label, (head, back, pages) in printed.items():
            self.assertEqual([head, back], ["Test Latest Range Flag Trend Results", [True, 1]], f"{label}: the histories put back after printing")
            self.assertTrue(len(pages) > 3 and max(p[0] for p in pages[:-1]) <= 792 / 4, [label, pages])
            self.assertTrue(all(bool(h) == bool(rs) and (not rs or h[0] < rs[0]) for _, h, rs in pages), [label, pages])
        self.assertGreater(sum(1 for _, h, rs in printed["Ten years of results, printed (performance)"][2] if not rs), 50, "pages holding only a history")

if __name__ == "__main__":
    unittest.main(verbosity=2)
