/* Sanguity · Labs tab, compared (#297; the charts are trends.js, the heatmap heatmap.js): up to three labs on one
   scale, each result placed within its own printed range (chartkit.js ckPos: 0% at its low limit, 100% at its high),
   so labs in any units share one axis, never a second y-scale; and each lab's median place before a milestone and from
   it on, as a dumbbell. Places and counts only, worded as what the records hold: no fitted line, no correlation, no
   word for whether a difference is good. Each has its table, steps from the keyboard and fits a phone.
   Plain script, loaded after heatmap.js; all files share one global scope. */
"use strict";
const cmpKey = s => s.name + "\n" + s.unit;   // a lab kept in two units is two series (#438)
/* a lab's results from a to b that can be placed: a low and a high limit on their report (or from Settings) */
const cmpPlaced = (s, a = state.from, b = state.to) => s.pts.filter(p => p.ts >= a && p.ts <= b && ckPos(p) != null);
/* the labs that can be compared: two such results on file at least */
const cmpLabs = () => SERIES.filter(s => !s.watch && cmpPlaced(s, -Infinity, Infinity).length >= 2);
/* a place in words, a minus sign for one under the low limit: "62%", "−15%" */
const cmpPct = v => (Math.round(v) < 0 ? "−" : "") + Math.abs(Math.round(v)) + "%";
/* a result's place in words, said as a bound where its value is one (#752): "<5" against 0–5 is "<100%", not 100% */
const CMP_BOUND = {"<": "<", "<=": "≤", ">": ">", ">=": "≥"};
const cmpPlace = p => (CMP_BOUND[rangeCmp(p[5])] || "") + cmpPct(ckPos(p));
const cmpMed = a => { const s = [...a].sort((x, y) => x - y), n = s.length; return n % 2 ? s[n >> 1] : (s[n / 2 - 1] + s[n / 2]) / 2; };
const CMP_SLOT = ["Lab 1 of 3: a solid line, dots", "Lab 2 of 3: a dashed line, squares", "Lab 3 of 3: a dotted line, rings"];
let cmpSel = null, cmpMsDate = null;   // the slots' labs (cmpKey, "" for none); the milestone picked, by its date
/* the slots' series (null where a slot is empty, or its lab is no longer on file). At first, up to three of the labs
   charted, in the list's order, made up to two from the most measured */
