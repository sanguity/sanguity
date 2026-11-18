/* Sanguity · helpers shared by every page: $, el (build DOM), api (call the backend).
   Plain script, loaded first; all files share one global scope. */
"use strict";
const $ = s => document.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const e = tag.startsWith("svg:") ? document.createElementNS("http://www.w3.org/2000/svg", tag.slice(4)) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    // an ARIA state is "true" or "false", never left out: CSS and screen readers read [aria-pressed="false"] as off
    if (k.startsWith("aria-") && typeof v === "boolean") { e.setAttribute(k, String(v)); continue; }
    if (v == null || v === false) continue;
    if (k === "text") e.textContent = v;
    else if (k === "class") e.setAttribute("class", v);
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
    else e.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null) e.append(c.nodeType ? c : document.createTextNode(c));
  return e;
};
/* the parts given that are there: Element.append, .prepend and .replaceChildren write a null out as the text "null"
   (#245), so a part that may be left out (x ? el(…) : null) goes in through this, as el() leaves out its own children:
   box.append(...present(a, b ? el(…) : null)) */
const present = (...kids) => kids.flat().filter(k => k != null);
/* a row that opens something when clicked, a button to the keyboard and screen readers too (#202): el("div", {class:
   "ov-row", ...pressable(() => openEvent(e))}, …). Its words are its name. A table's row gets a .cell-btn in a cell. */
const pressable = fn => ({role: "button", tabindex: 0, onclick: fn,
  onkeydown: ev => { if ((ev.key === "Enter" || ev.key === " ") && ev.target === ev.currentTarget) { ev.preventDefault(); fn(ev); } }});
/* "More about this" (#176): the rest of a card's explanation, a click away but still on the page and read by screen
   readers. Strings become paragraphs; nulls are left out. Pages write the same <details class="more"> by hand. */
const moreAbout = (...kids) => el("details", {class: "more"}, el("summary", {text: "More about this"}),
  ...kids.flat().filter(k => k != null && k !== "").map(k => typeof k === "string" ? el("p", {text: k}) : k));
/* An empty state (#199): one sentence saying what would appear here, and the one next step, a link ({href, text}) or
   a button ({onclick, text}); none (null) for someone who can't take it. tag: "div" for a whole card's place, "li"
   inside a list. The records tabs, the Sources and Sharing lists and the journal use it. */
const emptyState = (what, next, {tag = "div", cls = "card"} = {}) => el(tag, {class: `${cls} empty-state`.trim()}, el("p", {text: what}),
  next ? el(next.href ? "a" : "button", {class: "btn primary empty-next", href: next.href, type: next.href ? null : "button", text: next.text, onclick: next.onclick}) : null);
/* a line of text with each date in it kept on one line (#330), so a tile's "▼ −0.03 since Aug 18, 2026" never leaves
   its year alone below: the text as parts for el(), a date in a <span class="nw">, its textContent unchanged. The
   app's three date formats (dates.js): "Aug 18, 2026", "18 Aug 2026" and "2026-08-18", and "as of" kept as one too
   (#339; the line may break before the date, never between "as" and "of": with it, a date was wider than a narrow
   tile). Each date is anchored: no digit or point before its day, nothing more of a date after its year, and a
   month's short name a word of its own, two letters or more ("ag.", "Aug", "marras", "szept.", #345), a point after a
   day allowed ("18. Aug. 2026"), so "0.03 since 2026-08-18" keeps
   only the date together, never "03 since 2026" */
const keepDates = s => String(s).split(/((?<!\p{L})as of(?!\p{L})|(?:(?<![\d.])\d{4}-\d{2}-\d{2}(?![-\d])|(?<![\d.])\d{1,2}\.? \p{L}{2,}\.? \d{4}(?![-\d])|(?<!\p{L})\p{L}{2,}\.? \d{1,2}, \d{4}(?![-\d])))/u)
  .map((t, i) => i % 2 ? el("span", {class: "nw", text: t}) : t).filter(t => t !== "");
