/* Sanguity · Settings page (#35): how things look. A profile's settings and the person's own (Display), in the
   sections the server's registry (server/settings.py) gives, each setting with a simple editor for its kind, and per
   section one Save, Discard changes and "Reset to defaults" (#174). Viewers see the profile's settings without
   editors; their own (per-user) settings they can change, and someone with no profile yet still has those (#169).
   A new kind of setting adds an editor to EDITORS. What used to be here and isn't about how things look has its own
   page now (MOVED, at the end). */
"use strict";   // $, el and api come from util.js
let ME, PID = null, LAB_NAMES = [], LABS = [], NICK = {}, HAS_CLAIMS = false;

async function call(path, method = "GET", body) {
  const r = await api(path, {method, body: body === undefined ? undefined : JSON.stringify(body)});
  if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
  const j = r.status === 204 ? null : await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((typeof j.detail === "string" && j.detail) || (Array.isArray(j.detail) ? "Please check the fields." : r.status + " " + r.statusText));
  return j;
}
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
$("#logout").onclick = logOut;   // util.js: asks first about what isn't saved (#391)

/* ---------------------------------------------------------------- editors: each builds its controls from a value
   and reads the value back (get), calling changed() on every edit */
const small = (text, label, onclick) => el("button", {class: "btn small", type: "button", "aria-label": label, onclick}, text);   // text: words or an icon
const beside = (text, label, onclick) => el("button", {class: "btn", type: "button", "aria-label": label, onclick}, text);   // a button in a row of fields: their height (#352)

/* an ordered list of lab names: move up, down, remove, add (suggesting the profile's own labs) */
function labsEditor(value, changed, ro) {
  let list = [...value];
  const box = el("div", {class: "set-list"}), ol = el("ol", {class: "set-labs"});
  const input = el("input", {list: "labNames", placeholder: "Lab name", "aria-label": "Lab to add", maxlength: 120});
  function draw() {
    ol.replaceChildren(...list.map((n, i) => el("li", {},
      el("span", {class: "set-name", text: n}),
      ro ? null : el("span", {class: "set-acts"},
        small(icon("arrow-up"), `Move ${n} up`, () => { if (i) { [list[i - 1], list[i]] = [list[i], list[i - 1]]; draw(); changed(); ol.children[i - 1]?.querySelector("button")?.focus(); } }),
        small(icon("arrow-down"), `Move ${n} down`, () => { if (i < list.length - 1) { [list[i + 1], list[i]] = [list[i], list[i + 1]]; draw(); changed(); ol.children[i + 1]?.querySelectorAll("button")[1]?.focus(); } }),
        small("Remove", `Remove ${n}`, () => { list.splice(i, 1); draw(); changed(); })))));
    if (!list.length) ol.append(el("li", {class: "muted", text: "None"}));
  }
  const add = () => { const n = input.value.trim().replace(/\s+/g, " "); if (n && !list.includes(n)) { list.push(n); draw(); changed(); } input.value = ""; input.focus(); };
  input.addEventListener("keydown", ev => { if (ev.key === "Enter") { ev.preventDefault(); add(); } });
  draw();
  box.append(ol);
  if (!ro) box.append(el("div", {class: "set-add"}, input, el("button", {class: "btn", type: "button", text: "Add", onclick: add})));
  return {node: box, get: () => list};
}

/* rows of inputs (a table on wide screens, stacked on phones); cols: [{key, label, type, only, ...attrs}]. A
   checkbox column with only: true is ticked in one row at most (ticking it in one row unticks the others); a radio
   column is one choice across the rows (#259: a milestone's Primary), its radios one group. */
let rowsEditors = 0;
function rowsEditor(rows, cols, changed, ro, blank, cls = "") {
  const body = el("div", {class: "set-rows c" + cols.length + (cls ? " " + cls : "")}), group = "rows" + (++rowsEditors) + "-";
  let made = 0;
  const head = el("div", {class: "set-row set-head", "aria-hidden": "true"}, ...cols.map(c => el("span", {text: c.label})), el("span"));
  const tick = c => c.type === "checkbox" || c.type === "radio";
  const only = (c, input) => { if (c.only && input.checked) for (const x of body.querySelectorAll(`[data-k="${c.key}"]`)) if (x !== input) x.checked = false; };
  const make = r => {
    const id = group + (++made) + "-";   // c.of: the column that names the row, said with a radio's label ("Primary Diagnosis", #353)
    const row = el("div", {class: "set-row"}, ...cols.map(c => el("label", {class: tick(c) ? "set-tick" : null},
      el("span", {class: "set-lbl", id: id + c.key + "-l", text: c.label}),
      el("input", {"data-k": c.key, id: id + c.key, "aria-labelledby": c.of ? `${id}${c.key}-l ${id}${c.of}` : null, type: c.type || "text", name: c.type === "radio" ? group + c.key : null, value: tick(c) ? null : r[c.key] ?? "", checked: tick(c) ? !!r[c.key] : null,
        disabled: ro, oninput: ev => { if (tick(c)) only(c, ev.target); changed(); }, ...(c.attrs || {})}))));
    row.append(ro ? el("span") : beside("Remove", "Remove this row", () => { row.remove(); changed(); }));
    return row;
  };
  body.append(head, ...rows.map(make));
  const box = el("div", {}, body);
  if (!ro) box.append(el("div", {class: "set-add"}, el("button", {class: "btn small", type: "button", text: "Add a row",
    onclick: () => { const r = make(blank); body.append(r); r.querySelector("input").focus(); changed(); }})));
  const read = () => [...body.querySelectorAll(".set-row:not(.set-head)")].map(row =>
    Object.fromEntries(cols.map(c => { const i = row.querySelector(`[data-k="${c.key}"]`), v = i.value;
      return [c.key, tick(c) ? i.checked : c.type === "number" ? (v === "" ? null : +v) : v.trim()]; })));
  return {node: box, read};
}

function namesEditor(value, changed, ro, setting) {
  const isLab = setting.key === "labNicknames";
  const cols = [{key: "from", label: isLab ? "Name in the records" : "Tag in the records", attrs: {maxlength: 120, ...(isLab ? {list: "labNames"} : {})}},
                {key: "to", label: "Shown as", attrs: {maxlength: 120}}];
  const r = rowsEditor(Object.entries(value).map(([from, to]) => ({from, to})), cols, changed, ro, {from: "", to: ""});
  return {node: r.node, get: () => Object.fromEntries(r.read().filter(x => x.from || x.to).map(x => [x.from, x.to]))};
}

