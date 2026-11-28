/* Sanguity · install as a phone app (#56), on every page (after util.js): registers the service worker (sw.js),
   keeps this device's choice about offline records, and deletes the offline copy whenever the sign-in ends.

   Offline records are off unless the person turns them on, per device, under Settings → This device. When on, the
   records page (js/offline.js) keeps one copy of the last profile it loaded: its records (each record's details once it was opened
   on this device, #217), settings and watch data, never documents (PDFs, pictures) and never another profile's. That copy is outside the server's encryption at rest, so it's
   deleted on sign-out, when the sign-in page is reached (a sign-in that ended), when the sign-in's own end time passes
   (even with no connection), when the option is turned off, and once the person no longer has the profile (#591:
   removed from it, left it or deleted it; still(), whichever page hears it from the server). The lab names picked on Labs go on sign-out and on
   reaching the sign-in page too (#220).

   Why it isn't encrypted on the device: to open offline, the page needs the key without the server, so the key would
   have to be kept on the phone right beside the copy, and anyone who can open the app on the unlocked phone could
   read it either way. What a key from the server would add (a signed-out device holding nothing readable) comes from
   deleting the copy instead. So it's kept plainly, and Settings → This device says who could see it. */
"use strict";
const SgOffline = (() => {
  const CACHE = "sanguity-offline", FLAG = "sg-offline-records";
  const base = new URL("../", document.currentScript.src);   // the app's own folder (this file is in js/)
  const KEY = new URL("offline-copy", base).href;             // the one entry's name in that cache, not an address
  // service workers and the cache need a secure context: HTTPS, or localhost
  const works = () => "serviceWorker" in navigator && "caches" in window && window.isSecureContext;
  const on = () => { try { return works() && localStorage.getItem(FLAG) === "1"; } catch (e) { return false; } };
  const drop = () => "caches" in window ? caches.delete(CACHE).catch(() => false) : Promise.resolve(false);
  // the choice goes with the copy: a phone handed to someone else doesn't keep theirs without asking
  function forget() { try { localStorage.removeItem(FLAG); } catch (e) {} return drop(); }
  function set(want) { try { want ? localStorage.setItem(FLAG, "1") : localStorage.removeItem(FLAG); } catch (e) {} return want ? Promise.resolve() : drop(); }
  async function save(copy) {
    if (!on()) return;
    const c = await caches.open(CACHE);
    await c.put(KEY, new Response(JSON.stringify(copy), {headers: {"Content-Type": "application/json"}}));
  }
  /* the kept copy, or null; one past its sign-in's end, or unreadable, is deleted */
  async function load() {
    if (!("caches" in window)) return null;
    try {
      const r = await caches.match(KEY, {cacheName: CACHE});
      const c = r ? await r.json() : null;
      if (c && c.v === 1 && c.ends > Date.now() / 1000 && c.profile && typeof c.data === "string") return c;
    } catch (e) {}
    await drop();
    return null;
  }
  /* the copy, while its profile is among the person's (profiles: /api/me's, as the server says now); one whose
     profile isn't is deleted (#591: removed from it, left, or deleted) */
  async function still(profiles) {
    const c = await load();
    if (c && !(Array.isArray(profiles) && profiles.some(p => p && p.id === c.profile.id))) { await drop(); return null; }
    return c;
  }
  /* the person's own settings (theme, dates, units: settings/user, the flat {key: value} its saves answer with), into
     a copy's settings, nested as GET .../settings has them (#424); a copy without settings is left as it is */
  function withUser(copy, flat) {
    if (!copy.settings || !flat || typeof flat !== "object") return false;
    const s = JSON.parse(copy.settings);
    for (const [k, v] of Object.entries(flat)) {
      const path = k.split("."), last = path.pop();
      let d = s;
      for (const p of path) d = d[p] = d[p] && typeof d[p] === "object" ? d[p] : {};
      d[last] = v;
    }
    copy.settings = JSON.stringify(s);
    return true;
  }
  /* and back: the person's own of nested settings (a copy's, or a profile's as a save answers them), flat, as GET
     settings/user has them (#428). They're the settings of scope "user" in server/settings.py, the same on every profile */
  const MINE = ["theme", "inRange", "guide", "dates.timeZone", "dates.format", "units"];
  function mine(s) {
    const flat = {};
    for (const k of MINE) {
      const v = k.split(".").reduce((d, p) => d && typeof d === "object" ? d[p] : undefined, s);
      if (v !== undefined) flat[k] = v;
    }
    return flat;
  }
  return {base, works, on, set, save, load, drop, forget, still, withUser, mine};
})();

if (SgOffline.works()) navigator.serviceWorker.register(new URL("sw.js", SgOffline.base), {scope: SgOffline.base.pathname})
  .catch(e => console.warn("offline use unavailable:", e.message));

