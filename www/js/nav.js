/* Sanguity · the top bar on every signed-in page (after util.js and pwa.js, before the page's own script).

   One row: the brand (back to the records), the page's title, the profile picker on the pages about a profile
   (#170), the records page's tabs, then two menus. Add: import records, connect a source, type records in, a
   journal entry. The person's own menu: sharing (who sees the profile, share links, calendar feed, visit summary,
   export: #168), settings, account, Admin (admins only: nav.js asks the API itself, #167), Getting started (the
   first-run guide, #199), the light/dark toggle, log out. The items keep the ids the pages already use (#importLink,
   #journalBtn, #settingsLink, #theme, #logout, #who), so each page's script sets them up as before; nav.js only
   builds the bar and its menus (menuWire, above).

   <header class="topbar" data-title="Settings" data-profile> gets the bar, with the profile picker for data-profile;
   anything already inside it (the records page's profile picker, tabs and Install app button) stays where it is,
   between the brand and the menus. */
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
  list.addEventListener("beforetoggle", ev => {
    const on = ev.newState === "open";
    btn.setAttribute("aria-expanded", String(on));
    if (!on && !leaving && list.contains(document.activeElement)) btn.focus({preventScroll: true});
    leaving = false;
  });
  list.addEventListener("toggle", ev => { if (ev.newState === "open") { menuPlace(m); m.onOpen?.(); } });
  btn.addEventListener("keydown", ev => {
    if (ev.key !== "ArrowDown" && ev.key !== "Enter" && ev.key !== " ") return;
    ev.preventDefault();
    m.open();
    its()[0]?.focus();
  });
  list.addEventListener("keydown", ev => {
    const all = its(), i = all.indexOf(document.activeElement);
    const to = {ArrowDown: all[(i + 1) % all.length], ArrowUp: all[(i - 1 + all.length) % all.length], Home: all[0], End: all.at(-1)}[ev.key];
    if (to) { ev.preventDefault(); to.focus(); }
    else if (ev.key === "Tab") { leaving = true; m.close(); }   // on to what's next, not back to the button
  });
  list.addEventListener("click", ev => { if (ev.target.closest("[role^=menuitem]")) setTimeout(m.close); });
  return m;
}
/* without CSS anchor positioning (app.css, @supports not (anchor-name: --a)): where the menu opens, worked out here:
   under its button, 8 px inside the screen, its right edge on the button's when that's on the right; and how far
   down the page the bar ends, for a phone's short screen, where the bar scrolls away (#308, #319) */
