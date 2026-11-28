/* Sanguity · the command palette (#273): one search box, on every page with the top bar, over what you can go to or do.
   Ctrl+K (⌘K on a Mac), "/" when you're not typing in a field, or the top bar's Search (nav.js, barSearch) opens it,
   a <dialog> (openDialog, util.js). It finds, in groups:
   - Actions: what the top bar's Add menu offers here (Log a dose, Journal entry, Upload files…, as that menu shows
     them, so someone who can only view a profile isn't offered them), New share link (for the profile's owners), New
     profile (the profile menu's) and light or dark mode;
   - Records tabs: the tabs the profile shows (nav.js's links to them);
   - Pages and settings: every page with a sidebar and every group in it (groups.js, the list those sidebars are drawn
     from), and the parts of a group worth going to by name (Password, Units…): Settings' Install's for admins (#991),
     Sharing's by the profile's role, as their sidebars list them;
   - Labs: the lab names (by the name in the records, the name shown, the panel), opened as a chart on the Labs tab;
   - Events: the Timeline's index (the words its search box finds: an event's title, what it's about, its kind, a lab
     day's results' and tests' names), those the Timeline doesn't hide, each opened in the drawer on the Timeline, most
     recent first, and a search of the Timeline for what's typed when there are more, with how many it shows (#463).
   One ranking runs across them all (#403): a title starting with what's typed first, then one with each word typed
   starting a word of it, then what else it's known by (groups.js's words, Add's, a tab's, Log a dose's medications),
   always at the start of a word; the group with the best match comes first. Offline (offline.js) what needs the server
   (another page, New profile's too) or that Add shows disabled isn't listed, and it says so; the records page offline
   with no copy kept counts too (#423).
   On the records page Labs and Events come from the records shown (store.js), once app.js has drawn them, the list
   drawn again then (resume(); or none coming: its empty card). On another page they come from the API:
   the profile's records (GET profiles/{pid}/data, with its watch data and cycle tracking's, the kinds the Timeline
   lists, #470) are read once, the first time the palette opens there (again after another profile is chosen), and its
   settings, for the names labs are shown by, each time it opens; choosing one opens the records page there, which
   does the rest (resume(), from app.js once the records are drawn; sessionStorage says what). Nothing is fetched until
   the palette opens. Dates are this person's on every page (dates.js).

   It's a combobox and a listbox (WAI-ARIA's pattern): focus stays in the box while the up and down arrows move through
   what's found (aria-activedescendant; the first is marked from the start, so Enter opens the best match, waiting for
   the records if they're still on their way and could outrank it, #412, #423, unless an arrow key is pressed while it
   waits, #430; one picked with the arrows stays picked
   as the list is drawn again), Enter or a click opens it, Escape or Close closes the palette, and focus goes back to what had it (opened from a menu, to the
   menu's button). How many it found is said once typing stops. "/" can be turned off by the switch at its foot (a
   key on its own, WCAG 2.1.4), for this browser.
   SgPalette: open(q, typed in it first), resume(). Plain script, after nav.js and groups.js; all files share one scope. */
"use strict";
/* a cycle tracking record's id (cycle.js's, and the palette's on another page): from its first day and kind (a span, or
   a mark's kind), apart from every record's and workout's, so it names the same record each time they're read (#530:
   numbered in order, an Undo's reload gave an undone span's id to another, and the drawer stayed open on that one).
   used: the ids given in this read */
function cycleId(x, used) {
  let id = 5e8 + Date.parse(x.from || x.day) / 54e5 + (x.from ? 0 : {ovulation: 4, mucus: 8, spotting: 12}[x.kind] || 0);
  while (isFinite(id) && used.has(id)) id++;
  used.add(id);
  return id;
}
/* a workout's, an ECG's or a rhythm notification's id (store.js's and ecg.js's, and the palette's), k its kind: from
   when it started, to the second, and its kind, after every cycle record's, so it too names the same one each time
   they're read (#534: numbered on from the last record, a workout added or undone gave the next its id) */
