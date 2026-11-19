/* Sanguity · the Settings page's groups, in the sidebar every account page has (sidebar.js, #266), after settings.js,
   which draws the sections. Settings is how things look (#169): this profile's (Dashboard, About this person and its
   targets, Labs and ranges, Milestones, Workouts, Rename or delete) and the person's own (Display). What else was
   here has its own page now (moved.js).

   Each section (#sec-<id> from the server's list) belongs to a group, and About this person's two cards (#about,
   #targets: about.js, #267) to theirs. The sidebar lists the groups that have something to show this person; the
   address follows the group (settings.html#display), and an address naming a section (settings.html#sec-units) opens
   that section's group and scrolls to it. "Find in Settings" shows, across every group, only the settings whose name or
   help mentions what's typed.

   Under the group's title, whose settings these are (WHO): this profile's, for everyone it's shared with (or shown as
   they are, to someone who can only view it), or just yours. */
"use strict";
(() => {
  const main = document.querySelector("main.sets");
  if (!main) return;
  const GROUPS = SG_PAGES.settings.groups;   // groups.js: one list, which the command palette searches too (#273)
  /* whose settings each group holds: the registry's sections say it by their settings' scope (data-scope); a group
     missing here goes by that */
  const WHO = {dashboard: "profile", about: "profile", labs: "profile", milestones: "profile", activity: "profile", profile: "profile", display: "you"};
  const SAY = {
    profile: ["This profile", ", for everyone it's shared with."],
    view: ["This profile", ": you can view it, so these are shown as they are; its owners and editors change them."],
    you: ["Just for you", ", on every profile you see."],
    own: ["Just for you", "."],   // someone with no profile yet (settings.js sets no role then)
    server: ["This server", ", for everyone on it."],
  };
  const SCOPE_WHO = {profile: "profile", user: "you", install: "server"};
  function whoOf(g) {
    let w = WHO[g.id];
    if (!w) {
      const scopes = new Set(nav.partsOf(g).flatMap(p => [...p.querySelectorAll(".set[data-scope]")].map(s => s.dataset.scope)));
      w = scopes.size === 1 ? SCOPE_WHO[[...scopes][0]] : scopes.size ? "profile" : null;
    }
    return w === "profile" && main.dataset.role === "viewer" ? "view" : w === "you" && main.dataset.role === "" ? "own" : w;
  }
  /* an address naming a group this person's role on the profile doesn't give them (settings.html#profile, or
     sharing.html#profile moved here, for an editor or a viewer): once the role is known (data-role, settings.js), the
     group's why is said under the title of the one shown instead, until another is, and the address names that one
     (#419); as a note, in the style of the line above it, not a save's (#425) */
  const RANK = {viewer: 0, editor: 1, owner: 2}, said = document.getElementById("loadMsg");
  let told = null;
  function whyNot() {
    const h = decodeURIComponent(location.hash.slice(1)), role = main.dataset.role, g = GROUPS.find(x => x.id === h || x.has.includes(h));
    if (told || !role || !g?.why || RANK[role] >= RANK[g.needs]) return;
    told = g.why;
    sayIn(said, el("span", {class: "set-who", text: told}));
    if (nav.current()) history.replaceState(null, "", "#" + nav.current().id);
  }
  const who = document.getElementById("introHint");
  const nav = sideNav({id: "setNav", page: "Settings", groups: GROUPS, find: "Find in Settings", none: q => `Nothing in Settings matches “${q}”.`,
    allowed: (g, parts) => parts.some(p => !p.hidden),   // the groups with something to show: a part that's there, not hidden by its own script
    shown: () => { if (told && said.textContent === told) sayIn(said, ""); },   // (whyNot's, said for the group first shown)
    label: g => { const w = SAY[whoOf(g)]; return g.title + (w ? " · " + w[0] : ""); },
    intro: (g, q) => who.replaceChildren(...(q ? [`Matching “${q}”, from every group.`] : g && SAY[whoOf(g)] ? [el("strong", {text: SAY[whoOf(g)][0]}), SAY[whoOf(g)][1]] : [])),
    watch: [document.getElementById("sections")],   // the sections arrive later (the server's list), one by one
    bar: {save: saveParts, discard: discardParts, profile: () => PID}});   // the group's changes, one bar (#272, settings.js)
  nav.start();
  new MutationObserver(whyNot).observe(main, {attributes: true, attributeFilter: ["data-role"]});
})();
