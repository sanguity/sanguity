#!/usr/bin/env python3
"""Dashboard presets by condition (#186, condition-presets.json, server/presets.py): the definitions file (every lab a
LOINC code with a correct check digit that lab-aliases.json or the file itself names, no lab twice, no spelling for
two tests, a published source for every condition, home readings that exist), what the Settings page is offered for
a fictional profile (labs matched to its own series by code, name or spelling; the problem list's suggestion, never
from an allergy or a resolved problem; adult-only labs left out for a child), who may ask (owners and editors), and
"Measures ticked at first" in the visit summary. Fictional data only.

  python3 tests/test_condition_presets.py
"""
import copy, datetime, json, os, re, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-presets-test-")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_CORRECTIONS=os.path.join(TMP, "config", "corrections.json"))
for d in ("records", "keys", "build", "config", "profiles"):
    os.makedirs(os.path.join(TMP, d), exist_ok=True)
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, db as dbm, main, measures, presets, profiles as prof, records, settings  # noqa: E402
from server.importers import common   # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites", "lee": "maple tunnel brisk saddle"}
TODAY = datetime.date.today()
with open(os.path.join(ROOT, "condition-presets.json"), encoding="utf-8") as f:
    DOC = json.load(f)


def day(n):
    return (TODAY + datetime.timedelta(days=n)).isoformat() + "T09:00:00"


def series(name, unit="", **kw):
    return {"name": name, "unit": unit, "panel": "", "n": 2, "txt": [], **kw,
            "pts": [[day(-90), 1.0, None, None, "", "1"], [day(-30), 1.1, None, None, "", "1.1"]]}


# a fictional record: creatinine by its canonical name, an A1c by its LOINC code under the lab's own spelling, a
# "UACR" by a spelling only, potassium in a second unit's series too (the one with more results wins), a watch's
# series that is never a lab tile; type 2 diabetes in the problem list, an allergy that mentions CKD and a resolved
# anemia
DATA = {"built": day(0), "source": "fictional", "events": [], "details": {}, "costs": [], "upcoming": [],
        "labs": [series("Creatinine", "mg/dL"), series("GLYCOHEMOGLOBIN (A1C), HPLC", "%", loinc="4548-4"),
                 series("UACR", "mg/g"), {**series("Potassium, Blood", "mmol/L"), "pts": series("x")["pts"] * 2},
                 series("Potassium, Blood (mEq/L)", "mEq/L", loinc="2823-3"), series("Glucose, Blood", "mg/dL", watch=True)],
        "problems": [{"name": "Type 2 diabetes mellitus without complications", "type": "Diagnosis", "status": "Active"},
                     {"name": "Allergy: CKD contrast (rash)", "type": "Allergy", "status": "Active"},
                     {"name": "Anemia", "type": "Diagnosis", "status": "Resolved"},
                     {"name": "Pulmonary hypertension", "type": "Diagnosis", "status": "Active"}]}


