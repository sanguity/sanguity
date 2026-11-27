/* Sanguity · the top bar on every signed-in page (after util.js, pwa.js and groups.js, before the page's own script).

   One row, the same on every page (#264): the brand (back to the records), the profile menu (switch profile; Sources,
   Sharing; New profile), the records tabs as links (those that don't fit under More, #265), then two menus. Add:
   today's (a dose, a journal entry, a home reading) and records in (what's waiting for you, #283; upload, connect, type
   in, a Health Link). The person's own menu: Settings (#991: the profile's, yours and, for admins, the server's, in
   one page; "backups late" beside it for an admin: nav.js asks the API itself, #200), Getting started (the first-run
   guide, #199), the light/dark toggle, log out. The items keep the ids the pages
   already use (#importLink, #journalBtn, #settingsLink, #theme, #logout, #who, #profile, #tab-labs), so each page's
   script sets them up as before; nav.js only builds the bar and its menus (menuWire, below).

   <header class="topbar" data-profile> gets the bar; data-profile says the page fills the profile menu itself
   (SgNav.profiles). Anything already in it (the records page's Install app button) stays, before the menus. */
"use strict";
/* A menu (#191, #261): a button and its list of [role^=menuitem] items (tabindex -1, replaceable while it's closed),
   for every menu in the app: the top bar's Add and your menu, the records bar's More… periods, the Timeline's kind
   menus and Settings' row menus. The list is a popover="auto": the browser opens and closes it from its button, and
   closes it on a click elsewhere, on Escape or when another menu opens, so no page writes that itself. app.css places
   it under its button (CSS anchor positioning, where the browser has it; menuPlace() below where it doesn't), fading
   in. This keeps the rest: the button's aria-expanded; Enter, Space or the down arrow on the button opens it on its
   first item; the arrow keys, Home and End move through it; Tab leaves it, closed; and when it closes with focus
   inside it (Escape, an item picked), focus goes back to the button, unless the item sent it somewhere else.
   m is {btn, list, wrap?, onOpen?, text?}; menuWire(m) gives it open() and close(), and returns it (SgNav.menu). With
   text, the list is words rather than items (Labs' Legend and ⓘ, #279): the button opens and closes it as any button
   does, and focus stays on it. */
function menuWire(m) {
  const {btn, list} = m, its = () => [...list.querySelectorAll("[role^=menuitem]")].filter(x => !x.hidden);
  list.removeAttribute("hidden");
  list.popover = "auto";
  btn.popoverTargetElement = list;   // a click toggles it, and a click on its own button isn't one "elsewhere"
  const isOpen = () => list.matches(":popover-open");
  m.open = () => { if (!isOpen() && list.isConnected) list.showPopover({source: btn}); };
  m.close = () => { if (isOpen()) list.hidePopover(); };
  let leaving = false;
  // placed before it's drawn, and again while open as the page scrolls or resizes (#356); a list taken out of the page
  // while open closes with no beforetoggle, so then its listeners go at the next scroll or resize (#450)
  const follow = () => { if (list.isConnected) return menuPlace(m); removeEventListener("scroll", follow, {capture: true}); removeEventListener("resize", follow); };
  list.addEventListener("beforetoggle", ev => {
    const on = ev.newState === "open";
    btn.setAttribute("aria-expanded", String(on));
    if (on) {
      menuPlace(m);
      addEventListener("scroll", follow, {capture: true, passive: true});
      addEventListener("resize", follow);
    } else {
      removeEventListener("scroll", follow, {capture: true});
      removeEventListener("resize", follow);
    }
    if (!on && !leaving && list.contains(document.activeElement)) btn.focus({preventScroll: true});
    leaving = false;
  });
  list.addEventListener("toggle", ev => { if (ev.newState === "open") m.onOpen?.(); });
  btn.addEventListener("keydown", ev => {
    if (m.text) return;   // words, not items (Labs' Legend and ⓘ, #279): Enter and Space are the button's own, opening and closing it
    if (ev.key !== "ArrowDown" && ev.key !== "Enter" && ev.key !== " ") return;
    ev.preventDefault();
    m.open();
    if (!m.sheet) its()[0]?.focus();   // a sheet opens on its picked item (menuSheet, below)
  });
  list.addEventListener("keydown", ev => {
    const all = its(), i = all.indexOf(document.activeElement);
    const to = {ArrowDown: all[(i + 1) % all.length], ArrowUp: all[(i - 1 + all.length) % all.length], Home: all[0], End: all.at(-1)}[ev.key];
    if (to) { ev.preventDefault(); to.focus(); }
    else if (ev.key === "Tab" && !m.sheet) { leaving = true; m.close(); }   // on to what's next, not back to the button (a sheet keeps it, below)
  });
  list.addEventListener("click", ev => { if (ev.target.closest("[role^=menuitem]")) setTimeout(m.close); });
  return m;
}
/* where a menu opens (#356, #357), from where its button is now. Everywhere: how tall it may be, the larger of the room
   under its button and over it (6 px from the button, 8 px from the screen's edge), so it fits on one side or the
   other, scrolling inside if it must; app.css's anchor positioning (or the rest of this) puts it there. Without CSS
   anchor positioning (app.css, @supports not (anchor-name: --a)) the rest is worked out here: under its button, or
   over it where there's no room under and there is over; 8 px inside the screen sideways, its right edge on the
   button's when that's on the right. Called as it's about to open, before it's drawn (measured shown, unseen, for a
   moment), and while it's open as the page scrolls or the window changes size. */
