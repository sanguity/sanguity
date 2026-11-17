/* Sanguity · settings, helpers, formatting, shared state, persistence, the records bar's period, tooltip, milestones.
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
const CATS = [
  {k:"stay",      label:"Hospital stays",   color:"var(--stay-line)", kinds:["stay"]},
  {k:"visit",     label:"Visits",           color:"var(--c-visit)",   kinds:["visit"]},
  {k:"lab",       label:"Labs",             color:"var(--c-lab)",     kinds:["lab"]},
  {k:"imaging",   label:"Imaging",          color:"var(--c-imaging)", kinds:["imaging"]},
  {k:"procedure", label:"Procedures",       color:"var(--c-procedure)",kinds:["procedure"]},
  {k:"study",     label:"Reports & studies",color:"var(--c-study)",   kinds:["study"]},
  {k:"message",   label:"Messages",         color:"var(--c-message)", kinds:["message","appt_msg","auto_msg"]},
  {k:"letter",    label:"Letters & docs",   color:"var(--c-letter)",  kinds:["letter","document"]},
  {k:"med",       label:"Medications",      color:"var(--muted)",     kinds:["med","rx"]},
  {k:"claim",     label:"Insurance claims", color:"var(--axis)",      kinds:["claim"]},
  {k:"watch",     label:"Watch",            color:"var(--ink2)",      kinds:["exercise"]},
  {k:"journal",   label:"Journal",          color:"var(--c-journal)", kinds:["journal"]},   // the person's own entries (journal.js)
];
const CAT_OF = {}; CATS.forEach(c => c.kinds.forEach(k => CAT_OF[k] = c));
const KIND_LABEL = {lab:"Lab draw",imaging:"Imaging",procedure:"Procedure",study:"Report / study",message:"Conversation",appt_msg:"Appointment message",auto_msg:"Automated message",letter:"Letter",document:"Document",stay:"Hospital stay",visit:"Visit",labitem:"Lab result",exercise:"Workout (watch)",med:"Medication",rx:"Pharmacy fill",claim:"Insurance claim",journal:"Journal"};
const usd = v => (v || 0).toLocaleString(undefined, {style: "currency", currency: "USD", maximumFractionDigits: 0});
/* The profile's settings (server/settings.py), changed on the Settings page. The server sends every one, with its
   defaults filled in; these are only what the page uses if that fails, and should match the server's defaults. */
const DEFAULT_SETTINGS = {
  overview: {keyLabs: ["Hemoglobin", "Platelet Count", "WBC", "Automated Abs Neutrophil Cnt", "Lymphocytes Absolute", "Creatinine",
    "ALT", "AST", "Total Bilirubin", "Potassium, Blood", "Sodium, Blood", "Magnesium"]},
  trends: {defaultLabs: ["Hemoglobin", "Platelet Count", "WBC", "Automated Abs Neutrophil Cnt", "Creatinine", "ALT"],
    views: []},        // #37, saved views: [{name, labs, default}] (trends.js)
  labNicknames: {"Automated Abs Neutrophil Cnt": "Neutrophils (absolute)", "Lymphocytes Absolute": "Lymphocytes (absolute)", "Platelet Count": "Platelets",
    "Potassium, Blood": "Potassium", "Sodium, Blood": "Sodium", "Total Bilirubin": "Bilirubin, total"},
  tabs: {order: ["overview", "health", "timeline", "labs", "heatmap", "costs"], hidden: []},   // #40, applied by app.js
  theme: "system",     // #45, this user's default: "light", "dark" or "system" (the ◐ toggle overrides it per browser)
  sources: {},         // short source tags on lab results -> display names, e.g. {"EXT": "Outside lab"}
  units: {lab: "reported", weight: "reported", temperature: "reported"},   // #41, this user's (units.js)
  visitWorkouts: [],   // past visits whose title matches count as workouts on Health, e.g. {"match": "physical therapy", "regex": false, "name": "Physical therapy", "minutes": 45}
  milestones: [],      // named dates, e.g. {"date": "2026-03-01", "label": "Diagnosis", "primary": true} (#39)
};
let CFG = DEFAULT_SETTINGS;
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
    units: {...DEFAULT_SETTINGS.units, ...(obj(u.units) ? u.units : {})}};
  CFG.overview.keyLabs = list(CFG.overview.keyLabs, DEFAULT_SETTINGS.overview.keyLabs);
  CFG.trends.defaultLabs = list(CFG.trends.defaultLabs, DEFAULT_SETTINGS.trends.defaultLabs);
  CFG.trends.views = list(CFG.trends.views, DEFAULT_SETTINGS.trends.views);
}
const srcName = tag => CFG.sources[tag] || tag;
const DAY = 864e5;

