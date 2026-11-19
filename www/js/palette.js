/* Sanguity · the command palette (#273): one search box, on every page with the top bar, over what you can go to or do.
   Ctrl+K (⌘K on a Mac), "/" when you're not typing in a field, or the top bar's Search (nav.js, barSearch) opens it,
   a <dialog> (openDialog, util.js). It finds, in groups:
   - Actions: what the top bar's Add menu offers here (Log a dose, Journal entry, Upload files…, as that menu shows
     them, so someone who can only view a profile isn't offered them), New share link (for the profile's owners) and
     light or dark mode;
   - Records tabs: the tabs the profile shows (nav.js's links to them);
   - Pages and settings: every page with a sidebar and every group in it (groups.js, the list those sidebars are drawn
     from), and the parts of a group worth going to by name (Password, Units…): Admin's for admins, Sharing's by the
     profile's role, as their sidebars list them;
   - Labs: the lab names (by the name in the records, the name shown, the panel), opened as a chart on the Labs tab;
   - Events: the Timeline's index (the words its search box finds: an event's title, what it's about, its kind, a lab
     day's results' names), each opened in the drawer on the Timeline, most recent first, and a search of the Timeline
     for what's typed when there are more.
   On the records page Labs and Events come from the records shown (store.js). On another page they come from the API:
   the profile's records (GET profiles/{pid}/data, and its settings for the names labs are shown by) are read once,
   the first time the palette opens there (again after another profile is chosen), and choosing one opens the records
   page there, which does the rest (resume(), from app.js once the records are drawn; sessionStorage says what).
   Nothing is fetched until the palette opens.

   It's a combobox and a listbox (WAI-ARIA's pattern): focus stays in the box while the up and down arrows move through
   what's found (aria-activedescendant; the first is marked from the start, so Enter opens the best match), Enter or a
   click opens it, Escape or Close closes the palette, and focus goes back to what had it. How many it found is said
   once typing stops. "/" can be turned off by the switch at its foot (a key on its own, WCAG 2.1.4), for this browser.
   SgPalette: open(), resume(). Plain script, after nav.js and groups.js; all files share one scope. */