function menuPlace(m) {
  const {btn, list} = m, r = btn.getBoundingClientRect(), vw = document.documentElement.clientWidth, vh = document.documentElement.clientHeight;
  const below = Math.floor(vh - r.bottom - 14), above = Math.floor(r.top - 14);
  placeAt(list, {"--menu-room": Math.max(below, above, 40)});   // util.js; at least about one item
  if (CSS.supports("anchor-name: --a")) return;
  list.classList.add("sg-measure");   // shown (unseen) to be measured, if it isn't yet
  const w = list.offsetWidth, h = list.offsetHeight;
  list.classList.remove("sg-measure");
  const right = r.left + r.width / 2 > vw / 2;
  const left = Math.max(8, Math.min(right ? r.right - w : r.left, vw - 8 - w));
  const top = h <= below || below >= above ? r.bottom + 6 : r.top - 6 - h;
  placeAt(list, {"--menu-top": top, "--menu-left": left});
}
/* what's waiting for you on a profile (#283), from its jobs in state "preview" (GET .../jobs?state=preview) and its
   connections (GET .../connections): each import left at its preview to save or cancel, {job, conn} (conn: the portal
   whose first pull it is, or null), newest first, then each portal that needs you, {conn}: its access ended, or it
   stopped syncing. Add's count (below) and Sources' Overview (sources-overview.js) both read it; not an import with
   nothing in it (sgNothing) */
function sgWaiting(jobs, conns) {
  const cs = (conns && conns.connections || []).filter(c => c.state !== "disconnected");
  return [...(jobs || []).filter(j => j.state === "preview" && !sgNothing(j)).map(job => ({job, conn: cs.find(c => c.first_job === job.id) || null})),
    ...cs.filter(c => c.state === "needs reconnect" || c.warning).map(conn => ({conn}))];
}
/* a preview with nothing in it, new or already here (#534: an upload of cycle records only, once tracking is turned
   off): its review says so (import-jobs.js), and it isn't waiting for anyone */
const sgNothing = j => j.new === false && !j.stale && !(j.possible || []).length
  && !["events", "results", "other"].some(k => (j.duplicates || {})[k]) && !Object.values((j.duplicates || {}).watch || {}).some(n => n > 0);
/* the top bar's Search (#273): opens the command palette (palette.js wires it, and puts the keys that open it too in
   its tip). An icon, its name said. Hidden with the rest of the bar by lock() */
function barSearch() {
  return el("button", {class: "iconbtn bar-search", type: "button", id: "barSearch", "aria-label": "Search", "aria-haspopup": "dialog", "data-tip": "Search"},
    icon(sgIcon("search")));
}
/* ---- phones (#268, #269): at 700 px or narrower the top bar's menus, the records bar's period and the Timeline's
   filters open as sheets from the foot of the screen (openDialog's, util.js: #262), in reach of a thumb, the screen's
   width, swiped down by their grab handle or their heading, or closed with Escape or their ✕. */
const PHONE = matchMedia("(max-width:700px)");
/* a sheet: its heading and ✕ (the heading a handle to swipe it down by, as its grab bar is), then kids; kept in the page
   (closed) for openDialog(d, {as: "sheet"}). -> the <dialog> */
function phoneSheet(title, ...kids) {
  const id = "sheet" + (phoneSheet.n = (phoneSheet.n || 0) + 1);
  const d = el("dialog", {class: "sheet", "aria-labelledby": id},
    el("div", {class: "sheet-hd", "data-grab": ""}, el("h2", {id, text: title}),
      el("button", {class: "btn quiet sheet-x", type: "button", "aria-label": "Close", onclick: () => d.close()}, icon("x"))), ...kids);
  document.body.append(d);
  return d;
}
/* a menu (menuWire's m) that's a sheet on a phone and its popover wider, changing as the window does: the same list,
   moved into the sheet, the items worked the same way (the arrow keys, Home and End; Tab on to what follows in the
   sheet, not out of it); opened on its picked item, or its first; closed when one is picked. extra: what the sheet has
   under the menu (the period's date boxes). -> m */
function menuSheet(m, title, ...extra) {
  const {btn, list} = m, pop = {open: m.open, close: m.close}, home = list.parentNode;
  const body = el("div", {class: "sheet-body"}, ...extra), d = phoneSheet(title, body);
  d.sgFrom = btn;   // where ask() sends focus back once the sheet has gone (Log out, util.js)
  d.addEventListener("close", () => btn.setAttribute("aria-expanded", "false"));
  btn.addEventListener("click", () => { if (m.sheet) d.open ? d.close() : m.open(); });
  const sheet = () => {
    m.open = () => {
      if (d.open || !btn.isConnected) return;
      openDialog(d, {as: "sheet"});
      btn.setAttribute("aria-expanded", "true");
      m.onOpen?.();
      const its = [...list.querySelectorAll("[role^=menuitem]")].filter(x => !x.hidden);
      (its.find(x => x.getAttribute("aria-checked") === "true") || its[0])?.focus();
    };
    m.close = () => d.close();
  };
  function mode() {
    if (m.sheet === PHONE.matches) return;
    m.sheet = PHONE.matches;
    if (m.sheet) {
      pop.close();
      list.removeAttribute("popover");
      btn.popoverTargetElement = null;
      body.prepend(list);
      sheet();
    } else {
      d.close();
      home.append(list);
      list.popover = "auto";
      btn.popoverTargetElement = list;
      Object.assign(m, pop);
    }
    btn.setAttribute("aria-haspopup", m.sheet ? "dialog" : "menu");
  }
  PHONE.addEventListener("change", mode);
  mode();
  return m;
}
/* New profile is offered only to someone who may create one on this server (#1001, the owner's decision): /api/me's
   may_create_profiles (Settings → Install → General → Security: admins only, or everyone signed in). The profile
   menu's (and so the palette's), the records page's empty state (app.js) and each [data-np] of a page: "offer" shown
   to who may, "ask" (ask an admin) to who may not. The server refuses the rest (403). Only once /api/me says so: the
   profile menu's is hidden until then (#1050: it showed to a member while /api/me was slow) */
