/* Sanguity · the Sharing page (#168): who can see a profile, and every way its records go out, for the profile picked
   at the top, with a sidebar of groups (as Settings' and Admin's): People (who has access and as what, sharing with
   an account, changing roles, leaving), Share links (share-links.js), Calendar feed (calendar-feed.js), Visit summary
   (summary.js), Export everything and the profile's Activity (activity.js). Set apart from sharing, under Profile:
   renaming and deleting it (owners) and making a new one.

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
  const sel = $("#profile"), main = $("main.sets");
  $("#logout").onclick = async () => { try { await call("logout", "POST"); } catch (e) {} location.replace("login.html"); };

  /* ---------------------------------------------------------------- the sidebar: one group shown at a time */
  const RANK = {viewer: 0, editor: 1, owner: 2};
  const GROUPS = [
    {id: "people", title: "People", has: ["people"]},
    {id: "links", title: "Share links", has: ["links"], needs: "owner", why: "Share links are made by a profile's owners."},
    {id: "calendar", title: "Calendar feed", has: ["calendar"]},
    {id: "summary", title: "Visit summary", has: ["summaryIntro", "build", "preview"]},
    {id: "export", title: "Export everything", has: ["export"], needs: "editor", why: "A profile's owners and editors can export it."},
    {id: "activity", title: "Activity", has: ["activity"], needs: "owner", why: "A profile's activity is for its owners."},
    {id: "profile", title: "Profile", part: "Profiles", has: ["profileCard", "newProfile"]},
  ];
  const ALIAS = {profiles: "people"};   // the old Account tab's name (account.html#profiles)
  const PROFILE = GROUPS.at(-1);
  const allowed = g => P ? RANK[P.role] >= RANK[g.needs || "viewer"] : g === PROFILE;
  const list = el("ul", {class: "set-nav-list"});
  const nav = el("nav", {class: "set-nav", id: "shrNav", "aria-label": "Sharing sections"}, list);
  let current = null;

  function drawNav() {
    const rows = [];
    let part = null;
    for (const g of GROUPS.filter(allowed)) {
      const p = g.part || "Sharing";
      if (p !== part) { rows.push(el("li", {class: "set-nav-part", "aria-hidden": "true", text: p})); part = p; }
      rows.push(el("li", {class: g.part ? "shr-apart" : null}, el("a", {href: "#" + g.id, "data-group": g.id, text: g.title,
        onclick: ev => { ev.preventDefault(); show(g, {push: true}); }})));
    }
    list.replaceChildren(...rows);
  }
  const groupOf = id => GROUPS.find(g => g.has.includes(id));
  /* #links: a group; #calendar or #build: the group holding it, scrolled to it */
  function fromHash() {
    const h = decodeURIComponent(location.hash.slice(1)), g = GROUPS.find(x => x.id === (ALIAS[h] || h));
    if (g) return {g};
    const t = h && document.getElementById(h);
    let of = null;
    for (let n = t; n && n !== main && !of; n = n.parentElement) of = groupOf(n.id);
    return of ? {g: of, el: t} : {g: null};
  }
  function show(g, {push = false, scrollTo = null} = {}) {
    if (!g || !allowed(g)) {
      if (g && g.why && P) { sayIn($("#pageMsg"), g.why); push = true; }
      g = GROUPS.find(allowed);
    }
    const was = current;
    current = g;
    if (g.id === "activity" && g !== was) drawActivity();   // fresh each time it's opened
    for (const x of GROUPS) for (const id of x.has) document.getElementById(id).classList.toggle("grp-off", x !== g);
    for (const a of list.querySelectorAll("a")) a.setAttribute("aria-current", a.dataset.group === g.id ? "page" : "false");
    main.dataset.group = g.id;
    $("#groupTitle").textContent = g.title;
    viewTitle("Sharing: " + g.title, push && "Sharing: " + g.title);   // the browser tab's title; said when picked (#209)
    // on a phone the sidebar is one row that scrolls sideways: keep the group shown in sight
    const a = list.querySelector(`a[data-group="${g.id}"]`), at = a && a.getBoundingClientRect(), box = list.getBoundingClientRect();
    if (at && (at.left < box.left || at.right > box.right)) list.scrollLeft += at.left - box.left - 8;
    if (push && location.hash.slice(1) !== g.id) history.replaceState(null, "", "#" + g.id);
    if (scrollTo && scrollTo !== document.getElementById(g.id)) scrollTo.scrollIntoView({block: "start"});
    else if (push) window.scrollTo({top: 0});
  }
  const follow = () => { const r = fromHash(); if (r.g) { sayIn($("#pageMsg"), ""); show(r.g, {scrollTo: r.el}); } };

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
    fill(want);
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
      drawNav(); show(PROFILE);
      return;
    }
    SgNav.profiles(ME.profiles, want);   // the top bar's picker (nav.js, #170): want, else the address's or this browser's
    pick();
  }
  async function pick() {
    const p = P = ME.profiles.find(x => x.id === +sel.value);
    const [a, b] = SAYS[p.role];
    $("#introHint").replaceChildren(el("strong", {text: a}), b(p.name));
    for (const g of GROUPS) if (g.needs) for (const id of g.has) $("#" + id).hidden = !allowed(g);
    drawNav();
    if (current) show(current); else { const r = fromHash(); show(r.g, {scrollTo: r.el}); }
    $("#exportLink").href = `api/profiles/${p.id}/export`;
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
    if (current.id === "activity") drawActivity();
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
        el("td", {}, m.username, m.user_id === me ? el("span", {class: "muted", text: " (you)"}) : null),
        el("td", {}, owner ? roleSelect(m.role, async ev => {
          try { await call(`profiles/${p.id}/members`, "PUT", {username: m.username, role: ev.target.value}); await again(); sayIn($("#peopleMsg"), `${m.username} is now ${ev.target.value === "owner" ? "an" : "a"} ${ev.target.value}.`); }
          catch (e) { ev.target.value = m.role; fail(e); } }, `${m.username}'s role`) : m.role),
        el("td", {class: "acts"}, m.user_id === me ? el("button", {class: "btn small", type: "button", text: "Leave",
          onclick: async () => {
            if (!await ask({title: `Leave ${p.name}?`, message: "You'll stop having access to its records." + (owner ? "" : " An owner can share it with you again."),
                            action: "Leave profile", danger: true})) return;
            leave(p); }})
          : owner ? el("button", {class: "btn small", type: "button", text: "Remove", "aria-label": `Remove ${m.username}`,
            onclick: async () => {
              if (!await ask({title: `Remove ${m.username}'s access?`, message: `They'll stop seeing ${p.name}'s records. You can share it with them again later.`,
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
      // sharing with someone who already has access would quietly change their role: say so first
      const name = who.value.trim(), had = MEMBERS.find(m => m.username.toLowerCase() === name.toLowerCase());
      if (had && had.role === role.value) { fail(new Error(`${had.username} already has access as ${had.role}.`)); return; }
      if (had && !await ask({title: `Change ${had.username} to ${role.value}?`, message: `${had.username} already has access to ${p.name} as ${had.role}.`,
                             action: "Change role"})) return;
      try { await call(`profiles/${p.id}/members`, "PUT", {username: name, role: role.value}); await again(); sayIn($("#peopleMsg"), `Shared with ${name} as ${role.value}.`); }
      catch (e) { fail(e); } }},
      el("span", {class: "muted", text: "Share with"}), who, "as", role, el("button", {class: "btn small", type: "submit", text: "Share"})));
  }

  /* ---------------------------------------------------------------- Profile: rename, delete (owners), new */
  function drawProfileCard(p) {
    const msg = $("#profileMsg"), fail = e => sayIn(msg, e.message, true);
    sayIn(msg, "");
    if (p.role !== "owner") {
      $("#profileBody").replaceChildren(el("p", {class: "hint", text: `Only ${p.name}'s owners can rename or delete it.`}));
      return;
    }
    const name = el("input", {value: p.name, maxlength: 80, "aria-label": "Profile name"});
    $("#profileBody").replaceChildren(
      el("form", {class: "row", onsubmit: async ev => { ev.preventDefault();
        try { await call("profiles/" + p.id, "PATCH", {name: name.value}); await refresh(p.id); sayIn($("#profileMsg"), "Renamed."); } catch (e) { fail(e); } }},
        name, el("button", {class: "btn small", type: "submit", text: "Rename"})),
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

  /* ---------------------------------------------------------------- start */
  (async () => {
    try { ME = await call("me"); } catch (e) { return; }
    await datesReady();   // dates in this person's time zone and format (dates.js), before summary.js draws any
    if (ME.user.must_change_pw) { location.replace("account.html#password"); return; }
    if (ME.user.needs_two_factor) { location.replace("account.html#twofa"); return; }
    $("#who").textContent = ME.user.username;
    { const w = plainHttpWarning(); if (w) { $("#insecure").textContent = w; $("#insecure").hidden = false; } }
    main.prepend(nav);
    main.classList.add("with-nav");
    fill();   // the profile the address names, else this browser's last (nav.js)
    addEventListener("hashchange", follow);
  })();
})();
