#!/usr/bin/env python3
"""Find your provider (#93): the directory is built from the vendors' published lists by tools/build_directory.py
(here, from small fictional samples of each list), shipped as server/directory/providers.json with only what may be
redistributed, and searched by name, location, health system and city or state, with typing mistakes forgiven.
Entries carry what sign-in (#32) needs. Nothing contacts the network: every socket is refused for the whole run.

Fictional data only.

  python3 tests/test_directory.py
"""
import contextlib, gzip, io, json, os, re, shutil, socket, sys, tempfile, time, unittest, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "directory")
TMP = tempfile.mkdtemp(prefix="sanguity-directory-test-")
LOCAL = os.path.join(TMP, "data", "directory", "providers.json.gz")
os.environ["CONNECTED_SOURCES"] = "1"   # connected sources on, as before #1096 made them a switch, off by default
os.environ.update(SANGUITY_DATA=os.path.join(TMP, "data"), SANGUITY_RECORDS=os.path.join(TMP, "records"),
                  SANGUITY_BUILD=os.path.join(TMP, "no-build"), SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"),
                  SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"), PASSWORD_MIN_LENGTH="15",
                  SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"))
os.environ.pop("SANGUITY_DIRECTORY", None)
for d in ("records", "keys", "data"):
    os.makedirs(os.path.join(TMP, d), exist_ok=True)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)

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
from server import accounts, crypto, db as dbm, directory, main, outside, settings, twofactor   # noqa: E402
from server.directory import update   # noqa: E402

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


