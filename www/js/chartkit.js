/* Sanguity · the chart kit (#291): the parts every time chart is drawn with, so they read as one: Labs (trends.js
   drawChart) and Body and activity (health.js hChart) draw through it. Each part is its own small function, for the
   rest of the kit to plug in beside (the event track, the overview strip, sparklines):
   - ckScale, niceTicks, ckNum: the scales, and a value axis's round numbers, written as they are; ckFit, a label cut
     to its room; ckLines, one broken into lines that fit it;
   - ckTimeTicks, ckAxis, ckFoot: the time axis, its ticks at two levels (days over months, months over years), and
     the plot's foot alone, for a chart in a stack under one axis;
   - ckGrid, ckUnit: the value axis, a hairline and a label at each round number, and its unit as its title;
   - ckBand, ckBandLabel, ckBandLabels: the reference band, each result's own range, neutral, and its numbers at the
     right; a lab's every step said with its source (#752);
   - ckDecimate: a series with more points than pixels, drawn with what each pixel shows (#292);
   - ckLegend: a chart's key, each mark keyed as it's drawn (a line for a line);
   - ckCross, ckSync, ckTip: the crosshair, at one time on every time chart of a view, and the hover's readout pinned to
     the top of the plot beside it; ckAt, ckNearest, ckEach, ckOne: every result at the time hovered or stepped to, each
     said (#848), ckTimes every time's; ckRead, ckReadAt: a chart's readout in its header, as big whatever it says
     (#865, #894), at the width it's shown (ckWords, ckWrap, ckReadFit: its room, #900);
   - ckFlagRows: names over marks in up to three rows, with leader lines and "+N" (Labs' event track, event-track.js);
   - abnMark: an out-of-range result's ▲, ▼ or ◆;
   - ckSpark: the one sparkline, scaled to the reference band (the Overview, the visit summary, a share link's page);
   - ckSolo: a lab's chart on its own, at its box's width (a share link's histories, the visit summary's trends, #302);
   - ckFlagNames: milestones named so over a chart, flags on its top edge (Body and activity's first chart, #298);
   - ckStripBins, ckStripBars, ckStripH, ckStripDates, ckStripWindow: the overview strip, every year's records with the
     period a window on them (#295, charts.js stripDraw), its dates' row as tall as the text (#641);
   - ckPos, ckPosSpan, ckPosTicks, CK_SERIES, ckSeriesKey, ckDumbbell: labs compared on one scale, each result's place
     in its own range, and a lab's medians before and after a date (#297, compare.js);
   - ckHour, ckHourTicks, ckSpread: a day's hours as an axis, and a value's spread at each point, its median and
     percentile bands (#303, the glucose card's 24-hour profile, health.js).
   CHART_LAYERS (trends.js) and TL_LANES (timeline.js) stay the plug-ins of Labs' event track and the Timeline.
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
/* ---- scales */
/* d0..d1 onto r0..r1 (a value's B..T, a time's L..R), and back with .inv */
function ckScale(d0, d1, r0, r1) {
  const k = (r1 - r0) / (d1 - d0 || 1), f = v => r0 + (v - d0) * k;
  f.inv = p => d0 + (p - r0) / k;
  return f;
}
/* about n round numbers from lo to hi: steps of 1, 2, 2.5 or 5 times a power of ten */
function niceTicks(lo, hi, n) { const span = hi - lo, step0 = span / n, mag = Math.pow(10, Math.floor(Math.log10(step0))), step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => span / s <= n) || 10 * mag; const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10)); return out; }
/* a value axis's number as it is, with the decimals its step gives it ("176.5" between "176" and "177"): never
   rounded, so no gridline says another's number or its neighbour's (#654). Never "-0", which a step's sum can be */
const ckNum = v => (v || 0).toLocaleString("en-US", {maximumFractionDigits: 10, useGrouping: false});
/* how wide a chart label is, in the chart's own units: laid out in a hidden <text> of its class, so in the font and
   weight the chart draws it in. app.css lays these labels out at their own size (geometricPrecision), so the width
   doesn't change with the chart's scale, as a canvas's or a hinted font's would (#253). Kept per class and text until a
   font finishes loading; a chart drawn before Inter arrived measured its fallback, which is wider, so nothing overflows
   (drawing again then would move what's under the pointer: a tooltip's ▲ loads the fallback face too). */
const labelSizes = new Map();
let labelText = null;
function labelW(text, cls) {
  const key = cls + "\n" + text;
  if (!labelSizes.has(key)) {
    if (!labelText || !labelText.isConnected) labelText = document.body.appendChild(el("svg:svg", {class: "vh", "aria-hidden": "true"}, el("svg:text", {y: -40}))).firstChild;   // (over its 1 px box, not past the page's end)
    labelText.setAttribute("class", cls);
    labelText.textContent = text;
    labelSizes.set(key, labelText.getComputedTextLength());
  }
  return labelSizes.get(key);
}
document.fonts.addEventListener("loadingdone", () => labelSizes.clear());
/* text cut with "…" to the room there is, in a label of class cls (a lane's or a medication's name, #633) */
function ckFit(t, cls, room) { if (labelW(t, cls) <= room) return t; for (let n = t.length - 1; n > 1; n--) { const s = t.slice(0, n).trimEnd() + "…"; if (labelW(s, cls) <= room) return s; } return "…"; }
/* text broken at its spaces into lines no wider than room, in a label of class cls, each word whole (a word wider
   than room a line of its own): the heatmap's counts' heading at 200% text on a phone (#849) */
function ckLines(t, cls, room) {
  const out = [];
  for (const w of t.split(" ")) if (out.length && labelW(out.at(-1) + " " + w, cls) <= room) out[out.length - 1] += " " + w; else out.push(w);
  return out;
}
/* the labels' size against the 11.5 px the charts are laid out for, 1 or more: with larger text (200%, a phone's font
   size, #622) a chart's gutters, rows of labels and their offsets grow by it, so its labels stay inside it (1.4.4).
   The sizes labelW kept go when it changes */
let ckFs = 0, ckKv = 0;
const ckK = () => {
  // read once for a whole draw: kept until the drawing code running now is done (a microtask), as each read after the
  // page has changed made the browser work its styles out again, a quarter of a phone's frame of a drag (#300)
  if (ckKv) return ckKv;
  if (!labelText?.isConnected) labelW("0", "");
  labelText.setAttribute("class", "");   // a chart label's size, not that of the class labelW measured last (a lane's name is larger)
  const fs = parseFloat(getComputedStyle(labelText).fontSize);
  if (fs !== ckFs) { ckFs = fs; labelSizes.clear(); }
  ckKv = Math.max(1, fs / 11.5); queueMicrotask(() => { ckKv = 0; });
  return ckKv;
};

/* ---- the time axis: ticks at two levels, the finer over the coarser: days over "Sep 2026", months over "2026"; years
   alone in one row. The step is the smallest whose labels fit side by side, 44 px apart at least: more ticks on a wider
   or zoomed chart, as many on every chart of the same width and period. A month is never written so that it reads as a
   day ("Sep 26", #561): over its year, or where a row has room for one label only, as ckMonth writes it (Costs' charts,
   the heatmap's axis, the workouts' calendar) */
