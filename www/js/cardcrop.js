/* Sanguity · a card's photo found, straightened and cropped, in the browser (#1168): the insurance card dialog's photos
   (insurance.js). Nothing here leaves the device but the cropped card the dialog then sends; no outside service, no
   library.

   Finding the card (ccFind, on a copy at most CC_FIND px across, grey): blurred, its edges by their gradient (Sobel),
   each edge pixel voting for the straight lines through it along its own edge (a Hough transform, by the gradient's
   direction), the strongest lines kept; then every two pairs of roughly parallel lines, roughly across each other,
   make a four-sided shape, scored by how much of each side the edges follow, its size, and how near its sides come to
   a card's (85.6 by 54 mm, ID-1). The best one, if its sides are followed well enough, is the card; else none.
   Straightening it (ccWarp): the perspective that maps the four corners onto a card-shaped rectangle (a homography),
   each pixel of the result read from the photo between its four nearest (bilinear). Turned upright: landscape, then a
   quarter turn at a time by Turn.

   ccCrop(input, what): the dialog: the photo with its four corners, found or (when the card isn't found) set in from
   the edges, each moved by dragging it or with the arrow keys once focused (Shift for bigger steps; where it is, said
   as it moves), the crop shown beside as it will be saved. -> {blob (a JPEG, the card alone, no metadata: a canvas
   writes none), src, pts, turn, whole} or null. input: {file} the first time, {src, pts, turn} to adjust again.
   Plain script; all files share one scope. */
"use strict";
const CC_RATIO = 85.6 / 54, CC_FIND = 360, CC_SRC = 2000, CC_OUT = 1200;

