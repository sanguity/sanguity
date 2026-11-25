/* Sanguity · the Admin page (#167): what only admins do, for the whole server, in one place, with a sidebar of groups
   (sidebar.js, as on Settings): Accounts (and requiring two-factor sign-in), Backups (admin-backups.js,
   #200), Connected-source apps and
   their sync defaults, the Provider directory (both admin-sources.js), Lab name matches, Outside connections
   (outside.js) and Install activity (activity.js). Someone who isn't an admin gets a page saying so, and no menu item
   (nav.js). Admins need a second factor of their own once the server has more than one account: until then each
   part says so. */
"use strict";   // $, el and api come from util.js
/* api() from util.js, returning the JSON answer, or throwing with the server's message */
async function call(path, method = "GET", body) {
  const r = await api(path, {method, body: body ? JSON.stringify(body) : undefined});
  if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = j.detail, e = new Error(Array.isArray(d) ? "Please check the fields." : (d && d.message) || d || r.status + " " + r.statusText);
    e.detail = d; e.status = r.status; throw e;
  }
  return j;
}
const say = (id, text, bad = false) => sayIn($(id), text, bad);   // shown and said (util.js, #209)
const when = t => fmtDT(t * 1000);   // dates.js: this person's time zone and date format
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
let ME;

$("#logout").onclick = logOut;   // util.js: asks first about what isn't saved (#391)

/* ---------------------------------------------------------------- the sidebar: one group shown at a time */
/* The sidebar every account page has (sidebar.js, #266), its groups under People, Data safety, Connections and Lab
   matching. The address follows the group (admin.html#apps); an address naming a section (admin.html#users,
   #labmaps, #sec-sync, #outside) opens that section's group and scrolls to it. */
const GROUPS = SG_PAGES.admin.groups;   // groups.js: one list, which the command palette searches too (#273)
const AdminNav = sideNav({id: "admNav", page: "Admin", groups: GROUPS, find: "Find in Admin", none: q => `Nothing in Admin matches “${q}”.`,
  // the bar (#272): the sync defaults' numbers (admin-sources.js) and the backups kept (#409, admin-backups.js)
  bar: {save: async (parts, msg, had) => { for (const p of parts) await (p.id === "backups" ? bkKeepSave : syncSave)(msg, had); },
    discard: parts => { for (const p of parts) (p.id === "backups" ? bkKeepDiscard : syncDiscard)(); }}});

/* ---------------------------------------------------------------- notices: failed sign-ins, sources that stopped */
async function loadNotice() {
  const all = await call("security/notice"), n = all.notice, box = $("#secNotice");
  syncNotice(all.sync || []);
  codeNotice(all.codes_stopped || []);
  box.hidden = !n;
  if (!n) return;
  const list = (rows, key) => rows.map(r => `${r[key]} (${r.failures})`).join(", ");
  const msg = el("span", {class: "msg"});   // a Dismiss that didn't work says so here (#210: not in a pop-up)
  box.replaceChildren(
    el("b", {text: `${n.total} failed sign-in${n.total === 1 ? "" : "s"} since ${when(n.since)}`}), " (last at ", when(n.last), "). ",
    // #931: a wrong password or code from a device already signed in to the account (the fresh check, a password change...)
    n.checks ? `${n.checks === n.total ? "All" : n.checks.toLocaleString("en-US")} of them ${n.checks === 1 && n.total !== 1 ? "was" : "were"} a wrong password or code ` +
      "from a device already signed in to the account: someone there may be guessing it. Resetting its password signs it out everywhere. " : "",
    n.addresses.length ? `From: ${list(n.addresses, "ip")}. ` : "",
    n.accounts.length ? `Accounts tried: ${list(n.accounts, "username")}. ` : "",
    "Throttling slows guessing down; if you don't recognise this, check that the accounts tried have strong passwords " +
    "or two-factor sign-in, and look at the app's log (docker compose logs app) for details. ",
    el("button", {class: "btn small", type: "button", text: "Dismiss", onclick: async () => {
      try { await call("security/notice/dismiss", "POST"); box.hidden = true; } catch (e) { sayIn(msg, e.message, true); } }}),
    msg);
}

/* accounts whose authenticator codes stopped after 100 wrong ones since the last right one (#928): while it lasts */
function codeNotice(rows) {
  const box = $("#codeNotice");
  box.hidden = !rows.length;
  if (!rows.length) return;
  box.replaceChildren(el("b", {text: "Sign-in with an authenticator code is stopped for " +
    rows.map(r => r.username + (r.since ? ` (since ${when(r.since)})` : "")).join(", ") + ". "}),
    "Too many wrong codes were entered since the last right one, so someone may know the password. If the person " +
    "didn't make them, reset their password and their two-factor sign-in under Accounts. A recovery code or a passkey " +
    "still signs them in; a recovery code lets codes work again.");
}

