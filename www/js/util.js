/* Sanguity · helpers shared by every page: $, el (build DOM), api (call the backend).
   Plain script, loaded first; all files share one global scope. */
"use strict";
const $ = s => document.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const e = tag.startsWith("svg:") ? document.createElementNS("http://www.w3.org/2000/svg", tag.slice(4)) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    // an ARIA state is "true" or "false", never left out: CSS and screen readers read [aria-pressed="false"] as off
    if (k.startsWith("aria-") && typeof v === "boolean") { e.setAttribute(k, String(v)); continue; }
    if (v == null || v === false) continue;
    if (k === "text") e.textContent = v;
    else if (k === "class") e.setAttribute("class", v);
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
    else e.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null) e.append(c.nodeType ? c : document.createTextNode(c));
  return e;
};
/* the parts given that are there: Element.append, .prepend and .replaceChildren write a null out as the text "null"
   (#245), so a part that may be left out (x ? el(…) : null) goes in through this, as el() leaves out its own children:
   box.append(...present(a, b ? el(…) : null)) */
const present = (...kids) => kids.flat().filter(k => k != null);
/* each kind of record's name: the Timeline's and the drawer's (core.js), and the command palette's on every page, an
   event's words as the Timeline searches them (store.js, palette.js: #463) */
const KIND_LABEL = {lab:"Lab draw",imaging:"Imaging",procedure:"Procedure",study:"Report / study",message:"Conversation",appt_msg:"Appointment message",auto_msg:"Automated message",letter:"Letter",document:"Document",stay:"Hospital stay",visit:"Visit",contact:"Chart activity",labitem:"Lab result",exercise:"Workout (watch)",med:"Medication",rx:"Pharmacy fill",claim:"Insurance claim",journal:"Journal",mood:"Mood and energy",immunization:"Immunization",ecg:"ECG (watch)",rhythm:"Irregular rhythm notification (watch)",cycle:"Cycle tracking"};
/* the Timeline's kinds (core.js, the records page) and each kind's icon, here for the command palette on every page
   too (#470): its events off the records page, counted as the Timeline lists them and shown with their kinds' icons.
   Each kind's colour is the same in its chip, its Timeline lane, its rows and the drawer. Eight kinds have a colour of
   their own (#301, tokens.css: validated in light and dark); the rest are in a neutral ink and known by their icon and
   label: chart activity, immunizations, letters and ECGs in --ink2, as the watch's workouts, medications in --muted,
   claims in --axis, hospital stays in --stay-line. */
const CATS = [
  {k:"stay",      label:"Hospital stays",   color:"var(--stay-line)", kinds:["stay"]},
  {k:"visit",     label:"Visits",           color:"var(--c-visit)",   kinds:["visit"]},
  {k:"contact",   label:"Chart activity",   color:"var(--ink2)",      kinds:["contact"]},   // chart reviews, calls, faxes (#236)
  {k:"lab",       label:"Labs",             color:"var(--c-lab)",     kinds:["lab"]},
  {k:"imaging",   label:"Imaging",          color:"var(--c-imaging)", kinds:["imaging"]},
  {k:"procedure", label:"Procedures",       color:"var(--c-procedure)",kinds:["procedure"]},
  {k:"immunization", label:"Immunizations", color:"var(--ink2)", kinds:["immunization"]},   // #179: vaccines given
  {k:"study",     label:"Reports & studies",color:"var(--c-study)",   kinds:["study"]},
  {k:"message",   label:"Messages",         color:"var(--c-message)", kinds:["message","appt_msg","auto_msg"]},
  {k:"letter",    label:"Letters & docs",   color:"var(--ink2)",      kinds:["letter","document"]},
  {k:"med",       label:"Medications",      color:"var(--muted)",     kinds:["med","rx"]},
  {k:"claim",     label:"Insurance claims", color:"var(--axis)",      kinds:["claim"]},
  {k:"watch",     label:"Watch",            color:"var(--ink2)",      kinds:["exercise"]},
  {k:"ecg",       label:"ECG & rhythm",     color:"var(--ink2)",      kinds:["ecg","rhythm"]},   // #182: a watch's ECGs and irregular rhythm notifications (ecg.js)
  {k:"journal",   label:"Journal",          color:"var(--c-journal)", kinds:["journal"]},   // the person's own entries (journal.js)
  {k:"mood",      label:"Mood & energy",    color:"var(--c-journal)", kinds:["mood"]},      // #161: a day's mood and energy, written in the journal
  {k:"cycle",     label:"Cycle tracking",   color:"var(--c-cycle)",   kinds:["cycle"]},     // #183: only when its owner turned it on (cycle.js)
];
const CAT_OF = {}; CATS.forEach(c => c.kinds.forEach(k => CAT_OF[k] = c));
/* each kind's icon (#354, Lucide's, in icons/sprite.svg): one map, for the Timeline's chips and rows and for what
   shows a kind next (the drawer, the charts). A category's chip takes its key's (CATS[].k): its first kind's, or the
   watch's own. */
const KIND_ICON = {stay: "bed", visit: "stethoscope", contact: "clipboard", lab: "test-tube-diagonal", labitem: "test-tube-diagonal",
  imaging: "scan-line", procedure: "bandage", immunization: "syringe", study: "microscope", message: "message-square",
  appt_msg: "message-square", auto_msg: "message-square", letter: "mail", document: "file-text", med: "pill", rx: "pill",
  claim: "banknote", watch: "watch", exercise: "watch", ecg: "activity", rhythm: "activity", journal: "notebook", mood: "smile",
  cycle: "droplet"};
/* each kind's shape where it's drawn over or under a chart (#301): one shape for one meaning, so a ● is a result and
   nothing else. Drawn by shapeMark() below; the Labs charts' marks of procedures and imaging (trends.js), the
   journal's row (journal.js, by an entry's kind), the cycle's (cycle.js, by a mark's kind) and the heatmap's procedures
   (heatmap.js) take theirs from here, and so does the Legend. Kept for a result's own flag, never an event's: ▲ above
   its range, ▼ below, ◆ flagged otherwise (chartkit.js abnMark). A span (a medication, a period, a hospital stay) is a
   bar as long as it lasted, and a day's mood and energy two bars (journal.js jMoodBars). */
const KIND_SHAPE = {labitem: "circle", procedure: "plus", imaging: "square",
  symptom: "star", side_effect: "hexagon", note: "bar",   // the journal's kinds
  ovulation: "ring", mucus: "cross", spotting: "drop"};   // the cycle's marks
/* a kind's shape (#301, KIND_SHAPE) at x, y: about as much ink as a circle of radius r, whichever it is, so
   none looks bigger than another; attrs its class, fill, stroke and the rest, and data-shape names it */
function shapeMark(shape, x, y, r, attrs = {}) {
  const poly = pts => "M" + pts.map(([dx, dy]) => `${(x + dx).toFixed(2)},${(y + dy).toFixed(2)}`).join("L") + "Z";
  const turn = (n, R, a0 = -90) => Array.from({length: n}, (_, i) => { const a = (a0 + i * 360 / n) * Math.PI / 180; return [R * Math.cos(a), R * Math.sin(a)]; });
  const a = {"data-shape": shape, ...attrs}, t = r * .42, l = r * 1.15;   // a plus's half-thickness and arm
  const plus = [[-t, -l], [t, -l], [t, -t], [l, -t], [l, t], [t, t], [t, l], [-t, l], [-t, t], [-l, t], [-l, -t], [-t, -t]];
  switch (shape) {
    case "square": return el("svg:rect", {x: x - r * .9, y: y - r * .9, width: r * 1.8, height: r * 1.8, ...a});
    case "plus": return el("svg:path", {d: poly(plus), ...a});
    case "cross": return el("svg:path", {d: poly(plus.map(([dx, dy]) => [(dx - dy) * .7071, (dx + dy) * .7071])), ...a});
    case "star": return el("svg:path", {d: poly(turn(10, 1).map(([dx, dy], i) => [dx * r * (i % 2 ? .58 : 1.3), dy * r * (i % 2 ? .58 : 1.3)])), ...a});
    case "hexagon": return el("svg:path", {d: poly(turn(6, r * 1.1)), ...a});
    case "bar": return el("svg:rect", {x: x - r * 1.3, y: y - r * .6, width: r * 2.6, height: r * 1.2, rx: r * .3, ...a});
    case "drop": return el("svg:path", {d: `M${x},${y - r * 1.5}C${x + r * .5},${y - r * .8} ${x + r},${y - r * .2} ${x + r},${y + r * .25}`
      + `A${r},${r} 0 0 1 ${x - r},${y + r * .25}C${x - r},${y - r * .2} ${x - r * .5},${y - r * .8} ${x},${y - r * 1.5}Z`, ...a});
    case "ring": return el("svg:circle", {cx: x, cy: y, r: r * .9, fill: "var(--surface)", ...a});
    default: return el("svg:circle", {cx: x, cy: y, r, ...a});
  }
}
/* a legend's key: one shape drawn small, its own svg, hidden from screen readers (the words beside it say it) */
const shapeKey = (shape, attrs, r = 4) => el("svg:svg", {viewBox: "0 0 14 14", width: 14, height: 14, "aria-hidden": "true"}, shapeMark(shape, 7, 7.5, r, attrs));
/* a kind's icon, hidden from assistive technology beside the kind's name (util.js icon()); cls: a class of its own */
const kindIcon = (k, cls) => {
  const i = icon(KIND_ICON[k] || KIND_ICON[CAT_OF[k]?.k] || "file-text");
  if (cls) i.classList.add(cls);
  return i;
};
/* the kinds the Timeline shows, by its saved state s (core.js save(), restore()), or as it starts without one: all
   but Insurance claims (they're on Costs); a kind that came after s was saved is shown, but claims */
function savedCats(s) {
  const known = s?.cats ? new Set(s.known || CATS.filter(c => c.k !== "watch").map(c => c.k)) : new Set();
  return new Set([...(s?.cats || []), ...CATS.filter(c => !known.has(c.k) && c.k !== "claim").map(c => c.k)]);
}
/* each lab draw's own results (#470, #905): a result is the draw's that lists the lab event holding its row, by the
   draw's own list, wherever either sits. A row is its result's where it's in a lab event of that result's own import and
   day (drawer.js rowPoint), so the draws listing one of those are the result's: a pull's results whose lab event joins a
   lab day another import made (#899) are that lab day's, not also a C-CDA's of the same date, whose badge counted them
   and whose drawer didn't list them; and a result typed in from an outside lab on a clinic draw's day is its own draw's,
   not the clinic's too (#470). Its search words, its badge and ▲ under a search read them (store.js labDayWords,
   timeline.js tlAbn, palette.js). Of several, the one with a report nearest it in time. A result no draw lists a lab
   event of its import for is its import's own draw's that day, else every draw's that day, as before. draws: [{id, imp,
   ts, at: its reports' times, items: [[time, import]] of the lab events it lists}]; series: lab series, a point's
   import p.imp where several are joined (same.js), else the series'; a result that came twice shown once (same.js
   joinSeries, #825) each of its imports' draws' (p.also). ts(p) a point's time, day(t) its day. -> Map(draw id ->
   [[series, point]]) */
