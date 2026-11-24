/* Sanguity · detail drawer: one panel for every kind of event.
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
/* ---------------------------------------------------------------- detail drawer */
/* The drawer is a <dialog> (#262), shown by openDialog (util.js). On a screen 1200 px or wider it's docked (#270): a
   side panel beside the records, which stay usable (another record clicked opens in it, which stays open: not modal),
   the page moving over for it (html.sg-docked, app.css) instead of being covered, and drawn again for its new width.
   Anywhere else it's over the page, and modal, so Tab and a screen reader stay in it (#209; #725: Tab went on to the
   controls under it): a side panel down the right edge (the wider one too, which never docks), and on a phone (700 px
   or narrower) a bottom sheet, swiped down by its handle or its header to close. Escape closes it wherever focus is,
   and focus goes back to what opened it, found again if the page was drawn again meanwhile (drawerBack), or to what's
   nearest it in sight (drawerTo). .open says it's open (or opening). */
const drawer = $("#drawer");
let drawerReturn = null, drawerFrom = null;   // what opened it, and where that was on the page (drawerSpot)
/* whether what the drawer shows is still in the records, for what isn't a record of its own (an upcoming appointment,
   a medication's dose or reminder form): set as it's drawn, null again with the next head (drawerHead); after an
   Undo, app.js closes the drawer when it says no (#517) */
let drawerStill = null;
function closeDrawer() {
  const a = document.activeElement, want = drawer.classList.contains("open") && (drawer.contains(a) || a === document.body), sel = state.selEv;
  const r = drawerBack(), mine = want && r && drawerTo(sel) === r ? r : null;   // what opened it, as it is now
  drawer.classList.remove("open");
  if (drawer.open) { drawer.close(); liveFollow(); }
  mine?.focus({preventScroll: true});   // once the page behind isn't inert any more; found again as it's drawn (drawerDock)
  setTimeout(() => { if (!drawer.classList.contains("open")) $("#dbody").replaceChildren(); }, 250);   // unload any PDF / document once it has slid away
  state.selEv = null; document.querySelectorAll(".tl-row.sel").forEach(r => r.classList.remove("sel"));
  if (!drawerDock(false)) renderTimeline();   // undocked, the page is drawn again anyway
  // else what's in sight once the page is laid out without it (#421), so the page stays where it is; found again once
  // Labs' charts are drawn (the frame after, trends.js)
  const x = want && !mine ? drawerTo(sel) : null, at = drawerSpot(x);
  x?.focus({preventScroll: true});
  if (at) requestAnimationFrame(() => requestAnimationFrame(() => { if (!x.isConnected && document.activeElement === document.body) drawerFind(at)?.focus({preventScroll: true}); }));
  // what opened it, shown again once the page is laid out without the drawer (#479: a tab under More while docked, More
  // hidden as they all fit again, focus was left on the page)
  if (want && r) {   // the tabs are laid out again a few frames on: tried each frame, for half a second at most
    let n = 0;
    const back = () => {
      const a = document.activeElement;
      if (a !== document.body && a !== r && a.checkVisibility() && !(a.id === "tabMoreBtn")) return;   // somewhere else on purpose
      if (r.isConnected && r.checkVisibility()) { if (a !== r) r.focus({preventScroll: true}); return; }
      if (++n < 30) requestAnimationFrame(back);
    };
    requestAnimationFrame(back);
  }
  drawerReturn = drawerFrom = null;
}
$("#dclose").onclick = closeDrawer;
drawer.addEventListener("close", () => { if (drawer.classList.contains("open") && !drawer.open) closeDrawer(); });   // swiped away
const drawerPhone = matchMedia("(max-width: 700px)");
// docked beside the page, which stays usable; else over it, modal (app.css's widths)
const drawerDocks = () => !drawerPhone.matches && !drawer.classList.contains("wide") && innerWidth >= 1200;
function drawerFit() {
  if (!drawer.classList.contains("open")) return;
  const dock = drawerDocks();
  openDialog(drawer, {as: drawerPhone.matches ? "sheet" : "side", modal: !dock});
  drawerDock(dock);
}
/* where a control is, to find it again once what holds it is drawn again (a change saved, a dose logged, a resize, the
   drawer docking: #404): what holds it (its view on the page, or the drawer's body), and its place among the controls
   there that are the same (its tag, id, record, name and words), as many of those along (a chart's marker under each
   chart); a record's row (data-id: the Timeline's, the Overview's) by its record, as a rename changes its words */
const FOCUSABLE = "a[href], button, input, select, textarea, [tabindex]";
const focusSig = x => [x.tagName, x.id, x.dataset?.id, x.getAttribute("aria-label"), x.textContent.trim().slice(0, 80)].join("\u001f");
const recordSig = x => x.dataset?.id ? [x.tagName, x.classList[0], x.dataset.id].join("\u001f") : null;
const kinOf = x => x.tagName + "\u001f" + x.classList[0];   // a row of a list, whichever record (drawerTo)
function drawerSpot(x, v = x?.closest?.("main > section")) {
  if (!v) return null;
  const all = [...v.querySelectorAll(FOCUSABLE)], sig = focusSig(x), rec = recordSig(x), kin = kinOf(x);
  return {v, sig, rec, kin, n: all.filter(y => focusSig(y) === sig).indexOf(x), rn: rec ? all.filter(y => recordSig(y) === rec).indexOf(x) : -1,
    kn: all.filter(y => kinOf(y) === kin).indexOf(x)};
}
function drawerFind(s) {
  if (!s) return null;
  const all = [...s.v.querySelectorAll(FOCUSABLE)];
  return (s.rn >= 0 && all.filter(y => recordSig(y) === s.rec)[s.rn]) || (s.n >= 0 && all.filter(y => focusSig(y) === s.sig)[s.n]) || null;
}
/* what opened the drawer, as it is now: found again if the page was drawn again since (after every redraw, store.js
   keepFocus, and when it docks or closes) */
function drawerBack() {
  if (!drawerReturn) return null;
  const x = drawerReturn.isConnected ? drawerReturn : drawerFind(drawerFrom);
  if (x) { drawerReturn = x; drawerFrom = drawerSpot(x) || drawerFrom; }
  return drawerReturn;
}
/* where focus goes as the drawer closes (#417): what opened it, found again (drawerBack); else, asked once the drawer
   has closed and the page is laid out without it, only what's in sight there (the top bar always is), as the page
   doesn't move (#421): an item of a menu or a sheet that has closed since (a phone's Add → Journal entry), that menu's
   button; when that's gone or out of sight (the page had focus nowhere, as after the command palette or a click on a
   chart's mark; its row hidden or merged away), the open record's row in the view shown, else the row that's now where
   that one was (the next), else the heading of its list or of another part of the view (the chart's), else any control
   there */