/* every backend call: same-origin cookie, never cached, and the X-Sanguity header the API requires on changes */
const api = (path, opts = {}) => fetch("api/" + path, {cache: "no-store", credentials: "same-origin", ...opts,
  headers: {...(opts.method && opts.method !== "GET" ? {"X-Sanguity": "1", "Content-Type": "application/json"} : {}), ...opts.headers}});

/* The theme (#45): this browser's own choice (the ◐ toggle) wins, then the account's default (light, dark or
   "system"), remembered here so theme.js can apply it before the next page draws, then the system's. */
function applyTheme(accountDefault) {
  try {
    if (accountDefault !== undefined) {
      if (accountDefault === "light" || accountDefault === "dark") localStorage.setItem("ht-theme-default", accountDefault);
      else localStorage.removeItem("ht-theme-default");
    }
  } catch (e) {}
  let t = null;
  try { t = localStorage.getItem("ht-theme") || localStorage.getItem("ht-theme-default"); } catch (e) {}
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
}

/* The Overview's key-lab tiles (#36), shared by the Overview and the Settings preview: the chosen labs this profile
   has results for, in order. While the choice is still the default list and fewer than half of it (or of six) has
   results here, the profile's most-measured labs fill as many tiles, so a new profile needs no setting. */
const mostMeasured = series => series.filter(s => s && !s.watch && s.pts && s.pts.length)
  .sort((a, b) => b.pts.length - a.pts.length || a.name.localeCompare(b.name)).map(s => s.name);
function keyLabTiles(chosen, series, isDefault) {
  const has = new Set(series.filter(s => s && !s.watch && s.pts && s.pts.length).map(s => s.name));
  const out = chosen.filter(n => has.has(n));
  if (isDefault && out.length < Math.min(6, Math.ceil(chosen.length / 2)))
    for (const n of mostMeasured(series)) { if (out.length >= chosen.length) break; if (!out.includes(n)) out.push(n); }
  return out;
}

/* A warning when this page came over plain HTTP from somewhere other than this machine, the home network or a
   tailnet (where WireGuard already encrypts): passwords and records would cross the internet readable (ASVS 9.1.1). */
function plainHttpWarning() {
  if (location.protocol !== "http:") return null;
  const h = location.hostname.toLowerCase().replace(/^\[|\]$/g, "");
  const ip4 = h.match(/^(\d+)\.(\d+)\.\d+\.\d+$/);
  const local = h === "localhost" || h.endsWith(".localhost") || h.endsWith(".local") || h.endsWith(".lan") ||
    h.endsWith(".home.arpa") || h.endsWith(".internal") || h.endsWith(".ts.net") || !h.includes(".") && !h.includes(":") ||
    (ip4 && (+ip4[1] === 10 || +ip4[1] === 127 || (+ip4[1] === 192 && +ip4[2] === 168) || (+ip4[1] === 172 && +ip4[2] >= 16 && +ip4[2] <= 31) ||
             (+ip4[1] === 169 && +ip4[2] === 254) || (+ip4[1] === 100 && +ip4[2] >= 64 && +ip4[2] <= 127))) ||
    h === "::1" || /^f[cd]/.test(h) || h.startsWith("fe80:");
  return local ? null : "This page isn't using HTTPS, so your password and records travel unencrypted. " +
    "Put Sanguity behind HTTPS (a reverse proxy or Tailscale) before using it over the internet.";
}

