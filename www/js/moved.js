/* Sanguity · what moved: old addresses send you on to where it went, keeping the address's ?query (the profile,
   ?profile=2). Four ways:
   - <body data-moved="sharing.html#summary">: straight there (#168: summary.html).
   - the Sources page's old pages (#166): import.html (Upload files; #manual, #link, #payment, #files,
     #import-<id> as they were), connect.html (Portals and health plans; #admin is Settings' Connected-source apps,
     #167, #991), providers.html (finding your provider is in the connect step there).
   - a page that went into another whole (SG_INTO, #991): Account and Admin are Settings' You and Install, each
     group and part with the same id there, so account.html#passkeys opens settings.html#passkeys; the page's own
     address opens its first group, and an id that's another's there, or went elsewhere, is said (Admin's #activity
     and #labs: Workouts and Labs and ranges are Settings' own; Account's old Profiles tab went to Sharing, #168).
   - a part of a page that went to another, by its #: SG_MOVED, on arriving and as the # changes. sgMoved() does it
     there, and says whether it did (the page then draws nothing). Settings is how things look (#169): the phone's
     upload secret (#166) went to Sources, the calendar feed (#168) to Sharing; the server's (#167) went to Admin and
     came back with it (#991). About this person and Targets went from Sharing to Settings (#267), and so did renaming
     and deleting a profile (#402), while New profile is the profile menu's and the visit summary's measures ticked
     at first went from Settings to Sharing; Install's Lab name matches, to each profile's (#1114); General's Security and Sign-in provider, to Accounts and sign-in (#1119); General's Backups and logging, to Backups' Folder and
     key (and Logs and activity's Logging), its Experimental, to Connected sources (with Connected-source apps), and
     its About, to an About of its own (#1153). A "?" on its own in a new address (before its # or at its end) is the old
     one's query.
   Each old page also says where it went, for a browser that doesn't run this. */
"use strict";
const SG_MOVED = {
  "settings.html": {labmaps: "settings.html?#labmatches", watch: "sources.html?#phone", calendar: "sharing.html?#calendar", "sec-summary": "sharing.html?#sumTicked",
    "gen-security": "settings.html?#signin-rules", "gen-signin": "settings.html?#signin-provider",   // (#1119: General's, now Accounts and sign-in's)
    "gen-backups": "settings.html?#bk-files", "gen-experimental": "settings.html?#cs-switch", "gen-about": "settings.html?#install-about",
    apps: "settings.html?#admin"},   // (#1153: General's, and Install's Connected-source apps group, now in Connected sources)
  "sharing.html": {about: "settings.html?#about", targets: "settings.html?#targets", profile: "settings.html?#profile",
    profileCard: "settings.html?#profile", newProfile: "guide.html?new"},
};
const SG_INTO = {
  "account.html": {to: "settings.html", first: "security", ids: {profiles: "sharing.html?#people", admin: "general"}},
  "admin.html": {to: "settings.html", first: "general", ids: {activity: "installLog", labs: "labmatches", labmaps: "labmatches"}},
};
function sgMoved() {
  const to = (SG_MOVED[location.pathname.split("/").pop()] || {})[decodeURIComponent(location.hash.slice(1))];
  if (to) location.replace(to.replace(/\?(?=#|$)/, location.search));
  return !!to;
}
(() => {
  const to = document.body.dataset.moved;
  if (to) {
    const u = new URL(to, location.href);
    if (!u.search) u.search = location.search;
    location.replace(u.href);
    return;
  }
  const page = location.pathname.split("/").pop(), h = location.hash.slice(1), sources = "sources.html" + location.search;
  const into = SG_INTO[page];
  if (into) {
    const id = into.ids[decodeURIComponent(h)] || h || into.first;
    location.replace(id.includes(".html") ? id.replace(/\?(?=#|$)/, location.search) : `${into.to}${location.search}#${id}`);
    return;
  }
  if (page in SG_MOVED) { sgMoved(); addEventListener("hashchange", sgMoved); return; }
  location.replace(page === "connect.html" && h === "admin" ? "settings.html#admin"
    : page === "import.html" ? `${sources}#${h || "upload"}` : `${sources}#portals`);
})();
