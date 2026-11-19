/* Sanguity · tabs, render dispatch and startup (signs in, picks the profile, loads its data, settings and watch data).
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
/* ---------------------------------------------------------------- tabs & boot */
/* "health" is Body and activity (watch, sleep and smart-scale data) and "labs" is Labs, the chart and heatmap of
   the lab results (#172): Trends and Lab heatmap were two tabs, and their old ids still open Labs (TAB_ALIAS, core.js) */
const TABS = SgNav.TABS;   // the top bar's links to them (nav.js, #264)
/* the profile's tab layout (#40, CFG.tabs): its order, and the hidden tabs, which leave the top bar (nav.js puts
   them in it); asking for a hidden tab (a saved view, a link, a button elsewhere) opens the first shown one. The
   server gives a layout saved with the old tabs in the new ones (server/settings.py, tab_layout) */
let TAB_ORDER = [...TABS], TAB_HIDDEN = new Set();
const shownTabs = () => TAB_ORDER.filter(t => !TAB_HIDDEN.has(t));
function applyTabs(cfg) { ({order: TAB_ORDER, hidden: TAB_HIDDEN} = SgNav.layout(cfg)); }
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
  for (const k of TABS) { $("#tab-" + k).setAttribute("aria-current", t === k ? "page" : "false"); $("#view-" + k).hidden = t !== k; }   // the top bar's link (nav.js, #264)
  SgNav.fit();   // kept in sight: out from under More if it was there (#265)
  // on a phone the tabs are a row that scrolls sideways: the one shown in the middle of it
  { const b = $("#tab-" + t), bar = $(".tabs"); if (b.parentElement === bar && bar.scrollWidth > bar.clientWidth) bar.scrollTo({left: bar.scrollLeft + b.getBoundingClientRect().left - bar.getBoundingClientRect().left - (bar.clientWidth - b.offsetWidth) / 2, behavior: smoothly()}); }
  { const b = $("#tab-" + t); $("#tabTitle").textContent = b.textContent; viewTitle([b.textContent, PROFILE && PROFILE.name].filter(Boolean).join(" · "), changed && booted && !$(".tabs").contains(document.activeElement) && b.textContent); }   // "Labs · Ben · Sanguity"; said when opened from elsewhere (#209)
  // the records bar: its period on every tab with time (#171); the search and the categories on Timeline only (Labs
  // filters its lab names in the list's own header, #192: trends.js)
  $(".filters").style.display = t === "overview" ? "none" : "";
  $(".filters").classList.toggle("period-only", t !== "timeline");
  $("#chips").style.display = t === "timeline" ? "" : "none";
  renderAll();
  if (changed) window.scrollTo({top: 0, left: 0, behavior: "instant"});   // each tab starts at the top
  tabAddress(push && changed);   // #208
}
/* the tabs are links (#264): Tab reaches each, Enter or a click opens it here, Labs as it was last shown; one opened in
   a new window (Ctrl, ⌘ or Shift) is the browser's. One picked under More (#265) comes into the bar, with focus. Where
   the browser has them, and less motion isn't asked for, the page changes as a View Transition */
for (const k of TABS) $("#tab-" + k).addEventListener("click", ev => {
  if (ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey || ev.button) return;
  ev.preventDefault();
  const fromMore = !!ev.currentTarget.closest("[role=menu]");
  const go = () => { pickTab(k); if (fromMore) $("#tab-" + k).focus({preventScroll: true}); };
  if (k === state.tab || !document.startViewTransition || matchMedia("(prefers-reduced-motion: reduce)").matches) go();
  else document.startViewTransition(go);
});
let booted = false;
// Back, Forward, a link or a typed address: the tab it names (an old alias's # becomes its tab's own)
addEventListener("hashchange", () => {
  if (!booted) return;
  if (location.hash === "#log-dose") return logDose();
  const t = tabOfHash();
  if (t) { if (t[1]) state.labsView = t[1]; switchTab(t[0]); }
});
/* Add → Log a dose (#264): the Overview's "Today's medication doses" (medlog.js), its first button focused; with none
   to log (no current medication, no records yet, or a profile you can only view: an address typed in), a toast says
   why. Add offers it only for a profile with a current medication (doseOffer, #401) */
function logDose() {
  try { history.replaceState(history.state, "", location.pathname + location.search + (DATA ? "#overview" : "")); } catch (e) {}
  if (DATA) switchTab("overview");
  const card = $("#ovDoses");
  if (!card) {
    toast(!canEdit() ? `You can view ${PROFILE.name}: its owners and editors log its doses.`
      : `${PROFILE.name} has no current medication to log a dose for.`);
    return;
  }
  card.scrollIntoView({block: "start"});
  (card.querySelector(".dose-quick button") || card.querySelector("button")).focus({preventScroll: true});
}
const doseOffer = () => $("#doseLink").toggleAttribute("data-none", !DATA || !MEDS.some(mrCurrent));   // nav.js hides it then (#401)
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
/* the parts of the records each tab shows (store.js, #271): when one changes (a dose logged, an entry or an edit
   saved), the tab shown is drawn again in place, its scroll and focus kept; the others are drawn when they're opened */
