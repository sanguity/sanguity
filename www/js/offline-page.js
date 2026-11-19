/* Sanguity · the pages with the top bar other than the records page, without a connection (#413). Loaded after pwa.js,
   before nav.js and palette.js, on Settings, Sharing, Sources, Account, Admin and Getting started. (The records page
   has offline.js instead, which answers it from the copy this device may keep; these pages hold nothing offline.)

   - A request to the API that can't reach the server says so ("You're offline. This needs the server."), never the
     browser's own "Failed to fetch", and sets OFFLINE.on as offline.js does: the command palette then lists only what
     needs no server (palette.js), and your menu's Dark mode keeps the theme for this browser until it can be saved (#414).
   - Opened offline (no answer from the server yet): what the page would show needs the server (empty, or forms that
     can't be sent), so the page's own part (main) is left out and the banner offline.js uses says why, with a link
     to the records kept on this device, if it keeps any. The menus' links to the other pages say why they aren't
     followed, as on the records page (#385). Once the connection is back, the page is loaded again.
   - Gone offline while open: the banner says changes need the server, and what's on the page stays; once the
     connection is back, or the server answers again, the banner goes. */
"use strict";
const OFFLINE = {on: false, copy: null};   // the same flag as offline.js's: on, the server can't be reached

(function offlinePage() {
  const realFetch = window.fetch.bind(window), API = new URL("api/", location.href).pathname;
  const NEEDS_SERVER = "You're offline. This needs the server.", WHY = "Needs the server: you're offline";
  const name = document.title.split(" · ")[0];   // "Settings", before the sidebar adds its group
  let reached = false;   // the server answered since the page opened
  const apiPath = input => {
    const u = new URL(input instanceof Request ? input.url : String(input), location.href);
    return u.origin === location.origin && u.pathname.startsWith(API) ? u.pathname.slice(API.length) : null;
  };
  window.fetch = async function (input, opts) {
    const path = apiPath(input);
    if (path == null) return realFetch(input, opts);
    let r;
    try { r = await realFetch(input, opts); }
    catch (err) {
      if (err.name === "AbortError") throw err;
      away();
      throw new Error(NEEDS_SERVER);
    }
    reached = true;
    if (OFFLINE.on && !document.body.classList.contains("offline-page")) back();
    if (r.ok && /^settings\/user\/[^/]+$/.test(path) && ((opts && opts.method) || "GET") !== "GET") keepUser(path, r);
    return r;
  };
  /* the person's own settings saved here (Display, your menu's theme): the copy of records this device keeps takes
     them, so the records page offline shows them as saved (#424; offline.js does the same there) */
  let kept = Promise.resolve();
  function keepUser(path, r) {
    if (!SgOffline.on()) return;
    const body = r.clone().json();
    kept = kept.then(async () => {
      const j = await body, c = await SgOffline.load();
      if (c && SgOffline.withUser(c, path.endsWith("/reset") ? j.settings : j)) await SgOffline.save(c);
    }).catch(e => console.warn("offline copy not kept:", e.message));
  }

  const records = () => SgOffline.on()
    ? el("a", {href: SgOffline.base.pathname, text: "Read the records kept on this device"})
    : "This device doesn't keep records to read offline.";
  function away() {
    if (OFFLINE.on) return;
    OFFLINE.on = true;
    const opened = !reached, main = document.querySelector("main");
    document.body.classList.add("offline");
    // opened offline, its h1 is the page's (main, with its own, is left out: #424)
    const b = el("div", {id: "offlineBanner", class: "card offline-banner", role: "status"}, opened ? el("h1", {text: name}) : null, el("strong", {text: "Offline. "}),
      opened ? `${name} needs the server, so it isn't shown without a connection; it opens once you're back online. `
        : "Changes here need the server; they can be made once you're back online. ", records());
    main.before(b);
    if (!opened) return;
    document.body.classList.add("offline-page");
    main.hidden = true;
    // the menus' links to the other pages: reached with the arrow keys, their tip and description the reason (#385);
    // the records (the brand, the tabs, the banner's link) still open, from the copy kept here
    const header = () => {
      for (const a of document.querySelectorAll("header a.acct, dialog.sheet a.acct")) { a.setAttribute("aria-disabled", "true"); a.tabIndex = -1; a.dataset.tip = WHY; a.removeAttribute("title"); }
      const sel = document.getElementById("profile"); if (sel) { sel.disabled = true; sel.title = WHY; }
    };
    header(); setTimeout(header);
    document.addEventListener("click", e => {
      const a = e.target.closest && e.target.closest("a[href]");
      if (!a || a.getAttribute("href").startsWith("#")) return;
      const u = new URL(a.href);
      if (u.origin === location.origin && [SgOffline.base.pathname, SgOffline.base.pathname + "index.html"].includes(u.pathname)) return;
      e.preventDefault(); e.stopPropagation();
      b.classList.remove("flash"); void b.offsetWidth; b.classList.add("flash");
    }, true);
  }
  function back() {
    OFFLINE.on = false;
    document.body.classList.remove("offline");
    document.getElementById("offlineBanner")?.remove();
  }
  // the connection back (the browser says so, and the server answers): the page opened offline is loaded again
  addEventListener("online", () => {
    if (OFFLINE.on) realFetch(API + "me", {cache: "no-store", credentials: "same-origin"}).then(() => {
      if (document.body.classList.contains("offline-page")) location.reload(); else back();
    }, () => {});
  });
})();
