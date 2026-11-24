/* Sanguity · Labs tab, as a heatmap (#172; the charts are trends.js): every lab with ranges, a column per draw.
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
/* ---------------------------------------------------------------- heatmap */
/* a result's level against its own range, its cell's colour: 0 inside it, −1 to −4 below it and 1 to 4 above by how far;
   null with neither a range nor a flag that says which way. Whether and which way by its flag, as its ▲/▼, read
   first (ranges.js applyRangeOverrides, #752: "<15" against a low of 15 is low, though it's on the edge, at least one
   step; "<120" against a high of 110 is inside it, as it may be; #782: a report's Low with no range printed is one step
   below, as Labs marks it); how far by its value */
function hmLevel(p) {
  const v = p[1], lo = p[2], hi = p[3], fl = String(p[4] || "").trim(), f = /^h/i.test(fl) ? 1 : /^l/i.test(fl) ? -1 : 0;
  if (lo == null && hi == null) return f || null;
  if (!fl) return 0;
  const span = lo != null && hi != null && hi > lo ? hi - lo : Math.abs(hi ?? lo) || 1;
  let z = 0;
  if (lo != null && v < lo) z = -(lo - v) / span; else if (hi != null && v > hi) z = (v - hi) / span;
  if (f && Math.sign(z) !== f) z = f * 1e-9;
  if (z === 0) return 0;
  const a = Math.abs(z), step = a <= .1 ? 1 : a <= .3 ? 2 : a <= .6 ? 3 : 4;
  return Math.sign(z) * step;
}
/* a level's colour: in range the neutral grey, outside it 40% to 100% of the way to blue (below) or red (above), the
   nearest steps far enough from the grey to tell apart (#300: it's darker than it was, the card's white beside it) */
const hmFill = lv => lv === 0 ? "var(--div-mid)" : `color-mix(in oklab, var(--div-mid), var(${lv < 0 ? "--div-lo" : "--div-hi"}) ${20 + Math.abs(lv) * 20}%)`;
/* a result's mark, its flag's as Labs marks it: ▲ above, ▼ below (its level), ◆ for a flag its report gives that
   says neither, on a value inside its range (an "A": the range's grey, its flag said and counted); "" for none */
const hmMark = p => { const lv = hmLevel(p); return lv > 0 ? "H" : lv < 0 ? "L" : p[4] ? "A" : ""; };
/* a row's results in the period by day, each of them in time order (#471): a day can hold two draws' results, an
   outside lab's typed in on a clinic draw's day, and the last no longer hides the others. -> Map(day -> [point]) */
function hmDays(s) {
  const m = new Map();
  for (const p of s.pts) if (p.ts >= state.from && p.ts <= state.to) { const k = isoDay(p.ts); (m.get(k) || m.set(k, []).get(k)).push(p); }
  return m;
}
/* the draw a result is in (#471): the one with it among its own results (store.js labDayWords' e.res, util.js
   labDraws), the nearest in time where it's several draws'; null for a visit's Vitals, which are no draw's */
const hmDraw = (p, k) => EV.filter(e => e.k === "lab" && isoDay(e.ts) === k && e.res?.some(([, q]) => q === p))
  .sort((a, b) => Math.abs(a.ts - p.ts) - Math.abs(b.ts - p.ts))[0] || null;
