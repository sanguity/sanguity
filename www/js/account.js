/* Sanguity · Settings → You (#991; the Account page before, #169): Sign-in and security (password, two-factor
   sign-in, passkeys, where you're signed in) and This device (installing Sanguity, offline records; their cards are
   pwa.js's), two groups of Settings' sidebar (settings-nav.js, which starts this: acctLock, acctStart). Profiles, with
   their sharing and activity, are on the Sharing page (sharing.js, #168); the server's administration is Settings →
   Install (admin.js). call(), say(), when() and ME are settings.js's. */
"use strict";   // $, el and api come from util.js
const device = ua => {
  if (!ua) return "Unknown device";
  const b = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "Browser";
  const os = /iPhone|iPad/.test(ua) ? "iOS" : /Android/.test(ua) ? "Android" : /Windows/.test(ua) ? "Windows" : /Mac OS/.test(ua) ? "macOS" : /Linux/.test(ua) ? "Linux" : "";
  return os ? `${b} on ${os}` : b;
};

/* (#1109's review) with Sign in with OIDC only on, no password signs in, so none is asked: a change that asks for it
   asks for the fresh check instead (a code, a passkey or the provider: admin.js fresh, confirmIdentity), in its card */
const pwOff = () => !!ME.sign_in_provider?.only;
function reauthIn(card) {
  let box = card.querySelector(":scope > .reauth");
  if (!box) { box = el("div", {class: "reauth", hidden: true}); card.querySelector("h2").after(box); }
  return box;
}
const proved = (send, card) => pwOff() ? fresh(send, reauthIn(card)) : send();

/* ---------------------------------------------------------------- password */
/* each with Show (util.js pwField, #492), hidden again only as it's sent: not over a mismatch. Its reply brought into
   view with Change password, clear of a phone's tabs, focus where it was (#544: under the tabs, or below the screen) */
pwField($("#pwCur"), {name: "current password"});
pwField($("#pwNew"), {name: "new password"});
pwField($("#pwNew2"), {name: "repeated password"});
const pwSay = (text, bad) => { say("#pwMsg", text, bad); $("#pwMsg").parentElement.scrollIntoView({block: "nearest"}); };
/* your calendar feeds, which changing your password and removing a passkey end as signing out everywhere does (#597,
   #590): said beforehand, with how many (GET /api/me), and after, with where to make a new one */
const feedsWord = n => n === 1 ? "your calendar feed" : `your ${n} calendar feeds`;
const feedsWhy = n => `as a lost phone's calendar app keeps ${n === 1 ? "its address" : "their addresses"}`;
const feedsEnd = n => `${n === 1 ? "Your calendar feed is" : `Your ${n} calendar feeds are`} turned off too, ${feedsWhy(n)}: ` +
  `you can make ${n === 1 ? "a new one" : "new ones"} under Sharing, Calendar feed.`;
const feedsGone = n => n ? ` ${n === 1 ? "Your calendar feed is" : `Your ${n} calendar feeds are`} turned off too: make ` +
  `${n === 1 ? "a new one" : "new ones"} under Sharing, Calendar feed.` : "";
function pwFeeds() {   // the line beside Change password, while there are some: its form's and button's description (#962)
  const n = ME.calendar_feeds || 0;
  $("#pwFeeds").textContent = n ? `Changing it signs out your other devices and turns off ${feedsWord(n)}, ${feedsWhy(n)}. ` +
    `You can make ${n === 1 ? "a new one" : "new ones"} under Sharing, Calendar feed.` : "";
  $("#pwFeeds").hidden = !n;
}
/* how many, counted as it's asked (#962: page load's count, so a feed made or turned off since, in another tab, went
   unsaid or was said): as focus comes into Change password's form, and before a passkey's removal asks. -> the count */
