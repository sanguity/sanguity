#!/usr/bin/env python3
"""QR codes (#284, www/js/qr.js): our own encoder, byte mode at error correction level M, versions 1 to 10, held to
known vectors and read back. It runs in node (the script loaded into a vm context of its own, as a page would load it),
the test's own reader and Reed-Solomon code in Python checking what it makes:
  - the published pieces: the error correction codewords of ISO/IEC 18004's worked example (HELLO WORLD at 1-M), the
    format information of level M with each mask (its Table C.1) and the version information of versions 7 to 10
    (Table D.1), and how many bytes each version holds (Table 7);
  - whole symbols, module for module, the mask chosen included, against VECTORS below: made with segno 1.6.6, an
    independent encoder (not a dependency: the vectors are written out here), with its padding fixed where it pads a
    stream already on a byte boundary with a whole 0x00 codeword, against 7.4.10;
  - every length from 0 to 213 bytes read back: the format says level M and the mask used, the data is the text's
    UTF-8 bytes then the terminator and the pad codewords, and each block's error correction is right.
tests/browser_check.py reads the code shown beside a new share link back to its address with read_qr() below.

  python3 tests/test_qr.py      # needs node on the PATH
"""
import json, os, re, shutil, subprocess, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QR_JS = os.path.join(ROOT, "www", "js", "qr.js")

# level M: per version, the error correction codewords of each block, then (blocks, data codewords in each) per group
BLOCKS = [None, (10, (1, 16)), (16, (1, 28)), (26, (1, 44)), (18, (2, 32)), (24, (2, 43)), (16, (4, 27)), (18, (4, 31)),
          (22, (2, 38), (2, 39)), (22, (3, 36), (2, 37)), (26, (4, 43), (1, 44))]
ALIGN = [None, [], [6, 18], [6, 22], [6, 26], [6, 30], [6, 34], [6, 22, 38], [6, 24, 42], [6, 26, 46], [6, 28, 50]]
MASKS = [lambda r, c: (r + c) % 2 == 0, lambda r, c: r % 2 == 0, lambda r, c: c % 3 == 0, lambda r, c: (r + c) % 3 == 0,
         lambda r, c: (r // 2 + c // 3) % 2 == 0, lambda r, c: r * c % 2 + r * c % 3 == 0,
         lambda r, c: (r * c % 2 + r * c % 3) % 2 == 0, lambda r, c: ((r + c) % 2 + r * c % 3) % 2 == 0]
BYTES = [None, 14, 26, 42, 62, 84, 106, 122, 152, 180, 213]   # Table 7: the bytes each version holds at level M

# GF(256) on x^8+x^4+x^3+x^2+1, and the error correction codewords of a block
EXP, LOG = [0] * 512, [0] * 256
_x = 1
for _i in range(255):
    EXP[_i] = EXP[_i + 255] = _x
    LOG[_x] = _i
    _x = (_x << 1) ^ (0x11D if _x & 0x80 else 0)


def rs(data, n):
    gen = [1]
    for i in range(n):   # (x - α^0)…(x - α^(n-1)), highest power first
        gen = [a ^ (EXP[LOG[b] + i] if b else 0) for a, b in zip(gen + [0], [0] + gen)]
    rem = list(data) + [0] * n
    for i in range(len(data)):
        f = rem[i]
        if f:
            for j, g in enumerate(gen):
                rem[i + j] ^= EXP[LOG[g] + LOG[f]] if g else 0
    return rem[len(data):]


def function_map(n):
    """the modules that aren't data: finders and separators, timing, alignment, format and version areas"""
    v = (n - 17) // 4
    fn = [[False] * n for _ in range(n)]
    for r0, c0 in ((0, 0), (0, n - 8), (n - 8, 0)):
        for r in range(r0, r0 + 8):
            for c in range(c0, c0 + 8):
                fn[r][c] = True
    for i in range(n):
        fn[6][i] = fn[i][6] = True
    for i in range(9):
        fn[8][i] = fn[i][8] = True
    for i in range(8):
        fn[8][n - 1 - i] = fn[n - 1 - i][8] = True
    a = ALIGN[v]
    for r in a:
        for c in a:
            if not ((r == 6 and c in (6, a[-1])) or (c == 6 and r == a[-1])):
                for dr in range(-2, 3):
                    for dc in range(-2, 3):
                        fn[r + dr][c + dc] = True
    if v >= 7:
        for i in range(18):
            fn[n - 11 + i % 3][i // 3] = fn[i // 3][n - 11 + i % 3] = True
    return fn


def read_codes(m):
    """a symbol's modules (rows of 0 and 1) -> (its level's two bits, its mask, the data blocks, their error correction
    blocks), the format read from its first copy and checked against the second"""
    n = len(m)
    v = (n - 17) // 4
    assert 1 <= v <= 10 and n == 17 + 4 * v, n
    first = [m[8][c] for c in (0, 1, 2, 3, 4, 5, 7)] + [m[r][8] for r in (8, 7, 5, 4, 3, 2, 1, 0)]
    second = [m[r][8] for r in range(n - 1, n - 8, -1)] + [m[8][c] for c in range(n - 8, n)]
    assert first == second, (first, second)
    fmt = int("".join(map(str, first)), 2) ^ 0x5412
    level, mask = fmt >> 13, fmt >> 10 & 7
    fn, bits, right = function_map(n), [], n - 1
    while right >= 1:
        if right == 6:
            right = 5
        for step in range(n):
            for c in (right, right - 1):
                r = step if (right + 1) & 2 else n - 1 - step
                if not fn[r][c]:
                    bits.append(m[r][c] ^ MASKS[mask](r, c))
        right -= 2
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits) - 7, 8)]
    ec, *groups = BLOCKS[v]
    sizes = [d for b, d in groups for _ in range(b)]
    data, k = [[] for _ in sizes], 0
    for i in range(max(sizes)):
        for b, size in enumerate(sizes):
            if i < size:
                data[b].append(words[k])
                k += 1
    ecs = [[] for _ in sizes]
    for i in range(ec):
        for b in range(len(sizes)):
            ecs[b].append(words[k])
            k += 1
    return level, mask, data, ecs


