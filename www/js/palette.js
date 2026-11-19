/* Sanguity · the command palette (#273): one search box, on every page with the top bar, over what you can go to or do.
   Ctrl+K (⌘K on a Mac), "/" when you're not typing in a field, or the top bar's Search (nav.js, barSearch) opens it,
   a <dialog> (openDialog, util.js). It finds, in groups:
   - Actions: what the top bar's Add menu offers here (Log a dose, Journal entry, Upload files…, as that menu shows
     them, so someone who can only view a profile isn't offered them), New share link (for the profile's owners), New
     profile (the profile menu's) and light or dark mode;
   - Records tabs: the tabs the profile shows (nav.js's links to them);
   - Pages and settings: every page with a sidebar and every group in it (groups.js, the list those sidebars are drawn
     from), and the parts of a group worth going to by name (Password, Units…): Admin's for admins, Sharing's by the
     profile's role, as their sidebars list them;
   - Labs: the lab names (by the name in the records, the name shown, the panel), opened as a chart on the Labs tab;
   - Events: the Timeline's index (the words its search box finds: an event's title, what it's about, its kind, a lab
     day's results' names), each opened in the drawer on the Timeline, most recent first, and a search of the Timeline
     for what's typed when there are more.
   One ranking runs across them all (#403): a title starting with what's typed first, then one with each word typed
   starting a word of it, then what else it's known by (groups.js's words, Add's, a tab's, Log a dose's medications),
   always at the start of a word; the group with the best match comes first. Offline (offline.js) what needs the server
   isn't listed, and it says so.
   On the records page Labs and Events come from the records shown (store.js). On another page they come from the API:
   the profile's records (GET profiles/{pid}/data, and its settings for the names labs are shown by) are read once,
   the first time the palette opens there (again after another profile is chosen), and choosing one opens the records
   page there, which does the rest (resume(), from app.js once the records are drawn; sessionStorage says what).
   Nothing is fetched until the palette opens.

   It's a combobox and a listbox (WAI-ARIA's pattern): focus stays in the box while the up and down arrows move through
   what's found (aria-activedescendant; the first is marked from the start, so Enter opens the best match), Enter or a
   click opens it, Escape or Close closes the palette, and focus goes back to what had it (opened from a menu, to the
   menu's button). How many it found is said once typing stops. "/" can be turned off by the switch at its foot (a
   key on its own, WCAG 2.1.4), for this browser.
   SgPalette: open(q, typed in it first), resume(). Plain script, after nav.js and groups.js; all files share one scope. */
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
    return {labs: [...by.values()], events, meds: current(d.medications)};
  }
  /* the names of the medications the records list as current (meds.js mrCurrent), Log a dose's words */
  const current = ms => (Array.isArray(ms) ? ms : []).filter(m => m && m.spans?.at?.(-1)?.open).map(m => m.name);
  const medNames = () => (onRecords ? (recs() ? current(DATA.medications) : []) : REC?.meds || []).join(" ");

  /* ---- what it finds: {icon (a name or an element), title, ctx (where or what it is), words, go()} */
  function actions() {
    const out = [], add = document.getElementById("addMenu");
    if (add && !add.hidden) for (const x of add.querySelectorAll("[role^=menuitem]")) {
      if (x.hidden || x.closest("[role=group]")?.hidden) continue;
      out.push({icon: x.querySelector("svg.icon"), title: x.textContent.trim(), ctx: "Add", go: () => x.click()});
    }
    if (profile()?.role === "owner") out.push({icon: sgIcon("sharing/links"), title: "New share link", ctx: "Sharing", go: newLink});
    // the profile menu's New profile, its only one (#402), even where that menu isn't shown (no profile yet)
    const np = document.getElementById("newProfileLink");
    if (np && !document.getElementById("barSearch").hidden) out.push({icon: sgIcon("sharing/newProfile"), title: "New profile", ctx: "Profile menu", words: "create add person",
      go: () => { location.href = np.href; }});
    const th = document.getElementById("theme");
    if (th && !th.hidden) out.push({icon: sgIcon("theme"), title: th.textContent.trim(), ctx: "Your menu", words: "theme dark light", go: () => th.click()});
    return out;
  }
  /* what else the actions are known by (#403): Add's ways in, as their Sources groups (groups.js), the rest here, and
     Log a dose by the names of the profile's current medications; and whether one opens another page, which needs
     the server (New share link: Sharing) */
  const ADD_WORDS = {doseLink: "medication meds medicine pill tablet dose took taken", journalBtn: "note diary symptom feeling",
    "home-reading": "blood pressure weight glucose temperature"};
  function known(xs) {
    const items = [...document.querySelectorAll("#addMenu [role^=menuitem]")];
    return xs.map(x => {
      const m = items.find(i => i.textContent.trim() === x.title);
      if (!m) return x.go === newLink ? {...x, server: true} : x;
      const href = m.getAttribute("href") || "", hash = href.split("#")[1] || "", g = SG_PAGES.sources.groups.find(g => g.id === hash);
      return {...x, server: !!href && !href.startsWith("#"),
        words: [x.words, ADD_WORDS[m.id] || ADD_WORDS[hash], g && g.words, m.id === "doseLink" && medNames()].filter(Boolean).join(" ")};
    });
  }
  const TAB_WORDS = {overview: "home dashboard summary tiles", health: "body vitals steps sleep heart weight", timeline: "events history",
    labs: "tests results charts blood work", costs: "money bills claims payments insurance", meds: "meds medication medicine prescriptions"};
  function tabs() {
    if (document.querySelector("header.topbar nav.tabs")?.hidden) return [];
    return SgNav.TABS.map(t => document.getElementById("tab-" + t)).filter(a => a && !a.hidden)
      .map(a => ({icon: sgIcon(a.id.slice(4)), title: a.textContent.trim(), ctx: "Records tab", words: TAB_WORDS[a.id.slice(4)], go: () => a.click()}));
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
        for (const [title, id, words] of g.finds || []) out.push({icon: sgIcon(P.page.replace(".html", "/") + id), title, ctx: `${P.title} › ${g.title}`, words, go: () => goTo(P, id, true)});
      }
    }
    if (page !== "guide.html") out.push({icon: sgIcon("guide"), title: "Getting started", ctx: "Page", words: "guide first records", go: () => { location.href = "guide.html" + (pid() ? "?profile=" + pid() : ""); }});
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

  /* how well something matches what's typed (#403), the same in every group, lower better: 0 its title starts with
     it all, 1 each word typed starts a word of the title, 2 the first does, 3 only what else it's known by has them;
     -1 not at all. A word typed is found only at the start of a word, never inside one ("hem" isn't in "theme").
     Words are letters and digits: "two-factor" is "two factor", as "mg/dL" is "mg dl" */
  const flat = s => " " + norm(s).replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  function score(title, also, words) {
    const t = flat(title), hay = t + flat(also), at = (h, w) => h.includes(" " + w);
    if (!words.every(w => at(hay, w))) return -1;
    return t.startsWith(" " + words.join(" ")) ? 0 : words.every(w => at(t, w)) ? 1 : at(t, words[0]) ? 2 : 3;
  }
  /* xs scored (x.s, or its title, then where it is and its words), best first, then as given or by more, at most
     cap: [[score, x]] */
  const ranked = (xs, words, cap, more = () => 0) => xs.map(x => [x.s ?? score(x.title, [x.ctx, x.words].join(" "), words), x])
    .filter(([n]) => n >= 0).sort((a, b) => a[0] - b[0] || more(a[1], b[1])).slice(0, cap);
  /* the groups found for what's typed: [heading, entries], those with the best match first, so the one marked (what
     Enter opens) is the best of all; and a note to show, if any. Offline (offline.js, on the records page) what needs
     the server isn't listed, as the menus don't follow their links to it, and the note says so */
  function find(typed) {
    const words = flat(typed).trim().split(" ").filter(Boolean), off = typeof OFFLINE !== "undefined" && OFFLINE.on;
    const acts = known(actions()).filter(x => !off || !x.server), pgs = all => off ? [] : pages(all);   // (every page is another)
    const offNote = off ? "Pages and settings aren't listed: they need the server, and you're offline." : "";
    if (!words.length) return {groups: [["Actions", acts], ["Records tabs", tabs()], ["Pages", pgs(false)]], offNote};
    const groups = [["Actions", ranked(acts, words, 8)], ["Records tabs", ranked(tabs(), words, 8)], ["Pages and settings", ranked(pgs(true), words, 10)]];
    const L = labs(), E = events();
    if (L) groups.push(["Labs", ranked(L.map(s => ({icon: "test-tube-diagonal", title: s.shown, n: s.n, s: score(s.shown, [s.name, s.panel].join(" "), words),
      ctx: [s.shown !== s.name && "In the records: " + s.name, `${s.n} result${s.n === 1 ? "" : "s"}`].filter(Boolean).join(" · "), go: () => openLab(s.name)})),
      words, 8, (a, b) => b.n - a.n)]);
    if (E) {
      // the Timeline's own search: the whole of what's typed in an event's words (core.js matchQ); listed, those with
      // each word at the start of one of theirs, the most recent first; the rest through the Timeline's search
      const lq = typed.trim().toLowerCase(), hit = E.filter(e => e._q.includes(lq)), kind = k => typeof KIND_LABEL !== "undefined" && KIND_LABEL[k];
      const found = ranked(hit.map(e => ({icon: typeof kindIcon === "function" ? kindIcon(e.k) : "file-text", title: e.title, ts: e.ts || 0, s: score(e.title, e._q, words),
        ctx: [kind(e.k), day(e.ts || e.t)].filter(Boolean).join(" · "), go: () => openEv(e.id)})), words, 6, (a, b) => b.ts - a.ts);
      if (hit.length > found.length) found.push([found.length ? found.at(-1)[0] : 3, {icon: "arrow-right", title: `Search the Timeline for “${typed.trim()}”`,
        ctx: `${hit.length.toLocaleString()} event${hit.length === 1 ? "" : "s"} in all`, go: () => timeline(typed.trim())}]);
      groups.push(["Events", found]);
    }
    const best = xs => xs.length ? xs[0][0] : 9;   // (the sort keeps the order above for a tie)
    return {groups: groups.sort((a, b) => best(a[1]) - best(b[1])).map(([h, xs]) => [h, xs.map(([, x]) => x)]), offNote,
      wait: !onRecords && pid() && !REC ? "Looking through the records…" : !onRecords && REC?.failed ? "The records couldn't be searched just now." : ""};
  }

  /* ---- the list */
  let shown = [], active = -1, sayT = 0;
  function draw() {
    const typed = input.value, {groups, wait, offNote} = find(typed);
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
    const nothing = typed.trim() && !shown.length && !wait ? `Nothing matches “${typed.trim()}”${offNote ? " offline" : ""}.` : "";
    note.textContent = [wait, nothing, offNote].filter(Boolean).join(" ");
    note.hidden = !note.textContent;
    clearTimeout(sayT);
    if (typed.trim()) sayT = setTimeout(() => announce(nothing ? [nothing, offNote].join(" ").trim() : shown.length ? `${shown.length} found.` : wait), 700);
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
  /* opened from a menu (Add open, then "/" or Ctrl+K), what had focus is an item in a list now closed: focus goes back
     to the menu's button instead (#403) */
  let menuBtn = null;
  dlg.addEventListener("close", () => {
    clearTimeout(sayT);
    const a = document.activeElement;
    if (menuBtn && menuBtn.isConnected && (!a || a === document.body || dlg.contains(a))) menuBtn.focus({preventScroll: true});
    menuBtn = null;
  });

  /* open(q): with q typed (a sidebar's "Search everywhere", sidebar.js), else empty */
  function open(q = "") {
    if (btn.hidden || btn.closest("[hidden]")) return;   // the bar locked (account.js: a password to change first)
    if (dlg.open) { input.select(); return; }
    if (document.querySelector("dialog:modal")) return;   // not over a question waiting for an answer, or the drawer over the page
    who();
    records();
    const pop = document.activeElement?.closest?.("[popover]");
    menuBtn = pop ? [...document.querySelectorAll("button")].find(b => b.popoverTargetElement === pop) || null : null;
    input.value = typeof q === "string" ? q : "";   // (not a click's event, from the Search button)
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
    if (location.hash === "#" + id) dispatchEvent(new HashChangeEvent("hashchange", {oldURL: location.href, newURL: location.href}));   // (the page's listeners read newURL: import-records.js)
    else location.hash = id;
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