const ckMonth = t => { const d = zdate(t); return `${d.toLocaleDateString(undefined, {month: "short"})} ’${String(d.getFullYear()).slice(2)}`; };   // "Sep ’26"
const CK_STEPS = [[1, "d"], [2, "d"], [7, "d"], [14, "d"], [1, "m"], [2, "m"], [3, "m"], [6, "m"], [1, "y"], [2, "y"], [5, "y"], [10, "y"], [20, "y"]];
const CK_DAYS = {d: 1, m: 30.44, y: 365.25};
/* the ticks of one step from a to b, each {t, label, unit}: unit names the coarser level it's in (its month, its year) */
function ckStepTicks(a, b, n, u) {
  const d = zdate(a), out = [];
  d.setHours(0, 0, 0, 0);
  if (u === "d") {
    if (n >= 7) d.setDate(d.getDate() - (d.getDay() + 6) % 7);   // Mondays; every other one by its week's number
    if (n === 14 && Math.floor((d.getTime() + 4 * DAY) / (7 * DAY)) % 2) d.setDate(d.getDate() + 7);
    if (n === 2 && Math.round(d.getTime() / DAY) % 2) d.setDate(d.getDate() + 1);
  } else if (u === "m") { d.setDate(1); d.setMonth(Math.floor(d.getMonth() / n) * n); }
  else { d.setMonth(0, 1); d.setFullYear(Math.floor(d.getFullYear() / n) * n); }
  for (; d.getTime() <= b; u === "d" ? d.setDate(d.getDate() + n) : u === "m" ? d.setMonth(d.getMonth() + n) : d.setFullYear(d.getFullYear() + n)) {
    const t = d.getTime(); if (t < a) continue;
    out.push(u === "d" ? {t, label: String(d.getDate()), unit: d.getFullYear() * 12 + d.getMonth(), major: d.toLocaleDateString(undefined, {month: "short", year: "numeric"})}
      : u === "m" ? {t, label: d.toLocaleDateString(undefined, {month: "short"}), unit: d.getFullYear(), major: String(d.getFullYear())}
      : {t, label: String(d.getFullYear())});
  }
  return out;
}
/* -> {rows: 1 or 2, ticks: [{t, x, label, major (the coarser level's words, on a unit's first tick; null), mx}]}; x the
   time scale, W the chart's width (a label kept inside it); least: the shortest step, in days (a chart of months' bars
   has no days to tick, Costs', #300) */
function ckTimeTicks(a, b, x, W, least = 0) {
  ckK();
  const perDay = Math.abs(x(a + DAY) - x(a));
  let ticks = [], u = "y";
  for (const [n, unit] of CK_STEPS) {
    if (n * CK_DAYS[unit] * perDay < 36 || n * CK_DAYS[unit] < least) continue;   // too close to try (36 px: on a phone six months of All is about 43, #615); the labels then tested below
    ticks = ckStepTicks(a, b, n, unit); u = unit;
    if (ticks.every((k, i) => !i || x(k.t) - x(ticks[i - 1].t) >= (labelW(k.label, "") + labelW(ticks[i - 1].label, "")) / 2 + 12)) break;
  }
  let prev = null;
  for (const k of ticks) {
    const w = labelW(k.label, "");
    k.x = Math.max(w / 2, Math.min(W - w / 2, x(k.t)));
    if (u === "y" || (prev && prev.unit === k.unit)) k.major = null;
    else k.mx = Math.max(0, Math.min(W - labelW(k.major, "ck-yr"), k.x - w / 2));   // left-aligned with its tick's label
    prev = k;
  }
  // a coarser label that would run into the next one goes: the first, from the period's start, before a real boundary
  const majors = ticks.filter(k => k.major);
  majors.forEach((k, i) => { const nx = majors[i + 1]; if (nx && k.mx + labelW(k.major, "ck-yr") + 12 > nx.mx) k.major = null; });
  return {rows: u === "y" ? 1 : 2, ticks};
}
/* the height under the plot that ckAxis takes, for rows of labels */
const ckAxisH = rows => Math.round((rows === 2 ? 36 : 22) * ckK());
/* the plot's foot: a line along B, from L to R; ckAxis draws it, and a chart in a stack of them with one axis (#293) */
const ckFoot = (g, L, R, B) => g.push(el("svg:line", {x1: L, x2: R, y1: B, y2: B, stroke: "var(--axis)"}));
/* the time axis: its line along the plot's foot (B, from L to R), then its labels, the finer row's baseline at y1 (as
   far under B as larger text needs, ckK) and the coarser's under it */
function ckAxis(g, tk, L, R, B, y1) {
  const ck = ckK(); y1 = B + (y1 - B) * ck;
  ckFoot(g, L, R, B);
  for (const k of tk.ticks) {
    g.push(el("svg:text", {x: k.x, y: y1, "text-anchor": "middle", text: k.label}));
    if (k.major) g.push(el("svg:text", {class: "ck-yr", x: k.mx, y: y1 + 14 * ck, text: k.major}));
  }
}
/* the value axis: a hairline across the plot at each of vals, labelled left of it with fmt(v), the number as it is
   (ckNum) in whatever words fmt puts round it */
function ckGrid(g, y, vals, L, R, fmt = ckNum) {
  const k = ckK();
  for (const v of vals) g.push(el("svg:line", {class: "gridline", x1: L, x2: R, y1: y(v), y2: y(v)}), el("svg:text", {x: L - 6, y: y(v) + 4 * k, "text-anchor": "end", text: fmt(v)}));
}
/* the value axis's title: the unit, over its numbers at the top left, level, its baseline at yb (#293) */
const ckUnit = (g, unit, yb) => unit && g.push(el("svg:text", {class: "ck-unit", x: 0, y: yb, text: unit}));

/* ---- the reference band (#293): each result's own printed range as steps in time (ranges.js rangeSteps), a neutral
   wash with solid hairline edges, never a status colour: it says where the report's range is, not whether a value is
   good (app.css .refband, .refedge; the lab panel's strip draws with the same). Each edge one stepped path, joined where
   neighbouring steps both have it. The band is clipped to the plot, T to B, L to R */
function ckBand(g, steps, x, y, L, R, T, B) {
  const edge = {lo: "", hi: ""};
  steps.forEach((st, i) => {
    const xa = Math.max(L, x(st.a)), xb = Math.min(R, x(st.b)); if (xb <= xa) return;
    const a = st.hi != null ? Math.max(T, y(st.hi)) : T, b = st.lo != null ? Math.min(B, y(st.lo)) : B;
    if (b > a) g.push(el("svg:rect", {class: "refband", x: xa, y: a, width: xb - xa, height: b - a}));
    for (const k of ["lo", "hi"]) {
      if (st[k] == null) continue;
      const ey = Math.max(T, Math.min(B, y(st[k]))).toFixed(1);
      edge[k] += (i && steps[i - 1][k] != null && edge[k] ? `V${ey}` : `M${xa.toFixed(1)},${ey}`) + `H${xb.toFixed(1)}`;
    }
  });
  for (const k of ["lo", "hi"]) if (edge[k]) g.push(el("svg:path", {class: "refedge", d: edge[k], fill: "none"}));
}
/* the band's numbers at the right of the plot, in ink: words ("13.5–17.5 on report") for the step st at the right end,
   set right-aligned at R, inside the band under its top edge, else over its foot, else just outside it, whichever is
   the first clear of every point (pts: [px, py]) and of the labels placed already (taken), inside the plot, T to B; L
   to R too, its first word alone where the words are wider (larger text, #622). Its offsets grow with the text (ckK).
   -> its box [x0, x1, baseline], or null */
