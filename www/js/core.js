/* Sanguity · settings, helpers, formatting, shared state, persistence, the records bar's period, tooltip, milestones.
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
const CATS = [
  {k:"stay",      label:"Hospital stays",   color:"var(--stay-line)", kinds:["stay"]},
  {k:"visit",     label:"Visits",           color:"var(--c-visit)",   kinds:["visit"]},
  {k:"contact",   label:"Chart activity",   color:"var(--c-contact)", kinds:["contact"]},   // chart reviews, calls, faxes (#236)
  {k:"lab",       label:"Labs",             color:"var(--c-lab)",     kinds:["lab"]},
  {k:"imaging",   label:"Imaging",          color:"var(--c-imaging)", kinds:["imaging"]},
  {k:"procedure", label:"Procedures",       color:"var(--c-procedure)",kinds:["procedure"]},
  {k:"immunization", label:"Immunizations", color:"var(--c-immunization)", kinds:["immunization"]},   // #179: vaccines given
  {k:"study",     label:"Reports & studies",color:"var(--c-study)",   kinds:["study"]},
  {k:"message",   label:"Messages",         color:"var(--c-message)", kinds:["message","appt_msg","auto_msg"]},
  {k:"letter",    label:"Letters & docs",   color:"var(--c-letter)",  kinds:["letter","document"]},
  {k:"med",       label:"Medications",      color:"var(--muted)",     kinds:["med","rx"]},
  {k:"claim",     label:"Insurance claims", color:"var(--axis)",      kinds:["claim"]},
  {k:"watch",     label:"Watch",            color:"var(--ink2)",      kinds:["exercise"]},
  {k:"ecg",       label:"ECG & rhythm",     color:"var(--c-ecg)",     kinds:["ecg","rhythm"]},   // #182: a watch's ECGs and irregular rhythm notifications (ecg.js)
  {k:"journal",   label:"Journal",          color:"var(--c-journal)", kinds:["journal"]},   // the person's own entries (journal.js)
  {k:"mood",      label:"Mood & energy",    color:"var(--c-journal)", kinds:["mood"]},      // #161: a day's mood and energy, written in the journal
  {k:"cycle",     label:"Cycle tracking",   color:"var(--c-cycle)",   kinds:["cycle"]},     // #183: only when its owner turned it on (cycle.js)
];
const CAT_OF = {}; CATS.forEach(c => c.kinds.forEach(k => CAT_OF[k] = c));
// KIND_LABEL, each kind's name: util.js (#463: the command palette's, on every page)
/* each kind's icon (#354, Lucide's, in icons/sprite.svg): one map, for the Timeline's chips and rows and for what
   shows a kind next (the drawer, the charts). A category's chip takes its key's (CATS[].k): its first kind's, or the
   watch's own. */
const KIND_ICON = {stay: "bed", visit: "stethoscope", contact: "clipboard", lab: "test-tube-diagonal", labitem: "test-tube-diagonal",
  imaging: "scan-line", procedure: "bandage", immunization: "syringe", study: "microscope", message: "message-square",
  appt_msg: "message-square", auto_msg: "message-square", letter: "mail", document: "file-text", med: "pill", rx: "pill",
  claim: "banknote", watch: "watch", exercise: "watch", ecg: "activity", rhythm: "activity", journal: "notebook", mood: "smile",
  cycle: "droplet"};
/* a kind's icon, hidden from assistive technology beside the kind's name (util.js icon()); cls: a class of its own */
const kindIcon = (k, cls) => {
  const i = icon(KIND_ICON[k] || KIND_ICON[CAT_OF[k]?.k] || "file-text");
  if (cls) i.classList.add(cls);
  return i;
};
const usd = v => (v || 0).toLocaleString(undefined, {style: "currency", currency: "USD", maximumFractionDigits: 0});
/* The profile's settings (server/settings.py), changed on the Settings page. The server sends every one, with its
   defaults filled in; these are only what the page uses if that fails, and should match the server's defaults. */