function watchId(x, k, used) {
  const s = String(x.start || ""), t = Math.round(Date.parse(/(Z|[+-]\d\d:?\d\d)$/i.test(s) ? s : s + "Z") / 1000);
  let id = 2e9 + (isFinite(t) ? t : 0) * 4 + ({ecg: 1, rhythm: 2}[k] || 0);
  while (used.has(id)) id += 4;
  used.add(id);
  return id;
}
const SgPalette = (() => {
  const btn = document.getElementById("barSearch");
  if (!btn || typeof SG_PAGES === "undefined") return {open() {}, resume() {}};
  const onRecords = !!document.getElementById("view-overview"), page = location.pathname.split("/").pop() || "index.html";
  const KEY = "sg-palette";   // sessionStorage: what to do once another page is there ({do, v, pid})
  const norm = s => String(s || "").toLowerCase().normalize("NFD").replace(/\p{M}/gu, "");
  const pid = () => document.getElementById("profile")?.value || "";
  const recs = () => onRecords && typeof DATA !== "undefined" && DATA;   // store.js: the records page's, once loaded
  /* the records page's records drawn (app.js: booted), or none coming (its empty card: none yet, or they couldn't be
     loaded) */
  const drawn = () => (typeof booted !== "undefined" && booted) || !!document.querySelector("main > .empty");
  /* offline: offline.js's or offline-page.js's flag; on the records page with no copy kept, offline.js says so on the
     page (body.offline) and leaves the flag off */
  const offline = () => (typeof OFFLINE !== "undefined" && OFFLINE.on) || document.body.classList.contains("offline");
  /* "/" is a key on its own, so it can be turned off (WCAG 2.1.4: speech input can type it), by the switch at the
     palette's foot; this browser remembers */
  const slashOn = () => { try { return localStorage.getItem("sg-palette-slash") !== "off"; } catch (e) { return true; } };
  /* the keys that open it, in the Search button's tip and said with its name */
  const mac = /Mac|iP(hone|ad|od)/.test(navigator.platform || "");
  function keys() {
    btn.dataset.tip = `Search (${mac ? "⌘K" : "Ctrl+K"}${slashOn() ? " or /" : ""})`;
    btn.setAttribute("aria-keyshortcuts", (mac ? "Meta+K" : "Control+K") + (slashOn() ? " /" : ""));
  }
  keys();

  /* ---- the dialog */
  const input = el("input", {type: "text", id: "palQ", role: "combobox", "aria-expanded": "false", "aria-controls": "palList",
    "aria-autocomplete": "list", "aria-label": "Search", autocomplete: "off", spellcheck: "false", placeholder: "Search records, labs, pages and settings"});
  const list = el("div", {class: "pal-list", id: "palList", role: "listbox", "aria-label": "Found"});
  const note = el("p", {class: "pal-note", hidden: ""});
  const dlg = el("dialog", {class: "palette", "aria-label": "Search"},
    el("div", {class: "pal-head"}, icon("search"), input,
      el("button", {class: "btn quiet", type: "button", "aria-label": "Close", "data-tip": "Close (Esc)", onclick: () => dlg.close()}, icon("x"))),
    list, note,
    el("div", {class: "pal-foot"}, el("span", {class: "pal-hint", "aria-hidden": "true"}, icon("arrow-up"), icon("arrow-down"), " to move · Enter to open · Esc to close"),
      el("label", {class: "switch"}, el("input", {type: "checkbox", role: "switch", id: "palSlash", checked: slashOn() || null,
        onchange: ev => { try { localStorage.setItem("sg-palette-slash", ev.target.checked ? "on" : "off"); } catch (e) {} keys(); }}), "Open with “/”")));
  document.body.append(dlg);

  /* ---- who you are, and the records of another page's profile, fetched when it first opens */
  let ME = null, meWait = null, meDone = false, REC = null, recFor = null, RAW = null;   // RAW: [records, watch, cycle] as fetched
  const who = () => meWait ||= api("me").then(r => r.ok ? r.json() : null).catch(() => null).then(m => { ME = m; meDone = true; if (dlg.open) draw(); });
  const profile = () => ME?.profiles?.find(p => String(p.id) === pid()) || null;
  const RANK = {viewer: 0, editor: 1, owner: 2};
  document.addEventListener("change", ev => { if (ev.target.id === "profile") recFor = REC = RAW = null; });
  /* the records once; the profile's settings each time it opens, so a lab renamed in Settings' Names is found by its
     new name (#470); with the watch's data and cycle tracking's (for its owners and editors only, as cycle.js asks),
     and this person's dates (dates.js) */
  function records() {
    const p = pid();
    if (onRecords || !p) return;
    const json = r => r.status === 200 ? r.json() : null, opt = path => api(path).then(json).catch(() => null);
    const names = () => api(`profiles/${p}/settings`).then(r => r.ok ? r.json() : {}).catch(() => ({}));
    if (recFor === p) {
      if (RAW) names().then(s => { if (recFor === p && RAW) { REC = fromData(...RAW, s); if (dlg.open) draw(); } });
      return;
    }
    recFor = p; REC = RAW = null;
    const cycle = who().then(() => RANK[profile()?.role] >= RANK.editor ? opt(`profiles/${p}/cycle`) : null);
    Promise.all([api(`profiles/${p}/data`).then(json), opt(`profiles/${p}/watch`), cycle, names(), datesReady()])
      .then(([d, w, c, s]) => { if (recFor === p) { RAW = d && [d, w, c]; REC = d ? fromData(d, w, c, s) : {labs: [], events: []}; } })
      .catch(() => { if (recFor === p) REC = {labs: [], events: [], failed: true}; })
      .finally(() => { if (dlg.open) draw(); });
  }
  /* the API's records as the palette needs them: the labs' names, and each event with the words the Timeline searches
     (its title, what it's about, its kind's name); a lab day's as the records page has them (store.js labDayWords,
     #463): its reports' titles and the names of its own tests (util.js labDraws, #470), as the records and Labs name
     them (a visit's Vitals aside). And what the records page adds to the records (#470), with the ids and words it
     gives them there: the watch's workouts (store.js storeWatch) and ECGs (ecg.js ecgLoad), the journal's entries
     (journal.js journalLoad, but where on the body) and cycle tracking's (cycle.js cycleLoad) */
  function fromData(d, w, c, s) {
    const nick = s && s.labNicknames && typeof s.labNicknames === "object" ? s.labNicknames : {}, by = new Map(), ts = new Map(), title = new Map(), lab = [];
    for (const x of d.labs || []) if (x && x.name && (x.pts || []).length) {
      const have = by.get(x.name);
      if (have) have.n += x.pts.length; else by.set(x.name, {name: x.name, shown: nick[x.name] || x.name, panel: x.panel || "", n: x.pts.length});
      if (x.panel !== "Vitals") { lab.push(x); x.pts.forEach((p, n) => { p.off = x.unlisted?.[n] ? [x.imp] : null; }); }   // (#950, as same.js joinSeries)
    }
    const imp = new Map(), folded = new Map();
    for (const e of d.events || []) { title.set(e.id, e.title || ""); ts.set(e.id, zParse(e.t)); imp.set(e.id, e.imp); if (e.folded) folded.set(e.id, e.folded); }
    const listed = e => (d.details?.[e.id]?.ids || []).filter(i => isFinite(ts.get(i)));   // (the lab events it lists, #905)
    const draws = (d.events || []).filter(e => e.k === "lab").map(e => ({id: e.id, imp: e.imp, ts: ts.get(e.id), at: listed(e).map(i => ts.get(i)),
      items: listed(e).map(i => [ts.get(i), imp.get(i)]), folded: listed(e).flatMap(i => (folded.get(i) || []).map(([x, t, n]) => [x, zParse(t), n]))}));   // (#972)
    const own = labDraws(draws, lab, p => zParse(p[0]), isoDay);
    const labDay = e => { const names = [...new Set(own.get(e.id).map(([x]) => x.name))]; return [...(d.details?.[e.id]?.ids || []).map(i => title.get(i)), ...names, ...names.map(n => nick[n])]; };
    const ev = (e, words, t = zParse(e.t)) => ({id: e.id, k: e.k, title: e.title || "", ts: t, hidden: !!e.hidden, _q: words.filter(Boolean).join(" ").toLowerCase()});
    const events = (d.events || []).map(e => ev(e, [e.title, e.sub, KIND_LABEL[e.k], keyDocWords(e), ...(e.k === "lab" ? labDay(e) : [])], ts.get(e.id)));   // (a key document's kind, #1135)
    const wids = new Set();
    for (const x of w?.exercise || []) { const t = x.name.replace(/_/g, " "); events.push(ev({id: watchId(x, "exercise", wids), k: "exercise", t: x.start, title: t}, [t, "workout exercise"])); }
    for (const x of w?.ecg || []) if (x && x.start && ["ecg", "irregular_rhythm"].includes(x.type)) {
      const k = x.type === "ecg" ? "ecg" : "rhythm", t = k === "ecg" ? (x.classification ? "ECG: " + x.classification : "ECG") : "Irregular rhythm notification", e = ev({id: watchId(x, k, wids), k, t: x.start, title: t}, [t, "ecg electrocardiogram heart rhythm watch", x.device]);
      if (isFinite(e.ts)) events.push(e);
    }
    const J = {note: "Note", symptom: "Symptom", side_effect: "Side effect", mood: "Mood"};   // journal.js J_LABEL
    for (const j of Array.isArray(d.journal) ? d.journal : []) if (j && J[j.kind]) {   // its title as journal.js jTitle gives it
      const mood = [j.mood != null ? `mood ${j.mood}/10` : "", j.energy != null ? `energy ${j.energy}/10` : ""].filter(Boolean).join(", "), note = (j.note || "").replace(/\s+/g, " ").trim();
      const t = j.kind === "mood" ? (mood ? mood[0].toUpperCase() + mood.slice(1) : "Mood") : j.title || (note.length > 60 ? note.slice(0, 59) + "…" : note || "Note");
      const e = ev({id: -j.id, k: j.kind === "mood" ? "mood" : "journal", t: j.t, title: t}, [t, j.note, j.med, j.group, J[j.kind], j.kind === "mood" && "energy", "journal"]);
      if (isFinite(e.ts)) events.push(e);
    }
    if (c && c.on) {
      const used = new Set(), cy = (t, end, x, sub) => { const e = ev({id: cycleId(x, used), k: "cycle", t, title: x.title}, [x.title, sub, "cycle tracking period"]); if (isFinite(e.ts) && isFinite(zParse(end))) events.push(e); };
      for (const x of c.spans || []) cy(x.from + "T00:00:00", x.to + "T23:59:59", x, x.text);
      for (const x of c.marks || []) cy(x.day + "T12:00:00", x.day + "T12:00:00", x, x.app);
    }
    return {labs: [...by.values()], events, meds: current(d.medications), steps: !!w?.metrics?.some(m => m && m.key === "steps" && m.pts?.length)};
  }
  /* the names of the medications the records list as current (meds.js mrCurrent), Log a dose's words */
  const current = ms => (Array.isArray(ms) ? ms : []).filter(m => m && m.spans?.at?.(-1)?.open).map(m => m.name);
  const medNames = () => (onRecords ? (recs() ? current(DATA.medications) : []) : REC?.meds || []).join(" ");

  /* ---- what it finds: {icon (a name or an element), title, ctx (where or what it is), words, go()} */
  /* Add's items wherever its list is (#412: on a phone, in its sheet: nav.js menuSheet), while Add is shown */
  const addItems = () => document.getElementById("addMenu")?.hidden ? [] : [...document.getElementById("addMenuList")?.querySelectorAll("[role^=menuitem]") || []];
  function actions() {
    const out = [];
    for (const x of addItems()) {
      if (x.hidden || x.closest("[role=group]")?.hidden || x.getAttribute("aria-disabled") === "true") continue;   // (offline: offline.js)
      out.push({icon: x.querySelector("svg.icon"), title: x.textContent.trim(), ctx: "Add", go: () => x.click()});
    }
    if (profile()?.role === "owner") out.push({icon: sgIcon("sharing/links"), title: "New share link", ctx: "Sharing", go: newLink});
    // the profile menu's New profile, its only one (#402), even where that menu isn't shown (no profile yet); not where
    // it's hidden, for someone who may not create one (#1001, nav.js sgMayNew)
    const np = document.getElementById("newProfileLink");
    if (np && !np.hidden && !document.getElementById("barSearch").hidden) out.push({icon: sgIcon("sharing/newProfile"), title: "New profile", ctx: "Profile menu", words: "create add child family someone else another",   // (#524: not "person": “add person” is Sharing's)
      server: true, go: () => { location.href = np.href; }});
    // (#1134) family history: the Overview's card, and its Add a relative for owners and editors (family.js famResume)
    const fam = hash => () => {
      if (!onRecords) location.href = "./" + (pid() ? `?profile=${pid()}` : "") + hash;
      else if (location.hash === hash) famResume();
      else location.hash = hash;   // (app.js's hashchange: famResume)
    };
    if (profile() && !document.getElementById("barSearch").hidden) {
      out.push({icon: sgIcon("family"), title: "Family history", ctx: "Overview › Health record", words: "relatives relative parents conditions hereditary genetic",
        server: !onRecords, go: fam("#family-history")});
      if (RANK[profile().role] >= RANK.editor) out.push({icon: sgIcon("family"), title: "Add a relative", ctx: "Family history",
        words: "family history relative mother father sister brother grandparent conditions", server: !onRecords, go: fam("#add-relative")});
    }
    const th = document.getElementById("theme");
    if (th && !th.hidden) out.push({icon: sgIcon("theme"), title: th.textContent.trim(), ctx: "Your menu", words: "theme dark light", go: () => th.click()});
    return out;
  }
  /* what else the actions are known by (#403): Add's ways in, as their Sources groups (groups.js), the rest here, and
     Log a dose by the names of the profile's current medications; and whether one opens another page, which needs
     the server (New share link: Sharing) */
  const ADD_WORDS = {doseLink: "medication meds medicine pill tablet dose took taken", journalBtn: "note diary symptom feeling",
    medAddLink: "medication meds medicine pill tablet new own prescription",
    "home-reading": "blood pressure weight glucose temperature"};
  function known(xs) {
    const items = addItems();
    return xs.map(x => {
      const m = items.find(i => i.textContent.trim() === x.title);
      if (!m) return x.go === newLink ? {...x, server: true} : x;
      const href = m.getAttribute("href") || "", hash = href.split("#")[1] || "", g = SG_PAGES.sources.groups.find(g => g.id === hash);
      return {...x, server: !!href && !href.startsWith("#"),
        words: [x.words, ADD_WORDS[m.id] || ADD_WORDS[hash], g && g.words, m.id === "doseLink" && medNames()].filter(Boolean).join(" ")};
    });
  }
  const TAB_WORDS = {overview: "home dashboard summary tiles", health: "body vitals steps sleep heart weight", timeline: "events history",
    labs: "tests results charts blood work", costs: "money bills claims payments insurance", medications: "meds medicine prescriptions pills doses taking reminders"};
  /* the views with an address of their own (#452, app.js HASH_TABS), each listed as well, once something's typed: [tab,
     view, its # after the tab's, title, words]; a tab itself opens its first view, as its address does */
  const TAB_VIEWS = [["labs", "heatmap", "-heatmap", "Heatmap", "grid colors colours every result table"],
    ["labs", "compare", "-compare", "Compare", "side by side within ranges milestone before after"],
    ["labs", "latest", "-latest", "Latest", "latest results newest last every lab list now"],
    ["timeline", "journal", "-journal", "Journal", "notes diary entries photos symptoms"],
    ["costs", "paid", "-paid", "What you paid", "out of pocket spent spending payments total hsa fsa premiums"],
    // (#1127, #1128, #1136) cards of Costs' views, opened with focus on their heading (costs.js costJump)
    ["costs", "claims", "", "Plan years", "deductible oop maximum max plan year limits family met", "plan"],
    ["costs", "paid", "-paid", "Bills", "add a bill bills provider statement owe billed due", "bill"],
    ["costs", "paid", "-paid", "Tax-year report", "tax report taxes year deduction itemized receipts zip csv pdf hsa", "tax"]];
  const FIRST_VIEW = {labs: "home", timeline: "timeline", costs: "claims"};   // (Labs where it opens, here as from another page: #1026)
  function tabs(all) {
    if (document.querySelector("header.topbar nav.tabs")?.hidden) return [];
    // on the records page that tab, in that view; elsewhere the records page at its address
    const open = (a, t, view, hash) => () => {
      if (onRecords) pickTab(t, view);
      else { const u = new URL(a.href); u.hash = t + hash; location.href = u.href; }
    };
    return SgNav.TABS.map(t => document.getElementById("tab-" + t)).filter(a => a && !a.hidden).flatMap(a => {
      const t = a.id.slice(4), title = a.textContent.trim();
      return [{icon: sgIcon(t), title, ctx: "Records tab", words: TAB_WORDS[t], go: FIRST_VIEW[t] ? open(a, t, FIRST_VIEW[t], "") : () => a.click()},
        ...(all ? TAB_VIEWS.filter(v => v[0] === t).map(([, view, hash, vt, words, card]) => ({icon: sgIcon(t), title: vt, ctx: `Records tab › ${title}`, words,
          go: card && onRecords ? () => { pickTab(t, view); requestAnimationFrame(() => costJump(card)); } : open(a, t, view, hash)})) : [])];
    });
  }
  /* the pages (not the one you're on) and, when something's typed, their groups and named parts; at: what else a
     search takes as their place's name (groups.js place: You's "account", Install's "admin", #991) */
  function pages(all) {
    const out = [], role = RANK[profile()?.role] ?? -1;
    for (const P of Object.values(SG_PAGES)) {
      if (P.page !== page) out.push({icon: P.icon, title: P.title, ctx: "Page", words: P.words, go: () => goTo(P, "")});
      if (!all) continue;
      for (const g of P.groups) {
        if (g.rest || (g.admin && !ME?.user?.is_admin) || (g.cs && !sgConnected(ME)) || (g.needs && role < RANK[g.needs])) continue;
        const here = P.page === page;   // (first of those found as well, #524)
        const up = {icon: g.icon, title: g.title, ctx: P.title, at: g.place, words: g.words, here, go: () => goTo(P, g.id)};
        out.push(up);
        for (const [title, id, words, needs] of g.finds || []) if (!needs || role >= RANK[needs]) out.push({icon: sgIcon(P.page.replace(".html", "/") + id), title, ctx: `${P.title} › ${g.title}`, at: g.place, words, here, up, go: () => goTo(P, id, true)});
      }
    }
    if (page !== "guide.html") out.push({icon: sgIcon("guide"), title: "Getting started", ctx: "Page", words: "guide first records", go: () => { location.href = "guide.html" + (pid() ? "?profile=" + pid() : ""); }});
    return out;
  }
  /* the labs: null while another page's are still on their way */
  function labs() {
    if (!onRecords) return REC && REC.labs;
    if (!recs() || !drawn()) return [];
    const seen = new Map();
    for (const s of SERIES) if (!s.watch && s.pts.length) {
      const have = seen.get(s.name);
      if (have) have.n += s.pts.length; else seen.set(s.name, {name: s.name, shown: nice(s.name), panel: s.panel || "", n: s.pts.length});
    }
    return [...seen.values()];
  }
  const events = () => onRecords ? (recs() && drawn() ? EV : []) : REC && REC.events;
  /* the count the Timeline shows for a search (#463), hit its events found: on the records page its list's, the
     Timeline's own (core.js visible: its period and kinds; timeline.js tlListed: workouts when listed), said as the
     line over it says them; on another page, of everything on file, which is the period it opens with from there
     (timeline(), #470: its saved period counted no more), in the kinds it shows (its saved ones, util.js savedCats: a
     lab result has none, its lab day is listed) and its workouts when it lists them (timeline.js tlWorkListed) */
  const many = n => `${n.toLocaleString("en-US")} event${n === 1 ? "" : "s"}`;
  /* (#1129) the records whose documents' text has lq: null until the server has said, then the list drawn again */
  const TEXT = new Map();
  function textHits(lq) {
    const p = pid(), k = p + " " + lq;
    if (lq.length < 3 || !p) return null;
    if (!TEXT.has(k)) {
      TEXT.set(k, null);
      docsFound(p, lq).then(ids => { TEXT.set(k, ids); if (ids.size && dlg.open && input.value.trim().toLowerCase() === lq) draw(); });
    }
    return TEXT.get(k);
  }
  function tlCount(lq, hit) {
    if (!onRecords) {
      let s = null;
      try { s = JSON.parse(localStorage.getItem("ht-state:" + pid()) || "null"); } catch (e) {}   // (core.js save)
      const cats = savedCats(s), cat = e => CAT_OF[e.k]?.k, lanes = new Set(REC.events.map(cat).concat(REC.steps ? ["watch"] : []));
      const work = s?.tlWork === true || (cats.has("watch") && [...lanes].every(k => !k || k === "watch" || !cats.has(k))), shown = hit.filter(e => cats.has(cat(e)));
      return many(shown.filter(e => work || e.k !== "exercise").length) + " on file" + unlisted(work ? 0 : shown.filter(e => e.k === "exercise").length);
    }
    const was = state.q;
    state.q = lq;
    try {
      const all = EV.filter(visible), n = tlListed(all).length, when = periodLabel();
      // (#477: the search is one of the filters, so "with these filters" always, as the line over the list says it then)
      return `${when[0].toUpperCase() + when.slice(1)}, with these filters: ${many(n)}${unlisted(tlWorkListed() ? 0 : all.filter(isWork).length)}`;
    } finally { state.q = was; }
  }
  /* the workouts found that the list leaves out, as the line over it says them (timeline.js tlSum, #472) */
  const unlisted = w => w ? ` · ${w.toLocaleString("en-US")} workout${w === 1 ? "" : "s"}, not listed` : "";

  /* how well something matches what's typed (#403), the same in every group, lower better: 0 its title is what's typed
     (#452: the Journal view before Add's Journal entry), .5 its title starts with it all, 1 each word typed starts a word of the title, 2 the first does, 3 only what else it's known by has them;
     -1 not at all. A word typed is found only at the start of a word, never inside one ("hem" isn't in "theme").
     Words are letters and digits: "two-factor" is "two factor", as "mg/dL" is "mg dl". A plural typed finds its
     singular too (#502: "codes" finds "code", "matches" "match"; #506: "activities" "activity"). where: where it is
     (its page, its group), which counts for less than its own words (#506: "remove account" found Account's Passkeys,
     "add remove" there and "account" its page, before Admin's Accounts): .5 more when a word is only there, and then
     only for an entry with each of the others in its title (#524: "remove account" still found Passkeys, and "new account"
     Password, for someone with no Admin: a page's name narrows its titles down, as in "account password", and finds
     nothing by itself with the rest in an entry's words), and such an entry is as good as one with them all in its
     title, less .5 (#528: Password for “account password” came after Admin's Accounts). The little words of a button's label ("Test this
     connection", "Sign out all other devices") needn't be anywhere (STOP), unless they're all that's typed; its whole
     label is still what a title is compared with */
  const flat = s => " " + norm(s).replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  const singular = w => w.length < 4 ? w : /ies$/.test(w) ? w.slice(0, -3) + "y" : w.replace(/(ch|sh|x|ss)es$/, "$1").replace(/([^s])s$/, "$1");
  const STOP = new Set("a an the this that my your our its it all of or and to for from on in at as if".split(" "));
  const kept = typed => typed.some(w => !STOP.has(w)) ? typed.filter(w => !STOP.has(w)) : typed;
  const at = (h, w) => h.includes(" " + w) || h.includes(" " + singular(w));
  function score(title, also, typed, where = "") {
    const t = flat(title), own = t + flat(also), words = kept(typed);
    if (!words.every(w => at(own + flat(where), w))) return -1;
    const away = words.filter(w => !at(own, w));
    if (away.length && away.length < words.length && !words.every(w => away.includes(w) || at(t, w))) return -1;
    const all = " " + typed.join(" "), less = away.length ? .5 : 0;
    return t === all ? 0 : t.startsWith(all) ? .5 : words.every(w => at(t, w)) ? 1 : away.length && away.length < words.length ? 1 + less : (at(t, words[0]) ? 2 : 3) + less;
  }
  /* between pages and settings found as well (#524): one with more of the words typed whole first ("use" is Admin's
     "Use it", not "users"), then one on this page ("Turn off" on Account is two-factor sign-in's, not cycle tracking's) */
  const whole = (x, words) => { const h = flat([x.title, x.words].join(" ")) + " "; return kept(words).filter(w => h.includes(` ${w} `) || h.includes(` ${singular(w)} `)).length; };
  const nearer = words => (a, b) => whole(b, words) - whole(a, words) || (b.here ? 1 : 0) - (a.here ? 1 : 0);
  /* xs scored (x.s, or its title, then its words, then where it is), best first, then as given or by more, at most
     cap: [[score, x]] */
  /* a part found (a group's finds, x.up its group) with a word typed only in its place that its group's own title or
     words hold comes after that group (#532: “rename profile” found Delete this profile, “rename” only in “Settings ›
     Rename or delete”, before Rename or delete) */
  const where = x => x.at ? `${x.ctx} ${x.at}` : x.ctx;
  function scored(x, words) {
    const n = score(x.title, x.words || "", words, where(x)), g = x.up, own = flat(x.title) + flat(x.words || "");
    if (n < 0 || !g || !kept(words).some(w => !at(own, w) && at(flat(g.title) + flat(g.words || ""), w))) return n;
    const above = score(g.title, g.words || "", words, where(g));
    return above < 0 ? n : Math.max(n, above + .25);
  }
  const ranked = (xs, words, cap, more = () => 0) => xs.map(x => [x.s ?? scored(x, words), x])
    .filter(([n]) => n >= 0).sort((a, b) => a[0] - b[0] || more(a[1], b[1])).slice(0, cap);
  /* the groups found for what's typed: [heading, entries], those with the best match first, so the one marked (what
     Enter opens) is the best of all; and a note to show, if any. Offline (offline.js, on the records page) what needs
     the server isn't listed, as the menus don't follow their links to it, and the note says so */
  function find(typed) {
    const words = flat(typed).trim().split(" ").filter(Boolean), off = offline();
    const acts = known(actions()).filter(x => !off || !x.server), pgs = all => off ? [] : pages(all);   // (every page is another)
    const offNote = off ? "Pages and settings aren't listed: they need the server, and you're offline." : "";
    if (!words.length) return {groups: [["Actions", acts], ["Records tabs", tabs()], ["Pages", pgs(false)]], offNote};
    const groups = [["Actions", ranked(acts, words, 8)], ["Records tabs", ranked(tabs(true), words, 8)], ["Pages and settings", ranked(pgs(true), words, 10, nearer(words))]];
    const L = labs(), E = events();
    if (L) groups.push(["Labs", ranked(L.map(s => ({icon: "test-tube-diagonal", title: s.shown, n: s.n, s: score(s.shown, [s.name, s.panel].join(" "), words),
      ctx: [s.shown !== s.name && "In the records: " + s.name, `${s.n} result${s.n === 1 ? "" : "s"}`].filter(Boolean).join(" · "), go: () => openLab(s.name)})),
      words, 8, (a, b) => b.n - a.n)]);
    if (E) {
      // the Timeline's own search: the whole of what's typed in an event's words (core.js matchQ); listed, those with
      // each word at the start of one of theirs, the most recent first; the rest through the Timeline's search
      // (#463) a record the Timeline hides isn't listed (core.js visible), and a lab result's title is its panel as Labs
      // names it, as the drawer it opens does
      // (#1129) and those whose documents' text has it (util.js docsFound), after those found by their own words
      const lq = typed.trim().toLowerCase(), inText = off ? null : textHits(lq), kind = k => KIND_LABEL[k];
      const hit = E.filter(e => !e.hidden && (e._q.includes(lq) || inText?.has(e.id)));
      const found = ranked(hit.map(e => { const title = e.k === "labitem" ? panelName(e.title) : e.title, own = e._q.includes(lq);
        return {icon: kindIcon(e.k), title, ts: e.ts || 0, s: own ? score(title, e._q, words) : 3.5,
          ctx: [!own && "Found in its text", kind(e.k), isFinite(e.ts) && fmtD(e.ts)].filter(Boolean).join(" · "), go: () => openEv(e.id)}; }), words, 6, (a, b) => b.ts - a.ts);
      if (hit.length > found.length) found.push([found.length ? found.at(-1)[0] : 3, {icon: "arrow-right", title: `Search the Timeline for “${typed.trim()}”`,
        ctx: tlCount(lq, hit), go: () => timeline(typed.trim())}]);
      groups.push(["Events", found]);
    }
    const best = xs => xs.length ? xs[0][0] : 9;   // (the sort keeps the order above for a tie)
    groups.sort((a, b) => best(a[1]) - best(b[1]));
    return {groups: groups.map(([h, xs]) => [h, xs.map(([, x]) => x)]), offNote, top: groups[0][1].length ? [groups[0][0], groups[0][1][0][0]] : null,
      wait: recWait() ? "Looking through the records…" : !onRecords && REC?.failed ? "The records couldn't be searched just now." : ""};
  }

  /* ---- the list */
  let shown = [], active = -1, sayT = 0;
  /* Enter while who you are, or the records, are still on their way (#412; the records page's own too, #423): what's
     listed may not have the best match yet ("cre" finds Create… before Creatinine is there), so unless an arrow key
     picked one, Enter waits and opens the best match once they're in (draw, below). Not when nothing still to come
     can outrank it (sure): a title starting with what's typed, in a group listed before every group still to come
     (on another page Labs and Events; on the records page Add's actions too: Log a dose comes with the records) */
  let later = false, moved = false, top = null;
  const recWait = () => onRecords ? !drawn() : !!pid() && !REC, loading = () => !meDone || recWait();
  const ORDER = ["Actions", "Records tabs", "Pages and settings"], sure = () => meDone && top && top[1] < 1 && !onRecords && ORDER.includes(top[0]);
  function enter() {
    if (!input.value.trim() || moved || !loading() || sure()) return pick(active);
    later = true;
    announce("Looking through the records…");
  }
  const keyOf = x => x.title + "\n" + x.ctx;
  function draw() {
    const typed = input.value, was = moved && shown[active] ? keyOf(shown[active][1]) : null, found = find(typed), {groups, wait, offNote} = found;
    top = found.top || null;
    shown = [];
    list.replaceChildren(...groups.filter(([, xs]) => xs.length).map(([head, xs], g) => el("div", {role: "group", "aria-labelledby": "palG" + g},
      el("div", {class: "pal-sec", id: "palG" + g, role: "presentation", text: head}),
      ...xs.map(x => {
        const i = shown.length, o = el("div", {class: "pal-opt", role: "option", id: "palO" + i, "aria-selected": "false",
          onclick: () => pick(i), onpointermove: () => mark(i, false)},
          x.icon && x.icon.nodeType ? x.icon.cloneNode(true) : icon(x.icon || "arrow-right"), el("span", {class: "pal-t", text: x.title}),
          x.ctx ? el("span", {class: "pal-c"}, el("span", {class: "vh", text: ", "}), x.ctx) : null);
        shown.push([o, x]);
        return o;
      }))));
    input.setAttribute("aria-expanded", String(shown.length > 0));
    // the one picked with the arrows stays picked, if it's still listed (the records arriving, #423); else the first
    const keep = was == null ? -1 : shown.findIndex(([, x]) => keyOf(x) === was);
    active = -1;
    mark(keep >= 0 ? keep : shown.length ? 0 : -1, keep >= 0);
    moved = keep >= 0;
    if (later && !loading()) { later = false; return pick(active); }
    const nothing = typed.trim() && !shown.length && !wait ? `Nothing matches “${typed.trim()}”${offNote ? " offline" : ""}.` : "";
    note.textContent = [wait, nothing, offNote].filter(Boolean).join(" ");
    note.hidden = !note.textContent;
    clearTimeout(sayT);
    if (typed.trim()) sayT = setTimeout(() => announce(nothing ? [nothing, offNote].join(" ").trim() : shown.length ? `${shown.length} found.` : wait), 700);
  }
  /* the option marked: the one Enter opens, and the one a screen reader says */
  function mark(i, scroll = true) {
    if (shown[active]) shown[active][0].setAttribute("aria-selected", "false");
    active = i;
    const o = shown[i] && shown[i][0];
    if (!o) { input.removeAttribute("aria-activedescendant"); return; }
    o.setAttribute("aria-selected", "true");
    input.setAttribute("aria-activedescendant", o.id);
    if (scroll) o.scrollIntoView({block: "nearest"});
  }
  function pick(i) {
    const x = shown[i] && shown[i][1];
    if (!x) return;
    dlg.close();   // first: focus goes back to what had it, and the page is no longer inert
    x.go();
  }
  input.addEventListener("input", () => { later = moved = false; draw(); });
  input.addEventListener("keydown", ev => {
    const n = shown.length;
    // an arrow after Enter, while it waits for the records, takes it back: the one arrowed to opens on its own Enter (#430)
    if ((ev.key === "ArrowDown" || ev.key === "ArrowUp") && n) { ev.preventDefault(); moved = true; later = false; mark((active + (ev.key === "ArrowDown" ? 1 : n - 1)) % n); }
    else if ((ev.key === "PageDown" || ev.key === "PageUp") && n) { ev.preventDefault(); moved = true; later = false; mark(Math.max(0, Math.min(n - 1, active + (ev.key === "PageDown" ? 5 : -5)))); }
    else if (ev.key === "Enter") { ev.preventDefault(); enter(); }
  });
  /* opened from a menu (Add open, then "/" or Ctrl+K), what had focus is an item in a list now closed: focus goes back
     to the menu's button instead (#403) */
  let menuBtn = null;
  dlg.addEventListener("close", () => {
    clearTimeout(sayT);
    later = moved = false;
    const a = document.activeElement;
    if (menuBtn && menuBtn.isConnected && (!a || a === document.body || dlg.contains(a))) menuBtn.focus({preventScroll: true});
    menuBtn = null;
  });

  /* open(q): with q typed (a sidebar's "Search everywhere", sidebar.js), else empty */
  function open(q = "") {
    if (btn.hidden || btn.closest("[hidden]")) return;   // the bar locked (account.js: a password to change first)
    if (dlg.open) { input.select(); return; }
    if (document.querySelector("dialog:modal")) return;   // not over a question waiting for an answer, or the drawer over the page
    who();
    records();
    const pop = document.activeElement?.closest?.("[popover]");
    menuBtn = pop ? [...document.querySelectorAll("button")].find(b => b.popoverTargetElement === pop) || null : null;
    input.value = typeof q === "string" ? q : "";   // (not a click's event, from the Search button)
    document.getElementById("palSlash").checked = slashOn();   // (as another window may have set it)
    draw();
    openDialog(dlg, {as: "center"});
    input.focus();
  }
  btn.addEventListener("click", open);
  /* Ctrl+K or ⌘K anywhere; "/" anywhere but in a field you type in, unless it was turned off */
  const typing = t => t && t.matches?.("textarea, select, [contenteditable]:not([contenteditable=false]), input:not([type=checkbox],[type=radio],[type=button],[type=submit],[type=reset],[type=range],[type=color],[type=file])");
  addEventListener("keydown", ev => {
    if (ev.defaultPrevented || ev.isComposing || ev.altKey) return;
    const k = (ev.key || "").toLowerCase();
    if (k === "k" && (ev.ctrlKey || ev.metaKey) && !ev.shiftKey) { ev.preventDefault(); open(); }
    else if (ev.key === "/" && slashOn() && !ev.ctrlKey && !ev.metaKey && !typing(ev.target)) { ev.preventDefault(); open(); }
  });

  /* ---- going there */
  /* a page's group or part: on this page, through its address, as its sidebar's links do (sidebar.js follows it), then
     focus on its title (the h1 the group's name is in, or the part's heading); on another, that page there */
  function goTo(P, id, part) {
    if (P.page !== page) { location.href = P.page + (P.profile && pid() ? "?profile=" + encodeURIComponent(pid()) : "") + (id ? "#" + id : ""); return; }
    if (!id) return;
    addEventListener("hashchange", () => {
      const t = part ? document.getElementById(id)?.querySelector("h2") : document.getElementById("groupTitle");
      if (!t || t.closest("[hidden]")) return;
      if (!t.hasAttribute("tabindex")) t.tabIndex = -1;
      t.focus({preventScroll: !!part});   // a part: scrolled to by the sidebar already
    }, {once: true});
    if (location.hash === "#" + id) dispatchEvent(new HashChangeEvent("hashchange", {oldURL: location.href, newURL: location.href}));   // (the page's listeners read newURL: import-records.js)
    else location.hash = id;
  }
  /* the records page, to do the rest there (resume) */
  function arrive(what, v, hash) {
    try { sessionStorage.setItem(KEY, JSON.stringify({do: what, v, pid: pid()})); } catch (e) {}
    location.href = "./" + (pid() ? "?profile=" + encodeURIComponent(pid()) : "") + hash;
  }
  /* a lab: charted on the Labs tab, focus on its chart (or its table) */
  function openLab(name) {
    if (!onRecords) return arrive("lab", name, "#labs-chart");   // (its charts' own address: #labs alone is where Labs opens, #1026)
    state.sel.add(name);
    pickTab("labs", "chart");
    const card = [...document.querySelectorAll("#charts .chart")].find(c => c.dataset.lab === name);   // in the list's order (#441)
    const t = card && card.querySelector("svg.pz, .tablewrap");
    if (!t || t.closest("[hidden]")) return;
    if (!t.hasAttribute("tabindex")) t.tabIndex = -1;
    t.focus();
  }
  /* an event: the Timeline, with it open in the drawer */
  function openEv(id) {
    if (!onRecords) return arrive("event", id, "#timeline");
    const e = byId.get(id);
    if (!e) return;
    keepFocus(() => pickTab("timeline"));   // what had focus before the palette opened, if it's drawn again: what the drawer gives it back to (#417)
    openEvent(e);
  }
  /* the Timeline's search, for what was typed; focus on the list's heading, which says how many it shows. From another
     page (all), on everything on file, as the line said (tlCount, #470) */
  function timeline(q, all) {
    if (!onRecords) return arrive("timeline", q, "#timeline");
    if (all) periodVisit("all");   // for this visit: the person's own period stays saved (core.js, #472)
    $("#q").value = q;
    state.q = q.toLowerCase();
    pickTab("timeline");
    const h = $("#listTitle");
    if (h) { h.tabIndex = -1; h.focus(); }
  }
  /* Sharing → Share links, and its New share link pressed once it's there, focus in the form it opens */
  function newLink() {
    if (page !== "sharing.html") {
      try { sessionStorage.setItem(KEY, JSON.stringify({do: "new-link", pid: pid()})); } catch (e) {}
      location.href = "sharing.html?profile=" + encodeURIComponent(pid()) + "#links";
      return;
    }
    if (location.hash !== "#links") goTo(SG_PAGES.sharing, "links");   // (arriving, the sidebar follows the address itself)
    waitFor(() => [...document.querySelectorAll("#linksBody button")].find(b => b.textContent === "New share link" && !b.closest("[hidden]")), b => {
      b.click();
      waitFor(() => $("#linksBody .share-form :is(input, select, textarea)"), f => f.focus());
    });
  }
  /* then(x) once test() gives x, within a few seconds (the page's own script still drawing it) */
  function waitFor(test, then, ms = 8000) {
    const x = test();
    if (x) return then(x);
    const ob = new MutationObserver(() => { const y = test(); if (y) { ob.disconnect(); clearTimeout(t); then(y); } });
    const t = setTimeout(() => ob.disconnect(), ms);
    ob.observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ["hidden"]});
  }
  /* what was chosen on another page, done here: on the records page once they're drawn (app.js), on Sharing as it
     loads; and on the records page, the list drawn again with them (#423) */
  function resume() {
    if (onRecords && dlg.open) draw();
    let w = null;
    try { w = JSON.parse(sessionStorage.getItem(KEY) || "null"); } catch (e) {}
    if (!w || (onRecords ? w.do === "new-link" : w.do !== "new-link")) return;
    try { sessionStorage.removeItem(KEY); } catch (e) {}
    if (String(w.pid || "") !== pid()) return;   // another profile since
    ({lab: openLab, event: openEv, timeline: q => timeline(q, true), "new-link": newLink})[w.do]?.(w.v);
  }
  if (page === "sharing.html") document.addEventListener("DOMContentLoaded", () => waitFor(() => pid(), resume));
  // the records page with none coming (app.js's empty card in place of the page, offline with no copy too): drawn again
  if (onRecords) {
    const ob = new MutationObserver(() => { if (!drawn()) return; ob.disconnect(); if (dlg.open) draw(); });
    ob.observe(document.querySelector("main"), {childList: true});
  }
  return {open, resume};
})();
