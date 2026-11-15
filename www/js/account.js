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

/* ---------------------------------------------------------------- activity */
const WHAT = {
  view: () => "opened the records",
  document: d => `opened ${d.name || "a document"}`,
  create: () => "created the profile",
  rename: d => `renamed the profile from “${d.from}” to “${d.to}”`,
  share: d => d.from ? `changed ${d.username}’s role from ${d.from} to ${d.role}` : `shared it with ${d.username} as ${d.role}`,
  unshare: (d, e) => d.username === e.actor ? "left the profile" : `removed ${d.username}’s access`,
  import: d => `imported records (${d.documents} documents)`,
  reload: () => "loaded the latest records",
  setup: () => "set up Sanguity with the first account",
  password: () => "changed their password",
  "account.create": d => `created the account ${d.username}${d.admin ? " (admin)" : ""}`,
  "account.disable": d => `disabled ${d.username}`,
  "account.enable": d => `enabled ${d.username}`,
  "account.admin": d => `made ${d.username} an admin`,
  "account.unadmin": d => `removed ${d.username}’s admin role`,
  "account.reset": d => `reset ${d.username}’s password`,
};
const FILTERS = [["", "Everything"], ["view", "Opened the records"], ["document", "Opened documents"],
  ["share", "Sharing"], ["unshare", "Removed access"], ["rename", "Renames"], ["import", "Imports"], ["reload", "Reloads"]];
const describe = e => (WHAT[e.action] || (() => e.action))(e.detail || {}, e);
function activityRows(list) {
  return list.map(e => el("tr", {},
    el("td", {class: "nowrap", text: when(e.at)}), el("td", {text: e.actor}), el("td", {text: describe(e)}),
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
function showTemp(username, pw) {
  say("#userMsg", "");
  $("#userMsg").replaceChildren(`One-time password for ${username}: `, el("code", {text: pw}), " — give it to them privately; it isn't shown again.");
}
async function loadUsers() {
  const list = await call("users");
  $("#userList").replaceChildren(el("table", {class: "tbl"},
    el("thead", {}, el("tr", {}, ...["Account", "Status", "Devices", ""].map(h => el("th", {text: h})))),
    el("tbody", {}, ...list.map(u => {
      const self = u.id === ME.user.id;
      const act = (label, body, done) => el("button", {class: "btn small", type: "button", text: label, disabled: self || null,
        onclick: async () => {
          try {
            let r;
            try { r = await call("users/" + u.id, "PATCH", body); }
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
        el("td", {text: u.sessions}),
        el("td", {class: "acts"},
          act(u.disabled ? "Enable" : "Disable", {disabled: !u.disabled}),
          act(u.is_admin ? "Remove admin" : "Make admin", {is_admin: !u.is_admin}),
          act("Reset password", {reset_password: true}, r => setTimeout(() => showTemp(u.username, r.temporary_password)))));
    }))));
}
$("#userNew").onsubmit = async ev => {
  ev.preventDefault();
  try { const r = await call("users", "POST", {username: $("#userName").value.trim(), is_admin: $("#userAdmin").checked}); $("#userNew").reset(); await loadUsers(); showTemp(r.username, r.temporary_password); }
  catch (e) { say("#userMsg", e.message, true); }
};

/* ---------------------------------------------------------------- start */
(async () => {
  try { ME = await call("me"); } catch (e) { return; }
  $("#who").textContent = ME.user.username;
  $("#pwHint").textContent = `At least ${ME.password_min} characters. A few unrelated words make a strong password you can remember.`;
  if (ME.user.must_change_pw) {   // nothing else is available until the one-time password is replaced
    $("#mustChange").hidden = false; $("#back").hidden = true;
    for (const id of ["#sessions", "#profiles", "#users"]) $(id).hidden = true;
    $("#pwCur").focus();
    return;
  }
  await Promise.all([loadSessions(), loadProfiles(), ME.user.is_admin ? loadUsers() : null]);
  if (ME.user.is_admin) {   // account events only: who created, disabled or reset which account (no records)
    const panel = activityPanel("activity");
    panel.firstChild.textContent = "Account activity";
    $("#acctLog").replaceChildren(panel);
  }
  $("#users").hidden = !ME.user.is_admin;
  if (location.hash) document.querySelector(location.hash)?.scrollIntoView();
})();
