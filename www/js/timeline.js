/* Sanguity · Timeline tab: lane chart with zoom and pan, its events by day, and the switch to the journal (#277).
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
/* ---------------------------------------------------------------- timeline */
const tlSvg = $("#tl");
function tlScale(w) { const L = w < 600 ? 96 : 150, R = w - (w < 600 ? 6 : 16); return {L, R, x: t => L + (t - state.from) / (state.to - state.from) * (R - L), t: x => state.from + (x - L) / (R - L) * (state.to - state.from)}; }
function ticks(a, b, w) {
  const span = b - a, out = [], target = Math.max(3, Math.floor(w / 110));
  const steps = [[DAY,"d"],[7*DAY,"w"],[30.44*DAY,"m"],[91.3*DAY,"q"],[182.6*DAY,"h"],[365.25*DAY,"y"],[730.5*DAY,"2y"]];
  let st = steps.find(s => span / s[0] <= target) || steps[steps.length - 1];
  const d = zdate(a);
  if (st[1] === "d" || st[1] === "w") { d.setHours(0,0,0,0); const inc = st[1] === "d" ? 1 : 7; while (d.getTime() < b) { if (d.getTime() >= a) out.push([d.getTime(), d.toLocaleDateString(undefined,{month:"short",day:"numeric"})]); d.setDate(d.getDate() + inc); } }
  else { const mStep = {m:1,q:3,h:6,y:12,"2y":24}[st[1]]; d.setDate(1); d.setHours(0,0,0,0); d.setMonth(Math.floor(d.getMonth()/mStep)*mStep);
    while (d.getTime() < b) { if (d.getTime() >= a) out.push([d.getTime(), mStep >= 12 ? String(d.getFullYear()) : (d.getMonth() === 0 ? d.toLocaleDateString(undefined,{month:"short",year:"numeric"}) : d.toLocaleDateString(undefined,{month:"short"}))]); d.setMonth(d.getMonth() + mStep); } }
  return out;
}
/* lanes that draw themselves: {k: {height(w) -> px (0: draw the lane's events as dots), draw(g, S, top, h, cat, w)}}.
   meds.js adds the Medications lane (#50): one row of spans per medication */
