#!/usr/bin/env python3
"""Headless browser check of the app with the fictional demo data, served by the real backend.

Starts the API (with www/ served by it) on a temporary data folder holding the demo build, signs in through the
login page, opens every tab and checks its main content rendered, opens the detail drawer for every kind of
event, drags and scroll-zooms the charts, toggles the event sort, switches profiles, uploads a zip and files on
the import page (a fictional C-CDA and a FHIR export, previewed, saved in the background and then shown on the timeline;
the C-CDA again, previewed as all duplicates and cancelled; the FHIR one then undone from the import history, and
gone from the timeline; UHC, BCBS and spreadsheet claims, shown on the Costs tab; a CareSpace and an Epic MyChart
export), types two lab results in on the import page and finds them on Trends, types a visit, a medication, a
claim and a letter with a PDF into their own forms, finds them on the timeline and Costs and opens the PDF, types in a payment with
its receipt and an HSA tag and sees it matched to the claim, counted once and retagged FSA on Costs (#55), uploads a lab with a spelling Sanguity
doesn't know, accepts the series suggested at the preview and finds it on Trends, then sees it matched as before on
the next upload, pastes a fictional SMART Health Link served by a local HTTPS server (what it says is shown and
nothing is fetched until Fetch; then its preview, and saved), sets up a connected source against the fake SMART on
FHIR server (tests/fakefhir), connects a profile by signing in with a choice of what to pull, saves its first pull's
preview, sees the attachments the install's rules left out and fetches one anyway, syncs a new result, changes what
the connection pulls, and is asked to reconnect when access ends, with a warning on the profile's main page, uploads a
Blue Button export and connects a health plan's patient-access API (the fake again) and finds both sets of claims on
Costs, sees the source's and the Health Link's hosts under Admin → Outside connections, changes settings on the
Settings page and resets them (a milestone added there marked on Trends and a "since" period on Health; a
workout rule, a catastrophic pattern refused first, counted on Health; drags and keyboard-moves the key-lab tiles, hides and
reorders tabs, sets a default theme a fresh browser picks up, picks SI units and kg and sees them on Trends, the
Overview tiles and the drawer with the reported values beside them, sets ranges of its own for results whose
report gives none and sees them used and labelled on Trends, the heatmap, the Overview and the visit summary, #44),
builds a visit summary (sections, a lab, questions
kept as a draft), previews it and downloads the PDF, reading it back, has two accounts in different time zones each see days split at their own midnight and
dates in their own format, journal times and the visit summary page included (#42), makes the profile's watch receiver secret on the Settings page, shown once and then
masked (#46), with no Settings card showing a stray "null" (#175), opens the account
page and the Admin page (#167: accounts, the sync defaults, the provider directory, the lab name match listed, the
install's activity; old addresses landing there; someone who isn't an admin told it isn't for them and shown no server
setting or command on the everyday pages), sees each record's source in the drawer, on Trends and on the heatmap, links the
same result from a file and the sync at the first pull's preview and sees it once on Trends and in the drawer (undone
and linked again there)
and follows its link to the import history, makes a share link on the account page and opens it with no session
under the real CSP (only the chosen sections and lab, no cookie, at 375 px too), finds the view in the activity and
revokes it (#47), checks the "not medical advice" notice on every tab and beside
out-of-range highlighting, sees a medication's start, dose change, stop and pharmacy fills as spans on the
timeline, in its history in the drawer and, when ticked, under the Trends charts (#50, at 375 px too), opens every tab
and every page's groups at 375 px in light and dark with nothing scrolling sideways and no control cut off or
overlapping (#61), runs axe-core's WCAG 2.0, 2.1 and 2.2 A and AA rules on about a hundred pages and states in light
and dark (a serious or critical finding fails), Tabs and Shift+Tabs through the pages at 375 px (focus always shown,
never under a sticky bar), fits every page at 320 px and with WCAG's text spacing, and checks forced colours and the
sign-in's autofill and paste (#202, docs/accessibility.md), and fails
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


MED_RECORDS = 990101


def add_med_records(build):
    """A fictional medication with a dose change and a stop, listed by a clinic, with pharmacy fills (#50): the demo's
    own medications are each listed once, with no end."""
    path = os.path.join(build, "data.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    i = MED_RECORDS
    rows = [("2025-02-03T09:00:00", "Examplocin 5 mg tablet", "Active", None), ("2025-05-12T10:00:00", "Examplocin 10 mg tablet", "Active", None),
            ("2025-08-20T10:00:00", "Examplocin 10 mg tablet", "Discontinued", "2025-09-30")]
    for t, title, status, end in rows:
        data["events"].append({"id": i, "t": t, "k": "med", "title": title, "sub": f"Take 1 tablet daily · {status}"})
        data["details"][str(i)] = {"type": "med", "instructions": "Take 1 tablet daily", "status": status, "src": "Fictional Clinic",
                                   "prescriber": "Dr. Rowan Vale", "rxnorm": "9990001", **({"end": end} if end else {})}
        i += 1
    for t, title in (("2025-02-05T17:00:00", "EXAMPLOCIN 5MG TAB"), ("2025-03-07T17:00:00", "EXAMPLOCIN 5MG TAB"), ("2025-05-14T17:00:00", "EXAMPLOCIN 10MG TAB")):
        data["events"].append({"id": i, "t": t, "k": "rx", "title": title, "sub": "Pharmacy fill · 30-day supply"})
        i += 1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


RANGE_RECORDS = 990201


def add_range_records(build):
    """Two creatinine results from a fictional outside lab whose range (0.6–1.2 mg/dL) isn't the demo clinic's
    (0.7–1.3), between the clinic's own (#58): one chart with two ranges. 1.25 is high by its own lab's range, though
    inside the clinic's."""
    path = os.path.join(build, "data.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    cr = next(s for s in data["labs"] if s["name"] == "Creatinine")
    i = RANGE_RECORDS
    for t, v, flag in (("2026-07-15T09:10:00", 0.95, ""), ("2026-09-10T09:05:00", 1.25, "H")):
        cr["pts"].append([t, v, 0.6, 1.2, flag, str(v)])
        data["events"].append({"id": i, "t": t, "k": "lab", "title": "1 lab result", "sub": "CREATININE"})
        data["details"][str(i)] = {"type": "labday", "ids": [i + 1]}
        data["events"].append({"id": i + 1, "t": t, "k": "labitem", "title": "CREATININE", "sub": "Final · Fictional Outside Lab", "hidden": 1})
        data["details"][str(i + 1)] = {"type": "result", "order": "CREATININE", "collected": t[:16].replace("T", " "), "resulted": t[:10],
                                       "status": "Final", "specimen": "Blood", "lab": "Fictional Outside Lab", "dx": [],
                                       "rows": [["Creatinine", str(v), "mg/dL", "0.6 - 1.2", flag]], "report": [], "comments": []}
        i += 2
    cr["pts"].sort(key=lambda p: p[0])
    cr["n"] = len(cr["pts"])
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
    add_med_records(os.path.join(TMP, "build"))
    add_range_records(os.path.join(TMP, "build"))
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


USER_MENU = {"#logout", "#settingsLink", "#sharingLink", "#theme"}


def menu_click(pg, item):
    """Click an item of the top bar's menus (nav.js): open its menu first, as a person would."""
    pg.click("#userMenuBtn" if item in USER_MENU else "#addMenuBtn")
    pg.click(item)


def stopped(e):
    """A stopped check's error, with the lines of Playwright's call log that say why (what covered an element,
    what it waited for), so a failure on CI's machines can be read without running it again."""
    lines = str(e).splitlines()
    why = [l.strip() for l in lines[1:] if any(w in l for w in ("intercepts pointer", "not visible", "not stable", "outside of the viewport",
                                                                  "waiting for", "element is", "retrying", "resolved to"))]
    seen = []
    for l in why:
        if l not in seen:
            seen.append(l)
    return lines[0] + ("" if not seen else " | " + " | ".join(seen[:6]))


# what phone_fits measures, in the page (#61)
PHONE_FITS_JS = r"""(() => {
  const vw = document.documentElement.clientWidth, out = [];
  const name = e => {
    const t = (e.getAttribute("aria-label") || e.textContent || e.value || e.getAttribute("placeholder") || "").replace(/\s+/g, " ").trim().slice(0, 30);
    return e.tagName.toLowerCase() + (e.id ? "#" + e.id : "") + (t ? ` "${t}"` : "");
  };
  const sw = document.documentElement.scrollWidth;
  if (sw > vw) {   // name the outermost boxes that stick out, leaving out what scrolls inside a box of its own or is fixed
    const free = e => { for (let a = e.parentElement; a && a !== document.body; a = a.parentElement) { const cs = getComputedStyle(a);
      if (cs.overflowX !== "visible" || cs.position === "fixed") return false; } return getComputedStyle(e).position !== "fixed"; };
    const wide = [...document.body.querySelectorAll("*")].filter(e => e.getBoundingClientRect().right > vw + 0.5 && !e.closest("svg *") && free(e))
      .map(e => [e, e.getBoundingClientRect().right]);
    const top = wide.filter(([e]) => !wide.some(([p]) => p !== e && p.contains(e))).slice(0, 3).map(([e, r]) => name(e) + " to " + Math.round(r));
    out.push(`the page scrolls sideways: ${sw} px wide (${top.join(", ")})`);
  }
  const CONTROLS = "a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [role=tab], [role=switch], [role=checkbox], [role=radio], [tabindex]:not([tabindex='-1'])";
  const layer = e => {   // a control's layer: a fixed or sticky bar, a popup or dialog, or the page itself
    for (let a = e; a && a !== document.body; a = a.parentElement) {
      const cs = getComputedStyle(a);
      if (cs.position === "fixed" || cs.position === "sticky" || a.matches("dialog, [popover]") || (cs.position === "absolute" && cs.zIndex !== "auto")) return a;
    }
    return document.body;
  };
  const items = [];
  for (const e of document.querySelectorAll(CONTROLS)) {
    if (e.closest("svg, [inert]") || !e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) continue;
    // the part that can be seen: cut to every box around it that scrolls or hides its overflow (a row of tabs that
    // scrolls sideways shows only some of them); none if it's clipped away (visually hidden)
    let rects = [...e.getClientRects()].map(r => ({l: r.left, r: r.right, t: r.top, b: r.bottom})), hidden = false;
    for (let a = e; a && a !== document.documentElement; a = a.parentElement) {
      const cs = getComputedStyle(a);
      if ((cs.clip && cs.clip !== "auto") || (cs.clipPath && cs.clipPath !== "none")) { hidden = true; break; }
      if (a !== e && (cs.overflowX !== "visible" || cs.overflowY !== "visible")) {
        const c = a.getBoundingClientRect(), x = cs.overflowX !== "visible", y = cs.overflowY !== "visible";
        rects = rects.map(r => ({l: x ? Math.max(r.l, c.left) : r.l, r: x ? Math.min(r.r, c.right) : r.r, t: y ? Math.max(r.t, c.top) : r.t, b: y ? Math.min(r.b, c.bottom) : r.b}));
      }
      if (cs.position === "fixed") break;
    }
    rects = rects.filter(r => r.r - r.l > 2 && r.b - r.t > 2);   // nothing left to see, or a 1 px visually-hidden box
    if (hidden || !rects.length) continue;
    const l = Math.min(...rects.map(r => r.l)), r = Math.max(...rects.map(r => r.r)), b = Math.max(...rects.map(r => r.b)), lay = layer(e);
    if (r <= 0 || b + scrollY <= 0 || (l >= vw && lay !== document.body)) continue;   // moved off the screen on purpose: a skip link, a closed drawer
    if (l < -0.5 || r > vw + 0.5) out.push(`${name(e)} is cut off at the screen's edge (${Math.round(l)} to ${Math.round(r)} px)`);
    items.push({e, rects, lay});
  }
  for (let i = 0; i < items.length; i++) for (let j = i + 1; j < items.length; j++) {
    const a = items[i], b = items[j];
    if (a.lay !== b.lay || a.e.contains(b.e) || b.e.contains(a.e)) continue;
    if (a.rects.some(p => b.rects.some(q => Math.min(p.r, q.r) - Math.max(p.l, q.l) > 2 && Math.min(p.b, q.b) - Math.max(p.t, q.t) > 2)))
      out.push(`${name(a.e)} overlaps ${name(b.e)}`);
  }
  return out;
})()"""


def phone_fits(page, label="", ignore=()):
    """What doesn't fit a phone-width page (#61), as a list of problems, empty when it fits. Set the viewport first
    (375 px wide; the sweep "every page at 375 px" uses 375 x 800) and open what is to be measured: a tab, a group, a
    menu or panel. It measures what is drawn now and changes nothing, so it also works under the real CSP.

    Reported:
    - the page scrolls sideways (the document is wider than the viewport), naming the outermost boxes that stick out;
    - a control cut off at the screen's edge: part of it on screen, part off;
    - two controls overlapping by more than 2 px each way, in the same layer.
    A control is a link, button, input, select, textarea, summary, an element with a widget role (button, tab,
    switch, checkbox, radio) or one in the tab order. SVG content (chart marks) is left out.

    Not reported, so these don't need ignoring: controls that aren't drawn (display:none, visibility:hidden,
    opacity 0, inside a closed <details> or an [inert] part); the part of a control scrolled out of sight inside a
    box that scrolls (the tab bar, the period presets, a sidebar's row of groups, a wide table's wrapper) or hidden
    by its overflow; a control clipped away or 2 px or less across (a visually-hidden skip link), or moved wholly
    off the screen to the left or top (a skip link until it's focused), or to the right inside a fixed panel (the
    closed drawer); a control inside another (a button in a summary); and controls in different layers — a fixed or
    sticky bar, a <dialog> or [popover], or an absolutely positioned box with a z-index (an open menu, the drawer) is
    meant to lie over the page.

    label goes before each problem ("Sources #upload: …"). ignore is a list of substrings: a problem containing one
    is dropped, for a known one owned by another issue (say which, beside it)."""
    found = [p for p in page.evaluate(PHONE_FITS_JS) if not any(i in p for i in ignore)]
    return [f"{label}: {p}" if label else p for p in found]


def answer_ask(page, yes=True, fill=None, timeout=10000):
    """Answer the app's own dialog (ask() in util.js, #210) as a person would, after the click that opens it: wait for
    it, type fill into its box if given, then click its action button (yes) or Cancel, and wait until it's gone.
    Returns [title, message], for checks on what it asked. (The app asks nothing in the browser's own pop-ups: main()
    counts any as a problem.)"""
    box = page.wait_for_selector("dialog.sg-ask[open]", timeout=timeout)
    said = box.evaluate("d => [d.querySelector('h2').textContent, d.querySelector('.sg-ask-msg').textContent]")
    if fill is not None:
        box.query_selector("input").fill(fill)
    box.query_selector(".sg-ask-acts button" + (":last-child" if yes else ":first-child")).click()
    box.wait_for_element_state("hidden", timeout=timeout)
    return said


# ---- axe-core (#202): Deque's accessibility engine, from the axe-playwright-python package (requirements-dev.txt), which
# bundles a pinned axe.min.js. Only that script is used: put into the page under test by page.evaluate, which the
# Content-Security-Policy doesn't apply to, so the strict pages are checked as they are; the app never loads it.
AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]   # WCAG 2.0, 2.1 and 2.2, levels A and AA
try:
    import axe_playwright_python
    with open(os.path.join(os.path.dirname(axe_playwright_python.__file__), "axe.min.js"), encoding="utf-8") as _f:
        AXE_JS = _f.read()
except (ImportError, OSError):
    AXE_JS = None
AXE_RUN = """tags => axe.run(document, {runOnly: {type: 'tag', values: tags}, resultTypes: ['violations']}).then(r => r.violations.map(v => ({
  id: v.id, impact: v.impact, n: v.nodes.length, at: v.nodes.slice(0, 3).map(n => n.target.join(' ')),
  why: (v.nodes[0].failureSummary || '').split('\\n').slice(1, 2).join('').trim().slice(0, 160)})))"""


def axe_check(page, label):
    """axe-core's WCAG A and AA rules on the page as it is drawn now (open what is to be checked first: a tab, a menu, a
    dialog). Returns (serious, other): the serious and critical findings, which fail a check, and the moderate and minor
    ones, which are listed; each "label: rule (impact) ×n at selector, … — what's wrong"."""
    if AXE_JS is None:
        return [f"{label}: axe-core isn't installed (pip install --require-hashes -r requirements-dev.txt)"], []
    if not page.evaluate("typeof axe === 'object' && typeof axe.run === 'function'"):
        page.evaluate(AXE_JS)
    serious, other = [], []
    for v in page.evaluate(AXE_RUN, AXE_TAGS):
        (serious if v["impact"] in ("serious", "critical") else other).append(
            f"{label}: {v['id']} ({v['impact']}) ×{v['n']} at {', '.join(v['at'])} — {v['why']}")
    return serious, other


# a stop of Tab (#202): what has focus, whether it looks any different from when it hasn't (2.4.7), and whether all of
# it is under a fixed or sticky bar (2.4.11). Focus is taken away and given back to compare, which keeps :focus-visible.
FOCUS_STOP_JS = r"""(() => {
  const a = document.activeElement; if (!a || a === document.body || a === document.documentElement) return null;
  const t = (a.getAttribute('aria-label') || a.textContent || a.value || '').replace(/\s+/g, ' ').trim().slice(0, 30);
  const name = a.tagName.toLowerCase() + (a.id ? '#' + a.id : '') + (t ? ` "${t}"` : '');
  const again = window.__fw.has(a); window.__fw.add(a);
  const look = () => { const c = getComputedStyle(a); return [c.outlineStyle !== 'none' && parseFloat(c.outlineWidth) > 0 ? c.outlineStyle + c.outlineWidth + c.outlineColor : '',
    c.boxShadow, c.backgroundColor, c.borderColor, c.color, c.textDecorationLine, c.fill, c.stroke, c.strokeWidth, c.transform].join('|'); };
  const on = look(), r = a.getBoundingClientRect();
  a.blur(); const still = a.matches(':focus-within'), off = look(); a.focus({preventScroll: true});   // still: a date field's calendar button keeps focus
  const bar = e => { for (let p = e; p && p !== document.body; p = p.parentElement) { const cs = getComputedStyle(p); if (cs.position === 'fixed' || cs.position === 'sticky') return true; } return false; };
  const covered = r.width > 4 && r.height > 4 && [[r.left + 2, r.top + 2], [r.right - 2, r.top + 2], [r.left + 2, r.bottom - 2], [r.right - 2, r.bottom - 2], [(r.left + r.right) / 2, (r.top + r.bottom) / 2]]
    .every(([x, y]) => { const e = document.elementFromPoint(x, y); return e && e !== a && !a.contains(e) && !e.contains(a) && !bar(a) && bar(e); });
  return {name, again, shown: still ? on.split('|')[0] !== '' : on !== off, covered};
})()"""


def focus_walk(page, label, back=False, stops=60):
    """Tab (or Shift+Tab from the page's end) through up to `stops` controls; the problems found, each "label: control …".
    A stop that shows no change when focused, or lies wholly under a sticky or fixed bar, is a problem."""
    if back:
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight); (f => { if (f) { f.tabIndex = -1; f.focus(); } })(document.querySelector('.pagefoot, footer'))")
    else:
        page.evaluate("document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0)")
    page.evaluate("window.__fw = new WeakSet()")
    out = []
    for _ in range(stops):
        page.keyboard.press("Shift+Tab" if back else "Tab")
        s = page.evaluate(FOCUS_STOP_JS)
        if not s:
            continue
        if s["again"]:   # round to the start
            break
        if not s["shown"]:
            out.append(f"{label}: {s['name']} shows no sign of focus")
        if s["covered"]:
            out.append(f"{label}: {s['name']} has focus under a sticky bar")
    return out