def read_qr(m):
    """a symbol's text, as bytes: its byte-mode segment, its padding checked"""
    v = (len(m) - 17) // 4
    _, _, data, _ = read_codes(m)
    bits = "".join(f"{w:08b}" for b in data for w in b)
    assert bits[:4] == "0100", bits[:4]
    width = 8 if v < 10 else 16
    count = int(bits[4:4 + width], 2)
    at = 4 + width + 8 * count
    out = bytes(int(bits[i:i + 8], 2) for i in range(4 + width, at, 8))
    rest = bits[at:]
    zeros = min(4, len(rest)) + (-(at + min(4, len(rest)))) % 8   # the terminator, then to the byte's end
    assert set(rest[:zeros]) <= {"0"}, rest[:16]
    pads = rest[zeros:]
    assert all(pads[i:i + 8] == ("11101100" if i % 16 == 0 else "00010001") for i in range(0, len(pads), 8)), pads[:32]
    return out


def path_modules(d, size):
    """the modules of a qrSvg() path ("M x y h w v1 h-w z" per run of dark modules, 4 modules of quiet zone round it)"""
    m = [[0] * size for _ in range(size)]
    for x, y, w in re.findall(r"M(\d+) (\d+)h(\d+)v1h-\d+z", d):
        for c in range(int(x) - 4, int(x) - 4 + int(w)):
            m[int(y) - 4][c] = 1
    return m


def qr_js(expr, data=None):
    """expr evaluated in node with qr.js loaded as a page loads it; DATA is data, given in"""
    js = ("const vm = require('vm'), fs = require('fs'); const ctx = vm.createContext({TextEncoder});"
          "vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx, {filename: 'qr.js'});"
          "ctx.DATA = JSON.parse(fs.readFileSync(0, 'utf8'));"
          "console.log(JSON.stringify(vm.runInContext(process.argv[2], ctx)));")
    r = subprocess.run(["node", "-e", js, QR_JS, expr], input=json.dumps(data), capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)


ALPH = "abcdefghijklmnopqrstuvwxyz0123456789-_"


def text(n):
    return "".join(ALPH[(i * 7 + 3) % len(ALPH)] for i in range(n))


def prose(n):
    return "".join(chr(32 + (i * 37 + 11) % 95) for i in range(n))   # printable ASCII, spaces and punctuation too