/* ---------------------------------------------------------------- #43 visits counted as workouts: "visits titled X
   count as workouts of N minutes". The text is found anywhere in the title; "Regular expression" (advanced) makes
   it a pattern, which the server checks is valid and safe before saving (server/settings.py safe_regex). */
function rulesEditor(value, changed, ro) {
  const cols = [{key: "match", label: "Visit title contains", attrs: {maxlength: 200, placeholder: "physical therapy"}},
                {key: "name", label: "Count as", attrs: {maxlength: 80, placeholder: "Physical therapy"}},
                {key: "minutes", label: "Minutes", type: "number", attrs: {min: 1, max: 600, step: 1}},
                {key: "regex", label: "Regular expression", type: "checkbox"}];
  const r = rowsEditor(value, cols, changed, ro, {match: "", name: "", minutes: 45, regex: false}, "rules");
  return {node: r.node, get: () => r.read().filter(x => x.match || x.name)
    .map(x => ({match: x.match, regex: x.regex, name: x.name, minutes: x.minutes}))};   // the server's order, for same()
}

/* ---------------------------------------------------------------- #39 milestones: a date and a name each, one of
   them primary (or none: then the first counts). The server keeps them in date order. */
function milestonesEditor(value, changed, ro) {
  const cols = [{key: "date", label: "Date", type: "date", attrs: {min: "1900-01-01", max: "2100-12-31", required: true}},
                {key: "label", label: "Name", attrs: {maxlength: 60, placeholder: "Diagnosis"}},
                {key: "primary", label: "Primary", type: "radio", of: "label"}];
  const r = rowsEditor(value, cols, changed, ro, {date: "", label: "", primary: false}, "ms");
  return {node: r.node, get: () => r.read().filter(x => x.date || x.label)
    .map(x => ({date: x.date, label: x.label, ...(x.primary ? {primary: true} : {})}))};
}

/* any other value: its JSON, for a kind this page has no editor for yet */
function jsonEditor(value, changed, ro) {
  const ta = el("textarea", {class: "set-json", rows: 4, disabled: ro, oninput: changed, "aria-label": "Value (JSON)"});
  ta.value = JSON.stringify(value, null, 1);
  return {node: ta, get: () => { try { return JSON.parse(ta.value); } catch (e) { return ta.value; } }};
}

/* ---------------------------------------------------------------- #36, #40, #45: key-lab tiles, tabs, theme */

/* a small menu of a row's actions: its ⋯ button opens it by touch, mouse or keyboard (Enter, Space or the down
   arrow; then the arrow keys, and Escape to close it: the app's one menu, SgNav.menu in nav.js). entries: [{text, run}] */
let rowMenus = 0;
function rowMenu(label, entries, act) {
  const id = "rowMenu" + (++rowMenus);
  const btn = el("button", {class: "btn small set-more-btn", type: "button", "data-act": act, "aria-label": label,
    "aria-haspopup": "menu", "aria-expanded": "false", "aria-controls": id}, icon("ellipsis"));
  const items = entries.map(e => el("button", {class: "menu-item", role: "menuitem", type: "button", tabindex: "-1", text: e.text,
    onclick: () => { m.close(); e.run(); }}));
  const list = el("div", {class: "menu-list", id, role: "menu", "aria-label": label}, ...items);
  const m = SgNav.menu({btn, list, wrap: el("div", {class: "menu set-more"}, btn, list)});
  return m.wrap;
}

/* a list put in order three ways: drag an item by its handle (mouse, pen or touch), focus the handle and press
   up or down (Home, End), or its ↑ and ↓ buttons. items are strings; opts.name(item) names it, opts.body(item)
   gives what its row shows, opts.extra(item, i) more buttons; opts.changed() after every change. With opts.more
   (item, i), more entries [{text, run}] for a menu of the row's actions that takes the buttons' place on a phone
   (#174), so each row stays one line. */
function orderList(items, opts) {
  let list = [...items];
  const ol = el("ol", {class: "set-labs set-order" + (opts.more && !opts.ro ? " has-more" : "")});
  const said = el("span", {class: "set-sr", "aria-live": "polite"});
  const refocus = (i, act) => ol.children[i]?.querySelector(`[data-act="${act}"]`)?.focus();
  const tell = i => { said.textContent = `${opts.name(list[i])}: ${i + 1} of ${list.length}.`; };
  const move = (from, to, act) => {
    if (to < 0 || to >= list.length || to === from) return;
    const [x] = list.splice(from, 1); list.splice(to, 0, x);
    draw(); opts.changed(); refocus(to, act); tell(to);
  };
  function drag(ev, li) {
    if (ev.button !== 0) return;
    ev.preventDefault();
    // the list takes the pointer, not the handle: moving the row in the page would drop the handle's capture
    const grip = ol;
    try { ol.setPointerCapture(ev.pointerId); } catch (e) {}
    li.classList.add("dragging");
    const onMove = e => {
      const before = [...ol.children].find(c => { if (c === li) return false; const r = c.getBoundingClientRect(); return e.clientY < r.top + r.height / 2; });
      if (before) { if (li.nextElementSibling !== before) ol.insertBefore(li, before); }
      else if (ol.lastElementChild !== li) ol.append(li);
    };
    const onUp = () => {
      for (const [k, f] of [["pointermove", onMove], ["pointerup", onUp], ["pointercancel", onUp]]) grip.removeEventListener(k, f);
      const mine = list[+li.dataset.i], order = [...ol.children].map(c => list[+c.dataset.i]), to = order.indexOf(mine);
      const moved = !same(order, list);
      list = order; draw();
      if (moved) { opts.changed(); tell(to); }
      refocus(to, "grip");
    };
    grip.addEventListener("pointermove", onMove); grip.addEventListener("pointerup", onUp); grip.addEventListener("pointercancel", onUp);
  }
  function draw() {
    ol.replaceChildren(...list.map((it, i) => {
      const n = opts.name(it), li = el("li", {"data-i": i});
      const grip = opts.ro ? null : el("button", {class: "set-grip btn quiet small", type: "button", "data-act": "grip", title: "Drag to reorder",
        "aria-label": `Reorder ${n}, ${i + 1} of ${list.length}: drag, or press up or down`,
        onkeydown: ev => { const to = {ArrowUp: i - 1, ArrowDown: i + 1, Home: 0, End: list.length - 1}[ev.key]; if (to !== undefined) { ev.preventDefault(); move(i, to, "grip"); } },
        onpointerdown: ev => drag(ev, li)}, icon("grip-vertical"));
      li.append(...[grip, ...opts.body(it, i)].filter(Boolean));
      if (!opts.ro) li.append(el("span", {class: "set-acts"},
        el("button", {class: "btn small", type: "button", "data-act": "up", "aria-label": `Move ${n} up`, disabled: i === 0, onclick: () => move(i, i - 1, i > 1 ? "up" : "down")}, icon("arrow-up")),
        el("button", {class: "btn small", type: "button", "data-act": "down", "aria-label": `Move ${n} down`, disabled: i === list.length - 1, onclick: () => move(i, i + 1, i + 2 < list.length ? "down" : "up")}, icon("arrow-down")),
        ...(opts.extra ? opts.extra(it, i) : [])));
      if (opts.more && !opts.ro) {
        const last = list.length - 1;
        li.append(rowMenu(`Move or remove ${n}`, [
          ...(i > 1 ? [{text: "Move to the top", run: () => move(i, 0, "more")}] : []),
          ...(i > 0 ? [{text: "Move up", run: () => move(i, i - 1, "more")}] : []),
          ...(i < last ? [{text: "Move down", run: () => move(i, i + 1, "more")}] : []),
          ...(i < last - 1 ? [{text: "Move to the bottom", run: () => move(i, last, "more")}] : []),
          ...opts.more(it, i).map(e => ({text: e.text, run: () => {
            e.run();
            // the row may be gone: the focus goes to the row now in its place (or the last one)
            if (!ol.contains(document.activeElement)) refocus(Math.min(i, list.length - 1), "more");
          }})),
        ], "more"));
      }
      return li;
    }));
    if (!list.length) ol.append(el("li", {class: "muted", text: "None"}));
    if (opts.drawn) opts.drawn();
  }
  draw();
  return {node: el("div", {}, ol, said), get: () => list, set: v => { list = [...v]; draw(); }, redraw: draw};
}