/* gray: w*h values 0..255 -> {pts: [[x, y] x4] (top-left, top-right, bottom-right, bottom-left, as found), score} or null */
function ccFind(gray, w, h) {
  const n = w * h, small = Math.min(w, h);
  let a = Float32Array.from(gray), b = new Float32Array(n);
  for (let pass = 0; pass < 2; pass++) {   // a 3 x 3 box blur, twice
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      let s = 0, c = 0;
      for (let yy = Math.max(0, y - 1); yy <= Math.min(h - 1, y + 1); yy++)
        for (let xx = Math.max(0, x - 1); xx <= Math.min(w - 1, x + 1); xx++) { s += a[yy * w + xx]; c++; }
      b[y * w + x] = s / c;
    }
    [a, b] = [b, a];
  }
  const mag = new Float32Array(n), dir = new Float32Array(n);
  for (let y = 1; y < h - 1; y++) for (let x = 1; x < w - 1; x++) {
    const i = y * w + x;
    const gx = a[i - w + 1] + 2 * a[i + 1] + a[i + w + 1] - a[i - w - 1] - 2 * a[i - 1] - a[i + w - 1];
    const gy = a[i + w - 1] + 2 * a[i + w] + a[i + w + 1] - a[i - w - 1] - 2 * a[i - w] - a[i - w + 1];
    mag[i] = Math.hypot(gx, gy); dir[i] = Math.atan2(gy, gx);
  }
  const sorted = Float32Array.from(mag).sort(), T = Math.max(sorted[Math.floor(n * 0.88)], 12);
  // the lines: theta in [0, pi) by 1 degree, rho from the middle of the picture
  const NT = 180, R = Math.ceil(Math.hypot(w, h) / 2) + 2, NR = 2 * R + 1, acc = new Float32Array(NT * NR);
  const COS = Float32Array.from({length: NT}, (_, t) => Math.cos(t * Math.PI / NT)), SIN = Float32Array.from({length: NT}, (_, t) => Math.sin(t * Math.PI / NT));
  for (let y = 2; y < h - 2; y++) for (let x = 2; x < w - 2; x++) {
    const i = y * w + x;
    if (mag[i] < T) continue;
    let t0 = Math.round(((dir[i] % Math.PI) + Math.PI) % Math.PI / Math.PI * NT);
    const wt = Math.min(mag[i] / T, 3), cx = x - w / 2, cy = y - h / 2;
    for (let d = -3; d <= 3; d++) {
      let t = t0 + d;
      if (t < 0) t += NT; else if (t >= NT) t -= NT;
      const r = Math.round(cx * COS[t] + cy * SIN[t]);
      acc[t * NR + r + R] += wt;
    }
  }
  const peaks = [];
  for (let t = 0; t < NT; t++) for (let r = 2; r < NR - 2; r++) {
    const v = acc[t * NR + r];
    if (v < small * 0.15) continue;
    let top = true;
    for (let dt = -2; dt <= 2 && top; dt++) for (let dr = -3; dr <= 3; dr++) {
      if (!dt && !dr) continue;
      let tt = t + dt, rr = r;
      if (tt < 0) { tt += NT; rr = NR - 1 - r; } else if (tt >= NT) { tt -= NT; rr = NR - 1 - r; }
      const u = acc[tt * NR + Math.min(NR - 1, Math.max(0, rr + dr))];
      if (u > v || (u === v && (dt < 0 || (dt === 0 && dr < 0)))) { top = false; break; }
    }
    if (top) peaks.push({t: t * Math.PI / NT, r: r - R, v});
  }
  peaks.sort((p, q) => q.v - p.v);
  const lines = [];
  for (const p of peaks) {   // the strongest, none within 6 degrees and 8 px of a stronger one
    if (lines.some(q => { const d = Math.abs(p.t - q.t); return d < 0.105 ? Math.abs(p.r - q.r) < 8 : Math.PI - d < 0.105 && Math.abs(p.r + q.r) < 8; })) continue;
    lines.push(p);
    if (lines.length >= 22) break;
  }
  // a line as a unit normal (cos t, sin t) and its distance; two of them roughly parallel (within 22 degrees)
  const angle = (p, q) => { const d = Math.abs(p.t - q.t) % Math.PI; return Math.min(d, Math.PI - d); };
  const pairs = [];
  for (let i = 0; i < lines.length; i++) for (let j = i + 1; j < lines.length; j++) {
    const p = lines[i], q = lines[j];
    if (angle(p, q) > 0.384) continue;
    const qr = Math.abs(p.t - q.t) > Math.PI / 2 ? -q.r : q.r;   // q's distance along p's normal
    if (Math.abs(p.r - qr) < small * 0.15) continue;
    pairs.push([p, q]);
  }
  const meet = (p, q) => {
    const d = Math.cos(p.t) * Math.sin(q.t) - Math.sin(p.t) * Math.cos(q.t);
    if (Math.abs(d) < 1e-6) return null;
    return [(p.r * Math.sin(q.t) - q.r * Math.sin(p.t)) / d + w / 2, (q.r * Math.cos(p.t) - p.r * Math.cos(q.t)) / d + h / 2];
  };
  const edgeAt = (x, y, nx, ny) => {   // an edge near (x, y) along the side: 1 or -1 by which way its gradient crosses it
    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
      const xx = Math.round(x) + dx, yy = Math.round(y) + dy;
      if (xx < 1 || yy < 1 || xx >= w - 1 || yy >= h - 1) continue;
      const i = yy * w + xx, c = Math.cos(dir[i]) * nx + Math.sin(dir[i]) * ny;
      if (mag[i] >= T * 0.5 && Math.abs(c) > 0.8) return Math.sign(c);
    }
    return 0;
  };
  const support = (p, q, way) => {   // how much of the side edges follow; way: [across one way, the other], added to
    const len = Math.hypot(q[0] - p[0], q[1] - p[1]), k = Math.max(16, Math.round(len / 3));
    const nx = -(q[1] - p[1]) / len, ny = (q[0] - p[0]) / len;
    let hit = 0;
    for (let s = 1; s < k; s++) {
      const e = edgeAt(p[0] + (q[0] - p[0]) * s / k, p[1] + (q[1] - p[1]) * s / k, nx, ny);
      if (e) { hit++; way[e > 0 ? 0 : 1]++; }
    }
    return hit / (k - 1);
  };
  let best = null;
  for (let i = 0; i < pairs.length; i++) for (let j = i + 1; j < pairs.length; j++) {
    const [p1, p2] = pairs[i], [q1, q2] = pairs[j];
    if (Math.min(angle(p1, q1), angle(p1, q2), angle(p2, q1), angle(p2, q2)) < 0.87) continue;   // across: 50 degrees at least
    const pts = [meet(p1, q1), meet(q1, p2), meet(p2, q2), meet(q2, p1)];
    if (pts.some(c => !c || c[0] < -0.04 * w || c[0] > 1.04 * w || c[1] < -0.04 * h || c[1] > 1.04 * h)) continue;
    let sign = 0, convex = true, area = 0;
    for (let k = 0; k < 4; k++) {
      const o = pts[k], p = pts[(k + 1) % 4], q = pts[(k + 2) % 4];
      const cr = (p[0] - o[0]) * (q[1] - p[1]) - (p[1] - o[1]) * (q[0] - p[0]);
      if (sign && Math.sign(cr) !== sign) convex = false;
      sign = Math.sign(cr);
      area += o[0] * p[1] - p[0] * o[1];
    }
    area = Math.abs(area) / 2 / (w * h);
    if (!convex || area < 0.06 || area > 0.995) continue;
    const side = k => Math.hypot(pts[(k + 1) % 4][0] - pts[k][0], pts[(k + 1) % 4][1] - pts[k][1]);
    const l1 = (side(0) + side(2)) / 2, l2 = (side(1) + side(3)) / 2, ratio = Math.max(l1, l2) / Math.min(l1, l2);
    const fit = Math.exp(-(Math.log(ratio / CC_RATIO) ** 2) / (2 * 0.2 * 0.2));
    if (fit < 0.05) continue;
    const way = [0, 0], s = [0, 1, 2, 3].map(k => support(pts[k], pts[(k + 1) % 4], way)), low = Math.min(...s);
    if (low < 0.3) continue;
    const same = Math.max(...way) / (way[0] + way[1]);   // a card is lighter (or darker) than what's round it on every side
    const score = (s[0] + s[1] + s[2] + s[3]) / 4 * Math.sqrt(low) * fit * Math.pow(area, 0.3) * same * same;
    if (!best || score > best.score) best = {pts, score};
  }
  return best && {pts: ccOrder(best.pts), score: best.score};
}

