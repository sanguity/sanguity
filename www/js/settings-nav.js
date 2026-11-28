/* Sanguity · the Settings page's groups, in the sidebar every page of settings has (sidebar.js, #266), after the
   scripts that draw them, and the page's start. One page in three parts (#991), each a list in the sidebar named by
   it:
   - This person: the profile shown (Dashboard, About this person and its targets, Labs and ranges, Milestones,
     Workouts, Rename or delete; settings.js, about.js, profile-card.js);
   - You: Sign-in and security and This device (account.js, pwa.js; the Account page before), and Display;
   - Install, for admins only: General (#998), Accounts and sign-in, Backups, Logs and activity, Connected sources
     (experimental), Outside connections and About (#1153; admin.js and the scripts after it; the Admin page before),
     with the admins' notices (NOTICES) over each.
   The old Account and Admin pages' addresses open their group here (moved.js), each part with the id it had.

   Each section (#sec-<id> from the server's list) belongs to a group, and the other cards to theirs (groups.js). The
   sidebar lists the groups that have something to show this person, once this person's settings are drawn (You's
   and Install's then too, so the first group is the same from the start); the address follows the group
   (settings.html#display), and an address naming a section (settings.html#sec-units, #passkeys) opens that section's
   group and scrolls to it. "Find in Settings" shows, across every group, only the settings whose name or help
   mentions what's typed.

   Under the group's title, whose settings these are (WHO): this profile's, for everyone it's shared with (or shown as
   they are, to someone who can only view it), just yours (and what the group's for, its hint: Sign-in and security,
   This device), or the server's, for its admins. */
