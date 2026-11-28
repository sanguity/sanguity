/* Sanguity · Timeline tab: lane chart with zoom and pan, its events by day, and the switch to the journal (#277).
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
/* ---------------------------------------------------------------- timeline
   The lanes on the chart kit (#300, chartkit.js): its time scale and axis (ckScale, ckTimeTicks, ckAxis), a hairline
   at each tick; the milestones named once over the lanes (ckFlagNames) and a faint line at each, as Labs' and Body's
   have them. A lane's marks within 6 px of a mark's first are one mark, with how many it holds beside it where there's
   room (its first record's data-id, and data-g, its place in tlSvg._groups: its readout lists them, a press zooms in
   to them), a lab day's ▲ when any of its draws has a value out of range (#250); a lane with nothing in the period isn't drawn, and a
   line under the chart names the kinds chosen that have nothing in it. The watch's steps are one path, a pixel's
   tallest day. The lanes' names are in a gutter as wide as they need, or over each lane where that would take too
   much of a phone's width (larger text, #633), every size and row growing with the labels' (ckK). Dragged, pinched or
   scrolled through panZoom (charts.js, #631), drawn again once a frame with the overview strip's window */
const tlSvg = $("#tl");
/* lanes that draw themselves: {k: {height(w, S) -> px (0: draw the lane's events as dots), draw(g, S, top, h, c, w),
   shows() -> anything to draw in the period, labelY(S): where its name goes in the gutter (px under its top; its
   middle without one)}}. S: {L, R, x, k, over (the names over the lanes)}. meds.js adds the Medications lane (#50): one row of
   spans per medication; cycle.js and journal.js theirs */
const TL_LANES = {};
/* each lane's events in the period, in time order, from one pass over the records: Map(lane -> [event]) */
function tlLaneEvents() {
  const by = new Map();
  for (const e of EV) {
    const c = CAT_OF[e.k];
    if (!c || e.hidden || !state.cats.has(c.k) || !inRange(e) || !matchQ(e)) continue;
    if (!by.has(c)) by.set(c, []);
    by.get(c).push(e);
  }
  for (const l of by.values()) l.sort((a, b) => a.ts - b.ts);
  return by;
}
const tlStepsIn = () => STEPS.filter(([ts]) => ts + DAY >= state.from && ts <= state.to);
const tlName = (c, narrow) => narrow ? c.label.replace(" & studies", "").replace(" & docs", "") : c.label;
/* a notification, a ring beside the recordings' dots (#182) */
const tlHollow = e => e.k === "appt_msg" || e.k === "auto_msg" || e.k === "rhythm";
/* marks within 6 px of a group's first, and drawn alike (a notification's ring isn't merged into a recording's dot):
   one group, at its middle -> [{x, items}] */
