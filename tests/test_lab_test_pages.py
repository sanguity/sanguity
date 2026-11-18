#!/usr/bin/env python3
""""What is this test?" links (#163, lab-test-pages.json, server/lab_pages.py): the table as reviewed (every address
https on an allowed host, with nothing but a path: no query, fragment, port or user name, so no value, date or anything
about the person can go with a link; each lab a LOINC code with a correct check digit or a lab-aliases.json name; no lab
or spelling on two pages), what it covers (every lab in the demo, every lab the starter conditions follow, the
fixtures' common tests, every lab-aliases.json name but the few listed without a page), what the checks catch, and the
route the records page reads it from (anyone signed in, nothing of anyone's, never cached). Fictional data only.

  python3 tests/test_lab_test_pages.py
"""
import copy, json, os, subprocess, sys, tempfile, unittest, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-lab-pages-test-")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_CORRECTIONS=os.path.join(TMP, "config", "corrections.json"))
for d in ("records", "keys", "build", "config", "profiles"):
    os.makedirs(os.path.join(TMP, d), exist_ok=True)
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, db as dbm, lab_pages, main, presets, profiles as prof   # noqa: E402
from server.importers import common   # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
with open(os.path.join(ROOT, "lab-test-pages.json"), encoding="utf-8") as f:
    DOC = json.load(f)
# lab-aliases.json's names with no page: MedlinePlus has none about these as tests of their own (a blood differential's
# page doesn't name them), so they get no link rather than a page about something else
NO_PAGE = {"Immature Granulocytes %", "Immature Granulocytes Absolute", "Metamyelocytes Relative", "Myelocytes Relative", "NRBC"}


def page_of(name, code=None):
    """The page the records page would link, as test-pages.js chooses it: by code, then by name."""
    ix = lab_pages.index()
    n = ix["codes"].get(code or "")
    if n is None:
        n = ix["names"].get(name.strip().lower())
    return ix["pages"][n]["url"] if n is not None else None