"use strict";
const SgPalette = (() => {
  const btn = document.getElementById("barSearch");
  if (!btn || typeof SG_PAGES === "undefined") return {open() {}, resume() {}};
  const onRecords = !!document.getElementById("view-overview"), page = location.pathname.split("/").pop() || "index.html";
  const KEY = "sg-palette";   // sessionStorage: what to do once another page is there ({do, v, pid})
  const norm = s => String(s || "").toLowerCase().normalize("NFD").replace(/\p{M}/gu, "");
  const pid = () => document.getElementById("profile")?.value || "";
  const day = t => typeof fmtD === "function" ? fmtD(t) : String(t).slice(0, 10);   // dates.js, where the page has it
  const recs = () => onRecords && typeof DATA !== "undefined" && DATA;   // store.js: the records page's, once loaded
  /* "/" is a key on its own, so it can be turned off (WCAG 2.1.4: speech input can type it), by the switch at the
     palette's foot; this browser remembers */
  const slashOn = () => { try { return localStorage.getItem("sg-palette-slash") !== "off"; } catch (e) { return true; } };
  /* the keys that open it, in the Search button's tip and said with its name */
  const mac = /Mac|iP(hone|ad|od)/.test(navigator.platform || "");
  function keys() {
    btn.dataset.tip = `Search (${mac ? "⌘K" : "Ctrl+K"}${slashOn() ? " or /" : ""})`;
    btn.setAttribute("aria-keyshortcuts", (mac ? "Meta+K" : "Control+K") + (slashOn() ? " /" : ""));
  }
  keys();

  /* ---- the dialog */
  const input = el("input", {type: "text", id: "palQ", role: "combobox", "aria-expanded": "false", "aria-controls": "palList",
    "aria-autocomplete": "list", "aria-label": "Search", autocomplete: "off", spellcheck: "false", placeholder: "Search records, labs, pages and settings"});
  const list = el("div", {class: "pal-list", id: "palList", role: "listbox", "aria-label": "Found"});
  const note = el("p", {class: "pal-note", hidden: ""});
  const dlg = el("dialog", {class: "palette", "aria-label": "Search"},
    el("div", {class: "pal-head"}, icon("search"), input,
      el("button", {class: "btn quiet", type: "button", "aria-label": "Close", "data-tip": "Close (Esc)", onclick: () => dlg.close()}, icon("x"))),
    list, note,
    el("div", {class: "pal-foot"}, el("span", {class: "pal-hint", "aria-hidden": "true"}, icon("arrow-up"), icon("arrow-down"), " to move · Enter to open · Esc to close"),
      el("label", {class: "switch"}, el("input", {type: "checkbox", role: "switch", id: "palSlash", checked: slashOn() || null,
        onchange: ev => { try { localStorage.setItem("sg-palette-slash", ev.target.checked ? "on" : "off"); } catch (e) {} keys(); }}), "Open with “/”")));
  document.body.append(dlg);

  /* ---- who you are, and the records of another page's profile, fetched when it first opens */
  let ME = null, meWait = null, REC = null, recFor = null;
  const who = () => meWait ||= api("me").then(r => r.ok ? r.json() : null).catch(() => null).then(m => { ME = m; if (dlg.open) draw(); });
  const profile = () => ME?.profiles?.find(p => String(p.id) === pid()) || null;
  const RANK = {viewer: 0, editor: 1, owner: 2};
  document.addEventListener("change", ev => { if (ev.target.id === "profile") recFor = REC = null; });
  function records() {
    const p = pid();
    if (onRecords || !p || recFor === p) return;
    recFor = p; REC = null;
    const json = r => r.status === 200 ? r.json() : null;
    Promise.all([api(`profiles/${p}/data`).then(json), api(`profiles/${p}/settings`).then(r => r.ok ? r.json() : {}).catch(() => ({}))])
      .then(([d, s]) => { if (recFor === p) REC = d ? fromData(d, s) : {labs: [], events: []}; })
      .catch(() => { if (recFor === p) REC = {labs: [], events: [], failed: true}; })
      .finally(() => { if (dlg.open) draw(); });
  }
  /* the API's records as the palette needs them: the labs' names, and each event with the words the Timeline searches */
  function fromData(d, s) {
    const nick = s && s.labNicknames && typeof s.labNicknames === "object" ? s.labNicknames : {}, by = new Map(), title = new Map();
    for (const x of d.labs || []) if (x && x.name && (x.pts || []).length) {
      const have = by.get(x.name);
      if (have) have.n += x.pts.length; else by.set(x.name, {name: x.name, shown: nick[x.name] || x.name, panel: x.panel || "", n: x.pts.length});
    }
    for (const e of d.events || []) title.set(e.id, e.title || "");
    const events = (d.events || []).map(e => ({id: e.id, k: e.k, title: e.title || "", ts: Date.parse(e.t), t: e.t,
      _q: [e.title, e.sub, ...(e.k === "lab" ? (d.details?.[e.id]?.ids || []).map(i => title.get(i)) : [])].filter(Boolean).join(" ").toLowerCase()}));
    return {labs: [...by.values()], events};
  }

  /* ---- what it finds: {icon (a name or an element), title, ctx (where or what it is), words, go()} */
  const TAB_ICON = {overview: "layout-grid", health: "activity", timeline: "calendar", labs: "test-tube-diagonal", costs: "banknote"};
  function actions() {
    const out = [], add = document.getElementById("addMenu");
    if (add && !add.hidden) for (const x of add.querySelectorAll("[role^=menuitem]")) {
      if (x.hidden || x.closest("[role=group]")?.hidden) continue;
      out.push({icon: x.querySelector("svg.icon"), title: x.textContent.trim(), ctx: "Add", go: () => x.click()});
    }
    if (profile()?.role === "owner") out.push({icon: "share-2", title: "New share link", ctx: "Sharing", go: newLink});
    const th = document.getElementById("theme");
    if (th && !th.hidden) out.push({icon: "sun-moon", title: th.textContent.trim(), ctx: "Your menu", words: "theme dark light", go: () => th.click()});
    return out;
  }
  function tabs() {
    if (document.querySelector("header.topbar nav.tabs")?.hidden) return [];
    return SgNav.TABS.map(t => document.getElementById("tab-" + t)).filter(a => a && !a.hidden)
      .map(a => ({icon: TAB_ICON[a.id.slice(4)], title: a.textContent.trim(), ctx: "Records tab", go: () => a.click()}));
  }
  /* the pages (not the one you're on) and, when something's typed, their groups and named parts */
  function pages(all) {
    const out = [], role = RANK[profile()?.role] ?? -1;
    for (const P of Object.values(SG_PAGES)) {
      if (P.admin && !ME?.user?.is_admin) continue;
      if (P.page !== page) out.push({icon: P.icon, title: P.title, ctx: "Page", words: P.words, go: () => goTo(P, "")});
      if (!all) continue;
      for (const g of P.groups) {
        if (g.rest || (g.needs && role < RANK[g.needs])) continue;
        out.push({icon: g.icon, title: g.title, ctx: P.title, words: g.words, go: () => goTo(P, g.id)});
        for (const [title, id, words] of g.finds || []) out.push({icon: g.icon, title, ctx: `${P.title} › ${g.title}`, words, go: () => goTo(P, id, true)});
      }
    }
    if (page !== "guide.html") out.push({icon: "compass", title: "Getting started", ctx: "Page", words: "guide first records", go: () => { location.href = "guide.html" + (pid() ? "?profile=" + pid() : ""); }});
    return out;
  }
  /* the labs: null while another page's are still on their way */
  function labs() {
    if (!onRecords) return REC && REC.labs;
    if (!recs()) return [];
    const seen = new Map();
    for (const s of SERIES) if (!s.watch && s.pts.length) {
      const have = seen.get(s.name);
      if (have) have.n += s.pts.length; else seen.set(s.name, {name: s.name, shown: nice(s.name), panel: s.panel || "", n: s.pts.length});
    }
    return [...seen.values()];
  }
  const events = () => onRecords ? (recs() ? EV : []) : REC && REC.events;

  /* how well x matches: every word typed somewhere in it, then its title starting with what's typed, a word of the
     title starting with it, the title holding it, or only what else it's known by (-1: not at all) */
  function score(title, hay, words, q) {
    const t = norm(title);
    if (!words.every(w => hay.includes(w))) return -1;
    return t.startsWith(q) ? 0 : (" " + t).includes(" " + words[0]) ? 1 : t.includes(words[0]) ? 2 : 3;
  }
  const ranked = (xs, q, words, cap) => xs.map(x => [score(x.title, norm([x.title, x.ctx, x.words].join(" ")), words, q), x])
    .filter(([s]) => s >= 0).sort((a, b) => a[0] - b[0]).slice(0, cap).map(([, x]) => x);
  /* the groups found for q: [heading, entries]; and a note to show, if any */
  function find(typed) {
    const q = norm(typed), words = q.split(/\s+/).filter(Boolean);
    if (!q) return {groups: [["Actions", actions()], ["Records tabs", tabs()], ["Pages", pages(false)]]};
    const groups = [["Actions", ranked(actions(), q, words, 8)], ["Records tabs", ranked(tabs(), q, words, 8)],
      ["Pages and settings", ranked(pages(true), q, words, 10)]];
    const L = labs(), E = events();
    if (L) groups.push(["Labs", L.map(s => [score(s.shown, norm([s.name, s.shown, s.panel].join(" ")), words, q), s]).filter(([n]) => n >= 0)
      .sort((a, b) => a[0] - b[0] || b[1].n - a[1].n).slice(0, 8).map(([, s]) => ({icon: "test-tube-diagonal", title: s.shown,
        ctx: [s.shown !== s.name && "In the records: " + s.name, `${s.n} result${s.n === 1 ? "" : "s"}`].filter(Boolean).join(" · "), go: () => openLab(s.name)}))]);
    if (E) {
      // the Timeline's own search: the whole of what's typed in an event's words (core.js matchQ)
      const lq = typed.trim().toLowerCase(), hit = E.filter(e => e._q.includes(lq)), CAP = 6;
      const top = hit.map(e => [norm(e.title).startsWith(q) ? 0 : norm(e.title).includes(q) ? 1 : 2, e]).sort((a, b) => a[0] - b[0] || (b[1].ts || 0) - (a[1].ts || 0)).slice(0, CAP);
      const kind = k => typeof KIND_LABEL !== "undefined" && KIND_LABEL[k];
      const found = top.map(([, e]) => ({icon: typeof kindIcon === "function" ? kindIcon(e.k) : "file-text", title: e.title,
        ctx: [kind(e.k), day(e.ts || e.t)].filter(Boolean).join(" · "), go: () => openEv(e.id)}));
      if (hit.length > CAP) found.push({icon: "arrow-right", title: `Search the Timeline for “${typed.trim()}”`, ctx: `${hit.length.toLocaleString()} events in all`, go: () => timeline(typed.trim())});
      groups.push(["Events", found]);
    }
    return {groups, wait: !onRecords && pid() && !REC ? "Looking through the records…" : !onRecords && REC?.failed ? "The records couldn't be searched just now." : ""};
  }

  /* ---- the list */
  let shown = [], active = -1, sayT = 0;
  function draw() {
    const typed = input.value, {groups, wait} = find(typed);
    shown = [];
    list.replaceChildren(...groups.filter(([, xs]) => xs.length).map(([head, xs], g) => el("div", {role: "group", "aria-labelledby": "palG" + g},
      el("div", {class: "pal-sec", id: "palG" + g, role: "presentation", text: head}),
      ...xs.map(x => {
        const i = shown.length, o = el("div", {class: "pal-opt", role: "option", id: "palO" + i, "aria-selected": "false",
          onclick: () => pick(i), onpointermove: () => mark(i, false)},
          x.icon && x.icon.nodeType ? x.icon.cloneNode(true) : icon(x.icon || "arrow-right"), el("span", {class: "pal-t", text: x.title}),
          x.ctx ? el("span", {class: "pal-c"}, el("span", {class: "vh", text: ", "}), x.ctx) : null);
        shown.push([o, x]);
        return o;
      }))));
    input.setAttribute("aria-expanded", String(shown.length > 0));
    mark(shown.length ? 0 : -1, false);
    const nothing = typed.trim() && !shown.length && !wait ? `Nothing matches “${typed.trim()}”.` : "";
    note.textContent = [wait, nothing].filter(Boolean).join(" ");
    note.hidden = !note.textContent;
    clearTimeout(sayT);
    if (typed.trim()) sayT = setTimeout(() => announce(nothing || (shown.length ? `${shown.length} found.` : wait)), 700);
  }
  /* the option marked: the one Enter opens, and the one a screen reader says */
  function mark(i, scroll = true) {
    if (shown[active]) shown[active][0].setAttribute("aria-selected", "false");
    active = i;
    const o = shown[i] && shown[i][0];
    if (!o) { input.removeAttribute("aria-activedescendant"); return; }
    o.setAttribute("aria-selected", "true");
    input.setAttribute("aria-activedescendant", o.id);
    if (scroll) o.scrollIntoView({block: "nearest"});
  }
  function pick(i) {
    const x = shown[i] && shown[i][1];
    if (!x) return;
    dlg.close();   // first: focus goes back to what had it, and the page is no longer inert
    x.go();
  }
  input.addEventListener("input", draw);
  input.addEventListener("keydown", ev => {
    const n = shown.length;
    if ((ev.key === "ArrowDown" || ev.key === "ArrowUp") && n) { ev.preventDefault(); mark((active + (ev.key === "ArrowDown" ? 1 : n - 1)) % n); }
    else if ((ev.key === "PageDown" || ev.key === "PageUp") && n) { ev.preventDefault(); mark(Math.max(0, Math.min(n - 1, active + (ev.key === "PageDown" ? 5 : -5)))); }
    else if (ev.key === "Enter") { ev.preventDefault(); pick(active); }
  });
  dlg.addEventListener("close", () => clearTimeout(sayT));

  function open() {
    if (btn.hidden || btn.closest("[hidden]")) return;   // the bar locked (account.js: a password to change first)
    if (dlg.open) { input.select(); return; }
    if (document.querySelector("dialog:modal")) return;   // not over a question waiting for an answer, or the drawer over the page
    who();
    records();
    input.value = "";
    document.getElementById("palSlash").checked = slashOn();   // (as another window may have set it)
    draw();
    openDialog(dlg, {as: "center"});
    input.focus();
  }
  btn.addEventListener("click", open);
  /* Ctrl+K or ⌘K anywhere; "/" anywhere but in a field you type in, unless it was turned off */
  const typing = t => t && t.matches?.("textarea, select, [contenteditable]:not([contenteditable=false]), input:not([type=checkbox],[type=radio],[type=button],[type=submit],[type=reset],[type=range],[type=color],[type=file])");
  addEventListener("keydown", ev => {
    if (ev.defaultPrevented || ev.isComposing || ev.altKey) return;
    const k = (ev.key || "").toLowerCase();
    if (k === "k" && (ev.ctrlKey || ev.metaKey) && !ev.shiftKey) { ev.preventDefault(); open(); }
    else if (ev.key === "/" && slashOn() && !ev.ctrlKey && !ev.metaKey && !typing(ev.target)) { ev.preventDefault(); open(); }
  });

  /* ---- going there */
  /* a page's group or part: on this page, through its address, as its sidebar's links do (sidebar.js follows it), then
     focus on its title (the h1 the group's name is in, or the part's heading); on another, that page there */
  function goTo(P, id, part) {
    if (P.page !== page) { location.href = P.page + (P.profile && pid() ? "?profile=" + encodeURIComponent(pid()) : "") + (id ? "#" + id : ""); return; }
    if (!id) return;
    addEventListener("hashchange", () => {
      const t = part ? document.getElementById(id)?.querySelector("h2") : document.getElementById("groupTitle");
      if (!t || t.closest("[hidden]")) return;
      if (!t.hasAttribute("tabindex")) t.tabIndex = -1;
      t.focus({preventScroll: !!part});   // a part: scrolled to by the sidebar already
    }, {once: true});
    if (location.hash === "#" + id) dispatchEvent(new HashChangeEvent("hashchange")); else location.hash = id;
  }
  /* the records page, to do the rest there (resume) */
  function arrive(what, v, hash) {
    try { sessionStorage.setItem(KEY, JSON.stringify({do: what, v, pid: pid()})); } catch (e) {}
    location.href = "./" + (pid() ? "?profile=" + encodeURIComponent(pid()) : "") + hash;
  }
  /* a lab: charted on the Labs tab, focus on its chart (or its table) */
  function openLab(name) {
    if (!onRecords) return arrive("lab", name, "#labs");
    state.sel.add(name);
    pickTab("labs", "chart");
    const i = SERIES.filter(s => !s.watch && state.sel.has(s.name)).findIndex(s => s.name === name), card = $("#charts").children[i];
    const t = card && card.querySelector("svg.pz, .tablewrap");
    if (!t || t.closest("[hidden]")) return;
    if (!t.hasAttribute("tabindex")) t.tabIndex = -1;
    t.focus();
  }
  /* an event: the Timeline, with it open in the drawer */
  function openEv(id) {
    if (!onRecords) return arrive("event", id, "#timeline");
    const e = byId.get(id);
    if (!e) return;
    pickTab("timeline");
    openEvent(e);
  }
  /* the Timeline's search, for what was typed; focus on the list's heading, which says how many it shows */
  function timeline(q) {
    if (!onRecords) return arrive("timeline", q, "#timeline");
    $("#q").value = q;
    state.q = q.toLowerCase();
    pickTab("timeline");
    const h = $("#listTitle");
    if (h) { h.tabIndex = -1; h.focus(); }
  }
  /* Sharing → Share links, and its New share link pressed once it's there, focus in the form it opens */
  function newLink() {
    if (page !== "sharing.html") {
      try { sessionStorage.setItem(KEY, JSON.stringify({do: "new-link", pid: pid()})); } catch (e) {}
      location.href = "sharing.html?profile=" + encodeURIComponent(pid()) + "#links";
      return;
    }
    if (location.hash !== "#links") goTo(SG_PAGES.sharing, "links");   // (arriving, the sidebar follows the address itself)
    waitFor(() => [...document.querySelectorAll("#linksBody button")].find(b => b.textContent === "New share link" && !b.closest("[hidden]")), b => {
      b.click();
      waitFor(() => $("#linksBody .share-form :is(input, select, textarea)"), f => f.focus());
    });
  }
  /* then(x) once test() gives x, within a few seconds (the page's own script still drawing it) */
  function waitFor(test, then, ms = 8000) {
    const x = test();
    if (x) return then(x);
    const ob = new MutationObserver(() => { const y = test(); if (y) { ob.disconnect(); clearTimeout(t); then(y); } });
    const t = setTimeout(() => ob.disconnect(), ms);
    ob.observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ["hidden"]});
  }
  /* what was chosen on another page, done here: on the records page once they're drawn (app.js), on Sharing as it loads */
  function resume() {
    let w = null;
    try { w = JSON.parse(sessionStorage.getItem(KEY) || "null"); } catch (e) {}
    if (!w || (onRecords ? w.do === "new-link" : w.do !== "new-link")) return;
    try { sessionStorage.removeItem(KEY); } catch (e) {}
    if (String(w.pid || "") !== pid()) return;   // another profile since
    ({lab: openLab, event: openEv, timeline, "new-link": newLink})[w.do]?.(w.v);
  }
  if (page === "sharing.html") document.addEventListener("DOMContentLoaded", () => waitFor(() => pid(), resume));
  return {open, resume};
})();
