#!/usr/bin/env python3
"""Find your provider (#93): the directory is built from the vendors' published lists by tools/build_directory.py
(here, from small fictional samples of each list), shipped as server/directory/providers.json with only what may be
redistributed, and searched by name, location, health system and city or state, with typing mistakes forgiven.
Entries carry what sign-in (#32) needs. Nothing contacts the network: every socket is refused for the whole run.

Fictional data only.

  python3 tests/test_directory.py
"""
import contextlib, gzip, io, json, os, re, shutil, socket, sys, tempfile, unittest, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "directory")
TMP = tempfile.mkdtemp(prefix="sanguity-directory-test-")
LOCAL = os.path.join(TMP, "data", "directory", "providers.json.gz")
os.environ.update(SANGUITY_DATA=os.path.join(TMP, "data"), SANGUITY_RECORDS=os.path.join(TMP, "records"),
                  SANGUITY_BUILD=os.path.join(TMP, "no-build"), SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"),
                  SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"), PASSWORD_MIN_LENGTH="15",
                  SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"))
os.environ.pop("SANGUITY_DIRECTORY", None)
for d in ("records", "keys", "data"):
    os.makedirs(os.path.join(TMP, d), exist_ok=True)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

# ---- no network, from here on: any connection or name lookup beyond this machine is refused and noted, and the
# run fails if there was one (the event loop's own self-pipe, a loopback socket pair on Windows, is allowed)
CONTACTS = []
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_real_socket, _real_getaddrinfo = socket.socket, socket.getaddrinfo


class GuardedSocket(_real_socket):
    def connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and address[0] not in LOOPBACK:
            CONTACTS.append(address)
            raise OSError(f"test_directory: network contact refused ({address!r})")
        return super().connect(address)

    def connect_ex(self, address):
        return self.connect(address)


def _lookup(host, *a, **k):
    if host in LOOPBACK or host is None:
        return _real_getaddrinfo(host, *a, **k)
    CONTACTS.append(host)
    raise OSError(f"test_directory: name lookup refused ({host!r})")


def _no_lookup(host, *a, **k):
    CONTACTS.append(host)
    raise OSError(f"test_directory: name lookup refused ({host!r})")


socket.socket = GuardedSocket
socket.getaddrinfo = _lookup
socket.gethostbyname = socket.gethostbyname_ex = _no_lookup

import logging   # noqa: E402

from fastapi.testclient import TestClient   # noqa: E402

import build_directory as bd   # noqa: E402
from server import accounts, db as dbm, directory, main   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = "harbour lantern pebble crow"
SAMPLES = [f"--from=epic-brands={os.path.join(FIX, 'epic_brands_sample.json')}",
           f"--from=oracle-millennium={os.path.join(FIX, 'oracle_millennium_sample.json')}"]


def build(args):
    with contextlib.redirect_stderr(io.StringIO()):   # what it wrote, said on stderr
        bd.main(args)


def build_local():
    build(SAMPLES + ["--only-files", "--out", LOCAL])


class BuildScript(unittest.TestCase):
    def test_the_guard_refuses_the_network(self):
        before = len(CONTACTS)
        with self.assertRaises(OSError):
            urllib.request.urlopen("https://open.epic.com/Endpoints/Brands", timeout=2)
        self.assertGreater(len(CONTACTS), before)
        del CONTACTS[before:]

    def test_builds_from_the_samples(self):
        out = os.path.join(TMP, "built.json.gz")
        build(SAMPLES + ["--only-files", "--out", out])
        doc = json.loads(gzip.open(out).read())
        by = {e["id"]: e for e in doc["entries"]}
        lake = by["epic:9001-1"]
        self.assertEqual(lake["name"], "Lakeside Regional Health")
        self.assertEqual(lake["fhir_base"], "https://fhir.lakeside.example/api/FHIR/R4/")
        self.assertEqual(lake["org"], "Lakeside Health System, Inc.")
        self.assertIn("Harborview Children's Clinic", lake["aliases"])
        self.assertIn(["Peoria", "IL"], lake["places"], "upper-case cities are written normally")
        self.assertEqual(lake["places"].count(["Springfield", "IL"]), 1, "places are listed once")
        self.assertEqual(lake["logo"], "https://lakeside.example/logo.svg")   # as published; the app never fetches it
        self.assertEqual(lake["portal"], "https://mychart.lakeside.example/")
        self.assertEqual(by["oracle:t1"]["places"], [["Bismarck", "ND"]])
        self.assertNotIn("epic:9004-1", by, "an endpoint that isn't active is left out")
        self.assertNotIn("epic:9005-1", by, "an endpoint without HTTPS is left out")
        for e in doc["entries"]:
            self.assertTrue(e["source"] and e["retrieved"], e["id"])
        self.assertTrue(all(s["included"] for s in doc["sources"]))
        self.assertEqual({s["id"]: s.get("entries") for s in doc["sources"] if s["id"] != "curated"},
                         {"epic-brands": 3, "oracle-millennium": 2})

    def test_bundled_file_is_what_the_script_writes_and_only_what_may_be_shared(self):
        out = os.path.join(TMP, "bundled.json")
        build(["--bundled", "--out", out])
        with open(out, "rb") as a, open(directory.BUNDLED, "rb") as b:
            self.assertEqual(a.read(), b.read(), "run tools/build_directory.py --bundled to update providers.json")
        doc = json.load(open(directory.BUNDLED, encoding="utf-8"))
        srcs = {s["id"]: s for s in doc["sources"]}
        for e in doc["entries"]:
            self.assertTrue(srcs[e["source"]]["redistributable"], e["id"])
        for key in ("epic-brands", "oracle-millennium"):
            self.assertFalse(srcs[key]["included"])
            self.assertIn("build_directory.py", srcs[key]["why"], "the file says how to add what it leaves out")
            self.assertTrue(srcs[key]["terms"].startswith("https://"))
        self.assertLess(os.path.getsize(directory.BUNDLED), 20_000)

    def test_unknown_source_is_refused(self):
        with self.assertRaises(SystemExit):
            build(["--from", "nope=x.json", "--only-files", "--out", os.path.join(TMP, "x.json.gz")])


class Search(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build_local()
        cls.ix = directory.index()

    def names(self, q):
        return [e["name"] for e in self.ix.search(q)]

    def test_by_name(self):
        self.assertEqual(self.names("Lakeside Regional")[0], "Lakeside Regional Health")
        self.assertEqual(self.names("northwind")[0], "Northwind Medical Center")
        self.assertEqual(self.names("north")[0], "Northwind Medical Center", "the start of a word is enough")

    def test_by_a_location_or_health_system(self):
        self.assertEqual(self.names("harborview children"), ["Lakeside Regional Health"])
        self.assertEqual(self.names("Lakeside Health System")[0], "Lakeside Regional Health")

    def test_by_city_and_state(self):
        self.assertEqual(self.names("Duluth"), ["Northwind Medical Center"])
        self.assertEqual(self.names("bismarck"), ["PRAIRIE VALLEY HOSPITAL"])
        self.assertEqual(self.names("Riverton WY"), ["Lakeside Regional Health"])
        self.assertEqual(self.names("hospital new york"), ["Saint Brendan Hospital"], "a state by its name")
        self.assertIn("Saint Brendan Hospital", self.names("NY"))

    def test_by_a_misspelling(self):
        self.assertEqual(self.names("Nortwind")[0], "Northwind Medical Center")       # a letter missing
        self.assertEqual(self.names("Lakesdie")[0], "Lakeside Regional Health")       # two letters swapped
        self.assertEqual(self.names("Prarie Valey")[0], "PRAIRIE VALLEY HOSPITAL")     # one in each word
        self.assertEqual(self.names("Saint Brendam Hosptial")[0], "Saint Brendan Hospital")
        self.assertEqual(self.names("Junipr Foot"), ["Juniper Foot & Ankle, PC"])
        self.assertEqual(self.names("Juniper Fot"), [], "words under four letters must be right, or the start of one")
        self.assertEqual(self.names("zzzzqqq"), [])

    def test_sandboxes_and_medicare_are_always_there(self):
        self.assertIn("Epic on FHIR sandbox (test patients)", self.names("epic sandbox"))
        self.assertEqual(self.names("medicare")[0], "Medicare (Blue Button 2.0)")

    def test_entries_have_what_sign_in_needs(self):
        for e in self.ix.entries:
            self.assertTrue(e["id"] and e["name"], e)
            self.assertTrue(e["fhir_base"].startswith("https://") and e["fhir_base"].endswith("/"), e["id"])
            v = directory.VENDORS[e["vendor"]]
            for k in ("name", "developer_site", "docs", "scopes", "client", "note", "realistic"):
                self.assertTrue(v[k], (e["vendor"], k))
            self.assertNotIn("steps", v)   # one copy of each vendor's setup steps: connections.GUIDES, on Admin (#166)
            self.assertIsInstance(v["needs_secret"], bool)
        self.assertEqual(directory.VENDORS["oracle"]["realistic"], "unlikely")

    def test_odd_queries(self):
        self.assertEqual(self.ix.search(""), [])
        self.assertEqual(self.ix.search("!!! ... the of"), [])
        self.ix.search("é" * 5000)   # long, and not ASCII: no error
        self.assertLessEqual(len(self.ix.search("a")), directory.MAX_RESULTS)


class LocalCopy(unittest.TestCase):
    def tearDown(self):
        build_local()

    def test_new_copy_is_read_without_a_restart(self):
        build_local()
        self.assertIn("epic:9001-1", directory.index().by_id)
        os.remove(LOCAL)
        ix = directory.index()
        self.assertNotIn("epic:9001-1", ix.by_id, "without the install's copy, only the bundled list")
        self.assertIn("epic:sandbox", ix.by_id)

    def test_a_broken_copy_falls_back_to_the_bundled_list(self):
        with gzip.open(LOCAL, "wb") as f:
            f.write(b"{not json")
        ix = directory.index()
        self.assertIn("cms:bluebutton", ix.by_id)
        self.assertNotIn("epic:9001-1", ix.by_id)

    def test_entries_without_a_vendor_or_https_are_skipped(self):
        doc = {"format": 1, "entries": [{"id": "x:1", "name": "Odd", "vendor": "elsewhere", "fhir_base": "https://x.example/"},
                                        {"id": "epic:2", "name": "Plain", "vendor": "epic", "fhir_base": "http://x.example/"}]}
        with gzip.open(LOCAL, "wb") as f:
            f.write(json.dumps(doc).encode())
        ix = directory.index()
        self.assertNotIn("x:1", ix.by_id)
        self.assertNotIn("epic:2", ix.by_id)
        self.assertIn("epic:sandbox", ix.by_id, "bundled entries are added to the install's copy")


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build_local()
        cls.ctx = TestClient(main.app, base_url="http://testserver").__enter__()
        db = dbm.connect()
        accounts.create(db, "robin", password=PW)
        db.close()
        cls.c = TestClient(main.app, base_url="http://testserver")
        r = cls.c.post("/api/login", json={"username": "robin", "password": PW}, headers=H)
        assert r.status_code == 200, r.text

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

    def test_signed_in_only(self):
        anon = TestClient(main.app, base_url="http://testserver")
        self.assertEqual(anon.get("/api/directory?q=lakeside").status_code, 401)
        self.assertEqual(anon.get("/api/directory/entry?id=epic:sandbox").status_code, 401)

    def test_search(self):
        r = self.c.get("/api/directory", params={"q": "harborview"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["cache-control"], "no-store")
        j = r.json()
        hit = j["results"][0]
        self.assertEqual((hit["id"], hit["vendor"], hit["fhir_base"]), ("epic:9001-1", "epic", "https://fhir.lakeside.example/api/FHIR/R4/"))
        self.assertEqual(hit["matched"], "Harborview Children's Clinic", "says which location matched")
        self.assertEqual(hit["place"], "Springfield, IL")
        self.assertIn("logo", hit)
        self.assertEqual(set(j["vendors"]), {"epic", "oracle", "cms-bluebutton"})
        self.assertTrue(all(s["included"] for s in j["sources"]))
        self.assertEqual(self.c.get("/api/directory", params={"q": "x" * 201}).status_code, 422)

    def test_entry_and_return_addresses(self):
        j = self.c.get("/api/directory/entry", params={"id": "oracle:t1"}).json()
        self.assertEqual(j["entry"]["fhir_base"], "https://fhir-myrecord.example/r4/t1/")
        self.assertEqual(j["vendors"]["oracle"]["realistic_label"], "Unlikely for a home install")
        back = j["return_addresses"]
        self.assertEqual(back[0]["url"], "http://testserver/connect/return")
        self.assertTrue(back[0]["current"])
        self.assertFalse(back[0]["likely_accepted"], "plain HTTP, and a name with no dot")
        j = self.c.get("/api/directory/entry", params={"id": "epic:sandbox"},
                       headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "health.example.com"}).json()
        self.assertEqual(j["return_addresses"][0], {"url": "https://health.example.com/connect/return", "current": True,
                                                    "likely_accepted": True, "why": None})
        j = self.c.get("/api/directory/entry", params={"id": "epic:sandbox"},
                       headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "sanguity.tail1234.ts.net"}).json()
        self.assertFalse(j["return_addresses"][0]["likely_accepted"])
        self.assertEqual(self.c.get("/api/directory/entry", params={"id": "epic:nope"}).status_code, 404)

    def test_page_is_served_and_listed(self):
        with open(os.path.join(ROOT, "nginx.conf"), encoding="utf-8") as f:   # among the pages nginx serves
            pages = re.search(r"location ~ \^/\(([a-z|]+)\)\\\.html\$", f.read())
        self.assertIn("providers", pages.group(1).split("|"))   # its old address, which opens the Sources page (#166)
        self.assertIn("sources", pages.group(1).split("|"))
        with open(os.path.join(ROOT, "www", "sources.html"), encoding="utf-8") as f:   # in the connect step there
            page = f.read()
        self.assertIn('id="finder"', page)
        self.assertIn('src="js/providers.js"', page)
        for name in ("sources.html", "providers.html"):
            with open(os.path.join(ROOT, "www", name), encoding="utf-8") as f:
                page = f.read()
            self.assertNotIn("<script>", page, "no inline script")
            self.assertNotRegex(page, r"\son[a-z]+=", "no inline handlers")


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)
    assert not CONTACTS, f"the network was contacted: {CONTACTS}"


if __name__ == "__main__":
    unittest.main(verbosity=2)
