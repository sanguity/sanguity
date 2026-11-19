/* Sanguity · About this person (#194) and Targets of your own (#234), on Settings under This profile (#267; on
   Sharing before): drawAbout(p) draws them for p, a profile of /api/me's, in #about and #targets (settings.html).
   - The birth date, the due date of a baby born early, the sex used for reference ranges and a measured maximum heart
     rate, saved together; the height history, each entry added or removed on its own; and the heights a clinic
     recorded in the records, beside it, each offered for the history (person.js, server/person.py).
   - Targets of your own for home readings: targetsCard (targets.js).
   Owners and editors change them; viewers see them as text. The details and each target are fields ([data-field]):
   one changed is marked "not saved" and counted in its card's data-dirty, for the group's bar (sidebar.js, #272) to
   save them all in one go (aboutSave) or put them back (aboutDiscard); what's typed and not saved stays as typed
   when the cards are drawn again for something else (a height added, a target cleared). Heights show in this person's Units choice, else as each
   was typed, with the other unit beside. call() is the page's (settings.js). Plain script; all files share one scope. */
"use strict";
let ABOUT_P = null, ABOUT_A = null, HEIGHT_PREF = "reported", UNITS_PREF = {};   // the profile shown and its answer drawn; this person's Units choices (their own setting): heights; targets' (#234)
async function drawAbout(p) {
  ABOUT_P = p;
  $("#about").hidden = $("#targets").hidden = false;
  if (!drawAbout.wired) { drawAbout.wired = true; for (const part of [$("#about"), $("#targets")]) for (const t of ["input", "change"]) part.addEventListener(t, aboutCount); }
  sayIn($("#aboutMsg"), ""); sayIn($("#targetsMsg"), "");
  let a;
  try {   // the Units choice read each time: Display, on the same page, may have changed it
    UNITS_PREF = (await call("settings/user")).units || {}; HEIGHT_PREF = UNITS_PREF.height || "reported";
    a = await call(`profiles/${p.id}/person`);
  } catch (e) { $("#aboutBody").replaceChildren(); $("#targetsBody").replaceChildren(); sayIn($("#aboutMsg"), e.message, true); return; }
  if (ABOUT_P === p) showAbout(p, a, true);
}
const aboutDay = d => fmtD(zParse(d));
/* the Units choice saved or undone in Display (settings.js, #408): both cards drawn again in it, what's typed there
   kept (a target typed in a unit that changed, turned into the new one) */
function aboutUnits(units) {
  const was = UNITS_PREF;
  UNITS_PREF = units || {}; HEIGHT_PREF = UNITS_PREF.height || "reported";
  if (same(UNITS_PREF, was) || !ABOUT_A || $("#about").dataset.profile !== String(ABOUT_P.id)) return;
  // the height being added: its date and value as typed, and the unit it's typed in once a value is
  const typed = [...$("#about").querySelectorAll(".about-add input[id]")].filter(i => i.value !== i.defaultValue).map(i => [i.id, i.value]);
  const typedIn = typed.some(([id]) => id !== "heightDate") && $("#about input[name=heightUnit]:checked")?.value;
  const units0 = Object.fromEntries(aboutFields().map(f => [f.dataset.field, f.dataset.unit]));
  showAbout(ABOUT_P, ABOUT_A);   // (the targets typed: kept as typed)
  for (const [id, v] of typed) { const i = document.getElementById(id); if (i) i.value = v; }
  const u = typedIn && $(`#about input[name=heightUnit][value=${typedIn}]`);
  if (u && !u.checked) { u.checked = true; u.dispatchEvent(new Event("change", {bubbles: true})); }
  for (const f of aboutFields()) {
    const k = f.dataset.field, from = units0[k], to = f.dataset.unit;
    if (!from || !to || from === to || !fieldChanged(f)) continue;
    const pl = (k === "glucose" || k === "cgm") ? (/mmol/.test(to) ? 1 : 0) : TGT[k].places;
    for (const n of ["low", "high"]) { const i = f.querySelector(`[name="${n}"]`); if (i && i.value !== "") i.value = +tgtConv(k, tgtCanon(k, +i.value, from), to).toFixed(pl); }
  }
  aboutCount();
}