function tlGroups(evs, x) {
  const out = [];
  for (const e of evs) {
    const px = x(e.ts), g = out.at(-1);
    if (g && px - g.x0 < 6 && tlHollow(e) === tlHollow(g.items[0])) { g.items.push(e); g.x = (g.x0 + px) / 2; } else out.push({x0: px, x: px, items: [e]});
  }
  return out;
}
function renderTimeline(live) {   // live: a frame of a gesture, at the width it had (#631)
  if (!live) tlAbnDays();
  const w = live && tlSvg._geom ? tlSvg._geom.w : Math.max(300, tlSvg.parentElement.clientWidth - 32), narrow = w < 600, k = ckK();
  // drawn again with nothing changed but the record picked (the drawer opening or closing): the marks' selection only,
  // so nothing moves on the page behind it (#370); a gesture's frame is never the drawing to keep
  const key = [state.from, state.to, w, k, [...state.cats].join(), state.q, DOC_GEN, EV, EV.length, MEDS, CYCLE, DOSES, STEPS, STAYS, MILESTONES];
  if (!live && tlSvg._key?.every((v, i) => v === key[i])) return tlSel();
  tlSvg._key = live ? null : key;
  const evs = tlLaneEvents(), steps = state.cats.has("watch") ? tlStepsIn() : [];
  const chosen = CATS.filter(c => c.k !== "stay" && state.cats.has(c.k) && hasLane(c));
  const shows = c => TL_LANES[c.k]?.shows ? TL_LANES[c.k].shows() : evs.has(c) || (c.k === "watch" && steps.length > 0);
  // (a gesture's frames keep the lanes it began with, so the chart doesn't change height under the pointer)
  const lanes = live && tlSvg._lanes ? tlSvg._lanes : tlSvg._lanes = chosen.filter(shows), empty = chosen.filter(c => !lanes.includes(c));
  // the gutter: as wide as the widest name, or the names over their lanes where that's more than a third of the chart
  const base = narrow ? 96 : 150, need = Math.max(0, ...lanes.map(c => labelW(tlName(c, narrow), "lane-label"))) + 10;
  const over = need > Math.max(base, w * .36), L = over ? 8 : Math.max(base, need), R = w - (narrow ? 6 : 16);
  const x = ckScale(state.from, state.to, L, R), S = {L, R, x, k, over}, LH = over ? 15 * k : 0, g = [], groups = [];
  const ms = MILESTONES.map(m => [dayTs(m.date), m.label]).filter(([t]) => t >= state.from && t <= state.to);
  const top = ms.length ? ckFlagNames(g, ms.map(([t, n]) => ({x: x(t), text: n, title: `${n} · ${fmtD(t)}`})), L, R, 0) : 8;
  const own = lanes.map(c => TL_LANES[c.k]?.height(w, S) || 0), laneH = over ? 32 : Math.max(34, 26 * k);
  const laneHs = own.map(n => LH + (n || laneH)), laneTop = [];
  let plotB = top;
  for (const h of laneHs) { laneTop.push(plotB); plotB += h; }
  if (!lanes.length) plotB += laneH;
  const tk = ckTimeTicks(state.from, state.to, x, w), h = plotB + ckAxisH(tk.rows);
  tlSvg.setAttribute("viewBox", `0 0 ${w} ${h}`); tlSvg.setAttribute("height", h); tlSvg._geom = {L, R, T: top, B: plotB, w};
  for (const t of tk.ticks) g.push(el("svg:line", {class: "gridline", x1: x(t.t), x2: x(t.t), y1: top, y2: plotB}));
  ckAxis(g, tk, L, R, plotB, plotB + 16);
  const msAt = g.length, msWords = msMarks(g, x, state.from, state.to, top, plotB, R, null);
  for (const m of g.slice(msAt)) m.classList.add("faint");
  if (state.cats.has("stay")) for (const s of STAYS) {
    if (s.te < state.from || s.ts > state.to) continue;
    const x1 = Math.max(L, x(s.ts)), x2 = Math.min(R, x(s.te));
    g.push(el("svg:rect", {class: "ev", x: x1, y: top, width: Math.max(3, x2 - x1), height: plotB - top, fill: "var(--stay)", "data-id": s.id, rx: 2}));
  }
  tlSvg._steps = null;
  lanes.forEach((c, i) => {
    const t0 = laneTop[i] + LH, lh = laneHs[i] - LH, lane = TL_LANES[c.k], name = tlName(c, narrow);
    // its name: over it, or in the gutter, cut to the gutter's width with its whole name as its title
    const ly = over ? laneTop[i] + 12 * k : t0 + (lane?.labelY ? lane.labelY(S) : lh / 2) + 4 * k, fit = over ? name : ckFit(name, "lane-label", L - 8);
    g.push(el("svg:text", {class: "lane-label", x: 0, y: ly, text: fit}, fit !== name ? el("svg:title", {text: name}) : null));
    g.push(el("svg:line", {class: "gridline", x1: L, x2: R, y1: t0 + lh, y2: t0 + lh, "stroke-opacity": .5}));
    if (own[i]) return lane.draw(g, S, t0, lh, c, w);
    const y = t0 + lh / 2;
    if (c.k === "watch" && steps.length) {   // a day's steps a bar, the tallest of each pixel (#292), one path
      const max = steps.reduce((m, s) => Math.max(m, s[1]), 1), base = t0 + lh - 3, dw = x(state.from + DAY) - x(state.from), bw = Math.max(1, dw - (dw > 4 ? 1 : 0));
      let d = "";
      for (const [ts, v] of ckDecimate(steps, p => x(p[0]), 1, {lo: null})) {
        const x0 = Math.max(L, x(ts)), x1 = Math.min(R, x(ts) + bw); if (x1 <= x0) continue;
        d += `M${x0.toFixed(1)},${base}h${(x1 - x0).toFixed(1)}v${(-Math.max(1, (lh - 10) * v / max)).toFixed(1)}h${(x0 - x1).toFixed(1)}Z`;
      }
      if (d) g.push(el("svg:path", {class: "tl-steps", d, fill: MUTED, "fill-opacity": .45}));
      tlSvg._steps = {list: steps, y0: t0, y1: t0 + lh};
    }
    const gs = tlGroups(evs.get(c) || [], x);
    gs.forEach((gr, n) => {
      const its = gr.items, e = its[0], one = its.length === 1, px = gr.x;
      if (gr.x < L - 9 || gr.x > R + 9) return;
      for (const s of its) if (s.end && s.te - s.ts > DAY && x(s.te) - x(s.ts) > 4)
        g.push(el("svg:line", {x1: x(s.ts), x2: Math.min(R, x(s.te)), y1: y, y2: y, stroke: c.color, "stroke-width": 2, "stroke-opacity": .45, "stroke-linecap": "round", "pointer-events": "none"}));
      const sel = its.some(s => s.id === state.selEv);
      if (one && e.k === "journal") return g.push(journalMark(e, px, y, sel));   // shape by kind, size by severity (journal.js)
      const r = c.k === "lab" ? Math.min(9, 3.5 + Math.sqrt(its.reduce((a, s) => a + (s.n || 1), 0)) * .9) : one ? (e.k === "auto_msg" || e.k === "appt_msg" ? 3.5 : 5) : Math.min(7, 4.5 + its.length / 4);
      const hollow = tlHollow(e);
      g.push(el("svg:circle", {class: "ev" + (sel ? " sel" : ""), cx: px, cy: y, r, fill: hollow ? "var(--surface)" : c.color, stroke: hollow ? c.color : "var(--surface)",
        "stroke-width": e.k === "rhythm" ? 2 : 1.5, "data-id": e.id, ...(one ? {} : {"data-g": groups.length, "data-n": its.length})}));
      if (!one) {   // how many, beside it where it's clear of the next mark
        groups.push({c, items: its});
        const say = its.length.toLocaleString("en-US"), next = gs[n + 1];
        if ((next ? next.x - 8 : w) - px > r + 4 + labelW(say, "")) g.push(el("svg:text", {x: px + r + 2, y: y + 4 * k, text: say}));
      }
      if (c.k === "lab" && its.some(tlAbn)) {   // a ▲ above the day, known by its shape and not only its red (#250); a search's tests' (#464)
        const m = abnMark(px, y - r - 4.4, "H", 3.4, "tl-abn"); m.setAttribute("fill", "var(--crit)"); m.setAttribute("pointer-events", "none"); g.push(m);
      }
    });
  });
  tlSvg._groups = groups;
  if (!lanes.length) g.push(el("svg:text", {x: w / 2, y: top + 20, "text-anchor": "middle", text: chosen.length ? "Nothing in these dates" : "No categories selected"}));
  tlSvg.replaceChildren(...g);
  // the kinds chosen with nothing in these dates, said once under the chart, not drawn as empty lanes
  const none = $("#tlNone") || tlSvg.after(el("p", {class: "hint", id: "tlNone"})) || $("#tlNone");
  none.hidden = !lanes.length || !empty.length;
  none.textContent = empty.length ? `Nothing in these dates: ${empty.map(c => c.label).join(", ")}.` : "";
  // its table alternative is the list under it (#60): the same events, filters and period, each with its date, kind and title
  tlSvg.setAttribute("aria-label", `Timeline of medical events, ${fmtD(state.from)} to ${fmtD(state.to)}.${msWords ? " " + msWords : ""} The events it shows are listed under Events, below (its workouts with List workouts).`);
}
/* the record picked, marked on the lanes as they're drawn: its mark (a merged one holding it), or its medication's bars */
function tlSel() {
  const med = medShown();
  for (const n of tlSvg.querySelectorAll(".ev")) {
    const ds = n.dataset;
    n.classList.toggle("sel", n.matches(".medbar") ? MEDS[+ds.med] === med : ds.g != null ? tlSvg._groups[+ds.g].items.some(e => e.id === state.selEv)
      : ds.id != null && !n.matches("rect:not(.cymark)") && +ds.id === state.selEv);   // (a stay's shading isn't marked)
  }
}
/* a group's readout: its lane, how many and their dates, the first five, and what a click does */
function tlGroupTip(gr, ev) {
  const its = gr.items, a = its[0].ts, b = its.at(-1).ts, same = isoDay(a) === isoDay(b);
  showTip([el("div", {class: "v", text: `${gr.c.label}: ${its.length.toLocaleString("en-US")} records`}),
    el("div", {class: "s", text: same ? fmtD(a) : `${fmtD(a)} – ${fmtD(b)}`}),
    ...its.slice(0, 5).map(e => el("div", {class: "s", text: e.title})), its.length > 5 ? el("div", {class: "s", text: `and ${its.length - 5} more`}) : null,
    el("div", {class: "s", text: a === b ? "Click or tap for the first" : "Click or tap to zoom in"})], ev);
}
/* a group pressed: the dates around it, so its marks come apart; one moment's records, the first opened */
function tlGroupOpen(gr) {
  const a = gr.items[0].ts, b = gr.items.at(-1).ts;
  if (a === b) return openEvent(gr.items[0]);
  const m = (a + b) / 2, half = Math.max(DAY, (b - a) * 1.5);
  setRange(m - half, m + half);
}
/* the watch's day under the pointer, on its lane: [time, steps] */
function tlStepAt(ev) {
  const st = tlSvg._steps; if (!st) return null;
  const r = tlSvg.getBoundingClientRect(), k = tlSvg._geom.w / r.width, py = (ev.clientY - r.top) * k;
  if (py < st.y0 || py > st.y1) return null;
  const t = ckScale(state.from, state.to, tlSvg._geom.L, tlSvg._geom.R).inv((ev.clientX - r.left) * k), l = st.list;
  let lo = 0, hi = l.length - 1;
  while (lo < hi) { const m = (lo + hi + 1) >> 1; if (l[m][0] <= t) lo = m; else hi = m - 1; }
  return l[lo] && t >= l[lo][0] && t < l[lo][0] + DAY ? l[lo] : null;
}
/* hovering and pressing: a mark's readout, or the watch's day's steps; a press opens its record (the drag and the zoom
   are panZoom's, charts.js; a click after a drag doesn't reach here) */
