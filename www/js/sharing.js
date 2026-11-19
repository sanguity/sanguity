/* Sanguity · the Sharing page (#168): who can see a profile, and every way its records go out, for the profile picked
   at the top, with the sidebar of groups every account page has (sidebar.js, #266): People (who has access and as
   what, sharing with an account, changing roles, leaving), Share links (share-links.js), Calendar feed
   (calendar-feed.js), Visit summary (summary.js), Export everything and the profile's Activity (activity.js). Set
   apart from sharing, under Profiles: renaming and deleting it (owners) and making a new one. About this person and
   Targets are on Settings now (#267; about.js), and their old addresses (sharing.html#about) send you there (moved.js).

   Someone who can only view a profile sees what they may (who has access, the visit summary, their own calendar
   feed) and no owner's controls: the groups they can't use aren't in the sidebar. The address follows the group
   (sharing.html#links); an address naming a part of one (#calendar, #build) opens that group. The old addresses
   (account.html#profiles, summary.html, settings.html#calendar) send you here. */
"use strict";   // $, el and api come from util.js
/* api() from util.js, returning the JSON answer (null for none), or throwing with the server's message. Also the
   call() that share-links.js, activity.js and summary.js use. */
async function call(path, method = "GET", body) {
  const r = await api(path, {method, body: body === undefined ? undefined : JSON.stringify(body)});
  if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
  const j = r.status === 204 ? null : await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = j && j.detail, e = new Error(Array.isArray(d) ? "Please check the fields." : (d && d.message) || d || r.status + " " + r.statusText);
    e.detail = d; throw e;
  }
  return j;
}
const when = t => fmtDT(t * 1000);   // dates.js: this person's time zone and date format

