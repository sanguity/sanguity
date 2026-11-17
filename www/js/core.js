/* Sanguity · settings, helpers, formatting, shared state, persistence, date range, tooltip, milestones.
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
const state = { from: 0, to: 0, min: 0, max: 0, preset: "2y", cats: new Set(CATS.map(c => c.k).filter(k => k !== "claim")), q: "", tab: "overview", marks: true, smooth: false, sel: new Set(), selEv: null, tables: new Set(), asc: false };

/* ---------------------------------------------------------------- persistence */
function save() { try { localStorage.setItem(stateKey(), JSON.stringify({preset: state.preset, from: state.from, to: state.to, cats: [...state.cats], known: CATS.map(c => c.k), tab: state.tab, sel: [...state.sel], marks: state.marks, smooth: state.smooth, asc: state.asc, v: 2})); } catch (e) {} }
function restore() {
  try {
    let raw = localStorage.getItem(stateKey());
    // the one view saved before profiles existed goes to the first profile opened after the upgrade
    if (raw == null && (raw = localStorage.getItem("ht-state")) != null) localStorage.removeItem("ht-state");
    const s = JSON.parse(raw || "null"); if (!s) return;
    if (s.cats) { const known = new Set(s.known || CATS.filter(c => c.k !== "watch").map(c => c.k)); state.cats = new Set([...s.cats, ...CATS.filter(c => !known.has(c.k) && c.k !== "claim").map(c => c.k)]); }
    if (s.sel) state.sel = new Set(s.sel);
    if (s.tab && s.v === 2) state.tab = s.tab; if (s.preset) state.preset = s.preset;
    if (typeof s.marks === "boolean") state.marks = s.marks; if (typeof s.smooth === "boolean") state.smooth = s.smooth; if (typeof s.asc === "boolean") state.asc = s.asc;
    if (s.preset === "custom" && s.from && s.to) { state.from = s.from; state.to = s.to; }
  } catch (e) {}
}
/* the ◐ toggle: this browser's own theme, over the account's default (applyTheme in util.js; theme.js applied it first) */
function setTheme(t) { try { t ? localStorage.setItem("ht-theme", t) : localStorage.removeItem("ht-theme"); } catch (e) {} applyTheme(); }
$("#theme").onclick = () => { const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches; setTheme(dark ? "light" : "dark"); };

/* ---------------------------------------------------------------- range */
const PRESETS = [["all","All"],["5y","5y"],["2y","2y"],["1y","1y"],["6m","6m"],["90d","90d"],["30d","30d"]];
function applyPreset(p) {
  state.preset = p; const end = state.max + DAY;
  const span = {"5y":1826,"2y":730,"1y":365,"6m":182,"90d":90,"30d":30}[p];
  if (p === "all") { const pad = (state.max - state.min) * .02 + DAY; state.from = state.min - pad; state.to = state.max + pad; }
  else if (span) { state.to = end; state.from = end - span * DAY; }
  renderAll();
}
function setRange(a, b, fromUser = true) {
  const minSpan = 2 * DAY; if (b - a < minSpan) { const m = (a + b) / 2; a = m - minSpan/2; b = m + minSpan/2; }
  state.from = a; state.to = b; if (fromUser) state.preset = "custom"; renderAll();
}
function renderFilters() {
  const pre = $("#presets"); pre.replaceChildren(...PRESETS.map(([k, l]) => el("button", {"aria-pressed": state.preset === k, text: l, onclick: () => applyPreset(k)})));
  $("#from").value = isoDay(state.from); $("#to").value = isoDay(state.to);
  const counts = {}; EV.forEach(e => { const c = CAT_OF[e.k]; if (c && inRange(e)) counts[c.k] = (counts[c.k] || 0) + 1; });
  $("#chips").replaceChildren(...CATS.filter(c => hasLane(c)).map(c => el("button", {class: "chip", "aria-pressed": state.cats.has(c.k), onclick: () => { state.cats.has(c.k) ? state.cats.delete(c.k) : state.cats.add(c.k); renderAll(); }},
    el("span", {class: "key", style: {background: c.color}}), `${c.label} `, el("span", {style: {color: "var(--muted)"}, text: counts[c.k] || 0}))));
}
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
const msPeriods = () => MILESTONES.map((m, i) => ["m" + i, "Since " + lcLabel(m.label)]);
function inPeriod(rows, p) {   // rows with a .d (YYYY-MM-DD) inside cost period p
  const m = msOf(p);
  return m ? rows.filter(r => r.d >= m.date) : p === "all" || isMsKey(p) ? rows : rows.filter(r => r.d.startsWith(p));
}
