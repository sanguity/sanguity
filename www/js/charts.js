/* Sanguity · shared chart behaviour: drag/pinch/scroll pan-zoom (Labs' charts and heatmap, the Timeline, Body and activity), milestone
   markers, and the Labs tab's Chart / Heatmap switch.
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
/* drag to pan, scroll wheel or pinch to zoom, double-click to reset — shared by Labs (both views), the Timeline and Body and activity.
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
  draw: () => { chartsBox.querySelectorAll(".chart svg").forEach(svg => { if (svg._s && svg._geom) drawChart(svg, svg._s, inWindow(svg._s), svg._read, true); }); labAxisDraw(); drawTrack(); stripDraw(); },   // and their one axis (#293), and the event track over them (#294)
  end: () => { renderFilters(); renderCharts(); save(); }});
// Labs, as a heatmap: the same date window; columns are draw days, so the time axis is the column area
panZoom($("#hmWrap"), {...mainWindow,
  plot: ev => ev.target.closest?.("#hm, #hmAxis") && $("#hm")._geom ? $("#hm") : null, span: svgSpan, data: () => seriesSpan(hmLabs().filter(labMatch)),
  draw: () => { renderHeatmap(true); stripDraw(); }, end: () => { renderFilters(); renderHeatmap(); save(); }});   // renderHeatmap lives in a later file: call it, do not reference it at load
// the Timeline (#631): its lanes drawn again once a frame, and the overview strip's window with them; its list once let go
panZoom(tlSvg, {...mainWindow, plot: () => tlSvg._geom ? tlSvg : null, span: svgSpan, data: () => [state.min, Math.max(state.max, state.last)],
  draw: () => { renderTimeline(true); stripDraw(); }, end: () => renderAll()});
// Body and activity: all its charts share the records bar's period too ("All" there is the span of what it draws, health.js hSpan)
panZoom($("#view-health"), {...mainWindow, minSpan: 3 * DAY,
  plot: ev => { const s = ev.target.closest?.("svg"); return s && s._draw && s._geom ? s : null; }, span: svgSpan,
  get: () => hRange(), data: () => WATCH ? hSpan() : null,
  draw: () => { const [a, b] = hRange(); $("#view-health").querySelectorAll("svg").forEach(s => s._draw && s._draw(a, b, true)); stripDraw(); },
  end: () => { renderFilters(); renderHealth(); save(); }});
/* ---- the overview strip (#295): under the period bar on Labs, Body and activity and the Timeline (on a phone, in the
   charts' header with the period's pill: core.js phoneBar). Across every year (All's dates, core.js allSpan), how many of
   the tab's records each stretch holds (chartkit.js ckStripBins: lab results, the phone's readings, every record), the
   milestones as flags, and the period as a window: dragged by its middle to move it, by an edge to stretch it, a press
   beside it bringing it there, a milestone's flag picked "Since" it; for the keyboard its edges are two range inputs, a
   day a step. The charts follow as it moves; hovering a stretch says its count */
