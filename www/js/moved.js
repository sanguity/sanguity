/* Sanguity · what moved: old addresses send you on to where it went, keeping the address's ?query (the profile,
   ?profile=2). Three ways:
   - <body data-moved="sharing.html#summary">: straight there (#168: summary.html).
   - the Sources page's old pages (#166): import.html (Upload files; #manual, #link, #payment, #files,
     #import-<id> as they were), connect.html (Portals and health plans; #admin is on the Admin page, #167),
     providers.html (finding your provider is in the connect step there).
   - a part of a page that went to another, by its #: SG_MOVED, on arriving and as the # changes. sgMoved() does it
     there, and says whether it did (the page then draws nothing). Settings is how things look (#169): the server's
     (#167) went to Admin, the phone's upload secret (#166) to Sources, the calendar feed (#168) to Sharing. Account's
     Profiles tab went to Sharing (#168) and its Admin tab to Admin (#167). About this person and Targets went from
     Sharing to Settings (#267). A "?" in a new address is the old one's query.
   Each old page also says where it went, for a browser that doesn't run this. */
"use strict";
const SG_MOVED = {
  "settings.html": {outside: "admin.html#outside", sources: "admin.html#apps", "sec-sync": "admin.html#sec-sync",
    watch: "sources.html?#phone", calendar: "sharing.html?#calendar"},
  "account.html": {profiles: "sharing.html#people", admin: "admin.html", users: "admin.html#users", labmaps: "admin.html#labmaps"},
  "sharing.html": {about: "settings.html?#about", targets: "settings.html?#targets"},
};
function sgMoved() {
  const to = (SG_MOVED[location.pathname.split("/").pop()] || {})[decodeURIComponent(location.hash.slice(1))];
  if (to) location.replace(to.replace("?", location.search));
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
  if (page in SG_MOVED) { sgMoved(); addEventListener("hashchange", sgMoved); return; }
  location.replace(page === "connect.html" && h === "admin" ? "admin.html#admin"
    : page === "import.html" ? `${sources}#${h || "upload"}` : `${sources}#portals`);
})();