const labInfo = n => LABS.find(s => s.name === n);
const nick = n => NICK[n] || n;

/* #36 the Overview's key-lab tiles: the profile's own labs, picked, ordered, and previewed as the Overview shows them */
function tilesEditor(value, changed, ro, setting) {
  const msg = el("span", {class: "msg", role: "status"});
  const preview = el("div", {class: "tiles set-preview", role: "group", "aria-label": "Preview of the Overview's key lab tiles"});
  const sugg = el("div", {class: "set-sugg"});
  const isDefault = list => same(list, setting.default);
  const update = () => { drawPreview(); drawSuggestions(); changed(); };
  const ol = orderList(value, {ro, changed: update, name: nick,
    body: n => { const s = labInfo(n); return [el("span", {class: "set-name"}, nick(n), nick(n) !== n ? el("small", {class: "muted", text: ` (${n})`}) : null),
      el("span", {class: "set-count muted", text: s ? `${s.pts.length} result${s.pts.length === 1 ? "" : "s"}` : "no results here"})]; },
    extra: n => [el("button", {class: "btn small", type: "button", text: "Remove", "aria-label": `Remove ${nick(n)}`,
      onclick: () => { ol.set(ol.get().filter(x => x !== n)); update(); input.focus(); }})],
    more: n => [{text: "Remove", run: () => { ol.set(ol.get().filter(x => x !== n)); update(); }}]});
  function drawPreview() {
    const names = keyLabTiles(ol.get(), LABS, isDefault(ol.get()));
    preview.replaceChildren(...names.map(n => {
      const s = labInfo(n), last = s.pts[s.pts.length - 1];
      return el("div", {class: "tile", "data-lab": n},
        el("span", {class: "lbl"}, el("span", {text: nick(n), "data-tip": nick(n) !== n ? n : null}), nick(n) !== n ? el("span", {class: "vh", text: ` (${n})`}) : null,   // #358
          last[4] === "H" ? el("span", {class: "st hi", text: "▲ High"}) : last[4] === "L" ? el("span", {class: "st lo", text: "▼ Low"}) : null),
        el("span", {class: "val"}, last[5] || String(last[1]), el("small", {text: s.unit || ""})));
    }));
    if (!names.length) preview.append(el("p", {class: "hint", text: LABS.length ? "None of these labs has results in this profile yet." : "This profile has no lab results yet."}));
    else if (isDefault(ol.get()) && names.some(n => !ol.get().includes(n)))
      preview.prepend(el("p", {class: "hint set-wide", text: "Few of the default labs have results here, so the Overview shows this profile's most-measured labs until you choose."}));
  }
  function drawSuggestions() {
    if (ro) return;
    const top = mostMeasured(LABS).filter(n => !ol.get().includes(n)).slice(0, 8);
    sugg.replaceChildren(...(top.length ? [el("span", {class: "muted", text: "Most measured here: "}),
      ...top.map(n => el("button", {class: "chip", type: "button", "aria-label": `Add ${nick(n)}`,
        onclick: () => { ol.set([...ol.get(), n]); update(); }}, icon("plus"), nick(n))),
      el("button", {class: "btn small", type: "button", text: "Use the most-measured labs",
        onclick: () => { ol.set(mostMeasured(LABS).slice(0, 12)); update(); sayIn(msg, "Now the 12 most-measured labs; Save to keep them."); }})] : []));
  }
  const input = el("input", {list: "labNames", placeholder: "Lab name", "aria-label": "Lab to add as a tile", maxlength: 120});
  const add = () => {
    const n = input.value.trim().replace(/\s+/g, " ");
    if (!n) return;
    if (LABS.length && !labInfo(n)) { sayIn(msg, `This profile has no lab called “${n}”. Pick one from the list.`, true); return; }
    if (!ol.get().includes(n)) { ol.set([...ol.get(), n]); update(); }
    input.value = ""; sayIn(msg, ""); input.focus();
  };
  input.addEventListener("keydown", ev => { if (ev.key === "Enter") { ev.preventDefault(); add(); } });
  drawPreview(); drawSuggestions();
  const box = el("div", {class: "set-list"}, ol.node);
  if (!ro) box.append(el("div", {class: "set-add"}, input, el("button", {class: "btn", type: "button", text: "Add", onclick: add})), sugg, msg);
  box.append(el("h4", {class: "set-ph", text: "Preview"}), preview);
  return {node: box, get: () => ol.get()};
}