function drawerTo(sel = state.selEv) {
  const r = drawerBack();
  let x = r;
  const box = x?.closest?.("dialog"), pop = x?.closest?.("[popover]");
  if (box && box !== drawer && !box.open) x = box.sgFrom;   // a sheet's button (nav.js menuSheet)
  else if (pop && !pop.matches(":popover-open")) x = [...document.querySelectorAll("button")].find(b => b.popoverTargetElement === pop);
  const seen = y => !!y && y.isConnected && y !== document.body && !drawer.contains(y) && y.checkVisibility();
  const top = document.querySelector("header.topbar")?.getBoundingClientRect().bottom || 0, tabs = document.querySelector("nav.tabs")?.getBoundingClientRect();
  const low = tabs?.top > innerHeight / 2 ? tabs.top : innerHeight;   // under the top bar, above a phone's tab bar
  const sight = y => seen(y) && (!!y.closest("header.topbar") || (b => b.bottom > top && b.top < low && b.right > 0 && b.left < innerWidth)(y.getBoundingClientRect()));
  if (x === r ? seen(x) : sight(x)) return x;
  const s = drawerFrom, v = [s?.v, document.querySelector("main > section:not([hidden])")].find(seen);
  if (!v) return null;
  const all = [...v.querySelectorAll(FOCUSABLE)].filter(seen), kin = s ? all.filter(y => kinOf(y) === s.kin) : [];
  x = [all.find(y => sel != null && y.dataset.id === String(sel)), s && s.v === v && s.kn >= 0 && kin[Math.min(s.kn, kin.length - 1)],
    v.querySelector("#listTitle"), ...v.querySelectorAll("h2"), ...all].find(sight);   // the Timeline's list's heading first
  if (x?.matches("h2") && !x.hasAttribute("tabindex")) x.tabIndex = -1;
  return x || null;
}
/* docked or not: the page's width changes, so its charts are drawn again; what had focus on it, and what focus goes
   back to, are found again in what's drawn. True if it changed. */
function drawerDock(on) {
  const root = document.documentElement;
  if (root.classList.contains("sg-docked") === on) return false;
  const was = document.activeElement, a = drawerSpot(was);
  drawerBack();
  root.classList.toggle("sg-docked", on);
  dockStill = ptrAt;
  renderAll();
  const back = () => {   // now, and again once Labs' charts are drawn (the frame after, trends.js)
    if (a && !was.isConnected && [was, document.body].includes(document.activeElement)) drawerFind(a)?.focus({preventScroll: true});
  };
  back(); requestAnimationFrame(() => requestAnimationFrame(back));
  // docked, what it was opened from still in view; undocked, what has focus on the page, which the wider page moved
  // (a claim's row, focused as the drawer closed, #467), as the Timeline keeps its row: the whole row, not only the
  // button in it (#473: a claim's name at the window's edge, its date and amounts cut off), then the button clear of a
  // table's sticky headers
  const r = on ? drawerBack() : document.activeElement;
  if (r?.closest?.("main")) { r.closest("tr")?.scrollIntoView({block: "nearest"}); r.scrollIntoView({block: "nearest"}); }
  return true;
}
// the page moving over puts something else under a pointer that hasn't moved: no tooltip for it (util.js, by hover,
// which would take the next Escape) until the pointer moves
let ptrAt = "", dockStill = null;
addEventListener("pointermove", ev => { ptrAt = ev.clientX + "," + ev.clientY; if (dockStill !== ptrAt) dockStill = null; }, true);
addEventListener("pointerover", ev => { if (dockStill != null) ev.stopPropagation(); }, true);
addEventListener("resize", drawerFit);
function setWide(on) { drawer.classList.toggle("wide", on); drawerFit(); $("#dwide").setAttribute("aria-pressed", on); $("#dwide").dataset.tip = on ? "Narrower panel" : "Wider panel"; try { localStorage.setItem("ht-wide", on ? "1" : ""); } catch (e) {} }
$("#dwide").onclick = () => setWide(!drawer.classList.contains("wide"));
try { if (localStorage.getItem("ht-wide")) setWide(true); } catch (e) {}
// not an ask() over it, nor a key something else took: a toast's, or a menu's or the rename box's (an open popover
// that isn't the toasts or the tooltip; the browser closes it after this, #364)
addEventListener("keydown", e => {
  if (e.key === "Escape" && !e.defaultPrevented && !e.target.closest?.("dialog:not(#drawer)") && !document.querySelector(":popover-open:not([popover=manual])")) closeDrawer();
});
// modal, the browser would close it on any Escape: only that listener does, so a form in it (renaming) keeps its own
drawer.addEventListener("cancel", e => e.preventDefault());
// over the page (not docked, not a phone's sheet), a click on the dimmed page beside it closes it, focus back where it
// was opened (#725: modal there, the page under it took no clicks, where it had opened the record clicked). Only a
// press that started there too, focus left where it was (#755: words in it selected with the mouse let go past its
// edge closed it), and never while a form in it holds a change not saved, which would be gone, nothing asked: words
// typed (a journal note, a new title), and any other field changed (#784: a dose set to Skipped at 07:15, a reminder's
// time, a journal entry's mood or date, Fix date's new date, a new category or the similar entries ticked, a photo
// chosen), or removed (#811: a reminder's time, a journal photo): that click does nothing. ✕ and Escape still close it.
const drawerBeside = e => e.target === drawer && drawer.matches(":modal") && !drawerPhone.matches
  && (r => e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom)(drawer.getBoundingClientRect());
/* a change (#784, #811): each of its forms (the ⋯ menu's corrections, .edit-form, too) read whole as it's drawn, every
   field in order by its id or name (its value, its tick, a radio's or a toggle's state; an empty one holds nothing to
   lose: a time added and left blank), and compared whole with the form now. So a field or a row drawn and since
   removed is a change (a reminder's time, a journal photo: its id is a field of the form), as one added is (a photo
   chosen: sent at once, its box emptied once it's sent), and a change undone isn't (a photo added and removed again).
   What's drawn in a form until it's first touched (a key, a click, a field changed) is how it was drawn (the similar
   entries Change category asks the server for), and a form drawn anew (its content all put in again: the ⋯ menu's
   next correction, or Cancel emptying it) is read again from there. A new record's forms are new (drawerHead) */
