"""ECG recordings and irregular rhythm notifications from a watch (#182, server/ecg.py): an uploaded Apple Health export
(its electrocardiograms/ CSV files and export.xml's notifications) and Health Auto Export's uploads to watch/receiver.py
give the same events; the samples are kept apart from the summary, sealed, read by their own route; the name and birth
date at the top of Apple's files are never kept; nothing reaches a share link or the visit summary. All data here is
fictional, written for these tests: tests/fixtures/importers/apple-health/ecg is made by fictional_ecg() below
(python tests/test_ecg.py --write-fixture writes it again).

  python tests/test_ecg.py
"""
import atexit, csv, hashlib, importlib.util, io, json, math, os, random, shutil, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "importers", "apple-health", "ecg", "apple_health_export")
OFF = timezone(timedelta(hours=-4))
NAME, BIRTH = "Robin Fictional-Example", "Mar 3, 1970"   # never kept anywhere
DEVICE, WATCH = "Watch7,4", "Fictional Watch"
# the fictional recordings: (local start, classification, beats a minute, irregular beat spacing, symptoms)
RECORDINGS = [((2026, 9, 12, 8, 15, 4), "Sinus Rhythm", 64, False, ""),
              ((2026, 10, 3, 21, 40, 31), "Atrial Fibrillation", 96, True, "Rapid, pounding, or fluttering heartbeat"),
              ((2026, 10, 20, 7, 2, 50), "Inconclusive", 112, False, "")]
NOTIFICATIONS = [((2026, 10, 2, 23, 5, 0), (2026, 10, 3, 1, 15, 0)), ((2026, 10, 19, 3, 30, 0), (2026, 10, 19, 5, 10, 0))]
RATE, SECONDS = 512, 30


def fictional_ecg(bpm, irregular, seed):
    """30 seconds at 512 Hz of a made-up single-lead trace in µV, by formula: P, QRS and T waves as Gaussian bumps a
    beat, a slow drift and a little noise. It isn't anyone's and isn't meant to be medically faithful."""
    rnd, beats, t = random.Random(seed), [], 0.25
    while t < SECONDS + 1:
        beats.append(t)
        t += 60 / bpm * (rnd.uniform(0.6, 1.4) if irregular else rnd.uniform(0.97, 1.03))
    waves = ((-0.16, 110, 0.025), (-0.03, -70, 0.008), (0, 1050, 0.010), (0.025, -240, 0.010), (0.24, 280, 0.045))
    out = []
    for i in range(SECONDS * RATE):
        s = i / RATE
        v = 30 * math.sin(2 * math.pi * 0.23 * s) + rnd.gauss(0, 8)
        for b in beats:
            if -0.4 < s - b < 0.5:
                v += sum(a * math.exp(-((s - b - c) / w) ** 2) for c, a, w in waves if not (irregular and c < -0.1))
        out.append(round(v, 3))
    return out


def recordings():
    """[(local start, classification, symptoms, samples)] of the fictional recordings."""
    return [(datetime(*t, tzinfo=OFF), cls, sym, fictional_ecg(bpm, irr, i)) for i, (t, cls, bpm, irr, sym) in enumerate(RECORDINGS)]


def fmt(t):
    return t.strftime("%Y-%m-%d %H:%M:%S %z")


