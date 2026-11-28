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
/* a tile's sparkline (chartkit.js ckSpark, #299): a lab's latest in its colour, the watch's and scale's a measure (#301) */
const spark = (s, pts, {band = true} = {}) => ckSpark(pts, {band, color: s.watch ? "var(--measure)" : "var(--c-lab)"});
/* a tile or row to its lab's chart on Labs, over the last 6 months (the records bar's "6m") */
function goSeries(name) { state.sel.add(name); state.labsView = "chart"; setPeriod("6m"); pickTab("labs"); }
// beside every out-of-range colouring (Labs has the same line in index.html)
const RANGE_NOTE = "Out-of-range values are flagged against the reference ranges in your own lab reports. This isn't medical advice.";

/* ---- readings taken at home as tiles (#225): blood pressure, glucose and body temperature, chosen under Settings →
   Overview → Home readings, in Readings after the watch's (#274). Each the latest reading, at home or at a visit, with
   its date and where it came from (health.js's hVitals and gData, the same readings as Body and activity); a click
   opens its card. */
function homeTiles() {
  const want = (CFG.overview || {}).homeTiles || [];
  if (!want.length) return [];
  const V = hVitals(), out = [];
  const latest = rows => rows.reduce((m, r) => !m || r.t > m.t ? r : m, null);
  const where = r => r.home ? `at home · ${r.src}` : `at a visit · ${r.src}`;
  // a reading at home above or below a target of the person's own, in words (#234): " · systolic ▲ above your target"
  const mine = (r, names) => r && r.home && r.tf ? r.tf.map((f, i) => f ? `${names[i]} ${tgtWord(f)}`.trim() : "").filter(Boolean).map(t => " · " + t).join("") : "";
  const tile = (label, val, unit, sub, id) => el("button", {class: "tile home-tile", "data-home": id.slice(3), onclick: () => openVital(id)},
    el("span", {class: "lbl"}, el("span", {text: label})), el("span", {class: "val"}, val, el("small", {text: unit})),
    // a date kept on one line (#330), and each "·" with the word before it (#379, #385): else a line can start with
    // one, as "· at home · Typed in" under the reading's date and time (a time keeps its AM or PM, keepDates)
    el("span", {class: "delta"}, keepDates(sub.replaceAll(" · ", "\u00a0· "))));
  for (const k of want) {
    if (k === "bp") {
      const r = latest([...V.bp.home, ...V.bp.clinic]);
      out.push(tile("Blood pressure", r ? bpText(r) : "–", "mmHg", r ? `${fmtDT(r.t)} · ${where(r)}${mine(r, ["systolic", "diastolic"])}` : "no readings yet", "#h-bp"));
    } else if (k === "temp") {
      const r = latest([...V.temp.home, ...V.temp.clinic]);
      out.push(tile("Body temperature", r ? r.v[0].toFixed(1) : "–", V.temp.unit, r ? `${fmtDT(r.t)} · ${where(r)}${mine(r, [""])}` : "no readings yet", "#h-temp"));
    } else if (k === "glucose") {
      if (G === undefined) G = gData();
      const m = G && G.meter.length ? G.meter.at(-1) : null, d = G && G.days.length ? G.days.at(-1) : null;
      if (d && (!m || d.t >= m[0] - DAY / 2)) out.push(tile("Blood glucose", G.fmt(d.avg), `${G.unit} avg`,
        `${fmtD(d.t)} · the day's CGM average (${G.fmt(d.lo)}–${G.fmt(d.hi)}) · phone app${d.oin != null ? ` · ${gPct(d.oin)} in your own range` : ""}`, "#h-glucose"));
      else out.push(tile("Blood glucose", m ? G.fmt(m[1]) : "–", G ? G.unit : "", !m ? "no readings yet"
        : (m[2] ? `${fmtDT(m[0])} · a meter reading at home · ${[m[2], m[3]].filter(Boolean).join(", ")}`   // typed in, its meal note (#232)
        : `${fmtDT(m[0])} · a meter reading · from a phone app`) + (m[4] ? ` · ${tgtWord(m[4])}` : ""), "#h-glucose"));   // against your target (#234)
    }
  }
  return out;
}

