/* Sanguity · pages that moved: their old addresses send you on to where they went, keeping the address's ?query
   (the profile, ?profile=2). Two ways:
   - <body data-moved="sharing.html#summary">: straight there (#168: summary.html).
   - the Sources page's old pages (#166): import.html (Upload files; #manual, #link, #payment, #files,
     #import-<id> as they were), connect.html (Portals and health plans; #admin is on the Admin page, #167),
     providers.html (finding your provider is in the connect step there). settings.html#watch is sent on by
     settings.js.
   Each page also says where it went, for a browser that doesn't run this. */
"use strict";
(() => {
  const to = document.body.dataset.moved;
  if (to) {
    const u = new URL(to, location.href);
    if (!u.search) u.search = location.search;
    location.replace(u.href);
    return;
  }
  const page = location.pathname.split("/").pop(), h = location.hash.slice(1), sources = "sources.html" + location.search;
  location.replace(page === "connect.html" && h === "admin" ? "admin.html#admin"
    : page === "import.html" ? `${sources}#${h || "upload"}` : `${sources}#portals`);
})();