/* four corners in the order top-left, top-right, bottom-right, bottom-left, by where they are */
function ccOrder(pts) {
  const cx = pts.reduce((s, p) => s + p[0], 0) / 4, cy = pts.reduce((s, p) => s + p[1], 0) / 4;
  const round = [...pts].sort((p, q) => Math.atan2(p[1] - cy, p[0] - cx) - Math.atan2(q[1] - cy, q[0] - cx));   // clockwise, y down
  const first = round.reduce((k, p, i) => p[0] + p[1] < round[k][0] + round[k][1] ? i : k, 0);
  return [0, 1, 2, 3].map(k => round[(first + k) % 4].map(Number));
}

/* the corners for the result's top-left, top-right, bottom-right and bottom-left: upright (landscape) as found,
   then a quarter turn clockwise each turn; and the result's size */
function ccLayout(pts, turn = 0) {
  let c = ccOrder(pts);
  const d = (p, q) => Math.hypot(p[0] - q[0], p[1] - q[1]);
  if (d(c[0], c[1]) + d(c[2], c[3]) < d(c[1], c[2]) + d(c[3], c[0])) c = [c[3], c[0], c[1], c[2]];   // taken upright: lying down
  for (let k = 0; k < ((turn % 4) + 4) % 4; k++) c = [c[3], c[0], c[1], c[2]];
  const wide = turn % 2 === 0;
  return {c, w: wide ? CC_OUT : Math.round(CC_OUT / CC_RATIO), h: wide ? Math.round(CC_OUT / CC_RATIO) : CC_OUT};
}

/* the homography taking the rectangle w x h onto the corners c (top-left first, clockwise): [h0..h7] */
function ccHomography(c, w, h) {
  const from = [[0, 0], [w, 0], [w, h], [0, h]], A = [];
  for (let k = 0; k < 4; k++) {
    const [u, v] = from[k], [x, y] = c[k];
    A.push([u, v, 1, 0, 0, 0, -u * x, -v * x, x], [0, 0, 0, u, v, 1, -u * y, -v * y, y]);
  }
  for (let i = 0; i < 8; i++) {   // Gaussian elimination, partial pivoting
    let p = i;
    for (let r = i + 1; r < 8; r++) if (Math.abs(A[r][i]) > Math.abs(A[p][i])) p = r;
    [A[i], A[p]] = [A[p], A[i]];
    for (let r = 0; r < 8; r++) {
      if (r === i || !A[i][i]) continue;
      const f = A[r][i] / A[i][i];
      for (let k = i; k < 9; k++) A[r][k] -= f * A[i][k];
    }
  }
  return A.map((row, i) => row[8] / row[i]);
}

/* the card straightened: src {data (RGBA), width, height}, its corners c (ccLayout's), into out {data, width, height} */
function ccWarp(src, c, out) {
  const H = ccHomography(c, out.width, out.height), sw = src.width, sh = src.height, s = src.data, o = out.data;
  for (let v = 0; v < out.height; v++) for (let u = 0; u < out.width; u++) {
    const z = H[6] * u + H[7] * v + 1, x = Math.min(sw - 1.001, Math.max(0, (H[0] * u + H[1] * v + H[2]) / z)),
      y = Math.min(sh - 1.001, Math.max(0, (H[3] * u + H[4] * v + H[5]) / z));
    const x0 = x | 0, y0 = y | 0, fx = x - x0, fy = y - y0, i = (y0 * sw + x0) * 4, j = (v * out.width + u) * 4;
    for (let k = 0; k < 3; k++)
      o[j + k] = (s[i + k] * (1 - fx) + s[i + 4 + k] * fx) * (1 - fy) + (s[i + sw * 4 + k] * (1 - fx) + s[i + sw * 4 + 4 + k] * fx) * fy;
    o[j + 3] = 255;
  }
  return out;
}