async function feedsNow() {
  try { ME.calendar_feeds = (await call("me")).calendar_feeds || 0; pwFeeds(); } catch (e) {}   // (kept as it was offline)
  return ME?.calendar_feeds || 0;
}
$("#pwForm").addEventListener("focusin", ev => { if (!ev.currentTarget.contains(ev.relatedTarget)) feedsNow(); });
$("#pwForm").onsubmit = async ev => {
  ev.preventDefault();
  if ($("#pwNew").value !== $("#pwNew2").value) return pwSay("The new passwords don't match.", true);
  pwSent(ev.target);
  try {
    const r = await call("me/password", "POST", {current: $("#pwCur").value, new: $("#pwNew").value});
    $("#pwForm").reset();
    if (ME.user.must_change_pw) { location.replace("./"); return; }
    pwSay("Password changed." + (r.signed_out_elsewhere ? ` Signed out ${r.signed_out_elsewhere} other device${r.signed_out_elsewhere > 1 ? "s" : ""}.` : "") +
      feedsGone(r.calendar_feeds));   // (#597)
    ME.calendar_feeds = 0; pwFeeds();
    loadSessions();
  } catch (e) { pwSay(e.message, true); }
};

/* ---------------------------------------------------------------- sessions */
/* how long a sign-in lasts, as the admins set it (#1001: Accounts and sign-in → Sign-in rules, which tells it again on a change), read-only */
const sessLasts = () => { $("#sessLasts").textContent = signInLasts(ME.session, "You stay signed in on a device until you log out") + " This server's admins set it."; };
async function loadSessions() {
  const list = await call("sessions");
  sessLasts();
  $("#sessList").replaceChildren(el("table", {class: "tbl"},
    el("thead", {}, el("tr", {}, ...["Device", "Address", "Signed in", "Last active", ""].map(thOf))),
    el("tbody", {}, ...list.map(s => el("tr", {},
      el("td", {}, device(s.agent), s.current ? el("span", {class: "tag", text: "this device"}) : null),
      // each time kept with its AM or PM (keepDates, util.js; #515: "8:37 / AM" at 320 px)
      el("td", {text: s.ip || "–"}), el("td", {}, keepDates(when(s.created))), el("td", {}, keepDates(when(s.last_seen))),
      el("td", {}, s.current ? null : el("button", {class: "btn small", type: "button", text: "Sign out",
        onclick: async () => {
          const at = list.indexOf(s);
          try { await call("sessions/" + s.id, "DELETE"); await loadSessions(); }
          catch (e) {   // signed out elsewhere (#524: its row stayed, "No such session." above the page's top): where it was
            if (e.status !== 404) { say("#sessMsg", e.message, true); return; }
            await loadSessions(); say("#sessMsg", "");
            return goneSay([...$("#sessList tbody").rows], at, e.message, "button", $("#sessions h2"), $("#sessList"));
          }
          sessFocus(at);
        }})))))));
  $("#sessOthers").disabled = list.length < 2;
}
/* its button gone with the device signed out (#493): focus on the next device's Sign out, else the one before's, else
   (none left but this one) on the card's heading (util.js) */
const sessFocus = (at = 0) => focusNear([...$("#sessList tbody").rows], at, "button", $("#sessions h2"));
$("#sessOthers").onclick = async () => {
  // it turns off your calendar feeds too (#590): asked first, saying so, with how many, only when you have one (#1078)
  const feeds = await feedsNow();
  if (!await ask({title: "Sign out all other devices?", action: "Sign out others", message: feeds ? feedsEnd(feeds) : ""})) return;
  try {
    const r = await call("sessions/revoke-others", "POST");
    // the calendar feeds that went with them (#590), said, with where to make a new one
    say("#sessMsg", `Signed out ${r.revoked} device${r.revoked === 1 ? "" : "s"}.` + feedsGone(r.calendar_feeds));
    if (r.calendar_feeds) { ME.calendar_feeds = 0; pwFeeds(); }
    await loadSessions(); sessFocus();
  }
  catch (e) { say("#sessMsg", e.message, true); }
};

