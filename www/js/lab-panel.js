/* Sanguity · a lab's panel (#296): its results in the period shown, as counts and positions, in the drawer. Opened from
   a lab's name (a chart's header on Labs, a row of the heatmap's table). What it says is what the lab's table says,
   counted: how many results, the first and the latest, the lowest and the highest with their days, the median, how
   many sat inside their own printed range (each result's own, as the chart's band) and the latest that did, and the
   change from the first to the latest; and a strip of every value against its band. Never a fitted line, a trend or a
   word for whether it's good: the records' own numbers only. Plain script; all files share one scope. */
"use strict";
/* the lab the drawer shows (openLab marks the drawer with it; drawerHead, drawing anything else there, takes the mark
   away), or null. A lab kept in two units is two series, told apart by the unit (#438) */
const labShown = () => drawer.classList.contains("open") && drawer.dataset.lab != null
  && SERIES.find(s => !s.watch && s.name === drawer.dataset.lab && s.unit === drawer.dataset.labUnit) || null;
/* a lab's name as the button that opens its panel: a chart's header's (cls "name"), a heatmap table row's; text, its
   words (the name, and the unit where the row gives it) */
const labOpener = (s, cls, text = nice(s.name)) => el("button", {class: `cell-btn lab-open ${cls}`.trim(), type: "button", text,
  "aria-haspopup": "dialog", onclick: () => openLab(s)});
/* where a result sits against its own range: "in", "above", "below", "flagged" (another flag), or "" (no range and no
   flag). The report's flag first, as the table and the chart's ▲/▼ show it; then the value against the range */
function labWhere(p) {
  const f = FLAG_OF(p[4]);
  if (f) return f[1] === "High" ? "above" : f[1] === "Low" ? "below" : "flagged";
  if (p[2] == null && p[3] == null) return "";
  return p[3] != null && p[1] > p[3] ? "above" : p[2] != null && p[1] < p[2] ? "below" : "in";
}
/* the decimals a result is printed with, so a median or a change is said to the same places ("14.8", "0.91") */
const labDec = p => (/\.(\d+)/.exec(String(p[5] || p[1]))?.[1].length) || 0;
/* a result given as the value measured, not as a bound ("<0.5", ">90"): a change is worked out only between two such,
   here and on the Overview's tiles (#718) */
