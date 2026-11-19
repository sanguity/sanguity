/* Sanguity · the Sources page (#166): everything about records coming into a profile, one section at a time, with a
   sidebar of them like Settings' and Admin's. Loaded before the parts' own scripts (import*.js, connect.js,
   providers.js, sources-watch.js, sources-overview.js), which draw their sections as they did on their own pages.

   This script signs the page in (who's signed in, the plain-HTTP warning, Log out), fills the one Profile choice every
   section follows (#profile, the top bar's picker: nav.js, #170), and shows one group of sections at a time, in the
   sidebar every account page has (sidebar.js, #266): the Overview, then what stays Connected (portals and health
   plans, the phone and watch), what comes in One-off (files, typed in, a Health Link) and the History. The address
   follows the group (sources.html#portals); an address naming something inside a section (#import-12, a record's
   Source link, #100) opens that section's group, and the old pages' addresses (import.html#manual, connect.html,
   providers.html, settings.html#watch: moved.js) land on theirs.

   window.SgSources: ready (a promise of /api/me, or null when there's nothing to show), pid(), profile(),
   canEdit() (owner or editor of the profile chosen), show(group). Choosing another profile is a "change" on
   #profile, as before; showing another group is a "sources:group" event on document. */
"use strict";   // $, el and api come from util.js
const SgSources = (() => {
  const main = $("main.src"), sel = $("#profile");
  const GROUPS = SG_PAGES.sources.groups;   // groups.js: one list, which the command palette searches too (#273)
  let ME = null;
  const profile = () => ME && ME.profiles.find(p => p.id === +sel.value) || null;
  const canEdit = () => { const p = profile(); return !!p && (p.role === "owner" || p.role === "editor"); };

  /* ---------------------------------------------------------------- the sidebar */
  const nav = sideNav({id: "srcNav", page: "Sources", groups: GROUPS, find: "Find in Sources", none: q => `Nothing in Sources matches “${q}”.`,
    alias: {zip: "upload", folder: "upload", files: "upload", payment: "manual", "home-reading": "manual", watch: "phone"},   // Add → Home reading (#264);   // the old pages' addresses (import.html#manual, #link, #zip, #payment; settings.html#watch: moved.js)
    later: [[/^import-\d+$/, "history"], [/^conn-\d+$/, "portals"]],   // drawn after the page loads: an import in the history (a record's Source link, #100), a connection
    intro: hint, shown: g => document.dispatchEvent(new CustomEvent("sources:group", {detail: g.id}))});
  const show = (id, opts) => nav.show(id, opts);
  nav.start();
  /* the way back to the getting-started guide (#199): its links here carry ?guide=records or ?guide=phone */
  const fromGuide = new URLSearchParams(location.search).get("guide"), back = el("a", {class: "btn primary small", id: "guideBack", text: "Back to the guide"});
  const backTo = () => { back.href = `guide.html?profile=${encodeURIComponent(sel.value || new URLSearchParams(location.search).get("profile") || "")}#${fromGuide}`; };
  if (fromGuide === "records" || fromGuide === "phone") {
    backTo();
    sel.addEventListener("change", backTo);
    nav.nav.after(el("p", {class: "guide-back", id: "guideBar"}, el("span", {text: fromGuide === "phone"
      ? "Getting started: set up the phone here, then go back to the guide." : "Getting started: bring in the first records here, then go back to the guide."}), back));
  }
  // the import history's section comes from its own script (import-history.js), after this one
  document.addEventListener("DOMContentLoaded", nav.again);

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
  $("#logout").onclick = logOut;   // util.js: asks first about what isn't saved (#391)
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
    if (!me.profiles.length) { nav.hide(); $("#noProfiles").hidden = false; return null; }
    ME = me;
    SgNav.profiles(me.profiles);   // the top bar's picker (nav.js, #170)
    backTo();   // #199
    sel.addEventListener("change", hint);
    $("#intro").hidden = false;
    hint();
    return me;
  })();
  return {ready, pid: () => +sel.value || null, profile, canEdit, show: id => show(id, {push: true})};
})();
