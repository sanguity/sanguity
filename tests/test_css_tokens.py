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
  px spacing     the different px values in padding, margin and gap (row-gap, column-gap, grid-gap, a side's and a
                 logical longhand too), outside var() (#333). 0 and a nudge of 1, 2 or 3 px (or -1 to -3) don't count:
                 the redesign left nudges of that size on purpose (a 24 px target kept from growing its line, a hairline
                 lined up). Positional offsets (top, left, right, bottom, inset) and scroll-margin/scroll-padding aren't
                 counted: they place something (a menu under its button, a sticky sidebar under the bar) or clear a
                 sticky bar, measured from what they place it against, not spacing between things.
                 (These four have tokens, in tokens.css (#255, #256), and are counted by their different values: the
                 off-scale ones a file still has are listed in ALLOWED, so reusing one adds nothing and a new one fails.)
  font weights   each number given by font-weight or before the font shorthand's size ("600 12px/1.4 ..."); the
                 keywords (bold, normal) aren't numbers, and an @font-face rule's weights say what a font file covers,
                 not how text looks, so they aren't counted either
  durations      each time other than 0 in transition, animation and their -duration and -delay longhands (.3s, 150ms)
                 (the weights and durations have tokens too, so each one written out counts)
  style attributes  each style="..." on a page's elements
and in every script, comments skipped:
  script styles  each style handed to el() (a `style:` or "style": key in an object literal, an object or a string),
                 each write to an element's style (`.style.left = ...`, `.style[k] = ...`, `.style.setProperty(...)`,
                 `.style.cssText = ...`, `.style = "..."`, `Object.assign(x.style, ...)`) and each
                 `setAttribute("style", ...)`.
                 A value the page computes as it runs (a position, a bar's width) counts too: the number is what the
                 redesign brings down, not a ban. A `style:` whose value is a formatter's keyword ("currency",
                 "percent", "decimal", "unit", "long", "short", "narrow": Intl.NumberFormat, toLocaleString and the
                 like) isn't a style and isn't counted.
  colours        a colour written in a string ('...', "..." or a template's text), where it would reach an element
                 (el("svg:rect", {fill: "#f00"}), a style string, an iframe's srcdoc):
                 - a hex colour of 3, 4, 6 or 8 digits standing alone: not part of a longer word or id ("#add-form",
                   "#fade2"), not after a letter, digit, &, / or # ("&#160;", "page.html#abc", "/#abc"), and not in a
                   string handed to $(), $$(), querySelector(), closest(), matches() or getElementById(), or given
                   to an href or a hash: those are ids and fragments, not colours ("#list" isn't hex anyway); and
                   not in running prose, after a plain word and a space ("Issue #252", "Room #101"), unless the word
                   is one of CSS's that come before a colour ("1px solid #fff", "auto #000"), the hex is in a
                   declaration ("color: #fff") or the string is given to a colour key (border: "x #fff");
                 - a colour function, rgb() to oklch(), anywhere in a string;
                 - a named colour only where a colour goes: in a declaration of a colour property inside the string
                   ("fill:red", "border:1px solid white"), or anywhere in a string given to a colour key or
                   attribute (fill: "red", backgroundColor: "white", setAttribute("stroke", "black")), the branches
                   of a ternary or a default there too (fill: on ? "red" : "blue", x.style.color = c || "red"), or in
                   a colour attribute of markup inside the string ('<rect fill="red"/>'). So the word in
                   "e.g. Red patch, 3 cm" isn't one.
                 To find the strings, a / where a value can start begins a regex, skipped; after a name, a ")" or
                 a ++ or -- it divides (a++ / 2).
  px spacing, font weights, durations
                 as in a stylesheet, wherever a script writes them (#333): in a declaration inside any string
                 ("padding:4px 0", "font-weight:600", a srcdoc's <style>), and in a style it sets: each key of an object
                 handed to el() or Object.assign(x.style, ...), a ternary's branches there too (marginTop: "10px",
                 fontWeight: 500; a bare number in a spacing key is read as px, as el() would set it),
                 x.style.marginTop = ... and x.style.setProperty("margin-top", ...).
Known limits (#321): what the scanner can't see, so a review has to.
  - A colour put together as the page runs ("#" + hex, "r" + "ed", a template's ${c} with c from elsewhere) or kept in a
    variable first (const c = "red"; el("i", {fill: c})): only a hex or a colour function in the variable's string counts.
  - A ternary or default more than about 60 characters from its colour key, or one whose condition has a comma, a
    brace or a ":" (a call with two arguments), isn't followed back to the key.
  - A regex right after a ")" (if (x) /re/.test(s)) is taken for a division, so strings later on that line are missed.
  - Spacing, a weight or a time kept in a variable first (const m = "6px"; el("p", {style: {margin: m}})), or set
    through x.style[k] = ...: the value isn't followed back to the style. And "margin: 7px" in running text in a
    string counts, as a declaration would.
  - A style reached another way: x["style"] = ..., x.attributes, a CSSStyleSheet (insertRule, adoptedStyleSheets), a
    <style> element built by a script (its text is read as strings, so a hex or a colour declaration in it still counts).
  - A hex in prose after a CSS word ("see the top #252") still counts, and one after a lowercase "word:" counts as a
    declaration's ("note: issue #252"). A named colour in markup counts only in a colour attribute (not title="red"),
    and any " fill=red" in a string is taken for markup, prose or not.
  - The focus ring: a rule ringing every element of a kind through an attribute ([type=checkbox]:focus) passes as a
    component's own, as does one under a class (.app :focus-visible); :has(...) isn't opened up, only searched.

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
    # its two colours: a video's black letterbox (#000) and the sticky Save bar's shadow upward (rgba(0,0,0,.35))
    # its two px font sizes are outside the type scale on purpose (#255): the journal's body figure, in its own units, 9 px
    # for its sides and 7.5 px for a region's count (the heatmap's ? is an icon since #257)
    # its radius is a colour key's or swatch's 2 px (a data mark, not a component); its shadows are bars drawn inset
    # (a selected item's accent bar, a seg's underline), the sticky Save bar's shadow upward and the offline flash (#256)
    # its px spacing is four negative margins (#333): -4, -6 and -8 px tuck a note under what it's about (a chart's hint,
    # the tiles' range note, a stats note, More about this) or pull a phone's page title up under the brand; -5 and -6 px
    # keep a 24 px target (the ✎, a test link, Reset filters on a phone) from making its line taller
    # its durations: the offline banner's .6s flash, and the reduced-motion rule's 1ms, which stops animations but still
    # lets them end (an animationend handler runs)
    "www/css/app.css": {"colours": 2, "px font sizes": 2, "radii": 1, "shadows": 5, "px spacing": 4, "durations": 2},
    # a page's one colour (sign-in's and setup's too: their button's text is --on-accent now) is the brand mark's white
    # highlight, stroke="#fff"
    "www/account.html": {"colours": 1},
    "www/admin.html": {"colours": 1},
    "www/guide.html": {"colours": 1},
    "www/index.html": {"colours": 1, "style attributes": 3},
    "www/login.html": {"colours": 1},
    "www/settings.html": {"colours": 1},
    "www/setup.html": {"colours": 1},
    "www/share.html": {"colours": 1},
    "www/sharing.html": {"colours": 1},
    "www/sources.html": {"colours": 1},
    "www/js/app.js": {"script styles": 2},
    "www/js/core.js": {"script styles": 11},
    # its spacing and weights are on lines #333 left alone: margins of 8 and 12 px (on the scale, written out) and the
    # running totals' end labels' and the bar lists' weights (600, 500)
    "www/js/costs.js": {"script styles": 38, "px spacing": 2, "font weights": 3},
    "www/js/cycle.js": {"script styles": 2},
    # the colour and the spacing are a record's own frame (srcdoc), which doesn't load the tokens: its text, its margin
    # and its table cells' padding
    "www/js/drawer.js": {"script styles": 17, "colours": 1, "px spacing": 2},
    "www/js/health.js": {"script styles": 15},
    "www/js/heatmap.js": {"script styles": 5},
    "www/js/journal.js": {"script styles": 3},
    "www/js/medlog.js": {"script styles": 1},
    "www/js/meds.js": {"script styles": 7},
    "www/js/nav.js": {"script styles": 1},
    "www/js/overview.js": {"script styles": 7, "px spacing": 1},   # 8 px margins, on the scale but written out
    "www/js/rename.js": {"script styles": 2},
    "www/js/timeline.js": {"script styles": 1},
    "www/js/trends.js": {"script styles": 8},
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
                          r"|\.style\s*[-+]?=(?!=)"   # x.style = "left:0" sets the attribute
                          r"""|\bsetAttribute(?:NS\s*\([^,()]*,|\()\s*["'`]style["'`]|\bObject\.assign\(\s*[^,()]*\.style\s*,""")
# colours in a script's strings (the rules are in the docstring)
JS_HEX = re.compile(r"(?<![\w&/#])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![\w-])")
JS_FUNC = re.compile(r"\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(", re.I)
NAME = re.compile(r"\b(?:" + "|".join(NAMED) + r")\b", re.I)
COLOUR_PROP = r"(?:color|fill|stroke|background|border|outline|shadow|decoration|caret|accent|rule)"
JS_DECL = re.compile(r"(?<![\w-])[a-z-]*" + COLOUR_PROP + r"[a-z-]*\s*:\s*([^;{}]*)")   # fill:red inside a string
# a ternary's or a default's branches, between a colour key and its string: fill: on ? "red" : "blue", c || "red"
_ATOM = r"""(?:"[^"\n]*"|'[^'\n]*'|[^,;{}\n?:|&"'`])"""
TERNARY = r"(?:%s*(?:\?\??|\|\||&&)\s*(?:%s*:\s*)?)*" % (_ATOM, _ATOM)
COLOUR_KEY = re.compile(COLOUR_PROP + r"""[\w-]*["']?\s*(?:[:,]|[!=]?==?)\s*""" + TERNARY + "$", re.I)   # fill: "…", ("stroke", "…")
# a colour attribute in markup written in a string: '<rect fill="red"/>', `<stop stop-color=${c}>`
JS_MARKUP = re.compile(r"""(?<=\s)(?:stop-color|flood-color|lighting-color|fill|stroke|color|bgcolor)\s*=\s*\\?"""
                       r"""(?:"([^"]*)"|'([^']*)'|([^\s"'>\\]+))""", re.I)
# a hex after a plain word and a space is prose ("Issue #252", "Room #101"), not a colour: unless the word is one of CSS's
# that come before a colour (1px solid #fff), or the hex is in a declaration (color: #fff) or a colour key's string
PROSE = re.compile(r"(?<![\w-])([A-Za-z]+)\s+$")
CSS_WORDS = set("""solid dashed dotted double groove ridge inset outset wavy underline overline thin medium thick none
hidden auto center top bottom left right repeat cover contain fixed scroll local at to in""".split())
IN_DECL = re.compile(r"""(?<![\w-])[a-z-]+\s*:[^;{}"'<>]*$""")
NOT_A_COLOUR = re.compile(r"""(?:(?:\$\$?|querySelector(?:All)?|closest|matches|getElementById)\s*\(\s*"""
                          r"""|\b(?:href|hash)["']?\s*(?:[:=]|[!=]==?))\s*$""")   # an id or a fragment
# a page's tags, its style="..." and the attributes that take a colour
TAG = re.compile(r"""<[a-zA-Z][\w:-]*(?:\s+[^\s"'>=/]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'>]+))?)*\s*/?>""")
ATTR = re.compile(r"""\s([^\s"'>=/]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")
COLOUR_ATTR = ("fill", "stroke", "color", "bgcolor", "stop-color", "flood-color", "lighting-color")
# spacing, weights and motion (#333): the properties that take spacing (positional offsets and scroll-margin/-padding
# aren't: they place a thing or clear a sticky bar, measured from what they clear), a px value in one (a nudge of 3 px
# or less is fine), a bare number (a weight, or the font shorthand's before its size) and a time
SPACING = re.compile(r"(?:padding|margin)(?:-(?:top|right|bottom|left|inline|block)(?:-(?:start|end))?)?"
                     r"|(?:row-|column-|grid-|grid-row-|grid-column-)?gap")
MOTION = re.compile(r"(?:transition|animation)(?:-duration|-delay)?")
PX_SPACE = re.compile(r"(?<![\w.#-])-?(?:\d*\.)?\d+px\b")
NUMBER = re.compile(r"(?<![\w.#/$-])-?(?:\d*\.)?\d+(?![\w.%/(-])")
TIME = re.compile(r"(?<![\w.#-])(?:\d*\.)?\d+m?s\b")
DISTINCT = ("px font sizes", "radii", "shadows", "px spacing")   # counted by their different values, not their uses

FIX = {
    "colours": "use a colour token from www/css/tokens.css (var(--ink), var(--accent)...); if none fits, add one there, "
               "light and dark",
    "px font sizes": "use a size token from www/css/tokens.css, var(--fs-1) (11.5 px) to var(--fs-7) (28 px), or em",
    "radii": "use a radius token: var(--r-sm), var(--r-md), var(--r-lg) or var(--r-pill)",
    "shadows": "use an elevation token: var(--shadow-1) (raised a little) or var(--shadow-2) (floating: menus, dialogs)",
    "px spacing": "use a spacing token, var(--sp-1) (4 px) to var(--sp-7) (48 px), or a nudge of 3 px or less; in a "
                  "script, give the element a class and space it in www/css/app.css",
    "font weights": "use a weight token: var(--fw-regular), var(--fw-medium), var(--fw-semibold) or var(--fw-bold)",
    "durations": "use a motion token: var(--dur-1) (120 ms: hover, press, menus) or var(--dur-2) (200 ms: the drawer), "
                 "with var(--ease-out)",
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


def on_scale(prop, value):
    """{what: [value, ...]} that one declaration writes out instead of a spacing, weight or motion token (#333): px
    spacing past a 3 px nudge, a numeric weight, a time other than 0 (quoted text and var() already taken out)"""
    out = {}
    if SPACING.fullmatch(prop):
        out["px spacing"] = [v for v in PX_SPACE.findall(value) if abs(float(v[:-2])) > 3]
    if prop in ("font-weight", "font"):   # the shorthand's weight comes before its size: "600 12px/1.4 ..."
        out["font weights"] = NUMBER.findall(value if prop == "font-weight" else value.split("/")[0])
    if MOTION.fullmatch(prop):
        out["durations"] = [t for t in TIME.findall(value) if float(t.rstrip("ms"))]
    return out


def css_found(css, first_line=1):
    """{what: [(line, value), ...]} for one stylesheet's text"""
    out = {"colours": [], "px font sizes": [], "radii": [], "shadows": [], "px spacing": [], "font weights": [], "durations": []}
    css = re.sub(r"@font-face\s*\{[^{}]*\}", lambda m: "\n" * m.group().count("\n"),   # a font's own weights, not a style's
                 blank_comments(css, r"/\*.*?\*/"))
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
        for what, values in on_scale(prop, own).items():
            out[what] += [(line, v) for v in values]
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
        elif js.startswith(("++", "--"), i):   # a++ / 2 divides: no regex starts after ++ or --
            i, prev = i + 2, ")"
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


def prose(text):
    """whether a hex after this text (a string's, up to the hex) is in running prose, as in "Issue #252" """
    word = PROSE.search(text)
    return bool(word) and word.group(1).lower() not in CSS_WORDS and not IN_DECL.search(text)


def js_colours(js):
    """[(line, colour)] written in a script's strings (comments already blanked); the rules are in the docstring"""
    out = []
    for at, s in js_strings(js):
        before = js[max(0, at - 60):at].rstrip("\"'`")   # the code just before the string
        key = COLOUR_KEY.search(before)
        hits = [] if NOT_A_COLOUR.search(before) else [(m.start(), m.group()) for m in JS_HEX.finditer(s)
                                                       if key or not prose(s[:m.start()])]
        hits += [(m.start(), m.group()) for m in JS_FUNC.finditer(s)]
        bare = re.sub(r"--[\w-]+", lambda v: " " * len(v.group()), s)   # var(--red) and "--red" aren't red
        if key:
            hits += [(m.start(), m.group()) for m in NAME.finditer(bare)]
        else:
            hits += [(d.start(1) + m.start(), m.group()) for d in JS_DECL.finditer(bare) for m in NAME.finditer(d.group(1))]
            hits += [(a.start(g) + m.start(), m.group()) for a in JS_MARKUP.finditer(bare) for g in (1, 2, 3)
                     if a.group(g) is not None for m in NAME.finditer(a.group(g))]
        out += [(js.count("\n", 0, at + o) + 1, v) for o, v in sorted(hits)]
    return out


def past_string(js, i):
    """the index just past the string that starts at js[i] (a template's ${...} included)"""
    q, j = js[i], i + 1
    while j < len(js) and js[j] != q:
        if js[j] == "\\":
            j += 1
        elif q == "`" and js.startswith("${", j):
            j = scan(js, j + 2, "}")
        j += 1
    return j + 1


def scan(js, i, stops):
    """the index of the first of stops from i that isn't in a string or in brackets opened after i, or of a bracket
    closing one opened before i, or the end"""
    depth = 0
    while i < len(js):
        c = js[i]
        if c in "\"'`":
            i = past_string(js, i)
            continue
        if depth == 0 and c in stops:
            return i
        depth += (c in "([{") - (c in ")]}")
        if depth < 0:
            return i
        i += 1
    return i


def kebab(name):
    return re.sub(r"[A-Z]", lambda c: "-" + c.group().lower(), name).lower()   # marginTop: margin-top


def style_values(expr):
    """(offset, value) of what a script's expression can set a style to: each string in it, and each number outside
    them as the style would read it (a weight's 500; a margin's 8, which is 8px to el())"""
    bare = re.sub(r""""[^"\n]*"|'[^'\n]*'|`[^`]*`""", lambda m: " " * len(m.group()), expr)
    return js_strings(expr) + [(m.start(), m.group()) for m in NUMBER.finditer(bare)]


def js_objects(js, start, end):
    """(offset, property, expression) for each key of the object literals between start and end: {marginTop: "4px"}"""
    out, i = [], start
    while i < end:
        if js[i] in "\"'`":
            i = past_string(js, i)
        elif js[i] == "{":
            close, k = scan(js, i + 1, "}"), i + 1
            while k < close:
                e = scan(js, k, ",}")
                m = re.match(r"""\s*(?:["'`]([\w-]+)["'`]|([A-Za-z_$][\w$]*))\s*:""", js[k:e])
                if m:
                    out.append((k + m.end(), kebab(m.group(1) or m.group(2)), js[k + m.end():e]))
                k = e + 1
            i = close + 1
        else:
            i += 1
    return out


def js_scale(js):
    """{what: [(line, value), ...]} for the spacing, weights and times a script writes out (comments already blanked):
    in a declaration inside any string ("padding:4px 0", a srcdoc's <style>), and in a style it sets: an object handed
    to el() or Object.assign(x.style, ...) ({gap: "6px", fontWeight: 500}, a ternary's branches there too),
    x.style.marginTop = ... and x.style.setProperty("margin-top", ...)"""
    out = {"px spacing": [], "font weights": [], "durations": []}
    line = lambda at: js.count("\n", 0, at) + 1

    def add(at, prop, values):
        for off, v in values:
            v = v + "px" if re.fullmatch(NUMBER.pattern, v) and SPACING.fullmatch(prop) else VAR.sub("", v)
            for what, hits in on_scale(prop, v).items():
                out[what] += [(line(at + off), h) for h in hits]

    for at, s in js_strings(js):
        if ":" in s:
            for ln, prop, value in declarations("{" + s + "}", line(at)):
                for what, hits in on_scale(prop, VAR.sub("", re.sub(r"\"[^\"]*\"|'[^']*'", '""', value))).items():
                    out[what] += [(ln, h) for h in hits]
    for m in SCRIPT_STYLE.finditer(js):
        one = re.match(r"\.style\.([A-Za-z]+)\s*[-+]?=", m.group())
        if one:   # x.style.marginTop = ...
            add(m.end(), kebab(one.group(1)), style_values(js[m.end():scan(js, m.end(), ";\n,)}")]))
        elif m.group().endswith("("):   # x.style.setProperty("margin-top", ...)
            args = js[m.end():scan(js, m.end(), ")")]
            names = js_strings(args)
            if names:
                add(m.end(), names[0][1].lower(), [(o, v) for o, v in style_values(args) if o > names[0][0] + len(names[0][1])])
        elif not re.search(r"\[|=$|setAttribute", m.group()):   # style: {...} (or a ternary of them), Object.assign(x.style, {...})
            end = scan(js, m.end(), ")" if m.group().startswith("Object") else ",)}")
            for at, prop, expr in js_objects(js, m.end(), end):
                add(at, prop, style_values(expr))
    return {what: sorted(hits, key=lambda h: h[0]) for what, hits in out.items()}


def js_found(js):
    """{what: [(line, value), ...]} for one script"""
    js = blank_comments(js, JS_COMMENTS)
    return {"script styles": [(js.count("\n", 0, m.start()) + 1, m.group()) for m in SCRIPT_STYLE.finditer(js)],
            "colours": js_colours(js), **js_scale(js)}


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


def selector_list(text):
    """a selector list split at its own commas, not those inside :is(...) and the like"""
    out, depth, start = [], 0, 0
    for i, c in enumerate(text):
        depth += (c in "([") - (c in ")]")
        if c == "," and depth == 0:
            out.append(text[start:i]); start = i + 1
    return [s.strip() for s in out + [text[start:]]]


def closing(text, i):
    """the index just past the ) that closes the ( before i"""
    depth = 1
    while i < len(text) and depth:
        depth += (text[i] == "(") - (text[i] == ")"); i += 1
    return i


def opened(selector):
    """a selector's alternatives, its :is(...) and :where(...) lists opened up: ":is(a,b):focus" is a:focus and b:focus"""
    m = re.search(r":(?:is|where|matches|-webkit-any|-moz-any)\(", selector)
    if not m:
        return [selector]
    end = closing(selector, m.end())
    return [o for alt in selector_list(selector[m.end():end - 1]) for o in opened(selector[:m.start()] + alt + selector[end:])][:64]


def rings_every(selector):
    """whether a selector reaches whatever has focus, or every element of a kind with it: no class, id or attribute
    narrows it (a :not(...) doesn't), so "a:focus-visible", "body :focus-visible", ":is(button,input):focus" do, and
    ".btn:focus-visible", "svg[role=application]:focus" (a component's) don't"""
    for alt in opened(selector):
        while (m := re.search(r":not\(", alt)):
            alt = alt[:m.start()] + alt[closing(alt, m.end()):]
        if re.search(r":focus(?:-visible)?(?![\w-])", alt) and not re.search(r"[.#\[]", alt):
            return True
    return False


def focus_rings():
    """["file: selector"] for every rule under www/ (stylesheets and pages' <style>, at any depth) that sets an outline
    for every element or every element of a kind that has focus"""
    def text(p):
        with open(p, encoding="utf-8") as f:
            return f.read()
    sources = [(rel(p), text(p)) for p in files("css")]
    sources += [(rel(p), "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", text(p), re.S | re.I))) for p in files("html")]
    rings = []
    for path, css in sources:
        for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", blank_comments(css, r"/\*.*?\*/")):
            if "outline" in m.group(2) and any(rings_every(s) for s in selector_list(m.group(1))):
                rings.append(f"{path}: {' '.join(m.group(1).split())}")
    return rings


def hex6(c):
    c = "".join(c.lower().split())
    return "#" + "".join(ch * 2 for ch in c[1:]) if re.fullmatch(r"#[0-9a-f]{3,4}", c) else c


def token_hint(value):
    """' (the light theme's var(--raised))' when a colour written out is exactly a token's light value"""
    with open(os.path.join(ROOT, TOKENS), encoding="utf-8") as f:
        light = re.search(r":root\s*\{(.*?)\}", blank_comments(f.read(), r"/\*.*?\*/"), re.S)
    pairs = re.findall(r"(--[\w-]+)\s*:\s*([^;]+)", light.group(1) if light else "")
    first = lambda v: re.sub(r"^light-dark\(\s*([^,]+),.*", r"\1", v.strip())   # a light-dark() pair's light value (#254)
    names = [p for p, v in pairs if hex6(first(v)) == hex6(value)]
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
        or page's <style>, in a subfolder too, rings focus again for every element or every element of a kind (a
        component's own ring is fine), in a list (:is(button,input):focus-visible) or under another (body :focus-visible)
        included (#321)"""
        self.assertEqual(focus_rings(), ["www/css/base.css: :where(:focus-visible)"],
                         "one focus ring, in www/css/base.css; a page or component sets its own only to look different")
        for path in files("html"):
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
        self.assertEqual(got, {"colours": 3, "px font sizes": 2, "radii": 1, "shadows": 1, "px spacing": 0, "font weights": 1,
                               "durations": 0})
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
                   'if (p.style[k] === v) {}', "el('p', {fontStyle: 1})", "if (p.style == s) {}"]:
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

    def test_no_way_round_it_in_scripts_either(self):   # #321
        for js in ['x.style = "left:0"', "x.style += ';top:0'", "x.style=s"]:
            self.assertEqual(len(js_found(js)["script styles"]), 1, js)
        colours = {'el("i", {fill: on ? "red" : "blue"})': ["red", "blue"],
                   'x.style.color = on ? "red" : "blue"': ["red", "blue"],
                   'r.setAttribute("stroke", k === "a" ? "White" : a && b ? "black" : "navy")': ["White", "black", "navy"],
                   'el("i", {fill: c || "red", stroke: c ?? "#abc"})': ["red", "#abc"],
                   """const m = '<svg><rect width="4" fill="red"/></svg>'""": ["red"],
                   'f.srcdoc = `<stop stop-color=${c}><text x="0" fill=\'White\' stroke=navy>`': ["White", "navy"],
                   'q = "<rect fill=\\"red\\">"': ["red"],
                   'i++ / 2; el("i", {fill: "#f00"}); n-- / 2; el("i", {fill: "red"})': ["#f00", "red"],
                   'el("i", {style: {border: "1px solid #fff"}}); x.style.outline = "auto #123"': ["#fff", "#123"],
                   'const B = "1px dashed #abc", C = "color: see #def", D = "#999"': ["#abc", "#def", "#999"]}
        for js, want in colours.items():
            self.assertEqual([v for _, v in js_found(js)["colours"]], want, js)
        for js in ['el("p", {text: "Issue #252"})', 'say(`Fixed in issue #252, Room #101 at ${t}`)', "p.textContent = 'Bed #12a'",
                   'el("i", {fill: on ? "var(--red)" : "none", title: on ? "Red" : "Blue"})', 'go("?color=red")',
                   """const m = '<p class="red" title="white">'""", 'n = a++ / 2 + b; s = "Issue #fab"']:
            self.assertEqual(js_found(js)["colours"], [], js)

    def test_spacing_weights_and_durations(self):   # #333
        found_in = lambda css: {w: [v for _, v in h] for w, h in css_found(css).items() if w in ("px spacing", "font weights", "durations") and h}
        for css, want in {".a{margin:7px}": {"px spacing": ["7px"]}, ".a{font-weight:550}": {"font weights": ["550"]},
                          ".a{transition:.3s}": {"durations": [".3s"]}, ".a{padding:var(--sp-2) 10px;gap:6px 0}": {"px spacing": ["10px", "6px"]},
                          ".a{margin-top:-5px;row-gap:12px}": {"px spacing": ["-5px", "12px"]},
                          ".a{font:600 12px/1.4 var(--font-ui)}": {"font weights": ["600"]},
                          ".a{transition:opacity var(--dur-1) var(--ease-out),transform 150ms;animation-delay:2s}": {"durations": ["150ms", "2s"]}}.items():
            self.assertEqual(found_in(css), want, css)
        for css in [".a{margin:1px}", ".a{margin:var(--sp-2)}", ".a{padding:2px 3px 0;margin:-3px 0 -1px}", ".a{gap:calc(var(--sp-2) + 1px)}",
                    ".a{top:7px;left:10px;inset:5px;scroll-padding-top:44px;scroll-margin-top:30px}", ".a{font-weight:var(--fw-bold)}",
                    ".a{font-weight:bold;font:var(--fs-3)/1.45 var(--font-ui)}", ".a{transition-duration:0s;transition:none;animation:spin var(--dur-2) linear}",
                    ".a{width:7px;border:5px solid;line-height:1.5;font-size:var(--fs-2)}",
                    "@font-face{font-family:x;font-weight:100 900;src:url(x.woff2)}@font-face{font-family:y;font-weight:700}"]:
            self.assertEqual(found_in(css), {}, css)
        # pages: a <style> and a style attribute
        got = html_found('<style>p{margin:7px}</style><p style="font-weight:550;transition:.3s;padding:1px">')
        self.assertEqual([(w, [v for _, v in got[w]]) for w in ("px spacing", "font weights", "durations")],
                         [("px spacing", ["7px"]), ("font weights", ["550"]), ("durations", [".3s"])])
        # scripts: an object handed to el(), a style string, a declaration in any string, x.style.x =, setProperty
        js_in = lambda js: {w: [v for _, v in h] for w, h in js_found(js).items() if w in ("px spacing", "font weights", "durations") and h}
        for js, want in {'el("p", {style: {margin: "7px"}})': {"px spacing": ["7px"]},
                         'el("p", {style: {marginTop: "4px", gap: on ? "10px" : "6px", fontWeight: 550}})': {"px spacing": ["4px", "10px", "6px"], "font weights": ["550"]},
                         'el("p", {"style": {"padding-left": 18, transition: "opacity .3s"}})': {"px spacing": ["18px"], "durations": [".3s"]},
                         "el('i', {style: 'fill:var(--ink);font-weight:600;margin:0 7px'})": {"px spacing": ["7px"], "font weights": ["600"]},
                         'f.srcdoc = `<style>body{margin:14px}</style>${h}`': {"px spacing": ["14px"]},
                         'x.style.marginLeft = 8 + "px"; y.style.transition = "all .3s"': {"px spacing": ["8px"], "durations": [".3s"]},
                         'x.style.setProperty("margin-top", "7px")': {"px spacing": ["7px"]},
                         'Object.assign(x.style, {padding: "5px 2px"})': {"px spacing": ["5px"]},
                         'el("dd", {style: k ? {fontWeight: "700"} : null})': {"font weights": ["700"]}}.items():
            self.assertEqual(js_in(js), want, js)
        for js in ['el("p", {style: {margin: 0, marginTop: "1px", padding: "var(--sp-2) 3px", fontWeight: "var(--fw-bold)"}})',
                   'el("p", {class: "mt-2", style: {left: x + "px", top: "7px", width: "7px"}})', 'x.style.scrollPaddingTop = h + 4 + "px"',
                   '{type: "line", data: d, gap: 45, width: 2}', 'el("p", {text: "Leave 7px, a weight of 550 and .3s"})',
                   'el("p", {style: {transition: "none", animationDuration: "0s"}})', 'x.style.setProperty("--w", "7px")']:
            self.assertEqual(js_in(js), {}, js)

    def test_the_focus_ring_check_finds_what_it_should(self):   # #321
        for sel in ["*:focus", "a:focus-visible", ":where(:focus-visible)", ":is(button,input):focus-visible", "body :focus-visible",
                    "main :where(a, .x):focus", "button:not(.plain):focus-visible", "label:has(>input:focus-visible)"]:
            self.assertTrue(rings_every(sel), sel)
        for sel in [".btn:focus-visible", "svg[role=application]:focus-visible", "#add:focus", ":is(.a, .b):focus",
                    "form:focus-within", "input:not(:focus)", "a:hover"]:
            self.assertFalse(rings_every(sel), sel)
        self.assertEqual(selector_list(":is(button,input):focus-visible, .a , b"), [":is(button,input):focus-visible", ".a", "b"])
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            for path, text in {"www/css/base.css": ":where(:focus-visible){outline:2px solid}",
                               "www/css/kit/x.css": ".a:focus{outline:0}:is(button,input):focus-visible{outline:1px solid}",
                               "www/kit/p.html": "<style>body :focus-visible{outline:none}</style>"}.items():
                os.makedirs(os.path.dirname(os.path.join(tmp, path)), exist_ok=True)
                with open(os.path.join(tmp, path), "w", encoding="utf-8") as f:
                    f.write(text)
            with mock.patch.object(sys.modules[__name__], "ROOT", tmp):
                self.assertEqual(focus_rings(), ["www/css/base.css: :where(:focus-visible)",
                                                 "www/css/kit/x.css: :is(button,input):focus-visible",
                                                 "www/kit/p.html: body :focus-visible"])

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