function cmpSeries(labs = cmpLabs()) {
  if (!cmpSel) {
    const on = labGroups(byFrequency(labs.filter(s => state.sel.has(s.name)))).flatMap(g => g.labs).slice(0, 3);
    const pick = [...on, ...byFrequency(labs).filter(s => !on.includes(s))].slice(0, Math.max(2, on.length));
    cmpSel = [0, 1, 2].map(i => pick[i] ? cmpKey(pick[i]) : "");
  }
  return cmpSel.map(k => labs.find(s => cmpKey(s) === k) || null);
}
/* a result's flag in the tooltip's words */
const cmpFlag = f => /^h/i.test(f) ? "▲ Above range" : /^l/i.test(f) ? "▼ Below range" : "◆ Flagged";
function renderCompare() {
  const labs = cmpLabs(), body = $("#cmpBody"), pick = $("#cmpPick");
  if (labs.length < 2) {
    pick.replaceChildren(); $("#cmpMsCard").hidden = true; $("#cmpHd .view-seg")?.remove();
    body.replaceChildren(emptyState("Comparing needs two labs whose reports print a low and a high limit, each with two results or more."));
    return;
  }
  const list = cmpSeries(labs), slots = list.map((s, i) => s && {s, i}).filter(Boolean);
  // the slots: each its key, its lab (a lab in another slot can't be picked twice) and its readout under it
  const groups = labGroups(byFrequency(labs));
  pick.replaceChildren(...[0, 1, 2].map(i => el("div", {class: "cmp-slot"}, ckSeriesKey(i),
    el("select", {id: "cmpLab" + i, "aria-label": CMP_SLOT[i], onchange: ev => { cmpSel[i] = ev.target.value; keepFocus(renderCompare); }},
      i ? el("option", {value: "", text: "None", selected: !list[i] || null}) : null,
      ...groups.map(({panel, labs: ls}) => el("optgroup", {label: panel}, ...ls.map(s => el("option", {value: cmpKey(s), text: `${nice(s.name)} (${s.unit})`,
        selected: list[i] === s || null, disabled: list.some((o, j) => j !== i && o === s) || null}))))),
    el("span", {class: "read", id: "cmpRead" + i}))));
  const hd = $("#cmpHd"); hd.querySelector(".view-seg")?.remove(); hd.append(viewSeg("#cmp", "the labs compared", renderCompare));
  const placed = slots.map(r => ({...r, pts: cmpPlaced(r.s)})), inP = r => r.s.pts.filter(p => p.ts >= state.from && p.ts <= state.to);
  const kids = [];
  if (!placed.some(r => r.pts.length)) kids.push(wayBack("results of the labs compared"));   // #382
  if (state.tables.has("#cmp")) kids.push(cmpTable(slots));
  else {
    const svg = chartSvg(cmpSays(placed), {id: "cmpSvg", class: "pz"});   // named as it's drawn (#652)
    svg._slots = slots;
    kids.push(svg);
    requestAnimationFrame(() => cmpDraw(svg));
  }
  // what isn't drawn: results whose report gives one limit or none, in the table only
  const off = placed.map(r => [r, inP(r).length - r.pts.length]).filter(([, n]) => n);
  if (off.length) kids.push(el("p", {class: "hint", text: `Not drawn, listed in the table: ${off.map(([r, n]) => `${n} result${n > 1 ? "s" : ""} of ${nice(r.s.name)}`).join(", ")}, whose report gives one limit or none.`}));
  body.replaceChildren(...kids);
  cmpRead(null);
  renderCmpMs(placed.filter(r => r.pts.length));
}
/* the slots' readouts: each lab's latest in the period, or got[i], its result at the date hovered (null: none near) */
function cmpRead(got) {
  const list = cmpSel ? cmpSeries() : [];
  list.forEach((s, i) => {
    const r = $("#cmpRead" + i); if (!r) return;
    const pts = s ? cmpPlaced(s) : [], p = got ? got[i] : pts.at(-1);
    r.textContent = !s ? "" : p ? `${got ? "" : "Latest "}${readText(s, p)[0]} · ${cmpPlace(p)} · ${fmtD(p.ts)}` : got ? "No result near this date" : "No results in this period";
  });
}
/* the chart: the period's time scale, the place in each result's own range up the side, the range itself the band from
   0% to 100%; each lab a line in its slot's ink and dash, its results its slot's mark (▲/▼/◆ for one its report flags,
   as on every Labs chart). Milestones a faint line each, named once over it. live: a frame of a drag (#292) */