// fmtD, fmtDT, fmtMonth, isoDay, dayTs, zParse, zdate: dates.js, in this person's own time zone and date format (#42)
const fmtNum = v => Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? (+v.toFixed(1)).toString() : (+v.toFixed(2)).toString();

/* ---------------------------------------------------------------- the account and profile being viewed (api() is in util.js) */
let ME = null, PROFILE = null;   // the signed-in account and the profile being viewed
const stateKey = () => "ht-state" + (PROFILE ? ":" + PROFILE.id : "");

let DATA, EV = [], STAYS = [], SERIES = [], byId = new Map(), WATCH = null, STEPS = [];
const hasLane = c => EV.some(e => CAT_OF[e.k] === c) || (c.k === "watch" && STEPS.length > 0);
/* preset: the records bar's period (#171), shared by every tab with time ("period" below); labsView: the Labs tab's
   "chart" or "heatmap" (#172) */
const state = { from: 0, to: 0, min: 0, max: 0, preset: "2y", cats: new Set(CATS.map(c => c.k).filter(k => k !== "claim")), q: "", tab: "overview", labsView: "chart", marks: true, smooth: false, sel: new Set(), selEv: null, tables: new Set(), asc: false };

/* ---------------------------------------------------------------- persistence */
function save() { try { localStorage.setItem(stateKey(), JSON.stringify({preset: state.preset, from: state.from, to: state.to, cats: [...state.cats], known: CATS.map(c => c.k), tab: state.tab, labsView: state.labsView, sel: [...state.sel], marks: state.marks, smooth: state.smooth, asc: state.asc, v: 2})); } catch (e) {} }
function restore() {
  try {
    let raw = localStorage.getItem(stateKey());
    // the one view saved before profiles existed goes to the first profile opened after the upgrade
    if (raw == null && (raw = localStorage.getItem("ht-state")) != null) localStorage.removeItem("ht-state");
    const s = JSON.parse(raw || "null"); if (!s) return;
    if (s.cats) { const known = new Set(s.known || CATS.filter(c => c.k !== "watch").map(c => c.k)); state.cats = new Set([...s.cats, ...CATS.filter(c => !known.has(c.k) && c.k !== "claim").map(c => c.k)]); }
    if (s.sel) state.sel = new Set(s.sel);
    if (s.tab && s.v === 2) [state.tab, state.labsView] = tabAlias(s.tab, s.labsView);   // Trends and Lab heatmap are Labs now (#172)
    if (s.preset) state.preset = s.preset;
    if (typeof s.marks === "boolean") state.marks = s.marks; if (typeof s.smooth === "boolean") state.smooth = s.smooth; if (typeof s.asc === "boolean") state.asc = s.asc;
    if (s.preset === "custom" && s.from && s.to) { state.from = s.from; state.to = s.to; }
  } catch (e) {}
}
/* the light/dark toggle is in the top bar's menu (nav.js) */

/* ---------------------------------------------------------------- period (#171)
   The records bar's one period, shared by every tab with time (Body and activity, Timeline, Labs, Costs): a preset
   ("all", "5y" ... "30d"), a milestone ("m0", "m1"...: since that date), a calendar year ("2024"), or "custom" (dates
   typed in, or a chart dragged or zoomed). state.from/to are its dates. Presets and milestones end at the latest
   record; "All" is everything each tab has (Body and activity: the watch and scale data's span; Costs: every claim). */
