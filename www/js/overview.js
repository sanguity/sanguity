/* Sanguity · Overview tab.
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
/* ---------------------------------------------------------------- overview */
// hint text built from the data, so no provider or insurer names live in the page
const listOf = (xs, sep = " and ") => { const u = [...new Set(xs.filter(Boolean))]; return u.length < 2 ? (u[0] || "") : u.slice(0, -1).join(", ") + sep + u.at(-1); };
const monthYear = d => zdate(dayTs(d.slice(0, 10))).toLocaleDateString(undefined, {month: "short", year: "numeric"});
function insurerSpans(rows) {   // [[insurer, first date, last date]] in order of first claim
  const by = new Map();
  for (const r of rows) { if (!r.ins) continue; const v = by.get(r.ins); if (!v) by.set(r.ins, [r.d, r.d]); else { if (r.d < v[0]) v[0] = r.d; if (r.d > v[1]) v[1] = r.d; } }
  return [...by.entries()].map(([n, [a, b]]) => [n, a, b]).sort((p, q) => p[1].localeCompare(q[1]));
}
function insurerHint(rows) {
  const s = insurerSpans(rows); if (!s.length) return "";
  if (s.length === 1) return `From ${s[0][0]} claims (${monthYear(s[0][1])} – ${monthYear(s[0][2])}).`;
  return "From " + listOf(s.map(([n, a, b], i) => `${n} (${monthYear(a)}${i === s.length - 1 ? " on" : " – " + monthYear(b)})`)) + " claims.";
}
const seriesBy = n => SERIES.find(s => s.name === n);
function statusOf(p) {
  if (!p) return null;
  const st = p[4] === "H" ? {cls: "hi", text: "▲ High"} : p[4] === "L" ? {cls: "lo", text: "▼ Low"} : (p[2] != null || p[3] != null) ? {cls: "ok", text: "✓ In range"} : null;
  if (st && p.rs === "settings") st.title = "Against a " + RANGE_FROM_SETTINGS;   // #44
  return st;
}
function spark(s, pts, {w = 200, h = 34, band = true} = {}) {
  const svg = el("svg:svg", {viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: "none", "aria-hidden": "true"});
  if (pts.length < 2) return svg;
  const t0 = pts[0].ts, t1 = pts[pts.length - 1].ts;
  const vs = pts.map(p => p[1]), last = pts[pts.length - 1];
  const rl = band ? median(pts.map(p => p[2]).filter(v => v != null)) : null, rh = band ? median(pts.map(p => p[3]).filter(v => v != null)) : null;
  let lo = Math.min(...vs, ...(rl != null ? [rl] : [])), hi = Math.max(...vs, ...(rh != null ? [rh] : []));
  if (lo === hi) { lo -= 1; hi += 1; }
  const x = t => 3 + (t - t0) / ((t1 - t0) || 1) * (w - 6), y = v => h - 4 - (v - lo) / (hi - lo) * (h - 8);
  const g = [];
  if (rl != null || rh != null) { const a = rh != null ? y(rh) : 0, b = rl != null ? y(rl) : h; g.push(el("svg:rect", {x: 0, y: a, width: w, height: Math.max(0, b - a), fill: "var(--good)", "fill-opacity": .1})); }
  g.push(el("svg:path", {d: pts.map((p, i) => (i ? "L" : "M") + x(p.ts).toFixed(1) + "," + y(p[1]).toFixed(1)).join(""), fill: "none", stroke: "var(--muted)", "stroke-width": 1.5, "vector-effect": "non-scaling-stroke"}));
  g.push(el("svg:circle", {cx: x(last.ts), cy: y(last[1]), r: 3, fill: last[4] ? "var(--crit)" : "var(--accent)"}));
  svg.replaceChildren(...g);
  return svg;
}
/* a tile or row to its lab's chart on Labs, over the last 6 months (the records bar's "6m") */
function goSeries(name) { state.sel.add(name); state.labsView = "chart"; setPeriod("6m"); pickTab("labs"); }
// beside every out-of-range colouring (Labs has the same line in index.html)
const RANGE_NOTE = "Out-of-range values are flagged against the reference ranges in your own lab reports. This isn't medical advice.";

