/* Sanguity · what the filters leave out (#190). Whenever a tab's filters narrow what it shows, a line says how
   ("Showing 3 of 11 kinds · matching “visit”") and ends with one "Reset filters", which puts that tab's filters back
   as they start: never the period, which has its own control in the records bar. filterLine() draws the line; each tab
   describes its own filters where they are:
   - the Timeline's kinds and search: under the records bar (#fsum, below), still there on a phone with the Filters
     panel closed, where the Filters button counts the filters on ("Filters (2)"); its counts say they're for what's
     shown (#statsNote). The kinds' visible "Show all" (#189) gave way to it: the two sat side by side, and each kind's
     ⋯ menu still has Show all for the kinds alone;
   - the Labs name filter: under its box, in the lab list's header and the heatmap's (trends.js, labCount);
   - Costs: under each list's own filters, the claims and the out-of-pocket amounts, their totals then saying they're
     for what's shown (costs.js);
   - the journal's card on the Timeline (journal.js, jList).
   Reset is said through announce(), and the Timeline's search says how many events match once typing stops; nothing
   is said on each keystroke.
   Plain script, loaded in order by index.html after core.js; all files share one global scope. */
"use strict";
/* p (a new <p class="fsum"> if none) holding parts joined by " · " and a Reset filters button; no parts: emptied and
   hidden. reset() puts the filters back and draws the tab again; focus then goes to what focusTo() returns, a control
   that's still there (the button itself goes with the line). -> p */
function filterLine(parts, reset, focusTo, p = el("p", {class: "fsum"})) {
  parts = parts.filter(Boolean);
  p.hidden = !parts.length;
  p.replaceChildren(...(parts.length ? [el("span", {class: "fsum-t", text: parts.join(" · ")}), " ", el("span", {class: "fsum-act"}, el("span", {class: "fsum-sep", "aria-hidden": "true", text: "· "}),
    el("button", {type: "button", class: "fsum-reset", text: "Reset filters", onclick: () => {
      reset();
      announce("Filters reset.");
      focusTo?.()?.focus();
    }}))] : []));
  return p;
}
/* the kinds part: "Showing only Visits", "Showing 3 of 11 kinds" (labels: the kinds shown) */
const kindsShown = (labels, all) => labels.length === 1 ? `Showing only ${labels[0]}` : `Showing ${labels.length ? labels.length : "none"} of ${all} kinds`;
/* the parts: the kinds part first if any, else "Showing <noun> <the first of the rest>" ("Showing events matching
   “visit”"); rest: what each other filter keeps, falsy for one that's off */
function showingParts(kinds, noun, rest) {
  rest = rest.filter(Boolean);
  return kinds ? [kinds, ...rest] : rest.length ? [`Showing ${noun} ${rest[0]}`, ...rest.slice(1)] : [];
}
const visibleIn = (...els) => els.find(n => n && n.getClientRects().length);   // the first drawn: a hidden one has no box

/* ---- the Timeline: its kinds and search (records bar) */
const KINDS_OFF_AT_START = new Set(["claim"]);   // as state.cats starts (core.js): Insurance claims are on Costs
function timelineFilters() {
  const kinds = CATS.filter(hasLane), on = kinds.filter(c => state.cats.has(c.k));
  const narrowed = kinds.some(c => !state.cats.has(c.k) && !KINDS_OFF_AT_START.has(c.k)), q = state.q ? $("#q").value.trim() : "";
  return {kinds, on, narrowed, q, n: (narrowed ? 1 : 0) + (q ? 1 : 0)};
}
/* every kind shown (Insurance claims as they were) and no search */
function resetTimelineFilters() {
  for (const c of CATS.filter(hasLane)) if (!KINDS_OFF_AT_START.has(c.k)) state.cats.add(c.k);
  clearTimeout(qT); state.q = ""; $("#q").value = "";
  renderAll();
}
/* from renderAll (app.js): the line, the Filters button's count and the counts' caption */
function renderFilterSum() {
  const tl = state.tab === "timeline", f = timelineFilters();
  filterLine(tl ? showingParts(f.narrowed && kindsShown(f.on.map(c => c.label), f.kinds.length), "events", [f.q && `matching “${f.q}”`]) : [],
    resetTimelineFilters, () => visibleIn(...document.querySelectorAll("#chips .toggle"), $("#ftoggle"), $("#q")), $("#fsum"));
  $("#ftoggle").textContent = !tl ? "Dates" : f.n ? `Filters (${f.n})` : "Filters";   // what it opens on a phone
  $("#statsNote").hidden = !(tl && f.n);
}
{ let t; $("#q").addEventListener("input", () => {   // said once typing stops, not on each key
  clearTimeout(t); t = setTimeout(() => { if (state.q) { const n = EV.filter(visible).length; announce(`${n ? n.toLocaleString() : "No"} event${n === 1 ? "" : "s"} match.`); } }, 1000);
}); }