const PRESETS = [["all","All"],["5y","5y"],["2y","2y"],["1y","1y"],["6m","6m"],["90d","90d"],["30d","30d"]];
const PRESET_DAYS = {"5y": 1826, "2y": 730, "1y": 365, "6m": 182, "90d": 90, "30d": 30};
const PRESET_TEXT = {"5y": "the last 5 years", "2y": "the last 2 years", "1y": "the last year", "6m": "the last 6 months", "90d": "the last 90 days", "30d": "the last 30 days"};
const isYearKey = k => /^\d{4}$/.test(k || "");
/* the period's dates in state, drawing nothing (the tab switched to next draws it) */
function setPeriod(p) {
  if (p === "dx") p = msKey(primaryMs());   // the older saved key for the primary milestone ("all" if there's none)
  const end = state.max + DAY, m = msOf(p);
  if (!(p in PRESET_DAYS) && p !== "all" && !m && !isYearKey(p)) p = "all";   // e.g. a milestone since deleted
  state.preset = p;
  if (p === "all") { const pad = (state.max - state.min) * .02 + DAY; state.from = state.min - pad; state.to = state.max + pad; }
  else if (m) { state.to = end; state.from = Math.min(zParse(m.date + "T00:00:00"), end - 7 * DAY); }
  else if (isYearKey(p)) { state.from = zParse(p + "-01-01T00:00:00"); state.to = zParse((+p + 1) + "-01-01T00:00:00") - 1; }
  else { state.to = end; state.from = end - PRESET_DAYS[p] * DAY; }
}
function applyPreset(p) { setPeriod(p); renderAll(); }
function setRange(a, b, fromUser = true) {
  const minSpan = 2 * DAY; if (b - a < minSpan) { const m = (a + b) / 2; a = m - minSpan/2; b = m + minSpan/2; }
  state.from = a; state.to = b; if (fromUser) state.preset = "custom"; renderAll();
}
/* the period in words, for a heading: "the last 2 years", "since diagnosis (Mar 1, 2026)", "2024", or its dates */
function periodLabel(all = "everything on file") {
  const p = state.preset, m = msOf(p);
  if (m) return `since ${lcLabel(m.label)} (${fmtD(dayTs(m.date))})`;
  return p === "all" ? all : isYearKey(p) ? p : PRESET_TEXT[p] || `${fmtD(state.from)} – ${fmtD(state.to)}`;
}
/* a tab's time window: the period's dates, or with "All" the span of what that tab shows ([first, last]) */
const periodWindow = span => state.preset === "all" && span ? [span[0] - DAY / 2, span[1] + DAY / 2] : [state.from, state.to];
/* rows with a .d (YYYY-MM-DD) inside the period: every row with "All" */
function inPeriod(rows) {
  if (state.preset === "all") return rows;
  const a = isoDay(state.from), b = isoDay(state.to);
  return rows.filter(r => r.d >= a && r.d <= b);
}
/* the calendar years with records, newest first: the records' own dates, watch days and payments */
let YEARS = null;
function periodYears() {
  if (!YEARS) {
    const ys = new Set();
    for (const e of EV) if (/^\d{4}/.test(e.t || "")) ys.add(e.t.slice(0, 4));
    for (const s of SERIES) if (s.watch) for (const p of s.pts) ys.add(String(p[0]).slice(0, 4));
    for (const r of (DATA.outOfPocket || {}).payments || []) if (/^\d{4}/.test(r.d || "")) ys.add(r.d.slice(0, 4));
    YEARS = [...ys].filter(isYearKey).sort().reverse();
  }
  return YEARS;
}
function renderFilters() {
  const pm = primaryMs(), pk = pm ? msKey(pm) : null;   // the primary milestone has a button: "Since diagnosis"
  $("#presets").replaceChildren(...[...PRESETS, ...(pm ? [[pk, "Since " + lcLabel(pm.label)]] : [])].map(([k, l]) =>
    el("button", {type: "button", "aria-pressed": state.preset === k, text: l, title: k === pk ? "Since " + fmtD(dayTs(pm.date)) : null, onclick: () => applyPreset(k)})));
  { const pre = $("#presets"), b = pre.querySelector("[aria-pressed=true]");   // on a phone the buttons scroll: the chosen one in view
    if (b && pre.scrollWidth > pre.clientWidth) pre.scrollLeft = b.offsetLeft - pre.offsetLeft - (pre.clientWidth - b.offsetWidth) / 2; }
  const pick = $("#periodPick"), on = isMsKey(state.preset) || isYearKey(state.preset) ? state.preset : "";
  pick.replaceChildren(el("option", {value: "", text: MILESTONES.length ? "Milestone or year…" : "Year…"}),
    MILESTONES.length ? el("optgroup", {label: "Since a milestone"}, ...MILESTONES.map((m, i) => el("option", {value: "m" + i, text: `Since ${lcLabel(m.label)} (${fmtD(dayTs(m.date))})`}))) : null,
    el("optgroup", {label: "Calendar year"}, ...periodYears().map(y => el("option", {value: y, text: y}))));
  pick.value = on; pick.classList.toggle("on", !!on);
  $("#from").value = isoDay(state.from); $("#to").value = isoDay(state.to);
  $(".dates").classList.toggle("on", state.preset === "custom");
  const counts = {}; EV.forEach(e => { const c = CAT_OF[e.k]; if (c && inRange(e)) counts[c.k] = (counts[c.k] || 0) + 1; });
  $("#chips").replaceChildren(...CATS.filter(c => hasLane(c)).map(c => el("button", {class: "chip", "aria-pressed": state.cats.has(c.k), onclick: () => { state.cats.has(c.k) ? state.cats.delete(c.k) : state.cats.add(c.k); renderAll(); }},
    el("span", {class: "key", style: {background: c.color}}), `${c.label} `, el("span", {style: {color: "var(--muted)"}, text: counts[c.k] || 0}))));
}
$("#periodPick").onchange = ev => { if (ev.target.value) applyPreset(ev.target.value); };
$("#from").onchange = () => { const v = zParse($("#from").value + "T00:00"); if (!isNaN(v)) setRange(v, Math.max(state.to, v + DAY)); };
$("#to").onchange = () => { const v = zParse($("#to").value + "T23:59"); if (!isNaN(v)) setRange(Math.min(state.from, v - DAY), v); };
let qT; $("#q").oninput = () => { clearTimeout(qT); qT = setTimeout(() => { state.q = $("#q").value.trim().toLowerCase(); renderAll(); }, 150); };