const sgMayNew = me => me?.may_create_profiles === true;
const SgNav = (() => {
  const header = document.querySelector("header.topbar");
  if (!header) return {sync() {}, lock() {}, profiles() { return null; }, profile() { return null; }, layout() { return {}; }, fit() {}, waited() {}, menu: menuWire, TABS: []};
  const svgLogo = header.querySelector(".brand-name");
  const onRecords = !!document.getElementById("view-overview"), page = location.pathname.split("/").pop() || "index.html";

  /* the brand: a link back to the records, except on the records page itself */
  if (svgLogo && !onRecords) {
    const a = el("a", {class: "brand-link", href: "./", "data-tip": "Back to your records"});
    a.append(...svgLogo.childNodes);
    svgLogo.append(a);
  }

  const items = m => [...m.list.querySelectorAll("[role^=menuitem]")].filter(x => !x.hidden);   // menuitem, menuitemradio (#191)
  const caret = () => el("span", {class: "menu-caret", "aria-hidden": "true"}, icon("chevron-down"));
  function menu(id, label, ico, entries) {
    const btn = el("button", {class: "iconbtn menu-btn", type: "button", id: id + "Btn", "aria-haspopup": "menu", "aria-expanded": "false",
      "aria-controls": id + "List"}, ico ? el("span", {class: "menu-ico", "aria-hidden": "true"}, icon(sgIcon(ico))) : null, label ? el("span", {class: "menu-word", text: label}) : null);
    const list = el("div", {class: "menu-list", id: id + "List", role: "menu", "aria-labelledby": id + "Btn"}, ...entries);
    for (const x of list.querySelectorAll("[role^=menuitem]")) x.tabIndex = -1;
    return menuWire({btn, list, wrap: el("div", {class: "menu", id}, btn, list)});
  }
  /* each item with its icon before its words (#354), hidden from assistive technology; a link to the page you're on
     says so (aria-current: Settings, to its You part, on Settings, #991). ico: the place's key in groups.js's one list
     of icons (sgIcon, #399) */
  const link = (href, ico, text, attrs = {}) => el("a", {class: "menu-item acct", role: "menuitem", href,
    "aria-current": href.split(/[?#]/)[0] === page ? "page" : null, ...attrs}, icon(sgIcon(ico)), text);
  const action = (id, ico, text, attrs = {}) => el("button", {class: "menu-item", role: "menuitem", type: "button", id, ...attrs}, icon(sgIcon(ico)), text);
  const sep = () => el("div", {class: "menu-sep", role: "separator"});
  const head = text => el("div", {class: "menu-head", "aria-hidden": "true", text});
  const group = (label, ...kids) => el("div", {role: "group", "aria-label": label}, head(label), ...kids);

  /* ---- the profile menu (#170, #264): the profile shown, by name and your role; in it, the other profiles shared
     with you (your role in each), this profile's own pages and New profile. The choice itself is kept in a hidden
     <select id="profile">, so the pages' scripts read its value and follow its "change" as they did when it was the
     picker; a page that stops the change (Settings, with changes not saved, #210) leaves it as it was. The page fills
     it once it knows who's signed in, SgNav.profiles(list, want); on a page that isn't about a profile (Account,
     Admin: no data-profile) nav.js does. The choice is this browser's (ht-profile) and the address's (?profile=2,
     when the address names one). */
  const ROLE = {owner: "owner", editor: "editor", viewer: "view only"};
  let pick = header.querySelector("#profile");
  if (pick) pick.remove(); else pick = el("select", {id: "profile", "aria-label": "Profile"});
  pick.className = "";
  pick.hidden = true;
  const prof = menu("profileMenu", "", "", []);
  prof.btn.classList.add("profile-btn");
  const radios = el("div", {role: "group", "aria-label": "Switch profile"}), rSep = sep();
  // its settings are Settings' This person, in your menu (#991)
  const pLinks = [link("sources.html", "sources", "Sources", {id: "sourcesLink"}),
    link("sharing.html", "sharing", "Sharing", {id: "sharingLink"})];
  const npSep = sep(), npLink = link("guide.html?new", "sharing/newProfile", "New profile", {id: "newProfileLink"});
  npSep.hidden = npLink.hidden = true;   // (until /api/me says this person may: sgMayNew)
  prof.list.append(radios, rSep, ...pLinks, npSep, npLink);
  for (const x of items(prof)) x.tabIndex = -1;
  const wrap = el("div", {class: "profile-pick", id: "profilePick", hidden: ""}, prof.wrap, pick);
  svgLogo.after(wrap);
  // before the page's own "change" listeners, so a page that reloads (the records page) opens the new one
  pick.addEventListener("change", () => remember(pick.value), true);
  document.addEventListener("change", ev => { if (ev.target === pick) draw(); });
  prof.list.addEventListener("click", ev => {
    const b = ev.target.closest("[data-pid]");
    if (!b || b.getAttribute("aria-disabled") === "true") return;
    prof.close();   // first, so focus is back on its button before the page follows the change
    if (pick.value === b.dataset.pid) return;
    pick.value = b.dataset.pid;
    pick.dispatchEvent(new Event("change", {bubbles: true}));
    draw();
  });
  /* this browser's last profile, and the page's own address (#415: always, not only when it named one, so a page
     opened at / keeps its profile on a reload after another browser tab has switched) */
  function remember(id) {
    try { localStorage.setItem("ht-profile", String(id)); } catch (e) {}
    const q = new URLSearchParams(location.search);
    if (q.get("profile") === String(id)) return;
    q.set("profile", id);
    try { history.replaceState(history.state, "", location.pathname + "?" + q + location.hash); } catch (e) {}
  }
  let known = [], locked = false;
  // the bar fitted again as its name's box changes: its height follows the text size, which the bar's observers (below)
  // can miss, nothing in the bar changing width with it (#832: at 360 px, text from 130% to 150% left the name cut
  // short of its floor, never on its own row)
  const nameSeen = new ResizeObserver(() => requestAnimationFrame(fit));
  /* the button and the menu for the profile chosen now (the select's value): its name and role; the others to switch
     to (not while the select is disabled: offline, or a save under way); its pages' and the tabs' addresses */
  function draw() {
    const p = known.find(x => String(x.id) === pick.value);
    wrap.hidden = tabs.hidden = !p || locked;   // no profile left (#501: after Leave): no records tabs either
    sync();   // Add, by your role on it (#401)
    waitingFor(p);   // and what's waiting for you there (#283)
    canEditTabs(p);
    if (!p) return;
    tabsOf(p.id);   // its tabs, whenever the profile changes (#415)
    const role = x => ROLE[x.role] || x.role;
    const name = el("span", {class: "profile-name", text: p.name});
    prof.btn.replaceChildren(el("span", {class: "vh", text: "Profile: "}), name, " ", el("span", {class: "profile-role", text: role(p)}), caret());
    nameSeen.disconnect(); nameSeen.observe(name);
    radios.replaceChildren(...(known.length < 2 ? [] : [head("Switch profile"), ...known.map(x => el("button", {class: "menu-item", role: "menuitemradio",
      type: "button", tabindex: "-1", "data-pid": x.id}, icon("check"), x.name, el("span", {class: "menu-note", text: role(x)})))]));
    rSep.hidden = known.length < 2;
    mark();
    for (const a of header.querySelectorAll("a[href]")) carry(a, p.id);
    for (const m of [prof, add, user, more]) for (const a of m.list.querySelectorAll("a[href]")) carry(a, p.id);   // (a sheet's list is out of the bar, #269)
  }
  /* a link that goes to another page, or to the records page's tabs from another, opens the profile shown here, not
     this browser's last (#397: one browser tab per child, each its own), as the palette's links do: ?profile= added,
     the link's own query kept. A link within the page (#log-dose on the records page) and New profile are left. */
  const hrefs = new WeakMap();
  function carry(a, id) {
    if (!hrefs.has(a)) hrefs.set(a, a.getAttribute("href"));
    const h = hrefs.get(a);
    if (!h || h[0] === "#" || a.id === "newProfileLink") return;
    const [path, hash] = h.split("#"), [file, qs] = path.split("?"), q = new URLSearchParams(qs || "");
    q.set("profile", id);
    a.setAttribute("href", `${file}?${q}${hash !== undefined ? "#" + hash : ""}`);
  }
  /* fill the menu with the profiles shared with you and choose one: want, else the address's, else this browser's
     last, else the first. Returns the profile's id (null when there are none). */
  function profiles(list, want) {
    known = list || [];
    if (!known.some(p => p.id === want)) {
      try { want = +(new URLSearchParams(location.search).get("profile") || localStorage.getItem("ht-profile")); } catch (e) {}
    }
    const p = known.find(x => x.id === want) || known[0];
    pick.replaceChildren(...known.map(x => el("option", {value: x.id, text: `${x.name} (${ROLE[x.role] || x.role})`})));
    if (p) { pick.value = p.id; remember(p.id); }
    draw();
    return p ? p.id : null;
  }
  /* which is chosen, and whether another can be: as the menu opens too (the select disabled since, or put back by its
     page) */
  function mark() {
    for (const b of radios.querySelectorAll("[data-pid]")) {
      b.setAttribute("aria-checked", String(b.dataset.pid === pick.value));
      if (pick.disabled) b.setAttribute("aria-disabled", "true"); else b.removeAttribute("aria-disabled");
    }
  }
  prof.onOpen = mark;

  /* ---- the records tabs (#264): links to the records page's tabs, on every page, in the profile's order without its
     hidden ones (Settings → Dashboard → Tabs: the records page passes its own, other pages ask for it). On the records
     page app.js shows the one picked (aria-current, then SgNav.fit()) and follows the links itself. Those that don't
     fit go under More (#265), which ends with Edit tabs…; the tab shown always stays in sight, taking the last place
     there is. More itself is always whole (#395): the tabs never shrink, the profile's name does first, down to FLOOR
     (or its whole length, if that's less); where even one tab beside More doesn't fit, that tab goes under More too,
     and More takes its name ("Labs ▾", raised as the tab shown is); with still too little room for the name, other
     things make way for it, or it takes a row of its own (#785, fit()). On a phone they're the bar at the screen's foot
     instead, five places (phoneBar, below). */
  const TABS = [["overview", "Overview"], ["health", "Body and activity"], ["medications", "Medications"], ["timeline", "Timeline"], ["labs", "Labs"], ["costs", "Costs"]];
  const tabEl = Object.fromEntries(TABS.map(([t, text]) => [t, el("a", {id: "tab-" + t, href: (onRecords ? "#" : "./#") + t, text,
    "data-tip": t === "health" ? "Watch, sleep and smart-scale data" : null})]));
  const editTabs = el("a", {class: "menu-item acct", role: "menuitem", id: "editTabs", href: "settings.html#sec-tabs"}, icon(sgIcon("settings/sec-tabs")), "Edit tabs…");
  const editSep = sep(), more = menu("tabMore", "More", "", [editSep, editTabs]);
  more.btn.className = "tab-more";
  more.btn.append(caret());
  const tabs = el("nav", {class: "tabs", "aria-label": "Records"}, ...Object.values(tabEl), more.wrap);
  wrap.after(tabs);
  let order = TABS.map(([t]) => t), hidden = new Set();
  const FLOOR = 8;   // the profile's name, in em, kept before a tab goes under More
  function fit() {
    const shown = order.filter(t => !hidden.has(t)).map(t => tabEl[t]), had = document.activeElement;
    const cur = shown.find(a => a.getAttribute("aria-current") === "page"), word = more.btn.querySelector(".menu-word");
    const put = vis => {
      for (const a of shown) {
        const out = !vis.includes(a);
        a.classList.toggle("menu-item", out);
        if (out) { a.setAttribute("role", "menuitem"); a.tabIndex = -1; } else { a.removeAttribute("role"); a.removeAttribute("tabindex"); }
      }
      tabs.prepend(...vis);
      more.list.prepend(...shown.filter(a => !vis.includes(a)));
      more.wrap.hidden = vis.length === shown.length;
      const named = !vis.length && !!cur;   // every tab under More: it says which is shown
      word.textContent = named ? cur.textContent : "More";
      more.btn.setAttribute("aria-label", named ? cur.textContent + ", more tabs" : "More tabs");
      more.btn.classList.toggle("tab-cur", named);
    };
    const name = wrap.hidden ? null : prof.btn.querySelector(".profile-name"), fs = name ? parseFloat(getComputedStyle(name).fontSize) : 0;
    const floor = name ? Math.min(name.scrollWidth, FLOOR * fs) : 0;
    const tight = () => header.scrollWidth > header.clientWidth + 1 || tabs.scrollWidth > tabs.clientWidth + 1
      || (name && (floor < name.scrollWidth ? name.clientWidth + 1 < floor : name.scrollWidth > name.clientWidth));   // (a short name: whole)
    // what's made way for the name (#785), off: its role said only ("role": in the button's name, as at 480 px and
    // narrower; not on a row of its own), the brand's word ("word"), More's name for the tab shown ("more": "More", the
    // tab still in its name, raised as the tab shown)
    const off = [], squeeze = row => {
      prof.btn.querySelector(".profile-role")?.classList.toggle("vh", off.includes("role") && !row);
      header.querySelector(".brand-word")?.classList.toggle("vh", off.includes("word"));
      word.textContent = more.btn.classList.contains("tab-cur") && !off.includes("more") ? cur.textContent : "More";
    };
    const back = (ok, row) => { for (const w of [...off].reverse()) { off.splice(off.indexOf(w), 1); squeeze(row); if (!ok()) { off.push(w); squeeze(row); } } };
    header.classList.remove("prof-row");
    put(shown);
    squeeze();
    if (PHONE.matches) put(phoneSlots(shown, cur));
    else {
      for (let n = shown.length - 1; n >= 0 && tight(); n--) {
        const vis = shown.slice(0, n);
        if (cur && n && !vis.includes(cur)) vis[n - 1] = cur;
        put(vis);
      }
      // above 700 px (#785: a tablet, a phone on its side, larger text), every tab under More and the name still cut
      // under 6 em (or its whole length, if that's less), or the bar wider than the window: its role, the brand's word
      // and More's name go in turn until it has that, those it can spare then given back. Still cut, it goes to a row of
      // its own under the bar, as on a phone (#622), its role beside it, and the others back where the bar's row has room
      const low = Math.min(name?.scrollWidth ?? 0, 6 * fs), cramped = () => header.scrollWidth > header.clientWidth + 1
        || name && (low < name.scrollWidth ? name.clientWidth + 1 < low : name.scrollWidth > name.clientWidth);   // (a short name: whole)
      for (const w of ["role", "word", "more"]) if (cramped()) { off.push(w); squeeze(); }
      if (name && cramped()) {
        header.classList.add("prof-row");
        squeeze(true);
        back(() => header.scrollWidth <= header.clientWidth + 1 && header.querySelector(".bar-menus").getBoundingClientRect().top < svgLogo.getBoundingClientRect().bottom, true);
      } else back(() => !cramped());
    }
    // on a phone with larger text (the name over 16 px, #622), a name cut short with less than 6 em of room beside the
    // brand and the menus goes to a row of its own under them (.prof-row); the room counted as it is on one row,
    // whichever it's on. At the usual size the bar stays one row, a name cut at 320 px as before (#268, #315)
    if (name && PHONE.matches) {
      const cs = getComputedStyle(header), gap = parseFloat(cs.columnGap) || 0, flow = [...header.children].filter(c => c.checkVisibility() && !/fixed|absolute/.test(getComputedStyle(c).position));
      const room = header.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - (flow.length - 1) * gap
        - flow.reduce((s, c) => s + (c === wrap ? prof.btn.offsetWidth - name.clientWidth : c.matches(".spacer") ? 0 : c.offsetWidth), 0);
      header.classList.toggle("prof-row", fs > 16 && room < Math.min(name.scrollWidth + 1, 6 * fs));
    }
    // the profile's name cut short: the whole of it in a tip (said in the button's name already, #396)
    if (name && name.scrollWidth > name.clientWidth) prof.btn.dataset.tip = name.textContent; else delete prof.btn.dataset.tip;
    // a tab that had focus and moved: focus stays with it, or goes to More when it's put away
    if (shown.includes(had) && document.activeElement !== had) (had.parentElement === tabs ? had : more.btn).focus({preventScroll: true});
  }
  /* the profile's layout ({order, hidden}, as Settings saves it): every tab in order, a tab it doesn't name after the
     rest, except Medications, after Body and activity (a layout from before it, kept offline: server/settings.py
     tab_layout gives the server's so, #275), at least one shown. Returns {order, hidden} */
  const NEW_TABS = {medications: "health"};
  function layout(cfg) {
    const ids = TABS.map(([t]) => t), ok = x => Array.isArray(x) ? x.filter(t => ids.includes(t)) : [];
    order = [...new Set(ok(cfg && cfg.order))];
    for (const t of ids) if (!order.includes(t)) order.splice(order.includes(NEW_TABS[t]) ? order.indexOf(NEW_TABS[t]) + 1 : order.length, 0, t);
    hidden = new Set(ok(cfg && cfg.hidden));
    if (hidden.size >= ids.length) hidden.clear();
    for (const t of ids) tabEl[t].hidden = hidden.has(t);
    fit();
    return {order, hidden};
  }
  /* the profile's tabs, on a page other than the records page: asked for as it's chosen, the first time and on every
     switch (#415); not found, the usual ones rather than the last profile's */
  let tabsFor = null;
  function tabsOf(pid) {
    if (onRecords || tabsFor === pid) return;
    tabsFor = pid;
    const put = s => { if (tabsFor === pid) layout(s && s.tabs); };
    api(`profiles/${pid}/settings`).then(r => r.ok ? r.json() : null).then(put, () => put(null));
  }
  /* Edit tabs… (More's) for those who can change the profile's tabs, its owners and editors: a viewer would find only
     switches they can't turn (#415) */
  function canEditTabs(p) {
    const can = !!p && (p.role === "owner" || p.role === "editor");
    for (const x of [editTabs, editSep]) setHidden(x, !can);
  }

  /* ---- Add (#264), in two groups. Today: a dose of a current medication (the Medications tab's Today, #275, or the
     Overview's doses: app.js), a medication of your own (its form on the records page, medmine.js, #1092), a journal entry (its form opens on the records page; from another page the link takes you there and it
     opens, #173), a home reading (Type records in, with a blood pressure's form). Records: each way in, opening its
     section of the Sources page (#166). On the Sources page itself only the address's #section changes: the page
     isn't loaded again, so the profile in its address (?profile=2) and what's typed in are kept (#388) */
  const src = page === "sources.html" ? "" : "sources.html", rec = onRecords ? "" : "./";
  const add = menu("addMenu", "Add", "add", [
    group("Today",
      link(rec + "#log-dose", "dose", "Log a dose", {id: "doseLink", "data-needs": "import"}),
      link(rec + "#add-medication", "medications", "Add a medication", {id: "medAddLink", "data-needs": "import"}),   // your own (#1092)
      onRecords ? action("journalBtn", "journal", "Journal entry", {hidden: ""}) : link("./#new-journal-entry", "journal", "Journal entry", {id: "journalBtn"}),
      link(src + "#home-reading", "reading", "Home reading", {"data-needs": "import"})),
    group("Records",
      link(src + "#waiting", "sources/waiting", "Waiting for you", {id: "waitingLink", "data-needs": "import", "data-none": ""}),   // #283, below
      link(src + "#upload", "sources/upload", "Upload files", {id: "importLink", "data-needs": "import"}),
      link(src + "#portals", "sources/portals", "Connect a portal or plan", {"data-needs": "import"}),
      link(src + "#manual", "sources/manual", "Type records in", {"data-needs": "import"}),
      link(src + "#link", "sources/link", "Paste a Health Link", {"data-needs": "import"})),
  ]);
  /* the person's own menu (#264): what's theirs whichever profile is shown. Settings (#991: Account, Display and Admin
     before) opens its You part (#you: its first group, Sign-in and security); the profile's and the server's are its
     other parts */
  const who = el("span", {id: "who", text: "Account"});
  const user = menu("userMenu", "", "", [
    link("settings.html#you", "settings", "Settings", {id: "settingsLink"}),
    link("guide.html", "guide", "Getting started", {id: "guideLink"}),   // the first-run guide, any time (#199)
    action("theme", "theme", "Dark mode"),
    sep(),
    action("logout", "logout", "Log out"),
  ]);
  // its name starts with what it shows, "demo, your menu" (#400: speech input says what it sees, WCAG 2.5.3)
  user.btn.replaceChildren(who, el("span", {class: "vh", text: ", your menu"}), caret());
  add.btn.append(caret());
  // the spacer before the records page's Install app button, so it sits with the menus
  const inst = document.getElementById("installBtn");
  if (inst) inst.before(el("span", {class: "spacer"}));
  else header.append(el("span", {class: "spacer"}));
  // in one box, so where the bar wraps on a phone the two go to the next row together, never your menu alone (#319)
  const menus = el("div", {class: "bar-menus"}, barSearch(), add.wrap, user.wrap);
  header.append(menus);

  /* light or dark (#410): the account's theme, as Settings → Display has it, shown at once and saved, a toast saying
     so with an Undo (back to what it was, if it's still as set here); a page with it shown draws it again (theme:changed,
     settings.js). Someone with a password to change first (lock()) can't save settings yet: theirs is this browser's
     own, the only one there is (applyTheme in util.js) */
  const isDark = () => document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  const themeBtn = document.getElementById("theme");
  const themeLabel = () => { themeBtn.lastChild.textContent = isDark() ? "Light mode" : "Dark mode"; };   // after its icon
  const putTheme = async v => {
    const r = await api("settings/user/theme", {method: "PUT", body: JSON.stringify({value: v})});
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    try { localStorage.removeItem("ht-theme"); } catch (e) {}
    applyTheme(v);
    document.dispatchEvent(new Event("theme:changed"));
  };
  themeBtn.onclick = async () => {
    const to = isDark() ? "light" : "dark";
    if (locked) { try { localStorage.setItem("ht-theme", to); } catch (e) {} applyTheme(); return; }
    const was = document.documentElement.dataset.theme;
    document.documentElement.dataset.theme = to;   // shown as it's picked
    let from;
    try { from = (await (await api("settings/user")).json()).theme; await putTheme(to); }
    catch (e) {
      if (themeLater(to, e)) return;
      if (was) document.documentElement.dataset.theme = was; else delete document.documentElement.dataset.theme; toast(`The theme isn't saved: ${e.message}`); return;
    }
    toast(`Saved your theme: ${to === "dark" ? "Dark" : "Light"}.`, {action: "Undo", onAction: async () => {
      const now = (await (await api("settings/user")).json()).theme;
      if (now !== to) { toast("The theme was changed since, so it's left as it is."); return; }
      await putTheme(from);
    }});
  };
  new MutationObserver(themeLabel).observe(document.documentElement, {attributes: true, attributeFilter: ["data-theme"]});
  themeLabel();
  /* offline (#414): the server can't be reached (offline.js's or offline-page.js's OFFLINE, or a request that never
     got an answer), so the theme picked is this browser's own for now, as before #410, and saved for the account
     once the server answers again: on the next page opened online, or here when the connection is back */
  const LATER = "sg-theme-later", away = e => (typeof OFFLINE !== "undefined" && OFFLINE.on) || e instanceof TypeError;
  const themeName = v => v === "dark" ? "Dark" : "Light";
  function themeLater(to, e) {
    if (!away(e)) return false;
    try { localStorage.setItem("ht-theme", to); localStorage.setItem(LATER, to); } catch (x) {}
    applyTheme();
    toast(`${themeName(to)} mode on this browser for now: you're offline. It's saved for your account once you're back online.`);
    return true;
  }
  async function saveLater() {
    let v = null, saved = true;
    try { v = localStorage.getItem(LATER); } catch (e) {}
    if (!v || locked) return;
    try { await putTheme(v); }
    catch (e) { if (away(e)) return; saved = false; }   // not reached yet: another time; refused: it stays this browser's own (Display says so)
    try { localStorage.removeItem(LATER); } catch (e) {}
    if (saved) toast(`Saved your theme for your account: ${themeName(v)}, as picked offline.`);
  }
  addEventListener("online", () => setTimeout(saveLater, 1000));

  /* Add follows your role on the profile shown (#401), on every page and as you switch profile: its ways to add are
     for the profile's owners and editors, and Log a dose is for one with a current medication where the page knows
     (the records page marks it data-none when there's none); a group with nothing in it goes, and with nothing in it
     at all (no profile shown, or one you can only view), Add goes, and the command palette offers none of them */
  const setHidden = (x, h) => { if (x.hidden !== h) x.hidden = h; };   // only real changes: the observer below sees these
  function sync() {
    const p = known.find(x => String(x.id) === pick.value), can = !!p && (p.role === "owner" || p.role === "editor");
    for (const x of add.list.querySelectorAll("[data-needs], #journalBtn")) setHidden(x, !can || x.hasAttribute("data-none"));
    for (const g of add.list.querySelectorAll("[role=group]")) setHidden(g, ![...g.querySelectorAll("[role^=menuitem]")].some(x => !x.hidden));
    setHidden(add.wrap, locked || items(add).length === 0);
  }
  new MutationObserver(sync).observe(add.list, {attributes: true, subtree: true, attributeFilter: ["data-none"]});
  sync();

  /* ---- Waiting for you (#283), first under Add's Records while something is: imports left at their preview, to save
     or cancel, and portals that need you (sgWaiting, below), with how many, opening Sources' Overview where they're
     listed. Asked for the profile shown, by its owners and editors (the jobs are theirs), once the page is idle and
     each time Add opens; Sources' Overview, which reads them itself, gives its count (SgNav.waited) */
  const waitLink = document.getElementById("waitingLink"), waitN = el("span", {class: "menu-n"});
  waitLink.append(" ", waitN);
  let waitFor = null, waitSeq = 0;
  function waited(pid, n) {
    if (String(pid) !== pick.value) return;
    waitN.textContent = n ? String(n) : "";
    if (n) waitLink.removeAttribute("data-none"); else waitLink.setAttribute("data-none", "");
  }
  async function askWaiting(polled = false) {   // polled: asked on the page's own, as a profile is drawn (#1063: not use)
    const p = known.find(x => String(x.id) === pick.value), mine = ++waitSeq;
    if (!p || locked || (p.role !== "owner" && p.role !== "editor") || (typeof OFFLINE !== "undefined" && OFFLINE.on)) return;
    try {
      const [jobs, conns] = await Promise.all([`profiles/${p.id}/jobs?state=preview`, `profiles/${p.id}/connections`]
        .map(path => api(path, polled ? background() : {}).then(r => r.ok ? r.json() : null)));
      if (mine === waitSeq && jobs) waited(p.id, sgWaiting(jobs, conns).length);
    } catch (e) {}   // (offline, or the server away: nothing said)
  }
  function waitingFor(p) {   // the profile drawn (draw, above): asked again once it's another
    if (!p || p.id === waitFor) return;
    waitFor = p.id;
    waited(p.id, 0);
    if (window.requestIdleCallback) requestIdleCallback(() => askWaiting(true), {timeout: 3000}); else setTimeout(() => askWaiting(true), 1000);
  }
  add.onOpen = askWaiting;

  /* ---- a menu's link to a group or a part of a page with a sidebar (#398, #400): focus goes to what it opened, the
     group's heading (the page's h1) or the part's, as the palette's links do. On that page itself it only changes the
     address, which the sidebar follows (About this person from Settings' profile menu); to another page, once that
     page has drawn it (Edit tabs… from the records page: Settings → Tabs), unless something else had focus first. */
  const ARRIVE = "sg-arrive", sidePage = f => typeof SG_PAGES !== "undefined" && Object.values(SG_PAGES).some(P => P.page === f);
  function headingOf(id) {   // (a group, a label's first group as in settings.html#you, #991, or a part)
    const gs = Object.values(SG_PAGES).find(P => P.page === page)?.groups || [], is = g => g.id === id || g.part?.toLowerCase() === id;
    const group = gs.some(is), part = !group && document.getElementById(id);
    if (!group && !part) return null;   // (#home-reading: its page puts focus in the form it opens)
    if (group && !is(gs.find(g => g.id === document.querySelector("main.sets")?.dataset.group) || {})) return null;   // (the group once it's shown: #991)
    let t = part && part.querySelector(":scope > h2, :scope > .set-hd > h2");
    if (!t || t.classList.contains("vh")) t = document.getElementById("groupTitle");   // a card alone in its group: the h1 says it
    return t && t.checkVisibility() ? t : null;
  }
  function focusOn(id, wait) {
    const go = () => {
      if (wait && document.activeElement !== document.body) return true;
      const modal = document.querySelector("dialog:modal");   // a phone's sheet the link was in: once it's closed, and has given focus back
      if (modal) { modal.addEventListener("close", () => setTimeout(go), {once: true}); return true; }
      const t = headingOf(id);
      if (!t) return false;
      if (!t.hasAttribute("tabindex")) t.tabIndex = -1;
      t.focus({preventScroll: true});   // (the sidebar has scrolled to it)
      return true;
    };
    if (!wait) return void setTimeout(go);
    if (go()) return;
    const ob = new MutationObserver(() => { if (go()) ob.disconnect(); });
    ob.observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ["hidden", "class"]});
    setTimeout(() => ob.disconnect(), wait);
  }
  for (const m of [prof, add, user, more]) m.list.addEventListener("click", ev => {
    const a = ev.target.closest("a[href]");
    if (!a || ev.defaultPrevented || ev.button || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    const u = new URL(a.href), file = u.pathname.split("/").pop() || "index.html", id = decodeURIComponent(u.hash.slice(1));
    if (!id || !sidePage(file)) return;
    if (file !== page) { try { sessionStorage.setItem(ARRIVE, file + "#" + id); } catch (e) {} return; }
    ev.preventDefault();   // the same page: the address only, the profile shown kept
    addEventListener("hashchange", () => focusOn(id), {once: true});
    if (location.hash !== u.hash) location.hash = u.hash;
    else dispatchEvent(new HashChangeEvent("hashchange", {oldURL: location.href, newURL: location.href}));   // (the page's own listeners read newURL)
  });
  document.addEventListener("DOMContentLoaded", () => {   // (groups.js is there by then)
    let w = null;
    try { w = sessionStorage.getItem(ARRIVE); sessionStorage.removeItem(ARRIVE); } catch (e) {}
    const id = decodeURIComponent(location.hash.slice(1));
    if (w && id && w === page + "#" + id) focusOn(id, 10000);
  });
  /* for an admin, "backups late" beside Settings (#200; beside Admin before #991: Settings → Backups says why, in
     Install): once the page's own scripts have asked who's signed in; and on a page that isn't about a profile, the
     profile menu. With no profile at all, no records tabs on any page, here for every one: those
     that fill the menu themselves may stop first (#505: Sources and the guide's New profile did) */
  const own = onRecords || header.hasAttribute("data-profile");
  document.addEventListener("DOMContentLoaded", () => {
    api("me").then(r => r.ok ? r.json() : null).then(me => {
      if (me) {   // New profile where it's offered (sgMayNew)
        npSep.hidden = npLink.hidden = !sgMayNew(me);
        for (const e of document.querySelectorAll("[data-np]")) e.hidden = (e.dataset.np === "offer") !== sgMayNew(me);
      }
      if (me && (!own || me.profiles?.length === 0) && !locked) profiles(me.profiles);
      if (me && !me.offline) {
        saveLater();   // a theme picked offline (#414)
        if (Array.isArray(me.profiles)) forgetProfiles(id => !me.profiles.some(p => p.id === id));   // a profile no longer theirs (pwa.js, #936)
      }
      if (me && me.user && me.user.is_admin && me.backups_late && !locked)
        document.getElementById("settingsLink").append(" ", el("span", {class: "badge bk-late"}, icon("triangle-alert"), " backups late"));
    }).catch(() => {});
  });
  /* nothing to go to but Log out (a password to change or a second factor to set up first, account.js) */
  function lock() {
    locked = true;
    add.wrap.hidden = wrap.hidden = tabs.hidden = $("#barSearch").hidden = true;
    for (const x of items(user)) if (x.id !== "logout" && x.id !== "theme") x.hidden = true;
    for (const s of user.list.querySelectorAll(".menu-sep")) s.hidden = true;
    const b = header.querySelector(".brand-link");
    if (b) { b.removeAttribute("href"); b.removeAttribute("title"); }
  }
  /* the bar's height as --bar-h, so what gets focus is scrolled clear of it (scroll-padding in app.css, #202); and
     the tabs fitted again as the bar's room changes (the window, the profile's name, the fonts arriving), in the
     next frame, not inside the observer's own */
  new ResizeObserver(() => {
    document.documentElement.style.setProperty("--bar-h", header.offsetHeight + "px");
    requestAnimationFrame(fit);
  }).observe(header);
  for (const x of [wrap, menus]) new ResizeObserver(() => requestAnimationFrame(fit)).observe(x);
  document.fonts?.ready.then(fit);

  /* ---- phones (#268): the top bar one row, the brand (its mark alone at 480 px or narrower), the profile menu, then
     Add and your menu as icons; the records tabs a bar fixed at the screen's foot (app.css), each an icon and a short
     name ("Body": the whole name is still its name, for a screen reader and #tabTitle). The bar has five places: every
     tab while there are five or fewer, else four and More, the tab shown among the four (phoneSlots). Under 500 px
     tall the tabs go back to a row in the top bar, which scrolls away with the page (#308). The top bar's menus are
     sheets there (menuSheet, above, #269). */
  function phoneSlots(shown, cur) {
    if (shown.length <= 5) return shown;
    const vis = shown.slice(0, 4);
    if (cur && !vis.includes(cur)) vis[3] = cur;
    return vis;
  }
  function phoneBar() {
    const SHORT = {health: "Body", medications: "Meds"};
    for (const [t] of TABS) {
      const a = tabEl[t];
      a.replaceChildren(icon(sgIcon(t)), el("span", {class: "tab-long", text: a.textContent}));
      if (SHORT[t]) a.dataset.short = SHORT[t];
    }
    more.btn.prepend(el("span", {class: "menu-ico", "aria-hidden": "true"}, icon(sgIcon("more"))));
    user.btn.prepend(el("span", {class: "menu-ico", "aria-hidden": "true"}, icon(sgIcon("you"))));
    const brand = header.querySelector(".brand-link") || svgLogo, word = brand && [...brand.childNodes].find(n => n.nodeType === 3 && n.textContent.trim());
    if (word) word.replaceWith(el("span", {class: "brand-word", text: word.textContent}));
    menuSheet(prof, "Profile");
    menuSheet(add, "Add");
    menuSheet(user, "Your menu");
    menuSheet(more, "More tabs");
  }
  phoneBar();
  /* the profile shown, {id, name, role}, or null (a drop anywhere uploads to it: drop.js, #281) */
  const profile = () => locked ? null : known.find(x => String(x.id) === pick.value) || null;
  return {sync, lock, profiles, profile, layout, fit, waited, menu: menuWire, TABS: TABS.map(([t]) => t)};
})();
