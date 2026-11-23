/* Sanguity · menstrual cycle tracking on the records page (#183; server/cycle.py has the records and the privacy rules,
   server/cycle_tracking.py the setting). Only for a profile whose owner turned it on (Sources → Phone and watch), and
   only for its owners and editors: a viewer's page never asks, and the server refuses them.

   GET profiles/<id>/cycle gives the spans (a period as logged in Health Connect, or a run of days with flow recorded)
   and the marks (ovulation tests, cervical mucus, spotting), each with its words. They become events of kind "cycle"
   in EV (ids by day and kind, the same each time they're read: palette.js cycleId, #530): a Timeline lane of their own (TL_LANES,
   periods as bars, the rest as marks), its kind chip, the Events list and the drawer; and, when "Cycle tracking" is
   ticked under Labs (#192), a row on the event track over the charts (CHART_LAYERS, #294). That tick isn't remembered: it's asked for each
   time the page opens. The records aren't kept in this browser: not in the offline copy (offline.js keeps the
   records, settings and watch data only), not in localStorage, and not on the Overview's recent list.

   The words describe what was recorded; nothing here predicts a period, a fertile window or ovulation.
   Plain script, loaded in order by index.html after journal.js; all files share one global scope. */
"use strict";
let CYCLE = [];             // the events: {id, k: "cycle", t, end?, ts, te, title, sub, cy: the span or mark}
let cycleOnCharts = false;  // Labs: "Cycle tracking" ticked (never remembered)

let cycleAsked = 0;   // the latest load's number: an earlier one still waiting adds nothing (#526)
async function cycleLoad() {
  const ask = ++cycleAsked;
  if (CYCLE.length) EV = EV.filter(e => e.k !== "cycle");   // loaded again: in place of the earlier ones
  CYCLE = [];
  cycleControls();
  if (!PROFILE || (PROFILE.role !== "owner" && PROFILE.role !== "editor")) return;
  let c = null;
  try { const r = await api(`profiles/${PROFILE.id}/cycle`); if (r.ok) c = await r.json(); } catch (e) {}
  if (ask !== cycleAsked || !c || !c.on) return;
  const used = new Set();
  for (const s of c.spans || []) {
    const e = {id: cycleId(s, used), k: "cycle", t: s.from + "T00:00:00", end: s.to + "T23:59:59", title: s.title, sub: s.text, cy: {...s, kind: "span"}};
    e.ts = zParse(e.t); e.te = zParse(e.end); CYCLE.push(e);
  }
  for (const m of c.marks || []) {
    const e = {id: cycleId(m, used), k: "cycle", t: m.day + "T12:00:00", title: m.title, sub: m.app || "", cy: m};
    e.ts = e.te = zParse(e.t); CYCLE.push(e);
  }
  CYCLE = CYCLE.filter(e => isFinite(e.ts) && isFinite(e.te));
  for (const e of CYCLE) { e._q = [e.title, e.sub, "cycle tracking period"].join(" ").toLowerCase(); byId.set(e.id, e); }
  EV = EV.concat(CYCLE);
  cycleControls();
}

/* ---------------------------------------------------------------- one mark's shape (a span is a bar): its kind's
   (util.js KIND_SHAPE, #301), an ovulation test a ring, cervical mucus a cross, spotting a drop */
function cycleShape(m, x, y, attrs) {
  const shape = KIND_SHAPE[m.kind] || "ring";
  return shapeMark(shape, x, y, 3.6, shape === "ring" ? {stroke: "var(--c-cycle)", "stroke-width": 1.6, ...attrs} : {fill: "var(--c-cycle)", ...attrs});
}
function cycleDraw(g, x, y, L, R, events, attrsOf, each) {
  for (const e of events) {
    let node;
    if (e.cy.kind === "span") {
      const x1 = Math.max(L, x(e.ts)), x2 = Math.min(R, x(e.te));
      if (x2 < L || x1 > R) continue;
      node = el("svg:rect", {x: x1, y: y - 5, width: Math.max(3, x2 - x1), height: 10, rx: 3, fill: "var(--c-cycle)", "fill-opacity": e.cy.logged ? .85 : .6, ...attrsOf(e)});
    } else {
      const mx = x(e.ts); if (mx < L - 4 || mx > R + 4) continue;
      node = cycleShape(e.cy, mx, y, attrsOf(e));
    }
    if (each) each(node, e);
    g.push(node);
  }
}
const cycleVisible = e => state.cats.has("cycle") && inRange(e) && matchQ(e);

/* the Timeline's lane: bars and marks on one row, each opening the drawer (timeline.js picks them up by data-id) */
TL_LANES.cycle = {
  shows: () => CYCLE.some(cycleVisible),
  height: (w, S) => S.over ? 32 : Math.max(34, 26 * S.k),
  draw(g, S, top, h) {
    cycleDraw(g, S.x, top + h / 2, S.L, S.R, CYCLE.filter(cycleVisible), e => ({class: "ev cymark" + (state.selEv === e.id ? " sel" : ""), "data-id": e.id}));
  },
};

