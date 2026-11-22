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
/* the list's labs by panel (#279, labGroups in util.js), the most measured first in each and the panels in that order;
   the labs with no panel in a collapsed Other (open while filtering, or once opened). Series, not names: a lab kept in
   two units is two rows (#438) */
const byFrequency = list => [...list].sort((a, b) => b.n - a.n || nice(a.name).localeCompare(nice(b.name)));
let labOtherOpen = false;
function renderSeriesList() {
  const wrap = $("#seriesRows"); wrap.replaceChildren();
  const all = SERIES.filter(s => !s.watch && s.n >= 2), top = all.filter(labMatch), ids = new Set();
  labCount("#labQn", top.length, all.length);
  const groups = labGroups(byFrequency(top));
  // the list's header (#192, index.html): under the filter, what's selected with Clear and Defaults, then the saved
  // views (#37, trends-views.js); the header stays while the rows scroll, so typing in the filter keeps its focus,
  // and Clear and Defaults keep it on themselves as the charts are drawn again (store.js keepFocus, #462)
  $("#seriesSel").replaceChildren(el("b", {text: `${state.sel.size} selected`}), el("span", {class: "spacer"}),
    el("button", {class: "btn", type: "button", text: "Clear", onclick: () => { state.sel.clear(); keepFocus(renderLabs); }}),
    el("button", {class: "btn", type: "button", text: "Defaults", onclick: () => { state.sel = startLabs(); keepFocus(renderLabs); }}));
  $("#tvBox").replaceChildren(...[viewsBar()].filter(Boolean));
  if (!top.length) wrap.append(el("p", {class: "hint lab-none"}, `No lab names match “${$("#labQ").value.trim()}”. `,
    el("button", {class: "btn", type: "button", text: "Clear the filter", onclick: () => { clearLabFilter(); $("#labQ").focus(); }})));
  for (const {panel, labs} of groups) {
    let box = wrap;
    if (panel === LAB_OTHER && groups.length > 1) {
      box = el("details", {class: "grp-other", open: labOtherOpen || !!state.labQ || null, ontoggle: ev => { if (!state.labQ) labOtherOpen = ev.target.open; }},
        el("summary", {class: "grp lab-panel"}, el("h3", {text: panel}), el("span", {class: "grp-n"})));
      wrap.append(box);
    } else wrap.append(el("h3", {class: "grp lab-panel", text: panel}));   // the panels as headings, under the list's h2 (#441)
    for (const s of labs) {
      const L = latest(s), tq = testLink(s.name, true);   // #163
      const base = "s_" + s.name.replace(/\W/g, "_");   // a lab's second unit: s_Hemoglobin_2 (#438)
      let id = base;
      for (let k = 2; ids.has(id); k++) id = base + "_" + k;
      ids.add(id);
      // the name's and the latest value's tips: the checkbox's too, shown when it has focus, and its description (#358)
      const nmTip = `${renamedTitle("lab", s.name)} · ${s.n} results`, lvTip = L ? `Latest ${fmtD(L[0])}${ucOrig(s, L) ? " · reported " + ucOrig(s, L) : ""}` : "";
      box.append(el("label", {class: "srow" + (tq ? " has-tq" : ""), for: id},
        el("input", {type: "checkbox", id, "data-lab": s.name, checked: state.sel.has(s.name) || null, "aria-describedby": id + "_about", "data-tip": [nmTip, lvTip].filter(Boolean).join("\n"),
          onchange: ev => { ev.target.checked ? state.sel.add(s.name) : state.sel.delete(s.name);
            for (const c of wrap.querySelectorAll("input[data-lab]")) if (c.dataset.lab === s.name) c.checked = ev.target.checked;   // its other unit's row too (#438)
            renderCharts(); save(); $("#seriesSel b").textContent =`${state.sel.size} selected`; otherSays(); tvRefresh(); }}),
        el("span", {class: "nm", "data-tip": nmTip, text: nice(s.name)}), tq,
        el("span", {class: "lv" + (L && L[4] ? " abn" : ""), "data-tip": lvTip, text: L ? `${L[4] ? (L[4] === "H" ? "▲ " : "▼ ") : ""}${L[5] || fmtNum(L[1])} ${s.unit}` : ""}),
        el("span", {id: id + "_about", hidden: "", text: [nmTip, lvTip].filter(Boolean).join(". ")})));
    }
  }
  otherSays();
}
/* Other's count, with how many of its labs are charted, said on it while it's closed */
function otherSays() {
  const d = $("#seriesRows .grp-other"); if (!d) return;
  const n = d.querySelectorAll(".srow").length, on = d.querySelectorAll(".srow input:checked").length;
  d.querySelector(".grp-n").textContent = ` · ${n} lab${n === 1 ? "" : "s"}${on ? `, ${on} charted` : ""}`;
}
let hoverT = null;
/* the rows of the event track over the charts (event-track.js, #294), one per layer: {name, space() -> its height in px
   (0: nothing to show), draw(g, x, top, R)} draws it from y = top, x(t) being the charts' time scale, and rows() -> [[t,
   kind, words, open(), its kind's key for an icon]] gives its table's. meds.js, medlog.js, journal.js and cycle.js add
   theirs. */
