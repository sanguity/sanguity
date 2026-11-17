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
  // each result's own range, stepped where they differ (#58, ranges.js), never one range across labs
  const steps = band ? rangeSteps(pts, -Infinity, Infinity) : [];
  const rls = steps.map(st => st.lo).filter(v => v != null), rhs = steps.map(st => st.hi).filter(v => v != null);
  let lo = Math.min(...vs, ...rls), hi = Math.max(...vs, ...rhs);
  if (lo === hi) { lo -= 1; hi += 1; }
  const x = t => 3 + (t - t0) / ((t1 - t0) || 1) * (w - 6), y = v => h - 4 - (v - lo) / (hi - lo) * (h - 8);
  const g = [];
  for (const st of steps) {
    const xa = Math.max(0, x(st.a)), xb = Math.min(w, x(st.b)), a = st.hi != null ? y(st.hi) : 0, b = st.lo != null ? y(st.lo) : h;
    g.push(el("svg:rect", {class: "spark-ref", x: xa, y: a, width: Math.max(0, xb - xa), height: Math.max(0, b - a), fill: "var(--good)", "fill-opacity": .1}));
  }
  g.push(el("svg:path", {d: pts.map((p, i) => (i ? "L" : "M") + x(p.ts).toFixed(1) + "," + y(p[1]).toFixed(1)).join(""), fill: "none", stroke: "var(--muted)", "stroke-width": 1.5, "vector-effect": "non-scaling-stroke"}));
  g.push(last[4] ? abnMark(x(last.ts), y(last[1]), last[4], 3.4, "spark-abn")   // ▲ or ▼ when it's out of range, not only red (#60)
    : el("svg:circle", {cx: x(last.ts), cy: y(last[1]), r: 3, fill: "var(--accent)"}));
  svg.replaceChildren(...g);
  return svg;
}
/* a tile or row to its lab's chart on Labs, over the last 6 months (the records bar's "6m") */
function goSeries(name) { state.sel.add(name); state.labsView = "chart"; setPeriod("6m"); pickTab("labs"); }
// beside every out-of-range colouring (Labs has the same line in index.html)
const RANGE_NOTE = "Out-of-range values are flagged against the reference ranges in your own lab reports. This isn't medical advice.";

/* ---- readings taken at home as tiles (#225): blood pressure, glucose and body temperature, chosen under Settings →
   Overview → Home readings, after the key labs. Each the latest reading, at home or at a visit, with its date and
   where it came from (health.js's hVitals and gData, the same readings as Body and activity); a click opens its card. */
function homeTiles() {
  const want = (CFG.overview || {}).homeTiles || [];
  if (!want.length) return [];
  const V = hVitals(), out = [];
  const latest = rows => rows.reduce((m, r) => !m || r.t > m.t ? r : m, null);
  const where = r => r.home ? `at home · ${r.src}` : `at a visit · ${r.src}`;
  const tile = (label, val, unit, sub, id) => el("button", {class: "tile home-tile", "data-home": id.slice(3), onclick: () => openVital(id)},
    el("span", {class: "lbl"}, el("span", {text: label})), el("span", {class: "val"}, val, el("small", {text: unit})),
    el("span", {class: "delta", text: sub}));
  for (const k of want) {
    if (k === "bp") {
      const r = latest([...V.bp.home, ...V.bp.clinic]);
      out.push(tile("Blood pressure", r ? bpText(r) : "–", "mmHg", r ? `${fmtDT(r.t)} · ${where(r)}` : "no readings yet", "#h-bp"));
    } else if (k === "temp") {
      const r = latest([...V.temp.home, ...V.temp.clinic]);
      out.push(tile("Body temperature", r ? r.v[0].toFixed(1) : "–", V.temp.unit, r ? `${fmtDT(r.t)} · ${where(r)}` : "no readings yet", "#h-temp"));
    } else if (k === "glucose") {
      if (G === undefined) G = gData();
      const m = G && G.meter.length ? G.meter.at(-1) : null, d = G && G.days.length ? G.days.at(-1) : null;
      if (d && (!m || d.t >= m[0] - DAY / 2)) out.push(tile("Blood glucose", G.fmt(d.avg), `${G.unit} avg`,
        `${fmtD(d.t)} · the day's CGM average (${G.fmt(d.lo)}–${G.fmt(d.hi)}) · phone app`, "#h-glucose"));
      else out.push(tile("Blood glucose", m ? G.fmt(m[1]) : "–", G ? G.unit : "", m ? `${fmtDT(m[0])} · a meter reading · from a phone app` : "no readings yet", "#h-glucose"));
    }
  }
  return out;
}

