/* Sanguity · shared chart behaviour: drag/pinch/scroll pan-zoom (Labs' charts and heatmap, Body and activity), milestone
   markers, and the Labs tab's Chart / Heatmap switch.
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
/* drag to pan, scroll wheel or pinch to zoom, double-click to reset — shared by Labs (both views) and Body and activity.
   plot(ev) -> the chart under the pointer (or null); span(plot) -> [left, width] of its time axis in client px;
   get() -> [a, b]; set(a, b) updates state; draw() redraws live; end() does the full render once the gesture stops;
   data() -> [first, last] of what the view draws (null: none).
   The window keeps touching the data (#382): a gesture moves it as far as the first or last record, or where it was
   when the gesture began if that was further; past that it goes on as a rubber band does, a quarter of itself at
   most, and springs back when let go. Zooming out stops at all of it. */
function panZoom(box, {plot, span, get, set, draw, end, reset, start, data, minSpan = 2 * DAY}) {
  const ptrs = new Map(); let drag = null, pinch = null, raf = 0, dirty = false, finT = 0, justDragged = false;
  let lim = null, want = null, anim = 0, rest = null;   // the gesture's limits, the window it asks for, the spring back
  const timeAt = (p, cx) => { const [l, w] = span(p), [a, b] = get(); return a + (cx - l) / w * (b - a); };
  const limits = () => {
    if (!lim) { const [a, b] = get(), d = data?.(); lim = d && d[0] <= d[1] ? [Math.min(a, d[0]), Math.max(b, d[1])] : [-Infinity, Infinity]; }
    return lim;
  };
  // where a window rests: inside the limits, as wide as they are at most
  const inside = (a, b) => { const [l0, l1] = limits(), w = b - a; return w >= l1 - l0 ? [l0, l1] : a < l0 ? [l0, l0 + w] : b > l1 ? [l1 - w, l1] : [a, b]; };
  // the window shown for one asked for: past a limit by x, it goes x·m / (x + m), never m, a quarter of the window
  const band = (a, b) => {
    const [c, d] = inside(a, b), m = (d - c) / 4, over = (c - a) || (d - b);
    if (!over || d - c < b - a) return [c, d];
    const s = Math.sign(over) * Math.abs(over) * m / (Math.abs(over) + m);
    return [c - s, d - s];
  };
  const apply = (a, b) => {
    if (!isFinite(a) || !isFinite(b)) return;   // a chart taken out of the page mid-gesture measures 0 px wide
    if (b - a < minSpan) { const m = (a + b) / 2; a = m - minSpan / 2; b = m + minSpan / 2; }
    want = [a, b]; [a, b] = band(a, b);
    set(a, b); dirty = true; cancelAnimationFrame(raf); raf = requestAnimationFrame(draw);
  };
  // let go: back inside the limits, eased over 200 ms (at once with reduced motion), then the full render
  const finish = () => {
    clearTimeout(finT); if (!dirty) return; dirty = false; cancelAnimationFrame(raf);
    const from = get(); rest = inside(...from); lim = want = null;
    if (from[0] === rest[0] && from[1] === rest[1]) return end();
    const t0 = performance.now(), dur = matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 200;
    let was = from;
    const step = now => {
      const [a, b] = get(); if (Math.abs(a - was[0]) + Math.abs(b - was[1]) > 1) return anim = 0;   // a period picked meanwhile: that one stays
      const k = dur ? Math.min(1, (now - t0) / dur) : 1, e = 1 - (1 - k) ** 3;
      set(...(was = [from[0] + (rest[0] - from[0]) * e, from[1] + (rest[1] - from[1]) * e])); draw();
      if (k < 1) anim = requestAnimationFrame(step); else { anim = 0; end(); }
    };
    anim = requestAnimationFrame(step);
  };
  // a new gesture while it springs back starts from where it rests (the full render comes when that one ends)
  const settle = () => { if (!anim) return; cancelAnimationFrame(anim); anim = 0; set(...rest); dirty = true; };
  box.addEventListener("pointerdown", ev => {
    const p = plot(ev); if (!p || (ev.pointerType === "mouse" && ev.button !== 0)) return;
    settle(); ptrs.set(ev.pointerId, ev.clientX);
    if (ptrs.size === 2) {
      const [x1, x2] = [...ptrs.values()], [a, b] = get(); pinch = {d: Math.abs(x1 - x2) || 1, t: timeAt(p, (x1 + x2) / 2), a, b}; drag = null;
      for (const id of ptrs.keys()) try { box.setPointerCapture(id); } catch (e) {}
      hideTip(); return;
    }
    const [a, b] = get(); drag = {p, id: ev.pointerId, x: ev.clientX, a, b, moved: false};
  });
  box.addEventListener("pointermove", ev => {
    if (!ptrs.has(ev.pointerId)) return;
    ptrs.set(ev.pointerId, ev.clientX);
    if (pinch && ptrs.size === 2) { const [x1, x2] = [...ptrs.values()], k = pinch.d / (Math.abs(x1 - x2) || 1); apply(pinch.t - (pinch.t - pinch.a) * k, pinch.t + (pinch.b - pinch.t) * k); return; }
    if (!drag || drag.id !== ev.pointerId) return;
    const dx = ev.clientX - drag.x;
    if (!drag.moved) {
      if (Math.abs(dx) < 4) return;
      drag.moved = true; box.classList.add("dragging"); hideTip(); start?.();
      try { box.setPointerCapture(ev.pointerId); } catch (e) {}   // capture only once it is a drag, so plain clicks still reach the chart
    }
    const dt = dx / span(drag.p)[1] * (drag.b - drag.a); apply(drag.a - dt, drag.b - dt);
  });
  const up = ev => {
    if (!ptrs.delete(ev.pointerId)) return;
    if (pinch && ptrs.size < 2) pinch = null;
    if (drag && drag.id === ev.pointerId) drag = null;
    if (!ptrs.size) {
      if (box.classList.contains("dragging")) { justDragged = true; setTimeout(() => justDragged = false, 0); }
      box.classList.remove("dragging"); finish();
    }
  };
  box.addEventListener("pointerup", up); box.addEventListener("pointercancel", up);
  box.addEventListener("click", ev => { if (justDragged) { ev.stopPropagation(); ev.preventDefault(); } }, true);
  box.addEventListener("dblclick", ev => { if (plot(ev)) { ev.preventDefault(); clearTimeout(finT); cancelAnimationFrame(anim); anim = 0; dirty = false; lim = want = null; reset(); } });
  box.addEventListener("wheel", ev => {
    const p = plot(ev); if (!p) return;
    settle();
    const [a, b] = want || get();   // a gesture's wheel goes on from the window it asked for, not the one held back
    if (!ev.ctrlKey && Math.abs(ev.deltaX) > Math.abs(ev.deltaY)) {   // horizontal swipe / shift+wheel: pan
      const dt = ev.deltaX / span(p)[1] * (b - a); apply(a + dt, b + dt);
    } else {                                  // scroll wheel and trackpad pinch (arrives as ctrl+wheel): zoom around the pointer, like the Timeline
      const t = timeAt(p, ev.clientX), k = Math.exp(ev.deltaY * 0.0015 * (ev.deltaMode === 1 ? 30 : 1));
      apply(t - (t - a) * k, t + (b - t) * k);
    }
    ev.preventDefault(); hideTip(); clearTimeout(finT); finT = setTimeout(finish, 300);
  }, {passive: false});
}
// the first and last time of series' points: what a view's window keeps touching (#382); null for none
const seriesSpan = list => { let a = Infinity, b = -Infinity; for (const s of list) if (s.pts.length) { a = Math.min(a, s.pts[0].ts); b = Math.max(b, s.pts[s.pts.length - 1].ts); } return a <= b ? [a, b] : null; };
/* a view's window with nothing of its in it (dates typed in past the records, #382): its charts keep their plots, to
   drag back and double-click, and this above them says so, with the way back to the period before, or to everything */