def rows(hexes, n):
    return [[int(b) for b in bin(int(h, 16))[2:].zfill(len(h) * 4)[:n]] for h in hexes]


@unittest.skipUnless(shutil.which("node") or os.environ.get("CI"), "needs node")   # in CI, a missing node fails
class QR(unittest.TestCase):
    def test_reed_solomon(self):
        """ISO/IEC 18004's worked example, HELLO WORLD at 1-M: its 16 data codewords give these 10 (and the test's own
        Reed-Solomon code agrees, as it's used below)"""
        data = [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17]
        want = [196, 35, 39, 119, 235, 215, 231, 226, 93, 23]
        self.assertEqual(rs(data, 10), want)
        self.assertEqual(qr_js("qrCode.ecc(DATA, 10)", data), want)

    def test_format_and_version_bits(self):
        """level M's format information for masks 0 to 7 (Table C.1), and versions 7 to 10's version information
        (Table D.1)"""
        self.assertEqual(qr_js("[0, 1, 2, 3, 4, 5, 6, 7].map(m => qrCode.formatBits(m).toString(2).padStart(15, '0'))"),
                         ["101010000010010", "101000100100101", "101111001111100", "101101101001011",
                          "100010111111001", "100000011001110", "100111110010111", "100101010100000"])
        self.assertEqual(qr_js("[7, 8, 9, 10].map(v => qrCode.versionBits(v).toString(2).padStart(18, '0'))"),
                         ["000111110010010100", "001000010110111100", "001001101010011001", "001010010011010011"])

    def test_capacity(self):
        """the smallest version that holds the text: each version's most bytes in it, one more in the next; past
        version 10's 213 bytes, none (qrSvg gives null then); a character's UTF-8 bytes are what count"""
        got = qr_js("DATA.map(n => { const q = qrCode('x'.repeat(n)); return q && [q.version, q.size]; })",
                    [n + d for n in BYTES[1:] for d in (0, 1)])
        want = [[w, 17 + 4 * w] for v in range(1, 11) for w in (v, v + 1)][:-1] + [None]
        self.assertEqual(got, want)
        self.assertEqual(qr_js("[qrCode('é'.repeat(7)).version, qrCode('é'.repeat(8)).version, qrCode(new Uint8Array(14)).version]"), [1, 2, 1])

    def test_vectors(self):
        """whole symbols module for module, with the version and the mask chosen (or given), from version 1 to 10"""
        got = qr_js("DATA.map(([t, m]) => { const q = qrCode(t, {mask: m}); return [q.version, q.mask, q.modules]; })",
                    [[t, m] for t, m, *_ in VECTORS])
        for (t, m, v, mask, hexes), (gv, gmask, mods) in zip(VECTORS, got):
            with self.subTest(text=t[:40], version=v):
                self.assertEqual((gv, gmask), (v, mask))
                self.assertEqual(mods, rows(hexes, 17 + 4 * v))

    def test_mask_choice(self):
        """the mask chosen for each length from 0 to 213 bytes of two texts, as the same independent encoder chose it:
        what holds the penalty's weights and rules to ISO/IEC 18004's, which the whole symbols above alone don't"""
        got = qr_js("DATA.map(t => qrCode(t).mask).join('')", [f(n) for f in (text, prose) for n in range(214)])
        self.assertEqual(got, CHOSEN["text"] + CHOSEN["prose"])

    def test_reads_back(self):
        """every length from 0 to 213 bytes, and text past ASCII: read back to its bytes, level M, the mask it says,
        and each block's error correction as the test's own Reed-Solomon code makes it"""
        texts = [text(n) for n in range(214)] + ["é ünïcode ✓ " * 4, "https://sanguity.example/share.html#" + text(43)]
        got = qr_js("DATA.map(t => { const q = qrCode(t); return [q.mask, q.modules]; })", texts)
        masks = set()
        for t, (mask, mods) in zip(texts, got):
            with self.subTest(n=len(t)):
                level, read_mask, data, ecs = read_codes(mods)
                self.assertEqual((level, read_mask), (0, mask))
                self.assertEqual(read_qr(mods), t.encode())
                v = (len(mods) - 17) // 4
                self.assertEqual(ecs, [rs(b, BLOCKS[v][0]) for b in data])
                masks.add(mask)
        self.assertGreater(len(masks), 4, masks)   # the masks are chosen, not one used for all

    def test_svg(self):
        """qrSvg() (with a stand-in for util.js's el()): an <svg class="qr" role="img"> named by its label, its viewBox
        the symbol and 4 modules of quiet zone each side, one path of the dark modules in the text's colour, which
        path_modules() reads back to the symbol; null for a text too long"""
        got = qr_js("""(() => { el = (tag, attrs, ...kids) => ({tag, attrs, kids});
            const s = qrSvg(DATA, {label: 'The link as a QR code'}), q = qrCode(DATA);
            return [s.tag, s.attrs, s.kids.map(k => [k.tag, k.attrs]), q.modules, qrSvg('x'.repeat(214))]; })()""",
                    "https://sanguity.example/share.html#" + text(43))
        tag, attrs, kids, mods, none = got
        n = len(mods)
        self.assertEqual((tag, attrs["class"], attrs["role"], attrs["aria-label"], attrs["viewBox"]),
                         ("svg:svg", "qr", "img", "The link as a QR code", f"0 0 {n + 8} {n + 8}"))
        self.assertEqual([k[0] for k in kids], ["svg:path"])
        self.assertEqual(kids[0][1]["fill"], "currentColor")
        self.assertEqual(path_modules(kids[0][1]["d"], n), mods)
        self.assertIsNone(none)