/* ---------------------------------------------------------------- two-factor sign-in */
const APPS = "an authenticator app such as Google Authenticator, Microsoft Authenticator, 1Password or Bitwarden";
/* the line under the heading: a hint at rest, the steps to take while turning it on (#280: those stay in full) */
const tfHint = (text, steps) => { const p = $("#tfHint"); p.textContent = text; p.className = steps ? "howto" : "hint"; };
/* focus moved to what a button of its own drew in its place (#499: it fell to the page with the button): to, in view
   with its card, its heading and the line under it saying what now, where the card fits the window */
function tfFocus(to) {
  const card = $("#twofa"), pad = getComputedStyle(document.documentElement);
  if (to.matches("h2")) to.tabIndex = -1;
  to.focus({preventScroll: true});
  (card.offsetHeight <= innerHeight - parseFloat(pad.scrollPaddingTop) - parseFloat(pad.scrollPaddingBottom) ? card : to).scrollIntoView({block: "nearest"});
}
/* focus: true once one of its own buttons drew it again (Cancel, Turn off, I've saved them): then on Turn on, else (on
   now) on the card's heading */
async function loadTwoFactor(focus = false) {
  const st = await call("me/2fa"), body = $("#tfBody");
  say("#tfMsg", "");
  if (!st.on) {
    tfHint(`Ask for a code from an authenticator app as well as your password, so a stolen password alone can't open your records.` +
      (st.required ? " This server requires it." : ""));
    // your password first (#590: a device left signed in could turn on an app of its own and lock you out), with Show
    const pw = el("input", {type: "password", autocomplete: "current-password", placeholder: "Your password", "aria-label": "Your password", required: true});
    body.replaceChildren(el("form", {class: "row", onsubmit: ev => { ev.preventDefault(); pwSent(ev.target); startTwoFactor(pw.value); }},
      pwOff() ? null : pwField(pw).box, el("button", {class: "btn primary", type: "submit", text: "Turn on"})));
    if (focus) tfFocus(pw);
    return;
  }
  tfHint(`On. You have ${st.recovery_codes_left} recovery code${st.recovery_codes_left === 1 ? "" : "s"} left` +
    (st.recovery_codes_left <= 3 ? ": make new ones soon." : ".") +
    // #928: stopped after 100 wrong codes since the last right one, which someone who knows the password may have made
    (st.codes_stopped ? " But codes from your authenticator app are stopped: too many wrong ones were entered since the last right one, " +
      "so someone may know your password. Change your password, then sign in once with a recovery code to use codes again, " +
      "or ask an admin to reset your two-factor sign-in." : ""));
  const code = el("input", {inputmode: "numeric", autocomplete: "one-time-code", placeholder: "Code from the app", "aria-label": "Code from your authenticator app"});
  const pw = el("input", {type: "password", autocomplete: "current-password", placeholder: "Your password", "aria-label": "Your password"});
  body.replaceChildren(
    el("form", {class: "row", onsubmit: async ev => { ev.preventDefault();
      try { showRecoveryCodes((await call("me/2fa/recovery-codes", "POST", {code: code.value})).recovery_codes); } catch (e) { say("#tfMsg", e.message, true); } }},
      code, el("button", {class: "btn small", type: "submit", text: "Make new recovery codes"})),
    st.required && !st.passkeys ? el("p", {class: "hint", text: "This server requires two-factor sign-in, so it can't be turned off. Add a passkey first to stop using the app."}) :
    el("form", {class: "row", onsubmit: async ev => { ev.preventDefault();
      if (!await ask({title: "Turn off two-factor sign-in?", message: "Every other device will be signed out. You can turn it on again later.",
                      action: "Turn off", danger: true})) return;
      pwSent(ev.target);
      try {
        const r = await proved(() => call("me/2fa/off", "POST", {password: pw.value}), $("#twofa"));
        if (r.must_change_pw) { location.replace(location.pathname + location.search + "#password"); location.reload(); return; }
        loadTwoFactor(true); loadPasskeys(); loadSessions();
      } catch (e) { say("#tfMsg", e.message, true); } }},
      pwOff() ? null : pwField(pw).box, el("button", {class: "btn small", type: "submit", text: "Turn off"})));   // with Show (#492)
  if (focus) tfFocus($("#twofa h2"));
}
/* password: the one typed beside Turn on, sent again with the first code (the server checks both, #590) */
async function startTwoFactor(password) {
  let s;
  try { s = await proved(() => call("me/2fa/start", "POST", {password}), $("#twofa")); } catch (e) { return say("#tfMsg", e.message, true); }
  const code = el("input", {inputmode: "numeric", autocomplete: "one-time-code", placeholder: "123 456", "aria-label": "Code from your authenticator app", required: true});
  tfHint(`Scan this with ${APPS}, then enter the code it shows. If you can't scan, type the key instead.`, true);
  // its QR code drawn here from the otpauth address (qr.js, #495: the server drew a second one), shown, as it's the way
  // in; the key under it with the app's one Copy (util.js copyBtn), for typing it in instead
  const key = el("code", {class: "tf-key", text: s.secret.match(/.{1,4}/g).join(" ")});
  $("#tfBody").replaceChildren(
    el("div", {class: "tf-setup"},
      qrShow(s.uri, {what: "the key for your authenticator app", caption: "Scan it with the authenticator app.", shown: true})?.fig,
      el("div", {class: "copy-val"}, el("span", {class: "copy-val-name", text: "Key"}),
        el("div", {class: "copy-val-row"}, key, copyBtn(s.secret, {what: "the key", sel: key})))),
    el("form", {class: "row", onsubmit: async ev => { ev.preventDefault();
      try { showRecoveryCodes((await proved(() => call("me/2fa/confirm", "POST", {code: code.value, password}), $("#twofa"))).recovery_codes, true); } catch (e) { say("#tfMsg", e.message, true); } }},
      code, el("button", {class: "btn primary small", type: "submit", text: "Confirm"}),
      el("button", {class: "btn small", type: "button", text: "Cancel", onclick: () => loadTwoFactor(true)})));
  code.focus();
}
function showRecoveryCodes(codes, justTurnedOn) {
  const text = "Sanguity recovery codes for " + ME.user.username + "\nEach works once, in place of a code from your app.\n\n" + codes.join("\n") + "\n";
  say("#tfMsg", "");   // (#502: a wrong code's "doesn't match" stayed under them)
  tfHint((justTurnedOn ? "Two-factor sign-in is on. " : "") +
    "Save these recovery codes somewhere safe, away from your phone. Each one signs you in once if you lose your phone. " +
    "They won't be shown again.", true);
  const done = el("button", {class: "btn primary small", type: "button", text: "I've saved them", onclick: async () => {
    if (ME.user.needs_two_factor) { location.replace("./"); return; }
    await Promise.all([loadTwoFactor(true), loadPasskeys(), loadSessions()]);
    adminAgain("#tfMsg"); }});
  // the codes focused as they're shown (#499), in view
  const shown = el("pre", {class: "tf-codes", tabindex: "-1", text: codes.join("\n")});
  $("#tfBody").replaceChildren(
    shown,
    el("div", {class: "row"},
      el("a", {class: "btn small", download: "sanguity-recovery-codes.txt", href: "data:text/plain;charset=utf-8," + encodeURIComponent(text), text: "Download"}),
      done));
  tfFocus(shown);
}