/* connected sources that stopped syncing, across the install (#102): counts per source, no profiles named */
function syncNotice(rows) {
  const box = $("#syncNotice");
  box.hidden = !rows.length;
  if (!rows.length) return;
  const part = r => [r.access && `${r.access} with access ended`, r.failing && `${r.failing} failing`,
    r.stale && `${r.stale} not synced lately`].filter(Boolean).join(", ");
  box.replaceChildren(el("b", {text: "Connected sources need attention: "}),
    rows.map(r => `${r.source} (${part(r)})`).join("; ") + ". ",
    "Each profile's owners and editors see it on the profile's page and can sign in again or sync from ",
    el("a", {href: "sources.html#portals", text: "Sources → Portals and health plans"}), ".");
}

/* ---------------------------------------------------------------- accounts (admin) */
/* admins need a second factor of their own once the server has more than one account: until then, say so (the
   server refuses the rest of this page until then too) */
function adminGate() {
  $("#adminTf").hidden = false; $("#adminTools").hidden = $("#userDanger").hidden = true; $("#users").hidden = false;
  $("#secNotice").hidden = $("#codeNotice").hidden = $("#syncNotice").hidden = $("#backupNotice").hidden = true;
  for (const id of ["#backups", "#admin", "#sec-sync", "#labmaps", "#installLog"]) gateCard($(id));
}
function gateCard(card) {
  if (card.querySelector(".adm-gate")) return;
  const title = card.querySelector("h2")?.textContent;   // the card's title, wherever it sits in the card
  for (const c of card.children) c.hidden = true;
  card.append(...(title ? [el("h2", {text: title})] : []), el("p", {class: "warn adm-gate"},
    "First turn on two-factor sign-in (the authenticator app) or add a passkey for your own account, under ",
    el("a", {href: "account.html#security", text: "Account → Sign-in & security"}), "."));
  card.hidden = false;
}
/* resetting a password, turning off someone's 2FA, making an admin, and (#590) giving a profile an owner, disabling
   its only owner or handing its profiles over needs a fresh check (the password, a code or a
   passkey within a few minutes): when the server asks, ask the person, then try again. box: where to ask (Backups
   has its own, #200) */
async function fresh(action, box = $("#reauth")) {
  try { return await action(); }
  catch (e) { if (e.detail !== "reauth_required") throw e; }
  await confirmIdentity(box);
  return action();
}
/* its Use a passkey only where one can work (#528: at an IP address, or with none made here, it could only say so once
   pressed): a browser that has them, at a name with HTTPS (util.js passkeysWork), and a passkey of yours made here; a
   code asked for only from someone with an authenticator app (#532: an admin whose second factor is a passkey has none) */
async function confirmIdentity(box = $("#reauth")) {
  const [pk, app] = await Promise.all([passkeysWork() && call("me/passkeys?origin=" + encodeURIComponent(location.origin))
    .then(st => !st.problem && st.passkeys.some(k => k.here), () => false), call("me/2fa").then(st => st.on, () => true)]);
  const ways = ["your password", app && "a code from your authenticator app", pk && "a passkey"].filter(Boolean);
  return new Promise((resolve, reject) => {
    const msg = el("span", {class: "msg", role: "status"});
    const secret = el("input", {type: "password", autocomplete: "current-password", placeholder: app ? "Password or code" : "Password",
      "aria-label": app ? "Your password, or a code from your authenticator app" : "Your password", required: true});
    const done = ok => { box.hidden = true; box.replaceChildren();   // (its why, for a line saying "Not saved" itself: #536)
      ok ? resolve() : reject(Object.assign(new Error("Not done: you didn't confirm it's you."), {why: "you didn't confirm it's you."})); };
    const failed = e => sayIn(msg, e.message, true);
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
      el("p", {}, el("b", {text: "Confirm it's you. "}), `This needs ${ways.slice(0, -1).join(", ")}${ways.length > 1 ? " or " : ""}${ways.at(-1)} ` +
        `(it then isn't asked again for ${ME.reauth_minutes} minutes).`),
      el("form", {class: "row", onsubmit: async ev => { ev.preventDefault();
        pwSent(ev.target);
        try { await call("me/reauth", "POST", {secret: secret.value}); done(true); } catch (e) { failed(e); } }},
        pwField(secret, {name: app ? "password or code" : "password"}).box, el("button", {class: "btn primary small", type: "submit", text: "Confirm"}),
        pk ? el("button", {class: "btn small", type: "button", text: "Use a passkey", onclick: viaPasskey}) : null,
        el("button", {class: "btn small", type: "button", text: "Cancel", onclick: () => done(false)})),
      msg);
    box.hidden = false;
    box.scrollIntoView({block: "nearest"});
    secret.focus();
  });
}
/* a one-time password (#499): shown once, so where it's seen, with focus on its Copy, in view: under Create an account,
   over the list (for a reset from a row's ⋯ too: #userMsg, where it was, is under the list); under the note asking for
   a second factor, once Admin waits for one. It stays until the next one, or until its account (id) is removed,
   disabled or renamed (#502: loadUsers) */
