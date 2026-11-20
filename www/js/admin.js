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
    e.detail = d; throw e;
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
  box.hidden = !n;
  if (!n) return;
  const list = (rows, key) => rows.map(r => `${r[key]} (${r.failures})`).join(", ");
  const msg = el("span", {class: "msg"});   // a Dismiss that didn't work says so here (#210: not in a pop-up)
  box.replaceChildren(
    el("b", {text: `${n.total} failed sign-in${n.total === 1 ? "" : "s"} since ${when(n.since)}`}), " (last at ", when(n.last), "). ",
    n.addresses.length ? `From: ${list(n.addresses, "ip")}. ` : "",
    n.accounts.length ? `Accounts tried: ${list(n.accounts, "username")}. ` : "",
    "Throttling slows guessing down; if you don't recognise this, check that the accounts tried have strong passwords " +
    "or two-factor sign-in, and look at the app's log (docker compose logs app) for details. ",
    el("button", {class: "btn small", type: "button", text: "Dismiss", onclick: async () => {
      try { await call("security/notice/dismiss", "POST"); box.hidden = true; } catch (e) { sayIn(msg, e.message, true); } }}),
    msg);
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
  $("#secNotice").hidden = $("#syncNotice").hidden = $("#backupNotice").hidden = true;
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
/* resetting a password, turning off someone's 2FA or making an admin needs a fresh check (the password, a code or a
   passkey within a few minutes): when the server asks, ask the person, then try again. box: where to ask (Backups
   has its own, #200) */
