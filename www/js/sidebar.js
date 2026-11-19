/* Sanguity · the sidebar of Sources, Sharing, Settings, Account and Admin (#266): one component for the five pages,
   each giving its groups in order, {id, title, icon, part, has: [the ids of its parts], why}.
   - The sidebar lists them under their part's label (part: "This profile"; on a phone, a row that scrolls sideways,
     each new part set apart by a line), the group shown marked, and a group with a part changed and not saved yet
     (data-dirty) marked too.
   - One group is shown at a time. Its title is the page's h1 (#groupTitle) and the browser tab's. A card's own
     title shows only beside another card of the group: alone, it's said, not shown, under the h1 that says it.
   - Its search box shows, from every group, the parts that mention what's typed (in a part with settings, .set,
     only those that do), each with its group's name above, or says that none does, with a button to clear it (#247).
   - The address follows the group (sources.html#portals). An address naming a part, or something in one
     (settings.html#sec-units, sources.html#import-12), opens that group and scrolls to it. Addresses that moved to
     another page are moved.js's.

   sideNav(o) with o.id (the sidebar's id, setNav: its search box setFind, its empty state setNone), o.page ("Settings":
   the tab's title says "Settings: Display"), o.groups, o.find (the search box's words) and o.none(q) (what a search
   finding nothing says). Optional: o.allowed(g, parts), whether g is listed (every group, else); o.refuse(g), for an
   address naming one that isn't, true to put the group shown in the address instead; o.shown(g, was), after another
   group is shown; o.intro(g, q), after every change (g null while searching for q); o.label(g), the line above a part
   a search finds; o.alias {old: group or part id}; o.later [[pattern, group id]], for parts drawn later; o.watch,
   elements whose children arriving redraw it, as hidden, data-dirty or data-role changing anywhere in main does; a
   group with rest: true holds their children no other group names.
   Gives {nav, start(), show(group or id, {push, scrollTo}), follow(), refresh(), again(), hide(), current(),
   partsOf(g)}. Plain script; all files share one scope. */