function labDraws(draws, series, ts, day) {
  const out = new Map(), by = new Map(), held = new Map(), add = (m, k, x) => (m.get(k) || m.set(k, []).get(k)).push(x);
  for (const e of draws) {
    out.set(e.id, []); add(by, day(e.ts) + "\u001f" + e.imp, e); add(by, day(e.ts), e);
    for (const k of new Set((e.items || []).map(([t, i]) => day(t) + "\u001f" + i))) add(held, k, e);
  }
  const near = (e, t) => Math.min(...(e.at?.length ? e.at : [e.ts]).map(a => Math.abs(a - t)));
  const pick = (mine, t) => mine.length > 1 ? mine.reduce((a, b) => near(b, t) < near(a, t) ? b : a) : mine[0];
  for (const s of series) for (const p of s.pts) {
    const t = ts(p), d = day(t), imp = p.imp ?? s.imp;
    const at = new Set([imp, ...(p.also || [])].map(i => held.get(d + "\u001f" + i) || by.get(d + "\u001f" + i)).filter(Boolean).map(m => pick(m, t)));
    for (const e of at.size ? at : by.get(d) || []) out.get(e.id).push([s, p]);
  }
  return out;
}
/* a row that opens something when clicked, a button to the keyboard and screen readers too (#202): el("div", {class:
   "ov-row", ...pressable(() => openEvent(e))}, …). Its words are its name. A table's row gets a .cell-btn in a cell. */
const pressable = fn => ({role: "button", tabindex: 0, onclick: fn,
  onkeydown: ev => { if ((ev.key === "Enter" || ev.key === " ") && ev.target === ev.currentTarget) { ev.preventDefault(); fn(ev); } }});
/* the background to a part of a page (#280): how it works, where its numbers come from, what stays private. Behind
   the ⓘ beside its heading (aboutWire, below), so a heading has one line of hint under it at most. Put where the
   words are wanted, after the heading and its hint; strings become paragraphs, nulls are left out; a first {title}
   names it ("About the charts") and puts its ⓘ there, where there's no heading to put it beside; a {name} names it
   alone, its ⓘ still beside the heading (Today's doses, under headings that differ by tab, #460). Pages write the
   same <div class="about-words" hidden> by hand (data-title for a title). */
function aboutWords(...kids) {
  const o = kids[0] && !kids[0].nodeType && typeof kids[0] === "object" && !Array.isArray(kids[0]) ? kids.shift() : {};
  const w = el("div", {class: "about-words", hidden: "", "data-title": o.title, "data-name": o.name, id: o.id},
    ...kids.flat().filter(k => k != null && k !== "").map(k => typeof k === "string" ? el("p", {text: k}) : k));
  aboutWire.later.add(w);
  if (aboutWire.later.size === 1) { queueMicrotask(aboutWire); requestAnimationFrame(() => aboutWire()); }
  return w;
}
/* a setting's or a section's help as the server words it (#280): its first line the hint under the heading, what
   follows a line break (and more, more paragraphs) behind the heading's ⓘ. -> the nodes */
const helpNodes = (help, ...more) => {
  const [line, ...rest] = (help || "").split("\n"), words = [...rest, ...more].filter(Boolean);
  return present(line ? el("p", {class: "hint", text: line}) : null, words.length ? aboutWords(...words) : null);
};
/* a setting row (#286, app.css .set-row): lbl, what's set (its heading or label, a hint, its ⓘ's words), on the
   left; ctl, its control and message, on the right; attrs the row's own (a class added to set-row's; tag: "section"
   for another element than a div). -> the row */
const setRow = (lbl, ctl, {tag = "div", ...attrs} = {}) => el(tag, {...attrs, class: "set-row" + (attrs.class ? " " + attrs.class : "")},
  el("div", {class: "set-row-lbl"}, lbl), el("div", {class: "set-row-ctl"}, ctl));
/* a small menu of a row's actions (Settings' ordered lists, Admin's accounts, #287): its ⋯ button opens it by touch,
   mouse or keyboard (Enter, Space or the down arrow; then the arrow keys, and Escape to close it: the app's one menu,
   SgNav.menu in nav.js). entries: [{text, run, danger}], a null for a line between two; act: the button's
   data-act; cls: the menu's class ("set-more": shown only on a phone, in Settings' lists). -> the menu */
let rowMenus = 0;
function rowMenu(label, entries, act, cls = "set-more") {
  const id = "rowMenu" + (++rowMenus);
  const btn = el("button", {class: "btn small set-more-btn", type: "button", "data-act": act, "aria-label": label,
    "aria-haspopup": "menu", "aria-expanded": "false", "aria-controls": id}, icon("ellipsis"));
  const items = entries.map(e => e ? el("button", {class: "menu-item" + (e.danger ? " danger" : ""), role: "menuitem", type: "button", tabindex: "-1",
    text: e.text, onclick: () => { m.close(); e.run(); }}) : el("div", {class: "menu-sep", role: "separator"}));
  const list = el("div", {class: "menu-list", id, role: "menu", "aria-label": label}, ...items);
  const m = SgNav.menu({btn, list, wrap: el("div", {class: "menu " + cls}, btn, list)});
  return m.wrap;
}
/* the danger zone (#287, app.css .danger-zone): what can't be undone, deleting a profile (profile-card.js) or removing
   an account (admin.js), in one red-edged box of its own under the settings, not a row among them. A setting row: its
   title and why on the left; on the right o.pick (Admin's choice of account) or a button, o.open ("Delete profile…"),
   that opens the rest: what goes (o.says(name), nodes), what to do first (o.first(name): Export everything first), and
   the name to type exactly before its red button, o.action, does o.run(typed), which throws to say why not (the server
   checks the name again). -> {node, show(name, {focus}), hide(), msg}: show(name) opens it for that name, focus on the
   box to type it in unless focus is false, the whole of it brought into view (its red button too, clear of a phone's
   tabs); msg says how it went, under it. While it runs, focus waits in the box (#493: the red button turns itself off),
   so it's there still once a question asked meanwhile closes, or the run fails and msg says why */
let dangerZones = 0;
function dangerZone(o) {
  const id = "dz" + (++dangerZones), typed = el("input", {id: id + "Name", autocomplete: "off", autocapitalize: "none", spellcheck: "false"});
  const go = el("button", {class: "btn danger", type: "button", text: o.action, disabled: true});
  const said = el("div", {class: "dz-says"}), first = el("div", {class: "dz-first"}), lbl = el("label", {for: typed.id});
  const msg = el("p", {class: "msg dz-msg"});
  const body = el("div", {class: "dz-body", id: id + "Body", hidden: true}, said, first, el("div", {class: "dz-name"}, lbl, typed), go);
  let name = null, busy = false;
  const ready = () => { go.disabled = busy || name == null || typed.value.trim() !== name; };
  typed.oninput = ready;
  typed.onkeydown = ev => { if (ev.key === "Enter" && !go.disabled) { ev.preventDefault(); go.click(); } };
  go.onclick = async () => {
    if (document.activeElement === go) typed.focus({preventScroll: true});
    busy = true; ready(); sayIn(msg, "");
    try { await o.run(typed.value); }
    catch (e) {
      sayIn(msg, e.message, true);
      // still in the zone: the box, open still, else what's left of it (o.run may have closed it: the account gone)
      if (!node.contains(document.activeElement)) (body.hidden ? node.querySelector("select, .dz-open") : typed)?.focus();
    }
    finally { busy = false; ready(); }
  };
  const open = o.open ? el("button", {class: "btn dz-open", type: "button", text: o.open, "aria-expanded": "false", "aria-controls": body.id,
    onclick: () => body.hidden ? show(o.name) : hide()}) : null;
  function show(n, {focus = true} = {}) {
    name = n; typed.value = ""; lbl.textContent = `Type ${n} to confirm`;
    said.replaceChildren(...[].concat(o.says(n)).map(x => typeof x === "string" ? el("p", {text: x}) : x));
    first.replaceChildren(...[].concat(o.first ? o.first(n) : []));
    first.hidden = !first.childElementCount;
    body.hidden = false; open?.setAttribute("aria-expanded", "true"); sayIn(msg, ""); ready();
    if (focus) typed.focus({preventScroll: true});
    // all of it in view, clear of the top bar and a phone's tabs (html's scroll-padding); taller than the room there
    // is, its foot (the red button) at the room's foot. Focus left where it was (its picker, #499: it opened out of
    // sight on a phone), the picker in view too: the whole zone, else from the picker down
    const pad = getComputedStyle(document.documentElement), room = innerHeight - parseFloat(pad.scrollPaddingTop) - parseFloat(pad.scrollPaddingBottom);
    const whole = focus ? body : node, fits = whole.getBoundingClientRect().height <= room;
    (fits ? whole : focus ? go : node.firstElementChild).scrollIntoView({block: fits ? "nearest" : focus ? "end" : "start", behavior: smoothly()});
  }
  function hide() { body.hidden = true; open?.setAttribute("aria-expanded", "false"); name = o.open ? o.name : null; typed.value = ""; ready(); }
  const node = el("section", {class: "danger-zone", "aria-labelledby": id + "Title"},
    setRow([el("h3", {class: "sub", id: id + "Title", text: o.title}), o.hint ? el("p", {class: "hint", text: o.hint}) : null], o.pick || open),
    body, msg);
  return {node, show, hide, msg};
}
/* focus once a row's action has taken its control away (#493: Remove, Revoke, Sign out): the same control in the row
   now in its place (the next one), else in the nearest before it, else fallback (a heading, made focusable; a card's
   heading said only, its card alone in its group, gives way to the page's, sidebar.js), in view. rows: the rows as
   drawn now; i: where the row was among them; sel: the control in a row */
function focusNear(rows, i, sel, fallback) {
  const near = [...rows.slice(i), ...rows.slice(0, i).reverse()].map(r => r.querySelector(sel)).find(x => x && !x.disabled);
  const to = near || (fallback?.classList.contains("vh") && document.getElementById("groupTitle")) || fallback;
  if (!to) return;
  if (!near && to.matches("h1, h2, h3, h4")) to.tabIndex = -1;
  to.focus({preventScroll: true});
  to.scrollIntoView({block: "nearest"});
}
/* a row removed elsewhere (#524: another tab, another device or another admin; its row stayed, what the server said
   out of sight): once the list is drawn again without it (rows, as now), that said where it was, a line across a
   table (tr.row-msg) or before the next row, else in box once none is left; focus as focusNear puts it, both in view.
   The list's next drawing, or its next reply (a .row-msg's), takes it away. Focus that bringing the line into view
   leaves out of sight (#532: the card's heading, far above the last ownerless profile's line on a phone) goes to the
   line itself */