const STRIP_SAYS = {labs: "lab results", health: "readings", timeline: "records"};
const strip = $("#strip"), stripSvg = strip.querySelector("svg"), stripIn = [...strip.querySelectorAll("input")];
let stripAt = null, stripMemo = null, stripRaf = 0;   // the strip as last drawn: its scale, bins and window
// (Labs': each result its charts hold, not each report, #656)
const stripItems = tab => tab === "timeline" ? EV.filter(e => !e.hidden).map(e => [e.ts, 1]) : SERIES.filter(s => !s.watch === (tab === "labs")).flatMap(s => s.pts.map(p => [p.ts, 1]));
const stripWin = () => state.tab === "health" ? hRange() : [state.from, state.to];   // Body's All: the span of what it draws (health.js)
// a stretch in words: "March 2025", "Week of Mar 3, 2025", "Jan ’25 – Mar ’25"
function stripStretch({edges, step: [n, u]}, i) {
  const t = edges[i];
  return u === "y" ? String(zdate(t).getFullYear()) : u === "m" ? (n === 1 ? fmtMonth(t) : `${ckMonth(t)} – ${ckMonth(edges[i + 1] - DAY)}`) : (n === 7 ? "Week of " : "") + fmtD(t);
}
function stripDraw() {
  const [a, b] = allSpan(), says = DATA && isFinite(a + b) && STRIP_SAYS[state.tab], w = says ? strip.clientWidth || strip.parentNode.clientWidth : 0;   // (no records with a date: none)
  const x = ckScale(a, b, 4, w - 4), key = [state.tab, a, b, w].join();
  if (says && (stripMemo?.key !== key || stripMemo.ev !== EV)) stripMemo = {key, ev: EV, bins: ckStripBins(stripItems(state.tab), a, b, x)};
  strip.hidden = !says || !stripMemo.bins.n.some(Boolean);
  if (strip.hidden || !w) return;
  const B = 23, h = ckStripH(B), [wa, wb] = stripWin(), clip = t => Math.max(a, Math.min(b, t)), g = [];   // taller with larger text (#641)
  ckStripBars(g, stripMemo.bins, x, 5, B);
  ckStripDates(g, a, b, x, B, w - 6);
  for (const m of MILESTONES) {
    const t = dayTs(m.date); if (t < a || t > b) continue;
    const mx = x(t);
    g.push(el("svg:line", {class: "msflag", x1: mx, x2: mx, y1: 1, y2: B}), el("svg:path", {class: "msflag-head", d: `M${mx},1L${mx + 6},3.5L${mx},6Z`}));
  }
  ckStripWindow(g, x(clip(wa)), x(clip(wb)), h, B);
  stripSvg.setAttribute("viewBox", `0 0 ${w} ${h}`); stripSvg.setAttribute("height", h);
  stripSvg.replaceChildren(...g);
  const step = stripStretch(stripMemo.bins, 0).startsWith("Week") ? "week" : {d: "day", m: stripMemo.bins.step[0] === 3 ? "quarter" : "month", y: "year"}[stripMemo.bins.step[1]];
  stripSvg.setAttribute("aria-label", `${says[0].toUpperCase() + says.slice(1)} per ${step}, ${fmtD(a + DAY)} to ${fmtD(b - DAY)}, the period shown as a window on them: ${fmtD(clip(wa))} to ${fmtD(clip(wb))}.`);
  stripAt = {x, a, b, wa, wb, w, h};
  const day = t => Math.round((clip(t) - a) / DAY);
  stripIn.forEach((inp, i) => { inp.min = 0; inp.max = day(b); inp.value = day(i ? wb : wa); inp.setAttribute("aria-valuetext", fmtD(clip(i ? wb : wa))); });
}
/* the period from the strip: shown at once, the tab drawn again once a frame (all of it when it's let go) */
function stripSet(a, b, now) {
  const {a: a0, b: b0} = stripAt, w = Math.min(b - a, b0 - a0);   // kept on the strip, as wide as it at most
  if (a < a0) [a, b] = [a0, a0 + w]; else if (b > b0) [a, b] = [b0 - w, b0];
  state.from = stripAt.wa = a; state.to = stripAt.wb = b; state.preset = "custom";
  cancelAnimationFrame(stripRaf);
  if (now) renderAll(); else stripRaf = requestAnimationFrame(renderAll);
}
stripIn.forEach((inp, i) => inp.oninput = () => {
  const t = stripAt.a + inp.valueAsNumber * DAY, {wa, wb} = stripAt;
  stripSet(i ? Math.min(wa, t - 2 * DAY) : t, i ? t : Math.max(wb, t + 2 * DAY), true);
});
let stripDrag = null;
const stripPx = ev => { const r = stripSvg.getBoundingClientRect(); return [(ev.clientX - r.left) * stripAt.w / r.width, (ev.clientY - r.top) * stripAt.h / r.height]; };
// what a press at px takes: an edge ("a", "b"; within 8 px outside the window, or 6 inside one 16 px wide or more), the window to move ("mid"), or null beside it
function stripGrab(px) {
  const xa = stripAt.x(stripAt.wa), xb = stripAt.x(stripAt.wb), wide = xb - xa >= 16;
  if (px < xa) return xa - px <= 8 ? "a" : null;
  if (px > xb) return px - xb <= 8 ? "b" : null;
  return wide && px - xa <= 6 ? "a" : wide && xb - px <= 6 ? "b" : "mid";
}
const stripMs = (px, py) => py < 9 ? MILESTONES.find(m => Math.abs(stripAt.x(dayTs(m.date)) - px) <= 5) : null;
strip.addEventListener("pointerdown", ev => {
  if (!stripAt || ev.button || ev.target.matches("input")) return;
  const [px, py] = stripPx(ev), t = stripAt.x.inv(px), ms = stripMs(px, py);
  // beside it: brought there as it's let go or dragged, not as a finger lands, which may be the page's scroll (#656)
  const grab = stripGrab(px) || (ms ? null : "beside");
  stripDrag = {grab, t, wa: stripAt.wa, wb: stripAt.wb, was: [state.from, state.to, state.preset], ms, moved: false, id: ev.pointerId};
  strip.setPointerCapture(ev.pointerId); hideTip(); ev.preventDefault();
});
const stripThere = (d, t) => { const half = (d.wb - d.wa) / 2; stripSet(t - half, t + half); d.wa = stripAt.wa; d.wb = stripAt.wb; d.grab = "mid"; };
strip.addEventListener("pointermove", ev => {
  if (!stripAt) return;
  const [px, py] = stripPx(ev), t = stripAt.x.inv(px), d = stripDrag;
  if (!d) {   // hovering: the stretch's count, or the milestone's name, and how a press would take it
    const ms = stripMs(px, py), bins = stripMemo.bins, i = bins.edges.findLastIndex(e => e <= t);
    strip.dataset.grab = ms ? "ms" : stripGrab(px) || "";
    if (ms) return showTip([el("div", {class: "v", text: "Since " + lcLabel(ms.label)}), el("div", {class: "s", text: fmtD(dayTs(ms.date))})], ev);
    return i >= 0 && i < bins.n.length ? showTip([el("div", {class: "v", text: `${bins.n[i].toLocaleString("en-US")} ${STRIP_SAYS[state.tab]}`}), el("div", {class: "s", text: stripStretch(bins, i)})], ev) : hideTip();
  }
  if (d.id !== ev.pointerId || (!d.moved && Math.abs(t - d.t) < Math.abs(stripAt.x.inv(4) - stripAt.x.inv(0)))) return;
  d.moved = true;
  if (d.grab === "beside") stripThere(d, d.t);
  const dt = t - d.t;
  const at = Math.max(stripAt.a, Math.min(stripAt.b, t));
  if (d.grab === "a") stripSet(Math.min(at, d.wb - 2 * DAY), d.wb);
  else if (d.grab === "b") stripSet(d.wa, Math.max(at, d.wa + 2 * DAY));
  else if (d.grab === "mid") stripSet(d.wa + dt, d.wb + dt);
});
/* a finger's (or a pen's) tap sends its click after it's let go, where it was let go: by then the period has moved, and
   the pill's words and Reset with it, so the click lands on whatever control is under the finger now (#726; no
   preventDefault() on pointerdown or touchend stops it). The first click in the next 600 ms is eaten, before anything
   else hears it, unless another press comes first */