"use strict";
function sideNav(o) {
  const main = $("main.sets"), G = o.groups, pre = o.id.replace(/Nav$/, ""), pools = o.watch || [];
  const named = id => G.some(g => g.has.includes(id));
  const partsOf = g => g.rest ? pools.flatMap(p => [...p.children].filter(c => !named(c.id)))
    : g.has.map(id => document.getElementById(id)).filter(Boolean);
  const listed = () => G.filter(g => !o.allowed || o.allowed(g, partsOf(g)));
  const byId = id => G.find(g => g.id === ((o.alias || {})[id] || id));
  function groupOf(n) {
    for (; n && n !== main; n = n.parentElement) {
      const g = n.id && (G.find(x => x.has.includes(n.id)) || (pools.includes(n.parentElement) && G.find(x => x.rest)));
      if (g) return g;
    }
    return null;
  }
  const search = el("input", {type: "search", id: pre + "Find", placeholder: o.find, "aria-label": o.find, autocomplete: "off"});
  const list = el("ul", {class: "set-nav-list"});
  const nav = el("nav", {class: "set-nav", id: o.id, "aria-label": o.page + " sections"}, search, list);
  const none = el("div", {id: pre + "None", hidden: ""});
  let current = null, chosen = false, pending = false, drawn = null;

  /* the links, drawn again only when the groups listed change (another page's script may add to a link) */
  function draw() {
    const gs = listed(), key = gs.map(g => g.id).join();
    if (key === drawn) return gs;
    drawn = key;
    let part = gs[0]?.part;
    list.replaceChildren(...gs.flatMap((g, i) => {
      const fresh = !i || g.part !== part;
      part = g.part;
      return [fresh && g.part ? el("li", {class: "set-nav-part", "aria-hidden": "true", text: g.part}) : null,
        el("li", {class: fresh && i ? "set-nav-new" : null}, el("a", {href: "#" + g.id, "data-group": g.id,
          onclick: ev => { ev.preventDefault(); show(g, {push: true, by: true}); }}, icon(g.icon), g.title))].filter(Boolean);
    }));
    return gs;
  }
  const clear = () => { search.value = ""; apply(); search.focus(); };
  function apply() {
    const typed = search.value.trim(), q = typed.toLowerCase(), gs = listed();
    for (const line of main.querySelectorAll(".set-in")) line.remove();   // (put back below while searching)
    const on = new Set(), all = new Set();   // a part may be in more than one group (Sources' view-only note)
    for (const g of G) {
      const parts = partsOf(g);
      for (const p of parts) {
        all.add(p);
        if (!q) { if (g === current) on.add(p); continue; }
        if (!gs.includes(g) || p.hidden || on.has(p)) continue;
        // searching: every group, each part with only the settings that mention it
        const sets = p.querySelectorAll(".set");
        let hit = false;
        for (const s of sets) { const h = s.textContent.toLowerCase().includes(q); s.classList.toggle("set-off", !h); hit ||= h; }
        if (!sets.length) hit = p.textContent.toLowerCase().includes(q);
        if (!hit) continue;
        on.add(p);
        const label = o.label ? o.label(g) : g.title;   // the group's name, unless the part's own title says it
        if (p.querySelector(":scope > h2, :scope > .set-hd > h2")?.textContent !== label) p.prepend(el("p", {class: "set-in", text: label}));
      }
    }
    // a card's title only beside another of the group's cards; alone, the h1 above says it (said, still, and a place for focus)
    const alone = !q && current && partsOf(current).filter(p => !p.hidden).length === 1;
    for (const p of all) {
      p.classList.toggle("grp-off", !on.has(p));
      if (!q) for (const s of p.querySelectorAll(".set-off")) s.classList.remove("set-off");
      for (const h of p.querySelectorAll(":scope > h2, :scope > .set-hd > h2")) h.classList.toggle("vh", alone && on.has(p));
    }
    const found = !!q && on.size > 0;
    for (const a of list.querySelectorAll("a")) {
      const g = byId(a.dataset.group), dirty = partsOf(g).some(p => p.hasAttribute("data-dirty")), mark = a.querySelector(".set-nav-dirty");
      a.setAttribute("aria-current", !q && g === current ? "page" : "false");
      if (dirty && !mark) a.append(el("span", {class: "set-nav-dirty", "data-tip": "Changes not saved"}, el("span", {class: "vh", text: ", changes not saved"})));
      else if (!dirty && mark) mark.remove();
    }
    main.dataset.group = q ? "" : current?.id || "";
    $("#groupTitle").textContent = q ? o.find : current?.title || o.page;
    viewTitle(q || !current ? o.page : `${o.page}: ${current.title}`);   // the browser tab's title (#209)
    const nothing = !!q && !found;
    if (nothing) {
      const what = o.none(typed);
      if (none.hidden) announce(what);   // as it starts matching nothing, not at every letter after
      none.replaceChildren(emptyState(what, {text: "Clear the search", onclick: clear}));
    }
    if (none.hidden === nothing) none.hidden = !nothing;   // only a real change (Settings watches hidden)
    if (o.intro) o.intro(q ? null : current, typed);
  }
  function show(g, {push = false, scrollTo = null, by = false} = {}) {
    if (typeof g === "string") g = byId(g);
    const gs = draw();
    if (!g || !gs.includes(g)) {
      if (g && o.refuse && o.refuse(g)) push = true;
      g = gs[0];
    }
    if (!g) return;
    const was = current;
    current = g;
    if (by || push) chosen = true;
    search.value = "";
    apply();
    if (push && location.hash.slice(1) !== g.id) history.replaceState(null, "", "#" + g.id);
    if (push && (by || g !== was)) announce(`${o.page}: ${g.title}`);   // a sidebar link says nothing of the group it shows (#209)
    // on a phone the sidebar is a row that scrolls sideways: the group shown stays in sight
    const a = list.querySelector(`a[data-group="${g.id}"]`), at = a && a.getBoundingClientRect(), box = list.getBoundingClientRect();
    if (at && (at.left < box.left || at.right > box.right)) list.scrollLeft += at.left - box.left - 8;
    if (scrollTo && !scrollTo.hidden) scrollTo.scrollIntoView({block: "start"});
    else if (push) window.scrollTo({top: 0});
    if (g !== was && o.shown) o.shown(g, was);
  }
  /* #portals: a group; #sec-units or #import-12: the group of the part holding it, and it to scroll to */
  function fromHash() {
    const h = decodeURIComponent(location.hash.slice(1));
    if (!h) return {};
    const g = byId(h);
    if (g) return {g};
    const t = document.getElementById((o.alias || {})[h] || h), of = groupOf(t);
    if (of) return {g: of, el: t};
    const later = (o.later || []).find(([re]) => re.test(h));
    return {g: later ? byId(later[1]) : null, later: true};
  }
  /* the address's group (the one shown, if it names none; else the first) */
  function follow() {
    const r = fromHash();
    pending = !!r.later || !!r.g && !listed().includes(r.g);   // a part or group not there yet: shown once it is (refresh, again)
    show(r.g || current, {scrollTo: r.el, by: !!r.g});
  }
  /* the groups listed again; until someone picks one (or the address names one), the first listed is shown */
  function refresh() {
    const gs = draw();
    if (pending) {
      const r = fromHash();
      if (gs.includes(r.g) && (r.el || !r.later)) { pending = false; show(r.g, {scrollTo: r.el, by: true}); return; }
    }
    if (gs.length && (!chosen || !gs.includes(current)) && current !== gs[0]) show(gs[0]);
    else apply();
  }
  function start() {
    main.prepend(nav);
    main.classList.add("with-nav");
    ($("#intro") || nav).after(none);
    search.addEventListener("input", apply);
    addEventListener("hashchange", () => { const r = fromHash(); if (r.g) show(r.g, {scrollTo: r.el, push: !r.el, by: true}); });
    for (const p of pools) new MutationObserver(refresh).observe(p, {childList: true});
    // a part shown, hidden or changed: only a real change (a hidden set again to what it was is a record too)
    new MutationObserver(rs => { if (rs.some(r => r.oldValue !== r.target.getAttribute(r.attributeName))) refresh(); })
      .observe(main, {attributes: true, attributeOldValue: true, subtree: true, attributeFilter: ["hidden", "data-dirty", "data-role"]});
    follow();
  }
  return {nav, start, show, follow, refresh, partsOf, current: () => current,
    again: () => { const r = fromHash(); if (r.el) { pending = false; show(r.g, {scrollTo: r.el}); } else apply(); },   // once the parts have their content
    hide() { nav.hidden = true; main.classList.remove("with-nav"); }};
}
