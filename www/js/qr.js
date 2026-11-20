/* Sanguity · QR codes (#284), our own small encoder: no library (#243). Byte mode (the text's UTF-8), error correction
   level M (about 15% of a damaged code can be read back), versions 1 to 10 (up to 213 bytes, a share link or a phone's
   upload address with room to spare), the smallest that fits, and the mask with the lowest penalty. ISO/IEC 18004:
   the data in codewords with Reed-Solomon error correction (GF(256), x^8+x^4+x^3+x^2+1), interleaved by block, placed
   in the zigzag around the finder, timing and alignment patterns; each of the eight masks scored (runs of a colour,
   2×2 blocks, finder-like patterns, the share of dark modules) on the symbol before its format and version
   information are drawn in. tests/test_qr.py holds it to known vectors and reads its codes back.

   qrCode(text, {mask}) -> {version, mask, size, modules: rows of 0 (light) and 1 (dark)}, or null when the text is too
                           long; text a string or a Uint8Array
   qrSvg(text, {label})  -> an <svg class="qr" role="img"> of it with its 4-module quiet zone (app.css .qr: dark on
                           light in both themes, kept so in forced colours), or null when too long */
"use strict";
const qrCode = (() => {
  // level M per version: the error correction codewords of each block, then [blocks, data codewords in each] per group
  const M = [null, [10, [1, 16]], [16, [1, 28]], [26, [1, 44]], [18, [2, 32]], [24, [2, 43]], [16, [4, 27]], [18, [4, 31]],
    [22, [2, 38], [2, 39]], [22, [3, 36], [2, 37]], [26, [4, 43], [1, 44]]];
  const ALIGN = [null, [], [6, 18], [6, 22], [6, 26], [6, 30], [6, 34], [6, 22, 38], [6, 24, 42], [6, 26, 46], [6, 28, 50]];
  const MASKS = [(r, c) => (r + c) % 2 === 0, r => r % 2 === 0, (r, c) => c % 3 === 0, (r, c) => (r + c) % 3 === 0,
    (r, c) => ((r >> 1) + Math.floor(c / 3)) % 2 === 0, (r, c) => r * c % 2 + r * c % 3 === 0,
    (r, c) => (r * c % 2 + r * c % 3) % 2 === 0, (r, c) => ((r + c) % 2 + r * c % 3) % 2 === 0];
  const EXP = new Uint8Array(510), LOG = new Uint8Array(256);
  for (let i = 0, x = 1; i < 255; i++, x = x & 128 ? (x << 1) ^ 0x11d : x << 1) { EXP[i] = EXP[i + 255] = x; LOG[x] = i; }
  const mul = (a, b) => a && b ? EXP[LOG[a] + LOG[b]] : 0;
  const dataCap = v => M[v].slice(1).reduce((s, [b, d]) => s + b * d, 0);

  /* the n error correction codewords of data: the remainder of data·x^n divided by (x - α^0)…(x - α^(n-1)) */
  function ecc(data, n) {
    let g = [1];
    for (let i = 0; i < n; i++) g = [...g, 0].map((c, j) => c ^ (j ? mul(g[j - 1], EXP[i]) : 0));
    const r = new Array(n).fill(0);
    for (const d of data) {
      const f = d ^ r.shift();
      r.push(0);
      for (let j = 0; j < n; j++) r[j] ^= mul(g[j + 1], f);
    }
    return r;
  }
  /* the 15 format bits (level M is 00) and the 18 version bits, each with its BCH code */
  const bch = (v, gen, top) => { let r = v << top; for (let b = 31 - Math.clz32(gen); 31 - Math.clz32(r) >= b;) r ^= gen << (31 - Math.clz32(r) - b); return v << top | r; };
  const formatBits = mask => bch(mask, 0x537, 10) ^ 0x5412;
  const versionBits = v => bch(v, 0x1f25, 12);

  /* the final sequence: mode 0100, the byte count, the bytes, the terminator, pad bytes; blocks with their error
     correction, interleaved */
  function codewords(bytes, v) {
    const [ec, ...groups] = M[v], cap = dataCap(v), bits = [];
    const put = (x, len) => { for (let i = len - 1; i >= 0; i--) bits.push(x >>> i & 1); };
    put(4, 4); put(bytes.length, v < 10 ? 8 : 16);
    for (const b of bytes) put(b, 8);
    put(0, Math.min(4, cap * 8 - bits.length));
    put(0, (8 - bits.length % 8) % 8);
    const data = [];
    for (let i = 0; i < bits.length; i += 8) data.push(bits.slice(i, i + 8).reduce((a, b) => a << 1 | b, 0));
    for (let p = 0xec; data.length < cap; p ^= 0xec ^ 0x11) data.push(p);
    const blocks = [];
    let at = 0;
    for (const [n, d] of groups) for (let i = 0; i < n; i++) { const b = data.slice(at, at += d); blocks.push([b, ecc(b, ec)]); }
    const out = [];
    for (let i = 0; i < groups.at(-1)[1]; i++) for (const [d] of blocks) if (i < d.length) out.push(d[i]);
    for (let i = 0; i < ec; i++) for (const [, e] of blocks) out.push(e[i]);
    return out;
  }

  /* ISO/IEC 18004's penalty: runs of five or more of a colour in a row or column (3, and 1 for each more), each 2×2
     block of one colour (3), each finder-like 1:1:3:1:1 with four light modules before or after it, outside the
     symbol counting as light (40), and 10 for each 5% the dark share is from half */
  const FINDER = "1011101";
  function finders(line) {
    let n = 0, i = line.indexOf(FINDER);
    while (i !== -1) {
      const lit = (a, b) => !line.slice(Math.max(a, 0), Math.max(b, 0)).includes("1");
      const yes = lit(i - 4, i) || lit(i + 7, i + 11);
      if (yes) n += 40;
      i = line.indexOf(FINDER, i + (yes ? 7 : 4));
    }
    return n;
  }
  function penalty(m) {
    const n = m.length;
    let score = 0, dark = 0;
    for (let i = 0; i < n; i++) {
      const row = m[i].join(""), col = m.map(r => r[i]).join("");
      for (const line of [row, col]) for (const run of line.match(/0+|1+/g)) if (run.length >= 5) score += run.length - 2;
      score += finders(row) + finders(col);
      for (let j = 0; j < n; j++) {
        dark += m[i][j];
        if (i && j && m[i][j] === m[i][j - 1] && m[i][j] === m[i - 1][j] && m[i][j] === m[i - 1][j - 1]) score += 3;
      }
    }
    return score + 10 * Math.floor(Math.abs(dark / (n * n) * 100 - 50) / 5);
  }

  return Object.assign(function qrCode(text, {mask} = {}) {
    const bytes = typeof text === "string" ? new TextEncoder().encode(text) : text;
    let v = 1;
    while (v <= 10 && 4 + (v < 10 ? 8 : 16) + 8 * bytes.length > dataCap(v) * 8) v++;
    if (v > 10) return null;
    const n = 17 + 4 * v, m = [...Array(n)].map(() => new Array(n).fill(0)), fn = [...Array(n)].map(() => new Array(n).fill(false));
    const set = (r, c, on) => { m[r][c] = on ? 1 : 0; fn[r][c] = true; };
    for (const [r0, c0] of [[0, 0], [0, n - 7], [n - 7, 0]])   // the finders, each in its light separator
      for (let r = -1; r <= 7; r++) for (let c = -1; c <= 7; c++) {
        const d = Math.max(Math.abs(r - 3), Math.abs(c - 3));
        if (r0 + r >= 0 && r0 + r < n && c0 + c >= 0 && c0 + c < n) set(r0 + r, c0 + c, d !== 2 && d !== 4);
      }
    for (let i = 8; i < n - 8; i++) { set(6, i, i % 2 === 0); set(i, 6, i % 2 === 0); }   // timing
    const a = ALIGN[v], last = a.at(-1);
    for (const r of a) for (const c of a) {
      if (r === 6 && (c === 6 || c === last) || c === 6 && r === last) continue;   // not over a finder
      for (let dr = -2; dr <= 2; dr++) for (let dc = -2; dc <= 2; dc++) set(r + dr, c + dc, Math.max(Math.abs(dr), Math.abs(dc)) !== 1);
    }
    // the format and version areas, and the dark module, kept light while the masks are scored
    for (let i = 0; i < 9; i++) { if (!fn[8][i]) set(8, i, 0); if (!fn[i][8]) set(i, 8, 0); }
    for (let i = 0; i < 8; i++) { set(8, n - 1 - i, 0); set(n - 1 - i, 8, 0); }
    if (v >= 7) for (let i = 0; i < 18; i++) { set(n - 11 + i % 3, Math.floor(i / 3), 0); set(Math.floor(i / 3), n - 11 + i % 3, 0); }
    // the codewords in the zigzag: two columns at a time from the right, up then down, past the vertical timing
    const cw = codewords(bytes, v);
    let k = 0;
    for (let right = n - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5;
      for (let step = 0; step < n; step++) for (let j = 0; j < 2; j++) {
        const c = right - j, r = (right + 1) & 2 ? step : n - 1 - step;
        if (!fn[r][c] && k < cw.length * 8) { m[r][c] = cw[k >> 3] >>> (7 - (k & 7)) & 1; k++; }
      }
    }
    const masked = p => m.map((row, r) => row.map((x, c) => fn[r][c] ? x : x ^ (MASKS[p](r, c) ? 1 : 0)));
    let best = mask, out = mask == null ? null : masked(mask);
    if (out === null) {
      let low = Infinity;
      for (let p = 0; p < 8; p++) { const t = masked(p), s = penalty(t); if (s < low) { low = s; best = p; out = t; } }
    }
    const f = formatBits(best), bit = (x, i) => x >>> i & 1;
    for (let i = 0; i < 6; i++) out[i][8] = bit(f, i);
    out[7][8] = bit(f, 6); out[8][8] = bit(f, 7); out[8][7] = bit(f, 8);
    for (let i = 9; i < 15; i++) out[8][14 - i] = bit(f, i);
    for (let i = 0; i < 8; i++) out[8][n - 1 - i] = bit(f, i);
    for (let i = 8; i < 15; i++) out[n - 15 + i][8] = bit(f, i);
    out[n - 8][8] = 1;
    if (v >= 7) {
      const vb = versionBits(v);
      for (let i = 0; i < 18; i++) out[n - 11 + i % 3][Math.floor(i / 3)] = out[Math.floor(i / 3)][n - 11 + i % 3] = bit(vb, i);
    }
    return {version: v, mask: best, size: n, modules: out};
  }, {ecc, formatBits, versionBits, penalty});
})();

function qrSvg(text, {label = "QR code"} = {}) {
  const q = qrCode(text);
  if (!q) return null;
  const d = q.modules.flatMap((row, r) => [...row.join("").matchAll(/1+/g)].map(x => `M${x.index + 4} ${r + 4}h${x[0].length}v1h-${x[0].length}z`)).join("");
  return el("svg:svg", {class: "qr", viewBox: `0 0 ${q.size + 8} ${q.size + 8}`, role: "img", "aria-label": label, "shape-rendering": "crispEdges"},
    el("svg:path", {d, fill: "currentColor"}));
}
