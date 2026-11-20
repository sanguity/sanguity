/* Sanguity · the records page's data store (#271): the records (GET api/profiles/{pid}/data), the profile's settings
   and the watch data in one place, with what every tab reads worked out from them (EV, the events, the journal's and
   the watch's among them; STAYS, SERIES, byId, STEPS), and a change notice for each part: "events", "labs",
   "medications" (and the doses logged, the reminders), "journal" and "costs" (the out-of-pocket part, DATA.oop).
   A view says which parts it shows (storeOn) and is drawn again, in place, only when one of them changes: the tab shown
   (app.js) and the journal's card (journal.js). A change made on the page doesn't load the page again: the API's
   reply carries what it changed, put in here (storePut: journal entries, doses, reminders, a Costs mark's out-of-pocket
   part), or, for an edit, which can change any record, the records whole (storeRecords, drawer.js afterEdit); the
   offline copy takes the same reply (offline.js, #405). Switching profile still loads the page.
   Plain script, loaded in order by index.html after core.js; all files share one global scope. */
"use strict";
let CFG = DEFAULT_SETTINGS;   // the settings (core.js has the defaults)
let DATA, EV = [], STAYS = [], SERIES = [], byId = new Map(), WATCH = null, STEPS = [];
const STORE_PARTS = ["events", "labs", "medications", "journal", "costs"];

/* each value as the server sent it (a name map it sends is the whole map: a default nickname removed in Settings
   stays removed); a missing or malformed one keeps the default */
function applySettings(u) {
  if (!u || typeof u !== "object") return;
  const obj = v => v && typeof v === "object" && !Array.isArray(v), list = (v, d) => Array.isArray(v) ? v : d;
  CFG = {...DEFAULT_SETTINGS, ...u,
    overview: {...DEFAULT_SETTINGS.overview, ...(obj(u.overview) ? u.overview : {})},
    trends: {...DEFAULT_SETTINGS.trends, ...(obj(u.trends) ? u.trends : {})},
    labNicknames: obj(u.labNicknames) ? u.labNicknames : DEFAULT_SETTINGS.labNicknames,
    sources: obj(u.sources) ? u.sources : DEFAULT_SETTINGS.sources,
    visitWorkouts: list(u.visitWorkouts, DEFAULT_SETTINGS.visitWorkouts),
    milestones: list(u.milestones, DEFAULT_SETTINGS.milestones),
    tabs: obj(u.tabs) ? u.tabs : DEFAULT_SETTINGS.tabs,
    theme: ["light", "dark", "system"].includes(u.theme) ? u.theme : DEFAULT_SETTINGS.theme,
    units: {...DEFAULT_SETTINGS.units, ...(obj(u.units) ? u.units : {})},
    person: {...DEFAULT_SETTINGS.person, ...(obj(u.person) ? u.person : {})}};
  CFG.overview.keyLabs = list(CFG.overview.keyLabs, DEFAULT_SETTINGS.overview.keyLabs);
  CFG.trends.defaultLabs = list(CFG.trends.defaultLabs, DEFAULT_SETTINGS.trends.defaultLabs);
  CFG.trends.views = list(CFG.trends.views, DEFAULT_SETTINGS.trends.views);
  // milestones are a setting since #39 (the server moved those of data.json and corrections.json in once)
  MILESTONES = CFG.milestones.filter(m => m && /^\d{4}-\d{2}-\d{2}/.test(m.date || ""))
    .map(m => ({...m, date: m.date.slice(0, 10), label: m.label || "Milestone"})).sort((a, b) => a.date.localeCompare(b.date));
  if (DATA) labDayWords();   // a lab day's search words, with the names labs are shown by now (#463: a rename)
}