function goneSay(rows, at, text, sel, fallback, box) {
  const m = el("p", {class: "msg bad"}), near = rows[at] || rows[at - 1];
  const line = near?.matches("tr") ? el("tr", {class: "row-msg"}, el("td", {colspan: near.cells.length}, m)) : (m.classList.add("row-msg"), m);
  rows[at] ? rows[at].before(line) : near ? near.after(line) : box.prepend(line);
  sayIn(m, text, true);
  focusNear(rows, at, sel, fallback);
  line.scrollIntoView({block: "nearest"});
  const r = document.activeElement.getBoundingClientRect(), pad = getComputedStyle(document.documentElement);
  if (r.top < parseFloat(pad.scrollPaddingTop) || r.bottom > innerHeight - parseFloat(pad.scrollPaddingBottom)) { m.tabIndex = -1; m.focus({preventScroll: true}); }
}
/* An empty state (#199): one sentence saying what would appear here, and the one next step, a link ({href, text}) or
   a button ({onclick, text}); none (null) for someone who can't take it. tag: "div" for a whole card's place, "li"
   inside a list. The records tabs, the Sources and Sharing lists and the journal use it. */
const emptyState = (what, next, {tag = "div", cls = "card"} = {}) => el(tag, {class: `${cls} empty-state`.trim()}, el("p", {text: what}),
  next ? el(next.href ? "a" : "button", {class: `btn primary empty-next ${/\bcard\b/.test(cls) ? "large" : "small"}`, href: next.href, type: next.href ? null : "button", text: next.text, onclick: next.onclick}) : null);
/* the one Copy (#495), for every value there is to copy: a small button named for it ("Copy the upload address", what
   = "the upload address"). Copied, it says "Copied" on itself and to a screen reader, and "Copy" again after 2 s.
   Where the browser keeps the clipboard from the page (plain HTTP) or refuses, it selects the value instead (sel: the
   field or the element showing it) and says so, "Selected" on itself and "Selected: press Ctrl+C or ⌘C to copy it."
   aloud. text: the value, or a function giving it when pressed. Its size: small beside a value shown as text, a
   field's height beside a field (sel an input, #352). -> the button */
function copyBtn(text, {what = "it", sel = null} = {}) {
  const b = el("button", {class: `btn${sel?.tagName === "INPUT" ? "" : " small"} copy-btn`, type: "button", text: "Copy", "aria-label": `Copy ${what}`});
  let back = null;
  b.onclick = async () => {
    let said = "Copied.";
    try {
      if (!navigator.clipboard) throw new Error("no clipboard here");
      await navigator.clipboard.writeText(typeof text === "function" ? text() : text);
      b.textContent = "Copied";
    } catch (e) {
      if (sel && typeof sel.select === "function") { sel.focus(); sel.select(); } else if (sel) getSelection().selectAllChildren(sel);
      b.textContent = "Selected"; said = "Selected: press Ctrl+C or ⌘C to copy it.";
    }
    announce(said);
    clearTimeout(back); back = setTimeout(() => { b.textContent = "Copy"; }, 2000);
  };
  return b;
}
/* a password field (#290), every one in the app (#492: sign-in, setup, Account's, Admin's, a Health Link's passcode).
   A Show button in the field's end shows what's typed, and Hide hides it again (named "Show password", or "Show" and
   the name given: "Show passcode"); pwSent(form) hides it again as the form is really sent, after its own checks
   (#492: hidden on any submit, a mismatch was said over two hidden fields), so a browser saves it as a password.
   With min (a new password), the hint given (the field's aria-describedby) counts toward it as it's typed, "9 of 15",
   and says once that it's long enough. -> {box: the field and its Show, to place where a field made here goes; count(min):
   a new minimum, as the server gives it} */
function pwField(input, {min = 0, hint = null, name = "password"} = {}) {
  const show = el("button", {type: "button", class: "btn quiet small pw-show", id: input.id ? input.id + "Show" : null, "aria-controls": input.id || null});
  const shown = on => {
    input.type = on ? "text" : "password";
    show.textContent = on ? "Hide" : "Show";
    show.setAttribute("aria-label", `${on ? "Hide" : "Show"} ${name}`);
  };
  shown(false);
  show.onclick = () => shown(input.type === "password");
  input.addEventListener("pw-sent", () => shown(false));
  const label = input.closest("label");
  // in its label, the field named by the label's own words, not with Show's after them ("Current password Show …")
  if (label && !input.hasAttribute("aria-label")) input.setAttribute("aria-label", label.textContent.trim());
  const box = el(label ? "span" : "div", {class: "pw"});   // (a label holds phrasing content only)
  input.replaceWith(box);
  box.append(input, show);
  if (!hint) return {box, count() {}};
  const n = el("span", {class: "pw-count"});
  let enough = false;
  const count = () => {
    const len = [...input.value].length, ok = len >= min;
    n.classList.toggle("ok", ok);
    n.replaceChildren(...(ok ? [icon("check"), " Long enough"] : [`${len} of ${min}`]));
    if (ok && !enough && document.activeElement === input) announce(`Long enough: ${len} characters.`);
    enough = ok;
  };
  hint.replaceChildren(el("span", {text: hint.textContent.replace(/\d+/, () => min)}), n);
  input.addEventListener("input", count);
  count();
  return {box, count(m) { if (m) { min = m; hint.firstChild.textContent = hint.firstChild.textContent.replace(/\d+/, m); count(); } }};
}
/* the passwords in a form (or any box) hidden again as it's sent (pwField) */
const pwSent = box => box.querySelectorAll(".pw > input").forEach(i => i.dispatchEvent(new Event("pw-sent")));
/* sign-in's and setup's reason (#505): err, their alert line, put under the field it's about, which is marked invalid,
   described by it (aria-errormessage and -describedby) and given focus, the field's label, the field and the reason
   scrolled into sight together (on a phone the reason was under the button, out of sight); with no field, back at its
   place, after home. Each send clears the last mark ("" and no field) */
function fieldSaid(err, text, field, home) {
  for (const f of document.querySelectorAll(`[aria-errormessage="${err.id}"]`)) {
    const d = f.getAttribute("aria-describedby").split(" ").filter(x => x !== err.id).join(" ");
    f.removeAttribute("aria-invalid"); f.removeAttribute("aria-errormessage");
    if (d) f.setAttribute("aria-describedby", d); else f.removeAttribute("aria-describedby");
  }
  (field ? field.closest(".pw") || field : home).after(err);   // moved before its words, so they're said where it is
  err.textContent = text;
  if (!field) { if (text) err.scrollIntoView({block: "nearest"}); return; }
  field.setAttribute("aria-invalid", "true");
  field.setAttribute("aria-errormessage", err.id);
  field.setAttribute("aria-describedby", [err.id, field.getAttribute("aria-describedby")].filter(Boolean).join(" "));
  field.focus({preventScroll: true});
  err.scrollIntoView({block: "nearest"});
  field.labels?.[0]?.scrollIntoView({block: "nearest"});
}
/* a line of text with each date in it kept on one line (#330), so a tile's "▼ −0.03 since Aug 18, 2026" never leaves
   its year alone below: the text as parts for el(), a date in a <span class="nw">, its textContent unchanged. The
   app's three date formats (dates.js): "Aug 18, 2026", "18 Aug 2026" and "2026-08-18", and "as of" kept as one too
   (#339; the line may break before the date, never between "as" and "of": with it, a date was wider than a narrow
   tile). Each date is anchored: no digit or point before its day, nothing more of a date after its year, and a
   month's short name a word of its own, two letters or more ("ag.", "Aug", "marras", "szept.", #345), a point after a
   day allowed ("18. Aug. 2026"), so "0.03 since 2026-08-18" keeps
   only the date together, never "03 since 2026". A comma right after a date stays with it (#515: Chrome broke the
   line before it, ", 8:37 AM", where a date and its time are one). A time keeps its AM or PM as dates.js writes it
   (#517: a no-break space, on every page, keepDates or not) */
const keepDates = s => String(s).split(/((?<!\p{L})as of(?!\p{L})|(?:(?<![\d.])\d{4}-\d{2}-\d{2}(?![-\d])|(?<![\d.])\d{1,2}\.? \p{L}{2,}\.? \d{4}(?![-\d])|(?<!\p{L})\p{L}{2,}\.? \d{1,2}, \d{4}(?![-\d])),?)/u)
  .map((t, i) => i % 2 ? el("span", {class: "nw", text: t}) : t).filter(t => t !== "");
/* a tile's change in words (#298, #623): "↑ 5.3 lb", "↓ 0.2", or "no change" where its size is written as nothing (m,
   as the tile writes it; u its unit). Never ▲ / ▼, which mark a result's own flags (#301) */
const chgWords = (d, m, u = "") => +String(m).replace(/[^\d.]/g, "") === 0 ? "no change" : `${d > 0 ? "↑" : "↓"} ${m}${u}`;
/* every backend call: same-origin cookie, never cached, and the X-Sanguity header the API requires on changes */
const api = (path, opts = {}) => fetch("api/" + path, {cache: "no-store", credentials: "same-origin", ...opts,
  headers: {...(opts.method && opts.method !== "GET" ? {"X-Sanguity": "1", "Content-Type": "application/json"} : {}), ...opts.headers}});

/* The theme (#45): this browser's own choice (the light/dark toggle) wins, then the account's default (light, dark or
   "system"), remembered here so theme.js can apply it before the next page draws, then the system's. */
function applyTheme(accountDefault) {
  try {
    if (accountDefault !== undefined) {
      if (accountDefault === "light" || accountDefault === "dark") localStorage.setItem("ht-theme-default", accountDefault);
      else localStorage.removeItem("ht-theme-default");
    }
  } catch (e) {}
  let t = null;
  try { t = localStorage.getItem("ht-theme") || localStorage.getItem("ht-theme-default"); } catch (e) {}
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
}

/* The Overview's key-lab tiles (#36), shared by the Overview and the Settings preview: the chosen labs this profile
   has results for, in order. While the choice is still the default list and fewer than half of it (or of six) has
   results here, the profile's most-measured labs fill as many tiles, so a new profile needs no setting. */
const mostMeasured = series => series.filter(s => s && !s.watch && s.pts && s.pts.length)
  .sort((a, b) => b.pts.length - a.pts.length || a.name.localeCompare(b.name)).map(s => s.name);
function keyLabTiles(chosen, series, isDefault) {
  const has = new Set(series.filter(s => s && !s.watch && s.pts && s.pts.length).map(s => s.name));
  const out = chosen.filter(n => has.has(n));
  if (isDefault && out.length < Math.min(6, Math.ceil(chosen.length / 2)))
    for (const n of mostMeasured(series)) { if (out.length >= chosen.length) break; if (!out.includes(n)) out.push(n); }
  return out;
}

/* Labs grouped by panel (#279), the one grouping wherever labs are listed: the Labs list, its charts and heatmap, the
   Overview's key-lab tiles and Settings' lists of labs, so a lab is under the same panel everywhere. labGroups(labs,
   series) -> [{panel, labs}]: labs are names or series, kept as given (a lab in two units is two series of one name,
   joinSeries in same.js, and each keeps its own place, #438), in the order given, under the panel their name came in;
   the panels in the order of their first lab, then "Other": the labs with no panel, or alone in theirs here (a test
   drawn on its own, like Magnesium, whose "panel" is the test). series: the profile's labs, [{name, panel, watch}];
   the records page's SERIES, else Settings' LABS. A name's panel is the first one it came with, as joinSeries keeps
   it. */
