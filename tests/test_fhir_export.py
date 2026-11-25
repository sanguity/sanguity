#!/usr/bin/env python3
"""Export as FHIR R4 (#205, server/fhir_export.py): the bundle re-imports through Sanguity's own FHIR importer with the
same records, over the demo profile as the browser check builds it (its labs and vitals with their own ranges, visits,
a stay, conditions and allergies, medications and fills, immunizations, procedures and studies, documents with their
files, letters, messages, claims, home readings from the phone and typed in, glucose from a meter and a CGM, ECGs with
their waveforms) and over every FHIR importer fixture; it validates against HL7's FHIR R4 JSON schema (the subset in
tests/fixtures/fhir-r4, tests/fhir_schema_check.py); its ids are stable; About this person goes on the Patient (the sex
for reference ranges never as Patient.gender); cycle records only when ticked; only the parts ticked; the route's roles,
activity log and streaming. Fictional data only.

  python3 tests/test_fhir_export.py
"""
import io, json, logging, os, re, shutil, subprocess, sys, tempfile, time, unittest, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-fhir-export-test-")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"),
                  SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"), SANGUITY_WATCH_KEYS=os.path.join(TMP, "watch-keys", "receiver-secrets.json"),
                  PASSWORD_MIN_LENGTH="15")
os.makedirs(os.path.join(TMP, "watch-keys"))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import browser_check as bc   # noqa: E402  (its builders of the demo's extra records; nothing of the browser is started)
import fhir_schema_check   # noqa: E402
from fastapi.testclient import TestClient   # noqa: E402

from server import (accounts, atrest, cycle_tracking, db as dbm, export, fhir_export, importers, journal, main, profiles,   # noqa: E402
                    records, settings, watch_store)

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites", "lee": "maple tunnel brisk saddle"}
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "importers", "fhir")
TYPED = [["2026-11-02T08:15:00-05:00", 131, 84, 71, None, "Typed in"]]   # home readings typed in (#224)
TYPED_GLUCOSE = [["2026-11-02T08:20:00-05:00", 104.0, None, "Typed in", "fasting"]]
TYPED_SPO2 = [["2026-11-02T08:25:00-05:00", 97.0, None, "Typed in"]]


def build_demo():
    """The demo as tests/browser_check.py builds it, in this test's data folder."""
    bc.TMP = TMP
    build, watch = os.path.join(TMP, "build"), os.path.join(TMP, "watch")
    subprocess.run([sys.executable, "-I", os.path.join(ROOT, "demo", "generate.py"), build, "--end", "2026-11-15", "--watch", watch],
                   check=True, stdout=subprocess.DEVNULL)
    bc.add_html_record(build)
    bc.add_med_records(build)
    bc.add_range_records(build)
    bc.add_glucose_watch(watch)
    bc.add_immunization_records(build)
    bc.add_vitals_records(build, watch)
    bc.add_fitness_records(build, watch)
    bc.add_workout_hr(watch)