/* ---------------------------------------------------------------- the dialog */
const CC_NAMES = ["Top-left corner", "Top-right corner", "Bottom-right corner", "Bottom-left corner"];

async function ccSource(file) {   // the photo, upright as taken (its orientation applied), at most CC_SRC px across
  const bmp = await createImageBitmap(file);
  const k = Math.min(1, CC_SRC / Math.max(bmp.width, bmp.height)), cv = el("canvas", {width: Math.round(bmp.width * k), height: Math.round(bmp.height * k)});
  cv.getContext("2d").drawImage(bmp, 0, 0, cv.width, cv.height);
  bmp.close?.();
  return cv;
}

function ccDetect(src) {   // -> {pts in src's pixels, found}
  const k = Math.min(1, CC_FIND / Math.max(src.width, src.height)), w = Math.max(8, Math.round(src.width * k)), h = Math.max(8, Math.round(src.height * k));
  const cv = el("canvas", {width: w, height: h}), g = cv.getContext("2d", {willReadFrequently: true});
  g.drawImage(src, 0, 0, w, h);
  const px = g.getImageData(0, 0, w, h).data, gray = new Uint8ClampedArray(w * h);
  for (let i = 0; i < w * h; i++) gray[i] = px[4 * i] * 0.299 + px[4 * i + 1] * 0.587 + px[4 * i + 2] * 0.114;
  const got = ccFind(gray, w, h);
  if (got) return {found: true, pts: got.pts.map(([x, y]) => [Math.min(src.width, Math.max(0, x / k)), Math.min(src.height, Math.max(0, y / k))])};
  const mx = src.width * 0.1, my = src.height * 0.1;
  return {found: false, pts: [[mx, my], [src.width - mx, my], [src.width - mx, src.height - my], [mx, src.height - my]]};
}

function ccRender(src, pts, turn, long) {   // the crop as a canvas, its long side `long` px
  const L = ccLayout(pts, turn), k = long / CC_OUT, w = Math.round(L.w * k), h = Math.round(L.h * k);
  const from = src.sgPixels ||= src.getContext("2d", {willReadFrequently: true}).getImageData(0, 0, src.width, src.height);   // read once
  const cv = el("canvas", {width: w, height: h}), o = cv.getContext("2d"), img = o.createImageData(w, h);
  ccWarp(from, L.c, img);
  o.putImageData(img, 0, 0);
  return cv;
}

const ccBlob = cv => new Promise((ok, no) => cv.toBlob(b => b ? ok(b) : no(new Error("The picture couldn't be made.")), "image/jpeg", 0.9));