def write_fixture():
    """The fictional export: export.xml with a few records and the notifications, and one CSV a recording."""
    shutil.rmtree(FIXTURE, ignore_errors=True)
    os.makedirs(os.path.join(FIXTURE, "electrocardiograms"))
    for start, cls, sym, samples in recordings():
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        for k, v in (("Name", NAME), ("Date of Birth", BIRTH), ("Recorded Date", fmt(start)), ("Classification", cls),
                     ("Symptoms", sym), ("Software Version", "2.0"), ("Device", DEVICE), ("Sample Rate", f"{RATE} hertz"),
                     ("Lead", "Lead I"), ("Unit", "µV")):
            w.writerow([k, v])
        buf.write("\n" + "".join(f"{v}\n" for v in samples))
        with open(os.path.join(FIXTURE, "electrocardiograms", f"ecg_{start.date()}.csv"), "w", encoding="utf-8", newline="") as f:
            f.write(buf.getvalue())
    recs = "".join(f' <Record type="HKCategoryTypeIdentifierIrregularHeartRhythmEvent" sourceName="{WATCH}" sourceVersion="11.0" '
                   f'creationDate="{fmt(datetime(*e, tzinfo=OFF))}" startDate="{fmt(datetime(*s, tzinfo=OFF))}" '
                   f'endDate="{fmt(datetime(*e, tzinfo=OFF))}" value="HKCategoryValueNotApplicable"/>\n' for s, e in NOTIFICATIONS)
    with open(os.path.join(FIXTURE, "export.xml"), "w", encoding="utf-8") as f:
        f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE HealthData [
<!ELEMENT HealthData (ExportDate,Me,(Record|Correlation|Workout|ActivitySummary)*)>
<!ATTLIST HealthData locale CDATA #REQUIRED>
]>
<HealthData locale="en_US">
 <ExportDate value="2026-11-01 09:00:00 -0400"/>
 <Me HKCharacteristicTypeIdentifierDateOfBirth="" HKCharacteristicTypeIdentifierBiologicalSex="HKBiologicalSexNotSet"/>
 <Record type="HKQuantityTypeIdentifierStepCount" sourceName="{WATCH}" unit="count" startDate="2026-10-03 08:00:00 -0400" endDate="2026-10-03 08:59:00 -0400" value="900"/>
 <Record type="HKCategoryTypeIdentifierHighHeartRateEvent" sourceName="{WATCH}" startDate="2026-10-05 14:00:00 -0400" endDate="2026-10-05 14:10:00 -0400" value="HKCategoryValueNotApplicable"/>
{recs}</HealthData>
""")


def hae_payloads():
    """The same recordings and notifications as Health Auto Export sends them: an ECG automation's upload (voltages in
    µV, "mcV", with Unix times) and a Heart Rate Notifications automation's, with a high heart rate alert beside."""
    ecg = []
    for start, cls, _, samples in recordings():
        t0 = start.timestamp()
        ecg.append({"start": fmt(start), "end": fmt(start + timedelta(seconds=SECONDS)), "classification": cls,
                    "averageHeartRate": {0: 65, 1: 97, 2: 113}[len(ecg)], "numberOfVoltageMeasurements": len(samples),
                    "samplingFrequency": RATE, "source": WATCH,
                    "voltageMeasurements": [{"date": t0 + i / RATE, "voltage": v, "units": "mcV"} for i, v in enumerate(samples)]})
    notes = [{"heartNotification": "irregular_rhythm_notification", "start": fmt(datetime(*s, tzinfo=OFF)), "end": fmt(datetime(*e, tzinfo=OFF)),
              "source": {"name": WATCH, "identifier": "com.example.fictionalwatch"}, "heartRateData": []} for s, e in NOTIFICATIONS]
    notes.append({"heartNotification": "high_heart_rate_notification", "start": "2026-10-05 14:00:00 -0400", "end": "2026-10-05 14:10:00 -0400",
                  "threshold": 120, "source": {"name": WATCH, "identifier": "com.example.fictionalwatch"},
                  "heartRateData": [{"qty": 125, "units": "count/min", "start": 1791223200, "end": 1791223205}]})
    return {"data": {"ecg": ecg}}, {"data": {"heartRateNotifications": notes}}


if __name__ == "__main__" and "--write-fixture" in sys.argv:
    write_fixture()
    sys.exit(0)