/* after a second factor is added: who's signed in, again; an admin who needed one for Install (once the server has
   more than one account) and now has one is told Install is open again, its link drawing the page again on Accounts
   (Install's cards say what's missing until then: admin.js adminGate) */
async function adminAgain(m) {
  const had = ME.user.admin_needs_two_factor;
  ME = await call("me");
  if (had && !ME.user.admin_needs_two_factor) $(m).append(" Install is open to you again: ", el("a", {href: "#accounts", text: "go to Accounts and sign-in",
    onclick: ev => { ev.preventDefault(); location.replace("#accounts"); location.reload(); }}), ".");
}

/* ---------------------------------------------------------------- passkeys */
const guessName = () => { const d = device(navigator.userAgent); return d === "Browser" ? "Passkey" : d.replace(/^\w+ on /, ""); };
async function loadPasskeys() {
  // the card's own words first, and each part below drawn whatever happens to another (#1117: the list not loaded
  // left the card its heading alone, no list and no Add, nothing said, though the passkeys still signed in)
  $("#pkHint").textContent = "Face ID, Touch ID, Windows Hello, your phone or a security key, on its own or after your password.";
  let st;
  try { st = await call("me/passkeys?origin=" + encodeURIComponent(location.origin)); }
  catch (e) { $("#pkWhy").hidden = true; $("#pkBody").replaceChildren(); pkUnloaded(e); return; }
  say("#pkMsg", "");
  // the server's list, whatever this browser can do; Add where it can make one, else why not
  const usable = passkeysWork() && !st.problem;
  // what's in the way, to act on, stays in full (#280), its addresses links to this page there (#540: as words, no path)
  $("#pkWhy").replaceChildren(...(usable ? "" : st.problem || pkCant())
    .split(/(https?:\/\/[\w.:[\]-]*[\w\]])/).map((t, i) => i % 2 ? el("a", {href: t + location.pathname, text: t}) : t));
  $("#pkWhy").hidden = usable;
  try { pkList(st); } catch (e) { pkUnloaded(e); }
  if (!usable) $("#pkBody").replaceChildren();
  else pkAdd();
  if (usable && st.user_handle)   // the device can forget passkeys removed here (or on another device)
    passkeySignalAccepted(st.site, st.user_handle, st.passkeys.filter(k => k.here).map(k => k.credential_id));
}
/* why this browser can't make one here when the server sees nothing in the way: a page that isn't secure, else a browser
   without passkeys (#1117: it said to open the https:// address, the one it was at) */