const labSure = p => !/[<>≤≥]/.test(p[5] || "");
/* the results' counts and positions, from the results the lab's table lists for the period (pts, oldest first) */
function labStats(pts) {
  if (!pts.length) return null;
  const n = pts.length, first = pts[0], last = pts[n - 1], dec = Math.max(...pts.map(labDec));
  // the lowest and highest: the first day each was reached, and how many more times it was
  const ext = pick => { const v = pick(...pts.map(p => p[1])), at = pts.filter(p => p[1] === v); return {p: at[0], more: at.length - 1}; };
  // the median: the middle result as printed; between two, their mean to one more place where it needs it
  const by = [...pts].sort((a, b) => a[1] - b[1]), mid = by[(n - 1) >> 1];
  let median = mid[5] || fmtNum(mid[1]);
  if (n % 2 === 0) { const v = (mid[1] + by[n >> 1][1]) / 2, t = v.toFixed(dec + 1); median = t.endsWith("0") ? v.toFixed(dec) : t; }
  const where = {in: 0, above: 0, below: 0, flagged: 0, "": 0};
  for (const p of pts) where[labWhere(p)]++;
  // the change from the first to the latest, as printed; not worked out from a value given as "<0.5" or ">90"
  const d = last[1] - first[1], pct = first[1] ? Math.round(d / Math.abs(first[1]) * 100) : null;
  const signed = (v, t) => (v > 0 ? "+" : v < 0 ? "−" : "") + t;   // a minus sign, not a hyphen
  const change = n < 2 || !labSure(first) || !labSure(last) ? null
    : {text: signed(d, Math.abs(d).toFixed(Math.max(labDec(first), labDec(last)))), pct: pct == null ? "" : ` (${signed(pct, Math.abs(pct))}%)`};
  return {n, first, last, low: ext(Math.min), high: ext(Math.max), median, where, ranged: n - where[""], change,
    lastIn: [...pts].reverse().find(p => labWhere(p) === "in") || null, fromSettings: pts.filter(p => p.rs === "settings").length};
}
const labPct = (a, b) => b ? ` (${Math.round(a / b * 100)}%)` : "";
/* the period in the panel's line: "Results in the last 2 years", "Results since diagnosis (…)", "Every result on file" */
function labPeriod() {
  const p = periodLabel("");
  if (state.preset === "all" || !p) return "Every result on file";
  return /^since /.test(p) ? "Results " + p : /^(the |\d{4}$)/.test(p) ? "Results in " + p : "Results, " + p;
}
/* the lab's panel in the drawer (drawer.js), for the period shown */
function openLab(s) {
  if (!s) return;
  state.selEv = null;
  const pts = s.pts.filter(p => p.ts >= state.from && p.ts <= state.to), st = labStats(pts), u = s.unit;
  const body = drawerHead("labitem", nice(s.name), `${labPeriod()} · ${u}`);
  drawer.dataset.lab = s.name; drawer.dataset.labUnit = s.unit;   // (labShown)
  const val = p => readText(s, p)[0], at = p => `${val(p)} · ${fmtD(p.ts)}`;
  const row = (k, v, key) => v ? [el("dt", {text: k}), el("dd", {"data-k": key, text: v})] : [];
  if (!st) body.append(note("No results in this period. A longer period shows them."));
  else {
    const w = st.where, out = [["above", "▲", "above"], ["below", "▼", "below"], ["flagged", "◆", "flagged"]].filter(([k]) => w[k]);
    body.append(el("dl", {class: "facts lab-facts"}, ...[
      ["Results", String(st.n), "n"], ["Median", `${st.median} ${u}`, "median"],
      ["Inside own range", st.ranged ? `${w.in} of ${st.ranged}${labPct(w.in, st.ranged)}` : "None has a range", "in"],
      ["First to latest", st.change ? `${st.change.text} ${u}${st.change.pct}` : st.n < 2 ? "One result" : "Not worked out", "change"],
    ].map(([k, v, key]) => el("div", {}, el("dt", {text: k}), el("dd", {"data-k": key, text: v})))));
    body.append(el("div", {class: "sect", text: "Every result against its range"}), labStrip(s, pts));
    body.append(el("dl", {class: "kv lab-kv"},
      ...row("First", at(st.first), "first"), ...row("Latest", at(st.last), "latest"),
      ...row("Lowest", at(st.low.p) + (st.low.more ? `, and ${st.low.more} more time${st.low.more > 1 ? "s" : ""}` : ""), "low"),
      ...row("Highest", at(st.high.p) + (st.high.more ? `, and ${st.high.more} more time${st.high.more > 1 ? "s" : ""}` : ""), "high"),
      ...row("Latest inside its range", st.ranged ? (st.lastIn ? at(st.lastIn) : "None in this period") : "", "lastin"),
      ...row("Outside its range", st.ranged ? (out.length ? out.map(([k, m, word]) => `${m} ${w[k]} ${word}`).join(" · ") : "None") : "", "out"),
      ...row("No range on the report", w[""] ? `${w[""]} result${w[""] > 1 ? "s" : ""}` : "", "norange"),
      ...row("Reference range", rangesDiffer(pts) ? `${rangesDiffer(pts)} ${u}: each result's own` : st.ranged && pts.some(rangeWords) ? `${rangeWords(pts.find(rangeWords))} ${u}` : "", "range"),
      ...row("Range from settings", st.fromSettings ? `${st.fromSettings} result${st.fromSettings > 1 ? "s" : ""}, whose report gives none` : "", "settings"),
      ...row("In the records", nice(s.name) !== s.name ? s.name : "", "name")));
  }
  // to the lab's chart or table on Labs, unless that's what's beside the panel already, the drawer docked (#755: over
  // the page, it's under the scrim, out of reach)
  const onLabs = drawerDocks() && state.tab === "labs" && state.labsView === "chart" && state.sel.has(s.name), table = state.tables.has(s.name);
  const go = (asTable) => { if (asTable) state.tables.add(s.name); else state.tables.delete(s.name); showSeries(s.name); };
  body.append(el("div", {class: "btns dgo"}, onLabs && !table ? null : goBtn("Chart these results", () => go(false)),
    onLabs && table ? null : goBtn("Show them as a table", () => go(true))),
    el("p", {class: "hint dnote", text: "Counts and positions from the lab's table for this period. This isn't medical advice."}),
    aboutWords({title: "How these are counted"}, "Every result the lab's table lists for the period, against each result's own reference range on its report (or one you set in Settings, where the report gives none), the range the chart's band steps with.",
      "A result counts as outside its range when its report flags it, or when its value is past the range; the median is the middle result, or the mean of the two in the middle. The change is the latest result less the first, as printed; it doesn't say how the results in between went."));
  showDrawer();
}
/* the panel drawn again as the period or the records change (app.js renderAll), in place: its scroll and focus kept */
function labAgain() { const s = labShown(); if (s) drawerKeep(() => openLab(s)); }
/* the strip: every result in the period as a mark over time, against the reference band of its own range (the chart's
   stepped band, ranges.js), ▲/▼/◆ for one outside it; no line between them. A picture of what the facts above say;
   its marks stepped through from the keyboard, each said, as a chart's are (charts.js chartKeys) */