const TEMP = el("div", {id: "userTemp", class: "temp-pw", role: "group", "aria-labelledby": "userTempName"});
function showTemp(username, pw, days, id) {
  Object.assign(TEMP.dataset, {user: id, name: username});
  const code = el("code", {text: pw}), copy = copyBtn(pw, {what: "the one-time password", sel: code});
  const how = `Give it to them privately: it isn't shown again, and stops working if it isn't used within ${days || 7} days.`;
  TEMP.replaceChildren(el("div", {class: "copy-val"}, el("span", {class: "copy-val-name", id: "userTempName", text: `One-time password for ${username}`}),
    el("div", {class: "copy-val-row"}, code, copy)), el("p", {class: "hint", text: how}));
  ($("#adminTools").hidden ? $("#adminTf") : $("#userNewRow")).after(TEMP);
  sayIn($("#userMsg"), ""); sayIn($("#userNewMsg"), "");   // (an earlier "Not done" there no longer so)
  announce(`One-time password for ${username}: ${pw}. ${how}`);
  copy.focus({preventScroll: true});
  TEMP.scrollIntoView({block: "nearest"});
}
/* the accounts (#287): a row each, its name with badges (admin, two-factor, disabled; "(you)" on your own), where it's
   signed in, and a ⋯ menu of what can be done to it (util.js rowMenu, as Settings' rows). Your own row has none: none
   of those can be done to your own account here. Removing one is the danger zone's, under the list. On a phone each row
   is a little card, each value under its column's name (util.js labelCells, #320). */
const devices = n => n ? plural(n, "device") : "no device";
/* mine: the account whose ⋯ focus goes back to once drawn again (a change from its row, #493: remembered before it's
   asked or checked, by which time focus has moved on); by default the row focus is in now */
async function loadUsers(mine = document.activeElement?.closest?.("#userList tr[data-user]")?.dataset.user) {
  // the profiles without an owner too, drawn before what's said and focused under them (#515: drawn after, the zone's
  // reply went out of sight); they're drawn again only where they changed
  const [list] = await Promise.all([call("users"), loadOwnerless()]);
  const t = TEMP.isConnected && list.find(u => String(u.id) === TEMP.dataset.user);
  if (TEMP.isConnected && (!t || t.disabled || t.username !== TEMP.dataset.name)) TEMP.remove();   // its password no use now (#502)
  $("#userList").replaceChildren(el("table", {class: "tbl users"},
    el("thead", {}, el("tr", {}, ...["Account", "Signed in on", ""].map(h => el("th", {text: h})))),
    el("tbody", {}, ...list.map(u => {
      const self = u.id === ME.user.id;
      return el("tr", {"data-user": u.id},
        el("td", {class: "who"}, el("b", {text: u.username}), self ? el("span", {class: "muted", text: " (you)"}) : null,
          u.is_admin ? el("span", {class: "tag", text: "admin"}) : null,
          u.two_factor ? el("span", {class: "tag", text: "two-factor"}) : null,
          u.codes_stopped ? el("span", {class: "tag off", text: "codes stopped"}) : null,   // (#928)
          u.disabled ? el("span", {class: "tag off", text: "disabled"}) : null,
          !u.disabled && u.must_change_pw ? el("span", {class: "muted new-pw", text: "Hasn't chosen a password yet"}) : null),
        el("td", {text: devices(u.sessions)}),
        el("td", {class: "acts"}, self ? null : userMenu(u)));
    }))));
  accountsZone(list.filter(u => u.id !== ME.user.id));
  // a change from a row's menu redraws the list: focus back on that row's ⋯
  if (mine) rowBack(mine);
}
/* focus back on an account's ⋯ (#287), unless it's somewhere else that's still there (the zone's box, the reauth form),
   and the row in view (#499: the check, over the list, had scrolled it away) */