const DRAWER_FORMS = ":is(form, .edit-form)", FORM_FIELDS = ":is(input, select, textarea, [aria-checked], [aria-pressed])";
const fieldNow = f => f.matches("[type=checkbox], [type=radio]") ? f.checked : f.matches("input, select, textarea") ? f.value
  : f.getAttribute("aria-checked") ?? f.getAttribute("aria-pressed");
const formNow = x => JSON.stringify([...x.querySelectorAll(FORM_FIELDS)].map(f => [f.id || f.name || "", fieldNow(f)]).filter(([, v]) => v !== ""));
const drawerWas = new WeakMap(), drawerTouched = new WeakSet();
new MutationObserver(rs => {
  for (const r of rs) if (r.target.matches?.(DRAWER_FORMS) && [...r.target.childNodes].every(n => [...r.addedNodes].includes(n))) drawerTouched.delete(r.target);
  for (const x of drawer.querySelectorAll(DRAWER_FORMS)) if (!drawerTouched.has(x)) drawerWas.set(x, formNow(x));
}).observe(drawer, {childList: true, subtree: true});
for (const t of ["keydown", "click", "input", "change"])   // (in the capture phase: before the form's own handlers change it)
  drawer.addEventListener(t, e => { const x = e.target.closest?.(DRAWER_FORMS); if (x && drawerWas.has(x)) drawerTouched.add(x); }, true);
const drawerChanged = () => [...drawer.querySelectorAll(DRAWER_FORMS)].some(x => drawerTouched.has(x) && formNow(x) !== drawerWas.get(x));
let drawerPress = false;
drawer.addEventListener("mousedown", e => { if ((drawerPress = drawerBeside(e))) e.preventDefault(); });
drawer.addEventListener("click", e => {
  if (drawerPress && drawerBeside(e) && !drawerChanged()) closeDrawer();
  drawerPress = false;
});

/* ---------------------------------------------------------------- one layout for every record (#270)
   The head: the kind's icon in its colour, the title, one line of what kind it is and when, and a ⋯ menu of the
   corrections (Hide, Rename, Fix date, Merge…: drawerMenu). The body: its key facts (three or four, dl.facts), what
   the record holds, the actions that go somewhere (.dgo: another tab, the medication's history, a journal note), and
   where it came from. Each fact once: a value the head or a key fact says isn't said again below (drawerFacts), by
   meaning as well as by its words (#406): a day the head names, however it's written, or the place its title is. */
let drawerSaid = new Set(), drawerKeys = [];   // drawerKeys: the key facts, [label, value] (factSays)
const saidKey = v => String(v).trim().toLowerCase();
/* days: the timestamps of the days the head names (a fact gives a day without its time) */
function drawerHead(k, title, line, tone, days = []) {
  const key = $("#dkey");
  key.replaceChildren(icon(KIND_ICON[k] || (k ? "file-text" : "history")));   // none: the list of edits
  key.style.setProperty("--tone", tone || CAT_OF[k]?.color || "var(--muted)");
  $("#dtitle").textContent = title; $("#dsub").textContent = line;
  drawerSaid = new Set([title, ...title.split(" · "), ...line.split(" · ").flatMap(x => x.split(" – ")), ...days.filter(isFinite).map(fmtD)].filter(Boolean).map(saidKey));
  drawerKeys = [];
  drawerMenu([]); delete drawer.dataset.med; delete drawer.dataset.lab; drawerStill = null;   // a medication's history marks it again (meds.js medShown, #417), a lab's panel (lab-panel.js labShown, #296)
  const body = $("#dbody"); body.replaceChildren(); body.scrollTop = 0; body.classList.remove("fill");
  return body;
}
/* a value as it's said under the head: "" when the head or a key fact said it already, and without what they said at
   its start ("Northfield General Hospital, oncology unit" under that hospital's title: "Oncology unit", #406) */
function unsaid(v) {
  const k = saidKey(v), at = [...drawerSaid].find(s => s.length > 3 && k.startsWith(s + ", "));
  if (drawerSaid.has(k)) return "";
  if (!at) return v;
  const rest = String(v).trim().slice(at.length + 2);
  return rest[0].toUpperCase() + rest.slice(1);
}
/* [label, value, dd's attributes?] pairs, those with a value not already said: the key facts ("facts", each in its own
   box, then said) or the rest ("kv") */
function drawerFacts(rows, cls = "kv") {
  const keep = rows.filter(r => r && r[1] != null && r[1] !== "").map(([k, v, o]) => [k, unsaid(v), o]).filter(r => r[1] !== "");
  if (!keep.length) return null;
  if (cls === "kv") return el("dl", {class: "kv"}, keep.flatMap(([k, v, o]) => [el("dt", {text: k}), el("dd", {text: v, ...o})]));
  keep.forEach(r => { drawerSaid.add(saidKey(r[1])); drawerKeys.push(r); });
  return el("dl", {class: "facts"}, keep.map(([k, v, o]) => el("div", {}, el("dt", {text: k}), el("dd", {text: v, ...o}))));
}
/* whether a key fact says x in other words (#418): x's one number is the fact's value, and a word of x is a word of
   its label ("2 messages": Messages 2; "30-day supply": Days' supply 30) */
const stems = s => (String(s).toLowerCase().match(/[a-z]+/g) || []).map(w => w.replace(/s$/, ""));
function factSays(x) {
  const n = String(x).match(/\d+(?:[.,]\d+)?/g), w = stems(x);
  return n?.length === 1 && drawerKeys.some(([k, v]) => String(v).trim() === n[0] && stems(k).some(s => w.includes(s)));
}
/* the ⋯ menu: [{text, tip?, run}], none (hidden) for a record that can't be corrected; label: what its button says */
const dMenu = SgNav.menu({btn: $("#dmoreBtn"), list: $("#dmoreList")});
function drawerMenu(items, label = "Correct this record") {
  items = items.filter(Boolean);
  for (const x of [$("#dmoreBtn"), $("#dmoreList")]) x.setAttribute("aria-label", label);
  $("#dmoreBtn").dataset.tip = label;
  $("#dmoreList").replaceChildren(...items.map(x => el("button", {class: "menu-item", role: "menuitem", type: "button", tabindex: "-1", text: x.text,
    "data-tip": x.tip, onclick: () => { dMenu.close(); x.run(); }})));
  $("#dmore").hidden = !items.length;
}
/* while its details load (#217): the words (details.js) and the shape of what's coming */
const drawerSkeleton = () => el("div", {class: "skel", "aria-hidden": "true"}, el("i"), el("i"), el("i"), el("i"));
/* opened from a row: it grows into the drawer (a View Transition), where the browser can and motion isn't reduced;
   with the drawer already open the record just changes in it */
