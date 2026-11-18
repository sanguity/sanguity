#!/usr/bin/env python3
"""Builds www/icons/sprite.svg, the app's icons, from Lucide's SVGs (#257).

Lucide (https://lucide.dev, ISC licence, www/icons/lucide-LICENSE.txt) draws each icon on a 24 by 24 grid in strokes.
The sprite keeps only the ones the app uses, ICONS below, each as <symbol id="i-NAME" viewBox="0 0 24 24"> holding
Lucide's shapes as they are. How they're drawn (no fill, currentColor strokes 2 wide, round ends and joins) is set
once in app.css's .icon rule, which the shapes inherit through <use>, so forced colours and the theme reach them.

  mkdir /tmp/lucide && cd /tmp/lucide && npm pack lucide-static@1.67.0 --ignore-scripts && tar -xzf lucide-static-1.67.0.tgz
  python3 -I tools/build_sprite.py /tmp/lucide/package

It reads <package>/icons/NAME.svg and <package>/package.json (to check the version), and writes the sprite. To add an
icon, add its Lucide name to ICONS and run this again; www/js/util.js's icon(name) and the pages' markup use
"icons/sprite.svg#i-NAME". A medical or app-specific glyph Lucide lacks would come from Tabler (MIT) the same way.
Standard library only.
"""
import json, os, sys
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "www", "icons", "sprite.svg")
LUCIDE = "1.67.0"   # the lucide-static release these come from (published 2026-11-03)
SVG = "{http://www.w3.org/2000/svg}"

# Lucide's names, in the order the sprite lists them; what each stands for in the app
ICONS = [
    "x",                     # close (the drawer), remove (a row, a chip, a chart)
    "check",                 # done, in range, good, the default, a step of the guide done
    "triangle-alert",        # a warning: backups late, a source needing attention
    "circle-question-mark",  # "What is this test?" where space is short (#163)
    "external-link",         # a link that opens another site
    "arrow-right",           # go on to a page or tab ("Open Costs")
    "arrow-up", "arrow-down",  # move up or down a list; a table's sort order
    "chevron-down",          # a menu button's caret
    "ellipsis",              # a row's or a kind's menu of more actions
    "pencil",                # rename
    "grip-vertical",         # drag to reorder
    "plus",                  # Add
    "chevrons-left-right", "chevrons-right-left",  # the drawer wider, narrower
    "sun-moon",              # light or dark (the share page)
]


def shapes(path):
    """the shapes of one Lucide icon, written out compactly"""
    svg = ET.parse(path).getroot()
    if svg.tag != SVG + "svg" or svg.get("viewBox") != "0 0 24 24":
        raise SystemExit(f"{path}: not a 24 by 24 Lucide icon")
    out = []
    for e in svg:
        tag = e.tag.removeprefix(SVG)
        if tag not in ("path", "circle", "rect", "line", "polyline", "polygon", "ellipse") or len(e):
            raise SystemExit(f"{path}: an unexpected <{tag}>")
        out.append(f"<{tag}" + "".join(f' {k}="{v}"' for k, v in e.attrib.items()) + "/>")
    return "".join(out)


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    pkg = sys.argv[1]
    with open(os.path.join(pkg, "package.json"), encoding="utf-8") as f:
        meta = json.load(f)
    if meta.get("name") != "lucide-static" or meta.get("version") != LUCIDE:
        raise SystemExit(f"expected lucide-static {LUCIDE}, found {meta.get('name')} {meta.get('version')}")
    symbols = [f'<symbol id="i-{n}" viewBox="0 0 24 24">{shapes(os.path.join(pkg, "icons", n + ".svg"))}</symbol>' for n in ICONS]
    text = (f"<!-- Sanguity's icons: Lucide {LUCIDE} (ISC licence, lucide-LICENSE.txt beside this file), built by tools/build_sprite.py -->\n"
            '<svg xmlns="http://www.w3.org/2000/svg">\n' + "\n".join(symbols) + "\n</svg>\n")
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"{os.path.relpath(OUT, ROOT)}: {len(ICONS)} icons, {len(text.encode()):,} bytes")


if __name__ == "__main__":
    main()
