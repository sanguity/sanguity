"""Text recognition for scanned documents (#1129): the words of a scanned letter or report, read on this server by
Tesseract, so the Timeline's search and the command palette's find the document. Nothing leaves the machine. The
text is for finding a document only: it is never read for lab values or anything else, and never shown as the
document's data (the drawer has it behind "Text found by text recognition", drawer.js).

What's read: a document of a type in TYPES, a PDF or a picture. A PDF page with text of its own (a text layer) is
read from that, never recognised; a page without has its picture recognised (the largest image on it). PDFs are
hostile input: each is opened by pypdf in a process of its own, with a memory limit, as importers/pdf_labs.py opens
them, which hands back each page's text, or its picture as an image Tesseract reads (JPEG, a fax's TIFF, PNM).
Tesseract is a program run with no shell, the picture on its standard input (nothing decrypted is written to disk),
at a lower priority and on one core, each run at most RUN_TIMEOUT. At most MAX_PAGES pages and MAX_SECONDS of a
document are read: what's left out is said.

When: a document added (profiles.index_documents wakes the thread once its import is saved), and every document there
was before, one at a time in the background (the pass: newest first, its progress in General → Text recognition,
status()). A row in document_text (schema 58) is a document read, or being read: a stop in the middle reads it again
at the next start, and one whose reading was begun TRIES times is marked failed, never tried again. One where no
text is found (a photo, a blank page) is marked so, and not read again either.

Languages: English is in the image (TESSDATA/eng.traineddata, from tessdata_fast). An admin adds others: a
.traineddata file they import (import_language: its name a language code, at most MAX_LANG_BYTES, its structure
checked, then a trial run on SAMPLE), or one downloaded from the tesseract-ocr project's tessdata_fast (download:
only while the install setting ocr.downloads is on, through net.py under outside.for_ocr's permit). They're kept in
DATA_DIR/languages/ (backed up: backup.CONTENTS). The install setting ocr.languages says which are used besides
English. Each run gets a folder of its own in the temporary folder with links to the files it uses.

  start(), stop(), wake()         the thread ("sanguity-ocr")
  status(db)                      what General → Text recognition shows
  search(db, pid, q)              the ids of pid's records whose documents' text has q (the Timeline's search: all
                                  of what's typed, in lower case)
  text_of(db, pid, doc)           a document's text and state, for its drawer
  read_picture(data)              the text of one picture read now and kept nowhere: an insurance card's photo, for
                                  the suggestions its dialog makes (#1168, insurance.from_text), with the same
                                  limits, languages and Tesseract run as a document's, one at a time

  python -m server.ocr check      recognises SAMPLE with the built-in English (CI's Docker jobs run it)
"""
import base64, contextlib, json, logging, os, re, shutil, struct, subprocess, sys, tempfile, threading, time, urllib.parse

from . import config, crypto, db as dbm, net, records, settings

log = logging.getLogger("sanguity.ocr")

TESSERACT = os.environ.get("SANGUITY_TESSERACT", "tesseract")   # the program, or "off" for no text recognition
TESSDATA = os.environ.get("SANGUITY_TESSDATA", "/usr/share/tessdata")   # where the image keeps eng.traineddata
LANG_DIR = os.path.join(config.DATA_DIR, "languages")   # the languages an admin added
# tessdata_fast's files, at the commit the image's English is from (docker/app.Dockerfile)
DOWNLOAD = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/87416418657359cb625c412a48b6e1d6d41c29bd/"
PURPOSE = "Text recognition language"   # outside_contacts' purpose

TYPES = ("pdf", "png", "jpg", "jpeg", "gif", "tif", "tiff", "webp", "bmp")
MAX_PAGES = 20               # pages read of one document
MAX_SECONDS = 300            # time spent on one document
RUN_TIMEOUT = 120            # one Tesseract run (a page)
MAX_BYTES = 200 * config.MB  # a document bigger than this isn't read
MAX_PIXELS = 60_000_000      # a page's picture bigger than this isn't (an A4 page at 600 dpi is 35 million)
MAX_CHARS = 200_000          # text kept of one document
LAYER_CHARS = 32             # a PDF page with this many letters and digits of its own is read from its text layer
MEMORY = 1024 * config.MB    # address-space limit of the PDF reader's process, where the system has one
TESSERACT_MEMORY = 1536 * config.MB   # ... and of each Tesseract run's
MAX_LANG_BYTES = 64 * config.MB   # a language file (tessdata_fast's are 1 to 13 MB; tessdata's largest about 50)
TRIES = 2
TICK = 600                   # seconds between looks for documents no one woke the thread for
MIN_QUERY = 3                # letters typed before documents are searched
LANG_FILE = re.compile(r"^([a-z_]{3,16})\.traineddata$")