function openFrom(row, open) {
  if (!document.startViewTransition || drawer.classList.contains("open") || matchMedia("(prefers-reduced-motion: reduce)").matches) return open();
  row.classList.add("vt-from"); drawer.classList.add("vt");
  document.startViewTransition(() => { row.classList.remove("vt-from"); open(); }).finished.finally(() => drawer.classList.remove("vt"));
}

function docFrame(html, h = 420, title = "The record as it was sent") {   // title: what a screen reader says of the frame (#202)
  const f = el("iframe", {class: "doc", sandbox: "allow-popups", title, style: {height: h + "px"}});
  f.srcdoc = `<!doctype html><meta charset=utf-8><base target=_blank><style>body{font:14px/1.45 system-ui,sans-serif;color:#111;margin:14px}table{border-collapse:collapse}td,th{padding:2px 6px}</style>${html}`;
  f.onload = () => { try { f.style.height = Math.min(1600, f.contentDocument.body.scrollHeight + 30) + "px"; } catch (e) {} };
  return f;
}
/* the result a drawer row shows, {s, p}, or null (a text result): of the series the server matched the row to (d.series,
   server/records.py _row_series: by the row's name or its canonical one, so a report's "Glucose" is Labs' "Glucose,
   Blood", #782), else the one with the row's name; in the row's import, on its day, with its value as reported (or
   its one result that day) */
const rowSaid = t => { const m = /^\s*([<>]=?|=<|=>|≤|≥)?\s*(-?\d+(?:\.\d+)?)\s*$/.exec(t ?? ""); return m ? rangeCmp(m[1] || "") + +m[2] : String(t ?? "").trim(); };
function rowPoint(d, i, ev) {
  if (!ev) return null;
  const row = d.rows[i], name = d.series?.[i] ?? row[0], v = rowSaid(row.orig ? row.orig[0] : row[1]), day = isoDay(ev.ts);
  for (const s of DATA.labs) {
    if (s.watch || s.name !== name || (ev.imp != null && s.imp != null && s.imp !== ev.imp)) continue;
    const on = s.pts.filter(p => isoDay(p.ts) === day), p = on.find(p => rowSaid(p.orig ? p.orig.v : p[5] || p[1]) === v) || (on.length === 1 ? on[0] : null);
    if (p) return {s, p};
  }
  return null;
}
/* a result row's flag (#752, #782): the one Labs shows for its result (rowPoint), by the one rule (ranges.js) against its
   report's range or, where the report gives none, a range from settings; a row with no result, its report's */
