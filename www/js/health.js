/* Sanguity · Body and activity tab ("health"): watch, sleep and smart-scale charts.
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
/* ---------------------------------------------------------------- body and activity (watch + scale, no hospital data) */
/* its period is the records bar's (#171, core.js); its option, shading hospital stays, is saved per profile. hLoad runs
   once the profile is known; the one setting saved before profiles existed goes to the first profile opened. A
   period saved here before #171 gives way to the records bar's. */
const hState = {stays: true};
const hKey = () => "ht-health" + (PROFILE ? ":" + PROFILE.id : "");
function hLoad() {
  try {
    let raw = localStorage.getItem(hKey());
    if (raw == null && (raw = localStorage.getItem("ht-health")) != null) localStorage.removeItem("ht-health");
    const v = JSON.parse(raw || "{}");
    hState.stays = typeof v.stays === "boolean" ? v.stays : true;
  } catch (e) {}
}
function hSave() { try { localStorage.setItem(hKey(), JSON.stringify({stays: hState.stays})); } catch (e) {} }
function openHealth(a, b) { closeDrawer(); state.from = a; state.to = b; state.preset = "custom"; switchTab("health"); }
function hData() {
  const M = Object.fromEntries(((WATCH || {}).metrics || []).map(m => [m.key, m]));
  const ser = k => (M[k] ? M[k].pts : []).map(([d, v]) => [dayTs(d), v]);
  // drop readings that sit >12% away from the median of the other weights within 90 days (e.g. a profile weight typed into another app)
  const rawW = ser("weight"), cwRaw = (DATA.clinicWeights || []).map(([t, v]) => [dayTs(t.slice(0, 10)), v]);
  const ref = [...rawW, ...cwRaw];
  const scale = rawW.filter(p => { const near = ref.filter(q => q !== p && Math.abs(q[0] - p[0]) < 90 * DAY).map(q => q[1]).sort((x, y) => x - y);
    return near.length < 3 || Math.abs(p[1] - near[near.length >> 1]) / near[near.length >> 1] <= 0.12; });
  const scaleDays = new Set(scale.map(p => isoDay(p[0])));
  const clinic = (DATA.clinicWeights || []).map(([t, v, org]) => [dayTs(t.slice(0, 10)), v, org]).filter(p => !scaleDays.has(isoDay(p[0])));
  const sleep = ser("sleep_h").map(([t, v]) => { const d = isoDay(t), g = k => ((M[k] ? Object.fromEntries(M[k].pts) : {})[d] || 0) / 60; return [t, v, g("sleep_deep_min"), g("sleep_rem_min"), g("sleep_light_min")]; });
  const lo = M.hr_min ? Object.fromEntries(M.hr_min.pts) : {}, hi = M.hr_max ? Object.fromEntries(M.hr_max.pts) : {};
  const hr = ser("hr_avg").map(([t, v]) => [t, v, lo[isoDay(t)], hi[isoDay(t)]]);
  const ex = ((WATCH || {}).exercise || []).map(x => ({t: zParse(x.start), name: (x.name || "").replace(/_/g, " "), kind: x.kind, min: x.minutes || 0}));
  // visits matching a settings rule (e.g. physical therapy) count as workouts: one per rule per day, past visits only.
  // A rule's text is found anywhere in the title, ignoring case; with "regex" it is a regular expression the server
  // checked is safe to run (server/settings.py, whose rule_matches does the same)
  const head = t => (t || "").replace(/\s+/g, " ").trim().slice(0, 200);   // a title's first 200 characters (TITLE_MAX)
  for (const rule of CFG.visitWorkouts) {
    let test;
    if (rule.regex) { try { const re = new RegExp(rule.match, "i"); test = t => re.test(head(t)); } catch (e) { continue; } }
    else { const want = String(rule.match || "").toLowerCase(); if (!want) continue; test = t => head(t).toLowerCase().includes(want); }
    const name = rule.name || rule.match, days = new Set();
    for (const e of DATA.events) {
      if (e.k !== "visit" || !test(e.title) || e.ts > Date.now()) continue;
      const d = isoDay(e.ts); if (days.has(d)) continue; days.add(d);
      ex.push({t: e.ts, name, kind: "visit_workout", min: +rule.minutes || 30, pt: true, rule: name, who: (e.sub || "").split(" · ")[0]});
    }
  }
  ex.sort((p, q) => p.t - q.t);
  const all = [...scale, ...ser("steps"), ...ser("hr_rest"), ...sleep, ...hr].map(p => p[0]);
  return {scale, clinic, sleep, hr, ex, rest: ser("hr_rest"), steps: ser("steps"), fat: ser("body_fat"), lean: ser("lean_mass"), bmr: ser("bmr"),
    hrv: ser("hrv"), first: all.length ? Math.min(...all) : Date.now(), last: all.length ? Math.max(...all) : Date.now()};
}
let H = null;
/* the charts' window: the records bar's period; with "All", from the first watch or scale day to the last */
function hRange() { H = H || hData(); return periodWindow([H.first, H.last]); }
const inR = (pts, a, b) => pts.filter(p => p[0] >= a && p[0] <= b);
const mean = a => a.length ? a.reduce((x, y) => x + y, 0) / a.length : null;
function rolling(pts, days = 7) {  // trailing mean over the last `days` calendar days with data
  const out = []; let j = 0, s = 0;
  for (let i = 0; i < pts.length; i++) {
    s += pts[i][1];
    while (pts[j][0] <= pts[i][0] - days * DAY) { s -= pts[j][1]; j++; }
    if (i - j + 1 >= Math.min(3, days)) out.push([pts[i][0], s / (i - j + 1)]);
  }
  return out;
}
function weekly(pts, fn = mean) {  // [[t, v...]] -> weekly buckets (Monday), averaging each column
  const m = new Map();
  for (const p of pts) { const d = zdate(p[0]); d.setHours(12, 0, 0, 0); d.setDate(d.getDate() - (d.getDay() + 6) % 7); const k = d.getTime(); if (!m.has(k)) m.set(k, []); m.get(k).push(p); }
  return [...m.entries()].sort((a, b) => a[0] - b[0]).map(([k, ps]) => [k + 3 * DAY, ...ps[0].slice(1).map((_, i) => fn(ps.map(p => p[i + 1] || 0)))]);
}

