/* Sanguity · the Settings page's sidebar: its sections in groups, one group shown at a time (after settings.js, which
   draws the sections). Settings is how things look (#169): this profile's (Dashboard, Labs and ranges, Milestones and
   activity) and the person's own (Display). What else was here has its own page now (settings.js, MOVED).

   Each section (#sec-<id> from the server's list) belongs to a group.
   The sidebar lists the groups that have something to show this person; the address follows the group
   (settings.html#display), and an address naming a section (settings.html#sec-units) opens that section's group
   and scrolls to it.
   "Find a setting" shows, across every group, only the settings whose name or help mentions what's typed.

   Above the sections, the group's title and whose settings these are (WHO): this profile's, for everyone it's
   shared with (or shown as they are, to someone who can only view it), or just yours. A group with a
   change not saved yet (a section with data-dirty, settings.js) is marked in the sidebar. */
"use strict";
(() => {
  const main = document.querySelector("main.sets");
  if (!main) return;
  const GROUPS = [
    {id: "dashboard", title: "Dashboard", part: "This profile", hint: "How this profile's tabs look, for everyone it's shared with",
     has: ["sec-overview", "sec-tabs", "sec-trends", "sec-names"]},
    {id: "labs", title: "Labs and ranges", part: "This profile", hint: "Ranges of your own for results that come without one",
     has: ["sec-ranges"]},
    {id: "activity", title: "Milestones and activity", part: "This profile", hint: "Named dates on every chart, and visits counted as workouts",
     has: ["sec-milestones", "sec-health"]},
    {id: "display", title: "Display", part: "You", hint: "Theme, dates and units, on every profile you see",
     has: ["sec-appearance", "sec-dates", "sec-units"]},
  ];
  const OTHER = {id: "more", title: "More", part: "", hint: "", has: []};   // a section no group names yet
  /* whose settings each group holds: the registry's sections say it by their settings' scope (data-scope); a group
     missing here goes by that */
  const WHO = {dashboard: "profile", labs: "profile", activity: "profile", display: "you"};
  const SAY = {
    profile: ["This profile", ", for everyone it's shared with."],
    view: ["This profile", ": you can view it, so these are shown as they are; its owners and editors change them."],
    you: ["Just for you", ", on every profile you see."],
    own: ["Just for you", "."],   // someone with no profile yet (settings.js sets no role then)
    server: ["This server", ", for everyone on it."],
  };
  const SCOPE_WHO = {profile: "profile", user: "you", install: "server"};
  const groupOf = id => GROUPS.find(g => g.has.includes(id)) || (id.startsWith("sec-") ? OTHER : null);
  const parts = () => [...main.querySelectorAll("#sections > section")];
  const partsOf = g => parts().filter(p => groupOf(p.id) === g);
  function whoOf(g) {
    let w = WHO[g.id];
    if (!w) {
      const scopes = new Set(partsOf(g).flatMap(p => [...p.querySelectorAll(".set[data-scope]")].map(s => s.dataset.scope)));
      w = scopes.size === 1 ? SCOPE_WHO[[...scopes][0]] : scopes.size ? "profile" : null;
    }
    return w === "profile" && main.dataset.role === "viewer" ? "view" : w === "you" && main.dataset.role === "" ? "own" : w;
  }
  const say = w => SAY[w] ? [el("strong", {text: SAY[w][0]}), SAY[w][1]] : [];
  const title = document.getElementById("groupTitle"), who = document.getElementById("introHint");

  const search = el("input", {type: "search", id: "setFind", placeholder: "Find a setting", "aria-label": "Find a setting", autocomplete: "off"});
  const list = el("ul", {class: "set-nav-list"});
  const nav = el("nav", {class: "set-nav", id: "setNav", "aria-label": "Settings sections"}, search, list);
  main.prepend(nav);
  main.classList.add("with-nav");
  let current = null, pending = !!location.hash, chosen = false;

  /* the groups with something to show: a section that's there and not hidden by its own script */
  const shown = () => {
    const have = new Set(parts().filter(p => !p.hidden).map(p => groupOf(p.id)).filter(Boolean));
    return [...GROUPS, OTHER].filter(g => have.has(g));
  };
  function drawNav() {
    const gs = shown();
    let lastPart = null;
    const rows = [];
    for (const g of gs) {
      if (g.part && g.part !== lastPart) { rows.push(el("li", {class: "set-nav-part", "aria-hidden": "true", text: g.part})); lastPart = g.part; }
      const dirty = partsOf(g).some(p => p.hasAttribute("data-dirty"));
      rows.push(el("li", {}, el("a", {href: "#" + g.id, "data-group": g.id, "aria-current": g === current ? "page" : null,
        onclick: ev => { ev.preventDefault(); show(g, {push: true, by: true}); }}, g.title,
        dirty ? el("span", {class: "set-nav-dirty", title: "Changes not saved"}, el("span", {class: "set-sr", text: ", changes not saved"})) : null)));
    }
    list.replaceChildren(...rows);
    if (pending) {   // an address naming a section that has only just arrived
      const r = groupFromHash();
      if (r && gs.includes(r.g)) { pending = false; show(r.g, {scrollTo: r.el, by: true}); return; }
    }
    // until the person picks one (or the address names one), the first group there is; sections arrive one by one
    if (gs.length && (!chosen || !gs.includes(current)) && current !== gs[0]) show(gs[0]);
  }
  function apply() {
    const q = search.value.trim().toLowerCase();
    for (const line of main.querySelectorAll(".set-in")) line.remove();   // (put back below while searching)
    for (const p of parts()) {
      const g = groupOf(p.id);
      if (!g) continue;
      if (q) {
        // searching: every group, each section with only the settings that mention it
        let any = false;
        const sets = p.querySelectorAll(".set");
        for (const s of sets) { const hit = s.textContent.toLowerCase().includes(q); s.classList.toggle("set-off", !hit); any ||= hit; }
        if (!sets.length) any = p.textContent.toLowerCase().includes(q);
        p.classList.toggle("grp-off", !any);
      } else {
        for (const s of p.querySelectorAll(".set-off")) s.classList.remove("set-off");
        p.classList.toggle("grp-off", g !== current);
      }
    }
    for (const a of list.querySelectorAll("a")) a.setAttribute("aria-current", !q && a.dataset.group === current?.id ? "page" : "false");
    main.dataset.group = q ? "" : current?.id || "";
    // the title and whose settings these are; searching, each section shown says its group
    if (title) title.textContent = q ? "Find a setting" : current?.title || "Settings";
    if (who) who.replaceChildren(...(q ? [`Matching “${search.value.trim()}”, from every group.`] : current ? say(whoOf(current)) : []));
    if (q) for (const p of parts()) {
      const g = groupOf(p.id), w = g && SAY[whoOf(g)];
      if (g && !p.classList.contains("grp-off")) p.prepend(el("p", {class: "set-in", text: g.title + (w ? " · " + w[0] : "")}));
    }
  }
  function show(g, {push = false, scrollTo = null, by = false} = {}) {
    current = g;
    if (by) chosen = true;
    if (search.value) search.value = "";
    apply();
    if (push && location.hash.slice(1) !== g.id) history.replaceState(null, "", "#" + g.id);
    const target = scrollTo || null;
    if (target) target.scrollIntoView({block: "start"});
    else if (push) window.scrollTo({top: 0});
  }
  /* #display: a group; #sec-units: that section, in its group */
  function groupFromHash() {
    const h = decodeURIComponent(location.hash.slice(1));
    if (!h) return null;
    const g = GROUPS.find(x => x.id === h);
    if (g) return {g};
    const target = document.getElementById(h);
    const of = target && groupOf(target.id) || (target && target.closest("section") && groupOf(target.closest("section").id));
    return of ? {g: of, el: target} : null;
  }
  addEventListener("hashchange", () => { const r = groupFromHash(); if (r) show(r.g, {scrollTo: r.el, by: true}); });
  search.addEventListener("input", apply);

  // the sections arrive later (the server's list): group them as they do
  const redraw = () => { drawNav(); apply(); };
  new MutationObserver(redraw).observe(document.getElementById("sections"), {childList: true});
  new MutationObserver(redraw).observe(main, {attributes: true, subtree: true, attributeFilter: ["hidden", "data-dirty", "data-role"]});
  redraw();
})();