const rowFlag = (d, i, ev, at = rowPoint(d, i, ev)) => at ? at.p[4] || "" : d.rows[i][4] || "";
/* how many of a result's rows are flagged, as its table marks them */
const rowsOut = (d, ev) => (d?.rows || []).filter((_, i) => rowFlag(d, i, ev)).length;
function resultTable(d, ev) {
  const rows = d.rows;
  if (!rows || !rows.length) return null;
  const at = rows.map((_, i) => rowPoint(d, i, ev));
  return scrollBox({class: "tablewrap", "aria-label": "Results", style: {maxHeight: "none"}}, el("table", {class: "data"},
    el("thead", {}, el("tr", {}, ["Component", "Value", "Ref range"].map(h => el("th", {scope: "col", text: h})), el("th", {scope: "col"}, el("span", {class: "vh", text: "Flag"})))),
    el("tbody", {}, rows.map(([n, v, u, rr], i) => {
      // its series' chart (the series it's a result of, #782); a value flagged against a range from settings names that
      // range on a line under it, as the row's range is its report's: "<3.0 mmol/L (settings range 3.9–5.5)", as the visit
      // summary says it (under the value, so the flag's column stays as narrow as "▼ Low" on a phone). A row whose report
      // printed no range, its result's from another copy of it (#825), names that range and where it came from in its Ref
      // range (#834: crossmatch.copies' "rangeFrom"): "3.5–5.1 (from the other copy's report)"
      const f = rowFlag(d, i, ev, at[i]), sn = at[i]?.s.name ?? n, chart = SERIES.some(s => s.name === sn && s.n), own = f && at[i]?.p.rs === "settings";
      const copied = !String(rr ?? "").trim() && at[i]?.p.rangeFrom != null && at[i].p.rs !== "settings" && rangeWords(at[i].p);
      return el("tr", {class: chart ? "link" : "", onclick: () => { if (chart) showSeries(sn); }},
        el("td", {}, chart ? el("button", {class: "cell-btn", type: "button", text: nice(n)}) : nice(n), nice(n) !== n ? el("small", {class: "muted", text: ` (${n})`}) : null,   // #38
          SERIES.some(s => s.name === n && s.n) ? renameButton("lab", n) : null, testLink(n, true)), el("td", {class: f ? "abn" : "", text: `${v ?? ""} ${u || ""}`.trim()}, ...rowOrig(rows[i]),
          own ? el("small", {class: "orig", text: `(settings range ${rangeWords(at[i].p)})`}) : null), el("td", {text: copied ? `${copied} (from the other copy's report)` : rr || ""}), el("td", {class: f ? "abn" : ""}, ...flagNodes(f)));   // the mark and the word (#60); "What is this test?" (#163); a value kept with its unit (#270)
    }))));
}
/* #41: a converted row's value and range as reported (units.js), on its own line under the value */
function rowOrig(row) {
  return row.orig ? [el("small", {class: "orig", text: `Reported ${`${row.orig[0] ?? ""} ${row.orig[1] || ""}`.trim()}${row.orig[2] ? ", ref " + row.orig[2] : ""}`})] : [];
}
/* a result: its key facts when it's the record open (top), else (one of a lab day's) all of them as a list */
function resultBody(d, eid, top) {
  const out = [], key = [["Ordered by", d.provider], ["Status", d.status], ["Lab", d.lab], ["Specimen", d.specimen]];
  if (top) out.push(drawerFacts(key, "facts"));
  out.push(drawerFacts([["Order", d.order && panelName(d.order)], ["Collected", recordDate(d.collected)], ["Resulted", recordDate(d.resulted)], ...(top ? [] : key), ["Diagnosis", (d.dx || []).join("; ")]]));
  const t = resultTable(d, byId.get(eid)); if (t) out.push(el("div", {class: "sect", text: "Results"}), t);
  const same = sameNotes(eid, d); if (same) out.push(same);   // the same result from another source (same.js)
  for (const [k, h, isHtml] of d.report || []) out.push(el("div", {class: "sect", text: k === "combinedRTFNarrativeImpression" ? "Report" : k}),
    isHtml ? docFrame(h, 420, k === "combinedRTFNarrativeImpression" ? "Report" : k) : el("pre", {class: "report", text: h.replace(/\r\n/g, "\n").trim()}));
  if (d.comments && d.comments.length) out.push(el("div", {class: "sect", text: "Provider comments"}), ...d.comments.map(c => el("p", {text: c})));
  return out.filter(Boolean);
}
const uniq = xs => [...new Set(xs.filter(Boolean))].join("; ");
const goBtn = (text, onclick) => el("button", {class: "btn", type: "button", text, onclick});
const note = text => el("p", {class: "hint dnote", text});
function openEvent(e) {
  if (!e) return;
  if (e.k === "journal" || e.k === "mood") return openJournal(e);   // journal.js (a mood: #161)
  if (e.k === "cycle") return openCycle(e);       // cycle.js (#183)
  const d = DATA.details[e.id] || {};
  state.selEv = e.id;
  // its kind once (#406): a title that starts with it ("Hospital stay · Northfield General Hospital") is the rest, the
  // line under it the kind and when; the facts leave out the days it names
  const kind = KIND_LABEL[e.k] || "Record", parts = e.title.split(" · ");
  let title = parts.length > 1 && saidKey(parts[0]) === saidKey(kind) ? parts.slice(1).join(" · ") : e.k === "labitem" ? panelName(e.title) : e.title;   // a result's panel as Labs names it (#451)
  if (d.type === "labday") title = labDayTitle(e, title);   // its values counted, as its Results fact counts them (#458)
  const when = e.end && e.te - e.ts > DAY ? fmtD(e.ts) + " – " + fmtD(e.te) : fmtDT(e.ts);
  const body = drawerHead(e.k, title, saidKey(title) === saidKey(kind) ? when : `${kind} · ${when}`, null, [e.ts, e.te]);
  { const wait = detailsWait(e, body); if (wait) { body.append(drawerSkeleton()); showDrawer(); return wait; } }   // its details, loaded when it's opened (details.js, #217)
  const fixes = WATCH_KINDS.has(e.k) ? null : editPanel(e);   // the ⋯ menu's corrections, and what was corrected; the watch's own aren't records
  const main = [], go = [], facts = rows => main.push(drawerFacts(rows, "facts")), kv = rows => main.push(drawerFacts(rows));
  if (d.type === "result") main.push(...resultBody(d, e.id, true));
  else if (d.type === "labday") {
    const rs = d.ids.map(id => DATA.details[id]).filter(Boolean), abn = d.ids.reduce((a, id) => a + rowsOut(DATA.details[id], byId.get(id)), 0);
    facts([["Results", String(rs.reduce((a, r) => a + (r.rows || []).length, 0))], ["Out of range", abn ? String(abn) : "None"],
      ["Ordered by", uniq(rs.map(r => r.provider))], ["Lab", uniq(rs.map(r => r.lab))]]);
    go.push(goBtn("Show this day in Labs", () => { setRange(e.ts - 45*DAY, e.ts + 45*DAY); pickTab("labs"); }));
    for (const id of d.ids) {
      const r = DATA.details[id], ev2 = byId.get(id); if (!r) continue;
      const abn = rowsOut(r, ev2);
      const det = el("details", {class: "res", open: d.ids.length <= 3 || null}, el("summary", {}, el("span", {text: panelName(r.order || ev2.title)}), el("span", {class: "spacer"}), el("span", {class: "res-t", text: fmtDT(ev2.ts).split(", ").pop()}), abn ? el("span", {class: "badge abn", "data-tip": `${abn} outside reference range`, text: abn}, el("span", {class: "vh", text: " outside reference range"})) : null), el("div", {}, ...resultBody({...r, order: null}, id), eventSource(ev2)));
      main.push(det);
    }
  } else if (d.type === "conversation") {
    facts([["Messages", String(d.messages.length)], ["From", uniq(d.messages.map(m => m.from)).replace(/; /g, ", ")]]);
    for (const m of d.messages) {
      const html = /<[a-z][\s\S]*>/i.test(m.body);
      main.push(el("div", {class: "msg"}, el("div", {class: "mh"}, el("b", {text: m.from}), " · " + fmtDT(m.t)),
        html ? docFrame(m.body, 200, `Message from ${m.from}`) : el("pre", {text: m.body.replace(/\r\n/g, "\n").trim()}),
        m.att && m.att.length ? el("div", {class: "mh", text: "Attachments: " + m.att.join(", ")}) : null));
    }
  } else if (d.type === "letter") main.push(docFrame(d.html, 600, e.title || "Letter"));
  else if (d.type === "file" && !d.url) main.push(note("Its file wasn't found in what was imported, so there's nothing to show."));   // profiles.py index_documents
  else if (d.type === "file") {
    // a PDF where the browser can't show one in a page (Chrome on Android, #636): no frame, which would show "This
    // content is blocked"; Open first, a tab of its own, where the phone hands it to its PDF app
    const noPdf = d.ext === "pdf" && navigator.pdfViewerEnabled === false;
    go.push(el("a", {class: "btn" + (noPdf ? " primary" : ""), href: d.url, target: "_blank", rel: "noopener", text: noPdf ? "Open" : "Open in new tab"}), el("a", {class: "btn", href: d.url, download: "", text: "Download"}));
    // documents fill the panel's remaining height so the viewer's own scrollbar and toolbar stay on screen
    // HTML records: no scripts (the sandbox has no allow-scripts, and the server sends script-src 'none'), but
    // same-origin, so the record can load the pictures and stylesheet saved beside it (an opaque origin can't)
    if (d.ext === "html" || d.ext === "htm") { body.classList.add("fill"); main.push(el("iframe", {class: "doc", src: d.url, sandbox: "allow-same-origin", title: e.title})); }
    else if (["jpg","jpeg","png","gif"].includes(d.ext)) main.push(el("img", {class: "doc-img", src: d.url, alt: e.title}));
    else if (noPdf) main.push(el("p", {class: "doc-card"}, icon("file-text"), el("span", {}, el("b", {text: e.title || "Document"}), el("br"),
      el("span", {class: "muted", text: "A PDF. This browser can't show it here: Open shows it in your PDF app, or Download keeps a copy."}))));
    else if (d.ext === "pdf") { body.classList.add("fill"); main.push(el("iframe", {class: "doc", src: d.url + (d.url.includes("#") ? "" : "#view=FitH"), title: e.title})); }
    else if (d.ext === "tif" || d.ext === "tiff") main.push(note("Scanned TIFF document — browsers can't preview TIFF; use Download and open it in Photos or a PDF viewer."));
  } else if (d.type === "stay") {
    if (d.discharge) {
      facts([["Where", d.where], ["Admission", d.admission], ["Transferred from", d.transfer_from], ["Transferred to", d.transfer_to]]);
      kv([["Admitted", fmtDT(e.ts)], ["Discharged", fmtDT(e.te)], ["Recorded as", d.how]]);
    } else {
      facts([["Admitted", d.admit], ["Last result", fmtDT(d.lastResult)], ["Inpatient results", d.results]]);
      main.push(note("Estimated: no admission record was available, so the end date is the last inpatient lab result."));
    }
    if (d.note) main.push(note(d.note));
    go.push(goBtn("Zoom timeline to this stay", () => { setRange(e.ts - 3*DAY, e.te + 3*DAY); closeDrawer(); }),
      goBtn("Labs during this stay", () => { setRange(e.ts - 3*DAY, e.te + 3*DAY); pickTab("labs"); closeDrawer(); }));
  } else if (d.type === "exercise") {
    facts([["Activity", d.kind], ["Duration", d.minutes + " min"], ["From", "Watch via Health Connect"]]);
    kv([["Started", fmtDT(e.ts)], ["Ended", fmtDT(e.te)]]);
    if (d.hr) main.push(el("div", {class: "sect", text: "Heart rate"}), workoutZones(d.hr));   // and its zones (#197, health.js)
    go.push(goBtn("Body and activity around this day", () => openHealth(e.ts - 14*DAY, e.ts + 14*DAY)));
  } else if (d.type === "ecg") main.push(...ecgBody(e, d));   // a watch's ECG or irregular rhythm notification (ecg.js, #182)
  else if (d.type === "visit") {
    facts([["Provider", d.who], ["Location", d.where], ["Diagnosis", d.dx], ["Billed as", d.billed]]);
    if (d.note) main.push(note(d.note));
    if (d.procs && d.procs.length) main.push(el("div", {class: "sect", text: "Recorded that day"}), el("ul", {class: "dlist"}, ...d.procs.map(p => el("li", {text: p}))));
    const sameDay = EV.filter(x => x.k === "document" && isoDay(x.ts) === isoDay(e.ts));
    if (sameDay.length) main.push(el("div", {class: "sect", text: "Documents from that day"}), el("div", {class: "btns"}, ...sameDay.map(x => goBtn(x.title, () => openEvent(x)))));
  } else if (d.type === "claim") {
    const st = (d.status || "").replace(/_/g, " ").toLowerCase();
    facts([["Status", st, /denied/.test(st) ? {class: "abn"} : null], ["Billed", usd(d.billed)], ["Plan paid", usd(d.paid)], ["Your share", usd(d.owe)]]);
    kv([["Insurer", d.insurer], ["Type", d.kind], ["Provider", d.provider], ["Drug", d.drug], ["Claim #", d.claimId], ["Network", (d.network || "").replace(/_/g, " ").toLowerCase()]]);
    if (d.lines && d.lines.length) main.push(el("div", {class: "sect", text: "Services billed"}), scrollBox({class: "tablewrap", "aria-label": "Services billed", style: {maxHeight: "none"}}, el("table", {class: "data"},
      el("thead", {}, el("tr", {}, ["Date", "Service", "Code", "Billed"].map(h => el("th", {text: h})))),
      el("tbody", {}, d.lines.map(l => el("tr", {}, el("td", {text: recordDate(l.date) || ""}), el("td", {}, l.desc || "", ...(l.denied || []).map(x => el("div", {class: "abn denied", text: "Denied: " + x}))),
        el("td", {text: l.code || ""}), el("td", {text: l.billed ? usd(l.billed) : ""})))))));
    if (/denied/.test(st)) main.push(note("\"Provider denied\" usually means the provider must correct or resubmit the claim and you aren't billed for it; \"denied\" can mean you are. The EOB on your insurer's site has the exact reason."));
  } else if (d.type === "immunization") {   // #179: what the record says of a vaccine given, as recorded
    facts([["Dose", d.dose], ["Given by", d.performer], ["Where", d.where], ["Status", d.status]]);
    kv([["Vaccine", d.vaccine], ["Code", d.code ? `${d.system ? d.system + " " : ""}${d.code}` : ""], ["Given", fmtD(e.ts)], ["Amount", d.amount], ["Route", d.route],
      ["Site", d.site], ["Lot", d.lot], ["Made by", d.manufacturer], ["Reason", d.reason]]);
    if (d.note) main.push(note(d.note));
  } else if (d.type === "med" && e.k === "rx") {   // #179: a pharmacy fill (FHIR MedicationDispense), not a medication list's line
    facts([["Pharmacy", d.pharmacy], ["Quantity", d.quantity], ["Days' supply", d.daysSupply ? String(d.daysSupply) : ""], ["Prescribed by", d.prescriber]]);
    kv([["Filled", fmtD(e.ts)], ["Instructions", d.instructions], ["Status", d.status]]);
  } else if (d.type === "med") {
    const typed = d.src === "Typed in";   // hand entry (import page): the dates are the ones typed
    facts([["Instructions", d.instructions], ["Status", d.status], ["Prescribed by", d.prescriber]]);
    kv([[typed ? "Started" : "Listed since", fmtD(e.ts)], ["Stopped", d.end ? fmtD(d.end) : ""]]);
    if (!typed) main.push(note("From the clinic's medication list. The date is when it was recorded there, not necessarily when you started it."));
  }
  // a list of details built elsewhere (an ECG's, ecg.js) leaves out what the head or a key fact says, as drawerFacts does
  for (const dl of main) if (dl?.matches?.("dl.kv")) for (const dd of dl.querySelectorAll(":scope > dd"))
    if (drawerSaid.has(saidKey(dd.textContent))) { dd.previousElementSibling?.remove(); dd.remove(); }
  // what the line under its title said that nothing else does (a stay's reason, a fill's supply), said once, first;
  // not what a key fact says in other words (a conversation's count, a fill's supply when it's a fact: #418)
  if (d.type !== "labday") {
    const said = [...drawerSaid, ...main.map(n => n ? saidKey(n.textContent) : "")].join("\n");
    const rest = (e.sub || "").split(" · ").filter(x => x && !said.includes(saidKey(x)) && !factSays(x));
    if (rest.length) main.unshift(note(rest.join(" · ")));
  }
  { const mh = medHistoryAction(e); if (mh) go.push(mh); }   // a medication's whole history (meds.js, #50)
  go.push(journalAddAction(e));   // "Add a journal note" for this day (journal.js)
  body.append(...present(fixes, ...main, el("div", {class: "btns dgo"}, ...go), eventSource(e, !fixes?.querySelector(".edited"))));   // where it came from (sources.js), "Edited" once
  showDrawer();
  renderTimeline(); document.querySelectorAll(".tl-row").forEach(r => r.classList.remove("sel"));
}
function showDrawer() {
  if (!drawer.classList.contains("open")) { drawerReturn = document.activeElement; drawerFrom = drawerSpot(drawerReturn); }
  drawer.classList.add("open"); drawerFit();
  $("#dclose").focus({preventScroll: true});
}
/* the drawer drawn again with the same thing in it (a change saved there, a dose logged beside it: #404): its scroll,
   the parts opened or closed and the lists shown whole (data-whole="1") stay as they were, and focus stays on what had
   it, or on the same control drawn again. draw() may give back a promise (its details loading again, details.js):
   kept once that's drawn. */
