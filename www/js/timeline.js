/* Sanguity · Timeline tab: lane chart with zoom and pan, stats, event list.
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
/* ---------------------------------------------------------------- timeline */
const tlSvg = $("#tl");
function tlScale(w) { const L = w < 600 ? 96 : 150, R = w - (w < 600 ? 6 : 16); return {L, R, x: t => L + (t - state.from) / (state.to - state.from) * (R - L), t: x => state.from + (x - L) / (R - L) * (state.to - state.from)}; }
function ticks(a, b, w) {
  const span = b - a, out = [], target = Math.max(3, Math.floor(w / 110));
  const steps = [[DAY,"d"],[7*DAY,"w"],[30.44*DAY,"m"],[91.3*DAY,"q"],[182.6*DAY,"h"],[365.25*DAY,"y"],[730.5*DAY,"2y"]];
  let st = steps.find(s => span / s[0] <= target) || steps[steps.length - 1];
  const d = new Date(a);
  if (st[1] === "d" || st[1] === "w") { d.setHours(0,0,0,0); const inc = st[1] === "d" ? 1 : 7; while (d.getTime() < b) { if (d.getTime() >= a) out.push([d.getTime(), d.toLocaleDateString(undefined,{month:"short",day:"numeric"})]); d.setDate(d.getDate() + inc); } }
  else { const mStep = {m:1,q:3,h:6,y:12,"2y":24}[st[1]]; d.setDate(1); d.setHours(0,0,0,0); d.setMonth(Math.floor(d.getMonth()/mStep)*mStep);
    while (d.getTime() < b) { if (d.getTime() >= a) out.push([d.getTime(), mStep >= 12 ? String(d.getFullYear()) : (d.getMonth() === 0 ? d.toLocaleDateString(undefined,{month:"short",year:"numeric"}) : d.toLocaleDateString(undefined,{month:"short"}))]); d.setMonth(d.getMonth() + mStep); } }
  return out;
}
function renderTimeline() {
  const w = Math.max(320, tlSvg.parentElement.clientWidth - 32);
  const lanes = CATS.filter(c => c.k !== "stay" && state.cats.has(c.k) && hasLane(c));
  const laneH = 34, top = msInRange(state.from, state.to) ? 22 : 8, axisH = 26, h = top + Math.max(1, lanes.length) * laneH + axisH;
  tlSvg.setAttribute("viewBox", `0 0 ${w} ${h}`); tlSvg.setAttribute("height", h);
  const S = tlScale(w), g = [];
  const plotB = top + lanes.length * laneH;
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
    const y = top + i * laneH + laneH / 2;
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
      if (e.end && e.te - e.ts > DAY && S.x(e.te) - x > 4) {
        g.push(el("svg:line", {x1: x, x2: Math.min(S.R, S.x(e.te)), y1: y, y2: y, stroke: c.color, "stroke-width": 2, "stroke-opacity": .45, "stroke-linecap": "round", "pointer-events": "none"}));
      }
      const r = e.k === "lab" ? Math.min(9, 3.5 + Math.sqrt(e.n || 1) * .9) : e.k === "auto_msg" || e.k === "appt_msg" ? 3.5 : 5;
      const fill = e.k === "appt_msg" || e.k === "auto_msg" ? "var(--surface)" : c.color;
      g.push(el("svg:circle", {class: "ev" + (state.selEv === e.id ? " sel" : ""), cx: x, cy: y, r, fill, stroke: e.k === "appt_msg" || e.k === "auto_msg" ? c.color : "var(--surface)", "stroke-width": e.k === "appt_msg" || e.k === "auto_msg" ? 1.5 : 1.5, "data-id": e.id}));
      if (e.abn && e.k === "lab") g.push(el("svg:circle", {cx: x, cy: y - r - 4, r: 2, fill: "var(--crit)", "pointer-events": "none"}));
    }
  });
  if (!lanes.length) g.push(el("svg:text", {x: w/2, y: top + 20, "text-anchor": "middle", text: "No categories selected"}));
  tlSvg.replaceChildren(...g);
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
  drag = {x: ev.clientX, from: state.from, to: state.to, moved: false}; tlSvg.setPointerCapture(ev.pointerId);
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
  const id = +ds.id; if (id) showEvTip(byId.get(id), ev); else hideTip();
});
tlSvg.addEventListener("pointerup", ev => {
  ptrs.delete(ev.pointerId);
  if (pinch) { if (ptrs.size < 2) { pinch = null; drag = null; renderAll(); } return; }
  tlSvg.classList.remove("dragging");
  if (drag && drag.moved) { drag = null; renderAll(); return; }
  drag = null; const id = +ev.target?.dataset?.id; if (id) openEvent(byId.get(id));
});
tlSvg.addEventListener("pointerleave", hideTip);
tlSvg.addEventListener("dblclick", () => applyPreset("all"));
$("#ftoggle").onclick = () => { const f = $(".filters"), open = f.classList.toggle("open"); $("#ftoggle").setAttribute("aria-expanded", open); };
let lastW = innerWidth;
addEventListener("resize", () => { if (innerWidth === lastW) return; lastW = innerWidth; clearTimeout(window._rz); window._rz = setTimeout(renderAll, 120); });

