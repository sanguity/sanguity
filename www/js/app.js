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
/* the tab in the address (#208), with its view (#442): index.html#overview, #health, #medications (#275), #timeline,
   #timeline-journal (the Timeline as the journal, #277), Labs' views each at its own, #labs-chart (its charts),
   #labs-heatmap, #labs-compare (#297) and #labs-latest (#993), and #labs alone Labs where it opens (labsHome, core.js:
   the view picked, else Latest on a phone and Chart wider, #997; another page's tab bar links there; #1026: it was the
   charts too, so a chart reloaded came back as Latest or the view picked), #costs (Costs' claims) or #costs-paid (what
   you paid, #278); the old #trends and #heatmap still open Labs as charts or the heatmap. What follows a ? is for the tab: #costs?drug=metformin, the drug's claims (medications.js, #452). An
   address with no # opens the tab last used (save(), core.js). tabOfHash: the address's [tab, its
   view], or null; setView: a view, by its name, on the tab it belongs to */
const HASH_TABS = {...TAB_ALIAS, labs: ["labs", "home"], "labs-chart": ["labs", "chart"], "labs-heatmap": ["labs", "heatmap"], "labs-compare": ["labs", "compare"], "labs-latest": ["labs", "latest"], timeline: ["timeline", "timeline"], "timeline-journal": ["timeline", "journal"],
  costs: ["costs", "claims"], "costs-paid": ["costs", "paid"]};
const tabOfHash = () => { const h = decodeURIComponent(location.hash.slice(1)).toLowerCase().replace(/\?.*/s, ""); return HASH_TABS[h] || (TABS.includes(h) ? [h] : null); };
const tabHash = () => "#" + state.tab + (state.tab === "labs" ? "-" + state.labsView : state.tab === "timeline" && state.tlView === "journal" ? "-journal"
  : state.tab === "costs" && costState.view === "paid" ? "-paid" : "");
function setView(v) {
  if (v === "home") state.labsView = labsHome();   // (#labs alone, #1026)
  else if (LABS_VIEWS.includes(v)) state.labsView = v;
  else if (v === "timeline" || v === "journal") state.tlView = v;
  else if (v === "claims" || v === "paid") costState.view = v;
}
/* the address follows the tab shown: a new history entry when someone picks another tab or another view of one (push:
   each tab's view switch too, #442), so Back and Forward move between them; the boot, an old alias and the app's own
   switches replace the entry instead. history's own
   calls fire no hashchange, so the listener below doesn't run again. At the boot, another # in the address (Add →
   Journal entry's #new-journal-entry) is left for its page to read */
function tabAddress(push) {
  const h = tabHash();
  if (location.hash === h || (!booted && location.hash.length > 1 && !tabOfHash())) return;
  try { if (push) history.pushState(null, "", h); else history.replaceState(history.state, "", h); } catch (e) {}
}
const pickTab = (t, view) => switchTab(t, view, true);   // someone chose it: a tab, a tile, a button that opens a tab
/* t: a tab, or an old id ("trends", "heatmap": the Labs tab in that view); view: Labs' "chart" or "heatmap", the
   Timeline's "timeline" or "journal" (#277), or Costs' "claims" or "paid" (#442), if given; push: someone picked it
   (pickTab), so Back returns to the tab, or the view, before. The Timeline picked from elsewhere (an event, a medication's dates) is the timeline itself; its own tab
   link opens it as it was last shown, as Labs' does */
