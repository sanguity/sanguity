#!/usr/bin/env python3
"""The kinds of record's colours (#301): eight at most, validated as one categorical palette in light and in dark.

Reads the --c-* pairs from www/css/tokens.css and the Timeline's kinds, in order, from www/js/util.js (CATS), and holds
each theme's colours to the data-viz palette checks that can be measured (the same as its validator's, with the same
thresholds and colour-blindness model, so a palette passing here passes there):
  lightness band   OKLCH L 0.43-0.77 in light, 0.48-0.67 in dark (on the card: #ffffff, #1a1917)
  chroma floor     OKLCH C 0.10 or more: under it a hue reads as grey
  CVD separation   neighbours in the Timeline's order, OKLab distance x100 under protanopia and deuteranopia
                   (Machado, Oliveira and Fernandes 2009, severity 1): 6 at least (8 the target)
  normal vision    the same neighbours unsimulated: 15 at least
  contrast         3:1 against the card in dark; in light the two under it are named (aqua, yellow), each mark having
                   its words, its icon and a list or table beside it; cycle tracking 3:1 against the page too (#183)
and with every pair, not only neighbours, for the four colours that meet on a Labs chart: the lab's line, procedures'
and imaging's marks and the journal's row. Then: chart activity, immunizations, letters and ECGs are in a neutral ink;
--measure (a value over time that isn't a kind of record) is not one of the kinds' colours; and Body and activity and
Costs draw none of the kinds' colours but the Labs colour, on lab results.

    python tests/test_palette.py
"""
import math, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
read = lambda *p: open(os.path.join(ROOT, *p), encoding="utf-8").read()
BAND = {"light": (0.43, 0.77), "dark": (0.48, 0.67)}
SURFACE = {"light": "#ffffff", "dark": "#1a1917"}
MACHADO = {"protan": [[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]],
           "deutan": [[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.011820, 0.042940, 0.968881]]}
LIGHT_UNDER_3 = {"procedure", "study"}   # aqua and yellow on white: their marks have words beside them
PAGE = {"light": "#f6f5f1", "dark": "#121110"}
ON_A_LAB_CHART = ["lab", "imaging", "procedure", "journal"]


def lin(h):
    return [(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4) for c in (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))]


def oklab(rgb):
    r, g, b = rgb
    l, m, s = (math.copysign(abs(v) ** (1 / 3), v) for v in (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b,
                                                              0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b,
                                                              0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b))
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s, 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def sim(h, kind):
    c = lin(h)
    return c if not kind else [min(1, max(0, sum(row[i] * c[i] for i in range(3)))) for row in MACHADO[kind]]


def delta(a, b, kind=None):
    return 100 * math.dist(oklab(sim(a, kind)), oklab(sim(b, kind)))


def contrast(a, b):
    lum = lambda h: sum(w * c for w, c in zip((0.2126, 0.7152, 0.0722), lin(h)))
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def tokens():
    """{name: (light, dark)} for each --c-* and --measure in tokens.css"""
    out = {}
    for name, val in re.findall(r"--((?:c-[a-z]+)|measure):\s*([^;]+);", read("www", "css", "tokens.css")):
        m = re.fullmatch(r"light-dark\((#[0-9a-f]{6}),\s*(#[0-9a-f]{6})\)", val.strip())
        out[name] = (m[1], m[2]) if m else (val.strip(), val.strip())
    return out


def kinds():
    """the Timeline's kinds in order, [(k, its colour as CATS gives it)]"""
    cats = read("www", "js", "util.js").split("const CATS = [", 1)[1].split("];", 1)[0]
    return re.findall(r'\{k:"(\w+)",\s*label:"[^"]*",\s*color:"([^"]+)"', cats)


def main():
    fails, tok, ks = [], tokens(), kinds()
    fail = lambda msg: fails.append(msg) or print("FAIL", msg)
    own = [(k, re.fullmatch(r"var\(--(c-[a-z]+)\)", c)[1]) for k, c in ks if re.fullmatch(r"var\(--c-[a-z]+\)", c)]
    order = list(dict.fromkeys(t for _, t in own))   # mood shares the journal's
    print(f"{len(order)} colours, in the Timeline's order: {', '.join(t[2:] for t in order)}")
    if len(order) > 8:
        fail(f"{len(order)} kinds' colours: eight at most")
    unused = sorted(t for t in tok if t.startswith("c-") and t not in order)
    if unused:
        fail(f"tokens no kind uses: {unused}")
    for mode, i in (("light", 0), ("dark", 1)):
        pal = [tok[t][i] for t in order]
        lo, hi = BAND[mode]
        for t, h in zip(order, pal):
            L, a, b = oklab(lin(h))
            if not lo <= L <= hi:
                fail(f"{mode} {t} {h}: lightness {L:.3f} outside {lo}-{hi}")
            if math.hypot(a, b) < 0.10:
                fail(f"{mode} {t} {h}: chroma {math.hypot(a, b):.3f} under 0.10")
            cr = contrast(h, SURFACE[mode])
            if cr < 3 and not (mode == "light" and t[2:] in LIGHT_UNDER_3):
                fail(f"{mode} {t} {h}: {cr:.2f}:1 against the card, under 3:1")
        if contrast(tok["c-cycle"][i], PAGE[mode]) < 3:
            fail(f"{mode}: cycle tracking's lane under 3:1 against the page (#183)")
        pairs = list(zip(order, order[1:]))
        cvd = min((min(delta(tok[a][i], tok[b][i], k) for k in MACHADO), a, b) for a, b in pairs)
        nv = min((delta(tok[a][i], tok[b][i]), a, b) for a, b in pairs)
        print(f"{mode}: neighbours' worst CVD {cvd[0]:.1f} ({cvd[1][2:]}, {cvd[2][2:]}), normal vision {nv[0]:.1f} ({nv[1][2:]}, {nv[2][2:]})")
        if cvd[0] < 6:
            fail(f"{mode}: {cvd[1]} and {cvd[2]} {cvd[0]:.1f} apart for colour-blind eyes, under 6")
        if nv[0] < 15:
            fail(f"{mode}: {nv[1]} and {nv[2]} {nv[0]:.1f} apart, under 15")
        chart = ["c-" + k for k in ON_A_LAB_CHART]
        every = [(x, y) for n, x in enumerate(chart) for y in chart[n + 1:]]
        cvd4 = min(min(delta(tok[x][i], tok[y][i], k) for k in MACHADO) for x, y in every)
        nv4 = min(delta(tok[x][i], tok[y][i]) for x, y in every)
        print(f"{mode}: on a Labs chart, every pair: worst CVD {cvd4:.1f}, normal vision {nv4:.1f}")
        if cvd4 < 8 or nv4 < 15:
            fail(f"{mode}: the colours on a Labs chart ({', '.join(ON_A_LAB_CHART)}) don't pass with every pair")
        m = tok["measure"][i]
        near = min((delta(m, tok[t][i]), t) for t in order)
        if near[0] < 8:
            fail(f"{mode}: --measure {m} is {near[0]:.1f} from {near[1]}: too like a kind of record")
    colour = dict(ks)
    for k in ("contact", "immunization", "letter", "ecg"):
        if colour.get(k) != "var(--ink2)":
            fail(f"{k} is {colour.get(k)}, not the neutral ink")
    for f in ("health.js", "costs.js"):
        used = sorted(set(re.findall(r"--c-[a-z]+", read("www", "js", f))) - {"--c-lab"})
        if used:
            fail(f"{f} draws a measure in a kind's colour: {used}")
    print("FAILED" if fails else "OK: the kinds' colours pass in light and dark")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