function drawerKeep(draw) {
  const body = $("#dbody"), top = body.scrollTop, sum = d => d.querySelector(":scope > summary")?.textContent;
  const open = new Map([...body.querySelectorAll("details")].map(d => [sum(d), d.open]));
  const whole = [...body.querySelectorAll("[data-whole]")].map(b => b.dataset.whole === "1");
  const was = document.activeElement, at = body.contains(was) ? drawerSpot(was, body) : null;
  const keep = () => {
    for (const d of body.querySelectorAll("details")) if (open.has(sum(d))) d.open = open.get(sum(d));
    [...body.querySelectorAll("[data-whole]")].forEach((b, i) => { if (whole[i] && b.dataset.whole !== "1") b.click(); });
    body.scrollTop = top;
    const x = was?.isConnected ? was : drawerFind(at);
    if (x && x !== document.activeElement && was !== document.body) x.focus({preventScroll: true});
  };
  const r = draw();
  return r?.then ? r.then(keep) : keep();
}


/* ---------------------------------------------------------------- corrections (edits, server/edits.py)
   Owners and editors can hide, rename, re-date, un-stay or merge the open event; everyone sees what was edited.
   Edits are applied on the server, so a change's reply carries the records again: they're put in place of the page's
   (store.js, #271), which draws again what shows them, and the panel shows what it showed. */
