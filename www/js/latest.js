/* Sanguity · Labs' Latest (#993): each lab's latest result in the period, a row a lab, read by the one rule Labs reads
   them by: its value and unit as Labs shows them (readText: the units chosen, a converted value's as reported), its
   flag (ranges.js FLAG_OF: ▲ ▼ ◆ and their word), its range said as #752 says it (refText) and its day; two results at
   the latest time both said, as the chart's readout says them (chartkit.js ckLast, #904). A row is a link to its lab's
   chart: activating it charts the lab on Labs, focus on its chart, and Back returns to the row. It follows the period:
   a lab with no result in it isn't listed, and how many aren't is said. Grouped by panel, with a name filter and
   Flagged only (#994); each result's change since the one before and how long ago it was taken (#995); the results
   added since this browser last showed it marked New (#996). Plain script; all files share one scope. */
"use strict";
/* the period in the view's line: "in the last 2 years", "since diagnosis (…)", "on file" for All */
function ltPeriod() {
  const p = periodLabel("");
  return state.preset === "all" || !p ? "on file" : /^since /.test(p) ? p : /^(the |\d{4}$)/.test(p) ? "in " + p : "from " + p;
}
/* how long ago a result was taken (#995), by the days between its day and today in this person's time zone (dates.js
   isoDay, zToday): "today", "yesterday", "12 days ago", "6 weeks ago", "5 months ago", "2 years ago" (whole years by
   the calendar); old: a year or more */
function ltAge(t) {
  const day = isoDay(t), today = zToday(), n = Math.round((dayTs(today) - dayTs(day)) / DAY);
  const years = +today.slice(0, 4) - +day.slice(0, 4) - (today.slice(5) < day.slice(5) ? 1 : 0);
  return {old: years >= 1, words: n < 0 ? "dated ahead of today" : !n ? "today" : n === 1 ? "yesterday" : n < 14 ? `${n} days ago` : n < 61 ? `${Math.round(n / 7)} weeks ago`
    : years < 1 ? `${Math.floor(n / 30.44)} months ago` : `${years} year${years === 1 ? "" : "s"} ago`};
}
/* the change to each result at the latest time from the result before that time (#995), in its unit and to the places
   they're printed with, as a key lab's tile says it (overview.js ovPrev, util.js chgWords: "↑ 0.4", "↓ 12", "no
   change") and as the lab panel works out its change (lab-panel.js labDec, labSure: never from a value given as "<5",
   "not worked out"); "–" with no result before it in the period. Two at the latest time each have theirs, in the order
   they're said (#982's rule for the lab panel's First to latest: the change to each) */
function ltChange(pts, last) {
  const prev = pts.findLast(p => p.ts < last[0].ts);
  if (!prev) return {words: "–", said: "no result before it in this period"};
  const each = last.map(q => !labSure(prev) || !labSure(q) ? "change not worked out"
    : chgWords(q[1] - prev[1], Math.abs(q[1] - prev[1]).toFixed(Math.max(labDec(prev), labDec(q)))));
  return {words: each.join("; "), said: `since ${fmtD(prev.ts)}`, prev};
}
/* ---- New (#996): results added since this browser last showed Latest for this profile (an upload, a sync, typed in:
   from an import newer than the newest it had shown; imports' ids only grow, server/db.py), marked "New · today" (when
   its import came) and counted in the heading. The newest import shown is kept with the filters (ltKeep's seen), read
   once a page, so what's new stays marked while the page is open and not once it's loaded again; nothing is marked on
   a first look or with storage blocked. Never sent anywhere */
let ltSince;   // the newest import shown before this page showed Latest: undefined till it does, null for none kept
const ltIsNew = p => ltSince != null && p.imp > ltSince;
function ltSeen() {
  let m = 0;
  for (const s of SERIES) if (!s.watch) for (const p of s.pts) if (p.imp > m) m = p.imp;
  const was = ltKept().seen;
  if (!(m <= was)) ltKeep({seen: m});
}
const ltNew = p => { const at = sourceOf(p.imp)?.at;
  return el("span", {class: "badge"}, "New", at ? el("span", {"aria-hidden": "true", text: " · "}) : null, at ? el("span", {class: "vh", text: ", added "}) : null, at ? ltAge(at * 1000).words : null); };
/* a lab's row: [{s, pts: its results in the period, last: those at its latest time, the flagged first}] -> an <a>; its
   change since the result before (ltChange) and how long ago it was taken (ltAge), a year or more ago dimmed (.old, its
   words ink that keeps 4.5:1) and said "last taken 2 years ago"; marked New where its latest is (ltNew, #996) */
function ltRow({s, pts, last}, id) {
  const ranges = last.map(p => refText(p) || "no range on its report"), from = last.map(p => ucOrig(s, p)).filter(Boolean);
  const sep = () => el("span", {class: "vh", text: ", "}), age = ltAge(last[0].ts), chg = ltChange(pts, last);
  return el("a", {class: "ov-row lt-row" + (age.old ? " old" : ""), id, href: "#labs", "data-lab": s.name, "data-unit": s.unit, onclick: ev => {
      if (ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey || ev.button) return;   // a new tab or window: the browser's
      ev.preventDefault(); ltOpen(s, id); }},
    el("span", {class: "n"}, nice(s.name), ...(last.some(ltIsNew) ? [" ", ltNew(last.find(ltIsNew))] : [])), sep(),
    el("span", {class: "v"}, ...last.map((p, i) => { const f = FLAG_OF(p[4]);
      return el("span", {class: "lt-v"}, i ? el("span", {class: "vh", text: "; "}) : null, readText(s, p)[0],
        f ? el("span", {class: "st " + f.cls}, el("span", {"aria-hidden": "true", text: f.mark}), " " + f.word) : null); })), sep(),
    el("span", {class: "s", text: [ranges.join("; "), from.length ? "reported " + from.join("; ") : "", fmtD(last[0].ts), (age.old ? "last taken " : "") + age.words].filter(Boolean).join(" · ")}), sep(),
    el("span", {class: "c", "data-tip": chg.prev ? `Since ${fmtD(chg.prev.ts)}: ${readText(s, chg.prev)[0]}` : null}, chg.words, el("span", {class: "vh", text: " " + chg.said})));
}
/* ---- its filters (#994): a name and Flagged only, kept per profile in this browser (ht-latest:<profile>, every read
   and write guarded: with storage blocked they're kept for the page). ltKept(): what's kept, {} for nothing;
   ltKeep(part): part kept with it */
