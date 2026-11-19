/* Sanguity · menstrual cycle tracking on the records page (#183; server/cycle.py has the records and the privacy rules,
   server/cycle_tracking.py the setting). Only for a profile whose owner turned it on (Sources → Phone and watch), and
   only for its owners and editors: a viewer's page never asks, and the server refuses them.

   GET profiles/<id>/cycle gives the spans (a period as logged in Health Connect, or a run of days with flow recorded)
   and the marks (ovulation tests, cervical mucus, spotting), each with its words. They become events of kind "cycle"
   in EV (ids from CYCLE_BASE up, apart from every record's and workout's): a Timeline lane of their own (TL_LANES,
   periods as bars, the rest as marks), its kind chip, the Events list and the drawer; and, when "Cycle tracking" is
   ticked under Labs (#192), a row under each chart (CHART_LAYERS). That tick isn't remembered: it's asked for each
   time the page opens. The records aren't kept in this browser: not in the offline copy (offline.js keeps the
   records, settings and watch data only), not in localStorage, and not on the Overview's recent list.

   The words describe what was recorded; nothing here predicts a period, a fertile window or ovulation.
   Plain script, loaded in order by index.html after journal.js; all files share one global scope. */
"use strict";
let CYCLE = [];             // the events: {id, k: "cycle", t, end?, ts, te, title, sub, cy: the span or mark}
let cycleOnCharts = false;  // Labs: "Cycle tracking" ticked (never remembered)
const CYCLE_BASE = 5e8;
const CYCLE_SHAPE = {ovulation: "circle", mucus: "diamond", spotting: "dot"};

async function cycleLoad() {
  if (CYCLE.length) EV = EV.filter(e => e.k !== "cycle");   // loaded again: in place of the earlier ones
  CYCLE = [];
  cycleControls();
  if (!PROFILE || (PROFILE.role !== "owner" && PROFILE.role !== "editor")) return;
  let c = null;
  try { const r = await api(`profiles/${PROFILE.id}/cycle`); if (r.ok) c = await r.json(); } catch (e) {}
  if (!c || !c.on) return;
  let id = CYCLE_BASE;
  for (const s of c.spans || []) {
    const e = {id: ++id, k: "cycle", t: s.from + "T00:00:00", end: s.to + "T23:59:59", title: s.title, sub: s.text, cy: {...s, kind: "span"}};
    e.ts = zParse(e.t); e.te = zParse(e.end); CYCLE.push(e);
  }
  for (const m of c.marks || []) {
    const e = {id: ++id, k: "cycle", t: m.day + "T12:00:00", title: m.title, sub: m.app || "", cy: m};
    e.ts = e.te = zParse(e.t); CYCLE.push(e);
  }
  CYCLE = CYCLE.filter(e => isFinite(e.ts) && isFinite(e.te));
  for (const e of CYCLE) { e._q = [e.title, e.sub, "cycle tracking period"].join(" ").toLowerCase(); byId.set(e.id, e); }
  EV = EV.concat(CYCLE);
  cycleControls();
}

/* ---------------------------------------------------------------- one mark's shape (a span is a bar) */
function cycleShape(m, x, y, attrs) {
  const shape = CYCLE_SHAPE[m.kind];
  if (shape === "diamond") return el("svg:path", {d: `M${x},${y - 4.5}L${x + 4.5},${y}L${x},${y + 4.5}L${x - 4.5},${y}Z`, fill: "var(--surface)", stroke: "var(--c-cycle)", "stroke-width": 1.6, ...attrs});
  if (shape === "dot") return el("svg:circle", {cx: x, cy: y, r: 2.6, fill: "var(--c-cycle)", ...attrs});
  return el("svg:circle", {cx: x, cy: y, r: 3.6, fill: "var(--surface)", stroke: "var(--c-cycle)", "stroke-width": 1.6, ...attrs});
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
  height: () => 34,
  draw(g, S, top, h, c, w) {
    const y = top + h / 2;
    g.push(el("svg:text", {class: "lane-label", x: 0, y: y + 4, text: w < 600 ? "Cycle" : c.label}));
    g.push(el("svg:line", {class: "gridline", x1: S.L, x2: S.R, y1: top + h, y2: top + h, "stroke-opacity": .5}));
    cycleDraw(g, S.x, y, S.L, S.R, CYCLE.filter(cycleVisible), e => ({class: "ev cymark" + (state.selEv === e.id ? " sel" : ""), "data-id": e.id}));
  },
};

/* Labs: one row under each chart's time axis, when ticked (#192's "Show on charts") */
const cycleInWindow = () => cycleOnCharts ? CYCLE.filter(e => e.te >= state.from && e.ts <= state.to && state.cats.has("cycle")) : [];
CHART_LAYERS.push({
  space: () => cycleInWindow().length ? 18 : 0,
  draw(g, x, top, R) {
    const y = top + 9;
    g.push(el("svg:text", {class: "jrow-label", x: 46, y: y + 3.5, "text-anchor": "end", text: "Cycle"}));
    cycleDraw(g, x, y, 52, R, cycleInWindow(), e => ({class: "cymark", tabindex: 0, role: "button", "aria-label": `${e.title}, ${cycleWhen(e)}`}), (m, e) => {
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
      cycleOnCharts = ev.target.checked; $("#cycleLegend").hidden = !cycleOnCharts; renderCharts(); }}), " Cycle tracking");
    ($("#optJournalLabel") || marks).after(box);
  }
  if (!key && legend) legend.append(key = el("span", {id: "cycleLegend", class: "jlegend", hidden: true},
    el("svg:svg", {viewBox: "0 0 44 12", width: 44, height: 12, "aria-hidden": "true"},
      el("svg:rect", {x: 1, y: 1, width: 14, height: 10, rx: 3, fill: "var(--c-cycle)", "fill-opacity": .85}),
      cycleShape({kind: "ovulation"}, 23, 6, {}), cycleShape({kind: "mucus"}, 33, 6, {}), cycleShape({kind: "spotting"}, 41, 6, {})),
    "Cycle tracking: bar = period or flow recorded; ○\u00a0ovulation test, ◇\u00a0cervical mucus, •\u00a0spotting"));   // each mark kept with its first word (#355)
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
