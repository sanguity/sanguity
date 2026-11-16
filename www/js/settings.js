/* Sanguity · Settings page (#35): a profile's settings, in the sections the server's registry (server/settings.py)
   gives, each with a simple editor for its kind and Save, and "Reset to defaults" per section. Viewers see the
   profile's settings without editors; their own (per-user) settings they can change. A new kind of setting adds
   an editor to EDITORS. */
"use strict";   // $, el and api come from util.js
let ME, PID = null, LAB_NAMES = [], LABS = [], NICK = {}, HAS_CLAIMS = false;

async function call(path, method = "GET", body) {
  const r = await api(path, {method, body: body === undefined ? undefined : JSON.stringify(body)});
  if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
  const j = r.status === 204 ? null : await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((typeof j.detail === "string" && j.detail) || (Array.isArray(j.detail) ? "Please check the fields." : r.status + " " + r.statusText));
  return j;
}
const sayIn = (m, text, bad = false) => { m.textContent = text; m.classList.toggle("bad", bad); };
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
$("#logout").onclick = async () => { try { await call("logout", "POST"); } catch (e) {} location.replace("login.html"); };

/* ---------------------------------------------------------------- editors: each builds its controls from a value
   and reads the value back (get), calling changed() on every edit */
const small = (text, label, onclick) => el("button", {class: "btn small", type: "button", text, "aria-label": label, onclick});

/* an ordered list of lab names: move up, down, remove, add (suggesting the profile's own labs) */
function labsEditor(value, changed, ro) {
  let list = [...value];
  const box = el("div", {class: "set-list"}), ol = el("ol", {class: "set-labs"});
  const input = el("input", {list: "labNames", placeholder: "Lab name", "aria-label": "Lab to add", maxlength: 120});
  function draw() {
    ol.replaceChildren(...list.map((n, i) => el("li", {},
      el("span", {class: "set-name", text: n}),
      ro ? null : el("span", {class: "set-acts"},
        small("↑", `Move ${n} up`, () => { if (i) { [list[i - 1], list[i]] = [list[i], list[i - 1]]; draw(); changed(); ol.children[i - 1]?.querySelector("button")?.focus(); } }),
        small("↓", `Move ${n} down`, () => { if (i < list.length - 1) { [list[i + 1], list[i]] = [list[i], list[i + 1]]; draw(); changed(); ol.children[i + 1]?.querySelectorAll("button")[1]?.focus(); } }),
        small("Remove", `Remove ${n}`, () => { list.splice(i, 1); draw(); changed(); })))));
    if (!list.length) ol.append(el("li", {class: "muted", text: "None"}));
  }
  const add = () => { const n = input.value.trim().replace(/\s+/g, " "); if (n && !list.includes(n)) { list.push(n); draw(); changed(); } input.value = ""; input.focus(); };
  input.addEventListener("keydown", ev => { if (ev.key === "Enter") { ev.preventDefault(); add(); } });
  draw();
  box.append(ol);
  if (!ro) box.append(el("div", {class: "set-add"}, input, el("button", {class: "btn small", type: "button", text: "Add", onclick: add})));
  return {node: box, get: () => list};
}

/* rows of inputs (a table on wide screens, stacked on phones); cols: [{key, label, type, only, ...attrs}]. A
   checkbox column with only: true is ticked in one row at most (ticking it in one row unticks the others). */