class Table(unittest.TestCase):
    """The file as reviewed: what a contributor's new page must pass."""

    def test_the_file_passes_its_checks(self):
        self.assertEqual(lab_pages.check(DOC), [])
        self.assertEqual(lab_pages.load()["pages"], DOC["pages"])

    def test_every_address_is_https_on_an_allowed_host_with_nothing_but_a_path(self):
        self.assertEqual(lab_pages.HOSTS, ("medlineplus.gov",), "a new host is a reviewed decision: say why in lab_pages.py")
        self.assertEqual(set(DOC["sites"]), {"medlineplus.gov"})
        for p in DOC["pages"]:
            u = urllib.parse.urlsplit(p["url"])
            self.assertEqual((u.scheme, u.netloc, u.query, u.fragment), ("https", "medlineplus.gov", "", ""), p["url"])
            self.assertRegex(u.path, r"^/(lab-tests/[a-z0-9-]+/|ency/article/\d{6}\.htm)$", "a lab test page, or an encyclopedia article")
        lab_test = [p for p in DOC["pages"] if "/lab-tests/" in p["url"]]
        self.assertGreater(len(lab_test), len(DOC["pages"]) * 0.9, "MedlinePlus's lab test pages first")

    def test_what_the_route_gives_is_the_table_alone(self):
        ix = lab_pages.index()
        self.assertEqual(set(ix), {"pages", "codes", "names"})
        self.assertEqual([p["url"] for p in ix["pages"]], [p["url"] for p in DOC["pages"]])
        self.assertTrue(all(set(p) == {"url", "title", "site"} and p["site"] == "MedlinePlus" for p in ix["pages"]))
        self.assertTrue(all(lab_pages.url_ok(p["url"]) for p in ix["pages"]))

    def test_the_demo_labs_each_have_a_page(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run([sys.executable, "-I", os.path.join(ROOT, "demo", "generate.py"), d, "--end", "2026-11-15"],
                           check=True, stdout=subprocess.DEVNULL)
            with open(os.path.join(d, "data.json"), encoding="utf-8") as f:
                labs = json.load(f)["labs"]
        self.assertTrue(labs)
        missing = [s["name"] for s in labs if not page_of(s["name"], s.get("loinc"))]
        self.assertEqual(missing, [])
        self.assertEqual(page_of("Lymphocytes Absolute"), "https://medlineplus.gov/lab-tests/blood-differential/")

    def test_the_starter_conditions_labs_each_have_a_page(self):
        # the starter set (#186); a condition added later is best given pages for its labs too, but its labs without
        # one simply have no link, so they're listed here, not failed
        doc = presets.load()
        starter = {"type-2-diabetes", "chronic-kidney-disease", "hypertension", "heart-failure", "thyroid-disease",
                   "high-cholesterol", "liver-disease", "anemia", "cancer-treatment"}
        self.assertLessEqual(starter, {c["id"] for c in doc["conditions"]})
        codes = {c for cond in doc["conditions"] if cond["id"] in starter for c in cond["labs"]}
        self.assertEqual(sorted(c for c in codes if c not in lab_pages.index()["codes"]), [])
        later = sorted({c for cond in doc["conditions"] for c in cond["labs"]} - set(lab_pages.index()["codes"]))
        if later:
            print(f"\n  later conditions' labs with no page (no link): {', '.join(later)}", file=sys.stderr)
        # and each by its spellings too, for a series that has no code
        for code in codes:
            d = presets.lab(doc, code)
            for s in d["spellings"]:
                self.assertEqual(page_of(s), page_of("", code), (code, s))

    def test_lab_aliases_names_each_have_a_page_but_the_few_listed(self):
        missing = {n for n, d in common.lab_defs().items() if not page_of(n, d.get("loinc"))}
        self.assertEqual(missing, NO_PAGE)
        self.assertEqual(page_of("UREA NITROGEN (BUN)"), "https://medlineplus.gov/lab-tests/bun-blood-urea-nitrogen/", "an alias")

    def test_the_fixtures_common_tests(self):
        # names and codes the importers' fictional fixtures give their series
        for name, code, url in [
                ("Hemoglobin A1c", None, "lab-tests/hemoglobin-a1c-hba1c-test/"), ("HEMOGLOBIN A1c", None, "lab-tests/hemoglobin-a1c-hba1c-test/"),
                ("LDL Chol Calc (NIH)", None, "lab-tests/cholesterol-levels/"), ("CHOLESTEROL, TOTAL", None, "lab-tests/cholesterol-levels/"),
                ("TROPONIN I", "10839-9", "lab-tests/troponin-test/"), ("Vitamin D, 25-Hydroxy", None, "lab-tests/vitamin-d-test/"),
                ("Hep C Virus Ab", None, "lab-tests/hepatitis-testing/"), ("eGFR", None, "lab-tests/estimated-glomerular-filtration-rate-egfr-and-gfr-tests/"),
                ("Color of Urine", "5778-6", "ency/article/003579.htm"), ("Glucose, Blood (mmol/L)", "2345-7", "lab-tests/blood-glucose-test/"),
                ("Some lab", "718-7", "lab-tests/hemoglobin-test/")]:
            self.assertEqual(page_of(name, code), "https://medlineplus.gov/" + url, name)
        # no match, no link: a vital sign, a test no page is about, a name no file knows
        for name, code in [("Blood pressure, systolic", "8480-6"), ("Height", "8302-2"), ("NRBC", None), ("Cellularity", None), ("BMI", None)]:
            self.assertIsNone(page_of(name, code), name)

    def test_what_the_checks_catch(self):
        def bad(change):
            d = copy.deepcopy(DOC)
            change(d)
            return " ".join(lab_pages.check(d))
        p0 = lambda d: d["pages"][0]   # noqa: E731  (Hemoglobin Test)
        for url in ["http://medlineplus.gov/lab-tests/hemoglobin-test/", "https://example.com/lab-tests/hemoglobin-test/",
                    "https://medlineplus.gov.example.com/x/", "https://medlineplus.gov/lab-tests/hemoglobin-test/?value=14.2",
                    "https://medlineplus.gov/lab-tests/hemoglobin-test/#2026-09-10", "https://medlineplus.gov:8443/lab-tests/hemoglobin-test/",
                    "https://someone@medlineplus.gov/lab-tests/hemoglobin-test/", "https://medlineplus.gov/lab tests/", "https://medlineplus.gov",
                    "javascript:alert(1)", "//medlineplus.gov/lab-tests/hemoglobin-test/"]:
            self.assertIn("must be https on medlineplus.gov", bad(lambda d: p0(d).update(url=url)), url)
        self.assertIn("sites must name", bad(lambda d: d["sites"].update({"example.com": "Example"})))
        self.assertIn("needs a name in sites", bad(lambda d: d["sites"].clear()))
        self.assertIn("listed twice", bad(lambda d: d["pages"][1].update(url=p0(d)["url"])))
        self.assertIn("needs the page's title", bad(lambda d: p0(d).update(title="")))
        self.assertIn("unknown keys", bad(lambda d: p0(d).update(note="x")))
        self.assertIn("isn't a LOINC code", bad(lambda d: p0(d)["labs"].append("718-8")), "a wrong check digit")
        self.assertIn("isn't a LOINC code", bad(lambda d: p0(d)["labs"].append("Hgb")), "a name lab-aliases.json doesn't know")
        self.assertIn("by its code, 718-7", bad(lambda d: p0(d).update(labs=["Hemoglobin"])), "a known name with a code")
        self.assertIn("also on", bad(lambda d: d["pages"][1]["labs"].append("718-7")), "one lab, two pages")
        self.assertIn("another test", bad(lambda d: p0(d).update(aka=["HCT"])), "a spelling of another test")
        self.assertIn("also spells a lab", bad(lambda d: d["pages"][1].update(aka=["HGB"])), "one spelling, two pages")
        self.assertIn("labs must be a list", bad(lambda d: p0(d).update(labs=[], aka=[])), "a page about no lab")
        self.assertIn("_review", bad(lambda d: d.pop("_review")))


class Route(unittest.TestCase):
    """GET /api/lab-test-pages: anyone signed in; the app's own table, the same for everyone; never cached."""

    @classmethod
    def setUpClass(cls):
        dbm.init()
        cls.ctx = TestClient(main.app, base_url="http://testserver").__enter__()
        db = dbm.connect()
        pw = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites"}
        uid = {n: accounts.create(db, n, password=p)[0] for n, p in pw.items()}
        prof.create_profile(db, "Robin (fictional)", owner_id=uid["robin"])
        db.close()
        cls.c = {}
        for n, p in pw.items():
            c = TestClient(main.app, base_url="http://testserver")
            assert c.post("/api/login", json={"username": n, "password": p}, headers=H).status_code == 200
            cls.c[n] = c

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

    def test_who_may_ask_and_what_they_get(self):
        self.assertEqual(TestClient(main.app, base_url="http://testserver").get("/api/lab-test-pages").status_code, 401)
        a, b = self.c["robin"].get("/api/lab-test-pages"), self.c["kim"].get("/api/lab-test-pages")
        self.assertEqual((a.status_code, b.status_code), (200, 200))
        self.assertEqual(a.json(), b.json(), "the same table for someone with a profile and someone with none")
        self.assertEqual(a.json(), json.loads(json.dumps(lab_pages.index())))
        self.assertIn("no-store", a.headers.get("cache-control", ""))
        self.assertNotIn("Robin", a.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