def main():
    srv, base = start_app()
    problems, passed = [], []

    def check(name, ok, detail=""):
        (passed if ok else problems).append(f"{name}{': ' + str(detail) if detail != '' else ''}")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        # the app asks in its own dialog (#210): a browser pop-up (confirm, alert, prompt) on any page is a problem,
        # dismissed, never quietly accepted. Every page made from here on watches for one.
        def native_dialog(d):
            problems.append(f"a browser {d.type}() pop-up (#210: use ask() or sayIn): {d.message[:120]}")
            d.dismiss()
        new_page, new_context = browser.new_page, browser.new_context
        def watched_page(*a, **k):
            pg = new_page(*a, **k)
            pg.on("dialog", native_dialog)
            return pg
        def watched_context(*a, **k):
            c = new_context(*a, **k)
            c.on("page", lambda pg: pg.on("dialog", native_dialog))
            return c
        browser.new_page, browser.new_context = watched_page, watched_context
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
        # the Sources page (#166): its old addresses open their sections, each part drawn by its own script
        strict.goto(base + "/import.html")
        strict.wait_for_url("**/sources.html#upload", timeout=10000)
        strict.wait_for_selector("#importer", state="visible", timeout=10000)
        check("under the CSP: the Sources page's scripts run, import.html opening Upload files",
              strict.evaluate("document.querySelectorAll('#profile option').length") == 2 and strict.evaluate(
                  "[...document.querySelectorAll('#srcNav a')].map(a => a.textContent)") == ["Overview", "Upload files", "Type records in",
                  "Paste a Health Link", "Portals and health plans", "Phone and watch", "Import history"])
        for sel in ("#srcList h3", "#historyList li", "#watch .w-addr", "#connList p"):
            strict.wait_for_selector(sel, state="attached", timeout=10000)
        check("…every section drawn, only the group's shown", strict.evaluate(
              "['importer', 'typeIn', 'healthLink', 'mine', 'watch', 'history', 'srcOverview'].filter(id => getComputedStyle(document.getElementById(id)).display !== 'none')") == ["importer"])
        null_cards = "[...document.querySelectorAll('main .card')].filter(c => /\\bnull\\b/.test(c.textContent)).map(c => c.id || c.className)"
        check("no Sources card shows a stray “null”", strict.evaluate(null_cards) == [], strict.evaluate(null_cards))
        strict.goto(base + "/settings.html")
        strict.wait_for_selector("#sec-overview .set-labs li", timeout=10000)
        check("under the CSP: settings.js runs", strict.evaluate("document.querySelectorAll('#sections > section').length") == 10)
        check("no Settings card shows a stray “null” (#175)", strict.evaluate(null_cards) == [], strict.evaluate(null_cards))
        check("…and Settings has no Watch and scale any more (#166)", not strict.evaluate("!!document.getElementById('watch')")
              and "Watch and scale" not in strict.evaluate("document.getElementById('setNav').textContent"))
        strict.goto(base + "/settings.html?profile=1#watch")   # its old address: Sources → Phone and watch
        strict.wait_for_url("**/sources.html?profile=1#phone", timeout=10000)
        strict.wait_for_selector("#watch h2", timeout=10000)
        check("under the CSP: the watch card shows the upload address, on Sources", strict.evaluate("document.querySelector('#watch .w-addr').textContent").endswith(":8731/ingest")
              and strict.evaluate("document.querySelector('#srcNav a[aria-current=page]').dataset.group") == "phone")
        # the Admin page (#167): its sidebar of groups, each part drawn by its own script
        strict.goto(base + "/admin.html")
        for sel in ("#userList table", "#sec-sync .set", "#sourceList", "#dirSources ul", "#labmapList", "#outsideBody h3", "#acctLog tbody tr", "#bkStatus .kv"):
            strict.wait_for_selector(sel, state="attached", timeout=10000)
        groups = strict.evaluate("[...document.querySelectorAll('#admNav a')].map(a => a.textContent)")
        check("under the CSP: the Admin page's scripts run, with a sidebar of its groups", groups ==
              ["Accounts", "Backups", "Connected-source apps", "Provider directory", "Lab name matches", "Outside connections", "Install activity"], groups)
        strict.wait_for_selector("#adminLink:not([hidden])", state="attached", timeout=10000)
        check("…and the menu offers Admin to an admin", strict.evaluate("document.getElementById('adminLink').getAttribute('href')") == "admin.html")
        check("no Admin card shows a stray “null”", strict.evaluate(null_cards) == [], strict.evaluate(null_cards))
        # the Sharing page (#168): its sidebar, each part drawn by its own script
        strict.goto(base + "/sharing.html#summary")   # the visit summary (#48)
        strict.wait_for_selector("#build:not([hidden]) #sections input", timeout=10000)
        check("under the CSP: summary.js runs", strict.evaluate("document.querySelectorAll('#sections input').length") == 5)
        strict.wait_for_selector("#calendar:not([hidden])", state="attached", timeout=10000)   # in its own group, so not visible
        strict.wait_for_selector("#memberList tbody tr", state="attached", timeout=10000)
        strict.click("#shrNav a[data-group=activity]")   # drawn when its group is opened
        strict.wait_for_selector("#actBody .activity tbody tr", timeout=10000)
        groups = strict.evaluate("[...document.querySelectorAll('#shrNav a')].map(a => a.textContent)")
        check("under the CSP: the Sharing page's scripts run, with a sidebar of its groups, Profile set apart", groups ==
              ["People", "Share links", "Calendar feed", "Visit summary", "Export everything", "Activity", "Profile"]
              and strict.evaluate("document.querySelector('#shrNav a[data-group=profile]').closest('li').previousElementSibling.classList.contains('set-nav-part')"), groups)
        check("no Sharing card shows a stray “null”", strict.evaluate(null_cards) == [], strict.evaluate(null_cards))
        # ---- shorter help text (#176): a card leads with a short line, and the rest of its explanation is behind one
        # "More about this" toggle, the same on every page: closed at first, still in the page when closed, opened and
        # closed by keyboard, readable in dark mode (this page's theme) and at 375 px. What someone needs to decide
        # safely stays in the lead, outside the toggle.
        more_js = """[...document.querySelectorAll('details.more')].map(d => { const s = d.querySelector(':scope > summary'), cs = getComputedStyle(s);
            return {at: (d.parentElement.closest('[id]') || {}).id, open: d.open, says: s.textContent, inside: d.textContent.length > s.textContent.length + 20,
                    look: [cs.fontSize, cs.color, cs.cursor].join(' ')}; })"""
        looks, seen_more = set(), {}
        for path, ready in (("sources.html?profile=1#upload", "#watch .w-addr"), ("sharing.html?profile=1#links", "#links details.more"),
                            ("admin.html#labs", "#dirAdd details.more"), ("", "#view-overview .card")):
            strict.goto(base + "/" + path)
            strict.wait_for_selector(ready, state="attached", timeout=10000)
            found = strict.evaluate(more_js)
            seen_more[path] = sorted({f["at"] for f in found})
            looks |= {f["look"] for f in found}
            check(f"{path or 'records'}: help beyond a card's lead is behind “More about this”, closed, still in the page (#176)",
                  found and all(not f["open"] and f["says"] == "More about this" and f["inside"] for f in found), found)
        check("…on the cards of every page that has them", all(set(seen_more[k]) >= want for k, want in (
              ("sources.html?profile=1#upload", {"importer", "panel-manual", "panel-link", "mine", "finder", "watch"}),
              ("sharing.html?profile=1#links", {"linksBody", "calendar", "summaryIntro", "export"}),
              ("admin.html#labs", {"users", "admin", "directory", "dirAdd", "labmaps", "installLog"}),
              ("", {"view-timeline", "labsChart", "labsHeatmap"}))), seen_more)
        dark = [int(x) for x in re.findall(r"\d+", next(iter(looks)).split(" ", 1)[1])[:3]] if looks else [0, 0, 0]
        check("…styled once: the same toggle everywhere, light on the dark theme", len(looks) == 1 and sum(dark) > 450, looks)
        strict.goto(base + "/sharing.html?profile=1#links")
        strict.wait_for_selector("#links details.more", timeout=10000)
        links_lead = strict.evaluate("document.querySelector('#links .share-links > p.hint').textContent")
        strict.click("#shrNav a[data-group=export]")
        export_lead = strict.evaluate("document.querySelector('#export > p.hint').textContent")
        check("…while what a person needs to decide stays in the leads: a share link's reach, an export's privacy", "Whoever has the link can open it" in links_lead
              and "as private as the records" in export_lead, (links_lead, export_lead))
        strict.goto(base + "/sources.html?profile=1#history")
        strict.wait_for_selector("#historyList li", timeout=10000)
        check("…and that undoing an import removes what it brought, with Fetch the only time a Health Link's server is contacted",
              "Undoing an import removes the records" in strict.inner_text("#history")
              and "only when you choose Fetch" in strict.evaluate("document.querySelector('#healthLink p.hint').textContent"))
        strict.click("#srcNav a[data-group=upload]")
        summ = "#importer details.more > summary"
        strict.focus(summ)
        strict.keyboard.press("Enter")
        opened = strict.evaluate("[document.querySelector('#importer details.more').open, document.querySelector('#importer details.more p').checkVisibility()]")
        strict.set_viewport_size({"width": 375, "height": 800})
        strict.wait_for_timeout(200)
        fits = strict.evaluate("document.documentElement.scrollWidth <= 375 && document.querySelector('#importer details.more').getBoundingClientRect().right <= 375")
        strict.keyboard.press("Enter")
        check("…opened by keyboard, its text shown, fitting 375 px; closed again by keyboard", opened == [True, True] and fits
              and not strict.evaluate("document.querySelector('#importer details.more').open"), (opened, fits))
        strict.set_viewport_size({"width": 1400, "height": 900})
        # ---- keyboard and screen-reader basics (#209), under the CSP: on every page with the top bar, Tab's first stop
        # is "Skip to content", shown while it has focus (readable on this page's dark theme, at 1400 and 375 px), and
        # Enter moves focus to the page's main part without changing the address; the browser tab's title names the
        # view shown; a group picked in a sidebar is said through the page's one shared status line (util.js)
        def contrast(c1, c2):   # WCAG contrast of two computed "rgb(…)" colours
            def lum(c):
                f = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
                r, g, b = [f(int(x) / 255) for x in re.findall(r"\d+", c)[:3]]
                return 0.2126 * r + 0.7152 * g + 0.0722 * b
            hi, lo = sorted((lum(c1), lum(c2)), reverse=True)
            return round((hi + 0.05) / (lo + 0.05), 1)

        def said(pg, box="sgStatus", tries=30):   # what the shared status line (or alert) says, once it says it
            for _ in range(tries):
                pg.wait_for_timeout(50)
                t = pg.evaluate(f"document.getElementById('{box}').textContent")
                if t:
                    return t
            return ""
        focused = """(() => { const a = document.activeElement, r = a.getBoundingClientRect(), cs = getComputedStyle(a);
            return {text: a.textContent, top: r.top, right: r.right, color: cs.color, bg: cs.backgroundColor}; })()"""
        skips, titles = {}, {}
        for path, ready, title, width in (("", "#view-overview .card", None, 1400), ("sources.html?profile=1", "#srcList h3", "Sources: Overview", 375),
                                          ("settings.html?profile=1", "#sec-overview .set-labs li", "Settings: Dashboard", 1400),
                                          ("sharing.html?profile=1", "#memberList tbody tr", "Sharing: People", 375),
                                          ("account.html", "#acctNav a", "Account: Sign-in and security", 1400), ("admin.html", "#userList table", "Admin: Accounts", 375),
                                          ("guide.html?profile=1", "#step-name:not([hidden])", "Getting started: Whose records", 375)):   # #199
            strict.set_viewport_size({"width": width, "height": 800})
            strict.goto(base + "/" + path)
            strict.wait_for_selector(ready, state="attached", timeout=10000)
            strict.wait_for_timeout(200)
            title = title or strict.evaluate("document.querySelector('[role=tab][aria-selected=true]').textContent") + " · My records"
            hash_was = strict.evaluate("location.hash")
            strict.keyboard.press("Tab")
            f = strict.evaluate(focused)
            strict.keyboard.press("Enter")
            skips[path or "records"] = [f["text"], f["top"] >= 0 and f["right"] <= width, contrast(f["color"], f["bg"]),
                                        strict.evaluate("document.activeElement === document.querySelector('main')"), strict.evaluate("location.hash") == hash_was]
            titles[path or "records"] = [strict.title(), title + " · Sanguity"]
        strict.set_viewport_size({"width": 1400, "height": 900})
        check("on every page with the top bar, Tab's first stop is “Skip to content”, shown while focused, readable on the dark theme, at 1400 and 375 px (#209)",
              len(skips) == 7 and all(v[0] == "Skip to content" and v[1] and v[2] >= 4.5 for v in skips.values()), skips)
        check("…Enter moves focus to the page's main part, the address unchanged", all(v[3] and v[4] for v in skips.values()), skips)
        check("…and the browser tab's title names what's shown: “Labs · My records · Sanguity”, “Settings: Dashboard · Sanguity”",
              all(a == b for a, b in titles.values()), titles)
        check("…every page has the one shared status line: role=status, and role=alert for errors", strict.evaluate(
              "[...document.querySelectorAll('.sg-live')].map(x => [x.id, x.getAttribute('role')])") == [["sgStatus", "status"], ["sgAlert", "alert"]])
        group_said = {}
        for path, ready, link, want in (("sharing.html?profile=1", "#memberList tbody tr", "#shrNav a[data-group=links]", "Sharing: Share links"),
                                        ("settings.html?profile=1", "#sec-overview .set-labs li", "#setNav a[data-group=display]", "Settings: Display")):
            strict.goto(base + "/" + path)
            strict.wait_for_selector(ready, state="attached", timeout=10000)
            strict.wait_for_timeout(200)
            strict.evaluate("document.getElementById('sgStatus').textContent = ''")
            strict.click(link)
            group_said[path] = [strict.title(), said(strict), want]
        check("…a group picked in a sidebar becomes the title, and is said through the status line (a link says nothing of what it shows)",
              all(t == w + " · Sanguity" and s == w for t, s, w in group_said.values()), group_said)
        strict.goto(base + "/providers.html?q=medicare")   # find your provider (#93): the bundled list, searched
        strict.wait_for_selector(".dir-hit", timeout=10000)
        check("under the CSP: providers.js searches the directory", "Medicare" in strict.evaluate("document.querySelector('.dir-hit').textContent"))
        strict.wait_for_selector("#srcNote:not([hidden])", timeout=10000)
        note = strict.evaluate("document.getElementById('srcNote').textContent")
        check("…saying which lists aren't loaded, with no command: an admin gets a link to Admin (#167)", "build_directory" not in note and "python" not in note
              and strict.evaluate("document.querySelector('#srcNote a').getAttribute('href')") == "admin.html#directory", note)
        check("…in the connect step of Sources → Portals and health plans (#166)", strict.evaluate("location.pathname + location.search + location.hash")
              == "/sources.html?q=medicare#portals" and strict.evaluate("getComputedStyle(document.getElementById('mine')).display") != "none")
        strict.click(".dir-hit")
        strict.wait_for_selector("#detail h3", timeout=10000)
        detail = strict.inner_text("#detail")
        set_up = strict.evaluate("document.querySelector('#detail a[href^=\"admin.html?\"]').getAttribute('href')")
        check("…a provider not set up here sends an admin to set it up, with no copy of the vendor's steps",
              "Not set up on this server yet" in detail and "Return address" not in detail and "Scopes" not in detail
              and set_up.startswith("admin.html?vendor=insurer&issuer=https") and set_up.endswith("#apps"), (detail[:300], set_up))
        strict.goto(base + "/" + set_up)
        strict.wait_for_selector("#guide ol li", timeout=10000)
        check("…where Admin has the steps (one copy), the form filled in with the provider",
              strict.evaluate("[document.getElementById('fVendor').value, document.getElementById('fIssuer').value.startsWith('https://'), "
                              "document.getElementById('fName').value]") == ["insurer", True, "Medicare (Blue Button 2.0)"]
              and "Blue Button" in strict.inner_text("#guide"), strict.evaluate("document.getElementById('fName').value"))
        # ---- share links (#47), under the CSP: an owner makes one on the Sharing page, saying what a holder sees; a
        # browser with no session opens it (at 1400 and 375 px) and sees only the chosen sections and lab, with no
        # cookie set; the view is in the profile's activity; once revoked, the link says it doesn't work
        def sl_until(pg, expr, tries=100):   # wait_for_function evaluates a string, which the CSP forbids; evaluate isn't
            for _ in range(tries):
                if pg.evaluate(expr):
                    return True
                pg.wait_for_timeout(100)
            raise TimeoutError(expr)
        strict.goto(base + "/sharing.html?profile=1#links")
        strict.wait_for_selector("#links .share-links button:has-text('New share link')", timeout=10000)
        mine = strict.locator("#links")
        check("share links: the panel is open in its group, with no button to open it",
              strict.evaluate("document.querySelector('#links .share-links-wrap > button').hidden && !document.querySelector('#links .share-links').hidden"))
        mine.locator("button", has_text="New share link").click()
        form = mine.locator(".share-form")
        form.locator("input[aria-label='Name for this link']").fill("Dr. Fictional")
        form.locator("input[value=medications]").uncheck()
        form.locator("input[aria-label=From]").fill("")
        form.locator(".share-labs input[type=radio]").nth(1).check()
        lab = form.locator(".share-labpick input").first.get_attribute("value")
        form.locator(".share-labpick input").first.check()
        sees = form.locator(".share-sees").inner_text()
        check("share links: the form says plainly what a holder of the link can see",
              all(w in sees for w in ("Anyone who has this link", "My records", "Visits and hospital stays", f"only {lab}", "of any date",
                                      "no documents", "nothing can be changed", "logged")) and "Medications" not in sees, sees[:400])
        form.locator("button[type=submit]").click()
        strict.wait_for_selector(".share-made .share-url", timeout=10000)
        share_url = strict.input_value(".share-made .share-url")
        check("share links: the link is shown once, with its token after the #", re.fullmatch(re.escape(base) + r"/share\.html#[\w-]{43}", share_url), share_url)
        check("share links: the list names it, not yet opened", "Dr. Fictional" in mine.locator(".share-links table").inner_text()
              and "never" in mine.locator(".share-links table").inner_text())
        holder = browser.new_context(viewport={"width": 1400, "height": 900})   # the real CSP, no cookies, no session
        hp = holder.new_page()
        hp.on("request", elsewhere)
        hp.on("pageerror", lambda e: csp_problems.append(f"share page script error: {e}"))
        hp.on("console", lambda m: m.type == "error" and "Content Security Policy" in m.text and csp_problems.append(m.text))
        api_paths = []
        hp.on("request", lambda r: "/api/" in r.url and api_paths.append(r.url.split("/api/", 1)[1]))
        r = hp.goto(share_url)
        check("share links: the page is sent with the pages' CSP", "script-src 'self'" in (r.headers.get("content-security-policy") or ""))
        hp.wait_for_selector("#share-visits", timeout=10000)
        shown = hp.evaluate("[...document.querySelectorAll('#shareBody > section')].map(s => s.id)")
        check("share links: only the chosen sections", shown == ["share-visits", "share-results"], shown)
        labs_shown = hp.evaluate("[...document.querySelectorAll('#share-results h3')].map(h => h.textContent)")
        check("share links: only the chosen lab", len(labs_shown) == 1 and labs_shown[0].startswith(lab), labs_shown)
        check("share links: the page says whose records, until when, and not medical advice",
              "My records" in hp.inner_text("#shareHead") and "stops working on" in hp.inner_text("#shareHead") and "Not medical advice" in hp.inner_text("main"))
        check("share links: the token never appears in an API address, and only the view is asked for", api_paths == ["share/view"], api_paths)
        check("share links: no cookie is set for the holder", holder.cookies() == [], holder.cookies())
        hp.set_viewport_size({"width": 375, "height": 800})
        hp.wait_for_timeout(200)
        check("share links: the shared page fits 375 px", hp.evaluate("document.documentElement.scrollWidth") <= 375, hp.evaluate("document.documentElement.scrollWidth"))
        shared_bad = phone_fits(hp, "light")   # #61: and no control cut off or overlapping, in light and dark
        shared_axe = axe_check(hp, "share.html, light")   # #202: and axe-core's WCAG A and AA rules, under the real CSP
        hp.emulate_media(color_scheme="dark")
        hp.wait_for_timeout(100)
        shared_bad += phone_fits(hp, "dark") + ([] if hp.evaluate("matchMedia('(prefers-color-scheme: dark)').matches") else ["dark: not dark"])
        shared_axe = [a + b for a, b in zip(shared_axe, axe_check(hp, "share.html, dark"))]
        hp.emulate_media(color_scheme=None)
        check("share links: …with no control cut off or overlapping, in light and dark (#61)", not shared_bad, shared_bad[:8] or "")
        check("share links: …and no serious or critical axe-core finding, in light and dark (WCAG 2.2 AA, #202)", not shared_axe[0],
              (shared_axe[0] + shared_axe[1])[:8] or "")
        strict.set_viewport_size({"width": 375, "height": 800})
        strict.wait_for_timeout(200)
        check("share links: the owner's panel fits 375 px", strict.evaluate("document.documentElement.scrollWidth") <= 375, strict.evaluate("document.documentElement.scrollWidth"))
        strict.set_viewport_size({"width": 1400, "height": 900})
        strict.goto(base + "/sharing.html?profile=1#activity")   # the same page: Activity is read afresh when it's opened
        sl_until(strict, "[...document.querySelectorAll('#actBody .activity tbody tr')].some(r => r.textContent.includes('opened the share link “Dr. Fictional”'))")
        row = strict.evaluate("[...document.querySelectorAll('#actBody .activity tbody tr')].find(r => r.textContent.includes('opened the share link')).textContent")
        check("share links: the view is in the profile's activity, as the share link", "share link" in row and share_url.split("#")[1] not in row, row)
        strict.click("#shrNav a[data-group=links]")
        strict.wait_for_selector("#links .share-links button:has-text('Revoke')", timeout=10000)
        mine.locator(".share-links button", has_text="Revoke").first.click()
        answer_ask(strict)
        sl_until(strict, "document.querySelector('#links .share-links table').textContent.includes('revoked')")
        hp.reload()
        sl_until(hp, "document.querySelector('#shareHead h2').textContent === \"This link doesn't work\"")
        check("share links: a revoked link shows nothing", hp.evaluate("document.getElementById('shareBody').children.length") == 0)
        hp.goto(base + "/share.html#not-a-real-token-at-all-0000000000000000000")
        sl_until(hp, "document.querySelector('#shareHead h2').textContent === \"This link doesn't work\"")
        check("share links: a made-up link says it doesn't work", True)
        holder.close()
        strict.goto(base + "/account.html")
        strict.wait_for_selector("#userMenuBtn", state="visible")
        strict.wait_for_timeout(500)
        menu_click(strict, "#logout")
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
        check("Body and activity setting saved before profiles carries over", js("hState.stays === false && localStorage.getItem('ht-health') === null"))
        check("view saved before profiles carries over, its Lab heatmap tab now Labs as a heatmap (#172)",
              js("state.tab === 'labs' && state.labsView === 'heatmap' && state.preset === '1y' && localStorage.getItem('ht-state') === null && !!localStorage.getItem('ht-state:1')"))

        def open_tab(t):   # "heatmap" and "labs": the Labs tab as a heatmap or as charts (#172)
            page.click("#tab-" + ("labs" if t == "heatmap" else t))
            if t in ("labs", "heatmap"):
                page.click("#labsView button[data-view=%s]" % ("heatmap" if t == "heatmap" else "chart"))
        view_of = lambda t: "labs" if t == "heatmap" else t

        try:
            # ---- every tab renders its main content (Labs as a heatmap first, so it's left showing charts)
            expect = {
                "overview": "document.querySelectorAll('#view-overview .tile').length",
                "health": "[...document.querySelectorAll('#view-health svg')].filter(s => s._draw && s.childNodes.length > 3).length",
                "timeline": "document.querySelectorAll('#tl circle.ev').length + ',' + document.querySelectorAll('#list .row').length",
                "heatmap": "document.querySelectorAll('#hm rect.cell').length + ',' + +!document.getElementById('labsHeatmap').hidden + ',' + +document.getElementById('labsChart').hidden",
                "labs": "[...document.querySelectorAll('#charts .chart svg')].filter(s => s.childNodes.length > 3).length + ',' + +!document.getElementById('labsChart').hidden",
                "costs": "document.querySelectorAll('#view-costs .stat').length",
            }
            for tab, probe in expect.items():
                open_tab(tab)
                settle()
                n = js(probe)
                visible = js(f"!document.getElementById('view-{view_of(tab)}').hidden")
                check(f"{ {'heatmap': 'Labs (heatmap)', 'labs': 'Labs (charts)'}.get(tab, tab)} tab renders", visible and all(int(x) > 0 for x in str(n).split(",")), n)
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
            flagged = {   # the tab's out-of-range highlighting, and its caption (Labs has one for both its views)
                "overview": "#view-overview .tile .st.hi, #view-overview .tile .st.lo",
                "heatmap": "#hm rect.cell[style*='--div-hi'], #hm rect.cell[style*='--div-lo']",
                "labs": "#charts .pt.abn",
            }
            theme_before = js("document.documentElement.dataset.theme || null")
            for theme in ("light", "dark"):
                js("t => { document.documentElement.dataset.theme = t; }", theme)
                for width in (1400, 375):
                    page.set_viewport_size({"width": width, "height": 900 if width > 375 else 800})
                    for tab, marks in flagged.items():
                        open_tab(tab)
                        settle(300)
                        note = page.locator(f"#view-{view_of(tab)} .range-note")
                        shown = note.count() == 1 and note.is_visible()
                        text = note.inner_text() if shown else ""
                        check(f"{tab} ({theme}, {width} px): caption beside the out-of-range highlighting",
                              shown and js(f"document.querySelectorAll({marks!r}).length") > 0
                              and "reference ranges in your own lab reports" in text and "isn't medical advice" in text, text)
                        legible = js("""(() => { const n = document.querySelector('#view-%s .range-note'), f = document.getElementById('notice');
                            const bg = getComputedStyle(document.body).backgroundColor;
                            return getComputedStyle(n).color !== bg && getComputedStyle(f).color !== bg; })()""" % view_of(tab))
                        check(f"{tab} ({theme}, {width} px): caption and footer readable, page fits",
                              legible and js("document.documentElement.scrollWidth") <= width, js("document.documentElement.scrollWidth"))
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            page.set_viewport_size({"width": 1400, "height": 900})

            # ---- charts for everyone (#60): every chart has Chart | Table (a pick-one pair, #193) and its table has
            # column headers and rows; the heatmap's table has a header per lab and per draw day; the Timeline's chart
            # points to its list; a Labs chart and a Body and activity chart take focus and step through their points
            # from the keyboard, with the hover's readout and tooltip, said to screen readers; out-of-range results are
            # marked by shape or words as well as colour (Labs charts, heatmap cells and table, Overview tiles, the
            # drawer's results); the tables fit 375 px in light and dark
            js("window._was60 = [new Set(state.sel), state.preset, state.from, state.to]; state.tables.clear(); state.sel = new Set(['Hemoglobin']); applyPreset('all'); switchTab('labs', 'chart'); renderLabs()")
            settle(400)
            seg = js("""(() => { const s = document.querySelector('#charts .chart .hd .view-seg');
                return s && [s.getAttribute('role'), s.getAttribute('aria-label'), [...s.children].map(b => [b.textContent, b.getAttribute('aria-pressed')])]; })()""")
            check("charts (#60): a Labs chart has Chart | Table, a pick-one pair with Chart selected",
                  seg == ["group", "Show Hemoglobin as", [["Chart", "true"], ["Table", "false"]]], seg)
            flagged = js("SERIES.find(s => s.name === 'Hemoglobin').pts.filter(p => p.ts >= state.from && p.ts <= state.to && p[4]).map(p => p[4])")
            marks = js("""[...document.querySelectorAll('#charts .chart svg .pt.abn')].map(m => [m.tagName, m.getAttribute('d') || ''])""")
            check("…its out-of-range results are triangles, ▲ above the range and ▼ below, not only red dots",
                  len(flagged) > 0 and len(marks) == len(flagged) and all(t == "path" and d.count("L") == 2 for t, d in marks)
                  and "▲▼" in page.inner_text("#labsChart .legend"), [len(flagged), len(marks), marks[:2]])
            label = js("document.querySelector('#charts .chart svg').getAttribute('aria-label')")
            check("…the chart takes focus, as a chart summed up in words",
                  js("(s => [s.getAttribute('tabindex'), s.getAttribute('role'), s.getAttribute('aria-roledescription')])(document.querySelector('#charts .chart svg'))") == ["0", "application", "chart"]
                  and re.match(r"Hemoglobin: \d+ results from .+ to .+, latest .+ g/dL, \d+ outside reference range\. Left and right arrow keys", label or ""), label)
            page.focus("#charts .chart svg")
            page.keyboard.press("End")
            settle(150)
            last = js("(s => { const p = s.pts.filter(p => p.ts <= state.to).at(-1); return [p[5] || fmtNum(p[1]), fmtDT(p.ts)]; })(SERIES.find(s => s.name === 'Hemoglobin'))")
            tipped = js("[getComputedStyle(tip).display, tip.innerText, document.querySelector('#charts .chart .read').textContent]")
            check("…End shows the last result, its tooltip and readout as when hovering", tipped[0] == "block" and last[0] in tipped[1]
                  and last[1] in tipped[1] and last[1] in tipped[2] and not tipped[2].startswith("Latest"), tipped)
            page.keyboard.press("ArrowLeft")
            settle(200)
            before = js("tip.innerText")
            check("…← the one before it, said to screen readers",
                  before != tipped[1] and before.split("\n")[0] in js("document.getElementById('sgStatus').textContent"), [before, js("document.getElementById('sgStatus').textContent")])
            page.keyboard.press("Home")
            settle(150)
            first = js("(s => fmtDT(s.pts.filter(p => p.ts >= state.from)[0].ts))(SERIES.find(s => s.name === 'Hemoglobin'))")
            check("…Home the first", first in js("tip.innerText"), js("tip.innerText"))
            page.keyboard.press("Escape")
            settle(100)
            check("…and Escape lets go: no tooltip, the readout back to the latest, focus kept",
                  js("getComputedStyle(tip).display") == "none" and js("document.querySelector('#charts .chart .read').textContent").startswith("Latest")
                  and js("document.activeElement === document.querySelector('#charts .chart svg')"))
            ring = js("(s => [getComputedStyle(s).outlineStyle, s.matches(':focus-visible')])(document.querySelector('#charts .chart svg'))")
            check("…a ring shows the focus", ring == ["solid", True], ring)
            page.click("#charts .chart .view-seg button:text-is('Table')")
            settle(200)
            t = js("""(() => { const t = document.querySelector('#charts .chart table.data');
                return t && {heads: [...t.querySelectorAll('thead th')].map(h => [h.getAttribute('scope'), h.textContent]), rows: t.querySelectorAll('tbody tr').length,
                  abn: [...t.querySelectorAll('tbody td.abn:last-child')].map(td => td.textContent), focus: document.activeElement.textContent + ':' + document.activeElement.getAttribute('aria-pressed')}; })()""")
            check("…Table shows its table instead, headed, a row per result, each out-of-range one saying ▲ High or ▼ Low; focus stays on Table",
                  t and t["heads"] == [["col", "Date"], ["col", "Value"], ["col", "Ref range"], ["col", "Flag"]] and t["rows"] > len(flagged) - 1
                  and len(t["abn"]) == len(flagged) and all(a in ("▲ High", "▼ Low") for a in t["abn"]) and t["focus"] == "Table:true"
                  and not js("document.querySelector('#charts .chart svg')"), t)
            # the heatmap: a mark on every out-of-range cell, and a table with a header per lab and per draw day
            js("state.tables.clear(); switchTab('labs', 'heatmap')")
            settle(400)
            cells = js("[document.querySelectorAll('#hm rect.cell[style*=\"--div-hi\"], #hm rect.cell[style*=\"--div-lo\"]').length, document.querySelectorAll('#hm .hm-mark').length]")
            check("heatmap (#60): every out-of-range cell is marked ▲ or ▼ as well as coloured, and the legend says so",
                  cells[0] > 0 and cells[0] == cells[1] and "▲ above the range" in page.inner_text("#hmLegend"), cells)
            nrows, ncols = js("document.querySelectorAll('#hm text.row').length"), js("new Set([...document.querySelectorAll('#hm rect.cell')].map(c => c.dataset.k)).size")
            page.click("#hmHd .view-seg button:text-is('Table')")
            settle(300)
            ht = js("""(() => { const t = document.querySelector('#hmTable table');
                return t && [document.getElementById('hmWrap').hidden, document.getElementById('hmTable').hidden, t.querySelectorAll('tbody th[scope=row]').length,
                  t.querySelectorAll('thead th[scope=col]').length, [...t.querySelectorAll('td.abn .vh')].map(v => v.textContent.trim()),
                  t.querySelectorAll('tbody th[scope=rowgroup]').length]; })()""")
            check("…Table: a real table, a header for each lab and each draw day, out-of-range results said high or low",
                  ht and ht[0] and not ht[1] and ht[2] == nrows and ht[3] == ncols + 1 and len(ht[4]) == cells[0]
                  and set(ht[4]) <= {"high", "low"} and ht[5] > 0, ht and ht[:4] + [len(ht[4]), ht[5]] + [nrows, ncols])
            # Body and activity: each card's charts, or one table; a chart steps from the keyboard
            js("state.tables.clear(); switchTab('health')")
            settle(500)
            check("Body and activity (#60): every card with charts has Chart | Table",
                  js("[...document.querySelectorAll('#view-health .view-seg')].map(s => s.dataset.key)") == ["#h-body", "#h-sleep", "#h-activity", "#h-heart"])
            page.focus("#h-body svg")
            page.keyboard.press("Home")
            settle(150)
            hk = js("[document.querySelector('#h-body svg').getAttribute('aria-label'), getComputedStyle(tip).display, tip.innerText, document.querySelector('#h-body svg .cross').getAttribute('visibility')]")
            check("…its charts step from the keyboard too, with the hover's crosshair and tooltip", re.match(r"Weight: \d+ readings from", hk[0] or "")
                  and hk[1] == "block" and " lb" in hk[2] and hk[3] == "visible", hk)
            page.keyboard.press("Escape")
            for k in ("#h-body", "#h-sleep", "#h-activity", "#h-heart"):
                page.click(f"{k} .view-seg button:text-is('Table')")
                settle(200)
            ht = js("['#h-body', '#h-sleep', '#h-activity', '#h-heart'].map(k => document.querySelector(k + ' svg') ? -1 : document.querySelectorAll(k + ' table.data tbody tr').length)")
            check("…Table shows each card's values as a table, a row per day or week", all(n > 0 for n in ht), ht)
            # Costs: both charts
            js("state.tables.clear(); switchTab('costs')")
            settle(500)
            for key in ("#costBars", "#paidCum"):
                page.click(f"#view-costs .view-seg[data-key='{key}'] button:text-is('Table')")
                settle(200)
            ct = js("['#costBars', '#paidCum'].map(k => document.querySelector(`.view-seg[data-key='${k}']`).closest('.card').querySelectorAll('.chart-table tbody tr').length)")
            check("Costs (#60): the claims by month and the running totals each have a table", all(n > 0 for n in ct), ct)
            # the Timeline: its list is the table
            js("switchTab('timeline')")
            settle(300)
            check("Timeline (#60): the chart says its events are listed below, and they are",
                  "listed under Events, below" in js("document.getElementById('tl').getAttribute('aria-label')") and js("document.querySelectorAll('#list .row').length") > 0)
            # not by colour alone, elsewhere: the Overview's tiles and the drawer's results
            js("switchTab('overview')")
            settle(300)
            ov = js("""[...document.querySelectorAll('#view-overview .tile')].filter(t => t.querySelector('.st.hi, .st.lo'))
                .map(t => [t.querySelector('.st').textContent, !!t.querySelector('svg .spark-abn'), !t.querySelector('svg circle')])""")
            check("Overview (#60): an out-of-range tile says High or Low, and its sparkline ends in ▲ or ▼, not a red dot",
                  ov and all(s in ("▲ High", "▼ Low") and m and c for s, m, c in ov), ov)
            js("openEvent(EV.find(e => e.k === 'lab' && e.abn))")
            settle(300)
            dr = js("""[...document.querySelectorAll('#dbody table.data tbody tr')].filter(r => r.querySelector('td.abn')).map(r => r.lastElementChild.textContent)""")
            check("drawer (#60): every out-of-range result says ▲ High or ▼ Low beside its colour, and the count says what it counts",
                  dr and all(x in ("▲ High", "▼ Low") for x in dr) and "outside reference range" in js("document.querySelector('#dbody .badge.abn')?.textContent || ''"), dr)
            page.keyboard.press("Escape")
            # the tables and the Chart | Table pairs at 375 px, light and dark
            theme_before = js("document.documentElement.dataset.theme || null")
            page.set_viewport_size({"width": 375, "height": 800})
            bad = []
            for theme in ("light", "dark"):
                js("t => { document.documentElement.dataset.theme = t; }", theme)
                for tab, keys in (("labs", ["Hemoglobin"]), ("heatmap", ["#hm"]), ("health", ["#h-body", "#h-sleep", "#h-activity", "#h-heart"]), ("costs", ["#costBars", "#paidCum"])):
                    for table in (False, True):
                        js("([keys, on]) => { state.tables.clear(); if (on) keys.forEach(k => state.tables.add(k)); }", [keys, table])
                        js("t => t === 'heatmap' ? switchTab('labs', 'heatmap') : t === 'labs' ? switchTab('labs', 'chart') : switchTab(t)", tab)
                        settle(400)
                        bad += phone_fits(page, f"{tab} {'table' if table else 'chart'} ({theme})")
                        if table:
                            look = js("""(() => { const td = document.querySelector('.chart-table td, .hm-table td'); const bg = getComputedStyle(document.body).backgroundColor;
                                return td && [getComputedStyle(td).color, bg]; })()""")
                            if not look or look[0] == look[1]:
                                bad.append(f"{tab} table ({theme}): unreadable {look}")
            check("charts (#60): the tables and Chart | Table fit 375 px and read, in light and dark", not bad, bad[:8])
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            js("state.tables.clear(); [state.sel, state.preset, state.from, state.to] = window._was60; state.labsView = 'chart'; save()")
            page.set_viewport_size({"width": 1400, "height": 900})

            # ---- each result against its own range (#58): the demo's creatinine (0.7–1.3 mg/dL) with two results from
            # an outside lab (0.6–1.2, add_range_records): the chart's band steps with each result's range and its
            # header says the ranges differ; its table gives each row its own; 1.25 is high by its own lab's range though
            # inside the clinic's; the heatmap and the drawer judge each result by its own; a series whose ranges agree
            # has one band and no note; the chart and its table fit 375 px, in light and dark
            js("window._was58 = [new Set(state.sel), state.preset, state.from, state.to]; state.tables.clear(); state.sel = new Set(['Creatinine', 'Hemoglobin']); applyPreset('all'); switchTab('labs', 'chart'); renderLabs()")
            settle(400)
            cr_card = "[...document.querySelectorAll('#charts .chart')].find(c => c.querySelector('.name').textContent === 'Creatinine')"
            hb_card = "[...document.querySelectorAll('#charts .chart')].find(c => c.querySelector('.name').textContent === 'Hemoglobin')"
            note = js(f"{cr_card}.querySelector('.hd .rdiff')?.textContent || ''")
            check("own ranges (#58): a chart whose results' ranges differ says which, in its header",
                  note.startswith("Reference range differs between results: 0.7–1.3 and 0.6–1.2 mg/dL.") and "steps with each result's own range" in note, note)
            bands = js(f"""[...{cr_card}.querySelectorAll('svg .refband')].map(r => [+r.getAttribute('y'), +r.getAttribute('height'), +r.getAttribute('x'), +r.getAttribute('width')])""")
            geo = js(f"{cr_card}.querySelector('svg')._geom")
            check("…its band steps with each result's own range: two ranges, five steps (clinic, outside, clinic, outside, clinic) across the chart",
                  len(bands) == 5 and len({(round(b[0], 1), round(b[1], 1)) for b in bands}) == 2
                  and abs(bands[0][2] - geo["L"]) < .5 and abs(sum(b[3] for b in bands) - (geo["R"] - geo["L"])) < 1, [bands, geo])
            check("…its edges are each one stepped line", js(f"{cr_card}.querySelectorAll('svg path.refedge').length") == 2
                  and "V" in js(f"{cr_card}.querySelector('svg path.refedge').getAttribute('d')"))
            hi = js("SERIES.find(s => s.name === 'Creatinine').pts.filter(p => p[4]).map(p => [p[0], p[1], p[2], p[3], p[4]])")
            check("…1.25 mg/dL is high by its own lab's range (0.6–1.2), though inside the clinic's, and is marked ▲",
                  ["2026-09-10T09:05:00", 1.25, 0.6, 1.2, "H"] in hi and js(f"{cr_card}.querySelectorAll('svg .pt.abn').length") == len(hi), hi)
            check("…the chart says so to screen readers",
                  "Reference range differs between results: 0.7–1.3 and 0.6–1.2 mg/dL" in js(f"{cr_card}.querySelector('svg').getAttribute('aria-label')"))
            check("…a chart whose results' ranges agree has one band and no note",
                  js(f"{hb_card}.querySelectorAll('svg .refband').length") == 1 and not js(f"!!{hb_card}.querySelector('.rdiff')"))
            js("state.tables.add('Creatinine'); renderCharts()")
            settle(200)
            rows = js(f"""[...{cr_card}.querySelectorAll('table.data tbody tr')].map(r => [...r.cells].map(c => c.textContent))""")
            outside = [r for r in rows if r[0].startswith(("Jul 15, 2026", "Sep 10, 2026"))]
            check("…its table gives each row its own range, and the note says so",
                  len(outside) == 2 and all(r[2].startswith("0.6 – 1.2") for r in outside)
                  and all(r[2].startswith("0.7 – 1.3") for r in rows if r not in outside) and "▲ High" in [r[3] for r in outside]
                  and js(f"{cr_card}.querySelector('.rdiff').textContent").endswith("Each row gives its own."), outside)
            js("state.tables.clear(); switchTab('labs', 'heatmap')")
            settle(400)
            hm = js("""(() => { const s = SERIES.find(s => s.name === 'Creatinine'), p = s.pts.find(p => p[0].startsWith('2026-09-10'));
                return [hmLevel(p[1], p[2], p[3]), [...document.querySelectorAll('#hm text.row')].find(t => t.firstChild.textContent === 'Creatinine')?.querySelector('title').textContent]; })()""")
            check("heatmap (#58): the outside lab's 1.25 is coloured high against its own range; the row's name says the ranges differ",
                  hm[0] > 0 and "Reference range differs between results: 0.7–1.3 and 0.6–1.2 mg/dL" in (hm[1] or ""), hm)
            js("openEvent(EV.find(e => e.k === 'lab' && e.t.startsWith('2026-09-10')))")   # its id is the database's
            page.wait_for_selector("#drawer.open #dbody table.data", timeout=10000)
            dr = js("[...document.querySelectorAll('#dbody table.data tbody tr')].map(r => [...r.cells].map(c => c.textContent)).find(c => c[0].includes('Creatinine'))")
            check("drawer (#58): the outside lab's result with its own range, said High", dr and re.match(r"0\.6 ?[-–] ?1\.2", dr[2]) and "High" in dr[-1], dr)
            page.keyboard.press("Escape")
            js("switchTab('overview')")
            settle(300)
            sp = js("""[...document.querySelectorAll('#view-overview .tile')].find(t => t.textContent.includes('Creatinine'))?.querySelectorAll('svg .spark-ref').length ?? -1""")
            check("Overview (#58): the creatinine tile's sparkline shades each result's own range, stepped", sp == 5, sp)
            # 375 px, light and dark: the note under the name, in the chart and in the table
            theme_before = js("document.documentElement.dataset.theme || null")
            page.set_viewport_size({"width": 375, "height": 800})
            bad = []
            for theme in ("light", "dark"):
                js("t => { document.documentElement.dataset.theme = t; }", theme)
                for table in (False, True):
                    js("on => { state.tables.clear(); if (on) state.tables.add('Creatinine'); switchTab('labs', 'chart'); }", table)
                    settle(400)
                    bad += phone_fits(page, f"Creatinine {'table' if table else 'chart'} ({theme})")
                    look = js(f"""(n => n && [getComputedStyle(n).color, getComputedStyle(document.body).backgroundColor, n.getBoundingClientRect().right])({cr_card}.querySelector('.rdiff'))""")
                    if not look or look[0] == look[1] or look[2] > 375:
                        bad.append(f"note ({theme}, {'table' if table else 'chart'}): {look}")
            check("own ranges (#58): the chart and table with the note fit 375 px and read, in light and dark", not bad, bad[:6])
            page.set_viewport_size({"width": 1400, "height": 900})
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            js("state.tables.clear(); [state.sel, state.preset, state.from, state.to] = window._was58; state.labsView = 'chart'; save()")

            # ---- detail drawer for every kind of event
            for k in js("[...new Set(EV.filter(e => !e.hidden).map(e => e.k))].sort()"):
                js("k => openEvent(EV.find(e => e.k === k && !e.hidden))", k)
                settle(200)
                ok = js("document.getElementById('drawer').classList.contains('open') && document.getElementById('dtitle').textContent.length > 0")
                check(f"drawer opens for {k}", ok)
                page.keyboard.press("Escape")
            check("drawer closes on Escape", not js("document.getElementById('drawer').classList.contains('open')"))

            # ---- renaming where a name is shown (#38): a lab from its Trends chart shows the new name on every tab and in
            # the drawer, the name in the records on hover; back to it from the heatmap; an import from the drawer's
            # Source line; a source tag from under a Trends table; the form fits 375 px; viewers get no ✎
            lab = "Hemoglobin"
            js("n => { switchTab('labs'); applyPreset('all'); state.sel.add(n); renderLabs(); }", lab)
            settle(300)
            page.click(f"#charts .chart .hd [data-rename=lab][data-key='{lab}']")
            page.wait_for_selector(".rename-box input", timeout=5000)
            check("rename: ✎ on a Trends chart opens a form with the name in it", js("document.querySelector('.rename-box input').value") == lab)
            page.fill(".rename-box input", "Hgb (renamed)")
            page.keyboard.press("Enter")
            page.wait_for_function("n => CFG.labNicknames[n] === 'Hgb (renamed)' && !document.querySelector('.rename-box')", arg=lab, timeout=10000)
            settle(300)
            hd = js("n => { const b = document.querySelector(`#charts .chart .hd [data-key='${n}']`), h = b && b.parentElement; return h && [h.querySelector('.name').textContent, h.querySelector('.name').title]; }", lab)
            check("…the chart is called by the new name, the name in the records on hover", hd == ["Hgb (renamed)", f"In the records: {lab}"], hd)
            check("…and so is the list of labs", "Hgb (renamed)" in js("[...document.querySelectorAll('#series .nm')].map(x => x.textContent)"))
            check("…focus goes back to its ✎", js("document.activeElement && document.activeElement.dataset.rename") == "lab")
            check("…kept on the server", js("n => api('profiles/1/settings').then(r => r.json()).then(j => j.labNicknames[n])", lab) == "Hgb (renamed)")
            js("switchTab('overview')")
            settle(300)
            check("…the Overview tile too", js("n => [...document.querySelectorAll('#view-overview .tile .lbl span[title]')].filter(s => s.title === n).map(s => s.textContent)", lab) == ["Hgb (renamed)"])
            js("switchTab('heatmap')")
            settle(300)
            row = f"#hm text.row[data-key='{lab}']"
            check("…the heatmap row too", page.locator(row).count() == 1 and page.locator(row).text_content().startswith("Hgb (renamed)"),
                  page.locator(row).text_content() if page.locator(row).count() else None)
            rid = js("n => { const e = EV.find(e => (DATA.details[e.id] || {}).type === 'result' && (DATA.details[e.id].rows || []).some(r => r[0] === n)); return e && e.id; }", lab)
            js("id => openEvent(byId.get(id))", rid)
            settle(200)
            cell = js("n => { const td = [...document.querySelectorAll('#dbody table.data td')].find(td => td.title === 'In the records: ' + n); return td && td.textContent; }", lab)
            check("…and the drawer's results, with the name in the records beside it", cell and "Hgb (renamed)" in cell and f"({lab})" in cell, cell)
            page.keyboard.press("Escape")
            page.click(row)
            page.wait_for_selector(".rename-box", timeout=5000)
            page.click(".rename-box button:text-is('Use the original name')")
            page.wait_for_function("n => !(n in CFG.labNicknames) && !document.querySelector('.rename-box')", arg=lab, timeout=10000)
            settle(300)
            check("…and back to the name in the records from the heatmap", page.locator(row).text_content().startswith(lab))
            # an import, from the drawer's Source line
            js("id => openEvent(byId.get(id))", rid)
            settle(200)
            imp = js("id => byId.get(id).imp", rid)
            label = js("i => DATA.imports[i].label", imp)
            page.click("#dbody > .source [data-rename=import]")
            page.wait_for_selector(".rename-box input", timeout=5000)
            page.fill(".rename-box input", "Fictional clinic portal")
            page.keyboard.press("Enter")
            page.wait_for_function("i => DATA.imports[i].name === 'Fictional clinic portal' && !document.querySelector('.rename-box')", arg=imp, timeout=10000)
            settle(200)
            src = js("document.querySelector('#dbody > .source') && document.querySelector('#dbody > .source').textContent")
            check("rename: an import from the drawer, its label kept beside it", js("document.getElementById('drawer').classList.contains('open')")
                  and src and "Fictional clinic portal" in src and f"({label})" in src, src)
            check("…in the import history too", js("i => api('profiles/1/imports').then(r => r.json()).then(j => j.imports.find(e => e.id === i).name)", imp) == "Fictional clinic portal")
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            page.click("#dbody > .source [data-rename=import]")
            page.wait_for_selector(".rename-box input", timeout=5000)
            box = js("(() => { const r = document.querySelector('.rename-box').getBoundingClientRect(); return [r.left, r.right, document.documentElement.scrollWidth]; })()")
            check("…the rename form fits 375 px", box[0] >= 0 and box[1] <= 375 and box[2] <= 375, box)
            page.keyboard.press("Escape")
            check("…Escape closes the form, not the drawer", js("!document.querySelector('.rename-box') && document.getElementById('drawer').classList.contains('open')"))
            page.set_viewport_size({"width": 1400, "height": 900})
            settle(200)
            page.click("#dbody > .source [data-rename=import]")
            page.wait_for_selector(".rename-box", timeout=5000)
            page.click(".rename-box button:text-is('Use the original name')")
            page.wait_for_function("i => !DATA.imports[i].name", arg=imp, timeout=10000)
            page.keyboard.press("Escape")
            # a source tag, from under the Trends table (the demo's results carry none, so one is given here)
            js("n => { switchTab('labs', 'chart'); const s = SERIES.find(s => s.name === n); s.pts[s.pts.length - 1][6] = 'EXT'; state.tables.add(n); renderLabs(); }", lab)
            settle(200)
            page.click("#charts .tags [data-rename=tag][data-key=EXT]")
            page.wait_for_selector(".rename-box input", timeout=5000)
            page.fill(".rename-box input", "Outside lab")
            page.keyboard.press("Enter")
            page.wait_for_function("CFG.sources.EXT === 'Outside lab' && !document.querySelector('.rename-box')", timeout=10000)
            settle(200)
            check("rename: a source tag from under the Trends table, shown in the table",
                  js("[...document.querySelectorAll('#charts table.data td')].some(td => td.textContent.includes('Outside lab'))")
                  and "Outside lab (EXT)" in js("document.querySelector('#charts .tags').textContent"))
            page.click("#charts .tags [data-rename=tag][data-key=EXT]")
            page.wait_for_selector(".rename-box", timeout=5000)
            page.click(".rename-box button:text-is('Use the original name')")
            page.wait_for_function("!('EXT' in CFG.sources)", timeout=10000)
            check("…viewers get no ✎", js("""(() => { const role = PROFILE.role; PROFILE.role = 'viewer'; renderLabs();
                const n = document.querySelectorAll('#charts [data-rename], #dbody [data-rename]').length; PROFILE.role = role; return n; })()""") == 0)
            js("n => { const s = SERIES.find(s => s.name === n); s.pts[s.pts.length - 1].length = 6; state.tables.delete(n); renderLabs(); }", lab)

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
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && (DATA.edits || []).length === 0 && booted", timeout=20000)
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
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && (DATA.edits || []).length === 0 && booted", timeout=20000)
            check("…and undoing it restores the title", js(f"({there})", pick))
            page.keyboard.press("Escape")

            # ---- the journal (#49): a symptom added from an event's drawer, pre-dated to it, shown in the Journal lane
            # and as a marker on Trends sized by its severity; its home on the Timeline tab with the filters (#173); deleted
            # again at the end
            check("the journal starts empty", js("JOURNAL.length === 0 && !document.getElementById('journalBtn').hidden"))
            js("switchTab('overview')")
            menu_click(page, "#journalBtn")
            settle(200)
            check("Add → Journal entry opens the new-entry form, over the tab you were on (#173)",
                  js("document.getElementById('dtitle').textContent === 'New journal entry' && state.tab === 'overview'")
                  and page.input_value("#dbody input[type=date]") == js("isoDay(Date.now())") and page.is_visible("#dbody .jform input[type=text]"),
                  js("[document.getElementById('dtitle').textContent, state.tab]"))
            check("…saying whose journal, with more than one profile", js("document.getElementById('dsub').textContent").endswith(" · My records"),
                  js("document.getElementById('dsub').textContent"))
            page.click("#dbody .jform button:text-is('Cancel')")
            settle(300)
            check("…and Cancel closes it, giving focus back to Add", js("!drawer.classList.contains('open') && document.activeElement.id === 'addMenuBtn'"),
                  js("document.activeElement.id"))
            page.click("#tab-timeline")
            settle()
            home = js("""(() => { const c = document.getElementById('journalHome'), cards = [...document.querySelectorAll('#view-timeline > .card')];
                return c && {title: c.querySelector('h2').textContent, at: cards.indexOf(c), tl: cards.findIndex(x => x.contains(document.getElementById('tl'))),
                ev: cards.findIndex(x => x.contains(document.getElementById('list'))), add: !!c.querySelector('button.primary'), rows: c.querySelectorAll('.jrow').length}; })()""")
            check("the journal has a home on the Timeline tab, between the timeline and the events, with New entry even when empty",
                  home and home["title"] == "Journal" and home["tl"] < home["at"] < home["ev"] and home["add"] and home["rows"] == 0, home)
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
            js("closeDrawer()")   # the entry's panel covers the tab bar's right end on narrower or wider fonts
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
            js("id => openEvent(byId.get(id))", entry["id"])
            settle(200)
            page.click("#dbody .btns button:text-is('All journal entries')")
            settle(300)
            check("“All journal entries” goes to the journal's home on the Timeline tab, focused",
                  js("state.tab === 'timeline' && !drawer.classList.contains('open') && document.activeElement.id === 'journalHome'")
                  and page.inner_text("#journalHome h2") == "Journal (1)" and page.locator("#journalHome .jrow").count() == 1,
                  js("[state.tab, document.activeElement.id]"))
            jtoggles = r"""[...document.querySelectorAll('#journalHome .jfilters .toggle')].map(t => [t.textContent.trim().replace(/ \d+$/, ''),
              t.querySelector(':scope > input[type=checkbox]') ? String(t.querySelector('input').checked) : t.getAttribute('aria-pressed')])"""
            check("the journal's kinds and “Only the dates shown” are on/off toggle chips that say true or false (#193)",
                  js(jtoggles) == [["Notes", "true"], ["Symptoms", "true"], ["Side effects", "true"], ["Only the dates shown", "false"]], js(jtoggles))
            jsum = "(p => p && !p.hidden ? p.textContent : null)(document.querySelector('#journalHome .fsum'))"   # what the filters leave out (#190)
            check("the journal's filters as they start: no filter line (#190)", js(jsum) is None, js(jsum))
            page.click("#journalHome .jfilters .toggle[data-kind=symptom]")   # the kinds are on/off chips (#193)
            check("…filtered by kind", page.locator("#journalHome .jrow").count() == 0 and "No entries match" in page.inner_text("#journalHome .jlist")
                  and js("document.querySelector('#journalHome .toggle[data-kind=symptom]').getAttribute('aria-pressed')") == "false")
            check("…a line under them says so, with Reset filters (#190)", js(jsum) == "Showing 2 of 3 kinds · Reset filters", js(jsum))
            page.click("#journalHome .jfilters .toggle[data-kind=symptom]")
            page.fill("#journalHome .jfilters input[type=search]", "after lunch")
            check("…and by what they say", page.locator("#journalHome .jrow").count() == 1)
            check("…said on that line too (#190)", js(jsum) == "Showing entries matching “after lunch” · Reset filters", js(jsum))
            page.check("#journalHome .jinrange input")
            js("id => setRange(byId.get(id).ts + 40 * DAY, byId.get(id).ts + 80 * DAY)", entry["id"])
            settle(300)
            check("…and, ticked, by the timeline's dates as they change", page.locator("#journalHome .jrow").count() == 0)
            js("id => setRange(byId.get(id).ts - 20 * DAY, byId.get(id).ts + 20 * DAY)", entry["id"])
            settle(300)
            check("…back in the dates shown, it's listed again", page.locator("#journalHome .jrow").count() == 1)
            page.uncheck("#journalHome .jinrange input")
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("the journal's home fits 375 px", js("(c => document.documentElement.scrollWidth <= 375 && c.scrollWidth <= c.clientWidth)(document.getElementById('journalHome'))"),
                  js("(c => [document.documentElement.scrollWidth, c.scrollWidth, c.clientWidth])(document.getElementById('journalHome'))"))
            theme_before = js("document.documentElement.dataset.theme || null")
            jbad = []
            for theme in ("light", "dark"):   # filtered, with its line (#190)
                js("t => { document.documentElement.dataset.theme = t; }", theme)
                settle(100)
                jbad += phone_fits(page, f"journal filtered, {theme}")
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            check("…filtered, its line and Reset filters fit too, in light and dark (#190)", js(jsum) and not jbad, jbad)
            page.set_viewport_size({"width": 1400, "height": 900})
            js("document.getElementById('sgStatus').textContent = ''")
            page.click("#journalHome .fsum-reset")
            check("…Reset filters puts every filter back, says so, focus in the journal's search (#190)", js(jsum) is None
                  and page.input_value("#journalHome .jfilters input[type=search]") == "" and page.locator("#journalHome .jrow").count() == 1
                  and js("[...document.querySelectorAll('#journalHome .jfilters .toggle')].every(t => (t.getAttribute('aria-pressed') || String(t.querySelector('input').checked)) === (t.dataset.kind ? 'true' : 'false'))")
                  and said(page) == "Filters reset." and js("document.activeElement.matches('#journalHome .jfilters input[type=search]')"), js(jsum))
            js("TAB_HIDDEN.add('timeline'); journalHome()")
            check("…with the Timeline tab hidden, the list opens in the panel instead",
                  js("document.getElementById('dtitle').textContent") == "Journal" and page.locator("#dbody .jrow").count() == 1)
            js("TAB_HIDDEN.delete('timeline'); closeDrawer()")
            settle(300)
            page.click("#journalHome .jrow")
            settle(200)
            page.click("#dbody .btns button:text-is('Delete')")
            page.click("#dbody .btns button.primary:text-is('Delete')")
            page.wait_for_function("typeof JOURNAL !== 'undefined' && EV.length > 0 && JOURNAL.length === 0 && booted && document.activeElement.id === 'journalHome'", timeout=20000)
            check("deleting the entry takes it off the timeline and Trends, back to the journal's home",
                  js("document.querySelectorAll('#charts .jmark, #tl .jmark').length") == 0 and page.inner_text("#journalHome h2") == "Journal")
            page.goto(base + "/account.html")
            page.wait_for_selector("#addMenuBtn", timeout=10000)
            menu_click(page, "#journalBtn")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && booted && document.getElementById('dtitle').textContent === 'New journal entry'", timeout=20000)
            check("Add → Journal entry on another page opens the records page with the form, the address naming its tab again (#208)",
                  js("drawer.classList.contains('open') && location.hash === tabHash() && location.hash !== '#new-journal-entry'"), js("location.href"))
            page.keyboard.press("Escape")
            page.click("#tab-timeline")
            settle()

            # ---- the medication timeline (#50): spans with the dose change, the stop and the fills; the history in the
            # drawer; the rows under each Trends chart when ticked; at 375 px
            page.click("#tab-timeline")
            js("setRange(dayTs('2025-01-01'), dayTs('2025-12-31'))")
            settle()
            med = js("""(() => { const m = MEDS.find(m => m.key === 'examplocin'); return m && {i: m.i, spans: m.spans.length, changes: m.changes.length,
                fills: m.fills.length, stop: m.stop, open: m.spans.map(s => s.open)}; })()""")
            check("the records' medication is laid out as spans: a dose change, a stop, three fills",
                  med and med["spans"] == 2 and med["changes"] == 1 and med["fills"] == 3 and med["stop"] == "2025-09-30" and med["open"] == [False, False], med)
            drawn = js(f"""({{bars: document.querySelectorAll('#tl .medbar[data-med="{med["i"]}"]').length,
                change: document.querySelectorAll('#tl .medchange').length, stop: document.querySelectorAll('#tl .medstop').length,
                fills: document.querySelectorAll('#tl .medfill[data-med="{med["i"]}"]').length,
                name: [...document.querySelectorAll('#tl text.medname')].some(t => t.textContent.startsWith('Examplocin')),
                dots: [...document.querySelectorAll('#tl circle[data-id]')].filter(c => ['med', 'rx'].includes(byId.get(+c.dataset.id)?.k)).length,
                demoOpen: MEDS.filter(m => m.spans.some(s => s.open)).length}})""") if med else {}
            check("…drawn in the Medications lane: two bars, a change tick, a stop cap and a fill mark each, no dots",
                  drawn.get("bars") == 2 and drawn.get("change", 0) >= 1 and drawn.get("stop", 0) >= 1 and drawn.get("fills") == 3 and drawn.get("name") and drawn.get("dots") == 0, drawn)
            check("…the demo's medications, listed with no end, run on as open spans", drawn.get("demoOpen", 0) >= 3, drawn)
            if med:
                bar = page.locator(f'#tl .medbar[data-med="{med["i"]}"][data-span="0"]')
                bar.hover()
                tip = page.inner_text("#tip")
                check("hovering a span says what the records list, dated in this person's format", "Examplocin" in tip and "Dr. Rowan Vale" in tip
                      and js("fmtD(dayTs('2025-02-03'))") in tip, tip)
                bar.click()
                settle(200)
                body = page.inner_text("#dbody")
                check("clicking a span opens the medication's history: its listings, the change and the fills",
                      js("document.getElementById('dtitle').textContent").startswith("Examplocin") and page.locator("#dbody table.medspans tbody tr").count() == 2
                      and page.locator("#dbody .medchanges li").count() == 1 and page.locator("#dbody table.medfills tbody tr").count() == 3
                      and "Discontinued" in body and "9990001" in body and js("fmtD(dayTs('2025-09-30'))") in body, body[:400])
                check("…saying what the records say, not advice", "This isn't medical advice" in body
                      and not re.search(r"\b(should|recommend|missed|too (high|low))\b", body, re.I), body[-300:])
                page.click("#dbody table.medfills tbody tr >> nth=0")
                settle(200)
                check("a fill in the history opens the fill, which leads back to the history",
                      js("byId.get(state.selEv)?.k") == "rx" and page.locator("#dbody button:text-is('Medication history')").count() == 1)
                page.click("#dbody button:text-is('Medication history')")
                settle(200)
                page.set_viewport_size({"width": 375, "height": 800})
                settle(300)
                check("the medication history fits 375 px", js("document.documentElement.scrollWidth <= 375 && document.getElementById('dbody').scrollWidth <= document.getElementById('dbody').clientWidth"),
                      js("[document.documentElement.scrollWidth, document.getElementById('dbody').scrollWidth]"))
                page.keyboard.press("Escape")
                settle(200)
                narrow = js(f"""(() => {{ const svg = document.getElementById('tl'), r = svg.getBoundingClientRect();
                    const bars = [...svg.querySelectorAll('.medbar[data-med="{med["i"]}"]')].map(b => b.getBoundingClientRect());
                    return {{fits: document.documentElement.scrollWidth <= 375 && r.right <= 375, bars: bars.length, inside: bars.every(b => b.left >= r.left - 1 && b.right <= r.right + 1),
                      names: [...svg.querySelectorAll('text.medname')].every(t => t.firstChild.textContent.length <= 13)}}; }})()""")
                check("the Medications lane fits 375 px, names shortened", narrow["fits"] and narrow["bars"] == 2 and narrow["inside"] and narrow["names"], narrow)
                page.set_viewport_size({"width": 1400, "height": 900})
                settle(200)
                page.click("#tab-labs")
                settle()
                if not js("document.querySelectorAll('#charts .chart svg').length"):
                    js("state.sel.add(SERIES.find(s => !s.watch && s.n >= 2).name); renderLabs()")
                    settle()
                check("Trends leaves the medications out until they're ticked", not page.is_checked("#optMeds") and js("document.querySelectorAll('#charts .medbar').length") == 0)
                page.check("#optMeds")
                settle()
                rows = js(f"""[...document.querySelectorAll('#charts .chart svg')].map(s => s.querySelectorAll('.medbar[data-med="{med["i"]}"]').length)""")
                check("…then every chart has the medication's spans under its time axis", len(rows) > 0 and all(n == 2 for n in rows), rows)
                page.locator(f'#charts .chart svg .medbar[data-med="{med["i"]}"] >> nth=0').click()
                settle(200)
                check("…and a span there opens the medication's history", js("document.getElementById('dtitle').textContent").startswith("Examplocin"))
                page.keyboard.press("Escape")
                page.set_viewport_size({"width": 375, "height": 800})
                settle(300)
                check("Trends with the medications fits 375 px", js("document.documentElement.scrollWidth <= 375"), js("document.documentElement.scrollWidth"))
                page.set_viewport_size({"width": 1400, "height": 900})
                page.uncheck("#optMeds")
                settle(200)
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
            page.wait_for_url("**/sources.html?profile=1#import-*", timeout=10000)
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
            shown = js(f"{json.dumps(labs3[:2])}.map(nice)")   # charts show a lab's nickname, if it has one (#38)
            check("picking a view charts its labs", picked() == sorted(labs3[:2]) and sorted(charted) == sorted(shown), charted)
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
            page.goto(base + "/settings.html?profile=1#sec-trends")
            page.wait_for_selector("[data-key='trends.views'] .set-view", timeout=10000)
            check("Settings lists the saved views, the default one ticked",
                  js("[...document.querySelectorAll(\"[data-key='trends.views'] .set-view\")].map(v => [v.querySelector('[data-k=name]').value, v.querySelector('[data-k=default]').checked])")
                  == [["CBC (browser check)", True], ["Kidney (browser check)", False]])
            check("…and fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.click("[data-key='trends.views'] button[aria-label='Remove the view Kidney (browser check)']")
            page.click("#sec-trends .set-foot button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-trends .set-foot > .msg').textContent.startsWith('Saved')", timeout=10000)
            page.set_viewport_size({"width": 1400, "height": 900})
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0", timeout=20000)
            js("switchTab('labs')")
            settle(300)
            check("a view removed on Settings is gone from Trends", js("[...document.querySelectorAll('#viewPick option')].map(o => o.value)") == ["CBC (browser check)"])
            page.click("#tvBox button:text-is('Delete')")
            answer_ask(page)
            page.wait_for_function("CFG.trends.views.length === 0", timeout=10000)
            check("deleting the last view from Trends leaves none", js("document.getElementById('viewPick').disabled") is True)
            if kept is not None:
                js(f"localStorage.setItem(stateKey(), {json.dumps(kept)})")
            page.reload()
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0", timeout=20000)
            js("switchTab('labs')")
            settle(300)

            # ---- keyboard and screen-reader basics (#209) on the records page (light theme): "Skip to content" readable;
            # the browser tab's title follows the tab, said only when the tab is opened from elsewhere (the tab bar
            # already says which tab is selected); a save and an error said through the shared status line and alert,
            # the message shown beside them too; the details drawer, covering the page at 375 px, keeps Tab inside
            # it, the page behind inert and out of Chromium's accessibility tree, focus given back on Escape; less
            # motion when the system asks for it
            page.reload()
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && booted", timeout=20000)
            page.keyboard.press("Tab")
            f = js(focused)
            check("records, light theme: “Skip to content” first, shown and readable (#209)", f["text"] == "Skip to content" and f["top"] >= 0
                  and contrast(f["color"], f["bg"]) >= 4.5, [f, contrast(f["color"], f["bg"])])
            js("switchTab('overview')")
            settle(200)
            js("document.getElementById('sgStatus').textContent = ''")
            page.click("#tab-labs")
            settle(300)
            in_bar = [page.title(), js("document.getElementById('sgStatus').textContent")]
            page.click("#tab-overview")
            settle(300)
            page.locator("#view-overview button", has_text="Open Costs").first.click()
            elsewhere_said = [page.title(), said(page)]
            check("the tab's title is the tab and the profile; picked in the tab bar, nothing more is said",
                  in_bar == ["Labs · My records · Sanguity", ""], in_bar)
            check("…opened from a button elsewhere, the tab is said through the status line", elsewhere_said == ["Costs · My records · Sanguity", "Costs"], elsewhere_said)
            js("switchTab('labs')")
            settle(300)
            js(f"state.sel = new Set({json.dumps(labs3[:1])}); renderLabs()")
            page.click("#viewSaveOpen")
            page.fill("#viewName", "")
            page.click("#viewSave")
            err = [said(page, "sgAlert"), js("document.querySelector('#tvBox .tv-msg').textContent"),
                   js("document.querySelector('#tvBox .tv-msg').classList.contains('bad')"), js("document.querySelector('#tvBox .tv-msg').getAttribute('role')")]
            js("document.getElementById('sgStatus').textContent = ''")
            page.fill("#viewName", "Said (browser check)")
            page.click("#viewSave")
            page.wait_for_function("CFG.trends.views.length === 1", timeout=10000)
            saved_said = [said(page), page.inner_text("#tvBox .tv-msg")]
            check("an error is said at once through the alert (role=alert), and shown beside the form, not said twice",
                  err == ["Give the view a name.", "Give the view a name.", True, None], err)
            check("…a save through the status line (role=status), shown beside it too", saved_said[0].startswith("Saved “Said (browser check)”")
                  and saved_said[0] == saved_said[1], saved_said)
            js("api('profiles/' + PROFILE.id + '/settings/trends.views', {method: 'PUT', body: JSON.stringify({value: []})})")   # (no view left behind)
            page.set_viewport_size({"width": 375, "height": 800})
            js("switchTab('timeline')")
            settle(300)
            page.focus("#list .row")
            page.keyboard.press("Enter")
            page.wait_for_selector("#drawer.open", timeout=5000)
            settle(300)
            behind = js("[...document.body.children].filter(x => x.inert).map(x => x.id || x.tagName.toLowerCase())")
            stops = []
            for _ in range(30):
                page.keyboard.press("Tab")
                stops.append(js("document.getElementById('drawer').contains(document.activeElement) ? 'drawer' : document.activeElement === document.body ? 'body' : document.activeElement.id || document.activeElement.className"))
            def ax_roles():   # the landmarks in Chromium's own accessibility tree, what a screen reader is given
                cdp = page.context.new_cdp_session(page)
                tree = cdp.send("Accessibility.getFullAXTree")["nodes"]
                cdp.detach()
                return sorted({n["role"]["value"] for n in tree if not n.get("ignored") and n.get("role", {}).get("value") in ("main", "banner", "complementary")})
            exposed = ax_roles()
            check("at 375 px the drawer covers the page: Tab stays inside it, the page behind inert (#209)",
                  "content" in behind and "drawer" not in behind and set(stops) <= {"drawer", "body"} and stops.count("drawer") > 20, [behind, stops])
            check("…and out of Chromium's accessibility tree: only the drawer is there", "main" not in exposed and "banner" not in exposed and "complementary" in exposed, exposed)
            page.keyboard.press("Escape")
            settle(300)
            check("…Escape closes it, the page usable again and focus back on the event it came from",
                  js("![...document.body.children].some(x => x.inert && x.id !== 'drawer') && document.getElementById('drawer').inert && document.activeElement.matches('#list .row')"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.focus("#list .row")
            page.keyboard.press("Enter")
            page.wait_for_selector("#drawer.open", timeout=5000)
            check("…at 1400 px it covers only part of the page, which stays usable", not js("document.querySelector('main').inert")
                  and ax_roles() == ["banner", "complementary", "main"], ax_roles())
            page.keyboard.press("Escape")
            motion = "[getComputedStyle(document.getElementById('drawer')).transitionDuration, smoothly()]"
            page.emulate_media(reduced_motion="reduce")
            less = js(motion)
            page.emulate_media(reduced_motion="no-preference")
            check("less motion when asked for: no transitions, no smooth scrolling (and both back when not)", less == ["0s", "auto"]
                  and js(motion) == ["0.18s", "smooth"], [less, js(motion)])
            page.reload()   # the saved views as the server has them again (none)
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && booted", timeout=20000)

            # ---- Body and activity: drag changes the records bar's period (#171)
            page.click("#tab-health")
            settle()
            hbox = page.locator("#view-health svg.pz").first.bounding_box()
            hbefore = js("hRange()")
            page.mouse.move(hbox["x"] + hbox["width"] * 0.4, hbox["y"] + hbox["height"] * 0.5)
            page.mouse.down()
            page.mouse.move(hbox["x"] + hbox["width"] * 0.7, hbox["y"] + hbox["height"] * 0.5, steps=8)
            page.mouse.up()
            settle()
            check("Body and activity drag pans", js("hRange()[0]") < hbefore[0] and js("state.preset") == "custom")
            check("…its own range: the date boxes show it, and More… says Custom dates (#191)",
                  js("[!document.getElementById('dates').hidden, document.getElementById('from').value === isoDay(state.from), document.getElementById('periodMoreBtn').textContent]")
                  == [True, True, "Custom dates▾"], js("[document.getElementById('dates').hidden, document.getElementById('periodMoreBtn').textContent]"))

            # ---- one period for every tab with time (#171): picked on one tab, the same dates on the others (Labs in
            # both its views, #172); a calendar year, a milestone or dates typed in; kept on a reload; Costs by month or
            # by year; the records bar at 375 px on each tab, and none on the Overview
            page.click("#tab-timeline")
            page.click("#presets button:text-is('1y')")
            settle()
            want = js("[state.from, state.to]")
            seen = {}
            for t in ("labs", "heatmap", "health", "costs", "timeline"):
                open_tab(t)
                settle(300)
                seen[t] = js("""[state.preset, document.querySelector('#presets [aria-pressed=true]')?.textContent,
                    document.getElementById('from').value, getComputedStyle(document.querySelector('.filters')).display !== 'none']""")
            check("period: 1y picked on Timeline is the period on Labs (charts and heatmap), Body and activity and Costs",
                  all(v[:2] == ["1y", "1y"] and v[3] for v in seen.values()) and len({v[2] for v in seen.values()}) == 1, seen)
            check("…Body and activity charts those dates", js("hRange()") == want, [js("hRange()"), want])
            open_tab("costs")
            settle(300)
            check("…Costs counts the claims in them, and says so",
                  js("costRows().length > 0 && costRows().every(r => r.d >= isoDay(state.from) && r.d <= isoDay(state.to))")
                  and "the last year" in page.inner_text("#paidCard h2"), page.inner_text("#paidCard h2"))
            open_tab("overview")
            check("…the Overview has no records bar", js("getComputedStyle(document.querySelector('.filters')).display") == "none")
            year = js("periodYears().find(y => (DATA.costs || []).some(r => r.d.startsWith(y)))")
            open_tab("costs")
            page.click("#periodMoreBtn")   # the other periods are in More… (#191)
            opaque = r"c => { const m = c.match(/rgba?\(([^)]+)\)/); return !!m && (m[1].split(',').length < 4 || +m[1].split(',')[3] === 1); }"
            check("…the More… menu is readable: a solid background (not see-through over the page)",
                  js(f"(({opaque}))(getComputedStyle(document.getElementById('periodMoreList')).backgroundColor)"),
                  js("getComputedStyle(document.getElementById('periodMoreList')).backgroundColor"))
            page.click(f"#periodMoreList [data-period='{year}']")
            settle(300)
            check("period: a calendar year picked from More…", js("[state.preset, isoDay(state.from), isoDay(state.to)]") == [year, f"{year}-01-01", f"{year}-12-31"]
                  and js("y => { const b = document.getElementById('periodMoreBtn'); return b.classList.contains('on') && b.textContent.startsWith(y) && !document.querySelector('#presets [aria-pressed=true]'); }", year),
                  js("[state.preset, isoDay(state.from), isoDay(state.to), document.getElementById('periodMoreBtn').textContent]"))
            check("…Costs lists that year's claims", js("costRows().length") == js("y => DATA.costs.filter(r => r.d.startsWith(y)).length", year) > 0,
                  js("costRows().length"))
            js("applyPreset('all')")
            page.click("#costBy button:text-is('By year')")
            settle(300)
            labels = js("[...document.querySelectorAll('#view-costs svg[aria-label=\"Yearly insurance costs\"] text')].map(t => t.textContent).filter(t => !t.startsWith('$'))")
            check("…and groups them by year when asked", labels and all(re.fullmatch(r"\d{4}", x) for x in labels)
                  and "Insurance claims by year" in page.inner_text("#view-costs"), labels)
            page.click("#costBy button:text-is('By month')")
            js("costState.by = ''")
            other = js("(m => m ? msKey(m) : null)(MILESTONES.find(m => m !== primaryMs()))")   # the primary one is a button
            check("the demo has a milestone besides the primary one, for More…", other is not None)
            if other:
                page.click("#periodMoreBtn")
                page.click(f"#periodMoreList [data-period={other}]")
                settle(300)
                check("period: since a milestone, picked from More…", js("[state.preset, isoDay(state.from)]") == [other, js("k => msOf(k).date", other)],
                      js("[state.preset, isoDay(state.from)]"))
            open_tab("health")
            start = js("isoDay(state.max - 120 * DAY)")
            page.click("#periodMoreBtn")
            page.click("#periodMoreList [data-period=custom]")
            page.fill("#from", start)
            settle(300)
            check("period: dates typed in on Body and activity", js("[state.preset, isoDay(state.from), isoDay(hRange()[0])]") == ["custom", start, start],
                  js("[state.preset, isoDay(state.from), isoDay(hRange()[0])]"))
            open_tab("timeline")
            settle(300)
            check("…are the Timeline's too", js("[document.getElementById('from').value, isoDay(state.from)]") == [start, start]
                  and js("!document.getElementById('dates').hidden"), js("document.getElementById('from').value"))
            # the kinds' chips say which are shown: aria-pressed "true" or "false" (never left out), and an off one has no
            # tint (#193: on/off toggle chips, not faded)
            chips = r"[...document.querySelectorAll('#chips .toggle')].map(b => [b.textContent.trim().split(/ \d/)[0], b.getAttribute('aria-pressed'), getComputedStyle(b).backgroundColor])"
            clear = "rgba(0, 0, 0, 0)"
            check("Timeline: every kind's chip says whether it's shown, and a hidden kind has no tint (Insurance claims start hidden)",
                  all(p in ("true", "false") for _, p, _ in js(chips)) and all((p == "false") == (bg == clear) for _, p, bg in js(chips))
                  and any(n.startswith("Insurance claims") and p == "false" for n, p, _ in js(chips)), js(chips))
            page.click("#chips .kind:nth-child(2) .toggle")   # each chip with its ⋯ menu (#189)
            settle(200)
            check("…and clicking one hides it, showing so", js(chips)[1][1] == "false" and js(chips)[1][2] == clear, js(chips)[1])
            page.click("#chips .kind:nth-child(2) .toggle")   # each chip with its ⋯ menu (#189)
            settle(200)
            page.reload()
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && booted", timeout=20000)
            check("…and kept on a reload, the date boxes showing them", js("[state.preset, document.getElementById('from').value, !document.getElementById('dates').hidden]") == ["custom", start, True],
                  js("[state.preset, document.getElementById('from').value, !document.getElementById('dates').hidden]"))
            page.set_viewport_size({"width": 375, "height": 800})
            for t in ("health", "timeline", "labs", "heatmap", "costs"):
                open_tab(t)
                settle(300)
                fits = js("document.documentElement.scrollWidth")
                check(f"the records bar on {t} fits 375 px, with its period showing", fits <= 375 and
                      js("['presets', 'periodMoreBtn'].every(id => { const r = document.getElementById(id).getBoundingClientRect(); return r.width > 0 && r.right <= 375; })"), fits)
            page.set_viewport_size({"width": 1400, "height": 900})

            # ---- the period's controls (#191): each kind of period (a preset, All, the primary milestone's button, another
            # milestone and a calendar year from More…, custom dates) shows exactly one chosen control and its words; the
            # date boxes only for custom dates, Clear dates going back; More… is a menu worked by keyboard (arrow keys,
            # Escape back to its button), closed by a click outside, nothing in it cut off and its text readable, in light
            # and dark at 1400 and 375 px; old saved period keys still open
            period = r"""(() => {
              const more = document.getElementById('periodMoreBtn'), words = document.getElementById('periodWords');
              return {chosen: [...[...document.querySelectorAll('#presets [aria-pressed=true]')].map(b => b.textContent),
                               ...(more.classList.contains('on') ? ['More: ' + more.textContent.replace('▾', '')] : [])],
                      words: words.hidden ? null : words.textContent, dates: !document.getElementById('dates').hidden,
                      ticked: [...document.querySelectorAll('#periodMoreList [aria-checked=true]')].map(i => i.dataset.period)};
            })()"""
            was = js("[state.from, state.to]")   # the custom dates above, put back after
            open_tab("timeline")
            settle(200)
            pm = js("[msKey(primaryMs()), 'Since ' + lcLabel(primaryMs().label), fmtD(dayTs(primaryMs().date))]")
            om = js("(m => [msKey(m), 'Since ' + lcLabel(m.label), fmtD(dayTs(m.date))])(MILESTONES.find(m => m !== primaryMs()))")
            yr = js("periodYears()[1]")   # a whole year before this one
            kinds = [("2y", "#presets button:text-is('2y')", ["2y"], "Last 2 years", []),
                     ("all", "#presets button:text-is('All')", ["All"], "Everything on file", []),
                     (pm[0], f"#presets button:text-is('{pm[1]}')", [pm[1]], pm[2] + " – today", []),
                     (om[0], f"#periodMoreList [data-period={om[0]}]", ["More: " + om[1]], om[2] + " – today", [om[0]]),
                     (yr, f"#periodMoreList [data-period='{yr}']", ["More: " + yr], yr, [yr])]
            for key, sel, chosen, words, ticked in kinds:
                if sel.startswith("#periodMoreList"):
                    page.click("#periodMoreBtn")
                page.click(sel)
                settle(250)
                got = js(period)
                check(f"period {key}: one control chosen, its words beside it, no date boxes", js("state.preset") == key
                      and got == {"chosen": chosen, "words": words, "dates": False, "ticked": ticked}, got)
            page.click("#periodMoreBtn")
            page.click("#periodMoreList [data-period=custom]")
            settle(250)
            got = js(period)
            check("period custom: Custom dates… shows the date boxes (the period's dates, to change), More… the one chosen",
                  got == {"chosen": ["More: Custom dates"], "words": None, "dates": True, "ticked": ["custom"]} and js("document.activeElement.id") == "from"
                  and js("[document.getElementById('from').value, document.getElementById('to').value]") == [f"{yr}-01-01", f"{yr}-12-31"],
                  [got, js("document.activeElement.id")])
            page.click("#datesClear")
            settle(250)
            check("…and Clear dates goes back to the period before, the boxes gone", js(period)["chosen"] == ["More: " + yr] and not js(period)["dates"]
                  and js("document.activeElement.id") == "periodMoreBtn", [js(period), js("document.activeElement.id")])
            js("applyPreset('2y')")
            settle(200)
            menu_state = "[document.getElementById('periodMoreBtn').getAttribute('aria-expanded'), document.getElementById('periodMoreList').hidden, document.activeElement.id || document.activeElement.textContent]"
            check("More… is a menu button: aria-haspopup, aria-expanded and a menu of radio items",
                  js("(b => [b.getAttribute('aria-haspopup'), b.getAttribute('aria-expanded'), document.getElementById(b.getAttribute('aria-controls')).getAttribute('role')])(document.getElementById('periodMoreBtn'))")
                  == ["menu", "false", "menu"] and js("[...document.querySelectorAll('#periodMoreList button')].every(i => i.getAttribute('role') === 'menuitemradio' && i.tabIndex === -1)"))
            page.focus("#periodMoreBtn")
            page.keyboard.press("Enter")
            first = js(menu_state)
            page.keyboard.press("ArrowDown")
            second = js("document.activeElement.dataset.period")
            page.keyboard.press("End")
            last = js("document.activeElement.dataset.period")
            page.keyboard.press("Escape")
            check("…Enter opens it on its first item, arrow keys and End move, Escape closes it and focus goes back to the button",
                  first[:2] == ["true", False] and first[2].startswith(om[1]) and second and last == "custom"
                  and js(menu_state) == ["false", True, "periodMoreBtn"], [first, second, last, js(menu_state)])
            page.keyboard.press("ArrowDown")
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Enter")
            settle(250)
            check("…Enter on an item picks it, focus back on More…", js("state.preset") == second and js(menu_state) == ["false", True, "periodMoreBtn"],
                  [js("state.preset"), js(menu_state)])
            page.click("#periodMoreBtn")
            page.mouse.click(1300, page.locator("#periodMoreBtn").bounding_box()["y"] + 10)   # the records bar's empty end
            check("…and a click outside closes it", js(menu_state)[:2] == ["false", True], js(menu_state)[:2])
            menu_look = r"""(() => {
              const rgb = c => { const x = document.createElement('canvas').getContext('2d'); x.fillStyle = c; x.fillRect(0, 0, 1, 1); return [...x.getImageData(0, 0, 1, 1).data]; };
              const bg = n => { for (; n; n = n.parentElement) { const c = rgb(getComputedStyle(n).backgroundColor); if (c[3] > 0) return c; } return [255, 255, 255, 255]; };
              const lum = c => { const v = c.slice(0, 3).map(x => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; }); return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
              const ratio = n => { const [a, b] = [lum(rgb(getComputedStyle(n).color)), lum(bg(n))].sort((p, q) => q - p); return Math.round((a + 0.05) / (b + 0.05) * 10) / 10; };
              const list = document.getElementById('periodMoreList'), r = list.getBoundingClientRect(), items = [...list.querySelectorAll('[role=menuitemradio]')];
              return {inside: r.left >= 0 && r.right <= document.documentElement.clientWidth, solid: rgb(getComputedStyle(list).backgroundColor)[3] === 255,
                      cut: items.filter(i => i.scrollWidth > i.clientWidth + 1 || i.getBoundingClientRect().right > r.right).map(i => i.textContent),
                      text: Math.min(...items.map(ratio)), quiet: Math.min(...[...list.querySelectorAll('.menu-head, .menu-note')].map(ratio)),
                      chosen: ratio(document.getElementById('periodMoreBtn')), words: ratio(document.getElementById('periodWords'))};
            })()"""
            theme_before = js("document.documentElement.dataset.theme || null")
            for width in (1400, 375):
                page.set_viewport_size({"width": width, "height": 800 if width == 375 else 900})
                for theme in ("light", "dark"):
                    js("t => { document.documentElement.dataset.theme = t; }", theme)
                    js(f"applyPreset('{yr}')")   # More… chosen, its item ticked
                    settle(200)
                    bad = phone_fits(page, f"{theme}, {yr}") if width == 375 else []
                    page.click("#periodMoreBtn")
                    settle(100)
                    look = js(menu_look)
                    if width == 375:
                        bad += phone_fits(page, f"{theme}, More… open")
                    page.keyboard.press("Escape")
                    check(f"period at {width} px, {theme}: the More… menu fits the screen, nothing in it cut off, its text and the chosen period readable",
                          look["inside"] and look["solid"] and not look["cut"] and look["text"] >= 4.5 and look["quiet"] >= 4.5
                          and look["chosen"] >= 4.5 and look["words"] >= 4.5, look)
                    if width == 375:
                        page.click("#periodMoreBtn")
                        page.click("#periodMoreList [data-period=custom]")
                        settle(200)
                        bad += phone_fits(page, f"{theme}, custom dates")
                        check(f"period at 375 px, {theme}: the presets, More…, the words and the date boxes fit, no control cut off or overlapping",
                              not bad and js("['presets', 'periodMoreBtn', 'from', 'to', 'datesClear'].every(id => { const r = document.getElementById(id).getBoundingClientRect(); return r.width > 0 && r.right <= 375; })"),
                              bad[:6])
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            page.set_viewport_size({"width": 1400, "height": 900})
            # periods saved before #191: "dx" (the primary milestone), a milestone, a year
            for key, chosen in (("dx", [pm[1]]), (om[0], ["More: " + om[1]]), (yr, ["More: " + yr])):
                js("k => localStorage.setItem(stateKey(), JSON.stringify({...JSON.parse(localStorage.getItem(stateKey())), preset: k}))", key)
                page.reload()
                page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && booted", timeout=20000)
                check(f"a period saved as {key!r} opens as it was", js(period)["chosen"] == chosen and not js(period)["dates"], js(period))
            js("([a, b]) => setRange(a, b)", was)
            open_tab("labs")

            # ---- pick one and on or off (#193): every pick-one group (.seg) on each records tab, and the journal form's
            # kinds, has exactly one selected; every toggle chip (.toggle) says "true" or "false"; each kind has one look in
            # light, dark and forced colors, told apart by more than opacity; focus shows; the chips fit 375 px
            controls = r"""(() => {
              const vis = n => n.getClientRects().length > 0;
              const on = b => b.getAttribute('aria-pressed') === 'true' || b.getAttribute('aria-checked') === 'true' || !!b.querySelector(':scope > input:checked');
              const said = b => ['true', 'false'].includes(b.getAttribute('aria-pressed') || b.getAttribute('aria-checked')) || !!b.querySelector(':scope > input[type=radio]');
              const groups = [...document.querySelectorAll('.seg')].filter(vis).map(g => {
                let n = [...g.children].filter(on).length;   // the period: a preset, or More… naming what it picked (#191)
                if (g.id === 'presets') n += document.getElementById('periodMoreBtn').classList.contains('on');
                return [g.id || g.getAttribute('aria-label'), n, [...g.children].every(said)];
              });
              const toggles = [...document.querySelectorAll('.toggle')].filter(vis).map(t => {
                const box = t.querySelector(':scope > input[type=checkbox]');
                return [t.textContent.trim(), box ? String(box.checked) : t.getAttribute('aria-pressed')];
              });
              return {groups, toggles};
            })()"""
            want_groups = {"timeline": {"presets", "sortSeg"}, "labs": {"presets", "labsView"}, "heatmap": {"presets", "labsView"}, "costs": {"presets", "costBy"}}
            want_toggles = {"timeline": "Visits", "labs": "Procedures & imaging", "heatmap": None, "costs": None}
            for t in ("timeline", "labs", "heatmap", "costs"):
                open_tab(t)
                settle(300)
                c = js(controls)
                names = {g[0] for g in c["groups"]}
                check(f"pick one ({t}): every group has exactly one selected, each choice saying so (#193)",
                      want_groups[t] <= names and all(n == 1 and said for _, n, said in c["groups"]), c["groups"])
                check(f"on or off ({t}): every toggle says true or false (#193)", all(s in ("true", "false") for _, s in c["toggles"])
                      and (want_toggles[t] is None or any(n.startswith(want_toggles[t]) for n, _ in c["toggles"])), c["toggles"])
            open_tab("labs")
            check("…the chart overlays are toggle chips: Procedures & imaging, Medications, Journal",
                  js("['optMarks', 'optMeds', 'optJournal'].every(id => document.getElementById(id).parentElement.classList.contains('toggle'))"))
            js("journalNew()")
            settle(200)
            c = js(controls)
            check("pick one: the journal form's kinds have exactly one checked", [g[1:] for g in c["groups"] if g[0] == "Kind of entry"] == [[1, True]], c["groups"])
            js("closeDrawer()")
            open_tab("timeline")
            look = r"""(() => {
              const cs = (n, p) => getComputedStyle(n, p || null), lab = [...document.querySelectorAll('#chips .toggle')].find(b => b.textContent.startsWith('Labs'));
              const vis = [...document.querySelectorAll('#chips .toggle')].find(b => b.textContent.startsWith('Visits'));
              const s = [...document.querySelectorAll('#sortSeg button')], sel = s.find(b => b.getAttribute('aria-pressed') === 'true'), un = s.find(b => b !== sel);
              const t = b => ({bg: cs(b).backgroundColor, color: cs(b).color, border: cs(b).borderColor + ' ' + cs(b).borderStyle, mark: cs(b, '::before').content,
                               key: cs(b.querySelector('.key')).backgroundColor, opacity: +cs(b).opacity});
              return {shown: t(vis), hidden: t(lab), sel: [cs(sel).backgroundColor, cs(sel).boxShadow, cs(sel).fontWeight, +cs(sel).opacity],
                      un: [cs(un).backgroundColor, cs(un).boxShadow, cs(un).fontWeight, +cs(un).opacity]};
            })()"""
            theme_before = js("document.documentElement.dataset.theme || null")
            js("state.cats.delete('lab'); state.cats.add('visit'); renderAll()")
            for mode in ("light", "dark", "forced colors"):
                if mode == "forced colors":
                    page.emulate_media(forced_colors="active")
                else:
                    js("t => { document.documentElement.dataset.theme = t; }", mode)
                settle(200)
                lk = js(look)
                sh, hd = lk["shown"], lk["hidden"]
                check(f"on or off ({mode}): shown has a fill, a ✓ and its colour key; hidden is outlined only, no ✓, a grey key; not by opacity",
                      sh["bg"] != hd["bg"] and (mode == "forced colors" or hd["bg"] == "rgba(0, 0, 0, 0)")
                      and "✓" in sh["mark"] and hd["mark"] in ("none", "normal") and sh["key"] != hd["key"] and sh["border"] != hd["border"]
                      and sh["opacity"] == hd["opacity"] == 1, lk)
                check(f"pick one ({mode}): the selected choice has its own fill, and a bar or the system highlight; not by opacity",
                      lk["sel"][0] != lk["un"][0] and lk["sel"][3] == lk["un"][3] == 1
                      and (mode == "forced colors" or ("inset" in lk["sel"][1] and lk["un"][1] == "none" and int(lk["sel"][2]) > int(lk["un"][2]))), lk)
            page.emulate_media(forced_colors="none")
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            js("state.cats.add('lab'); renderAll()")
            settle(200)
            page.focus("#chips .kind-more[data-k=watch]")
            page.keyboard.press("Shift+Tab")   # back from its ⋯ menu button (#189): the Watch chip
            ring = "(e => [e.className, e.matches(':focus-visible'), getComputedStyle(e).outlineStyle, getComputedStyle(e).outlineWidth])(document.activeElement)"
            check("focus shows on a toggle chip reached by keyboard", js(ring)[0] == "toggle" and js(ring)[1:3] == [True, "solid"], js(ring))
            page.focus("#sortSeg [data-sort=desc]")
            page.keyboard.press("Tab")
            check("…and on a pick-one choice", js("document.activeElement.dataset.sort") == "asc" and js(ring)[1:3] == [True, "solid"], js(ring))
            page.set_viewport_size({"width": 375, "height": 800})
            open_tab("timeline")
            page.click("#ftoggle")
            settle(200)
            # the page's width, then each shown control's right edge
            fit = "sel => [document.documentElement.scrollWidth, ...[...document.querySelectorAll(sel)].filter(n => n.getClientRects().length).map(n => Math.round(n.getBoundingClientRect().right))]"
            check("on or off at 375 px: the kinds' chips wrap inside the screen", max(js(fit, "#chips .toggle")) <= 375 and len(js(fit, "#chips .toggle")) > 3, js(fit, "#chips .toggle"))
            page.click("#ftoggle")
            open_tab("labs")
            settle(300)
            check("…and the chart overlays", max(js(fit, "#view-labs .toggle")) <= 375 and len(js(fit, "#view-labs .toggle")) == 1 + 3, js(fit, "#view-labs .toggle"))
            page.set_viewport_size({"width": 1400, "height": 900})

            # ---- Timeline kinds: each chip's name says its state; Show all and Only this by mouse, keyboard and on a
            # phone; a hidden kind's count muted and its ⋯ untinted in light, dark and forced colors; kept on a reload (#189)
            open_tab("timeline")
            settle(300)
            kinds = r"""[...document.querySelectorAll('#chips .kind')].map(k => { const b = k.querySelector('.toggle');
              return [b.dataset.k, b.getAttribute('aria-pressed'), b.getAttribute('aria-label'), b.querySelector('.n').textContent]; })"""
            lanes = js("CATS.filter(hasLane).map(c => c.k)")
            ks, names = js(kinds), js("Object.fromEntries(CATS.map(c => [c.k, c.label]))")
            check("kinds (#189): each chip's name says its kind, count and whether it's shown, as its pressed state does",
                  [k for k, *_ in ks] == lanes and all(lab == f"{names[k]}, {n}, {'shown' if p == 'true' else 'hidden'}" for k, p, lab, n in ks)
                  and ["claim", "false", f"{names['claim']}, {ks[lanes.index('claim')][3]}, hidden"] == ks[lanes.index("claim")][:3], ks)
            vis_n = names["visit"]
            check("…found by that name (\"Visits, 31, shown\")", page.get_by_role("button", name=re.compile(rf"^{vis_n}, \d+, shown$")).count() == 1)
            check("…and as kinds start (Insurance claims hidden), no \"Show all\" button beside the chips and no filter line: Reset filters took that place (#190)",
                  not js("!!document.querySelector('#chips .kinds-all')") and page.is_hidden("#fsum"))
            # Only this, by mouse: the ⋯ beside Visits, then "Show only Visits"
            page.click("#chips .kind-more[data-k=visit]")
            menu = "[...document.querySelectorAll('#chips .menu-list')].filter(m => !m.hidden).map(m => [m.id, ...[...m.querySelectorAll('[role=menuitem]')].map(i => i.textContent)])"
            check("kinds: a chip's ⋯ opens its menu, Show only this and Show all", js(menu) == [["kindMenu-visit", f"Show only {vis_n}", "Show all"]]
                  and js("document.querySelector('#chips .kind-more[data-k=visit]').getAttribute('aria-expanded')") == "true", js(menu))
            js("document.getElementById('sgStatus').textContent = ''")
            page.click("#kindMenu-visit [role=menuitem]:first-child")
            settle(200)
            only = lambda: [k for k, p, *_ in js(kinds) if p == "true"]
            check("kinds: Show only Visits shows Visits alone, on the chips and the timeline, and says so",
                  only() == ["visit"] and js(menu) == [] and said(page) == f"Showing only {vis_n}"
                  and js("[...document.querySelectorAll('#list .row')].length > 0 && EV.filter(visible).every(e => CAT_OF[e.k].k === 'visit')"), only())
            page.reload()
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && booted", timeout=20000)
            settle(300)
            check("…kept on a reload", only() == ["visit"] and js("JSON.parse(localStorage.getItem(stateKey())).cats.filter(k => CATS.filter(hasLane).some(c => c.k === k))") == ["visit"], only())
            # a hidden kind's look: the count muted, the key grey and its ⋯ untinted, in each theme (#193 checks the chip itself)
            part = r"""k => { const w = document.querySelector(`#chips .toggle[data-k=${k}]`).parentElement, cs = n => getComputedStyle(n);
              return {n: cs(w.querySelector('.n')).color, key: cs(w.querySelector('.key')).backgroundColor, more: cs(w.querySelector('.kind-more')).backgroundColor}; }"""
            theme_before = js("document.documentElement.dataset.theme || null")
            for mode in ("light", "dark", "forced colors"):
                if mode == "forced colors":
                    page.emulate_media(forced_colors="active")
                else:
                    js("t => { document.documentElement.dataset.theme = t; }", mode)
                settle(150)
                sh, hd = js(part, "visit"), js(part, "lab")
                check(f"kinds ({mode}): a hidden kind's count is muted, its key grey and its ⋯ untinted, unlike a shown one's",
                      sh["n"] != hd["n"] and sh["key"] != hd["key"] and sh["more"] != hd["more"] if mode != "forced colors"
                      else sh["key"] != hd["key"] and sh["more"] != hd["more"], [sh, hd])
            page.emulate_media(forced_colors="none")
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            # Show all, by keyboard from a chip's menu: Enter on the ⋯ opens it on its first item, arrow down, Enter
            page.focus("#chips .kind-more[data-k=lab]")
            page.keyboard.press("Enter")
            check("kinds by keyboard: Enter on a ⋯ opens its menu, focus on its first item",
                  js("[document.activeElement.textContent, document.activeElement.closest('[role=menu]')?.id]") == [f"Show only {names['lab']}", "kindMenu-lab"],
                  js("document.activeElement.outerHTML"))
            page.keyboard.press("Escape")
            check("…Escape closes it, focus back on the ⋯", js("document.activeElement.matches('.kind-more[data-k=lab]')") and js(menu) == [])
            page.keyboard.press("ArrowDown")
            page.keyboard.press("ArrowDown")
            js("document.getElementById('sgStatus').textContent = ''")
            page.keyboard.press("Enter")
            settle(200)
            check("…Show all from it shows every kind, says so, focus kept on that ⋯, and \"Show all\" goes",
                  only() == lanes and said(page) == "Showing every kind" and js("document.activeElement.matches('.kind-more[data-k=lab]')")
                  and page.is_hidden("#fsum"), [only(), js("document.activeElement.outerHTML")])
            # a chip toggled by keyboard keeps focus; right-click (or the menu key, or a long press) on a chip opens its menu
            page.focus("#chips .toggle[data-k=imaging]")
            page.keyboard.press("Space")
            settle(150)
            check("kinds by keyboard: Space hides a kind, focus kept on its chip, its name saying hidden",
                  js("document.activeElement.matches('.toggle[data-k=imaging]') && document.activeElement.getAttribute('aria-label').endsWith(', hidden')"))
            page.click("#chips .toggle[data-k=study]", button="right")
            check("kinds: right-click on a chip opens its menu", js(menu)[:1] == [["kindMenu-study", f"Show only {names['study']}", "Show all"]], js(menu))
            page.keyboard.press("Escape")
            page.click("#fsum .fsum-reset")   # the filter line's Reset filters (#190), where a "Show all" button was
            settle(200)
            check("kinds: the filter line's Reset filters shows every kind (Insurance claims shown, as they were), focus on the first chip", only() == lanes
                  and js("document.activeElement === document.querySelector('#chips .toggle')"), only())
            # 375 px, light and dark: the chips and a menu open on the last one fit
            page.set_viewport_size({"width": 375, "height": 800})
            page.click("#ftoggle")
            js("state.cats.delete('claim'); renderAll()")
            for theme in ("light", "dark"):
                js("t => { document.documentElement.dataset.theme = t; }", theme)
                last = lanes[-1]
                page.click(f"#chips .kind-more[data-k={last}]")
                settle(150)
                box = js(f"(r => [r.left, r.right])(document.getElementById('kindMenu-{last}').getBoundingClientRect())")
                check(f"kinds at 375 px ({theme}): chips and an open menu fit", phone_fits(page, "Timeline kinds") == [] and box[0] >= 0 and box[1] <= 375,
                      [phone_fits(page, "Timeline kinds"), box])
                page.click(f"#chips .kind-more[data-k={last}]")
            page.click("#chips .kind-more[data-k=visit]")
            page.click("#kindMenu-visit [role=menuitem]:first-child")
            settle(200)
            check("kinds at 375 px: Show only this from the ⋯", only() == ["visit"] and phone_fits(page, "Timeline kinds") == [], only())
            page.click("#fsum .fsum-reset")
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            js("state.cats.delete('claim'); renderAll()")   # as the checks after this expect: Insurance claims hidden
            page.click("#ftoggle")
            page.set_viewport_size({"width": 1400, "height": 900})

            # ---- what the filters leave out (#190): on each tab that filters, one line saying how, ending in Reset filters,
            # which puts that tab's filters back but not the period. The Timeline's is under the records bar, still shown on
            # a phone with the panel closed, the Filters button counting the filters on and its counts captioned; the Labs
            # name filter's is under its box (charts and heatmap); each Costs list's under its filters, their totals then
            # for what's shown. Filtered and not, at 1400 and 375 px, light and dark (phone_fits, and the line's contrast).
            # The journal's card is checked with the journal, above.
            fsum = lambda sel="#fsum": js("s => (p => p && !p.hidden && p.getClientRects().length ? p.textContent : null)(document.querySelector(s))", sel)
            start_kinds = "CATS.filter(hasLane).every(c => state.cats.has(c.k) === (c.k !== 'claim'))"
            ftog = lambda: js("document.getElementById('ftoggle').textContent")
            theme_before = js("document.documentElement.dataset.theme || null")
            set_theme = lambda t: js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", t)
            def line_look(sel):   # the line's words and its Reset against the background behind them: [contrast, contrast]
                c = js("""s => { const p = document.querySelector(s), bg = n => { for (; n; n = n.parentElement) { const b = getComputedStyle(n).backgroundColor;
                  if (b !== 'rgba(0, 0, 0, 0)' && b !== 'transparent') return b; } return 'rgb(255, 255, 255)'; };
                  return [getComputedStyle(p.querySelector('.fsum-t')).color, getComputedStyle(p.querySelector('.fsum-reset')).color, bg(p)]; }""", sel)
                return [contrast(c[0], c[2]), contrast(c[1], c[2])]
            open_tab("timeline")
            tl_preset = js("state.preset")
            js("applyPreset('1y')")
            settle(300)
            check("filters (#190): the Timeline as it starts (only Insurance claims hidden): no filter line, no count on Filters, its counts not captioned",
                  fsum() is None and js(start_kinds) and js("state.q") == "" and ftog() == "Filters" and page.is_hidden("#statsNote"), [fsum(), ftog()])
            page.click("#chips .toggle[data-k=lab]")
            js("document.getElementById('sgStatus').textContent = ''")
            page.click("#q")
            page.locator("#q").press_sequentially("Visit", delay=60)
            settle(300)
            want = js("(k => `Showing ${k.filter(c => state.cats.has(c.k)).length} of ${k.length} kinds · matching “Visit” · Reset filters`)(CATS.filter(hasLane))")
            check("…a kind hidden and a search typed: one line under the records bar says so, “Showing 9 of 11 kinds · matching “Visit” · Reset filters”",
                  fsum() == want and js("document.getElementById('fsum').parentElement.classList.contains('filters')"), [fsum(), want])
            n_vis = js("EV.filter(visible).length")
            check("…its counts say they're for what's shown, as the list's are", page.is_visible("#statsNote") and "for what's shown" in page.inner_text("#statsNote")
                  and page.inner_text("#listTitle") == f"Events ({n_vis:,})", page.inner_text("#listTitle"))
            n_said = f"{n_vis:,} event{'s' if n_vis != 1 else ''} match." if n_vis else "No events match."
            check("…how many events match is said once typing stops, not for each key", said(page, tries=40) == n_said,
                  [js("document.getElementById('sgStatus').textContent"), n_said])
            for theme in ("light", "dark"):
                set_theme(theme)
                settle(100)
                look = line_look("#fsum")
                check(f"…at 1400 px ({theme}): the line and its Reset filters are readable", min(look) >= 4.5 and fsum() == want, look)
            page.set_viewport_size({"width": 375, "height": 800})
            for theme in ("light", "dark"):
                set_theme(theme)
                settle(200)
                closed = [fsum() == want, page.is_hidden("#chips"), page.is_hidden("#q"), ftog()]
                bad = phone_fits(page, f"Timeline filtered, {theme}")
                page.click("#ftoggle")
                settle(150)
                bad += phone_fits(page, f"Timeline filtered, panel open, {theme}")
                page.click("#ftoggle")
                look = line_look("#fsum")
                check(f"filters at 375 px ({theme}): with the panel closed the line stays, “Filters (2)” says how many are on; it fits, open or closed, and reads",
                      closed == [True, True, True, "Filters (2)"] and not bad and min(look) >= 4.5, [closed, bad, look])
            js("document.getElementById('sgStatus').textContent = ''")
            page.click("#fsum .fsum-reset")
            settle(200)
            check("…Reset filters there puts the kinds and the search back, not the period; said; focus on the Filters button",
                  fsum() is None and js(start_kinds) and js("state.q") == "" and page.input_value("#q") == "" and js("state.preset") == "1y"
                  and ftog() == "Filters" and page.is_hidden("#statsNote") and said(page) == "Filters reset." and js("document.activeElement.id") == "ftoggle",
                  [fsum(), js("state.preset"), js("document.activeElement.id")])
            for theme in ("light", "dark"):
                set_theme(theme)
                settle(150)
                bad = phone_fits(page, f"Timeline unfiltered, {theme}")
                page.click("#ftoggle")
                bad += phone_fits(page, f"Timeline unfiltered, panel open, {theme}")
                page.click("#ftoggle")
                check(f"…and unfiltered at 375 px ({theme}), it all fits, open or closed", not bad and fsum() is None, bad)
            page.set_viewport_size({"width": 1400, "height": 900})
            set_theme(theme_before)
            page.click("#chips .kind-more[data-k=visit]")
            page.click("#kindMenu-visit [role=menuitem]:first-child")
            settle(200)
            check("…Show only Visits: “Showing only Visits · Reset filters”", fsum() == f"Showing only {names['visit']} · Reset filters", fsum())
            page.focus("#fsum .fsum-reset")
            page.keyboard.press("Enter")
            settle(200)
            check("…Reset filters by keyboard: as kinds start, focus on the first chip", fsum() is None and js(start_kinds)
                  and js("document.activeElement === document.querySelector('#chips .toggle')"), js("document.activeElement.outerHTML"))
            # Labs: the name filter's line, under its box in the list's header and the heatmap's
            open_tab("labs")
            js("clearLabFilter()")
            settle(300)
            check("filters on Labs (#190): no name filter, no line", fsum("#labSum") is None and ftog() == "Dates")
            page.fill("#labQ", "hemo")
            settle(400)
            lab_want = "Showing lab names matching “hemo” · Reset filters"
            check("…filtered: under the box, “Showing lab names matching “hemo” · Reset filters”", fsum("#labSum") == lab_want
                  and js("document.getElementById('labSum').previousElementSibling.contains(document.getElementById('labQ'))"), fsum("#labSum"))
            page.set_viewport_size({"width": 375, "height": 800})
            for theme in ("light", "dark"):
                set_theme(theme)
                settle(200)
                bad, look = phone_fits(page, f"Labs filtered, {theme}"), line_look("#labSum")
                check(f"…at 375 px ({theme}) it fits and reads", not bad and min(look) >= 4.5 and fsum("#labSum") == lab_want, [bad, look])
            page.set_viewport_size({"width": 1400, "height": 900})
            set_theme(theme_before)
            open_tab("heatmap")
            settle(400)
            check("…the heatmap's header has the same line", fsum("#hmSum") == lab_want, fsum("#hmSum"))
            js("document.getElementById('sgStatus').textContent = ''")
            page.click("#hmSum .fsum-reset")
            settle(300)
            check("…its Reset filters empties both boxes, said, focus in the heatmap's box", fsum("#hmSum") is None and js("state.labQ") == ""
                  and page.input_value("#hmQ") == page.input_value("#labQ") == "" and said(page) == "Filters reset." and js("document.activeElement.id") == "hmQ")
            open_tab("labs")
            settle(300)
            check("…and on the chart, no line", fsum("#labSum") is None)
            # Costs: each list's filters, the claims' and the out-of-pocket amounts'
            open_tab("costs")
            js("applyPreset('all')")
            settle(400)
            claims_hint = "document.querySelector('#costTable .toolbar ~ p.hint').textContent"
            check("filters on Costs (#190): unfiltered, no line by either list, the claims' totals for them all",
                  fsum("#costTable .fsum") is None and fsum("#paidTable .fsum") is None and re.match(r"^[\d,]+ claims · billed", js(claims_hint)), js(claims_hint))
            page.select_option("#costTable select.search", "Pharmacy")
            settle(400)
            c_want = js("(r => `Showing ${r.filter(x => x.cat === 'Pharmacy').length.toLocaleString()} of ${r.length.toLocaleString()} claims · type of care: Pharmacy · Reset filters`)(costRows())")
            check("…a type of care picked: “Showing 13 of 96 claims · type of care: Pharmacy · Reset filters”, the totals for the claims shown",
                  fsum("#costTable .fsum") == c_want and js(claims_hint).startswith("For the claims shown: billed $"), [fsum("#costTable .fsum"), c_want, js(claims_hint)])
            page.click("#paidTable .toggle")   # HSA only
            settle(400)
            p_want = js("(r => `Showing ${r.filter(x => x.hsa === 'HSA').length.toLocaleString()} of ${r.length.toLocaleString()} amounts · paid with an HSA · Reset filters`)(paidRows())")
            check("…HSA only: “Showing 12 of 40 amounts · paid with an HSA · Reset filters”, the total for the amounts shown",
                  fsum("#paidTable .fsum") == p_want and page.inner_text("#paidTable .toolbar .hint").startswith("For the amounts shown: $"),
                  [fsum("#paidTable .fsum"), p_want, page.inner_text("#paidTable .toolbar .hint")])
            page.set_viewport_size({"width": 375, "height": 800})
            for theme in ("light", "dark"):
                set_theme(theme)
                settle(200)
                bad, look = phone_fits(page, f"Costs filtered, {theme}"), line_look("#paidTable .fsum") + line_look("#costTable .fsum")
                check(f"…at 375 px ({theme}) both fit and read", not bad and min(look) >= 4.5, [bad, look])
            page.set_viewport_size({"width": 1400, "height": 900})
            set_theme(theme_before)
            settle(300)   # the resize redraws the tab first (timeline.js), which would take the focus Reset gives
            page.click("#costTable .fsum-reset")
            settle(300)
            check("…the claims' Reset filters clears their filters only, focus in their search",
                  fsum("#costTable .fsum") is None and js("[costState.q, costState.cat, costState.deniedOnly]") == ["", "", False]
                  and fsum("#paidTable .fsum") == p_want and js("document.activeElement.matches('#costTable input.search')"),
                  [js("[costState.q, costState.cat, costState.deniedOnly]"), fsum("#paidTable .fsum"), p_want, js("document.activeElement.outerHTML.slice(0, 90)")])
            page.click("#paidTable .fsum-reset")
            settle(300)
            check("…the amounts' puts HSA only off, focus on that toggle; the period kept", fsum("#paidTable .fsum") is None and js("paidState.q") == ""
                  and js("document.activeElement.matches('#paidTable .toggle') && document.activeElement.getAttribute('aria-pressed') === 'false'")
                  and js("state.preset") == "all" and re.match(r"^[\d,]+ amounts? · \$", page.inner_text("#paidTable .toolbar .hint")))
            page.set_viewport_size({"width": 375, "height": 800})
            for theme in ("light", "dark"):
                set_theme(theme)
                settle(200)
                bad = phone_fits(page, f"Costs unfiltered, {theme}")
                check(f"…and unfiltered at 375 px ({theme}), it fits", not bad, bad)
            page.set_viewport_size({"width": 1400, "height": 900})
            set_theme(theme_before)
            js("p => applyPreset(p)", tl_preset)
            settle(200)

            # ---- Labs: the name filter in the lab list's header with its count, the overlays grouped apart from the
            # legend (#192); one filter for the chart's list and the heatmap, apart from the Timeline's search
            open_tab("labs")
            js("clearLabFilter()")
            settle(300)
            check("Labs: the records bar shows no search box (Timeline's alone)", not page.is_visible("#q") and js("document.getElementById('ftoggle').textContent") == "Dates")
            order = js("""(() => { const y = s => document.querySelector(s).getBoundingClientRect().top, hd = document.querySelector('#series .series-hd');
              return [hd.contains(document.getElementById('labQ')), hd.contains(document.getElementById('viewPick')),
                      [...hd.querySelectorAll('#seriesSel button')].map(b => b.textContent), y('#labQ') < y('#seriesSel') && y('#seriesSel') < y('#tvBox'),
                      y('#tvBox') < y('#seriesRows')]; })()""")
            check("…the name filter heads the lab list, above what's selected, Clear, Defaults and the saved views",
                  order == [True, True, ["Clear", "Defaults"], True, True], order)
            n_all = js("SERIES.filter(s => !s.watch && s.n >= 2).length")
            rows = lambda: js("document.querySelectorAll('#seriesRows .srow').length")
            check("…its count says how many labs there are", page.inner_text("#labQn") == f"{n_all} labs" and rows() == n_all, [page.inner_text("#labQn"), rows()])
            sel_before = js("[...state.sel].sort()")
            page.click("#labQ")
            page.locator("#labQ").press_sequentially("hemo", delay=40)
            settle(400)
            want = js("SERIES.filter(s => !s.watch && s.n >= 2 && [s.name, nice(s.name), s.panel].some(t => t.toLowerCase().includes('hemo'))).length")
            check("…typing filters the list and counts the matches (\"k of n\"), keeping the focus in the box",
                  0 < want < n_all and rows() == want and page.inner_text("#labQn") == f"{want} of {n_all}"
                  and js("document.activeElement.id") == "labQ" and js("state.labQ") == "hemo", [rows(), want, page.inner_text("#labQn")])
            check("…said to a screen reader, and the charts and their selection stay as they were",
                  said(page) == f"{want} of {n_all} labs match." and js("[...state.sel].sort()") == sel_before)
            page.click("#seriesSel button:text-is('Clear')")
            settle(300)
            check("…Clear still clears while filtering, the filter kept", js("state.sel.size") == 0 and rows() == want and page.input_value("#labQ") == "hemo")
            page.click("#seriesSel button:text-is('Defaults')")
            settle(300)
            check("…and Defaults brings the starting labs back", js("[...state.sel].sort()") == js("[...startLabs()].sort()") and js("state.sel.size") > 0)
            check("…the saved views stay beside them", page.is_visible("#viewPick"))
            page.click("#labsView button[data-view=heatmap]")
            settle(400)
            hm_all = js("SERIES.filter(s => !s.watch && s.n >= 20 && s.pts.some(p => p[2] != null || p[3] != null)).length")
            hm_want = js("SERIES.filter(s => !s.watch && s.n >= 20 && s.pts.some(p => p[2] != null || p[3] != null) && labMatch(s)).length")
            hm_rows = lambda: js("document.querySelectorAll('#hm text.row').length")
            check("…the heatmap has the same filter in its header, its rows and count filtered by it",
                  page.input_value("#hmQ") == "hemo" and hm_rows() == hm_want > 0 and page.inner_text("#hmQn") == f"{hm_want} of {hm_all}",
                  [page.input_value("#hmQ"), hm_rows(), hm_want, page.inner_text("#hmQn")])
            page.fill("#hmQ", "")
            settle(400)
            check("…emptied there, every lab with ranges", hm_rows() == hm_all and page.inner_text("#hmQn") == f"{hm_all} labs" and js("state.labQ") == "")
            page.fill("#hmQ", "zzzz")
            settle(400)
            check("…and none matching says so", page.inner_text("#hmQn") == f"None of {hm_all}" and "No lab names match" in js("document.getElementById('hm').textContent"))
            page.click("#labsView button[data-view=chart]")
            settle(400)
            check("…back on the chart, the list has what was typed there", page.input_value("#labQ") == "zzzz" and rows() == 0
                  and page.inner_text("#labQn") == f"None of {n_all}" and "No lab names match “zzzz”" in page.inner_text("#seriesRows"))
            page.click("#seriesRows button:text-is('Clear the filter')")
            settle(300)
            check("…its Clear the filter shows them all again, focus back in the box",
                  rows() == n_all and page.input_value("#labQ") == "" and page.input_value("#hmQ") == "" and js("document.activeElement.id") == "labQ")
            open_tab("timeline")
            page.fill("#q", "hemo")
            settle(400)
            check("Timeline: its search keeps its own words, apart from the Labs filter", page.get_attribute("#q", "placeholder") == "Search events…"
                  and js("state.q") == "hemo" and js("state.labQ") == "")
            open_tab("labs")
            settle(300)
            check("…and doesn't filter the Labs list", rows() == n_all and page.input_value("#labQ") == "")
            open_tab("timeline")
            page.fill("#q", "")
            settle(300)
            open_tab("labs")
            settle(300)
            grp = js("""(() => { const g = document.getElementById('optMarks').closest('[role=group]'), lg = document.querySelector('#labsChart .legend');
              return [g && document.getElementById(g.getAttribute('aria-labelledby')).textContent, ['optMarks', 'optMeds', 'optJournal'].map(id => g && g.contains(document.getElementById(id))),
                      [...g.querySelectorAll('.toggle')].filter(t => !t.hidden).map(t => t.textContent.trim()),
                      !!lg && !g.contains(lg) && !lg.querySelector('input, button') && lg.textContent.includes('Out of range')]; })()""")
            check("…the chart overlays grouped under “Show on charts:”, the legend apart from them and only a legend",
                  grp == ["Show on charts:", [True, True, True], ["Procedures & imaging", "Medications", "Journal"], True], grp)
            marks0 = js("state.marks")
            page.click("label:has(#optMarks)")
            settle(300)
            check("…Procedures & imaging still turns its marks off and on from there", js("state.marks") == (not marks0)
                  and page.is_checked("#optMarks") == (not marks0))
            page.click("label:has(#optMarks)")
            settle(300)
            theme_before = js("document.documentElement.dataset.theme || null")
            for mode in ("light", "dark"):
                js("t => { document.documentElement.dataset.theme = t; }", mode)
                page.set_viewport_size({"width": 375, "height": 800})
                open_tab("labs")
                page.fill("#labQ", "hemo")
                settle(400)
                bad = phone_fits(page, f"Labs, filtered ({mode})")
                look = js("""(() => { const i = document.getElementById('labQ'), n = document.getElementById('labQn'), r = i.getBoundingClientRect(), c = n.getBoundingClientRect();
                  return [getComputedStyle(i).color !== getComputedStyle(i).backgroundColor, getComputedStyle(n).color !== getComputedStyle(document.querySelector('#series .series-hd')).backgroundColor,
                          Math.abs(r.top - c.top) < r.height, c.right <= innerWidth]; })()""")
                check(f"…at 375 px ({mode}): the filter and its count on one line, readable, the list and overlays fitting", look == [True, True, True, True] and not bad, [look, bad])
                page.fill("#labQ", "zzzz")
                settle(400)
                bad = phone_fits(page, f"Labs, nothing matching ({mode})")
                check(f"…nothing matching fits too ({mode})", page.is_visible("#seriesRows .lab-none") and not bad, bad)
                page.click("#labsView button[data-view=heatmap]")
                settle(400)
                page.fill("#hmQ", "hemo")
                settle(400)
                bad = phone_fits(page, f"Labs heatmap, filtered ({mode})")
                check(f"…and the heatmap's filter ({mode})", page.is_visible("#hmQ") and not bad, bad)
                js("clearLabFilter()")
                page.click("#labsView button[data-view=chart]")
                settle(300)
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before)
            page.set_viewport_size({"width": 1400, "height": 900})

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
            js("applyPreset('30d')")
            page.select_option("#profile", label="Second person (owner)")
            page.wait_for_function("document.querySelector('main .empty') && PROFILE && PROFILE.name === 'Second person'", timeout=10000)
            check("the period is kept per profile (not carried to another)", js("state.preset") == "2y", js("state.preset"))
            check("empty profile explains itself", "no records yet" in page.inner_text("main"))
            check("empty profile hides the tabs and filters", js("document.querySelector('.tabs').hidden && document.querySelector('.filters').hidden && getComputedStyle(document.querySelector('.tabs')).display === 'none'"))
            check("empty profile's one next step is the getting-started guide, with no redirect there for an account with records elsewhere (#199)",
                  js("[...document.querySelectorAll('main .empty-next')].map(a => a.getAttribute('href'))") == ["guide.html?profile=2"]
                  and js("location.pathname") == "/", js("[...document.querySelectorAll('main .empty-next')].map(a => a.getAttribute('href'))"))
            check("the header links to the import page", js("!document.getElementById('importLink').hidden"))

            # ---- import page: upload a fictional C-CDA in a zip into the empty profile; it's imported in the background
            # (#20) and its records reach the timeline. Then single files, with #20's hook stood in for. Reached through
            # the guide (#199): Get started, past its first step, then Upload an export
            page.click("main .empty a")
            page.wait_for_selector("#step-name:not([hidden])", timeout=10000)
            page.click("#step-name [data-skip]")
            page.click("#step-records a[data-to=upload]")
            page.wait_for_selector("#importer", state="visible", timeout=10000)
            check("…whose Upload an export opens Sources → Upload files for that profile, with a way back to the guide (#199)",
                  js("location.search + location.hash") == "?profile=2&guide=records#upload" and page.is_visible("#guideBar")
                  and js("document.getElementById('guideBack').getAttribute('href')") == "guide.html?profile=2#records", js("location.href"))
            check("import page offers the profiles you can import into, the linked one chosen",
                  js("[...document.querySelectorAll('#profile option')].map(o => o.textContent)") == ["My records (owner)", "Second person (owner)"]
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
            check("…its link names the profile and the tab in the address (#208)",
                  js("document.querySelector('.import-job a[href$=\"#timeline\"]')?.getAttribute('href')") == "index.html?profile=2#timeline")
            page.click(".import-job a[href$='#timeline']")   # "See them on the timeline"
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
            js("applyPreset('all')")
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
            page.select_option("#profile", label="My records (owner)")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records' && "
                                   "TABS.some(t => !document.getElementById('view-' + t).hidden)", timeout=20000)   # its saved view restored
            check("switching back restores the profile's tab", js("state.tab") == "timeline", js("state.tab"))
            check("switching back restores the profile's period", js("state.preset") == "30d", js("state.preset"))

            # ---- import history (#24): the C-CDA and FHIR imports are listed; undoing the FHIR one takes its records
            # off the timeline and leaves the C-CDA's. The setup build's import can't be undone, and says why.
            page.goto(base + "/sources.html?profile=1#history")
            page.wait_for_selector("#history li[data-import]", timeout=10000)
            check("the setup build's import is listed, with why it can't be undone and no Undo",
                  js("(() => { const li = document.querySelector('#history li[data-import]'); return !!li.querySelector('.note') && "
                     "li.querySelector('.note').textContent.includes('rebuild') && !li.querySelector('button.undo'); })()"))
            page.select_option("#profile", label="Second person (owner)")
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
            page.goto(base + "/import.html?profile=2#manual")   # its old address: Sources → Type records in (#166)
            page.wait_for_url("**/sources.html?profile=2#manual", timeout=10000)
            page.wait_for_selector("#typeIn", state="visible", timeout=10000)
            check("Type records in shows its rows, not the upload controls",
                  js("!document.getElementById('panel-manual').hidden && document.querySelectorAll('.man-row').length === 1 && "
                     "getComputedStyle(document.getElementById('importer')).display === 'none'"))
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
            page.goto(base + "/sources.html?profile=2")
            page.wait_for_selector("#srcOverview", state="visible", timeout=10000)
            page.click("#srcNav a[data-group=manual]")
            page.wait_for_selector("#typeIn", state="visible", timeout=10000)
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
            js("applyPreset('all')")
            settle(300)
            costs = page.inner_text("#view-costs")
            check("the typed claim is on Costs, under its care category", "Typed office visit claim" in costs and "Doctor visits" in costs, costs[:300])

            # ---- out-of-pocket costs without a finance export (#55): the typed claim's share is counted until a
            # payment typed in with its receipt and an HSA tag matches it; the receipt opens; the tag changed to FSA
            # on the Costs tab; HSA/FSA totals; the list fits 375 px
            share = js("oopRows().filter(r => r.type === 'claim' && r.claim.title === 'Typed office visit claim').map(r => [r.counted, srcText(r)])")
            check("the typed claim's share is counted out of pocket, saying it came from the claim",
                  len(share) == 1 and share[0][0] == 50 and share[0][1].startswith("Claim: your share"), share)
            page.goto(base + "/import.html?profile=2#payment")
            page.wait_for_selector(".man-item[data-kind=payment]", timeout=10000)
            check("the Costs tab's link opens a payment's form", js("document.querySelectorAll('.man-item').length") == 1)
            page.fill(card(1, "date"), "2026-11-05")
            page.fill(card(1, "payee"), "Lakeside Clinic (fictional)")
            page.fill(card(1, "amount"), "$50.00")
            page.select_option(card(1, "hsa"), "HSA")
            page.set_input_files(card(1, "file"), pdf_path)
            page.wait_for_function("document.querySelector('.man-item .man-file').textContent.includes('uploaded')", timeout=10000)
            page.wait_for_function("document.getElementById('manMsg').textContent === '1 record ready to save.' && !document.getElementById('manSave').disabled", timeout=5000)
            check("…which says it's paid with an HSA and has a receipt", "HSA" in js("document.querySelector('.man-check').textContent")
                  and "receipt" in js("document.querySelector('.man-check').textContent"), js("document.querySelector('.man-check').textContent"))
            page.click("#manSave")
            page.wait_for_function("document.getElementById('manMsg').textContent.startsWith('Saved')", timeout=10000)
            page.click("#manMsg a")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE && PROFILE.name === 'Second person' && DATA.oop", timeout=20000)
            js("switchTab('costs')")
            page.wait_for_function("!document.getElementById('view-costs').hidden", timeout=5000)
            js("paidState.q = ''; applyPreset('all')")
            settle(300)
            pay = js("oopRows().filter(r => r.type === 'pay' && r.who === 'Lakeside Clinic (fictional)' && r.amount === 50)"
                     ".map(r => [r.src, r.hsa, r.claim && r.claim.title, r.receipt != null, r.match])")
            check("the typed payment is listed as typed in, HSA, with its receipt, and matched to the claim",
                  pay == [["typed", "HSA", "Typed office visit claim", True, "auto"]], pay)
            check("…so the claim's share isn't counted again",
                  js("oopRows().some(r => r.type === 'claim' && r.claim.title === 'Typed office visit claim')") is False)
            check("the HSA total counts it, and HSA and FSA are listed by year",
                  "$50" in page.inner_text("#oopHsa") and "2026" in page.inner_text("#oopTax"), [page.inner_text("#oopHsa"), js("!!document.getElementById('oopTax')")])
            row = js("oopKey(oopRows().find(r => r.type === 'pay' && r.who === 'Lakeside Clinic (fictional)' && r.amount === 50))")
            page.click(f"#paidTable tr[data-oop={row}]")
            page.wait_for_selector("#oopEdit", timeout=5000)
            check("a payment says where its number came from", "typed in" in page.inner_text("#oopEdit") and "matched by amount" in page.inner_text("#oopEdit"),
                  page.inner_text("#oopEdit")[:300])
            page.click("#oopEdit >> text=Open receipt")
            page.wait_for_selector("#drawer.open #dbody iframe.doc", timeout=5000)
            check("…and its receipt opens in the drawer as a document", "/documents/" in js("document.querySelector('#dbody iframe.doc').getAttribute('src')"))
            js("closeDrawer()")
            page.select_option("#oopHsaSel", "FSA")
            page.click("#oopEdit >> text=Save")
            page.wait_for_function("document.querySelector('#oopEdit .oop-msg') && document.querySelector('#oopEdit .oop-msg').textContent === 'Saved.'", timeout=10000)
            check("tagged FSA on the Costs tab, it moves to the FSA total", "$50" in page.inner_text("#oopFsa") and "$0" in page.inner_text("#oopHsa"),
                  [page.inner_text("#oopFsa"), page.inner_text("#oopHsa")])
            page.set_viewport_size({"width": 375, "height": 800})
            settle(300)
            check("the out-of-pocket list and its editor fit 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            only = js("""(() => { const had = DATA.costs, o = DATA.oop; DATA.costs = []; DATA.oop = {payments: o.payments.map(p => ({...p, claim: null})), claims: []};
                           renderCosts(); const out = [!!document.getElementById('paidCard'), !!document.getElementById('costTable'),
                           document.getElementById('oopTotal').textContent]; DATA.costs = had; DATA.oop = o; return out; })()""")
            check("with no claims at all, Costs shows the payments alone", only[0] and not only[1] and "Total out of pocket" in only[2], only)
            js("paidState.open = null; renderCosts()")

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
            page.goto(base + "/sources.html?profile=2#link")
            page.wait_for_selector("#healthLink", state="visible", timeout=10000)
            check("Paste a Health Link hides the file drop", js("!document.getElementById('panel-link').hidden && getComputedStyle(document.getElementById('importer')).display === 'none'"))
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
            page.select_option("#profile", label="My records (owner)")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records'", timeout=20000)

            # ---- Settings (#35): change settings on the screen, and the dashboard follows; reset a section
            tiles = "[...document.querySelectorAll('#view-overview .tiles .tile .lbl span[title]')].map(s => [s.title, s.textContent])"
            js("switchTab('overview')")
            settle(300)
            before = js(tiles)
            check("the header links to Settings for this profile", js("document.getElementById('settingsLink').getAttribute('href')") == "settings.html?profile=1")
            menu_click(page, "#settingsLink")
            page.wait_for_selector("#sec-overview .set-labs li", timeout=10000)
            check("Settings shows every section", js("[...document.querySelectorAll('#sections > section h2')].map(h => h.textContent)") ==
                  ["Overview", "Lab charts", "Names", "Body and activity", "Milestones", "Tabs", "Appearance", "Dates and times", "Units", "Reference ranges"], js("[...document.querySelectorAll('#sections > section h2')].map(h => h.textContent)"))
            check("…but not the server's sync defaults or outside connections: those are on the Admin page (#167)",
                  js("!document.getElementById('sec-sync') && !document.getElementById('outside') && !document.querySelector('.set-nav-list a[data-group=sources], .set-nav-list a[data-group=outside]')"))
            # ---- the Settings sidebar (settings-nav.js) and the top bar's menus (nav.js)
            display = "id => getComputedStyle(document.getElementById(id)).display"
            check("Settings: a sidebar of groups, one shown at a time",
                  js("[...document.querySelectorAll('.set-nav-list a')].map(a => a.textContent)")[:4] == ["Dashboard", "Labs and ranges", "Milestones and activity", "Display"]
                  and js(display, "sec-overview") != "none" and js(display, "sec-units") == "none", js("[...document.querySelectorAll('.set-nav-list a')].map(a => a.textContent)"))
            page.fill("#setFind", "time zone")
            check("…Find a setting shows the settings that match, from every group",
                  js(display, "sec-dates") != "none" and js(display, "sec-overview") == "none" and js(display, "sec-units") == "none")
            page.fill("#setFind", "")
            page.click(".set-nav-list a[data-group=display]")
            check("…a group picked in the sidebar is the one shown, and the address says so",
                  js(display, "sec-units") != "none" and js(display, "sec-overview") == "none" and js("location.hash") == "#display")
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("…Settings and its sidebar fit 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            # ---- #174: the group's title and whose settings these are above it (no card of its own), one Save per
            # section with a way back, and on a phone the page's title whole and each key lab one row with a menu
            check("Settings at 375 px: the top bar's title is whole",
                  js("(t => t.textContent === 'Settings' && t.scrollWidth <= t.clientWidth && t.scrollHeight <= t.clientHeight + 1)(document.querySelector('header .page-title'))"),
                  js("(t => [t.scrollWidth, t.clientWidth, t.scrollHeight, t.clientHeight])(document.querySelector('header .page-title'))"))
            check("…Display says, under its title, that its settings are just for you",
                  js("document.getElementById('groupTitle').textContent") == "Display" and js("document.getElementById('introHint').textContent").startswith("Just for you")
                  and js("getComputedStyle(document.getElementById('intro')).borderTopWidth") == "0px", js("document.getElementById('introHint').textContent"))
            groups = js("""(() => { const out = {}; for (const a of document.querySelectorAll('.set-nav-list a')) { a.click();
                out[a.dataset.group] = [document.getElementById('introHint').textContent.split(/[,:]/)[0],
                  [...new Set([...document.querySelectorAll('#sections > section:not(.grp-off) .set[data-scope]')].map(s => s.dataset.scope))]]; }
                return out; })()""")
            say = {"profile": "This profile", "user": "Just for you", "install": "This server"}
            check("…and every group says whose its settings are, as their scope has it",
                  groups["dashboard"][0] == "This profile" and groups["display"][0] == "Just for you" and "calendar" not in groups
                  and all(w in say.values() and len(sc) <= 1 and (not sc or say[sc[0]] == w) for w, sc in groups.values()), groups)
            saves = js("[...document.querySelectorAll('#sections > section')].map(s => [...s.querySelectorAll('button')].filter(b => b.textContent === 'Save').length)")
            check("…one Save per section (none per setting), and nothing marked unsaved on arrival",
                  set(saves) == {1} and not js("[...document.querySelectorAll('#sections .set button')].some(b => b.textContent === 'Save')")
                  and not js("document.querySelector('#sections > [data-dirty], .set-changed')"), saves)
            page.click(".set-nav-list a[data-group=dashboard]")
            rows = "[...document.querySelectorAll('#sec-overview .set-order li')]"
            check("…on a phone each key lab is one row: its handle, name and a menu, the ↑ ↓ Remove buttons put away",
                  js(f"""{rows}.every(li => {{ const r = li.getBoundingClientRect(), m = li.querySelector('.set-more-btn').getBoundingClientRect(),
                      g = li.querySelector('.set-grip').getBoundingClientRect();
                      return m.top - r.top < 30 && g.top - r.top < 30 && m.top >= r.top && m.bottom <= r.bottom && g.top >= r.top && g.bottom <= r.bottom && !li.querySelector('.set-acts').offsetParent; }})"""),
                  js(f"{rows}.map(li => Math.round(li.getBoundingClientRect().height))"))
            order = "[...document.querySelectorAll('#sec-overview .set-more-btn')].map(b => b.getAttribute('aria-label').slice('Move or remove '.length))"
            o0 = js(order)
            page.click("#sec-overview .set-order li:nth-child(1) .set-more-btn")
            page.click("#sec-overview .set-order li:nth-child(1) .menu-item:text-is('Move down')")
            check("…its menu, tapped, moves the lab down", js(order)[:2] == [o0[1], o0[0]], js(order)[:3])
            check("…and it shows it isn't saved: on the setting, with its section's Save kept in view, and its group in the sidebar",
                  js("document.querySelector(\"[data-key='overview.keyLabs']\").classList.contains('set-changed')")
                  and js("getComputedStyle(document.querySelector('#sec-overview .set-foot')).position") == "sticky"
                  and js("document.querySelector('.set-nav-list a[data-group=dashboard]').textContent") == "Dashboard, changes not saved")
            page.focus("#sec-overview .set-order li:nth-child(2) .set-more-btn")
            page.keyboard.press("Enter")
            first_act = js("document.activeElement.textContent")
            page.keyboard.press("ArrowDown")
            check("…the menu works from the keyboard too: Enter opens it on its first action, the arrows move through it",
                  first_act == "Move up" and js("document.activeElement.textContent") == "Move down", first_act)
            page.keyboard.press("Escape")
            check("…and Escape closes it, back on its button",
                  js("document.activeElement.getAttribute('aria-label')") == f"Move or remove {o0[0]}" and js("document.querySelector('#sec-overview .set-order li:nth-child(2) .menu-list').hidden"))
            page.keyboard.press("Enter")
            page.keyboard.press("End")
            page.keyboard.press("Enter")
            check("…its last action takes the lab out, the focus on the row now in its place",
                  o0[0] not in js(order) and js("document.activeElement.getAttribute('aria-label')") == f"Move or remove {o0[2]}", js(order)[:3])
            leave = "(() => { const e = new Event('beforeunload', {cancelable: true}); dispatchEvent(e); return e.defaultPrevented; })()"
            check("…leaving the page with it not saved asks first", js(leave))
            page.select_option("#profile", label="Second person (owner)")
            answer_ask(page, yes=False)
            check("…so does switching profiles, which stays put when told to",
                  js("PID") == 1 and js("document.getElementById('profile').value") == "1" and js("document.getElementById('sec-overview').hasAttribute('data-dirty')"))
            page.click("#sec-overview .set-foot button:text-is('Discard changes')")
            check("…Discard changes puts it back as saved, and nothing's left unsaved",
                  js(order) == o0 and not js("document.querySelector('#sections > [data-dirty]')") and not js(leave)
                  and js("document.querySelector('.set-nav-list a[data-group=dashboard]').textContent") == "Dashboard"
                  and js("document.querySelector('#sec-overview .set-foot > .msg').textContent").startswith("Changes discarded"), js(order)[:3])
            settle(200)
            check("…and the key labs fit 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            # (#210) told yes in the app's dialog, switching profile with changes not saved goes on to the other profile
            page.click("#sec-overview .set-order li:nth-child(1) .set-more-btn")
            page.click("#sec-overview .set-order li:nth-child(1) .menu-item:text-is('Move down')")
            page.select_option("#profile", label="Second person (owner)")
            switch_asked = answer_ask(page)
            page.wait_for_function("PID === 2 && !document.querySelector('#sections > [data-dirty]')", timeout=10000)
            check("…and told yes in the app's dialog, it switches, the changes left behind (#210)",
                  switch_asked[0] == "Switch profile and lose your changes?" and js("document.getElementById('profile').value") == "2", switch_asked)
            page.select_option("#profile", "1")
            page.wait_for_function("PID === 1", timeout=10000)
            settle(400)
            check("…and back on the first profile, its key labs are as saved", js(order) == o0, js(order)[:3])
            page.set_viewport_size({"width": 1400, "height": 900})
            page.click(".set-nav-list a[data-group=dashboard]")
            page.click("#userMenuBtn")
            check("the top bar: your menu opens with its items, Admin among them for an admin", page.is_visible("#settingsLink") and page.is_visible("#logout")
                  and page.is_visible("#adminLink") and js("document.getElementById('userMenuBtn').getAttribute('aria-expanded')") == "true")
            page.keyboard.press("Escape")
            check("…Escape closes it and puts the focus back on its button", page.is_hidden("#userMenuList") and js("document.activeElement.id") == "userMenuBtn")
            page.focus("#addMenuBtn")
            page.keyboard.press("ArrowDown")
            check("…the Add menu opens from the keyboard, on its first item", js("document.activeElement.textContent") == "Upload files", js("document.activeElement.textContent"))
            page.keyboard.press("ArrowDown")
            check("…and the arrow keys move through it", js("document.activeElement.textContent") == "Connect a portal or plan", js("document.activeElement.textContent"))
            check("…each way in opening its section of Sources (#166)", js("[...document.querySelectorAll('#addMenuList a[data-needs=import]')].map(a => a.getAttribute('href'))")
                  == ["sources.html#upload", "sources.html#portals", "sources.html#manual", "sources.html#link"])
            page.keyboard.press("Escape")
            # ---- the Admin page (#167): the server's sync defaults, from their old address on Settings
            page.goto(base + "/settings.html#sources")
            page.wait_for_url("**/admin.html#apps", timeout=10000)
            page.wait_for_selector("#sec-sync .set", timeout=10000)
            check("Admin: an old Settings address opens its new place, the sync defaults (#167)",
                  js(display, "sec-sync") != "none" and js(display, "admin") != "none" and js(display, "users") == "none")
            check("…as a switch, numbers and choices, with one Save for the section (#101, #102, #174)",
                  js("[document.querySelectorAll('#sec-sync input[type=checkbox]').length, document.querySelectorAll('#sec-sync input[type=number]').length, "
                     "document.querySelectorAll('#sec-sync button.primary').length]") == [5, 3, 1], js("document.getElementById('sec-sync').innerText.slice(0, 300)"))
            page.fill("#sec-sync [data-key='sync.staleDays'] input", "5")
            page.click("#syncSave")
            page.wait_for_function("document.querySelector('#sec-sync .msg').textContent.startsWith('Saved')", timeout=10000)
            stale = "fetch('api/profiles/1/settings').then(r => r.json()).then(s => s.sync.staleDays)"
            check("…saved for every connection on the server", js(stale) == 5 and "set for this server" in page.inner_text("#sec-sync [data-key='sync.staleDays']"), js(stale))
            page.click("#sec-sync .set-hd button")
            answer_ask(page)
            page.wait_for_function("document.querySelector('#sec-sync .msg').textContent.includes('back to')", timeout=10000)
            check("…and Reset to defaults puts them back", js(stale) == 3, js(stale))
            page.click("#admNav a[data-group=directory]")
            dir_text = page.inner_text("#directory")
            check("Admin: a group picked in the sidebar is the one shown, and the address says so",
                  js(display, "directory") != "none" and js(display, "sec-sync") == "none" and js("location.hash") == "#directory")
            check("…the provider directory says what's loaded, and how to add Epic's and Oracle Health's lists",
                  "Medicare" in dir_text and "not loaded" in dir_text and "python3 tools/build_directory.py" in dir_text, dir_text[:400])
            for gid in ("accounts", "backups", "apps", "directory", "labs", "outside", "activity"):
                page.click(f"#admNav a[data-group={gid}]")
                page.set_viewport_size({"width": 375, "height": 800})
                settle(150)
                if js("document.documentElement.scrollWidth") > 375:
                    check(f"Admin's {gid} group fits 375 px", False, js("document.documentElement.scrollWidth"))
                page.set_viewport_size({"width": 1400, "height": 900})
            check("…and each of its groups fits 375 px", True)
            # ---- backups in Admin (#200): the key reminder in the card's lead; Back up now runs in the background and
            # then says what it made (when, size, where); Check a backup says it's good, its temporary copy gone; the
            # schedule is a pick-one with how many to keep, and turning it off is asked first in the app's dialog; a
            # backup gone stale shows the warning on every group, beside Backups in the sidebar and on the Admin menu
            # item. In light and dark, at 1400 and 375 px.
            bk_dir = os.path.join(TMP, "backups")
            page.click("#admNav a[data-group=backups]")
            page.wait_for_selector("#bkStatus .kv", timeout=10000)
            check("Backups (#200): its own group, the key reminder in the lead (not behind More about this)",
                  js(display, "backups") != "none" and js("location.hash") == "#backups"
                  and "encryption key isn't in the backups" in js("document.querySelector('#backups > .bk-key').textContent")
                  and js("document.querySelector('#backups > .bk-key').checkVisibility()"), js("location.hash"))
            check("…no backup yet, said in words", js("document.getElementById('bkState').textContent").startswith("No backups yet"))
            page.click("#bkNow")
            page.wait_for_function("document.getElementById('bkNowMsg').textContent.startsWith('Backed up')", timeout=30000)
            made = sorted(n for n in os.listdir(bk_dir) if n.startswith("sanguity-backup-"))
            bk_text = page.inner_text("#bkStatus")
            check("…Back up now runs in the background, then says what it made: when, how big and where", len(made) == 1
                  and made[0] in js("document.getElementById('bkNowMsg').textContent") and "✓ Up to date." in bk_text
                  and bk_dir in bk_text and "1 backup in the folder" in bk_text and js("document.getElementById('bkProgress').hidden"), (made, bk_text[:300]))
            page.click("#bkList tbody button")
            page.wait_for_selector("#bkResult .bk-state", timeout=30000)
            res = page.inner_text("#bkResult")
            check("…Check a backup restores a copy, says it's good with the counts, and the copy is gone",
                  res.startswith("✓ Good.") and "The copy was deleted" in res and js("document.querySelectorAll('#bkResult .bk-counts tbody tr').length") == 6
                  and sorted(os.listdir(bk_dir)) == made and "✓ good" in page.inner_text("#bkList tbody"), (res[:300], os.listdir(bk_dir)))
            page.click("#bkSchedSeg label:has-text('Daily')")
            page.fill("#bkKeep", "3")
            page.click("#bkSchedule button[type=submit]")
            page.wait_for_function("document.getElementById('bkNowMsg').textContent.startsWith('Backups are scheduled daily')", timeout=10000)
            sched = "fetch('api/backups').then(r => r.json()).then(s => [s.schedule, s.keep])"
            check("…the schedule: daily, keeping the newest 3, saved", js(sched) == ["daily", 3] and "daily, keeping the newest 3" in page.inner_text("#bkStatus"), js(sched))
            page.click("#bkSchedSeg label:has-text('Off')")
            page.click("#bkSchedule button[type=submit]")
            asked = answer_ask(page)
            for _ in range(50):   # the fresh check, if the sign-in was more than a few minutes ago
                if js("!document.getElementById('bkReauth').hidden || document.getElementById('bkNowMsg').textContent.startsWith('Scheduled backups are off')"):
                    break
                settle(100)
            if js("!document.getElementById('bkReauth').hidden"):
                page.fill("#bkReauth input[type=password]", PW)
                page.click("#bkReauth button[type=submit]")
            page.wait_for_function("document.getElementById('bkNowMsg').textContent.startsWith('Scheduled backups are off')", timeout=10000)
            check("…turning it off is asked first, in the app's dialog", asked[0] == "Turn off scheduled backups?" and js(sched)[0] == "off", asked)
            # stale: the backup made 10 days ago, as far as the app can tell (its name says when it was made)
            stale_name = time.strftime("sanguity-backup-%Y%m%d-%H%M%S.tar.gz", time.localtime(time.time() - 10 * 86400))
            os.replace(os.path.join(bk_dir, made[0]), os.path.join(bk_dir, stale_name))
            page.goto(base + "/admin.html#accounts")
            page.reload()   # only the address's #part changed: load the page again
            page.wait_for_selector("#backupNotice:not([hidden])", timeout=10000)
            notice = page.inner_text("#backupNotice")
            check("…a late backup: the warning on every group (Accounts here), saying how old and linking to Backups", "Backups need attention" in notice
                  and "10 days old" in notice and js("document.querySelector('#backupNotice a').getAttribute('href')") == "#backups", notice)
            check("…and beside Backups in the sidebar, in words", js("document.querySelector('#admNav a[data-group=backups] .bk-late')?.textContent") == "late")
            page.click("#userMenuBtn")
            check("…and on the Admin menu item", js("document.querySelector('#adminLink .badge')?.textContent") == "⚠ backups late",
                  js("document.getElementById('adminLink').textContent"))
            page.keyboard.press("Escape")
            page.click("#backupNotice a")
            page.wait_for_selector("#bkStatus .kv", timeout=10000)
            theme_was_200 = js("localStorage.getItem('ht-theme')")
            for theme in ("light", "dark"):
                js(f"localStorage.setItem('ht-theme', '{theme}'); applyTheme()")
                settle(150)
                low = js("""[...document.querySelectorAll('#backups .bk-state, #backups .bk-key, #backups .kv dd, #bkResult .bk-state, #backupNotice')]
                            .map(e => [e.id || e.className, getComputedStyle(e).color, getComputedStyle(document.getElementById('backups')).backgroundColor])""")
                low = [(w, contrast(c, bg)) for w, c, bg in low if contrast(c, bg) < 4.5]
                check(f"…readable in the {theme} theme (text contrast 4.5 or more)", low == [], low)
                for width in (1400, 375):
                    page.set_viewport_size({"width": width, "height": 900})
                    settle(150)
                    fits = phone_fits(page, f"Admin Backups, {theme}, {width} px")
                    check(f"…Backups fits {width} px in the {theme} theme", fits == [], fits)
                page.set_viewport_size({"width": 1400, "height": 900})
            js(f"localStorage.setItem('ht-theme', {json.dumps(theme_was_200)}); applyTheme()" if theme_was_200 else "localStorage.removeItem('ht-theme'); applyTheme()")
            os.replace(os.path.join(bk_dir, stale_name), os.path.join(bk_dir, made[0]))   # up to date again for the checks below
            page.goto(base + "/settings.html?profile=1")   # back to Settings for the checks below
            page.wait_for_selector("#sec-overview .set-labs li", timeout=10000)
            first = before[0][0]
            page.click(f"#sec-overview button[aria-label='Move {first} down']")
            page.click("#sec-overview .set-foot button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-overview .set-foot > .msg').textContent.startsWith('Saved')", timeout=10000)
            page.click("[data-key=labNicknames] button:text-is('Add a row')")
            page.fill("[data-key=labNicknames] .set-row:last-child input[data-k=from]", first)
            page.fill("[data-key=labNicknames] .set-row:last-child input[data-k=to]", "Renamed in Settings")
            page.click("#sec-names .set-foot button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-names .set-foot > .msg').textContent.startsWith('Saved')", timeout=10000)
            # ---- calendar feed (#51): made on the Sharing page (#168; Settings' old address opens it there), its address
            # shown once with setup steps, and the address works for a calendar app with no session
            page.goto(base + "/settings.html?profile=1#calendar")
            page.wait_for_url("**/sharing.html?profile=1#calendar", timeout=10000)
            page.wait_for_selector("#calendar:not([hidden]) #calNew:not([hidden])", timeout=10000)
            check("Settings' old calendar address opens Sharing → Calendar feed", js("document.querySelector('#shrNav [aria-current=page]').textContent") == "Calendar feed")
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
            check("the Sharing page's calendar feed fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records'", timeout=20000)
            js("switchTab('overview')")
            settle(300)
            after = js(tiles)
            check("…the Overview tiles follow the new order and name",
                  [t for t, _ in after[:2]] == [before[1][0], first] and after[1][1] == "Renamed in Settings", after[:3])
            page.goto(base + "/settings.html?profile=1#sec-overview")
            page.wait_for_selector("#sec-overview .set-labs li", timeout=10000)
            for sec in ("overview", "names"):
                page.click(f"#sec-{sec} button:text-is('Reset to defaults')")
                answer_ask(page)
                page.wait_for_function(f"document.querySelector('#sec-{sec} .set-foot > .msg').textContent.includes('back to their defaults')", timeout=10000)
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records'", timeout=20000)
            js("switchTab('overview')")
            settle(300)
            check("…and Reset to defaults brings them back", js(tiles) == before, js(tiles)[:3])

            # ---- #36 key-lab tiles: drag or keyboard-move them, with a live preview, and the Overview follows
            page.goto(base + "/settings.html?profile=1#sec-overview")
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
            page.click("#sec-overview .set-foot button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-overview .set-foot > .msg').textContent.startsWith('Saved')", timeout=10000)
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records'", timeout=20000)
            js("switchTab('overview')")
            settle(300)
            check("…the Overview follows the new order", [t for t, _ in js(tiles)[:3]] == p2[:3], js(tiles)[:3])

            # ---- #40 tabs: hide Costs and move Timeline up; gone from the bar and the arrow keys, a saved view or a
            # link to it opens the first shown tab, and one tab at least stays shown
            js("switchTab('costs')")   # the saved view now names Costs
            page.goto(base + "/settings.html?profile=1#sec-tabs")
            page.wait_for_selector("#sec-tabs input[data-tab=costs]", timeout=10000)
            page.uncheck("#sec-tabs input[data-tab=costs]")
            page.click("#sec-tabs button[aria-label='Move Timeline up']")
            page.click("#sec-tabs .set-foot button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-tabs .set-foot > .msg').textContent.startsWith('Saved')", timeout=10000)
            shown_tabs = "[...document.querySelectorAll('.tabs [role=tab]')].filter(b => !b.hidden).map(b => b.id.slice(4))"
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records' && booted", timeout=20000)
            check("tabs: a hidden tab leaves the bar, the others in their new order",
                  js(shown_tabs) == ["overview", "timeline", "health", "labs"], js(shown_tabs))
            check("…a saved view naming it opens the first shown tab", js("state.tab") == "overview" and js("!document.getElementById('view-overview').hidden"), js("state.tab"))
            page.focus("#tab-overview")
            page.keyboard.press("ArrowLeft")
            check("…the arrow keys skip it", js("state.tab") == "labs" and js("document.activeElement.id") == "tab-labs", js("state.tab"))
            page.keyboard.press("ArrowRight")
            js("switchTab('costs')")
            check("…asking for it opens the first shown tab", js("state.tab") == "overview", js("state.tab"))
            page.goto(base + "/#costs")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records' && booted", timeout=20000)
            check("…so does a link to it", js("state.tab") == "overview" and js("document.getElementById('view-costs').hidden"), js("state.tab"))
            page.goto(base + "/#timeline")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records' && booted", timeout=20000)
            check("…while a link to a shown tab opens it", js("state.tab") == "timeline", js("state.tab"))
            for old, view in (("heatmap", "heatmap"), ("trends", "chart"), ("labs", "chart")):   # #172: the old tabs' links open Labs
                page.goto(base + "/#" + old)
                page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records' && booted", timeout=20000)
                check(f"…a link to #{old} opens Labs as a {view}", js("[state.tab, state.labsView]") == ["labs", view]
                      and js("id => !document.getElementById('view-labs').hidden && !document.getElementById(id).hidden", "labsHeatmap" if view == "heatmap" else "labsChart"),
                      js("[state.tab, state.labsView]"))
            page.goto(base + "/settings.html?profile=1#sec-tabs")
            page.wait_for_selector("#sec-tabs input[data-tab=costs]", timeout=10000)
            for t in ("overview", "health", "labs"):
                page.uncheck(f"#sec-tabs input[data-tab={t}]")
            check("…and the last shown tab can't be hidden", js("document.querySelector('#sec-tabs input[data-tab=timeline]').disabled"))
            page.click("#sec-tabs button:text-is('Reset to defaults')")
            answer_ask(page)
            page.wait_for_function("document.querySelector('#sec-tabs .set-foot > .msg').textContent.includes('back to their defaults')", timeout=10000)
            page.click("#sec-overview button:text-is('Reset to defaults')")
            answer_ask(page)
            page.wait_for_function("document.querySelector('#sec-overview .set-foot > .msg').textContent.includes('back to their defaults')", timeout=10000)
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records' && booted", timeout=20000)
            check("…Reset to defaults shows every tab again", js(shown_tabs) == ["overview", "health", "timeline", "labs", "costs"], js(shown_tabs))
            # a layout saved before #172, with Trends and Lab heatmap: Labs takes the first one's place, and stays shown
            # while either was; the Settings page lists the five tabs
            db = dbm.connect()
            db.execute("INSERT INTO settings(key, profile_id, user_id, value, sealed, changed, changed_by) VALUES('tabs', 1, NULL, ?, NULL, ?, NULL)",
                       (json.dumps({"order": ["overview", "heatmap", "health", "timeline", "labs", "costs"], "hidden": ["labs", "costs"]}), int(time.time())))
            db.close()
            page.reload()
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records' && booted", timeout=20000)
            check("…a layout saved with Trends and Lab heatmap shows Labs where the heatmap was, shown as it was",
                  js(shown_tabs) == ["overview", "labs", "health", "timeline"] and js("CFG.tabs") == {"order": ["overview", "labs", "health", "timeline", "costs"], "hidden": ["costs"]},
                  [js(shown_tabs), js("CFG.tabs")])
            page.goto(base + "/settings.html?profile=1#sec-tabs")
            page.wait_for_selector("#sec-tabs input[data-tab=costs]", timeout=10000)
            check("…and Settings lists the five tabs, Labs shown", js("[...document.querySelectorAll('#sec-tabs input[data-tab]')].map(i => [i.dataset.tab, i.checked])")
                  == [["overview", True], ["labs", True], ["health", True], ["timeline", True], ["costs", False]],
                  js("[...document.querySelectorAll('#sec-tabs input[data-tab]')].map(i => [i.dataset.tab, i.checked])"))
            page.click("#sec-tabs button:text-is('Reset to defaults')")
            answer_ask(page)
            page.wait_for_function("document.querySelector('#sec-tabs .set-foot > .msg').textContent.includes('back to their defaults')", timeout=10000)
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records' && booted", timeout=20000)
            js("switchTab('overview')")

            # ---- the tab in the address (#208), in a browser of its own (its own history and saved view): picking a
            # tab adds a history entry, so Back and Forward move between tabs; the app's own switches and the Labs view
            # replace it; a reload, an old alias and a copied address in a new browser open the tab named; ?profile=
            # stays beside the #, and an address with no # opens the tab last used
            ready = "typeof EV !== 'undefined' && EV.length > 0 && PROFILE.name === 'My records' && booted"
            session = [{k: c[k] for k in ("name", "value", "domain", "path", "httpOnly", "sameSite")} for c in page.context.cookies() if c["name"] == "sg_session"]
            tctx = browser.new_context(viewport={"width": 1400, "height": 900}, bypass_csp=True)
            tctx.add_cookies(session)
            tp = tctx.new_page()
            tp.on("pageerror", lambda e: problems.append(f"script error (tab address): {e}"))
            blank = tp.evaluate("history.length")   # the new tab's about:blank
            tp.goto(base + "/")
            tp.wait_for_function(ready, timeout=20000)
            addr = lambda: tp.evaluate("[location.search, location.hash, state.tab, state.labsView, history.length]")
            n0 = tp.evaluate("history.length")
            check("tab address: opening the page with no # names the tab it opens, without a history entry of its own",
                  addr()[1:] == ["#overview", "overview", "chart", n0] and n0 == blank + 1, [addr(), blank])
            for t in ("timeline", "labs", "costs"):
                tp.click("#tab-" + t)
            check("…picking Timeline, Labs and Costs adds an entry each, the address naming the tab", addr()[1:] == ["#costs", "costs", "chart", n0 + 3], addr())
            tp.go_back()
            tp.go_back()
            tp.wait_for_function("state.tab === 'timeline'", timeout=5000)
            check("…Back twice lands on Timeline", addr()[1:3] == ["#timeline", "timeline"] and tp.evaluate("!document.getElementById('view-timeline').hidden"), addr())
            tp.go_forward()
            tp.wait_for_function("state.tab === 'labs'", timeout=5000)
            check("…and Forward on Labs", addr()[1:3] == ["#labs", "labs"], addr())
            tp.evaluate("switchTab('overview'); switchTab('costs'); switchTab('heatmap')")
            check("…the app's own switches replace the entry (Labs as the heatmap: #labs-heatmap)", addr()[1:] == ["#labs-heatmap", "labs", "heatmap", n0 + 3], addr())
            tp.click("#labsView button[data-view=chart]")
            check("…so does the Labs view", addr()[1:] == ["#labs", "labs", "chart", n0 + 3], addr())
            tp.click("#labsView button[data-view=heatmap]")
            tp.reload()
            tp.wait_for_function(ready, timeout=20000)
            check("…a reload keeps the tab and its view", addr()[1:4] == ["#labs-heatmap", "labs", "heatmap"], addr())
            tp.evaluate("location.hash = '#trends'")
            tp.wait_for_function("state.labsView === 'chart'", timeout=5000)
            check("…an old alias opens its tab, the address then naming it", addr()[1:4] == ["#labs", "labs", "chart"], addr())
            tp.goto(base + "/?profile=1#costs")
            tp.wait_for_function(ready + " && state.tab === 'costs'", timeout=20000)
            tp.click("#tab-health")
            tp.click("#tab-labs")
            tp.click("#labsView button[data-view=heatmap]")
            check("…?profile= stays beside the #", addr()[:2] == ["?profile=1", "#labs-heatmap"], addr())
            copied = tp.url
            other = browser.new_context(viewport={"width": 1400, "height": 900}, bypass_csp=True)   # no saved view
            other.add_cookies(session)
            op = other.new_page()
            op.on("pageerror", lambda e: problems.append(f"script error (copied address): {e}"))
            op.goto(copied)
            op.wait_for_function(ready, timeout=20000)
            check("…a copied address opens the same tab in a new browser",
                  op.evaluate("[location.search, location.hash, state.tab, state.labsView, !document.getElementById('labsHeatmap').hidden]")
                  == ["?profile=1", "#labs-heatmap", "labs", "heatmap", True], op.evaluate("[location.href, state.tab, state.labsView]"))
            other.close()
            tp.go_back()
            tp.wait_for_function("state.tab === 'health'", timeout=5000)
            check("…and Back from there returns to Body and activity, the profile kept", addr()[:3] == ["?profile=1", "#health", "health"], addr())
            tp.goto(base + "/")
            tp.wait_for_function(ready, timeout=20000)
            check("…while the address with no # opens the tab last used", addr()[1:3] == ["#health", "health"], addr())
            tctx.close()

            # ---- #45 a default theme for the account: a fresh browser signed in as the same person uses it; the ◐
            # toggle still overrides it there; Reset to defaults follows the system again
            js("localStorage.removeItem('ht-theme')")
            page.goto(base + "/settings.html?profile=1#sec-appearance")
            page.wait_for_selector("#sec-appearance input[value=dark]", timeout=10000)
            page.check("#sec-appearance input[value=dark]")
            page.click("#sec-appearance .set-foot button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-appearance .set-foot > .msg').textContent.startsWith('Saved')", timeout=10000)
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
            menu_click(fp, "#theme")
            fp.reload()
            fp.wait_for_function("typeof EV !== 'undefined' && EV.length > 0", timeout=20000)
            check("…where the ◐ toggle still overrides it", fp.evaluate("document.documentElement.dataset.theme") == "light")
            fresh.close()
            page.click("#sec-appearance button:text-is('Reset to defaults')")
            answer_ask(page)
            page.wait_for_function("document.querySelector('#sec-appearance .set-foot > .msg').textContent.includes('back to their defaults')", timeout=10000)
            check("…and Reset to defaults follows the system again",
                  js("document.documentElement.dataset.theme === undefined && localStorage.getItem('ht-theme-default') === null"))
            # ---- #41 unit preferences: SI units and kg, picked on the Settings page, convert the demo's creatinine
            # (mg/dL to umol/L), magnesium and hemoglobin and the weights on Trends, the Overview tiles and the drawer, the
            # reported value beside each; bilirubin has no exact conversion and stays as reported; at 375 px too
            page.goto(base + "/settings.html?profile=1#sec-units")
            page.wait_for_selector("#sec-units input[name=units-lab][value=si]", timeout=10000)
            page.check("#sec-units input[name=units-lab][value=si]")
            page.check("#sec-units input[name=units-weight][value=kg]")
            # ---- pick one on Settings (#193): the theme, each part's units and the sex for reference ranges are segmented
            # controls of radios, each with exactly one checked, and the radio picked (above, through its see-through box)
            # is the one that looks selected
            radios = r"""[...document.querySelectorAll('fieldset.set-radios > .seg')].map(g => [g.closest('[id^=sec-]').id + ' ' + (g.parentElement.dataset.part || ''),
              g.querySelectorAll(':scope > label > input[type=radio]:checked').length, g.children.length,
              [...g.children].filter(l => getComputedStyle(l).boxShadow.includes('inset')).map(l => l.querySelector('input').value).join()])"""
            got = js(radios)
            check("pick one on Settings: every group of radios is one control with exactly one checked (theme, units, sex for ranges)",
                  len(got) == 5 and all(n == 1 and k > 1 for _, n, k, _ in got), got)
            check("…and the one picked looks selected", [x[3] for x in got if x[0] in ("sec-units lab", "sec-units weight")] == ["si", "kg"], got)
            page.click("#sec-units .set-foot button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-units .set-foot > .msg').textContent.startsWith('Saved')", timeout=10000)
            page.goto(base + "/")
            page.wait_for_function("typeof booted !== 'undefined' && booted && PROFILE.id === 1", timeout=20000)
            unit_of = "n => { const s = SERIES.find(s => s.name === n); const p = s && s.pts.at(-1); return s && {unit: s.unit, from: s.uc ? s.uc.from : null, v: p[1], orig: p.orig ? p.orig.v : null, lo: p[2]}; }"
            cr, mg, hb, bili = (js(unit_of, n) for n in ("Creatinine", "Magnesium", "Hemoglobin", "Total Bilirubin"))
            check("units: SI shows creatinine in µmol/L, converted exactly from what was reported",
                  cr["unit"] == "µmol/L" and cr["from"] == "mg/dL" and abs(cr["v"] - float(cr["orig"]) * 10000 / 113.12) < 1e-6
                  and abs(cr["lo"] - 61.9) < 1e-9, cr)
            check("…magnesium in mmol/L and hemoglobin in g/L", (mg["unit"], hb["unit"]) == ("mmol/L", "g/L")
                  and abs(hb["v"] - float(hb["orig"]) * 10) < 1e-9, (mg, hb))
            check("…bilirubin, with no exact conversion, stays as reported", bili["unit"] == "mg/dL" and bili["from"] is None, bili)
            check("…and the weights are in kg", js("SERIES.find(s => s.key === 'weight').unit") == "kg")
            js("switchTab('overview')")
            tile = js("[...document.querySelectorAll('#view-overview .tile')].find(t => t.textContent.includes('Creatinine'))?.textContent || ''")
            check("Overview: the creatinine tile in µmol/L, with the reported value", "µmol/L" in tile and re.search(r"Reported [\d.]+ mg/dL", tile), tile)
            wtile = js("[...document.querySelectorAll('#view-overview .tile')].find(t => t.textContent.startsWith('Weight'))?.textContent || ''")
            check("…the weight tile in kg, with the reported lb", "kg" in wtile and re.search(r"Reported [\d.]+ lb", wtile), wtile)
            # over all of the records: the period earlier checks can leave saved (the last 30 days) starts hours after the
            # demo's last creatinine, so the charts had no results and the table no rows in some runs
            js("state.sel = new Set(['Creatinine', 'Total Bilirubin']); state.tables.clear(); setPeriod('all'); switchTab('labs'); renderLabs()")
            page.wait_for_selector("#charts .chart .unit", timeout=10000)
            heads = js("[...document.querySelectorAll('#charts .chart .unit')].map(u => u.textContent)")
            check("Trends: the chart says it's converted; bilirubin's doesn't", len(heads) == 2   # charts come in the series' order
                  and sorted(h.split(" · ")[0] for h in heads) == ["mg/dL", "µmol/L (converted from mg/dL)"], heads)
            rd = js("document.querySelector('#charts .chart .rdiff')?.textContent || ''")   # #58: ranges compared once converted
            check("…and the two labs' creatinine ranges, converted, still differ", rd.startswith("Reference range differs between results: 61.9–115 and 53–106 µmol/L"), rd)
            js("state.tables.add('Creatinine'); renderCharts()")
            cell = js("document.querySelector('#charts table.data tbody td:nth-child(2)').textContent")
            check("…its table shows the reported value beside each", re.search(r"µmol/L \(reported [\d.]+ mg/dL, ref 0\.7–1\.3\)", cell), cell)
            js("state.tables.clear()")
            ev = js("EV.find(e => e.k === 'lab' && (DATA.details[e.id]?.ids || []).some(i => (DATA.details[i]?.rows || []).some(r => r[0] === 'Creatinine')))?.id")
            js("id => openEvent(byId.get(id))", ev)
            page.wait_for_selector("#drawer.open #dbody td small.orig", timeout=10000)
            row = js("[...document.querySelectorAll('#dbody tr')].map(r => [...r.cells].map(c => c.textContent)).find(c => c[0].replace('✎', '').trim() === 'Creatinine')")
            check("drawer: the result in µmol/L, its range converted, the reported value and range under it",
                  row and re.match(r"[\d.]+ µmol/LReported [\d.]+ mg/dL, ref 0\.7", row[1]) and re.match(r"61\.9–115", row[2]), row)
            page.set_viewport_size({"width": 375, "height": 800})
            check("…and the drawer fits 375 px", js("document.documentElement.scrollWidth <= 375 && document.getElementById('dbody').scrollWidth <= document.getElementById('dbody').clientWidth"),
                  js("document.getElementById('dbody').scrollWidth"))
            js("closeDrawer(); switchTab('overview')")
            check("…the Overview with converted tiles fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.goto(base + "/settings.html?profile=1#sec-units")
            page.wait_for_selector("#sec-units input[name=units-lab][value=si]", timeout=10000)
            check("the Settings page with the units fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.click("#sec-units button:text-is('Reset to defaults')")
            answer_ask(page)
            page.wait_for_function("document.querySelector('#sec-units .set-foot > .msg').textContent.includes('back to their defaults')", timeout=10000)
            check("…Reset to defaults shows units as reported again", js("document.querySelector('#sec-units input[name=units-lab]:checked').value") == "reported")
            # ---- visit summary (#48): pick the visit and sections, add a lab, type questions (kept as a draft),
            # preview, then download the PDF and read it back
            page.goto(base + "/")   # from the main page, after the settings checks above
            page.wait_for_function("typeof booted !== 'undefined' && booted", timeout=20000)
            menu = js("[...document.querySelectorAll('#userMenuList [role=menuitem]')].map(x => x.textContent)")
            check("your menu has one Sharing item, in place of Visit summary and Share links (#168)",
                  js("document.getElementById('sharingLink').getAttribute('href')") == "sharing.html" and "Sharing" in menu
                  and "Visit summary" not in menu and "Share links" not in menu, menu)
            was_profile = js("localStorage.getItem('ht-profile')")   # the Sharing page remembers the profile picked, as Settings does
            page.goto(base + "/summary.html?profile=2")   # the second profile: results from several imports, Ferritin typed in
            page.wait_for_url("**/sharing.html?profile=2#summary", timeout=10000)   # the old address opens Sharing → Visit summary
            page.wait_for_selector("#build:not([hidden])", timeout=10000)
            check("the old visit summary address opens Sharing → Visit summary, on that profile",
                  js("document.getElementById('profile').value") == "2" and js("document.querySelector('#shrNav [aria-current=page]').textContent") == "Visit summary")
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
            check("the visit summary and its preview fit 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
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
            # ---- the profile picker in the top bar (#170): on every page about a profile, each profile by name and
            # your role; the page follows it and keeps its section; the address's ?profile= and this browser follow it
            was_profile = js("localStorage.getItem('ht-profile')")
            both = ["My records (owner)", "Second person (owner)"]
            for path, ready in (("/", "typeof booted !== 'undefined' && booted"), ("/sources.html#portals", "!document.getElementById('intro').hidden"),
                                ("/sharing.html#people", "!!document.querySelector('#memberList tbody tr')"),
                                ("/settings.html#display", "!!document.querySelector('#sections > section')")):
                page.goto(base + path)
                page.wait_for_function(ready, timeout=20000)
                check(f"{path}: the profile picker is in the top bar, each profile by name and role, and the page has no Profile choice of its own",
                      js("document.querySelectorAll('#profile').length === 1 && !!document.querySelector('header.topbar #profilePick > select#profile') "
                         "&& !document.querySelector('main #profile, main label.set-profile')")
                      and page.get_by_role("combobox", name="Profile").is_visible()
                      and js("[...document.getElementById('profile').options].map(o => o.textContent)") == both,
                      js("[...document.querySelectorAll('#profile option')].map(o => o.textContent)"))
            page.focus("header .brand-link")
            page.keyboard.press("Tab")
            check("…reached from the keyboard right after the brand and the page's title", js("document.activeElement.id") == "profile", js("document.activeElement.id"))
            for path in ("/account.html", "/admin.html"):
                page.goto(base + path)
                page.wait_for_load_state("networkidle")
                check(f"{path}: no profile picker, the page isn't about a profile", js("!document.getElementById('profile') && !document.getElementById('profilePick')"))
            page.goto(base + "/sources.html?profile=2#portals")
            page.wait_for_function("!document.getElementById('intro').hidden && document.getElementById('introHint').textContent.includes('Second person')", timeout=10000)
            check("an address naming a profile (?profile=2) opens it, and this browser remembers it",
                  js("document.getElementById('profile').value") == "2" and js("localStorage.getItem('ht-profile')") == "2")
            page.select_option("#profile", "1")
            page.wait_for_function("document.getElementById('introHint').textContent.includes('My records')", timeout=10000)
            check("switching profile redraws the page for it, in the same section, and the address and this browser follow",
                  js("[location.search, location.hash, document.querySelector('#srcNav [aria-current=page]').textContent, localStorage.getItem('ht-profile')]")
                  == ["?profile=1", "#portals", "Portals and health plans", "1"],
                  js("[location.search, location.hash, localStorage.getItem('ht-profile')]"))
            page.goto(base + "/settings.html#display")
            page.wait_for_selector("#sections > section", state="attached", timeout=10000)
            page.select_option("#profile", "2")
            page.wait_for_function("PID === 2 && !!document.querySelector('#sections > section')", timeout=10000)
            check("…on Settings too, keeping its group", js("location.hash") == "#display" and js("localStorage.getItem('ht-profile')") == "2")
            page.goto(base + "/")
            page.wait_for_function("typeof booted !== 'undefined' && booted || !!document.querySelector('main .empty')", timeout=20000)
            check("…and the records page opens the profile chosen there", js("PROFILE.id") == 2 and js("document.getElementById('profile').value") == "2")
            page.select_option("#profile", "1")   # the records page loads again for it
            page.wait_for_function("typeof booted !== 'undefined' && booted && PROFILE.id === 1", timeout=20000)
            check("…switching there opens the other", js("document.getElementById('profile').value") == "1" and js("localStorage.getItem('ht-profile')") == "1")
            page.set_viewport_size({"width": 375, "height": 800})
            box = "(s => { const r = document.querySelector(s).getBoundingClientRect(); return [r.top, r.bottom, r.left, r.right]; })"
            check("at 375 px the records page fits, the picker a row of its own between the menus and the tabs",
                  js("document.documentElement.scrollWidth") <= 375 and js(box + "('#addMenu')")[1] <= js(box + "('#profilePick')")[0]
                  and js(box + "('#profilePick')")[1] <= js(box + "('header .tabs')")[0] + 1 and js(box + "('#profilePick')")[3] <= 375,
                  [js("document.documentElement.scrollWidth"), js(box + "('#addMenu')"), js(box + "('#profilePick')"), js(box + "('header .tabs')")])
            for path, ready in (("/sources.html#portals", "!document.getElementById('intro').hidden"), ("/sharing.html#people", "!!document.querySelector('#memberList tbody tr')"),
                                ("/settings.html#display", "!!document.querySelector('#sections > section')")):
                page.goto(base + path)
                page.wait_for_function(ready, timeout=20000)
                check(f"…{path} fits too, the picker under the page's title and the menus, whole",
                      js("document.documentElement.scrollWidth") <= 375 and page.is_visible("#profile")
                      and js(box + "('.page-title')")[1] <= js(box + "('#profilePick')")[0] and js(box + "('#profilePick')")[3] <= 375
                      and js(box + "('#profilePick')")[1] <= js(box + "('header.topbar')")[1],
                      [js("document.documentElement.scrollWidth"), js(box + "('.page-title')"), js(box + "('#profilePick')")])
            page.set_viewport_size({"width": 1400, "height": 900})
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
            page.goto(base + "/settings.html?profile=1#sec-milestones")
            page.wait_for_selector("#sec-milestones [data-key=milestones] .set-row:not(.set-head)", timeout=10000)
            check("Settings shows the Milestones section", "Milestones" in js("[...document.querySelectorAll('#sections > section h2')].map(h => h.textContent)"))
            ms = "[data-key=milestones]"
            page.click(f"{ms} button:text-is('Add a row')")
            page.fill(f"{ms} .set-row:last-child input[data-k=date]", "2026-08-01")
            page.fill(f"{ms} .set-row:last-child input[data-k=label]", "Browser check milestone")
            page.check(f"{ms} .set-row:last-child input[data-k=primary]")
            check("…one primary at a time", js(f"document.querySelectorAll('{ms} input[data-k=primary]:checked').length") == 1)
            page.click("#sec-milestones .set-foot button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-milestones .set-foot > .msg').textContent.startsWith('Saved')", timeout=10000)
            rules = "[data-key=visitWorkouts]"
            page.click(f"{rules} button:text-is('Add a row')")
            page.fill(f"{rules} .set-row:last-child input[data-k=match]", "(a+)+$")
            page.fill(f"{rules} .set-row:last-child input[data-k=name]", "Browser check rule")
            page.fill(f"{rules} .set-row:last-child input[data-k=minutes]", "40")
            page.check(f"{rules} .set-row:last-child input[data-k=regex]")
            n0 = len(problems)
            page.click("#sec-health .set-foot button:text-is('Save')")
            page.wait_for_function(f"document.querySelector('{rules} .msg').textContent.includes('very slow')", timeout=10000)
            settle(200)
            refused = [p for p in problems[n0:] if ("HTTP 400" in p and p.endswith("/settings/visitWorkouts")) or "status of 400" in p]
            problems[n0:] = [p for p in problems[n0:] if p not in refused]   # this refusal is the point, not a fault
            check("a catastrophic regular expression is refused before saving",
                  "very slow" in js(f"document.querySelector('{rules} .msg').textContent") and len(refused) == 2, refused)
            page.fill(f"{rules} .set-row:last-child input[data-k=match]", want)
            page.uncheck(f"{rules} .set-row:last-child input[data-k=regex]")
            page.click("#sec-health .set-foot button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-health .set-foot > .msg').textContent.startsWith('Saved')", timeout=10000)
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
            page.click("#presets button:text-is('Since browser check milestone')")
            settle()
            check("…and is a “since” period in the records bar, on Body and activity", js("isoDay(hRange()[0])") == "2026-08-01",
                  js("[state.preset, new Date(hRange()[0]).toISOString()]"))
            check("…the primary one, the Overview counts from", js("primaryMs().label") == "Browser check milestone")
            page.click("#presets button:text-is('All')")
            settle()
            counted = js("H.ex.filter(x => x.pt && x.rule === 'Browser check rule').length")
            check("the Health tab counts visits matching the rule", counted == days and days > 0, f"{counted} of {days} days ({want})")
            check("…and says so", f"{days} browser check rule appointment" in js("document.getElementById('view-health').innerText"))
            page.goto(base + "/settings.html?profile=1#sec-milestones")
            page.wait_for_selector("#sec-milestones", timeout=10000)
            for sec in ("milestones", "health"):
                page.click(f"#sec-{sec} button:text-is('Reset to defaults')")
                answer_ask(page)
                page.wait_for_function(f"document.querySelector('#sec-{sec} .set-foot > .msg').textContent.includes('back to their defaults')", timeout=10000)
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
            za.goto(base + "/settings.html?profile=1#sec-dates")
            za.wait_for_selector("#sec-dates select", timeout=10000)
            za.select_option("[data-key='dates.timeZone'] select", "America/Los_Angeles")
            za.select_option("[data-key='dates.format'] select", "dmy")
            za.click("#sec-dates .set-foot button:text-is('Save')")   # one Save for the section's two changes (#174)
            za.wait_for_function("document.querySelector('#sec-dates .set-foot > .msg').textContent == 'Saved “Time zone” and “Date format”.'", timeout=10000)
            check("…changed on the Settings page, just for that person", mine(za)["dates.timeZone"] == "America/Los_Angeles"
                  and mine(zb)["dates.timeZone"] == "America/Chicago", (mine(za), mine(zb)))
            zb.goto(base + "/settings.html?profile=1#dashboard")   # #174: a viewer of the profile
            zb.wait_for_selector("#sec-dates .set-foot", state="attached", timeout=10000)
            check("a viewer's Settings: the profile's groups say they're shown as they are, with no Save, while their own settings have one",
                  zb.evaluate("document.getElementById('introHint').textContent").startswith("This profile: you can view it")
                  and zb.evaluate("!document.querySelector('#sec-overview .set-foot, #sec-names .set-foot') && !!document.querySelector('#sec-units .set-foot')"),
                  zb.evaluate("document.getElementById('introHint').textContent"))
            # #169: with no profile at all, Settings still has their own Display settings, says how to get a profile,
            # and saves; an old address opened by a link within the page goes on to its new home too
            sdb.execute("DELETE FROM memberships WHERE user_id=? AND profile_id=1", (uid2,))
            zb.goto(base + "/settings.html")
            zb.wait_for_selector("#sec-dates .set-foot", state="attached", timeout=10000)
            check("no profile: Settings shows Display (their own), says how to get a profile, and no profile choice",
                  zb.evaluate("[...document.querySelectorAll('#setNav a')].map(a => a.dataset.group).join()") == "display"
                  and zb.evaluate("[...document.querySelectorAll('#sections > section')].map(s => s.id).join()") == "sec-appearance,sec-dates,sec-units"
                  and zb.is_visible("#noProfiles") and zb.locator("#noProfiles a[href='sharing.html#profile']").count() == 1
                  and zb.is_hidden("#profile") and zb.evaluate("document.getElementById('groupTitle').textContent") == "Display",
                  zb.evaluate("[...document.querySelectorAll('#sections > section')].map(s => s.id).join()"))
            zb.select_option("[data-key='dates.format'] select", "dmy")
            zb.click("#sec-dates .set-foot button:text-is('Save')")
            zb.wait_for_function("document.querySelector('#sec-dates .set-foot > .msg').textContent == 'Saved “Date format”.'", timeout=10000)
            check("…and saves them", mine(zb)["dates.format"] == "dmy", mine(zb))
            zb.evaluate("location.hash = '#watch'")
            zb.wait_for_url("**/sources.html#phone", timeout=10000)
            check("…an old Settings address followed within the page opens its new home (#watch → Sources)", True)
            sdb.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, 1, 'viewer')", (uid2,))
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
            check("a viewer gets no Journal entry under Add, and no journal card while no one has written in it (#173)",
                  zb.evaluate("document.getElementById('journalBtn').hidden && !document.getElementById('journalHome')"))
            check("…and the header", " built 20" in zb.evaluate("document.getElementById('meta').textContent"), zb.evaluate("document.getElementById('meta').textContent"))
            check("…a journal entry's time is theirs too: 11:30 pm in Auckland, not in this browser's Chicago",
                  zb.evaluate("jTime('2026-03-10T23:30') === Date.parse('2026-03-10T10:30:00Z') && isoDay(jTime('2026-03-10')) === '2026-03-10'"),
                  zb.evaluate("new Date(jTime('2026-03-10T23:30')).toISOString()"))
            zb.goto(base + "/sharing.html?profile=1#summary")
            zb.wait_for_selector("#build:not([hidden]) #since option", state="attached", timeout=10000)
            since_opt = zb.evaluate("document.querySelector('#since option').textContent")
            check("…the visit summary writes dates in their format", bool(__import__("re").search(r"\(.*, \d{4}-\d{2}-\d{2}\)", since_opt)), since_opt)
            # a viewer's Sharing page (#168): who has access, the visit summary and their own calendar feed, and no
            # owner's controls; an address for an owner's group shows People instead, saying why
            zb.wait_for_selector("#memberList tbody tr", state="attached", timeout=10000)
            groups = zb.evaluate("[...document.querySelectorAll('#shrNav a')].map(a => a.textContent)")
            check("a viewer's Sharing page: People, Calendar feed, Visit summary and Profile only", groups == ["People", "Calendar feed", "Visit summary", "Profile"], groups)
            zb.click("#shrNav a[data-group=people]")
            people = zb.inner_text("#people")
            check("…who has access and as what, with Leave for themselves and no role menus, Remove or Share",
                  USER in people and "owner" in people and USER2 in people and zb.evaluate("!document.querySelector('#people select, #people form')")
                  and zb.evaluate("[...document.querySelectorAll('#people button')].map(b => b.textContent).join()") == "Leave"
                  and f"ask {USER}" in people, people[:300])
            zb.click("#shrNav a[data-group=calendar]")
            zb.wait_for_selector("#calendar:not([hidden]) #calNew:not([hidden])", timeout=10000)
            check("…their own calendar feed can be made, with no list of everyone's", zb.evaluate("document.getElementById('calOthers').hidden"))
            zb.click("#shrNav a[data-group=profile]")
            check("…Profile: no Rename or Delete, only New profile", zb.evaluate("!document.querySelector('#profileCard button, #profileCard input')")
                  and zb.is_visible("#profNew"), zb.inner_text("#profileCard"))
            zb.goto(base + "/sharing.html?profile=1#links")
            zb.wait_for_selector("#memberList tbody tr", timeout=10000)
            check("…an owner's group in the address shows People, saying share links are for owners",
                  zb.evaluate("document.querySelector('#shrNav [aria-current=page]').textContent") == "People" and "owners" in zb.inner_text("#pageMsg")
                  and zb.evaluate("location.hash") == "#people" and zb.evaluate("document.getElementById('links').hidden && document.getElementById('linksBody').children.length === 0"))
            check("…with one profile, the top bar names it and their role, as a plain label (#170)",
                  zb.evaluate("document.getElementById('profile').hidden && !document.getElementById('profileOne').hidden")
                  and zb.evaluate("document.getElementById('profileOne').textContent") == "Profile: My records view only" and zb.is_visible("#profileOne .profile-role"),
                  zb.evaluate("document.getElementById('profileOne').textContent"))
            zb.goto(base + "/import.html")
            zb.wait_for_selector("#importer", state="attached", timeout=10000)
            zb.evaluate("datesReady().then(() => true)")   # the zone read from the server, not only from the last visit
            check("…the import pages read them too", zb.evaluate("DATES.zone") == "Pacific/Auckland" and zb.evaluate("zToday()") == zb.evaluate(
                "new Intl.DateTimeFormat('en-CA', {timeZone: 'Pacific/Auckland'}).format(new Date())"), zb.evaluate("DATES.zone"))
            # Sources for someone who can only view the profile (#166): what reaches it and its history, nothing to add
            zb.wait_for_selector("#viewOnly", state="visible", timeout=10000)
            shown_src = "['importer', 'viewOnly', 'watch'].filter(id => getComputedStyle(document.getElementById(id)).display !== 'none')"
            check("Sources, view only: a note in place of Upload files", zb.evaluate(shown_src) == ["viewOnly"]
                  and "you can view this profile" in zb.inner_text("#introHint"), (zb.evaluate(shown_src), zb.inner_text("#introHint")))
            zb.click("#srcNav a[data-group=phone]")
            check("…and of Phone and watch, which only owners and editors set up", zb.evaluate(shown_src) == ["viewOnly"], zb.evaluate(shown_src))
            zb.click("#srcNav a[data-group=overview]")
            zb.wait_for_selector("#srcList h3", timeout=10000)
            ov = zb.inner_text("#srcOverview")
            check("…the Overview lists what reached the profile, with no phone and nothing to add",
                  "Setup build" in ov and "Phone app" not in ov and "Connect a portal" not in ov, ov[:300])
            zb.click("#srcNav a[data-group=history]")
            zb.wait_for_selector("#historyList li[data-import]", timeout=10000)
            check("…and the import history, with no Undo", zb.evaluate("location.hash") == "#history"
                  and zb.evaluate("!document.querySelector('#historyList button.undo')"))
            # someone who isn't an admin (#167): no Admin in their menu, the Admin page (and its old addresses) says it isn't
            # for them, and the everyday pages name no server setting, file or command, only what they can do
            sdb.execute("UPDATE memberships SET role='editor' WHERE user_id=? AND profile_id=1", (uid2,))   # sees the watch card
            jargon = re.compile(r"PUBLIC_ORIGINS|WATCH_DIR|WATCH_UPLOAD_URL|watch_secret|build_directory|connected-sources-oauth|sources\.json|\.env\b|docker compose")
            zb.goto(base + "/account.html#admin")
            zb.wait_for_url("**/admin.html", timeout=10000)
            zb.wait_for_selector("#notAdmin:not([hidden])", timeout=10000)
            shown_parts = "[...document.querySelectorAll('main > *')].filter(c => !c.hidden).map(c => c.id).join()"
            check("Admin: its old address opens it, and someone who isn't an admin gets only a page saying it isn't for them",
                  zb.evaluate(shown_parts) == "notAdmin" and not zb.evaluate("!!document.getElementById('admNav')"), zb.evaluate(shown_parts))
            zb.wait_for_load_state("networkidle")
            check("…and their menu has no Admin item", zb.evaluate("document.getElementById('adminLink').hidden"))
            said = {}
            for path, ready in (("settings.html?profile=1#watch", "#watch #watchRotate"), ("connect.html", "#noSources:not([hidden])"),
                                ("providers.html", "#srcNote:not([hidden])")):
                zb.goto(base + "/" + path)
                zb.wait_for_selector(ready, timeout=10000)
                said[path] = zb.inner_text("main")
            check("…the everyday pages name no server setting, file or command for them", not any(jargon.search(t) for t in said.values()),
                  {k: jargon.findall(t) for k, t in said.items()})
            check("…and say to ask their admin where something isn't set up",
                  "ask them" in said["connect.html"] and "Ask your admin" in said["providers.html"] and "original receiver secret" in said["settings.html?profile=1#watch"],
                  {k: t[:200] for k, t in said.items()})
            # ---- the getting-started guide by role (#199): someone given another person's empty profile gets none of
            # its owner's steps. As a viewer, the records page says its owners and editors bring records in and links
            # to who has access, and the guide has no steps; as an editor, Get started opens the guide at the first
            # records, with no naming the profile. Neither is sent to the guide by itself.
            from server import profiles as sprof
            pid3 = sprof.create_profile(sdb, "Shared empty profile", owner_id=sdb.execute("SELECT id FROM users WHERE username=?", (USER,)).fetchone()[0])
            sdb.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (uid2, pid3))
            zb.goto(base + f"/?profile={pid3}")
            zb.wait_for_selector("main .empty-state", timeout=10000)
            nexts = "[...document.querySelectorAll('main .empty-next')].map(a => [a.textContent, a.getAttribute('href')])"
            check("guide by role: a viewer of an empty profile is told who brings records in, with one link to who has access",
                  zb.evaluate("location.pathname") == "/" and "its owners and editors bring them in" in zb.inner_text("main")
                  and zb.evaluate(nexts) == [["See who has access", f"sharing.html?profile={pid3}#people"]], zb.evaluate(nexts))
            zb.goto(base + f"/guide.html?profile={pid3}")
            zb.wait_for_selector("#step-view:not([hidden])", timeout=10000)
            check("…and the guide gives them no steps, only the Overview", zb.evaluate(
                  "[...document.querySelectorAll('.guide-step')].filter(s => !s.hidden).map(s => s.id).join() + '|' + document.getElementById('guideSteps').hidden + '|' + document.getElementById('guideSkip').hidden")
                  == "step-view|true|true" and "owners and editors" in zb.inner_text("#step-view"), zb.inner_text("main")[:300])
            sdb.execute("UPDATE memberships SET role='editor' WHERE user_id=? AND profile_id=?", (uid2, pid3))
            zb.goto(base + f"/?profile={pid3}")
            zb.wait_for_selector("main .empty-state", timeout=10000)
            check("…an editor gets Get started", zb.evaluate(nexts) == [["Get started", f"guide.html?profile={pid3}"]], zb.evaluate(nexts))
            zb.click("main .empty-next")
            zb.wait_for_selector("#step-records:not([hidden])", timeout=10000)
            check("…which opens the guide at the first records, with no step naming the profile (its owners' to do)",
                  zb.evaluate("[...document.querySelectorAll('#guideSteps li')].map(li => li.textContent.replace(/^\\d/, ''))") == ["First records", "Phone (optional)", "Overview"]
                  and zb.evaluate("location.hash") == "#records", zb.evaluate("[...document.querySelectorAll('#guideSteps li')].map(li => li.textContent)"))
            sprof.delete_profile(sdb, pid3)
            za.evaluate("""v => Promise.all(Object.entries(v).map(([k, x]) =>
                api('settings/user/' + k, {method: 'PUT', body: JSON.stringify({value: x})})))""", before)
            for c in zctx.values():
                c.close()
            sdb.execute("DELETE FROM users WHERE id=?", (uid2,))
            sdb.close()

            # ---- a watch receiver secret per profile (#46): made on Sources → Phone and watch (#166), shown once, then masked
            page.goto(base + "/sources.html?profile=1#phone")
            page.wait_for_selector("#watch #watchRotate", timeout=10000)
            check("watch card: the server's original secret is still this profile's", "original receiver secret" in page.inner_text("#watch"),
                  page.inner_text("#watch")[:300])
            check("…saying when the phone last sent something", page.inner_text("#watchLast") != "", page.inner_text("#watchLast"))
            page.click("#watchRotate")
            answer_ask(page)
            page.wait_for_selector("#watchSecret", timeout=10000)
            made = js("document.getElementById('watchSecret').textContent")
            check("…makes a secret and shows it once, with a copy button", len(made) >= 40 and js("!!document.querySelector('#watch button[aria-label=\"Copy the secret\"]')"), made)
            check("…with no stray “null” in the card (#175)", not re.search(r"\bnull\b", page.inner_text("#watch")), page.inner_text("#watch")[:300])
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("…and the card fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            # iPhone (#148): Health Auto Export's setup steps beside Life Dashboard's: address, header, data, how often
            hae_txt = page.inner_text("#watchHae")
            check("watch card: Health Auto Export's steps (address, header, data, how often, workouts)",
                  all(w in hae_txt for w in ("Health Auto Export", "REST API", ":8731/ingest", "Authorization", "Bearer " + made,
                                             "heart rate variability", "sleep analysis", "Since last sync", "Workouts"))
                  and "Life Dashboard Companion" in page.inner_text("#watch") and len(js("[...document.querySelectorAll('#watchHae li')]")) >= 6,
                  hae_txt[:400])
            check("…with the header value to copy while the secret is shown",
                  js("!!document.querySelector('#watchHae button[aria-label=\"Copy the header value\"]')"))
            check("…and the steps fit 375 px", js("(() => { const b = document.getElementById('watchHae').getBoundingClientRect(); "
                                                  "return b.width > 0 && b.right <= 375 && [...document.querySelectorAll('#watchHae code, #watchHae li')]"
                                                  ".every(e => e.getBoundingClientRect().right <= 375.5); })()"),
                  js("document.getElementById('watchHae').getBoundingClientRect().right"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.reload()
            page.wait_for_selector("#watch .w-secret", timeout=10000)
            masked = js("document.querySelector('#watch .w-secret').textContent")
            check("…then only its last characters", masked.endswith(made[-4:]) and made not in page.content(), masked)
            check("…and the iPhone steps name the secret without showing it",
                  "Bearer <the secret>" in page.inner_text("#watchHae") and not js("!!document.querySelector('#watchHae button')"))
            # ---- reference ranges from settings (#44): a profile whose results come without a range; its own ranges,
            # set on the Settings page, colour those results on Trends, the heatmap, the Overview and the visit
            # summary, labelled "range from settings", while a report's own range and flag stay; the page's rules
            # (ranges.js) give the same answers as the server's (server/ranges.py); fits 375 px
            from server import db as dbm, profiles as prof_r, ranges as rng, records as rec_r
            was_profile = js("localStorage.getItem('ht-profile')")
            days = [f"2025-{m:02d}-15" for m in range(6, 13)] + [f"2026-{m:02d}-{d_:02d}" for m in range(1, 11) for d_ in (5, 20)]
            vals = [60, 90, 120, 250, 180, 160, 140, 130, 170, 210, 95, 12, 40, 80, 110, 140, 145, 160, 175, 120, 100, 90, 85, 70, 65, 300, 400]
            fe = [[f"{d_}T08:00:00", v, None, None, "", str(v)] for d_, v in zip(days, vals)]
            fe[3] = [fe[3][0], 250, 30, 400, "", "250"]   # its own report's range: in range by it, though above the setting's
            fe[11] = [fe[11][0], 12, None, None, "L", "12"]   # flagged by its own lab, with no range: the flag stays
            ug = [["2026-02-05T08:00:00", 500, None, None, "", "500"], ["2026-06-05T08:00:00", 90, None, None, "", "90"]]
            db = dbm.connect()
            owner = db.execute("SELECT id FROM users WHERE username=?", (USER,)).fetchone()["id"]
            rpid = prof_r.create_profile(db, "Ranges (fictional)", owner_id=owner)
            with dbm.transaction(db):
                rec_r.store(db, rpid, {"built": "2026-11-15", "source": "fictional",
                                       "events": [{"id": i + 1, "t": f"{d_}T08:00:00", "k": "lab", "title": "Lab results"} for i, d_ in enumerate(days)],
                                       "details": {}, "costs": [], "upcoming": [],
                                       "labs": [{"name": "Ferritin", "unit": "ng/mL", "panel": "Iron", "n": len(fe), "txt": [], "pts": fe},
                                                {"name": "Ferritin", "unit": "ug/L", "panel": "Iron", "n": 2, "txt": [], "pts": ug}]},
                            "upload", fmt="csv")
            db.close()
            page.goto(base + f"/settings.html?profile={rpid}#sec-ranges")
            page.wait_for_selector("#sec-ranges [data-key='ranges.overrides'] .set-sugg .chip", timeout=10000)
            chips = js("[...document.querySelectorAll('#sec-ranges .set-sugg .chip')].map(b => b.textContent)")
            check("ranges: Settings suggests the labs whose results have no range, by unit",
                  len(chips) == 2 and chips[0] == f"+ Ferritin (ng/mL) · {len(fe) - 2}", chips)
            page.fill("#sec-ranges [data-key='ranges.birthDate'] input[type=date]", "1990-03-01")
            page.check("#sec-ranges [data-key='ranges.sex'] input[value=female]")
            page.click("#sec-ranges .set-foot button:text-is('Save')")
            page.wait_for_function("document.querySelector('#sec-ranges .set-foot > .msg').textContent.startsWith('Saved “Birth date” and “Sex')", timeout=10000)
            ov = "#sec-ranges [data-key='ranges.overrides']"
            page.wait_for_selector(f"{ov} .set-sugg .chip", timeout=10000)
            page.locator(f"{ov} .set-sugg .chip").first.click()
            r1 = page.locator(f"{ov} .set-row:not(.set-head)").nth(0)
            check("…a suggestion adds a row with its lab and unit", r1.locator("[data-k=lab]").input_value() == "Ferritin"
                  and r1.locator("[data-k=unit]").input_value() == "ng/mL")
            r1.locator("[data-k=low]").fill("200")
            r1.locator("[data-k=high]").fill("20")
            n_problems = len(problems)
            page.locator("#sec-ranges .set-foot button:text-is('Save')").click()
            page.wait_for_function(f"document.querySelector(\"{ov} .msg\").textContent.includes('low is above the high')", timeout=10000)
            check("…a low above the high is refused, in words, beside the setting and its section's Save",
                  "isn't saved" in js("document.querySelector('#sec-ranges .set-foot > .msg').textContent")
                  and js("document.getElementById('sec-ranges').hasAttribute('data-dirty')"), js("document.querySelector('#sec-ranges .set-foot > .msg').textContent"))
            settle(200)
            del problems[n_problems:]   # the refusal's 400 was asked for
            r1.locator("[data-k=low]").fill("20")
            r1.locator("[data-k=high]").fill("200")
            page.locator(f"{ov} button:text-is('Add a range')").click()
            r2 = page.locator(f"{ov} .set-row:not(.set-head)").nth(1)
            r2.locator("[data-k=lab]").fill("Ferritin")
            r2.locator("[data-k=unit]").fill("ng/mL")
            r2.locator("[data-k=sex]").select_option("female")
            r2.locator("[data-k=ageFrom]").fill("36")
            r2.locator("[data-k=low]").fill("15")
            r2.locator("[data-k=high]").fill("150")
            pv = js("document.getElementById('rangesPreview').textContent")
            check("…the preview says how many results the rows fit", pv.startswith(f"{len(fe)} results here have no range") and f"fit {len(fe) - 2}" in pv, pv)
            page.set_viewport_size({"width": 375, "height": 800})
            settle(200)
            check("…and the ranges editor fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.locator("#sec-ranges .set-foot button:text-is('Save')").click()
            page.wait_for_function("document.querySelector('#sec-ranges .set-foot > .msg').textContent.startsWith('Saved “Ranges')", timeout=10000)
            page.goto(base + "/")
            page.wait_for_function("typeof booted !== 'undefined' && booted && PROFILE.name === 'Ranges (fictional)'", timeout=20000)
            got = js("SERIES.filter(s => !s.watch).flatMap(s => s.pts.map(p => [s.name, s.unit, p[0], p[2], p[3], p[4] || '', p.rs || '']))")
            cfg = js("CFG.ranges")
            want = []
            for unit, pts in (("ng/mL", fe), ("ug/L", ug)):
                for q in pts:
                    hit = None if rng.has_own(q[2], q[3], q[4]) else rng.find(cfg, "Ferritin", unit, q[0])
                    want.append(["Ferritin", unit, q[0], hit["low"] if hit else q[2], hit["high"] if hit else q[3],
                                 rng.flag_for(q[1], hit) if hit else q[4], "settings" if hit else ""])
            check("…the page applies them as the server does (ranges.js and server/ranges.py agree)", sorted(got) == sorted(want),
                  [g for g in got if g not in want][:3])
            check("…by the age on each result's day: the female 36+ row from 2026-03-01, the row for anyone before",
                  [g[4] for g in got if g[1] == "ng/mL" and g[2][:10] in ("2026-02-20", "2026-03-20")] == [200, 150])
            check("…never over a report's own range or flag, nor in another unit",
                  [g[3:] for g in got if g[2].startswith(days[3])][0] == [30, 400, "", ""] and [g for g in got if g[2].startswith(days[11])][0][5:] == ["L", ""]
                  and all(g[6] == "" for g in got if g[1] == "ug/L"))
            js("state.sel = new Set(['Ferritin']); applyPreset('all'); switchTab('labs'); renderLabs()")
            settle(400)
            card = "[...document.querySelectorAll('#charts .chart')].find(c => c.querySelector('.unit').textContent.startsWith('ng/mL'))"
            check("Trends: the chart says where the range came from, and colours the results outside it",
                  "range from settings" in js(f"{card}.querySelector('.unit').textContent") and js(f"{card}.querySelectorAll('.pt.abn').length") > 0
                  and js(f"!!{card}.querySelector('.refband')"), js(f"{card}.querySelector('.unit').textContent"))
            js(f"{card}.querySelector('.hd .view-seg button:last-child').click()")   # its table: Chart | Table (#60)
            settle(200)
            table = js("document.querySelector('#charts .tablewrap').textContent")
            check("…its table labels each such range, and keeps the report's own", "(range from settings)" in table and "30 – 400" in table, table[:200])
            check("…and the caption says so", "range from settings" in page.inner_text("#view-labs .range-note")
                  and "reference ranges in your own lab reports" in page.inner_text("#view-labs .range-note"))
            js("state.tables.clear(); renderCharts()")
            for width in (1400, 375):
                page.set_viewport_size({"width": width, "height": 900 if width > 375 else 800})
                js("switchTab('labs', 'heatmap')")
                settle(300)
                check(f"heatmap ({width} px): the results coloured against the range from settings, and fits",
                      js("document.querySelectorAll('#hm rect.cell[style*=\"--div-hi\"], #hm rect.cell[style*=\"--div-lo\"]').length") > 0
                      and "range from settings" in page.inner_text("#view-labs .range-note") and js("document.documentElement.scrollWidth") <= width,
                      js("document.documentElement.scrollWidth"))
                js("switchTab('overview')")
                settle(300)
                check(f"Overview ({width} px): the flag says it's against a range from settings, and fits",
                      js("document.querySelector('#view-overview .tile .st')?.title") == "Against a range from settings"
                      and "(range from settings)" in page.inner_text("#view-overview .ov-row .r")
                      and "range from settings" in page.inner_text("#view-overview .range-note") and js("document.documentElement.scrollWidth") <= width,
                      js("document.querySelector('#view-overview .tile .st')?.title"))
                js("switchTab('labs', 'chart')")
                settle(300)
                check(f"Labs as charts ({width} px) fits", js("document.documentElement.scrollWidth") <= width, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.goto(base + f"/sharing.html?profile={rpid}#summary")
            page.wait_for_selector("#build:not([hidden])", timeout=10000)
            page.click("#previewBtn")
            page.wait_for_selector("#preview:not([hidden]) h3", timeout=10000)
            check("visit summary: a result without a range is judged against the range from settings, marked so",
                  "(settings)" in js("document.getElementById('preview').textContent")
                  and "range from settings" in js("document.getElementById('sumSettingsNote')?.textContent || ''"))
            db = dbm.connect()
            prof_r.delete_profile(db, rpid)
            db.close()
            js("v => v === null ? localStorage.removeItem('ht-profile') : localStorage.setItem('ht-profile', v)", was_profile)
            # ---- connected sources (#32, #91), against the fake SMART on FHIR server standing in for an Epic sandbox:
            # an admin sets the source up and tests it, the profile connects by signing in, the first pull is previewed
            # and saved, Sync now brings a new result, and when the patient ends access the page asks to reconnect
            from tests.fakefhir import FakeFHIR
            fake = FakeFHIR(patients=["robin-example"]).start()
            fake.signin_patient = "pat-1"   # who "signs in" at the fake's authorize page
            fake.register_client("sanguity-browser-check", [base + "/connect/return"])
            allowed.add(origin(fake.fhir_base))   # its sign-in page, which the browser visits
            page.goto(base + "/connect.html#admin")   # its old address: the setup is on the Admin page now (#167)
            page.wait_for_url("**/admin.html#admin", timeout=10000)
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
            page.goto(base + "/connect.html")   # its old address: Sources → Portals and health plans (#166)
            page.wait_for_url("**/sources.html#portals", timeout=10000)
            page.wait_for_selector("#adminNote:not([hidden])", timeout=10000)
            check("connected sources: no setup on the everyday page, and an admin is pointed to Admin (#167)",
                  js("!document.getElementById('srcForm') && document.querySelector('#adminNote a').getAttribute('href')") == "admin.html#apps")
            page.select_option("#profile", label="Second person (owner)")
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
            page.wait_for_url("**/sources.html?*#portals", timeout=20000)
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
                  and js("[...document.querySelectorAll('#dbody td')].filter(td => td.title === 'Hemoglobin').length") == 1,
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
            page.goto(base + "/sources.html#portals")
            page.select_option("#profile", label="Second person (owner)")
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
            page.click("#srcNav a[data-group=overview]")   # Sources' overview (#166): every way in, the one that stopped first
            page.wait_for_selector("#srcWarn .src-alert", timeout=10000)
            ov = page.inner_text("#srcOverview")
            check("the Sources overview warns that the source stopped, and lists it with when it last brought records",
                  "access ended" in page.inner_text("#srcWarn") and "Fake Epic sandbox" in ov and "Last brought records" in ov
                  and page.locator("#srcList .src-row.bad", has_text="Fake Epic sandbox").count() == 1, ov[:500])
            page.click("#srcWarn a")
            page.wait_for_function("location.hash.startsWith('#conn-') && getComputedStyle(document.getElementById('mine')).display !== 'none'", timeout=5000)
            check("…its link opens the connection under Portals and health plans",
                  js("document.querySelector('#srcNav a[aria-current=page]').dataset.group") == "portals")
            page.set_viewport_size({"width": 375, "height": 800})
            page.click("#srcNav a[data-group=overview]")
            settle(200)
            check("…and the overview fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            prev_profile = js("localStorage.getItem('ht-profile')")
            js(f"localStorage.setItem('ht-profile', '{conn_pid}')")   # the profile's main page warns too (#102)
            page.goto(base + "/")
            page.wait_for_selector("#syncWarn:not([hidden])", timeout=20000)
            warn = page.inner_text("#syncWarn")
            check("the profile's main page warns that the source stopped syncing, with a link to it",
                  "access ended" in warn and js("document.querySelector('#syncWarn a').getAttribute('href')").startswith(f"sources.html?profile={conn_pid}#conn-"), warn)
            page.set_viewport_size({"width": 375, "height": 800})
            check("…and the warning fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            js(f"api('profiles/{conn_pid}/settings/sync.attachTypes', {{method: 'DELETE'}})")
            js(f"localStorage.setItem('ht-profile', {json.dumps(prev_profile)} || '')")
            check("only the source's own host was contacted", fake.hosts() == {f"127.0.0.1:{fake.port}"}, fake.hosts())

            # ---- outside connections (#97, #59): the Admin page lists every host the install may contact, by purpose
            page.goto(base + "/settings.html#outside")   # its old address (#167)
            page.wait_for_url("**/admin.html#outside", timeout=10000)
            page.wait_for_function("document.getElementById('outsideBody').textContent.includes('May contact now')", timeout=10000)
            out = page.inner_text("#outside")
            check("Admin says plainly that nothing else is contacted", "Nothing else leaves this server" in out, out[:200])
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
            page.goto(base + "/admin.html#apps")
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
            page.goto(base + "/sources.html#portals")
            page.select_option("#profile", label="Second person (owner)")
            page.wait_for_selector("#sourceButtons button:has-text('Fake health plan')", timeout=10000)
            page.click("#sourceButtons button:has-text('Fake health plan')")   # what to pull (#99), then sign in
            page.wait_for_selector(".conn-choose", timeout=10000)
            page.locator(".conn-choose").get_by_role("button", name="Sign in at").click()
            page.wait_for_url("**/sources.html?*#portals", timeout=20000)
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
            js("applyPreset('all')")
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

            # ---- connecting a portal or health plan made easier (#165): Admin's walk-through per vendor (numbered steps,
            # the values to paste with Copy, the form as a step of its own, Test this connection last), a source saved as
            # waiting for the vendor's review, and what an everyday user sees for it on Portals and health plans (what's
            # coming, and to upload the portal's export meanwhile, no connect button and no setup words), at 1400 and
            # 375 px in light and dark; and the upload page's note on an iPhone's Health Records
            look_165 = r"""sel => {
              const rgb = c => { const x = document.createElement('canvas').getContext('2d'); x.fillStyle = c; x.fillRect(0, 0, 1, 1); return [...x.getImageData(0, 0, 1, 1).data]; };
              const bg = n => { for (; n; n = n.parentElement) { const c = rgb(getComputedStyle(n).backgroundColor); if (c[3] > 0) return c; } return [255, 255, 255, 255]; };
              const lum = c => { const v = c.slice(0, 3).map(x => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; }); return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
              const ratio = n => { const [a, b] = [lum(rgb(getComputedStyle(n).color)), lum(bg(n))].sort((p, q) => q - p); return Math.round((a + 0.05) / (b + 0.05) * 10) / 10; };
              const ns = [...document.querySelectorAll(sel)].filter(n => n.getClientRects().length);
              return ns.length ? Math.min(...ns.map(ratio)) : 0;
            }"""
            theme_before_165 = js("document.documentElement.dataset.theme || null")
            page.set_viewport_size({"width": 1400, "height": 900})
            page.goto(base + "/admin.html#apps")
            page.wait_for_function("!document.getElementById('admin').hidden && document.querySelector('#walk > li')", timeout=10000)
            walk = lambda: js("""(() => { const w = document.getElementById('walk'); return {
                n: w.children.length, steps: SETUP.guides[document.getElementById('fVendor').value].steps.length,
                copies: [...w.querySelectorAll('.walk-copy')].map(c => [c.querySelector('.walk-k').textContent, c.querySelector('code').textContent,
                                                                     !!c.querySelector('button')]),
                form: !!w.querySelector(':scope > li #srcFields #fClient'), last: w.lastElementChild.querySelector('.walk-t').textContent,
                test: !!w.lastElementChild.querySelector('#walkTest')}; })()""")
            per = {}
            for v in ("epic", "oracle", "insurer", "other"):
                page.select_option("#fVendor", v)
                per[v] = walk()
            check("Admin's walk-through (#165): every vendor's steps numbered, then the form as a step, then Test this connection",
                  all(w["n"] == w["steps"] + 2 and w["steps"] >= 4 and w["form"] and w["last"] == "Test this connection" and w["test"]
                      for w in per.values()), per)
            check("…each step with the values to paste and a Copy button: the return address for every vendor, the types Epic's "
                  "scopes read, an insurer's scopes, a sandbox's FHIR base",
                  all(any(c[0] == "Return address" and c[1] == base + "/connect/return" and c[2] for c in w["copies"]) for w in per.values())
                  and any(c[0] == "Types it reads" and "Observation" in c[1] for c in per["epic"]["copies"])
                  and any(c[0] == "Scopes" and "patient/ExplanationOfBenefit.read" in c[1] for c in per["insurer"]["copies"])
                  and any(c[0] == "Sandbox FHIR base" and "bluebutton" in c[1] for c in per["insurer"]["copies"]),
                  {k: w["copies"] for k, w in per.items()})
            page.select_option("#fVendor", "epic")
            ret = page.locator("#walk .walk-copy", has_text="Return address")
            ret.get_by_role("button", name="Copy the return address").click()
            page.wait_for_function("document.querySelector('#walk .walk-copy .msg').textContent !== ''", timeout=5000)
            said = ret.locator(".msg").inner_text()
            check("…Copy copies it, or selects it where the browser keeps the clipboard from the page", said in ("Copied.", "Selected: press Ctrl+C or ⌘C."), said)
            page.fill("#fScopes", "launch/patient patient/Patient.read patient/Observation.read")
            check("…the types to pick follow the scopes typed in", js("document.querySelector('#walk [data-paste=types]').textContent") == "Patient, Observation",
                  js("document.querySelector('#walk [data-paste=types]').textContent"))
            check("…and the last step can't test anything until the source is saved", js("document.getElementById('walkTest').disabled"))
            # saved while the vendor reviews the app: first refused without a client ID, then saved as waiting
            page.fill("#fName", "Lakeside Health (fictional)")
            page.fill("#fIssuer", "https://fhir.lakeside.example/api/FHIR/R4")
            page.fill("#fClient", "")
            page.click("#fSave")
            page.wait_for_function("document.getElementById('fMsg').textContent.includes('Waiting for the vendor')", timeout=10000)
            check("a source with no client ID yet is refused, saying to tick Waiting for the vendor's review",
                  "bad" in js("document.getElementById('fMsg').className"), page.inner_text("#fMsg"))
            problems[:] = [x for x in problems if not ("HTTP 400: " in x and "/api/connect/sources" in x)
                           and "status of 400" not in x]   # refused on purpose: that was the point
            page.check("#fWaiting")
            page.click("#fSave")
            page.wait_for_function("document.getElementById('sourceList').textContent.includes('Lakeside Health (fictional)') && "
                                   "document.getElementById('walkTest').textContent === 'Test Lakeside Health (fictional)'", timeout=10000)
            row = page.locator("#sourceList .prof", has_text="Lakeside Health (fictional)")
            check("…then saved as waiting: tagged in the list, the walk-through staying on it at its last step, ready to test",
                  "Waiting for review" in row.inner_text() and "people are told to upload" in row.inner_text()
                  and js("document.activeElement.id") == "walkTest" and not js("document.getElementById('walkTest').disabled")
                  and "waiting for the vendor's review" in page.inner_text("#fMsg"), [row.inner_text()[:300], page.inner_text("#fMsg")])
            wait_id = js("SETUP.sources.find(s => s.name === 'Lakeside Health (fictional)').id")
            for width in (1400, 375):
                page.set_viewport_size({"width": width, "height": 800 if width == 375 else 900})
                for theme in ("light", "dark"):
                    js("t => { document.documentElement.dataset.theme = t; }", theme)
                    settle(150)
                    bad = phone_fits(page, f"Admin walk-through, {theme}") if width == 375 else []
                    looks = {s: js(look_165, s) for s in ("#walk .walk-t", "#walk .walk-v", "#walk .walk-k", "#walk .walk-step > p",
                                                         "#sourceList .walk-tag", "label.walk-wait")}
                    wide = js("document.documentElement.scrollWidth")
                    check(f"…the walk-through at {width} px, {theme}: readable steps, values, tags and the waiting switch"
                          + (", nothing cut off, overlapping or scrolling sideways" if width == 375 else ""),
                          not bad and wide <= width and all(v >= 4.5 for v in looks.values()), [bad[:6], wide, looks])
            js("t => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }", theme_before_165)
            page.set_viewport_size({"width": 1400, "height": 900})
            # what an editor who isn't an admin sees for it (a second account, removed afterwards)
            from server import db as dbm, security
            sdb = dbm.connect()
            uid3 = sdb.execute("INSERT INTO users(username, pw_hash, is_admin, created) VALUES(?, ?, 0, 0)", ("lee", security.hash_password(PW2))).lastrowid
            sdb.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (uid3, conn_pid))
            lctx = browser.new_context(viewport={"width": 1400, "height": 900}, bypass_csp=True)
            lp = lctx.new_page()
            lp.on("pageerror", lambda e: problems.append(f"script error (lee): {e}"))
            lp.goto(base + "/login.html")
            lp.fill("#u", "lee")
            lp.fill("#p", PW2)
            lp.click("#b")
            lp.wait_for_function("typeof PROFILE !== 'undefined' && PROFILE", timeout=20000)
            lp.goto(base + f"/sources.html?profile={conn_pid}#portals")
            lp.wait_for_selector(f"#wait-{wait_id}", timeout=10000)
            note = lp.inner_text(f"#wait-{wait_id}")
            setup_words = re.compile(r"client ID|FHIR|scope|discovery|redirect|issuer|PUBLIC_ORIGINS|Admin|\.env|vendor's developer", re.I)
            check("Portals and health plans: a source waiting for review says it's coming and to upload the export meanwhile, "
                  "in place of a connect button",
                  "isn't ready to connect yet: it's waiting for Epic's review" in note and "upload them under Sources → Upload files" in note
                  and not lp.locator("#sourceButtons button", has_text="Lakeside").count()
                  and lp.locator(f"#wait-{wait_id} a", has_text="Upload files").get_attribute("href") == "#upload", note)
            check("…in plain words, with nothing about the server's setup for someone who isn't an admin (#167)",
                  not setup_words.search(note) and lp.is_hidden("#adminNote"), setup_words.findall(note))
            for width in (1400, 375):
                lp.set_viewport_size({"width": width, "height": 800 if width == 375 else 900})
                for theme in ("light", "dark"):
                    lp.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
                    lp.wait_for_timeout(150)
                    bad = phone_fits(lp, f"Portals waiting, {theme}") if width == 375 else []
                    looks = [lp.evaluate(look_165, s) for s in (f"#wait-{wait_id} p", f"#wait-{wait_id} .tag", f"#wait-{wait_id} a")]
                    check(f"…the waiting note at {width} px, {theme}: readable" + (", and it fits" if width == 375 else ""),
                          not bad and min(looks) >= 4.5 and lp.evaluate("document.documentElement.scrollWidth") <= width, [bad[:6], looks])
            lp.set_viewport_size({"width": 1400, "height": 900})
            lp.click(f"#wait-{wait_id} a")
            lp.wait_for_function("document.querySelector('main.src').dataset.group === 'upload'", timeout=5000)
            apple = lp.inner_text("#appleRecords")
            check("…its Upload files opens the upload page, which says an iPhone's Apple Health export holds its Health Records",
                  lp.evaluate("location.hash") == "#upload" and lp.is_visible("#appleRecords") and "Export All Health Data" in apple
                  and "Health Records" in apple and "FHIR" in apple, apple)
            lctx.close()
            sdb.execute("DELETE FROM users WHERE id=?", (uid3,))
            sdb.close()
            # the review done: no longer waiting, so not shown to people until it has a client ID and a test; then removed
            row = page.locator("#sourceList .prof", has_text="Lakeside Health (fictional)")
            row.get_by_role("button", name="Review done").click()
            page.wait_for_function("document.querySelector('#sourceList').textContent.includes('Paste the client ID')", timeout=10000)
            check("Review done needs the client ID first, and says so", "Waiting for review" in row.inner_text(), row.inner_text()[:200])
            problems[:] = [x for x in problems if not ("HTTP 400: " in x and "/api/connect/sources" in x)
                           and "status of 400" not in x]   # refused on purpose: that was the point
            page.evaluate(f"api('connect/sources/{wait_id}', {{method: 'DELETE'}}).then(r => r.status)")
            page.reload()
            page.wait_for_function("typeof SETUP !== 'undefined' && SETUP && !SETUP.sources.some(s => s.name === 'Lakeside Health (fictional)')", timeout=10000)

            # ---- every page at 375 px (#61): each records tab (Labs as charts and as a heatmap) with its filters open, an
            # event's drawer and the top bar's menus; every group of Sources (the profile with connected sources and
            # imports), Sharing, Settings, Account and Admin; and the sign-in page with no session; in light and dark:
            # nothing scrolls sideways, and no control is cut off at the edge or overlaps another (phone_fits). The
            # shared page (share.html) is checked the same way with the share links, under the real CSP.
            phone_known = ()   # problems owned by another issue, each with the issue's number beside it: none now
            theme_was = js("localStorage.getItem('ht-theme')")
            page.set_viewport_size({"width": 375, "height": 800})
            for theme in ("light", "dark"):
                js(f"localStorage.setItem('ht-theme', '{theme}')")
                page.goto(base + "/")
                page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && typeof booted !== 'undefined' && booted", timeout=20000)
                bad = []
                for t in ("overview", "health", "timeline", "heatmap", "labs", "costs"):
                    open_tab(t)
                    settle(250)
                    bad += phone_fits(page, t, phone_known)
                    if page.is_visible("#ftoggle"):
                        page.click("#ftoggle")
                        settle(150)
                        bad += phone_fits(page, t + " with its filters open", phone_known)
                        page.click("#ftoggle")
                    if t == "timeline":
                        page.click("#list .row >> nth=0")
                        page.wait_for_function("document.getElementById('drawer').classList.contains('open')", timeout=5000)
                        settle(250)
                        bad += phone_fits(page, "the drawer", phone_known)
                        page.keyboard.press("Escape")
                        settle(250)
                for m in ("#addMenuBtn", "#userMenuBtn"):
                    page.click(m)
                    bad += phone_fits(page, m + " open", phone_known)
                    page.keyboard.press("Escape")
                check(f"at 375 px, {theme}: every records tab, with its filters open, the drawer and the menus fit, no control cut off or overlapping (#61)",
                      js("document.documentElement.dataset.theme") == theme and not bad, bad[:8] or "")
                bad, seen = [], []
                for path in (f"sources.html?profile={conn_pid}", "sharing.html?profile=1", "settings.html?profile=1", "account.html", "admin.html"):
                    page.goto(base + "/" + path)
                    page.wait_for_selector(".set-nav a[data-group]", timeout=10000)
                    settle(300)
                    for g in js("[...document.querySelectorAll('.set-nav a[data-group]')].map(a => a.dataset.group)"):
                        page.click(f".set-nav a[data-group={g}]")
                        settle(150)
                        bad += phone_fits(page, f"{path.split('.')[0]} #{g}", phone_known)
                        seen.append(f"{path.split('.')[0]}#{g}")
                ctx = browser.new_context(viewport={"width": 375, "height": 800}, color_scheme=theme)   # no session: the sign-in page
                lp = ctx.new_page()
                lp.goto(base + "/login.html")
                lp.wait_for_selector("#u", timeout=10000)
                bad += phone_fits(lp, "sign-in", phone_known)
                ctx.close()
                check(f"at 375 px, {theme}: every group of Sources, Sharing, Settings, Account and Admin, and the sign-in page, fit, "
                      "no control cut off or overlapping (#61)", len(seen) >= 25 and not bad, bad[:8] or f"{len(seen)} groups")
            js(f"localStorage.setItem('ht-theme', {json.dumps(theme_was)})" if theme_was else "localStorage.removeItem('ht-theme')")
            page.set_viewport_size({"width": 1400, "height": 900})

            # ---- WCAG 2.2 AA (#202, docs/accessibility.md): axe-core's WCAG 2.0, 2.1 and 2.2 A and AA rules (axe_check) on
            # every page and its main states, in light and dark: each records tab (Labs as charts and as a heatmap, each also
            # as its table), an event's drawer for several kinds, the app's own dialog (ask(), with a box to type in), the
            # More… period menu, a kind's ⋯ menu and the top bar's two menus; every group of Sources, Sharing, Settings,
            # Account and Admin; the guide and the provider finder; the sign-in page with no session. A serious or critical
            # finding fails; moderate and minor ones are listed under a passing check. (The shared page, setup and a fresh
            # install's empty tabs are checked where they're opened: share links and first run.) Then the keyboard: Tab and
            # Shift+Tab through each records tab and page at 375 px, every stop showing focus and none under a sticky bar;
            # every page at 320 px (400% zoom) and with WCAG's text spacing fitting; and in forced colours, the filled
            # buttons keep an edge.
            t_axe, axe_bad, axe_other, axe_seen = time.time(), [], [], []
            def axe_at(pg, label):
                bad_, other_ = axe_check(pg, label)
                axe_bad.extend(bad_); axe_other.extend(other_); axe_seen.append(label)
            theme_was = js("localStorage.getItem('ht-theme')")
            for theme in ("light", "dark"):
                js(f"localStorage.setItem('ht-theme', '{theme}')")
                page.goto(base + "/")
                page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && typeof booted !== 'undefined' && booted", timeout=20000)
                for t in ("overview", "health", "timeline", "heatmap", "labs", "costs"):
                    open_tab(t)
                    settle(300)
                    axe_at(page, f"{theme} {t}")
                    if t == "heatmap" or (t == "labs" and js("document.querySelectorAll('#charts .chart .view-seg').length")):   # as its table (#60)
                        seg = "#hmHd .view-seg button" if t == "heatmap" else "#charts .chart .view-seg button"
                        page.click(f"{seg} >> nth=1")
                        settle(200)
                        axe_at(page, f"{theme} {t} as a table")
                        page.click(f"{seg} >> nth=0")
                        settle(200)
                for k in ("lab", "claim", "med", "letter", "visit"):   # an event's drawer
                    if js("k => !!EV.find(e => e.k === k && !e.hidden)", k):
                        js("k => openEvent(EV.find(e => e.k === k && !e.hidden))", k)
                        settle(250)
                        axe_at(page, f"{theme} the drawer, {k}")
                        page.keyboard.press("Escape")
                        settle(150)
                open_tab("timeline")
                settle(200)
                page.click("#periodMoreBtn")
                axe_at(page, f"{theme} the More… period menu")
                page.keyboard.press("Escape")
                page.click("#chips .kind-more >> nth=0")
                axe_at(page, f"{theme} a kind's ⋯ menu")
                page.keyboard.press("Escape")
                for m in ("#addMenuBtn", "#userMenuBtn"):
                    page.click(m)
                    axe_at(page, f"{theme} {m} open")
                    page.keyboard.press("Escape")
                js("""() => { const i = el('input', {type: 'text'});
                    ask({title: 'Rename this view? (browser check)', message: ['A check of the dialog (#202).', el('label', {}, 'New name', i)], action: 'Rename', field: i}); }""")
                page.wait_for_selector("dialog.sg-ask[open]", timeout=5000)
                axe_at(page, f"{theme} the app's own dialog")
                answer_ask(page, yes=False)
                for path in (f"sources.html?profile={conn_pid}", "sharing.html?profile=1", "settings.html?profile=1", "account.html", "admin.html"):
                    page.goto(base + "/" + path)
                    page.wait_for_selector(".set-nav a[data-group]", timeout=10000)
                    settle(300)
                    for g in js("[...document.querySelectorAll('.set-nav a[data-group]')].map(a => a.dataset.group)"):
                        page.click(f".set-nav a[data-group={g}]")
                        settle(150)
                        axe_at(page, f"{theme} {path.split('.')[0]}#{g}")
                for path, ready in (("guide.html?profile=1", "#step-name:not([hidden])"), ("providers.html?q=medicare", ".dir-hit")):
                    page.goto(base + "/" + path)
                    page.wait_for_selector(ready, timeout=10000)
                    settle(200)
                    axe_at(page, f"{theme} {path.split('?')[0]}")
                ctx = browser.new_context(viewport={"width": 1400, "height": 900}, color_scheme=theme)   # no session, the real CSP
                lp = ctx.new_page()
                lp.goto(base + "/login.html")
                lp.wait_for_selector("#u", timeout=10000)
                axe_at(lp, f"{theme} sign-in")
                ctx.close()
            js(f"localStorage.setItem('ht-theme', {json.dumps(theme_was)})" if theme_was else "localStorage.removeItem('ht-theme')")
            check(f"WCAG 2.2 AA, axe-core: no serious or critical finding on {len(axe_seen)} pages and states, in light and dark (#202)",
                  len(axe_seen) >= 90 and not axe_bad, axe_bad[:10] or f"{len(axe_seen)} checked in {time.time() - t_axe:.0f} s")
            check("…its moderate and minor findings, listed (none fail)", True, "; ".join(axe_other[:12]) or "none")
            # the keyboard at 375 px, where the top bar is tallest: Tab from the start and Shift+Tab from the end
            t_keys, kbad = time.time(), []
            page.set_viewport_size({"width": 375, "height": 800})
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && typeof booted !== 'undefined' && booted", timeout=20000)
            for t in ("overview", "timeline", "heatmap", "labs", "costs"):
                open_tab(t)
                settle(250)
                kbad += focus_walk(page, t) + focus_walk(page, t, back=True)
            for path, g in (("sources.html?profile=1", "manual"), ("settings.html?profile=1", "labs"), ("sharing.html?profile=1", "summary"), ("admin.html", "apps")):
                page.goto(base + "/" + path)
                page.wait_for_selector(".set-nav a[data-group]", timeout=10000)
                page.click(f".set-nav a[data-group={g}]")
                settle(200)
                kbad += focus_walk(page, f"{path.split('.')[0]}#{g}") + focus_walk(page, f"{path.split('.')[0]}#{g}", back=True)
            check("keyboard at 375 px: Tab and Shift+Tab through each records tab and a group of each page, every stop showing focus "
                  "and none hidden under the top bar or a sticky header (2.4.7, 2.4.11, #202)", not kbad, kbad[:8] or f"{time.time() - t_keys:.0f} s")
            # reflow at 320 px (1280 px at 400%), light; and WCAG's text spacing (1.4.12) at 375 px
            spacing = "* { line-height: 1.5 !important; letter-spacing: .12em !important; word-spacing: .16em !important } p { margin-bottom: 2em !important }"
            for width, spaced in ((320, False), (375, True)):
                page.set_viewport_size({"width": width, "height": 640 if width == 320 else 800})
                rbad = []
                page.goto(base + "/")
                page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0 && typeof booted !== 'undefined' && booted", timeout=20000)
                if spaced:
                    page.add_style_tag(content=spacing)
                for t in ("overview", "health", "timeline", "heatmap", "labs", "costs"):
                    open_tab(t)
                    settle(250)
                    rbad += phone_fits(page, t)
                for path in (f"sources.html?profile={conn_pid}", "sharing.html?profile=1", "settings.html?profile=1", "account.html", "admin.html"):
                    page.goto(base + "/" + path)
                    page.wait_for_selector(".set-nav a[data-group]", timeout=10000)
                    if spaced:
                        page.add_style_tag(content=spacing)
                    settle(250)
                    for g in js("[...document.querySelectorAll('.set-nav a[data-group]')].map(a => a.dataset.group)"):
                        page.click(f".set-nav a[data-group={g}]")
                        settle(100)
                        rbad += phone_fits(page, f"{path.split('.')[0]}#{g}")
                check(f"at {width} px{' with WCAG text spacing' if spaced else ' (400% zoom)'}: every records tab and every page's groups fit, "
                      "nothing scrolling sideways, no control cut off or overlapping (1.4.10, 1.4.12, #202)", not rbad, rbad[:8] or "")
            page.set_viewport_size({"width": 1400, "height": 900})
            # forced colours (Windows' high contrast): a filled button (Save, Sign in, a dialog's action) keeps an edge
            ctx = browser.new_context(viewport={"width": 1400, "height": 900}, forced_colors="active")
            lp = ctx.new_page()
            lp.goto(base + "/login.html")
            lp.wait_for_selector("#b", timeout=10000)
            edges = {"sign-in": lp.evaluate("(c => c.borderStyle !== 'none' || c.outlineStyle !== 'none' || c.backgroundColor !== getComputedStyle(document.querySelector('form')).backgroundColor)(getComputedStyle(document.getElementById('b')))")}
            ctx.close()
            page.emulate_media(forced_colors="active")
            page.goto(base + "/account.html")
            page.wait_for_selector(".btn.primary", timeout=10000)
            edges["account"] = js("[...document.querySelectorAll('.btn.primary, .btn.danger')].filter(b => b.checkVisibility()).every(b => { const c = getComputedStyle(b); return c.borderStyle !== 'none' && parseFloat(c.borderWidth) >= 1; })")
            page.emulate_media(forced_colors="none")
            check("in forced colours, the filled buttons keep an edge (#202)", all(edges.values()), edges)
            # accessible sign-in (3.3.8): a password manager can fill the fields, and pasting isn't stopped
            ctx = browser.new_context(viewport={"width": 1400, "height": 900})
            lp = ctx.new_page()
            lp.goto(base + "/login.html")
            lp.wait_for_selector("#u", timeout=10000)
            paste = """ids => ids.map(id => { const i = document.getElementById(id), ev = new ClipboardEvent('paste', {bubbles: true, cancelable: true});
                i.dispatchEvent(ev); return [id, i.getAttribute('autocomplete'), ev.defaultPrevented]; })"""
            got = lp.evaluate(paste, ["u", "p", "c"])
            ctx.close()
            check("sign-in: fields a password manager fills (username, current-password, one-time-code) and pasting allowed (3.3.8, #202)",
                  got == [["u", "username", False], ["p", "current-password", False], ["c", "one-time-code", False]], got)

            # ---- account page: password and devices; profiles and their sharing on the Sharing page (#168); the Admin
            # page: accounts (#167)
            page.goto(base + "/account.html")
            page.wait_for_function("document.querySelectorAll('#sessList tbody tr').length > 0", timeout=10000)
            check("account page lists devices, and nothing of the server's (#167) or of profiles (#168)",
                  js("!document.getElementById('tab-admin') && !document.getElementById('users') && !document.getElementById('labmaps') && !document.getElementById('secNotice')")
                  and js("!document.getElementById('acctTabs') && !document.getElementById('profiles') && !document.getElementById('profList')"))
            page.goto(base + "/account.html#users")   # the old Admin tab's addresses open the Admin page
            page.wait_for_url("**/admin.html#users", timeout=10000)
            page.wait_for_function("!document.getElementById('users').hidden && document.querySelectorAll('#userList tbody tr').length > 0", timeout=10000)
            check("the old Admin tab's address opens the Admin page, on Accounts", js(display, "users") != "none" and js(display, "labmaps") == "none")
            page.goto(base + "/account.html#labmaps")
            page.wait_for_url("**/admin.html#labmaps", timeout=10000)
            page.wait_for_function("document.querySelector('#labmapList tr[data-labmap]')", timeout=10000)
            lm = js("[...document.querySelectorAll('#labmapList tr[data-labmap]')].map(tr => tr.textContent)")
            check("Admin lists the lab name match, who made it and from what, with Change and Remove (#23)",
                  any("Hemoglobin Whole Bld" in t and "Hemoglobin" in t and "demo" in t and "CSV" in t and "Change" in t and "Remove" in t for t in lm)
                  and "only admins can change or remove" in page.inner_text("#labmaps") and js(display, "labmaps") != "none", lm)
            # the old Profiles tab's address opens Sharing → People (#168); a section link still lands on Account
            page.goto(base + "/account.html#profiles")
            page.wait_for_url("**/sharing.html#people", timeout=10000)
            page.wait_for_selector("#memberList tbody tr", timeout=10000)
            check("the old Profiles tab's address opens Sharing → People", js("document.querySelector('#shrNav [aria-current=page]').textContent") == "People")
            page.goto(base + "/account.html#twofa")
            page.wait_for_function("document.getElementById('tfHint').textContent.length > 0", timeout=10000)
            check("an old section link still opens Account, in its group", js(display, "twofa") != "none" and js("location.hash") == "#twofa"
                  and js("document.querySelector('#acctNav [aria-current=page]').textContent") == "Sign-in and security", js("location.hash"))
            # #169: Account is you and this device, a group each in its sidebar; how things look is on Settings
            check("Account: two groups, sign-in and security first, and nothing else on the page",
                  js("[...document.querySelectorAll('#acctNav a')].map(a => a.textContent).join('|')") == "Sign-in and security|This device"
                  and js(display, "installApp") == "none" and js(display, "offlineRecords") == "none"
                  and js("document.getElementById('groupTitle').textContent") == "Sign-in and security"
                  and js("[...document.querySelectorAll('main > section')].every(s => ['password', 'twofa', 'passkeys', 'sessions', 'installApp', 'offlineRecords'].includes(s.id))"))
            page.click("#acctNav a[data-group=device]")
            check("…This device: installing it and offline records, and the address says so",
                  js(display, "installApp") != "none" and js(display, "offlineRecords") != "none" and js(display, "password") == "none"
                  and js(display, "sessions") == "none" and js("location.hash") == "#device"
                  and js("document.getElementById('groupTitle').textContent") == "This device")
            for addr, shows in (("#offline", "offlineRecords"), ("#install", "installApp"), ("#security", "password"), ("#sessions", "sessions")):
                page.goto(base + "/account.html" + addr)
                page.wait_for_function(f"getComputedStyle(document.getElementById('{shows}')).display !== 'none'", timeout=10000)
            check("…each of its addresses opens its group (#offline, #install, #security, #sessions)", True)
            page.set_viewport_size({"width": 375, "height": 800})
            for g in ("security", "device"):
                page.click(f"#acctNav a[data-group={g}]")
                settle(150)
                check(f"…Account's {g} group fits 375 px", js("document.documentElement.scrollWidth") <= 375, js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.goto(base + "/admin.html#accounts")
            page.wait_for_function("document.querySelectorAll('#userList tbody tr').length > 0", timeout=10000)
            page.fill("#userName", "alex")
            page.click("#userNew button[type=submit]")
            page.wait_for_function("document.querySelector('#userMsg code')", timeout=10000)
            check("admin creates an account with a one-time password", len(js("document.querySelector('#userMsg code').textContent")) >= 20)
            # no longer the only account: admin actions now wait for the admin's own second factor (issue #75)
            check("with a second account, the admin is asked for a second factor",
                  js("!document.getElementById('adminTf').hidden && document.getElementById('adminTools').hidden"))
            check("…and every other part of Admin the server now refuses says so", js("['admin', 'sec-sync', 'labmaps', 'installLog'].every(id => document.querySelector('#' + id + ' .adm-gate'))"))
            # two-factor sign-in: turn it on from the key shown, confirm with a code, get recovery codes
            from server import twofactor
            import time as _time
            totp = lambda key, ahead=0: twofactor._code(key, int(_time.time() // twofactor.PERIOD) + ahead)
            check("the admin's note links to Account's security tab", js("document.querySelector('#adminTf a').getAttribute('href')") == "account.html#security")
            page.click("#adminTf a")
            page.wait_for_url("**/account.html#security", timeout=10000)
            page.wait_for_function("document.getElementById('tfHint').textContent.length > 0", timeout=10000)
            check("which opens it", js(display, "twofa") != "none")
            page.locator("#twofa").get_by_role("button", name="Turn on").click()
            page.wait_for_selector("#twofa .tf-key")
            key = js("document.querySelector('#twofa .tf-key').textContent").replace(" ", "")
            check("2FA setup shows a QR code", js("document.querySelector('#twofa .tf-setup img').naturalWidth > 100"))
            page.locator("#twofa input").fill(totp(key))
            page.locator("#twofa").get_by_role("button", name="Confirm").click()
            page.wait_for_selector("#twofa .tf-codes")
            check("2FA confirmed, 10 recovery codes shown", len(js("document.querySelector('#twofa .tf-codes').textContent").split()) == 10)
            page.locator("#twofa").get_by_role("button", name="I've saved them").click()
            page.wait_for_function("document.getElementById('tfHint').textContent.startsWith('On.') && document.querySelector('#tfMsg a[href=\"admin.html\"]')", timeout=10000)
            check("with 2FA on, Account says Admin is open again", True)
            page.click("#tfMsg a")
            page.wait_for_function("!document.getElementById('adminTools').hidden && document.querySelector('#userList').textContent.includes('alex')", timeout=10000)
            check("with 2FA on, the account tools are back", js("document.getElementById('adminTf').hidden && !document.querySelector('.adm-gate')"))
            # activity: the profile's log (Sharing → Activity, #168) shows the records being opened (by this run), and
            # filters by kind; the admin's account log shows the account just created
            page.goto(base + "/sharing.html?profile=1#activity")
            page.wait_for_function("[...document.querySelectorAll('#actBody .activity tbody tr')].some(r => r.textContent.includes('opened the records'))", timeout=10000)
            check("profile activity shows the records being opened", js(display, "activity") != "none" and js(display, "people") == "none")
            page.locator("#actBody .activity select").nth(1).select_option("document")
            page.wait_for_function("[...document.querySelectorAll('#actBody .activity tbody tr')].every(r => r.textContent.includes('opened') && !r.textContent.includes('opened the records'))", timeout=10000)
            check("activity filters by kind", js("document.querySelector('#actBody .activity tbody').textContent.includes('demo-report.pdf') || document.querySelector('#actBody .activity tbody').textContent.includes('Nothing yet')"))
            page.goto(base + "/admin.html#activity")
            page.wait_for_function("document.querySelector('#acctLog tbody') && document.querySelector('#acctLog tbody').textContent.includes('created the account alex')", timeout=10000)
            check("admin sees the install's activity, under Admin → Install activity", js(display, "installLog") != "none" and js(display, "users") == "none")
            page.goto(base + "/sharing.html?profile=1#export")
            page.wait_for_selector("#exportLink", timeout=10000)
            href = js("document.getElementById('exportLink').getAttribute('href')")
            head = js(f"fetch('{href}').then(r => r.arrayBuffer()).then(b => [...new Uint8Array(b.slice(0, 2))])")
            check("Export everything downloads a zip", href == "api/profiles/1/export" and head == [0x50, 0x4B], (href, head))
            # People on the second profile (picked at the top): sharing with someone who already has access asks before
            # changing their role, and declining keeps it; then sharing for real, and a role changed in the table
            page.select_option("#profile", "2")
            page.click("#shrNav a[data-group=people]")
            page.wait_for_function("document.querySelector('#introHint').textContent.includes('Second person') && document.getElementById('people').dataset.profile === '2'", timeout=10000)
            check("picking another profile shows its people, and the address keeps the group", js("location.hash") == "#people"
                  and USER in page.inner_text("#memberList"))
            form = page.locator("#shareWith form")
            form.locator("input").fill(USER)
            form.locator("select").select_option("viewer")
            form.get_by_role("button", name="Share").click()
            page.wait_for_selector("dialog.sg-ask[open]", timeout=10000)
            share_focus = js("document.activeElement.textContent")
            asked_share = answer_ask(page, yes=False)
            settle(300)
            check("sharing with an existing member asks first", "already has access to Second person as owner" in asked_share[1]
                  and asked_share[0] == f"Change {USER} to viewer?", asked_share)
            check("declining keeps their role", js("fetch('api/profiles/2/members').then(r => r.json()).then(m => m[0].role)") == "owner")

            # ---- the app's own dialog (#210): asked in a <dialog> over the page, not the browser's pop-up (main() counts
            # any of those as a problem). At 375 px, in each theme: it fits, has a title and Cancel beside a button named for
            # the action, red for one that can't be undone with focus starting on Cancel; Escape cancels and focus goes back
            # to the button that opened it. A change that can be undone starts on its action. Later, Remove alex is opened
            # and confirmed by keyboard alone (Enter, Tab, Enter).
            check("…its question starts on the action, a change that can be undone (#210)", share_focus == "Change role", share_focus)
            theme_was_210 = js("localStorage.getItem('ht-theme')")
            page.set_viewport_size({"width": 375, "height": 800})
            dialog_js = """(() => { const d = document.querySelector('dialog.sg-ask[open]'), b = [...d.querySelectorAll('.sg-ask-acts button')];
              const rgb = c => c.match(/[\\d.]+/g).slice(0, 3).map(Number), r = d.getBoundingClientRect();
              return {modal: d.matches(':modal'), role: d.getAttribute('role'), title: d.querySelector('h2').textContent,
                named: document.getElementById(d.getAttribute('aria-labelledby')).textContent, buttons: b.map(x => x.textContent),
                onCancel: document.activeElement === b[0], bg: rgb(getComputedStyle(d).backgroundColor), ink: rgb(getComputedStyle(d).color),
                action: rgb(getComputedStyle(b[1]).backgroundColor), inside: r.left >= 16 && r.right <= innerWidth - 16 && r.top >= 0 && r.bottom <= innerHeight}; })()"""
            for theme in ("light", "dark"):
                js(f"localStorage.setItem('ht-theme', '{theme}'); applyTheme()")
                page.locator("#memberList button", has_text="Leave").click()
                page.wait_for_selector("dialog.sg-ask[open]", timeout=10000)
                d = js(dialog_js)
                fits = phone_fits(page, f"the dialog, {theme}")
                dark_bg = sum(d["bg"]) < 200 and sum(d["ink"]) > 600
                check(f"at 375 px, {theme}: Leave asks in the app's own dialog, titled, Cancel and a red Leave profile, focus on Cancel (#210)",
                      d["modal"] and d["role"] == "alertdialog" and d["title"] == d["named"] == "Leave Second person?"
                      and d["buttons"] == ["Cancel", "Leave profile"] and d["onCancel"] and d["action"] == ([208, 59, 59] if theme == "light" else [196, 52, 52])   # a deeper red in dark, for white text (#202)
                      and d["inside"] and (dark_bg if theme == "dark" else sum(d["bg"]) > 700 and sum(d["ink"]) < 100) and not fits, fits or d)
                page.keyboard.press("Escape")
                page.wait_for_selector("dialog.sg-ask", state="detached", timeout=10000)
                check(f"…{theme}: Escape cancels it, and focus is back on Leave",
                      js("document.activeElement.textContent") == "Leave" and USER in page.inner_text("#memberList")
                      and js("fetch('api/profiles/2/members').then(r => r.json()).then(m => m.length)") == 1, js("document.activeElement.outerHTML"))
            # the sign-in page's note after a recovery code: on the page with a Continue, not an alert (#210)
            ctx = browser.new_context(viewport={"width": 375, "height": 800}, color_scheme="dark")   # no session: the sign-in page
            lp = ctx.new_page()
            lp.goto(base + "/login.html")
            lp.wait_for_selector("#u", timeout=10000)
            lp.evaluate("signedIn({recovery_codes_left: 7})")
            lp.wait_for_timeout(300)   # (the real CSP: no wait_for_function) announce() says it after a moment
            check("after a recovery code, the sign-in page says what it means, with Continue focused (#210)",
                  "Signed in with a recovery code. You have 7 left" in lp.inner_text("#recoveryNote")
                  and "You have 7 left" in lp.evaluate("document.getElementById('sgStatus').textContent")   # said, not shown twice:
                  and lp.evaluate("document.getElementById('sgStatus').getBoundingClientRect().width") <= 1
                  and lp.evaluate("document.activeElement.id") == "recoveryGo" and not phone_fits(lp, "sign-in note"), phone_fits(lp, "sign-in note"))
            ctx.close()
            form.locator("input").fill("alex")
            form.locator("select").select_option("editor")
            form.get_by_role("button", name="Share").click()
            page.wait_for_function("document.querySelectorAll('#memberList tbody tr').length === 2 && document.getElementById('peopleMsg').textContent === 'Shared with alex as editor.'", timeout=10000)
            check("sharing adds them, as the role chosen", js("document.querySelector(\"#memberList select[aria-label=\\\"alex's role\\\"]\").value") == "editor")
            page.select_option("#memberList select[aria-label=\"alex's role\"]", "viewer")
            page.wait_for_function("document.getElementById('peopleMsg').textContent === 'alex is now a viewer.'", timeout=10000)
            check("changing a role in the table saves it", js("fetch('api/profiles/2/members').then(r => r.json()).then(m => m.find(x => x.username === 'alex').role)") == "viewer")
            page.focus("#memberList button[aria-label='Remove alex']")   # (#210: at 375 px, dark, by keyboard alone)
            page.keyboard.press("Enter")
            page.wait_for_selector("dialog.sg-ask[open]", timeout=10000)
            on_cancel = js("document.activeElement.textContent")
            page.keyboard.press("Tab")
            on_action = js("document.activeElement.textContent")
            page.keyboard.press("Enter")
            page.wait_for_function("document.querySelectorAll('#memberList tbody tr').length === 1", timeout=10000)
            check("Remove takes their access away", "alex" not in page.inner_text("#memberList"))
            check("…confirmed by keyboard in the app's dialog: Enter opens it on Cancel, Tab reaches Remove access, Enter does it (#210)",
                  on_cancel == "Cancel" and on_action == "Remove access" and not js("document.querySelector('dialog.sg-ask')"), (on_cancel, on_action))
            js(f"localStorage.setItem('ht-theme', {json.dumps(theme_was_210)}); applyTheme()" if theme_was_210 else "localStorage.removeItem('ht-theme'); applyTheme()")
            page.set_viewport_size({"width": 1400, "height": 900})
            page.set_viewport_size({"width": 375, "height": 800})
            for g in ("people", "links", "calendar", "summary", "export", "activity", "profile"):
                page.click(f"#shrNav a[data-group={g}]")
                settle(200)
                check(f"Sharing → {g} fits 375 px, its item in sight in the sidebar's row", js("document.documentElement.scrollWidth") <= 375
                      and js(f"(() => {{ const a = document.querySelector('#shrNav a[data-group={g}]').getBoundingClientRect(), b = document.querySelector('#shrNav ul').getBoundingClientRect(); return a.left >= b.left - 1 && a.right <= b.right + 1; }})()"),
                      js("document.documentElement.scrollWidth"))
            page.set_viewport_size({"width": 1400, "height": 900})
            # Profile, apart from sharing: a new profile is made and shown; deleting one needs its name typed exactly
            page.click("#shrNav a[data-group=profile]")
            page.fill("#profName", "Third person (fictional)")
            page.click("#profNew button[type=submit]")
            page.wait_for_function("document.getElementById('profMsg').textContent.startsWith('Created')", timeout=10000)
            third = js("document.getElementById('profile').value")
            check("New profile makes one, and the page shows it", js("document.querySelector('#profile option:checked').textContent") == "Third person (fictional) (owner)"
                  and js("document.querySelector('#profileBody input').value") == "Third person (fictional)", third)
            page.fill("#profileBody input", "Third person, renamed")
            page.click("#profileBody button:text-is('Rename')")
            page.wait_for_function("document.getElementById('profileMsg').textContent === 'Renamed.' && document.querySelector('#profile option:checked').textContent === 'Third person, renamed (owner)'", timeout=10000)
            check("Rename renames it, in the picker too", True)
            page.select_option("#profile", "2")
            page.wait_for_function("document.querySelector('#profileBody input').value === 'Second person'", timeout=10000)
            second = page.locator("#profileCard")
            second.get_by_role("button", name="Delete profile…").click()
            go = second.get_by_role("button", name="Delete it and everything in it")
            check("delete needs the profile's name first", go.is_disabled())
            second.get_by_label("Type Second person to confirm").fill("Second person")
            go.click()
            page.wait_for_function("![...document.querySelectorAll('#profile option')].some(o => o.textContent.includes('Second person')) && document.getElementById('pageMsg').textContent.startsWith('Deleted')", timeout=10000)
            check("…and another profile is shown after it", js("document.getElementById('profile').value") != "2")
            js(f"api('profiles/{third}', {{method: 'DELETE', body: JSON.stringify({{confirm_name: 'Third person, renamed'}})}}).then(r => r.status)")
            js("localStorage.setItem('ht-profile', '1')")
            gone_from = len(problems)
            check("deleting a profile removes it", js("fetch('api/profiles/2/data').then(r => r.status)") == 404)
            problems[gone_from:] = [x for x in problems[gone_from:] if "404" not in x]   # that 404 was the point
            # resetting a password needs a fresh check: once the sign-in is more than a few minutes old, it asks first
            from server import db as dbm, profiles
            db = dbm.connect()
            db.execute("UPDATE sessions SET verified_at=0")
            page.goto(base + "/admin.html#accounts")
            page.wait_for_function("document.querySelector('#userList').textContent.includes('alex')", timeout=10000)
            before = len(problems)
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
            before = len(problems)
            page.locator("#userList tr", has_text="alex").get_by_role("button", name="Disable").click()
            asked = answer_ask(page)
            page.wait_for_function("[...document.querySelectorAll('#userList tr')].some(r => r.textContent.includes('alex') && r.textContent.includes('disabled'))", timeout=10000)
            problems[before:] = [x for x in problems[before:] if "409" not in x]   # the refusal that prompts the question
            check("disabling a profile's only owner asks first", asked[0] == "Disable alex?" and "only owner of Alex's records" in asked[1], asked)
            page.wait_for_function("document.querySelector('#ownerless') && document.querySelector('#ownerless').textContent.includes(\"Alex's records\")", timeout=10000)
            check("an admin sees profiles left without an owner", True)
            # removing that account: confirmed, then asked who owns Alex's records instead (this admin)
            before_remove = len(problems)
            page.locator("#userList tr", has_text="alex").get_by_role("button", name="Remove").click()
            answer_ask(page)
            owner_asked = answer_ask(page, fill=USER)   # (#210: the app's dialog with a box for the name, not prompt)
            page.wait_for_function("!document.querySelector('#userList').textContent.includes('alex')", timeout=10000)
            check("…asked who takes over, in the app's dialog with a box for the name", owner_asked[0] == "Who takes over from alex?"
                  and "only owner of Alex's records" in owner_asked[1], owner_asked)
            problems[before_remove:] = [x for x in problems[before_remove:] if "409" not in x]   # the question's refusal
            mine_now = js("fetch('api/me').then(r => r.json()).then(j => j.profiles.map(p => p.name))")
            check("removing a sole owner hands their profile to whoever is named", "Alex's records" in mine_now, mine_now)
            # log out ends the session: the API now refuses
            page.goto(base + "/")
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0", timeout=20000)
            before = len(problems)
            menu_click(page, "#logout")
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
            menu_click(pk, "#logout")
            pk.wait_for_url("**/login.html", timeout=10000)
            check("sign-in page offers a passkey", pk.is_visible("#pk") and pk.inner_text("#pk") == "Sign in with a passkey")
            pk.click("#pk")
            pk.wait_for_function(SIGNED_IN_OR_ERROR, timeout=20000)
            check("a passkey signs in on its own", pk.evaluate("fetch('api/me').then(r => r.json()).then(j => j.user && j.user.username)") == USER,
                  pk.evaluate("(document.getElementById('e') || {}).textContent"))
            menu_click(pk, "#logout")
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
            problems.append(f"check stopped: {stopped(e)}")
            try:   # what the page showed when it stopped, for failures that only happen on CI's machines
                problems.append("page then: " + page.url + " " + page.evaluate("JSON.stringify({profile: document.getElementById('profile')?.value, "
                                "group: document.querySelector('main')?.dataset.group, buttons: document.getElementById('sourceButtons')?.textContent.slice(0, 200), "
                                "mine: document.getElementById('mine')?.className, intro: document.getElementById('introHint')?.textContent.slice(0, 120), "
                                "drawer: document.getElementById('drawer')?.className})"))
            except Exception:
                pass
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
            ph.goto(base + "/account.html#device")
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
            ip.goto(base + "/account.html#install")
            ip.wait_for_selector("#installApp .install-steps", timeout=10000)
            steps = ip.inner_text("#installApp")
            check("on an iPhone: Safari's Share, then Add to Home Screen", "Share button" in steps and "Add to Home Screen" in steps, steps)
            check("…and the page fits 390 px", ip.evaluate("document.documentElement.scrollWidth") <= 390)
            iphone.close()

            # turned on under Account (off until then); the records loaded next are kept
            ph.goto(base + "/account.html#offline")
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
            ph.goto(base + "/account.html#offline")
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
            menu_click(ph, "#logout")
            ph.wait_for_url("**/login.html", timeout=10000)
            until(NO_COPY, 5000)
            check("sign-out: the kept copy and the choice are deleted, the app's own files stay",
                  ph.evaluate(NO_COPY) and ph.evaluate("localStorage.getItem('sg-offline-records')") is None and ph.evaluate("caches.has('sanguity-shell-v1')"))
            # offline, the browser reports each request it couldn't make; signing out ends in 401s
            problems[before:] = [x for x in problems[before:] if "ERR_INTERNET_DISCONNECTED" not in x and "401" not in x]
            ctx.close()
        except Exception as e:   # report what was caught so far instead of a bare traceback
            problems.append(f"PWA check stopped: {stopped(e)}")
            if "ph" in locals():
                problems.append(f"phone page at {ph.url}: " + str(ph.evaluate("document.body.innerText.slice(0, 300)")))
        # ---- first run (#199): a fresh install, a second app on a data folder of its own, under the real CSP: setup,
        # then the getting-started guide opens by itself on the new, empty profile; it's named, a fictional C-CDA is
        # uploaded through Sources and the guide's way back, the phone is skipped, and the Overview shows the records.
        # Then the empty states: a second, empty profile's one next step, and every records tab's (a profile with only
        # a visit), Sources' Overview and Import history; at 375 px in light and dark, the guide's steps too
        fresh = None
        try:
            before = len(problems)
            fdir = os.path.join(TMP, "fresh")
            for d in ("keys", "watch-keys"):
                os.makedirs(os.path.join(fdir, d), exist_ok=True)
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0)); fport = s.getsockname()[1]
            fresh = subprocess.Popen([sys.executable, "-m", "server"], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={
                **os.environ, "SANGUITY_DATA": fdir, "SANGUITY_RECORDS": os.path.join(fdir, "records"), "SANGUITY_KEY_FILE": os.path.join(fdir, "keys", "data_key"),
                "SANGUITY_WATCH_FILE": os.path.join(fdir, "watch", "daily.json"), "SANGUITY_WATCH_KEYS": os.path.join(fdir, "watch-keys", "receiver-secrets.json"),
                "SANGUITY_SERVE_STATIC": "1", "HOST": "127.0.0.1", "PORT": str(fport)})
            fbase = f"http://127.0.0.1:{fport}"
            code_file = os.path.join(fdir, "setup-code.txt")
            for _ in range(300):   # up, with its setup code written
                try:
                    if os.path.exists(code_file) and urllib.request.urlopen(fbase + "/api/setup", timeout=1).status == 200:
                        break
                except Exception:
                    pass
                time.sleep(0.1)
            allowed.add(fbase)
            fctx = browser.new_context(viewport={"width": 1400, "height": 900})   # the real CSP: evaluate only, no wait_for_function
            fp = fctx.new_page()
            fp.on("request", elsewhere)
            fp.on("pageerror", lambda e: problems.append(f"script error (first run): {e}"))
            fp.on("console", lambda m: m.type == "error" and "401" not in m.text and problems.append(f"console error (first run): {m.text}"))

            def f_until(expr, ms=20000):
                for _ in range(ms // 100):
                    if fp.evaluate(expr):
                        return True
                    fp.wait_for_timeout(100)
                raise TimeoutError(f"waited {ms} ms for {expr}")
            guide_is = lambda: fp.evaluate("fetch('api/settings/user').then(r => r.json()).then(j => j.guide)")
            fp.goto(fbase + "/")
            fp.wait_for_url("**/setup.html", timeout=10000)
            fp.wait_for_selector("#c", timeout=10000)
            setup_axe = axe_check(fp, "setup.html, light")   # #202: axe-core's WCAG A and AA rules, under the real CSP
            fp.emulate_media(color_scheme="dark")
            setup_axe = [a + b for a, b in zip(setup_axe, axe_check(fp, "setup.html, dark"))]
            fp.emulate_media(color_scheme="no-preference")
            check("first run: setup has no serious or critical axe-core finding, in light and dark (WCAG 2.2 AA, #202)", not setup_axe[0],
                  (setup_axe[0] + setup_axe[1])[:8] or "")
            with open(code_file, encoding="utf-8") as f:
                fp.fill("#c", f.read().strip())
            fp.fill("#u", "robin")
            fp.fill("#p", "a fictional first-run passphrase")
            fp.fill("#p2", "a fictional first-run passphrase")
            fp.click("#b")
            fp.wait_for_url("**/guide.html?profile=1#name", timeout=15000)
            fp.wait_for_selector("#step-name:not([hidden])", timeout=10000)
            steps_js = "[...document.querySelectorAll('#guideSteps li')].map(li => li.textContent.replace(/^[\\d✓]/, '').replace(' (done)', ''))"
            check("first run: setup signs in and the guide opens by itself on the new, empty profile, at whose records it holds",
                  fp.evaluate(steps_js) == ["Whose records", "First records", "Phone (optional)", "Overview"] and fp.input_value("#profName") == "robin"
                  and fp.is_visible("#step-name [data-skip]") and fp.is_visible("#skipGuide") and guide_is() == "", fp.evaluate(steps_js))
            fp.fill("#profName", "Robin Example")
            fp.click("#nameGo")
            fp.wait_for_selector("#step-records:not([hidden])", timeout=10000)
            check("…naming the profile there (who it's for), then the first records, each way skippable",
                  fp.evaluate("fetch('api/me').then(r => r.json()).then(j => j.profiles.map(p => p.name))") == ["Robin Example"]
                  and "Robin Example" in fp.inner_text("#profileOne") and fp.evaluate("location.hash") == "#records"
                  and fp.is_visible("#step-records [data-skip]") and fp.is_hidden("#step-records [data-next]"))
            ways = fp.evaluate("[...document.querySelectorAll('#step-records .guide-way')].map(a => a.getAttribute('href'))")
            check("…its ways in are Sources' own sections: upload, a portal or plan (#165's section), typing in",
                  ways == [f"sources.html?profile=1&guide=records#{g}" for g in ("upload", "portals", "manual")], ways)
            fp.click("#step-records a[data-to=upload]")
            fp.wait_for_selector("#importer", state="visible", timeout=10000)
            check("…Upload an export opens Sources → Upload files, saying it's part of the guide, with Back to the guide",
                  fp.is_visible("#guideBar") and fp.get_attribute("#guideBack", "href") == "guide.html?profile=1#records")
            fp.click("#tab-files")
            fp.set_input_files("#pickFiles", os.path.join(ROOT, "tests", "fixtures", "importers", "ccda", "maple-grove-ccd", "maple-grove-ccd.xml"))
            f_until("!document.getElementById('go').disabled", 5000)
            fp.click("#go")
            last = "[...document.querySelectorAll('.import-job')].pop()"
            f_until(f"!!{last} && /^(Ready to import|All duplicates|Not imported)/.test({last}.textContent)", 30000)
            fp.evaluate(f"{last}.querySelector('.imp-actions .primary').click()")
            f_until(f"/^(Imported|Not imported)/.test({last}.textContent)", 30000)
            check("…where a fictional C-CDA is uploaded, previewed and saved", fp.evaluate(f"{last}.textContent").startswith("Imported."), fp.evaluate(f"{last}.textContent")[:200])
            fp.click("#guideBack")
            fp.wait_for_selector("#recordsHave:not([hidden])", timeout=10000)
            check("…and back in the guide, the step says the records are in, with Continue in place of Skip",
                  "Robin Example has records now, from 1 import" in fp.inner_text("#recordsHave") and fp.is_visible("#step-records [data-next]")
                  and fp.is_hidden("#step-records [data-skip]") and fp.evaluate("document.querySelector('#guideSteps li.done')?.textContent") == "✓First records (done)",
                  fp.inner_text("#step-records")[:300])
            fp.click("#step-records [data-next]")
            fp.wait_for_selector("#step-phone:not([hidden])", timeout=10000)
            check("…then a phone, optional, set up on Sources → Phone and watch",
                  "(optional)" in fp.inner_text("#phoneTitle") and fp.get_attribute("#step-phone .guide-way", "href") == "sources.html?profile=1&guide=phone#phone")
            fp.click("#step-phone [data-skip]")
            fp.wait_for_selector("#step-done:not([hidden])", timeout=10000)
            f_until("document.getElementById('doneSays').textContent.length > 0", 5000)
            check("…skipped, to the end: the Overview, with nothing left to skip, the guide not yet marked done",
                  "records are in" in fp.inner_text("#doneSays") and fp.is_hidden("#guideSkip") and guide_is() == "", fp.inner_text("#step-done"))
            # every step, and Sources with the way back, at 375 px in light and dark
            fp.set_viewport_size({"width": 375, "height": 800})
            bad = []
            for theme in ("light", "dark"):
                fp.emulate_media(color_scheme=theme)
                for step in ("name", "records", "phone", "done"):
                    fp.goto(fbase + f"/guide.html?profile=1#{step}")
                    fp.wait_for_selector(f"#step-{step}:not([hidden])", timeout=10000)
                    fp.wait_for_timeout(150)
                    bad += phone_fits(fp, f"{theme} guide #{step}")
                fp.goto(fbase + "/sources.html?profile=1&guide=records#upload")
                fp.wait_for_selector("#guideBar", timeout=10000)
                fp.wait_for_timeout(150)
                bad += phone_fits(fp, f"{theme} Sources with the way back")
            dark_ok = fp.evaluate("matchMedia('(prefers-color-scheme: dark)').matches")
            fp.emulate_media(color_scheme=None)
            fp.set_viewport_size({"width": 1400, "height": 900})
            check("…each step of the guide, and the way back on Sources, fit 375 px in light and dark", not bad and dark_ok, bad[:8] or "")
            fp.goto(fbase + "/guide.html?profile=1#done")
            fp.wait_for_selector("#step-done:not([hidden])", timeout=10000)
            fp.click("#finish")
            fp.wait_for_url("**/?profile=1#overview", timeout=10000)
            f_until("typeof booted !== 'undefined' && booted && EV.length > 0")
            check("…Open the Overview lands on the profile's records, the guide marked done",
                  fp.evaluate("document.querySelectorAll('#view-overview .tile').length") > 0 and guide_is() == "done", guide_is())
            fp.goto(fbase + "/")
            f_until("typeof booted !== 'undefined' && booted")
            check("…and signing in again opens the records, not the guide; the menu still has Getting started",
                  fp.evaluate("location.pathname") == "/" and fp.get_attribute("#guideLink", "href") == "guide.html")
            # the empty states: a second, empty profile
            new_profile = lambda name: fp.evaluate("""n => fetch('api/profiles', {method: 'POST', headers: {'X-Sanguity': '1', 'Content-Type': 'application/json'},
                body: JSON.stringify({name: n})}).then(r => r.json()).then(j => j.id)""", name)
            pid2 = new_profile("Sam Example")
            fp.goto(fbase + f"/?profile={pid2}")
            fp.wait_for_selector("main .empty-state", timeout=10000)
            nexts = "sel => [...document.querySelectorAll(sel + ' .empty-next')].map(a => [a.textContent, a.getAttribute('href')])"
            check("empty states: an empty profile says what would appear and has one next step, Get started (the guide)",
                  fp.evaluate(nexts, "main") == [["Get started", f"guide.html?profile={pid2}"]] and "no records yet" in fp.inner_text("main")
                  and fp.evaluate("location.pathname") == "/", fp.evaluate(nexts, "main"))
            fp.goto(fbase + f"/sources.html?profile={pid2}")
            fp.wait_for_selector("#srcList .empty-state", timeout=10000)
            src_next = fp.evaluate(nexts, "#srcList")
            fp.click("#srcNav a[data-group=history]")
            fp.wait_for_selector("#historyList .empty-state", timeout=10000)
            check("…Sources' Overview and Import history: what would be listed, and one next step, Upload files",
                  src_next == [["Upload files", "#upload"]] and fp.evaluate(nexts, "#historyList") == [["Upload files", "#upload"]], (src_next, fp.evaluate(nexts, "#historyList")))
            # a profile with only a visit: every records tab says what would be there, with one next step
            r = fp.evaluate(f"""fetch('api/profiles/{pid2}/manual', {{method: 'POST', headers: {{'X-Sanguity': '1', 'Content-Type': 'application/json'}},
                body: JSON.stringify({{save: true, items: [{{kind: 'visit', date: '2026-10-01', title: 'Fictional check-up', who: 'Dr. Example', where: 'Example Clinic'}}]}})}}).then(r => r.status)""")
            want = {"overview": [["Add lab results", f"sources.html?profile={pid2}#manual"]], "health": [["Connect a phone", f"sources.html?profile={pid2}#phone"]],
                    "timeline": [["Show everything", None]], "labs": [["Add lab results", f"sources.html?profile={pid2}#manual"]],
                    "costs": [["Type in a claim or payment", f"sources.html?profile={pid2}#payment"]]}
            got, bad = {}, []
            fp.goto(fbase + f"/?profile={pid2}#overview")
            f_until("typeof booted !== 'undefined' && booted && EV.length > 0")
            for t in want:
                fp.click("#tab-" + t)
                if t == "timeline":
                    fp.fill("#q", "no such event")
                    f_until("!!document.querySelector('#list .empty-state')", 5000)
                got[t] = fp.evaluate(nexts, "#view-" + t)
            check("…every records tab with nothing to show (a profile with only a visit) says what would be there, with one next step",
                  r == 200 and got == want and fp.is_visible("#notice") and "Not medical advice" in fp.inner_text("#notice"), (r, got))
            fp.click("#tab-timeline")
            fp.click("#list .empty-next")
            f_until("document.querySelectorAll('#list .row').length === 1 && state.q === '' && state.preset === 'all'", 5000)
            check("…the Timeline's Show everything clears the search and the period", fp.evaluate("document.activeElement.id") == "listTitle")
            fp.set_viewport_size({"width": 375, "height": 800})
            empty_axe = [[], []]
            for theme in ("light", "dark"):
                fp.emulate_media(color_scheme=theme)
                fp.goto(fbase + f"/?profile={pid2}#overview")
                f_until("typeof booted !== 'undefined' && booted")
                for t in want:
                    fp.click("#tab-" + t)
                    fp.wait_for_timeout(150)
                    bad += phone_fits(fp, f"{theme} {t}")
                    empty_axe = [a + b for a, b in zip(empty_axe, axe_check(fp, f"empty {t}, {theme}"))]   # #202
            fp.emulate_media(color_scheme=None)
            fp.set_viewport_size({"width": 1400, "height": 900})
            check("…and each fits 375 px, in light and dark", not bad, bad[:8] or "")
            check("…with no serious or critical axe-core finding (WCAG 2.2 AA, #202)", not empty_axe[0], (empty_axe[0] + empty_axe[1])[:8] or "")
            fctx.close()
        except Exception as e:   # report what was caught so far instead of a bare traceback
            problems.append(f"first-run check stopped: {stopped(e)}")
            if "fp" in locals():
                problems.append(f"first-run page at {fp.url}: " + str(fp.evaluate("document.body.innerText.slice(0, 300)")))
        finally:
            if fresh:
                fresh.terminate()
                fresh.wait(10)
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
