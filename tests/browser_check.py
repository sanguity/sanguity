#!/usr/bin/env python3
"""Headless browser check of the app with the fictional demo data.

Serves www/, opens every tab and checks its main content rendered, opens the detail drawer for every kind
of event, drags and scroll-zooms the charts, toggles the event sort, and fails on any script error,
console error, failed request or NaN written into an SVG attribute.

  python3 demo/generate.py --end 2026-11-15     # writes the demo data into www/
  python3 tests/browser_check.py                # needs: pip install playwright && playwright install chromium
"""
import functools, http.server, os, sys, threading

from playwright.sync_api import sync_playwright

WWW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "www")
NAN_HOOK = """window.__nan = []; const sa = Element.prototype.setAttribute;
Element.prototype.setAttribute = function (k, v) { if (String(v) === "NaN") window.__nan.push(this.tagName + "." + k); return sa.call(this, k, v); };"""


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def main():
    if not os.path.isfile(os.path.join(WWW, "data.json")):
        sys.exit("www/data.json is missing: run demo/generate.py first")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=WWW))   # threaded: Chrome opens idle spare connections that would block a single-threaded server
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    problems, passed = [], []

    def check(name, ok, detail=""):
        (passed if ok else problems).append(f"{name}{': ' + str(detail) if detail != '' else ''}")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.add_init_script(NAN_HOOK)
        page.on("pageerror", lambda e: problems.append(f"script error: {e}"))
        page.on("console", lambda m: m.type == "error" and problems.append(f"console error: {m.text}"))
        page.on("response", lambda r: r.status >= 400 and problems.append(f"HTTP {r.status}: {r.url}"))
        page.goto(base + "/index.html")
        try:
            page.wait_for_function("typeof EV !== 'undefined' && EV.length > 0", timeout=20000)
        except Exception:
            print("App did not start. Page says:", page.inner_text("main")[:300].replace("\n", " "))
            for x in problems:
                print("FAIL  ", x)
            sys.exit(1)
        settle = lambda ms=500: page.wait_for_timeout(ms)
        js = page.evaluate

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
        except Exception as e:   # report what was caught so far instead of a bare traceback
            problems.append(f"check stopped: {str(e).splitlines()[0]}")
        browser.close()
    srv.shutdown()

    for x in passed:
        print("ok    ", x)
    for x in problems:
        print("FAIL  ", x)
    print(f"\n{len(passed)} passed, {len(problems)} failed")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