/* (#220) the lab series picked on Labs, by name (sel in each profile's view, ht-state:<id>, core.js), go at sign-out
   too, and the lab name typed in Labs' Latest filter (q in ht-latest:<id>, latest.js, #994); the rest of each (period,
   tab, kinds; Flagged only and what Latest has shown, each result by its place, #1070) is kept */
function forgetPicks() {
  try {
    const keys = [];
    for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); if (k === "ht-state" || k.startsWith("ht-state:") || k.startsWith("ht-latest:")) keys.push(k); }
    for (const k of keys) {
      let s = null;
      try { s = JSON.parse(localStorage.getItem(k)); } catch (e) {}
      const name = k.startsWith("ht-latest:") ? "q" : "sel";
      if (s && typeof s === "object" && name in s) delete s[name];
      if (s && Array.isArray(s.shown)) s.shown = s.shown.filter(x => !String(x).includes("|"));   // a result as written, kept before #1070
      if (s && typeof s === "object") localStorage.setItem(k, JSON.stringify(s));
    }
  } catch (e) {}
}
/* (#936) what this browser keeps of a profile (its view on the records page, ht-state:<id>, with the lab names picked;
   Body and activity's options, ht-health:<id> and ht-glucose:<id>; Labs' Latest's filters, ht-latest:<id>, #994; the
   last one picked, ht-profile) goes with it: on its Delete here (profile-card.js), and as any page hears from the
   server which profiles are the person's (nav.js), for those it doesn't list (deleted elsewhere, or left). gone(id) ->
   whether that profile's go */
function forgetProfiles(gone) {
  try {
    const keys = [];
    for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i), m = /^ht-(?:state|health|glucose|latest):(\d+)$/.exec(k || ""); if (m && gone(+m[1])) keys.push(k); }
    if (/^\d+$/.test(localStorage.getItem("ht-profile") || "") && gone(+localStorage.getItem("ht-profile"))) keys.push("ht-profile");
    for (const k of keys) localStorage.removeItem(k);
  } catch (e) {}
}
/* (#429) what the sign-in left waiting on the device, for the account or for the next page, goes too, so the next
   person to sign in here doesn't get it: a theme picked offline, to be saved for the account once online (nav.js,
   sg-theme-later), with this browser's stand-in for it meanwhile (ht-theme, while it's the same), and what the
   command palette left the records page to do (palette.js, sg-palette in this tab); and the account's theme as this
   browser keeps it for the first frame (ht-theme-default, util.js applyTheme), as the next account's isn't it (#433),
   with the account's In-range results (ht-inrange, #1108) */
function forgetWaiting() {
  try {
    const t = localStorage.getItem("sg-theme-later");
    if (t) { localStorage.removeItem("sg-theme-later"); if (localStorage.getItem("ht-theme") === t) localStorage.removeItem("ht-theme"); }
    localStorage.removeItem("ht-theme-default"); localStorage.removeItem("ht-inrange");
  } catch (e) {}
  try { sessionStorage.removeItem("sg-palette"); } catch (e) {}
}
// signing out (any page's Log out button) or reaching the sign-in or setup page: whatever the device kept is deleted
document.addEventListener("sg:logout", () => { SgOffline.forget(); forgetPicks(); forgetWaiting(); });   // util.js's logOut, once it's going ahead (#391)
if (/\/(login|setup)\.html$/.test(location.pathname)) { SgOffline.forget(); forgetPicks(); forgetWaiting(); }

/* Log out without the server (#422: offline, or it didn't answer): what the device kept goes at once (sg:logout),
   and the sign-in on the server is ended once it's reached, never claimed before. Until then every page here goes
   to the sign-in page, which ends it as soon as the server answers (login.js) and says which has happened. */
const SgLogout = (() => {
  const KEY = "sg-logout-later";
  const due = () => { try { return localStorage.getItem(KEY) === "1"; } catch (e) { return false; } };
  const later = () => { try { localStorage.setItem(KEY, "1"); return true; } catch (e) { return false; } };
  async function finish() {   // true: the server's sign-in is ended (or there was none left)
    try { if (!(await api("logout", {method: "POST"})).ok) return false; } catch (e) { return false; }
    try { localStorage.removeItem(KEY); } catch (e) {}
    return true;
  }
  /* (#432) Log out, online or not, asks the other open tabs first (util.js logOut): elsewhere() resolves to the names
     of the pages whose own "Leave site?" guards hold something not saved, as they answer within ms (600: a tab busy
     drawing, or a phone's in the background, still answers, #436); once it's going
     ahead, tell() sends them all to the sign-in page (signedOutElsewhere below) */
  const ch = "BroadcastChannel" in window ? new BroadcastChannel("sg-logout") : null;
  function elsewhere(ms = 600) {
    if (!ch) return Promise.resolve([]);
    const id = Math.random().toString(36).slice(2), names = [];
    const heard = ({data}) => { if (data && data.answer === id) names.push(data.name); };
    ch.addEventListener("message", heard);
    ch.postMessage({ask: id});
    return new Promise(r => setTimeout(() => { ch.removeEventListener("message", heard); r(names); }, ms));
  }
  const tell = () => { try { ch && ch.postMessage({out: true}); } catch (e) {} };
  return {due, later, finish, KEY, elsewhere, tell, ch};
})();
/* and in every tab: one opened meanwhile, and one already open (#429: Log out in another tab, which says so here with
   a storage event, and a message on the channel online or not, #432; or this one shown again, asleep when it was
   said), goes there at once, so no tab keeps the sign-in alive or shows what it had loaded. What it had typed goes,
   as the sign-out was asked for, after this tab was asked about it */