/* ---- changes not saved (#272) */
const aboutFields = () => [...document.querySelectorAll("#about [data-field], #targets [data-field]")];
const fieldChanged = f => [...f.querySelectorAll("input, select")].some(i => i.type === "radio" || i.type === "checkbox" ? i.checked !== i.defaultChecked
  : i.tagName === "SELECT" ? [...i.options].some(o => o.selected !== o.defaultSelected) : i.value !== i.defaultValue);
/* each field marked, and each card's data-dirty the number changed in it */
function aboutCount() {
  for (const part of [$("#about"), $("#targets")]) {
    let n = 0;
    for (const f of part.querySelectorAll("[data-field]")) { const c = fieldChanged(f); f.classList.toggle("set-changed", c); n += c; }
    if (n) part.setAttribute("data-dirty", n); else part.removeAttribute("data-dirty");
  }
}
/* what's typed in the fields changed, but those in drop (their keys): [[key, [[value, checked], ...]]], to put back */
const typedNow = (drop = []) => aboutFields().filter(f => fieldChanged(f) && !drop.includes(f.dataset.field))
  .map(f => [f.dataset.field, [...f.querySelectorAll("input, select")].map(i => [i.value, i.checked])]);
function typedBack(list) {
  for (const [k, vals] of list) {
    const f = aboutFields().find(x => x.dataset.field === k);
    if (f) [...f.querySelectorAll("input, select")].forEach((i, n) => {
      if (!vals[n]) return;
      if (i.type === "radio" || i.type === "checkbox") i.checked = vals[n][1]; else i.value = vals[n][0];
    });
  }
  aboutCount();
}
/* a field's values for PUT person: one of the details, or a target ({targets: {[k]: target}}, null to clear it) */
const DETAIL = {birthDate: "Birth date", dueDate: "Due date, if born early", sex: "Sex used for reference ranges", maxHr: "Maximum heart rate"};
function fieldValue(f) {
  const k = f.dataset.field, v = n => f.querySelector(`[name="${n}"]`)?.value ?? "";
  if (k === "sex") return {sex: f.querySelector("input:checked")?.value ?? ""};
  if (k in DETAIL) return {[k]: k === "maxHr" ? (v(k) === "" ? 0 : +v(k)) : v(k)};
  const n = x => v(x) === "" ? null : +v(x);
  const body = k === "bp" ? {sysLow: n("sysLow"), sysHigh: n("sysHigh"), diaLow: n("diaLow"), diaHigh: n("diaHigh")} : {low: n("low"), high: n("high")};
  const none = Object.values(body).every(x => x == null);
  return {targets: {[k]: none && (ABOUT_A.targets || {})[k] ? null : {...body, ...(k === "bp" ? {} : {unit: f.dataset.unit}), by: v("by").trim(), on: v("on")}}};   // emptied: cleared
}
const fieldLabel = k => DETAIL[k] || TGT[k].name;
/* the bar's Save (settings.js saveParts): every field changed in these cards, in one change; a toast says what, with
   an Undo that puts back each one still as it was saved (#367). Focus, if it was on the bar, goes to the first one's
   heading without scrolling to it (#368, #376); else back where it was. */
