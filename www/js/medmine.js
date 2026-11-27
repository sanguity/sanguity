/* Sanguity · your own medications, and your changes to those from the records (#1092; server/medications.py, #1091).
   A medication of DATA.medications may carry "own" (an entry of your own: {id, key, its fields, by, at}) or "change"
   (your changes over one from the records: {id, the fields changed, by, at, changed_by}) with "listed" (what the
   records say: the fields, and their spans as they were). Owners and editors add one on the Medications tab (Add a
   medication), and in a medication's drawer Edit it, or put one from the records back as listed, with an Undo. Every
   change's reply carries the records whole, put in place (store.js storeRecords), the tab drawn again with them.
   Plain script, loaded in order by index.html after medlog.js; all files share one global scope. */
"use strict";
const MM_FIELDS = [["name", "Name"], ["strength", "Strength"], ["dose", "Dose and instructions"], ["route", "Route"],
  ["start", "Started"], ["stop", "Stopped"], ["status", "Status"], ["prescriber", "Prescribed by"], ["note", "Note"]];
const MM_MAX = {name: 200, strength: 100, dose: 500, route: 100, prescriber: 200, note: 2000};
const MM_STATUS = ["Active", "On hold", "Stopped"];
/* what the records say of one, as its fields (server/medications.listed): what Edit starts from */
function mmListed(m) {
  const s = m.spans.at(-1) || {}, x = m.listed || {name: m.name, dose: s.dose, start: m.spans[0]?.from || m.start, stop: m.stop, status: s.status, prescriber: s.prescriber};
  return Object.fromEntries(MM_FIELDS.map(([k]) => [k, x[k] || ""]));
}
/* its fields as they are now: your own entry's, or the records' with your changes over them */
const mmChanged = m => m.change ? MM_FIELDS.filter(([k]) => k in m.change) : [];
const mmFields = m => m.own ? Object.fromEntries(MM_FIELDS.map(([k]) => [k, m.own[k] || ""]))
  : {...mmListed(m), ...Object.fromEntries(mmChanged(m).map(([k]) => [k, m.change[k]]))};
const mmSays = (k, v) => !v ? "None" : k === "start" || k === "stop" ? fmtD(dayTs(v)) : v;
/* a change sent; its reply's records put in place -> the reply, or null (said in msg, or announced) */
async function mmSend(method, path, body, msg) {
  const say = (t, bad) => msg ? sayIn(msg, t, bad) : announce(t);
  if (msg) sayIn(msg, "Saving…");
  let r, j;
  try { r = await api(`profiles/${PROFILE.id}/medications/${path}`, {method, body: JSON.stringify(body)}); j = await r.json().catch(() => ({})); }
  catch (err) { say("Couldn't reach the server: " + err.message, true); return null; }
  if (!r.ok) { say(typeof j.detail === "string" ? j.detail : `Couldn't save it (${r.status}).`, true); return null; }
  if (j.records) storeRecords(j.records); else location.reload();
  return j;
}
const mmOpen = key => { const m = MEDS.find(x => x.key === key); if (m) openMedication(m); return m; };

/* the Medications tab's Add a medication, for owners and editors */
const medAddBtn = () => canEdit() ? el("button", {class: "btn small needs-server", type: "button", text: "Add a medication", onclick: () => medEditForm(null)}) : null;

/* in a medication's drawer (meds.js openMedication): your entry, or your change beside what the records list, and
   Edit and Put back as listed for owners and editors */
function medMineSection(m) {
  const out = [], changed = mmChanged(m);
  if (m.own) {
    out.push(el("div", {class: "sect", text: "Your own entry"}),
      drawerFacts([...MM_FIELDS.filter(([k]) => !["name", "start", "stop"].includes(k)).map(([k, l]) => [l, m.own[k] || ""]), ["Added by", m.own.by || ""]]));
  } else if (changed.length) {
    const was = mmListed(m);
    out.push(el("div", {class: "sect", text: "Your change"}),
      scrollBox({class: "tablewrap", "aria-label": "Your change and what the records list"}, el("table", {class: "data"},
        el("thead", {}, el("tr", {}, ["", "Your change", "As listed"].map(h => el("th", {text: h})))),
        el("tbody", {}, changed.map(([k, l]) => el("tr", {}, el("th", {scope: "row", text: l}), el("td", {text: mmSays(k, m.change[k])}), el("td", {text: mmSays(k, was[k])})))))),
      el("p", {class: "hint", text: `By ${m.change.changed_by || m.change.by}. The records stay as they are, and a later pull doesn't undo it.`}));
  }
  if (!canEdit()) return out;
  const btn = (text, onclick, more = {}) => el("button", {class: "btn needs-server", type: "button", text, onclick, ...more});
  out.push(el("div", {class: "btns"}, btn("Edit", () => medEditForm(m), {"aria-label": `Edit ${m.name}`}),
    changed.length ? btn("Put back as listed", () => mmPutBack(m), {"data-tip": "Undo your changes: as the records list it"}) : null));
  return out;
}
async function mmPutBack(m) {
  const was = mmFields(m);
  if (!await mmSend("PUT", "listed", {key: m.key, ...mmListed(m)})) return;
  medAgain(MEDS.find(x => x.key === m.key));
  toast(`${was.name}: as the records list it again.`, {action: "Undo", onAction: async () => {
    if (await mmSend("PUT", "listed", {key: m.key, ...was})) { mmOpen(m.key); announce("Your change is back."); }
  }});
}