const TAB_SHOWS = {overview: ["events", "labs", "medications"], health: ["events"], timeline: ["events", "medications", "journal"],
  labs: ["events", "labs", "medications", "journal"], costs: ["events", "costs"]};
storeOn(STORE_PARTS, parts => {
  if (parts.has("medications")) doseOffer();
  if (booted && TAB_SHOWS[state.tab].some(p => parts.has(p))) keepFocus(renderAll);
});
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
$("#logout").onclick = logOut;   // util.js: asks first about what isn't saved (#391)
/* a connected source that stopped syncing (#102): one line under the header, linking to it. Owners and editors can
   fix it, so theirs is a warning; viewers get a quieter note that the records may be behind */
async function syncWarning(pid) {
  let d;
  try { const r = await api(`profiles/${pid}/connections/warnings`); if (!r.ok) return; d = await r.json(); } catch (e) { return; }
  const w = d.warnings, box = $("#syncWarn");
  if (!w.length) { box.hidden = true; return; }
  const more = w.length > 1 ? ` (and ${w.length - 1} more)` : "";
  const link = el("a", {href: `sources.html?profile=${pid}#conn-${w[0].connection}`}, d.can_fix ? "Portals and health plans " : "Details ", icon("arrow-right"));
  box.classList.toggle("quiet", !d.can_fix);
  box.replaceChildren(d.can_fix ? el("span", {}, icon("triangle-alert", {label: "Warning:"}), " " + w[0].text + more) :
    el("span", {text: `Records from ${w[0].source} may be out of date: ${w[0].text.replace(/^[^:]*: /, "")}${more}`}), link);
  box.hidden = false;
}
(async function boot() {
  const dates = datesReady();   // this person's time zone and date format (dates.js), read alongside the rest
  let data;
  try {
    const m = await api("me");
    if (m.status === 401) { const s = await api("setup").then(r => r.json()).catch(() => ({})); location.replace(s.needed ? "setup.html" : "login.html"); return; }
    if (!m.ok) throw new Error(m.status + " " + m.statusText);
    ME = await m.json();
    if (ME.user.must_change_pw) { location.replace("account.html#password"); return; }
    if (ME.user.needs_two_factor) { location.replace("account.html#twofa"); return; }
    $("#who").textContent = ME.user.username;
    const canImport = p => p.role === "owner" || p.role === "editor";
    doseOffer();   // none until the records are here (#401)
    // the first-run guide and the empty states (#199): with no profile, make one (guide.html?new)
    if (!ME.profiles.length) { emptyCard(emptyState(`No health profile is shared with you yet: make one of your own, or ask a profile's owner to share theirs with ${ME.user.username}.`, {href: "guide.html?new", text: "Make a profile"})); return; }
    // the top bar's picker (nav.js, #170): the profile the address names, else this browser's last, else the first
    PROFILE = ME.profiles.find(p => p.id === SgNav.profiles(ME.profiles));
    hLoad();
    renderProfileMenu();
    syncWarning(PROFILE.id);
    testPagesLoad();   // "What is this test?" links (test-pages.js, #163), alongside the records
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
      // arriving from Add on another page (#401): a journal entry can be the first record; a dose can't, and says why
      await dates;
      if (location.hash === "#log-dose") logDose(); else if (canImport(PROFILE)) journalResume();
      return;
    }
    if (!r.ok) throw new Error(r.status + " " + r.statusText);
    data = await r.json();
    // this profile's settings (changed on the Settings page); if they can't be loaded the defaults are used
    try {
      const s = await api(`profiles/${PROFILE.id}/settings`);
      if (s.ok) applySettings(await s.json());
    } catch (e) { console.warn("settings ignored:", e.message); }
  } catch (e) { emptyCard("Couldn't load the records: " + e.message); return; }
  applyTabs(CFG.tabs);
  applyTheme(CFG.theme);   // this user's default theme (#45), unless this browser has its own (the light/dark toggle)
  // optional: watch data from the Health Connect receiver
  try {
    const w = await api(`profiles/${PROFILE.id}/watch`);
    if (w.status === 200) WATCH = await w.json();
  } catch (e) {}
  await dates;   // before any time is read: one without an offset is this person's wall-clock time
  storeRecords(data);   // the records, the watch's joined to them, ready to draw (store.js, #271)
  if (RANGES_USED) for (const n of document.querySelectorAll(".range-note")) n.textContent += RANGE_NOTE_SETTINGS;
  await cycleLoad();   // cycle tracking, only when turned on and for owners and editors (cycle.js, #183)
  storeSpan();
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
  journalResume();  // Add → Journal entry from another page (journal.js)
  SgPalette.resume();   // a lab or an event chosen in the command palette on another page (palette.js, #273)
  if (location.hash === "#log-dose") logDose();   // arriving from Add on another page (nav.js)
})();