const LAB_OTHER = "Other";
function labGroups(labs, series = typeof SERIES !== "undefined" ? SERIES : typeof LABS !== "undefined" ? LABS : []) {
  const panelOf = new Map(), size = new Map(), by = new Map();
  for (const s of series) if (s && !s.watch && !panelOf.has(s.name)) panelOf.set(s.name, s.panel || "");
  for (const p of panelOf.values()) size.set(p, (size.get(p) || 0) + 1);
  for (const x of labs) {
    const p = panelOf.get(typeof x === "string" ? x : x.name), g = p && size.get(p) > 1 ? panelName(p) : LAB_OTHER;
    if (!by.has(g)) by.set(g, []);
    by.get(g).push(x);
  }
  const other = by.get(LAB_OTHER); by.delete(LAB_OTHER);
  return [...by].map(([panel, labs]) => ({panel, labs})).concat(other ? [{panel: LAB_OTHER, labs: other}] : []);
}
/* a panel's name as it's read wherever it's shown (#441), the panel given by labGroups, the Timeline and the drawer:
   one that came in capitals in sentence case, its abbreviations kept (#451, #458): a word of four letters or fewer is
   kept as written unless it's one of LAB_WORDS (the common short words: "TYPE AND SCREEN" -> "Type and screen"), and
   so is a word with a digit, whose parts in LAB_WORDS alone are recased ("25-hydroxy", "COVID-19"); a hyphened one is
   read part by part ("D-dimer", "NT-PROBNP"); a longer word is recased when it reads as one: a vowel, after
   consonants a word can start with, and no three consonants at its end that a word can't end in ("PROBNP" stays). "W/O" is one word. "IRON AND TIBC" ->
   "Iron and TIBC", "SED RATE (ESR)" -> "Sed rate (ESR)", "CBC W/O DIFF" -> "CBC w/o diff", "ACTH". One in mixed case
   as it came */
const LAB_WORDS = new Set(("AND OR OF WITH W/ W/O TO FOR IN ON BY NO PLUS ONLY SED RATE TIME IRON UREA FREE CELL RED GAS " +
  "ACID URIC DIFF QUAL TEST BODY MASS ZINC LEAD BONE SWAB REACTIVE HYDROXY SPECIFIC TYPE DRUG GAP AUTO MONO GRAM HIGH LOW " +
  "COMP HEP VIT MAG PHOS FLU PREG BETA PAP TOX LOAD CORD BASE FAST HOUR SITE SKIN BILE STOOL").split(" "));
const labWordy = w => /^(?:[^AEIOUY]|[BCDFGKPT][LR]|[CPTW]H[LR]?|S[CKPT]?[LRH]?|S[MNQW]|[DST]W|WR|[GKP]N|P[ST]|RH)?[AEIOUY]/.test(w)
  && !/[^AEIOUYHLRST]{3}$/.test(w);
const panelPart = w => LAB_WORDS.has(w) || w.length > 4 && labWordy(w) ? w.toLowerCase() : w;
const panelWord = t => { const w = t.replace(/\/$/, "");
  return (LAB_WORDS.has(t) || LAB_WORDS.has(w) ? w.toLowerCase() : /\d/.test(w) ? w.replace(/[A-Z]+/g, x => LAB_WORDS.has(x) ? x.toLowerCase() : x)
    : w.replace(/[A-Z]+/g, panelPart)) + t.slice(w.length); };
const panelName = p => /[a-z]/.test(p) ? p : p.replace(/W\/O\b|[A-Z0-9]+(?:-[A-Z0-9]+)*\/?/g, panelWord)
  .replace(/^([^A-Za-z0-9]*)([a-z])/, (m, a, c) => a + c.toUpperCase());
/* a lab day's line under its title, its panels as Labs names them (#451; store.js, for the Timeline, its tips and the
   Overview's Recent): the importers join them with ", ", and a name can hold one ("VITAMIN D, 25-HYDROXY"), so one
   its results came under (their records' titles) is taken whole. The importers cut it at a whole name, with "…"
   (#458); one cut before that at 160 characters, mid-name, has that name left out for the "…" */
function labDaySub(e) {
  const known = (DATA.details[e.id]?.ids || []).map(i => byId.get(i)?.title).filter(Boolean).sort((a, b) => b.length - a.length), out = [];
  for (let s = e.sub; s; ) {
    const k = known.find(x => s.startsWith(x) && (s.length === x.length || s.startsWith(", ", x.length))) || s.split(", ")[0];
    out.push(k === s && e.sub.length === 160 && !known.includes(k) && !k.endsWith("…") ? "…" : panelName(k)); s = s.slice(k.length + 2);
  }
  return out.join(", ");
}
/* a lab day's title once its results are in (the drawer, #458): its values counted, as its Results fact counts them,
   for a day an importer titled by its reports before #458 ("3 lab results" over 12 values) */
function labDayTitle(e, title = e.title) {
  const rs = (DATA.details[e.id]?.ids || []).map(i => DATA.details[i]);
  if (!rs.length || !rs.every(r => r && r.rows)) return title;
  const n = rs.reduce((a, r) => a + r.rows.length, 0);
  return title.replace(/^\d+ lab results?(?= · |$)/, `${n} lab result${n === 1 ? "" : "s"}`);
}

/* A warning when this page came over plain HTTP from somewhere other than this machine, the home network or a
   tailnet (where WireGuard already encrypts): passwords and records would cross the internet readable (ASVS 9.1.1). */
function plainHttpWarning() {
  if (location.protocol !== "http:") return null;
  const h = location.hostname.toLowerCase().replace(/^\[|\]$/g, "");
  const ip4 = h.match(/^(\d+)\.(\d+)\.\d+\.\d+$/);
  const local = h === "localhost" || h.endsWith(".localhost") || h.endsWith(".local") || h.endsWith(".lan") ||
    h.endsWith(".home.arpa") || h.endsWith(".internal") || h.endsWith(".ts.net") || !h.includes(".") && !h.includes(":") ||
    (ip4 && (+ip4[1] === 10 || +ip4[1] === 127 || (+ip4[1] === 192 && +ip4[2] === 168) || (+ip4[1] === 172 && +ip4[2] >= 16 && +ip4[2] <= 31) ||
             (+ip4[1] === 169 && +ip4[2] === 254) || (+ip4[1] === 100 && +ip4[2] >= 64 && +ip4[2] <= 127))) ||
    h === "::1" || /^f[cd]/.test(h) || h.startsWith("fe80:");
  return local ? null : "This page isn't using HTTPS, so your password and records travel unencrypted. " +
    "Put Sanguity behind HTTPS (a reverse proxy or Tailscale) before using it over the internet.";
}

/* passkeys (WebAuthn): the server's JSON options in, the browser's answer back out as JSON (base64url) */
const b64u = {
  dec: s => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)), c => c.charCodeAt(0)).buffer,
  enc: b => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""),
};
/* where passkeys can work: a browser that has them, in a secure context, at a name: a browser refuses them at an IP
   address, over HTTPS too (#527: sign-in offered one there, and only pressing it said so). h: a hostname, to check one */
const ipHost = (h = location.hostname) => /^[\d.]+$/.test(h) || h.includes(":");
const passkeysWork = (h = location.hostname) => !!(window.PublicKeyCredential && navigator.credentials && window.isSecureContext) && !ipHost(h);
const _creds = list => (list || []).map(c => ({...c, id: b64u.dec(c.id)}));
async function passkeyCreate(o) {
  const c = await navigator.credentials.create({publicKey: {...o, challenge: b64u.dec(o.challenge),
    user: {...o.user, id: b64u.dec(o.user.id)}, excludeCredentials: _creds(o.excludeCredentials)}});
  return {id: c.id, rawId: b64u.enc(c.rawId), type: c.type, clientExtensionResults: c.getClientExtensionResults(),
    response: {clientDataJSON: b64u.enc(c.response.clientDataJSON), attestationObject: b64u.enc(c.response.attestationObject),
      transports: c.response.getTransports ? c.response.getTransports() : []}};
}
async function passkeyGet(o) {
  const c = await navigator.credentials.get({publicKey: {...o, challenge: b64u.dec(o.challenge), allowCredentials: _creds(o.allowCredentials)}});
  const r = c.response;
  return {id: c.id, rawId: b64u.enc(c.rawId), type: c.type, clientExtensionResults: c.getClientExtensionResults(),
    response: {clientDataJSON: b64u.enc(r.clientDataJSON), authenticatorData: b64u.enc(r.authenticatorData),
      signature: b64u.enc(r.signature), userHandle: r.userHandle && r.userHandle.byteLength ? b64u.enc(r.userHandle) : null}};
}
/* Tell the device which passkeys Sanguity no longer accepts, so it can delete them (the WebAuthn Signal API;
   browsers without it just skip this). */
function passkeySignalUnknown(rpId, credentialId) {
  try { if (PublicKeyCredential.signalUnknownCredential) PublicKeyCredential.signalUnknownCredential({rpId, credentialId}).catch(() => {}); } catch (e) {}
}
function passkeySignalAccepted(rpId, userId, ids) {
  try { if (PublicKeyCredential.signalAllAcceptedCredentials) PublicKeyCredential.signalAllAcceptedCredentials({rpId, userId, allAcceptedCredentialIds: ids}).catch(() => {}); } catch (e) {}
}
/* the browser's refusal, in words: cancelled, timed out, or not allowed here */
const passkeyProblem = e => e && e.name === "NotAllowedError" ? "The passkey request was cancelled or timed out."
  : e && e.name === "InvalidStateError" ? "This device already has a passkey for your account."
  : e && e.name === "SecurityError" ? "Passkeys don't work at this address. Open Sanguity at its https:// name."
  : (e && e.message) || "The passkey didn't work.";

/* ---- keyboard and screen-reader basics (#209)
   announce(text, {error}): says a short message to screen readers through the page's one shared status line, two
   visually hidden live regions every page has (#sgStatus, role=status: said when the reader is free, for saves and
   changes; #sgAlert, role=alert: said at once, for errors). Nothing is shown: a message someone should see goes on
   the page too, beside what it's about (sayIn does both). The same words twice in a row are said twice; messages
   within a moment of each other are said together. Use it for anything that changes without moving focus. */
const announce = (() => {
  const box = {}, wait = {}, next = {};
  const make = () => {
    if (box.status || !document.body) return;
    box.status = el("div", {class: "vh sg-live sg-follow", id: "sgStatus", role: "status"});
    box.alert = el("div", {class: "vh sg-live sg-follow", id: "sgAlert", role: "alert"});
    document.body.append(box.status, box.alert);
  };
  make();   // there from the start: a screen reader only follows a live region that was in the page before it changed
  if (!box.status) document.addEventListener("DOMContentLoaded", make);
  return (text, {error = false} = {}) => {
    make();
    const k = error ? "alert" : "status";
    if (!box[k] || !text) return;
    clearTimeout(wait[k]);
    next[k] = [...(next[k] || []), String(text)];
    box[k].textContent = "";   // emptied first, so the same words again are a change too
    wait[k] = setTimeout(() => { box[k].textContent = next[k].join(" "); next[k] = []; }, 60);
  };
})();
/* sayIn(m, text, bad): a page's message line (a .msg beside its Save button, a form's hint): shows the text (or
   nodes: words with a link), marked .bad for an error, and says it through announce(), as an alert when bad. The
   line itself stops being a live region (older markup has role=status), so it isn't said twice. An empty text
   clears it and says nothing. repeat: false for a line redrawn as you type, whose same words again aren't said;
   say: false for words a toast says (below), shown here without being said twice. */
