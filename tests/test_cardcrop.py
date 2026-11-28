#!/usr/bin/env python3
"""An insurance card's photo found and straightened in the browser (#1168, www/js/cardcrop.js), on fixture photos made
here: a card (85.6 by 54 mm, with a header band and lines of "text") drawn on a noisy, busy background, straight,
skewed in perspective, rotated, standing on end, small in the frame, and in low contrast. ccFind must put each corner
within 2.5% of the picture's diagonal of the card's; a picture with no card finds none. ccHomography and ccWarp
take the corners to a card-shaped rectangle, ccLayout lays a card found on end down flat and turns it a quarter at a
time. It runs in node (the script loaded into a vm context of its own, as a page would load it). Fictional pictures.

  python3 tests/test_cardcrop.py      # needs node on the PATH; -v prints how near each was
"""
import json, math, os, random, shutil, subprocess, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "www", "js", "cardcrop.js")
RATIO = 85.6 / 54
W, H = 360, 270


def homography(quad):
    """the map from the unit square ((0,0) top-left, clockwise) onto quad, as a function (u, v) -> (x, y)"""
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = quad
    dx1, dx2, dy1, dy2 = x1 - x2, x3 - x2, y1 - y2, y3 - y2
    sx, sy = x0 - x1 + x2 - x3, y0 - y1 + y2 - y3
    d = dx1 * dy2 - dx2 * dy1
    g, h = (sx * dy2 - dx2 * sy) / d, (dx1 * sy - sx * dy1) / d
    a, b, c = x1 - x0 + g * x1, x3 - x0 + h * x3, x0
    e, f, i = y1 - y0 + g * y1, y3 - y0 + h * y3, y0

    def to(u, v):
        z = g * u + h * v + 1
        return (a * u + b * v + c) / z, (e * u + f * v + i) / z

    def back(x, y):   # its inverse, for drawing
        A = [[a - g * x, b - h * x], [e - g * y, f - h * y]]
        r = [x - c, y - i]
        det = A[0][0] * A[1][1] - A[0][1] * A[1][0]
        return (r[0] * A[1][1] - A[0][1] * r[1]) / det, (A[0][0] * r[1] - r[0] * A[1][0]) / det
    return to, back