function stripNoClick() {
  const eat = ev => { ev.preventDefault(); ev.stopImmediatePropagation(); off(); };
  const off = () => { clearTimeout(t); removeEventListener("click", eat, true); removeEventListener("pointerdown", off, true); }, t = setTimeout(off, 600);
  addEventListener("click", eat, true); addEventListener("pointerdown", off, true);
}
const stripUp = ev => {
  const d = stripDrag; if (!d || d.id !== ev.pointerId) return;
  stripDrag = null;
  if (ev.type === "pointercancel") {   // the browser took the gesture (a finger's scroll): the period as it was (#656)
    if (d.moved) { [state.from, state.to, state.preset] = d.was; cancelAnimationFrame(stripRaf); renderAll(); }
    return;
  }
  if (ev.pointerType !== "mouse") stripNoClick();
  if (!d.moved && d.ms) return applyPreset(msKey(d.ms));   // a milestone's flag: since it
  if (!d.moved && d.grab === "beside") stripThere(d, d.t);
  cancelAnimationFrame(stripRaf); renderAll();
};
strip.addEventListener("pointerup", stripUp); strip.addEventListener("pointercancel", stripUp);
strip.addEventListener("pointerleave", () => { if (!stripDrag) hideTip(); });

/* milestone rules on a time chart: a dashed line in --milestone (never the accent, #334) from y0 to y1, label on its own
   row at labelY (null = no labels). A label stays inside the plot, x(a) to R (#312): after its rule, before it near the
   right edge, against that edge, or shortened with "…" to the room there is; its <title> has the full names and dates.
   Returns the milestones shown, in words for the chart's aria-label ("" when none). */
const msInRange = (a, b) => MILESTONES.some(m => { const t = dayTs(m.date); return t >= a && t <= b; });
/* the milestones from a to b in words, as a chart's aria-label says them ("" when none): "Milestones: Diagnosis, Mar 3, 2025; …." */
function msSay(a, b) {
  const shown = MILESTONES.map(m => [m.label, dayTs(m.date)]).filter(([, t]) => t >= a && t <= b).map(([l, t]) => `${l}, ${fmtD(t)}`);
  return shown.length ? `Milestone${shown.length > 1 ? "s" : ""}: ${shown.join("; ")}.` : "";
}
function msMarks(g, x, a, b, y0, y1, R, labelY) {
  const L = x(a), tw = s => labelW(s, "mslabel"), names = items => items.map(([m]) => m.label).join(" · ");
  const groups = [];   // a milestone whose label would run into the previous label joins it: "Diagnosis · Treatment start"
  for (const m of MILESTONES) {
    const t = dayTs(m.date); if (t < a || t > b) continue;
    const mx = x(t);
    g.push(el("svg:line", {class: "msmark", x1: mx, x2: mx, y1: y0, y2: y1}));
    const last = groups[groups.length - 1];
    if (last && mx < last.x + tw(names(last.items)) + 12) last.items.push([m, t]); else groups.push({x: mx, items: [[m, t]]});
  }
  const words = msSay(a, b);
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
   flagNodes(f): a result's flag in words, with its mark (ranges.js flagWord: "▲ High", "▼ Low", or "◆ Flagged" for any
   other flag), the mark hidden from screen readers (they say the word). */
