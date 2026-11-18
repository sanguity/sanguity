#!/usr/bin/env python3
"""The contrast of the colour tokens (#254): each pair that is text on a surface, or an edge that has to be seen, in
light and dark, as the browser resolves www/css/tokens.css. Prints the table in docs/accessibility.md, and fails
(exit 1) when a pair is under what WCAG 2.2 AA asks of it or when the three ways a theme is reached disagree.

  pip install -r requirements-dev.txt && playwright install chromium
  python3 tools/contrast.py

Each token is read from an element that uses it (getComputedStyle), so light-dark() pairs, rgb() with an alpha and
color-scheme are resolved by Chromium itself, in four states: the system's light and dark with no theme chosen, and a
chosen light and dark theme (data-theme on <html>, as theme.js sets it) under the other system setting. A token with an
alpha (--hover and --ring in dark) is laid over the surface it's on before measuring.
"""
import os, re, sys

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (foreground, background, needs, what): background "a/b" is token a laid over token b; needs is WCAG's minimum,
# 4.5 for text (1.4.3), 3 for an edge or a focus ring (1.4.11), None for a decorative step shown for reference
TEXT = [(fg, bg, 4.5, what) for fg, what in (("--ink", "text"), ("--ink2", "secondary text"), ("--muted", "muted text"),
                                             ("--accent", "a link, a selected control's text"), ("--crit", "out of range, an error"),
                                             ("--good-text", "in range, done"))
        for bg in ("--page", "--surface", "--raised", "--hover/--surface", "--hover/--raised")]
PAIRS = TEXT + [
    ("--on-accent", "--accent-fill", 4.5, "a filled button's text"),
    ("--on-accent", "--crit-fill", 4.5, "a filled button's text, for something that can't be undone"),
    ("--field-line", "--raised", 3, "a field's edge, against its inside"),
    ("--field-line", "--surface", 3, "a field's edge, against the card"),
    ("--field-line", "--page", 3, "a field's edge, against the page (sign-in, setup)"),
    ("--accent", "--page", 3, "the focus ring"),
    ("--accent", "--surface", 3, "the focus ring"),
    ("--accent", "--raised", 3, "the focus ring"),
    ("--crit", "--raised", 3, "a field marked wrong"),
    ("--surface", "--page", None, "a card against the page"),
    ("--ring", "--page", None, "a card's edge against the page"),
    ("--grid", "--surface", None, "a divider, a chart's grid"),
    ("--axis", "--surface", None, "a chart's axis"),
]
STATES = {"light": ("light", None), "dark": ("dark", None), "chosen light": ("dark", "light"), "chosen dark": ("light", "dark")}
READ = """names => Object.fromEntries(names.map(n => { const s = document.createElement('span'); s.style.color = `var(${n})`;
  document.body.append(s); const c = getComputedStyle(s).color; s.remove(); return [n, c]; }))"""


def rgba(css):
    n = [float(x) for x in re.findall(r"[\d.]+", css)]
    return (n[0], n[1], n[2], n[3] if len(n) > 3 else 1.0)


def over(top, under):
    a = top[3]
    return tuple(top[i] * a + under[i] * (1 - a) for i in range(3)) + (1.0,)


def luminance(c):
    lin = [(v / 255) / 12.92 if v / 255 <= 0.04045 else ((v / 255 + 0.055) / 1.055) ** 2.4 for v in c[:3]]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def hexed(c):
    return "#" + "".join(f"{round(v):02x}" for v in c[:3])


def measure():
    with open(os.path.join(ROOT, "www", "css", "tokens.css"), encoding="utf-8") as f:
        css = f.read()
    names = sorted(set(re.findall(r"(--[\w-]+)\s*:", re.sub(r"/\*.*?\*/", "", css, flags=re.S))) - {"--font-ui", "--font-display", "--font-mono"})
    out = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for state, (system, chosen) in STATES.items():
            page = browser.new_page(color_scheme=system)
            page.set_content(f"<!doctype html><html{f' data-theme={chosen}' if chosen else ''}><style>{css}</style><body></body></html>")
            out[state] = {n: rgba(c) for n, c in page.evaluate(READ, names).items()}
            page.close()
        browser.close()
    return out


def colour(tokens, name, base="--page"):
    """a token as an opaque colour: laid over the token after its "/", or over the page if it has an alpha of its own"""
    top, _, under = name.partition("/")
    c = tokens[top]
    return over(c, colour(tokens, under or base)) if c[3] < 1 else c


def main():
    got = measure()
    problems = [f"{a} and {b} differ at {n}: {got[a][n]} vs {got[b][n]}" for a, b in (("light", "chosen light"), ("dark", "chosen dark"))
                for n in got[a] if got[a][n] != got[b][n]]
    rows = ["| Foreground | Background | What it is | Light | Dark | Needs |", "|---|---|---|---:|---:|---:|"]
    for fg, bg, needs, what in PAIRS:
        cells = []
        for theme in ("light", "dark"):
            t = got[theme]
            back = colour(t, bg)
            r = ratio(colour(t, fg, bg.split("/")[-1] if "/" in bg else bg), back)
            cells.append(f"{r:.2f} ({hexed(colour(t, fg, bg.split('/')[-1] if '/' in bg else bg))} on {hexed(back)})")
            if needs and r < needs:
                problems.append(f"{fg} on {bg} in {theme}: {r:.2f}, needs {needs}")
        bg_name = f"`{bg.split('/')[0]}` on `{bg.split('/')[1]}`" if "/" in bg else f"`{bg}`"
        rows.append(f"| `{fg}` | {bg_name} | {what} | {cells[0]} | {cells[1]} | {f'{needs}:1' if needs else '–'} |")
    print("\n".join(rows))
    if problems:
        print("\n" + "\n".join(problems), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
