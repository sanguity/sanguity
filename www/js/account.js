/* Sanguity · account page: you and this device (#169). Two groups in a sidebar: Sign-in and security (password,
   two-factor sign-in, passkeys, where you're signed in) and This device (installing Sanguity, offline records; their
   cards are pwa.js's). Profiles, with their sharing and activity, are on the Sharing page (sharing.js, #168); the
   server's administration is on the Admin page (admin.js, #167); how things look is on Settings. */
"use strict";   // $, el and api come from util.js
/* api() from util.js, returning the JSON answer, or throwing with the server's message */
async function call(path, method = "GET", body) {
  const r = await api(path, {method, body: body ? JSON.stringify(body) : undefined});
  if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = j.detail, e = new Error(Array.isArray(d) ? "Please check the fields." : (d && d.message) || d || r.status + " " + r.statusText);
    e.detail = d; throw e;
  }
  return j;
}
const say = (id, text, bad = false) => sayIn($(id), text, bad);   // shown and said (util.js, #209)
const when = t => fmtDT(t * 1000);   // dates.js: this person's time zone and date format
const device = ua => {
  if (!ua) return "Unknown device";
  const b = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "Browser";
  const os = /iPhone|iPad/.test(ua) ? "iOS" : /Android/.test(ua) ? "Android" : /Windows/.test(ua) ? "Windows" : /Mac OS/.test(ua) ? "macOS" : /Linux/.test(ua) ? "Linux" : "";
  return os ? `${b} on ${os}` : b;
};
let ME;

$("#logout").onclick = logOut;   // util.js: asks first about what isn't saved (#391)

/* ---------------------------------------------------------------- password */
/* each with Show (util.js pwField, #492), hidden again only as it's sent: not over a mismatch */
pwField($("#pwCur"), {name: "current password"});
pwField($("#pwNew"), {name: "new password"});
pwField($("#pwNew2"), {name: "repeated password"});
$("#pwForm").onsubmit = async ev => {
  ev.preventDefault();
  if ($("#pwNew").value !== $("#pwNew2").value) return say("#pwMsg", "The new passwords don't match.", true);
  pwSent(ev.target);
  try {
    const r = await call("me/password", "POST", {current: $("#pwCur").value, new: $("#pwNew").value});
    $("#pwForm").reset();
    if (ME.user.must_change_pw) { location.replace("./"); return; }
    say("#pwMsg", "Password changed." + (r.signed_out_elsewhere ? ` Signed out ${r.signed_out_elsewhere} other device${r.signed_out_elsewhere > 1 ? "s" : ""}.` : ""));
    loadSessions();
  } catch (e) { say("#pwMsg", e.message, true); }
};

/* ---------------------------------------------------------------- sessions */
async function loadSessions() {
  const list = await call("sessions");
  $("#sessList").replaceChildren(el("table", {class: "tbl"},
    el("thead", {}, el("tr", {}, ...["Device", "Address", "Signed in", "Last active", ""].map(h => el("th", {text: h})))),
    el("tbody", {}, ...list.map(s => el("tr", {},
      el("td", {}, device(s.agent), s.current ? el("span", {class: "tag", text: "this device"}) : null),
      el("td", {text: s.ip || "–"}), el("td", {text: when(s.created)}), el("td", {text: when(s.last_seen)}),
      el("td", {}, s.current ? null : el("button", {class: "btn small", type: "button", text: "Sign out",
        onclick: async () => {
          const at = list.indexOf(s);
          try { await call("sessions/" + s.id, "DELETE"); await loadSessions(); } catch (e) { say("#sessMsg", e.message, true); return; }
          sessFocus(at);
        }})))))));
  $("#sessOthers").disabled = list.length < 2;
}
/* its button gone with the device signed out (#493): focus on the next device's Sign out, else the one before's, else
   (none left but this one) on the card's heading (util.js) */