const pkCant = () => !window.isSecureContext ? "This browser can't make passkeys here: open Sanguity at its https:// address."
  : "This browser can't make passkeys. Add one in a browser or on a device that can, such as Chrome, Safari, Edge or Firefox: it's listed here too.";
/* the list not loaded, or not drawn (#1117: the card was left bare, nothing said): why, and Try again, focus then on
   the list drawn, else the Add form, else the card's heading */
function pkUnloaded(e) {
  console.warn("passkeys not shown:", e);
  const m = el("span", {class: "msg"});
  $("#pkList").replaceChildren(el("p", {class: "row"}, m, el("button", {class: "btn small", type: "button", text: "Try again", onclick: async () => {
    await loadPasskeys();
    focusNear([$("#pkList"), $("#pkBody")], 0, ":is(input, button)", $("#passkeys h2"));
  }})));
  sayIn(m, `Your passkeys couldn't be loaded: ${e.message}`, true);
}
/* the account's passkeys, each with Rename and Remove */
function pkList(st) {
  const last = st.passkeys.length === 1 && st.required && !st.authenticator_app;
  $("#pkList").replaceChildren(st.passkeys.length ? el("table", {class: "tbl pk"},
    el("thead", {}, el("tr", {}, ...["Name", "Added", "Last used", ""].map(thOf))),
    el("tbody", {}, ...st.passkeys.map(k => {
      // Enter in its name renames it, as its Rename does (#524)
      const name = el("input", {value: k.name, maxlength: 60, "aria-label": "Passkey name", enterkeyhint: "done",
        onkeydown: ev => { if (ev.key === "Enter" && !ev.isComposing) { ev.preventDefault(); ev.target.closest("tr").querySelector("td.acts button").click(); } }});
      return el("tr", {"data-pk": k.id},
        el("td", {}, name, k.here ? null : el("div", {class: "muted", text: `works at ${k.rp_id} only`})),
        el("td", {text: when(k.created)}), el("td", {text: k.last_used ? when(k.last_used) : "never"}),
        el("td", {class: "acts"},
          el("button", {class: "btn small", type: "button", text: "Rename", onclick: async () => {
            pkSay(null);
            // its new name the one Remove asks about (#524: it named the old one)
            try { k.name = (await call("me/passkeys/" + k.id, "PATCH", {name: name.value})).name; name.value = k.name; pkSay(k.id, "Renamed."); }
            catch (e) { e.status === 404 ? pkGone(st.passkeys.indexOf(k), e.message) : pkSay(k.id, e.message, true); } }}),
          el("button", {class: "btn small", type: "button", text: "Remove", disabled: last || null,
            title: last ? "This server requires two-factor sign-in, and this is your only second step" : null, onclick: async () => {
              // your password, as adding one asks (#590: a device left signed in could take it away); every other
              // device is signed out, as turning off the authenticator app does
              const pw = el("input", {type: "password", autocomplete: "current-password", required: true});
              const label = el("label", {}, "Your password", pw), feeds = await feedsNow();
              pwField(pw);
              if (!await ask({title: `Remove the passkey “${k.name}”?`, message: ["Remove it from the device too, in its passwords or passkeys settings.",
                                "Every other device signed in to your account is signed out." + (feeds ? " " + feedsEnd(feeds) : ""), pwOff() ? null : label],
                              action: "Remove passkey", danger: true, field: pwOff() ? null : pw})) return;
              const at = st.passkeys.indexOf(k);
              try {
                const r = await proved(() => call("me/passkeys/" + k.id, "DELETE", {password: pw.value}), $("#passkeys"));
                if (r.must_change_pw) { location.replace(location.pathname + location.search + "#password"); location.reload(); return; }
                await Promise.all([loadPasskeys(), loadSessions()]);
                if (r.signed_out_elsewhere || r.calendar_feeds)   // and the calendar feeds that went with it (#597)
                  say("#pkMsg", "Removed." + (r.signed_out_elsewhere ? ` Signed out ${r.signed_out_elsewhere} other device${r.signed_out_elsewhere === 1 ? "" : "s"}.` : "") +
                    feedsGone(r.calendar_feeds));
                if (r.calendar_feeds) { ME.calendar_feeds = 0; pwFeeds(); }
              } catch (e) { e.status === 404 ? pkGone(at, e.message) : say("#pkMsg", e.message, true); return; }
              pkFocus(at); }})));
    }))) : emptyState("No passkeys yet: each one you add is listed here, with when it was last used.", null, {cls: ""}));   // (#494)
  if (last) $("#pkList").append(el("p", {class: "hint", text: "This server requires two-factor sign-in, so your only passkey can't be removed until you add another or turn on the authenticator app."}));
}
/* Add a passkey: its name, your password */
function pkAdd() {
  const pw = el("input", {type: "password", autocomplete: "current-password", placeholder: "Your password", "aria-label": "Your password", required: true});
  const nm = el("input", {value: guessName(), maxlength: 60, placeholder: "Name, e.g. My phone", "aria-label": "Passkey name"});
  $("#pkBody").replaceChildren(el("form", {class: "row", onsubmit: async ev => { ev.preventDefault();
    pkSay(null); pwSent(ev.target);   // a row's last reply gone too (#524: two at once)
    try {
      const o = await proved(() => call("me/passkeys/options", "POST", {password: pw.value}), $("#passkeys"));
      let credential;
      try { credential = await passkeyCreate(o.options); } catch (e) { throw new Error(passkeyProblem(e)); }
      const r = await call("me/passkeys", "POST", {state: o.state, credential, name: nm.value});
      if (ME.user.needs_two_factor) { location.replace("./"); return; }
      await loadPasskeys(); pkSay(r.id, `Added “${r.name}”.`, false, true); loadTwoFactor(); adminAgain("#pkRowMsg");
    } catch (e) { say("#pkMsg", e.message, true); } }},
    nm, pwOff() ? null : pwField(pw).box, el("button", {class: "btn primary small", type: "submit", text: "Add a passkey"})));   // with Show (#492)
}
/* a passkey's reply (#519: Added and Renamed were under the Add form, out of sight), as Admin's rows' (rowSay): under its
   name until the next one (id null: just that), the row in view; focus: on its Rename, as it's added (the form that had
   focus was drawn again) */