(function signedOutElsewhere() {
  if (/\/(login|setup|share)\.html$/.test(location.pathname)) return;
  const leave = () => {
    try { sgLeaving = true; } catch (e) {}   // (util.js's: the page's "Leave site?" guards stand aside)
    location.replace(new URL("login.html", SgOffline.base));
  };
  const go = () => { if (SgLogout.due()) leave(); };
  if (SgLogout.ch) SgLogout.ch.addEventListener("message", ({data}) => {
    if (!data) return;
    if (data.out) return leave();
    if (!data.ask) return;
    const e = new Event("beforeunload", {cancelable: true});   // (as logOut asks its own page)
    dispatchEvent(e);
    if (e.defaultPrevented) SgLogout.ch.postMessage({answer: data.ask, name: document.title.split(" · ")[0].split(": ")[0]});   // "Settings"
  });
  go();
  addEventListener("storage", e => { if (e.key === SgLogout.KEY || e.key === null) go(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) go(); });
  addEventListener("pageshow", e => { if (e.persisted) go(); });
})();

/* the browser's bar in the app's colours, light or dark, following the page's own theme switch too */
(function themeColour() {
  const metas = [...document.querySelectorAll('meta[name="theme-color"][media]')];
  const apply = () => {
    const t = document.documentElement.dataset.theme;
    for (const m of metas) { m.dataset.scheme ||= m.media; m.media = !t ? m.dataset.scheme : m.dataset.scheme.includes(t) ? "all" : "not all"; }
  };
  apply();
  new MutationObserver(apply).observe(document.documentElement, {attributes: true, attributeFilter: ["data-theme"]});
})();

/* ---------------------------------------------------------------- Settings → This device: offline records on it */
(async function offlineChoice() {
  const box = document.getElementById("offlineRecords");
  if (!box) return;
  const msg = el("span", {class: "msg", role: "status"});
  const input = el("input", {type: "checkbox", role: "switch", id: "offlineOn"});   // a switch (#259)
  const status = async () => {
    if (!SgOffline.works()) { msg.textContent = "Not available at this address: offline use needs HTTPS (or localhost)."; return; }
    if (!SgOffline.on()) { msg.textContent = "Off. Nothing is kept on this device."; return; }
    const c = await SgOffline.load();
    // the time it was loaded in this person's format: dates.js comes after this file, so once the page's scripts have run
    if (document.readyState === "loading") await new Promise(r => document.addEventListener("DOMContentLoaded", r, {once: true}));
    await datesReady();
    msg.textContent = c ? `On. Kept: the records in ${c.profile.name}, loaded ${fmtDT(c.loaded)}.`
      : "On. Open your records once and they'll be kept for offline viewing.";
  };
  input.checked = SgOffline.on();
  input.disabled = !SgOffline.works();
  // at once, with an Undo (#272): off deletes the copy, which comes back once records are opened again
  const turn = async on => { await SgOffline.set(on); input.checked = on; status(); };
  input.onchange = async () => {
    const on = input.checked;
    await turn(on);
    toast(on ? "Offline records are on for this device." : "Offline records are off for this device: its copy is deleted.",
      {action: "Undo", onAction: () => turn(!on)});
  };
  box.replaceChildren(   // the card's head (#494: a line of hint, its ⓘ), then a setting row (#286, util.js): its name, the switch
    el("h2", {text: "Offline records on this device"}),
    el("p", {class: "hint", text: "A copy of the last profile you opened, to read its records here without a connection."}),
    aboutWords("Sanguity opens from your home screen without a connection either way. With this on, this device also keeps a copy of " +
      "the last profile you opened, read-only, with the time it was loaded. A record's details, such as a result's values or a claim's " +
      "lines, are kept once you've opened it on this device.",
      "It holds that one profile's records, settings and watch data, never documents or other profiles. It's deleted when you sign " +
      "out, when your sign-in ends, and when you turn this off."),
    setRow(el("p", {class: "set-row-name", text: "A copy on this device"}),
    // the warning before turning it on stays in full (#280), beside the switch
    [el("p", {class: "howto", text: "That copy isn't encrypted the way records on the server are: anyone who can use this device while " +
      "it's unlocked could open it. Leave it off on a shared device."}),
    el("label", {class: "check switch"}, input, " Keep records for offline viewing on this device"),
    el("div", {class: "row"}, msg)]));
  status();
})();