/* ---- the Overview grouped by the questions it answers (#274, plan #243), in this order at every width:
   Today (today's doses, what's coming up, when the watch last synced), Key labs (in the person's own order, by panel,
   the status only where it's an exception), Readings (the watch's, the scale's and the home readings, in one row), then
   the Health record (problems, allergies, immunizations, family history), what's Recent (clinical records, the workouts in one line) and
   the insurance costs. Each group under its own h2, its cards under it as h3s (#331); one with nothing in it says what
   would be there (#440), as Key labs does. */
const ovHead = (title, ...more) => el("div", {class: "ov-h"}, el("h2", {text: title}), ...more);
/* "today", "tomorrow", "in 9 days", "in 3 weeks", "in 4 months", "in 72 years": a day (YYYY-MM-DD) from today, in
   this person's zone; years beyond 24 months (#1103: it was "in 867 months") */
function inDays(day) {
  const n = Math.round((dayTs(day) - dayTs(zToday())) / DAY), m = Math.round(n / 30.4), y = Math.round(n / 365.25);
  return n <= 0 ? "today" : n === 1 ? "tomorrow" : n < 14 ? `in ${n} days` : n < 61 ? `in ${Math.round(n / 7)} weeks`
    : m <= 24 ? `in ${m} months` : `in ${y} year${y === 1 ? "" : "s"}`;
}
const summaryHref = () => `sharing.html?profile=${PROFILE.id}#summary`;   // the visit summary (#168), the next appointment chosen
const upWhen = u => fmtD(dayTs(u.t.slice(0, 10))) + (u.t.length > 10 ? ", " + fmtTime(zParse(u.t)) : "");

/* a way to another tab, one look on the Overview (#440): a link drawn as a button, with its arrow. A click with Ctrl, ⌘,
   Shift or Alt is the browser's (a new tab or window), as the tabs' own are (app.js) */
const ovGo = (tab, text, go = () => pickTab(tab)) => el("a", {class: "btn", href: "#" + tab, onclick: ev => {
  if (ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey || ev.button) return;
  ev.preventDefault(); go(); }}, text + " ", icon("arrow-right"));
/* an Overview group with nothing in it (#440): what would be there, as Key labs says it; the one next step is Key labs'
   (#199), so none here */
const ovNone = what => emptyState(what, null);

function ovToday() {
  // today's medication doses (medlog.js, #235): how they look and work is the dose list's own. With the Medications
  // tab shown, the next few, one line each, so Upcoming stays on a phone's first screen (#440), and the way to the
  // tab under them, whoever is looking: to the rest, or to the tab
  const meds = shownTabs().includes("medications") && MEDS.length, doses = todayDoses({few: meds ? 3 : 0});
  const more = doses ? +doses.dataset.more || 0 : 0, go = meds ? ovGo("medications", more ? `${more} more on Medications` : "Open Medications") : null;
  if (doses && go) doses.querySelector(".dose-week").after(go);
  const card = doses || (go && el("div", {class: "card", id: "ovMeds"}, el("h3", {text: "Medications"}),
    el("p", {class: "hint", text: "None listed as current: the records give each an end."}), go));   // only past ones
  const up = ovUpcoming(), fu = fuCard(), sync = WATCH && WATCH.last_payload ? `watch last synced ${fmtDT(WATCH.last_payload)}` : "";
  const day = zdate(Date.now()).toLocaleDateString(undefined, {weekday: "long", month: "long", day: "numeric"});
  return [ovHead("Today", el("p", {class: "hint", text: [day, sync].filter(Boolean).join(" · ")})),
    card || up || fu ? el("div", {class: "ov-today"}, card, up, fu) : ovNone("Today's medication doses and upcoming appointments appear here, from the records or typed in.")];
}
/* the appointments from today on, the next first, each saying when ("in 9 days") and opening its details; and the
   visit summary to prepare for the next */
