#!/usr/bin/env python3
"""Headless browser check of the app with the fictional demo data, served by the real backend.

Starts the API (with www/ served by it) on a temporary data folder holding the demo build, signs in through the
login page, opens every tab and checks its main content rendered, opens the detail drawer for every kind of
event, drags and scroll-zooms the charts, toggles the event sort, switches profiles, uploads a zip and files on
the import page (a fictional C-CDA and a FHIR export, previewed, saved in the background and then shown on the timeline;
the C-CDA again, previewed as all duplicates and cancelled; the FHIR one then undone from the import history, and
gone from the timeline; UHC, BCBS and spreadsheet claims, shown on the Costs tab; a CareSpace and an Epic MyChart
export), types two lab results in on the import page and finds them on Trends, types a visit, a medication, a
claim and a letter with a PDF into their own forms, finds them on the timeline and Costs and opens the PDF, uploads a lab with a spelling Sanguity
doesn't know, accepts the series suggested at the preview and finds it on Trends, then sees it matched as before on
the next upload, pastes a fictional SMART Health Link served by a local HTTPS server (what it says is shown and
nothing is fetched until Fetch; then its preview, and saved), sets up a connected source against the fake SMART on
FHIR server (tests/fakefhir), connects a profile by signing in, saves its first pull's preview, syncs a new result
and is asked to reconnect when access ends, uploads a Blue Button export and connects a health plan's patient-access
API (the fake again) and finds both sets of claims on Costs, sees the source's and the Health Link's hosts under
Settings → Outside connections, changes settings on the Settings page and resets them, opens the account
page (where the match is listed for admins), sees each record's source in the drawer, on Trends and on the heatmap
and follows its link to the import history, checks the "not medical advice" notice on every tab and beside
out-of-range highlighting, and fails
on any script error, console error, failed request, request to another host or NaN written into an SVG attribute.

  pip install -r requirements-dev.txt && playwright install chromium
  python3 tests/browser_check.py
"""
import json, os, shutil, socket, subprocess, sys, tempfile, threading, time

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-browser-")
USER, PW = "demo", "a long demo passphrase"
SHL = None   # the local HTTPS host of a fictional SMART Health Link (tests/shl_server.py), started with the app
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
    from server import config
    config.PUBLIC_ORIGINS.add(f"http://127.0.0.1:{port}")   # the return address connected sources sign in back to
    config.CONNECT_ALLOW_HTTP = True                         # the fake SMART on FHIR server is plain HTTP on this machine
    global SHL
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    import shl_server
    SHL = shl_server.Server().start()   # a local HTTPS host for a SMART Health Link (#94); server.shl may reach it
    SHL.trust()
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
        # no page contacts another host (#59): only the app itself, and the fake vendor's sign-in page while a test
        # signs in there (the browser visits a vendor's sign-in; the server never contacts anything unlisted)
        allowed = {base}
        origin = lambda u: "/".join(u.split("/")[:3])
        elsewhere = lambda r: (not r.url.startswith(("data:", "blob:", "about:")) and origin(r.url) not in allowed
                               and problems.append(f"request to another host: {r.url}"))
        strict.on("request", elsewhere)
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
        strict.goto(base + "/import.html")
        strict.wait_for_selector("#importer", state="visible", timeout=10000)
        check("under the CSP: import.js runs", strict.evaluate("document.querySelectorAll('#profile option').length") == 2)
        strict.goto(base + "/settings.html")
        strict.wait_for_selector("#sec-overview .set-labs li", timeout=10000)
        check("under the CSP: settings.js runs", strict.evaluate("document.querySelectorAll('#sections > section').length") == 4)
        strict.goto(base + "/providers.html?q=medicare")   # find your provider (#93): the bundled list, searched
        strict.wait_for_selector(".dir-hit", timeout=10000)
        check("under the CSP: providers.js searches the directory", "Medicare" in strict.evaluate("document.querySelector('.dir-hit').textContent"))
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
        page.on("request", elsewhere)
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
                if tab == "heatmap":   # a cell's tooltip says where the result came from (#100)
                    page.hover("#hm rect.cell[data-s]")
                    settle(100)
                    tipped = js("document.getElementById('tip').innerText")
                    check("hovering a heatmap cell shows its source", "From Setup build, imported" in tipped, tipped[:200])
                    page.mouse.move(0, 0)
                page.locator("#notice").scroll_into_view_if_needed()
                check(f"{tab} tab: the 'not medical advice' notice shows in the footer",
                      page.is_visible("#notice") and "Not medical advice." in page.inner_text("#notice")
                      and "your care team interprets them" in page.inner_text("#notice"))

            # ---- next to out-of-range highlighting (#57): a caption on each tab that colours it, in light and dark, at 375 px
            flagged = {   # the tab's out-of-range highlighting, and its caption
                "overview": "#view-overview .tile .st.hi, #view-overview .tile .st.lo",
                "labs": "#charts .pt.abn",
                "heatmap": "#hm rect.cell[style*='--div-hi'], #hm rect.cell[style*='--div-lo']",
            }
            theme_before = js("document.documentElement.dataset.theme || null")
            for theme in ("light", "dark"):
                js("t => { document.documentElement.dataset.theme = t; }", theme)
                for width in (1400, 375):
                    page.set_viewport_size({"width": width, "height": 900 if width > 375 else 800})
                    for tab, marks in flagged.items():
                        page.click(f"#tab-{tab}")
                        settle(300)
                        note = page.locator(f"#view-{tab} .range-note")
                        shown = note.count() == 1 and note.is_visible()
                        text = note.inner_text() if shown else ""
                        check(f"{tab} ({theme}, {width} px): caption beside the out-of-range highlighting",
                              shown and js(f"document.querySelectorAll({marks!r}).length") > 0
                              and "reference ranges in your own lab reports" in text and "isn't medical advice" in text, text)
                        legible = js("""(() => { const n = document.querySelector('#view-%s .range-note'), f = document.getElementById('notice');
                            const bg = getComputedStyle(document.body).backgroundColor;
                            return getComputedStyle(n).color !== bg && getComputedStyle(f).color !== bg; })()""" % tab)
                        check(f"{tab} ({theme}, {width} px): caption and footer readable, page fits",
                              legible and js("document.documentElement.scrollWidth") <= width, js("document.documentElement.scrollWidth"))
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            page.set_viewport_size({"width": 1400, "height": 900})

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

            # ---- corrections (#33): hide an event from the drawer, see it leave the timeline, undo; rename shows "Edited"
            page.click("#tab-timeline")
            js("applyPreset('all')")
            settle()
            pick = js("""(() => { const e = EV.find(e => !['exercise', 'labitem', 'lab'].includes(e.k) && EV.filter(x => x.title === e.title).length === 1);
                return e && {id: e.id, t: e.t, title: e.title}; })()""")
            there = "t => EV.some(e => e.title === t.title && e.t === t.t)"
            listed = "t => [...document.querySelectorAll('#list .row')].some(r => r.textContent.includes(t.title))"
            check("an event to correct is on the timeline", pick and js(f"({there})", pick) and js(f"({listed})", pick), pick)
            js("id => openEvent(byId.get(id))", pick["id"])
            settle(200)
            page.click("#dbody .edit-acts button:text-is('Hide')")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && document.getElementById('drawer').classList.contains('open') && "
                                   "document.getElementById('dtitle').textContent === 'Edits to these records'", timeout=20000)
            settle()
            check("hidden from the drawer: the event leaves the timeline", not js(f"({there})", pick) and not js(f"({listed})", pick))
            check("…and the panel lists the edit, with Undo", pick["title"] in page.inner_text("#dbody") and page.locator("#dbody button[data-edit]").count() == 1,
                  page.inner_text("#dbody")[:200])
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the edits panel fits 375 px", js("document.documentElement.scrollWidth <= 375 && document.getElementById('dbody').scrollWidth <= document.getElementById('dbody').clientWidth"),
                  js("[document.documentElement.scrollWidth, document.getElementById('dbody').scrollWidth]"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.click("#dbody button[data-edit]")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && (DATA.edits || []).length === 0", timeout=20000)
            settle()
            check("undoing the edit brings it back", js(f"({there})", pick) and js(f"({listed})", pick))
            again = js(f"(t => EV.find(e => e.title === t.title && e.t === t.t).id)", pick)
            js("id => openEvent(byId.get(id))", again)
            settle(200)
            page.click("#dbody .edit-acts button:text-is('Rename')")
            page.fill("#dbody .edit-form input", "Renamed in the browser check")
            page.click("#dbody .edit-form button:text-is('Save')")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && document.getElementById('dtitle').textContent === 'Renamed in the browser check'", timeout=20000)
            check("a rename shows in the drawer, marked Edited", "Edited" in page.inner_text("#dbody .edited") and page.locator("#dbody .edited button[data-edit]").count() == 1)
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the event panel with its corrections fits 375 px", js("document.documentElement.scrollWidth <= 375 && document.getElementById('dbody').scrollWidth <= document.getElementById('dbody').clientWidth"),
                  js("[document.documentElement.scrollWidth, document.getElementById('dbody').scrollWidth]"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.click("#dbody .edited button[data-edit]")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && (DATA.edits || []).length === 0", timeout=20000)
            check("…and undoing it restores the title", js(f"({there})", pick))
            page.keyboard.press("Escape")

            # ---- where each record came from (#100): a Source line in the drawer, linking to the import in the history
            visit = js("EV.find(e => e.k === 'visit' && !e.hidden).id")
            js("id => openEvent(byId.get(id))", visit)
            settle(200)
            src = page.locator("#dbody p.source")
            text = src.inner_text() if src.count() == 1 else ""
            check("the drawer shows an event's source and when it was imported", "Setup build" in text and "imported" in text, text)
            js("openEvent(EV.find(e => e.k === 'lab' && (DATA.details[e.id] || {}).ids && DATA.details[e.id].ids.length > 1))")
            settle(200)
            n = js("DATA.details[state.selEv].ids.filter(i => DATA.details[i] && byId.get(i)).length")
            check("each result of a lab day shows its source", n > 1 and page.locator("#dbody details.res p.source").count() == n,
                  (n, page.locator("#dbody details.res p.source").count()))
            js("id => openEvent(byId.get(id))", visit)
            settle(200)
            page.focus("#dbody p.source a")   # by keyboard: the link takes focus and Enter follows it
            page.keyboard.press("Enter")
            page.wait_for_url("**/import.html?profile=1#import-*", timeout=10000)
            page.wait_for_selector("#historyList li.hl", timeout=10000)
            check("the source link opens that import in the history, highlighted and focused",
                  js("document.querySelectorAll('#historyList li.hl').length === 1 && document.activeElement.id === location.hash.slice(1)"
                     " && document.activeElement.classList.contains('hl') && document.activeElement.textContent.includes('setup build')"),
                  js("[location.hash, document.activeElement.id, document.activeElement.textContent.slice(0, 80)]"))
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0", timeout=20000)
            settle()

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
            # hovering a point shows where it came from (#100)
            box = page.locator("#charts .chart svg").first.bounding_box()
            tipped = ""
            for f in (0.5, 0.6, 0.4, 0.7, 0.3, 0.8, 0.2, 0.9):
                page.mouse.move(box["x"] + box["width"] * f, box["y"] + box["height"] * 0.5)
                settle(100)
                tipped = js("getComputedStyle(document.getElementById('tip')).display === 'block' ? document.getElementById('tip').innerText : ''")
                if "From " in tipped:
                    break
            check("hovering a Trends point shows its source and import date", "From Setup build, imported" in tipped, tipped[:200])
            page.mouse.move(0, 0)

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
            check("empty profile links to the import page", js("document.querySelector('main .empty a').getAttribute('href')") == "import.html?profile=2")
            check("the header links to the import page", js("!document.getElementById('importLink').hidden"))

            # ---- import page: upload a fictional C-CDA in a zip into the empty profile; it's imported in the background
            # (#20) and its records reach the timeline. Then single files, with #20's hook stood in for.
            page.click("main .empty a")
            page.wait_for_selector("#importer", state="visible", timeout=10000)
            check("import page offers the profiles you can import into, the linked one chosen",
                  js("[...document.querySelectorAll('#profile option')].map(o => o.textContent)") == ["My records", "Second person"]
                  and js("document.getElementById('profile').value") == "2")
            import io, zipfile
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(os.path.join(ROOT, "tests", "fixtures", "importers", "ccda", "maple-grove-ccd", "maple-grove-ccd.xml"),
                        "export/summary.xml")
                z.writestr("export/README.txt", "A fictional portal export, for the browser check.\n")
            page.set_input_files("#pickZip", files=[{"name": "portal-export.zip", "mimeType": "application/zip", "buffer": buf.getvalue()}])
            page.wait_for_function("!document.getElementById('go').disabled", timeout=5000)
            check("the chosen zip is listed", "portal-export.zip" in page.inner_text("#list"))
            page.click("#go")
            page.wait_for_function("document.getElementById('msg').textContent.startsWith('Uploaded')", timeout=20000)
            msg = page.inner_text("#msg")
            check("a zip uploads through the page", msg == "Uploaded 1 file (2 once unpacked).", msg)
            check("each file's progress reaches the end", js("[...document.querySelectorAll('#list progress')].every(p => p.value === 1)"))
            from server import db as dbm, uploads
            db = dbm.connect()
            up = db.execute("SELECT id, state FROM uploads WHERE profile_id=2 ORDER BY id DESC").fetchone()
            check("the upload is stored, unpacked and ready", up and up["state"] == "ready" and
                  sorted(uploads.manifest(db, 2, up["id"])) == ["export/README.txt", "export/summary.xml"])
            page.wait_for_selector(".import-job", timeout=5000)   # the job's card, which shows its stage while it runs
            last_card = "[...document.querySelectorAll('.import-job')].pop()"
            page.wait_for_function(f"/^(Ready to import|All duplicates|Not imported)/.test({last_card}.textContent)", timeout=30000)
            card = js(f"{last_card}.innerText")
            check("the import stops at its preview, with what it would add, the dates, and Save and Cancel (#22)",
                  card.startswith("Ready to import.") and "C-CDA" in card and "visit" in card and "lab result" in card
                  and js(f"[...{last_card}.querySelectorAll('.imp-actions button')].map(b => b.textContent)") == ["Save", "Cancel"], card)
            check("…and nothing is saved yet", db.execute("SELECT COUNT(*) FROM imports WHERE profile_id=2").fetchone()[0] == 0)
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the preview fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            js(f"{last_card}.querySelector('.imp-actions .primary').click()")
            page.wait_for_function(f"/^(Imported|Not imported)/.test({last_card}.textContent)", timeout=30000)
            card = js(f"{last_card}.innerText")
            check("the upload is imported in the background, as C-CDA, and the page says what came in",
                  card.startswith("Imported.") and "C-CDA" in card and "visit" in card and "lab result" in card, card)
            job = db.execute("SELECT state, importer FROM jobs WHERE upload_id=?", (up["id"],)).fetchone()
            check("the job is done, read by the C-CDA importer", job is not None and tuple(job) == ("done", "ccda"), job and tuple(job))
            # the same export again: everything is a duplicate; cancelling it leaves nothing behind
            imports_before = db.execute("SELECT COUNT(*) FROM imports WHERE profile_id=2").fetchone()[0]
            page.set_input_files("#pickZip", files=[{"name": "portal-export.zip", "mimeType": "application/zip", "buffer": buf.getvalue()}])
            page.wait_for_function("!document.getElementById('go').disabled", timeout=5000)
            page.click("#go")
            page.wait_for_function(f"document.querySelectorAll('.import-job').length >= 2 && "
                                   f"/^(Ready to import|All duplicates|Not imported)/.test({last_card}.textContent)", timeout=30000)
            card = js(f"{last_card}.innerText")
            check("the same export uploaded again shows as all duplicates", card.startswith("All duplicates.") and "duplicates skipped" in card, card)
            js(f"{last_card}.querySelector('.imp-dups').open = true")
            check("…and the duplicates can be seen, each with a box to keep it anyway",
                  js(f"{last_card}.querySelectorAll('.imp-dups li input[type=checkbox]').length") > 0
                  and "visit" in js(f"{last_card}.querySelector('.imp-dups ul').innerText"))
            again = db.execute("SELECT id FROM uploads WHERE profile_id=2 ORDER BY id DESC").fetchone()["id"]
            js(f"[...{last_card}.querySelectorAll('.imp-actions button')].find(b => b.textContent === 'Cancel').click()")
            page.wait_for_function(f"{last_card}.textContent.startsWith('Cancelled.')", timeout=10000)
            job = db.execute("SELECT id, state FROM jobs WHERE upload_id=?", (again,)).fetchone()
            check("cancelling discards the import, its preview and its upload",
                  job["state"] == "discarded" and db.execute("SELECT state FROM uploads WHERE id=?", (again,)).fetchone()[0] == "discarded"
                  and db.execute("SELECT COUNT(*) FROM imports WHERE profile_id=2").fetchone()[0] == imports_before
                  and not os.path.exists(os.path.join(TMP, "profiles", "2", "previews", str(job["id"]))), tuple(job))
            # ...and a fictional FHIR export (a Bundle with two PDF attachments) into the same profile: Phase 2 is done
            # when both a C-CDA file and a FHIR export import without a terminal
            fhir_dir = os.path.join(ROOT, "tests", "fixtures", "importers", "fhir", "bundle")
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for d, _, names in os.walk(fhir_dir):
                    for n in names:
                        full = os.path.join(d, n)
                        z.write(full, "fhir-export/" + os.path.relpath(full, fhir_dir).replace(os.sep, "/"))
            page.set_input_files("#pickZip", files=[{"name": "fhir-export.zip", "mimeType": "application/zip", "buffer": buf.getvalue()}])
            page.wait_for_function("!document.getElementById('go').disabled", timeout=5000)
            page.click("#go")
            page.wait_for_function(f"document.querySelectorAll('.import-job').length >= 3 && "
                                   f"/^(Ready to import|All duplicates|Not imported)/.test({last_card}.textContent)", timeout=30000)
            js(f"{last_card}.querySelector('.imp-actions .primary').click()")
            page.wait_for_function(f"/^(Imported|Not imported)/.test({last_card}.textContent)", timeout=30000)
            card = js(f"{last_card}.textContent")
            check("a FHIR export uploads and is imported in the background, as FHIR",
                  card.startswith("Imported.") and "FHIR" in card and "document" in card, card)
            up = db.execute("SELECT id FROM uploads WHERE profile_id=2 ORDER BY id DESC").fetchone()
            job = db.execute("SELECT state, importer FROM jobs WHERE upload_id=?", (up["id"],)).fetchone()
            check("the job is done, read by the FHIR importer", job is not None and tuple(job) == ("done", "fhir"), job and tuple(job))
            # ...and insurer claims (#30): a UHC and a BCBS claims.json in zips, and a generic EOB spreadsheet on its own
            claims_fix = os.path.join(ROOT, "tests", "fixtures", "importers")
            for n, (name, rel) in enumerate([("uhc", "uhc/member-export/claims.json"), ("bcbs", "bcbs/member-export/claims.json"),
                                             ("claims-csv", "claims-csv/eob/claims.csv")]):
                with open(os.path.join(claims_fix, *rel.split("/")), "rb") as f:
                    raw = f.read()
                if name == "claims-csv":
                    page.click("#tab-files")
                    page.set_input_files("#pickFiles", files=[{"name": "eob.csv", "mimeType": "text/csv", "buffer": raw}])
                else:
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                        z.writestr(f"{name}-claims/claims.json", raw)
                    page.set_input_files("#pickZip", files=[{"name": f"{name}-claims.zip", "mimeType": "application/zip", "buffer": buf.getvalue()}])
                page.wait_for_function("!document.getElementById('go').disabled", timeout=5000)
                cards = js("document.querySelectorAll('.import-job').length")
                page.click("#go")
                page.wait_for_function(f"document.querySelectorAll('.import-job').length > {cards} && "
                                       f"/^(Ready to import|All duplicates|Not imported)/.test({last_card}.textContent)", timeout=30000)
                js(f"{last_card}.querySelector('.imp-actions .primary')?.click()")   # Save, at its preview (#22)
                page.wait_for_function(f"/^(Imported|Not imported)/.test({last_card}.textContent)", timeout=30000)
                card = js(f"{last_card}.textContent")
                up = db.execute("SELECT id FROM uploads WHERE profile_id=2 ORDER BY id DESC").fetchone()
                job = db.execute("SELECT state, importer FROM jobs WHERE upload_id=?", (up["id"],)).fetchone()
                check(f"claims upload is imported in the background by the {name} importer",
                      card.startswith("Imported.") and job is not None and tuple(job) == ("done", name), (card, job and tuple(job)))
            page.click("#tab-zip")
            # ...and a fictional CareSpace / OncoEMR portal export (api/*.json, its C-CDA and two PDFs), zipped in a folder
            cs_dir = os.path.join(ROOT, "tests", "fixtures", "importers", "carespace", "full")
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for d, _, names in os.walk(cs_dir):
                    for n in names:
                        full = os.path.join(d, n)
                        z.write(full, "carespace-export/" + os.path.relpath(full, cs_dir).replace(os.sep, "/"))
            page.set_input_files("#pickZip", files=[{"name": "carespace-export.zip", "mimeType": "application/zip", "buffer": buf.getvalue()}])
            page.wait_for_function("!document.getElementById('go').disabled", timeout=5000)
            cards = js("document.querySelectorAll('.import-job').length")
            page.click("#go")
            page.wait_for_function(f"document.querySelectorAll('.import-job').length > {cards} && "
                                   f"/^(Ready to import|All duplicates|Not imported)/.test({last_card}.textContent)", timeout=30000)
            js(f"{last_card}.querySelector('.imp-actions .primary')?.click()")   # Save, at its preview (#22)
            page.wait_for_function(f"/^(Imported|Not imported)/.test({last_card}.textContent)", timeout=30000)
            card = js(f"{last_card}.textContent")
            check("a CareSpace export uploads and is imported in the background, as CareSpace",
                  card.startswith("Imported.") and "CareSpace" in card and "lab result" in card, card)
            up = db.execute("SELECT id FROM uploads WHERE profile_id=2 ORDER BY id DESC").fetchone()
            job = db.execute("SELECT state, importer FROM jobs WHERE upload_id=?", (up["id"],)).fetchone()
            check("the job is done, read by the CareSpace importer", job is not None and tuple(job) == ("done", "carespace"), job and tuple(job))
            # ...and the fictional Epic MyChart export build.py reads (#28): its JSON, documents and C-CDA visit record
            epic_dir = os.path.join(ROOT, "tests", "fixtures", "build", "mychart-20261101")
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for d, _, names in os.walk(epic_dir):
                    for n in names:
                        full = os.path.join(d, n)
                        z.write(full, "mychart-20261101/" + os.path.relpath(full, epic_dir).replace(os.sep, "/"))
            page.set_input_files("#pickZip", files=[{"name": "mychart-export.zip", "mimeType": "application/zip", "buffer": buf.getvalue()}])
            page.wait_for_function("!document.getElementById('go').disabled", timeout=5000)
            cards = js("document.querySelectorAll('.import-job').length")
            page.click("#go")
            page.wait_for_function(f"document.querySelectorAll('.import-job').length > {cards} && "
                                   f"/^(Ready to import|All duplicates|Not imported)/.test({last_card}.textContent)", timeout=30000)
            js(f"{last_card}.querySelector('.imp-actions .primary')?.click()")   # Save, at its preview (#22)
            page.wait_for_function(f"/^(Imported|Not imported)/.test({last_card}.textContent)", timeout=30000)
            card = js(f"{last_card}.textContent")
            check("an Epic MyChart export uploads and is imported in the background, as Epic MyChart",
                  card.startswith("Imported.") and "Epic MyChart" in card and "lab result" in card, card)
            up = db.execute("SELECT id FROM uploads WHERE profile_id=2 ORDER BY id DESC").fetchone()
            job = db.execute("SELECT state, importer FROM jobs WHERE upload_id=?", (up["id"],)).fetchone()
            check("the job is done, read by the Epic MyChart importer", job is not None and tuple(job) == ("done", "epic-mychart"),
                  job and tuple(job))
            js("window.__hook = []; window.sanguityImport = {uploaded: a => window.__hook.push(a)}")
            page.click("#tab-files")
            page.set_input_files("#pickFiles", files=[{"name": n, "mimeType": "text/csv", "buffer": b"date,test,value\n2026-02-01,WBC,6.1\n"}
                                                      for n in ("a.csv", "b.csv")])
            page.click("#go")
            page.wait_for_function("window.__hook.length === 1", timeout=20000)
            hook = js("window.__hook[0]")
            last = db.execute("SELECT id FROM uploads WHERE profile_id=2 ORDER BY id DESC").fetchone()["id"]
            check("after the upload, #20's hook is called with the profile and upload", hook == {"profileId": 2, "uploadId": last}, hook)
            check("…and the page says how many were uploaded", page.inner_text("#msg") == "Uploaded 2 files.", page.inner_text("#msg"))
            db.close()
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("import page fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.click(".import-job a[href='index.html']")   # "See them on the timeline"
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE && PROFILE.name === 'Second person' && "
                                   "TABS.some(t => !document.getElementById('view-' + t).hidden)", timeout=20000)   # the saved view restored
            check("the imported records open on the timeline of the profile they went into", js("state.tab") == "timeline", js("state.tab"))
            check("…where the C-CDA's visits are listed", "Outpatient visit" in page.inner_text("#list"), page.inner_text("#list")[:300])
            listed = page.inner_text("#list")
            check("…and the FHIR export's stay and documents, beside them", "Riverside General Hospital" in listed and "Discharge summary" in listed,
                  listed[:400])
            check("…and the Epic MyChart export's conversation and hospital admission", "Lab question" in listed and "Hospital admission" in listed,
                  listed[:400])
            doc = js("""(async () => { const e = EV.find(e => e.title === 'Discharge summary'), d = e && DATA.details[e.id];
                if (!d || !d.url) return null; const r = await fetch(d.url);
                return {status: r.status, type: r.headers.get('content-type'), pdf: (await r.text()).startsWith('%PDF')}; })()""")
            check("…whose attached PDF, stored with the upload, opens", doc == {"status": 200, "type": "application/pdf", "pdf": True}, doc)
            # the imported claims are on the Costs tab: rows from each insurer, with their care categories and denials
            page.click("#tab-costs")
            settle()
            js("costState.period = 'all'; renderCosts()")
            settle(200)
            rows = js("[...document.querySelectorAll('#costTable tbody tr')].map(tr => [...tr.cells].map(td => td.textContent))")
            by = {(r[0], r[2]): r for r in rows}
            check("Costs tab lists every imported claim (6 UHC, 5 BCBS, 5 from the spreadsheet)", len(rows) == 16, len(rows))
            check("…a UHC claim with its category and your share",
                  by.get(("2026-10-07", "Riverbend Infusion Clinic"), [None] * 8)[3] == "Chemo, infusions & drugs", by.get(("2026-10-07", "Riverbend Infusion Clinic")))
            check("…a denied BCBS claim, marked denied",
                  (by.get(("2026-06-24", "Harbor Physical Therapy")) or [""] * 8)[7] == "denied", by.get(("2026-06-24", "Harbor Physical Therapy")))
            check("…a claim from the spreadsheet, its two service lines added up",
                  (by.get(("2026-02-03", "Valley Reference Laboratory")) or [""] * 8)[3:5] == ["Lab & pathology", "$200"],
                  by.get(("2026-02-03", "Valley Reference Laboratory")))
            check("…and the insurers named", {"UnitedHealthcare", "Blue Cross Blue Shield", "Insurance"} <= set(js("DATA.costs.map(c => c.ins)")))
            page.select_option("#profile", label="My records")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records'", timeout=20000)
            check("switching back restores the profile's tab", js("state.tab") == "timeline", js("state.tab"))
            check("switching back restores the profile's Health period", js("hState.period") == "30d", js("hState.period"))

            # ---- import history (#24): the C-CDA and FHIR imports are listed; undoing the FHIR one takes its records
            # off the timeline and leaves the C-CDA's. The setup build's import can't be undone, and says why.
            page.goto(base + "/import.html?profile=1")
            page.wait_for_selector("#history li[data-import]", timeout=10000)
            check("the setup build's import is listed, with why it can't be undone and no Undo",
                  js("(() => { const li = document.querySelector('#history li[data-import]'); return !!li.querySelector('.note') && "
                     "li.querySelector('.note').textContent.includes('rebuild') && !li.querySelector('button.undo'); })()"))
            page.select_option("#profile", label="Second person")
            page.wait_for_selector("#history li[data-format='fhir']", timeout=10000)
            hist = js("[...document.querySelectorAll('#history li[data-import]')].map(li => li.dataset.format)")
            check("the import history lists the C-CDA, FHIR, claims, CareSpace and Epic imports, newest first",
                  hist == ["epic-mychart", "carespace", "claims-csv", "bcbs", "uhc", "fhir", "ccda"], hist)
            check("…with the uploaded files", "fhir-export/" in page.inner_text("#history li[data-format='fhir'] .files"))
            page.click("#history li[data-format='fhir'] button.undo")
            page.click("#history li[data-format='fhir'] .confirm button.danger")
            page.wait_for_selector("#history li.undone[data-format='fhir']", timeout=10000)
            check("…undoing the FHIR one marks it undone, the C-CDA one still there",
                  js("!document.querySelector(\"#history li[data-format='ccda']\").classList.contains('undone')") and
                  page.inner_text("#historyMsg").startswith("Undone"), page.inner_text("#historyMsg"))
            js("localStorage.setItem('ht-profile', '2')")
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE && PROFILE.name === 'Second person' && "
                                   "TABS.some(t => !document.getElementById('view-' + t).hidden)", timeout=20000)
            js("switchTab('timeline')")   # the claims check left the profile on Costs
            settle(300)
            listed = page.inner_text("#list")
            check("…and the FHIR export's records leave the timeline, the C-CDA's staying",
                  "Riverside General Hospital" not in listed and "Discharge summary" not in listed and "Outpatient visit" in listed, listed[:400])

            # ---- import page, "Type them in" (#27): two results typed in, checked as they're typed, saved, on Trends
            page.goto(base + "/import.html?profile=2")
            page.wait_for_selector("#importer", state="visible", timeout=10000)
            page.click("#tab-manual")
            check("the Type them in tab shows its rows, not the upload controls",
                  js("!document.getElementById('panel-manual').hidden && document.querySelectorAll('.man-row').length === 1 && "
                     "getComputedStyle(document.getElementById('drop')).display === 'none' && "
                     "getComputedStyle(document.getElementById('goRow')).display === 'none'"))
            page.wait_for_function("document.querySelectorAll('#labNames option').length > 10", timeout=5000)
            check("test names are suggested from lab-aliases.json", js("[...document.querySelectorAll('#labNames option')].some(o => o.value === 'Hemoglobin')")
                  and js("document.querySelector('.man-row [data-f=test]').getAttribute('list')") == "labNames")
            row = lambda i, f: f".man-row:nth-child({i}) [data-f={f}]"
            page.fill(row(1, "date"), "2026-11-02")
            page.fill(row(1, "time"), "08:10")
            page.fill(row(1, "test"), "HGB")
            page.fill(row(1, "value"), "12.4")
            page.fill(row(1, "unit"), "g/dL")
            page.fill(row(1, "range"), "13.5 - 17.5")
            page.wait_for_function("document.querySelector('.man-row .man-check').textContent.includes('Hemoglobin')", timeout=5000)
            first = js("document.querySelector('.man-row .man-check').textContent")
            check("a typed row is checked as you go: its canonical name, range and flag", "Hemoglobin" in first and "13.5 – 17.5" in first
                  and "Low" in first, first)
            page.click("#manAdd")
            check("a new row takes the last one's date and time", js(f"document.querySelector('{row(2, 'date')}').value") == "2026-11-02"
                  and js(f"document.querySelector('{row(2, 'time')}').value") == "08:10")
            page.fill(row(2, "test"), "Ferritin")
            page.fill(row(2, "value"), "abc")
            page.wait_for_function("document.querySelectorAll('.man-check')[1].textContent.includes('text result')", timeout=5000)
            check("a value that isn't a number is pointed out", True)
            page.fill(row(2, "value"), "88")
            page.fill(row(2, "unit"), "ng/mL")
            page.fill(row(2, "range"), "30-400")
            page.wait_for_function("document.getElementById('manMsg').textContent === '2 results ready to save.'", timeout=5000)
            # a block of cells pasted from a spreadsheet fills rows of its own; removing them leaves the two typed
            js("""(() => { const dt = new DataTransfer(); dt.setData('text/plain', 'Date\\tTest\\tResult\\tUnits\\n2026-11-02\\tWBC\\t6.1\\tK/uL\\n'
                   + '2026-11-02\\tPlatelets\\t250\\tK/uL\\n'); document.getElementById('manAdd').click();
                   document.querySelector('.man-row:nth-child(3) [data-f=date]').dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true})); })()""")
            check("rows pasted from a spreadsheet fill in by their header", js("[...document.querySelectorAll('.man-row [data-f=test]')].map(i => i.value)")
                  == ["HGB", "Ferritin", "WBC", "Platelets"], js("[...document.querySelectorAll('.man-row [data-f=test]')].map(i => i.value)"))
            page.wait_for_function("document.getElementById('manMsg').textContent === '4 results ready to save.'", timeout=5000)
            page.click(".man-row:nth-child(4) .del")
            page.click(".man-row:nth-child(3) .del")
            page.wait_for_function("document.getElementById('manMsg').textContent === '2 results ready to save.' && !document.getElementById('manSave').disabled", timeout=5000)
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the Type them in tab fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.click("#manSave")
            page.wait_for_function("document.getElementById('manMsg').textContent.startsWith('Saved')", timeout=10000)
            check("the typed results save", page.inner_text("#manMsg").startswith("Saved 2 results."), page.inner_text("#manMsg"))
            db = dbm.connect()
            imp = db.execute("SELECT origin, format FROM imports WHERE profile_id=2 ORDER BY id DESC").fetchone()
            db.close()
            check("…as one import of their own", imp is not None and tuple(imp) == ("manual", "manual"), imp and tuple(imp))
            page.click("#manMsg a")   # "See them on Trends"
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE && PROFILE.name === 'Second person' && "
                                   "!document.getElementById('view-labs').hidden && document.querySelectorAll('#charts .chart').length > 0", timeout=20000)
            typed = js("""SERIES.filter(s => ['Hemoglobin', 'Ferritin'].includes(s.name))
                          .map(s => [s.name, s.pts.filter(p => p[6] === 'Typed in').map(p => [p[1], p[4]])]).filter(x => x[1].length)""")
            check("the typed results are on Trends, charted under their canonical names",
                  sorted(map(tuple, [(n, tuple(map(tuple, p))) for n, p in typed])) == [("Ferritin", ((88, ""),)), ("Hemoglobin", ((12.4, "L"),))], typed)
            charts = page.inner_text("#charts")
            check("…with a chart for each", "Hemoglobin" in charts and "Ferritin" in charts, charts[:300])

            # ---- the other kinds typed in: a visit, a medication, a claim and a letter with a PDF, saved as one import;
            # the visit on the timeline, the claim on Costs, the letter opening in the drawer
            page.goto(base + "/import.html?profile=2")
            page.wait_for_selector("#importer", state="visible", timeout=10000)
            page.click("#tab-manual")
            card = lambda i, f: f".man-item:nth-child({i}) [data-f={f}]"

            def add_record(kind):
                page.select_option("#manKind", kind)
                page.click("#manAddKind")
            add_record("visit")
            check("a record's form opens with its date filled in and the next field focused",
                  js("document.querySelectorAll('.man-item').length === 1 && /^\\d{4}-\\d\\d-\\d\\d$/.test(document.querySelector('.man-item [data-f=date]').value)")
                  and js("document.activeElement.dataset.f") == "time", js("document.activeElement.dataset.f"))
            page.fill(card(1, "date"), "2026-11-03")
            page.fill(card(1, "date"), "2026-13-03")
            page.fill(card(1, "title"), "Typed follow-up visit")
            page.wait_for_function("document.querySelector('.man-item [data-f=date]').getAttribute('aria-invalid') === 'true'", timeout=5000)
            check("a field that can't be read is marked, with why", "couldn't be read" in js("document.querySelector('.man-item .man-err').textContent")
                  and page.is_disabled("#manSave"), js("document.querySelector('.man-item .man-err').textContent"))
            page.fill(card(1, "date"), "2026-11-03")
            page.fill(card(1, "who"), "Dr. Ada Quill (fictional)")
            page.fill(card(1, "where"), "Lakeside Clinic (fictional)")
            add_record("med")
            page.fill(card(2, "name"), "Typed ferrous sulfate 325 mg")
            page.fill(card(2, "instructions"), "1 tablet daily")
            add_record("claim")
            page.fill(card(3, "provider"), "Lakeside Clinic (fictional)")
            page.fill(card(3, "title"), "Typed office visit claim")
            page.fill(card(3, "billed"), "$250.00")
            page.fill(card(3, "paid"), "200")
            page.fill(card(3, "owe"), "50")
            add_record("document")
            page.fill(card(4, "title"), "Typed referral letter")
            page.select_option(card(4, "doctype"), "letter")
            pdf_path = os.path.join(TMP, "typed-letter.pdf")
            with open(pdf_path, "wb") as f:   # a one-page PDF with fictional text
                body = b"BT /F1 12 Tf 72 720 Td (Fictional referral letter) Tj ET"
                objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
                        b"<< /Length %d >>\nstream\n" % len(body) + body + b"\nendstream", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
                out, offs = b"%PDF-1.4\n", []
                for i, o in enumerate(objs):
                    offs.append(len(out))
                    out += b"%d 0 obj\n" % (i + 1) + o + b"\nendobj\n"
                xref = len(out)
                out += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(b"%010d 00000 n \n" % o for o in offs)
                out += b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % xref
                f.write(out)
            page.set_input_files(card(4, "file"), pdf_path)
            page.wait_for_function("document.querySelector('.man-item:nth-child(4) .man-file').textContent.includes('uploaded')", timeout=10000)
            page.wait_for_function("document.getElementById('manMsg').textContent === '4 records ready to save.' && !document.getElementById('manSave').disabled", timeout=5000)
            checks = js("[...document.querySelectorAll('.man-item .man-check')].map(c => c.textContent)")
            check("each record says what it became", "Typed follow-up visit" in checks[0] and "Doctor visits" in checks[2]
                  and "typed-letter.pdf" in checks[3], checks)
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the record forms fit 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.click("#manSave")
            page.wait_for_function("document.getElementById('manMsg').textContent.startsWith('Saved')", timeout=10000)
            check("the typed records save as one import", page.inner_text("#manMsg").startswith("Saved 4 records."), page.inner_text("#manMsg"))
            page.click("#manMsg a")   # "See them on the timeline"
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE && PROFILE.name === 'Second person' && "
                                   "!document.getElementById('view-timeline').hidden", timeout=20000)
            typed = js("['Typed follow-up visit', 'Typed ferrous sulfate 325 mg', 'Typed referral letter'].map(t => (EV.find(e => e.title === t) || {}).k)")
            check("the visit, the medication and the letter are on the timeline", typed == ["visit", "med", "letter"], typed)
            js("openEvent(EV.find(e => e.title === 'Typed referral letter'))")
            page.wait_for_selector("#drawer.open #dbody iframe.doc", timeout=5000)
            src = js("document.querySelector('#dbody iframe.doc').getAttribute('src')")
            got = js(f"fetch({json.dumps(src.split('#')[0])}).then(async r => [r.status, r.headers.get('content-type'), (await r.text()).slice(0, 8)])")
            check("…and the letter's PDF opens in the drawer", "/documents/" in src and got == [200, "application/pdf", "%PDF-1.4"], [src, got])
            js("closeDrawer(); switchTab('costs')")
            page.wait_for_function("!document.getElementById('view-costs').hidden", timeout=5000)
            js("costState.period = 'all'; renderCosts()")
            settle(300)
            costs = page.inner_text("#view-costs")
            check("the typed claim is on Costs, under its care category", "Typed office visit claim" in costs and "Doctor visits" in costs, costs[:300])

            # ---- lab names matched on import (#23): a spelling lab-aliases.json doesn't know is suggested the profile's
            # Hemoglobin series at the preview; accepted, its point is charted with Hemoglobin; uploaded again, it's
            # matched as before without being asked
            def upload_csv(name, body):
                page.goto(base + "/import.html?profile=2")
                page.wait_for_selector("#importer", state="visible", timeout=10000)
                page.click("#tab-files")
                page.set_input_files("#pickFiles", files=[{"name": name, "mimeType": "text/csv", "buffer": body}])
                page.wait_for_function("!document.getElementById('go').disabled", timeout=5000)
                page.click("#go")
                page.wait_for_function(f"document.querySelectorAll('.import-job').length && "
                                       f"/^(Ready to import|All duplicates|Not imported)/.test({last_card}.textContent)", timeout=30000)
            lab_csv = "Date,Test,Result,Units,Reference Range\n{},Hemoglobin Whole Bld,{},g/dL,13.5 - 17.5\n"
            upload_csv("other-lab.csv", lab_csv.format("2026-11-09", "12.9").encode())
            labs = js(f"""(() => {{ const li = {last_card}.querySelector('.imp-labs li'); if (!li) return null;
                const sel = li.querySelector('select'); return {{text: li.textContent, value: sel.value,
                chosen: sel.selectedOptions[0].textContent, options: sel.options.length}}; }})()""")
            check("the preview lists the unknown lab name, with the profile's Hemoglobin series suggested and chosen",
                  labs is not None and "Hemoglobin Whole Bld" in labs["text"] and labs["value"] == "Hemoglobin"
                  and "similar name, same unit" in labs["chosen"] and labs["options"] > 3, labs)
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the preview with lab names to match fits 375 px", js("document.documentElement.scrollWidth") <= 375,
                  js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            js(f"{last_card}.querySelector('.imp-actions .primary').click()")   # Save, accepting the suggestion
            page.wait_for_function(f"/^(Imported|Not imported)/.test({last_card}.textContent)", timeout=30000)
            card = js(f"{last_card}.textContent")
            check("…saved, saying the name went into an existing series", card.startswith("Imported.") and "1 lab name put in an existing series" in card, card)
            page.goto(base + "/")
            page.wait_for_function("typeof SERIES !== 'undefined' && SERIES.length > 0 && PROFILE && PROFILE.name === 'Second person'", timeout=20000)
            js("switchTab('labs')")
            settle(300)
            hgb = js("SERIES.filter(s => s.name === 'Hemoglobin').flatMap(s => s.pts.map(p => [p[0].slice(0, 10), p[1]]))")
            check("…and its result is on Trends in the existing Hemoglobin series",
                  ["2026-11-09", 12.9] in hgb and not js("SERIES.some(s => s.name === 'Hemoglobin Whole Bld')"), hgb)
            check("…charted there", "Hemoglobin" in page.inner_text("#charts"), page.inner_text("#charts")[:200])
            upload_csv("other-lab-again.csv", lab_csv.format("2026-11-12", "13.2").encode())
            again = js(f"(() => {{ const li = {last_card}.querySelector('.imp-labs li'); return li && [li.textContent, li.querySelector('select').value]; }})()")
            check("uploaded again, the name is matched as before", again is not None and "Matched as before" in again[0] and again[1] == "Hemoglobin", again)
            js(f"{last_card}.querySelector('.imp-actions .primary').click()")
            page.wait_for_function(f"/^(Imported|Not imported)/.test({last_card}.textContent)", timeout=30000)
            check("…and saved into Hemoglobin again", js(f"{last_card}.textContent").startswith("Imported."))

            # ---- a SMART Health Link pasted (#94): its host, label and passcode flag are shown and nothing is fetched;
            # Fetch and preview fetches it once and shows the import's preview, which saves like an upload
            import shl_server
            SHL.seen.clear()
            link = SHL.share(shl_server.bundle("2026-11-10", 13.9), passcode="fictional-2468", label="Fictional clinic",
                             viewer="https://viewer.example.org/")
            page.goto(base + "/import.html?profile=2")
            page.wait_for_selector("#importer", state="visible", timeout=10000)
            page.click("#tab-link")
            check("the Paste a link tab hides the file drop", js("!document.getElementById('panel-link').hidden && getComputedStyle(document.getElementById('drop')).display === 'none'"))
            page.fill("#shlText", link)
            page.wait_for_selector("#shlAbout", state="visible", timeout=5000)
            facts = page.inner_text("#shlFacts")
            check("the pasted link's host, label and passcode flag are shown", f"localhost:{SHL.port}" in facts and "Fictional clinic" in facts
                  and "needed" in facts and js("!document.getElementById('shlPassRow').hidden"), facts)
            check("…and nothing was fetched from it", SHL.seen == [], SHL.seen)
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the Paste a link tab fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.fill("#shlPass", "fictional-2468")
            page.click("#shlGo")
            page.wait_for_function(f"document.querySelectorAll('.import-job').length && /^(Ready to import|All duplicates|Not imported)/.test({last_card}.textContent)", timeout=30000)
            card = js(f"{last_card}.textContent")
            check("Fetch and preview fetches it once and shows the preview", card.startswith("Ready to import") and "FHIR" in card
                  and len(SHL.seen) == 1, (card[:200], len(SHL.seen)))
            js(f"{last_card}.querySelector('.imp-actions .primary').click()")
            page.wait_for_function(f"/^(Imported|Not imported)/.test({last_card}.textContent)", timeout=30000)
            check("…and saves it", js(f"{last_card}.textContent").startswith("Imported."), js(f"{last_card}.textContent")[:200])
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE && PROFILE.name === 'Second person'", timeout=20000)
            page.select_option("#profile", label="My records")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records'", timeout=20000)

            # ---- Settings (#35): change settings on the screen, and the dashboard follows; reset a section
            tiles = "[...document.querySelectorAll('#view-overview .tiles .tile .lbl span[title]')].map(s => [s.title, s.textContent])"
            js("switchTab('overview')")
            settle(300)
            before = js(tiles)
            check("the header links to Settings for this profile", js("document.getElementById('settingsLink').getAttribute('href')") == "settings.html?profile=1")
            page.click("#settingsLink")
            page.wait_for_selector("#sec-overview .set-labs li", timeout=10000)
            check("Settings shows every section", js("[...document.querySelectorAll('#sections > section h2')].map(h => h.textContent)") ==
                  ["Overview", "Trends", "Names", "Health"], js("[...document.querySelectorAll('#sections > section h2')].map(h => h.textContent)"))
            first = before[0][0]
            page.click(f"#sec-overview button[aria-label='Move {first} down']")
            page.click("#sec-overview .set button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-overview > .msg').textContent.startsWith('Saved')", timeout=10000)
            page.click("[data-key=labNicknames] button:text-is('Add a row')")
            page.fill("[data-key=labNicknames] .set-row:last-child input[data-k=from]", first)
            page.fill("[data-key=labNicknames] .set-row:last-child input[data-k=to]", "Renamed in Settings")
            page.click("[data-key=labNicknames] button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-names > .msg').textContent.startsWith('Saved')", timeout=10000)
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the Settings page fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records'", timeout=20000)
            js("switchTab('overview')")
            settle(300)
            after = js(tiles)
            check("…the Overview tiles follow the new order and name",
                  [t for t, _ in after[:2]] == [before[1][0], first] and after[1][1] == "Renamed in Settings", after[:3])
            page.goto(base + "/settings.html?profile=1")
            page.wait_for_selector("#sec-overview .set-labs li", timeout=10000)
            for sec in ("overview", "names"):
                page.once("dialog", lambda d: d.accept())
                page.click(f"#sec-{sec} button:text-is('Reset to defaults')")
                page.wait_for_function(f"document.querySelector('#sec-{sec} > .msg').textContent.includes('back to their defaults')", timeout=10000)
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records'", timeout=20000)
            js("switchTab('overview')")
            settle(300)
            check("…and Reset to defaults brings them back", js(tiles) == before, js(tiles)[:3])
            # ---- connected sources (#32, #91), against the fake SMART on FHIR server standing in for an Epic sandbox:
            # an admin sets the source up and tests it, the profile connects by signing in, the first pull is previewed
            # and saved, Sync now brings a new result, and when the patient ends access the page asks to reconnect
            from tests.fakefhir import FakeFHIR
            fake = FakeFHIR(patients=["robin-example"]).start()
            fake.signin_patient = "pat-1"   # who "signs in" at the fake's authorize page
            fake.register_client("sanguity-browser-check", [base + "/connect/return"])
            allowed.add(origin(fake.fhir_base))   # its sign-in page, which the browser visits
            page.goto(base + "/connect.html")
            page.wait_for_selector("#sourceList", state="attached")
            page.wait_for_function("document.getElementById('returnAddr').textContent.length > 5 && !document.getElementById('admin').hidden", timeout=10000)
            check("connected sources: the setup screen gives the exact return address to register",
                  js("document.getElementById('returnAddr').textContent") == base + "/connect/return", js("document.getElementById('returnAddr').textContent"))
            check("…and warns that a plain-HTTP, LAN-only address is unlikely to be accepted",
                  "plain HTTP" in page.inner_text("#addrWarn") and "own network" in page.inner_text("#addrWarn"), page.inner_text("#addrWarn"))
            check("…with a guide per vendor: where to register, and the scopes", "fhir.epic.com" in page.inner_text("#guide")
                  and "patient/Observation" in js("document.getElementById('fScopes').value"))
            page.fill("#fName", "Fake Epic sandbox")
            page.fill("#fIssuer", fake.fhir_base)
            page.fill("#fClient", "sanguity-browser-check")
            page.click("#fSave")
            page.wait_for_function("document.getElementById('sourceList').textContent.includes('Fake Epic sandbox')", timeout=10000)
            page.locator("#sourceList .prof", has_text="Fake Epic sandbox").get_by_role("button", name="Test this connection").click()
            page.wait_for_function("document.getElementById('sourceList').textContent.includes('Discovery works') || document.getElementById('sourceList').textContent.includes('tested ')", timeout=10000)
            page.reload()
            page.select_option("#profile", label="Second person")
            page.wait_for_selector("#sourceButtons a", timeout=10000)
            conn_pid = js("+document.getElementById('profile').value")
            page.click("#sourceButtons a")   # to the fake's sign-in, which approves and sends the browser back
            page.wait_for_url("**/connect.html**", timeout=20000)
            page.wait_for_selector("#import-status button.primary", timeout=30000)   # the first pull's preview
            check("signing in connects the source, and its first pull is previewed like an upload",
                  "Ready to import" in page.inner_text("#import-status"), page.inner_text("#import-status")[:300])
            page.click("#import-status button.primary")
            page.wait_for_function("document.getElementById('import-status').textContent.includes('Imported.')", timeout=30000)
            page.wait_for_function("document.getElementById('connList').textContent.includes('New records come in on a schedule')", timeout=20000)
            check("…saved, and the connection then syncs on a schedule", True)
            imports = lambda: js(f"fetch('api/profiles/{conn_pid}/connections').then(r => r.json()).then(j => j.connections[0].imports)")
            fake.add({"resourceType": "Observation", "status": "final", "effectiveDateTime": "2026-11-12T08:00:00Z",
                      "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "laboratory"}]}],
                      "code": {"coding": [{"system": "http://loinc.org", "code": "2345-7"}], "text": "Glucose"},
                      "valueQuantity": {"value": 101, "unit": "mg/dL"}}, patient="pat-1")
            page.get_by_role("button", name="Sync now").click()
            for _ in range(100):
                if imports() == 2:
                    break
                page.wait_for_timeout(200)
            check("Sync now brings the new result in, as an import of its own", imports() == 2, imports())
            page.set_viewport_size({"width": 375, "height": 800})
            page.reload()
            page.wait_for_function("document.getElementById('connList').textContent.includes('Fake Epic sandbox')", timeout=10000)
            check("connected sources fit 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            fake.revoke_all("pat-1")    # the patient ends access in their portal
            fake.expire_access_tokens()
            page.reload()
            page.get_by_role("button", name="Sync now").click()
            page.wait_for_timeout(1500)
            page.reload()
            page.wait_for_selector("#reconnect:not([hidden])", timeout=20000)
            check("when access ends, the page asks to sign in again", "access ended" in page.inner_text("#reconnect")
                  and page.get_by_role("button", name="Sign in again").count() == 1, page.inner_text("#connList")[:300])
            check("only the source's own host was contacted", fake.hosts() == {f"127.0.0.1:{fake.port}"}, fake.hosts())

            # ---- outside connections (#97, #59): Settings lists every host the install may contact, by purpose
            page.goto(base + "/settings.html#outside")
            page.wait_for_function("document.getElementById('outsideBody').textContent.includes('May contact now')", timeout=10000)
            out = page.inner_text("#outside")
            check("Settings says plainly that nothing else is contacted", "Nothing else leaves this server" in out, out[:200])
            check("…lists the connected source with its host, scopes and last contact",
                  "Fake Epic sandbox" in out and f"127.0.0.1:{fake.port}" in out and "patient/Observation" in out
                  and "Second person" in out and "Last contact" in out, out[:600])
            check("…and the Health Link's host", f"localhost:{SHL.port}" in out and "SMART Health Link" in out, out[-600:])
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the outside connections fit 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            allowed.discard(origin(fake.fhir_base))
            fake.stop()

            # ---- insurers (#92): a Blue Button bulk export (NDJSON) uploaded on the import page, then a health plan's
            # patient-access API (the fake, standing in for Blue Button's sandbox: a confidential client) set up as an
            # insurer source and connected; both bring their claims onto the Costs tab
            from tests.fakefhir import PAYER_FIXTURES
            bb_dir = os.path.join(ROOT, "tests", "fixtures", "importers", "fhir", "blue-button")
            page.goto(base + f"/import.html?profile={conn_pid}")
            page.wait_for_selector("#importer", state="visible", timeout=10000)
            page.click("#tab-files")
            bb_files = []
            for n in sorted(os.listdir(bb_dir)):
                with open(os.path.join(bb_dir, n), "rb") as f:
                    bb_files.append({"name": n, "mimeType": "application/x-ndjson", "buffer": f.read()})
            page.set_input_files("#pickFiles", files=bb_files)
            page.wait_for_function("!document.getElementById('go').disabled", timeout=5000)
            cards = js("document.querySelectorAll('.import-job').length")
            page.click("#go")
            page.wait_for_function(f"document.querySelectorAll('.import-job').length > {cards} && "
                                   f"/^(Ready to import|All duplicates|Not imported)/.test({last_card}.textContent)", timeout=30000)
            js(f"{last_card}.querySelector('.imp-actions .primary')?.click()")
            page.wait_for_function(f"/^(Imported|Not imported)/.test({last_card}.textContent)", timeout=30000)
            card = js(f"{last_card}.textContent")
            db = dbm.connect()
            up = db.execute("SELECT id FROM uploads WHERE profile_id=? ORDER BY id DESC", (conn_pid,)).fetchone()
            job = db.execute("SELECT state, importer FROM jobs WHERE upload_id=?", (up["id"],)).fetchone()
            db.close()
            check("a Blue Button export (NDJSON) uploads and is imported, as FHIR, with its claims",
                  card.startswith("Imported.") and job is not None and tuple(job) == ("done", "fhir") and "claim" in card, (card, job and tuple(job)))

            payer = FakeFHIR(fixtures=PAYER_FIXTURES).start()
            allowed.add(origin(payer.fhir_base))   # its sign-in page too
            payer.signin_patient = "mem-1"
            payer.register_client("sanguity-payer-check", [base + "/connect/return"], secret="fictional-payer-secret")
            page.goto(base + "/connect.html")
            page.wait_for_function("!document.getElementById('admin').hidden && document.getElementById('returnAddr').textContent.length > 5", timeout=10000)
            page.select_option("#fVendor", "insurer")
            guide_text = page.inner_text("#guide")
            check("an insurer source has its own guide: CARIN Blue Button scopes, Blue Button's sandbox, payers' portals marked unverified",
                  "Blue Button" in guide_text and "patient/ExplanationOfBenefit.read" in guide_text and "unverified" in guide_text
                  and "sandbox.bluebutton.cms.gov" in guide_text, guide_text[:400])
            check("…and its scopes ask for claims, coverage and the patient only",
                  js("document.getElementById('fScopes').value") == js("SETUP.insurer_scopes")
                  and "Observation" not in js("document.getElementById('fScopes').value"), js("document.getElementById('fScopes').value"))
            page.fill("#fName", "Fake health plan")
            page.fill("#fIssuer", payer.fhir_base)
            page.fill("#fClient", "sanguity-payer-check")
            page.select_option("#fAuth", "confidential")
            page.fill("#fSecret", "fictional-payer-secret")
            page.click("#fSave")
            page.wait_for_function("document.getElementById('sourceList').textContent.includes('Fake health plan')", timeout=10000)
            page.locator("#sourceList .prof", has_text="Fake health plan").get_by_role("button", name="Test this connection").click()
            page.wait_for_function("[...document.querySelectorAll('#sourceList .prof')].some(p => p.textContent.includes('Fake health plan') && "
                                   "(p.textContent.includes('Discovery works') || p.textContent.includes('tested ')))", timeout=10000)
            page.reload()
            page.select_option("#profile", label="Second person")
            page.wait_for_selector("#sourceButtons a:has-text('Fake health plan')", timeout=10000)
            page.click("#sourceButtons a:has-text('Fake health plan')")
            page.wait_for_url("**/connect.html**", timeout=20000)
            page.wait_for_selector("#import-status button.primary", timeout=30000)
            check("connecting the health plan previews its claims like an upload",
                  "Ready to import" in page.inner_text("#import-status") and "claim" in page.inner_text("#import-status"),
                  page.inner_text("#import-status")[:300])
            page.click("#import-status button.primary")
            page.wait_for_function("document.getElementById('import-status').textContent.includes('Imported.')", timeout=30000)
            check("…pulling only Patient, ExplanationOfBenefit and Coverage",
                  {r.path for r in payer.requests if r.path.startswith("/fhir/") and r.path != "/fhir/metadata" and ".well-known" not in r.path}
                  == {"/fhir/Patient/mem-1", "/fhir/ExplanationOfBenefit", "/fhir/Coverage"},
                  sorted({r.path for r in payer.requests}))
            prev_profile = js("localStorage.getItem('ht-profile')")   # put back after: later checks rely on it
            js(f"localStorage.setItem('ht-profile', '{conn_pid}')")
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && PROFILE && PROFILE.name === 'Second person' && "
                                   "TABS.some(t => !document.getElementById('view-' + t).hidden)", timeout=20000)
            js("switchTab('costs')")
            page.wait_for_function("!document.getElementById('view-costs').hidden", timeout=5000)
            js("costState.period = 'all'; renderCosts()")
            settle(200)
            rows = js("[...document.querySelectorAll('#costTable tbody tr')].map(tr => [...tr.cells].map(td => td.textContent))")
            ins = set(js("DATA.costs.map(c => c.ins)"))
            check("the Costs tab shows the claims from the Blue Button export and from the health plan's API",
                  {"Medicare (fictional sandbox)", "Lakeview Health Plan (fictional)"} <= ins, sorted(ins))
            mri = [r for r in rows if r[1] == "MRI lumbar spine without contrast"]
            check("…a denied claim from each, marked denied", len(mri) == 2 and all(r[7] == "denied" for r in mri), mri)
            fills = [r for r in rows if r[1] == "Atorvastatin Calcium 20mg tablet"]
            check("…and the pharmacy fills, under Pharmacy", len(fills) == 2 and all(r[3] == "Pharmacy" for r in fills), fills)
            check("only the health plan's own host was contacted for it", payer.hosts() == {f"127.0.0.1:{payer.port}"}, payer.hosts())
            allowed.discard(origin(payer.fhir_base))
            payer.stop()
            js(f"localStorage.setItem('ht-profile', {json.dumps(prev_profile or '')})" if prev_profile else "localStorage.removeItem('ht-profile')")

            # ---- account page: password, devices, profiles with sharing, accounts (admin)
            page.goto(base + "/account.html")
            page.wait_for_function("document.querySelectorAll('#sessList tbody tr').length > 0 && document.querySelectorAll('#profList .prof').length === 2 && !document.getElementById('users').hidden", timeout=10000)
            check("account page lists devices, profiles and accounts", True)
            page.wait_for_function("document.querySelector('#labmapList tr[data-labmap]')", timeout=10000)
            lm = js("[...document.querySelectorAll('#labmapList tr[data-labmap]')].map(tr => tr.textContent)")
            check("the admin tab lists the lab name match, who made it and from what, with Change and Remove (#23)",
                  any("Hemoglobin Whole Bld" in t and "Hemoglobin" in t and "demo" in t and "CSV" in t and "Change" in t and "Remove" in t for t in lm)
                  and "only admins can change or remove" in page.inner_text("#labmaps"), lm)
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
    SHL.stop()
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
