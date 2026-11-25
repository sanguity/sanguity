/* Sanguity · Settings page (#35): how things look. A profile's settings and the person's own (Display), in the
   sections the server's registry (server/settings.py) gives, each setting with a simple editor for its kind: a
   simple preference saved as it's changed, with an Undo; lists and values saved by the group's bar (#272), and a
   section's "Reset to defaults" when it isn't at them (#174). Viewers see the profile's settings without
   editors; their own (per-user) settings they can change, and someone with no profile yet still has those (#169).
   A new kind of setting adds an editor to EDITORS. About this person and Targets (#267) are about.js's, the profile's
   name and deleting it (#402) profile-card.js's. What used to be here and isn't about how things look has its own
   page now, its old addresses moved.js's. */
"use strict";   // $, el and api come from util.js
let ME, PID = null, LAB_NAMES = [], LABS = [], NICK = {}, HAS_CLAIMS = false;

async function call(path, method = "GET", body, opts = {}) {   // opts: fetch's own (keepalive, #419)
  const r = await api(path, {method, body: body === undefined ? undefined : JSON.stringify(body), ...opts});
  if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
  const j = r.status === 204 ? null : await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error((typeof j.detail === "string" && j.detail) || (Array.isArray(j.detail) ? "Please check the fields." : r.status + " " + r.statusText)), {status: r.status});
  return j;
}
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
$("#logout").onclick = logOut;   // util.js: asks first about what isn't saved (#391)

/* ---------------------------------------------------------------- editors: each builds its controls from a value
   and reads the value back (get), calling changed() on every edit */
const small = (text, label, onclick) => el("button", {class: "btn small", type: "button", "aria-label": label, onclick}, text);   // text: words or an icon
const beside = (text, label, onclick) => el("button", {class: "btn", type: "button", "aria-label": label, onclick}, text);   // a button in a row of fields: their height (#352)

/* a set of lab names: remove, add (suggesting the profile's own labs). By panel, as the Labs tab lists them (#441,
   labGroups in util.js): a list under each panel's name when there's more than one, each lab by the name it's shown by
   (a nickname, with its name in the records beside it, as Key labs shows it). Its order does nothing (Labs' charts
   follow the list, #441), so there's none to set (#451); a saved order whose panels are mixed is the same list. Within
   a panel the labs are in Labs' order, the most measured first (#458). Add takes the name a lab is shown by or its
   name in the records, suggests the names shown, and refuses one this profile has no lab by, as Key labs does. What
   it saves keeps the order saved, a lab added after them. */
function labsEditor(value, changed, ro) {
  const count = n => LABS.reduce((a, s) => a + (s.name === n ? s.pts.length : 0), 0);
  const tidy = l => labGroups([...l].sort((a, b) => count(b) - count(a) || nick(a).localeCompare(nick(b))), LABS).flatMap(g => g.labs);
  let list = tidy(value);
  const box = el("div", {class: "set-list"}), lists = el("div", {class: "set-groups"}), msg = el("span", {class: "msg", role: "status"});
  const input = el("input", {list: "labShown", placeholder: "Lab name", "aria-label": "Lab to add", maxlength: 120});
  const row = n => el("li", {"data-lab": n},
    el("span", {class: "set-name"}, nick(n), nick(n) !== n ? el("small", {class: "muted", text: ` (${n})`}) : null),
    ro ? null : el("span", {class: "set-acts"}, small("Remove", `Remove ${nick(n)}`, () => {
      const at = [...lists.querySelectorAll("li")].findIndex(li => li.dataset.lab === n);
      list = list.filter(x => x !== n); draw(); changed();
      (lists.querySelectorAll("li button")[at] || lists.querySelectorAll("li button")[at - 1] || input).focus();   // the next one's Remove, else the last's
    })));
  function draw() {
    const gs = labGroups(list, LABS);
    lists.replaceChildren(...(list.length ? gs.flatMap(g => present(gs.length > 1 ? el("h4", {class: "lab-panel set-grp", text: g.panel}) : null,
      el("ul", {class: "set-labs"}, ...g.labs.map(row)))) : [el("ul", {class: "set-labs"}, el("li", {class: "muted", text: "None"}))]));
  }
  const add = () => {
    const typed = input.value.trim().replace(/\s+/g, " "), n = labNamed(typed);
    if (!typed) return;
    if (LABS.length && !n) { sayIn(msg, `This profile has no lab called “${typed}”. Pick one from the list.`, true); return; }
    if (!list.includes(n)) { list = tidy([...list, n]); draw(); changed(); }
    input.value = ""; sayIn(msg, ""); input.focus();
  };
  input.addEventListener("keydown", ev => { if (ev.key === "Enter") { ev.preventDefault(); add(); } });
  draw();
  box.append(lists);
  if (!ro) box.append(el("div", {class: "set-add"}, input, el("button", {class: "btn", type: "button", text: "Add", onclick: add})), msg);
  return {node: box, get: () => { const l = [...value.filter(n => list.includes(n)), ...list.filter(n => !value.includes(n))];   // (the order saved: it does nothing)
    return same(l, value) ? value : l; }};
}

/* rows of inputs (a table on wide screens, stacked on phones); cols: [{key, label, type, only, ...attrs}]. A
   checkbox column with only: true is ticked in one row at most (ticking it in one row unticks the others); a radio
   column is one choice across the rows (#259: a milestone's Primary), its radios one group. With none, empty says what
   would be listed (emptyState(), #494) in place of the columns' names, and Add a row is the next step. */