const TL_LANES = {};
function renderTimeline() {
  tlAbnDays();
  const w = Math.max(320, tlSvg.parentElement.clientWidth - 32);
  const lanes = CATS.filter(c => c.k !== "stay" && state.cats.has(c.k) && hasLane(c));
  const laneH = 34, top = msInRange(state.from, state.to) ? 22 : 8, axisH = 26;
  const own = lanes.map(c => TL_LANES[c.k]?.height(w) || 0), laneHs = own.map(n => n || laneH), laneTop = laneHs.map((_, i) => laneHs.slice(0, i).reduce((a, b) => a + b, 0));
  const h = top + (laneHs.reduce((a, b) => a + b, 0) || laneH) + axisH;
  tlSvg.setAttribute("viewBox", `0 0 ${w} ${h}`); tlSvg.setAttribute("height", h);
  const S = tlScale(w), g = [];
  const plotB = top + laneHs.reduce((a, b) => a + b, 0);
  for (const [t, label] of ticks(state.from, state.to, S.R - S.L)) {
    const x = S.x(t);
    g.push(el("svg:line", {class: "gridline", x1: x, x2: x, y1: top, y2: plotB}));
    g.push(el("svg:text", {x, y: plotB + 16, "text-anchor": "middle", text: label}));
  }
  g.push(el("svg:line", {x1: S.L, x2: S.R, y1: plotB, y2: plotB, stroke: "var(--axis)"}));
  msMarks(g, S.x, state.from, state.to, top > 8 ? 17 : top, plotB, S.R, 13);
  if (state.cats.has("stay")) for (const s of STAYS) {
    if (s.te < state.from || s.ts > state.to) continue;
    const x1 = Math.max(S.L, S.x(s.ts)), x2 = Math.min(S.R, S.x(s.te));
    g.push(el("svg:rect", {class: "ev", x: x1, y: top, width: Math.max(3, x2 - x1), height: plotB - top, fill: "var(--stay)", "data-id": s.id, rx: 2}));
  }
  lanes.forEach((c, i) => {
    if (own[i]) { TL_LANES[c.k].draw(g, S, top + laneTop[i], laneHs[i], c, w); return; }
    const y = top + laneTop[i] + laneH / 2;
    g.push(el("svg:text", {class: "lane-label", x: 0, y: y + 4, text: w < 600 ? c.label.replace(" & studies", "").replace(" & docs", "") : c.label}));
    g.push(el("svg:line", {class: "gridline", x1: S.L, x2: S.R, y1: y + laneH/2, y2: y + laneH/2, "stroke-opacity": .5}));
    if (c.k === "watch" && STEPS.length) {
      const max = Math.max(...STEPS.map(s => s[1])) || 1, base = y + laneH / 2 - 3;
      for (const [ts, v] of STEPS) {
        if (ts + DAY < state.from || ts > state.to) continue;
        const x0 = S.x(ts), x1 = S.x(ts + DAY), hh = Math.max(1, (laneH - 10) * v / max);
        if (x1 < S.L || x0 > S.R) continue;
        g.push(el("svg:rect", {x: Math.max(S.L, x0), y: base - hh, width: Math.max(1, Math.min(S.R, x1) - Math.max(S.L, x0) - (x1 - x0 > 4 ? 1 : 0)), height: hh,
          fill: "var(--muted)", "fill-opacity": .45, "data-steps": v, "data-day": ts}));
      }
    }
    const evs = EV.filter(e => !e.hidden && CAT_OF[e.k] === c && inRange(e) && matchQ(e));
    for (const e of evs) {
      const x = S.x(e.ts);
      if (e.k === "journal") { g.push(journalMark(e, x, y, state.selEv === e.id)); continue; }   // shape by kind, size by severity (journal.js)
      if (e.end && e.te - e.ts > DAY && S.x(e.te) - x > 4) {
        g.push(el("svg:line", {x1: x, x2: Math.min(S.R, S.x(e.te)), y1: y, y2: y, stroke: c.color, "stroke-width": 2, "stroke-opacity": .45, "stroke-linecap": "round", "pointer-events": "none"}));
      }
      const r = e.k === "lab" ? Math.min(9, 3.5 + Math.sqrt(e.n || 1) * .9) : e.k === "auto_msg" || e.k === "appt_msg" ? 3.5 : 5;
      const hollow = e.k === "appt_msg" || e.k === "auto_msg" || e.k === "rhythm";   // #182: a notification, beside the recordings' dots
      const fill = hollow ? "var(--surface)" : c.color;
      g.push(el("svg:circle", {class: "ev" + (state.selEv === e.id ? " sel" : ""), cx: x, cy: y, r, fill, stroke: hollow ? c.color : "var(--surface)", "stroke-width": e.k === "rhythm" ? 2 : 1.5, "data-id": e.id}));
      if (e.k === "lab" && tlAbn(e)) {   // a ▲ above the day, known by its shape and not only its red (#250); a search's tests' (#464)
        const m = abnMark(x, y - r - 4.4, "H", 3.4, "tl-abn"); m.setAttribute("fill", "var(--crit)"); m.setAttribute("pointer-events", "none"); g.push(m);
      }
    }
  });
  if (!lanes.length) g.push(el("svg:text", {x: w/2, y: top + 20, "text-anchor": "middle", text: "No categories selected"}));
  tlSvg.replaceChildren(...g);
  // its table alternative is the list under it (#60): the same events, filters and period, each with its date, kind and title
  tlSvg.setAttribute("aria-label", `Timeline of medical events, ${fmtD(state.from)} to ${fmtD(state.to)}. The events it shows are listed under Events, below (its workouts with List workouts).`);
}
/* zoom & pan */
let drag = null;
tlSvg.addEventListener("wheel", ev => {
  ev.preventDefault();
  const r = tlSvg.getBoundingClientRect(), S = tlScale(r.width), t = S.t(ev.clientX - r.left);
  const k = Math.exp((ev.deltaY || ev.deltaX) * 0.0015);
  setRange(t - (t - state.from) * k, t + (state.to - t) * k);
}, {passive: false});
const ptrs = new Map(); let pinch = null;
tlSvg.addEventListener("pointerdown", ev => {
  ptrs.set(ev.pointerId, ev.clientX);
  if (ptrs.size === 2) { const [a, b] = [...ptrs.values()], r = tlSvg.getBoundingClientRect(), S = tlScale(r.width); pinch = {d: Math.abs(a - b) || 1, t: S.t((a + b) / 2 - r.left), from: state.from, to: state.to}; drag = null; return; }
  // on: the mark pressed, since with the pointer captured pointerup's target is the svg itself
  drag = {x: ev.clientX, from: state.from, to: state.to, moved: false, on: ev.target}; tlSvg.setPointerCapture(ev.pointerId);
});
tlSvg.addEventListener("pointercancel", ev => { ptrs.delete(ev.pointerId); pinch = null; drag = null; tlSvg.classList.remove("dragging"); });
tlSvg.addEventListener("pointermove", ev => {
  if (ptrs.has(ev.pointerId)) ptrs.set(ev.pointerId, ev.clientX);
  if (pinch && ptrs.size === 2) {
    const [a, b] = [...ptrs.values()], k = pinch.d / (Math.abs(a - b) || 1);
    state.from = pinch.t - (pinch.t - pinch.from) * k; state.to = pinch.t + (pinch.to - pinch.t) * k; state.preset = "custom"; hideTip(); renderTimeline(); return;
  }
  if (drag) {
    const dx = ev.clientX - drag.x; if (Math.abs(dx) > 3) { drag.moved = true; tlSvg.classList.add("dragging"); hideTip(); }
    if (drag.moved) { const r = tlSvg.getBoundingClientRect(), S = tlScale(r.width), dxv = 0, dt = dx / (S.R - S.L) * (drag.to - drag.from); state.from = drag.from - dt; state.to = drag.to - dt; state.preset = "custom"; renderTimeline(); }
    return;
  }
  const ds = ev.target?.dataset || {};
  if (ds.steps) { showTip([el("div", {class: "v", text: `${(+ds.steps).toLocaleString()} steps`}), el("div", {class: "s", text: fmtD(+ds.day) + " · from watch"})], ev); return; }
  if (ds.med != null) { medTip(MEDS[+ds.med], ds, ev); return; }   // a medication's span or fill (meds.js)
  const id = +ds.id; if (id) showEvTip(byId.get(id), ev); else hideTip();
});
tlSvg.addEventListener("pointerup", ev => {
  ptrs.delete(ev.pointerId);
  if (pinch) { if (ptrs.size < 2) { pinch = null; drag = null; renderAll(); } return; }
  tlSvg.classList.remove("dragging");
  if (drag && drag.moved) { drag = null; renderAll(); return; }
  const ds = (drag && drag.on || ev.target)?.dataset || {}; drag = null;
  const id = +ds.id; if (id) openEvent(byId.get(id));
  else if (ds.med != null) openMedication(MEDS[+ds.med]);   // a medication's span (meds.js)
});
tlSvg.addEventListener("pointerleave", hideTip);
tlSvg.addEventListener("dblclick", () => applyPreset("all"));
let lastW = innerWidth;
addEventListener("resize", () => { if (innerWidth === lastW) return; lastW = innerWidth; clearTimeout(window._rz); window._rz = setTimeout(() => keepFocus(renderAll), 120); });   // focus kept (store.js, #271)