/* ---------------------------------------------------------------- installing on this device
   Chrome, Edge and Samsung Internet (Android and desktop) offer an install prompt the page may open from a button
   (beforeinstallprompt); iPhone and iPad have no prompt, so the page says where Safari's Add to Home Screen is. The
   header's Install app button shows only while the browser offers that prompt; Settings → This device always says how,
   or why not here (a plain-HTTP address), or that this is the installed app already. */
const SgInstall = (() => {
  let prompt = null;
  const listeners = new Set();
  const changed = () => listeners.forEach(f => f());
  addEventListener("beforeinstallprompt", e => { e.preventDefault(); prompt = e; changed(); });
  addEventListener("appinstalled", () => { prompt = null; changed(); });
  const installed = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  // iPadOS reports itself as a Mac: one with touch is an iPad
  const ios = () => /iPhone|iPad|iPod/.test(navigator.userAgent) || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1);
  async function install() {
    if (!prompt) return "unavailable";
    const p = prompt; prompt = null;
    p.prompt();
    const r = await p.userChoice.catch(() => ({outcome: "dismissed"}));
    changed();
    return r.outcome;   // "accepted" or "dismissed"
  }
  return {installed, ios, install, can: () => !!prompt, onChange: f => listeners.add(f)};
})();

(function headerButton() {
  const b = document.getElementById("installBtn");
  if (!b) return;
  const show = () => { b.hidden = !SgInstall.can() || SgInstall.installed(); };
  b.onclick = () => SgInstall.install();
  SgInstall.onChange(show);
  show();
})();

(function installCard() {
  const box = document.getElementById("installApp");
  if (!box) return;
  const msg = el("span", {class: "msg", role: "status"});
  const body = el("div");
  function draw(https) {
    let parts;
    if (SgInstall.installed()) {
      parts = [el("p", {class: "hint", text: "You're using the installed app on this device. To remove it, delete it from the home screen like any other app."})];
    } else if (!window.isSecureContext) {
      parts = [el("p", {class: "hint", text: "Browsers only install from an HTTPS address, and this page uses plain HTTP."}),
        https ? el("p", {}, "Open ", el("a", {href: https + location.pathname, text: https}),
          " on this device, sign in, and come back here.")
          : el("p", {class: "hint", text: "Ask whoever runs this Sanguity for its HTTPS address."})];
    } else if (SgInstall.can()) {
      const btn = el("button", {class: "btn", type: "button", text: "Install on this device"});
      btn.onclick = async () => {
        const r = await SgInstall.install();
        msg.textContent = r === "accepted" ? "Installed. Open Sanguity from your home screen or app list." : r === "dismissed" ? "Not installed." : "";
        draw(https);   // the prompt is used up: drawn again without the button, keeping this answer
      };
      parts = [el("p", {class: "hint", text: "Adds Sanguity to this device's home screen or app list, with its own icon and window."}),
        el("div", {class: "row"}, btn, msg)];
    } else if (SgInstall.ios()) {
      parts = [el("p", {class: "hint", text: "On iPhone and iPad, Safari installs it:"}),
        el("ol", {class: "install-steps"},
          el("li", {text: "Tap the Share button (the square with an arrow pointing up), at the bottom or top of the screen."}),
          el("li", {text: "Scroll down and tap Add to Home Screen, then Add."}),
          el("li", {text: "Open Sanguity from its new icon. You'll be asked to sign in there once."}))];
    } else {
      parts = [el("p", {class: "hint", text: "Look in your browser's menu for Install app, Install Sanguity or Add to Home screen. " +
        "Chrome and Edge show an install button here when they're ready; Firefox on a computer can't install web apps."})];
    }
    // the last answer stays when the card is drawn again (a used prompt is gone, so the button is too)
    const more = [...parts.slice(1), ...(SgInstall.can() || !msg.textContent ? [] : [el("div", {class: "row"}, msg)])];
    // the card's head a line of hint (#494), then a setting row (#286) where there's more than words
    body.replaceChildren(parts[0], ...(more.length ? [setRow(el("p", {class: "set-row-name", text: "How to install"}), more)] : []));
  }
  box.replaceChildren(el("h2", {text: "Install on this device"}), body);
  let https = null;
  draw(https);
  SgInstall.onChange(() => draw(https));
  if (!window.isSecureContext) fetch("api/me", {credentials: "same-origin"}).then(r => r.ok ? r.json() : {}).then(j => { https = j.https_address || null; draw(https); }).catch(() => {});
})();