let rowsEditors = 0;
function rowsEditor(rows, cols, changed, ro, blank, cls = "", empty = "Nothing here yet.") {
  const body = el("div", {class: "set-lines c" + cols.length + (cls ? " " + cls : "")}), group = "rows" + (++rowsEditors) + "-";
  let made = 0;
  const head = el("div", {class: "set-line set-head", "aria-hidden": "true"}, ...cols.map(c => el("span", {text: c.label})), el("span"));
  const none = emptyState(empty, null, {cls: "set-none"});
  const tidy = () => { const n = !body.querySelector(".set-line:not(.set-head)"); head.hidden = n; none.hidden = !n; };
  const tick = c => c.type === "checkbox" || c.type === "radio";
  const only = (c, input) => { if (c.only && input.checked) for (const x of body.querySelectorAll(`[data-k="${c.key}"]`)) if (x !== input) x.checked = false; };
  const make = r => {
    const id = group + (++made) + "-";   // c.of: the column that names the row, said with a radio's label ("Primary Diagnosis", #353)
    const row = el("div", {class: "set-line"}, ...cols.map(c => el("label", {class: tick(c) ? "set-tick" : null},
      el("span", {class: "set-lbl", id: id + c.key + "-l", text: c.label}),
      el("input", {"data-k": c.key, id: id + c.key, "aria-labelledby": c.of ? `${id}${c.key}-l ${id}${c.of}` : null, type: c.type || "text", name: c.type === "radio" ? group + c.key : null, value: tick(c) ? null : r[c.key] ?? "", checked: tick(c) ? !!r[c.key] : null,
        disabled: ro, oninput: ev => { if (tick(c)) only(c, ev.target); changed(); }, ...(c.attrs || {})}))));
    row.append(ro ? el("span") : beside("Remove", "Remove this row", () => {
      const next = row.nextElementSibling?.querySelector("input") || row.previousElementSibling?.querySelector("input") || addBtn;
      row.remove(); tidy(); next?.focus(); changed(); }));
    return row;
  };
  body.append(head, ...rows.map(make));
  const addBtn = ro ? null : el("button", {class: "btn small", type: "button", text: "Add a row",
    onclick: () => { const r = make(blank); body.append(r); tidy(); r.querySelector("input").focus(); changed(); }});
  const box = el("div", {}, none, body);
  if (!ro) box.append(el("div", {class: "set-add"}, addBtn));
  tidy();
  const read = () => [...body.querySelectorAll(".set-line:not(.set-head)")].map(row =>
    Object.fromEntries(cols.map(c => { const i = row.querySelector(`[data-k="${c.key}"]`), v = i.value;
      return [c.key, tick(c) ? i.checked : c.type === "number" ? (v === "" ? null : +v) : v.trim()]; })));
  return {node: box, read};
}

function namesEditor(value, changed, ro, setting) {
  const isLab = setting.key === "labNicknames";
  const cols = [{key: "from", label: isLab ? "Name in the records" : "Tag in the records", attrs: {maxlength: 120, ...(isLab ? {list: "labNames"} : {})}},
                {key: "to", label: "Shown as", attrs: {maxlength: 120}}];
  const r = rowsEditor(Object.entries(value).map(([from, to]) => ({from, to})), cols, changed, ro, {from: "", to: ""}, "",
    isLab ? "No lab names changed yet: each shows as the records name it." : "No result sources named yet: each shows as its tag in the records.");
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
  const r = rowsEditor(value, cols, changed, ro, {match: "", name: "", minutes: 45, regex: false}, "rules", "No visits count as workouts yet.");
  return {node: r.node, get: () => r.read().filter(x => x.match || x.name)
    .map(x => ({match: x.match, regex: x.regex, name: x.name, minutes: x.minutes}))};   // the server's order, for same()
}

/* ---------------------------------------------------------------- #39 milestones: a date and a name each, one of
   them primary (or none: then the first counts). The server keeps them in date order. */
