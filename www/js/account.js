/* Sanguity · account page: password, two-factor sign-in, passkeys, signed-in devices, this device, and profiles with
   their sharing and activity (activity.js). The server's administration is on the Admin page (admin.js, #167). */
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
const say = (id, text, bad = false) => { const m = $(id); m.textContent = text; m.classList.toggle("bad", bad); };
const when = t => fmtDT(t * 1000);   // dates.js: this person's time zone and date format
const device = ua => {
  if (!ua) return "Unknown device";
  const b = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "Browser";
  const os = /iPhone|iPad/.test(ua) ? "iOS" : /Android/.test(ua) ? "Android" : /Windows/.test(ua) ? "Windows" : /Mac OS/.test(ua) ? "macOS" : /Linux/.test(ua) ? "Linux" : "";
  return os ? `${b} on ${os}` : b;
};
let ME;

$("#logout").onclick = async () => { try { await call("logout", "POST"); } catch (e) {} location.replace("login.html"); };

/* ---------------------------------------------------------------- password */
$("#pwForm").onsubmit = async ev => {
  ev.preventDefault();
  if ($("#pwNew").value !== $("#pwNew2").value) return say("#pwMsg", "The new passwords don't match.", true);
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
        onclick: async () => { try { await call("sessions/" + s.id, "DELETE"); loadSessions(); } catch (e) { say("#sessMsg", e.message, true); } }})))))));
  $("#sessOthers").disabled = list.length < 2;
}
$("#sessOthers").onclick = async () => {
  try { const r = await call("sessions/revoke-others", "POST"); say("#sessMsg", `Signed out ${r.revoked} device${r.revoked === 1 ? "" : "s"}.`); loadSessions(); }
  catch (e) { say("#sessMsg", e.message, true); }
};

/* ---------------------------------------------------------------- profiles and sharing */
const ROLES = ["owner", "editor", "viewer"];
function roleSelect(value, onchange) {
  const s = el("select", {"aria-label": "Role", onchange}, ...ROLES.map(r => el("option", {value: r, text: r})));
  s.value = value; return s;
}
async function loadProfiles() {
  ME = await call("me");
  const box = $("#profList");
  if (!ME.profiles.length) { box.replaceChildren(el("p", {class: "hint", text: "No profiles are shared with you yet."})); return; }
  const cards = [];
  for (const p of ME.profiles) {
    const msg = el("span", {class: "msg", role: "status"});
    const fail = e => { msg.textContent = e.message; msg.classList.add("bad"); };
    const head = el("div", {class: "prof-hd"}, el("b", {text: p.name}), el("span", {class: "tag", text: p.role}),
      el("span", {class: "muted", text: p.members > 1 ? `shared with ${p.members - 1} other${p.members > 2 ? "s" : ""}` : "only you"}));
    const body = [head];
    if (p.role !== "viewer")   // owners and editors can take everything with them
      body.push(el("div", {class: "row"},
        el("a", {class: "btn small", href: `api/profiles/${p.id}/export`, download: "", text: "Export everything"}),
        el("span", {class: "muted", text: "a zip of the records, documents, settings and activity; keep it private"})));
    if (p.role === "owner") {
      const name = el("input", {value: p.name, maxlength: 80, "aria-label": "Profile name"});
      body.push(el("div", {class: "row"}, name, el("button", {class: "btn small", type: "button", text: "Rename",
        onclick: async () => { try { await call("profiles/" + p.id, "PATCH", {name: name.value}); loadProfiles(); } catch (e) { fail(e); } }})));
      const members = await call(`profiles/${p.id}/members`);
      body.push(el("table", {class: "tbl"}, el("tbody", {}, ...members.map(m => el("tr", {},
        el("td", {}, m.username, m.user_id === ME.user.id ? el("span", {class: "muted", text: " (you)"}) : null),
        el("td", {}, roleSelect(m.role, async ev => { try { await call(`profiles/${p.id}/members`, "PUT", {username: m.username, role: ev.target.value}); loadProfiles(); } catch (e) { ev.target.value = m.role; fail(e); } })),
        el("td", {}, el("button", {class: "btn small", type: "button", text: m.user_id === ME.user.id ? "Leave" : "Remove",
          onclick: async () => { if (!confirm(m.user_id === ME.user.id ? `Stop having access to ${p.name}?` : `Remove ${m.username}'s access to ${p.name}?`)) return;
            try { await call(`profiles/${p.id}/members/${m.user_id}`, "DELETE"); loadProfiles(); } catch (e) { fail(e); } }})))))));
      const who = el("input", {placeholder: "username", autocapitalize: "none", spellcheck: "false", "aria-label": "Share with username", required: true});
      const role = roleSelect("viewer");
      body.push(el("form", {class: "row", onsubmit: async ev => { ev.preventDefault();
        // sharing with someone who already has access would quietly change their role: say so first
        const name = who.value.trim(), had = members.find(m => m.username.toLowerCase() === name.toLowerCase());
        if (had && had.role === role.value) { fail(new Error(`${had.username} already has access as ${had.role}.`)); return; }
        if (had && !confirm(`${had.username} already has access as ${had.role}. Change them to ${role.value}?`)) return;
        try { await call(`profiles/${p.id}/members`, "PUT", {username: name, role: role.value}); loadProfiles(); } catch (e) { fail(e); } }},
        el("span", {class: "muted", text: "Share with"}), who, "as", role, el("button", {class: "btn small", type: "submit", text: "Share"})));
      body.push(activityPanel(`profiles/${p.id}/activity`, members));
      body.push(shareLinksPanel(p));   // share links (#47), js/share-links.js
      body.push(deletePanel(p, fail));
    } else {
      body.push(el("div", {class: "row"}, el("button", {class: "btn small", type: "button", text: "Leave this profile",
        onclick: async () => { if (!confirm(`Stop having access to ${p.name}? An owner can share it with you again.`)) return;
          try { await call(`profiles/${p.id}/members/${ME.user.id}`, "DELETE"); loadProfiles(); } catch (e) { fail(e); } }})));
    }
    body.push(msg);
    cards.push(el("div", {class: "prof"}, ...body));
  }
  box.replaceChildren(...cards);
}
$("#profNew").onsubmit = async ev => {
  ev.preventDefault();
  try { await call("profiles", "POST", {name: $("#profName").value}); $("#profNew").reset(); say("#profMsg", "Created."); loadProfiles(); }
  catch (e) { say("#profMsg", e.message, true); }
};