/* #40 the main tabs: shown or hidden, in order; one at least stays shown */
const TAB_LABEL = {overview: "Overview", health: "Body and activity", timeline: "Timeline", labs: "Labs", costs: "Costs"};   // #172
function tabsEditor(value, changed, ro) {
  const hidden = new Set(value.hidden);
  let ol = null;
  const sync = () => {   // the last shown tab can't be hidden
    if (!ol) return;
    const shown = ol.get().filter(t => !hidden.has(t)).length;
    for (const cb of ol.node.querySelectorAll("input[type=checkbox]")) cb.disabled = ro || (cb.checked && shown === 1);
  };
  ol = orderList(value.order, {ro, name: t => TAB_LABEL[t] || t, changed, drawn: () => sync(),
    body: t => [el("label", {class: "set-name set-check switch"},   // a switch each (#259)
      el("input", {type: "checkbox", role: "switch", checked: !hidden.has(t), "data-tab": t, onchange: ev => { ev.target.checked ? hidden.delete(t) : hidden.add(t); sync(); changed(); }}),
      ` Show ${TAB_LABEL[t] || t}`,
      t === "costs" && !HAS_CLAIMS ? el("small", {class: "muted", text: " · no claims in this profile"}) : null)]});
  sync();
  return {node: ol.node, get: () => ({order: ol.get(), hidden: ol.get().filter(t => hidden.has(t))})};
}

/* a pick-one group of radios (#193): the labels, each with its radio, drawn as one segmented control (app.css .seg) */
const radioSeg = labels => el("div", {class: "seg wrap"}, ...labels);
/* #45 this user's default theme; Dark mode or Light mode in the top bar's menu overrides it for one browser */
function themeEditor(value, changed, ro) {
  const opts = [["system", "Follow the system"], ["light", "Light"], ["dark", "Dark"]];
  const box = el("fieldset", {class: "set-radios"}, el("legend", {class: "set-sr", text: "Default theme"}),
    radioSeg(opts.map(([v, l]) => el("label", {}, el("input", {type: "radio", name: "theme", value: v, checked: v === value, disabled: ro, onchange: changed}), l))));
  let own = null; try { own = localStorage.getItem("ht-theme"); } catch (e) {}
  const note = el("p", {class: "hint", id: "themeOwn"});
  if (own === "light" || own === "dark")
    note.append(`This browser uses its own choice, ${own}, set with Dark mode or Light mode in your menu. `,
      el("button", {class: "btn small", type: "button", text: "Use my default on this browser", onclick: () => {
        try { localStorage.removeItem("ht-theme"); } catch (e) {} applyTheme(); note.textContent = "This browser now uses your default."; }}));
  return {node: el("div", {}, box, note.childNodes.length ? note : null), get: () => box.querySelector("input:checked")?.value || value};
}
/* #225 some of a few choices, each a checkbox (the Overview's home readings as tiles), in the server's order */
function choicesEditor(value, changed, ro, setting) {
  const boxes = Object.entries(setting.choices || {}).map(([id, label]) => el("label", {class: "check"},
    el("input", {type: "checkbox", value: id, checked: value.includes(id) || null, disabled: ro, onchange: changed}), " " + label));
  return {node: el("div", {class: "row set-choices", role: "group", "aria-label": setting.label}, ...boxes),
    get: () => boxes.map(b => b.querySelector("input")).filter(i => i.checked).map(i => i.value)};
}
const EDITORS = {labs: labsEditor, tiles: tilesEditor, names: namesEditor, rules: rulesEditor, tabs: tabsEditor, theme: themeEditor,
  milestones: milestonesEditor, choices: choicesEditor};

/* ---------------------------------------------------------------- dates and times (#42): each person's own time
   zone and date format (dates.js uses them on every page) */
/* the zone is a field you type into to search the IANA zones (#259: a list of about 400 was long to scroll), shown
   with spaces ("America/New York"); only a zone on the list is kept, anything else says so and isn't saved */
let zoneLists = 0;
function zoneEditor(value, changed, ro) {
  let zones = [];
  try { zones = Intl.supportedValuesOf("timeZone"); } catch (e) {}
  const here = browserZone(), shown = z => z.replace(/_/g, " ");
  let cur = value || here;
  for (const z of [cur, here, "UTC"]) if (!zones.includes(z)) zones.unshift(z);
  const id = "zones" + (++zoneLists), msgId = id + "-msg";
  const list = el("datalist", {id}, ...zones.map(z => el("option", {value: shown(z)})));
  const input = el("input", {class: "set-select set-zone", type: "text", list: id, "aria-label": "Time zone", "aria-describedby": msgId,
    autocomplete: "off", spellcheck: "false", disabled: ro, value: shown(cur)});
  const hint = el("span", {class: "hint", id: msgId});
  const use = beside("Use this device's", "Use this device's time zone", () => { input.value = shown(here); pick(); input.focus(); });   // the field's height (#374); hidden now, focus on the field (#376)
  use.hidden = ro;
  const zoneOf = t => zones.find(z => z === t.trim().replace(/ /g, "_")) || zones.find(z => shown(z).toLowerCase() === t.trim().toLowerCase());
  const note = () => { const same = cur === here; use.hidden = ro || same;
    hint.textContent = input.getAttribute("aria-invalid") === "true" ? "Pick a time zone from the list, such as America/New York."
      : same ? "" : `This device is set to ${shown(here)}.`; };
  const pick = (final = false) => {   // a zone on the list is the value; what's typed so far only once it's left
    const z = zoneOf(input.value);
    if (z) { input.removeAttribute("aria-invalid"); if (final) input.value = shown(z); if (z !== cur) { cur = z; changed(); } }
    else if (final) input.setAttribute("aria-invalid", "true");
    note();
  };
  input.oninput = () => pick();
  input.onchange = () => pick(true);
  note();
  return {node: el("div", {class: "set-add"}, input, list, use, hint), get: () => cur};
}
function formatEditor(value, changed, ro) {
  const sel = el("select", {class: "set-select", "aria-label": "Date format", disabled: ro, onchange: changed},
    ...Object.entries(DATE_FORMATS).map(([k, example]) => el("option", {value: k, text: example})));
  sel.value = value || browserFormat();
  return {node: el("div", {class: "set-add"}, sel), get: () => sel.value};
}
EDITORS.timezone = zoneEditor;
EDITORS.dateformat = formatEditor;

