#!/usr/bin/env python3
"""The stylesheets' ratchet (#252): values written out by hand instead of taken from the tokens may only get fewer.

It counts, in every stylesheet under www/css/ but tokens.css, and in the <style> of every page under www/:
  colours        every hex colour (#fff), rgb()/rgba(), hsl()/hsla() or other colour function, and named colour
                 (white, red...) in a declaration's value, a custom property's too (quoted text and var() names are
                 skipped). The colours have tokens, so each one written out counts.
  px font sizes  the different px sizes given by font-size or the font shorthand
  radii          the different border-radius values (a corner's too) with a size of their own: not 0, inherit or a
                 lone var()
  shadows        the different box-shadow values with a size of their own: not none or a lone var()
                 (these three have no tokens yet, so reusing a value the file already has adds nothing; a new one does)
and in every script under www/js/, comments skipped:
  script styles  each style handed to el() (a `style:` key in an object literal, an object or a string) and each
                 write to an element's style (`.style.left = ...`, `.style.setProperty(...)`, `.style.cssText = ...`).
                 A value the page computes as it runs (a position, a bar's width) counts too: the number is what the
                 redesign brings down, not a ban.

Each count is held against ALLOWED below, today's numbers. A count that goes up fails, naming the file, where, and
what to use instead; one that goes down fails too, asking for the lower number here, so it can't creep back. A file
not listed may have none. tokens.css itself may hold only custom properties and color-scheme.

  python3 tests/test_css_tokens.py             # the check
  python3 tests/test_css_tokens.py --counts    # today's counts, as ALLOWED would list them
"""
import glob, os, re, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKENS = "www/css/tokens.css"

# file: {what: count}. Lower a number when a change brings it down; never raise one to let a new value in.
ALLOWED = {
    "www/css/app.css": {"colours": 11, "px font sizes": 18, "radii": 17, "shadows": 12},
    "www/css/summary.css": {"px font sizes": 2, "radii": 2},
    "www/login.html": {"colours": 2, "px font sizes": 4, "radii": 2, "shadows": 1},
    "www/setup.html": {"colours": 2, "px font sizes": 4, "radii": 2, "shadows": 1},
    "www/js/app.js": {"script styles": 2},
    "www/js/core.js": {"script styles": 12},
    "www/js/costs.js": {"script styles": 48},
    "www/js/cycle.js": {"script styles": 2},
    "www/js/drawer.js": {"script styles": 18},
    "www/js/health.js": {"script styles": 26},
    "www/js/heatmap.js": {"script styles": 6},
    "www/js/journal.js": {"script styles": 3},
    "www/js/medlog.js": {"script styles": 1},
    "www/js/meds.js": {"script styles": 7},
    "www/js/nav.js": {"script styles": 1},
    "www/js/overview.js": {"script styles": 15},
    "www/js/rename.js": {"script styles": 2},
    "www/js/share.js": {"script styles": 1},
    "www/js/timeline.js": {"script styles": 1},
    "www/js/trends.js": {"script styles": 9},
}