/* ---------------------------------------------------------------- two-factor sign-in */
const APPS = "an authenticator app such as Google Authenticator, Microsoft Authenticator, 1Password or Bitwarden";
async function loadTwoFactor() {
  const st = await call("me/2fa"), body = $("#tfBody");
  say("#tfMsg", "");
  if (!st.on) {
    $("#tfHint").textContent = `Ask for a code from ${APPS} as well as your password, so a stolen password alone can't ` +
      "open your records." + (st.required ? " This server requires it." : "");
    body.replaceChildren(el("button", {class: "btn primary", type: "button", text: "Turn on", onclick: startTwoFactor}));
    return;
  }
  $("#tfHint").textContent = `On. You have ${st.recovery_codes_left} recovery code${st.recovery_codes_left === 1 ? "" : "s"} left` +
    (st.recovery_codes_left <= 3 ? ": make new ones soon." : ".");
  const code = el("input", {inputmode: "numeric", autocomplete: "one-time-code", placeholder: "Code from the app", "aria-label": "Code from your authenticator app"});
  const pw = el("input", {type: "password", autocomplete: "current-password", placeholder: "Your password", "aria-label": "Your password"});
  body.replaceChildren(
    el("form", {class: "row", onsubmit: async ev => { ev.preventDefault();
      try { showRecoveryCodes((await call("me/2fa/recovery-codes", "POST", {code: code.value})).recovery_codes); } catch (e) { say("#tfMsg", e.message, true); } }},
      code, el("button", {class: "btn small", type: "submit", text: "Make new recovery codes"})),
    st.required && !st.passkeys ? el("p", {class: "hint", text: "This server requires two-factor sign-in, so it can't be turned off. Add a passkey first to stop using the app."}) :
    el("form", {class: "row", onsubmit: async ev => { ev.preventDefault();
      if (!confirm("Turn off two-factor sign-in? Every other device will be signed out.")) return;
      try {
        const r = await call("me/2fa/off", "POST", {password: pw.value});
        if (r.must_change_pw) { location.replace("account.html#password"); location.reload(); return; }
        loadTwoFactor(); loadPasskeys(); loadSessions();
      } catch (e) { say("#tfMsg", e.message, true); } }},
      pw, el("button", {class: "btn small", type: "submit", text: "Turn off"})));
}
async function startTwoFactor() {
  let s;
  try { s = await call("me/2fa/start", "POST"); } catch (e) { return say("#tfMsg", e.message, true); }
  const code = el("input", {inputmode: "numeric", autocomplete: "one-time-code", placeholder: "123 456", "aria-label": "Code from your authenticator app", required: true});
  $("#tfHint").textContent = `Scan this with ${APPS}, then enter the code it shows. If you can't scan, type the key instead.`;
  $("#tfBody").replaceChildren(
    el("div", {class: "tf-setup"},
      el("img", {src: s.qr, alt: "QR code for your authenticator app", width: 180, height: 180}),
      el("div", {}, el("div", {class: "muted", text: "Key"}), el("code", {class: "tf-key", text: s.secret.match(/.{1,4}/g).join(" ")}))),
    el("form", {class: "row", onsubmit: async ev => { ev.preventDefault();
      try { showRecoveryCodes((await call("me/2fa/confirm", "POST", {code: code.value})).recovery_codes, true); } catch (e) { say("#tfMsg", e.message, true); } }},
      code, el("button", {class: "btn primary small", type: "submit", text: "Confirm"}),
      el("button", {class: "btn small", type: "button", text: "Cancel", onclick: loadTwoFactor})));
  code.focus();
}
function showRecoveryCodes(codes, justTurnedOn) {
  const text = "Sanguity recovery codes for " + ME.user.username + "\nEach works once, in place of a code from your app.\n\n" + codes.join("\n") + "\n";
  $("#tfHint").textContent = (justTurnedOn ? "Two-factor sign-in is on. " : "") +
    "Save these recovery codes somewhere safe, away from your phone. Each one signs you in once if you lose your phone. " +
    "They won't be shown again.";
  const done = el("button", {class: "btn primary small", type: "button", text: "I've saved them", onclick: async () => {
    if (ME.user.needs_two_factor) { location.replace("./"); return; }
    await Promise.all([loadTwoFactor(), loadPasskeys(), loadSessions()]);
    adminAgain("#tfMsg"); }});
  $("#tfBody").replaceChildren(
    el("pre", {class: "tf-codes", text: codes.join("\n")}),
    el("div", {class: "row"},
      el("a", {class: "btn small", download: "sanguity-recovery-codes.txt", href: "data:text/plain;charset=utf-8," + encodeURIComponent(text), text: "Download"}),
      done));
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
  $("#pkHint").textContent = "Sign in with Face ID, Touch ID, Windows Hello, your phone or a security key: on its own, " +
    "or as the step after your password. A passkey can't be phished or guessed, and your password still works too." +
    (usable ? "" : " " + (st.problem || "This browser can't make passkeys here: open Sanguity at its https:// address."));
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
              if (!confirm(`Remove the passkey “${k.name}”? Remove it from the device too (in its passwords or passkeys settings).`)) return;
              try {
                const r = await call("me/passkeys/" + k.id, "DELETE");
                if (r.must_change_pw) { location.replace("account.html#password"); location.reload(); return; }
                loadPasskeys();
              } catch (e) { say("#pkMsg", e.message, true); } }})));
    }))) : el("p", {class: "hint", text: "No passkeys yet."}));
  if (last) $("#pkList").append(el("p", {class: "hint", text: "This server requires two-factor sign-in, so your only passkey can't be removed until you add another or turn on the authenticator app."}));
  if (!usable) { $("#pkBody").replaceChildren(); return; }
  const pw = el("input", {type: "password", autocomplete: "current-password", placeholder: "Your password", "aria-label": "Your password", required: true});
  const nm = el("input", {value: guessName(), maxlength: 60, placeholder: "Name, e.g. My phone", "aria-label": "Passkey name"});
  $("#pkBody").replaceChildren(el("form", {class: "row", onsubmit: async ev => { ev.preventDefault();
    say("#pkMsg", "");
    try {
      const o = await call("me/passkeys/options", "POST", {password: pw.value});
      let credential;
      try { credential = await passkeyCreate(o.options); } catch (e) { throw new Error(passkeyProblem(e)); }
      const r = await call("me/passkeys", "POST", {state: o.state, credential, name: nm.value});
      if (ME.user.needs_two_factor) { location.replace("./"); return; }
      await loadPasskeys(); say("#pkMsg", `Added “${r.name}”.`); loadTwoFactor(); adminAgain("#pkMsg");
    } catch (e) { say("#pkMsg", e.message, true); } }},
    nm, pw, el("button", {class: "btn primary small", type: "submit", text: "Add a passkey"})));
}