/* #37 saved Trends views: each a name, its labs and whether it's the default (one at most); renamed, changed,
   removed or added here, saved from Trends too (trends-views.js) */
function viewsEditor(value, changed, ro) {
  const box = el("div", {class: "set-views"}), list = el("div", {class: "set-vlist"});
  let n = 0;
  const make = v => {
    const id = "tv" + (++n), name = el("input", {id: id + "n", value: v.name, maxlength: 60, disabled: ro, oninput: changed, "data-k": "name"});
    const def = el("input", {type: "radio", name: "tvDefault", checked: v.default ? "" : null, disabled: ro, onchange: changed, "data-k": "default"});
    const labs = labsEditor(v.labs, changed, ro);
    const item = el("div", {class: "set-view"},
      el("div", {class: "row"}, el("label", {for: id + "n", text: "Name"}), name,
        el("label", {class: "check"}, def, "Default"),
        ro ? null : beside("Remove", `Remove the view ${v.name || "without a name"}`, () => { item.remove(); changed(); })),
      labs.node);
    item._labs = labs;
    return item;
  };
  list.append(...value.map(make));
  const none = el("p", {class: "hint", text: "No saved views yet. Pick labs on Labs and press Save view…"});
  box.append(...present(list, value.length ? null : none));
  if (!ro) box.append(el("div", {class: "set-add"},
    small("Add a view", "Add a view", () => { none.remove(); const it = make({name: "", labs: [], default: false}); list.append(it); it.querySelector("input").focus(); changed(); }),
    small("No default", "No default view", () => { list.querySelectorAll("[data-k=default]").forEach(r => { r.checked = false; }); changed(); })));
  return {node: box, get: () => [...list.children].map(it => ({name: it.querySelector("[data-k=name]").value.trim(),
    labs: it._labs.get(), default: it.querySelector("[data-k=default]").checked}))};
}
EDITORS.views = viewsEditor;
/* #41 this user's units: one group of choices per part (lab tests, weights, temperatures), from the server's list */
function unitsEditor(value, changed, ro, st) {
  const parts = [["lab", "Lab tests"], ["weight", "Weight"], ["temperature", "Temperature"], ["height", "Height"]];   // height: #194
  const boxes = parts.map(([part, label]) => el("fieldset", {class: "set-radios", "data-part": part}, el("legend", {text: label}),
    radioSeg(Object.entries((st.choices || {})[part] || {}).map(([v, l]) => el("label", {},
      el("input", {type: "radio", name: "units-" + part, value: v, checked: v === (value[part] || "reported"), disabled: ro, onchange: changed}), l)))));
  return {node: el("div", {class: "set-units"}, ...boxes),
    get: () => Object.fromEntries(parts.map(([part], i) => [part, boxes[i].querySelector("input:checked")?.value || value[part] || "reported"]))};
}
EDITORS.units = unitsEditor;

/* ---------------------------------------------------------------- #44 reference ranges from settings (ranges.js) */
/* the birth date and sex the rows' ages and sexes are matched against: About this person's (#194, Sharing), loaded
   with the sections; the preview of what the rows colour uses them */
let RANGE_CFG = {birthDate: "", sex: "", overrides: []};
function rangesUses() {
  const b = RANGE_CFG.birthDate, s = RANGE_CFG.sex;
  return el("p", {class: "hint", id: "rangesUses"}, "Ages and sexes are matched against ",
    el("a", {href: `sharing.html?profile=${PID}#about`, text: "About this person"}), ": ",
    b ? `born ${fmtD(zParse(b))}` : "no birth date (rows with an age band aren't used)", "; ",
    s ? `sex used for reference ranges ${s}` : "no sex for reference ranges (only rows for anyone are used)", ".");
}
/* the profile's lab series with numeric results whose report gives no range or flag: [{name, unit, pts}] */
const unranged = () => LABS.filter(s => !s.watch).map(s => ({name: s.name, unit: s.unit || "",
  pts: s.pts.filter(p => typeof p[1] === "number" && p[2] == null && p[3] == null && !String(p[4] || "").trim())})).filter(s => s.pts.length);
