/* Sanguity · tabs, render dispatch and startup (signs in, picks the profile, loads its data, settings and watch data).
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
/* ---------------------------------------------------------------- tabs & boot */
/* "health" is Body and activity (watch, sleep and smart-scale data) and "labs" is Labs, the chart and heatmap of
   the lab results (#172): Trends and Lab heatmap were two tabs, and their old ids still open Labs (TAB_ALIAS, core.js) */
const TABS = ["overview", "health", "timeline", "labs", "costs"];
/* the profile's tab layout (#40, CFG.tabs): its order, and the hidden tabs, which leave the tab bar and the arrow
   keys; asking for a hidden tab (a saved view, a link, a button elsewhere) opens the first shown one. The server
   gives a layout saved with the old tabs in the new ones (server/settings.py, tab_layout) */
let TAB_ORDER = [...TABS], TAB_HIDDEN = new Set();
const shownTabs = () => TAB_ORDER.filter(t => !TAB_HIDDEN.has(t));
function applyTabs(cfg) {
  const order = (Array.isArray(cfg && cfg.order) ? cfg.order : []).filter(t => TABS.includes(t));
  TAB_ORDER = [...new Set([...order, ...TABS])];
  TAB_HIDDEN = new Set((Array.isArray(cfg && cfg.hidden) ? cfg.hidden : []).filter(t => TABS.includes(t)));
  if (!shownTabs().length) TAB_HIDDEN.clear();
  const bar = $(".tabs");
  bar.append(...TAB_ORDER.map(t => $("#tab-" + t)));
  for (const t of TABS) $("#tab-" + t).hidden = TAB_HIDDEN.has(t);
}
/* the tab in the address (#208): index.html#overview, #health, #timeline, #labs (Labs as charts), #labs-heatmap (Labs
   as the heatmap) or #costs; the old #trends and #heatmap still open Labs as charts or the heatmap. An address with
   no # opens the tab last used (save(), core.js). tabOfHash: the address's [tab, Labs view], or null */
const HASH_TABS = {...TAB_ALIAS, labs: ["labs", "chart"], "labs-heatmap": ["labs", "heatmap"]};
const tabOfHash = () => { const h = decodeURIComponent(location.hash.slice(1)).toLowerCase(); return HASH_TABS[h] || (TABS.includes(h) ? [h] : null); };
const tabHash = () => "#" + state.tab + (state.tab === "labs" && state.labsView === "heatmap" ? "-heatmap" : "");
/* the address follows the tab shown: a new history entry when someone picks another tab (push), so Back and Forward
   move between tabs; the boot, an old alias and the app's own switches replace the entry instead. history's own
   calls fire no hashchange, so the listener below doesn't run again. At the boot, another # in the address (Add →
   Journal entry's #new-journal-entry) is left for its page to read */
function tabAddress(push) {
  const h = tabHash();
  if (location.hash === h || (!booted && location.hash.length > 1 && !tabOfHash())) return;
  try { if (push) history.pushState(null, "", h); else history.replaceState(history.state, "", h); } catch (e) {}
}
const pickTab = (t, view) => switchTab(t, view, true);   // someone chose it: a tab, a tile, a button that opens a tab
/* t: a tab, or an old id ("trends", "heatmap": the Labs tab in that view); view: Labs' "chart" or "heatmap", if given;
   push: someone picked it (pickTab), so Back returns to the tab before */
