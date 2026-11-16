/* Sanguity · account page: password, signed-in devices, profiles and sharing, and (for admins) accounts. */
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
const when = t => new Date(t * 1000).toLocaleString(undefined, {year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit"});
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
  const done = el("button", {class: "btn primary small", type: "button", text: "I've saved them", onclick: () => {
    if (ME.user.needs_two_factor) { location.replace("./"); return; }
    if (ME.user.admin_needs_two_factor) { location.reload(); return; }
    loadTwoFactor(); loadPasskeys(); loadSessions(); }});
  $("#tfBody").replaceChildren(
    el("pre", {class: "tf-codes", text: codes.join("\n")}),
    el("div", {class: "row"},
      el("a", {class: "btn small", download: "sanguity-recovery-codes.txt", href: "data:text/plain;charset=utf-8," + encodeURIComponent(text), text: "Download"}),
      done));
}

/* ---------------------------------------------------------------- failed sign-in bursts (admins) */
async function loadNotice() {
  const n = (await call("security/notice")).notice, box = $("#secNotice");
  box.hidden = !n;
  if (!n) return;
  const list = (rows, key) => rows.map(r => `${r[key]} (${r.failures})`).join(", ");
  box.replaceChildren(
    el("b", {text: `${n.total} failed sign-in${n.total === 1 ? "" : "s"} since ${when(n.since)}`}), " (last at ", when(n.last), "). ",
    n.addresses.length ? `From: ${list(n.addresses, "ip")}. ` : "",
    n.accounts.length ? `Accounts tried: ${list(n.accounts, "username")}. ` : "",
    "Throttling slows guessing down; if you don't recognise this, check that the accounts tried have strong passwords " +
    "or two-factor sign-in, and look at the app's log (docker compose logs app) for details. ",
    el("button", {class: "btn small", type: "button", text: "Dismiss", onclick: async () => {
      try { await call("security/notice/dismiss", "POST"); box.hidden = true; } catch (e) { alert(e.message); } }}));
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
      if (ME.user.admin_needs_two_factor) { location.reload(); return; }
      await loadPasskeys(); say("#pkMsg", `Added “${r.name}”.`); loadTwoFactor(); ME = await call("me");
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

/* ---------------------------------------------------------------- activity */
const WHAT = {
  view: () => "opened the records",
  document: d => `opened ${d.name || "a document"}`,
  create: () => "created the profile",
  rename: d => `renamed the profile from “${d.from}” to “${d.to}”`,
  share: d => d.repair ? `made ${d.username} an owner, as an admin (the profile had no active owner)` :
    d.from ? `changed ${d.username}’s role from ${d.from} to ${d.role}` : `shared it with ${d.username} as ${d.role}`,
  unshare: (d, e) => d.username === e.actor ? "left the profile" : `removed ${d.username}’s access`,
  import: d => d.counts ? `imported an upload${d.skipped && (d.skipped.events || d.skipped.results)
    ? `, skipping ${d.skipped.events + d.skipped.results} duplicate${d.skipped.events + d.skipped.results === 1 ? "" : "s"}` : ""}` +
    (d.labs && d.labs.matched ? `, matching ${d.labs.matched} lab name${d.labs.matched === 1 ? "" : "s"} to existing series` : "")
    : `imported records (${d.documents} documents)`,
  reload: () => "loaded the latest records",
  export: () => "exported everything",
  upload: d => `uploaded ${d.files} file${d.files === 1 ? "" : "s"} to import`,
  discard: () => "cancelled an import at its preview",
  undo: d => `undid an import${d.label ? ` (${d.label})` : ""}, removing what it brought`,
  edit: d => (d.undone ? "undid an edit of" : ({hide: "hid", rename: "renamed", date: "changed the date of", not_stay: "marked as not a hospital stay",
    merge: "merged another event into"}[d.action] || "edited")) + ` “${d.title || "an event"}”` + (d.to && !d.undone ? ` to “${d.to}”` : ""),
  setup: () => "set up Sanguity with the first account",
  password: () => "changed their password",
  "account.create": d => `created the account ${d.username}${d.admin ? " (admin)" : ""}`,
  "account.disable": d => `disabled ${d.username}`,
  "account.enable": d => `enabled ${d.username}`,
  "account.admin": d => `made ${d.username} an admin`,
  "account.unadmin": d => `removed ${d.username}’s admin role`,
  "account.reset": d => `reset ${d.username}’s password`,
  "2fa.on": () => "turned on two-factor sign-in",
  "2fa.off": () => "turned off two-factor sign-in",
  "2fa.codes": () => "made new recovery codes",
  "2fa.recovery": d => `signed in with a recovery code (${d.left} left)`,
  "2fa.reset": d => `turned off two-factor sign-in for ${d.username}`,
  "security.notice_dismissed": () => "dismissed the failed sign-ins notice",
  "passkey.add": d => `added the passkey “${d.name}”`,
  "passkey.remove": d => `removed the passkey “${d.name}”`,
  "settings.require_2fa": d => d.on ? "required two-factor sign-in for everyone" : "stopped requiring two-factor sign-in",
  "account.remove": d => `removed the account ${d.username}` +
    (d.handed_over && d.handed_over.length ? `, handing ${d.handed_over.join(", ")} to someone else` : ""),
  "profile.repair": d => `made ${d.username} owner of “${d.name}”, which had no active owner`,
  "profile.delete": d => `deleted the profile “${d.name}”${d.members > 1 ? ` (shared with ${d.members - 1} other${d.members > 2 ? "s" : ""})` : ""}`,
  "labmap.change": d => `changed the lab name match for “${d.name}” from ${d.from ? `“${d.from}”` : "its own series"} to ${d.to ? `“${d.to}”` : "its own series"}`,
  "labmap.remove": d => `removed the lab name match for “${d.name}”${d.target ? ` (to “${d.target}”)` : ""}`,
};
const FILTERS = [["", "Everything"], ["view", "Opened the records"], ["document", "Opened documents"],
  ["share", "Sharing"], ["unshare", "Removed access"], ["rename", "Renames"], ["import", "Imports"], ["reload", "Reloads"],
  ["export", "Exports"], ["upload", "Uploads"], ["discard", "Cancelled imports"], ["undo", "Undone imports"], ["edit", "Edits"]];
const describe = e => (WHAT[e.action] || (() => e.action))(e.detail || {}, e);
function activityRows(list) {
  return list.map(e => el("tr", {},
    el("td", {class: "nowrap", text: when(e.at)}), el("td", {text: e.actor + (e.actor_removed ? " (removed)" : "")}), el("td", {text: describe(e)}),
    el("td", {class: "muted", text: e.ip || ""})));
}
/* a log that opens on demand: newest first, filtered by person (profile logs) and kind, older entries on request */
function activityPanel(path, members) {
  const box = el("div", {class: "activity", hidden: true});
  const who = members ? el("select", {"aria-label": "Person"}, el("option", {value: "", text: "Everyone"}),
    ...members.map(m => el("option", {value: m.user_id, text: m.username}))) : null;
  const what = members ? el("select", {"aria-label": "Kind of activity"}, ...FILTERS.map(([v, t]) => el("option", {value: v, text: t}))) : null;
  const tbody = el("tbody"), more = el("button", {class: "btn small", type: "button", text: "Show older", hidden: true});
  let oldest = null;
  async function load(reset) {
    const q = new URLSearchParams({limit: 50});
    if (who && who.value) q.set("actor", who.value);
    if (what && what.value) q.set("action", what.value);
    if (!reset && oldest) q.set("before", oldest);
    const list = await call(`${path}?${q}`);
    if (reset) tbody.replaceChildren();
    tbody.append(...activityRows(list));
    if (reset && !list.length) tbody.append(el("tr", {}, el("td", {colspan: 4, class: "muted", text: "Nothing yet."})));
    oldest = list.length ? list[list.length - 1].id : oldest;
    more.hidden = list.length < 50;
  }
  for (const s of [who, what]) if (s) s.onchange = () => load(true);
  more.onclick = () => load(false);
  box.append(...(members ? [el("div", {class: "row"}, who, what)] : []),
    el("table", {class: "tbl"}, el("thead", {}, el("tr", {}, ...["When", "Who", "What", "Address"].map(h => el("th", {text: h})))), tbody), more);
  const toggle = el("button", {class: "btn small", type: "button", text: "Activity", "aria-expanded": "false",
    onclick: async () => { box.hidden = !box.hidden; toggle.setAttribute("aria-expanded", !box.hidden); if (!box.hidden) await load(true); }});
  return el("div", {class: "activity-wrap"}, toggle, box);
}

/* ---------------------------------------------------------------- accounts (admin) */
/* admins need a second factor of their own once the server has more than one account: until then, say so */
function adminGate() {
  $("#adminTf").hidden = false; $("#adminTools").hidden = true; $("#users").hidden = false;
}
/* resetting a password, turning off someone's 2FA or making an admin needs a fresh check (the password, a code or a
   passkey within a few minutes): when the server asks, ask the person, then try again */
async function fresh(action) {
  try { return await action(); }
  catch (e) { if (e.detail !== "reauth_required") throw e; }
  await confirmIdentity();
  return action();
}
function confirmIdentity() {
  return new Promise((resolve, reject) => {
    const box = $("#reauth"), msg = el("span", {class: "msg", role: "status"});
    const secret = el("input", {type: "password", autocomplete: "current-password", placeholder: "Password or code",
      "aria-label": "Your password, or a code from your authenticator app", required: true});
    const done = ok => { box.hidden = true; box.replaceChildren(); ok ? resolve() : reject(new Error("Not done: you didn't confirm it's you.")); };
    const failed = e => { msg.textContent = e.message; msg.classList.add("bad"); };
    const viaPasskey = async () => {
      try {
        const o = await call("me/reauth/passkey/options", "POST");
        let credential;
        try { credential = await passkeyGet(o.options); } catch (e) { throw new Error(passkeyProblem(e)); }
        await call("me/reauth/passkey", "POST", {state: o.state, credential});
        done(true);
      } catch (e) { failed(e); }
    };
    box.replaceChildren(
      el("p", {}, el("b", {text: "Confirm it's you. "}), `This needs your password, a code from your authenticator app or a passkey ` +
        `(it then isn't asked again for ${ME.reauth_minutes} minutes).`),
      el("form", {class: "row", onsubmit: async ev => { ev.preventDefault();
        try { await call("me/reauth", "POST", {secret: secret.value}); done(true); } catch (e) { failed(e); } }},
        secret, el("button", {class: "btn primary small", type: "submit", text: "Confirm"}),
        passkeysWork() ? el("button", {class: "btn small", type: "button", text: "Use a passkey", onclick: viaPasskey}) : null,
        el("button", {class: "btn small", type: "button", text: "Cancel", onclick: () => done(false)})),
      msg);
    box.hidden = false;
    box.scrollIntoView({block: "nearest"});
    secret.focus();
  });
}
function showTemp(username, pw, days) {
  say("#userMsg", "");
  $("#userMsg").replaceChildren(`One-time password for ${username}: `, el("code", {text: pw}),
    ` — give it to them privately; it isn't shown again, and stops working if not used within ${days || 7} days.`);
}
async function loadUsers() {
  const list = await call("users");
  loadOwnerless();
  $("#userList").replaceChildren(el("table", {class: "tbl"},
    el("thead", {}, el("tr", {}, ...["Account", "Status", "2FA", "Devices", ""].map(h => el("th", {text: h})))),
    el("tbody", {}, ...list.map(u => {
      const self = u.id === ME.user.id;
      const act = (label, body, done) => el("button", {class: "btn small", type: "button", text: label, disabled: self || null,
        onclick: async () => {
          try {
            let r;
            try { r = await fresh(() => call("users/" + u.id, "PATCH", body)); }
            catch (e) {   // disabling the only owner of a profile: ask first
              if (!(e.detail && e.detail.sole_owner_of) || !confirm(e.message)) throw e.detail && e.detail.sole_owner_of ? new Error("Not disabled.") : e;
              r = await call("users/" + u.id, "PATCH", {...body, force: true});
            }
            done && done(r); loadUsers();
          } catch (e) { say("#userMsg", e.message, true); }
        }});
      return el("tr", {},
        el("td", {}, u.username, u.is_admin ? el("span", {class: "tag", text: "admin"}) : null, self ? el("span", {class: "muted", text: " (you)"}) : null),
        el("td", {text: u.disabled ? "disabled" : u.must_change_pw ? "hasn't chosen a password yet" : "active"}),
        el("td", {text: u.two_factor ? "on" : "off"}),
        el("td", {text: u.sessions}),
        el("td", {class: "acts"},
          act(u.disabled ? "Enable" : "Disable", {disabled: !u.disabled}),
          act(u.is_admin ? "Remove admin" : "Make admin", {is_admin: !u.is_admin}),
          act("Reset password", {reset_password: true}, r => setTimeout(() => showTemp(u.username, r.temporary_password, r.temporary_days))),
          self ? null : el("button", {class: "btn small", type: "button", text: "Remove", onclick: () => removeAccount(u)}),
          u.two_factor ? el("button", {class: "btn small", type: "button", text: "Turn off 2FA", disabled: self || null, onclick: async () => {
            if (!confirm(`Turn off two-factor sign-in for ${u.username}? Do this only if they lost both their phone and their recovery codes. They'll be signed out everywhere.`)) return;
            try { await fresh(() => call("users/" + u.id, "PATCH", {disable_2fa: true})); loadUsers(); } catch (e) { say("#userMsg", e.message, true); } }}) : null));
    }))));
}
/* lab names matched during imports (#23): install-wide, so admins change or remove them; never shows results */
async function loadLabmaps() {
  const [list, names] = await Promise.all([call("labmaps"), call("lab-names").catch(() => [])]);
  $("#labmapNames").replaceChildren(...[...new Set([...names, ...list.map(m => m.target).filter(Boolean)])].map(n => el("option", {value: n})));
  $("#labmaps").hidden = false;
  if (!list.length) { $("#labmapList").replaceChildren(el("p", {class: "muted", text: "None yet: they're made when an import's lab names are matched at its preview."})); return; }
  const as = m => m.target ? `charted as “${m.target}”` : "kept as a series of its own";
  $("#labmapList").replaceChildren(el("table", {class: "tbl"},
    el("thead", {}, el("tr", {}, ...["Reported as", "Goes to", "From", "Added", ""].map(h => el("th", {text: h})))),
    el("tbody", {}, ...list.map(m => {
      const goes = el("td", {class: "lm-target", text: m.target || "its own series"});
      const edit = () => {
        const input = el("input", {type: "text", list: "labmapNames", value: m.target || "", maxlength: 120, "aria-label": `Series for ${m.name}`,
                                   placeholder: "Leave empty to keep it separate"});
        const ok = el("button", {class: "btn small primary", type: "submit", text: "Save"});
        const no = el("button", {class: "btn small", type: "button", text: "Cancel", onclick: () => loadLabmaps()});
        goes.replaceChildren(el("form", {class: "lm-edit", onsubmit: async ev => {
          ev.preventDefault();
          try { const r = await call("labmaps/" + m.id, "PATCH", {target: input.value.trim() || null}); say("#labmapMsg", `“${r.name}” is now ${as(r)}.`); loadLabmaps(); }
          catch (e) { say("#labmapMsg", e.message, true); }
        }}, input, ok, no));
        input.focus();
      };
      return el("tr", {"data-labmap": m.id},
        el("td", {}, el("b", {text: m.name}), el("span", {class: "muted", text: [m.unit, m.loinc && "LOINC " + m.loinc].filter(Boolean).map(x => " · " + x).join("")})),
        goes,
        el("td", {text: m.label}),
        el("td", {}, `${when(m.created)} by ${m.created_by}`, m.changed ? el("div", {class: "muted", text: `changed ${when(m.changed)} by ${m.changed_by}`}) : null),
        el("td", {class: "acts"},
          el("button", {class: "btn small", type: "button", text: "Change", onclick: edit}),
          el("button", {class: "btn small", type: "button", text: "Remove", onclick: async () => {
            if (!confirm(`Forget the match for “${m.name}”? The next import that has it will ask again. Results already saved keep their series.`)) return;
            try { await call("labmaps/" + m.id, "DELETE"); say("#labmapMsg", `Forgot the match for “${m.name}”.`); loadLabmaps(); }
            catch (e) { say("#labmapMsg", e.message, true); }
          }})));
    }))));
}
/* profiles nobody can manage (every owner disabled or removed): an admin names a new owner; never shows records */
async function loadOwnerless() {
  const list = await call("ownerless"), box = $("#ownerless");
  if (!list.length) { box.replaceChildren(); return; }
  box.replaceChildren(el("div", {class: "danger-zone ownerless"},
    el("p", {}, el("b", {text: "Profiles without an owner. "}), "Every owner of these was disabled or removed, so nobody can ",
      "share or rename them. Give each an owner who should look after it. Whoever you choose can see its records."),
    ...list.map(p => {
      const active = p.members.filter(m => !m.disabled);
      const pick = el("select", {"aria-label": `New owner of ${p.name}`},
        ...active.map(m => el("option", {value: m.username, text: `${m.username} (now ${m.role})`})),
        el("option", {value: "", text: "Another account…"}));
      const other = el("input", {placeholder: "username", autocapitalize: "none", spellcheck: "false", "aria-label": "Another account", hidden: active.length > 0});
      pick.onchange = () => { other.hidden = pick.value !== ""; if (!other.hidden) other.focus(); };
      const go = el("button", {class: "btn small", type: "button", text: "Make owner", onclick: async () => {
        const who = (pick.value || other.value).trim();
        if (!who) return;
        if (who.toLowerCase() === ME.user.username.toLowerCase() && !confirm(`This gives you access to ${p.name}'s records. Continue?`)) return;
        try { await call(`ownerless/${p.id}/owner`, "POST", {username: who}); say("#userMsg", `${who} now owns ${p.name}.`); loadOwnerless(); loadProfiles(); }
        catch (e) { say("#userMsg", e.message, true); }
      }});
      return el("div", {class: "row"}, el("b", {text: p.name}),
        el("span", {class: "muted", text: p.members.length ? p.members.map(m => `${m.username} (${m.role}${m.disabled ? ", disabled" : ""})`).join(", ") : "no members"}),
        pick, other, go);
    })));
}

/* removing an account for good: if they alone own profiles, ask who takes those over first */
async function removeAccount(u) {
  if (!confirm(`Remove the account ${u.username} for good? They're signed out and lose access to every profile. ` +
               "Their past activity stays in the logs, marked as removed. Their profiles' records stay.")) return;
  let body = {};
  for (;;) {
    try {
      const r = await call("users/" + u.id, "DELETE", body);
      say("#userMsg", `Removed ${u.username}.` + (r.handed_over.length ? ` ${body.hand_to} now owns ${r.handed_over.join(", ")}.` : ""));
      loadUsers(); loadProfiles();
      return;
    } catch (e) {
      if (!(e.detail && e.detail.sole_owner_of)) { say("#userMsg", e.message, true); return; }
      const who = prompt(e.message + "\n\nType the username of the new owner, or leave it empty to remove anyway " +
                         "(an admin can give it an owner later, under Accounts).", "");
      if (who === null) { say("#userMsg", "Not removed."); return; }
      body = who.trim() ? {hand_to: who.trim()} : {force: true};
    }
  }
}
$("#userNew").onsubmit = async ev => {
  ev.preventDefault();
  try {
    const body = {username: $("#userName").value.trim(), is_admin: $("#userAdmin").checked};
    const r = await fresh(() => call("users", "POST", body));
    $("#userNew").reset();
    if (r.admin_needs_two_factor) { ME.user.admin_needs_two_factor = true; adminGate(); }   // not the only account any more
    else await loadUsers();
    showTemp(r.username, r.temporary_password, r.temporary_days);
  } catch (e) { say("#userMsg", e.message, true); }
};

/* ---------------------------------------------------------------- tabs */
/* Sign-in & security, Profiles, Admin. The address (#security, #profiles, #admin) keeps the tab across a reload, and
   older links to a section (#password, #twofa, #passkeys, #sessions, #users) open the tab that holds it. */
const TAB_OF = {security: "security", password: "security", twofa: "security", passkeys: "security", sessions: "security",
  profiles: "profiles", admin: "admin", users: "admin"};
const TAB_NAMES = ["security", "profiles", "admin"];
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
addEventListener("hashchange", () => { if (tabsOn) openFromHash(); });

/* ---------------------------------------------------------------- start */
(async () => {
  try { ME = await call("me"); } catch (e) { return; }
  $("#who").textContent = ME.user.username;
  { const w = plainHttpWarning(); if (w) { $("#insecure").textContent = w; $("#insecure").hidden = false; } }
  $("#pwHint").textContent = `At least ${ME.password_min} characters. A few unrelated words make a strong password you can remember.`;
  if (ME.user.must_change_pw) {   // nothing else is available until the one-time password is replaced (no tabs)
    $("#mustChange").hidden = false; $("#back").hidden = true;
    for (const id of ["#sessions", "#profiles", "#users", "#twofa", "#passkeys"]) $(id).hidden = true;
    $("#pwCur").focus();
    return;
  }
  if (ME.user.needs_two_factor) {   // the server requires it: set it up before anything else (no tabs)
    $("#needTf").hidden = false; $("#back").hidden = true;
    for (const id of ["#sessions", "#profiles", "#users"]) $(id).hidden = true;
    await Promise.all([loadTwoFactor(), loadPasskeys()]);
    return;
  }
  $("#tab-admin").hidden = !ME.user.is_admin;
  $("#acctTabs").hidden = false; tabsOn = true;
  openFromHash();
  const admin = ME.user.is_admin && !ME.user.admin_needs_two_factor;
  await Promise.all([loadSessions(), loadProfiles(), loadTwoFactor(), loadPasskeys(), admin ? loadUsers() : null,
    admin ? loadNotice() : null, admin ? loadLabmaps() : null]);
  if (ME.user.is_admin && !admin) { adminGate(); return; }
  if (ME.user.is_admin) {
    const req = $("#reqTf");
    req.checked = (await call("settings")).require_2fa;
    req.onchange = async () => {
      try { await call("settings", "PATCH", {require_2fa: req.checked}); say("#userMsg", req.checked ? "Everyone now needs two-factor sign-in." : "Two-factor sign-in is now optional."); }
      catch (e) { req.checked = !req.checked; say("#userMsg", e.message, true); }
    };
  }
  if (ME.user.is_admin) {   // account events only: who created, disabled or reset which account (no records)
    const panel = activityPanel("activity");
    panel.firstChild.textContent = "Account activity";
    $("#acctLog").replaceChildren(panel);
  }
  $("#users").hidden = !ME.user.is_admin;
  openFromHash();   // again: the cards above have their content now, so a section link lands where it should
})();