function switchTab(t, view, push) {
  if (catsKept && t !== "timeline") { state.cats = catsKept; catsKept = null; }   // a link's kinds were for the Timeline's visit (#475): the person's own elsewhere (#479)
  if (TAB_ALIAS[t]) [t, state.labsView] = TAB_ALIAS[t];
  if (t === "timeline" && push && !view) view = "timeline";
  setView(view);
  if (!shownTabs().includes(t)) t = shownTabs()[0];
  const changed = t !== state.tab;
  if (changed && drawer.classList.contains("open")) closeDrawer();   // details belong to the tab they were opened from
  hideTip(); state.tab = t;
  for (const k of TABS) { $("#tab-" + k).setAttribute("aria-current", t === k ? "page" : "false"); $("#view-" + k).hidden = t !== k; }   // the top bar's link (nav.js, #264)
  SgNav.fit();   // kept in sight: out from under More if it was there (#265)
  // on a phone the tabs are a row that scrolls sideways: the one shown in the middle of it
  { const b = $("#tab-" + t), bar = $(".tabs"); if (b.parentElement === bar && bar.scrollWidth > bar.clientWidth) bar.scrollTo({left: bar.scrollLeft + b.getBoundingClientRect().left - bar.getBoundingClientRect().left - (bar.clientWidth - b.offsetWidth) / 2, behavior: smoothly()}); }
  { const b = $("#tab-" + t); $("#tabTitle").textContent = b.textContent; viewTitle([b.textContent, PROFILE && PROFILE.name].filter(Boolean).join(" · "), changed && booted && b.textContent); }   // "Labs · Ben · Sanguity"; said once opened (#209), from the tab bar too: its links' aria-current isn't reliably said (#1075)
  // the records bar: its period, on every tab with time (#171); each tab's own filters are in the tab (the Timeline's
  // kinds and search in its Events card's header, #277; Labs' lab names in its list's, #192)
  $(".filters").style.display = t === "overview" ? "none" : "";
  renderAll();
  if (changed) window.scrollTo({top: 0, left: 0, behavior: "instant"});   // each tab starts at the top
  tabAddress(push);   // #208: a new entry when the tab or its view changed (#442)
}
/* the tabs are links (#264): Tab reaches each, Enter or a click opens it here, Labs as it was last shown; one opened in
   a new window (Ctrl, ⌘ or Shift) is the browser's. One picked under More (#265) comes into the bar, with focus. Where
   the browser has them, and less motion isn't asked for, the page changes as a View Transition */