function sayIn(m, text, bad = false, {repeat = true, say = true} = {}) {
  if (!m) return;
  if (m.getAttribute("role") === "status") m.removeAttribute("role");
  const was = m.textContent;
  m.replaceChildren(...[].concat(text == null ? [] : text));
  m.classList.toggle("bad", !!bad);
  if (say && m.textContent && (repeat || m.textContent !== was)) announce(m.textContent, {error: !!bad});
}
/* viewTitle(title, say): the browser tab's title for what's shown ("Labs · Ben" → "Labs · Ben · Sanguity"), and,
   when say is given, those words said politely: for a view changed by a sidebar link or a button elsewhere, which a
   screen reader wouldn't otherwise mention (a tab picked in the tab bar already says it's selected) */
function viewTitle(title, say) {
  document.title = title ? `${title} · Sanguity` : "Sanguity";
  if (say) announce(say);
}
/* scrollTo/scrollIntoView's behavior: "smooth", unless the person asked for less motion (CSS can't turn off a
   script's smooth scroll; app.css turns off the transitions) */
const smoothly = () => matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
/* "Skip to content": the first thing Tab reaches on a page with a top bar, shown while it has focus. It moves focus
   to the page's <main> without changing the address (the records tabs and the sidebars' groups live there). */
(() => {
  const bar = document.querySelector("body > header"), main = document.querySelector("main");
  if (!bar || !main) return;
  main.id ||= "content";
  document.body.prepend(el("a", {class: "skip-link", href: "#" + main.id, text: "Skip to content", onclick: ev => {
    ev.preventDefault();
    main.tabIndex = -1;
    main.focus({preventScroll: true});
    if (main.getBoundingClientRect().top < 0) main.scrollIntoView();
  }}));
})();
/* A table's column names on its cells (#320): on a phone an .acct .tbl hides its header row and each row is a little
   card (app.css), so each cell shows its column's name before its value instead, from data-label (td::before).
   labelCells(table) copies the names; a cell under an empty heading (a row's buttons), spanning columns or with
   nothing in it gets none. The names are the head's last row (a share link's history has its test's name above, #491).
   It's run here for every table.tbl on the account pages as it's drawn or redrawn, so no page labels its own. */
function labelCells(table) {
  const top = table.tHead && table.tHead.rows[table.tHead.rows.length - 1];
  const heads = top ? [...top.cells].flatMap(th => Array(th.colSpan).fill(th.colSpan > 1 ? "" : th.textContent.trim())) : [];
  for (const body of table.tBodies) for (const row of body.rows) {
    let col = 0;
    for (const c of row.cells) {
      const name = c.colSpan === 1 && c.tagName === "TD" && (c.textContent.trim() || c.querySelector("input, select, textarea, button, img, svg"))
        ? heads[col] || "" : "";
      col += c.colSpan;
      if (name && c.dataset.label !== name) c.dataset.label = name;
      else if (!name && c.hasAttribute("data-label")) c.removeAttribute("data-label");
    }
  }
}
(() => {
  const main = document.querySelector("main.acct");
  if (!main) return;
  const all = root => root.querySelectorAll ? [...(root.matches("table.tbl") ? [root] : []), ...root.querySelectorAll("table.tbl")] : [];
  all(main).forEach(labelCells);
  new MutationObserver(recs => {
    const seen = new Set();
    for (const r of recs) {
      const t = r.target.closest && r.target.closest("table.tbl");
      if (t) seen.add(t);
      for (const n of r.addedNodes) for (const x of all(n)) seen.add(x);
    }
    seen.forEach(labelCells);
  }).observe(main, {childList: true, subtree: true});
})();

/* ---- the app's own dialog (#210)
   ask({title, message, action, danger, cancel, field}): asks before doing something, in a <dialog> opened with
   showModal(), not the browser's own pop-up (which ignores dark mode, can't mark the risky button, and on some phones
   is titled with the server's address). It shows the title, the message (words, a list of paragraphs, or nodes),
   Cancel and a button named for the action ("Remove passkey", "Turn off"). danger: the action can't be
   undone (it removes, revokes or turns something off): its button is red and focus starts on Cancel, so a stray
   Enter or Space doesn't do it; otherwise focus starts on the action. field: an input inside the message, focused
   first; Enter in it does the action. Escape or Cancel say no. Resolves true for the action, false otherwise, and
   focus goes back to whatever had it (the button that opened it). Use: if (!await ask({...})) return;
   A plain notice isn't asked: it goes in the page's message line beside what it's about (sayIn, above). */
function ask({title, message = "", action = "OK", cancel = "Cancel", danger = false, field = null} = {}) {
  const opener = document.activeElement, id = "sgAsk" + (ask.n = (ask.n || 0) + 1);
  return new Promise(resolve => {
    const no = el("button", {class: "btn", type: "button", text: cancel, onclick: () => box.close("")});
    const yes = el("button", {class: "btn " + (danger ? "danger" : "primary"), type: "submit", value: "yes", text: action});
    const box = el("dialog", {class: "sg-ask", role: danger ? "alertdialog" : null, "aria-labelledby": id + "t", "aria-describedby": id + "m"},
      el("form", {method: "dialog"},
        el("h2", {id: id + "t", text: title}),
        el("div", {class: "sg-ask-msg", id: id + "m"}, ...[].concat(message).filter(m => m != null && m !== "")
          .map(m => typeof m === "string" ? el("p", {text: m}) : m)),
        el("div", {class: "sg-ask-acts"}, no, yes)));
    box.addEventListener("close", () => {
      liveFollow();   // the status line back out first (below)
      box.remove();
      // asked from a menu's item (Log out, #391): the menu has closed by now, so focus goes to the button that opens it
      // (a sheet's on a phone, nav.js: #269)
      const pop = opener && opener.closest?.("[popover], dialog.sheet"), back = pop && !(pop.open || pop.matches(":popover-open"))
        ? pop.sgFrom || [...document.querySelectorAll("button")].find(b => b.popoverTargetElement === pop) : opener;
      if (back && back.isConnected && back !== document.body) back.focus();
      resolve(box.returnValue === "yes");
    });
    document.body.append(box);
    openDialog(box);   // centred, modal (below)
    (field || (danger ? no : yes)).focus();
  });
}

/* ---- dialogs, side panels and sheets (#262): the app's one <dialog>, shown one of three ways.
   openDialog(d, {as, modal}): shows the <dialog> d (or changes how it's shown, if it's open already):
     as "center"  a dialog over the middle of the page, ask()'s;
        "side"    a panel down the right edge, sliding in (the records' drawer, drawer.js);
        "sheet"   a sheet from the bottom of the screen, for phones (the drawer, at 700 px or narrower): a grab handle
                  on top, and swiped down by it (or by anything in it marked data-grab, not a control) it closes. A
                  sheet still needs a close button of its own: swiping is never the only way (WCAG 2.5.7).
     modal (default true): the page behind is inert, so Tab and a screen reader stay in it, and a scrim covers the
       page; shown with modal false, the page stays usable (the drawer beside the records).
   Escape closes it (a modal one natively; one that isn't, while focus is in it), and focus goes back to what had it
   before it opened. The browser keeps focus inside a modal one: no page writes a focus trap. d.close() closes it;
   its "close" event says it closed, however that was. While a modal one is open the page behind doesn't scroll, nor move sideways (#370)
   (app.css). One that's open already changes between modal and not only while no other modal dialog is open: shown
   again with showModal() it would go on top of an ask() waiting for an answer over it (a phone turned with the drawer
   open, #364), hiding the question and leaving the page inert. It waits until that one closes, then is shown the way
   it was last asked for. */
function openDialog(d, {as = "center", modal = true} = {}) {
  if (!d.sgWired) wireDialog(d);
  d.sgWant = {as, modal};
  const over = d.open && d.matches(":modal") !== modal && [...document.querySelectorAll("dialog:modal")].filter(x => x !== d).at(-1);
  if (over) {
    if (d.sgWait !== over) {
      d.sgWait = over;
      over.addEventListener("close", () => { if (d.sgWait === over) d.sgWait = null; if (d.open) openDialog(d, d.sgWant); }, {once: true});
    }
    return;
  }
  d.classList.add("sg-dlg");
  for (const k of ["center", "side", "sheet"]) d.classList.toggle("sg-dlg-" + k, k === as);
  if (as === "sheet" && !d.querySelector(":scope > .sg-grab")) d.prepend(el("div", {class: "sg-grab", "aria-hidden": "true"}));
  if (d.open && d.matches(":modal") === modal) return;
  const was = document.activeElement;
  // the first modal one: whether the page has a scrollbar that takes room (not one drawn over it, as on a phone), whose
  // room is kept while the page can't scroll, so nothing behind moves sideways (#370, app.css)
  if (modal && !document.querySelector("dialog:modal")) document.documentElement.toggleAttribute("data-sg-bar", innerWidth > document.documentElement.clientWidth);
  if (d.open) d.close();   // shown the other way: closed and opened again at once (its "close" event sees it open)
  else { d.sgBack = was; placeAt(d, {"--drag": 0}); }
  if (modal) d.showModal(); else d.show();
  if (d.contains(was) && was !== document.activeElement) was.focus({preventScroll: true});
  liveFollow();
}
/* what every page shares (.sg-follow: the status line of announce(), #sgStatus and #sgAlert, the toasts, the
   tooltip and a chart's readout, #tip, #755) goes into the modal dialog on top while one is open, and back to the
   page after: what's behind a modal dialog is inert, and under it (a screen reader would hear nothing said there, a
   toast's Undo couldn't be pressed, a readout wouldn't be seen). The
   toasts (#362) go into a dialog's flow, at its foot, so they're never over what's in it (its buttons, what has
   focus): the modal one on top, or else the drawer open beside the page, which they'd cover; out of one they're a
   popover over the page again. A question (ask()) leaves them where they were, inert behind it (#369): asked from a
   toast's Undo, it would show that toast under its own buttons, and Tab from them would land on it. */
