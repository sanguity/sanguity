/* Sanguity · Labs' Latest (#993): each lab's latest result in the period, a row a lab, read by the one rule Labs reads
   them by: its value and unit as Labs shows them (readText: the units chosen, a converted value's as reported), its
   flag (ranges.js FLAG_OF: ▲ ▼ ◆ and their word), its range said as #752 says it (refText) and its day; two results at
   the latest time both said, as the chart's readout says them (chartkit.js ckLast, #904). A row is a link to its lab's
   chart: activating it charts the lab on Labs, focus on its chart, and Back returns to the row. It follows the period:
   a lab with no result in it isn't listed, and how many aren't is said. Plain script; all files share one scope. */
"use strict";
/* the period in the view's line: "in the last 2 years", "since diagnosis (…)", "on file" for All */
function ltPeriod() {
  const p = periodLabel("");
  return state.preset === "all" || !p ? "on file" : /^since /.test(p) ? p : /^(the |\d{4}$)/.test(p) ? "in " + p : "from " + p;
}
/* a lab's row: [{s, pts: its results in the period, last: those at its latest time, the flagged first}] -> an <a> */
function ltRow({s, last}, id) {
  const ranges = last.map(p => refText(p) || "no range on its report"), from = last.map(p => ucOrig(s, p)).filter(Boolean);
  const sep = () => el("span", {class: "vh", text: ", "});
  return el("a", {class: "ov-row lt-row", id, href: "#labs", "data-lab": s.name, "data-unit": s.unit, onclick: ev => {
      if (ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey || ev.button) return;   // a new tab or window: the browser's
      ev.preventDefault(); ltOpen(s, id); }},
    el("span", {class: "n", text: nice(s.name)}), sep(),
    el("span", {class: "v"}, ...last.map((p, i) => { const f = FLAG_OF(p[4]);
      return el("span", {class: "lt-v"}, i ? el("span", {class: "vh", text: "; "}) : null, readText(s, p)[0],
        f ? el("span", {class: "st " + f.cls}, el("span", {"aria-hidden": "true", text: f.mark}), " " + f.word) : null); })), sep(),
    el("span", {class: "s", text: [ranges.join("; "), from.length ? "reported " + from.join("; ") : "", fmtD(last[0].ts)].filter(Boolean).join(" · ")}));
}
/* the view, drawn for the period shown (charts.js renderLabs) */
function renderLatest() {
  const rows = [], ids = new Set();
  let none = 0;
  for (const s of SERIES) {
    if (s.watch || !s.pts.length) continue;
    const pts = s.pts.filter(p => p.ts >= state.from && p.ts <= state.to);
    if (pts.length) rows.push({s, pts, last: ckLast(pts)}); else none++;
  }
  const n = rows.length;
  $("#ltSays").textContent = `Each lab's latest result ${ltPeriod()}: ${n} lab${n === 1 ? "" : "s"}.` + (none ? ` ${none} more ${none === 1 ? "has" : "have"} none in it.` : "");
  const by = new Map(rows.map(r => [r.s, r]));
  $("#ltRows").replaceChildren(n ? el("ul", {class: "lt-list"}, ...byFrequency(rows.map(r => r.s)).map(s => {
    const base = "lt_" + s.name.replace(/\W/g, "_");   // a lab in two units: lt_Hemoglobin_2 (#438)
    let id = base;
    for (let k = 2; ids.has(id); k++) id = base + "_" + k;
    ids.add(id);
    return el("li", {}, ltRow(by.get(s), id));
  })) : el("p", {class: "hint", text: "No lab results in this period. A longer period shows them."}));
}
/* a row's lab charted on Labs, focus on its chart (charts.js showSeries), the address a new entry (#labs), so Back
   returns to Latest: focus then on the row again (ltBack, as the address changes back, below) */
let ltBack = null;
function ltOpen(s, id) { ltBack = id; showSeries(s.name, s); }
addEventListener("hashchange", () => {
  if (!ltBack) return;
  requestAnimationFrame(() => {   // (after app.js has shown the view the address names)
    const a = state.tab === "labs" && state.labsView === "latest" && document.getElementById(ltBack);
    if (!a) return;
    ltBack = null; a.focus();
  });
});