const DEFAULT_SETTINGS = {
  overview: {keyLabs: ["Hemoglobin", "Platelet Count", "WBC", "Automated Abs Neutrophil Cnt", "Lymphocytes Absolute", "Creatinine",
    "ALT", "AST", "Total Bilirubin", "Potassium, Blood", "Sodium, Blood", "Magnesium"],
    homeTiles: []},    // #225: "bp", "glucose", "temp" as tiles after the key labs (overview.js)
  trends: {defaultLabs: ["Hemoglobin", "Platelet Count", "WBC", "Automated Abs Neutrophil Cnt", "Creatinine", "ALT"],
    views: []},        // #37, saved views: [{name, labs, default}] (trends.js)
  labNicknames: {"Automated Abs Neutrophil Cnt": "Neutrophils (absolute)", "Lymphocytes Absolute": "Lymphocytes (absolute)", "Platelet Count": "Platelets",
    "Potassium, Blood": "Potassium", "Sodium, Blood": "Sodium", "Total Bilirubin": "Bilirubin, total"},
  tabs: {order: ["overview", "health", "medications", "timeline", "labs", "costs"], hidden: []},   // #40, applied by app.js
  theme: "system",     // #45, this user's default: "light", "dark" or "system" (the light/dark toggle overrides it per browser)
  sources: {},         // short source tags on lab results -> display names, e.g. {"EXT": "Outside lab"}
  units: {lab: "reported", weight: "reported", temperature: "reported", height: "reported"},   // #41, this user's (units.js)
  person: {birthDate: "", sex: "", heights: []},   // #194, About this person (person.js has the helpers)
  visitWorkouts: [],   // past visits whose title matches count as workouts on Health, e.g. {"match": "physical therapy", "regex": false, "name": "Physical therapy", "minutes": 45}
  milestones: [],      // named dates, e.g. {"date": "2026-03-01", "label": "Diagnosis", "primary": true} (#39)
};
// CFG, the settings in use, and applySettings(): store.js (#271)
const srcName = tag => CFG.sources[tag] || tag;
const DAY = 864e5;

// fmtD, fmtDT, fmtMonth, isoDay, dayTs, zParse, zdate: dates.js, in this person's own time zone and date format (#42)
const fmtNum = v => Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? (+v.toFixed(1)).toString() : (+v.toFixed(2)).toString();

/* ---------------------------------------------------------------- the account and profile being viewed (api() is in util.js) */
let ME = null, PROFILE = null;   // the signed-in account and the profile being viewed
const stateKey = () => "ht-state" + (PROFILE ? ":" + PROFILE.id : "");

// DATA, EV, STAYS, SERIES, byId, WATCH, STEPS: the records and what's worked out from them, in store.js (#271)
const hasLane = c => EV.some(e => CAT_OF[e.k] === c) || (c.k === "watch" && STEPS.length > 0);
/* preset: the records bar's period (#171), shared by every tab with time ("period" below); labsView: the Labs tab's
   "chart" or "heatmap" (#172); tlView: the Timeline tab's "timeline" or "journal", and tlWork: its list has the
   workouts too (#277, timeline.js) */
const state = { from: 0, to: 0, min: 0, max: 0, preset: "2y", cats: new Set(CATS.map(c => c.k).filter(k => k !== "claim")), q: "", tab: "overview", labsView: "chart", tlView: "timeline", tlWork: false, marks: true, smooth: false, sel: new Set(), selEv: null, tables: new Set(), asc: false };

