/* Sanguity · Labs tab, as charts (#172; the heatmap is heatmap.js): a chart per chosen lab, or its table.
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
/* ---------------------------------------------------------------- labs */
function latest(s) { return s.pts[s.pts.length - 1]; }
/* ---- the Labs filter (#192): lab names, typed in the lab list's header (charts) or the heatmap's (#labQ, #hmQ: one
   filter for both views, not kept after a reload). state.labQ is what's typed, in lower case ("" for none); the
   Timeline's search is state.q. labMatch(s): a lab the filter lets through, by its name in the records, the name it's
   shown by or its panel. clearLabFilter(): no filter, both boxes emptied, the view drawn again */
state.labQ = "";
const labMatch = s => !state.labQ || [s.name, nice(s.name), s.panel || ""].some(t => t.toLowerCase().includes(state.labQ));
function labCount(box, n, all) {
  $(box).textContent = state.labQ ? `${n || "None"} of ${all}` : `${all} lab${all === 1 ? "" : "s"}`;   // "12 of 64", "64 labs"
  const hm = box === "#hmQn";   // and under the box, what it keeps, with Reset filters (#190, filters.js)
  filterLine(state.labQ ? [`Showing lab names matching “${$(hm ? "#hmQ" : "#labQ").value.trim()}”`] : [], clearLabFilter, () => $(hm ? "#hmQ" : "#labQ"), $(hm ? "#hmSum" : "#labSum"));
}
const labFilterDraw = () => state.labsView === "heatmap" ? renderHeatmap() : renderSeriesList();
function clearLabFilter() { state.labQ = ""; $("#labQ").value = $("#hmQ").value = ""; labFilterDraw(); }
{
  let t;
  for (const [id, other] of [["#labQ", "#hmQ"], ["#hmQ", "#labQ"]]) $(id).oninput = ev => {
    $(other).value = ev.target.value;
    clearTimeout(t); t = setTimeout(() => {
      state.labQ = ev.target.value.trim().toLowerCase(); labFilterDraw();
      if (state.labQ) announce(`${$(id + "n").textContent} labs match.`);   // the count changes without moving focus (#209)
    }, 150);
  };
}
/* a lab's row given focus scrolls clear of the list's sticky header (#202, 2.4.11) */
new ResizeObserver(([e]) => { const list = e.target.parentElement; if (list) list.style.scrollPaddingTop = e.target.offsetHeight + 4 + "px"; }).observe($("#series .series-hd"));
function renderSeriesList() {
  const wrap = $("#seriesRows"); wrap.replaceChildren();
  const all = SERIES.filter(s => !s.watch && s.n >= 2), top = all.filter(labMatch);
  labCount("#labQn", top.length, all.length);
  const groups = new Map();
  for (const s of top) { const g = s.watch ? s.panel : s.n >= 40 ? s.panel || "Other" : "Less frequent"; if (!groups.has(g)) groups.set(g, []); groups.get(g).push(s); }
  const rank = g => g === "Less frequent" ? 2 : g.startsWith("Watch") ? 1 : 0;
  const order = [...groups.keys()].sort((a, b) => rank(a) - rank(b) || (rank(a) === 1 ? 0 : groups.get(b).length - groups.get(a).length));
  // the list's header (#192, index.html): under the filter, what's selected with Clear and Defaults, then the saved
  // views (#37, trends-views.js); the header stays while the rows scroll, so typing in the filter keeps its focus
  $("#seriesSel").replaceChildren(el("b", {text: `${state.sel.size} selected`}), el("span", {class: "spacer"}),
    el("button", {class: "btn", type: "button", text: "Clear", onclick: () => { state.sel.clear(); renderLabs(); }}),
    el("button", {class: "btn", type: "button", text: "Defaults", onclick: () => { state.sel = startLabs(); renderLabs(); }}));
  $("#tvBox").replaceChildren(...[viewsBar()].filter(Boolean));
  if (!top.length) wrap.append(el("p", {class: "hint lab-none"}, `No lab names match “${$("#labQ").value.trim()}”. `,
    el("button", {class: "btn", type: "button", text: "Clear the filter", onclick: () => { clearLabFilter(); $("#labQ").focus(); }})));
  for (const g of order) {
    wrap.append(el("div", {class: "grp", text: g}));
    for (const s of g.startsWith("Watch") ? groups.get(g) : groups.get(g).sort((a, b) => b.n - a.n)) {
      const L = latest(s); const id = "s_" + s.name.replace(/\W/g, "_"), tq = testLink(s.name, true);   // #163
      wrap.append(el("label", {class: "srow" + (tq ? " has-tq" : ""), for: id},
        el("input", {type: "checkbox", id, checked: state.sel.has(s.name) || null, onchange: ev => { ev.target.checked ? state.sel.add(s.name) : state.sel.delete(s.name); renderCharts(); save(); $("#seriesSel b").textContent =`${state.sel.size} selected`; tvRefresh(); }}),
        el("span", {class: "nm", "data-tip": `${renamedTitle("lab", s.name)} · ${s.n} results`, text: nice(s.name)}), tq,
        el("span", {class: "lv" + (L && L[4] ? " abn" : ""), "data-tip": L ? `Latest ${fmtD(L[0])}${ucOrig(s, L) ? " · reported " + ucOrig(s, L) : ""}` : "", text: L ? `${L[4] ? (L[4] === "H" ? "▲ " : "▼ ") : ""}${L[5] || fmtNum(L[1])} ${s.unit}` : ""})));
    }
  }
}
let hoverT = null;
/* extra rows under each chart's time axis, one per layer: {space() -> its height in px (0: nothing to show),
   draw(g, x, top, R)} draws it from y = top, x(t) being the chart's time scale. journal.js adds the journal's markers. */
