#!/usr/bin/env python3
"""The stylesheets' ratchet (#252): values written out by hand instead of taken from the tokens may only get fewer.

It reads every stylesheet under www/ but tokens.css, the <style> and style="..." attributes of every page under
www/, and every script under www/, subfolders included (#307), and counts per file:
  colours        every hex colour (#fff), rgb()/rgba(), hsl()/hsla() or other colour function, and named colour
                 (white, red...) in a declaration's value, a custom property's too (quoted text and var() names are
                 skipped), in a style="..." attribute, and in a page's colour attribute (fill, stroke, color,
                 stop-color...; the theme-color <meta> is left alone: the browser needs a plain colour there).
                 The colours have tokens, so each one written out counts.
  px font sizes  the different px sizes given by font-size or the font shorthand
  radii          the different border-radius values (a corner's too) with a size of their own: not 0, inherit or a
                 lone var()
  shadows        the different box-shadow values with a size of their own: not none or a lone var()
                 (these three have no tokens yet, so reusing a value the file already has adds nothing; a new one does)
  style attributes  each style="..." on a page's elements
and in every script, comments skipped:
  script styles  each style handed to el() (a `style:` or "style": key in an object literal, an object or a string),
                 each write to an element's style (`.style.left = ...`, `.style[k] = ...`, `.style.setProperty(...)`,
                 `.style.cssText = ...`, `Object.assign(x.style, ...)`) and each `setAttribute("style", ...)`.
                 A value the page computes as it runs (a position, a bar's width) counts too: the number is what the
                 redesign brings down, not a ban. A `style:` whose value is a formatter's keyword ("currency",
                 "percent", "decimal", "unit", "long", "short", "narrow": Intl.NumberFormat, toLocaleString and the
                 like) isn't a style and isn't counted.
  colours        a colour written in a string ('...', "..." or a template's text), where it would reach an element
                 (el("svg:rect", {fill: "#f00"}), a style string, an iframe's srcdoc):
                 - a hex colour of 3, 4, 6 or 8 digits standing alone: not part of a longer word or id ("#add-form",
                   "#fade2"), not after a letter, digit, &, / or # ("&#160;", "page.html#abc", "/#abc"), and not in a
                   string handed to $(), $$(), querySelector(), closest(), matches() or getElementById(), or given
                   to an href or a hash: those are ids and fragments, not colours ("#list" isn't hex anyway);
                 - a colour function, rgb() to oklch(), anywhere in a string;
                 - a named colour only where a colour goes: in a declaration of a colour property inside the string
                   ("fill:red", "border:1px solid white"), or anywhere in a string given to a colour key or
                   attribute (fill: "red", backgroundColor: "white", setAttribute("stroke", "black")). So the word
                   in "e.g. Red patch, 3 cm" isn't one.

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
    # a page's one colour (sign-in's and setup's third) is the brand mark's white highlight, stroke="#fff"
    "www/account.html": {"colours": 1},
    "www/admin.html": {"colours": 1},
    "www/guide.html": {"colours": 1},
    "www/index.html": {"colours": 1, "style attributes": 3},
    "www/login.html": {"colours": 3, "px font sizes": 4, "radii": 2, "shadows": 1, "style attributes": 1},
    "www/settings.html": {"colours": 1},
    "www/setup.html": {"colours": 3, "px font sizes": 4, "radii": 2, "shadows": 1, "style attributes": 3},
    "www/share.html": {"colours": 1},
    "www/sharing.html": {"colours": 1},
    "www/sources.html": {"colours": 1},
    "www/js/app.js": {"script styles": 2},
    "www/js/core.js": {"script styles": 11},
    "www/js/costs.js": {"script styles": 47},
    "www/js/cycle.js": {"script styles": 2},
    "www/js/drawer.js": {"script styles": 18, "colours": 1},   # the colour: a record's text in its own frame (srcdoc)
    "www/js/health.js": {"script styles": 26},
    "www/js/heatmap.js": {"script styles": 6},
    "www/js/journal.js": {"script styles": 3},
    "www/js/medlog.js": {"script styles": 1},
    "www/js/meds.js": {"script styles": 7},
    "www/js/nav.js": {"script styles": 1},
    "www/js/overview.js": {"script styles": 15},
    "www/js/rename.js": {"script styles": 2},
    "www/js/timeline.js": {"script styles": 1},
    "www/js/trends.js": {"script styles": 9},
    "www/js/util.js": {"script styles": 1},   # el() itself, where a style object is applied
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
FORMATTER = r"""\s*["'`](?:currency|percent|decimal|unit|long|short|narrow)["'`]"""   # {style: "currency"} isn't a style
SCRIPT_STYLE = re.compile(r"""(?<![\w$.-])["']?style["']?\s*:(?!%s)""" % FORMATTER +   # style: and "style": (not font-style:)
                          r"|\.style\.(?:[A-Za-z]+\s*[-+]?=(?!=)|setProperty\s*\()|\.style\s*\[[^\]]*\]\s*[-+]?=(?!=)"
                          r"""|\bsetAttribute(?:NS\s*\([^,()]*,|\()\s*["'`]style["'`]|\bObject\.assign\(\s*[^,()]*\.style\s*,""")
# colours in a script's strings (the rules are in the docstring)
JS_HEX = re.compile(r"(?<![\w&/#])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![\w-])")
JS_FUNC = re.compile(r"\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(", re.I)
NAME = re.compile(r"\b(?:" + "|".join(NAMED) + r")\b", re.I)
COLOUR_PROP = r"(?:color|fill|stroke|background|border|outline|shadow|decoration|caret|accent|rule)"
JS_DECL = re.compile(r"(?<![\w-])[a-z-]*" + COLOUR_PROP + r"[a-z-]*\s*:\s*([^;{}]*)")   # fill:red inside a string
COLOUR_KEY = re.compile(COLOUR_PROP + r"""[\w-]*["']?\s*(?:[:,]|[!=]?==?)\s*$""", re.I)   # fill: "…", ("stroke", "…")
NOT_A_COLOUR = re.compile(r"""(?:(?:\$\$?|querySelector(?:All)?|closest|matches|getElementById)\s*\(\s*"""
                          r"""|\b(?:href|hash)["']?\s*(?:[:=]|[!=]==?))\s*$""")   # an id or a fragment
# a page's tags, its style="..." and the attributes that take a colour
TAG = re.compile(r"""<[a-zA-Z][\w:-]*(?:\s+[^\s"'>=/]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'>]+))?)*\s*/?>""")
ATTR = re.compile(r"""\s([^\s"'>=/]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")
COLOUR_ATTR = ("fill", "stroke", "color", "bgcolor", "stop-color", "flood-color", "lighting-color")
DISTINCT = ("px font sizes", "radii", "shadows")   # counted by their different values, not their uses

FIX = {
    "colours": "use a colour token from www/css/tokens.css (var(--ink), var(--accent)...); if none fits, add one there, "
               "light and dark",
    "px font sizes": "use a size the file already has, or em/rem (a type scale of tokens comes with the redesign)",
    "radii": "use a radius the file already has (radius tokens come with the redesign)",
    "shadows": "use a shadow the file already has (elevation tokens come with the redesign)",
    "script styles": "give the element a class and style it in www/css/app.css with the tokens, instead of setting "
                     "its style from the script",
    "style attributes": "give the element a class and style it in www/css/app.css with the tokens, instead of a "
                        "style=\"...\" attribute",
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


def html_found(html):
    """{what: [(line, value), ...]} for one page: its <style>, its style="..." attributes and its colour attributes"""
    out = {}
    def add(found):
        for what, hits in found.items():
            out.setdefault(what, []).extend(hits)
    for m in re.finditer(r"<style[^>]*>(.*?)</style>", html, re.S | re.I):
        add(css_found(m.group(1), html.count("\n", 0, m.start(1)) + 1))
    markup = re.sub(r"<!--.*?-->|<(style|script)\b[^>]*>.*?</\1\s*>", lambda m: "\n" * m.group().count("\n"), html,
                    flags=re.S | re.I)
    for tag in TAG.finditer(markup):
        for a in ATTR.finditer(tag.group()):
            name, value = a.group(1).lower(), next(v for v in a.group(2, 3, 4) if v is not None)
            line = markup.count("\n", 0, tag.start() + a.start(1)) + 1
            if name == "style":
                out.setdefault("style attributes", []).append((line, f'style="{value}"'))
                add(css_found("{" + value + "}", line))
            elif name in COLOUR_ATTR:
                add({"colours": [(line, c) for c in COLOUR.findall(VAR.sub("", value))]})
    return out


REGEX_AFTER = ("return", "typeof", "case", "in", "of", "void", "delete", "throw", "new", "yield", "await", "do", "else")


def js_strings(js):
    """(offset, text) of every string in a script (comments already blanked): '...', "..." and each text part of a
    template, the strings inside its ${...} on their own. A / where a value can start begins a regex, skipped."""
    out, i, n, prev, inside = [], 0, len(js), "", []   # inside: for each open ${, the depth of braces within it

    def template(i):   # the text from i to the template's end or its next ${
        j = i
        while j < n and js[j] != "`" and not js.startswith("${", j):
            j += 2 if js[j] == "\\" else 1
        out.append((i, js[i:j]))
        return (j + 2, True) if js.startswith("${", j) else (j + 1, False)

    while i < n:
        c = js[i]
        if c in "\"'":
            j = i + 1
            while j < n and js[j] not in (c, "\n"):
                j += 2 if js[j] == "\\" else 1
            out.append((i + 1, js[i + 1:j]))
            i, prev = j + 1, "a"
        elif c == "`" or (c == "}" and inside and inside[-1] == 0):
            if c == "}":
                inside.pop()
            i, opened = template(i + 1)
            if opened:
                inside.append(0)
            prev = "(" if opened else "a"
        elif c == "/" and (prev == "" or prev in REGEX_AFTER or (len(prev) == 1 and prev in "(,=:[!&|?{};+-*%<>~^")):
            j, in_class = i + 1, False
            while j < n and js[j] != "\n" and (js[j] != "/" or in_class):
                if js[j] == "\\":
                    j += 1
                elif js[j] in "[]":
                    in_class = js[j] == "["
                j += 1
            i, prev = j + 1, "a"
        elif c.isalnum() or c in "_$":
            prev = re.match(r"[\w$]+", js[i:]).group()
            i += len(prev)
        else:
            if inside and c in "{}":
                inside[-1] += 1 if c == "{" else -1
            if not c.isspace():
                prev = c
            i += 1
    return out


def js_colours(js):
    """[(line, colour)] written in a script's strings (comments already blanked); the rules are in the docstring"""
    out = []
    for at, s in js_strings(js):
        before = js[max(0, at - 60):at].rstrip("\"'`")   # the code just before the string
        hits = [] if NOT_A_COLOUR.search(before) else [(m.start(), m.group()) for m in JS_HEX.finditer(s)]
        hits += [(m.start(), m.group()) for m in JS_FUNC.finditer(s)]
        bare = re.sub(r"--[\w-]+", lambda v: " " * len(v.group()), s)   # var(--red) and "--red" aren't red
        if COLOUR_KEY.search(before):
            hits += [(m.start(), m.group()) for m in NAME.finditer(bare)]
        else:
            hits += [(d.start(1) + m.start(), m.group()) for d in JS_DECL.finditer(bare) for m in NAME.finditer(d.group(1))]
        out += [(js.count("\n", 0, at + o) + 1, v) for o, v in sorted(hits)]
    return out


def js_found(js):
    """{what: [(line, value), ...]} for one script"""
    js = blank_comments(js, JS_COMMENTS)
    return {"script styles": [(js.count("\n", 0, m.start()) + 1, m.group()) for m in SCRIPT_STYLE.finditer(js)],
            "colours": js_colours(js)}


def files(ext):
    return sorted(glob.glob(os.path.join(ROOT, "www", "**", "*." + ext), recursive=True))


def found():
    """{file: {what: [(line, value), ...]}} over the stylesheets, the pages and the scripts, under www/ at any depth"""
    out = {}
    for path in files("css"):
        if rel(path) != TOKENS:
            with open(path, encoding="utf-8") as f:
                out[rel(path)] = css_found(f.read())
    for path in files("html"):
        with open(path, encoding="utf-8") as f:
            out[rel(path)] = html_found(f.read())
    for path in files("js"):
        with open(path, encoding="utf-8") as f:
            out[rel(path)] = js_found(f.read())
    return {f: {w: h for w, h in c.items() if h} for f, c in out.items()}


def hex6(c):
    c = "".join(c.lower().split())
    return "#" + "".join(ch * 2 for ch in c[1:]) if re.fullmatch(r"#[0-9a-f]{3,4}", c) else c


def token_hint(value):
    """' (the light theme's var(--raised))' when a colour written out is exactly a token's light value"""
    with open(os.path.join(ROOT, TOKENS), encoding="utf-8") as f:
        light = re.search(r":root\s*\{(.*?)\}", blank_comments(f.read(), r"/\*.*?\*/"), re.S)
    names = [p for p, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+)", light.group(1) if light else "") if hex6(v) == hex6(value)]
    return f" (the light theme's {' or '.join(f'var({p})' for p in names[:3])})" if names else ""


def count(what, hits):
    return len({v for _, v in hits}) if what in DISTINCT else len(hits)


def where(what, hits):
    """where to look: each line and its value (a colour that is a token's light value says which), or for the distinct
    counts each value with its first line, the rarest first (a new one is used once)"""
    if what not in DISTINCT:
        hint = token_hint if what == "colours" else lambda v: ""
        return "; ".join(f"line {line}: {v}{hint(v)}" for line, v in hits[:40]) + (" ..." if len(hits) > 40 else "")
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

    def test_one_focus_ring(self):
        """the ring for whatever has keyboard focus is one rule, in base.css, which every page loads (#310): no stylesheet
        or page's <style> rings focus again for every element or every element of a kind (a component's own ring is fine)"""
        rings = []
        def text(p):
            with open(p, encoding="utf-8") as f:
                return f.read()
        sources = [(rel(p), text(p)) for p in sorted(glob.glob(os.path.join(ROOT, "www", "css", "*.css")))]
        sources += [(rel(p), "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", text(p), re.S | re.I)))
                    for p in sorted(glob.glob(os.path.join(ROOT, "www", "*.html")))]
        for path, css in sources:
            for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", blank_comments(css, r"/\*.*?\*/")):
                if "outline" in m.group(2) and any(re.fullmatch(r"(?:\*|[a-z]+)?(?::where\()?(?:\*|[a-z]+)?:focus(?:-visible)?\)?", s.strip())
                                                   for s in m.group(1).split(",")):
                    rings.append(f"{path}: {' '.join(m.group(1).split())}")
        self.assertEqual(rings, ["www/css/base.css: :where(:focus-visible)"],
                         "one focus ring, in www/css/base.css; a page or component sets its own only to look different")
        for path in sorted(glob.glob(os.path.join(ROOT, "www", "*.html"))):
            with open(path, encoding="utf-8") as f:
                links = re.findall(r'<link rel="stylesheet" href="(?:\.\./)?css/([\w.-]+)">', f.read())
            self.assertEqual(links[:2], ["tokens.css", "base.css"], f"{rel(path)} loads tokens.css, then base.css, first")

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

    def test_no_way_round_it_in_scripts(self):   # #307
        styles = ['el("p", {"style": {color: "var(--ink)"}})', "el('p', {'style': 'left:0'})", 'p.setAttribute("style", s)',
                  "p.setAttributeNS(null, 'style', s)", "Object.assign(p.style, {left: 0})", 'p.style["left"] = x',
                  "p.style[k] = v", "p.style.left += 4", "el('p', {style: ''})"]
        for js in styles:
            self.assertEqual(len(js_found(js)["script styles"]), 1, js)
        for js in ['n.toLocaleString(undefined, {style: "currency", currency: "USD"})', "new Intl.NumberFormat(u, {style: 'percent'})",
                   'new Intl.RelativeTimeFormat(u, {style: "narrow"})', '{unit: "kilogram", style: `unit`}', 'p.textContent = "font-style: italic"',
                   'if (p.style[k] === v) {}', "el('p', {fontStyle: 1})"]:
            self.assertEqual(js_found(js)["script styles"], [], js)

        colours = {'el("svg:rect", {fill: "#ff0000"})': ["#ff0000"], 'el("i", {stroke: "white"})': ["white"],
                   'r.setAttribute("stroke", "Black")': ["Black"], "el('i', {'stop-color': '#0f08'})": ["#0f08"],
                   'p.style.backgroundColor = "tomato"': ["tomato"], 'el("p", {style: "border:1px solid red;color:var(--ink)"})': ["red"],
                   'el("rect", {style: `fill:${on ? "#abc" : "rgb(1 2 3)"}`})': ["#abc", "rgb("],
                   'f.srcdoc = `<style>body{color:#111}</style>${html}<p style="color:hsl(0 0% 0%)">`': ["#111", "hsl("],
                   "const C = ['#123456', '#12345678']": ["#123456", "#12345678"]}
        for js, want in colours.items():
            self.assertEqual([v for _, v in js_found(js)["colours"]], want, js)
        for js in ['$("#add").onclick = go', "document.querySelector('#bed .x')", 'p.closest("#fade")', 'a.href = "#cafe"',
                   'el("a", {href: "#deed"})', 'if (location.hash === "#face") {}', 'el("p", {id: "add-form", text: "#add-form"})',
                   'p.textContent = "&#160;"', 'go("page.html#abc"); go("/#abc")', 'el("a", {href: "#list"})', '"#abcde #fade2"',
                   'el("input", {placeholder: "e.g. Red patch, 3 cm, left forearm"})', 'el("p", {class: "tag red"})',
                   'p.textContent = "white blood cells"', 's.replace(/["\']#fff/g, "")', '// fill: "#fff"\n/* stroke: "red" */',
                   'el("i", {fill: "var(--red)", style: "fill:var(--red);background:color-mix(in srgb, var(--muted) 12%, transparent)"})',
                   'el("rect", {fill: "transparent", stroke: "currentColor"})']:
            self.assertEqual(js_found(js)["colours"], [], js)
        self.assertEqual(js_found('a = 1;\nb = `x ${c ? `y` : "z"}`;\nel("i", {fill: "#f00"})')["colours"], [(3, "#f00")],
                         "a template inside a template keeps the strings in step, and the lines right")

    def test_no_way_round_it_in_pages(self):   # #307
        html = """<!doctype html><meta name="theme-color" content="#f9f9f7"><style>p{color:#fff}</style>
<p style="color:#f00;margin:0">a</p><div style='border-radius:3px; font-size:11px'>b</div>
<svg><path fill="red" stroke="var(--ink)"/><path fill=none stroke=#00ff00 /></svg>
<!-- <p style="color:red"> --><p>Never write style="..." here, or a fill="red"</p><p title="a > b" style="top:0">c</p>"""
        got = html_found(html)
        self.assertEqual(got["style attributes"], [(2, 'style="color:#f00;margin:0"'), (2, "style=\"border-radius:3px; font-size:11px\""),
                                                   (4, 'style="top:0"')])
        self.assertEqual(got["colours"], [(1, "#fff"), (2, "#f00"), (3, "red"), (3, "#00ff00")])
        self.assertEqual((got["radii"], got["px font sizes"]), ([(2, "3px")], [(2, "11px")]))

    def test_no_way_round_it_in_subfolders(self):   # #307
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            for path, text in {"www/css/kit/x.css": ".a{color:#f00}", "www/js/charts/y.js": 'el("rect", {fill: "#f00"})',
                               "www/kit/p.html": '<p style="top:0">', "www/css/tokens.css": ":root{--ink:#f00}"}.items():
                os.makedirs(os.path.dirname(os.path.join(tmp, path)), exist_ok=True)
                with open(os.path.join(tmp, path), "w", encoding="utf-8") as f:
                    f.write(text)
            with mock.patch.object(sys.modules[__name__], "ROOT", tmp):
                self.assertEqual(found(), {"www/css/kit/x.css": {"colours": [(1, "#f00")]},
                                           "www/js/charts/y.js": {"colours": [(1, "#f00")]},
                                           "www/kit/p.html": {"style attributes": [(1, 'style="top:0"')]}})
                self.assertEqual(where("colours", [(1, "#f00"), (2, "#FF0000")]),
                                 "line 1: #f00 (the light theme's var(--ink)); line 2: #FF0000 (the light theme's var(--ink))")


if __name__ == "__main__":
    if "--counts" in sys.argv[1:]:
        print("ALLOWED = {")
        for path, c in found().items():
            if c:
                print(f"    {path!r}: {{" + ", ".join(f"{w!r}: {count(w, h)}" for w, h in c.items()) + "},")
        print("}")
        sys.exit(0)
    unittest.main(verbosity=2)