/* ---------------------------------------------------------------- the tab: the timeline, or the journal (#277)
   The chart and its Events card, or the journal's card (journal.js, in #tlJournal), switched as Labs' Chart | Heatmap
   is, the view in the address (#timeline-journal, app.js) and kept (state.tlView). Someone who can only read a journal
   no one has written in has no journal card: no switch, and the timeline. */
function renderTimelineTab() {
  const has = !!$("#journalHome");
  if (!has) state.tlView = "timeline";
  const j = state.tlView === "journal";
  $(".tl-head").hidden = !has;
  for (const b of $("#tlView").children) b.setAttribute("aria-pressed", b.dataset.view === state.tlView);
  $("#tlMain").hidden = j; $("#tlJournal").hidden = !j;
  if (j) jHomeDraw?.();   // "Only the dates shown" and the body map follow the period
  else { renderTimeline(); renderList(); }
}
$("#tlView").onclick = ev => {
  const b = ev.target.closest("button[data-view]"); if (!b || b.dataset.view === state.tlView) return;
  hideTip(); state.tlView = b.dataset.view; renderTimelineTab(); save(); tabAddress(true);   // #timeline or #timeline-journal (#208), Back returning to the view before (#442)
};

/* ---------------------------------------------------------------- the Events card (#277)
   The chart's table alternative (#60): its events, with the same period, kinds and search (the kinds and the search in
   the card's header, the Filters sheet on a phone: core.js), grouped by day under month headings. A day's date is said
   once, short ("31 Fri"), and each row has its whole date for screen readers (.d); the kind is a small label beside the
   title. Workouts are most of what a watch records, so they're left out of the list (their lane stays) unless "List
   workouts" is on (state.tlWork) or Watch is the only kind shown; listed, a day's workouts fold into one row ("3 workouts
   · 2 h 10 m") that opens to each. TL_PAGE events are drawn at a time, whole days, with Show more for the next, and a
   month off screen is skipped by the browser (content-visibility, app.css): ten years list everything, within the
   budgets (docs/performance.md). One line over the list counts what the kinds' chips don't: the lab results, those out
   of range, and the workouts. */