function rowsEditor(rows, cols, changed, ro, blank, cls = "") {
  const body = el("div", {class: "set-rows c" + cols.length + (cls ? " " + cls : "")});
  const head = el("div", {class: "set-row set-head", "aria-hidden": "true"}, ...cols.map(c => el("span", {text: c.label})), el("span"));
  const tick = c => c.type === "checkbox";
  const only = (c, input) => { if (c.only && input.checked) for (const x of body.querySelectorAll(`[data-k="${c.key}"]`)) if (x !== input) x.checked = false; };
  const make = r => {
    const row = el("div", {class: "set-row"}, ...cols.map(c => el("label", {class: tick(c) ? "set-tick" : null},
      el("span", {class: "set-lbl", text: c.label}),
      el("input", {"data-k": c.key, type: c.type || "text", value: tick(c) ? null : r[c.key] ?? "", checked: tick(c) ? !!r[c.key] : null,
        disabled: ro, oninput: ev => { if (tick(c)) only(c, ev.target); changed(); }, ...(c.attrs || {})}))));
    row.append(ro ? el("span") : small("Remove", "Remove this row", () => { row.remove(); changed(); }));
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
                {key: "primary", label: "Primary", type: "checkbox", only: true}];
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
/* a whole number, on or off, some of a few choices (the install's sync.* settings, #101) */
function numberEditor(value, changed, ro) {
  const input = el("input", {type: "number", step: 1, min: 1, value, disabled: ro, oninput: changed, class: "set-num", "aria-label": "Value"});
  return {node: input, get: () => input.value === "" ? null : +input.value};
}
function toggleEditor(value, changed, ro) {
  const input = el("input", {type: "checkbox", checked: value ? "" : null, disabled: ro, onchange: changed});
  return {node: el("label", {class: "check"}, input, "On"), get: () => input.checked};
}
function choicesEditor(value, changed, ro, setting) {
  const boxes = Object.entries(setting.choices || {}).map(([id, label]) => el("label", {class: "check"},
    el("input", {type: "checkbox", value: id, checked: value.includes(id) ? "" : null, disabled: ro, onchange: changed}), label));
  return {node: el("div", {class: "row set-choices"}, ...boxes),
    get: () => boxes.map(b => b.querySelector("input")).filter(i => i.checked).map(i => i.value)};
}

/* ---------------------------------------------------------------- #36, #40, #45: key-lab tiles, tabs, theme */

/* a list put in order three ways: drag an item by its handle (mouse, pen or touch), focus the handle and press
   up or down (Home, End), or its ↑ and ↓ buttons. items are strings; opts.name(item) names it, opts.body(item)
   gives what its row shows, opts.extra(item, i) more buttons; opts.changed() after every change. */
function orderList(items, opts) {
  let list = [...items];
  const ol = el("ol", {class: "set-labs set-order"});
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
      const grip = opts.ro ? null : el("button", {class: "set-grip", type: "button", "data-act": "grip", text: "⠿", title: "Drag to reorder",
        "aria-label": `Reorder ${n}, ${i + 1} of ${list.length}: drag, or press up or down`,
        onkeydown: ev => { const to = {ArrowUp: i - 1, ArrowDown: i + 1, Home: 0, End: list.length - 1}[ev.key]; if (to !== undefined) { ev.preventDefault(); move(i, to, "grip"); } },
        onpointerdown: ev => drag(ev, li)});
      li.append(...[grip, ...opts.body(it, i)].filter(Boolean));
      if (!opts.ro) li.append(el("span", {class: "set-acts"},
        el("button", {class: "btn small", type: "button", text: "↑", "data-act": "up", "aria-label": `Move ${n} up`, disabled: i === 0, onclick: () => move(i, i - 1, i > 1 ? "up" : "down")}),
        el("button", {class: "btn small", type: "button", text: "↓", "data-act": "down", "aria-label": `Move ${n} down`, disabled: i === list.length - 1, onclick: () => move(i, i + 1, i + 2 < list.length ? "down" : "up")}),
        ...(opts.extra ? opts.extra(it, i) : [])));
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
      onclick: () => { ol.set(ol.get().filter(x => x !== n)); update(); input.focus(); }})]});
  function drawPreview() {
    const names = keyLabTiles(ol.get(), LABS, isDefault(ol.get()));
    preview.replaceChildren(...names.map(n => {
      const s = labInfo(n), last = s.pts[s.pts.length - 1];
      return el("div", {class: "tile", "data-lab": n},
        el("span", {class: "lbl"}, el("span", {text: nick(n), title: n}),
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
      ...top.map(n => el("button", {class: "chip", type: "button", "aria-label": `Add ${nick(n)}`, text: `+ ${nick(n)}`,
        onclick: () => { ol.set([...ol.get(), n]); update(); }})),
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
  if (!ro) box.append(el("div", {class: "set-add"}, input, el("button", {class: "btn small", type: "button", text: "Add", onclick: add})), sugg, msg);
  box.append(el("h4", {class: "set-ph", text: "Preview"}), preview);
  return {node: box, get: () => ol.get()};
}

/* #40 the main tabs: shown or hidden, in order; one at least stays shown */
const TAB_LABEL = {overview: "Overview", health: "Health", timeline: "Timeline", labs: "Trends", heatmap: "Lab heatmap", costs: "Costs"};
function tabsEditor(value, changed, ro) {
  const hidden = new Set(value.hidden);
  let ol = null;
  const sync = () => {   // the last shown tab can't be hidden
    if (!ol) return;
    const shown = ol.get().filter(t => !hidden.has(t)).length;
    for (const cb of ol.node.querySelectorAll("input[type=checkbox]")) cb.disabled = ro || (cb.checked && shown === 1);
  };
  ol = orderList(value.order, {ro, name: t => TAB_LABEL[t] || t, changed, drawn: () => sync(),
    body: t => [el("label", {class: "set-name set-check"},
      el("input", {type: "checkbox", checked: !hidden.has(t), "data-tab": t, onchange: ev => { ev.target.checked ? hidden.delete(t) : hidden.add(t); sync(); changed(); }}),
      ` Show ${TAB_LABEL[t] || t}`,
      t === "costs" && !HAS_CLAIMS ? el("small", {class: "muted", text: " · no claims in this profile"}) : null)]});
  sync();
  return {node: ol.node, get: () => ({order: ol.get(), hidden: ol.get().filter(t => hidden.has(t))})};
}

/* #45 this user's default theme; the ◐ toggle on the records page overrides it for one browser */
function themeEditor(value, changed, ro) {
  const opts = [["system", "Follow the system"], ["light", "Light"], ["dark", "Dark"]];
  const box = el("fieldset", {class: "set-radios"}, el("legend", {class: "set-sr", text: "Default theme"}),
    ...opts.map(([v, l]) => el("label", {}, el("input", {type: "radio", name: "theme", value: v, checked: v === value, disabled: ro, onchange: changed}), " " + l)));
  let own = null; try { own = localStorage.getItem("ht-theme"); } catch (e) {}
  const note = el("p", {class: "hint", id: "themeOwn"});
  if (own === "light" || own === "dark")
    note.append(`This browser uses its own choice, ${own}, set with ◐ on the records page. `,
      el("button", {class: "btn small", type: "button", text: "Use my default on this browser", onclick: () => {
        try { localStorage.removeItem("ht-theme"); } catch (e) {} applyTheme(); note.textContent = "This browser now uses your default."; }}));
  return {node: el("div", {}, box, note.childNodes.length ? note : null), get: () => box.querySelector("input:checked")?.value || value};
}
const EDITORS = {labs: labsEditor, tiles: tilesEditor, names: namesEditor, rules: rulesEditor, tabs: tabsEditor, theme: themeEditor,
  number: numberEditor, toggle: toggleEditor, choices: choicesEditor,
  milestones: milestonesEditor};

/* ---------------------------------------------------------------- the sections */
const FROM = {default: "default", install: "set for this server", profile: "set for this profile", user: "your own"};
function settingNode(st, onSaved) {
  const ro = !st.can_change;
  const msg = el("span", {class: "msg", role: "status"});
  const save = el("button", {class: "btn primary small", type: "button", text: "Save", disabled: true, hidden: ro});
  let ed;
  const changed = () => { save.disabled = same(ed.get(), st.value); sayIn(msg, ""); };
  ed = (EDITORS[st.kind] || jsonEditor)(st.value, changed, ro, st);
  save.onclick = async () => {
    save.disabled = true;
    try {
      await call(`profiles/${PID}/settings/${encodeURIComponent(st.key)}`, "PUT", {value: ed.get()});
      await onSaved(`Saved “${st.label}”.`);
    } catch (e) { sayIn(msg, e.message, true); save.disabled = false; }
  };
  const tag = el("span", {class: "tag", text: FROM[st.from] || st.from});
  return el("div", {class: "set", "data-key": st.key},
    el("h3", {class: "sub"}, st.label, tag, st.scope === "user" ? el("span", {class: "tag", text: "just for you"}) : null),
    st.help ? el("p", {class: "hint", text: st.help}) : null,
    ed.node,
    el("div", {class: "row"}, save, msg));
}

async function load(note) {
  const box = $("#sections");
  let d;
  try { d = await call(`profiles/${PID}/settings/sections`); }
  catch (e) { sayIn($("#loadMsg"), e.message, true); box.replaceChildren(); return; }
  sayIn($("#loadMsg"), "");
  const all = d.sections.flatMap(sec => sec.settings);
  NICK = all.find(st => st.key === "labNicknames")?.value || {};
  { const th = all.find(st => st.key === "theme"); if (th) applyTheme(th.value); }   // #45: the saved default, here too
  const editable = d.role === "owner" || d.role === "editor";
  $("#introHint").textContent = editable
    ? "These change the dashboard for everyone this profile is shared with, unless a setting says it's just for you."
    : "You can view this profile, so its settings are shown here as they are; only its owners and editors change them.";
  box.replaceChildren(...d.sections.map(sec => {
    const msg = el("span", {class: "msg", role: "status"});
    const changedHere = sec.settings.some(st => st.can_change && st.from !== "default" && st.from !== "install");
    const anyEditable = sec.settings.some(st => st.can_change);
    const reset = el("button", {class: "btn small", type: "button", text: "Reset to defaults", disabled: !changedHere, hidden: !anyEditable,
      onclick: async () => {
        if (!confirm(`Put the ${sec.title} settings back to their defaults?`)) return;
        reset.disabled = true;
        try { await call(`profiles/${PID}/settings/reset`, "POST", {section: sec.id}); await load({section: sec.id, text: `${sec.title} settings are back to their defaults.`}); }
        catch (e) { sayIn(msg, e.message, true); reset.disabled = false; }
      }});
    const card = el("section", {class: "card", id: "sec-" + sec.id},
      el("div", {class: "set-hd"}, el("h2", {text: sec.title}), reset),
      sec.help ? el("p", {class: "hint", text: sec.help}) : null,
      ...sec.settings.map(st => settingNode(st, text => load({section: sec.id, text}))),
      msg);
    if (note && note.section === sec.id) sayIn(msg, note.text);
    return card;
  }));
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

/* ---------------------------------------------------------------- start */
(async () => {
  try { ME = await call("me"); } catch (e) { return; }
  if (ME.user.must_change_pw) { location.replace("account.html#password"); return; }
  if (ME.user.needs_two_factor) { location.replace("account.html#twofa"); return; }
  $("#who").textContent = ME.user.username;
  { const w = plainHttpWarning(); if (w) { $("#insecure").textContent = w; $("#insecure").hidden = false; } }
  if (!ME.profiles.length) { $("#noProfiles").hidden = false; return; }
  const sel = $("#profile");
  sel.replaceChildren(...ME.profiles.map(p => el("option", {value: p.id, text: p.name + (p.role === "viewer" ? " (view only)" : "")})));
  let want; try { want = +(new URLSearchParams(location.search).get("profile") || localStorage.getItem("ht-profile")); } catch (e) {}
  if (ME.profiles.some(p => p.id === want)) sel.value = want;
  sel.closest("label").hidden = ME.profiles.length < 2;
  const pick = () => { PID = +sel.value; try { localStorage.setItem("ht-profile", sel.value); } catch (e) {} loadLabNames().then(() => load()); };
  sel.onchange = pick;
  $("#intro").hidden = false;
  pick();
})();