async function fresh(action, box = $("#reauth")) {
  try { return await action(); }
  catch (e) { if (e.detail !== "reauth_required") throw e; }
  await confirmIdentity(box);
  return action();
}
function confirmIdentity(box = $("#reauth")) {
  return new Promise((resolve, reject) => {
    const msg = el("span", {class: "msg", role: "status"});
    const secret = el("input", {type: "password", autocomplete: "current-password", placeholder: "Password or code",
      "aria-label": "Your password, or a code from your authenticator app", required: true});
    const done = ok => { box.hidden = true; box.replaceChildren(); ok ? resolve() : reject(new Error("Not done: you didn't confirm it's you.")); };
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
  say("#userMsg", [`One-time password for ${username}: `, el("code", {text: pw}),
    ` — give it to them privately; it isn't shown again, and stops working if not used within ${days || 7} days.`]);
}
/* the accounts (#287): a row each, its name with badges (admin, two-factor, disabled; "(you)" on your own), where it's
   signed in, and a ⋯ menu of what can be done to it (util.js rowMenu, as Settings' rows). Your own row has none: none
   of those can be done to your own account here. Removing one is the danger zone's, under the list. On a phone each row
   is a little card, each value under its column's name (util.js labelCells, #320). */
const devices = n => n ? plural(n, "device") : "no device";
async function loadUsers() {
  const list = await call("users"), mine = document.activeElement?.closest?.("#userList tr[data-user]")?.dataset.user;
  loadOwnerless();
  $("#userList").replaceChildren(el("table", {class: "tbl users"},
    el("thead", {}, el("tr", {}, ...["Account", "Signed in on", ""].map(h => el("th", {text: h})))),
    el("tbody", {}, ...list.map(u => {
      const self = u.id === ME.user.id;
      return el("tr", {"data-user": u.id},
        el("td", {class: "who"}, el("b", {text: u.username}), self ? el("span", {class: "muted", text: " (you)"}) : null,
          u.is_admin ? el("span", {class: "tag", text: "admin"}) : null,
          u.two_factor ? el("span", {class: "tag", text: "two-factor"}) : null,
          u.disabled ? el("span", {class: "tag off", text: "disabled"}) : null,
          !u.disabled && u.must_change_pw ? el("span", {class: "muted new-pw", text: "Hasn't chosen a password yet"}) : null),
        el("td", {text: devices(u.sessions)}),
        el("td", {class: "acts"}, self ? null : userMenu(u)));
    }))));
  accountsZone(list.filter(u => u.id !== ME.user.id));
  // a change from a row's menu redraws the list: focus back on that row's ⋯
  if (mine && document.activeElement === document.body)
    $(`#userList tr[data-user="${mine}"] .set-more-btn`)?.focus({preventScroll: true});
}
/* a row's ⋯: each change but Enable is asked first (#249), before any fresh check, so Cancel sends nothing */
function userMenu(u) {
  const n = u.username;
  return rowMenu(`Manage ${n}`, [
    u.is_admin
      ? {text: "Remove admin…", run: () => changeUser(u, {is_admin: false}, {title: `Remove ${n}'s admin role?`,
          message: "They keep their account and the profiles shared with them, but can't use the Admin page any more.", action: "Remove admin"})}
      : {text: "Make admin…", run: () => changeUser(u, {is_admin: true}, {title: `Make ${n} an admin?`, message: [
          "Admins look after every account on this server: they can add, disable and remove accounts, reset passwords and " +
          "turn off two-factor sign-in, and look after backups and sources.",
          "They don't see a profile's records unless it's shared with them. They'll need two-factor sign-in of their own to use the Admin page."],
          action: "Make admin", danger: true})},
    {text: "Reset password…", run: () => changeUser(u, {reset_password: true}, {title: `Reset ${n}'s password?`, message: [
      "Their password stops working at once, and they're signed out everywhere.",
      "You'll get a one-time password to give them; they choose a new one when they first sign in with it." +
      (u.two_factor ? " Their two-factor sign-in stays on." : "")], action: "Reset password", danger: true},
      r => setTimeout(() => showTemp(n, r.temporary_password, r.temporary_days)))},
    u.two_factor ? {text: "Turn off two-factor sign-in…", run: () => changeUser(u, {disable_2fa: true}, {title: `Turn off two-factor sign-in for ${n}?`,
      message: ["Do this only if they lost both their phone and their recovery codes.", "They'll be signed out everywhere."], action: "Turn off", danger: true})} : false,
    u.disabled
      ? {text: "Enable", run: () => changeUser(u, {disabled: false})}
      : {text: "Disable…", run: () => changeUser(u, {disabled: true}, {title: `Disable ${n}?`, message: [
          "They're signed out everywhere and can't sign in until an admin enables the account again.",
          "Their profiles and records stay as they are."], action: "Disable", danger: true})},
    null,
    {text: "Remove account…", danger: true, run: () => { $("#rmUser").value = u.id; USERZONE.show(n); }}].filter(e => e !== false), "user", "user-more");
}
async function changeUser(u, body, sure, done) {
  if (sure && !await ask(sure)) return;
  try {
    let r;
    try { r = await fresh(() => call("users/" + u.id, "PATCH", body)); }
    catch (e) {   // disabling the only owner of a profile: the server says which, and it's asked once more
      if (!(e.detail && e.detail.sole_owner_of)) throw e;
      if (!await ask({title: `Disable ${u.username} anyway?`, message: e.message, action: "Disable anyway", danger: true})) throw new Error("Not disabled.");
      r = await call("users/" + u.id, "PATCH", {...body, force: true});
    }
    done && done(r); await loadUsers();
  } catch (e) { say("#userMsg", e.message, true); }
}
/* profiles nobody can manage (every owner disabled or removed): an admin names a new owner; never shows records */
async function loadOwnerless() {
  const list = await call("ownerless"), box = $("#ownerless");
  if (!list.length) { box.replaceChildren(); return; }
  box.replaceChildren(el("div", {class: "ownerless"},
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
        if (who.toLowerCase() === ME.user.username.toLowerCase() &&
            !await ask({title: `Own ${p.name} yourself?`, message: `This gives you access to the records in ${p.name}.`, action: "Make me owner"})) return;
        try { await call(`ownerless/${p.id}/owner`, "POST", {username: who}); say("#userMsg", `${who} now owns ${p.name}.`); loadOwnerless(); }
        catch (e) { say("#userMsg", e.message, true); }
      }});
      return el("div", {class: "row"}, el("b", {text: p.name}),
        el("span", {class: "muted", text: p.members.length ? p.members.map(m => `${m.username} (${m.role}${m.disabled ? ", disabled" : ""})`).join(", ") : "no members"}),
        pick, other, go);
    })));
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
async function removeAccount(u, typed) {
  let body = {confirm_name: typed};
  for (;;) {
    try {
      const r = await call("users/" + u.id, "DELETE", body);
      $("#rmUser").value = "";
      await loadUsers();
      sayIn(USERZONE.msg, `Removed ${u.username}.` + (r.handed_over.length ? ` ${body.hand_to} now owns ${r.handed_over.join(", ")}.` : ""));
      $("#rmUser").focus();   // the box it was typed in went with it
      return;
    } catch (e) {
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
  try {
    const body = {username: $("#userName").value.trim(), is_admin: $("#userAdmin").checked};
    const r = await fresh(() => call("users", "POST", body));
    $("#userNew").reset();
    if (r.admin_needs_two_factor) { ME.user.admin_needs_two_factor = true; adminGate(); }   // not the only account any more
    else await loadUsers();
    showTemp(r.username, r.temporary_password, r.temporary_days);
  } catch (e) { say("#userMsg", e.message, true); }
};

/* ---------------------------------------------------------------- lab name matches */
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
            if (!await ask({title: `Forget the match for “${m.name}”?`,
                            message: "The next import that has it will ask again. Results already saved keep their series.",
                            action: "Forget match", danger: true})) return;
            try { await call("labmaps/" + m.id, "DELETE"); say("#labmapMsg", `Forgot the match for “${m.name}”.`); loadLabmaps(); }
            catch (e) { say("#labmapMsg", e.message, true); }
          }})));
    }))));
}

/* ---------------------------------------------------------------- requiring two-factor sign-in, install activity */
async function loadRequireTf() {
  const req = $("#reqTf");   // its replies in #reqTfMsg, right under it (#323): #userMsg is below the account form
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
    try { await call("settings", "PATCH", {require_2fa: req.checked}); say("#reqTfMsg", req.checked ? "Everyone now needs two-factor sign-in." : "Two-factor sign-in is now optional."); }
    catch (e) { req.checked = !req.checked; say("#reqTfMsg", e.message, true); }
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