const WATCH_KINDS = new Set(["exercise", "ecg", "rhythm"]);   // events made from the watch summary, not records (#182)
const canEdit = () =>!!PROFILE && (PROFILE.role === "owner" || PROFILE.role === "editor");
const editsOf = e => (DATA.edits || []).filter(x => x.ids.includes(e.id));
const quoted = t => `“${t || "an event"}”`;
// the kinds an entry can move between (#237; server/edits.py MOVABLE), in the order the choice lists them
const MOVABLE = ["visit", "contact", "procedure", "imaging", "study", "message", "appt_msg", "auto_msg", "letter", "document"];
const batchOf = x => (x.value || {}).batch ? (DATA.edits || []).filter(y => (y.value || {}).batch === x.value.batch).length : 1;
function describeEdit(x) {
  const was = x.was || {}, v = x.value || {};
  if (x.action === "hide") return `Hidden: ${quoted(was.title)}`;
  if (x.action === "rename") return `Renamed ${quoted(was.title)} to ${quoted(v.title)}`;
  if (x.action === "date") return `Date of ${quoted(was.title)} changed${was.t ? " from " + fmtDT(was.t) : ""} to ${fmtDT(v.t)}`;
  if (x.action === "not_stay") return `${quoted(was.title)} marked as not a hospital stay`;
  if (x.action === "merge") return `${quoted((x.other || v.other || {}).title)} merged into ${quoted(was.title)}`;
  if (x.action === "kind") return `${quoted(was.title)} moved to ${KIND_LABEL[v.k] || v.k}`;
  if (x.action === "kind_rule") return `Rule: entries titled ${quoted(v.title)}${v.label ? " from " + v.label : ""} go to ${KIND_LABEL[v.k] || v.k}`
    + ` (${x.ids.length} now)`;
  return "Edited";
}
/* reply: the change's, with the records (none left: the page again, which says so); reopen: what the panel shows
   now, {event} (or {alt}, a result linked away: the other side's day) if it's still there, else every edit */
function afterEdit(reopen, reply) {
  if (!reply?.records) { location.reload(); return; }
  const was = drawer.classList.contains("open") ? state.selEv : undefined;
  storeRecords(reply.records);
  const e = byId.get(reopen.event) || byId.get(reopen.alt);
  if (e && e.id === was) drawerKeep(() => openEvent(e));   // the same record: as it was, scrolled to where it was (#404)
  else if (e) openEvent(e); else openEdits();
  announce("Saved.");
}
async function sendEdit(path, opts, msg, reopen) {
  sayIn(msg, "Saving…");
  let r, j;
  try { r = await api(`profiles/${PROFILE.id}/edits${path}`, opts); j = await r.json().catch(() => ({})); }
  catch (err) { sayIn(msg, "Couldn't reach the server: " + err.message, true); return; }
  if (!r.ok) { sayIn(msg, typeof j.detail === "string" ? j.detail : `Couldn't save the edit (${r.status}).`, true); return; }
  afterEdit(reopen, j);
}
function undoButton(x, msg, reopen) {
  const n = batchOf(x);   // made at once to similar entries (#237): undone together
  // what "all" is, in its name and its tip (a title was on no screen, #371); a lone Undo beside its edit says it all
  const all = n > 1 ? `this edit and the ${n - 1} made with it` : null;
  return el("button", {class: "btn small", type: "button", text: n > 1 ? `Undo all ${n}` : "Undo", "data-edit": x.id,
    "aria-label": all && `Undo all ${n}: ${all}`, "data-tip": all && all[0].toUpperCase() + all.slice(1),
    onclick: () => sendEdit("/" + x.id, {method: "DELETE"}, msg, reopen)});
}
/* the entries that look like this one (the same source, kind and title), offered for the same change (#237):
   [the node, {ids(), rule()}]; with `rule`, also "keep doing this for new imports" */