const CHART_LAYERS = [];
function renderCharts() {
  const box = $("#charts"); box.replaceChildren();
  document.querySelectorAll(".marks-key").forEach(k => k.hidden = !state.marks);   // their legend, while they're shown (#250)
  const sel =SERIES.filter(s => !s.watch && state.sel.has(s.name));
  if (!sel.length) { box.append(el("div", {class: "card empty", text: "Pick lab values on the left to chart them."})); return; }
  for (const s of sel) box.append(chartCard(s));
}
const MARK_KINDS = new Set(["procedure", "imaging"]);
/* a procedure's or an imaging's mark at the top of a lab chart, in its kind's colour: a circle and a square, never ▲ or
   ▼, which on the same chart say above or below range (#250). The legend under Show on charts draws the same. */
function evHead(k, cx, cy) {
  const a = {class: "evhead", "data-k": k, fill: CAT_OF[k].color};
  return k === "imaging" ? el("svg:rect", {...a, x: cx - 3.3, y: cy - 3.3, width: 6.6, height: 6.6})
    : el("svg:circle", {...a, cx, cy, r: 3.7});
}
{
  const legend = document.querySelector("#view-labs .chart-legend");
  const key = (k, words) => el("span", {class: "jlegend marks-key", hidden: true}, el("svg:svg", {viewBox: "0 0 10 10", width: 10, height: 10, "aria-hidden": "true"}, evHead(k, 5, 5)), words);
  if (legend) legend.append(key("procedure", "Procedure"), key("imaging", "Imaging"));
}
function shortTitle(t) {
  let s = t.replace(/\s*\(.*?\)\s*/g, " ").split(/\s+(?:WITH|W\/?O?|WO|AND)\s+/i)[0].trim().toLowerCase();
  s = s.charAt(0).toUpperCase() + s.slice(1);
  return s.length > 24 ? s.slice(0, 23) + "…" : s;
}
const nice = n => CFG.labNicknames[n] || n;   // the name a lab is shown by (Settings > Names, or renamed where shown: rename.js)
/* under a lab's table: the source tags its results carry, each as shown, with the tag in the records and ✎ */
function tagLine(pts) {
  const tags = [...new Set(pts.map(p => p[6]).filter(t => t && t !== "Clinic"))];
  if (!tags.length) return null;
  return el("p", {class: "source tags"}, el("b", {text: tags.length > 1 ? "Source tags " : "Source tag "}),
    ...tags.flatMap((t, i) => [i ? "; " : null, el("span", {text: srcName(t)}),
      srcName(t) !== t ? el("span", {class: "muted", text: ` (${t})`}) : null, renameButton("tag", t)]));
}
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
  return [`${p[5] || fmtNum(p[1])} ${u}`, ucOrig(s, p) ? "Reported as " + ucOrig(s, p) : ""];   // #41: as reported, beside a converted value
}
/* under a chart's name, when its results' reference ranges differ (two labs, or a lab that changed its range): which
   ones, and that the band (or each table row) follows each result's own (#58) */
