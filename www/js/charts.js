/* Sanguity · shared chart behaviour: drag/pinch/scroll pan-zoom (Labs' charts and heatmap, Body and activity), milestone
   markers, and the Labs tab's Chart / Heatmap switch.
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
/* drag to pan, scroll wheel or pinch to zoom, double-click to reset — shared by Labs (both views) and Body and activity.
   plot(ev) -> the chart under the pointer (or null); span(plot) -> [left, width] of its time axis in client px;
   get() -> [a, b]; set(a, b) updates state; draw() redraws live; end() does the full render once the gesture stops. */
function panZoom(box, {plot, span, get, set, draw, end, reset, start, minSpan = 2 * DAY}) {
  const ptrs = new Map(); let drag = null, pinch = null, raf = 0, dirty = false, finT = 0, justDragged = false;
  const timeAt = (p, cx) => { const [l, w] = span(p), [a, b] = get(); return a + (cx - l) / w * (b - a); };
  const apply = (a, b) => {
    if (b - a < minSpan) { const m = (a + b) / 2; a = m - minSpan / 2; b = m + minSpan / 2; }
    set(a, b); dirty = true; cancelAnimationFrame(raf); raf = requestAnimationFrame(draw);
  };
  const finish = () => { clearTimeout(finT); if (!dirty) return; dirty = false; cancelAnimationFrame(raf); end(); };
  box.addEventListener("pointerdown", ev => {
    const p = plot(ev); if (!p || (ev.pointerType === "mouse" && ev.button !== 0)) return;
    ptrs.set(ev.pointerId, ev.clientX);
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
  box.addEventListener("dblclick", ev => { if (plot(ev)) { ev.preventDefault(); clearTimeout(finT); dirty = false; reset(); } });
  box.addEventListener("wheel", ev => {
    const p = plot(ev); if (!p) return;
    const [a, b] = get();
    if (!ev.ctrlKey && Math.abs(ev.deltaX) > Math.abs(ev.deltaY)) {   // horizontal swipe / shift+wheel: pan
      const dt = ev.deltaX / span(p)[1] * (b - a); apply(a + dt, b + dt);
    } else {                                  // scroll wheel and trackpad pinch (arrives as ctrl+wheel): zoom around the pointer, like the Timeline
      const t = timeAt(p, ev.clientX), k = Math.exp(ev.deltaY * 0.0015 * (ev.deltaMode === 1 ? 30 : 1));
      apply(t - (t - a) * k, t + (b - t) * k);
    }
    ev.preventDefault(); hideTip(); clearTimeout(finT); finT = setTimeout(finish, 300);
  }, {passive: false});
}
const svgSpan = svg => { const r = svg.getBoundingClientRect(), g = svg._geom, k = r.width / g.w; return [r.left + g.L * k, (g.R - g.L) * k]; };
const mainWindow = {get: () => [state.from, state.to], set: (a, b) => { state.from = a; state.to = b; state.preset = "custom"; }, reset: () => applyPreset("all")};
// Labs, as charts: every chart shares state.from/to, so they all move together
const chartsBox = $("#charts");
const inWindow = s => s.pts.filter(p => p.ts >= state.from && p.ts <= state.to);
panZoom(chartsBox, {...mainWindow,
  plot: ev => { const s = ev.target.closest?.(".chart svg"); return s && s._geom ? s : null; }, span: svgSpan,
  start: () => chartsBox.querySelectorAll(".chart svg").forEach(o => o._hover && o._hover(null)),
  draw: () => chartsBox.querySelectorAll(".chart svg").forEach(svg => { if (svg._s && svg._geom) drawChart(svg, svg._s, inWindow(svg._s), svg._read); }),
  end: () => { renderFilters(); renderCharts(); save(); }});
// Labs, as a heatmap: the same date window; columns are draw days, so the time axis is the column area
panZoom($("#hmWrap"), {...mainWindow,
  plot: ev => ev.target.closest?.("#hm, #hmAxis") && $("#hm")._geom ? $("#hm") : null, span: svgSpan,
  draw: () => renderHeatmap(), end: () => { renderFilters(); renderHeatmap(); save(); }});   // renderHeatmap lives in a later file: call it, do not reference it at load
// Body and activity: all its charts share the records bar's period too ("All" there is the watch and scale data's span)
panZoom($("#view-health"), {...mainWindow, minSpan: 3 * DAY,
  plot: ev => { const s = ev.target.closest?.("svg"); return s && s._draw && s._geom ? s : null; }, span: svgSpan,
  get: () => hRange(),
  draw: () => { const [a, b] = hRange(); $("#view-health").querySelectorAll("svg").forEach(s => s._draw && s._draw(a, b)); },
  end: () => { renderFilters(); renderHealth(); save(); }});
/* milestone rules on a time chart: accent dashed line from y0 to y1, label on its own row at labelY (null = no labels) */
const msInRange = (a, b) => MILESTONES.some(m => { const t = dayTs(m.date); return t >= a && t <= b; });
function msMarks(g, x, a, b, y0, y1, R, labelY) {
  const textW = s => s.length * 6.4 + 8;   // ~10.5px semibold
  const groups = [];   // a milestone whose label would run into the previous label joins it: "Diagnosis · Treatment start"
  for (const m of MILESTONES) {
    const t = dayTs(m.date); if (t < a || t > b) continue;
    const mx = x(t);
    g.push(el("svg:line", {class: "msmark", x1: mx, x2: mx, y1: y0, y2: y1}));
    const last = groups[groups.length - 1];
    if (last && mx < last.x + textW(last.items.map(([n]) => n.label).join(" · "))) last.items.push([m, t]); else groups.push({x: mx, items: [[m, t]]});
  }
  if (labelY == null) return;
  for (const {x: mx, items} of groups) {
    const text = items.map(([m]) => m.label).join(" · "), flip = mx + textW(text) > R;   // keep the label inside the chart
    g.push(el("svg:text", {class: "mslabel", x: flip ? mx - 4 : mx + 4, y: labelY, "text-anchor": flip ? "end" : "start"}, text,
      el("svg:title", {text: items.map(([m, t]) => `${m.label} · ${fmtD(t)}`).join("\n")})));
  }
}
/* ---- chart accessibility (#60): every chart has a table to show instead, out-of-range results are marked by shape
   as well as colour, and a chart takes focus, its points stepped through from the keyboard.
   flagWord(f): a result's flag in words, with its mark: "▲ High", "▼ Low", or "◆ Flagged" for any other flag;
   flagNodes(f): the same, the mark hidden from screen readers (they say the word). */
const FLAG_OF = f => !f ? null : /^h/i.test(f) ? ["▲", "High"] : /^l/i.test(f) ? ["▼", "Low"] : ["◆", "Flagged"];
const flagWord = f => (FLAG_OF(f) || []).join(" ");
const flagNodes = f => { const w = FLAG_OF(f); return w ? [el("span", {"aria-hidden": "true", text: w[0] + " "}), w[1]] : []; };
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
   shown with flagWord's mark and in the out-of-range colour). It scrolls, so it takes focus to be scrolled by keyboard,
   named by its columns (#202) */
function chartTable(heads, rows, cls = "") {
  const td = c => Array.isArray(c) ? el("td", {class: c[1] ? "abn" : null}, c[0], c[1] ? " " : null, ...flagNodes(c[1])) : el("td", {text: c ?? ""});
  return el("div", {class: "tablewrap chart-table", tabindex: 0, role: "region", "aria-label": `${heads.slice(1).join(", ")} by ${heads[0].toLowerCase()}`}, el("table", {class: "data " + cls},
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
function niceTicks(lo, hi, n) { const span = hi - lo, step0 = span / n, mag = Math.pow(10, Math.floor(Math.log10(step0))), step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => span / s <= n) || 10 * mag; const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10)); return out; }
/* the Labs tab (#172): the chosen labs as charts (each with its table), or every lab with ranges as a heatmap */
function renderLabs() {
  const hm = state.labsView === "heatmap";
  // no lab results at all (#199): what would be here, and where they come from, in place of the charts and heatmap
  const none = !SERIES.some(s => !s.watch && s.pts.length);
  $("#labsNone")?.remove();
  $(".labs-head").hidden = none;
  $("#labsChart").hidden = hm || none; $("#labsHeatmap").hidden = !hm || none;
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
$("#labsView").onclick = ev => { const b = ev.target.closest("button[data-view]"); if (!b || b.dataset.view === state.labsView) return; hideTip(); state.labsView = b.dataset.view; renderLabs(); tabAddress(); };   // #labs or #labs-heatmap (#208)
function showSeries(name) { state.sel.add(name); state.labsView = "chart"; closeDrawer(); pickTab("labs"); }