function ckBandLabel(g, words, st, y, R, T, B, pts, L = 0, taken = []) {
  const x1 = R - 4;
  if (x1 - labelW(words, "ck-ref") < L) words = words.split(" ")[0];
  return ckRefAt(g, words, st, y, x1 - labelW(words, "ck-ref"), x1, T, B, pts, taken);
}
/* a lab's band said step by step, with where each range came from (#752; ranges.js rangeSaid): "3.5–5.1 on report",
   "5–20 from settings". The latest step at the plot's right where it reaches it (ckBandLabel), else over its own
   stretch, centred; each before it over its own (#717). Where its numbers don't fit there, its source alone ("on
   report", "from settings"), which may run past its stretch (centred on it) for the latest and for one whose source
   isn't the next step's, so a report's range beside one from settings is said either way; else nothing (its marks'
   titles and the table say it: a long chart's many narrow steps aren't each named). The latest first, none over a
   label placed already. at: the points, [px, py] */
function ckBandLabels(g, steps, x, y, L, R, T, B, at) {
  const taken = [], fits = (t, room) => labelW(t, "ck-ref") <= room;
  for (let i = steps.length - 1; i >= 0; i--) {
    const st = steps[i], a = Math.max(L, x(st.a)), b = Math.min(R, x(st.b)), end = b >= R - .5, last = i === steps.length - 1;
    if (b - a < 1) continue;
    const room = end ? R - 4 - L : b - a - 8, spill = last || st.rs !== steps[i + 1].rs;
    const words = fits(rangeSaid(st), room) ? rangeSaid(st) : fits(rangeSrc(st), spill ? R - L - 8 : room) ? rangeSrc(st) : null;
    if (!words) continue;
    const w = labelW(words, "ck-ref"), x0 = Math.max(L + 4, Math.min(R - 4 - w, (a + b - w) / 2));
    const box = end ? ckBandLabel(g, words, st, y, R, T, B, at, L, taken) : ckRefAt(g, words, st, y, x0, x0 + w, T, B, at, taken);
    if (box) taken.push(box);
  }
}
/* a band's words from x0 to x1 at the first of those places clear of every point and of the labels placed already
   (taken: their boxes, never overlapped), or at the first such place; none where there's no such place */
function ckRefAt(g, words, st, y, x0, x1, T, B, pts, taken = []) {
  const k = ckK(), hi = st.hi != null ? Math.max(T, y(st.hi)) : T, lo = st.lo != null ? Math.min(B, y(st.lo)) : B;
  const inside = lo - hi >= 16 * k ? (st.hi != null ? [hi + 12 * k, lo - 4] : [lo - 4, hi + 12 * k]) : [];   // by its printed edge first
  const at = [...inside, hi - 4, lo + 12 * k].filter(yb => yb - 10 * k >= T && yb + 2 <= B   // baselines, 10 px of text over each
    && !taken.some(([a, b, tb]) => a < x1 + 6 && x0 < b + 6 && Math.abs(tb - yb) < 14 * k));
  const clear = yb => !pts.some(([px, py]) => px > x0 - 5 && px < x1 + 5 && py > yb - 15 * k && py < yb + 6);
  const yb = at.find(clear) ?? at[0];
  if (yb == null) return null;
  g.push(el("svg:text", {class: "ck-ref", x: x1, y: yb, "text-anchor": "end", text: words}));
  return [x0, x1, yb];
}

/* ---- marks (#60): an out-of-range result's mark on a chart: a triangle pointing up (above its range) or down (below),
   a diamond for any other flag, by its way (ranges.js FLAG_OF); s is its half-width */
function abnMark(cx, cy, f, s = 4.5, cls = "pt abn") {
  const p = (dx, dy) => `${(cx + dx).toFixed(1)},${(cy + dy).toFixed(1)}`, way = FLAG_OF(f)?.way;
  const d = way === 1 ? `M${p(0, -s * 1.1)}L${p(s, s * .7)}L${p(-s, s * .7)}Z` : way === -1 ? `M${p(0, s * 1.1)}L${p(s, -s * .7)}L${p(-s, -s * .7)}Z`
    : `M${p(0, -s)}L${p(s, 0)}L${p(0, s)}L${p(-s, 0)}Z`;
  return el("svg:path", {class: cls, d});
}
/* the order a chart's marks are drawn in (#825): in time, at one time a flagged result's after an unflagged one's, so
   its ▲, ▼ or ◆ is never under a dot. pts in time order, with .ts */
const ckOnTop = pts => pts.some((p, i) => i && p.ts === pts[i - 1].ts && pts[i - 1][4] && !p[4]) ? [...pts].sort((a, b) => a.ts - b.ts || !!a[4] - !!b[4]) : pts;

/* ---- the sparkline (#299): the one on the Overview's tiles, the visit summary's trends and a share link's results.
   A lab's values in time on a scale set by its reference band, not by its own values: the band the middle 60% of the
   height (a one-sided range's edge at the middle), widened for a value outside it, so a change reads by its size
   against the range (Creatinine −0.03 and Platelets −15 both small) and two results never draw a full-height diagonal.
   With fewer than three results, no line: the latest one's mark against its band (nothing without a range). The plot
   is stretched to the box CSS gives it; the latest's mark is drawn over it at its own size, placed in percentages of
   the box, so never stretched: a dot in color, or ▲/▼/◆ in red when it's out of range.
   pts: Labs' points ([, value, low, high, flag] with .ts; ckPt makes one), in time order; band false for a measure
   with no range (the watch's, the scale's); r the dot's radius. Decorative: the text beside it says what it shows */
const ckPt = (ts, v, lo, hi, f, e) => Object.assign([null, v, lo ?? null, hi ?? null, f || ""], {ts}, e ? {7: e} : null);   // e: its edge (ranges.js rangeEdge, #780)
/* the value scale's ends for values vs and the band's lowest low and highest high (null: none) */
function ckSparkSpan(vs, lo, hi) {
  const v0 = Math.min(...vs), v1 = Math.max(...vs);
  let a, b;
  if (lo != null && hi != null && hi > lo) { const p = (hi - lo) / 3; a = lo - p; b = hi + p; }
  else if (lo != null || hi != null) { const e = hi ?? lo, k = Math.max(e - v0, v1 - e, Math.abs(e) / 10) || 1; a = e - k; b = e + k; }
  else { const k = (v1 - v0) / 10 || Math.abs(v0) / 10 || 1; a = v0 - k; b = v1 + k; }
  const m = (b - a) / 20;   // room for a value at either end
  return [Math.min(a, v0 - m), Math.max(b, v1 + m)];
}
function ckSpark(pts, {band = true, r = 3, color = INK, cls = ""} = {}) {
  const svg = el("svg:svg", {class: ("spark " + cls).trim(), "aria-hidden": "true"});
  pts = pts.filter(p => Number.isFinite(p[1]));
  if (!pts.length) return svg;
  const last = pts.at(-1), line = pts.length > 2, use = line ? pts : [last];
  const steps = band ? rangeSteps(use, -Infinity, Infinity) : [];
  if (!line && !steps.length) return svg;
  const los = steps.map(st => st.lo).filter(v => v != null), his = steps.map(st => st.hi).filter(v => v != null);
  const [d0, d1] = ckSparkSpan(use.map(p => p[1]), los.length ? Math.min(...los) : null, his.length ? Math.max(...his) : null);
  const x = ckScale(line ? pts[0].ts : last.ts - 1, last.ts, 3, 97), y = ckScale(d0, d1, 100, 0), g = [];
  ckBand(g, steps, x, y, 0, 100, 0, 100);
  if (line) g.push(el("svg:path", {d: pts.map((p, i) => (i ? "L" : "M") + x(p.ts).toFixed(1) + "," + y(p[1]).toFixed(1)).join(""), fill: "none", stroke: MUTED, "stroke-width": 1.5}));
  for (const e of g) e.setAttribute("vector-effect", "non-scaling-stroke");
  const at = {x: x(last.ts) + "%", y: y(last[1]) + "%"};
  svg.append(el("svg:svg", {viewBox: "0 0 100 100", preserveAspectRatio: "none", width: "100%", height: "100%"}, ...g),
    last[4] ? el("svg:svg", {...at, overflow: "visible"}, abnMark(0, 0, last[4], r * 1.15, "spark-abn"))   // ▲ or ▼, not only red (#60)
      : el("svg:circle", {cx: at.x, cy: at.y, r, fill: color}));
  return svg;
}