let TL_PAGE = 400;   // events drawn at a time (the browser check draws fewer, to see Show more on the demo)
const isWork = e => e.k === "exercise";
const tlN = (n, one, many = one + "s") => `${n.toLocaleString()} ${n === 1 ? one : many}`;
const tlMins = m => (m < 60 ? `${m} min` : `${Math.floor(m / 60)} h${m % 60 ? ` ${m % 60} m` : ""}`).replace(/ /g, "\u00a0");   // kept on one line
/* Watch the only kind shown: its workouts are what's listed, so List workouts reads on and can't be put off (#444) */
const tlWatchOnly = () => state.cats.has("watch") && CATS.filter(hasLane).every(c => c.k === "watch" || !state.cats.has(c.k));
const tlWorkListed = () => state.tlWork || tlWatchOnly();
/* the events the list holds, in its order */
const tlListed = (all = EV.filter(visible), listed = tlWorkListed()) => all.filter(e => listed || !isWork(e)).sort((a, b) => state.asc ? a.ts - b.ts : b.ts - a.ts);
$("#sortSeg").onclick = ev => { const b = ev.target.closest("button[data-sort]"); if (!b) return; state.asc = b.dataset.sort === "asc"; renderList(); save(); };
$("#tlWork").onclick = ev => { if (ev.target.getAttribute("aria-disabled") === "true") ev.preventDefault(); };   // (focusable still, its tip saying why)
$("#tlWork").onchange = ev => { state.tlWork = ev.target.checked; renderList(); save(); };
/* the results of these lab draws, as Labs counts them (#451): the values on their days, and those out of range (e.n
   counts reports, so "100 results, 67 out of range" read as 67 of 100 when it was 67 of 442 values); Labs' Vitals,
   readings taken at visits, aren't a draw's; with tests (a Set of series), theirs only. -> [n, abn] */
function tlLabValues(labs, tests = null) {
  const from = [...new Set(labs.map(e => isoDay(e.ts)))].map(d => zParse(d + "T00:00:00")).sort((a, b) => a - b);
  const to = from.map(t => zParse(isoDay(t + 1.5 * DAY) + "T00:00:00"));   // the next day's start
  let n = 0, abn = 0;
  if (from.length) for (const s of SERIES) if (!s.watch && s.panel !== "Vitals" && (!tests || tests.has(s))) for (const p of s.pts) {   // (a visit's vitals aren't a draw's)
    let lo = 0, hi = from.length - 1;   // the last day that starts at or before it
    while (lo < hi) { const m = (lo + hi + 1) >> 1; if (from[m] <= p.ts) lo = m; else hi = m - 1; }
    if (from[lo] <= p.ts && p.ts < to[lo]) { n++; if (p[4]) abn++; }
  }
  return [n, abn];
}
/* the tests a search names (#459): the lab series whose name, the name Labs shows it by, or panel has what was typed,
   as a lab day's search words do (store.js labDayWords); none with no search */
const tlTestsHit = () => !state.q ? [] : SERIES.filter(s => !s.watch && s.panel !== "Vitals"
  && [s.name, nice(s.name), s.panel || "", panelName(s.panel || "")].some(t => t.toLowerCase().includes(state.q)));