function milestonesEditor(value, changed, ro) {
  const cols = [{key: "date", label: "Date", type: "date", attrs: {min: "1900-01-01", max: "2100-12-31", required: true}},
                {key: "label", label: "Name", attrs: {maxlength: 60, placeholder: "Diagnosis"}},
                {key: "primary", label: "Primary", type: "radio", of: "label"}];
  const r = rowsEditor(value, cols, changed, ro, {date: "", label: "", primary: false}, "ms", "No milestones yet: each one is marked on every chart.");
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

/* (a row's ⋯ menu, rowMenu(), is util.js's: Admin's accounts have one too, #287) */

/* a list put in order three ways: drag an item by its handle (mouse, pen or touch), focus the handle and press
   up or down (Home, End), or its ↑ and ↓ buttons. items are strings; opts.name(item) names it, opts.body(item)
   gives what its row shows, opts.extra(item, i) more buttons; opts.changed() after every change. With opts.more
   (item, i), more entries [{text, run}] for a menu of the row's actions that takes the buttons' place on a phone
   (#174), so each row stays one line. With opts.group(items) -> [{panel, labs}], the items are kept in those groups
   (the key labs by panel, #279), a list for each under its name when there's more than one, and move within theirs. */
function orderList(items, opts) {
  const tidy = l => opts.group ? opts.group(l).flatMap(g => g.labs) : [...l];
  let list = tidy(items), span = [];   // span[i]: [first, last], the rows item i moves among (its group's)
  const cls = "set-labs set-order" + (opts.more && !opts.ro ? " has-more" : "");
  const ol = el(opts.group ? "div" : "ol", {class: opts.group ? "set-groups" : cls});
  const said = el("span", {class: "set-sr", "aria-live": "polite"});
  const refocus = (i, act) => ol.querySelector(`li[data-i="${i}"] [data-act="${act}"]`)?.focus();
  const tell = i => { const [a, b] = span[i]; said.textContent = `${opts.name(list[i])}: ${i - a + 1} of ${b - a + 1}.`; };
  const move = (from, to, act) => {
    const [a, b] = span[from]; to = Math.max(a, Math.min(b, to));
    if (to === from) return;
    const [x] = list.splice(from, 1); list.splice(to, 0, x);
    draw(); opts.changed(); refocus(to, act); tell(to);
  };
  function drag(ev, li) {
    if (ev.button !== 0) return;
    ev.preventDefault();
    // the row's list takes the pointer, not the handle: moving the row in the page would drop the handle's capture
    const grip = li.parentElement, [a, b] = span[+li.dataset.i];
    try { grip.setPointerCapture(ev.pointerId); } catch (e) {}
    li.classList.add("dragging");
    const onMove = e => {
      const before = [...grip.children].find(c => { if (c === li) return false; const r = c.getBoundingClientRect(); return e.clientY < r.top + r.height / 2; });
      if (before) { if (li.nextElementSibling !== before) grip.insertBefore(li, before); }
      else if (grip.lastElementChild !== li) grip.append(li);
    };
    const onUp = () => {
      for (const [k, f] of [["pointermove", onMove], ["pointerup", onUp], ["pointercancel", onUp]]) grip.removeEventListener(k, f);
      const to = a + [...grip.children].indexOf(li), order = [...list.slice(0, a), ...[...grip.children].map(c => list[+c.dataset.i]), ...list.slice(b + 1)];
      const moved = !same(order, list);
      list = order; draw();
      if (moved) { opts.changed(); tell(to); }
      refocus(to, "grip");
    };
    grip.addEventListener("pointermove", onMove); grip.addEventListener("pointerup", onUp); grip.addEventListener("pointercancel", onUp);
  }
  function draw() {
    list = tidy(list);
    const groups = opts.group ? opts.group(list) : [{labs: list}];
    span = []; let at = 0;
    for (const g of groups) { const a = at; at += g.labs.length; for (let k = a; k < at; k++) span[k] = [a, at - 1]; }
    const rows = list.map((it, i) => {
      const n = opts.name(it), li = el("li", {"data-i": i}), [a, b] = span[i];
      const grip = opts.ro ? null : el("button", {class: "set-grip btn quiet small", type: "button", "data-act": "grip", title: "Drag to reorder",
        "aria-label": `Reorder ${n}, ${i - a + 1} of ${b - a + 1}: drag, or press up or down`,
        onkeydown: ev => { const to = {ArrowUp: i - 1, ArrowDown: i + 1, Home: a, End: b}[ev.key]; if (to !== undefined) { ev.preventDefault(); move(i, to, "grip"); } },
        onpointerdown: ev => drag(ev, li)}, icon("grip-vertical"));
      li.append(...[grip, ...opts.body(it, i)].filter(Boolean));
      if (!opts.ro) li.append(el("span", {class: "set-acts"},
        el("button", {class: "btn small", type: "button", "data-act": "up", "aria-label": `Move ${n} up`, disabled: i === a, onclick: () => move(i, i - 1, i - 1 > a ? "up" : "down")}, icon("arrow-up")),
        el("button", {class: "btn small", type: "button", "data-act": "down", "aria-label": `Move ${n} down`, disabled: i === b, onclick: () => move(i, i + 1, i + 1 < b ? "down" : "up")}, icon("arrow-down")),
        ...(opts.extra ? opts.extra(it, i) : [])));
      if (opts.more && !opts.ro) {
        li.append(rowMenu(`Move or remove ${n}`, [
          ...(i > a + 1 ? [{text: "Move to the top", run: () => move(i, a, "more")}] : []),
          ...(i > a ? [{text: "Move up", run: () => move(i, i - 1, "more")}] : []),
          ...(i < b ? [{text: "Move down", run: () => move(i, i + 1, "more")}] : []),
          ...(i < b - 1 ? [{text: "Move to the bottom", run: () => move(i, b, "more")}] : []),
          ...opts.more(it, i).map(e => ({text: e.text, run: () => {
            e.run();
            // the row may be gone: the focus goes to the row now in its place (or the last one)
            if (!ol.contains(document.activeElement)) refocus(Math.min(i, list.length - 1), "more");
          }})),
        ], "more"));
      }
      return li;
    });
    const none = list.length ? [] : [el("li", {class: "muted", text: "None"})];
    if (!opts.group) ol.replaceChildren(...rows, ...none);
    else ol.replaceChildren(...(none.length ? [el("ol", {class: cls}, ...none)] : groups.flatMap(g => {
      const a = list.indexOf(g.labs[0]);
      return present(groups.length > 1 ? el("h4", {class: "lab-panel set-grp", text: g.panel}) : null, el("ol", {class: cls}, ...rows.slice(a, a + g.labs.length)));
    })));
    if (opts.drawn) opts.drawn();
  }
  draw();
  return {node: el("div", {}, ol, said), get: () => list, set: v => { list = [...v]; draw(); }, redraw: draw};
}

const labInfo = n => LABS.find(s => s.name === n);
/* the profile's labs as the records page reads them (#845), for the key-lab tiles' preview to show what the Overview's
   tiles do: each result's range and flag by the one rule (ranges.js applyRangeOverrides, with the ranges and About this
   person as saved: a flag as its report's, an older import's "HH" High, a range from Settings where its report gave
   none), a lab's imports joined in one series a unit, in time, at one time a flagged result after an unflagged one
   (same.js joinSeries, #833). It read the latest of the lab's first series as sent, and only H and L: no ◆, no flag
   from a range, a copy's in another import's series. LABS stays as sent (the ranges' preview, unranged, reads it).
   labLatest(n): of n's series (one a unit, #438), the one whose latest result is the latest, as the Overview's tile */
let labsMemo = null;
function labsRead() {
  if (labsMemo?.[0] === LABS && labsMemo[1] === RANGE_CFG) return labsMemo[2];
  const by = new Map();
  for (const s of LABS) {
    if (s.watch) continue;
    const k = s.name + "\u001f" + (s.unit || ""), pts = s.pts.map(p => Object.assign([...p], {ts: zParse(p[0])}));
    if (by.has(k)) by.get(k).pts.push(...pts); else by.set(k, {...s, pts});
  }
  const out = [...by.values()];
  applyRangeOverrides(out, RANGE_CFG);
  for (const s of out) s.pts.sort((a, b) => a.ts - b.ts || !!a[4] - !!b[4]);
  labsMemo = [LABS, RANGE_CFG, out];
  return out;
}
const labLatest = n => labsRead().filter(s => s.name === n).reduce((a, s) => !a || s.pts.at(-1).ts > a.pts.at(-1).ts ? s : a, null);
const nick = n => NICK[n] || n;
/* the lab a name typed in means (#458): its name in the records or the one it's shown by, in any letter case; null
   when this profile has none by it, the name as typed when its labs aren't known here. #labShown suggests the names
   shown, each with its name in the records when that's another */
const labNamed = t => { const low = t.toLowerCase();
  return !LABS.length ? t : LAB_NAMES.find(n => n === t) || LAB_NAMES.find(n => nick(n) === t) || LAB_NAMES.find(n => n.toLowerCase() === low || nick(n).toLowerCase() === low) || null; };
const labShownList = () => $("#labShown").replaceChildren(...LAB_NAMES.map(n => el("option", {value: nick(n), label: nick(n) !== n ? n : null})));

/* #36 the Overview's key-lab tiles: the profile's own labs, picked, ordered, and previewed as the Overview shows them:
   by panel (#279, labGroups in util.js), in order within each. A saved order whose panels are mixed is the same
   order grouped, not a change to save. */
function tilesEditor(value, changed, ro, setting) {
  const msg = el("span", {class: "msg", role: "status"});
  const preview = el("div", {class: "tiles set-preview", role: "group", "aria-label": "Preview of the Overview's key lab tiles"});
  const sugg = el("div", {class: "set-sugg"});
  const groups = list => labGroups(list, LABS), grouped = list => groups(list).flatMap(g => g.labs);
  const isDefault = list => same(list, grouped(setting.default));
  const update = () => { drawPreview(); drawSuggestions(); changed(); };
  const ol = orderList(value, {ro, changed: update, name: nick, group: groups,
    body: n => { const s = labInfo(n); return [el("span", {class: "set-name"}, nick(n), nick(n) !== n ? el("small", {class: "muted", text: ` (${n})`}) : null),
      el("span", {class: "set-count muted", text: s ? `${s.pts.length} result${s.pts.length === 1 ? "" : "s"}` : "no results here"})]; },
    extra: n => [el("button", {class: "btn small", type: "button", text: "Remove", "aria-label": `Remove ${nick(n)}`,
      onclick: () => { ol.set(ol.get().filter(x => x !== n)); update(); input.focus(); }})],
    more: n => [{text: "Remove", run: () => { ol.set(ol.get().filter(x => x !== n)); update(); }}]});
  function drawPreview() {
    const names = keyLabTiles(ol.get(), LABS, isDefault(ol.get())), gs = groups(names);
    preview.replaceChildren(...gs.flatMap(g => [gs.length > 1 ? el("h5", {class: "lab-panel", text: g.panel}) : null, ...g.labs.map(n => {
      const s = labLatest(n), last = s.pts.at(-1), st = statusOf(last);   // its status as the Overview's tile says it (#845)
      return el("div", {class: "tile", "data-lab": n},
        el("span", {class: "lbl"}, el("span", {text: nick(n), "data-tip": nick(n) !== n ? n : null}), nick(n) !== n ? el("span", {class: "vh", text: ` (${n})`}) : null,   // #358
          ...(st && st.cls !== "ok" ? [el("span", {class: "st " + st.cls, "data-tip": st.title}, st.text), st.title ? el("span", {class: "vh", text: `, ${st.title.toLowerCase()}`}) : null] : [])),
        el("span", {class: "val"}, last[5] || String(last[1]), el("small", {text: s.unit || ""})));
    })]).filter(Boolean));
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
  const input = el("input", {list: "labShown", placeholder: "Lab name", "aria-label": "Lab to add as a tile", maxlength: 120});
  const add = () => {
    const typed = input.value.trim().replace(/\s+/g, " "), n = labNamed(typed);
    if (!typed) return;
    if (LABS.length && !n) { sayIn(msg, `This profile has no lab called “${typed}”. Pick one from the list.`, true); return; }
    if (!ol.get().includes(n)) { ol.set([...ol.get(), n]); update(); }
    input.value = ""; sayIn(msg, ""); input.focus();
  };
  input.addEventListener("keydown", ev => { if (ev.key === "Enter") { ev.preventDefault(); add(); } });
  drawPreview(); drawSuggestions();
  const box = el("div", {class: "set-list"}, ol.node);
  if (!ro) box.append(el("div", {class: "set-add"}, input, el("button", {class: "btn", type: "button", text: "Add", onclick: add})), sugg, msg);
  box.append(el("h4", {class: "set-ph", text: "Preview"}), preview);
  return {node: box, get: () => { const l = ol.get(); return same(l, grouped(setting.value)) ? setting.value : l; }};
}

/* #40 the main tabs: shown or hidden, in order; one at least stays shown */
const TAB_LABEL = {overview: "Overview", health: "Body and activity", medications: "Medications", timeline: "Timeline", labs: "Labs", costs: "Costs"};   // #172, #275
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
/* #45 this user's theme, shown as it's picked (#272: saved then, settings.js saveNow), as Dark mode or Light mode in
   your menu saves it too (#410). A choice of this browser's own (made there before a password was changed, or left
   from before #410) is said, with a way to drop it. Each control has an id, for focus to come back to once the
   section is drawn again (a toast's Undo). */
function themeEditor(value, changed, ro) {
  const note = el("p", {class: "hint", id: "themeOwn"});
  const opts = [["system", "Follow the system"], ["light", "Light"], ["dark", "Dark"]];
  const box = el("fieldset", {class: "set-radios"}, el("legend", {class: "set-sr", text: "Default theme"}),
    radioSeg(opts.map(([v, l]) => el("label", {}, el("input", {type: "radio", name: "theme", id: "theme-" + v, value: v, checked: v === value, disabled: ro,
      onchange: () => { note.remove(); changed(); }}), l))));   // (this browser's own choice gives way to it)
  let own = null; try { own = localStorage.getItem("ht-theme"); } catch (e) {}
  if (own === "light" || own === "dark")
    note.append(`This browser shows ${own}, a choice of its own, until you pick a theme here. `,
      el("button", {class: "btn small", type: "button", text: "Use my default on this browser", onclick: () => {
        try { localStorage.removeItem("ht-theme"); } catch (e) {} applyTheme(); note.textContent = "This browser now uses your default."; }}));
  return {node: el("div", {}, box, note.childNodes.length ? note : null), get: () => box.querySelector("input:checked")?.value || value};
}
/* #225 some of a few choices, each a checkbox (the Overview's home readings as tiles), in the server's order */
function choicesEditor(value, changed, ro, setting) {
  const boxes = Object.entries(setting.choices || {}).map(([id, label]) => el("label", {class: "check"},
    el("input", {type: "checkbox", id: `${setting.key}-${id}`, value: id, checked: value.includes(id) || null, disabled: ro, onchange: changed}), " " + label));
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
  const input = el("input", {class: "set-select set-zone", type: "text", id: "timeZone", list: id, "aria-label": "Time zone", "aria-describedby": msgId,
    autocomplete: "off", spellcheck: "false", disabled: ro, value: shown(cur)});
  const hint = el("span", {class: "hint", id: msgId});
  const use = beside("Use this device's", "Use this device's time zone", () => { input.value = shown(here); pick(true); input.focus(); });   // the field's height (#374); hidden now, focus on the field (#376)
  use.hidden = ro;
  const zoneOf = t => zones.find(z => z === t.trim().replace(/ /g, "_")) || zones.find(z => shown(z).toLowerCase() === t.trim().toLowerCase());
  const note = () => { const same = cur === here; use.hidden = ro || same;
    hint.textContent = input.getAttribute("aria-invalid") === "true" ? "Pick a time zone from the list, such as America/New York."
      : same ? "" : `This device is set to ${shown(here)}.`; };
  /* a zone on the list is the value once it's picked from the list or the field is left (#272: saved then), not as
     it's typed: "EST" on the way to "EST5EDT" isn't one */
  const pick = (final = false) => {
    const z = zoneOf(input.value);
    if (z) { input.removeAttribute("aria-invalid"); if (final) { input.value = shown(z); if (z !== cur) { cur = z; changed(); } } }
    else if (final) input.setAttribute("aria-invalid", "true");
    note();
  };
  input.oninput = ev => pick(ev.inputType === "insertReplacementText" || !ev.inputType);   // (picked from the list)
  input.onchange = () => pick(true);
  note();
  return {node: el("div", {class: "set-add"}, input, list, use, hint), get: () => cur};
}
function formatEditor(value, changed, ro) {
  const sel = el("select", {class: "set-select", id: "dateFormat", "aria-label": "Date format", disabled: ro, onchange: changed},
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
      el("input", {type: "radio", name: "units-" + part, id: `units-${part}-${v}`, value: v, checked: v === (value[part] || "reported"), disabled: ro, onchange: changed}), l)))));
  return {node: el("div", {class: "set-units"}, ...boxes),
    get: () => Object.fromEntries(parts.map(([part], i) => [part, boxes[i].querySelector("input:checked")?.value || value[part] || "reported"]))};
}
EDITORS.units = unitsEditor;