function similarChoice(e, rule) {
  const box = el("div", {class: "similar", "aria-live": "polite"}, el("p", {class: "hint", text: "Looking for similar entries…"}));
  const got = {ids: () => [], rule: () => false};
  api(`profiles/${PROFILE.id}/edits/similar?event=${e.id}`).then(r => r.ok ? r.json() : null).then(j => {
    if (!j) { box.replaceChildren(); return; }
    const from = j.label ? ` from ${j.label}` : "", kids = [];
    if (j.items.length) {
      const checks = j.items.map(x => el("input", {type: "checkbox", value: x.id, "aria-label": `${fmtDT(x.t)} · ${x.title}`}));
      const all = el("input", {type: "checkbox", "data-similar": "all"});
      [all, ...checks].forEach(c => c.checked = true);
      all.onchange = () => checks.forEach(c => c.checked = all.checked);
      checks.forEach(c => c.onchange = () => { all.checked = checks.every(x => x.checked); all.indeterminate = !all.checked && checks.some(x => x.checked); });
      const n = j.items.length;
      kids.push(el("label", {class: "check"}, all, el("span", {text: `Also the ${n} other ${n === 1 ? "entry" : "entries"} titled ${quoted(e.title)}${from}`})),
        el("details", {}, el("summary", {text: `See ${n === 1 ? "it" : "them"}`}),
          el("ul", {class: "similar-list"}, ...j.items.map((x, i) => el("li", {}, el("label", {class: "check"}, checks[i], el("span", {text: fmtDT(x.t)})))))));
      got.ids = () => checks.filter(c => c.checked).map(c => +c.value);
    }
    if (rule) {
      const r = el("input", {type: "checkbox", "data-similar": "rule"});
      kids.push(el("label", {class: "check"}, r, el("span", {text: `Keep doing this for new imports${from}`})));
      got.rule = () => r.checked;
    }
    box.replaceChildren(...kids);
  }).catch(() => box.replaceChildren());
  return [box, got];
}
/* the open record's corrections: the ⋯ menu's (drawerMenu), a form opened from it at the top of the body, then what
   was corrected (with Undo) and what's being saved; null when there's nothing to show and it can't be corrected */
function editPanel(e) {
  const box = el("div", {class: "edits"}), msg = el("p", {class: "hint edit-msg", role: "status"});
  const mine = editsOf(e);
  if (mine.length) box.append(el("div", {class: "edited"}, el("b", {text: "Edited"}),
    ...mine.map(x => el("div", {class: "edit-line"}, el("span", {text: describeEdit(x) + (x.by ? ` · by ${x.by}` : "")}),
      canEdit() ? undoButton(x, msg, {event: e.id}) : null))));
  if (!canEdit()) return mine.length ? box : null;
  const forms = el("div", {class: "edit-form"});
  const post = (body, reopen = {event: e.id}) => sendEdit("", {method: "POST", body: JSON.stringify({event: e.id, ...body})}, msg, reopen);
  const form = (label, input, onsave, ...extra) => {
    forms.replaceChildren(el("label", {}, el("span", {text: label}), input), ...extra,
      el("div", {}, el("button", {class: "btn primary", type: "button", text: "Save", onclick: onsave}),
        el("button", {class: "btn", type: "button", text: "Cancel", onclick: () => { forms.replaceChildren(); $("#dmoreBtn").focus(); }})));
    $("#dbody").scrollTop = 0; input.focus();
  };
  drawerMenu([
    {text: "Hide", tip: "Leave this event out of the records", run: () => post({action: "hide"}, {edits: true})},
    {text: "Rename", run: () => {
      const i = el("input", {type: "text", maxlength: 300, "aria-label": "New title"}); i.value = e.title;
      const [box, got] = similarChoice(e, false);
      form("New title", i, () => post({action: "rename", title: i.value, ...(got.ids().length ? {also: got.ids()} : {})}), box);
    }},
    MOVABLE.includes(e.k) ? {text: "Change category", run: () => {
      const sel = el("select", {"aria-label": "Category"}, ...MOVABLE.filter(k => k !== e.k).map(k => el("option", {value: k, text: KIND_LABEL[k] || k})));
      const [box, got] = similarChoice(e, true);
      form(`Move this ${(KIND_LABEL[e.k] || "entry").toLowerCase()} to`, sel, () => {
        const also = got.ids(), rule = got.rule();
        post({action: "kind", k: sel.value, ...(also.length ? {also} : {}), ...(rule ? {rule} : {})});
      }, box);
    }} : null,
    {text: "Fix date", run: () => {
      const i = el("input", {type: "datetime-local", "aria-label": "New date and time"}); i.value = String(e.t || "").slice(0, 16);
      form("Starts", i, () => post({action: "date", t: i.value}));
    }},
    e.k === "stay" ? {text: "Not a hospital stay", run: () => post({action: "not_stay"})} : null,
    {text: "Merge with…", run: () => {
      const near = DATA.events.filter(x => x.id !== e.id && !WATCH_KINDS.has(x.k) &&Math.abs(x.ts - e.ts) <= 7 * DAY)
        .sort((a, b) => Math.abs(a.ts - e.ts) - Math.abs(b.ts - e.ts)).slice(0, 50);
      if (!near.length) { sayIn(msg, "No other event within a week of this one to merge with."); return; }
      const sel = el("select", {"aria-label": "Event to merge into this one"},
        ...near.map(x => el("option", {value: x.id, text: `${fmtDT(x.ts)} · ${KIND_LABEL[x.k] || x.k} · ${x.title}`})));
      form("Fold this event into the open one (it keeps its own title and date)", sel, () => post({action: "merge", other: +sel.value}));
    }},
    (DATA.edits || []).length + (DATA.same || []).length ? {text: `All edits (${(DATA.edits || []).length + (DATA.same || []).length})`, run: openEdits} : null]);
  box.prepend(forms); box.append(msg);
  return box;
}
/* every edit to these records, including those whose event is gone (its import undone, or changed at the source) */
function openEdits() {
  const list = DATA.edits || [], msg = el("p", {class: "hint edit-msg", role: "status"}), links = sameLines(msg);
  state.selEv = null;
  const n = list.length + links.length;
  const body = drawerHead(null, "Edits to these records", n ? `${n} edit${n === 1 ? "" : "s"}, applied every time the records load` : "No edits");
  body.append(el("div", {class: "edits"}, ...list.map(x => {
    const ev = x.found && x.action !== "hide" ? byId.get(x.ids[0]) : null;
    return el("div", {class: "edit-line"},
      el("span", {}, describeEdit(x), x.by ? ` · by ${x.by}` : "", x.found ? null : el("em", {class: "missing", text: " · not found in the current records"})),
      ev ? el("button", {class: "btn small", type: "button", text: "Open", onclick: () => openEvent(ev)}) : null,
      canEdit() ? undoButton(x, msg, {edits: true}) : null);
  }), ...links, msg));
  showDrawer();
  renderTimeline();
}