class Definitions(unittest.TestCase):
    """The file as reviewed: tests/test_condition_presets.py is what a contributor's new condition must pass."""

    def test_the_file_passes_its_checks(self):
        self.assertEqual(presets.check(DOC), [])
        self.assertEqual(presets.load()["conditions"], DOC["conditions"])

    def test_starter_set(self):
        ids = [c["id"] for c in DOC["conditions"]]
        for want in ("type-2-diabetes", "chronic-kidney-disease", "hypertension", "heart-failure", "thyroid-disease",
                     "high-cholesterol", "liver-disease", "anemia", "cancer-treatment"):
            self.assertIn(want, ids)
        self.assertTrue(8 <= len(ids) <= 200)
        for c in DOC["conditions"]:
            self.assertTrue(c["sources"], c["id"])
            self.assertFalse(c["reviewed"], f"{c['id']}: no clinician has reviewed it yet; say so until one has")
        self.assertIn("clinician", DOC["_review"])

    def test_every_lab_is_a_known_loinc_code(self):
        for code in [c for c in common._by_loinc()]:
            self.assertTrue(presets.loinc_ok(code), f"lab-aliases.json's {code}")
        self.assertTrue(presets.loinc_ok("718-7") and presets.loinc_ok("98979-8"))
        self.assertFalse(presets.loinc_ok("718-6") or presets.loinc_ok("7187") or presets.loinc_ok("abc-1"), "a wrong check digit, no dash")
        for c in DOC["conditions"]:
            for code in c["labs"]:
                self.assertTrue(presets.loinc_ok(code), f"{c['id']}: {code}")
                self.assertTrue(code in common._by_loinc() or code in DOC["labs"], f"{c['id']}: {code} is named somewhere")
            self.assertEqual(len(set(c["labs"])), len(c["labs"]), f"{c['id']}: no lab twice")

    def test_the_fuller_list(self):
        """#187: the issue's groups each have conditions; every code in the file's own labs table is used, and every
        code in every definition is known (form, check digit, named by lab-aliases.json or the table)."""
        ids = {c["id"] for c in DOC["conditions"]}
        for want in ("type-1-diabetes", "prediabetes", "gestational-diabetes", "hypothyroidism", "hyperthyroidism", "gout",
                     "atrial-fibrillation", "coronary-artery-disease", "familial-hypercholesterolemia", "dialysis", "kidney-transplant",
                     "hepatitis-b", "hepatitis-c", "cirrhosis", "celiac-disease", "crohns-disease", "ulcerative-colitis",
                     "sickle-cell-disease", "hemochromatosis", "multiple-myeloma", "prostate-cancer", "colorectal-cancer",
                     "rheumatoid-arthritis", "lupus", "hiv", "pulmonary-hypertension", "epilepsy-drug-levels", "pregnancy",
                     "testosterone-therapy", "eating-disorders", "alcohol-use-disorder", "warfarin", "lithium", "clozapine",
                     "dmard-monitoring", "isotretinoin", "amiodarone", "statin"):
            self.assertIn(want, ids)
        used = {code for c in DOC["conditions"] for code in c["labs"]}
        for sys_ in DOC["systems"]:
            self.assertTrue(any(c["system"] == sys_["id"] for c in DOC["conditions"]), f"{sys_['id']} has a condition")
        for code in DOC["labs"]:
            if code != "_about":
                self.assertTrue(presets.loinc_ok(code), code)
                self.assertIn(code, used, f"labs: {code} is listed but no condition uses it")
        for code in used:
            self.assertTrue(presets.loinc_ok(code) and presets.lab(DOC, code), f"{code} is a known LOINC code")
        self.assertTrue(presets.lab(DOC, "2857-1")["adult"], "PSA: an adult's measure")

    def test_wording_names_no_dose_or_advice(self):
        """Medicine entries name what's commonly monitored, never a dose; nothing tells anyone what to do."""
        dose = re.compile(r"\b\d+(\.\d+)?\s*(mg|mcg|µg|g|ml|units?|iu|mmol)\b", re.I)
        advice = re.compile(r"\b(should|must|recommend|you need|get tested)\b", re.I)
        for c in DOC["conditions"]:
            text = " ".join([c["name"], *c["synonyms"]])
            self.assertFalse(dose.search(text), f"{c['id']}: no dose")
            self.assertFalse(advice.search(text), f"{c['id']}: no advice")
        self.assertIn("never a dose", DOC["_about"])

    def test_suggestions_for_the_fuller_list(self):
        """Look-alike problems don't suggest the wrong preset (the "except" words, #187)."""
        def ids(name, **kw):
            return [x["id"] for x in presets.suggestions(DOC, [{"name": name, "type": "Diagnosis", **kw}])]
        self.assertEqual(ids("Hashimoto's thyroiditis"), ["hypothyroidism"])
        self.assertEqual(ids("Toxic multinodular goiter"), ["hyperthyroidism"])
        self.assertEqual(ids("Sickle cell trait"), [])
        self.assertEqual(ids("Sickle beta thalassemia"), ["sickle-cell-disease"])
        self.assertEqual(ids("Lupus anticoagulant positive"), [])
        self.assertEqual(ids("Hearing aids"), [])
        self.assertEqual(ids("Hepatitis B vaccination"), [])
        self.assertEqual(ids("Gestational hypertension"), ["preeclampsia"])
        self.assertEqual(ids("Medullary thyroid cancer"), ["cancer-treatment"])
        self.assertEqual(ids("Smoldering multiple myeloma"), ["mgus"])
        self.assertEqual(ids("Primary biliary cirrhosis"), ["primary-biliary-cholangitis"])
        self.assertEqual(ids("Statin intolerance"), [])
        self.assertEqual(ids("Latent tuberculosis"), [])
        self.assertEqual(ids("HIV pre-exposure prophylaxis (PrEP)"), ["hiv-prep"])
        self.assertEqual(ids("Familial hypercholesterolemia"), ["familial-hypercholesterolemia"])
        self.assertEqual(ids("End stage renal disease (ESRD)"), ["dialysis"])

    def test_home_readings_and_summary_ticks_exist(self):
        for h, (label, tile, sec) in presets.HOME.items():
            self.assertTrue(tile is None or tile in settings.HOME_TILES, h)
            self.assertIn(sec, measures.IDS, h)
        self.assertEqual(settings.SUMMARY_TICKS, measures.LABELS, "Settings → Visit summary offers measures.py's sections")

    def test_what_the_checks_catch(self):
        def bad(change):
            d = copy.deepcopy(DOC)
            change(d)
            return " ".join(presets.check(d))
        c0 = lambda d: d["conditions"][0]
        self.assertIn("check digit", bad(lambda d: c0(d)["labs"].append("4548-5")))
        self.assertIn("doesn't know 1234-4", bad(lambda d: c0(d)["labs"].append("1234-4")))
        self.assertIn("twice", bad(lambda d: c0(d)["labs"].append(c0(d)["labs"][0])))
        self.assertIn("a source", bad(lambda d: c0(d).update(sources=[])))
        self.assertIn("https", bad(lambda d: c0(d)["sources"][0].update(url="http://example.test")))
        self.assertIn("year", bad(lambda d: c0(d)["sources"][0].pop("year")))
        self.assertIn("used twice", bad(lambda d: d["conditions"].append(copy.deepcopy(c0(d)))))
        self.assertIn("synonym", bad(lambda d: d["conditions"][1]["synonyms"].append("T2D")))
        self.assertIn("system", bad(lambda d: c0(d).update(system="lungs")))
        self.assertIn("home", bad(lambda d: c0(d).update(home=["steps"])))
        self.assertIn("reviewed", bad(lambda d: c0(d).pop("reviewed")))
        self.assertIn("unknown keys", bad(lambda d: c0(d).update(note="x")))
        self.assertIn("not Hgb", bad(lambda d: d["labs"].update({"718-7": {"name": "Hgb"}})), "a code lab-aliases.json knows keeps its name")
        self.assertIn("another test", bad(lambda d: d["labs"]["4548-4"]["aka"].append("HGB")), "a spelling of another test")
        self.assertIn("both", bad(lambda d: d["labs"]["2093-3"]["aka"].append("HbA1c")), "one spelling, two codes")
        self.assertIn("both Total Cholesterol", bad(lambda d: (d["labs"].update({"14647-2": {"name": "Total Cholesterol"}}),
                                                                c0(d)["labs"].append("14647-2"))), "two codes, one name, in one condition")