# a line of text, "Scanned letter about zinnwaldite" (tests/fixtures/ocr/scan.png): what check() and a language's
# trial run recognise
SAMPLE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAggAAAA8AQAAAAAr839cAAACfUlEQVRIx+3VPW7bMBQA4CczMDMYYscOgomiF3A3D0IItBdxe4Jk01BUChSYS4"
    "FciY6A6hoUdAGNGgS9PlKJIykBMgToUEhcLJP8ZL4fGfC9FyzCIizCIvzvQiZH9zXq5xv98lOn+n06E3oQ7xb4XBBivK+aC0k6FTqZvy2UIyF+"
    "IajibQFHgmrnQvpuQdWNMOKShpX2M2oTAzNXH9DyjLe8aLLUBCVNh1JoiK9W8WEbzAS/FmgYbhjqbAMsCwENy1jDHgWaXm/FEaIY4kME6UQQbi"
    "0HRvt4RgKQAIMQ2OBJYLDa8hCiPQkfZwJgXnDNClbwnOd4FFyEYs3p21t7skVDcchLNx3yzTbaJXFynahZRelalLzghaCBR6lFKI8CdZ03ReMF"
    "7adJUNGuI6FT06rmusLS7Za1E1QpQvWHSr3WT4Lw0xsu0Qs3/VRo2T323C2BlazxCHQKWJPAdFu230joB+H3SrbRJ6qoa5gKGFAsnfAAQMIdML"
    "EhCy0J2gud9MLdVwleOIAcCy2eBMIrQnYWWvUoxK8LaSUwcEseZO8EhWKjehLqs5B6IfsRcy98n9YkCffVcFCJLg4khE7QFMmydZFshkCbQ6yi"
    "LyTcvBC0HQ46zqZEcc6mfRTsPn0tm5066Qfa5fe7ijoLRU71VNgKT09C8muoqJmwYrd56LPJ6MLjmrK5pmxqlp2MOZkKMx+mwjY/99FuR8JuVp"
    "PUTxD4UA19IRj1BUUSXF9YsOmzABfUF8msLxAE9aYXjPC9KRnLthINuN60l2ehanq4oN5MIpi+q41qpfGvkUY19PNsmueWHmKvO9GJVjajBzKN"
    "jAqMLf96i7AIi/BPhL85jx5ryL0YWwAAAABJRU5ErkJggg==")
SAMPLE_WORDS = ("scanned", "letter")

# tessdata_fast's languages offered for download, by the name people know them by (its other files are scripts, old
# forms of a language and vertical text, which an admin can still import)
NAMES = {
    "afr": "Afrikaans", "ara": "Arabic", "aze": "Azerbaijani", "bel": "Belarusian", "ben": "Bengali", "bos": "Bosnian",
    "bul": "Bulgarian", "cat": "Catalan", "ces": "Czech", "chi_sim": "Chinese (simplified)", "chi_tra": "Chinese (traditional)",
    "cym": "Welsh", "dan": "Danish", "deu": "German", "ell": "Greek", "epo": "Esperanto", "est": "Estonian", "eus": "Basque",
    "fas": "Persian", "fil": "Filipino", "fin": "Finnish", "fra": "French", "gle": "Irish", "glg": "Galician",
    "guj": "Gujarati", "hat": "Haitian Creole", "heb": "Hebrew", "hin": "Hindi", "hrv": "Croatian", "hun": "Hungarian",
    "hye": "Armenian", "ind": "Indonesian", "isl": "Icelandic", "ita": "Italian", "jpn": "Japanese", "kan": "Kannada",
    "kat": "Georgian", "kaz": "Kazakh", "khm": "Khmer", "kor": "Korean", "lao": "Lao", "lat": "Latin", "lav": "Latvian",
    "lit": "Lithuanian", "mal": "Malayalam", "mar": "Marathi", "mkd": "Macedonian", "mlt": "Maltese", "msa": "Malay",
    "mya": "Burmese", "nep": "Nepali", "nld": "Dutch", "nor": "Norwegian", "pan": "Punjabi", "pol": "Polish",
    "por": "Portuguese", "pus": "Pashto", "ron": "Romanian", "rus": "Russian", "sin": "Sinhala", "slk": "Slovak",
    "slv": "Slovenian", "spa": "Spanish", "sqi": "Albanian", "srp": "Serbian (Cyrillic)", "srp_latn": "Serbian (Latin)",
    "swa": "Swahili", "swe": "Swedish", "tam": "Tamil", "tel": "Telugu", "tgk": "Tajik", "tha": "Thai", "tir": "Tigrinya",
    "tur": "Turkish", "uig": "Uyghur", "ukr": "Ukrainian", "urd": "Urdu", "uzb": "Uzbek", "vie": "Vietnamese",
    "yid": "Yiddish", "yor": "Yoruba"}


class LanguageError(ValueError):
    """A language file that wasn't added, and why, in plain words."""


class _OutOfTime(Exception):
    pass


class _Unreadable(Exception):
    pass


# ---------------------------------------------------------------- Tesseract

def binary():
    """The Tesseract program, or None when there's none (or SANGUITY_TESSERACT=off)."""
    if not TESSERACT or TESSERACT == "off":
        return None
    return shutil.which(TESSERACT)


def available():
    return bool(binary()) and os.path.isfile(os.path.join(TESSDATA, "eng.traineddata"))


def _env():
    # one core (it's a background job), the C locale it wants for its numbers, and nothing else of the app's
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "OMP_THREAD_LIMIT": "1", "LC_ALL": "C"}


def _file_of(code):
    return os.path.join(TESSDATA if code == "eng" else LANG_DIR, code + ".traineddata")


@contextlib.contextmanager
def _tessdata(codes, files=None):
    """A folder of its own with a link to each language's file (files: {code: path} to use instead)."""
    d = tempfile.mkdtemp(prefix="sanguity-ocr-")
    try:
        for c in codes:
            os.symlink(os.path.abspath((files or {}).get(c) or _file_of(c)), os.path.join(d, c + ".traineddata"))
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _limited():
    """(Run in the child before Tesseract starts) its address space capped, as the PDF reader's is."""
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (TESSERACT_MEMORY, TESSERACT_MEMORY))
    except (ImportError, ValueError, OSError):
        pass


