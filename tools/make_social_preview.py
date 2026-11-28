#!/usr/bin/env python3
"""Draws docs/logo/sanguity-social-preview.png (1280 x 640), the picture shown when a link to the project or to
sanguity.com is shared: the dark wordmark over the website's burgundy-to-blue glow, with its headline.

  pip install -r requirements-dev.txt && playwright install chromium
  python3 tools/make_social_preview.py

Uses the outlined wordmark (tools/make_wordmark.py) and the app's own fonts, so it looks the same wherever it's
drawn. GitHub's repository preview is uploaded by hand: Settings → General → Social preview.
"""
import os, pathlib

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "logo" / "sanguity-social-preview.png"
W, H = 1280, 640

PAGE = """<!doctype html><html><head><style>
@font-face{font-family:Inter;src:url(%(fonts)s/inter-v4.001-latin.woff2);font-weight:100 900}
@font-face{font-family:"Source Serif 4";src:url(%(fonts)s/source-serif-4-v4.004-wght-latin.woff2);font-weight:200 900}
html,body{margin:0;width:%(w)dpx;height:%(h)dpx;overflow:hidden;background:#0b0909}
body{position:relative;display:grid;place-items:center;font-family:Inter,sans-serif;color:#f4f0ea}
.glow{position:absolute;inset:-120px;filter:blur(110px)}
.glow i{position:absolute;border-radius:50%%}
.glow i:nth-child(1){width:720px;height:620px;left:-40px;top:-140px;background:rgb(150 36 62 / .8)}
.glow i:nth-child(2){width:620px;height:560px;right:-60px;top:-100px;background:rgb(57 135 229 / .42)}
.glow i:nth-child(3){width:620px;height:460px;left:460px;top:420px;background:rgb(164 147 242 / .3)}
.fade{position:absolute;inset:0;background:radial-gradient(70%% 80%% at 50%% 50%%,transparent 40%%,rgb(11 9 9 / .55))}
.c{position:relative;text-align:center}
.c img{width:520px;display:block;margin:0 auto}
h1{margin:34px 0 0;font:560 52px/1.05 "Source Serif 4",serif;letter-spacing:-.025em}
.g{background:linear-gradient(100deg,#ff9aa9,#e0607a 35%%,#a493f2 70%%,#8db4ee);-webkit-background-clip:text;background-clip:text;color:transparent}
p{margin:26px 0 0;font-size:22px;color:#c9c2b8;letter-spacing:.01em}
</style></head><body>
<div class="glow"><i></i><i></i><i></i></div><div class="fade"></div>
<div class="c">
  <img src="%(logo)s/sanguity-wordmark-dark.svg" alt="">
  <h1>Your whole health story <span class="g">on your own server</span></h1>
  <p>Open source · Self-hosted · sanguity.com</p>
</div>
</body></html>"""


def main():
    html = PAGE % {"w": W, "h": H, "fonts": (ROOT / "www" / "fonts").as_uri(), "logo": (ROOT / "docs" / "logo").as_uri()}
    page_file = ROOT / "docs" / "logo" / ".social-preview.html"
    page_file.write_text(html)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": W, "height": H})
            page.goto(page_file.as_uri())
            page.evaluate("document.fonts.ready")
            page.wait_for_timeout(300)
            page.screenshot(path=str(OUT))
            browser.close()
    finally:
        page_file.unlink(missing_ok=True)
    print("wrote", OUT.relative_to(ROOT))


if __name__ == "__main__":
    main()