class Offer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dbm.init()
        cls.ctx = TestClient(main.app, base_url="http://testserver").__enter__()
        db = dbm.connect()
        cls.uid = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        cls.pid = prof.create_profile(db, "Robin (fictional)", owner_id=cls.uid["robin"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (cls.uid["kim"], cls.pid))
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (cls.uid["lee"], cls.pid))
        with dbm.transaction(db):
            records.store(db, cls.pid, DATA, "upload", fmt="fhir")
        db.close()
        cls.c = {}
        for n in PW:
            c = TestClient(main.app, base_url="http://testserver")
            assert c.post("/api/login", json={"username": n, "password": PW[n]}, headers=H).status_code == 200
            cls.c[n] = c

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

    def offer(self, who="lee"):
        r = self.c[who].get(f"/api/profiles/{self.pid}/condition-presets")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_1_who_may_ask(self):
        self.assertEqual(self.c["kim"].get(f"/api/profiles/{self.pid}/condition-presets").status_code, 403, "a viewer changes no settings")
        self.offer("robin")
        self.assertEqual(self.c["lee"].get("/api/profiles/999/condition-presets").status_code, 404)

    def test_2_labs_matched_to_this_profiles_series(self):
        o = self.offer()
        self.assertFalse(o["child"])
        self.assertEqual([s["id"] for s in o["systems"]], [s["id"] for s in DOC["systems"]])
        t2 = next(c for c in o["conditions"] if c["id"] == "type-2-diabetes")
        got = {x["name"]: x["series"] for x in t2["labs"]}
        self.assertEqual(got["Hemoglobin A1c"], "GLYCOHEMOGLOBIN (A1C), HPLC", "by its LOINC code, under the lab's own name")
        self.assertEqual(got["Urine Albumin/Creatinine Ratio"], "UACR", "by a spelling the file gives")
        self.assertEqual(got["Creatinine"], "Creatinine", "by lab-aliases.json's name")
        self.assertEqual(got["Potassium, Blood"], "Potassium, Blood", "two series fit: the one with more results")
        self.assertIsNone(got["Glucose, Blood"], "a watch's series is never a lab tile: no results here")
        self.assertIsNone(got["CKD-EPI eGFR"])
        self.assertEqual([x["name"] for x in t2["labs"]][:3], ["Hemoglobin A1c", "Glucose, Blood", "Urine Albumin/Creatinine Ratio"], "in the file's order")
        self.assertEqual(t2["adultOnly"], [])
        self.assertEqual([(h["id"], h["tile"], h["section"]) for h in t2["home"]],
                         [("glucose", "glucose", "glucose"), ("bp", "bp", "home_bp"), ("weight", None, "bmi")])
        self.assertTrue(t2["sources"][0]["url"].startswith("https://"))

    def test_3_suggested_from_problems_only(self):
        o = self.offer()
        self.assertEqual(o["suggest"], [{"id": "type-2-diabetes", "problem": "Type 2 diabetes mellitus without complications"},
                                        {"id": "pulmonary-hypertension", "problem": "Pulmonary hypertension"}],
                         "not from the allergy (CKD) or the resolved anemia; pulmonary hypertension its own preset (#187), not high blood pressure's")
        s = presets.suggestions(DOC, [{"name": "Essential hypertension (HTN)", "status": "active"},
                                      {"name": "Hodgkin lymphoma, in remission", "type": "Diagnosis"},
                                      {"name": "Lymphomatoid papulosis", "type": "Diagnosis"}])
        self.assertEqual([x["id"] for x in s], ["hypertension", "cancer-treatment"], "whole words only: lymphomatoid isn't lymphoma")

    def test_4_a_childs_profile_leaves_adult_measures_out(self):
        r = self.c["robin"].put(f"/api/profiles/{self.pid}/person", json={"birthDate": (TODAY.replace(year=TODAY.year - 9) - datetime.timedelta(days=3)).isoformat()}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        try:
            o = self.offer()
            self.assertTrue(o["child"])
            ckd = next(c for c in o["conditions"] if c["id"] == "chronic-kidney-disease")
            self.assertEqual(ckd["adultOnly"], ["CKD-EPI eGFR"])
            self.assertNotIn("CKD-EPI eGFR", [x["name"] for x in ckd["labs"]])
        finally:
            self.c["robin"].put(f"/api/profiles/{self.pid}/person", json={"birthDate": "1980-04-30"}, headers=H)
        self.assertFalse(self.offer()["child"])

    def test_5_measures_ticked_at_first_in_the_visit_summary(self):
        url = f"/api/profiles/{self.pid}/summary"
        off = lambda: {s["id"]: s.get("off", False) for s in self.c["kim"].get(url).json()["sections"]}
        self.assertEqual(self.c["kim"].put(f"/api/profiles/{self.pid}/settings/summary.ticked", json={"value": ["bmi"]}, headers=H).status_code,
                         403, "a viewer can't")
        r = self.c["lee"].put(f"/api/profiles/{self.pid}/settings/summary.ticked", json={"value": ["bmi", "home_bp", "glucose"]}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["summary"]["ticked"], ["home_bp", "glucose", "bmi"], "in the summary's order")
        self.assertEqual(self.c["lee"].put(f"/api/profiles/{self.pid}/settings/summary.ticked", json={"value": ["steps"]}, headers=H).status_code, 400)
        self.assertNotIn("home_bp", off(), "only offered when the profile has some, ticked or not")
        was = measures.offered
        measures.offered = lambda c: list(measures.IDS)   # as if the phone had sent every measure
        try:
            got = off()
        finally:
            measures.offered = was
        self.assertEqual({k for k in measures.IDS if not got[k]}, {"home_bp", "glucose", "bmi"}, "the three ticked at first, the rest not")
        self.assertTrue(all(v is False for k, v in got.items() if k not in measures.IDS), "the record sections as before")
        r = self.c["lee"].post(f"/api/profiles/{self.pid}/settings/reset", json={"section": "summary"}, headers=H)
        self.assertEqual(r.json()["reset"], ["summary.ticked"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