tlSvg.addEventListener("pointermove", ev => {
  if (tlSvg.classList.contains("dragging")) return;
  const ds = ev.target?.dataset || {};
  if (ds.g != null) return tlGroupTip(tlSvg._groups[+ds.g], ev);
  if (ds.med != null) return medTip(MEDS[+ds.med], ds, ev);   // a medication's span or fill (meds.js)
  const id = +ds.id; if (id) return showEvTip(byId.get(id), ev);
  const st = tlStepAt(ev);
  if (st) return showTip([el("div", {class: "v", text: `${st[1].toLocaleString("en-US")} steps`}), el("div", {class: "s", text: fmtD(st[0]) + " · from watch"})], ev);
  hideTip();
});
tlSvg.addEventListener("click", ev => {
  const ds = ev.target?.dataset || {};
  if (ds.g != null) { hideTip(); return tlGroupOpen(tlSvg._groups[+ds.g]); }
  const id = +ds.id; if (id) openEvent(byId.get(id));
  else if (ds.med != null) openMedication(MEDS[+ds.med]);   // a medication's span (meds.js)
});
tlSvg.addEventListener("pointerleave", hideTip);
let lastW = innerWidth;
addEventListener("resize", () => {   // focus kept (store.js, #271), and in sight if it was before the resize (#738)
  if (innerWidth === lastW) return;
  lastW = innerWidth; clearTimeout(window._rz);
  const was = sightHold();
  window._rz = setTimeout(() => keepFocus(renderAll, was), 120);
});

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
const tlN = (n, one, many = one + "s") => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
const tlMins = m => (m < 60 ? `${m} min` : `${Math.floor(m / 60)} h${m % 60 ? ` ${m % 60} m` : ""}`).replace(/ /g, "\u00a0");   // kept on one line
/* Watch the only kind shown: its workouts are what's listed, so List workouts reads on and can't be put off (#444) */
const tlWatchOnly = () => state.cats.has("watch") && CATS.filter(hasLane).every(c => c.k === "watch" || !state.cats.has(c.k));
const tlWorkListed = () => state.tlWork || tlWatchOnly();
/* the events the list holds, in its order */
const tlListed = (all = EV.filter(visible), listed = tlWorkListed()) => all.filter(e => listed || !isWork(e)).sort((a, b) => state.asc ? a.ts - b.ts : b.ts - a.ts);
$("#sortSeg").onclick = ev => { const b = ev.target.closest("button[data-sort]"); if (!b) return; state.asc = b.dataset.sort === "asc"; renderList(); save(); };
$("#tlWork").onclick = ev => { if (ev.target.getAttribute("aria-disabled") === "true") ev.preventDefault(); };   // (focusable still, its tip saying why)
$("#tlWork").onchange = ev => { state.tlWork = ev.target.checked; renderList(); save(); };
/* the results of these lab draws, as Labs counts them (#451): their values, and those out of range (e.n counts
   reports, so "100 results, 67 out of range" read as 67 of 100 when it was 67 of 442 values); Labs' Vitals, readings
   taken at visits, aren't a draw's; with tests (a Set of series), theirs only. A draw's values are its own (store.js
   labDayWords' e.res, #471), as its badge counts them: an outside lab's result typed in on a clinic draw's day isn't
   counted when only the clinic's draw is listed. -> [n, abn, the flags of those, for their words (#819)] */