const CHART_LAYERS = [];
function renderCharts() {
  const box = $("#charts"); box.replaceChildren();
  legendSync();   // the Legend's keys for what the charts draw (#250, #301)
  const sel = SERIES.filter(s => !s.watch && state.sel.has(s.name));
  if (!sel.length) { box.append(el("div", {class: "card empty", text: "Pick lab values in the list to chart them."})); return; }
  // in the list's order, under its panels' names (#441)
  const groups = labGroups(byFrequency(sel));
  for (const {panel, labs} of groups) {
    if (groups.length > 1) box.append(el("h3", {class: "lab-panel", text: panel}));
    for (const s of labs) box.append(chartCard(s));
  }
  const track = trackCard(); if (track) box.prepend(track);   // what happened in the period, over them, once (#294)
  if (box.querySelector("svg.ck")) labAxis(box);   // a chart drawn, not only tables: the stack's time axis under it
}
/* a lab chart's width in its own units: its card's, less the card's padding; the stack's axis takes the same (#293) */
const labW = card => Math.max(320, card.clientWidth - 28);
/* the stack's one time axis (#293): the kit's (chartkit.js ckAxis), drawn once under the charts on their time scale,
   and kept in view at the foot of the window while they scroll, as the heatmap's is. In a box padded as a card is,
   so it's as wide as every chart and lines up with them */
