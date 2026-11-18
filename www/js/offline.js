/* Sanguity · the records page without a connection (#56). Loaded by index.html after util.js and pwa.js, before
   app.js, so it sits between app.js and the API without app.js knowing:

   - online, with offline records turned on for this device (Account), it keeps a copy of what app.js loads for the
     profile it shows (its records, settings and watch data) through SgOffline (pwa.js), explicitly, in its own cache;
     the API's answers themselves stay no-store and nothing else from the API is kept. With the option off, or after
     a 401, any copy is deleted. A record's details come when it's opened (details.js, #217), so the copy keeps those
     of the records opened on this device, with the records they belong to (X-Records-Rev: while those stay the same,
     a new copy keeps the details opened before);
   - when the server can't be reached, app.js is answered from that copy (only that profile; anything else, and every
     change, is refused as needing the server), and the page says it's offline, read-only, and when the records were
     loaded. Without a copy it says records aren't available offline. */
"use strict";
const OFFLINE = {on: false, copy: null};   // on: showing the kept copy, without the server

(function offlineRecords() {
  const realFetch = window.fetch.bind(window);
  const API = new URL("api/", location.href).pathname;
  const NEEDS_SERVER = "You're offline. This needs the server.";
  const json = (body, status = 200) => new Response(typeof body === "string" ? body : JSON.stringify(body),
    {status, headers: {"Content-Type": "application/json", "Cache-Control": "no-store"}});
  const apiPath = input => {
    const u = new URL(input instanceof Request ? input.url : String(input), location.href);
    return u.origin === location.origin && u.pathname.startsWith(API) ? u.pathname.slice(API.length) : null;
  };
  const PART = /^profiles\/(\d+)\/(data|settings|watch)$/;
  const DETAILS = /^profiles\/(\d+)\/details$/;   // ?ids=1,2,3 (#217)
  const idsOf = url => (new URL(url, location.href).searchParams.get("ids") || "").split(",").filter(Boolean).map(Number);

  /* ---- online: keep what was loaded, if this device keeps records */
  // each answer is handled in the order it came (settings and watch data after the records they go with)
  let me = null, copy = null, queue = Promise.resolve();
  const write = () => copy && SgOffline.save(copy);
  function keep(path, r) {
    if (r.status === 401) { SgOffline.forget(); return; }
    if (!SgOffline.on()) { if (path === "me") SgOffline.drop(); return; }   // turned off (perhaps in another tab)
    if (path === "me") { me = r.ok ? r.clone().json().catch(() => null) : null; return; }
    const dm = path.match(DETAILS);
    if (dm && r.status === 200) {   // a record opened: its details join the copy, if it's of the same records (#217)
      const pid = +dm[1], rev = r.headers.get("X-Records-Rev"), ids = idsOf(r.url), body = r.clone().json();
      queue = queue.then(async () => {
        if (!copy || copy.profile.id !== pid || !rev || copy.rev !== rev) return;
        const got = (await body).details || {};
        for (const id of ids) copy.details[id] = got[id] || null;   // null: it has none
        await write();
      }).catch(e => console.warn("offline copy not kept:", e.message));
      return;
    }
    const m = path.match(PART);
    if (!m || !me) return;
    const pid = +m[1], part = m[2];
    const text = r.status === 200 ? r.clone().text() : null;   // now, before app.js reads the answer
    queue = queue.then(async () => {
      const who = await me;
      if (part === "data") {
        const profile = who && (who.profiles || []).find(p => p.id === pid);
        // no records (204), an error, or a sign-in with no end time: nothing from before is kept either
        if (!text || !profile || !who.session_ends) { copy = null; await SgOffline.drop(); return; }
        const rev = r.headers.get("X-Records-Rev") || "", before = copy || await SgOffline.load();
        const same = before && before.profile.id === pid && rev && before.rev === rev && before.details;   // #217
        copy = {v: 1, loaded: Date.now(), ends: who.session_ends, user: who.user.username,
          profile: {id: profile.id, name: profile.name, role: profile.role}, data: await text, settings: null, watch: null,
          rev, details: same ? before.details : {}};
        await write();
      } else if (text && copy && copy.profile.id === pid) { copy[part] = await text; await write(); }
    }).catch(e => console.warn("offline copy not kept:", e.message));
  }

  /* ---- offline: answer from the kept copy */
  function fromCopy(path, method, url) {
    const c = OFFLINE.copy;
    if (method !== "GET") return json({detail: NEEDS_SERVER}, 503);
    const dm = path.match(DETAILS);
    if (dm && +dm[1] === c.profile.id) {   // the records opened here before; missing: the others (#217)
      const kept = c.details || null, details = {}, missing = [];   // a copy from before #217 has every record's in its data
      if (kept) for (const id of idsOf(url)) {
        if (!(id in kept)) missing.push(id);
        else if (kept[id]) details[id] = kept[id];
      }
      return json({details, missing});
    }
    if (path === "me") return json({user: {id: 0, username: c.user, is_admin: false, must_change_pw: false, two_factor: false, needs_two_factor: false},
      profiles: [c.profile], two_factor_required: false, session_ends: c.ends, offline: true});
    const m = path.match(PART);
    if (m && +m[1] === c.profile.id) return c[m[2]] == null ? json({detail: "Not kept for offline viewing."}, 404) : json(c[m[2]]);
    return json({detail: NEEDS_SERVER}, 503);
  }

  window.fetch = async function (input, opts) {
    const path = apiPath(input);
    if (path == null) return realFetch(input, opts);
    const method = ((opts && opts.method) || (input instanceof Request ? input.method : "GET")).toUpperCase();
    if (OFFLINE.on) return fromCopy(path, method, input instanceof Request ? input.url : String(input));
    let r;
    try { r = await realFetch(input, opts); }
    catch (err) {
      if (path === "me" && method === "GET") {   // the server can't be reached at all
        const c = SgOffline.on() ? await SgOffline.load() : null;
        if (c) { OFFLINE.on = true; OFFLINE.copy = c; offlineView(); return fromCopy(path, method); }
        notAvailable();
        throw new Error("you're offline.");
      }
      throw err;
    }
    if (method === "GET") keep(path, r);
    return r;
  };

  /* ---- what the page shows offline */
  function banner(...kids) {
    const b = document.getElementById("offlineBanner") || el("div", {id: "offlineBanner", class: "card offline-banner", role: "status"});
    b.replaceChildren(...kids);
    if (!b.isConnected) document.querySelector("main").before(b);
    return b;
  }
  function notAvailable() {
    document.body.classList.add("offline");
    banner(el("strong", {text: "Offline. Records aren't available offline on this device. "}),
      "Sanguity needs its server to show them. To read the last records you open here without a connection, turn on ",
      el("b", {text: "Offline records on this device"}), " under Account when you're back online.");
  }
  function offlineView() {
    const c = OFFLINE.copy;
    document.body.classList.add("offline");
    banner(el("strong", {text: "Offline · read-only. "}),
      `${c.profile.name}, as loaded on ${new Date(c.loaded).toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"})}. `,
      el("span", {class: "why", text: "Changes, documents, imports, settings and your account need the server; they come back when you're online."}));
    const why = "Needs the server: you're offline";
    const header = () => {
      for (const a of document.querySelectorAll("header a.acct")) { a.setAttribute("aria-disabled", "true"); a.tabIndex = -1; a.title = why; }
      const sel = document.getElementById("profile"); if (sel) { sel.disabled = true; sel.title = why; }
    };
    header(); setTimeout(header);   // and again once app.js has set up the header
    // links to the other pages (they'd open without their data): not followed, the reason shown instead
    document.addEventListener("click", e => {
      const a = e.target.closest && e.target.closest("a[href]");
      if (!a || a.getAttribute("href").startsWith("#")) return;
      e.preventDefault(); e.stopPropagation();
      const b = document.getElementById("offlineBanner"); b.classList.remove("flash"); void b.offsetWidth; b.classList.add("flash");
    }, true);
    // the detail panel: documents aren't kept, and corrections need the server
    const body = document.getElementById("dbody");
    const tidy = () => {
      for (const f of body.querySelectorAll("iframe[src], img[src]")) {
        if (/^(data|blob):/.test(f.getAttribute("src"))) continue;
        f.replaceWith(el("p", {class: "hint offline-note", text: "Documents aren't kept on this device. Open this one when you're back online."}));
      }
      for (const a of body.querySelectorAll("a[href]:not([aria-disabled])")) if (!a.getAttribute("href").startsWith("#")) { a.setAttribute("aria-disabled", "true"); a.title = why; }
      for (const box of body.querySelectorAll(".edits:not([data-offline])")) {
        box.dataset.offline = "1";
        for (const c of box.querySelectorAll("button, input, select, textarea")) { c.disabled = true; c.title = why; }
        box.prepend(el("p", {class: "hint offline-note", text: "Corrections need the server: you're offline."}));
      }
      for (const c of body.querySelectorAll(".edits[data-offline] :is(button, input, select, textarea):not(:disabled)")) { c.disabled = true; c.title = why; }
    };
    if (body) new MutationObserver(tidy).observe(body, {childList: true, subtree: true});
  }
})();
