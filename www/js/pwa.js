/* Sanguity · install as a phone app (#56), on every page (after util.js): registers the service worker (sw.js),
   keeps this device's choice about offline records, and deletes the offline copy whenever the sign-in ends.

   Offline records are off unless the person turns them on, per device, under Account. When on, the records page
   (js/offline.js) keeps one copy of the last profile it loaded: its records, settings and watch data, never
   documents (PDFs, pictures) and never another profile's. That copy is outside the server's encryption at rest, so it's
   deleted on sign-out, when the sign-in page is reached (a sign-in that ended), when the sign-in's own end time passes
   (even with no connection), and when the option is turned off.

   Why it isn't encrypted on the device: to open offline, the page needs the key without the server, so the key would
   have to be kept on the phone right beside the copy, and anyone who can open the app on the unlocked phone could
   read it either way. What a key from the server would add (a signed-out device holding nothing readable) comes from
   deleting the copy instead. So it's kept plainly, and the Account page says who could see it. */
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
  return {base, works, on, set, save, load, drop, forget};
})();

if (SgOffline.works()) navigator.serviceWorker.register(new URL("sw.js", SgOffline.base), {scope: SgOffline.base.pathname})
  .catch(e => console.warn("offline use unavailable:", e.message));

// signing out (any page's Log out button) or reaching the sign-in or setup page: whatever the device kept is deleted
document.addEventListener("click", e => { if (e.target.closest && e.target.closest("#logout")) SgOffline.forget(); }, true);
if (/\/(login|setup)\.html$/.test(location.pathname)) SgOffline.forget();

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

/* ---------------------------------------------------------------- Account: offline records on this device */
(async function offlineChoice() {
  const box = document.getElementById("offlineRecords");
  if (!box) return;
  const msg = el("span", {class: "msg", role: "status"});
  const input = el("input", {type: "checkbox", id: "offlineOn"});
  const status = async () => {
    if (!SgOffline.works()) { msg.textContent = "Not available at this address: offline use needs HTTPS (or localhost)."; return; }
    if (!SgOffline.on()) { msg.textContent = "Off. Nothing is kept on this device."; return; }
    const c = await SgOffline.load();
    msg.textContent = c ? `On. Kept: ${c.profile.name}'s records, loaded ${new Date(c.loaded).toLocaleString()}.`
      : "On. Open your records once and they'll be kept for offline viewing.";
  };
  input.checked = SgOffline.on();
  input.disabled = !SgOffline.works();
  input.onchange = async () => { await SgOffline.set(input.checked); status(); };
  box.replaceChildren(
    el("h2", {text: "Offline records on this device"}),
    el("p", {class: "hint", text: "Sanguity opens from your home screen without a connection either way. With this on, this device also " +
      "keeps a copy of the last profile you opened, so you can read its records offline (read-only, with the time they were loaded)."}),
    el("p", {class: "hint", text: "That copy isn't encrypted the way records on the server are: anyone who can use this device while it's " +
      "unlocked could open it. It holds that one profile's records, settings and watch data, never documents or other profiles. " +
      "It's deleted when you sign out, when your sign-in ends, and when you turn this off. Leave it off on a shared device."}),
    el("label", {class: "check"}, input, " Keep records for offline viewing on this device"),
    el("div", {class: "row"}, msg));
  status();
})();