const inRange = e => e.te >= state.from && e.ts <= state.to;
const matchQ = e => !state.q || e._q.includes(state.q);
const visible = e => !e.hidden && state.cats.has(CAT_OF[e.k]?.k) && inRange(e) && matchQ(e);

/* tooltip */
const tip = $("#tip");
function showTip(nodes, ev) { tip.replaceChildren(...nodes.filter(Boolean)); tip.style.display = "block"; const pad = 14, r = tip.getBoundingClientRect(); let x = ev.clientX + pad, y = ev.clientY + pad; if (x + r.width > innerWidth - 8) x = ev.clientX - r.width - pad; if (y + r.height > innerHeight - 8) y = ev.clientY - r.height - pad; tip.style.left = x + "px"; tip.style.top = y + "px"; }
function hideTip() { tip.style.display = "none"; }
function showEvTip(e, ev) {
  if (!e) return; const c = CAT_OF[e.k];
  showTip([el("div", {class: "v", text: e.title}),
    el("div", {class: "s"}, el("span", {class: "k", style: {background: c.color}}), `${KIND_LABEL[e.k]} · ${e.end && e.te - e.ts > DAY ? fmtD(e.ts) + " – " + fmtD(e.te) : fmtDT(e.ts)}`),
    e.sub ? el("div", {class: "s", text: e.sub}) : null,
    e.abn ? el("div", {class: "s", style: {color: "var(--crit)"}, text: `▲ ${e.abn} value${e.abn > 1 ? "s" : ""} out of range`}) : null], ev);
}

/* milestones: named dates (diagnosis, surgery, treatment start...) from the settings (CFG.milestones), oldest first.
   Period keys "m0", "m1"... mean "since that milestone"; the older saved key "dx" means the primary one. */
let MILESTONES = [];
const primaryMs = () => MILESTONES.find(m => m.primary) || MILESTONES[0] || null;
const msKey = m => m ? "m" + MILESTONES.indexOf(m) : "all";
const isMsKey = k => k === "dx" || /^m\d+$/.test(k || "");
const msOf = k => k === "dx" ? primaryMs() : isMsKey(k) ? MILESTONES[+k.slice(1)] || null : null;
const lcLabel = s => /^[A-Z][a-z]/.test(s) ? s[0].toLowerCase() + s.slice(1) : s;   // "Treatment start" -> "treatment start", "CAR-T" stays
/* the old tab ids (#172): Trends and Lab heatmap are the Labs tab's chart and heatmap. -> [tab, labsView] */
const TAB_ALIAS = {trends: ["labs", "chart"], heatmap: ["labs", "heatmap"]};
const tabAlias = (t, view) => TAB_ALIAS[t] || [t, view === "heatmap" ? "heatmap" : "chart"];
