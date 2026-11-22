/* Sanguity · the chart kit (#291): the parts every time chart is drawn with, so they read as one: Labs (trends.js
   drawChart) and Body and activity (health.js hChart) draw through it. Each part is its own small function, for the
   rest of the kit to plug in beside (decimation, the event track, the overview strip, sparklines):
   - ckScale, niceTicks: the scales, and a value axis's round numbers;
   - ckTimeTicks, ckAxis: the time axis, its ticks at two levels (days over months, months over years);
   - ckGrid: the value axis, a hairline and a label at each round number;
   - ckLegend: a chart's key, each mark keyed as it's drawn (a line for a line);
   - ckCross, ckSync, ckTip: the crosshair, at one time on every time chart of a view, and the hover's readout pinned to
     the top of the plot beside it.
   CHART_LAYERS (trends.js) and TL_LANES (timeline.js) stay the plug-ins of Labs' charts and the Timeline.
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
    if (!labelText || !labelText.isConnected) labelText = document.body.appendChild(el("svg:svg", {class: "vh", "aria-hidden": "true"}, el("svg:text", {}))).firstChild;
    labelText.setAttribute("class", cls);
    labelText.textContent = text;
    labelSizes.set(key, labelText.getComputedTextLength());
  }
  return labelSizes.get(key);
}
document.fonts.addEventListener("loadingdone", () => labelSizes.clear());

/* ---- the time axis: ticks at two levels, the finer over the coarser: days over "Sep 2026", months over "2026"; years
   alone in one row. The step is the smallest whose labels fit side by side, 44 px apart at least: more ticks on a wider
   or zoomed chart, as many on every chart of the same width and period. */
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
   time scale, W the chart's width (a label kept inside it) */
function ckTimeTicks(a, b, x, W) {
  const perDay = Math.abs(x(a + DAY) - x(a));
  let ticks = [], u = "y";
  for (const [n, unit] of CK_STEPS) {
    if (n * CK_DAYS[unit] * perDay < 44) continue;
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
const ckAxisH = rows => rows === 2 ? 36 : 22;
/* the time axis: its line along the plot's foot (B, from L to R), then its labels, the finer row's baseline at y1 and
   the coarser's under it */
function ckAxis(g, tk, L, R, B, y1) {
  g.push(el("svg:line", {x1: L, x2: R, y1: B, y2: B, stroke: "var(--axis)"}));
  for (const k of tk.ticks) {
    g.push(el("svg:text", {x: k.x, y: y1, "text-anchor": "middle", text: k.label}));
    if (k.major) g.push(el("svg:text", {class: "ck-yr", x: k.mx, y: y1 + 14, text: k.major}));
  }
}
/* the value axis: a hairline across the plot at each of vals, labelled left of it with fmt(v) */
function ckGrid(g, y, vals, L, R, fmt) {
  for (const v of vals) g.push(el("svg:line", {class: "gridline", x1: L, x2: R, y1: y(v), y2: y(v)}), el("svg:text", {x: L - 6, y: y(v) + 4, "text-anchor": "end", text: fmt(v)}));
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
   own nearest point to t, or hides it for null or nothing near, and returns that point. ckSync(svg, t) does so on every
   time chart (svg.ck) of svg's view, so they all show one date, and returns svg's own point */
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