const rowBack = id => {
  const a = document.activeElement, b = $(`#userList tr[data-user="${id}"] .set-more-btn`);
  if (b && (!a || a === document.body || !a.isConnected)) { b.focus({preventScroll: true}); b.scrollIntoView({block: "nearest"}); }
};
/* a row's ⋯: each change but Enable is asked first (#249), before any fresh check, so Cancel sends nothing */
function userMenu(u) {
  const n = u.username;
  return rowMenu(`Manage ${n}`, [
    u.is_admin
      ? {text: "Remove admin…", run: () => changeUser(u, {is_admin: false}, {title: `Remove ${n}'s admin role?`,
          message: "They keep their account and the profiles shared with them, but can't use the Admin page any more.", action: "Remove admin"},
          `${n} isn't an admin any more.`)}
      : {text: "Make admin…", run: () => changeUser(u, {is_admin: true}, {title: `Make ${n} an admin?`, message: [
          "Admins look after every account on this server: they can add, disable and remove accounts, reset passwords and " +
          "turn off two-factor sign-in, and look after backups and sources.",
          "They don't see a profile's records unless it's shared with them. They'll need two-factor sign-in of their own to use the Admin page."],
          action: "Make admin", danger: true}, `${n} is an admin now.`)},
    {text: "Reset password…", run: () => changeUser(u, {reset_password: true}, {title: `Reset ${n}'s password?`, message: [
      "Their password stops working at once, and they're signed out everywhere.",
      "You'll get a one-time password to give them; they choose a new one when they first sign in with it." +
      (u.two_factor ? " Their two-factor sign-in stays on." : "")], action: "Reset password", danger: true},
      r => showTemp(n, r.temporary_password, r.temporary_days, u.id))},
    u.two_factor ? {text: "Turn off two-factor sign-in…", run: () => changeUser(u, {disable_2fa: true}, {title: `Turn off two-factor sign-in for ${n}?`,
      message: [tfOffSays(u), "They'll be signed out everywhere."], action: "Turn off", danger: true}, `Turned off two-factor sign-in for ${n}: ${tfOffGone(u)} removed.`)} : false,
    u.disabled
      ? {text: "Enable", run: () => changeUser(u, {disabled: false}, null, `Enabled ${n}: they can sign in again.`)}
      : {text: "Disable…", run: () => changeUser(u, {disabled: true}, {title: `Disable ${n}?`, message: [
          "They're signed out everywhere and can't sign in until an admin enables the account again.",
          "Their profiles and records stay as they are."], action: "Disable", danger: true}, `Disabled ${n}: they're signed out everywhere.`)},
    null,
    {text: "Remove account…", danger: true, run: () => { $("#rmUser").value = u.id; USERZONE.show(n); }}].filter(e => e !== false), "user", "user-more");
}
/* what turning off someone's two-factor sign-in removes (#536: a passkey-only account was told of a phone and recovery
   codes it never had, and not that its passkeys go): the server takes the authenticator app, its codes and every passkey */
function tfOffSays(u) {
  const k = u.passkeys, keys = k > 1 ? `their ${k} passkeys` : "their passkey", any = k > 1 ? "any of them" : "it";
  if (!u.authenticator_app) return `It removes ${keys}. Do this only if they can't use ${any} any more.`;
  return `It removes their authenticator app${k ? `, its recovery codes and ${keys}` : " and its recovery codes"}. Do this only if they lost both ` +
    `their phone and their recovery codes${k ? `, and can't use ${k > 1 ? "any of their passkeys" : "their passkey"}` : ""}.`;
}
/* and, once done, what went (#541) */
const tfOffGone = u => u.authenticator_app ? `the authenticator app${u.passkeys ? `, its recovery codes and ${plural(u.passkeys, "passkey")}` : " and its recovery codes"}`
  : plural(u.passkeys, "passkey");
/* what the last action under Accounts said in a row, or where one was, in the accounts' list or the ownerless one's,
   gone as the next starts (#532: “No such account.” stayed through a Make owner, and “No such profile.” where the
   ownerless list was until it changed) */
const accQuiet = () => { for (const m of document.querySelectorAll("#userList .row-msg, #ownerless .row-msg")) m.remove(); say("#userMsg", ""); };
/* a row's action's reply (#502: it was under the list, out of sight): said in its row, under the account's name, until
   the next action starts (id null: just that) */
function rowSay(id, text, bad = false) {
  accQuiet();
  const tr = id != null && $(`#userList tr[data-user="${id}"]`);
  if (!tr) return text && say("#userMsg", text, bad);
  const m = el("p", {class: "msg row-msg"});
  tr.querySelector("td.who").append(m);
  sayIn(m, text, bad);
  rowBack(id);   // focus on its ⋯ if it was lost, and the row in view where it fits
  tr.scrollIntoView({block: "nearest"});
}
/* done: what follows (a reset's password shown), or the words saying it worked, in its row (#541: it said nothing) */
async function changeUser(u, body, sure, done) {
  rowSay(null);
  if (sure && !await ask(sure)) return;
  try {
    let r;
    try { r = await fresh(() => call("users/" + u.id, "PATCH", body)); }
    catch (e) {   // disabling the only owner of a profile: the server says which, and it's asked once more
      if (!(e.detail && e.detail.sole_owner_of)) throw e;
      if (!await ask({title: `Disable ${u.username} anyway?`, message: e.message, action: "Disable anyway", danger: true})) throw new Error("Not disabled.");
      r = await fresh(() => call("users/" + u.id, "PATCH", {...body, force: true}));   // (#590: the fresh check too)
    }
    await loadUsers(u.id);
    typeof done === "function" ? done(r) : rowSay(u.id, done);   // (a reset's password then shown, focus on it: #499)
  } catch (e) {
    if (e.status !== 404) return rowSay(u.id, e.message, true);   // (the check's Cancel: its form went with it)
    // removed meanwhile (#528: its row stayed, and the zone offered it): drawn away, why said where it was (util.js)
    const rows = () => [...$("#userList tbody").rows], at = rows().findIndex(r => r.dataset.user === String(u.id));
    await loadUsers();
    goneSay(rows(), Math.max(at, 0), e.message, ".set-more-btn", $("#users h2"), $("#userList"));
  }
}
/* profiles nobody can manage (every owner disabled or removed): an admin names a new owner; never shows records. Drawn
   again only when the list changed, and then a profile's row only if it did (#515: each account's action emptied every
   name typed or picked here, and what Make owner said): OWNERLESS has each row drawn, by profile id, with what from */