/* ---- a lab's chart on its own (#302): a share link's histories and the visit summary's trends, where there's no
   period, event track or hover state, and a table beside it says each result in full. Drawn at its box's width, and
   again as that changes (ckSoloRO; not while hidden, so a closed history isn't drawn till it's opened): the band
   (each result's own range, ckBand) with each step said, its source too (ckBandLabels: "3.5–5.1 on report", "5–20 from
   settings", #649, #717, #752), the value axis
   (ckGrid, ckUnit), the line through the results and a dot each, ▲/▼/◆ for one out of range (only those past 120
   results, as Labs'), and the time axis (ckAxis). Each mark's <title> is say(p), for a mouse. h: the plot's height,
   taller as the text is larger (ckK). print {w, h}: its width and plot's height as it's printed, drawn so as printing
   starts and as it was after (the page's print rules size its box: the share page's beside its table's first rows, as
   one over them left pages a third empty). pts: ckPt points in time order. -> the svg, its words label (role img) */
const ckSoloRO = new ResizeObserver(es => { for (const e of es) e.target._draw?.(); });
let ckPrint = false;
for (const t of ["beforeprint", "afterprint"]) addEventListener(t, () => {
  ckPrint = t === "beforeprint";
  for (const s of document.querySelectorAll("svg.ck-solo.drawn")) { s._w = 0; s._draw(); }
});
function ckSolo(pts, {h = 120, print = null, unit = "", label = "", say = () => ""} = {}) {
  const svg = el("svg:svg", {class: "ck-solo", role: "img", "aria-label": label});
  pts = pts.filter(p => Number.isFinite(p[1]));
  const fmt = v => v.toLocaleString("en-US", {maximumFractionDigits: 4});
  svg._draw = () => {
    const w = ckPrint && print ? print.w : Math.round(svg.getBoundingClientRect().width), k = ckK();
    if (!w || !pts.length || (w === svg._w && k === svg._k)) return;
    svg._w = w; svg._k = k;
    const ph = ckPrint && print ? print.h : h;
    const a0 = pts[0].ts, b0 = pts.at(-1).ts, pad = Math.max((b0 - a0) * .03, a0 === b0 ? 15 * DAY : DAY), a = a0 - pad, b = b0 + pad;
    const steps = rangeSteps(pts, a, b), vals = pts.map(p => p[1]);
    let lo = Math.min(...vals, ...steps.map(st => st.lo).filter(v => v != null)), hi = Math.max(...vals, ...steps.map(st => st.hi).filter(v => v != null));
    if (lo === hi) { lo -= Math.abs(lo) * .1 || 1; hi += Math.abs(hi) * .1 || 1; }
    const p8 = (hi - lo) * .08; lo -= p8; hi += p8;
    const ticks = niceTicks(lo, hi, ph < 90 ? 3 : 5);
    const L = Math.max(...ticks.map(v => labelW(fmt(v), ""))) + 10, R = w - 6, T = (unit ? 22 : 8) * k, B = T + ph * k;
    const x = ckScale(a, b, L, R), y = ckScale(lo, hi, B, T), g = [];
    ckGrid(g, y, ticks, L, R, fmt);
    ckUnit(g, unit, T - 10 * k);
    ckBand(g, steps, x, y, L, R, T, B);
    ckBandLabels(g, steps, x, y, L, R, T, B, pts.map(p => [x(p.ts), y(p[1])]));   // each step said with its source (#649, #717, #752)
    if (pts.length > 1) g.push(el("svg:path", {class: "line", d: ckDecimate(pts.map(p => [x(p.ts), y(p[1])]), q => q[0], 1, {ends: true}).map((q, i) => (i ? "L" : "M") + q[0].toFixed(1) + "," + q[1].toFixed(1)).join("")}));
    for (const p of ckOnTop(pts)) if (pts.length <= 120 || p[4]) {
      const m = p[4] ? abnMark(x(p.ts), y(p[1]), p[4]) : el("svg:circle", {class: "pt", cx: x(p.ts), cy: y(p[1]), r: 3});
      m.append(el("svg:title", {text: say(p)}));
      g.push(m);
    }
    const tk = ckTimeTicks(a, b, x, w);
    ckAxis(g, tk, L, R, B, B + 15);
    svg.setAttribute("viewBox", `0 0 ${w} ${B + ckAxisH(tk.rows)}`);
    svg.classList.add("drawn");   // (app.css: no height till it's drawn)
    svg.replaceChildren(...g);
  };
  ckSoloRO.observe(svg);
  return svg;
}

/* ---- decimation (#292): a series with more points than its marks have room for is drawn with fewer, the extremes kept.
   items in time order, px(p) its x on the chart, w a column's width (1 px for a line or a band, a dot's width for dots,
   a bar's for bars). With more items than columns, each column keeps its lowest by lo(p) and its highest by hi(p) (lo
   null: the highest only, as for bars from zero), with ends its first and last too, so a line's path is the same to
   the pixel; keep(p) is never dropped (a flagged result's ▲ or ▼). Fewer: items as they are. What a chart's table,
   tooltip and keyboard read is the series itself, never this */
function ckDecimate(items, px, w, {lo = p => p[1], hi = lo || (p => p[1]), ends = false, keep} = {}) {
  const n = items.length;
  if (n < 3 || n <= (px(items[n - 1]) - px(items[0])) / w + 1) return items;
  const out = [];
  for (let i = 0; i < n;) {
    const c = Math.floor(px(items[i]) / w); let j = i + 1, l = i, h = i;   // (a column of one at least, NaN's too)
    for (; j < n && Math.floor(px(items[j]) / w) === c; j++) {
      if (lo && lo(items[j]) < lo(items[l])) l = j;
      if (hi(items[j]) > hi(items[h])) h = j;
    }
    const k = new Set(ends ? [i, l, h, j - 1] : lo ? [l, h] : [h]);
    if (keep) for (let m = i; m < j; m++) if (keep(items[m])) k.add(m);
    for (const m of [...k].sort((p, q) => p - q)) out.push(items[m]);
    i = j;
  }
  return out;
}

/* ---- the legend: items [colour, words, how it's drawn]: "line" a short stroke, 1 or "dot" a point, "ring" a hollow
   one (a visit's reading), "band" a wash, else a swatch (a bar, an area): the key mirrors the mark (app.css .legend i).
   The words in ink, the colour only on the key. tag: "span" for one in a chart's header line. (Its one style, a key's
   colour, is every chart's legend's: the kit's took the place of three) */
function ckLegend(items, tag = "div") {
  return el(tag, {class: "legend"}, ...items.map(([c, t, k]) => el("span", {},
    el("i", {class: k === 1 ? "dot" : k || null, style: {[k === "ring" ? "borderColor" : "background"]: c}}), t)));
}