/* Labs: one row on the event track over the charts (#294), when ticked (#192's "Show on charts") */
const cycleInWindow = () => cycleOnCharts ? CYCLE.filter(e => e.te >= state.from && e.ts <= state.to && state.cats.has("cycle")) : [];
LEGEND_HAS.cycle = () => cycleInWindow().length > 0;   // its Legend key (trends.js legendSync, #301)
CHART_LAYERS.push({
  name: "Cycle tracking", short: "Cycle",
  space: () => cycleInWindow().length ? 18 * ckK() : 0,
  rows: () => cycleInWindow().map(e => [e.ts, "Cycle tracking", `${e.title}, ${cycleWhen(e)}`, () => openEvent(e), "cycle"]),
  draw(g, x, top, R, L) {
    cycleDraw(g, x, top + 9 * ckK(), L, R, cycleInWindow(), e => ({class: "cymark", tabindex: 0, role: "button", "aria-label": `${e.title}, ${cycleWhen(e)}`}), (m, e) => {
      m.addEventListener("pointermove", ev => { ev.stopPropagation(); showEvTip(e, ev); });
      m.addEventListener("pointerleave", hideTip);
      m.addEventListener("click", ev => { ev.stopPropagation(); hideTip(); openEvent(e); });
      m.addEventListener("keydown", ev => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); openEvent(e); } });
    });
  },
});
/* the tick beside Procedures & imaging, Medications and Journal, and its legend: only while there's something to show */
function cycleControls() {
  const marks = $("#optMarks")?.closest("label"), legend = document.querySelector("#view-labs .legend");
  let box = $("#optCycleLabel"), key = $("#cycleLegend");
  if (!CYCLE.length) { box?.remove(); key?.remove(); cycleOnCharts = false; return; }
  if (!box && marks) {
    box = el("label", {id: "optCycleLabel", class: "toggle"}, el("input", {type: "checkbox", id: "optCycle", onchange: ev => {
      cycleOnCharts = ev.target.checked; renderCharts(); }}), " Cycle tracking");
    ($("#optJournalLabel") || marks).after(box);
  }
  // its Legend key (#301: while it's on the charts in these dates), each mark kept with its word (#355)
  if (!key && legend) legend.append(key = el("span", {id: "cycleLegend", class: "jlegend keys", "data-legend": "cycle", hidden: true}, "Cycle tracking:",
    el("span", {}, el("svg:svg", {viewBox: "0 0 16 12", width: 16, height: 12, "aria-hidden": "true"}, el("svg:rect", {x: 1, y: 1, width: 14, height: 10, rx: 3, fill: "var(--c-cycle)", "fill-opacity": .85})),
      "period or flow recorded"),
    ...[["ovulation", "ovulation test"], ["mucus", "cervical mucus"], ["spotting", "spotting"]].map(([k, w]) =>
      el("span", {}, el("svg:svg", {viewBox: "0 0 12 12", width: 12, height: 12, "aria-hidden": "true"}, cycleShape({kind: k}, 6, 6.5, {})), w))));
}

/* ---------------------------------------------------------------- the drawer */
const cycleDay = d => fmtD(dayTs(d));
const cycleWhen = e => e.cy.kind === "span" ? (e.cy.from === e.cy.to ? cycleDay(e.cy.from) : `${cycleDay(e.cy.from)} – ${cycleDay(e.cy.to)}`) : cycleDay(e.cy.day);
function openCycle(e) {
  const s = e.cy;
  state.selEv = e.id;
  const body = drawerHead("cycle", e.title, `Cycle tracking · ${cycleWhen(e)}`), kv = rows => drawerFacts(rows) || "";   // drawer.js: the days are in its head (#270)
  if (s.kind === "span") {
    body.append(kv([["Recorded as", s.logged ? "A period, logged as one" : "Flow, day by day"],
      ["From the app", s.app], ["Source", s.source]]));
    if (s.flow && s.flow.length) body.append(el("div", {class: "sect", text: "Flow recorded"}),
      scrollBox({class: "tablewrap", "aria-label": "Flow recorded, day by day", style: {maxHeight: "none"}}, el("table", {class: "data"},
        el("thead", {}, el("tr", {}, el("th", {scope: "col", text: "Day"}), el("th", {scope: "col", text: "Flow"}))),
        el("tbody", {}, s.flow.map(([d, v]) => el("tr", {}, el("td", {text: cycleDay(d)}), el("td", {text: v})))))));
  } else {
    body.append(kv([["Day", cycleDay(s.day)], ["Record", {ovulation: "Ovulation test", mucus: "Cervical mucus", spotting: "Spotting"}[s.kind]],
      [s.kind === "ovulation" ? "Result" : "Appearance", s.value], ["Sensation", s.kind === "mucus" ? s.detail : ""], ["From the app", s.app], ["Source", s.source]]));
  }
  body.append(el("p", {class: "hint", text: "As the phone app recorded it. Sanguity doesn't predict periods or fertile days, and this isn't medical advice."}),
    el("p", {class: "hint"}, el("a", {href: `sources.html?profile=${PROFILE.id}#phone`, text: "Cycle tracking settings"}), ": who sees this, and deleting it."));
  showDrawer();
  renderTimeline();
}