let OWNERLESS = new Map();
async function loadOwnerless() {
  const list = await call("ownerless"), box = $("#ownerless"), drawn = JSON.stringify(list);
  if (box.dataset.drawn === drawn) return;
  box.dataset.drawn = drawn;
  const had = OWNERLESS, a = document.activeElement;
  OWNERLESS = new Map(list.map(p => { const k = JSON.stringify(p), o = had.get(p.id); return [p.id, o && o[0] === k ? o : [k, ownerlessRow(p)]]; }));
  if (!list.length) { box.replaceChildren(); return; }
  box.replaceChildren(el("div", {class: "ownerless"},
    el("p", {}, el("b", {text: "Profiles without an owner. "}), "Every owner of these was disabled or removed, so nobody can ",
      "share or rename them. Give each an owner who should look after it. Whoever you choose can see its records."),
    ...[...OWNERLESS.values()].map(([, row]) => row)));
  if (a && a.isConnected && a !== document.activeElement) a.focus({preventScroll: true});   // (a row kept: moved, it lost focus)
}
const ownRows = () => [...$("#ownerless").querySelectorAll(".ownerless > .row")];
/* a profile's row: who's in it, a new owner picked or typed, and Make owner */
function ownerlessRow(p) {
  const active = p.members.filter(m => !m.disabled);
  const pick = el("select", {"aria-label": `New owner of ${p.name}`},
    ...active.map(m => el("option", {value: m.username, text: `${m.username} (now ${m.role})`})),
    el("option", {value: "", text: "Another account…"}));
  const other = el("input", {placeholder: "username", autocapitalize: "none", spellcheck: "false", "aria-label": "Another account", hidden: active.length > 0});
  pick.onchange = () => { other.hidden = pick.value !== ""; if (!other.hidden) other.focus(); };
  const go = el("button", {class: "btn small", type: "button", text: "Make owner", onclick: async () => {
    const who = (pick.value || other.value).trim();
    if (!who) return;
    if (who.toLowerCase() === ME.user.username.toLowerCase() &&
        !await ask({title: `Own ${p.name} yourself?`, message: `This gives you access to the records in ${p.name}.`, action: "Make me owner"})) return;
    const row = go.closest(".row"), at = ownRows().indexOf(row);
    ownSay(null);
    try { await fresh(() => call(`ownerless/${p.id}/owner`, "POST", {username: who})); await loadOwnerless(); }   // (#590: the fresh check)
    catch (e) {
      // the profile deleted, or given an owner, meanwhile (#528: its row stayed): drawn away, why said where it was
      if (e.status === 404 || e.status === 409) { await loadOwnerless(); if (!OWNERLESS.has(p.id)) return goneSay(ownRows(), at, e.message, "button", $("#users h2"), $("#ownerless")); }
      return ownSay(row, e.message, true);   // under its row, focus still on its Make owner (#510)
    }
    // its row gone (#502: focus fell to the page): on the next profile's Make owner, else the one before's, what it
    // said where its row was (#510: it was under the accounts list, out of sight); the last one gone, where the list
    // was, with focus on it (#506: focus went to the card's heading, the message out of sight under the accounts)
    const rows = ownRows(), done = `${who} now owns ${p.name}.`;
    if (rows.length) { focusNear(rows, at, "button"); return rows[at] ? ownSay(rows[at], done, false, true) : ownSay(rows[at - 1], done); }
    const m = el("p", {class: "msg", tabindex: "-1", text: done + " Every profile has an owner now."});
    $("#ownerless").replaceChildren(m);
    m.focus({preventScroll: true}); m.scrollIntoView({block: "nearest"});
  }});
  other.onkeydown = ev => { if (ev.key === "Enter" && !ev.isComposing) { ev.preventDefault(); go.click(); } };   // as Make owner (#524)
  return el("div", {class: "row"}, el("b", {text: p.name}),
    el("span", {class: "muted", text: p.members.length ? p.members.map(m => `${m.username} (${m.role}${m.disabled ? ", disabled" : ""})`).join(", ") : "no members"}),
    pick, other, go);
}
/* Make owner's reply (#510), as a row's action's is (rowSay): under its row, or, its row gone, before the next one,
   in view with the Make owner focus is on, until the next Make owner (row null: just that) */