/* passkeys (WebAuthn): the server's JSON options in, the browser's answer back out as JSON (base64url) */
const b64u = {
  dec: s => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)), c => c.charCodeAt(0)).buffer,
  enc: b => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""),
};
const passkeysWork = () => !!(window.PublicKeyCredential && navigator.credentials && window.isSecureContext);
const _creds = list => (list || []).map(c => ({...c, id: b64u.dec(c.id)}));
async function passkeyCreate(o) {
  const c = await navigator.credentials.create({publicKey: {...o, challenge: b64u.dec(o.challenge),
    user: {...o.user, id: b64u.dec(o.user.id)}, excludeCredentials: _creds(o.excludeCredentials)}});
  return {id: c.id, rawId: b64u.enc(c.rawId), type: c.type, clientExtensionResults: c.getClientExtensionResults(),
    response: {clientDataJSON: b64u.enc(c.response.clientDataJSON), attestationObject: b64u.enc(c.response.attestationObject),
      transports: c.response.getTransports ? c.response.getTransports() : []}};
}
async function passkeyGet(o) {
  const c = await navigator.credentials.get({publicKey: {...o, challenge: b64u.dec(o.challenge), allowCredentials: _creds(o.allowCredentials)}});
  const r = c.response;
  return {id: c.id, rawId: b64u.enc(c.rawId), type: c.type, clientExtensionResults: c.getClientExtensionResults(),
    response: {clientDataJSON: b64u.enc(r.clientDataJSON), authenticatorData: b64u.enc(r.authenticatorData),
      signature: b64u.enc(r.signature), userHandle: r.userHandle && r.userHandle.byteLength ? b64u.enc(r.userHandle) : null}};
}
/* Tell the device which passkeys Sanguity no longer accepts, so it can delete them (the WebAuthn Signal API;
   browsers without it just skip this). */
function passkeySignalUnknown(rpId, credentialId) {
  try { if (PublicKeyCredential.signalUnknownCredential) PublicKeyCredential.signalUnknownCredential({rpId, credentialId}).catch(() => {}); } catch (e) {}
}
function passkeySignalAccepted(rpId, userId, ids) {
  try { if (PublicKeyCredential.signalAllAcceptedCredentials) PublicKeyCredential.signalAllAcceptedCredentials({rpId, userId, allAcceptedCredentialIds: ids}).catch(() => {}); } catch (e) {}
}
/* the browser's refusal, in words: cancelled, timed out, or not allowed here */
const passkeyProblem = e => e && e.name === "NotAllowedError" ? "The passkey request was cancelled or timed out."
  : e && e.name === "InvalidStateError" ? "This device already has a passkey for your account."
  : e && e.name === "SecurityError" ? "Passkeys don't work at this address. Open Sanguity at its https:// name."
  : (e && e.message) || "The passkey didn't work.";

/* ---- keyboard and screen-reader basics (#209)
   announce(text, {error}): says a short message to screen readers through the page's one shared status line, two
   visually hidden live regions every page has (#sgStatus, role=status: said when the reader is free, for saves and
   changes; #sgAlert, role=alert: said at once, for errors). Nothing is shown: a message someone should see goes on
   the page too, beside what it's about (sayIn does both). The same words twice in a row are said twice; messages
   within a moment of each other are said together. Use it for anything that changes without moving focus. */
const announce = (() => {
  const box = {}, wait = {}, next = {};
  const make = () => {
    if (box.status || !document.body) return;
    box.status = el("div", {class: "vh sg-live", id: "sgStatus", role: "status"});
    box.alert = el("div", {class: "vh sg-live", id: "sgAlert", role: "alert"});
    document.body.append(box.status, box.alert);
  };
  make();   // there from the start: a screen reader only follows a live region that was in the page before it changed
  if (!box.status) document.addEventListener("DOMContentLoaded", make);
  return (text, {error = false} = {}) => {
    make();
    const k = error ? "alert" : "status";
    if (!box[k] || !text) return;
    clearTimeout(wait[k]);
    next[k] = [...(next[k] || []), String(text)];
    box[k].textContent = "";   // emptied first, so the same words again are a change too
    wait[k] = setTimeout(() => { box[k].textContent = next[k].join(" "); next[k] = []; }, 60);
  };
})();
/* sayIn(m, text, bad): a page's message line (a .msg beside its Save button, a form's hint): shows the text (or
   nodes: words with a link), marked .bad for an error, and says it through announce(), as an alert when bad. The
   line itself stops being a live region (older markup has role=status), so it isn't said twice. An empty text
   clears it and says nothing. repeat: false for a line redrawn as you type, whose same words again aren't said. */