function tlLabValues(labs, tests = null) {
  const seen = new Set();   // (a result whose import has no draw that day is each of that day's draws')
  for (const e of labs) for (const [s, p] of e.res || []) if (!tests || tests.has(s)) seen.add(p);
  const flags = [...seen].filter(p => p[4]).map(p => p[4]);
  return [seen.size, flags.length, flags];
}
/* the tests a search names (#459): the lab series whose name, the name Labs shows it by, or panel has what was typed,
   as a lab day's search words do (store.js labDayWords); none with no search */
const tlTestsHit = () => !state.q ? [] : SERIES.filter(s => !s.watch && s.panel !== "Vitals"
  && [s.name, nice(s.name), s.panel || "", panelName(s.panel || "")].some(t => t.toLowerCase().includes(state.q)));
/* a lab draw's out-of-range count: its own results flagged, as Labs flags them (store.js labDayWords' e.res; #782: by
   the one rule, a range from settings too, as the line over the list and the drawer count them, not the count its
   importer stored, e.abn); under a search that names tests, those tests' (#464: "psa" showed "2 out of range" and a ▲
   on draws whose PSA was in range, from their other tests; #470: "creatinine" on a clinic draw for an outside lab's
   result typed in that day). A draw with no results in a series (text only) keeps its importer's count. tlAbnDays
   works out the tests for a redraw (null: no search names tests) */