# no debug output, and no list of its settings, whatever a language file's own settings say: a second guard to _clean's.
# (It keeps debug_file off; a language's tessedit_write_params_to_file is acted on as the language loads, before
# the command line's settings, so only _clean taking it out stops that one.)
NO_FILES = ["-c", "debug_file=/dev/null", "-c", "tessedit_write_params_to_file="]


def _tesseract(image, folder, codes, deadline, dpi=None, page=None):
    """The text Tesseract finds in image (bytes of a picture it reads; page: one page of a TIFF of several). Raises
    _OutOfTime, or _Unreadable."""
    left = deadline - time.monotonic()
    if left < 1:
        raise _OutOfTime()
    cmd = [binary() or "tesseract", "stdin", "stdout", "--tessdata-dir", folder, "-l", "+".join(codes), *NO_FILES]
    if dpi:
        cmd += ["--dpi", str(int(dpi))]
    if page is not None:
        cmd += ["-c", f"tessedit_page_number={int(page)}"]
    if (nice := shutil.which("nice")):
        cmd = [nice, "-n", "10", *cmd]
    try:   # (preexec_fn only sets the limit: no lock or other thread's state is touched in the child)
        p = subprocess.run(cmd, input=image, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=folder, env=_env(),
                           timeout=min(RUN_TIMEOUT, left), preexec_fn=_limited if os.name == "posix" else None,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise _OutOfTime() from None
    except OSError:
        raise _Unreadable() from None
    if p.returncode != 0:
        raise _Unreadable()
    return p.stdout[:MAX_CHARS * 4].decode("utf-8", "replace")


# ---------------------------------------------------------------- a picture's size, from its own header

def dimensions(data):
    """[(width, height)] of each page of a picture, as its own header says (PNG, JPEG, GIF, BMP, WebP, PNM, TIFF), or
    None when it's none of those or its header can't be read: what Tesseract would decode, checked before it does."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            return [struct.unpack(">II", data[16:24])]
        if data[:2] == b"\xff\xd8":
            return _jpeg(data)
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return [struct.unpack("<HH", data[6:10])]
        if data[:2] == b"BM":
            w, h = struct.unpack("<ii", data[18:26])
            return [(abs(w), abs(h))]
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return _webp(data)
        if (m := re.match(rb"P[1-6]\s+(?:#[^\n]*\s+)*(\d{1,9})\s+(?:#[^\n]*\s+)*(\d{1,9})\s", data[:512])):
            return [(int(m.group(1)), int(m.group(2)))]
        if data[:4] in (b"II*\x00", b"MM\x00*"):
            return _tiff(data)
    except (struct.error, ValueError, IndexError):
        pass
    return None


def _jpeg(data):
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
        elif marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
            i += 2
        elif 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):   # a frame's start: its size
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return [(w, h)]
        else:
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


def _webp(data):
    kind = data[12:16]
    if kind == b"VP8X":
        return [(1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little"))]
    if kind == b"VP8L":
        b = int.from_bytes(data[21:25], "little")
        return [((b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1)]
    if kind == b"VP8 ":
        w, h = struct.unpack("<HH", data[26:30])
        return [(w & 0x3FFF, h & 0x3FFF)]
    return None


def _tiff(data):
    """Each page's size, from its image file directory (ImageWidth, ImageLength)."""
    e = "<" if data[:2] == b"II" else ">"
    at, out, seen = struct.unpack(e + "I", data[4:8])[0], [], set()
    while at and at not in seen and len(out) < 10000:
        seen.add(at)
        n = struct.unpack(e + "H", data[at:at + 2])[0]
        size = {}
        for k in range(n):
            tag, kind = struct.unpack(e + "HH", data[at + 2 + 12 * k:at + 6 + 12 * k])
            value = data[at + 10 + 12 * k:at + 14 + 12 * k]
            if tag in (256, 257):
                size[tag] = struct.unpack(e + "H", value[:2])[0] if kind == 3 else struct.unpack(e + "I", value)[0]
        if len(size) < 2:
            return None
        out.append((size[256], size[257]))
        at = struct.unpack(e + "I", data[at + 2 + 12 * n:at + 6 + 12 * n])[0]
    return out or None


# ---------------------------------------------------------------- a PDF's pages, in a process of its own

# python -I -B -c _PDF_READER max_pages memory max_chars layer_chars max_pixels, the PDF on its standard input. It
# writes one JSON line {"count": pages} (or {"error": "encrypted" | "unreadable" | "nopypdf"}), then one per page read:
# {"page", "text"}, {"page", "image": "jpg" | "tif" | "pbm" | "pgm" | "ppm", "dpi", "size"} followed by size bytes of
# the picture, or {"page", "none": why}
_PDF_READER = r"""
import io, json, re, sys
try:
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (int(sys.argv[2]), int(sys.argv[2])))
except Exception:
    pass
out = sys.stdout.buffer
def say(h, payload=b""):
    if payload:
        h["size"] = len(payload)
    out.write(json.dumps(h).encode() + b"\n" + payload)
    out.flush()
try:
    import pypdf
except ImportError:
    say({"error": "nopypdf"}); sys.exit(0)
import logging
logging.disable(logging.CRITICAL)
max_pages, max_chars, layer, max_px = int(sys.argv[1]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
try:
    r = pypdf.PdfReader(io.BytesIO(sys.stdin.buffer.read()))
    if r.is_encrypted and not r.decrypt(""):
        say({"error": "encrypted"}); sys.exit(0)
    n = len(r.pages)
except Exception:
    say({"error": "unreadable"}); sys.exit(0)
say({"count": n})
INVERT = bytes(255 - i for i in range(256))
def obj(x):
    return x.get_object() if hasattr(x, "get_object") else x
def space(cs):
    # (components, a palette as a 256-byte grey table, or None) of an image's colour space; (0, None): not read
    cs = obj(cs)
    if isinstance(cs, list) and cs:
        kind = str(obj(cs[0]))
        if kind == "/ICCBased":
            return int(obj(obj(cs[1]).get("/N", 0))), None
        if kind in ("/CalGray", "/CalRGB"):
            return (1 if kind == "/CalGray" else 3), None
        if kind == "/Indexed" and len(cs) == 4:
            base, _ = space(cs[1])
            hival, look = int(obj(cs[2])), obj(cs[3])
            look = look.get_data() if hasattr(look, "get_data") else getattr(look, "original_bytes", None) or bytes(look)
            if base not in (1, 3):
                return 0, None
            grey = []
            for i in range(256):
                e = look[min(i, hival) * base:min(i, hival) * base + base]
                grey.append(0 if len(e) < base else e[0] if base == 1 else (299 * e[0] + 587 * e[1] + 114 * e[2]) // 1000)
            return 1, bytes(grey)
        return 0, None
    return {"/DeviceGray": 1, "/DeviceRGB": 3, "/G": 1, "/RGB": 3}.get(str(cs), 0), None
def picture(page):
    res = obj(page.get("/Resources"))
    xo = obj(res.get("/XObject")) if res else None
    best = None
    for k in (xo or {}):
        x = obj(xo[k])
        if str(x.get("/Subtype")) != "/Image":
            continue
        w, h = int(obj(x.get("/Width", 0))), int(obj(x.get("/Height", 0)))
        if w > 0 and h > 0 and (best is None or w * h > best[0]):
            best = (w * h, x, w, h)
    if best is None:
        return "no picture"
    _, x, w, h = best
    if w * h > max_px:
        return "too big"
    f = obj(x.get("/Filter"))
    f = str(obj(f[-1])) if isinstance(f, list) and f else str(f) if f else ""
    box = page.mediabox
    across = float(box.height if int(obj(page.get("/Rotate", 0)) or 0) % 180 else box.width) or 612.0
    dpi = max(70, min(1200, round(w / (across / 72))))
    if f in ("/DCTDecode", "/DCT"):
        return "jpg", x.get_data(), dpi
    if f in ("/CCITTFaxDecode", "/CCF"):
        return "tif", x.get_data(), dpi
    if f in ("/JPXDecode", "/JBIG2Decode"):
        return "not a kind read"
    raw = x.get_data()
    mask = bool(obj(x.get("/ImageMask", False)))
    bpc = 1 if mask else int(obj(x.get("/BitsPerComponent", 8)))
    n, grey = (1, None) if mask else space(x.get("/ColorSpace"))
    decode = [float(obj(v)) for v in obj(x.get("/Decode") or [])]
    flipped = decode[:2] == [1.0, 0.0]
    if n == 1 and bpc == 1 and grey is None:   # a PDF's 0 is black (an image mask's 0 paints), a PBM's 1
        need = (w + 7) // 8 * h
        return ("pbm", b"P4\n%d %d\n" % (w, h) + (raw[:need] if flipped else raw[:need].translate(INVERT)), dpi) if len(raw) >= need else "short"
    if bpc != 8:
        return "not a kind read"
    if grey is not None or n == 1:
        need = w * h
        if len(raw) < need:
            return "short"
        px = raw[:need].translate(grey) if grey is not None else raw[:need].translate(INVERT) if flipped else raw[:need]
        return "pgm", b"P5\n%d %d\n255\n" % (w, h) + px, dpi
    if n == 3 and len(raw) >= w * h * 3:
        return "ppm", b"P6\n%d %d\n255\n" % (w, h) + raw[:w * h * 3], dpi
    return "not a kind read"
for i in range(min(n, max_pages)):
    try:
        page = r.pages[i]
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if len(re.findall(r"\w", text)) >= layer:
            say({"page": i, "text": text[:max_chars]})
            continue
        got = picture(page)
        if isinstance(got, str):
            say({"page": i, "none": got})
        else:
            say({"page": i, "image": got[0], "dpi": got[2]}, got[1])
    except MemoryError:
        say({"page": i, "none": "memory"}); break
    except Exception:
        say({"page": i, "none": "unreadable"})
"""


def _pdf_pages(data, deadline):
    """(header, picture bytes) for each page the reader sends, as it sends them; it's stopped at the deadline."""
    p = subprocess.Popen([sys.executable, "-I", "-B", "-c", _PDF_READER, str(MAX_PAGES), str(MEMORY), str(MAX_CHARS),
                          str(LAYER_CHARS), str(MAX_PIXELS)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, env=_env(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    stop = threading.Timer(max(1.0, deadline - time.monotonic()), p.kill)
    stop.start()
    feed = threading.Thread(target=_feed, args=(p.stdin, data), daemon=True)
    feed.start()
    try:
        while True:
            line = p.stdout.readline(4 * MAX_CHARS + 4096)
            if not line.endswith(b"\n"):
                return
            h = json.loads(line)
            size = int(h.get("size") or 0)
            if size < 0 or size > 4 * MAX_PIXELS:
                return
            payload = p.stdout.read(size) if size else b""
            if len(payload) < size:
                return
            yield h, payload
    finally:
        stop.cancel()
        if p.poll() is None:
            p.kill()
        p.wait()
        p.stdout.close()


def _feed(pipe, data):
    with contextlib.suppress(OSError):
        pipe.write(data)
    with contextlib.suppress(OSError):
        pipe.close()


# ---------------------------------------------------------------- one document

def _found(text):
    """Text worth keeping: two words of three letters or more (a photo's specks read as text aren't)."""
    return len(re.findall(r"[^\W\d_]{3,}", text)) >= 2


def read(path, ext, codes=("eng",)):
    """What one document's file says: {"state": "found" | "none" | "failed", "why", "source": "ocr" | "layer",
    "text", "pages", "read", "capped": "pages" | "time" | None}."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return {"state": "failed", "why": "missing"}
    if size > MAX_BYTES:
        return {"state": "failed", "why": "too_big"}
    data = crypto.read_file(path)
    deadline = time.monotonic() + MAX_SECONDS
    if ext != "pdf":   # a picture's own size, as its header says, before Tesseract decodes it
        pages = dimensions(data)
        if not pages:
            return {"state": "failed", "why": "unreadable"}
        if any(w * h > MAX_PIXELS for w, h in pages[:MAX_PAGES]):
            return {"state": "failed", "why": "too_big"}
    with _tessdata(codes) as folder:
        if ext == "pdf":
            got = _read_pdf(data, folder, codes, deadline)
        elif len(pages) > 1:   # a TIFF of several pages: each read on its own, MAX_PAGES at most
            got = _read_tiff(data, folder, codes, deadline, len(pages))
        else:
            try:
                got = {"texts": [_tesseract(data, folder, codes, deadline)], "ocr": True}
            except _OutOfTime:
                return {"state": "failed", "why": "time"}
            except _Unreadable:
                return {"state": "failed", "why": "unreadable"}
    if "state" in got:
        return got
    text = "\n\n".join(t.strip() for t in got["texts"] if t.strip())[:MAX_CHARS]
    out = {"pages": got.get("pages"), "read": got.get("read"), "capped": got.get("capped")}
    if not _found(text):
        return {"state": "none", **out}
    return {"state": "found", "source": "ocr" if got["ocr"] else "layer", "text": text, **out}


def _read_tiff(data, folder, codes, deadline, pages):
    texts, done, capped = [], 0, None
    for i in range(min(pages, MAX_PAGES)):
        try:
            texts.append(_tesseract(data, folder, codes, deadline, page=i))
        except _OutOfTime:
            capped = "time"
            break
        except _Unreadable:
            pass
        done += 1
    return {"texts": texts, "ocr": True, "pages": pages, "read": done,
            "capped": capped or ("pages" if pages > MAX_PAGES else None)}


def _read_pdf(data, folder, codes, deadline):
    pages, texts, done, capped, ocr = None, [], 0, None, False
    for h, payload in _pdf_pages(data, deadline):
        if "error" in h:
            return {"state": "failed", "why": h["error"] if h["error"] in ("encrypted", "nopypdf") else "unreadable"}
        if "count" in h:
            pages = int(h["count"])
            continue
        if "text" in h:
            texts.append(h["text"])
        elif "image" in h:
            size = dimensions(payload)   # the picture's own header, not the size the PDF declared for it
            try:
                if not size or len(size) > 1 or size[0][0] * size[0][1] > MAX_PIXELS:
                    raise _Unreadable()
                texts.append(_tesseract(payload, folder, codes, deadline, h.get("dpi")))
                ocr = True
            except _OutOfTime:
                capped = "time"
                break
            except _Unreadable:
                pass
        done += 1
    if pages is None:
        return {"state": "failed", "why": "time" if time.monotonic() >= deadline - 1 else "unreadable"}
    if capped is None and done < min(pages, MAX_PAGES) and time.monotonic() >= deadline - 1:
        capped = "time"
    if capped is None and pages > MAX_PAGES:
        capped = "pages"
    return {"texts": texts, "ocr": ocr, "pages": pages, "read": done, "capped": capped}


# ---------------------------------------------------------------- the thread, and the pass over every document

_thread, _stop, _wake = None, threading.Event(), threading.Event()
_now = {}   # the document being read: {"doc", "started"}
_lock = threading.Lock()


def start():
    global _thread
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="sanguity-ocr", daemon=True)
    _thread.start()


def stop(timeout=5):
    global _thread
    _stop.set()
    _wake.set()
    if _thread is not None:
        _thread.join(timeout)
    _thread = None


def wake():
    _wake.set()


def _loop():
    while not _stop.is_set():
        did = False
        try:
            if available():
                db = dbm.connect()
                try:
                    did = step(db)
                finally:
                    db.close()
        except Exception:
            log.exception("text recognition")
        if not did:
            _wake.wait(TICK)
            _wake.clear()


_TYPES_SQL = ", ".join("?" * len(TYPES))


def recover(db):
    """At start-up: a document whose reading was begun TRIES times without an end is marked failed, never tried again."""
    db.execute("UPDATE document_text SET state='failed', why='stopped' WHERE state='reading' AND tries >= ?", (TRIES,))


def _next(db):
    return db.execute(f"""SELECT d.id, d.path, d.ext FROM documents d LEFT JOIN document_text t ON t.doc_id = d.id
                          WHERE d.ext IN ({_TYPES_SQL}) AND (t.doc_id IS NULL OR (t.state = 'reading' AND t.tries < ?))
                          ORDER BY d.rowid DESC LIMIT 1""", (*TYPES, TRIES)).fetchone()


def step(db):
    """Read the next document waiting, if there is one. True when one was read."""
    r = _next(db)
    if r is None:
        return False
    read_one(db, r["id"], crypto.dec(r["path"]), r["ext"])
    return True


_card = threading.Lock()   # one card read at a time (a person waits for it, so it doesn't queue behind the pass)
CARD_SECONDS = 60


def read_picture(data):
    """The text Tesseract finds in one picture (bytes of a JPEG, PNG or WebP, checked already), read now and kept
    nowhere: its standard input, its standard output. {"state": "found" | "none" | "failed" | "off", "why",
    "text"}; "off" when there's no text recognition on this server."""
    if not available():
        return {"state": "off"}
    pages = dimensions(data)
    if not pages or len(pages) != 1:
        return {"state": "failed", "why": "unreadable"}
    if pages[0][0] * pages[0][1] > MAX_PIXELS:
        return {"state": "failed", "why": "too_big"}
    codes = in_use()
    with _card:
        try:
            with _tessdata(codes) as folder:
                text = _tesseract(data, folder, codes, time.monotonic() + CARD_SECONDS).strip()[:MAX_CHARS]
        except _OutOfTime:
            return {"state": "failed", "why": "time"}
        except _Unreadable:
            return {"state": "failed", "why": "unreadable"}
    return {"state": "found", "text": text} if re.search(r"[^\W_]{3,}", text) else {"state": "none"}


def in_use():
    """The languages documents are read with: English, and those of ocr.languages there's a file for whose settings
    are only recognition's (_clean made them so as it was added; one put in the folder some other way, or by a
    version before, isn't used until it's added again)."""
    return ["eng"] + [c for c in settings.install_value("ocr.languages") or [] if _config_ok(_file_of(c))]


def read_one(db, doc, path, ext):
    try:
        with dbm.transaction(db):
            db.execute("""INSERT INTO document_text(doc_id, state, tries, at) VALUES(?, 'reading', 1, ?)
                          ON CONFLICT(doc_id) DO UPDATE SET state='reading', tries=tries+1, at=excluded.at""", (doc, dbm.now()))
    except dbm.sqlite3.IntegrityError:   # its document went meanwhile
        return
    codes = in_use()
    with _lock:
        _now.update(doc=doc, started=dbm.now())
    try:
        got = read(path, ext, codes)
    except Exception:
        log.exception("text recognition of a document")   # where, never the message (main.QuietTracebacks)
        got = {"state": "failed", "why": "error"}
    finally:
        with _lock:
            _now.clear()
    db.execute("""UPDATE document_text SET state=?, source=?, why=?, pages=?, read=?, capped=?, langs=?, at=?, body=?
                  WHERE doc_id=?""", (got["state"], got.get("source"), got.get("why"), got.get("pages"), got.get("read"),
                                      got.get("capped"), "+".join(codes), dbm.now(), crypto.enc(got.get("text")), doc))


def status(db):
    """General → Text recognition: whether this server can read documents, its languages, and the pass's progress."""
    counts = dict(db.execute(f"""SELECT COALESCE(t.state, 'waiting'), COUNT(*) FROM documents d
                                 LEFT JOIN document_text t ON t.doc_id = d.id WHERE d.ext IN ({_TYPES_SQL})
                                 GROUP BY COALESCE(t.state, 'waiting')""", TYPES).fetchall())
    waiting = counts.get("waiting", 0) + counts.get("reading", 0)
    total = sum(counts.values())
    have = {x["code"] for x in languages()}
    wanted = settings.install_value("ocr.languages") or []
    with _lock:
        reading = bool(_now)
    return {"available": available(), "languages": languages(), "in_use": ["eng"] + [c for c in wanted if c in have],
            "downloads": bool(settings.install_value("ocr.downloads")), "host": net.host_of(DOWNLOAD),
            "offered": [{"code": c, "name": n} for c, n in sorted(NAMES.items(), key=lambda x: x[1]) if c not in have],
            "max_mb": MAX_LANG_BYTES // config.MB, "max_pages": MAX_PAGES, "max_minutes": MAX_SECONDS // 60,
            "pass": {"total": total, "read": total - waiting, "waiting": waiting, "found": counts.get("found", 0),
                     "none": counts.get("none", 0), "failed": counts.get("failed", 0), "reading": reading}}


# ---------------------------------------------------------------- finding documents, and a document's text

def _flat(text):
    return " ".join(text.lower().split())


def search(db, pid, q):
    """The ids of profile pid's records whose document has q in its text (all of q, as the Timeline's search finds
    an event's words; spaces and line breaks count as one space)."""
    q = _flat(str(q or ""))[:200]
    if len(q) < MIN_QUERY:
        return []
    docs = [r["doc_id"] for r in db.execute("""SELECT t.doc_id, t.body FROM document_text t JOIN documents d ON d.id = t.doc_id
                                                 WHERE d.profile_id=? AND t.state='found'""", (pid,))
            if q in _flat(crypto.dec(r["body"]) or "")]
    return records.document_events(db, pid, docs) if docs else []


def text_of(db, pid, doc):
    """{"state": "found" | "none" | "failed" | "waiting" | "off" | "not_read", ...} of one of pid's documents, or None
    when it isn't one: found with "source", "text", "pages", "read", "capped", "max_pages", "max_minutes"."""
    d = db.execute("SELECT ext FROM documents WHERE id=? AND profile_id=?", (doc, pid)).fetchone()
    if d is None:
        return None
    if d["ext"] not in TYPES:
        return {"state": "not_read"}
    r = db.execute("SELECT * FROM document_text WHERE doc_id=?", (doc,)).fetchone()
    if r is None or (r["state"] == "reading" and r["tries"] < TRIES):
        return {"state": "waiting" if available() else "off"}
    out = {"state": r["state"], "pages": r["pages"], "read": r["read"], "capped": r["capped"],
           "max_pages": MAX_PAGES, "max_minutes": MAX_SECONDS // 60}
    if r["state"] == "found":
        out.update(source=r["source"], text=crypto.dec(r["body"]))
    return out


# ---------------------------------------------------------------- languages

def languages():
    """English, then each language file an admin added: [{"code", "name", "built_in", "size"}]."""
    out = [{"code": "eng", "name": "English", "built_in": True, "size": None}]
    with contextlib.suppress(OSError):
        for f in sorted(os.listdir(LANG_DIR)):
            m = LANG_FILE.match(f)
            p = os.path.join(LANG_DIR, f)
            if m and m.group(1) not in ("eng", "osd") and os.path.isfile(p) and not os.path.islink(p):
                out.append({"code": m.group(1), "name": NAMES.get(m.group(1), m.group(1)), "built_in": False, "size": os.path.getsize(p)})
    return out


def code_of(name):
    """The language code a file's name gives ("deu.traineddata": "deu"). Raises LanguageError."""
    m = LANG_FILE.match(str(name or ""))
    if not m:
        raise LanguageError("A language file is named for its language's code, then .traineddata (deu.traineddata, "
                            "chi_sim.traineddata): 3 to 16 small letters or _.")
    if m.group(1) == "eng":
        raise LanguageError("English is built in already.")
    if m.group(1) == "osd":
        raise LanguageError("osd.traineddata finds a page's orientation, not a language's words, so it isn't used here.")
    return m.group(1)


def _shape(path):
    """Whether path looks like a Tesseract language file: a count of parts, then where each starts in the file."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(4 + 8 * 64)
    if len(head) < 12:
        return False
    n = struct.unpack("<i", head[:4])[0]
    if not 1 <= n <= 64 or len(head) < 4 + 8 * n:
        return False
    starts = [s for s in struct.unpack(f"<{n}q", head[4:4 + 8 * n]) if s != -1]
    return bool(starts) and all(4 + 8 * n <= s < size for s in starts) and starts == sorted(starts)


# A language file's own settings (its first part, the "config"; tessdata's Chinese, Japanese, Korean, Hindi and
# Arabic files have one) are applied by Tesseract each time it starts, and a setting can name a file it then writes
# (debug_file, tessedit_write_params_to_file). Only these recognition settings are kept, each with a value that names
# no path; the rest are taken out before the file is first tried (and in_use passes over a file with any other).
CONFIG_KEPT = re.compile(r"^(?:preserve_interword_spaces|tessedit_ocr_engine_mode|tessedit_char_(?:black|white)list|"
                         r"pageseg_devanagari_split_strategy|segment_nonalphabetic_script|merge_fragments_in_matrix|"
                         r"(?:tosp|classify|textord|language_model|chop|wordrec|segsearch|edges|lstm)_[a-z0-9_]+)$")
CONFIG_NEVER = re.compile(r"file|debug|write|dir|path|output|print|display|show|save|dump|log", re.I)


def _safe_config(raw):
    keep = []
    for line in raw.decode("utf-8", "replace").splitlines():
        parts = line.strip().split(None, 1)
        if (len(parts) == 2 and CONFIG_KEPT.match(parts[0]) and not CONFIG_NEVER.search(parts[0])
                and not re.search(r"[/\\\x00-\x1f\x7f]", parts[1].strip())):
            keep.append(f"{parts[0]} {parts[1].strip()}")
    return ("\n".join(keep) + "\n").encode() if keep else b""


def _config_ok(path):
    """Whether the language file at path has no settings, or only those _safe_config keeps."""
    try:
        if not _shape(path):
            return False
        with open(path, "rb") as f:
            head = f.read(4 + 8 * 64)
            n = struct.unpack("<i", head[:4])[0]
            starts = struct.unpack(f"<{n}q", head[4:4 + 8 * n])
            if starts[0] == -1:
                return True
            end = min([s for s in starts[1:] if s != -1] or [os.path.getsize(path)])
            if not 0 <= end - starts[0] <= 65536:
                return False
            f.seek(starts[0])
            part = f.read(end - starts[0])
    except OSError:
        return False
    return _safe_config(part) == part


def _clean(path):
    """Rewrite the language file at path with its config (part 0) holding only CONFIG_KEPT's settings, or none.
    Its parts keep their order; where each starts is worked out again. (_shape has checked the table.)"""
    with open(path, "rb") as f:
        data = f.read()
    n = struct.unpack("<i", data[:4])[0]
    starts = list(struct.unpack(f"<{n}q", data[4:4 + 8 * n]))
    if starts[0] == -1:
        return
    order = sorted((s, i) for i, s in enumerate(starts) if s != -1)
    parts = {i: data[s:(order[k + 1][0] if k + 1 < len(order) else len(data))] for k, (s, i) in enumerate(order)}
    parts[0] = _safe_config(parts[0])
    at, out, new = 4 + 8 * n, [], [-1] * n
    for _, i in order:
        if parts[i] or i != 0:
            new[i] = at
            out.append(parts[i])
            at += len(parts[i])
    with open(path, "wb") as f:
        f.write(struct.pack("<i", n) + struct.pack(f"<{n}q", *new) + b"".join(out))


def _add(code, tmp):
    """Check the file at tmp and put it in place as code's; tmp is removed either way. Raises LanguageError."""
    try:
        if not _shape(tmp):
            raise LanguageError("That isn't a Tesseract language file (.traineddata), so it wasn't added.")
        _clean(tmp)
        if not available():
            raise LanguageError("This server has no Tesseract to try it with, so it wasn't added.")
        with _tessdata([code], {code: tmp}) as folder:
            try:   # Tesseract reads it once, on the sample, with a time limit: one it can't use isn't kept
                _tesseract(SAMPLE, folder, [code], time.monotonic() + 60)
            except (_OutOfTime, _Unreadable):
                raise LanguageError("Tesseract couldn't use it as a language file, so it wasn't added.") from None
        os.chmod(tmp, 0o600)
        os.replace(tmp, os.path.join(LANG_DIR, code + ".traineddata"))
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.remove(tmp)
    return next(x for x in languages() if x["code"] == code)


def _temp():
    os.makedirs(LANG_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=LANG_DIR, prefix=".added-", suffix=".tmp")
    return os.fdopen(fd, "wb"), tmp


def import_language(name, data):
    """An admin's own language file (bytes), named name. Returns its entry; raises LanguageError."""
    code = code_of(name)
    if len(data) > MAX_LANG_BYTES:
        raise LanguageError(f"A language file can be at most {MAX_LANG_BYTES // config.MB} MB.")
    f, tmp = _temp()
    with f:
        f.write(data)
    return _add(code, tmp)


def remove_language(code):
    """Remove an added language (and from ocr.languages, by the caller). False when there was none."""
    if code == "eng" or not settings.LANGUAGE_CODE.match(code or ""):
        return False
    try:
        os.remove(os.path.join(LANG_DIR, code + ".traineddata"))
        return True
    except FileNotFoundError:
        return False


def download(db, code, who=None, ip=None):
    """Download code's file from tessdata_fast, while ocr.downloads is on (the permit allows no host otherwise)."""
    from . import outside
    if code not in NAMES:
        raise LanguageError("That isn't one of the languages offered for download.")
    if not settings.install_value("ocr.downloads"):
        raise LanguageError("Downloading languages is off: turn it on first, under Text recognition.")
    url = DOWNLOAD + code + ".traineddata"
    u = urllib.parse.urlsplit(url)
    f, tmp = _temp()
    try:
        with f:
            conn = net.connection(outside.for_ocr(db, who, ip), url, timeout=config.setting("DOWNLOAD_TIMEOUT"))   # (#1158)
            try:
                conn.request("GET", u.path, headers={"User-Agent": "Sanguity"})
                r = conn.getresponse()
                if r.status != 200:
                    raise LanguageError(f"{net.host_of(url)} answered {r.status} {r.reason}".rstrip() + ", so nothing was added.")
                got = 0
                while (part := r.read(1 << 20)):
                    got += len(part)
                    if got > MAX_LANG_BYTES:
                        raise LanguageError(f"It's bigger than {MAX_LANG_BYTES // config.MB} MB, so it wasn't added.")
                    f.write(part)
            finally:
                conn.close()
    except net.Refused as e:
        _drop(tmp)
        raise LanguageError(str(e)) from None
    except net.Errors as e:
        _drop(tmp)
        raise LanguageError(f"It couldn't be downloaded: {net.why(e)}.") from None
    except BaseException:
        _drop(tmp)
        raise
    return _add(code, tmp)


def _drop(path):
    with contextlib.suppress(FileNotFoundError):
        os.remove(path)


# ---------------------------------------------------------------- python -m server.ocr check

def check():
    """Recognise SAMPLE with English, as the server would: (whether its words were found, the text)."""
    if not available():
        return False, "no Tesseract, or no eng.traineddata in " + TESSDATA
    with _tessdata(["eng"]) as folder:
        try:
            text = _tesseract(SAMPLE, folder, ["eng"], time.monotonic() + 60)
        except (_OutOfTime, _Unreadable) as e:
            return False, type(e).__name__
    return all(w in text.lower() for w in SAMPLE_WORDS), text.strip()


if __name__ == "__main__":
    if sys.argv[1:] != ["check"]:
        sys.exit("python -m server.ocr check")
    ok, said = check()
    print(said)
    sys.exit(0 if ok else 1)
