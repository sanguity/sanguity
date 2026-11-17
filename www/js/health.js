/* Sanguity · Body and activity tab ("health"): watch, sleep and smart-scale charts.
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
/* ---------------------------------------------------------------- body and activity (watch + scale, no hospital data) */
/* its period is the records bar's (#171, core.js); its option, shading hospital stays, is saved per profile. hLoad runs
   once the profile is known; the one setting saved before profiles existed goes to the first profile opened. A
   period saved here before #171 gives way to the records bar's. */
const hState = {stays: true, bmiWho: false};   // bmiWho: the WHO's adult BMI categories shown (#195)
const hKey = () => "ht-health" + (PROFILE ? ":" + PROFILE.id : "");
function hLoad() {
  try {
    let raw = localStorage.getItem(hKey());
    if (raw == null && (raw = localStorage.getItem("ht-health")) != null) localStorage.removeItem("ht-health");
    const v = JSON.parse(raw || "{}");
    hState.stays = typeof v.stays === "boolean" ? v.stays : true;
    hState.bmiWho = v.bmiWho === true;
  } catch (e) {}
}
function hSave() { try { localStorage.setItem(hKey(), JSON.stringify({stays: hState.stays, bmiWho: hState.bmiWho})); } catch (e) {} }
function openHealth(a, b) { closeDrawer(); state.from = a; state.to = b; state.preset = "custom"; pickTab("health"); }
function hData() {
  const M = Object.fromEntries(((WATCH || {}).metrics || []).map(m => [m.key, m]));
  const ser = k => (M[k] ? M[k].pts : []).map(([d, v]) => [dayTs(d), v]);
  // drop readings that sit >12% away from the median of the other weights within 90 days (e.g. a profile weight typed into another app)
  const rawW = ser("weight"), cwRaw = (DATA.clinicWeights || []).map(([t, v]) => [dayTs(t.slice(0, 10)), v]);
  // the others within 90 days are found in the readings put in time order, not by looking through every reading for
  // each one (ten years of a daily scale took seconds, #201)
  const ref = [...rawW, ...cwRaw].sort((x, y) => x[0] - y[0]);
  const scale = rawW.filter(p => {
    let lo = 0, hi = ref.length;   // the first reading less than 90 days before
    while (lo < hi) { const m = (lo + hi) >> 1; if (ref[m][0] <= p[0] - 90 * DAY) lo = m + 1; else hi = m; }
    const near = [];
    for (let i = lo; i < ref.length && ref[i][0] < p[0] + 90 * DAY; i++) if (ref[i] !== p) near.push(ref[i][1]);
    near.sort((x, y) => x - y);
    return near.length < 3 || Math.abs(p[1] - near[near.length >> 1]) / near[near.length >> 1] <= 0.12; });
  const scaleDays = new Set(scale.map(p => isoDay(p[0])));
  const clinic = (DATA.clinicWeights || []).map(([t, v, org]) => [dayTs(t.slice(0, 10)), v, org]).filter(p => !scaleDays.has(isoDay(p[0])));
  const byDay = k => M[k] ? Object.fromEntries(M[k].pts) : {};   // once per metric, not once per night (#201)
  const parts = ["sleep_deep_min", "sleep_rem_min", "sleep_light_min"].map(byDay);
  const sleep = ser("sleep_h").map(([t, v]) => { const d = isoDay(t); return [t, v, ...parts.map(m => (m[d] || 0) / 60)]; });
  const lo = M.hr_min ? Object.fromEntries(M.hr_min.pts) : {}, hi = M.hr_max ? Object.fromEntries(M.hr_max.pts) : {};
  const hr = ser("hr_avg").map(([t, v]) => [t, v, lo[isoDay(t)], hi[isoDay(t)]]);
  const ex = ((WATCH || {}).exercise || []).map(x => ({t: zParse(x.start), name: (x.name || "").replace(/_/g, " "), kind: x.kind, min: x.minutes || 0, hr: x.hr}));   // hr: its heart rate and zones (#197)
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
  const bmi = bmiSeries([...scale, ...clinic].sort((p, q) => p[0] - q[0]), "lb");   // #195: each weight with the height of its day
  return {scale, clinic, bmi, sleep, hr, ex, rest: ser("hr_rest"), steps: ser("steps"), fat: ser("body_fat"), lean: ser("lean_mass"), bmr: ser("bmr"),
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
/* label, noun, nav (#60): what the chart shows ("Weight"), what each point is ("reading"), and the points the
   keyboard steps through ([[t, …]], the ones its tip reads) */
function hChart(host, a, b, {h = 190, layers, fmtY = v => v, unit = "", zero = false, tip, label = "Chart", noun = "day", nav = layers[0].data}) {
  const svg = el("svg:svg", {role: "img", class: "pz", "aria-label": label}); host.append(svg);
  svg._draw = (a, b) => {   // also called live while dragging, with the new window
    if (!svg.isConnected) return;   // re-rendered before this frame came round
    const cs = getComputedStyle(host), w = Math.max(260, host.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight)), L = 44, R = w - 8, T = msInRange(a, b) ? 22 : 8, B = h - 22;
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`); svg.setAttribute("height", h); svg.style.width = "100%"; svg._geom = {L, R, w};
    const navIn = nav.filter(p => p[0] >= a && p[0] <= b);   // the keyboard's points (#60)
    chartKeys(svg, `${label}: ${navIn.length} ${noun}${navIn.length === 1 ? "" : "s"}${navIn.length ? ` from ${fmtD(navIn[0][0])} to ${fmtD(navIn[navIn.length - 1][0])}` : " in this period"}. Left and right arrow keys step through them.`,
      () => navIn.map(p => p[0]), () => "", () => {});   // until it's drawn (below)
    let lo = Infinity, hi = -Infinity;
    for (const l of layers) for (const p of l.data || []) {
      if (p[0] < a || p[0] > b) continue;
      const vs = l.type === "bars" ? [l.keys.reduce((s, _, i) => s + (p[i + 1] || 0), 0)] : l.type === "band" || l.type === "pairs" ? [p[1], p[2]] : [p[1]];
      for (const v of vs) if (v != null && isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    }
    if (!isFinite(lo)) { svg.replaceChildren(el("svg:text", {x: w / 2, y: h / 2, "text-anchor": "middle", text: "No data in this period"})); return; }
    for (const l of layers) if (l.type === "target") for (const v of [l.low, l.high]) if (v != null && isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }   // #234: in sight
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
      const pts = (l.data || []).filter(p => p[0] >= a && p[0] <= b);
      if (l.type === "target") {   // a target of the person's own (#234): a band between its ends, or a line at its one end, labelled
        const top = y(l.high ?? l.low), bot = y(l.low ?? l.high);
        if (l.low != null && l.high != null) g.push(el("svg:rect", {class: "tgt-band", x: L, y: top, width: R - L, height: Math.max(0, bot - top)}));
        for (const v of [l.low, l.high]) if (v != null) g.push(el("svg:line", {class: "tgt-edge", x1: L, x2: R, y1: y(v), y2: y(v)}));
        g.push(el("svg:text", {class: "tgt-label", x: R - 4, y: top - 4 < T + 10 ? top + 12 : top - 4, "text-anchor": "end", text: l.label}));
      } else if (l.type === "bars") {
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
        for (const p of pts) { const f = l.flag && l.flag(p);   // ▲ / ▼ for a reading above or below a target of the person's own (#234)
          g.push(f ? abnMark(x(p[0]), y(p[1]), f, 4) : el("svg:circle", {cx: x(p[0]), cy: y(p[1]), r, fill: l.hollow ? "var(--surface)" : l.color, stroke: l.hollow ? l.color : "var(--surface)", "stroke-width": l.hollow ? 1.5 : 1, opacity: l.opacity || 1})); }
      } else if (l.type === "ybands") {   // ranges of the value across the chart, each labelled (the WHO's BMI categories, #195)
        l.bands.forEach(([from, to, label], i) => {
          const top = y(Math.min(hi, to)), bot = y(Math.max(lo, from));
          if (!(bot - top > 0)) return;
          if (i % 2) g.push(el("svg:rect", {x: L, y: top, width: R - L, height: bot - top, fill: "color-mix(in srgb, var(--muted) 12%, transparent)"}));
          if (from > lo && from < hi) g.push(el("svg:line", {class: "gridline band-edge", x1: L, x2: R, y1: bot, y2: bot}));
          if (bot - top >= 14) g.push(el("svg:text", {x: R - 4, y: top + 12, "text-anchor": "end", class: "band-label", text: label}));
        });
      } else if (l.type === "pairs") {   // two values of one reading, [t, a, b, flag of a, flag of b] (#180): a line between two dots; a flagged value is ▲ / ▼ (#60)
        for (const p of pts) {
          const cx = x(p[0]);
          if (p[1] != null && p[2] != null) g.push(el("svg:line", {x1: cx, x2: cx, y1: y(p[1]), y2: y(p[2]), stroke: l.color, "stroke-width": 1.5, opacity: .7}));
          for (const i of [1, 2]) if (p[i] != null) g.push(p[i + 2] ? abnMark(cx, y(p[i]), p[i + 2]) : el("svg:circle", {cx, cy: y(p[i]), r: l.r || 3,
            fill: l.hollow ? "var(--surface)" : l.color, stroke: l.hollow ? l.color : "var(--surface)", "stroke-width": l.hollow ? 1.5 : 1}));
        }
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
    // the keyboard (#60): the same crosshair and tooltip as hovering, the tooltip at the top of the crosshair
    chartKeys(svg, svg.getAttribute("aria-label"), () => navIn.map(p => p[0]), t => {
      const nodes = tip && tip(t); if (!nodes) return "";
      cross.setAttribute("x1", x(nodes.t)); cross.setAttribute("x2", x(nodes.t)); cross.setAttribute("visibility", "visible");
      showTip(nodes.rows, svgPoint(svg, x(nodes.t), T));
      return tipWords(nodes.rows);
    }, () => { hideTip(); cross.setAttribute("visibility", "hidden"); });
  };
  requestAnimationFrame(() => svg._draw(a, b));
  return svg;
}
const nearest = (pts, t, maxDays = 3) => { let best = null; for (const p of pts) if (Math.abs(p[0] - t) <= maxDays * DAY && (!best || Math.abs(p[0] - t) < Math.abs(best[0] - t))) best = p; return best; };
const tipRow = (color, text) => el("div", {class: "s"}, color ? el("span", {class: "k", style: {background: color}}) : null, text);

function renderHealth() {
  const box = $("#view-health"); box.replaceChildren();
  if (!WATCH) {   // #199: what would be here, and where it comes from (its owners and editors set the phone up)
    box.append(emptyState("Steps, sleep, heart rate and weight from a phone, watch or smart scale appear here once a phone sends them.",
      canEdit() ? {href: `sources.html?profile=${PROFILE.id}#phone`, text: "Connect a phone"} : null));
    growthCard(box, (title, key) => el("div", {class: "list-hd"}, el("h2", {text: title}), el("span", {class: "spacer"}), viewSeg(key, title, renderHealth)));   // a child's growth from the records (#196)
    return;
  }
  H = H || hData();
  const [a, b] = hRange(), len = b - a, long = len > 150 * DAY;
  // toolbar (the period is in the records bar)
  box.append(el("div", {class: "toolbar"},
    el("label", {}, el("input", {type: "checkbox", checked: hState.stays || null, onchange: e => { hState.stays = e.target.checked; hSave(); renderHealth(); }}), "Shade hospital stays"),
    el("span", {class: "hint", style: {margin: 0}, text: `Watch and smart scale via Health Connect${WATCH.last_payload ? " · last sync " + fmtDT(WATCH.last_payload) : ""}`})),
    el("p", {class: "hint chart-hint", text: "Drag any chart sideways to move through time: every chart moves together."}),
    moreAbout("Scroll or pinch to zoom, and double-click to show everything."));
  // tiles
  const prev = pts => inR(pts, a - len, a);
  const wAll = [...H.scale, ...H.clinic].sort((p, q) => p[0] - q[0]), wIn = inR(wAll, a, b);
  const tiles = el("div", {class: "tiles", style: {gridTemplateColumns: "repeat(auto-fill,minmax(170px,1fr))"}});
  const tile = (label, val, unit, delta, href) => tiles.append(el("button", {class: "tile", onclick: () => $(href)?.scrollIntoView({behavior: smoothly(), block: "start"})},
    el("span", {class: "lbl", text: label}), el("span", {class: "val"}, val, el("small", {text: unit})), el("span", {class: "delta", text: delta})));
  const chg = (cur, base, f, u = "") => cur != null && base != null ? `${cur >= base ? "▲ +" : "▼ −"}${f(Math.abs(cur - base))}${u}` : "";
  const latestW = wIn.length ? wIn[wIn.length - 1] : wAll[wAll.length - 1];
  tile("Weight", latestW ? latestW[1].toFixed(1) : "–", "lb", wIn.length > 1 ? `${chg(wIn[wIn.length - 1][1], wIn[0][1], v => v.toFixed(1), " lb")} over this period` : latestW ? `last ${fmtD(latestW[0])}` : "", "#h-body");
  bmiTile(tile, a, b);   // BMI (#195, below)
  const fIn = inR(H.fat, a, b);
  if (H.fat.length) tile("Body fat", fIn.length ? fIn[fIn.length - 1][1].toFixed(1) : "–", "%", fIn.length > 1 ? `${chg(fIn[fIn.length - 1][1], fIn[0][1], v => v.toFixed(1), " pts")} over this period` : "no readings in this period", "#h-body");
  gTile(tile, a, b);   // blood glucose (#181, below)
  const rIn = mean(inR(H.rest, a, b).map(p => p[1])), rPrev = mean(prev(H.rest).map(p => p[1]));
  tile("Resting heart rate", rIn != null ? Math.round(rIn) : "–", "bpm avg", rPrev != null ? `${chg(rIn, rPrev, v => Math.round(v))} vs previous period` : "", "#h-heart");
  const sIn = inR(H.sleep, a, b), sAvg = mean(sIn.map(p => p[1])), sPrev = mean(prev(H.sleep).map(p => p[1]));
  // the stage averages are hours (H.sleep's p[2], p[3]); shown in minutes, as the chart's tooltip and table give them (#216)
  tile("Sleep", sAvg != null ? sAvg.toFixed(1) : "–", "h / night", sIn.some(p => p[2] + p[3] + p[4] > 0) ? (st => `${Math.round(mean(st.map(p => p[2])) * 60)} min deep · ${Math.round(mean(st.map(p => p[3])) * 60)} min REM`)(sIn.filter(p => p[2] + p[3] + p[4] > 0)) : sIn.length ? `${sIn.length} nights · no stage data` : "", "#h-sleep");
  const stIn = mean(inR(H.steps, a, b).map(p => p[1])), stPrev = mean(prev(H.steps).map(p => p[1]));
  tile("Steps", stIn != null ? Math.round(stIn).toLocaleString() : "–", "/ day", stPrev != null ? `${chg(stIn, stPrev, v => Math.round(v).toLocaleString())} vs previous period` : "", "#h-activity");
  const exIn = H.ex.filter(x => x.t >= a && x.t <= b);
  tile("Workouts", exIn.length, exIn.length === 1 ? "session" : "sessions", exIn.length ? `${Math.round(exIn.reduce((s, x) => s + x.min, 0) / 60 * 10) / 10} hours total` : "", "#h-activity");
  vitalTiles(tile, a, b);   // the latest blood pressure (#180)
  box.append(tiles);

  const C = {w: "var(--c-lab)", clinic: "var(--muted)", fat: "var(--c-imaging)", lean: "var(--c-procedure)", deep: "var(--seq1)", rem: "var(--seq2)", light: "var(--seq3)", steps: "var(--c-lab)", hr: "var(--c-letter)", rest: "var(--c-message)"};
  const legend = items => el("div", {class: "legend", style: {margin: "4px 0 6px"}}, ...items.map(([c, t, round]) => el("span", {}, el("i", {style: {background: c, borderRadius: round ? "50%" : null, width: round ? "9px" : null, height: round ? "9px" : null}}), t)));

  /* Chart | Table on each card (#60): its charts, or one table of what they show, a row per day (or week) in the
     period, newest first. byDay([pts, …]) -> [{t, v: [the point from each on that day, or undefined]}] */
  const head = (title, key) => el("div", {class: "list-hd"}, el("h2", {text: title}), el("span", {class: "spacer"}), viewSeg(key, title, renderHealth));
  const byDay = (...sets) => { const m = new Map();
    sets.forEach((pts, i) => { for (const p of inR(pts, a, b)) { const k = isoDay(p[0]); if (!m.has(k)) m.set(k, {t: p[0], v: []}); m.get(k).v[i] = p; } });
    return [...m.values()].sort((p, q) => q.t - p.t); };
  const f1 = p => p ? p[1].toFixed(1) : "", f0 = p => p ? Math.round(p[1]).toLocaleString() : "";
  const weekOf = t => "Week of " + fmtD(t - 3 * DAY);
  const vx = {a, b, long, legend, head};   // for the readings' cards (#180)
  // body
  const body = el("div", {class: "card", id: "h-body"}, head("Weight & body composition", "#h-body"),
    el("p", {class: "hint", text: "Scale readings, with weights taken at clinic visits filling the gaps. The line is a 14-day average."}));
  box.append(body);
  const wT = tgtIn("weight", "lb"), wSide = p => wT && !p[2] ? tgtSide(p[1], ...wT, 1) : "";   // a target of the person's own (#234): the scale's weights
  tgtLine(body, "weight", "lb", wT ? inR(H.scale, a, b).map(wSide) : null);
  if (state.tables.has("#h-body")) {
    const comp = H.fat.length || H.lean.length;
    const bm = H.bmi.length > 0, who = bm && hState.bmiWho && H.bmi.some(r => !r.child);   // BMI (#195)
    body.append(chartTable(["Date", "Weight (lb)", "From", ...(bm ? ["BMI (kg/m²)"] : []), ...(who ? ["WHO adult category"] : []), ...(comp ? ["Body fat (%)", "Lean mass (lb)"] : [])],
      byDay(wAll, H.fat, H.lean, H.bmi.map(r => [r.t, r.v, r])).map(({t, v: [w, fat, lean, bp]}) => [fmtD(t), w && wSide(w) ? [f1(w), wSide(w), TGT_WORDS[wSide(w)]] : f1(w), w ? (w[2] ? `Clinic visit (${w[2]})` : "Smart scale") : "",
        ...(bm ? [bp ? bmiWords(bp[2]) : ""] : []), ...(who ? [bp && !bp[2].child ? whoWords(bp[2].v) : ""] : []), ...(comp ? [f1(fat), f1(lean)] : [])])));
  } else { body.append(legend([[C.w, "Smart scale", 1], [C.clinic, "Clinic visit", 1], [C.w, "14-day average"]])); if (wT) body.lastChild.append(tgtKey()); }
  const wTrend = rolling(wAll, 14);
  if (!state.tables.has("#h-body")) hChart(body, a, b, {h: 220, fmtY: v => Math.round(v), label: "Weight", noun: "reading", nav: wAll, layers: [
    ...(wT ? [tgtLayer(wT)] : []),
    {type: "line", data: wTrend, color: C.w, width: 2, gap: 45},
    {type: "dots", data: H.clinic, color: C.clinic, hollow: true, r: 3},
    {type: "dots", data: H.scale, color: C.w, opacity: .55, flag: wSide}],
    tip: t => { const p = nearest(wAll, t, len > 200 * DAY ? 10 : 4); if (!p) return null; const fat = nearest(H.fat, p[0], .6), lean = nearest(H.lean, p[0], .6);
      const bp = H.bmi.find(r => r.w === p);   // its BMI (#195)
      return {t: p[0], rows: [el("div", {class: "v", text: `${p[1].toFixed(1)} lb`}), el("div", {class: "s", text: fmtD(p[0]) + (p[2] ? ` · clinic visit (${p[2]})` : " · smart scale")}),
        wSide(p) ? el("div", {class: "s", text: tgtWord(wSide(p))}) : null,
        bp ? el("div", {class: "s", text: `BMI ${bmiWords(bp)}`}) : null,
        fat ? tipRow(C.fat, `Body fat ${fat[1].toFixed(1)}%`) : null, lean ? tipRow(C.lean, `Lean mass ${lean[1].toFixed(1)} lb`) : null]}; }});
  bmiChart(body, a, b, vx);   // BMI over time (#195, below)
  if ((H.fat.length || H.lean.length) && !state.tables.has("#h-body")) {
    const two = el("div", {class: "ov-cols", style: {gridTemplateColumns: "1fr 1fr", marginTop: "10px"}});
    const c1 = el("div", {}, el("h3", {class: "sub", text: "Body fat (%)"})), c2 = el("div", {}, el("h3", {class: "sub", text: "Lean mass (lb)"}));
    two.append(c1, c2); body.append(two);
    hChart(c1, a, b, {h: 150, fmtY: v => v.toFixed(0), label: "Body fat", noun: "reading", layers: [{type: "line", data: H.fat, color: C.fat, gap: 45}, {type: "dots", data: H.fat, color: C.fat}],
      tip: t => { const p = nearest(H.fat, t, 10); return p && {t: p[0], rows: [el("div", {class: "v", text: `${p[1].toFixed(1)}%`}), el("div", {class: "s", text: fmtD(p[0])})]}; }});
    hChart(c2, a, b, {h: 150, fmtY: v => v.toFixed(0), label: "Lean mass", noun: "reading", layers: [{type: "line", data: H.lean, color: C.lean, gap: 45}, {type: "dots", data: H.lean, color: C.lean}],
      tip: t => { const p = nearest(H.lean, t, 10); return p && {t: p[0], rows: [el("div", {class: "v", text: `${p[1].toFixed(1)} lb`}), el("div", {class: "s", text: fmtD(p[0])})]}; }});
  }

  growthCard(box, head);   // a child's growth charts (#196, below)
  hGlucose(box, a, b, {head, legend, byDay});   // blood glucose (#181, below)

  // sleep
  const sl = el("div", {class: "card", id: "h-sleep"}, head("Sleep", "#h-sleep"),
    el("p", {class: "hint", text: long ? "Weekly averages per night, split by stage." : "Each bar is one night, split by stage. Nights without stage data show total sleep."}));
  box.append(sl);
  const nights = H.sleep.map(p => { const st = p[2] + p[3] + p[4]; return [p[0], p[2], p[3], p[4], st > 0 ? 0 : p[1]]; });
  const sBars = long ? weekly(nights) : nights;
  const mins = v => v ? String(Math.round(v * 60)) : "";
  if (state.tables.has("#h-sleep")) sl.append(chartTable([long ? "Week" : "Night", long ? "Asleep (h, average a night)" : "Asleep (h)", "Deep (min)", "REM (min)", "Light (min)"],
    byDay(sBars).map(({t, v: [p]}) => [long ? weekOf(t) : fmtD(t), (p[1] + p[2] + p[3] + p[4]).toFixed(1), mins(p[1]), mins(p[2]), mins(p[3])])));
  else sl.append(legend([[C.deep, "Deep"], [C.rem, "REM"], [C.light, "Light"], ["var(--muted)", "Asleep, no stages"]]));
  if (!state.tables.has("#h-sleep")) hChart(sl, a, b, {h: 200, fmtY: v => v + "h", label: long ? "Sleep, weekly averages" : "Sleep", noun: long ? "week" : "night", nav: sBars, layers: [{type: "bars", data: sBars, keys: ["deep", "rem", "light", "other"], colors: [C.deep, C.rem, C.light, "var(--muted)"], every: long ? 7 * DAY : DAY}],
    tip: t => { const p = nearest(sBars, t, long ? 4 : .6); if (!p) return null; const tot = p[1] + p[2] + p[3] + p[4];
      return {t: p[0], rows: [el("div", {class: "v", text: `${tot.toFixed(1)} h${long ? " avg / night" : ""}`}), el("div", {class: "s", text: long ? "Week of " + fmtD(p[0] - 3 * DAY) : fmtD(p[0])}),
        p[1] ? tipRow(C.deep, `Deep ${Math.round(p[1] * 60)} min`) : null, p[2] ? tipRow(C.rem, `REM ${Math.round(p[2] * 60)} min`) : null, p[3] ? tipRow(C.light, `Light ${Math.round(p[3] * 60)} min`) : null]}; }});

  // activity
  const act = el("div", {class: "card", id: "h-activity"}, head("Activity", "#h-activity"),
    el("p", {class: "hint", text: long ? "Average steps per day, by week, with a 30-day average line." : "Steps per day, with a 7-day average line."}));
  box.append(act);
  const stBars = long ? weekly(H.steps) : H.steps;
  if (state.tables.has("#h-activity")) {   // steps and workouts, by day or by week (the weeks as weekly() makes them)
    const wk = t => { const d = zdate(t); d.setHours(12, 0, 0, 0); d.setDate(d.getDate() - (d.getDay() + 6) % 7); return d.getTime() + 3 * DAY; };
    const exBy = new Map(); for (const x of exIn) { const t = long ? wk(x.t) : dayTs(isoDay(x.t)); exBy.set(t, [...(exBy.get(t) || []), x]); }
    const exPts = [...exBy.keys()].map(t => [t]);
    act.append(chartTable([long ? "Week" : "Date", long ? "Steps (average a day)" : "Steps", "Workouts"],
      byDay(stBars, exPts).map(({t, v: [p, e]}) => [long ? weekOf(t) : fmtD(t), f0(p),
        (exBy.get(e ? e[0] : NaN) || []).map(x => x.pt ? `${x.rule} appointment, counted as ${x.min} min` : `${x.name || "Workout"} ${x.min} min`).join("; ")])));
  } else act.append(legend([[C.steps, long ? "Steps / day (weekly avg)" : "Steps"], ["var(--ink2)", long ? "30-day average" : "7-day average"]]));
  if (!state.tables.has("#h-activity")) hChart(act, a, b, {h: 190, fmtY: v => v >= 1000 ? Math.round(v / 1000) + "k" : v, label: long ? "Steps, weekly averages" : "Steps", noun: long ? "week" : "day", nav: stBars, layers: [
    {type: "bars", data: stBars.map(p => [p[0], p[1]]), keys: ["steps"], colors: [C.steps], every: long ? 7 * DAY : DAY},
    {type: "line", data: rolling(H.steps, long ? 30 : 7), color: "var(--ink2)", width: 1.6, gap: 10}],
    tip: t => { const p = nearest(stBars, t, long ? 4 : .6); return p && {t: p[0], rows: [el("div", {class: "v", text: `${Math.round(p[1]).toLocaleString()} steps${long ? " / day avg" : ""}`}), el("div", {class: "s", text: long ? "Week of " + fmtD(p[0] - 3 * DAY) : fmtD(p[0])})]}; }});
  // workout calendar (in the table instead, with Table, #60)
  const cal = el("div", {style: {marginTop: "12px"}}, el("h3", {class: "sub", text: `Workouts · ${exIn.length} in this period${exIn.some(x => x.pt) ? ` (${listOf([...new Set(exIn.filter(x => x.pt).map(x => x.rule))].map(r => { const xs = exIn.filter(x => x.pt && x.rule === r); return `${xs.length} ${r.toLowerCase()} appointment${xs.length === 1 ? "" : "s"}, counted as ${xs[0].min} min each`; }))})` : ""}`}));
  if (!state.tables.has("#h-activity")) act.append(cal);
  if (!state.tables.has("#h-activity")) requestAnimationFrame(() => {
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

  hZones(box, a, b, {long, legend, head, byDay});   // heart rate zones on workouts (#197, below)

  // heart
  const ht = el("div", {class: "card", id: "h-heart"}, head("Heart", "#h-heart"),
    el("p", {class: "hint", text: "Resting heart rate, and the daily average heart rate inside the day's low–high range."}));
  box.append(ht);
  const rest = H.rest, restT = rolling(rest, long ? 14 : 5);
  if (state.tables.has("#h-heart")) {
    ht.append(chartTable(["Date", "Resting (bpm)", "Average (bpm)", "Low–high (bpm)", ...(H.hrv.length ? ["Heart rate variability (ms)"] : [])],
      byDay(rest, H.hr, H.hrv).map(({t, v: [r, h, hv]}) => [fmtD(t), f0(r), f0(h), h && h[2] != null ? `${Math.round(h[2])}–${Math.round(h[3])}` : "", ...(H.hrv.length ? [f0(hv)] : [])])));
    return renderVitals(box, vx);   // the readings' cards come after the heart's (#180)
  }
  ht.append(legend([[C.rest, "Resting"], [C.hr, "Daily average"], ["color-mix(in srgb, " + C.hr + " 30%, transparent)", "Daily low–high"]]));
  hChart(ht, a, b, {h: 210, fmtY: v => Math.round(v), label: "Heart rate", noun: "day", nav: H.hr.length ? H.hr : rest, layers: [
    {type: "band", data: H.hr.map(p => [p[0], p[2], p[3]]), color: C.hr},
    {type: "line", data: H.hr, color: C.hr, width: 1.4, opacity: .8, gap: 4},
    {type: "line", data: restT, color: C.rest, width: 2.2, gap: 10},
    {type: "dots", data: long ? [] : rest, color: C.rest, r: 2.4}],
    tip: t => { const p = nearest(H.hr, t, .6) || nearest(rest, t, .6); if (!p) return null; const r = nearest(rest, p[0], .6), h = nearest(H.hr, p[0], .6);
      return {t: p[0], rows: [el("div", {class: "v", text: fmtD(p[0])}), r ? tipRow(C.rest, `Resting ${Math.round(r[1])} bpm`) : null,
        h ? tipRow(C.hr, `Average ${Math.round(h[1])} bpm${h[2] != null ? ` · range ${Math.round(h[2])}–${Math.round(h[3])}` : ""}`) : null]}; }});
  if (H.hrv.length) {
    const hv = el("div", {style: {marginTop: "10px"}}, el("h3", {class: "sub", text: "Heart rate variability (ms)"})); ht.append(hv);
    hChart(hv, a, b, {h: 140, label: "Heart rate variability", noun: "day", nav: H.hrv, layers: [{type: "line", data: rolling(H.hrv, 7), color: C.rest, gap: 10}, {type: "dots", data: H.hrv, color: C.rest, opacity: .4}],
      tip: t => { const p = nearest(H.hrv, t, .6); return p && {t: p[0], rows: [el("div", {class: "v", text: `${Math.round(p[1])} ms`}), el("div", {class: "s", text: fmtD(p[0])})]}; }});
  }
  renderVitals(box, vx);
}

/* ---------------------------------------------------------------- readings at home and at visits (#180, #184) */
/* Blood pressure taken at home, as the phone apps send it (watch/receiver.py's bp_readings: [time, systolic,
   diastolic, pulse, source, app]), beside the readings taken at visits: the records' systolic and diastolic series
   (C-CDA, FHIR, typed-in vitals), paired by the minute they were taken. What the records and devices say only: no
   categories or targets of Sanguity's own; a reading's own flag from a clinic's report shows, ▲ or ▼ (#60). The same
   visit's reading from two imports, or a home reading also in the records (typed in as vitals), is shown once.
   Body temperature and blood oxygen likewise (#184): a thermometer's readings (temp_readings: [time, °C, source, app])
   in the person's temperature unit (#41), and the watch's daily blood oxygen, each beside the visits' readings. */
const vitalCode = s => String(s.loinc || (/^loinc:([\w-]+)/.exec(s.key || "") || [])[1] || "");
const VITALS = {   // which of the records' series hold a vital, by LOINC code or name
  sys: s => !s.watch && (vitalCode(s) === "8480-6" || /systolic/i.test(s.name || "")),
  dia: s => !s.watch && (vitalCode(s) === "8462-4" || /diastolic/i.test(s.name || "")),
  temp: s => !s.watch && !!tempScale(s.unit) && (["8310-5", "8331-1", "8332-9", "8333-7", "8328-7", "8329-5", "8334-5"].includes(vitalCode(s))
    || (/temp/i.test(s.name || "") && !/skin|room|change/i.test(s.name || ""))),
  spo2: s => !s.watch && /%/.test(s.unit || "") && (["59408-5", "2708-6", "59417-6", "20564-1"].includes(vitalCode(s))
    || /spo2|sp o2|oxygen sat|o2 sat|pulse ox/i.test(s.name || "")),
};
/* a temperature unit as "F" or "C" (°F, [degF], Cel...), or null; a value from one to the other */
const tempScale = u => { const k = String(u || "").trim().toLowerCase().replace(/^\[|\]$/g, "").replace(/^(°|deg)\s*/, "");
  return k === "f" ? "F" : k === "c" || k === "cel" ? "C" : null; };
const toScale = (v, from, to) => from === to ? v : to === "F" ? v * 9 / 5 + 32 : (v - 32) * 5 / 9;
const minuteOf = t => Math.round(t / 6e4);
const vitalSource = (s, p) => (p[6] && p[6] !== "Clinic" ? srcName(p[6]) : "") || importName(p.imp ?? s.imp) || "the records";
/* the records' readings of a vital kept as one series or two (tests: one per value), paired by the minute:
   [{t, v: [value of each], f: [its flag], src}], oldest first; conv(series, value) puts a value in the unit shown */
function clinicReadings(tests, conv = (s, v) => v) {
  const by = new Map();
  tests.forEach((test, i) => { for (const s of SERIES.filter(test)) for (const p of s.pts) {
    if (typeof p[1] !== "number" || !isFinite(p[1]) || !isFinite(p.ts)) continue;
    const k = minuteOf(p.ts); let r = by.get(k);
    if (!r) by.set(k, r = {t: p.ts, v: [], f: [], src: ""});
    if (r.v[i] == null) { r.v[i] = conv(s, p[1]); r.f[i] = p[4] || ""; r.src = r.src || vitalSource(s, p); }
  } });
  return [...by.values()].sort((p, q) => p.t - q.t);
}
/* a clinic reading that is a home one (the same values within a minute) is left out, the home one saying so */
function notHome(home, clinic) {
  const at = new Map(home.map(h => [minuteOf(h.t), h]));
  return clinic.filter(c => { for (const d of [0, -1, 1]) { const h = at.get(minuteOf(c.t) + d);
    if (h && h.v.every((v, i) => Math.abs(v - c.v[i]) < .05)) { h.also = c.src; return false; } } return true; });
}
/* readings typed in at home (#224, server/manual_items.py) are in the phone's lists with the app "Typed in": one the
   phone sent too (the same values within a minute) is shown once, the phone's, saying so */
const TYPED = "Typed in";
function onceTyped(rows) {
  const at = new Map(rows.filter(r => !r.typed).map(r => [minuteOf(r.t), r]));
  return rows.filter(r => { if (!r.typed) return true; for (const d of [0, -1, 1]) { const p = at.get(minuteOf(r.t) + d);
    if (p && p.v.every((v, i) => Math.abs(v - r.v[i]) < .05)) { p.typedToo = true; return false; } } return true; });
}
function hVitals() {
  const ok = r => Array.isArray(r) && isFinite(zParse(r[0]));
  const home = onceTyped(((WATCH || {}).bp_readings || []).filter(r => ok(r) && isFinite(r[1]) && isFinite(r[2]))
    .map(r => ({t: zParse(r[0]), v: [+r[1], +r[2]], f: [], pulse: r[3] == null ? null : +r[3], src: r[4] ? `${r[4]} (${r[5] || "phone"})` : r[5] || "phone", home: true, typed: r[5] === TYPED}))
    .sort((p, q) => p.t - q.t));
  // body temperature (#184): in °F or °C as chosen in Settings → Units (#41), else as the visits' records give it
  const tSeries = SERIES.filter(VITALS.temp), pref = (CFG.units || {}).temperature;
  const unit = pref === "F" || pref === "C" ? pref : tSeries.map(s => tempScale(s.unit)).find(Boolean) || "C";
  const temps = onceTyped(((WATCH || {}).temp_readings || []).filter(r => ok(r) && isFinite(r[1]))
    .map(r => ({t: zParse(r[0]), v: [toScale(+r[1], "C", unit)], f: [], src: r[2] ? `${r[2]} (${r[3] || "phone"})` : r[3] || "phone", home: true, typed: r[3] === TYPED}))
    .sort((p, q) => p.t - q.t));
  // blood oxygen (#184): the phone's daily average and low; the visits' readings one by one; readings typed in at home (#224) one by one
  const M = Object.fromEntries(((WATCH || {}).metrics || []).map(m => [m.key, m])), low = Object.fromEntries(M.spo2_min ? M.spo2_min.pts : []);
  const days = (M.spo2_avg ? M.spo2_avg.pts : []).map(([d, v]) => ({t: dayTs(d), v: [v, low[d] ?? null], f: [], src: "Phone, the day's readings", home: true, day: true}));
  const typedO2 = ((WATCH || {}).spo2_readings || []).filter(r => ok(r) && isFinite(r[1]))
    .map(r => ({t: zParse(r[0]), v: [+r[1]], f: [], src: r[2] ? `${r[2]} (${r[3] || "phone"})` : r[3] || "phone", home: true, typed: r[3] === TYPED})).sort((p, q) => p.t - q.t);
  return tgtMark({bp: {home, clinic: notHome(home, clinicReadings([VITALS.sys, VITALS.dia]))},   // tgtMark: against targets of the person's own (#234)
    temp: {unit: "°" + unit, chosen: pref === unit, home: temps, clinic: notHome(temps, clinicReadings([VITALS.temp], (s, v) => toScale(v, tempScale(s.unit), unit)))},
    spo2: {home: days, typed: typedO2, clinic: notHome(typedO2, clinicReadings([VITALS.spo2]))}});
}
const bpText = r => `${r.v[0] != null ? fmtNum(r.v[0]) : "–"}/${r.v[1] != null ? fmtNum(r.v[1]) : "–"}`;
const homeAlso = r => `${r.typedToo ? " · also typed in" : ""}${r.also ? ` · also in the records (${r.also})` : ""}`;   // #224
const bpWhere = r => r.home ? `at home · ${r.src}${homeAlso(r)}` : `at a visit · ${r.src}`;
const flagsOf = (r, names = ["Systolic", "Diastolic"]) => r.f.concat(r.tf || []).slice(0, Math.max(r.f.length, (r.tf || []).length))
  .map((_, i) => r.f[i] ? `${names[i]} ${flagWord(r.f[i])}` : r.tf && r.tf[i] ? `${names[i]} ${tgtWord(r.tf[i])}` : "").filter(Boolean).join(", ");   // a clinic's flag, or above or below your target (#234)
/* the tile: the latest reading in the period, at home or at a visit */
function vitalTiles(tile, a, b) {
  H.vit = H.vit || hVitals();
  const {home, clinic} = H.vit.bp, all = [...home, ...clinic].filter(r => r.t >= a && r.t <= b).sort((p, q) => p.t - q.t);
  if (!home.length && !clinic.length) return;
  const last = all.at(-1);
  tile("Blood pressure", last ? bpText(last) : "–", "mmHg", last ? `${all.length} reading${all.length === 1 ? "" : "s"} · latest ${last.home ? "at home" : "at a visit"}, ${fmtD(last.t)}` : "no readings in this period", "#h-bp");
}
function renderVitals(box, {a, b, long, legend, head}) {
  H.vit = H.vit || hVitals();
  const {home, clinic} = H.vit.bp;
  if (home.length || clinic.length) bpCard(box, a, b, long, legend, head, home, clinic);
  if (H.vit.temp.home.length || H.vit.temp.clinic.length) tempCard(box, a, b, legend, head, H.vit.temp);
  if (H.vit.spo2.home.length || H.vit.spo2.typed.length || H.vit.spo2.clinic.length) oxygenCard(box, a, b, long, legend, head, H.vit.spo2);
  fitnessCards(box, {a, b, long, legend, head});   // respiratory rate and VO2 max (#226), below
}
function bpCard(box, a, b, long, legend, head, home, clinic) {
  const C = {home: "var(--c-lab)", clinic: "var(--ink2)"}, inP = r => r.t >= a && r.t <= b;
  const card = el("div", {class: "card", id: "h-bp", tabindex: -1}, head("Blood pressure", "#h-bp"),
    el("p", {class: "hint", text: long ? "Readings taken at home as weekly averages, the band running from diastolic up to systolic; readings taken at visits one by one, systolic above diastolic."
      : "Each reading's two pressures, systolic above diastolic, joined by a line: taken at home, and at visits."}));
  box.append(card);
  const all = [...home, ...clinic].filter(inP).sort((p, q) => p.t - q.t);
  const bT = [tgtIn("bp", "mmHg", "sys"), tgtIn("bp", "mmHg", "dia")];   // targets of the person's own (#234)
  tgtLine(card, "bp", "mmHg", bT.some(Boolean) ? home.filter(inP).map(r => (r.tf || []).find(Boolean) || "") : null, " Readings taken at visits keep their own report's flags.");
  if (state.tables.has("#h-bp")) {   // the list of readings: when, both pressures, pulse, where and from what
    card.append(chartTable(["Date and time", "Systolic (mmHg)", "Diastolic (mmHg)", "Pulse (bpm)", "Where", "Source"],
      [...all].reverse().map(r => [fmtDT(r.t), tgtCell(r.v[0] != null ? fmtNum(r.v[0]) : "", r, 0), tgtCell(r.v[1] != null ? fmtNum(r.v[1]) : "", r, 1),
        r.pulse != null ? fmtNum(r.pulse) : "", r.home ? "Home" : "Clinic visit", r.src + homeAlso(r).replace(/ · /g, "; ")])));
    return;
  }
  card.append(legend([[C.home, long ? "At home, weekly average" : "At home", 1], [C.clinic, "At a visit", 1]]));
  if (bT.some(Boolean)) card.lastChild.append(tgtKey());
  if (clinic.some(r => r.f.some(Boolean))) card.querySelector(".hint").append(" ▲ or ▼ marks a value the clinic's report flagged as high or low.");
  const wk = long ? weekly(home.map(r => [r.t, r.v[0], r.v[1]])) : [];
  const homePts = home.map(r => [r.t, r.v[0], r.v[1], (r.tf || [])[0] || "", (r.tf || [])[1] || "", r]), clinicPts = clinic.map(r => [r.t, r.v[0], r.v[1], r.f[0], r.f[1], r]);
  const nav = [...(long ? wk : homePts), ...clinicPts].sort((p, q) => p[0] - q[0]);
  const bL = [tgtLayer(bT[0], "Your systolic target"), tgtLayer(bT[1], "Your diastolic target")].filter(Boolean);
  hChart(card, a, b, {h: 220, fmtY: v => Math.round(v), label: long ? "Blood pressure, home readings by week" : "Blood pressure", noun: "reading", nav, layers: long ? [...bL,
    {type: "band", data: wk, color: C.home},
    {type: "line", data: wk.map(p => [p[0], p[1]]), color: C.home, width: 1.6},
    {type: "line", data: wk.map(p => [p[0], p[2]]), color: C.home, width: 1.6},
    {type: "pairs", data: clinicPts, color: C.clinic, hollow: true, r: 3.5}] : [...bL,
    {type: "pairs", data: homePts, color: C.home, r: homePts.length > 200 ? 2 : 3},
    {type: "pairs", data: clinicPts, color: C.clinic, hollow: true, r: 3.5}],
    tip: t => { const p = nearest(nav, t, long ? 4 : 2); if (!p) return null; const r = p[5];
      if (!r) return {t: p[0], rows: [el("div", {class: "v", text: `${Math.round(p[1])}/${Math.round(p[2])} mmHg`}), el("div", {class: "s", text: `Week of ${fmtD(p[0] - 3 * DAY)} · average of the readings at home`})]};
      return {t: p[0], rows: [el("div", {class: "v", text: `${bpText(r)} mmHg`}), el("div", {class: "s", text: `${fmtDT(r.t)} · ${bpWhere(r)}`}),
        r.pulse != null ? el("div", {class: "s", text: `Pulse ${fmtNum(r.pulse)} bpm`}) : null, flagsOf(r) ? el("div", {class: "s", text: flagsOf(r)}) : null]}; }});
  // the latest readings, listed (all of them with Table)
  const recent = all.slice(-5).reverse();
  if (recent.length) card.append(el("h3", {class: "sub", style: {marginTop: "10px"}, text: all.length > 5 ? `Latest 5 of ${all.length} readings in this period (Table lists them all)` : "Readings in this period"}),
    el("ul", {class: "vital-list"}, ...recent.map(r => el("li", {}, el("b", {text: `${bpText(r)} mmHg`}), r.pulse != null ? ` · pulse ${fmtNum(r.pulse)}` : "",
      flagsOf(r) ? el("span", {class: "abn", text: ` · ${flagsOf(r)}`}) : "", el("span", {class: "muted", text: ` · ${fmtDT(r.t)} · ${bpWhere(r)}`})))));
}
/* on Labs, under a vital's chart: the same vital at home, on Body and activity */
function vitalLink(s) {
  const k = !WATCH ? null : VITALS.sys(s) || VITALS.dia(s) ? "bp" : VITALS.temp(s) ? "temp" : VITALS.spo2(s) ? "spo2" : null;
  if (!k) return null;
  H = H || hData(); H.vit = H.vit || hVitals();
  if (!H.vit[k].home.length && !(H.vit[k].typed || []).length) return null;   // #224: or typed in at home
  const [id, name] = {bp: ["#h-bp", "Blood pressure"], temp: ["#h-temp", "Body temperature"], spo2: ["#h-oxygen", "Blood oxygen"]}[k];
  return el("p", {class: "hint vital-link"}, k === "spo2" && H.vit.spo2.home.length ? "The phone's daily blood oxygen is beside these on " : "Readings taken at home are beside these on ",
    el("a", {href: "#health", text: `Body and activity → ${name}`, onclick: ev => { ev.preventDefault(); openVital(id); }}), ".");
}
function openVital(id) {
  closeDrawer(); pickTab("health");
  // the card gets focus once drawn; if Body and activity draws again while its data finish loading, the card is
  // replaced and focus would fall to the page, so it's given again until it stays (about a second at most)
  let frames = 0, scrolled = false;
  const give = () => {
    const c = $(id);
    if (c && document.activeElement !== c && (document.activeElement === document.body || !document.activeElement || !document.activeElement.isConnected || frames === 0)) {
      if (!scrolled) { c.scrollIntoView({behavior: smoothly(), block: "start"}); scrolled = true; }
      c.focus({preventScroll: true});
    }
    if (++frames < 60 && (!c || document.activeElement === c || document.activeElement === document.body)) requestAnimationFrame(give);
  };
  requestAnimationFrame(give);
}

/* ---------------------------------------------------------------- blood glucose (#181) */
/* A meter's readings (finger-stick) as points, and each day a continuous glucose monitor (CGM) was worn as that day's
   lowest to highest reading with its average, from the receiver's summary (server/glucose.py; no five-minute reading
   reaches the page). Time in ranges is the share of a day's CGM readings below 54, below 70 (those below 54
   included), 70–180 and above 180 mg/dL: the standard published ranges, labelled so, never advice or anyone's own
   targets. Values are in this person's lab units (#41: mg/dL, or mmol/L with the SI choice) by the metrics' conv.
   Lab glucose stays a Labs series; it is drawn here too only when asked (gOpt.labs, remembered per profile). */
const G_STD = {mg: ["54", "70", "180"], mmol: ["3.0", "3.9", "10.0"]};   // the standard ranges, as each unit writes them
const G_C = {cgm: "var(--c-journal)", meter: "var(--c-message)", lab: "var(--c-lab)", lt54: "var(--seq1)", lt70: "var(--seq3)", in: "var(--c-journal)", gt180: "var(--c-study)"};
const gOpt = {labs: false};
const G_MEAL = {before: "before a meal", after: "after a meal", other: "other"};   // a typed reading's note, when chosen (#224)
const gMeter = (m, fmt, u) => `${m[2] || "Meter"}, ${fmtTime(m[0])}: ${fmt(m[1])} ${u}${m[3] ? " · " + m[3] : ""}${m[4] ? " · " + tgtWord(m[4]) : ""}`;
const gKey = () => "ht-glucose" + (PROFILE ? ":" + PROFILE.id : "");
let G;   // undefined until made, null when there is no glucose
function gData() {
  const M = Object.fromEntries(((WATCH || {}).metrics || []).filter(m => m.key.startsWith("glucose_")).map(m => [m.key, m]));
  const typed = ((WATCH || {}).glucose_readings || []).filter(r => Array.isArray(r) && isFinite(zParse(r[0])) && isFinite(r[1]));   // #224: [time, mg/dL, source, app, meal]
  const base = M.glucose_avg || M.glucose_meter;
  if (!base && !typed.length) return null;
  try { gOpt.labs = JSON.parse(localStorage.getItem(gKey()) || "{}").labs === true; } catch (e) {}
  const uc = ucChoose(base ? base.conv : WATCH.glucose_conv), mmol = !!uc && /mmol/i.test(uc.to), cv = v => v == null ? null : uc ? v * uc.a + uc.b : v;
  const col = k => M[k] ? Object.fromEntries(M[k].pts) : {};
  const [lo, hi, n, hours, lt54, lt70, inr, gt180, olt, oin, ogt] = ["min", "max", "n", "hours", "lt54", "lt70", "in", "gt180", "own_lt", "own_in", "own_gt"].map(k => col("glucose_" + k));
  const days = (M.glucose_avg ? M.glucose_avg.pts : []).map(([d, v]) => ({t: dayTs(d), d, avg: cv(v), lo: cv(lo[d]), hi: cv(hi[d]), n: n[d] || 0,
    hours: hours[d], lt54: lt54[d], lt70: lt70[d], in: inr[d], gt180: gt180[d], olt: olt[d], oin: oin[d], ogt: ogt[d]}));   // own: time in the person's own range (#234)
  const phone = (M.glucose_meter ? M.glucose_meter.pts : []).map(([w, v]) => [zParse(w), cv(v)]).filter(p => isFinite(p[0]));
  // readings typed in at home (#224), [t, value, source, meal note]: one the phone sent too (within a minute) once
  const near = (p, q) => Math.abs(p[0] - q[0]) <= 6e4 && Math.abs(p[1] - q[1]) < (mmol ? .05 : .5);
  const meter = [...phone, ...typed.map(r => [zParse(r[0]), cv(+r[1]), r[3] || TYPED, G_MEAL[r[4]] || ""]).filter(p => !phone.some(q => near(p, q)))]
    .sort((p, q) => p[0] - q[0]);
  // lab glucose in this chart's unit, by the server's exact factor (mg/dL to mmol/L) when the units differ
  const F = ((base ? base.conv : WATCH.glucose_conv) || {}).si?.[1], want = mmol ? "mmol/l" : "mg/dl";   // typed in only: no base (#232)
  const inUnit = (v, u) => { const k = String(u || "").toLowerCase().replace(/\s/g, "");
    return k === want ? v : !F ? null : k === "mg/dl" ? v * F : k === "mmol/l" ? v / F : null; };
  const lab = [];
  for (const s of SERIES) {
    if (s.watch || !/glucose/i.test(s.name) || /a1c|urine|\bur\b|csf|fluid|tolerance|gtt|challenge/i.test(s.name)) continue;
    for (const p of s.pts) { const v = typeof p[1] === "number" ? inUnit(p[1], s.unit) : null; if (v != null && isFinite(p.ts)) lab.push([p.ts, v, p[4], s.name]); }
  }
  lab.sort((p, q) => p[0] - q[0]);
  const all = [...days.map(d => d.t), ...meter.map(p => p[0])];
  const mT = tgtIn("glucose", uc ? uc.to : "mg/dL");   // a target of the person's own for meter readings (#234): each one's flag, p[4]
  for (const p of meter) p[4] = mT ? tgtSide(p[1], ...mT, mmol ? 1 : 0) : "";
  return {unit: uc ? uc.to : "mg/dL", mT, cT: tgtIn("cgm", uc ? uc.to : "mg/dL"), std: mmol ? G_STD.mmol : G_STD.mg, inStd: mmol ? [3.9, 10] : [70, 180], days, meter, lab,
    first: Math.min(...all), last: Math.max(...all), fmt: v => v == null ? "" : mmol ? v.toFixed(1) : String(Math.round(v))};
}
/* the CGM days and meter readings in [a, b], with the period's time in ranges (each day's weighed by its readings) */
function gPeriod(a, b) {
  const ds = G.days.filter(d => d.t >= a && d.t <= b), n = ds.reduce((s, d) => s + d.n, 0);
  const share = k => n ? ds.reduce((s, d) => s + (d[k] || 0) * d.n, 0) / n : null;
  const od = ds.filter(d => d.oin != null), on = od.reduce((s, d) => s + d.n, 0), own = k => on ? od.reduce((s, d) => s + (d[k] || 0) * d.n, 0) / on : null;   // (#234)
  return {ds, n, avg: share("avg"), lt54: share("lt54"), lt70: share("lt70"), in: share("in"), gt180: share("gt180"), od, olt: own("olt"), oin: own("oin"), ogt: own("ogt"),
    hours: ds.reduce((s, d) => s + (d.hours || 0), 0), meter: G.meter.filter(p => p[0] >= a && p[0] <= b)};
}
const gPct = v => v == null ? "–" : v > 0 && v < 0.05 ? "under 0.1%" : (v > 0 && v < 9.95 ? v.toFixed(1) : String(Math.round(v))) + "%";
const gHours = h => h == null ? "" : `${Number.isInteger(h) ? h : h.toFixed(1)} h`;
/* the tile, among Body and activity's */
function gTile(tile, a, b) {
  if (G === undefined) G = gData();
  if (!G) return;
  const P = gPeriod(a, b), [, l, h] = G.std;
  if (P.n) tile("Glucose", G.fmt(P.avg), `${G.unit} avg`, `${gPct(P.in)} of CGM readings in ${l}–${h}`, "#h-glucose");
  else if (P.meter.length) { const k = P.meter.filter(p => p[2]).length;   // typed in among them (#232)
    tile("Glucose", G.fmt(mean(P.meter.map(p => p[1]))), `${G.unit} avg`, `${P.meter.length} meter reading${P.meter.length === 1 ? "" : "s"}` +
      (!k ? "" : k === P.meter.length ? " typed in" : `, ${k} typed in`), "#h-glucose"); }
  else tile("Glucose", "–", G.unit, `last ${fmtD(G.last)}`, "#h-glucose");
}
/* the card: time in ranges for the period's CGM days, then the chart or its table (#60). head, legend and byDay are
   renderHealth's */
function hGlucose(box, a, b, {head, legend, byDay}) {
  if (G === undefined) G = gData();
  if (!G) return;
  const u = G.unit, [s0, s1, s2] = G.std, fmt = G.fmt, P = gPeriod(a, b), labs = gOpt.labs && G.lab.length > 0, table = state.tables.has("#h-glucose");
  const card = el("div", {class: "card", id: "h-glucose"}, head("Blood glucose", "#h-glucose"),
    el("p", {class: "hint", text: !G.days.length && G.meter.every(m => m[2]) ? "Meter (finger-stick) readings typed in at home, as points."   // #224
      : "From a phone: each day a continuous glucose monitor (CGM) was worn, its lowest to highest reading and its average; meter (finger-stick) readings as points" + (G.meter.some(m => m[2]) ? ", those typed in at home among them, said so." : ".")}));
  box.append(card);
  tgtLine(card, "glucose", u, G.mT ? P.meter.map(p => p[4]) : null);   // targets of the person's own (#234)
  if (G.lab.length) card.append(el("div", {class: "toolbar"}, el("label", {},
    el("input", {type: "checkbox", checked: gOpt.labs || null, onchange: e => { gOpt.labs = e.target.checked; try { localStorage.setItem(gKey(), JSON.stringify({labs: gOpt.labs})); } catch (x) {} renderHealth(); }}),
    "Show lab glucose results alongside")));
  if (P.n) {   // time in ranges, the period's CGM days together
    const parts = [["lt54", P.lt54], ["lt70", Math.max(0, P.lt70 - P.lt54)], ["in", P.in], ["gt180", P.gt180]];
    const cover = P.ds.length ? Math.min(100, P.hours / (P.ds.length * 24) * 100) : 0;
    card.append(el("div", {class: "glu-tir"},
      el("h3", {class: "sub", text: `Time in ranges · ${P.ds.length} CGM day${P.ds.length === 1 ? "" : "s"} in this period, ${gPct(cover)} of the time with readings`}),
      el("div", {class: "glu-bar", "aria-hidden": "true"}, ...parts.filter(p => p[1] > 0).map(([k, v]) => el("span", {style: {width: v + "%", background: G_C[k]}}))),
      el("ul", {}, ...[["lt54", `Below ${s0} ${u}`, P.lt54], ["lt70", `Below ${s1} (with below ${s0})`, P.lt70], ["in", `${s1}–${s2}`, P.in], ["gt180", `Above ${s2}`, P.gt180]]
        .map(([k, t, v]) => el("li", {}, el("i", {style: {background: G_C[k]}}), `${t}: `, el("b", {text: gPct(v)})))),
      el("p", {class: "hint", text: `The standard published ranges for CGM readings (${s1}–${s2} ${u}, with below ${s1} and below ${s0} counted separately): not advice, and not anyone's own targets.`})));
    if (G.cT) {   // beside it, time in the person's own range (#234), labelled as theirs
      const t = targetOf("cgm"), who = tgtWho(t), range = tgtText("cgm", u), part = P.od.length < P.ds.length ? P.ds.length - P.od.length : 0;
      card.append(el("div", {class: "glu-tir glu-own"},
        el("h3", {class: "sub", text: `Time in your own range · ${range}, your target as your care team gave it${who ? ` (${who})` : ""}`}),
        P.od.length ? el("ul", {}, ...[["Below it", P.olt], ["In it", P.oin], ["Above it", P.ogt]].map(([w, v]) => el("li", {}, `${w}: `, el("b", {text: gPct(v)}))))
          : el("p", {class: "muted", text: "Not known for these CGM days: they came before Sanguity kept what this needs."}),
        el("p", {class: "hint", text: `${P.od.length ? `Over ${P.od.length} CGM day${P.od.length === 1 ? "" : "s"} in this period` : ""}${part && P.od.length ? ` (${part} came before Sanguity kept what this needs)` : ""}${P.od.length ? "; t" : "T"}he standard published ranges are above, labelled as the publisher's.`})));
    }
  }
  // the days in the period, each with its meter readings and (when shown) lab results
  const meterBy = new Map(), labBy = new Map(), dayBy = new Map(G.days.map(d => [d.d, d]));
  for (const p of G.meter) { const k = isoDay(p[0]); meterBy.set(k, [...(meterBy.get(k) || []), p]); }
  if (labs) for (const p of G.lab) { const k = isoDay(p[0]); labBy.set(k, [...(labBy.get(k) || []), p]); }
  const dayPts = [...new Set([...G.days.map(d => d.d), ...meterBy.keys(), ...labBy.keys()])].sort().map(d => [dayTs(d)]);
  if (!dayPts.some(p => p[0] >= a && p[0] <= b)) { card.append(el("p", {class: "hint", text: `No glucose readings in this period (the last on ${fmtD(G.last)}).`})); return; }
  if (table) {
    card.append(chartTable(["Date", `Average (${u})`, `Low–high (${u})`, `${s1}–${s2} (%)`, `Below ${s1} (%)`, `Below ${s0} (%)`, `Above ${s2} (%)`, "Hours with readings", `Meter readings (${u})`, ...(labs ? [`Lab results (${u})`] : [])],
      byDay(dayPts).map(({t}) => { const k = isoDay(t), d = dayBy.get(k), ls = labBy.get(k) || [];
        return [fmtD(t), d ? fmt(d.avg) : "", d ? `${fmt(d.lo)}–${fmt(d.hi)}` : "", d ? gPct(d.in) : "", d ? gPct(d.lt70) : "", d ? gPct(d.lt54) : "", d ? gPct(d.gt180) : "",
          d ? `${gHours(d.hours)} (${d.n} readings)` : "", (meterBy.get(k) || []).map(m => `${fmtTime(m[0])} ${fmt(m[1])}${m[2] || m[4] ? ` (${[m[2] && m[2].toLowerCase(), m[3], tgtWord(m[4])].filter(Boolean).join(", ")})` : ""}`).join("; "),
          ...(labs ? [ls.length ? [ls.map(l => `${fmt(l[1])} (${nice(l[3])})`).join("; "), ls.find(l => l[2])?.[2] || ""] : ""] : [])]; })));
    return;
  }
  card.append(legend([[G_C.cgm, "CGM daily average"], ["color-mix(in srgb, " + G_C.cgm + " 30%, transparent)", "CGM daily low–high"], [G_C.meter, G.meter.some(m => m[2]) ? "Meter reading, from the phone or typed in" : "Meter reading", 1],
    ...(labs ? [[G_C.lab, "Lab result", 1]] : []), ["color-mix(in srgb, var(--muted) 30%, transparent)", `${s1}–${s2} ${u}, the standard range`]]));
  if (G.mT) card.lastChild.append(tgtKey("Your target for meter readings, as your care team gave it"));
  const std = [];   // the standard range as a band, over the days with glucose
  for (let t = dayTs(isoDay(G.first)) - DAY; t <= G.last + DAY; t += DAY) std.push([t, ...G.inStd]);
  const runs = [];   // the CGM's days in runs, so a band never bridges the weeks between two sensors
  for (const d of G.days) { const r = runs[runs.length - 1]; if (r && d.t - r[r.length - 1].t <= 1.5 * DAY) r.push(d); else runs.push([d]); }
  const long = b - a > 200 * DAY;
  hChart(card, a, b, {h: 220, fmtY: v => fmt(v), label: "Blood glucose", noun: "day", nav: dayPts, layers: [
    {type: "band", data: std, color: "var(--muted)"}, ...(G.mT ? [tgtLayer(G.mT, "Your target, meter readings")] : []),
    ...runs.flatMap(r => r.length > 1 ? [{type: "band", data: r.map(d => [d.t, d.lo, d.hi]), color: G_C.cgm}, {type: "line", data: r.map(d => [d.t, d.avg]), color: G_C.cgm, width: 1.8, gap: 2}]
      : [{type: "dots", data: [[r[0].t, r[0].avg]], color: G_C.cgm}]),
    {type: "dots", data: G.meter, color: G_C.meter, flag: p => p[4]},
    ...(labs ? [{type: "dots", data: G.lab, color: G_C.lab, hollow: true, r: 4}] : [])],
    tip: t => { const p = nearest(dayPts, t, long ? 6 : .6); if (!p) return null;
      const k = isoDay(p[0]), d = dayBy.get(k), ms = meterBy.get(k) || [], ls = labBy.get(k) || [];
      return {t: p[0], rows: [el("div", {class: "v", text: fmtD(p[0])}),
        d ? tipRow(G_C.cgm, `CGM average ${fmt(d.avg)} ${u}, ${fmt(d.lo)}–${fmt(d.hi)}`) : null,
        d ? el("div", {class: "s", text: `${s1}–${s2}: ${gPct(d.in)} · below ${s1}: ${gPct(d.lt70)} (below ${s0}: ${gPct(d.lt54)}) · above ${s2}: ${gPct(d.gt180)}`}) : null,
        d ? el("div", {class: "s", text: `${gHours(d.hours)} of readings (${d.n})`}) : null,
        ...ms.slice(0, 6).map(m => tipRow(G_C.meter, gMeter(m, fmt, u))), ms.length > 6 ? el("div", {class: "s", text: `and ${ms.length - 6} more meter readings`}) : null,
        ...ls.map(l => tipRow(G_C.lab, `${nice(l[3])}, lab: ${fmt(l[1])} ${u}${l[2] ? " · " + flagWord(l[2]) : ""}`))]}; }});
}

/* the latest few readings of a card, listed: text(r) -> what was measured */
function latestList(card, all, n, text, where, flags) {
  const recent = all.slice(-n).reverse();
  if (!recent.length) return;
  card.append(el("h3", {class: "sub", style: {marginTop: "10px"}, text: all.length > n ? `Latest ${n} of ${all.length} in this period (Table lists them all)` : "In this period"}),
    el("ul", {class: "vital-list"}, ...recent.map(r => el("li", {}, el("b", {text: text(r)}), flags(r) ? el("span", {class: "abn", text: ` · ${flags(r)}`}) : "",
      el("span", {class: "muted", text: ` · ${where(r)}`})))));
}
/* body temperature (#184): each reading at home and at a visit, in the unit chosen */
function tempCard(box, a, b, legend, head, {unit, chosen, home, clinic}) {
  const C = {home: "var(--c-imaging)", clinic: "var(--ink2)"};
  const val = r => `${r.v[0].toFixed(1)} ${unit}`, where = r => `${fmtDT(r.t)} · ${r.home ? "at home" : "at a visit"} · ${r.src}${homeAlso(r)}`;
  const flags = r => flagsOf(r, ["Temperature"]);
  const card = el("div", {class: "card", id: "h-temp", tabindex: -1}, head("Body temperature", "#h-temp"),
    el("p", {class: "hint", text: `Each reading of a thermometer at home, and the readings taken at visits, in ${unit}${chosen ? " (Settings → Units)" : ""}.`
      + (clinic.some(r => r.f[0]) ? " ▲ or ▼ marks a reading the clinic's report flagged as high or low." : "")}));
  box.append(card);
  const all = [...home, ...clinic].filter(r => r.t >= a && r.t <= b).sort((p, q) => p.t - q.t);
  const tT = tgtIn("temp", unit);   // a target of the person's own (#234)
  tgtLine(card, "temp", unit, tT ? home.filter(r => r.t >= a && r.t <= b).map(r => (r.tf || [])[0] || "") : null);
  if (state.tables.has("#h-temp")) {
    card.append(chartTable(["Date and time", `Temperature (${unit})`, "Where", "Source"],
      [...all].reverse().map(r => [fmtDT(r.t), tgtCell(r.v[0].toFixed(1), r, 0), r.home ? "Home" : "Clinic visit", r.src + homeAlso(r).replace(/ · /g, "; ")])));
    return;
  }
  card.append(legend([[C.home, "At home", 1], [C.clinic, "At a visit", 1]]));
  if (tT) card.lastChild.append(tgtKey());
  const homePts = home.map(r => [r.t, r.v[0], null, (r.tf || [])[0] || "", "", r]), clinicPts = clinic.map(r => [r.t, r.v[0], null, r.f[0], "", r]);
  const nav = [...homePts, ...clinicPts].sort((p, q) => p[0] - q[0]);
  hChart(card, a, b, {h: 160, fmtY: v => String(+v.toFixed(1)), label: "Body temperature", noun: "reading", nav, layers: [...(tT ? [tgtLayer(tT)] : []),
    {type: "pairs", data: homePts, color: C.home}, {type: "pairs", data: clinicPts, color: C.clinic, hollow: true, r: 3.5}],
    tip: t => { const p = nearest(nav, t, 2); if (!p) return null; const r = p[5];
      return {t: p[0], rows: [el("div", {class: "v", text: val(r)}), el("div", {class: "s", text: where(r)}), flags(r) ? el("div", {class: "s", text: flags(r)}) : null]}; }});
  latestList(card, all, 3, val, where, flags);
}
/* blood oxygen (#184): the phone's daily average above the day's low, and each reading at a visit; a reading typed in
   at home (#224) one by one, filled, as a visit's is hollow */
function oxygenCard(box, a, b, long, legend, head, {home, typed = [], clinic}) {
  const C = {home: "var(--c-journal)", typed: "var(--c-lab)", clinic: "var(--ink2)"};
  const val = r => r.day ? `${fmtNum(r.v[0])}% average${r.v[1] != null ? `, low ${fmtNum(r.v[1])}%` : ""}` : `${fmtNum(r.v[0])}%`;
  const where = r => r.day ? `${fmtD(r.t)} · ${r.src}` : `${fmtDT(r.t)} · ${r.home ? "at home" : "at a visit"} · ${r.src}${homeAlso(r)}`, flags = r => flagsOf(r, ["SpO2"]);
  const card = el("div", {class: "card", id: "h-oxygen", tabindex: -1}, head("Blood oxygen (SpO2)", "#h-oxygen"),
    el("p", {class: "hint", text: (home.length ? (long ? "The phone's daily average by week, the band reaching down to the week's average low" : "The phone's daily average, the band reaching down to the day's low") + "; " : "")
      + (typed.length ? "readings taken at home one by one, and " : "") + (home.length || typed.length ? "readings taken at visits one by one." : "Readings taken at visits one by one.")
      + (clinic.some(r => r.f[0]) ? " ▲ or ▼ marks a reading the clinic's report flagged as high or low." : "")}));
  box.append(card);
  const all = [...home, ...typed, ...clinic].filter(r => r.t >= a && r.t <= b).sort((p, q) => p.t - q.t);
  const oT = tgtIn("spo2", "%");   // a target of the person's own (#234): each day's average, and each reading typed in
  tgtLine(card, "spo2", "%", oT ? all.filter(r => r.home).map(r => (r.tf || [])[0] || "") : null, oT && home.length ? " At home: each day's average from the phone, and each reading typed in." : "");
  if (state.tables.has("#h-oxygen")) {
    card.append(chartTable(["Date", "SpO2 (%)", "Day's low (%)", "Where", "Source"],
      [...all].reverse().map(r => [r.day ? fmtD(r.t) : fmtDT(r.t), tgtCell(fmtNum(r.v[0]), r, 0), r.v[1] != null ? fmtNum(r.v[1]) : "",
        r.day ? "Home, daily average" : r.home ? "Home, one reading" : "Clinic visit", r.src + homeAlso(r).replace(/ · /g, "; ")])));
    return;
  }
  card.append(legend([...(home.length ? [[C.home, long ? "Phone, weekly" : "Phone, daily average", 1]] : []), ...(typed.length ? [[C.typed, "At home, one reading", 1]] : []),
    [C.clinic, "At a visit", 1]]));
  if (oT) card.lastChild.append(tgtKey());
  const dayPts = home.map(r => [r.t, r.v[0], r.v[1] ?? r.v[0], r]), pts = long ? weekly(dayPts.map(p => p.slice(0, 3))) : dayPts;
  const clinicPts = clinic.map(r => [r.t, r.v[0], null, r.f[0], "", r]), typedPts = typed.map(r => [r.t, r.v[0], null, (r.tf || [])[0] || "", "", r]);
  const nav = [...(long ? pts : home.map(r => [r.t, r.v[0], r.v[1], "", "", r])), ...typedPts, ...clinicPts].sort((p, q) => p[0] - q[0]);
  hChart(card, a, b, {h: 160, fmtY: v => Math.round(v) + "%", label: long ? "Blood oxygen, weekly" : "Blood oxygen", noun: long ? "week or reading" : "day or reading", nav, layers: [
    ...(oT ? [tgtLayer(oT)] : []),
    {type: "band", data: pts, color: C.home}, {type: "line", data: pts, color: C.home, width: 1.6, gap: long ? 21 : 3},
    ...(long ? [] : [{type: "dots", data: pts, color: C.home, r: pts.length > 200 ? 1.5 : 2.4, flag: p => p[3] && p[3].tf ? p[3].tf[0] : ""}]),
    {type: "pairs", data: typedPts, color: C.typed, r: 3.5},
    {type: "pairs", data: clinicPts, color: C.clinic, hollow: true, r: 3.5}],
    tip: t => { const p = nearest(nav, t, long ? 4 : .6); if (!p) return null; const r = p[5];
      if (!r) return {t: p[0], rows: [el("div", {class: "v", text: `${fmtNum(p[1])}% average, low ${fmtNum(p[2])}%`}), el("div", {class: "s", text: `Week of ${fmtD(p[0] - 3 * DAY)} · phone, daily figures`})]};
      return {t: p[0], rows: [el("div", {class: "v", text: val(r)}), el("div", {class: "s", text: where(r)}), flags(r) ? el("div", {class: "s", text: flags(r)}) : null]}; }});
  latestList(card, all, 3, val, where, flags);
}

/* ---------------------------------------------------------------- respiratory rate and cardio fitness (#226) */
/* The phone's daily figures from watch/receiver.py (resp and vo2max: from Health Connect, Health Auto Export or an
   uploaded Apple Health export, made the same way), a small card each. Respiratory rate is the day's average of what
   a watch measured (most measure it during sleep), beside the rates taken at visits (the records' vitals, LOINC 9279-1
   or by name, per minute), told apart. VO2 max is the watch's own estimate of cardio fitness, in mL/kg/min, shown as it
   gives it: no fitness levels or categories of Sanguity's own. */
const RESP_TEST = s => !s.watch && /min/i.test(s.unit || "") && (vitalCode(s) === "9279-1" || /respirat|\bresp\b|breathing rate/i.test(s.name || ""));
function hFitness() {
  const M = Object.fromEntries(((WATCH || {}).metrics || []).map(m => [m.key, m]));
  const days = (k, src) => (M[k] ? M[k].pts : []).map(([d, v]) => ({t: dayTs(d), v: [v], f: [], src, home: true, day: true}));
  return {resp: {home: days("resp", "phone, the day's average"), clinic: clinicReadings([RESP_TEST]), unit: (M.resp || {}).unit || "breaths/min"},
    vo2: {home: days("vo2max", "phone, the watch's estimate"), clinic: [], unit: (M.vo2max || {}).unit || "mL/kg/min"}};
}
function fitnessCards(box, vx) {
  H.fit = H.fit || hFitness();
  const {resp, vo2} = H.fit;
  if (resp.home.length || resp.clinic.length) phoneDayCard(box, vx, {...resp, id: "h-resp", title: "Respiratory rate", name: "Respiratory rate", color: "var(--c-study)", near: .6,
    hint: long => `${long ? "The phone's daily average by week" : "The phone's daily average"} of the breaths a minute a watch measured (most measure it during sleep); rates taken at visits one by one.`});
  if (vo2.home.length) phoneDayCard(box, vx, {...vo2, id: "h-vo2", title: "Cardio fitness (VO2 max)", name: "VO2 max", color: "var(--c-procedure)", near: 4,
    hint: long => `The watch's estimate of VO2 max, the most oxygen the body uses in hard exercise, in ${vo2.unit}${long ? ", by week" : ""}: as the device gives it, without fitness levels.`});
}
/* one card: the phone's days (weekly averages over a long period) as a line with dots, the visits' readings hollow */
function phoneDayCard(box, {a, b, long, legend, head}, o) {
  const C = {home: o.color, clinic: "var(--ink2)"}, key = "#" + o.id, visits = o.clinic.length > 0, fmt = v => fmtNum(Math.round(v * 10) / 10);
  const val = r => `${fmt(r.v[0])} ${o.unit}`, flags = r => flagsOf(r, [o.name]);
  const where = r => r.day ? `${fmtD(r.t)} · ${r.src}` : `${fmtDT(r.t)} · at a visit · ${r.src}`;
  const card = el("div", {class: "card", id: o.id, tabindex: -1}, head(o.title, key),
    el("p", {class: "hint", text: o.hint(long) + (o.clinic.some(r => r.f[0]) ? " ▲ or ▼ marks a reading the clinic's report flagged as high or low." : "")}));
  box.append(card);
  const all = [...o.home, ...o.clinic].filter(r => r.t >= a && r.t <= b).sort((p, q) => p.t - q.t);
  if (state.tables.has(key)) {
    card.append(chartTable(["Date", `${o.name} (${o.unit})`, ...(visits ? ["Where"] : []), "Source"],
      [...all].reverse().map(r => [r.day ? fmtD(r.t) : fmtDT(r.t), [fmt(r.v[0]), r.f[0]], ...(visits ? [r.day ? "Phone" : "Clinic visit"] : []),
        r.day ? r.src[0].toUpperCase() + r.src.slice(1) : r.src])));
    return;
  }
  card.append(legend([[C.home, long ? "Phone, weekly average" : "Phone, by day", 1], ...(visits ? [[C.clinic, "At a visit", 1]] : [])]));
  const dayPts = o.home.map(r => [r.t, r.v[0], null, "", "", r]), pts = long ? weekly(o.home.map(r => [r.t, r.v[0]])) : dayPts;
  const clinicPts = o.clinic.map(r => [r.t, r.v[0], null, r.f[0], "", r]);
  const nav = [...pts, ...clinicPts].sort((p, q) => p[0] - q[0]);
  hChart(card, a, b, {h: 150, fmtY: v => String(+v.toFixed(1)), label: long ? `${o.title}, weekly` : o.title,
    noun: visits ? "reading" : long ? "week" : "day", nav, layers: [   // "readings": the phone's days and the visits'
    {type: "line", data: pts, color: C.home, width: 1.6, gap: long ? 21 : 10},
    {type: "dots", data: pts, color: C.home, r: pts.length > 200 ? 1.5 : 2.6},
    {type: "pairs", data: clinicPts, color: C.clinic, hollow: true, r: 3.5}],
    tip: t => { const p = nearest(nav, t, long ? 4 : o.near); if (!p) return null; const r = p[5];
      if (!r) return {t: p[0], rows: [el("div", {class: "v", text: `${fmt(p[1])} ${o.unit} average`}), el("div", {class: "s", text: `Week of ${fmtD(p[0] - 3 * DAY)} · phone, daily figures`})]};
      return {t: p[0], rows: [el("div", {class: "v", text: val(r)}), el("div", {class: "s", text: where(r)}), flags(r) ? el("div", {class: "s", text: flags(r)}) : null]}; }});
  latestList(card, all, 3, val, where, flags);
}

/* ---------------------------------------------------------------- heart rate zones on workouts (#197) */
/* The commonly published five zones (50–60%, 60–70%, 70–80%, 80–90% and 90% and above of a maximum heart rate), from
   the maximum entered in About this person or else estimated as 220 minus the age on each workout's day, the common
   formula, said so wherever zones show. server/hrzones.py works out each workout's seconds in each zone from the
   phone's readings (WATCH.exercise[i].hr: avg, max, zmax, basis, age, zones: [below zone 1, zone 1 … 5]); without
   readings only its average and highest are shown against the zones. Without a birth date or a maximum, no zones:
   it says how to add one. A convention, never a judgement of the workout. */
const Z_PCT = [50, 60, 70, 80, 90];
const Z_C = ["var(--zone1)", "var(--zone2)", "var(--zone3)", "var(--zone4)", "var(--zone5)"];
const zoneBounds = zmax => Z_PCT.map(p => Math.floor((zmax * p + 50) / 100));   // each zone's lower limit, as hrzones.bounds
const zoneOf = (bpm, zmax) => zoneBounds(zmax).filter(x => Math.round(bpm) >= x).length;   // 0: below zone 1
const zoneRange = (k, zmax) => { const lo = zoneBounds(zmax); return k === 5 ? `${lo[4]} bpm and above` : `${lo[k - 1]}–${lo[k] - 1} bpm`; };
const zoneName = k => k ? `zone ${k}` : "below zone 1";
const zMin = s => Math.round(s / 60);
const aboutLink = () => el("a", {href: `sharing.html?profile=${PROFILE.id}#about`, text: "About this person"});
/* where a workout's zones come from, in words */
const zoneBasis = hr => hr.basis === "measured" ? `the maximum heart rate entered in About this person, ${hr.zmax} bpm`
  : `an estimated maximum heart rate of ${hr.zmax} bpm: 220 minus age ${hr.age}, the common formula`;
/* how to get zones, when there's no birth date or maximum */
const zonesNeed = () => el("p", {class: "hint zones-need"}, "Heart rate zones need a birth date or a measured maximum heart rate: ",
  canEdit() ? "add one in " : "an owner or editor can add one in ", aboutLink(), ".");
/* one workout's heart rate and zones: for the drawer (workout details) and the zones card */
function workoutZones(hr) {
  if (!hr || (hr.avg == null && hr.max == null)) return null;
  const k = v => hr.zmax ? ` (${zoneName(zoneOf(v, hr.zmax))})` : "";
  const parts = [el("p", {class: "zone-line"}, [hr.avg != null ? `Average ${Math.round(hr.avg)} bpm${k(hr.avg)}` : "", hr.max != null ? `highest ${Math.round(hr.max)} bpm${k(hr.max)}` : ""].filter(Boolean).join(" · "))];
  if (!hr.zmax) return el("div", {class: "zones-one"}, ...parts, zonesNeed());
  if (hr.zones) {
    const total = hr.zones.reduce((s, x) => s + x, 0) || 1;
    parts.push(el("table", {class: "tbl zones-tbl"}, el("caption", {class: "vh", text: "Time in each heart rate zone"}),
      el("thead", {}, el("tr", {}, ...["Zone", "Heart rate", "Time"].map(h => el("th", {scope: "col", text: h})))),
      el("tbody", {}, ...[5, 4, 3, 2, 1, 0].map(z => el("tr", {}, el("th", {scope: "row"}, z ? el("i", {class: "zone-key", style: {background: Z_C[z - 1]}}) : null, z ? `Zone ${z}` : "Below zone 1"),
        el("td", {text: z ? zoneRange(z, hr.zmax) : `under ${zoneBounds(hr.zmax)[0]} bpm`}),
        el("td", {text: `${zMin(hr.zones[z])} min${hr.zones[z] ? ` (${Math.round(hr.zones[z] / total * 100)}%)` : ""}`}))))));
  } else parts.push(el("p", {class: "muted", text: "The phone sent no readings during it, only its average and highest, so the time in each zone isn't known."}));
  parts.push(el("p", {class: "muted", text: `Zones from ${zoneBasis(hr)}. A common convention, not advice.`}));
  return el("div", {class: "zones-one"}, ...parts);
}
/* the card on Body and activity: minutes in each zone a week, from the workouts with readings; the period's workouts
   with heart rate listed, each opening its details. head, legend and byDay are renderHealth's */
function hZones(box, a, b, {long, legend, head, byDay}) {
  const info = (WATCH || {}).hrZones || {}, withHr = H.ex.filter(x => x.hr && (x.hr.avg != null || x.hr.max != null));
  if (!withHr.length) return;
  const inP = withHr.filter(x => x.t >= a && x.t <= b), key = "#h-zones";
  const card = el("div", {class: "card", id: "h-zones", tabindex: -1});
  box.append(card);
  if (!info.basis) {   // nothing to show zones against: say how to add one
    card.append(el("div", {class: "list-hd"}, el("h2", {text: "Heart rate zones"})),
      el("p", {class: "hint", text: `${withHr.length} workout${withHr.length === 1 ? " has" : "s have"} heart rate from the phone.`}), zonesNeed());
    return;
  }
  const last = [...inP].reverse().find(x => x.hr.zmax) || [...withHr].reverse().find(x => x.hr.zmax);
  card.append(head("Heart rate zones", key), el("p", {class: "hint", text: "Minutes a week in each of the five common zones, from the workouts the phone sent heart rate readings for. "
    + (info.basis === "measured" ? `Zones from the maximum heart rate entered in About this person, ${info.maxHr} bpm.`
      : `Zones from an estimated maximum heart rate, 220 minus age (the common formula), so it follows the age on each workout's day${last ? `: ${last.hr.zmax} bpm at ${last.hr.age} on the latest here` : ""}.`)
    + " A common convention, not advice."}));
  const wk = t => { const d = zdate(t); d.setHours(12, 0, 0, 0); d.setDate(d.getDate() - (d.getDay() + 6) % 7); return d.getTime() + 3 * DAY; };
  const weeks = new Map();
  for (const x of withHr) { if (!x.hr.zones) continue; const k = wk(x.t), w = weeks.get(k) || {z: [0, 0, 0, 0, 0, 0], n: 0}; x.hr.zones.forEach((s, i) => { w.z[i] += s; }); w.n++; weeks.set(k, w); }
  const pts = [...weeks.entries()].sort((p, q) => p[0] - q[0]).map(([t, w]) => [t, ...w.z.slice(1).map(s => s / 60), w]);
  if (state.tables.has(key)) {
    card.append(chartTable(["Week", ...[1, 2, 3, 4, 5].map(k => `Zone ${k} (min)`), "Below zone 1 (min)", "Workouts with readings"],
      byDay(pts).map(({t, v: [p]}) => ["Week of " + fmtD(t - 3 * DAY), ...p.slice(1, 6).map(m => String(Math.round(m))), String(zMin(p[6].z[0])), String(p[6].n)])));
  } else {
    card.append(legend(Z_PCT.map((p, i) => [Z_C[i], `Zone ${i + 1} · ${p}${i < 4 ? `–${Z_PCT[i + 1]}` : "% and above"}${i < 4 ? "%" : ""}`])));
    hChart(card, a, b, {h: 180, fmtY: v => Math.round(v) + "m", label: "Heart rate zones, minutes a week", noun: "week", nav: pts, layers: [
      {type: "bars", data: pts, keys: ["z1", "z2", "z3", "z4", "z5"], colors: Z_C, every: 7 * DAY}],
      tip: t => { const p = nearest(pts, t, 4); if (!p) return null;
        return {t: p[0], rows: [el("div", {class: "v", text: `Week of ${fmtD(p[0] - 3 * DAY)}`}), el("div", {class: "s", text: `${p[6].n} workout${p[6].n === 1 ? "" : "s"} with readings`}),
          ...[5, 4, 3, 2, 1].map(k => p[k] >= 0.5 ? tipRow(Z_C[k - 1], `Zone ${k}: ${Math.round(p[k])} min`) : null),
          p[6].z[0] >= 30 ? el("div", {class: "s", text: `Below zone 1: ${zMin(p[6].z[0])} min`}) : null]}; }});
  }
  // the period's workouts with heart rate, the latest first; each opens its details (the drawer)
  const recent = inP.slice(-5).reverse();
  if (!recent.length) { card.append(el("p", {class: "hint", text: "No workouts with heart rate in this period."})); return; }
  card.append(el("h3", {class: "sub", style: {marginTop: "10px"}, text: inP.length > 5 ? `Latest 5 of ${inP.length} workouts with heart rate in this period` : "Workouts with heart rate in this period"}),
    el("ul", {class: "vital-list zones-list"}, ...recent.map(x => {
      const ev = EV.find(e => e.k === "exercise" && e.ts === x.t), z = x.hr.zones, most = z ? Math.max(...z.slice(1)) : 0;
      const top = most > 0 ? [1, 2, 3, 4, 5].filter(k => z[k] === most) : [];   // the zone (or zones, tied) with the most time
      const what = [x.hr.avg != null ? `average ${Math.round(x.hr.avg)} bpm` : "", x.hr.max != null ? `highest ${Math.round(x.hr.max)}` : "",
        top.length ? `most time in zone${top.length > 1 ? "s" : ""} ${listOf(top.map(String))}` : z ? "" : "average and highest only"].filter(Boolean).join(", ");
      return el("li", {}, ev ? el("button", {class: "zone-open", type: "button", text: `${x.name || "Workout"}, ${x.min} min`, onclick: () => openEvent(ev)}) : el("b", {text: `${x.name || "Workout"}, ${x.min} min`}),
        ` · ${what}`, el("span", {class: "muted", text: ` · ${fmtDT(x.t)}`}));
    })));
}


/* ---------------------------------------------------------------- BMI over time (#195; growth.js) */
/* Each weight's BMI with the height in effect on its day (About this person's history): a line under the weight's,
   the latest in a tile, and a column in the card's table. The number only; the WHO's categories for adults when the
   person turns them on (hState.bmiWho, remembered per profile), always named as the WHO's, never for a weight taken
   under 20. */
const bmiNum = r => bmiRound(r.v).toFixed(1);
const bmiWords = r => { const p = r.child ? bmiChildPct(r) : null; return bmiNum(r) + (p == null ? "" : ` (${pctText(p)} percentile for age)`); };   // #196
const whoWords = v => { const c = whoBmi(v); return c ? `${c.range} (“${c.name}”)` : ""; };
const bmiHeight = h => heightText(h.cm, heightUnit((CFG.units || {}).height, h.unit));
function bmiTile(tile, a, b) {
  if (!H.bmi.length) return;
  if (H.bmi.some(r => r.child)) growthLoad();   // a child's percentile for age (#196)
  const inP = H.bmi.filter(r => r.t >= a && r.t <= b), last = inP.at(-1) || H.bmi.at(-1);
  const who = hState.bmiWho && !last.child && whoBmi(last.v), pct = last.child ? bmiChildPct(last) : null;
  tile("BMI", bmiNum(last), "kg/m²", who ? `WHO: ${who.range}` : pct != null ? `${pctText(pct)} percentile for age` : `${inP.length ? "latest" : "last"} ${fmtD(last.t)}`, "#h-body");
}
function bmiChart(card, a, b, {long}) {
  if (state.tables.has("#h-body")) return;   // the table has a BMI column instead
  const all = H.bmi, box = el("div", {class: "bmi"}, el("h3", {class: "sub", text: "BMI (kg/m²)"}));
  if (!all.length) {   // weights, but none with a height known by its day
    if (!H.scale.length && !H.clinic.length) return;
    const none = !personOf().heights.length;
    box.append(el("p", {class: "hint"}, none ? "With a height under " : "No weight here has a height from its day or before in ",
      canEdit() ? el("a", {href: `sharing.html?profile=${PROFILE.id}#about`, text: "About this person"}) : "About this person",
      none ? ", each weight's BMI is shown here." : `, so none has a BMI${personOf().birthDate ? " (under 20, the height must be from the 3 months before the weight)" : ""}.`));
    card.append(box);
    return;
  }
  const adult = all.some(r => !r.child), who = hState.bmiWho && adult;
  box.append(el("p", {class: "hint", text: "Body mass index: the weight in kilograms divided by the square of the height in metres, each weight with the height in effect on its day (About this person)."
    + (all.some(r => r.child) ? " Under 20, it's shown without the adult categories" + (SEX_CODE[personOf().sex] ? ", with its percentile for age and sex on the CDC's chart (Growth charts, below)." : ".") : "")}));
  if (adult) box.append(el("div", {class: "toolbar"}, el("label", {},
    el("input", {type: "checkbox", id: "bmiWho", checked: hState.bmiWho || null, onchange: e => { hState.bmiWho = e.target.checked; hSave(); renderHealth(); }}),
    "Show the WHO's categories for adults")));
  if (who) box.append(el("p", {class: "hint", text: "The World Health Organization's categories of BMI for adults (20 and over): under 18.5, 18.5–24.9, 25–29.9, and 30 and over. "
    + "Shown because they were turned on; they're the WHO's, not advice."}));
  card.append(box);
  const C = {bmi: "var(--c-lab)", clinic: "var(--muted)"};
  const pts = all.map(r => [r.t, r.v, r]), clinic = pts.filter(p => p[2].w[2]), scale = pts.filter(p => !p[2].w[2]);
  hChart(box, a, b, {h: 150, fmtY: v => String(+v.toFixed(1)), label: "BMI", noun: "weight", nav: pts, layers: [
    ...(who ? [{type: "ybands", data: [], bands: WHO_BMI.map(([lo, hi, range]) => [lo, hi, "WHO " + range])}] : []),
    {type: "line", data: rolling(pts, 14), color: C.bmi, width: 2, gap: 45},
    {type: "dots", data: clinic, color: C.clinic, hollow: true, r: 3},
    {type: "dots", data: long && scale.length > 400 ? [] : scale, color: C.bmi, opacity: .55}],
    tip: t => { const p = nearest(pts, t, b - a > 200 * DAY ? 10 : 4); if (!p) return null; const r = p[2];
      return {t: p[0], rows: [el("div", {class: "v", text: `BMI ${bmiWords(r)}`}),
        el("div", {class: "s", text: `${fmtD(r.t)} · ${r.w[1].toFixed(1)} lb${r.w[2] ? ` at a clinic visit (${r.w[2]})` : " on the smart scale"}`}),
        el("div", {class: "s", text: `Height ${bmiHeight(r.h)}, from ${fmtD(dayTs(r.h.date))}`}),
        who && !r.child ? el("div", {class: "s", text: `The WHO's category for adults: ${whoWords(r.v)}`}) : null,
        r.child ? el("div", {class: "s", text: `Aged ${r.age}: ${bmiChildPct(r) != null ? "the percentile is the CDC's, for age and sex" : "under 20, no adult categories"}`}) : null]}; }});
}

/* ---------------------------------------------------------------- children's growth (#196; growth.js) */
/* For a profile with a birth date and a sex used for reference ranges, the measurements taken before 20 over the
   published growth charts' percentile curves for age and sex, each measurement's percentile in its readout, the table
   and the latest list: weight (the smart scale's and the clinic visits'), height (About this person's history, and the
   heights in the records), BMI from 2 years (#195's, each weight with the height of its day) and head circumference
   under 2 when the records have it. The WHO's charts to 2 years, the CDC's from 2 to 20, picked as a pair; ages before
   2 corrected for a baby born early (About this person's due date). Where measurements fall, sourced and labelled;
   never what that means. Weights in kg and lengths in cm, or lb and inches as chosen under Settings → Units. */
const grState = {set: null};   // the chart set picked on this page: "who" or "cdc"
let GT;   // the growth tables once fetched (null if they couldn't be)
const LEN_CM = {cm: 1, in: 2.54, "[in_i]": 2.54, inch: 2.54, inches: 2.54, m: 100};
const isHeightSeries = s => !s.watch && (/^(body )?height\b|^height$|^stature$/i.test(s.name || "") || ["8302-2", "8306-3", "3137-7", "8308-9"].includes(vitalCode(s)));
const isHeadSeries = s => !s.watch && (/head circ|occipital|\bofc\b/i.test(s.name || "") || ["9843-4", "8287-5"].includes(vitalCode(s)));
const G_NAME = {weight: "Weight", height: "Height", head: "Head circumference", bmi: "BMI"};
const gName = (key, set) => key === "height" && set === "who" ? "Length" : G_NAME[key];
const gUnit = key => { const u = CFG.units || {};
  return key === "bmi" ? ["kg/m²", 1] : key === "weight" ? (u.weight === "lb" ? ["lb", 1 / LB_KG] : ["kg", 1]) : u.height === "ftin" ? ["in", 1 / 2.54] : ["cm", 1]; };
const gVal = m => { const [u, f] = gUnit(m.key); return `${(m.x * f).toFixed(1)} ${u}`; };
const G_SET = {who: ["Birth to 2 years (WHO)", "the WHO Child Growth Standards"], cdc: ["2 to 20 years (CDC)", "the CDC growth charts"]};
function gAgeText(age) {
  const t = d => { const m = Math.floor(d / MONTH_DAYS + 1e-9);
    return d < 31 ? `${d} day${d === 1 ? "" : "s"}` : m < 24 ? `${m} month${m === 1 ? "" : "s"}` : `${Math.floor(m / 12)} years${m % 12 ? ` ${m % 12} month${m % 12 === 1 ? "" : "s"}` : ""}`; };
  return age.corrected ? `${t(age.chrono)} (corrected: ${t(age.days)})` : t(age.chrono);
}
/* the measurements taken before 20, each {t, day, key, x (kg, cm or kg/m²), src, age, set, p}; null without a birth
   date and sex, or with none */
function growthData() {
  const P = personOf();
  if (!P.birthDate || !SEX_CODE[P.sex]) return null;
  H = H || hData();
  const out = [], seen = new Set(), hist = new Set(P.heights.map(h => h.date));
  const add = (t, key, x, src) => {
    const day = isoDay(t), age = growthAge(day);
    if (!age || !(x > 0) || seen.has(key + day + x.toFixed(1))) return;
    const set = growthSet(age.days);
    if ((key === "head" && set !== "who") || (key === "bmi" && set !== "cdc")) return;   // the charts there are
    seen.add(key + day + x.toFixed(1));
    out.push({t, day, key, x, src, age, set, p: GT ? growthPct(GT, key, P.sex, age.days, x) : null});
  };
  for (const p of [...H.scale, ...H.clinic]) add(p[0], "weight", p[1] * LB_KG, p[2] ? `clinic visit (${p[2]})` : "smart scale");
  for (const h of P.heights) add(dayTs(h.date), "height", h.cm, "About this person");
  for (const [test, key] of [[isHeightSeries, "height"], [isHeadSeries, "head"]]) for (const s of SERIES.filter(test)) {
    const f = LEN_CM[String(s.unit || "").trim().toLowerCase()];
    if (f) for (const p of s.pts) if (typeof p[1] === "number" && isFinite(p.ts) && !(key === "height" && hist.has(isoDay(p.ts)))) add(p.ts, key, p[1] * f, `the records (${vitalSource(s, p)})`);
  }
  for (const r of H.bmi) if (r.child) add(r.t, "bmi", r.v, `the weight, with the height of ${fmtD(dayTs(r.h.date))}`);
  out.sort((p, q) => p.t - q.t || p.key.localeCompare(q.key));
  return out.length ? {sex: P.sex, meas: out} : null;
}
/* a child's BMI on the BMI chart (#195): its percentile for age and sex, once the tables are here */
function bmiChildPct(r) {
  const P = personOf(), age = GT && SEX_CODE[P.sex] ? growthAge(isoDay(r.t)) : null;
  return age && growthSet(age.days) === "cdc" ? growthPct(GT, "bmi", P.sex, age.days, r.v) : null;
}
function growthLoad() {   // fetch the tables once, and draw Body and activity again when they're here
  if (GT !== undefined || GROWTH_T) return;
  growthTables().then(t => { GT = t || null; if (state.tab === "health") renderHealth(); });
}
function growthCard(box, head) {
  const P = personOf();
  if (!P.birthDate || !SEX_CODE[P.sex]) return;
  if (GT === undefined) { if (growthData()) growthLoad(); return; }   // drawn again once the tables are here
  const D = growthData();
  if (!D) return;
  const card = el("div", {class: "card", id: "h-growth", tabindex: -1}, head("Growth charts", "#h-growth"),
    el("p", {class: "hint", text: "Where each measurement falls on the published growth charts for age and sex: the WHO's from birth to 2 years, the CDC's from 2 to 20, as the CDC recommends. "
      + "They show where measurements fall, not what that means."}));
  box.append(card);
  if (!GT) { card.append(el("p", {class: "hint", text: "The growth charts' tables couldn't be loaded. Reload the page to try again."})); return; }
  const corrected = D.meas.some(m => m.age.corrected);
  const note = el("p", {class: "hint growth-src", text: "Charts: the WHO Child Growth Standards (2006), birth to 2 years; the CDC growth charts (2000, with BMI for age from the 2022 extended charts), 2 to 20 years. "
    + "Curves as each publishes them: the WHO's 3rd, 15th, 50th, 85th and 97th percentiles, the CDC's 5th to 95th."
    + (corrected ? " Before 2 years, ages are corrected for being born early (the due date in About this person)." : "")});
  if (state.tables.has("#h-growth")) {   // every measurement, newest first, with both charts' sets
    card.append(chartTable(["Date", "Age", "Measurement", "Value", "Percentile", "Chart", "From"],
      [...D.meas].reverse().map(m => [fmtD(m.t), gAgeText(m.age), gName(m.key, m.set), gVal(m), m.p == null ? "" : pctText(m.p), m.set.toUpperCase(), m.src])), note);
    return;
  }
  const sets = ["who", "cdc"].filter(s => D.meas.some(m => m.set === s));
  if (!sets.includes(grState.set)) grState.set = D.meas.at(-1).set;
  const set = grState.set;
  if (sets.length > 1) card.append(el("div", {class: "seg wrap growth-set", role: "group", "aria-label": "Growth charts for"},
    ...sets.map(s => el("button", {type: "button", "aria-pressed": s === set, text: G_SET[s][0], onclick: () => {
      if (grState.set === s) return; grState.set = s; hideTip(); renderHealth(); $("#h-growth .growth-set [aria-pressed=true]")?.focus(); }}))));
  else card.append(el("p", {class: "growth-one", text: `${G_SET[set][0].replace(/ \(.*/, "")}: ${G_SET[set][1]}`}));
  const keys = set === "who" ? ["weight", "height", "head"] : ["weight", "height", "bmi"];
  const grid = el("div", {class: "growth-grid"});
  card.append(grid);
  for (const key of keys) {
    const pts = D.meas.filter(m => m.set === set && m.key === key);
    if (!pts.length) continue;
    const [u] = gUnit(key), cell = el("div", {class: "growth-cell"}, el("h3", {class: "sub", text: `${gName(key, set)} for age (${u})`}));
    grid.append(cell);
    growthChart(cell, D.sex, set, key, pts);
  }
  const latest = keys.map(k => D.meas.filter(m => m.set === set && m.key === k).at(-1)).filter(Boolean);
  card.append(el("h3", {class: "sub", text: "Latest on these charts"}), el("ul", {class: "vital-list"}, ...latest.map(m => el("li", {},
    el("b", {text: `${gName(m.key, m.set)} ${gVal(m)}`}), m.p == null ? "" : ` · ${pctText(m.p)} percentile`,
    el("span", {class: "muted", text: ` · ${fmtD(m.t)} · aged ${gAgeText(m.age)} · from ${m.src}`})))), note);
}
/* one chart: x the age in days, the publisher's percentile curves, and the measurements */
function growthChart(host, sex, set, key, pts) {
  const [unit, f] = gUnit(key), Y = 365.25;
  const curves = set === "who" ? GT.who.curves : key === "bmi" ? GT.cdc.bmiCurves : GT.cdc.curves;
  const from = set === "who" ? 0 : 24 * MONTH_DAYS, last = Math.max(...pts.map(m => m.age.days));
  const to = set === "who" ? WHO_TO : Math.min(CDC_TO, Math.max(6 * Y, (Math.ceil(last / Y) + 2) * Y));
  const lines = curves.map(c => [c, growthCurve(GT, key, sex, c, from, to).map(([d, x]) => [d, x * f])]);
  const name = `${gName(key, set)} for age`;
  const svg = el("svg:svg", {class: "growth-chart"});
  host.append(svg);
  const at = d => pts.find(m => m.age.days === d);
  const rows = m => [el("div", {class: "v", text: `${gVal(m)} · ${m.p == null ? "no percentile" : pctText(m.p) + " percentile"}`}),
    el("div", {class: "s", text: `${fmtD(m.t)} · aged ${gAgeText(m.age)}`}), el("div", {class: "s", text: `From ${m.src} · ${set === "who" ? "WHO" : "CDC"} chart`})];
  const draw = () => {
    if (!svg.isConnected) return;
    const w = Math.max(260, host.clientWidth), h = 230, L = 40, R = w - 40, T = 10, B = h - 36;
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`); svg.setAttribute("height", h); svg.style.width = "100%"; svg._geom = {L, R, w};
    let lo = Infinity, hi = -Infinity;
    for (const [, ln] of lines) for (const p of ln) { lo = Math.min(lo, p[1]); hi = Math.max(hi, p[1]); }
    for (const m of pts) { lo = Math.min(lo, m.x * f); hi = Math.max(hi, m.x * f); }
    const pad = (hi - lo) * 0.05 || 1; lo -= pad; hi += pad;
    const x = d => L + (d - from) / (to - from) * (R - L), y = v => B - (v - lo) / (hi - lo) * (B - T);
    const g = [];
    for (const v of niceTicks(lo, hi, 5)) { g.push(el("svg:line", {class: "gridline", x1: L, x2: R, y1: y(v), y2: y(v)})); g.push(el("svg:text", {x: L - 6, y: y(v) + 4, "text-anchor": "end", text: String(+v.toFixed(1))})); }
    g.push(el("svg:line", {x1: L, x2: R, y1: B, y2: B, stroke: "var(--axis)"}));
    const yrs = (to - from) / Y, step = set === "who" ? (w < 420 ? 6 : 3) * MONTH_DAYS : (yrs > 9 || w < 420 ? 2 : 1) * Y;
    for (let d = from; d <= to + 1; d += step) g.push(el("svg:text", {x: x(d), y: B + 15, "text-anchor": "middle", text: String(Math.round(set === "who" ? d / MONTH_DAYS : d / Y))}));
    g.push(el("svg:text", {x: (L + R) / 2, y: h - 3, "text-anchor": "middle", text: set === "who" ? "Age (months)" : "Age (years)"}));
    const labels = [];   // each curve named at its end, moved apart where the curves end close together
    for (const [c, ln] of lines) {
      if (ln.length < 2) continue;
      g.push(el("svg:path", {class: "growth-curve" + (c === 50 ? " mid" : ""), d: "M" + ln.map(p => `${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join("L")}));
      labels.push([y(ln[ln.length - 1][1]) + 4, pctText(c)]);
    }
    labels.sort((p, q) => p[0] - q[0]);
    for (let i = 1; i < labels.length; i++) labels[i][0] = Math.max(labels[i][0], labels[i - 1][0] + 10);
    const over = labels.length ? labels[labels.length - 1][0] - (B + 4) : 0;
    if (over > 0) for (let i = labels.length - 1; i >= 0; i--) labels[i][0] = Math.min(labels[i][0], (i === labels.length - 1 ? B + 4 : labels[i + 1][0] - 10));
    for (const [ly, text] of labels) g.push(el("svg:text", {class: "growth-pct", x: R + 4, y: ly, text}));
    if (pts.length > 1) g.push(el("svg:path", {d: "M" + pts.map(m => `${x(m.age.days).toFixed(1)},${y(m.x * f).toFixed(1)}`).join("L"), fill: "none", stroke: "var(--c-lab)", "stroke-width": 1.4, opacity: .7}));
    for (const m of pts) g.push(el("svg:circle", {cx: x(m.age.days), cy: y(m.x * f), r: pts.length > 60 ? 2.4 : 3.6, fill: "var(--c-lab)", stroke: "var(--surface)", "stroke-width": 1}));
    const cross = el("svg:line", {class: "cross", y1: T, y2: B, visibility: "hidden"});
    const hit = el("svg:rect", {x: L, y: T, width: R - L, height: B - T, fill: "transparent"});
    const near = cx => { let best = null; for (const m of pts) { const dx = Math.abs(x(m.age.days) - cx); if (dx <= 14 && (!best || dx < best[0])) best = [dx, m]; } return best && best[1]; };
    const show = (m, ev) => { cross.setAttribute("x1", x(m.age.days)); cross.setAttribute("x2", x(m.age.days)); cross.setAttribute("visibility", "visible"); showTip(rows(m), ev); };
    hit.addEventListener("pointermove", ev => { const r = svg.getBoundingClientRect(), m = near((ev.clientX - r.left) * (w / r.width));
      if (m) show(m, ev); else { hideTip(); cross.setAttribute("visibility", "hidden"); } });
    hit.addEventListener("pointerleave", () => { hideTip(); cross.setAttribute("visibility", "hidden"); });
    g.push(cross, hit);
    svg.replaceChildren(...g);
    chartKeys(svg, `${name}, ${set === "who" ? "the WHO's chart, birth to 2 years" : "the CDC's chart, 2 to 20 years"}: ${pts.length} measurement${pts.length === 1 ? "" : "s"}, the latest ${gVal(pts.at(-1))}`
      + `${pts.at(-1).p == null ? "" : `, ${pctText(pts.at(-1).p)} percentile`}. Left and right arrow keys step through them.`,
      () => [...new Set(pts.map(m => m.age.days))], d => { const m = at(d); if (!m) return ""; const r = rows(m); show(m, svgPoint(svg, x(d), T)); return tipWords(r); },
      () => { hideTip(); cross.setAttribute("visibility", "hidden"); });
  };
  requestAnimationFrame(draw);
}