function pkSay(id, text, bad = false, focus = false) {
  for (const m of document.querySelectorAll("#pkList .row-msg")) m.remove();
  say("#pkMsg", "");
  const tr = id != null && $(`#pkList tr[data-pk="${id}"]`);
  if (!tr) return text && say("#pkMsg", text, bad);
  const m = el("p", {class: "msg row-msg", id: "pkRowMsg"});
  tr.cells[0].append(m);
  sayIn(m, text, bad);
  if (focus) tr.querySelector("td.acts button").focus({preventScroll: true});
  tr.scrollIntoView({block: "nearest"});
}
/* its button gone with the passkey removed (#502), as Sign out's (sessFocus): focus on the next passkey's Remove, else
   the one before's, else (none left, or the last can't be removed) on the card's heading */
const pkFocus = at => focusNear([...$("#pkList").querySelectorAll("tbody tr")], at, "td.acts button:last-child", $("#passkeys h2"));
/* removed elsewhere (#524: its row stayed, "No such passkey." under the Add form, out of sight): the list drawn again,
   what the server said where it was (util.js goneSay), focus as after a Remove */
async function pkGone(at, text) {
  await loadPasskeys();
  goneSay([...$("#pkList").querySelectorAll("tbody tr")], at, text, "td.acts button:last-child", $("#passkeys h2"), $("#pkList"));
}