function rangeNote(s, pts, table) {
  const d = s.watch ? null : rangesDiffer(pts);
  const parts = d ? d.split(/((?:[≤≥] )?-?[\d.]+(?:–-?[\d.]+)?)/).map((t, i) => i % 2 ? el("span", {class: "nw", text: t}) : t) : [];   // a range isn't broken across lines
  return d ? el("p", {class: "rdiff"}, el("b", {text: RANGE_DIFFERS + ": "}), ...parts, ` ${s.unit}. `,
    table ? "Each row gives its own." : "The shaded band steps with each result's own range.") : null;
}
function chartCard(s) {
  const card = el("div", {class: "chart card"});   // the card (#353)
  const pts = s.pts.filter(p => p.ts >= state.from && p.ts <= state.to);
  const nAbn = pts.filter(p => p[4]).length;
  const read = el("span", {class: "read"});
  const showTable = state.tables.has(s.name);
  const hasStages = s.kind === "stack" && pts.some(p => p.parts && (p.parts.deep || p.parts.rem || p.parts.light));
  const legend = hasStages ? el("span", {class: "lg"}, ...[["Deep", "--seq1"], ["REM", "--seq2"], ["Light", "--seq3"]].map(([n, c]) => el("span", {}, el("i", {style: {background: `var(${c})`}}), n)))
    : s.kind === "band" ? el("span", {class: "lg"}, el("span", {}, el("i", {style: {background: "var(--c-lab)", height: "3px"}}), "Daily average"), el("span", {}, el("i", {style: {background: "var(--c-lab)", opacity: .2}}), "Low–high")) : null;
  card.append(el("div", {class: "hd"}, el("span", {class: "name", "data-tip": renamedTitle("lab", s.name), text: nice(s.name)}), s.watch ? null : renameButton("lab", s.name),
    el("span", {class: "unit", text: `${s.unit}${s.uc ? ` (converted from ${s.uc.from})` : ""}${s.watch ? (s.key === "weight" ? " · scale + clinic visits" : s.panel.startsWith("Body") ? " · smart scale" : " · watch, daily") : ""} · ${pts.length} ${s.watch ? "day" : "result"}${pts.length === 1 ? "" : "s"}${nAbn ? ` · ${nAbn} outside reference range` : ""}${pts.some(p => p.rs === "settings") ? " · " + RANGE_FROM_SETTINGS + " for results without one" : ""}`}), testLink(s.name), legend, read,   // "What is this test?" (#163, test-pages.js)
    viewSeg(s.name, nice(s.name), renderCharts),   // Chart | Table (#60)
    el("button", {class: "btn quiet small", type: "button", "data-tip": `Remove ${nice(s.name)}`, "aria-label": `Remove ${nice(s.name)}`, onclick: () => { state.sel.delete(s.name); renderLabs(); }}, icon("x")),
    rangeNote(s, pts, showTable)));
  { const v = vitalLink(s); if (v) card.append(v); }   // a vital's readings at home, on Body and activity (#180, health.js)
  if (showTable) {
    card.append(scrollBox({class: "tablewrap", "aria-label": `${nice(s.name)}, as a table`}, el("table", {class: "data"}, el("thead", {}, el("tr", {}, ["Date", "Value", s.watch ? "Detail" : "Ref range"].map(h => el("th", {scope: "col", text: h})), el("th", {scope: "col"}, el("span", {class: "vh", text: "Flag"})))),
      el("tbody", {}, [...pts].reverse().map(p => { const [a, b] = readText(s, p); return el("tr", {}, el("td", {text: s.watch ? fmtD(p.ts) : fmtDT(p.ts)}), el("td", {class: p[4] ? "abn" : "", text: a + (!s.watch && b ? ` (reported ${ucOrig(s, p)})` : "")}),
        el("td", {text: (s.watch ? b : p[2] != null || p[3] != null ? `${p[2] ?? ""} – ${p[3] ?? ""}${p.rs === "settings" ? " (" + RANGE_FROM_SETTINGS + ")" : ""}` : "") + (p[6] ? ` · ${p[6] === "Clinic" ? "clinic visit" + (p.org ? " (" + p.org + ")" : "") : srcName(p[6])}` : "") + (p.same ? " · same result from " + p.same.length + " more source" + (p.same.length > 1 ? "s" : "") : "")}), el("td", {class: p[4] ? "abn" : ""}, ...flagNodes(p[4]))); })))));   // the mark and the word, not colour alone (#60)
    const src = seriesSource(s); if (src) card.append(src);
    { const t = tagLine(pts); if (t) card.append(t); }   // its results' source tags, renamed here (#38)
    return card;
  }
  const svg = el("svg:svg", {role: "img", class: "pz", "aria-label": `${nice(s.name)} over time`});
  svg._s = s; svg._read = read;
  card.append(svg);
  requestAnimationFrame(() => drawChart(svg, s, pts, read));
  return card;
}
function drawChart(svg, s, pts, read) {
  if (!svg.isConnected) return;
  const marks = markEvents(), hasMs = msInRange(state.from, state.to);
  const layers = CHART_LAYERS.map(l => [l, l.space()]).filter(([, n]) => n > 0), below = layers.reduce((a, [, n]) => a + n, 0);
  const w = Math.max(320, svg.parentElement.clientWidth - 28), h = (s.kind === "stack" || s.kind === "bars" ? 150 : 170) + (hasMs ? 14 : 0) + below;
  const L = 52, R = w - 12, T = (marks.length ? 24 : 10) + (hasMs ? 14 : 0), B = h - 22 - below;
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`); svg.setAttribute("height", h); svg._geom = {L, R, w};
  const x = t => L + (t - state.from) / (state.to - state.from) * (R - L);
  const smoothOn = state.smooth && s.watch && pts.length > 3;
  const vals = pts.map(p => p[1]).concat(s.kind === "band" ? pts.flatMap(p => [p.lo, p.hi]).filter(v => v != null) : []);
  // the reference band: each result's own range, as steps when they differ (#58, ranges.js), never one range for all
  const steps = s.watch ? [] : rangeSteps(pts, state.from, state.to);
  const lows = steps.map(st => st.lo).filter(v => v != null), highs = steps.map(st => st.hi).filter(v => v != null);
  const refLo = lows.length ? Math.min(...lows) : null, refHi = highs.length ? Math.max(...highs) : null;
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
  {
    const edge = {lo: "", hi: ""};   // each edge as one stepped path, joined where neighbouring steps both have it
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
  const msWords = msMarks(g, x, state.from, state.to, hasMs ? 16 : T, B, R, 12);
  // treatment events: dashed rule + head, and a procedure named beside it where there is room: inside the chart, clear
  // of the label before it and of every other mark's head (#312). One left unnamed keeps its tooltip and drawer.
  const headX = marks.map(e => x(e.ts));
  let lastEnd = -1e9;
  marks.forEach((e, i) => {
    const mx = headX[i];
    g.push(el("svg:line", {class: "evmark", x1: mx, x2: mx, y1: T - 4, y2: B}));
    g.push(evHead(e.k, mx, T - 7));
    if (e.k !== "procedure") return;
    const text = shortTitle(e.title), x0 = mx + 6, x1 = x0 + labelW(text, "evlabel");
    if (x0 < lastEnd + 8 || x1 > R || headX.some((hx, j) => j !== i && hx + 5 > x0 && hx - 5 < x1)) return;
    g.push(el("svg:text", {class: "evlabel", x: x0, y: T - 5, text}));
    lastEnd = x1;
  });
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
    for (const p of pts) if (!dense || p[4]) g.push(p[4] ? abnMark(x(p.ts), y(p[1]), p[4]) : el("svg:circle", {class: "pt", cx: x(p.ts), cy: y(p[1]), r: 3}));   // ▲ above, ▼ below its range (#60)
  }
  if (!pts.length) g.push(el("svg:text", {x: (L + R) / 2, y: (T + B) / 2, "text-anchor": "middle", text: "No values in this date range"}));
  const cross = el("svg:line", {class: "cross", y1: T, y2: B, visibility: "hidden"});
  const dot = el("svg:circle", {r: 5.5, fill: "none", stroke: "var(--ink)", "stroke-width": 2, visibility: "hidden", "pointer-events": "none"});
  const hit = el("svg:rect", {x: L, y: T - 12, width: R - L, height: B - T + 12, fill: "transparent"});
  g.push(cross, dot, hit);
  { let top = B + 1; for (const [l, n] of layers) { l.draw(g, x, top, R); top += n; } }   // after hit, so their marks take the pointer
  svg.replaceChildren(...g.filter(Boolean));
  const lastP = pts[pts.length - 1];
  const setRead = p => {
    read.replaceChildren();
    if (!p) { if (lastP) read.append("Latest ", el("b", {text: readText(s, lastP)[0].replace(` ${s.unit}`, "")}), ` · ${fmtD(lastP.ts)}`); return; }
    read.append(el("b", {text: readText(s, p)[0]}), p[4] ? el("span", {style: {color: "var(--crit)", fontWeight: "var(--fw-semibold)"}, text: " " + flagWord(p[4])}) : "", ` · ${s.watch ? fmtD(p.ts) : fmtDT(p.ts)}`);
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
  // what hovering at time t shows (every chart's readout at that date, this one's tooltip at ev); the keyboard too (#60)
  const showAt = (t, px, ev) => {
    let mine = null; document.querySelectorAll("#charts svg").forEach(o => { const b = o._hover && o._hover(t); if (o === svg) mine = b; });
    const near = marks.filter(e => Math.abs(x(e.ts) - px) < 8);
    const nodes = [];
    if (mine) {
      const [a, b] = readText(s, mine);
      nodes.push(el("div", {class: "v", text: a}), el("div", {class: "s"}, el("span", {class: "k", style: {background: "var(--c-lab)"}}), nice(s.name), nice(s.name) !== s.name ? el("span", {class: "muted", text: ` (${s.name})`}) : null), b ? el("div", {class: "s", text: b}) : null,
        el("div", {class: "s", text: (s.watch ? fmtD(mine.ts) : fmtDT(mine.ts)) + (mine[2] != null || mine[3] != null ? " · " + refText(mine) : "")}),
        mine[6] ? el("div", {class: "s", text: "Source: " + srcName(mine[6]) + (srcName(mine[6]) !== mine[6] ? ` (${mine[6]})` : "")}) : null,
        !s.watch && pointSources(s, mine) ? el("div", {class: "s", text: "From " + pointSources(s, mine)}) : null,
        mine[4] ? el("div", {class: "s", style: {color: "var(--crit)"}, text: /^h/i.test(mine[4]) ? "▲ Above range" : /^l/i.test(mine[4]) ? "▼ Below range" : "◆ Flagged"}) : null);
    }
    for (const e of near.slice(0, 3)) nodes.push(el("div", {class: "s mt-1"}, el("span", {class: "k", style: {background: CAT_OF[e.k].color}}), `${e.title} · ${fmtD(e.ts)}`));
    nodes.length ? showTip(nodes, ev) : hideTip();
    return tipWords(nodes);
  };
  hit.addEventListener("pointermove", ev => {
    const r = svg.getBoundingClientRect(), px = (ev.clientX - r.left) * (w / r.width);
    showAt(state.from + (px - L) / (R - L) * (state.to - state.from), px, ev);
  });
  // the keyboard (#60): ← and → step through the results shown, with the same readout and tooltip as hovering
  const unit = s.watch ? (s.kind === "stack" ? "night" : "day") : "result", nAbn = pts.filter(p => p[4]).length;
  chartKeys(svg, `${nice(s.name)}: ${pts.length} ${unit}${pts.length === 1 ? "" : "s"}${pts.length ? ` from ${fmtD(pts[0].ts)} to ${fmtD(lastP.ts)}, latest ${readText(s, lastP)[0]}` : " in this period"}`
    + `${nAbn ? `, ${nAbn} outside reference range` : ""}${rangesDiffer(pts) ? `. ${RANGE_DIFFERS}: ${rangesDiffer(pts)} ${s.unit}` : ""}. Left and right arrow keys step through them.${msWords ? " " + msWords : ""}`,
    () => pts.map(p => p.ts),
    t => { const p = pts.find(p => p.ts === t); return showAt(t, x(t), svgPoint(svg, x(t), p ? y(p[1]) : T)); },
    () => { document.querySelectorAll("#charts svg").forEach(o => o._hover && o._hover(null)); hideTip(); });
  hit.addEventListener("click", ev => {
    const r = svg.getBoundingClientRect(), px = (ev.clientX - r.left) * (w / r.width);
    const e = marks.find(e => Math.abs(x(e.ts) - px) < 8); if (e) openEvent(e);
  });
  hit.addEventListener("pointerleave", () => { if (!chartsBox.classList.contains("dragging")) { document.querySelectorAll("#charts svg").forEach(o => o._hover && o._hover(null)); hideTip(); } });
}