/* ---------------------------------------------------------------- change notices */
const storeSubs = [];   // [parts, fn]
/* fn(parts changed, a Set) whenever one of these parts changes */
const storeOn = (parts, fn) => storeSubs.push([[].concat(parts), fn]);
function storeChanged(...parts) {
  const got = new Set(parts);
  for (const [want, fn] of storeSubs) if (want.some(p => got.has(p))) fn(got);
}
/* draw() draws part of the page again; the control that had focus, if draw() replaced it, gives focus to its new self
   (the same id, or the same kind of control with the same name, shown), so a redraw doesn't drop it to the page; an ⓘ left
   open opens again (util.js aboutKeep); and what focus goes back to when the drawer closes is found again too */
const focusKey = x => [x.tagName, x.type, x.id || x.getAttribute("aria-label") || (x.labels?.[0] || x).textContent.trim(), JSON.stringify(x.dataset)].join("\n");
/* the control in sight, clear of the bar at the top and what's over the bottom (html's scroll-padding) */
function inSight(x) {
  const r = x.getBoundingClientRect(), pad = getComputedStyle(document.documentElement);
  return r.bottom > parseFloat(pad.scrollPaddingTop) && r.top < innerHeight - parseFloat(pad.scrollPaddingBottom);
}
function keepFocus(draw) {
  const a = document.activeElement, key = a && a !== document.body ? focusKey(a) : null, open = aboutOpen(), seen = key && inSight(a);
  draw();
  aboutWire();   // the ⓘ drawn with it, there now to be found (util.js, #280)
  // one shown: a tab not shown keeps its controls, and a twin of the same name there can't take focus (#455)
  if (key && !a.isConnected) [...document.querySelectorAll(a.tagName)].find(x => focusKey(x) === key && x.checkVisibility())?.focus({preventScroll: true});
  // in sight before, in sight after: a page that grew shorter, scrolled up by the browser, can't leave it above the
  // top (#469, 2.4.11)
  const f = document.activeElement;
  if (seen && f !== document.body && !inSight(f)) f.scrollIntoView({block: "nearest"});
  aboutKeep(open);   // and one left open, open still (#447)
  drawerBack();   // and what opened the drawer, found again (drawer.js, #404)
}

/* ---------------------------------------------------------------- the records */
/* the records as the API sends them (with WATCH and CFG already in): every event and series made ready to draw, the
   watch's charts and workouts joined to them, the journal, the medications and their log; then a notice for each part */
function storeRecords(data) {
  DATA = data; byId = new Map();
  for (const e of DATA.events) {
    e.ts = zParse(e.t); e.te = e.end ? zParse(e.end) : e.ts;
    e._q = e._q0 = (e.title + " " + (e.sub || "") + " " + (KIND_LABEL[e.k] || "")).toLowerCase();   // (_q0: before labDayWords)
    byId.set(e.id, e);
    if (e.k === "lab") { const d = DATA.details[e.id]; e.n = d ? d.ids.length : 1; }
  }
  EV = [...DATA.events];
  STAYS = DATA.events.filter(e => e.k === "stay");
  for (const e of DATA.events) if (e.k === "lab" && e.sub) e.sub = labDaySub(e);   // its panels as Labs names them (util.js, #451)
  for (const s of DATA.labs) for (const p of s.pts) p.ts = zParse(p[0]);
  // #44: results with no range of their own (ranges.js). Before the units, since an override is stated in the
  // reported unit and is then converted with the values (#41)
  RANGES_USED = applyRangeOverrides(DATA.labs, rangeConfig(CFG));   // with About this person's birth date and sex (#194)
  applyUnits(DATA.labs); applyUnitsToRows(DATA.labs, DATA.details);   // this person's units, exact conversions only (units.js, #41)
  SERIES = joinSeries(DATA.labs);   // one chart per test, whichever imports brought it (same.js)
  labDayWords();   // a lab day found by its tests' names (#459)
  STEPS = [];
  if (WATCH) storeWatch();
  journalLoad();   // the journal's entries join EV as events of kind "journal" (journal.js)
  if (CYCLE.length) { for (const e of CYCLE) byId.set(e.id, e); EV = EV.concat(CYCLE); }   // cycle tracking, loaded once (cycle.js, #183)
  medsLoad();      // each medication's spans and fills (meds.js, #50)
  medLogLoad();    // the medication log: doses typed in and the phone's (medlog.js, #235)
  storeSpan();
  // what was worked out from the records before: the calendar years, Body and activity's data, the details opened
  YEARS = null; H = null; G = undefined; DETAILS_GOT.clear(); DETAILS_MISSED.clear();
  if (booted && state.preset !== "custom") setPeriod(state.preset);   // a period ending at the latest record
  storeChanged(...STORE_PARTS);
}
/* a lab day's search words (#459): besides its title, panels and kind, its reports' titles (the panels again, as the
   records spell them) and its tests' names, as the records and Labs name them ("ferritin" finds the draws with a
   Ferritin result, not only "iron studies"). Its tests are its own results, not its day's (util.js labDraws, #470:
   an outside lab's creatinine typed in on a clinic draw's day isn't the clinic draw's), kept as e.res [[series,
   point]] for its badge under a search (timeline.js tlAbn), a visit's Vitals aside. Made again when the settings
   change (applySettings): a lab renamed is found by its new name at once, and no longer by the old (#463) */