function sayIn(m, text, bad = false, {repeat = true} = {}) {
  if (!m) return;
  if (m.getAttribute("role") === "status") m.removeAttribute("role");
  const was = m.textContent;
  m.replaceChildren(...[].concat(text == null ? [] : text));
  m.classList.toggle("bad", !!bad);
  if (m.textContent && (repeat || m.textContent !== was)) announce(m.textContent, {error: !!bad});
}
/* viewTitle(title, say): the browser tab's title for what's shown ("Labs · Ben" → "Labs · Ben · Sanguity"), and,
   when say is given, those words said politely: for a view changed by a sidebar link or a button elsewhere, which a
   screen reader wouldn't otherwise mention (a tab picked in the tab bar already says it's selected) */
function viewTitle(title, say) {
  document.title = title ? `${title} · Sanguity` : "Sanguity";
  if (say) announce(say);
}
/* scrollTo/scrollIntoView's behavior: "smooth", unless the person asked for less motion (CSS can't turn off a
   script's smooth scroll; app.css turns off the transitions) */
const smoothly = () => matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
/* "Skip to content": the first thing Tab reaches on a page with a top bar, shown while it has focus. It moves focus
   to the page's <main> without changing the address (the records tabs and the sidebars' groups live there). */
(() => {
  const bar = document.querySelector("body > header"), main = document.querySelector("main");
  if (!bar || !main) return;
  main.id ||= "content";
  document.body.prepend(el("a", {class: "skip-link", href: "#" + main.id, text: "Skip to content", onclick: ev => {
    ev.preventDefault();
    main.tabIndex = -1;
    main.focus({preventScroll: true});
    if (main.getBoundingClientRect().top < 0) main.scrollIntoView();
  }}));
})();
/* A table's column names on its cells (#320): on a phone an .acct .tbl hides its header row and each row is a little
   card (app.css), so each cell shows its column's name before its value instead, from data-label (td::before).
   labelCells(table) copies the names; a cell under an empty heading (a row's buttons), spanning columns or with
   nothing in it gets none.
   It's run here for every table.tbl on the account pages as it's drawn or redrawn, so no page labels its own. */
function labelCells(table) {
  const heads = table.tHead && table.tHead.rows[0] ? [...table.tHead.rows[0].cells].flatMap(th => Array(th.colSpan).fill(th.colSpan > 1 ? "" : th.textContent.trim())) : [];
  for (const body of table.tBodies) for (const row of body.rows) {
    let col = 0;
    for (const c of row.cells) {
      const name = c.colSpan === 1 && c.tagName === "TD" && (c.textContent.trim() || c.querySelector("input, select, textarea, button, img, svg"))
        ? heads[col] || "" : "";
      col += c.colSpan;
      if (name && c.dataset.label !== name) c.dataset.label = name;
      else if (!name && c.hasAttribute("data-label")) c.removeAttribute("data-label");
    }
  }
}
(() => {
  const main = document.querySelector("main.acct");
  if (!main) return;
  const all = root => root.querySelectorAll ? [...(root.matches("table.tbl") ? [root] : []), ...root.querySelectorAll("table.tbl")] : [];
  all(main).forEach(labelCells);
  new MutationObserver(recs => {
    const seen = new Set();
    for (const r of recs) {
      const t = r.target.closest && r.target.closest("table.tbl");
      if (t) seen.add(t);
      for (const n of r.addedNodes) for (const x of all(n)) seen.add(x);
    }
    seen.forEach(labelCells);
  }).observe(main, {childList: true, subtree: true});
})();

