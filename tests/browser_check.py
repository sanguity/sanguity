#!/usr/bin/env python3
"""Headless browser check of the app with the fictional demo data, served by the real backend.

Starts the API (with www/ served by it) on a temporary data folder holding the demo build, signs in through the
login page, opens every tab and checks its main content rendered, opens the detail drawer for every kind of
event, drags and scroll-zooms the charts, toggles the event sort, switches profiles, opens the account page,
and fails on any script error, console error, failed request or NaN written into an SVG attribute.

  pip install -r requirements-dev.txt && playwright install chromium
  python3 tests/browser_check.py
"""
import json, os, shutil, socket, subprocess, sys, tempfile, threading, time

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-browser-")
USER, PW = "demo", "a long demo passphrase"
NAN_HOOK = """window.__nan = []; const sa = Element.prototype.setAttribute;
Element.prototype.setAttribute = function (k, v) { if (String(v) === "NaN") window.__nan.push(this.tagName + "." + k); return sa.call(this, k, v); };"""


HTML_RECORD = 990001


def add_html_record(build):
    """An .htm record with a stylesheet and a picture saved beside it, like portal exports have (the demo's only
    document is a PDF), so the drawer is checked with a record that loads its own parts."""
    folder = os.path.join(build, "files", "visit summary_files")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(build, "files", "visit summary.htm"), "w", encoding="utf-8") as f:
        f.write('<link rel="stylesheet" href="visit summary_files/style.css"><p id="t">Visit summary</p>'
                '<img id="i" src="visit summary_files/logo.png">')
    with open(os.path.join(folder, "style.css"), "w", encoding="utf-8") as f:
        f.write("#t { color: rgb(255, 0, 0) }")
    with open(os.path.join(folder, "logo.png"), "wb") as f:   # a 1x1 PNG
        f.write(bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c63"
                              "f8ffff3f0005fe02fea7d6a4ba0000000049454e44ae426082"))
    path = os.path.join(build, "data.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data["events"].append({"id": HTML_RECORD, "t": "2025-10-01T10:00:00", "k": "document", "title": "Visit summary (HTM)", "sub": "Test"})
    data["details"][str(HTML_RECORD)] = {"type": "file", "ext": "htm", "url": "files/visit summary.htm"}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def start_app():
    """Demo build in a temporary data folder, one account owning it plus a second empty profile, then the API."""
    os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_SERVE_STATIC="1",
                      SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"))
    subprocess.run([sys.executable, "-I", os.path.join(ROOT, "demo", "generate.py"), os.path.join(TMP, "build"), "--end", "2026-11-15",
                    "--watch", os.path.join(TMP, "watch")], check=True, stdout=subprocess.DEVNULL)
    add_html_record(os.path.join(TMP, "build"))
    sys.path.insert(0, ROOT)
    import uvicorn
    from server import atrest, db as dbm, main, profiles, security
    dbm.init()
    db = dbm.connect()
    atrest.check_key(db)   # as the app does at start-up: the key, then the pepper passwords are hashed with
    atrest.load_pepper(db)
    profiles.bootstrap(db)
    uid = db.execute("INSERT INTO users(username, pw_hash, is_admin, created) VALUES(?, ?, 1, 0)", (USER, security.hash_password(PW))).lastrowid
    db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, 1, 'owner')", (uid,))
    profiles.create_profile(db, "Second person", owner_id=uid)
    db.close()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.05)
    return srv, f"http://127.0.0.1:{port}"


