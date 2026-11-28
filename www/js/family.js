/* Sanguity · family history (#1134; server/family.py). DATA.familyHistory: each relative's entry as a record says it
   (FHIR's FamilyMemberHistory, a C-CDA's Family History section) or one of your own ("own": {id, by, at}); grouped here
   by relative, its relation and name. DATA.hiddenFamily: the records' entries hidden by hand. The Overview's Health
   record lists each relative (ovFamily); one opens in the drawer with its entries, each saying where it's from, and for
   owners and editors Edit and Remove on their own and Hide on the records' (which are never edited: they say what the
   record says). Add a relative opens the form in the drawer. Facts as recorded, nothing read into them. Every change's
   reply carries the records whole, put in place (medmine.js mmSend's way).
   Plain script, loaded in order by index.html after medmine.js; all files share one global scope. */
"use strict";
const FAM_RELATIONS = ["Mother", "Father", "Sister", "Brother", "Daughter", "Son", "Maternal grandmother", "Maternal grandfather",
  "Paternal grandmother", "Paternal grandfather", "Aunt", "Uncle", "Cousin", "Half-sister", "Half-brother", "Niece", "Nephew"];
const famOf = x => [x.relation, x.name].map(v => String(v || "").trim().toLowerCase()).join("|");
const famWho = x => x.name ? `${x.relation} (${x.name})` : x.relation;
const famCond = c => c.name + [c.onset ? `onset age ${c.onset}` : "", c.death ? "contributed to death" : ""].filter(Boolean).map(w => ` (${w})`).join("");
function famStatus(xs) {
  const a = xs.find(x => x.deathAge), d = xs.find(x => typeof x.deceased === "boolean");
  return a ? `Deceased, age ${a.deathAge}` : d ? (d.deceased ? "Deceased" : "Living") : "";
}
function famGroups() {
  const g = new Map();
  for (const x of DATA.familyHistory || []) { const k = famOf(x); if (!g.has(k)) g.set(k, []); g.get(k).push(x); }
  return [...g.values()];
}
const famSend = (method, path, body, msg) => mmSend(method, path, body, msg, "family");

/* the Health record's card: a row per relative, its conditions under it; Add a relative and the hidden for editors */
function ovFamily() {
  const groups = famGroups(), hid = Array.isArray(DATA.hiddenFamily) ? DATA.hiddenFamily : [];
  if (!groups.length && !canEdit()) return null;
  const card = el("div", {class: "card", id: "ovFamily"}, el("h3", {text: "Family history" + (groups.length ? ` (${groups.length})` : "")}),
    el("p", {class: "hint", text: groups.length ? "Relatives and their conditions, as the records and your own entries say" : "Relatives and their conditions, from the records or added here"}));
  for (const xs of groups) card.append(el("div", {class: "ov-row", "data-fam": famOf(xs[0]), ...pressable(ev => openFrom(ev.currentTarget, () => openFamily(famOf(xs[0]))))},
    el("span", {class: "n", text: famWho(xs[0])}), el("span", {class: "r", text: famStatus(xs)}),
    el("span", {class: "s", text: [...new Set(xs.flatMap(x => (x.conditions || []).map(famCond)))].join(" · ") || "No conditions recorded"})));
  if (canEdit()) card.append(el("div", {class: "btns mt-2"}, el("button", {class: "btn small needs-server", type: "button", text: "Add a relative", onclick: () => famEditForm(null)})));
  if (hid.length) card.append(el("details", {class: "mt-2"}, el("summary", {text: `Hidden (${hid.length})`}),
    el("ul", {class: "mr-list", "aria-label": "Hidden family history"}, ...hid.map(h => el("li", {class: "mr-item"},
      el("span", {class: "mr-says", text: famWho(h)}),
      canEdit() ? el("button", {class: "btn small needs-server", type: "button", text: "Show again", "aria-label": `Show ${famWho(h)} again`, onclick: async () => {
        if (!await famSend("PUT", "hidden", {key: h.key, hidden: false})) return;
        document.querySelector(`#ovFamily [data-fam="${CSS.escape(famOf(h))}"]`)?.focus();
        announce(`${famWho(h)} is shown again.`);
      }}) : null)))));
  return card;
}

