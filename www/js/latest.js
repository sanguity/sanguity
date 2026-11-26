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
/* ---- New (#996): a lab's latest result added since this browser's first look at Latest for this profile (an upload,
   a sync, typed in: from an import newer than the newest then, seen; imports' ids only grow, server/db.py) that Latest
   hasn't shown since, its row drawn in view (#1027: what Latest last drew was taken as shown, a result outside the
   period, hidden by Flagged only or below the fold, or Latest only passed through on the way to a chart, never
   marked). Marked "New · today" (when its import came), counted in the heading. What's shown is kept with the filters
   (ltKeep's seen and shown, each result by ltKey) and read once a page, so a result stays marked while the page is
   open and not once it's loaded again after its row was in view; once every lab's latest since seen has been shown,
   seen moves on and the list starts again. Nothing is marked on a first look or with storage blocked. Never sent
   anywhere. Each result kept by its place, not what it says (#1070: kept as its import, time and value, values and
   times were left in the browser after sign-out; a hash of them could be guessed back): its import's id and which of
   that import's lab results it is, in the order the records list them (DATA.labs, a copy left out by same.js counted
   too), "12.3". One kept as written before is found among the results and kept by its place as it's read */
let ltBase;   // {seen, shown: Set} as this page found them: undefined till Latest is drawn, null on a first look (or blocked)
let ltPlaces = null;   // {labs: the DATA.labs they're of, at: point -> "imp.n", was: "imp|time|value" -> "imp.n"}
const ltPlace = () => {
  if (ltPlaces?.labs === DATA.labs) return ltPlaces;
  const at = new Map(), was = new Map(), n = new Map();
  for (const s of DATA.labs) if (!s.watch) for (const p of s.pts) {
    const k = n.get(s.imp) || 0; n.set(s.imp, k + 1);
    at.set(p, `${s.imp}.${k}`); was.set(`${s.imp}|${p[0]}|${p[1]}`, `${s.imp}.${k}`);
  }
  return ltPlaces = {labs: DATA.labs, at, was};
};
const ltKey = p => ltPlace().at.get(p) || `${p.imp}.`;
const ltShownKept = k => Array.isArray(k.shown) ? k.shown.map(x => String(x).includes("|") ? ltPlace().was.get(String(x)) : x).filter(Boolean) : [];
const ltIsNew = p => ltBase != null && p.imp > ltBase.seen && !ltBase.shown.has(ltKey(p));
const ltNewest = () => { let m = 0; for (const s of SERIES) if (!s.watch) for (const p of s.pts) if (p.imp > m) m = p.imp; return m; };
function ltRead() {
  const k = ltKept();
  if (Number.isInteger(k.seen)) ltBase = {seen: k.seen, shown: new Set(ltShownKept(k))};
  else { ltBase = null; ltKeep({seen: ltNewest(), shown: []}); }   // (a first look: what's here now isn't new)
}
/* results shown (their rows drawn in view): kept, and seen moved on once every lab's latest since it has been */
function ltShown(keys) {
  const k = ltKept();
  if (!Number.isInteger(k.seen)) return;
  const shown = new Set([...ltShownKept(k), ...keys]), live = new Set(), last = [];
  for (const s of SERIES) if (!s.watch && s.pts.length) {
    for (const p of s.pts) if (p.imp > k.seen) live.add(ltKey(p));
    for (const p of ckLast(s.pts)) if (p.imp > k.seen) last.push(ltKey(p));
  }
  ltKeep(last.every(x => shown.has(x)) ? {seen: Math.max(k.seen, ltNewest()), shown: []} : {shown: [...shown].filter(x => live.has(x))});
}
const ltInView = new IntersectionObserver(es => {
  const keys = es.filter(e => e.isIntersecting && e.target.isConnected && $("#labsLatest").checkVisibility()).flatMap(e => e.target._new);
  for (const e of es) if (e.isIntersecting) ltInView.unobserve(e.target);
  if (keys.length) ltShown(keys);
}, {threshold: .75});
const ltNew = p => { const at = sourceOf(p.imp)?.at;
  return el("span", {class: "badge"}, "New", at ? el("span", {"aria-hidden": "true", text: " · "}) : null, at ? el("span", {class: "vh", text: ", added "}) : null, at ? ltAge(at * 1000).words : null); };
/* a lab's row: [{s, pts: its results in the period, last: those at its latest time, the flagged first}] -> an <a>; its
   change since the result before (ltChange) and how long ago it was taken (ltAge), a year or more ago dimmed (.old, its
   words ink that keeps 4.5:1) and said "last taken 2 years ago"; marked New where its latest is (ltNew, #996), and
   watched till it's drawn in view (ltInView). Its ranges said once where two at one time have one (#1027) */