"use strict";
let SET_NAV = null;   // the sidebar (sideNav), for account.js's and admin.js's again() once their cards are drawn
(() => {
  const main = document.querySelector("main.sets");
  if (!main) return;
  const GROUPS = SG_PAGES.settings.groups;   // groups.js: one list, which the command palette searches too (#273)
  /* whose settings each group holds: the registry's sections say it by their settings' scope (data-scope); a group
     missing here goes by that */
  const WHO = {dashboard: "profile", about: "profile", insurance: "profile", labs: "profile", milestones: "profile", activity: "profile", profile: "profile",
    security: "you", device: "you", display: "you"};
  const SAY = {
    profile: ["This profile", ", for everyone it's shared with."],
    view: ["This profile", ": you can view it, so these are shown as they are; its owners and editors change them."],
    you: ["Just for you", ", on every profile you see."],
    own: ["Just for you", "."],   // someone with no profile yet (settings.js sets no role then)
    server: ["This server", ", for everyone on it."],
    install: ["This server", ", for its admins."],   // Install (#991)
  };
  const SCOPE_WHO = {profile: "profile", user: "you", install: "server"};
  function whoOf(g) {
    let w = g.admin ? "install" : WHO[g.id];
    if (!w) {
      const scopes = new Set(nav.partsOf(g).flatMap(p => [...p.querySelectorAll(".set[data-scope]")].map(s => s.dataset.scope)));
      w = scopes.size === 1 ? SCOPE_WHO[[...scopes][0]] : scopes.size ? "profile" : null;
    }
    return w === "profile" && main.dataset.role === "viewer" ? "view" : w === "you" && main.dataset.role === "" ? "own" : w;
  }
  /* an address naming a group this person's role on the profile doesn't give them (settings.html#profile, or
     sharing.html#profile moved here, for an editor or a viewer), or one of Install's, someone who isn't an admin
     (admin.html#backups, from an old bookmark; #991): once it's known (data-role, settings.js; ready, below), the
     group's why is said under the title of the one shown instead, until another is, and the address names that one
     (#419); as a note, in the style of the line above it, not a save's (#425) */
  const RANK = {viewer: 0, editor: 1, owner: 2}, said = document.getElementById("loadMsg");
  const NOT_ADMIN = () => "Install is for this server's admins, and your account isn't one: for an account for someone else, " +
    (sgConnected(ME) ? "a portal or health plan set up, " : "") + "or anything else about the whole server, ask your admin.";   // (#1096: portals while they're on)
  let told = null, ready = false, locked = false;
  function whyNot(hash = location.hash) {
    const h = decodeURIComponent(hash.slice(1)), role = main.dataset.role, g = GROUPS.find(x => x.id === h || x.has.includes(h));
    if (told || !ready || !g) return;
    const why = g.admin ? !ME.user.is_admin && NOT_ADMIN() : role && g.why && RANK[role] < RANK[g.needs] && g.why;
    if (!why) return;
    told = why;
    sayIn(said, el("span", {class: "set-who", text: told}));
    if (nav.current()) history.replaceState(null, "", "#" + nav.current().id);
  }
  const who = document.getElementById("introHint");
  const NOTICES = ["secNotice", "codeNotice", "syncNotice", "backupNotice"].map(id => document.getElementById(id)), none = document.getElementById("noProfiles");
  // Install's parts with changes to save (the backups kept, the sync defaults' numbers: admin-backups.js, admin-sources.js)
  const ADM = {"gen-host": [hostSave, hostDiscard], "signin-rules": [genSecSave, genSecDiscard], "signin-provider": [signinSave, signinDiscard],
               logging: [genBkSave, genBkDiscard],   // Logs and activity's (General's before #1153): the days of activity kept (gen-backups.js)
               "gen-phone": [phoneSave, phoneDiscard], "gen-notify": [notifySave, notifyDiscard], "gen-restart": [genRsSave, genRsDiscard], backups: [bkKeepSave, bkKeepDiscard], "sec-sync": [syncSave, syncDiscard]};
  const nav = SET_NAV = sideNav({id: "setNav", page: "Settings", groups: GROUPS, find: "Find in Settings", none: q => `Nothing in Settings matches “${q}”.`,
    // the groups with something to show: a part that's there, not hidden by its own script; locked, Sign-in and security alone
    allowed: (g, parts) => ready && parts.some(p => !p.hidden) && (!locked || g.id === "security"),
    alias: {install: "installApp", offline: "offlineRecords", sources: "connected"},   // Account's short ones; Settings' Connected sources before #167 (and since #1153)
    shown: () => { if (told && said.textContent === told) sayIn(said, ""); },   // (whyNot's, said for the group first shown)
    label: g => { const w = SAY[whoOf(g)]; return g.title + (w ? " · " + w[0] : ""); },
    intro: (g, q) => {
      const w = g && SAY[whoOf(g)];   // (and what it is, where it says: "Just for you: how you sign in…")
      who.replaceChildren(...(q ? [`Matching “${q}”, from every group.`] : w ? [el("strong", {text: w[0]}), g.hint ? ": " + g.hint : w[1]] : []));
      // what's said over a part's groups only (#991): the admins' notices over Install's, and no profile yet over You's
      const part = !q && g?.part;
      for (const n of NOTICES) n.classList.toggle("grp-off", part !== "Install");
      none.classList.toggle("grp-off", part !== "You");
      if (typeof bkMark === "function") bkMark();   // "late" beside Backups, its link drawn again (admin-backups.js)
    },
    watch: [document.getElementById("sections")],   // the sections arrive later (the server's list), one by one
    // the group's changes, one bar (#272): this person's and Display's (settings.js), Install's
    bar: {save: async (parts, msg, had) => {
      const own = parts.filter(p => !ADM[p.id]);
      if (own.length) await saveParts(own, msg, had);
      // (#1049) a part's save left waiting on its confirmation (General → Host's typed one: focus in it, the part still
      // changed) keeps focus and its words in the bar; the parts after it save without taking focus, theirs said after
      let wait = null;
      const after = [];
      for (const p of parts) if (ADM[p.id]) {
        const m = wait == null ? msg : el("span");
        await ADM[p.id][0](m, had && wait == null);
        if (wait != null && m.textContent) { after.push(" ", ...m.childNodes); sayIn(msg, [wait, ...after], m.classList.contains("bad")); }
        else if (wait == null && p.hasAttribute("data-dirty") && p.contains(document.activeElement)) wait = msg.textContent;
      }
    },
    discard: parts => { const own = parts.filter(p => !ADM[p.id]); if (own.length) discardParts(own); for (const p of parts) if (ADM[p.id]) ADM[p.id][1](); },
    profile: () => PID, keeps: p => !!ADM[p.id]}});   // (Install's don't change with the profile shown)
  new MutationObserver(() => whyNot()).observe(main, {attributes: true, attributeFilter: ["data-role"]});
  // another address on this page (settings.html#accounts followed from Settings): its why said, once the sidebar has
  // shown the group it shows instead, and the last one's gone
  addEventListener("hashchange", () => {
    const h = location.hash;
    if (told && said.textContent === told) sayIn(said, "");
    told = null;
    setTimeout(() => whyNot(h));
  });

  /* ---------------------------------------------------------------- start */
  (async () => {
    if (sgMoved()) return;   // what was here and isn't about how things look (moved.js)
    nav.start();
    try { ME = await call("me"); } catch (e) { return; }
    await datesReady();   // dates in this person's time zone and format (dates.js)
    $("#who").textContent = ME.user.username;
    { const w = plainHttpWarning(); if (w) { $("#insecure").textContent = w; $("#insecure").hidden = false; } }
    $("#intro").hidden = false;
    // a one-time password to replace, or a second factor the server requires: that alone, the sidebar put away (account.js)
    if (acctLock()) { locked = ready = true; nav.hide(); nav.follow(); return; }
    const first = setStart();   // this person's (settings.js)
    acctStart(first);           // You's (account.js)
    adminStart(first);          // Install's, for admins (admin.js)
    await first;
    ready = true;
    nav.refresh();
    whyNot();
  })();
})();
