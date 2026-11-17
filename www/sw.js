/* Sanguity · service worker (#56): keeps the app's own pages, styles, scripts and icons, so it opens from the home
   screen without a connection.

   It never handles /api/ or anything else outside the list below: those requests go to the network exactly as the
   page made them (every API answer is Cache-Control: no-store), and nothing the API sends is kept here. The one copy
   of records a device may keep for offline viewing is written by the page itself (js/offline.js), only after the
   person turns it on under Account, in its own cache ("sanguity-offline"), which this worker never reads or writes.

   Pages, styles and scripts hold no health data (each page asks the API who is signed in). They're fetched from the
   network first, so an update shows at once, and the kept copy is used only when the network can't be reached. */
"use strict";
const SHELL = "sanguity-shell-v1";
const SCOPE = new URL(self.registration.scope);
// Every page, style, script and icon in www/ (tests/test_pwa.py checks this list against the folder). Not
// connect-return.html: that's where a connected source's sign-in comes back, with a code in the address.
const FILES = [
  "", "index.html", "login.html", "setup.html", "account.html", "import.html", "settings.html", "providers.html", "connect.html",
  "summary.html", "share.html",
  "manifest.webmanifest", "css/app.css", "css/summary.css",
  "icons/icon.svg", "icons/icon-192.png", "icons/icon-512.png", "icons/maskable-512.png", "icons/apple-touch-icon.png",
  "js/account.js", "js/app.js", "js/charts.js", "js/connect.js", "js/core.js", "js/costs.js", "js/dates.js", "js/drawer.js", "js/health.js",
  "js/heatmap.js", "js/import-history.js", "js/import-jobs.js", "js/import-manual.js", "js/import-records.js", "js/import-shl.js",
  "js/import.js", "js/journal.js", "js/login.js", "js/meds.js", "js/offline.js", "js/outside.js", "js/overview.js", "js/providers.js",
  "js/pwa.js", "js/ranges.js", "js/rename.js", "js/same.js", "js/settings-calendar.js", "js/settings.js", "js/setup.js",
  "js/sources.js", "js/summary.js", "js/theme.js", "js/timeline.js", "js/trends.js", "js/trends-views.js", "js/units.js",
  "js/util.js", "js/share.js", "js/share-links.js",
];

/* the part of a same-origin address after the app's own folder, if it's one of the app's own files: "" for the
   start page, "js/app.js"... Pages and files added later (a new js/ file) are kept too once fetched. */
function shellPath(url) {
  if (url.origin !== SCOPE.origin || !url.pathname.startsWith(SCOPE.pathname)) return null;
  const p = url.pathname.slice(SCOPE.pathname.length);
  if (FILES.includes(p)) return p;
  if (/^[a-z-]+\.html$/.test(p) && p !== "connect-return.html") return p;
  if (/^(css|js|icons)\/[\w.-]+$/.test(p)) return p;
  return null;
}

self.addEventListener("install", e => {
  // each file on its own: one missing file mustn't stop the rest from being kept
  e.waitUntil(caches.open(SHELL).then(c => Promise.all(FILES.map(f =>
    fetch(new Request(new URL(f, SCOPE), {cache: "no-store", credentials: "same-origin"}))
      .then(r => r.ok && c.put(new URL(f, SCOPE), r)).catch(() => {}))))
    .then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k.startsWith("sanguity-shell-") && k !== SHELL).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const path = shellPath(new URL(req.url));
  if (path == null) return;   // the API, documents, connect/return: the network only, untouched
  e.respondWith(fromNetworkThenKept(req, path));
});

async function fromNetworkThenKept(req, path) {
  const key = new URL(path, SCOPE).href;   // without the query (?profile=...): the file is the same
  try {
    const r = await fetch(req);
    if (r.ok && r.type === "basic" && !r.redirected) {
      const copy = r.clone();
      caches.open(SHELL).then(c => c.put(key, copy)).catch(() => {});
    }
    return r;
  } catch (err) {
    const kept = await caches.match(key, {cacheName: SHELL});
    if (kept) return kept;
    return new Response("Sanguity can't reach its server, and this page isn't kept on this device yet.",
      {status: 503, headers: {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store"}});
  }
}