/* stats + list */
function renderStats() {
  const v = EV.filter(visible); const c = k => v.filter(e => k.includes(e.k)).length;
  const abn = v.filter(e => e.k === "lab").reduce((a, e) => a + (e.abn || 0), 0);
  const items = [[c(["lab"]), "lab draw days"], [v.filter(e => e.k === "lab").reduce((a, e) => a + (e.n || 0), 0), "lab results"], [abn, "out-of-range values"], [c(["imaging","procedure","study"]), "imaging, procedures & reports"], [c(["stay"]), "hospital stays"], [c(["message","appt_msg","auto_msg"]), "conversations"]];
  if (EV.some(e => e.k === "visit")) items.push([c(["visit"]), "visits"]);
  $("#stats").replaceChildren(...items.map(([n, l]) => el("div", {class: "stat"}, el("div", {class: "n", text: n.toLocaleString()}), el("div", {class: "l", text: l}))));
}
$("#sortSeg").onclick = ev => { const b = ev.target.closest("button[data-sort]"); if (!b) return; state.asc = b.dataset.sort === "asc"; renderList(); save(); };
function renderList() {
  const v = EV.filter(visible).sort((a, b) => state.asc ? a.ts - b.ts : b.ts - a.ts);
  $("#listTitle").textContent = `Events (${v.length.toLocaleString()})`;
  for (const b of $("#sortSeg").children) b.setAttribute("aria-pressed", (b.dataset.sort === "asc") === state.asc);
  const list = $("#list"); list.replaceChildren();
  if (!v.length) { list.append(el("div", {class: "empty", text: "Nothing matches these filters."})); return; }
  let month = "", frag = document.createDocumentFragment(), shown = 0;
  const LIMIT = 600;
  for (const e of v) {
    if (shown++ >= LIMIT) { frag.append(el("div", {class: "empty", text: `${v.length - LIMIT} ${state.asc ? "newer" : "older"} events not shown — narrow the date range or search.`})); break; }
    const m = fmtMonth(e.ts); if (m !== month) { month = m; frag.append(el("div", {class: "month", text: m})); }
    const c = CAT_OF[e.k];
    frag.append(el("div", {class: "row" + (state.selEv === e.id ? " sel" : ""), tabindex: 0, onclick: () => openEvent(e), onkeydown: k => { if (k.key === "Enter") openEvent(e); }},
      el("span", {class: "d", text: fmtD(e.ts)}), el("span", {class: "key", style: {background: c.color}}),
      el("span", {class: "t", text: e.title}),
      el("span", {class: "b"}, e.abn ? el("span", {class: "badge abn", text: `${e.abn} out of range`}) : el("span", {class: "badge", text: KIND_LABEL[e.k]})),
      el("span", {class: "s", text: e.sub || ""})));
  }
  list.append(frag);
}

