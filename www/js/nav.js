/* Sanguity · the top bar on every signed-in page (after util.js and pwa.js, before the page's own script).

   One row: the brand (back to the records), the page's title, the profile picker on the pages about a profile
   (#170), the records page's tabs, then two menus. Add: import records, connect a source, type records in, a
   journal entry. The person's own menu: sharing (who sees the profile, share links, calendar feed, visit summary,
   export: #168), settings, account, Admin (admins only: nav.js asks the API itself, #167), Getting started (the
   first-run guide, #199), the light/dark toggle, log out. The items keep the ids the pages already use (#importLink,
   #journalBtn, #settingsLink, #theme, #logout, #who), so each page's script sets them up as before; nav.js only
   builds the bar and opens and closes the menus.

   <header class="topbar" data-title="Settings" data-profile> gets the bar, with the profile picker for data-profile;
   anything already inside it (the records page's profile picker, tabs and Install app button) stays where it is,
   between the brand and the menus. */
"use strict";
const SgNav = (() => {
  const header = document.querySelector("header.topbar");
  if (!header) return {sync() {}, lock() {}, profiles() { return null; }, menu() {}};
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

  let openMenu = null;
  function close(m, focusBtn) {
    if (!m) return;
    m.list.hidden = true; m.btn.setAttribute("aria-expanded", "false");
    if (focusBtn) m.btn.focus();
    if (openMenu === m) openMenu = null;
  }
  const items = m => [...m.list.querySelectorAll("[role^=menuitem]")].filter(x => !x.hidden);   // menuitem, menuitemradio (#191)
  function menu(id, label, icon, entries) {
    const btn = el("button", {class: "iconbtn menu-btn", type: "button", id: id + "Btn", "aria-haspopup": "menu", "aria-expanded": "false",
      "aria-controls": id + "List"}, el("span", {class: "menu-ico", "aria-hidden": "true", text: icon}), label);
    const list = el("div", {class: "menu-list", id: id + "List", role: "menu", "aria-labelledby": id + "Btn", hidden: ""}, ...entries);
    for (const x of entries) if (x.getAttribute && x.getAttribute("role") === "menuitem") x.tabIndex = -1;
    return wire({btn, list, wrap: el("div", {class: "menu", id}, btn, list)});
  }
  /* a menu's behaviour, for one built above or in a page's own markup (the records bar's More… periods, #191): m is
     {btn, list, wrap, onOpen?}; its items are the list's [role^=menuitem] (tabindex -1), replaceable while it's closed */
  function wire(m) {
    const {btn, list} = m;
    btn.onclick = () => {
      if (openMenu === m) return close(m);
      close(openMenu);
      list.hidden = false; btn.setAttribute("aria-expanded", "true"); openMenu = m;
      m.onOpen?.();
    };
    btn.onkeydown = ev => {
      if (ev.key === "ArrowDown" || ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        if (openMenu !== m) btn.onclick();
        items(m)[0]?.focus();
      }
    };
    list.onkeydown = ev => {
      const all = items(m), i = all.indexOf(document.activeElement);
      const to = {ArrowDown: all[(i + 1) % all.length], ArrowUp: all[(i - 1 + all.length) % all.length], Home: all[0], End: all.at(-1)}[ev.key];
      if (to) { ev.preventDefault(); to.focus(); }
      else if (ev.key === "Escape") { ev.preventDefault(); close(m, true); }
      else if (ev.key === "Tab") close(m);
    };
    list.addEventListener("click", ev => { if (ev.target.closest("[role^=menuitem]")) setTimeout(() => close(m)); });
    return m;
  }
  document.addEventListener("click", ev => { if (openMenu && !openMenu.wrap.contains(ev.target)) close(openMenu); });
  document.addEventListener("keydown", ev => { if (ev.key === "Escape" && openMenu) close(openMenu, true); });

  const link = (href, text, attrs = {}) => el("a", {class: "menu-item acct", role: "menuitem", href, text, ...attrs});
  const action = (id, text, attrs = {}) => el("button", {class: "menu-item", role: "menuitem", type: "button", id, text, ...attrs});
  const sep = () => el("div", {class: "menu-sep", role: "separator"});

  /* Add: records in, each way opening its section of the Sources page (#166); a journal entry, whose form journal.js
     opens on the records page (from another page, the link takes you there and it opens, #173) */
  const add = menu("addMenu", "Add", "+", [
    link("sources.html#upload", "Upload files", {id: "importLink", "data-needs": "import"}),
    link("sources.html#portals", "Connect a portal or plan", {"data-needs": "import"}),
    link("sources.html#manual", "Type records in", {"data-needs": "import"}),
    link("sources.html#link", "Paste a Health Link", {"data-needs": "import"}),
    sep(), onRecords ? action("journalBtn", "Journal entry", {hidden: ""}) : link("./#new-journal-entry", "Journal entry", {id: "journalBtn"}),
  ]);
  /* the person's own menu */
  const who = el("span", {id: "who", text: "Account"});
  const user = menu("userMenu", "", "", [
    link("sharing.html", "Sharing", {id: "sharingLink"}),
    sep(),
    link("sources.html", "Sources", {id: "sourcesLink"}),
    link("settings.html", "Settings", {id: "settingsLink"}),
    link("account.html", "Account"),
    link("admin.html", "Admin", {id: "adminLink", hidden: ""}),
    link("guide.html", "Getting started", {id: "guideLink"}),   // the first-run guide, any time (#199)
    action("theme", "Dark mode"),
    sep(),
    action("logout", "Log out"),
  ]);
  user.btn.replaceChildren(who, el("span", {class: "menu-caret", "aria-hidden": "true", text: "▾"}));
  add.btn.append(el("span", {class: "menu-caret", "aria-hidden": "true", text: "▾"}));
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
  const themeLabel = () => { themeBtn.textContent = isDark() ? "Light mode" : "Dark mode"; };
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
        if (me.backups_late) a.append(" ", el("span", {class: "badge bk-late", text: "⚠ backups late"}));   // #200: Admin → Backups says why
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
  return {sync, lock, profiles, menu: wire};
})();