async function aboutSave(parts, msg, had) {
  const p = ABOUT_P, a = ABOUT_A, fields = aboutFields().filter(f => fieldChanged(f) && parts.some(x => x.contains(f)));
  if (!fields.length) return;
  const keys = fields.map(f => f.dataset.field), body = {}, back = document.activeElement?.id;
  for (const f of fields) { const v = fieldValue(f); if (v.targets) body.targets = {...body.targets, ...v.targets}; else Object.assign(body, v); }
  const pmsg = keys.some(k => k in DETAIL) ? $("#aboutMsg") : $("#targetsMsg");
  let got;
  try { got = await call(`profiles/${p.id}/person`, "PUT", body); }
  catch (e) { sayIn(pmsg, e.message, true, {say: false}); sayIn(msg, e.message, true); return; }
  const said = keys.length === 1 && !(keys[0] in DETAIL) ? targetSaid(keys[0], a.targets?.[keys[0]], got.targets?.[keys[0]]) : `Saved ${quoted(keys.map(fieldLabel))}.`;
  sayIn(msg, "");
  if (ABOUT_P === p) {
    showAbout(p, got, keys);
    document.dispatchEvent(new Event("about:changed"));   // Settings' ranges match ages and sexes against these
    for (const m of [$("#aboutMsg"), $("#targetsMsg")]) sayIn(m, m === pmsg ? said : "", false, {say: false});
    (had ? document.getElementById(keys[0] in DETAIL ? "aboutHd" : `tgt-${keys[0]}-hd`) : back && document.getElementById(back))?.focus({preventScroll: true});
  }
  const was = Object.fromEntries(keys.map(k => [k, k in DETAIL ? (k === "maxHr" ? a.maxHr || 0 : a[k] || "") : typedTarget(k, a.targets?.[k])]));
  toast(said, {action: "Undo", onAction: () => aboutUndo(p, keys, was, got)});
}
/* a target as it was typed (in its unit), for PUT person to enter again; null: none */
const typedTarget = (k, t) => !t ? null : k === "bp" ? {...t} : {...t, low: tgtConv(k, t.low, t.unit), high: tgtConv(k, t.high, t.unit)};
const aboutOf = (got, k) => k in DETAIL ? got[k] : (got.targets || {})[k] || null;
async function aboutUndo(p, keys, was, wrote) {
  const now = await call(`profiles/${p.id}/person`), back = keys.filter(k => same(aboutOf(now, k), aboutOf(wrote, k))), since = keys.filter(k => !back.includes(k));
  let got = now;
  if (back.length) {
    const body = {};
    for (const k of back) if (k in DETAIL) body[k] = was[k]; else body.targets = {...body.targets, [k]: was[k]};
    got = await call(`profiles/${p.id}/person`, "PUT", body);
  }
  const text = [back.length ? `Undone: ${quoted(back.map(fieldLabel))} ${back.length === 1 ? "is" : "are"} as before.` : "",
    since.length ? `${quoted(since.map(fieldLabel))} ${since.length === 1 ? "was" : "were"} changed since, so ${since.length === 1 ? "it's" : "they're"} left as ${since.length === 1 ? "it is" : "they are"}.` : ""].join(" ").trim();
  if (ABOUT_P?.id !== p.id) { toast(`${p.name}: ${text}`); return; }
  showAbout(ABOUT_P, got, back);
  document.dispatchEvent(new Event("about:changed"));
  const m = keys.some(k => k in DETAIL) ? $("#aboutMsg") : $("#targetsMsg");
  for (const x of [$("#aboutMsg"), $("#targetsMsg")]) sayIn(x, x === m ? text : "");
}
/* the bar's Discard: these cards' fields as saved, focus on the first card's heading */
function aboutDiscard(parts) {
  showAbout(ABOUT_P, ABOUT_A, aboutFields().filter(f => parts.some(x => x.contains(f))).map(f => f.dataset.field));
  sayIn(parts[0].id === "about" ? $("#aboutMsg") : $("#targetsMsg"), "Changes discarded: back as saved.");
  document.getElementById(parts[0].id === "about" ? "aboutHd" : "targetsHd")?.focus();
}

/* drawn for p from a (GET person's answer). drop: the fields whose typed values aren't kept (their keys; true: none
   is, another profile's drawn) */