function switchTab(t, view, push) {
  if (TAB_ALIAS[t]) [t, state.labsView] = TAB_ALIAS[t];
  if (view === "chart" || view === "heatmap") state.labsView = view;
  if (!shownTabs().includes(t)) t = shownTabs()[0];
  const changed = t !== state.tab;
  if (changed && drawer.classList.contains("open")) closeDrawer();   // details belong to the tab they were opened from
  hideTip(); state.tab = t;
  { const b = $("#tab-" + t), bar = b.parentElement; if (bar.scrollWidth > bar.clientWidth) bar.scrollTo({left: bar.scrollLeft + b.getBoundingClientRect().left - bar.getBoundingClientRect().left - (bar.clientWidth - b.offsetWidth) / 2, behavior: smoothly()}); }
  for (const k of TABS) { $("#tab-" + k).setAttribute("aria-selected", t === k); $("#view-" + k).hidden = t !== k; }
  { const b = $("#tab-" + t); viewTitle([b.textContent, PROFILE && PROFILE.name].filter(Boolean).join(" · "), changed && booted && !b.parentElement.contains(document.activeElement) && b.textContent); }   // "Labs · Ben · Sanguity"; said when opened from elsewhere (#209)
  // the records bar: its period on every tab with time (#171); the search and the categories on Timeline only (Labs
  // filters its lab names in the list's own header, #192: trends.js)
  $(".filters").style.display = t === "overview" ? "none" : "";
  $(".filters").classList.toggle("period-only", t !== "timeline");
  $("#ftoggle").textContent = t !== "timeline" ? "Dates" : "Filters";   // what it opens on a phone
  $("#chips").style.display = t === "timeline" ? "" : "none";
  renderAll();
  if (changed) window.scrollTo({top: 0, left: 0, behavior: "instant"});   // each tab starts at the top
  tabAddress(push && changed);   // #208
}
for (const k of TABS) $("#tab-" + k).onclick = () => pickTab(k);
$(".tabs").addEventListener("keydown", ev => {   // left and right (Home, End) move between the shown tabs
  const shown = shownTabs(), i = shown.indexOf(ev.target.id.replace(/^tab-/, ""));
  if (i < 0) return;
  const next = {ArrowRight: shown[(i + 1) % shown.length], ArrowLeft: shown[(i + shown.length - 1) % shown.length], Home: shown[0], End: shown.at(-1)}[ev.key];
  if (!next) return;
  ev.preventDefault(); pickTab(next); $("#tab-" + next).focus();
});
let booted = false;
// Back, Forward, a link or a typed address: the tab it names (an old alias's # becomes its tab's own)
addEventListener("hashchange", () => { const t = tabOfHash(); if (t && booted) { if (t[1]) state.labsView = t[1]; switchTab(t[0]); } });
$("#optMarks").onchange = e => { state.marks = e.target.checked; renderCharts(); save(); };
$("#optSmooth").onchange = e => { state.smooth = e.target.checked; renderCharts(); save(); };
function renderAll() {
  if (!DATA) return;   // no records (an empty profile's card, #199): nothing to draw, as when the window is resized
  renderFilters();
  renderFilterSum();   // what the filters leave out, and the Filters button's count (#190, filters.js)
  $("#optMarks").checked = state.marks; $("#optSmooth").checked = state.smooth;
  if (state.tab === "timeline") { renderStats(); renderTimeline(); renderList(); }
  else if (state.tab === "labs") renderLabs();
  else if (state.tab === "costs") renderCosts();
  else if (state.tab === "health") renderHealth();
  else renderOverview();
  save();
}
/* no records to show (an empty profile, none shared, or an error): a message instead of the tabs, and none of the
   controls that would render them, since those need DATA and the tab sections this replaces. One empty state
   (emptyState, util.js: #199), or the words of an error */
function emptyCard(...kids) {
  $(".tabs").hidden = true; $(".filters").hidden = true;
  const one = kids.length === 1 && kids[0].nodeType ? kids[0] : el("div", {class: "card"}, ...kids);
  one.classList.add("empty");
  document.querySelector("main").replaceChildren(one);
}
/* another profile: the page again (nav.js remembers the choice first), on the tab that profile last showed here, so
   the address loses its tab's # (#208) */
function renderProfileMenu() {
  $("#profile").onchange = () => { try { history.replaceState(history.state, "", location.pathname + location.search); } catch (e) {} location.reload(); };
}
$("#logout").onclick = async () => { try { await api("logout", {method: "POST"}); } catch (e) {} location.replace("login.html"); };
/* a connected source that stopped syncing (#102): one line under the header, linking to it. Owners and editors can
   fix it, so theirs is a warning; viewers get a quieter note that the records may be behind */