function renderOverview() {
  const box = $("#view-overview"); box.replaceChildren();
  const labDays = EV.filter(e => e.k === "lab").map(e => e.ts);
  const lastDraw = labDays.length ? Math.max(...labDays) : null;
  // no lab results yet (#199): what the tiles would show, and where results come from, in place of the tiles and the
  // out-of-range card (which would otherwise say everything is in range)
  const noLabs = !SERIES.some(s => !s.watch && s.pts.length);
  if (noLabs) box.append(emptyState("Key lab results appear here as tiles, each with its latest value and whether it's outside its reference range.",
    canEdit() ? {href: `sources.html?profile=${PROFILE.id}#manual`, text: "Add lab results"} : null));
  // key lab tiles
  if (!noLabs) box.append(el("div", {class: "ov-h"}, el("h2", {text: "Key labs"}), el("p", {class: "hint", text: lastDraw ? `latest draw ${fmtD(lastDraw)} · sparklines show the last 6 months · click a tile for its full trend` : ""})));
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
  const home = homeTiles();   // readings taken at home, when chosen in Settings (#225)
  if (!noLabs) { tiles.append(...home); box.append(tiles, el("p", {class: "range-note", text: RANGE_NOTE + (RANGES_USED ? RANGE_NOTE_SETTINGS : "")})); }
  else if (home.length) box.append(el("div", {class: "ov-h"}, el("h2", {text: "Home readings"})), el("div", {class: "tiles"}, ...home));
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
    oc.append(el("div", {class: "ov-row", ...pressable(() => goSeries(o.s.name))},
      el("span", {class: "n", text: nice(o.s.name)}), el("span", {class: "v"}, `${o.last[5] || fmtNum(o.last[1])} ${o.s.unit} `, el("span", {class: "st " + st.cls, text: st.text}), ucOrig(o.s, o.last) ? el("span", {class: "orig", text: " reported " + ucOrig(o.s, o.last)}) : null),
      el("span", {class: "r", text: refText(o.last)}),
      el("span", {class: "s", text: o.streak > 1 ? `${st.cls === "hi" ? "High" : "Low"} on the last ${o.streak} draws, since ${fmtD(o.since)}` : `First out-of-range result, ${fmtD(o.last.ts)}`})));
  }
  if (out.length > 14) oc.append(el("p", {class: "hint", style: {marginTop: "8px"}, text: `${out.length - 14} more in Labs.`}));
  if (!noLabs) left.append(oc);
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
  // problem list (allergies have a card of their own, under it: #179)
  const probs =(DATA.problems || []).filter(p => (p.status || "").toLowerCase() === "active" && p.type !== "Allergy");
  if (probs.length) {
    const pc = el("div", {class: "card"}, el("h2", {text: `Active problem list (${probs.length})`}), listOf(probs.map(p => p.src)) ? el("p", {class: "hint", text: "As recorded by " + listOf(probs.map(p => p.src))}) : null);
    for (const p of probs) pc.append(el("div", {class: "ov-row", style: {cursor: "default"}}, el("span", {class: "n", style: {fontWeight: 450}, text: p.name}), el("span", {class: "r", text: p.date ? fmtD(dayTs(p.date)) : ""}), el("span", {})));
    left.append(pc);
  }
  // ---- allergies and immunizations (#179): as the records say them, from C-CDA, FHIR, a Health Card or typed in
  const ended = s => /^(resolved|inactive|refuted|entered in error)$/i.test(s || "");
  const allergies = (DATA.problems || []).filter(p => p.type === "Allergy" && !ended(p.status));
  if (allergies.length) {
    const ac = el("div", {class: "card", id: "ovAllergies"}, el("h2", {text: `Allergies and intolerances (${allergies.length})`}),
      listOf(allergies.map(p => p.src)) ? el("p", {class: "hint", text: "As recorded by " + listOf(allergies.map(p => p.src))}) : null);
    for (const p of allergies) ac.append(el("div", {class: "ov-row", style: {cursor: "default"}},
      el("span", {class: "n", style: {fontWeight: 450}, text: p.substance || p.name.replace(/^Allergy: /, "")}),
      el("span", {class: "r", text: p.date ? fmtD(dayTs(p.date)) : ""}), el("span", {}),
      el("span", {class: "s", text: [(p.reactions || []).length ? "Reaction: " + p.reactions.join(", ") : "", p.severity ? "Severity: " + p.severity : "",
        p.criticality ? "Criticality: " + p.criticality : "", p.status && p.status !== "Active" ? p.status : ""].filter(Boolean).join(" · ")})));
    left.append(ac);
  }
  const imms = EV.filter(e => e.k === "immunization" && !e.hidden).sort((a, b) => b.ts - a.ts);
  if (imms.length) {
    const ic = el("div", {class: "card", id: "ovImmunizations"}, el("h2", {text: `Immunizations (${imms.length})`}),
      el("p", {class: "hint", text: "Vaccines the records say were given, newest first"}));
    for (const e of imms.slice(0, 8)) {
      const d = DATA.details[e.id] || {};
      ic.append(el("div", {class: "ov-row", ...pressable(() => openEvent(e))},
        el("span", {class: "n"}, el("span", {class: "key", style: {display: "inline-block", width: "12px", height: "3px", borderRadius: "2px", background: CAT_OF[e.k].color, marginRight: "8px", verticalAlign: "middle"}}), e.title),
        el("span", {class: "r", text: fmtD(e.ts)}), el("span", {}),
        el("span", {class: "s", text: [d.dose ? "Dose " + d.dose : "", d.where || d.performer].filter(Boolean).join(" · ")})));
    }
    if (imms.length > 8) ic.append(el("button", {class: "btn", style: {marginTop: "8px"}, text: `All ${imms.length} on the Timeline →`, onclick: () => {
      setPeriod("all"); state.cats = new Set(["immunization"]); pickTab("timeline"); }}));
    left.append(ic);
  }
  // recent events
  const rc = el("div", {class: "card"}, el("h2", {text: "Recent"}));
  const recent = EV.filter(e => !e.hidden && e.k !== "auto_msg" && e.k !== "cycle").sort((a, b) => b.ts - a.ts).slice(0, 10);   // cycle records: their lane only (#183)
  for (const e of recent) rc.append(el("div", {class: "ov-row", ...pressable(() => openEvent(e))},
    el("span", {class: "n"}, el("span", {class: "key", style: {display: "inline-block", width: "12px", height: "3px", borderRadius: "2px", background: CAT_OF[e.k].color, marginRight: "8px", verticalAlign: "middle"}}), e.title),
    el("span", {class: "r", text: fmtD(e.ts)}), el("span", {}),
    e.sub ? el("span", {class: "s", text: e.sub}) : null));
  right.append(rc);
}

