"""The website's screenshots (sanguity/sanguity.com: public/assets/shots/), from the fictional demo data, in dark and
light, as WebP.

The same throwaway demo install as tools/screenshots.py (nothing real is read or written), captured at 1440 x 900 at
2x, and the phone at 390 x 844 at 3x, then resized and saved as WebP so the page stays light.

  pip install -r requirements-dev.txt pillow && playwright install chromium
  python3 tools/site_screenshots.py [folder]   # default: ../sanguity.com/public/assets/shots, the website's checkout beside this one
"""
import os, shutil, sys, tempfile, time

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "..", "sanguity.com", "public", "assets", "shots"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import screenshots as S  # noqa: E402  (its start() makes the demo install)

BOOTED = "typeof booted !== 'undefined' && booted"
TABS = {
    "overview": "",
    "labs": "switchTab('labs', 'chart'); applyPreset('2y'); state.sel = new Set(['Hemoglobin', 'WBC']); renderAll(); renderLabs()",
    "heatmap": "switchTab('labs', 'heatmap'); applyPreset('2y')",
    "health": "applyPreset('90d')",
    "timeline": "applyPreset('1y')",
    "costs": "applyPreset(msKey(primaryMs()))",
}


def save(png, name):
    im = Image.open(png).convert("RGB")
    w = 1100 if name.startswith("phone") else 2400
    if im.width > w:
        im = im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
    im.save(os.path.join(OUT, name + ".webp"), "WEBP", quality=82, method=6)
    print("wrote", os.path.join(OUT, name + ".webp"))


def main():
    os.makedirs(OUT, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="sanguity-site-shots-")
    png = os.path.join(tmp, "shot.png")
    srv, base = S.start(tmp)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            errors = []
            for theme in ("dark", "light"):
                opts = dict(color_scheme=theme, timezone_id="America/Chicago", locale="en-US", bypass_csp=True)
                ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2, **opts)
                ctx.add_init_script(f"try {{ localStorage.setItem('ht-theme', '{theme}') }} catch (e) {{}}")
                page = ctx.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(base + "/login.html")
                page.fill("#u", S.USER)
                page.fill("#p", S.PW)
                page.click("#b")
                page.wait_for_function(BOOTED, timeout=30000)
                for name, setup in TABS.items():
                    page.goto(base + "/")
                    page.wait_for_function(BOOTED, timeout=30000)
                    page.evaluate(f"switchTab('{'labs' if name == 'heatmap' else name}')")
                    if setup:
                        page.evaluate(setup)
                    page.wait_for_timeout(900)
                    page.screenshot(path=png)
                    save(png, f"{name}-{theme}")
                page.goto(base + "/sources.html?profile=1#upload")
                page.wait_for_selector("#importer", state="visible", timeout=15000)
                page.wait_for_timeout(700)
                page.screenshot(path=png)
                save(png, f"import-{theme}")

                ph = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3, is_mobile=True, has_touch=True, **opts)
                ph.add_init_script(f"try {{ localStorage.setItem('ht-theme', '{theme}') }} catch (e) {{}}")
                ph.add_cookies(ctx.cookies())
                pp = ph.new_page()
                for tab in ("overview", "health"):
                    pp.goto(base + "/")
                    pp.wait_for_function(BOOTED, timeout=30000)
                    pp.evaluate(f"switchTab('{tab}')")
                    pp.wait_for_timeout(900)
                    pp.screenshot(path=png)
                    save(png, f"phone-{tab}-{theme}")
                ph.close()
                ctx.close()
            browser.close()
            if errors:
                sys.exit("script errors while taking screenshots: " + "; ".join(errors))
    finally:
        srv.should_exit = True
        time.sleep(0.3)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