/* ---- the crosshair, synced: a chart sets svg._hover(t), which puts its crosshair (and whatever else it marks) at its
   own nearest point to t, or hides it for null or nothing near, and returns what it marks there (a lab's chart: every
   result at that time, ckAt, #848). ckSync(svg, t) does so on every time chart (svg.ck) of svg's view, so they all
   show one date, and returns svg's own */
const ckCross = (T, B) => el("svg:line", {class: "cross", y1: T, y2: B, visibility: "hidden"});
function ckCrossAt(cross, px) {
  if (px == null) return cross.setAttribute("visibility", "hidden");
  cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
}
function ckSync(svg, t) {
  let mine = null;
  for (const o of (svg.closest("[id^=view-]") || document).querySelectorAll("svg.ck")) { const p = o._hover?.(t); if (o === svg) mine = p; }
  return mine;
}
/* the hover's readout: pinned to the top of the plot (svg._geom.T) beside the crosshair at px, wherever the pointer is
   along it, so it never covers the point under the pointer; the keyboard's is the same (core.js showTip keeps it on
   the screen: right of the crosshair, else left) */
function ckTip(svg, px, nodes) {
  if (!nodes.some(Boolean)) return hideTip();
  const p = svgPoint(svg, px, svg._geom.T);
  showTip(nodes, {clientX: p.clientX, clientY: p.clientY - 14});   // showTip sets it 14 px below the point it's given
}
/* ---- every result at one time (#848): two results at one time (two imports' draws, a pair marked "Not the same",
   #833) are one place on a chart, one stop of its keyboard (charts.js chartKeys) and of its hover, and each is said in
   the readout, the tooltip and what's announced. The keyboard went no further than their time (it looked for the
   time's index and found the first again), and what read the point nearest read the first, so the flagged one, drawn
   last over it (#825), was never said. ckAt(pts, t): pts' results at time t (pts with .ts), a flagged one first (the
   one ringed; ckFirst, their one order); ckTimes(pts): every time's results, each so, in pts' order; ckNearest(pts, t,
   tol): those at the time nearest t, within tol ([] for none); ckEach(at, say): their words, say(p) for one,
   "2 results: …; …" for more; ckOne(p, value): one of them told apart, its value as given, its flag and its range:
   "5.6 mmol/L ▲ High (3.5–5.3)" */
const ckFirst = (a, b) => !!b[4] - !!a[4];
const ckAt = (pts, t) => pts.filter(p => p.ts === t).sort(ckFirst);
function ckTimes(pts) { const at = new Map(); for (const p of pts) (at.get(p.ts) || at.set(p.ts, []).get(p.ts)).push(p); return [...at.values()].map(g => g.sort(ckFirst)); }
function ckNearest(pts, t, tol = Infinity) {
  let b = null;
  for (const p of pts) if (!b || Math.abs(p.ts - t) < Math.abs(b.ts - t)) b = p;
  return b && Math.abs(b.ts - t) <= tol ? ckAt(pts, b.ts) : [];
}
const ckEach = (at, say) => at.length > 1 ? `${at.length} results: ${at.map(say).join("; ")}` : at.length ? say(at[0]) : "";
const ckOne = (p, value) => [value, flagWord(p[4]), rangeWords(p) && `(${rangeWords(p)})`].filter(Boolean).join(" ");
/* ---- a chart's readout in its header (#865): the latest, or what's hovered or stepped to, the same size whatever it
   says, so the plot never moves under the pointer. A longer line (#848's two results at one time, one result's at
   700–1000 px or with larger text) wrapped the header, the plot dropped, the pointer hovered nothing, the readout said
   "Latest" again and the plot jumped back: the ▲ under the mouse said nothing. So what it says is in one cell of a grid
   (app.css .ck-read) with the widest three of all it can say there too, unseen, and its header is laid out for the
   longest; on a phone it wraps to its words, uncut (#862). And it's as tall as the most lines any of them takes at the
   width it has (#900: wrapped, a narrower one could take a line more than the widest three, and the chart moved
   still): each broken where the page breaks it (ckWords, ckWrap), its words measured by the page as it's drawn
   (ckReadFit; a canvas can't draw Inter's tabular figures, and its widths were a pixel off a glyph), and its room set
   again as the window is resized, a font loads or its width or its text's size changes otherwise (ckRooms, watching
   the room, its unseen probe), with nothing laid out at a hover. ckRead(box, says): box the readout; says each thing
   it can say, [text or element, ...]; -> say(parts), showing those. One each of those alike, every digit as 0 where
   they're tabular (each as wide), the widest three as one line in a canvas's measure */
let ckPen = null, ckFonts = 0;
/* txt's words, [[kind, text], ...] (kind 0 the box's own text, n its nth element's): [what's before each (the kind of
   the space, -1 nothing: a line's start, -2 a dash it can break after), its pieces [[kind, text], ...], ...], broken
   where the page breaks it: at a space, and after a range's dash ("(13.5–" "18.4)"; not at a hyphen or a slash) */
function ckWords(txt) {
  const out = [];
  let word = null, gap = -1;
  for (const [k, t] of txt) for (const s of t.split(/( +)/)) {
    if (s[0] === " ") { if (word) out.push(gap, word), word = null, gap = -1; if (out.length && gap < 0) gap = k; }   // spaces as one; none at a line's start
    else if (s) s.split(/(?<=–)/).forEach((d, i) => { if (i) out.push(gap, word), word = null, gap = -2; (word ??= []).push([k, d]); });
  }
  if (word) out.push(gap, word);
  return out;
}
/* how many lines a thing takes in a line room wide, broken as the page breaks it (ckWords). ws its widths (ckReadFit):
   [what's kept spare in each line, then for each word the space before it, what the two words beside it are wider by
   (Inter's x after a figure and a space is a ×: "356 ×10^3/uL"; at a line's start it's an x again), and the word]. A
   word wider than the line, broken by the page where it can be, is taken as the lines it fills, and the next word on a
   line of its own */
function ckWrap(ws, room) {
  room -= ws[0];
  let n = 0, x = room;   // x: the line so far, room when full
  for (let i = 1; i < ws.length; i += 3) {
    const g = ws[i] + ws[i + 1], w = ws[i + 2];
    if (x + g + w > room) n += Math.ceil(w / room) || 1, x = w > room ? room : w; else x += g + w;
  }
  return n || 1;
}
/* boxes' rooms (ckRead) fitted to their probes' width, all read at one layout, then written: their words measured by
   the page, in their own style (app.css .ck-sizer), when drawn, their font, size or spacing changes or a font has
   loaded, each piece alone, as it starts or ends a line, each two words of a kind side by side, and each thing whole,
   in one line (box._ckWide, each thing's widths for ckWrap); and each box's probe as many lines as the most anything it
   says takes (lines of the box's own, not a min-height: one in lh set from a ResizeObserver was worked out from the
   line of the box's parent). One hidden (no width) is left till it's shown */