function ownSay(row, text, bad = false, before = false) {
  accQuiet();
  if (!row) return;
  const m = el("p", {class: "msg row-msg"});
  row[before ? "before" : "after"](m);
  sayIn(m, text, bad);
  m.scrollIntoView({block: "nearest"});
}

/* removing an account for good (#287): the danger zone under the list (util.js dangerZone, as deleting a profile), the
   account picked there or by its row's "Remove account…", its username typed before it goes (the server checks it
   again). If they alone own profiles, who takes those over is asked then. others: every account but your own */
let USERZONE, USERS = new Map();
function accountsZone(others) {
  USERS = new Map(others.map(u => [String(u.id), u]));
  if (!USERZONE) {
    const pick = el("select", {id: "rmUser"});
    USERZONE = dangerZone({title: "Remove an account", hint: "For good: they lose access to every profile. It can't be undone.",
      pick: el("label", {class: "dz-name"}, "Account to remove", pick),
      says: () => ["They're signed out everywhere and lose access to every profile shared with them.",
                   "Their past activity stays in the logs, marked as removed. Their profiles' records stay."],
      action: "Remove the account", run: typed => removeAccount(USERS.get(pick.value), typed)});
    pick.onchange = () => { const u = USERS.get(pick.value); u ? USERZONE.show(u.username, {focus: false}) : USERZONE.hide(); };
    $("#userDanger").replaceChildren(USERZONE.node);
  }
  const pick = $("#rmUser"), was = pick.value;
  pick.replaceChildren(el("option", {value: "", text: "Choose an account…"}), ...others.map(u => el("option", {value: u.id, text: u.username})));
  pick.value = USERS.has(was) ? was : "";
  if (!pick.value) USERZONE.hide();
  USERZONE.node.hidden = !others.length;   // yours alone: nothing to remove here
}
/* an account gone from the zone: said on its line, focus on its picker (the box it was typed in went with it), both in
   view once the list over them is drawn (#515: under a phone's tabs); with no other account left, the zone hidden, on
   Accounts' line and the page's heading (#493) */
function zoneSay(text, bad = false) {
  if (USERZONE.node.hidden) { say("#userMsg", text, bad); focusNear([], 0, null, $("#users h2")); }
  else { sayIn(USERZONE.msg, text, bad); $("#rmUser").focus({preventScroll: true}); USERZONE.node.scrollIntoView({block: "nearest"}); }
}
async function removeAccount(u, typed) {
  accQuiet();
  let body = {confirm_name: typed};
  for (;;) {
    try {
      const r = await fresh(() => call("users/" + u.id, "DELETE", body));   // handing profiles over: the fresh check (#590)
      $("#rmUser").value = "";
      await loadUsers();
      zoneSay(`Removed ${u.username}.` + (r.handed_over.length ? ` ${body.hand_to} now owns ${r.handed_over.join(", ")}.` : ""));
      return;
    } catch (e) {
      // removed meanwhile (by another admin, or in another tab): the list and the zone's picker drawn again without it,
      // the zone closed, saying why (#493: it stayed listed)
      if (e.status === 404) { await loadUsers(); zoneSay(e.message, true); return; }
      if (!(e.detail && e.detail.sole_owner_of)) throw e;
      // who takes their profiles over: asked in the app's dialog, with a box for the name (#210: was the browser's prompt)
      const field = el("input", {autocapitalize: "none", spellcheck: "false", autocomplete: "off"});
      if (!await ask({title: `Who takes over from ${u.username}?`,
                      message: [e.message, el("label", {}, "New owner's username", field),
                                "Leave it empty to remove the account anyway: an admin can give the profile an owner later, under Accounts."],
                      action: "Remove account", danger: true, field})) throw new Error("Not removed.");
      body = {confirm_name: typed, ...(field.value.trim() ? {hand_to: field.value.trim()} : {force: true})};
    }
  }
}
$("#userNew").onsubmit = async ev => {
  ev.preventDefault();
  const form = ev.target;
  sayIn($("#userNewMsg"), ""); accQuiet();
  try {
    const body = {username: $("#userName").value.trim(), is_admin: $("#userAdmin").checked};
    const r = await fresh(() => call("users", "POST", body));
    form.reset();
    if (r.admin_needs_two_factor) { ME.user.admin_needs_two_factor = true; adminGate(); }   // not the only account any more
    else await loadUsers();
    showTemp(r.username, r.temporary_password, r.temporary_days, r.id);
  } catch (e) {
    // said beside the form (#499: it was under the list, out of sight); after the check's Cancel (its form went with
    // it: an admin's is the one Create that asks), focus back on Create, in view
    say("#userNewMsg", e.message, true);
    if (!form.contains(document.activeElement)) form.querySelector("[type=submit]").focus({preventScroll: true});
    form.closest(".set-row-ctl").scrollIntoView({block: "nearest"});
  }
};