function wayBack(what) {
  const b = (text, p, cls = "") => el("button", {class: "btn empty-next " + cls, type: "button", text, onclick: () => periodBack(p)});
  const e = emptyState(`No ${what} in these dates. Drag a chart back toward them, or:`);
  e.classList.add("pz-back");
  e.append(el("div", {class: "empty-acts"}, b(backWords(), lastPeriod, "primary"), lastPeriod !== "all" ? b("Show all", "all") : null));
  return e;
}
const svgSpan = svg => { const r = svg.getBoundingClientRect(), g = svg._geom, k = r.width / g.w; return [r.left + g.L * k, (g.R - g.L) * k]; };
const mainWindow = {get: () => [state.from, state.to], set: (a, b) => { state.from = a; state.to = b; state.preset = "custom"; }, reset: () => applyPreset("all")};
// Labs, as charts: every chart shares state.from/to, so they all move together
const chartsBox = $("#charts");
const inWindow = s => s.pts.filter(p => p.ts >= state.from && p.ts <= state.to);
panZoom(chartsBox, {...mainWindow,
  plot: ev => { const s = ev.target.closest?.(".chart svg, .ck-axis"); return s && s._geom ? s : null; }, span: svgSpan,   // the charts' one axis too (#293)
  start: () => chartsBox.querySelectorAll(".chart svg").forEach(o => o._hover && o._hover(null)), data: () => seriesSpan(SERIES.filter(s => !s.watch && state.sel.has(s.name))),
  draw: () => { chartsBox.querySelectorAll(".chart svg").forEach(svg => { if (svg._s && svg._geom) drawChart(svg, svg._s, inWindow(svg._s), svg._read, true); }); labAxisDraw(); drawTrack(); },   // and their one axis (#293), and the event track over them (#294)
  end: () => { renderFilters(); renderCharts(); save(); }});
