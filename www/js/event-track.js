/* Sanguity · Labs' event track (#294): what happened in the period, drawn once over the charts, not again in each.
   Milestones (Settings) as flags with their names, procedures and imaging (while "Procedures & imaging" is on) by their
   kind's shape (util.js KIND_SHAPE, #301), their names in up to three rows with leader lines and "+N" for the rest
   (chartkit.js ckFlagRows); under them the rows ticked under "Show on charts" (CHART_LAYERS: medications as spans,
   doses, the journal, mood, cycle tracking). It stays under the top bar while the charts scroll, where it isn't too tall for
   the window; each chart keeps only a faint line at each milestone (trends.js drawChart). Hover or step through it from
   the keyboard for each event's name, Enter or a click opens its record, and Chart | Table shows it as a table.
   Plain script, loaded in order by index.html after charts.js; all files share one global scope. */
"use strict";
/* what the track marks, in time order: {t, k ("milestone", "procedure", "imaging"), words, e (the record; none for a
   milestone)} */
function trackItems() {
  const ms = MILESTONES.map(m => ({t: dayTs(m.date), k: "milestone", words: m.label})).filter(i => i.t >= state.from && i.t <= state.to);
  return [...ms, ...markEvents().map(e => ({t: e.ts, k: e.k, words: e.title, e}))].sort((a, b) => a.t - b.t);
}
/* a procedure's or an imaging's name on the track: "CT chest abdomen pelvis" for "CT chest abdomen pelvis with contrast
   (CTAP)", in sentence case where the records write it in capitals; ckFlagRows shortens it to the room there is */
function shortTitle(t) {
  const s = t.replace(/\s*\(.*?\)\s*/g, " ").split(/\s+(?:WITH|W\/?O?|WO|AND)\s+/i)[0].trim();
  return /[a-z]/.test(s) ? s : s.charAt(0) + s.slice(1).toLowerCase();
}
const trackKind = k => k === "milestone" ? "Milestone" : KIND_LABEL[k];
const trackSays = i => `${trackKind(i.k)}: ${i.words}, ${fmtD(i.t)}`;
/* "3 milestones, 6 procedures, 2 imaging" */
function trackCount(items) {
  const n = k => items.filter(i => i.k === k).length, say = (k, one, many) => n(k) ? `${n(k)} ${n(k) === 1 ? one : many}` : null;
  return [say("milestone", "milestone", "milestones"), say("procedure", "procedure", "procedures"), say("imaging", "imaging", "imaging")].filter(Boolean).join(", ");
}
const trackLayers = () => CHART_LAYERS.filter(l => l.space() > 0);
/* what the track's drawing says to a screen reader, as it takes focus */
const trackLabel = (items, layers) => `Events, ${fmtD(state.from)} to ${fmtD(state.to)}: ${trackCount(items) || "none"}`
  + `${layers.length ? `; under them, ${layers.map(l => l.name.toLowerCase()).join(", ")}` : ""}.${items.length ? " Left and right arrow keys step through them, Enter opens one." : ""}`;
/* how far what has focus in a chart scrolls clear of the track while it's stuck (app.css, 2.4.11): its height, the one
   style set from here */
const trackRoom = px => $("#charts").style.setProperty("--track-h", px + "px");
/* an event's mark beside its words (a tooltip's): a milestone's flag, a procedure's or an imaging's shape */
const trackKey = k => k === "milestone" ? icon("flag") : shapeKey(KIND_SHAPE[k], {fill: CAT_OF[k].color});
/* the track's card, first in #charts (trends.js renderCharts), or null with nothing to show; drawn once the charts are */
function trackCard() {
  const items = trackItems(), layers = trackLayers();
  trackRoom(0);
  if (!items.length && !layers.length) return null;
  const card = el("div", {class: "card ev-track", id: "evTrack"},
    el("div", {class: "hd"}, el("h3", {class: "name", text: "Events"}),
      el("span", {class: "unit", text: [trackCount(items), ...layers.map(l => l.name)].filter(Boolean).join(" · ")}),
      viewSeg("#track", "the events", renderCharts)));
  if (state.tables.has("#track")) card.append(trackTable(items, layers));
  else {   // named as it's made, so focus on it is found again when it's drawn anew (store.js keepFocus)
    card.append(el("svg:svg", {class: "ck ev-svg", tabindex: 0, role: "application", "aria-roledescription": "event track", "aria-label": trackLabel(items, layers)}));
    requestAnimationFrame(() => drawTrack());
  }
  return card;
}
/* the same as a table, newest first: each event, and each row's what it draws, its record a button that opens it */
function trackTable(items, layers) {
  const rows = [...items.map(i => [i.t, trackKind(i.k), i.words, i.e && (() => openEvent(i.e)), i.k]), ...layers.flatMap(l => l.rows?.() || [])].sort((a, b) => b[0] - a[0]);
  return scrollBox({class: "tablewrap chart-table", "aria-label": "Events, as a table"}, el("table", {class: "data"},
    el("thead", {}, el("tr", {}, ["Date", "Kind", "Event"].map(h => el("th", {scope: "col", text: h})))),
    el("tbody", {}, rows.map(([t, kind, words, open, k]) => el("tr", {}, el("td", {text: fmtD(t)}),
      el("td", {}, k ? (k === "milestone" ? icon("flag") : kindIcon(k)) : null, kind),
      el("td", {}, open ? el("button", {class: "cell-btn", type: "button", text: words, onclick: open}) : words))))));
}
/* the track's drawing, at the charts' x for each date (their plot's edges, _geom), so a milestone's flag is over its
   line in every chart; drawn again as the charts are while they're dragged (charts.js; live: a frame of it), its names
   in as many rows as when the drag began, more of them under a "+N" if they need more, so it doesn't grow or shrink
   over the charts while they're dragged (#895: a row more, 30 px at 200% text, moved them under the pointer) */