/* ---------------------------------------------------------------- deleting a profile */
function deletePanel(p, fail) {
  const typed = el("input", {autocomplete: "off", spellcheck: "false"});   // no placeholder: it would look filled in
  const go = el("button", {class: "btn small danger", type: "button", text: "Delete it and everything in it", disabled: true});
  typed.oninput = () => { go.disabled = typed.value.trim() !== p.name; };
  go.onclick = async () => {
    go.disabled = true;
    try {
      const r = await call(`profiles/${p.id}`, "DELETE", {confirm_name: typed.value});
      try { if (localStorage.getItem("ht-profile") === String(p.id)) localStorage.removeItem("ht-profile"); } catch (e) {}
      say("#profMsg", `Deleted “${p.name}”.` + (r.unlinked_build ? " Its records came from the setup build, which won't be loaded into a profile again unless an admin asks for it." : ""));
      loadProfiles();
    } catch (e) { fail(e); go.disabled = false; }
  };
  const box = el("div", {class: "danger-zone", hidden: true},
    el("p", {}, "This deletes ", el("b", {text: p.name}), " for everyone it's shared with: its records, documents, settings, ",
      "activity log and sharing. Your original exports and the watch receiver's data stay, and so do backups made before now. ",
      "It can't be undone."),
    el("div", {class: "row"}, el("a", {class: "btn small", href: `api/profiles/${p.id}/export`, download: "", text: "Export everything first"})),
    el("label", {}, `Type ${p.name} to confirm`, typed),
    el("div", {class: "row"}, go));
  const open = el("button", {class: "btn small", type: "button", text: "Delete profile…", "aria-expanded": "false",
    onclick: () => { box.hidden = !box.hidden; open.setAttribute("aria-expanded", !box.hidden); if (!box.hidden) typed.focus(); }});
  return el("div", {class: "activity-wrap"}, open, box);
}