/* ---------------------------------------------------------------- lab name matches */
/* lab names matched during imports (#23): a review's for its profile, an admin's before for every profile (#927, said
   under From, never which profile); admins change or remove them; never shows results. Those
   to check (#569: remembered before #546 could fill in a look-alike as sure, and not applied until kept) come first,
   marked, with Keep and Forget */
async function loadLabmaps() {
  const [list, names] = await Promise.all([call("labmaps"), call("lab-names").catch(() => [])]);
  $("#labmapNames").replaceChildren(...[...new Set([...names, ...list.map(m => m.target).filter(Boolean)])].map(n => el("option", {value: n})));
  $("#labmaps").hidden = false;
  if (!list.length) { $("#labmapList").replaceChildren(emptyState("None yet: they're made when an import's lab names are matched at its preview.", null, {cls: ""})); return; }   // (#494)
  const as = m => m.target ? `charted as “${m.target}”` : "kept as a series of its own";
  const toCheck = list.filter(m => m.check).length;
  $("#labmapList").replaceChildren(...toCheck ? [el("p", {class: "howto", text: `${toCheck} ${toCheck === 1 ? "match" : "matches"} to check: ` +
    "made before Sanguity stopped filling in look-alike names (Vitamin B6 as Vitamin B1), so not used until kept."})] : [], el("table", {class: "tbl lm"},
    el("thead", {}, el("tr", {}, ...["Reported as", "Goes to", "From", "Added", ""].map(h => el("th", {text: h})))),
    el("tbody", {}, ...list.map(m => {
      const goes = el("td", {class: "lm-target"}, m.target || "its own series", m.check ? el("span", {class: "tag lm-check", text: "To check"}) : null);
      const edit = () => {
        const input = el("input", {type: "text", list: "labmapNames", value: m.target || "", maxlength: 120, "aria-label": `Series for ${m.name}`,
                                   placeholder: "Leave empty to keep it separate"});
        const ok = el("button", {class: "btn small primary", type: "submit", text: "Save"});
        const no = el("button", {class: "btn small", type: "button", text: "Cancel", onclick: () => lmAgain(m.id, "button")});
        goes.replaceChildren(el("form", {class: "lm-edit", onsubmit: async ev => {
          ev.preventDefault();
          try { const r = await call("labmaps/" + m.id, "PATCH", {target: input.value.trim() || null}); say("#labmapMsg", `“${r.name}” is now ${as(r)}.`); lmAgain(m.id, "button"); }
          catch (e) { e.status === 404 ? lmAgain(m.id, "button", e.message) : say("#labmapMsg", e.message, true); }
        }}, input, ok, no));
        input.focus();
      };
      return el("tr", {"data-labmap": m.id},
        el("td", {}, el("b", {text: m.name}), el("span", {class: "muted", text: [m.unit, m.loinc && "LOINC " + m.loinc].filter(Boolean).map(x => " · " + x).join("")})),
        goes,
        el("td", {}, m.label, el("div", {class: "muted", text: m.one_profile ? "for one profile" : "for every profile"})),
        el("td", {}, `${when(m.created)} by ${m.created_by}`, m.changed ? el("div", {class: "muted", text: `changed ${when(m.changed)} by ${m.changed_by}`}) : null),
        el("td", {class: "acts"},
          m.check ? el("button", {class: "btn small primary", type: "button", text: "Keep", onclick: async () => {
            // only the match shown (#575: refused, 409, once another admin has changed it)
            try { const r = await call("labmaps/" + m.id, "PATCH", {target: m.target, keep: true}); say("#labmapMsg", `Kept: “${r.name}” is ${as(r)}.`); lmAgain(m.id, "button"); }
            catch (e) { e.status === 404 || e.status === 409 ? lmAgain(m.id, "button", e.message) : say("#labmapMsg", e.message, true); }
          }}) : null,
          el("button", {class: "btn small", type: "button", text: "Change", onclick: edit}),
          el("button", {class: "btn small", type: "button", text: m.check ? "Forget" : "Remove", onclick: async () => {
            if (!await ask({title: `Forget the match for “${m.name}”?`,
                            message: "The next import that has it will ask again. Results already saved keep their series.",
                            action: "Forget match", danger: true})) return;
            try { await call("labmaps/" + m.id, "DELETE"); say("#labmapMsg", `Forgot the match for “${m.name}”.`); lmAgain(m.id, "button:last-child"); }
            catch (e) { e.status === 404 ? lmAgain(m.id, "button:last-child", e.message) : say("#labmapMsg", e.message, true); }
          }})));
    }))));
}

