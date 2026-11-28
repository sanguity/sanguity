#!/usr/bin/env python3
"""Install as a phone app (#56): the manifest, icons and service worker are served with the right types and the
pages' own headers (by nginx.conf and by the development server alike), the worker keeps every file of the app but
nothing from /api/, every page links the manifest and registers the worker, every file it keeps is UTF-8 (#248), and
/api/me says when the sign-in ends (the offline copy of records is deleted then). The browser side (install, offline
view, clearing) is in tests/browser_check.py.

  pip install -r requirements-dev.txt
  python3 tests/test_pwa.py
"""
import json, os, re, struct, subprocess, sys, tempfile, shutil, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WWW = os.path.join(ROOT, "www")
PAGES = ["index", "login", "setup", "account", "import", "settings", "providers", "connect", "summary", "share", "admin", "sources", "sharing", "guide"]

# run in a process of its own: the development server's static routes exist only when SANGUITY_SERVE_STATIC is set
# before server.main is imported, which other test files in the same run may already have done without it
SERVED = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
from fastapi.testclient import TestClient
from server import atrest, db as dbm, main, security
dbm.init(); db = dbm.connect(); atrest.check_key(db); atrest.load_pepper(db)
from server import settings_import; settings_import.take(db, say=lambda *a: None)   # PUBLIC_ORIGINS, taken in (#1158)
db.execute("INSERT INTO users(username, pw_hash, is_admin, created) VALUES('pwa', ?, 1, 0)", (security.hash_password("a long test passphrase"),))
db.close()
c = TestClient(main.app)
out = {}
for path in ("/manifest.webmanifest", "/sw.js", "/js/pwa.js", "/js/offline.js", "/icons/icon-192.png", "/fonts/inter-v4.001-latin.woff2", "/index.html"):
    r = c.get(path)
    out[path] = {"status": r.status_code, "type": r.headers.get("content-type", ""), "cache": r.headers.get("cache-control"),
                 "csp": r.headers.get("content-security-policy")}