function drawTrack(live) {
  const svg = $("#evTrack svg.ev-svg"); if (!svg?.isConnected) return;
  const items = trackItems(), shown = trackLayers(), layers = shown.map(l => [l, l.space()]), geo = $("#charts .chart svg.ck")?._geom;
  const w = labW(svg.parentElement), L = geo?.L ?? 52, R = geo ? w - (geo.w - geo.R) : w - 12, x = ckScale(state.from, state.to, L, R), held = live ? svg._rows : 0;
  const flags = ckFlagRows(items.map(i => ({x: x(i.t), text: i.k === "milestone" ? i.words : shortTitle(i.words), cls: i.k === "milestone" ? "mslabel" : "evlabel", rank: i.k === "milestone" ? 0 : 1, i})), L, R, held || 3);
  const k = ckK(), RH = 15 * k, nRows = held || (flags.length ? Math.max(...flags.map(f => f.row)) + 1 : 0), named = items.length || held, yM = named ? 6 + nRows * RH + 8 : 0;   // (rows as tall as larger text, #622)
  if (!live) svg._rows = nRows;
  let h = named ? yM + 10 : 2;
  // the rows' names (a layer's short) in the gutter, or over each row where one is wider than it (larger text, #633)
  const g = [], layersTop = h, over = layers.some(([l]) => l.short && labelW(l.short, "jrow-label") > L - 8), LH = l => over && l.short ? 14 * k : 0;
  h += layers.reduce((a, [l, n]) => a + n + LH(l), 0) + 4;
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`); svg.setAttribute("height", h); svg._geom = {L, R, T: 0, B: h, w};
  if (items.length) {
    g.push(el("svg:line", {class: "gridline", x1: L, x2: R, y1: yM, y2: yM}));
    for (const f of flags) {   // a name, and its leader down to its mark: a milestone's in its own ink, as its flag
      const ly = 6 + (nRows - 1 - f.row) * RH + RH * 2 / 3, ms = f.head.i.k === "milestone";
      g.push(el("svg:line", {class: ms ? "msflag" : "evlead", x1: f.x, x2: f.x, y1: ly + 3, y2: yM - (ms ? 0 : 5)}));
      const more = f.items.length > 1 ? f.text.match(/ \+\d+$/)[0] : "";
      g.push(el("svg:text", {class: f.cls + " ev-name", x: f.a + 2, y: ly, "data-ti": items.indexOf(f.head.i)}, more ? f.text.slice(0, -more.length) : f.text,
        more ? el("svg:tspan", {class: "ev-more", text: more}) : null));
    }
    items.forEach(i => {   // every mark, a milestone a flag on its pole; named or among a name's "+N"
      const mx = x(i.t);
      if (i.k !== "milestone") return g.push(evHead(i.k, mx, yM));
      g.push(el("svg:line", {class: "msflag", x1: mx, x2: mx, y1: yM - 7, y2: yM + 6}), el("svg:path", {class: "msflag-head", d: `M${mx},${yM - 7}L${mx + 7},${yM - 4.5}L${mx},${yM - 2}Z`}));
    });
  }
  const cross = el("svg:line", {class: "cross", y1: 0, y2: h, visibility: "hidden"});
  g.push(cross);
  { let top = layersTop; for (const [l, n] of layers) {
    if (l.short) g.push(el("svg:text", {class: "jrow-label", x: over ? L : L - 6, y: over ? top + 11 * k : top + n / 2 + 3.5 * k, "text-anchor": over ? null : "end", text: l.short}));
    top += LH(l); l.draw(g, x, top, R, L); top += n; } }
  svg.replaceChildren(...g);
  // a chart's crosshair puts the track's at the same date (chartkit.js ckSync)
  svg._hover = t => { ckCrossAt(cross, t == null ? null : x(t)); return null; };
  const near = px => items.filter(i => Math.abs(x(i.t) - px) < 8);
  const tipFor = list => [...list.slice(0, 5).map(i => el("div", {class: "s ev-tip"}, trackKey(i.k), `${i.words} · ${trackKind(i.k)}, ${fmtD(i.t)}`)), list.length > 5 ? el("div", {class: "s", text: `and ${list.length - 5} more`}) : null];
  const pxOf = ev => { const r = svg.getBoundingClientRect(); return [(ev.clientX - r.left) * (w / r.width), (ev.clientY - r.top) * (w / r.width)]; };
  svg.onpointermove = ev => {
    const [px, py] = pxOf(ev), named = items[ev.target.closest("[data-ti]")?.dataset.ti];
    ckSync(svg, x.inv(px));
    const list = named ? [named] : py < yM + 8 ? near(px) : [];
    if (list.length) ckTip(svg, named ? x(named.t) : px, tipFor(list)); else hideTip();
  };
  svg.onpointerleave = () => { if (svg._kt == null) { ckSync(svg, null); hideTip(); } };
  svg.onclick = ev => {
    const [px, py] = pxOf(ev), i = items[ev.target.closest("[data-ti]")?.dataset.ti] || (py < yM + 8 ? near(px).find(i => i.e) : null);
    if (i?.e) { hideTip(); openEvent(i.e); }
  };
  // the keyboard: ← and → step through every event, those under a "+N" too, with its name said and shown; Enter opens it
  svg.setAttribute("aria-label", trackLabel(items, shown));
  svg.onkeydown = ev => {
    const k = ev.key, n = items.length;
    if (k === "Escape") { if (svg._kt == null) return; svg._kt = null; ckSync(svg, null); hideTip(); ev.preventDefault(); ev.stopPropagation(); return; }
    if ((k === "Enter" || k === " ") && items[svg._kt]?.e) { ev.preventDefault(); hideTip(); return openEvent(items[svg._kt].e); }
    if (!n || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(k)) return;
    ev.preventDefault();
    const at = svg._kt;
    svg._kt = k === "Home" ? 0 : k === "End" ? n - 1 : at == null ? (k === "ArrowLeft" ? n - 1 : 0) : Math.max(0, Math.min(n - 1, at + (k === "ArrowLeft" ? -1 : 1)));
    const i = items[svg._kt];
    ckSync(svg, i.t); ckTip(svg, x(i.t), tipFor([i]));
    announce(`${trackSays(i)}. ${svg._kt + 1} of ${n}.`);
  };
  svg.onblur = () => { if (svg._kt == null) return; svg._kt = null; ckSync(svg, null); hideTip(); };
  svg._kt = null;
  trackStick();
}
/* under the top bar while the charts scroll (at the top where the bar scrolls away), unless it would take too much of
   the window, or the tallest that takes focus in the stack (a chart, or a lab's table: #723, a table under it) wouldn't
   fit whole between it and the stack's axis, clear of both by the page's scroll padding (#651: at 844×390 it hid the
   top of a chart given focus), or the window is a phone's (#615: with the top bar, the tab bar and the stack's axis it
   left half the screen for the charts); what has focus in a chart scrolls clear of it (app.css, 2.4.11). Asked again
   as the axis changes height (trends.js footH) */
function trackStick() {
  const card = $("#evTrack svg.ev-svg")?.closest(".ev-track"); if (!card) return;   // (its drawing, not its table)
  const tall = Math.max(0, ...[...document.querySelectorAll("#charts .chart :is(svg.ck, .tablewrap)")].map(x => x.getBoundingClientRect().height));
  const h = card.offsetHeight, pad = getComputedStyle(document.documentElement);
  const stuck = !matchMedia("(max-width:640px)").matches && h <= innerHeight * .35
    && innerHeight - parseFloat(pad.scrollPaddingTop) - parseFloat(pad.scrollPaddingBottom) - h >= tall;
  card.classList.toggle("stuck", stuck);
  trackRoom(stuck ? h : 0);
}