/* a lab draw's out-of-range count: under a search that names tests, those tests' on its day, as the line over the list
   counts them (#464: "psa" showed "2 out of range" and a ▲ on draws whose PSA was in range, from their other tests);
   else all its values'. tlAbnDays works out the days' counts for a redraw (null: no search names tests) */
let tlAbnDay = null;
function tlAbnDays() {
  const tests = tlTestsHit();
  tlAbnDay = tests.length ? new Map() : null;
  for (const s of tests) for (const p of s.pts) if (p[4]) { const d = isoDay(p.ts); tlAbnDay.set(d, (tlAbnDay.get(d) || 0) + 1); }
}
const tlAbn = e => e.k === "lab" && tlAbnDay ? tlAbnDay.get(isoDay(e.ts)) || 0 : e.abn;
/* the line over the list: "The last year: 9 lab draws, 31 results, 3 out of range · 97 workouts, not listed". Under a
   search that names tests, the draws' results are those tests' only ("23 lab draws, 23 PSA results": not the 598 values
   of the draws that have a PSA, 8 of them out of range, read as PSA's), and it says which */
function tlSum(v, listed) {
  const labs = v.filter(e => e.k === "lab"), tests = tlTestsHit(), [res, abn] = tlLabValues(labs, tests.length ? new Set(tests) : null);
  const work = v.filter(isWork).length, parts = [], when = periodLabel(), names = [...new Set(tests.map(s => nice(s.name)))];
  const what = !names.length ? tlN(res, "result") : names.length === 1 ? tlN(res, `${names[0]} result`)
    : `${tlN(res, "result")} of the ${names.length} tests matching “${timelineFilters().q || state.q}”`;
  if (labs.length) parts.push(`${tlN(labs.length, "lab draw")}, ${what}${abn ? `, ${abn.toLocaleString()} out of range` : ""}`);
  if (work) parts.push(tlN(work, "workout") + (listed ? "" : ", not listed"));
  const p = $("#tlSum");
  p.hidden = !parts.length;
  p.textContent = parts.length ? `${when[0].toUpperCase() + when.slice(1)}${timelineFilters().n ? ", with these filters" : ""}: ${parts.join(" · ")}` : "";
}
let tlOpen = new Set(), tlList = null;   // the days whose workouts are open ("YYYY-MM-DD"); the list as drawn so far
function renderList() {
  const listed = tlWorkListed(), all = EV.filter(visible), v = tlListed(all, listed);
  tlSum(all, listed); tlAbnDays();
  $("#listTitle").textContent = `Events (${v.length.toLocaleString()})`;
  for (const b of $("#sortSeg").children) b.setAttribute("aria-pressed", (b.dataset.sort === "asc") === state.asc);
  const wb = $("#tlWork"), only = tlWatchOnly();
  wb.checked = listed;
  if (only) { wb.setAttribute("aria-disabled", "true"); wb.dataset.tip = "Watch is the only kind shown, so its workouts are listed"; }
  else { wb.removeAttribute("aria-disabled"); delete wb.dataset.tip; }
  const list = $("#list");
  if (!v.length) {   // #199: what would be here, and the one way back to it
    const w = all.filter(isWork).length, head = () => { const h = $("#listTitle"); h.tabIndex = -1; h.focus(); };   // the button goes with the empty list
    tlList = null;
    list.replaceChildren(w ? emptyState(`Only workouts in this period match these filters: ${tlN(w, "workout")}, left out of the list.`, {text: "List workouts", onclick: () => {
      state.tlWork = true; renderList(); save(); ($("#list .tl-row") || $("#listTitle")).focus();
    }}, {cls: ""}) : emptyState("No events in this period match these filters.", {text: "Show everything", onclick: () => {
      setPeriod("all"); resetTimelineFilters(); announce("Showing everything."); head();
    }}, {cls: ""}));
    return;
  }
  // the days in order, each with its rows: an event, or the day's workouts (an array, where its first one is)
  const days = [];
  for (const e of v) {
    const key = isoDay(e.ts); let d = days[days.length - 1];
    if (!d || d.key !== key) days.push(d = {key, ts: e.ts, rows: [], work: null, n: 0});
    d.n++;
    if (!isWork(e)) d.rows.push(e);
    else if (d.work) d.work.push(e);
    else d.rows.push(d.work = [e]);
  }
  // as many as were shown, while it's the same list (a record saved, the window resized); else the first page
  const key = JSON.stringify([state.from, state.to, [...state.cats], state.q, state.asc, listed]);
  const upTo = tlList?.key === key ? tlList.n : TL_PAGE;
  tlList = {key, days, at: 0, n: 0, mon: "", sec: null};
  list.replaceChildren();
  tlMore(upTo);
}
/* the next days, until n events in all are drawn (whole days), then Show more if any are left. -> the first row drawn */
function tlMore(n) {
  const L = tlList, list = $("#list"), frag = document.createDocumentFragment();
  let first = null;
  $("#tlMore")?.remove();
  while (L.at < L.days.length && L.n < n) {
    const d = L.days[L.at++], mon = d.key.slice(0, 7), z = zdate(d.ts);
    if (mon !== L.mon) { L.mon = mon; frag.append(L.sec = el("section", {class: "tl-month"}, el("div", {class: "month", text: fmtMonth(d.ts)}))); }
    const day = el("div", {class: "tl-day"}, el("span", {class: "dd", "aria-hidden": "true"}, el("b", {text: z.getDate()}), z.toLocaleDateString(undefined, {weekday: "short"})));
    for (const r of d.rows) day.append(...(Array.isArray(r) ? tlFold(r, d) : [tlRow(r)]));
    first ??= day.querySelector(".tl-row");
    L.sec.append(day); L.n += d.n;
  }
  list.append(frag);
  let next = 0;
  for (let i = L.at; i < L.days.length && next < TL_PAGE; i++) next += L.days[i].n;
  if (next) list.append(el("button", {class: "btn tl-more", id: "tlMore", type: "button", text: `Show ${next.toLocaleString()} more`,
    onclick: () => tlMore(L.n + TL_PAGE)?.focus()}));   // the first of them focused, the button gone (#202)
  return first;
}
/* an event's row: its whole date (for screen readers: the day's is shown once), its kind's icon in its lane's colour
   (#354), the title and its kind, a lab day's out-of-range count, and what it's about. It grows into the drawer
   (drawer.js, #270), and is found again by its record after a redraw (#404) */