out["login"] = c.post("/api/login", json={"username": "pwa", "password": "a long test passphrase"}, headers={"X-Sanguity": "1"}).status_code
r = c.get("/api/me")
out["me"] = {"status": r.status_code, "cache": r.headers.get("cache-control"), "body": r.json()}
print(json.dumps(out))
"""


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", head[16:24])


class Pwa(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.mkdtemp(prefix="sanguity-pwa-")
        try:
            env = {**os.environ, "SANGUITY_SERVE_STATIC": "1", "SANGUITY_DATA": tmp, "SANGUITY_RECORDS": os.path.join(tmp, "records"),
                   "SANGUITY_KEY_FILE": os.path.join(tmp, "keys", "data_key"), "SANGUITY_WATCH_FILE": os.path.join(tmp, "watch", "daily.json"),
                   "PUBLIC_ORIGINS": "http://sanguity.lan:8740, https://sanguity.example.test:8443"}
            os.makedirs(os.path.join(tmp, "records"))
            r = subprocess.run([sys.executable, "-c", SERVED, ROOT], env=env, capture_output=True, text=True, timeout=120)
            if r.returncode:
                raise RuntimeError(r.stderr[-2000:])
            cls.served = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        with open(os.path.join(ROOT, "nginx.conf"), encoding="utf-8") as f:
            cls.nginx = f.read()

    def test_dev_server_sends_the_types_and_page_headers(self):
        from_nginx = re.search(r'add_header Content-Security-Policy "([^"]+)" always;', self.nginx).group(1)
        want = {"/manifest.webmanifest": "application/manifest+json", "/sw.js": "text/javascript",
                "/js/pwa.js": "javascript", "/js/offline.js": "javascript", "/icons/icon-192.png": "image/png",
                "/fonts/inter-v4.001-latin.woff2": "font/woff2"}
        for path, kind in want.items():
            got = self.served[path]
            self.assertEqual(got["status"], 200, path)
            self.assertIn(kind, got["type"], path)
        for path in ("/manifest.webmanifest", "/sw.js"):
            self.assertEqual(self.served[path]["cache"], "no-store", path)   # the worker is checked for updates on every visit
            self.assertEqual(self.served[path]["csp"], from_nginx, path)

    def test_nginx_serves_them_with_the_same_types_and_headers(self):
        self.assertRegex(self.nginx, r"location = /manifest\.webmanifest \{ types \{ \} default_type application/manifest\+json; \}")
        self.assertRegex(self.nginx, r"location = /sw\.js \{ types \{ \} default_type text/javascript; \}")
        self.assertIn("location /icons/ { }", self.nginx)
        # none of them sets its own headers, which in nginx would drop the server's no-store, CSP and the rest
        for loc in re.findall(r"location (?:= )?/(?:manifest\.webmanifest|sw\.js|icons/) \{[^\n]*\}", self.nginx):
            self.assertNotIn("add_header", loc)
        # the fonts (#253): a long cache, as woff2, and the headers a font needs, since their own add_header replaces the
        # server's set (no CSP needed: the pages' default-src 'self' covers font-src)
        fonts = re.search(r"location /fonts/ \{(.*?)\n    \}", self.nginx, re.S).group(1)
        self.assertIn("font/woff2 woff2;", fonts)
        self.assertIn('add_header Cache-Control "public, max-age=31536000, immutable" always;', fonts)
        self.assertIn('add_header X-Content-Type-Options "nosniff" always;', fonts)
        self.assertTrue(all(re.fullmatch(r"[\w-]+-v\d[\d.]*-[\w-]+\.woff2|[\w-]+-OFL\.txt", n) for n in os.listdir(os.path.join(WWW, "fonts"))),
                        "each font's file name has its version (so the long cache never serves an old one), beside its licence")
        # the CSP's default-src 'self' covers the worker and the manifest; nothing wider was added for them
        csp = re.search(r'add_header Content-Security-Policy "([^"]+)" always;', self.nginx).group(1)
        self.assertIn("default-src 'self'", csp)
        self.assertNotRegex(csp, r"(worker|manifest)-src [^;]*(\*|https?:|blob:)")

    def test_manifest_is_installable(self):
        with open(os.path.join(WWW, "manifest.webmanifest"), encoding="utf-8") as f:
            m = json.load(f)
        for key in ("name", "short_name", "start_url", "scope", "display", "theme_color", "background_color", "icons"):
            self.assertIn(key, m)
        self.assertEqual(m["display"], "standalone")
        self.assertFalse(m["start_url"].startswith(("/", "http")), "relative, so the app works under a sub-path too")
        sizes = {}
        for icon in m["icons"]:
            path = os.path.join(WWW, icon["src"])
            self.assertTrue(os.path.isfile(path), icon["src"])
            if icon["type"] == "image/png":
                w, h = png_size(path)
                self.assertEqual(icon["sizes"], f"{w}x{h}", icon["src"])
                sizes.setdefault(icon.get("purpose", "any"), set()).add(w)
        self.assertTrue({192, 512} <= sizes["any"])
        self.assertIn(512, sizes["maskable"])
        self.assertEqual(png_size(os.path.join(WWW, "icons", "apple-touch-icon.png")), (180, 180))

    def test_every_page_links_the_manifest_and_registers_the_worker(self):
        for page in PAGES:
            with open(os.path.join(WWW, page + ".html"), encoding="utf-8") as f:
                html = f.read()
            self.assertIn('<link rel="manifest" href="manifest.webmanifest">', html, page)
            self.assertIn('<link rel="apple-touch-icon" href="icons/apple-touch-icon.png">', html, page)
            self.assertEqual(len(re.findall(r'<meta name="theme-color" content="#[0-9a-f]{6}" media="\(prefers-color-scheme: (light|dark)\)">', html)), 2, page)
            self.assertIn('<script src="js/util.js"></script>\n<script src="js/pwa.js"></script>', html, page)
            # no font is preloaded (#328): on a slow connection Inter's preload shared the bandwidth with the stylesheets
            # and held up the first paint by 0.15 to 0.3 s; the text shows at once in the metric-matched fallback instead
            # (base.css), and the stylesheets come first
            self.assertNotIn('rel="preload"', html, page)
            self.assertIn('<link rel="stylesheet" href="css/tokens.css">\n<link rel="stylesheet" href="css/base.css">', html, page)
        with open(os.path.join(WWW, "index.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertLess(html.index('src="js/offline.js"'), html.index('src="js/app.js"'), "before app.js asks the API anything")
        with open(os.path.join(WWW, "settings.html"), encoding="utf-8") as f:   # its You → This device (#991; Account before)
            html = f.read()
        self.assertIn('id="offlineRecords"', html)
        self.assertLess(html.index('id="installApp"'), html.index('id="offlineRecords"'), "how to install, then what it keeps offline")
        with open(os.path.join(WWW, "index.html"), encoding="utf-8") as f:
            self.assertIn('id="installBtn" type="button"', f.read())

    def test_worker_keeps_every_app_file_and_nothing_from_the_api(self):
        with open(os.path.join(WWW, "sw.js"), encoding="utf-8") as f:
            sw = f.read()
        listed = set(json.loads("[" + re.search(r"const FILES = \[(.*?)\];", sw, re.S).group(1).strip().rstrip(",") + "]"))
        have = {p + ".html" for p in PAGES} | {"", "manifest.webmanifest"}
        for sub in ("css", "js"):
            have |= {f"{sub}/{n}" for n in os.listdir(os.path.join(WWW, sub))}
        have |= {f"icons/{n}" for n in os.listdir(os.path.join(WWW, "icons")) if not n.endswith(".txt")}   # not Lucide's licence (#257)
        self.assertIn("icons/sprite.svg", have)
        have |= {f"fonts/{n}" for n in os.listdir(os.path.join(WWW, "fonts")) if n.endswith(".woff2")}   # not the licences
        have.discard("js/connect-return.js")   # only for where a connected source's sign-in comes back, never offline
        self.assertEqual(listed, have, "sw.js FILES lists every page, style, script, font and icon in www/")
        self.assertFalse(any(f.startswith("api/") or "connect-return" in f for f in listed))
        # requests it answers are only the app's own files: never /api/, documents or connect/return. The API's one
        # mention is its route around the worker, to the network (#453)
        self.assertIn("if (path == null) return;", sw)
        code = re.sub(r"/\*.*?\*/|//[^\n]*", "", sw, flags=re.S)
        route = 'e.addRoutes({condition: {urlPattern: new URLPattern({pathname: SCOPE.pathname + "api/*"})}, source: "network"})'
        self.assertIn(route, code)
        self.assertNotIn("api/", code.replace(route, ""))
        # it never writes the offline records' cache (the page does, only when turned on)
        self.assertNotIn("sanguity-offline", re.sub(r"/\*.*?\*/|//[^\n]*", "", sw, flags=re.S))

    def test_every_app_file_is_utf8(self):
        # #248: the pages say they're UTF-8 and are sent as such, so a byte of another encoding (a Windows-1252 "…",
        # 0x85) shows as a garbled character. Every file under www/ but the images and fonts decodes.
        bad = []
        for root, _, files in os.walk(WWW):
            for n in files:
                if n.endswith((".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".woff2")):
                    continue
                path = os.path.join(root, n)
                with open(path, "rb") as f:
                    data = f.read()
                try:
                    data.decode("utf-8")
                except UnicodeDecodeError as e:
                    bad.append(f"{os.path.relpath(path, ROOT)}: byte {data[e.start]:#04x} at {e.start}")
        self.assertEqual(bad, [], "files under www/ that aren't valid UTF-8")

    def test_the_offline_copy_knows_the_persons_own_settings(self):
        # #428: the records page offline answers settings/user from the copy, and a profile's settings saved on Settings
        # give the copy the person's own: pwa.js names them (SgOffline.mine), so they must be the server's of scope "user"
        with open(os.path.join(WWW, "js", "pwa.js"), encoding="utf-8") as f:
            mine = json.loads(re.search(r"const MINE = (\[[^\]]*\]);", f.read()).group(1))
        sys.path.insert(0, ROOT)
        from server import settings
        self.assertEqual(sorted(mine), sorted(k for k, d in settings.SETTINGS.items() if d.scope == "user"))

    def test_me_says_when_the_sign_in_ends(self):
        self.assertEqual(self.served["login"], 200)
        me = self.served["me"]
        self.assertEqual(me["status"], 200)
        self.assertEqual(me["cache"], "no-store")
        import time
        self.assertEqual(me["body"]["https_address"], "https://sanguity.example.test:8443", "for installing from a plain-HTTP page")
        ends = me["body"]["session_ends"]
        self.assertTrue(time.time() < ends <= time.time() + 12 * 3600 + 60, "12 hours without a second factor")


if __name__ == "__main__":
    unittest.main(verbosity=2)