/* one chart: x = time [a, b]; layers: bars (stacked), band, line, dots */
function hChart(host, a, b, {h = 190, layers, fmtY = v => v, unit = "", zero = false, tip}) {
  const svg = el("svg:svg", {role: "img", class: "pz"}); host.append(svg);
  svg._draw = (a, b) => {   // also called live while dragging, with the new window
    if (!svg.isConnected) return;   // re-rendered before this frame came round
    const cs = getComputedStyle(host), w = Math.max(260, host.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight)), L = 44, R = w - 8, T = msInRange(a, b) ? 22 : 8, B = h - 22;
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`); svg.setAttribute("height", h); svg.style.width = "100%"; svg._geom = {L, R, w};
    let lo = Infinity, hi = -Infinity;
    for (const l of layers) for (const p of l.data) {
      if (p[0] < a || p[0] > b) continue;
      const vs = l.type === "bars" ? [l.keys.reduce((s, _, i) => s + (p[i + 1] || 0), 0)] : l.type === "band" ? [p[1], p[2]] : [p[1]];
      for (const v of vs) if (v != null && isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    }
    if (!isFinite(lo)) { svg.replaceChildren(el("svg:text", {x: w / 2, y: h / 2, "text-anchor": "middle", text: "No data in this period"})); return; }
    if (zero || layers.some(l => l.type === "bars")) lo = 0;
    const pad = (hi - lo) * 0.08 || Math.abs(hi) * 0.05 || 1; hi += pad; if (!zero && lo !== 0) lo -= pad;
    const x = t => L + (t - a) / (b - a) * (R - L), y = v => B - (v - lo) / (hi - lo) * (B - T);
    const g = [];
    if (hState.stays) for (const s of STAYS) if (s.te >= a && s.ts <= b) g.push(el("svg:rect", {x: x(Math.max(a, s.ts)), y: T, width: Math.max(2, x(Math.min(b, s.te)) - x(Math.max(a, s.ts))), height: B - T, fill: "var(--stay)"}));
    for (const v of niceTicks(lo, hi, 4)) { g.push(el("svg:line", {class: "gridline", x1: L, x2: R, y1: y(v), y2: y(v)})); g.push(el("svg:text", {x: L - 6, y: y(v) + 4, "text-anchor": "end", text: fmtY(v)})); }
    msMarks(g, x, a, b, T > 8 ? 17 : T, B, R, 13);
    g.push(el("svg:line", {x1: L, x2: R, y1: B, y2: B, stroke: "var(--axis)"}));
    const span = (b - a) / DAY, fmtX = span > 500 ? {year: "numeric"} : span > 75 ? {month: "short"} : {month: "short", day: "numeric"};
    const step = span > 500 ? 365 : span > 75 ? 30.4 : 7, n = Math.max(1, Math.ceil(span / step / Math.max(2, Math.floor((R - L) / 64))));
    const d0 = zdate(a); if (span > 500) d0.setMonth(0, 1); else if (span > 75) d0.setDate(1); d0.setHours(12);
    for (let d = zdate(d0), i = 0; d.getTime() <= b; i++) {
      if (d.getTime() >= a && i % n === 0) g.push(el("svg:text", {x: x(d.getTime()), y: h - 5, "text-anchor": "middle", text: d.toLocaleDateString(undefined, fmtX)}));
      if (span > 500) d.setFullYear(d.getFullYear() + 1); else if (span > 75) d.setMonth(d.getMonth() + 1); else d.setDate(d.getDate() + 7);
    }
    for (const l of layers) {
      const pts = l.data.filter(p => p[0] >= a && p[0] <= b);
      if (l.type === "bars") {
        const bw = Math.max(1, Math.min(18, (R - L) / ((b - a) / (l.every || DAY)) * 0.72));
        for (const p of pts) { let acc = 0; l.keys.forEach((k, i) => { const v = p[i + 1] || 0; if (v <= 0) return; g.push(el("svg:rect", {x: x(p[0]) - bw / 2, y: y(acc + v), width: bw, height: Math.max(0.5, y(acc) - y(acc + v)), fill: l.colors[i]})); acc += v; }); }
      } else if (l.type === "band") {
        const ok = pts.filter(p => p[1] != null && p[2] != null);
        if (ok.length > 1) g.push(el("svg:path", {d: "M" + ok.map(p => `${x(p[0])},${y(p[2])}`).join("L") + "L" + ok.slice().reverse().map(p => `${x(p[0])},${y(p[1])}`).join("L") + "Z", fill: l.color, opacity: .16}));
      } else if (l.type === "line") {
        let d = "", prev = null;
        for (const p of pts) { d += (prev == null || p[0] - prev > (l.gap || 21) * DAY ? "M" : "L") + `${x(p[0])},${y(p[1])}`; prev = p[0]; }
        if (d) g.push(el("svg:path", {d, fill: "none", stroke: l.color, "stroke-width": l.width || 2, "stroke-linejoin": "round", "stroke-linecap": "round", opacity: l.opacity || 1}));
      } else if (l.type === "dots") {
        const r = l.r || (pts.length > 200 ? 1.8 : 3);
        for (const p of pts) g.push(el("svg:circle", {cx: x(p[0]), cy: y(p[1]), r, fill: l.hollow ? "var(--surface)" : l.color, stroke: l.hollow ? l.color : "var(--surface)", "stroke-width": l.hollow ? 1.5 : 1, opacity: l.opacity || 1}));
      }
    }
    const cross = el("svg:line", {class: "cross", y1: T, y2: B, visibility: "hidden"});
    const hit = el("svg:rect", {x: L, y: T, width: R - L, height: B - T, fill: "transparent"});
    hit.addEventListener("pointermove", ev => {
      const r = svg.getBoundingClientRect(), t = a + ((ev.clientX - r.left) * (w / r.width) - L) / (R - L) * (b - a);
      const nodes = tip && tip(t); if (!nodes) { hideTip(); cross.setAttribute("visibility", "hidden"); return; }
      cross.setAttribute("x1", x(nodes.t)); cross.setAttribute("x2", x(nodes.t)); cross.setAttribute("visibility", "visible");
      showTip(nodes.rows, ev);
    });
    hit.addEventListener("pointerleave", () => { hideTip(); cross.setAttribute("visibility", "hidden"); });
    g.push(cross, hit);
    svg.replaceChildren(...g);
  };
  requestAnimationFrame(() => svg._draw(a, b));
  return svg;
}
const nearest = (pts, t, maxDays = 3) => { let best = null; for (const p of pts) if (Math.abs(p[0] - t) <= maxDays * DAY && (!best || Math.abs(p[0] - t) < Math.abs(best[0] - t))) best = p; return best; };
const tipRow = (color, text) => el("div", {class: "s"}, color ? el("span", {class: "k", style: {background: color}}) : null, text);

function renderHealth() {
  const box = $("#view-health"); box.replaceChildren();
  if (!WATCH) { box.append(el("div", {class: "card empty", text: "No watch or scale data yet. It appears here once the phone syncs Health Connect to the receiver."})); return; }
  H = H || hData();
  const [a, b] = hRange(), len = b - a, long = len > 150 * DAY;
  // toolbar (the period is in the records bar)
  box.append(el("div", {class: "toolbar"},
    el("label", {}, el("input", {type: "checkbox", checked: hState.stays || null, onchange: e => { hState.stays = e.target.checked; hSave(); renderHealth(); }}), "Shade hospital stays"),
    el("span", {class: "hint", style: {margin: 0}, text: `Watch and smart scale via Health Connect${WATCH.last_payload ? " · last sync " + fmtDT(WATCH.last_payload) : ""}`})),
    el("p", {class: "hint chart-hint", text: "Drag any chart sideways to move through time — every chart moves together · scroll or pinch to zoom · double-click to show everything."}));
  // tiles
  const prev = pts => inR(pts, a - len, a);
  const wAll = [...H.scale, ...H.clinic].sort((p, q) => p[0] - q[0]), wIn = inR(wAll, a, b);
  const tiles = el("div", {class: "tiles", style: {gridTemplateColumns: "repeat(auto-fill,minmax(170px,1fr))"}});
  const tile = (label, val, unit, delta, href) => tiles.append(el("button", {class: "tile", onclick: () => $(href)?.scrollIntoView({behavior: "smooth", block: "start"})},
    el("span", {class: "lbl", text: label}), el("span", {class: "val"}, val, el("small", {text: unit})), el("span", {class: "delta", text: delta})));
  const chg = (cur, base, f, u = "") => cur != null && base != null ? `${cur >= base ? "▲ +" : "▼ −"}${f(Math.abs(cur - base))}${u}` : "";
  const latestW = wIn.length ? wIn[wIn.length - 1] : wAll[wAll.length - 1];
  tile("Weight", latestW ? latestW[1].toFixed(1) : "–", "lb", wIn.length > 1 ? `${chg(wIn[wIn.length - 1][1], wIn[0][1], v => v.toFixed(1), " lb")} over this period` : latestW ? `last ${fmtD(latestW[0])}` : "", "#h-body");
  const fIn = inR(H.fat, a, b);
  if (H.fat.length) tile("Body fat", fIn.length ? fIn[fIn.length - 1][1].toFixed(1) : "–", "%", fIn.length > 1 ? `${chg(fIn[fIn.length - 1][1], fIn[0][1], v => v.toFixed(1), " pts")} over this period` : "no readings in this period", "#h-body");
  const rIn = mean(inR(H.rest, a, b).map(p => p[1])), rPrev = mean(prev(H.rest).map(p => p[1]));
  tile("Resting heart rate", rIn != null ? Math.round(rIn) : "–", "bpm avg", rPrev != null ? `${chg(rIn, rPrev, v => Math.round(v))} vs previous period` : "", "#h-heart");
  const sIn = inR(H.sleep, a, b), sAvg = mean(sIn.map(p => p[1])), sPrev = mean(prev(H.sleep).map(p => p[1]));
  tile("Sleep", sAvg != null ? sAvg.toFixed(1) : "–", "h / night", sIn.some(p => p[2] + p[3] + p[4] > 0) ? (st => `${Math.round(mean(st.map(p => p[2])))} min deep · ${Math.round(mean(st.map(p => p[3])))} min REM`)(sIn.filter(p => p[2] + p[3] + p[4] > 0)) : sIn.length ? `${sIn.length} nights · no stage data` : "", "#h-sleep");
  const stIn = mean(inR(H.steps, a, b).map(p => p[1])), stPrev = mean(prev(H.steps).map(p => p[1]));
  tile("Steps", stIn != null ? Math.round(stIn).toLocaleString() : "–", "/ day", stPrev != null ? `${chg(stIn, stPrev, v => Math.round(v).toLocaleString())} vs previous period` : "", "#h-activity");
  const exIn = H.ex.filter(x => x.t >= a && x.t <= b);
  tile("Workouts", exIn.length, exIn.length === 1 ? "session" : "sessions", exIn.length ? `${Math.round(exIn.reduce((s, x) => s + x.min, 0) / 60 * 10) / 10} hours total` : "", "#h-activity");
  box.append(tiles);

  const C = {w: "var(--c-lab)", clinic: "var(--muted)", fat: "var(--c-imaging)", lean: "var(--c-procedure)", deep: "var(--seq1)", rem: "var(--seq2)", light: "var(--seq3)", steps: "var(--c-lab)", hr: "var(--c-letter)", rest: "var(--c-message)"};
  const legend = items => el("div", {class: "legend", style: {margin: "4px 0 6px"}}, ...items.map(([c, t, round]) => el("span", {}, el("i", {style: {background: c, borderRadius: round ? "50%" : null, width: round ? "9px" : null, height: round ? "9px" : null}}), t)));

  // body
  const body = el("div", {class: "card", id: "h-body"}, el("h2", {text: "Weight & body composition"}),
    el("p", {class: "hint", text: "Scale readings, with weights taken at clinic visits filling the gaps. The line is a 14-day average."}),
    legend([[C.w, "Smart scale", 1], [C.clinic, "Clinic visit", 1], [C.w, "14-day average"]]));
  box.append(body);
  const wTrend = rolling(wAll, 14);
  hChart(body, a, b, {h: 220, fmtY: v => Math.round(v), layers: [
    {type: "line", data: wTrend, color: C.w, width: 2, gap: 45},
    {type: "dots", data: H.clinic, color: C.clinic, hollow: true, r: 3},
    {type: "dots", data: H.scale, color: C.w, opacity: .55}],
    tip: t => { const p = nearest(wAll, t, len > 200 * DAY ? 10 : 4); if (!p) return null; const fat = nearest(H.fat, p[0], .6), lean = nearest(H.lean, p[0], .6);
      return {t: p[0], rows: [el("div", {class: "v", text: `${p[1].toFixed(1)} lb`}), el("div", {class: "s", text: fmtD(p[0]) + (p[2] ? ` · clinic visit (${p[2]})` : " · smart scale")}),
        fat ? tipRow(C.fat, `Body fat ${fat[1].toFixed(1)}%`) : null, lean ? tipRow(C.lean, `Lean mass ${lean[1].toFixed(1)} lb`) : null]}; }});
  if (H.fat.length || H.lean.length) {
    const two = el("div", {class: "ov-cols", style: {gridTemplateColumns: "1fr 1fr", marginTop: "10px"}});
    const c1 = el("div", {}, el("h3", {class: "sub", text: "Body fat (%)"})), c2 = el("div", {}, el("h3", {class: "sub", text: "Lean mass (lb)"}));
    two.append(c1, c2); body.append(two);
    hChart(c1, a, b, {h: 150, fmtY: v => v.toFixed(0), layers: [{type: "line", data: H.fat, color: C.fat, gap: 45}, {type: "dots", data: H.fat, color: C.fat}],
      tip: t => { const p = nearest(H.fat, t, 10); return p && {t: p[0], rows: [el("div", {class: "v", text: `${p[1].toFixed(1)}%`}), el("div", {class: "s", text: fmtD(p[0])})]}; }});
    hChart(c2, a, b, {h: 150, fmtY: v => v.toFixed(0), layers: [{type: "line", data: H.lean, color: C.lean, gap: 45}, {type: "dots", data: H.lean, color: C.lean}],
      tip: t => { const p = nearest(H.lean, t, 10); return p && {t: p[0], rows: [el("div", {class: "v", text: `${p[1].toFixed(1)} lb`}), el("div", {class: "s", text: fmtD(p[0])})]}; }});
  }

  // sleep
  const sl = el("div", {class: "card", id: "h-sleep"}, el("h2", {text: "Sleep"}),
    el("p", {class: "hint", text: long ? "Weekly averages per night, split by stage." : "Each bar is one night, split by stage. Nights without stage data show total sleep."}),
    legend([[C.deep, "Deep"], [C.rem, "REM"], [C.light, "Light"], ["var(--muted)", "Asleep, no stages"]]));
  box.append(sl);
  const nights = H.sleep.map(p => { const st = p[2] + p[3] + p[4]; return [p[0], p[2], p[3], p[4], st > 0 ? 0 : p[1]]; });
  const sBars = long ? weekly(nights) : nights;
  hChart(sl, a, b, {h: 200, fmtY: v => v + "h", layers: [{type: "bars", data: sBars, keys: ["deep", "rem", "light", "other"], colors: [C.deep, C.rem, C.light, "var(--muted)"], every: long ? 7 * DAY : DAY}],
    tip: t => { const p = nearest(sBars, t, long ? 4 : .6); if (!p) return null; const tot = p[1] + p[2] + p[3] + p[4];
      return {t: p[0], rows: [el("div", {class: "v", text: `${tot.toFixed(1)} h${long ? " avg / night" : ""}`}), el("div", {class: "s", text: long ? "Week of " + fmtD(p[0] - 3 * DAY) : fmtD(p[0])}),
        p[1] ? tipRow(C.deep, `Deep ${Math.round(p[1] * 60)} min`) : null, p[2] ? tipRow(C.rem, `REM ${Math.round(p[2] * 60)} min`) : null, p[3] ? tipRow(C.light, `Light ${Math.round(p[3] * 60)} min`) : null]}; }});

  // activity
  const act = el("div", {class: "card", id: "h-activity"}, el("h2", {text: "Activity"}),
    el("p", {class: "hint", text: long ? "Average steps per day, by week, with a 30-day average line." : "Steps per day, with a 7-day average line."}),
    legend([[C.steps, long ? "Steps / day (weekly avg)" : "Steps"], ["var(--ink2)", long ? "30-day average" : "7-day average"]]));
  box.append(act);
  const stBars = long ? weekly(H.steps) : H.steps;
  hChart(act, a, b, {h: 190, fmtY: v => v >= 1000 ? Math.round(v / 1000) + "k" : v, layers: [
    {type: "bars", data: stBars.map(p => [p[0], p[1]]), keys: ["steps"], colors: [C.steps], every: long ? 7 * DAY : DAY},
    {type: "line", data: rolling(H.steps, long ? 30 : 7), color: "var(--ink2)", width: 1.6, gap: 10}],
    tip: t => { const p = nearest(stBars, t, long ? 4 : .6); return p && {t: p[0], rows: [el("div", {class: "v", text: `${Math.round(p[1]).toLocaleString()} steps${long ? " / day avg" : ""}`}), el("div", {class: "s", text: long ? "Week of " + fmtD(p[0] - 3 * DAY) : fmtD(p[0])})]}; }});
  // workout calendar
  const cal = el("div", {style: {marginTop: "12px"}}, el("h3", {class: "sub", text: `Workouts · ${exIn.length} in this period${exIn.some(x => x.pt) ? ` (${listOf([...new Set(exIn.filter(x => x.pt).map(x => x.rule))].map(r => { const xs = exIn.filter(x => x.pt && x.rule === r); return `${xs.length} ${r.toLowerCase()} appointment${xs.length === 1 ? "" : "s"}, counted as ${xs[0].min} min each`; }))})` : ""}`}));
  act.append(cal);
  requestAnimationFrame(() => {
    const start = zdate(a); start.setHours(12, 0, 0, 0); start.setDate(start.getDate() - (start.getDay() + 6) % 7);
    const weeks = Math.ceil((b - start.getTime()) / (7 * DAY)), w = Math.max(300, cal.clientWidth), L = 28;
    const cs = Math.max(3, Math.min(20, Math.floor((w - L) / weeks) - 2)), gap = cs > 6 ? 2 : 1, hh = 7 * (cs + gap) + 16;
    const byDay = new Map(); for (const x of exIn) { const k = isoDay(x.t); const v = byDay.get(k) || {min: 0, list: []}; v.min += x.min; v.list.push(x); byDay.set(k, v); }
    const svg = el("svg:svg", {viewBox: `0 0 ${w} ${hh}`, height: hh, style: "width:100%"}), g = [];
    ["Mon", "", "Wed", "", "Fri", "", ""].forEach((d, i) => d && cs >= 8 && g.push(el("svg:text", {x: 0, y: 14 + i * (cs + gap) + cs - 2, text: d, style: "font-size:10px"})));
    let lastM = -1;
    for (let wk = 0; wk < weeks; wk++) for (let dd = 0; dd < 7; dd++) {
      const t = start.getTime() + (wk * 7 + dd) * DAY; if (t < a - DAY || t > b) continue;
      const k = isoDay(t), v = byDay.get(k), cx = L + wk * (cs + gap), cy = 14 + dd * (cs + gap);
      const m = zdate(t).getMonth(); if (dd === 0 && m !== lastM && (weeks < 60 || m % 3 === 0)) { lastM = m; g.push(el("svg:text", {x: cx, y: 10, text: zdate(t).toLocaleDateString(undefined, weeks > 60 ? {month: "short", year: "2-digit"} : {month: "short"}), style: "font-size:10px"})); }
      const fill = !v ? "var(--grid)" : v.min >= 60 ? "var(--seq1)" : v.min >= 30 ? "var(--seq2)" : "var(--seq3)";
      const r = el("svg:rect", {x: cx, y: cy, width: cs, height: cs, rx: cs > 6 ? 2 : 0, fill});
      if (v) { r.style.cursor = "default"; r.addEventListener("pointermove", ev => showTip([el("div", {class: "v", text: fmtD(t)}), ...v.list.map(x => el("div", {class: "s", text: x.pt ? `${x.rule} appointment${x.who ? " with " + x.who : ""} · counted as ${x.min} min` : `${x.name} · ${x.min} min`}))], ev)); r.addEventListener("pointerleave", hideTip); }
      g.push(r);
    }
    svg.replaceChildren(...g); cal.append(svg,
      el("div", {class: "legend", style: {marginTop: "4px"}}, el("span", {}, el("i", {style: {background: "var(--grid)"}}), "None"), el("span", {}, el("i", {style: {background: "var(--seq3)"}}), "Under 30 min"),
        el("span", {}, el("i", {style: {background: "var(--seq2)"}}), "30–60 min"), el("span", {}, el("i", {style: {background: "var(--seq1)"}}), "60+ min")));
    const kinds = new Map(); for (const x of exIn) { const k = x.name || "Workout"; const v = kinds.get(k) || {n: 0, min: 0}; v.n++; v.min += x.min; kinds.set(k, v); }
    if (kinds.size) cal.append(el("p", {class: "hint", style: {marginTop: "6px"}, text: [...kinds.entries()].sort((p, q) => q[1].min - p[1].min).slice(0, 6).map(([k, v]) => `${k}: ${v.n}× · ${Math.round(v.min / 6) / 10} h`).join("   ·   ")}));
  });

  // heart
  const ht = el("div", {class: "card", id: "h-heart"}, el("h2", {text: "Heart"}),
    el("p", {class: "hint", text: "Resting heart rate, and the daily average heart rate inside the day's low–high range."}),
    legend([[C.rest, "Resting"], [C.hr, "Daily average"], ["color-mix(in srgb, " + C.hr + " 30%, transparent)", "Daily low–high"]]));
  box.append(ht);
  const rest = H.rest, restT = rolling(rest, long ? 14 : 5);
  hChart(ht, a, b, {h: 210, fmtY: v => Math.round(v), layers: [
    {type: "band", data: H.hr.map(p => [p[0], p[2], p[3]]), color: C.hr},
    {type: "line", data: H.hr, color: C.hr, width: 1.4, opacity: .8, gap: 4},
    {type: "line", data: restT, color: C.rest, width: 2.2, gap: 10},
    {type: "dots", data: long ? [] : rest, color: C.rest, r: 2.4}],
    tip: t => { const p = nearest(H.hr, t, .6) || nearest(rest, t, .6); if (!p) return null; const r = nearest(rest, p[0], .6), h = nearest(H.hr, p[0], .6);
      return {t: p[0], rows: [el("div", {class: "v", text: fmtD(p[0])}), r ? tipRow(C.rest, `Resting ${Math.round(r[1])} bpm`) : null,
        h ? tipRow(C.hr, `Average ${Math.round(h[1])} bpm${h[2] != null ? ` · range ${Math.round(h[2])}–${Math.round(h[3])}` : ""}`) : null]}; }});
  if (H.hrv.length) {
    const hv = el("div", {style: {marginTop: "10px"}}, el("h3", {class: "sub", text: "Heart rate variability (ms)"})); ht.append(hv);
    hChart(hv, a, b, {h: 140, layers: [{type: "line", data: rolling(H.hrv, 7), color: C.rest, gap: 10}, {type: "dots", data: H.hrv, color: C.rest, opacity: .4}],
      tip: t => { const p = nearest(H.hrv, t, .6); return p && {t: p[0], rows: [el("div", {class: "v", text: `${Math.round(p[1])} ms`}), el("div", {class: "s", text: fmtD(p[0])})]}; }});
  }
}

