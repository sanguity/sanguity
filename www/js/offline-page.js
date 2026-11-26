/* Sanguity · the pages with the top bar other than the records page, without a connection (#413). Loaded after pwa.js,
   before nav.js and palette.js, on Settings (with its You and Install, Account and Admin before #991), Sharing, Sources
   and Getting started. (The records page has offline.js instead, which answers it from the copy this device may keep; these pages hold nothing offline.)

   - A request to the API that can't reach the server says so ("You're offline. This needs the server."), never the
     browser's own "Failed to fetch", and sets OFFLINE.on as offline.js does: the command palette then lists only what
     needs no server (palette.js), and your menu's Dark mode keeps the theme for this browser until it can be saved (#414).
   - Opened offline (no answer from the server yet): what the page would show needs the server (empty, or forms that
     can't be sent), so the page's own part (main) is left out and the banner offline.js uses says why, with a link
     to the records kept on this device, if it keeps any. The menus' links to the other pages say why they aren't
     followed, as on the records page (#385). Once the connection is back, the page is loaded again.
   - Gone offline while open: the banner says changes need the server, and what's on the page stays; once the
     connection is back, or the server answers again, the banner goes.
   - The copy kept for the records page goes once its profile isn't the person's any more (#591): when the server's
     "me" no longer lists it (removed from it by an owner), and on Leave or Delete here (Sharing, Settings). */
"use strict";
const OFFLINE = {on: false, copy: null};   // the same flag as offline.js's: on, the server can't be reached

(function offlinePage() {
  const realFetch = window.fetch.bind(window), API = new URL("api/", location.href).pathname;
  const NEEDS_SERVER = "You're offline. This needs the server.", WHY = "Needs the server: you're offline";
  const name = document.title.split(" · ")[0];   // "Settings", before the sidebar adds its group
  let reached = false;   // the server answered since the page opened
  // being left: a request torn down with the page fails, which says nothing of the server (#457)
  let leaving = false;
  addEventListener("pagehide", () => { leaving = true; });
  addEventListener("pageshow", e => { if (e.persisted) leaving = false; });
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
      // the page left, not the server (#457); a task later, as the request torn down fails before pagehide is heard
      await new Promise(r => setTimeout(r));
      if (leaving) throw err;
      away();
      throw new Error(NEEDS_SERVER);
    }
    reached = true;
    if (OFFLINE.on && !document.body.classList.contains("offline-page")) back();
    const method = ((opts && opts.method) || (input instanceof Request ? input.method : "GET")).toUpperCase();
    if (r.ok && SETTINGS.test(path) && method !== "GET") keepSettings(path, r);
    if (r.ok) stillTheirs(path, method, r);
    return r;
  };
  /* settings saved here (Display, your menu's theme, the other sections): the copy of records this device keeps takes
     them, so the records page offline shows them as saved (#424; offline.js does the same there). The reply is all of
     them (a reset's are its "settings"): the person's own, flat (settings/user/…), or a profile's, nested
     (profiles/<id>/settings/…, as Settings saves them with a profile picked, #428): the copy's own profile's are its
     settings now; another profile's give the person's own, which are the same on every profile */
  const SETTINGS = /^(settings\/user|profiles\/(\d+)\/settings)\/[^/]+(\/name)?$/;
  let kept = Promise.resolve();
  function keepSettings(path, r) {
    if (!SgOffline.on()) return;
    const body = r.clone().json(), pid = path.match(SETTINGS)[2];
    kept = kept.then(async () => {
      const j = await body, c = await SgOffline.load(), now = path.endsWith("/reset") ? j.settings : j;
      if (!c || !now || typeof now !== "object") return;
      if (pid && +pid === c.profile.id) c.settings = JSON.stringify(now);
      else if (!SgOffline.withUser(c, pid ? SgOffline.mine(now) : now)) return;
      await SgOffline.save(c);
    }).catch(e => console.warn("offline copy not kept:", e.message));
  }

  /* (#591) the copy's profile no longer the person's: "me" not listing it, or a Leave (members/<your id>) or Delete
     of it here. A Delete of it takes the copy at once; after a member's removal, which may be yours, "me" is asked,
     and the copy goes unless it still lists the profile, or if it can't be asked */
  const GONE = /^profiles\/(\d+)(\/members\/\d+)?$/;
  function stillTheirs(path, method, r) {
    if (!SgOffline.on()) return;
    const gm = method === "DELETE" && path.match(GONE);
    if (path === "me" && method === "GET") {
      const body = r.clone().json();
      kept = kept.then(async () => { await SgOffline.still((await body).profiles); }).catch(e => console.warn("offline copy not checked:", e.message));
    } else if (gm) {
      kept = kept.then(async () => {
        const c = await SgOffline.load();
        if (!c || c.profile.id !== +gm[1]) return;
        if (!gm[2]) { await SgOffline.drop(); return; }
        const now = await realFetch(API + "me", {cache: "no-store", credentials: "same-origin"}).then(x => x.ok ? x.json() : null, () => null);
        await SgOffline.still(now && now.profiles);
      }).catch(e => console.warn("offline copy not checked:", e.message));
    }
  }

  const records = () => SgOffline.on()
    ? el("a", {href: SgOffline.base.pathname, text: "Read the records kept on this device"})
    : "This device doesn't keep records to read offline.";
  function away() {
    if (OFFLINE.on) return;
    OFFLINE.on = true;
    watchBack();
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
  /* the connection back (the server answers: asked when the browser says it's online, and every few seconds while the
     page is shown offline, as the records page does, #428: that isn't always said, nor is the server always back with
     it): the page opened offline is loaded again */
  let asking = false, watching = 0;
  function again() {
    if (!OFFLINE.on || asking) return;
    asking = true;
    realFetch(API + "me", {cache: "no-store", credentials: "same-origin"}).then(() => {
      if (document.body.classList.contains("offline-page")) location.reload(); else back();
    }, () => {}).finally(() => { asking = false; });
  }
  addEventListener("online", again);
  function watchBack() { watching ||= setInterval(() => { if (!document.hidden) again(); }, 4000); }
})();