function labStrip(s, pts) {
  const svg = el("svg:svg", {class: "lab-strip", height: 96});
  requestAnimationFrame(() => labStripDraw(svg, s, pts));
  return svg;
}
function labStripDraw(svg, s, pts) {
  if (!svg.isConnected) return;
  const w = Math.max(200, svg.parentElement.clientWidth), h = 96, L = 6, R = w - 6, T = 8, B = h - 20;
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`); svg._geom = {w};
  const x = t => L + (t - state.from) / (state.to - state.from) * (R - L);
  const steps = rangeSteps(pts, state.from, state.to);
  const vs = pts.map(p => p[1]).concat(steps.flatMap(st => [st.lo, st.hi]).filter(v => v != null));
  let lo = Math.min(...vs), hi = Math.max(...vs);
  if (lo === hi) { lo -= Math.abs(lo) * .1 || 1; hi += Math.abs(hi) * .1 || 1; }
  const pad = (hi - lo) * .12; lo -= pad; hi += pad;
  const y = v => B - (v - lo) / (hi - lo) * (B - T), g = [];
  for (const st of steps) {   // each step of the band, its printed edges solid hairlines
    const a = Math.max(L, x(st.a)), b = Math.min(R, x(st.b)); if (b <= a) continue;
    const top = st.hi != null ? y(st.hi) : T, bot = st.lo != null ? y(st.lo) : B;
    g.push(el("svg:rect", {class: "refband", x: a, y: top, width: b - a, height: bot - top}));
    for (const k of ["lo", "hi"]) if (st[k] != null) g.push(el("svg:line", {class: "refedge", x1: a, x2: b, y1: y(st[k]), y2: y(st[k])}));
  }
  g.push(el("svg:line", {x1: L, x2: R, y1: B, y2: B, stroke: "var(--axis)"}),
    el("svg:text", {x: L, y: h - 5, text: fmtD(state.from)}), el("svg:text", {x: R, y: h - 5, "text-anchor": "end", text: fmtD(state.to)}));
  for (const p of pts) g.push(p[4] ? abnMark(x(p.ts), y(p[1]), p[4]) : el("svg:circle", {class: "pt", cx: x(p.ts), cy: y(p[1]), r: 4}));
  const ring = el("svg:circle", {r: 7, fill: "none", stroke: "var(--ink)", "stroke-width": 2, visibility: "hidden", "pointer-events": "none"});
  g.push(ring);
  svg.replaceChildren(...g);
  const show = (p, ev) => {
    if (!p) { ring.setAttribute("visibility", "hidden"); hideTip(); return ""; }
    ring.setAttribute("cx", x(p.ts)); ring.setAttribute("cy", y(p[1])); ring.setAttribute("visibility", "visible");
    const f = flagWord(p[4]), nodes = [el("div", {class: "v", text: readText(s, p)[0]}), el("div", {class: "s", text: fmtDT(p.ts)}),
      rangeWords(p) ? el("div", {class: "s", text: refText(p)}) : null, f ? el("div", {class: "s abn", text: f}) : null];
    showTip(nodes, ev || svgPoint(svg, x(p.ts), y(p[1])));
    return tipWords(nodes);
  };
  svg.onpointermove = ev => {
    const r = svg.getBoundingClientRect(), px = (ev.clientX - r.left) * (w / r.width);
    let best = null; for (const p of pts) if (!best || Math.abs(x(p.ts) - px) < Math.abs(x(best.ts) - px)) best = p;
    show(best && Math.abs(x(best.ts) - px) < 12 ? best : null, ev);
  };
  svg.onpointerleave = () => show(null);
  const out = pts.filter(p => labWhere(p) && labWhere(p) !== "in").length;
  chartKeys(svg, `${nice(s.name)}: ${pts.length} result${pts.length === 1 ? "" : "s"} against ${rangesDiffer(pts) ? "each one's own" : "its"} reference range, ${out} outside it. Left and right arrow keys step through them.`,
    () => pts.map(p => p.ts), t => show(pts.find(p => p.ts === t)), () => show(null));
}
