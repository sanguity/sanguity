/* Sanguity · the records page without a connection (#56). Loaded by index.html after util.js and pwa.js, before
   app.js, so it sits between app.js and the API without app.js knowing:

   - online, with offline records turned on for this device (Account), it keeps a copy of what app.js loads for the
     profile it shows (its records, settings and watch data) through SgOffline (pwa.js), explicitly, in its own cache;
     the API's answers themselves stay no-store and nothing else from the API is kept. With the option off, or after
     a 401, any copy is deleted. A record's details come when it's opened (details.js, #217), so the copy keeps those
     of the records opened on this device, with the records they belong to (X-Records-Rev: while those stay the same,
     a new copy keeps the details opened before). A change made on the page (#271) goes into the copy too, from the
     same reply the page puts in place, with the records' revision after it (#405), so the copy stays as the page is
     and keeps the details of what's opened after it;
   - when the server can't be reached, app.js is answered from that copy (only that profile, and the person's own
     settings as its settings have them: the theme, the date format, units, #428; anything else, and every
     change, is refused as needing the server), and the page says it's offline, read-only, and when the records were
     loaded. Without a copy it says records aren't available offline.
   - a copy whose profile the server no longer lists for the person (removed from it, left it, or it was deleted:
     #591) is deleted as soon as the page hears so (SgOffline.still), here and on the other pages (offline-page.js). */
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
    if (path === "me") {
      me = r.ok ? r.clone().json().catch(() => null) : null;
      if (!r.ok) return;
      const who = me;
      queue = queue.then(async () => {   // no longer theirs (#591): the copy goes, whichever profile this page shows
        const w = await who;
        if (!w) return;
        if (copy && !(w.profiles || []).some(p => p.id === copy.profile.id)) copy = null;
        await SgOffline.still(w.profiles);
      }).catch(e => console.warn("offline copy not checked:", e.message));
      return;
    }
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

  /* a change's reply, for the copy's profile, with the records' revision after it: the records whole (an edit, a link
     between results; the details opened before went with the records they belonged to), the rows of the list it
     changed (store.js putRows), the out-of-pocket part (a Costs mark) or a source's own name (rename.js, #416);
     rev: what the copy's records are now */
  const CHANGE = /^profiles\/(\d+)\/(edits|same|labmaps|journal|medications\/doses|medications\/reminders|costs\/(?:mark|plans|bills)|imports\/(\d+)\/name)(\/|$)/;
  const LIST = {journal: "journal", "medications/doses": "doses", "medications/reminders": "reminders"};
  function keepChange(path, r) {
    const cm = path.match(CHANGE), rev = r.headers.get("X-Records-Rev");
    if (!cm || !r.ok || !rev || !SgOffline.on()) return;
    const pid = +cm[1], body = r.clone().json(), list = LIST[cm[2]];
    queue = queue.then(async () => {
      if (!copy || copy.profile.id !== pid) return;
      const j = await body;
      if ("records" in j) {
        if (!j.records) { copy = null; await SgOffline.drop(); return; }   // none left
        copy.data = JSON.stringify(j.records); copy.details = {};
      } else if (cm[3]) {   // the source's name: the details opened before stay, as its records do
        const data = JSON.parse(copy.data), src = (data.imports || {})[cm[3]];
        if (src) { if (j.name) src.name = j.name; else delete src.name; }
        copy.data = JSON.stringify(data);
      } else {
        const data = JSON.parse(copy.data);
        if (list) data[list] = putRows(data[list], list, j); else data.oop = j.oop || undefined;
        copy.data = JSON.stringify(data);
      }
      copy.rev = rev;
      await write();
    }).catch(e => console.warn("offline copy not kept:", e.message));
  }

  /* settings saved on the page (a lab's or a source tag's name, rename.js; a saved Trends view; the journal's groups):
     the reply is the settings whole, as GET .../settings answers them (a reset's are its "settings"), so they're the
     copy's settings now (#416) */
  const SETTINGS = /^profiles\/(\d+)\/settings(\/|$)/;
  function keepSettings(path, r) {
    const sm = path.match(SETTINGS);
    if (!sm || !r.ok || !SgOffline.on()) return;
    const pid = +sm[1], body = r.clone().json();
    queue = queue.then(async () => {
      if (!copy || copy.profile.id !== pid) return;
      const j = await body, now = path.endsWith("/reset") ? j.settings : j;
      if (!now || typeof now !== "object") return;
      copy.settings = JSON.stringify(now);
      await write();
    }).catch(e => console.warn("offline copy not kept:", e.message));
  }

  /* the person's own settings saved here (your menu's theme; the reply is all of them, flat, a reset's its
     "settings"): the copy's settings take them, as the next load would (#424; offline-page.js on the other pages) */
  const USER = /^settings\/user\/[^/]+$/;
  function keepUser(path, r) {
    if (!USER.test(path) || !r.ok || !SgOffline.on()) return;
    const body = r.clone().json();
    queue = queue.then(async () => {
      const j = await body;
      if (copy && SgOffline.withUser(copy, path.endsWith("/reset") ? j.settings : j)) await write();
    }).catch(e => console.warn("offline copy not kept:", e.message));
  }

  /* ---- offline: answer from the kept copy */
  // the person's own settings (theme, time zone, date format, units: GET settings/user), as the copy's settings have
  // them (#428: dates.js reads the date format there)
  const mine = c => c.settings ? json(SgOffline.mine(JSON.parse(c.settings))) : json({detail: "Not kept for offline viewing."}, 404);
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
    if (path === "settings/user") return mine(c);
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
      if (path === "settings/user" && method === "GET") {   // (dates.js asks before "me": the date format, #428)
        const c = SgOffline.on() ? await SgOffline.load() : null;
        if (c) return mine(c);
      }
      throw err;
    }
    if (method === "GET") keep(path, r); else { keepChange(path, r); keepSettings(path, r); keepUser(path, r); }
    return r;
  };

  /* the connection back (the server answers: asked when the browser says it's online, and every few seconds while the
     page is shown, as that isn't always said, nor is the server always back with it): what's sent goes to the server
     again, and the page is loaded again, live, as the other pages are (offline-page.js); a theme picked offline is
     saved then (nav.js saveLater, #424). An entry being written (a form in the drawer, a journal entry offline, say)
     or another dialog open is left to finish first: its Save reaches the server now, and the page is loaded again once
     it's done, the banner saying so meanwhile (#430); a record only shown there isn't waited for */
  let asking = false;
  function back() {
    if (asking || !document.body.classList.contains("offline")) return;
    asking = true;
    realFetch(API + "me", background({cache: "no-store", credentials: "same-origin"})).then(() => {   // (#1063: not use)
      OFFLINE.on = false;
      let said = false;
      (function live() {
        if (!document.querySelector("#drawer[open] form, dialog[open]:not(#drawer)")) { location.reload(); return; }
        if (!said) {
          said = true;
          banner(...[document.querySelector("#offlineBanner > h1"), el("strong", {text: "Back online. "}),
            "What you save now goes to the server; the page loads the latest records once you've closed what's open."].filter(Boolean));
        }
        setTimeout(live, 1000);
      })();
    }, () => { asking = false; });
  }
  addEventListener("online", back);
  let watching = 0;
  const watchBack = () => { watching ||= setInterval(() => { if (!document.hidden) back(); }, 4000); };

  /* ---- what the page shows offline */
  function banner(...kids) {
    const b = document.getElementById("offlineBanner") || el("div", {id: "offlineBanner", class: "card offline-banner", role: "status"});
    b.replaceChildren(...kids);
    if (!b.isConnected) document.querySelector("main").before(b);
    return b;
  }
  function notAvailable() {
    document.body.classList.add("offline");
    watchBack();
    // the page's h1 (the tab's goes with the records it names, app.js emptyCard: #424)
    banner(el("h1", {text: "Records"}), el("strong", {text: "Offline. Records aren't available offline on this device. "}),
      "Sanguity needs its server to show them. To read the last records you open here without a connection, turn on ",
      el("b", {text: "Offline records on this device"}), " under Settings, This device, when you're back online.");
  }
  function offlineView() {
    const c = OFFLINE.copy;
    document.body.classList.add("offline");
    watchBack();
    const loadedOn = () => `${c.profile.name}, as loaded on ${fmtDT(c.loaded)}. `, when = document.createTextNode(loadedOn());
    banner(el("strong", {text: "Offline · read-only. "}),
      when, el("span", {class: "why", text: "Changes, documents, imports, settings and your account need the server; they come back when you're online."}));
    // in their date format, the copy's (dates.js, loaded by the time a request fails, reads it from settings/user: #428)
    datesReady().then(() => { const t = loadedOn(); if (when.data !== t) when.data = t; });
    const why = "Needs the server: you're offline";
    const header = () => {
      // the menus' links to the other pages: still reached with the arrow keys, so the reason is their tip and
      // description (#261), shown on focus too, not a title only a pointer finds (#385); on a phone they're in sheets (#269)
      for (const a of document.querySelectorAll("header a.acct, dialog.sheet a.acct")) { a.setAttribute("aria-disabled", "true"); a.tabIndex = -1; a.dataset.tip = why; a.removeAttribute("title"); }
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
      // a link not followed: its tip (#261) is why, in place of what it would do, and its name no longer says it
      // opens a new tab (#378); no title, which would be a second tooltip
      for (const a of body.querySelectorAll("a[href]:not([aria-disabled])")) if (!a.getAttribute("href").startsWith("#")) {
        a.setAttribute("aria-disabled", "true"); a.dataset.tip = why;
        const name = a.getAttribute("aria-label");
        if (name) a.setAttribute("aria-label", name.replace(/,? opens in a new tab$/, ""));
      }
      for (const box of body.querySelectorAll(".edits:not([data-offline])")) {
        box.dataset.offline = "1";
        for (const c of box.querySelectorAll("button, input, select, textarea")) { c.disabled = true; c.title = why; }
        box.prepend(el("p", {class: "hint offline-note", text: "Corrections need the server: you're offline."}));
      }
      for (const c of body.querySelectorAll(".edits[data-offline] :is(button, input, select, textarea):not(:disabled)")) { c.disabled = true; c.title = why; }
      serverOnly(body);
    };
    if (body) new MutationObserver(tidy).observe(body, {childList: true, subtree: true});
    /* what saves, wherever it's drawn (#449): a control with class needs-server, or each in a box with it (a
       medication's Log a dose and Add a reminder, a dose's or a reminder's Edit and Remove, the drawer's forms'
       Save, Costs' out-of-pocket editor). A button stays in reach, aria-disabled, its tip the reason, as today's
       doses' are (#439); a field is disabled, its title the reason (a disabled control gets no focus for a tip);
       neither a press nor a form's submit does anything. The drawer and Costs' editor say why in a line too, as
       today's doses do (#456): the drawer at its top, unless its corrections' line says it; the editor before its form */
    const serverOnly = root => {
      for (const box of root.querySelectorAll(".needs-server:not([data-offline])")) {
        box.dataset.offline = "1";
        const part = box.closest("#dbody, .oop-edit");
        if (part && !part.querySelector(".offline-saves, .edits[data-offline]")) {
          const p = el("p", {class: "hint offline-note offline-saves", text: "Changes need the server: you're offline."});
          if (part === body) part.prepend(p); else box.before(p);
        }
        for (const c of box.matches("button, input, select, textarea") ? [box] : box.querySelectorAll("button, input, select, textarea"))
          if (c.matches("button")) { c.setAttribute("aria-disabled", "true"); c.dataset.tip = why; } else { c.disabled = true; c.title = why; }
      }
    };
    const off = e => { e.preventDefault(); e.stopImmediatePropagation(); };
    document.addEventListener("click", e => { if (e.target.closest && e.target.closest(".needs-server[aria-disabled=true], .needs-server [aria-disabled=true]")) off(e); }, true);
    document.addEventListener("submit", e => { if (e.target.querySelector(".needs-server[aria-disabled=true]")) off(e); }, true);
    const main = document.querySelector("main");
    if (main) new MutationObserver(() => serverOnly(main)).observe(main, {childList: true, subtree: true});
  }
})();