/* ---------------------------------------------------------------- sign-in provider (#1109) */
/* this account's identity at the install's provider (routes_oidc.py): linked by signing in there (after the fresh
   check, in the card: admin.js fresh), which goes to the provider and back through the sign-in page (login.js, which
   leaves what happened in OIDC_SAID for this card to say); unlinked after the fresh check, which signs out every other
   device. Hidden while the install has no provider. */
const OIDC_SAID = "sgOidcSaid", OIDC_LINK = "sgOidcLink";
async function loadOidc() {
  const card = $("#oidc"), p = ME.sign_in_provider;
  if (!p) { card.hidden = true; return; }
  let st;
  try { st = await call("me/oidc"); } catch (e) { say("#oidcMsg", e.message, true); return; }
  card.hidden = false;
  const label = (st.provider || p).label, mine = st.identities.filter(i => i.current);
  $("#oidcHint").textContent = `Sign in with ${label} too, once your identity there is linked to this account.` +
    ((st.provider || p).only ? ` This server signs in with ${label} only.` : "");
  // the password first, as adding a passkey asks it (#1109's review: someone at a signed-in browser for a few minutes
  // could link an identity of their own); with OIDC only on, the fresh check instead (proved)
  const pw = el("input", {type: "password", autocomplete: "current-password", placeholder: "Your password", "aria-label": "Your password", required: true});
  const link = el("form", {class: "row", onsubmit: async ev => {
    ev.preventDefault(); say("#oidcMsg", ""); pwSent(ev.target);
    try {
      const r = await proved(() => call("me/oidc/start", "POST", {password: pw.value}), $("#oidc"));
      sessionStorage.setItem(OIDC_LINK, "1");
      location.assign(r.url);
    } catch (e) { say("#oidcMsg", e.message, true); }
  }}, pwOff() ? null : pwField(pw).box, el("button", {class: "btn small", type: "submit", id: "oidcLinkBtn", text: `Link your ${label} identity`}));
  $("#oidcList").replaceChildren(...present(mine.length ? el("table", {class: "tbl pk"},
    el("thead", {}, el("tr", {}, ...["Identity", "Linked", "Last used", ""].map(thOf))),
    el("tbody", {}, ...mine.map(i => el("tr", {"data-oidc": i.id},
      el("td", {}, i.name || i.subject, i.name ? el("div", {class: "muted", text: `ID ${i.subject}`}) : null,
        // an admin's link (by its subject): said to its person, a warning until it's first used (#1109's review)
        i.linked_by_admin ? el("div", {class: i.last_used ? "muted" : "warn", text: i.last_used ? `Linked for you by ${i.linked_by || "an admin"}.` :
          `Linked for you by ${i.linked_by || "an admin"}, and not used yet. If you don't know it, unlink it and tell them.`}) : null),
      el("td", {text: when(i.created)}), el("td", {text: i.last_used ? when(i.last_used) : "never"}),
      el("td", {class: "acts"}, el("button", {class: "btn small", type: "button", text: "Unlink", onclick: () => oidcUnlink(i, label)})))))) :
    emptyState(`Not linked: link it to sign in with ${label}.`, null, {cls: ""}), mine.length ? null : link));
  const said = sessionStorage.getItem(OIDC_SAID);   // from the sign-in page, back from the provider
  if (said && JSON.parse(said).where === "oidc") {
    sessionStorage.removeItem(OIDC_SAID);
    const {text, bad} = JSON.parse(said);
    say("#oidcMsg", text, bad);
    $("#oidcHd").focus();
  }
}
async function oidcUnlink(i, label) {
  say("#oidcMsg", "");
  if (!await ask({title: `Unlink “${i.name || i.subject}”?`, danger: true, action: "Unlink",
      message: [`It no longer signs in to this account with ${label}.`, "Every other device signed in to your account is signed out."]})) return;
  try {
    const r = await fresh(() => call("me/oidc/" + i.id, "DELETE"), $("#oidcReauth"));
    await Promise.all([loadOidc(), loadSessions()]);
    say("#oidcMsg", "Unlinked." + (r.signed_out_elsewhere ? ` Signed out ${r.signed_out_elsewhere} other device${r.signed_out_elsewhere === 1 ? "" : "s"}.` : ""));
    $("#oidcLinkBtn")?.focus();
  } catch (e) { say("#oidcMsg", e.message, true); }
}

