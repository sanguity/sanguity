"""A small PDF writer on the standard library, for the visit summary (summary.py, #48).

Enough for a printed page of text, tables and sparklines: US Letter pages, the two standard Helvetica fonts (every
PDF reader has them, so nothing is embedded), text in Windows-1252 (WinAnsiEncoding; other characters are put as
their closest plain letter, or "?"), lines, filled rectangles, polylines and dots, in grey or a colour, each page's
drawing compressed with zlib. Text widths come from the fonts' published metrics, so lines can be wrapped and
columns cut to fit. The output is bytes, built in memory: nothing is written to disk.

    doc = Document(title="Visit summary")
    page = doc.page()
    page.text(50, 740, "Hello", size=12, bold=True)
    pdf_bytes = doc.output()

Coordinates are PDF points (1/72 inch) from the bottom-left corner of the page.
"""
import unicodedata, zlib

WIDTH, HEIGHT = 612, 792   # US Letter

# Advance widths (per 1000 units of font size) of the printable ASCII characters, 32 (space) to 126 (~), from the
# Adobe Font Metrics of Helvetica and Helvetica-Bold
_REG = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556,
        556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500,
        667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333,
        556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500,
        722, 500, 500, 500, 334, 260, 334, 584]
_BOLD = [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556,
         556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556,
         722, 611, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556, 333,
         556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611, 611, 389, 556, 333, 611, 556,
         778, 556, 556, 500, 389, 280, 389, 584]
# a few characters past ASCII the summary uses (Windows-1252 has them); any other is about a digit wide
_EXTRA = {"–": (556, 556), "—": (1000, 1000), "‘": (222, 278), "’": (222, 278), "“": (333, 500), "”": (333, 500),
          "•": (350, 350), "·": (278, 278), "°": (400, 400), "±": (584, 584), "µ": (556, 611), "…": (1000, 1000),
          "×": (584, 584), " ": (278, 278)}
# characters Windows-1252 lacks, said in ones it has
_PLAIN = {"≤": "<=", "≥": ">=", "▲": "^", "▼": "v", "→": "->", "←": "<-", "−": "-", "‐": "-", "‑": "-", " ": " ",
          " ": " ", "✓": "ok"}


def plain(text):
    """text with every character the fonts can show: kept when Windows-1252 has it, else its plain form."""
    out = []
    for ch in str(text):
        if ch in "\r\n\t":
            out.append(" ")
            continue
        if ch in _PLAIN:
            out.append(_PLAIN[ch])
            continue
        if unicodedata.category(ch)[0] == "C":
            continue
        try:
            ch.encode("cp1252")
            out.append(ch)
        except UnicodeEncodeError:
            base = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
            out.append(base or "?")
    return "".join(out)


def width(text, size, bold=False):
    """How wide text is, in points, at this font size."""
    table, total = _BOLD if bold else _REG, 0
    for ch in plain(text):
        o = ord(ch)
        if 32 <= o <= 126:
            total += table[o - 32]
        else:
            total += _EXTRA.get(ch, (556, 556))[1 if bold else 0]
    return total * size / 1000


def fit(text, size, room, bold=False):
    """text cut to fit room points, ending in "…" when it was cut."""
    text = plain(text)
    if width(text, size, bold) <= room:
        return text
    while text and width(text + "…", size, bold) > room:
        text = text[:-1]
    return text.rstrip() + "…" if text else ""


def wrap(text, size, room, bold=False):
    """text broken into lines that fit room points (a word longer than a line is broken across lines, never cut)."""
    lines = []
    for para in str(text).split("\n"):
        if width(para, size, bold) <= room:   # kept as it is, spaces and all ("11.8 Low  Nov 3")
            lines.append(plain(para))
            continue
        line = ""
        for word in plain(para).split(" "):
            if not word:
                continue
            trial = f"{line} {word}" if line else word
            if width(trial, size, bold) <= room:
                line = trial
                continue
            if line:
                lines.append(line)
            while width(word, size, bold) > room and len(word) > 1:
                n = len(word) - 1
                while n > 1 and width(word[:n], size, bold) > room:
                    n -= 1
                lines.append(word[:n])
                word = word[n:]
            line = word
        lines.append(line)
    return lines