// Labs, as a heatmap: the same date window; columns are draw days, so the time axis is the column area
panZoom($("#hmWrap"), {...mainWindow,
  plot: ev => ev.target.closest?.("#hm, #hmAxis") && $("#hm")._geom ? $("#hm") : null, span: svgSpan, data: () => seriesSpan(hmLabs().filter(labMatch)),
  draw: () => renderHeatmap(true), end: () => { renderFilters(); renderHeatmap(); save(); }});   // renderHeatmap lives in a later file: call it, do not reference it at load
// Body and activity: all its charts share the records bar's period too ("All" there is the watch and scale data's span)
panZoom($("#view-health"), {...mainWindow, minSpan: 3 * DAY,
  plot: ev => { const s = ev.target.closest?.("svg"); return s && s._draw && s._geom ? s : null; }, span: svgSpan,
  get: () => hRange(), data: () => WATCH ? (H = H || hData(), [H.first, H.last]) : null,
  draw: () => { const [a, b] = hRange(); $("#view-health").querySelectorAll("svg").forEach(s => s._draw && s._draw(a, b, true)); },
  end: () => { renderFilters(); renderHealth(); save(); }});
/* milestone rules on a time chart: a dashed line in --milestone (never the accent, #334) from y0 to y1, label on its own
   row at labelY (null = no labels). A label stays inside the plot, x(a) to R (#312): after its rule, before it near the
   right edge, against that edge, or shortened with "…" to the room there is; its <title> has the full names and dates.
   Returns the milestones shown, in words for the chart's aria-label ("" when none). */
const msInRange = (a, b) => MILESTONES.some(m => { const t = dayTs(m.date); return t >= a && t <= b; });
function msMarks(g, x, a, b, y0, y1, R, labelY) {
  const L = x(a), tw = s => labelW(s, "mslabel"), names = items => items.map(([m]) => m.label).join(" · ");
  const groups = [], shown = [];   // a milestone whose label would run into the previous label joins it: "Diagnosis · Treatment start"
  for (const m of MILESTONES) {
    const t = dayTs(m.date); if (t < a || t > b) continue;
    const mx = x(t);
    shown.push(`${m.label}, ${fmtD(t)}`);
    g.push(el("svg:line", {class: "msmark", x1: mx, x2: mx, y1: y0, y2: y1}));
    const last = groups[groups.length - 1];
    if (last && mx < last.x + tw(names(last.items)) + 12) last.items.push([m, t]); else groups.push({x: mx, items: [[m, t]]});
  }
  const words = shown.length ? `Milestone${shown.length > 1 ? "s" : ""}: ${shown.join("; ")}.` : "";
  if (labelY == null) return words;
  const shorten = (s, room) => { for (let n = s.length - 1; n > 2; n--) { const t = s.slice(0, n).trimEnd() + "…"; if (tw(t) <= room) return t; } return null; };
  const place = (items, mx, left) => {   // where a group's label goes, starting no further left than left; null: no room
    const text = names(items), w = tw(text);
    if (mx + 4 + w <= R) return {x: mx + 4, anchor: "start", text, end: mx + 4 + w};
    if (mx - 4 - w >= left) return {x: mx - 4, anchor: "end", text, end: mx - 4};
    if (R - w >= left) return {x: R, anchor: "end", text, end: R};
    const short = shorten(text, R - left);
    return short && {x: left, anchor: "start", text: short, end: left + tw(short)};
  };
  const labels = [];   // a group with no room left joins the one before it
  for (let {x: mx, items} of groups) {
    let left = labels.length ? labels[labels.length - 1].at.end + 8 : L, at;
    while (!(at = place(items, mx, left)) && labels.length) { const p = labels.pop(); items = [...p.items, ...items]; mx = p.mx; left = p.left; }
    if (at) labels.push({items, mx, left, at});
  }
  for (const {items, at} of labels)
    g.push(el("svg:text", {class: "mslabel", x: at.x, y: labelY, "text-anchor": at.anchor}, at.text,
      el("svg:title", {text: items.map(([m, t]) => `${m.label} · ${fmtD(t)}`).join("\n")})));
  return words;
}
/* ---- chart accessibility (#60): every chart has a table to show instead, out-of-range results are marked by shape
   as well as colour, and a chart takes focus, its points stepped through from the keyboard.
   flagWord(f): a result's flag in words, with its mark: "▲ High", "▼ Low", or "◆ Flagged" for any other flag;
   flagNodes(f): the same, the mark hidden from screen readers (they say the word). */