function ovUpcoming() {
  const today = zToday(), up = (DATA.upcoming || []).filter(u => u && typeof u.t === "string" && u.t.slice(0, 10) >= today)
    .sort((a, b) => a.t.localeCompare(b.t));
  if (!up.length) return null;
  const FEW = 4, at = listOf(up.map(u => u.where));
  const uc = el("div", {class: "card", id: "ovUpcoming"}, el("h3", {text: "Upcoming"}), at ? el("p", {class: "hint", text: "Scheduled at " + at}) : null);
  up.slice(0, FEW).forEach((u, i) => uc.append(el("div", {class: "ov-row", "data-up": i, ...pressable(ev => openFrom(ev.currentTarget, () => openUpcoming(u)))},
    el("span", {class: "n", text: u.title || "Appointment"}), el("span", {class: "r"}, ...keepDates(`${inDays(u.t.slice(0, 10))} · ${upWhen(u)}`)),
    [u.who, u.where].some(Boolean) ? el("span", {class: "s", text: [u.who, u.where].filter(Boolean).join(" · ")}) : null)));
  if (up.length > FEW) uc.append(el("p", {class: "hint mt-2", text: `${up.length - FEW} more scheduled through ${fmtD(dayTs(up.at(-1).t.slice(0, 10)))}`}));
  uc.append(el("a", {class: "btn mt-3", href: summaryHref()}, "Prepare a visit summary ", icon("arrow-right")));
  return uc;
}
/* an upcoming appointment's details, in the drawer (drawer.js) */
function openUpcoming(u) {
  state.selEv = null;
  const procs = (Array.isArray(u.procs) ? u.procs : []).map(p => typeof p === "string" ? p : p && (p.name || p.title)).filter(Boolean);
  const body = drawerHead("visit", u.title || "Appointment", `Upcoming · ${upWhen(u)} · ${inDays(u.t.slice(0, 10))}`), same = JSON.stringify(u);
  drawerStill = () => (DATA.upcoming || []).some(x => JSON.stringify(x) === same);   // its import undone: closed (app.js, #517)
  body.append(...present(drawerFacts([["With", u.who], ["Where", u.where], ["For", u.dx], ["Planned", procs.join(", ")]], "facts")),
    el("div", {class: "btns"}, el("a", {class: "btn", href: summaryHref()}, "Prepare a visit summary ", icon("arrow-right"))),
    el("p", {class: "hint", text: "An appointment the records list as scheduled. The visit summary gathers what changed since the last visit, for this one."}));
  showDrawer();
}

/* the result before a series' latest (#833): the latest at an earlier time, never another result at its time (two the
   person said aren't the same), so no change is worked out between them. Of those, the latest is the flagged one
   (same.js joinSeries) */
const ovPrev = s => s.pts.findLast(p => p.ts < s.pts.at(-1).ts);
/* a key lab's tile: its latest value, the status only when it's outside its range, the change since the result before
   (its date only when it isn't the one the header says), and the last 6 months */
