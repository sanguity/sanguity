/* Sanguity · the Settings page's sidebar: its sections in groups, one group shown at a time (after settings.js,
   outside.js and settings-calendar.js, which draw the sections).

   Each section (#sec-<id> from the server's list, and the page's own #calendar, #watch and #outside cards) belongs
   to a group. The sidebar lists the groups that have something to show this person (the watch card only for owners
   and editors, Connected sources only for admins); the address follows the group (settings.html#display), and an
   address naming a section (settings.html#outside, #sec-units) opens that section's group and scrolls to it.
   "Find a setting" shows, across every group, only the settings whose name or help mentions what's typed. */
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
    {id: "calendar", title: "Calendar feed", part: "You", hint: "Appointments and expected lab draws in your own calendar app",
     has: ["calendar"]},
    {id: "watch", title: "Watch and scale", part: "Devices and sources", hint: "Where this profile's phone uploads watch and scale data",
     has: ["watch"]},
    {id: "sources", title: "Connected sources", part: "Devices and sources", hint: "Defaults for every connection on this server",
     has: ["sec-sync"]},
    {id: "outside", title: "Outside connections", part: "Devices and sources", hint: "Every host this server may contact",
     has: ["outside"]},
  ];
  const OTHER = {id: "more", title: "More", part: "", hint: "", has: []};   // a section no group names yet
  const groupOf = id => GROUPS.find(g => g.has.includes(id)) || (id.startsWith("sec-") ? OTHER : null);
  const parts = () => [...main.querySelectorAll("#sections > section, #calendar, #watch, #outside")];

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
      rows.push(el("li", {}, el("a", {href: "#" + g.id, "data-group": g.id, "aria-current": g === current ? "page" : null, text: g.title,
        onclick: ev => { ev.preventDefault(); show(g, {push: true, by: true}); }})));
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
  /* #display: a group; #outside or #sec-units: that section, in its group */
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

  // the sections arrive later (the server's list, the watch card for owners and editors): group them as they do
  const redraw = () => { drawNav(); apply(); };
  new MutationObserver(redraw).observe(document.getElementById("sections"), {childList: true});
  new MutationObserver(redraw).observe(main, {attributes: true, subtree: true, attributeFilter: ["hidden"]});
  redraw();
})();
