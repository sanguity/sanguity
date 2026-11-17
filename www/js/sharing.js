/* Sanguity · the Sharing page (#168): who can see a profile, and every way its records go out, for the profile picked
   at the top, with a sidebar of groups (as Settings' and Admin's): People (who has access and as what, sharing with
   an account, changing roles, leaving), Share links (share-links.js), Calendar feed (calendar-feed.js), Visit summary
   (summary.js), Export everything and the profile's Activity (activity.js). Set apart from sharing, under Profile:
   renaming and deleting it (owners) and making a new one; and About this person (#194): its birth date, sex used for
   reference ranges and height history (person.js), which owners and editors change and viewers see.

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
    {id: "about", title: "About this person", part: "Profiles", has: ["about", "targets"]},   // #194; targets of your own (#234)
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
    for (const id of ["people", "profileCard", "export", "links", "activity", "about", "targets"]) $("#" + id).hidden = !has;
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
    exportCycle(p);   // #183
    drawProfileCard(p);
    drawAbout(p);   // #194
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
  /* ---------------------------------------------------------------- About this person (#194; person.js, server/person.py):
     the birth date and the sex used for reference ranges, saved together; the height history, each entry added or
     removed on its own; and the heights a clinic recorded in the records, beside it, each offered for the history.
     Owners and editors change them; viewers see them as text. Heights show in this person's Units choice, else as
     each was typed, with the other unit beside. */
  let HEIGHT_PREF = null, UNITS_PREF = {};   // this person's Units choices (their own setting), read once: heights; targets' (#234)
  async function drawAbout(p) {
    sayIn($("#aboutMsg"), ""); sayIn($("#targetsMsg"), "");
    let a;
    try {
      if (HEIGHT_PREF == null) { UNITS_PREF = (await call("settings/user")).units || {}; HEIGHT_PREF = UNITS_PREF.height || "reported"; }
      a = await call(`profiles/${p.id}/person`);
    } catch (e) { $("#aboutBody").replaceChildren(); $("#targetsBody").replaceChildren(); sayIn($("#aboutMsg"), e.message, true); return; }
    if (P === p) showAbout(p, a);
  }
  const aboutDay = d => fmtD(zParse(d));
  function showAbout(p, a) {
    const fail = (e, msg = $("#aboutMsg")) => sayIn(msg, e.message, true), edit = a.can_change, today = isoDay(Date.now());
    const age = a.birthDate ? ageOn(a.birthDate, today) : null;
    const hText = h => { const u = heightUnit(HEIGHT_PREF, h.unit);
      return el("span", {}, heightText(h.cm, u), el("span", {class: "muted", text: ` (${heightText(h.cm, u === "cm" ? "ftin" : "cm")})`})); };
    /* a change: the server answers with everything again; drawn afresh, focus back on the control that had it (or
       focus, an id); said in msg (the targets' card has its own, #234) */
    async function send(path, method, payload, said, focus, msg = $("#aboutMsg")) {
      const had = document.activeElement && document.activeElement.id;
      try {
        const got = await call(path, method, payload);
        if (P !== p) return;
        showAbout(p, got);
        for (const m of [$("#aboutMsg"), $("#targetsMsg")]) sayIn(m, m === msg ? (typeof said === "function" ? said(got) : said) : "");
        (focus && document.getElementById(focus) || had && document.getElementById(had) || $("#aboutHeightHd")).focus();
      } catch (e) { fail(e, msg); }
    }
    const parts = [];
    // the birth date and sex
    if (edit) {
      const birth = el("input", {type: "date", id: "aboutBirth", value: a.birthDate, min: "1900-01-01", max: today});
      const due = el("input", {type: "date", id: "aboutDue", value: a.dueDate || "", min: a.birthDate || "1900-01-01"});   // #196
      const sexes = el("fieldset", {class: "about-sex"}, el("legend", {text: "Sex used for reference ranges"}),
        el("div", {class: "seg wrap"}, ...[["female", "Female"], ["male", "Male"], ["", "Not set"]].map(([v, l]) =>
          el("label", {}, el("input", {type: "radio", name: "aboutSex", value: v, checked: (a.sex || "") === v}), l))),
        el("p", {class: "muted", text: "What labs base their reference ranges on, for ranges of your own set for female or male. " +
          "It isn't gender identity, which Sanguity doesn't ask for."}));
      const maxHr = el("input", {type: "number", id: "aboutMaxHr", min: 100, max: 230, step: 1, inputmode: "numeric", value: a.maxHr || ""});   // #197
      parts.push(el("form", {class: "about-details", onsubmit: ev => { ev.preventDefault();
        send(`profiles/${p.id}/person`, "PUT", {birthDate: birth.value || "", sex: sexes.querySelector("input:checked")?.value ?? "",
          maxHr: maxHr.value === "" ? 0 : +maxHr.value, dueDate: due.value || ""}, "Saved."); }},
        el("div", {class: "about-field"}, el("label", {for: "aboutBirth", text: "Birth date"}),
          el("div", {class: "about-row"}, birth, el("button", {class: "btn small", type: "button", text: "Clear", "aria-label": "Clear the birth date",
            onclick: () => { birth.value = ""; birth.focus(); }}), age != null ? el("span", {class: "muted", text: `aged ${age} today`}) : null),
          el("p", {class: "muted", text: "For the age on each result's day: ranges of your own by age band, and what depends on age."})),
        el("div", {class: "about-field"}, el("label", {for: "aboutDue", text: "Due date, if born early"}),   // #196
          el("div", {class: "about-row"}, due, el("button", {class: "btn small", type: "button", text: "Clear", "aria-label": "Clear the due date",
            onclick: () => { due.value = ""; due.focus(); }})),
          el("p", {class: "muted", text: "Optional, for a baby born early: until 2 years, growth charts use the age counted from it (the corrected age)."})),
        sexes,
        el("div", {class: "about-field"}, el("label", {for: "aboutMaxHr", text: "Maximum heart rate, measured (bpm)"}),   // heart rate zones (#197)
          el("div", {class: "about-row"}, maxHr),
          el("p", {class: "muted", text: "Only if you know it from a test, an exercise test for example. Heart rate zones on workouts use it; " +
            "left empty, they use the common estimate, 220 minus the age."})),
        el("div", {class: "about-row"}, el("button", {class: "btn primary", type: "submit", id: "aboutSave", text: "Save"}))));
    } else {
      parts.push(el("dl", {class: "about-dl"},
        el("dt", {text: "Birth date"}), el("dd", {text: a.birthDate ? aboutDay(a.birthDate) + (age != null ? ` (aged ${age} today)` : "") : "Not set"}),
        el("dt", {text: "Sex used for reference ranges"}), el("dd", {text: a.sexes[a.sex] || "Not set"}),
        el("dt", {text: "Maximum heart rate, measured"}), el("dd", {text: a.maxHr ? `${a.maxHr} bpm` : "Not set (heart rate zones use 220 minus the age)"}),   // #197
        ...(a.dueDate ? [el("dt", {text: "Due date, if born early"}), el("dd", {text: aboutDay(a.dueDate)})] : [])),   // #196
        el("p", {class: "hint", text: `Only ${p.name}'s owners and editors can change these.`}));
    }
    // the height history, newest first
    parts.push(el("h3", {class: "sub", id: "aboutHeightHd", tabindex: -1, text: "Height"}),
      el("p", {class: "hint", text: "A dated history: a child grows, and an adult's may change. The height on a day is the latest one written down by then."}));
    if (edit) {
      const typed = HEIGHT_PREF === "cm" || HEIGHT_PREF === "ftin" ? HEIGHT_PREF : (a.heights.at(-1)?.unit || "cm");
      const date = el("input", {type: "date", id: "heightDate", value: today, min: a.birthDate || "1900-01-01", max: today, required: true});
      const cm = el("input", {type: "number", id: "heightCm", min: 20, max: 272, step: "any", inputmode: "decimal"});
      const ft = el("input", {type: "number", id: "heightFt", min: 0, max: 8, step: 1, inputmode: "numeric"});
      const inch = el("input", {type: "number", id: "heightIn", min: 0, max: 11.9, step: "any", inputmode: "decimal"});
      const cmBox = el("label", {class: "about-num"}, "Height in cm", cm);
      const ftBox = el("div", {class: "about-ftin"}, el("label", {class: "about-num"}, "Feet", ft), el("label", {class: "about-num"}, "Inches", inch));
      const units = el("fieldset", {class: "about-typed"}, el("legend", {text: "Typed in"}),
        el("div", {class: "seg"}, ...[["cm", "cm"], ["ftin", "ft and in"]].map(([v, l]) =>
          el("label", {}, el("input", {type: "radio", name: "heightUnit", value: v, checked: v === typed}), l))));
      const shown = () => { const f = units.querySelector("input:checked").value === "ftin"; cmBox.hidden = f; ftBox.hidden = !f; };
      units.addEventListener("change", shown);
      const num = i => i.value === "" ? null : +i.value;
      parts.push(el("form", {class: "about-add", onsubmit: ev => { ev.preventDefault();
        const unit = units.querySelector("input:checked").value, had = a.heights.some(h => h.date === date.value);
        send(`profiles/${p.id}/person/heights`, "POST", unit === "ftin" ? {date: date.value, unit, ft: num(ft), inches: num(inch)} : {date: date.value, unit, cm: num(cm)},
          `${had ? "Replaced" : "Added"} the height on ${aboutDay(date.value)}.`); }},
        el("label", {class: "about-num"}, "Date", date), units, cmBox, ftBox,
        el("button", {class: "btn", type: "submit", id: "heightAdd", text: "Add height"})));
      shown();
    }
    const mine = [...a.heights].reverse();
    parts.push(mine.length ? el("table", {class: "tbl about-heights"},
      el("thead", {}, el("tr", {}, ...["Date", "Height", ""].map(h => el("th", {text: h})))),
      el("tbody", {}, ...mine.map(h => el("tr", {}, el("td", {text: aboutDay(h.date)}), el("td", {}, hText(h)),
        el("td", {class: "acts"}, edit ? el("button", {class: "btn small", type: "button", text: "Remove", "aria-label": `Remove the height on ${aboutDay(h.date)}`,
          onclick: async () => {
            if (!await ask({title: "Remove this height?", message: `${heightText(h.cm, heightUnit(HEIGHT_PREF, h.unit))} on ${aboutDay(h.date)}. You can add it again.`,
                            action: "Remove height", danger: true})) return;
            send(`profiles/${p.id}/person/heights/${h.date}`, "DELETE", undefined, `Removed the height on ${aboutDay(h.date)}.`); }}) : null)))))
      : el("p", {class: "muted", id: "aboutNoHeight", text: "No height written down yet."}));
    // the heights a clinic recorded: beside the history, never in it until someone adds one
    if (a.recorded.length) {
      const MORE = 8, rows = a.recorded.map(r => el("tr", {}, el("td", {text: aboutDay(r.date)}), el("td", {}, hText(r)), el("td", {text: r.source || ""}),
        el("td", {class: "acts"}, r.inHistory ? el("span", {class: "muted", text: "In the history"})
          : edit ? el("button", {class: "btn small", type: "button", text: "Add to the history", "aria-label": `Add the height on ${aboutDay(r.date)} to the history`,
            onclick: () => send(`profiles/${p.id}/person/heights`, "POST", {date: r.date, unit: r.unit, cm: r.cm}, `Added the height on ${aboutDay(r.date)}.`)}) : null)));
      rows.slice(MORE).forEach(r => { r.hidden = true; });
      const all = rows.length > MORE ? el("button", {class: "btn small", type: "button", text: `Show ${rows.length - MORE} more`,
        onclick: () => { rows.forEach(r => { r.hidden = false; }); all.remove(); }}) : null;
      parts.push(el("h3", {class: "sub", text: "Heights in the records"}),
        el("p", {class: "hint", text: "Measured at a clinic and brought in with the records, or in the phone's Health app and brought in with its export. " +   // #223
          "They're shown here, not used, until " +
          (edit ? "you add one to the history" : "an owner or editor adds one to the history") + ": an import can be undone, and a measurement can be off."}),
        el("table", {class: "tbl about-heights about-recorded"},
          el("thead", {}, el("tr", {}, ...["Date", "Height", "From", ""].map(h => el("th", {text: h})))), el("tbody", {}, ...rows)),
        all ? el("div", {class: "about-row"}, all) : null);
    }
    $("#aboutBody").replaceChildren(...parts.filter(Boolean));
    $("#targetsBody").replaceChildren(targetsCard(p, a, UNITS_PREF, (path, method, payload, said, focus) => send(path, method, payload, said, focus, $("#targetsMsg"))));   // #234
    $("#about").dataset.profile = p.id;   // whose details are drawn (the browser check waits for it)
  }

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