/* another of a day's results, in the tip of one (#471): "Also at 8:31 AM: 0.89 mg/dL · In range" */
const hmAlso = (s, p) => `Also at ${fmtTime(p.ts)}: ${p[5] || fmtNum(p[1])} ${s.unit}${statusOf(p) ? " · " + statusOf(p).text : ""}`;
/* the labs the heatmap has a row for: twenty results or more, with ranges */
const hmLabs = () => SERIES.filter(s => !s.watch && s.n >= 20 && s.pts.some(p => p[2] != null || p[3] != null));
const hmCut = new Map();   // a panel's name as it fits the names' column, by its width (below)
document.fonts.addEventListener("loadingdone", () => hmCut.clear());
function renderHeatmap(live) {   // live: a frame of a drag (charts.js), at the width it had (#292)
  const asTable = state.tables.has("#hm");   // Grid | Table (#60)
  const hd = $("#hmHd"); hd.querySelector(".view-seg")?.remove(); hd.append(viewSeg("#hm", "the heatmap", renderHeatmap, "Grid"));
  $("#hmLegend").hidden = $("#hmWrap").hidden = asTable; $("#hmTable").hidden = !asTable;
  const all = hmLabs(), shown = all.filter(labMatch);   // the Labs filter (#192, trends.js)
  labCount("#hmQn", shown.length, all.length);
  // by panel as in the list (#279, labGroups in util.js): the most measured first, Other last; a panel with one row on
  // the heatmap in Other too (#300), whatever the filter. grp: a series' panel, by the series, as a lab kept in two
  // units is two rows of one name (#438)
  const grp = new Map(), rows = [];
  for (const {panel, labs} of labGroups(byFrequency(shown), all)) for (const s of labs) { grp.set(s, panel); rows.push(s); }
  // columns: draw days in range, [day, its time]; colOf: a day's column
  const days = new Map();
  for (const s of rows) for (const p of s.pts) if (p.ts >= state.from && p.ts <= state.to) { const k = isoDay(p.ts); if (!days.has(k)) days.set(k, dayTs(k)); }
  const cols = [...days.entries()].sort((a, b) => a[1] - b[1]), colOf = new Map(cols.map(([d], i) => [d, i]));
  $("#hmBack")?.remove();
  if (rows.length && !cols.length) $("#hmWrap").before(Object.assign(wayBack("lab draws"), {id: "hmBack"}));   // #382
  if (asTable) { hmTable(rows, cols, grp); return; }
  const svg = $("#hm"), axis = $("#hmAxis");
  // laid out in its box's own width (#476: not a floor of 320 px, which a phone's 270 px box cut the right of), 320 while
  // it isn't shown
  const avail = (live && svg._avail) || (svg._avail = svg.parentElement.clientWidth || 320);
  // its fixed sizes grow with the labels (chartkit.js ckK, #633: at 200% text the names and the axis fell outside):
  // the names' column, kept to under half the box, the rows and the lines over them
  const k = ckK(), LW = Math.min(Math.round((avail < 600 ? 120 : 190) * k), Math.floor(avail * .45)), top = 34 * k, rowH = 14 * k, gap = 2, GH = 16 * k;
  if (!rows.length || !cols.length) {   // a plot kept to drag back to where the draws are, and double-click (#382)
    svg.replaceChildren(el("svg:rect", {width: avail, height: 96, fill: "transparent"}),
      el("svg:text", {x: avail / 2, y: 52, "text-anchor": "middle", text: !rows.length && state.labQ ? "No lab names match the filter." : "No lab draws in this date range."}));
    svg.setAttribute("viewBox", `0 0 ${avail} 96`); svg.setAttribute("width", avail); svg.setAttribute("height", 96);
    svg._geom = {L: LW, R: avail - 8, w: avail}; axis.replaceChildren(); axis.setAttribute("height", 0); return;
  }
  const cellPts = rows.map(hmDays);   // each row's results by day (#471)
  // each row's results outside their own range in the period, counted at its end (#300), as many as it has ▲, ▼ and ◆
  const outN = cellPts.map(m => { let n = 0; for (const ps of m.values()) for (const p of ps) if (hmMark(p)) n++; return n; });
  const RW = Math.ceil(labelW(String(Math.max(...outN)), "")) + 10;   // their column, right of the grid
  // columns always fit the width (fractional widths are fine, under 1 px too: #476, ten years on a phone); zoom in to
  // get wider cells. On the kit's scale (chartkit.js ckScale): a column's index to its left edge
  const cw = Math.min(22, (avail - LW - RW) / cols.length), colX = ckScale(0, 1, LW, LW + cw), R = colX(cols.length);
  // the colours, and the marks that say the same without them (#60): ▲ above the range, ▼ below (◆ flagged, named where
  // there's one), on cells wide enough (3 px), and where some aren't, a word on it: a cell under 3 px, or a part of one
  // split between a day's draws (#476)
  const thin = cw < 3 || cellPts.some(m => [...m.values()].some(ps => ps.length > 1 && cw / ps.length < 3 && ps.some(hmMark)));
  const dia = cellPts.some(m => [...m.values()].some(ps => ps.some(p => hmMark(p) === "A")));
  $("#hmLegend").replaceChildren(...[-4, -3, -2, -1, 0, 1, 2, 3, 4].map(lv => el("i", {style: {background: hmFill(lv)}})),
    el("span", {text: "← far below · below · in range · above · far above →"}), el("span", {style: {color: "var(--muted)"}, text: "(distance outside the range, as a share of the range's width)"}),
    el("span", {class: "hm-key"}, el("b", {"aria-hidden": "true", text: "▲"}), " above the range, ", el("b", {"aria-hidden": "true", text: "▼"}), " below",
      dia ? [", ", el("b", {"aria-hidden": "true", text: "◆"}), " flagged by its report"] : null, thin ? el("span", {style: {color: "var(--muted)"}, text: " (zoom in to see them on each cell, or show the table)"}) : null));
  const groups = []; let gy = top;
  const yOf = new Map();
  let lastPanel = null;
  for (const s of rows) { const p = grp.get(s); if (p !== lastPanel) { groups.push([p, gy]); gy += GH; lastPanel = p; } yOf.set(s, gy); gy += rowH; }
  const W = Math.max(R + RW, LW + 8 + labelW("Outside", "")), H = gy + 4;   // (room for the counts' heading over a narrow grid)
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("width", W); svg.setAttribute("height", H);
  svg._geom = {L: LW, R, w: W};
  const g = [];
  // hospital stay strip + procedure markers (the procedures by day once, not the records once a column: #292)
  const procDays = new Map();
  for (const e of EV) if (e.k === "procedure" && e.ts >= state.from - DAY && e.ts <= state.to + DAY) { const d = isoDay(e.ts); (procDays.get(d) || procDays.set(d, []).get(d)).push(e); }
  cols.forEach(([d, ts], c) => {
    const cx = colX(c);
    if (STAYS.some(st => ts >= st.ts - DAY / 2 && ts <= st.te + DAY / 2)) g.push(el("svg:rect", {x: cx, y: 8 * k, width: cw, height: 6 * k, fill: "var(--muted)", "fill-opacity": .5}));
    const procs = procDays.get(d) || [];
    if (procs.length) g.push(shapeMark(KIND_SHAPE.procedure, cx + cw / 2, 24 * k, 3.4 * k, {class: "hm-proc", "data-proc": procs.map(e => e.title).join("; "), "data-day": ts}));   // its kind's shape (#301)
  });
  g.push(el("svg:text", {x: LW - 8, y: 14 * k, "text-anchor": "end", text: "Hospital"}));
  g.push(el("svg:text", {x: LW - 8, y: 28 * k, "text-anchor": "end", text: "Procedure"}));
  for (const [p, py] of groups) g.push(el("svg:text", {class: "grp", x: 0, y: py + 11 * k, text: p}));
  // the counts' heading, on the first panel's line, right of its name
  g.push(el("svg:text", {x: W - 4, y: top + 11 * k, "text-anchor": "end", text: "Outside"}, el("svg:title", {text: "Results outside their reference range in these dates"})));
  const pix = [];   // each row's cells by pixel, where columns are under one (below)
  rows.forEach((s, i) => {
    // a lab kept in two units says which row is which, its unit kept whole (#438)
    const ry = yOf.get(s), unit = s.unit && rows.some(o => o !== s && o.name === s.name) ? ` (${s.unit})` : "";
    const nm = nice(s.name), edit = canRename();   // an editor renames a row by its name (#38, rename.js)
    // "What is this test?" (#163, test-pages.js): a ? in a circle beside the name, clicked (on the circle under the icon,
    // which takes no clicks itself, #257); by keyboard, the link in the table or a chart
    const tp = testPage(s.name), nx = LW - (tp ? 24 * k : 8);
    if (tp) g.push(el("svg:g", {class: "hm-tq"}, el("svg:title", {text: testPageName(s.name, tp)}),
      el("svg:circle", {cx: LW - 14 * k, cy: ry + rowH / 2, r: 7 * k, "data-tp": tp.url}),
      el("svg:use", {class: "icon", href: `${ICON_SPRITE}#i-circle-question-mark`, x: LW - 21 * k, y: ry + rowH / 2 - 7 * k, width: 14 * k, height: 14 * k})));
    g.push(el("svg:text", {class: "row" + (edit ? " renamable" : ""), x: nx, y: ry + rowH - 4 * k, "text-anchor": "end",
      ...(edit ? {"data-rename": "lab", "data-key": s.name} : {})},   // clicked; by keyboard, the ✎ in the heatmap's table or a chart (#202: the grid is one picture)
      hmFit(nm, unit, nx - 2),   // cut to its room as measured, its unit whole
      el("svg:title", {text: renamedTitle("lab", s.name) + unit + (edit ? " · click to rename" : "") + rowRanges(s)})));
    g.push(el("svg:text", {x: W - 4, y: ry + rowH - 4 * k, "text-anchor": "end", text: outN[i].toLocaleString("en-US")}));
    // a day's results side by side in its cell, each its own colour and mark (#471)
    const cells = [...cellPts[i]].flatMap(([d, ps]) => ps.map((p, j) => ({k: d, ps, p, j, lv: hmLevel(p), x: colX(colOf.get(d)) + j * cw / ps.length})));
    const fill = lv => lv == null ? "var(--grid)" : hmFill(lv);
    if (cw < 1) {
      // columns under a pixel wide (ten years on a phone): a pixel's cell each, the result furthest outside its range in
      // it (chartkit.js ckDecimate, #292), drawn as one path per colour, a run of pixels of one colour one rectangle
      // (#300: ten thousand one-pixel cells were most of a drag's frame); the pointer's x finds its cell (hmAt)
      const runs = new Map(), at = pix[i] = new Map();
      let last = null;
      for (const c of ckDecimate(cells, c => c.x - LW, 1, {lo: null, hi: c => c.lv == null ? -1 : Math.abs(c.lv)})) {
        const x = Math.floor(c.x), f = fill(c.lv);
        at.set(x, c);
        if (last && last.f === f && last.b === x) { last.b = x + 1; continue; }
        if (last) runs.set(last.f, (runs.get(last.f) || "") + `M${last.a},${ry + 1}H${last.b}v${rowH - gap}H${last.a}z`);
        last = {f, a: x, b: x + 1};
      }
      if (last) runs.set(last.f, (runs.get(last.f) || "") + `M${last.a},${ry + 1}H${Math.min(last.b, R)}v${rowH - gap}H${last.a}z`);
      for (const [f, d] of runs) g.push(el("svg:path", {class: "cell-run", d, fill: f, "data-run": i}));
      return;
    }
    for (const {k: d, ps, p, j, lv, x} of cells) {
      const w = cw / ps.length;
      const r = el("svg:rect", {class: "cell", x: x + (w > 6 ? .5 : 0), y: ry + 1, width: w - (w > 6 ? 1 : 0), height: rowH - gap, rx: w > 8 ? 2 : null, style: `fill:${fill(lv)}`});
      r.dataset.s = s.name; r.dataset.i = i; r.dataset.k = d; r.dataset.j = j;
      g.push(r);
      const f = hmMark(p);
      if (f && w >= 3) g.push(abnMark(x + w / 2, ry + 1 + (rowH - gap) / 2, f, Math.min(3.6 * k, w / 2 - .8), "hm-mark"));   // #60
    }
  });
  // the time axis, the kit's (chartkit.js ckAxis), in a strip of its own that sticks to the bottom of the window until
  // the heatmap's end scrolls into view; its ticks at the columns (hmTicks). A line at each tick goes under the cells
  // (#479): over them they took the taps, and on a phone's narrow columns they covered a draw's out-of-range colour
  const tk = hmTicks(cols.map(c => c[1]), colX, cw, W), AH = ckAxisH(tk.rows), ax = [];
  const lines = tk.ticks.map(t => el("svg:line", {x1: t.lx, x2: t.lx, y1: top - 2, y2: H, stroke: "var(--grid)", "pointer-events": "none"}));
  ckAxis(ax, tk, LW, R, .5, 15);
  ax.push(el("svg:text", {x: LW - 8, y: .5 + 14.5 * k, "text-anchor": "end", text: "Draw date"}));
  // the strip as wide as its year or month where the grid is narrower (one draw day, #485), not cut there
  const AW = Math.ceil(Math.max(W, ...tk.ticks.map(t => t.major ? t.mx + labelW(t.major, "ck-yr") + 2 : 0)));
  axis.setAttribute("viewBox", `0 0 ${AW} ${AH}`); axis.setAttribute("width", AW); axis.setAttribute("height", AH);
  svg.replaceChildren(...lines, ...g); axis.replaceChildren(...ax);
  // a panel's name kept inside the label column (#441): cut short where it would run into the grid, whole in its tip;
  // measured once for the column's width, not again in each frame of a drag, which laid the whole grid out (#292)
  for (const t of svg.querySelectorAll("text.grp")) {
    const full = t.textContent, key = full + "\n" + LW;
    if (hmCut.has(key)) t.textContent = hmCut.get(key);
    else { for (let n = full.length; n > 4 && t.getComputedTextLength() > LW - 8; n--) t.textContent = full.slice(0, n - 1).trimEnd() + "…"; hmCut.set(key, t.textContent); }
    if (t.textContent !== full) t.append(el("svg:title", {text: full}));
  }
  // the cell under the pointer: a cell's own, or where columns are under a pixel, its row's at the pointer's x
  const hmAt = ev => {
    const d = ev.target.dataset || {};
    if (d.s) return {i: +d.i, k: d.k, j: +d.j || 0};
    if (d.run == null) return null;
    const b = svg.getBoundingClientRect(), x = Math.floor((ev.clientX - b.left) * W / b.width), m = pix[+d.run], c = m.get(x) || m.get(x - 1) || m.get(x + 1);
    return c && {i: +d.run, k: c.k, j: c.j};
  };
  svg.onpointermove = ev => {
    const d = ev.target.dataset || {}, at = hmAt(ev);
    if (d.proc) { showTip([el("div", {class: "v", text: d.proc}), el("div", {class: "s", text: fmtD(+d.day)})], ev); return; }
    if (!at) { hideTip(); return; }
    const s = rows[at.i], ps = cellPts[at.i].get(at.k) || [], p = ps[at.j]; if (!p) return;
    const st = statusOf(p);
    showTip([el("div", {class: "v", text: `${p[5] || fmtNum(p[1])} ${s.unit}`}), el("div", {class: "s", text: nice(s.name) + (nice(s.name) !== s.name ? ` (${s.name})` : "")}), el("div", {class: "s", text: [fmtDT(p.ts), refText(p)].filter(Boolean).join(" · ")}),
      ucOrig(s, p) ? el("div", {class: "s", text: "Reported as " + ucOrig(s, p)}) : null,   // #41
      st ? el("div", {class: "s", style: {color: st.cls === "ok" ? "var(--good-text)" : "var(--crit)"}}, st.icon ? [icon(st.icon), " "] : null, st.text) : null,
      pointSources(s, p) ? el("div", {class: "s", text: "From " + pointSources(s, p)}) : null,   // with any linked as the same (#96)
      ...ps.filter(q => q !== p).map(q => el("div", {class: "s", text: hmAlso(s, q)}))], ev);   // the day's others (#471)
  };
  svg.onpointerleave = hideTip;
  svg.onclick = ev => {
    const d = ev.target.dataset || {}, at = hmAt(ev);
    if (d.rename) { openRename(ev.target, "lab", d.key); return; }
    if (d.tp) { window.open(d.tp, "_blank", "noopener,noreferrer"); return; }   // the page's address alone (#163)
    const day = at?.k || (d.day && isoDay(+d.day)); if (!day) return;
    const p = at && cellPts[at.i]?.get(day)?.[at.j];   // the draw that holds the result (#471), not the day's first
    const e = (p && hmDraw(p, day)) || EV.find(e => e.k === "lab" && isoDay(e.ts) === day) || EV.find(e => e.k === "procedure" && isoDay(e.ts) === day); if (e) openEvent(e);
  };
}
/* a row's name as it fits w px (measured, chartkit.js labelW), cut with "…", its unit (a lab kept in two) kept whole;
   kept by its room, for a drag's frames */