/* ---------------------------------------------------------------- start (settings-nav.js) */
/* a one-time password to replace, or the second factor the server requires to set up: Sign-in and security alone,
   the top bar's menus put away (nav.js lock) and nothing else asked of the server, which refuses it until then.
   -> true if so (settings-nav.js then hides the sidebar, listing that group alone) */
function acctLock() {
  $("#pwHint").textContent = `At least ${ME.password_min} characters. A few unrelated words make a strong password you can remember.`;
  pwFeeds();   // (#597)
  if (ME.user.must_change_pw) {   // nothing else is available until the one-time password is replaced
    $("#mustChange").hidden = $("#password").hidden = false; SgNav.lock();
    $("#pwCur").focus();
    return true;
  }
  if (ME.user.needs_two_factor) {   // the server requires it: set it up before anything else
    $("#needTf").hidden = false; SgNav.lock();
    for (const id of ["#password", "#twofa", "#passkeys"]) $(id).hidden = false;
    Promise.all([loadTwoFactor(), loadPasskeys()]).then(() => SET_NAV.again());
    return true;
  }
  return false;
}
/* the rest: shown once first (this person's settings drawn) is done, so the sidebar's first group is theirs from the
   start; then the cards' content, and an address naming one of them lands on it */
async function acctStart(first) {
  const loads = Promise.all([loadSessions(), loadTwoFactor(), loadPasskeys()]);
  await first;
  for (const g of SG_PAGES.settings.groups) if (g.part === "You" && g.id !== "display") for (const id of g.has) $("#" + id).hidden = false;
  $("#oidc").hidden = true;   // (#1109: shown by loadOidc, where the install has a provider)
  $("#notify").hidden = true;   // (#1130: shown by loadNotify, once the install has a notification server)
  await Promise.all([loads, loadOidc(), loadNotify()]);
  const said = sessionStorage.getItem(OIDC_SAID);   // (#1109) back from the provider's fresh check (login.js): said once
  if (said) { sessionStorage.removeItem(OIDC_SAID); toast(JSON.parse(said).text); }
  SET_NAV.again();   // the cards have their content now, so a section link lands where it should
}
