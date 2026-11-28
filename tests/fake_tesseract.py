"""A stand-in for Tesseract (tests/test_ocr.py): answers as `tesseract stdin stdout --tessdata-dir D -l L [--dpi N]`
does, without recognising anything. Run as  python3 -I fake_tesseract.py CONTROL ARGS..., where CONTROL is a JSON file
the test writes: {"text": what a picture "says", "sleep": seconds per run, "log": a file each run's arguments are added
to}.

  - every language in -l must be a file in the tessdata folder whose first four bytes are a small count, and that
    doesn't hold b"BROKEN" (exit 1 otherwise, as Tesseract fails on a language file it can't load)
  - the picture on standard input must start as a PNG, JPEG, TIFF, GIF, BMP, WebP or PNM file does (exit 1 otherwise)
  - a blank one says nothing: a PNM with no dark pixel, or tests/fixtures/ocr/blank.png; any other says "text"
"""
import hashlib, json, os, struct, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
MAGIC = (b"\x89PNG", b"\xff\xd8", b"II*\x00", b"MM\x00*", b"GIF8", b"BM", b"RIFF", b"P4", b"P5", b"P6")


def blank(data):
    if data[:2] in (b"P4", b"P5", b"P6"):
        parts = data.split(b"\n", 3 if data[:2] != b"P4" else 2)
        px = parts[-1]
        return not any(px) if data[:2] == b"P4" else min(px or b"\xff") >= 128
    with open(os.path.join(HERE, "fixtures", "ocr", "blank.png"), "rb") as f:
        return hashlib.sha256(data).digest() == hashlib.sha256(f.read()).digest()


def main(control, args):
    with open(control, encoding="utf-8") as f:
        c = json.load(f)
    if c.get("log"):
        with open(c["log"], "a", encoding="utf-8") as f:
            f.write(json.dumps(args) + "\n")
    folder = args[args.index("--tessdata-dir") + 1] if "--tessdata-dir" in args else "."
    for code in (args[args.index("-l") + 1] if "-l" in args else "eng").split("+"):
        try:
            with open(os.path.join(folder, code + ".traineddata"), "rb") as f:
                body = f.read()
        except OSError:
            sys.stderr.write(f"Failed loading language '{code}'\n")
            return 1
        if len(body) < 4 or not 1 <= struct.unpack("<i", body[:4])[0] <= 64 or b"BROKEN" in body:
            sys.stderr.write(f"Failed loading language '{code}'\n")
            return 1
    data = sys.stdin.buffer.read()
    if not data.startswith(MAGIC):
        sys.stderr.write("Error in pixReadMem: Unknown format\n")
        return 1
    time.sleep(float(c.get("sleep") or 0))
    if not blank(data):
        sys.stdout.write(c.get("text", "Scanned letter about zinnwaldite") + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2:]))