/* ---------------------------------------------------------------- #44 reference ranges from settings (ranges.js) */
/* the birth date and sex the rows' ages and sexes are matched against: About this person's (#194, here: #267), loaded
   with the sections; the preview of what the rows colour uses them */
let RANGE_CFG = {birthDate: "", sex: "", overrides: []};
function rangesUses() {
  const b = RANGE_CFG.birthDate, s = RANGE_CFG.sex;
  return el("p", {class: "hint", id: "rangesUses"}, "Ages and sexes are matched against ",
    el("a", {href: `settings.html?profile=${PID}#about`, text: "About this person"}), ": ",
    b ? `born ${fmtD(zParse(b))}` : "no birth date (rows with an age band aren't used)", "; ",
    s ? `sex used for reference ranges ${s}` : "no sex for reference ranges (only rows for anyone are used)", ".");
}
/* the profile's lab series with numeric results whose report gives no range or flag: [{name, unit, pts}] */
const unranged = () => LABS.filter(s => !s.watch).map(s => ({name: s.name, unit: s.unit || "",
  pts: s.pts.filter(p => typeof p[1] === "number" && p[2] == null && p[3] == null && !String(p[4] || "").trim())})).filter(s => s.pts.length);
function rangesEditor(value, changed, ro) {
  const SEX = [["any", "Anyone"], ["female", "Female"], ["male", "Male"]];
  const cols = [["lab", "Lab (name in the records)"], ["unit", "Unit"], ["sex", "Sex"], ["ageFrom", "Age from"], ["ageTo", "Age to"], ["low", "Low"], ["high", "High"]];
  const body = el("div", {class: "set-lines rng"});
  body.append(el("div", {class: "set-line set-head", "aria-hidden": "true"}, ...cols.map(([, l]) => el("span", {text: l})), el("span")));
  const num = (k, v, attrs) => el("input", {"data-k": k, type: "number", value: v ?? "", disabled: ro, oninput: update, step: "any", ...attrs});
  const make = r => {
    const field = (k, l, input) => el("label", {}, el("span", {class: "set-lbl", text: l}), input);
    const row = el("div", {class: "set-line"},
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
  const read = () => [...body.querySelectorAll(".set-line:not(.set-head)")].map(row => {
    const v = k => row.querySelector(`[data-k="${k}"]`).value, n = k => v(k) === "" ? null : +v(k);
    return {lab: v("lab").trim(), unit: v("unit").trim(), sex: v("sex"), ageFrom: n("ageFrom"), ageTo: n("ageTo"), low: n("low"), high: n("high")};
  }).filter(r => r.lab || r.unit || r.low != null || r.high != null);
  const add = r => { const row = make(r); body.append(row); row.querySelector("input").focus(); update(); };
  const sugg = el("div", {class: "set-sugg"}), preview = el("p", {class: "hint", id: "rangesPreview", role: "status"});
  function update() {
    const rows = read(), cfg = {...RANGE_CFG, overrides: rows}, without = unranged();
    const total = without.reduce((a, s) => a + s.pts.length, 0);
    let hit = 0, out = 0;
    for (const s of without) for (const p of s.pts) { const r = rangeRowFor(cfg, s.name, s.unit, String(p[0]).slice(0, 10)); if (r) { hit++; if (rangeFlag(p[1], r.low, r.high, rangeCmp(p[5]))) out++; } }   // the one flag rule (#752)
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

/* ---------------------------------------------------------------- the sections (#174, #272): each setting with its
   editor. A simple preference (a kind in INSTANT: the theme, the time zone and date format, units, choices ticked on
   or off) is saved as it's changed, a toast saying so with an Undo. A list or a value (key labs, names, ranges,
   milestones...) is kept as changed, marked "not saved", its section's data-dirty the number of them, until the
   group's bar (sidebar.js) saves every change shown, or Discard puts them back as saved; the group is marked in the
   sidebar, and leaving the page or picking another profile asks first (sidebar.js). Whose settings these are is said
   once per group, above them; a setting says it only where its section mixes them. Where a setting's value comes
   from ("your own", "set for this profile"), and its section's Reset to defaults, show only when it isn't the
   default. A section of one setting named as it is (Tabs, Units, Milestones) has one heading, the section's (#398). */
const FROM = {default: "default", install: "set for this server", profile: "set for this profile", user: "your own"};
const SCOPE_TAG = {install: "for this server", profile: "for this profile", user: "just for you"};
const INSTANT = new Set(["theme", "timezone", "dateformat", "units", "choices"]);
/* at its default: the server's, or for the time zone and date format (set from the browser at the first sign-in,
   dates.js) this browser's own */
const atDefault = st => same(st.value, st.default) || (st.kind === "timezone" && st.value === browserZone()) || (st.kind === "dateformat" && st.value === browserFormat());
const isOwn = st => st.from === "profile" || st.from === "user";   // kept here: Reset to defaults takes it off
const setBase = () => PID == null ? "settings/user" : `profiles/${PID}/settings`;

/* one setting, a setting row (#286): on the left its name, where its value comes from (unless it's the default) and
   its help; on the right its editor, and a line for what went wrong saving it. changed(item) after every edit in it; changed() when it's filled in otherwise (put). A simple
   preference on its way to the server isn't a change not saved (#407): counts() is what the bar, the sidebar's dot,
   "not saved" and the questions before leaving count; for one of those, only one held for the bar: refused by the
   server, or filled in by put(). solo: the section, when this is its only setting, with its name, which the section's
   heading says: no heading of its own, only its marks (#398), its help with the section's in the card's head (#494),
   and its control as wide as the row. */
const SET_ITEMS = {};   // key -> the setting's item drawn last: put(value) fills its editor in, unsaved (presets.js, #186)
const WIDE = new Set(["ranges"]);   // a table of many columns: under its label, the card's width (#286)
function settingNode(st, changed, mixed, solo) {
  const ro = !st.can_change;
  const msg = el("span", {class: "msg", role: "status"});
  const from = el("span", {class: "tag"});
  const unsaved = el("span", {class: "tag set-unsaved", text: "not saved"});
  const node = el("div", {class: "set set-row" + (WIDE.has(st.kind) ? " wide" : solo ? " wide set-solo" : ""), "data-key": st.key, "data-scope": st.scope});   // a setting row (#286)
  const tag = () => { from.textContent = FROM[st.from] || st.from; from.hidden = atDefault(st); };
  let ed = null;
  const item = {st, node, msg, instant: INSTANT.has(st.kind) && !ro, held: false,
    dirty: () => !ro && !!ed && !same(ed.get(), st.value),
    counts: () => item.dirty() && (!item.instant || item.held),
    value: () => ed.get(),
    saved(value) { st.value = value; st.from = st.scope; item.held = false; tag(); mark(); },
    refuse() { item.held = true; mark(); changed(); },
    back(value, was) { st.value = value; st.from = was; tag(); mark(); changed(); }};   // put back by an Undo (#359)
  const mark = () => node.classList.toggle("set-changed", item.counts());
  const make = v => (EDITORS[st.kind] || jsonEditor)(v, () => { item.held = false; sayIn(msg, ""); mark(); changed(item); }, ro, st);
  ed = make(st.value);
  item.put = (v, held = true) => { const old = ed.node; ed = make(v); old.replaceWith(ed.node); item.held = held; mark(); changed(); };   // #186: not saved, a simple one too
  SET_ITEMS[st.key] = item;
  tag();
  node.append(el("div", {class: "set-row-lbl"}, el(solo ? "div" : "h3", {class: "sub"}, solo ? null : st.label, from, mixed && SCOPE_TAG[st.scope] ? el("span", {class: "tag", text: SCOPE_TAG[st.scope]}) : null, unsaved),
    solo ? null : helpNodes(st.help)),   // (alone in its section: its help with the section's, in the card's head, #494)
    el("div", {class: "set-row-ctl"}, ed.node, msg));
  return item;
}

/* the section's settings as the server has them now: {key: {value, from}} */
async function stored(base) {
  const d = await call(`${base}/sections`);
  return Object.fromEntries(d.sections.flatMap(sec => sec.settings).map(st => [st.key, {value: st.value, from: st.from}]));
}
/* an Undo (#263, #359) of a save, a change saved as it was made, or a Reset: each setting it changed goes back to
   what it was (the default it came from taken off again, where a profile's setting can be; otherwise the value it
   had), but only one that still holds what was written: one changed again since is left, and said to be. Its
   sections (secs) are drawn again as saved, unless one has changes not saved: those are kept, its other settings
   shown as they are now. pid: the profile it was on (PID then): another picked since keeps its page as it is, and
   a toast says what was undone in the first (#367), unless the settings are the person's own (Appearance, Dates,
   Units: the same on every profile), drawn and said as without a switch (#377). */
async function undoSave(base, secs, was, pid) {
  const now = await stored(base), back = [], since = [];
  for (const w of was) {
    if (!w.wrote || !now[w.key] || !same(now[w.key].value, w.wrote.value)) { since.push(w.label); continue; }
    const path = `${base}/${encodeURIComponent(w.key)}`;
    if ((w.from === "default" || w.from === "install") && !base.startsWith("settings/")) await call(path, "DELETE");
    else await putSet(path, w.value, now[w.key].value);
    back.push(w);
  }
  const text = [back.length ? `Undone: ${quoted(back.map(w => w.label))} ${back.length === 1 ? "is" : "are"} as before.` : "",
    since.length ? `${quoted(since)} ${since.length === 1 ? "was" : "were"} changed since, so ${since.length === 1 ? "it's" : "they're"} left as ${since.length === 1 ? "it is" : "they are"}.` : ""].join(" ").trim();
  if (PID !== pid && !secs.every(sec => sec.settings.every(st => st.scope === "user"))) { toast(`${ME.profiles.find(x => x.id === pid)?.name || "The other profile"}: ${text}`); return; }
  const card = id => document.getElementById("sec-" + id), ids = secs.map(sec => sec.id);
  const kept = ids.filter(id => card(id)?.hasAttribute("data-dirty"));
  if (!kept.length) { await load({sections: ids, text}, ids); return; }
  // changes not saved in one: kept, each setting's saved value now the one put back (so one changed back to it is
  // no longer a change); a setting not changed there shows it
  const after = await stored(base).catch(() => now);
  for (const w of back) {
    const it = SET_ITEMS[w.key];
    if (!it || !kept.some(id => card(id).contains(it.node)) || !after[w.key]) continue;
    const dirty = it.dirty();
    it.back(after[w.key].value, after[w.key].from);
    if (!dirty) it.put(after[w.key].value);
  }
  await load();   // the other sections, as they are now: these, still with changes, stay
  const clean = ids.filter(id => !card(id)?.hasAttribute("data-dirty"));
  for (const id of ids) if (!clean.includes(id)) sayIn(card(id)?.querySelector(".set-msg"), text + " Your changes not saved yet are kept.");
  if (clean.length) await load({sections: clean, text}, clean);
}

/* a setting saved, as call() does: a whole value (a list, a map) by util.js saveWhole, with the one it was made from
   (read), so one changed since in another tab or by someone else is refused (409, #923) rather than lost under it, and
   this page's own saves of it go on from each other (#956) */
async function putSet(path, value, read) {
  if (read === null || typeof read !== "object") return call(path, "PUT", {value});
  const r = await saveWhole(path, value, read);
  if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error((typeof j.detail === "string" && j.detail) || (Array.isArray(j.detail) ? "Please check the fields." : r.status + " " + r.statusText)), {status: r.status});
  return j;
}
/* “A”, “A” and “B”, “A”, “B” and “C” */
const quoted = labels => labels.map((l, i) => (i === 0 ? "" : i === labels.length - 1 ? " and " : ", ") + `“${l}”`).join("");

/* a section: its title, Reset to defaults (when something in it isn't the default), a line for what's said about it,
   and its settings. opts.note is said in that line once drawn (shown only, with opts.quiet: a toast says it). The
   card keeps its items and section (sgItems, sgSec), and sgChanged() and sgTidy() for after a save. */
function sectionCard(sec, opts = {}) {
  const msg = el("span", {class: "msg set-msg"}); sayIn(msg, opts.note || "", false, {say: !opts.quiet});   // said too (util.js, #209), or by a toast
  const mixed = new Set(sec.settings.map(st => st.scope)).size > 1;
  let card = null;
  const changed = it => {
    const n = items.filter(x => x.counts()).length;
    if (card) { if (n) card.setAttribute("data-dirty", n); else card.removeAttribute("data-dirty"); }
    if (n && !msg.classList.contains("bad")) sayIn(msg, "");
    if (card && it && it.instant && it.dirty()) saveNow(it, card);
  };
  const solo = sec.settings.length === 1 && sec.settings[0].label === sec.title ? sec : null;
  const items = sec.settings.map(st => settingNode(st, changed, mixed, solo));
  const reset = el("button", {class: "btn small", type: "button", text: "Reset to defaults", onclick: () => resetSection(card)});
  const tidy = () => { reset.hidden = !items.some(it => it.st.can_change && isOwn(it.st) && !atDefault(it.st)); };
  tidy();
  card = el("section", {class: "card", id: "sec-" + sec.id},
    el("div", {class: "set-hd"}, el("h2", {id: `sec-${sec.id}-hd`, tabindex: -1, text: sec.title}), reset),
    helpNodes(sec.help, ...(solo ? solo.settings[0].help.split("\n") : [])),   // the card's head: one line of hint, the rest behind its ⓘ (#494)
    sec.id === "overview" ? presetsBlock(sec) : null,   // "Start from a condition" (presets.js, #186)
    ...items.map(it => it.node), msg);   // (at its foot: words changing there move nothing in it)
  return Object.assign(card, {sgItems: items, sgSec: sec, sgChanged: () => changed(), sgTidy: tidy});
}

/* a simple preference, saved as it's changed (#272): the latest value, one save at a time per setting, a toast
   saying it with an Undo (as a save's). The theme shows at once, this browser's own choice (your menu's) giving way
   to it. On its way it isn't counted as a change not saved (#407). Not saved (the server said no): it stays changed,
   its line says why, and the bar offers to save it. Its section drawn again meanwhile (the bar's Discard of other
   changes there): the setting drawn now is the one marked saved or refused. Each save goes with keepalive, and one
   still waiting behind another's answer as the page is left is sent then (saveLeft, #419). Another profile picked
   meanwhile (#425): it's still sent, to the profile it was made on (path), which for the person's own (Units) is the
   same for every profile; the other profile is drawn once it's answered (pick, below), and one of the first
   profile's own is said with its name. SAVING: the saves on their way. Once the page is being left (SET_LEAVING,
   #457), one failing does nothing here: its request was sent with keepalive and still reaches the server, though its
   answer doesn't come back to the page, which saw it as refused, put the theme back and let the next one go out
   again after saveLeft's; and no save queued behind it is sent, saveLeft having sent the latest. */
const SAVING = new Set();
let SET_LEAVING = false;
function saveNow(it, card) {
  const pid = PID, base = setBase(), sec = card.sgSec, path = `${base}/${encodeURIComponent(it.st.key)}`;
  let mine = null;   // this browser's own theme, put back by the Undo
  if (it.st.kind === "theme") { try { mine = localStorage.getItem("ht-theme"); localStorage.removeItem("ht-theme"); } catch (e) {} applyTheme(it.value()); }
  it.path = path;
  const q = it.queue = (it.queue || Promise.resolve()).then(async () => {
    if (SET_LEAVING || !it.dirty()) return;
    const value = it.value(), was = [{key: it.st.key, label: it.st.label, value: it.st.value, from: it.st.from, wrote: {value}}];
    let now = it;
    const live = () => { const x = SET_ITEMS[it.st.key]; now = x && x !== it && x.node.isConnected ? x : it; card = now.node.closest(".card") || card; };
    it.sending = value;
    try { await call(path, "PUT", {value}, {keepalive: true}); }
    catch (e) {
      // (a task later: the request torn down as the page goes fails before pagehide has been heard by every listener)
      await new Promise(r => setTimeout(r));
      if (SET_LEAVING) return;
      live(); sayIn(now.msg, `Not saved: ${e.message}`, true); now.refuse(); if (it.st.kind === "theme") applyTheme(it.st.value); return; }
    finally { it.sending = undefined; }
    live(); now.saved(value); card.sgChanged(); card.sgTidy();
    if (it.st.kind === "timezone" || it.st.kind === "dateformat") useDates(SET_ITEMS["dates.timeZone"]?.st.value, SET_ITEMS["dates.format"]?.st.value);
    if (it.st.kind === "units") aboutUnits(value);   // About this person and Targets in them (about.js, #408)
    const other = PID !== pid && it.st.scope !== "user" ? ME.profiles.find(x => x.id === pid)?.name : "";
    const said = (other ? other + ": " : "") + `Saved “${it.st.label}”.`;
    sayIn(card.querySelector(".set-msg"), said, false, {say: false});
    const kept = await stored(base).catch(() => null);
    if (kept && kept[it.st.key]) was[0].wrote = kept[it.st.key];
    toast(said, {action: "Undo", onAction: async () => {
      await undoSave(base, [sec], was, pid);
      if (mine) { try { localStorage.setItem("ht-theme", mine); } catch (e) {} applyTheme(); }
    }});
  }).catch(() => {});
  SAVING.add(q);
  q.finally(() => SAVING.delete(q));
}

/* the page left while a simple preference waits behind the answer to its last save (#419: Units is one setting for
   its four kinds, so lb then ft/in is two saves, the second sent once the first is answered): its latest value is
   sent then, with keepalive, so it isn't lost. One the server refused (held) is a change not saved, asked about
   before leaving (sidebar.js); one on its way already with this value isn't sent again. */
function saveLeft() {
  SET_LEAVING = true;
  for (const it of Object.values(SET_ITEMS)) {
    if (!it.instant || !it.path || it.held || !it.dirty() || (it.sending !== undefined && same(it.sending, it.value()))) continue;
    api(it.path, {method: "PUT", body: JSON.stringify({value: it.value()}), keepalive: true}).catch(() => {});
  }
}
addEventListener("pagehide", saveLeft);
addEventListener("pageshow", e => { if (e.persisted) SET_LEAVING = false; });   // back from the back/forward cache

/* the bar's Save (sidebar.js): every change in these sections, in turn; the first the server refuses stops it, said
   there and in the bar (msg), the ones before it saved. Done: a toast says what, with an Undo, and focus, if it was
   on the bar, goes to the first section's heading (#368), without scrolling the page up to it: what was saved stays
   in view (#376). Gives back whether all were saved. */
async function saveSections(cards, msg, had) {
  const base = setBase(), pid = PID, done = [], was = [], secs = [];
  for (const card of cards) for (const it of card.sgItems.filter(x => x.counts())) {
    const value = it.value(), before = {key: it.st.key, label: it.st.label, value: it.st.value, from: it.st.from};
    try { await putSet(`${base}/${encodeURIComponent(it.st.key)}`, value, it.st.value); }
    catch (e) {
      if (e.status === 409) {   // (#923) changed since: its section read again, as it is now, to make the change on
        sayIn(msg, (done.length ? `Saved ${quoted(done)}. ` : "") + e.message, true);
        await load({sections: [card.sgSec.id], text: e.message}, [card.sgSec.id]);
        return false;
      }
      // the ones before it are saved; this one and the rest stay as changed, to put right and save again
      sayIn(it.msg, e.message, true, {say: false});
      for (const c of cards) c.sgChanged();
      sayIn(msg, (done.length ? `Saved ${quoted(done)}. ` : "") + `${quoted([it.st.label])} isn't saved: ${e.message}`, true);
      it.node.scrollIntoView({block: "nearest"});
      if (done.length) load();
      return false;
    }
    it.saved(value);
    done.push(it.st.label); was.push({...before, wrote: {value}});
    if (!secs.includes(card.sgSec)) secs.push(card.sgSec);
  }
  for (const c of cards) c.sgChanged();
  if (!done.length) return true;
  const said = `Saved ${quoted(done)}.`, ids = secs.map(sec => sec.id);
  sayIn(msg, "");
  await load({sections: ids, text: said, quiet: true});
  if (had) document.getElementById(`sec-${ids[0]}-hd`)?.focus({preventScroll: true});
  // what the server keeps now (as it stored it), for the Undo to tell whether a setting was changed again since
  const kept = await stored(base).catch(() => null);
  if (kept) for (const w of was) if (kept[w.key]) w.wrote = kept[w.key];
  toast(said, {action: "Undo", onAction: () => undoSave(base, secs, was, pid)});
  return true;
}
/* the bar's Discard: these sections drawn again as saved, focus on the first one's heading; a simple preference on
   its way to the server (#407) isn't a change to discard: it stays as picked */
function discardSections(cards) {
  let first = null;
  for (const c of cards) {
    const fresh = sectionCard(c.sgSec, {note: first ? "" : "Changes discarded: back as saved."});
    for (const it of c.sgItems) if (it.dirty() && !it.counts()) { const x = fresh.sgItems.find(y => y.st === it.st); if (x) { x.put(it.value(), false); x.queue = it.queue; x.path = it.path; } }
    c.replaceWith(fresh);
    first ||= fresh;
  }
  first?.querySelector("h2").focus();
}
/* Reset to defaults: at once, with an Undo that puts back what each setting was (#272); asked first only when the
   section has changes not saved, which it loses */
async function resetSection(card) {
  const sec = card.sgSec, base = setBase(), pid = PID, msg = card.querySelector(".set-msg");
  if (card.hasAttribute("data-dirty") && !await ask({title: `Reset the ${sec.title} settings?`, action: "Reset to defaults", danger: true,
      message: "Each of them goes back to its default, and your changes to them not saved yet are lost."})) return;
  const had = card.contains(document.activeElement) || document.activeElement?.id === "setReset";   // (or its Reset beside the h1, sidebar.js)
  const was = card.sgItems.filter(it => it.st.can_change && isOwn(it.st)).map(it => ({key: it.st.key, label: it.st.label, value: it.st.value, from: it.st.from}));
  try { await call(`${base}/reset`, "POST", {section: sec.id}); }
  catch (e) { sayIn(msg, e.message, true); return; }
  const said = `${sec.title} settings are back to their defaults.`;
  await load({sections: [sec.id], text: said, quiet: true}, [sec.id]);
  if (had) document.getElementById(`sec-${sec.id}-hd`)?.focus({preventScroll: true});
  const kept = await stored(base).catch(() => ({}));
  for (const w of was) w.wrote = kept[w.key];
  toast(said, {action: "Undo", onAction: () => undoSave(base, [sec], was, pid)});
}
/* the bar's Save and Discard (settings-nav.js): the parts shown with changes, sections and About this person's */
const aboutPart = p => p.id === "about" || p.id === "targets";
async function saveParts(parts, msg, had) {
  const cards = parts.filter(p => p.sgItems), about = parts.filter(aboutPart);
  if (cards.length && !await saveSections(cards, msg, had)) return;
  if (about.length) await aboutSave(about, msg, had && !cards.length);
}
function discardParts(parts) {
  discardSections(parts.filter(p => p.sgItems));
  const about = parts.filter(aboutPart);
  if (about.length) aboutDiscard(about);
}

/* the profile's sections (or, with no profile, the person's own), drawn again: one with changes not saved stays as
   it is, unless it's one forced (force: section ids) or all are drawn afresh (another profile); note: {sections,
   text, quiet} to say in those sections */
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
  labShownList();   // the names the lists show (#458)
  { const ov = every.find(x => x.key === "ranges.overrides"); RANGE_CFG = {...rangeConfig(merged), ...(ov ? {overrides: ov.value} : {})}; }   // #44
  { const th = every.find(st => st.key === "theme"); if (th) applyTheme(th.value); }   // #45: the saved default, here too
  { const u = every.find(st => st.kind === "units"); if (u) aboutUnits(u.value); }   // and the Units (an Undo's too): About this person's (#408)
  { const tb = every.find(st => st.key === "tabs"); if (tb) SgNav.layout(tb.value); }   // the top bar's tabs as saved, Undo and Reset too (#398)
  document.querySelector("main.sets").dataset.role = d.role || "";   // whose settings each group's are (settings-nav.js)
  const forced = [].concat(force || []);
  const cards = d.sections.map(sec => {
    const old = document.getElementById("sec-" + sec.id);
    if (!all && old && old.parentNode === box && (old.hasAttribute("data-dirty") || old.sgItems?.some(x => x.dirty())) && !forced.includes(sec.id)) return old;   // (one on its way, #407)
    return sectionCard(sec, {note: note && (note.sections || []).includes(sec.id) ? note.text : "", quiet: !!(note && note.quiet)});
  });
  const inPlace = !all && box.children.length === cards.length && d.sections.every((sec, i) => box.children[i].id === "sec-" + sec.id);
  if (inPlace) cards.forEach((c, i) => { if (box.children[i] !== c) box.children[i].replaceWith(c); });
  else box.replaceChildren(...cards);
  // the visit summary's measures are ticked on Sharing → Visit summary (#402, summary.js): their section stays here out
  // of sight, for Start from a condition to fill in (presets.js) and the group's bar to save
  const ticks = document.getElementById("sec-summary");
  if (ticks) ticks.hidden = true;
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
  if (sgMoved()) return;   // what was here and isn't about how things look (moved.js)
  try { ME = await call("me"); } catch (e) { return; }
  await datesReady();   // dates in this person's time zone and format (dates.js)
  if (ME.user.must_change_pw) { location.replace("account.html#password"); return; }
  if (ME.user.needs_two_factor) { location.replace("account.html#twofa"); return; }
  $("#who").textContent = ME.user.username;
  document.addEventListener("theme:changed", () => load());   // your menu's Dark or Light mode saved the theme (nav.js, #410)
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
  // About this person (about.js, #267) once the sections are there: its group arrives with theirs, not before
  // and its name and deleting it (profile-card.js, #402), with them too
  // (once the saves on their way are answered, #425: what's drawn is what the server keeps)
  const pick = () => { PID = +sel.value; const p = ME.profiles.find(x => x.id === PID); Promise.all([loadLabNames(), ...SAVING]).then(() => load(null, null, true)).then(() => { if (PID === p.id) { drawAbout(p); drawProfile(p); } }); };
  document.addEventListener("about:changed", () => load());   // the ranges' preview matches ages and sexes against it
  sel.onchange = pick;
  $("#intro").hidden = false;
  pick();
})();