function hmFit(nm, unit, w) {
  const key = nm + unit + "\n" + w;
  if (!hmCut.has(key)) {
    let t = nm + unit;
    for (let n = nm.length; n > 4 && labelW(t, "") > w; n--) t = nm.slice(0, n - 1).trimEnd() + "…" + unit;
    hmCut.set(key, t);
  }
  return hmCut.get(key);
}
/* the heatmap's time axis (#300): its columns are draw days side by side, not evenly spaced time. The kit's ticks
   (chartkit.js ckTimeTicks) for the columns' dates as if they were spread evenly across them; then each tick goes to
   the first column on or after it (its line at the column's edge; its label at a day's own column's middle), a tick
   with no column before the next one's dropped, the first column named too, and from the right (the latest always
   named, #481) one too close to the label after it. Its coarser row named again at the first tick of each month or year left. ts: the columns'
   times in order, colX(i) a column's left edge, cw their width, W the axis's. -> as ckTimeTicks, each tick with lx */
function hmTicks(ts, colX, cw, W) {
  const n = ts.length, a = ts[0], b = ts[n - 1] + DAY, L = colX(0), tk = ckTimeTicks(a, b, ckScale(a, b, L, colX(n)), W);
  const at = [];
  for (let j = 0, i = 0; i < tk.ticks.length; i++) {
    const t = tk.ticks[i];
    while (j < n && ts[j] < t.t) j++;
    if (j === n) break;
    if (at.at(-1)?.j === j) at.pop();
    at.push({...t, j});
  }
  if (at[0]?.j !== 0) {   // the first column named too, in the step's units (a month's, a day's or a year's)
    if (!tk.ticks.length) tk.rows = 2;   // (no tick in the period: a month over its year)
    const d = zdate(a), days = tk.rows === 2 && /^\d+$/.test(tk.ticks[0]?.label), y = d.getFullYear();
    at.unshift({t: a, j: 0, label: tk.rows === 1 ? String(y) : days ? String(d.getDate()) : d.toLocaleDateString(undefined, {month: "short"}), unit: days ? y * 12 + d.getMonth() : y});
  }
  const lw = t => labelW(t.label, ""), keep = [];
  for (const t of at.reverse()) {
    t.lx = colX(t.j); t.x = Math.max(L - 4 + lw(t) / 2, Math.min(W - lw(t) / 2, t.lx + (isoDay(ts[t.j]) === isoDay(t.t) ? cw / 2 : 0)));
    const nx = keep.at(-1);
    if (!nx || nx.x - t.x >= (lw(t) + lw(nx)) / 2 + 12) keep.push(t);
  }
  keep.reverse();
  keep.forEach((t, i) => {
    const d = zdate(t.t), days = /^\d+$/.test(t.label) && tk.rows === 2;
    t.major = tk.rows === 2 && (!i || keep[i - 1].unit !== t.unit) ? (days ? d.toLocaleDateString(undefined, {month: "short", year: "numeric"}) : String(d.getFullYear())) : null;
    if (t.major) t.mx = Math.max(L - 4, Math.min(W - labelW(t.major, "ck-yr"), t.x - lw(t) / 2));
  });
  const majors = keep.filter(t => t.major);
  majors.forEach((t, i) => { const nx = majors[i + 1]; if (nx && t.mx + labelW(t.major, "ck-yr") + 12 > nx.mx) t.major = null; });
  return {rows: tk.rows, ticks: keep};
}
/* a lab row's name, hovered: its results' ranges when they differ in the period shown; each cell is coloured against
   its own result's range (#58) */
