/* Sanguity · your own medications, and your changes to those from the records (#1092, #1093; server/medications.py,
   #1091). A medication of DATA.medications may carry "own" (an entry of your own: {id, key, its fields, by, at}) or
   "change" (your changes over one from the records: {id, the fields changed, by, at, changed_by}) with "listed" (what
   the records say: the fields, and their spans as they were). DATA.hiddenMeds lists those of the records hidden by
   hand. Owners and editors add one on the Medications tab (Add a medication), and in a medication's drawer Edit it,
   put one from the records back as listed, or Remove it: one of their own is deleted, one from the records hidden (its
   records kept, a later pull doesn't bring it back), each with an Undo; Hidden on the tab shows each one again. Every
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
/* a change sent; its reply's records put in place -> the reply, or null (said in msg, or announced). base: the API's
   part (family history's too, family.js) */
async function mmSend(method, path, body, msg, base = "medications") {
  const say = (t, bad) => msg ? sayIn(msg, t, bad) : announce(t);
  if (msg) sayIn(msg, "Saving…");
  let r, j;
  try { r = await api(`profiles/${PROFILE.id}/${base}/${path}`, {method, body: JSON.stringify(body)}); j = await r.json().catch(() => ({})); }
  catch (err) { say("Couldn't reach the server: " + err.message, true); return null; }
  if (!r.ok) { say(typeof j.detail === "string" ? j.detail : `Couldn't save it (${r.status}).`, true); return null; }
  if (!j.records) location.reload();
  else if (!DATA) await recordsFirst(j.records);   // a profile with nothing imported: its first records, its tabs drawn (#1091)
  else storeRecords(j.records);
  return j;
}
/* Add → Add a medication (nav.js #add-medication), on this page or arriving from another: the form, the address the
   tab's again (a reload doesn't open it twice); for owners and editors */
function medAddResume() {
  if (location.hash !== "#add-medication") return;
  // on the Medications tab, where it's listed once saved (#1092's check: from another page it was the Overview)
  if (DATA && canEdit() && state.tab !== "medications" && shownTabs().includes("medications")) switchTab("medications");
  history.replaceState(history.state, "", location.pathname + location.search + (DATA ? tabHash() : ""));
  if (canEdit()) medEditForm(null);
}
const mmOpen = key => { const m = MEDS.find(x => x.key === key); if (m) openMedication(m); return m; };

/* the Medications tab's Add a medication, for owners and editors */
const medAddBtn = () => canEdit() ? el("button", {class: "btn small needs-server", type: "button", text: "Add a medication", onclick: () => medEditForm(null)}) : null;
/* the medications of the records hidden by hand (#1093), on the tab: closed until opened, each with Show again */
let medHiddenOpen = false;
function medHiddenCard() {
  const hid = Array.isArray(DATA.hiddenMeds) ? DATA.hiddenMeds : [];
  if (!hid.length) return null;
  return el("details", {class: "card med-past", open: medHiddenOpen || null, ontoggle: e => { medHiddenOpen = e.target.open; }},
    el("summary", {}, el("h2", {text: `Hidden (${hid.length})`})),
    el("p", {class: "hint", text: "Taken off this list by hand: their records are kept, and a later pull doesn't bring them back."}),
    el("ul", {class: "mr-list", "aria-label": "Hidden medications"}, ...hid.map(h => el("li", {class: "mr-item"},
      el("span", {class: "mr-says", text: h.name + (h.start ? ` · from ${fmtD(dayTs(h.start))}` : "") + (h.stop ? ` to ${fmtD(dayTs(h.stop))}` : "")}),
      canEdit() ? el("button", {class: "btn small needs-server", type: "button", text: "Show again", "aria-label": `Show ${h.name} again`, onclick: async () => {
        if (!await mmSend("PUT", "hidden", {key: h.key, hidden: false})) return;
        const row = document.querySelector(`#view-medications [data-med-key="${CSS.escape(h.key)}"] .cell-btn`);
        row?.focus();
        announce(`${h.name} is shown again.`);
      }}) : null))));
}

/* in a medication's drawer (meds.js openMedication): your entry, or your change beside what the records list, and
   Edit, Put back as listed and Remove for owners and editors */
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
    changed.length ? btn("Put back as listed", () => mmPutBack(m), {"data-tip": "Undo your changes: as the records list it"}) : null,
    btn("Remove", () => mmRemove(m), {"aria-label": `Remove ${m.name}`, "data-tip": m.own ? "Delete your entry" : "Hide it here; its records are kept"})));
  return out;
}
/* focus once the drawer has closed on what's gone: the toast's Undo (as Import history's removals; it went to the next
   row, the Undo a long way off), back to that row once the toast goes */
function mmAfterGone(t) {
  closeDrawer();
  requestAnimationFrame(() => { if (t.node?.isConnected) t.node.querySelector(".sg-toast-acts button")?.focus(); });
}
async function mmRemove(m) {
  const name = m.name;
  if (m.own) {
    const j = await mmSend("DELETE", `own/${m.own.id}`);
    if (!j) return;
    const logged = DOSES.filter(d => d.key === m.key).length;
    // (its doses are kept, but the tab and its drawers list them nowhere without it: said where they are)
    mmAfterGone(toast(`Removed ${name}` + (logged ? `. Its ${logged} logged dose${logged > 1 ? "s are" : " is"} kept, in Export everything, and come${logged > 1 ? "" : "s"} back with Undo.` : "."), {action: "Undo", onAction: async () => {
      if (await mmSend("POST", "own", j.removed)) { mmOpen(j.removed.key); announce(`${name} is back.`); }
    }}));
    return;
  }
  if (!await mmSend("PUT", "hidden", {key: m.key, hidden: true})) return;
  mmAfterGone(toast(`Hid ${name}: its records are kept. Hidden on the Medications tab shows it again.`, {action: "Undo", onAction: async () => {
    if (await mmSend("PUT", "hidden", {key: m.key, hidden: false})) { mmOpen(m.key); announce(`${name} is shown again.`); }
  }}));
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