function ckReadFit(boxes) {
  boxes = boxes.filter(b => b._ckProbe?.isConnected);
  const keys = boxes.map(b => { const cs = getComputedStyle(b); return [cs.fontWeight, cs.fontSize, cs.fontFamily, cs.letterSpacing, cs.wordSpacing, cs.fontVariantNumeric, ckFonts].join(); });
  const two = (ws, i) => {   // a word and the one before it, of one kind, the space's if there's one: [their kind, them side by side]
    const a = ws[i - 1], b = ws[i + 1], g = ws[i];
    return i && a.length === 1 && b.length === 1 && a[0][0] === b[0][0] && (g === -2 || g === a[0][0]) ? [a[0][0], a[0][1] + (g < 0 ? "" : " ") + b[0][1]] : null;
  };
  const jobs = boxes.map((box, j) => {
    if (box._ckKey === keys[j]) return [box, null, null, null];
    const at = new Map(), sizer = el("span", {class: "ck-sizer"});
    const piece = (kind, t) => { const key = kind + "\t" + t; if (!at.has(key)) { const e = kind ? box._ckKinds[kind - 1].cloneNode(false) : el("span"); e.textContent = t; at.set(key, sizer.appendChild(e)); } };
    for (const ws of box._ckToks) for (let i = 0; i < ws.length; i += 2) {
      if (ws[i] >= 0) piece(ws[i], " ");
      for (const [kind, t] of ws[i + 1]) piece(kind, t);
      if (two(ws, i)) piece(...two(ws, i));
    }
    const whole = box._ckParts.map(parts => sizer.appendChild(el("span", {class: "w"}, ...parts.map(p => p.nodeType === 1 ? p.cloneNode(true) : p))));
    box.append(sizer);
    return [box, at, whole, sizer];
  });
  const wide = e => e.getBoundingClientRect().width;
  for (const job of jobs) job.push(job[0]._ckProbe.getBoundingClientRect()), job[1] &&= new Map([...job[1]].map(([key, e]) => [key, wide(e)])), job[2] &&= job[2].map(wide);
  jobs.forEach(([box, w, whole, sizer, r], j) => {
    sizer?.remove();
    const p = r.width, lines = box._ckProbe.childElementCount + 1;
    if (!p) return;
    box._ckW = p; box._ckLh = r.height / lines;   // what it was fitted to (ckRooms)
    if (w) {
      box._ckKey = keys[j];
      box._ckWide = box._ckToks.map((ws, t) => {
        const out = [0];
        for (let i = 0; i < ws.length; i += 2) {
          const g = ws[i] < 0 ? 0 : w.get(ws[i] + "\t "), wd = ws[i + 1].reduce((a, [kind, s]) => a + w.get(kind + "\t" + s), 0), pair = two(ws, i);
          out.push(g, pair ? w.get(pair.join("\t")) - w.get(pair[0] + "\t" + ws[i - 1][0][1]) - g - wd : 0, wd);
        }
        // spare: what it's wider by in one line than its words side by side, and where the page lays it out in fractions
        // of a pixel, a quarter of one, for the page's rounding of each run (in whole pixels a line that fits exactly fits)
        out[0] = Math.max(0, whole[t] - out.reduce((a, b) => a + b, 0)) + (out.every(Number.isInteger) && Number.isInteger(whole[t]) ? 0 : .25);
        return out;
      });
    }
    let n = 1;
    for (const ws of box._ckWide) n = Math.max(n, ckWrap(ws, p));
    if (n !== lines) box._ckProbe.replaceChildren("\u200b", ...Array.from({length: n - 1}, () => [el("br"), "\u200b"]).flat());   // (each line the box's)
  });
}
/* fitted again as the window's resized (before it's laid out, so it's drawn so), as a font loads, and in the next frame
   as a probe's width or line changes otherwise (a scroll bar shown, the text's size), all of them once: never inside
   the observer's own, which would change the page under the page's other observers (util.js sgFoot), and never for
   its own lines written, which change only its height (each fit was followed by another, a frame later, after every
   draw and resize: on a slow machine, more for the page to do as a tab's first drawn, #900's CI failure) */
const ckReadAll = () => ckReadFit([...document.querySelectorAll(".ck-read")]);
const ckLater = new Set();
let ckLaterRaf = 0;
const ckRooms = new ResizeObserver(es => {
  for (const {target: p, contentRect: r} of es) {
    if (!p.isConnected) { ckRooms.unobserve(p); continue; }   // (one taken out: no longer watched)
    if (r.width && (r.width !== p.parentElement._ckW || Math.abs(r.height / (p.childElementCount + 1) - p.parentElement._ckLh) > .01)) ckLater.add(p.parentElement);
  }
  if (ckLater.size && !ckLaterRaf) ckLaterRaf = requestAnimationFrame(() => { const bs = [...ckLater]; ckLater.clear(); ckLaterRaf = 0; ckReadFit(bs); });
});
addEventListener("resize", ckReadAll);
document.fonts.addEventListener("loadingdone", () => { ckFonts++; ckReadAll(); });
function ckRead(box, says) {
  const cs = getComputedStyle(box), tab = cs.fontVariantNumeric.includes("tabular"), font = ` ${cs.fontSize} ${cs.fontFamily}`, seen = new Map(), kinds = new Map();
  const kind = p => { if (p.nodeType !== 1) return 0; const k = p.tagName + p.style.cssText; if (!kinds.has(k)) kinds.set(k, [kinds.size + 1, p]); return kinds.get(k)[0]; };
  ckPen ??= document.createElement("canvas").getContext("2d");
  for (const ps of says) {
    const parts = present(ps).filter(p => p !== ""), txt = parts.map(p => { const t = String(p.textContent ?? p); return [kind(p), tab ? t.replace(/\d/g, "0") : t]; });
    const key = txt.join("\n");
    if (!seen.has(key)) seen.set(key, [txt.reduce((w, [k, t]) => { ckPen.font = (k ? "bold" : "normal") + font; return w + ckPen.measureText(t).width; }, 0), parts, txt]);
  }
  const all = [...seen.values()], said = el("span"), probe = el("span", {class: "ck-room ck-probe", "aria-hidden": "true", text: "\u200b"}), room = [...all].sort((a, b) => b[0] - a[0]).slice(0, 3);
  box._ckToks = all.map(([, , txt]) => ckWords(txt)); box._ckParts = all.map(([, parts]) => parts); box._ckKinds = [...kinds.values()].map(([, p]) => p); box._ckKey = null;
  box.classList.add("ck-read");
  if (box._ckProbe) ckRooms.unobserve(box._ckProbe);
  box.replaceChildren(said, ...room.map(([, parts]) => el("span", {class: "ck-room", "aria-hidden": "true"}, ...parts)), box._ckProbe = probe);
  ckRooms.observe(probe);
  ckReadFit([box]);
  return ps => said.replaceChildren(...present(ps));
}
/* a time chart's readout for its results pts (ckRead): say(at, last) its words, for at, the results at the time hovered
   or stepped to as ckAt gives them, or for null, at rest, last the latest time's (ckLast: two at that time are both
   the latest, #904: the readout said one). Its room is every time's words and every latest's, built by say as what's
   shown is, each time's results in the order they're said (#894: the room had two at one time in the file's order,
   the hover the flagged one first, and wrapped, what was said could take a line more, so the chart moved still);
   more: other words it can say */
const ckLast = pts => pts.length ? ckAt(pts, pts.at(-1).ts) : [];
const ckReadAt = (box, pts, say, more = []) => { const ts = ckTimes(pts); return ckRead(box, [...ts.map(at => say(null, at)), ...ts.map(at => say(at)), ...more]); };

/* ---- the overview strip (#295): a tab's records across every year in 40 px (more with larger text, #641), under the
   period bar, the period a window on it (charts.js stripDraw). ckStripBins: the stretch, the shortest of a day, a week,
   a month, a quarter or a year that's 3 px wide at least on x, and how many records each holds; items [[time, how
   many]], a to b -> {edges: [each stretch's start, then b], n: [its count], step: [n, unit]} */