function labTile(s, since, lastDraw) {
  const n = s.name, last = s.pts.at(-1), prev = ovPrev(s);
  const st0 = statusOf(last), st = st0 && st0.cls !== "ok" ? st0 : null;
  // the change, never from a value given as "<5" or ">90" (#718: "<5" then 6 was "↑ 1"): said not worked out, as the
  // lab panel says it (labSure)
  const d = prev ? last[1] - prev[1] : null, sure = !prev || labSure(prev) && labSure(last);
  const recent = s.pts.filter(p => p.ts >= last.ts - 182 * DAY);
  // a nickname's name in the records and "Against a range from settings": the name's and the status's tips, said in
  // the tile's name for screen readers, and the tile's own tip, shown when it has focus (#358)
  const real = nice(n) !== n ? n : null, about = [real, st && st.title].filter(Boolean).join("\n");
  const tile = el("button", {class: "tile", "data-tip": about || null, onclick: () => goSeries(n)},
    el("span", {class: "lbl"}, el("span", {text: nice(n), "data-tip": real}), real ? el("span", {class: "vh", text: ` (${real})`}) : null,
      st ? el("span", {class: "st " + st.cls, "data-tip": st.title}, st.text) : null,
      st && st.title ? el("span", {class: "vh", text: `, ${st.title.toLowerCase()}`}) : null),
    el("span", {class: "val"}, last[5] || fmtNum(last[1]), el("small", {text: s.unit})),
    ucOrig(s, last) ? el("span", {class: "orig", text: "Reported " + ucOrig(s, last)}) : null,   // #41
    // "as of" (a value older than the latest draw) on a line of its own, with no "·" to start a line (#389, #338); a
    // date of its own for the change ("since Sep 10, 2026": not the header's) on the next line, in either font, so the
    // tile is as tall in the fallback as in Inter
    // the change as ↑ / ↓ or "no change" (util.js chgWords), never ▲ / ▼, a result's own flags (#623)
    el("span", {class: "delta"}, ...(d == null ? [] : !sure ? ["change not worked out"] : [...keepDates(chgWords(d, fmtNum(Math.abs(d) < 1e-9 ? 0 : Math.abs(d)))
      + (isoDay(prev.ts) === since ? "" : "\u00a0since")), ...(isoDay(prev.ts) === since ? [] : [" ", el("br"), ...keepDates(fmtD(prev.ts))])]),
      ...(last.ts < (lastDraw || 0) - 2 * DAY ? [...(d == null ? [] : [" ", el("br")]), ...keepDates(`as of ${fmtD(last.ts)}`)] : [])),
    // (#1103) none under two results: a lone dot in a band says nothing about a trend (its room kept, the tiles level)
    spark(s, recent.filter(p => Number.isFinite(p[1])).length < 2 ? [] : recent));
  // "What is this test?" (#163, test-pages.js): a link can't be inside the tile's button, so beside it in one card
  const tq = testLink(n, true);
  return tq ? el("div", {class: "tile-tq"}, tile, tq) : tile;
}
/* Key labs: the header says the latest draw, the date the changes are from (once), and what's outside its range at its
   latest result: every key lab, however long ago it was last tested ("not repeated since"), and any other lab in the
   last two weeks of draws. Then the tiles, in the person's order, grouped by panel (labGroups, trends.js: #279) */