async function ccCrop(input, what) {
  let src = input.src;
  if (!src) {
    try { src = await ccSource(input.file); }
    catch (e) { throw new Error(`${input.file.name || "That file"} couldn't be opened as a picture.`); }
  }
  const det = input.pts ? {found: null, pts: input.pts.map(p => [...p])} : ccDetect(src);
  let pts = det.pts, turn = input.turn || 0, whole = false;
  const id = "cc" + (ccCrop.n = (ccCrop.n || 0) + 1);
  const say = el("p", {class: "cc-say", id: id + "-say", "aria-live": "polite"});
  // the photo and its corners, one SVG in the photo's own pixels: each corner a focusable handle (role button)
  const W = src.width, Hh = src.height;
  const poly = el("svg:polygon", {});
  const pic = el("svg:image", {width: W, height: Hh, preserveAspectRatio: "none"});
  const stage = el("svg:svg", {class: "cc-stage", viewBox: `0 0 ${W} ${Hh}`, role: "group", "aria-label": `The photo of the ${what}, with its four corners`}, pic, poly);
  pic.setAttribute("href", src.toDataURL("image/jpeg", 0.85));   // (a data: address: the pages' policy allows no blob: one)
  const prev = el("canvas", {class: "cc-prev", role: "img", "aria-label": `The ${what}, cropped as it will be saved`});
  const pct = v => Math.round(v * 100);
  const where = i => `${pct(pts[i][0] / W)}% across, ${pct(pts[i][1] / Hh)}% down`;
  const at = e => {   // a pointer's place in the photo's pixels
    const q = new DOMPoint(e.clientX, e.clientY).matrixTransform(stage.getScreenCTM().inverse());
    return [Math.min(W, Math.max(0, q.x)), Math.min(Hh, Math.max(0, q.y))];
  };
  let later = 0;
  const handles = [0, 1, 2, 3].map(i => {
    const b = el("svg:g", {class: "cc-h", tabindex: "0", role: "button", "aria-describedby": id + "-how"}, el("svg:circle", {class: "cc-hit"}), el("svg:circle", {class: "cc-ring"}));
    b.addEventListener("keydown", e => {
      const step = e.shiftKey ? 0.05 : 0.005, mv = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]}[e.key];
      if (!mv) return;
      e.preventDefault();
      pts[i] = [Math.min(W, Math.max(0, pts[i][0] + mv[0] * step * W)), Math.min(Hh, Math.max(0, pts[i][1] + mv[1] * step * Hh))];
      draw();
      clearTimeout(later); later = setTimeout(() => { say.textContent = `${b.dataset.name}: ${where(i)}.`; }, 300);
    });
    b.addEventListener("pointerdown", e => { e.preventDefault(); b.setPointerCapture(e.pointerId); b.focus(); });
    b.addEventListener("pointermove", e => { if (b.hasPointerCapture(e.pointerId)) { pts[i] = at(e); draw(); } });
    b.addEventListener("pointerup", e => { if (b.hasPointerCapture(e.pointerId)) { b.releasePointerCapture(e.pointerId); say.textContent = `${b.dataset.name}: ${where(i)}.`; } });
    stage.append(b);
    return b;
  });
  let queued = 0;
  function draw() {
    const named = ccOrder(pts), k = W / Math.max(1, stage.getBoundingClientRect().width || 320);   // (a handle 32 px across, however big the photo)
    handles.forEach((b, i) => {
      const [hit, ring] = b.children, n = named.findIndex(p => p[0] === pts[i][0] && p[1] === pts[i][1]);
      for (const c of [hit, ring]) { c.setAttribute("cx", pts[i][0]); c.setAttribute("cy", pts[i][1]); }
      hit.setAttribute("r", 22 * k); ring.setAttribute("r", 14 * k);
      b.dataset.name = CC_NAMES[n < 0 ? i : n];   // named for where it is now (corners dragged past each other change names)
      b.setAttribute("aria-label", `${b.dataset.name}, ${where(i)}`);
    });
    poly.setAttribute("points", pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" "));
    cancelAnimationFrame(queued);
    queued = requestAnimationFrame(() => {   // the preview, small
      const cv = ccRender(src, pts, turn, 360);
      prev.width = cv.width; prev.height = cv.height;
      prev.getContext("2d").drawImage(cv, 0, 0);
    });
  }
  new ResizeObserver(() => draw()).observe(stage);
  draw();
  const turnBtn = el("button", {type: "button", class: "btn small", text: "Turn", "aria-describedby": id + "-turned", onclick: () => {
    turn = (turn + 1) % 4; draw(); say.textContent = "Turned a quarter turn clockwise.";
  }});
  const wholeBtn = el("button", {type: "button", class: "btn small", text: "Use the whole photo", onclick: ev => { whole = true; ev.currentTarget.closest("dialog").close("yes"); }});
  const found = det.found === true ? "Found the card's edges: check each corner is on a corner of the card." :
    det.found === false ? "The card's edges weren't found: put each corner on a corner of the card." : "Put each corner on a corner of the card.";
  const yes = await ask({title: `Crop the ${what}`, action: "Use this crop", cls: "cc-dlg", message: [
    el("p", {text: found}),
    el("div", {class: "cc-wrap"}, stage),
    el("p", {class: "hint", id: id + "-how", text: "Drag a corner, or focus it and use the arrow keys (with Shift for bigger steps)."}),
    say,
    el("p", {class: "hint", id: id + "-turned", text: "As it will be saved, the card upright:"}), prev,
    el("div", {class: "row"}, turnBtn, wholeBtn)]});
  if (!yes) return null;
  if (whole) {
    const k = Math.min(1, 1600 / Math.max(src.width, src.height)), cv = el("canvas", {width: Math.round(src.width * k), height: Math.round(src.height * k)});
    cv.getContext("2d").drawImage(src, 0, 0, cv.width, cv.height);
    return {blob: await ccBlob(cv), src, pts, turn, whole: true};
  }
  return {blob: await ccBlob(ccRender(src, pts, turn, CC_OUT)), src, pts, turn, whole: false};
}