const sessFocus = (at = 0) => focusNear([...$("#sessList tbody").rows], at, "button", $("#sessions h2"));
$("#sessOthers").onclick = async () => {
  try { const r = await call("sessions/revoke-others", "POST"); say("#sessMsg", `Signed out ${r.revoked} device${r.revoked === 1 ? "" : "s"}.`); await loadSessions(); sessFocus(); }
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
    body.replaceChildren(el("button", {class: "btn primary", type: "button", text: "Turn on", onclick: startTwoFactor}));
    if (focus) tfFocus(body.firstChild);
    return;
  }
  tfHint(`On. You have ${st.recovery_codes_left} recovery code${st.recovery_codes_left === 1 ? "" : "s"} left` +
    (st.recovery_codes_left <= 3 ? ": make new ones soon." : "."));
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
        const r = await call("me/2fa/off", "POST", {password: pw.value});
        if (r.must_change_pw) { location.replace("account.html#password"); location.reload(); return; }
        loadTwoFactor(true); loadPasskeys(); loadSessions();
      } catch (e) { say("#tfMsg", e.message, true); } }},
      pwField(pw).box, el("button", {class: "btn small", type: "submit", text: "Turn off"})));   // with Show (#492)
  if (focus) tfFocus($("#twofa h2"));
}
async function startTwoFactor() {
  let s;
  try { s = await call("me/2fa/start", "POST"); } catch (e) { return say("#tfMsg", e.message, true); }
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
      try { showRecoveryCodes((await call("me/2fa/confirm", "POST", {code: code.value})).recovery_codes, true); } catch (e) { say("#tfMsg", e.message, true); } }},
      code, el("button", {class: "btn primary small", type: "submit", text: "Confirm"}),
      el("button", {class: "btn small", type: "button", text: "Cancel", onclick: () => loadTwoFactor(true)})));
  code.focus();
}
function showRecoveryCodes(codes, justTurnedOn) {
  const text = "Sanguity recovery codes for " + ME.user.username + "\nEach works once, in place of a code from your app.\n\n" + codes.join("\n") + "\n";
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

/* after a second factor is added: who's signed in, again; an admin who needed one for the Admin page (once the
   server has more than one account) and now has one is told Admin is open again */
async function adminAgain(m) {
  const had = ME.user.admin_needs_two_factor;
  ME = await call("me");
  if (had && !ME.user.admin_needs_two_factor) $(m).append(" Admin is open to you again: ", el("a", {href: "admin.html", text: "go to Admin"}), ".");
}

/* ---------------------------------------------------------------- passkeys */
const guessName = () => { const d = device(navigator.userAgent); return d === "Browser" ? "Passkey" : d.replace(/^\w+ on /, ""); };
async function loadPasskeys() {
  const st = await call("me/passkeys?origin=" + encodeURIComponent(location.origin));
  say("#pkMsg", "");
  const usable = passkeysWork() && !st.problem;
  $("#pkHint").textContent = "Face ID, Touch ID, Windows Hello, your phone or a security key, on its own or after your password.";
  // what's in the way, to act on, stays in full (#280)
  $("#pkWhy").textContent = usable ? "" : st.problem || "This browser can't make passkeys here: open Sanguity at its https:// address.";
  $("#pkWhy").hidden = usable;
  if (usable && st.user_handle)   // the device can forget passkeys removed here (or on another device)
    passkeySignalAccepted(st.site, st.user_handle, st.passkeys.filter(k => k.here).map(k => k.credential_id));
  const last = st.passkeys.length === 1 && st.required && !st.authenticator_app;
  $("#pkList").replaceChildren(st.passkeys.length ? el("table", {class: "tbl"},
    el("thead", {}, el("tr", {}, ...["Name", "Added", "Last used", ""].map(h => el("th", {text: h})))),
    el("tbody", {}, ...st.passkeys.map(k => {
      const name = el("input", {value: k.name, maxlength: 60, "aria-label": "Passkey name"});
      return el("tr", {},
        el("td", {}, name, k.here ? null : el("div", {class: "muted", text: `works at ${k.rp_id} only`})),
        el("td", {text: when(k.created)}), el("td", {text: k.last_used ? when(k.last_used) : "never"}),
        el("td", {class: "acts"},
          el("button", {class: "btn small", type: "button", text: "Rename", onclick: async () => {
            try { await call("me/passkeys/" + k.id, "PATCH", {name: name.value}); say("#pkMsg", "Renamed."); } catch (e) { say("#pkMsg", e.message, true); } }}),
          el("button", {class: "btn small", type: "button", text: "Remove", disabled: last || null,
            title: last ? "This server requires two-factor sign-in, and this is your only second step" : null, onclick: async () => {
              if (!await ask({title: `Remove the passkey “${k.name}”?`, message: "Remove it from the device too, in its passwords or passkeys settings.",
                              action: "Remove passkey", danger: true})) return;
              try {
                const r = await call("me/passkeys/" + k.id, "DELETE");
                if (r.must_change_pw) { location.replace("account.html#password"); location.reload(); return; }
                loadPasskeys();
              } catch (e) { say("#pkMsg", e.message, true); } }})));
    }))) : emptyState("No passkeys yet: each one you add is listed here, with when it was last used.", null, {cls: ""}));   // (#494)
  if (last) $("#pkList").append(el("p", {class: "hint", text: "This server requires two-factor sign-in, so your only passkey can't be removed until you add another or turn on the authenticator app."}));
  if (!usable) { $("#pkBody").replaceChildren(); return; }
  const pw = el("input", {type: "password", autocomplete: "current-password", placeholder: "Your password", "aria-label": "Your password", required: true});
  const nm = el("input", {value: guessName(), maxlength: 60, placeholder: "Name, e.g. My phone", "aria-label": "Passkey name"});
  $("#pkBody").replaceChildren(el("form", {class: "row", onsubmit: async ev => { ev.preventDefault();
    say("#pkMsg", ""); pwSent(ev.target);
    try {
      const o = await call("me/passkeys/options", "POST", {password: pw.value});
      let credential;
      try { credential = await passkeyCreate(o.options); } catch (e) { throw new Error(passkeyProblem(e)); }
      const r = await call("me/passkeys", "POST", {state: o.state, credential, name: nm.value});
      if (ME.user.needs_two_factor) { location.replace("./"); return; }
      await loadPasskeys(); say("#pkMsg", `Added “${r.name}”.`); loadTwoFactor(); adminAgain("#pkMsg");
    } catch (e) { say("#pkMsg", e.message, true); } }},
    nm, pwField(pw).box, el("button", {class: "btn primary small", type: "submit", text: "Add a passkey"})));   // with Show (#492)
}

/* ---------------------------------------------------------------- the sidebar and the address */
/* The sidebar every account page has (sidebar.js, #266): one group shown at a time, the address following it
   (account.html#device). An address naming a section (#password, #twofa, #passkeys, #sessions, #installApp,
   #offlineRecords) opens its group and scrolls to it; #install and #offline are short for the last two. The old
   Profiles and Admin tabs' addresses (#profiles; #admin, #users, #labmaps) open Sharing and Admin (moved.js). */
const GROUPS = SG_PAGES.account.groups;   // groups.js: one list, which the command palette searches too (#273)
let locked = false;   // until the one-time password is replaced, or the second factor the server requires is set up: that alone
const AcctNav = sideNav({id: "acctNav", page: "Account", groups: GROUPS, find: "Find in Account", none: q => `Nothing in Account matches “${q}”.`,
  allowed: g => !locked || g === GROUPS[0], alias: {install: "installApp", offline: "offlineRecords"},
  intro: g => { $("#introHint").textContent = g ? g.hint : ""; }});
const lockNav = () => { locked = true; AcctNav.hide(); AcctNav.follow(); };

/* ---------------------------------------------------------------- start */
(async () => {
  if (sgMoved()) return;   // the old Profiles and Admin tabs' addresses (moved.js)
  AcctNav.start();
  try { ME = await call("me"); } catch (e) { return; }
  await datesReady();   // dates in this person's time zone and format (dates.js)
  $("#who").textContent = ME.user.username;
  { const w = plainHttpWarning(); if (w) { $("#insecure").textContent = w; $("#insecure").hidden = false; } }
  $("#pwHint").textContent = `At least ${ME.password_min} characters. A few unrelated words make a strong password you can remember.`;
  if (ME.user.must_change_pw) {   // nothing else is available until the one-time password is replaced
    $("#mustChange").hidden = false; SgNav.lock(); lockNav();
    for (const id of ["#sessions", "#twofa", "#passkeys"]) $(id).hidden = true;
    $("#pwCur").focus();
    return;
  }
  if (ME.user.needs_two_factor) {   // the server requires it: set it up before anything else
    $("#needTf").hidden = false; SgNav.lock(); lockNav();
    $("#sessions").hidden = true;
    await Promise.all([loadTwoFactor(), loadPasskeys()]);
    return;
  }
  await Promise.all([loadSessions(), loadTwoFactor(), loadPasskeys()]);
  AcctNav.again();   // the cards have their content now, so a section link lands where it should
})();
