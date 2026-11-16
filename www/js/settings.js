/* Sanguity · Settings page (#35): a profile's settings, in the sections the server's registry (server/settings.py)
   gives, each with a simple editor for its kind and Save, and "Reset to defaults" per section. Viewers see the
   profile's settings without editors; their own (per-user) settings they can change. A new kind of setting adds
   an editor to EDITORS. */
"use strict";   // $, el and api come from util.js
let ME, PID = null, LAB_NAMES = [];

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

/* rows of inputs (a table on wide screens, stacked on phones); cols: [{key, label, type, ...attrs}] */
function rowsEditor(rows, cols, changed, ro, blank) {
  const body = el("div", {class: "set-rows c" + cols.length});
  const head = el("div", {class: "set-row set-head", "aria-hidden": "true"}, ...cols.map(c => el("span", {text: c.label})), el("span"));
  const make = r => {
    const row = el("div", {class: "set-row"}, ...cols.map(c => el("label", {},
      el("span", {class: "set-lbl", text: c.label}),
      el("input", {"data-k": c.key, type: c.type || "text", value: r[c.key] ?? "", disabled: ro, oninput: changed,
        ...(c.attrs || {})}))));
    row.append(ro ? el("span") : small("Remove", "Remove this row", () => { row.remove(); changed(); }));
    return row;
  };
  body.append(head, ...rows.map(make));
  const box = el("div", {}, body);
  if (!ro) box.append(el("div", {class: "set-add"}, el("button", {class: "btn small", type: "button", text: "Add a row",
    onclick: () => { const r = make(blank); body.append(r); r.querySelector("input").focus(); changed(); }})));
  const read = () => [...body.querySelectorAll(".set-row:not(.set-head)")].map(row =>
    Object.fromEntries(cols.map(c => { const v = row.querySelector(`[data-k="${c.key}"]`).value; return [c.key, c.type === "number" ? (v === "" ? null : +v) : v.trim()]; })));
  return {node: box, read};
}

function namesEditor(value, changed, ro, setting) {
  const isLab = setting.key === "labNicknames";
  const cols = [{key: "from", label: isLab ? "Name in the records" : "Tag in the records", attrs: {maxlength: 120, ...(isLab ? {list: "labNames"} : {})}},
                {key: "to", label: "Shown as", attrs: {maxlength: 120}}];
  const r = rowsEditor(Object.entries(value).map(([from, to]) => ({from, to})), cols, changed, ro, {from: "", to: ""});
  return {node: r.node, get: () => Object.fromEntries(r.read().filter(x => x.from || x.to).map(x => [x.from, x.to]))};
}

function rulesEditor(value, changed, ro) {
  const cols = [{key: "match", label: "Visit title matches", attrs: {maxlength: 200, placeholder: "^physical therapy\\b"}},
                {key: "name", label: "Count as", attrs: {maxlength: 80, placeholder: "Physical therapy"}},
                {key: "minutes", label: "Minutes", type: "number", attrs: {min: 1, max: 600, step: 1}}];
  const r = rowsEditor(value, cols, changed, ro, {match: "", name: "", minutes: 30});
  return {node: r.node, get: () => r.read().filter(x => x.match || x.name)};
}

/* any other value: its JSON, for a kind this page has no editor for yet */
function jsonEditor(value, changed, ro) {
  const ta = el("textarea", {class: "set-json", rows: 4, disabled: ro, oninput: changed, "aria-label": "Value (JSON)"});
  ta.value = JSON.stringify(value, null, 1);
  return {node: ta, get: () => { try { return JSON.parse(ta.value); } catch (e) { return ta.value; } }};
}
const EDITORS = {labs: labsEditor, names: namesEditor, rules: rulesEditor};

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

/* the profile's lab names, suggested when adding a lab or a nickname */
async function loadLabNames() {
  LAB_NAMES = [];
  try {
    const r = await api(`profiles/${PID}/data`);
    if (r.status === 200) LAB_NAMES = [...new Set(((await r.json()).labs || []).map(s => s.name))].sort();
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
  const pick = () => { PID = +sel.value; try { localStorage.setItem("ht-profile", sel.value); } catch (e) {} load(); loadLabNames(); };
  sel.onchange = pick;
  $("#intro").hidden = false;
  pick();
})();