function ovKeyLabs(lastDraw) {
  const keyLabs = keyLabTiles(CFG.overview.keyLabs, SERIES, JSON.stringify(CFG.overview.keyLabs) === JSON.stringify(DEFAULT_SETTINGS.overview.keyLabs));
  // a lab kept in two units is two series of one name (#438): its tile is the one with the latest result (#451)
  const latestOf = n => SERIES.filter(s => s.name === n && !s.watch && s.pts.length).reduce((a, s) => !a || s.pts.at(-1).ts > a.pts.at(-1).ts ? s : a, null);
  const shown = keyLabs.map(latestOf).filter(Boolean);
  const tally = new Map();
  for (const s of shown) { const p = ovPrev(s); if (p) { const d = isoDay(p.ts); tally.set(d, (tally.get(d) || 0) + 1); } }
  const since = [...tally].sort((a, b) => b[1] - a[1] || b[0].localeCompare(a[0]))[0]?.[0] || null;   // the date most tiles compare with
  const out = [];
  for (const s of SERIES) {
    if (s.watch || !s.pts.length) continue;
    const last = s.pts.at(-1), key = keyLabs.includes(s.name);
    if (!last[4] || !(key || lastDraw && last.ts >= lastDraw - 14 * DAY)) continue;
    let i = s.pts.length - 1; while (i > 0 && s.pts[i - 1][4] === last[4]) i--;
    out.push({s, last, key, since: s.pts[i].ts, streak: s.pts.length - i, old: last.ts < (lastDraw || 0) - 2 * DAY});
  }
  out.sort((a, b) => (b.key - a.key) || b.s.n - a.s.n);
  const flags = el("div", {class: "ov-out", id: "ovOut"}, el("p", {class: "ov-out-say", text: out.length
    ? `${capFirst(flaggedWords(out.map(o => o.last[4]), "its"))} at its latest result (${out.length}):` : "Nothing outside its reference range at its latest result."}));   // (#819)
  for (const o of out.slice(0, 14)) {
    const st = statusOf(o.last), word = FLAG_OF(o.last[4]).word;
    flags.append(el("div", {class: "ov-row", ...pressable(() => goSeries(o.s.name))},
      el("span", {class: "n", text: nice(o.s.name)}), el("span", {class: "v"}, `${o.last[5] || fmtNum(o.last[1])} ${o.s.unit} `, el("span", {class: "st " + st.cls, text: st.text}), ucOrig(o.s, o.last) ? el("span", {class: "orig", text: " reported " + ucOrig(o.s, o.last)}) : null),
      el("span", {class: "r", text: refText(o.last)}),
      el("span", {class: "s", text: (o.streak > 1 ? `${word} on the last ${o.streak} results, since ${fmtD(o.since)}` : `${word} on ${fmtD(o.last.ts)}`) + (o.old ? ", not repeated since" : "")})));
  }
  if (out.length > 14) flags.append(el("p", {class: "hint", text: `${out.length - 14} more in Labs.`}));
  const groups = labGroups(shown);   // the series themselves, each looked up by itself (#438)
  // one line of hint, the dates; the rest behind its ⓘ (#280, #460)
  const hint = [lastDraw ? `Latest draw ${fmtD(lastDraw)}` : "", since ? `changes since ${fmtD(dayTs(since))}` : ""].filter(Boolean).join(" · ");
  const parts = [ovHead("Key labs", hint ? el("p", {class: "hint"}, keepDates(hint)) : null),
    aboutWords("Sparklines show the last 6 months.", "A tile opens its lab's full trend on Labs."), flags];
  for (const g of groups) {
    if (groups.length > 1) parts.push(el("h3", {class: "ov-panel lab-panel", text: g.panel}));
    parts.push(el("div", {class: "tiles"}, ...g.labs.map(s => labTile(s, since, lastDraw))));
  }
  parts.push(el("p", {class: "range-note", text: RANGE_NOTE + (RANGES_USED ? RANGE_NOTE_SETTINGS : "")}));
  return parts;
}

/* the watch's and the scale's tiles: the last 7 days against the 4 weeks before, the latest weight against the weight
   at diagnosis */
