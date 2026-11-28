#!/usr/bin/env python3
"""Draws the wordmarks and the lockup in docs/logo/ as outlines in Inter SemiBold, the app's own font.

They used to be <text> in the viewer's system font, placed for one font's widths: elsewhere the "S" fell off the
left, the "y" was cut and the drop missed the "ı". Outlines look the same everywhere. The drops come from
sanguity-mark.svg and sanguity-mark-dark.svg, so run this again after changing the mark.

  pip install fonttools brotli uharfbuzz
  python3 tools/make_wordmark.py

Writes:
  sanguity-wordmark.svg        "Sanguıty" with the drop, its line thickened and no end dot, as the "i"'s dot
  sanguity-wordmark-dark.svg   the same for dark backgrounds (the README picks it by prefers-color-scheme)
  sanguity-wordmark-plain.svg  the drop alone, no line
  sanguity-lockup.svg          the mark beside "Sanguity"
"""
import io, os, xml.etree.ElementTree as ET

import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGO = os.path.join(ROOT, "docs", "logo")
FONT = os.path.join(ROOT, "www", "fonts", "inter-v4.001-latin.woff2")
WEIGHT = 600
INK, INK_DARK = "#2b2826", "#ece9e4"
SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)


def font():
    f = TTFont(FONT)
    f = instantiateVariableFont(f, {"wght": WEIGHT})
    f.flavor = None
    buf = io.BytesIO(); f.save(buf)
    return f, buf.getvalue()


def shape(data, text, size, tracking=0.0):
    """Glyph names and pen positions (px) for text at size, kerned as Inter kerns it."""
    face = hb.Face(data); hfont = hb.Font(face)
    buf = hb.Buffer(); buf.add_str(text); buf.guess_segment_properties()
    hb.shape(hfont, buf, {"kern": True, "liga": True})
    scale = size / face.upem
    x, out = 0.0, []
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        out.append((info.codepoint, x + pos.x_offset * scale))
        x += pos.x_advance * scale + tracking
    return out, x - tracking


def outline(f, gid, x, baseline, size):
    """One glyph's path, flipped to SVG's downward y, and its box in px."""
    gs = f.getGlyphSet(); name = f.getGlyphOrder()[gid]
    s = size / f["head"].unitsPerEm
    pen = SVGPathPen(gs, ntos=lambda v: f"{v:.2f}".rstrip("0").rstrip("."))
    gs[name].draw(TransformPen(pen, (s, 0, 0, -s, x, baseline)))
    g = f["glyf"][name]; g.recalcBounds(f["glyf"])
    box = (x + g.xMin * s, baseline - g.yMax * s, x + g.xMax * s, baseline - g.yMin * s) if hasattr(g, "xMin") else None
    return pen.getCommands(), box


def mark_parts(name):
    """The mark's drop (the filled path), line (the stroked path) and end dot (the circle), from its SVG."""
    root = ET.parse(os.path.join(LOGO, name)).getroot()
    paths = root.findall(f"{{{SVG_NS}}}path")
    drop = next(e for e in paths if e.get("fill") not in (None, "none"))
    line = next(e for e in paths if e.get("stroke") and e.get("stroke-opacity") is None)
    return drop, line, root.find(f"{{{SVG_NS}}}circle")


def el(e, **change):
    e = ET.fromstring(ET.tostring(e)); [e.set(k.replace("_", "-"), v) for k, v in change.items()]
    return ET.tostring(e, encoding="unicode").replace(f' xmlns="{SVG_NS}"', "").replace("ns0:", "")


def drop_group(mark, with_line, x, y, scale):
    drop, line, _ = mark_parts(mark)
    inner = el(drop) + (el(line, stroke_width="4.4") if with_line else "")
    return f'<g transform="translate({x:.2f} {y:.2f}) scale({scale})">{inner}</g>'


def svg(view, body):
    vb = " ".join(f"{v:.2f}".rstrip("0").rstrip(".") for v in view)
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" role="img" aria-label="Sanguity">\n  {body}\n</svg>\n'


def wordmark(f, data, ink, mark, with_line):
    size, base, scale = 48, 66, 0.48
    glyphs, width = shape(data, "Sanguıty", size)
    paths, boxes, stem = [], [], None
    for i, (gid, x) in enumerate(glyphs):
        d, box = outline(f, gid, x, base, size)
        paths.append(d); boxes.append(box)
        if i == 5: stem = box                      # the dotless i: the drop sits over its stem
    x_height = f["OS/2"].sxHeight * size / f["head"].unitsPerEm
    # the drop's box is 64 units; its point is at y=7 and its round bottom at y=59.5, centred on x=32
    drop_bottom = base - x_height - 4.5
    dx, dy = (stem[0] + stem[2]) / 2 - 32 * scale, drop_bottom - 59.5 * scale
    text = f'<path fill="{ink}" d="{" ".join(paths)}"/>'
    left = min(b[0] for b in boxes if b); right = max(b[2] for b in boxes if b)
    bottom = max(b[3] for b in boxes if b); top = dy + 7 * scale
    pad = 1.5
    view = (left - pad, top - pad, right - left + 2 * pad, bottom - top + 2 * pad)
    return svg(view, text + "\n  " + drop_group(mark, with_line, dx, dy, scale))


def lockup(f, data):
    size, base, x0 = 34, 47, 64
    glyphs, width = shape(data, "Sanguity", size, tracking=-0.5)
    paths, boxes = [], []
    for gid, x in glyphs:
        d, box = outline(f, gid, x0 + x, base, size)
        paths.append(d); boxes.append(box)
    drop, line, dot = mark_parts("sanguity-mark.svg")
    mark = el(drop) + el(line) + (el(dot) if dot is not None else "")
    right = max(b[2] for b in boxes if b) + 2
    return svg((0, 0, right, 64), mark + f'\n  <path fill="{INK}" d="{" ".join(paths)}"/>')


def main():
    f, data = font()
    files = {
        "sanguity-wordmark.svg": wordmark(f, data, INK, "sanguity-mark.svg", True),
        "sanguity-wordmark-dark.svg": wordmark(f, data, INK_DARK, "sanguity-mark-dark.svg", True),
        "sanguity-wordmark-plain.svg": wordmark(f, data, INK, "sanguity-mark.svg", False),
        "sanguity-lockup.svg": lockup(f, data),
    }
    for name, text in files.items():
        with open(os.path.join(LOGO, name), "w") as out:
            out.write(text)
        print("wrote", os.path.join("docs", "logo", name), f"({len(text) // 1024} KB)")


if __name__ == "__main__":
    main()