function ltRow({s, pts, last}, id) {
  const ranges = [...new Set(last.map(p => refText(p) || "no range"))].map((r, i, all) => r !== "no range" ? r
    : all.length === 1 && last.length > 1 ? "no range on their reports" : "no range on its report"), from = last.map(p => ucOrig(s, p)).filter(Boolean);
  const sep = () => el("span", {class: "vh", text: ", "}), age = ltAge(last[0].ts), chg = ltChange(pts, last);
  // at its charts' own address (#1026); opened in a new tab or window (the browser's), the lab is among those charted there
  const elsewhere = () => { state.sel.add(s.name); save(); };
  return el("a", {class: "ov-row lt-row" + (age.old ? " old" : ""), id, href: "#labs-chart", "data-lab": s.name, "data-unit": s.unit, onclick: ev => {
      if (ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey || ev.button) return elsewhere();
      ev.preventDefault(); ltOpen(s, id); }, onauxclick: ev => { if (ev.button === 1) elsewhere(); }},
    el("span", {class: "n"}, nice(s.name), ...(last.some(ltIsNew) ? [" ", ltNew(last.find(ltIsNew))] : [])), sep(),
    el("span", {class: "v"}, ...last.map((p, i) => { const f = FLAG_OF(p[4]);
      return el("span", {class: "lt-v"}, i ? el("span", {class: "vh", text: "; "}) : null, readText(s, p)[0],
        f ? el("span", {class: "st " + f.cls}, el("span", {"aria-hidden": "true", text: f.mark}), " " + f.word) : null); })), sep(),
    el("span", {class: "s", text: [ranges.join("; "), from.length ? "reported " + from.join("; ") : "", fmtD(last[0].ts), (age.old ? "last taken " : "") + age.words].filter(Boolean).join(" · ")}), sep(),
    el("span", {class: "c", "data-tip": chg.prev ? `Since ${fmtD(chg.prev.ts)}: ${readText(s, chg.prev)[0]}` : null}, chg.words, el("span", {class: "vh", text: " " + chg.said})));
}
const ltWatch = a => { a._new = a._last.filter(ltIsNew).map(ltKey); if (a._new.length) ltInView.observe(a); return a; };
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
  if (ltBase === undefined) ltRead();   // (#996)
  ltInView.disconnect();
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
  $("#ltQn").textContent = (k < n ? `${k || "None"} of ${n} lab${n === 1 ? "" : "s"}` : `${n} lab${n === 1 ? "" : "s"}`) + (f.flagged ? ", flagged only" : "");
  $("#ltNew").textContent = fresh ? ` · ${fresh} new` : "";   // in the heading (#1027)
  filterLine([f.flagged && "Showing flagged labs only", f.q && `${f.flagged ? "" : "Showing labs "}matching “${ltF.q.trim()}”`], ltReset, () => $("#ltQ"), $("#ltSum"));
  // (with none in the period, said once: #1027)
  $("#ltSays").textContent = !n ? `No lab has a result ${ltPeriod()}. A longer period shows the ${none === 1 ? "one lab" : `${none} labs`}.`
    : `Each lab's latest result ${ltPeriod()}.` + (none ? ` ${none} more ${none === 1 ? "has" : "have"} none in it.` : "");
  const by = new Map(shown.map(r => [r.s, r])), groups = labGroups(byFrequency(shown.map(r => r.s)));
  $("#ltRows").replaceChildren(...!n ? []
    : !k ? [el("p", {class: "hint", text: f.flagged && !f.q ? "No lab's latest result in this period is flagged." : "No lab matches these filters."})]
    : groups.flatMap(({panel, labs}) => [groups.length > 1 ? el("h3", {class: "lab-panel", text: panel}) : null,
      el("ul", {class: "lt-list"}, ...labs.map(s => el("li", {}, ltWatch(Object.assign(ltRow(by.get(s), ids.get(s)), {_last: by.get(s).last})))))].filter(Boolean)));
}
/* a row's lab charted on Labs, focus on its chart (charts.js showSeries), the address a new entry (#labs-chart), so Back
   returns to Latest: focus then on the row again, and Forward on the chart. Each kept with its own entry of the history
   (history.state's ltRow on Latest's, ltChart on the chart's; #1027: a row kept for the page came back on a later
   Back, after Latest was left another way, and a Back after Forward had none) */
function ltOpen(s, id) {
  try { history.replaceState({...history.state, ltRow: id}, ""); } catch (e) {}
  showSeries(s.name, s);
  try { history.replaceState({...history.state, ltChart: [s.name, s.unit]}, ""); } catch (e) {}
}
addEventListener("hashchange", () => requestAnimationFrame(() => {   // (after app.js has shown the view the address names)
  const h = history.state || {};
  if (state.tab !== "labs") return;
  if (state.labsView === "latest" && h.ltRow) document.getElementById(h.ltRow)?.focus();
  else if (state.labsView === "chart" && h.ltChart) {
    const c = [...$("#charts").querySelectorAll(".chart")].find(c => c._s?.name === h.ltChart[0] && c._s.unit === h.ltChart[1]);
    c?.querySelector(state.tables.has(h.ltChart[0]) ? ".hd .lab-open" : "svg.ck")?.focus();
  }
}));