function rangesEditor(value, changed, ro) {
  const SEX = [["any", "Anyone"], ["female", "Female"], ["male", "Male"]];
  const cols = [["lab", "Lab (name in the records)"], ["unit", "Unit"], ["sex", "Sex"], ["ageFrom", "Age from"], ["ageTo", "Age to"], ["low", "Low"], ["high", "High"]];
  const body = el("div", {class: "set-rows rng"});
  body.append(el("div", {class: "set-row set-head", "aria-hidden": "true"}, ...cols.map(([, l]) => el("span", {text: l})), el("span")));
  const num = (k, v, attrs) => el("input", {"data-k": k, type: "number", value: v ?? "", disabled: ro, oninput: update, step: "any", ...attrs});
  const make = r => {
    const field = (k, l, input) => el("label", {}, el("span", {class: "set-lbl", text: l}), input);
    const row = el("div", {class: "set-row"},
      field("lab", cols[0][1], el("input", {"data-k": "lab", value: r.lab || "", list: "labNames", maxlength: 120, disabled: ro, oninput: update})),
      field("unit", "Unit", el("input", {"data-k": "unit", value: r.unit || "", maxlength: 30, disabled: ro, oninput: update})),
      field("sex", "Sex", el("select", {"data-k": "sex", disabled: ro, onchange: update}, ...SEX.map(([v, l]) => el("option", {value: v, text: l, selected: (r.sex || "any") === v ? "" : null})))),
      field("ageFrom", "Age from", num("ageFrom", r.ageFrom, {min: 0, max: 130, step: 1, placeholder: "any"})),
      field("ageTo", "Age to", num("ageTo", r.ageTo, {min: 0, max: 130, step: 1, placeholder: "any"})),
      field("low", "Low", num("low", r.low)),
      field("high", "High", num("high", r.high)));
    row.append(ro ? el("span") : beside("Remove", "Remove this range", () => { row.remove(); update(); }));
    return row;
  };
  const read = () => [...body.querySelectorAll(".set-row:not(.set-head)")].map(row => {
    const v = k => row.querySelector(`[data-k="${k}"]`).value, n = k => v(k) === "" ? null : +v(k);
    return {lab: v("lab").trim(), unit: v("unit").trim(), sex: v("sex"), ageFrom: n("ageFrom"), ageTo: n("ageTo"), low: n("low"), high: n("high")};
  }).filter(r => r.lab || r.unit || r.low != null || r.high != null);
  const add = r => { const row = make(r); body.append(row); row.querySelector("input").focus(); update(); };
  const sugg = el("div", {class: "set-sugg"}), preview = el("p", {class: "hint", id: "rangesPreview", role: "status"});
  function update() {
    const rows = read(), cfg = {...RANGE_CFG, overrides: rows}, without = unranged();
    const total = without.reduce((a, s) => a + s.pts.length, 0);
    let hit = 0, out = 0;
    for (const s of without) for (const p of s.pts) { const r = rangeRowFor(cfg, s.name, s.unit, String(p[0]).slice(0, 10)); if (r) { hit++; if ((r.high != null && p[1] > r.high) || (r.low != null && p[1] < r.low)) out++; } }
    preview.textContent = !total ? "Every lab result here has a range or flag from its report, so none of these would be used yet."
      : `${total} result${total === 1 ? "" : "s"} here ha${total === 1 ? "s" : "ve"} no range on ${total === 1 ? "its" : "their"} report; these ranges fit ${hit}` +
        (hit ? `, ${out} of them outside.` : ".");
    if (!ro) {
      const have = new Set(rows.map(r => rNorm(r.lab) + "\u001f" + rUnit(r.unit)));
      const left = without.filter(s => !have.has(rNorm(s.name) + "\u001f" + rUnit(s.unit))).sort((a, b) => b.pts.length - a.pts.length).slice(0, 8);
      sugg.replaceChildren(...(left.length ? [el("span", {class: "muted", text: "Results without a range: "}),
        ...left.map(s => el("button", {class: "chip", type: "button", "aria-label": `Add a range for ${s.name} in ${s.unit || "no unit"}`,
          onclick: () => add({lab: s.name, unit: s.unit, sex: "any"})}, icon("plus"), `${nick(s.name)}${s.unit ? " (" + s.unit + ")" : ""} · ${s.pts.length}`))] : []));
    }
    changed();
  }
  body.append(...value.map(make));
  const box = el("div", {}, body);
  if (!ro) box.append(el("div", {class: "set-add"}, el("button", {class: "btn small", type: "button", text: "Add a range", onclick: () => add({sex: "any"})})), sugg);
  box.append(preview, rangesUses());   // #194
  const ed = {node: box, get: read};
  queueMicrotask(update);
  return ed;
}
EDITORS.ranges = rangesEditor;

/* ---------------------------------------------------------------- the sections (#174): each setting with its
   editor, and one Save per section for every setting changed in it; Discard changes puts them back as saved. A
   setting changed and not saved yet says so, its section has data-dirty (its group is marked in the sidebar,
   settings-nav.js, and the section's Save stays in view while it's long), and leaving the page asks first. Whose
   settings these are is said once per group, above them; a setting says it only where its section mixes them. */
const FROM = {default: "default", install: "set for this server", profile: "set for this profile", user: "your own"};
const SCOPE_TAG = {install: "for this server", profile: "for this profile", user: "just for you"};

/* one setting: its name, where its value comes from, its editor, and a line for what went wrong saving it.
   changed() after every edit. */
const SET_ITEMS = {};   // key -> the setting's item drawn last: put(value) fills its editor in, unsaved (presets.js, #186)
function settingNode(st, changed, mixed) {
  const ro = !st.can_change;
  const msg = el("span", {class: "msg", role: "status"});
  const from = el("span", {class: "tag", text: FROM[st.from] || st.from});
  const unsaved = el("span", {class: "tag set-unsaved", text: "not saved"});
  const node = el("div", {class: "set", "data-key": st.key, "data-scope": st.scope});
  let ed = null;
  const item = {st, node, msg,
    dirty: () => !ro && !!ed && !same(ed.get(), st.value),
    value: () => ed.get(),
    saved(value) { st.value = value; st.from = st.scope; from.textContent = FROM[st.from] || st.from; mark(); },
    back(value, was) { st.value = value; st.from = was; from.textContent = FROM[st.from] || st.from; mark(); changed(); }};   // put back by an Undo (#359)
  const mark = () => node.classList.toggle("set-changed", item.dirty());
  const make = v => (EDITORS[st.kind] || jsonEditor)(v, () => { sayIn(msg, ""); mark(); changed(); }, ro, st);
  ed = make(st.value);
  item.put = v => { const old = ed.node; ed = make(v); old.replaceWith(ed.node); mark(); changed(); };   // #186
  SET_ITEMS[st.key] = item;
  node.append(...present(el("h3", {class: "sub"}, st.label, from, mixed && SCOPE_TAG[st.scope] ? el("span", {class: "tag", text: SCOPE_TAG[st.scope]}) : null, unsaved),
    st.help ? el("p", {class: "hint", text: st.help}) : null,
    ed.node, msg));
  return item;
}

/* the section's settings as the server has them now: {key: {value, from}} */
async function stored(base) {
  const d = await call(`${base}/sections`);
  return Object.fromEntries(d.sections.flatMap(sec => sec.settings).map(st => [st.key, {value: st.value, from: st.from}]));
}
/* a save's Undo (#263, #359): each setting it saved goes back to what it was (the default it came from taken off
   again, where a profile's setting can be; otherwise the value it had), but only one that still holds what this save
   wrote: one changed again since is left, and said to be. The section is drawn again as saved, unless it has changes
   not saved: those are kept, its other settings shown as they are now. pid: the profile it saved (PID then): another
   picked since keeps its page as it is, and a toast says what was undone in the first (#367), unless the section's
   settings are the person's own (Appearance, Dates, Units: the same on every profile), drawn and said as without a
   switch (#377). */
