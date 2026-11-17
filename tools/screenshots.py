"""The README's screenshots (docs/screenshots/), from the fictional demo data, in dark mode.

Starts the app on a throwaway copy of the demo (demo/generate.py) in a temporary folder, signs in with a local
account made for the purpose, and captures each page at 1400 x 860 (the phone one at 390 x 844). Nothing real is
read or written: the data folder, key and account exist only for the run and are deleted after it.

  pip install -r requirements-dev.txt && playwright install chromium
  python3 tools/screenshots.py              # all of them
  python3 tools/screenshots.py trends       # just some, by name

The demo ends on END, so the pictures don't change from one day to the next.
"""
import os, shutil, socket, subprocess, sys, tempfile, threading, time

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "screenshots")
END = "2026-11-15"
USER, PW = "demo", "a long demo passphrase for screenshots"
W, H = 1400, 860


def start(tmp):
    os.environ.update(SANGUITY_DATA=tmp, SANGUITY_RECORDS=os.path.join(tmp, "records"), SANGUITY_SERVE_STATIC="1",
                      SANGUITY_WATCH_FILE=os.path.join(tmp, "watch", "daily.json"), SANGUITY_KEY_FILE=os.path.join(tmp, "keys", "data_key"),
                      SANGUITY_WATCH_KEYS=os.path.join(tmp, "watch-keys", "receiver-secrets.json"))
    os.makedirs(os.path.join(tmp, "watch-keys"), exist_ok=True)
    subprocess.run([sys.executable, "-I", os.path.join(ROOT, "demo", "generate.py"), os.path.join(tmp, "build"), "--end", END,
                    "--watch", os.path.join(tmp, "watch")], check=True, stdout=subprocess.DEVNULL)
    sys.path.insert(0, ROOT)
    import uvicorn
    from server import atrest, db as dbm, main, profiles, security
    dbm.init()
    db = dbm.connect()
    atrest.check_key(db)
    atrest.load_pepper(db)
    profiles.bootstrap(db)
    uid = db.execute("INSERT INTO users(username, pw_hash, is_admin, created) VALUES(?, ?, 1, 0)", (USER, security.hash_password(PW))).lastrowid
    db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, 1, 'owner')", (uid,))
    db.close()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.05)
    return srv, f"http://127.0.0.1:{port}"


def main(want):
    tmp = tempfile.mkdtemp(prefix="sanguity-shots-")
    srv, base = start(tmp)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport={"width": W, "height": H}, color_scheme="dark", timezone_id="America/Chicago", locale="en-US",
                                  bypass_csp=True)   # for the waits below (they evaluate strings)
            ctx.add_init_script("try { localStorage.setItem('ht-theme', 'dark') } catch (e) {}")
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/login.html")
            page.fill("#u", USER)
            page.fill("#p", PW)
            page.click("#b")
            page.wait_for_function("typeof booted !== 'undefined' && booted", timeout=30000)
            js, settle = page.evaluate, lambda ms=700: page.wait_for_timeout(ms)

            def home(tab, setup=""):
                page.goto(base + "/")
                page.wait_for_function("typeof booted !== 'undefined' && booted", timeout=30000)
                js(f"switchTab('{tab}')")
                if setup:
                    js(setup)
                settle()

            def shot(name, full=False):
                path = os.path.join(OUT, name + ".png")
                page.screenshot(path=path, full_page=full)
                print("wrote", os.path.relpath(path, ROOT))

            shots = {
                "overview": lambda: (home("overview"), shot("overview")),
                "trends": lambda: (home("labs", "switchTab('labs', 'chart'); applyPreset('2y'); state.sel = new Set(['Hemoglobin', 'WBC']); renderAll(); renderLabs()"), shot("trends")),
                "health": lambda: (home("health", "applyPreset('90d')"), shot("health")),
                "timeline": lambda: (home("timeline", "applyPreset('1y')"), shot("timeline")),
                "costs": lambda: (home("costs", "applyPreset(msKey(primaryMs()))"), shot("costs")),
                "import": lambda: (page.goto(base + "/sources.html?profile=1#upload"), page.wait_for_selector("#importer", state="visible", timeout=15000), settle(), shot("import")),
                "settings": lambda: (page.goto(base + "/settings.html?profile=1"), page.wait_for_selector("#sec-overview .set-preview .tile", timeout=15000), settle(), shot("settings")),
                "summary": lambda: (page.goto(base + "/summary.html?profile=1"), page.wait_for_selector("#build:not([hidden])", timeout=15000),
                                    page.click("#previewBtn"), page.wait_for_selector("#preview:not([hidden]) h3", timeout=15000), settle(),
                                    js("window.scrollTo(0, document.getElementById('preview').getBoundingClientRect().top + scrollY - 80)"), settle(300), shot("summary")),
            }
            for name, take in shots.items():
                if not want or name in want:
                    take()
            if not want or "phone" in want:   # the Overview on a phone
                ph = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True,
                                         color_scheme="dark", timezone_id="America/Chicago", locale="en-US",
                                  bypass_csp=True)   # for the waits below (they evaluate strings)
                ph.add_init_script("try { localStorage.setItem('ht-theme', 'dark') } catch (e) {}")
                ph.add_cookies(ctx.cookies())
                pp = ph.new_page()
                pp.goto(base + "/")
                pp.wait_for_function("typeof booted !== 'undefined' && booted", timeout=30000)
                pp.wait_for_timeout(700)
                pp.screenshot(path=os.path.join(OUT, "phone.png"))
                print("wrote", os.path.relpath(os.path.join(OUT, "phone.png"), ROOT))
            browser.close()
            if errors:
                sys.exit("script errors while taking screenshots: " + "; ".join(errors))
    finally:
        srv.should_exit = True
        time.sleep(0.3)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main(set(sys.argv[1:]))