function liveFollow() {
  const modal = [...document.querySelectorAll("dialog:modal")].at(-1), home = modal || document.body;
  for (const r of document.querySelectorAll(".sg-follow:not(#sgToasts)")) if (r.parentElement !== home) home.append(r);
  const t = document.getElementById("sgToasts"), to = modal || document.querySelector("dialog:is(.sg-dlg-side,.sg-dlg-sheet)[open]") || document.body;
  if (!t || t.parentElement === to || modal?.matches(".sg-ask")) return;
  const had = t.contains(document.activeElement) ? document.activeElement : null;
  to.append(t);
  if (to !== document.body) t.removeAttribute("popover");
  else if (!t.matches(":popover-open")) { t.setAttribute("popover", "manual"); t.showPopover(); }
  if (had) had.focus({preventScroll: true});   // (a move takes focus away)
  sgFoot();   // (over the page, or not, they're in the foot's layer or not)
}
function wireDialog(d) {
  d.sgWired = true;
  d.addEventListener("close", () => {
    liveFollow();
    if (d.open) return;   // opened again the other way
    const back = d.sgBack;
    d.sgBack = null;
    if (back && back.isConnected && back !== document.body && (d.contains(document.activeElement) || document.activeElement === document.body)) back.focus({preventScroll: true});
  });
  // not while a menu or the rename box is open (a popover that isn't the toasts or the tooltip): the browser closes
  // that after this, and only that (#366, as drawer.js's own Escape does)
  d.addEventListener("keydown", ev => {
    if (ev.key === "Escape" && !ev.defaultPrevented && d.open && !d.matches(":modal") && !document.querySelector(":popover-open:not([popover=manual])")) d.close();
  });
  // a sheet swiped down: by its grab handle, or what's marked data-grab, not a control in it; far enough (a quarter
  // of its height, at most 120 px) or quick, it closes, else it springs back
  d.addEventListener("pointerdown", ev => {
    const g = ev.target.closest(".sg-grab, [data-grab]");
    if (!d.classList.contains("sg-dlg-sheet") || !ev.isPrimary || ev.button !== 0 || !g || !d.contains(g) ||
        ev.target.closest("button, a, input, select, textarea, label, [tabindex]")) return;
    const y0 = ev.clientY, t0 = ev.timeStamp;
    let dy = 0;
    try { g.setPointerCapture(ev.pointerId); } catch (e) {}
    d.classList.add("sg-dragging");
    const move = e => { dy = Math.max(0, e.clientY - y0); placeAt(d, {"--drag": dy}); };
    const up = e => {
      for (const [k, f] of [["pointermove", move], ["pointerup", up], ["pointercancel", up]]) g.removeEventListener(k, f);
      d.classList.remove("sg-dragging");
      if (e.type === "pointerup" && (dy > Math.min(120, d.offsetHeight / 4) || dy > 24 && dy / Math.max(1, e.timeStamp - t0) > 0.6)) d.close();
      else placeAt(d, {"--drag": 0});
    };
    g.addEventListener("pointermove", move); g.addEventListener("pointerup", up); g.addEventListener("pointercancel", up);
  });
}

/* ---- tooltips (#261): data-tip="words" on an element, for what a native title= would say that isn't said on the
   screen otherwise: an icon button's name (✎, ✕, ⋯), a name cut short or written out under its nickname, a figure's
   date or range. Unlike a title, it shows for the keyboard too (on focus, when focus is shown), stays while the
   pointer moves onto it, never covers its element (above it, or below where there's no room: CSS anchor positioning,
   or placed from here where the browser has none), and Escape puts it away without moving the pointer or focus
   (WCAG 1.4.13); a press on its element puts it away too, and it has no timer. While shown it is the element's
   description (aria-describedby), unless the element's name or description already says each line of it (#358: a
   screen reader would hear it twice). It's for what can take focus, or for hovering what's said elsewhere too: a tip
   on a span or a cell, which the keyboard, a tap and a screen reader don't reach, has its words in the page as well,
   as text for screen readers or a focusable control's tip and description (#358). One shared popover="manual", so it
   never closes an open menu. A title= stays where it's purely a hint beside a name that already says it all, on a
   disabled control (which gets no focus or pointer events) and on an iframe, whose title is its name. Charts' own
   readouts (#tip, core.js) are the chart kit's. */
/* placeAt(node, {"--name": px}): where a browser without CSS anchor positioning shows a popover (a tooltip, a menu:
   nav.js), measured by the script, as custom properties app.css's @supports not (anchor-name: --a) rules read */
const placeAt = (node, at) => { for (const [k, v] of Object.entries(at)) node.style.setProperty(k, v + "px"); };
(() => {
  let box = null, on = null, by = null, wait = 0, gone = 0;
  const norm = s => (s || "").replace(/\s+/g, " ").trim().toLowerCase();
  const textOf = ids => (ids || "").split(" ").filter(x => x && x !== "sgTip").map(x => document.getElementById(x)?.textContent || "").join(" ");
  // what the element says already: its name (aria-labelledby, aria-label, its label's words or its own) and description
  const says = t => norm([textOf(t.getAttribute("aria-labelledby")) || t.getAttribute("aria-label") || (t.labels?.[0] || t).textContent,
    textOf(t.getAttribute("aria-describedby"))].join(" "));
  const describe = (t, add) => {
    const ids = (t.getAttribute("aria-describedby") || "").split(" ").filter(x => x && x !== "sgTip");
    if (add) ids.push("sgTip");
    if (ids.length) t.setAttribute("aria-describedby", ids.join(" ")); else t.removeAttribute("aria-describedby");
  };
  function hide() {
    clearTimeout(wait); clearTimeout(gone);
    if (!on) return;
    on.classList.remove("sg-tip-on"); describe(on, false);
    on = by = null;
    if (box.matches(":popover-open")) box.hidePopover();
  }
  function show(t, how) {
    clearTimeout(wait); clearTimeout(gone);
    const text = t.dataset.tip;
    if (!text || !t.isConnected) return hide();
    if (on !== t) hide();
    if (!box) (document.querySelector("dialog:modal") || document.body).append(box = el("div", {class: "sg-tip sg-follow", id: "sgTip", role: "tooltip", popover: "manual"}));
    box.textContent = text;
    on = t; by = how;
    t.classList.add("sg-tip-on");   // the anchor app.css places it from
    describe(t, !text.split("\n").every(l => says(t).includes(norm(l))));
    if (!box.matches(":popover-open")) {
      // focus given back while another popover opens (a menu closed by the next one): shown once that's done
      try { box.showPopover(); } catch (e) { wait = setTimeout(() => { if (on === t) { on = null; show(t, how); } }); return; }
    }
    place();
  }
  // without CSS anchor positioning: above, or below where there's no room above; 8 px inside the screen. Again as the
  // page scrolls or the window changes size, so it stays by its element (#356)
  function place() {
    if (!on || CSS.supports("anchor-name: --a")) return;
    const r = on.getBoundingClientRect(), w = box.offsetWidth, h = box.offsetHeight, vw = document.documentElement.clientWidth;
    placeAt(box, {"--tip-left": Math.max(8, Math.min(r.left + r.width / 2 - w / 2, vw - 8 - w)), "--tip-top": r.top - h - 6 >= 0 ? r.top - h - 6 : r.bottom + 6});
  }
  addEventListener("scroll", place, {capture: true, passive: true});
  addEventListener("resize", place);
  const later = (t, how) => { clearTimeout(wait); wait = setTimeout(() => show(t, how), on ? 0 : 400); };
  document.addEventListener("pointerover", ev => {
    if (ev.pointerType === "touch") return;
    if (box && box.contains(ev.target)) { clearTimeout(gone); return; }   // onto the tooltip itself: it stays
    const t = ev.target.closest && ev.target.closest("[data-tip]");
    if (t === on) clearTimeout(gone);
    else if (t) later(t, "hover");
  }, true);
  document.addEventListener("pointerout", ev => {
    const to = ev.relatedTarget;
    if (!(to && (on && on.contains(to) || box && box.contains(to)))) { clearTimeout(wait); if (on && by === "hover") gone = setTimeout(hide, 200); }
  }, true);
  document.addEventListener("focusin", ev => {
    const t = ev.target;
    // brought wholly into view first, in a strip or table that scrolls sideways: focus leaves one half out (#372)
    if (t.matches && t.matches("[data-tip]:focus-visible")) { t.scrollIntoView({block: "nearest", inline: "nearest"}); show(t, "focus"); }
    else if (on && by === "focus") hide();
  });
  document.addEventListener("focusout", ev => { if (ev.target === on && by === "focus") hide(); });
  document.addEventListener("pointerdown", ev => { if (on && !box.contains(ev.target)) hide(); }, true);
  // Escape: away it goes; one shown by hovering takes the key, so the panel or menu behind stays open
  document.addEventListener("keydown", ev => {
    if (ev.key !== "Escape" || !on) return;
    const hovered = by === "hover";
    hide();
    if (hovered) ev.stopPropagation();
  }, true);
})();

/* ---- a table's wrapper that may scroll (#335, #340)
   scrollBox(attrs, ...kids): el("div", attrs, ...kids), for a wrapper (.tablewrap, .share-scroll) whose table may be
   wider or taller than it. While it scrolls, sideways or down, it's a tab stop and a region, so a keyboard can reach
   what's out of sight and a screen reader says what it is; while all of it shows, it's neither, so it adds no stop
   and no landmark. A ResizeObserver on it and on its table decides, as the window, the text or the rows change. Its
   name stays on: give it an aria-label (or aria-labelledby to its heading); without one it takes the table's caption
   or its column names. */
function scrollBox(attrs, ...kids) {
  const box = el("div", attrs, ...kids);
  // its name, kept aside: a region is named, a plain box isn't (ARIA names no plain div, #347)
  const name = box.hasAttribute("aria-labelledby") ? ["aria-labelledby", box.getAttribute("aria-labelledby")] : ["aria-label", box.getAttribute("aria-label") || (() => {
    const t = box.querySelector("table"), head = t && t.tHead && t.tHead.rows[0];
    return (t && t.caption ? t.caption.textContent.trim() : "") ||
      (head ? [...head.cells].map(c => c.textContent.trim()).filter(Boolean).join(", ") : "") || "Table"; })()];
  box.removeAttribute("aria-label"); box.removeAttribute("aria-labelledby");
  const fit = () => {
    const scrolls = box.scrollWidth > box.clientWidth + 1 || box.scrollHeight > box.clientHeight + 1;
    if (scrolls && box.getAttribute("role") !== "region") { box.setAttribute("tabindex", "0"); box.setAttribute("role", "region"); box.setAttribute(...name); }
    else if (!scrolls && box.hasAttribute("role")) {
      box.removeAttribute("role"); box.removeAttribute(name[0]);
      if (box === document.activeElement) box.setAttribute("tabindex", "-1");   // focus stays put, out of the Tab order
      else box.removeAttribute("tabindex");
    }
  };
  const watch = new ResizeObserver(fit);
  for (const n of [box, ...box.children]) watch.observe(n);
  return box;
}

/* ---- an icon (#257): one of Lucide's, from icons/sprite.svg (tools/build_sprite.py lists them), in place of the
   Unicode glyphs the app drew from whichever font had them. icon("x") is
   <svg class="icon i-x" aria-hidden="true" focusable="false"><use href="…/icons/sprite.svg#i-x"/></svg>: 1em square,
   in the text's colour (app.css .icon), hidden from screen readers, as the button or link it's in is named by its
   words, aria-label or title. icon(name, {label}) is an icon that says something itself, with nothing around it to
   name it (a ✓ for "good"): the icon, then the label as visually hidden text, in a span. The sprite's address is
   taken from this script's own, so a page in a subfolder (connect-return.html, at /connect/return) finds it too, as
   its stylesheets do with ../. */