if "server.config" not in sys.modules:
    TMP = tempfile.mkdtemp(prefix="sanguity-ecg-test-")
    atexit.register(shutil.rmtree, TMP, True)
    os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "no-build"),
                      SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), PASSWORD_MIN_LENGTH="15",
                      SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"), SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"))
    os.makedirs(os.path.join(TMP, "records"), exist_ok=True)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, crypto, db as dbm, ecg, history, main, profiles as prof, share_links, summary, watch_store   # noqa: E402
from server.importers import apple_health   # noqa: E402

H = {"X-Sanguity": "1"}
PW = "plum lantern seventy quietly"
SAME = ("key", "type", "start", "end", "classification", "rate", "samples", "unit", "wave")   # what both sources give


def files():
    out = {"apple_health_export/export.xml": os.path.join(FIXTURE, "export.xml")}
    for n in sorted(os.listdir(os.path.join(FIXTURE, "electrocardiograms"))):
        out[f"apple_health_export/electrocardiograms/{n}"] = os.path.join(FIXTURE, "electrocardiograms", n)
    return out


def load_receiver():
    spec = importlib.util.spec_from_file_location("watch_receiver_ecg", os.path.join(ROOT, "watch", "receiver.py"))
    rx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rx)
    return rx


class App(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = TestClient(main.app).__enter__()   # app start-up: database, key, pepper
        cls.db = dbm.connect()
        cls.uid, _ = accounts.create(cls.db, "ecg-robin", password=PW)
        cls.rx = load_receiver()
        cls.rx.DATA = tempfile.mkdtemp(prefix="rx-", dir=os.environ["SANGUITY_DATA"])
        cls.out = apple_health.parse(files())
        cls.watch = cls.out["data"]["watch"]

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.ctx.__exit__(None, None, None)

    def store(self, slot=""):
        s = self.rx.Store(slot)
        self.addCleanup(shutil.rmtree, self.rx.slot_dir(slot) if slot else os.path.join(self.rx.DATA, "raw"), True)
        return s

    def send(self, store, p):
        b = json.dumps(p).encode()
        self.rx.archive(b, store.slot)
        return store.ingest(json.loads(b), hashlib.sha256(b).hexdigest())

    def client(self):
        c = TestClient(main.app)
        self.assertEqual(c.post("/api/login", json={"username": "ecg-robin", "password": PW}, headers=H).status_code, 200)
        return c

    # ---------------------------------------------------------------- the fixture
    def test_the_fixture_is_the_fictional_formula(self):
        for start, cls, _, samples in recordings():
            with open(os.path.join(FIXTURE, "electrocardiograms", f"ecg_{start.date()}.csv"), encoding="utf-8") as f:
                text = f.read()
            ev, wave = ecg.from_csv(text)
            self.assertEqual(wave["samples"], samples)
            self.assertEqual(ev["classification"], cls)

    # ---------------------------------------------------------------- an uploaded export
    def test_the_export_gives_its_recordings_and_notifications(self):
        evs = self.watch["ecg"]
        self.assertEqual([e["type"] for e in evs], ["ecg", "irregular_rhythm", "ecg", "irregular_rhythm", "ecg"])
        recs = [e for e in evs if e["type"] == "ecg"]
        self.assertEqual([e["classification"] for e in recs], ["Sinus Rhythm", "Atrial Fibrillation", "Inconclusive"], "word for word")
        first = recs[0]
        self.assertEqual(first["start"], "2026-09-12T12:15:04+00:00")
        self.assertEqual(first["end"], "2026-09-12T12:15:34+00:00")
        self.assertEqual((first["rate"], first["samples"], first["lead"], first["unit"], first["device"], first["software"], first["via"]),
                         (512, 15360, "Lead I", "µV", DEVICE, "2.0", "Apple Health export"))
        self.assertNotIn("avg_hr", first, "the export holds no average heart rate")
        self.assertNotIn("symptoms", first)
        self.assertEqual(recs[1]["symptoms"], "Rapid, pounding, or fluttering heartbeat")
        self.assertEqual(set(self.watch["ecg_waves"]), {e["key"] for e in recs})
        notes = [e for e in evs if e["type"] == "irregular_rhythm"]
        self.assertEqual([(e["start"], e["end"], e["device"]) for e in notes],
                         [("2026-10-03T03:05:00+00:00", "2026-10-03T05:15:00+00:00", WATCH), ("2026-10-19T07:30:00+00:00", "2026-10-19T09:10:00+00:00", WATCH)])
        self.assertEqual(self.out["warnings"], [])
        from server import importers as registry
        self.assertEqual(registry.check(self.out, files()), [])
        self.assertEqual(registry.match(files())["importer"].NAME, "apple-health")

    def test_the_name_and_birth_date_are_never_kept(self):
        text = json.dumps(self.out, ensure_ascii=False)
        for word in (NAME, "Robin", BIRTH, "1970", "Date of Birth"):
            self.assertNotIn(word, text)

    def test_another_language_by_place_and_a_file_it_cant_read(self):
        with open(os.path.join(FIXTURE, "electrocardiograms", "ecg_2026-09-12.csv"), encoding="utf-8") as f:
            lines = f.read().split("\n")
        heads = ["Nom", "Date de naissance", "Date d’enregistrement", "Classification", "Symptômes", "Version du logiciel",
                 "Appareil", "Fréquence d’échantillonnage", "Dérivation", "Unité"]
        fr = [h + "," + line.split(",", 1)[1] for h, line in zip(heads, lines[:10])] + lines[10:]
        fr[3] = "Classification,Rythme sinusal"
        ev, wave = ecg.from_csv("\n".join(fr))
        self.assertEqual((ev["classification"], ev["rate"], len(wave["samples"]), ev["start"]), ("Rythme sinusal", 512, 15360, "2026-09-12T12:15:04+00:00"))
        self.assertNotIn(NAME, json.dumps([ev, wave], ensure_ascii=False))
        d = tempfile.mkdtemp(dir=os.environ["SANGUITY_DATA"])
        self.addCleanup(shutil.rmtree, d, True)
        with open(os.path.join(d, "bad.csv"), "w", encoding="utf-8") as f:
            f.write("Name,Someone\nRecorded Date,yesterday\n\n1\n2\n")
        evs, waves, warn = ecg.from_export({"x/electrocardiograms/bad.csv": os.path.join(d, "bad.csv")})
        self.assertEqual((evs, waves), ([], {}))
        self.assertEqual(warn, ["1 ECG file in the export couldn't be read, so it was left out."])

    # ---------------------------------------------------------------- Health Auto Export, the same events
    def test_a_payload_gives_the_same_events(self):
        store = self.store()
        recs, notes = hae_payloads()
        self.assertTrue(self.rx.is_hae(recs) and self.rx.is_hae(notes))
        self.send(store, recs)
        self.send(store, notes)
        got = store.summary()["ecg"]
        want = self.watch["ecg"]
        self.assertEqual([{k: e.get(k) for k in SAME} for e in got], [{k: e.get(k) for k in SAME} for e in want])
        self.assertNotIn("2026-10-05", json.dumps(got), "a high heart rate alert is left out")
        self.assertEqual([e.get("avg_hr") for e in got if e["type"] == "ecg"], [65, 97, 113])
        self.assertEqual({e["device"] for e in got}, {WATCH})
        blobs = dict(store.db.execute("SELECT key, wave FROM ecg WHERE wave IS NOT NULL"))
        self.assertEqual({k: ecg.unpack(v)["samples"] for k, v in blobs.items()},
                         {k: w["samples"] for k, w in self.watch["ecg_waves"].items()}, "the same samples")
        self.assertNotIn("samples\":[", json.dumps(store.summary()), "no samples in the summary")

    def test_sent_again_counts_once_and_keeps_its_samples(self):
        store = self.store()
        recs, notes = hae_payloads()
        self.send(store, recs)
        self.send(store, recs)   # the same upload again: a duplicate
        bare = {"data": {"ecg": [{k: v for k, v in e.items() if k != "voltageMeasurements"} for e in recs["data"]["ecg"]],
                         "heartRateNotifications": notes["data"]["heartRateNotifications"][:1]}}
        self.send(store, bare)   # the recordings again without their voltages, and a notification
        got = store.summary()["ecg"]
        self.assertEqual(len([e for e in got if e["type"] == "ecg"]), 3)
        self.assertTrue(all(e.get("wave") for e in got if e["type"] == "ecg"))
        self.assertEqual(store.db.execute("SELECT COUNT(*) FROM ecg WHERE wave IS NOT NULL").fetchone()[0], 3)

    def test_the_receiver_writes_the_samples_sealed_beside_its_summary(self):
        store = self.store("41")
        self.send(store, hae_payloads()[0])
        store.save()
        store.write_summary()
        d = os.path.join(os.path.dirname(self.rx.summary_file("41")), "ecg")
        self.addCleanup(shutil.rmtree, os.path.dirname(self.rx.summary_file("41")), True)
        names = sorted(os.listdir(d))
        self.assertEqual(names, sorted(ecg.file_name(e["key"]) for e in self.watch["ecg"] if e["type"] == "ecg"))
        for n in names:
            self.assertTrue(crypto.file_is_encrypted(os.path.join(d, n)))
        self.assertEqual(self.rx.ecg_files("41"), [os.path.join(d, n) for n in names])
        daily = json.loads(crypto.read_file(self.rx.summary_file("41")))
        self.assertEqual(len(daily["ecg"]), 3)
        self.assertNotIn("voltage", json.dumps(daily))

    def test_an_older_database_reads_the_ecgs_already_in_raw_once(self):
        store = self.store("42")
        self.addCleanup(shutil.rmtree, os.path.dirname(self.rx.summary_file("42")), True)
        self.send(store, hae_payloads()[0])   # archived and taken by a receiver from before #182, which left its ECGs out
        store.db.execute("DELETE FROM ecg")
        store.db.execute("DELETE FROM meta WHERE k='ecg'")
        store.save()
        again = self.rx.Store("42")
        self.assertEqual(again.catch_up(), 1)
        self.assertEqual(len(again.summary()["ecg"]), 3)
        self.assertEqual(again.meta("ecg"), "1")
        again.save()
        self.assertEqual(self.rx.Store("42").catch_up(), 0, "only once")

    def test_merge_keeps_what_each_source_has(self):
        store = self.store()
        self.send(store, hae_payloads()[0])
        phone = store.summary()
        both = watch_store.merge({**self.watch, "ecg_waves": None}, phone)
        rec = next(e for e in both["ecg"] if e["type"] == "ecg")
        self.assertEqual((rec["avg_hr"], rec["lead"], rec["software"], rec["via"]), (65, "Lead I", "2.0", "Health Auto Export"))
        self.assertEqual(len(both["ecg"]), 5, "the phone's three recordings are the export's three")

    # ---------------------------------------------------------------- saved, served, undone; never shared
    def test_saved_served_by_its_own_route_and_gone_with_its_import(self):
        pid = prof.create_profile(self.db, "ECG test", owner_id=self.uid)
        stored = watch_store.save_import(self.db, pid, self.watch, import_id=None)
        self.assertEqual((stored["ecgs"], stored["rhythm"]), (3, 2))
        layer = json.loads(crypto.read_file(watch_store.layer_path(pid)))
        self.assertNotIn("ecg_waves", layer)
        self.assertNotIn("samples\":[", json.dumps(layer))
        d = watch_store.waves_dir(pid)
        self.assertEqual(len(os.listdir(d)), 3)
        for n in os.listdir(d):
            with open(os.path.join(d, n), "rb") as f:
                self.assertFalse(b"Lead I" in f.read(), "sealed")
        c = self.client()
        page = c.get(f"/api/profiles/{pid}/watch").json()
        self.assertEqual(len(page["ecg"]), 5)
        self.assertNotIn("samples\":[", json.dumps(page), "the records page gets no samples")
        k = next(e["key"] for e in page["ecg"] if e["type"] == "ecg")
        r = c.get(f"/api/profiles/{pid}/watch/ecg/{k.split(':')[1]}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("cache-control"), "no-store")
        self.assertEqual(r.json()["samples"], self.watch["ecg_waves"][k]["samples"])
        for bad in ("0123456789abcdef", "..%2F..%2Fx", "ECG", k.split(":")[1].upper()):
            self.assertEqual(c.get(f"/api/profiles/{pid}/watch/ecg/{bad}").status_code, 404, bad)
        other = prof.create_profile(self.db, "Not Robin's", owner_id=None)
        self.assertEqual(c.get(f"/api/profiles/{other}/watch/ecg/{k.split(':')[1]}").status_code, 404)
        self.assertEqual(TestClient(main.app).get(f"/api/profiles/{pid}/watch/ecg/{k.split(':')[1]}").status_code, 401)
        # an import that brought them, undone: its samples go once the undo has committed
        p3 = prof.create_profile(self.db, "ECG undo", owner_id=self.uid)
        iid3 = self.db.execute("INSERT INTO imports(profile_id, at, origin, format) VALUES(?, 0, 'upload', 'apple-health')", (p3,)).lastrowid
        watch_store.save_import(self.db, p3, self.watch, import_id=iid3)
        self.assertEqual(len(os.listdir(watch_store.waves_dir(p3))), 3)
        self.db.execute("DELETE FROM imports WHERE id=?", (iid3,))
        finish = watch_store.forget_import(self.db, p3, iid3)
        self.assertEqual(len(os.listdir(watch_store.waves_dir(p3))), 3, "kept until the undo commits")
        finish(True)
        self.assertEqual(os.listdir(watch_store.waves_dir(p3)), [])
        self.assertIsNone(watch_store.load(self.db, p3))

    def test_a_receivers_samples_are_found_beside_its_file(self):
        store = self.store("43")
        self.send(store, hae_payloads()[0])
        store.save()
        store.write_summary()
        self.addCleanup(shutil.rmtree, os.path.dirname(self.rx.summary_file("43")), True)
        pid = prof.create_profile(self.db, "ECG phone", owner_id=self.uid, watch_file=self.rx.summary_file("43"))
        k = store.summary()["ecg"][0]["key"]
        w = watch_store.wave(self.db, pid, k.split(":")[1])
        self.assertEqual(len(w["samples"]), 15360)

    def test_in_a_share_link_or_the_visit_summary_only_when_ticked(self):
        """#225: a section of their own, unticked unless chosen; what the device said, never the samples."""
        pid = prof.create_profile(self.db, "Sharing test", owner_id=self.uid)
        watch_store.save_import(self.db, pid, self.watch)
        c = self.client()
        offered = c.get(f"/api/profiles/{pid}/links").json()["sections"]
        self.assertIn("ecg", [s["id"] for s in offered], "offered: the profile has some")
        self.assertIn("ecg", [s["id"] for s in c.get(f"/api/profiles/{pid}/summary").json()["sections"] if s.get("off")])
        but = [s for s in share_links.SECTION_IDS if s != "ecg"]
        for sections, shown in ((but, False), (["ecg"], True)):
            r = c.post(f"/api/profiles/{pid}/links", json={"label": "Everything", "sections": sections, "hours": 24}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            view = TestClient(main.app).post("/api/share/view", json={"token": r.json()["path"].split("#", 1)[1]}, headers=H)
            self.assertEqual(view.status_code, 200, view.text)
            built = summary.build(self.db, pid, self.uid, "Sharing test", {"sections": [s for s, _ in summary.sections() if s in sections or s == "questions"],
                                                                         "since": "2026-09-01"})
            for text in (view.text, json.dumps(built, ensure_ascii=False)):
                for word in ("Sinus", "Atrial", "Inconclusive", "Irregular rhythm notification"):
                    (self.assertIn if shown else self.assertNotIn)(word, text)
                for word in ("Lead I", "samples", "15360"):
                    self.assertNotIn(word, text, "never the samples")
        self.assertNotIn("cycle", share_links.SECTION_IDS)

    def test_the_export_carries_the_samples_and_loads_them_again(self):
        """#225: Export everything gives each recording's samples as watch/ecg/<16 hex>.json; loading it puts them
        back beside the loaded daily.json, sealed, where the drawer reads them."""
        import zipfile
        from server import export
        from server import records
        pid = prof.create_profile(self.db, "ECG export", owner_id=self.uid)
        with dbm.transaction(self.db):
            records.store(self.db, pid, {"events": [{"id": 1, "t": "2026-10-01", "k": "visit", "title": "Visit"}], "labs": []}, "dataset")
        watch_store.save_import(self.db, pid, self.watch)
        z = zipfile.ZipFile(io.BytesIO(b"".join(export.stream(export.plan(self.db, pid), "ecg-robin"))))
        names = sorted(n for n in z.namelist() if n.startswith("watch/ecg/"))
        self.assertEqual(len(names), 3)
        k = next(e["key"] for e in self.watch["ecg"] if e["type"] == "ecg")
        self.assertEqual(json.loads(z.read(f"watch/ecg/{k.split(':')[1]}.json"))["samples"], self.watch["ecg_waves"][k]["samples"])
        self.assertIn("watch/ecg/", z.read("README.txt").decode())
        back = prof.create_profile(self.db, "ECG loaded", owner_id=self.uid)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as out:
            for n in z.namelist():
                out.writestr(n, z.read(n))
            out.writestr("watch/ecg/0123456789abcdef.json", b"not json")   # a damaged one is left out
        buf.seek(0)
        export.import_archive(self.db, back, buf)
        self.assertEqual(watch_store.wave(self.db, back, k.split(":")[1])["samples"], self.watch["ecg_waves"][k]["samples"])
        self.assertIsNone(watch_store.wave(self.db, back, "0123456789abcdef"))
        p = watch_store.wave_path(self.db, back, k.split(":")[1])
        with open(p, "rb") as f:
            self.assertNotIn(b"samples", f.read(), "sealed")

    def test_the_hook_for_sharing_later(self):
        rows = ecg.shared_rows({**self.watch, "tz": "America/New_York"}, "2026-10-01", "2026-10-31")
        self.assertEqual([(r["when"], r["title"]) for r in rows],
                         [("2026-10-20", "ECG: Inconclusive"), ("2026-10-19", "Irregular rhythm notification"),
                          ("2026-10-03", "ECG: Atrial Fibrillation"), ("2026-10-02", "Irregular rhythm notification")])
        self.assertNotIn("samples", json.dumps(rows))

    def test_keys_are_stable_and_checked(self):
        t = datetime(2026, 9, 12, 8, 15, 4, 250000, tzinfo=OFF)
        self.assertEqual(ecg.key("ecg", t), ecg.key("ecg", t.astimezone(timezone.utc).replace(microsecond=0)))
        self.assertNotEqual(ecg.key("ecg", t), ecg.key("rhythm", t))
        self.assertIsNone(ecg.file_name("rhythm:0123456789abcdef"))
        self.assertIsNone(ecg.file_name("ecg:../../etc/passwd"))
        self.assertEqual(ecg.file_name("ecg:0123456789abcdef"), "0123456789abcdef.json.gz")


if __name__ == "__main__":
    unittest.main()