async function undoSave(base, reload, sec, was, pid) {
  const now = await stored(base), back = [], since = [];
  for (const w of was) {
    if (!w.wrote || !now[w.key] || !same(now[w.key].value, w.wrote.value)) { since.push(w.label); continue; }
    const path = `${base}/${encodeURIComponent(w.key)}`;
    if ((w.from === "default" || w.from === "install") && !base.startsWith("settings/")) await call(path, "DELETE");
    else await call(path, "PUT", {value: w.value});
    back.push(w);
  }
  const text = [back.length ? `Undone: ${quoted(back.map(w => w.label))} ${back.length === 1 ? "is" : "are"} as before.` : "",
    since.length ? `${quoted(since)} ${since.length === 1 ? "was" : "were"} changed since, so ${since.length === 1 ? "it's" : "they're"} left as ${since.length === 1 ? "it is" : "they are"}.` : ""].join(" ").trim();
  if (PID !== pid && !sec.settings.every(st => st.scope === "user")) { toast(`${ME.profiles.find(x => x.id === pid)?.name || "The other profile"}: ${text}`); return; }
  const card = () => document.getElementById("sec-" + sec.id);
  if (!card()?.hasAttribute("data-dirty")) { await reload({section: sec.id, text}, sec.id); return; }
  // changes not saved in it: kept, each setting's saved value now the one put back (so one changed back to it is
  // no longer a change); a setting not changed here shows it
  const after = await stored(base).catch(() => now);
  for (const w of back) {
    const it = SET_ITEMS[w.key];
    if (!it || !card().contains(it.node) || !after[w.key]) continue;
    const dirty = it.dirty();
    it.back(after[w.key].value, after[w.key].from);
    if (!dirty) it.put(after[w.key].value);
  }
  await reload();   // the other sections, as they are now: this one, still with changes, stays
  if (card()?.hasAttribute("data-dirty")) sayIn(card().querySelector(".set-foot > .msg"), text + " Your changes not saved yet are kept.");
  else await reload({section: sec.id, text}, sec.id);
}

/* “A”, “A” and “B”, “A”, “B” and “C” */
const quoted = labels => labels.map((l, i) => (i === 0 ? "" : i === labels.length - 1 ? " and " : ", ") + `“${l}”`).join("");

/* a section: its title, Reset to defaults, its settings and one Save for them all. opts.base is where its settings
   are changed (the API's profiles/<id>/settings unless given); opts.reload(note, force) draws the sections again
   after a save or a reset (note: {section, text, quiet} to say in this one, or only show with quiet, a toast saying
   it; force: this section's id, to draw it again even with changes not saved); opts.note is said in it once drawn
   (shown only, with opts.quiet). A save is said by a toast (#263) with an Undo, which puts back what each setting
   saved was: its value, or the default it came from (taken off again, where the setting can be). */
function sectionCard(sec, opts = {}) {
  const base = opts.base || (PID == null ? "settings/user" : `profiles/${PID}/settings`);
  const reload = opts.reload || load;
  const msg = el("span", {class: "msg"}); sayIn(msg, opts.note || "", false, {say: !opts.quiet});   // said too (util.js, #209), or by a toast
  const anyEditable = sec.settings.some(st => st.can_change);
  const changedHere = sec.settings.some(st => st.can_change && st.from !== "default" && st.from !== "install");
  const mixed = new Set(sec.settings.map(st => st.scope)).size > 1;
  const save = el("button", {class: "btn primary small", type: "button", text: "Save", disabled: true});
  const discard = el("button", {class: "btn small", type: "button", text: "Discard changes", disabled: true});
  let card = null;
  const changed = () => {
    const n = items.filter(it => it.dirty()).length;
    save.disabled = discard.disabled = !n;
    card?.toggleAttribute("data-dirty", n > 0);
    if (n && !msg.classList.contains("bad")) sayIn(msg, "");
  };
  const items = sec.settings.map(st => settingNode(st, () => changed(), mixed));
  save.onclick = async () => {
    const todo = items.filter(it => it.dirty());
    if (!todo.length) return;
    const pid = PID, had = card.contains(document.activeElement);   // (before Save is disabled, which takes focus away)
    save.disabled = discard.disabled = true;
    sayIn(msg, "Saving…");
    const done = [], was = [];
    for (const it of todo) {
      const value = it.value(), before = {key: it.st.key, label: it.st.label, value: it.st.value, from: it.st.from};
      try { await call(`${base}/${encodeURIComponent(it.st.key)}`, "PUT", {value}); }
      catch (e) {
        // the ones before it are saved; this one and the rest stay as changed, to put right and save again
        sayIn(it.msg, e.message, true);
        changed();
        sayIn(msg, (done.length ? `Saved ${quoted(done)}. ` : "") + `${quoted([it.st.label])} isn't saved: ${e.message}`, true);
        it.node.scrollIntoView({block: "nearest"});
        if (had && !card.contains(document.activeElement)) save.focus({preventScroll: true});   // (enabled again)
        if (done.length) reload();
        return;
      }
      it.saved(value);
      done.push(it.st.label); was.push({...before, wrote: {value}});
    }
    changed();
    const said = `Saved ${quoted(done)}.`;
    await reload({section: sec.id, text: said, quiet: true});
    // drawn again, Save with it (and disabled now): focus on the section's heading, to come back to from the toast
    // (#368), without scrolling the page up to it: what was saved stays in view (#376)
    if (had && !document.getElementById("sec-" + sec.id)?.contains(document.activeElement)) document.getElementById(`sec-${sec.id}-hd`)?.focus({preventScroll: true});
    // what the server keeps now (as it stored it), for the Undo to tell whether a setting was changed again since
    const kept = await stored(base).catch(() => null);
    if (kept) for (const w of was) if (kept[w.key]) w.wrote = kept[w.key];
    toast(said, {action: "Undo", onAction: () => undoSave(base, reload, sec, was, pid)});
  };
  discard.onclick = () => {
    const fresh = sectionCard(sec, {...opts, note: "Changes discarded: back as saved."});
    card.replaceWith(fresh);
    fresh.querySelector("h2").focus();
  };
  const reset = el("button", {class: "btn small", type: "button", text: "Reset to defaults", disabled: !changedHere, hidden: !anyEditable,
    onclick: async () => {
      if (!await ask({title: `Reset the ${sec.title} settings?`, message: "Each of them goes back to its default.",
                      action: "Reset to defaults", danger: true})) return;
      reset.disabled = true;
      try { await call(`${base}/reset`, "POST", {section: sec.id}); await reload({section: sec.id, text: `${sec.title} settings are back to their defaults.`}, sec.id); }
      catch (e) { sayIn(msg, e.message, true); reset.disabled = false; }
    }});
  card = el("section", {class: "card", id: "sec-" + sec.id},
    el("div", {class: "set-hd"}, el("h2", {id: `sec-${sec.id}-hd`, tabindex: -1, text: sec.title}), reset),
    sec.help ? el("p", {class: "hint", text: sec.help}) : null,
    sec.id === "overview" ? presetsBlock(sec) : null,   // "Start from a condition" (presets.js, #186)
    ...items.map(it => it.node),
    anyEditable ? el("div", {class: "set-foot"}, save, discard, msg) : null);
  return card;
}
const unsavedSections = () => [...document.querySelectorAll("#sections > section[data-dirty]")];
addEventListener("beforeunload", ev => { if (!sgLeaving && unsavedSections().length) { ev.preventDefault(); ev.returnValue = ""; } });
/* another profile: changes not saved here would be lost. Asked before anything else follows the picker, so saying
   no leaves the whole page on this profile: the change stops here and the picker shows this profile while the app's
   dialog asks (#210); saying yes picks the other profile again, and that change goes on to everything else. */