NAMED = """aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue blueviolet brown burlywood
cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan darkgoldenrod darkgray
darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen
darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink deepskyblue dimgray dimgrey dodgerblue
firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray green greenyellow grey honeydew
hotpink indianred indigo ivory khaki lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan
lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen lightskyblue lightslategray
lightslategrey lightsteelblue lightyellow lime limegreen linen magenta maroon mediumaquamarine mediumblue mediumorchid
mediumpurple mediumseagreen mediumslateblue mediumspringgreen mediumturquoise mediumvioletred midnightblue mintcream
mistyrose moccasin navajowhite navy oldlace olive olivedrab orange orangered orchid palegoldenrod palegreen
paleturquoise palevioletred papayawhip peru pink plum powderblue purple rebeccapurple red rosybrown royalblue
saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue slateblue slategray slategrey snow springgreen
steelblue tan teal thistle tomato turquoise violet wheat white whitesmoke yellow yellowgreen""".split()
COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(|\b(?:" + "|".join(NAMED) + r")\b", re.I)
PX = re.compile(r"\d*\.?\d+px")
A_SIZE = re.compile(r"(?<![\w.#-])(?:\d*\.\d+|[1-9]\d*)")   # a number that isn't a lone 0
RADIUS = re.compile(r"border(?:-[a-z]+)*-radius")
VAR = re.compile(r"var\([^()]*(?:\([^()]*\)[^()]*)*\)")
JS_COMMENTS = r"/\*.*?\*/|(?<![:\"'`\\])//[^\n]*"   # not the // of "https://"
SCRIPT_STYLE = re.compile(r"\bstyle\s*:|\.style\.(?:[A-Za-z]+\s*=(?!=)|setProperty\s*\()")
DISTINCT = ("px font sizes", "radii", "shadows")   # counted by their different values, not their uses

FIX = {
    "colours": "use a colour token from www/css/tokens.css (var(--ink), var(--accent)...); if none fits, add one there, "
               "light and dark",
    "px font sizes": "use a size the file already has, or em/rem (a type scale of tokens comes with the redesign)",
    "radii": "use a radius the file already has (radius tokens come with the redesign)",
    "shadows": "use a shadow the file already has (elevation tokens come with the redesign)",
    "script styles": "give the element a class and style it in www/css/app.css with the tokens, instead of setting "
                     "its style from the script",
}


def rel(path):
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def blank_comments(text, pattern):
    """the text without its comments, their newlines kept, so line numbers stay right"""
    return re.sub(pattern, lambda m: "\n" * m.group().count("\n"), text, flags=re.S)


def declarations(css, first_line=1):
    """(line, property, value) for every declaration in a stylesheet"""
    css = blank_comments(css, r"/\*.*?\*/")
    for m in re.finditer(r"(?<=[{;])\s*(--[\w-]+|[a-zA-Z-]+)\s*:\s*([^;{}]*)(?=[;}])", css):
        yield first_line + css.count("\n", 0, m.start(1)), m.group(1).lower(), " ".join(m.group(2).split())


def css_found(css, first_line=1):
    """{what: [(line, value), ...]} for one stylesheet's text"""
    out = {"colours": [], "px font sizes": [], "radii": [], "shadows": []}
    for line, prop, value in declarations(css, first_line):
        bare = re.sub(r"\"[^\"]*\"|'[^']*'", '""', value).replace("!important", "").strip()   # content:"✓" and the like
        out["colours"] += [(line, c) for c in COLOUR.findall(re.sub(r"--[\w-]+", "", bare))]   # not var(--red)'s name
        own = VAR.sub("", bare)
        if prop in ("font-size", "font") and PX.search(own):
            out["px font sizes"].append((line, PX.search(own).group()))
        if RADIUS.fullmatch(prop) and A_SIZE.search(own):
            out["radii"].append((line, bare))
        if prop == "box-shadow" and A_SIZE.search(own):
            out["shadows"].append((line, bare))
    return out


def found():
    """{file: {what: [(line, value), ...]}} over the stylesheets, the pages' <style> and the scripts"""
    out = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "www", "css", "*.css"))):
        if rel(path) != TOKENS:
            with open(path, encoding="utf-8") as f:
                out[rel(path)] = css_found(f.read())
    for path in sorted(glob.glob(os.path.join(ROOT, "www", "*.html"))):
        with open(path, encoding="utf-8") as f:
            html = f.read()
        here = out[rel(path)] = {}
        for m in re.finditer(r"<style[^>]*>(.*?)</style>", html, re.S | re.I):
            for what, hits in css_found(m.group(1), html.count("\n", 0, m.start(1)) + 1).items():
                here.setdefault(what, []).extend(hits)
    for path in sorted(glob.glob(os.path.join(ROOT, "www", "js", "*.js"))):
        with open(path, encoding="utf-8") as f:
            js = blank_comments(f.read(), JS_COMMENTS)
        out[rel(path)] = {"script styles": [(js.count("\n", 0, m.start()) + 1, m.group()) for m in SCRIPT_STYLE.finditer(js)]}
    return {f: {w: h for w, h in c.items() if h} for f, c in out.items()}