function showAbout(p, a, drop = []) {
  const keep = drop === true || $("#about").dataset.profile !== String(p.id) ? [] : typedNow(drop);
  ABOUT_A = a;
  const fail = (e, msg = $("#aboutMsg")) => sayIn(msg, e.message, true), edit = a.can_change, today = isoDay(Date.now());
  const age = a.birthDate ? ageOn(a.birthDate, today) : null;
  const hText = h => { const u = heightUnit(HEIGHT_PREF, h.unit);
    return el("span", {}, heightText(h.cm, u), el("span", {class: "muted", text: ` (${heightText(h.cm, u === "cm" ? "ftin" : "cm")})`})); };
  /* a change: the server answers with everything again; drawn afresh, focus back on the control that had it (or
     focus, an id); said in msg (the targets' card has its own, #234) */
  /* quiet: the words shown in the message line, said by a toast instead (#263). Gives back, once it's done, the
     words to say, false if it isn't. Done after another profile was picked (#377): that page stays as it is, and
     the words name p (a toast says them here, unless quiet: the caller's toast, with its Undo, does). */
  async function send(path, method, payload, said, focus, msg = $("#aboutMsg"), quiet = false) {
    const had = document.activeElement && document.activeElement.id;
    try {
      const got = await call(path, method, payload), text = typeof said === "function" ? said(got) : said;
      if (ABOUT_P !== p) { const named = `${p.name}: ${text}`; if (!quiet) toast(named); return named; }
      showAbout(p, got, Object.keys(payload?.targets || {}));   // (a target cleared isn't kept as it was typed)
      document.dispatchEvent(new Event("about:changed"));   // Settings' ranges match ages and sexes against these
      for (const m of [$("#aboutMsg"), $("#targetsMsg")]) sayIn(m, m === msg ? text : "", false, {say: !quiet});
      (focus && document.getElementById(focus) || had && document.getElementById(had) || $("#aboutHeightHd")).focus();
      return text;
    } catch (e) { fail(e, msg); return false; }
  }
  /* an Undo (#367): what a change took away put back, on p's own profile, only if what's there now is still what the
     change left (left(answer), on GET person's): otherwise it's left as it is, and since says so. Drawn and said as
     send() does while p is the profile shown; with another shown, that page stays as it is and a toast says it.
     Throws when the server refuses (the toast says so, and stays). */
  async function undo(left, path, method, payload, said, since, focus, msg = $("#aboutMsg")) {
    let got = await call(`profiles/${p.id}/person`), text = since;
    if (left(got)) { got = await call(path, method, payload); text = said; }
    if (ABOUT_P?.id !== p.id) { toast(`${p.name}: ${text}`); return; }
    showAbout(ABOUT_P, got, Object.keys(payload?.targets || {}));
  document.dispatchEvent(new Event("about:changed"));
    for (const m of [$("#aboutMsg"), $("#targetsMsg")]) sayIn(m, m === msg ? text : "");
    (focus && document.getElementById(focus) || $("#aboutHeightHd")).focus();
  }
  const parts = [];
  // the birth date and sex: fields the group's bar saves (#272); Enter in one saves too
  const saveBar = ev => { ev.preventDefault(); $("#setBar .btn.primary")?.click(); };
  const notSaved = () => el("span", {class: "tag set-unsaved", text: "not saved"});
  if (edit) {
    const birth = el("input", {type: "date", id: "aboutBirth", name: "birthDate", value: a.birthDate, min: "1900-01-01", max: today});
    const due = el("input", {type: "date", id: "aboutDue", name: "dueDate", value: a.dueDate || "", min: a.birthDate || "1900-01-01"});   // #196
    const sexes = el("fieldset", {class: "about-sex", "data-field": "sex"}, el("legend", {}, "Sex used for reference ranges", notSaved()),
      el("div", {class: "seg wrap"}, ...[["female", "Female"], ["male", "Male"], ["", "Not set"]].map(([v, l]) =>
        el("label", {}, el("input", {type: "radio", name: "aboutSex", value: v, checked: (a.sex || "") === v}), l))),
      el("p", {class: "muted", text: "What labs base their reference ranges on, for ranges of your own set for female or male. " +
        "It isn't gender identity, which Sanguity doesn't ask for."}));
    const maxHr = el("input", {type: "number", id: "aboutMaxHr", name: "maxHr", min: 100, max: 230, step: 1, inputmode: "numeric", value: a.maxHr || ""});   // #197
    parts.push(el("form", {class: "about-details", onsubmit: saveBar},
      el("div", {class: "about-field", "data-field": "birthDate"}, el("label", {for: "aboutBirth"}, "Birth date", notSaved()),
        el("div", {class: "about-row"}, birth, el("button", {class: "btn", type: "button", text: "Clear", "aria-label": "Clear the birth date",
          onclick: () => { birth.value = ""; birth.focus(); aboutCount(); }}), age != null ? el("span", {class: "muted", text: `aged ${age} today`}) : null),
        el("p", {class: "muted", text: "For the age on each result's day: ranges of your own by age band, and what depends on age."})),
      el("div", {class: "about-field", "data-field": "dueDate"}, el("label", {for: "aboutDue"}, "Due date, if born early", notSaved()),   // #196
        el("div", {class: "about-row"}, due, el("button", {class: "btn", type: "button", text: "Clear", "aria-label": "Clear the due date",
          onclick: () => { due.value = ""; due.focus(); aboutCount(); }})),
        el("p", {class: "muted", text: "Optional, for a baby born early: until 2 years, growth charts use the age counted from it (the corrected age)."})),
      sexes,
      el("div", {class: "about-field", "data-field": "maxHr"}, el("label", {for: "aboutMaxHr"}, "Maximum heart rate, measured (bpm)", notSaved()),   // heart rate zones (#197)
        el("div", {class: "about-row"}, maxHr),
        el("p", {class: "muted", text: "Only if you know it from a test, an exercise test for example. Heart rate zones on workouts use it; " +
          "left empty, they use the common estimate, 220 minus the age."}))));
  } else {
    parts.push(el("dl", {class: "about-dl"},
      el("dt", {text: "Birth date"}), el("dd", {text: a.birthDate ? aboutDay(a.birthDate) + (age != null ? ` (aged ${age} today)` : "") : "Not set"}),
      el("dt", {text: "Sex used for reference ranges"}), el("dd", {text: a.sexes[a.sex] || "Not set"}),
      el("dt", {text: "Maximum heart rate, measured"}), el("dd", {text: a.maxHr ? `${a.maxHr} bpm` : "Not set (heart rate zones use 220 minus the age)"}),   // #197
      ...(a.dueDate ? [el("dt", {text: "Due date, if born early"}), el("dd", {text: aboutDay(a.dueDate)})] : [])),   // #196
      el("p", {class: "hint", text: `Only the owners and editors of ${p.name} can change these.`}));
  }
  // the height history, newest first
  parts.push(el("h3", {class: "sub", id: "aboutHeightHd", tabindex: -1, text: "Height"}),
    el("p", {class: "hint", text: "A dated history: a child grows, and an adult's may change. The height on a day is the latest one written down by then."}));
  if (edit) {
    const typed = HEIGHT_PREF === "cm" || HEIGHT_PREF === "ftin" ? HEIGHT_PREF : (a.heights.at(-1)?.unit || "cm");
    const date = el("input", {type: "date", id: "heightDate", value: today, min: a.birthDate || "1900-01-01", max: today, required: true});
    const cm = el("input", {type: "number", id: "heightCm", min: 20, max: 272, step: "any", inputmode: "decimal"});
    const ft = el("input", {type: "number", id: "heightFt", min: 0, max: 8, step: 1, inputmode: "numeric"});
    const inch = el("input", {type: "number", id: "heightIn", min: 0, max: 11.9, step: "any", inputmode: "decimal"});
    const cmBox = el("label", {class: "about-num"}, "Height in cm", cm);
    const ftBox = el("div", {class: "about-ftin"}, el("label", {class: "about-num"}, "Feet", ft), el("label", {class: "about-num"}, "Inches", inch));
    const units = el("fieldset", {class: "about-typed"}, el("legend", {text: "Typed in"}),
      el("div", {class: "seg"}, ...[["cm", "cm"], ["ftin", "ft and in"]].map(([v, l]) =>
        el("label", {}, el("input", {type: "radio", name: "heightUnit", value: v, checked: v === typed}), l))));
    const shown = () => { const f = units.querySelector("input:checked").value === "ftin"; cmBox.hidden = f; ftBox.hidden = !f; };
    units.addEventListener("change", shown);
    const num = i => i.value === "" ? null : +i.value;
    parts.push(el("form", {class: "about-add", onsubmit: ev => { ev.preventDefault();
      const unit = units.querySelector("input:checked").value, had = a.heights.some(h => h.date === date.value);
      send(`profiles/${p.id}/person/heights`, "POST", unit === "ftin" ? {date: date.value, unit, ft: num(ft), inches: num(inch)} : {date: date.value, unit, cm: num(cm)},
        `${had ? "Replaced" : "Added"} the height on ${aboutDay(date.value)}.`); }},
      el("label", {class: "about-num"}, "Date", date), units, cmBox, ftBox,
      el("button", {class: "btn", type: "submit", id: "heightAdd", text: "Add height"})));
    shown();
  }
  const mine = [...a.heights].reverse();
  parts.push(mine.length ? el("table", {class: "tbl about-heights"},
    el("thead", {}, el("tr", {}, ...["Date", "Height", ""].map(h => el("th", {text: h})))),
    el("tbody", {}, ...mine.map(h => el("tr", {}, el("td", {text: aboutDay(h.date)}), el("td", {}, hText(h)),
      el("td", {class: "acts"}, edit ? el("button", {class: "btn small", type: "button", text: "Remove", "aria-label": `Remove the height on ${aboutDay(h.date)}`,
        onclick: async () => {   // at once, with an Undo that adds it back as it was (#263; it asked first before), if none was since (#367)
          const said = `Removed the height on ${aboutDay(h.date)}: ${heightText(h.cm, heightUnit(HEIGHT_PREF, h.unit))}.`;
          const done = await send(`profiles/${p.id}/person/heights/${h.date}`, "DELETE", undefined, said, null, $("#aboutMsg"), true);
          if (done)
            toast(done, {action: "Undo", onAction: () => undo(got => !got.heights.some(x => x.date === h.date),   // none written down on that day since
              `profiles/${p.id}/person/heights`, "POST", {date: h.date, unit: h.unit, cm: h.cm}, `Undone: the height on ${aboutDay(h.date)} is back.`,
              `A height on ${aboutDay(h.date)} was written down since: it's left as it is.`)}); }}) : null)))))
    : el("p", {class: "muted", id: "aboutNoHeight", text: "No height written down yet."}));
  // the heights a clinic recorded: beside the history, never in it until someone adds one
  if (a.recorded.length) {
    const MORE = 8, rows = a.recorded.map(r => el("tr", {}, el("td", {text: aboutDay(r.date)}), el("td", {}, hText(r)), el("td", {text: r.source || ""}),
      el("td", {class: "acts"}, r.inHistory ? el("span", {class: "muted", text: "In the history"})
        : edit ? el("button", {class: "btn small", type: "button", text: "Add to the history", "aria-label": `Add the height on ${aboutDay(r.date)} to the history`,
          onclick: () => send(`profiles/${p.id}/person/heights`, "POST", {date: r.date, unit: r.unit, cm: r.cm}, `Added the height on ${aboutDay(r.date)}.`)}) : null)));
    rows.slice(MORE).forEach(r => { r.hidden = true; });
    const all = rows.length > MORE ? el("button", {class: "btn small", type: "button", text: `Show ${rows.length - MORE} more`,
      onclick: () => { rows.forEach(r => { r.hidden = false; }); all.remove(); }}) : null;
    parts.push(el("h3", {class: "sub", text: "Heights in the records"}),
      el("p", {class: "hint", text: "Measured at a clinic and brought in with the records, or in the phone's Health app and brought in with its export. " +   // #223
        "They're shown here, not used, until " +
        (edit ? "you add one to the history" : "an owner or editor adds one to the history") + ": an import can be undone, and a measurement can be off."}),
      el("table", {class: "tbl about-heights about-recorded"},
        el("thead", {}, el("tr", {}, ...["Date", "Height", "From", ""].map(h => el("th", {text: h})))), el("tbody", {}, ...rows)),
      all ? el("div", {class: "about-row"}, all) : null);
  }
  $("#aboutBody").replaceChildren(...parts.filter(Boolean));
  $("#targetsBody").replaceChildren(targetsCard(p, a, UNITS_PREF, (path, method, payload, said, focus, quiet) => send(path, method, payload, said, focus, $("#targetsMsg"), quiet),
    (left, path, method, payload, said, since, focus) => undo(left, path, method, payload, said, since, focus, $("#targetsMsg")), saveBar, notSaved));   // #234
  $("#about").dataset.profile = p.id;   // whose details are drawn (the browser check waits for it)
  typedBack(keep);
}