def main():
    srv, base = start_app()
    problems, passed = [], []

    def check(name, ok, detail=""):
        (passed if ok else problems).append(f"{name}{': ' + str(detail) if detail != '' else ''}")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        # the pages under their real Content-Security-Policy (no inline or eval'd script): every page's own scripts
        # run, with no violation reported. Only page.evaluate here, which the CSP doesn't apply to.
        strict = browser.new_page(viewport={"width": 1400, "height": 900})
        csp_problems = []
        strict.on("pageerror", lambda e: csp_problems.append(f"script error: {e}"))
        strict.on("console", lambda m: m.type == "error" and "Content Security Policy" in m.text and csp_problems.append(m.text))
        r = strict.goto(base + "/setup.html")
        check("pages are sent with a CSP that allows no inline script", "script-src 'self';" in (r.headers.get("content-security-policy") or ""))
        strict.wait_for_url("**/login.html", timeout=10000)   # setup.js ran: setup is done, so it sends you to sign in
        strict.evaluate("localStorage.setItem('ht-theme', 'dark')")
        strict.reload()
        check("under the CSP: theme.js runs", strict.evaluate("document.documentElement.dataset.theme") == "dark")
        # login.js rewrites the note from /api/setup (the page's own text breaks the line, so only its version matches)
        strict.wait_for_function("document.getElementById('stay').textContent.includes('a passkey, otherwise')", timeout=5000)
        stay = strict.evaluate("document.getElementById('stay').textContent")
        check("sign-in page says how long a sign-in lasts", "log out: up to 30 days with two-factor sign-in or a passkey, otherwise 12 hours." in stay, stay)
        strict.fill("#u", USER)
        strict.fill("#p", PW)
        strict.click("#b")
        for _ in range(100):   # login.js signs in, then the app loads the records
            if strict.evaluate("location.pathname.endsWith('/') && typeof EV !== 'undefined' && EV.length > 0"):
                break
            strict.wait_for_timeout(200)
        check("under the CSP: login.js signs in and the app starts", strict.evaluate("typeof EV !== 'undefined' && EV.length > 0"))
        strict.goto(base + "/account.html")
        strict.wait_for_selector("#logout", state="visible")
        strict.wait_for_timeout(500)
        strict.click("#logout")
        strict.wait_for_url("**/login.html", timeout=10000)
        check("under the CSP: no violations", not csp_problems, "; ".join(csp_problems)[:500])
        strict.evaluate("localStorage.clear()")
        strict.close()
        # the checks below drive the page with Playwright's waits, which evaluate script the CSP would refuse
        page = browser.new_page(viewport={"width": 1400, "height": 900}, bypass_csp=True)
        page.add_init_script(NAN_HOOK)
        page.on("pageerror", lambda e: problems.append(f"script error: {e}"))
        page.on("console", lambda m: m.type == "error" and problems.append(f"console error: {m.text}"))
        page.on("response", lambda r: r.status >= 400 and problems.append(f"HTTP {r.status}: {r.url}"))
        page.goto(base + "/")
        page.wait_for_url("**/login.html", timeout=10000)   # signed out: the app sends you to sign in
        page.fill("#u", USER)
        page.fill("#p", "wrong password")
        page.click("#b")
        page.wait_for_function("document.getElementById('e').textContent.length > 0")
        check("wrong password refused", "Wrong username or password" in page.inner_text("#e"))
        check("no plain-HTTP warning on this machine", page.is_hidden("#insecure") and page.evaluate("plainHttpWarning()") is None)
        problems[:] = [x for x in problems if "401" not in x]   # signed out so far: the 401s were the point
        page.evaluate("localStorage.setItem('ht-state', JSON.stringify({tab: 'heatmap', preset: '1y', v: 2}))")   # a view saved before profiles
        page.evaluate("localStorage.setItem('ht-health', JSON.stringify({period: '1y', stays: false}))")
        page.fill("#p", PW)
        page.click("#b")
        try:
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0", timeout=20000)
        except Exception:
            print("App did not start. Page says:", page.inner_text("main")[:300].replace("\n", " "))
            for x in problems:
                print("FAIL  ", x)
            sys.exit(1)
        settle = lambda ms=500: page.wait_for_timeout(ms)
        js = page.evaluate
        check("Health setting saved before profiles carries over", js("hState.period === '1y' && hState.stays === false && localStorage.getItem('ht-health') === null"))
        check("view saved before profiles carries over", js("state.tab === 'heatmap' && state.preset === '1y' && localStorage.getItem('ht-state') === null && !!localStorage.getItem('ht-state:1')"))

        try:
            # ---- every tab renders its main content
            expect = {
                "overview": "document.querySelectorAll('#view-overview .tile').length",
                "health": "[...document.querySelectorAll('#view-health svg')].filter(s => s._draw && s.childNodes.length > 3).length",
                "timeline": "document.querySelectorAll('#tl circle.ev').length + ',' + document.querySelectorAll('#list .row').length",
                "labs": "[...document.querySelectorAll('#charts .chart svg')].filter(s => s.childNodes.length > 3).length",
                "heatmap": "document.querySelectorAll('#hm rect.cell').length",
                "costs": "document.querySelectorAll('#view-costs .stat').length",
            }
            for tab, probe in expect.items():
                page.click(f"#tab-{tab}")
                settle()
                n = js(probe)
                visible = js(f"!document.getElementById('view-{tab}').hidden")
                check(f"{tab} tab renders", visible and all(int(x) > 0 for x in str(n).split(",")), n)

            # ---- detail drawer for every kind of event
            for k in js("[...new Set(EV.filter(e => !e.hidden).map(e => e.k))].sort()"):
                js("k => openEvent(EV.find(e => e.k === k && !e.hidden))", k)
                settle(200)
                ok = js("document.getElementById('drawer').classList.contains('open') && document.getElementById('dtitle').textContent.length > 0")
                check(f"drawer opens for {k}", ok)
                page.keyboard.press("Escape")
            check("drawer closes on Escape", not js("document.getElementById('drawer').classList.contains('open')"))

            # ---- an HTML record shows in the drawer with the stylesheet and picture saved beside it, scripts off
            js("openEvent(DATA.events.find(e => e.title === 'Visit summary (HTM)'))")   # ids are the database's
            page.wait_for_function("""(() => { const f = document.querySelector('#dbody iframe.doc'), d = f && f.contentDocument;
                return d && d.getElementById('i') && d.getElementById('i').complete && d.readyState === 'complete'; })()""", timeout=10000)
            settle(300)
            look = js("""(() => { const f = document.querySelector('#dbody iframe.doc'), d = f.contentDocument;
                return {color: getComputedStyle(d.getElementById('t')).color, img: d.getElementById('i').naturalWidth,
                        sandbox: f.getAttribute('sandbox')}; })()""")
            check("HTML record loads its own stylesheet and picture", look["color"] == "rgb(255, 0, 0)" and look["img"] == 1, look)
            check("HTML record can't run scripts", "allow-scripts" not in (look["sandbox"] or "") and look["sandbox"] is not None, look["sandbox"])
            page.keyboard.press("Escape")

            # ---- Trends: drag pans every chart together, ctrl+wheel zooms, milestones drawn on All
            page.click("#tab-labs")
            js("applyPreset('1y')")
            settle()
            box = page.locator("#charts .chart svg").first.bounding_box()
            before = js("[state.from, state.to]")
            page.mouse.move(box["x"] + box["width"] * 0.6, box["y"] + box["height"] * 0.5)
            page.mouse.down()
            page.mouse.move(box["x"] + box["width"] * 0.3, box["y"] + box["height"] * 0.5, steps=8)
            page.mouse.up()
            settle()
            after = js("[state.from, state.to]")
            check("Trends drag pans", after[0] > before[0] and abs((after[1] - after[0]) - (before[1] - before[0])) < 1000, f"moved {(after[0] - before[0]) / 864e5:.0f} days")
            span = after[1] - after[0]
            page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5)
            page.keyboard.down("Control")
            page.mouse.wheel(0, -300)
            page.keyboard.up("Control")
            settle(700)
            z = js("state.to - state.from")
            check("Trends wheel zooms", z < span * 0.95, f"{span / 864e5:.0f} -> {z / 864e5:.0f} days")
            js("applyPreset('all')")
            settle()
            check("milestones drawn on Trends", js("document.querySelectorAll('#charts .msmark').length") > 0)

            # ---- Health: drag changes its own period
            page.click("#tab-health")
            settle()
            hbox = page.locator("#view-health svg.pz").first.bounding_box()
            hbefore = js("hRange()")
            page.mouse.move(hbox["x"] + hbox["width"] * 0.4, hbox["y"] + hbox["height"] * 0.5)
            page.mouse.down()
            page.mouse.move(hbox["x"] + hbox["width"] * 0.7, hbox["y"] + hbox["height"] * 0.5, steps=8)
            page.mouse.up()
            settle()
            check("Health drag pans", js("hRange()[0]") < hbefore[0] and js("hState.period") == "custom")

            # ---- Timeline: event sort toggle
            page.click("#tab-timeline")
            settle()
            first_new = js("document.querySelector('#list .row .d').textContent")
            page.click("#sortSeg [data-sort=asc]")
            settle(200)
            first_old = js("document.querySelector('#list .row .d').textContent")
            check("event sort toggles", first_new != first_old, f"{first_new} -> {first_old}")

            nan = js("window.__nan")
            check("no NaN SVG attributes", not nan, nan[:5] if nan else "")

            # ---- profile switcher: the second profile has no records yet, and its own saved view
            check("profile switcher shown", js("!document.getElementById('profile').hidden && document.getElementById('profile').options.length === 2"))
            js("hState.period = '30d'; hSave()")
            page.select_option("#profile", label="Second person")
            page.wait_for_function("document.querySelector('main .empty') && PROFILE && PROFILE.name === 'Second person'", timeout=10000)
            check("Health period is kept per profile", js("hState.period") == "90d", js("hState.period"))
            check("empty profile explains itself", "no records yet" in page.inner_text("main"))
            check("empty profile hides the tabs and filters", js("document.querySelector('.tabs').hidden && document.querySelector('.filters').hidden && getComputedStyle(document.querySelector('.tabs')).display === 'none'"))
            page.select_option("#profile", label="My records")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records'", timeout=20000)
            check("switching back restores the profile's tab", js("state.tab") == "timeline", js("state.tab"))
            check("switching back restores the profile's Health period", js("hState.period") == "30d", js("hState.period"))

            # ---- account page: password, devices, profiles with sharing, accounts (admin)
            page.goto(base + "/account.html")
            page.wait_for_function("document.querySelectorAll('#sessList tbody tr').length > 0 && document.querySelectorAll('#profList .prof').length === 2 && !document.getElementById('users').hidden", timeout=10000)
            check("account page lists devices, profiles and accounts", True)
            # three tabs: Sign-in & security first; Admin only for admins; the address keeps the tab, old links still land
            vis = lambda sel: js(f"!document.querySelector('{sel}').hidden")
            check("account tabs: security shown first, Admin offered", vis("#panel-security") and not vis("#panel-profiles") and vis("#tab-admin"))
            page.click("#tab-profiles")
            check("a tab shows its panel and goes in the address", vis("#panel-profiles") and not vis("#panel-security") and js("location.hash") == "#profiles")
            page.reload()
            page.wait_for_function("document.querySelectorAll('#profList .prof').length === 2", timeout=10000)
            check("the tab survives a reload", vis("#panel-profiles"))
            page.goto(base + "/account.html#twofa")
            page.wait_for_function("document.getElementById('tfHint').textContent.length > 0", timeout=10000)
            check("an old section link opens the tab that holds it", vis("#panel-security") and js("location.hash") == "#twofa")
            page.focus("#tab-security"); page.keyboard.press("ArrowRight")
            check("arrow keys move between tabs", vis("#panel-profiles") and js("document.activeElement.id") == "tab-profiles")
            page.click("#tab-admin")
            page.fill("#userName", "alex")
            page.click("#userNew button[type=submit]")
            page.wait_for_function("document.querySelector('#userMsg code')", timeout=10000)
            check("admin creates an account with a one-time password", len(js("document.querySelector('#userMsg code').textContent")) >= 20)
            # no longer the only account: admin actions now wait for the admin's own second factor (issue #75)
            check("with a second account, the admin is asked for a second factor",
                  js("!document.getElementById('adminTf').hidden && document.getElementById('adminTools').hidden"))
            # two-factor sign-in: turn it on from the key shown, confirm with a code, get recovery codes
            from server import twofactor
            import time as _time
            totp = lambda key, ahead=0: twofactor._code(key, int(_time.time() // twofactor.PERIOD) + ahead)
            check("the admin's note links to the security tab", js("document.querySelector('#adminTf a').getAttribute('href')") == "#security")
            page.click("#adminTf a")
            page.wait_for_function("!document.getElementById('panel-security').hidden", timeout=5000)
            check("which opens it", vis("#panel-security") and not vis("#panel-admin"))
            page.locator("#twofa").get_by_role("button", name="Turn on").click()
            page.wait_for_selector("#twofa .tf-key")
            key = js("document.querySelector('#twofa .tf-key').textContent").replace(" ", "")
            check("2FA setup shows a QR code", js("document.querySelector('#twofa .tf-setup img').naturalWidth > 100"))
            page.locator("#twofa input").fill(totp(key))
            page.locator("#twofa").get_by_role("button", name="Confirm").click()
            page.wait_for_selector("#twofa .tf-codes")
            check("2FA confirmed, 10 recovery codes shown", len(js("document.querySelector('#twofa .tf-codes').textContent").split()) == 10)
            page.locator("#twofa").get_by_role("button", name="I've saved them").click()
            page.wait_for_function("document.getElementById('tfHint').textContent.startsWith('On.') && "
                                   "!document.getElementById('adminTools').hidden && document.querySelector('#userList').textContent.includes('alex')", timeout=10000)
            check("with 2FA on, the account tools are back", js("document.getElementById('adminTf').hidden"))
            # activity: the profile's log shows the records being opened (by this run), and filters by kind;
            # the admin's account log shows the account just created
            page.click("#tab-profiles")
            mine = page.locator("#profList .prof", has_text="My records")
            mine.get_by_role("button", name="Activity").click()
            page.wait_for_function("[...document.querySelectorAll('#profList .activity tbody tr')].some(r => r.textContent.includes('opened the records'))", timeout=10000)
            check("profile activity shows the records being opened", True)
            mine.locator(".activity select").nth(1).select_option("document")
            page.wait_for_function("[...document.querySelectorAll('#profList .activity tbody tr')].every(r => r.textContent.includes('opened') && !r.textContent.includes('opened the records'))", timeout=10000)
            check("activity filters by kind", js("document.querySelector('#profList .activity tbody').textContent.includes('demo-report.pdf') || document.querySelector('#profList .activity tbody').textContent.includes('Nothing yet')"))
            page.click("#tab-admin")
            page.locator("#acctLog").get_by_role("button", name="Account activity").click()
            page.wait_for_function("document.querySelector('#acctLog tbody') && document.querySelector('#acctLog tbody').textContent.includes('created the account alex')", timeout=10000)
            check("admin sees account activity", True)
            href = js("document.querySelector('#profList .prof a[download]').getAttribute('href')")
            head = js(f"fetch('{href}').then(r => r.arrayBuffer()).then(b => [...new Uint8Array(b.slice(0, 2))])")
            check("Export everything downloads a zip", head == [0x50, 0x4B], head)
            # sharing with someone who already has access asks before changing their role; declining keeps it
            page.click("#tab-profiles")
            asked_share = []
            page.once("dialog", lambda d: (asked_share.append(d.message), d.dismiss()))
            form = page.locator("#profList .prof", has_text="Second person").locator("form")
            form.locator("input").fill(USER)
            form.locator("select").select_option("viewer")
            form.get_by_role("button", name="Share").click()
            settle(300)
            check("sharing with an existing member asks first", asked_share and "already has access as owner" in asked_share[0], asked_share[:1])
            check("declining keeps their role", js("fetch('api/profiles/2/members').then(r => r.json()).then(m => m[0].role)") == "owner")
            # deleting a profile: the button stays off until its name is typed exactly
            second = page.locator("#profList .prof", has_text="Second person")
            second.get_by_role("button", name="Delete profile…").click()
            go = second.get_by_role("button", name="Delete it and everything in it")
            check("delete needs the profile's name first", go.is_disabled())
            second.get_by_label("Type Second person to confirm").fill("Second person")
            go.click()
            page.wait_for_function("![...document.querySelectorAll('#profList .prof')].some(p => p.textContent.includes('Second person'))", timeout=10000)
            gone_from = len(problems)
            check("deleting a profile removes it", js("fetch('api/profiles/2/data').then(r => r.status)") == 404)
            problems[gone_from:] = [x for x in problems[gone_from:] if "404" not in x]   # that 404 was the point
            # resetting a password needs a fresh check: once the sign-in is more than a few minutes old, it asks first
            from server import db as dbm, profiles
            db = dbm.connect()
            db.execute("UPDATE sessions SET verified_at=0")
            before = len(problems)
            page.click("#tab-admin")
            page.locator("#userList tr", has_text="alex").get_by_role("button", name="Reset password").click()
            page.wait_for_selector("#reauth input", state="visible", timeout=10000)
            check("resetting a password asks to confirm it's you", "Confirm it's you" in page.inner_text("#reauth"))
            page.fill("#reauth input", PW)
            page.locator("#reauth").get_by_role("button", name="Confirm").click()
            page.wait_for_function("document.getElementById('reauth').hidden && document.querySelector('#userMsg code') && "
                                   "document.getElementById('userMsg').textContent.includes('alex')", timeout=10000)
            check("after the password, the reset goes ahead", True)
            problems[before:] = [x for x in problems[before:] if "403" not in x]   # the refusal that asks
            # disabling the only owner of a profile asks first; accepting disables the account
            alex = db.execute("SELECT id FROM users WHERE username='alex'").fetchone()[0]
            profiles.create_profile(db, "Alex's records", owner_id=alex)
            db.close()
            asked, before = [], len(problems)
            page.once("dialog", lambda d: (asked.append(d.message), d.accept()))
            page.locator("#userList tr", has_text="alex").get_by_role("button", name="Disable").click()
            page.wait_for_function("[...document.querySelectorAll('#userList tr')].some(r => r.textContent.includes('alex') && r.textContent.includes('disabled'))", timeout=10000)
            problems[before:] = [x for x in problems[before:] if "409" not in x]   # the refusal that prompts the question
            check("disabling a profile's only owner asks first", asked and "only owner of Alex's records" in asked[0], asked[:1])
            page.wait_for_function("document.querySelector('#ownerless') && document.querySelector('#ownerless').textContent.includes(\"Alex's records\")", timeout=10000)
            check("an admin sees profiles left without an owner", True)
            # removing that account: confirmed, then asked who owns Alex's records instead (this admin)
            answers = iter([True, USER])
            def answer(d):
                a = next(answers)
                d.accept(a) if isinstance(a, str) else d.accept()
            page.on("dialog", answer)
            before_remove = len(problems)
            page.locator("#userList tr", has_text="alex").get_by_role("button", name="Remove").click()
            page.wait_for_function("!document.querySelector('#userList').textContent.includes('alex')", timeout=10000)
            page.remove_listener("dialog", answer)
            problems[before_remove:] = [x for x in problems[before_remove:] if "409" not in x]   # the question's refusal
            page.wait_for_function("[...document.querySelectorAll('#profList .prof')].some(p => p.textContent.includes(\"Alex's records\"))", timeout=10000)
            check("removing a sole owner hands their profile to whoever is named", True)
            # log out ends the session: the API now refuses
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0", timeout=20000)
            before = len(problems)
            page.click("#logout")
            page.wait_for_url("**/login.html", timeout=10000)
            check("log out ends the session", js("fetch('api/me').then(r => r.status)") == 401)
            # signing in again takes the password, then a code
            page.fill("#u", USER)
            page.fill("#p", PW)
            page.click("#b")
            page.wait_for_selector("#c", state="visible")
            check("2FA: the password alone doesn't sign in", js("fetch('api/me').then(r => r.status)") == 401)
            page.fill("#c", totp(key, 1))
            page.click("#b")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0", timeout=20000)
            check("2FA: the code signs in", js("fetch('api/me').then(r => r.status)") == 200)
            problems[before:] = [x for x in problems[before:] if "401" not in x]   # only the 401s after logging out were the point
            # passkeys, with Chromium's virtual authenticator, at localhost: a secure context with a name (passkeys
            # refuse an IP address like 127.0.0.1). Same server; this session's cookie carried over to that name.
            local = base.replace("127.0.0.1", "localhost")
            SIGNED_IN_OR_ERROR = "!location.pathname.endsWith('login.html') || (document.getElementById('e') || {}).textContent"
            ctx = browser.new_context(viewport={"width": 1400, "height": 900}, bypass_csp=True)
            ctx.add_cookies([{**{k: c[k] for k in ("name", "value", "path", "httpOnly", "sameSite")}, "domain": "localhost"}
                             for c in page.context.cookies() if c["name"] == "sg_session"])
            pk = ctx.new_page()
            pk.on("pageerror", lambda e: problems.append(f"script error (localhost): {e}"))
            pk.on("console", lambda m: m.type == "error" and problems.append(f"console error (localhost): {m.text}"))
            before = len(problems)
            pk.on("response", lambda r: r.status >= 400 and problems.append(f"HTTP {r.status}: {r.url}"))
            cdp = ctx.new_cdp_session(pk)
            cdp.send("WebAuthn.enable")
            cdp.send("WebAuthn.addVirtualAuthenticator", {"options": {"protocol": "ctap2", "transport": "internal",
                     "hasResidentKey": True, "hasUserVerification": True, "isUserVerified": True}})
            pk.goto(local + "/account.html")
            pk.wait_for_selector("#pkBody form")
            pk.locator("#pkBody input[type=password]").fill(PW)
            pk.locator("#pkBody").get_by_role("button", name="Add a passkey").click()
            pk.wait_for_selector("#pkList tbody tr", timeout=10000)
            check("a passkey is added and listed", "never" in pk.inner_text("#pkList"), pk.inner_text("#pkMsg"))
            pk.click("#logout")
            pk.wait_for_url("**/login.html", timeout=10000)
            check("sign-in page offers a passkey", pk.is_visible("#pk") and pk.inner_text("#pk") == "Sign in with a passkey")
            pk.click("#pk")
            pk.wait_for_function(SIGNED_IN_OR_ERROR, timeout=20000)
            check("a passkey signs in on its own", pk.evaluate("fetch('api/me').then(r => r.json()).then(j => j.user && j.user.username)") == USER,
                  pk.evaluate("(document.getElementById('e') || {}).textContent"))
            pk.click("#logout")
            pk.wait_for_url("**/login.html", timeout=10000)
            pk.fill("#u", USER)
            pk.fill("#p", PW)
            pk.click("#b")
            pk.wait_for_selector("#c", state="visible")
            check("after the password: the code, or a passkey", pk.is_visible("#pk") and pk.inner_text("#pk") == "Use a passkey")
            pk.click("#pk")
            pk.wait_for_function(SIGNED_IN_OR_ERROR, timeout=20000)
            check("a passkey finishes the sign-in after the password", pk.evaluate("fetch('api/me').then(r => r.status)") == 200)
            problems[before:] = [x for x in problems[before:] if "401" not in x]   # signed out on the way, on purpose
            ctx.close()
        except Exception as e:   # report what was caught so far instead of a bare traceback
            problems.append(f"check stopped: {str(e).splitlines()[0]}")
            if "pk" in locals():
                problems.append(f"passkey page at {pk.url}: " + str(pk.evaluate("document.body.innerText.slice(0, 300)")))
        browser.close()
    srv.should_exit = True
    time.sleep(0.3)
    shutil.rmtree(TMP, ignore_errors=True)

    for x in passed:
        print("ok    ", x)
    for x in problems:
        print("FAIL  ", x)
    print(f"\n{len(passed)} passed, {len(problems)} failed")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