def count(what, hits):
    return len({v for _, v in hits}) if what in DISTINCT else len(hits)


def where(what, hits):
    """where to look: each value with its first line for the distinct counts, the rarest first (a new one is used once)"""
    if what not in DISTINCT:
        return "lines " + ", ".join(str(line) for line, _ in hits[:40]) + (" ..." if len(hits) > 40 else "")
    first, uses = {}, {}
    for line, v in hits:
        first.setdefault(v, line); uses[v] = uses.get(v, 0) + 1
    return "; ".join(f"{v} (line {first[v]}, {uses[v]} use{'s' if uses[v] > 1 else ''})" for v in sorted(first, key=lambda v: (uses[v], first[v]))[:8]) + " ..."


class CssTokens(unittest.TestCase):
    def test_the_token_file_holds_only_tokens(self):
        with open(os.path.join(ROOT, TOKENS), encoding="utf-8") as f:
            css = f.read()
        other = [f"line {line}: {prop}" for line, prop, _ in declarations(css) if not prop.startswith("--") and prop != "color-scheme"]
        self.assertEqual(other, [], f"{TOKENS} holds the tokens (custom properties) and color-scheme only; "
                                    "style elements in www/css/app.css")
        self.assertRegex(css, r"^(?:\s|/\*(?s:.*?)\*/)*@layer reset, tokens, base, components, pages, utilities;",
                         "tokens.css declares the layers' order first (every page loads it first)")

    def test_no_more_raw_values_than_allowed(self):
        now, problems = found(), []
        for path in sorted(set(now) | set(ALLOWED)):
            have, allowed = now.get(path, {}), ALLOWED.get(path, {})
            for what in sorted(set(have) | set(allowed)):
                n, cap = count(what, have.get(what, [])), allowed.get(what, 0)
                if n > cap:
                    problems.append(f"{path}: {n} {what}, {cap} allowed ({where(what, have[what])}). Instead, {FIX[what]}. "
                                    "Don't raise the number in tests/test_css_tokens.py to let a new one in.")
                elif n < cap:
                    problems.append(f"{path}: {what} down to {n} from {cap}, thank you. Lower ALLOWED[{path!r}][{what!r}] "
                                    f"to {n} in tests/test_css_tokens.py, so it stays down.")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_the_allow_list_names_files_that_exist(self):
        for path in ALLOWED:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, path)), f"{path} in ALLOWED: no such file (take it out)")
            self.assertNotEqual(path, TOKENS, "tokens.css isn't counted: it's where the values belong")

    def test_the_counter_finds_what_it_should(self):
        css = """.a{color:#fff;background:rgba(0,0,0,.1);border:1px solid red;content:"red";stroke:var(--red)}
.b{font-size:13px;font:600 12px/1.4 system-ui;font-size:1.1em;font-size:13px!important;border-radius:8px;border-top-left-radius:0}
.c{border-radius:var(--r);border-radius:8px;box-shadow:none;box-shadow:0 1px 2px var(--ring);box-shadow:var(--lift)}
@media (forced-colors:active){.d:hover{color:CanvasText;border-color:currentColor;fill:var(--ink)}}"""
        got = {w: count(w, h) for w, h in css_found(css).items()}
        self.assertEqual(got, {"colours": 3, "px font sizes": 2, "radii": 1, "shadows": 1})
        js = 'el("p", {style: {color: "var(--ink2)"}}); x.style.left = "4px"; if (x.style.left == "") {}\n' \
             '// el("i", {style: {}})\nx.style.setProperty("--w", w); fetch("https://example.test/"); y.style.top = 0 /* .style.x = 1 */'
        self.assertEqual(len(SCRIPT_STYLE.findall(blank_comments(js, JS_COMMENTS))), 4)


if __name__ == "__main__":
    if "--counts" in sys.argv[1:]:
        print("ALLOWED = {")
        for path, c in found().items():
            if c:
                print(f"    {path!r}: {{" + ", ".join(f"{w!r}: {count(w, h)}" for w, h in c.items()) + "},")
        print("}")
        sys.exit(0)
    unittest.main(verbosity=2)