/* the add / edit form, in the drawer. m: the medication (null: a new one of your own) */
function medEditForm(m) {
  const own = !m || !!m.own, f = m ? mmFields(m) : {start: zToday(), status: "Active"};
  const body = drawerHead("med", m ? `Edit ${m.name}` : "Add a medication", m ? (own ? "Your own entry" : "From the records: your changes are kept over them") : "Your own entry");
  if (m) drawerStill = () => MEDS.some(x => x.key === m.key);   // (app.js, #517)
  const uid = Math.random().toString(36).slice(2, 8), msg = el("p", {class: "hint edit-msg", role: "status"}), inp = {};
  const field = (k, label, input) => {
    input.id = `mm-${k}-${uid}`; input.value = f[k] || ""; inp[k] = input;
    return el("div", {class: "jfield"}, el("label", {for: input.id, text: label}), input);
  };
  const text = k => el("input", {type: "text", maxlength: MM_MAX[k], autocomplete: "off"});
  const statuses = [...new Set(["", ...MM_STATUS, f.status || ""])];
  const status = el("select", {}, ...statuses.map(s => el("option", {value: s, text: s || "Not given"})));
  const today = el("button", {class: "btn small", type: "button", text: "Stopped today", onclick: () => {
    inp.stop.value = zToday(); if (!/stop/i.test(status.value)) status.value = "Stopped"; sayIn(msg, `Stopped ${fmtD(dayTs(zToday()))}.`); }});
  const form = el("form", {class: "jform", novalidate: true},
    own ? null : el("p", {class: "hint", text: "The records stay as they are; what you change here is kept over them, and a later pull doesn't undo it. Put back as listed takes it away."}),
    field("name", "Name", Object.assign(text("name"), {required: true})), field("strength", "Strength (optional)", text("strength")),
    field("dose", "Dose and instructions (optional)", text("dose")), field("route", "Route (optional)", text("route")),
    el("div", {class: "jwhen"}, field("start", own ? "Started" : "Started (optional)", el("input", {type: "date", required: own})), field("stop", "Stopped (optional)", el("input", {type: "date"}))), el("div", {}, today),
    field("status", "Status", status), field("prescriber", "Prescribed by (optional)", text("prescriber")),
    field("note", "Note (optional)", el("textarea", {rows: 3, maxlength: MM_MAX.note})),
    el("div", {class: "btns"}, el("button", {class: "btn primary needs-server", type: "submit", text: m ? "Save" : "Add medication"}),
      el("button", {class: "btn", type: "button", text: "Cancel", onclick: () => m ? openMedication(MEDS.find(x => x.key === m.key) || m) : closeDrawer()})), msg);
  form.onsubmit = async ev => {
    ev.preventDefault();
    const out = Object.fromEntries(MM_FIELDS.map(([k]) => [k, inp[k].value.trim()]));
    const bad = (t, x) => { sayIn(msg, t, true); x.focus(); };
    if (!out.name) return bad("Give the medication's name.", inp.name);
    if (own && !out.start) return bad("Give the date it started.", inp.start);
    if (out.start && out.stop && out.stop < out.start) return bad("The stop date is before the start date.", inp.stop);
    const j = await mmSend(m ? "PUT" : "POST", !m ? "own" : own ? `own/${m.own.id}` : "listed", own ? out : {key: m.key, ...out}, msg);
    if (!j) return;
    mmOpen(j.key);
    announce(m ? "Saved." : `${out.name} added.`);
  };
  body.append(form);
  showDrawer();
  inp.name.focus({preventScroll: true});
}