const flagNodes = (f, words) => { const w = FLAG_OF(f); return w ? [el("span", {"aria-hidden": "true", text: w.mark + " "}), words || w.word] : []; };   // words: another wording (#234)
/* Chart | Table in a chart's header: a pick-one pair (#193). key: what state.tables remembers it by (a lab's name,
   or "#…" for the other charts); what: the chart, for its label ("Show Hemoglobin as"); redraw() draws it again;
   graphic: the word for the picture ("Heatmap"). Focus stays on the choice once it's redrawn. */
function viewSeg(key, what, redraw, graphic = "Chart") {
  const table = state.tables.has(key);
  const pick = want => {
    if (want === table) return;
    const top = seg.getBoundingClientRect().top;   // where it was, for once the charts are drawn (store.js keepSight, #720)
    if (want) state.tables.add(key); else state.tables.delete(key);
    hideTip(); redraw();
    const b = [...document.querySelectorAll(".view-seg")].find(s => s.dataset.key === key)?.querySelector("[aria-pressed=true]");
    if (b) { b.focus(); keepSight(b, top); }
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
/* a chart's svg as it's made, a tab stop already, with the name chartKeys gives it once it's drawn (label): so focus on
   a chart drawn anew (a resize, the records changed) goes to its twin, found by its id or its name (store.js keepFocus),
   not to the page (#652) */
const chartSvg = (label, attrs) => el("svg:svg", {tabindex: 0, role: "application", "aria-roledescription": "chart", "aria-label": label, ...attrs});
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
/* the Labs tab (#172): the chosen labs as charts (each with its table), every lab with ranges as a heatmap, or up to
   three labs compared within their own ranges (#297, compare.js) */
function renderLabs() {
  const hm = state.labsView === "heatmap", cmp = state.labsView === "compare";
  // no lab results at all (#199): what would be here, and where they come from, in place of the charts and heatmap
  const none = !SERIES.some(s => !s.watch && s.pts.length);
  $("#labsNone")?.remove();
  $(".labs-head").hidden = none;
  $("#labsChart").hidden = hm || cmp || none; $("#labsHeatmap").hidden = !hm || none; $("#labsCompare").hidden = !cmp || none;
  for (const x of document.querySelectorAll("#view-labs .chart-only")) x.hidden = hm || cmp;   // the header's overlays, Legend and ⓘ (#279)
  if (none) {
    const e = emptyState("Lab results appear here, each charted over time against its reference range.",
      canEdit() ? {href: `sources.html?profile=${PROFILE.id}#manual`, text: "Add lab results"} : null);
    e.id = "labsNone";
    $("#view-labs").append(e);
    return save();
  }
  for (const b of $("#labsView").children) b.setAttribute("aria-pressed", b.dataset.view === state.labsView);
  if (hm) renderHeatmap(); else if (cmp) renderCompare(); else { renderSeriesList(); renderCharts(); }
  save();
}
$("#labsView").onclick = ev => { const b = ev.target.closest("button[data-view]"); if (!b || b.dataset.view === state.labsView) return; hideTip(); state.labsView = b.dataset.view; renderLabs(); tabAddress(true); };   // #labs, #labs-heatmap or #labs-compare (#208, #297), Back returning to the view before (#442)
/* a lab on Labs as a chart, or as its table, from the drawer (a lab's panel's Chart these results or Show them as a
   table, a result's row): the drawer closes, taking with it what had focus, so focus goes to its chart once it's drawn,
   or to its name over its table, and that's said (#652: it fell to the page, and nothing said the lab was charted) */
function showSeries(name) {
  state.sel.add(name); state.labsView = "chart"; closeDrawer(); pickTab("labs");
  requestAnimationFrame(() => {
    const t = state.tables.has(name), x = [...$("#charts").querySelectorAll(".chart")].find(c => c.dataset.lab === name)?.querySelector(t ? ".hd .lab-open" : "svg.ck");
    if (!x) return;
    x.focus();
    announce(`${nice(name)} ${t ? "shown as a table" : "charted"} on Labs.`);
  });
}