function menuPlace(m) {
  if (CSS.supports("anchor-name: --a")) return;
  const r = m.btn.getBoundingClientRect(), vw = document.documentElement.clientWidth, w = m.list.offsetWidth;
  const bar = document.querySelector("header.topbar"), right = r.left + r.width / 2 > vw / 2;
  const left = Math.max(8, Math.min(right ? r.right - w : r.left, vw - 8 - w));
  placeAt(m.list, {"--menu-top": r.bottom + 6, "--menu-left": left, "--menu-bar": bar ? bar.getBoundingClientRect().bottom + scrollY : 0});   // util.js
}
const SgNav = (() => {
  const header = document.querySelector("header.topbar");
  if (!header) return {sync() {}, lock() {}, profiles() { return null; }, menu: menuWire};
  const svgLogo = header.querySelector(".brand-name");
  const onRecords = !!document.getElementById("tab-overview");

  /* the brand: a link back to the records, except on the records page itself */
  if (svgLogo && !onRecords) {
    const a = el("a", {class: "brand-link", href: "./", title: "Back to your records"});
    a.append(...svgLogo.childNodes);
    svgLogo.append(a);
  }
  if (header.dataset.title) svgLogo.after(el("span", {class: "page-title", text: header.dataset.title}));

  /* the profile picker (#170), on the pages about a profile (<header data-profile>, and the records page, whose
     #profile is already in the bar): every profile shared with you, by name and your role, or a plain label when
     there's only one. It keeps the id #profile, so the page's scripts read its value and follow its "change" as
     they did their own select's. The page fills it once it knows who's signed in: SgNav.profiles(list, want). The
     choice is this browser's (ht-profile) and the address's (?profile=2, when the address names one). */
  const ROLE = {owner: "owner", editor: "editor", viewer: "view only"};
  let pick = header.querySelector("#profile");
  if (pick || header.hasAttribute("data-profile")) {
    const fresh = !pick;
    if (fresh) pick = el("select", {id: "profile"});
    pick.className = "iconbtn profile";
    pick.removeAttribute("hidden");
    pick.setAttribute("aria-label", "Profile");
    const wrap = el("div", {class: "profile-pick", id: "profilePick", hidden: ""});
    if (fresh) (header.querySelector(".page-title") || svgLogo).after(wrap); else pick.before(wrap);
    wrap.append(pick, el("span", {class: "profile-one", id: "profileOne", hidden: ""}));
    // before the page's own "change" listeners, so a page that reloads (the records page) opens the new one
    pick.addEventListener("change", () => remember(pick.value), true);
  }
  function remember(id) {
    try { localStorage.setItem("ht-profile", String(id)); } catch (e) {}
    const q = new URLSearchParams(location.search);
    if (!q.has("profile") || q.get("profile") === String(id)) return;
    q.set("profile", id);
    try { history.replaceState(history.state, "", location.pathname + "?" + q + location.hash); } catch (e) {}
  }
  /* fill the picker with the profiles shared with you and choose one: want, else the address's, else this
     browser's last, else the first. Returns the profile's id (null when there are none). */
  function profiles(list, want) {
    if (!pick) return null;
    const wrap = pick.parentElement, one = wrap.querySelector(".profile-one");
    list = list || [];
    if (!list.some(p => p.id === want)) {
      try { want = +(new URLSearchParams(location.search).get("profile") || localStorage.getItem("ht-profile")); } catch (e) {}
    }
    const p = list.find(x => x.id === want) || list[0];
    const label = x => `${x.name} (${ROLE[x.role] || x.role})`;
    pick.replaceChildren(...list.map(x => el("option", {value: x.id, text: label(x)})));
    wrap.hidden = !p;
    if (!p) return null;
    pick.value = p.id;
    remember(p.id);
    pick.hidden = list.length < 2;
    one.hidden = list.length !== 1;
    wrap.classList.toggle("many", list.length > 1);
    one.replaceChildren(el("span", {class: "vh", text: "Profile: "}), el("span", {class: "profile-name", text: p.name}), " ",
      el("span", {class: "profile-role", text: ROLE[p.role] || p.role}));
    return p.id;
  }

  const items = m => [...m.list.querySelectorAll("[role^=menuitem]")].filter(x => !x.hidden);   // menuitem, menuitemradio (#191)
  function menu(id, label, ico, entries) {
    const btn = el("button", {class: "iconbtn menu-btn", type: "button", id: id + "Btn", "aria-haspopup": "menu", "aria-expanded": "false",
      "aria-controls": id + "List"}, ico ? el("span", {class: "menu-ico", "aria-hidden": "true"}, icon(ico)) : null, label);
    const list = el("div", {class: "menu-list", id: id + "List", role: "menu", "aria-labelledby": id + "Btn"}, ...entries);
    for (const x of entries) if (x.getAttribute && x.getAttribute("role") === "menuitem") x.tabIndex = -1;
    return menuWire({btn, list, wrap: el("div", {class: "menu", id}, btn, list)});
  }

  /* each item with its icon before its words (#354), hidden from assistive technology */
  const link = (href, ico, text, attrs = {}) => el("a", {class: "menu-item acct", role: "menuitem", href, ...attrs}, icon(ico), text);
  const action = (id, ico, text, attrs = {}) => el("button", {class: "menu-item", role: "menuitem", type: "button", id, ...attrs}, icon(ico), text);
  const sep = () => el("div", {class: "menu-sep", role: "separator"});

  /* Add: records in, each way opening its section of the Sources page (#166); a journal entry, whose form journal.js
     opens on the records page (from another page, the link takes you there and it opens, #173) */
  const add = menu("addMenu", "Add", "plus", [
    link("sources.html#upload", "upload", "Upload files", {id: "importLink", "data-needs": "import"}),
    link("sources.html#portals", "plug", "Connect a portal or plan", {"data-needs": "import"}),
    link("sources.html#manual", "keyboard", "Type records in", {"data-needs": "import"}),
    link("sources.html#link", "link", "Paste a Health Link", {"data-needs": "import"}),
    sep(), onRecords ? action("journalBtn", "notebook", "Journal entry", {hidden: ""}) : link("./#new-journal-entry", "notebook", "Journal entry", {id: "journalBtn"}),
  ]);
  /* the person's own menu */
  const who = el("span", {id: "who", text: "Account"});
  const user = menu("userMenu", "", "", [
    link("sharing.html", "share-2", "Sharing", {id: "sharingLink"}),
    sep(),
    link("sources.html", "database", "Sources", {id: "sourcesLink"}),
    link("settings.html", "settings", "Settings", {id: "settingsLink"}),
    link("account.html", "circle-user", "Account"),
    link("admin.html", "wrench", "Admin", {id: "adminLink", hidden: ""}),
    link("guide.html", "compass", "Getting started", {id: "guideLink"}),   // the first-run guide, any time (#199)
    action("theme", "sun-moon", "Dark mode"),
    sep(),
    action("logout", "log-out", "Log out"),
  ]);
  user.btn.replaceChildren(who, el("span", {class: "menu-caret", "aria-hidden": "true"}, icon("chevron-down")));
  add.btn.append(el("span", {class: "menu-caret", "aria-hidden": "true"}, icon("chevron-down")));
  user.btn.setAttribute("aria-label", "Your menu");
  // the spacer before the records page's Install app button, so it sits with the menus
  const inst = document.getElementById("installBtn");
  if (inst) inst.before(el("span", {class: "spacer"}));
  else header.append(el("span", {class: "spacer"}));
  // in one box, so where the bar wraps on a phone the two go to the next row together, never your menu alone (#319)
  header.append(el("div", {class: "bar-menus"}, add.wrap, user.wrap));

  /* light or dark: this browser's own choice over the account's default (applyTheme in util.js) */
  const isDark = () => document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  const themeBtn = document.getElementById("theme");
  const themeLabel = () => { themeBtn.lastChild.textContent = isDark() ? "Light mode" : "Dark mode"; };   // after its icon
  themeBtn.onclick = () => { try { localStorage.setItem("ht-theme", isDark() ? "light" : "dark"); } catch (e) {} applyTheme(); themeLabel(); };
  new MutationObserver(themeLabel).observe(document.documentElement, {attributes: true, attributeFilter: ["data-theme"]});
  themeLabel();

  /* the ways in follow #importLink (the records page hides it for someone who can only view); with nothing in it,
     Add goes */
  const setHidden = (x, h) => { if (x.hidden !== h) x.hidden = h; };   // only real changes: the observer below sees these
  function sync() {
    const imp = document.getElementById("importLink");
    for (const x of add.list.querySelectorAll("[data-needs=import]")) if (x !== imp) setHidden(x, imp.hidden);
    setHidden(add.wrap, items(add).length === 0);
    for (const s of add.list.querySelectorAll(".menu-sep")) setHidden(s, imp.hidden || !items(add).some(x => !x.dataset.needs));
  }
  new MutationObserver(sync).observe(add.list, {attributes: true, subtree: true, attributeFilter: ["hidden"]});
  sync();
  /* Admin, for admins only (#167): once the page's own scripts have asked who's signed in */
  let locked = false;
  document.addEventListener("DOMContentLoaded", () => {
    api("me").then(r => r.ok ? r.json() : null).then(me => {
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
    add.wrap.hidden = true;
    for (const x of items(user)) if (x.id !== "logout" && x.id !== "theme") x.hidden = true;
    for (const s of user.list.querySelectorAll(".menu-sep")) s.hidden = true;
    const b = header.querySelector(".brand-link");
    if (b) { b.removeAttribute("href"); b.removeAttribute("title"); }
  }
  /* the bar's height as --bar-h, so what gets focus is scrolled clear of it (scroll-padding in app.css, #202) */
  new ResizeObserver(() => document.documentElement.style.setProperty("--bar-h", header.offsetHeight + "px")).observe(header);
  return {sync, lock, profiles, menu: menuWire};
})();
