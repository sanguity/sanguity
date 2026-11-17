/* Sanguity · the Sources page (#166): everything about records coming into a profile, one section at a time, with a
   sidebar of them like Settings' and Admin's. Loaded before the parts' own scripts (import*.js, connect.js,
   providers.js, sources-watch.js, sources-overview.js), which draw their sections as they did on their own pages.

   This script signs the page in (who's signed in, the plain-HTTP warning, Log out), fills the one Profile choice every
   section follows (#profile, the top bar's picker: nav.js, #170), and shows one group of
   sections at a time. The address follows the group (sources.html#portals); an address naming something inside a
   section (#import-12, a record's Source link, #100) opens that section's group, and the old pages' addresses
   (import.html#manual, connect.html, providers.html, settings.html#watch: moved.js) land on theirs.

   window.SgSources: ready (a promise of /api/me, or null when there's nothing to show), pid(), profile(),
   canEdit() (owner or editor of the profile chosen), show(group). Choosing another profile is a "change" on
   #profile, as before; showing another group is a "sources:group" event on document. */
"use strict";   // $, el and api come from util.js
const SgSources = (() => {
  const main = $("main.src"), sel = $("#profile");
  const GROUPS = [
    {id: "overview", title: "Overview", has: ["srcOverview"]},
    {id: "upload", title: "Upload files", has: ["importer", "viewOnly"]},
    {id: "manual", title: "Type records in", has: ["typeIn", "viewOnly"]},
    {id: "link", title: "Paste a Health Link", has: ["healthLink", "viewOnly"]},
    {id: "portals", title: "Portals and health plans", has: ["mine"]},
    {id: "phone", title: "Phone and watch", has: ["watch", "viewOnly"]},
    {id: "history", title: "Import history", has: ["history"]},
  ];
  /* the old pages' addresses (import.html#manual, #link, #zip, #payment; settings.html#watch: moved.js) */
  const ALIAS = {zip: "upload", folder: "upload", files: "upload", payment: "manual", watch: "phone"};
  /* what's drawn after the page loads: an import in the history (a record's Source link, #100), a connection */
  const LATER = [[/^import-\d+$/, "history"], [/^conn-\d+$/, "portals"]];
  let ME = null, current = null;
  const profile = () => ME && ME.profiles.find(p => p.id === +sel.value) || null;
  const canEdit = () => { const p = profile(); return !!p && (p.role === "owner" || p.role === "editor"); };

  /* ---------------------------------------------------------------- the sidebar */
  const list = el("ul", {class: "set-nav-list"});
  const nav = el("nav", {class: "set-nav", id: "srcNav", "aria-label": "Sources sections"}, list);
  list.replaceChildren(...GROUPS.map(g => el("li", {}, el("a", {href: "#" + g.id, "data-group": g.id, text: g.title,
    onclick: ev => { ev.preventDefault(); show(g.id, {push: true}); }}))));
  function apply() {
    for (const g of GROUPS) for (const id of g.has) {
      const part = document.getElementById(id);
      if (part) part.classList.toggle("grp-off", !current.has.includes(id));
    }
    for (const a of list.querySelectorAll("a")) {
      a.setAttribute("aria-current", a.dataset.group === current.id ? "page" : "false");
      // on a phone the sidebar is a row that scrolls sideways: the group shown stays in sight
      if (a.dataset.group === current.id && list.scrollWidth > list.clientWidth) list.scrollLeft = a.parentElement.offsetLeft - list.offsetLeft - 16;
    }
    main.dataset.group = current.id;
    hint();
  }
  function show(id, {push = false, scrollTo = null} = {}) {
    const g = GROUPS.find(x => x.id === (ALIAS[id] || id)) || GROUPS[0], changed = g !== current;
    current = g;
    apply();
    viewTitle("Sources: " + g.title, push && changed && "Sources: " + g.title);   // the browser tab's title; said when picked (#209)
    if (push && location.hash.slice(1) !== g.id) history.replaceState(null, "", "#" + g.id);
    if (scrollTo) scrollTo.scrollIntoView({block: "start"});
    else if (push) window.scrollTo({top: 0});
    if (changed) document.dispatchEvent(new CustomEvent("sources:group", {detail: g.id}));
  }
  /* #portals: a group; #import-12, #conn-3, #history: what's in a group's section */
  function fromHash() {
    const h = decodeURIComponent(location.hash.slice(1));
    if (!h) return {id: "overview"};
    if (GROUPS.some(g => g.id === h) || ALIAS[h]) return {id: h};
    const t = document.getElementById(h);
    const g = t && GROUPS.find(x => x.has.some(id => { const s = document.getElementById(id); return s && s.contains(t); }));
    if (g) return {id: g.id, el: t};
    const later = LATER.find(([re]) => re.test(h));
    return {id: later ? later[1] : "overview"};
  }
  const follow = () => { const r = fromHash(); show(r.id, {scrollTo: r.el}); };
  main.prepend(nav);
  main.classList.add("with-nav");
  follow();
  addEventListener("hashchange", follow);
  // the import history's section comes from its own script (import-history.js), after this one
  document.addEventListener("DOMContentLoaded", () => { if (fromHash().el) follow(); else apply(); });

  /* ---------------------------------------------------------------- whose records, and what you can do here */
  function hint() {
    const p = profile();
    if (!p) return;
    main.dataset.role = canEdit() ? "editor" : "viewer";
    $("#viewOnly").hidden = canEdit();
    $("#introHint").replaceChildren(el("strong", {text: p.name}), canEdit()
      ? ": what you add here goes into this profile, for everyone it's shared with."
      : ": you can view this profile, so you see what reaches it; its owners and editors add records.");
  }

  /* ---------------------------------------------------------------- start */
  $("#logout").onclick = async () => { try { await api("logout", {method: "POST"}); } catch (e) {} location.replace("login.html"); };
  const ready = (async () => {
    let r;
    try { r = await api("me"); } catch (e) { return null; }
    if (r.status === 401) { location.replace("login.html"); return null; }
    if (!r.ok) return null;
    const me = await r.json();
    await datesReady();   // dates in this person's time zone and format (dates.js)
    if (me.user.must_change_pw) { location.replace("account.html#password"); return null; }
    if (me.user.needs_two_factor) { location.replace("account.html#twofa"); return null; }
    $("#who").textContent = me.user.username;
    { const w = plainHttpWarning(); if (w) { $("#insecure").textContent = w; $("#insecure").hidden = false; } }
    if (!me.profiles.length) { nav.hidden = true; main.classList.remove("with-nav"); $("#noProfiles").hidden = false; return null; }
    ME = me;
    SgNav.profiles(me.profiles);   // the top bar's picker (nav.js, #170)
    sel.addEventListener("change", hint);
    $("#intro").hidden = false;
    hint();
    return me;
  })();
  return {ready, pid: () => +sel.value || null, profile, canEdit, show: id => show(id, {push: true})};
})();
