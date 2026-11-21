/* Sanguity · the sidebar of Sources, Sharing, Settings, Account and Admin (#266): one component for the five pages,
   each giving its groups in order, {id, title, icon, part, has: [the ids of its parts], why}.
   - The sidebar lists them under their part's label (part: "This profile"; on a phone, a row that scrolls sideways,
     each new part set apart by a line), the group shown marked, and a group with a part changed and not saved yet
     (data-dirty) marked too.
   - One group is shown at a time. Its title is the page's h1 (#groupTitle) and the browser tab's. A card's own
     title shows only beside another card of the group: alone, it's said, not shown, under the h1 that says it, and
     its Reset to defaults (the button in its .set-hd) shows beside the h1 instead (#409).
   - Its search box shows, from every group, the parts that mention what's typed (in a part with settings, .set,
     only those that do), each with its group's name above, or says that none does, with a button to clear it (#247)
     and one to search everywhere, the command palette opened with the words (palette.js, #403).
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
   Gives {nav, start(), show(group or id, {push, scrollTo}), follow(), refresh(), again(), hide(none), current(),
   partsOf(g)}.

   The group bar (#272), with o.bar = {save(parts, msg, had), discard(parts), profile()}: while a part shown has
   changes not saved (data-dirty, the number of them), one bar at the foot of the screen says how many, with Discard
   and Save for them all: "3 changes · Discard · Save". save and discard get the parts shown with changes (had: focus
   was in the bar); save gives back once it's done (or has said in msg what stopped it), each part's data-dirty saying
   what's left. A part with changes, shown or not, is asked about before the page is left (logOut() too, util.js) or
   another profile is picked (profile(): the one shown, to put the picker back on if the answer is to stay). Plain
   script; all files share one scope. */
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
  const lone = el("button", {class: "btn small", type: "button", id: pre + "Reset", hidden: ""});   // a lone card's Reset, beside the h1
  let current = null, chosen = false, pending = false, drawn = null, shown = [], loneInfo = null;
  const dirtyIn = ps => ps.filter(p => p.hasAttribute("data-dirty"));
  const bar = o.bar ? groupBar() : null;

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
        // or its own ⓘ's words (#447: a section's help, behind the ⓘ beside its title, or beside the h1 while it's
        // alone), outside every .set: the part shown, all its settings with it if none of them says it
        else if (!hit && [...p.querySelectorAll(".about-words"), ...(loneInfo && p.contains(loneInfo.sgHome) ? loneInfo.querySelectorAll(".about-words") : [])]
          .some(w => !w.closest(".set") && w.textContent.toLowerCase().includes(q))) {
          hit = true;
          for (const s of sets) s.classList.remove("set-off");
        }
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
    const r = alone ? partsOf(current).find(p => !p.hidden)?.querySelector(":scope > .set-hd > .btn:not([hidden])") : null;
    if (r) { lone.textContent = r.textContent; lone.onclick = () => r.click(); }
    if (lone.hidden !== !r) lone.hidden = !r;
    // and its ⓘ (#280, util.js), moved beside the h1, and back beside its own title once that shows
    const part = alone ? partsOf(current).find(p => !p.hidden) : null;
    const info = part && (loneInfo && part.contains(loneInfo.sgHome) ? loneInfo : part.querySelector(":scope > .set-hd > .info"));
    if (loneInfo && loneInfo !== info) {   // back to its title; or gone, where its card was drawn again without it (#487's phone run)
      const h = loneInfo.sgHome.isConnected && loneInfo.sgHome.querySelector(":scope > h2");
      if (h) h.after(loneInfo); else loneInfo.remove();
      loneInfo = null;
    }
    if (info && info !== loneInfo) { info.sgHome = info.parentElement; $("#groupTitle").after(info); loneInfo = info; }
    shown = [...on];
    if (bar) bar.draw();
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
      // and offers to search everywhere (#403): the command palette, with the words typed here, may find it on another page
      const b = (text, onclick, cls = "") => el("button", {class: "btn large empty-next " + cls, type: "button", text, onclick});
      const e = emptyState(what);
      e.append(el("div", {class: "empty-acts"},
        typeof SgPalette !== "undefined" && document.getElementById("barSearch") ? b(`Search everywhere for “${typed}”`, () => SgPalette.open(typed), "primary") : null,
        b("Clear the search", clear)));
      none.replaceChildren(e);
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
  /* the bar: how many changes the parts shown have, Discard and Save */
  function groupBar() {
    const n = el("span", {class: "sg-bar-n"}), msg = el("span", {class: "msg"});
    const drop = el("button", {class: "btn", type: "button", text: "Discard"});
    const save = el("button", {class: "btn primary", type: "button", text: "Save"});
    // at the screen's foot, in one layer with the toasts (util.js sgFoot, #544): measured again as it shows or goes
    const box = el("div", {class: "sg-bar sg-bar-foot", id: pre + "Bar", role: "region", "aria-label": "Changes not saved", hidden: ""},
      el("span", {class: "sg-bar-what"}, n, msg), el("span", {class: "sg-bar-acts"}, drop, save));
    let busy = false;
    const draw = () => {
      const c = dirtyIn(shown).reduce((a, p) => a + (+p.dataset.dirty || 1), 0);
      n.textContent = `${c} change${c === 1 ? "" : "s"}`;
      if (!c && !busy) sayIn(msg, "");
      box.hidden = !c && !busy;
      sgFoot();
      save.disabled = drop.disabled = busy || !c;
    };
    save.onclick = async () => {
      const ps = dirtyIn(shown);
      if (!ps.length || busy) return;
      const had = box.contains(document.activeElement);   // (before Save is disabled, which takes focus away)
      busy = true; draw(); sayIn(msg, "Saving…");
      try { await o.bar.save(ps, msg, had); }
      catch (e) { sayIn(msg, e.message, true); }
      finally {
        busy = false; if (msg.textContent === "Saving…") sayIn(msg, ""); draw();
        // not all saved: focus back on Save, to put it right and save again (all saved: the page put it on a heading)
        if (had && !box.hidden && (!document.activeElement || document.activeElement === document.body)) save.focus({preventScroll: true});
      }
    };
    drop.onclick = () => { const ps = dirtyIn(shown); sayIn(msg, ""); if (ps.length) o.bar.discard(ps); };
    // leaving the page, or picking another profile, with changes not saved: asked first
    const all = () => dirtyIn(G.flatMap(partsOf));
    addEventListener("beforeunload", ev => { if (!sgLeaving && all().length) { ev.preventDefault(); ev.returnValue = ""; } });
    let switching = false;
    document.addEventListener("change", async ev => {   // before the page's own listeners: said no, the profile stays
      const pick = ev.target, was = o.bar.profile && o.bar.profile();
      if (pick.id !== "profile" || switching || was == null) return;
      const names = [...new Set(all().map(p => groupOf(p)?.title).filter(Boolean))];
      if (!names.length) return;
      const wanted = pick.value;
      ev.stopImmediatePropagation();
      pick.value = was;
      const list = names.map((t, i) => (i === 0 ? "" : i === names.length - 1 ? " and " : ", ") + `“${t}”`).join("");
      if (!await ask({title: "Switch profile and lose your changes?", message: `Your changes in ${list} aren't saved.`,
                      action: "Switch and lose changes", cancel: "Stay here", danger: true})) return;
      pick.value = wanted;
      switching = true;
      try { pick.dispatchEvent(new Event("change", {bubbles: true})); } finally { switching = false; }
    }, true);
    return {box, draw};
  }
  function start() {
    main.prepend(nav);
    if (bar) main.append(bar.box);
    main.classList.add("with-nav");
    ($("#intro") || nav).after(none);
    $("#groupTitle")?.after(lone);
    search.addEventListener("input", apply);
    aboutWire.after = refresh;   // a card's ⓘ made once it's drawn (util.js): moved beside the h1 if the card's alone
    addEventListener("hashchange", () => { const r = fromHash(); if (r.g) show(r.g, {scrollTo: r.el, push: !r.el, by: true}); });
    for (const p of pools) new MutationObserver(refresh).observe(p, {childList: true});
    // a part shown, hidden or changed: only a real change (a hidden set again to what it was is a record too)
    new MutationObserver(rs => { if (rs.some(r => r.oldValue !== r.target.getAttribute(r.attributeName))) refresh(); })
      .observe(main, {attributes: true, attributeOldValue: true, subtree: true, attributeFilter: ["hidden", "data-dirty", "data-role"]});
    follow();
  }
  return {nav, start, show, follow, refresh, partsOf, current: () => current,
    again: () => { const r = fromHash(); if (r.el) { pending = false; show(r.g, {scrollTo: r.el}); } else apply(); },   // once the parts have their content
    // none: no group is shown any more (Sharing's last profile left, #523), so the title is the page's alone; it kept
    // the last group's, put back by each change that redraws the list
    hide(none = false) { nav.hidden = true; main.classList.remove("with-nav"); if (none) { current = null; apply(); } }};
}