/* ---------------------------------------------------------------- persistence */
function save() { try { localStorage.setItem(stateKey(), JSON.stringify({preset: state.preset, from: state.from, to: state.to, cats: [...state.cats], known: CATS.map(c => c.k), tab: state.tab, labsView: state.labsView, tlView: state.tlView, costView: typeof costState === "object" ? costState.view : undefined, tlWork: state.tlWork, sel: [...state.sel], marks: state.marks, smooth: state.smooth, asc: state.asc, v: 2})); } catch (e) {} }
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
    if (s.tlView === "journal") state.tlView = "journal";
    if (s.costView === "paid" && typeof costState === "object") costState.view = "paid";   // Costs' What you paid (#452), as Labs' and the Timeline's views
    if (typeof s.tlWork === "boolean") state.tlWork = s.tlWork;
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
let lastPeriod = state.preset;   // the last period that wasn't custom dates: where "Clear dates" goes back to (#191)
/* the period's dates in state, drawing nothing (the tab switched to next draws it) */
function setPeriod(p) {
  if (p === "dx") p = msKey(primaryMs());   // the older saved key for the primary milestone ("all" if there's none)
  const end = state.max + DAY, m = msOf(p);
  if (!(p in PRESET_DAYS) && p !== "all" && !m && !isYearKey(p)) p = "all";   // e.g. a milestone since deleted
  state.preset = lastPeriod = p;
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
/* ---- the period's controls (#191): preset buttons (All, 5y ... 30d, and "Since <the primary milestone>"), one More…
   menu (the other milestones, the calendar years, Custom dates…), the period in words beside them, and the date boxes
   only for custom dates (typed in, or a chart dragged or zoomed), with "Clear dates" back to the period before. One
   control shows as chosen: a preset button, or More… naming what it picked. */
/* the period in words, beside the controls: "Last 2 years", "Apr 14, 2025 – today" (since a milestone), "2025" (a year) */
function periodWords() {
  const p = state.preset, m = msOf(p);
  if (m) return `${fmtD(dayTs(m.date))} – today`;
  if (isYearKey(p)) return p;
  if (p === "custom") return `${fmtD(state.from)} – ${isoDay(state.to) >= zToday() ? "today" : fmtD(state.to)}`;
  return p === "all" ? "Everything on file" : (PRESET_TEXT[p] || "").replace(/^the l/, "L");
}
const moreBtn = $("#periodMoreBtn"), moreList = $("#periodMoreList");
function renderPeriod() {
  const p = state.preset, pm = primaryMs(), pk = pm ? msKey(pm) : null;   // the primary milestone has a button: "Since diagnosis"
  $("#presets").replaceChildren(...[...PRESETS, ...(pm ? [[pk, "Since " + lcLabel(pm.label)]] : [])].map(([k, l]) =>
    el("button", {type: "button", "aria-pressed": p === k, text: l, "data-tip": k === pk ? "Since " + fmtD(dayTs(pm.date)) : null, onclick: () => applyPreset(k)})));
  const item = (k, text, note) => el("button", {type: "button", class: "menu-item", role: "menuitemradio", tabindex: "-1", "aria-checked": p === k, "data-period": k},
    icon("check"), text, note ? el("span", {class: "menu-note", text: note}) : null);
  const group = (label, items) => items.length ? el("div", {role: "group", "aria-label": label}, el("div", {class: "menu-head", "aria-hidden": "true", text: label}), ...items) : null;
  moreList.replaceChildren(...present(   // a group with nothing in it is left out
    group("Since a milestone", MILESTONES.filter(m => m !== pm).map(m => item(msKey(m), "Since " + lcLabel(m.label), fmtD(dayTs(m.date))))),
    group("Calendar year", periodYears().map(y => item(y, y))),
    el("div", {class: "menu-sep", role: "separator"}), item("custom", "Custom dates…")));
  const m = p !== pk && msOf(p), picked = m ? "Since " + lcLabel(m.label) : isYearKey(p) ? p : p === "custom" ? "Custom dates" : "";
  moreBtn.replaceChildren(picked || "More…", el("span", {class: "menu-caret", "aria-hidden": "true"}, icon("chevron-down")));
  moreBtn.classList.toggle("on", !!picked);
  moreBtn.setAttribute("aria-label", picked ? `Period: ${picked}. More periods` : "More periods");
  $("#periodWords").textContent = periodWords();
  $("#periodWords").hidden = p === "custom";   // the date boxes say it
  $("#dates").hidden = p !== "custom" && !PHONE.matches;   // in the phone's sheet they're always there
  $("#datesClear").hidden = p !== "custom";
  // on a phone (#269): the pill names the period, and its sheet lists every one
  const pm2 = msOf(p);
  pill.replaceChildren(el("span", {class: "vh", text: "Period: "}), pm2 ? "Since " + lcLabel(pm2.label) : periodWords(),
    el("span", {class: "menu-caret", "aria-hidden": "true"}, icon("chevron-down")));
  pillList.replaceChildren(...present(
    ...PRESETS.map(([k]) => item(k, k === "all" ? "Everything on file" : PRESET_TEXT[k].replace(/^the l/, "L"))),
    group("Since a milestone", MILESTONES.map(m => item(msKey(m), "Since " + lcLabel(m.label), fmtD(dayTs(m.date))))),
    group("Calendar year", periodYears().map(y => item(y, y)))));
  $("#from").value = isoDay(state.from); $("#to").value = isoDay(state.to);
  const lp = lastPeriod, lm = msOf(lp);
  $("#datesClear").dataset.tip = "Back to " + (lp === "all" ? "everything on file" : lm ? "since " + lcLabel(lm.label) : PRESET_TEXT[lp] || lp);
}
SgNav.menu({btn: moreBtn, list: moreList, wrap: $("#periodMore"), onOpen() {   // nav.js: a popover, kept on the screen by app.css
  const on = moreList.querySelector("[aria-checked=true]");
  if (on) moreList.scrollTop = on.offsetTop - moreList.clientHeight / 2;
}});
moreList.addEventListener("click", ev => {
  const b = ev.target.closest("[data-period]"); if (!b) return;
  if (b.dataset.period !== "custom") { applyPreset(b.dataset.period); moreBtn.focus(); return; }
  state.preset = "custom"; renderAll(); $("#from").focus();   // the period's dates, to change
});
$("#datesClear").onclick = () => {
  applyPreset(lastPeriod);
  (PHONE.matches ? pillList.querySelector("[aria-checked=true]") || pill : $("#presets [aria-pressed=true]") || moreBtn).focus();
};
/* ---- on a phone (#269, nav.js's sheets): the period is one pill, "Last 2 years ▾", whose sheet lists every period
   (the presets, each milestone, the calendar years: a menu, picked by touch or keyboard as More… is) with the date boxes
   for custom dates under it, moved there from the bar; and on the Timeline, the Events card's Filters (#277) opens a
   sheet of its own: the search (moved there too), each kind with records as a checkbox (its icon, name and count,
   renderKinds) and Reset. */
const pill = $("#periodPill"), pillList = $("#periodPillList");
const datesBox = el("section", {class: "sheet-dates", "aria-labelledby": "datesHd"}, el("h3", {id: "datesHd", text: "Custom dates"}));
menuSheet(SgNav.menu({btn: pill, list: pillList}), "Period", datesBox);
pillList.addEventListener("click", ev => { const b = ev.target.closest("[data-period]"); if (b) applyPreset(b.dataset.period); });   // the sheet closes (nav.js)
const kindChecks = el("div", {class: "kind-checks", role: "group", "aria-labelledby": "kindChecksHd"});
const qSlot = el("div", {class: "sheet-q"});
const fSheet = phoneSheet("Filters", el("div", {class: "sheet-body"}, qSlot, el("h3", {id: "kindChecksHd", text: "Kinds of records"}), kindChecks),
  el("div", {class: "sheet-foot"}, el("button", {class: "btn", type: "button", text: "Reset filters", onclick() { resetTimelineFilters(); announce("Filters reset."); }}),
    el("button", {class: "btn primary", type: "button", text: "Done", onclick: () => fSheet.close()})));
$("#ftoggle").onclick = () => { openDialog(fSheet, {as: "sheet"}); $("#ftoggle").setAttribute("aria-expanded", "true"); };
fSheet.addEventListener("close", () => $("#ftoggle").setAttribute("aria-expanded", "false"));
/* the date boxes and the search: in the sheets on a phone; wider, the dates in the records bar and the search after the
   kinds, in the Events card's header (#277) */
function barOrSheet() {
  if (PHONE.matches) { datesBox.append($("#dates")); qSlot.append($("#q")); }
  else { $("#periodWords").after($("#dates")); $("#chips").after($("#q")); fSheet.close(); }
  if (typeof booted !== "undefined" && booted) renderPeriod();
}
PHONE.addEventListener("change", barOrSheet);
barOrSheet();
function renderFilters() {
  renderPeriod();
  renderKinds();
}
/* ---------------------------------------------------------------- the Timeline's kinds (#189)
   In the Events card's header with the search (#277: they were in the records bar on every tab, and filter only the
   Timeline's chart and list). Each kind with records is an on/off chip (#193, app.css .toggle) in the kind's colour, its name saying the state
   ("Visits, 31, shown"). Beside it a "⋯" menu button: "Show only Visits", and "Show all" while any kind is hidden.
   Right-click on a chip, or the keyboard's menu key, opens the same menu (and a long press, on phones that send one
   as a right-click). Shown kinds: state.cats, kept by save(). The "Show all" button that followed the chips while any
   was hidden gave way to the filters' line under them and its Reset filters (#190, filters.js). */
/* every kind with records shown (only == null), or only that one */
function showKinds(only) {
  $("#chips .menu-list:popover-open")?.hidePopover();   // focus back on its ⋯ (nav.js), kept there once redrawn
  for (const c of CATS.filter(hasLane)) if (only == null || c.k === only) state.cats.add(c.k); else state.cats.delete(c.k);
  renderAll();
  announce(only == null ? "Showing every kind" : `Showing only ${CATS.find(c => c.k === only).label}`);
}
function renderKinds() {
  const box = $("#chips"), was = box.contains(document.activeElement) ? document.activeElement : null;
  const counts = {}; EV.forEach(e => { const c = CAT_OF[e.k]; if (c && inRange(e)) counts[c.k] = (counts[c.k] || 0) + 1; });
  const kinds = CATS.filter(hasLane), hidden = kinds.some(c => !state.cats.has(c.k));
  const item = (k, text, run) => el("button", {type: "button", class: "menu-item", role: "menuitem", tabindex: "-1", "data-k": k, text, onclick: run});
  const checks = [], had = kindChecks.contains(document.activeElement) && document.activeElement.dataset.k;
  box.replaceChildren(...kinds.map(c => {
    const on = state.cats.has(c.k), n = counts[c.k] || 0, id = "kindMenu-" + c.k;
    const chip = el("button", {type: "button", class: "toggle", "data-k": c.k, "aria-pressed": on, "aria-label": `${c.label}, ${n}, ${on ? "shown" : "hidden"}`,
      onclick: () => { on ? state.cats.delete(c.k) : state.cats.add(c.k); renderAll(); }},
      kindIcon(c.k, "key"), el("span", {class: "l", "data-l": `${c.label}\u2002${n}`}, el("span", {}, `${c.label}\u2002`, el("span", {class: "n", text: n}))));   // its icon, in its colour: the chart's key (#354); the words, as wide as app.css's .l says (#434)
    const btn = el("button", {type: "button", class: "kind-more", "data-k": c.k, "aria-label": `More for ${c.label}`,
      "data-tip": `Show only ${c.label}${hidden ? ", or show all" : ""}`, "aria-haspopup": "menu", "aria-expanded": "false", "aria-controls": id}, icon("ellipsis"));
    const items = [item(c.k, `Show only ${c.label}`, () => showKinds(c.k)), hidden ? item(c.k, "Show all", () => showKinds()) : null].filter(Boolean);
    const list = el("div", {class: "menu-list", id, role: "menu", "aria-label": c.label}, ...items);
    const m = SgNav.menu({btn, list});   // nav.js: a popover, kept on the screen by app.css
    chip.oncontextmenu = ev => { ev.preventDefault(); m.open(); items[0].focus(); };
    const check = el("label", {class: "kind-check"}, el("input", {type: "checkbox", "data-k": c.k, checked: on,
      onchange: () => { on ? state.cats.delete(c.k) : state.cats.add(c.k); renderAll(); }}), kindIcon(c.k, "key"), c.label, el("span", {class: "n", text: n}));
    checks.push(check);   // the phone's Filters sheet (#269)
    for (const x of [chip, btn, check]) x.style.setProperty("--tone", c.color);
    return el("span", {class: "kind"}, chip, btn, list);
  }));
  kindChecks.replaceChildren(...checks);
  if (had) kindChecks.querySelector(`[data-k="${had}"]`)?.focus();
  // a chip or its menu had focus: the same one keeps it once redrawn (or the first chip, if its kind went)
  if (was) (box.querySelector(`${was.classList.contains("toggle") ? ".toggle" : ".kind-more"}[data-k="${was.dataset.k}"]`) || box.querySelector(".toggle"))?.focus();
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
addEventListener("keydown", e => { if (e.key === "Escape") hideTip(); });   // a hover's readout goes with Escape, the pointer left where it is (1.4.13, #202)
function showEvTip(e, ev) {
  if (!e) return; const c = CAT_OF[e.k], abn = tlAbn(e);   // a lab draw's, under a search its tests' (timeline.js, #464)
  showTip([el("div", {class: "v", text: e.title}),
    el("div", {class: "s"}, el("span", {class: "k", style: {background: c.color}}), `${KIND_LABEL[e.k]} · ${e.end && e.te - e.ts > DAY ? fmtD(e.ts) + " – " + fmtD(e.te) : fmtDT(e.ts)}`),
    e.sub ? el("div", {class: "s", text: e.sub}) : null,
    abn ? el("div", {class: "s", style: {color: "var(--crit)"}, text: `▲ ${abn} value${abn > 1 ? "s" : ""} out of range`}) : null], ev);
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