/* the matches drawn again after a Save, Cancel or Remove (#528: focus fell to the page): focus on the same button of
   its row (sel: Change, or Remove), else the next row's or the one before's, as Passkeys' (util.js focusNear); one
   removed elsewhere (gone: what the server said), that said where it was. Its row found by id where it's still there:
   a match changed moves to the top (newest first), and by its old place focus went to another's Change; one Keep
   refused (#575: changed elsewhere), said above its row where it is now */
async function lmAgain(id, sel, gone) {
  const rows = () => [...$("#labmapList").querySelectorAll("tr[data-labmap]")], find = () => rows().findIndex(r => r.dataset.labmap === String(id));
  const at = Math.max(0, find());
  await loadLabmaps();
  sel = "td.acts " + sel;
  if (gone) return goneSay(rows(), find() < 0 ? at : find(), gone, sel, $("#labmaps h2"), $("#labmapList"));
  focusNear(rows(), find() < 0 ? at : find(), sel, $("#labmaps h2"));
}

/* ---------------------------------------------------------------- requiring two-factor sign-in, install activity */
async function loadRequireTf() {
  const req = $("#reqTf");   // its replies in #reqTfMsg, right under it (#323): #userMsg is below the list
  req.checked = (await call("settings")).require_2fa;
  req.onchange = async () => {
    // turning it on is asked first while some accounts have no second factor (#314): each is then refused on every
    // request until it sets one up (server/main.py, user). Turning it off isn't: it takes nothing away
    if (req.checked) {
      let list;
      try { list = await call("users"); } catch (e) { req.checked = false; say("#reqTfMsg", e.message, true); return; }
      const without = list.filter(u => !u.disabled && !u.two_factor), n = without.length;
      // without one of your own the server refuses it (main.py, change_server_settings): said at once, not asked (#318)
      if (without.some(u => u.id === ME.user.id)) {
        req.checked = false;
        say("#reqTfMsg", ["To require it for everyone, first turn on two-factor sign-in (the authenticator app) or add a passkey " +
          "for your own account, under ", el("a", {href: "account.html#security", text: "Account → Sign-in & security"}), "."], true);
        return;
      }
      if (n && !await ask({title: "Require two-factor sign-in for everyone?", danger: true, action: "Require it", message: [
          `${n === 1 ? "1 account doesn't" : `${n} accounts don't`} have a second factor yet (an authenticator app or a passkey).`,
          "An account without one can't open any records until it has one: Sanguity asks it to set one up first. " +
          "You can turn this off again at any time."]})) { req.checked = false; return; }
    }
    try {   // (#1006: the fresh check, as every server setting's change)
      await fresh(() => call("settings", "PATCH", {require_2fa: req.checked}));
      say("#reqTfMsg", req.checked ? "Everyone now needs two-factor sign-in." : "Two-factor sign-in is now optional.");
    }
    catch (e) { req.checked = !req.checked; say("#reqTfMsg", e.message, true); }
    if (!document.activeElement || document.activeElement === document.body) req.focus({preventScroll: true});   // (the check's form gone)
  };
}
/* account events only: who created, disabled or reset which account, and the server's own changes (no records) */
function loadInstallLog() {
  $("#acctLog").replaceChildren(activityPanel("activity", null, {open: true}));
}

/* ---------------------------------------------------------------- start */
(async () => {
  try { ME = await call("me"); } catch (e) { return; }
  await datesReady();   // dates in this person's time zone and format (dates.js)
  if (ME.user.must_change_pw) { location.replace("account.html#password"); return; }
  if (ME.user.needs_two_factor) { location.replace("account.html#twofa"); return; }
  $("#who").textContent = ME.user.username;
  { const w = plainHttpWarning(); if (w) { $("#insecure").textContent = w; $("#insecure").hidden = false; } }
  if (!ME.user.is_admin) { $("#notAdmin").hidden = false; return; }
  for (const g of GROUPS) for (const id of g.has) $("#" + id).hidden = false;
  $("#intro").hidden = false;
  AdminNav.start();
  SgOutside.load();     // outside.js
  loadDirectory();      // admin-sources.js
  if (ME.user.admin_needs_two_factor) { adminGate(); AdminNav.again(); return; }
  loadInstallLog();
  await Promise.all([loadUsers(), loadNotice(), loadLabmaps(), loadRequireTf(), loadSetup(), loadSync(), loadBackups()]);
  AdminNav.again();
})();