const FLAG_OF = f => !f ? null : /^h/i.test(f) ? ["▲", "High"] : /^l/i.test(f) ? ["▼", "Low"] : ["◆", "Flagged"];
const flagWord = f => (FLAG_OF(f) || []).join(" ");
const flagNodes = (f, words) => { const w = FLAG_OF(f); return w ? [el("span", {"aria-hidden": "true", text: w[0] + " "}), words || w[1]] : []; };   // words: another wording (#234)
/* an out-of-range result's mark on a chart: a triangle pointing up (above its range) or down (below), a diamond for
   any other flag; s is its half-width */
function abnMark(cx, cy, f, s = 4.5, cls = "pt abn") {
  const p = (dx, dy) => `${(cx + dx).toFixed(1)},${(cy + dy).toFixed(1)}`;
  const d = /^h/i.test(f) ? `M${p(0, -s * 1.1)}L${p(s, s * .7)}L${p(-s, s * .7)}Z` : /^l/i.test(f) ? `M${p(0, s * 1.1)}L${p(s, -s * .7)}L${p(-s, -s * .7)}Z`
    : `M${p(0, -s)}L${p(s, 0)}L${p(0, s)}L${p(-s, 0)}Z`;
  return el("svg:path", {class: cls, d});
}
/* Chart | Table in a chart's header: a pick-one pair (#193). key: what state.tables remembers it by (a lab's name,
   or "#…" for the other charts); what: the chart, for its label ("Show Hemoglobin as"); redraw() draws it again;
   graphic: the word for the picture ("Heatmap"). Focus stays on the choice once it's redrawn. */
function viewSeg(key, what, redraw, graphic = "Chart") {
  const table = state.tables.has(key);
  const pick = want => {
    if (want === table) return;
    if (want) state.tables.add(key); else state.tables.delete(key);
    hideTip(); redraw();
    [...document.querySelectorAll(".view-seg")].find(s => s.dataset.key === key)?.querySelector("[aria-pressed=true]")?.focus();
  };
  const seg = el("div", {class: "seg view-seg", role: "group", "aria-label": `Show ${what} as`},
    el("button", {type: "button", "aria-pressed": !table, text: graphic, onclick: () => pick(false)}),
    el("button", {type: "button", "aria-pressed": table, text: "Table", onclick: () => pick(true)}));
  seg.dataset.key = key;
  return seg;
}
/* a table of a chart's values: heads, then rows of cells (a string, or [text, flag] for a value outside its range,
   shown with flagWord's mark and in the out-of-range colour; [text, flag, words] says words instead, as "Above your
   target", #234). It scrolls, so it takes focus to be scrolled by keyboard,
   named by its columns (#202) */