/* ---------------------------------------------------------------- tabs */
/* Sign-in & security, Profiles. The address (#security, #profiles) keeps the tab across a reload, and older links to
   a section (#password, #twofa, #passkeys, #sessions) open the tab that holds it. The Admin tab's old addresses
   (#admin, #users, #labmaps) open the Admin page (#167). */
const TAB_OF = {security: "security", password: "security", twofa: "security", passkeys: "security", sessions: "security",
  profiles: "profiles"};
const TAB_NAMES = ["security", "profiles"];
const MOVED = {"#admin": "admin.html", "#users": "admin.html#users", "#labmaps": "admin.html#labmaps"};
let tabsOn = false;
function showTab(name, {focus = false, remember = true} = {}) {
  if (!TAB_NAMES.includes(name) || $("#tab-" + name).hidden) name = "security";
  for (const t of TAB_NAMES) {
    const on = t === name, btn = $("#tab-" + t);
    btn.setAttribute("aria-selected", on); btn.tabIndex = on ? 0 : -1;
    $("#panel-" + t).hidden = !on;
  }
  if (focus) $("#tab-" + name).focus();
  if (remember && tabsOn && location.hash.slice(1) !== name && TAB_OF[location.hash.slice(1)] !== name)
    history.replaceState(null, "", "#" + name);
  return name;
}
function openFromHash() {
  const key = location.hash.slice(1), tab = showTab(TAB_OF[key] || "security", {remember: false});
  const target = key && key !== tab && document.getElementById(key);
  if (target && !target.hidden) target.scrollIntoView({block: "start"});
}
$("#acctTabs").addEventListener("click", ev => {
  const b = ev.target.closest("[role=tab]");
  if (b) showTab(b.id.slice(4));
});
$("#acctTabs").addEventListener("keydown", ev => {   // arrow keys move between tabs, as for any tab list
  const shown = TAB_NAMES.filter(t => !$("#tab-" + t).hidden), i = shown.indexOf(document.activeElement.id.slice(4));
  if (i < 0) return;
  const next = ev.key === "ArrowRight" ? shown[(i + 1) % shown.length] : ev.key === "ArrowLeft" ? shown[(i + shown.length - 1) % shown.length]
    : ev.key === "Home" ? shown[0] : ev.key === "End" ? shown[shown.length - 1] : null;
  if (next) { ev.preventDefault(); showTab(next, {focus: true}); }
});
addEventListener("hashchange", () => { if (MOVED[location.hash]) location.replace(MOVED[location.hash]); else if (tabsOn) openFromHash(); });

/* ---------------------------------------------------------------- start */
(async () => {
  if (MOVED[location.hash]) { location.replace(MOVED[location.hash]); return; }
  try { ME = await call("me"); } catch (e) { return; }
  await datesReady();   // dates in this person's time zone and format (dates.js)
  $("#who").textContent = ME.user.username;
  { const w = plainHttpWarning(); if (w) { $("#insecure").textContent = w; $("#insecure").hidden = false; } }
  $("#pwHint").textContent = `At least ${ME.password_min} characters. A few unrelated words make a strong password you can remember.`;
  if (ME.user.must_change_pw) {   // nothing else is available until the one-time password is replaced (no tabs)
    $("#mustChange").hidden = false; SgNav.lock();
    for (const id of ["#sessions", "#profiles", "#twofa", "#passkeys"]) $(id).hidden = true;
    $("#pwCur").focus();
    return;
  }
  if (ME.user.needs_two_factor) {   // the server requires it: set it up before anything else (no tabs)
    $("#needTf").hidden = false; SgNav.lock();
    for (const id of ["#sessions", "#profiles"]) $(id).hidden = true;
    await Promise.all([loadTwoFactor(), loadPasskeys()]);
    return;
  }
  $("#acctTabs").hidden = false; tabsOn = true;
  openFromHash();
  await Promise.all([loadSessions(), loadProfiles(), loadTwoFactor(), loadPasskeys()]);
  openFromHash();   // again: the cards above have their content now, so a section link lands where it should
})();