let switchAsked = false;
document.addEventListener("change", async ev => {
  if (ev.target.id !== "profile" || PID == null || switchAsked) return;
  const left = unsavedSections().map(c => c.querySelector("h2").textContent);
  if (!left.length) return;
  const pick = ev.target, wanted = pick.value;
  ev.stopImmediatePropagation();
  pick.value = PID;
  if (!await ask({title: "Switch profile and lose your changes?", message: `Your changes to ${quoted(left)} aren't saved.`,
                  action: "Switch and lose changes", cancel: "Stay here", danger: true})) return;
  pick.value = wanted;
  switchAsked = true;
  try { pick.dispatchEvent(new Event("change", {bubbles: true})); } finally { switchAsked = false; }
}, true);

/* the profile's sections (or, with no profile, the person's own), drawn again: one with changes not saved stays as
   it is, unless it's the one forced or all are drawn afresh (another profile); note: {section, text} to say in that
   section */
async function load(note, force, all = false) {
  const box = $("#sections");
  let d, merged = null;
  try {
    // #194: About this person's birth date and sex (not on this screen), from the merged settings
    [d, merged] = await Promise.all([call(PID == null ? "settings/user/sections" : `profiles/${PID}/settings/sections`),
      PID == null ? null : call(`profiles/${PID}/settings`).catch(() => null)]);
  }
  catch (e) { sayIn($("#loadMsg"), e.message, true); if (all) box.replaceChildren(); return; }   // (changes not saved stay)
  sayIn($("#loadMsg"), "");
  await presetsLoad(PID, d.role);   // Start from a condition's offer, for the Overview section (presets.js, #186)
  d.sections = d.sections.filter(sec => sec.settings.some(st => st.scope !== "install"));   // the server's own: on the Admin page (#167)
  const every = d.sections.flatMap(sec => sec.settings);
  NICK = every.find(st => st.key === "labNicknames")?.value || {};
  { const ov = every.find(x => x.key === "ranges.overrides"); RANGE_CFG = {...rangeConfig(merged), ...(ov ? {overrides: ov.value} : {})}; }   // #44
  { const th = every.find(st => st.key === "theme"); if (th) applyTheme(th.value); }   // #45: the saved default, here too
  document.querySelector("main.sets").dataset.role = d.role || "";   // whose settings each group's are (settings-nav.js)
  const cards = d.sections.map(sec => {
    const old = document.getElementById("sec-" + sec.id);
    if (!all && old && old.parentNode === box && old.hasAttribute("data-dirty") && sec.id !== force) return old;
    return sectionCard(sec, {note: note && note.section === sec.id ? note.text : "", quiet: !!(note && note.quiet)});
  });
  const inPlace = !all && box.children.length === cards.length && d.sections.every((sec, i) => box.children[i].id === "sec-" + sec.id);
  if (inPlace) cards.forEach((c, i) => { if (box.children[i] !== c) box.children[i].replaceWith(c); });
  else box.replaceChildren(...cards);
}

/* the profile's labs: their names, suggested when adding a lab or a nickname, and their results, for the key-lab
   tiles' suggestions and preview; and whether it has claims (said beside the Costs tab) */
async function loadLabNames() {
  LAB_NAMES = []; LABS = []; HAS_CLAIMS = false;
  try {
    const r = await api(`profiles/${PID}/data`);
    if (r.status === 200) {
      const d = await r.json();
      LABS = (d.labs || []).filter(s => s && typeof s.name === "string" && Array.isArray(s.pts) && s.pts.length);
      LAB_NAMES = [...new Set(LABS.map(s => s.name))].sort();
      HAS_CLAIMS = (d.costs || []).length > 0 || (d.events || []).some(e => e.k === "claim");
    }
  } catch (e) {}
  $("#labNames").replaceChildren(...LAB_NAMES.map(n => el("option", {value: n})));
}

/* ---------------------------------------------------------------- what moved
   Settings is how things look (#169); what was here besides went to its own page, and its old addresses open it
   there, keeping the profile (?profile=2): the server's (#167) to Admin, the phone's upload secret (#166) to
   Sources, the calendar feed (#168) to Sharing. */
const MOVED = {"#outside": "admin.html#outside", "#sources": "admin.html#apps", "#sec-sync": "admin.html#sec-sync",
  "#watch": "sources.html?#phone", "#calendar": "sharing.html?#calendar"};
function moved() {
  const to = MOVED[location.hash];
  if (to) location.replace(to.replace("?", location.search));
  return !!to;
}
addEventListener("hashchange", moved);

/* ---------------------------------------------------------------- start */
(async () => {
  if (moved()) return;
  try { ME = await call("me"); } catch (e) { return; }
  await datesReady();   // dates in this person's time zone and format (dates.js)
  if (ME.user.must_change_pw) { location.replace("account.html#password"); return; }
  if (ME.user.needs_two_factor) { location.replace("account.html#twofa"); return; }
  $("#who").textContent = ME.user.username;
  { const w = plainHttpWarning(); if (w) { $("#insecure").textContent = w; $("#insecure").hidden = false; } }
  if (!ME.profiles.length) {   // no profile yet: their own settings, and how to get one
    $("#noProfiles").hidden = false;
    SgNav.profiles([]);   // no profile: the top bar's picker (nav.js, #170) stays out of sight
    $("#intro").hidden = false;
    load(null, null, true);
    return;
  }
  const sel = $("#profile");
  SgNav.profiles(ME.profiles);   // the top bar's picker (nav.js, #170): the address's profile, else this browser's last
  const pick = () => { PID = +sel.value; loadLabNames().then(() => load(null, null, true)); };
  sel.onchange = pick;
  $("#intro").hidden = false;
  pick();
})();