function ckStripBins(items, a, b, x) {
  const perDay = Math.abs(x(a + DAY) - x(a)), step = [[1, "d"], [7, "d"], [1, "m"], [3, "m"], [1, "y"]].find(([n, u]) => n * CK_DAYS[u] * perDay >= 3) || [1, "y"];
  const edges = [a, ...ckStepTicks(a, b, ...step).map(k => k.t).filter(t => t > a && t < b), b], n = new Array(edges.length - 1).fill(0);
  for (const [t, k] of items) {
    if (!(t >= a && t < b)) continue;
    let lo = 0, hi = n.length - 1;   // the last stretch starting at or before t
    while (lo < hi) { const m = (lo + hi + 1) >> 1; if (edges[m] <= t) lo = m; else hi = m - 1; }
    n[lo] += k;
  }
  return {edges, n, step};
}
/* its bars, one path: each stretch's count up from the foot B, the most up to T (counts, from zero: no scale of their
   own), any at all 2 px at least; 1 px apart where they're 4 px wide or more */
function ckStripBars(g, {edges, n}, x, T, B) {
  const max = n.reduce((m, v) => Math.max(m, v), 0); let d = "";
  n.forEach((v, i) => {
    if (!v) return;
    const x0 = x(edges[i]), w = x(edges[i + 1]) - x0, gap = w >= 4 ? 1 : 0;
    d += `M${x0.toFixed(1)},${B}h${(w - gap).toFixed(1)}V${(B - Math.max(2, (B - T) * v / max)).toFixed(1)}H${x0.toFixed(1)}Z`;
  });
  ckFoot(g, x(edges[0]), x(edges.at(-1)), B);
  if (d) g.push(el("svg:path", {class: "sb-bar", d}));
}
/* its height, for bars whose foot is at B: a row of dates under them as tall as the text (ckK: 40 px in all for bars
   from 5 to 23, 55 with text at 200%), so the dates stay clear of the bars and inside the window's edges (#641) */
const ckStripH = B => Math.round(B + 2 + 15 * ckK());
/* its dates under the foot B, their baselines 12 px under it and their room growing with the text (ckK, #641): the years
   (each, or every 2, 5 or 10), or for two years or less the months, each at a short tick, as many as fit side by side
   inside W; one too near W's end goes back over its tick, centred on it at most, if it's clear of the one before
   (larger text on a phone, ten years: "2020 2025", not "2020" alone) */
function ckStripDates(g, a, b, x, B, W) {
  const years = b - a > 730 * DAY, k = ckK(), y = B + 12 * k, off = 3 * k;
  for (const [n, u] of CK_STEPS) {
    if (u === "d" || (years && u === "m")) continue;
    const ticks = ckStepTicks(a, b, n, u), say = t => u === "y" ? t.label : ckMonth(t.t);
    if (ticks.some((t, i) => i && x(t.t) - x(ticks[i - 1].t) < off + labelW(say(ticks[i - 1]), "") + 7)) continue;
    let end = -7;   // the last label's right (none yet: the strip's left)
    for (const t of ticks) {
      const tx = x(t.t), lw = labelW(say(t), ""), lx = Math.min(tx + off, W - lw);
      g.push(el("svg:line", {class: "gridline", x1: tx, x2: tx, y1: B, y2: B + 4}));
      if (lx >= end + 7 && lx >= tx - lw / 2) { g.push(el("svg:text", {x: lx, y, text: say(t)})); end = lx + lw; }
    }
    return;
  }
}
/* the window: the period, xa to xb, the strip's height h, with a grip at each edge over the bars (their foot B, so never
   on the dates); sb-a and sb-b name the grips, for each one's range input to show its focus on it (app.css) */
function ckStripWindow(g, xa, xb, h, B) {
  g.push(el("svg:rect", {class: "sb-win", x: xa, y: 1, width: Math.max(1, xb - xa), height: h - 2, rx: 3}));
  for (const [px, k] of [[xa, "sb-a"], [xb, "sb-b"]]) g.push(el("svg:rect", {class: "sb-grip " + k, x: px - 3.5, y: B - 16, width: 7, height: 16, rx: 2}));
}

/* ---- names over marks (#294, the event track): items [{x, text, cls, rank}] in time order, each mark named in one of
   `rows` rows over it, a leader line from its name down to it. Marks within 6 px share a name: the first by rank, then
   time, with "+N" for how many more; a name with no room takes its marks to its nearest neighbour's, until every name
   fits. A name stays inside L..R, 6 px off the next in its row, and no leader runs through a name. Laid out twice, from
   the right (an earlier name over a later one) and by rank first, keeping the one with more names, then with more of
   the first rank. -> [{items, head, x (its leader's), text, cls, row (0 the lowest), a, b}] */
function ckFlagRows(items, L, R, rows = 3) {
  ckK();
  const start = [], top = Math.min(...items.map(i => i.rank ?? 9));
  for (const it of items) { const g = start.at(-1); if (g && it.x - g[0].x < 6) g.push(it); else start.push([it]); }
  const score = p => p.length * 1e3 + p.filter(f => (f.head.rank ?? 9) === top).length;
  return [false, true].map(byRank => ckFlagLay(start, L, R, rows, byRank)).reduce((a, b) => score(b) > score(a) ? b : a);
}
function ckFlagLay(groups, L, R, rows, byRank) {
  const box = (a, w) => Math.max(L, Math.min(R - w, a));
  for (;;) {
    const placed = [], failed = new Set(), heads = groups.map(g => g.reduce((h, it) => (it.rank ?? 9) < (h.rank ?? 9) ? it : h));
    for (const i of groups.map((_, i) => i).sort((i, j) => (byRank ? (heads[i].rank ?? 9) - (heads[j].rank ?? 9) : 0) || j - i)) {
      const g = groups[i], head = heads[i], x = head.x;
      const more = g.length > 1 ? ` +${g.length - 1}` : "", text = ckFit(head.text, head.cls, Math.min(220, R - L) - 4 - (more ? labelW(more, head.cls) : 0)) + more;
      const w = labelW(text, head.cls) + 4;
      let at = null;
      // after its leader in the lowest row with room, else before it (one before it in a lower row would stop any
      // name to its left going over it)
      for (const a of new Set([box(x, w), box(x - w, w)])) for (let row = 0; row < rows && !at; row++) {
        const b = a + w;
        if (!placed.some(p => p.row === row ? a < p.b + 6 && b > p.a - 6 : p.row < row ? x >= p.a - 3 && x <= p.b + 3 : p.x >= a - 3 && p.x <= b + 3))
          at = {items: g, head, x, text, cls: head.cls, row, a, b};
      }
      if (at) placed.push(at); else failed.add(i);
    }
    if (!failed.size || groups.length < 2) return placed;
    // each name with no room: its marks to the nearer group beside it, a gap in time between them being in px
    const next = [];
    let carry = [];
    groups.forEach((g, i) => {
      g = [...carry, ...g]; carry = [];
      if (!failed.has(i)) return next.push(g);
      const nx = groups[i + 1], left = next.length ? g[0].x - next.at(-1).at(-1).x : Infinity, right = nx ? nx[0].x - g.at(-1).x : Infinity;
      if (next.length && left <= right) next.at(-1).push(...g); else if (nx) carry = g; else next.push(g);
    });
    groups = next;
  }
}
/* ---- milestone names over a chart (#298, Body and activity's first): items [{x, text, title}] in time order, named
   by ckFlagRows in rows under top, each a leader down to a flag on the plot's top edge; a name's <title> has the full
   names of its marks ("+N" too). -> the plot's top under them (top with none); the plot draws a faint line at each */