function watchTiles() {
  const ws = ["hr_rest", "sleep", "steps", "hr", "weight"].map(k => SERIES.find(s => s.key === k)).filter(s => s && s.pts.length);
  if (!ws.length) return [];
  const out = [], now = Math.max(...ws.filter(s => s.key !== "weight").flatMap(s => s.pts.map(p => p.ts)));
  const avg = (s, a, b) => { const v = s.pts.filter(p => p.ts > a && p.ts <= b).map(p => p[1]); return v.length ? v.reduce((x, y) => x + y, 0) / v.length : null; };
  for (const s of ws) {
    if (s.key === "weight") {
      const last = s.pts.at(-1), pm = primaryMs(), dx = pm ? dayTs(pm.date) : NaN;
      const at = pm ? s.pts.filter(p => Math.abs(p.ts - dx) < 75 * DAY).sort((a, b) => Math.abs(a.ts - dx) - Math.abs(b.ts - dx))[0] : null;
      const d = at && last !== at ? last[1] - at[1] : null;
      // the scale's readings left out (store.js weightsOut, #754) over the sparkline's year and after the latest kept,
      // said as Body says them: a later one was the latest nowhere, and said nowhere (#783)
      const gone = (s.out || []).filter(p => p.ts > last.ts - 365 * DAY).length;
      out.push(el("button", {class: "tile", onclick: () => { if (pm) setPeriod(msKey(pm)); pickTab("health"); }},
        el("span", {class: "lbl", text: "Weight"}),
        el("span", {class: "val"}, last[1].toFixed(1), el("small", {text: s.unit})),
        ucOrig(s, last) ? el("span", {class: "orig", text: "Reported " + ucOrig(s, last)}) : null,   // #41
        el("span", {class: "delta"}, keepDates((d != null ? `${chgWords(d, Math.abs(d).toFixed(1), " " + s.unit)} since ${lcLabel(pm.label)} · ${fmtD(last.ts)}` : fmtD(last.ts))
          + (gone ? ` · ${gone} scale reading${gone === 1 ? "" : "s"} left out` : ""))),
        spark(s, s.pts.filter(p => p.ts > last.ts - 365 * DAY), {band: false})));
      continue;
    }
    const cur = avg(s, now - 7 * DAY, now), base = avg(s, now - 35 * DAY, now - 7 * DAY);
    const label = {hr_rest: "Resting heart rate", sleep: "Sleep", steps: "Steps", hr: "Heart rate, average"}[s.key];
    const fmt = v => s.key === "steps" ? Math.round(v).toLocaleString("en-US") : s.key === "sleep" ? v.toFixed(1) : Math.round(v).toString();
    out.push(el("button", {class: "tile", onclick: () => { setPeriod("30d"); pickTab("health"); }},
      el("span", {class: "lbl", text: label}),
      el("span", {class: "val"}, cur == null ? "–" : fmt(cur), el("small", {text: s.key === "sleep" ? "h / night" : s.key === "steps" ? "/ day" : "bpm"})),
      el("span", {class: "delta", text: cur != null && base != null ? `${chgWords(cur - base, fmt(Math.abs(cur - base)))} vs prior 4 weeks` : "not enough history yet"}),
      spark(s, s.pts.filter(p => p.ts > now - 30 * DAY), {band: false})));
  }
  return out;
}
/* Readings: the watch's, the scale's and the readings at home (#225), in one row */
function ovReadings() {
  const watch = watchTiles(), home = homeTiles();
  if (!watch.length && !home.length) return [ovHead("Readings"), ovNone("Readings from a watch, a smart scale or taken at home appear here as tiles, each with its latest value.")];
  return [ovHead("Readings", watch.length ? el("p", {class: "hint", text: "The watch's last 7 days against the 4 weeks before, and the latest weight against your weight at diagnosis."}) : null),
    watch.length ? aboutWords("Sparklines show the last 30 days for the watch, and the last year for weight.") : null,
    el("div", {class: "tiles ov-read"}, ...watch, ...home)];
}

/* a record's row (the Recent list's, the immunizations'): its kind's icon in its lane's colour, as on the Timeline (#354) */
function eventRow(e, s) {
  const key = kindIcon(e.k, "key"); key.style.color = CAT_OF[e.k].color;
  return el("div", {class: "ov-row", "data-id": e.id, ...pressable(ev => openFrom(ev.currentTarget, () => openEvent(e)))},
    el("span", {class: "n"}, key, e.title), el("span", {class: "r", text: fmtD(e.ts)}), s != null ? el("span", {class: "s", text: s}) : null);
}
const NOT_CLINICAL = new Set(["exercise", "ecg", "rhythm", "journal", "mood", "cycle", "auto_msg", "claim"]);   // the watch's, the person's own, insurers'

