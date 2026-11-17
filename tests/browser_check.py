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
FHIR server (tests/fakefhir), connects a profile by signing in with a choice of what to pull, saves its first pull's
preview, sees the attachments the install's rules left out and fetches one anyway, syncs a new result, changes what
the connection pulls, and is asked to reconnect when access ends, with a warning on the profile's main page, uploads a
Blue Button export and connects a health plan's patient-access API (the fake again) and finds both sets of claims on
Costs, sees the source's and the Health Link's hosts under Settings → Outside connections, changes settings on the
Settings page and resets them (a milestone added there marked on Trends and a "since" period on Health; a
workout rule, a catastrophic pattern refused first, counted on Health; drags and keyboard-moves the key-lab tiles, hides and
reorders tabs, sets a default theme a fresh browser picks up), builds a visit summary (sections, a lab, questions
kept as a draft), previews it and downloads the PDF, reading it back, has two accounts in different time zones each see days split at their own midnight and
dates in their own format (#42), makes the profile's watch receiver secret on the Settings page, shown once and then
masked (#46), opens the account
page (where the match is listed for admins), sees each record's source in the drawer, on Trends and on the heatmap, links the
same result from a file and the sync at the first pull's preview and sees it once on Trends and in the drawer (undone
and linked again there)
and follows its link to the import history, checks the "not medical advice" notice on every tab and beside
out-of-range highlighting, and fails
on any script error, console error, failed request, request to another host or NaN written into an SVG attribute.

  pip install -r requirements-dev.txt && playwright install chromium
  python3 tests/browser_check.py
"""
import json, os, re, shutil, socket, subprocess, sys, tempfile, threading, time, urllib.request

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-browser-")
USER, PW = "demo", "a long demo passphrase"
USER2, PW2 = "pat", "another long demo passphrase"   # a second account, viewing profile 1 from another time zone (#42)
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
                      SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                      SANGUITY_WATCH_KEYS=os.path.join(TMP, "watch-keys", "receiver-secrets.json"))
    os.makedirs(os.path.join(TMP, "watch-keys"), exist_ok=True)
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
        check("under the CSP: settings.js runs", strict.evaluate("document.querySelectorAll('#sections > section').length") == 9)
        strict.wait_for_selector("#watch h2", timeout=10000)
        check("under the CSP: the watch card shows the upload address", strict.evaluate("document.querySelector('#watch .w-addr').textContent").endswith(":8731/ingest"))
        strict.goto(base + "/summary.html")   # the visit summary page (#48)
        strict.wait_for_selector("#build:not([hidden]) #sections input", timeout=10000)
        check("under the CSP: summary.js runs", strict.evaluate("document.querySelectorAll('#sections input').length") == 5)
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
        try:   # booted: switchTab has run (EV is set before the watch data loads)
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && typeof booted !== 'undefined' && booted", timeout=20000)
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

            # ---- the journal (#49): a symptom added from an event's drawer, pre-dated to it, shown in the Journal lane
            # and as a marker on Trends sized by its severity; the list with its filters; deleted again at the end
            check("the journal starts empty", js("JOURNAL.length === 0 && !document.getElementById('journalBtn').hidden"))
            day_of = js("(() => { const e = EV.find(e => e.k === 'visit' && !e.hidden && /T\\d\\d:\\d\\d/.test(e.t)); openEvent(e); return {id: e.id, day: isoDay(e.ts)}; })()")
            settle(200)
            page.click("#dbody .jadd button")
            check("“Add a journal note” from an event's drawer opens the form dated to it",
                  js("document.getElementById('dtitle').textContent") == "New journal entry" and page.input_value("#dbody input[type=date]") == day_of["day"],
                  page.input_value("#dbody input[type=date]"))
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the journal form fits 375 px", js("document.documentElement.scrollWidth <= 375 && document.getElementById('dbody').scrollWidth <= document.getElementById('dbody').clientWidth"),
                  js("[document.documentElement.scrollWidth, document.getElementById('dbody').scrollWidth]"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.focus("#dbody .jkinds button[aria-checked=true]")   # the kind by keyboard: Note -> Symptom
            page.keyboard.press("ArrowRight")
            check("the kind is chosen with the arrow keys", js("document.activeElement.dataset.kind") == "symptom" and page.is_visible("#dbody input[type=range]"))
            page.fill("#dbody .jform input[type=text]", "Headache (browser check)")
            page.focus("#dbody input[type=range]")
            page.keyboard.press("Home")
            for _ in range(7):
                page.keyboard.press("ArrowRight")
            check("severity set by keyboard, said in words", page.inner_text("#dbody output") == "7/10 · severe", page.inner_text("#dbody output"))
            meds = js("[...document.querySelectorAll('#dbody select option')].map(o => o.value).filter(Boolean)")
            if meds:
                page.select_option("#dbody .jform select", meds[0])
            page.fill("#dbody textarea", "Started after lunch (fictional)")
            page.click("#dbody .jform button[type=submit]")
            page.wait_for_function("typeof JOURNAL !== 'undefined' && JOURNAL.length === 1 && document.getElementById('dtitle').textContent === 'Headache (browser check)'", timeout=20000)
            settle()
            entry = js("({id: JOURNAL[0].id, day: isoDay(JOURNAL[0].ts), sev: JOURNAL[0].j.severity, med: JOURNAL[0].j.med, kind: JOURNAL[0].j.kind})")
            check("the symptom is saved, dated to the event, and opens in the drawer",
                  entry["day"] == day_of["day"] and entry["sev"] == 7 and entry["kind"] == "symptom" and entry["med"] == (meds[0] if meds else None)
                  and "7/10" in page.inner_text("#dbody"), entry)
            page.click("#tab-timeline")
            js("id => setRange(byId.get(id).ts - 20 * DAY, byId.get(id).ts + 20 * DAY)", entry["id"])
            settle()
            lane = js("""(() => { const m = document.querySelector('#tl .jmark'); return {lane: [...document.querySelectorAll('#tl .lane-label')].some(t => t.textContent === 'Journal'),
                n: document.querySelectorAll('#tl .jmark').length, r: m && +m.getAttribute('r'), shape: m && m.tagName}; })()""")
            check("…on the timeline in its own Journal lane, sized by severity", lane["lane"] and lane["n"] == 1 and lane["shape"] == "circle" and abs(lane["r"] - 6.15) < 0.01, lane)
            check("…and in the event list", "Headache (browser check)" in page.inner_text("#list"))
            page.click("#tab-labs")
            settle()
            if not js("document.querySelectorAll('#charts .chart svg').length"):
                js("state.sel.add(SERIES.find(s => !s.watch && s.n >= 2).name); renderLabs()")
                settle()
            marks = js("[...document.querySelectorAll('#charts .jmark')].map(m => m.getAttribute('aria-label'))")
            charts = js("document.querySelectorAll('#charts .chart svg').length")
            check("…and as a marker under every Trends chart, labelled with its severity", len(marks) == charts > 0 and all("severity 7/10" in m and "Headache" in m for m in marks), marks[:2])
            check("the Trends legend says what the marker sizes mean", "bigger = more severe" in page.inner_text("#view-labs .legend"))
            page.focus("#charts .jmark")
            page.keyboard.press("Enter")
            settle(200)
            check("a Trends marker opens its entry from the keyboard", js("document.getElementById('dtitle').textContent") == "Headache (browser check)")
            page.keyboard.press("Escape")
            check("…and Escape takes focus back to the marker", js("document.activeElement.classList.contains('jmark')"))
            page.uncheck("#optJournal")
            settle(200)
            check("the Journal box on Trends hides the markers", js("document.querySelectorAll('#charts .jmark').length") == 0)
            page.check("#optJournal")
            page.click("#journalBtn")
            settle(200)
            check("the Journal button lists the entries", js("document.getElementById('dtitle').textContent") == "Journal" and page.locator("#dbody .jrow").count() == 1)
            page.select_option("#dbody .jfilters select", "note")
            check("…filtered by kind", page.locator("#dbody .jrow").count() == 0 and "No entries match" in page.inner_text("#dbody .jlist"))
            page.select_option("#dbody .jfilters select", "symptom")
            page.fill("#dbody .jfilters input[type=search]", "after lunch")
            check("…and by what they say", page.locator("#dbody .jrow").count() == 1)
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the journal list fits 375 px", js("document.documentElement.scrollWidth <= 375 && document.getElementById('dbody').scrollWidth <= document.getElementById('dbody').clientWidth"),
                  js("[document.documentElement.scrollWidth, document.getElementById('dbody').scrollWidth]"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.click("#dbody .jrow")
            settle(200)
            page.click("#dbody .btns button:text-is('Delete')")
            page.click("#dbody .btns button.primary:text-is('Delete')")
            page.wait_for_function("typeof JOURNAL !== 'undefined' && EV.length > 0 && JOURNAL.length === 0 && document.getElementById('dtitle').textContent === 'Journal'", timeout=20000)
            check("deleting the entry takes it off the timeline and Trends", js("document.querySelectorAll('#charts .jmark, #tl .jmark').length") == 0)
            page.keyboard.press("Escape")
            page.click("#tab-timeline")
            settle()

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

            # ---- saved Trends views (#37): save what's charted as a view, switch between views, make one the default
            # (Trends starts with it when this browser has chosen nothing), on Settings too, at 375 px; deleted again
            kept = js("localStorage.getItem(stateKey())")
            labs3 = js("SERIES.filter(s => !s.watch && s.n >= 2).slice(0, 3).map(s => s.name)")
            picked = lambda: sorted(js("[...state.sel]"))
            js(f"state.sel = new Set({json.dumps(labs3[:2])}); renderLabs()")
            page.click("#viewSaveOpen")
            page.fill("#viewName", "CBC (browser check)")
            page.press("#viewName", "Enter")
            page.wait_for_function("document.getElementById('viewPick') && document.getElementById('viewPick').value === 'CBC (browser check)'", timeout=10000)
            check("a view is saved from what's charted, and shown as the current one",
                  js("CFG.trends.views.map(v => [v.name, v.labs])") == [["CBC (browser check)", labs3[:2]]]
                  and "Saved “CBC (browser check)”" in page.inner_text("#tvBox"), page.inner_text("#tvBox"))
            page.click(f"#series input#s_{re.sub(r'[^A-Za-z0-9_]', '_', labs3[2])}")
            settle(200)
            check("…ticking another lab leaves the view (the picker says so)", js("document.getElementById('viewPick').value") == "")
            js(f"state.sel = new Set({json.dumps(labs3[2:])}); renderLabs()")
            page.click("#viewSaveOpen")
            page.fill("#viewName", "Kidney (browser check)")
            page.click("#viewSave")
            page.wait_for_function("CFG.trends.views.length === 2", timeout=10000)
            page.select_option("#viewPick", "CBC (browser check)")
            settle(300)
            charted = js("[...document.querySelectorAll('#charts .chart .name')].map(n => n.textContent)")
            check("picking a view charts its labs", picked() == sorted(labs3[:2]) and sorted(charted) == sorted(labs3[:2]), charted)
            page.click("#tvBox button:text-is('Make default')")
            page.wait_for_function("CFG.trends.views.some(v => v.default && v.name === 'CBC (browser check)')", timeout=10000)
            check("…and making it the default marks it", js("document.querySelector('#tvBox [aria-pressed=true]').textContent") == "Default ✓"
                  and "(default)" in js("document.getElementById('viewPick').selectedOptions[0].textContent"))
            page.select_option("#viewPick", "Kidney (browser check)")
            settle(200)
            check("switching views charts the other's labs", picked() == labs3[2:3])
            js("localStorage.removeItem(stateKey())")   # nothing chosen in this browser: Trends starts with the default view
            page.reload()
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0", timeout=20000)
            js("switchTab('labs')")
            settle(300)
            check("with nothing chosen in this browser, Trends starts with the default view",
                  picked() == sorted(labs3[:2]) and js("document.getElementById('viewPick').value") == "CBC (browser check)", picked())
            saved = js("api('profiles/' + PROFILE.id + '/settings').then(r => r.json()).then(j => j.trends.views.map(v => [v.name, v.default]))")
            check("…the views are kept by the server, for the profile", saved == [["CBC (browser check)", True], ["Kidney (browser check)", False]], saved)
            page.set_viewport_size({"width": 375, "height": 800})
            page.click("#viewSaveOpen")
            settle(200)
            check("the saved-views bar, with its form open, fits 375 px",
                  js("document.documentElement.scrollWidth <= 375 && document.getElementById('series').scrollWidth <= document.getElementById('series').clientWidth"),
                  js("[document.documentElement.scrollWidth, document.getElementById('series').scrollWidth]"))
            page.goto(base + "/settings.html?profile=1")
            page.wait_for_selector("[data-key='trends.views'] .set-view", timeout=10000)
            check("Settings lists the saved views, the default one ticked",
                  js("[...document.querySelectorAll(\"[data-key='trends.views'] .set-view\")].map(v => [v.querySelector('[data-k=name]').value, v.querySelector('[data-k=default]').checked])")
                  == [["CBC (browser check)", True], ["Kidney (browser check)", False]])
            check("…and fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.click("[data-key='trends.views'] button[aria-label='Remove the view Kidney (browser check)']")
            page.click("[data-key='trends.views'] button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-trends > .msg').textContent.startsWith('Saved')", timeout=10000)
            page.set_viewport_size({"width": 1400, "height": 900})
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0", timeout=20000)
            js("switchTab('labs')")
            settle(300)
            check("a view removed on Settings is gone from Trends", js("[...document.querySelectorAll('#viewPick option')].map(o => o.value)") == ["CBC (browser check)"])
            page.once("dialog", lambda d: d.accept())
            page.click("#tvBox button:text-is('Delete')")
            page.wait_for_function("CFG.trends.views.length === 0", timeout=10000)
            check("deleting the last view from Trends leaves none", js("document.getElementById('viewPick').disabled") is True)
            if kept is not None:
                js(f"localStorage.setItem(stateKey(), {json.dumps(kept)})")
            page.reload()
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0", timeout=20000)
            js("switchTab('labs')")
            settle(300)

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
                  ["Overview", "Trends", "Names", "Health", "Connected sources", "Milestones", "Tabs", "Appearance", "Dates and times"], js("[...document.querySelectorAll('#sections > section h2')].map(h => h.textContent)"))
            check("…with the server's attachment and sync defaults as a switch, numbers and choices (#101, #102)",
                  js("[document.querySelectorAll('#sec-sync input[type=checkbox]').length, document.querySelectorAll('#sec-sync input[type=number]').length]") == [5, 3],
                  js("document.getElementById('sec-sync').innerText.slice(0, 300)"))
            first = before[0][0]
            page.click(f"#sec-overview button[aria-label='Move {first} down']")
            page.click("#sec-overview .set button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-overview > .msg').textContent.startsWith('Saved')", timeout=10000)
            page.click("[data-key=labNicknames] button:text-is('Add a row')")
            page.fill("[data-key=labNicknames] .set-row:last-child input[data-k=from]", first)
            page.fill("[data-key=labNicknames] .set-row:last-child input[data-k=to]", "Renamed in Settings")
            page.click("[data-key=labNicknames] button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-names > .msg').textContent.startsWith('Saved')", timeout=10000)
            # ---- calendar feed (#51): made on the Settings page, its address shown once with setup steps, and the
            # address works for a calendar app with no session
            page.wait_for_selector("#calendar:not([hidden]) #calNew:not([hidden])", timeout=10000)
            check("calendar feed: minimal details are the default", js("document.querySelector('input[name=calDetails]:checked').value") == "minimal")
            page.click("#calCreate")
            page.wait_for_selector("#calSetup:not([hidden])", timeout=10000)
            feed_url = js("document.getElementById('calUrl').value")
            steps = page.inner_text("#calSetup")
            check("…creating one shows its address and setup steps for each calendar app",
                  re.match(re.escape(base) + r"/api/profiles/1/calendar\.ics\?token=[\w-]{40,}$", feed_url) is not None
                  and all(w in steps for w in ("iPhone", "Mac", "Google Calendar", "Outlook")) and page.locator("#calCopy").is_visible(), feed_url)
            check("…and warns that a local plain-HTTP address won't reach a calendar elsewhere", "plain HTTP" in page.inner_text("#calReach"))
            with urllib.request.urlopen(feed_url) as resp:
                ics = resp.read().decode("utf-8")
                cache = resp.headers.get("Cache-Control")
            check("…the address gives an iCalendar feed with the upcoming visit, titles only",
                  ics.startswith("BEGIN:VCALENDAR\r\n") and "SUMMARY:Appointment\r\n" in ics and "Follow-up" not in ics and cache.startswith("private"), ics[:200])
            check("…the owner sees it among the profile's feeds", USER in page.inner_text("#calList"))
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the calendar feed's setup panel fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.click("#calDone")
            check("…and the address isn't kept on the page once done", js("document.getElementById('calUrl').value") == "")
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

            # ---- #36 key-lab tiles: drag or keyboard-move them, with a live preview, and the Overview follows
            page.goto(base + "/settings.html?profile=1")
            page.wait_for_selector("#sec-overview .set-preview .tile", timeout=10000)
            preview = "[...document.querySelectorAll('#sec-overview .set-preview .tile')].map(t => t.dataset.lab)"
            p0 = js(preview)
            check("key labs: the preview shows the Overview's tiles", p0 == [t for t, _ in before], p0[:4])
            page.click("#sec-overview button[aria-label='Remove Magnesium']")   # the demo has just the 12 default labs
            check("…a lab taken out leaves the preview", "Magnesium" not in js(preview))
            check("…and is suggested among the profile's most-measured labs",
                  js("[...document.querySelectorAll('#sec-overview .set-sugg .chip')].map(b => b.getAttribute('aria-label'))") == ["Add Magnesium"])
            page.click("#sec-overview .set-sugg .chip")
            check("…to put back", js(preview) == p0, js(preview)[-2:])
            grips = page.locator("#sec-overview .set-grip")
            g2, g0 = grips.nth(2).bounding_box(), grips.nth(0).bounding_box()
            page.mouse.move(g2["x"] + g2["width"] / 2, g2["y"] + g2["height"] / 2)
            page.mouse.down()
            page.mouse.move(g0["x"] + g0["width"] / 2, g0["y"] + 2, steps=8)
            page.mouse.up()
            p1 = js(preview)
            check("…dragging a lab by its handle moves it, and the preview follows at once", p1[:3] == [p0[2], p0[0], p0[1]], p1[:3])
            grips.nth(0).focus()
            page.keyboard.press("ArrowDown")
            page.keyboard.press("ArrowDown")
            page.keyboard.press("ArrowUp")
            p2 = js(preview)
            check("…and so do the arrow keys on its handle, which keeps the focus",
                  p2[:3] == [p0[0], p0[2], p0[1]] and js("document.activeElement.dataset.act") == "grip", p2[:3])
            page.click("#sec-overview .set button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-overview > .msg').textContent.startsWith('Saved')", timeout=10000)
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records'", timeout=20000)
            js("switchTab('overview')")
            settle(300)
            check("…the Overview follows the new order", [t for t, _ in js(tiles)[:3]] == p2[:3], js(tiles)[:3])

            # ---- #40 tabs: hide Costs and move Timeline up; gone from the bar and the arrow keys, a saved view or a
            # link to it opens the first shown tab, and one tab at least stays shown
            js("switchTab('costs')")   # the saved view now names Costs
            page.goto(base + "/settings.html?profile=1")
            page.wait_for_selector("#sec-tabs input[data-tab=costs]", timeout=10000)
            page.uncheck("#sec-tabs input[data-tab=costs]")
            page.click("#sec-tabs button[aria-label='Move Timeline up']")
            page.click("#sec-tabs .set button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-tabs > .msg').textContent.startsWith('Saved')", timeout=10000)
            shown_tabs = "[...document.querySelectorAll('.tabs [role=tab]')].filter(b => !b.hidden).map(b => b.id.slice(4))"
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records' && booted", timeout=20000)
            check("tabs: a hidden tab leaves the bar, the others in their new order",
                  js(shown_tabs) == ["overview", "timeline", "health", "labs", "heatmap"], js(shown_tabs))
            check("…a saved view naming it opens the first shown tab", js("state.tab") == "overview" and js("!document.getElementById('view-overview').hidden"), js("state.tab"))
            page.focus("#tab-overview")
            page.keyboard.press("ArrowLeft")
            check("…the arrow keys skip it", js("state.tab") == "heatmap" and js("document.activeElement.id") == "tab-heatmap", js("state.tab"))
            page.keyboard.press("ArrowRight")
            js("switchTab('costs')")
            check("…asking for it opens the first shown tab", js("state.tab") == "overview", js("state.tab"))
            page.goto(base + "/#costs")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records' && booted", timeout=20000)
            check("…so does a link to it", js("state.tab") == "overview" and js("document.getElementById('view-costs').hidden"), js("state.tab"))
            page.goto(base + "/#timeline")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records' && booted", timeout=20000)
            check("…while a link to a shown tab opens it", js("state.tab") == "timeline", js("state.tab"))
            page.goto(base + "/settings.html?profile=1")
            page.wait_for_selector("#sec-tabs input[data-tab=costs]", timeout=10000)
            for t in ("overview", "health", "labs", "heatmap"):
                page.uncheck(f"#sec-tabs input[data-tab={t}]")
            check("…and the last shown tab can't be hidden", js("document.querySelector('#sec-tabs input[data-tab=timeline]').disabled"))
            page.once("dialog", lambda d: d.accept())
            page.click("#sec-tabs button:text-is('Reset to defaults')")
            page.wait_for_function("document.querySelector('#sec-tabs > .msg').textContent.includes('back to their defaults')", timeout=10000)
            page.once("dialog", lambda d: d.accept())
            page.click("#sec-overview button:text-is('Reset to defaults')")
            page.wait_for_function("document.querySelector('#sec-overview > .msg').textContent.includes('back to their defaults')", timeout=10000)
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records' && booted", timeout=20000)
            check("…Reset to defaults shows every tab again", js(shown_tabs) == ["overview", "health", "timeline", "labs", "heatmap", "costs"], js(shown_tabs))
            js("switchTab('overview')")

            # ---- #45 a default theme for the account: a fresh browser signed in as the same person uses it; the ◐
            # toggle still overrides it there; Reset to defaults follows the system again
            js("localStorage.removeItem('ht-theme')")
            page.goto(base + "/settings.html?profile=1")
            page.wait_for_selector("#sec-appearance input[value=dark]", timeout=10000)
            page.check("#sec-appearance input[value=dark]")
            page.click("#sec-appearance .set button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-appearance > .msg').textContent.startsWith('Saved')", timeout=10000)
            check("theme: saving a default applies it here", js("document.documentElement.dataset.theme") == "dark")
            fresh = browser.new_context(viewport={"width": 375, "height": 800}, bypass_csp=True)
            fresh.add_cookies([{k: c[k] for k in ("name", "value", "domain", "path", "httpOnly", "sameSite")}
                               for c in page.context.cookies() if c["name"] == "sg_session"])
            fp = fresh.new_page()
            fp.on("pageerror", lambda e: problems.append(f"script error (fresh browser): {e}"))
            fp.goto(base + "/")
            fp.wait_for_function("typeof EV !== 'undefined' && EV.length > 0", timeout=20000)
            check("…a fresh browser uses it once signed in", fp.evaluate("document.documentElement.dataset.theme") == "dark")
            fp.goto(base + "/account.html")   # a page that loads no settings: theme.js has it from the last visit
            check("…and so does its next page, before it draws", fp.evaluate("document.documentElement.dataset.theme") == "dark")
            fp.goto(base + "/")
            fp.wait_for_function("typeof EV !== 'undefined' && EV.length > 0", timeout=20000)
            fp.click("#theme")
            fp.reload()
            fp.wait_for_function("typeof EV !== 'undefined' && EV.length > 0", timeout=20000)
            check("…where the ◐ toggle still overrides it", fp.evaluate("document.documentElement.dataset.theme") == "light")
            fresh.close()
            page.once("dialog", lambda d: d.accept())
            page.click("#sec-appearance button:text-is('Reset to defaults')")
            page.wait_for_function("document.querySelector('#sec-appearance > .msg').textContent.includes('back to their defaults')", timeout=10000)
            check("…and Reset to defaults follows the system again",
                  js("document.documentElement.dataset.theme === undefined && localStorage.getItem('ht-theme-default') === null"))
            # ---- visit summary (#48): pick the visit and sections, add a lab, type questions (kept as a draft),
            # preview, then download the PDF and read it back
            page.goto(base + "/")   # from the main page, after the settings checks above
            page.wait_for_function("typeof booted !== 'undefined' && booted", timeout=20000)
            check("the header links to the visit summary", js("document.getElementById('summaryLink').getAttribute('href')") == "summary.html")
            was_profile = js("localStorage.getItem('ht-profile')")   # the summary page remembers the profile picked, as Settings does
            page.goto(base + "/summary.html?profile=2")   # the second profile: results from several imports, Ferritin typed in
            page.wait_for_selector("#build:not([hidden])", timeout=10000)
            check("the summary page offers every section", js("[...document.querySelectorAll('#sections input')].map(i => i.value)") ==
                  ["out_of_range", "trends", "medications", "appointments", "questions"])
            check("…the last visit is the default since point", js("document.querySelector('#since option').textContent.startsWith('The last visit (')"))
            page.uncheck("#sections input[value=appointments]")
            page.fill("#labIn", "Ferritin")
            page.click("#labAdd")
            check("…a lab added to the trends", js("PICKED") == ["Ferritin"], js("PICKED"))
            page.fill("#questions", "Is the ferritin result worth rechecking?\nShould the next draw be fasting?")
            page.wait_for_function("document.getElementById('qMsg').textContent === 'Draft saved.'", timeout=10000)
            page.click("#previewBtn")
            page.wait_for_selector("#preview:not([hidden]) h3", timeout=10000)
            heads = js("[...document.querySelectorAll('#preview h3')].map(h => h.textContent.replace(/ \\(\\d+\\)$/, ''))")
            check("…the preview shows the ticked sections", len(heads) == 4 and heads[0].startswith("Out of range since") and heads[-1] == "My questions", heads)
            check("…with the questions and the notice", "Should the next draw be fasting?" in js("document.getElementById('preview').textContent")
                  and "isn't medical advice" in js("document.getElementById('preview').textContent"))
            check("…and Ferritin among the trends", "Ferritin" in js("document.getElementById('preview').textContent"))
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the summary page and its preview fit 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            with page.expect_download(timeout=20000) as dl:
                page.click("#download")
            pdf_path = os.path.join(TMP, "summary-download.pdf")
            dl.value.save_as(pdf_path)
            from pypdf import PdfReader
            reader = PdfReader(pdf_path)
            text = "\n".join(p_.extract_text() for p_ in reader.pages)
            check("the summary downloads as a PDF of one or two pages", dl.value.suggested_filename.startswith("visit-summary-Second-person-")
                  and len(reader.pages) in (1, 2), (dl.value.suggested_filename, len(reader.pages)))
            check("…holding the sections, the questions and the notice",
                  all(w in text for w in ("Visit summary", "Second person", "Out of range since", "Trends since", "Ferritin",
                                          "Current medications", "Should the next draw be fasting?", "isn't medical advice"))
                  and "Upcoming appointments" not in text, text[:300])
            page.wait_for_function("document.getElementById('buildMsg').textContent.startsWith('Downloaded')", timeout=5000)
            page.reload()
            page.wait_for_selector("#build:not([hidden])", timeout=10000)
            check("…and the questions draft is still there", js("document.getElementById('questions').value").startswith("Is the ferritin result"))
            page.fill("#questions", "")   # leave no draft behind
            page.wait_for_function("document.getElementById('qMsg').textContent === 'Draft saved.'", timeout=10000)
            js("v => v === null ? localStorage.removeItem('ht-profile') : localStorage.setItem('ht-profile', v)", was_profile)
            # ---- milestones (#39) and visits counted as workouts (#43) on the Settings page: a milestone added (primary)
            # shows as a marker on Trends and a "since" period on Health; a rule (a bad regular expression refused
            # first) makes the Health tab count the matching visits; both sections reset
            page.goto(base + "/")   # from the main page, after the visit summary checks above
            page.wait_for_function("typeof booted !== 'undefined' && booted && PROFILE.name === 'My records'", timeout=20000)
            check("the demo's milestones moved into the settings", js("MILESTONES.length") > 0, js("MILESTONES"))
            visit = js("(() => { const n = {}; for (const e of DATA.events) if (e.k === 'visit' && e.ts < Date.now()) n[e.title] = (n[e.title] || 0) + 1;"
                       " return Object.entries(n).sort((a, b) => b[1] - a[1])[0][0]; })()")
            want = visit.split(" ")[0].lower()
            days = js(f"new Set(DATA.events.filter(e => e.k === 'visit' && e.ts < Date.now() && e.title.replace(/\\s+/g, ' ').trim().slice(0, 200).toLowerCase().includes({json.dumps(want)})).map(e => isoDay(e.ts))).size")
            page.goto(base + "/settings.html?profile=1")
            page.wait_for_selector("#sec-milestones [data-key=milestones] .set-row:not(.set-head)", timeout=10000)
            check("Settings shows the Milestones section", "Milestones" in js("[...document.querySelectorAll('#sections > section h2')].map(h => h.textContent)"))
            ms = "[data-key=milestones]"
            page.click(f"{ms} button:text-is('Add a row')")
            page.fill(f"{ms} .set-row:last-child input[data-k=date]", "2026-08-01")
            page.fill(f"{ms} .set-row:last-child input[data-k=label]", "Browser check milestone")
            page.check(f"{ms} .set-row:last-child input[data-k=primary]")
            check("…one primary at a time", js(f"document.querySelectorAll('{ms} input[data-k=primary]:checked').length") == 1)
            page.click(f"{ms} button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-milestones > .msg').textContent.startsWith('Saved')", timeout=10000)
            rules = "[data-key=visitWorkouts]"
            page.click(f"{rules} button:text-is('Add a row')")
            page.fill(f"{rules} .set-row:last-child input[data-k=match]", "(a+)+$")
            page.fill(f"{rules} .set-row:last-child input[data-k=name]", "Browser check rule")
            page.fill(f"{rules} .set-row:last-child input[data-k=minutes]", "40")
            page.check(f"{rules} .set-row:last-child input[data-k=regex]")
            n0 = len(problems)
            page.click(f"{rules} button:text-is('Save')")
            page.wait_for_function(f"document.querySelector('{rules} .msg').textContent.includes('very slow')", timeout=10000)
            settle(200)
            refused = [p for p in problems[n0:] if ("HTTP 400" in p and p.endswith("/settings/visitWorkouts")) or "status of 400" in p]
            problems[n0:] = [p for p in problems[n0:] if p not in refused]   # this refusal is the point, not a fault
            check("a catastrophic regular expression is refused before saving",
                  "very slow" in js(f"document.querySelector('{rules} .msg').textContent") and len(refused) == 2, refused)
            page.fill(f"{rules} .set-row:last-child input[data-k=match]", want)
            page.uncheck(f"{rules} .set-row:last-child input[data-k=regex]")
            page.click(f"{rules} button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-health > .msg').textContent.startsWith('Saved')", timeout=10000)
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the milestones and workout rules fit 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records'", timeout=20000)
            page.click("#tab-labs")
            js("applyPreset('all')")
            settle()
            check("…the new milestone is marked on Trends",
                  js("[...document.querySelectorAll('#charts .mslabel')].some(t => t.textContent.includes('Browser check milestone'))"))
            page.click("#tab-health")
            settle()
            page.click("#view-health .seg button:text-is('Since browser check milestone')")
            settle()
            check("…and is a “since” period on Health", js("isoDay(hRange()[0])") == "2026-08-01",
                  js("[hState.period, new Date(hRange()[0]).toISOString()]"))
            check("…the primary one, the Overview counts from", js("primaryMs().label") == "Browser check milestone")
            page.click("#view-health .seg button:text-is('All')")
            settle()
            counted = js("H.ex.filter(x => x.pt && x.rule === 'Browser check rule').length")
            check("the Health tab counts visits matching the rule", counted == days and days > 0, f"{counted} of {days} days ({want})")
            check("…and says so", f"{days} browser check rule appointment" in js("document.getElementById('view-health').innerText"))
            page.goto(base + "/settings.html?profile=1")
            page.wait_for_selector("#sec-milestones", timeout=10000)
            for sec in ("milestones", "health"):
                page.once("dialog", lambda d: d.accept())
                page.click(f"#sec-{sec} button:text-is('Reset to defaults')")
                page.wait_for_function(f"document.querySelector('#sec-{sec} > .msg').textContent.includes('back to their defaults')", timeout=10000)
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records'", timeout=20000)
            check("…both reset", js("MILESTONES.length === 0 && !CFG.visitWorkouts.length"), js("[MILESTONES, CFG.visitWorkouts]"))
            # ---- each person's own time zone and date format (#42): two accounts on one install, their browsers both
            # in Chicago, each set from the browser on first sign-in; then one chooses Los Angeles on the Settings page
            # and the other Auckland, and each sees days split at their own midnight
            def sign_in(ctx, user, pw):
                pg = ctx.new_page()
                pg.on("pageerror", lambda e: problems.append(f"script error ({user}): {e}"))
                pg.goto(base + "/login.html")
                pg.fill("#u", user)
                pg.fill("#p", pw)
                pg.click("#b")
                pg.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE && PROFILE.id === 1", timeout=20000)
                return pg
            from server import db as dbm, security   # a second account only for this (with two, an admin needs a second factor)
            sdb = dbm.connect()
            uid2 = sdb.execute("INSERT INTO users(username, pw_hash, is_admin, created) VALUES(?, ?, 0, 0)", (USER2, security.hash_password(PW2))).lastrowid
            sdb.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, 1, 'viewer')", (uid2,))
            zctx = {u: browser.new_context(viewport={"width": 1400, "height": 900}, bypass_csp=True, timezone_id="America/Chicago")
                    for u in (USER, USER2)}
            za, zb = sign_in(zctx[USER], USER, PW), sign_in(zctx[USER2], USER2, PW2)
            mine = lambda pg: pg.evaluate("fetch('api/settings/user').then(r => r.json())")
            check("time zone and date format set from the browser on first sign-in",
                  mine(zb)["dates.timeZone"] == "America/Chicago" and mine(zb)["dates.format"] == "mdy", mine(zb))
            before = mine(za)   # the demo account's, put back afterwards for the checks below
            za.goto(base + "/settings.html?profile=1")
            za.wait_for_selector("#sec-dates select", timeout=10000)
            za.select_option("[data-key='dates.timeZone'] select", "America/Los_Angeles")
            za.click("[data-key='dates.timeZone'] button:text-is('Save')")
            za.wait_for_function("document.querySelector('#sec-dates > .msg').textContent.startsWith('Saved')", timeout=10000)
            za.select_option("[data-key='dates.format'] select", "dmy")
            za.click("[data-key='dates.format'] button:text-is('Save')")
            za.wait_for_function("document.querySelector('#sec-dates > .msg').textContent.startsWith('Saved')", timeout=10000)
            check("…changed on the Settings page, just for that person", mine(za)["dates.timeZone"] == "America/Los_Angeles"
                  and mine(zb)["dates.timeZone"] == "America/Chicago", (mine(za), mine(zb)))
            zb.evaluate("""Promise.all([['dates.timeZone', 'Pacific/Auckland'], ['dates.format', 'ymd']].map(([k, v]) =>
                api('settings/user/' + k, {method: 'PUT', body: JSON.stringify({value: v})})))""")
            for pg in (za, zb):
                pg.goto(base + "/")
                pg.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.id === 1", timeout=20000)
            late = "Date.parse('2026-03-10T23:30:00Z')"   # 4:30 pm in Los Angeles, 12:30 pm the next day in Auckland
            # the timeline's day ticks at each person's midnight: 07:00 UTC in Los Angeles (PDT), 11:00 UTC in Auckland (NZDT)
            first_tick = "new Date(ticks(Date.parse('2026-03-09T12:00:00Z'), Date.parse('2026-03-13T12:00:00Z'), 1000)[0][0]).toISOString()"
            got = {u: (pg.evaluate(f"isoDay({late})"), pg.evaluate(first_tick), pg.evaluate(f"fmtD({late})"))
                   for u, pg in ((USER, za), (USER2, zb))}
            check("two people in different time zones each see days split at their own midnight",
                  got[USER][:2] == ("2026-03-10", "2026-03-10T07:00:00.000Z") and got[USER2][:2] == ("2026-03-11", "2026-03-10T11:00:00.000Z"), got)
            check("…and dates in their own format", got[USER][2] == "10 Mar 2026" and got[USER2][2] == "2026-03-11", got)
            # Health's daily charts and workout calendar, the drawer and Costs by month bin with the same days
            weekly_key = "new Date(weekly([[Date.parse('2026-03-08T20:00:00Z'), 1]])[0][0] - 3 * DAY).toISOString()"
            check("…Health's weekly bins start at their own Monday",
                  (za.evaluate(weekly_key), zb.evaluate(weekly_key)) == ("2026-03-02T20:00:00.000Z", "2026-03-08T23:00:00.000Z"),
                  (za.evaluate(weekly_key), zb.evaluate(weekly_key)))
            zb.click("#tab-timeline")
            zb.wait_for_selector("#list .row .d", timeout=10000)
            shown = zb.evaluate("document.querySelector('#list .row .d').textContent")
            check("…the timeline's event list in that format", bool(__import__("re").fullmatch(r"\d{4}-\d{2}-\d{2}", shown)), shown)
            check("…and the header", " built 20" in zb.evaluate("document.getElementById('meta').textContent"), zb.evaluate("document.getElementById('meta').textContent"))
            zb.goto(base + "/import.html")
            zb.wait_for_selector("#importer", state="attached", timeout=10000)
            check("…the import pages read them too", zb.evaluate("DATES.zone") == "Pacific/Auckland" and zb.evaluate("zToday()") == zb.evaluate(
                "new Intl.DateTimeFormat('en-CA', {timeZone: 'Pacific/Auckland'}).format(new Date())"), zb.evaluate("DATES.zone"))
            za.evaluate("""v => Promise.all(Object.entries(v).map(([k, x]) =>
                api('settings/user/' + k, {method: 'PUT', body: JSON.stringify({value: x})})))""", before)
            for c in zctx.values():
                c.close()
            sdb.execute("DELETE FROM users WHERE id=?", (uid2,))
            sdb.close()

            # ---- a watch receiver secret per profile (#46): made on the Settings page, shown once, then masked
            page.goto(base + "/settings.html?profile=1")
            page.wait_for_selector("#watch #watchRotate", timeout=10000)
            check("watch card: the server's original secret is still this profile's", "original receiver secret" in page.inner_text("#watch"),
                  page.inner_text("#watch")[:300])
            page.once("dialog", lambda d: d.accept())
            page.click("#watchRotate")
            page.wait_for_selector("#watchSecret", timeout=10000)
            made = js("document.getElementById('watchSecret').textContent")
            check("…makes a secret and shows it once, with a copy button", len(made) >= 40 and js("!!document.querySelector('#watch button[aria-label=\"Copy the secret\"]')"), made)
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("…and the card fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.reload()
            page.wait_for_selector("#watch .w-secret", timeout=10000)
            masked = js("document.querySelector('#watch .w-secret').textContent")
            check("…then only its last characters", masked.endswith(made[-4:]) and made not in page.content(), masked)
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
            page.wait_for_selector("#sourceButtons button", timeout=10000)
            conn_pid = js("+document.getElementById('profile').value")
            # the same results (#96): a spreadsheet of the fake patient's 4 March 2025 labs, with dates only and
            # Hemoglobin in g/L, saved in the profile first; the first pull below brings them again
            from server import uploads as up_mod
            db = dbm.connect()
            try:
                csv_up = up_mod.create(db, conn_pid, None)
                up_mod.add_file(db, conn_pid, csv_up, "labs.csv", io.BytesIO(
                    b"Date,Test,Result,Units,Reference Range,Flag,Panel\n03/04/2025,Hemoglobin,121,g/L,135-175,L,CBC\n"
                    b"06/02/2025,Hemoglobin,128,g/L,135-175,L,CBC\n"))
                up_mod.finish(db, conn_pid, csv_up)
            finally:
                db.close()
            write = "{method: 'POST', credentials: 'same-origin', headers: {'X-Sanguity': '1', 'Content-Type': 'application/json'}, body: '{}'}"
            csv_job = js(f"fetch('api/profiles/{conn_pid}/uploads/{csv_up}/import', {write}).then(r => r.json()).then(j => j.id)")
            job_state = lambda: js(f"fetch('api/profiles/{conn_pid}/jobs/{csv_job}').then(r => r.json()).then(j => j.state)")
            for want in ("preview", "done"):
                for _ in range(150):
                    if job_state() == want:
                        break
                    page.wait_for_timeout(200)
                if want == "preview":
                    js(f"fetch('api/profiles/{conn_pid}/jobs/{csv_job}/confirm', {write})")
            check("the same results from a file are in the profile before the source connects", job_state() == "done", job_state())
            csv_label = js(f"fetch('api/profiles/{conn_pid}/jobs/{csv_job}').then(r => r.json()).then(j => j.label)")
            # the install's attachment rules (#101, settings registry): only text attachments are fetched by themselves
            check("the attachment defaults are install settings", js(f"api('profiles/{conn_pid}/settings/sync.attachTypes', "
                  "{method: 'PUT', body: JSON.stringify({value: ['text']})}).then(r => r.status)") == 200)
            page.click("#sourceButtons button")   # choose what to pull (#99), then on to the fake's sign-in
            page.wait_for_selector(".conn-choose", timeout=10000)
            check("connecting shows what each kind of record brings", "reference ranges" in page.inner_text(".conn-choose")
                  and page.locator(".conn-choose input[type=checkbox]").count() == 7, page.inner_text(".conn-choose")[:300])
            page.uncheck(".conn-choose input[value=visits]")
            page.locator(".conn-choose").get_by_role("button", name="Sign in at").click()   # approves and comes back
            page.wait_for_url("**/connect.html**", timeout=20000)
            page.wait_for_selector("#import-status button.primary", timeout=30000)   # the first pull's preview
            check("signing in connects the source, and its first pull is previewed like an upload",
                  "Ready to import" in page.inner_text("#import-status"), page.inner_text("#import-status")[:300])
            same_li = page.locator("#import-status .imp-same li", has_text="Hemoglobin")
            flag = same_li.inner_text() if same_li.count() == 1 else page.inner_text("#import-status")[:600]
            check("…flagging a result the file brought as possibly the same, with both sources named (#96)",
                  "Hemoglobin 12.1 g/dL" in flag and "Possibly the same result as Hemoglobin 121 g/L" in flag
                  and f"from {csv_label}" in flag, flag)
            check("…and nothing ticked until the person says so", js("[...document.querySelectorAll('#import-status .imp-same input')].every(b => !b.checked)"))
            same_li.get_by_label("Same result: Hemoglobin 12.1 g/dL").check()
            page.click("#import-status button.primary")
            page.wait_for_function("document.getElementById('import-status').textContent.includes('Imported.')", timeout=30000)
            check("…saved, with the result shown once", "shown once, as the same result from another source" in page.inner_text("#import-status"),
                  page.inner_text("#import-status")[:400])
            page.wait_for_function("document.getElementById('connList').textContent.includes('New records come in on a schedule')", timeout=20000)
            check("…saved, and the connection then syncs on a schedule", True)
            page.reload()
            page.wait_for_function("document.getElementById('connList').textContent.includes('Attachments left out')", timeout=20000)
            chosen = page.inner_text(".conn-chosen")
            check("the connection says what it pulls, without the kind left unticked", "lab results" in chosen
                  and "visits" not in chosen and "only when you ask" not in chosen, chosen)
            page.click(".conn-skipped summary")
            skipped = page.inner_text(".conn-skipped")
            check("attachments the rules left out are listed, with why", "Attachments left out (2)" in skipped
                  and "PDF" in skipped and "doesn't fetch" in skipped, skipped)
            page.locator(".conn-skipped li").first.get_by_role("button", name="Fetch anyway").click()
            page.wait_for_function("document.getElementById('connList').textContent.includes('Fetched')", timeout=20000)
            check("…and Fetch anyway brings one in", "Attachments left out (1)" in page.inner_text("#connList"), page.inner_text("#connList")[:400])
            # on Trends: one Hemoglobin chart, one point on 4 March 2025 listing both sources; the drawer the same
            profile_before_96 = js("localStorage.getItem('ht-profile')")
            js(f"localStorage.setItem('ht-profile', '{conn_pid}')")
            page.goto(base + "/")
            page.wait_for_function(f"typeof SERIES !== 'undefined' && PROFILE && PROFILE.id === {conn_pid} && SERIES.length > 0", timeout=20000)
            hgb_day = ("(() => { const s = SERIES.filter(s => s.name === 'Hemoglobin' && !s.watch); "
                       "return s.map(x => x.pts.filter(p => p[0].startsWith('2025-03-04')).map(p => pointSources(x, p))).flat(); })()")
            on_day = js(hgb_day)
            check("Trends: the same result from the file and the sync is one point, with both sources (#96)",
                  len(on_day) == 1 and on_day[0].count(";") == 1 and "same result" in on_day[0], on_day)
            js("setRange(Date.parse('2025-01-01'), Date.parse('2025-12-31')); state.sel = new Set(['Hemoglobin']); state.tables.add('Hemoglobin'); switchTab('labs')")
            page.wait_for_selector("#charts table.data tbody tr", timeout=10000)
            rows = js("[...document.querySelectorAll('#charts .chart')].filter(c => c.querySelector('.name').textContent === 'Hemoglobin')"
                      ".map(c => c.querySelectorAll('tbody tr').length)")
            check("…one Hemoglobin chart with 2025's results (the C-CDA's g/dL one has none then), one row per result",
                  [n for n in rows if n] == [2], rows)
            check("…the table says the 4 March result came from one more source, and lists both",
                  "same result from 1 more source" in page.inner_text("#charts") and "Sources" in page.inner_text("#charts .source"),
                  page.inner_text("#charts")[:400])
            page.set_viewport_size({"width": 375, "height": 800})
            day_ev = js("DATA.events.find(e => e.k === 'lab' && e.t.startsWith('2025-03-04') && "
                        "(DATA.details[e.id]?.ids || []).some(i => (DATA.details[i]?.same || []).length)).id")
            js(f"openEvent(byId.get({day_ev}))")
            page.wait_for_selector("#dbody .same-line", timeout=5000)
            check("the drawer: the result once, saying the same result also came from the sync, with Undo link",
                  "the same result also came from" in page.inner_text("#dbody .same")
                  and page.locator("#dbody .same button", has_text="Undo link").count() == 1
                  and js("[...document.querySelectorAll('#dbody td')].filter(td => td.textContent === 'Hemoglobin').length") == 1,
                  page.inner_text("#dbody")[:500])
            check("…and fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.click("#dbody .same button:has-text('Undo link')")
            page.wait_for_selector("#dbody .same-line.possible", timeout=20000)   # reloaded, the panel reopened
            check("undoing the link shows both again, flagged as possibly the same", len(js(hgb_day)) == 2
                  and page.locator("#dbody .same-line.possible button", has_text="Same result").count() >= 1, js(hgb_day))
            page.click("#dbody .same-line.possible:has-text('Hemoglobin') button:has-text('Same result')")
            page.wait_for_selector("#dbody .same-line:not(.possible)", timeout=20000)
            check("…and linking it again from the drawer shows it once", len(js(hgb_day)) == 1, js(hgb_day))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.goto(base + "/connect.html")
            page.select_option("#profile", label="Second person")
            page.wait_for_function("document.getElementById('connList').textContent.includes('New records come in on a schedule')", timeout=20000)
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
            page.get_by_role("button", name="What it pulls…").click()
            page.wait_for_selector(".conn-options", timeout=10000)
            check("connected sources fit 375 px, with a connection's choices open",
                  js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.check(".conn-options input[value=visits]")   # granted at sign-in? no: unticked, so it asks to sign in again
            check("a kind the access doesn't cover says it needs signing in again",
                  "Needs signing in again" in page.inner_text(".conn-options"), page.inner_text(".conn-options")[:300])
            page.uncheck(".conn-options input[value=visits]")
            page.uncheck(".conn-options input[value=medications]")
            check("…and a kind turned off offers to remove what it brought",
                  page.get_by_role("button", name="Remove medications it brought").count() == 1)
            page.locator(".conn-options").get_by_role("button", name="Save").click()
            page.wait_for_function("document.getElementById('connList').textContent.includes('Saved. The next sync')", timeout=10000)
            check("…saved for the next sync", "medications" not in page.inner_text(".conn-chosen"), page.inner_text(".conn-chosen"))
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
            prev_profile = js("localStorage.getItem('ht-profile')")
            js(f"localStorage.setItem('ht-profile', '{conn_pid}')")   # the profile's main page warns too (#102)
            page.goto(base + "/")
            page.wait_for_selector("#syncWarn:not([hidden])", timeout=20000)
            warn = page.inner_text("#syncWarn")
            check("the profile's main page warns that the source stopped syncing, with a link to it",
                  "access ended" in warn and js("document.querySelector('#syncWarn a').getAttribute('href')").startswith("connect.html?profile="), warn)
            page.set_viewport_size({"width": 375, "height": 800})
            check("…and the warning fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            js(f"api('profiles/{conn_pid}/settings/sync.attachTypes', {{method: 'DELETE'}})")
            js(f"localStorage.setItem('ht-profile', {json.dumps(prev_profile)} || '')")
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
            js("(p => p == null ? localStorage.removeItem('ht-profile') : localStorage.setItem('ht-profile', p))", profile_before_96)   # as before #96's visit

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
            page.wait_for_selector("#sourceButtons button:has-text('Fake health plan')", timeout=10000)
            page.click("#sourceButtons button:has-text('Fake health plan')")   # what to pull (#99), then sign in
            page.wait_for_selector(".conn-choose", timeout=10000)
            page.locator(".conn-choose").get_by_role("button", name="Sign in at").click()
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
        # ---- install as a phone app (#56), on a phone-sized page under the real CSP: the manifest and service worker
        # meet the install criteria; offline, with offline records off, the app's shell opens and says records aren't
        # available; turned on, the last profile opens read-only with the time it was loaded; turning it off, the
        # sign-in's end time and signing out each delete the copy. Signed in with a session of its own.
        try:
            before = len(problems)
            from server import config as sg_config, db as sg_db, security as sg_sec
            db = sg_db.connect()
            token, now = sg_sec.new_token(), sg_db.now()
            uid = db.execute("SELECT id FROM users WHERE username=?", (USER,)).fetchone()[0]
            db.execute("INSERT INTO sessions(token_hash, user_id, created, last_seen, expires, ip, agent, verified_at) VALUES(?,?,?,?,?,?,?,?)",
                       (sg_sec.token_hash(token), uid, now, now, now + 3600, "127.0.0.1", "browser check", now))
            db.close()
            ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
            ctx.add_cookies([{"name": sg_config.COOKIE, "value": token, "url": base, "httpOnly": True, "sameSite": "Strict"}])
            ph = ctx.new_page()
            ph.on("pageerror", lambda e: problems.append(f"script error (phone): {e}"))
            ph.on("console", lambda m: m.type == "error" and problems.append(f"console error (phone): {m.text}"))
            ph.on("response", lambda r: r.status >= 400 and problems.append(f"HTTP {r.status} (phone): {r.url}"))
            LOADED = "typeof EV !== 'undefined' && EV.length > 0"
            COPY = "caches.open('sanguity-offline').then(c => c.keys().then(ks => ks.length === 1 ? c.match(ks[0]).then(r => r.json()) : null))"
            NO_COPY = "caches.has('sanguity-offline').then(h => !h)"

            def until(expr, ms):   # page.wait_for_function evaluates a string, which the page's CSP refuses
                for _ in range(ms // 100):
                    if ph.evaluate(expr):
                        return
                    ph.wait_for_timeout(100)
                raise TimeoutError(f"waited {ms} ms for {expr}")

            def kept_copy(full=False):   # the copy, once the records page has written it
                c = None
                for _ in range(50):
                    c = ph.evaluate(COPY)
                    if c and (not full or (c.get("settings") and c.get("watch"))):
                        break
                    ph.wait_for_timeout(200)
                return c

            ph.goto(base + "/")
            ph.evaluate("localStorage.setItem('ht-profile', '1')")
            ph.reload()
            until(LOADED, 20000)
            until("!!(navigator.serviceWorker && navigator.serviceWorker.controller)", 15000)
            check("PWA: the service worker controls the page", ph.evaluate("navigator.serviceWorker.controller.scriptURL").endswith("/sw.js"))
            man = ph.evaluate("fetch(document.querySelector('link[rel=manifest]').href).then(async r => ({type: r.headers.get('content-type'), j: await r.json()}))")
            check("PWA: the manifest is served as a manifest, with its icons",
                  man["type"].startswith("application/manifest+json") and man["j"]["display"] == "standalone"
                  and {"192x192", "512x512"} <= {i["sizes"] for i in man["j"]["icons"]} and any(i.get("purpose") == "maskable" for i in man["j"]["icons"]), man["type"])
            errs = ctx.new_cdp_session(ph).send("Page.getInstallabilityErrors")["installabilityErrors"]
            check("PWA: Chromium finds nothing stopping the install", not [e for e in errs if e["errorId"] != "in-incognito"], errs)
            # the worker keeps the app's own files, and nothing from the API
            kept = dict(ph.evaluate("caches.keys().then(ks => Promise.all(ks.map(k => caches.open(k).then(c => c.keys()).then(rs => [k, rs.map(r => r.url)]))))"))
            shell = kept.get("sanguity-shell-v1", [])
            check("PWA: the app's pages, scripts and icons are kept",
                  any(u.endswith("/js/app.js") for u in shell) and any(u.endswith("/icons/icon-192.png") for u in shell), len(shell))
            check("PWA: nothing from /api/ is kept, and no records until they're turned on",
                  not any("/api/" in u for u in shell) and "sanguity-offline" not in kept, list(kept))

            # offline, with offline records off: the app's shell, and why there are no records
            ctx.set_offline(True)
            ph.reload()
            ph.wait_for_selector("#offlineBanner", timeout=10000)
            text = ph.inner_text("#offlineBanner")
            check("offline, records off: the app opens and says records aren't available offline",
                  "Records aren't available offline" in text and ph.is_visible("header h1") and ph.evaluate("typeof EV === 'undefined' || !EV.length"), text)
            ctx.set_offline(False)

            # installing (#56): without the browser's install prompt, the Account page says where to look; with it (a
            # stand-in event: headless Chromium never offers one), a button opens it, and the header shows Install app
            FAKE_PROMPT = ("(() => { const e = new Event('beforeinstallprompt'); e.prompt = () => { window.sgPrompted = (window.sgPrompted || 0) + 1; }; "
                           "e.userChoice = Promise.resolve({outcome: 'accepted'}); dispatchEvent(e); })()")
            ph.goto(base + "/account.html")
            ph.wait_for_selector("#installApp h2")
            check("Account: how to install on this device, without a prompt from the browser",
                  "Install app" in ph.inner_text("#installApp") and ph.locator("#installApp button").count() == 0, ph.inner_text("#installApp"))
            ph.evaluate(FAKE_PROMPT)
            ph.locator("#installApp button", has_text="Install on this device").click()
            until("document.querySelector('#installApp .msg') && document.querySelector('#installApp .msg').textContent.startsWith('Installed')", 5000)
            check("…with the browser's prompt, Install on this device opens it", ph.evaluate("window.sgPrompted") == 1)
            ph.goto(base + "/")
            until(LOADED, 20000)
            check("the header has no Install app button until the browser offers to install", ph.locator("#installBtn").is_hidden())
            ph.evaluate(FAKE_PROMPT)
            check("…then it does, and it opens the browser's prompt", ph.locator("#installBtn").is_visible())
            ph.click("#installBtn")
            until("window.sgPrompted === 1", 5000)
            check("…and it goes once used (a prompt opens once)", ph.locator("#installBtn").is_hidden())
            check("the header still fits 390 px with it", ph.evaluate("document.documentElement.scrollWidth") <= 390, ph.evaluate("document.documentElement.scrollWidth"))
            iphone = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                                         user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
            iphone.add_cookies([{"name": sg_config.COOKIE, "value": token, "url": base, "httpOnly": True, "sameSite": "Strict"}])
            ip = iphone.new_page()
            ip.on("pageerror", lambda e: problems.append(f"script error (iPhone): {e}"))
            ip.goto(base + "/account.html")
            ip.wait_for_selector("#installApp .install-steps", timeout=10000)
            steps = ip.inner_text("#installApp")
            check("on an iPhone: Safari's Share, then Add to Home Screen", "Share button" in steps and "Add to Home Screen" in steps, steps)
            check("…and the page fits 390 px", ip.evaluate("document.documentElement.scrollWidth") <= 390)
            iphone.close()

            # turned on under Account (off until then); the records loaded next are kept
            ph.goto(base + "/account.html")
            ph.wait_for_selector("#offlineOn")
            check("Account: offline records are off by default, with what's kept explained",
                  not ph.is_checked("#offlineOn") and "anyone who can use this device while it's unlocked" in ph.inner_text("#offlineRecords"))
            ph.check("#offlineOn")
            ph.goto(base + "/")
            until(LOADED, 20000)
            copy = kept_copy(full=True)
            check("records on: one profile's records, settings and watch data are kept",
                  bool(copy) and copy["profile"]["id"] == 1 and bool(json.loads(copy["data"])["events"]) and bool(copy.get("settings")) and bool(copy.get("watch")),
                  copy and sorted(copy))
            # offline: that profile, read-only, with when it was loaded
            ctx.set_offline(True)
            ph.reload()
            until(LOADED, 20000)
            text = ph.inner_text("#offlineBanner")
            name = ph.evaluate("PROFILE.name")
            check("offline, records on: the last profile opens read-only, with when it was loaded",
                  "Offline · read-only" in text and f"{name}, as loaded on" in text, text)
            ph.click("#tab-labs")
            ph.wait_for_selector("#charts svg", timeout=10000)
            check("offline: Trends draws from the kept copy", ph.evaluate("document.querySelectorAll('#charts svg').length") > 0)
            check("offline: Import, Settings and Account say they need the server",
                  ph.evaluate("[...document.querySelectorAll('header a.acct')].every(a => a.getAttribute('aria-disabled') === 'true' && /offline/.test(a.title))"))
            ph.evaluate("document.querySelector('#settingsLink').click()")
            ph.wait_for_timeout(300)
            check("offline: a link to another page isn't followed", ph.evaluate("location.pathname") == "/")
            check("offline: a change is refused as needing the server",
                  ph.evaluate("api('profiles/1/edits', {method: 'POST', body: '{}'}).then(async r => [r.status, (await r.json()).detail])") == [503, "You're offline. This needs the server."])
            ph.evaluate("openEvent(byId.get(+Object.keys(DATA.details).find(k => DATA.details[k].type === 'file' && byId.get(+k))))")   # a PDF
            ph.wait_for_selector("#dbody .offline-note", timeout=5000)
            check("offline: a document isn't loaded, and says why",
                  ph.evaluate("!document.querySelector('#dbody iframe[src], #dbody img[src]')") and "aren't kept on this device" in ph.inner_text("#dbody"))
            ph.evaluate("closeDrawer()")
            ctx.set_offline(False)

            # turned off: the copy goes
            ph.goto(base + "/account.html")
            ph.wait_for_selector("#offlineOn")
            until("document.querySelector('#offlineRecords .msg').textContent.includes('records, loaded')", 5000)
            ph.uncheck("#offlineOn")
            until(NO_COPY, 5000)
            check("records off: the kept copy is deleted", ph.evaluate(NO_COPY))
            # on again: once the sign-in's end time has passed, the copy isn't shown, and is deleted, with no connection
            ph.check("#offlineOn")
            ph.goto(base + "/")
            until(LOADED, 20000)
            kept_copy()
            ph.evaluate("""caches.open('sanguity-offline').then(async c => { const k = (await c.keys())[0], j = await (await c.match(k)).json();
                           j.ends = Date.now() / 1000 - 1; await c.put(k, new Response(JSON.stringify(j))); })""")
            ctx.set_offline(True)
            ph.reload()
            ph.wait_for_selector("#offlineBanner", timeout=10000)
            check("offline, past the sign-in's end: no records, and the copy is deleted",
                  "Records aren't available offline" in ph.inner_text("#offlineBanner") and ph.evaluate(NO_COPY))
            ctx.set_offline(False)
            # signing out deletes the copy and the choice; the app's own files stay
            ph.goto(base + "/")
            until(LOADED, 20000)
            check("records on: kept again", bool(kept_copy()))
            ph.click("#logout")
            ph.wait_for_url("**/login.html", timeout=10000)
            until(NO_COPY, 5000)
            check("sign-out: the kept copy and the choice are deleted, the app's own files stay",
                  ph.evaluate(NO_COPY) and ph.evaluate("localStorage.getItem('sg-offline-records')") is None and ph.evaluate("caches.has('sanguity-shell-v1')"))
            # offline, the browser reports each request it couldn't make; signing out ends in 401s
            problems[before:] = [x for x in problems[before:] if "ERR_INTERNET_DISCONNECTED" not in x and "401" not in x]
            ctx.close()
        except Exception as e:   # report what was caught so far instead of a bare traceback
            problems.append(f"PWA check stopped: {str(e).splitlines()[0]}")
            if "ph" in locals():
                problems.append(f"phone page at {ph.url}: " + str(ph.evaluate("document.body.innerText.slice(0, 300)")))
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
