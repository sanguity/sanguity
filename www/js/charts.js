/* Sanguity · shared chart behaviour: drag/pinch/scroll pan-zoom (Trends, Health, heatmap) and milestone markers.
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
/* drag to pan, scroll wheel or pinch to zoom, double-click to reset — shared by Trends, Health and the lab heatmap.
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
// Trends: every chart shares state.from/to, so they all move together
const chartsBox = $("#charts");
const inWindow = s => s.pts.filter(p => p.ts >= state.from && p.ts <= state.to);
panZoom(chartsBox, {...mainWindow,
  plot: ev => { const s = ev.target.closest?.(".chart svg"); return s && s._geom ? s : null; }, span: svgSpan,
  start: () => chartsBox.querySelectorAll(".chart svg").forEach(o => o._hover && o._hover(null)),
  draw: () => chartsBox.querySelectorAll(".chart svg").forEach(svg => { if (svg._s && svg._geom) drawChart(svg, svg._s, inWindow(svg._s), svg._read); }),
  end: () => { renderFilters(); renderCharts(); save(); }});
// Lab heatmap: same date window as Trends; columns are draw days, so the time axis is the column area
panZoom($("#hmWrap"), {...mainWindow,
  plot: ev => ev.target.closest?.("#hm, #hmAxis") && $("#hm")._geom ? $("#hm") : null, span: svgSpan,
  draw: () => renderHeatmap(), end: () => { renderFilters(); renderHeatmap(); save(); }});   // renderHeatmap lives in a later file: call it, do not reference it at load
// Health: all charts share the Health tab's own period
panZoom($("#view-health"), {minSpan: 3 * DAY,
  plot: ev => { const s = ev.target.closest?.("svg"); return s && s._draw && s._geom ? s : null; }, span: svgSpan,
  get: () => hRange(), set: (a, b) => { hState.custom = [a, b]; hState.period = "custom"; },
  draw: () => { const [a, b] = hRange(); $("#view-health").querySelectorAll("svg").forEach(s => s._draw && s._draw(a, b)); },
  end: () => renderHealth(), reset: () => { hState.period = "all"; hState.custom = null; hSave(); renderHealth(); }});
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
function median(a) { const s = [...a].sort((x, y) => x - y); return s[Math.floor(s.length / 2)]; }
function niceTicks(lo, hi, n) { const span = hi - lo, step0 = span / n, mag = Math.pow(10, Math.floor(Math.log10(step0))), step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => span / s <= n) || 10 * mag; const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10)); return out; }
function renderLabs() { renderSeriesList(); renderCharts(); save(); }
function showSeries(name) { state.sel.add(name); closeDrawer(); switchTab("labs"); }