function cmpDraw(svg, live) {
  if (!svg.isConnected) return;
  const w = live && svg._geom ? svg._geom.w : labW(svg.parentElement), [L, R] = labLR(w), x = ckScale(state.from, state.to, L, R), k = ckK();
  const on = svg._slots.map(r => ({...r, pts: cmpPlaced(r.s)})), all = on.flatMap(r => r.pts.map(ckPos)), [lo, hi] = ckPosSpan(all);
  const g = [], ms = MILESTONES.map(m => [dayTs(m.date), m.label]).filter(([t]) => t >= state.from && t <= state.to);
  ckUnit(g, "% of its own range", 12 * k);
  const T = ms.length ? ckFlagNames(g, ms.map(([t, n]) => ({x: x(t), text: n, title: `${n} · ${fmtD(t)}`})), L, R, 14 * k) : 22 * k;
  const tk = ckTimeTicks(state.from, state.to, x, w), B = T + 200, h = B + ckAxisH(tk.rows);
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`); svg.setAttribute("height", h); svg._geom = {L, R, T, B, w};
  const y = ckScale(lo, hi, B, T), at = p => y(Math.max(lo, Math.min(hi, ckPos(p))));
  for (const st of STAYS) { if (st.te < state.from || st.ts > state.to) continue; const a = Math.max(L, x(st.ts)), b = Math.min(R, x(st.te)); g.push(el("svg:rect", {class: "stayband", x: a, y: T, width: Math.max(2, b - a), height: B - T})); }
  ckGrid(g, y, ckPosTicks(lo, hi), L, R, cmpPct);
  ckBand(g, [{a: state.from, b: state.to, lo: 0, hi: 100}], x, y, L, R, T, B);
  const msAt = g.length; msMarks(g, x, state.from, state.to, T, B, R, null);
  for (const m of g.slice(msAt)) m.classList.add("faint");
  ckBandLabel(g, "0–100%: each result's own range", {lo: 0, hi: 100}, y, R, T, B, on.flatMap(r => r.pts.map(p => [x(p.ts), at(p)])), L);
  ckAxis(g, tk, L, R, B, B + 15);
  for (const r of on) {
    const c = CK_SERIES[r.i], xy = ckDecimate(r.pts.map(p => [x(p.ts), at(p)]), q => q[0], 1, {ends: true});
    if (xy.length > 1) g.push(el("svg:path", {class: "cmp-line", stroke: c.color, "stroke-dasharray": c.dash, d: xy.map(([px, py], i) => (i ? "L" : "M") + px.toFixed(1) + "," + py.toFixed(1)).join("")}));
    const dense = r.pts.length > 120;   // as on a lab's chart: then only the flagged ones' marks
    for (const p of r.pts) if (p[4]) g.push(abnMark(x(p.ts), at(p), p[4])); else if (!dense) g.push(ckSeriesMark(r.i, x(p.ts), at(p), 3.4));
  }
  if (!all.length) g.push(el("svg:text", {x: (L + R) / 2, y: (T + B) / 2, "text-anchor": "middle", text: "No results of these labs in this date range"}));
  const cross = ckCross(T, B), rings = on.map(() => el("svg:circle", {r: 6, fill: "none", stroke: "var(--ink)", "stroke-width": 2, visibility: "hidden", "pointer-events": "none"}));
  const hit = el("svg:rect", {x: L, y: T - 12, width: R - L, height: B - T + 12, fill: "transparent"});
  g.push(cross, ...rings, hit);
  svg.replaceChildren(...g);
  // hovering at time t: the nearest date with a result, each lab's result nearest it (within 3% of the period) ringed,
  // the slots' readouts saying them, and the tooltip at the top of the crosshair; the keyboard steps through the same
  const times = [...new Set(on.flatMap(r => r.pts.map(p => p.ts)))].sort((a, b) => a - b), tol = (state.to - state.from) * .03;
  const near = (ts, t, get = v => v) => { let b = null; for (const v of ts) if (b == null || Math.abs(get(v) - t) < Math.abs(get(b) - t)) b = v; return b != null && Math.abs(get(b) - t) <= tol ? b : null; };
  svg._hover = t => {
    const t0 = t == null ? null : near(times, t);
    ckCrossAt(cross, t0 == null ? null : x(t0));
    const got = [null, null, null];
    on.forEach((r, j) => {
      const p = t0 == null ? null : near(r.pts, t0, q => q.ts);
      rings[j].setAttribute("visibility", p ? "visible" : "hidden");
      if (p) { rings[j].setAttribute("cx", x(p.ts)); rings[j].setAttribute("cy", at(p)); got[r.i] = p; }
    });
    cmpRead(t0 == null ? null : got);
    return t0 == null ? null : [t0, got];
  };
  const showAt = (t, px) => {
    const h = svg._hover(t); if (!h) { hideTip(); return ""; }
    const [t0, got] = h, nodes = [el("div", {class: "v", text: fmtD(t0)}), ...on.map(r => { const p = got[r.i]; return p ? el("div", {class: "s"}, ckSeriesKey(r.i),
      `${nice(r.s.name)}: ${readText(r.s, p)[0]}, ${cmpPlace(p)} of its range (${refWords(p)})${p[4] ? " · " + cmpFlag(p[4]) : ""}${isoDay(p.ts) !== isoDay(t0) ? " · " + fmtD(p.ts) : ""}`) : null; })];
    ckTip(svg, x(t0) ?? px, nodes);
    return tipWords(nodes);
  };
  hit.addEventListener("pointermove", ev => { const b = svg.getBoundingClientRect(), px = (ev.clientX - b.left) * (w / b.width); showAt(x.inv(px), px); });
  hit.addEventListener("pointerleave", () => { if (!$("#labsCompare").classList.contains("dragging")) { svg._hover(null); hideTip(); } });
  chartKeys(svg, cmpSays(on), () => times, t => showAt(t, x(t)), () => { svg._hover(null); hideTip(); });
}
/* the chart's words, its name (chartKeys): each lab's results placed in the period and the latest's place, and the
   milestones. on: the slots' labs with their results placed ({s, pts}) */
const cmpSays = on => `Labs compared within their own ranges, ${fmtD(state.from)} to ${fmtD(state.to)}, 0% at each result's low limit and 100% at its high: `
  + on.map(r => `${nice(r.s.name)}, ${r.pts.length} result${r.pts.length === 1 ? "" : "s"}${r.pts.length ? `, latest ${cmpPlace(r.pts.at(-1))} of its range` : ""}`).join("; ")
  + `. Left and right arrow keys step through the dates.${msSay(state.from, state.to) ? " " + msSay(state.from, state.to) : ""}`;
/* the chart as a table: every result of the labs compared in the period, newest first, with its place in its range (or
   why it has none) */
function cmpTable(slots) {
  const rows = slots.flatMap(({s}) => s.pts.filter(p => p.ts >= state.from && p.ts <= state.to).map(p => [s, p])).sort((a, b) => b[1].ts - a[1].ts);
  return chartTable(["Date", "Lab", "Value", "Reference range", "Place in its range"], rows.map(([s, p]) => [fmtDT(p.ts), nice(s.name), [readText(s, p)[0], p[4]],
    refWords(p) ?? "None on the report", ckPos(p) != null ? cmpPlace(p) : "Not placed: one limit or none"]));
}

/* ---- before and after a milestone: for each lab compared, the median place of its results in the period before the
   milestone's day and from that day on, a ring and a dot joined, with how many results each is of and the difference
   in points of the range. The milestone: one in the period, picked here (at first the one most labs have results on
   both sides of) */
function cmpSides(pts, t) {
  // a result given as a bound ("<5", #752) has no one place: left out of the medians, and said (bound: their values)
  const side = a => { if (!a.length) return null; const m = a.filter(labSure);
    return {n: a.length, bound: a.filter(p => !labSure(p)).map(p => p[5]), pos: m.length ? cmpMed(m.map(ckPos)) : null,
      val: m.length ? labStats(m).median : null, one: !rangesDiffer(m)}; };
  return {before: side(pts.filter(p => p.ts < t)), after: side(pts.filter(p => p.ts >= t))};
}
/* a side's median value and median place: as one ("12.85 g/dL, −14% of its range") only where its results have one
   range; where it changed, two figures, the median value not where the median place is in either range (#653: 12.85
   was −16% of 12–16 and +21% of 13.5–17.5, said as −14%). Results given as a bound said as left out (#752) */
const cmpBound = d => `${d.bound.length} given as a bound (${listOf(d.bound.map(t => `“${t}”`), ", ")})`;
const cmpMedWords = (d, u) => d.pos == null ? `no median: ${cmpBound(d)}` : `${d.val} ${u}${d.one ? `, ${cmpPct(d.pos)} of its range` : `; median place ${cmpPct(d.pos)} of each result's own range`}`
  + (d.bound.length ? `; ${cmpBound(d)} left out` : "");
/* a side's median place in words: "62%", "none before", or "bounds only" where every result of it was given as one */
const cmpSidePct = (d, none) => !d ? none : d.pos == null ? "bounds only" : cmpPct(d.pos);
function renderCmpMs(placed) {
  const card = $("#cmpMsCard"), pick = $("#cmpMsPick"), body = $("#cmpMsBody");
  card.hidden = !placed.length;
  if (!placed.length) return;
  const hd = $("#cmpMsHd"); hd.querySelector(".view-seg")?.remove();
  const day = m => zParse(m.date + "T00:00:00"), ms = MILESTONES.filter(m => day(m) > state.from + DAY && day(m) <= state.to);   // (one at the period's start has no before)
  if (!ms.length) {
    pick.replaceChildren();
    body.replaceChildren(el("p", {class: "hint", text: MILESTONES.length ? "No milestone falls in this period. A longer one, or All, takes in yours." : "No milestones yet: they're set in Settings, under Milestones."}));
    return;
  }
  hd.append(viewSeg("#cmpms", "before and after", renderCompare));
  const both = m => placed.filter(r => { const s = cmpSides(r.pts, day(m)); return s.before && s.after; }).length;
  const m = ms.find(m => m.date === cmpMsDate) || ms.reduce((a, b) => both(b) > both(a) || (both(b) === both(a) && b.primary && !a.primary) ? b : a);
  const sel = el("select", {id: "cmpMs", onchange: ev => { cmpMsDate = ev.target.value; keepFocus(renderCompare); }},
    ...ms.map(o => el("option", {value: o.date, text: `${o.label} · ${fmtD(dayTs(o.date))}`, selected: o === m || null})));
  pick.replaceChildren(el("div", {class: "cmp-slot"}, el("label", {for: "cmpMs", text: "Milestone"}), sel),
    ckLegend([["var(--ink2)", `Median before ${lcLabel(m.label)}`, "ring"], ["var(--ink2)", "From it on", 1]]));
  const t = day(m), rows = placed.map(r => ({...r, ...cmpSides(r.pts, t)}));
  if (state.tables.has("#cmpms")) {
    const side = (r, d) => d ? [String(d.n), cmpMedWords(d, r.s.unit)] : ["0", "No results"];
    body.replaceChildren(chartTable(["Lab", "Results before", "Median before", "Results from it on", "Median from it on", "Difference in median place"],
      rows.map(r => [nice(r.s.name), ...side(r, r.before), ...side(r, r.after), cmpDiff(r)])));
    return;
  }
  const svg = chartSvg(cmpMsSays(rows, m), {id: "cmpMsSvg"});   // named as it's drawn (#652)
  body.replaceChildren(svg);
  requestAnimationFrame(() => cmpMsDraw(svg, rows, m));
}
/* the difference after less before, in points of the range: "↓ 32 points", "no change"; "—" with a side empty */
const cmpBoth = r => r.before?.pos != null && r.after?.pos != null;
const cmpDiff = r => cmpBoth(r) ? chgWords(r.after.pos - r.before.pos, Math.abs(Math.round(r.after.pos - r.before.pos)), " points") : "—";
/* a row's words: "Hemoglobin: 62% → 30%, ↓ 32 points" */
const cmpMsWords = r => `${nice(r.s.name)}: ${cmpSidePct(r.before, "none before")} → ${cmpSidePct(r.after, "none from it on")}${cmpBoth(r) ? ", " + cmpDiff(r) : ""}`;
/* the dumbbells: a row per lab, its words over its line, on one scale of places across (the band 0–100% under them) */
function cmpMsDraw(svg, rows, m) {
  if (!svg.isConnected) return;
  const w = labW(svg.parentElement), k = ckK(), L = 18 * k, R = w - 18 * k, g = [];
  const [lo, hi] = ckPosSpan(rows.flatMap(r => [r.before?.pos, r.after?.pos].filter(v => v != null))), x = ckScale(lo, hi, L, R);
  // each row's words on one line, or the name over its figures where they don't fit; cut with "…" where even it doesn't
  const fit = (t, room) => { if (labelW(t, "db-name") <= room) return t; for (let n = t.length - 1; n > 1; n--) if (labelW(t.slice(0, n) + "…", "db-name") <= room) return t.slice(0, n) + "…"; return "…"; };
  // each row: its words, then its line in a strip of the scale (the grid and the band, 0–100%, behind it, not the words)
  let yy = 0;
  const lay = rows.map(r => {
    const name = nice(r.s.name) + ":", rest = cmpMsWords(r).slice(name.length), one = labelW(name + rest, "db-name") <= w - 8;
    const lines = one ? [[name, rest]] : [[fit(name, w), ""], ["", fit(rest.trim(), w)]];
    const top = yy; yy += (lines.length * 16 + 26) * k;
    return {r, lines, top, cy: yy - 13 * k};
  });
  // the scale's numbers under it: every 25, 50 or 100, the first whose labels fit side by side (larger text, #622)
  const ticks = [ckPosTicks(lo, hi), ...[50, 100].map(st => ckPosTicks(lo, hi).filter(v => !(v % st)))]
    .find(ts => ts.every((v, i) => !i || x(v) - x(ts[i - 1]) >= (labelW(cmpPct(v), "") + labelW(cmpPct(ts[i - 1]), "")) / 2 + 6)) || [0, 100];
  const T = 0, B = yy, h = B + 20 * k;
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`); svg.setAttribute("height", h); svg._geom = {L, R, T, B, w};
  for (const v of ticks) g.push(el("svg:text", {x: x(v), y: B + 15 * k, "text-anchor": "middle", text: cmpPct(v)}));
  g.push(el("svg:line", {x1: L, x2: R, y1: B, y2: B, stroke: "var(--axis)"}));
  for (const {r, lines, top, cy} of lay) {
    const a = cy - 10 * k, b = cy + 10 * k;
    g.push(...ticks.map(v => el("svg:line", {class: "gridline", x1: x(v), x2: x(v), y1: a, y2: b})), el("svg:rect", {class: "refband", x: x(0), y: a, width: x(100) - x(0), height: b - a}),
      ...[0, 100].map(v => el("svg:line", {class: "refedge", x1: x(v), x2: x(v), y1: a, y2: b})));
    lines.forEach(([n, t], j) => g.push(el("svg:text", {class: "db-name", x: 0, y: top + (j + 1) * 16 * k - 2 * k}, n ? el("svg:tspan", {class: "db-lab", text: n}) : null, t)));
    ckDumbbell(g, x, lo, hi, cy, r.i, r.before?.pos, r.after?.pos);
  }
  const hl = el("svg:rect", {class: "db-hl", x: 0, width: w, rx: 4, visibility: "hidden"});
  g.unshift(hl);
  svg.replaceChildren(...g);
  // a row hovered, or stepped to with the arrow keys: it's marked, and the tooltip says its figures
  const said = r => [el("div", {class: "v", text: nice(r.s.name)}), ...[["Before " + lcLabel(m.label), r.before], [`From ${lcLabel(m.label)} on`, r.after]].map(([k2, d]) =>
    el("div", {class: "s", text: `${k2}: ${d ? `median ${cmpMedWords(d, r.s.unit)}, of ${d.n} result${d.n > 1 ? "s" : ""}` : "no results"}`})),
    cmpBoth(r) ? el("div", {class: "s", text: "Difference: " + cmpDiff(r)}) : null];
  const show = i => {
    if (i == null || !lay[i]) { hl.setAttribute("visibility", "hidden"); hideTip(); return ""; }
    const {r, top, cy} = lay[i], nodes = said(r);
    hl.setAttribute("y", top); hl.setAttribute("height", cy + 10 * k - top); hl.setAttribute("visibility", "visible");
    const v = r.after?.pos ?? r.before?.pos ?? 0;
    showTip(nodes, svgPoint(svg, x(Math.max(lo, Math.min(hi, v))), cy + 6));
    return tipWords(nodes);
  };
  svg.onpointermove = ev => { const b = svg.getBoundingClientRect(), py = (ev.clientY - b.top) * (w / b.width); show(lay.findIndex(l => py >= l.top && py <= l.cy + 10 * k)); };
  svg.onpointerleave = () => show(null);
  chartKeys(svg, cmpMsSays(rows, m), () => lay.map((_, i) => i), show, () => show(null));
}
/* the dumbbells' words, their name (chartKeys): each lab's median place before the milestone m and from it on */
const cmpMsSays = (rows, m) => `Each lab's median place in its own range before ${lcLabel(m.label)} (${fmtD(dayTs(m.date))}) and from it on, in this period: `
  + rows.map(r => cmpMsWords(r).replace("→", "then")).join("; ") + ". Left and right arrow keys step through the labs.";
/* drag to pan, wheel or pinch to zoom, as Labs' charts (charts.js panZoom): the same period */
panZoom($("#labsCompare"), {...mainWindow,
  plot: ev => { const s = ev.target.closest?.("#cmpSvg"); return s && s._geom ? s : null; }, span: svgSpan,
  start: () => $("#cmpSvg")?._hover?.(null), data: () => seriesSpan((cmpSel ? cmpSeries() : []).filter(Boolean)),
  draw: () => { const s = $("#cmpSvg"); if (s?._geom) cmpDraw(s, true); stripDraw(); }, end: () => { renderFilters(); renderCompare(); save(); }});