function labDayWords() {
  const draws = DATA.events.filter(e => e.k === "lab"), at = e => (DATA.details[e.id]?.ids || []).map(i => byId.get(i)?.ts).filter(isFinite);
  const own = labDraws(draws.map(e => ({id: e.id, imp: e.imp, ts: e.ts, at: at(e)})), SERIES.filter(s => !s.watch && s.panel !== "Vitals"), p => p.ts, isoDay);
  for (const e of draws) {
    const d = DATA.details[e.id], names = [...new Set((e.res = own.get(e.id)).map(([s]) => s.name))];
    e._q = e._q0 + " " + [...(d ? d.ids.map(i => byId.get(i)?.title || "") : []), ...names, ...names.map(nice)].join(" ").toLowerCase();
  }
}
/* the records' span, the watch's last day included, and the journal's and cycle tracking's entries (EV, #472): one
   written after the latest import is in the period that ends at the latest record, as the palette finds it */
function storeSpan() {
  let min = Infinity, max = -Infinity;
  // the person's own entries (journal, mood, cycle) count up to today, not past it: one dated ahead mustn't move the
  // end of every period there (#472)
  const own = new Set(["journal", "mood", "cycle"]), today = dayTs(zToday()) + DAY - 1;
  for (const e of EV) if (isFinite(e.ts)) { const t = own.has(e.k) ? Math.min(e.ts, today) : e.ts; if (t < min) min = t; if (t > max) max = t; }
  state.min = min; state.max = max;
  for (const s of SERIES) if (s.watch && s.pts.length) state.max = Math.max(state.max, s.pts[s.pts.length - 1].ts);
}
/* the watch data from the Health Connect receiver, joined to the records: its charts in SERIES, its workouts, ECGs and
   rhythm notifications in the events */
