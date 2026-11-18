#!/usr/bin/env python3
"""Draws the home-screen icons in www/icons/ from the crimson drop in docs/logo/ (#56).

  pip install -r requirements-dev.txt && playwright install chromium
  python3 tools/make_icons.py

Writes:
  icon.svg               the mark itself (browsers that take an SVG icon)
  icon-192.png, -512     the drop on a transparent square ("any": Android, desktop installs)
  maskable-512.png       the drop inside the safe zone on the page colour, for launchers that cut icons to a shape
  apple-touch-icon.png   180 px on the page colour (iOS fills transparency with black, so it's opaque)
The PNGs are committed; run this again only when the logo changes.
"""
import os, shutil

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARK = os.path.join(ROOT, "docs", "logo", "sanguity-mark.svg")
OUT = os.path.join(ROOT, "www", "icons")
PAGE = "#f6f5f1"   # --page in www/css/tokens.css (light), also the manifest's background_color

# name, size in px, background (None = transparent), share of the side the drop's 64-unit box takes
ICONS = [("icon-192.png", 192, None, 1.0), ("icon-512.png", 512, None, 1.0),
         ("maskable-512.png", 512, PAGE, 0.72),        # inside the 80% circle every mask keeps
         ("apple-touch-icon.png", 180, PAGE, 0.8)]


def main():
    os.makedirs(OUT, exist_ok=True)
    svg = open(MARK, encoding="utf-8").read()
    shutil.copyfile(MARK, os.path.join(OUT, "icon.svg"))
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, size, bg, share in ICONS:
            page = browser.new_page(viewport={"width": size, "height": size})
            side = size * share
            page.set_content(f"""<html><body style="margin:0;background:{bg or 'transparent'}">
              <div style="width:{size}px;height:{size}px;display:flex;align-items:center;justify-content:center">
              <div style="width:{side}px;height:{side}px">{svg.replace('<svg ', '<svg width="100%" height="100%" ', 1)}</div></div></body></html>""")
            page.screenshot(path=os.path.join(OUT, name), omit_background=bg is None)
            page.close()
        browser.close()
    print("wrote", ", ".join(n for n, *_ in ICONS), "and icon.svg to", OUT)


if __name__ == "__main__":
    main()