def rect(cx, cy, long, angle=0.0, tilt=0.0, stand=False):
    """a card's corners: centred at (cx, cy), long px wide, turned angle degrees, its top narrower by tilt (a
    perspective), stood on end"""
    w, h = (long / RATIO, long) if stand else (long, long / RATIO)
    pts = [(-w / 2 * (1 - tilt), -h / 2), (w / 2 * (1 - tilt), -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    t = math.radians(angle)
    return [(cx + x * math.cos(t) - y * math.sin(t), cy + x * math.sin(t) + y * math.cos(t)) for x, y in pts]


def photo(quad, card=200, bg=90, noise=12, seed=1, busy=True, stand=False, clutter=35, band=40):
    """a grey picture W x H of the card at quad on a background with noise, and (busy) stripes and blotches of up to
    clutter lighter or darker"""
    rnd = random.Random(seed)
    px = [[bg + rnd.gauss(0, noise) for _ in range(W)] for _ in range(H)]
    if busy:   # a table's grain and a few things lying on it
        for _ in range(6):
            x0, y0, x1, y1 = rnd.randrange(W), rnd.randrange(H), rnd.randrange(W), rnd.randrange(H)
            v = rnd.uniform(-clutter, clutter)
            for y in range(min(y0, y1), max(y0, y1)):
                for x in range(min(x0, x1), min(max(x0, x1), min(x0, x1) + 40)):
                    px[y][x] += v
        for y in range(H):
            s = 10 * math.sin(y / 7.0)
            for x in range(W):
                px[y][x] += s
    _, back = homography(quad)
    xs, ys = [p[0] for p in quad], [p[1] for p in quad]
    lines = [(0.08 + 0.1 * k, 0.15 + 0.5 * rnd.random()) for k in range(3, 9)]   # "text": rows, each so long
    for y in range(max(0, int(min(ys)) - 1), min(H, int(max(ys)) + 2)):
        for x in range(max(0, int(min(xs)) - 1), min(W, int(max(xs)) + 2)):
            u, v = back(x + 0.5, y + 0.5)
            if stand:
                u, v = v, 1 - u
            if not (0 <= u <= 1 and 0 <= v <= 1):
                continue
            val = card - band if v < 0.22 else card   # a band across its top, its name in it
            for top, length in lines:
                if top <= v <= top + 0.04 and 0.07 <= u <= 0.07 + length:
                    val = card - 110
            if 0.1 <= v <= 0.16 and 0.07 <= u <= 0.5:
                val = card + 30
            px[y][x] = val + rnd.gauss(0, noise)
    return [max(0, min(255, int(round(v)))) for row in px for v in row]


def run(expr, data):
    js = ("const vm = require('vm'), fs = require('fs'); const ctx = vm.createContext({});"
          "vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx, {filename: 'cardcrop.js'});"
          "ctx.DATA = JSON.parse(fs.readFileSync(0, 'utf8'));"
          "console.log(JSON.stringify(vm.runInContext(process.argv[2], ctx)));")
    r = subprocess.run(["node", "-e", js, JS, expr], input=json.dumps(data), capture_output=True, text=True, timeout=300)
    if r.returncode:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)


def off_by(found, quad):
    """the largest distance from a corner to the found one, as a share of the diagonal, the order matched round"""
    diag = math.hypot(W, H)
    return min(max(math.hypot(found[(k + s) % 4][0] - quad[k][0], found[(k + s) % 4][1] - quad[k][1]) for k in range(4))
               for s in range(4)) / diag


CASES = {   # name: (corners, how it's drawn)
    "straight": (rect(180, 135, 250), {}),
    "skewed": (rect(178, 140, 260, tilt=0.18), {}),
    "rotated": (rect(180, 135, 230, angle=21), {"seed": 2}),
    "rotated the other way, skewed": (rect(185, 130, 240, angle=-14, tilt=0.12), {"seed": 3}),
    "on end": (rect(180, 135, 220, angle=6, stand=True), {"seed": 4, "stand": True}),
    "small in the frame": (rect(150, 120, 140, angle=8), {"seed": 5}),
    "low contrast": (rect(180, 135, 250, angle=9, tilt=0.08), {"card": 128, "bg": 104, "noise": 9, "seed": 6, "clutter": 12, "band": 12}),
    "low contrast, quiet background": (rect(176, 138, 240, angle=-5), {"card": 150, "bg": 135, "noise": 6, "busy": False, "seed": 7, "band": 0}),
}


@unittest.skipUnless(shutil.which("node") or os.environ.get("CI"), "needs node")   # in CI, a missing node fails
class CardCrop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pics = {k: photo(q, **kw) for k, (q, kw) in CASES.items()}
        pics["no card"] = photo(rect(-9999, -9999, 10), seed=9, clutter=0)   # a table's grain alone
        cls.found = run(f"Object.fromEntries(Object.entries(DATA).map(([k, g]) => [k, ccFind(g, {W}, {H})]))", pics)

    def test_found_on_each_fixture(self):
        report = []
        for name, (quad, _) in CASES.items():
            got = self.found[name]
            self.assertIsNotNone(got, f"{name}: not found")
            err = off_by(got["pts"], quad)
            report.append(f"  {name}: within {err * 100:.1f}% of the diagonal (score {got['score']:.2f})")
            self.assertLess(err, 0.025, f"{name}: {got['pts']} for {quad}")
        if "-v" in sys.argv:
            print("\n" + "\n".join(report), file=sys.stderr)

    def test_corners_in_order(self):
        """top-left, top-right, bottom-right, bottom-left, whichever way the card lies"""
        for name in ("straight", "rotated", "skewed"):
            tl, tr, br, bl = self.found[name]["pts"]
            self.assertTrue(tl[0] < tr[0] and bl[0] < br[0] and tl[1] < bl[1] and tr[1] < br[1], (name, self.found[name]["pts"]))

    def test_none_without_a_card(self):
        self.assertIsNone(self.found["no card"])

    def test_straightened(self):
        """the homography takes the result's corners to the card's, and the warp reads the photo there; a card found
        on end is laid down flat, landscape, and Turn turns it a quarter clockwise each time"""
        quad = [[40, 30], [300, 50], [290, 230], [30, 210]]
        got = run("(() => { const H = ccHomography(DATA.q, 120, 76), at = (u, v) => { const z = H[6]*u + H[7]*v + 1;"
                  " return [(H[0]*u + H[1]*v + H[2]) / z, (H[3]*u + H[4]*v + H[5]) / z]; };"
                  " const src = {width: 4, height: 4, data: Array.from({length: 64}, (_, i) => i % 4 === 3 ? 255 : 10 * (i >> 2))},"
                  " out = {width: 2, height: 2, data: new Array(16).fill(0)};"
                  " ccWarp(src, [[0, 0], [3, 0], [3, 3], [0, 3]], out);"
                  " return {corners: [at(0, 0), at(120, 0), at(120, 76), at(0, 76)], px: out.data,"
                  " standing: ccLayout([[100, 20], [160, 20], [160, 115], [100, 115]]),"
                  " turned: ccLayout([[0, 0], [100, 0], [100, 63], [0, 63]], 1)}; })()", {"q": quad})
        for a, b in zip(got["corners"], quad):
            self.assertAlmostEqual(a[0], b[0], places=6)
            self.assertAlmostEqual(a[1], b[1], places=6)
        self.assertEqual(got["px"][0:4], [0, 0, 0, 255], "the result's top-left pixel is the photo's top-left")
        self.assertEqual(got["px"][12:16], [75, 75, 75, 255], "its bottom-right, at (1.5, 1.5) of the photo: between four pixels (bilinear)")
        st = got["standing"]
        self.assertEqual((st["w"], st["h"]), (1200, 757), "on end: laid down, landscape")
        self.assertEqual(st["c"][0], [100, 115], "its bottom-left corner the result's top-left")
        tu = got["turned"]
        self.assertEqual((tu["w"], tu["h"]), (757, 1200), "a quarter turn: standing")
        self.assertEqual(tu["c"][0], [0, 63])


if __name__ == "__main__":
    unittest.main(verbosity=2, argv=[a for a in sys.argv if a != "-v"])
