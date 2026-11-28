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
    "info",                  # ⓘ: more about what's shown, a click away (the Labs charts' header, #279)
    "external-link",         # a link that opens another site
    "arrow-right",           # go on to a page or tab ("Open Costs")
    "arrow-up", "arrow-down",  # move up or down a list; a table's sort order
    "chevron-down",          # a menu button's caret
    "ellipsis",              # a row's or a kind's menu of more actions
    "pencil",                # rename
    "grip-vertical",         # drag to reorder
    "plus",                  # Add
    "chevrons-left-right", "chevrons-right-left",  # the drawer wider, narrower
    "sun-moon",              # light or dark (the share page, your menu's Dark mode, Settings' Display)
    # the kinds of record (#354): core.js's KIND_ICON, on the Timeline's chips and rows
    "bed",                   # a hospital stay
    "stethoscope",           # a visit
    "clipboard",             # chart activity: a chart review, a call, a fax
    "test-tube-diagonal",    # labs (and Settings' Labs and ranges)
    "scan-line",             # imaging
    "bandage",               # a procedure
    "syringe",               # an immunization
    "microscope",            # a report or study
    "message-square",        # a conversation, an appointment or automated message
    "mail", "file-text",     # a letter, a document (and Sharing's Visit summary)
    "pill",                  # a medication, a pharmacy fill
    "banknote",              # an insurance claim
    "watch",                 # the watch, a workout (and Sources' Phone and watch)
    "activity",              # an ECG, an irregular rhythm notification
    "notebook",              # the journal (and Add's Journal entry)
    "smile",                 # mood and energy
    "droplet",               # cycle tracking
    # the places (#354), one icon each wherever they're shown (#399: groups.js's SG_ICON and SG_PAGES, sgIcon())
    "layout-grid", "flag",   # the Overview tab, Settings' Milestones
    "upload", "keyboard", "link", "plug", "history",  # Sources (and Add): Upload files, Type records in, Paste a Health
                             # Link, Portals and health plans, Import history
    "users", "share-2", "calendar", "download", "user",  # Sharing: People; the Sharing page; Calendar feed, Export
                             # everything; Settings' About this person
    "lock", "smartphone",    # Account: Sign-in and security, This device
    "archive", "book-user", "globe",  # Admin: Backups, Provider directory, Outside connections
    "database", "settings", "circle-user", "wrench", "compass", "log-out",
                             # the Sources and Settings pages, your menu (its button on a phone), Admin, Getting
                             # started, Log out
    "search",                # the top bar's Search, the command palette (#273)
    # the records tabs on a phone's bar at the screen's foot (#268): Overview is layout-grid, Labs test-tube-diagonal,
    # Costs banknote, More ellipsis, your menu circle-user (above)
    "heart-pulse", "chart-gantt",  # Body and activity, Timeline
    "pill-bottle",           # Medications (#275): where they're kept, apart from a dose's pill
    # #399: a place of its own for what shared one
    "gauge",                 # Add's Home reading
    "user-plus", "user-pen",  # New profile; Settings: Rename or delete (Sharing's Profile before #402)
    "id-card",               # the emergency card (Sharing, #1126)
    "monitor",               # Display (your menu, Settings)
    "layout-list", "layout-dashboard",  # Sources' Overview, Settings' Dashboard
    "ruler", "dumbbell", "folder",  # Settings: Labs and ranges, Workouts, More
    "send", "scroll-text",   # Sharing: Share links (and New share link), Activity
    "user-cog", "logs", "app-window",  # Admin: Accounts, Install activity, Connected-source apps (Lab name matches'
                             # arrow-right-left went with its page, #1114)
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
