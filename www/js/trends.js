/* Sanguity · Trends tab: lab and watch series charts.
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
/* ---------------------------------------------------------------- labs */
function latest(s) { return s.pts[s.pts.length - 1]; }
function renderSeriesList() {
  const wrap = $("#series"); wrap.replaceChildren();
  const q = state.q;
  const top = SERIES.filter(s => !s.watch && s.n >= 2 && (!q || s.name.toLowerCase().includes(q) || s.panel.toLowerCase().includes(q)));
  const groups = new Map();
  for (const s of top) { const g = s.watch ? s.panel : s.n >= 40 ? s.panel || "Other" : "Less frequent"; if (!groups.has(g)) groups.set(g, []); groups.get(g).push(s); }
  const rank = g => g === "Less frequent" ? 2 : g.startsWith("Watch") ? 1 : 0;
  const order = [...groups.keys()].sort((a, b) => rank(a) - rank(b) || (rank(a) === 1 ? 0 : groups.get(b).length - groups.get(a).length));
  wrap.append(el("div", {style: {padding: "12px 14px 6px", display: "flex", gap: "8px", alignItems: "center"}}, el("b", {text: `${state.sel.size} selected`}), el("span", {class: "spacer"}),
    el("button", {class: "btn", style: {margin: 0}, text: "Clear", onclick: () => { state.sel.clear(); renderLabs(); }}),
    el("button", {class: "btn", style: {margin: 0}, text: "Defaults", onclick: () => { state.sel = new Set(CFG.trends.defaultLabs.filter(n => SERIES.some(s => s.name === n))); renderLabs(); }})));
  for (const g of order) {
    wrap.append(el("div", {class: "grp", text: g}));
    for (const s of g.startsWith("Watch") ? groups.get(g) : groups.get(g).sort((a, b) => b.n - a.n)) {
      const L = latest(s); const id = "s_" + s.name.replace(/\W/g, "_");
      wrap.append(el("label", {class: "srow", for: id},
        el("input", {type: "checkbox", id, checked: state.sel.has(s.name) || null, onchange: ev => { ev.target.checked ? state.sel.add(s.name) : state.sel.delete(s.name); renderCharts(); save(); wrap.querySelector("b").textContent = `${state.sel.size} selected`; }}),
        el("span", {class: "nm", title: `${s.name} · ${s.n} results`, text: s.name}),
        el("span", {class: "lv" + (L && L[4] ? " abn" : ""), title: L ? `Latest ${fmtD(L[0])}` : "", text: L ? `${L[4] ? (L[4] === "H" ? "▲ " : "▼ ") : ""}${L[5] || fmtNum(L[1])} ${s.unit}` : "text"})));
    }
  }
}
let hoverT = null;
function renderCharts() {
  const box = $("#charts"); box.replaceChildren();
  const sel = SERIES.filter(s => !s.watch && state.sel.has(s.name));
  if (!sel.length) { box.append(el("div", {class: "card empty", text: "Pick lab values on the left to chart them."})); return; }
  for (const s of sel) box.append(chartCard(s));
}
const MARK_KINDS = new Set(["procedure", "imaging"]);
function shortTitle(t) {
  let s = t.replace(/\s*\(.*?\)\s*/g, " ").split(/\s+(?:WITH|W\/?O?|WO|AND)\s+/i)[0].trim().toLowerCase();
  s = s.charAt(0).toUpperCase() + s.slice(1);
  return s.length > 24 ? s.slice(0, 23) + "…" : s;
}
const nice = n => CFG.labNicknames[n] || n;
function markEvents() { return state.marks ? EV.filter(e => MARK_KINDS.has(e.k) && e.ts >= state.from && e.ts <= state.to) : []; }
function smooth7(pts, key = 1) {   // centered 7-day mean, by time
  const out = []; let a = 0, b = 0, sum = 0;
  for (let i = 0; i < pts.length; i++) {
    const t = pts[i].ts;
    while (b < pts.length && pts[b].ts <= t + 3.5 * DAY) { sum += pts[b][key]; b++; }
    while (pts[a].ts < t - 3.5 * DAY) { sum -= pts[a][key]; a++; }
    out.push(sum / (b - a));
  }
  return out;
}
function readText(s, p) {
  const u = s.unit;
  if (s.kind === "band") return [`${fmtNum(p[1])} ${u} avg`, p.lo != null ? `range ${fmtNum(p.lo)}–${fmtNum(p.hi)}` : ""];
  if (s.kind === "stack") { const pr = p.parts || {}; return [`${fmtNum(p[1])} h asleep`, ["deep", "rem", "light"].filter(k => pr[k]).map(k => `${k === "rem" ? "REM" : k} ${fmtNum(pr[k])} h`).join(" · ")]; }
  if (s.kind === "bars") return [`${Math.round(p[1]).toLocaleString()} ${u}`, ""];
  return [`${p[5] || fmtNum(p[1])} ${u}`, ""];
}
function chartCard(s) {
  const card = el("div", {class: "chart"});
  const pts = s.pts.filter(p => p.ts >= state.from && p.ts <= state.to);
  const nAbn = pts.filter(p => p[4]).length;
  const read = el("span", {class: "read"});
  const showTable = state.tables.has(s.name);
  const hasStages = s.kind === "stack" && pts.some(p => p.parts && (p.parts.deep || p.parts.rem || p.parts.light));
  const legend = hasStages ? el("span", {class: "lg"}, ...[["Deep", "--seq1"], ["REM", "--seq2"], ["Light", "--seq3"]].map(([n, c]) => el("span", {}, el("i", {style: {background: `var(${c})`}}), n)))
    : s.kind === "band" ? el("span", {class: "lg"}, el("span", {}, el("i", {style: {background: "var(--c-lab)", height: "3px"}}), "Daily average"), el("span", {}, el("i", {style: {background: "var(--c-lab)", opacity: .2}}), "Low–high")) : null;
  card.append(el("div", {class: "hd"}, el("span", {class: "name", text: s.name}),
    el("span", {class: "unit", text: `${s.unit}${s.watch ? (s.key === "weight" ? " · scale + clinic visits" : s.panel.startsWith("Body") ? " · smart scale" : " · watch, daily") : ""} · ${pts.length} ${s.watch ? "day" : "result"}${pts.length === 1 ? "" : "s"}${nAbn ? ` · ${nAbn} outside reference range` : ""}`}), legend, read,
    el("button", {text: showTable ? "Chart" : "Table", onclick: () => { showTable ? state.tables.delete(s.name) : state.tables.add(s.name); renderCharts(); }}),
    el("button", {text: "✕", title: "Remove", onclick: () => { state.sel.delete(s.name); renderLabs(); }})));
  if (showTable) {
    card.append(el("div", {class: "tablewrap"}, el("table", {class: "data"}, el("thead", {}, el("tr", {}, ["Date", "Value", s.watch ? "Detail" : "Ref range", ""].map(h => el("th", {text: h})))),
      el("tbody", {}, [...pts].reverse().map(p => { const [a, b] = readText(s, p); return el("tr", {}, el("td", {text: s.watch ? fmtD(p.ts) : fmtDT(p.ts)}), el("td", {class: p[4] ? "abn" : "", text: a}),
        el("td", {text: (s.watch ? b : p[2] != null || p[3] != null ? `${p[2] ?? ""} – ${p[3] ?? ""}` : "") + (p[6] ? ` · ${p[6] === "Clinic" ? "clinic visit" + (p.org ? " (" + p.org + ")" : "") : srcName(p[6])}` : "") + (p.same ? " · same result from " + p.same.length + " more source" + (p.same.length > 1 ? "s" : "") : "")}), el("td", {class: p[4] ? "abn" : "", text: p[4] === "H" ? "▲ High" : p[4] === "L" ? "▼ Low" : ""})); })))));
    const src = seriesSource(s); if (src) card.append(src);
    return card;
  }
  const svg = el("svg:svg", {role: "img", class: "pz", "aria-label": `${s.name} over time`});
  svg._s = s; svg._read = read;
  card.append(svg);
  requestAnimationFrame(() => drawChart(svg, s, pts, read));
  return card;
}
function drawChart(svg, s, pts, read) {
  if (!svg.isConnected) return;
  const marks = markEvents(), hasMs = msInRange(state.from, state.to);
  const w = Math.max(320, svg.parentElement.clientWidth - 28), h = (s.kind === "stack" || s.kind === "bars" ? 150 : 170) + (hasMs ? 14 : 0);
  const L = 52, R = w - 12, T = (marks.length ? 24 : 10) + (hasMs ? 14 : 0), B = h - 22;
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`); svg.setAttribute("height", h); svg._geom = {L, R, w};
  const x = t => L + (t - state.from) / (state.to - state.from) * (R - L);
  const smoothOn = state.smooth && s.watch && pts.length > 3;
  const vals = pts.map(p => p[1]).concat(s.kind === "band" ? pts.flatMap(p => [p.lo, p.hi]).filter(v => v != null) : []);
  const lows = pts.map(p => p[2]).filter(v => v != null), highs = pts.map(p => p[3]).filter(v => v != null);
  const refLo = lows.length ? median(lows) : null, refHi = highs.length ? median(highs) : null;
  let lo, hi;
  if (s.kind === "stack" || s.kind === "bars") { lo = 0; hi = Math.max(1, ...vals) * 1.08; }
  else {
    lo = Math.min(...vals, ...(refLo != null ? [refLo] : []), ...(refHi != null && !vals.length ? [refHi] : []));
    hi = Math.max(...vals, ...(refHi != null ? [refHi] : []), ...(refLo != null && !vals.length ? [refLo] : []));
    if (!isFinite(lo)) { lo = 0; hi = 1; }
    if (lo === hi) { lo -= Math.abs(lo) * .1 || 1; hi += Math.abs(hi) * .1 || 1; }
    const pad = (hi - lo) * .08; lo -= pad; hi += pad; if (lo < 0 && Math.min(...vals) >= 0) lo = Math.max(lo, 0 - pad * .5);
  }
  const y = v => B - (v - lo) / (hi - lo) * (B - T);
  const g = [];
  for (const st of STAYS) { if (st.te < state.from || st.ts > state.to) continue; const a = Math.max(L, x(st.ts)), b = Math.min(R, x(st.te)); g.push(el("svg:rect", {class: "stayband", x: a, y: T, width: Math.max(2, b - a), height: B - T})); }
  for (const v of niceTicks(lo, hi, 4)) { g.push(el("svg:line", {class: "gridline", x1: L, x2: R, y1: y(v), y2: y(v)})); g.push(el("svg:text", {x: L - 6, y: y(v) + 4, "text-anchor": "end", text: s.kind === "bars" && v >= 1000 ? (v / 1000) + "k" : fmtNum(v)})); }
  if ((refLo != null || refHi != null) && !s.watch) {
    const a = refHi != null ? Math.max(T, y(refHi)) : T, b = refLo != null ? Math.min(B, y(refLo)) : B;
    if (b > a) g.push(el("svg:rect", {class: "refband", x: L, y: a, width: R - L, height: b - a}));
    if (refHi != null && y(refHi) > T) g.push(el("svg:line", {class: "refedge", x1: L, x2: R, y1: y(refHi), y2: y(refHi)}));
    if (refLo != null && y(refLo) < B) g.push(el("svg:line", {class: "refedge", x1: L, x2: R, y1: y(refLo), y2: y(refLo)}));
  }
  msMarks(g, x, state.from, state.to, hasMs ? 16 : T, B, R, 12);
  // treatment events: dashed rule + label (procedures labelled when there is room)
  let lastLabelX = -1e9;
  for (const e of marks) {
    const mx = x(e.ts);
    g.push(el("svg:line", {class: "evmark", x1: mx, x2: mx, y1: T - 4, y2: B}));
    g.push(el("svg:path", {d: `M${mx - 4},${T - 10} L${mx + 4},${T - 10} L${mx},${T - 4} Z`, fill: CAT_OF[e.k].color}));
    if (e.k === "procedure" && mx - lastLabelX > 90 && mx + 60 < R) {
      g.push(el("svg:text", {class: "evlabel", x: mx + 6, y: T - 5, text: shortTitle(e.title)}));
      lastLabelX = mx;
    }
  }
  for (const [t, lab] of ticks(state.from, state.to, R - L)) g.push(el("svg:text", {x: x(t), y: h - 6, "text-anchor": "middle", text: lab}));
  g.push(el("svg:line", {x1: L, x2: R, y1: B, y2: B, stroke: "var(--axis)"}));
  const dayW = Math.max(1, x(state.from + DAY) - x(state.from));
  const barW = Math.max(1, Math.min(14, dayW * .72));
  const line = (arr, cls, extra = {}) => arr.length > 1 ? el("svg:path", {class: cls, d: arr.map(([px, py], i) => (i ? "L" : "M") + px.toFixed(1) + "," + py.toFixed(1)).join(""), ...extra}) : null;
  if (s.kind === "bars") {
    for (const p of pts) { const top = y(p[1]); g.push(el("svg:rect", {x: x(p.ts) - barW / 2, y: top, width: barW, height: Math.max(0, B - top), rx: Math.min(2, barW / 2), fill: "var(--c-lab)", "fill-opacity": smoothOn ? .45 : .85})); }
    if (smoothOn) g.push(line(smooth7(pts).map((v, i) => [x(pts[i].ts), y(v)]), "line", {stroke: "var(--ink2)"}));
  } else if (s.kind === "stack") {
    for (const p of pts) {
      const pr = p.parts || {}, cx = x(p.ts) - barW / 2; let acc = 0;
      const segs = pr.deep || pr.rem || pr.light ? [["deep", "--seq1"], ["rem", "--seq2"], ["light", "--seq3"]] : [];
      for (const [k, c] of segs) { const v = pr[k] || 0; if (!v) continue; const y1 = y(acc + v), y0 = y(acc); g.push(el("svg:rect", {x: cx, y: y1, width: barW, height: Math.max(0, y0 - y1 - (acc + v < p[1] - .01 ? 1 : 0)), fill: `var(${c})`, "fill-opacity": smoothOn ? .55 : 1})); acc += v; }
      if (!segs.length) g.push(el("svg:rect", {x: cx, y: y(p[1]), width: barW, height: Math.max(0, B - y(p[1])), fill: "var(--seq2)", "fill-opacity": smoothOn ? .55 : 1}));
    }
    if (smoothOn) g.push(line(smooth7(pts).map((v, i) => [x(pts[i].ts), y(v)]), "line", {stroke: "var(--ink2)"}));
  } else if (s.kind === "band") {
    const bp = pts.filter(p => p.lo != null && p.hi != null);
    if (bp.length > 1) {
      const loS = smoothOn ? smooth7(bp.map(p => Object.assign([p.ts, p.lo], {ts: p.ts}))) : bp.map(p => p.lo);
      const hiS = smoothOn ? smooth7(bp.map(p => Object.assign([p.ts, p.hi], {ts: p.ts}))) : bp.map(p => p.hi);
      const top = bp.map((p, i) => `${x(p.ts).toFixed(1)},${y(hiS[i]).toFixed(1)}`), bot = bp.map((p, i) => `${x(p.ts).toFixed(1)},${y(loS[i]).toFixed(1)}`).reverse();
      g.push(el("svg:path", {d: "M" + top.join("L") + "L" + bot.join("L") + "Z", fill: "var(--c-lab)", "fill-opacity": .16}));
    }
    const avg = smoothOn ? smooth7(pts) : pts.map(p => p[1]);
    g.push(line(avg.map((v, i) => [x(pts[i].ts), y(v)]), "line"));
  } else {
    if (smoothOn) { g.push(line(pts.map(p => [x(p.ts), y(p[1])]), "line", {"stroke-opacity": .3})); g.push(line(smooth7(pts).map((v, i) => [x(pts[i].ts), y(v)]), "line")); }
    else if (pts.length > 1) g.push(line(pts.map(p => [x(p.ts), y(p[1])]), "line"));
    const dense = pts.length > 120 || s.watch;
    for (const p of pts) if (!dense || p[4]) g.push(el("svg:circle", {class: "pt" + (p[4] ? " abn" : ""), cx: x(p.ts), cy: y(p[1]), r: p[4] ? 4 : 3}));
  }
  if (!pts.length) g.push(el("svg:text", {x: (L + R) / 2, y: (T + B) / 2, "text-anchor": "middle", text: "No values in this date range"}));
  const cross = el("svg:line", {class: "cross", y1: T, y2: B, visibility: "hidden"});
  const dot = el("svg:circle", {r: 5.5, fill: "none", stroke: "var(--ink)", "stroke-width": 2, visibility: "hidden", "pointer-events": "none"});
  const hit = el("svg:rect", {x: L, y: T - 12, width: R - L, height: B - T + 12, fill: "transparent"});
  g.push(cross, dot, hit);
  svg.replaceChildren(...g.filter(Boolean));
  const lastP = pts[pts.length - 1];
  const setRead = p => {
    read.replaceChildren();
    if (!p) { if (lastP) read.append("Latest ", el("b", {text: readText(s, lastP)[0].replace(` ${s.unit}`, "")}), ` · ${fmtD(lastP.ts)}`); return; }
    read.append(el("b", {text: readText(s, p)[0]}), p[4] ? el("span", {style: {color: "var(--crit)", fontWeight: 650}, text: p[4] === "H" ? " ▲ High" : " ▼ Low"}) : "", ` · ${s.watch ? fmtD(p.ts) : fmtDT(p.ts)}`);
  };
  setRead(null);
  svg._hover = t => {
    if (t == null || !pts.length) { cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); setRead(null); return null; }
    let best = pts[0]; for (const p of pts) if (Math.abs(p.ts - t) < Math.abs(best.ts - t)) best = p;
    const tol = Math.max((state.to - state.from) * 0.03, s.watch ? DAY * .6 : 0);
    if (Math.abs(best.ts - t) > tol) { cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); setRead(null); return null; }
    cross.setAttribute("x1", x(best.ts)); cross.setAttribute("x2", x(best.ts)); cross.setAttribute("visibility", "visible");
    dot.setAttribute("cx", x(best.ts)); dot.setAttribute("cy", y(best[1])); dot.setAttribute("visibility", "visible");
    setRead(best); return best;
  };
  hit.addEventListener("pointermove", ev => {
    const r = svg.getBoundingClientRect(), px = (ev.clientX - r.left) * (w / r.width);
    const t = state.from + (px - L) / (R - L) * (state.to - state.from);
    let mine = null; document.querySelectorAll("#charts svg").forEach(o => { const b = o._hover && o._hover(t); if (o === svg) mine = b; });
    const near = marks.filter(e => Math.abs(x(e.ts) - px) < 8);
    const nodes = [];
    if (mine) {
      const [a, b] = readText(s, mine);
      nodes.push(el("div", {class: "v", text: a}), el("div", {class: "s"}, el("span", {class: "k", style: {background: "var(--c-lab)"}}), s.name), b ? el("div", {class: "s", text: b}) : null,
        el("div", {class: "s", text: (s.watch ? fmtD(mine.ts) : fmtDT(mine.ts)) + (mine[2] != null || mine[3] != null ? ` · ref ${mine[2] ?? ""}–${mine[3] ?? ""}` : "")}),
        mine[6] ? el("div", {class: "s", text: "Source: " + srcName(mine[6])}) : null,
        !s.watch && pointSources(s, mine) ? el("div", {class: "s", text: "From " + pointSources(s, mine)}) : null,
        mine[4] ? el("div", {class: "s", style: {color: "var(--crit)"}, text: mine[4] === "H" ? "▲ Above range" : "▼ Below range"}) : null);
    }
    for (const e of near.slice(0, 3)) nodes.push(el("div", {class: "s", style: {marginTop: "4px"}}, el("span", {class: "k", style: {background: CAT_OF[e.k].color}}), `${e.title} · ${fmtD(e.ts)}`));
    nodes.length ? showTip(nodes, ev) : hideTip();
  });
  hit.addEventListener("click", ev => {
    const r = svg.getBoundingClientRect(), px = (ev.clientX - r.left) * (w / r.width);
    const e = marks.find(e => Math.abs(x(e.ts) - px) < 8); if (e) openEvent(e);
  });
  hit.addEventListener("pointerleave", () => { if (!chartsBox.classList.contains("dragging")) { document.querySelectorAll("#charts svg").forEach(o => o._hover && o._hover(null)); hideTip(); } });
}