const ICON_SPRITE = new URL("../icons/sprite.svg", document.currentScript ? document.currentScript.src : location.href).pathname;
function icon(name, {label} = {}) {
  const svg = el("svg:svg", {class: `icon i-${name}`, "aria-hidden": "true", focusable: "false"}, el("svg:use", {href: `${ICON_SPRITE}#i-${name}`}));
  return label ? el("span", {class: "icon-l"}, svg, el("span", {class: "vh", text: label})) : svg;
}

/* ---- ⓘ (#280): a part's background (aboutWords, above) behind a button beside its heading, named "About <heading>"
   (its tip too), opening the words under it as Labs' Legend does: a menu's popover of words (nav.js menuWire, text),
   closed by the button again, Escape or a press elsewhere, focus kept on the button or put back on it; on a phone (700
   px or narrower) in a sheet from the screen's foot, as the menus are, one sheet for them all. The heading is the
   nearest before the words (beside them, or before the box they're in); the ⓘ goes after it, in the row it's in (a
   card's .set-hd or .list-hd), or a .set-hd made around it. With a title, the ⓘ is where the words were. Each is done
   once it's in the page: those there at first now (scripts load after the page's HTML), those made later as soon as
   they're put in. */
const ABOUT_PHONE = matchMedia("(max-width:700px)");
function aboutWire() {
  const later = aboutWire.later;
  let did = 0;
  for (const w of [...later, ...document.querySelectorAll(".about-words:not(.pop-text)")]) {
    if (!w.isConnected) { if ((w.sgTries = (w.sgTries || 0) + 1) > 30) later.delete(w); continue; }
    later.delete(w);
    if (w.classList.contains("pop-text")) continue;
    // its heading: the nearest before it within its card (or section, form, the drawer's body), never one outside
    let h = null;
    const stop = w.parentElement.closest(".card, section, form, dialog, #dbody, main");
    if (!w.dataset.title) for (let n = w; n && !h && n !== stop; n = n.parentElement)
      for (let x = n.previousElementSibling; x && !h; x = x.previousElementSibling)
        h = x.matches("h1, h2, h3, h4") ? x : x.querySelector(":scope > :is(h1, h2, h3, h4)");
    // the heading's own words: not its tags ("default", "not saved") or a count ("Journal (3)"); with none, what the
    // part it's in is named by (the drawer: its title), and it's put where it is
    const named = !h && !w.dataset.title && document.getElementById((w.closest("[aria-labelledby]")?.getAttribute("aria-labelledby") || "").split(" ")[0]);
    const title = w.dataset.title || w.dataset.name || (h ? ([...h.childNodes].filter(c => c.nodeType === 3).map(c => c.data).join("").trim() || h.textContent.trim()).replace(/\s*\(\d[\d,]*\)$/, "")
      : named?.textContent.trim() || "this part");
    const name = "About " + title, id = w.id || aboutId(title);
    const btn = el("button", {class: "btn quiet small", type: "button", id: id + "Btn", "aria-label": name, "data-tip": name, "aria-expanded": false, "aria-controls": id}, icon("info"));
    w.removeAttribute("hidden");
    Object.assign(w, {id, popover: "auto"});
    w.classList.add("menu-list", "pop-text");
    w.setAttribute("role", "group"); w.setAttribute("aria-label", name);
    const box = el("span", {class: "menu pop info"});
    if (h) {
      let row = h.parentElement;
      if (!row.matches(".set-hd, .list-hd, .ov-h")) { row = el("div", {class: "set-hd"}); h.replaceWith(row); row.append(h); }
      h.after(box);
    } else w.replaceWith(box);
    box.append(btn, w);
    did++;
    const wire = () => {
      menuWire({btn, list: w, text: true});
      btn.addEventListener("click", () => { if (ABOUT_PHONE.matches) aboutSheet(btn, w); });
      aboutMode(btn);
    };
    if (typeof menuWire === "function") wire(); else document.addEventListener("DOMContentLoaded", wire, {once: true});
  }
  if (later.size) requestAnimationFrame(() => aboutWire());
  if (did) aboutWire.after?.();   // (sidebar.js: a lone card's ⓘ beside the h1)
}
aboutWire.later = new Set();
/* its id, from what it's about (#447): the same each time its part is drawn again, so focus on its button and its words
   left open are found again there (store.js keepFocus); a second of the same name on the page gets a number */
function aboutId(title) {
  const base = "about-" + (title.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "") || "part");
  let id = base;
  for (let n = 2; document.getElementById(id); n++) id = base + "-" + n;
  return id;
}
/* an ⓘ left open, kept through a redraw of its part (#447, store.js keepFocus): the ⓘ of its id, drawn again, opens
   too; on a phone the sheet showing its words stays, the new ⓘ's now (its words and focus go back there as it closes).
   aboutOpen() before the redraw, aboutKeep(what it gave) after */
const aboutOpen = () => ({pops: [...document.querySelectorAll(".pop-text:popover-open")].map(w => w.id), sheet: aboutSheet.d?.open ? aboutSheet.d.sgShows : null});
function aboutKeep({pops, sheet}) {
  for (const id of pops) {
    const w = document.getElementById(id), btn = w && document.getElementById(id + "Btn");
    if (btn && !w.matches(":popover-open")) w.showPopover({source: btn});
  }
  const d = aboutSheet.d;
  if (!sheet || sheet.btn.isConnected || !d.open) return;
  const btn = document.getElementById(sheet.btn.id), w = btn && document.getElementById(btn.getAttribute("aria-controls"));
  if (!w) return;
  if (d.sgBack === sheet.btn) d.sgBack = btn;
  w.replaceChildren();   // (its words are in the sheet)
  d.sgShows = {btn, w}; d.sgFrom = btn;
  btn.setAttribute("aria-expanded", "true");
}
/* a popover's on a wider screen, a sheet's on a phone (the button's click opens it: aboutSheet) */
// (an ⓘ whose words have gone with a part drawn again has nothing to show: it goes too, #487's phone run)
const aboutMode = btn => { const w = document.getElementById(btn.getAttribute("aria-controls")); if (!w) { btn.closest(".info")?.remove(); return; } if (ABOUT_PHONE.matches) w.hidePopover?.(); btn.popoverTargetElement = ABOUT_PHONE.matches ? null : w; };
ABOUT_PHONE.addEventListener("change", () => { for (const b of document.querySelectorAll(".info > button")) aboutMode(b); });
function aboutSheet(btn, w) {
  let d = aboutSheet.d;
  if (!d) {
    d = aboutSheet.d = phoneSheet("", el("div", {class: "sheet-body pop-text"}));
    d.addEventListener("close", () => { const s = d.sgShows; if (!s) return; s.w.append(...d.querySelector(".sheet-body").childNodes); s.btn.setAttribute("aria-expanded", "false"); d.sgShows = null; });
  }
  d.querySelector(".sheet-hd h2").textContent = w.getAttribute("aria-label");
  d.querySelector(".sheet-body").append(...w.childNodes);
  d.sgShows = {btn, w}; d.sgFrom = btn;
  openDialog(d, {as: "sheet"});
  btn.setAttribute("aria-expanded", "true");
}
aboutWire();

/* ---- toasts (#263): toast(text, {action, onAction, actions, timeout}) says that something has happened, with what
   can be done about it, at the foot of the screen on every page: "Saved “Key labs”. · Undo", "Imported 6 lab results ·
   Open Labs · Undo". action and onAction are a button and what it does (an Undo); actions more of them, each
   {text, onClick} or a link {text, href, onClick} (onClick, if any, before the link is followed). An onClick that
   gives back false wasn't done (a question it asked was answered No): the toast stays. A toast with something to do
   (an Undo) stays until it's done, put away or sent away by a fourth (#362: on a touch screen nothing else could keep
   it, and an Undo may be the only way back), or its actions are taken away (#522); one that only says stays at least 10 seconds (timeout, in ms, can only
   make it longer), its time stopping while the pointer is on it or focus is in it. Its close button puts it away, as
   Escape does with focus in it. Alt+N takes focus to the newest toast's first button (not while typing in a field: on a Mac, Option+N types ñ,
   #361); no toast takes focus by itself. Focus that went into the toasts, however it got there, goes back where it
   came from when its toast goes, however it goes (acted on, put away, or sent away by a fourth; to a dialog's first
   control if it came from the dialog itself, #368), and stays on a button while its action runs. Up to three show at once, the newest at the foot; a fourth sends the oldest away.
   They're one region named Notifications (a landmark a screen reader can go to), its list a polite live region, so
   each toast is said once as it appears: its words are said only there, not through announce() as well (a page's own
   line beside it shows them with sayIn's say: false). The region is a popover over the page, in the foot's layer with
   the page's bar (sgFoot, above), clear of which the page scrolls what takes focus; while a modal dialog (not a question) or the drawer is open it's in that dialog's flow, at
   its foot, over nothing in it (liveFollow, above), and its buttons work there. An action that fails says so in the toast,
   which stays. What can't be undone, or touches security, still asks first (ask()). */
/* the screen's foot as one layer (#544): a phone's tabs, the page's bar (sidebar.js's, .sg-bar-foot: fixed there, in
   its column) and above it the toasts over the page, measured as one. What the page takes from it (app.css):
   --sg-foot-bar, the bar's top from the screen's foot, under the toasts; --sg-foot, the layer's top, which the page's
   end scrolls above (the body's padding) and a review's bar (import-jobs.js), sticky in its card, keeps above;
   --sg-room, the scroll padding, the most of those with a gap, so what takes focus is clear of all of them. A review's
   bar in a phone's sheet (drop.js) makes room in that sheet's body (--sg-bar-room). Measured again as any of them,
   or the page, changes size (what's gone from the page let go) and as the window does; sgFoot() asks for it */