let tlAbnDay = null;
function tlAbnDays() {
  const tests = tlTestsHit();
  tlAbnDay = tests.length ? new Set(tests) : null;
}
const tlAbnFlags = e => e.k === "lab" && (tlAbnDay || e.res?.length) ? (e.res || []).filter(([s, p]) => p[4] && (!tlAbnDay || tlAbnDay.has(s))).map(([, p]) => p[4]) : null;
const tlAbn = e => tlAbnFlags(e)?.length ?? e.abn;
/* its count in words, as its badge and tip say it (#819): "2 out of range", "2 out of range or flagged" where one is a ◆ */
const tlAbnSaid = e => `${tlAbn(e)} ${flaggedWords(tlAbnFlags(e) || [], "short")}`;
/* the line over the list: "The last year: 9 lab draws, 31 results, 3 out of range · 97 workouts, not listed". Under a
   search that names tests, the draws' results are those tests' only ("23 lab draws, 23 PSA results": not the 598 values
   of the draws that have a PSA, 8 of them out of range, read as PSA's), and it says which */
function tlSum(v, listed) {
  const labs = v.filter(e => e.k === "lab"), tests = tlTestsHit(), [res, abn, flags] = tlLabValues(labs, tests.length ? new Set(tests) : null);
  const work = v.filter(isWork).length, parts = [], when = periodLabel(), names = [...new Set(tests.map(s => nice(s.name)))];
  const what = !names.length ? tlN(res, "result") : names.length === 1 ? tlN(res, `${names[0]} result`)
    : `${tlN(res, "result")} of the ${names.length} tests matching “${timelineFilters().q || state.q}”`;
  if (labs.length) parts.push(`${tlN(labs.length, "lab draw")}, ${what}${abn ? `, ${abn.toLocaleString("en-US")} ${flaggedWords(flags, "short")}` : ""}`);
  if (work) parts.push(tlN(work, "workout") + (listed ? "" : ", not listed"));
  const p = $("#tlSum");
  p.hidden = !parts.length;
  p.textContent = parts.length ? `${when[0].toUpperCase() + when.slice(1)}${timelineFilters().n ? ", with these filters" : ""}: ${parts.join(" · ")}` : "";
}
let tlOpen = new Set(), tlList = null;   // the days whose workouts are open ("YYYY-MM-DD"); the list as drawn so far
function renderList() {
  const listed = tlWorkListed(), all = EV.filter(visible), v = tlListed(all, listed);
  tlSum(all, listed); tlAbnDays();
  $("#listTitle").textContent = `Events (${v.length.toLocaleString("en-US")})`;
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
  if (next) list.append(el("button", {class: "btn tl-more", id: "tlMore", type: "button", text: `Show ${next.toLocaleString("en-US")} more`,
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
    el("span", {class: "k"}, KIND_LABEL[e.k], tlAbn(e) ? el("span", {class: "badge abn", text: tlAbnSaid(e)}) : null),
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