def _string(text):
    """A PDF literal string in Windows-1252, with ( ) \\ escaped and bytes past ASCII as octal escapes."""
    out = []
    for b in plain(text).encode("cp1252", "replace"):
        if b in (0x28, 0x29, 0x5C):
            out.append("\\" + chr(b))
        elif 32 <= b < 127:
            out.append(chr(b))
        else:
            out.append(f"\\{b:03o}")
    return "(" + "".join(out) + ")"


def _rgb(c):
    if isinstance(c, (int, float)):
        return f"{c:.3f} {c:.3f} {c:.3f}"
    return " ".join(f"{v:.3f}" for v in c)


class Page:
    def __init__(self):
        self.ops = []

    def text(self, x, y, s, size=10, bold=False, color=0):
        if s:
            self.ops.append(f"BT /{'F2' if bold else 'F1'} {size:g} Tf {_rgb(color)} rg {x:.2f} {y:.2f} Td {_string(s)} Tj ET")

    def text_right(self, x, y, s, size=10, bold=False, color=0):
        self.text(x - width(s, size, bold), y, s, size, bold, color)

    def line(self, x1, y1, x2, y2, w=0.5, color=0):
        self.ops.append(f"{_rgb(color)} RG {w:g} w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S")

    def rect(self, x, y, w, h, fill=0.9):
        self.ops.append(f"{_rgb(fill)} rg {x:.2f} {y:.2f} {w:.2f} {h:.2f} re f")

    def polyline(self, pts, w=0.8, color=0):
        if len(pts) >= 2:
            path = " ".join(f"{x:.2f} {y:.2f} {'m' if i == 0 else 'l'}" for i, (x, y) in enumerate(pts))
            self.ops.append(f"1 J 1 j {_rgb(color)} RG {w:g} w {path} S")

    def dot(self, x, y, r=1.4, color=0):
        k = r * 0.5523   # a circle from four Bézier curves
        self.ops.append(f"{_rgb(color)} rg {x + r:.2f} {y:.2f} m "
                        f"{x + r:.2f} {y + k:.2f} {x + k:.2f} {y + r:.2f} {x:.2f} {y + r:.2f} c "
                        f"{x - k:.2f} {y + r:.2f} {x - r:.2f} {y + k:.2f} {x - r:.2f} {y:.2f} c "
                        f"{x - r:.2f} {y - k:.2f} {x - k:.2f} {y - r:.2f} {x:.2f} {y - r:.2f} c "
                        f"{x + k:.2f} {y - r:.2f} {x + r:.2f} {y - k:.2f} {x + r:.2f} {y:.2f} c f")


class Document:
    def __init__(self, title="", author="Sanguity"):
        self.title, self.author, self.pages = title, author, []

    def page(self):
        p = Page()
        self.pages.append(p)
        return p

    def output(self):
        """The finished PDF, as bytes."""
        objs = []   # object n is objs[n - 1]

        def add(body):
            objs.append(body)
            return len(objs)

        catalog = add(None)
        pages = add(None)
        reg = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        bold = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        kids = []
        for p in self.pages or [Page()]:
            data = zlib.compress("\n".join(p.ops).encode("ascii"), 9)
            content = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data) + data + b"\nendstream")
            kids.append(add((f"<< /Type /Page /Parent {pages} 0 R /MediaBox [0 0 {WIDTH} {HEIGHT}] /Contents {content} 0 R "
                             f"/Resources << /Font << /F1 {reg} 0 R /F2 {bold} 0 R >> >> >>").encode()))
        objs[catalog - 1] = f"<< /Type /Catalog /Pages {pages} 0 R >>".encode()
        objs[pages - 1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
        info = add(f"<< /Title {_string(self.title)} /Producer {_string(self.author)} >>".encode())

        out, offsets = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"), []
        for n, body in enumerate(objs, start=1):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
        out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
        out += b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, catalog, info, xref)
        return bytes(out)