function chartTable(heads, rows, cls = "") {
  const td = c => Array.isArray(c) ? el("td", {class: c[1] ? "abn" : null}, c[0], c[1] ? " " : null, ...flagNodes(c[1], c[2])) : el("td", {text: c ?? ""});
  return scrollBox({class: "tablewrap chart-table", "aria-label": `${heads.slice(1).join(", ")} by ${heads[0].toLowerCase()}`}, el("table", {class: "data " + cls},
    el("thead", {}, el("tr", {}, heads.map(h => el("th", {scope: "col", text: h})))),
    el("tbody", {}, rows.map(r => el("tr", {}, r.map(td))))));
}
/* the keyboard on a chart: it takes focus (role application, said as a chart, label summing it up); ← and → step
   between its points, Home and End go to the first and last, Escape lets go. times() -> the points' times shown now,
   in order; show(t) shows that point's readout (the hover's) and returns its words, said to screen readers; clear()
   hides it. Set again on every redraw. */
function chartKeys(svg, label, times, show, clear) {
  svg.setAttribute("tabindex", "0"); svg.setAttribute("role", "application");
  svg.setAttribute("aria-roledescription", "chart"); svg.setAttribute("aria-label", label);
  svg.onkeydown = ev => {
    const ts = times(), n = ts.length, k = ev.key;
    if (k === "Escape") { if (svg._kt == null) return; svg._kt = null; clear(); ev.preventDefault(); ev.stopPropagation(); return; }
    if (!n || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(k)) return;
    ev.preventDefault();
    let i = svg._kt == null ? -1 : ts.indexOf(svg._kt);
    if (i < 0 && svg._kt != null) i = ts.reduce((b, t, j) => Math.abs(t - svg._kt) < Math.abs(ts[b] - svg._kt) ? j : b, 0);   // moved: the nearest
    i = k === "Home" ? 0 : k === "End" ? n - 1 : i < 0 ? (k === "ArrowLeft" ? n - 1 : 0) : Math.max(0, Math.min(n - 1, i + (k === "ArrowLeft" ? -1 : 1)));
    svg._kt = ts[i];
    const words = show(ts[i]);
    if (words) announce(words.replace(/[▲▼◆]\s*/g, ""));
  };
  svg.onblur = () => { if (svg._kt == null) return; svg._kt = null; clear(); };
}
/* where a point of a chart is on the screen, for its tooltip: x, y in the svg's own units */
const svgPoint = (svg, x, y) => { const r = svg.getBoundingClientRect(), k = r.width / svg._geom.w; return {clientX: r.left + x * k, clientY: r.top + y * k}; };
const tipWords = nodes => nodes.filter(Boolean).map(n => n.textContent.trim()).filter(Boolean).join(". ");
function median(a) { const s = [...a].sort((x, y) => x - y); return s[Math.floor(s.length / 2)]; }
/* the Labs tab (#172): the chosen labs as charts (each with its table), or every lab with ranges as a heatmap */
function renderLabs() {
  const hm = state.labsView === "heatmap";
  // no lab results at all (#199): what would be here, and where they come from, in place of the charts and heatmap
  const none = !SERIES.some(s => !s.watch && s.pts.length);
  $("#labsNone")?.remove();
  $(".labs-head").hidden = none;
  $("#labsChart").hidden = hm || none; $("#labsHeatmap").hidden = !hm || none;
  for (const x of document.querySelectorAll("#view-labs .chart-only")) x.hidden = hm;   // the header's overlays, Legend and ⓘ (#279)
  if (none) {
    const e = emptyState("Lab results appear here, each charted over time against its reference range.",
      canEdit() ? {href: `sources.html?profile=${PROFILE.id}#manual`, text: "Add lab results"} : null);
    e.id = "labsNone";
    $("#view-labs").append(e);
    return save();
  }
  for (const b of $("#labsView").children) b.setAttribute("aria-pressed", b.dataset.view === state.labsView);
  if (hm) renderHeatmap(); else { renderSeriesList(); renderCharts(); }
  save();
}
$("#labsView").onclick = ev => { const b = ev.target.closest("button[data-view]"); if (!b || b.dataset.view === state.labsView) return; hideTip(); state.labsView = b.dataset.view; renderLabs(); tabAddress(true); };   // #labs or #labs-heatmap (#208), Back returning to the view before (#442)
function showSeries(name) { state.sel.add(name); state.labsView = "chart"; closeDrawer(); pickTab("labs"); }