function storeWatch() {
  const M = Object.fromEntries((WATCH.metrics || []).map(m => [m.key, m]));
  const byDay = k => Object.fromEntries(M[k] ? M[k].pts : []);
  const mkPts = m => m.pts.map(([d, v]) => { const p = [d + "T12:00:00", v, null, null, "", ""]; p.ts = dayTs(d); p.day = d; return p; });
  const add = (m, extra) => { const pts = mkPts(m); const s = {name: m.name, unit: m.unit, panel: m.group === "Body" ? "Body · smart scale" : "Watch · " + m.group, n: pts.length, pts, txt: [], watch: true, key: m.key, kind: "line", conv: m.conv, ...extra}; SERIES.push(s); return s; };
  if (M.hr_avg) {   // one chart: daily average inside a low–high band
    const lo = byDay("hr_min"), hi = byDay("hr_max");
    const s = add(M.hr_avg, {name: "Heart rate", key: "hr", kind: "band"});
    for (const p of s.pts) { p.lo = lo[p.day]; p.hi = hi[p.day]; }
  }
  if (M.sleep_h) {  // stacked deep / REM / light per night
    const parts = {deep: byDay("sleep_deep_min"), rem: byDay("sleep_rem_min"), light: byDay("sleep_light_min")};
    const s = add(M.sleep_h, {name: "Sleep", key: "sleep", kind: "stack"});
    for (const p of s.pts) p.parts = {deep: (parts.deep[p.day] || 0) / 60, rem: (parts.rem[p.day] || 0) / 60, light: (parts.light[p.day] || 0) / 60};
  }
  if (M.steps) { add(M.steps, {kind: "bars"}); STEPS = mkPts(M.steps).map(p => [zParse(p.day + "T00:00:00"), p[1]]); }
  const cw = DATA.clinicWeights || [];
  if (M.weight || cw.length) {
    const s = add(M.weight || {key: "weight", name: "Weight", unit: "lb", group: "Body", pts: [], conv: DATA.unitConv?.lb}, {name: "Body weight", key: "weight"});
    const have = new Set(s.pts.map(p => p.day));
    for (const [t, v, org] of cw) {
      const day = t.slice(0, 10); if (have.has(day)) continue; have.add(day);
      const p = [day + "T12:00:00", v, null, null, "", "", "Clinic"]; p.ts = dayTs(day); p.day = day; p.org = org; s.pts.push(p);
    }
    s.pts.sort((a, b) => a.ts - b.ts); s.n = s.pts.length;
  }
  const folded = new Set(["weight", "hr_avg", "hr_min", "hr_max", "sleep_h", "sleep_deep_min", "sleep_rem_min", "sleep_light_min", "sleep_awake_min", "steps"]);
  for (const m of WATCH.metrics || []) if (!folded.has(m.key) && !m.key.startsWith("glucose_")) add(m);   // glucose: Body and activity's own (#181)
  let wid = DATA.events.reduce((m, e) => Math.max(m, +e.id || 0), 1e7);   // after every record's id
  for (const x of WATCH.exercise || []) {
    const ev = {id: ++wid, t: x.start, end: x.end, k: "exercise", title: x.name.replace(/_/g, " "), sub: `${x.minutes} min`};
    ev.ts = zParse(ev.t); ev.te = zParse(ev.end); ev._q = (ev.title + " workout exercise").toLowerCase();
    DATA.events.push(ev); byId.set(ev.id, ev); DATA.details[ev.id] = {type: "exercise", kind: x.kind, minutes: x.minutes, hr: x.hr};   // hr: heart rate zones (#197)
  }
  ecgLoad(wid);   // ECGs and irregular rhythm notifications (ecg.js, #182)
  EV = [...DATA.events];
  applyUnits(SERIES.filter(s => s.watch));   // after the clinic weights joined the scale's (#41)
}
/* a change's reply (the API's, #271) put in: list, the records' list it changed ("journal", "doses" or "reminders"),
   reply[list] the rows added or changed, reply.removed the ids of those removed; or "oop", a Costs mark's, reply.oop
   the out-of-pocket part whole (#405). Only what follows from it is worked out again, and the part it belongs to gets
   its notice. */
function storePut(list, reply) {
  if (list === "oop") { DATA.oop = reply.oop || undefined; storeChanged("costs"); return; }
  DATA[list] = putRows(DATA[list], list, reply);
  if (list === "journal") {   // the span with it, and a period ending at the latest record with that (#472)
    journalLoad(); YEARS = null; storeSpan();
    if (booted && state.preset !== "custom") setPeriod(state.preset);
  } else if (list === "doses") medLogLoad();
  storeChanged(list === "journal" ? "journal" : "medications");
}
/* a list's rows with a change's reply put in, as the server lists them: reminders as they were set, the rest by date
   (the offline copy's too, offline.js) */
function putRows(rows, list, reply) {
  const gone = new Set([...(reply.removed || []), ...(reply[list] || []).map(x => x.id)]);
  return (Array.isArray(rows) ? rows : []).filter(x => !gone.has(x.id)).concat(reply[list] || [])
    .sort((a, b) => list !== "reminders" && ((a.t > b.t) - (a.t < b.t)) || a.id - b.id);
}