/* a relative's entries, in the drawer */
function openFamily(k) {
  const xs = (DATA.familyHistory || []).filter(x => famOf(x) === k);
  if (!xs.length) { closeDrawer(); return; }
  state.selEv = null;
  const body = drawerHead("", famWho(xs[0]), ["Family history", famStatus(xs)].filter(Boolean).join(" · "));
  $("#dkey").replaceChildren(icon("users"));
  drawerStill = () => (DATA.familyHistory || []).some(x => famOf(x) === k);   // (app.js, #517)
  const btn = (text, onclick, more = {}) => el("button", {class: "btn needs-server", type: "button", text, onclick, ...more});
  for (const x of xs) {
    body.append(el("div", {class: "sect", text: x.own ? "Your own entry" : `From ${x.src || "the records"}`}),
      ...present(drawerFacts([["Conditions", (x.conditions || []).map(famCond).join("; ") || "None recorded"], ["Living or deceased", famStatus([x])],
        ["Note", x.note], ["Recorded", x.date ? fmtD(dayTs(x.date)) : ""], ["Added by", x.own?.by]])));
    if (canEdit()) body.append(el("div", {class: "btns"}, ...(x.own
      ? [btn("Edit", () => famEditForm(x), {"aria-label": `Edit ${famWho(x)}`}), btn("Remove", () => famRemove(x), {"aria-label": `Remove ${famWho(x)}`, "data-tip": "Delete your entry"})]
      : [btn("Hide", () => famHide(x), {"aria-label": `Hide ${famWho(x)} from ${x.src || "the records"}`, "data-tip": "Hide it here; its records are kept"})])));
  }
  if (!xs.some(x => x.own)) body.append(el("p", {class: "hint", text: "As the records say it: they're not changed here. Hide takes an entry off; a later pull doesn't bring it back."}));
  showDrawer();
}
async function famRemove(x) {
  const j = await famSend("DELETE", `own/${x.own.id}`);
  if (!j) return;
  mmAfterGone(toast(`Removed ${famWho(x)}.`, {action: "Undo", onAction: async () => {
    if (await famSend("POST", "own", j.removed)) { openFamily(famOf(x)); announce(`${famWho(x)} is back.`); }
  }}));
}
async function famHide(x) {
  if (!await famSend("PUT", "hidden", {key: x.key, hidden: true})) return;
  mmAfterGone(toast(`Hid ${famWho(x)}: its records are kept. Hidden, under Family history, shows it again.`, {action: "Undo", onAction: async () => {
    if (await famSend("PUT", "hidden", {key: x.key, hidden: false})) { openFamily(famOf(x)); announce(`${famWho(x)} is shown again.`); }
  }}));
}

/* the add / edit form, in the drawer. x: your own entry (null: a new one) */
function famEditForm(x) {
  const body = drawerHead("", x ? `Edit ${famWho(x)}` : "Add a relative", "Family history · your own entry");
  $("#dkey").replaceChildren(icon("users"));
  if (x) drawerStill = () => (DATA.familyHistory || []).some(y => y.key === x.key);
  const uid = Math.random().toString(36).slice(2, 8), msg = el("p", {class: "hint edit-msg", role: "status"}), inp = {};
  const field = (k, label, input, v) => {
    input.id = `fam-${k}-${uid}`; input.value = v || ""; inp[k] = input;
    return el("div", {class: "jfield"}, el("label", {for: input.id, text: label}), input);
  };
  const text = (max, more = {}) => el("input", {type: "text", maxlength: max, autocomplete: "off", ...more});
  const list = el("datalist", {id: `fam-rel-${uid}`}, ...FAM_RELATIONS.map(r => el("option", {value: r})));
  const conds = el("div", {class: "jform"}), rows = [];
  const addCond = (c = {}) => {
    const n = rows.length + 1, name = text(200), onset = text(40, {inputmode: "numeric"});
    name.id = `fam-c${n}-${uid}`; onset.id = `fam-o${n}-${uid}`; name.value = c.name || ""; onset.value = c.onset || "";
    rows.push([name, onset]);
    conds.append(el("div", {class: "jwhen"}, el("div", {class: "jfield"}, el("label", {for: name.id, text: `Condition ${n}`}), name),
      el("div", {class: "jfield"}, el("label", {for: onset.id, text: `Onset age (optional)`}), onset)));
    return name;
  };
  for (const c of (x?.conditions?.length ? x.conditions : [{}])) addCond(c);
  const status = el("select", {}, ...[["", "Not said"], ["living", "Living"], ["deceased", "Deceased"]].map(([v, t]) => el("option", {value: v, text: t})));
  const st = !x || typeof x.deceased !== "boolean" ? "" : x.deceased ? "deceased" : "living";
  const form = el("form", {class: "jform", novalidate: true},
    field("relation", "Relative", text(100, {required: true, list: list.id}), x?.relation), list,
    field("name", "Name (optional)", text(100), x?.name), conds,
    el("div", {}, el("button", {class: "btn small", type: "button", text: "Add another condition", onclick: () => addCond().focus()})),
    el("div", {class: "jwhen"}, field("status", "Living or deceased", status, st), field("deathAge", "Age at death (optional)", text(40, {inputmode: "numeric"}), x?.deathAge)),
    field("note", "Note (optional)", el("textarea", {rows: 3, maxlength: 2000}), x?.note),
    el("div", {class: "btns"}, el("button", {class: "btn primary needs-server", type: "submit", text: x ? "Save" : "Add relative"}),
      el("button", {class: "btn", type: "button", text: "Cancel", onclick: () => x ? openFamily(famOf(x)) : closeDrawer()})), msg);
  form.onsubmit = async ev => {
    ev.preventDefault();
    const out = {relation: inp.relation.value.trim(), name: inp.name.value.trim(), status: inp.status.value, deathAge: inp.deathAge.value.trim(),
      note: inp.note.value.trim(), conditions: rows.map(([n, o]) => ({name: n.value.trim(), onset: o.value.trim()})).filter(c => c.name || c.onset)};
    const bad = (t, f) => { sayIn(msg, t, true); f.focus(); };
    if (!out.relation) return bad("Say who the relative is (mother, brother…).", inp.relation);
    const noName = rows.find(([n, o]) => !n.value.trim() && o.value.trim());
    if (noName) return bad("Give the condition's name, or leave its onset age empty.", noName[0]);
    if (out.deathAge && out.status !== "deceased") return bad("An age at death needs Deceased.", inp.status);
    const j = await famSend(x ? "PUT" : "POST", x ? `own/${x.own.id}` : "own", out, msg);
    if (!j) return;
    openFamily(famOf(out));
    announce(x ? "Saved." : `${famWho(out)} added.`);
  };
  body.append(form);
  showDrawer();
  inp.relation.focus({preventScroll: true});
}