function ckFlagNames(g, items, L, R, top) {
  if (!items.length) return top;
  const flags = ckFlagRows(items.map(i => ({...i, cls: "mslabel", rank: 0})), L, R), rows = Math.max(...flags.map(f => f.row)) + 1, k = ckK(), T = top + rows * 15 * k + 10;   // rows as tall as the text (#622)
  for (const f of flags) {
    const ly = top + (rows - f.row) * 15 * k, more = f.items.length > 1 ? f.text.match(/ \+\d+$/)[0] : "";
    g.push(el("svg:line", {class: "msflag", x1: f.x, x2: f.x, y1: ly + 3, y2: T}), el("svg:text", {class: "mslabel", x: f.a + 2, y: ly}, more ? f.text.slice(0, -more.length) : f.text,
      more ? el("svg:tspan", {class: "ev-more", text: more}) : null, el("svg:title", {text: f.items.map(i => i.title).join("\n")})));
  }
  for (const i of items) g.push(el("svg:path", {class: "msflag-head", d: `M${i.x},${T - 7}L${i.x + 7},${T - 4.5}L${i.x},${T - 2}Z`}));
  return T;
}

/* ---- labs compared (#297, compare.js): each result placed within its own printed range, 0 at its low limit and 100
   at its high, so labs of any units share one axis (never a second y-scale). ckPos(p): that place, or null for a
   result whose report gives no low and high (a one-sided "≤ 5", or none) */
const ckPos = p => p[2] != null && p[3] != null && p[3] > p[2] ? (p[1] - p[2]) / (p[3] - p[2]) * 100 : null;
/* the axis's ends for places ps: the range, 0 to 100, always in it; a far value widens it to -100 or 300 at most (one
   past that is drawn at the edge, its mark saying which way), with room at either end */
function ckPosSpan(ps) {
  const a = Math.max(-100, Math.min(0, ...ps)), b = Math.min(300, Math.max(100, ...ps)), pad = (b - a) * .06;
  return [a - pad, b + pad];
}
/* its round numbers: steps of 25, 50 or 100, so 0 and 100 are always two of them */
function ckPosTicks(lo, hi) {
  const s = [25, 50, 100].find(s => (hi - lo) / s <= 6) || 100, out = [];
  for (let v = Math.ceil(lo / s) * s; v <= hi; v += s) out.push(v);
  return out;
}
/* the labs compared, in order: each a line in its own ink and dash and a mark of its own shape, so none is told by its
   colour alone: the Labs blue, solid, dots; the secondary ink, dashed, squares; a lighter grey, dotted, rings (each 3:1
   or more on the card, light and dark) */
const CK_SERIES = [{color: "var(--c-lab)", dash: null, shape: "circle"}, {color: INK2, dash: "7 4", shape: "square"},
  {color: "var(--field-line)", dash: "1.5 3.5", shape: "ring"}];
/* series i's mark at x, y, in its shape and ink, a ring of the card round it where it's filled */
const ckSeriesMark = (i, x, y, r) => { const k = CK_SERIES[i], ring = k.shape === "ring";
  return shapeMark(k.shape, x, y, r, {fill: ring ? "var(--surface)" : k.color, stroke: ring ? k.color : "var(--surface)", "stroke-width": ring ? 2 : 1.5}); };
/* its key, as it's drawn: a short stretch of its line with its mark on it; hidden from screen readers (its name says it) */
const ckSeriesKey = i => el("svg:svg", {class: "cmp-k", viewBox: "0 0 28 14", width: 28, height: 14, "aria-hidden": "true"},
  el("svg:line", {x1: 1, x2: 27, y1: 7, y2: 7, stroke: CK_SERIES[i].color, "stroke-dasharray": CK_SERIES[i].dash, "stroke-width": 2}), ckSeriesMark(i, 14, 7, 3.5));
/* a dumbbell: a row's two places at y on the value scale x (over lo..hi), before (a ring) and after (a dot) in series
   i's ink, joined; either may be null (no results on that side). A place past the scale is drawn at its end */
function ckDumbbell(g, x, lo, hi, y, i, a, b) {
  const c = CK_SERIES[i].color, [x0, x1] = [a, b].map(v => v == null ? null : x(Math.max(lo, Math.min(hi, v))));
  if (x0 != null && x1 != null) g.push(el("svg:line", {class: "db-bar", x1: x0, x2: x1, y1: y, y2: y, stroke: c}));
  if (x0 != null) g.push(shapeMark("ring", x0, y, 5.5, {class: "db-a", stroke: c}));
  if (x1 != null) g.push(shapeMark("circle", x1, y, 5.5, {class: "db-b", fill: c}));
}

/* ---- a day's hours (#303, the glucose card's 24-hour profile): x from 0 (midnight) to 24 (the next). ckHour(h): an
   hour as the app writes a time, en-US, "12 AM" to "11 PM", its AM or PM kept with it (a no-break space, as fmtTime's) */
const ckHour = h => `${h % 12 || 12} ${h % 24 < 12 ? "AM" : "PM"}`;
/* the hours' ticks for ckAxis, one row: every 3, 6 or 12 hours, the most whose labels fit side by side inside W (as
   ckTimeTicks tries its steps; larger text, fewer) */
function ckHourTicks(x, W) {
  ckK();
  let ticks = [];
  for (const n of [3, 6, 12]) {
    ticks = [];
    for (let h = 0; h <= 24; h += n) { const label = ckHour(h), w = labelW(label, ""); ticks.push({t: h, label, x: Math.max(w / 2, Math.min(W - w / 2, x(h)))}); }
    if (ticks.every((k, i) => !i || k.x - ticks[i - 1].x >= (labelW(k.label, "") + labelW(ticks[i - 1].label, "")) / 2 + 12)) break;
  }
  return {rows: 1, ticks};
}
/* a value's spread at each point, in color: runs of rows [x, p5, p25, p50, p75, p95] (a run's points joined, nothing
   drawn between runs): the 5th to 95th percentile a wash with hairline edges, the 25th to 75th a deeper wash over it,
   the median a 2 px line, so the bands read in one hue, lighter the wider (validated light and dark: each step 0.06
   of lightness at least from the next; the wash's edges 2:1 on the card, the median 3.5:1). A run of one point is a
   short flat stretch, lone either side of it */
function ckSpread(g, runs, x, y, color, lone = .25) {
  const at = (r, i) => r.map(p => `${x(p[0]).toFixed(1)},${y(p[i]).toFixed(1)}`), line = (r, i) => "M" + at(r, i).join("L");
  const area = (r, i, j) => line(r, j) + "L" + at(r, i).reverse().join("L") + "Z";
  let out = "", edge = "", mid = "", med = "";
  for (let r of runs) {
    if (r.length === 1) r = [[r[0][0] - lone, ...r[0].slice(1)], [r[0][0] + lone, ...r[0].slice(1)]];
    out += area(r, 1, 5); edge += line(r, 1) + line(r, 5); mid += area(r, 2, 4); med += line(r, 3);
  }
  g.push(el("svg:path", {d: out, fill: color, "fill-opacity": .15}), el("svg:path", {d: edge, fill: "none", stroke: color, "stroke-opacity": .6}),
    el("svg:path", {d: mid, fill: color, "fill-opacity": .35}),
    el("svg:path", {d: med, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round"}));
}