function rowRanges(s) {
  const d = rangesDiffer(s.pts.filter(p => p.ts >= state.from && p.ts <= state.to));
  return d ? `\n${RANGE_DIFFERS}: ${d} ${s.unit}` : "";
}
/* the heatmap as a table (#60): a row per lab, grouped by panel as on the heatmap, a column per draw day (a day's
   results from two draws each on a line with its time, #471); a result outside its reference range has ▲ or ▼ beside
   it, said as "high" or "low". What a row's and a result's tips say (the name in the records, the reference range) is
   in the row's heading for screen readers too, and the description of its ✎ and ? (#358); a result's own range is
   said in its cell only where the row's ranges differ. A panel's name sticks at the left of its row, as the labs'
   names do, scrolled sideways (#464: its cell is the row's width). After the name, how many of its results are
   outside their range, as at the grid's row's end (#300) */
let hmAbout = 0;
function hmTable(rows, cols, grp) {
  const box = $("#hmTable");
  if (!rows.length || !cols.length) { box.replaceChildren(el("p", {class: "hint", text: "No lab draws in this date range."})); return; }
  const bodies = [];
  let panel = null, tb = null;
  for (const s of rows) {
    if (grp.get(s) !== panel) {
      panel = grp.get(s); tb = el("tbody", {}); bodies.push(tb);
      tb.append(el("tr", {class: "grp"}, el("th", {scope: "rowgroup", colspan: cols.length + 2}, el("span", {text: panel}))));
    }
    const byDay = hmDays(s);
    const differ = rowRanges(s), ranged = [...byDay.values()].flat().find(p => p[2] != null || p[3] != null);
    const about = [nice(s.name) !== s.name ? renamedTitle("lab", s.name) : null,
      differ ? differ.trim() : ranged ? `ref ${rangeWords(ranged)}${s.unit ? " " + s.unit : ""}${ranged.rs === "settings" ? ` (${RANGE_FROM_SETTINGS})` : ""}` : null].filter(Boolean).join("; ");
    const aboutId = about ? "hmAbout" + (++hmAbout) : null, edit = renameButton("lab", s.name), tq = testLink(s.name, true);   // #163
    const name = labOpener(s, "", `${nice(s.name)}${s.unit ? ` (${s.unit})` : ""}`);   // its name opens its panel (lab-panel.js, #296)
    for (const b of [name, edit, tq]) if (b && aboutId) b.setAttribute("aria-describedby", aboutId);
    tb.append(el("tr", {}, el("th", {scope: "row", "data-tip": renamedTitle("lab", s.name) + differ}, name,
      about ? el("span", {class: "vh"}, ", ", el("span", {id: aboutId, text: about})) : null,
      edit || tq ? el("span", {class: "hm-acts"}, edit, tq) : null),   // the ✎ and ? kept together, a phone's name wrapping (#469)
      el("td", {text: [...byDay.values()].flat().filter(hmMark).length.toLocaleString("en-US")}),
      ...cols.map(([k]) => {
        const ps = byDay.get(k); if (!ps) return el("td", {});
        const one = p => {
          const f = hmMark(p), w = f ? FLAG_OF(f) : null;   // as the heatmap marks it
          const ref = refText(p);
          return [w, ref, [p[5] || fmtNum(p[1]), w ? el("span", {"aria-hidden": "true", text: " " + w[0]}) : null, w ? el("span", {class: "vh", text: " " + w[1].toLowerCase()}) : null,
            ref && differ ? el("span", {class: "vh hm-ref", text: ", " + ref}) : null]];
        };
        if (ps.length === 1) { const [w, ref, kids] = one(ps[0]); return el("td", {class: w ? "abn" : null, "data-tip": ref}, ...kids); }
        // a day's results from two draws (#471): each on a line of its own with its time, and its own ▲ or ▼
        return el("td", {"data-tip": ps.map(p => `${fmtTime(p.ts)}: ${one(p)[1] || "no range"}`).join("; ")},
          ...ps.flatMap((p, j) => { const [w, , kids] = one(p); return [j ? el("br") : null, el("span", {class: w ? "abn" : null}, ...kids, ` (${fmtTime(p.ts)})`)]; }));
      })));
  }
  const dia = rows.some(s => [...hmDays(s).values()].some(ps => ps.some(p => hmMark(p) === "A")));
  box.replaceChildren(el("p", {class: "hint", text: `${rows.length} lab${rows.length === 1 ? "" : "s"} on ${cols.length} draw day${cols.length === 1 ? "" : "s"}. ▲ above its reference range, ▼ below${dia ? ", ◆ flagged by its report" : ""}.`}),
    scrollBox({class: "tablewrap hm-table", "aria-label": "Lab results by draw day"}, el("table", {class: "data"},
      el("thead", {}, el("tr", {}, el("th", {scope: "col", text: "Lab"}), el("th", {scope: "col", text: "Outside range"}), ...cols.map(([, ts]) => el("th", {scope: "col", text: fmtD(ts)})))), ...bodies)));
}