# (text, mask given or None, version, mask, each row's modules as hex, left to right, padded with 0s to whole digits)
VECTORS = [
    ("a", None, 1, 5, [
        "fe5bf8", "82b208", "bad2e8", "bab2e8", "ba4ae8", "823208", "feabf8", "00c000", "82b670", "981dc8", "2e6b00",
        "559f50", "d3dff8", "00c828", "fe74f0", "822238", "ba74e0", "ba5f40", "ba5dd8", "823f40", "fea930"]),
    ("HELLO WORLD", None, 1, 4, [
        "fecbf8", "821208", "ba52e8", "ba92e8", "baeae8", "829208", "feabf8", "009800", "8bf7c8", "10b878", "3f3690",
        "f8c400", "faab30", "00af58", "feead0", "825d98", "bad630", "ba48d8", "ba71c0", "821400", "feffa8"]),
    ("http://127.0.0.1:8731/ingest", None, 3, 4, [
        "fed063f8", "8250b208", "ba6dfae8", "bad3b2e8", "baa6eae8", "82bf5208", "feaaabf8", "00e24000", "8bd7d7c8",
        "ed082af8", "27672908", "446df518", "824c5110", "65c6aed8", "9ae0efa8", "692a7e98", "9bd7d050", "98500a98",
        "275f00e8", "0ddde418", "cffc4fc8", "00968888", "feb0fae8", "82424898", "bab7bf98", "ba787170", "ba072958",
        "82259f98", "fee21c90"]),
    ("Bearer " + text(43), None, 4, 2, [
        "fe4cd93f8", "827fcc208", "bae5f4ae8", "bada8a2e8", "bac961ae8", "82b7ec208", "feaaaabf8", "00c124800",
        "be776abe0", "d9381d278", "abd9a6560", "04b20c160", "3e3932480", "d12b89d68", "06b052550", "b8babe1e8",
        "c36ad2580", "54d47bb38", "076fc87e0", "591eeef60", "e311834d0", "9d2927220", "beb3eaa50", "85972d768",
        "af0bc1f80", "0098bf8b8", "fe7529a90", "82cf058f0", "bafd3af98", "ba93d6498", "baf028e00", "822c84460",
        "fe98d9e50"]),
    ("https://sanguity.example/share.html#" + text(43), None, 5, 2, [
        "fe67dbcbf8", "823b8a9a08", "baaf96bae8", "bacdd3cae8", "baf4bf8ae8", "82e0667208", "feaaaaabf8", "00962c1800",
        "be5b4673e0", "f155df4410", "860a007af8", "f4d8ae84c8", "7f606affb8", "05aaf94910", "1a43a2bfd8", "c4cd863190",
        "ee5bf36fb0", "3c83192150", "7759e4bbf8", "611705ae48", "1e35d3f6a0", "79943d2840", "ef2b82bbd8", "bcaf1e9988",
        "1bcc3adfa0", "893bb56960", "b6520c1418", "810ea79190", "baa8c27ff0", "00d8f308c0", "fe4b4a0ab8", "82e46c08d8",
        "bab78bdfa8", "baa7772e88", "baafe80018", "82710d2888", "fedbc2e6f8"]),
    (text(100), None, 6, 2, [
        "fe073d6dbf8", "822ca210a08", "baab0d22ae8", "baa9a2edae8", "bad6dd29ae8", "82e10cdc208", "feaaaaaabf8",
        "00fc0ca3800", "be01c36fbe0", "944f5b29ad8", "860d8250900", "b8e93e99fc0", "0714c3e7520", "dc8c4b29a98",
        "dff572108e0", "05e31c22858", "cf9f53f6120", "184adb29e98", "ff4f461e1a0", "a054e701c48", "824795f4120",
        "1454bd69998", "02968a56d40", "a021a69bb50", "72daf0d6520", "441c1f69fa8", "0308ea9a4f0", "21b80db86c0",
        "bacc58d7578", "e19807e9ba8", "bf4dee12db0", "9565a78a0d8", "8b6a4857ff8", "00f2922c8a8", "fe3faaffad0",
        "82f0002b8d0", "ba892ed6ff0", "bac0152d138", "babcc073f80", "827c7610a50", "fef200e52a0"]),
    (text(121), 3, 7, 3, [
        "fef10547cbf8", "82d452129208", "ba3b770292e8", "ba8fef435ae8", "ba51bfb1fae8", "827b38bb4208", "feaaaaaaabf8",
        "00bed8ff4000", "b7250fb91258", "4c3325175c68", "a75fe246f158", "396f23b39050", "e3b172032570", "e48ba8edc380",
        "03d068374280", "e8fc7009bba8", "838c0e67e928", "cd102e997128", "4f669dbdf770", "adb1e9ab0d98", "bf918f9e3fe8",
        "f89dd886d898", "aabdbaca6aa8", "a8eab8d4a8d0", "3f8e4fe27fc8", "c54e56319f20", "17f9d8e25860", "b54ab99edaf0",
        "0a690dd5b3e0", "61872d146098", "1a502e673c30", "1c5cc41848c0", "132af65a4560", "84b85fdf0d28", "0a842287a3d8",
        "79f99f75fbd8", "9b074f912fd0", "00fcd8ed58d0", "fecbcaef0aa0", "82de58f9e8f0", "ba4c2ff3cfa8", "bac2d385ec48",
        "baaea335fc50", "8224708b3f88", "fec5927f73a0"]),
    (text(150), None, 8, 2, [
        "fe4c20cf04bf8", "825149b1fba08", "bab9819c91ae8", "bafcf5e6692e8", "babc27ef842e8", "828e922066208",
        "feaaaaaaaabf8", "00e6863c91000", "be3c9be66abe0", "112900cf81700", "7ff1d5a067858", "9d4fa11c91288",
        "271f20e66af78", "89bbbacf81760", "47c6ea2067938", "5146f91ed2288", "2f9b3ee06cf38", "fd2342cf85760",
        "fb80722063938", "9d5e512fd2288", "474b7eb07cf38", "7cb702d795760", "ffc113f16bfb8", "a882522fd2888",
        "2a85feb07caa0", "88f42237958e0", "4ff5f3f16afb8", "3cdd906fd2108", "53b363307d0a0", "a0fa46b794b40",
        "1ba5f2d16bf78", "3041346df4900", "9baefb361d9b8", "0964d7b794a40", "92fb4bd52af78", "bd3d9cedf4888",
        "f2dd7b371d9a0", "557997b60ca50", "47662bc432f68", "708c7cedf4898", "e2f1bbf71dfa8", "009d72360c8d0",
        "fe4e8aa432ae8", "82961e2df4898", "baa0bbf71cfa8", "bad977160c9f8", "bafd69c432200", "82531bcfd6f48",
        "fee2a8f3588b8"]),
    (text(180), None, 9, 2, [
        "fe4f429f0c23f8", "826452e1ab3208", "bad7ab87c192e8", "ba93792d7e6ae8", "bad526fb1d62e8", "8288fe8de7a208",
        "feaaaaaaaaabf8", "009acc88e4f000", "be4a1cff7d1be0", "e14672aeddb198", "72b8d6253a5fc0", "ad3eb7c2b18d10",
        "ffcc29695a3fe0", "edea5ace906068", "7294b1702f9330", "382c93d786e950", "8aec61b31e6cf8", "65105eda0571e0",
        "2a25e5847e97e0", "30be458e93a450", "9e3db4f03819a0", "809dc3cfdc74d8", "abce8930225a00", "f972ebcaf39948",
        "8fcab9fd0a4fc0", "88b6268a8078a8", "daf0b9a92f5af0", "98e1d58b86a8c0", "2ffa31fa780fe0", "952eb9a69ca1b8",
        "1669d4fce6d500", "4555e050d2da50", "3f1088cf3d4520", "7c7983368de8c8", "fbe8241d3349c0", "30cbe343b5be00",
        "abb2305b4a6670", "1502af630071c8", "ba08908ca60570", "844b7bf5c69bd0", "2ef9f9563f6f38", "1c8ed7a7c42bf8",
        "dec7be9d6fc060", "61ade684d5bb48", "131541ff4a0fa8", "0099ba8f046888", "fe3ad5a9ba4aa0", "82bff38fd188d8",
        "baf70cfd6e7fe8", "bafcac0b857f48", "baedf5e46f8498", "8273a36be4f1d0", "fef475c27d06a0"]),
    (text(213), None, 10, 6, [
        "fe8e44a7b3bb3f8", "82d6598d755d208", "baf4ede7d14f2e8", "ba515e3fddfd2e8", "baa2febf8ab12e8",
        "824665e35f82208", "feaaaaaaaaaabf8", "001b94233394800", "9ff3737e6c844b8", "6915f1e2ea73ea0",
        "f21430686d93558", "cdd31947b12af70", "c348874cea88288", "4531e8ee1b65b90", "ff89716d8a11b80",
        "38f2d6e50502738", "e2cc4701c85c718", "d82150dabceec38", "ee774b300989c88", "09bf4dec44e77b8",
        "4619222a3ac37d0", "6c4089bfb6bfbf0", "4b2cee90ad825f8", "20480b42a038238", "9ad6fb0ec8ed4d8",
        "1901cf86c7bdea0", "5f8debbf8649ff0", "68f9ece2523b8e0", "dab5df2baa3ead8", "58b940e369238d8",
        "1fe2a5bf8189fb8", "682b779744d3168", "4702b7721c84018", "698602befa725f8", "5b5b744fec1aa50",
        "f04f3cf8f52e620", "f7dd8664eac9a58", "80a216c64f69f20", "372044322308dc0", "499b4c052327538",
        "d2d2462b891d2c0", "9988cfaca5eb5d8", "8f34f13701d9a08", "65b1f28421315f8", "6f8bc45338a2e48",
        "c81a1f9c36bef90", "a70136e2b81a7f8", "f9f5f4b8f458ab8", "0308fd3ec8fbfc0", "00ccfba283bc8e0",
        "fefcbaea1edcae0", "82e0d02236768e0", "ba9ad23fba2ffc8", "bab9aee06de7460", "ba6481e9521dcd8",
        "826f908776b1378", "fe9e1d100890e00"]),
    ("é ünïcode ✓", None, 2, 7, [
        "fe6a3f8", "8270a08", "ba2b2e8", "ba0c2e8", "ba402e8", "82a9a08", "feaabf8", "005c800", "96b2500", "10b5658",
        "32a7408", "59edfb0", "cb53620", "3cf7f78", "8af7bd0", "65504e8", "c74cfe8", "00cf8a0", "fe7bab0", "82ca8b0",
        "ba01f98", "ba931a0", "ba40128", "8263c20", "fe88eb8"]),
]
# the mask segno 1.6.6 chose for text(n) and prose(n), n from 0 to 213, a digit each
CHOSEN = {
    "text": "64400457742725211125116626622753672762256225565555262252452326227223222222222323232222222222222222222222232"
            "22222232322222222226222222222522222222222622322222222222222222222222222222262226262626262666666626727272266",
    "prose": "62005022674755540444344133451727272724622216664426466445462662764646666676666171757612555252635323527757227"
             "22222222222222276566666655656666654766666667676666636666616266566261666662367727632767625367673336576657635",
}


if __name__ == "__main__":
    unittest.main()
