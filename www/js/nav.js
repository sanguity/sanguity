/* Sanguity · the top bar on every signed-in page (after util.js and pwa.js, before the page's own script).

   One row: the brand (back to the records), the page's title or, on the records page, its profile picker and tabs,
   then two menus. Add: import records, connect a source, type records in, a journal entry. The person's own menu:
   visit summary, share links, settings, account, Admin (admins only: nav.js asks the API itself, #167), the
   light/dark toggle, log out. The items keep the ids the pages already use (#importLink, #journalBtn, #summaryLink,
   #settingsLink, #theme, #logout, #who), so each page's script sets them up as before; nav.js only builds the bar and
   opens and closes the menus.

   <header class="topbar" data-title="Settings"> gets the bar; anything already inside it (the records page's
   profile picker, tabs and Install app button) stays where it is, between the brand and the menus. */
"use strict";
const SgNav = (() => {
  const header = document.querySelector("header.topbar");
  if (!header) return {sync() {}, lock() {}};
  const svgLogo = header.querySelector("h1");
  const onRecords = !!document.getElementById("tab-overview");

  /* the brand: a link back to the records, except on the records page itself */
  if (svgLogo && !onRecords) {
    const a = el("a", {class: "brand-link", href: "./", title: "Back to your records"});
    a.append(...svgLogo.childNodes);
    svgLogo.append(a);
  }
  if (header.dataset.title) svgLogo.after(el("span", {class: "page-title", text: header.dataset.title}));

  let openMenu = null;
  function close(m, focusBtn) {
    if (!m) return;
    m.list.hidden = true; m.btn.setAttribute("aria-expanded", "false");
    if (focusBtn) m.btn.focus();
    if (openMenu === m) openMenu = null;
  }
  const items = m => [...m.list.querySelectorAll("[role=menuitem]")].filter(x => !x.hidden);
  function menu(id, label, icon, entries) {
    const btn = el("button", {class: "iconbtn menu-btn", type: "button", id: id + "Btn", "aria-haspopup": "menu", "aria-expanded": "false",
      "aria-controls": id + "List"}, el("span", {class: "menu-ico", "aria-hidden": "true", text: icon}), label);
    const list = el("div", {class: "menu-list", id: id + "List", role: "menu", "aria-labelledby": id + "Btn", hidden: ""}, ...entries);
    const m = {btn, list, wrap: el("div", {class: "menu", id}, btn, list)};
    for (const x of entries) if (x.getAttribute && x.getAttribute("role") === "menuitem") x.tabIndex = -1;
    btn.onclick = () => {
      if (openMenu === m) return close(m);
      close(openMenu);
      list.hidden = false; btn.setAttribute("aria-expanded", "true"); openMenu = m;
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
    list.addEventListener("click", ev => { if (ev.target.closest("[role=menuitem]")) setTimeout(() => close(m)); });
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
    link("summary.html", "Visit summary", {id: "summaryLink"}),
    link("account.html#profiles", "Share links"),
    sep(),
    link("sources.html", "Sources", {id: "sourcesLink"}),
    link("settings.html", "Settings", {id: "settingsLink"}),
    link("account.html", "Account"),
    link("admin.html", "Admin", {id: "adminLink", hidden: ""}),
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
  header.append(add.wrap, user.wrap);

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
      if (me && me.user && me.user.is_admin && !locked) document.getElementById("adminLink").hidden = false;
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
  return {sync, lock};
})();