for (const k of TABS) $("#tab-" + k).addEventListener("click", ev => {
  if (ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey || ev.button) return;
  ev.preventDefault();
  const fromMore = !!ev.currentTarget.closest("[role=menu]");
  const go = () => { pickTab(k, k === "timeline" ? state.tlView : null); if (fromMore) $("#tab-" + k).focus({preventScroll: true}); };
  if (k === state.tab || !document.startViewTransition || matchMedia("(prefers-reduced-motion: reduce)").matches) go();
  else document.startViewTransition(go);
});
let booted = false;
// Back, Forward, a link or a typed address: the tab it names (an old alias's # becomes its tab's own)
addEventListener("hashchange", () => {
  // Add → Add a medication (medmine.js, #1092): on a profile with nothing imported too, its first medication of its own
  if (location.hash === "#add-medication" && (booted || PROFILE && !DATA)) return medAddResume();
  if (/^#(family-history|add-relative)$/.test(location.hash) && (booted || PROFILE && !DATA)) return famResume();   // (#1134)
  if (!booted) return;
  if (location.hash === "#log-dose") return logDose();
  const t = tabOfHash(), drug = t?.[0] === "costs" && medCostsArrive();   // #costs?drug=… (#452)
  if (t) switchTab(t[0], t[1]);
  if (drug) medCostsShow();   // as a click on the link shows them (#456)
});
/* Add → Log a dose (#264): today's doses (medlog.js) on the Medications tab, where the log lives (#275), or on the
   Overview while that tab is hidden, the first button focused; with none to log (no current medication, no records
   yet, or a profile you can only view: an address typed in), a toast says why. Add offers it only for a profile with
   a current medication (doseOffer, #401) */
function logDose() {
  const t = shownTabs().includes("medications") ? "medications" : "overview";
  try { history.replaceState(history.state, "", location.pathname + location.search + (DATA ? "#" + t : "")); } catch (e) {}
  if (DATA) switchTab(t);
  const card = canEdit() && $(t === "medications" ? "#medToday" : "#ovDoses");
  const first = card && (card.querySelector(".dose-quick button") || card.querySelector("button"));
  if (!first) {
    toast(!canEdit() ? `You can view ${PROFILE.name}: its owners and editors log its doses.`
      : `${PROFILE.name} has no current medication to log a dose for.`);
    return;
  }
  card.scrollIntoView({block: "start"});
  first.focus({preventScroll: true});
}
const doseOffer = () => $("#doseLink").toggleAttribute("data-none", !DATA || !MEDS.some(mrCurrent));   // nav.js hides it then (#401)
$("#optMarks").onchange = e => { state.marks = e.target.checked; renderCharts(); save(); };
$("#optSmooth").onchange = e => { state.smooth = e.target.checked; renderCharts(); save(); };
function renderAll() {
  if (!DATA) return;   // no records (an empty profile's card, #199): nothing to draw, as when the window is resized
  renderFilters();
  renderFilterSum();   // what the filters leave out, and the Filters button's count (#190, filters.js)
  $("#optMarks").checked = state.marks; $("#optSmooth").checked = state.smooth;
  if (state.tab === "timeline") renderTimelineTab();   // the timeline or the journal (timeline.js, #277)
  else if (state.tab === "labs") renderLabs();
  else if (state.tab === "costs") renderCosts();
  else if (state.tab === "health") renderHealth();
  else if (state.tab === "medications") renderMedications();   // medications.js (#275)
  else renderOverview();
  phoneBar();   // on a phone, Body's and Labs' period beside Show (core.js, #615)
  labAgain();   // a lab's panel in the drawer, for the period now shown (lab-panel.js, #296)
  save();
}
/* the parts of the records each tab shows (store.js, #271): when one changes (a dose logged, an entry or an edit
   saved), the tab shown is drawn again in place, its scroll and focus kept; the others are drawn when they're opened */
const TAB_SHOWS = {overview: ["events", "labs", "medications"], health: ["events"], medications: ["events", "medications"],
  timeline: ["events", "medications", "journal"], labs: ["events", "labs", "medications", "journal"], costs: ["events", "costs"]};
storeOn(STORE_PARTS, parts => {
  if (parts.has("medications")) doseOffer();
  if (booted && TAB_SHOWS[state.tab].some(p => parts.has(p))) keepFocus(renderAll);
});
/* no records to show (an empty profile, none shared, or an error): a message instead of the tabs, and none of the
   controls that would render them, since those need DATA and the tab sections this replaces. One empty state
   (emptyState, util.js: #199), or the words of an error. The page's h1 kept over it, named by head (#514: with no
   profile, or on an empty one, the page had none; it's the tab's name again once records come), and the document's
   title with it */
let mainKids = null;   // the tabs' sections, kept for an empty profile's first records (recordsFirst)
function emptyCard(head, ...kids) {
  $(".tabs").hidden = true; $(".filters").hidden = true;
  const one = kids.length === 1 && kids[0].nodeType ? kids[0] : el("div", {class: "card"}, ...kids);
  one.classList.add("empty");
  const main = document.querySelector("main");
  mainKids ??= [...main.childNodes];
  const h1 = mainKids.find(n => n.id === "tabTitle");
  h1.textContent = head;
  viewTitle(head);   // the browser tab's title says it too (#518: "Sanguity" alone; the tab's name once records come)
  // (offline with no records kept, the banner has the page's h1: offline.js notAvailable, #424)
  main.replaceChildren(...present(document.querySelector("#offlineBanner > h1") ? null : h1, one));
}
/* an empty profile's first records, saved from the upload dialog (drop.js, #504): its tabs in place of the empty
   card, drawn as the page draws them when it loads, without loading it again (the toast's Undo, the dialog's other
   reviews and a file still sending stay). Records that come while they're being drawn are drawn in place after */
let firstDrawn = null;
function recordsFirst(data) {
  if (firstDrawn || DATA) return (firstDrawn || Promise.resolve()).then(() => recordsAgain(data));
  if (mainKids) document.querySelector("main").replaceChildren(...mainKids);
  $(".tabs").hidden = false; $(".filters").hidden = false;
  return firstDrawn = recordsDraw(data);
}
/* the profile's records read again (an import saved from the upload dialog, drop.js, or undone: sg:imports below)
   drawn in place, with the watch data and cycle records, which come apart from them (#522: an Apple Health zip's
   readings and cycle records, which only a reload drew, and an Undo left drawn); read as recordsDraw reads them, then
   joined to the records (storeRecords). One at a time, each after the last (#526: two together put the cycle records
   in twice) */
let againLast = Promise.resolve();
function recordsAgain(data) {
  return againLast = againLast.catch(() => {}).then(async () => {
    try {
      const w = await api(`profiles/${PROFILE.id}/watch`);
      if (w.status === 200) WATCH = await w.json(); else if (w.status === 204) WATCH = null;
    } catch (e) {}
    await cycleLoad();
    storeRecords(data);
  });
}
const canImport = p => p.role === "owner" || p.role === "editor";
/* the profile shown has no records: its empty card (#199), for someone who can bring them in, or not */
function emptyProfile() {
  emptyCard(`${PROFILE.name}: no records yet`, canImport(PROFILE)
    ? emptyState(`${PROFILE.name} has no records yet: lab results, visits, medications and the rest appear here once they're brought in.`,
        {href: `guide.html?profile=${PROFILE.id}`, text: "Get started"})
    : emptyState(`${PROFILE.name} has no records yet: its owners and editors bring them in, and they appear here for you too.`,
        {href: `sharing.html?profile=${PROFILE.id}#people`, text: "See who has access"}));
}
/* an import of the profile shown undone (a toast's Undo, import-jobs.js told, #508): its records read again and drawn
   in place (recordsAgain: its watch data and cycle records too, #522), or, with none left, its empty card back, as a reload would show it; focus, where the import's
   card isn't shown (the upload dialog closed), on the empty card's next step, or the tab's own link (it's always in
   the bar, nav.js), else its first heading shown */
document.addEventListener("sg:imports", ev => {
  const d = ev.detail;
  if (!d.undone || !PROFILE || d.pid !== PROFILE.id || !(DATA || firstDrawn)) return;
  d.wait((async () => {
    await firstDrawn;   // (an empty profile's first records still being drawn)
    const r = await api(`profiles/${PROFILE.id}/data`);
    if (r.status === 200) {
      await recordsAgain(await r.json());
      // the drawer showing one of the import's records, or a medication only it brought, closes (#513: it stayed, with
      // its pencil, ⋯ and Source link), and whatever else it shows that's gone with it (drawerStill, #517: an upcoming
      // appointment); focus where it closes to (drawer.js)
      if (drawer.classList.contains("open") && (state.selEv != null && !byId.has(state.selEv) || drawer.dataset.med != null && !medShown()
          || drawerStill && !drawerStill())) closeDrawer();
      d.focus =[$("#tab-" + state.tab), ...document.querySelectorAll(`#view-${state.tab} h2:not(.vh)`)].find(h => h.checkVisibility());
    } else if (r.status === 204) {
      if (drawer.classList.contains("open")) closeDrawer();
      DATA = null; WATCH = null; firstDrawn = null; booted = false;   // (#526: its watch data drawn again with the next records)
      $("#meta").textContent = "";
      emptyProfile();
      doseOffer();
      d.focus = document.querySelector("main > .empty .empty-next");
    }
  })());
});
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
/* this profile's settings (changed on the Settings page); if they can't be loaded the defaults are used */
async function settingsLoad() {
  try {
    const s = await api(`profiles/${PROFILE.id}/settings`);
    if (s.ok) applySettings(await s.json());
  } catch (e) { console.warn("settings ignored:", e.message); }
}
(async function boot() {
  const dates = datesReady();   // this person's time zone and date format (dates.js), read alongside the rest
  let data;
  try {
    const m = await api("me");
    if (m.status === 401) { const s = await api("setup").then(r => r.json()).catch(() => ({})); location.replace(s.needed ? "setup.html" : "login.html"); return; }
    if (!m.ok) throw new Error(m.status + " " + m.statusText);
    ME = await m.json();
    if (ME.user.must_change_pw) { location.replace("settings.html#password"); return; }
    if (ME.user.needs_two_factor) { location.replace("settings.html#twofa"); return; }
    $("#who").textContent = ME.user.username;
    doseOffer();   // none until the records are here (#401)
    // the first-run guide and the empty states (#199): with no profile, make one (guide.html?new)
    if (!ME.profiles.length) {   // (with the account's own theme, as Settings shows it, #433)
      const u = await api("settings/user").then(x => x.ok ? x.json() : null).catch(() => null);
      if (u) applyTheme(u.theme, u.inRange);
      emptyCard("No health profile yet", sgMayNew(ME)   // (#1001: an admin's alone, on this server: who to ask, nothing to create)
        ? emptyState(`No health profile is shared with you yet: create one of your own, or ask a profile's owner to share theirs with ${ME.user.username}.`, {href: "guide.html?new", text: "Create a profile"})
        : emptyState(`No health profile is shared with you yet: ask a profile's owner to share theirs with ${ME.user.username}, or an admin to create one for you.`));
      return;
    }
    // the top bar's picker (nav.js, #170): the profile the address names, else this browser's last, else the first
    PROFILE = ME.profiles.find(p => p.id === SgNav.profiles(ME.profiles));
    hLoad();
    renderProfileMenu();
    if (sgConnected(ME)) syncWarning(PROFILE.id);   // (#1096) none while connected sources are off
    testPagesLoad();   // "What is this test?" links (test-pages.js, #163), alongside the records
    const r = await api(`profiles/${PROFILE.id}/data`);
    if (r.status === 401) { location.replace("login.html"); return; }
    if (r.status >= 500) { const j = await r.json().catch(() => ({})); throw new Error(j.detail || r.status + " " + r.statusText); }
    if (r.status === 204) {   // no records yet (#199): the getting-started guide, opened by itself the first time
      if (PROFILE.role === "owner" && !PROFILE.demo && ME.profiles.filter(p => !p.demo).length === 1) {   // your own profile, and your only one (the demo beside it doesn't count, #1101)
        const u = await api("settings/user").then(x => x.ok ? x.json() : {}).catch(() => ({}));
        if (u.guide === "" && !/^#(new-journal-entry|log-dose|add-medication|add-relative)$/.test(location.hash)) { location.replace(`guide.html?profile=${PROFILE.id}`); return; }   // not finished or skipped yet; not when Add sent them to do something here (#479)
      }
      await settingsLoad();
      applyTabs(CFG.tabs);   // its tabs as it has them, as the other pages show them (#425)
      applyTheme(CFG.theme, CFG.inRange);   // and the account's theme, as with records (#430)
      emptyProfile();
      // arriving from Add on another page (#401): a journal entry can be the first record; a dose can't, and says why
      await dates;
      if (location.hash === "#log-dose") logDose(); else if (canImport(PROFILE)) { journalResume(); medAddResume(); famResume(); }
      return;
    }
    if (!r.ok) throw new Error(r.status + " " + r.statusText);
    data = await r.json();
    await settingsLoad();
  } catch (e) { emptyCard("Records", "Couldn't load the records: " + e.message); return; }
  await recordsDraw(data);
  try { performance.mark("sanguity:ready"); } catch (e) {}   // the records page is ready: what docs/performance.md times (#201)
  journalSaved();   // the first entry on a profile with no records, saved: the page again, with it open (journal.js, #477)
  journalResume();  // Add → Journal entry from another page (journal.js)
  SgPalette.resume();   // a lab or an event chosen in the command palette on another page (palette.js, #273)
  if (location.hash === "#log-dose") logDose();   // arriving from Add on another page (nav.js)
  medAddResume();   // Add → Add a medication, from here or another page (medmine.js, #1092)
  famResume();   // the palette's Family history or Add a relative, from here or another page (family.js, #1134)
})();
/* the records drawn: as the page loads, or an empty profile's first (recordsFirst) */
async function recordsDraw(data) {
  applyTabs(CFG.tabs);
  applyTheme(CFG.theme, CFG.inRange);   // this user's default theme (#45), unless this browser has its own (the light/dark toggle), and In-range results (#1108)
  // optional: watch data from the Health Connect receiver
  try {
    const w = await api(`profiles/${PROFILE.id}/watch`);
    if (w.status === 200) WATCH = await w.json(); else if (w.status === 204) WATCH = null;   // (none now: #526)
  } catch (e) {}
  await datesReady();   // before any time is read: one without an offset is this person's wall-clock time
  storeRecords(data);   // the records, the watch's joined to them, ready to draw (store.js, #271)
  if (RANGES_USED) for (const n of document.querySelectorAll(".range-note")) n.textContent += RANGE_NOTE_SETTINGS;
  await cycleLoad();   // cycle tracking, only when turned on and for owners and editors (cycle.js, #183)
  storeSpan();
  // when the records were built from their imports, and the watch's last sync: not the build's own name (a file's,
  // the owner's report), but the demo's, which says it's made up (#1101); a profile of imports alone has no build to
  // name (#199: it said "undefined · built"). Each date whole on its line (no-break spaces; a time may go to the next)
  const nb = d => d.replace(/ /g, "\u00a0").replace(/,\u00a0(?=\d{1,2}:)/, ", ");
  $("#meta").textContent = [PROFILE?.demo && DATA.source, DATA.built && "records imported " + nb(fmtD(DATA.built)), WATCH && WATCH.last_payload && "watch synced " + nb(fmtDT(WATCH.last_payload))]
    .filter(Boolean).join(" · ");
  restore();   // a saved period naming a milestone that's since gone is All (setPeriod)
  // Labs opens on Latest on a phone and on Chart wider till a view's picked (#997), whatever was shown last; #labs
  // alone opens it there too, and each view's own address that view (#1026)
  if (!labsPicked) state.labsView = labsHome();
  const arrived = tabOfHash();
  if (arrived) { state.tab = arrived[0]; setView(arrived[1]); }
  // a journal and nothing imported (#472): the Timeline's Journal view, where its entries are, unless the address
  // names another tab; a watch's alone (#477: a phone connected first), Body and activity, where its charts are
  else if (DATA.imports && !Object.keys(DATA.imports).length) {
    if (WATCH && !(DATA.journal || []).length && shownTabs().includes("health")) state.tab = "health";
    // medications of your own and no journal (#1091): Medications, where they are
    else if (MEDS.length && !(DATA.journal || []).length && shownTabs().includes("medications")) state.tab = "medications";
    else [state.tab, state.tlView] = ["timeline", "journal"];
  }
  const drug = arrived?.[0] === "costs" && medCostsArrive();   // (#costs?drug=…, #452)
  if (!state.sel.size) state.sel = startLabs();   // the default saved view, else trends.defaultLabs (trends.js)
  if (state.preset !== "custom" || !(state.to > state.from)) setPeriod(state.preset);
  await testPagesLoad();   // before the first draw, so the links are there (#163)
  switchTab(state.tab);
  if (drug) medCostsShow();   // the drug's claims in sight, focus in their search, as a click on the link (#456)
  booted = true;
}