/* ---- the app's own dialog (#210)
   ask({title, message, action, danger, cancel, field}): asks before doing something, in a <dialog> opened with
   showModal(), not the browser's own pop-up (which ignores dark mode, can't mark the risky button, and on some phones
   is titled with the server's address). It shows the title, the message (words, a list of paragraphs, or nodes),
   Cancel and a button named for the action ("Remove passkey", "Turn off"). danger: the action can't be
   undone (it removes, revokes or turns something off): its button is red and focus starts on Cancel, so a stray
   Enter or Space doesn't do it; otherwise focus starts on the action. field: an input inside the message, focused
   first; Enter in it does the action. Escape or Cancel say no. Resolves true for the action, false otherwise, and
   focus goes back to whatever had it (the button that opened it). Use: if (!await ask({...})) return;
   A plain notice isn't asked: it goes in the page's message line beside what it's about (sayIn, above). */
function ask({title, message = "", action = "OK", cancel = "Cancel", danger = false, field = null} = {}) {
  const opener = document.activeElement, id = "sgAsk" + (ask.n = (ask.n || 0) + 1);
  return new Promise(resolve => {
    const no = el("button", {class: "btn", type: "button", text: cancel, onclick: () => box.close("")});
    const yes = el("button", {class: "btn " + (danger ? "danger" : "primary"), type: "submit", value: "yes", text: action});
    const box = el("dialog", {class: "sg-ask", role: danger ? "alertdialog" : null, "aria-labelledby": id + "t", "aria-describedby": id + "m"},
      el("form", {method: "dialog"},
        el("h2", {id: id + "t", text: title}),
        el("div", {class: "sg-ask-msg", id: id + "m"}, ...[].concat(message).filter(m => m != null && m !== "")
          .map(m => typeof m === "string" ? el("p", {text: m}) : m)),
        el("div", {class: "sg-ask-acts"}, no, yes)));
    box.addEventListener("close", () => {
      box.remove();
      if (opener && opener.isConnected && opener !== document.body) opener.focus();
      resolve(box.returnValue === "yes");
    });
    document.body.append(box);
    box.showModal();
    (field || (danger ? no : yes)).focus();
  });
}

/* ---- a table's wrapper that may scroll (#335, #340)
   scrollBox(attrs, ...kids): el("div", attrs, ...kids), for a wrapper (.tablewrap, .share-scroll) whose table may be
   wider or taller than it. While it scrolls, sideways or down, it's a tab stop and a region, so a keyboard can reach
   what's out of sight and a screen reader says what it is; while all of it shows, it's neither, so it adds no stop
   and no landmark. A ResizeObserver on it and on its table decides, as the window, the text or the rows change. Its
   name stays on: give it an aria-label (or aria-labelledby to its heading); without one it takes the table's caption
   or its column names. */
function scrollBox(attrs, ...kids) {
  const box = el("div", attrs, ...kids);
  if (!box.hasAttribute("aria-label") && !box.hasAttribute("aria-labelledby")) {
    const t = box.querySelector("table"), head = t && t.tHead && t.tHead.rows[0];
    box.setAttribute("aria-label", (t && t.caption ? t.caption.textContent.trim() : "") ||
      (head ? [...head.cells].map(c => c.textContent.trim()).filter(Boolean).join(", ") : "") || "Table");
  }
  const fit = () => {
    const scrolls = box.scrollWidth > box.clientWidth + 1 || box.scrollHeight > box.clientHeight + 1;
    if (scrolls && box.getAttribute("role") !== "region") { box.setAttribute("tabindex", "0"); box.setAttribute("role", "region"); }
    else if (!scrolls && box.hasAttribute("role")) {
      box.removeAttribute("role");
      if (box === document.activeElement) box.setAttribute("tabindex", "-1");   // focus stays put, out of the Tab order
      else box.removeAttribute("tabindex");
    }
  };
  const watch = new ResizeObserver(fit);
  for (const n of [box, ...box.children]) watch.observe(n);
  return box;
}
