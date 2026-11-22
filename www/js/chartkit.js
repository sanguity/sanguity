/* Sanguity · the chart kit (#291): the parts every time chart is drawn with, so they read as one: Labs (trends.js
   drawChart) and Body and activity (health.js hChart) draw through it. Each part is its own small function, for the
   rest of the kit to plug in beside (the event track, the overview strip, sparklines):
   - ckScale, niceTicks: the scales, and a value axis's round numbers;
   - ckTimeTicks, ckAxis, ckFoot: the time axis, its ticks at two levels (days over months, months over years), and
     the plot's foot alone, for a chart in a stack under one axis;
   - ckGrid, ckUnit: the value axis, a hairline and a label at each round number, and its unit as its title;
   - ckBand, ckBandLabel: the reference band, each result's own range, neutral, and its numbers at the right;
   - ckDecimate: a series with more points than pixels, drawn with what each pixel shows (#292);
   - ckLegend: a chart's key, each mark keyed as it's drawn (a line for a line);
   - ckCross, ckSync, ckTip: the crosshair, at one time on every time chart of a view, and the hover's readout pinned to
     the top of the plot beside it;
   - ckFlagRows: names over marks in up to three rows, with leader lines and "+N" (Labs' event track, event-track.js).
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
   time scale, W the chart's width (a label kept inside it) */
function ckTimeTicks(a, b, x, W) {
  const perDay = Math.abs(x(a + DAY) - x(a));
  let ticks = [], u = "y";
  for (const [n, unit] of CK_STEPS) {
    if (n * CK_DAYS[unit] * perDay < 36) continue;   // too close to try (36 px: on a phone six months of All is about 43, #615); the labels then tested below
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
/* the plot's foot: a line along B, from L to R; ckAxis draws it, and a chart in a stack of them with one axis (#293) */
const ckFoot = (g, L, R, B) => g.push(el("svg:line", {x1: L, x2: R, y1: B, y2: B, stroke: "var(--axis)"}));
/* the time axis: its line along the plot's foot (B, from L to R), then its labels, the finer row's baseline at y1 and
   the coarser's under it */
function ckAxis(g, tk, L, R, B, y1) {
  ckFoot(g, L, R, B);
  for (const k of tk.ticks) {
    g.push(el("svg:text", {x: k.x, y: y1, "text-anchor": "middle", text: k.label}));
    if (k.major) g.push(el("svg:text", {class: "ck-yr", x: k.mx, y: y1 + 14, text: k.major}));
  }
}
/* the value axis: a hairline across the plot at each of vals, labelled left of it with fmt(v) */
function ckGrid(g, y, vals, L, R, fmt) {
  for (const v of vals) g.push(el("svg:line", {class: "gridline", x1: L, x2: R, y1: y(v), y2: y(v)}), el("svg:text", {x: L - 6, y: y(v) + 4, "text-anchor": "end", text: fmt(v)}));
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
   the first clear of every point (pts: [px, py]) and inside the plot, T to B */
function ckBandLabel(g, words, st, y, R, T, B, pts) {
  const w = labelW(words, "ck-ref"), x1 = R - 4, x0 = x1 - w;
  const hi = st.hi != null ? Math.max(T, y(st.hi)) : T, lo = st.lo != null ? Math.min(B, y(st.lo)) : B;
  const inside = lo - hi >= 16 ? (st.hi != null ? [hi + 12, lo - 4] : [lo - 4, hi + 12]) : [];   // by its printed edge first
  const at = [...inside, hi - 4, lo + 12].filter(yb => yb - 10 >= T && yb + 2 <= B);   // baselines, 10 px of text over each
  const clear = yb => !pts.some(([px, py]) => px > x0 - 5 && px < x1 + 5 && py > yb - 15 && py < yb + 6);
  const yb = at.find(clear) ?? at[0];
  if (yb != null) g.push(el("svg:text", {class: "ck-ref", x: x1, y: yb, "text-anchor": "end", text: words}));
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

/* ---- names over marks (#294, the event track): items [{x, text, cls, rank}] in time order, each mark named in one of
   `rows` rows over it, a leader line from its name down to it. Marks within 6 px share a name: the first by rank, then
   time, with "+N" for how many more; a name with no room takes its marks to its nearest neighbour's, until every name
   fits. A name stays inside L..R, 6 px off the next in its row, and no leader runs through a name. Laid out twice, from
   the right (an earlier name over a later one) and by rank first, keeping the one with more names, then with more of
   the first rank. -> [{items, head, x (its leader's), text, cls, row (0 the lowest), a, b}] */
function ckFlagRows(items, L, R, rows = 3) {
  const start = [], top = Math.min(...items.map(i => i.rank ?? 9));
  for (const it of items) { const g = start.at(-1); if (g && it.x - g[0].x < 6) g.push(it); else start.push([it]); }
  const score = p => p.length * 1e3 + p.filter(f => (f.head.rank ?? 9) === top).length;
  return [false, true].map(byRank => ckFlagLay(start, L, R, rows, byRank)).reduce((a, b) => score(b) > score(a) ? b : a);
}
function ckFlagLay(groups, L, R, rows, byRank) {
  const fit = (t, cls, room) => { if (labelW(t, cls) <= room) return t; for (let n = t.length - 1; n > 1; n--) { const s = t.slice(0, n).trimEnd() + "…"; if (labelW(s, cls) <= room) return s; } return "…"; };
  const box = (a, w) => Math.max(L, Math.min(R - w, a));
  for (;;) {
    const placed = [], failed = new Set(), heads = groups.map(g => g.reduce((h, it) => (it.rank ?? 9) < (h.rank ?? 9) ? it : h));
    for (const i of groups.map((_, i) => i).sort((i, j) => (byRank ? (heads[i].rank ?? 9) - (heads[j].rank ?? 9) : 0) || j - i)) {
      const g = groups[i], head = heads[i], x = head.x;
      const more = g.length > 1 ? ` +${g.length - 1}` : "", text = fit(head.text, head.cls, Math.min(220, R - L) - 4 - (more ? labelW(more, head.cls) : 0)) + more;
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
