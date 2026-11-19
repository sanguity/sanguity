/* Sanguity · the top bar on every signed-in page (after util.js and pwa.js, before the page's own script).

   One row, the same on every page (#264): the brand (back to the records), the profile menu (switch profile; About
   this person, Settings, Sources, Sharing; New profile), the records tabs as links (those that don't fit under More,
   #265), then two menus. Add: today's (a dose, a journal entry, a home reading) and records in (upload, connect, type
   in, a Health Link). The person's own menu: Account, Display, Admin (admins only: nav.js asks the API itself, #167),
   Getting started (the first-run guide, #199), the light/dark toggle, log out. The items keep the ids the pages
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
   m is {btn, list, wrap?, onOpen?}; menuWire(m) gives it open() and close(), and returns it (SgNav.menu). */
function menuWire(m) {
  const {btn, list} = m, its = () => [...list.querySelectorAll("[role^=menuitem]")].filter(x => !x.hidden);
  list.removeAttribute("hidden");
  list.popover = "auto";
  btn.popoverTargetElement = list;   // a click toggles it, and a click on its own button isn't one "elsewhere"
  const isOpen = () => list.matches(":popover-open");
  m.open = () => { if (!isOpen() && list.isConnected) list.showPopover({source: btn}); };
  m.close = () => { if (isOpen()) list.hidePopover(); };
  let leaving = false;
  const follow = () => menuPlace(m);   // placed before it's drawn, and again while open as the page scrolls or resizes (#356)
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
/* the top bar's Search (#273): opens the command palette (palette.js wires it, and puts the keys that open it too in
   its tip). An icon, its name said. Hidden with the rest of the bar by lock() */
function barSearch() {
  return el("button", {class: "iconbtn bar-search", type: "button", id: "barSearch", "aria-label": "Search", "aria-haspopup": "dialog", "data-tip": "Search"},
    icon("search"));
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
const SgNav = (() => {
  const header = document.querySelector("header.topbar");
  if (!header) return {sync() {}, lock() {}, profiles() { return null; }, layout() { return {}; }, fit() {}, menu: menuWire, TABS: []};
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
      "aria-controls": id + "List"}, ico ? el("span", {class: "menu-ico", "aria-hidden": "true"}, icon(ico)) : null, label ? el("span", {class: "menu-word", text: label}) : null);
    const list = el("div", {class: "menu-list", id: id + "List", role: "menu", "aria-labelledby": id + "Btn"}, ...entries);
    for (const x of list.querySelectorAll("[role^=menuitem]")) x.tabIndex = -1;
    return menuWire({btn, list, wrap: el("div", {class: "menu", id}, btn, list)});
  }
  /* each item with its icon before its words (#354), hidden from assistive technology; a link to the page you're on
     says so (aria-current) */
  const link = (href, ico, text, attrs = {}) => el("a", {class: "menu-item acct", role: "menuitem", href,
    "aria-current": href.split("?")[0] === page ? "page" : null, ...attrs}, icon(ico), text);
  const action = (id, ico, text, attrs = {}) => el("button", {class: "menu-item", role: "menuitem", type: "button", id, ...attrs}, icon(ico), text);
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
  const pLinks = [link("settings.html#about", "book-user", "About this person", {id: "aboutLink"}),   // in Settings now (#267)
    link("settings.html", "settings", "Settings", {id: "settingsLink"}), link("sources.html", "database", "Sources", {id: "sourcesLink"}),
    link("sharing.html", "share-2", "Sharing", {id: "sharingLink"})];
  prof.list.append(radios, rSep, ...pLinks, sep(), link("guide.html?new", "plus", "New profile", {id: "newProfileLink"}));
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
  function remember(id) {
    try { localStorage.setItem("ht-profile", String(id)); } catch (e) {}
    const q = new URLSearchParams(location.search);
    if (!q.has("profile") || q.get("profile") === String(id)) return;
    q.set("profile", id);
    try { history.replaceState(history.state, "", location.pathname + "?" + q + location.hash); } catch (e) {}
  }
  let known = [], locked = false;
  /* the button and the menu for the profile chosen now (the select's value): its name and role; the others to switch
     to (not while the select is disabled: offline, or a save under way); its pages' and the tabs' addresses */
  function draw() {
    const p = known.find(x => String(x.id) === pick.value);
    wrap.hidden = !p || locked;
    if (!p) return;
    const role = x => ROLE[x.role] || x.role;
    prof.btn.replaceChildren(el("span", {class: "vh", text: "Profile: "}), el("span", {class: "profile-name", text: p.name}), " ",
      el("span", {class: "profile-role", text: role(p)}), caret());
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
    if (p) { pick.value = p.id; remember(p.id); tabsOf(p.id); }
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
     and More takes its name ("Labs ▾", raised as the tab shown is). On a phone they're the bar at the screen's foot
     instead, five places (phoneBar, below). */
  const TABS = [["overview", "Overview"], ["health", "Body and activity"], ["timeline", "Timeline"], ["labs", "Labs"], ["costs", "Costs"]];
  const tabEl = Object.fromEntries(TABS.map(([t, text]) => [t, el("a", {id: "tab-" + t, href: (onRecords ? "#" : "./#") + t, text,
    "data-tip": t === "health" ? "Watch, sleep and smart-scale data" : null})]));
  const editTabs = el("a", {class: "menu-item acct", role: "menuitem", id: "editTabs", href: "settings.html#sec-tabs"}, icon("pencil"), "Edit tabs…");
  const more = menu("tabMore", "More", "", [sep(), editTabs]);
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
    const name = wrap.hidden ? null : prof.btn.querySelector(".profile-name");
    const floor = name ? Math.min(name.scrollWidth, FLOOR * parseFloat(getComputedStyle(name).fontSize)) : 0;
    const tight = () => header.scrollWidth > header.clientWidth + 1 || tabs.scrollWidth > tabs.clientWidth + 1
      || (name && (floor < name.scrollWidth ? name.clientWidth + 1 < floor : name.scrollWidth > name.clientWidth));   // (a short name: whole)
    put(shown);
    if (PHONE.matches) put(phoneSlots(shown, cur));
    else {
      for (let n = shown.length - 1; n >= 0 && tight(); n--) {
        const vis = shown.slice(0, n);
        if (cur && n && !vis.includes(cur)) vis[n - 1] = cur;
        put(vis);
      }
    }
    // the profile's name cut short: the whole of it in a tip (said in the button's name already, #396)
    if (name && name.scrollWidth > name.clientWidth) prof.btn.dataset.tip = name.textContent; else delete prof.btn.dataset.tip;
    // a tab that had focus and moved: focus stays with it, or goes to More when it's put away
    if (shown.includes(had) && document.activeElement !== had) (had.parentElement === tabs ? had : more.btn).focus({preventScroll: true});
  }
  /* the profile's layout ({order, hidden}, as Settings saves it): every tab in order, a tab it doesn't name after the
     rest, at least one shown. Returns {order, hidden} */
  function layout(cfg) {
    const ids = TABS.map(([t]) => t), ok = x => Array.isArray(x) ? x.filter(t => ids.includes(t)) : [];
    order = [...new Set([...ok(cfg && cfg.order), ...ids])];
    hidden = new Set(ok(cfg && cfg.hidden));
    if (hidden.size >= ids.length) hidden.clear();
    for (const t of ids) tabEl[t].hidden = hidden.has(t);
    fit();
    return {order, hidden};
  }
  let tabsFor = null;
  function tabsOf(pid) {
    if (onRecords || tabsFor === pid) return;
    tabsFor = pid;
    api(`profiles/${pid}/settings`).then(r => r.ok ? r.json() : null).then(s => { if (s && tabsFor === pid) layout(s.tabs); }).catch(() => {});
  }

  /* ---- Add (#264), in two groups. Today: a dose of a current medication (the Overview's "Today's medication doses",
     app.js), a journal entry (its form opens on the records page; from another page the link takes you there and it
     opens, #173), a home reading (Type records in, with a blood pressure's form). Records: each way in, opening its
     section of the Sources page (#166). On the Sources page itself only the address's #section changes: the page
     isn't loaded again, so the profile in its address (?profile=2) and what's typed in are kept (#388) */
  const src = page === "sources.html" ? "" : "sources.html", rec = onRecords ? "" : "./";
  const add = menu("addMenu", "Add", "plus", [
    group("Today",
      link(rec + "#log-dose", "pill", "Log a dose", {id: "doseLink", "data-needs": "import"}),
      onRecords ? action("journalBtn", "notebook", "Journal entry", {hidden: ""}) : link("./#new-journal-entry", "notebook", "Journal entry", {id: "journalBtn"}),
      link(src + "#home-reading", "activity", "Home reading", {"data-needs": "import"})),
    group("Records",
      link(src + "#upload", "upload", "Upload files", {id: "importLink", "data-needs": "import"}),
      link(src + "#portals", "plug", "Connect a portal or plan", {"data-needs": "import"}),
      link(src + "#manual", "keyboard", "Type records in", {"data-needs": "import"}),
      link(src + "#link", "link", "Paste a Health Link", {"data-needs": "import"})),
  ]);
  /* the person's own menu (#264): what's theirs whichever profile is shown */
  const who = el("span", {id: "who", text: "Account"});
  const user = menu("userMenu", "", "", [
    link("account.html", "circle-user", "Account", {id: "accountLink"}),
    link("settings.html#display", "settings", "Display", {id: "displayLink"}),
    link("admin.html", "wrench", "Admin", {id: "adminLink", hidden: ""}),
    link("guide.html", "compass", "Getting started", {id: "guideLink"}),   // the first-run guide, any time (#199)
    action("theme", "sun-moon", "Dark mode"),
    sep(),
    action("logout", "log-out", "Log out"),
  ]);
  user.btn.replaceChildren(who, caret());
  add.btn.append(caret());
  user.btn.setAttribute("aria-label", "Your menu");
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
    catch (e) { if (was) document.documentElement.dataset.theme = was; else delete document.documentElement.dataset.theme; toast(`The theme isn't saved: ${e.message}`); return; }
    toast(`Saved your theme: ${to === "dark" ? "Dark" : "Light"}.`, {action: "Undo", onAction: async () => {
      const now = (await (await api("settings/user")).json()).theme;
      if (now !== to) { toast("The theme was changed since, so it's left as it is."); return; }
      await putTheme(from);
    }});
  };
  new MutationObserver(themeLabel).observe(document.documentElement, {attributes: true, attributeFilter: ["data-theme"]});
  themeLabel();

  /* the ways in follow #importLink (the records page hides it for someone who can only view); a group with nothing
     in it goes, and with nothing in it at all, Add goes */
  const setHidden = (x, h) => { if (x.hidden !== h) x.hidden = h; };   // only real changes: the observer below sees these
  function sync() {
    const imp = document.getElementById("importLink");
    for (const x of add.list.querySelectorAll("[data-needs=import]")) if (x !== imp) setHidden(x, imp.hidden);
    for (const g of add.list.querySelectorAll("[role=group]")) setHidden(g, ![...g.querySelectorAll("[role^=menuitem]")].some(x => !x.hidden));
    setHidden(add.wrap, items(add).length === 0);
  }
  new MutationObserver(sync).observe(add.list, {attributes: true, subtree: true, attributeFilter: ["hidden"]});
  sync();
  /* Admin, for admins only (#167): once the page's own scripts have asked who's signed in; and on a page that isn't
     about a profile, the profile menu */
  const own = onRecords || header.hasAttribute("data-profile");
  document.addEventListener("DOMContentLoaded", () => {
    api("me").then(r => r.ok ? r.json() : null).then(me => {
      if (me && !own && !locked) profiles(me.profiles);
      if (me && me.user && me.user.is_admin && !locked) {
        const a = document.getElementById("adminLink");
        a.hidden = false;
        if (me.backups_late) a.append(" ", el("span", {class: "badge bk-late"}, icon("triangle-alert"), " backups late"));   // #200: Admin → Backups says why
      }
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
    const TAB_ICON = {overview: "layout-grid", health: "heart-pulse", timeline: "chart-gantt", labs: "test-tube-diagonal", costs: "banknote", meds: "pill"};
    const SHORT = {health: "Body", meds: "Meds"};
    for (const [t] of TABS) {
      const a = tabEl[t];
      a.replaceChildren(icon(TAB_ICON[t] || "file-text"), el("span", {class: "tab-long", text: a.textContent}));
      if (SHORT[t]) a.dataset.short = SHORT[t];
    }
    more.btn.prepend(el("span", {class: "menu-ico", "aria-hidden": "true"}, icon("ellipsis")));
    user.btn.prepend(el("span", {class: "menu-ico", "aria-hidden": "true"}, icon("circle-user")));
    const brand = header.querySelector(".brand-link") || svgLogo, word = brand && [...brand.childNodes].find(n => n.nodeType === 3 && n.textContent.trim());
    if (word) word.replaceWith(el("span", {class: "brand-word", text: word.textContent}));
    menuSheet(prof, "Profile");
    menuSheet(add, "Add");
    menuSheet(user, "Your menu");
    menuSheet(more, "More tabs");
  }
  phoneBar();
  return {sync, lock, profiles, layout, fit, menu: menuWire, TABS: TABS.map(([t]) => t)};
})();