(() => {
  let ME = null, P = null, MEMBERS = [], MEMBERS_OF = null;   // MEMBERS_OF: the profile MEMBERS are of
  const sel = $("#profile");
  $("#logout").onclick = logOut;   // util.js: asks first about what isn't saved (#391)

  /* ---------------------------------------------------------------- the sidebar: one group shown at a time */
  const RANK = {viewer: 0, editor: 1, owner: 2};
  const GROUPS = [
    {id: "people", title: "People", icon: "users", part: "Sharing", has: ["people"]},
    {id: "links", title: "Share links", icon: "share-2", part: "Sharing", has: ["links"], needs: "owner", why: "Share links are made by a profile's owners."},
    {id: "calendar", title: "Calendar feed", icon: "calendar", part: "Sharing", has: ["calendar"]},
    {id: "summary", title: "Visit summary", icon: "file-text", part: "Sharing", has: ["summaryIntro", "build", "preview"]},
    {id: "export", title: "Export everything", icon: "download", part: "Sharing", has: ["export"], needs: "editor", why: "A profile's owners and editors can export it."},
    {id: "activity", title: "Activity", icon: "history", part: "Sharing", has: ["activity"], needs: "owner", why: "A profile's activity is for its owners."},
    {id: "profile", title: "Profile", icon: "circle-user", part: "Profiles", has: ["profileCard", "newProfile"]},
  ];
  const PROFILE = GROUPS.at(-1);
  const allowed = g => P ? RANK[P.role] >= RANK[g.needs || "viewer"] : g === PROFILE;
  const nav = sideNav({id: "shrNav", page: "Sharing", groups: GROUPS, find: "Find in Sharing", none: q => `Nothing in Sharing matches “${q}”.`,
    allowed, alias: {profiles: "people"},   // the old Account tab's name (account.html#profiles)
    refuse: g => { if (g.why && P) { sayIn($("#pageMsg"), g.why); return true; } },
    shown: g => { if (g.id === "activity") drawActivity(); }});   // fresh each time it's opened
  let started = false;
  /* the groups this profile's role allows, the one shown kept if it's one of them (the address's, the first time) */
  const showAllowed = () => { if (started) nav.show(nav.current()); else { started = true; nav.start(); } };

  /* ---------------------------------------------------------------- the profile picked, and what you can do with it */
  const SAYS = {
    owner: ["You're an owner", n => ` of ${n}: you choose who sees it and how it's shared.`],
    editor: ["You're an editor", n => ` of ${n}: you can change its records and export them. Its owners choose who sees it.`],
    viewer: ["You can view", n => ` ${n}: you can make a visit summary and a calendar feed of your own. Its owners choose who sees it.`],
  };
  /* the page's own note: above the groups, or with no profile left, under New profile */
  const note = (text, bad) => sayIn($("#intro").hidden ? $("#profMsg") : $("#pageMsg"), text, bad);

  async function refresh(want) {
    ME = await call("me");
    await fill(want);
  }
  function fill(want) {
    const has = ME.profiles.length > 0;
    $("#noProfiles").hidden = has;
    $("#intro").hidden = !has;
    $("#newProfile").hidden = false;
    for (const id of ["people", "profileCard", "export", "links", "activity"]) $("#" + id).hidden = !has;
    if (!has) {
      P = null;
      SgNav.profiles([]);
      for (const id of ["calendar", "summaryIntro", "build", "preview"]) $("#" + id).hidden = true;
      showAllowed();
      return;
    }
    SgNav.profiles(ME.profiles, want);   // the top bar's picker (nav.js, #170): want, else the address's or this browser's
    return pick();   // refresh waits for it: what's asked next about a role reads the people it draws (#249)
  }
  async function pick() {
    const p = P = ME.profiles.find(x => x.id === +sel.value);
    const [a, b] = SAYS[p.role];
    $("#introHint").replaceChildren(el("strong", {text: a}), b(p.name));
    for (const g of GROUPS) if (g.needs) for (const id of g.has) $("#" + id).hidden = !allowed(g);
    showAllowed();
    $("#exportLink").href = `api/profiles/${p.id}/export`;
    exportCycle(p);   // #183
    drawProfileCard(p);
    if (p.role === "owner") $("#linksBody").replaceChildren(shareLinksPanel(p, {open: true}));   // share-links.js
    else $("#linksBody").replaceChildren();
    sayIn($("#peopleMsg"), "");
    MEMBERS_OF = null;
    $("#actBody").replaceChildren();
    let got = [];
    try { got = await call(`profiles/${p.id}/members`); }
    catch (e) { sayIn($("#peopleMsg"), e.message, true); }
    if (P !== p) return;   // another profile was picked meanwhile
    MEMBERS = got; MEMBERS_OF = p.id;
    drawPeople(p);
    $("#people").dataset.profile = p.id;   // whose people are drawn (the browser check waits for it)
    if (nav.current()?.id === "activity") drawActivity();
  }
  /* the profile's activity (owners), drawn when its group is shown, once its people are known (for the filter) */
  function drawActivity() {
    if (!P || P.role !== "owner" || MEMBERS_OF !== P.id) return;
    $("#actBody").replaceChildren(activityPanel(`profiles/${P.id}/activity`, MEMBERS, {open: true}));   // activity.js
  }
  sel.addEventListener("change", () => { sayIn($("#pageMsg"), ""); pick(); });

  /* ---------------------------------------------------------------- People: who has access and as what */
  const ROLES = ["owner", "editor", "viewer"];
  function roleSelect(value, onchange, label = "Role") {
    const s = el("select", {"aria-label": label, onchange}, ...ROLES.map(r => el("option", {value: r, text: r})));
    s.value = value; return s;
  }
  /* a role change that's hard to take back is asked first (#249): giving someone owner, taking it away, or changing
     your own (only an owner can, so it's a step down, and only another owner can undo it). Another person's
     editor/viewer change isn't: either of you can change it back (otherwise: what's asked then, if anything). m may be
     someone not yet shared with ({username}). Resolves true to go ahead. Owner is a grant as serious as Admin's
     Make admin, so its question starts on Cancel too (#314). */
  const an = r => (r === "owner" || r === "editor" ? "an " : "a ") + r;
  function sureOfRole(p, m, to, otherwise = null) {
    if (m.user_id === ME.user.id) {
      // the last owner whose account is enabled, as the server counts them (dbm.active_owners, #314): it says why not
      if (!MEMBERS.some(x => x.role === "owner" && x.user_id !== m.user_id && !x.disabled)) return otherwise ? otherwise() : true;
      return ask({title: `Make yourself ${an(to)} of ${p.name}?`, action: "Change my role", danger: true, message: [
        to === "viewer" ? `You'll only be able to view ${p.name}: no longer change or export its records, see or change its ` +
                          "cycle tracking, share it, change who has access, make share links, see its activity, or rename or delete it."
          : `You'll no longer be able to share ${p.name}, change who has access, make share links, see its activity, turn cycle ` +
            "tracking on or off or delete what it kept, or rename or delete it. You can still change its records and export them.",
        "Only another owner can make you an owner again."]});
    }
    if (to === "owner") return ask({title: `Make ${m.username} an owner of ${p.name}?`, action: "Make owner", danger: true, message:
      `Owners choose who sees ${p.name}: ${m.username} could share it, change anyone's role (yours too), rename it and delete it.`});
    if (m.role === "owner") return ask({title: `Make ${m.username} ${an(to)} of ${p.name}?`, action: "Change role", message:
      `${m.username} will no longer be able to share it, change who has access, or rename or delete it.`});
    return otherwise ? otherwise() : true;
  }
  async function leave(p) {
    try { await call(`profiles/${p.id}/members/${ME.user.id}`, "DELETE"); await refresh(null); note(`You no longer have access to ${p.name}.`); }
    catch (e) { sayIn($("#peopleMsg"), e.message, true); }
  }
  function drawPeople(p) {
    const msg = $("#peopleMsg"), fail = e => sayIn(msg, e.message, true);
    const owner = p.role === "owner", me = ME.user.id;
    const again = async () => { await refresh(p.id); };   // a role changed: yours too, perhaps
    $("#memberList").replaceChildren(el("table", {class: "tbl shr-members"},
      el("thead", {}, el("tr", {}, ...["Person", "Role", ""].map(h => el("th", {text: h})))),
      el("tbody", {}, ...MEMBERS.map(m => el("tr", {},
        el("td", {}, m.username, m.user_id === me ? el("span", {class: "muted", text: " (you)"}) : null,
          m.disabled ? el("span", {class: "muted", text: " (account disabled)"}) : null),   // owners are told (#314)
        el("td", {}, owner ? roleSelect(m.role, async ev => {
          if (!await sureOfRole(p, m, ev.target.value)) { ev.target.value = m.role; return; }
          try { await call(`profiles/${p.id}/members`, "PUT", {username: m.username, role: ev.target.value}); await again(); sayIn($("#peopleMsg"), `${m.username} is now ${an(ev.target.value)}.`); }
          catch (e) { ev.target.value = m.role; fail(e); } }, `${m.username}'s role`) : m.role),
        el("td", {class: "acts"}, m.user_id === me ? el("button", {class: "btn small", type: "button", text: "Leave",
          onclick: async () => {
            if (!await ask({title: `Leave ${p.name}?`, message: "You'll stop having access to its records." + (owner ? "" : " An owner can share it with you again."),
                            action: "Leave profile", danger: true})) return;
            leave(p); }})
          : owner ? el("button", {class: "btn small", type: "button", text: "Remove", "aria-label": `Remove ${m.username}`,
            onclick: async () => {
              if (!await ask({title: `Remove ${m.username}'s access?`, message: `They'll stop seeing the records in ${p.name}. You can share it with them again later.`,
                              action: "Remove access", danger: true})) return;
              try { await call(`profiles/${p.id}/members/${m.user_id}`, "DELETE"); await again(); sayIn($("#peopleMsg"), `${m.username} no longer has access.`); } catch (e) { fail(e); } }})
          : null))))));
    if (!owner) {
      const owners = MEMBERS.filter(m => m.role === "owner").map(m => m.username);
      $("#shareWith").replaceChildren(el("p", {class: "hint", text: "Only its owners can share it or change who has access" +
        (owners.length ? `: ask ${owners.join(" or ")}.` : ".")}));
      return;
    }
    const who = el("input", {placeholder: "username", autocapitalize: "none", spellcheck: "false", "aria-label": "Share with username", required: true});
    const role = roleSelect("viewer", null, "Role to share as");
    $("#shareWith").replaceChildren(el("form", {class: "row shr-share", onsubmit: async ev => { ev.preventDefault();
      // sharing with someone who already has access would quietly change their role: say so first; owner is asked as above
      const name = who.value.trim(), had = MEMBERS.find(m => m.username.toLowerCase() === name.toLowerCase());
      if (had && had.role === role.value) { fail(new Error(`${had.username} already has access as ${had.role}.`)); return; }
      if (!await sureOfRole(p, had || {username: name}, role.value, had && (() => ask({title: `Change ${had.username} to ${role.value}?`,
                            message: `${had.username} already has access to ${p.name} as ${had.role}.`, action: "Change role"})))) return;
      try { await call(`profiles/${p.id}/members`, "PUT", {username: name, role: role.value}); await again(); sayIn($("#peopleMsg"), `Shared with ${name} as ${role.value}.`); }
      catch (e) { fail(e); } }},
      el("span", {class: "muted", text: "Share with"}), who, "as", role, el("button", {class: "btn", type: "submit", text: "Share"})));
  }

  /* ---------------------------------------------------------------- Profile: rename, delete (owners), new */
  function drawProfileCard(p) {
    const msg = $("#profileMsg"), fail = e => sayIn(msg, e.message, true);
    sayIn(msg, "");
    if (p.role !== "owner") {
      $("#profileBody").replaceChildren(el("p", {class: "hint", text: `Only the owners of ${p.name} can rename or delete it.`}));
      return;
    }
    const name = el("input", {value: p.name, maxlength: 80, "aria-label": "Profile name"});
    $("#profileBody").replaceChildren(
      el("form", {class: "row", onsubmit: async ev => { ev.preventDefault();
        try { await call("profiles/" + p.id, "PATCH", {name: name.value}); await refresh(p.id); sayIn($("#profileMsg"), "Renamed."); } catch (e) { fail(e); } }},
        name, el("button", {class: "btn", type: "submit", text: "Rename"})),
      deletePanel(p, fail));
  }
  function deletePanel(p, fail) {
    const typed = el("input", {autocomplete: "off", spellcheck: "false"});   // no placeholder: it would look filled in
    const go = el("button", {class: "btn small danger", type: "button", text: "Delete it and everything in it", disabled: true});
    typed.oninput = () => { go.disabled = typed.value.trim() !== p.name; };
    go.onclick = async () => {
      go.disabled = true;
      try {
        const r = await call(`profiles/${p.id}`, "DELETE", {confirm_name: typed.value});
        try { if (localStorage.getItem("ht-profile") === String(p.id)) localStorage.removeItem("ht-profile"); } catch (e) {}
        await refresh(null);
        note(`Deleted “${p.name}”.` + (r.unlinked_build ? " Its records came from the setup build, which won't be loaded into a profile again unless an admin asks for it." : ""));
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
  $("#profNew").onsubmit = async ev => {
    ev.preventDefault();
    const name = $("#profName").value.trim();
    try {
      const r = await call("profiles", "POST", {name});
      $("#profNew").reset();
      await refresh(r.id);
      sayIn($("#profMsg"), `Created “${name}”: it's the profile shown here now, so you can share it under People.`);
    } catch (e) { sayIn($("#profMsg"), e.message, true); }
  };

  /* ---------------------------------------------------------------- Export: the cycle records, ticked each time (#183):
     offered to owners and editors only while the profile has cycle tracking on; unticked again once the export starts.
     The format (#205): Sanguity's own zip, or FHIR R4 with the kinds of records ticked (all, by default) */
  async function exportCycle(p) {
    const wrap = $("#exportCycleWrap"), box = $("#exportCycle"), link = $("#exportLink"), base = `api/profiles/${p.id}/export`;
    /* the format (#205): Sanguity's own zip, or a FHIR bundle of the parts ticked (all of them: no include=) */
    const parts = [...document.querySelectorAll("input[name=exportPart]")], fhir = () => $("input[name=exportFormat][value=fhir]").checked;
    const href = () => {
      const q = [];
      if (fhir()) {
        q.push("format=fhir");
        if (parts.some(x => !x.checked)) q.push("include=" + parts.filter(x => x.checked).map(x => x.value).join(","));
      }
      if (box.checked) q.push("cycle=1");
      return base + (q.length ? "?" + q.join("&") : "");
    };
    const draw = () => {
      $("#exportParts").hidden = !fhir();
      link.textContent = fhir() ? "Export as FHIR" : "Export everything";
      link.href = href();
    };
    wrap.hidden = true; box.checked = false;
    for (const x of [box, ...parts, ...document.querySelectorAll("input[name=exportFormat]")]) x.onchange = () => { sayIn($("#exportMsg"), ""); draw(); };
    draw();
    link.onclick = ev => {
      if (fhir() && !parts.some(x => x.checked) && !box.checked) {   // nothing to put in the bundle
        ev.preventDefault();
        sayIn($("#exportMsg"), "Tick at least one kind of record to include.", true);
        return;
      }
      setTimeout(() => { box.checked = false; link.href = href(); }, 0);
    };
    if (p.role !== "owner" && p.role !== "editor") return;
    try { const c = await call(`profiles/${p.id}/cycle`); if (P === p) wrap.hidden = !c.on; } catch (e) {}
  }
  /* ---------------------------------------------------------------- start */
  (async () => {
    if (sgMoved()) return;   // About this person's old address (#267): on Settings now
    try { ME = await call("me"); } catch (e) { return; }
    await datesReady();   // dates in this person's time zone and format (dates.js), before summary.js draws any
    if (ME.user.must_change_pw) { location.replace("account.html#password"); return; }
    if (ME.user.needs_two_factor) { location.replace("account.html#twofa"); return; }
    $("#who").textContent = ME.user.username;
    { const w = plainHttpWarning(); if (w) { $("#insecure").textContent = w; $("#insecure").hidden = false; } }
    addEventListener("hashchange", () => sayIn($("#pageMsg"), ""));   // (before the sidebar's: a group refused says so after)
    fill();   // the profile the address names, else this browser's last (nav.js)
  })();
})();