const sgFoot = (() => {
  let raf = 0, was = 0;
  const seen = new Set(), ro = new ResizeObserver(() => fit());
  const px = (n, at, v) => v ? placeAt(n, {[at]: Math.ceil(v)}) : n.style.removeProperty(at);
  const fit = () => raf || (raf = requestAnimationFrame(() => {
    raf = 0;
    const html = document.documentElement, bars = [...document.querySelectorAll(".sg-bar")], t = document.getElementById("sgToasts");
    for (const e of seen) if (!e.isConnected) { ro.unobserve(e); seen.delete(e); }
    for (const e of [document.body, t?.firstElementChild, ...bars]) if (e && !seen.has(e)) { seen.add(e); ro.observe(e); }
    // the page's bar, in its column (its box's content, after a sidebar's: main.sets.with-nav's first column), and
    // the toasts over it there; with no bar, still there, never over the sidebar (#550)
    const bar = bars.find(b => b.matches(".sg-bar-foot") && !b.hidden && !b.closest("dialog")), p = bar ? bar.parentElement : document.querySelector("main.with-nav");
    let l = 0, w = 0;
    if (p) {
      const s = getComputedStyle(p), cols = s.display === "grid" ? s.gridTemplateColumns.split(" ") : [];
      const pl = parseFloat(s.paddingLeft) + (cols.length > 1 ? parseFloat(cols[0]) + parseFloat(s.columnGap) : 0);
      l = p.getBoundingClientRect().left + p.clientLeft + pl; w = p.clientWidth - pl - parseFloat(s.paddingRight);
    }
    px(html, "--sg-bar-l", l); px(html, "--sg-bar-w", w);
    const under = bar ? innerHeight - bar.getBoundingClientRect().top : 0;
    px(html, "--sg-foot-bar", under);
    // the stack as tall as the newest toasts that fit in it whole: none cut at its top to its Undo alone (#550); the
    // older scroll into it, as all do when the newest alone doesn't fit (in a dialog's flow: its own, app.css; the
    // palette's, a quarter of the screen, fitted so too: #570)
    t?.style.removeProperty("max-height");
    if (t?.parentElement.matches("body, dialog.palette")) {
      const s = getComputedStyle(t), pad = parseFloat(s.paddingTop) + parseFloat(s.paddingBottom), gap = parseFloat(getComputedStyle(t.firstElementChild).rowGap);
      const hs = [...t.firstElementChild.children].map(e => e.getBoundingClientRect().height).reverse();
      let h = -gap, k = 0;
      while (k < hs.length && h + gap + hs[k] <= parseFloat(s.maxHeight) - pad) h += gap + hs[k++];
      if (k && k < hs.length) placeAt(t, {"max-height": h + pad});
    }
    const top = Math.max(under, t?.parentElement === document.body && t.querySelector(".sg-toast") ? innerHeight - t.getBoundingClientRect().top : 0);
    px(html, "--sg-foot", top);
    // a review's bars: the room each takes from the foot of what scrolls, its own height and what's under it
    let room = top && top + 8;
    const sheets = new Map();
    for (const b of bars) if (b !== bar && !b.hidden && b.offsetHeight) {
      const r = b.offsetHeight + (parseFloat(getComputedStyle(b).bottom) || 0) + 8, s = b.closest(".sheet-body");
      if (s) sheets.set(s, Math.max(sheets.get(s) || 0, r + (parseFloat(getComputedStyle(s).paddingBottom) || 0)));
      else if (!b.closest("dialog")) room = Math.max(room, r);
    }
    px(html, "--sg-room", room);
    for (const s of document.querySelectorAll(".sheet-body")) px(s, "--sg-bar-room", sheets.get(s));
    // grown over what has focus (a toast come as focus moved, #387's saved line), that's brought clear of it again, if
    // it fits (not a review's whole card, #281)
    const a = document.activeElement, r = room > was && a?.closest && !a.closest("#sgToasts, .sg-bar, dialog") ? a.getBoundingClientRect() : null;
    if (r && r.height && r.top < innerHeight && r.bottom > innerHeight - room && r.height < innerHeight - room - parseFloat(getComputedStyle(html).scrollPaddingTop))
      a.scrollIntoView({block: "nearest"});
    was = room;
  }));
  addEventListener("resize", fit);
  // a box for text, given focus, is brought into view whole (as far as it fits), clear of all that: the browser
  // brings only its caret's line (#544: My questions, under the toasts but its first line)
  document.addEventListener("focusin", e => {
    const a = e.target;
    if (a.localName === "textarea" && !a.closest("dialog")) requestAnimationFrame(() => {
      if (document.activeElement === a && a.getBoundingClientRect().bottom > innerHeight - parseFloat(getComputedStyle(document.documentElement).scrollPaddingBottom)) a.scrollIntoView({block: "nearest"});
    });
  });
  return fit;
})();
const toast = (() => {
  let region = null, list = null, back = null;
  const make = () => {
    // every page with app.css, where its look is: not sign-in or setup, which have nothing to report or undo
    if (region || !document.body || !document.querySelector('link[rel=stylesheet][href$="app.css"]')) return;
    list = el("div", {class: "sg-toast-list", "aria-live": "polite", "aria-relevant": "additions"});
    region = el("section", {class: "sg-toasts sg-follow", id: "sgToasts", "aria-label": "Notifications", popover: "manual"}, list);
    document.body.append(region);
    region.showPopover();
    liveFollow();   // into a dialog, if one is open
    sgFoot();   // measured, and watched from now: on the page too, where nothing else asks (Sources, #550)
  };
  make();   // there from the start, as announce()'s lines are: a live region is followed only if it was there first
  if (!region) document.addEventListener("DOMContentLoaded", make);
  // where focus came from into the toasts (Alt+N, Tab or a click), to go back to: not a question asked from a toast
  // (its answer gives focus back to the toast) and not nowhere
  const from = ev => { const r = ev.relatedTarget; if (r && !region.contains(r) && !r.closest("dialog.sg-ask")) back = r; };
  // (a <select> takes no typed text: Alt+N works from one, #368)
  const typing = e => !!e?.closest?.("textarea, [contenteditable]:not([contenteditable=false])") ||
    e?.tagName === "INPUT" && !/^(checkbox|radio|button|submit|reset|range|color|file|image)$/.test(e.type);
  document.addEventListener("keydown", ev => {
    if (!ev.altKey || ev.ctrlKey || ev.metaKey || ev.code !== "KeyN" || !list || !list.lastElementChild || typing(ev.target)) return;
    ev.preventDefault();
    const t = list.lastElementChild;
    if (!region.contains(document.activeElement)) back = document.activeElement;
    t.querySelector("button, a").focus();
  });
  return (text, {action, onAction, actions = [], timeout = 10000} = {}) => {
    make();
    if (!list) { announce(text); return {close() {}, node: null}; }
    const acts = [...(action ? [{text: action, onClick: onAction}] : []), ...actions];
    const words = el("p", {class: "sg-toast-text", text});
    let left = Math.max(10000, timeout), start = 0, timer = 0, over = false, t = null;
    const gone = () => {
      clearTimeout(timer);
      if (!t.isConnected) return;
      const had = t.contains(document.activeElement);
      t.remove();
      sgFoot();   // the stack refitted (#570, below)
      if (had) {
        // where focus was, or what took its place when the page was drawn again (the same id); else another toast
        let to = !back || back === document.body ? null : back.isConnected ? back : back.id ? document.getElementById(back.id) : null;
        // a dialog itself (a click on its text focuses it): its first control, not the dialog (#368)
        if (to?.tagName === "DIALOG") to = [...to.querySelectorAll("button, a[href], input, select, textarea")].find(c => !c.disabled && c.getClientRects().length) || to;
        if (to) { back = null; to.focus({preventScroll: true}); }
        else list.lastElementChild?.querySelector("button, a").focus();
      }
    };
    const acting = () => !!t.querySelector(".sg-toast-acts");   // (taken away, it only says: #522)
    const run = () => { clearTimeout(timer); if (!acting()) { start = Date.now(); timer = setTimeout(gone, left); } };   // (one to act on stays)
    const pause = () => { if (timer) { clearTimeout(timer); timer = 0; left = Math.max(2000, left - (Date.now() - start)); } };
    const resume = () => { if (!over && !t.contains(document.activeElement)) run(); };
    // its words and its actions in one box, which wraps the actions under the words, and its ✕ beside that box, on the
    // first line: never wrapped onto a line of its own (#389)
    t = el("div", {class: "sg-toast", role: "group", "aria-label": "Notification"},
      el("div", {class: "sg-toast-body"}, words,
      acts.length ? el("div", {class: "sg-toast-acts"}, ...acts.map(a => a.href ? el("a", {class: "btn small", href: a.href, text: a.text, onclick: a.onClick})
        : el("button", {class: "btn small", type: "button", text: a.text, onclick: async ev => {
          // not disabled while it runs (aria-disabled): focus stays on it, to go back from when the toast goes
          const b = ev.currentTarget;
          if (b.getAttribute("aria-disabled") === "true") return;
          b.setAttribute("aria-disabled", "true");
          try { if (await a.onClick() === false) b.removeAttribute("aria-disabled"); else gone(); }   // false: not done (a question answered No)
          catch (e) { b.removeAttribute("aria-disabled"); words.textContent = `${text} ${a.text} didn't work: ${e.message}`; }
        }}))) : null),
      el("button", {class: "btn quiet sg-toast-x", type: "button", "aria-label": "Dismiss", onclick: gone}, icon("x")),
      acts.length ? el("span", {class: "vh", text: " Alt+N goes to it."}) : null);
    // its actions taken away (an Undo done elsewhere: import-jobs.js undoneElsewhere), it only says, so it goes in
    // time as one that only says does (#522: it stayed, over Import history's foot)
    if (acts.length) new MutationObserver((_, o) => { if (!acting()) { o.disconnect(); resume(); } }).observe(t.firstElementChild, {childList: true});
    t.addEventListener("pointerenter", () => { over = true; pause(); });
    t.addEventListener("pointerleave", () => { over = false; resume(); });
    t.addEventListener("focusin", ev => { from(ev); pause(); });
    t.addEventListener("focusout", ev => { if (!t.contains(ev.relatedTarget)) resume(); });
    t.addEventListener("keydown", ev => { if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); gone(); } });
    while (list.children.length >= 3) list.firstElementChild.sgGone();   // focus in it goes back too
    t.sgGone = gone;
    list.append(t);
    // (the list's ResizeObserver alone missed a fourth the height of the oldest it sent away: the stack's height kept
    // for the toasts before, the newest two cut, #570)
    sgFoot();
    run();
    return {close: gone, node: t};
  };
})();

/* Log out, the same on every page (#391). What the page hasn't saved (its beforeunload guards: Settings' sections,
   Type records in, an upload on its way) is asked about first, in the app's own dialog: signed out first, there'd be
   nothing left to save it with, and the browser's "Leave site?" would come too late. Said yes to, the guards stand
   aside (sgLeaving), and pwa.js deletes what the device kept (sg:logout). The server not reached (offline), the
   sign-in there is ended once it is, and the sign-in page says so (pwa.js SgLogout, #422). The other open tabs are
   asked too, and named (#432): signed out, they go to the sign-in page, and what they hadn't saved is lost as well. */
let sgLeaving = false;
async function logOut() {
  const unsaved = new Event("beforeunload", {cancelable: true});
  dispatchEvent(unsaved);
  const here = unsaved.defaultPrevented, tabs = await SgLogout.elsewhere(), there = [...new Set(tabs)].sort();
  const list = there.length > 1 ? there.slice(0, -1).join(", ") + " and " + there.at(-1) : there[0];
  if ((here || tabs.length) && !await ask({title: "Log out with changes not saved?", danger: true, action: "Log out", cancel: "Stay",
      message: !tabs.length ? "What's on this page and not saved yet will be lost. Stay to save it first." :
        `Changes not saved ${here ? "on this page and " : ""}in ${list} (${tabs.length > 1 ? "other tabs" : "another tab"}) will be lost. Stay to save them first.`})) return;
  sgLeaving = true;
  document.dispatchEvent(new Event("sg:logout"));
  let out = false;
  try { out = (await api("logout", {method: "POST"})).ok; } catch (e) {}
  if (!out && !SgLogout.later()) {   // nowhere to note it for later: not signed out, and said so
    sgLeaving = false;
    toast("You're still signed in: logging out needs the server. Try again once you're online.");
    return;
  }
  SgLogout.tell();   // the other tabs, to the sign-in page (#432)
  location.replace("login.html");
}