function ovRecord() {
  const left = [], right = [];
  // problem list (allergies have a card of their own, under it: #179)
  const probs = (DATA.problems || []).filter(p => (p.status || "").toLowerCase() === "active" && p.type !== "Allergy");
  if (probs.length) {
    const pc = el("div", {class: "card"}, el("h3", {text: `Active problem list (${probs.length})`}), listOf(probs.map(p => p.src)) ? el("p", {class: "hint", text: "As recorded by " + listOf(probs.map(p => p.src))}) : null);
    for (const p of probs) pc.append(el("div", {class: "ov-row still"}, el("span", {class: "n plain", text: p.name}), el("span", {class: "r", text: p.date ? fmtD(dayTs(p.date)) : ""})));
    left.push(pc);
  }
  // ---- allergies and immunizations (#179): as the records say them, from C-CDA, FHIR, a Health Card or typed in
  const ended = s => /^(resolved|inactive|refuted|entered in error)$/i.test(s || "");
  const allergies = (DATA.problems || []).filter(p => p.type === "Allergy" && !ended(p.status));
  if (allergies.length) {
    const ac = el("div", {class: "card", id: "ovAllergies"}, el("h3", {text: `Allergies and intolerances (${allergies.length})`}),
      listOf(allergies.map(p => p.src)) ? el("p", {class: "hint", text: "As recorded by " + listOf(allergies.map(p => p.src))}) : null);
    for (const p of allergies) ac.append(el("div", {class: "ov-row still"},
      el("span", {class: "n plain", text: p.substance || p.name.replace(/^Allergy: /, "")}),
      el("span", {class: "r", text: p.date ? fmtD(dayTs(p.date)) : ""}),
      el("span", {class: "s", text: [(p.reactions || []).length ? "Reaction: " + p.reactions.join(", ") : "", p.severity ? "Severity: " + p.severity : "",
        p.criticality ? "Criticality: " + p.criticality : "", p.status && p.status !== "Active" ? p.status : ""].filter(Boolean).join(" · ")})));
    left.push(ac);
  }
  // ---- key documents (#1135): each marked one, a tap from its drawer
  const keys = EV.filter(e => e.keyDoc && !e.hidden).sort((a, b) => Object.keys(KEYDOC).indexOf(a.keyDoc.kind) - Object.keys(KEYDOC).indexOf(b.keyDoc.kind) || b.ts - a.ts);
  if (keys.length) left.push(el("div", {class: "card", id: "ovKeyDocs"}, el("h3", {text: `Key documents (${keys.length})`}),
    ...keys.map(e => eventRow(e, KEYDOC[e.keyDoc.kind] + (e.keyDoc.kept ? ` · kept at ${e.keyDoc.kept}` : "")))));
  const imms = EV.filter(e => e.k === "immunization" && !e.hidden).sort((a, b) => b.ts - a.ts);
  if (imms.length) {
    const ic = el("div", {class: "card", id: "ovImmunizations"}, el("h3", {text: `Immunizations (${imms.length})`}),
      el("p", {class: "hint", text: "Vaccines the records say were given, newest first"}));
    for (const e of imms.slice(0, 8)) { const d = DATA.details[e.id] || {}; ic.append(eventRow(e, [d.dose ? "Dose " + d.dose : "", d.where || d.performer].filter(Boolean).join(" · "))); }
    // (all of them for this visit: the period and kinds saved stay the person's, #475)
    if (imms.length > 8) ic.append(el("button", {class: "btn mt-2", onclick: () => {
      periodVisit("all"); kindsVisit(["immunization"]); pickTab("timeline"); }}, `All ${imms.length} on the Timeline `, icon("arrow-right")));
    left.push(ic);
  }
  // (#1134) family history, grouped by relative (family.js); with no relative yet, only beside the record's other cards:
  // a Health record with nothing else says what would be there (#440), Add a relative in the palette
  const fam = ovFamily();
  if (fam && (left.length || famGroups().length || (DATA.hiddenFamily || []).length)) left.push(fam);
  // Recent: the clinical records (cycle records only in their lane, #183), and the watch's workouts in one line: those
  // of the 30 days to the latest one
  const recent = EV.filter(e => !e.hidden && !NOT_CLINICAL.has(e.k)).sort((a, b) => b.ts - a.ts).slice(0, 8);
  const all = EV.filter(e => e.k === "exercise" && !e.hidden && e.ts <= Date.now()), end = all.length ? Math.max(...all.map(e => e.ts)) : 0;
  const works = all.filter(e => e.ts > end - 30 * DAY);
  if (recent.length || works.length) {
    const rc = el("div", {class: "card", id: "ovRecent"}, ...recent.map(e => eventRow(e, e.sub || null)));
    if (works.length) {
      const min = works.reduce((m, e) => m + ((DATA.details[e.id] || {}).minutes || 0), 0), key = kindIcon("exercise", "key");
      key.style.color = CAT_OF.exercise.color;
      rc.append(el("div", {class: "ov-row", id: "ovWorkouts", ...pressable(() => { setPeriod("30d"); pickTab("health"); })},
        el("span", {class: "n"}, key, `${works.length} workout${works.length === 1 ? "" : "s"} ` + (end > Date.now() - 2 * DAY ? "in the last 30 days" : `in the 30 days to ${fmtD(end)}`)),
        el("span", {class: "r", text: min >= 60 ? `${Math.floor(min / 60)} h ${min % 60} min` : `${min} min`}),
        el("span", {class: "s", text: listOf(works.map(e => e.title)) + ", from the watch"})));
    }
    right.push(ovHead("Recent"), rc);
  } else right.push(ovHead("Recent"), ovNone("The latest visits, results and other clinical records appear here."));
  // insurance cost summary
  if ((DATA.costs || []).length) {
    const rows = DATA.costs, pm = primaryMs(), dx = pm ? rows.filter(r => r.d >= pm.date) : rows;
    const cc = el("div", {class: "card"}, dx.length ? el("p", {class: "hint", text: `Claims from ${fmtD(dayTs(dx.reduce((m, r) => r.d < m ? r.d : m, dx[0].d)))} on (${listOf(insurerSpans(dx).map(s => s[0]), ", then ")}).`}) : null);
    cc.append(threeFigures(claimFigures(dx, false)),   // Costs' three figures, in its order, look and layout (costs.js, #442, #448, #456)
      ovGo("costs", "Open Costs", () => { if (pm) setPeriod(msKey(pm)); costState.view = "claims"; pickTab("costs"); }));   // the same period, on Claims (#443), as the Overview's other ways to a tab (#440)
    right.push(ovHead(pm ? `Insurance costs since ${lcLabel(pm.label)}` : "Insurance costs"), cc);
  }
  return el("div", {class: "ov-cols"}, el("div", {}, ovHead("Health record"),
    ...(left.length ? left : [ovNone("Active problems, allergies, immunizations and family history appear here, as the records list them.")])), el("div", {}, ...right));
}