def unzip(blob, into):
    """The export unpacked as an upload is, and the importer's files dict for it."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        z.extractall(into)
    return importers.files_in(into)


def t19(s):
    """A time compared across the round trip: its wall-clock time (the export gives a time without an offset its
    zone's, which FHIR requires); a date as a date."""
    s = str(s or "")
    return s[:10] if len(s) <= 10 else s[:19]


def words(s):
    """Text compared loosely: the words, whatever the markup and spacing."""
    return " ".join(re.sub(r"<[^>]+>", " ", str(s or "")).split())


MED = {"discontinued": "stopped", "complete": "completed", "inactive": "completed"}


def project(data, watch=None):
    """What a record is, kind by kind, in a form both sides of the round trip give the same way: the export's
    mapping is in docs/fhir-export.md, and these are its exact parts (what FHIR has no place for is left out here,
    and tested on its own: a condition's source, a claim's care category, a stay's sub line)."""
    d, out = data.get("details") or {}, {}
    det = lambda e: d.get(str(e["id"])) or d.get(e["id"]) or {}   # an importer keys them by number, the records by string
    add = lambda k, v: out.setdefault(k, []).append(v)
    costs = {c.get("id"): c for c in data.get("costs") or []}
    for e in data.get("events") or []:
        k, x = e["k"], det(e)
        if e.get("hidden") and k != "labitem":
            continue
        if k == "labitem" or (k == "lab" and x.get("type") == "result"):
            add("labitem", (t19(e["t"]), x.get("order"), x.get("provider") or "", x.get("lab") or "",
                            tuple((r[0], str(r[1]), r[2] or "", r[3] or "", r[4] or "") for r in x.get("rows") or [])))
        elif k == "lab":
            add("labday", (t19(e["t"])[:10], e["title"]))
        elif k in ("visit", "contact"):   # chart activity (#236) goes out as an Encounter and comes back as a visit
            add("visit", (t19(e["t"]), e["title"], x.get("who") or "", x.get("where") or "", x.get("dx") or "", x.get("note") or "",
                    tuple(x.get("procs") or [])))
        elif k == "stay":
            add(k, (t19(e["t"]), t19(e.get("end")), e["title"]))
        elif k in ("imaging", "study", "procedure"):
            add(k, (t19(e["t"]), e["title"], words(" ".join(str(p[1]) for p in x.get("report") or []))))
        elif k == "immunization":
            add(k, (t19(e["t"]), *(x.get(f) for f in ("vaccine", "code", "system", "dose", "amount", "route", "site", "lot",
                                                      "manufacturer", "performer", "where"))))
        elif k == "med":
            st = str(x.get("status") or "").lower()
            add(k, (t19(e["t"]), e["title"], x.get("instructions") or "", x.get("prescriber"), x.get("rxnorm"),
                    str(x.get("end") or "")[:10], MED.get(st, st)))
        elif k in ("claim", "rx") and (x.get("type") == "claim" or e["id"] in costs):
            c = costs.get(e["id"]) or {}
            add("claim", (t19(e["t"])[:10], e["title"], float(c.get("billed", x.get("billed")) or 0), float(c.get("paid", x.get("paid")) or 0),
                          float(c.get("owe", x.get("owe")) or 0), c.get("ins") or x.get("insurer"), str(x.get("kind") or c.get("kind")).lower()))
        elif k == "rx":
            supply = x.get("daysSupply") or (int(m[1]) if (m := re.search(r"(\d+)-day supply", e.get("sub") or "")) else None)
            add(k, (t19(e["t"]), e["title"], supply, x.get("quantity"), x.get("prescriber")))
        elif k in ("document", "letter") or x.get("type") == "file":
            add("document", (t19(e["t"]), k, e["title"]))
        elif x.get("type") == "conversation":
            add("message", (t19(e["t"]), k, e["title"], tuple((m.get("from"), t19(m.get("t")), m.get("body")) for m in x.get("messages") or [])))
        else:
            add("other", (k, e["title"]))
    for s in data.get("labs") or []:
        for p in s.get("pts") or []:
            add("point", (s["name"], t19(p[0]), p[1], p[2] if len(p) > 2 else None, p[3] if len(p) > 3 else None, p[4] if len(p) > 4 else "",
                          str(p[5]) if len(p) > 5 else str(p[1])))   # the value as reported, its precision kept ("2.0")
    for p in data.get("problems") or []:
        add("problem", ("Allergy" if p.get("type") == "Allergy" else "Diagnosis" if p.get("type") != "Health concern" else p["type"],
                        p["name"], p.get("date"), p.get("status"), p.get("severity"), p.get("criticality")))
    for u in data.get("upcoming") or []:
        add("upcoming", (t19(u["t"])[:10], u["title"], u.get("who") or "", u.get("where") or "", u.get("dx") or ""))
    for w in data.get("clinicWeights") or []:
        add("weight", (t19(w[0]), w[1], w[2] if len(w) > 2 and w[2] not in ("FHIR", "Typed in") else None))
    for m in (watch or {}).get("metrics") or []:
        add("measure", (m["key"], m["name"], m["unit"], m["group"], tuple(map(tuple, m["pts"]))))
    for k in watch_store.READINGS:
        for r in (watch or {}).get(k) or []:
            add(k, tuple(r))
    for e in (watch or {}).get("ecg") or []:
        add("ecg", tuple(e.get(f) for f in ("key", "type", "start", "end", "classification", "avg_hr", "device", "via", "samples", "rate")))
    return {k: sorted(v, key=repr) for k, v in out.items()}


class FhirExport(unittest.TestCase):
    maxDiff = None
    @classmethod
    def setUpClass(cls):
        build_demo()
        dbm.init()
        db = dbm.connect()
        atrest.check_key(db)
        atrest.load_pepper(db)
        bc.add_cycle_watch(os.path.join(TMP, "watch"))   # #183, sealed with the key just made
        profiles.bootstrap(db)
        bc.add_ecg_watch(db, os.path.join(TMP, "watch"))   # #182
        db.close()
        cls.ctx = TestClient(main.app, base_url="http://testserver").__enter__()
        db = dbm.connect()
        cls.uids = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        for who, role in (("robin", "owner"), ("lee", "editor"), ("kim", "viewer")):
            db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, 1, ?)", (cls.uids[who], role))
        with dbm.transaction(db):
            for key, value in (("person.birthDate", "1979-06-02"), ("person.sex", "female"),
                               ("person.heights", [{"date": "2025-01-14", "cm": 168.2, "unit": "cm"}, {"date": "2026-08-12", "cm": 167.6, "unit": "ftin"}])):
                settings.put(db, key, value, "profile", 1)
            watch_store.save_import(db, 1, {"metrics": [], "exercise": [], "bp_readings": TYPED, "glucose_readings": TYPED_GLUCOSE,
                                            "spo2_readings": TYPED_SPO2})
            journal.add(db, 1, {"id": cls.uids["robin"], "username": "robin"}, {"kind": "note", "t": "2026-11-03", "title": None,
                                                                                 "note": "A fictional journal note that stays out of FHIR"})
            cycle_tracking.turn(db, 1, True, cls.uids["robin"])
        cls.db = db

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.ctx.__exit__(None, None, None)
        shutil.rmtree(TMP, True)

    def export(self, pid=1, parts=None, cycle=False):
        p = fhir_export.plan(self.db, pid, parts or set(fhir_export.PART_KEYS), cycle=cycle)
        return b"".join(fhir_export.stream(p, "robin", zone="America/New_York"))

    def bundle(self, blob):
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            return json.loads(z.read("bundle.json")), z.namelist(), z.read("README.txt").decode()

    def round_trip(self, pid, blob):
        """(the profile's records and watch summary, what the FHIR importer reads from the export of them)"""
        data, watch = records.assemble(self.db, pid), watch_store.load(self.db, pid)
        files = unzip(blob, tempfile.mkdtemp(dir=TMP))
        got = importers.run(files)
        self.assertEqual(got["importer"], "fhir", "the registry picks the FHIR importer for the export")
        return data, watch, got

    def imported(self, c, pid, files):
        """An upload of {path: bytes} into profile pid through the API, imported as the import page does it: the
        background job, its preview saved. Returns the job."""
        up = c.post(f"/api/profiles/{pid}/uploads", headers=H).json()["id"]
        for path, data in files.items():
            r = c.put(f"/api/profiles/{pid}/uploads/{up}/files", params={"path": path}, content=data,
                      headers={**H, "Content-Type": "application/octet-stream"})
            self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(c.post(f"/api/profiles/{pid}/uploads/{up}/finish", headers=H).status_code, 200)
        jid = c.post(f"/api/profiles/{pid}/uploads/{up}/import", headers=H).json()["id"]
        end = time.time() + 120
        while time.time() < end:
            j = c.get(f"/api/profiles/{pid}/jobs/{jid}").json()
            if j["state"] == "preview":
                self.assertEqual(c.post(f"/api/profiles/{pid}/jobs/{jid}/confirm", headers=H).status_code, 202)
            elif j["state"] in ("done", "failed"):
                self.assertEqual(j["state"], "done", j)
                return j
            time.sleep(0.05)
        self.fail(f"job {jid} didn't finish")

    def compare(self, a, b, heights=()):
        """Profile a's records against profile b's, which were loaded from a's FHIR export: the same, kind by kind.
        heights: About this person's heights, which come back as a body height series (what a clinic's would be,
        offered on About this person to add to the history: an import never changes a setting)."""
        db = self.db
        want = project(records.assemble(db, a), watch_store.load(db, a))
        # a CGM day's readings counted by whole mg/dL (#234's glucose_dist) and by hour (#303's glucose_hourly) aren't
        # exported: the daily summaries are (docs/fhir-export.md), so the day's time in a person's own range and its
        # place in the 24-hour profile aren't known after loading it back
        if want.get("measure"):
            want["measure"] = [m for m in want["measure"] if m[0] not in ("glucose_dist", "glucose_hourly")] or None
            if not want["measure"]:
                del want["measure"]
        have = project(records.assemble(db, b), watch_store.load(db, b))
        came = [p for p in have.get("point", []) if p[0] == "Body height"]
        have["point"] = [p for p in have.get("point", []) if p[0] != "Body height"] or None
        if not have["point"]:
            del have["point"]
        self.assertEqual(sorted((p[1][:10], p[2]) for p in came), sorted((h["date"], h["cm"]) for h in heights))
        for kind in sorted(set(want) | set(have)):
            with self.subTest(kind=kind):
                self.assertEqual(want.get(kind), have.get(kind))
        return want

    # ---------------------------------------------------------------- the round trip
    def test_demo_round_trip(self):
        """The demo exported from the Sharing page's route and uploaded into an empty profile: the same records."""
        robin = self.login("robin")
        blob = robin.get("/api/profiles/1/export?format=fhir").content
        pid = profiles.create_profile(self.db, "Round trip", owner_id=self.uids["robin"])
        job = self.imported(robin, pid, {"sanguity-fhir.zip": blob})
        self.assertEqual(job["importer"], "fhir")
        heights = settings.stored(self.db, "profile", 1)["person.heights"]
        want = self.compare(1, pid, heights)
        for kind in ("labitem", "labday", "point", "visit", "stay", "claim", "rx", "med", "immunization", "document", "message",
                     "problem", "upcoming", "weight", "bp_readings", "temp_readings", "glucose_readings", "spo2_readings", "measure",
                     "ecg", "imaging", "study", "procedure"):
            self.assertTrue(want.get(kind), f"the demo has {kind} records to compare")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM documents WHERE profile_id=?", (pid,)).fetchone()[0], 4,
                         "the documents and the letters, stored as the profile's")
        c = robin.get(f"/api/profiles/{pid}/watch/ecg/682f8a18a654dde3")
        self.assertEqual(c.status_code, 200, c.text)
        self.assertEqual(len(c.json()["samples"]), 15360, "an ECG's waveform came back with every sample")

    def test_importer_reads_everything_it_was_given(self):
        """The importer on its own, over the demo's export: nothing it skips, and no cycle records unless ticked."""
        data, watch, got = self.round_trip(1, self.export())
        self.assertFalse([w for w in got["warnings"] if "Skipped" in w or "cycle" in w], got["warnings"])
        self.assertEqual(len(got["data"]["watch"]["ecg_waves"]), 3)
        self.assertEqual(sum(len(project(data, watch).get(k, [])) for k in ("labitem", "visit", "claim")),
                         sum(len(project(got["data"], got["data"]["watch"]).get(k, [])) for k in ("labitem", "visit", "claim")))

    def test_documents_come_back_with_their_files(self):
        blob = self.export()
        files = unzip(blob, tempfile.mkdtemp(dir=TMP))
        got = importers.run(files)["data"]
        urls = sorted(v["url"] for v in got["details"].values() if v.get("type") == "file")
        self.assertEqual(len(urls), 4, urls)
        self.assertTrue(all(u and u.split("#")[0] in files for u in urls), urls)
        _, names, _ = self.bundle(blob)
        self.assertTrue(any(n.endswith("visit summary_files/style.css") for n in names), "an HTML record's stylesheet beside it")
        letter = next(v for k, v in got["details"].items() if v.get("ext") == "html" and "letters/" in v["url"])
        with open(files[letter["url"]], encoding="utf-8") as f:
            self.assertIn("Six cycles", f.read())

    def test_fixtures_round_trip(self):
        """Every FHIR importer fixture: uploaded into a profile of its own, exported, uploaded into another: the same."""
        robin = self.login("robin")
        for name in sorted(os.listdir(FIXTURES)):
            folder = os.path.join(FIXTURES, name)
            if not os.path.isdir(folder):
                continue
            with self.subTest(fixture=name):
                a = profiles.create_profile(self.db, f"Fixture {name}", owner_id=self.uids["robin"])
                b = profiles.create_profile(self.db, f"Fixture {name} again", owner_id=self.uids["robin"])
                files = {}
                for rel, path in importers.files_in(folder).items():
                    with open(path, "rb") as f:
                        files[rel] = f.read()
                self.imported(robin, a, files)
                blob = robin.get(f"/api/profiles/{a}/export?format=fhir").content
                self.assertEqual(fhir_schema_check.errors(self.bundle(blob)[0]), [])
                self.assertEqual(self.imported(robin, b, {"fhir.zip": blob})["importer"], "fhir")
                self.compare(a, b)

    def test_a_flag_that_says_no_way_comes_back(self):
        """#817: a result its report flagged abnormal with no way the range places ("A": inside its range, with none
        printed, or a text result; the importers keep it since #752 and #817) goes out as interpretation "A" and comes
        back "A", on the point and the row: the FHIR importer placed "A" by the range alone, so it came back with none."""
        t = "2026-10-01T08:00:00"
        data = {"built": "2026-10-02", "source": "fictional", "costs": [], "upcoming": [],
                "events": [{"id": 1, "t": t, "k": "lab", "title": "Lab results (fictional)"},
                           {"id": 2, "t": t, "k": "labitem", "title": "Flags panel (fictional)", "hidden": 1, "abn": 3}],
                "details": {"1": {"type": "labday", "ids": [2]},
                            "2": {"type": "result", "order": "Flags panel (fictional)",
                                  "rows": [["RDW", "13.1", "%", "11.0 - 15.0", "A"], ["Fibrinogen", "300", "mg/dL", "", "A"],
                                           ["Urine culture", "Positive", "", "", "A"]]}},
                "labs": [{"name": "RDW", "unit": "%", "panel": "", "n": 1, "txt": [], "pts": [[t, 13.1, 11.0, 15.0, "A", "13.1"]]},
                         {"name": "Fibrinogen", "unit": "mg/dL", "panel": "", "n": 1, "txt": [], "pts": [[t, 300, None, None, "A", "300"]]},
                         {"name": "MPV", "unit": "fL", "panel": "", "n": 1, "txt": [],   # no report holds it: from its series
                          "pts": [["2026-10-05T08:00:00", 9.9, 7.5, 12.5, "A", "9.9"]]}]}
        pid = profiles.create_profile(self.db, "Flags (fictional)", owner_id=self.uids["robin"])
        with dbm.transaction(self.db):
            records.store(self.db, pid, data, "upload", fmt="csv")
        blob = self.export(pid, {"labs"})
        said = {e["resource"]["code"]["text"]: [c["code"] for i in e["resource"].get("interpretation") or [] for c in i["coding"]]
                for e in self.bundle(blob)[0]["entry"] if e["resource"]["resourceType"] == "Observation"}
        self.assertEqual(said, {"RDW": ["A"], "Fibrinogen": ["A"], "Urine culture": ["A"], "MPV": ["A"]})
        have, _, got = self.round_trip(pid, blob)
        want, back = project(have), project(got["data"])
        self.assertEqual([p[5] for p in back.get("point", [])], ["A", "A", "A"], back.get("point"))
        self.assertEqual(back.get("point"), want.get("point"))
        self.assertEqual({r[0]: r[4] for x in back.get("labitem", []) for r in x[4]},
                         {"RDW": "A", "Fibrinogen": "A", "Urine culture": "A", "MPV": "A"}, "each row (MPV's a result of its own)")
        profiles.delete_profile(self.db, pid)

    def test_each_printed_flag_goes_out_and_comes_back(self):
        """#835: a report row's flag as printed (a PDF's rows keep it: "High", "Critical High", "Abnormal", "AA",
        "Critical", "Alert", "Yes"...) goes out as its HL7 interpretation code by the one rule (common.flag_code: "H",
        "HH", "A", "AA"...), and comes back as the same flag on the point and the row (common.report_flag): only "H",
        "L", "HH", "LL" and "A" were written, so an in-range "Abnormal" was lost on export."""
        from server.importers import common
        t = "2026-10-01T08:00:00"
        printed = ["H", "HH", "High", "Critical High", "L", "Low", "Panic Low", "A", "AA", "Abnormal", "Critical", "Alert", "Yes", "N", ""]
        cases = [(p, 13, 11, 15) for p in printed] + [("Abnormal", 16, 11, 15), ("Critical", 10, 11, 15), ("Critical", 13, None, None)]
        name = lambda i: f"Flag {i} (fictional)"
        data = {"built": "2026-10-02", "source": "fictional", "costs": [], "upcoming": [],
                "events": [{"id": 1, "t": t, "k": "lab", "title": "Lab results (fictional)"},
                           {"id": 2, "t": t, "k": "labitem", "title": "Flags panel (fictional)", "hidden": 1}],
                "details": {"1": {"type": "labday", "ids": [2]},
                            "2": {"type": "result", "order": "Flags panel (fictional)",
                                  "rows": [[name(i), str(v), "U/L", f"{lo} - {hi}" if lo is not None else "", p] for i, (p, v, lo, hi) in enumerate(cases)]}},
                "labs": [{"name": name(i), "unit": "U/L", "panel": "", "n": 1, "txt": [],
                          "pts": [[t, v, lo, hi, common.report_flag(p, v, lo, hi), str(v)]]} for i, (p, v, lo, hi) in enumerate(cases)]}
        pid = profiles.create_profile(self.db, "Printed flags (fictional)", owner_id=self.uids["robin"])
        with dbm.transaction(self.db):
            records.store(self.db, pid, data, "upload", fmt="csv")
        blob = self.export(pid, {"labs"})
        said = {e["resource"]["code"]["text"]: [c["code"] for i in e["resource"].get("interpretation") or [] for c in i["coding"]]
                for e in self.bundle(blob)[0]["entry"] if e["resource"]["resourceType"] == "Observation"}
        codes = ["H", "HH", "H", "HH", "L", "L", "LL", "A", "AA", "A", "AA", "A", "A", "", "", "A", "AA", "AA"]
        self.assertEqual([said.get(name(i)) for i in range(len(cases))], [[c] if c else [] for c in codes])
        _, _, got = self.round_trip(pid, blob)
        back = {s["name"]: s["pts"][0][4] for s in got["data"]["labs"]}
        rows = {r[0]: r[4] for e in got["data"]["details"].values() if isinstance(e, dict) and e.get("type") == "result" for r in e["rows"]}
        want = [common.report_flag(p, v, lo, hi) for p, v, lo, hi in cases]
        self.assertEqual([back.get(name(i)) for i in range(len(cases))], want, "each point's flag back")
        self.assertEqual([rows.get(name(i)) for i in range(len(cases))], want, "each row's flag back, by the one rule")
        profiles.delete_profile(self.db, pid)

    def test_an_organizers_results_at_their_own_times_go_out_once(self):
        """#876: a C-CDA organizer's results each at their own time (08:30, 08:45 and 09:10, the organizer's lab event at
        the first): the export found a report's rows' results only at its event's minute, so the two later went out
        twice, as the report's row at 08:30 (no range, the row's text alone) and as their result at its own time, and
        came back as two results each. Now a row's result is found on its event's day too, in the same import, and goes
        out once, at its own time, in its report; read back, the same results, rows and lab day."""
        from server.importers import ccda
        import ranges_matrix as M
        rows = [("ALT", "U/L", "2026-10-01T08:30:00", "41", "7-35"), ("AST", "U/L", "2026-10-01T08:45:00", "30", "8-33"),
                ("Glucose", "mg/dL", "2026-10-01T09:10:00", "99", "70-99"), ("Albumin", "g/dL", "2026-10-01T09:10:00", "4.4", "3.5-5.0")]
        d = tempfile.mkdtemp(dir=TMP)
        with open(os.path.join(d, "organizer.xml"), "w", encoding="utf-8") as f:
            f.write(M.ccda(rows))
        data = ccda.parse(importers.files_in(d))["data"]
        self.assertEqual([(e["k"], e["t"]) for e in data["events"] if e["k"] == "labitem"], [("labitem", "2026-10-01T08:30:00")])
        pid = profiles.create_profile(self.db, "Organizer (fictional)", owner_id=self.uids["robin"])
        with dbm.transaction(self.db):
            records.store(self.db, pid, data, "upload", fmt="ccda")
        blob = self.export(pid, {"labs"})
        obs = [e["resource"] for e in self.bundle(blob)[0]["entry"] if e["resource"]["resourceType"] == "Observation"]
        said = sorted((o["code"]["text"], o["effectiveDateTime"][:19]) for o in obs)
        self.assertEqual(said, [("ALT", "2026-10-01T08:30:00"), ("AST", "2026-10-01T08:45:00"), ("Albumin", "2026-10-01T09:10:00"),
                                ("Glucose", "2026-10-01T09:10:00")], "each result once, at its own time")
        report = next(e["resource"] for e in self.bundle(blob)[0]["entry"] if e["resource"]["resourceType"] == "DiagnosticReport")
        self.assertEqual(len(report["result"]), 4, "each in its report")
        have, _, got = self.round_trip(pid, blob)
        want, back = project(have), project(got["data"])
        for kind in ("point", "labitem", "labday"):
            with self.subTest(kind=kind):
                self.assertEqual(back.get(kind), want.get(kind))
        profiles.delete_profile(self.db, pid)

    # ---------------------------------------------------------------- the corrections made in Sanguity
    def test_the_records_as_the_app_shows_them(self):
        """#916: the bundle holds the records with every correction made in Sanguity applied, as the page shows them: a
        hidden visit is left out, a merged one is one Encounter (with what the other added), a renamed one has its new
        title, a re-dated one its new time, a stay marked "Not a hospital stay" is an ambulatory visit, a visit moved to
        Procedures a Procedure, and a result two sources gave, linked as the same result, one Observation. It was built
        from the records as imported (export.plan's, raw since #589, for Export everything's data.json, which carries
        the corrections beside them and still does), so all of them went out as they were imported."""
        d = lambda day, hm="09:00": f"2026-{day}T{hm}:00"
        file = {"built": "2026-05-01", "source": "fictional file", "costs": [],
                "events": [{"id": 1, "t": d("03-02"), "k": "visit", "title": "Annual physical (fictional)", "sub": "", "key": "f:v1"},
                           {"id": 2, "t": d("03-10"), "k": "visit", "title": "Follow-up (fictional)", "sub": "", "key": "f:v2"},
                           {"id": 3, "t": d("03-10", "09:05"), "k": "visit", "title": "Clinic visit (fictional)", "sub": "", "key": "f:v3"},
                           {"id": 4, "t": d("03-20", "10:00"), "k": "visit", "title": "Telehealth (fictional)", "sub": "", "key": "f:v4"},
                           {"id": 5, "t": d("04-01", "10:00"), "k": "visit", "title": "Dated wrong (fictional)", "sub": "", "key": "f:v5"},
                           {"id": 6, "t": d("04-05", "08:00"), "end": d("04-05", "18:00"), "k": "stay",
                            "title": "Day unit · Fictional Hospital", "sub": "Observation", "key": "f:s1"},
                           {"id": 7, "t": d("04-09", "11:00"), "k": "visit", "title": "Skin biopsy (fictional)", "sub": "", "key": "f:v7"}],
                "details": {"2": {"type": "visit", "note": "Fictional follow-up"}, "3": {"type": "visit", "who": "Dr Example (fictional)"},
                            "6": {"type": "stay", "admit": d("04-05", "08:00"), "discharge": d("04-05", "18:00")}},
                "labs": [{"name": "Hemoglobin", "unit": "g/dL", "loinc": "718-7", "key": "f:hgb", "panel": "CBC",
                          "pts": [[d("03-02"), 13.5, 12, 16, "", "13.5"]]}]}
        pull = {"events": [], "details": {}, "costs": [],   # the same result from a portal, in other units
                "labs": [{"name": "Hemoglobin", "unit": "g/L", "loinc": "718-7", "key": "p:hgb", "panel": "CBC",
                          "pts": [[d("03-02"), 135, 120, 160, "", "135"]]}]}
        pid = profiles.create_profile(self.db, "Corrected (fictional)", owner_id=self.uids["robin"])
        with dbm.transaction(self.db):
            records.store(self.db, pid, file, "dataset")
            records.store(self.db, pid, pull, "upload", fmt="fhir")
        robin, p = self.login("robin"), f"/api/profiles/{pid}"
        ev = {e["title"]: e["id"] for e in records.assemble(self.db, pid)["events"]}
        for body in ({"event": ev["Annual physical (fictional)"], "action": "hide"},
                     {"event": ev["Follow-up (fictional)"], "action": "merge", "other": ev["Clinic visit (fictional)"]},
                     {"event": ev["Telehealth (fictional)"], "action": "rename", "title": "Video visit (fictional)"},
                     {"event": ev["Dated wrong (fictional)"], "action": "date", "t": "2026-04-02T14:30"},
                     {"event": ev["Day unit · Fictional Hospital"], "action": "not_stay"},
                     {"event": ev["Skin biopsy (fictional)"], "action": "kind", "k": "procedure"}):
            r = robin.post(p + "/edits", json=body, headers=H)
            self.assertEqual(r.status_code, 201, r.text)
        ref = records.assemble(self.db, pid)["possible"][0]["ref"]
        self.assertEqual(robin.post(p + "/same", json={"ref": ref, "same": True}, headers=H).status_code, 201)

        bundle = self.bundle(robin.get(p + "/export?format=fhir").content)[0]
        res = [e["resource"] for e in bundle["entry"]]
        enc = {r["type"][0]["text"]: r for r in res if r["resourceType"] == "Encounter"}
        self.assertEqual(sorted(enc), ["Dated wrong (fictional)", "Day unit · Fictional Hospital", "Follow-up (fictional)",
                                       "Video visit (fictional)"],
                         "not the hidden visit, the one merged in, the renamed one's old title, nor the one moved to Procedures")
        follow = enc["Follow-up (fictional)"]
        self.assertEqual([x["individual"]["display"] for x in follow["participant"]], ["Dr Example (fictional)"],
                         "the merged visit, with who saw them from the one merged into it")
        self.assertTrue(enc["Dated wrong (fictional)"]["period"]["start"].startswith("2026-04-02T14:30:00"), enc["Dated wrong (fictional)"])
        stay = enc["Day unit · Fictional Hospital"]
        self.assertEqual((stay["class"]["code"], stay["period"].get("end")), ("AMB", None), "not a hospital stay: a visit, with no end")
        self.assertEqual([r["code"]["text"] for r in res if r["resourceType"] == "Procedure"], ["Skin biopsy (fictional)"])
        hgb = [r for r in res if r["resourceType"] == "Observation" and r["code"].get("text") == "Hemoglobin"]
        self.assertEqual(len(hgb), 1, "the result both sources gave, linked as the same result: once")
        self.assertNotIn("Annual physical", json.dumps(bundle))
        self.assertNotIn("Clinic visit", json.dumps(bundle))
        self.assertNotIn("Telehealth", json.dumps(bundle))
        # Export everything's data.json stays as imported, with the corrections beside it (#589)
        raw = export.plan(self.db, pid)["data"]
        self.assertEqual(len(raw["events"]), 7)
        self.assertEqual(sorted(x["action"] for x in raw["corrections"]["edits"]), ["date", "hide", "kind", "merge", "not_stay", "rename"])
        self.assertEqual(len(raw["corrections"]["same"]), 1)
        profiles.delete_profile(self.db, pid)

    def test_copies_of_one_result_go_once(self):
        """#947: one result that came from three sources, not linked as the same result (a portal's report, a
        hospital's report of the same draw, a spreadsheet with no range), is one result on the page (#825: Labs, the
        summary and share links show it once, with the range and flag its reports give it). The export joined only
        results linked as the same, so it went out three times, one Observation each. Now it's one, in each report that
        holds it, as the page shows it (records.served, #916)."""
        t = "2026-06-02T08:30:00"

        def report(eid, order, key):
            return ({"events": [{"id": eid, "t": t, "k": "lab", "title": "Lab results", "sub": "1 lab result"},
                                {"id": eid + 1, "t": t, "k": "labitem", "title": order, "sub": "", "key": key}],
                     "details": {str(eid): {"type": "labday", "ids": [eid + 1]},
                                 str(eid + 1): {"type": "result", "order": order, "rows": [["Potassium", "5.2", "mmol/L", "3.5-5.1", "H"]]}}})
        k = lambda key, lo=3.5, hi=5.1, flag="H": {"name": "Potassium", "unit": "mmol/L", "loinc": "2823-3", "key": key, "panel": "BMP",   # noqa: E731
                                                   "pts": [[t, 5.2, lo, hi, flag, "5.2"]]}
        portal, hospital = report(1, "Basic metabolic panel", "p:bmp"), report(1, "BMP", "h:bmp")
        pid = profiles.create_profile(self.db, "Three copies (fictional)", owner_id=self.uids["robin"])
        with dbm.transaction(self.db):
            records.store(self.db, pid, {**portal, "costs": [], "labs": [k("p:k")]}, "upload", fmt="fhir")
            records.store(self.db, pid, {**hospital, "costs": [], "labs": [k("h:k")]}, "upload", fmt="ccda")
            records.store(self.db, pid, {"events": [], "details": {}, "costs": [], "labs": [k("c:k", None, None, "")]}, "upload", fmt="csv")
        shown = records.assemble(self.db, pid)
        self.assertEqual(len({(s["name"], s["unit"]) for s in shown["labs"]}), 1)
        self.assertFalse(shown.get("same"), "not linked as the same result")
        res = [e["resource"] for e in self.bundle(self.export(pid, {"labs"}))[0]["entry"]]
        obs = [r for r in res if r["resourceType"] == "Observation" and r["code"].get("text") == "Potassium"]
        self.assertEqual(len(obs), 1, "one result, once")
        self.assertEqual((obs[0]["valueQuantity"]["value"], obs[0]["interpretation"][0]["coding"][0]["code"]), (5.2, "H"))
        reports = [r for r in res if r["resourceType"] == "DiagnosticReport"]
        self.assertEqual(sorted(x["code"]["text"] for x in reports), ["BMP", "Basic metabolic panel"])
        self.assertEqual([[x["reference"] for x in r["result"]] for r in reports], [[f"urn:uuid:{obs[0]['id']}"]] * 2, "in each report")
        profiles.delete_profile(self.db, pid)

    # ---------------------------------------------------------------- the bundle itself
    def test_valid_against_the_fhir_r4_schema(self):
        bundle, _, _ = self.bundle(self.export(cycle=True))
        self.assertEqual(bundle["type"], "collection")
        self.assertEqual(fhir_schema_check.errors(bundle), [])
        # and the checker does catch what's wrong (a time with no offset, an unknown element, a bad code)
        bad = {"resourceType": "Observation", "status": "done", "code": {"text": "x"}, "effectiveDateTime": "2026-01-01T10:00:00", "colour": 1}
        self.assertEqual(len(fhir_schema_check.errors(bad)), 3, fhir_schema_check.errors(bad))

    def test_ids_are_stable_and_unique(self):
        a, _, _ = self.bundle(self.export())
        b, _, _ = self.bundle(self.export())
        ids = [e["resource"]["id"] for e in a["entry"]]
        self.assertEqual(len(ids), len(set(ids)), "no two resources share an id")
        self.assertEqual(ids, [e["resource"]["id"] for e in b["entry"]], "the same records, the same ids")
        self.assertTrue(all(e["fullUrl"] == "urn:uuid:" + e["resource"]["id"] for e in a["entry"]))
        refs = set(re.findall(r'"reference": ?"(urn:uuid:[^"]+)"', json.dumps(a)))
        self.assertLessEqual(refs, {e["fullUrl"] for e in a["entry"]}, "every reference is to a resource in the bundle")

    def test_about_this_person_on_the_patient(self):
        bundle, _, _ = self.bundle(self.export())
        pat = bundle["entry"][0]["resource"]
        self.assertEqual(pat["resourceType"], "Patient")
        self.assertEqual(pat["birthDate"], "1979-06-02")
        self.assertNotIn("gender", pat, "the sex used for reference ranges is never written as administrative gender")
        ext = pat["extension"][0]
        self.assertEqual(ext["url"], fhir_export.SEX_EXT)
        self.assertEqual(ext["extension"][0]["valueCodeableConcept"]["coding"][0]["code"], "female-typical")
        heights = [e["resource"] for e in bundle["entry"] if "8302-2" in json.dumps(e["resource"].get("code"))]
        self.assertEqual(sorted(h["valueQuantity"]["value"] for h in heights), [167.6, 168.2])
        self.assertTrue(all(h["meta"]["profile"] == [fhir_export.VITALS + "bodyheight"] for h in heights))
        # without the part: no birth date, no sex, no heights
        bundle, _, _ = self.bundle(self.export(parts=set(fhir_export.PART_KEYS) - {"person"}))
        pat = bundle["entry"][0]["resource"]
        self.assertNotIn("birthDate", pat)
        self.assertNotIn("extension", pat)
        self.assertFalse([e for e in bundle["entry"] if "8302-2" in json.dumps(e["resource"].get("code"))])

    def test_home_readings_and_ecgs(self):
        bundle, _, _ = self.bundle(self.export())
        res = [e["resource"] for e in bundle["entry"]]
        bp = [r for r in res if r["resourceType"] == "Observation" and r.get("device") is None and "85354-9" in json.dumps(r["code"])
              and r.get("performer") == [{"reference": "urn:uuid:" + res[0]["id"]}]]
        self.assertEqual(len(bp), 1, "the reading typed in: the Patient its performer, no device")
        self.assertEqual([c["valueQuantity"]["value"] for c in bp[0]["component"]], [131, 84, 71])
        phone = [r for r in res if r.get("device") and "85354-9" in json.dumps(r.get("code"))]
        self.assertTrue(phone and all(r["meta"]["profile"] == [fhir_export.VITALS + "bp"] for r in phone))
        devices = {r["id"]: r for r in res if r["resourceType"] == "Device"}
        cuff = devices[phone[0]["device"]["reference"][9:]]
        self.assertEqual(cuff["deviceName"][0]["name"], "Fictional Cuff")
        cgm = [r for r in res if '"glucose_avg"' in json.dumps(r.get("code"))]
        self.assertEqual(len(cgm), 14, "a CGM's days as daily summaries, never each reading")
        ecgs = [r for r in res if '"ecg"' in json.dumps(r.get("code"))]
        self.assertEqual(len(ecgs), 3)
        wave = next(c["valueSampledData"] for c in ecgs[0]["component"] if "valueSampledData" in c)
        self.assertEqual((wave["period"], wave["dimensions"], len(wave["data"].split())), (1.953125, 1, 15360))
        self.assertEqual(ecgs[0]["valueCodeableConcept"]["text"], "Sinus Rhythm", "the device's classification, as it said it")

    def test_a_measure_whose_values_arent_numbers_stays_out(self):
        """A metric of the watch summary whose day values are lists (#234's glucose distribution) or text: no Observation."""
        b = fhir_export.Bundle({"pid": 1, "created": 0}, None)
        watch = {"metrics": [{"key": "glucose_dist", "name": "Glucose distribution", "unit": "readings", "group": "Glucose",
                              "pts": [["2026-11-01", [0, 3, 40, 2]], ["2026-11-02", "n/a"]]},
                             {"key": "steps", "name": "Steps", "unit": "steps", "group": "Activity", "pts": [["2026-11-01", 4200]]}]}
        got = list(fhir_export._home(b, watch))
        self.assertEqual([o["valueQuantity"]["value"] for o in got], [4200])

    def test_cycle_records_only_when_ticked(self):
        plain, _, readme = self.bundle(self.export())
        self.assertNotIn("cycle_", json.dumps(plain))
        self.assertNotIn("Cycle records", readme)
        ticked, _, readme = self.bundle(self.export(cycle=True))
        cyc = [e["resource"] for e in ticked["entry"] if "cycle_" in json.dumps(e["resource"].get("code"))]
        self.assertTrue(cyc)
        self.assertIn("Cycle records: ticked in for this export.", readme)
        got = importers.run(unzip(self.export(cycle=True), tempfile.mkdtemp(dir=TMP)))
        self.assertTrue(any(w.startswith(f"Left out {len(cyc)} cycle records") for w in got["warnings"]), got["warnings"])
        self.assertNotIn("cycle", got["data"].get("watch") or {})

    def test_only_the_parts_ticked_and_what_doesnt_map(self):
        bundle, names, readme = self.bundle(self.export(parts={"labs"}))
        kinds = {e["resource"]["resourceType"] for e in bundle["entry"]}
        self.assertEqual(kinds, {"Patient", "Observation", "DiagnosticReport", "Organization", "Practitioner"}, kinds)
        self.assertNotIn("Fictional Cuff", json.dumps(bundle), "no home readings")
        self.assertEqual([n for n in names if not n.endswith(".json") and n != "README.txt"], [], "no documents")
        for left in ("Claims", "Messages", "Home readings and the phone's daily measures", "the journal", "workouts", "out-of-pocket payments"):
            self.assertIn(left, readme)
        self.assertNotIn("journal note", json.dumps(self.bundle(self.export())[0]), "the journal never goes in")
        with self.assertRaises(ValueError):
            fhir_export.parts_of("labs,nonsense")

    def test_the_page_offers_the_same_parts(self):
        with open(os.path.join(ROOT, "www", "sharing.html"), encoding="utf-8") as f:
            page = f.read()
        offered = re.findall(r'<input type="checkbox" name="exportPart" value="([a-z]+)" checked> ([^<]+)</label>', page)
        self.assertEqual(offered, fhir_export.PARTS, "Sharing → Export everything lists fhir_export.PARTS, in its order and words")

    def test_streamed(self):
        p = fhir_export.plan(self.db, 1, set(fhir_export.PART_KEYS))
        pieces = list(fhir_export.stream(p, "robin"))
        self.assertGreater(len(pieces), 5, "written and sent a piece at a time")
        self.assertLess(max(map(len, pieces)), 3_000_000)

    # ---------------------------------------------------------------- the route
    def login(self, name):
        c = TestClient(main.app, base_url="http://testserver")
        r = c.post("/api/login", json={"username": name, "password": PW[name]}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return c

    def test_route_roles_and_activity(self):
        lee = self.login("lee")
        r = lee.get("/api/profiles/1/export?format=fhir&include=labs,visits")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["content-type"], "application/zip")
        self.assertRegex(r.headers["content-disposition"], r'filename="sanguity-[a-z0-9-]+-\d{8}-fhir\.zip"')
        bundle, _, _ = self.bundle(r.content)
        self.assertNotIn("ExplanationOfBenefit", {e["resource"]["resourceType"] for e in bundle["entry"]})
        self.assertEqual(lee.get("/api/profiles/1/export?format=fhir&include=bogus").status_code, 400)
        self.assertEqual(lee.get("/api/profiles/1/export?format=xml").status_code, 400)
        self.assertEqual(self.login("kim").get("/api/profiles/1/export?format=fhir").status_code, 403, "viewers don't export")
        row = self.db.execute("SELECT action, detail FROM activity WHERE profile_id=1 AND action='export' ORDER BY id DESC").fetchone()
        from server import crypto
        self.assertEqual(json.loads(crypto.dec(row["detail"])), {"format": "fhir", "parts": ["labs", "visits"]})
        self.assertEqual(self.login("robin").get("/api/profiles/1/export?format=fhir&cycle=1").status_code, 200)
        r = lee.get("/api/profiles/1/export")   # the Sanguity zip as before
        self.assertIn("data.json", zipfile.ZipFile(io.BytesIO(r.content)).namelist())


if __name__ == "__main__":
    unittest.main(verbosity=2)