function renderOverview() {
  const box = $("#view-overview"); box.replaceChildren();
  const labDays = EV.filter(e => e.k === "lab").map(e => e.ts);
  const lastDraw = labDays.length ? Math.max(...labDays) : null;
  // key lab tiles
  box.append(el("div", {class: "ov-h"}, el("h2", {text: "Key labs"}), el("p", {class: "hint", text: lastDraw ? `latest draw ${fmtD(lastDraw)} · sparklines show the last 6 months · click a tile for its full trend` : ""})));
  const tiles = el("div", {class: "tiles"});
  const keyLabs = keyLabTiles(CFG.overview.keyLabs, SERIES, JSON.stringify(CFG.overview.keyLabs) === JSON.stringify(DEFAULT_SETTINGS.overview.keyLabs));
  for (const n of keyLabs) {
    const s = seriesBy(n); if (!s || !s.pts.length) continue;
    const last = s.pts[s.pts.length - 1], prev = s.pts[s.pts.length - 2];
    const st = statusOf(last);
    const d = prev ? last[1] - prev[1] : null;
    const recent = s.pts.filter(p => p.ts >= last.ts - 182 * DAY);
    tiles.append(el("button", {class: "tile", onclick: () => goSeries(n)},
      el("span", {class: "lbl"}, el("span", {text: nice(n), title: n}), st ? el("span", {class: "st " + st.cls, text: st.text, title: st.title}) : null),
      el("span", {class: "val"}, last[5] || fmtNum(last[1]), el("small", {text: s.unit})),
      ucOrig(s, last) ? el("span", {class: "orig", text: "Reported " + ucOrig(s, last)}) : null,   // #41
      el("span", {class: "delta", text: (d == null ? "" : `${d > 0 ? "▲ +" : d < 0 ? "▼ " : "± "}${fmtNum(Math.abs(d) < 1e-9 ? 0 : d).replace("-", "−")} since ${fmtD(prev.ts)}`) + (last.ts < (lastDraw || 0) - 2 * DAY ? ` · as of ${fmtD(last.ts)}` : "")}),
      spark(s, recent)));
  }
  box.append(tiles, el("p", {class: "range-note", text: RANGE_NOTE + (RANGES_USED ? RANGE_NOTE_SETTINGS : "")}));
  const cols = el("div", {class: "ov-cols"}), left = el("div", {}), right = el("div", {});
  cols.append(left, right); box.append(cols);
  // out of range at latest draw, with how long
  const out = [];
  for (const s of SERIES) {
    if (s.watch || !s.pts.length) continue;
    const last = s.pts[s.pts.length - 1];
    if (!last[4] || !lastDraw || last.ts < lastDraw - 14 * DAY) continue;
    let i = s.pts.length - 1; while (i > 0 && s.pts[i - 1][4] === last[4]) i--;
    out.push({s, last, since: s.pts[i].ts, streak: s.pts.length - i});
  }
  out.sort((a, b) => (keyLabs.includes(b.s.name) - keyLabs.includes(a.s.name)) || b.s.n - a.s.n);
  const oc = el("div", {class: "card"}, el("h2", {text: `Outside reference range at latest draw (${out.length})`}),
    el("p", {class: "hint", text: "Values from the last two weeks of draws that were flagged high or low, and how long they have been that way. Not a diagnosis; your care team interprets these in context."}));
  if (!out.length) oc.append(el("div", {class: "empty", style: {padding: "16px"}, text: "Everything measured recently is within its reference range."}));
  for (const o of out.slice(0, 14)) {
    const st = statusOf(o.last);
    oc.append(el("div", {class: "ov-row", onclick: () => goSeries(o.s.name)},
      el("span", {class: "n", text: nice(o.s.name)}), el("span", {class: "v"}, `${o.last[5] || fmtNum(o.last[1])} ${o.s.unit} `, el("span", {class: "st " + st.cls, text: st.text}), ucOrig(o.s, o.last) ? el("span", {class: "orig", text: " reported " + ucOrig(o.s, o.last)}) : null),
      el("span", {class: "r", text: refText(o.last)}),
      el("span", {class: "s", text: o.streak > 1 ? `${st.cls === "hi" ? "High" : "Low"} on the last ${o.streak} draws, since ${fmtD(o.since)}` : `First out-of-range result, ${fmtD(o.last.ts)}`})));
  }
  if (out.length > 14) oc.append(el("p", {class: "hint", style: {marginTop: "8px"}, text: `${out.length - 14} more in Labs.`}));
  left.append(oc);
  // watch summary
  const wk = ["hr_rest", "sleep", "steps", "hr", "weight"];
  const ws = wk.map(k => SERIES.find(s => s.key === k)).filter(Boolean);
  if (ws.length) {
    const wc = el("div", {class: "card"}, el("h2", {text: "Watch & scale"}), el("p", {class: "hint", text: "The watch's last 7 days against the 4 weeks before, and the latest weight against your weight at diagnosis."}),
      moreAbout("Sparklines show the last 30 days for the watch, and the last year for weight."));
    const grid = el("div", {class: "tiles", style: {gridTemplateColumns: "repeat(auto-fill,minmax(160px,1fr))", marginBottom: 0}});
    const now = Math.max(...ws.filter(s => s.key !== "weight").flatMap(s => s.pts.map(p => p.ts)));
    const avg = (s, a, b) => { const v = s.pts.filter(p => p.ts > a && p.ts <= b).map(p => p[1]); return v.length ? v.reduce((x, y) => x + y, 0) / v.length : null; };
    for (const s of ws) {
      if (s.key === "weight") {
        const last = s.pts[s.pts.length - 1], pm = primaryMs(), dx = pm ? dayTs(pm.date) : NaN;
        const at = pm ? s.pts.filter(p => Math.abs(p.ts - dx) < 75 * DAY).sort((a, b) => Math.abs(a.ts - dx) - Math.abs(b.ts - dx))[0] : null;
        const d = at && last !== at ? last[1] - at[1] : null;
        grid.append(el("button", {class: "tile", onclick: () => { setPeriod(msKey(pm)); pickTab("health"); }},
          el("span", {class: "lbl", text: "Weight"}),
          el("span", {class: "val"}, last ? last[1].toFixed(1) : "–", el("small", {text: s.unit})),
          ucOrig(s, last) ? el("span", {class: "orig", text: "Reported " + ucOrig(s, last)}) : null,   // #41
          el("span", {class: "delta", text: d != null ? `${d >= 0 ? "▲ +" : "▼ −"}${Math.abs(d).toFixed(1)} ${s.unit} since ${lcLabel(pm.label)} · ${fmtD(last.ts)}` : last ? fmtD(last.ts) : ""}),
          spark(s, s.pts.filter(p => p.ts > last.ts - 365 * DAY), {band: false})));
        continue;
      }
      const cur = avg(s, now - 7 * DAY, now), base = avg(s, now - 35 * DAY, now - 7 * DAY);
      const label = {hr_rest: "Resting heart rate", sleep: "Sleep", steps: "Steps", hr: "Heart rate, average"}[s.key];
      const fmt = v => s.key === "steps" ? Math.round(v).toLocaleString() : s.key === "sleep" ? v.toFixed(1) : Math.round(v).toString();
      grid.append(el("button", {class: "tile", onclick: () => { setPeriod("30d"); pickTab("health"); }},
        el("span", {class: "lbl", text: label}),
        el("span", {class: "val"}, cur == null ? "–" : fmt(cur), el("small", {text: s.key === "sleep" ? "h / night" : s.key === "steps" ? "/ day" : "bpm"})),
        el("span", {class: "delta", text: cur != null && base != null ? `${cur >= base ? "▲ +" : "▼ −"}${fmt(Math.abs(cur - base))} vs prior 4 weeks` : "not enough history yet"}),
        spark(s, s.pts.filter(p => p.ts > now - 30 * DAY), {band: false})));
    }
    wc.append(grid);
    right.append(wc);
  }
  // upcoming appointments
  const up = (DATA.upcoming || []).filter(u => u.t >= isoDay(Date.now()));
  if (up.length) {
    const uc = el("div", {class: "card"}, el("h2", {text: "Upcoming"}), listOf(up.map(u => u.where)) ? el("p", {class: "hint", text: "Scheduled at " + listOf(up.map(u => u.where))}) : null);
    for (const u of up.slice(0, 6)) uc.append(el("div", {class: "ov-row", style: {cursor: "default"}},
      el("span", {class: "n", text: u.title}), el("span", {class: "r", text: fmtD(dayTs(u.t))}), el("span", {}),
      el("span", {class: "s", text: [u.who, u.where].filter(Boolean).join(" · ")})));
    if (up.length > 6) uc.append(el("p", {class: "hint", style: {marginTop: "6px"}, text: `${up.length - 6} more scheduled through ${fmtD(dayTs(up[up.length - 1].t))}`}));
    right.append(uc);
  }
  // insurance cost summary
  if ((DATA.costs || []).length) {
    const rows = DATA.costs, pm = primaryMs(), dx = pm ? rows.filter(r => r.d >= pm.date) : rows;
    const cc = el("div", {class: "card"}, el("h2", {text: pm ? `Insurance costs since ${lcLabel(pm.label)}` : "Insurance costs"}),
      dx.length ? el("p", {class: "hint", text: `Claims from ${fmtD(dayTs(dx.reduce((m, r) => r.d < m ? r.d : m, dx[0].d)))} on (${listOf(insurerSpans(dx).map(s => s[0]), ", then ")}).`}) : null);
    const mini = (l, v) => el("div", {}, el("div", {style: {fontSize: "20px", fontWeight: 650}, text: v}), el("div", {class: "hint", style: {margin: 0}, text: l}));
    cc.append(el("div", {style: {display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: "10px", margin: "6px 0 10px"}},
      mini("billed", usd(sumOf(dx, "billed"))), mini("paid by insurance", usd(sumOf(dx, "paid"))), mini("your share", usd(sumOf(dx, "owe")))),
      el("button", {class: "btn", text: "Open Costs →", onclick: () => { if (pm) setPeriod(msKey(pm)); pickTab("costs"); }}));   // the same period
    right.append(cc);
  }
  // problem list
  const probs =(DATA.problems || []).filter(p => (p.status || "").toLowerCase() === "active");
  if (probs.length) {
    const pc = el("div", {class: "card"}, el("h2", {text: `Active problem list (${probs.length})`}), listOf(probs.map(p => p.src)) ? el("p", {class: "hint", text: "As recorded by " + listOf(probs.map(p => p.src))}) : null);
    for (const p of probs) pc.append(el("div", {class: "ov-row", style: {cursor: "default"}}, el("span", {class: "n", style: {fontWeight: 450}, text: p.name}), el("span", {class: "r", text: p.date ? fmtD(dayTs(p.date)) : ""}), el("span", {})));
    left.append(pc);
  }
  // recent events
  const rc = el("div", {class: "card"}, el("h2", {text: "Recent"}));
  const recent = EV.filter(e => !e.hidden && e.k !== "auto_msg").sort((a, b) => b.ts - a.ts).slice(0, 10);
  for (const e of recent) rc.append(el("div", {class: "ov-row", onclick: () => openEvent(e)},
    el("span", {class: "n"}, el("span", {class: "key", style: {display: "inline-block", width: "12px", height: "3px", borderRadius: "2px", background: CAT_OF[e.k].color, marginRight: "8px", verticalAlign: "middle"}}), e.title),
    el("span", {class: "r", text: fmtD(e.ts)}), el("span", {}),
    e.sub ? el("span", {class: "s", text: e.sub}) : null));
  right.append(rc);
}