async function syncWarning(pid) {
  let d;
  try { const r = await api(`profiles/${pid}/connections/warnings`); if (!r.ok) return; d = await r.json(); } catch (e) { return; }
  const w = d.warnings, box = $("#syncWarn");
  if (!w.length) { box.hidden = true; return; }
  const more = w.length > 1 ? ` (and ${w.length - 1} more)` : "";
  const link = el("a", {href: `sources.html?profile=${pid}#conn-${w[0].connection}`, text: d.can_fix ? "Portals and health plans →" : "Details →"});
  box.classList.toggle("quiet", !d.can_fix);
  box.replaceChildren(d.can_fix ? el("span", {text: "⚠ " + w[0].text + more}) :
    el("span", {text: `Records from ${w[0].source} may be out of date: ${w[0].text.replace(/^[^:]*: /, "")}${more}`}), link);
  box.hidden = false;
}
(async function boot() {
  const dates = datesReady();   // this person's time zone and date format (dates.js), read alongside the rest
  try {
    const m = await api("me");
    if (m.status === 401) { const s = await api("setup").then(r => r.json()).catch(() => ({})); location.replace(s.needed ? "setup.html" : "login.html"); return; }
    if (!m.ok) throw new Error(m.status + " " + m.statusText);
    ME = await m.json();
    if (ME.user.must_change_pw) { location.replace("account.html#password"); return; }
    if (ME.user.needs_two_factor) { location.replace("account.html#twofa"); return; }
    $("#who").textContent = ME.user.username;
    const canImport = p => p.role === "owner" || p.role === "editor";
    $("#importLink").hidden = !ME.profiles.some(canImport);
    // the first-run guide and the empty states (#199): with no profile, make one (guide.html?new)
    if (!ME.profiles.length) { emptyCard(emptyState(`No health profile is shared with you yet: make one of your own, or ask a profile's owner to share theirs with ${ME.user.username}.`, {href: "guide.html?new", text: "Make a profile"})); return; }
    // the top bar's picker (nav.js, #170): the profile the address names, else this browser's last, else the first
    PROFILE = ME.profiles.find(p => p.id === SgNav.profiles(ME.profiles));
    hLoad();
    renderProfileMenu();
    syncWarning(PROFILE.id);
    testPagesLoad();   // "What is this test?" links (test-pages.js, #163), alongside the records
    $("#settingsLink").href = `settings.html?profile=${PROFILE.id}`;
    const r = await api(`profiles/${PROFILE.id}/data`);
    if (r.status === 401) { location.replace("login.html"); return; }
    if (r.status >= 500) { const j = await r.json().catch(() => ({})); throw new Error(j.detail || r.status + " " + r.statusText); }
    if (r.status === 204) {   // no records yet (#199): the getting-started guide, opened by itself the first time
      if (PROFILE.role === "owner" && ME.profiles.length === 1) {   // your own profile, and your only one
        const u = await api("settings/user").then(x => x.ok ? x.json() : {}).catch(() => ({}));
        if (u.guide === "") { location.replace(`guide.html?profile=${PROFILE.id}`); return; }   // not finished or skipped yet
      }
      emptyCard(canImport(PROFILE)
        ? emptyState(`${PROFILE.name} has no records yet: lab results, visits, medications and the rest appear here once they're brought in.`,
            {href: `guide.html?profile=${PROFILE.id}`, text: "Get started"})
        : emptyState(`${PROFILE.name} has no records yet: its owners and editors bring them in, and they appear here for you too.`,
            {href: `sharing.html?profile=${PROFILE.id}#people`, text: "See who has access"}));
      return;
    }
    if (!r.ok) throw new Error(r.status + " " + r.statusText);
    DATA = await r.json();
    // this profile's settings (changed on the Settings page); if they can't be loaded the defaults are used
    try {
      const s = await api(`profiles/${PROFILE.id}/settings`);
      if (s.ok) applySettings(await s.json());
    } catch (e) { console.warn("settings ignored:", e.message); }
    // milestones are a setting since #39 (the server moved those of data.json and corrections.json in once)
    MILESTONES = CFG.milestones.filter(m => m && /^\d{4}-\d{2}-\d{2}/.test(m.date || ""))
      .map(m => ({...m, date: m.date.slice(0, 10), label: m.label || "Milestone"})).sort((a, b) => a.date.localeCompare(b.date));
  } catch (e) { emptyCard("Couldn't load the records: " + e.message); return; }
  applyTabs(CFG.tabs);
  applyTheme(CFG.theme);   // this user's default theme (#45), unless this browser has its own (the ◐ toggle)
  await dates;   // before any time is read: one without an offset is this person's wall-clock time
  for (const e of DATA.events) {
    e.ts = zParse(e.t); e.te = e.end ? zParse(e.end) : e.ts;
    e._q = (e.title + " " + (e.sub || "") + " " + (KIND_LABEL[e.k] || "")).toLowerCase();
    byId.set(e.id, e);
    if (e.k === "lab") { const d = DATA.details[e.id]; e.n = d ? d.ids.length : 1; }
  }
  EV = DATA.events.filter(e => e.k !== "stay" ? true : true);
  STAYS = DATA.events.filter(e => e.k === "stay");
  // let search hit lab-day events through their child results' names
  for (const e of DATA.events) if (e.k === "lab") { const d = DATA.details[e.id]; if (d) e._q += " " + d.ids.map(i => (byId.get(i)?.title || "")).join(" ").toLowerCase(); }
  for (const s of DATA.labs) for (const p of s.pts) p.ts = zParse(p[0]);
  // #44: results with no range of their own (ranges.js). Before the units, since an override is stated in the
  // reported unit and is then converted with the values (#41)
  RANGES_USED = applyRangeOverrides(DATA.labs, rangeConfig(CFG));   // with About this person's birth date and sex (#194)
  applyUnits(DATA.labs); applyUnitsToRows(DATA.labs, DATA.details);   // this person's units, exact conversions only (units.js, #41)
  SERIES = joinSeries(DATA.labs);   // one chart per test, whichever imports brought it (same.js)
  if (RANGES_USED) for (const n of document.querySelectorAll(".range-note")) n.textContent += RANGE_NOTE_SETTINGS;
  // optional: watch data from the Health Connect receiver
  try {
    const w = await api(`profiles/${PROFILE.id}/watch`);
    if (w.status === 200) WATCH = await w.json();
  } catch (e) {}
  if (WATCH) {
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
    wid = ecgLoad(wid);   // ECGs and irregular rhythm notifications (ecg.js, #182)
    EV = DATA.events;
    applyUnits(SERIES.filter(s => s.watch));   // after the clinic weights joined the scale's (#41)
  }
  journalLoad();   // the journal's entries join EV as events of kind "journal" (journal.js)
  await cycleLoad();   // cycle tracking, only when turned on and for owners and editors (cycle.js, #183)
  medsLoad();      // each medication's spans and fills (meds.js, #50)
  medLogLoad();    // the medication log: doses typed in and the phone's (medlog.js, #235)
  const ts = DATA.events.map(e => e.ts).filter(isFinite); state.min = Math.min(...ts); state.max = Math.max(...ts);
  for (const s of SERIES) if (s.watch && s.pts.length) state.max = Math.max(state.max, s.pts[s.pts.length - 1].ts);
  $("#meta").textContent = [DATA.source, DATA.built && "built " + fmtD(DATA.built), WATCH && WATCH.last_payload && "watch synced " + fmtDT(WATCH.last_payload)]
    .filter(Boolean).join(" · ");   // a profile of imports alone has no build to name (#199: it said "undefined · built")
  restore();   // a saved period naming a milestone that's since gone is All (setPeriod)
  { const t = tabOfHash(); if (t) { state.tab = t[0]; if (t[1]) state.labsView = t[1]; } }
  if (!state.sel.size) state.sel = startLabs();   // the default saved view, else trends.defaultLabs (trends.js)
  if (state.preset !== "custom" || !(state.to > state.from)) setPeriod(state.preset);
  await testPagesLoad();   // before the first draw, so the links are there (#163)
  switchTab(state.tab);
  booted = true;
  try { performance.mark("sanguity:ready"); } catch (e) {}   // the records page is ready: what docs/performance.md times (#201)
  drawerResume();   // after an edit's reload (drawer.js)
  journalResume();  // after a journal entry was saved (journal.js)
  medsResume();     // after a medication reminder was saved (meds.js, #162)
})();