function tlRow(e, cls) {
  const key = kindIcon(e.k, "key");
  key.style.color = CAT_OF[e.k].color;
  return el("div", {class: "tl-row" + (cls ? " " + cls : "") + (state.selEv === e.id ? " sel" : ""), "data-id": e.id, ...pressable(ev => openFrom(ev.currentTarget, () => openEvent(e)))},
    el("span", {class: "d vh", text: fmtD(e.ts)}), key, el("span", {class: "t", text: e.title}),
    el("span", {class: "k"}, KIND_LABEL[e.k], tlAbn(e) ? el("span", {class: "badge abn", text: `${tlAbn(e)} out of range`}) : null),
    e.sub ? el("span", {class: "s", text: e.sub}) : null);
}
/* a day's workouts: one row, or several folded into one that opens to each (kept open across redraws, tlOpen) */
function tlFold(w, d) {
  if (w.length === 1) return [tlRow(w[0])];
  const mins = w.reduce((a, e) => a + (DATA.details[e.id]?.minutes || 0), 0);
  let subs = [];
  const fold = () => { subs = w.map(e => tlRow(e, "tl-sub")); row.classList.remove("sel"); return subs; };
  const row = el("div", {class: "tl-row tl-fold" + (w.some(e => e.id === state.selEv) ? " sel" : ""), "data-day": d.key, "aria-expanded": "false", ...pressable(() => {
      const open = !tlOpen.has(d.key);
      tlOpen[open ? "add" : "delete"](d.key);
      row.setAttribute("aria-expanded", open);
      if (open) row.after(...fold()); else { subs.forEach(s => s.remove()); subs = []; }
    })},
    el("span", {class: "d vh", text: fmtD(d.ts)}), kindIcon("exercise", "key"), el("span", {class: "t", text: `${w.length} workouts${mins ? " · " + tlMins(mins) : ""}`}),
    el("span", {class: "k"}, "Workouts", icon("chevron-down")), el("span", {class: "s", text: [...new Set(w.map(e => e.title))].join(", ")}));
  if (!tlOpen.has(d.key)) return [row];
  row.setAttribute("aria-expanded", "true");
  return [row, ...fold()];
}