function labAxis(box) {
  const svg = el("svg:svg", {class: "ck-axis", "aria-hidden": "true"});
  box.append(el("div", {class: "card ck-foot"}, svg));
  requestAnimationFrame(() => labAxisDraw(svg));
}
function labAxisDraw(svg = $("#charts .ck-axis")) {
  if (!svg?.isConnected) return;
  const w = labW(svg.parentElement), L = 52, R = w - 12, x = ckScale(state.from, state.to, L, R), tk = ckTimeTicks(state.from, state.to, x, w), h = ckAxisH(tk.rows), g = [];
  ckAxis(g, tk, L, R, .5, 15);
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`); svg.setAttribute("height", h); svg._geom = {L, R, B: .5, w};
  svg.replaceChildren(...g);
}
const MARK_KINDS = new Set(["procedure", "imaging"]);
/* a procedure's or an imaging's mark on the event track over the charts (#294), in its kind's colour and shape (util.js KIND_SHAPE,
   #301): a plus and a square, never a ● like a result or a ▲ or ▼, which on the same chart say above or below range
   (#250). The Legend draws the same. */
const evHead = (k, cx, cy) => shapeMark(KIND_SHAPE[k], cx, cy, 3.7, {class: "evhead", "data-k": k, fill: CAT_OF[k].color});
/* the Legend's keys for what's drawn over or under the charts: each only while its kind is on them in these dates
   (#301), LEGEND_HAS[its data-legend]() saying so; journal.js and cycle.js add theirs */
const LEGEND_HAS = {procedure: () => markEvents().some(e => e.k === "procedure"), imaging: () => markEvents().some(e => e.k === "imaging")};
function legendSync() { for (const k of document.querySelectorAll("#view-labs .chart-legend [data-legend]")) k.hidden = !LEGEND_HAS[k.dataset.legend]?.(); }
{
  const legend = document.querySelector("#view-labs .chart-legend");
  const key = (k, words) => el("span", {class: "jlegend marks-key", "data-legend": k, hidden: true}, shapeKey(KIND_SHAPE[k], {fill: CAT_OF[k].color}), words);
  if (legend) legend.append(key("procedure", "Procedure"), key("imaging", "Imaging"));
  // the header's Legend and ⓘ (#279, index.html): each opens under its button as a menu does (nav.js), words, not items
  if ($("#legendBtn")) menuWire({btn: $("#legendBtn"), list: $("#legendList"), text: true});   // (the ⓘ: util.js aboutWire, #280)
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
  if (s.kind === "bars") return [`${Math.round(p[1]).toLocaleString("en-US")} ${u}`, ""];
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
  const card = el("div", {class: "chart card", "data-lab": s.name});   // the card (#353)
  const pts = s.pts.filter(p => p.ts >= state.from && p.ts <= state.to);
  const nAbn = pts.filter(p => p[4]).length;
  const read = el("span", {class: "read"});
  const showTable = state.tables.has(s.name);
  const hasStages = s.kind === "stack" && pts.some(p => p.parts && (p.parts.deep || p.parts.rem || p.parts.light));
  const legend = hasStages ? ckLegend([["var(--seq1)", "Deep"], ["var(--seq2)", "REM"], ["var(--seq3)", "Light"]], "span")
    : s.kind === "band" ? ckLegend([["var(--c-lab)", "Daily average", "line"], ["var(--c-lab)", "Low–high", "band"]], "span") : null;
  // its name in the records, when renamed, on the screen beside the unit, not in a tip only a mouse finds (#358)
  card.append(el("div", {class: "hd"}, s.watch ? el("span", {class: "name", text: nice(s.name)}) : labOpener(s, "name"), s.watch ? null : renameButton("lab", s.name),
    el("span", {class: "unit", text: `${s.unit}${!s.watch && nice(s.name) !== s.name ? " · " + renamedTitle("lab", s.name).replace(/^In/, "in") : ""}${s.uc ? ` (converted from ${s.uc.from})` : ""}${s.watch ? (s.key === "weight" ? " · scale + clinic visits" : s.panel.startsWith("Body") ? " · smart scale" : " · watch, daily") : ""} · ${pts.length} ${s.watch ? "day" : "result"}${pts.length === 1 ? "" : "s"}${nAbn ? ` · ${nAbn} outside reference range` : ""}${pts.some(p => p.rs === "settings") ? " · " + RANGE_FROM_SETTINGS + " for results without one" : ""}`}), testLink(s.name), legend, read,   // "What is this test?" (#163, test-pages.js)
    viewSeg(s.name, nice(s.name), renderCharts),   // Chart | Table (#60)
    el("button", {class: "btn quiet small", type: "button", "data-tip": `Remove ${nice(s.name)}`, "aria-label": `Remove ${nice(s.name)}`, onclick: () => {
      // focus to the next chart's ×, else to this lab's box in the list (or Other, closed, it's in), else to the
      // filter that hides it, not <body> (#462)
      const cs = [...$("#charts").querySelectorAll(".chart")], next = cs.slice(cs.indexOf(card) + 1).find(c => c.dataset.lab !== s.name)?.dataset.lab;
      state.sel.delete(s.name); renderLabs();
      const box = [...$("#seriesRows").querySelectorAll("input[data-lab]")].find(c => c.dataset.lab === s.name);
      ([...$("#charts").querySelectorAll(".chart")].find(c => c.dataset.lab === next)?.querySelector(".hd > [aria-label^=Remove]") || (box?.checkVisibility() ? box : box?.closest("details")?.querySelector("summary")) || $("#labQ")).focus(); }}, icon("x")),
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
  const svg = el("svg:svg", {role: "img", class: "pz ck", "aria-label": `${nice(s.name)} over time`});
  svg._s = s; svg._read = read;
  card.append(svg);
  requestAnimationFrame(() => drawChart(svg, s, pts, read));
  return card;
}
function drawChart(svg, s, pts, read, live) {   // live: a frame of a drag, at the width it had (#292)
  if (!svg.isConnected) return;
  // one chart of the stack (#293): 200–220 px tall, its unit over its numbers, and no time axis of its own: the
  // stack's one is under it (labAxis), each chart on the same time scale (the kit's, chartkit.js). Events are on the
  // track over the stack (#294): only a faint line at each milestone here
  const w = live && svg._geom ? svg._geom.w : labW(svg.parentElement), L = 52, R = w - 12, x = ckScale(state.from, state.to, L, R);
  const T = 22, B = T + 184, h = B + 6;
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`); svg.setAttribute("height", h); svg._geom = {L, R, T, B, w};
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
  const y = ckScale(lo, hi, B, T);
  const g = [];
  for (const st of STAYS) { if (st.te < state.from || st.ts > state.to) continue; const a = Math.max(L, x(st.ts)), b = Math.min(R, x(st.te)); g.push(el("svg:rect", {class: "stayband", x: a, y: T, width: Math.max(2, b - a), height: B - T})); }
  ckGrid(g, y, niceTicks(lo, hi, 5), L, R, v => s.kind === "bars" && v >= 1000 ? (v / 1000) + "k" : fmtNum(v));
  ckUnit(g, s.unit, T - 10);
  ckBand(g, steps, x, y, L, R, T, B);
  { const st = steps[steps.length - 1];   // the numbers of the range at the right end, as printed (#293)
    if (st) ckBandLabel(g, `${st.key} ${st.rs === "settings" ? "from settings" : "on report"}`, st, y, R, T, B, pts.map(p => [x(p.ts), y(p[1])])); }
  // a faint line at each milestone, named once on the event track over the charts (#294), and in the chart's words;
  // procedures and imaging, the medications and the rest are on the track only
  const msAt = g.length, msWords = msMarks(g, x, state.from, state.to, T, B, R, null);
  for (const m of g.slice(msAt)) m.classList.add("faint");
  ckFoot(g, L, R, B);
  const dayW = Math.max(1, x(state.from + DAY) - x(state.from));
  const barW = Math.max(1, Math.min(14, dayW * .72));
  // a line through [x, y]s, and bars and bands, decimated to what each pixel shows (chartkit.js ckDecimate, #292)
  const line = (arr, cls, extra = {}) => arr.length > 1 ? el("svg:path", {class: cls, d: ckDecimate(arr, q => q[0], 1, {ends: true}).map(([px, py], i) => (i ? "L" : "M") + px.toFixed(1) + "," + py.toFixed(1)).join(""), ...extra}) : null;
  const tallest = () => ckDecimate(pts, p => x(p.ts), 1, {lo: null});
  if (s.kind === "bars") {
    for (const p of tallest()) { const top = y(p[1]); g.push(el("svg:rect", {x: x(p.ts) - barW / 2, y: top, width: barW, height: Math.max(0, B - top), rx: Math.min(2, barW / 2), fill: "var(--c-lab)", "fill-opacity": smoothOn ? .45 : .85})); }
    if (smoothOn) g.push(line(smooth7(pts).map((v, i) => [x(pts[i].ts), y(v)]), "line", {stroke: "var(--ink2)"}));
  } else if (s.kind === "stack") {
    for (const p of tallest()) {
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
      const edges = ckDecimate(bp.map((p, i) => [x(p.ts), loS[i], hiS[i]]), q => q[0], 1, {lo: q => q[1], hi: q => q[2], ends: true});
      const top = edges.map(q => `${q[0].toFixed(1)},${y(q[2]).toFixed(1)}`), bot = edges.map(q => `${q[0].toFixed(1)},${y(q[1]).toFixed(1)}`).reverse();
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
  const cross = ckCross(T, B);
  const dot = el("svg:circle", {r: 5.5, fill: "none", stroke: "var(--ink)", "stroke-width": 2, visibility: "hidden", "pointer-events": "none"});
  const hit = el("svg:rect", {x: L, y: T - 12, width: R - L, height: B - T + 12, fill: "transparent"});
  g.push(cross, dot, hit);
  svg.replaceChildren(...g.filter(Boolean));
  const lastP = pts[pts.length - 1];
  const setRead = p => {
    read.replaceChildren();
    if (!p) { if (lastP) read.append("Latest ", el("b", {text: readText(s, lastP)[0].replace(` ${s.unit}`, "")}), ` · ${fmtD(lastP.ts)}`); return; }
    read.append(el("b", {text: readText(s, p)[0]}), p[4] ? el("span", {style: {color: "var(--crit)", fontWeight: "var(--fw-semibold)"}, text: " " + flagWord(p[4])}) : "", ` · ${s.watch ? fmtD(p.ts) : fmtDT(p.ts)}`);
  };
  setRead(null);
  svg._hover = t => {
    if (t == null || !pts.length) { ckCrossAt(cross, null); dot.setAttribute("visibility", "hidden"); setRead(null); return null; }
    let best = pts[0]; for (const p of pts) if (Math.abs(p.ts - t) < Math.abs(best.ts - t)) best = p;
    const tol = Math.max((state.to - state.from) * 0.03, s.watch ? DAY * .6 : 0);
    if (Math.abs(best.ts - t) > tol) { ckCrossAt(cross, null); dot.setAttribute("visibility", "hidden"); setRead(null); return null; }
    ckCrossAt(cross, x(best.ts));
    dot.setAttribute("cx", x(best.ts)); dot.setAttribute("cy", y(best[1])); dot.setAttribute("visibility", "visible");
    setRead(best); return best;
  };
  // what hovering at time t shows: every chart's crosshair and readout at that date (ckSync), this one's tooltip
  // pinned at the top of its crosshair (ckTip); the keyboard too (#60)
  const showAt = (t, px) => {
    const mine = ckSync(svg, t);
    const nodes = [];
    if (mine) {
      const [a, b] = readText(s, mine);
      nodes.push(el("div", {class: "v", text: a}), el("div", {class: "s"}, el("span", {class: "k", style: {background: "var(--c-lab)"}}), nice(s.name), nice(s.name) !== s.name ? el("span", {class: "muted", text: ` (${s.name})`}) : null), b ? el("div", {class: "s", text: b}) : null,
        el("div", {class: "s", text: (s.watch ? fmtD(mine.ts) : fmtDT(mine.ts)) + (mine[2] != null || mine[3] != null ? " · " + refText(mine) : "")}),
        mine[6] ? el("div", {class: "s", text: "Source: " + srcName(mine[6]) + (srcName(mine[6]) !== mine[6] ? ` (${mine[6]})` : "")}) : null,
        !s.watch && pointSources(s, mine) ? el("div", {class: "s", text: "From " + pointSources(s, mine)}) : null,
        mine[4] ? el("div", {class: "s", style: {color: "var(--crit)"}, text: /^h/i.test(mine[4]) ? "▲ Above range" : /^l/i.test(mine[4]) ? "▼ Below range" : "◆ Flagged"}) : null);
    }
    ckTip(svg, mine ? x(mine.ts) : px, nodes);
    return tipWords(nodes);
  };
  hit.addEventListener("pointermove", ev => {
    const r = svg.getBoundingClientRect(), px = (ev.clientX - r.left) * (w / r.width);
    showAt(x.inv(px), px);
  });
  // the keyboard (#60): ← and → step through the results shown, with the same readout and tooltip as hovering
  const unit = s.watch ? (s.kind === "stack" ? "night" : "day") : "result", nAbn = pts.filter(p => p[4]).length;
  chartKeys(svg, `${nice(s.name)}: ${pts.length} ${unit}${pts.length === 1 ? "" : "s"}${pts.length ? ` from ${fmtD(pts[0].ts)} to ${fmtD(lastP.ts)}, latest ${readText(s, lastP)[0]}` : " in this period"}`
    + `${nAbn ? `, ${nAbn} outside reference range` : ""}${rangesDiffer(pts) ? `. ${RANGE_DIFFERS}: ${rangesDiffer(pts)} ${s.unit}` : ""}. Left and right arrow keys step through them.${msWords ? " " + msWords : ""}`,
    () => pts.map(p => p.ts),
    t => showAt(t, x(t)), () => { ckSync(svg, null); hideTip(); });
  hit.addEventListener("pointerleave", () => { if (!chartsBox.classList.contains("dragging")) { ckSync(svg, null); hideTip(); } });
}
