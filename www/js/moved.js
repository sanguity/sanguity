/* Sanguity · the old addresses of what the Sources page holds now (#166): import.html (Upload files; #manual, #link,
   #payment, #files, #import-<id> as they were), connect.html (Portals and health plans; #admin is on the Admin page,
   #167), providers.html (finding your provider is in the connect step there). The query (?profile=, ?q=, ?id=,
   ?connected=, ?problem=) comes along. settings.html#watch is sent on by settings.js. */
"use strict";
(() => {
  const page = location.pathname.split("/").pop(), h = location.hash.slice(1), sources = "sources.html" + location.search;
  location.replace(page === "connect.html" && h === "admin" ? "admin.html#admin"
    : page === "import.html" ? `${sources}#${h || "upload"}` : `${sources}#portals`);
})();