/* nothing imported yet, whatever there is of a journal or a watch (#477: the empty profile's card, #199, is gone once
   either is there): the ways to bring records in, first. For those who only view it, who brings them in */
function ovStart() {
  if (!DATA.imports || Object.keys(DATA.imports).length) return [];
  const q = `?profile=${PROFILE.id}`, go = (href, text, cls = "") => el("a", {class: `btn large empty-next ${cls}`, href, text});
  if (!canEdit()) return [ovHead("Get started"), emptyState(`Nothing is imported into ${PROFILE.name} yet: its owners and editors bring records in, and they appear here for you too.`,
    {href: `sharing.html${q}#people`, text: "See who has access"})];
  const card = emptyState(`Nothing is imported into ${PROFILE.name} yet: lab results, visits, medications and the rest appear here once they're brought in.`);
  card.append(el("div", {class: "empty-acts"}, go(`guide.html${q}`, "Open the guide", "primary"), go(`sources.html${q}#upload`, "Upload files"),
    WATCH ? null : go(`sources.html${q}#phone`, "Connect a phone"), go(`sources.html${q}#manual`, "Type records in")));
  return [ovHead("Get started"), card];
}

function renderOverview() {
  const box = $("#view-overview"); box.replaceChildren();
  const labDays = EV.filter(e => e.k === "lab").map(e => e.ts);
  const lastDraw = labDays.length ? Math.max(...labDays) : null;
  // no lab results yet (#199): what the tiles would show, and where results come from, in place of the key labs
  // (which would otherwise say nothing is outside its range)
  const noLabs = !SERIES.some(s => !s.watch && s.pts.length);
  box.append(...ovStart(), ...present(...ovToday()));
  if (noLabs) box.append(ovHead("Key labs"), emptyState("Key lab results appear here as tiles, each with its latest value and whether it's outside its reference range.",
    canEdit() ? {href: `sources.html?profile=${PROFILE.id}#manual`, text: "Add lab results"} : null));
  else box.append(...ovKeyLabs(lastDraw));
  box.append(...present(...ovReadings(), ovRecord()));
}