class FromThePage(unittest.TestCase):
    """Settings → Install → Provider directory (#992): the server reads Epic's and Oracle Health's lists itself, from a
    fake of each list's host (tests/directory_host.py), when an admin asks, and weekly once that's turned on."""

    @classmethod
    def setUpClass(cls):
        from tests.directory_host import DirectoryHost
        cls.host = DirectoryHost().start().use()
        cls.ctx = TestClient(main.app, base_url="http://testserver").__enter__()
        db = dbm.connect()
        if not db.execute("SELECT 1 FROM users WHERE username='robin'").fetchone():
            accounts.create(db, "robin", password=PW)
        db.execute("UPDATE users SET is_admin=1 WHERE username='robin'")
        if not db.execute("SELECT 1 FROM users WHERE username='kim'").fetchone():
            accounts.create(db, "kim", password=PW)
        cls.c, cls.kim = (TestClient(main.app, base_url="http://testserver") for _ in "ab")
        for c, who in ((cls.c, "robin"), (cls.kim, "kim")):
            r = c.post("/api/login", json={"username": who, "password": PW}, headers=H)
            assert r.status_code == 200, r.text
        # an admin's own second factor, now that the server has two accounts (main.admin), once signed in
        db.execute("UPDATE users SET totp_secret=? WHERE username='robin'", (crypto.enc(twofactor.new_secret()),))
        db.close()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        cls.host.stop()
        build_local()

    def setUp(self):
        for k in self.host.mode:
            self.host.mode[k] = "ok"
        self.host.hits.clear()
        if os.path.exists(LOCAL):
            os.remove(LOCAL)
        db = dbm.connect()
        for k in ("directory.last", "directory.read_at"):
            dbm.meta_set(db, k, None)
        db.execute("DELETE FROM settings WHERE key='directory.weekly'")
        settings.forget_saved()
        db.execute("DELETE FROM outside_contacts WHERE purpose=?", (outside.DIRECTORY,))
        db.close()
        update.wake()   # the app's thread looks at the weekly update (off): it contacts no one

    def contacts(self):
        db = dbm.connect()
        try:
            return [(crypto.dec(r["host"]), r["count"]) for r in db.execute("SELECT host, count FROM outside_contacts WHERE purpose=?", (outside.DIRECTORY,))]
        finally:
            db.close()

    def wait(self):
        for _ in range(200):
            j = self.c.get("/api/provider-directory").json()
            if not j["running"]:
                return j
            time.sleep(0.05)
        self.fail("the update didn't finish")

    def test_an_admin_adds_both_lists_and_the_next_search_finds_an_epic_hospital(self):
        j = self.c.get("/api/provider-directory").json()
        self.assertEqual({x["id"]: x["included"] for x in j["lists"]}, {"epic-brands": False, "oracle-millennium": False, "curated": True})
        self.assertEqual(j["hosts"], [f"127.0.0.1:{self.host.port}"])
        self.assertEqual([x["adds"][:9] for x in j["lists"] if x["fetched"]], ["about 1,3", "about 1,3"], "what each would add")
        self.assertEqual(self.c.get("/api/directory", params={"q": "harborview"}).json()["results"], [])
        time.sleep(0.2)
        self.assertEqual((self.host.hits, self.contacts()), ([], []), "nothing is contacted before the button")
        db = dbm.connect()
        start = db.execute("SELECT MAX(id) FROM activity").fetchone()[0]
        db.close()
        r = self.c.post("/api/provider-directory/update", headers=H)
        self.assertEqual(r.status_code, 202, r.text)
        j = self.wait()
        self.assertEqual(j["last"]["read"], {"epic-brands": 3, "oracle-millennium": 2})
        self.assertEqual(j["last"]["failed"], {})
        self.assertTrue(all(x["included"] for x in j["lists"]))
        hit = self.c.get("/api/directory", params={"q": "harborview"}).json()["results"][0]
        self.assertEqual(hit["id"], "epic:9001-1", "found at once, with no restart")
        self.assertEqual(self.contacts(), [(f"127.0.0.1:{self.host.port}", 1)])
        db = dbm.connect()
        try:
            log = [(r["action"], r["actor"], json.loads(crypto.dec(r["detail"]))) for r in
                   db.execute("SELECT * FROM activity WHERE id > ? AND profile_id IS NULL AND action IN ('outside', 'directory.update') ORDER BY id", (start,))]
        finally:
            db.close()
        self.assertEqual(log[0][:2], ("outside", "robin"))
        self.assertEqual(log[0][2]["purpose"], "Provider directory")
        self.assertEqual(log[-1], ("directory.update", "robin", {"origin": "manual", "read": {"epic-brands": 3, "oracle-millennium": 2}, "failed": {}}))
        self.assertFalse([f for f in os.listdir(os.path.dirname(LOCAL)) if f.startswith(".update-")], "its downloads are gone")

    def test_a_failed_or_cut_short_download_keeps_the_earlier_lists(self):
        update.run("manual")
        self.assertIn("oracle:t1", directory.index().by_id)
        for mode, why in (("cut", "cut short"), ("error", "503"), ("junk", "isn't the list")):
            self.host.mode["oracle-millennium"] = mode
            last = update.run("manual")
            self.assertEqual(list(last["read"]), ["epic-brands"], mode)
            self.assertIn(why, last["failed"]["oracle-millennium"], mode)
            ix = directory.index()
            self.assertIn("oracle:t1", ix.by_id, f"{mode}: the earlier Oracle list is still in use")
            self.assertTrue(next(s for s in ix.sources if s["id"] == "oracle-millennium")["included"])
        self.host.mode["epic-brands"] = "cut"
        before = os.stat(LOCAL).st_mtime_ns
        last = update.run("manual")
        self.assertEqual(last["read"], {})
        self.assertEqual(set(last["failed"]), {"epic-brands", "oracle-millennium"})
        self.assertEqual(os.stat(LOCAL).st_mtime_ns, before, "nothing read: the copy isn't touched")
        self.assertIn("epic:9001-1", directory.index().by_id)
        said = self.c.get("/api/provider-directory").json()["last"]
        self.assertEqual(said["failed"]["epic-brands"], "the download was cut short")

    def test_the_weekly_update_is_off_until_turned_on_then_runs_and_is_shown(self):
        db = dbm.connect()
        try:
            self.assertIsNone(update.due_at(db), "off by default")
            settings.put(db, "directory.weekly", True)
            self.assertLessEqual(update.due_at(db), time.time(), "never read: due now")
            update.run("weekly")
            j = self.c.get("/api/provider-directory").json()
            self.assertTrue(j["weekly"])
            self.assertEqual(j["next"], j["read_at"] + update.WEEK)
            self.assertEqual(j["last"]["origin"], "weekly")
            actor = db.execute("SELECT actor FROM activity WHERE action='directory.update' ORDER BY id DESC").fetchone()[0]
            self.assertEqual(actor, "weekly directory update")
            self.host.mode["epic-brands"] = self.host.mode["oracle-millennium"] = "error"
            update.run("weekly")
            self.assertGreaterEqual(update.due_at(db), time.time() + update.RETRY - 5, "a failed try: again a day later")
            settings.put(db, "directory.weekly", False)
            self.assertIsNone(update.due_at(db))
        finally:
            db.close()

    def test_one_update_at_a_time_and_only_admins(self):
        self.host.mode["epic-brands"] = "slow"
        self.host.slow.clear()
        try:
            self.assertEqual(self.c.post("/api/provider-directory/update", headers=H).status_code, 202)
            r = self.c.post("/api/provider-directory/update", headers=H)
            self.assertEqual(r.status_code, 409, r.text)
            self.assertTrue(self.c.get("/api/provider-directory").json()["running"])
        finally:
            self.host.slow.set()
        self.wait()
        self.assertEqual(self.kim.get("/api/provider-directory").status_code, 403)
        self.assertEqual(self.kim.post("/api/provider-directory/update", headers=H).status_code, 403)
        self.assertEqual(TestClient(main.app, base_url="http://testserver").get("/api/provider-directory").status_code, 401)


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)
    assert not CONTACTS, f"the network was contacted: {CONTACTS}"


if __name__ == "__main__":
    unittest.main(verbosity=2)