const LT_KEY = () => "ht-latest:" + (PROFILE ? PROFILE.id : "");
function ltKept() { try { const v = JSON.parse(localStorage.getItem(LT_KEY()) || "null"); return v && typeof v === "object" ? v : {}; } catch (e) { return {}; } }
function ltKeep(part) { try { localStorage.setItem(LT_KEY(), JSON.stringify({...ltKept(), ...part})); } catch (e) {} }
let ltF = null;   // {q: the name typed, flagged: Flagged only}, read once a profile's records are shown
const ltMatch = (s, q) => !q || [s.name, nice(s.name), s.panel || ""].some(t => t.toLowerCase().includes(q));   // as Labs' filter (trends.js labMatch)
const ltFlagged = r => r.last.some(p => FLAG_OF(p[4]));   // its latest flagged by the one rule (▲ ▼ ◆), either of two at that time
function ltFilters() {
  if (!ltF) { const k = ltKept(); ltF = {q: typeof k.q === "string" ? k.q : "", flagged: k.flagged === true}; $("#ltQ").value = ltF.q; }
  $("#ltFlag").checked = ltF.flagged;
  return {q: ltF.q.trim().toLowerCase(), flagged: ltF.flagged};
}
{
  let t;
  $("#ltQ").oninput = ev => {
    clearTimeout(t); t = setTimeout(() => {
      ltF.q = ev.target.value; ltKeep({q: ltF.q}); renderLatest();
      if (ltF.q.trim()) announce(`${$("#ltQn").textContent}.`);   // the count changes without moving focus (#209)
    }, 150);
  };
  $("#ltFlag").onchange = ev => { ltF.flagged = ev.target.checked; ltKeep({flagged: ltF.flagged}); renderLatest(); announce(`${$("#ltQn").textContent}.`); };
}
function ltReset() { ltF.q = ""; ltF.flagged = false; $("#ltQ").value = ""; ltKeep({q: "", flagged: false}); renderLatest(); }
/* the view, drawn for the period shown (charts.js renderLabs): its labs by panel, as everywhere they're grouped (util.js
   labGroups, #279), the most measured first in each (as Labs' list), Other last; its count in its heading ("12 of 60
   labs, flagged only") */
function renderLatest() {
  if (ltSince === undefined) { const v = ltKept().seen; ltSince = Number.isInteger(v) ? v : null; }   // (#996)
  const f = ltFilters(), rows = [], ids = new Map();
  let none = 0;
  for (const s of SERIES) {
    if (s.watch || !s.pts.length) continue;
    const base = "lt_" + s.name.replace(/\W/g, "_");   // its row's id, the same whatever's filtered: a lab in two units lt_Hemoglobin_2 (#438)
    let id = base;
    for (let k = 2; [...ids.values()].includes(id); k++) id = base + "_" + k;
    ids.set(s, id);
    const pts = s.pts.filter(p => p.ts >= state.from && p.ts <= state.to);
    if (pts.length) rows.push({s, pts, last: ckLast(pts)}); else none++;
  }
  const shown = rows.filter(r => ltMatch(r.s, f.q) && (!f.flagged || ltFlagged(r))), n = rows.length, k = shown.length;
  const fresh = shown.filter(r => r.last.some(ltIsNew)).length;
  $("#ltQn").textContent = (k < n ? `${k || "None"} of ${n} lab${n === 1 ? "" : "s"}` : `${n} lab${n === 1 ? "" : "s"}`) + (f.flagged ? ", flagged only" : "") + (fresh ? ` · ${fresh} new` : "");
  filterLine([f.flagged && "Showing flagged labs only", f.q && `${f.flagged ? "" : "Showing labs "}matching “${ltF.q.trim()}”`], ltReset, () => $("#ltQ"), $("#ltSum"));
  $("#ltSays").textContent = `Each lab's latest result ${ltPeriod()}.` + (none ? ` ${none} more ${none === 1 ? "has" : "have"} none in it.` : "");
  const by = new Map(shown.map(r => [r.s, r])), groups = labGroups(byFrequency(shown.map(r => r.s)));
  $("#ltRows").replaceChildren(...!n ? [el("p", {class: "hint", text: "No lab results in this period. A longer period shows them."})]
    : !k ? [el("p", {class: "hint", text: f.flagged && !f.q ? "No lab's latest result in this period is flagged." : "No lab matches these filters."})]
    : groups.flatMap(({panel, labs}) => [groups.length > 1 ? el("h3", {class: "lab-panel", text: panel}) : null,
      el("ul", {class: "lt-list"}, ...labs.map(s => el("li", {}, ltRow(by.get(s), ids.get(s)))))].filter(Boolean)));
  ltSeen();   // shown: what's new now isn't, the next time the page is loaded (#996)
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
